// Sweet Shelves Lister in a real browser: load the unpacked extension, serve a fake Sweet Shelves
// API and eBay-like pages through request interception, then walk the queue flow from the side
// panel: pick the first queued item, search its UPC on the prelist page, fill the listing form,
// guide the missing fields, and record the listing when eBay's success page shows the item number.
// Uses the system Edge like the other Playwright tests.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { chromium } = require('playwright');

const extensionPath = fs.existsSync(path.join(__dirname, 'lister-extension')) ? path.join(__dirname, 'lister-extension')
  : path.join(__dirname, '..', 'lister-extension');

const UPC = '883049370897';
const queue = {
  ebay: [
    { id: 11, upc: UPC + '-1', baseUpc: UPC, suffixed: true, title: 'Lenox Butterfly Meadow Dinner Plate (unit 1)', thumb: '', addedAt: '2026-09-14T08:00:00', platform: 'ebay', status: 'queued', listedAt: '', listed: { ebay: false, amazon: false }, skipped: { ebay: false, amazon: false }, otherStatus: 'queued', existing: [], storeUrl: '', alreadyOnStore: false, links: [], proposal: { id: 7, status: 'proposed', ready: true }, preparing: false, prepStatus: { status: 'bad', reason: 'chip' }, notes: { written: 1, voice: 1 }, defect: 'Missing pieces' },
    { id: 12, upc: '012345678905', baseUpc: '012345678905', suffixed: false, title: 'Other item', thumb: '', addedAt: '2026-09-15T08:00:00', platform: 'ebay', status: 'queued', listedAt: '', listed: { ebay: false, amazon: false }, skipped: { ebay: false, amazon: false }, otherStatus: 'listed', existing: [{ listingId: '112233445566', state: 'Active' }], storeUrl: 'https://www.ebay.com/itm/112233445566', alreadyOnStore: true, links: [], proposal: {}, preparing: false },
  ],
  amazon: [],
};
queue.amazon = queue.ebay.map(it => ({ ...it, platform: 'amazon', existing: [], storeUrl: '', alreadyOnStore: false }));
const detail = {
  upc: UPC + '-1', baseUpc: UPC, suffixed: true, title: 'Lenox Butterfly Meadow Dinner Plate 10.75 in Porcelain',
  fields: {
    upc: UPC, sku: UPC + '-1', title: 'Lenox Butterfly Meadow Dinner Plate 10.75 in Porcelain', price: 24.5, currency: 'USD',
    quantity: 1, condition: 'USED_GOOD', amazonCondition: 'used_good', conditionDescription: 'Small chip on the rim',
    descriptionHtml: '<p>Lenox <b>Butterfly Meadow</b> dinner plate.</p>', descriptionText: 'Lenox Butterfly Meadow dinner plate.',
    categoryId: '36027', categoryPath: 'Home & Garden > Dinnerware', brand: 'Lenox', aspects: { Brand: ['Lenox'], Material: ['Porcelain'] },
    images: [], lots: ['L-1'], racks: ['A-3'], listableQuantity: 1, source: 'proposal', conditionDescriptionSource: 'notes',
  },
  prepStatus: { status: 'bad', reason: 'chip', updatedAt: '2026-09-15', quantity: 1 },
  gate: { prepQty: 1, rackQty: 1, liveEbay: 0, liveAmazon: 0, listable: 1, mismatch: false },
  condition: { condition: 'USED_GOOD', conditionDescription: 'Small chip on the rim', reason: 'prep notes mention a flaw', assumed: false },
  notes: [{ id: 5, text: 'LT: nuotrauka | EN: Small chip on the rim', english: 'Small chip on the rim', createdAt: '2026-09-15T10:00:00' }],
  defect: '', videos: [], inventory: { quantity: 1, positions: ['B-1'], rows: [] }, cost: 4.5, bol: {},
  voiceNotes: [{ id: 41, url: 'https://pi.nexuscentralhq.org/static/items_prep/v.webm', createdAt: '2026-09-15', english: 'scratched on the back', lithuanian: 'subraižytas gale', status: 'complete', error: '' }],
  photos: [{ url: 'https://pi.nexuscentralhq.org/static/items_prep/p1.jpg', source: 'prep', id: null, name: 'p1.jpg', from: '' }],
  existing: { ebay: [], amazon: [] }, links: [], queue: { id: 11, status: 'queued', listed: { ebay: false, amazon: false }, skipped: [] },
  proposal: { id: 7, status: 'proposed', ready: true, flags: [] }, preparing: { running: false, error: '' },
  mobilePhotosUrl: 'https://pi.nexuscentralhq.org/items-to-list/mobile-photos?upc=' + UPC + '-1', aiPhotoPrompt: 'Clean up this product photo.',
};

const prelist = `<!doctype html><title>Sell | eBay</title>
<input type="search" id="gh-ac" placeholder="Search for anything" aria-label="Search for anything">
<h1>Tell us what you're selling</h1>
<form id="prelist"><input type="text" id="what" placeholder="Enter your product's brand, model, or UPC" aria-label="Tell us what you're selling"><button type="submit">Search</button></form>
<script>document.getElementById('prelist').addEventListener('submit', e => { e.preventDefault(); location.href = 'https://www.ebay.com/sl/prelist/identify?sr=sug&title=' + encodeURIComponent(document.getElementById('what').value); });</script>`;

// eBay's "Find a match" step: other sellers' listings (their /itm/ ids), then "Confirm details" with a
// condition radio, then the real form. The real URL carries the other seller's item as itemId.
const matchPage = `<!doctype html><title>Find a match | eBay</title><h1>Find a match</h1><p>for "883049370897"</p>
<p>Related listings from other sellers</p><ul>
<li><a href="https://www.ebay.com/sl/prelist/identify?sr=sug&title=883049370897&mode=SellLikeItem&itemId=168611515264&view=sellnode-condition"><h3>Salt and Pepper Shakers Table Decoration Meal Condiment Container</h3></a></li>
<li><a href="https://www.ebay.com/sl/prelist/identify?sr=sug&title=883049370897&mode=SellLikeItem&itemId=335566001122&view=sellnode-condition"><h3>Lenox Butterfly Meadow Dinner Plate 10.75 in Porcelain</h3></a></li>
</ul><button>Continue without match</button>`;
const confirmPage = `<!doctype html><title>Confirm details | eBay</title><h2>Confirm details</h2>
<p>You have selected another seller's listing to help draft your item.</p>
<label><input type="radio" name="cond" value="new"> New</label><label><input type="radio" name="cond" value="open"> Open box</label>
<label><input type="radio" name="cond" value="used"> Used</label><label><input type="radio" name="cond" value="parts"> For parts or not working</label>
<a href="https://www.ebay.com/sl/list?mode=AddItem&draftId=77">Continue to listing</a>`;

const ebayForm = `<!doctype html><title>Create your listing | eBay</title>
<h1>Create your listing</h1>
<h2>PHOTOS &amp; VIDEO</h2><p>0/25</p><div class="uploader-dropzone">Drag and drop files</div>
<label for="title">Title *</label><input id="title" maxlength="80">
<label for="subtitle">Subtitle</label><input id="subtitle" maxlength="55">
<label for="upc">UPC</label><input id="upc" name="upc">
<label for="cl">Custom label (SKU)</label><input id="cl" name="customLabel">
<label for="cond">Condition *</label><select id="cond"><option>Select</option><option>New</option><option>New (Other)</option><option>Used</option><option>Good</option></select>
<label for="cd">Condition description</label><textarea id="cd" name="conditionDescription"></textarea>
<label for="brand">Brand</label><input id="brand" role="combobox">
<label for="material">Material</label><input id="material" role="combobox">
<label for="color">Color *</label><input id="color" role="combobox" required>
<label for="price">Buy It Now price</label><input id="price" name="price">
<label for="ship">Shipping cost</label><input id="ship" name="shippingCost">
<label for="qty">Quantity</label><input id="qty" name="quantity" type="number">
<div><span>Description</span><div id="desc" contenteditable="true" aria-label="Item description"></div></div>
<input type="file" id="photos" accept="image/*" multiple>
<input type="search" placeholder="Search eBay" aria-label="Search for anything">
<a role="button" href="https://www.ebay.com/sl/list/success?itemId=335566778899&mode=AddItem">List it</a>
<script>window.events = []; for (const el of document.querySelectorAll('input,textarea,select,[contenteditable]')) el.addEventListener('input', e => window.events.push(e.target.id));
document.getElementById('photos').addEventListener('change', e => { window.photoNames = Array.from(e.target.files).map(f => f.name); });</script>`;

