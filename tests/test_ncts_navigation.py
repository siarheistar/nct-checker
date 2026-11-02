"""Offline regression tests for the NCTS form change and access-denied responses."""
import os
import unittest

from playwright.async_api import async_playwright

from app import NCTChecker, NCTSiteError


class NavigationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.playwright = await async_playwright().start()
        options = {'headless': True}
        if os.environ.get('NCT_TEST_BROWSER'):
            options['executable_path'] = os.environ['NCT_TEST_BROWSER']
        self.browser = await self.playwright.chromium.launch(**options)
        self.page = await self.browser.new_page()
        self.checker = NCTChecker('05D77568')
        self.checker.BASE_URL = 'https://ncts.test/'

    async def asyncTearDown(self):
        await self.browser.close()
        await self.playwright.stop()

    async def serve(self, input_html, result_html):
        async def respond(route):
            html = result_html if '/vehicle' in route.request.url else (
                '<form action="/vehicle">' + input_html
                + '<button id="btnSearchVehicle">Search</button></form>'
            )
            await route.fulfill(status=200, content_type='text/html', body=html)
        await self.page.route('https://ncts.test/**', respond)

    async def test_current_registration_form_does_not_require_rid_in_url(self):
        await self.serve(
            '<input name="Registration" id="Registration">',
            '<button id="confirmVehicleYes">Yes</button>',
        )
        await self.checker._open_vehicle_confirmation(self.page)
        self.assertIn('/vehicle?Registration=05D77568', self.page.url)
        self.assertTrue(await self.page.locator('#confirmVehicleYes').is_visible())

    async def test_legacy_registration_form_is_still_supported(self):
        await self.serve('<input id="rid" name="rid">', '<button id="confirmVehicleYes">Yes</button>')
        await self.checker._open_vehicle_confirmation(self.page)
        self.assertIn('rid=05D77568', self.page.url)

    async def test_block_after_search_is_not_reported_as_timeout(self):
        await self.serve(
            '<input name="Registration">',
            '<iframe src="/_Incapsula_Resource">Request unsuccessful</iframe>',
        )
        with self.assertRaisesRegex(NCTSiteError, 'blocking automated access'):
            await self.checker._open_vehicle_confirmation(self.page)

    async def test_block_on_homepage_is_detected_before_search(self):
        await self.page.route('https://ncts.test/**', lambda route: route.fulfill(
            status=403, content_type='text/html', body='<h1>Access Denied</h1>',
        ))
        with self.assertRaisesRegex(NCTSiteError, 'blocking automated access'):
            await self.checker._open_vehicle_confirmation(self.page)

    async def test_invalid_vehicle_is_not_retried_as_timeout(self):
        await self.serve(
            '<input name="Registration">',
            '<span class="field-validation-error">Vehicle not found</span>',
        )
        with self.assertRaisesRegex(NCTSiteError, 'could not validate'):
            await self.checker._open_vehicle_confirmation(self.page)

    async def test_blocked_results_cannot_be_returned_as_successful_empty_list(self):
        await self.page.set_content('<p>Request unsuccessful. Incident ID: example</p>')
        with self.assertRaisesRegex(NCTSiteError, 'blocking automated access'):
            await self.checker._extract_availability(self.page)


if __name__ == '__main__':
    unittest.main()
