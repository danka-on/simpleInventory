// eBay's other important boxes, through the injected page script alone (no panel, no server):
// the Custom label (SKU) hides behind "See title options" and a switch - the fill opens both and
// types our code (suffixed or self-made) in as the box draws; Brand and UPC in the item specifics
// are filled; and the guide's checklist lists Brand, UPC and the custom label so an empty one is
// looked at rather than passed over.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require('playwright');

const root = fs.existsSync(path.join(__dirname, 'lister-extension')) ? path.join(__dirname, 'lister-extension')
  : path.join(__dirname, '..', 'lister-extension');
const matcherSrc = fs.readFileSync(path.join(root, 'matcher.js'), 'utf8');
const contentSrc = fs.readFileSync(path.join(root, 'content.js'), 'utf8');

// The options fold open on "See title options"; the custom label box only exists once its switch is on.
function listingPage() {
  return `<!doctype html><html><head><title>List an item</title></head><body>
<form>
  <section><h2>Title</h2><label for="title">Item title</label><input id="title" name="title">
    <button type="button" id="seeOptions">See title options</button>
    <div id="options" hidden>
      <div class="row"><span>Subtitle</span><input type="checkbox" role="switch" id="subtitleSwitch"></div>
      <div class="row"><span>Custom label (SKU)</span><input type="checkbox" role="switch" id="skuSwitch"></div>
      <div id="skuBox"></div>
    </div>
  </section>
  <section><h2>Item specifics</h2>
    <div><label for="brand">Brand</label><input id="brand" name="brand" type="text"></div>
    <div><label for="upc">UPC</label><input id="upc" name="upc" type="text"></div>
  </section>
  <section><h2>Pricing</h2><label for="price">Item price</label><input id="price" name="price" type="text">
    <label for="qty">Quantity</label><input id="qty" name="quantity" type="text"></section>
</form>
<script>
  document.getElementById('seeOptions').addEventListener('click', () => {
    setTimeout(() => { document.getElementById('options').hidden = false; }, 150);
  });
  document.getElementById('skuSwitch').addEventListener('change', e => {
    setTimeout(() => {
      document.getElementById('skuBox').innerHTML = e.target.checked ? '<label for="customLabel">Custom label (SKU)</label><input id="customLabel" name="customLabel" maxlength="50">' : '';
    }, 150);
  });
</script></body></html>`;
}

const VALUES = { upc: '076440150179', sku: '076440150179-2', brand: 'Riedel', title: 'Red Wine Glasses', price: 34.99, quantity: 1, condition: 'NEW' };

async function openListing(context) {
  const page = await context.newPage();
  await page.route('https://www.ebay.com/**', route => route.fulfill({ contentType: 'text/html', body: listingPage() }));
  await page.addInitScript(() => {
    window.chrome = { runtime: { onMessage: { addListener() {} }, sendMessage: () => Promise.resolve() },
      storage: { local: { get: () => Promise.resolve({}), set: () => Promise.resolve() } } };
  });
  await page.goto('https://www.ebay.com/lstng?draftId=1&mode=AddItem');
  await page.evaluate(matcherSrc);
  await page.evaluate(contentSrc);
  return page;
}

(async () => {
  const browser = await chromium.launch({ channel: 'msedge' });
  const context = await browser.newContext();
  try {
    // Fill: brand and UPC go straight in; the custom label is opened, switched on and typed as it draws.
    let page = await openListing(context);
    const report = await page.evaluate(v => window.__ssLister.fill({ values: v, store: 'ebay' }), VALUES);
    assert.equal(await page.inputValue('#brand'), 'Riedel', 'brand filled from the proposal');
    assert.equal(await page.inputValue('#upc'), '076440150179', 'UPC filled with the catalogue code');
    const pending = report.filled.find(f => f.target === 'customLabel');
    assert.ok(pending && pending.pending, 'the custom label is reported as opened, still to be typed');
    await page.waitForSelector('#customLabel', { timeout: 4000 });
    await page.waitForFunction(() => document.getElementById('customLabel').value === '076440150179-2', null, { timeout: 8000 });
    assert.equal(await page.isChecked('#skuSwitch'), true, 'the Custom label switch is on');
    assert.equal(await page.isChecked('#subtitleSwitch'), false, 'the subtitle switch is left alone');
    assert.equal(await page.inputValue('#customLabel'), '076440150179-2', 'our suffixed code is the custom label');
    // A second fill on the same page does not fold the options away or clear the code.
    await page.evaluate(v => window.__ssLister.fill({ values: v, store: 'ebay' }), VALUES);
    await page.waitForTimeout(700);
    assert.equal(await page.inputValue('#customLabel'), '076440150179-2', 'still there after a refill');
    assert.equal(await page.isChecked('#skuSwitch'), true, 'switch still on after a refill');
    await page.close();

    // Guide without a fill: Brand, UPC and the custom label are on the checklist; empty ones stay open.
    page = await openListing(context);
    const thin = { ...VALUES, brand: '', upc: '' };
    await page.evaluate(v => window.__ssLister.guideStart({ values: v, store: 'ebay', aspects: {} }), thin);
    await page.waitForFunction(() => document.getElementById('customLabel') && document.getElementById('customLabel').value === '076440150179-2', null, { timeout: 8000 });
    await page.waitForTimeout(2300);  // one guide refresh tick, so the late box is on the list
    const state = await page.evaluate(() => window.__ssLister.guideState());
    const byTarget = Object.fromEntries(state.rows.map(r => [r.target, r]));
    assert.ok(byTarget.brand && !byTarget.brand.done, 'empty Brand is listed as open');
    assert.ok(byTarget.upc && !byTarget.upc.done, 'empty UPC is listed as open');
    assert.ok(byTarget.sku && byTarget.sku.done, 'the custom label row is listed and done');
    assert.equal(byTarget.sku.label, 'SKU / custom label');
    await page.close();

    // No code to put in: nothing is opened.
    page = await openListing(context);
    await page.evaluate(v => window.__ssLister.fill({ values: v, store: 'ebay' }), { ...VALUES, sku: '' });
    await page.waitForTimeout(900);
    assert.equal(await page.isHidden('#options'), true, 'title options stay folded without a code');
    await page.close();
    console.log('lister eBay fields: ok');
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error); process.exit(1); });
