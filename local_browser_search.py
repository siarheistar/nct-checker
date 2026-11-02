#!/usr/bin/env python3
"""Open NCTS locally, wait for manual Human/consent steps, then read dates only."""
import argparse
import asyncio
from contextlib import suppress
from datetime import datetime, timezone
import json
from pathlib import Path
import re
from urllib.parse import quote, urlsplit

from playwright.async_api import async_playwright, Error as PlaywrightError

NCTS_URL = 'https://www.ncts.ie/'
CHECKER_URL = 'https://nct-checker.fly.dev/'
PROFILE = Path(__file__).resolve().parent / '.nct-browser-profile'


def parse_dates(text):
    """Parse the visible NCTS cards/table; never extract vehicle or session data."""
    months = {'jan': 1, 'feb': 2, 'mar': 3, 'apr': 4, 'may': 5, 'jun': 6,
              'jul': 7, 'aug': 8, 'sep': 9, 'oct': 10, 'nov': 11, 'dec': 12}
    lines = [line.strip() for line in text.replace('\u00a0', ' ').splitlines() if line.strip()]
    slots = set()

    def add(centre, label):
        match = re.fullmatch(r'(\d{1,2})(?:st|nd|rd|th)?\s+([A-Za-z]+)\s+(\d{4})', label)
        if not match or not centre or len(centre) > 120 or re.search(r'[<>]', centre):
            return
        try:
            year = int(match[3])
            if not 2000 <= year <= 2100:
                return
            date = datetime(year, months[match[2][:3].lower()], int(match[1])).date().isoformat()
        except (KeyError, ValueError):
            return
        slots.add((date, centre))

    for i, line in enumerate(lines):
        if line.upper() in ('SELECTED CENTRE', 'OTHER SUGGESTED CENTRE') and i + 3 < len(lines):
            if lines[i + 2].upper() == 'NEXT AVAILABLE DATE':
                add(lines[i + 1], lines[i + 3])
        row = re.match(r'^(.+?)\t+(\d{1,2}(?:st|nd|rd|th)?\s+[A-Za-z]+\s+\d{4})(?:\t|$)', line)
        if row:
            add(row[1].strip(), row[2])
    return [{'centre': centre, 'date': date} for date, centre in sorted(slots)]


def result_url(slots):
    payload = {'version': 1, 'capturedAt': datetime.now(timezone.utc).isoformat(), 'slots': slots}
    return CHECKER_URL + '#ncts-data=' + quote(json.dumps(payload, separators=(',', ':')))


