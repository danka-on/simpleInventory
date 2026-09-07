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

console.log('FBA transport: all destinations require quotes; parcel filtering and purchase gating passed.');
