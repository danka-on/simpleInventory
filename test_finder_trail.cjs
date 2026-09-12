const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const html = fs.readFileSync('templates/finder.html', 'utf8').replaceAll('\r\n', '\n');
const trailCode = html.slice(html.indexOf('// ===== Trail ====='), html.indexOf('// ===== Records ====='));
const historyCode = html.slice(html.indexOf('function groupHistory('), html.indexOf('function renderHistory('));
assert.ok(trailCode.length > 1000 && historyCode.length > 100);

const elements = new Map();
function element(id) {
  const classes = new Set();
  return {id, value: '', checked: false, hidden: false, innerHTML: '', open: false, events: {}, listeners: {},
    set textContent(value) { this.innerHTML = value; }, get textContent() { return this.innerHTML; },
    classList: {toggle: (name, force) => { force ? classes.add(name) : classes.delete(name); }, add: name => classes.add(name), remove: name => classes.delete(name), contains: name => classes.has(name)},
    addEventListener(name, callback) { this.listeners[name] = callback; }, scrollIntoView() {}, dataset: {}};
}
const escape = value => String(value ?? '').replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;').replaceAll('"', '&quot;').replaceAll("'", '&#39;');
const pending = [];
const context = {
  URLSearchParams, AbortController, Number, String, Set, Map, Date, CSS: {escape: value => value}, setTimeout() {}, navigator: {},
  esc: escape, attrEsc: escape, t: key => key, missingImg: 'data:image/svg+xml;utf8,missing',
  formatUpcDisplay: value => String(value), sameUpc: (a, b) => String(a) === String(b),
  locationChip: (loc, pic) => loc ? `<span class="loc">${escape(loc)}</span>` : '',
  recordOriginalLink: (source, record) => source.id === 'sold.db:orders' ? {href: '/searchrack?db=sold&q=' + encodeURIComponent(record.order_id || ''), label: 'Open sold order'} : null,
  recordDetailsHtml: record => '<dl>' + Object.keys(record).join(',') + '</dl>',
  showPrepPopupHtml(markup) { context.dialog = markup; },
  document: {
    getElementById(id) { if (!elements.has(id)) elements.set(id, element(id)); return elements.get(id); },
    querySelectorAll() { return []; }, querySelector() { return null; },
  },
  fetch(url) { return new Promise(resolve => pending.push({url, resolve})); },
  window: {innerWidth: 1400},
};
context.familyToggle = context.document.getElementById('recordsFamily');
vm.createContext(context);
vm.runInContext(historyCode + '\n' + trailCode, context);
// Top-level const bindings live in the context's script scope, not on the global object.
const trailState = vm.runInContext('trailState', context);
const TILE_KINDS = vm.runInContext('TILE_KINDS', context);

