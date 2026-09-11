const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

const html = fs.readFileSync('templates/fba_prep.html', 'utf8');
function block(startMarker, endMarker) {
  const start = html.indexOf(startMarker);
  const end = html.indexOf(endMarker, start);
  assert.ok(start >= 0 && end > start, `Missing transport source: ${startMarker}`);
  return html.slice(start, end);
}
const state = {amazon: {
  selected_placement_option_id: 'split',
  placement_options: [{placementOptionId: 'split', status: 'ACCEPTED', shipmentIds: ['A', 'B']}],
  shipments: [{shipmentId: 'A', destination: {warehouseId: 'FC-A'}},
              {shipmentId: 'B', destination: {warehouseId: 'FC-B'}}],
  transportation_options: [],
}};
const context = vm.createContext({state, esc: value => String(value ?? ''),
  localDate: () => '2026-09-07',
  quoteText: quote => quote?.cost?.amount !== undefined ? `$${quote.cost.amount}` : 'Quote unavailable',
});
vm.runInContext(block('    function purchasableTransportationOptions(', '    function workflowDestinations('), context);
vm.runInContext(block('    function transportationHtml(', '    function finishedAmazonHtml('), context);

function parcel(shipmentId) {
  return {shipmentId, transportationOptionId: `parcel-${shipmentId}`, shippingMode: 'GROUND_SMALL_PARCEL',
          shippingSolution: 'AMAZON_PARTNERED_CARRIER', can_purchase: true, preconditions: [],
          quote: {cost: {amount: 12.34}}, carrier: {name: 'Parcel carrier'}};
}
state.amazon.transportation_options = [parcel('A')];
let rendered = context.transportationHtml();
assert.match(rendered, /quote for 1 destination/);
assert.match(rendered, /FC-B/);
assert.match(rendered, /data-confirm-transport[^>]*disabled/);

state.amazon.transportation_options.push(parcel('B'));
rendered = context.transportationHtml();
assert.doesNotMatch(rendered, /data-confirm-transport[^>]*disabled/);
assert.match(rendered, /value="parcel-A"/);
assert.match(rendered, /value="parcel-B"/);

state.amazon.transportation_options.push({...parcel('A'), transportationOptionId: 'freight', shippingMode: 'FREIGHT_LTL'});
state.amazon.transportation_options.push({...parcel('A'), transportationOptionId: 'self-booked', can_purchase: false,
                                        shippingSolution: 'USE_YOUR_OWN_CARRIER'});
rendered = context.transportationHtml();
assert.equal(context.purchasableTransportationOptions().length, 2);
assert.doesNotMatch(rendered, /value="freight"|value="self-booked"/);
assert.match(rendered, /data-generate-transport/);

// Every option Amazon returned stays visible with its price and the reason it cannot be bought.
assert.match(rendered, /2 other options Amazon returned for this destination/);
assert.match(rendered, /Amazon partnered freight ltl is arranged in Seller Central/);
assert.match(rendered, /Book this yourself with the carrier/);
assert.equal((rendered.match(/\$12\.34/g) || []).length, 4, 'each option shows a price');

// A carrier Amazon priced but blocked on a precondition still shows both.
state.amazon.transportation_options.push({...parcel('B'), transportationOptionId: 'needs-pallet',
                                        can_purchase: false, preconditions: ['PALLET_INFORMATION_REQUIRED'],
                                        quote: {cost: {amount: 88.5}}});
rendered = context.transportationHtml();
assert.match(rendered, /Amazon requires first: PALLET_INFORMATION_REQUIRED/);
assert.match(rendered, /\$88\.5/);
assert.doesNotMatch(rendered, /value="needs-pallet"/);

// A server-supplied reason always wins over the browser fallback.
state.amazon.transportation_options.push({...parcel('B'), transportationOptionId: 'server-reason',
                                        can_purchase: false, block_reason: 'Amazon said no.'});
assert.match(context.transportationHtml(), /Amazon said no\./);

// A plan cancelled on Amazon offers a rebuild that keeps the packed cartons.
context.packedUnits = () => 122;
vm.runInContext(block('    function cancelledPlanHtml(', '    function finishedAmazonHtml('), context);
state.amazon.boxes = [{local_id: 'BOX-01'}, {local_id: 'BOX-02'}];
state.amazon.amazon_cancelled = {plan_status: 'VOIDED', restorable: true, plan_cancelled: true,
  cancelled_shipments: [{shipmentId: 'A', shipmentConfirmationId: 'FBA19PL7W2P5', status: 'CANCELLED', warehouseId: 'SCK8'}],
  live_shipments: [], destinations: ['SCK8']};
let cancelled = context.cancelledPlanHtml();
assert.match(cancelled, /FBA19PL7W2P5/);
assert.match(cancelled, /VOIDED/);
assert.match(cancelled, /2 cartons and 122 packed units are kept exactly as scanned/);
assert.match(cancelled, /data-restore-plan/);
assert.match(cancelled, /id="restorePlanText"/);

// A half-cancelled plan is reported but never offers the rebuild.
state.amazon.amazon_cancelled = {plan_status: 'ACTIVE', restorable: false, partial: true,
  cancelled_shipments: [{shipmentId: 'A', shipmentConfirmationId: 'FBA-A', status: 'CANCELLED', warehouseId: 'SCK8'}],
  live_shipments: [{shipmentId: 'B', shipmentConfirmationId: 'FBA-B', status: 'IN_TRANSIT'}], destinations: []};
cancelled = context.cancelledPlanHtml();
assert.doesNotMatch(cancelled, /data-restore-plan/);
assert.match(cancelled, /FBA-B \(IN_TRANSIT\)/);
assert.match(cancelled, /Cancel the remaining shipment in Seller Central/);

console.log('FBA transport: quote gating, full option visibility with prices, and cancelled-plan restore passed.');
