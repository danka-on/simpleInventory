// Sweet Shelves Lister FB tab in a real browser: the unpacked extension against a fake Sweet
// Shelves API. Opens the FB tab (the heartbeat then says 'fb', so Items to List's "+" adds to
// the Facebook list), reviews an item (price, condition, category search), marks it Ready,
// builds Facebook's workbook (a real file download), marks the workbook listed, and goes back
// to eBay. Uses the system Edge like the other Playwright tests.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { chromium } = require('playwright');

const extensionPath = path.join(__dirname, 'lister-extension');
const UPC = '883049370897';
const CATEGORY = 'Home & Kitchen//Kitchen & Dining//Dinnerware//Plates';

(async () => {
  const userDataDir = fs.mkdtempSync(path.join(os.tmpdir(), 'lister-fb-browser-'));
  const context = await chromium.launchPersistentContext(userDataDir, {
    channel: 'msedge', headless: false, acceptDownloads: true,
    args: ['--headless=new', `--disable-extensions-except=${extensionPath}`, `--load-extension=${extensionPath}`, '--no-first-run'],
  });
  const calls = { panel: [], saves: [], build: 0, listed: 0, generate: [], categories: [] };
  const fbItem = { upc: UPC, status: 'queued', title: 'Lenox Butterfly Meadow Dinner Plate', price: 24, condition: 'New', category: '',
                   reviewed: false, batchId: null, thumb: '', problems: [] };
  const batches = [];
  try {
    await context.route('https://pi.nexuscentralhq.org/**', async route => {
      const request = route.request();
      const url = new URL(request.url());
      const json = (body, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) });
      if (url.pathname === '/api/lister/ping') return json({ success: true, version: '0.2.0', user: 'dan@example.com' });
      if (url.pathname === '/api/lister/queue') return json({ success: true, items: [], counts: { queued: 0, listed: 0, hidden: 0 } });
      if (url.pathname === '/api/lister/links') return json({ success: true, links: [] });
      if (url.pathname === '/api/lister/fb/queue') {
        const items = fbItem.status === 'listed' ? [] : [fbItem];
        const count = s => items.filter(i => i.status === s).length;
        return json({ success: true, items, counts: { queued: count('queued'), ready: count('ready'), in_template: count('in_template') },
                      batches: batches.filter(b => b.status === 'built'), listedToday: fbItem.status === 'listed' ? 1 : 0,
                      uploadUrl: 'https://www.facebook.com/marketplace/create/item', helpUrl: '', maxRows: 50 });
      }
      if (url.pathname === `/api/lister/fb/item/${UPC}` && request.method() === 'GET') {
        return json({ success: true, upc: UPC, status: fbItem.status, reviewed: false, batchId: fbItem.batchId,
          draft: { title: fbItem.title, price: 24, condition: 'New', description: 'Lenox plate.', category: '',
                   suggestions: ['Home & Kitchen//Kitchen & Dining//Dinnerware//Dinnerware Sets'] },
          photos: [{ url: 'https://pi.nexuscentralhq.org/static/p1.jpg', thumb: 'https://pi.nexuscentralhq.org/static/p1.jpg' }],
          conditions: ['New', 'Used - Like New', 'Used - Good', 'Used - Fair'], notes: '', defect: 'Small chip', racks: [],
          limits: { title: 150, description: 5000 }, problems: [] });
      }
      // The full Lister item the FB review card borrows its strip and photo card from.
      if (url.pathname === `/api/lister/queue/${UPC}` && request.method() === 'GET') {
        return json({ success: true, item: { upc: UPC, title: 'LENOX BUTTERFLY MEADOW DINNER PLT', suffixed: false,
          photos: [{ url: 'https://pi.nexuscentralhq.org/static/p1.jpg', name: 'p1.jpg', source: 'listing' },
                   { url: 'https://pi.nexuscentralhq.org/static/p2.jpg', name: 'p2.jpg', source: 'prep' }],
          inventory: { quantity: 2, positions: ['B-4'] }, gate: { rackQty: 2, listable: 2, liveEbay: 1, liveAmazon: 0 },
          links: [{ platform: 'ebay', url: 'https://www.ebay.com/itm/335566778899', kind: 'listed', listing_id: '335566778899' }],
          existing: {}, queue: { listed: { ebay: true }, skipped: [] }, notes: [], voiceNotes: [], defect: 'Small chip',
          fields: { title: 'Lenox Butterfly Meadow Dinner Plate', brand: 'Lenox', aspects: { Color: ['White'] } },
          mobilePhotosUrl: 'https://pi.nexuscentralhq.org/items-to-list/mobile-photos/x' } });
      }
      if (url.pathname === '/api/lister/qr') return route.fulfill({ status: 200, contentType: 'image/gif', body: Buffer.from('R0lGODlhAQABAAAAACw=', 'base64') });
      if (url.pathname === '/api/lister/fb/categories') {
        // Word search ('plates') lists matches; a whole title scores suggestions (auto-category).
        const q = url.searchParams.get('q') || '';
        calls.categories.push(q);
        return json({ success: true, categories: q === 'plates' ? [CATEGORY] : [], suggested: /dinner plate/i.test(q) ? [CATEGORY] : [] });
      }
      if (url.pathname.startsWith('/static/')) return route.fulfill({ status: 200, contentType: 'image/gif', body: Buffer.from('R0lGODlhAQABAAAAACw=', 'base64') });
      if (/^\/api\/lister\/fb\/batch\/\d+\.xlsx$/.test(url.pathname)) {
        return route.fulfill({ status: 200, contentType: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', body: Buffer.from('PK fake xlsx') });
      }
      assert.equal(request.headers()['x-sweet-shelves-lister'], '1', 'mutations carry the extension header: ' + url.pathname);
      if (url.pathname === '/api/lister/panel') { calls.panel.push(request.postDataJSON()); return json({ success: true, active: true }); }
      if (url.pathname === `/api/lister/fb/item/${UPC}`) {
        const body = request.postDataJSON();
        calls.saves.push(body);
        Object.assign(fbItem, { price: Number(body.price), condition: body.condition, category: body.category, status: body.ready ? 'ready' : 'queued' });
        return json({ success: true, upc: UPC, status: fbItem.status, problems: [] });
      }
      if (url.pathname === `/api/lister/queue/${UPC}/generate`) {
        const body = request.postDataJSON();
        calls.generate.push(body);
        return json(body.kind === 'title'
          ? { success: true, kind: 'title', title: 'Lenox Butterfly Meadow Dinner Plate 10.75 in White' }
          : { success: true, kind: 'description', descriptionText: 'Beautiful Lenox Butterfly Meadow dinner plate.', descriptionHtml: '<p>x</p>' });
      }
      if (url.pathname === '/api/lister/fb/build') {
        calls.build += 1;
        batches.push({ id: 7, createdAt: '2026-09-18T16:30:00', count: 1, status: 'built' });
        Object.assign(fbItem, { status: 'in_template', batchId: 7 });
        return json({ success: true, batchId: 7, count: 1, download: '/api/lister/fb/batch/7.xlsx' });
      }
      if (url.pathname === '/api/lister/fb/batch/7/listed') {
        calls.listed += 1;
        batches[0].status = 'listed';
        fbItem.status = 'listed';
        return json({ success: true, batchId: 7, listed: 1, recorded: 1, problems: [] });
      }
      return json({ success: false, error: 'nope ' + url.pathname }, 404);
    });

    let [worker] = context.serviceWorkers();
    if (!worker) worker = await context.waitForEvent('serviceworker');
    const extensionId = new URL(worker.url()).host;
    const panel = await context.newPage();
    await panel.goto(`chrome-extension://${extensionId}/sidepanel.html`);
    await panel.waitForSelector('#storeFb');
    assert.deepEqual(await panel.$$eval('.stores .store', els => els.map(el => el.id)), ['storeEbay', 'storeAmazon', 'storeFb', 'storeNew'],
      'FB sits after Amazon, before + NEW');

    // Open the FB tab: the queue card hides, the FB list shows, the heartbeat reports fb.
    await panel.click('#storeFb');
    await panel.waitForSelector(`#fbCard .fb-row[data-upc="${UPC}"]`);
    assert.equal(await panel.$eval('#listCard', el => el.hidden), true);
    assert.equal(await panel.$eval('#storeFb', el => el.getAttribute('aria-selected')), 'true');
    await panel.waitForFunction(() => document.getElementById('goBtn').textContent === 'Review the next item');
    for (let i = 0; i < 40 && !calls.panel.some(p => p.platform === 'fb'); i++) await panel.waitForTimeout(100);
    assert.ok(calls.panel.some(p => p.platform === 'fb'), 'heartbeat says fb: ' + JSON.stringify(calls.panel));

    // Review: price, condition, a category found by search, then Ready.
    await panel.click(`#fbCard .fb-row[data-upc="${UPC}"]`);
    await panel.waitForSelector('#fbTitle');
    // Auto-category: the title alone picks the category and says so; nothing was chosen by hand yet.
    await panel.waitForFunction(cat => document.getElementById('fbCat').value === cat, CATEGORY);
    assert.match(await panel.textContent('#fbCard .fb-catline'), /Plates\s*auto/);
    assert.ok(calls.categories.some(q => /Lenox Butterfly Meadow Dinner Plate/.test(q)), 'the whole title was scored');
    assert.equal(await panel.$eval('#fbTitle', el => el.value), 'Lenox Butterfly Meadow Dinner Plate');
    assert.match(await panel.textContent('#fbTitleN'), /^35\/150$/);
    assert.match(await panel.textContent('#fbCard .fb-note'), /Small chip/);
    // The same cards as the eBay/Amazon item: Rack · eBay · Amazon, and the whole photo card.
    await panel.waitForSelector('#fbCard .strip .cell.rack');
    assert.match(await panel.textContent('#fbCard .strip .cell.rack'), /2\s*to list/);
    assert.match(await panel.textContent('#fbCard .strip .cell.rack'), /B-4/);
    assert.match(await panel.textContent('#fbCard .strip .cell.ebay'), /Listed/);
    assert.match(await panel.textContent('#fbCard .strip .cell.amazon'), /Not listed/);
    assert.equal(await panel.$$eval('#fbCard .photos .photo', els => els.length), 2);
    for (const id of ['photoLink', 'addPhoto', 'photoRefresh', 'aiPhotos']) assert.ok(await panel.$('#fbCard #' + id), 'photo card has ' + id);
    assert.equal(await panel.$('#fbCard #sendPhotos'), null, 'no "Send to page": there is no Facebook form to fill');
    await panel.click('#fbCard .photos .photo');
    assert.ok(await panel.$eval('#fbCard .photos .photo', el => el.classList.contains('selected')), 'a photo can be ticked');
    await panel.click('#fbCard #addPhoto');
    await panel.waitForSelector('#fbCard .qr img');
    // AI beside Title and Description fills the box; nothing is saved until Save.
    await panel.click('#fbCard [data-act="ai-title"]');
    await panel.waitForFunction(() => document.getElementById('fbTitle').value.endsWith('10.75 in White'));
    assert.equal(calls.generate.at(-1).kind, 'title');
    assert.equal(calls.generate.at(-1).values.brand, 'Lenox', 'the writer gets the item facts');
    assert.equal(calls.generate.at(-1).values.condition, 'New', 'and the Facebook condition');
    assert.ok(calls.generate.at(-1).values.notes.includes('Small chip'), 'and the notes');
    await panel.click('#fbCard [data-act="ai-description"]');
    await panel.waitForFunction(() => document.getElementById('fbDesc').value.startsWith('Beautiful Lenox'));
    assert.match(await panel.textContent('#fbTitleN'), /^50\/150$/, 'the counter follows');
    assert.equal(calls.saves.length, 0, 'nothing saved by the AI buttons');
    // Back to the original text so the rest of this test reads as before.
    await panel.fill('#fbTitle', 'Lenox Butterfly Meadow Dinner Plate');
    await panel.fill('#fbDesc', 'Lenox plate.');
    await panel.fill('#fbPrice', '22');
    await panel.selectOption('#fbCond', 'Used - Good');
    await panel.fill('#fbCatSearch', 'plates');
    await panel.waitForSelector('#fbCatResults .fb-cat');
    await panel.click('#fbCatResults .fb-cat');
    assert.equal(await panel.$eval('#fbCat', el => el.value), CATEGORY);
    assert.doesNotMatch(await panel.textContent('#fbCard .fb-catline'), /auto/, 'a hand-picked category is not auto');
    assert.equal(await panel.textContent('#goBtn'), 'Ready ✓ · next item');
    await panel.click('#goBtn');
    await panel.waitForSelector(`#fbCard .fb-row.ready[data-upc="${UPC}"]`);
    assert.deepEqual(calls.saves.at(-1), { title: 'Lenox Butterfly Meadow Dinner Plate', price: '22', condition: 'Used - Good',
      category: CATEGORY, description: 'Lenox plate.', ready: true });

    // Build the workbook: a real download of Facebook's file.
    await panel.waitForFunction(() => document.getElementById('goBtn').textContent === 'Build Facebook workbook (1 ready)');
    const [download] = await Promise.all([panel.waitForEvent('download'), panel.click('#goBtn')]);
    assert.equal(download.suggestedFilename(), 'facebook-marketplace-7.xlsx');
    assert.equal(calls.build, 1);
    await panel.waitForSelector('#fbCard .fb-batch');
    assert.match(await panel.textContent('#fbCard .fb-batch'), /Workbook #7/);

    // Mark listed needs a second click.
    await panel.click('#fbCard [data-act="listed"]');
    assert.equal(calls.listed, 0);
    await panel.waitForFunction(() => document.querySelector('#fbCard [data-act="listed"]').textContent.includes('Sure?'));
    await panel.click('#fbCard [data-act="listed"]');
    await panel.waitForSelector('#fbCard .fb-empty');
    assert.equal(calls.listed, 1);

    // Back to eBay: the queue shows again and the heartbeat says ebay.
    const before = calls.panel.length;
    await panel.click('#storeEbay');
    assert.equal(await panel.$eval('#fbCard', el => el.hidden), true);
    assert.equal(await panel.$eval('#listCard', el => el.hidden), false);
    for (let i = 0; i < 40 && !calls.panel.slice(before).some(p => p.platform === 'ebay'); i++) await panel.waitForTimeout(100);
    assert.ok(calls.panel.slice(before).some(p => p.platform === 'ebay'), 'heartbeat back to ebay');
    console.log('lister fb browser test passed');
  } finally {
    await context.close();
  }
})().catch(error => { console.error(error); process.exit(1); });
