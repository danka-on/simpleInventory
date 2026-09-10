// Units labelled in "Print label only" mode must not get a second label when
// they are later packed; units with no label left over must still auto-print.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

const html = fs.readFileSync('templates/fba_prep.html', 'utf8');
function block(start, end) {
  const a = html.indexOf(start), b = html.indexOf(end, a);
  assert.ok(a >= 0 && b > a, start);
  return html.slice(a, b);
}

function run({labelsPrinted, packedBefore}) {
  const prints = [];
  const messages = [];
  let saved = false;
  const row = {uid: 1, barcode: '025398232475', seller_sku: 'SKU-1', title: 'Glass set', quantity: 3};
  const state = {
    sessionId: 1, clientId: 'client-000000000001', busy: false, activeBoxId: 'BOX-01',
    autoPrintEnabled: true, workingLocations: [{code: 'A1B1', active: true}],
    amazon: {
      packing_groups: [{packing_group_id: 'pg-1', label: 'Group 1', items: [{msku: 'SKU-1'}]}],
      printed_item_label_counts: {'SKU-1': labelsPrinted},
      boxes: [{local_id: 'BOX-01', packing_group_id: 'pg-1', contents: [{msku: 'SKU-1', quantity: packedBefore + 1}]}],
    },
  };
  const input = {value: '025398232475', classList: {remove() {}, toggle() {}}, disabled: false};
  const context = vm.createContext({
    state, $: () => input, Object, String, Number, Boolean, Error, Map, Set, Array, console,
    plannedRowForScan: () => row, scanRejection: () => null, showScanRejection: () => false,
    rowNeedsAmazonLabel: () => true, printedAmazonLabelCount: () => labelsPrinted,
    expectedByMsku: () => new Map([['sku-1', 3]]),
    packedByMsku: () => new Map([['sku-1', packedBefore + (saved ? 1 : 0)]]),
    activeBoxForGroup: () => 'BOX-01', setActiveBoxId: () => {},
    emptyAmazonWorkflow: () => ({}), newToken: () => 'unit-token-0001',
    printAmazonItemLabel: async () => { prints.push('print'); return {printed: true}; },
    prepVoiceForRow: () => '', specialPrepTypesForRow: () => [], amazonPrepInfo: () => ({label: ''}),
    findPackedRow: () => true, spokenBoxNumber: () => '1', touch: () => {},
    renderQueue: () => {}, renderAmazonWorkflow: () => {}, focusBarcodeInput: () => {},
    scheduleSharedSync: () => {}, operatorName: () => 'Tester',
    setStatus: (kind, message) => messages.push(kind + ': ' + message),
    setRouteCallout: (message, kind) => { if (message) messages.push('callout ' + kind + ': ' + message); },
    speakRoute: voice => messages.push('voice: ' + voice),
    fetch: async () => ({
      ok: (saved = true),
      json: async () => ({success: true, scan: {box_id: 'BOX-01', inventory_removed: 1, source_location: 'A1B1'}, amazon_workflow: state.amazon}),
    }),
  });
  vm.runInContext(block('    async function packScannedUnit(code){', '    function sessionSnapshot()'), context);
  return context.packScannedUnit('025398232475').then(() => ({prints, messages: messages.join(' | ')}));
}

(async () => {
  // Three labels already printed in label-only mode; the first pack prints nothing.
  let result = await run({labelsPrinted: 3, packedBefore: 0});
  assert.deepEqual(result.prints, [], 'a pre-labelled unit must not print again');
  assert.match(result.messages, /Label already printed/);
  assert.match(result.messages, /Already labelled/);
  assert.doesNotMatch(result.messages, /LABEL PRINT FAILED|Label still needed/);
  assert.match(result.messages, /callout ok:/, 'a pre-labelled unit is not a warning');

  // The third unit packed when only two labels exist still prints its own.
  result = await run({labelsPrinted: 2, packedBefore: 2});
  assert.deepEqual(result.prints, ['print'], 'an unlabelled unit must still auto-print');
  assert.match(result.messages, /Label printed/);

  // Plain packing with no pre-printing is unchanged.
  result = await run({labelsPrinted: 0, packedBefore: 0});
  assert.deepEqual(result.prints, ['print']);

  console.log('pre-labelled pack tests passed');
})();