(async () => {
  // Kind metadata and date helpers
  assert.equal(context.kindMeta('pulled').label, 'Pulled');
  assert.equal(context.kindMeta('mystery_kind').label, 'mystery kind');
  const now = new Date(2026, 8, 12, 15, 0, 0);
  assert.ok(context.dayLabel('2026-09-12T10:06:31', now).startsWith('today · '));
  assert.ok(context.dayLabel('2026-09-11T23:59:00', now).startsWith('yesterday · '));
  assert.ok(context.dayLabel('2026-09-05', now).startsWith('7 days ago · '));
  assert.ok(!context.dayLabel('2026-01-05', now).includes('ago'));
  assert.equal(context.dayLabel('not a date', now), 'not a date');
  assert.equal(context.timeLabel('2026-09-12'), '');
  assert.ok(/\d:\d\d [AP]M/.test(context.timeLabel('2026-09-12T10:06:31')));
  assert.ok(context.whenLabel('2026-04-29 18:31:00').includes('Apr 29, 2026'));

  // Event rendering escapes everything and shows the move arrow and status pills
  const moved = {id: 'rackhistory.db:removed_items:3', kind: 'moved', category: 'inventory', ts: '2026-08-10 09:00:00', sort: '2026-08-10T09:00:00',
    title: 'Moved 1 unit <b>x</b>', detail: 'locationmoved', position: 'hr2s3', from_position: 'hr1s1', to_position: 'hr2s3"', qty: 1, delta: 0,
    order_id: '', store: '', status: 'applied', note: '<script>alert(1)</script>', undone: false, pending: false, upc: '028199270349-2', source_id: 'rackhistory.db:removed_items', record: {id: 3, old_quantity: 2, new_quantity: 2}};
  const movedHtml = context.eventHtml(moved, true);
  assert.ok(!movedHtml.includes('<b>x</b>') && movedHtml.includes('&lt;b&gt;x&lt;/b&gt;'));
  assert.ok(!movedHtml.includes('<script>') && movedHtml.includes('&lt;script&gt;'));
  assert.ok(movedHtml.includes('hr1s1') && movedHtml.includes('<span class="arrow">→</span>') && movedHtml.includes('hr2s3&quot;'));
  assert.ok(movedHtml.includes('028199270349-2'), 'family mode shows the unit UPC');
  assert.ok(!movedHtml.includes('2 → 2'), 'moves do not show a quantity change');
  assert.ok(!movedHtml.includes('>applied<'), 'applied status is implied');
  const sold = {id: 'sold.db:orders:1615', kind: 'sold', category: 'sales', ts: '2025-12-03T12:42:33Z', sort: '2025-12-03T12:42:33', title: 'Sold 1 unit on amazon · order 112-9043165',
    detail: 'not pulled from shelf yet', position: '', from_position: '', to_position: '', qty: 1, delta: null, order_id: '112-9043165', store: 'amazon', status: 'unpulled', note: '',
    undone: false, pending: false, upc: '028199270349', source_id: 'sold.db:orders', record: {id: 1615, order_id: '112-9043165'}, price: 30, url: 'javascript:alert(1)'};
  const soldHtml = context.eventHtml(sold, false);
  assert.ok(soldHtml.includes('pill bad">unpulled') && soldHtml.includes('$30.00'));
  assert.ok(soldHtml.includes('href="/searchrack?db=sold&amp;q=112-9043165"') || soldHtml.includes('href="/searchrack?db=sold&q=112-9043165"'));
  assert.ok(!soldHtml.includes('javascript:'), 'unsafe listing urls are ignored');
  const undone = context.eventHtml({...moved, undone: true, pending: true, from_position: '', to_position: ''}, false);
  assert.ok(undone.includes('class="ev undone pending"') && undone.includes('>undone<'));

  // Flags
  assert.ok(context.flagHtml({level: 'alert', text: '<x>', event_ids: ['a', 'b']}).includes('data-focus="a,b"'));
  assert.ok(context.flagHtml({level: 'info', text: 'ok', event_ids: []}).includes('static'));
  assert.ok(!context.flagHtml({level: 'alert', text: '<x>', event_ids: []}).includes('<x>'));

  // Visibility filters
  trailState.hideUndone = true;
  assert.equal(context.eventVisible({...moved, undone: true}), false);
  trailState.hideUndone = false;
  trailState.category = 'sales';
  assert.equal(context.eventVisible(moved), false);
  assert.equal(context.eventVisible(sold), true);
  trailState.kinds = ['moved'];
  assert.equal(context.eventVisible(moved), true);
  assert.equal(context.eventVisible(sold), false);
  trailState.focusIds = new Set([sold.id]);
  assert.equal(context.eventVisible(sold), true);
  assert.equal(context.eventVisible(moved), false);
  trailState.focusIds = null; trailState.kinds = null; trailState.category = 'all';

  // Full render: not on any shelf, last exit, tiles, clues, day grouping
  trailState.upc = '028199270349';
  trailState.data = {
    upc: '028199270349', upc_display: '028199270349', family: false,
    identity: {title: 'Godinger <glass>', image: '', aliases: ['Amazon name'], lots: ['16981955'], custom: false},
    stock: [{searchrack_id: 11, quantity: 0, position: '', pending_deletion: '2026-09-08', upc: '028199270349'}],
    events: [moved, sold], flags: [{level: 'alert', text: '1 unit sold but never pulled', event_ids: [sold.id]}],
    ledger: {received: 2, shelf_adds: 2, pulled: 2, sold_units: 4, returned: 0, fba_units: 0, on_shelf: 0, moves: 1, adjustments: 0, sold_orders: 3},
    last_seen: {position: 'hr2s3', ts: '2026-09-12T10:06:31', kind: 'pulled', id: 'rackhistory.db:removed_items:8194'},
    last_exit: {position: 'hr2s3', ts: '2026-09-12T10:06:31', kind: 'pulled', id: 'rackhistory.db:removed_items:8194', title: 'Pulled 2 units from hr2s3 for order 111', order_id: '111'},
    errors: [], unavailable: [],
  };
  context.renderTrail();
  const content = elements.get('trailContent');
  assert.equal(content.hidden, false);
  assert.equal(elements.get('trailEmpty').hidden, true);
  assert.ok(content.innerHTML.includes('Godinger &lt;glass&gt;'));
  assert.ok(content.innerHTML.includes('not_on_shelf') && content.innerHTML.includes('last_left') && content.innerHTML.includes('<b>hr2s3</b>'));
  assert.ok(content.innerHTML.includes('data-jump="rackhistory.db:removed_items:8194"'));
  assert.ok(content.innerHTML.includes('scheduled for deletion on 2026-09-08'));
  assert.ok(content.innerHTML.includes('data-tile="sold_units"') && content.innerHTML.includes('<span class="n">4</span>'));
  assert.ok(content.innerHTML.includes('1 move(s)') && content.innerHTML.includes('3 order(s)'));
  assert.ok(content.innerHTML.includes('data-focus="sold.db:orders:1615"'));
  assert.ok(content.innerHTML.includes('LOT 16981955') && content.innerHTML.includes('Amazon name'));
  assert.equal((content.innerHTML.match(/class="day"/g) || []).length, 2, 'two different days produce two day headers');
  assert.ok(content.innerHTML.indexOf('Moved 1 unit') < content.innerHTML.indexOf('Sold 1 unit'), 'events keep server order');
  assert.ok(content.innerHTML.includes('data-category="sales"') && content.innerHTML.includes('data-category="inventory"'));
  assert.ok(!content.innerHTML.includes('data-category="prep"'), 'empty categories get no filter chip');

  // Filtering to a tile shows only that kind, and the focus bar appears
  trailState.kinds = TILE_KINDS.sold_units;
  context.renderTrail();
  assert.ok(content.innerHTML.includes('Sold 1 unit') && !content.innerHTML.includes('Moved 1 unit'));
  assert.ok(content.innerHTML.includes('focus-bar show'));
  trailState.kinds = null;

  // Detail drawer for an event
  const trailClick = content.listeners.click;
  trailClick({target: {closest: selector => selector === '[data-event-detail]' ? {dataset: {eventDetail: sold.id}} : null, id: ''}});
  assert.ok(context.dialog.includes('Sold 1 unit') && context.dialog.includes('Open sold order') && context.dialog.includes('<dl>id,order_id</dl>'));

  // Stock on the shelf renders location chips instead of the not-on-shelf state
  trailState.data.stock = [{searchrack_id: 10, quantity: 2, position: 'hr2s3', pictureposition: '', upc: '028199270349', warehouse_note: 'Top shelf'}];
  trailState.data.ledger.on_shelf = 2;
  context.renderTrail();
  assert.ok(content.innerHTML.includes('× 2') && !content.innerHTML.includes('not_on_shelf') && content.innerHTML.includes('warehouse-note-open-btn'));

  // Family mode marks related units only when the trail really mixes them
  trailState.data.family = true;
  trailState.data.events = [sold];
  context.renderTrail();
  assert.ok(!content.innerHTML.includes('showing_related'), 'no marker when every record is the exact UPC');
  assert.ok(!content.innerHTML.includes('<span class="pill">028199270349</span>'), 'no redundant per-unit UPC pills');
  trailState.data.events = [moved, sold];
  context.renderTrail();
  assert.ok(content.innerHTML.includes('showing_related (1)'), 'one related unit is named in the header');
  assert.ok(content.innerHTML.includes('<span class="pill">028199270349-2</span>'), 'the mixed-in unit is labelled on its event');
  trailState.data.family = false;

  // openTrail: family is passed through and stale responses are ignored
  const first = context.openTrail('111111111111', {family: true});
  const second = context.openTrail('222222222222', {family: true});
  assert.equal(pending.length, 2);
  assert.ok(pending[1].url.includes('family=1'));
  pending[1].resolve({ok: true, json: async () => ({upc: '222222222222', family: true, events: [sold], stock: [], identity: {title: 'Family item'}, ledger: {}, flags: []})});
  await second;
  pending[0].resolve({ok: true, json: async () => ({upc: '111111111111', events: [], stock: [], identity: {title: 'Stale'}, ledger: {}, flags: []})});
  await first;
  assert.equal(trailState.upc, '222222222222');
  assert.equal(trailState.family, true);
  assert.ok(content.innerHTML.includes('Family item') && !content.innerHTML.includes('Stale'));
  assert.equal(elements.get('recordsUpc').value, '222222222222');
  assert.equal(elements.get('recordsSection').hidden, false);

  // Errors are shown and never leak markup
  const failed = context.openTrail('333333333333');
  pending[2].resolve({ok: false, json: async () => ({error: '<b>Unavailable</b>'})});
  await failed;
  assert.ok(content.innerHTML.includes('&lt;b&gt;Unavailable&lt;/b&gt;'));

  // Ledger memory grouping
  const groups = context.groupHistory([
    {barcode: '111', title: 'Lamp', removed_at: '2026-01-01', item_position: 'a1', removal_type: 'add_to_shelf'},
    {barcode: '111', title: '', removed_at: '2026-02-01', item_position: 'b2', removal_type: 'inventoryremoved'},
    {barcode: '222', title: 'Vase', removed_at: '2026-01-15', item_position: '', removal_type: 'manual_edit'},
  ]);
  assert.equal(groups.map(group => group.barcode).join(','), '111,222');
  assert.equal(groups[0].count, 2);
  assert.equal(groups[0].last.removal_type, 'inventoryremoved');
  assert.equal(groups[0].positions.join(','), 'a1,b2');
  console.log('Finder trail rendering, filtering, related-unit marking, stale requests, and ledger grouping passed');
})().catch(error => { console.error(error); process.exitCode = 1; });
