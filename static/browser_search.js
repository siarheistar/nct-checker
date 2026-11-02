/* Read-only, human-assisted NCTS search. Never reads cookies or submits a booking. */
(function (root) {
    'use strict';

    function parseSlots(text) {
        const months = ['jan', 'feb', 'mar', 'apr', 'may', 'jun', 'jul', 'aug', 'sep', 'oct', 'nov', 'dec'];
        const lines = String(text).replace(/\u00a0/g, ' ').split(/\r?\n/).map(s => s.trim()).filter(Boolean);
        const slots = [];
        function add(centre, label) {
            const match = /^(\d{1,2})(?:st|nd|rd|th)?\s+([A-Za-z]+)\s+(\d{4})$/.exec(label);
            if (!match || !centre || centre.length > 120 || /[<>]/.test(centre)) return;
            const month = months.indexOf(match[2].slice(0, 3).toLowerCase());
            const day = Number(match[1]), year = Number(match[3]);
            if (month < 0 || year < 2000 || year > 2100) return;
            const date = new Date(Date.UTC(year, month, day));
            if (date.getUTCMonth() !== month || date.getUTCDate() !== day) return;
            const iso = date.toISOString().slice(0, 10);
            if (!slots.some(s => s.centre === centre && s.date === iso)) slots.push({centre, date: iso});
        }
        for (let i = 0; i < lines.length; i++) {
            if (/^(SELECTED CENTRE|OTHER SUGGESTED CENTRE)$/i.test(lines[i]) &&
                /^NEXT AVAILABLE DATE$/i.test(lines[i + 2] || '')) {
                add(lines[i + 1], lines[i + 3] || '');
            }
            const row = /^(.+?)\t+(\d{1,2}(?:st|nd|rd|th)?\s+[A-Za-z]+\s+\d{4})(?:\t|$)/.exec(lines[i]);
            if (row) add(row[1].trim(), row[2]);
        }
        return slots.sort((a, b) => a.date.localeCompare(b.date) || a.centre.localeCompare(b.centre));
    }

    function validateCapture(value) {
        if (!value || value.version !== 1 || !Array.isArray(value.slots) ||
            !value.slots.length || value.slots.length > 100 ||
            typeof value.capturedAt !== 'string' || !Number.isFinite(Date.parse(value.capturedAt))) {
            throw new Error('No valid NCTS dates found. Open the availability page and expand the suggested centres first.');
        }
        const slots = value.slots.map(slot => {
            if (!slot || typeof slot.centre !== 'string' || !slot.centre.trim() ||
                slot.centre.length > 120 || /[<>]/.test(slot.centre) ||
                typeof slot.date !== 'string' || !/^\d{4}-\d{2}-\d{2}$/.test(slot.date)) {
                throw new Error('Invalid centre or date in the imported results.');
            }
            const date = new Date(slot.date + 'T12:00:00Z');
            if (!Number.isFinite(date.getTime()) || date.toISOString().slice(0, 10) !== slot.date) {
                throw new Error('Invalid date in the imported results.');
            }
            return {centre: slot.centre.trim(), date: slot.date};
        });
        return {version: 1, capturedAt: value.capturedAt, slots: slots.sort((a, b) => a.date.localeCompare(b.date))};
    }

    function buildBookmarklet(origin) {
        function capture(destination, parse) {
            if (!['www.ncts.ie', 'ncts.ie'].includes(location.hostname)) {
                alert('Use this bookmark on the official NCTS availability page, after completing Human verification yourself.');
                return;
            }
            const slots = parse(document.body.innerText);
            if (!slots.length) {
                alert('No dates found. Continue to availability and expand SEE MORE SUGGESTED CENTRES first.');
                return;
            }
            // Only centre names and dates leave this page. No cookies, vehicle details or session URLs.
            const payload = {version: 1, capturedAt: new Date().toISOString(), slots};
            window.open(destination + '/#ncts-data=' + encodeURIComponent(JSON.stringify(payload)), '_blank', 'noopener,noreferrer');
        }
        return 'javascript:(' + capture.toString() + ')(' + JSON.stringify(origin) + ',' + parseSlots.toString() + ')';
    }

    const api = {parseSlots, validateCapture, buildBookmarklet};
    if (typeof module !== 'undefined' && module.exports) module.exports = api;
    if (!root.document) return;

    function mount() {
        const results = document.getElementById('results');
        const bookmark = document.getElementById('ncts-read-dates');
        if (!results || !bookmark) return;
        bookmark.href = buildBookmarklet(location.origin);

        function render(value) {
            const capture = validateCapture(value);
            const card = document.createElement('section');
            card.className = 'ncts-browser-results';
            const title = document.createElement('h2');
            title.textContent = 'Available dates from your NCTS session';
            const note = document.createElement('p');
            note.textContent = 'Captured ' + new Date(capture.capturedAt).toLocaleString('en-IE') +
                '. These dates are a snapshot; check again before booking. No appointment has been booked.';
            card.append(title, note);
            const table = document.createElement('table');
            const head = table.createTHead().insertRow();
            ['NCT centre', 'Next available date'].forEach(label => {
                const cell = document.createElement('th'); cell.textContent = label; head.append(cell);
            });
            const body = table.createTBody();
            const hasSession = typeof window.nctOpenSlot === 'function';
            const status = document.createElement('p');
            status.setAttribute('role', 'status');
            async function openSession(request, button) {
                button.disabled = true;
                status.textContent = 'Opening your existing NCTS session…';
                try {
                    const result = await window.nctOpenSlot(request);
                    status.textContent = result && result.success ?
                        'Opened in your original NCTS tab. Choose a time and confirm there yourself.' :
                        (result && result.error) || 'Could not open that date. Use your original NCTS tab.';
                } catch (_) { status.textContent = 'The browser helper is no longer connected. Keep the original NCTS tab open and restart the helper if needed.'; }
                finally { button.disabled = false; }
            }
            function sessionButton(label, accessibleLabel, request) {
                const button = document.createElement('button');
                button.type = 'button'; button.className = 'ncts-session-link';
                button.textContent = label; button.setAttribute('aria-label', accessibleLabel);
                button.addEventListener('click', () => openSession(request, button));
                return button;
            }
            capture.slots.forEach(slot => {
                const row = body.insertRow();
                const dateLabel = new Date(slot.date + 'T12:00:00Z').toLocaleDateString('en-IE', {
                    day: 'numeric', month: 'long', year: 'numeric', timeZone: 'Europe/Dublin'
                });
                const centreCell = row.insertCell(), dateCell = row.insertCell();
                if (hasSession) {
                    centreCell.append(sessionButton(slot.centre, 'Open centre ' + slot.centre,
                        {action: 'centre', centre: slot.centre, date: slot.date}));
                    dateCell.append(sessionButton(dateLabel, 'View times for ' + dateLabel + ' at ' + slot.centre,
                        {action: 'date', centre: slot.centre, date: slot.date}));
                } else {
                    centreCell.textContent = slot.centre; dateCell.textContent = dateLabel;
                }
            });
            card.append(table);
            if (hasSession) {
                const hint = document.createElement('p');
                hint.textContent = 'Click a centre to view its dates, or click a date to view times in your original NCTS tab. Your session stays open; nothing is booked automatically.';
                card.append(hint, sessionButton('Return to my NCTS tab', 'Return to my NCTS tab', {action: 'return'}), status);
            } else {
                const hint = document.createElement('p');
                hint.textContent = 'Keep your original NCTS tab open to book. One-click centre/date buttons are available when results are opened by the local browser helper.';
                card.append(hint);
            }
            results.replaceChildren(card);
            results.classList.add('active');
            results.scrollIntoView({behavior: 'smooth', block: 'start'});
        }

        const message = document.getElementById('ncts-import-message');
        document.getElementById('ncts-import-button').addEventListener('click', () => {
            try {
                const text = document.getElementById('ncts-page-text').value;
                if (text.length > 100000) throw new Error('Please paste only the NCTS availability page.');
                render({version: 1, capturedAt: new Date().toISOString(), slots: parseSlots(text)});
                message.textContent = '';
            } catch (error) { message.textContent = error.message; }
        });
        if (location.hash.startsWith('#ncts-data=')) {
            try {
                if (location.hash.length > 64000) throw new Error('Imported results are too large.');
                render(JSON.parse(decodeURIComponent(location.hash.slice('#ncts-data='.length))));
            } catch (error) {
                document.getElementById('ncts-browser-search').open = true;
                message.textContent = error.message;
            }
        }
    }
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', mount);
    else mount();
})(typeof window === 'undefined' ? {} : window);
