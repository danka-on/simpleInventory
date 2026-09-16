// Sweet Shelves Lister side panel. Talks to the Sweet Shelves server with the browser's
// Cloudflare sign-in and to the store page through the injected page script.
//
// Flow: the Listing Agent queue (from /items-to-list) is shown per store (eBay / Amazon). The
// oldest item is picked automatically. "Start on eBay/Amazon" opens the store's search page and
// types the UPC; the listing form is filled from the prepared proposal plus prep notes; fields
// that still need a value are highlighted; when the store confirms the listing it is recorded
// (with Undo) and the panel moves to the next item.
(() => {
  'use strict';
  const M = globalThis.SSListerMatcher;
  const SETTINGS_KEY = 'ssListerSettings';
  const LEARNED_KEY = 'ssListerLearned';
  const CURRENT_KEY = 'ssListerCurrent2';
  const PLATFORM_KEY = 'ssListerPlatform';
  const PROMPT_KEY = 'ssListerAiPrompt';
  const DEFAULTS = { server: 'https://pi.nexuscentralhq.org', actor: '', autoFill: true, autoGuide: true, autoLink: true, autoPrepare: true };
  const CONDITIONS = ['NEW', 'NEW_OTHER', 'NEW_WITH_DEFECTS', 'USED_EXCELLENT', 'USED_VERY_GOOD', 'USED_GOOD', 'USED_ACCEPTABLE', 'FOR_PARTS_OR_NOT_WORKING'];
  const VALUE_LABELS = {
    title: 'Title', price: 'Price', quantity: 'Quantity', sku: 'SKU / custom label', upc: 'UPC', asin: 'ASIN',
    brand: 'Brand', conditionDescription: 'Condition description', description: 'Description', condition: 'Condition',
  };
  const START_URLS = {
    ebay: () => 'https://www.ebay.com/sl/prelist/suggest?sr=wn',
    amazon: upc => 'https://sellercentral.amazon.com/product-search/search?q=' + encodeURIComponent(upc),
  };

  const state = {
    settings: { ...DEFAULTS }, connected: null, user: '', serverVersion: '', signIn: false,
    platform: 'ebay', view: 'list', filter: '', items: [], counts: {}, currentUpc: null, details: {}, edits: {},
    tab: null, page: null, report: null, pick: null, learned: {}, busy: '', lastLink: null,
    autoFilled: new Set(), searched: new Set(), autoLinked: new Set(), fillSessions: {}, pendingSearch: null,
    guide: null, selectedPhotos: new Set(), photoFiles: {}, aiPrompt: '', aiBusy: '', prepareTimers: {}, prepareAsked: new Set(),
    voiceBusy: new Set(), qrOpen: true, toastAction: null,
  };

  const $ = id => document.getElementById(id);
  const esc = value => String(value ?? '').replace(/[&<>"']/g, ch => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[ch]));
  const money = value => (value == null || value === '' ? '' : Number(value).toFixed(2));
  const storeName = p => (p === 'amazon' ? 'Amazon' : 'eBay');

  class SignInError extends Error { constructor() { super('Sign in to Sweet Shelves'); this.signIn = true; } }

  function toast(message, bad = false, action = null) {
    const el = $('toast');
    el.textContent = message || '';
    el.className = bad ? 'bad' : '';
    const button = $('toastAction');
    state.toastAction = action;
    button.hidden = !action;
    if (action) { button.textContent = action.label; button.onclick = () => { button.hidden = true; state.toastAction = null; void action.run(); }; }
    if (message) setTimeout(() => { if (el.textContent === message) { el.textContent = ''; if (state.toastAction === action) { button.hidden = true; state.toastAction = null; } } }, action ? 15000 : 6000);
  }

  // -- storage ------------------------------------------------------------------------

  async function loadStorage() {
    const stored = await chrome.storage.local.get([SETTINGS_KEY, LEARNED_KEY, CURRENT_KEY, PLATFORM_KEY, PROMPT_KEY]);
    state.settings = { ...DEFAULTS, ...(stored[SETTINGS_KEY] || {}) };
    state.learned = stored[LEARNED_KEY] || {};
    state.currentUpc = stored[CURRENT_KEY] || null;
    state.platform = stored[PLATFORM_KEY] === 'amazon' ? 'amazon' : 'ebay';
    state.aiPrompt = stored[PROMPT_KEY] || '';
  }

  async function saveSettings(settings) {
    state.settings = { ...DEFAULTS, ...settings };
    await chrome.storage.local.set({ [SETTINGS_KEY]: state.settings });
  }

  async function saveLearned() {
    await chrome.storage.local.set({ [LEARNED_KEY]: state.learned });
  }

  function remember() {
    void chrome.storage.local.set({ [CURRENT_KEY]: state.currentUpc, [PLATFORM_KEY]: state.platform, [PROMPT_KEY]: state.aiPrompt });
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

  // -- queue ----------------------------------------------------------------------------

  async function loadQueue({ keep = true } = {}) {
    if (state.connected === false) return;
    try {
      const data = await api('/api/lister/queue?platform=' + state.platform);
      state.items = data.items || [];
      state.counts[state.platform] = data.counts || {};
      state.connected = true; state.signIn = false;
      const active = state.items.filter(it => it.status === 'queued');
      const exists = state.items.some(it => it.upc === state.currentUpc);
      if (!keep || !exists || !state.currentUpc) state.currentUpc = active.length ? active[0].upc : (state.items[0]?.upc || null);
      remember();
      // The other store's count, cheaply, so the toggle shows both.
      void api('/api/lister/queue?platform=' + (state.platform === 'ebay' ? 'amazon' : 'ebay')).then(d => {
        state.counts[state.platform === 'ebay' ? 'amazon' : 'ebay'] = d.counts || {};
        renderStoreBar();
      }).catch(() => {});
    } catch (error) {
      if (error.signIn) { state.connected = false; state.signIn = true; }
      else toast(error.message, true);
    }
    renderAll();
    if (state.currentUpc) void loadDetail(state.currentUpc);
  }

  function current() {
    return state.items.find(it => it.upc === state.currentUpc) || null;
  }

  function detail() {
    return state.currentUpc ? state.details[state.currentUpc] || null : null;
  }

  async function loadDetail(upc, { force = false } = {}) {
    if (!upc || (!force && state.details[upc] && Date.now() - state.details[upc].loadedAt < 30000)) { renderDetail(); renderConfirm(); return state.details[upc]; }
    try {
      const data = await api('/api/lister/queue/' + encodeURIComponent(upc));
      const item = data.item;
      item.loadedAt = Date.now();
      state.details[upc] = item;
      if (upc === state.currentUpc) { renderDetail(); renderConfirm(); }
      void maybePrepare(item);
      void maybeTranscribe(item);
      return item;
    } catch (error) {
      toast(error.message, true);
      return null;
    }
  }

  // Build the proposal (title, price, category, specifics, description) in the background, then refresh.
  async function maybePrepare(item, { force = false } = {}) {
    if (!item || (!state.settings.autoPrepare && !force)) return;
    if (item.proposal?.ready && !force) return;
    const key = item.upc;
    if (!force && state.prepareAsked.has(key)) return;
    state.prepareAsked.add(key);
    try {
      const result = await api('/api/lister/queue/' + encodeURIComponent(key) + '/prepare', { method: 'POST', body: { force } });
      if (result.status === 'ready') { await loadDetail(key, { force: true }); return; }
      state.details[key].preparing = { running: true };
      if (key === state.currentUpc) renderDetail();
      let tries = 0;
      clearTimeout(state.prepareTimers[key]);
      const poll = async () => {
        tries += 1;
        const fresh = await loadDetail(key, { force: true });
        if (!fresh) return;
        if (fresh.proposal?.ready && !fresh.preparing?.running) { if (key === state.currentUpc) toast('Listing values prepared'); return; }
        if (fresh.preparing?.error) { if (key === state.currentUpc) toast('Prepare failed: ' + fresh.preparing.error, true); return; }
        if (tries < 40) state.prepareTimers[key] = setTimeout(poll, 4000);
      };
      state.prepareTimers[key] = setTimeout(poll, 4000);
    } catch (error) {
      if (error.status !== 501) toast('Prepare: ' + error.message, true);
    }
  }

  // Voice notes without text yet: transcribe + translate them (two at a time).
  async function maybeTranscribe(item) {
    if (!item || !state.settings.autoPrepare) return;
    for (const note of (item.voiceNotes || []).filter(v => v.status === 'pending' || v.status === 'error').slice(0, 2)) {
      void transcribe(item.upc, note.id, false);
    }
  }

  async function transcribe(upc, mediaId, reanalyze) {
    const key = upc + ':' + mediaId;
    if (state.voiceBusy.has(key)) return;
    state.voiceBusy.add(key);
    if (upc === state.currentUpc) renderDetail();
    try {
      const data = await api('/api/lister/voice/' + mediaId + '/analyze', { method: 'POST', body: { reanalyze } });
      const item = state.details[upc];
      if (item) {
        const note = (item.voiceNotes || []).find(v => v.id === mediaId);
        if (note) Object.assign(note, { english: data.analysis.english || '', lithuanian: data.analysis.lithuanian || '', status: data.analysis.status || 'complete', error: data.analysis.error || '' });
        applyVoiceToCondition(item);
      }
    } catch (error) {
      const item = state.details[upc];
      const note = item && (item.voiceNotes || []).find(v => v.id === mediaId);
      if (note) { note.status = 'error'; note.error = error.message; }
      if (error.status !== 409 && error.status !== 429) toast('Voice note: ' + error.message, true);
    } finally {
      state.voiceBusy.delete(key);
      if (upc === state.currentUpc) renderDetail();
    }
  }

  // English voice text feeds the condition note (and the description) when nothing was written.
  function applyVoiceToCondition(item) {
    const english = (item.voiceNotes || []).map(v => v.english).filter(Boolean);
    if (!english.length) return;
    const edits = state.edits[item.upc] || {};
    const currentNote = edits.conditionDescription ?? item.fields.conditionDescription ?? '';
    const missing = english.filter(text => !currentNote.includes(text));
    if (!missing.length) return;
    const merged = [currentNote, ...missing].filter(Boolean).join('\n');
    item.fields.conditionDescription = merged;
    item.fields.conditionDescriptionSource = 'notes';
    if (!(item.fields.descriptionText || '').includes(missing[0])) item.fields.descriptionText = [item.fields.descriptionText, 'Condition: ' + missing.join(' ')].filter(Boolean).join('\n\n');
    delete edits.conditionDescription;
  }

  // Values as edited in the panel (edits win over the prepared fields).
  function values(item) {
    if (!item) return null;
    const edits = state.edits[item.upc] || {};
    const fields = { ...item.fields, ...edits };
    if (state.platform === 'amazon' && item.existing?.amazon?.[0]?.asin) fields.asin = fields.asin || item.existing.amazon[0].asin;
    return fields;
  }

  // -- page (active tab) -------------------------------------------------------------------

  async function ensurePageScript(tabId) {
    try {
      const pong = await chrome.tabs.sendMessage(tabId, { target: 'ss-lister-page', type: 'ping' });
      if (pong?.ok && pong.version >= 3) return true;
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
    refreshTimer = setTimeout(() => { void refreshTab(); }, 300);
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
    if (state.page.store && state.page.store !== state.platform) {
      // Follow the store the user is looking at.
      state.platform = state.page.store; remember();
      await loadQueue();
    }
    // On the listing form the current item locks in and its details take over the panel;
    // leaving that page (success, another site) brings the queue back.
    const onForm = state.page.kind === 'listing-form' || state.page.kind === 'offer-form';
    if (onForm && current() && !state.locked) { state.locked = current().upc; state.view = 'item'; }
    else if (!onForm && state.locked) {
      // Out of the listing window (back to search, the success page, another site): show the queue again.
      state.locked = null;
      if (state.page.kind !== 'listing-success' && state.page.kind !== 'offer-success') state.view = 'list';
    }
    renderStoreBar(); renderPage(); renderDetail(); renderConfirm();
    await maybeAutoSearch();
    await maybeAssist();
    await maybeAutoFill();
    await maybeAutoLink();
  }

  // eBay's steps before the form (category, catalog match, condition): highlight our guess, learn the click.
  const ASSIST_KINDS = new Set(['listing-start', 'listing-category', 'listing-match', 'listing-confirm']);
  async function maybeAssist() {
    const item = current();
    const page = state.page;
    if (!item || page?.store !== 'ebay' || !ASSIST_KINDS.has(page.kind)) {
      if (state.assist && state.tab?.id) { try { await pageMessage({ type: 'assist-stop' }); } catch { /* page gone */ } }
      state.assist = null;
      return;
    }
    const info = detail() || await loadDetail(item.upc);
    if (!info) return;
    const v = values(info);
    try {
      const learned = (info.learned || {}).ebay || {};
      const result = await pageMessage({ type: 'assist-start', options: {
        title: v.title || item.title, brand: v.brand || '', condition: v.condition || '',
        categoryPath: v.categoryPath || learned.category?.chosen || '', matchTitle: learned.match?.chosen || '',
      } });
      state.assist = result.state; renderPage();
    } catch (error) {
      state.assist = null;
    }
  }

  // Title / description written by Claude from the item's values, condition and prep notes.
  async function generate(info, kind) {
    const v = values(info);
    state.genBusy = kind; renderPage();
    try {
      const notes = [...(info.notes || []).map(n => n.english || n.text), ...(info.voiceNotes || []).map(n => n.english), info.defect].filter(Boolean);
      const data = await api('/api/lister/queue/' + encodeURIComponent(info.upc) + '/generate', { method: 'POST', body: { kind, values: {
        title: v.title, systemTitle: info.title, brand: v.brand, categoryPath: v.categoryPath, condition: v.condition,
        conditionDescription: v.conditionDescription, notes, aspects: info.fields.aspects || {},
      } } });
      const edits = state.edits[info.upc] = { ...(state.edits[info.upc] || {}) };
      if (kind === 'title') edits.title = data.title;
      else { edits.descriptionText = data.descriptionText; edits.descriptionHtml = data.descriptionHtml; }
      const pushed = await pushValue(info, kind);
      toast((kind === 'title' ? 'Title written: ' + data.title : 'Description written from the item and its notes') + (pushed ? ' (on the page)' : ''));
    } catch (error) {
      toast('AI ' + kind + ': ' + error.message, true);
    } finally {
      state.genBusy = ''; renderPage(); renderDetail();
    }
  }

  async function recordChoice(message) {
    const item = current();
    if (!item) return;
    try {
      const data = await api('/api/lister/learn', { method: 'POST', body: { upc: item.upc, platform: 'ebay', step: message.step, chosen: message.chosen, suggested: message.suggested || '', url: message.url || '' } });
      const info = detail();
      if (info) info.learned = data.learned || info.learned;
      toast((data.agreed ? 'Same as suggested: ' : 'Learned ' + message.step + ': ') + String(message.chosen).slice(0, 60));
    } catch (error) {
      toast('Could not save that choice: ' + error.message, true);
    }
  }

  function tabKey(suffix) {
    return (state.tab?.id || 0) + '|' + (state.page?.url || '').split('#')[0] + '|' + suffix;
  }

  async function maybeAutoSearch({ force = false } = {}) {
    const item = current();
    const page = state.page;
    if (!item || !page?.store || page.kind !== 'listing-start') return;
    // Only on purpose: the user clicked an item or pressed Start. Opening the panel on the search page does nothing by itself.
    const wanted = force || state.pendingSearch?.upc === item.upc;
    if (!wanted) return;
    const key = tabKey('search|' + item.upc);
    if (!force && state.searched.has(key)) return;
    const result = await searchUpc({ auto: true });
    if (result?.ok) state.searched.add(key);  // otherwise the next page update tries again
  }

  async function searchUpc({ auto = false } = {}) {
    const item = current();
    if (!item || !state.page?.store) { if (!auto) toast('Open the store\'s "what are you selling" page first', true); return; }
    try {
      const result = await pageMessage({ type: 'search', options: { query: item.baseUpc || item.upc, store: state.page.store } });
      state.pendingSearch = null;
      toast(`Searched ${item.baseUpc || item.upc} on ${storeName(state.page.store)}`);
      return result;
    } catch (error) {
      if (!auto) toast('Search: ' + error.message, true);
    }
  }

  async function maybeAutoFill() {
    const item = current();
    const page = state.page;
    if (!state.settings.autoFill || !item || !page?.store) return;
    if (!(page.kind === 'listing-form' || page.kind === 'offer-form')) return;
    const key = tabKey('fill|' + item.upc);
    if (state.autoFilled.has(key)) return;
    state.autoFilled.add(key);
    await fillPage({ auto: true });
  }

  async function fillPage({ auto = false } = {}) {
    const item = current();
    const d = detail();
    if (!item || !state.page?.store) { if (!auto) toast('Open an eBay listing form or a Seller Central offer page first', true); return; }
    if (!d) { await loadDetail(item.upc); }
    const info = detail();
    if (!info) return;
    state.busy = 'fill'; renderDetail();
    try {
      const store = state.page.store;
      const learned = state.learned[store + ':' + (state.page.kind || '')] || {};
      const result = await pageMessage({ type: 'fill', options: { values: values(info), store, aspects: info.fields.aspects || {}, learned, includeDescription: store === 'ebay' } });
      state.report = result.report;
      state.fillSessions[state.tab.id] = item.upc;
      const filled = (result.report.filled || []).map(f => f.target);
      toast(filled.length ? `Filled ${filled.length} field${filled.length === 1 ? '' : 's'} on ${storeName(store)}` : 'No matching fields found on this page. Use "Pick a field".', !filled.length);
      if (info.proposal?.id) void api('/api/lister/events', { method: 'POST', body: { proposal_id: info.proposal.id, event: filled.length ? 'helper_filled' : 'helper_fill_failed', note: `${store} ${state.page.kind || ''}${auto ? ' (auto)' : ''}`, payload: { filled, unmatched: result.report.unmatched, aspects: result.report.aspects } } }).catch(() => {});
      if (state.settings.autoGuide) await guide('start', { silent: true });
    } catch (error) {
      toast('Fill failed: ' + error.message, true);
    } finally {
      state.busy = ''; renderDetail();
    }
  }

  // Guided fill: the page script outlines the fields that still need a value.
  async function guide(action, { silent = false, index = 0 } = {}) {
    const info = detail();
    if (!state.page?.store) { if (!silent) toast('Guide works on a store listing form', true); return; }
    try {
      let response;
      if (action === 'start') {
        if (!info) return;
        const v = values(info);
        const noteFields = v.conditionDescriptionSource === 'notes' && v.conditionDescription ? ['conditionDescription'] : [];
        response = await pageMessage({ type: 'guide-start', options: { values: v, store: state.page.store, aspects: info.fields.aspects || {}, noteFields } });
        if (!silent && !response.state.needed.length) toast('Nothing left to fill on this page');
      } else if (action === 'go') response = await pageMessage({ type: 'guide-go', index });
      else response = await pageMessage({ type: 'guide-' + action });
      state.guide = response.state;
      renderDetail();
    } catch (error) {
      if (!silent) toast('Guide: ' + error.message, true);
    }
  }

  chrome.runtime.onMessage.addListener(message => {
    if (message?.type === 'ss-lister-picked') {
      state.pick = { waiting: false, pickId: message.pickId, field: message.field, suggestions: message.suggestions || [] };
      renderPick();
    } else if (message?.type === 'ss-lister-pick-cancelled') {
      state.pick = null; renderPick();
    } else if (message?.type === 'ss-lister-guide') {
      state.guide = message.state; renderGuideOnly();
    } else if (message?.type === 'ss-lister-assist') {
      state.assist = message.state; renderPage();
    } else if (message?.type === 'ss-lister-choice') {
      void recordChoice(message);
    } else if (message?.type === 'ss-lister-dropped') {
      toast(message.ok ? `Dropped ${message.name} into the page's uploader` : 'Drop failed: ' + message.reason, !message.ok);
    }
  });

  // The page script asks for a dragged photo's bytes when they were not cached before the drag.
  chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
    if (message?.type !== 'ss-lister-photo-bytes') return false;
    photoFile(message.url)
      .then(f => sendResponse({ ok: true, name: f.name, type: f.type, base64: f.base64 }))
      .catch(error => sendResponse({ ok: false, reason: error.message || 'could not load the photo' }));
    return true;
  });

  // -- confirm & link (automatic on the success page, manual through the card) --------------------

  function linkBodyFromPage(item, platform, page, v) {
    return {
      upc: item.upc, proposal_id: detail()?.proposal?.id || undefined, platform,
      listing_id: platform === 'ebay' ? (page.listingId || '') : '', sku: page.sku || v.sku || '', asin: platform === 'amazon' ? (page.asin || v.asin || '') : '',
      store_upc: page.upc || '', url: page.url || '', price: v.price ?? null, quantity: v.quantity ?? null, title: v.title,
    };
  }

  async function maybeAutoLink() {
    const item = current();
    const page = state.page;
    const info = detail();
    if (!state.settings.autoLink || !item || !info || !page?.store) return;
    const platform = page.store;
    if (!(page.kind === 'listing-success' || page.kind === 'offer-success')) return;
    if (state.fillSessions[state.tab?.id] !== item.upc) return;  // only the item we filled in this tab
    if ((info.links || []).some(l => l.platform === platform)) return;
    const v = values(info);
    if (platform === 'ebay' && !page.listingId) return;
    if (platform === 'amazon' && !(page.sku || page.asin)) return;
    if (platform === 'amazon' && page.sku && v.sku && page.sku !== v.sku) return;  // a different SKU is on screen
    const key = tabKey('link|' + item.upc + '|' + (page.listingId || page.sku || page.asin));
    if (state.autoLinked.has(key)) return;
    state.autoLinked.add(key);
    await recordLink({ ...linkBodyFromPage(item, platform, page, v), note: 'auto-detected by the extension' }, { auto: true });
  }

  async function recordLink(body, { auto = false } = {}) {
    const upc = body.upc;
    state.busy = 'confirm'; renderConfirm();
    try {
      const data = await api('/api/lister/links', { method: 'POST', body });
      state.lastLink = data.link;
      const label = body.platform === 'ebay' ? ('item ' + body.listing_id) : ('SKU ' + (body.sku || body.asin));
      toast((data.duplicate ? 'Already linked: ' : (auto ? 'Listed and recorded: ' : 'Linked and recorded: ')) + label, false,
        data.duplicate ? null : { label: 'Undo', run: () => undoLink(data.link.id) });
      delete state.details[upc];
      await loadQueue({ keep: false });
      if (auto) state.view = 'list';
      renderAll();
    } catch (error) {
      toast(error.message, true);
    } finally {
      state.busy = ''; renderConfirm();
    }
  }

  async function confirmLink() {
    const item = current();
    const info = detail();
    if (!item || !info) return;
    const platform = document.querySelector('input[name="cfPlatform"]:checked')?.value || state.page?.store || state.platform;
    const body = {
      upc: item.upc, proposal_id: info.proposal?.id || undefined, platform,
      listing_id: $('cfListingId')?.value.trim() || '', sku: $('cfSku')?.value.trim() || '', asin: $('cfAsin')?.value.trim() || '',
      store_upc: $('cfStoreUpc')?.value.trim() || '', url: $('cfUrl')?.value.trim() || '',
      price: $('cfPrice')?.value.trim() || null, quantity: $('cfQuantity')?.value.trim() || null,
      note: $('cfNote')?.value.trim() || '', title: values(info).title,
    };
    await recordLink(body);
  }

  async function undoLink(linkId) {
    try {
      await api('/api/lister/links/' + linkId, { method: 'DELETE', body: {} });
      state.lastLink = null;
      toast('Link removed');
      state.details = {};
      await loadQueue();
    } catch (error) {
      toast(error.message, true);
    }
  }

  async function markExisting(item) {
    const entry = (item.existing || [])[0];
    if (!entry) return;
    try {
      const data = await api('/api/lister/queue/' + encodeURIComponent(item.upc) + '/mark-existing', { method: 'POST', body: { platform: state.platform, entry } });
      toast(data.duplicate ? 'That listing was already linked' : `Linked the existing ${storeName(state.platform)} listing`, false,
        data.duplicate ? null : { label: 'Undo', run: () => undoLink(data.link.id) });
      delete state.details[item.upc];
      await loadQueue({ keep: false });
    } catch (error) {
      toast(error.message, true);
    }
  }

  // -- skip (the X) -------------------------------------------------------------------------------

  function confirmModal({ title, text, okLabel = 'Remove', danger = true }) {
    return new Promise(resolve => {
      const modal = $('modal');
      modal.hidden = false;
      modal.innerHTML = `<div class="box" role="dialog" aria-modal="true"><strong>${esc(title)}</strong><p>${esc(text)}</p>
        <div class="row"><button id="modalOk" class="${danger ? 'danger' : 'primary'}" type="button">${esc(okLabel)}</button><button id="modalCancel" type="button">Cancel</button></div></div>`;
      const close = value => { modal.hidden = true; modal.innerHTML = ''; resolve(value); };
      $('modalOk').onclick = () => close(true);
      $('modalCancel').onclick = () => close(false);
      modal.onclick = event => { if (event.target === modal) close(false); };
      $('modalOk').focus();
    });
  }

  async function skipItem(item) {
    const other = state.platform === 'ebay' ? 'amazon' : 'ebay';
    const otherResolved = item.otherStatus !== 'queued';
    const text = otherResolved
      ? `It is already ${item.otherStatus} on ${storeName(other)}, so this removes it from the Listing Agent queue on Items to List.`
      : `It stays on the ${storeName(other)} list and in the Listing Agent queue until that one is done too.`;
    const ok = await confirmModal({ title: `Remove "${item.title || item.upc}" from the ${storeName(state.platform)} list?`, text });
    if (!ok) return;
    try {
      const platform = state.platform;
      const data = await api('/api/lister/queue/' + encodeURIComponent(item.upc) + '/skip', { method: 'POST', body: { platform } });
      toast(`Removed from ${storeName(platform)}` + (data.queue === 'removed' ? ' and the queue' : ''), false,
        { label: 'Undo', run: async () => { await api('/api/lister/queue/' + encodeURIComponent(item.upc) + '/skip', { method: 'POST', body: { platform, undo: true } }); await loadQueue(); } });
      delete state.details[item.upc];
      await loadQueue({ keep: state.currentUpc !== item.upc });
    } catch (error) {
      toast(error.message, true);
    }
  }

  // -- start listing on a store --------------------------------------------------------------------

  async function startOn(platform) {
    const item = current();
    if (!item) return;
    state.pendingSearch = { upc: item.upc, platform };
    const url = START_URLS[platform](item.baseUpc || item.upc);
    const onStore = state.page?.store === platform && state.tab?.id;
    if (onStore) await chrome.tabs.update(state.tab.id, { url });
    else await chrome.tabs.create({ url, active: true });
  }

  // -- photos ------------------------------------------------------------------------------------

  async function photoFile(url) {
    if (state.photoFiles[url]) return state.photoFiles[url];
    const data = await api('/api/lister/photos/fetch?url=' + encodeURIComponent(url));
    const binary = atob(data.base64);
    const bytes = new Uint8Array(binary.length);
    for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
    const file = new File([bytes], data.name || 'photo.jpg', { type: data.mime || 'image/jpeg' });
    state.photoFiles[url] = { file, base64: data.base64, name: file.name, type: file.type };
    return state.photoFiles[url];
  }

  function selectedPhotoUrls(info) {
    const all = (info.photos || []).map(p => p.url);
    const chosen = all.filter(u => state.selectedPhotos.has(u));
    return chosen.length ? chosen : all.filter(u => (info.photos.find(p => p.url === u) || {}).source !== 'catalog').slice(0, 12);
  }

  async function sendPhotos() {
    const info = detail();
    if (!info || !state.page?.store) { toast('Open the store listing form first', true); return; }
    const urls = selectedPhotoUrls(info);
    if (!urls.length) { toast('No photos to send', true); return; }
    state.busy = 'photos'; renderDetail();
    try {
      const files = [];
      for (const url of urls) { const f = await photoFile(url); files.push({ name: f.name, type: f.type, base64: f.base64 }); }
      const result = await pageMessage({ type: 'add-photos', options: { files } });
      toast(result.ok ? `Sent ${result.count} photo${result.count === 1 ? '' : 's'} to the page (${result.method})` : result.reason, !result.ok);
    } catch (error) {
      toast('Photos: ' + error.message, true);
    } finally {
      state.busy = ''; renderDetail();
    }
  }

  async function aiPhotoshop() {
    const info = detail();
    if (!info) return;
    const urls = (info.photos || []).map(p => p.url).filter(u => state.selectedPhotos.has(u));
    if (!urls.length) { toast('Tick the photos to clean up first', true); return; }
    const prompt = ($('aiPrompt')?.value || '').trim();
    state.aiPrompt = prompt && prompt !== info.aiPhotoPrompt ? prompt : '';
    remember();
    let done = 0;
    for (const url of urls) {
      state.aiBusy = `AI photoshop ${done + 1}/${urls.length}…`; renderDetail();
      try {
        const data = await api('/api/lister/photos/ai', { method: 'POST', body: { upc: info.upc, url, prompt } });
        info.photos.unshift(data.photo);
        state.selectedPhotos.delete(url);
        state.selectedPhotos.add(data.photo.url);
        done += 1;
      } catch (error) {
        toast('AI photoshop: ' + error.message, true);
        break;
      }
    }
    state.aiBusy = '';
    if (done) toast(`${done} photo${done === 1 ? '' : 's'} cleaned up; the originals are kept`);
    renderDetail();
  }

  // -- rendering ---------------------------------------------------------------------------

  function renderAll() {
    renderHeader(); renderStoreBar(); renderPage(); renderItems(); renderDetail(); renderConfirm();
  }

  function renderHeader() {
    const chip = $('connStatus');
    if (state.connected === null) { chip.textContent = 'connecting…'; chip.className = 'chip'; }
    else if (state.connected) { chip.textContent = state.user ? state.user.split('@')[0] : 'connected'; chip.className = 'chip ok'; chip.title = serverBase() + (state.user ? ' as ' + state.user : ''); }
    else { chip.textContent = state.signIn ? 'sign in' : 'offline'; chip.className = 'chip bad'; }
    $('signin').hidden = !state.signIn;
    $('versionLine').textContent = `Extension ${chrome.runtime.getManifest().version}` + (state.serverVersion ? ` · server ${state.serverVersion}` : '');
  }

  function renderStoreBar() {
    $('storeEbay').classList.toggle('active', state.platform === 'ebay');
    $('storeAmazon').classList.toggle('active', state.platform === 'amazon');
    for (const p of ['ebay', 'amazon']) {
      const c = state.counts[p] || {};
      $(p === 'ebay' ? 'countEbay' : 'countAmazon').textContent = c.queued != null ? `(${c.queued})` : '';
    }
    $('viewList').classList.toggle('active', state.view === 'list');
    $('viewItem').classList.toggle('active', state.view === 'item');
    $('listCard').hidden = state.view !== 'list' || Boolean(state.locked);
    const locked = state.locked ? state.items.find(it => it.upc === state.locked) : null;
    document.body.classList.toggle('locked', Boolean(state.locked));
    $('lock').classList.toggle('on', Boolean(state.locked));
    if (state.locked) $('lockTitle').textContent = `Listing: ${locked?.title || state.locked}`;
  }

  function renderPage() {
    const page = state.page || {};
    const el = $('pageCard');
    const item = current();
    const info = detail();
    if (!page.store) {
      el.innerHTML = `<div class="store"><span class="badge none">no store page</span><span class="grow muted small">Pick an item, then start the listing. The panel searches the UPC, fills the form and records the listing.</span></div>
        ${item ? `<div class="actions"><button id="startBtn" class="primary" type="button">Start on ${storeName(state.platform)}</button></div>` : ''}`;
      if ($('startBtn')) $('startBtn').onclick = () => startOn(state.platform);
      return;
    }
    const kindText = {
      'listing-start': 'search for the product', 'listing-category': 'choose a category', 'listing-match': 'find a catalog match', 'listing-confirm': 'confirm details',
      'listing-form': 'listing form', 'listing-success': 'listing confirmed', 'listing-live': 'live listing',
      'seller-hub': 'Seller Hub', 'offer-form': 'add product / offer form', 'offer-success': 'offer saved', 'product-search': 'product search', inventory: 'inventory',
    }[page.kind] || 'page';
    const assist = state.assist && state.assist.kind === page.kind ? state.assist : null;
    const assistLine = assist ? (assist.suggested
      ? `<div class="flag info"><b>Suggested:</b> ${esc(assist.suggested)}${page.kind === 'listing-confirm' ? ' (pre-selected)' : ' — your click teaches the panel'}</div>`
      : (assist.candidates ? '<div class="flag warn">No confident suggestion here; your choice will be remembered.</div>' : '')) : '';
    const chips = [];
    if (page.listingId && page.store === 'ebay') chips.push(`Item # <code>${esc(page.listingId)}</code>`);
    if (page.asin) chips.push(`ASIN <code>${esc(page.asin)}</code>`);
    if (page.sku) chips.push(`SKU <code>${esc(page.sku)}</code>`);
    const onForm = page.kind === 'listing-form' || page.kind === 'offer-form';
    const g = state.guide;
    const busy = Boolean(state.busy) || Boolean(state.genBusy);
    const actions = item ? [
      page.kind === 'listing-start' ? `<button id="searchBtn" class="primary" type="button" title="Type the UPC into the store's product search">Search UPC</button>` : '',
      !onForm && page.kind !== 'listing-start' && !ASSIST_KINDS.has(page.kind) ? `<button id="startBtn" type="button">Start on ${storeName(state.platform)}</button>` : '',
      onForm ? `<button id="fillBtn" class="primary" type="button" ${busy ? 'disabled' : ''}>${state.busy === 'fill' ? 'Filling…' : 'Fill page'}</button>` : '',
      onForm ? `<button id="guideBtn" type="button" title="Checklist overlay on the page: red = required and empty, green = filled">${g?.active ? 'Next field' : 'Checklist'}</button>` : '',
      onForm && g?.active ? '<button id="guideStop" class="mini" type="button">Hide</button>' : '',
      onForm && info ? `<button id="genTitle" class="mini" type="button" ${busy ? 'disabled' : ''} title="Write an 80-character title from the item and its notes, into the page">${state.genBusy === 'title' ? '…' : 'AI title'}</button>` : '',
      onForm && info ? `<button id="genDescription" class="mini" type="button" ${busy ? 'disabled' : ''} title="Write the description from the item, its condition and prep notes, into the page">${state.genBusy === 'description' ? '…' : 'AI description'}</button>` : '',
      `<button id="pickBtn" class="mini" type="button" title="Click a field on the store page and choose which value goes in">Pick a field…</button>`,
    ].filter(Boolean).join('') : '';
    el.innerHTML = `
      <div class="store"><span class="badge ${page.store}">${storeName(page.store)}</span>
        <span class="grow">${esc(kindText)}${g?.active && onForm ? ` <span class="muted small">· ${esc(g.open)} of ${esc(g.total)} left</span>` : ''}</span>
        <button id="pageRefresh" class="icon" type="button" title="Re-read page">↻</button></div>
      ${chips.length ? `<div class="chips">${chips.join('')}</div>` : ''}
      ${assistLine}
      ${actions ? `<div class="actions">${actions}</div>` : ''}
      ${page.error ? `<div class="flag warn">Page script: ${esc(page.error)}</div>` : ''}`;
    $('pageRefresh').onclick = () => refreshTab();
    if ($('startBtn')) $('startBtn').onclick = () => startOn(state.platform);
    if ($('searchBtn')) $('searchBtn').onclick = () => { state.pendingSearch = { upc: item.upc, platform: state.platform }; void searchUpc(); };
    if ($('fillBtn')) $('fillBtn').onclick = () => fillPage();
    if ($('guideBtn')) $('guideBtn').onclick = () => guide(g?.active ? 'next' : 'start');
    if ($('guideStop')) $('guideStop').onclick = () => guide('stop');
    if ($('genTitle')) $('genTitle').onclick = () => generate(info, 'title');
    if ($('genDescription')) $('genDescription').onclick = () => generate(info, 'description');
    if ($('pickBtn')) $('pickBtn').onclick = () => startPick();
  }

  // Put one value on the store page (after an AI text or a note was applied) without a full re-fill.
  async function pushValue(info, target) {
    if (!state.page?.store || !(state.page.kind === 'listing-form' || state.page.kind === 'offer-form')) return false;
    try {
      const result = await pageMessage({ type: 'fill', options: { values: values(info), store: state.page.store, targets: [target], aspects: {}, learned: state.learned[state.page.store + ':' + (state.page.kind || '')] || {} } });
      return (result.report.filled || []).some(f => f.target === target);
    } catch { return false; }
  }

  function renderItems() {
    const filter = state.filter.trim().toLowerCase();
    const rows = state.items.filter(it => !filter || (it.title || '').toLowerCase().includes(filter) || (it.upc || '').includes(filter));
    const list = $('itemList');
    if (!rows.length) {
      list.innerHTML = `<div class="empty">${state.connected === false ? 'Not connected.' : `Nothing queued for ${storeName(state.platform)}. Add items to the Listing Agent queue on <b>Items to List</b>.`}</div>`;
      return;
    }
    const active = rows.filter(it => it.status === 'queued');
    const done = rows.filter(it => it.status !== 'queued');
    const row = (it, first) => {
      const chips = [];
      if (it.alreadyOnStore) chips.push(`<span class="chip warn" title="The store already carries this UPC">on ${storeName(state.platform)} ${it.storeUrl ? `<a href="${esc(it.storeUrl)}" target="_blank" rel="noopener" title="Open the store listing">↗</a>` : ''}</span>`);
      if (it.status === 'listed') chips.push(`<span class="chip ok">listed ${it.storeUrl ? `<a href="${esc(it.storeUrl)}" target="_blank" rel="noopener">↗</a>` : ''}</span>`);
      if (it.otherStatus === 'listed') chips.push(`<span class="chip info" title="Already listed on the other store">${storeName(state.platform === 'ebay' ? 'amazon' : 'ebay')} ✓</span>`);
      if (it.suffixed) chips.push(`<span class="chip warn" title="Specific unit: the SKU keeps the -suffix">unit ${esc(it.upc.split('-')[1])}</span>`);
      if (it.preparing) chips.push('<span class="chip"><span class="spin"></span> preparing</span>');
      else if (it.proposal?.ready) chips.push('<span class="chip ok" title="Title, price, specifics and description are prepared">ready</span>');
      return `<div class="item ${it.upc === state.currentUpc ? 'current' : ''} ${first ? 'first' : ''} ${it.status === 'listed' ? 'listed' : ''}" data-upc="${esc(it.upc)}" title="${esc(it.title)}">
        ${it.thumb ? `<img src="${esc(it.thumb)}" alt="" loading="lazy">` : '<div class="noimg"></div>'}
        <div><div class="title">${esc(it.title || '(no title)')}</div>
          <div class="meta"><span>${esc(it.upc)}</span>${chips.join('')}</div></div>
        ${it.status === 'queued' ? `<button class="remove" data-skip="${esc(it.upc)}" type="button" title="Remove from the ${storeName(state.platform)} list" aria-label="Remove">×</button>` : '<span></span>'}
      </div>`;
    };
    list.innerHTML = active.map((it, i) => row(it, i === 0)).join('') +
      (done.length ? `<div class="sep">Listed on ${storeName(state.platform)} (${done.length})</div>` + done.map(it => row(it, false)).join('') : '');
    for (const el of list.querySelectorAll('.item')) {
      el.onclick = event => {
        if (event.target.closest('a, button')) return;
        state.currentUpc = el.dataset.upc; state.report = null; state.guide = null; state.selectedPhotos = new Set(); remember();
        renderItems(); renderDetail(); renderConfirm();
        void loadDetail(state.currentUpc).then(() => maybeAssist());
        // Picked while the store's search page is open: search this UPC right away.
        state.pendingSearch = { upc: state.currentUpc, platform: state.platform };
        void maybeAutoSearch({ force: true });
      };
    }
    for (const button of list.querySelectorAll('button[data-skip]')) {
      button.onclick = event => { event.stopPropagation(); const it = state.items.find(x => x.upc === button.dataset.skip); if (it) void skipItem(it); };
    }
  }

  function renderDetail() {
    const item = current();
    const info = detail();
    const el = $('detail');
    el.hidden = !item || state.view !== 'item';
    if (!item || state.view !== 'item') return;
    if (!info) {
      el.innerHTML = `<div class="head">${item.thumb ? `<img src="${esc(item.thumb)}" alt="">` : '<div class="noimg"></div>'}<div><div class="name">${esc(item.title || item.upc)}</div><div class="muted small">${esc(item.upc)}</div></div></div><p class="muted"><span class="spin"></span> Loading item…</p>`;
      return;
    }
    const v = values(info);
    const store = state.page?.store || '';
    const preparing = info.preparing?.running;
    const notes = info.notes || [];
    const voice = info.voiceNotes || [];
    const photos = info.photos || [];
    const listingPhotos = photos.filter(p => p.source === 'listing' || p.source === 'ai');
    const prepPhotos = photos.filter(p => p.source === 'prep');
    const catalogPhotos = photos.filter(p => p.source === 'catalog');
    const existing = (info.existing || {})[state.platform] || [];
    const linkedHere = (info.links || []).find(l => l.platform === state.platform);
    const aspects = Object.entries(info.fields.aspects || {});
    const prep = info.prepStatus || {};
    const stock = info.inventory || {};
    const storeChip = platform => {
      const linked = (info.links || []).some(l => l.platform === platform) || info.queue?.listed?.[platform];
      const onStore = ((info.existing || {})[platform] || []).length > 0;
      if (linked) return `<span class="chip ok">${storeName(platform)}: listed ✓</span>`;
      if (onStore) return `<span class="chip warn" title="The store already carries this UPC">${storeName(platform)}: on store</span>`;
      if (info.queue?.skipped?.includes(platform)) return `<span class="chip">${storeName(platform)}: skipped</span>`;
      return `<span class="chip info">${storeName(platform)}: not listed</span>`;
    };
    const tile = p => `<div class="photo ${p.source} ${state.selectedPhotos.has(p.url) ? 'selected' : ''}" data-url="${esc(p.url)}" draggable="true" title="${esc(p.name)} · drag onto the store page">
        <img src="${esc(p.url)}" alt="" loading="lazy" draggable="false"><input type="checkbox" data-select="${esc(p.url)}" ${state.selectedPhotos.has(p.url) ? 'checked' : ''}><span class="src">${esc(p.source === 'prep' ? 'condition' : p.source)}</span></div>`;
    const copyAll = [
      `Title: ${v.title}`, `Price: ${money(v.price)} ${v.currency || ''}`.trim(), `Quantity: ${v.quantity ?? ''}`, `SKU: ${v.sku}`, `UPC: ${v.upc}`,
      `Condition: ${v.condition}${v.conditionDescription ? ' - ' + v.conditionDescription : ''}`, v.brand ? `Brand: ${v.brand}` : '',
      v.categoryPath ? `eBay category: ${v.categoryPath} (${v.categoryId})` : '',
      aspects.length ? 'Item specifics: ' + aspects.map(([k, vals]) => `${k}=${vals.join('/')}`).join('; ') : '',
      '', v.descriptionText || '',
    ].filter((line, i, arr) => line !== '' || (arr[i + 1] || '') !== '').join('\n');
    el.innerHTML = `
      <div class="head">${(listingPhotos[0] || prepPhotos[0] || photos[0] || {}).url || item.thumb ? `<img src="${esc((listingPhotos[0] || prepPhotos[0] || photos[0] || {}).url || item.thumb)}" alt="">` : '<div class="noimg"></div>'}
        <div><div class="name">${esc(v.title || item.title || item.upc)}</div>
          <div class="muted small">${esc(info.upc)}${info.suffixed ? ` · <b>unit ${esc(info.upc.split('-')[1])}</b>` : ''}${info.cost != null ? ' · cost $' + money(info.cost) : ''}${v.price != null ? ' · price $' + esc(money(v.price)) : ''} · qty ${esc(v.quantity ?? '?')}</div>
          <div class="chips">
            ${prep.status ? `<span class="chip ${prep.status === 'good' ? 'ok' : (prep.status === 'bad' ? 'bad' : 'warn')}" title="${esc(prep.reason || '')}">status: ${esc(prep.status.toUpperCase())}</span>` : ''}
            <span class="chip ${stock.quantity ? 'ok' : 'warn'}" title="Warehouse rows with stock">stock: ${esc(stock.quantity ?? 0)}${stock.positions?.length ? ' · ' + esc(stock.positions.join(', ')) : ''}</span>
            ${storeChip('ebay')}${storeChip('amazon')}
          </div>
          <div class="chips">${preparing ? '<span class="chip"><span class="spin"></span> preparing title, price, specifics…</span>' : (info.proposal?.ready ? '<span class="chip ok">prepared</span>' : `<span class="chip warn">basic values only</span>`)}
            <button id="prepareBtn" class="mini" type="button" ${preparing ? 'disabled' : ''} title="Rebuild title, price, category, specifics and description">${info.proposal?.ready ? 'Re-prepare' : 'Prepare'}</button>
            ${info.preparing?.error ? `<span class="chip bad" title="${esc(info.preparing.error)}">prepare failed</span>` : ''}</div>
        </div></div>
      ${(info.proposal?.flags || []).filter(f => (f.level === 'block' || f.level === 'warn') && !/^Prepped:/i.test(f.message || '')).slice(0, 3).map(f => `<div class="flag ${esc(f.level)}">${esc(f.message)}</div>`).join('')}
      ${existing.length && !linkedHere ? `<div class="flag warn">Already on ${storeName(state.platform)}: ${existing.map(x => esc(x.listingId || x.asin || x.sku) + (x.state ? ' (' + esc(x.state) + ')' : '')).join(', ')}.
        <button id="markExisting" class="mini" type="button">Use that listing</button> ${item.storeUrl ? `<a href="${esc(item.storeUrl)}" target="_blank" rel="noopener">open</a>` : ''}</div>` : ''}

      <h3>Photos <span class="muted">(${listingPhotos.length})</span><span class="grow"></span><button id="addPhoto" class="mini" type="button">${state.qrOpen ? 'Hide QR' : '+ Photo'}</button><button id="photoSelectAll" class="mini" type="button">${state.selectedPhotos.size ? 'Clear' : 'Select all'}</button><button id="photoRefresh" class="mini" type="button" title="Reload photos (after taking new ones on the phone)">↻</button></h3>
      ${state.qrOpen ? `<div class="qr"><img src="${esc(serverBase() + '/api/lister/qr?text=' + encodeURIComponent(info.mobilePhotosUrl))}" alt="QR code for the phone photo page"><div class="small">Scan with the phone to add photos of this unit, then press ↻.<br><a href="${esc(info.mobilePhotosUrl)}" target="_blank" rel="noopener">open the page</a></div></div>` : ''}
      ${listingPhotos.length ? `<div class="photos">${listingPhotos.map(tile).join('')}</div>` : '<div class="muted small">No listing photos yet. Scan the QR code to add some from the phone, or use the condition photos below.</div>'}
      <div class="row tight">
        <button id="sendPhotos" type="button" ${store && !state.busy ? '' : 'disabled'} title="Puts the ticked photos (or all of ours) into the page's photo uploader">${state.busy === 'photos' ? 'Sending…' : 'Send to page'}</button>
        <button id="aiPhotos" type="button" ${state.aiBusy || !photos.length ? 'disabled' : ''}>${state.aiBusy ? esc(state.aiBusy) : 'AI photoshop'}</button>
        <button id="copyPhotos" class="mini" type="button">Copy URLs</button>
      </div>
      <details ${state.aiPrompt ? 'open' : ''}><summary>AI photoshop prompt</summary><textarea id="aiPrompt" rows="3">${esc(state.aiPrompt || info.aiPhotoPrompt || '')}</textarea><button id="aiPromptReset" class="mini" type="button">Reset to default</button></details>

      <h3>From prep: condition photos &amp; notes <span class="grow"></span>${prepPhotos.length || notes.length || voice.length || info.defect ? '' : '<span class="muted">none</span>'}</h3>
      ${prepPhotos.length ? `<div class="photos">${prepPhotos.map(tile).join('')}</div>` : ''}
      ${voice.map(n => `<div class="note"><b>Voice note</b> ${n.status === 'complete' ? '' : (state.voiceBusy.has(info.upc + ':' + n.id) || n.status === 'processing' ? '<span class="spin"></span> transcribing…' : `<button class="mini" data-transcribe="${n.id}" type="button">Transcribe</button>`)}
        ${n.english ? `<div>${esc(n.english)}</div>` : ''}${n.lithuanian ? `<div class="lt">${esc(n.lithuanian)}</div>` : ''}${n.error && !n.english ? `<div class="flag warn">${esc(n.error)}</div>` : ''}
        <audio controls preload="none" src="${esc(n.url)}"></audio>${n.english ? `<div class="row tight"><button class="mini" data-usenote="${n.id}" type="button">Use as condition note</button><button class="mini" data-transcribe="${n.id}" data-again="1" type="button">Redo</button></div>` : ''}</div>`).join('')}
      ${info.defect ? `<div class="note"><b>BOL reason:</b> ${esc(info.defect)}</div>` : ''}
      ${notes.map(n => `<div class="note">${esc(n.english || n.text)}${n.english && n.english !== n.text ? `<div class="lt">${esc(n.text)}</div>` : ''}<div class="when">${esc((n.createdAt || '').slice(0, 16).replace('T', ' '))}</div></div>`).join('')}
      ${v.conditionDescription ? `<div class="note" style="border-left:4px solid var(--blue)"><b>Condition note on the listing${v.conditionDescriptionSource === 'notes' ? ' (from the prep notes — read it once)' : ''}:</b> ${esc(v.conditionDescription)}</div>` : ''}

      ${catalogPhotos.length ? `<details><summary>Catalog images (${catalogPhotos.length})</summary><div class="photos">${catalogPhotos.map(tile).join('')}</div></details>` : ''}
      ${aspects.length ? `<details><summary>Item specifics (${aspects.length})</summary><div class="chips">${aspects.map(([k, vals]) => `<code title="click to copy" data-copy="${esc(vals[0])}">${esc(k)}: ${esc(vals.join(' / '))}</code>`).join('')}</div></details>` : ''}
      <div class="row"><button id="copyAllBtn" class="mini" type="button">Copy all values</button><button id="openAgent" class="link" type="button">Open in Listing Agent</button><button id="openItems" class="link" type="button">Items to List</button></div>
      <details><summary>Text for manual paste</summary><pre class="copyall">${esc(copyAll)}</pre></details>`;

    $('prepareBtn').onclick = () => { state.prepareAsked.delete(info.upc); void maybePrepare(info, { force: true }); };
    if ($('markExisting')) $('markExisting').onclick = () => markExisting(item);
    for (const button of el.querySelectorAll('button[data-transcribe]')) button.onclick = () => transcribe(info.upc, Number(button.dataset.transcribe), Boolean(button.dataset.again));
    for (const button of el.querySelectorAll('button[data-usenote]')) button.onclick = async () => {
      const note = voice.find(n => n.id === Number(button.dataset.usenote));
      if (!note) return;
      const currentNote = values(info).conditionDescription || '';
      const next = currentNote.includes(note.english) ? currentNote : [currentNote, note.english].filter(Boolean).join('\n');
      state.edits[info.upc] = { ...(state.edits[info.upc] || {}), conditionDescription: next, conditionDescriptionSource: 'notes' };
      const pushed = await pushValue(info, 'conditionDescription');
      toast(pushed ? 'Condition note updated on the page' : 'Condition note updated');
      renderDetail();
    };
    $('photoSelectAll').onclick = () => { if (state.selectedPhotos.size) state.selectedPhotos = new Set(); else state.selectedPhotos = new Set(photos.filter(p => p.source !== 'catalog').map(p => p.url)); renderDetail(); };
    $('photoRefresh').onclick = () => { delete state.details[info.upc]; void loadDetail(info.upc, { force: true }); };
    for (const box of el.querySelectorAll('input[data-select]')) box.onchange = () => { if (box.checked) state.selectedPhotos.add(box.dataset.select); else state.selectedPhotos.delete(box.dataset.select); box.closest('.photo').classList.toggle('selected', box.checked); };
    for (const tileEl of el.querySelectorAll('.photo')) {
      tileEl.onclick = event => { if (event.target.matches('input')) return; const box = tileEl.querySelector('input'); box.checked = !box.checked; box.dispatchEvent(new Event('change')); };
      tileEl.onmouseenter = () => { void photoFile(tileEl.dataset.url).catch(() => {}); };
      tileEl.onmousedown = () => { void photoFile(tileEl.dataset.url).catch(() => {}); };
      tileEl.ondragstart = event => {
        const url = tileEl.dataset.url;
        const cached = state.photoFiles[url];
        const photo = photos.find(p => p.url === url) || {};
        event.dataTransfer.effectAllowed = 'copy';
        event.dataTransfer.setData('text/uri-list', url);
        event.dataTransfer.setData('text/plain', url);
        // The page script turns this into a real file where it lands (a File cannot cross from the panel).
        event.dataTransfer.setData('application/x-sweetshelves-photo', JSON.stringify({ url, name: cached?.name || photo.name || 'photo.jpg', type: cached?.type || 'image/jpeg', base64: cached?.base64 || '' }));
        if (cached) {
          event.dataTransfer.setData('DownloadURL', `${cached.type}:${cached.name}:${url}`);
          try { event.dataTransfer.items.add(cached.file); } catch { /* the page gets the URL instead */ }
        }
      };
    }
    $('sendPhotos').onclick = () => sendPhotos();
    $('aiPhotos').onclick = () => aiPhotoshop();
    $('aiPromptReset').onclick = () => { $('aiPrompt').value = info.aiPhotoPrompt || ''; state.aiPrompt = ''; remember(); };
    $('addPhoto').onclick = () => { state.qrOpen = !state.qrOpen; renderDetail(); };
    $('copyPhotos').onclick = () => copy(photos.map(p => p.url).join('\n'), 'Copied photo URLs');
    $('copyAllBtn').onclick = () => copy(copyAll, 'Copied listing text');
    $('openAgent').onclick = () => chrome.tabs.create({ url: serverBase() + '/listingagent?upc=' + encodeURIComponent(info.upc) });
    $('openItems').onclick = () => chrome.tabs.create({ url: serverBase() + '/items-to-list?q=' + encodeURIComponent(info.baseUpc || info.upc) });
    for (const code of el.querySelectorAll('code[data-copy]')) code.onclick = () => copy(code.dataset.copy, 'Copied ' + code.dataset.copy);
  }

  function renderGuideOnly() {
    renderPage();
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

  async function applyPick(target, remember_) {
    const info = detail();
    const pick = state.pick;
    if (!info || !pick?.pickId) return;
    const vals = values(info);
    let value = target === 'description' ? (vals.descriptionText || '') : (vals[target] ?? '');
    const options = {};
    if (target === 'condition') { options.labels = M.conditionLabels(vals.condition, state.page.store); value = options.labels[0] || vals.condition; }
    if (target === 'description') options.html = vals.descriptionHtml || '';
    if (target.startsWith('aspect:')) {
      const aspect = target.slice(7);
      const list = (info.fields.aspects || {})[aspect] || [];
      value = list[0] || ''; options.labels = list;
    }
    try {
      const result = await pageMessage({ type: 'set-picked', pickId: pick.pickId, value: String(value), options });
      if (!result.ok) throw new Error(result.reason || 'could not set that field');
      if (remember_ && !target.startsWith('aspect:')) {
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
    const info = detail();
    const f = pick.field || {};
    const label = f.labelText || f.ariaLabel || f.placeholder || f.name || f.id || f.tag;
    const suggested = (pick.suggestions || []).slice(0, 3).map(s => s.target);
    const aspects = Object.keys(info?.fields?.aspects || {});
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
    const info = detail();
    if (!info || !$('cfPrice')) return;
    const v = values(info);
    if (!$('cfPrice').dataset.touched) $('cfPrice').value = v.price ?? '';
    if (!$('cfQuantity').dataset.touched) $('cfQuantity').value = v.quantity ?? '';
  }

  function renderConfirm() {
    const item = current();
    const info = detail();
    const el = $('confirm');
    el.hidden = !item || !info || state.view !== 'item';
    if (!item || !info || state.view !== 'item') return;
    const page = state.page || {};
    const platform = page.store || state.platform;
    const v = values(info);
    const linked = info.links || [];
    const lastLink = state.lastLink && state.lastLink.upc === info.upc ? state.lastLink : null;
    const successPage = page.kind === 'listing-success' || page.kind === 'offer-success' || page.kind === 'listing-live';
    el.innerHTML = `
      <details ${successPage || lastLink ? 'open' : ''}><summary><b>Confirm &amp; link</b> <span class="muted">(recorded automatically when the store confirms; use this if it did not)</span></summary>
      ${linked.length ? `<div class="flag info">Linked: ${linked.map(l => `${esc(l.platform)} ${esc(l.listing_id || l.sku || l.asin)}${l.url ? ` <a href="${esc(l.url)}" target="_blank" rel="noopener">open</a>` : ''}`).join(' · ')}</div>` : ''}
      ${lastLink ? `<div class="flag info"><b>Recorded.</b><ul class="steps">${(lastLink.effects?.steps || []).map(s => `<li>${esc(s)}</li>`).join('')}</ul><button id="undoLink" class="link danger" type="button">Undo this link</button></div>` : ''}
      <p class="small muted">The store's item number / SKU is tied to UPC <code>${esc(info.upc)}</code> so Ready to Ship and the warehouse find this exact unit.</p>
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
        <span class="muted small">Marks it listed on Items to List and in the ledger.</span></div></details>`;
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

  function setPlatform(platform) {
    if (state.platform === platform) return;
    state.platform = platform; state.report = null; state.guide = null; remember();
    void loadQueue({ keep: false });
  }

  function setView(view) {
    state.view = view; renderStoreBar(); renderDetail(); renderConfirm();
    if (view === 'item' && state.currentUpc) void loadDetail(state.currentUpc);
  }

  function wireStatic() {
    $('settingsBtn').onclick = () => {
      const s = $('settings'); s.hidden = !s.hidden;
      $('setServer').value = state.settings.server; $('setActor').value = state.settings.actor;
      for (const key of ['autoFill', 'autoGuide', 'autoLink', 'autoPrepare']) $('set' + key[0].toUpperCase() + key.slice(1)).checked = state.settings[key] !== false;
    };
    $('saveSettings').onclick = async () => {
      let server = $('setServer').value.trim() || DEFAULTS.server;
      try { const u = new URL(server); if (u.protocol !== 'https:' && !/^http:\/\/(localhost|127\.0\.0\.1)/.test(server)) throw new Error(); server = u.origin; } catch { toast('Server must be an https origin', true); return; }
      try {
        const granted = await chrome.permissions.contains({ origins: [server + '/*'] }) || await chrome.permissions.request({ origins: [server + '/*'] });
        if (!granted) { toast('Site access to that server was not granted', true); return; }
      } catch { /* permission API unavailable for this origin pattern; fetch will report */ }
      await saveSettings({ server, actor: $('setActor').value.trim(), autoFill: $('setAutoFill').checked,
        autoGuide: $('setAutoGuide').checked, autoLink: $('setAutoLink').checked, autoPrepare: $('setAutoPrepare').checked });
      $('settings').hidden = true; state.connected = null; renderHeader();
      await connect(); await loadQueue();
    };
    $('openServer').onclick = () => chrome.tabs.create({ url: serverBase() + '/items-to-list' });
    $('openLedger').onclick = () => chrome.tabs.create({ url: serverBase() + '/lister-ledger' });
    $('openUpdates').onclick = () => chrome.tabs.create({ url: serverBase() + '/lister/' });
    $('signinBtn').onclick = () => chrome.tabs.create({ url: serverBase() + '/lister/' });
    $('connStatus').onclick = () => { state.connected = null; renderHeader(); void connect().then(() => loadQueue()); };
    $('storeEbay').onclick = () => setPlatform('ebay');
    $('storeAmazon').onclick = () => setPlatform('amazon');
    $('unlock').onclick = () => { state.locked = null; setView('list'); renderStoreBar(); };
    $('viewList').onclick = () => setView('list');
    $('viewItem').onclick = () => setView('item');
    $('filter').oninput = () => { state.filter = $('filter').value; renderItems(); };
    $('reload').onclick = () => { state.details = {}; void connect().then(() => loadQueue()); void refreshTab(); };
    chrome.tabs.onActivated.addListener(scheduleRefresh);
    chrome.tabs.onUpdated.addListener((tabId, info, tab) => { if (tab?.active && (info.status === 'complete' || info.url)) scheduleRefresh(); });
    chrome.windows?.onFocusChanged?.addListener(() => scheduleRefresh());
    // Store pages render their forms after load; look again a little later.
    chrome.tabs.onUpdated.addListener((tabId, info, tab) => { if (tab?.active && info.status === 'complete') setTimeout(scheduleRefresh, 2500); });
  }

  async function main() {
    await loadStorage();
    wireStatic();
    renderAll();
    await connect();
    // The queue first, so a panel opened on the store's search page already has an item to search.
    await loadQueue();
    await refreshTab();
  }

  void main();
})();
