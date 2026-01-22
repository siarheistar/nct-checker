"""
NCT Availability Checker Web App
Flask-based web application for checking NCT test slot availability
"""

import asyncio
import re
import json
from datetime import datetime
from dataclasses import dataclass, asdict
from typing import Optional
from flask import Flask, render_template, request, jsonify
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeout

app = Flask(__name__)


@dataclass
class NCTSlot:
    """Represents an available NCT test slot"""
    centre: str
    date: str
    date_formatted: str
    days_away: int
    is_selected: bool = False

    def to_dict(self):
        return asdict(self)


@dataclass
class VehicleInfo:
    """Vehicle details from NCTS"""
    registration: str
    make: str = ""
    model: str = ""
    year: str = ""
    colour: str = ""
    nct_due_date: str = ""

    def to_dict(self):
        return asdict(self)


class NCTChecker:
    """NCT availability checker"""

    BASE_URL = "https://www.ncts.ie"

    def __init__(self, registration: str):
        self.registration = registration.upper().replace(" ", "").replace("-", "")
        self.vehicle_info: Optional[VehicleInfo] = None

    def parse_date(self, date_str: str) -> Optional[datetime]:
        """Parse date formats like '28th May 2026', '2nd Jun 2026'"""
        date_str = date_str.strip()
        date_str = re.sub(r'(\d+)(st|nd|rd|th)', r'\1', date_str)

        formats = [
            "%d %B %Y", "%d %b %Y", "%d/%m/%Y", "%d-%m-%Y",
            "%A %d %B", "%d %B", "%d %b",
        ]

        for fmt in formats:
            try:
                parsed = datetime.strptime(date_str, fmt)
                if parsed.year == 1900:
                    parsed = parsed.replace(year=datetime.now().year)
                    if parsed < datetime.now():
                        parsed = parsed.replace(year=datetime.now().year + 1)
                return parsed
            except ValueError:
                continue
        return None

    async def check_availability(self) -> dict:
        """Run the check and return results as dict"""
        slots = []
        vehicle_info = None
        error = None

        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            context = await browser.new_context(
                viewport={'width': 1920, 'height': 1080},
                user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
            )
            page = await context.new_page()

            try:
                # Step 1: Go to homepage
                await page.goto(self.BASE_URL, wait_until='networkidle', timeout=30000)
                await page.wait_for_timeout(1000)

                # Handle cookie consent
                try:
                    accept_btn = page.locator('#bs-gdpr-cookies-modal-accept-btn')
                    if await accept_btn.is_visible(timeout=3000):
                        await accept_btn.click()
                        await page.wait_for_timeout(1000)
                except:
                    pass

                # Enter registration
                await page.locator('#rid').fill(self.registration)
                await page.wait_for_timeout(500)
                await page.locator('#btnSearchVehicle').click()

                # Wait for vehicle confirmation page
                await page.wait_for_url('**rid=**', timeout=15000)
                await page.wait_for_timeout(2000)

                # Extract vehicle info
                vehicle_info = await self._extract_vehicle_info(page)

                # Check checkboxes and confirm
                checkboxes = page.locator('input[type="checkbox"]')
                for i in range(await checkboxes.count()):
                    cb = checkboxes.nth(i)
                    if not await cb.is_checked():
                        await cb.check()
                        await page.wait_for_timeout(200)

                await page.locator('#confirmVehicleYes').click()
                await page.wait_for_timeout(3000)

                # Click "SEE MORE SUGGESTED CENTRES" if available
                try:
                    see_more = page.locator('text=SEE MORE SUGGESTED CENTRES')
                    if await see_more.is_visible(timeout=3000):
                        await see_more.click()
                        await page.wait_for_timeout(2000)
                except:
                    pass

                # Extract availability
                slots = await self._extract_availability(page)

            except PlaywrightTimeout:
                error = "Timeout - the NCTS website took too long to respond. Please try again."
            except Exception as e:
                error = f"Error checking availability: {str(e)}"
            finally:
                await browser.close()

        return {
            "success": error is None,
            "error": error,
            "vehicle": vehicle_info.to_dict() if vehicle_info else None,
            "slots": [s.to_dict() for s in slots],
            "checked_at": datetime.now().isoformat(),
            "registration": self.registration
        }

    async def _extract_vehicle_info(self, page) -> Optional[VehicleInfo]:
        """Extract vehicle details from confirmation page"""
        try:
            info = VehicleInfo(registration=self.registration)
            page_text = await page.inner_text('body')

            patterns = {
                'make': r'Make[:\s]+([A-Z]+)',
                'model': r'Model[:\s]+([A-Z0-9\s]+?)(?=\s*Year|\s*$)',
                'year': r'Year[:\s]+(\d{4})',
                'colour': r'Colour[:\s]+([A-Z]+)',
                'nct_due_date': r'(?:Next )?NCT Due Date[:\s]+(\d{2}/\d{2}/\d{4})',
            }

            for field, pattern in patterns.items():
                match = re.search(pattern, page_text, re.I)
                if match:
                    setattr(info, field, match.group(1).strip())

            return info
        except:
            return None

    async def _extract_availability(self, page) -> list[NCTSlot]:
        """Extract available dates from the booking dashboard"""
        slots = []

        try:
            await page.wait_for_selector('text=Other Suggested Centre', timeout=10000)
            page_text = await page.inner_text('body')

            # Extract selected centre
            selected_centre = ""
            centre_match = re.search(r'SELECTED CENTRE\s*\n\s*([^\n]+)', page_text)
            if centre_match:
                selected_centre = centre_match.group(1).strip()

            # Extract selected centre's next available date
            date_match = re.search(r'NEXT AVAILABLE DATE\s*\n?\s*(\d+(?:st|nd|rd|th)?\s+\w+\s+\d{4})', page_text)
            if date_match and selected_centre:
                parsed_date = self.parse_date(date_match.group(1))
                if parsed_date:
                    slots.append(NCTSlot(
                        centre=selected_centre,
                        date=parsed_date.isoformat(),
                        date_formatted=parsed_date.strftime('%a %d %b %Y'),
                        days_away=(parsed_date - datetime.now()).days,
                        is_selected=True
                    ))

            # Extract other suggested centres - look for the full section
            # Pattern: "CentreName\tDate\t" or just lines with centre and date
            lines = page_text.split('\n')
            for i, line in enumerate(lines):
                line = line.strip()
                # Match pattern: "Fonthill	28th May 2026" or similar
                match = re.match(r'^([A-Za-z\s\d\(\),]+?)\t+(\d+(?:st|nd|rd|th)?\s+\w+\s+\d{4})', line)
                if match:
                    centre = match.group(1).strip()
                    date_str = match.group(2)
                    # Skip if it's the header or selected centre
                    if 'Other Suggested' in centre or 'Next Available' in centre:
                        continue
                    if centre == selected_centre:
                        continue
                    parsed_date = self.parse_date(date_str)
                    if parsed_date and centre:
                        slots.append(NCTSlot(
                            centre=centre,
                            date=parsed_date.isoformat(),
                            date_formatted=parsed_date.strftime('%a %d %b %Y'),
                            days_away=(parsed_date - datetime.now()).days,
                            is_selected=False
                        ))

            # Extract individual dates for selected centre
            if selected_centre:
                date_section = re.search(r'Select from available dates.*?‹›', page_text, re.DOTALL)
                if date_section:
                    dates_found = re.findall(
                        r'(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday)\s+(\d+(?:st|nd|rd|th)?\s+\w+)',
                        date_section.group(0)
                    )
                    for date_str in dates_found:
                        full_date = date_str + f" {datetime.now().year}"
                        parsed = self.parse_date(full_date)
                        if parsed:
                            if parsed < datetime.now():
                                parsed = parsed.replace(year=parsed.year + 1)
                            # Only add if not duplicate
                            if not any(s.centre == selected_centre and s.date == parsed.isoformat() for s in slots):
                                slots.append(NCTSlot(
                                    centre=selected_centre,
                                    date=parsed.isoformat(),
                                    date_formatted=parsed.strftime('%a %d %b %Y'),
                                    days_away=(parsed - datetime.now()).days,
                                    is_selected=True
                                ))

        except Exception as e:
            print(f"Error extracting availability: {e}")

        slots.sort(key=lambda x: x.date)
        return slots


def run_async(coro):
    """Run async function in sync context"""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


@app.route('/')
def index():
    """Home page"""
    return render_template('index.html')


@app.route('/api/check', methods=['POST'])
def check_availability():
    """API endpoint to check NCT availability"""
    data = request.get_json()
    registration = data.get('registration', '').strip()

    if not registration:
        return jsonify({"success": False, "error": "Please enter a registration number"})

    # Validate registration format (basic check)
    if len(registration) < 5 or len(registration) > 10:
        return jsonify({"success": False, "error": "Invalid registration format"})

    checker = NCTChecker(registration)
    result = run_async(checker.check_availability())

    return jsonify(result)


@app.route('/health')
def health():
    """Health check endpoint"""
    return jsonify({"status": "healthy", "timestamp": datetime.now().isoformat()})


if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)
