const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const html = fs.readFileSync('templates/listingagent.html', 'utf8');
const source = html.slice(html.indexOf('    function collectPayload(){'), html.indexOf('    function collectAmazonPayload(){'));
const ctx = {
  state: {activeUpc:'882864391223-1', images:[]},
  $: () => ({value:'882864391223'}),
  syncDescFromEditor(){}, getDescHtml:()=>'', collectEbayAspects:()=>({}),
  _effectiveEbayImages:images=>images, _isImageEnabled:()=>true,
};
vm.createContext(ctx); vm.runInContext(source, ctx);
assert.equal(ctx.collectPayload().upc, '882864391223-1');
ctx.state.activeUpc = '882864391223-2';
assert.equal(ctx.collectPayload().upc, '882864391223-2');
ctx.state.activeUpc = '';
assert.equal(ctx.collectPayload().upc, '882864391223');
console.log('PASS: selected suffixed identity survives base-UPC form data.');
