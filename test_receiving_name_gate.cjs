// The naming gate on both receiving screens must never add an unnamed item on
// its own. Two holes produced the nameless SEARCHRACK rows this covers:
//   1. /multibarcode's modal close recorded a skip and let the item through.
//   2. A failing /api/add-item/screen call added the item with no prompt.
const {chromium}=require('playwright');
const fs=require('node:fs');
const path=require('node:path');
const assert=require('node:assert/strict');

const NEEDS_NAME='234567890123';
const KNOWN='123456789012';

(async()=>{
 const browser=await chromium.launch({channel:'msedge',headless:true});
 for(const template of ['multibarcode','barcode']) {
  // screenMode drives what /api/add-item/screen does for NEEDS_NAME.
  for(const screenMode of ['needs-name','fails']) {
   let screenCalls=0;
   const identitySaves=[];
   const page=await browser.newPage({userAgent:'Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15',viewport:{width:390,height:844}});
   const errors=[];page.on('pageerror',e=>errors.push(e.message));
   await page.addInitScript(()=>{localStorage.setItem('scannerMode','handheld');localStorage.setItem('deviceType','iPhone')});
   await page.route('**/*',async route=>{
    const url=new URL(route.request().url());
    if(url.pathname==='/'+template) return route.fulfill({contentType:'text/html',body:fs.readFileSync(path.join(__dirname,'templates',template+'.html'),'utf8')});
    if(url.pathname==='/api/custom-item/identity') {
     identitySaves.push(route.request().postDataJSON());
     return route.fulfill({json:{success:true}});
    }
    if(url.pathname==='/api/add-item/screen') {
     const code=route.request().postDataJSON().barcodes[0];
     screenCalls++;
     if(screenMode==='fails'&&code===NEEDS_NAME) return route.fulfill({status:500,json:{success:false,error:'screening down'}});
     return route.fulfill({json:{success:true,items:{[code]:{title:code===NEEDS_NAME?'':'Known item',needs_manual_title:code===NEEDS_NAME,missing_image:false}}}});
    }
    if(url.pathname.startsWith('/static/')) {
     const file=path.join(__dirname,url.pathname);
     if(fs.existsSync(file)) return route.fulfill({contentType:file.endsWith('.js')?'application/javascript':'text/plain',body:fs.readFileSync(file)});
    }
    return route.fulfill({contentType:'application/json',body:'{}'});
   });
   await page.goto('http://scanner.test/'+template);
   await page.waitForTimeout(300);

   // Drive the gate directly: it is the contract both screens submit through.
   const runGate=code=>page.evaluate(c=>{window.__gate=ensureScannedBarcodeReadyForAdd(c)},code);
   const gateResult=()=>page.evaluate(()=>window.__gate);

   // A barcode the catalog already names never prompts.
   await runGate(KNOWN);
   assert.equal(await gateResult(),true,template+' '+screenMode+': known item should pass');
   assert.equal(await page.locator('#missingTitleModal').isVisible(),false,template+' '+screenMode+': known item must not prompt');

   // Closing the prompt must refuse the add on BOTH screens.
   screenCalls=0;
   await runGate(NEEDS_NAME);
   await page.locator('#missingTitleModal').waitFor({state:'visible'});
   const warned=await page.locator('#missingTitleScreenWarning').isVisible();
   assert.equal(warned,screenMode==='fails',template+' '+screenMode+': warning banner visibility');
   if(screenMode==='fails') assert.equal(screenCalls,2,template+': a failed screen call should be retried once');
   await page.locator('#missingTitleCloseBtn').click();
   assert.equal(await gateResult(),false,template+' '+screenMode+': closing the prompt must not add the item');

   // The refusal must not be remembered: the next scan prompts again.
   assert.equal(await page.evaluate(()=>sessionStorage.getItem('additem_manual_title_skips')),null,template+' '+screenMode+': no skip should be persisted');
   await runGate(NEEDS_NAME);
   await page.locator('#missingTitleModal').waitFor({state:'visible'});

   // Naming it saves durably and releases the item.
   await page.locator('#missingTitleInput').fill('Blue cotton shirt medium');
   await page.locator('#missingTitleSaveBtn').click();
   assert.equal(await gateResult(),true,template+' '+screenMode+': a named item should pass');
   assert.deepEqual(identitySaves.map(s=>s.title),['Blue cotton shirt medium'],template+' '+screenMode+': name must reach /api/custom-item/identity');
   assert.equal(identitySaves[0].upc,NEEDS_NAME,template+' '+screenMode+': name must be saved against the scanned barcode');

   assert.deepEqual(errors,[],template+' '+screenMode);
   console.log(template+': '+screenMode+' gate passed');
   await page.close();
  }
 }
 await browser.close();
})().catch(e=>{console.error(e);process.exit(1)});
