const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {chromium} = require('playwright');

(async () => {
  const browser = await chromium.launch({channel: 'msedge', headless: true});
  try {
    const page = await browser.newPage({viewport: {width: 1280, height: 900}});
    const html = fs.readFileSync('templates/fba_prep.html', 'utf8');
    const panel = html.slice(html.indexOf('    <section class="panel" id="panel-attention">'), html.indexOf('    <section class="panel" id="panel-review">'));
    const css = html.match(/<style>([\s\S]*?)<\/style>/)[1];
    let rows = [
      {id: 1, item: {barcode: '001234', title: 'Tablecloth <img src=x onerror=alert(1)>', seller_sku: 'SKU1', quantity: 3}, source_session_id: 5, status: 'needs_attention', category: 'approval', current_reason: 'Approval is required.', original_reason: 'Approval is required.', approval_required: 1, fix_url: '/listingagent?upc=001234', approval_url: 'https://sellercentral.amazon.com/hz/approvalrequest/restrictions/approve?asin=B000000001'},
      {id: 2, item: {barcode: '005678', title: 'Napkin rings', seller_sku: 'SKU2', quantity: 1}, source_session_id: 5, status: 'needs_attention', category: 'activation', current_reason: 'Waiting for FBA', original_reason: 'Waiting for FBA', fix_url: '/listingagent?upc=005678'},
      {id: 3, item: {barcode: '009999', title: 'Ready tablecloth', seller_sku: 'SKU3', quantity: 2}, source_session_id: 5, status: 'ready_to_retry', category: 'ready', current_reason: 'Ready to retry.', original_reason: 'Missing description', fix_url: '/listingagent?upc=009999'}
    ];
    const requests = [], errors = [];
    page.on('pageerror', e => errors.push(e.message));
    page.on('dialog', d => d.accept());
    await page.route('http://attention.test/**', async route => {
      const url = new URL(route.request().url());
      if (url.pathname === '/') return route.fulfill({contentType: 'text/html', body: `<meta charset="utf-8"><style>${css}</style><main style="padding:20px"><span id="attentionCount"></span>${panel.replace('class="panel"', 'class="panel active"')}</main><script>${fs.readFileSync('static/fba-attention.js', 'utf8')}</script><script>fbaAttention.open()</script>`});
      if (url.pathname === '/api/fba-prep/attention') return route.fulfill({json: {success: true, items: rows, drafts: [{id: 9, name: 'Retry batch'}]}});
      requests.push({url: url.pathname, data: route.request().postDataJSON()});
      const id = Number(url.pathname.split('/').at(-2));
      if (url.pathname.endsWith('/recheck')) {
        rows = rows.map(r => r.id === id ? {...r, status: 'ready_to_retry', category: 'ready', checked_at: new Date().toISOString(), current_reason: 'FBA offer active.'} : r);
        return route.fulfill({json: {success: true, item: rows.find(r => r.id === id)}});
      }
      if (url.pathname.endsWith('/add')) {
        rows = rows.map(r => r.id === id ? {...r, status: 'assigned', assigned_session_id: 9} : r);
        return route.fulfill({json: {success: true, session_id: 9}});
      }
      throw Error('Unexpected request ' + url.pathname);
    });
    await page.goto('http://attention.test/');
    await page.getByText('3 item types · 6 units saved · 1 ready to retry').waitFor();
    assert.equal(await page.locator('#attentionList img').count(), 0, 'Titles must be escaped');
    await page.locator('#attentionSearch').fill('005678');
    assert.equal(await page.locator('#attentionList article').count(), 1);
    await page.locator('#attentionSearch').press('Enter');
    assert.equal(requests.length, 0, 'Searching with a scanner must not change stock');
    await page.getByRole('button', {name: 'Recheck', exact: true}).click();
    await page.getByRole('button', {name: 'Add 1 units to next shipment'}).waitFor();
    await page.locator('#attentionSearch').fill('');
    await page.locator('#attentionFilter').selectOption('approval');
    assert.equal(await page.locator('#attentionList article').count(), 1);
    assert.match(await page.getByRole('link', {name: 'Open Amazon approval'}).getAttribute('href'), /^https:\/\/sellercentral.amazon.com\//);
    await page.locator('#attentionFilter').selectOption('ready');
    await page.locator('#attentionDestination').selectOption('9');
    await page.getByRole('button', {name: 'Add 2 units to next shipment'}).click();
    await page.getByText(/Saved 2 units in shipment #9/).waitFor();
    assert.deepEqual(requests.find(r => r.url.endsWith('/add')).data, {session_id: 9});
    await page.locator('#attentionFilter').selectOption('assigned');
    await page.getByRole('button', {name: 'Open shipment #9'}).waitFor();
    assert.equal(await page.locator('[data-add]').count(), 0);
    await page.locator('#attentionFilter').selectOption('all');
    if (process.argv[2]) await page.screenshot({path: path.join(process.argv[2], 'attention-desktop.png'), fullPage: true});
    await page.setViewportSize({width: 390, height: 844});
    assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth), 'Mobile page must not overflow horizontally');
    if (process.argv[2]) await page.screenshot({path: path.join(process.argv[2], 'attention-mobile.png'), fullPage: true});
    assert.deepEqual(errors, []);
    console.log('Needs attention browser: search/scans, escaping, recheck, filters, transfer destination, assigned state, and mobile layout passed.');
  } finally { await browser.close(); }
})().catch(error => {console.error(error); process.exitCode = 1;});
