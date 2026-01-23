#!/usr/bin/env python3
"""
NCT (National Car Test) Availability Checker - Full Flow Version
Automates the complete booking flow: reg entry → confirmation → date search

Usage:
    pip install playwright beautifulsoup4
    playwright install chromium

    # New booking flow - check availability for a new booking:
    python nct_checker_full.py --reg 05D77568 [--headless] [--json]

    # Reschedule flow - check availability for rescheduling existing booking:
    python nct_checker_full.py --reg 05D77568 --booking-id 81082214 [--headless] [--json]
"""

import argparse
import asyncio
import re
import json
from datetime import datetime
from dataclasses import dataclass, field
from typing import Optional
from pathlib import Path

try:
    from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeout
    PLAYWRIGHT_AVAILABLE = True
except ImportError:
    PLAYWRIGHT_AVAILABLE = False


@dataclass
class NCTSlot:
    """Represents an available NCT test slot"""
    centre: str
    date: datetime
    is_selected: bool = False
    
    def __str__(self):
        selected = " ⭐ SELECTED" if self.is_selected else ""
        return f"{self.centre}: {self.date.strftime('%A %d %B %Y')}{selected}"
    
    def days_from_now(self) -> int:
        return (self.date - datetime.now()).days
    
    def to_dict(self) -> dict:
        return {
            "centre": self.centre,
            "date": self.date.isoformat(),
            "date_formatted": self.date.strftime('%a %d %b %Y'),
            "days_away": self.days_from_now(),
            "is_selected_centre": self.is_selected
        }


@dataclass
class VehicleInfo:
    """Vehicle details from NCTS"""
    registration: str
    make: str = ""
    model: str = ""
    year: str = ""
    colour: str = ""
    nct_due_date: str = ""

    def __str__(self):
        return f"{self.year} {self.make} {self.model} ({self.colour}) - NCT due: {self.nct_due_date}"


@dataclass
class BookingInfo:
    """Existing booking details from NCTS"""
    booking_id: str
    registration: str
    centre: str = ""
    date: str = ""
    time: str = ""
    make: str = ""
    model: str = ""
    colour: str = ""

    def __str__(self):
        return f"Booking {self.booking_id}: {self.centre} on {self.date} at {self.time}"

    def to_dict(self) -> dict:
        return {
            "booking_id": self.booking_id,
            "registration": self.registration,
            "centre": self.centre,
            "date": self.date,
            "time": self.time,
            "make": self.make,
            "model": self.model,
            "colour": self.colour
        }


