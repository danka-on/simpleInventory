const assert = require('node:assert/strict');
const fs = require('node:fs');
const {chromium} = require('playwright');

(async () => {
  const browser = await chromium.launch({channel:'msedge', headless: true});
  try {
    const page = await browser.newPage({viewport: {width: 1200, height: 850}});
    const errors = [];
    page.on('pageerror', e => errors.push(e.message));
    const listing = {
      store:'ebay', item_id:'B0HFTDZV8K', listing_key:'123', listing_id:'ebay:123', title:'Serving board <script>unsafe</script>',
      attributes:{brands:['Yinka Ilori']}, upc:'', sku:'office', hint:'office', qty:1, hash:'hash-123', state:'suggested',
      image:'http://reconciliation.test/photo.svg', url:'https://www.ebay.com/itm/123', received:false, warehouse:[], match_kind:'',
      warehouse_qty:0, short_by:0,
      suggestions:[{searchrack_id:7, barcode:'762120414654', title:'Serving board', location:'A1', alternate_titles:['Cafe Custom Dream Mug'],
        quantity:2, image:'http://reconciliation.test/photo.svg', reason:'same title listed on amazon', score:.95}],
    };
    const posts = [];
    let failLink = false;
    let failKey = '';
    let reads = 0;
    let failMore = false;
    let moreCandidates = [];
    const extraListings = [];
    const unlistedItems = [];
    const payload = () => {
      const listings=[listing,...extraListings]; const by_state={};
      for (const l of listings) (by_state[l.state] ||= {listings:0,units:0}).listings++;
      const accounted=listings.filter(l=>l.state==='accounted').length;
      return {success:true, claude_search_available:true, claude_catalog:{rows:1591,estimated_usd_low:.05,estimated_usd_high:.10}, brand_names:{'Yinka Ilori':['yinka ilori','yinka lori']},type_names:{mug:['mug','mugs']},color_names:['blue'], totals:{listings:listings.length, live:listings.filter(l=>l.state!=='handled').length,
        accounted, coverage_pct:Math.round(100*accounted/listings.length), rack_rows:2,short:0,by_state}, listings,unlisted:unlistedItems};
    };
    const html = fs.readFileSync('templates/listing_reconciliation.html','utf8')
      .replace(/\{\{[\s\S]*?\}\}/g,'/static/favicon.ico');
    await page.route('**/*', async route => {
      const req = route.request(); const path = new URL(req.url()).pathname;
      if (path==='/photo.svg') return route.fulfill({contentType:'image/svg+xml',body:'<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="900"><rect width="1200" height="900" fill="blue"/></svg>'});
      if (path==='/listing-reconciliation') return route.fulfill({contentType:'text/html',body:html});
      if (path==='/api/listing-reconciliation' && req.method()==='GET') { reads++; return route.fulfill({json:payload()}); }
      if (req.method()==='POST') {
        const data=req.postDataJSON(); posts.push({path,data});
        if (data.action==='more_matches') {
          if (failMore) return route.fulfill({status:500,json:{success:false,error:'Try loading more again'}});
          const remaining=moreCandidates.filter(s=>!data.seen_ids.includes(s.searchrack_id));
          return route.fulfill({json:{success:true,suggestions:remaining.slice(0,3),has_more_suggestions:remaining.length>3}});
        }
        if (data.action==='search_warehouse') return route.fulfill({json:{success:true,cached:false,ai_search:{checked_at:123,catalog_rows:1591,match_count:1,estimated_cost_usd:.065},suggestions:listing.suggestions.map(s=>({...s,ai_verdict:'possible',reason:'Claude: Saved name agrees; confirm the size.'}))}});
        const target=[listing,...extraListings,...unlistedItems].find(l=>l.listing_key===data.listing_key) || listing;
        if (failLink || data.listing_key===failKey) return route.fulfill({status:409,json:{success:false,error:'The selected warehouse item is unavailable'}});
        if (path.endsWith('/inventory-match')) {
          target.state=data.clear?'suggested':'accounted';
          target.match_kind=data.clear?'':'linked';
          target.link=data.clear?undefined:{searchrack_id:7,stale:false};
          target.warehouse=data.clear?[]:[{searchrack_id:7,barcode:'762120414654',location:'A1',quantity:2}];
        } else {target.state=path.endsWith('/undismiss')?'suggested':'handled'; if(target.stock_status) target.stock_status=path.endsWith('/undismiss')?'needs_matching':'reviewed';}
        return route.fulfill({json:{success:true}});
      }
      return route.fulfill({body:''});
    });
    await page.goto('http://reconciliation.test/listing-reconciliation');
    await page.locator('#workspace').waitFor({state:'visible'});
    await page.locator('.thumb').first().click();
    await page.getByRole('dialog').waitFor({state:'visible'});
    assert.match(await page.locator('#previewImage').getAttribute('src'),/photo.svg/);
    assert.ok((await page.locator('#previewImage').boundingBox()).width>600);
    await page.keyboard.press('Escape');
    await page.getByRole('dialog').waitFor({state:'hidden'});
    await page.locator('.suggestion img').first().focus();
    await page.keyboard.press('Enter');
    await page.getByRole('dialog').waitFor({state:'visible'});
    await page.getByRole('button',{name:'Close image preview'}).click();
    await page.getByRole('dialog').waitFor({state:'hidden'});
    assert.equal(await page.locator('#tileLive').innerText(),'1');
    assert.equal(await page.locator('#itemList script').count(),0);
    const finder = await page.getByRole('link',{name:'Find in warehouse'}).getAttribute('href');
    assert.equal(new URL(finder,'http://reconciliation.test').searchParams.get('listing_key'),'123');
    await page.locator('#storeFilter').selectOption('amazon');
    assert.match(await page.locator('#itemList').innerText(),/Nothing here/);
    await page.locator('#storeFilter').selectOption('');
    await page.locator('#searchBox').fill('office');
    assert.equal(await page.locator('.item').count(),1);
    for (const query of ['board serving', 'servng board', 'serving office', 'A1', 'Café custom mug',
      '0762120414654', 'https://www.amazon.com/dp/B0HFTDZV8K']) {
      await page.locator('#searchBox').fill(query);
      assert.equal(await page.locator('.item').count(),1,query);
    }
    await page.locator('#searchBox').fill('0762120414655');
    assert.equal(await page.locator('.item').count(),0,'A wrong barcode must not fuzzy-match');
    await page.locator('#searchBox').fill('serving');
    await page.getByRole('tab',{name:'Enough stock (0)',exact:true}).click();
    assert.equal(await page.locator('.item').count(),1,'Search includes other statuses');
    await page.locator('#searchAllTabs').uncheck();
    assert.equal(await page.locator('.item').count(),0,'Current tab scope is honored');
    await page.locator('#searchAllTabs').check();
    await page.getByRole('tab',{name:'Needs matching (1)',exact:true}).click();
    await page.getByRole('button',{name:'Clear search',exact:true}).click();
    assert.equal(await page.locator('#searchBox').inputValue(),'');
    await page.locator('#searchBox').fill('office');
    await page.locator('#brandFilter').selectOption('Yinka Ilori');
    assert.equal(await page.locator('.item').count(),1);
    await page.locator('#searchBox').fill('yinka lori');
    assert.equal(await page.locator('.item').count(),1,'Brand alias can search canonical brand');
    await page.locator('#brandFilter').selectOption('');
    await page.locator('#searchBox').fill('office');
    assert.equal(await page.getByRole('button',{name:'Compare photos',exact:true}).count(),0);
    const readsBeforeClaude=reads;
    await page.getByRole('button',{name:'Search whole warehouse with Claude',exact:true}).click();
    await page.getByText('Claude: possible match',{exact:true}).first().waitFor();
    assert.equal(reads,readsBeforeClaude,'Claude search does not reload reconciliation');
    assert.equal(posts.at(-1).data.action,'search_warehouse');
    assert.deepEqual(Object.keys(posts.at(-1).data).sort(),['action','listing_key','store']);
    assert.match(await page.locator('.item').first().innerText(),/1,591 stocked rows/);
    assert.equal(await page.getByRole('button',{name:'View cached Claude search',exact:true}).count(),1);
    failLink=true;
    await page.getByRole('button',{name:'Link',exact:true}).click();
    await page.getByText('The selected warehouse item is unavailable',{exact:true}).waitFor();
    assert.equal(await page.getByRole('button',{name:'Link',exact:true}).isEnabled(),true);
    failLink=false;
    await page.getByRole('button',{name:'Link',exact:true}).click();
    await page.waitForFunction(()=>document.querySelector('#tileCoverage').textContent==='100%');
    assert.equal(posts.at(-1).data.searchrack_id,7);
    assert.equal(posts.at(-1).data.finder_learn,true);
    await page.getByRole('tab',{name:'Enough stock (1)',exact:true}).click();
    await page.getByRole('button',{name:'Unlink',exact:true}).click();
    await page.waitForFunction(()=>document.querySelector('#tileCoverage').textContent==='0%');
    await page.getByRole('tab',{name:'Needs matching (1)',exact:true}).click();
    await page.getByRole('button',{name:'Mark reviewed',exact:true}).click();
    await page.getByRole('tab',{name:'Reviewed (1)',exact:true}).waitFor();
    assert.equal(await page.locator('#tileLive').innerText(),'1');
    assert.equal(await page.locator('#tileCoverage').innerText(),'0%');
    await page.getByRole('tab',{name:'Reviewed (1)',exact:true}).click();
    await page.getByRole('button',{name:'Reopen issue',exact:true}).click();
    await page.getByRole('tab',{name:'Needs matching (1)',exact:true}).waitFor();
    await page.getByRole('tab',{name:'Needs matching (1)',exact:true}).click();
    await page.setViewportSize({width:390,height:844});
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
    assert.equal(await page.locator('.subtitle').count(),0);
    extraListings.push({...listing,listing_key:'456',listing_id:'ebay:456',title:'Second board',state:'suggested'});
    listing.suggestions.push({...listing.suggestions[0],searchrack_id:8,barcode:'882864344090'});
    await page.getByRole('button',{name:'Refresh',exact:true}).click();
    await page.getByRole('tab',{name:'Needs matching (2)',exact:true}).waitFor();
    const first=page.locator('.item[data-key="123"]');
    const second=page.locator('.item[data-key="456"]');
    await first.locator('[data-select-link]').nth(0).check();
    await first.locator('[data-select-link]').nth(1).check();
    assert.equal(await first.locator('[data-select-link]:checked').count(),1);
    await second.locator('[data-select-link]').nth(0).check();
    await page.locator('#storeFilter').selectOption('amazon');
    await page.getByRole('button',{name:'Link selected (2)',exact:true}).waitFor();
    await page.locator('#storeFilter').selectOption('');
    assert.equal(await page.locator('[data-select-link]:checked').count(),2);
    const beforeReads=reads; const beforePosts=posts.length;
    failKey='456';
    await page.getByRole('button',{name:'Link selected (2)',exact:true}).click();
    await page.waitForFunction(()=>document.querySelector('#linkBulkStatus').textContent.includes('1 failed') && !document.querySelector('#linkSelectedButton').disabled);
    assert.equal(reads-beforeReads,1);
    assert.equal(posts.length-beforePosts,2);
    assert.equal(posts[beforePosts].data.searchrack_id,8);
    assert.equal(await page.locator('[data-select-link]:checked').count(),1);
    assert.equal(await page.locator('#tileCoverage').innerText(),'50%');
    failKey='';
    await page.getByRole('button',{name:'Link selected (1)',exact:true}).click();
    await page.waitForFunction(()=>document.querySelector('#tileCoverage').textContent==='100%');
    assert.equal(reads-beforeReads,2);
    assert.equal(posts.length-beforePosts,3);
    assert.equal(posts.at(-1).data.listing_key,'456');
    assert.equal(await page.locator('[data-select-link]:checked').count(),0);
    // Expansion appends distinct candidates, retains selection, and never refreshes the page or runs Claude.
    extraListings.length=0;
    listing.state='suggested'; listing.warehouse=[]; delete listing.link;
    listing.has_more_suggestions=true;
    moreCandidates=Array.from({length:10},(_,i)=>({...listing.suggestions[0],searchrack_id:100+i,barcode:String(100000000000+i)}));
    listing.suggestions=moreCandidates.slice(0,3);
    await page.getByRole('button',{name:'Refresh',exact:true}).click();
    await page.getByRole('tab',{name:'Needs matching (1)',exact:true}).click();
    await page.locator('[data-select-link]').first().check();
    const expansionReads=reads; const expansionPosts=posts.length;
    failMore=true;
    await page.getByRole('button',{name:'Show 3 more matches',exact:true}).click();
    await page.getByText('Try loading more again',{exact:true}).waitFor();
    assert.equal(await page.locator('.suggestion').count(),3);
    failMore=false;
    for (const total of [6,9,10]) {
      await page.getByRole('button',{name:'Show 3 more matches',exact:true}).click();
      await page.waitForFunction(count=>document.querySelectorAll('.suggestion').length===count,total);
      assert.equal(await page.locator('[data-select-link]:checked').count(),1);
    }
    assert.equal(await page.getByRole('button',{name:'Show 3 more matches',exact:true}).count(),0);
    await page.getByText('No more matching products found locally.',{exact:true}).waitFor();
    assert.equal(reads,expansionReads);
    assert.ok(posts.slice(expansionPosts).every(p=>p.data.action==='more_matches'));
    assert.equal(new Set(await page.locator('[data-select-link]').evaluateAll(inputs=>inputs.map(i=>i.dataset.searchrackId))).size,10);
    await page.locator('#storeFilter').selectOption('amazon');
    await page.locator('#storeFilter').selectOption('');
    assert.equal(await page.locator('.suggestion').count(),10);
    assert.equal(await page.locator('[data-select-link]:checked').count(),1);
    // One consolidated page distinguishes a valid match from enough available stock.
    listing.state='accounted'; listing.stock_status='short_stock'; listing.physical_qty=3;
    listing.pending_sale_qty=2; listing.available_qty=1; listing.qty=3;
    extraListings.push({...listing,listing_key:'OUT',listing_id:'ebay:OUT',title:'Sold last mug',
      stock_status:'out_of_stock',physical_qty:1,pending_sale_qty:1,available_qty:0,qty:1});
    unlistedItems.push({...listing,store:'warehouse',listing_key:'UNLISTED',listing_id:'warehouse:UNLISTED',
      title:'Unlisted plate',state:'warehouse_unlisted',stock_status:'unlisted',upc:'100000000099',
      physical_qty:2,pending_sale_qty:0,available_qty:2,qty:0,suggestions:[]});
    await page.getByRole('button',{name:'Refresh',exact:true}).click();
    await page.getByRole('button',{name:'Clear search',exact:true}).click();
    await page.getByRole('tab',{name:'All active listings (2)',exact:true}).click();
    assert.equal(await page.locator('.item').count(),2);
    await page.getByRole('tab',{name:'Not enough stock (1)',exact:true}).click();
    assert.equal(await page.locator('.item').count(),1);
    assert.match(await page.locator('.item').innerText(),/Physical: 3 · Pending sales: 2 · Available: 1/);
    await page.getByRole('tab',{name:'Out of stock (1)',exact:true}).click();
    assert.match(await page.locator('.item').innerText(),/Sold last mug/);
    await page.getByRole('tab',{name:'Warehouse without listings (1)',exact:true}).click();
    assert.equal(await page.locator('.item').count(),1);
    assert.match(await page.getByRole('link',{name:'Create listing',exact:true}).getAttribute('href'),/upc=100000000099/);
    assert.equal(await page.getByRole('button',{name:'Search whole warehouse with Claude',exact:true}).count(),0);
    await page.goto('http://reconciliation.test/listing-reconciliation?tab=no_warehouse');
    await page.getByRole('tab',{name:'Out of stock (1)',exact:true}).waitFor();
    assert.equal(await page.locator('.item').count(),1);
    assert.match(await page.locator('.item').innerText(),/Sold last mug/);
    assert.deepEqual(errors,[]);
    console.log('Reconciliation browser checks passed: filters, safe rendering, Finder handoff, link failure/retry, unlink, dismiss/restore, totals, mobile layout, multi-select, candidate replacement, one refresh per batch, partial failure and retry.');
  } finally { await browser.close(); }
})().catch(error=>{console.error(error);process.exitCode=1;});
