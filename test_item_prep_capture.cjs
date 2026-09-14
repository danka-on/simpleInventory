// Item Prep and its BAD-flow diagnostic page share the receiving capture tools:
// a recorded voice note is transcribed (Lithuanian) and translated into the note, and
// photos go through the same photo step, where the first photo is the thumbnail.
// An item with no picture is asked for photos when it is marked GOOD.
const {chromium}=require('playwright');
const fs=require('node:fs');
const path=require('node:path');
const assert=require('node:assert/strict');

const NO_IMAGE='194137223323';
const WITH_IMAGE='035886420499';
const BAD_UNIT='194137223323-1';
const LT='Trūksta dviejų šaukštų.';
const EN='Two spoons are missing.';
const NOTE='LT: '+LT+' | EN: '+EN;
const IPAD='Mozilla/5.0 (iPad; CPU OS 15_6 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) CriOS/125.0 Mobile/15E148 Safari/604.1';

function fakeMicrophone() {
 Object.defineProperty(navigator,'mediaDevices',{configurable:true,value:{getUserMedia:async()=>new MediaStream()}});
 window.__sounds=[];
 document.addEventListener('media-capture:sound',event=>window.__sounds.push(event.detail));
 window.MediaRecorder=class {
  constructor(stream,options) { this.mimeType=(options&&options.mimeType)||'audio/webm'; this.state='inactive'; this.listeners={}; }
  static isTypeSupported(type) { return type.startsWith('audio/webm'); }
  addEventListener(type,listener) { this.listeners[type]=listener; }
  start() { this.state='recording'; }
  stop() {
   this.state='inactive';
   if(this.listeners.dataavailable) this.listeners.dataavailable({data:new Blob(['fake clip'],{type:this.mimeType})});
   if(this.listeners.stop) this.listeners.stop();
  }
 };
 window.open=()=>null;
}

function serveStatic(route,url) {
 const file=path.join(__dirname,url.pathname);
 if(fs.existsSync(file)) return route.fulfill({contentType:file.endsWith('.js')?'application/javascript; charset=utf-8':'text/plain',body:fs.readFileSync(file)});
 return route.fulfill({status:404,contentType:'text/plain',body:''});
}

async function page(browser, calls, reply) {
 const tab=await browser.newPage({userAgent:IPAD,viewport:{width:820,height:1180},hasTouch:true});
 const errors=[];tab.on('pageerror',e=>errors.push(e.message));
 await tab.addInitScript(fakeMicrophone);
 let photoUploads=0;
 await tab.route('**/*',async route=>{
  const request=route.request();
  const url=new URL(request.url());
  const p=url.pathname;
  const record=body=>calls.push({path:p,method:request.method(),body});
  if(url.host!=='prep.test') return route.fulfill({contentType:'text/css',body:''});
  if(p==='/item-prep') return route.fulfill({contentType:'text/html; charset=utf-8',body:fs.readFileSync(path.join(__dirname,'templates','item_prep.html'),'utf8')});
  if(p==='/item-prep/diagnostic') return route.fulfill({contentType:'text/html; charset=utf-8',body:fs.readFileSync(path.join(__dirname,'templates','item_prep_diagnostic.html'),'utf8').split('{{ upc }}').join(BAD_UNIT)});
  if(p.startsWith('/static/')) return serveStatic(route,url);
  if(p==='/api/lots') return route.fulfill({json:{lots:[{lot_number:'LOT1',import_date:'2026-09-01'}]}});
  if(p==='/api/bol_lookup') {
   const upc=url.searchParams.get('upc');
   return route.fulfill({json:{found:true,item:{upc,item_description:upc===NO_IMAGE?'Arch Studio Opal Glass Bowls':'Zwilling Squared Flatware',
    image_url:upc===WITH_IMAGE?'/static/custom_items/known.jpg':'',lot_number:'LOT1',unchecked_remaining:4,good_qty:0}}});
  }
  if(p==='/api/warehouse/voice-notes/transcribe') {
   record(request.postDataBuffer().toString('latin1'));
   if(reply.transcribeDelay) await new Promise(r=>setTimeout(r,reply.transcribeDelay));
   return route.fulfill({json:{success:true,lithuanian:LT,english:EN,warning:''}});
  }
  if(p==='/api/items_prep/status'&&request.method()==='POST') {
   const body=request.postDataJSON();record(body);
   return route.fulfill({json:{success:true,upc:body.upc,base_upc:body.upc,action:'created',quantity:1,lot_number:'LOT1'}});
  }
  if(p==='/api/items_prep/notes'&&request.method()==='POST') { record(request.postDataJSON()); return route.fulfill({json:{success:true}}); }
  if(/^\/api\/items_prep\/diagnostic\/[^/]+\/photos$/.test(p)&&request.method()==='POST') {
   photoUploads+=1;
   record(request.postDataBuffer().toString('latin1'));
   return route.fulfill({json:{success:true,images:[{id:photoUploads,image_path:'items_prep/upload-'+photoUploads+'.jpg'}]}});
  }
  if(p==='/api/items_prep/set_display_image') { record(request.postDataJSON()); return route.fulfill({json:{success:true}}); }
  if(p.startsWith('/api/items_prep/media/')&&request.method()==='POST') { record(''); return route.fulfill({json:{success:true}}); }
  if(p.startsWith('/api/items_prep/notes/')) return route.fulfill({json:{success:true,notes:[]}});
  if(p.startsWith('/api/items_prep/media/')) return route.fulfill({json:{success:true,items:[]}});
  return route.fulfill({contentType:'application/json',body:'{}'});
 });
 return {tab,errors};
}

