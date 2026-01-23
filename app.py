"""
NCT Availability Checker Web App
Flask-based web application for checking NCT test slot availability
With email notifications for earlier slot availability
"""

import asyncio
import re
import os
import smtplib
import threading
import time
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime
from dataclasses import dataclass, asdict
from typing import Optional
from flask import Flask, render_template, request, jsonify
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeout

app = Flask(__name__)

# In-memory storage for alerts (use Redis/DB in production)
alerts_store = {}
alerts_lock = threading.Lock()


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
        max_retries = 2

        for attempt in range(max_retries):
            try:
                async with async_playwright() as p:
                    # Use more conservative browser settings for Render
                    browser = await p.chromium.launch(
                        headless=True,
                        args=[
                            '--no-sandbox',
                            '--disable-setuid-sandbox',
                            '--disable-dev-shm-usage',
                            '--disable-gpu',
                            '--single-process',
                            '--no-zygote',
                            '--disable-extensions',
                        ]
                    )
                    context = await browser.new_context(
                        viewport={'width': 1280, 'height': 720},
                        user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
                    )
                    # Set longer default timeout
                    context.set_default_timeout(60000)
                    page = await context.new_page()

                    try:
                        # Step 1: Go to homepage with longer timeout
                        print(f"[Attempt {attempt + 1}] Loading NCTS homepage...")
                        await page.goto(self.BASE_URL, wait_until='domcontentloaded', timeout=45000)
                        await page.wait_for_timeout(2000)

                        # Handle cookie consent
                        try:
                            accept_btn = page.locator('#bs-gdpr-cookies-modal-accept-btn')
                            if await accept_btn.is_visible(timeout=5000):
                                await accept_btn.click()
                                await page.wait_for_timeout(1000)
                        except Exception:
                            pass

                        # Enter registration
                        print(f"[Attempt {attempt + 1}] Entering registration: {self.registration}")
                        reg_input = page.locator('#rid')
                        await reg_input.wait_for(state='visible', timeout=10000)
                        await reg_input.fill(self.registration)
                        await page.wait_for_timeout(500)
                        await page.locator('#btnSearchVehicle').click()

                        # Wait for vehicle confirmation page
                        print(f"[Attempt {attempt + 1}] Waiting for vehicle confirmation...")
                        await page.wait_for_url('**rid=**', timeout=30000)
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

                        print(f"[Attempt {attempt + 1}] Confirming vehicle...")
                        await page.locator('#confirmVehicleYes').click()
                        await page.wait_for_timeout(3000)

                        # Click "SEE MORE SUGGESTED CENTRES" if available
                        try:
                            see_more = page.locator('text=SEE MORE SUGGESTED CENTRES')
                            if await see_more.is_visible(timeout=5000):
                                await see_more.click()
                                await page.wait_for_timeout(2000)
                        except Exception:
                            pass

                        # Extract availability
                        print(f"[Attempt {attempt + 1}] Extracting availability...")
                        slots = await self._extract_availability(page)
                        
                        # Success - break retry loop
                        error = None
                        break

                    except PlaywrightTimeout as e:
                        print(f"[Attempt {attempt + 1}] Timeout: {e}")
                        if attempt < max_retries - 1:
                            print(f"Retrying...")
                            continue
                        error = "Timeout - the NCTS website took too long to respond. Please try again."
                    except Exception as e:
                        print(f"[Attempt {attempt + 1}] Error: {e}")
                        if attempt < max_retries - 1:
                            print(f"Retrying...")
                            continue
                        error = f"Error checking availability: {str(e)}"
                    finally:
                        await browser.close()
                        
            except Exception as e:
                print(f"[Attempt {attempt + 1}] Browser error: {e}")
                if attempt < max_retries - 1:
                    continue
                error = f"Browser error: {str(e)}"

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
        except Exception:
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


# ============== Email Alert System ==============

@dataclass
class Alert:
    """Represents an alert subscription"""
    registration: str
    email: str
    target_days: int  # Alert if slot is within this many days
    current_earliest: int  # Current earliest known days
    created_at: str
    last_checked: str = ""
    last_notified: str = ""

    def to_dict(self):
        return asdict(self)


def send_email(to_email: str, subject: str, html_content: str) -> bool:
    """Send email notification using SMTP"""
    smtp_host = os.environ.get('SMTP_HOST', 'smtp.gmail.com')
    smtp_port = int(os.environ.get('SMTP_PORT', '587'))
    smtp_user = os.environ.get('SMTP_USER', '')
    smtp_pass = os.environ.get('SMTP_PASS', '')
    from_email = os.environ.get('FROM_EMAIL', smtp_user)

    if not smtp_user or not smtp_pass:
        print("Email not configured - SMTP_USER and SMTP_PASS required")
        return False

    try:
        msg = MIMEMultipart('alternative')
        msg['Subject'] = subject
        msg['From'] = from_email
        msg['To'] = to_email

        html_part = MIMEText(html_content, 'html')
        msg.attach(html_part)

        with smtplib.SMTP(smtp_host, smtp_port) as server:
            server.starttls()
            server.login(smtp_user, smtp_pass)
            server.sendmail(from_email, to_email, msg.as_string())

        return True
    except Exception as e:
        print(f"Failed to send email: {e}")
        return False


