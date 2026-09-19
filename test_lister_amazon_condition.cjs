// Seller Central's Add offer page as it really is (2026-09-19): the SKU box, "Match lowest price"
// under the price, an Item Condition that is a Katal dropdown (kat-dropdown + kat-option in a
// shadow root, never a <select>) and a plain "Submit" kat-button. Through the injected page script
// alone: the fill sets the SKU and the condition and presses the price link even when it draws
// late; the HUD lists the condition as a checkpoint after quantity and price; walking past the
// last checkpoint presses Submit and tells the panel; and "All set" finds the Submit button.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require('playwright');

const root = fs.existsSync(path.join(__dirname, 'lister-extension')) ? path.join(__dirname, 'lister-extension')
  : path.join(__dirname, '..', 'lister-extension');
const matcherSrc = fs.readFileSync(path.join(root, 'matcher.js'), 'utf8');
const contentSrc = fs.readFileSync(path.join(root, 'content.js'), 'utf8');

// lateMs: how long the offer panel (with its Match lowest price link) takes to draw.
function offerPage({ lateMs = 0, preset = '' } = {}) {
  return `<!doctype html><html><head><title>Add offer</title></head><body>
<aside id="panel"></aside>
<form>
  <h1>Add price and inventory</h1>
  <kat-input id="skuHost" name="contribution_sku-0-value"></kat-input>
  <div>
    <label><input type="radio" name="fc" value="AMAZON_NA"> I want to use Fulfilled by Amazon (FBA) to ship my items and provide customer service if they sell. (Fulfilled by Amazon)</label>
    <label><input type="radio" name="fc" value="DEFAULT" id="mf"> I want to ship this item myself or use Amazon Easy Ship if it sells. (Merchant Fulfilled)</label>
  </div>
  <div id="qtyBox"></div>
  <label for="price">Your Price</label><span>USD$</span><input id="price" name="purchasable_offer-0-our_price-0-value" placeholder="Example: 9.00">
  <div id="advice"></div>
  <div><kat-label>Item Condition</kat-label><kat-dropdown id="cond" name="condition_type-0-value" placeholder="Example: New" value="${preset}"></kat-dropdown></div>
  <kat-button id="submit" label="Submit"></kat-button>
  <kat-button id="cancel" label="Cancel"></kat-button>
</form>
<script>
  class KatInput extends HTMLElement {
    connectedCallback() {
      const shadow = this.attachShadow({ mode: 'open' });
      shadow.innerHTML = '<label>SKU</label><input type="text" aria-label="SKU" name="' + this.getAttribute('name') + '">';
      this.input = shadow.querySelector('input');
    }
    get value() { return this.input ? this.input.value : ''; }
  }
  class KatButton extends HTMLElement {
    connectedCallback() {
      const shadow = this.attachShadow({ mode: 'open' });
      shadow.innerHTML = '<button type="button">' + this.getAttribute('label') + '</button>';
      shadow.querySelector('button').addEventListener('click', () => { window.pressed = (window.pressed || []).concat(this.getAttribute('label')); });
    }
  }
  class KatDropdown extends HTMLElement {
    constructor() {
      super();
      this.options = [{ name: 'New', value: 'new_new' }, { name: 'New - OEM', value: 'new_oem' }, { name: 'Used - Like New', value: 'used_like_new' },
        { name: 'Used - Very Good', value: 'used_very_good' }, { name: 'Used - Good', value: 'used_good' }, { name: 'Used - Acceptable', value: 'used_acceptable' }];
      this._value = '';
      this.opened = 0;
    }
    connectedCallback() {
      this._value = this.getAttribute('value') || '';
      const shadow = this.attachShadow({ mode: 'open' });
      shadow.innerHTML = '<div class="kat-select-container" aria-label="Item Condition"><div class="select-header" part="dropdown-header" tabindex="0"><div class="selection-text"></div><div class="placeholder-text">Example: New</div></div>'
        + '<div class="select-options" hidden><slot role="listbox">' + this.options.map((o, i) => '<kat-option part="dropdown-option' + i + '" value="' + o.value + '" role="option" aria-selected="false"><div class="standard-option-name">' + o.name + '</div></kat-option>').join('') + '</slot></div></div>';
      const list = shadow.querySelector('.select-options');
      shadow.querySelector('.select-header').addEventListener('click', () => { list.hidden = !list.hidden; this.opened += 1; });
      for (const option of shadow.querySelectorAll('kat-option')) option.addEventListener('click', () => {
        if (list.hidden) return;  // a closed list takes no clicks, like the real one
        this._value = option.getAttribute('value'); list.hidden = true;
        this.dispatchEvent(new CustomEvent('change', { bubbles: true, composed: true, detail: { value: this._value } }));
        this.paint();
      });
      this.paint();
    }
    paint() {
      const hit = this.options.find(o => o.value === this._value);
      const text = this.shadowRoot.querySelector('.selection-text');
      if (text) text.textContent = hit ? hit.name : '';
    }
    get value() { return this._value; }
    set value(v) { this._value = v; this.paint(); }
  }
  customElements.define('kat-input', KatInput);
  customElements.define('kat-button', KatButton);
  customElements.define('kat-dropdown', KatDropdown);
  // The quantity box only exists once the fulfilment radio is answered, as on the real page.
  document.getElementById('mf').addEventListener('change', () => {
    document.getElementById('qtyBox').innerHTML = '<label for="qty">Quantity</label><input id="qty" name="fulfillment_availability-0-quantity" type="text">';
  });
  setTimeout(() => {
    document.getElementById('panel').innerHTML = '<p>ASIN: B0DGMVJJLJ</p><p>Competing Marketplace Offers: 2 New from $29.40 + $0.00 shipping</p>';
    document.getElementById('advice').innerHTML = '<a href="#" class="link" id="match">Match lowest price: USD$29.40</a>';
    document.getElementById('match').addEventListener('click', event => {
      event.preventDefault();
      const box = document.getElementById('price'); box.value = '29.40'; box.dispatchEvent(new Event('input', { bubbles: true }));
    });
  }, ${lateMs});
</script></body></html>`;
}

