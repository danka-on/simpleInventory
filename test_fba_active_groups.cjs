const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const html=fs.readFileSync('templates/fba_prep.html','utf8');
const storage=new Map(), requests=[];
const box=(id,group)=>({local_id:id,packing_group_id:group});
const state={sessionId:4,activeBoxId:'',amazon:{boxes:[box('A1','G1'),box('A2','G1'),box('B1','G2'),box('B2','G2')],packing_groups:[{packing_group_id:'G1',items:[{msku:'SKU1'}]},{packing_group_id:'G2',items:[{msku:'SKU2'}]}]},workingLocations:[],clientId:'test'};
const input={classList:{remove(){}},disabled:false};
const context=vm.createContext({state,localStorage:{getItem:k=>storage.get(k),setItem:(k,v)=>storage.set(k,v),removeItem:k=>storage.delete(k)},
 operatorName:()=>"Test", $:()=>input,setStatus(){},setRouteCallout(){},renderAmazonWorkflow(){},focusBarcodeInput(){},speakRoute(){},showScanRejection:()=>false,
 plannedRowForScan:code=>({seller_sku:code,barcode:code,quantity:1}),scanRejection:()=>null,
 expectedByMsku:()=>new Map(),packedByMsku:()=>new Map(),rowNeedsAmazonLabel:()=>false,newToken:()=> 'scan-test',
 fetch:async(url,options)=>{requests.push(JSON.parse(options.body));throw Error('Stop before any mutation');}
});
function block(a,b){return html.slice(html.indexOf(a),html.indexOf(b,html.indexOf(a)))}
vm.runInContext(block('    const activeBoxPreferenceKey','    const $='),context);
vm.runInContext(block('    async function packScannedUnit(','    function sessionSnapshot'),context);
(async()=>{
 context.setActiveBoxId('A1');context.setActiveBoxId('B2');
 assert.equal(context.activeBoxForGroup('G1'),'A1');assert.equal(context.activeBoxForGroup('G2'),'B2');
 await context.packScannedUnit('SKU1');await context.packScannedUnit('SKU2');await context.packScannedUnit('SKU1');
 assert.deepEqual(requests.map(r=>r.active_box_id),['A1','B2','A1']);
 context.setActiveBoxId('A2');assert.equal(context.activeBoxForGroup('G2'),'B2');
 state.activeBoxesSessionKey=null;state.activeGroupBoxes={};
 assert.equal(context.activeBoxForGroup('G1'),'A2');assert.equal(context.activeBoxForGroup('G2'),'B2');
 state.amazon.boxes=state.amazon.boxes.filter(b=>b.local_id!=='A2');
 assert.equal(context.activeBoxForGroup('G1'),'A1');assert.equal(context.activeBoxForGroup('G2'),'B2');
 state.sessionId=5;context.setActiveBoxId('B1');
 state.sessionId=4;assert.equal(context.activeBoxForGroup('G2'),'B2');
 context.forgetActiveBoxId(4);assert.equal(storage.has('fba_active_box_4_groups'),false);
 console.log('Per-group carton selection, scan routing, reload, removal, and session isolation passed.');
})().catch(e=>{console.error(e);process.exitCode=1});
