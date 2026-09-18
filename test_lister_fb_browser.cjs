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
  const calls = { panel: [], saves: [], build: 0, listed: 0 };
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
      if (url.pathname === '/api/lister/fb/categories') return json({ success: true, categories: [CATEGORY], suggested: [] });
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
    assert.equal(await panel.$eval('#fbTitle', el => el.value), 'Lenox Butterfly Meadow Dinner Plate');
    assert.match(await panel.textContent('#fbTitleN'), /^35\/150$/);
    assert.match(await panel.textContent('#fbCard .fb-note'), /Small chip/);
    await panel.fill('#fbPrice', '22');
    await panel.selectOption('#fbCond', 'Used - Good');
    await panel.fill('#fbCatSearch', 'plates');
    await panel.waitForSelector('#fbCatResults .fb-cat');
    await panel.click('#fbCatResults .fb-cat');
    assert.equal(await panel.$eval('#fbCat', el => el.value), CATEGORY);
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
