// Sweet Shelves Lister in a real browser: load the unpacked extension, serve a fake Sweet Shelves
// API and an eBay-like listing form through request interception, then fill the form from the
// side panel and confirm the link. Uses the system Edge like the other Playwright tests.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { chromium } = require('playwright');

const extensionPath = fs.existsSync(path.join(__dirname, 'lister-extension')) ? path.join(__dirname, 'lister-extension')
  : path.join(__dirname, '..', 'lister-extension');

const proposal = {
  id: 7, upc: '883049370897', status: 'proposed', selected: true, selectedAt: '2026-09-13T10:00:00', platformHint: 'ebay',
  updatedAt: '2026-09-13T10:00:00', flags: [{ level: 'warn', code: 'qty', message: 'Quantity mismatch: listing 2 of 5' }],
  fields: {
    upc: '883049370897', sku: 'SS-PLATE-7', title: 'Lenox Butterfly Meadow Dinner Plate 10.75 in Porcelain', price: 24.5, currency: 'USD',
    quantity: 2, condition: 'NEW_OTHER', amazonCondition: 'new_open_box', conditionDescription: 'Open box, never used',
    descriptionHtml: '<p>Lenox <b>Butterfly Meadow</b> dinner plate.</p>', descriptionText: 'Lenox Butterfly Meadow dinner plate.',
    categoryId: '36027', categoryPath: 'Home & Garden > Dinnerware', brand: 'Lenox', aspects: { Brand: ['Lenox'], Material: ['Porcelain'] },
    images: [], lots: ['L-1'], racks: ['A-3'], listableQuantity: 2,
  },
  thumb: '', existing: { ebay: [], amazon: [] }, links: [], listedListingId: '',
};

const ebayForm = `<!doctype html><title>Create your listing | eBay</title>
<h1>Create your listing</h1>
<label for="title">Title</label><input id="title" maxlength="80">
<label for="subtitle">Subtitle</label><input id="subtitle" maxlength="55">
<label for="upc">UPC</label><input id="upc" name="upc">
<label for="cl">Custom label (SKU)</label><input id="cl" name="customLabel">
<label for="cond">Condition</label><select id="cond"><option>Select</option><option>New</option><option>New (Other)</option><option>Used</option></select>
<label for="cd">Condition description</label><textarea id="cd" name="conditionDescription"></textarea>
<label for="brand">Brand</label><input id="brand" role="combobox">
<label for="material">Material</label><input id="material" role="combobox">
<label for="price">Buy It Now price</label><input id="price" name="price">
<label for="ship">Shipping cost</label><input id="ship" name="shippingCost">
<label for="qty">Quantity</label><input id="qty" name="quantity" type="number">
<div><span>Description</span><div id="desc" contenteditable="true" aria-label="Item description"></div></div>
<input type="search" placeholder="Search eBay" aria-label="Search for anything">
<script>window.events = []; for (const el of document.querySelectorAll('input,textarea,select,[contenteditable]')) el.addEventListener('input', e => window.events.push(e.target.id));</script>`;