class NCTCheckerFull:
    """Full NCT availability checker with login flow"""

    BASE_URL = "https://www.ncts.ie"
    STATE_FILE = Path("nct_auth_state.json")

    def __init__(self, registration: str, headless: bool = True, save_state: bool = True, booking_id: str = None):
        self.registration = registration.upper().replace(" ", "")
        self.headless = headless
        self.save_state = save_state
        self.booking_id = booking_id
        self.vehicle_info: Optional[VehicleInfo] = None
        self.booking_info: Optional[BookingInfo] = None
    
    def parse_date(self, date_str: str) -> Optional[datetime]:
        """Parse date formats like '28th May 2026', '2nd Jun 2026'"""
        date_str = date_str.strip()
        
        # Remove ordinal suffixes (1st, 2nd, 3rd, 4th, etc.)
        date_str = re.sub(r'(\d+)(st|nd|rd|th)', r'\1', date_str)
        
        formats = [
            "%d %B %Y",      # 28 May 2026
            "%d %b %Y",      # 28 May 2026 (short month)
            "%d/%m/%Y",      # 28/05/2026
            "%d-%m-%Y",      # 28-05-2026
            "%A %d %B",      # Thursday 28 May (no year)
            "%d %B",         # 28 May (no year)
            "%d %b",         # 28 May (short, no year)
        ]
        
        for fmt in formats:
            try:
                parsed = datetime.strptime(date_str, fmt)
                # Add current/next year if not present
                if parsed.year == 1900:
                    parsed = parsed.replace(year=datetime.now().year)
                    if parsed < datetime.now():
                        parsed = parsed.replace(year=datetime.now().year + 1)
                return parsed
            except ValueError:
                continue
        
        return None
    
    async def check_availability(self) -> tuple[Optional[VehicleInfo], list[NCTSlot]]:
        """Run the full check flow"""
        slots = []
        
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=self.headless)
            
            # Try to use saved state if available
            context_options = {
                'viewport': {'width': 1920, 'height': 1080},
                'user_agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
            }
            
            if self.save_state and self.STATE_FILE.exists():
                try:
                    context_options['storage_state'] = str(self.STATE_FILE)
                    print("📂 Loading saved session state...")
                except:
                    pass
            
            context = await browser.new_context(**context_options)
            page = await context.new_page()
            
            try:
                # Step 1: Go to homepage and enter registration
                print(f"\n🚗 Step 1: Entering registration {self.registration}...")
                await page.goto(self.BASE_URL, wait_until='networkidle', timeout=30000)
                await page.wait_for_timeout(1000)

                # Handle cookie consent modal if present
                try:
                    accept_btn = page.locator('#bs-gdpr-cookies-modal-accept-btn')
                    if await accept_btn.is_visible(timeout=3000):
                        print("🍪 Accepting cookies...")
                        await accept_btn.click()
                        await page.wait_for_timeout(1000)
                except:
                    pass  # No cookie modal or already accepted

                # Find and fill registration input using the correct selector
                reg_input = page.locator('#rid')
                await reg_input.fill(self.registration)
                await page.wait_for_timeout(500)

                # Click the submit button
                submit_btn = page.locator('#btnSearchVehicle')
                await submit_btn.click()

                # Wait for navigation to vehicle confirmation page
                print("⏳ Waiting for vehicle details...")
                await page.wait_for_url('**rid=**', timeout=15000)
                await page.wait_for_timeout(2000)
                
                # Step 2: Extract vehicle info and confirm
                print("🚙 Step 2: Confirming vehicle details...")
                
                # Extract vehicle details
                self.vehicle_info = await self._extract_vehicle_info(page)
                if self.vehicle_info:
                    print(f"   Found: {self.vehicle_info}")
                
                # Accept terms checkboxes if not already checked
                checkboxes = page.locator('input[type="checkbox"]')
                checkbox_count = await checkboxes.count()

                for i in range(checkbox_count):
                    checkbox = checkboxes.nth(i)
                    if not await checkbox.is_checked():
                        await checkbox.check()
                        await page.wait_for_timeout(300)

                # Click YES button (it's an input[type="submit"] with id="confirmVehicleYes")
                yes_btn = page.locator('#confirmVehicleYes')
                await yes_btn.click()
                
                # Wait for booking dashboard
                print("⏳ Waiting for availability dashboard...")
                await page.wait_for_timeout(3000)
                
                # Step 3: Extract availability data
                print("📅 Step 3: Extracting available dates...")
                slots = await self._extract_availability(page)
                
                # Save state for future use
                if self.save_state:
                    await context.storage_state(path=str(self.STATE_FILE))
                    print(f"💾 Session state saved to {self.STATE_FILE}")
                
                # Optional: take screenshot for debugging
                # await page.screenshot(path='nct_dashboard.png', full_page=True)
                
            except PlaywrightTimeout as e:
                print(f"⚠️ Timeout: {e}")
            except Exception as e:
                print(f"❌ Error: {e}")
                # Take error screenshot
                await page.screenshot(path='nct_error.png')
                print("📸 Error screenshot saved to nct_error.png")
            finally:
                await browser.close()
        
        return self.vehicle_info, slots
    
    async def _extract_vehicle_info(self, page) -> Optional[VehicleInfo]:
        """Extract vehicle details from confirmation page"""
        try:
            info = VehicleInfo(registration=self.registration)
            
            # Look for vehicle details table/list
            page_text = await page.inner_text('body')
            
            # Extract using regex patterns
            patterns = {
                'make': r'Make[:\s]+([A-Z]+)',
                'model': r'Model[:\s]+([A-Z0-9]+)',
                'year': r'Year[:\s]+(\d{4})',
                'colour': r'Colour[:\s]+([A-Z]+)',
                'nct_due_date': r'(?:Next )?NCT Due Date[:\s]+(\d{2}/\d{2}/\d{4})',
            }
            
            for field, pattern in patterns.items():
                match = re.search(pattern, page_text, re.I)
                if match:
                    setattr(info, field, match.group(1))
            
            return info
        except:
            return None
    
    async def _extract_availability(self, page) -> list[NCTSlot]:
        """Extract available dates from the booking dashboard"""
        slots = []

        try:
            # Wait for the availability table to load
            await page.wait_for_selector('text=Other Suggested Centre', timeout=10000)

            # Get the selected centre info from the dark header bar
            selected_centre = ""
            try:
                # The selected centre header contains "SELECTED CENTRE" + centre name + date
                header_text = await page.inner_text('body')

                # Extract selected centre name (e.g., "Greenhills (Exit 11,M50)")
                centre_match = re.search(r'SELECTED CENTRE\s*\n\s*([^\n]+)', header_text)
                if centre_match:
                    selected_centre = centre_match.group(1).strip()

                # Extract the next available date for selected centre
                date_match = re.search(r'NEXT AVAILABLE DATE\s*\n?\s*(\d+(?:st|nd|rd|th)?\s+\w+\s+\d{4})', header_text)
                if date_match and selected_centre:
                    parsed_date = self.parse_date(date_match.group(1))
                    if parsed_date:
                        slots.append(NCTSlot(centre=selected_centre, date=parsed_date, is_selected=True))
            except:
                pass

            # Extract "Other Suggested Centre" table rows
            # Format: "Fonthill	28th May 2026" (tab-separated)
            page_text = await page.inner_text('body')

            # Find the table section
            table_match = re.search(r'Other Suggested Centre.*?SEE MORE', page_text, re.DOTALL)
            if table_match:
                table_text = table_match.group(0)
                # Parse each line looking for "CentreName    Date" pattern
                for line in table_text.split('\n'):
                    line = line.strip()
                    # Skip headers and buttons
                    if not line or 'Other Suggested' in line or 'Next Available' in line or 'SELECT' in line or 'Switch' in line:
                        continue
                    # Match pattern: "Fonthill	28th May 2026"
                    match = re.match(r'^([A-Za-z\s]+?)\s+(\d+(?:st|nd|rd|th)?\s+\w+\s+\d{4})', line)
                    if match:
                        centre = match.group(1).strip()
                        date_str = match.group(2)
                        parsed_date = self.parse_date(date_str)
                        if parsed_date and centre:
                            slots.append(NCTSlot(centre=centre, date=parsed_date))

            # Extract individual date buttons at the bottom for the selected centre
            # These show dates like "Thursday\n28th May"
            if selected_centre:
                date_section = re.search(r'Select from available dates.*?‹›', page_text, re.DOTALL)
                if date_section:
                    # Find all date patterns in this section
                    dates_found = re.findall(r'(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday)\s+(\d+(?:st|nd|rd|th)?\s+\w+)', date_section.group(0))
                    for date_str in dates_found:
                        # Add year
                        full_date = date_str + f" {datetime.now().year}"
                        parsed = self.parse_date(full_date)
                        if parsed:
                            # Adjust year if date is in past
                            if parsed < datetime.now():
                                parsed = parsed.replace(year=parsed.year + 1)
                            # Only add if not already in slots for this centre
                            if not any(s.centre == selected_centre and s.date == parsed for s in slots):
                                slots.append(NCTSlot(centre=selected_centre, date=parsed))

        except Exception as e:
            print(f"⚠️ Error extracting availability: {e}")

        # Sort by date
        slots.sort(key=lambda x: x.date)
        return slots

    async def _extract_booking_info(self, page) -> Optional[BookingInfo]:
        """Extract existing booking details from confirmation page"""
        try:
            info = BookingInfo(booking_id=self.booking_id, registration=self.registration)
            page_text = await page.inner_text('body')

            patterns = {
                'centre': r'NCT Centre[:\s]+([^\n]+)',
                'date': r'Date[:\s]+([^\n]+)',
                'time': r'Time[:\s]+(\d{1,2}:\d{2})',
                'make': r'Make[:\s]+([A-Z]+)',
                'model': r'Model[:\s]+([A-Z0-9]+)',
                'colour': r'Colour[:\s]+([A-Z]+)',
            }

            for field, pattern in patterns.items():
                match = re.search(pattern, page_text, re.I)
                if match:
                    setattr(info, field, match.group(1).strip())

            return info
        except:
            return None

    async def check_reschedule_availability(self) -> tuple[Optional[VehicleInfo], Optional[BookingInfo], list[NCTSlot]]:
        """Run the reschedule check flow for existing bookings"""
        slots = []

        if not self.booking_id:
            print("❌ Booking ID required for reschedule flow")
            return None, None, slots

        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=self.headless)

            context_options = {
                'viewport': {'width': 1920, 'height': 1080},
                'user_agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
            }

            if self.save_state and self.STATE_FILE.exists():
                try:
                    context_options['storage_state'] = str(self.STATE_FILE)
                    print("📂 Loading saved session state...")
                except:
                    pass

            context = await browser.new_context(**context_options)
            page = await context.new_page()

            try:
                # Step 1: Go to homepage and enter registration
                print(f"\n🚗 Step 1: Entering registration {self.registration}...")
                await page.goto(self.BASE_URL, wait_until='networkidle', timeout=30000)
                await page.wait_for_timeout(1000)

                # Handle cookie consent modal if present
                try:
                    accept_btn = page.locator('#bs-gdpr-cookies-modal-accept-btn')
                    if await accept_btn.is_visible(timeout=3000):
                        print("🍪 Accepting cookies...")
                        await accept_btn.click()
                        await page.wait_for_timeout(1000)
                except:
                    pass

                # Find and fill registration input
                reg_input = page.locator('#rid')
                await reg_input.fill(self.registration)
                await page.wait_for_timeout(500)
                await page.locator('#btnSearchVehicle').click()

                # Wait for vehicle confirmation page
                print("⏳ Waiting for vehicle details...")
                await page.wait_for_url('**rid=**', timeout=15000)
                await page.wait_for_timeout(2000)

                # Step 2: Extract vehicle info and confirm
                print("🚙 Step 2: Confirming vehicle details...")
                self.vehicle_info = await self._extract_vehicle_info(page)
                if self.vehicle_info:
                    print(f"   Found: {self.vehicle_info}")

                # Accept terms checkboxes
                checkboxes = page.locator('input[type="checkbox"]')
                checkbox_count = await checkboxes.count()
                for i in range(checkbox_count):
                    checkbox = checkboxes.nth(i)
                    if not await checkbox.is_checked():
                        await checkbox.check()
                        await page.wait_for_timeout(300)

                # Click YES button
                yes_btn = page.locator('#confirmVehicleYes')
                await yes_btn.click()
                await page.wait_for_timeout(3000)

                # Step 3: Click RESCHEDULE BOOKING button
                print("📅 Step 3: Selecting Reschedule Booking...")
                reschedule_btn = page.locator('text=RESCHEDULE BOOKING').first
                if not await reschedule_btn.is_visible(timeout=5000):
                    print("❌ No existing booking found - use regular check instead")
                    return self.vehicle_info, None, slots
                await reschedule_btn.click()
                await page.wait_for_timeout(2000)

                # Step 4: Enter Booking ID
                print(f"🔢 Step 4: Entering booking ID {self.booking_id}...")
                booking_input = page.locator('input[type="text"]').first
                await booking_input.wait_for(state='visible', timeout=10000)
                await booking_input.fill(self.booking_id)
                await page.wait_for_timeout(500)

                # Click the Reschedule Booking button to submit
                confirm_btn = page.locator('button:has-text("RESCHEDULE BOOKING"), input[type="submit"][value*="RESCHEDULE"]')
                if await confirm_btn.count() == 0:
                    confirm_btn = page.locator('text=RESCHEDULE BOOKING').last
                await confirm_btn.click()
                await page.wait_for_timeout(3000)

                # Step 5: Extract and confirm booking details
                print("📋 Step 5: Confirming booking details...")
                self.booking_info = await self._extract_booking_info(page)
                if self.booking_info:
                    print(f"   Found: {self.booking_info}")

                # Click YES to confirm booking
                confirm_yes = page.locator('text=YES').last
                if await confirm_yes.is_visible(timeout=5000):
                    await confirm_yes.click()
                    await page.wait_for_timeout(3000)

                # Step 6: Click "SEE MORE SUGGESTED CENTRES" if available
                try:
                    see_more = page.locator('text=SEE MORE SUGGESTED CENTRES')
                    if await see_more.is_visible(timeout=5000):
                        print("🔍 Expanding all centres...")
                        await see_more.click()
                        await page.wait_for_timeout(2000)
                except:
                    pass

                # Step 7: Extract availability
                print("📅 Step 6: Extracting available dates...")
                slots = await self._extract_availability(page)

                # Save state for future use
                if self.save_state:
                    await context.storage_state(path=str(self.STATE_FILE))
                    print(f"💾 Session state saved to {self.STATE_FILE}")

            except PlaywrightTimeout as e:
                print(f"⚠️ Timeout: {e}")
            except Exception as e:
                print(f"❌ Error: {e}")
                await page.screenshot(path='nct_reschedule_error.png')
                print("📸 Error screenshot saved to nct_reschedule_error.png")
            finally:
                await browser.close()

        return self.vehicle_info, self.booking_info, slots