def send_slot_alert(alert: Alert, slot_info: dict):
    """Send alert about earlier slot availability"""
    subject = f"NCT Alert: Earlier slot available for {alert.registration}!"

    html_content = f"""
    <html>
    <body style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto;">
        <div style="background: linear-gradient(135deg, #1a1a2e 0%, #16213e 100%); color: white; padding: 30px; border-radius: 12px;">
            <h1 style="margin: 0; color: #ffd700;">Earlier NCT Slot Found!</h1>
        </div>
        <div style="padding: 30px; background: #f5f5f5;">
            <p style="font-size: 18px; margin-bottom: 20px;">
                Good news! An earlier NCT slot is now available for <strong>{alert.registration}</strong>.
            </p>

            <div style="background: white; padding: 20px; border-radius: 8px; border-left: 4px solid #ffd700;">
                <h3 style="margin-top: 0; color: #333;">{slot_info.get('centre', 'Unknown Centre')}</h3>
                <p style="font-size: 24px; color: #1a1a2e; margin: 10px 0;">
                    {slot_info.get('date_formatted', 'Unknown Date')}
                </p>
                <p style="color: #666; margin-bottom: 0;">
                    <strong style="color: #ffd700; font-size: 20px;">{slot_info.get('days_away', '?')}</strong> days from today
                </p>
            </div>

            <p style="margin-top: 25px;">
                <a href="https://www.ncts.ie" style="display: inline-block; background: #ffd700; color: #1a1a2e; padding: 15px 30px; text-decoration: none; border-radius: 8px; font-weight: bold;">
                    Book Now on NCTS.ie
                </a>
            </p>

            <p style="color: #888; font-size: 12px; margin-top: 30px;">
                You're receiving this because you set up an alert for slots within {alert.target_days} days.
                Previous earliest was {alert.current_earliest} days away.
            </p>
        </div>
    </body>
    </html>
    """

    return send_email(alert.email, subject, html_content)


@app.route('/api/alerts', methods=['POST'])
def create_alert():
    """Create a new alert subscription"""
    data = request.get_json()
    registration = data.get('registration', '').strip().upper().replace(' ', '').replace('-', '')
    email = data.get('email', '').strip().lower()
    target_days = data.get('target_days', 30)

    if not registration or len(registration) < 5:
        return jsonify({"success": False, "error": "Invalid registration"})

    if not email or '@' not in email:
        return jsonify({"success": False, "error": "Invalid email address"})

    # Check current availability first
    checker = NCTChecker(registration)
    result = run_async(checker.check_availability())

    if not result['success'] or not result['slots']:
        return jsonify({
            "success": False,
            "error": "Could not find availability for this registration. Please check it first."
        })

    current_earliest = result['slots'][0]['days_away']

    alert = Alert(
        registration=registration,
        email=email,
        target_days=target_days,
        current_earliest=current_earliest,
        created_at=datetime.now().isoformat(),
        last_checked=datetime.now().isoformat()
    )

    alert_id = f"{registration}_{email}"

    with alerts_lock:
        alerts_store[alert_id] = alert

    return jsonify({
        "success": True,
        "alert_id": alert_id,
        "message": f"Alert created! You'll be notified when slots within {target_days} days become available.",
        "current_earliest": current_earliest,
        "vehicle": result['vehicle']
    })


@app.route('/api/alerts/<alert_id>', methods=['DELETE'])
def delete_alert(alert_id):
    """Delete an alert subscription"""
    with alerts_lock:
        if alert_id in alerts_store:
            del alerts_store[alert_id]
            return jsonify({"success": True, "message": "Alert deleted"})

    return jsonify({"success": False, "error": "Alert not found"})


@app.route('/api/alerts', methods=['GET'])
def list_alerts():
    """List all alerts (for admin/debugging)"""
    with alerts_lock:
        return jsonify({
            "alerts": [a.to_dict() for a in alerts_store.values()],
            "count": len(alerts_store)
        })


def check_alerts_background():
    """Background task to check alerts periodically"""
    while True:
        time.sleep(300)  # Check every 5 minutes

        with alerts_lock:
            alerts_to_check = list(alerts_store.items())

        for alert_id, alert in alerts_to_check:
            try:
                checker = NCTChecker(alert.registration)
                result = run_async(checker.check_availability())

                if result['success'] and result['slots']:
                    earliest = result['slots'][0]
                    days = earliest['days_away']

                    # Update last checked
                    with alerts_lock:
                        if alert_id in alerts_store:
                            alerts_store[alert_id].last_checked = datetime.now().isoformat()

                    # Check if earlier than before AND within target
                    if days < alert.current_earliest and days <= alert.target_days:
                        # Send notification
                        if send_slot_alert(alert, earliest):
                            with alerts_lock:
                                if alert_id in alerts_store:
                                    alerts_store[alert_id].current_earliest = days
                                    alerts_store[alert_id].last_notified = datetime.now().isoformat()
                            print(f"Alert sent for {alert.registration} to {alert.email}")

            except Exception as e:
                print(f"Error checking alert {alert_id}: {e}")

        # Sleep between checks to avoid hammering the site
        time.sleep(60)


# Start background alert checker (only if not in debug mode)
def start_alert_checker():
    if not app.debug:
        thread = threading.Thread(target=check_alerts_background, daemon=True)
        thread.start()
        print("Background alert checker started")


if __name__ == '__main__':
    start_alert_checker()
    app.run(debug=True, host='0.0.0.0', port=8081)