const VALUES = { upc: '048552604666', sku: '048552604666', title: 'Amelia 42pc Dinnerware Set', price: 34.99, quantity: 1, condition: 'NEW' };

async function openOffer(context, shape) {
  const page = await context.newPage();
  await page.route('https://sellercentral.amazon.com/**', route => route.fulfill({ contentType: 'text/html', body: offerPage(shape) }));
  await page.addInitScript(() => {
    window.sent = [];
    window.chrome = { runtime: { onMessage: { addListener() {} }, sendMessage: m => { window.sent.push(m); return Promise.resolve(); } },
      storage: { local: { get: () => Promise.resolve({}), set: () => Promise.resolve() } } };
  });
  await page.goto('https://sellercentral.amazon.com/interactive/listing/workflow/offer/offer?asin=B0DGMVJJLJ');
  await page.evaluate(matcherSrc);
  await page.evaluate(contentSrc);
  return page;
}

const fill = page => page.evaluate(v => window.__ssLister.fill({ values: v, store: 'amazon' }), VALUES);
const condition = page => page.evaluate(() => document.getElementById('cond').value);
const skuValue = page => page.evaluate(() => document.getElementById('skuHost').value);
const pressed = page => page.evaluate(() => window.pressed || []);
const sent = page => page.evaluate(() => window.sent.map(m => m.type));

