(() => {
  'use strict';
  const root=document.querySelector('main'), $=id=>document.getElementById(id);
  const endpoint=`/api/fba-prep/sessions/${encodeURIComponent(root.dataset.session)}/resolution`, barcode=root.dataset.barcode;
  let data=null, selected=null, busy=false, next=null;
  const esc=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const link=asin=>`https://www.amazon.com/dp/${encodeURIComponent(asin)}`;
  function lock(value){busy=value;$('refresh').disabled=value;$('more').disabled=value;$('submit').disabled=value||!selected||!$('confirm').checked;document.querySelectorAll('[data-choose]').forEach(b=>b.disabled=value||!data?.brand_issue);}
  function clearChoice(){selected=null;$('review').hidden=true;$('confirm').checked=false;document.querySelectorAll('article').forEach(n=>n.classList.remove('selected'));}
  async function load(more=false){
    if(busy)return;lock(true);if(!more){clearChoice();$('cards').replaceChildren();data=null;}
    $('status').textContent='Checking Amazon…';
    try{
      const response=await fetch(`${endpoint}?barcode=${encodeURIComponent(barcode)}&offset=${more?next:0}`), result=await response.json();
      if(!response.ok||!result.success)throw Error(result.error||'Could not load Amazon comparison.');
      if(more&&data.fingerprint!==result.fingerprint)throw Error('The family changed. Refresh before choosing a correction.');
      data=result;next=result.next_offset;
      $('current').innerHTML=`<h2>Your existing listing</h2><strong>${esc(result.current.title)}</strong><p>SKU: ${esc(result.sku||'Not created')} · ASIN: ${esc(result.current.asin)}</p><p>Catalog brand: <strong>${esc(result.current.brand||'Not supplied')}</strong> · Size: ${esc(result.current.size||'Not supplied')} · Color: ${esc(result.current.color||'Not supplied')}</p><a href="${link(result.current.asin)}" target="_blank" rel="noopener noreferrer">Open current product on Amazon ↗</a>`;
      const errors=result.issues.filter(i=>i.severity==='ERROR');
      $('status').textContent=errors.length?errors.map(i=>i.message).join('\n\n'):'Amazon currently reports no blocking listing issues. Use the normal FBA readiness checks before packing.';
      if(!result.brand_issue)$('status').textContent+='\nBrand editing is available when Amazon reports a brand issue. Other issues can be reviewed in Seller Central.';
      for(const card of result.cards){
        const article=document.createElement('article');article.dataset.asin=card.asin;
        article.innerHTML=`${card.asin===result.current.asin?'<span class="tag">CURRENT ITEM</span>':''}${card.image?`<img src="${esc(card.image)}" alt="${esc(card.title)}" loading="lazy">`:''}<h3>${esc(card.title||card.asin)}</h3><p>Brand: <strong>${esc(card.brand||'Not supplied')}</strong></p><p>Size: ${esc(card.size||'Not supplied')}<br>Color: ${esc(card.color||'Not supplied')}</p><a href="${link(card.asin)}" target="_blank" rel="noopener noreferrer">${esc(card.asin)} ↗</a><button data-choose ${!card.brand||!result.brand_issue?'disabled':''}>Use this brand: ${esc(card.brand||'Unavailable')}</button>`;
        article.querySelector('button').onclick=()=>{if(busy||!data.brand_issue||!card.brand)return;clearChoice();selected=card;article.classList.add('selected');$('proposal').textContent=`Submit brand “${card.brand}” for SKU ${data.sku}, ASIN ${data.current.asin}. Current catalog brand: ${data.current.brand||'not supplied'}.`;$('review').hidden=false;$('review').scrollIntoView({behavior:'smooth',block:'nearest'});lock(false);};
        $('cards').append(article);
      }
      if(result.failed_asins.length)$('status').textContent+='\nSome comparison products could not load: '+result.failed_asins.join(', ')+'. Refresh to retry.';
      $('more').hidden=next===null;
    }catch(error){$('status').textContent=error.message;clearChoice();data=null;}
    finally{lock(false);}
  }
  $('refresh').onclick=()=>load();$('more').onclick=()=>load(true);$('cancel').onclick=clearChoice;$('confirm').onchange=()=>lock(busy);
  $('submit').onclick=async()=>{
    if(busy||!selected||!$('confirm').checked)return;
    lock(true);
    try{
      const response=await fetch(endpoint,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({barcode,selected_asin:selected.asin,selected_brand:selected.brand,fingerprint:data.fingerprint,confirm_packaging:true})});
      const result=await response.json();if(!response.ok||!result.success)throw Error(result.error||'Submission could not be confirmed.');
      $('status').textContent=result.message+(result.response?.issues?.length?'\n\n'+result.response.issues.map(i=>i.message).join('\n'):'');clearChoice();
    }catch(error){$('status').textContent=error.message;clearChoice();}
    finally{lock(false);$('status').scrollIntoView({behavior:'smooth',block:'center'});}
  };
  load();
})();
