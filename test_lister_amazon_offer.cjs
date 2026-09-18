// Seller Central's Add offer price, through the injected page script alone (no panel, no server):
// the "Match lowest price" link is always pressed first, and only when the page draws no link is
// the lowest offer read from the page text instead. The rest of the Add offer page (who ships it,
// the late quantity box, the condition) is held by test_lister_browser.cjs.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require('playwright');

const root = fs.existsSync(path.join(__dirname, 'lister-extension')) ? path.join(__dirname, 'lister-extension')
  : path.join(__dirname, '..', 'lister-extension');
const matcherSrc = fs.readFileSync(path.join(root, 'matcher.js'), 'utf8');
const contentSrc = fs.readFileSync(path.join(root, 'content.js'), 'utf8');

// competing: the figure in the item panel; link: the figure the link puts in the box (or null for no link).
function offerPage({ competing, link }) {
  return `<!doctype html><html><head><title>Add offer</title></head><body>
<aside><h2>Anchor Hocking Red Wine Glasses, Set of 8</h2><p>ASIN: B0DGMVJJLJ</p>
${competing ? `<p>Competing Marketplace Offers: 2 New from $${competing} + $0.00 shipping</p>` : ''}</aside>
<form>
  <label for="sku">SKU</label><input id="sku" name="contribution_sku#1.value">
  <label for="price">Your Price</label><span>USD$</span>
  <input id="price" name="purchasable_offer#1.our_price#1.schedule#1.value_with_tax" placeholder="Example: 9.00">
  ${link ? `<div><a href="#" id="match">Match lowest price: USD$${link}</a></div>` : ''}
</form>
<script>
  window.matchClicks = 0;
  const match = document.getElementById('match');
  if (match) match.addEventListener('click', event => {
    event.preventDefault();
    window.matchClicks += 1;
    const box = document.getElementById('price');
    box.value = '${link || ''}';
    box.dispatchEvent(new Event('input', { bubbles: true }));
  });
</script></body></html>`;
}

const VALUES = { upc: '076440150179', sku: '761323062839-1', price: 34.99, quantity: 1, condition: 'NEW' };

async function openOffer(context, shape) {
  const page = await context.newPage();
  await page.route('https://sellercentral.amazon.com/**', route => route.fulfill({ contentType: 'text/html', body: offerPage(shape) }));
  // The page script expects an extension around it; nothing here listens, so a stub is enough.
  await page.addInitScript(() => {
    window.chrome = { runtime: { onMessage: { addListener() {} }, sendMessage: () => Promise.resolve() },
      storage: { local: { get: () => Promise.resolve({}), set: () => Promise.resolve() } } };
  });
  await page.goto('https://sellercentral.amazon.com/abis/listing/syh?asin=B0DGMVJJLJ');
  await page.evaluate(matcherSrc);
  await page.evaluate(contentSrc);
  return page;
}

const fill = page => page.evaluate(v => window.__ssLister.fill({ values: v, store: 'amazon' }), VALUES);
const priceEntry = report => report.filled.concat(report.skipped).find(f => f.target === 'matchLowest');

(async () => {
  const browser = await chromium.launch({ channel: 'msedge' });
  const context = await browser.newContext();
  try {
    // The link is there (the usual case): it is pressed, and Amazon's own figure wins even over a
    // different number written elsewhere on the page.
    let page = await openOffer(context, { competing: '31.00', link: '29.40' });
    let report = await fill(page);
    assert.equal(await page.evaluate(() => window.matchClicks), 1, 'the Match lowest price link is pressed once');
    assert.equal(await page.inputValue('#price'), '29.40', 'the price is what the link put in, not the panel text or ours');
    assert.equal(priceEntry(report).label, 'Match lowest price', 'reported as the link, not the text fallback');
    await page.close();

    // No link drawn: the lowest offer written beside the form goes in instead of our prepared 34.99.
    page = await openOffer(context, { competing: '29.40', link: null });
    report = await fill(page);
    assert.equal(await page.inputValue('#price'), '29.40', 'the competing offer figure is typed');
    assert.match(priceEntry(report).label, /read from the page/, 'reported as read from the page');
    await page.close();

    // Nothing on the page to match: our own prepared price stays.
    page = await openOffer(context, { competing: null, link: null });
    report = await fill(page);
    assert.equal(await page.inputValue('#price'), '34.99', 'nothing to match, so our price stays');
    assert.equal(priceEntry(report), undefined, 'and no lowest-price claim is made');
    await page.close();

    console.log('lister Amazon offer price checks passed');
  } finally {
    await context.close();
    await browser.close();
  }
})().catch(error => { console.error(error); process.exit(1); });
