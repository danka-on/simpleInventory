// The naming prompt on both receiving screens is a two-step flow: a name, typed or
// dictated through /api/warehouse/name-dictation, then optional photos. Abandoning it
// must never add the item, and photos are saved before the item is released.
// /barcode also gets two single-scan regressions that used to skip an entry.
const {chromium}=require('playwright');
const fs=require('node:fs');
const path=require('node:path');
const assert=require('node:assert/strict');

const NEEDS_NAME='234567890123';
const HAS_IMAGE='345678901234';
const KNOWN='123456789012';
const NEXT='210987654321';
const DICTATED='Lenox Tuscany Classics wine glasses, set of 4';
const IPHONE='Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15';

function serveStatic(route,url) {
 const file=path.join(__dirname,url.pathname);
 if(fs.existsSync(file)) return route.fulfill({contentType:file.endsWith('.js')?'application/javascript':'text/plain',body:fs.readFileSync(file)});
 return route.fulfill({contentType:'application/json',body:'{}'});
}

// A scripted microphone: the recorder hands back one small clip when stopped.
function fakeMicrophone() {
 localStorage.setItem('scannerMode','handheld');
 localStorage.setItem('deviceType','iPhone');
 Object.defineProperty(navigator,'mediaDevices',{configurable:true,value:{getUserMedia:async()=>new MediaStream()}});
 window.__recorders=[];
 window.MediaRecorder=class {
  constructor(stream,options) { this.mimeType=(options&&options.mimeType)||'audio/webm'; this.state='inactive'; window.__recorders.push(this); }
  static isTypeSupported(type) { return type.startsWith('audio/webm'); }
  start() { this.state='recording'; }
  stop() {
   this.state='inactive';
   if(this.ondataavailable) this.ondataavailable({data:new Blob(['fake clip'],{type:this.mimeType})});
   if(this.onstop) this.onstop();
  }
 };
}

