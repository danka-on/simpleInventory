// eBay's listing form price, through the injected page script alone (no panel, no server): eBay's
// own recommended price goes into the price box - also when its pricing card draws late - and the
// box wears a "Suggested price" tag. A "Recommended" shipping service is never read as the price,
// and a price the user typed is never written over.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require('playwright');

const root = fs.existsSync(path.join(__dirname, 'lister-extension')) ? path.join(__dirname, 'lister-extension')
  : path.join(__dirname, '..', 'lister-extension');
const matcherSrc = fs.readFileSync(path.join(root, 'matcher.js'), 'utf8');
const contentSrc = fs.readFileSync(path.join(root, 'content.js'), 'utf8');

// recommended: the figure eBay advises (null for none); lateMs: how long the pricing card takes to draw.
function listingPage({ recommended, lateMs = 0 }) {
  return `<!doctype html><html><head><title>List an item</title></head><body>
<form>
  <section><h2>Title</h2><label for="title">Item title</label><input id="title" name="title"></section>
  <section id="pricing"><h2>Pricing</h2>
    <div><label for="price">Item price</label><span>$</span><input id="price" name="price" type="text"></div>
    <div id="advice"></div>
  </section>
  <section><h2>Shipping</h2>
    <p>Recommended: USPS Ground Advantage $5.40</p>
    <label for="ship">Shipping cost</label><input id="ship" name="shippingCost">
  </section>
</form>
<script>
  const figure = ${recommended ? `'${recommended}'` : 'null'};
  if (figure) setTimeout(() => {
    document.getElementById('advice').innerHTML = '<p>Pricing recommendation</p><p>Recommended: $' + figure + '</p><p>Similar items sold for $12.00 - $40.00</p>';
  }, ${lateMs});
</script></body></html>`;
}

const VALUES = { upc: '076440150179', sku: '761323062839-1', title: 'Red Wine Glasses', price: 34.99, quantity: 1, condition: 'NEW' };

async function openListing(context, shape) {
  const page = await context.newPage();
  await page.route('https://www.ebay.com/**', route => route.fulfill({ contentType: 'text/html', body: listingPage(shape) }));
  await page.addInitScript(() => {
    window.chrome = { runtime: { onMessage: { addListener() {} }, sendMessage: () => Promise.resolve() },
      storage: { local: { get: () => Promise.resolve({}), set: () => Promise.resolve() } } };
  });
  await page.goto('https://www.ebay.com/lstng?draftId=1&mode=AddItem');
  await page.evaluate(matcherSrc);
  await page.evaluate(contentSrc);
  return page;
}

const fill = page => page.evaluate(v => window.__ssLister.fill({ values: v, store: 'ebay' }), VALUES);
const tag = page => page.evaluate(() => Array.from(document.querySelectorAll('[data-ss-ai-badge]')).map(b => b.textContent).join('|'));

(async () => {
  const browser = await chromium.launch({ channel: 'msedge' });
  const context = await browser.newContext();
  try {
    // Recommendation already on the page: it goes in at once and is reported.
    let page = await openListing(context, { recommended: '24.99' });
    await page.waitForSelector('#advice p');
    let report = await fill(page);
    assert.equal(await page.inputValue('#price'), '24.99', 'eBay recommended price replaces our prepared 34.99');
    const entry = report.filled.find(f => f.target === 'suggestedPrice');
    assert.ok(entry, 'reported as the suggested price');
    assert.match(await tag(page), /Suggested price \$24\.99/, 'the price box wears the Suggested price tag');
    assert.equal(await page.inputValue('#ship'), '', 'the shipping recommendation stays out of every box');
    await page.close();

    // The pricing card draws after the fill: our price first, then eBay's once it shows.
    page = await openListing(context, { recommended: '19.50', lateMs: 1200 });
    report = await fill(page);
    assert.equal(await page.inputValue('#price'), '34.99', 'our price until eBay advises');
    await page.waitForFunction(() => document.getElementById('price').value === '19.50', null, { timeout: 6000 });
    assert.match(await tag(page), /Suggested price \$19\.50/);
    await page.close();

    // The user typed their own price before the card drew: left alone.
    page = await openListing(context, { recommended: '19.50', lateMs: 1200 });
    await fill(page);
    await page.fill('#price', '45.00');
    await page.waitForTimeout(2500);
    assert.equal(await page.inputValue('#price'), '45.00', 'a price the user typed is never written over');
    assert.equal(await tag(page), '', 'and no suggested tag is claimed');
    await page.close();

    // No recommendation at all (only the shipping one): our prepared price stays.
    page = await openListing(context, { recommended: null });
    report = await fill(page);
    await page.waitForTimeout(1200);
    assert.equal(await page.inputValue('#price'), '34.99', 'no advice, so our price stays');
    assert.equal(report.filled.find(f => f.target === 'suggestedPrice'), undefined);
    await page.close();

    console.log('lister eBay suggested price checks passed');
  } finally {
    await context.close();
    await browser.close();
  }
})().catch(error => { console.error(error); process.exit(1); });