const jpeg=(tab,color)=>tab.evaluate(fill=>{
 const canvas=document.createElement('canvas');canvas.width=40;canvas.height=30;
 const g=canvas.getContext('2d');g.fillStyle=fill;g.fillRect(0,0,40,30);
 return canvas.toDataURL('image/jpeg');
},color).then(url=>Buffer.from(url.split(',')[1],'base64'));

async function addPickerPhoto(tab,color,count) {
 await tab.locator('#mediaPickerModal input[type="file"]').setInputFiles({name:'photo.jpg',mimeType:'image/jpeg',buffer:await jpeg(tab,color)});
 await tab.waitForFunction(n=>document.querySelectorAll('#mediaPickerModal .identity-photo img').length===n,count);
}

async function holdMic(tab,selector) {
 const box=await tab.locator(selector).boundingBox();
 await tab.mouse.move(box.x+box.width/2,box.y+box.height/2);
 await tab.mouse.down();
 await tab.waitForFunction(s=>document.querySelector(s).classList.contains('recording'),selector);
 await tab.mouse.up();
}

async function itemPrep(browser) {
 const calls=[];
 const reply={transcribeDelay:0};
 const {tab,errors}=await page(browser,calls,reply);
 const picker=tab.locator('#mediaPickerModal');
 const lookup=async upc=>{
  await tab.locator('#manualUpc').fill(upc);
  await tab.locator('#manualUpc').press('Enter');
  await tab.locator('#result').waitFor({state:'visible'});
  await tab.waitForFunction(code=>document.getElementById('upcTxt').textContent===code,upc);
 };
 const finishGood=async()=>{
  await tab.waitForFunction(()=>/Marked GOOD/.test(document.getElementById('statusMsg').textContent),null,{timeout:10000});
  await tab.locator('#result').waitFor({state:'hidden',timeout:10000});
 };
 const pathsSince=start=>calls.slice(start).map(c=>c.path);
 await tab.goto('http://prep.test/item-prep');
 await tab.waitForTimeout(500);

 // 1. A voice note is transcribed and translated into the note right away.
 await lookup(NO_IMAGE);
 await tab.evaluate(()=>{window.__sounds=[]});
 await holdMic(tab,'#quickVoiceNoteBtn');
 await tab.waitForFunction(text=>document.getElementById('quickPrepNote').value===text,NOTE);
 await tab.waitForFunction(()=>window.__sounds.includes('mic-off'));
 assert.deepEqual(await tab.evaluate(()=>window.__sounds),['mic-on','mic-off'],'item prep: the mic plays start and stop tones');
 assert.match(await tab.locator('#quickVoiceNoteStatus').textContent(),/Lithuanian and English/);
 assert.equal(await tab.locator('#quickPrepNote').evaluate(el=>el.tagName),'TEXTAREA','item prep: the note wraps');
 assert.ok(await tab.locator('#quickPrepNote').evaluate(el=>el.scrollHeight<=el.clientHeight+1),'item prep: the whole note is visible');
 const clip=calls.find(c=>c.path==='/api/warehouse/voice-notes/transcribe');
 assert.ok(clip&&/filename="prep_voice_\d+\.webm"/.test(clip.body),'item prep: the recording itself is sent for transcription');

 // 2. GOOD on an item with no picture asks for photos first; the star reorders them.
 let start=calls.length;
 await tab.locator('#goodBtn').click();
 await picker.waitFor({state:'visible'});
 assert.equal(await tab.locator('#mediaPickerHeading').textContent(),'This item needs a photo');
 assert.match(await tab.locator('#mediaPickerModal .media-picker-reason').textContent(),/No picture yet/);
 assert.equal(await tab.locator('#mediaPickerModal .media-picker-skip').isVisible(),true,'item prep: photos can be skipped on GOOD');
 assert.deepEqual(pathsSince(start),[],'item prep: nothing is saved while the photo step is open');
 await addPickerPhoto(tab,'#cc0000',1);
 await addPickerPhoto(tab,'#0000cc',2);
 const [red,blue]=await tab.$$eval('#mediaPickerModal .identity-photo img',images=>images.map(i=>i.src));
 await tab.locator('#mediaPickerModal .identity-photo').nth(1).locator('.identity-photo-promote').click();
 assert.deepEqual(await tab.$$eval('#mediaPickerModal .identity-photo img',images=>images.map(i=>i.src)),[blue,red]);
 assert.equal(await tab.locator('#mediaPickerModal .is-thumbnail .identity-photo-badge').textContent(),'Thumbnail');
 await tab.locator('#mediaPickerModal .media-picker-save').click();
 await finishGood();
 const status=calls.slice(start).find(c=>c.path==='/api/items_prep/status');
 assert.equal(status.body.status,'good');
 const note=calls.slice(start).find(c=>c.path==='/api/items_prep/notes');
 assert.equal(note&&note.body.note,NOTE,'item prep: the Lithuanian and English note is saved with GOOD');
 const uploads=calls.slice(start).filter(c=>c.path===`/api/items_prep/diagnostic/${NO_IMAGE}/photos`);
 assert.equal(uploads.length,2,'item prep: both photos are uploaded');
 assert.match(uploads[0].body,/prep_photo_\d+_2\.jpg/,'item prep: the second photo goes up first');
 assert.match(uploads[1].body,/prep_photo_\d+_1\.jpg/,'item prep: the thumbnail goes up last so it leads the newest-first list');
 const display=calls.slice(start).find(c=>c.path==='/api/items_prep/set_display_image');
 assert.deepEqual(display&&display.body,{upc:NO_IMAGE,image_path:'items_prep/upload-2.jpg',lot_number:'LOT1'},'item prep: the thumbnail becomes the item picture');
 assert.ok(pathsSince(start).includes(`/api/items_prep/media/${NO_IMAGE}`),'item prep: the voice recording is still saved');

 // 3. Closing the photo step cancels GOOD; Skip still marks the item GOOD without photos.
 await lookup(NO_IMAGE);
 start=calls.length;
 await tab.locator('#goodBtn').click();
 await picker.waitFor({state:'visible'});
 await tab.locator('#mediaPickerModal .media-picker-close').click();
 await picker.waitFor({state:'hidden'});
 await tab.waitForFunction(()=>!document.getElementById('goodBtn').disabled);
 assert.deepEqual(pathsSince(start),[],'item prep: closing the photo step does not mark GOOD');
 await tab.locator('#goodBtn').click();
 await picker.waitFor({state:'visible'});
 await tab.locator('#mediaPickerModal .media-picker-skip').click();
 await finishGood();
 assert.ok(pathsSince(start).includes('/api/items_prep/status'),'item prep: Skip still marks GOOD');
 assert.equal(pathsSince(start).some(p=>p.endsWith('/photos')||p.endsWith('set_display_image')),false,'item prep: Skip saves no photos');

 // 4. Add Photo opens the same step; its photos show with the thumbnail marked.
 await lookup(WITH_IMAGE);
 await tab.locator('#pendingPhotoBtn').click();
 await picker.waitFor({state:'visible'});
 assert.equal(await tab.locator('#mediaPickerHeading').textContent(),'Add photos');
 assert.equal(await tab.locator('#mediaPickerModal .media-picker-skip').isVisible(),false);
 assert.equal(await tab.locator('#mediaPickerModal .media-picker-current').isVisible(),true,'item prep: the current picture is shown');
 await addPickerPhoto(tab,'#00aa00',1);
 await tab.locator('#mediaPickerModal .media-picker-save').click();
 await picker.waitFor({state:'hidden'});
 assert.equal(await tab.locator('#pendingMediaPreviews img').count(),1);
 assert.equal(await tab.locator('#pendingMediaPreviews .pending-thumbnail-badge').textContent(),'Thumbnail');

 // 5. An item with a picture is not asked for photos, and GOOD waits for a slow transcription.
 reply.transcribeDelay=1500;
 start=calls.length;
 await holdMic(tab,'#quickVoiceNoteBtn');
 await tab.locator('#goodBtn').click();
 await finishGood();
 assert.equal(await picker.isVisible(),false,'item prep: no photo step for an item that has a picture');
 const slowNote=calls.slice(start).find(c=>c.path==='/api/items_prep/notes');
 assert.equal(slowNote&&slowNote.body.note,NOTE,'item prep: GOOD waited for the transcription');
 assert.equal(calls.slice(start).some(c=>c.path==='/api/items_prep/set_display_image'),false,'item prep: an existing picture is kept');
 assert.equal(calls.slice(start).filter(c=>c.path.endsWith('/photos')).length,1,'item prep: the added photo is uploaded');

 assert.deepEqual(errors,[],'item prep page errors');
 console.log('item prep: voice note translation and photo step passed');
 await tab.close();
}

