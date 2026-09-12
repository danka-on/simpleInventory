// /submitbarcode (additem.html): the receipt posts per-unit entries with a stable
// submission id, never re-posts after a 4xx, and retries with the same id after a 5xx.
const {chromium}=require('playwright');
const fs=require('node:fs');
const path=require('node:path');
const assert=require('node:assert/strict');

function parseForm(request){
 const type=request.headers()['content-type']||'';
 const body=request.postData()||'';
 const out={};
 if(type.includes('multipart/form-data')){
  for(const m of body.matchAll(/name="([^"]+)"\r\n\r\n([\s\S]*?)\r\n--/g)) out[m[1]]=m[2];
 } else {
  for(const [k,v] of new URLSearchParams(body)) out[k]=v;
 }
 return out;
}

(async()=>{
 const browser=await chromium.launch({channel:'msedge',headless:true});
 for(const scenario of ['rejected','retry','saved']){
  const page=await browser.newPage({viewport:{width:390,height:844}});
  const errors=[];page.on('pageerror',e=>errors.push(e.message));
  const dialogs=[];page.on('dialog',async d=>{dialogs.push(d.message());await d.accept();});
  const posts=[];
  await page.addInitScript(()=>{
   localStorage.setItem('disableAutoSubmit','1');
   // Seed the receipt only on the add page; the init script also runs on /position.
   if(location.pathname!=='/submitbarcode') return;
   sessionStorage.setItem('item_position','a1');
   sessionStorage.setItem('barcode_entries',JSON.stringify([
    {code:'123456789012',suffix:0,quantity:2,note:''},
    {code:'123456789012',suffix:0,quantity:1,note:'Torn box'}
   ]));
   sessionStorage.setItem('barcode','123456789012,123456789012,123456789012');
  });
  await page.route('**/*',async route=>{
   const request=route.request();
   const url=new URL(request.url());
   if(url.pathname==='/submitbarcode') return route.fulfill({contentType:'text/html',body:fs.readFileSync(path.join(__dirname,'templates','additem.html'),'utf8')});
   if(url.pathname==='/position') return route.fulfill({contentType:'text/html',body:'<title>position</title>'});
   if(url.pathname==='/api/add-item/screen') {
    const items={};
    for(const code of request.postDataJSON().barcodes) items[code]={title:'Known item',needs_manual_title:false,missing_image:false};
    return route.fulfill({json:{success:true,items}});
   }
   if(url.pathname==='/additemtrue') {
    posts.push({fields:parseForm(request),headers:request.headers(),navigation:request.isNavigationRequest()});
    if(scenario==='rejected') return route.fulfill({status:400,contentType:'text/plain',body:'Error: Unknown shelf position: a1'});
    if(scenario==='retry' && posts.length===1) return route.fulfill({status:500,contentType:'text/plain',body:'An internal error occurred'});
    if(scenario==='retry') return route.fulfill({contentType:'text/html',body:'<title>saved</title>'});
    return route.fulfill({json:{success:true,message:'Item added successfully',labels_to_print:[]}});
   }
   if(url.pathname.startsWith('/static/')) {
    const file=path.join(__dirname,url.pathname);
    if(fs.existsSync(file)) return route.fulfill({contentType:file.endsWith('.js')?'application/javascript':'text/plain',body:fs.readFileSync(file)});
   }
   return route.fulfill({contentType:'application/json',body:'{}'});
  });
  await page.goto('http://scanner.test/submitbarcode');
  await page.waitForTimeout(300);
  await page.locator('#addItemBtn').click();
  await page.waitForTimeout(800);

  assert.ok(posts.length>=1,scenario+': the receipt was posted');
  const first=posts[0].fields;
  assert.equal(first.barcode,'123456789012,123456789012,123456789012');
  assert.deepEqual(JSON.parse(first.entries),[
   {code:'123456789012',suffix:0,quantity:2,note:''},
   {code:'123456789012',suffix:0,quantity:1,note:'Torn box'}
  ]);
  assert.match(first.submission_id,/^[A-Za-z0-9_-]{8,64}$/);
  assert.equal(posts[0].headers['x-requested-with'],'fetch');

  if(scenario==='rejected'){
   assert.equal(posts.length,1,'a 4xx must not be re-posted natively');
   assert.deepEqual(dialogs,['Error: Unknown shelf position: a1']);
   assert.equal(new URL(page.url()).pathname,'/submitbarcode');
   assert.equal(await page.evaluate(()=>sessionStorage.getItem('barcode_entries')!==null),true,'the list is kept for the operator to fix');
  }
  if(scenario==='retry'){
   assert.equal(posts.length,2,'a 5xx falls back to one native submit');
   assert.equal(posts[1].navigation,true);
   assert.equal(posts[1].fields.submission_id,first.submission_id,'the retry reuses the receipt id so the server can dedupe it');
   assert.equal(posts[1].fields.entries,first.entries);
  }
  if(scenario==='saved'){
   await page.waitForURL('**/position');
   assert.equal(posts.length,1);
   assert.equal(await page.evaluate(()=>sessionStorage.getItem('barcode_entries')),null);
   assert.equal(await page.evaluate(()=>sessionStorage.getItem('item_position')),null);
  }
  assert.deepEqual(errors,[],scenario+': no page errors');
  await page.close();
 }
 await browser.close();
 console.log('additem receipt checks passed');
})().catch(e=>{console.error(e);process.exit(1);});