async function namingFlow(browser,template) {
 const calls=[];
 const reply={dictation:{status:200,json:{success:true,text:DICTATED}},thumbnail:200};
 const page=await browser.newPage({userAgent:IPHONE,viewport:{width:390,height:844},isMobile:true,hasTouch:true});
 const errors=[];page.on('pageerror',e=>errors.push(e.message));
 await page.addInitScript(fakeMicrophone);
 await page.route('**/*',async route=>{
  const request=route.request();
  const url=new URL(request.url());
  const p=url.pathname;
  if(p==='/'+template) return route.fulfill({contentType:'text/html',body:fs.readFileSync(path.join(__dirname,'templates',template+'.html'),'utf8')});
  if(p==='/api/add-item/screen') {
   const code=request.postDataJSON().barcodes[0];
   return route.fulfill({json:{success:true,items:{[code]:{title:'',needs_manual_title:true,missing_image:code!==HAS_IMAGE,image_url:code===HAS_IMAGE?'/static/custom_items/x.jpg':''}}}});
  }
  if(p==='/api/custom-item/identity') { calls.push({path:p,body:request.postDataJSON()}); return route.fulfill({json:{success:true}}); }
  if(p==='/api/warehouse/name-dictation') { calls.push({path:p,body:request.postDataBuffer().toString('latin1')}); return route.fulfill({status:reply.dictation.status,json:reply.dictation.json}); }
  if(p==='/api/items-prep/temp-item') {
   calls.push({path:p,body:request.postDataJSON()});
   return reply.thumbnail===200
    ? route.fulfill({json:{success:true,image_url:'/static/custom_items/'+NEEDS_NAME+'.jpg'}})
    : route.fulfill({status:500,json:{success:false,error:'Disk full'}});
  }
  if(p.startsWith('/api/items_prep/diagnostic/')) { calls.push({path:p,body:request.postDataBuffer().toString('latin1')}); return route.fulfill({json:{success:true,images:[]}}); }
  if(p.startsWith('/static/')) return serveStatic(route,url);
  return route.fulfill({contentType:'application/json',body:'{}'});
 });
 await page.goto('http://scanner.test/'+template);
 await page.waitForTimeout(300);

 const label=template;
 const modal=page.locator('#missingTitleModal');
 const mic=page.locator('#warehouseDictateBtn');
 const runGate=code=>page.evaluate(c=>{window.__gate=ensureScannedBarcodeReadyForAdd(c)},code);
 const gateResult=()=>page.evaluate(()=>window.__gate);
 const gatePending=()=>page.evaluate(()=>Promise.race([window.__gate,new Promise(r=>setTimeout(()=>r('pending'),250))]));
 const jpeg=Buffer.from((await page.evaluate(()=>{
  const canvas=document.createElement('canvas');canvas.width=40;canvas.height=30;
  const g=canvas.getContext('2d');g.fillStyle='#c00';g.fillRect(0,0,40,30);
  return canvas.toDataURL('image/jpeg');
 })).split(',')[1],'base64');
 const addPhoto=async count=>{
  await page.locator('#missingTitlePhotoInput').setInputFiles({name:'photo.jpg',mimeType:'image/jpeg',buffer:jpeg});
  await page.waitForFunction(n=>document.querySelectorAll('#warehousePhotoStrip img').length===n,count);
 };

 // 1. Dictate, confirm, take photos, retry a failed save.
 await runGate(NEEDS_NAME);
 await modal.waitFor({state:'visible'});
 assert.equal(await page.locator('#warehouseIdentitySteps').isVisible(),true,label+': an item without a picture shows both steps');
 assert.equal(await page.locator('#missingTitleHeading').textContent(),'Name this item');
 assert.equal(await mic.isVisible(),true,label+': the mic button is offered');
 assert.notEqual(await page.evaluate(()=>document.activeElement&&document.activeElement.id),'missingTitleInput',label+': the keyboard must not pop over the mic on a touch screen');
 await mic.click();
 await page.waitForFunction(()=>document.getElementById('warehouseDictateBtn').dataset.state==='listening');
 await mic.click();
 await page.waitForFunction(text=>document.getElementById('missingTitleInput').value===text,DICTATED);
 const clip=calls.find(c=>c.path==='/api/warehouse/name-dictation');
 assert.ok(clip&&clip.body.includes('name="audio"; filename="name.webm"'),label+': the recording is uploaded as a webm clip');
 assert.match(await page.locator('#warehouseIdentityStatus').textContent(),/Check it against the label/);
 assert.equal(await mic.getAttribute('data-state'),'idle');

 await page.locator('#missingTitleSaveBtn').click();
 await page.locator('#missingTitlePhotoSection').waitFor({state:'visible'});
 assert.equal(await page.locator('#warehouseNameStep').isVisible(),false,label+': confirming the name moves on to photos');
 assert.equal(await page.locator('#warehouseConfirmedName').textContent(),DICTATED);
 assert.deepEqual(calls.filter(c=>c.path==='/api/custom-item/identity').map(c=>c.body),[{upc:NEEDS_NAME,title:DICTATED}],label+': the name is saved before photos');
 assert.equal(await gatePending(),'pending',label+': the item waits for the photo step');
 assert.equal(await page.locator('#warehousePhotoDoneBtn').isDisabled(),true,label+': nothing to save before a photo');

 await addPhoto(1);
 await addPhoto(2);
 await addPhoto(3);
 await page.locator('#warehousePhotoStrip button').first().click();
 await page.waitForFunction(()=>document.querySelectorAll('#warehousePhotoStrip img').length===2);
 assert.equal(await page.locator('#warehousePhotoDoneBtn').textContent(),'Save 2 photos');
 assert.match(await page.locator('#missingTitlePhotoBtn').textContent(),/Add another photo/);

 reply.thumbnail=500;
 await page.locator('#warehousePhotoDoneBtn').click();
 await page.waitForFunction(()=>/Disk full/.test(document.getElementById('missingTitlePhotoStatus').textContent));
 assert.equal(await modal.isVisible(),true,label+': a failed photo save keeps the flow open');
 assert.equal(await gatePending(),'pending');
 reply.thumbnail=200;
 await page.locator('#warehousePhotoDoneBtn').click();
 assert.deepEqual(await gateResult(),{ready:true,title:DICTATED},label+': saved photos release the item');
 await modal.waitFor({state:'hidden'});
 const thumbnails=calls.filter(c=>c.path==='/api/items-prep/temp-item');
 assert.equal(thumbnails.length,2,label+': the thumbnail save is retried');
 assert.equal(thumbnails[1].body.upc,NEEDS_NAME);
 assert.equal(thumbnails[1].body.item_description,DICTATED);
 assert.match(thumbnails[1].body.image_data,/^data:image\/jpeg;base64,/);
 const gallery=calls.filter(c=>c.path.startsWith('/api/items_prep/diagnostic/'));
 assert.deepEqual(gallery.map(c=>c.path),['/api/items_prep/diagnostic/'+NEEDS_NAME+'/photos'],label+': all photos go to the item in one upload');
 assert.equal((gallery[0].body.match(/name="photos\[\]"/g)||[]).length,2,label+': both remaining photos are uploaded');

 // 2. A fresh prompt starts over; a typed name can skip photos.
 calls.length=0;
 await runGate(NEEDS_NAME);
 await modal.waitFor({state:'visible'});
 assert.equal(await page.locator('#warehouseNameStep').isVisible(),true,label+': the next prompt starts at the name');
 assert.equal(await page.locator('#warehousePhotoStrip img').count(),0,label+': photos from the last item are gone');
 await page.locator('#missingTitleInput').fill('Blue cotton shirt medium');
 await page.locator('#missingTitleSaveBtn').click();
 await page.locator('#warehousePhotoSkipBtn').click();
 assert.deepEqual(await gateResult(),{ready:true,title:'Blue cotton shirt medium'},label+': skipping photos still adds the named item');
 assert.deepEqual(calls.map(c=>c.path),['/api/custom-item/identity'],label+': skipping saves no photos');

 // 3. Editing the name goes back a step; closing during photos refuses the add.
 await runGate(NEEDS_NAME);
 await modal.waitFor({state:'visible'});
 await mic.click();
 await page.waitForFunction(()=>document.getElementById('warehouseDictateBtn').dataset.state==='listening');
 await page.locator('#missingTitleInput').fill('Red mug');
 await page.locator('#missingTitleSaveBtn').click();
 await page.locator('#missingTitlePhotoSection').waitFor({state:'visible'});
 assert.equal(await page.evaluate(()=>window.__recorders.at(-1).state),'inactive',label+': confirming a typed name stops the microphone');
 await page.locator('#warehouseEditNameBtn').click();
 await page.locator('#warehouseNameStep').waitFor({state:'visible'});
 assert.equal(await page.locator('#missingTitleInput').inputValue(),'Red mug');
 await page.locator('#missingTitleInput').fill('Red ceramic mug');
 await page.locator('#missingTitleSaveBtn').click();
 await page.locator('#missingTitlePhotoSection').waitFor({state:'visible'});
 assert.equal(await page.locator('#warehouseConfirmedName').textContent(),'Red ceramic mug');
 await page.locator('#missingTitleCloseBtn').click();
 assert.deepEqual(await gateResult(),{ready:false,title:''},label+': closing during photos must not add the item');

 // 4. An item that already has a picture is one step; dictation errors are shown.
 reply.dictation={status:422,json:{success:false,error:"Didn't catch a name. Hold the device closer and say it again."}};
 await runGate(HAS_IMAGE);
 await modal.waitFor({state:'visible'});
 assert.equal(await page.locator('#warehouseIdentitySteps').isVisible(),false,label+': one-step prompt hides the steps');
 await mic.click();
 await page.waitForFunction(()=>document.getElementById('warehouseDictateBtn').dataset.state==='listening');
 await mic.click();
 await page.waitForFunction(()=>/Didn't catch a name/.test(document.getElementById('warehouseIdentityStatus').textContent));
 assert.equal(await page.locator('#missingTitleInput').inputValue(),'',label+': a failed dictation leaves the field alone');
 await page.locator('#missingTitleInput').fill('Green glass vase');
 await page.locator('#missingTitleSaveBtn').click();
 assert.deepEqual(await gateResult(),{ready:true,title:'Green glass vase'},label+': no photo step when a picture exists');

 assert.deepEqual(errors,[],label);
 console.log(label+': naming flow passed');
 await page.close();
}

async function singleScanPage(browser,{locked,screenDelay}) {
 const submits=[];
 const page=await browser.newPage({userAgent:IPHONE,viewport:{width:390,height:844}});
 const errors=[];page.on('pageerror',e=>errors.push(e.message));
 await page.addInitScript(isLocked=>{
  localStorage.setItem('scannerMode','handheld');
  localStorage.setItem('deviceType','iPhone');
  localStorage.setItem('barcodeTimer','1');
  if(isLocked) sessionStorage.setItem('positionLocked','true');
 },locked);
 await page.route('**/*',async route=>{
  const request=route.request();
  const url=new URL(request.url());
  if(url.pathname==='/barcode') return route.fulfill({contentType:'text/html',body:fs.readFileSync(path.join(__dirname,'templates','barcode.html'),'utf8')});
  if(url.pathname==='/submitbarcode') {
   submits.push(new URLSearchParams(request.postData()||'').get('scanned_result'));
   return route.fulfill({contentType:'text/html',body:'<html><body>submitted</body></html>'});
  }
  if(url.pathname==='/api/custom-item/identity') return route.fulfill({json:{success:true}});
  if(url.pathname==='/api/add-item/screen') {
   const code=request.postDataJSON().barcodes[0];
   if(screenDelay[code]) await new Promise(r=>setTimeout(r,screenDelay[code]));
   const named=code!==NEEDS_NAME;
   return route.fulfill({json:{success:true,items:{[code]:{title:named?'Item '+code:'',needs_manual_title:!named,missing_image:false}}}});
  }
  if(url.pathname.startsWith('/static/')) return serveStatic(route,url);
  return route.fulfill({contentType:'application/json',body:'{}'});
 });
 await page.goto('http://scanner.test/barcode');
 await page.waitForTimeout(300);
 const scan=async code=>{
  await page.locator('#barcodeTextInput').fill(code);
  await page.locator('#barcodeTextInput').press('Enter');
 };
 return {page,submits,errors,scan};
}

(async()=>{
 const browser=await chromium.launch({channel:'msedge',headless:true});
 for(const template of ['multibarcode','barcode']) await namingFlow(browser,template);

 // /barcode single scan: a second entry made while the first is still being checked
 // was silently dropped and the first one was added instead.
 {
  const {page,submits,errors,scan}=await singleScanPage(browser,{locked:false,screenDelay:{[KNOWN]:1500}});
  await scan(KNOWN);
  await page.waitForTimeout(900);
  await scan(NEXT);
  await page.waitForFunction(()=>document.body.textContent.includes('submitted'),null,{timeout:10000});
  assert.deepEqual(submits,[NEXT],'barcode: the latest entry is the one added');
  assert.deepEqual(errors,[],'barcode single scan race');
  console.log('barcode: single-scan replacement passed');
  await page.close();
 }

 // /barcode with a locked shelf: the list auto-submit fired while an item was being
 // named, sending the list without it.
 {
  const {page,submits,errors,scan}=await singleScanPage(browser,{locked:true,screenDelay:{}});
  await scan(KNOWN);
  await page.waitForFunction(()=>document.querySelectorAll('#barcodeList .barcode-list-row').length===1);
  await scan(NEEDS_NAME);
  await page.locator('#missingTitleModal').waitFor({state:'visible'});
  await page.waitForTimeout(2500);
  assert.deepEqual(submits,[],'barcode locked: nothing submits while an item is being named');
  await page.locator('#missingTitleInput').fill('Blue cotton shirt medium');
  await page.locator('#missingTitleSaveBtn').click();
  await page.waitForFunction(()=>document.body.textContent.includes('submitted'),null,{timeout:10000});
  assert.equal(submits.length,1);
  assert.ok(submits[0].includes(KNOWN)&&submits[0].includes(NEEDS_NAME),'barcode locked: the named item is submitted with the list: '+submits[0]);
  assert.deepEqual(errors,[],'barcode locked auto-submit');
  console.log('barcode: locked auto-submit pause passed');
  await page.close();
 }
 await browser.close();
})().catch(e=>{console.error(e);process.exit(1)});
