"""Local helper waits for a person, then collects dates without choosing a slot."""
import asyncio
import json
import os
from pathlib import Path
import unittest
from urllib.parse import unquote, urlsplit

from playwright.async_api import async_playwright
from local_browser_search import collect_dates, parse_dates, result_url

FIXTURE = (Path(__file__).parent / 'fixtures/ncts_availability.txt').read_text()


class DateParsingTests(unittest.TestCase):
    def test_current_cards_are_sorted_and_no_vehicle_data_is_transferred(self):
        slots = parse_dates('Registration: 05D77568\n' + FIXTURE)
        self.assertEqual(len(slots), 6)
        self.assertEqual(slots[0], {'centre': 'Naas', 'date': '2026-11-12'})
        payload = json.loads(unquote(urlsplit(result_url(slots)).fragment[len('ncts-data='):]))
        self.assertEqual(set(payload), {'version', 'capturedAt', 'slots'})
        self.assertNotIn('05D77568', json.dumps(payload))


class LocalBrowserFlowTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.playwright = await async_playwright().start()
        options = {'headless': True}
        if os.environ.get('NCT_TEST_BROWSER'):
            options['executable_path'] = os.environ['NCT_TEST_BROWSER']
        self.browser = await self.playwright.chromium.launch(**options)
        self.page = await self.browser.new_page()
        self.requests = []
        self.task = None

        async def respond(route):
            path = urlsplit(route.request.url).path
            self.requests.append(path)
            pages = {
                '/': '<form action="/challenge"><input name="Registration"><button id="btnSearchVehicle">Search</button></form>',
                '/challenge': '<p>Additional security check</p><a id="test-human-finish" href="/voluntary">Simulate human completion</a>',
                '/voluntary': ('<h1>Voluntary Test Warning</h1><form action="/vehicle">'
                               '<label><input type="checkbox" required>To continue you must accept the Voluntary Test conditions.</label>'
                               '<button id="confirmVehicleYes">YES</button></form>'),
                '/vehicle': ('<p>Vehicle 05D77568</p><form action="/dates">'
                             '<label><input id="agreeChk" type="checkbox" required>I agree to the terms and conditions</label>'
                             '<label><input id="chkPrivacyRead" type="checkbox" required>I confirm the privacy notice</label>'
                             '<button id="confirmVehicleYes">YES</button></form>'),
                '/dates': ('<pre>' + FIXTURE + '</pre><form action="/book">'
                           '<input type="radio" name="slot" value="12nov"><button>Book now</button></form>'),
            }
            await route.fulfill(status=200, content_type='text/html', body=pages.get(path, 'Unexpected request'))
        await self.page.route('https://ncts.test/**', respond)

    async def asyncTearDown(self):
        if self.task and not self.task.done():
            self.task.cancel()
            try:
                await self.task
            except asyncio.CancelledError:
                pass
        await self.browser.close()
        await self.playwright.stop()

    async def start_helper(self, **approvals):
        await self.page.goto('https://ncts.test/')
        self.task = asyncio.create_task(collect_dates(
            self.page, '05D77568', timeout_seconds=10, tick=0.05,
            allowed_hosts=('ncts.test',), report=lambda _: None, **approvals,
        ))
        await self.page.locator('#test-human-finish').wait_for()

    async def test_human_completion_then_approved_conditions_resume_automatically(self):
        await self.start_helper(accept_current_voluntary_conditions=True, accept_current_site_conditions=True)
        await asyncio.sleep(0.2)
        self.assertEqual(urlsplit(self.page.url).path, '/challenge')
        # This is an offline fixture, not a real CAPTCHA.
        await self.page.locator('#test-human-finish').click()
        slots = await self.task
        self.assertEqual(len(slots), 6)
        self.assertEqual(slots[0]['centre'], 'Naas')
        self.assertFalse(await self.page.locator('input[name="slot"]').is_checked())
        self.assertNotIn('/book', self.requests)

    async def test_no_approval_means_no_terms_are_accepted(self):
        await self.start_helper()
        await self.page.locator('#test-human-finish').click()
        await self.page.get_by_role('heading', name='Voluntary Test Warning').wait_for()
        await asyncio.sleep(0.2)
        self.assertFalse(await self.page.get_by_role('checkbox').is_checked())
        self.assertNotIn('/vehicle', self.requests)
        self.assertFalse(self.task.done())

    async def test_approval_of_voluntary_conditions_does_not_accept_other_terms(self):
        await self.start_helper(accept_current_voluntary_conditions=True)
        await self.page.locator('#test-human-finish').click()
        await self.page.locator('#agreeChk').wait_for()
        await asyncio.sleep(0.2)
        self.assertFalse(await self.page.locator('#agreeChk').is_checked())
        self.assertFalse(await self.page.locator('#chkPrivacyRead').is_checked())
        self.assertNotIn('/dates', self.requests)

    async def test_manual_terms_are_followed_by_automatic_date_collection(self):
        await self.start_helper()
        await self.page.locator('#test-human-finish').click()
        await self.page.get_by_role('checkbox').check()
        await self.page.locator('#confirmVehicleYes').click()
        await self.page.locator('#agreeChk').check()
        await self.page.locator('#chkPrivacyRead').check()
        await self.page.locator('#confirmVehicleYes').click()
        slots = await self.task
        self.assertEqual(slots[0]['date'], '2026-11-12')
        self.assertNotIn('/book', self.requests)

    async def test_more_centres_link_is_expanded_without_selecting_a_slot(self):
        await self.page.goto('https://ncts.test/dates')
        await self.page.set_content('''
            <div>SELECTED CENTRE<br>Greenhills<br>NEXT AVAILABLE DATE<br>1st Mar 2027</div>
            <a href="#more" aria-expanded="false"
               onclick="document.getElementById('more').hidden=false; this.setAttribute('aria-expanded','true'); return false;">
               SEE MORE SUGGESTED CENTRES
            </a>
            <div id="more" hidden>OTHER SUGGESTED CENTRE<br>Naas<br>NEXT AVAILABLE DATE<br>12th Nov 2026</div>
            <input type="radio" name="slot">
        ''')
        slots = await collect_dates(self.page, '05D77568', timeout_seconds=3, tick=0.05,
                                    allowed_hosts=('ncts.test',), report=lambda _: None)
        self.assertEqual(len(slots), 2)
        self.assertEqual(slots[0]['centre'], 'Naas')
        self.assertFalse(await self.page.locator('input[name="slot"]').is_checked())
