const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

const html = fs.readFileSync('templates/fba_prep.html', 'utf8');
function block(start, end) {
  const a = html.indexOf(start), b = html.indexOf(end, a);
  assert.ok(a >= 0 && b > a, start);
  return html.slice(a, b);
}
const events = [];
const state = {queue: [], rejectedItems: [], workflowView: 2, busy: false, boxMoveMode: false, labelOnlyMode: false, amazon: {}};
const input = {value: '025398232475', classList: {remove() {}, toggle() {}}, disabled: false};
let rejection = null, needsLabel = true;
const row = {uid: 1, barcode: '025398232475', seller_sku: 'SKU-1', title: 'Glass set', quantity: 3, fnsku: 'X00LABEL01'};
const context = vm.createContext({
  state, $: () => input, Object, String, Number, Boolean, Error,
  amazonPackingMode: () => true, amazonPlanCreated: () => true,
  plannedRowForScan: () => row, scanRejection: () => rejection,
  showScanRejection: result => { if (result && result.rejected !== undefined) { events.push('rejected'); return true; } return false; },
  rowNeedsAmazonLabel: () => needsLabel, barcodeGuidanceFor: () => ({detail: 'Use regular item UPC'}),
  printAmazonItemLabel: async () => { events.push('print'); return {printed: true, printer: 'Item Prep'}; },
  printedAmazonLabelCount: () => 1, expectedByMsku: () => new Map([['sku-1', 3]]),
  prepVoiceForRow: () => '', planItemForMsku: () => ({fnsku: 'X00LABEL01'}),
  setStatus: (type, msg) => events.push('status:' + type + ':' + msg),
  setRouteCallout: (msg, type) => { if (msg) events.push('callout:' + type + ':' + msg); },
  speakRoute: voice => events.push('voice:' + voice),
  renderQueue: () => {}, renderAmazonWorkflow: () => {}, focusBarcodeInput: () => {},
  packScannedUnit: async () => events.push('pack'),
  moveScannedUnit: async () => events.push('move'),
  countScannedUnit: async () => events.push('count'),
});
vm.runInContext(block('    async function scanItem()', '    function newToken'), context);
vm.runInContext(block('    async function printLabelOnlyForScan(code){', '    async function packScannedUnit(code){'), context);

(async () => {
  // Default packing mode still packs.
  events.length = 0; state.labelOnlyMode = false;
  await context.scanItem();
  assert.deepEqual(events, ['pack']);

  // Label-only mode prints without packing.
  events.length = 0; state.labelOnlyMode = true; input.value = '025398232475';
  await context.scanItem();
  assert.ok(events.includes('print'), events.join('|'));
  assert.ok(!events.includes('pack'), events.join('|'));
  assert.ok(events.some(e => e.startsWith('callout:ok:LABEL ONLY · Glass set · FNSKU X00LABEL01 · label 1 of 3 printed · NOT packed')), events.join('|'));
  assert.ok(events.some(e => e.startsWith('voice:Label printed.') && e.endsWith('Not packed.')), events.join('|'));
  assert.equal(input.value, '');
  assert.equal(state.busy, false);

  // Box-move mode wins over label-only.
  events.length = 0; state.boxMoveMode = true; input.value = '025398232475';
  await context.scanItem();
  assert.deepEqual(events, ['move']);
  state.boxMoveMode = false;

  // Items that ship with the manufacturer barcode do not print.
  events.length = 0; needsLabel = false; input.value = '025398232475';
  await context.scanItem();
  assert.ok(!events.includes('print'), events.join('|'));
  assert.ok(events.some(e => e.startsWith('callout:warn:') && e.includes('no FNSKU label is needed')), events.join('|'));
  needsLabel = true;

  // Rejected items never print in label-only mode.
  events.length = 0; rejection = {rejected: true, error: 'Rejected. Do not pack.'}; input.value = '025398232475';
  await context.scanItem();
  assert.ok(events.includes('rejected'), events.join('|'));
  assert.ok(!events.includes('print'), events.join('|'));
  rejection = null;

  console.log('Print-label-only scan mode: dispatch, printing, no packing, move precedence, no-label and rejection guards passed.');
})().catch(error => { console.error(error); process.exit(1); });
