// Sweet Shelves Lister side panel. Talks to the Sweet Shelves server with the browser's
// Cloudflare sign-in and to the store page through the injected page script.
(() => {
  'use strict';
  const M = globalThis.SSListerMatcher;
  const SETTINGS_KEY = 'ssListerSettings';
  const LEARNED_KEY = 'ssListerLearned';
  const CURRENT_KEY = 'ssListerCurrent';
  const DEFAULTS = { server: 'https://pi.nexuscentralhq.org', actor: '', autoFill: true };
  const CONDITIONS = ['NEW', 'NEW_OTHER', 'NEW_WITH_DEFECTS', 'USED_EXCELLENT', 'USED_VERY_GOOD', 'USED_GOOD', 'USED_ACCEPTABLE', 'FOR_PARTS_OR_NOT_WORKING'];
  const VALUE_LABELS = {
    title: 'Title', price: 'Price', quantity: 'Quantity', sku: 'SKU / custom label', upc: 'UPC', asin: 'ASIN',
    brand: 'Brand', conditionDescription: 'Condition description', description: 'Description', condition: 'Condition',
  };

  const state = {
    settings: { ...DEFAULTS }, connected: null, user: '', serverVersion: '', signIn: false,
    scope: 'selected', filter: '', items: [], currentId: null, edits: {}, tab: null, page: null,
    report: null, pick: null, learned: {}, busy: '', lastLink: null, autoFilled: new Set(),
  };

  const $ = id => document.getElementById(id);
  const esc = value => String(value ?? '').replace(/[&<>"']/g, ch => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[ch]));
  const money = value => (value == null || value === '' ? '' : Number(value).toFixed(2));

  class SignInError extends Error { constructor() { super('Sign in to Sweet Shelves'); this.signIn = true; } }

  function toast(message, bad = false) {
    const el = $('toast');
    el.textContent = message || '';
    el.className = bad ? 'bad' : '';
    if (message) setTimeout(() => { if (el.textContent === message) el.textContent = ''; }, 6000);
  }

  // -- storage ------------------------------------------------------------------------

  async function loadStorage() {
    const stored = await chrome.storage.local.get([SETTINGS_KEY, LEARNED_KEY, CURRENT_KEY]);
    state.settings = { ...DEFAULTS, ...(stored[SETTINGS_KEY] || {}) };
    state.learned = stored[LEARNED_KEY] || {};
    state.currentId = stored[CURRENT_KEY] || null;
  }

  async function saveSettings(settings) {
    state.settings = { ...DEFAULTS, ...settings };
    await chrome.storage.local.set({ [SETTINGS_KEY]: state.settings });
  }

  async function saveLearned() {
    await chrome.storage.local.set({ [LEARNED_KEY]: state.learned });
  }

  // -- server -------------------------------------------------------------------------

  function serverBase() {
    return String(state.settings.server || DEFAULTS.server).replace(/\/+$/, '');
  }

  async function api(path, { method = 'GET', body } = {}) {
    const headers = { Accept: 'application/json', 'X-Sweet-Shelves-Lister': '1' };
    if (body !== undefined) headers['Content-Type'] = 'application/json';
    let response;
    try {
      response = await fetch(serverBase() + path, {
        method, headers, credentials: 'include', cache: 'no-store', redirect: 'manual',
        body: body === undefined ? undefined : JSON.stringify({ ...body, actor: body.actor || state.settings.actor || undefined }),
      });
    } catch (error) {
      throw new Error('Cannot reach ' + serverBase() + ' (' + (error.message || 'network error') + ')');
    }
    // Cloudflare Access answers an expired sign-in with a redirect to its login page.
    if (response.type === 'opaqueredirect' || response.status === 0 || response.status === 401) throw new SignInError();
    const text = await response.text();
    let data;
    try { data = JSON.parse(text); } catch { throw new SignInError(); }
    if (!response.ok && !data.success) throw Object.assign(new Error(data.error || ('HTTP ' + response.status)), { status: response.status, data });
    return data;
  }

  async function connect() {
    try {
      const ping = await api('/api/lister/ping');
      state.connected = true; state.signIn = false; state.user = ping.user || ''; state.serverVersion = ping.version || '';
    } catch (error) {
      state.connected = false; state.signIn = Boolean(error.signIn); state.user = '';
      if (!error.signIn) toast(error.message, true);
    }
    renderHeader();
  }

  async function loadItems() {
    if (state.connected === false) return;
    try {
      const data = await api('/api/lister/items?scope=' + encodeURIComponent(state.scope) + (state.page?.store ? '&platform=' + state.page.store : ''));
      state.items = data.items || [];
      state.connected = true; state.signIn = false;
      if (state.currentId && !state.items.some(it => it.id === state.currentId) && state.scope === 'selected' && state.items.length) state.currentId = state.items[0].id;
      if (!state.currentId && state.items.length) state.currentId = state.items[0].id;
    } catch (error) {
      if (error.signIn) { state.connected = false; state.signIn = true; }
      else toast(error.message, true);
    }
    renderHeader(); renderItems(); renderDetail(); renderConfirm();
  }

  function current() {
    return state.items.find(it => it.id === state.currentId) || null;
  }

  // Values as edited in the panel (edits win over the proposal).
  function values(item) {
    if (!item) return null;
    const edits = state.edits[item.id] || {};
    const fields = { ...item.fields, ...edits };
    if (state.page?.store === 'amazon' && item.existing?.amazon?.[0]?.asin) fields.asin = fields.asin || item.existing.amazon[0].asin;
    return fields;
  }

  // -- page (active tab) -------------------------------------------------------------------

  async function ensurePageScript(tabId) {
    try {
      const pong = await chrome.tabs.sendMessage(tabId, { target: 'ss-lister-page', type: 'ping' });
      if (pong?.ok) return true;
    } catch { /* not injected yet */ }
    await chrome.scripting.executeScript({ target: { tabId }, files: ['matcher.js', 'content.js'] });
    return true;
  }

  async function pageMessage(message) {
    if (!state.tab?.id) throw new Error('No active tab');
    await ensurePageScript(state.tab.id);
    const response = await chrome.tabs.sendMessage(state.tab.id, { target: 'ss-lister-page', ...message });
    if (!response?.ok && response?.reason) throw new Error(response.reason);
    return response;
  }

  let refreshTimer = null;
  function scheduleRefresh() {
    clearTimeout(refreshTimer);
    refreshTimer = setTimeout(() => { void refreshTab(); }, 250);
  }

  async function refreshTab() {
    const [tab] = await chrome.tabs.query({ active: true, lastFocusedWindow: true });
    state.tab = tab || null;
    const page = M.detectPage(tab?.url || '');
    page.url = tab?.url || '';
    state.page = page;
    if (page.store && tab?.id && /^https:/.test(tab.url || '')) {
      try {
        const result = await pageMessage({ type: 'detect' });
        if (result?.ok && result.page) state.page = { ...page, ...result.page };
      } catch (error) {
        state.page.error = error.message;
      }
    }
    renderPage(); renderDetail(); renderConfirm();
    void maybeAutoFill();
  }

  async function maybeAutoFill() {
    const item = current();
    const page = state.page;
    if (!state.settings.autoFill || !item || !page?.store) return;
    if (!(page.kind === 'listing-form' || page.kind === 'offer-form')) return;
    const key = (state.tab?.id || 0) + '|' + (page.url || '').split('#')[0] + '|' + item.id;
    if (state.autoFilled.has(key)) return;
    state.autoFilled.add(key);
    await fillPage({ auto: true });
  }

  async function fillPage({ auto = false } = {}) {
    const item = current();
    if (!item || !state.page?.store) { if (!auto) toast('Open an eBay listing form or a Seller Central offer page first', true); return; }
    state.busy = 'fill'; renderDetail();
    try {
      const store = state.page.store;
      const learned = state.learned[store + ':' + (state.page.kind || '')] || {};
      const result = await pageMessage({ type: 'fill', options: { values: values(item), store, aspects: item.fields.aspects || {}, learned, includeDescription: store === 'ebay' } });
      state.report = result.report;
      const filled = (result.report.filled || []).map(f => f.target);
      toast(filled.length ? `Filled ${filled.length} field${filled.length === 1 ? '' : 's'} on ${store}` : 'No matching fields found on this page. Use "Pick a field".', !filled.length);
      void api('/api/lister/events', { method: 'POST', body: { proposal_id: item.id, event: filled.length ? 'helper_filled' : 'helper_fill_failed', note: `${store} ${state.page.kind || ''}${auto ? ' (auto)' : ''}`, payload: { filled, unmatched: result.report.unmatched, aspects: result.report.aspects } } }).catch(() => {});
    } catch (error) {
      toast('Fill failed: ' + error.message, true);
    } finally {
      state.busy = ''; renderDetail();
    }
  }

  async function startPick() {
    if (!state.page?.store) { toast('Pick works on eBay and Seller Central pages', true); return; }
    try {
      await pageMessage({ type: 'pick-start' });
      state.pick = { waiting: true };
      renderPick();
      toast('Click a field on the store page');
    } catch (error) {
      toast('Could not start picking: ' + error.message, true);
    }
  }

  chrome.runtime.onMessage.addListener(message => {
    if (message?.type === 'ss-lister-picked') {
      state.pick = { waiting: false, pickId: message.pickId, field: message.field, suggestions: message.suggestions || [] };
      renderPick();
    } else if (message?.type === 'ss-lister-pick-cancelled') {
      state.pick = null; renderPick();
    }
  });

  async function applyPick(target, remember) {
    const item = current();
    const pick = state.pick;
    if (!item || !pick?.pickId) return;
    const vals = values(item);
    let value = target === 'description' ? (vals.descriptionText || '') : (vals[target] ?? '');
    const options = {};
    if (target === 'condition') { options.labels = M.conditionLabels(vals.condition, state.page.store); value = options.labels[0] || vals.condition; }
    if (target === 'description') options.html = vals.descriptionHtml || '';
    if (target.startsWith('aspect:')) {
      const aspect = target.slice(7);
      const list = (item.fields.aspects || {})[aspect] || [];
      value = list[0] || ''; options.labels = list;
    }
    try {
      const result = await pageMessage({ type: 'set-picked', pickId: pick.pickId, value: String(value), options });
      if (!result.ok) throw new Error(result.reason || 'could not set that field');
      if (remember && !target.startsWith('aspect:')) {
        const key = state.page.store + ':' + (state.page.kind || '');
        state.learned[key] = { ...(state.learned[key] || {}), [target]: M.signature(pick.field) };
        await saveLearned();
      }
      toast(`Filled ${VALUE_LABELS[target] || target}`);
      state.pick = null; renderPick();
    } catch (error) {
      toast(error.message, true);
    }
  }

  // -- confirm & link ----------------------------------------------------------------------

  async function confirmLink() {
    const item = current();
    if (!item) return;
    const platform = document.querySelector('input[name="cfPlatform"]:checked')?.value || state.page?.store || 'ebay';
    const body = {
      proposal_id: item.id, platform,
      listing_id: $('cfListingId')?.value.trim() || '', sku: $('cfSku')?.value.trim() || '', asin: $('cfAsin')?.value.trim() || '',
      store_upc: $('cfStoreUpc')?.value.trim() || '', url: $('cfUrl')?.value.trim() || '',
      price: $('cfPrice')?.value.trim() || null, quantity: $('cfQuantity')?.value.trim() || null,
      note: $('cfNote')?.value.trim() || '', title: values(item).title,
    };
    state.busy = 'confirm'; renderConfirm();
    try {
      const data = await api('/api/lister/links', { method: 'POST', body });
      state.lastLink = data.link;
      toast(data.duplicate ? 'That listing was already linked' : 'Linked and recorded');
      await loadItems();
    } catch (error) {
      toast(error.message, true);
    } finally {
      state.busy = ''; renderConfirm();
    }
  }

  async function undoLink(linkId) {
    try {
      await api('/api/lister/links/' + linkId, { method: 'DELETE', body: {} });
      state.lastLink = null;
      toast('Link removed');
      await loadItems();
    } catch (error) {
      toast(error.message, true);
    }
  }

  async function setSelected(item, selected) {
    try {
      await api('/api/lister/select', { method: 'POST', body: { proposal_ids: [item.id], selected, platform: state.page?.store || '' } });
      await loadItems();
    } catch (error) {
      toast(error.message, true);
    }
  }

  // -- rendering ---------------------------------------------------------------------------

  function renderHeader() {
    const chip = $('connStatus');
    if (state.connected === null) { chip.textContent = 'connecting…'; chip.className = 'chip'; }
    else if (state.connected) { chip.textContent = state.user ? state.user.split('@')[0] : 'connected'; chip.className = 'chip ok'; chip.title = serverBase() + (state.user ? ' as ' + state.user : ''); }
    else { chip.textContent = state.signIn ? 'sign in' : 'offline'; chip.className = 'chip bad'; }
    $('signin').hidden = !state.signIn;
    $('versionLine').textContent = `Extension ${chrome.runtime.getManifest().version}` + (state.serverVersion ? ` · server ${state.serverVersion}` : '');
  }

  function renderPage() {
    const page = state.page || {};
    const el = $('pageCard');
    if (!page.store) {
      el.innerHTML = `<div class="store"><span class="badge none">no store page</span><span class="muted small">Open eBay <b>Sell</b> (listing form) or Seller Central <b>Add a product</b>, and this panel fills it.</span></div>`;
      return;
    }
    const kindText = {
      'listing-form': 'listing form', 'listing-live': 'live listing', 'seller-hub': 'Seller Hub',
      'offer-form': 'add product / offer form', 'product-search': 'product search', inventory: 'inventory',
    }[page.kind] || 'page';
    const chips = [];
    if (page.listingId && page.store === 'ebay') chips.push(`Item # <code>${esc(page.listingId)}</code>`);
    if (page.asin) chips.push(`ASIN <code>${esc(page.asin)}</code>`);
    if (page.sku) chips.push(`SKU <code>${esc(page.sku)}</code>`);
    if (page.upc) chips.push(`UPC on page <code>${esc(page.upc)}</code>`);
    el.innerHTML = `
      <div class="store"><span class="badge ${page.store}">${page.store === 'ebay' ? 'eBay' : 'Amazon'}</span>
        <span class="grow">${esc(kindText)}${page.fieldCount != null ? ` <span class="muted small">· ${page.fieldCount} fields</span>` : ''}</span>
        <button id="pageRefresh" class="icon" type="button" title="Re-read page">↻</button></div>
      ${chips.length ? `<div class="chips">${chips.join('')}</div>` : ''}
      ${page.error ? `<div class="flag warn">Page script: ${esc(page.error)}</div>` : ''}`;
    $('pageRefresh').onclick = () => refreshTab();
  }

  function renderItems() {
    $('scopeSelected').classList.toggle('active', state.scope === 'selected');
    $('scopeOpen').classList.toggle('active', state.scope === 'open');
    const filter = state.filter.trim().toLowerCase();
    const rows = state.items.filter(it => !filter || (it.fields.title || '').toLowerCase().includes(filter) || (it.upc || '').includes(filter));
    const list = $('itemList');
    if (!rows.length) {
      list.innerHTML = `<div class="empty">${state.connected === false ? 'Not connected.' : state.scope === 'selected'
        ? 'Nothing selected for the browser yet. Switch to <b>All open</b> and tick the proposals you want to list.' : 'No open proposals.'}</div>`;
      return;
    }
    list.innerHTML = rows.map(it => {
      const f = it.fields;
      const stores = [];
      if (it.existing?.ebay?.length) stores.push('<span class="chip warn" title="Already on eBay">eBay ✓</span>');
      if (it.existing?.amazon?.length) stores.push('<span class="chip warn" title="Already on Amazon">Amazon ✓</span>');
      for (const link of it.links || []) stores.push(`<span class="chip ok" title="Linked ${esc(link.created_at)}">${esc(link.platform)} linked</span>`);
      const flags = (it.flags || []).filter(fl => fl.level === 'warn' || fl.level === 'block').length;
      return `<div class="item ${it.id === state.currentId ? 'current' : ''}" data-id="${it.id}">
        <input type="checkbox" data-select="${it.id}" ${it.selected ? 'checked' : ''} title="${it.selected ? 'Remove from browser list' : 'Send to browser list'}">
        ${it.thumb ? `<img src="${esc(it.thumb)}" alt="">` : '<div class="noimg"></div>'}
        <div><div class="title" title="${esc(f.title)}">${esc(f.title || '(no title)')}</div>
          <div class="meta"><span>$${esc(money(f.price))}</span><span>qty ${esc(f.quantity ?? '?')}</span><span>${esc(it.upc)}</span>
          ${it.status !== 'proposed' ? `<span class="chip info">${esc(it.status)}</span>` : ''}${flags ? `<span class="chip warn">${flags} flag${flags > 1 ? 's' : ''}</span>` : ''}${stores.join('')}</div></div>
      </div>`;
    }).join('');
    for (const row of list.querySelectorAll('.item')) {
      row.onclick = event => {
        if (event.target.matches('input[type=checkbox]')) return;
        state.currentId = Number(row.dataset.id); state.report = null;
        void chrome.storage.local.set({ [CURRENT_KEY]: state.currentId });
        renderItems(); renderDetail(); renderConfirm();
      };
    }
    for (const box of list.querySelectorAll('input[data-select]')) {
      box.onchange = () => { const it = state.items.find(x => x.id === Number(box.dataset.select)); if (it) void setSelected(it, box.checked); };
    }
  }

  function renderDetail() {
    const item = current();
    const el = $('detail');
    el.hidden = !item;
    if (!item) return;
    const v = values(item);
    const store = state.page?.store || '';
    const canFill = Boolean(store);
    const aspects = Object.entries(item.fields.aspects || {});
    const copyAll = [
      `Title: ${v.title}`, `Price: ${money(v.price)} ${v.currency || ''}`.trim(), `Quantity: ${v.quantity ?? ''}`, `SKU: ${v.sku}`, `UPC: ${v.upc}`,
      `Condition: ${v.condition}${v.conditionDescription ? ' - ' + v.conditionDescription : ''}`, v.brand ? `Brand: ${v.brand}` : '',
      v.categoryPath ? `eBay category: ${v.categoryPath} (${v.categoryId})` : '',
      aspects.length ? 'Item specifics: ' + aspects.map(([k, vals]) => `${k}=${vals.join('/')}`).join('; ') : '',
      '', v.descriptionText || '',
    ].filter((line, i, arr) => line !== '' || (arr[i + 1] || '') !== '').join('\n');
    const report = state.report;
    el.innerHTML = `
      <h2>Listing values <span class="muted">· proposal #${item.id}</span></h2>
      ${(item.flags || []).map(f => `<div class="flag ${esc(f.level)}">${esc(f.message)}</div>`).join('')}
      ${item.existing?.[store]?.length ? `<div class="flag warn">Already on ${store}: ${item.existing[store].map(x => esc(x.listingId || x.asin || x.sku) + (x.state ? ' (' + esc(x.state) + ')' : '')).join(', ')}. Linking again would make a duplicate.</div>` : ''}
      <label>Title<input id="fTitle" maxlength="80" value="${esc(v.title)}"></label><div class="counter"><span id="titleCount">${(v.title || '').length}</span>/80</div>
      <div class="grid">
        <label>Price (${esc(v.currency || 'USD')})<input id="fPrice" type="number" step="0.01" min="0" value="${esc(v.price ?? '')}"></label>
        <label>Quantity${v.listableQuantity != null ? ` <span class="muted">(max ${esc(v.listableQuantity)})</span>` : ''}<input id="fQuantity" type="number" step="1" min="1" value="${esc(v.quantity ?? '')}"></label>
        <label>SKU / custom label<input id="fSku" value="${esc(v.sku)}"></label>
        <label>UPC<input id="fUpc" value="${esc(v.upc)}"></label>
        <label>Condition<select id="fCondition">${CONDITIONS.map(c => `<option value="${c}" ${c === v.condition ? 'selected' : ''}>${c.replace(/_/g, ' ')}</option>`).join('')}</select></label>
        <label>Brand<input id="fBrand" value="${esc(v.brand)}"></label>
      </div>
      <label>Condition description<input id="fConditionDescription" value="${esc(v.conditionDescription)}"></label>
      <label>Description (plain text; eBay gets the HTML version)<textarea id="fDescription">${esc(v.descriptionText)}</textarea></label>
      <div class="row tight">
        ${v.lots?.length ? `<span class="chip">Lot ${esc(v.lots.join(', '))}</span>` : ''}
        ${v.racks?.length ? `<span class="chip">Rack ${esc(v.racks.join(', '))}</span>` : ''}
        ${v.categoryPath ? `<span class="chip" title="eBay category ${esc(v.categoryId)}">${esc(v.categoryPath)}</span>` : ''}
        ${v.amazonCondition ? `<span class="chip" title="Amazon condition">${esc(v.amazonCondition)}</span>` : ''}
      </div>
      ${aspects.length ? `<details><summary>Item specifics (${aspects.length})</summary><div class="chips">${aspects.map(([k, vals]) => `<code title="click to copy" data-copy="${esc(vals[0])}">${esc(k)}: ${esc(vals.join(' / '))}</code>`).join('')}</div></details>` : ''}
      ${v.images?.length ? `<div class="photos">${v.images.map(u => `<img src="${esc(u)}" alt="" title="${esc(u)}">`).join('')}</div>
        <div class="row tight"><button id="copyPhotos" type="button">Copy ${v.images.length} photo URL${v.images.length > 1 ? 's' : ''}</button><button id="openPhotos" type="button">Open photos in tabs</button><span class="muted small">eBay: Photos → Import from web</span></div>` : '<div class="muted small">No photos on this proposal.</div>'}
      <div class="row">
        <button id="fillBtn" class="primary" type="button" ${canFill && !state.busy ? '' : 'disabled'}>${state.busy === 'fill' ? 'Filling…' : `Fill ${store ? (store === 'ebay' ? 'eBay' : 'Amazon') + ' page' : 'page'}`}</button>
        <button id="pickBtn" type="button" ${canFill ? '' : 'disabled'}>Pick a field…</button>
        <button id="copyAllBtn" type="button">Copy all</button>
        <button id="openAgent" class="link" type="button">Open in Listing Agent</button>
      </div>
      ${report ? renderReport(report) : ''}
      <details><summary>Text for manual paste</summary><pre class="copyall">${esc(copyAll)}</pre></details>`;

    const bind = (id, key, transform = x => x) => { const input = $(id); input.oninput = () => { state.edits[item.id] = { ...(state.edits[item.id] || {}), [key]: transform(input.value) }; if (key === 'title') $('titleCount').textContent = input.value.length; renderConfirmValuesOnly(); }; };
    bind('fTitle', 'title'); bind('fPrice', 'price', x => x === '' ? null : Number(x)); bind('fQuantity', 'quantity', x => x === '' ? null : Number(x));
    bind('fSku', 'sku'); bind('fUpc', 'upc'); bind('fBrand', 'brand'); bind('fConditionDescription', 'conditionDescription'); bind('fDescription', 'descriptionText');
    $('fCondition').onchange = () => { state.edits[item.id] = { ...(state.edits[item.id] || {}), condition: $('fCondition').value }; };
    $('fillBtn').onclick = () => fillPage();
    $('pickBtn').onclick = () => startPick();
    $('copyAllBtn').onclick = () => copy(copyAll, 'Copied listing text');
    $('openAgent').onclick = () => chrome.tabs.create({ url: serverBase() + '/listingagent?upc=' + encodeURIComponent(item.upc) });
    if ($('copyPhotos')) $('copyPhotos').onclick = () => copy(v.images.join('\n'), 'Copied photo URLs');
    if ($('openPhotos')) $('openPhotos').onclick = () => { for (const url of v.images) void chrome.tabs.create({ url, active: false }); };
    for (const code of el.querySelectorAll('code[data-copy]')) code.onclick = () => copy(code.dataset.copy, 'Copied ' + code.dataset.copy);
  }

  function renderReport(report) {
    const rows = [];
    for (const f of report.filled || []) rows.push(`<li class="ok"><span>${esc(VALUE_LABELS[f.target] || f.target)}</span><span class="muted">→ ${esc(f.label || '')}${f.learned ? ' (taught)' : ''}</span></li>`);
    for (const a of report.aspects || []) rows.push(`<li class="${a.ok ? 'ok' : 'bad'}"><span>${esc(a.aspect)}: ${esc(a.value)}</span><span class="muted">${a.ok ? '→ ' + esc(a.label || '') : 'no matching option'}</span></li>`);
    for (const s of report.skipped || []) rows.push(`<li class="bad"><span>${esc(VALUE_LABELS[s.target] || s.target)}</span><span class="muted">${esc(s.reason || '')}</span></li>`);
    for (const u of report.unmatched || []) rows.push(`<li class="miss"><span>${esc(VALUE_LABELS[u] || u)}</span><span class="muted">no field found — use Pick a field</span></li>`);
    return `<ul class="report">${rows.join('')}</ul>`;
  }

  function renderPick() {
    const el = $('pickCard');
    const pick = state.pick;
    el.hidden = !pick;
    if (!pick) return;
    if (pick.waiting) {
      el.innerHTML = `<strong>Pick a field</strong><p class="small muted">Click the field on the store page. Press Esc there to cancel.</p><button id="pickCancel" type="button">Cancel</button>`;
      $('pickCancel').onclick = async () => { try { await pageMessage({ type: 'pick-stop' }); } catch { /* ignore */ } state.pick = null; renderPick(); };
      return;
    }
    const item = current();
    const f = pick.field || {};
    const label = f.labelText || f.ariaLabel || f.placeholder || f.name || f.id || f.tag;
    const suggested = (pick.suggestions || []).slice(0, 3).map(s => s.target);
    const aspects = Object.keys(item?.fields?.aspects || {});
    const others = Object.keys(VALUE_LABELS).filter(t => !suggested.includes(t));
    el.innerHTML = `<strong>Field picked:</strong> <code>${esc(label)}</code> <span class="muted small">(${esc(f.tag)}${f.type ? ' ' + esc(f.type) : ''}${f.value ? ', currently "' + esc(f.value) + '"' : ''})</span>
      <div class="small muted" style="margin-top:6px">Fill it with:</div>
      <div class="suggest">${suggested.map(t => `<button class="primary" data-target="${t}" type="button">${esc(VALUE_LABELS[t] || t)}</button>`).join('')}
        <select id="pickOther"><option value="">other value…</option>${others.map(t => `<option value="${t}">${esc(VALUE_LABELS[t])}</option>`).join('')}${aspects.map(a => `<option value="aspect:${esc(a)}">Item specific: ${esc(a)}</option>`).join('')}</select></div>
      <label class="check"><input id="pickRemember" type="checkbox" checked> Remember this field for ${esc(state.page?.store || 'this store')} ${esc(state.page?.kind || '')} pages</label>
      <div class="row tight"><button id="pickAgain" type="button">Pick another</button><button id="pickClose" type="button">Close</button></div>`;
    for (const button of el.querySelectorAll('button[data-target]')) button.onclick = () => applyPick(button.dataset.target, $('pickRemember').checked);
    $('pickOther').onchange = () => { if ($('pickOther').value) void applyPick($('pickOther').value, $('pickRemember').checked); };
    $('pickAgain').onclick = () => startPick();
    $('pickClose').onclick = () => { state.pick = null; renderPick(); };
  }

  function renderConfirmValuesOnly() {
    const item = current();
    if (!item || !$('cfPrice')) return;
    const v = values(item);
    if (!$('cfPrice').dataset.touched) $('cfPrice').value = v.price ?? '';
    if (!$('cfQuantity').dataset.touched) $('cfQuantity').value = v.quantity ?? '';
  }

  function renderConfirm() {
    const item = current();
    const el = $('confirm');
    el.hidden = !item;
    if (!item) return;
    const page = state.page || {};
    const platform = page.store || (item.platformHint || 'ebay');
    const v = values(item);
    const linked = item.links || [];
    const lastLink = state.lastLink && state.lastLink.upc === item.upc ? state.lastLink : null;
    el.innerHTML = `
      <h2>Confirm &amp; link</h2>
      ${linked.length ? `<div class="flag info">Linked: ${linked.map(l => `${esc(l.platform)} ${esc(l.listing_id || l.sku || l.asin)}${l.url ? ` <a href="${esc(l.url)}" target="_blank" rel="noopener">open</a>` : ''}`).join(' · ')}</div>` : ''}
      ${lastLink ? `<div class="flag info"><b>Recorded.</b><ul class="steps">${(lastLink.effects?.steps || []).map(s => `<li>${esc(s)}</li>`).join('')}</ul><button id="undoLink" class="link danger" type="button">Undo this link</button></div>` : ''}
      <p class="small muted">After the listing is live, confirm it here. The store's item number / SKU is tied to UPC <code>${esc(item.upc)}</code> so Ready to Ship and the warehouse find it even when the store shows another UPC.</p>
      <div class="row tight">
        <label class="check"><input type="radio" name="cfPlatform" value="ebay" ${platform === 'ebay' ? 'checked' : ''}> eBay</label>
        <label class="check"><input type="radio" name="cfPlatform" value="amazon" ${platform === 'amazon' ? 'checked' : ''}> Amazon</label>
      </div>
      <div class="grid">
        <label id="cfListingIdLabel" ${platform === 'amazon' ? 'hidden' : ''}>eBay item number<input id="cfListingId" value="${esc(platform === 'ebay' ? page.listingId || '' : '')}" placeholder="from the live listing URL"></label>
        <label id="cfAsinLabel" ${platform === 'ebay' ? 'hidden' : ''}>ASIN<input id="cfAsin" value="${esc(platform === 'amazon' ? page.asin || v.asin || '' : '')}" placeholder="B0…"></label>
        <label>Seller SKU on the store<input id="cfSku" value="${esc(page.sku || v.sku || '')}"></label>
        <label>UPC shown by the store<input id="cfStoreUpc" value="${esc(page.upc || '')}" placeholder="leave blank if same"></label>
        <label>Price<input id="cfPrice" type="number" step="0.01" value="${esc(v.price ?? '')}"></label>
        <label>Quantity<input id="cfQuantity" type="number" step="1" value="${esc(v.quantity ?? '')}"></label>
      </div>
      <label>Listing URL<input id="cfUrl" value="${esc(page.kind === 'listing-live' || platform === 'amazon' ? page.url || '' : (page.listingId ? 'https://www.ebay.com/itm/' + page.listingId : ''))}" placeholder="https://…"></label>
      <label>Note<input id="cfNote" placeholder="optional"></label>
      <div class="row"><button id="confirmBtn" class="primary" type="button" ${state.busy === 'confirm' ? 'disabled' : ''}>${state.busy === 'confirm' ? 'Recording…' : 'Confirm & link'}</button>
        <span class="muted small">Also marks the proposal listed and the queue done for this store.</span></div>`;
    for (const radio of el.querySelectorAll('input[name="cfPlatform"]')) radio.onchange = () => { $('cfListingIdLabel').hidden = radio.value === 'amazon'; $('cfAsinLabel').hidden = radio.value === 'ebay'; };
    for (const id of ['cfPrice', 'cfQuantity']) $(id).oninput = () => { $(id).dataset.touched = '1'; };
    $('confirmBtn').onclick = () => confirmLink();
    if ($('undoLink')) $('undoLink').onclick = () => undoLink(lastLink.id);
  }

  async function copy(text, message) {
    try { await navigator.clipboard.writeText(text); toast(message || 'Copied'); }
    catch { toast('Clipboard blocked; select the text under "Text for manual paste"', true); }
  }

  // -- wiring --------------------------------------------------------------------------------

  function wireStatic() {
    $('settingsBtn').onclick = () => { const s = $('settings'); s.hidden = !s.hidden; $('setServer').value = state.settings.server; $('setActor').value = state.settings.actor; $('setAutoFill').checked = state.settings.autoFill !== false; };
    $('saveSettings').onclick = async () => {
      let server = $('setServer').value.trim() || DEFAULTS.server;
      try { const u = new URL(server); if (u.protocol !== 'https:' && !/^http:\/\/(localhost|127\.0\.0\.1)/.test(server)) throw new Error(); server = u.origin; } catch { toast('Server must be an https origin', true); return; }
      try {
        const granted = await chrome.permissions.contains({ origins: [server + '/*'] }) || await chrome.permissions.request({ origins: [server + '/*'] });
        if (!granted) { toast('Site access to that server was not granted', true); return; }
      } catch { /* permission API unavailable for this origin pattern; fetch will report */ }
      await saveSettings({ server, actor: $('setActor').value.trim(), autoFill: $('setAutoFill').checked });
      $('settings').hidden = true; state.connected = null; renderHeader();
      await connect(); await loadItems();
    };
    $('openServer').onclick = () => chrome.tabs.create({ url: serverBase() + '/listingagent/review' });
    $('openUpdates').onclick = () => chrome.tabs.create({ url: serverBase() + '/lister/' });
    $('signinBtn').onclick = () => chrome.tabs.create({ url: serverBase() + '/lister/' });
    $('connStatus').onclick = () => { state.connected = null; renderHeader(); void connect().then(loadItems); };
    $('scopeSelected').onclick = () => { state.scope = 'selected'; void loadItems(); };
    $('scopeOpen').onclick = () => { state.scope = 'open'; void loadItems(); };
    $('filter').oninput = () => { state.filter = $('filter').value; renderItems(); };
    $('reload').onclick = () => { void connect().then(loadItems); void refreshTab(); };
    chrome.tabs.onActivated.addListener(scheduleRefresh);
    chrome.tabs.onUpdated.addListener((tabId, info, tab) => { if (tab?.active && (info.status === 'complete' || info.url)) scheduleRefresh(); });
    chrome.windows?.onFocusChanged?.addListener(() => scheduleRefresh());
  }

  async function main() {
    await loadStorage();
    wireStatic();
    renderHeader(); renderPage(); renderItems();
    await connect();
    await Promise.all([loadItems(), refreshTab()]);
  }

  void main();
})();
