/* Native eBay recommendations: explicit review, durable task IDs, no late draft writes. */
(() => {
  'use strict';
  const el = id => document.getElementById(id);
  const make = (tag, text, cls) => {
    const node = document.createElement(tag);
    if(text) node.textContent = text;
    if(cls) node.className = cls;
    return node;
  };
  const box = make('section', '', 'ws-native-mapping');
  box.append(make('h3', 'Let eBay fill the details'));
  box.append(make('p', 'Use your title, barcode and photos to get eBay’s own listing recommendations. Review them, then apply.'));
  const start = make('button', 'Get eBay recommendations', 'btn btnPrimary');
  start.type = 'button';
  const status = make('p', '', 'ws-match-status');
  status.setAttribute('role', 'status');
  const results = make('div');
  const connection = make('p');
  box.append(start, status, connection, results);
  el('cardStoreCatalog').querySelector('.cardBody').prepend(box);
  const fallback = make('details', '', 'ws-similar-fallback');
  fallback.id = 'ebaySimilarFallback';
  const fallbackSummary = make('summary', 'Similar listings · use as a fallback');
  fallbackSummary.style.cursor = 'pointer';
  fallbackSummary.style.fontWeight = '600';
  fallback.append(fallbackSummary);
  const similarFields = el('ebayCatalogQ').closest('.field');
  similarFields.before(fallback);
  fallback.append(similarFields);
  if(el('ebayMatchStatus')) fallback.append(el('ebayMatchStatus'));
  function showFallback(open) {
    fallback.open = open;
    fallbackSummary.textContent = open
      ? 'Similar listings · find details here'
      : 'Similar listings · use as a fallback';
  }
  let generation = 0, timer = null, current = '', running = false, baseline = null;
  let recoveredMismatchedTask = false;
  const snapshot = () => ({title:el('title').value, categoryId:el('categoryId').value,
    description:getDescHtml(), aspects:JSON.stringify(collectEbayAspects())});
  const same = (upc, version) => String(state.activeUpc || '') === upc && generation === version;
  const save = () => _scheduleWorkingDraftSave();

  function showPreview(previews, upc, version) {
    results.replaceChildren();
    for(const preview of previews) {
      const card = make('div', '', 'ws-native-preview');
      card.append(make('h4', preview.title || 'eBay recommendation'));
      card.append(make('p', preview.categoryName || preview.categoryId));
      const facts = make('dl');
      for(const [name, values] of Object.entries(preview.aspects || {})) {
        facts.append(make('dt', name), make('dd', values.join(', ')));
      }
      card.append(facts);
      if(preview.description) {
        const detail = make('details'); detail.append(make('summary', 'Suggested description'));
        // Server allowlists formatting and removes all attributes/active content.
        const description = make('div'); description.innerHTML = preview.description;
        detail.append(description); card.append(detail);
      }
      const hints = Object.entries(preview.hints || {});
      if(hints.length) card.append(make('p', 'Needs verification (not applied): ' + hints.map(([n,v]) => `${n}: ${v.join(', ')}`).join(' · ')));
      const apply = make('button', 'Apply eBay details', 'btn btnPrimary'); apply.type = 'button';
      apply.addEventListener('click', () => {
        if(!same(upc, version)) return;
        if(el('offerId').value) { status.textContent = 'This item has an existing offer. Use its original edit flow.'; return; }
        const now = snapshot();
        // A request completing must never overwrite edits made while it was in flight.
        // A missing baseline can only fill blanks.
        const allowed = key => baseline ? now[key] === baseline[key] : !now[key] || now[key] === '{}';
        const safeAspects = allowed('aspects') ? preview.aspects : {};
        _applyEbayCatalogProduct(preview.epid || '', allowed('title') ? preview.title : '', '', safeAspects,
          {ifEmpty:false, categoryId:allowed('categoryId') ? preview.categoryId : ''});
        if(preview.description && allowed('description')) setDescHtml(preview.description);
        el('mappingReferenceId').value = preview.mappingReferenceId;
        showFallback(false);
        save(); renderPreview();
        apply.disabled = true;
        status.textContent = 'eBay details applied. Your newer edits were kept. Check condition, price and shipping before listing.';
      });
      card.append(apply); results.append(card);
    }
  }

  async function poll(taskId, upc, version, began, attempt=0) {
    try {
      const response = await apiGet('/api/listingagent/ebay/mapping/task?' + new URLSearchParams({taskId, upc}));
      if(!same(upc, version)) return;
      if(response.status === 'processing') {
        if(Date.now() - began > 600000) {
          running = false; start.disabled = false; start.textContent = 'Check eBay recommendations';
          showFallback(true);
          status.textContent = 'eBay is still working. You can continue editing and check again later.'; return;
        }
        status.textContent = 'eBay is preparing your recommendations. You can keep editing.';
        timer = setTimeout(() => poll(taskId, upc, version, began, attempt+1), Math.min(5000 + attempt*2000, 20000));
        return;
      }
      running = false; start.disabled = false; start.textContent = 'Refresh eBay recommendations';
      const usable = response.status === 'ready' && (response.previews || []).some(preview =>
        preview.title || preview.description || preview.categoryId || Object.keys(preview.aspects || {}).length);
      showFallback(!usable);
      status.textContent = usable ? 'Ready to review. Only confident item specifics are included in Apply.' : (response.message || 'eBay returned no usable details. Choose a similar listing below.');
      showPreview(response.previews || [], upc, version);
    } catch(error) {
      if(!same(upc, version)) return;
      running = false; start.disabled = false; start.textContent = 'Check eBay recommendations';
      if(error.message.startsWith('This recommendation task does not belong to the selected item.')) {
        el('mappingTaskId').value = '';
        save();
        start.textContent = 'Get eBay recommendations';
        if(!recoveredMismatchedTask) {
          recoveredMismatchedTask = true;
          start.click();
          return;
        }
      }
      showFallback(true);
      status.textContent = error.message;
    }
  }
  start.addEventListener('click', async () => {
    const upc = String(state.activeUpc || '');
    if(!upc || running) return;
    clearTimeout(timer); const version = ++generation;
    running = true; start.disabled = true; results.replaceChildren(); baseline = snapshot();
    showFallback(false);
    status.textContent = 'Sending product details to eBay…';
    try {
      if(start.textContent === 'Check eBay recommendations' && el('mappingTaskId').value) {
        await poll(el('mappingTaskId').value, upc, version, Date.now()); return;
      }
      const payload = collectPayload();
      payload.upc = upc; // Task ownership uses the selected unit; only the server's eBay input strips suffixes.
      payload.images = (payload.images || []).map(url => {
        try { return new URL(url, window.location.href).href; } catch { return ''; }
      }).filter(Boolean);
      const response = await apiPost('/api/listingagent/ebay/mapping/start', payload);
      if(!same(upc, version)) return;
      el('mappingTaskId').value = response.taskId; save();
      status.textContent = response.skippedImages ? 'Some photos aren’t publicly accessible over HTTPS; eBay will use the remaining product details.' : 'eBay is preparing recommendations…';
      connection.textContent = response.publishingConnected ? '' : 'Your eBay publishing connection needs setup before these recommendations can be listed. You can still review and save the draft.';
      await poll(response.taskId, upc, version, Date.now());
    } catch(error) {
      if(!same(upc, version)) return;
      running = false; start.disabled = false; status.textContent = error.message;
      showFallback(true);
    }
  });
  function refresh() {
    const upc = String(state.activeUpc || '');
    box.hidden = state.activeStore === 'amazon';
    if(current !== upc) {
      current = upc; generation++; clearTimeout(timer); running = false; baseline = null;
      recoveredMismatchedTask = false;
      showFallback(false);
      results.replaceChildren(); status.textContent = ''; connection.textContent = ''; start.textContent = 'Get eBay recommendations';
    }
    start.disabled = running || !upc || !!el('offerId').value || el('marketplaceId').value !== 'EBAY_US';
    if(upc && (el('offerId').value || el('marketplaceId').value !== 'EBAY_US')) showFallback(true);
    if(!running && upc && el('mappingTaskId').value && !results.childElementCount && !status.textContent) {
      baseline = snapshot(); running = true; start.disabled = true;
      poll(el('mappingTaskId').value, upc, generation, Date.now());
    }
  }
  for(const name of ['renderPreview', '_applyWorkingDraft']) {
    const original = window[name];
    if(typeof original === 'function') window[name] = function(...args) {
      const value = original.apply(this,args); queueMicrotask(refresh); return value;
    };
  }
  document.addEventListener('change', refresh);
  refresh();
})();