async function diagnostic(browser) {
 const calls=[];
 const {tab,errors}=await page(browser,calls,{transcribeDelay:0});
 await tab.goto('http://prep.test/item-prep/diagnostic?upc='+encodeURIComponent(BAD_UNIT)+'&from=bad&qty=1');
 await tab.waitForTimeout(500);

 await tab.evaluate(()=>{window.__sounds=[]});
 await holdMic(tab,'#recordNoteBtn');
 await tab.waitForFunction(text=>document.getElementById('newNoteInput').value===text,NOTE);
 await tab.waitForFunction(()=>window.__sounds.includes('mic-off'));
 assert.deepEqual(await tab.evaluate(()=>window.__sounds),['mic-on','mic-off'],'diagnostic: the mic plays start and stop tones');
 assert.equal(await tab.locator('#newNoteInput').evaluate(el=>el.tagName),'TEXTAREA','diagnostic: the note box wraps');

 const picker=tab.locator('#mediaPickerModal');
 await tab.locator('#takePhotoBtn').click();
 await picker.waitFor({state:'visible'});
 assert.equal(await tab.locator('#mediaPickerModal .media-picker-skip').isVisible(),false);
 await addPickerPhoto(tab,'#cc0000',1);
 await addPickerPhoto(tab,'#00aa00',2);
 await addPickerPhoto(tab,'#0000cc',3);
 await tab.locator('#mediaPickerModal .identity-photo').nth(2).locator('.identity-photo-promote').click();
 await tab.locator('#mediaPickerModal .media-picker-save').click();
 await picker.waitFor({state:'hidden'});
 assert.equal(await tab.locator('#preview img').count(),3,'diagnostic: the photos are added to the capture list');
 const names=await tab.evaluate(()=>capturedFiles.map(entry=>entry.file.name));
 assert.equal(names.length,3);
 assert.match(names[0],/_3\.jpg$/,'diagnostic: saved order is reversed');
 assert.match(names[2],/_1\.jpg$/,'diagnostic: the thumbnail is saved last so it leads the newest-first list');

 assert.deepEqual(errors,[],'diagnostic page errors');
 console.log('diagnostic: voice note translation and photo step passed');
 await tab.close();
}

(async()=>{
 const browser=await chromium.launch({channel:'msedge',headless:true});
 await itemPrep(browser);
 await diagnostic(browser);
 await browser.close();
})().catch(e=>{console.error(e);process.exit(1)});
