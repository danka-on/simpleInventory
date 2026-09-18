// Seller Central's own controls, through the injected page script alone (no panel, no server):
// - the fulfilment question is a pair of Katal radio components whose host ignores a plain click;
//   Merchant Fulfilled must end up really selected, also when the question is drawn after the fill;
// - the product search button can be a bare magnifier with no words;
// - "All set - List it" on the HUD presses the page's own "Save and finish" (a Katal button) and
//   tells the side panel it did.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require('playwright');

const root = fs.existsSync(path.join(__dirname, 'lister-extension')) ? path.join(__dirname, 'lister-extension')
  : path.join(__dirname, '..', 'lister-extension');
const matcherSrc = fs.readFileSync(path.join(root, 'matcher.js'), 'utf8');
const contentSrc = fs.readFileSync(path.join(root, 'content.js'), 'utf8');

// Katal-like components: the radio's host swallows click(); only its inner <input> selects it.
const KATAL = `<script>
class KatRadio extends HTMLElement {
  constructor() {
    super();
    const root = this.attachShadow({ mode: 'open' });
    root.innerHTML = '<input type="radio"><slot></slot>';
    this._in = root.querySelector('input');
    this._in.addEventListener('change', () => {
      if (!this._in.checked) return;
      for (const other of document.querySelectorAll('kat-radiobutton')) {
        if (other !== this) { other.removeAttribute('checked'); other._in.checked = false; }
      }
      this.setAttribute('checked', '');
      window.picked = this.getAttribute('value');
    });
  }
  click() { /* the host has no action of its own */ }
}
customElements.define('kat-radiobutton', KatRadio);
class KatButton extends HTMLElement {
  constructor() {
    super();
    const root = this.attachShadow({ mode: 'open' });
    root.innerHTML = '<button type="button"><slot></slot></button>';
    root.querySelector('button').addEventListener('click', () => { window.pressed = (window.pressed || 0) + 1; });
  }
}
customElements.define('kat-button', KatButton);
</script>`;

const RADIOS = `
  <kat-radiobutton value="fba" label="I want to use Fulfilled by Amazon (FBA) to ship my items and provide customer service if it sells. (Fulfilled by Amazon)">I want to use Fulfilled by Amazon (FBA) to ship my items and provide customer service if it sells. (Fulfilled by Amazon)</kat-radiobutton>
  <kat-radiobutton value="mfn" label="I want to ship this item myself or use Amazon Easy Ship if it sells. (Merchant Fulfilled)">I want to ship this item myself or use Amazon Easy Ship if it sells. (Merchant Fulfilled)</kat-radiobutton>`;

function offerPage({ late = false } = {}) {
  return `<!doctype html><html><head><title>Add offer</title>${KATAL}</head><body>
<aside><p>ASIN: B0DGMVJJLJ</p></aside>
<form>
  <label for="sku">SKU</label><input id="sku" name="contribution_sku#1.value">
  <p>Fulfillment Channel Code</p><div id="fulfil">${late ? '' : RADIOS}</div>
  <label for="price">Your Price</label><input id="price" name="purchasable_offer#1.our_price#1.schedule#1.value_with_tax">
  <kat-button label="Save and finish" variant="primary">Save and finish</kat-button>
</form>
<script>${late ? `setTimeout(() => { document.getElementById('fulfil').innerHTML = ${JSON.stringify(RADIOS)}; }, 2500);` : ''}</script>
</body></html>`;
}

const SEARCH_PAGE = `<!doctype html><html><head><title>Add a Product</title></head><body><main>
<h1>Add a Product</h1>
<div class="search-bar">
  <input id="q" type="text" placeholder="Search by UPC, EAN, ISBN or ASIN" aria-label="Search by product ID">
  <button type="button" id="go"><span class="kat-icon" name="search" aria-hidden="true">&#x1F50D;</span></button>
</div>
<script>
  window.searched = '';
  document.getElementById('go').addEventListener('click', () => { window.searched = document.getElementById('q').value; });
</script></main></body></html>`;

const VALUES = { upc: '076440150179', sku: '761323062839-1', price: 34.99, quantity: 1, condition: 'NEW' };

async function open(context, url, html) {
  const page = await context.newPage();
  await page.route('https://sellercentral.amazon.com/**', route => route.fulfill({ contentType: 'text/html', body: html }));
  await page.addInitScript(() => {
    window.panelMessages = [];
    window.chrome = { runtime: { onMessage: { addListener() {} }, sendMessage: message => { window.panelMessages.push(message); return Promise.resolve(); } },
      storage: { local: { get: () => Promise.resolve({}), set: () => Promise.resolve() } } };
  });
  await page.goto(url);
  await page.evaluate(matcherSrc);
  await page.evaluate(contentSrc);
  return page;
}

(async () => {
  const browser = await chromium.launch({ channel: 'msedge' });
  const context = await browser.newContext();
  try {
    // 1. Merchant Fulfilled is really selected, never FBA, although the host ignores click().
    let page = await open(context, 'https://sellercentral.amazon.com/abis/listing/syh?asin=B0DGMVJJLJ', offerPage());
    const report = await page.evaluate(v => window.__ssLister.fill({ values: v, store: 'amazon' }), VALUES);
    assert.equal(await page.evaluate(() => window.picked), 'mfn', 'Merchant Fulfilled is selected');
    assert.ok(report.filled.some(f => f.target === 'fulfillment'), 'and reported as filled: ' + JSON.stringify(report.skipped));
    await page.close();

    // 2. Drawn after the fill: it is picked as soon as it shows up.
    page = await open(context, 'https://sellercentral.amazon.com/abis/listing/syh?asin=B0DGMVJJLJ', offerPage({ late: true }));
    await page.evaluate(v => window.__ssLister.fill({ values: v, store: 'amazon' }), VALUES);
    assert.equal(await page.evaluate(() => window.picked || ''), '', 'nothing to pick yet');
    await page.waitForFunction(() => window.picked === 'mfn', null, { timeout: 8000 });

    // 3. "All set - List it" presses the page's own Save and finish and tells the panel.
    assert.equal(await page.evaluate(() => window.__ssLister.pressSubmit()), true);
    await page.waitForFunction(() => window.pressed === 1, null, { timeout: 3000 });
    const told = await page.evaluate(() => window.panelMessages.find(m => m.type === 'ss-lister-submitted'));
    assert.equal(told.store, 'amazon', 'the panel hears it was submitted on Amazon');
    await page.close();

    // 4. A magnifier with no words is still the search button: the UPC is searched.
    page = await open(context, 'https://sellercentral.amazon.com/product-search', SEARCH_PAGE);
    const result = await page.evaluate(() => new Promise(resolve => window.__ssLister.search({ query: '076440150179', store: 'amazon' }, resolve)));
    assert.equal(result.ok, true, JSON.stringify(result));
    await page.waitForFunction(() => window.searched === '076440150179', null, { timeout: 8000 });
    assert.equal(result.submitted, 'button', 'pressed the search button, not a fallback');
    console.log('lister Seller Central controls test passed');
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error); process.exit(1); });