def print_results(vehicle: Optional[VehicleInfo], slots: list[NCTSlot], output_json: bool = False, booking: Optional[BookingInfo] = None):
    """Print results"""
    if output_json:
        result = {
            "vehicle": vars(vehicle) if vehicle else None,
            "booking": booking.to_dict() if booking else None,
            "slots": [s.to_dict() for s in slots],
            "checked_at": datetime.now().isoformat()
        }
        print(json.dumps(result, indent=2, default=str))
        return

    print("\n" + "=" * 70)

    if vehicle:
        print(f"🚗 VEHICLE: {vehicle}")

    if booking:
        print(f"📋 CURRENT BOOKING: {booking}")
    
    if not slots:
        print("\n❌ No available slots found.")
        print("\n💡 Tips:")
        print("   • Try again early morning (6-8 AM)")
        print("   • Cancellations appear throughout the day")
        print("   • Be flexible with test centres")
        return
    
    print(f"\n✅ Found {len(slots)} available slot(s):\n")
    print("-" * 70)
    print(f"{'#':<3} {'Centre':<30} {'Date':<18} {'Days Away':<12}")
    print("-" * 70)
    
    for i, slot in enumerate(slots, 1):
        days = slot.days_from_now()
        marker = "⭐" if slot.is_selected else "  "
        
        if days <= 7:
            days_str = f"📅 {days} days"
        elif days <= 30:
            days_str = f"📆 {days} days"
        else:
            days_str = f"{days} days"
        
        print(f"{marker}{i:<2} {slot.centre:<30} {slot.date.strftime('%a %d %b %Y'):<18} {days_str}")
    
    print("-" * 70)
    
    # Summary
    nearest = slots[0]
    print(f"\n🎯 NEAREST: {nearest.centre} on {nearest.date.strftime('%A %d %B %Y')}")
    print(f"   That's {nearest.days_from_now()} days from today")
    
    # Group by centre for quick overview
    print("\n📊 Summary by Centre:")
    centres = {}
    for slot in slots:
        if slot.centre not in centres:
            centres[slot.centre] = slot
    
    for centre, slot in sorted(centres.items(), key=lambda x: x[1].date):
        print(f"   • {centre}: {slot.date.strftime('%d %b %Y')} ({slot.days_from_now()} days)")


