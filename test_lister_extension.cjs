// Sweet Shelves Lister extension: field matching, page detection and manifest sanity (no browser).
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const root = fs.existsSync(path.join(__dirname, 'lister-extension')) ? path.join(__dirname, 'lister-extension')
  : path.join(__dirname, '..', 'lister-extension');
const M = require(path.join(root, 'matcher.js'));

function field(overrides) {
  return { tag: 'input', type: 'text', name: '', id: '', ariaLabel: '', placeholder: '', labelText: '', nearbyText: '', role: '', maxLength: 0, contenteditable: false, value: '', ...overrides };
}

// --- eBay-like listing form -----------------------------------------------------------------
const ebayForm = [
  field({ id: 'title', labelText: 'Title', maxLength: 80 }),
  field({ id: 'subtitle', labelText: 'Subtitle', maxLength: 55 }),
  field({ name: 'price', labelText: 'Buy It Now price', type: 'text' }),
  field({ name: 'shippingCost', labelText: 'Shipping cost' }),
  field({ name: 'quantity', labelText: 'Quantity', type: 'number' }),
  field({ name: 'customLabel', labelText: 'Custom label (SKU)' }),
  field({ ariaLabel: 'UPC', placeholder: 'Enter the UPC' }),
  field({ tag: 'textarea', name: 'conditionDescription', labelText: 'Condition description' }),
  field({ tag: 'div', contenteditable: true, ariaLabel: 'Item description', nearbyText: 'Description' }),
  field({ tag: 'select', labelText: 'Condition', options: ['Select', 'New', 'New (Other)', 'Used'] }),
  field({ tag: 'input', role: 'combobox', labelText: 'Brand' }),
  field({ tag: 'input', role: 'combobox', labelText: 'Material' }),
  field({ type: 'search', placeholder: 'Search eBay', ariaLabel: 'Search for anything' }),
];
const assigned = M.assign(ebayForm);
assert.equal(assigned.title.index, 0, 'title goes to the 80-char Title field, not Subtitle');
assert.equal(assigned.price.index, 2, 'price avoids shipping cost');
assert.equal(assigned.quantity.index, 4);
assert.equal(assigned.sku.index, 5, 'custom label is the eBay SKU');
assert.equal(assigned.upc.index, 6);
assert.equal(assigned.conditionDescription.index, 7);
assert.equal(assigned.description.index, 8, 'contenteditable editor wins the description');
assert.equal(assigned.brand.index, 10);
assert.equal(assigned.asin, undefined, 'no ASIN field on eBay');
assert.ok(!Object.values(assigned).some(hit => hit.index === 12), 'the site search box is never filled');

const aspects = M.matchAspects(ebayForm, { Brand: ['Lenox'], Material: ['Porcelain'], Color: ['White'] });
assert.equal(aspects.Brand.index, 10);
assert.equal(aspects.Material.index, 11);
assert.equal(aspects.Color, undefined);

// --- Seller Central-like offer form -----------------------------------------------------------
const amazonForm = [
  field({ name: 'contribution_sku#1.value', labelText: 'Seller SKU', ariaLabel: 'Seller SKU' }),
  field({ name: 'purchasable_offer#1.our_price#1.schedule#1.value_with_tax', labelText: 'Your price' }),
  field({ name: 'purchasable_offer#1.minimum_seller_allowed_price', labelText: 'Minimum price' }),
  field({ name: 'fulfillment_availability#1.quantity', labelText: 'Quantity', type: 'number' }),
  field({ tag: 'select', name: 'condition_type#1.value', labelText: 'Condition', options: ['Select', 'New', 'Used - Like New', 'Used - Good'] }),
  field({ tag: 'textarea', name: 'condition_note#1.value', labelText: 'Condition Note' }),
  field({ type: 'search', id: 'search-term', placeholder: 'Product name, UPC, EAN, ISBN, or ASIN' }),
];
const amazon = M.assign(amazonForm);
assert.equal(amazon.sku.index, 0);
assert.equal(amazon.price.index, 1, 'your price, not minimum price');
assert.equal(amazon.quantity.index, 3);
assert.equal(amazon.conditionDescription.index, 5);
assert.equal(amazon.title, undefined, 'the product search box must not receive the title');

