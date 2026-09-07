const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const html = fs.readFileSync('templates/finder.html', 'utf8').replaceAll('\r\n', '\n');
const code = html.slice(html.indexOf('function allDataButton('), html.indexOf('// Image popup\n'));
assert.ok(code.length > 100);
const elements = new Map();
function element() {
  return {value: '', checked: false, _text: '', children: [], events: {}, attributes: {}, innerHTML:'',
    set textContent(value) {this._text=value; this.children=[];}, get textContent() {return this._text;},
    addEventListener(name, callback) {this.events[name]=callback;}, setAttribute(name, value) {this.attributes[name]=value;},
    insertAdjacentHTML(position, html) {this.innerHTML += html;}, appendChild(child) { this.children.push(child); }};
}
const pending = [];
const escape = value => String(value ?? '').replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;').replaceAll('"', '&quot;').replaceAll("'", '&#39;');
const context = {URLSearchParams, AbortController, esc: escape, attrEsc: escape,
  showPrepPopupHtml(html) {context.dialog=html;},
  document: {
    getElementById(id) { if (!elements.has(id)) elements.set(id, element()); return elements.get(id); },
    createElement: element,
  },
  fetch(url, options) { return new Promise(resolve => pending.push({url, options, resolve})); },
};
vm.createContext(context);
vm.runInContext(code, context);
(async () => {
  const unsafe = context.recordHtml({title: '<img src=x onerror=alert(1)>', note: '<script>x</script>', quantity: 0, undone: false});
  assert.ok(!unsafe.includes('<script>'));
  assert.ok(unsafe.includes('&lt;img'));
  assert.ok(unsafe.includes('Qty: 0'));
  assert.ok(!unsafe.includes('<details>'));
  assert.ok(context.recordDetailsHtml({quantity: 0, undone: false}).includes('<dd>false</dd>'));
  assert.equal(context.recordCategory({id:'preplog.db:prep_log'}), 'Prep');
  assert.equal(context.recordCategory({id:'sold.db:orders'}), 'Sales & returns');
  assert.equal(context.recordSourceName({id:'sold.db:orders'}), 'Sold orders');
  assert.equal(context.isPrimaryRecordSource({id:'sold.db:finder_alias_changes'}), false);
  assert.ok(context.recordStatusBadge('good', false).includes('record-badge good'));
  assert.ok(context.recordStatusBadge('bad', false).includes('record-badge bad'));
  assert.ok(context.recordStatusBadge('good', true).includes('Undone'));
  assert.ok(!context.recordDetailsHtml({title:'Frame', source_row_json:'internal', id:34}).includes('internal'));
  assert.ok(context.recordDetailsHtml({source_row_json:'internal'}, true).includes('internal'));
  assert.equal(context.recordOriginalLink({id:'sold.db:orders'}, {order_id:'A & B',barcode:'123'}).href, '/searchrack?db=sold&q=A+%26+B');
  assert.equal(context.recordOriginalLink({id:'rackhistory.db:removed_items'}, {barcode:'123'}).href, '/searchrack?db=searchRack&q=123&open_history=1&history_search=123');
  assert.equal(context.recordOriginalLink({id:'preplog.db:prep_log'}, {upc:'123-2',lot_number:'LOT 8'}).href, '/item-prep/diagnostic/view?upc=123-2&lot=LOT+8');
  assert.equal(context.recordOriginalLink({id:'listinglog.db:listing_log'}, {upc:'123'}).href, '/listing-log?upc=123');
  assert.ok(!context.recordOriginalLink({id:'ebayStore.db:INVENTORY'}, {upc:'123',url:'javascript:alert(1)'}).href.startsWith('javascript:'));
  assert.equal(context.recordDate('2026-09-07'), 'Sep 7, 2026');
  assert.ok(context.recordHtml({order_id:'order-7', quantity:2, price:12.5}, {id:'sold.db:orders'}).includes('$12.50'));
  assert.ok(!context.allDataButton('" onclick="alert(1)').includes(' onclick="'));
  const first = context.showAllUpcData('111111111111');
  const second = context.showAllUpcData('222222222222');
  assert.ok(pending[0].options.signal.aborted);
  pending[1].resolve({ok: true, json: async () => ({sources: [], errors: [], unavailable: []})});
  await second;
  pending[0].resolve({ok: true, json: async () => ({sources: [], errors: ['Stale error'], unavailable: []})});
  await first;
  assert.ok(!elements.get('recordsResults').children.some(child => child.textContent.includes('Stale error')));
  assert.equal(elements.get('recordsUpc').value, '222222222222');
  assert.ok(elements.get('recordsResults').children.some(child => child.textContent === 'No matching records found.'));
  const failed = context.showAllUpcData('333333333333');
  pending[2].resolve({ok: false, json: async () => ({error: 'Unavailable'})});
  await failed;
  assert.equal(elements.get('recordsResults').textContent, 'Unavailable');
  const populated = context.showAllUpcData('444444444444');
  pending[3].resolve({ok:true,json:async()=>({sources:[
    {id:'sold.db:orders',total:2,next_offset:1,records:[{id:1,order_id:'SALE-1',quantity:1,price:12.5}]},
    {id:'preplog.db:prep_log',total:1,next_offset:null,records:[{status:'good',note:'Checked corners'}]},
    {id:'sold.db:finder_aliases',total:1,next_offset:null,records:[{title:'Supporting name',barcode_key:'444444444444'}]},
  ],errors:[],unavailable:[]})});
  await populated;
  const root = elements.get('recordsResults');
  root.children.find(child=>child.className==='record-nav').children.find(child=>child.textContent.startsWith('Sales & returns')).events.click();
  let sections = root.children.filter(child=>child.className==='record-source');
  assert.equal(sections.length, 1);
  assert.ok(sections[0].children[1].innerHTML.includes('SALE-1'));
  const more = sections[0].children[2];
  const paging = more.events.click();
  pending[4].resolve({ok:true,json:async()=>({sources:[{id:'sold.db:orders',next_offset:null,records:[{id:2,order_id:'SALE-2'}]}]})});
  await paging;
  assert.ok(sections[0].children[1].innerHTML.includes('SALE-2'));
  assert.equal(more.hidden, true);
  root.events.click({target:{closest:()=>({dataset:{recordSource:'sold.db:orders',recordIndex:'1'}})}});
  assert.ok(context.dialog.includes('SALE-2'));
  root.children.find(child=>child.className==='record-nav').children.find(child=>child.textContent.startsWith('Prep')).events.click();
  sections=root.children.filter(child=>child.className==='record-source');
  assert.equal(sections.length, 1);
  assert.ok(sections[0].children[1].innerHTML.includes('Checked corners'));
  root.children.find(child=>child.className==='record-nav').children.find(child=>child.textContent==='Sales & returns').events.click();
  assert.equal(root.children.filter(child=>child.className==='record-source').length, 1);
  elements.get('recordsSupporting').checked=true;
  elements.get('recordsSupporting').events.change();
  assert.equal(root.children.filter(child=>child.className==='record-source').length, 2);
  console.log('UPC record rendering, escaping, stale requests, and errors passed');
})().catch(error => { console.error(error); process.exitCode = 1; });
