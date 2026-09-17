const {chromium}=require('playwright');
const fs=require('node:fs');
const path=require('node:path');
const assert=require('node:assert/strict');
const PIXEL='data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==';
(async()=>{
 const browser=await chromium.launch({channel:'msedge',headless:true});
 for(const template of ['multibarcode','barcode']) {
  const page=await browser.newPage({viewport:{width:390,height:844}});
  const errors=[];page.on('pageerror',e=>errors.push(e.message));
  const saved=[];
  await page.addInitScript(()=>{localStorage.setItem('scannerMode','handheld');sessionStorage.setItem('positionLocked','true')});
  await page.route('**/*',async route=>{
   const url=new URL(route.request().url());
   if(url.pathname==='/'+template) return route.fulfill({contentType:'text/html',body:fs.readFileSync(path.join(__dirname,'templates',template+'.html'),'utf8')});
   if(url.pathname==='/img/known.png') return route.fulfill({contentType:'image/png',body:Buffer.from(PIXEL.split(',')[1],'base64')});
   if(url.pathname==='/api/add-item/screen') {
    const code=route.request().postDataJSON().barcodes[0];
    const hasImage=code==='123456789012';
    return route.fulfill({json:{success:true,items:{[code]:{title:hasImage?'Known item':'Plain item',image_url:hasImage?'/img/known.png':'',needs_manual_title:false,missing_image:!hasImage}}}});
   }
   if(url.pathname==='/api/items-prep/temp-item') {saved.push(route.request().postDataJSON());return route.fulfill({json:{success:true,image_url:'/img/known.png'}});}
   if(url.pathname.startsWith('/static/')) {
    const file=path.join(__dirname,url.pathname);
    if(fs.existsSync(file)) return route.fulfill({contentType:file.endsWith('.js')?'application/javascript':'text/plain',body:fs.readFileSync(file)});
   }
   return route.fulfill({contentType:'application/json',body:'{}'});
  });
  await page.goto('http://scanner.test/'+template);
  await page.waitForTimeout(300);
  const add=async code=>{
   if(template==='multibarcode') {await page.locator('#scannerInput').focus();await page.keyboard.type(code);await page.keyboard.press('Enter');}
   else await page.evaluate(c=>addBarcodeToList(c),code);
  };
  const card=page.locator('#lastScanPreview');
  assert.equal(await card.evaluate(el=>el.classList.contains('is-visible')),false);
  // The card sits directly above the list so the list scrolls underneath it.
  assert.equal(await card.evaluate(el=>el.nextElementSibling.id),'barcodeList');

  await add('123456789012');
  await page.waitForFunction(()=>document.querySelector('#lastScanPreview.is-visible .last-scan-thumb img.is-loaded'));
  assert.equal(await card.locator('.last-scan-title').textContent(),'Known item');

  await add('234567890123');
  await page.locator('#lastScanPreview .last-scan-add').waitFor({state:'visible'});
  assert.equal(await card.locator('.last-scan-title').textContent(),'Plain item');
  assert.equal(await card.locator('.last-scan-code').textContent(),'234567890123');

  await page.evaluate(px=>{window.MediaCapture.pickPhotos=async()=>({action:'save',photos:[px]})},PIXEL);
  await card.locator('.last-scan-add').click();
  await page.waitForFunction(()=>document.querySelector('#lastScanPreview .last-scan-thumb img.is-loaded'));
  assert.equal(saved.length,1);
  assert.equal(saved[0].upc,'234567890123');

  assert.deepEqual(errors,[],template);
  console.log(template+': last scan preview passed');await page.close();
 }
 await browser.close();
})().catch(e=>{console.error(e);process.exit(1)});
