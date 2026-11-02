const assert = require('node:assert/strict');
const test = require('node:test');
const fs = require('node:fs');
const vm = require('node:vm');
const {parseSlots, validateCapture, buildBookmarklet} = require('../static/browser_search.js');
const fixture = fs.readFileSync(__dirname + '/fixtures/ncts_availability.txt', 'utf8');

test('current NCTS cards produce all six centres sorted by earliest date', () => {
    const slots = parseSlots(fixture);
    assert.equal(slots.length, 6);
    assert.deepEqual(slots[0], {centre: 'Naas', date: '2026-11-12'});
    assert.deepEqual(slots[1], {centre: 'Fonthill', date: '2027-02-18'});
});
test('supports compact table rows, deduplicates and rejects impossible dates', () => {
    const slots = parseSlots('Naas\t12th November 2026\tSelect\nNaas\t12th November 2026\nX\t31st Feb 2027\nY\t1st Bogus 2027');
    assert.deepEqual(slots, [{centre: 'Naas', date: '2026-11-12'}]);
});
test('empty or blocked pages never become a successful capture', () => {
    assert.deepEqual(parseSlots('Access Denied. Human verification required.'), []);
    assert.throws(() => validateCapture({version: 1, capturedAt: new Date().toISOString(), slots: []}), /No valid NCTS dates/);
});
test('import rejects HTML injection and invalid dates', () => {
    const payload = {version: 1, capturedAt: new Date().toISOString(), slots: [{centre: '<img src=x onerror=alert(1)>', date: '2027-01-01'}]};
    assert.throws(() => validateCapture(payload), /Invalid centre/);
    payload.slots[0] = {centre: 'Naas', date: '2027-02-31'};
    assert.throws(() => validateCapture(payload), /Invalid date/);
});
test('bookmark transfers only centre names and dates, without cookies or vehicle details', () => {
    let opened;
    const script = buildBookmarklet('https://nct-checker.fly.dev').slice('javascript:'.length);
    vm.runInNewContext(script, {
        location: {hostname: 'www.ncts.ie'}, document: {body: {innerText: 'Registration: PRIVATE\n' + fixture}},
        window: {open: (...args) => opened = args}, alert: () => assert.fail('unexpected alert'),
    });
    assert.equal(opened[2], 'noopener,noreferrer');
    const url = new URL(opened[0]);
    assert.equal(url.origin, 'https://nct-checker.fly.dev');
    const payload = JSON.parse(decodeURIComponent(url.hash.slice('#ncts-data='.length)));
    assert.deepEqual(Object.keys(payload).sort(), ['capturedAt', 'slots', 'version']);
    assert.equal(payload.slots.length, 6);
    assert.ok(!JSON.stringify(payload).includes('PRIVATE'));
    payload.slots.forEach(slot => assert.deepEqual(Object.keys(slot).sort(), ['centre', 'date']));
});
test('bookmark refuses other websites and does not open a booking or payment', () => {
    let message;
    vm.runInNewContext(buildBookmarklet('https://nct-checker.fly.dev').slice('javascript:'.length), {
        location: {hostname: 'example.com'}, alert: text => message = text,
        window: {open: () => assert.fail('must not open')},
    });
    assert.match(message, /official NCTS availability page/);
});
