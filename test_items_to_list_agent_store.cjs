// Items to List's "+ Listing Agent" column while the Lister side panel is open: "+" adds to the store
// list the panel shows (eBay or Amazon) and only that one; moving the panel to the other store makes
// "+" add there. The column also says "Listed on eBay" / "Listed on Amazon", both when both.
// Serves templates/items_to_list.html (no Jinja in it) with a fake Sweet Shelves server behind it.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { chromium } = require('playwright');

const root = fs.existsSync(path.join(__dirname, 'templates')) ? __dirname : path.join(__dirname, '..');
const ORIGIN = 'https://pi.example';
const NEW = '111111111116';
const BOTH = '840115641220';

(async () => {
  const userDataDir = fs.mkdtempSync(path.join(os.tmpdir(), 'itl-agent-'));
  const context = await chromium.launchPersistentContext(userDataDir, { channel: 'msedge', headless: true, viewport: { width: 1500, height: 900 } });
  const panel = { active: false, platform: '' };
  // The server's per-store rules, in small.
  const stores = { [NEW]: { queue: '', ebay: 'off', amazon: 'off' }, [BOTH]: { queue: 'removed', ebay: 'listed', amazon: 'listed' } };
  const calls = { add: [], store: [] };
  try {
    await context.route(`${ORIGIN}/**`, async route => {
      const request = route.request();
      const url = new URL(request.url());
      const json = body => route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(body) });
      if (url.pathname === '/items-to-list') {
        return route.fulfill({ status: 200, contentType: 'text/html; charset=utf-8', body: fs.readFileSync(path.join(root, 'templates', 'items_to_list.html'), 'utf8') });
      }
      if (url.pathname.startsWith('/static/')) {
        const file = path.join(root, url.pathname);
        return fs.existsSync(file) ? route.fulfill({ status: 200, contentType: 'application/javascript', body: fs.readFileSync(file) }) : json({});
      }
      if (url.pathname === '/api/bol_items') {
        const results = [NEW, BOTH].map((upc, i) => ({ id: i + 1, upc, title: upc === NEW ? 'Ninja blender' : 'Lenox plate', status: 'good', quantity: 1, lot_number: 'L-1' }));
        return json({ results, total: 2, unique_items: 2, total_quantity: 2, page: 1, limit: 50 });
      }
      if (url.pathname === '/api/lister/panel') return json({ success: true, ...panel });
      if (url.pathname === '/api/lister/queue-stores') {
        const upcs = request.postDataJSON().upcs;
        return json({ success: true, items: Object.fromEntries(upcs.map(u => [u, stores[u] || { queue: '', ebay: 'off', amazon: 'off' }])) });
      }
      if (url.pathname === '/api/listingagent/queue/statuses') return json({ success: true, items: {} });
      if (url.pathname === '/api/listingagent/queue/add') {
        const body = request.postDataJSON();
        calls.add.push(body);
        stores[body.upc] = { queue: 'queued', ebay: 'on', amazon: 'on' };
        return json({ success: true, added: true, item: { upc: body.upc, status: 'queued' } });
      }
      const store = url.pathname.match(/^\/api\/lister\/queue\/([^/]+)\/store$/);
      if (store) {
        const upc = decodeURIComponent(store[1]);
        const body = request.postDataJSON();
        calls.store.push({ upc, ...body });
        const s = { ...stores[upc] };
        const other = body.platform === 'ebay' ? 'amazon' : 'ebay';
        if (body.on) { if (body.only && body.fresh) s[other] = 'off'; s[body.platform] = 'on'; s.queue = 'queued'; }
        else { s[body.platform] = 'off'; if (s.ebay !== 'on' && s.amazon !== 'on') s.queue = 'removed'; }
        stores[upc] = s;
        return json({ success: true, upc, state: s });
      }
      return json({ success: true, lots: [], results: [] });
    });

    const page = await context.newPage();
    const broken = [];
    page.on('pageerror', error => broken.push(String(error)));
    await page.goto(`${ORIGIN}/items-to-list`);
    const cell = upc => page.locator(`.agent-cell[data-upc="${upc}"]`);
    await cell(NEW).waitFor({ timeout: 20000 });

    // Listed on both stores: both say so, even though its queue row was cleared long ago.
    assert.deepEqual(await cell(BOTH).locator('.agent-listed').allTextContents(), ['Listed on eBay ✓', 'Listed on Amazon ✓']);
    assert.equal(await cell(BOTH).locator('button').count(), 0, 'nothing left to add it to');
    // No panel open: the plain "+".
    assert.equal((await cell(NEW).locator('button').textContent()).trim(), '+');

    // The side panel opens on eBay: the column says so and "+" adds to eBay only.
    panel.active = true; panel.platform = 'ebay';
    await page.waitForFunction(() => document.getElementById('agentHead').textContent.includes('eBay'), null, { timeout: 10000 });
    assert.equal((await cell(NEW).locator('button').textContent()).trim(), '+ eBay');
    await cell(NEW).locator('button').click();
    await page.waitForFunction(upc => document.querySelector(`.agent-cell[data-upc="${upc}"] button`)?.textContent.includes('On eBay list'), NEW, { timeout: 10000 });
    assert.equal(calls.add.length, 1, 'queued through the usual Listing Agent add');
    assert.equal(calls.add[0].upc, NEW);
    assert.deepEqual(calls.store.at(-1), { upc: NEW, platform: 'ebay', on: true, only: true, fresh: true }, 'and kept to eBay');
    assert.deepEqual(stores[NEW], { queue: 'queued', ebay: 'on', amazon: 'off' });

    // The panel moves to Amazon: the same row offers "+ Amazon", and it adds there without a second queue add.
    panel.platform = 'amazon';
    await page.waitForFunction(() => document.getElementById('agentHead').textContent.includes('Amazon'), null, { timeout: 10000 });
    assert.equal((await cell(NEW).locator('button').textContent()).trim(), '+ Amazon', 'reset for Amazon');
    await cell(NEW).locator('button').click();
    await page.waitForFunction(upc => document.querySelector(`.agent-cell[data-upc="${upc}"] button`)?.textContent.includes('On Amazon list'), NEW, { timeout: 10000 });
    assert.equal(calls.add.length, 1, 'already in the queue: no second add');
    assert.deepEqual(calls.store.at(-1), { upc: NEW, platform: 'amazon', on: true, only: true, fresh: false });
    // Pressed again it comes off Amazon only.
    await cell(NEW).locator('button').click();
    await page.waitForFunction(upc => document.querySelector(`.agent-cell[data-upc="${upc}"] button`)?.textContent.includes('+ Amazon'), NEW, { timeout: 10000 });
    assert.deepEqual(calls.store.at(-1), { upc: NEW, platform: 'amazon', on: false, only: true, fresh: false });
    assert.equal(stores[NEW].ebay, 'on', 'still on the eBay list');

    // The panel closes: the plain queue button again, saying which store it waits on.
    panel.active = false; panel.platform = '';
    await page.waitForFunction(() => document.getElementById('agentHead').textContent.trim() === '+ Listing Agent', null, { timeout: 10000 });
    assert.deepEqual(broken, []);
    console.log('items to list agent store test passed');
  } finally {
    await context.close();
    fs.rmSync(userDataDir, { recursive: true, force: true });
  }
})().catch(error => { console.error(error); process.exit(1); });