// A field the user taught takes priority over the heuristics.
const taught = M.signature(field({ name: 'weird-field-name', labelText: 'Enter the product name here' }));
assert.ok(M.matchesSignature(field({ name: 'weird-field-name', labelText: 'Enter the product name here' }), taught));
assert.ok(!M.matchesSignature(field({ name: 'other', labelText: 'Enter the product name here' }), taught));
const byLabel = M.signature(field({ labelText: 'Enter the product name here' }));
assert.ok(M.matchesSignature(field({ labelText: 'ENTER the product NAME here', tag: 'input' }), byLabel), 'label-only signatures match case-insensitively');

// Suggestions for a picked field rank the right value first.
assert.equal(M.suggestTargets(field({ labelText: 'Seller SKU' }))[0].target, 'sku');
assert.equal(M.suggestTargets(field({ labelText: 'Product description', tag: 'textarea' }))[0].target, 'description');
assert.deepEqual(M.suggestTargets(field({ type: 'checkbox', labelText: 'Price' })), [], 'checkboxes are never suggested');

// --- page detection --------------------------------------------------------------------------
assert.deepEqual(M.detectPage('https://www.ebay.com/sl/prelist/suggest?sr=wn'), { store: 'ebay', kind: 'listing-start', listingId: '', asin: '', sku: '' });
assert.equal(M.detectPage('https://www.ebay.com/sl/sell').kind, 'listing-start');
assert.equal(M.detectPage('https://www.ebay.com/sl/prelist/identify?upc=883049370897').kind, 'listing-match', 'the catalog match step is its own kind');
assert.equal(M.detectPage('https://www.ebay.com/sl/list?mode=AddItem&draftId=5').kind, 'listing-form');
assert.equal(M.detectPage('https://bulksell.ebay.com/ws/eBayISAPI.dll?SingleList').kind, 'listing-form');
const success = M.detectPage('https://www.ebay.com/sl/list/success?itemId=335566778899&mode=AddItem');
assert.equal(success.kind, 'listing-success');
assert.equal(success.listingId, '335566778899');
const live = M.detectPage('https://www.ebay.com/itm/Lenox-Plate/335566778899?hash=abc');
assert.equal(live.kind, 'listing-live');
assert.equal(live.listingId, '335566778899');
assert.equal(M.detectPage('https://www.ebay.com/itm/335566778899').listingId, '335566778899');
assert.equal(M.detectPage('https://www.ebay.com/sh/lst/active').kind, 'seller-hub');
const offer = M.detectPage('https://sellercentral.amazon.com/abis/listing/syh/ref=xx?asin=B0TESTASIN&sku=SS-1');
assert.equal(offer.store, 'amazon');
assert.equal(offer.kind, 'offer-form');
assert.equal(offer.asin, 'B0TESTASIN');
assert.equal(offer.sku, 'SS-1');
assert.equal(M.detectPage('https://sellercentral.amazon.com/product-search/search?q=883049370897').kind, 'listing-start');
assert.equal(M.detectPage('https://sellercentral.amazon.com/inventory').kind, 'inventory');
assert.equal(M.detectPage('https://www.amazon.com/dp/B0TESTASIN').store, '', 'the retail site is not a listing page');
assert.equal(M.detectPage('not a url').store, '');

