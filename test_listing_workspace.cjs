const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const html = fs.readFileSync('templates/listingagent.html','utf8');
for (const match of html.matchAll(/<script(?:\s[^>]*)?>([\s\S]*?)<\/script>/g)) new vm.Script(match[1]);
new vm.Script(fs.readFileSync('static/listing-workspace.js','utf8'));
function functionSource(name) {
  const start = html.indexOf(`    function ${name}(`);
  assert.ok(start >= 0,name);
  return html.slice(start,html.indexOf('\n    function ',start+10));
}
const ctx = {
  Number, Set, state:{},
  $: () => null, _isBlank: value => !String(value ?? '').trim(),
  clearFieldHighlights(){},markFieldInvalid(){},
};
vm.createContext(ctx);
vm.runInContext(functionSource('_getEbayCompletionMissing'),ctx);
vm.runInContext(functionSource('validateEbayPayload'),ctx);
const valid = {upc:'123456789012',sku:'SS-1',title:'Test item',condition:'USED_GOOD',listingDescription:'Tested, working item.',categoryId:'123',fulfillmentPolicyId:'ship',paymentPolicyId:'pay',returnPolicyId:'return',price:19.99,quantity:1,images:['https://example.com/photo.jpg']};
assert.equal(ctx.validateEbayPayload(valid,'publish').ok,true);
assert.equal(ctx._getEbayCompletionMissing(valid).length,0);
for(const patch of [{price:0},{price:-1},{price:Infinity},{quantity:0},{quantity:1.5},{quantity:NaN},{title:'x'.repeat(81)},{images:[]},{categoryId:''}]) {
 const payload={...valid,...patch};
 assert.equal(ctx.validateEbayPayload(payload,'publish').ok,false,JSON.stringify(patch));
 assert.ok(ctx._getEbayCompletionMissing(payload).length,JSON.stringify(patch));
}
vm.runInContext(functionSource('_getAmazonCompletionMissing'),ctx);
vm.runInContext(functionSource('validateAmazonPayload'),ctx);
const amazon={upc:'123',sku:'123',price:10,quantity:1,conditionType:'new_new',manualMode:false};
assert.equal(ctx.validateAmazonPayload(amazon).ok,true);
for(const patch of [{price:0},{quantity:1.5},{conditionType:'used_good',conditionNote:''},{upc:'',asin:''}]) {
 const payload={...amazon,...patch};
 assert.equal(ctx.validateAmazonPayload(payload).ok,false);
 assert.ok(ctx._getAmazonCompletionMissing(payload).length);
}
const start=html.indexOf('    let workingDraftSaveInFlight = null;');
const end=html.indexOf('    async function clearWorkingDraftForActiveItem()',start);
const state={activeUpc:'123',workingDraftDirty:true,workingDrafts:{}};
let value='first'; let resolveSave; let fail=false; let defer=false; let writes=0;
const saveCtx={state,Event:class{},document:{dispatchEvent(){}},$:()=>null,
 _workingDraftKey:String,syncDescFromEditor(){},renderQueue(){},
 _captureWorkingDraft:()=>({ts:Date.now(),fields:{title:value}}),
 apiPost:async()=>{writes++;if(fail)throw new Error('offline');if(defer)await new Promise(r=>resolveSave=r);return {};}
};
vm.createContext(saveCtx);vm.runInContext(html.slice(start,end),saveCtx);
(async()=>{
 assert.equal(await saveCtx.saveWorkingDraftForActiveItem(),true);
 assert.equal(state.workingDraftDirty,false);assert.equal(state.workingDraftSaveStatus,'saved');
 fail=true;state.workingDraftDirty=true;
 assert.equal(await saveCtx.saveWorkingDraftForActiveItem(),false);
 assert.equal(state.workingDraftDirty,true);assert.equal(state.workingDraftSaveStatus,'error');
 fail=false;defer=true;
 const pending=saveCtx.saveWorkingDraftForActiveItem();
 value='edited during save';resolveSave();await pending;
 assert.equal(state.workingDraftDirty,true,'in-flight edits must remain dirty');
 defer=false;await saveCtx.saveWorkingDraftForActiveItem();
 assert.equal(state.workingDraftDirty,false);
 assert.equal(state.workingDrafts['123'].fields.title,'edited during save');
 assert.equal(writes,4);
 console.log('PASS: script syntax, eBay/Amazon preflight (13 invalid cases), save success/failure and in-flight edits.');
})().catch(e=>{console.error(e);process.exitCode=1;});
