/* Restructure the editor without replacing its marketplace or warehouse handlers. */
(() => {
  'use strict';
  const byId = id => document.getElementById(id);
  const make = (tag, cls, text) => {
    const el = document.createElement(tag);
    if (cls) el.className = cls;
    if (text) el.textContent = text;
    return el;
  };
  document.body.classList.add('listing-workspace');
  byId('previewEmpty').replaceChildren(
    make('p', '', 'Choose an item from your queue to start. Product details and saved work will load automatically.'),
    Object.assign(make('a', 'btn btnGhost', 'Open List Manager'), {href:'/items-to-list'})
  );
  const intro = make('div', 'ws-intro');
  intro.innerHTML = '<div><div class="ws-eyebrow">Sweet Shelves / Sell</div></div><div class="ws-stat"><strong id="wsQueueTotal">0</strong>in your queue</div>';
  document.querySelector('.wrap').prepend(intro);

  const queueBody = byId('cardListing').querySelector('.cardBody');
  const filter = make('input', 'ws-filter');
  filter.type = 'search'; filter.placeholder = 'Find in queue…';
  filter.setAttribute('aria-label', 'Filter queue by title or barcode');
  const count = make('div', 'ws-count');
  count.setAttribute('aria-live', 'polite');
  queueBody.prepend(filter, count);
  function filterQueue() {
    const query = filter.value.trim().toLowerCase();
    let visible = 0;
    byId('queueList').querySelectorAll('.queueItem').forEach((row, index) => {
      const item = (state.queue || [])[index];
      row.hidden = !`${row.textContent} ${item?.upc || ''}`.toLowerCase().includes(query);
      row.style.display = row.hidden ? 'none' : '';
      if (!row.hidden) visible++;
      row.tabIndex = 0;
      row.setAttribute('role', 'button');
      row.setAttribute('aria-label', `Open ${item?.title || item?.upc || row.textContent}`);
      row.setAttribute('aria-current', row.classList.contains('active') ? 'true' : 'false');
    });
    count.textContent = query && !visible ? 'No matching items. Try another title or barcode.' : `${visible} item${visible === 1 ? '' : 's'}${query ? (visible === 1 ? ' matches your search' : ' match your search') : ' · select to continue'}`;
    byId('wsQueueTotal').textContent = (state.queue || []).length;
  }
  filter.addEventListener('input', filterQueue);
  byId('queueList').addEventListener('keydown', e => {
    if ((e.key === 'Enter' || e.key === ' ') && e.target.matches('.queueItem')) {
      e.preventDefault(); e.target.click();
    }
  });
  new MutationObserver(filterQueue).observe(byId('queueList'), {childList:true});

  function rename(id, text) {
    const heading = byId(id)?.querySelector('.cardHeader h2');
    if (heading) heading.textContent = text;
  }
  rename('cardItemPreview', '01 / Item & photos');
  rename('cardStoreCatalog', 'Get listing details from eBay');
  rename('cardEbayDraft', '02 / Listing details');
  rename('cardEbayAspects', '03 / Item specifics');
  rename('cardSettings', '06 / Shipping & returns');
  rename('cardStatusLog', 'Activity');
  const ebay = byId('storeEbayPage');
  const draft = byId('cardEbayDraft');
  const draftBody = draft.querySelector('.cardBody');
  const catalog = byId('cardStoreCatalog');
  catalog.classList.remove('collapsed');
  ebay.prepend(draft);
  byId('cardItemPreview').before(catalog);
  const matchStatus = make('p', 'ws-match-status');
  matchStatus.id = 'ebayMatchStatus'; matchStatus.setAttribute('role', 'status');
  catalog.querySelector('.cardBody').append(matchStatus);
  const category = byId('categoryId').closest('.field');
  const pricingSection = byId('price').closest('.formSection');
  const descriptionSection = byId('listingDescEditor').closest('.formSection');
  byId('title').closest('.field').after(category);
  function section(id, title, content) {
    const card = make('section', 'card'); card.id = id;
    const header = make('div', 'cardHeader');
    header.append(make('h2', '', title));
    const body = make('div', 'cardBody'); body.append(content);
    card.append(header, body); ebay.append(card);
    return card;
  }
  section('cardWsPrice', '04 / Price & quantity', pricingSection);
  pricingSection.querySelector('.formSectionLabel')?.remove();
  pricingSection.querySelector('.row2').append(byId('quantity').closest('.field'));
  section('cardWsDescription', '05 / Description', descriptionSection);
  descriptionSection.querySelector('.formSectionLabel')?.remove();
  ebay.append(byId('cardSettings'));
  const policyHint = make('p', 'ws-policies', 'Your saved eBay policies control shipping costs, handling time, payment, and returns. Reuse them to list faster.');
  byId('cardSettings').querySelector('.cardBody').prepend(policyHint);
  for (const [id, label] of [['fulfillmentPolicyId','Shipping policy'],['paymentPolicyId','Payment policy'],['returnPolicyId','Return policy']]) {
    document.querySelector(`label[for="${id}"]`).textContent = label;
  }
  byId('listingDuration').options[0].textContent = 'Good ’til cancelled';
  byId('condition').querySelectorAll('option').forEach(option => {
    option.textContent = option.textContent.replace(/\s*\([A-Z_]+\)$/, '');
  });
  byId('title').setAttribute('aria-describedby', 'wsTitleCount');
  const titleCount = make('span', 'ws-title-count'); titleCount.id = 'wsTitleCount';
  document.querySelector('label[for="title"]').append(titleCount);
  // Technical identifiers stay available, below the everyday listing controls.
  const identifiers = make('details', 'ws-identifiers');
  identifiers.append(make('summary', '', 'Inventory identifiers'));
  const identifierRow = byId('upc').closest('.row2');
  identifierRow.previousElementSibling?.classList.contains('formSectionLabel') && identifierRow.previousElementSibling.remove();
  identifiers.append(identifierRow); draftBody.append(identifiers);

  const nav = make('nav', 'ws-nav'); nav.setAttribute('aria-label', 'Listing sections');
  const sections = [['cardStoreCatalog','Find match'],['cardItemPreview','Photos'],['cardEbayDraft','Details'],['cardEbayAspects','Specifics'],['cardWsPrice','Price'],['cardWsDescription','Description'],['cardSettings','Shipping']];
  function jump(id) {
    const el = byId(id); if (!el) return;
    for (let parent = el; parent; parent = parent.parentElement) {
      if (parent.classList.contains('card')) parent.classList.remove('collapsed');
      if (parent.tagName === 'DETAILS') parent.open = true;
    }
    el.scrollIntoView({behavior:matchMedia('(prefers-reduced-motion: reduce)').matches ? 'instant' : 'smooth', block:'center'});
    const focus = el.matches('input,select,textarea,[contenteditable]') ? el : el.querySelector('input:not([readonly]),select,textarea,button');
    focus?.focus({preventScroll:true});
  }
  for (const [id, label] of sections) {
    const button = make('button', '', label); button.type = 'button';
    button.onclick = () => jump(id); nav.append(button);
  }
  document.querySelector('.storeBar').after(nav);

  const review = make('aside', 'ws-review'); review.setAttribute('aria-label', 'Listing review');
  review.innerHTML = `<div class="card"><div class="cardBody">
    <div class="ws-eyebrow">Before you list</div><h2 style="margin-top:7px">Listing review</h2>
    <img id="wsCover" class="ws-cover" alt="Main listing photo" hidden>
    <p id="wsReviewTitle" class="ws-review-title">Your next listing starts here.</p>
    <div id="wsReviewPrice" class="ws-price">—</div><div id="wsReviewMeta" class="ws-meta">Select an item from your queue.</div>
    <hr class="ws-divider"><div id="wsReady" class="ws-status" role="status"></div>
    <div id="wsChecks" class="ws-checks"></div>
    <div id="wsEbayActions" class="ws-actions"></div><div id="wsAmazonActions" class="ws-actions"></div>
    <div class="ws-actions" style="margin-top:8px"><button type="button" class="btn btnGhost" id="wsSave">Save draft</button></div>
    <p id="wsSaveStatus" class="ws-save-status" role="status">Edits save automatically.</p>
    <hr class="ws-divider"><details><summary>What gets prefilled?</summary><p>Choose a similar listing to fill the title, category, available specifics, and suggested asking price. An empty description is built from the product facts. Review the model and variant; your own condition, quantity, photos, and policies stay in place.</p><a href="https://www.ebay.com/sh/lst/active" target="_blank" rel="noopener noreferrer">Open eBay Seller Hub ↗</a></details>
  </div></div>`;
  document.querySelector('.layout').append(review);
  for (const id of ['btnPublish','btnEbayEditListing','ebayListedBar']) byId('wsEbayActions').append(byId(id));
  for (const id of ['btnAmazonPut','btnAmazonEditListing','amazonListedBar']) byId('wsAmazonActions').append(byId(id));
  const save = byId('wsSave');
  byId('btnNextField').onclick = () => {
    refresh();
    const firstMissing = byId('wsChecks').querySelector('button');
    if (firstMissing) firstMissing.click();
    else _nextButtonAction();
  };
  save.onclick = async () => {
    if (!state.activeUpc || save.disabled) return;
    save.disabled = true;
    byId('wsSaveStatus').textContent = 'Saving…';
    await saveWorkingDraftForActiveItem();
    refresh();
  };
  document.addEventListener('keydown', e => {
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 's') {
      e.preventDefault(); save.click();
    }
    if (e.altKey && e.key.toLowerCase() === 'q') { e.preventDefault(); filter.focus(); }
  });

  const targets = {'UPC':'upc','SKU':'sku','Title':'title','Title (maximum 80 characters)':'title','Listing Description':'listingDescEditor','Category':'categoryId','Fulfillment Policy':'fulfillmentPolicyId','Payment Policy':'paymentPolicyId','Return Policy':'returnPolicyId','Price':'price','Quantity':'quantity','Photos':'cardItemPreview','Amazon SKU':'amazonSku','Amazon Price':'amazonPrice','Amazon Quantity':'amazonQuantity','Amazon Condition Note':'amazonConditionNote','Amazon Product Type':'amazonProductType','ASIN or UPC':'amazonAsin'};
  let lastChecks = '';
  function refresh() {
    const amazon = state.activeStore === 'amazon';
    nav.hidden = amazon;
    nav.style.display = amazon ? 'none' : '';
    byId('wsEbayActions').style.display = amazon ? 'none' : 'grid';
    byId('wsAmazonActions').style.display = amazon ? 'grid' : 'none';
    const payload = amazon ? collectAmazonPayload() : collectPayload();
    const selected = !!state.item;
    catalog.style.display = amazon || !selected ? 'none' : '';
    document.body.classList.toggle('ws-no-item', !selected);
    const missing = selected ? _getCompletionMissing() : [];
    const title = (payload.title || '').trim();
    titleCount.textContent = ` ${byId('title').value.length} / 80`;
    titleCount.classList.toggle('over', byId('title').value.length > 80);
    byId('wsReviewTitle').textContent = selected ? title || 'Add a listing title' : 'Your next listing starts here.';
    byId('wsReviewPrice').textContent = selected && Number.isFinite(payload.price) ? new Intl.NumberFormat('en-US',{style:'currency',currency:payload.currency || 'USD'}).format(payload.price) : '—';
    const condition = byId(amazon ? 'amazonConditionType' : 'condition');
    byId('wsReviewMeta').textContent = selected ? `${amazon ? 'Amazon' : 'eBay · Buy It Now'} · Qty ${Number.isFinite(payload.quantity) ? payload.quantity : '—'} · ${condition.selectedOptions[0]?.textContent || ''}` : 'Select an item from your queue.';
    const cover = byId('wsCover'); const image = payload.images?.[0] || '';
    cover.hidden = !selected || !image;
    if (image && cover.getAttribute('src') !== image) cover.src = image;
    byId('wsReady').textContent = !selected ? 'Ready when you are' : missing.length ? `${missing.length} detail${missing.length === 1 ? ' needs' : 's need'} attention` : 'Local checks complete';
    if (selected && missing.length) byId('btnNextField').title = 'Go to the first incomplete detail';
    const signature = JSON.stringify([amazon, selected, missing]);
    if (signature !== lastChecks) {
      lastChecks = signature;
      const checks = byId('wsChecks'); checks.replaceChildren();
      if (selected && !missing.length) checks.append(make('p','hint','Required fields are filled. The marketplace will validate your listing when submitted.'));
      missing.forEach(label => {
        const button = make('button', 'ws-check'); button.type = 'button'; button.dataset.missing = 'true';
        button.append(make('span','','○'),make('span','',label));
        button.onclick = () => {
          if (label.startsWith('Item Specific: ')) {
            const name = label.slice(15);
            const field = [...byId('ebayAspectsBox').querySelectorAll('[data-ebay-aspect]')].find(el => el.dataset.ebayAspect === name);
            if (field) { jump('cardEbayAspects'); field.focus({preventScroll:true}); return; }
          }
          // Shared title/description live on the eBay editor even in Amazon manual mode.
          let target = targets[label] || (amazon ? 'cardAmazonOffer' : 'cardEbayDraft');
          if (amazon && ['title','listingDescEditor','upc'].includes(target)) setStore('ebay');
          jump(target);
        };
        checks.append(button);
      });
    }
    save.disabled = !selected || state.workingDraftSaveStatus === 'saving';
    const status = state.workingDraftSaveStatus;
    byId('wsSaveStatus').textContent = status === 'error' ? 'Draft not saved to server. Keep this page open and retry Save draft.' : status === 'saving' ? 'Saving…' : state.workingDraftDirty ? 'Unsaved changes · saving automatically…' : status === 'saved' ? 'Draft saved to server · Ctrl / ⌘ S to save' : 'Edits save automatically · Ctrl / ⌘ S';
  }
  let queued = false;
  function schedule() {
    if (queued) return; queued = true;
    requestAnimationFrame(() => { queued = false; refresh(); });
  }
  document.addEventListener('input', schedule);
  document.addEventListener('change', schedule);
  document.addEventListener('click', schedule);
  // Existing async catalog, photo, and draft operations update fields without DOM events.
  for (const name of ['renderPreview','_updateNextFieldBtn','renderQueue','updateListingActionState']) {
    const original = window[name];
    if (typeof original === 'function') window[name] = function(...args) {
      const result = original.apply(this,args); schedule(); return result;
    };
  }
  window.addEventListener('beforeunload', e => {
    if (state.workingDraftDirty || state.workingDraftSaveStatus === 'saving') { e.preventDefault(); e.returnValue = ''; }
  });
  document.addEventListener('listing-draft-status', schedule);
  filterQueue(); refresh();
})();
