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

const successPage = `<!doctype html><title>Your item is listed | eBay</title><h1>Congratulations! Your item is listed.</h1>
<p>Item number: 335566778899</p><a href="https://www.ebay.com/itm/335566778899">View listing</a>`;

(async () => {
  const userDataDir = fs.mkdtempSync(path.join(os.tmpdir(), 'lister-browser-'));
  const context = await chromium.launchPersistentContext(userDataDir, {
    channel: 'msedge', headless: false,
    args: ['--headless=new', `--disable-extensions-except=${extensionPath}`, `--load-extension=${extensionPath}`, '--no-first-run'],
  });
  const calls = { events: [], links: [], skip: [], prepare: [], learn: [], generate: [], detail: 0, preload: [] };
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
      if (url.pathname === '/api/lister/photos/fetch') return json({ success: true, name: 'own.jpg', mime: 'image/jpeg', base64: '/9j/4AAQ' });
      if (url.pathname.includes('/tiny-')) return route.fulfill({ status: 200, contentType: 'image/svg+xml', body: '<svg xmlns="http://www.w3.org/2000/svg" width="120" height="90"><rect width="120" height="90"/></svg>' });
      if (url.pathname.includes('/big-')) return route.fulfill({ status: 200, contentType: 'image/svg+xml', body: '<svg xmlns="http://www.w3.org/2000/svg" width="800" height="800"><rect width="800" height="800"/></svg>' });
      if (url.pathname.startsWith('/static/')) return route.fulfill({ status: 200, contentType: 'image/gif', body: Buffer.from('R0lGODlhAQABAAAAACw=', 'base64') });
      if (url.pathname === '/api/lister/preload' && request.method() === 'GET') { if (preload.running) preload.polls += 1; return json(preloadAll()); }
      if (url.pathname.endsWith('/preload') && request.method() === 'GET') return json({ success: true, preload: preloadItems()[decodeURIComponent(url.pathname.split('/')[4])] || null });
      if (url.pathname === '/api/lister/qr') return route.fulfill({ status: 200, contentType: 'image/svg+xml', body: '<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10"><rect width="10" height="10"/></svg>' });
      assert.equal(request.headers()['x-sweet-shelves-lister'], '1', 'mutations carry the extension header: ' + url.pathname);
      if (url.pathname === '/api/lister/preload') { const body = request.postDataJSON(); calls.preload.push(body); Object.assign(preload, { running: true, upcs: body.upcs, steps: body.steps, polls: 0 }); return json(preloadAll()); }
      if (url.pathname.endsWith('/preload')) { calls.preload.push(request.postDataJSON()); return json({ success: true, preload: { running: false, steps: { prepare: 'done' }, photos: null, startedAt: 'x', finishedAt: 'y' } }); }
      if (url.pathname.endsWith('/prepare')) { calls.prepare.push(url.pathname); return json({ success: true, status: 'ready', proposalId: 7 }); }
      if (url.pathname.endsWith('/skip')) { calls.skip.push(request.postDataJSON()); return json({ success: true, queue: 'queued' }); }
      if (url.pathname === '/api/lister/learn') { calls.learn.push(request.postDataJSON()); return json({ success: true, agreed: false, learned: { ebay: {} } }, 201); }
      if (url.pathname.endsWith('/amazon-check')) {
        const restricted = url.pathname.includes('012345678905');
        return json({ success: true, check: restricted ? { status: 'restricted', asin: 'B0OTHER', brand: 'Nike', reasons: ['Approval required for Nike'], error: '', cached: false } : { status: 'listable', asin: 'B0LENOX', brand: 'Lenox', reasons: [], error: '', cached: false } });
      }
      if (url.pathname.endsWith('/generate')) { const body = request.postDataJSON(); calls.generate.push(body); return json(body.kind === 'title' ? { success: true, kind: 'title', title: 'AI Lenox Butterfly Meadow Plate' } : { success: true, kind: 'description', descriptionHtml: '<p>AI description</p>', descriptionText: 'AI description' }); }
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
    // Suffixed rows highlight the suffix and carry the prep verdict instead of "unit 1".
    const firstRow = await panel.textContent('.item[data-upc="883049370897-1"]');
    assert.ok(!(await panel.$$eval('.item[data-upc="883049370897-1"] .chip', els => els.some(e => /^unit \d/.test(e.textContent.trim())))), 'no "unit 1" chip');
    assert.equal(await panel.$eval('.item[data-upc="883049370897-1"] .suffix', el => el.textContent), '1');
    assert.ok(firstRow.includes('bad · chip'), 'the Item Prep status and reason are on the row: ' + firstRow);
    assert.equal(await panel.$eval('.item[data-upc="883049370897-1"] .chip.defect', el => el.textContent), 'Missing pieces', 'the BOL defect is its own bubble');
    // Every queued item is checked against Amazon in the background; the verdict shows on the Amazon list only.
    await panel.waitForFunction(() => document.getElementById('busy').hidden, null, { timeout: 15000 });
    assert.ok(!(await panel.$('.item.restricted')), 'the eBay list does not carry Amazon verdicts');
    await panel.click('#storeAmazon');
    await panel.waitForFunction(() => document.querySelector('.item[data-upc="012345678905"]')?.classList.contains('restricted'), null, { timeout: 15000 });
    assert.ok((await panel.textContent('.item[data-upc="012345678905"]')).includes('Amazon ✕ restricted'));
    assert.ok((await panel.textContent('.item[data-upc="883049370897-1"]')).includes('Amazon ✓'));
    await panel.click('#storeEbay');
    await panel.waitForFunction(() => document.getElementById('countEbay').textContent.includes('2') && document.querySelector('.item.current')?.dataset.upc === '883049370897-1', null, { timeout: 15000 });
    await panel.waitForFunction(() => document.getElementById('busy').hidden, null, { timeout: 15000 });
    // Note marker, store-coloured badge, and the status / listed filters.
    assert.ok(firstRow.includes('📝 1') && firstRow.includes('🎤 1'), 'the row shows its note counts: ' + firstRow);
    assert.ok(await panel.$('.item[data-upc="012345678905"] .chip.store.ebay'), 'a UPC the store carries gets an eBay-coloured badge');
    await panel.click('#statusBad');
    assert.deepEqual(await panel.$$eval('.item', els => els.map(e => e.dataset.upc)), ['883049370897-1'], 'Bad shows only the bad unit');
    await panel.click('#statusListed');
    assert.deepEqual(await panel.$$eval('.item', els => els.map(e => e.dataset.upc)), ['012345678905'], 'Listed shows what a store already carries');
    await panel.click('#statusAll');
    assert.equal((await panel.$$('.item')).length, 2);
    assert.ok(await panel.$('#autoSendPhotos'), 'the auto send-to-page switch is on the store card');
    // Preload all: the whole queue is prepared in the background; the bar shows the percentage, the rows their state.
    assert.ok(await panel.$eval('#preloadBar', el => el.hidden), 'no preload bar before a preload');
    await panel.click('#preloadAll');
    await panel.waitForFunction(() => !document.getElementById('preloadBar').hidden, null, { timeout: 5000 });
    assert.deepEqual(calls.preload.at(-1), { upcs: ['883049370897-1', '012345678905'], steps: ['prepare'] }, 'the queued units with the switched-on steps (values only: no AI switch is on)');
    assert.ok((await panel.textContent('#preloadBar')).includes('Preloading'), 'the bar says it is preloading');
    assert.ok(await panel.$eval('#preloadAll', el => el.disabled), 'the button waits while it runs');
    await panel.waitForFunction(() => document.querySelector('#preloadBar').textContent.includes('100%'), null, { timeout: 15000 });
    assert.ok((await panel.textContent('#preloadBar')).includes('All 2 preloaded'), await panel.textContent('#preloadBar'));
    assert.ok(await panel.$('.item[data-upc="883049370897-1"] .chip.preload.done'), 'each row gets its ✓ when it is done');
    assert.ok(await panel.$('.item[data-upc="012345678905"] .chip.preload.done'));
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
    assert.ok((await panel.textContent('#pageCard')).includes('no store page'));
    // No Start button (double-clicking a queue item starts it, checked at the end). The automatic switches fold into one line.
    assert.ok(!(await panel.$('#startBtn')), 'the Start button is gone');
    assert.ok(await panel.$eval('.toggles-body', el => el.hidden), 'the automatic switches start minimized');
    const togglesHead = await panel.textContent('#togglesBtn');
    assert.ok(togglesHead.includes('Automatic') && togglesHead.includes('all off'), togglesHead);
    await panel.click('#togglesBtn');
    await panel.waitForFunction(() => !document.querySelector('.toggles-body').hidden);
    assert.deepEqual(await panel.$$eval('.toggles.auto-top .tg-title', els => els.map(e => e.textContent)), ['Listing text', 'Photos'], 'the menu is grouped');
    assert.ok(await panel.$eval('#autoSendAiOnly', el => el.checked), '"by default only send AI generated" starts on');
    await panel.click('label.sw:has(#autoSendPhotos)');
    await panel.waitForFunction(() => document.getElementById('togglesBtn').textContent.includes('send photos (AI only)'));
    await panel.click('label.sw:has(#autoSendPhotos)');
    await panel.waitForFunction(() => document.getElementById('togglesBtn').textContent.includes('all off'));
    await panel.click('#togglesBtn');
    await panel.waitForFunction(() => document.querySelector('.toggles-body').hidden);

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

    // Item view: unit, prep status, stock, store status, the condition note flagged as coming from prep, the QR code.
    await panel.click('#viewItem');
    await panel.waitForSelector('.tiles');
    const detailText = await panel.textContent('#detail');
    for (const expected of ['unit 1', 'status: BAD', '1 on the rack', '@ B-1', '1 to list', 'prep 1', 'Small chip on the rim']) {
      assert.ok(!(await panel.textContent('#detail')).includes('On the listing'), 'no "On the listing" label');
      assert.ok(await panel.$('.sticky .note2'), 'notes sit on the yellow sticky');
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
    assert.ok(await panel.$('#photo_autoAiPhotos') && await panel.$('#photo_autoSendPhotos') && await panel.$('#photo_autoSendAiOnly'), 'the photo switches sit with the photos');
    assert.ok(!(await panel.$('#photo_autoAiTitle')), 'the photo menu carries only the photo switches');
    assert.ok(await panel.$eval('.toggles.auto-photos .toggles-body', el => el.hidden), 'the photo switches start minimized');
    await panel.click('#photoTogglesBtn');
    await panel.waitForFunction(() => !document.querySelector('.toggles.auto-photos .toggles-body').hidden);
    await panel.click('label.sw:has(#photo_autoSendAiOnly)');
    await panel.waitForFunction(() => document.getElementById('autoSendAiOnly') && !document.getElementById('autoSendAiOnly').checked && !document.getElementById('photo_autoSendAiOnly').checked);
    await panel.click('label.sw:has(#photo_autoSendAiOnly)');
    await panel.waitForFunction(() => document.getElementById('autoSendAiOnly').checked);
    await panel.click('#photoTogglesBtn');
    await panel.waitForFunction(() => document.querySelector('.toggles.auto-photos .toggles-body').hidden);
    assert.ok(await panel.$('#autoAiTitle') && await panel.$('#autoAiDescription'), 'the automatic AI text switches sit on the store card');
    const tiles = await panel.$$eval('.tiles .tile', els => els.map(t => ({ k: t.querySelector('.k').textContent, v: t.querySelector('.v').textContent, cls: t.className })));
    assert.deepEqual(tiles.map(t => t.k), ['Warehouse stock', 'eBay', 'Amazon']);
    assert.ok(!detailText.includes('prep and rack differ'), 'the mismatch is shown visually, not as a sentence');
    assert.equal(await panel.$eval('.tile.stock .v a', a => a.getAttribute('href')), 'https://pi.nexuscentralhq.org/unified-search?q=883049370897-1', 'the rack count links to the warehouse search');
    assert.equal(await panel.$eval('.tile.stock a.prep', a => a.getAttribute('href')), 'https://pi.nexuscentralhq.org/item-prep?upc=883049370897-1', 'the prep count links to Item Prep');
    assert.ok(tiles[0].cls.includes('ok') && tiles[1].v === 'NOT LISTED' && tiles[1].cls.includes('todo') && tiles[2].v === 'NOT LISTED', JSON.stringify(tiles));
    assert.ok(!(await panel.$('.qr img')), 'the QR code starts minimized');
    await panel.click('#addPhoto');
    await panel.waitForSelector('.qr img');
    await panel.click('#addPhoto');

    // On eBay's prelist page the panel types the UPC and submits the search by itself.
    // (Opened from Playwright rather than the Start button: a tab the extension opens starts
    // loading before request interception attaches, so it would reach the real eBay.)
    const store = await context.newPage();
    await store.goto('https://www.ebay.com/sl/prelist/suggest?sr=wn');
    await store.bringToFront();
    // Nothing happens by itself on the search page; clicking the item in the queue searches its UPC.
    await panel.waitForFunction(() => document.getElementById('pageCard').textContent.includes('search for the product'), null, { timeout: 15000 });
    await store.waitForTimeout(1500);
    assert.ok(store.url().includes('/sl/prelist/suggest'), 'no search without a click');
    await panel.click('#viewList');
    await panel.click('.item[data-upc="883049370897-1"]');
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
    await store.waitForFunction(() => document.getElementById('color').style.outline.includes('rgb(220, 38, 38)'), null, { timeout: 10000 }).catch(() => {});  // the guide starts right after the fill
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
    // The checklist overlay on the page: red = required and empty, green = filled, blue = from the prep notes.
    await store.waitForFunction(() => document.querySelectorAll('#ss-lister-guide [data-ss-row]').length >= 1, null, { timeout: 15000 });
    const rows = await store.$$eval('#ss-lister-guide [data-ss-row]', els => els.map(row => ({ text: row.textContent.replace(/\s+/g, ' ').trim(), dot: row.querySelector('span').style.background })));
    const rowFor = name => rows.find(r => r.text.startsWith(name));
    assert.ok(rowFor('Photos') && rowFor('Photos').dot.includes('220, 38, 38'), 'photos (0/25) are a required, open (red) row: ' + JSON.stringify(rows));
    assert.ok(rowFor('Color').dot.includes('220, 38, 38'), 'the empty required Color field is red');
    assert.ok(rowFor('Title').dot.includes('22, 163, 74'), 'the filled title is green');
    assert.ok(rowFor('Quantity').text.includes('· 1'), 'the quantity row shows the entered quantity: ' + rowFor('Quantity').text);
    assert.ok(!rowFor('UPC'), 'the UPC is not on the checklist');
    assert.ok(rows.at(-1).text.startsWith('Quantity') && rows.at(-2).text.startsWith('Price'), 'price then quantity close the checklist: ' + rows.map(r => r.text.split(' ')[0]).join(','));
    assert.ok(rowFor('Condition description').dot.includes('37, 99, 235') && rowFor('Condition description').text.includes('prep notes'), 'the note-sourced condition description is blue with a disclaimer');
    assert.ok((await panel.textContent('#pageCard')).includes('of'), 'the store card shows the checklist progress');
    // The actions live on the overlay now, not on the store card.
    assert.ok(!(await panel.$('#fillBtn')) && !(await panel.$('#pickBtn')), 'no Fill / Pick buttons on the store card');
    const footButtons = await store.$$eval('#ss-lister-guide [data-ss="foot"] button', els => els.map(b => b.textContent.trim()));
    assert.ok(footButtons.includes('Next (Tab)'), 'overlay footer has Next (Tab): ' + footButtons);
    assert.ok(!footButtons.includes('Fill page'), 'the overlay has no Fill page button: ' + footButtons);
    assert.ok(!footButtons.includes('Pick a field…') && !footButtons.includes('Hide'), 'no Pick a field / Hide on the overlay footer');
    // The AI button beside the Title row writes the title through the panel and puts it on the page.
    await store.click(`#ss-lister-guide [data-ss-row="${rows.findIndex(r => r.text.startsWith('Title'))}"] [data-ss-ai="title"]`);
    await store.waitForFunction(() => document.getElementById('title').value === 'AI Lenox Butterfly Meadow Plate', null, { timeout: 15000 });
    assert.equal(calls.generate[0].kind, 'title');
    assert.ok(calls.generate[0].values.notes.includes('scratched on the back'), 'the voice note text feeds the AI prompt');
    await store.waitForFunction(() => Array.from(document.querySelectorAll('#ss-lister-guide [data-ss-row]')).some(r => r.textContent.includes('generated with AI')), null, { timeout: 10000 });
    const titleRowText = await store.$$eval('#ss-lister-guide [data-ss-row]', els => els.map(r => r.textContent.replace(/\s+/g, ' ').trim()).find(t => t.startsWith('Title')));
    assert.ok(await store.$eval('#ss-lister-guide', el => el.style.left === '16px' && el.style.right === ''), 'the overlay starts on the left');
    assert.ok(titleRowText.includes('generated with AI') && !titleRowText.includes('automatically'), 'a manual AI run is marked, without "(automatically)": ' + titleRowText);
    // Re-reading the page while the guide is up must not throw (0.2.2 did: "reading 'length'").
    await panel.click('#pageRefresh');
    await panel.waitForFunction(() => document.getElementById('pageCard').textContent.includes('listing form'), null, { timeout: 10000 });
    assert.ok(!(await panel.textContent('#pageCard')).includes('Page script:'), await panel.textContent('#pageCard'));
    // Clicking a green (filled) row still jumps to that field; the condition description row goes to its own field.
    const titleIndex = rows.findIndex(r => r.text.startsWith('Title'));
    await store.click(`#ss-lister-guide [data-ss-row="${titleIndex}"]`);
    await store.waitForFunction(() => document.activeElement && document.activeElement.id === 'title', null, { timeout: 10000 });
    const cdIndex = rows.findIndex(r => r.text.startsWith('Condition description'));
    await store.click(`#ss-lister-guide [data-ss-row="${cdIndex}"]`);
    await store.waitForFunction(() => document.activeElement && document.activeElement.id === 'cd', null, { timeout: 10000 });
    assert.equal(await store.evaluate(() => document.activeElement.id), 'cd');
    // The item is locked in on the listing page: the queue is hidden and the item view is up.
    assert.ok(await panel.$eval('#lock', el => el.classList.contains('on')));
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

    // Nothing ticked: by default only AI generated photos go, and never a too-small one.
    await panel.click('#photoSelectAll');  // clears the tick
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
    assert.ok(!(await panel.$eval('#lock', el => el.classList.contains('on'))), 'the lock is released after the listing is recorded');

    // Back on the search page nothing is searched until an item is clicked in the queue.
    await store.goto('https://www.ebay.com/sl/prelist/suggest?sr=wn');
    await panel.click('#storeAmazon');
    await panel.waitForSelector('#itemList .item[data-upc="883049370897-1"]', { timeout: 15000 });
    await panel.click('#storeEbay');
    await panel.waitForFunction(() => document.querySelector('#itemList .item') && !document.querySelector('#itemList .item[data-upc="883049370897-1"]'), null, { timeout: 15000 });
    await panel.waitForFunction(() => document.getElementById('pageCard').textContent.includes('search for the product'), null, { timeout: 15000 });
    await store.waitForTimeout(1500);
    assert.ok(store.url().includes('/sl/prelist/suggest'), 'the next queued item is not searched by itself');
    await panel.click('#viewList');
    await panel.click('.item[data-upc="012345678905"]');
    await store.waitForURL(/\/sl\/prelist\/identify\?sr=sug&title=012345678905/, { timeout: 20000 });
    // A double-click on a queue item starts it: the store tab goes back to eBay's start page and searches that UPC.
    await store.goto('https://www.ebay.com/sl/prelist/suggest?sr=wn');
    await panel.dblclick('.item[data-upc="012345678905"]');
    await store.waitForURL(/\/sl\/prelist\/identify\?sr=sug&title=012345678905/, { timeout: 20000 });
    console.log('lister browser test passed');
  } finally {
    await context.close();
    fs.rmSync(userDataDir, { recursive: true, force: true });
  }
})().catch(error => { console.error(error); process.exit(1); });