async def collect_dates(page, registration, timeout_seconds=900, tick=0.5,
                        allowed_hosts=('www.ncts.ie', 'ncts.ie'), report=print,
                        accept_current_voluntary_conditions=False,
                        accept_current_site_conditions=False):
    """A bounded observer: only cookie, registration-search and expand clicks are automated."""
    deadline = asyncio.get_running_loop().time() + timeout_seconds
    submissions = 0
    previous = None

    def announce(message):
        nonlocal previous
        if message != previous:
            report(message)
            previous = message

    while not page.is_closed() and asyncio.get_running_loop().time() < deadline:
        try:
            if urlsplit(page.url).hostname not in allowed_hosts:
                await asyncio.sleep(tick)
                continue
            # Cookie preferences are explicitly user-authorized. Never check terms or CAPTCHA.
            for selector in ('#bs-gdpr-cookies-modal-accept-btn',
                             '#CybotCookiebotDialogBodyLevelButtonLevelOptinAllowAll'):
                cookie = page.locator(selector)
                if await cookie.is_visible():
                    await cookie.click(timeout=3000)

            text = await page.locator('body').inner_text(timeout=3000)
            slots = parse_dates(text)
            if slots:
                more = page.get_by_text(re.compile(r'SEE MORE SUGGESTED CENTRES', re.I)).first
                if await more.is_visible() and await more.get_attribute('aria-expanded') != 'true':
                    await more.click(timeout=3000)
                    # NCTS currently expands existing cards synchronously. Also tolerate asynchronous updates.
                    await asyncio.sleep(max(tick, 1))
                    slots = parse_dates(await page.locator('body').inner_text(timeout=3000))
                announce('Dates found. No date, time or booking has been selected.')
                return slots

            confirm = page.locator('#confirmVehicleYes')
            if await confirm.is_visible():
                voluntary = page.get_by_role('heading', name='Voluntary Test Warning', exact=True)
                if accept_current_voluntary_conditions and await voluntary.is_visible():
                    consent = page.get_by_role('checkbox', name=re.compile('Voluntary Test conditions', re.I))
                    if await consent.count() == 1:
                        announce('Accepting the Voluntary Test conditions you explicitly approved for this search...')
                        await consent.check(timeout=3000)
                        await confirm.click(timeout=10000)
                        accept_current_voluntary_conditions = False
                        await page.wait_for_load_state('domcontentloaded', timeout=30000)
                        continue
                if accept_current_site_conditions and not await voluntary.is_visible():
                    terms = page.locator('#agreeChk')
                    privacy = page.locator('#chkPrivacyRead')
                    if await terms.is_visible() and await privacy.is_visible() and registration in text:
                        announce('Confirming the vehicle and NCTS terms you explicitly approved for this search...')
                        await terms.check(timeout=3000)
                        await privacy.check(timeout=3000)
                        await confirm.click(timeout=10000)
                        accept_current_site_conditions = False
                        await page.wait_for_load_state('domcontentloaded', timeout=30000)
                        continue
                announce('Confirm the vehicle and any required NCTS terms in the browser, then press Continue. '
                         'The helper will resume automatically.')
                await asyncio.sleep(tick)
                continue

            # The challenge may be nested in an Imperva frame. Observe it; never click or solve it.
            challenge = page.locator('iframe[src*="_Incapsula_Resource"], iframe[src*="hcaptcha"], iframe[src*="captcha"]')
            challenge_text = text.lower()
            for frame in page.frames:
                if frame == page.main_frame:
                    continue
                if any(marker in frame.url.lower() for marker in ('incapsula', 'captcha')):
                    try:
                        challenge_text += '\n' + (await frame.locator('body').inner_text(timeout=1000)).lower()
                    except PlaywrightError:
                        pass
            if 'access denied' in challenge_text and 'error 16' in challenge_text:
                announce('NCTS denied access without a Human checkbox. You can retry manually in this browser; '
                         'the helper will keep waiting.')
                await asyncio.sleep(tick)
                continue
            if await challenge.count() or any(marker in challenge_text for marker in (
                'verify you are human', 'i am human', 'я человек', 'additional security check',
            )):
                announce('Complete the Human check in the open Chrome window. The helper is waiting and will resume automatically.')
                await asyncio.sleep(tick)
                continue

            reg = page.locator('input[name="Registration"]:visible, #rid:visible').first
            if await reg.is_visible() and submissions < 2:
                announce('Entering registration and opening NCTS search...')
                await reg.fill(registration)
                await page.locator('#btnSearchVehicle').click(timeout=10000)
                submissions += 1
                await page.wait_for_load_state('domcontentloaded', timeout=30000)
                await asyncio.sleep(max(tick, 1))
                continue
            announce('Waiting for the NCTS availability page. Complete any visible prompts in the browser; '
                     'do not choose or book a slot yet.')
        except PlaywrightError:
            # Navigation can replace a frame between observations. It is safe to inspect again.
            if page.is_closed():
                return None
        await asyncio.sleep(tick)
    return None


class SessionNavigator:
    """Open a centre/day using the original NCTS tab; never select a time or book."""

    def __init__(self, page, slots, allowed_hosts=('www.ncts.ie', 'ncts.ie')):
        self.page = page
        self.allowed = {(slot['centre'], slot['date']) for slot in slots}
        self.allowed_hosts = allowed_hosts
        self.lock = asyncio.Lock()

    async def open(self, request):
        async with self.lock:
            action = request.get('action') if isinstance(request, dict) else None
            if action not in ('return', 'centre', 'date'):
                return {'success': False, 'error': 'Unknown navigation action.'}
            centre, date = request.get('centre'), request.get('date')
            if action != 'return' and (centre, date) not in self.allowed:
                return {'success': False, 'error': 'This centre/date was not in the captured results.'}
            if self.page.is_closed() or urlsplit(self.page.url).hostname not in self.allowed_hosts:
                return {'success': False, 'error': 'The original NCTS tab is no longer open. Start a new search.'}
            await self.page.bring_to_front()
            if action == 'return':
                return {'success': True}
            try:
                text = await self.page.locator('body').inner_text(timeout=3000)
                selected = re.search(r'SELECTED CENTRE\s*\n\s*([^\n]+)', text)
                if not selected or selected[1].strip() != centre:
                    dropdown = self.page.locator('#nctCentresDropdown')
                    if not await dropdown.count():
                        return {'success': False, 'error': 'NCTS is no longer showing availability. Complete any visible prompt in the original tab.'}
                    if not await dropdown.is_visible():
                        await self.page.get_by_text(re.compile('SELECT ANOTHER CENTRE', re.I)).first.click(timeout=5000)
                    await dropdown.select_option(label=centre, timeout=10000)
                    await self.page.wait_for_load_state('domcontentloaded', timeout=30000)
                    await self.page.get_by_role('heading', name=re.compile(re.escape(centre))).first.wait_for(timeout=10000)
                if action == 'date':
                    desired = datetime.strptime(date, '%Y-%m-%d').strftime('%d/%m/%Y')
                    radios = self.page.locator('input[name="SelectedBookingDay"]')
                    target = None
                    for i in range(await radios.count()):
                        value = await radios.nth(i).get_attribute('value') or ''
                        if value.split(' ')[0] == desired:
                            target = radios.nth(i)
                            break
                    if target is None:
                        return {'success': False, 'error': 'That date is no longer listed. The original NCTS tab is open with current availability; choose another date there.'}
                    # The label click submits only the day and opens available times, not a booking.
                    await target.locator('..').click(timeout=10000)
                    await self.page.wait_for_load_state('domcontentloaded', timeout=30000)
                return {'success': True}
            except PlaywrightError:
                return {'success': False, 'error': 'NCTS did not finish opening that centre/date. Continue in the original NCTS tab; no booking was made by the helper.'}


