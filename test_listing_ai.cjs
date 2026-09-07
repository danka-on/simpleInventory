const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const html = fs.readFileSync('templates/listingagent.html', 'utf8');
const start = html.indexOf("    if($('btnAiDesc')) $('btnAiDesc').onclick");
const end = html.indexOf("    $('btnClearDesc').onclick", start);
assert.ok(start >= 0 && end > start);

async function check(button, response, {switchItem = false, fail = false, supersede = false} = {}) {
  const fields = {
    btnAiDesc: {innerHTML: 'Description'}, btnAiTitle: {innerHTML: 'Title'},
    title: {value: 'Original', dispatchEvent() {}}, upc: {value: '123456789012'},
  };
  const logs = [];
  let complete, description = '';
  const state = {activeUpc: '123456789012-2', item: {title: 'Inventory title'}, aiDescReqId: 0, aiTitleReqId: 0};
  const ctx = {
    state, $: id => fields[id], _workingDraftKey: value => value,
    categorySelect: () => null, log: message => logs.push(message),
    setDescHtml: value => { description = value; }, Event: class {},
    apiPost: (url, payload) => {
      assert.equal(payload.upc, '123456789012');
      return new Promise((resolve, reject) => { complete = () => fail ? reject(new Error('API unavailable')) : resolve(response); });
    },
  };
  vm.createContext(ctx);
  vm.runInContext(html.slice(start, end), ctx);
  const pending = fields[button].onclick();
  assert.equal(fields[button].disabled, true);
  if (switchItem) state.activeUpc = '123456789012-3';
  if (supersede) { state.aiDescReqId++; state.aiTitleReqId++; }
  complete();
  await pending;
  assert.equal(fields[button].disabled, false);
  if (switchItem || supersede) {
    assert.equal(fields.title.value, 'Original');
    assert.equal(description, '');
    assert.equal(logs.length, 0);
  } else if (fail) {
    assert.match(logs[0], /failed: API unavailable/);
  } else if (button === 'btnAiTitle') {
    assert.equal(fields.title.value, response.title);
  } else {
    assert.equal(description, response.description);
  }
}

(async () => {
  for (const button of ['btnAiTitle', 'btnAiDesc']) {
    const response = {success: true, source: 'claude', title: 'Claude title', description: '<p>Claude description</p>'};
    for (const options of [{}, {fail: true}, {switchItem: true}, {switchItem: true, fail: true}, {supersede: true}]) {
      await check(button, response, options);
    }
  }
  console.log('PASS: both AI buttons apply suffixed-item responses, display errors, and reject stale responses.');
})().catch(error => { console.error(error); process.exitCode = 1; });
