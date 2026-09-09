const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

const html = fs.readFileSync('templates/fba_prep.html', 'utf8');
function block(start, end) {
  const a = html.indexOf(start), b = html.indexOf(end, a);
  assert.ok(a >= 0 && b > a);
  return html.slice(a, b);
}
const events = [];
const state = {queue: [], rejectedItems: [], workflowView: 2, busy: false};
let packing = true, hasPlan = true;
const input = {value: '025398232475'};
const context = vm.createContext({
  state, $: () => input,
  barcodeKey: value => String(value || '').replace(/^0+/, '').toLowerCase(),
  planItemForMsku: () => null,
  fbaEnablementStatus: row => row?.fba_enablement_status,
  amazonPackingMode: () => packing, amazonPlanCreated: () => hasPlan,
  setScanSortResult: result => events.push(result.headline),
  setStatus: () => {}, errorBeep: () => events.push('beep'),
  speakRoute: voice => events.push(voice), focusBarcodeInput: () => {},
  packScannedUnit: async () => events.push('pack'),
  moveScannedUnit: async () => events.push('move'),
  countScannedUnit: async () => events.push('count'),
  batteryRequired: () => false,
  renderAmazonWorkflow: () => {}, renderQueue: () => {}, touch: () => {},
  saveSession: async () => events.push('save:' + state.queue.length),
  selectWorkflowStep: index => events.push('step:' + index),
});
vm.runInContext(block('    function plannedRowForScan', '    function focusBarcodeInput'), context);
vm.runInContext(block('    async function scanItem()', '    function newToken'), context);
vm.runInContext(block('    function fbaEnablementSummary()', '    function fbaEnablementBadge'), context);
vm.runInContext(block('    function readyPlanRows()', '    function setAsideItemsHtml'), context);

async function scan(row) {
  state.queue = row ? [row] : [];
  events.length = 0;
  await context.scanItem();
}
(async () => {
  const reject = {barcode: input.value, seller_sku: 'BLOCKED', fba_enablement_status: 'failed',
    fba_enablement_error: 'Brand approval required'};
  await scan(reject);
  assert.deepEqual(events, ['REJECTED — DO NOT PACK', 'beep', 'Rejected. Do not pack. Set this item aside.']);
  state.boxMoveMode = true;
  await scan(reject);
  assert.ok(!events.includes('move'));
  state.boxMoveMode = false;
  state.rejectedItems = [reject];
  await scan(null);
  assert.ok(events.includes('REJECTED — DO NOT PACK'));
  await scan({...reject, fba_enablement_status: 'ready'});
  assert.deepEqual(events, ['pack']);
  await scan({...reject, fba_enablement_error: 'Amazon request timed out'});
  assert.deepEqual(events, ['CHECK NEEDED — DO NOT PACK', 'beep', 'Check needed. Set this item aside.']);
  state.amazon={operation:{kind:'create_plan',status:'FAILED',problems:[{code:'FBA_INB_0021',details:"There's an input error with the resource 'BLOCKED'.",message:'Approval required by Amazon'}]}};
  await scan({...reject,fba_enablement_status:'ready',fba_enablement_error:''});
  assert.ok(events.includes('REJECTED — DO NOT PACK'));
  assert.equal(context.planApprovalForRow({seller_sku:'BLOCKED'}),'Approval required by Amazon');
  state.amazon={};
  // Step one still counts catalog matches regardless of later setup rejection.
  packing = false; hasPlan = false; state.workflowView = 0;
  await scan(reject);
  assert.deepEqual(events, ['count']);
  const ready = {barcode: '111111111111', seller_sku: 'READY', quantity: 2, fba_enablement_status: 'ready'};
  const pending = {barcode: '333333333333', seller_sku: 'PENDING', quantity: 1, fba_enablement_status: 'needs_enablement'};
  const missing = {barcode: '444444444444', seller_sku: '', quantity: 1};
  state.queue = [ready, reject, pending, missing]; state.rejectedItems = [];
  events.length = 0;
  await context.continueWithReadyItems();
  assert.equal(state.queue.length, 1);
  assert.equal(state.queue[0].seller_sku, 'READY');
  assert.equal(state.queue[0].quantity, 2);
  assert.equal(state.rejectedItems.length, 3);
  assert.deepEqual(events, ['save:4', 'save:1', 'step:1']);
  assert.equal(context.scanRejection(state.rejectedItems.find(row => row.seller_sku === 'PENDING')).rejected, false);
  assert.equal(context.scanRejection(state.rejectedItems.find(row => row.seller_sku === 'BLOCKED')).rejected, true);
  // A shared seller SKU with a blocked row must not slip into the ready subset.
  state.queue = [ready, {...reject, seller_sku: 'READY'}];
  assert.equal(context.readyPlanRows().length, 0);
  console.log('FBA rejection display, voice, archived matches, and scan routing passed.');
})().catch(error => { console.error(error); process.exitCode = 1; });