async def main():
    parser = argparse.ArgumentParser(description="NCT Availability Checker - Full Flow")
    parser.add_argument("--reg", "-r", default="05D77568", help="Vehicle registration number")
    parser.add_argument("--booking-id", "-b", default="81082214", help="Existing booking ID (for reschedule flow)")
    parser.add_argument("--headless", action="store_true", default=True, help="Run headless (default)")
    parser.add_argument("--no-headless", dest="headless", action="store_false", help="Show browser window")
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    parser.add_argument("--no-save", dest="save_state", action="store_false", default=True, help="Don't save session state")
    args = parser.parse_args()

    if not PLAYWRIGHT_AVAILABLE:
        print("❌ Playwright not installed. Run:")
        print("   pip install playwright beautifulsoup4")
        print("   playwright install chromium")
        return

    is_reschedule = args.booking_id is not None

    if not args.json:
        print("=" * 70)
        if is_reschedule:
            print("🚗 NCT Availability Checker - Reschedule Flow")
            print(f"   Registration: {args.reg.upper()}")
            print(f"   Booking ID: {args.booking_id}")
        else:
            print("🚗 NCT Availability Checker - Full Flow")
            print(f"   Registration: {args.reg.upper()}")
        print("=" * 70)

    checker = NCTCheckerFull(
        registration=args.reg,
        headless=args.headless,
        save_state=args.save_state,
        booking_id=args.booking_id
    )

    if is_reschedule:
        vehicle, booking, slots = await checker.check_reschedule_availability()
        print_results(vehicle, slots, output_json=args.json, booking=booking)
    else:
        vehicle, slots = await checker.check_availability()
        print_results(vehicle, slots, output_json=args.json)


if __name__ == "__main__":
    asyncio.run(main())