// eBay's prelist steps: the other seller's item id in the URL is never our listing (real URL from 2026-09-16).
const sellLike = M.detectPage('https://www.ebay.com/sl/prelist/identify?sr=sug&title=761323062839&isUid=false&sssr=shListingsCTA&caty=177008&mode=SellLikeItem&itemId=168611515264&view=sellnode-condition');
assert.equal(sellLike.kind, 'listing-confirm');
assert.equal(sellLike.listingId, '', 'another seller\'s item is not our item number');
assert.equal(sellLike.matchId, '168611515264');
assert.equal(M.detectPage('https://www.ebay.com/sl/prelist/identify?sr=sug&title=761323062839').kind, 'listing-match');
assert.equal(M.detectPage('https://www.ebay.com/sl/list?mode=AddItem&itemId=168611515264&mode=SellLikeItem').listingId, '', 'SellLikeItem never yields our id');
assert.equal(M.detectPage('https://www.ebay.com/sl/list?mode=AddItem&draftId=5').listingId, '');
// Both radio sets eBay shows on "Confirm details": New/Open box/Used/For parts, and Brand New/Like New/Very Good/Good/Acceptable.
const radioPick = (condition, offered) => M.prelistCondition(condition).find(label => offered.includes(label));
const setA = ['New', 'Open box', 'Used', 'For parts or not working'];
const setB = ['Brand New', 'Like New', 'Very Good', 'Good', 'Acceptable'];
assert.equal(radioPick('USED_GOOD', setA), 'Used');
assert.equal(radioPick('USED_GOOD', setB), 'Good');
assert.equal(radioPick('USED_EXCELLENT', setB), 'Like New');
assert.equal(radioPick('USED_ACCEPTABLE', setB), 'Acceptable');
assert.equal(radioPick('NEW_OTHER', setA), 'Open box');
assert.equal(radioPick('NEW_OTHER', setB), 'Like New');
assert.equal(radioPick('NEW', setA), 'New');
assert.equal(radioPick('NEW', setB), 'Brand New');
assert.equal(radioPick('FOR_PARTS_OR_NOT_WORKING', setA), 'For parts or not working');
assert.equal(radioPick('FOR_PARTS_OR_NOT_WORKING', setB), 'Acceptable');
const ranked = M.rankCandidates([
  'Salt and Pepper Shakers Table Decoration Meal Condiment Container Hug Design',
  'NEW Nambe Hug Salt & Pepper Shakers | 2-Piece Set',
  'Washer & Dryer Parts',
], 'Nambe Hug Salt and Pepper Shaker Set', 'Nambe');
assert.equal(ranked[0].index, 1, 'the Nambe catalog listing wins');
assert.ok(ranked[0].score > ranked[1].score && ranked[2].score === 0);
assert.equal(M.categoryScore('Home & Garden > Kitchen, Dining & Bar > Kitchen Tools & Gadgets > Salt & Pepper', 'Home & Garden > Kitchen, Dining & Bar > Kitchen Tools & Gadgets > Salt & Pepper'), 1);
assert.ok(M.categoryScore('Collectibles > Kitchen & Home > Kitchen Tools & Gadgets > Salt & Pepper Shakers', 'Home & Garden > Kitchen, Dining & Bar > Kitchen Tools & Gadgets > Salt & Pepper') < 0.5);
assert.ok(M.categoryScore('Home & Garden > Major Appliances > Washer & Dryer Parts', 'Home & Garden > Kitchen, Dining & Bar > Salt & Pepper') < 0.3);

// The condition description is matched by its own label only, never by a nearby "Condition" heading,
// and eBay's editor for it may be a contenteditable box.
assert.equal(M.scoreTarget('conditionDescription', field({ tag: 'div', contenteditable: true, nearbyText: 'Condition description' })), 0, 'nearby text alone never makes a condition description');
assert.ok(M.scoreTarget('conditionDescription', field({ tag: 'div', contenteditable: true, ariaLabel: 'Condition description' })) > 0, 'a labelled contenteditable does');
const withEditor = M.assign([
  field({ tag: 'div', contenteditable: true, ariaLabel: 'Item description', nearbyText: 'Condition' }),
  field({ tag: 'textarea', ariaLabel: 'Condition description' }),
]);
assert.equal(withEditor.description.index, 0);
assert.equal(withEditor.conditionDescription.index, 1, 'the condition description row points at the condition field, not the item description editor');