// Seller Central /product-search: option tiles (a "Search" tile BEFORE the box), then the box and its Next button.
const amazonStart = `<!doctype html><title>Search products</title><h1>Search products</h1>
<div role="tablist"><button type="button">Search</button><button type="button">Product image</button><button type="button">Product IDs</button></div>
<section><p>Search your catalog or Amazon's catalog for a listing (or a variation) to sell or copy.</p>
<kat-input id="kw" placeholder="Product name, UPC, EAN, ISBN or ASIN"></kat-input><kat-button id="go" label="Next" disabled></kat-button></section>
<script>
// Katal keeps the real input/button inside an open shadow root, and only the host carries the
// placeholder / label / disabled state - the same shape Seller Central serves.
class KatInput extends HTMLElement {
  connectedCallback() {
    if (this.shadowRoot) return;
    const shadow = this.attachShadow({ mode: 'open' });
    shadow.innerHTML = '<input part="input">';
    this.input = shadow.querySelector('input');
    this.input.addEventListener('input', () => {
      const go = document.getElementById('go');
      setTimeout(() => { if (this.input.value) go.removeAttribute('disabled'); else go.setAttribute('disabled', ''); }, 700);
    });
  }
  get value() { return this.input ? this.input.value : ''; }
}
class KatButton extends HTMLElement {
  connectedCallback() {
    if (this.shadowRoot) return;
    const shadow = this.attachShadow({ mode: 'open' });
    shadow.innerHTML = '<button type="button">' + (this.getAttribute('label') || '') + '</button>';
    shadow.querySelector('button').addEventListener('click', () => {
      if (this.hasAttribute('disabled')) return;  // the host gates the click, as Katal does
      location.href = 'https://sellercentral.amazon.com/listing/results?q=' + encodeURIComponent(document.getElementById('kw').value);
    });
  }
}
customElements.define('kat-input', KatInput);
customElements.define('kat-button', KatButton);
</script>`;

// Seller Central's "Add offer" page as it really is: two look-alike fulfilment radios, a clickable
// "Match lowest price", the item condition, and a quantity box that only exists once the radio is
// answered. See the amazon-add-offer-form note.
const amazonOffer = `<!doctype html><title>Add offer</title><h1>Add offer</h1>
<label for="sku">SKU</label><input id="sku" name="sku">
<fieldset><legend>Fulfillment Channel Code</legend>
<label><input type="radio" name="fc" id="fba"> I want to use Fulfilled by Amazon (FBA) to ship my items and provide customer service if it sells. (Fulfilled by Amazon)</label>
<label><input type="radio" name="fc" id="mfn"> I want to ship this item myself or use Amazon Easy Ship if it sells. (Merchant Fulfilled)</label></fieldset>
<div id="qtyBox" hidden><label for="qty">Quantity</label><input id="qty" name="fulfillment_availability#1.quantity" type="number"></div>
<label for="price">Your Price</label><span>USD$</span><input id="price" name="price">
<div><a id="match" href="#">Match lowest price: USD$29.40</a></div>
<label for="cond">Item Condition</label><select id="cond"><option>Select</option><option>New</option><option>Used - Like New</option><option>Used - Good</option></select>
<script>
for (const id of ['mfn', 'fba']) document.getElementById(id).addEventListener('change', () => { document.getElementById('qtyBox').hidden = false; });
document.getElementById('match').addEventListener('click', event => {
  event.preventDefault();
  const price = document.getElementById('price');
  price.value = '29.40';
  price.dispatchEvent(new Event('input', { bubbles: true }));
});
</script>`;

const successPage = `<!doctype html><title>Your item is listed | eBay</title><h1>Congratulations! Your item is listed.</h1>
<p>Item number: 335566778899</p><a href="https://www.ebay.com/itm/335566778899">View listing</a>`;

