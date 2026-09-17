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
  const DEFAULTS = { server: 'https://pi.nexuscentralhq.org', actor: '', autoFill: true, autoGuide: true, autoLink: true, autoPrepare: true, autoAiTitle: false, autoAiDescription: false, autoAiPhotos: false, autoSendPhotos: false };
  const CONDITIONS = ['NEW', 'NEW_OTHER', 'NEW_WITH_DEFECTS', 'USED_EXCELLENT', 'USED_VERY_GOOD', 'USED_GOOD', 'USED_ACCEPTABLE', 'FOR_PARTS_OR_NOT_WORKING'];
  const VALUE_LABELS = {
    title: 'Title', price: 'Price', quantity: 'Quantity', sku: 'SKU / custom label', upc: 'UPC', asin: 'ASIN',
    brand: 'Brand', conditionDescription: 'Condition description', description: 'Description', condition: 'Condition',
  };
  const START_URLS = {
    ebay: () => 'https://www.ebay.com/sl/prelist/suggest?sr=wn',
    amazon: () => 'https://sellercentral.amazon.com/abis/listing/syh',  // "List Your Products": the panel types the UPC there
  };

  const state = {
    settings: { ...DEFAULTS }, connected: null, user: '', serverVersion: '', signIn: false,
    platform: 'ebay', view: 'list', filter: '', items: [], counts: {}, currentUpc: null, details: {}, edits: {},
    tab: null, page: null, report: null, pick: null, learned: {}, busy: '', lastLink: null,
    autoFilled: new Set(), searched: new Set(), autoLinked: new Set(), fillSessions: {}, pendingSearch: null,
    guide: null, selectedPhotos: new Set(), photoFiles: {}, aiPrompt: '', aiBusy: '', prepareTimers: {}, prepareAsked: new Set(),
    voiceBusy: new Set(), qrOpen: false, toastAction: null, autoText: new Set(), autoPhotos: new Set(),
    busyTasks: new Map(), checkingAmazon: new Set(), amazonRunning: false, tabItems: {}, autoSent: new Set(), statusFilter: 'all', aiGenerated: {},
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

  // Something is running: show the moving bar with what it is until every task released it.
  function busy(label) {
    const id = Symbol(label);
    state.busyTasks.set(id, label);
    renderBusy();
    return () => { state.busyTasks.delete(id); renderBusy(); };
  }

  function renderBusy() {
    const el = $('busy');
    if (!el) return;
    const labels = [...state.busyTasks.values()];
    el.hidden = !labels.length;
    $('busyLabel').textContent = labels[labels.length - 1] || '';
  }

  // Each store tab keeps its own item, so an unfinished eBay listing is untouched while another tab lists on Amazon.
  function bindTab(upc, platform, tabId = state.tab?.id) {
    if (!tabId || !upc) return;
    state.tabItems[tabId] = { upc, platform };
    try { void chrome.storage.session?.set({ ssListerTabs: state.tabItems }); } catch { /* session storage unavailable */ }
  }

  // -- storage ------------------------------------------------------------------------

  async function loadStorage() {
    const stored = await chrome.storage.local.get([SETTINGS_KEY, LEARNED_KEY, CURRENT_KEY, PLATFORM_KEY, PROMPT_KEY]);
    state.settings = { ...DEFAULTS, ...(stored[SETTINGS_KEY] || {}) };
    state.learned = stored[LEARNED_KEY] || {};
    state.currentUpc = stored[CURRENT_KEY] || null;
    state.platform = stored[PLATFORM_KEY] === 'amazon' ? 'amazon' : 'ebay';
    state.aiPrompt = stored[PROMPT_KEY] || '';
    try { state.tabItems = (await chrome.storage.session?.get('ssListerTabs'))?.ssListerTabs || {}; } catch { state.tabItems = {}; }
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
    const done = busy('Loading the queue…');
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
    done();
    renderAll();
    if (state.currentUpc) void loadDetail(state.currentUpc);
    void runAmazonChecks();
  }

  // Can Amazon take each queued UPC from us? One check at a time; the row shows the result.
  async function runAmazonChecks() {
    if (state.amazonRunning) return;
    const pending = state.items.filter(it => it.status === 'queued' && !it.amazonCheck && !state.checkingAmazon.has(it.baseUpc));
    if (!pending.length) return;
    state.amazonRunning = true;
    const done = busy('Checking Amazon listability…');
    try {
      for (const it of pending) {
        state.checkingAmazon.add(it.baseUpc); renderItems();
        try {
          const data = await api('/api/lister/queue/' + encodeURIComponent(it.upc) + '/amazon-check', { method: 'POST', body: {} });
          for (const row of state.items) if (row.baseUpc === it.baseUpc) row.amazonCheck = data.check;
        } catch (error) {
          for (const row of state.items) if (row.baseUpc === it.baseUpc) row.amazonCheck = { status: 'error', error: error.message, reasons: [] };
          if (error.status === 501 || error.signIn) break;
        } finally {
          state.checkingAmazon.delete(it.baseUpc); renderItems();
        }
      }
    } finally {
      state.amazonRunning = false; done();
    }
  }

  function current() {
    return state.items.find(it => it.upc === state.currentUpc) || null;
  }

  function detail() {
    return state.currentUpc ? state.details[state.currentUpc] || null : null;
  }

  async function loadDetail(upc, { force = false } = {}) {
    if (!upc || (!force && state.details[upc] && Date.now() - state.details[upc].loadedAt < 30000)) { renderDetail(); renderConfirm(); return state.details[upc]; }
    const done = busy('Loading the item…');
    try {
      const data = await api('/api/lister/queue/' + encodeURIComponent(upc));
      const item = data.item;
      item.loadedAt = Date.now();
      state.details[upc] = item;
      if (upc === state.currentUpc) { renderDetail(); renderConfirm(); }
      void maybePrepare(item);
      void maybeTranscribe(item);
      void maybeAutoPhotos(item);
      return item;
    } catch (error) {
      toast(error.message, true);
      return null;
    } finally {
      done();
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
      const release = busy('Preparing title, price, specifics and description…');
      let tries = 0;
      clearTimeout(state.prepareTimers[key]);
      const poll = async () => {
        tries += 1;
        const fresh = await loadDetail(key, { force: true });
        if (!fresh) return;
        if (fresh.proposal?.ready && !fresh.preparing?.running) { release(); if (key === state.currentUpc) toast('Listing values prepared'); return; }
        if (fresh.preparing?.error) { release(); if (key === state.currentUpc) toast('Prepare failed: ' + fresh.preparing.error, true); return; }
        if (tries < 40) state.prepareTimers[key] = setTimeout(poll, 4000); else release();
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
    const done = busy('Transcribing a voice note…');
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
      state.voiceBusy.delete(key); done();
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
        if (state.page.guide && !state.page.guide.active && state.guide?.active) state.guide = null;
      } catch (error) {
        state.page.error = error.message;
      }
    }
    const bound = tab?.id ? state.tabItems[tab.id] : null;
    if (state.page.store && state.page.store !== state.platform) {
      // Follow the store the user is looking at.
      state.platform = state.page.store; remember();
      await loadQueue();
    }
    if (bound && state.page.store && bound.platform === state.page.store && state.items.some(it => it.upc === bound.upc) && state.currentUpc !== bound.upc) {
      // This tab has its own item (another tab may be on a different one).
      state.currentUpc = bound.upc; state.report = null; state.guide = null; remember();
      void loadDetail(bound.upc);
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
  async function generate(info, kind, { auto = false } = {}) {
    const v = values(info);
    state.genBusy = kind; renderPage();
    const done = busy('Writing the ' + kind + ' with AI…');
    try {
      const notes = [...(info.notes || []).map(n => n.english || n.text), ...(info.voiceNotes || []).map(n => n.english), info.defect].filter(Boolean);
      const data = await api('/api/lister/queue/' + encodeURIComponent(info.upc) + '/generate', { method: 'POST', body: { kind, values: {
        title: v.title, systemTitle: info.title, brand: v.brand, categoryPath: v.categoryPath, condition: v.condition,
        conditionDescription: v.conditionDescription, notes, aspects: info.fields.aspects || {},
      } } });
      const edits = state.edits[info.upc] = { ...(state.edits[info.upc] || {}) };
      // Done for this item on this page: switching the auto toggle on later must not run it again.
      if (state.page?.store) state.autoText.add((state.tab?.id || 0) + '|' + state.page.store + '|' + info.upc + '|' + kind);
      if (kind === 'title') edits.title = data.title;
      else { edits.descriptionText = data.descriptionText; edits.descriptionHtml = data.descriptionHtml; }
      const pushed = await pushValue(info, kind);
      state.aiGenerated[info.upc] = { ...(state.aiGenerated[info.upc] || {}), [kind]: auto ? 'auto' : 'manual' };
      if (state.guide?.active) await guide('start', { silent: true });  // refresh the overlay's "generated with AI" marks
      toast((kind === 'title' ? 'Title written: ' + data.title : 'Description written from the item and its notes') + (pushed ? ' (on the page)' : ''));
    } catch (error) {
      toast('AI ' + kind + ': ' + error.message, true);
    } finally {
      state.genBusy = ''; done(); renderPage(); renderDetail();
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
    // Once per tab and item: eBay rewrites the form URL while editing (draft id), which must not refill.
    const key = (state.tab?.id || 0) + '|' + page.store + '|' + item.upc;
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
      await maybeAutoText();
      await maybeAutoSend();
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
        response = await pageMessage({ type: 'guide-start', options: { values: v, store: state.page.store, aspects: info.fields.aspects || {}, noteFields, aiFields: state.aiGenerated[info.upc] || {} } });
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
    } else if (message?.type === 'ss-lister-action') {
      if (message.action === 'fill') void fillPage();
      else if (message.action === 'pick') void startPick();
      else if (message.action === 'generate' && detail()) void generate(detail(), message.kind === 'description' ? 'description' : 'title');
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
      if (state.currentUpc) bindTab(state.currentUpc, body.platform);
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
    if (onStore) { await chrome.tabs.update(state.tab.id, { url }); bindTab(item.upc, platform); }
    else { const created = await chrome.tabs.create({ url, active: true }); bindTab(item.upc, platform, created?.id); }
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
    await sendPhotoUrls(info, urls);
  }

  // Our photos, AI version preferred over its original when one exists.
  function bestPhotoUrls(info) {
    const photos = info.photos || [];
    const aiFor = new Map(photos.filter(p => p.source === 'ai' && p.from).map(p => [p.from, p.url]));
    const out = [];
    for (const p of photos) {
      if (p.source === 'catalog') continue;
      const url = p.source === 'ai' ? p.url : (aiFor.get(p.name) || p.url);
      if (!out.includes(url)) out.push(url);
    }
    return out.slice(0, 24);
  }

  function pendingAiPhotos(info) {
    const done = new Set((info.photos || []).filter(p => p.source === 'ai').map(p => p.from).filter(Boolean));
    return (info.photos || []).some(p => (p.source === 'listing' || p.source === 'prep') && !done.has(p.name));
  }

  // Auto send: once per tab and item, after the fill (or after the automatic AI photos finished).
  async function maybeAutoSend() {
    const info = detail();
    const page = state.page;
    if (!info || !state.settings.autoSendPhotos || !page?.store || !(page.kind === 'listing-form' || page.kind === 'offer-form')) return;
    if (state.settings.autoAiPhotos && pendingAiPhotos(info)) return;  // maybeAutoPhotos sends when done
    const key = (state.tab?.id || 0) + '|' + page.store + '|' + info.upc;
    if (state.autoSent.has(key)) return;
    const urls = bestPhotoUrls(info);
    if (!urls.length) return;
    state.autoSent.add(key);
    await sendPhotoUrls(info, urls);
  }

  async function sendPhotoUrls(info, urls) {
    state.busy = 'photos'; renderDetail();
    const done = busy('Sending photos to the page…');
    try {
      const files = [];
      for (const url of urls) { const f = await photoFile(url); files.push({ name: f.name, type: f.type, base64: f.base64 }); }
      const result = await pageMessage({ type: 'add-photos', options: { files } });
      toast(result.ok ? `Sent ${result.count} photo${result.count === 1 ? '' : 's'} to the page (${result.method})` : result.reason, !result.ok);
    } catch (error) {
      toast('Photos: ' + error.message, true);
    } finally {
      state.busy = ''; done(); renderDetail();
    }
  }

  async function aiPhotoshop() {
    const info = detail();
    if (!info) return;
    const urls = (info.photos || []).map(p => p.url).filter(u => state.selectedPhotos.has(u));
    if (!urls.length) { toast('Tick the photos to clean up first', true); return; }
    await aiPhotoshopUrls(info, urls);
  }

  // Auto mode: every photo of ours (listing + prep) that has no AI version yet, once per item and session.
  async function maybeAutoPhotos(info) {
    if (!info || !state.settings.autoAiPhotos || state.aiBusy) return;
    const done = new Set((info.photos || []).filter(p => p.source === 'ai').map(p => p.from).filter(Boolean));
    const urls = (info.photos || []).filter(p => (p.source === 'listing' || p.source === 'prep') && !done.has(p.name) && !state.autoPhotos.has(p.url)).map(p => p.url);
    if (!urls.length) return;
    for (const url of urls) state.autoPhotos.add(url);
    await aiPhotoshopUrls(info, urls);
    await maybeAutoSend();
  }

  async function aiPhotoshopUrls(info, urls) {
    const prompt = ($('aiPrompt')?.value || state.aiPrompt || '').trim();
    state.aiPrompt = prompt && prompt !== info.aiPhotoPrompt ? prompt : '';
    remember();
    let done = 0;
    const release = busy('AI photoshop…');
    for (const url of urls) {
      state.aiBusy = `AI photoshop ${done + 1}/${urls.length}…`; renderDetail();
      try {
        const data = await api('/api/lister/photos/ai', { method: 'POST', body: { upc: info.upc, url, prompt } });
        info.photos.unshift(data.photo);
        state.autoPhotos.add(url);  // a manual run counts: the auto switch skips this photo later
        state.selectedPhotos.delete(url);
        state.selectedPhotos.add(data.photo.url);
        done += 1;
      } catch (error) {
        toast('AI photoshop: ' + error.message, true);
        break;
      }
    }
    state.aiBusy = ''; release();
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
    const toggles = `<div class="toggles">
        <label class="check"><input id="autoAiTitle" type="checkbox" ${state.settings.autoAiTitle ? 'checked' : ''}> AI title as the page loads</label>
        <label class="check"><input id="autoAiDescription" type="checkbox" ${state.settings.autoAiDescription ? 'checked' : ''}> AI description as the page loads</label>
        <label class="check"><input id="autoAiPhotosTop" type="checkbox" ${state.settings.autoAiPhotos ? 'checked' : ''}> AI photoshop on every photo</label>
        <label class="check"><input id="autoSendPhotos" type="checkbox" ${state.settings.autoSendPhotos ? 'checked' : ''}> send photos to the page as it loads</label>
      </div>`;
    const wireToggles = () => {
      for (const key of ['autoAiTitle', 'autoAiDescription']) {
        const box = $(key);
        if (box) box.onchange = async () => { await saveSettings({ ...state.settings, [key]: box.checked }); toast(box.checked ? 'Will write the ' + (key === 'autoAiTitle' ? 'title' : 'description') + ' automatically on every listing' : 'Automatic ' + (key === 'autoAiTitle' ? 'title' : 'description') + ' off'); if (box.checked) void maybeAutoText(); };
      }
      const sendBox = $('autoSendPhotos');
      if (sendBox) sendBox.onchange = async () => { await saveSettings({ ...state.settings, autoSendPhotos: sendBox.checked }); toast(sendBox.checked ? 'Photos will go to the page on every listing' : 'Automatic photo send off'); if (sendBox.checked) void maybeAutoSend(); };
      const photosBox = $('autoAiPhotosTop');
      if (photosBox) photosBox.onchange = async () => { await saveSettings({ ...state.settings, autoAiPhotos: photosBox.checked }); toast(photosBox.checked ? 'Every photo of ours will get an AI version automatically' : 'Automatic AI photoshop off'); renderDetail(); if (photosBox.checked && detail()) void maybeAutoPhotos(detail()); };
    };
    if (!page.store) {
      el.innerHTML = `<div class="store"><span class="badge none">no store page</span><span class="grow muted small">Pick an item, then start the listing. The panel searches the UPC, fills the form and records the listing.</span></div>
        ${item ? `<div class="actions"><button id="startBtn" class="primary" type="button">Start on ${storeName(state.platform)}</button></div>` : ''}${toggles}`;
      if ($('startBtn')) $('startBtn').onclick = () => startOn(state.platform);
      wireToggles();
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
    const actions = item ? [
      page.kind === 'listing-start' ? `<button id="searchBtn" class="primary" type="button" title="Type the UPC into the store's product search">Search UPC</button>` : '',
      !onForm && page.kind !== 'listing-start' && !ASSIST_KINDS.has(page.kind) ? `<button id="startBtn" type="button">Start on ${storeName(state.platform)}</button>` : '',
      onForm && !g?.active ? `<button id="guideBtn" class="primary" type="button" title="Show the checklist overlay on the page (fill, AI text, pick a field live there)">Show checklist</button>` : '',
    ].filter(Boolean).join('') : '';
    el.innerHTML = `
      <div class="store"><span class="badge ${page.store}">${storeName(page.store)}</span>
        <span class="grow">${esc(kindText)}${g?.active && onForm ? ` <span class="muted small">· ${esc(g.open)} of ${esc(g.total)} left</span>` : ''}${state.genBusy ? ` <span class="muted small">· writing ${esc(state.genBusy)}…</span>` : ''}</span>
        <button id="pageRefresh" class="icon" type="button" title="Re-read page">↻</button></div>
      ${chips.length ? `<div class="chips">${chips.join('')}</div>` : ''}
      ${assistLine}
      ${actions ? `<div class="actions">${actions}</div>` : ''}
      ${toggles}
      ${page.error ? `<div class="flag warn">Page script: ${esc(page.error)}</div>` : ''}`;
    $('pageRefresh').onclick = () => refreshTab();
    if ($('startBtn')) $('startBtn').onclick = () => startOn(state.platform);
    if ($('searchBtn')) $('searchBtn').onclick = () => { state.pendingSearch = { upc: item.upc, platform: state.platform }; void searchUpc(); };
    if ($('guideBtn')) $('guideBtn').onclick = () => guide('start');
    wireToggles();
  }

  // Put one value on the store page (after an AI text or a note was applied) without a full re-fill.
  async function pushValue(info, target) {
    if (!state.page?.store || !(state.page.kind === 'listing-form' || state.page.kind === 'offer-form')) return false;
    try {
      const result = await pageMessage({ type: 'fill', options: { values: values(info), store: state.page.store, targets: [target], aspects: {}, learned: state.learned[state.page.store + ':' + (state.page.kind || '')] || {} } });
      return (result.report.filled || []).some(f => f.target === target);
    } catch { return false; }
  }

  // The two auto-AI switches: once per tab and item, after the form was filled.
  async function maybeAutoText() {
    const info = detail();
    const page = state.page;
    if (!info || !page?.store || !(page.kind === 'listing-form' || page.kind === 'offer-form')) return;
    for (const kind of ['title', 'description']) {
      if (!state.settings[kind === 'title' ? 'autoAiTitle' : 'autoAiDescription']) continue;
      const key = (state.tab?.id || 0) + '|' + page.store + '|' + info.upc + '|' + kind;
      if (state.autoText.has(key)) continue;
      state.autoText.add(key);
      await generate(info, kind, { auto: true });
    }
  }

  function renderItems() {
    const filter = state.filter.trim().toLowerCase();
    const byStatus = it => {
      const f = state.statusFilter;
      if (f === 'good' || f === 'bad') return (it.prepStatus?.status || '') === f;
      if (f === 'listed') return it.status === 'listed' || it.alreadyOnStore || it.otherStatus === 'listed';
      return true;
    };
    for (const [id, value] of [['statusAll', 'all'], ['statusGood', 'good'], ['statusBad', 'bad'], ['statusListed', 'listed']]) $(id).classList.toggle('active', state.statusFilter === value);
    const rows = state.items.filter(it => byStatus(it) && (!filter || (it.title || '').toLowerCase().includes(filter) || (it.upc || '').includes(filter)));
    const list = $('itemList');
    if (!rows.length) {
      list.innerHTML = `<div class="empty">${state.connected === false ? 'Not connected.' : (state.statusFilter !== 'all' || filter ? 'Nothing matches this filter.' : `Nothing queued for ${storeName(state.platform)}. Add items to the Listing Agent queue on <b>Items to List</b>.`)}</div>`;
      return;
    }
    const active = rows.filter(it => it.status === 'queued');
    const done = rows.filter(it => it.status !== 'queued');
    const row = (it, first) => {
      const chips = [];
      const other = state.platform === 'ebay' ? 'amazon' : 'ebay';
      if (it.status === 'listed') chips.push(`<span class="chip store ${state.platform}" title="Listed on ${storeName(state.platform)} through the panel">${storeName(state.platform)} ✓ listed ${it.storeUrl ? `<a href="${esc(it.storeUrl)}" target="_blank" rel="noopener">↗</a>` : ''}</span>`);
      else if (it.alreadyOnStore) chips.push(`<span class="chip store ${state.platform}" title="${storeName(state.platform)} already carries this UPC">on ${storeName(state.platform)} ${it.storeUrl ? `<a href="${esc(it.storeUrl)}" target="_blank" rel="noopener" title="Open the store listing">↗</a>` : ''}</span>`);
      if (it.otherStatus === 'listed') chips.push(`<span class="chip store ${other}" title="Already listed on ${storeName(other)}">${storeName(other)} ✓ listed</span>`);
      if (it.notes) chips.push(`<span class="chip note" title="Prep notes on file">${it.notes.written ? '📝 ' + esc(it.notes.written) : ''}${it.notes.written && it.notes.voice ? ' · ' : ''}${it.notes.voice ? '🎤 ' + esc(it.notes.voice) : ''}</span>`);
      const ps = it.prepStatus || {};
      if (ps.status) chips.push(`<span class="chip ${ps.status === 'good' ? 'ok' : (ps.status === 'bad' ? 'bad' : 'warn')}" title="Item Prep status">${esc(ps.status)}${ps.reason ? ' · ' + esc(ps.reason) : ''}</span>`);
      if (it.defect && (it.defect || '').toLowerCase() !== (ps.reason || '').toLowerCase()) chips.push(`<span class="chip defect" title="Defect noted on the BOL">${esc(it.defect)}</span>`);
      if (it.preparing) chips.push('<span class="chip"><span class="spin"></span> preparing</span>');
      const ac = it.amazonCheck;
      if (state.checkingAmazon.has(it.baseUpc)) chips.push('<span class="chip checking"><span class="spin"></span> Amazon check</span>');
      else if (ac?.status === 'restricted') chips.push(`<span class="chip bad" title="${esc((ac.reasons || []).join(' · ') || 'Amazon restricts this listing for us')}">Amazon ✕ restricted</span>`);
      else if (ac?.status === 'no_asin') chips.push('<span class="chip warn" title="No ASIN for this UPC: Amazon has no product page to list against">not in Amazon catalog</span>');
      else if (ac?.status === 'listable') chips.push(`<span class="chip ok" title="ASIN ${esc(ac.asin)}${ac.brand ? ' · ' + esc(ac.brand) : ''}">Amazon ✓</span>`);
      else if (ac && ac.status !== 'listable') chips.push(`<span class="chip" title="${esc(ac.error || 'check failed')}">Amazon ?</span>`);
      const upcHtml = it.suffixed ? `${esc(it.baseUpc)}-<b class="suffix" title="Specific unit: the SKU keeps the -suffix">${esc(it.upc.split('-')[1])}</b>` : esc(it.upc);
      return `<div class="item ${it.upc === state.currentUpc ? 'current' : ''} ${first ? 'first' : ''} ${it.status === 'listed' ? 'listed' : ''} ${ac?.status === 'restricted' ? 'restricted' : ''}" data-upc="${esc(it.upc)}" title="${esc(it.title)}">
        ${it.thumb ? `<img src="${esc(it.thumb)}" alt="" loading="lazy">` : '<div class="noimg"></div>'}
        <div><div class="title">${esc(it.title || '(no title)')}</div>
          <div class="meta"><span>${upcHtml}</span>${chips.join('')}</div></div>
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
        if (state.page?.store) bindTab(state.currentUpc, state.platform);
        // Picked while the store's search page is open: search this UPC right away.
        state.pendingSearch = { upc: state.currentUpc, platform: state.platform };
        void maybeAutoSearch({ force: true });
      };
    }
    for (const button of list.querySelectorAll('button[data-skip]')) {
      button.onclick = event => { event.stopPropagation(); const it = state.items.find(x => x.upc === button.dataset.skip); if (it) void skipItem(it); };
    }
  }

  // "LT: ... | EN: ..." written notes split into their two halves for the side-by-side view.
  function noteHalves(note) {
    const text = String(note.text || '');
    const match = text.match(/^\s*LT:\s*([\s\S]*?)\s*\|\s*EN:\s*([\s\S]*)$/i);
    if (match) return { lt: match[1].trim(), en: match[2].trim() };
    if (/^\s*LT:/i.test(text)) return { lt: text.replace(/^\s*LT:\s*/i, '').trim(), en: note.english || '' };
    if (/^\s*EN:/i.test(text)) return { lt: '', en: text.replace(/^\s*EN:\s*/i, '').trim() };
    return { lt: '', en: note.english || text };
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
    const notes = info.notes || [];
    const voice = info.voiceNotes || [];
    const photos = info.photos || [];
    const existing = (info.existing || {})[state.platform] || [];
    const linkedHere = (info.links || []).find(l => l.platform === state.platform);
    const aspects = Object.entries(info.fields.aspects || {});
    const prep = info.prepStatus || {};
    const stock = info.inventory || {};
    const gate = info.gate || {};
    const storeTile = platform => {
      const linked = (info.links || []).some(l => l.platform === platform) || info.queue?.listed?.[platform];
      const onStore = ((info.existing || {})[platform] || []).length > 0;
      const skipped = info.queue?.skipped?.includes(platform);
      const [cls, text, sub] = linked ? ['ok', 'LISTED', 'recorded by the panel'] : onStore ? ['warn', 'ON STORE', 'already carries this UPC'] : skipped ? ['off', 'SKIPPED', 'left off this list'] : ['todo', 'NOT LISTED', ''];
      return `<div class="tile ${cls}"><div class="k">${storeName(platform)}</div><div class="v">${text}</div><div class="s">${esc(sub)}</div></div>`;
    };
    const rackQty = gate.rackQty ?? stock.quantity ?? 0;
    const listable = gate.listable ?? (stock.quantity || 0);
    const warehouseUrl = serverBase() + '/unified-search?q=' + encodeURIComponent(info.upc);
    const prepUrl = serverBase() + '/item-prep?upc=' + encodeURIComponent(info.upc);
    const stockLine = [
      `<b>${esc(listable)}</b> to list`,
      gate.prepQty != null ? `<a class="prep ${gate.mismatch ? 'differs' : ''}" href="${esc(prepUrl)}" target="_blank" rel="noopener" title="Open Item Prep${gate.mismatch ? ' — prep counted ' + esc(gate.prepQty) + ', the rack holds ' + esc(rackQty) : ''}">prep ${esc(gate.prepQty)}${gate.mismatch ? ' <span class="neq">≠</span>' : ''}</a>` : '',
      `live eBay ${esc(gate.liveEbay ?? 0)}`,
      `Amazon ${esc(gate.liveAmazon ?? 0)}`,
    ].filter(Boolean).join(' · ');
    const bubble = p => (p.source === 'ai' ? 'ai' : p.source === 'prep' ? 'prep' : p.source === 'catalog' ? 'catalog' : '');
    const tile = p => `<div class="photo ${p.source} ${state.selectedPhotos.has(p.url) ? 'selected' : ''}" data-url="${esc(p.url)}" draggable="true" title="${esc(p.name)} · drag onto the store page">
        <img src="${esc(p.url)}" alt="" loading="lazy" draggable="false"><input type="checkbox" data-select="${esc(p.url)}" ${state.selectedPhotos.has(p.url) ? 'checked' : ''}>
        ${bubble(p) ? `<span class="tag ${bubble(p)}">${bubble(p)}</span>` : ''}<span class="tag small" hidden>too small</span></div>`;
    const noteRow = (icon, lt, en, extra = '') => `<div class="note2"><span class="ico" title="${icon === '🎤' ? 'Voice note' : 'Written note'}">${icon}</span>
        <div class="lt">${lt ? esc(lt) : '<span class="muted">—</span>'}</div><div class="en">${en ? esc(en) : '<span class="muted">—</span>'}</div>${extra}</div>`;
    el.innerHTML = `
      <div class="head">${(photos[0] || {}).url || item.thumb ? `<img src="${esc((photos[0] || {}).url || item.thumb)}" alt="">` : '<div class="noimg"></div>'}
        <div><div class="name">${esc(v.title || item.title || item.upc)}</div>
          <div class="muted small">${esc(info.upc)}${info.suffixed ? ` · <b>unit ${esc(info.upc.split('-')[1])}</b>` : ''}${info.cost != null ? ' · cost $' + money(info.cost) : ''}${v.price != null ? ' · price $' + esc(money(v.price)) : ''} · qty ${esc(v.quantity ?? '?')}</div>
          ${prep.status ? `<div class="chips"><span class="chip ${prep.status === 'good' ? 'ok' : (prep.status === 'bad' ? 'bad' : 'warn')}" title="${esc(prep.reason || '')}">status: ${esc(prep.status.toUpperCase())}</span>${prep.reason ? `<span class="muted small">${esc(prep.reason)}</span>` : ''}</div>` : ''}
        </div></div>
      <div class="tiles">
        <div class="tile stock ${rackQty ? 'ok' : 'bad'} ${gate.mismatch ? 'mismatch' : ''}"><div class="k">Warehouse stock</div>
          <div class="v"><a href="${esc(warehouseUrl)}" target="_blank" rel="noopener" title="Open the warehouse search">${esc(rackQty)} on the rack</a>${stock.positions?.length ? ` <span class="pos">@ ${esc(stock.positions.join(', '))}</span>` : ''}</div>
          <div class="s">${stockLine}</div></div>
        ${storeTile('ebay')}${storeTile('amazon')}
      </div>
      ${existing.length && !linkedHere ? `<div class="flag warn">Already on ${storeName(state.platform)}: ${existing.map(x => esc(x.listingId || x.asin || x.sku) + (x.state ? ' (' + esc(x.state) + ')' : '')).join(', ')}.
        <button id="markExisting" class="mini" type="button">Use that listing</button> ${item.storeUrl ? `<a href="${esc(item.storeUrl)}" target="_blank" rel="noopener">open</a>` : ''}</div>` : ''}

      <section class="sticky">
      <h3>Notes <span class="grow"></span>${notes.length || voice.length || info.defect ? '<span class="muted">LT · EN</span>' : '<span class="muted">none</span>'}</h3>
      ${voice.map(n => noteRow('🎤', n.lithuanian, n.english, `<div class="acts">
          ${n.status === 'complete' || n.english ? '' : (state.voiceBusy.has(info.upc + ':' + n.id) || n.status === 'processing' ? '<span class="spin"></span>' : `<button class="mini" data-transcribe="${n.id}" type="button" title="Transcribe and translate">text</button>`)}
          <button class="mini" data-play="${esc(n.url)}" type="button" title="Play the recording">▶</button>
          ${n.english ? `<button class="mini" data-usenote="${n.id}" type="button" title="Add to the condition note on the listing">→ listing</button>` : ''}
          ${n.error && !n.english ? `<span class="chip bad" title="${esc(n.error)}">failed</span>` : ''}</div>`)).join('')}
      ${info.defect ? noteRow('📦', '', 'BOL reason: ' + info.defect) : ''}
      ${notes.map(n => { const h = noteHalves(n); return noteRow('📝', h.lt, h.en, `<div class="acts muted small">${esc((n.createdAt || '').slice(0, 10))}</div>`); }).join('')}
      <audio id="notePlayer" preload="none" hidden></audio>
      </section>

      <h3>Photos <span class="muted">(${photos.length})</span><span class="grow"></span><button id="addPhoto" class="mini" type="button">${state.qrOpen ? 'Hide QR' : '+ Photo (QR)'}</button><button id="photoSelectAll" class="mini" type="button">${state.selectedPhotos.size ? 'Clear' : 'Select ours'}</button><button id="photoRefresh" class="mini" type="button" title="Reload photos (after taking new ones on the phone)">↻</button></h3>
      ${state.qrOpen ? `<div class="qr"><img src="${esc(serverBase() + '/api/lister/qr?text=' + encodeURIComponent(info.mobilePhotosUrl))}" alt="QR code for the phone photo page"><div class="small">Scan with the phone to add photos of this unit, then press ↻.<br><a href="${esc(info.mobilePhotosUrl)}" target="_blank" rel="noopener">open the page</a></div></div>` : ''}
      ${photos.length ? `<div class="photos">${photos.map(tile).join('')}</div>` : '<div class="muted small">No photos yet. Use + Photo (QR) to add some from the phone.</div>'}
      <div class="row tight">
        <button id="sendPhotos" class="act send" type="button" ${store && !state.busy ? '' : 'disabled'} title="Puts the ticked photos (or all of ours) into the page's photo uploader">${state.busy === 'photos' ? '<span class="spin"></span> Sending…' : '⬆ Send to page'}</button>
        <button id="aiPhotos" class="act ai" type="button" ${state.aiBusy || !photos.length ? 'disabled' : ''}>${state.aiBusy ? '<span class="spin"></span> ' + esc(state.aiBusy) : '✨ AI photoshop'}</button>
        <label class="check small"><input id="autoAiPhotos" type="checkbox" ${state.settings.autoAiPhotos ? 'checked' : ''}> auto AI on every photo</label>
        <label class="check small"><input id="autoSendPhotosDetail" type="checkbox" ${state.settings.autoSendPhotos ? 'checked' : ''}> auto send to page</label>
      </div>
      <details ${state.aiPrompt ? 'open' : ''}><summary>AI photoshop prompt</summary><textarea id="aiPrompt" rows="3">${esc(state.aiPrompt || info.aiPhotoPrompt || '')}</textarea><button id="aiPromptReset" class="mini" type="button">Reset to default</button></details>
      ${aspects.length ? `<details><summary>Item specifics (${aspects.length})</summary><div class="chips">${aspects.map(([k, vals]) => `<code title="click to copy" data-copy="${esc(vals[0])}">${esc(k)}: ${esc(vals.join(' / '))}</code>`).join('')}</div></details>` : ''}
      `;

    if ($('markExisting')) $('markExisting').onclick = () => markExisting(item);
    for (const button of el.querySelectorAll('button[data-transcribe]')) button.onclick = () => transcribe(info.upc, Number(button.dataset.transcribe), Boolean(button.dataset.again));
    for (const button of el.querySelectorAll('button[data-play]')) button.onclick = () => {
      const player = $('notePlayer');
      if (player.src === button.dataset.play && !player.paused) { player.pause(); button.textContent = '▶'; return; }
      for (const b of el.querySelectorAll('button[data-play]')) b.textContent = '▶';
      player.src = button.dataset.play; void player.play().catch(() => toast('Could not play the recording', true)); button.textContent = '⏸';
      player.onended = () => { button.textContent = '▶'; };
    };
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
      const img = tileEl.querySelector('img');
      const flagSize = () => { if (img.naturalWidth && (img.naturalWidth < 500 || img.naturalHeight < 500)) tileEl.querySelector('.tag.small').hidden = false; };
      if (img.complete) flagSize(); else img.onload = flagSize;
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
    $('autoSendPhotosDetail').onchange = async () => { await saveSettings({ ...state.settings, autoSendPhotos: $('autoSendPhotosDetail').checked }); renderPage(); if ($('autoSendPhotosDetail').checked) void maybeAutoSend(); };
    $('autoAiPhotos').onchange = async () => { await saveSettings({ ...state.settings, autoAiPhotos: $('autoAiPhotos').checked }); renderPage(); toast($('autoAiPhotos').checked ? 'Every photo of ours will get an AI version automatically' : 'Automatic AI photoshop off'); if ($('autoAiPhotos').checked) void maybeAutoPhotos(info); };
    $('aiPromptReset').onclick = () => { $('aiPrompt').value = info.aiPhotoPrompt || ''; state.aiPrompt = ''; remember(); };
    $('addPhoto').onclick = () => { state.qrOpen = !state.qrOpen; renderDetail(); };
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
    for (const [id, value] of [['statusAll', 'all'], ['statusGood', 'good'], ['statusBad', 'bad'], ['statusListed', 'listed']]) $(id).onclick = () => { state.statusFilter = value; renderItems(); };
    $('reload').onclick = () => { state.details = {}; void connect().then(() => loadQueue()); void refreshTab(); };
    chrome.tabs.onActivated.addListener(scheduleRefresh);
    chrome.tabs.onUpdated.addListener((tabId, info, tab) => {
      if (info.status === 'loading' && !info.url) {
        for (const set of [state.autoFilled, state.autoText]) for (const key of [...set]) if (key.startsWith(tabId + '|')) set.delete(key);
        if (state.tab?.id === tabId) state.guide = null;
      }
      if (tab?.active && (info.status === 'complete' || info.url)) scheduleRefresh();
    });
    chrome.windows?.onFocusChanged?.addListener(() => scheduleRefresh());
    chrome.tabs.onRemoved.addListener(tabId => { delete state.tabItems[tabId]; try { void chrome.storage.session?.set({ ssListerTabs: state.tabItems }); } catch { /* ignore */ } });
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
