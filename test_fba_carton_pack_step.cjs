/**
 * Pack-step carton cost advisories.
 *
 * The freight checks have to land on step 3 while cartons can still be repacked,
 * so they run client-side in fba_prep.html rather than only in the Python
 * preflight. This pulls the real functions out of the template and drives them
 * with the cellar 3 cartons, which clear every cliff yet bill on cube.
 */
const assert = require('assert');
const fs = require('fs');
const path = require('path');

const template = fs.readFileSync(path.join(__dirname, 'templates', 'fba_prep.html'), 'utf8');

function extract(startMarker, endMarker) {
  const start = template.indexOf(startMarker);
  assert.notStrictEqual(start, -1, 'missing in template: ' + startMarker);
  const end = template.indexOf(endMarker, start);
  assert.notStrictEqual(end, -1, 'missing end marker after: ' + startMarker);
  return template.slice(start, end);
}

// Everything from the thresholds down to the end of packFreightSummaryHtml.
const source = extract('const CARTON_LIMITS=', '    const boxMinimums=');

const scope = {
  fmtNumber: value => Number(value).toLocaleString('en-US', { maximumFractionDigits: 2 }),
  esc: value => String(value),
  boxCubicInches: box => {
    const dims = ['length_in', 'width_in', 'height_in'].map(f => Number(box?.[f] || 0));
    return dims.every(v => v > 0) ? dims[0] * dims[1] * dims[2] : 0;
  },
  state: { amazon: { boxes: [] } },
  expectedByMsku: () => new Map(),
};

const factory = new Function(
  'fmtNumber', 'esc', 'boxCubicInches', 'state', 'expectedByMsku',
  source + '\nreturn {CARTON_LIMITS,CARTON_DENSITY_BREAK_EVEN,cartonSides,cartonMeasured,cartonGirth,' +
  'boxFreightText,boxFreightHtml,packFreightNotes,packFreightSummaryHtml};'
);
const api = factory(scope.fmtNumber, scope.esc, scope.boxCubicInches, scope.state, () => scope.expected);

const box = (id, l, w, h, wt) => ({ local_id: id, length_in: l, width_in: w, height_in: h, weight_lb: wt, contents: [] });

const CELLAR3 = [
  box('BOX-01', 34, 22, 18, 33.0), box('BOX-02', 34, 22, 18, 30.0),
  box('BOX-03', 34, 22, 18, 31.3), box('BOX-04', 34, 22, 18.5, 31.3),
  box('BOX-05', 23, 20, 18, 18.2), box('BOX-06', 24, 15, 15, 28.2),
  box('BOX-08', 34, 22, 18, 33.0), box('BOX-09', 34, 22, 19, 33.0),
  box('BOX-10', 36, 22, 18, 36.4), box('BOX-11', 36, 22, 19, 37.0),
  box('BOX-12', 34, 23, 19, 37.0),
];

let passed = 0;
function check(name, fn) {
  try { fn(); console.log('  ok   ' + name); passed += 1; }
  catch (err) { console.log('  FAIL ' + name + '\n       ' + err.message); process.exitCode = 1; }
}

console.log('geometry');
check('girth is longest plus twice the other two, whatever order they were typed', () => {
  assert.strictEqual(api.cartonGirth(box('B', 36, 22, 18, 30)), 116);
  assert.strictEqual(api.cartonGirth(box('B', 18, 36, 22, 30)), 116);
});
check('an unmeasured carton says nothing at all', () => {
  assert.strictEqual(api.boxFreightText(box('B', 0, 0, 0, 0)).text, '');
  assert.strictEqual(api.boxFreightText(box('B', 34, 22, 18, 0)).text, '');
  assert.strictEqual(api.boxFreightHtml(box('B', 0, 0, 0, 0)), '');
});