(async () => {
  const browser = await chromium.launch({ channel: 'msedge' });
  const context = await browser.newContext();
  try {
    // Everything drawn: SKU typed, merchant radio, quantity as it appears, the link pressed, condition picked.
    let page = await openOffer(context, {});
    await page.waitForSelector('#match');
    let report = await fill(page);
    assert.equal(await skuValue(page), '048552604666', 'our code is the SKU');
    assert.equal(await page.inputValue('#price'), '29.40', 'the Match lowest price link put the lowest price in');
    assert.equal(await condition(page), 'new_new', 'the Katal condition dropdown holds New');
    assert.ok(report.filled.some(f => f.target === 'condition' && f.label === 'Item Condition'), 'reported as the Item Condition');
    await page.waitForFunction(() => document.getElementById('qty') && document.getElementById('qty').value === '1', null, { timeout: 5000 });
    await page.close();

    // The offer panel draws late: our price first, then the link is found and pressed.
    page = await openOffer(context, { lateMs: 1500 });
    report = await fill(page);
    assert.equal(await page.inputValue('#price'), '34.99', 'our price until the link shows');
    await page.waitForFunction(() => document.getElementById('price').value === '29.40', null, { timeout: 8000 });
    await page.close();

    // The user typed a price before the panel drew: left alone.
    page = await openOffer(context, { lateMs: 1500 });
    await fill(page);
    await page.fill('#price', '41.00');
    await page.waitForSelector('#match');
    await page.waitForTimeout(1500);
    assert.equal(await page.inputValue('#price'), '41.00', 'a price the user typed is never written over');
    await page.close();

    // The HUD: quantity, price, then the condition as a checkpoint; Next past the last one presses Submit.
    page = await openOffer(context, {});
    await page.waitForSelector('#match');
    await fill(page);
    await page.waitForFunction(() => document.getElementById('qty') && document.getElementById('qty').value === '1', null, { timeout: 5000 });
    let state = await page.evaluate(v => window.__ssLister.guideStart({ values: v, store: 'amazon', confirmFields: ['quantity', 'price'] }), VALUES);
    const names = state.rows.map(r => r.target);
    assert.deepEqual(names.slice(0, 3), ['quantity', 'price', 'condition'], 'quantity, price, then the condition come first: ' + names.join(','));
    const cond = state.rows.find(r => r.target === 'condition');
    assert.ok(cond.done && cond.check && cond.value === 'New', 'the filled condition is a checkpoint showing New: ' + JSON.stringify(cond));
    assert.equal(state.rows.filter(r => !r.done).length, 0, 'nothing is empty: ' + JSON.stringify(state.rows.filter(r => !r.done)));
    assert.deepEqual(await pressed(page), [], 'Submit is not pressed while checkpoints are still to be seen');
    // Walk: the start stands on quantity; Next -> price, Next -> condition, Next -> past the end.
    assert.equal(state.index, 0, 'the walk starts on quantity');
    for (let i = 0; i < 2; i++) state = await page.evaluate(() => window.__ssLister.guideNext());
    assert.equal(state.index, 2, 'the walk stands on the condition (third checkpoint)');
    assert.deepEqual(await pressed(page), [], 'still nothing pressed while standing on the condition');
    state = await page.evaluate(() => window.__ssLister.guideNext());
    assert.equal(state.open, 0, 'every checkpoint seen');
    await page.waitForTimeout(600);
    assert.deepEqual(await pressed(page), ['Submit'], 'stepping past the last checkpoint presses the page\'s Submit');
    assert.ok((await sent(page)).includes('ss-lister-submitted'), 'the panel is told the listing was submitted');
    await page.close();

    // Guide without a fill on an empty condition: the row is open; Use picks our condition, then the walk goes on.
    page = await openOffer(context, {});
    await page.waitForSelector('#match');
    await page.evaluate(v => window.__ssLister.fill({ values: { ...v, condition: '' }, store: 'amazon' }), VALUES);
    await page.waitForFunction(() => document.getElementById('qty') && document.getElementById('qty').value === '1', null, { timeout: 5000 });
    state = await page.evaluate(v => window.__ssLister.guideStart({ values: v, store: 'amazon', confirmFields: ['quantity', 'price'] }), VALUES);
    let row = state.rows.find(r => r.target === 'condition');
    assert.ok(row && !row.done && row.suggestion === 'New', 'the empty condition is open with our suggestion: ' + JSON.stringify(row));
    state = await page.evaluate(() => window.__ssLister.guideGo(2));
    assert.equal(state.rows[state.index].target, 'condition');
    state = await page.evaluate(() => window.__ssLister.guideUse());
    assert.equal(await condition(page), 'new_new', 'Use puts our condition into the dropdown');
    await page.close();

    // The user presses the page's own Submit (inside the kat-button's shadow root): the panel hears of it once.
    page = await openOffer(context, {});
    await page.waitForSelector('#match');
    await fill(page);
    await page.evaluate(() => document.getElementById('submit').shadowRoot.querySelector('button').click());
    await page.evaluate(() => document.getElementById('submit').shadowRoot.querySelector('button').click());
    const messages = await page.evaluate(() => window.sent.filter(m => m.type === 'ss-lister-submitted'));
    assert.equal(messages.length, 1, "one submitted message for the user's own press: " + JSON.stringify(messages));
    assert.equal(messages[0].store, 'amazon');
    assert.equal(messages[0].own, true, "flagged as the user's own press");
    assert.deepEqual(await pressed(page), ['Submit', 'Submit'], 'the page still gets the click');
    await page.evaluate(() => document.getElementById('cancel').shadowRoot.querySelector('button').click());
    assert.equal((await page.evaluate(() => window.sent.filter(m => m.type === 'ss-lister-submitted'))).length, 1, 'Cancel is not a submit');
    await page.close();

    console.log('lister Amazon condition + submit: ok');
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error); process.exit(1); });