// --- success wording, required fields, the product search box ---------------------------------
assert.ok(M.successInfo('ebay', 'Congratulations! Your item is listed. View listing').success);
assert.ok(!M.successInfo('ebay', 'Create your listing. Title. Price.').success);
assert.ok(M.successInfo('amazon', 'Your listing has been saved. It may take up to 15 minutes for your changes to appear.').success);
assert.ok(!M.successInfo('amazon', 'Offer details. Seller SKU. Your price.').success);
assert.ok(!M.successInfo('', 'Congratulations').success);
assert.ok(M.isRequired(field({ labelText: 'Title *' })));
assert.ok(M.isRequired(field({ required: true })));
assert.ok(M.isRequired(field({ ariaRequired: true })));
assert.ok(!M.isRequired(field({ labelText: 'Subtitle' })));
assert.ok(M.isEmptyValue(field({ value: '' })));
assert.ok(M.isEmptyValue(field({ tag: 'select', value: 'Select' })));
assert.ok(!M.isEmptyValue(field({ tag: 'select', value: 'New' })));
assert.ok(!M.isEmptyValue(field({ value: '24.5' })));
const prelist = [
  field({ type: 'search', id: 'gh-ac', placeholder: 'Search for anything', ariaLabel: 'Search for anything' }),
  field({ type: 'text', placeholder: 'Tell us what you\'re selling', ariaLabel: 'Enter your product\'s brand, model, or UPC' }),
  field({ type: 'text', labelText: 'Zip code' }),
];
const scores = prelist.map(d => M.searchBoxScore('ebay', d));
assert.equal(scores[0], 0, 'the header search box is never the product search');
assert.ok(scores[1] >= 4 && scores[1] > scores[2], 'the prelist box wins');
assert.ok(M.searchBoxScore('amazon', amazonForm[6]) >= 6, 'Seller Central search-term box');
assert.equal(M.searchBoxScore('amazon', amazonForm[0]), 0, 'the SKU field is not a search box');

// --- condition helpers -----------------------------------------------------------------------
assert.equal(M.chooseOption(['Select', 'New', 'New (Other)', 'Used'], M.conditionLabels('NEW_OTHER', 'ebay')), 2);
assert.equal(M.chooseOption(['Select', 'New', 'Used - Like New', 'Used - Good'], M.conditionLabels('USED_GOOD', 'amazon')), 3);
assert.equal(M.chooseOption(['Select', 'New', 'Used - Like New', 'Used - Good'], M.conditionLabels('USED_EXCELLENT', 'amazon')), 2);
assert.equal(M.chooseOption(['Select', 'New'], M.conditionLabels('FOR_PARTS_OR_NOT_WORKING', 'amazon')), -1);
assert.equal(M.chooseOption(['Choose one', 'Lenox'], ['lenox']), 1);

// --- shipped files parse and the manifest is coherent -----------------------------------------
for (const name of ['background.js', 'content.js', 'sidepanel.js', 'update-bridge.js', 'matcher.js']) {
  new vm.Script(fs.readFileSync(path.join(root, name), 'utf8'), { filename: name });
}
const manifest = JSON.parse(fs.readFileSync(path.join(root, 'manifest.json'), 'utf8'));
assert.equal(manifest.name, 'Sweet Shelves Lister');
assert.equal(manifest.manifest_version, 3);
assert.ok(/^\d+\.\d+\.\d+$/.test(manifest.version));
assert.equal(manifest.background.service_worker, 'background.js');
assert.equal(manifest.side_panel.default_path, 'sidepanel.html');
for (const file of [manifest.background.service_worker, manifest.side_panel.default_path, ...manifest.content_scripts[0].js, ...Object.values(manifest.icons)]) {
  assert.ok(fs.existsSync(path.join(root, file)), 'manifest references a shipped file: ' + file);
}
assert.ok(manifest.host_permissions.includes('https://pi.nexuscentralhq.org/*'));
assert.ok(manifest.host_permissions.some(p => p.includes('ebay.com')) && manifest.host_permissions.some(p => p.includes('sellercentral.amazon.com')));
assert.ok(manifest.content_scripts[0].matches.every(m => m.endsWith('/lister/*')), 'the update bridge only runs on the update page');
const panel = fs.readFileSync(path.join(root, 'sidepanel.html'), 'utf8');
assert.ok(panel.includes('src="matcher.js"') && panel.includes('src="sidepanel.js"'));
for (const id of ['connStatus', 'pageCard', 'itemList', 'detail', 'confirm', 'pickCard', 'settings', 'signin', 'toast',
  'storeEbay', 'storeAmazon', 'viewList', 'viewItem', 'modal', 'toastAction', 'setAutoLink', 'setAutoPrepare']) {
  assert.ok(panel.includes(`id="${id}"`), 'side panel has #' + id);
}
const content = fs.readFileSync(path.join(root, 'content.js'), 'utf8');
for (const message of ['search', 'add-photos', 'guide-start', 'guide-next', 'guide-go', 'guide-use', 'guide-stop', 'assist-start', 'assist-stop', 'detect', 'fill']) {
  assert.ok(content.includes(`case '${message}'`), 'page script answers ' + message);
}
console.log('lister extension checks passed');