(async () => {
  const userDataDir = fs.mkdtempSync(path.join(os.tmpdir(), 'lister-browser-'));
  const context = await chromium.launchPersistentContext(userDataDir, {
    channel: 'msedge', headless: false,
    args: ['--headless=new', `--disable-extensions-except=${extensionPath}`, `--load-extension=${extensionPath}`, '--no-first-run'],
  });
  const calls = { events: [], links: [], skip: [], prepare: [], learn: [], generate: [], detail: 0, preload: [], photoLink: [] };
  // Preload all: the fake job finishes one item per status poll.
  const preload = { running: false, upcs: [], polls: 0, steps: [] };
  const preloadItems = () => Object.fromEntries(preload.upcs.map((u, i) => {
    const finished = preload.polls > i;
    const steps = Object.fromEntries(preload.steps.map((st, j) => [st, finished ? 'done' : (preload.polls === i ? (j === 0 ? 'running' : 'pending') : 'pending')]));
    return [u, { running: !finished, queued: preload.polls < i, started: 1, startedAt: 'x', finishedAt: finished ? 'y' : '', steps, photos: null }];
  }));
  const preloadAll = () => { const items = preloadItems(); const done = Object.values(items).filter(p => !p.running).length; preload.running = done < preload.upcs.length;
    return { success: true, running: preload.running, upcs: preload.upcs, total: preload.upcs.length, done, percent: preload.upcs.length ? Math.round(done * 100 / preload.upcs.length) : 100, items }; };
  let linked = false;
  try {
    // Fake Sweet Shelves server: the extension must send the anti-CSRF header and cookies.
    await context.route('https://pi.nexuscentralhq.org/**', async route => {
      const request = route.request();
      const url = new URL(request.url());
      const json = (body, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) });
      if (url.pathname === '/api/lister/ping') return json({ success: true, version: '0.2.0', user: 'dan@example.com' });
      if (url.pathname === '/api/lister/queue') {
        const platform = url.searchParams.get('platform');
        const items = (queue[platform] || []).map(it => linked && it.upc === UPC + '-1' ? platform !== 'ebay' ? { ...it, otherStatus: 'listed' } : { ...it, status: 'listed', listedAt: '2026-09-16T10:00:00', links: [{ platform: 'ebay', listing_id: '335566778899' }] } : it);
        return json({ success: true, items, counts: { queued: items.filter(i => i.status === 'queued').length, listed: items.filter(i => i.status !== 'queued').length, hidden: 0 } });
      }
      if (url.pathname === `/api/lister/queue/${UPC}-1`) { calls.detail += 1; return json({ success: true, item: linked ? { ...detail, links: [{ platform: 'ebay', listing_id: '335566778899', url: 'https://www.ebay.com/itm/335566778899' }] } : detail }); }
      if (url.pathname === '/api/lister/queue/012345678905') return json({ success: true, item: { ...detail, upc: '012345678905', baseUpc: '012345678905', suffixed: false, proposal: {}, fields: { ...detail.fields, source: 'inventory', sku: '012345678905', upc: '012345678905' } } });
      if (url.pathname === '/api/lister/photos/fetch') return json({ success: true, name: (url.searchParams.get('url') || '').split('/').pop() || 'own.jpg', mime: 'image/jpeg', base64: '/9j/4AAQ' });
      if (url.pathname.includes('/tiny-')) return route.fulfill({ status: 200, contentType: 'image/svg+xml', body: '<svg xmlns="http://www.w3.org/2000/svg" width="120" height="90"><rect width="120" height="90"/></svg>' });
      if (url.pathname.includes('/big-')) return route.fulfill({ status: 200, contentType: 'image/svg+xml', body: '<svg xmlns="http://www.w3.org/2000/svg" width="800" height="800"><rect width="800" height="800"/></svg>' });
      if (url.pathname.startsWith('/static/')) return route.fulfill({ status: 200, contentType: 'image/gif', body: Buffer.from('R0lGODlhAQABAAAAACw=', 'base64') });
      if (url.pathname === '/api/lister/preload' && request.method() === 'GET') { if (preload.running) preload.polls += 1; return json(preloadAll()); }
      if (url.pathname.endsWith('/preload') && request.method() === 'GET') return json({ success: true, preload: preloadItems()[decodeURIComponent(url.pathname.split('/')[4])] || null });
      if (url.pathname === '/api/lister/qr') return route.fulfill({ status: 200, contentType: 'image/svg+xml', body: '<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10"><rect width="10" height="10"/></svg>' });
      assert.equal(request.headers()['x-sweet-shelves-lister'], '1', 'mutations carry the extension header: ' + url.pathname);
      if (url.pathname === '/api/lister/preload') { const body = request.postDataJSON(); calls.preload.push(body); Object.assign(preload, { running: true, upcs: body.upcs, steps: body.steps, polls: 0 }); return json(preloadAll()); }
      if (url.pathname.endsWith('/preload')) { calls.preload.push(request.postDataJSON()); return json({ success: true, preload: { running: false, steps: { prepare: 'done' }, photos: null, startedAt: 'x', finishedAt: 'y' } }); }
      if (url.pathname === '/api/lister/photos/ai') {
        const body = request.postDataJSON(); const name = body.url.split('/').pop();
        calls.aiPhotos = (calls.aiPhotos || []).concat(name);
        return json({ success: true, photo: { url: `https://pi.nexuscentralhq.org/static/listingagent_uploads/big-ai-${name}.png`, source: 'ai', id: 50 + calls.aiPhotos.length, name: `big-ai-${name}.png`, from: name } });
      }
      if (url.pathname.endsWith('/prepare')) { calls.prepare.push(url.pathname); return json({ success: true, status: 'ready', proposalId: 7 }); }
      if (url.pathname.endsWith('/skip')) { calls.skip.push(request.postDataJSON()); return json({ success: true, queue: 'queued' }); }
      if (url.pathname === '/api/lister/learn') { calls.learn.push(request.postDataJSON()); return json({ success: true, agreed: false, learned: { ebay: {} } }, 201); }
      if (url.pathname.endsWith('/amazon-check')) {
        const restricted = url.pathname.includes('012345678905');
        return json({ success: true, check: restricted ? { status: 'restricted', asin: 'B0OTHER', brand: 'Nike', reasons: ['Approval required for Nike'], error: '', cached: false } : { status: 'listable', asin: 'B0LENOX', brand: 'Lenox', reasons: [], error: '', cached: false } });
      }
      if (url.pathname.endsWith('/generate')) { const body = request.postDataJSON(); calls.generate.push(body); return json(body.kind === 'title' ? { success: true, kind: 'title', title: 'AI Lenox Butterfly Meadow Plate' } : { success: true, kind: 'description', descriptionHtml: '<p>AI description</p>', descriptionText: 'AI description' }); }
      if (url.pathname === '/api/lister/photo-link') { calls.photoLink.push(request.postDataJSON()); return json({ success: true, url: detail.mobilePhotosUrl + '&camera=1', sent: ['Danka'], errors: [] }); }
      if (url.pathname === '/api/lister/events') { calls.events.push(request.postDataJSON()); return json({ success: true }); }
      if (url.pathname === '/api/lister/links' && request.method() === 'GET') {
        const today = new Date(); const pad = n => String(n).padStart(2, '0');
        const stamp = `${today.getFullYear()}-${pad(today.getMonth() + 1)}-${pad(today.getDate())}T10:15:00`;
        return json({ success: true, links: [
          ...(linked ? [{ id: 1, upc: UPC + '-1', platform: 'ebay', listing_id: '335566778899', url: 'https://www.ebay.com/itm/335566778899', title: 'Lenox Butterfly Meadow Dinner Plate', price: 24.5, created_by: 'dan@example.com', created_at: stamp }] : []),
          { id: 0, upc: '099999999999', platform: 'amazon', sku: 'OLD-1', asin: 'B0OLD', title: 'Old Amazon listing', price: 10, created_by: 'ona@example.com', created_at: '2026-01-02T09:00:00' },
        ] });
      }
      if (url.pathname === '/api/lister/links') {
        calls.links.push(request.postDataJSON());
        linked = true;
        return json({ success: true, duplicate: false, link: { id: 1, upc: UPC + '-1', platform: 'ebay', listing_id: '335566778899', effects: { steps: ['listing queue marked listed on ebay', 'Items to List marked listed on ebay', 'warehouse match -> B-1 (883049370897-1)'] } } }, 201);
      }
      return json({ success: false, error: 'nope' }, 404);
    });
    await context.route('https://sellercentral.amazon.com/**', route => {
      const url = new URL(route.request().url());
      const html = body => route.fulfill({ status: 200, contentType: 'text/html', body });
      calls.amazonPages = (calls.amazonPages || []).concat(url.pathname + url.search);
      if (url.pathname === '/product-search') return html(amazonStart);
      if (url.pathname.startsWith('/abis/listing/syh') && url.searchParams.get('asin')) return html(amazonOffer);
      // /abis/listing/syh resumes the last draft: it redirects to the offer step and errors.
      if (url.pathname.startsWith('/abis/listing/syh')) return html('<title>Add price and inventory</title><h1>We encountered an unexpected error</h1>');
      return html('<title>Seller Central</title><h1>Inventory</h1>');
    });
    await context.route('https://www.ebay.com/**', route => {
      const url = new URL(route.request().url());
      const html = body => route.fulfill({ status: 200, contentType: 'text/html', body });
      if (url.pathname.startsWith('/sl/prelist/identify')) return html(url.searchParams.get('view') === 'sellnode-condition' ? confirmPage : matchPage);
      if (url.pathname.startsWith('/sl/prelist')) return html(prelist);
      if (url.pathname.startsWith('/sl/list/success')) return html(successPage);
      if (url.pathname.startsWith('/sl/list')) return html(ebayForm);
      return html('<title>eBay listing</title><h1>Lenox plate</h1>');
    });

    let [worker] = context.serviceWorkers();
    if (!worker) worker = await context.waitForEvent('serviceworker');
    const extensionId = new URL(worker.url()).host;

    const panel = await context.newPage();
    await panel.goto(`chrome-extension://${extensionId}/sidepanel.html`);
    // The queue and one item are two places now, not two tabs: only leave the item when you are in it.
    const toQueue = async () => { if (!(await panel.$eval("#crumb", el => el.hidden))) await panel.click("#backToQueue"); };
    await panel.waitForFunction(() => document.getElementById('connStatus').classList.contains('ok'));
    assert.ok((await panel.$eval('#connStatus', el => el.title)).includes('dan'), 'the connection dot names who is signed in');
    await panel.waitForSelector('.item.current');
    // The oldest queued item is picked automatically and shown first.
    assert.equal(await panel.$eval('.item.current', el => el.dataset.upc), UPC + '-1');
    assert.ok((await panel.textContent('#countEbay')).includes('2'));
    assert.ok((await panel.textContent('.item[data-upc="012345678905"]')).includes('on eBay'), 'a UPC the store already carries says so');
    // Suffixed rows highlight the suffix and carry the prep verdict instead of "unit 1".
    const firstRow = await panel.textContent('.item[data-upc="883049370897-1"]');
    assert.ok(!(await panel.$$eval('.item[data-upc="883049370897-1"] .chip', els => els.some(e => /^unit \d/.test(e.textContent.trim())))), 'no "unit 1" chip');
    assert.equal(await panel.$eval('.item[data-upc="883049370897-1"] .suffix', el => el.textContent), '1');
    assert.ok(firstRow.includes('chip'), 'what is wrong with it is on the row, in words: ' + firstRow);
    assert.equal(await panel.$eval('.item[data-upc="883049370897-1"] .sig.flag', el => el.textContent), 'chip', 'one flag per row, whatever raised it');
    assert.ok((await panel.$eval('.item[data-upc="883049370897-1"] .sig.flag', el => el.title)).includes('Missing pieces'), 'the BOL defect rides along in the tooltip');
    // Every queued item is checked against Amazon in the background; the verdict shows on the Amazon list only.
    // The busy row is always in the layout now, so that nothing moves when work starts; "lit" is the class.
    await panel.waitForFunction(() => !document.getElementById('busy').classList.contains('on'), null, { timeout: 15000 });
    assert.ok(!(await panel.$('.item.blocked')), 'the eBay list does not carry Amazon verdicts');
    await panel.click('#storeAmazon');
    await panel.waitForFunction(() => document.querySelector('.item[data-upc="012345678905"]')?.classList.contains('blocked'), null, { timeout: 15000 });
    assert.ok((await panel.textContent('.item[data-upc="012345678905"]')).includes('Amazon restricted'));
    assert.ok(!(await panel.$('.item[data-upc="883049370897-1"] .sig.block')), 'a listable UPC says nothing: a clean row is the good row');
    await panel.click('#storeEbay');
    await panel.waitForFunction(() => document.getElementById('countEbay').textContent.includes('2') && document.querySelector('.item.current')?.dataset.upc === '883049370897-1', null, { timeout: 15000 });
    await panel.waitForFunction(() => !document.getElementById('busy').classList.contains('on'), null, { timeout: 15000 });
    // Note marker, store-coloured badge, and the status / listed filters.
    assert.ok(firstRow.includes('📝 1') && firstRow.includes('🎤 1'), 'the row shows its note counts: ' + firstRow);
    assert.ok((await panel.textContent('.item[data-upc="012345678905"] .sig.quiet')).includes('on eBay'), 'a UPC the store already carries says so, quietly');
    await panel.click('#statusFlagged');
    assert.deepEqual(await panel.$$eval('.item', els => els.map(e => e.dataset.upc)), ['883049370897-1'], 'Flagged shows only the one with something wrong');
    assert.equal(await panel.$eval('#statusFlagged .n', el => el.textContent), '1', 'the filters carry their counts');
    await panel.click('#statusListed');
    assert.deepEqual(await panel.$$eval('.item', els => els.map(e => e.dataset.upc)), ['012345678905'], 'Listed shows what a store already carries');
    await panel.click('#statusAll');
    assert.equal((await panel.$$('.item')).length, 2);
    assert.ok(!(await panel.$('#autoSendPhotos')), 'the automatic switches are not on the work surface');
    // Preload all: the whole queue is prepared in the background; the bar shows the percentage, the rows their state.
    assert.ok(await panel.$eval('#preloadBar', el => el.hidden), 'no preload bar before a preload');
    await panel.click('#preloadAll');
    await panel.waitForFunction(() => !document.getElementById('preloadBar').hidden, null, { timeout: 5000 });
    assert.deepEqual(calls.preload.at(-1), { upcs: ['883049370897-1', '012345678905'], steps: ['prepare'] }, 'the queued units with the switched-on steps (values only: no AI switch is on)');
    assert.ok((await panel.textContent('#preloadBar')).includes('Preloading'), 'the bar says it is preloading');
    assert.ok(await panel.$eval('#preloadAll', el => el.disabled), 'the button waits while it runs');
    await panel.waitForFunction(() => document.querySelector('#preloadBar').textContent.includes('100%'), null, { timeout: 15000 });
    assert.ok((await panel.textContent('#preloadBar')).includes('All 2 preloaded'), await panel.textContent('#preloadBar'));
    assert.ok(await panel.$('.item[data-upc="883049370897-1"] .bolt.on'), 'each row lights its bolt when it is done');
    assert.ok(await panel.$('.item[data-upc="012345678905"] .bolt.on'));
    assert.ok(!(await panel.$eval('#preloadAll', el => el.disabled)), 'the button is back');
    await panel.click('#preloadHide');
    assert.ok(await panel.$eval('#preloadBar', el => el.hidden), 'the finished bar can be dismissed');
    // Day theme by default; the header button switches to night and remembers it.
    assert.equal(await panel.$eval('html', el => el.dataset.theme), 'light');
    await panel.click('#themeBtn');
    await panel.waitForFunction(() => document.documentElement.dataset.theme === 'dark');
    assert.equal(await panel.$eval('#themeBtn', el => el.textContent), '☀');
    await panel.click('#themeBtn');
    await panel.waitForFunction(() => document.documentElement.dataset.theme === 'light');
    assert.ok(await panel.$eval('#pageCard', el => el.hidden), 'nothing is wrong, so the notice card is not there');
    assert.equal(await panel.textContent('#goBtn'), 'Start on eBay', 'the action bar names the next thing to do');
    // No Start button (double-clicking a queue item starts it, checked at the end). The automatic switches fold into one line.
    assert.ok(!(await panel.$('#startBtn')), 'the Start button is gone');
    await panel.click('#settingsBtn');
    assert.ok(await panel.$eval('.toggles-body', el => el.hidden), 'the automatic switches start minimized');
    const togglesHead = await panel.textContent('#togglesBtn');
    assert.ok(togglesHead.includes('Automatic') && togglesHead.includes('all off'), togglesHead);
    await panel.click('#togglesBtn');
    await panel.waitForFunction(() => !document.querySelector('.toggles-body').hidden);
    assert.deepEqual(await panel.$$eval('.toggles .tg-title', els => els.map(e => e.textContent)), ['Listing text', 'Photos'], 'the menu is grouped');
    assert.ok(await panel.$eval('#autoSendAiOnly', el => el.checked), '"by default only send AI generated" starts on');
    await panel.click('label.sw:has(#autoSendPhotos)');
    await panel.waitForFunction(() => document.getElementById('togglesBtn').textContent.includes('send photos (AI only)'));
    await panel.click('label.sw:has(#autoSendPhotos)');
    await panel.waitForFunction(() => document.getElementById('togglesBtn').textContent.includes('all off'));
    await panel.click('#togglesBtn');
    await panel.waitForFunction(() => document.querySelector('.toggles-body').hidden);
    await panel.click('#settingsBtn');
    await panel.waitForFunction(() => document.getElementById('settings').hidden);

    // The X asks first, then tells the server which store list to leave.
    await toQueue();
    await panel.click('.item[data-upc="012345678905"] button[data-skip]');
    await panel.waitForSelector('#modalOk');
    assert.ok((await panel.textContent('#modal')).includes('Amazon'), 'the confirmation explains the other store');
    await panel.click('#modalCancel');
    assert.equal(calls.skip.length, 0, 'cancel removes nothing');
    await toQueue();
    await panel.click('.item[data-upc="012345678905"] button[data-skip]');
    await panel.waitForSelector('#modalOk');
    await panel.click('#modalOk');
    await panel.waitForFunction(() => document.getElementById('toast').textContent.includes('Removed'));
    assert.deepEqual(calls.skip[0].platform, 'ebay');
    assert.equal(await panel.$eval('.item.current', el => el.dataset.upc), UPC + '-1', 'the current item is untouched');

    // Item view: unit, prep status, stock, store status, the condition note flagged as coming from prep, the QR code.
    await toQueue();
    await panel.click('.item[data-upc="883049370897-1"]');
    await panel.waitForSelector('.strip');
    assert.ok(!(await panel.$eval('#crumb', el => el.hidden)), 'the crumb is the way back to the queue');
    assert.equal((await panel.textContent('#backToQueue')).replace(/\s+/g, ' ').trim(), '\u2190 Back', 'the way out is a button that says Back');
    assert.ok(await panel.$eval('#listCard', el => el.hidden), 'one place at a time: the queue steps aside');
    const detailText = await panel.textContent('#detail');
    for (const expected of ['unit 1', '@ B-1', '1 to list', 'Small chip on the rim', 'chip']) {
      assert.ok(!(await panel.textContent('#detail')).includes('On the listing'), 'no "On the listing" label');
      assert.ok(await panel.$('.sect .note2'), 'notes sit in their own section');
      assert.ok(detailText.includes(expected), 'item view shows "' + expected + '"');
    }
    for (const gone of ['Prepped:', 'basic values', 'Prepare', 'Location matches', 'Copy all values', 'Open in Listing Agent', 'Items to List']) assert.ok(!detailText.includes(gone), 'item view no longer shows "' + gone + '"');
    // Notes come before photos, Lithuanian and English side by side, voice notes with a mic and a play button.
    assert.ok(detailText.indexOf('Notes') < detailText.indexOf('Photos'), 'notes are above the photos');
    const voiceRow = await panel.$eval('.note2', row => ({ ico: row.querySelector('.ico').textContent, lt: row.querySelector('.lt').textContent, en: row.querySelector('.en').textContent, play: Boolean(row.querySelector('button[data-play]')) }));
    assert.deepEqual(voiceRow, { ico: '🎤', lt: 'subraižytas gale', en: 'scratched on the back', play: true });
    const writtenRow = await panel.$$eval('.note2', rows => rows.map(row => ({ lt: row.querySelector('.lt').textContent, en: row.querySelector('.en').textContent })).pop());
    assert.deepEqual(writtenRow, { lt: 'nuotrauka', en: 'Small chip on the rim' }, 'a "LT: … | EN: …" note is split into its halves');
    assert.equal(await panel.$eval('.photo .tag.prep', t => t.textContent), 'prep', 'prep photos carry a small bubble');
    // No second copy of the switches by the photos, and no switch at all on the work surface.
    assert.ok(!(await panel.$('#photo_autoAiPhotos')), 'the duplicate photo switch menu is gone');
    assert.ok(await panel.$$eval('label.sw', els => els.length > 0 && els.every(e => e.closest('#settings') !== null)), 'every automatic switch lives in Settings');
    assert.ok(await panel.$eval('#settings', el => el.hidden), 'and Settings is shut while you work');
    assert.ok((await panel.textContent('.autoline')).includes('By itself'), 'the item view reports what runs by itself, in one line');
    // Stock and the two stores: one strip, three cells, colour only where it means something.
    const cells = await panel.$$eval('.strip .cell', els => els.map(c => ({ k: c.querySelector('.k').textContent, v: c.querySelector('.v').textContent.trim(), cls: c.className })));
    assert.deepEqual(cells.map(c => c.k), ['Rack', 'eBay', 'Amazon']);
    assert.ok(!detailText.includes('prep and rack differ'), 'the mismatch is shown visually, not as a sentence');
    assert.equal(await panel.$eval('.strip .cell a', a => a.getAttribute('href')), 'https://pi.nexuscentralhq.org/unified-search?q=883049370897-1', 'the rack count links to the warehouse search');
    assert.ok(!detailText.includes('/item-prep?upc='), 'the old Item Prep link is gone');
    assert.ok(cells[0].cls.includes('good') && cells[1].v === 'Not listed' && cells[1].cls.includes('idle') && cells[2].v === 'Not listed', JSON.stringify(cells));
    // The thing you must act on is said once, in words, above the strip.
    assert.ok((await panel.textContent('.alert')).includes('chip'), 'what is wrong is spelled out above the strip, not left as a chip');
    assert.ok(!(await panel.$('.qr img')), 'the QR code starts minimized');
    await panel.click('#addPhoto');
    await panel.waitForSelector('.qr img');
    await panel.click('#addPhoto');

    // "+ Photo link" sends the same camera page through the Telegram bot instead of showing a QR code.
    await panel.click('#photoLink');
    await panel.waitForFunction(() => document.getElementById('toast')?.textContent.includes('Camera link sent'));
    assert.deepEqual(calls.photoLink, [{ upc: UPC + '-1' }]);
    assert.ok((await panel.$eval('#toast', el => el.textContent)).includes('Danka'), 'the toast names who got it');

    // On eBay's prelist page the panel types the UPC and submits the search by itself.
    // (Opened from Playwright rather than the Start button: a tab the extension opens starts
    // loading before request interception attaches, so it would reach the real eBay.)
    const store = await context.newPage();
    await store.goto('https://www.ebay.com/sl/prelist/suggest?sr=wn');
    await store.bringToFront();
    // Nothing happens by itself on the search page; clicking the item in the queue searches its UPC.
    await panel.waitForFunction(() => document.body.dataset.pageKind === 'listing-start', null, { timeout: 15000 });
    await store.waitForTimeout(1500);
    assert.ok(store.url().includes('/sl/prelist/suggest'), 'no search without a click');
    await toQueue();
    await toQueue();
    await panel.click('.item[data-upc="883049370897-1"]');
    await store.waitForURL(/\/sl\/prelist\/identify\?sr=sug&title=883049370897/, { timeout: 20000 });
    assert.ok(!store.url().includes('%2D1'), 'the store search uses the catalog UPC without the -suffix');

    // "Find a match": the panel highlights the listing that looks like ours; the user's click is learned,
    // and the other seller's item id in the next URL must never be recorded as our listing.
    await panel.waitForFunction(() => document.body.dataset.pageKind === 'listing-match', null, { timeout: 15000 });
    await store.waitForFunction(() => Array.from(document.querySelectorAll('li')).some(li => li.style.boxShadow.includes('rgba(10, 156, 108')), null, { timeout: 15000 });
    const picked = await store.evaluate(() => Array.from(document.querySelectorAll('li')).find(li => li.style.boxShadow.includes('rgba(10, 156, 108')).textContent.trim());
    assert.ok(picked.startsWith('Lenox Butterfly Meadow'), 'the Lenox listing is the suggested match, got: ' + picked);
    assert.ok((await panel.textContent('#pageCard')).includes('Suggested:'));
    await store.click('li:nth-child(1) a');  // the user disagrees and picks the other listing
    await store.waitForURL(/view=sellnode-condition/, { timeout: 15000 });
    await panel.waitForFunction(() => document.getElementById('toast').textContent.includes('Learned match'), null, { timeout: 15000 });
    assert.equal(calls.learn.length, 1);
    assert.equal(calls.learn[0].step, 'match');
    assert.ok(calls.learn[0].chosen.startsWith('Salt and Pepper Shakers'));
    assert.ok(calls.learn[0].suggested.startsWith('Lenox Butterfly Meadow'));
    assert.equal(calls.learn[0].upc, UPC + '-1');

    // "Confirm details": our condition (USED_GOOD) is pre-selected; still nothing is recorded as listed.
    await panel.waitForFunction(() => document.body.dataset.pageKind === 'listing-confirm', null, { timeout: 15000 });
    await store.waitForFunction(() => document.querySelector('input[value="used"]').checked, null, { timeout: 15000 });
    assert.equal(calls.links.length, 0, 'the SellLikeItem item id is another seller\'s listing, not ours');
    assert.notEqual(await panel.$eval('body', el => el.dataset.pageKind), 'listing-success');
    await store.click('a[href*="/sl/list?mode=AddItem"]');

    // The listing form is filled from the prepared values, then the guide points at what is left.
    await panel.waitForFunction(() => document.body.dataset.pageKind === 'listing-form', null, { timeout: 20000 });
    await store.waitForFunction(() => document.getElementById('title').value.length > 0, null, { timeout: 20000 });
    await store.waitForFunction(() => document.getElementById('color').style.boxShadow.includes('rgba(220, 38, 38'), null, { timeout: 10000 }).catch(() => {});  // the guide starts right after the fill
    const filled = await store.evaluate(() => ({
      title: document.getElementById('title').value, subtitle: document.getElementById('subtitle').value,
      upc: document.getElementById('upc').value, sku: document.getElementById('cl').value,
      condition: document.getElementById('cond').value, cd: document.getElementById('cd').value,
      brand: document.getElementById('brand').value, material: document.getElementById('material').value,
      price: document.getElementById('price').value, ship: document.getElementById('ship').value,
      qty: document.getElementById('qty').value, desc: document.getElementById('desc').innerHTML,
      search: document.querySelector('input[type=search]').value, events: window.events,
      colorOutline: document.getElementById('color').style.boxShadow,
    }));
    assert.equal(filled.title, detail.fields.title);
    assert.equal(filled.subtitle, '', 'subtitle stays empty');
    assert.equal(filled.upc, UPC, 'the store gets the catalog UPC');
    assert.equal(filled.sku, UPC + '-1', 'the SKU keeps the unit suffix');
    assert.equal(filled.condition, 'Good');
    assert.equal(filled.cd, 'Small chip on the rim');
    assert.equal(filled.brand, 'Lenox');
    assert.equal(filled.material, 'Porcelain');
    assert.equal(filled.price, '24.5');
    assert.equal(filled.ship, '', 'shipping cost is not the price');
    assert.equal(filled.qty, '1');
    assert.ok(filled.desc.includes('Butterfly Meadow'), 'description editor received the HTML');
    assert.equal(filled.search, '', 'the site search box is untouched');
    assert.ok(filled.events.includes('title') && filled.events.includes('price'), 'React-style input events fired');
    assert.ok(filled.colorOutline.includes('rgba(220, 38, 38'), 'the empty required Color field glows red by the guide, got: ' + filled.colorOutline);
    // The checklist overlay on the page: red = required and empty, green = filled, blue = from the prep notes.
    // The overlay leads with the step you are on; every step is a checkpoint on the rail, and the
    // whole list is behind the chevron.
    await store.waitForFunction(() => document.querySelectorAll('#ss-lister-guide [data-ss-cp]').length >= 1, null, { timeout: 15000 });
    assert.ok(await store.$('#ss-lister-guide [data-ss="focus"], #ss-lister-guide [data-ss="body"] b'), 'the focus card names the current field');
    const openSteps = async () => { if (!(await store.$('#ss-lister-guide [data-ss-row]'))) await store.click('#ss-lister-guide [data-ss="steps"]'); await store.waitForSelector('#ss-lister-guide [data-ss-row]'); };
    await openSteps();
    const rows = await store.$$eval('#ss-lister-guide [data-ss-row]', els => els.map(row => ({ text: row.textContent.replace(/\s+/g, ' ').trim(), dot: row.querySelector('span').style.background })));
    const cpColours = await store.$$eval('#ss-lister-guide [data-ss-cp] span', els => els.map(s => s.style.background));
    assert.equal(cpColours.length, rows.length, 'one checkpoint per step');
    // Hovering a checkpoint reads that step out below it. The card is a fixed height so the overlay
    // cannot grow, shift out from under the cursor and bounce hover on and off - which it used to.
    await store.click('#ss-lister-guide [data-ss="steps"]');  // focus card, where the preview lands
    const hudBox = () => store.$eval('#ss-lister-guide', el => { const r = el.getBoundingClientRect(); return { top: Math.round(r.top), height: Math.round(r.height) }; });
    const restingBox = await hudBox();
    for (const i of [0, rows.length - 1, Math.floor(rows.length / 2)]) {
      await store.hover(`#ss-lister-guide [data-ss-cp="${i}"]`);
      assert.deepEqual(await hudBox(), restingBox, `hovering checkpoint ${i} must not move or resize the overlay`);
    }
    await store.hover('#ss-lister-guide [data-ss="title"]');
    assert.deepEqual(await hudBox(), restingBox, 'and it is back where it started');
    await openSteps();
    const rowFor = name => rows.find(r => r.text.startsWith(name));
    assert.ok(rowFor('Photos') && rowFor('Photos').dot.includes('220, 38, 38'), 'photos (0/25) are a required, open (red) row: ' + JSON.stringify(rows));
    assert.ok(rowFor('Color').dot.includes('220, 38, 38'), 'the empty required Color field is red');
    assert.ok(rowFor('Title').dot.includes('22, 163, 74'), 'the filled title is green');
    assert.ok(rowFor('Quantity').text.includes('· 1'), 'the quantity row shows the entered quantity: ' + rowFor('Quantity').text);
    assert.ok(!rowFor('UPC'), 'the UPC is not on the checklist');
    // The warehouse count sits under the store's own quantity box while the listing is being set up.
    const qtyTag = await store.$eval('[data-ss-qty]', el => ({ text: el.textContent, bg: el.style.background, shown: el.style.display }));
    assert.ok(qtyTag.text.includes('1 on the rack') && qtyTag.text.includes('B-1'), 'the quantity badge shows the rack count and position: ' + qtyTag.text);
    assert.ok(qtyTag.shown !== 'none' && qtyTag.bg.includes('10, 156, 108'), 'the badge is visible and green while the quantity fits the rack');
    await store.fill('#qty', '4');
    await store.waitForFunction(() => document.querySelector('[data-ss-qty]')?.textContent.includes('you typed 4'), null, { timeout: 15000 });
    assert.ok((await store.$eval('[data-ss-qty]', el => el.style.background)).includes('220, 38, 38'), 'typing more than the rack holds turns the badge red');
    await store.fill('#qty', '1');
    await store.waitForFunction(() => !document.querySelector('[data-ss-qty]')?.textContent.includes('you typed'), null, { timeout: 15000 });
    assert.ok(rows.at(-1).text.startsWith('Quantity') && rows.at(-2).text.startsWith('Price'), 'price then quantity close the checklist: ' + rows.map(r => r.text.split(' ')[0]).join(','));
    assert.ok(rowFor('Condition description').dot.includes('37, 99, 235'), 'the note-sourced condition description is blue');
    assert.ok(await store.$eval('#ss-lister-guide [data-ss-row] sup[title*="prep notes"]', el => el.textContent === 'NOTE'), 'where it came from is one mark, not a paragraph');
    assert.ok((await panel.textContent('#goBtn')).includes('left'), 'the action bar counts what is left: ' + await panel.textContent('#goBtn'));
    // The actions live on the overlay now, not on the store card.
    assert.ok(!(await panel.$('#fillBtn')) && !(await panel.$('#pickBtn')), 'no Fill / Pick buttons on the store card');
    // The HUD wears the panel's day theme and follows its moon/sun switch.
    assert.equal(await store.$eval('#ss-lister-guide', el => el.style.background), 'rgb(255, 255, 255)', 'the HUD is light like the panel');
    await panel.click('#themeBtn');
    await store.waitForFunction(() => document.querySelector('#ss-lister-guide')?.style.background === 'rgb(15, 23, 42)', null, { timeout: 10000 });
    await panel.click('#themeBtn');
    await store.waitForFunction(() => document.querySelector('#ss-lister-guide')?.style.background === 'rgb(255, 255, 255)', null, { timeout: 10000 });
    await store.click('#ss-lister-guide [data-ss="steps"]');  // back to the step you are on
    const footButtons = await store.$$eval('#ss-lister-guide [data-ss="foot"] button', els => els.filter(b => b.style.display !== 'none').map(b => b.textContent.trim()));
    assert.ok(footButtons.some(b => b.startsWith('Skip')), 'the overlay footer can skip a step: ' + footButtons);
    assert.ok(footButtons.some(b => /Use this|Go to it|Next field/.test(b)), 'the green button names what it does here: ' + footButtons);
    await openSteps();
    assert.ok(!footButtons.includes('Fill page'), 'the overlay has no Fill page button: ' + footButtons);
    assert.ok(!footButtons.includes('Pick a field…') && !footButtons.includes('Hide'), 'no Pick a field / Hide on the overlay footer');
    // The AI button beside the Title row writes the title through the panel and puts it on the page.
    await openSteps();
    await store.click(`#ss-lister-guide [data-ss-row="${rows.findIndex(r => r.text.startsWith('Title'))}"] [data-ss-ai="title"]`);
    await store.waitForFunction(() => document.getElementById('title').value === 'AI Lenox Butterfly Meadow Plate', null, { timeout: 15000 });
    assert.equal(calls.generate[0].kind, 'title');
    assert.ok(calls.generate[0].values.notes.includes('scratched on the back'), 'the voice note text feeds the AI prompt');
    await store.waitForFunction(() => Array.from(document.querySelectorAll('#ss-lister-guide [data-ss-row] sup')).some(s => s.title.includes('generated with AI')), null, { timeout: 10000 });
    const titleRowText = await store.$$eval('#ss-lister-guide [data-ss-row]', els => { const row = els.find(e => e.textContent.trim().startsWith('Title')); return (row?.querySelector('sup')?.title || '') + ' | ' + row?.textContent.replace(/\s+/g, ' ').trim(); });
    assert.ok(await store.$eval('#ss-lister-guide', el => el.style.left === '16px' && el.style.right === ''), 'the overlay starts on the left');
    // Dragging the HUD by its header remembers the spot for this browser (localStorage + chrome.storage.local).
    const head = await store.$('#ss-lister-guide [data-ss="head"]');
    const box = await head.boundingBox();
    await store.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
    await store.mouse.down();
    await store.mouse.move(box.x + box.width / 2 + 120, box.y + box.height / 2 - 90, { steps: 6 });
    await store.mouse.up();
    const dragged = await store.$eval('#ss-lister-guide', el => ({ left: parseFloat(el.style.left), top: parseFloat(el.style.top), bottom: el.style.bottom }));
    assert.ok(dragged.left > 16 && dragged.bottom === 'auto', 'the HUD moved with the pointer: ' + JSON.stringify(dragged));
    const savedPos = await store.evaluate(() => JSON.parse(localStorage.getItem('ss-lister-guide-pos') || 'null'));
    assert.ok(savedPos && Math.round(savedPos.left) === Math.round(dragged.left) && Math.round(savedPos.top) === Math.round(dragged.top),
      'the dragged position is saved for this browser: ' + JSON.stringify(savedPos));
    // Put it back where it was so the HUD does not sit over the fields the rest of this test clicks.
    await store.mouse.move(box.x + box.width / 2 + 120, box.y + box.height / 2 - 90);
    await store.mouse.down();
    await store.mouse.move(box.x + box.width / 2, box.y + box.height / 2, { steps: 6 });
    await store.mouse.up();
    assert.ok(titleRowText.includes('generated with AI') && !titleRowText.includes('automatically'), 'a manual AI run is marked, without "(automatically)": ' + titleRowText);
    assert.ok(titleRowText.split(' | ')[1].includes('AI'), 'the mark is one superscript on the row, not a line of its own: ' + titleRowText);
    // Re-reading the page while the guide is up must not throw (0.2.2 did: "reading 'length'").
    await panel.click('#moreBtn');
    await panel.click('.menu-item:has-text("Re-read the page")');
    await panel.waitForFunction(() => document.body.dataset.pageKind === 'listing-form', null, { timeout: 10000 });
    assert.ok(!(await panel.textContent('#pageCard')).includes('Page script:'), await panel.textContent('#pageCard'));
    // Clicking a green (filled) row still jumps to that field; the condition description row goes to its own field.
    const titleIndex = rows.findIndex(r => r.text.startsWith('Title'));
    await openSteps();
    await store.click(`#ss-lister-guide [data-ss-row="${titleIndex}"]`);
    await store.waitForFunction(() => document.activeElement && document.activeElement.id === 'title', null, { timeout: 10000 });
    const cdIndex = rows.findIndex(r => r.text.startsWith('Condition description'));
    await openSteps();
    await store.click(`#ss-lister-guide [data-ss-row="${cdIndex}"]`);
    await store.waitForFunction(() => !document.querySelector('#ss-lister-guide [data-ss-row]'), null, { timeout: 10000 });
    await store.waitForFunction(() => document.activeElement && document.activeElement.id === 'cd', null, { timeout: 10000 });
    assert.equal(await store.evaluate(() => document.activeElement.id), 'cd');
    // A checkpoint on the rail takes you straight back to a step you already filled.
    await store.click(`#ss-lister-guide [data-ss-cp="${titleIndex}"]`);
    await store.waitForFunction(() => document.activeElement && document.activeElement.id === 'title', null, { timeout: 10000 });
    // The item is locked in on the listing page: the queue is hidden and the item view is up.
    assert.ok(!(await panel.$eval('#crumbLock', el => el.hidden)), 'the crumb says you are on the form');
    assert.ok(await panel.$eval('#listCard', el => el.hidden));
    assert.ok(!(await panel.$eval('#detail', el => el.hidden)));
    assert.ok(calls.events.length >= 1 && calls.events[0].event === 'helper_filled' && calls.events[0].proposal_id === 7, 'fill reported to the proposal timeline');

    // Once every row is green the overlay offers one clear button that jumps to the page's List it.
    assert.equal(await store.$eval('#ss-lister-guide [data-ss="ready"]', b => b.style.display), 'none', 'not ready while Color and Photos are open');
    // Photos reach the page's uploader through the file input (the item view is already up: locked).
    detail.photos.push({ url: 'https://pi.nexuscentralhq.org/static/listingagent_uploads/own.jpg', source: 'listing', id: 1, name: 'own.jpg' });
    await panel.click('#photoRefresh');
    await panel.waitForSelector('.photo.listing');
    await panel.click('.photo.listing input[data-select]');  // only the listing photo, not the prep one
    await panel.click('#sendPhotos');
    await store.waitForFunction(() => Array.isArray(window.photoNames), null, { timeout: 15000 });
    assert.deepEqual(await store.evaluate(() => window.photoNames), ['own.jpg']);
    // A photo that went to the page is greyed out on its tile, so it is not sent twice by hand.
    await panel.waitForFunction(() => document.querySelector('.photo.listing.used'), null, { timeout: 10000 });
    assert.equal(await panel.$eval('.photo.listing .tag.used', t => t.textContent), 'used', 'the used tile carries a used bubble');
    assert.ok(!(await panel.$('.photo.prep.used')), 'a photo that never went to the page stays normal');

    // A photo dragged from the panel arrives on the page as our JSON drag type (a File cannot cross
    // from an extension page); the page script turns it into a real file for the uploader it landed on.
    await store.evaluate(() => {
      const dt = new DataTransfer();
      dt.setData('application/x-sweetshelves-photo', JSON.stringify({ url: 'https://pi.nexuscentralhq.org/static/listingagent_uploads/dragged.jpg', name: 'dragged.jpg', type: 'image/jpeg', base64: '/9j/4AAQ' }));
      const zone = document.querySelector('.uploader-dropzone');
      zone.dispatchEvent(new DragEvent('dragover', { bubbles: true, cancelable: true, dataTransfer: dt }));
      zone.dispatchEvent(new DragEvent('drop', { bubbles: true, cancelable: true, dataTransfer: dt }));
    });
    await store.waitForFunction(() => Array.isArray(window.photoNames) && window.photoNames.includes('dragged.jpg'), null, { timeout: 15000 });
    await panel.waitForFunction(() => document.getElementById('toast').textContent.includes('Dropped dragged.jpg'), null, { timeout: 10000 });
    // ...and when the bytes were not cached before the drag, the page script asks the panel for them.
    await store.evaluate(() => {
      const dt = new DataTransfer();
      dt.setData('application/x-sweetshelves-photo', JSON.stringify({ url: 'https://pi.nexuscentralhq.org/static/listingagent_uploads/own.jpg', name: 'own.jpg', type: 'image/jpeg', base64: '' }));
      document.querySelector('.uploader-dropzone').dispatchEvent(new DragEvent('drop', { bubbles: true, cancelable: true, dataTransfer: dt }));
    });
    await store.waitForFunction(() => window.photoNames && window.photoNames.length === 1 && window.photoNames[0] === 'own.jpg', null, { timeout: 15000 });

    // The AI photoshop prompt: typing in it stays on this item, "Save for all" makes it the default.
    await panel.click('details:has(#aiPrompt) summary');
    await panel.$eval('#aiPrompt', el => { el.value = 'Just this one.'; el.dispatchEvent(new Event('input', { bubbles: true })); });
    assert.equal(await panel.$eval('#aiPromptWhere', el => el.textContent), 'this item only');
    await panel.click('#aiPromptSave');
    await panel.waitForFunction(() => document.getElementById('aiPromptWhere').textContent === 'saved for all items', null, { timeout: 10000 });
    assert.equal(await panel.$eval('#aiPrompt', el => el.value), 'Just this one.', 'the saved prompt stays in the box');
    await panel.click('#aiPromptReset');
    await panel.waitForFunction(() => document.getElementById('aiPromptWhere').textContent === 'the default prompt', null, { timeout: 10000 });
    assert.equal(await panel.$eval('#aiPrompt', el => el.value), 'Clean up this product photo.', 'reset goes back to the default prompt');

    // Nothing ticked: by default only AI generated photos go, and never a too-small one.
    // clear every tick (each photo has its own checkbox)
    for (const box of await panel.$$('.photo.selected input[data-select]')) await box.click();
    await panel.waitForFunction(() => !document.querySelector('.photo.selected'));
    await panel.click('#sendPhotos');
    await panel.waitForFunction(() => document.getElementById('toast').textContent.includes('No AI photos yet'), null, { timeout: 10000 });
    detail.photos.unshift({ url: 'https://pi.nexuscentralhq.org/static/listingagent_uploads/big-ai.png', source: 'ai', id: 2, name: 'big-ai.png', from: 'own.jpg' },
      { url: 'https://pi.nexuscentralhq.org/static/listingagent_uploads/tiny-ai.png', source: 'ai', id: 3, name: 'tiny-ai.png', from: 'p1.jpg' });
    await panel.click('#photoRefresh');
    await panel.waitForSelector('.photo.ai');
    await panel.waitForFunction(() => { const t = document.querySelector('.photo[data-url$="tiny-ai.png"]'); return t && !t.querySelector('.tag.small').hidden && t.classList.contains('too-small'); }, null, { timeout: 10000 });
    assert.ok(await panel.$eval('.photo[data-url$="big-ai.png"] .tag.small', el => el.hidden), 'a big photo is not flagged');
    await store.evaluate(() => { window.photoNames = null; });
    await panel.click('#sendPhotos');
    await store.waitForFunction(() => Array.isArray(window.photoNames), null, { timeout: 15000 });
    assert.equal(await store.evaluate(() => window.photoNames.length), 1, 'only the big AI photo went to the page');
    detail.photos.splice(0, 2);
    await panel.click('#photoRefresh');
    await panel.waitForFunction(() => !document.querySelector('.photo.ai'));

    // Photos taken on the phone (QR) while the listing form is open: with auto AI photoshop + auto send on,
    // the panel notices them by itself, cleans up only the new one and sends only its AI version.
    await panel.click('#settingsBtn');  // the automatic switches live in Settings now
    await panel.click('#togglesBtn');
    await panel.waitForFunction(() => !document.querySelector('.toggles-body').hidden);
    await panel.click('label.sw:has(#autoSendPhotos)');
    await panel.click('label.sw:has(#autoAiPhotos)');
    await panel.click('#settingsBtn');  // and back to the item while they run
    await store.waitForFunction(() => Array.isArray(window.photoNames) && window.photoNames.some(n => n.startsWith("big-ai-")), null, { timeout: 20000 });
    await panel.waitForFunction(() => !document.getElementById('busy') || !document.body.textContent.includes('AI photoshop 1'), null, { timeout: 10000 });
    const aiBefore = calls.aiPhotos.length;
    await store.evaluate(() => { window.photoNames = null; });
    detail.photos.push({ url: 'https://pi.nexuscentralhq.org/static/listingagent_uploads/phone.jpg', source: 'listing', id: 9, name: 'phone.jpg' });
    await panel.waitForFunction(() => document.getElementById('toast').textContent.includes('from the phone') || !!document.querySelector('.photo[data-url$="phone.jpg"]'), null, { timeout: 15000 });
    await store.waitForFunction(() => Array.isArray(window.photoNames), null, { timeout: 20000 });
    assert.deepEqual(await store.evaluate(() => window.photoNames), ['big-ai-phone.jpg.png'], 'only the new phone photo (its AI version) went to the page');
    assert.deepEqual(calls.aiPhotos.slice(aiBefore), ['phone.jpg'], 'only the new photo was AI photoshopped');
    await panel.click('#settingsBtn');
    await panel.click('label.sw:has(#autoSendPhotos)');
    await panel.click('label.sw:has(#autoAiPhotos)');
    await panel.click('#togglesBtn');
    await panel.click('#settingsBtn');
    detail.photos = detail.photos.filter(p => p.name !== 'phone.jpg');
    await panel.click('#photoRefresh');
    await panel.waitForFunction(() => !document.querySelector('.photo[data-url$="phone.jpg"]'));

    // Walking away from a half-filled form keeps it: the queue offers the way back, and one click
    // puts the same tab back on the form with the item picked again.
    const formUrl = store.url();
    assert.ok(await panel.$eval('#sessionCard', el => el.hidden), 'nothing to come back to while we are on the form');
    await store.goto('https://www.ebay.com/mye/myebay/summary');
    await panel.waitForFunction(() => !document.getElementById('sessionCard').hidden, null, { timeout: 20000 });
    assert.ok(await panel.$eval('#crumbLock', el => el.hidden), 'off the form, so the lock is released');
    const resumeRow = await panel.textContent('#sessionCard');
    assert.ok(resumeRow.includes('Lenox') && resumeRow.includes('eBay'), 'the unfinished listing says which item and which store: ' + resumeRow);
    await panel.click('#sessionCard [data-resume]');
    await store.waitForURL(formUrl.split('#')[0], { timeout: 20000 });
    await panel.waitForFunction(() => document.getElementById('sessionCard').hidden, null, { timeout: 20000 });
    assert.equal(await panel.$eval('#crumbNow', el => el.textContent), 'Lenox Butterfly Meadow Dinner Plate (unit 1)', 'back on the item we left');
    // The form is filled and guided again, as it would be on any freshly opened one.
    await store.waitForSelector('#ss-lister-guide', { timeout: 30000 });
    await panel.waitForFunction(() => !document.getElementById('crumbLock').hidden, null, { timeout: 20000 });

    // Fill the last open row by hand and pretend the counter moved: the ready button shows and jumps to List it.
    await store.evaluate(() => { const c = document.getElementById('color'); c.value = 'White'; c.dispatchEvent(new Event('input', { bubbles: true })); document.querySelector('h2 + p').textContent = '1/25'; });
    await store.waitForFunction(() => document.querySelector('#ss-lister-guide [data-ss="ready"]').style.display !== 'none', null, { timeout: 15000 });
    await store.click('#ss-lister-guide [data-ss="ready"]');
    await store.waitForFunction(() => document.activeElement && document.activeElement.textContent.trim() === 'List it', null, { timeout: 10000 });
    // eBay's success page carries the item number: the listing is recorded without a click.
    await store.click('a[href*="/sl/list/success"]');
    await panel.waitForFunction(() => document.getElementById('toast').textContent.includes('recorded'), null, { timeout: 20000 });
    assert.equal(calls.links.length, 1);
    assert.equal(calls.links[0].platform, 'ebay');
    assert.equal(calls.links[0].listing_id, '335566778899');
    assert.equal(calls.links[0].upc, UPC + '-1');
    assert.equal(calls.links[0].sku, UPC + '-1');
    assert.equal(calls.links[0].proposal_id, 7);
    assert.ok(calls.links[0].note.includes('auto-detected'));
    assert.equal(await panel.$eval('#toastAction', el => el.textContent), 'Undo');
    // Listed on eBay: the item leaves the eBay list, stays on the Amazon list, and shows in the Listed side bar.
    await panel.waitForFunction(() => !document.querySelector('#itemList .item[data-upc="883049370897-1"]'), null, { timeout: 15000 });
    await panel.waitForFunction(() => document.getElementById('countListedToday').textContent === '1', null, { timeout: 15000 });
    await panel.click('#listedBtn');
    await panel.waitForSelector('#listedDrawer:not([hidden]) .litem');
    assert.deepEqual(await panel.$$eval('#listedList .litem', els => els.map(e => e.dataset.link)), ['1'], 'Today shows only today\'s listing');
    assert.ok((await panel.textContent('#listedList')).includes('item 335566778899'));
    await panel.click('#listedDrawer [data-range="all"]');
    assert.deepEqual(await panel.$$eval('#listedList .litem', els => els.map(e => e.dataset.link)), ['1', '0'], 'All shows every listing');
    await panel.click('#listedDrawer [data-store="amazon"]');
    assert.deepEqual(await panel.$$eval('#listedList .litem', els => els.map(e => e.dataset.link)), ['0'], 'the store filter keeps Amazon only');
    await panel.click('#listedDrawer [data-store="all"]');
    await panel.click('#listedDrawer [data-range="today"]');
    await panel.click('#listedClose');
    assert.ok(await panel.$eval('#listedDrawer', el => el.hidden));
    assert.ok(await panel.$eval('#crumbLock', el => el.hidden), 'the lock is released after the listing is recorded');

    // Back on the search page nothing is searched until an item is clicked in the queue.
    await store.goto('https://www.ebay.com/sl/prelist/suggest?sr=wn');
    await panel.click('#storeAmazon');
    await toQueue();
    await panel.waitForSelector('#itemList .item[data-upc="883049370897-1"]', { timeout: 15000 });
    await panel.click('#storeEbay');
    await panel.waitForFunction(() => document.querySelector('#itemList .item') && !document.querySelector('#itemList .item[data-upc="883049370897-1"]'), null, { timeout: 15000 });
    await panel.waitForFunction(() => document.body.dataset.pageKind === 'listing-start', null, { timeout: 15000 });
    await store.waitForTimeout(1500);
    assert.ok(store.url().includes('/sl/prelist/suggest'), 'the next queued item is not searched by itself');
    await toQueue();
    await toQueue();
    await panel.click('.item[data-upc="012345678905"]');
    await store.waitForURL(/\/sl\/prelist\/identify\?sr=sug&title=012345678905/, { timeout: 20000 });
    // The row's play button starts it: the store tab goes back to eBay's start page and searches that UPC.
    await store.goto('https://www.ebay.com/sl/prelist/suggest?sr=wn');
    await toQueue();
    await panel.click('.item[data-upc="012345678905"] button[data-start]');
    await store.waitForURL(/\/sl\/prelist\/identify\?sr=sug&title=012345678905/, { timeout: 20000 });
    // Amazon works the same: the row's play button opens Seller Central's product search (/abis/listing/syh
    // resumes a draft and errors on /interactive/listing/workflow/offer), types the UPC and presses Next.
    await store.goto('https://sellercentral.amazon.com/inventory');
    await panel.click('#storeAmazon');
    await toQueue();
    await panel.waitForSelector('#itemList .item[data-upc="012345678905"]', { timeout: 15000 });
    await panel.waitForTimeout(600);
    await toQueue();
    await panel.click('.item[data-upc="012345678905"] button[data-start]');
    await store.waitForURL(/\/listing\/results\?q=012345678905/, { timeout: 20000 });
    assert.ok(calls.amazonPages.includes('/product-search'), 'opened the product search: ' + calls.amazonPages);
    assert.ok(!calls.amazonPages.some(p => p.startsWith('/abis/listing/syh')), 'never the draft URL');
    // Already on that page: the play button searches right there, no reload.
    await store.goto('https://sellercentral.amazon.com/product-search');
    const opened = calls.amazonPages.length;
    await panel.waitForTimeout(1500);
    await toQueue();
    await panel.click('.item[data-upc="883049370897-1"] button[data-start]');
    await store.waitForURL(/\/listing\/results\?q=883049370897$/, { timeout: 20000 });
    assert.deepEqual(calls.amazonPages.slice(opened), ['/listing/results?q=883049370897'], 'searched in place: ' + calls.amazonPages.slice(opened));

    // The Add offer page: we always ship it ourselves, the quantity box that answer reveals gets
    // what the rack holds, and the price comes from Amazon's own "Match lowest price" link.
    await store.goto('https://sellercentral.amazon.com/abis/listing/syh?asin=B0LENOX&sku=883049370897-1');
    await store.waitForFunction(() => document.getElementById('mfn') && document.getElementById('mfn').checked, null, { timeout: 25000 });
    assert.ok(!(await store.$eval('#fba', el => el.checked)), 'the FBA radio is never the one picked');
    await store.waitForFunction(() => document.getElementById('qty') && document.getElementById('qty').value === '1', null, { timeout: 25000 });
    await store.waitForFunction(() => document.getElementById('price') && document.getElementById('price').value === '29.40', null, { timeout: 25000 });
    assert.equal(await store.$eval('#cond', el => el.value), 'Used - Good', 'the condition select gets our condition');
    // Quantity, then price, then the condition - and a value we put in is a violet "check it"
    // checkpoint until the user lands on it, not a silent green.
    await store.waitForFunction(() => document.querySelectorAll('#ss-lister-guide [data-ss-cp]').length >= 3, null, { timeout: 25000 });
    if (!(await store.$('#ss-lister-guide [data-ss-row]'))) await store.click('#ss-lister-guide [data-ss="steps"]');
    await store.waitForSelector('#ss-lister-guide [data-ss-row]');
    const offerRows = await store.$$eval('#ss-lister-guide [data-ss-row]', els => els.map(row => ({ text: row.textContent.replace(/\s+/g, ' ').trim(), dot: row.querySelector('span').style.background })));
    const offerNames = offerRows.map(r => r.text.split(' ')[0]).join(',');
    assert.ok(offerRows[0].text.startsWith('Quantity') && offerRows[1].text.startsWith('Price'), 'quantity then price lead the Amazon checklist: ' + offerNames);
    // Seller Central calls it "Item Condition", so the row wears the page's own words.
    assert.ok(/condition/i.test(offerRows[2].text), 'the condition comes third: ' + offerNames);
    assert.ok(offerRows[1].dot.includes('124, 58, 237'), 'the price we filled still wants a look (violet): ' + JSON.stringify(offerRows[1]));
    assert.ok(!offerRows.some(r => r.text.startsWith('Photos') && r.dot.includes('220, 38, 38')), 'photos are not a red must on a catalogue listing: ' + JSON.stringify(offerRows));
    await store.click('#ss-lister-guide [data-ss-cp="1"]');
    await store.waitForFunction(() => {
      const dots = document.querySelectorAll('#ss-lister-guide [data-ss-cp] span');
      return dots[1] && dots[1].style.background.includes('22, 163, 74');
    }, null, { timeout: 15000 });
    console.log('lister browser test passed');
  } finally {
    await context.close();
    fs.rmSync(userDataDir, { recursive: true, force: true });
  }
})().catch(error => { console.error(error); process.exit(1); });
