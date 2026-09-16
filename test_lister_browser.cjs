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
    { id: 11, upc: UPC + '-1', baseUpc: UPC, suffixed: true, title: 'Lenox Butterfly Meadow Dinner Plate (unit 1)', thumb: '', addedAt: '2026-09-14T08:00:00', platform: 'ebay', status: 'queued', listedAt: '', listed: { ebay: false, amazon: false }, skipped: { ebay: false, amazon: false }, otherStatus: 'queued', existing: [], storeUrl: '', alreadyOnStore: false, links: [], proposal: { id: 7, status: 'proposed', ready: true }, preparing: false },
    { id: 12, upc: '012345678905', baseUpc: '012345678905', suffixed: false, title: 'Other item', thumb: '', addedAt: '2026-09-15T08:00:00', platform: 'ebay', status: 'queued', listedAt: '', listed: { ebay: false, amazon: false }, skipped: { ebay: false, amazon: false }, otherStatus: 'listed', existing: [{ listingId: '112233445566', state: 'Active' }], storeUrl: 'https://www.ebay.com/itm/112233445566', alreadyOnStore: true, links: [], proposal: {}, preparing: false },
  ],
  amazon: [],
};
const detail = {
  upc: UPC + '-1', baseUpc: UPC, suffixed: true, title: 'Lenox Butterfly Meadow Dinner Plate 10.75 in Porcelain',
  fields: {
    upc: UPC, sku: UPC + '-1', title: 'Lenox Butterfly Meadow Dinner Plate 10.75 in Porcelain', price: 24.5, currency: 'USD',
    quantity: 1, condition: 'USED_GOOD', amazonCondition: 'used_good', conditionDescription: 'Small chip on the rim',
    descriptionHtml: '<p>Lenox <b>Butterfly Meadow</b> dinner plate.</p>', descriptionText: 'Lenox Butterfly Meadow dinner plate.',
    categoryId: '36027', categoryPath: 'Home & Garden > Dinnerware', brand: 'Lenox', aspects: { Brand: ['Lenox'], Material: ['Porcelain'] },
    images: [], lots: ['L-1'], racks: ['A-3'], listableQuantity: 1, source: 'proposal',
  },
  condition: { condition: 'USED_GOOD', conditionDescription: 'Small chip on the rim', reason: 'prep notes mention a flaw', assumed: false },
  notes: [{ id: 5, text: 'LT: nuotrauka | EN: Small chip on the rim', english: 'Small chip on the rim', createdAt: '2026-09-15T10:00:00' }],
  defect: '', voiceNotes: [], videos: [], photos: [], inventory: { quantity: 1, positions: ['B-1'], rows: [] }, cost: 4.5, bol: {},
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
<a href="https://www.ebay.com/sl/list/success?itemId=335566778899&mode=AddItem">List it</a>
<script>window.events = []; for (const el of document.querySelectorAll('input,textarea,select,[contenteditable]')) el.addEventListener('input', e => window.events.push(e.target.id));
document.getElementById('photos').addEventListener('change', e => { window.photoNames = Array.from(e.target.files).map(f => f.name); });</script>`;

const successPage = `<!doctype html><title>Your item is listed | eBay</title><h1>Congratulations! Your item is listed.</h1>
<p>Item number: 335566778899</p><a href="https://www.ebay.com/itm/335566778899">View listing</a>`;

(async () => {
  const userDataDir = fs.mkdtempSync(path.join(os.tmpdir(), 'lister-browser-'));
  const context = await chromium.launchPersistentContext(userDataDir, {
    channel: 'msedge', headless: false,
    args: ['--headless=new', `--disable-extensions-except=${extensionPath}`, `--load-extension=${extensionPath}`, '--no-first-run'],
  });
  const calls = { events: [], links: [], skip: [], prepare: [], learn: [], detail: 0 };
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
        const items = (queue[platform] || []).map(it => linked && it.upc === UPC + '-1' ? { ...it, status: 'listed', listedAt: '2026-09-16T10:00:00', links: [{ platform: 'ebay', listing_id: '335566778899' }] } : it);
        return json({ success: true, items, counts: { queued: items.filter(i => i.status === 'queued').length, listed: items.filter(i => i.status !== 'queued').length, hidden: 0 } });
      }
      if (url.pathname === `/api/lister/queue/${UPC}-1`) { calls.detail += 1; return json({ success: true, item: linked ? { ...detail, links: [{ platform: 'ebay', listing_id: '335566778899', url: 'https://www.ebay.com/itm/335566778899' }] } : detail }); }
      if (url.pathname === '/api/lister/queue/012345678905') return json({ success: true, item: { ...detail, upc: '012345678905', baseUpc: '012345678905', suffixed: false, proposal: {}, fields: { ...detail.fields, source: 'inventory', sku: '012345678905', upc: '012345678905' } } });
      if (url.pathname === '/api/lister/photos/fetch') return json({ success: true, name: 'own.jpg', mime: 'image/jpeg', base64: '/9j/4AAQ' });
      if (url.pathname.startsWith('/static/')) return route.fulfill({ status: 200, contentType: 'image/gif', body: Buffer.from('R0lGODlhAQABAAAAACw=', 'base64') });
      assert.equal(request.headers()['x-sweet-shelves-lister'], '1', 'mutations carry the extension header: ' + url.pathname);
      if (url.pathname.endsWith('/prepare')) { calls.prepare.push(url.pathname); return json({ success: true, status: 'ready', proposalId: 7 }); }
      if (url.pathname.endsWith('/skip')) { calls.skip.push(request.postDataJSON()); return json({ success: true, queue: 'queued' }); }
      if (url.pathname === '/api/lister/learn') { calls.learn.push(request.postDataJSON()); return json({ success: true, agreed: false, learned: { ebay: {} } }, 201); }
      if (url.pathname === '/api/lister/events') { calls.events.push(request.postDataJSON()); return json({ success: true }); }
      if (url.pathname === '/api/lister/links') {
        calls.links.push(request.postDataJSON());
        linked = true;
        return json({ success: true, duplicate: false, link: { id: 1, upc: UPC + '-1', platform: 'ebay', listing_id: '335566778899', effects: { steps: ['listing queue marked listed on ebay', 'Items to List marked listed on ebay', 'warehouse match -> B-1 (883049370897-1)'] } } }, 201);
      }
      return json({ success: false, error: 'nope' }, 404);
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
    await panel.waitForFunction(() => document.getElementById('connStatus').textContent.trim() === 'dan');
    await panel.waitForSelector('.item.current');
    // The oldest queued item is picked automatically and shown first.
    assert.equal(await panel.$eval('.item.current', el => el.dataset.upc), UPC + '-1');
    assert.ok((await panel.textContent('#countEbay')).includes('2'));
    assert.ok((await panel.textContent('.item[data-upc="012345678905"]')).includes('on eBay'), 'a UPC the store already carries says so');
    assert.ok((await panel.textContent('#pageCard')).includes('no store page'));

    // The X asks first, then tells the server which store list to leave.
    await panel.click('.item[data-upc="012345678905"] button[data-skip]');
    await panel.waitForSelector('#modalOk');
    assert.ok((await panel.textContent('#modal')).includes('Amazon'), 'the confirmation explains the other store');
    await panel.click('#modalCancel');
    assert.equal(calls.skip.length, 0, 'cancel removes nothing');
    await panel.click('.item[data-upc="012345678905"] button[data-skip]');
    await panel.waitForSelector('#modalOk');
    await panel.click('#modalOk');
    await panel.waitForFunction(() => document.getElementById('toast').textContent.includes('Removed'));
    assert.deepEqual(calls.skip[0].platform, 'ebay');
    assert.equal(await panel.$eval('.item.current', el => el.dataset.upc), UPC + '-1', 'the current item is untouched');

    // Item view shows the prep note, the prepared values and the suffixed SKU.
    await panel.click('#viewItem');
    await panel.waitForSelector('#fTitle');
    assert.equal(await panel.$eval('#fSku', el => el.value), UPC + '-1');
    assert.equal(await panel.$eval('#fUpc', el => el.value), UPC);
    assert.ok((await panel.textContent('#detail')).includes('Small chip on the rim'));
    assert.ok((await panel.textContent('#detail')).includes('unit 1'));

    // On eBay's prelist page the panel types the UPC and submits the search by itself.
    // (Opened from Playwright rather than the Start button: a tab the extension opens starts
    // loading before request interception attaches, so it would reach the real eBay.)
    const store = await context.newPage();
    await store.goto('https://www.ebay.com/sl/prelist/suggest?sr=wn');
    await store.bringToFront();
    await store.waitForURL(/\/sl\/prelist\/identify\?sr=sug&title=883049370897/, { timeout: 20000 });
    assert.ok(!store.url().includes('%2D1'), 'the store search uses the catalog UPC without the -suffix');

    // "Find a match": the panel highlights the listing that looks like ours; the user's click is learned,
    // and the other seller's item id in the next URL must never be recorded as our listing.
    await panel.waitForFunction(() => document.getElementById('pageCard').textContent.includes('find a catalog match'), null, { timeout: 15000 });
    await store.waitForFunction(() => Array.from(document.querySelectorAll('li')).some(li => li.style.outline.includes('rgb(10, 156, 108)')), null, { timeout: 15000 });
    const picked = await store.evaluate(() => Array.from(document.querySelectorAll('li')).find(li => li.style.outline.includes('rgb(10, 156, 108)')).textContent.trim());
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
    await panel.waitForFunction(() => document.getElementById('pageCard').textContent.includes('confirm details'), null, { timeout: 15000 });
    await store.waitForFunction(() => document.querySelector('input[value="used"]').checked, null, { timeout: 15000 });
    assert.equal(calls.links.length, 0, 'the SellLikeItem item id is another seller\'s listing, not ours');
    assert.ok(!(await panel.textContent('#pageCard')).includes('listing confirmed'));
    await store.click('a[href*="/sl/list?mode=AddItem"]');

    // The listing form is filled from the prepared values, then the guide points at what is left.
    await panel.waitForFunction(() => document.getElementById('pageCard').textContent.includes('listing form'), null, { timeout: 20000 });
    await store.waitForFunction(() => document.getElementById('title').value.length > 0, null, { timeout: 20000 });
    const filled = await store.evaluate(() => ({
      title: document.getElementById('title').value, subtitle: document.getElementById('subtitle').value,
      upc: document.getElementById('upc').value, sku: document.getElementById('cl').value,
      condition: document.getElementById('cond').value, cd: document.getElementById('cd').value,
      brand: document.getElementById('brand').value, material: document.getElementById('material').value,
      price: document.getElementById('price').value, ship: document.getElementById('ship').value,
      qty: document.getElementById('qty').value, desc: document.getElementById('desc').innerHTML,
      search: document.querySelector('input[type=search]').value, events: window.events,
      colorOutline: document.getElementById('color').style.outline,
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
    assert.ok(filled.colorOutline.includes('rgb(220, 38, 38)'), 'the empty required Color field is outlined red by the guide, got: ' + filled.colorOutline);
    await panel.waitForFunction(() => document.querySelectorAll('.needs li').length >= 1, null, { timeout: 15000 });
    const rows = await panel.$$eval('.needs li', els => els.map(li => ({ label: li.children[1].textContent, cls: li.className })));
    const photosRow = rows.find(r => r.label === 'Photos');
    assert.ok(photosRow && photosRow.cls.includes('req'), 'photos (0/25) are a required, open row: ' + JSON.stringify(rows));
    assert.ok(rows.find(r => r.label.startsWith('Color'))?.cls.includes('req'), 'the empty required Color field is red: ' + JSON.stringify(rows));
    assert.ok(rows.find(r => r.label === 'Title')?.cls.includes('done'), 'the filled title is green');
    assert.ok(rows.find(r => r.label === 'Price')?.cls.includes('done'), 'the filled price is green');
    assert.ok(await store.$eval('#ss-lister-guide', el => el.textContent.includes('required')), 'the overlay on the page lists what is required');
    // The item is locked in on the listing page: the queue is hidden and the item view is up.
    assert.ok(await panel.$eval('#lock', el => el.classList.contains('on')));
    assert.ok(await panel.$eval('#listCard', el => el.hidden));
    assert.ok(!(await panel.$eval('#detail', el => el.hidden)));
    assert.ok(calls.events.length >= 1 && calls.events[0].event === 'helper_filled' && calls.events[0].proposal_id === 7, 'fill reported to the proposal timeline');

    // Photos reach the page's uploader through the file input (the item view is already up: locked).
    detail.photos.push({ url: 'https://pi.nexuscentralhq.org/static/listingagent_uploads/own.jpg', source: 'listing', id: 1, name: 'own.jpg' });
    await panel.click('#photoRefresh');
    await panel.waitForSelector('.photo');
    await panel.click('#sendPhotos');
    await store.waitForFunction(() => Array.isArray(window.photoNames), null, { timeout: 15000 });
    assert.deepEqual(await store.evaluate(() => window.photoNames), ['own.jpg']);

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
    await panel.waitForFunction(() => document.querySelector('.item[data-upc="883049370897-1"]').classList.contains('listed'), null, { timeout: 15000 });
    console.log('lister browser test passed');
  } finally {
    await context.close();
    fs.rmSync(userDataDir, { recursive: true, force: true });
  }
})().catch(error => { console.error(error); process.exit(1); });
