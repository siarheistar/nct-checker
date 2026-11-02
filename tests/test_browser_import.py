"""Verify the assisted search UI without contacting NCTS or booking anything."""
import json
import os
from pathlib import Path
import unittest
from urllib.parse import quote, urlsplit

from playwright.async_api import async_playwright
from app import app


class BrowserImportTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.playwright = await async_playwright().start()
        options = {'headless': True}
        if os.environ.get('NCT_TEST_BROWSER'):
            options['executable_path'] = os.environ['NCT_TEST_BROWSER']
        self.browser = await self.playwright.chromium.launch(**options)
        self.page = await self.browser.new_page()
        self.requests = []
        client = app.test_client()

        async def respond(route):
            self.requests.append((route.request.method, urlsplit(route.request.url).path))
            response = client.get(urlsplit(route.request.url).path)
            status, content_type, body = response.status_code, response.content_type, response.data
            response.close()
            await route.fulfill(status=status, content_type=content_type, body=body)
        await self.page.route('https://checker.test/**', respond)

    async def asyncTearDown(self):
        await self.browser.close()
        await self.playwright.stop()

    async def test_paste_displays_sorted_centres_without_server_search_or_booking(self):
        await self.page.goto('https://checker.test/')
        await self.page.get_by_text('Search using my browser — Human verification', exact=True).click()
        await self.page.get_by_label('Paste the NCTS availability page text').fill(
            (Path(__file__).parent / 'fixtures/ncts_availability.txt').read_text()
        )
        await self.page.get_by_role('button', name='Show dates', exact=True).click()
        first = self.page.locator('.ncts-browser-results tbody tr').first
        self.assertIn('Naas', await first.inner_text())
        self.assertIn('12 November 2026', await first.inner_text())
        self.assertEqual(await self.page.locator('.ncts-browser-results tbody tr').count(), 6)
        self.assertFalse(any(method != 'GET' or path.startswith('/api/') for method, path in self.requests))

    async def test_bookmark_capture_imports_without_repeating_search(self):
        payload = {'version': 1, 'capturedAt': '2026-11-02T10:00:00Z',
                   'slots': [{'centre': 'Naas', 'date': '2026-11-12'}]}
        await self.page.goto('https://checker.test/#ncts-data=' + quote(json.dumps(payload)))
        await self.page.locator('.ncts-browser-results').wait_for()
        self.assertIn('Naas', await self.page.locator('.ncts-browser-results').inner_text())
        self.assertFalse(any(path.startswith('/api/') for _, path in self.requests))

    async def test_invalid_capture_does_not_render_untrusted_html(self):
        payload = {'version': 1, 'capturedAt': '2026-11-02T10:00:00Z',
                   'slots': [{'centre': '<img src=x onerror=alert(1)>', 'date': '2026-11-12'}]}
        await self.page.goto('https://checker.test/#ncts-data=' + quote(json.dumps(payload)))
        await self.page.get_by_text('Invalid centre or date in the imported results.', exact=True).wait_for()
        self.assertEqual(await self.page.locator('#results img').count(), 0)

    async def test_session_buttons_delegate_specific_centre_and_day_without_opening_new_tab(self):
        opened = []
        await self.page.expose_function('nctOpenSlot', lambda value: opened.append(value) or {'success': True})
        payload = {'version': 1, 'capturedAt': '2026-11-02T10:00:00Z',
                   'slots': [{'centre': 'Naas', 'date': '2026-11-12'}]}
        await self.page.goto('https://checker.test/#ncts-data=' + quote(json.dumps(payload)))
        await self.page.get_by_role('button', name='Open centre Naas', exact=True).click()
        await self.page.get_by_text('Opened in your original NCTS tab. Choose a time and confirm there yourself.', exact=True).wait_for()
        await self.page.get_by_role('button', name='View times for 12 November 2026 at Naas', exact=True).click()
        await self.page.get_by_text('Opened in your original NCTS tab. Choose a time and confirm there yourself.', exact=True).wait_for()
        self.assertEqual(opened[0], {'action': 'centre', 'centre': 'Naas', 'date': '2026-11-12'})
        self.assertEqual(opened[1], {'action': 'date', 'centre': 'Naas', 'date': '2026-11-12'})
        self.assertEqual(len(self.page.context.pages), 1)
        self.assertFalse(any(path.startswith('/api/') for _, path in self.requests))
