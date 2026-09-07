const {chromium}=require('playwright');
const fs=require('node:fs');
const path=require('node:path');
const assert=require('node:assert/strict');
(async()=>{
 const browser=await chromium.launch({channel:'msedge',headless:true});
 for(const template of ['multibarcode','barcode']) for(const feedback of ['missing','throws','loaded']) {
  const page=await browser.newPage({userAgent:'Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15',viewport:{width:390,height:844}});
  const errors=[];page.on('pageerror',e=>errors.push(e.message));
  await page.addInitScript(()=>{localStorage.setItem('scannerMode','handheld');localStorage.setItem('deviceType','iPhone')});
  await page.route('**/*',async route=>{
   const url=new URL(route.request().url());
   if(url.pathname==='/'+template) return route.fulfill({contentType:'text/html',body:fs.readFileSync(path.join(__dirname,'templates',template+'.html'),'utf8')});
   if(url.pathname==='/api/custom-item/identity') return route.fulfill({json:{success:true}});
   if(url.pathname==='/api/add-item/screen') {
    const code=route.request().postDataJSON().barcodes[0];
    return route.fulfill({json:{success:true,items:{[code]:{title:code==='234567890123'?'':'Known item',needs_manual_title:code==='234567890123',missing_image:false}}}});
   }
   if(url.pathname.startsWith('/static/')) {
    if(url.pathname==='/static/scan-feedback.js' && feedback!=='loaded') return route.fulfill({status:feedback==='missing'?404:200,contentType:'application/javascript',body:feedback==='throws'?"window.ScanFeedback={check:async()=>{throw Error('Audio failed')},needsInfo:async()=>{throw Error('Audio failed')}}":''});
    const file=path.join(__dirname,url.pathname);
    if(fs.existsSync(file)) return route.fulfill({contentType:file.endsWith('.js')?'application/javascript':'text/plain',body:fs.readFileSync(file)});
   }
   return route.fulfill({contentType:'application/json',body:'{}'});
  });
  await page.goto('http://scanner.test/'+template);
  await page.waitForTimeout(300);
  if(template==='multibarcode') {
   await page.locator('#scannerInput').focus();await page.keyboard.type('123456789012');await page.keyboard.press('Enter');
   await page.waitForFunction(()=>document.querySelectorAll('.barcode-item').length===1);
   await page.locator('#scannerInput').focus();await page.keyboard.type('123456789012');await page.keyboard.press('Enter');
   await page.waitForFunction(()=>document.querySelector('#countBadge').textContent==='2 items');
   await page.locator('#scannerInput').focus();await page.keyboard.type('234567890123');await page.keyboard.press('Enter');
   await page.locator('#missingTitleModal').waitFor({state:'visible'});
   await page.waitForTimeout(250);
   await page.locator('#missingTitleInput').fill('Test warehouse item');
   await page.locator('#missingTitleSaveBtn').click();
   await page.waitForFunction(()=>document.querySelector('#countBadge').textContent==='3 items');
   assert.equal(await page.locator('#scannerInput').isDisabled(),false);
   await page.locator('#scannerInput').focus();await page.keyboard.type('123456789012');await page.keyboard.press('Enter');
   await page.waitForFunction(()=>document.querySelector('#countBadge').textContent==='4 items');
  } else {
   // Exercise the single screen's actual scan handler without navigating away.
   await page.evaluate(()=>{window.queueSingleBarcodeOriginal=queueSingleBarcode;queueSingleBarcode=code=>window.capturedScan=code});
   await page.locator('#barcodeTextInput').focus();await page.keyboard.type('123456789012');await page.keyboard.press('Enter');
   await page.waitForFunction(()=>window.capturedScan==='123456789012');
  }
  assert.deepEqual(errors,[],template+' '+feedback);
  console.log(template+': '+feedback+' feedback, scanner input passed');await page.close();
 }
 await browser.close();
})().catch(e=>{console.error(e);process.exit(1)});
