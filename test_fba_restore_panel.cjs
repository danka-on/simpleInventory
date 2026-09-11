// The finished-shipment restore panel: what it asks Amazon, and what it refuses to do.
const assert = require('node:assert/strict'), fs = require('node:fs'), vm = require('node:vm');
const html = fs.readFileSync('templates/fba_prep.html', 'utf8');
function block(a, b) { const start = html.indexOf(a); const end = html.indexOf(b, start); assert.ok(start >= 0 && end > start, `Missing source: ${a}`); return html.slice(start, end); }

const elements = {
  restoreResult: {className: '', innerHTML: ''},
  restoreSessionSelect: {value: '', innerHTML: ''},
  restoreListText: {value: ''},
  restoreListBtn: {onclick: null},
};
const calls = [];
let nextResponse = null;
let confirmed = true;
const loaded = [];
const state = {sessionId: null, amazon: {}};

const context = vm.createContext({
  state,
  $: id => elements[id] || null,
  esc: value => String(value ?? ''),
  confirm: () => confirmed,
  setStatus() {},
  renderAmazonWorkflow() {},
  followCurrentWorkflowStep() {},
  refreshSessions: async () => {},
  loadSession: async id => { loaded.push(Number(id)); },
  readJsonResponse: async response => response.body,
  fetch: async (url, options) => {
    calls.push({url, method: options?.method || 'GET', body: options?.body ? JSON.parse(options.body) : null});
    if (!nextResponse) throw new Error('no stubbed response');
    const response = nextResponse; nextResponse = null; return response;
  },
});
vm.runInContext(block('    function restoreConfirmText(', '    async function resetPackedUnit('), context);

const ok = body => ({ok: true, body: Object.assign({success: true}, body)});
const fail = error => ({ok: false, body: {success: false, error}});

(async () => {
  // Only finished shipments that actually reached Amazon are offered.
  nextResponse = ok({sessions: [
    {id: 8, session_name: 'cellar 3', total_units: 122, amazon_summary: {inbound_plan_id: 'wf-8', box_count: 11}},
    {id: 9, session_name: 'draft only', total_units: 4, amazon_summary: {inbound_plan_id: '', box_count: 0}},
  ]});
  await context.loadFinishedSessions();
  assert.match(calls[0].url, /\/api\/fba-prep\/sessions\?status=completed/);
  assert.match(elements.restoreSessionSelect.innerHTML, /cellar 3 · 122 units · 11 cartons/);
  assert.doesNotMatch(elements.restoreSessionSelect.innerHTML, /draft only/);

  // Nothing is checked until a shipment is chosen.
  await context.checkFinishedShipment();
  assert.match(elements.restoreResult.innerHTML, /Choose a finished shipment first/);
  assert.equal(calls.length, 1, 'no Amazon call without a selection');

  // A live plan is reported, never offered for rebuild.
  elements.restoreSessionSelect.value = '8';
  nextResponse = ok({restore: {plan_id: 'wf-8', box_count: 11, packed_units: 122, pack_scan_count: 122, cancellation: {}}});
  await context.checkFinishedShipment();
  assert.equal(calls[1].method, 'GET');
  assert.match(elements.restoreResult.innerHTML, /still reports plan wf-8 as live/);
  assert.doesNotMatch(elements.restoreResult.innerHTML, /restoreListBtn/);

  // A half-cancelled plan names the shipment that is still Amazon's.
  nextResponse = ok({restore: {plan_id: 'wf-8', box_count: 11, packed_units: 122, pack_scan_count: 122, cancellation: {
    plan_status: 'ACTIVE', restorable: false, partial: true,
    cancelled_shipments: [{shipmentConfirmationId: 'FBA-A', warehouseId: 'SCK8'}],
    live_shipments: [{shipmentConfirmationId: 'FBA-B', status: 'IN_TRANSIT'}]}}});
  await context.checkFinishedShipment();
  assert.match(elements.restoreResult.innerHTML, /FBA-B \(IN_TRANSIT\)/);
  assert.match(elements.restoreResult.innerHTML, /Cancel it in Seller Central/);
  assert.doesNotMatch(elements.restoreResult.innerHTML, /Restore this shipment/);

  // A cancelled plan shows what is kept and offers the rebuild behind a typed confirmation.
  nextResponse = ok({restore: {plan_id: 'wf-8', box_count: 11, packed_units: 122, pack_scan_count: 122, cancellation: {
    plan_status: 'VOIDED', restorable: true, plan_cancelled: true,
    cancelled_shipments: [{shipmentConfirmationId: 'FBA19PL9TWJH', warehouseId: 'LBE1'},
                          {shipmentConfirmationId: 'FBA19PL7W2P5', warehouseId: 'SCK8'}],
    live_shipments: []}}});
  await context.checkFinishedShipment();
  assert.match(elements.restoreResult.innerHTML, /reports plan wf-8 as VOIDED/);
  assert.match(elements.restoreResult.innerHTML, /FBA19PL9TWJH → LBE1/);
  assert.match(elements.restoreResult.innerHTML, /11 cartons, 122 packed units and 122 carton scans would be carried over/);
  assert.match(elements.restoreResult.innerHTML, /Restore this shipment/);

  // The typed confirmation is required before anything is sent.
  elements.restoreListText.value = 'yes';
  await context.restoreFinishedShipment(8, 11);
  assert.match(elements.restoreResult.innerHTML, /Type RESTORE/);
  assert.equal(calls.length, 4, 'nothing is posted without the typed confirmation');

  // A cancelled dialog stops the restore too.
  elements.restoreListText.value = 'restore';
  confirmed = false;
  await context.restoreFinishedShipment(8, 11);
  assert.equal(calls.length, 4, 'nothing is posted when the dialog is dismissed');

  // Confirmed: one POST, then the reopened shipment is loaded.
  confirmed = true;
  nextResponse = ok({amazon_workflow: {stage: 'plan_created'}, items: []});
  await context.restoreFinishedShipment(8, 11);
  assert.equal(calls[4].method, 'POST');
  assert.match(calls[4].url, /\/api\/fba-prep\/sessions\/8\/amazon-restore$/);
  assert.deepEqual(calls[4].body, {confirm: true});
  assert.deepEqual(loaded, [8]);
  assert.match(elements.restoreResult.className, /ok/);

  // A server refusal is shown verbatim and nothing is opened.
  nextResponse = fail('Amazon still reports this plan as usable (active shipments: FBA-B).');
  await context.restoreFinishedShipment(8, 11);
  assert.match(elements.restoreResult.innerHTML, /active shipments: FBA-B/);
  assert.match(elements.restoreResult.className, /error/);
  assert.deepEqual(loaded, [8], 'a refused restore never opens the session');

  console.log('FBA restore panel: listing, live/partial refusal, typed confirmation, and reopen passed.');
})().catch(error => { console.error(error); process.exitCode = 1; });
