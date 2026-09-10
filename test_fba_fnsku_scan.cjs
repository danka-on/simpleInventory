// A printed FNSKU label covers the item's own barcode, so the packing scanner
// has to resolve that label to the same planned row the UPC resolves to.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

const html = fs.readFileSync('templates/fba_prep.html', 'utf8');
function block(start, end) {
  const a = html.indexOf(start), b = html.indexOf(end, a);
  assert.ok(a >= 0 && b > a, start);
  return html.slice(a, b);
}

const upcRow = {uid: 1, barcode: '025398232475', seller_sku: 'SKU-1', title: 'Glass set', quantity: 3};
const planOnlyRow = {uid: 2, barcode: '', seller_sku: 'SKU-2', title: 'Mug', quantity: 1};
const state = {
  queue: [upcRow, planOnlyRow],
  rejectedItems: [],
  amazon: {plan_items: [
    {msku: 'SKU-1', fnsku: 'X001234567', quantity: 3, asin: 'B00TESTASN'},
    {msku: 'SKU-2', fnsku: 'X009876543', quantity: 1},
  ]},
};
const context = vm.createContext({state, String, Number, Boolean, Object, Set, Map, Array});
vm.runInContext(block('    const barcodeKey=', '    function planApprovalForRow'), context);
vm.runInContext(block('    function planItems()', '    const amazonPrepTypeInfo'), context);

// The original UPC still routes to its row.
assert.equal(context.plannedRowForScan('025398232475'), upcRow);
assert.equal(context.plannedRowForScan('0025398232475'), upcRow);

// The FNSKU on the printed Amazon label routes to the same row, in any case.
assert.equal(context.plannedRowForScan('X001234567'), upcRow);
assert.equal(context.plannedRowForScan('x001234567'), upcRow);
assert.equal(context.plannedRowForScan('  X001234567  '), upcRow);

// Seller SKU and ASIN keep working as manual fallbacks.
assert.equal(context.plannedRowForScan('SKU-1'), upcRow);
assert.equal(context.plannedRowForScan('B00TESTASN'), upcRow);

// A row that never saved a warehouse barcode is still reachable by its label.
assert.equal(context.plannedRowForScan('X009876543'), planOnlyRow);

// An unrelated code still matches nothing.
assert.equal(context.plannedRowForScan('999999999999'), undefined);
assert.equal(context.plannedRowForScan('X000000000'), undefined);

console.log('fba fnsku scan tests passed');