(async () => {
  const userDataDir = fs.mkdtempSync(path.join(os.tmpdir(), 'lister-browser-'));
  const context = await chromium.launchPersistentContext(userDataDir, {
    channel: 'msedge', headless: false,
    args: ['--headless=new', `--disable-extensions-except=${extensionPath}`, `--load-extension=${extensionPath}`, '--no-first-run'],
  });
  const calls = { events: [], links: [], select: [] };
  try {
    // Fake Sweet Shelves server: the extension must send the anti-CSRF header and cookies.
    await context.route('https://pi.nexuscentralhq.org/**', async route => {
      const request = route.request();
      const url = new URL(request.url());
      const json = body => route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(body) });
      if (url.pathname === '/api/lister/ping') return json({ success: true, version: '0.1.0', user: 'dan@example.com' });
      if (url.pathname === '/api/lister/items') return json({ success: true, items: [proposal], counts: { selected: 1, returned: 1 } });
      assert.equal(request.headers()['x-sweet-shelves-lister'], '1', 'mutations carry the extension header');
      if (url.pathname === '/api/lister/events') { calls.events.push(request.postDataJSON()); return json({ success: true }); }
      if (url.pathname === '/api/lister/links') {
        calls.links.push(request.postDataJSON());
        return route.fulfill({ status: 201, contentType: 'application/json', body: JSON.stringify({ success: true, duplicate: false, link: { id: 1, upc: proposal.upc, platform: 'ebay', listing_id: '335566778899', effects: { steps: ['listing queue marked listed on ebay', 'warehouse match -> A-3 (883049370897)'] } } }) });
      }
      if (url.pathname === '/api/lister/select') { calls.select.push(request.postDataJSON()); return json({ success: true, total_selected: 1 }); }
      return route.fulfill({ status: 404, contentType: 'application/json', body: '{"success":false,"error":"nope"}' });
    });
    await context.route('https://www.ebay.com/**', route => {
      const url = new URL(route.request().url());
      if (url.pathname.startsWith('/sl/')) return route.fulfill({ status: 200, contentType: 'text/html', body: ebayForm });
      return route.fulfill({ status: 200, contentType: 'text/html', body: '<title>eBay listing</title><h1>Your item is listed</h1>' });
    });

    let [worker] = context.serviceWorkers();
    if (!worker) worker = await context.waitForEvent('serviceworker');
    const extensionId = new URL(worker.url()).host;

    const panel = await context.newPage();
    await panel.goto(`chrome-extension://${extensionId}/sidepanel.html`);
    await panel.waitForFunction(() => document.getElementById('connStatus').textContent.trim() === 'dan');
    await panel.waitForSelector('.item.current');
    assert.equal(await panel.textContent('.item .title'), proposal.fields.title);
    assert.ok((await panel.textContent('#pageCard')).includes('no store page'));
    assert.equal(await panel.$eval('#fillBtn', b => b.disabled), true, 'fill is disabled off store pages');

    // Open the eBay listing form in another tab; the panel follows the active tab and auto-fills.
    const store = await context.newPage();
    await store.goto('https://www.ebay.com/sl/list?mode=AddItem');
    await store.bringToFront();
    await panel.waitForFunction(() => document.getElementById('pageCard').textContent.includes('listing form'), null, { timeout: 15000 });
    await store.waitForFunction(() => document.getElementById('title').value.length > 0, null, { timeout: 15000 });
    const filled = await store.evaluate(() => ({
      title: document.getElementById('title').value, subtitle: document.getElementById('subtitle').value,
      upc: document.getElementById('upc').value, sku: document.getElementById('cl').value,
      condition: document.getElementById('cond').value, cd: document.getElementById('cd').value,
      brand: document.getElementById('brand').value, material: document.getElementById('material').value,
      price: document.getElementById('price').value, ship: document.getElementById('ship').value,
      qty: document.getElementById('qty').value, desc: document.getElementById('desc').innerHTML,
      search: document.querySelector('input[type=search]').value, events: window.events,
    }));
    assert.equal(filled.title, proposal.fields.title);
    assert.equal(filled.subtitle, '', 'subtitle stays empty');
    assert.equal(filled.upc, '883049370897');
    assert.equal(filled.sku, 'SS-PLATE-7');
    assert.equal(filled.condition, 'New (Other)');
    assert.equal(filled.cd, 'Open box, never used');
    assert.equal(filled.brand, 'Lenox');
    assert.equal(filled.material, 'Porcelain');
    assert.equal(filled.price, '24.5');
    assert.equal(filled.ship, '', 'shipping cost is not the price');
    assert.equal(filled.qty, '2');
    assert.ok(filled.desc.includes('Butterfly Meadow'), 'description editor received the HTML');
    assert.equal(filled.search, '', 'the site search box is untouched');
    assert.ok(filled.events.includes('title') && filled.events.includes('price'), 'React-style input events fired');
    await panel.waitForFunction(() => document.querySelectorAll('.report .ok').length >= 8);
    assert.ok(calls.events.length >= 1 && calls.events[0].event === 'helper_filled', 'fill reported to the proposal timeline');
    assert.deepEqual(calls.events[0].payload.filled.sort(), ['brand', 'condition', 'conditionDescription', 'description', 'price', 'quantity', 'sku', 'title', 'upc']);

    // The live listing page yields the item number, and confirming posts the link.
    await store.goto('https://www.ebay.com/itm/Lenox-Plate/335566778899');
    await panel.waitForFunction(() => (document.getElementById('cfListingId') || {}).value === '335566778899', null, { timeout: 15000 });
    assert.ok((await panel.textContent('#pageCard')).includes('live listing'));
    await panel.click('#confirmBtn');
    await panel.waitForFunction(() => document.getElementById('confirm').textContent.includes('Recorded.'), null, { timeout: 15000 });
    assert.equal(calls.links.length, 1);
    assert.equal(calls.links[0].platform, 'ebay');
    assert.equal(calls.links[0].listing_id, '335566778899');
    assert.equal(calls.links[0].proposal_id, 7);
    assert.equal(calls.links[0].url, 'https://www.ebay.com/itm/Lenox-Plate/335566778899');
    assert.equal(calls.links[0].sku, 'SS-PLATE-7');
    assert.ok((await panel.textContent('#confirm')).includes('warehouse match -> A-3'));
    console.log('lister browser test passed');
  } finally {
    await context.close();
    fs.rmSync(userDataDir, { recursive: true, force: true });
  }
})().catch(error => { console.error(error); process.exit(1); });