console.log('per-carton cliffs');
check('large package fires above 130 girth, not at it, and says how much to lose', () => {
  assert.ok(!/Large package/.test(api.boxFreightText(box('B', 36, 24, 23, 40)).text));
  const over = api.boxFreightText(box('B', 36, 24, 24, 40));
  assert.match(over.text, /Large package/);
  assert.strictEqual(over.kind, 'error');
  assert.match(over.text, /Take 1 in off width\+height/);
});
check('girth heads-up appears only in the band below the limit', () => {
  assert.match(api.boxFreightText(box('B', 36, 24, 21, 40)).text, /only 4 in under/);
  assert.ok(!/under the large-package limit/.test(api.boxFreightText(box('B', 24, 15, 15, 40)).text));
});
check('over 50 lb names the right lift label and says splitting helps', () => {
  const team = api.boxFreightText(box('B', 24, 18, 12, 60)).text;
  assert.match(team, /Team Lift/);
  assert.match(team, /Moving weight to another carton does help/);
  assert.match(api.boxFreightText(box('B', 24, 18, 12, 120)).text, /Mechanical Lift/);
  assert.ok(!/lb is over 50 lb/.test(api.boxFreightText(box('B', 24, 18, 12, 50)).text));
});
check('cellar 3 trips no dimension or weight cliff, only density', () => {
  for (const carton of CELLAR3) {
    const text = api.boxFreightText(carton).text;
    assert.ok(!/Large package/.test(text), carton.local_id + ' should clear large package');
    assert.ok(!/Additional handling/.test(text), carton.local_id + ' should clear additional handling');
    assert.ok(!/lb is over 50 lb/.test(text), carton.local_id + ' should be under 50 lb');
  }
});

console.log('cube billing');
check('a dense carton says nothing; a light bulky one prices the air', () => {
  assert.strictEqual(api.boxFreightText(box('B', 24, 18, 12, 45)).text, '');
  const bulky = api.boxFreightText(box('BOX-10', 36, 22, 18, 36.4)).text;
  assert.match(bulky, /Mostly air/);
  assert.match(bulky, /bills as 102\.56 lb, not its 36\.4 lb/);
});
check('the per-carton line gives a dollar figure and the fix', () => {
  const text = api.boxFreightText(box('BOX-10', 36, 22, 18, 36.4)).text;
  assert.match(text, /\$29\.77/);
  assert.match(text, /A smaller box is the only fix/);
  assert.match(text, /every 139 cubic inches you cut is 1 lb off/);
});
check('cellar 3 cartons report cube but still trip no cliff', () => {
  for (const carton of CELLAR3) {
    const text = api.boxFreightText(carton).text;
    assert.ok(!/Large package/.test(text), carton.local_id);
    assert.ok(!/Additional handling/.test(text), carton.local_id);
  }
});

console.log('shipment summary');
check('the summary prices the air across the shipment', () => {
  scope.state.amazon.boxes = CELLAR3;
  scope.expected = new Map([['a', 20]]);
  const note = api.packFreightNotes().find(n => /lb of air/.test(n));
  assert.ok(note, 'expected an air note');
  assert.match(note, /348\.4 lb but bill as 1,005\.38 lb/);
  assert.match(note, /Splitting cartons does not help/);
  assert.match(note, /All 11 cartons/);
});
check('partial measurement is labelled as provisional', () => {
  scope.state.amazon.boxes = [CELLAR3[0], CELLAR3[1], box('BOX-99', 0, 0, 0, 0)];
  assert.match(api.packFreightNotes().find(n => /lb of air/.test(n)), /2 of 3 cartons measured so far/);
});
check('LTL is suggested at about a pallet and not for a single carton', () => {
  scope.state.amazon.boxes = CELLAR3;
  assert.ok(api.packFreightNotes().some(note => /pricing LTL/.test(note)));
  scope.state.amazon.boxes = [box('B', 24, 18, 12, 40)];
  assert.ok(!api.packFreightNotes().some(note => /pricing LTL/.test(note)));
});
check('summary renders nothing when there is nothing to say', () => {
  scope.state.amazon.boxes = [box('B', 24, 18, 12, 45)];
  scope.expected = new Map([['a', 20]]);
  assert.strictEqual(api.packFreightSummaryHtml(), '');
});
check('summary says it is advisory when it does render', () => {
  scope.state.amazon.boxes = CELLAR3;
  scope.expected = new Map([['a', 20]]);
  const html = api.packFreightSummaryHtml();
  assert.match(html, /never blocking/);
  assert.match(html, /class="pack-freight"/);
});

console.log('\nwiring');
check('the advisory renders on both carton cards and in the pack pane', () => {
  assert.strictEqual((template.match(/\$\{boxFreightHtml\(box\)\}/g) || []).length, 2);
  assert.ok(template.includes('${packFreightSummaryHtml()}'));
});
check('typing a dimension refreshes the carton advisory', () => {
  const refresh = extract('function refreshBoxCompliance', '\n    function ');
  assert.match(refresh, /boxFreightText\(box\)/);
  assert.match(refresh, /box-freight/);
});

console.log('\n' + passed + ' passed');
