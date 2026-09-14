// The No Barcode modal must produce the same item a scan does. It used to save
// the placeholder "No barcode item" as the title, which both hid the real
// catalog name on the warehouse row and skipped the naming prompt entirely.
// On /barcode without a locked shelf it also printed the label before the item
// was named and could add the item while that label was still printing.
const {chromium}=require('playwright');
const fs=require('node:fs');
const path=require('node:path');
const assert=require('node:assert/strict');

const CODE='234567890123';

(async()=>{
 const browser=await chromium.launch({channel:'msedge',headless:true});
 for(const [template,mode] of [['multibarcode','locked'],['barcode','locked'],['barcode','single']]) {
  for(const scenario of ['blank-title','typed-title']) {
   const label=template+' '+mode+' '+scenario;
   const identitySaves=[];
   const prints=[];
   const events=[];
   const names={};   // what /api/add-item/screen knows about a barcode
   const page=await browser.newPage({userAgent:'Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15',viewport:{width:390,height:844}});
   const errors=[];page.on('pageerror',e=>errors.push(e.message));
   await page.addInitScript(locked=>{
    localStorage.setItem('scannerMode','handheld');
    localStorage.setItem('deviceType','iPhone');
    // /barcode screens at scan time only while a shelf is locked; the single-scan
    // path is exercised separately.
    if(locked) sessionStorage.setItem('positionLocked','true');
   },mode==='locked');
   await page.route('**/*',async route=>{
    const request=route.request();
    const url=new URL(request.url());
    if(url.pathname==='/'+template) return route.fulfill({contentType:'text/html',body:fs.readFileSync(path.join(__dirname,'templates',template+'.html'),'utf8')});
    if(url.pathname==='/api/custom-item/identity') {
     const body=request.postDataJSON();
     identitySaves.push(body);
     events.push('identity');
     names[body.upc]=body.item_description||body.title||'';
     return route.fulfill({json:{success:true}});
    }
    if(url.pathname==='/api/add-item/screen') {
     const code=request.postDataJSON().barcodes[0];
     const title=names[code]||'';
     return route.fulfill({json:{success:true,items:{[code]:{title,needs_manual_title:!title,missing_image:false}}}});
    }
    if(url.pathname==='/api/printer/config') return route.fulfill({json:{config:{print_method:'network'}}});
    if(url.pathname==='/api/printer/print-barcode') {
     const body=request.postDataJSON();
     prints.push(body);
     // A slow printer: the add must still wait for the label.
     await new Promise(r=>setTimeout(r,900));
     events.push('print:'+body.item_description);
     return route.fulfill({json:{success:true}});
    }
    if(url.pathname==='/submitbarcode') {
     events.push('submit:'+new URLSearchParams(request.postData()||'').get('scanned_result'));
     return route.fulfill({contentType:'text/html',body:'<html><body>submitted</body></html>'});
    }
    if(url.pathname.startsWith('/static/')) {
     const file=path.join(__dirname,url.pathname.split('?')[0]);
     if(fs.existsSync(file)) return route.fulfill({contentType:file.endsWith('.js')?'application/javascript':'text/plain',body:fs.readFileSync(file)});
    }
    return route.fulfill({contentType:'application/json',body:'{}'});
   });
   await page.goto('http://scanner.test/'+template);
   await page.waitForTimeout(300);

   await page.locator('#noBarcodeBtn').click();
   await page.locator('#noBarcodeModal').waitFor({state:'visible'});
   await page.locator('#noBarcodeInput').fill(CODE);
   if(scenario==='typed-title') await page.locator('#noBarcodeDescriptionInput').fill('Red ceramic mug');
   await page.locator('#noBarcodeUseBtn').click();

   if(scenario==='blank-title') {
    // No title typed means no name is known, so the item must go through the
    // same prompt a scanned item with no catalog hit gets.
    await page.locator('#missingTitleModal').waitFor({state:'visible'});
    assert.deepEqual(identitySaves,[],label+': a blank title must not save a placeholder identity');
    await page.waitForTimeout(1000);
    assert.deepEqual(prints,[],label+': nothing should print before the item is named');
    assert.deepEqual(events,[],label+': nothing is added before the item is named');
    await page.locator('#missingTitleInput').fill('Red ceramic mug');
    await page.locator('#missingTitleSaveBtn').click();
   }

   if(mode==='single') {
    await page.waitForFunction(()=>document.body.textContent.includes('submitted'),null,{timeout:10000});
    assert.deepEqual(events,['identity','print:Red ceramic mug','submit:'+CODE],label+': name, then label, then add');
   } else {
    await page.locator('#noBarcodeModal').waitFor({state:'hidden'});
   }
   assert.equal(identitySaves.length,1,label+': the name should be saved once');
   assert.equal(identitySaves[0].upc,CODE,label+': the name must be saved against this barcode');
   assert.equal(identitySaves[0].item_description||identitySaves[0].title,'Red ceramic mug',label+': the typed name must reach the registry');
   assert.deepEqual(prints.map(p=>[p.upc,p.item_description]),[[CODE,'Red ceramic mug']],label+': the label must carry the name, not a placeholder');

   assert.deepEqual(errors,[],label);
   console.log(label+': passed');
   await page.close();
  }
 }
 await browser.close();
})().catch(e=>{console.error(e);process.exit(1)});
