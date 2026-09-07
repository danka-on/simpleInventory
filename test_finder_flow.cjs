const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const html = fs.readFileSync('templates/finder.html', 'utf8');
const matchCode = html.slice(html.indexOf('let matchInFlight = false;'), html.indexOf('// Refresh the opener window (ready-to-ship)'));
async function check(listing) {
  const sent = [];
  const banner = {style: {}};
  const context = {
    orderId: listing ? '' : '1', isListingMatch: listing,
    listingStore: 'amazon', listingKey: 'sku1', listingId: '', orderTitle: 'Gallery frame', orderBarcode: '999999999999',
    _lastMatch: null, t: key => key, esc: String, formatUpcDisplay: String,
    document: {getElementById: id => id === 'successBanner' ? banner : null},
    refreshOpenerWindow() {}, setTimeout() {}, alert(message) {throw new Error(message);},
    async fetch(url, options) {
      sent.push({url, body: JSON.parse(options.body)});
      return {json: async () => ({success: true, finder_alias_undo: 'undo-token', old_barcode: '999999999999'})};
    }
  };
  vm.createContext(context);
  vm.runInContext(matchCode, context);
  await context.handleMatch(1, '123456789012', 'A1', null);
  assert.equal(sent[0].body.finder_learn, true);
  await context.undoMatch();
  assert.equal(sent[1].body.finder_alias_undo, 'undo-token');
  assert.equal(sent[1].body.finder_learn, undefined);
  if (listing) assert.equal(sent[1].body.clear, true);
}
(async () => {await check(false); await check(true); console.log('Finder order/listing learning and undo client checks passed');})().catch(error => {console.error(error); process.exitCode = 1;});
