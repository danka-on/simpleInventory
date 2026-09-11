// The destination-split cards: what an irreversible choice has to show before it is made.
const assert = require('node:assert/strict'), fs = require('node:fs'), vm = require('node:vm');
const html = fs.readFileSync('templates/fba_prep.html', 'utf8');
function block(a, b) { const start = html.indexOf(a); const end = html.indexOf(b, start); assert.ok(start >= 0 && end > start, `Missing source: ${a}`); return html.slice(start, end); }

const shipment = (id, warehouseId, city, state, units, skus, miles) => ({
  shipmentId: id, units, sku_count: skus, distance_miles: miles,
  source: {city: 'Fort Myers', stateOrProvinceCode: 'FL'},
  destination: {warehouseId, city, stateOrProvinceCode: state},
});
const option = (id, shipmentIds, amount, expiration) => ({
  placementOptionId: id, status: 'OFFERED', shipmentIds,
  fees: [{description: 'Placement service fee represents service to inbound with minimal shipment splits', value: {amount, code: 'USD'}}],
  fee_totals: [{currency: 'USD', amount}], discounts: [], expiration,
});

// A day out, so the expiry line is deterministic without freezing the clock.
const soon = new Date(Date.now() + 36 * 36e5).toISOString();
const state = {amazon: {
  shipments: [
    shipment('s1', 'SCK8', 'Oakley', 'CA', 68, 5, 2443),
    shipment('s2', 'HGR6', 'Hagerstown', 'MD', 54, 3, 933),
    shipment('s3', 'POC3', 'Jurupa Valley', 'CA', 61, 4, 2167),
    shipment('s4', 'ABQ2', 'Los Lunas', 'NM', 61, 4, 1577),
  ],
  placement_options: [
    option('pl27d5badc', ['s1', 's2'], 159.32, soon),
    option('plb9e4e863', ['s3', 's1'], 181.88, soon),
  ],
  boxes: [{contents: [{msku: 'SKU-1', quantity: 122}]}],
}};

const context = vm.createContext({
  state, esc: value => String(value ?? ''),
  packedUnits: () => 122,
  planItems: () => [],
  feeText: rows => rows.map(row => `$${Number(row.amount).toFixed(2)}`).join(' + '),
});
vm.runInContext(block('    function placementFeeAmount(', '    function transportationHtml('), context);

const rendered = context.placementOptionHtml();

// Every destination is named, placed, sized and distanced.
assert.match(rendered, /SCK8/);
assert.match(rendered, /Oakley, CA/);
assert.match(rendered, /Hagerstown, MD/);
assert.match(rendered, /68 units · 5 SKUs · ≈2,443 mi away/);
assert.match(rendered, /54 units · 3 SKUs · ≈933 mi away/);

// The fee is broken down and the cheapest option is called out.
assert.match(rendered, /\$159\.32/);
assert.match(rendered, /\$1\.31 per unit/);
assert.match(rendered, /Lowest fee/);
assert.match(rendered, /\$22\.56 more than the cheapest/);
assert.equal((rendered.match(/Lowest fee/g) || []).length, 1, 'exactly one cheapest option');

// The carrier question is answered up front rather than left as a blank card.
assert.match(rendered, /Amazon only quotes carriers for a <em>confirmed<\/em> split/);
assert.match(rendered, /straight-line estimates from Fort Myers, FL/);
assert.match(rendered, /Placement service fee represents service to inbound/);
assert.match(rendered, /Offer expires/);
assert.match(rendered, /about (36 hours|2 days) left/);

// Confirmation stays behind the typed guard, once per option.
assert.equal((rendered.match(/Type CONFIRM SPLIT/g) || []).length, 2);
assert.match(rendered, /data-confirm-placement="pl27d5badc"/);

// A shipment Amazon has not detailed yet degrades to the bare facts, never to a guess.
state.amazon.shipments = [{shipmentId: 's1', destination: {warehouseId: 'SCK8'}}];
state.amazon.placement_options = [option('plbare', ['s1'], 99, '')];
const bare = context.placementOptionHtml();
assert.match(bare, /SCK8/);
assert.doesNotMatch(bare, /units ·/);
assert.doesNotMatch(bare, /mi away/);
assert.doesNotMatch(bare, /Offer expires/);

// No options at all still offers the generate step.
state.amazon.placement_options = [];
assert.match(context.placementOptionHtml(), /Generate destination options/);

console.log('FBA placement detail: destinations, distances, unit splits, fee breakdown, and safe degradation passed.');
