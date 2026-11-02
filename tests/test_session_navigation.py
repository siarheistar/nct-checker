"""Centre/day handoff must reuse one authenticated tab and never book a slot."""
import os
import unittest
from urllib.parse import parse_qs, urlsplit

from playwright.async_api import async_playwright
from local_browser_search import SessionNavigator


class SessionNavigationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.playwright = await async_playwright().start()
        options = {'headless': True}
        if os.environ.get('NCT_TEST_BROWSER'):
            options['executable_path'] = os.environ['NCT_TEST_BROWSER']
        self.browser = await self.playwright.chromium.launch(**options)
        self.context = await self.browser.new_context()
        await self.context.add_cookies([{'name': 'test_session', 'value': 'same-session', 'url': 'https://ncts.test/'}])
        self.page = await self.context.new_page()
        self.requests = []

        async def respond(route):
            url = urlsplit(route.request.url)
            self.requests.append((url.path, route.request.headers.get('cookie', '')))
            centre = parse_qs(url.query).get('centre', ['Greenhills'])[0]
            if url.path == '/times':
                html = '<h1>Available times</h1><input type="radio" name="time"><button>Book now</button>'
            elif url.path == '/expired':
                html = '<h1>Additional security check</h1>'
            else:
                html = f'''<p>SELECTED CENTRE<br>{centre}<br>NEXT AVAILABLE DATE<br>17th Feb 2027</p>
                    <h1>Select from available dates at {centre}</h1>
                    <button onclick="document.getElementById('nctCentresDropdown').hidden=false">SELECT ANOTHER CENTRE</button>
                    <select id="nctCentresDropdown" hidden onchange="location.href='/dates?centre='+this.value">
                        <option selected>{centre}</option><option>Fonthill</option><option>Greenhills</option>
                    </select>
                    <label><input type="radio" name="SelectedBookingDay" value="17/02/2027 00:00:00"
                        onchange="location.href='/times?centre={centre}'">17 February 2027</label>'''
            await route.fulfill(status=200, content_type='text/html', body=html)
        await self.page.route('https://ncts.test/**', respond)
        await self.page.goto('https://ncts.test/dates')
        self.navigator = SessionNavigator(self.page, [{'centre': 'Fonthill', 'date': '2027-02-17'}],
                                          allowed_hosts=('ncts.test',))

    async def asyncTearDown(self):
        await self.context.close()
        await self.browser.close()
        await self.playwright.stop()

    async def test_centre_button_changes_centre_in_same_tab_without_selecting_day(self):
        result = await self.navigator.open({'action': 'centre', 'centre': 'Fonthill', 'date': '2027-02-17'})
        self.assertTrue(result['success'])
        self.assertIn('centre=Fonthill', self.page.url)
        self.assertEqual(len(self.context.pages), 1)
        self.assertFalse(await self.page.locator('input[name="SelectedBookingDay"]').is_checked())
        self.assertTrue(all('test_session=same-session' in cookie for _, cookie in self.requests))

    async def test_date_button_opens_times_without_selecting_time_or_booking(self):
        result = await self.navigator.open({'action': 'date', 'centre': 'Fonthill', 'date': '2027-02-17'})
        self.assertTrue(result['success'])
        self.assertIn('/times', self.page.url)
        self.assertFalse(await self.page.locator('input[name="time"]').is_checked())
        self.assertEqual(len(self.context.pages), 1)
        self.assertFalse(any(path in ('/book', '/login', '/') for path, _ in self.requests))
        self.assertTrue(all('test_session=same-session' in cookie for _, cookie in self.requests))

    async def test_uncaptured_date_is_rejected_before_navigation(self):
        before = self.page.url
        result = await self.navigator.open({'action': 'date', 'centre': 'Fonthill', 'date': '2027-02-18'})
        self.assertFalse(result['success'])
        self.assertEqual(self.page.url, before)

    async def test_stale_date_keeps_current_session_open(self):
        self.navigator.allowed.add(('Fonthill', '2027-02-18'))
        result = await self.navigator.open({'action': 'date', 'centre': 'Fonthill', 'date': '2027-02-18'})
        self.assertFalse(result['success'])
        self.assertIn('no longer listed', result['error'])
        self.assertIn('/dates', self.page.url)
        self.assertEqual(len(self.context.pages), 1)

    async def test_expired_session_is_not_silently_replaced_by_a_new_login(self):
        await self.page.goto('https://ncts.test/expired')
        result = await self.navigator.open({'action': 'date', 'centre': 'Fonthill', 'date': '2027-02-17'})
        self.assertFalse(result['success'])
        self.assertTrue(self.page.url.endswith('/expired'))