async def run(registration, timeout_seconds, accept_current_voluntary_conditions=False,
              accept_current_site_conditions=False):
    PROFILE.mkdir(mode=0o700, parents=True, exist_ok=True)
    PROFILE.chmod(0o700)
    async with async_playwright() as p:
        context = await p.chromium.launch_persistent_context(
            str(PROFILE), channel='chrome', headless=False, viewport={'width': 1200, 'height': 900},
        )
        try:
            page = context.pages[0] if context.pages else await context.new_page()
            await page.goto(NCTS_URL, wait_until='domcontentloaded', timeout=60000)
            await page.bring_to_front()
            print('NCTS is open in a separate Chrome window. Complete Human verification yourself when it appears.', flush=True)
            slots = await collect_dates(page, registration, timeout_seconds=timeout_seconds,
                                        report=lambda message: print(message, flush=True),
                                        accept_current_voluntary_conditions=accept_current_voluntary_conditions,
                                        accept_current_site_conditions=accept_current_site_conditions)
            if not slots:
                print('No dates collected. Nothing was booked. The browser remains open for manual use.', flush=True)
            else:
                for slot in slots:
                    print(f"{slot['date']}  {slot['centre']}", flush=True)
                results = await context.new_page()
                navigator = SessionNavigator(page, slots)

                async def open_original_session(source, request):
                    if source['page'] != results or source['frame'] != results.main_frame:
                        raise ValueError('Navigation is available only from the Checker results tab.')
                    if urlsplit(source['frame'].url).hostname != urlsplit(CHECKER_URL).hostname:
                        raise ValueError('Navigation is available only from the Checker origin.')
                    return await navigator.open(request)

                await results.expose_binding('nctOpenSlot', open_original_session)
                await results.goto(result_url(slots), wait_until='domcontentloaded', timeout=60000)
                await results.bring_to_front()
                print('Sorted dates are open in Checker. Centre/date buttons reuse the original NCTS tab; choose a time and book there yourself.', flush=True)
            print('Close this separate Chrome window when finished to stop the helper.', flush=True)
            with suppress(PlaywrightError):
                await context.wait_for_event('close', timeout=0)
        finally:
            with suppress(Exception):
                await context.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--registration', help='Vehicle registration (prompted if omitted)')
    parser.add_argument('--human-timeout', type=int, default=900, help='Seconds to wait for manual steps')
    parser.add_argument('--accept-current-voluntary-conditions', action='store_true',
                        help='Only use after explicit user approval of the currently shown Voluntary Test conditions')
    parser.add_argument('--accept-current-site-conditions', action='store_true',
                        help='Only use after explicit user approval of the currently shown NCTS terms and privacy notice')
    args = parser.parse_args()
    registration = re.sub(r'[\s-]', '', args.registration or input('Registration / номер автомобиля: ')).upper()
    if not re.fullmatch(r'[A-Z0-9]{5,10}', registration):
        parser.error('Enter a registration containing 5–10 letters/digits.')
    try:
        asyncio.run(run(registration, args.human_timeout, args.accept_current_voluntary_conditions,
                        args.accept_current_site_conditions))
    except KeyboardInterrupt:
        print('\nHelper stopped. The helper did not book an appointment.')


if __name__ == '__main__':
    main()
