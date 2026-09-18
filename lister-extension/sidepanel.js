// Sweet Shelves Lister side panel. Talks to the Sweet Shelves server with the browser's
// Cloudflare sign-in and to the store page through the injected page script.
//
// Flow: the Listing Agent queue (from /items-to-list) is shown per store (eBay / Amazon). The
// oldest item is picked automatically. Double-clicking an item opens the store's search page and
// types the UPC; the listing form is filled from the prepared proposal plus prep notes; fields
// that still need a value are highlighted; when the store confirms the listing it is recorded
// (with Undo) and the panel moves to the next item.
(() => {
  'use strict';
  const M = globalThis.SSListerMatcher;
  const SETTINGS_KEY = 'ssListerSettings';
  // QR glyph for the "+ Photo (QR)" button, so the phone-camera route is the obvious one.
  const QR_ICON = '<svg class="btn-ico" viewBox="0 0 16 16" aria-hidden="true"><path fill="currentColor" d="M1 1h6v6H1V1zm2 2v2h2V3H3zM9 1h6v6H9V1zm2 2v2h2V3h-2zM1 9h6v6H1V9zm2 2v2h2v-2H3zm6-2h2v2H9V9zm4 0h2v2h-2V9zm-4 4h2v2H9v-2zm2-2h2v2h-2v-2zm2 2h2v2h-2v-2z"/></svg>';
  const LEARNED_KEY = 'ssListerLearned';
  const CURRENT_KEY = 'ssListerCurrent2';
  const PLATFORM_KEY = 'ssListerPlatform';
  const PROMPT_KEY = 'ssListerAiPrompt';
  const TITLE_PROMPT_KEY = 'ssListerTitlePrompt';
  const NEW_KEY = 'ssListerNewDraft';
  // A listing walked away from half done: the store form it was on, kept so it can be clicked open again.
  const SESSIONS_KEY = 'ssListerSessions';
  const SESSION_MAX = 6, SESSION_TTL = 7 * 24 * 60 * 60 * 1000;
  const DEFAULTS = { server: 'https://pi.nexuscentralhq.org', actor: '', autoFill: true, autoGuide: true, autoLink: true, autoPrepare: true, autoAiTitle: false, autoAiDescription: false, autoAiPhotos: false, autoSendPhotos: false, autoSendAiOnly: true, togglesOpen: false, photoTogglesOpen: false, theme: 'light' };
  // The automatic switches, shown in the folding menu on the store card (all of them) and by the photos (the photo ones).
  const AUTO_TOGGLES = [
    { key: 'autoAiTitle', group: 'Listing text', icon: '✍️', label: 'AI title', short: 'AI title', hint: 'Written into the form as the page loads' },
    { key: 'autoAiDescription', group: 'Listing text', icon: '📄', label: 'AI description', short: 'AI description', hint: 'Shop template, written as the page loads' },
    { key: 'autoAiPhotos', group: 'Photos', icon: '✨', label: 'AI photoshop every photo', short: 'AI photos', hint: 'Each of our photos gets a cleaned-up AI version' },
    { key: 'autoSendPhotos', group: 'Photos', icon: '⬅', label: 'Send photos to the page', short: 'send photos', hint: 'As the listing form loads · too-small photos are never sent' },
    { key: 'autoSendAiOnly', group: 'Photos', icon: '🎯', label: 'By default only send AI generated', short: 'AI only', hint: 'Only photos run through AI photoshop (auto send and Send to page with nothing ticked)', sub: true },
  ];
  const MIN_PHOTO_SIDE = 500;
  const CONDITIONS = ['NEW', 'NEW_OTHER', 'NEW_WITH_DEFECTS', 'USED_EXCELLENT', 'USED_VERY_GOOD', 'USED_GOOD', 'USED_ACCEPTABLE', 'FOR_PARTS_OR_NOT_WORKING'];
  const VALUE_LABELS = {
    title: 'Title', price: 'Price', quantity: 'Quantity', sku: 'SKU / custom label', upc: 'UPC', asin: 'ASIN',
    brand: 'Brand', conditionDescription: 'Condition description', description: 'Description', condition: 'Condition',
  };
  const START_URLS = {
    ebay: () => 'https://www.ebay.com/sl/prelist/suggest?sr=wn',
    // Seller Central's product search is the first step: it takes the UPC and its Next button opens
    // the catalog result. /abis/listing/syh resumes the last draft (/interactive/listing/workflow/offer).
    amazon: () => 'https://sellercentral.amazon.com/product-search',
  };

  const state = {
    settings: { ...DEFAULTS }, connected: null, user: '', serverVersion: '', signIn: false,
    platform: 'ebay', view: 'list', filter: '', items: [], counts: {}, currentUpc: null, details: {}, edits: {},
    tab: null, page: null, report: null, pick: null, learned: {}, busy: '', lastLink: null,
    autoFilled: new Set(), searched: new Set(), autoLinked: new Set(), fillSessions: {}, pendingSearch: null,
    guide: null, selectedPhotos: new Set(), usedPhotos: new Set(), photoFiles: {}, aiPrompt: '', promptDraft: null, titlePrompt: '', savedTitlePrompt: '', aiBusy: '', prepareTimers: {}, prepareAsked: new Set(),
    voiceBusy: new Set(), qrOpen: false, advOpen: false, gearOpen: false, photoLinkBusy: false, toastAction: null, autoText: new Set(), autoPhotos: new Set(),
    busyTasks: new Map(), busyStarted: new Map(), autoPhotosTold: new Set(), checkingAmazon: new Set(), amazonRunning: false, tabItems: {}, sessions: [], lastForm: null, autoSent: new Set(), photoSizes: {}, statusFilter: 'all', aiGenerated: {},
    preload: { items: {}, all: null, watch: new Set(), asked: new Set(), timer: null, startedHere: false },
    // "+ NEW": the draft the phone is filling from the other end.
    newItem: { id: null, draft: null, barcodeDraft: '', timer: null, opening: false, linking: null, linkBusy: false, printed: {} },
  };

  const $ = id => document.getElementById(id);
  const esc = value => String(value ?? '').replace(/[&<>"']/g, ch => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[ch]));
  const money = value => (value == null || value === '' ? '' : Number(value).toFixed(2));
  const storeName = p => (p === 'amazon' ? 'Amazon' : 'eBay');
  // Write markup only when it really changed. An identical re-render would otherwise throw the
  // nodes away underneath: pictures reload, a caret in a box is lost, and the panel flickers.
  const written = new WeakMap();
  function setHtml(el, html) {
    if (written.get(el) === html) return false;
    written.set(el, html);
    el.innerHTML = html;
    return true;
  }

  class SignInError extends Error { constructor() { super('Sign in to Sweet Shelves'); this.signIn = true; } }

  function toast(message, bad = false, action = null) {
    const el = $('toast');
    el.textContent = message || '';
    const wrap = $('toastWrap');
    wrap.hidden = !message;
    wrap.className = 'toastwrap' + (bad ? ' bad' : '');
    const button = $('toastAction');
    state.toastAction = action;
    button.hidden = !action;
    if (action) { button.textContent = action.label; button.onclick = () => { button.hidden = true; state.toastAction = null; void action.run(); }; }
    if (message) setTimeout(() => { if (el.textContent === message) { el.textContent = ''; $('toastWrap').hidden = true; if (state.toastAction === action) { button.hidden = true; state.toastAction = null; } } }, action ? 15000 : 6000);
  }

  // Something is running: show the moving bar with what it is until every task released it.
  function busy(label) {
    const id = Symbol(label);
    state.busyTasks.set(id, label);
    state.busyStarted.set(id, Date.now());
    renderBusy();
    const release = () => { state.busyTasks.delete(id); state.busyStarted.delete(id); renderBusy(); };
    release.update = next => { if (state.busyTasks.has(id)) { state.busyTasks.set(id, next); renderBusy(); } };
    return release;
  }

  // The bar's row is always in the layout and only its ink fades, so nothing below it ever moves.
  // It waits before lighting up, so a quick request does not flash, and once lit it stays lit for a
  // moment, so a run of short requests cannot strobe.
  const BUSY_DELAY = 200, BUSY_MIN = 450;
  let busyWant = false, busyLit = false, busyLitAt = 0, busyTimer = null;
  function showBusy(want) {
    if (want === busyWant) return;
    busyWant = want;
    if (busyTimer) { clearTimeout(busyTimer); busyTimer = null; }
    if (want === busyLit) return;
    const light = () => { busyLit = want; busyLitAt = Date.now(); busyTimer = null; $('busy').classList.toggle('on', want); };
    const wait = want ? BUSY_DELAY : Math.max(0, BUSY_MIN - (Date.now() - busyLitAt));
    if (wait) busyTimer = setTimeout(light, wait); else light();
  }

  function renderBusy() {
    const el = $('busy');
    if (!el) return;
    // Safety net: nothing legitimately runs longer than ten minutes; drop anything older so the bar cannot stick.
    for (const [id, started] of state.busyStarted) if (Date.now() - started > 10 * 60 * 1000) { state.busyTasks.delete(id); state.busyStarted.delete(id); }
    const labels = [...state.busyTasks.values()];
    el.hidden = false;
    showBusy(Boolean(labels.length));
    if (labels.length) $('busyLabel').textContent = labels[labels.length - 1];
  }

  // Each store tab keeps its own item, so an unfinished eBay listing is untouched while another tab lists on Amazon.
  function bindTab(upc, platform, tabId = state.tab?.id) {
    if (!tabId || !upc) return;
    state.tabItems[tabId] = { upc, platform };
    try { void chrome.storage.session?.set({ ssListerTabs: state.tabItems }); } catch { /* session storage unavailable */ }
  }

  // -- storage ------------------------------------------------------------------------

  async function loadStorage() {
    const stored = await chrome.storage.local.get([SETTINGS_KEY, LEARNED_KEY, CURRENT_KEY, PLATFORM_KEY, PROMPT_KEY, TITLE_PROMPT_KEY, NEW_KEY, SESSIONS_KEY]);
    state.settings = { ...DEFAULTS, ...(stored[SETTINGS_KEY] || {}) };
    state.learned = stored[LEARNED_KEY] || {};
    state.currentUpc = stored[CURRENT_KEY] || null;
    state.platform = stored[PLATFORM_KEY] === 'amazon' ? 'amazon' : 'ebay';
    state.aiPrompt = stored[PROMPT_KEY] || '';
    state.newItem.id = stored[NEW_KEY] || null;
    state.sessions = freshSessions(stored[SESSIONS_KEY] || []);
    // The saved title prompt is the starting point of every session; editing it only changes this
    // session until "Save for all sessions" writes it back.
    state.savedTitlePrompt = stored[TITLE_PROMPT_KEY] || '';
    state.titlePrompt = state.savedTitlePrompt;
    try { state.tabItems = (await chrome.storage.session?.get('ssListerTabs'))?.ssListerTabs || {}; } catch { state.tabItems = {}; }
  }

  async function saveSettings(settings) {
    state.settings = { ...DEFAULTS, ...settings };
    await chrome.storage.local.set({ [SETTINGS_KEY]: state.settings });
    applyTheme();
  }

  // Day theme by default; the moon/sun button in the header switches to night and back.
  function applyTheme() {
    const dark = state.settings.theme === 'dark';
    document.documentElement.dataset.theme = dark ? 'dark' : 'light';
    const button = $('themeBtn');
    if (button) { button.textContent = dark ? '☀' : '🌙'; button.title = dark ? 'Switch to the day theme' : 'Switch to the night theme'; }
  }

  async function saveLearned() {
    await chrome.storage.local.set({ [LEARNED_KEY]: state.learned });
  }

  function remember() {
    void chrome.storage.local.set({ [CURRENT_KEY]: state.currentUpc, [PLATFORM_KEY]: state.platform, [PROMPT_KEY]: state.aiPrompt,
      [NEW_KEY]: state.newItem.id });
  }

  // -- unfinished listings -------------------------------------------------------------------
  // Walking away from a half-filled store form used to cost the whole way back: find the item,
  // search the UPC, step through the category and the match again. The form that was left is kept
  // instead - per item and store, for a week - and one click on it puts you back where you were.

  const sessionKey = s => s.upc + '|' + s.platform;
  const freshSessions = list => (list || [])
    .filter(s => s && s.upc && s.url && s.platform && Date.now() - (s.at || 0) < SESSION_TTL)
    .slice(0, SESSION_MAX);

  function saveSessions() {
    state.sessions = freshSessions(state.sessions);
    void chrome.storage.local.set({ [SESSIONS_KEY]: state.sessions });
    renderSessions();
  }

  // Standing on the form: remember the spot, and stop offering a way back to the page we are on.
  function noteForm(item, platform, page) {
    dropSession(item.upc, platform);
    state.lastForm = { upc: item.upc, platform, title: item.title || '', url: (page.url || '').split('#')[0],
      kind: page.kind || '', tabId: state.tab?.id || 0, at: Date.now() };
  }

  function keepSession(session) {
    if (!session?.url) return;
    const kept = { ...session, at: Date.now() };
    state.sessions = [kept, ...state.sessions.filter(s => sessionKey(s) !== sessionKey(kept))];
    state.lastForm = null;
    saveSessions();
  }

  // Listed, removed from the list, or back on the form: there is nothing to come back to.
  function dropSession(upc, platform) {
    const gone = s => s.upc === upc && (!platform || s.platform === platform);
    const kept = state.sessions.filter(s => !gone(s));
    if (state.lastForm && gone(state.lastForm)) state.lastForm = null;
    if (kept.length === state.sessions.length) return;
    state.sessions = kept;
    saveSessions();
  }

  async function resumeSession(session) {
    const s = state.sessions.find(x => sessionKey(x) === sessionKey(session)) || session;
    if (state.platform !== s.platform) { state.platform = s.platform; state.report = null; state.guide = null; remember(); await loadQueue({ keep: true }); }
    state.currentUpc = s.upc; state.view = 'item'; state.report = null; state.guide = null; remember();
    void loadDetail(s.upc);
    // The tab the form was left in: still on it, just come forward; wandered off somewhere else on
    // the same store, go back there; anywhere else (or closed), a new tab, so nothing is thrown away.
    let tab = null;
    if (s.tabId) { try { tab = await chrome.tabs.get(s.tabId); } catch { tab = null; } }
    const url = tab ? (tab.url || '').split('#')[0] : '';
    let tabId = 0;
    if (tab && (url === s.url || M.detectPage(url).store === s.platform)) {
      tabId = tab.id;
      await chrome.tabs.update(tab.id, { active: true, ...(url === s.url ? {} : { url: s.url }) });
      try { await chrome.windows?.update(tab.windowId, { focused: true }); } catch { /* window gone */ }
    } else {
      const created = await chrome.tabs.create({ url: s.url, active: true });
      tabId = created?.id || 0;
    }
    if (tabId) { s.tabId = tabId; forgetTabWork(tabId); bindTab(s.upc, s.platform, tabId); }
    saveSessions();
    renderAll();
    scheduleRefresh();
  }

  // Putting a form back on screen is a fresh start for that tab: the panel filled it once and
  // marked it done, and without forgetting that it would leave the reopened form untouched.
  function forgetTabWork(tabId) {
    for (const set of [state.autoFilled, state.autoText, state.autoSent, state.searched, state.autoLinked, state.autoPhotos])
      for (const key of [...set]) if (typeof key === 'string' && key.startsWith(tabId + '|')) set.delete(key);
    delete state.fillSessions[tabId];
    if (state.tab?.id === tabId) state.guide = null;
  }

  // "4 min ago" - how cold the unfinished listing is, in the fewest words.
  function ago(at) {
    const mins = Math.max(0, Math.round((Date.now() - (at || 0)) / 60000));
    if (mins < 1) return 'just now';
    if (mins < 60) return mins + ' min ago';
    const hours = Math.round(mins / 60);
    if (hours < 24) return hours + (hours === 1 ? ' hour ago' : ' hours ago');
    const days = Math.round(hours / 24);
    return days === 1 ? 'yesterday' : days + ' days ago';
  }

  // -- server -------------------------------------------------------------------------

  function serverBase() {
    return String(state.settings.server || DEFAULTS.server).replace(/\/+$/, '');
  }

  // -- location preview ---------------------------------------------------------------
  // The warehouse pages open a shelf photo for a rack code (static/location-preview.js on the
  // server). The panel cannot load that script (MV3 forbids remote code), so it asks the same
  // routes here with absolute URLs and keeps the first image that loads.
  const locPreview = (() => {
    const mapKey = code => {
      const base = String(code || '').replace(/\s+/g, '').toLowerCase().replace(/b\d+$/, '');
      if (!base || /^ofloor\d+$/.test(base)) return base;
      const shelf = base.match(/^(.*)s\d+$/);
      return shelf ? shelf[1] : base;
    };
    const shelfUrls = code => {
      const compact = String(code || '').replace(/\s+/g, '').toLowerCase();
      if (!compact) return [];
      const nobin = compact.replace(/b\d+$/, '');
      const codes = nobin === compact ? [compact] : [compact, nobin];
      // Garage and office codes have a marked "base" photo; everything else only has the shelf pic.
      const routes = /^(gr|gmid|gfloor|misc|or|omr|ofloor)/.test(mapKey(compact))
        ? ['/shelf-base/', '/shelf-image/', '/shelf-original/']
        : ['/shelf-image/', '/shelf-original/'];
      const urls = [];
      for (const route of routes) for (const one of codes) urls.push(serverBase() + route + encodeURIComponent(one) + '.png');
      return urls;
    };
    const mapUrls = code => {
      const key = mapKey(code);
      if (!key) return [];
      const office = ['/static/shelves/office/maps/officemap_', '/static/shelves/maps/office/officemap_'];
      const garage = ['/static/shelves/garage/maps/garagemap_', '/static/shelves/maps/garage/garagemap_'];
      const order = /^(gr|gmid|gfloor|misc)/.test(key) ? garage.concat(office) : office.concat(garage);
      return order.map(prefix => serverBase() + prefix + encodeURIComponent(key) + '.png');
    };
    const firstThatLoads = urls => new Promise(resolve => {
      let index = 0;
      const next = () => {
        if (index >= urls.length) return resolve(null);
        const src = urls[index++] + '?t=' + Date.now();
        const img = new Image();
        img.onload = () => resolve(src);
        img.onerror = next;
        img.src = src;
      };
      next();
    });
    let box = null;
    const ensure = () => {
      if (box) return box;
      box = document.createElement('div');
      box.className = 'locpv';
      box.hidden = true;
      box.innerHTML = '<div class="sheet"><div class="bar"><b class="code"></b><button class="close" type="button" aria-label="Close">✕</button></div><div class="pics"></div></div>';
      box.addEventListener('click', event => { if (event.target === box || event.target.closest('.close')) close(); });
      document.body.appendChild(box);
      document.addEventListener('keydown', event => { if (event.key === 'Escape' && !box.hidden) close(); });
      return box;
    };
    const close = () => { if (box) { box.hidden = true; box.querySelector('.pics').innerHTML = ''; } };
    const open = async code => {
      const el = ensure();
      const pics = el.querySelector('.pics');
      el.querySelector('.code').textContent = code;
      pics.innerHTML = '<div class="muted small"><span class="spin"></span> Looking for the shelf photo…</div>';
      el.hidden = false;
      const token = code + ':' + Date.now();
      el.dataset.token = token;
      const [shelf, map] = await Promise.all([firstThatLoads(shelfUrls(code)), firstThatLoads(mapUrls(code))]);
      if (el.dataset.token !== token || el.hidden) return;
      const shot = (src, label) => `<figure><img src="${esc(src)}" alt="${esc(label + ' for ' + code)}"><figcaption>${esc(label)}</figcaption></figure>`;
      pics.innerHTML = [shelf ? shot(shelf, 'Shelf photo') : '', map ? shot(map, 'Position map') : ''].filter(Boolean).join('')
        || `<div class="muted small">No picture stored for <b>${esc(code)}</b>.</div>`;
    };
    return { open, close };
  })();

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
      for (const it of state.items) if (it.preload?.running) { state.preload.items[it.upc] = it.preload; state.preload.watch.add(it.upc); }
      if (state.preload.watch.size) pollPreload();
      renderPreloadBar();
      state.counts[state.platform] = data.counts || {};
      state.connected = true; state.signIn = false;
      // An item that is no longer waiting on this store has no unfinished listing to go back to.
      const waiting = new Set(state.items.filter(it => it.status === 'queued').map(it => it.upc));
      for (const s of [...state.sessions]) if (s.platform === state.platform && !waiting.has(s.upc)) dropSession(s.upc, s.platform);
      const active = state.items.filter(it => it.status === 'queued');
      const exists = state.items.some(it => it.upc === state.currentUpc);
      if (!keep || !exists || !state.currentUpc) state.currentUpc = active.length ? active[0].upc : (state.items[0]?.upc || null);
      remember();
      // The other store's count, cheaply, so the toggle shows both.
      void api('/api/lister/queue?platform=' + (state.platform === 'ebay' ? 'amazon' : 'ebay')).then(d => {
        state.counts[state.platform === 'ebay' ? 'amazon' : 'ebay'] = d.counts || {};
        rememberThumbs(d.items);
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
    void loadListed();
  }

  // -- "✓ Listed" side bar ------------------------------------------------------------------
  // Listings the Lister took from start to end on the store (listing_links, kind 'listed'), newest first,
  // filtered by day range and store. Rows for a listing the store already carried ("Use that listing",
  // kind 'existing') are not the panel's own work: they are only counted as links in the summary.

  const isOwnListing = link => (link.kind || 'listed') !== 'existing';

  function rememberThumbs(items) {
    state.thumbs ||= {};
    for (const it of items || []) if (it.thumb) state.thumbs[it.upc] = it.thumb;
  }

  function localDay(date) {
    const d = date instanceof Date ? date : new Date(date);
    return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
  }

  function daysAgo(n) {
    const d = new Date(); d.setHours(0, 0, 0, 0); d.setDate(d.getDate() - n);
    return localDay(d);
  }

  function listedInRange(link, range) {
    const day = String(link.created_at || '').slice(0, 10);  // the server stamps local time
    if (range === 'today') return day === daysAgo(0);
    if (range === 'yesterday') return day === daysAgo(1);
    if (range === '7' || range === '30') return day >= daysAgo(Number(range) - 1);
    return true;
  }

  async function loadListed() {
    if (state.connected === false || state.listedLoading) return;
    state.listedLoading = true;
    try {
      const data = await api('/api/lister/links?limit=500');
      state.listed = data.links || [];
    } catch (error) {
      if ($('listedDrawer') && !$('listedDrawer').hidden) toast(error.message, true);
    } finally {
      state.listedLoading = false;
    }
    renderListed();
  }

  function renderListed() {
    const all = (state.listed || []).filter(isOwnListing);
    const linked = (state.listed || []).filter(l => !isOwnListing(l));
    const today = all.filter(l => listedInRange(l, 'today')).length;
    if ($('countListedToday')) $('countListedToday').textContent = String(today || 0);
    const drawer = $('listedDrawer');
    if (!drawer || drawer.hidden) return;
    const range = state.listedRange || 'today';
    const store = state.listedStore || 'all';
    for (const b of drawer.querySelectorAll('[data-range]')) b.classList.toggle('active', b.dataset.range === range);
    for (const b of drawer.querySelectorAll('[data-store]')) b.classList.toggle('active', b.dataset.store === store);
    const inRange = all.filter(l => listedInRange(l, range));
    const rows = inRange.filter(l => store === 'all' || l.platform === store);
    const byStore = p => inRange.filter(l => l.platform === p).length;
    const people = {};
    for (const l of rows) { const who = l.created_by ? String(l.created_by).split('@')[0] : '—'; people[who] = (people[who] || 0) + 1; }
    const linkedHereCount = linked.filter(l => listedInRange(l, range) && (store === 'all' || l.platform === store)).length;
    $('listedSummary').innerHTML = `<b>${rows.length}</b> listed · <span class="chip store ebay">eBay ${byStore('ebay')}</span><span class="chip store amazon">Amazon ${byStore('amazon')}</span>`
      + (Object.keys(people).length ? ' · ' + Object.entries(people).sort((a, b) => b[1] - a[1]).map(([who, n]) => `${esc(who)} ${n}`).join(' · ') : '')
      + (linkedHereCount ? ` <span class="muted" title="Listings the store already carried, linked with “Use that listing” — not listed through the Lister">· ${linkedHereCount} linked</span>` : '');
    const list = $('listedList');
    if (!rows.length) {
      list.innerHTML = `<div class="empty">${state.listedLoading && !state.listed ? 'Loading…' : 'Nothing listed through the Lister ' + ({ today: 'today', yesterday: 'yesterday', 7: 'in the last 7 days', 30: 'in the last 30 days' }[range] || 'yet') + '.'}</div>`;
      return;
    }
    const time = stamp => String(stamp || '').slice(11, 16);
    let lastDay = '';
    list.innerHTML = rows.map(l => {
      const day = String(l.created_at || '').slice(0, 10);
      const head = range !== 'today' && range !== 'yesterday' && day !== lastDay ? `<div class="day">${esc(day === daysAgo(0) ? 'Today' : day === daysAgo(1) ? 'Yesterday' : day)}</div>` : '';
      lastDay = day;
      const thumb = (state.thumbs || {})[l.upc];
      const id = l.platform === 'ebay' ? (l.listing_id ? 'item ' + l.listing_id : '') : [l.asin, l.sku].filter(Boolean).join(' · ');
      const suffixed = String(l.upc || '').includes('-');
      const upcHtml = suffixed ? `${esc(l.upc.split('-')[0])}-<b class="suffix">${esc(l.upc.split('-').slice(1).join('-'))}</b>` : esc(l.upc);
      return `${head}<div class="litem" data-link="${esc(l.id)}" title="${esc(l.title || '')}">
          ${thumb ? `<img src="${esc(thumb)}" alt="" loading="lazy">` : '<div class="noimg"></div>'}
          <div><div class="title">${esc(l.title || '(no title)')}</div>
            <div class="meta"><span class="chip store ${esc(l.platform)}">${esc(storeName(l.platform))}</span><span>${upcHtml}</span>
              ${id ? `<span>${l.url ? `<a href="${esc(l.url)}" target="_blank" rel="noopener" title="Open the live listing">${esc(id)} ↗</a>` : esc(id)}</span>` : ''}
              ${l.price != null ? `<span>$${esc(Number(l.price).toFixed(2))}</span>` : ''}
              <span title="${esc(l.created_at)}">${esc(time(l.created_at))}</span>${l.created_by ? `<span>· ${esc(String(l.created_by).split('@')[0])}</span>` : ''}</div></div>
        </div>`;
    }).join('');
  }

  function openListed(open) {
    const drawer = $('listedDrawer');
    drawer.hidden = !open;
    $('listedBtn').classList.toggle('active', open);
    if (open) { renderListed(); void loadListed(); }
  }

  // Amazon answers a condition-less restriction check with a row per condition it knows, including
  // collectible_* / refurbished, which we never sell. The server now judges only our own conditions;
  // these turn its verdict into something readable on the row.
  const CONDITION_LABEL = {
    new_new: 'new', new_open_box: 'open box', used_like_new: 'like new',
    used_very_good: 'very good', used_good: 'good', used_acceptable: 'acceptable',
  };
  const conditionLabel = (c) => CONDITION_LABEL[c] || String(c || '').replace(/_/g, ' ');

  function amazonCheckWhy(ac) {
    if (!ac) return '';
    const parts = [];
    if (ac.asin) parts.push('ASIN ' + ac.asin);
    const blocked = (ac.blockedConditions || []).map(conditionLabel);
    const open = (ac.openConditions || []).map(conditionLabel);
    if (blocked.length) parts.push('blocked in: ' + blocked.join(', '));
    if (open.length && blocked.length) parts.push('open in: ' + open.join(', '));
    const reasons = (ac.reasons || []).filter(Boolean);
    if (reasons.length) parts.push([...new Set(reasons)].join(' · '));
    return parts.join(' — ');
  }

  // Can Amazon take each queued UPC from us? One check at a time; the row shows the result.
  async function runAmazonChecks() {
    if (state.amazonRunning) return;
    const pending = state.items.filter(it => it.status === 'queued' && !it.amazonCheck && !state.checkingAmazon.has(it.baseUpc));
    if (!pending.length) return;
    state.amazonRunning = true;
    const done = busy('Checking Amazon…');
    try {
      let n = 0;
      for (const it of pending) {
        n += 1;
        done.update(`Checking Amazon: "${(it.title || it.upc).slice(0, 40)}" (${n}/${pending.length})`);
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
    const before = state.details[upc] ? ownPhotoNames(state.details[upc]) : null;
    try {
      const data = await api('/api/lister/queue/' + encodeURIComponent(upc));
      const item = data.item;
      item.loadedAt = Date.now();
      state.details[upc] = item;
      if (item.fields?.generated) state.aiGenerated[upc] = { ...item.fields.generated, ...(state.aiGenerated[upc] || {}) };
      if (item.preload) state.preload.items[upc] = item.preload;
      if (upc === state.currentUpc) { renderDetail(); renderConfirm(); }
      void maybePrepare(item);
      void maybeTranscribe(item);
      const added = before ? (item.photos || []).filter(p => isOwnPhoto(p) && !before.has(p.name)) : [];
      if (added.length) void autoNewPhotos(item, added); else void maybeAutoPhotos(item);
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
    if (!force && preloadRunning(item.upc)) return;  // the preload builds it
    // Not from the queue list: only once the item is opened, or a listing for it is under way on a store page.
    const listing = state.page?.store && (ASSIST_KINDS.has(state.page.kind) || state.page.kind === 'listing-form' || state.page.kind === 'offer-form');
    if (!force && state.view !== 'item' && !listing) return;
    const key = item.upc;
    if (!force && state.prepareAsked.has(key)) return;
    state.prepareAsked.add(key);
    try {
      const result = await api('/api/lister/queue/' + encodeURIComponent(key) + '/prepare', { method: 'POST', body: { force } });
      if (result.status === 'ready') { await loadDetail(key, { force: true }); return; }
      if (result.status === 'blocked') {
        if (key === state.currentUpc) toast('Listing Agent gate blocks this item: ' + ((result.reasons || [])[0] || 'see the review page') + '. Basic values are used.', true);
        return;
      }
      state.details[key].preparing = { running: true };
      if (key === state.currentUpc) renderDetail();
      const release = busy(`Preparing "${(item.title || item.fields?.title || key).slice(0, 40)}": title, price, specifics, description…`);
      let tries = 0;
      clearTimeout(state.prepareTimers[key]);
      const poll = async () => {
        tries += 1;
        const fresh = await loadDetail(key, { force: true });
        if (!fresh) { if (tries < 40) state.prepareTimers[key] = setTimeout(poll, 4000); else release(); return; }
        const finished = !fresh.preparing?.running;
        if (finished && fresh.proposal?.ready) { release(); if (key === state.currentUpc) toast('Listing values prepared'); return; }
        if (finished && fresh.proposal?.status === 'blocked') { release(); if (key === state.currentUpc) toast('Listing Agent gate blocks this item: ' + ((fresh.proposal.flags || []).find(f => f.level === 'block')?.message || 'see the review page') + '. Basic values are used.', true); return; }
        if (fresh.preparing?.error) { release(); if (key === state.currentUpc) toast('Prepare failed: ' + fresh.preparing.error, true); return; }
        if (finished && tries > 2) { release(); return; }  // the job ended without a usable proposal
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

  // -- Preload: everything the automatic switches would do, run on the server ahead of time -------------
  const PRELOAD_LABEL = { prepare: 'values', title: 'AI title', description: 'AI description', photos: 'AI photos' };
  function preloadSteps() {
    const s = state.settings;
    return ['prepare', s.autoAiTitle && 'title', s.autoAiDescription && 'description', s.autoAiPhotos && 'photos'].filter(Boolean);
  }
  function preloadOf(upc) { return state.preload.items[upc] || state.items.find(it => it.upc === upc)?.preload || null; }
  function preloadRunning(upc) { const p = preloadOf(upc); return !!(p && p.running); }
  function preloadSummary(p) {
    const steps = Object.entries(p.steps || {});
    const running = steps.find(([, v]) => v === 'running');
    const failed = steps.filter(([, v]) => String(v).startsWith('error'));
    const photos = p.photos && p.photos.total ? ` ${p.photos.done}/${p.photos.total}` : '';
    const problem = (k, v) => PRELOAD_LABEL[k] + ': ' + String(v).replace(/^error:\s*/, '');
    return { steps, running, failed, photos, problem };
  }
  // One item, as soon as it is picked with an AI switch on (values alone are maybePrepare's job).
  async function preloadItem(upc, { force = false } = {}) {
    if (!upc || state.connected === false) return;
    const steps = preloadSteps();
    if (steps.length < 2 && !force) return;
    const key = upc + '|' + steps.join(',');
    if (!force && state.preload.asked.has(key)) return;
    state.preload.asked.add(key);
    if (preloadRunning(upc)) { state.preload.watch.add(upc); pollPreload(); return; }
    try {
      const data = await api('/api/lister/queue/' + encodeURIComponent(upc) + '/preload', { method: 'POST', body: { steps } });
      state.preload.items[upc] = data.preload;
      state.preload.watch.add(upc);
      renderItems(); pollPreload();
    } catch (error) {
      if (error.status !== 501) toast('Preload: ' + error.message, true);
    }
  }
  // The whole list, one item after another, with the bar in the list header.
  async function preloadAll() {
    const upcs = state.items.filter(it => it.status === 'queued').map(it => it.upc);
    if (!upcs.length) { toast('Nothing queued to preload'); return; }
    const steps = preloadSteps();
    if (steps.length < 2) toast('No AI switch is on, so only the listing values are prepared. Switch on AI title, description or photos to preload those as well.');
    try {
      const data = await api('/api/lister/preload', { method: 'POST', body: { upcs, steps } });
      state.preload.startedHere = true;
      applyPreloadAll(data);
      for (const upc of upcs) state.preload.asked.add(upc + '|' + steps.join(','));
      toast(`Preloading ${upcs.length} item${upcs.length === 1 ? '' : 's'} in the background`);
      renderItems(); renderPreloadBar(); pollPreload();
    } catch (error) {
      if (error.status !== 501) toast('Preload all: ' + error.message, true);
    }
  }
  function applyPreloadAll(data) {
    state.preload.all = { running: !!data.running, total: data.total || 0, done: data.done || 0, percent: data.percent || 0, upcs: data.upcs || [] };
    for (const [upc, p] of Object.entries(data.items || {})) {
      const before = state.preload.items[upc];
      state.preload.items[upc] = p;
      if (p.running || (before && before.running)) state.preload.watch.add(upc);
    }
  }
  function pollPreload() {
    clearTimeout(state.preload.timer);
    state.preload.timer = setTimeout(async () => {
      if (state.connected === false) return;
      try {
        const all = await api('/api/lister/preload');
        applyPreloadAll(all);
        for (const upc of [...state.preload.watch]) {
          if (!(all.items || {})[upc]) {
            const data = await api('/api/lister/queue/' + encodeURIComponent(upc) + '/preload');
            if (data.preload) state.preload.items[upc] = data.preload;
          }
          const p = state.preload.items[upc];
          if (!p || !p.running) { state.preload.watch.delete(upc); if (p) await preloadFinished(upc, p); }
        }
      } catch (error) { console.warn('preload poll', error); }
      renderItems(); renderPreloadBar();
      if (state.preload.all?.running || state.preload.watch.size) pollPreload();
    }, 3000);
  }
  async function preloadFinished(upc, p) {
    const { failed, problem } = preloadSummary(p);
    const row = state.items.find(it => it.upc === upc);
    if (row) row.preload = p;
    const fresh = state.details[upc] ? await loadDetail(upc, { force: true }) : null;
    if (upc !== state.currentUpc) return;
    if (failed.length) toast('Preload: ' + failed.map(([k, v]) => problem(k, v)).join(' · '), true);
    else toast('Preloaded: ' + Object.keys(p.steps || {}).map(k => PRELOAD_LABEL[k]).join(', '));
    if (fresh) { void maybeAutoText(); void maybeAutoSend(); }
  }
  function renderPreloadBar() {
    const bar = $('preloadBar'); const button = $('preloadAll');
    if (!bar || !button) return;
    const all = state.preload.all;
    const running = !!all?.running;
    button.disabled = running;
    button.textContent = running ? '⚡ Preloading…' : '⚡ Preload all';
    if (!all || (!running && !state.preload.startedHere)) { bar.hidden = true; bar.classList.remove('done'); return; }
    const failed = (all.upcs || []).filter(u => preloadSummary(state.preload.items[u] || {}).failed.length).length;
    bar.hidden = false;
    bar.classList.toggle('done', !running);
    // Built once, then only the numbers move: rebuilding it each poll made the bar twitch.
    setHtml(bar, '<div class="track"><i></i></div><span class="say"></span><button id="preloadHide" class="mini" type="button" title="Hide">✓</button>');
    bar.querySelector('.track i').style.width = all.percent + '%';
    setHtml(bar.querySelector('.say'), `${running ? `Preloading <b>${all.done}</b> of ${all.total} · <b>${all.percent}%</b>` : `All ${all.total} preloaded · <b>100%</b>`}${failed ? ` · <span class="bad">${failed} failed</span>` : ''}`);
    const hide = $('preloadHide');
    hide.hidden = running;
    hide.onclick = () => { state.preload.startedHere = false; renderPreloadBar(); };
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
    if (onForm && state.page.store && current()) noteForm(current(), state.page.store, state.page);
    if (onForm && current() && !state.locked) { state.locked = current().upc; state.view = 'item'; }
    else if (!onForm && state.locked) {
      // Out of the listing window (back to search, the success page, another site): show the queue again.
      // Unless the store says it is listed, the form we left is kept, one click away, on the queue.
      const left = state.lastForm && state.lastForm.upc === state.locked ? state.lastForm : null;
      state.locked = null;
      if (state.page.kind === 'listing-success' || state.page.kind === 'offer-success') state.lastForm = null;
      else { keepSession(left); state.view = 'list'; }
    }
    renderStoreBar(); renderPage(); renderDetail(); renderConfirm();
    if (detail()) void maybePrepare(detail());
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
      }, instructions: kind === 'title' ? state.titlePrompt : '' } });
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
    // Only on purpose: the user clicked or double-clicked an item. Opening the panel on the search page does nothing by itself.
    const wanted = force || state.pendingSearch?.upc === item.upc;
    if (!wanted) return;
    const key = tabKey('search|' + item.upc);
    // A click / double-click (pendingSearch) always searches, even a UPC this tab searched before.
    if (!force && !state.pendingSearch && state.searched.has(key)) return;
    // Booked before the await: submitting is asynchronous now, and the second half of a
    // double-click must not fire a second search (two navigations abort each other).
    state.searched.add(key);
    const result = await searchUpc({ auto: true });
    if (result?.ok) state.pendingSearch = null;  // used up: one click, one search
    else state.searched.delete(key);  // otherwise the next page update tries again
  }

  async function searchUpc({ auto = false } = {}) {
    const item = current();
    if (!item || !state.page?.store) { if (!auto) toast('Open the store\'s "what are you selling" page first', true); return; }
    try {
      const result = await pageMessage({ type: 'search', options: { query: item.baseUpc || item.upc, store: state.page.store } });
      state.pendingSearch = null;
      if (result?.ok) toast(`Searched ${item.baseUpc || item.upc} on ${storeName(state.page.store)}`);
      else toast(`Could not search on ${storeName(state.page.store)}${result?.reason ? ': ' + result.reason : ''}`, true);
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
      const result = await pageMessage({ type: 'fill', options: { values: values(info), store, aspects: info.fields.aspects || {}, learned, includeDescription: store === 'ebay', aiFields: aiFieldsFor(info) } });
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
        // What the rack really holds, so the overlay can pin it under the store's quantity box.
        const inv = info.inventory || {}, g = info.gate || {};
        const stock = { rack: g.rackQty ?? inv.quantity ?? null, listable: g.listable ?? inv.quantity ?? null, positions: inv.positions || [] };
        // Quantity and price are put in for the user, so the HUD still stops at them for a look.
        response = await pageMessage({ type: 'guide-start', options: { values: v, store: state.page.store, aspects: info.fields.aspects || {}, noteFields, stock, confirmFields: ['quantity', 'price'], aiFields: state.aiGenerated[info.upc] || {} } });
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
      if (message.ok && message.url) markPhotosUsed([message.url]);
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
      dropSession(upc, body.platform);
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
      dropSession(item.upc, platform);
      await loadQueue({ keep: state.currentUpc !== item.upc });
    } catch (error) {
      toast(error.message, true);
    }
  }

  // Clear all: every item still waiting on this store comes off its list, the same as pressing each
  // row's X. One Undo puts them all back.
  async function clearAll() {
    const platform = state.platform;
    const items = state.items.filter(it => it.status === 'queued');
    if (!items.length) return;
    const n = items.length;
    const ok = await confirmModal({ title: `Clear all ${n} item${n === 1 ? '' : 's'} from the ${storeName(platform)} list?`,
      text: `They stay on the ${storeName(platform === 'ebay' ? 'amazon' : 'ebay')} list. Items already done there leave the Listing Agent queue on Items to List.`, okLabel: 'Clear all' });
    if (!ok) return;
    const skip = (upc, undo) => api('/api/lister/queue/' + encodeURIComponent(upc) + '/skip', { method: 'POST', body: undo ? { platform, undo: true } : { platform } });
    const done = busy(`Clearing ${n} item${n === 1 ? '' : 's'}\u2026`);
    const cleared = [];
    try {
      for (let i = 0; i < items.length; i += 6) {
        const batch = items.slice(i, i + 6);
        const results = await Promise.allSettled(batch.map(it => skip(it.upc)));
        results.forEach((r, j) => { if (r.status === 'fulfilled') cleared.push(batch[j].upc); });
      }
    } finally {
      done();
    }
    for (const upc of cleared) { delete state.details[upc]; dropSession(upc, platform); }
    const missed = n - cleared.length;
    toast(`Cleared ${cleared.length} from ${storeName(platform)}` + (missed ? ` \u2014 ${missed} could not be removed` : ''), Boolean(missed),
      cleared.length ? { label: 'Undo', run: async () => { await Promise.allSettled(cleared.map(upc => skip(upc, true))); await loadQueue(); } } : null);
    await loadQueue({ keep: false });
  }

  // -- start listing on a store --------------------------------------------------------------------

  async function startOn(platform) {
    const item = current();
    if (!item) return;
    state.pendingSearch = { upc: item.upc, platform };
    const onStore = state.page?.store === platform && state.tab?.id;
    // Already on the store's first page: type the UPC and search right here (no reload).
    if (onStore && (state.page.kind === 'listing-start' || state.page.kind === 'product-search')) {
      bindTab(item.upc, platform);
      await maybeAutoSearch({ force: true });
      return;
    }
    const url = START_URLS[platform](item.baseUpc || item.upc);
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

  // A picture can fail to load on one machine and be fine on another: a store CDN that refuses the
  // request from a chrome-extension:// page, or our own server behind Cloudflare Access, which an
  // <img> cannot sign in to. When one fails it is fetched once more through the server - the same
  // signed-in route the drag-and-drop photos use - and shown as its own bytes.
  // A picture the store will not give us either way (Bloomingdale's answers 403 to us) ends as
  // a clear pixel, so the row shows its empty grey tile instead of a broken-image icon.
  const BLANK_PIC = 'data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7';
  const proxiedPics = new Map();
  function proxiedPic(url) {
    if (!proxiedPics.has(url))
      proxiedPics.set(url, photoFile(url).then(p => 'data:' + (p.type || 'image/jpeg') + ';base64,' + p.base64));
    return proxiedPics.get(url);
  }

  // The picture a row should show, remembered on the node so a re-render does not reload it and
  // does not undo a picture that had to come through the server.
  function showPic(img, url) {
    if (img.dataset.src === url) return;
    img.dataset.src = url;
    delete img.dataset.retried;
    img.src = url;
  }

  document.addEventListener('error', event => {
    const img = event.target;
    if (!img || img.tagName !== 'IMG' || img.dataset.retried) return;
    const url = img.dataset.src || img.getAttribute('src') || '';
    if (!/^https?:/i.test(url)) return;
    img.dataset.src = url;
    img.dataset.retried = '1';
    proxiedPic(url).then(src => { img.src = src; }).catch(() => { img.src = BLANK_PIC; });
  }, true);

  // Photos that already went into the store page (sent, or dragged in) are greyed out on the tiles.
  function markPhotosUsed(urls) {
    const fresh = (urls || []).filter(u => u && !state.usedPhotos.has(u));
    if (!fresh.length) return;
    for (const url of fresh) state.usedPhotos.add(url);
    renderDetail();
  }

  // A link that went to every chat because this login has not linked its own Telegram says how to
  // change that; once linked, the server sends only to that phone (routed: 'you').
  function everyoneHint(result) {
    return result?.routed === 'everyone' ? ' · link your own on the Telegram page to get these only on your phone' : '';
  }

  // "+ Photo link": the bot messages the phone the same camera page the QR code points at, so
  // the phone is one notification tap away from shooting instead of pointing a camera at the screen.
  async function sendPhotoLink(info) {
    if (!info?.upc || state.photoLinkBusy) return;
    state.photoLinkBusy = true;
    renderDetail();
    try {
      const result = await api('/api/lister/photo-link', { method: 'POST', body: { upc: info.upc } });
      const who = (result.sent || []).join(', ');
      toast('\uD83D\uDCF2 Camera link sent' + (who ? ' to ' + who : '') + ' on Telegram' + everyoneHint(result));
    } catch (error) {
      toast('Photo link: ' + error.message, true);
    } finally {
      state.photoLinkBusy = false;
      renderDetail();
    }
  }

  async function sendPhotos() {
    const info = detail();
    if (!info || !state.page?.store) { toast('Open the store listing form first', true); return; }
    const ticked = (info.photos || []).map(p => p.url).filter(u => state.selectedPhotos.has(u));
    const urls = ticked.length ? ticked : await defaultSendUrls(info);
    if (!urls.length) { toast(state.settings.autoSendAiOnly ? 'No AI photos yet — run AI photoshop or tick the photos to send' : 'No photos to send', true); return; }
    await sendPhotoUrls(info, urls);
  }

  // Width/height of a photo: remembered from the tiles, otherwise loaded once. Unknown (failed load) counts as big enough.
  function photoSize(url) {
    if (state.photoSizes[url]) return Promise.resolve(state.photoSizes[url]);
    return new Promise(resolve => {
      const img = new Image();
      const finish = size => { clearTimeout(timer); if (size) state.photoSizes[url] = size; resolve(size); };
      const timer = setTimeout(() => finish(null), 8000);
      img.onload = () => finish({ w: img.naturalWidth, h: img.naturalHeight });
      img.onerror = () => finish(null);
      img.src = url;
    });
  }

  function isTooSmall(size) {
    return Boolean(size && size.w && (size.w < MIN_PHOTO_SIDE || size.h < MIN_PHOTO_SIDE));
  }

  // What goes to the page when nothing is ticked: AI versions only (setting) or ours with AI preferred; never a too-small photo.
  async function defaultSendUrls(info, { catalogFallback = false } = {}) {
    const photos = info.photos || [];
    let urls = state.settings.autoSendAiOnly ? photos.filter(p => p.source === 'ai').map(p => p.url) : bestPhotoUrls(info);
    if (!urls.length && catalogFallback && !state.settings.autoSendAiOnly) urls = photos.filter(p => p.source === 'catalog').map(p => p.url);
    const keep = [];
    for (const url of urls) if (!isTooSmall(await photoSize(url))) keep.push(url);
    return keep.slice(0, 24);
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
    if (state.aiBusy) return;  // an AI run is in progress: maybeAutoPhotos sends when it finishes
    const key = (state.tab?.id || 0) + '|' + page.store + '|' + info.upc;
    if (state.autoSent.has(key)) return;
    state.autoSent.add(key);  // claimed before the size checks so a second trigger does not send twice
    const urls = await defaultSendUrls(info, { catalogFallback: true });
    if (!urls.length) {
      state.autoSent.delete(key);  // try again once AI photos (or new photos) exist
      if (!state.autoSendNoted?.has(key)) {
        (state.autoSendNoted ||= new Set()).add(key);
        toast(state.settings.autoSendAiOnly ? 'Auto send: waiting for AI photos (only AI generated is on)' : 'Auto send: no photos big enough to send yet', true);
      }
      return;
    }
    toast(`Auto send: ${urls.length} photo${urls.length === 1 ? '' : 's'} to the page…`);
    await sendPhotoUrls(info, urls);
  }

  async function sendPhotoUrls(info, urls) {
    state.busy = 'photos'; renderDetail();
    const done = busy('Sending photos to the page…');
    try {
      const files = [];
      const aiUrls = new Set((info?.photos || []).filter(p => p.source === 'ai').map(p => p.url));
      for (const url of urls) { const f = await photoFile(url); files.push({ name: f.name, type: f.type, base64: f.base64, ai: aiUrls.has(url) }); }
      const result = await pageMessage({ type: 'add-photos', options: { files } });
      toast(result.ok ? `Sent ${result.count} photo${result.count === 1 ? '' : 's'} to the page (${result.method})` : result.reason, !result.ok);
      if (result.ok) markPhotosUsed(urls);
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
    if (!info || !state.settings.autoAiPhotos || state.aiBusy || preloadRunning(info.upc)) return;
    const done = new Set((info.photos || []).filter(p => p.source === 'ai').map(p => p.from).filter(Boolean));
    const fresh = p => !done.has(p.name) && !state.autoPhotos.has(p.url);
    let urls = (info.photos || []).filter(p => (p.source === 'listing' || p.source === 'prep') && fresh(p)).map(p => p.url);
    if (!urls.length && !(info.photos || []).some(p => p.source === 'listing' || p.source === 'prep')) {
      urls = (info.photos || []).filter(p => p.source === 'catalog' && fresh(p)).map(p => p.url).slice(0, 4);  // nothing of ours: the catalog pictures
    }
    if (!urls.length) { if (info.upc === state.currentUpc && !state.autoPhotosTold.has(info.upc)) { state.autoPhotosTold.add(info.upc); toast('Auto AI photoshop: every photo of this item already has an AI version'); } await maybeAutoSend(); return; }
    toast(`Auto AI photoshop: ${urls.length} photo${urls.length === 1 ? '' : 's'}…`);
    await aiPhotoshopUrls(info, urls);
    await maybeAutoSend();
  }

  // -- live phone photos (QR) ----------------------------------------------------------------
  function isOwnPhoto(p) { return p.source === 'listing' || p.source === 'prep'; }
  function ownPhotoNames(info) { return new Set((info.photos || []).filter(isOwnPhoto).map(p => p.name)); }

  // While an item is open, look for photos taken on the phone every few seconds (quietly: no busy bar,
  // no re-render unless something new arrived).
  const PHONE_PHOTO_POLL_MS = 5000;
  let phoneWatchBusy = false;
  function watchPhonePhotos() {
    setInterval(async () => {
      const upc = state.currentUpc;
      const info = detail();
      if (phoneWatchBusy || !upc || !info || document.hidden || state.connected === false) return;
      if (state.aiBusy || state.busy === 'photos' || preloadRunning(upc)) return;
      phoneWatchBusy = true;
      try {
        const data = await api('/api/lister/queue/' + encodeURIComponent(upc));
        const item = data?.item;
        if (!item || upc !== state.currentUpc || state.details[upc] !== info) return;
        const before = ownPhotoNames(info);
        const added = (item.photos || []).filter(p => isOwnPhoto(p) && !before.has(p.name));
        if (!added.length) return;
        item.loadedAt = Date.now();
        state.details[upc] = item;
        renderDetail();
        toast(`📷 ${added.length} new photo${added.length === 1 ? '' : 's'} from the phone`);
        await autoNewPhotos(item, added);
      } catch (error) {
        console.warn('phone photo watch', error);
      } finally {
        phoneWatchBusy = false;
      }
    }, PHONE_PHOTO_POLL_MS);
  }

  // New photos of an item already open: AI photoshop just those (auto switch), then send just those
  // (their AI versions) to the listing form (auto send switch) — the earlier ones are on the page already.
  async function autoNewPhotos(info, added) {
    const page = state.page;
    const onForm = !!(page?.store && (page.kind === 'listing-form' || page.kind === 'offer-form'));
    const sentKey = (state.tab?.id || 0) + '|' + (page?.store || '') + '|' + info.upc;
    // Nothing sent for this item yet: the normal auto flow covers every photo, the new ones included.
    if (!onForm || !state.settings.autoSendPhotos || !state.autoSent.has(sentKey)) { await maybeAutoPhotos(info); return; }
    if (state.settings.autoAiPhotos) {
      while (state.aiBusy) await new Promise(r => setTimeout(r, 1000));
      const aiDone = new Set((info.photos || []).filter(p => p.source === 'ai').map(p => p.from).filter(Boolean));
      const todo = added.filter(p => !aiDone.has(p.name) && !state.autoPhotos.has(p.url)).map(p => p.url);
      if (todo.length) { toast(`Auto AI photoshop: ${todo.length} new photo${todo.length === 1 ? '' : 's'}…`); await aiPhotoshopUrls(info, todo); }
    }
    const aiFor = new Map((info.photos || []).filter(p => p.source === 'ai' && p.from).map(p => [p.from, p.url]));
    const urls = [];
    for (const p of added) {
      const url = aiFor.get(p.name) || (state.settings.autoSendAiOnly ? '' : p.url);
      if (!url || (state.phoneSent ||= new Set()).has(sentKey + '|' + url)) continue;
      if (isTooSmall(await photoSize(url))) continue;
      urls.push(url);
    }
    if (!urls.length) {
      if (state.settings.autoSendAiOnly && !state.settings.autoAiPhotos) toast('Auto send: new photos not sent (only AI generated is on, AI photoshop is off)', true);
      return;
    }
    for (const url of urls) state.phoneSent.add(sentKey + '|' + url);
    toast(`Auto send: ${urls.length} new photo${urls.length === 1 ? '' : 's'} to the page…`);
    await sendPhotoUrls(info, urls);
  }

  // The prompt box: this item's unsaved edit if there is one, else the prompt saved for all items
  // (state.aiPrompt, persisted by "Save for all"), else the server's default.
  function promptFor(info) {
    const draft = state.promptDraft && state.promptDraft.upc === info.upc ? state.promptDraft.text : null;
    return ((draft ?? state.aiPrompt) || '').trim() || info.aiPhotoPrompt || '';
  }

  async function aiPhotoshopUrls(info, urls) {
    const prompt = promptFor(info).trim();
    let done = 0;
    const release = busy('AI photoshop…');
    for (const url of urls) {
      state.aiBusy = `AI photoshop ${done + 1}/${urls.length}…`; release.update(`AI photoshop ${done + 1} of ${urls.length}…`); renderDetail();
      try {
        const data = await api('/api/lister/photos/ai', { method: 'POST', body: { upc: info.upc, url, prompt } });
        info.photos.unshift(data.photo);
        state.autoPhotos.add(url);  // a manual run counts: the auto switch skips this photo later
        state.selectedPhotos.delete(url);
        state.selectedPhotos.add(data.photo.url);
        done += 1;
      } catch (error) {
        state.autoPhotos.delete(url);
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
    renderHeader(); renderStoreBar(); renderPage(); renderSessions(); renderItems(); renderDetail(); renderConfirm(); renderNew(); renderActionBar();
  }

  // The server is a dot: green when it answers, red when it does not. The detail is its tooltip.
  function renderHeader() {
    const dot = $('connStatus');
    if (state.connected === null) { dot.className = 'conn'; dot.title = 'Connecting\u2026'; }
    else if (state.connected) { dot.className = 'conn ok'; dot.title = (state.user ? state.user.split('@')[0] + ' \u00b7 ' : '') + serverBase(); }
    else { dot.className = 'conn bad'; dot.title = state.signIn ? 'Sign in to Sweet Shelves' : 'Offline \u2014 click to try again'; }
    $('signin').hidden = !state.signIn;
    $('versionLine').textContent = `Extension ${chrome.runtime.getManifest().version}` + (state.serverVersion ? ` \u00b7 server ${state.serverVersion}` : '');
  }

  // The panel is in one place at a time: the queue, or one item. The crumb is the way back.
  function renderStoreBar() {
    for (const p of ['ebay', 'amazon']) {
      $(p === 'ebay' ? 'storeEbay' : 'storeAmazon').setAttribute('aria-selected', String(state.platform === p));
      const c = state.counts[p] || {};
      $(p === 'ebay' ? 'countEbay' : 'countAmazon').textContent = c.queued != null ? c.queued : '';
    }
    $('storeNew').setAttribute('aria-selected', String(state.view === 'new'));
    const item = current();
    const onItem = state.view === 'item' && Boolean(item);
    const onNew = state.view === 'new';
    const locked = state.locked ? state.items.find(it => it.upc === state.locked) : null;
    document.body.classList.toggle('locked', Boolean(state.locked));
    $('crumb').hidden = !onItem && !onNew;
    if (onNew) $('crumbNow').textContent = 'New item';
    else if (onItem) $('crumbNow').textContent = locked?.title || item.title || item.upc;
    $('crumbLock').hidden = !state.locked || onNew;
    $('listCard').hidden = onItem || onNew;
    renderActionBar();
  }

  // The automatic switches. They live in Settings and nowhere else, so nothing on the work surface
  // can be knocked by accident; the item view reports what they are set to in one line.
  // The switch rows alone. `prefix` keeps the ids unique when the item's Options box shows them too.
  function autoRows(prefix = '') {
    const items = AUTO_TOGGLES;
    const s = state.settings;
    const groups = [...new Set(items.map(t => t.group))];
    const row = t => {
      const dim = t.sub && !s.autoSendPhotos;
      return `<label class="sw${t.sub ? ' sub' : ''}${dim ? ' dim' : ''}" title="${esc(t.hint)}">
          <input id="${prefix}${t.key}" data-auto="${t.key}" type="checkbox" role="switch" ${s[t.key] ? 'checked' : ''}><span class="track" aria-hidden="true"></span>
          <span class="ico" aria-hidden="true">${t.icon}</span><span class="txt"><b>${esc(t.label)}</b><small>${esc(t.hint)}</small></span></label>`;
    };
    return groups.map(g => `<div class="tgroup"><div class="tg-title">${esc(g)}</div>${items.filter(t => t.group === g).map(row).join('')}</div>`).join('');
  }

  function autoMenu() {
    const items = AUTO_TOGGLES;
    const open = Boolean(state.settings.togglesOpen);
    const s = state.settings;
    const on = items.filter(t => s[t.key] && !t.sub).map(t => t.short + (t.key === 'autoSendPhotos' && s.autoSendAiOnly ? ' (AI only)' : ''));
    return `<div class="toggles${open ? ' open' : ''}">
        <button id="togglesBtn" class="toggles-head" type="button" aria-expanded="${open}" title="${open ? 'Hide' : 'Show'} the automatic switches">
          <span class="caret">${open ? '\u25be' : '\u25b8'}</span><span class="lbl">Automatic</span>
          <span class="sum ${on.length ? 'on' : 'muted'}">${on.length ? on.map(x => `<span class="pill">${esc(x)}</span>`).join('') : 'all off'}</span></button>
        <div class="toggles-body"${open ? '' : ' hidden'}>
          ${autoRows()}
        </div>
      </div>`;
  }

  // Plain words for what the panel will do by itself, shown once on the item view.
  function autoSummary() {
    const s = state.settings;
    const on = AUTO_TOGGLES.filter(t => s[t.key] && !t.sub).map(t => t.short + (t.key === 'autoSendPhotos' && s.autoSendAiOnly ? ' (AI only)' : ''));
    if (s.autoFill) on.unshift('fill');
    return on.length ? on.join(' \u00b7 ') : 'nothing \u2014 you drive';
  }

  function renderAutoMenu() {
    const box = $('autoBox');
    if (!box) return;
    box.innerHTML = autoMenu();
    const head = $('togglesBtn');
    head.onclick = async () => { await saveSettings({ ...state.settings, togglesOpen: !state.settings.togglesOpen }); renderAutoMenu(); };
    wireAutoChecks(box);
  }

  function wireAutoChecks(scope) {
    for (const check of scope.querySelectorAll('input[data-auto]')) check.onchange = async () => {
      const key = check.dataset.auto;
      const on = check.checked;
      await saveSettings({ ...state.settings, [key]: on });
      toast({
        autoAiTitle: on ? 'Will write the title automatically on every listing' : 'Automatic title off',
        autoAiDescription: on ? 'Will write the description automatically on every listing' : 'Automatic description off',
        autoAiPhotos: on ? 'Every photo of ours will get an AI version automatically' : 'Automatic AI photoshop off',
        autoSendPhotos: on ? 'Photos will go to the page on every listing (never the too-small ones)' : 'Automatic photo send off',
        autoSendAiOnly: on ? 'Only AI generated photos are sent by default' : 'All of our photos are sent by default (AI version preferred)',
      }[key]);
      renderAutoMenu(); renderDetail();
      if (!on) return;
      if (key === 'autoAiTitle' || key === 'autoAiDescription') void maybeAutoText();
      else if (key === 'autoAiPhotos') { if (detail()) void maybeAutoPhotos(detail()); }
      else void maybeAutoSend();
    };
  }

  // Only what is being decided here, or has gone wrong. Where you are and what to do next is the
  // action bar's job, so this card is empty - and invisible - most of the time.
  function renderPage() {
    const page = state.page || {};
    const el = $('pageCard');
    const assist = state.assist && state.assist.kind === page.kind ? state.assist : null;
    const lines = [];
    if (assist && assist.suggested) lines.push(`<div class="flag info"><b>Suggested:</b> ${esc(assist.suggested)}${page.kind === 'listing-confirm' ? ' (pre-selected)' : ' \u2014 your click teaches the panel'}</div>`);
    else if (assist && assist.candidates) lines.push('<div class="flag warn">No confident suggestion here; your choice will be remembered.</div>');
    if (page.error) lines.push(`<div class="flag warn">Page script: ${esc(page.error)}</div>`);
    setHtml(el, lines.join(''));
    el.hidden = !lines.length;
    // The page kind stopped being a line of text on the store card; keep it readable for tests and bug reports.
    document.body.dataset.pageKind = page.kind || '';
    document.body.dataset.store = page.store || '';
    renderActionBar();
  }

  // The single next thing to do, named for where you actually are.
  function nextAction() {
    const item = current();
    const page = state.page || {};
    const g = state.guide;
    if (state.view === 'new') {
      const draft = state.newItem.draft;
      if (!draft) return { label: 'Start a new item', run: () => newStart() };
      if (draft.status !== 'draft') return { label: 'Submitted ✓', disabled: true };
      const missing = draft.missing || [];
      if (missing.length) return { label: 'Needs a ' + missing.join(' and a '), disabled: true,
                                   hint: 'Scan the barcode here; the phone sends the name' };
      return { label: 'Submit to Items to List', run: () => newSubmit(),
               hint: 'Writes the name, the photos and the notes onto ' + draft.upc + ', then queues it' };
    }
    if (state.connected === false) return { label: state.signIn ? 'Sign in to Sweet Shelves' : 'Try the server again', run: () => { state.connected = null; renderHeader(); return connect().then(() => loadQueue()); } };
    if (!item) return { label: `Nothing queued for ${storeName(state.platform)}`, disabled: true };
    const start = { label: `Start on ${storeName(state.platform)}`, run: () => startOn(state.platform), hint: 'Opens the store and types the UPC' };
    if (!page.store) return start;
    switch (page.kind) {
      case 'listing-start':
      case 'product-search':
        return { label: `Search ${esc(item.baseUpc || item.upc)}`, run: () => searchUpc(), hint: 'Types the UPC into the store search and presses it' };
      case 'listing-category':
      case 'listing-match':
      case 'listing-confirm':
        return { label: 'Choose on the page', disabled: true, hint: 'The panel highlights the closest match; your click teaches it' };
      case 'listing-form':
      case 'offer-form':
        if (!g?.active) return { label: 'Fill page', run: () => fillPage(), hint: 'Puts the prepared values into the form' };
        if (g.open) return { label: `Checklist \u00b7 ${g.open} left`, run: () => guide('start'), hint: 'Back to the checklist overlay on the page' };
        return { label: 'All set \u2014 list it on the page', run: () => guide('start'), hint: 'Every field has a value' };
      case 'listing-success':
      case 'offer-success':
      case 'listing-live': {
        const linked = (detail()?.links || []).some(l => l.platform === (page.store || state.platform));
        if (linked) return { label: 'Recorded \u2713', disabled: true, hint: 'The listing is on the ledger and on Items to List' };
        return { label: 'Record this listing', run: () => confirmLink(), hint: 'Ties the store item number to this UPC' };
      }
      default:
        return start;
    }
  }

  function renderActionBar() {
    const go = $('goBtn');
    if (!go) return;
    const action = nextAction();
    go.textContent = action.label;
    go.disabled = Boolean(action.disabled);
    go.title = action.hint || action.label;
    go.onclick = () => { if (!action.disabled && action.run) void action.run(); };
  }

  // Everything else you could do right here, one level down.
  function moreActions() {
    const item = current();
    const info = detail();
    const page = state.page || {};
    const onForm = page.kind === 'listing-form' || page.kind === 'offer-form';
    const list = [];
    if (state.view === 'new' && state.newItem.draft) {
      return [{ label: '↻ Look for new photos now', run: () => newLoad(state.newItem.draft.id) },
              { label: '↗ Open the phone page here', run: () => chrome.tabs.create({ url: state.newItem.draft.phoneUrl }) },
              { label: '✕ Throw this new item away', run: () => newCancel(), danger: true }];
    }
    if (state.view === 'list') list.push({ label: '\u26a1 Preload every queued item', run: () => preloadAll(), off: Boolean(state.preload.all?.running) });
    if (info && state.view === 'item') list.push({ label: '\u2b05 Send photos to the page', run: () => sendPhotos(), off: !page.store || Boolean(state.busy) });
    if (info && state.view === 'item') list.push({ label: '\u2728 AI photoshop the photos', run: () => aiPhotoshop(), off: Boolean(state.aiBusy) || !(info.photos || []).length });
    if (onForm) list.push({ label: '\u25ce Pick a field on the page', run: () => startPick() });
    if (page.store) list.push({ label: '\u21bb Re-read the page', run: () => refreshTab() });
    list.push({ label: '\u21bb Reload the queue', run: () => { state.details = {}; void connect().then(() => loadQueue()); void refreshTab(); } });
    if (item) list.push({ label: `\u2715 Take this off the ${storeName(state.platform)} list`, run: () => skipItem(item), danger: true });
    list.push({ label: '\u2197 Items to List', run: () => chrome.tabs.create({ url: serverBase() + '/items-to-list' }) });
    list.push({ label: '\u2197 Ledger', run: () => chrome.tabs.create({ url: serverBase() + '/lister-ledger' }) });
    return list;
  }

  function openMore() {
    const box = $('modal');
    const list = moreActions();
    box.innerHTML = `<div class="box menu"><h2>Also here</h2>
      ${list.map((a, i) => `<button class="menu-item${a.danger ? ' danger' : ''}" data-more="${i}" type="button"${a.off ? ' disabled' : ''}>${esc(a.label)}</button>`).join('')}
      <button class="menu-close" data-more="close" type="button">Close</button></div>`;
    box.hidden = false;
    box.onclick = event => {
      const button = event.target.closest('[data-more]');
      if (!button && event.target !== box) return;
      box.hidden = true;
      if (button && button.dataset.more !== 'close') void list[Number(button.dataset.more)].run();
    };
  }

  // Put one value on the store page (after an AI text or a note was applied) without a full re-fill.
  // Which texts AI wrote for this item: the page tags those fields "Added AI generated …".
  function aiFieldsFor(info) {
    return { ...(info?.fields?.generated || {}), ...(state.aiGenerated[info?.upc] || {}) };
  }

  async function pushValue(info, target) {
    if (!state.page?.store || !(state.page.kind === 'listing-form' || state.page.kind === 'offer-form')) return false;
    try {
      const result = await pageMessage({ type: 'fill', options: { values: values(info), store: state.page.store, targets: [target], aspects: {}, learned: state.learned[state.page.store + ':' + (state.page.kind || '')] || {}, aiFields: aiFieldsFor(info) } });
      return (result.report.filled || []).some(f => f.target === target);
    } catch { return false; }
  }

  // The two auto-AI switches: once per tab and item, after the form was filled.
  async function maybeAutoText() {
    const info = detail();
    const page = state.page;
    if (!info || !page?.store || !(page.kind === 'listing-form' || page.kind === 'offer-form')) return;
    if (preloadRunning(info.upc)) return;  // the preload is writing it; maybeAutoText runs again when it ends
    for (const kind of ['title', 'description']) {
      if (!state.settings[kind === 'title' ? 'autoAiTitle' : 'autoAiDescription']) continue;
      if (info.fields?.generated?.[kind]) continue;  // written earlier (preload or a click): the fill uses the stored text
      const key = (state.tab?.id || 0) + '|' + page.store + '|' + info.upc + '|' + kind;
      if (state.autoText.has(key)) continue;
      state.autoText.add(key);
      await generate(info, kind, { auto: true });
    }
  }

  // The way back into a listing that was left half done, at the top of the queue.
  function renderSessions() {
    const el = $('sessionCard');
    if (!el) return;
    const list = freshSessions(state.sessions);
    const row = s => `<div class="rsrow"><button class="rsgo" type="button" data-resume="${esc(sessionKey(s))}" title="Back to the ${storeName(s.platform)} form you left">`
      + `<span class="where ${s.platform}">${storeName(s.platform)}</span><span class="what">${esc(s.title || s.upc)}</span><span class="when">${esc(ago(s.at))}</span></button>`
      + `<button class="rsdrop" type="button" data-drop="${esc(sessionKey(s))}" title="Forget this one" aria-label="Forget this unfinished listing">×</button></div>`;
    const html = list.length ? `<div class="rshead">↩ Carry on where you left off</div>${list.map(row).join('')}` : '';
    el.hidden = !html;
    if (!setHtml(el, html)) return;
    for (const b of el.querySelectorAll('[data-resume]')) b.onclick = () => { const s = list.find(x => sessionKey(x) === b.dataset.resume); if (s) void resumeSession(s); };
    for (const b of el.querySelectorAll('[data-drop]')) b.onclick = () => { const s = list.find(x => sessionKey(x) === b.dataset.drop); if (s) dropSession(s.upc, s.platform); };
  }

  function renderItems() {
    const filter = state.filter.trim().toLowerCase();
    const isFlagged = it => (it.prepStatus?.status || '') === 'bad' || Boolean(it.defect);
    const isOnStore = it => Boolean(it.alreadyOnStore) || it.otherStatus === 'listed';
    const isBlocked = it => state.platform === 'amazon' && ['restricted', 'approval', 'no_asin'].includes(it.amazonCheck?.status || '');
    const tests = { all: () => true, flagged: isFlagged, listed: isOnStore, blocked: isBlocked };
    rememberThumbs(state.items);
    const queued = state.items.filter(it => it.status === 'queued');
    $('clearAll').disabled = !queued.length;
    // The filters carry their own counts: the numbers are why you would press one.
    for (const [id, value, n] of [['statusAll', 'all', queued.length], ['statusFlagged', 'flagged', queued.filter(isFlagged).length],
      ['statusListed', 'listed', queued.filter(isOnStore).length], ['statusBlocked', 'blocked', queued.filter(isBlocked).length]]) {
      const button = $(id);
      button.setAttribute('aria-pressed', String(state.statusFilter === value));
      button.querySelector('.n').textContent = n;
      button.hidden = value === 'blocked' && state.platform !== 'amazon';
    }
    const match = it => !filter || (it.title || '').toLowerCase().includes(filter) || (it.upc || '').includes(filter);
    const rows = queued.filter(it => (tests[state.statusFilter] || tests.all)(it) && match(it));
    const list = $('itemList');
    if (!rows.length) {
      const listedHere = state.items.filter(it => it.status === 'listed').length;
      list.innerHTML = `<div class="empty">${state.connected === false ? 'Not connected.' : (state.statusFilter !== 'all' || filter ? 'Nothing matches this filter.' : (listedHere ? `Everything queued for ${storeName(state.platform)} is listed.` : `Nothing queued for ${storeName(state.platform)}. Add items to the Listing Agent queue on <b>Items to List</b>.`))}</div>`;
      return;
    }
    // Only the exceptions earn a word here: a row with nothing on it is the good row. At most two,
    // in fixed slots - what blocks this item, then where it already lives.
    const signalsOf = it => {
      const signals = [];
      const ac = state.platform === 'amazon' ? it.amazonCheck : null;
      if (state.platform === 'amazon' && state.checkingAmazon.has(it.baseUpc)) signals.push('<span class="sig quiet"><span class="spin"></span> checking</span>');
      else if (ac?.status === 'restricted') signals.push(`<span class="sig block" title="${esc(amazonCheckWhy(ac) || 'Amazon restricts this listing for us')}">restricted</span>`);
      else if (ac?.status === 'approval') signals.push(`<span class="sig block" title="${esc(amazonCheckWhy(ac) || 'Amazon needs to approve us for this')}">needs approval</span>`);
      else if (ac?.status === 'no_asin') signals.push('<span class="sig block" title="No ASIN for this UPC: Amazon has no product page to list against">no ASIN</span>');
      const bad = it.prepStatus?.status === 'bad' ? (it.prepStatus.reason || 'bad') : '';
      const wrong = bad || it.defect || '';
      // One flag, even when Item Prep and the BOL both have something to say; the tooltip carries both.
      if (wrong) signals.push(`<span class="sig flag" title="${esc([bad ? 'Item Prep: ' + bad : '', it.defect ? 'BOL: ' + it.defect : ''].filter(Boolean).join(' · '))}">${esc(wrong)}</span>`);
      const noted = it.notes ? [it.notes.written ? '\ud83d\udcdd ' + it.notes.written : '', it.notes.voice ? '\ud83c\udfa4 ' + it.notes.voice : ''].filter(Boolean).join(' \u00b7 ') : '';
      if (noted) signals.push(`<span class="sig quiet" title="Prep notes on file">${esc(noted)}</span>`);
      const other = state.platform === 'ebay' ? 'amazon' : 'ebay';
      if (it.status === 'listed') signals.push(`<span class="sig done" title="Listed through the panel">listed${it.storeUrl ? ` <a href="${esc(it.storeUrl)}" target="_blank" rel="noopener">\u2197</a>` : ''}</span>`);
      else if (it.alreadyOnStore) signals.push(`<span class="sig quiet" title="${storeName(state.platform)} already carries this UPC">on ${storeName(state.platform)}${it.storeUrl ? ` <a href="${esc(it.storeUrl)}" target="_blank" rel="noopener">\u2197</a>` : ''}</span>`);
      else if (it.otherStatus === 'listed') signals.push(`<span class="sig quiet">on ${storeName(other)}</span>`);
      if (it.preparing) signals.push('<span class="sig quiet"><span class="spin"></span> preparing</span>');
      return signals.slice(0, 2);
    };
    // Preload is one bolt: lit when it is ready, dim while it waits, amber when a step failed.
    const boltOf = it => {
      const pl = preloadOf(it.upc);
      const s = pl ? preloadSummary(pl) : null;
      return !pl ? '' : pl.running ? '<span class="spin" title="Preloading"></span>'
        : pl.queued ? '<span class="bolt off" title="Waiting its turn in Preload all">\u26a1</span>'
        : s.failed.length ? `<span class="bolt warn" title="${esc(s.failed.map(([k, v]) => s.problem(k, v)).join(' \u00b7 '))}">\u26a1</span>`
        : `<span class="bolt on" title="${esc(s.steps.map(([k, v]) => PRELOAD_LABEL[k] + ' ' + (v === 'done' ? '\u2713' : v)).join(' \u00b7 '))}">\u26a1</span>`;
    };
    const upcHtml = it => (it.suffixed ? `${esc(it.baseUpc)}-<b class="suffix" title="Specific unit: the SKU keeps the -suffix">${esc(it.upc.split('-')[1])}</b>` : esc(it.upc));

    // A row is built once and then patched in place. Rebuilding the list with innerHTML on every
    // render throws the nodes away: the list jumps back to the top, every thumbnail reloads and the
    // row flickers under the cursor - which is what a single click used to look like.
    const newRow = it => {
      const el = document.createElement('div');
      el.className = 'item';
      el.dataset.upc = it.upc;
      el.innerHTML = `<span class="stripe"></span>${it.thumb ? '<img alt="" loading="lazy">' : '<div class="noimg"></div>'}`
        + '<div class="body"><div class="title"></div><div class="sub"></div></div>'
        + '<span class="state"><span class="bolt-slot"></span><button class="inspect" type="button">Details</button>'
        + '<button class="remove" type="button" aria-label="Remove">\u00d7</button></span><div class="go"></div>';
      return el;
    };
    const patchRow = (el, it) => {
      const armed = it.upc === state.armedUpc && it.upc === state.currentUpc;
      const cls = `item${it.upc === state.currentUpc ? ' current' : ''}${armed ? ' armed' : ''}${isBlocked(it) ? ' blocked' : ''}${it.status === 'listed' ? ' listed' : ''}`;
      if (el.className !== cls) el.className = cls;
      const tip = armed ? `Click again to list it on ${storeName(state.platform)}` : `${it.title} \u2014 click to select, click again to list`;
      if (el.title !== tip) el.title = tip;
      // The picture is only touched when the picture itself changed, so it never reloads.
      const pic = el.children[1];
      if (it.thumb && pic.tagName === 'IMG') showPic(pic, it.thumb);
      else if (it.thumb) { const img = document.createElement('img'); img.alt = ''; img.loading = 'lazy'; showPic(img, it.thumb); el.replaceChild(img, pic); }
      else if (pic.tagName === 'IMG') { const box = document.createElement('div'); box.className = 'noimg'; el.replaceChild(box, pic); }
      const body = el.children[2];
      const text = it.title || '(no title)';
      if (body.firstElementChild.textContent !== text) body.firstElementChild.textContent = text;
      setHtml(body.children[1], `<span class="upc">${upcHtml(it)}</span>${signalsOf(it).join('')}`);
      // The selected row carries its own next step, so the second click has an obvious target.
      setHtml(el.children[4], armed ? `Click again to list on ${storeName(state.platform)} →` : '');
      const right = el.children[3];
      setHtml(right.firstElementChild, boltOf(it));
      const inspect = right.children[1];
      inspect.dataset.open = it.upc;
      inspect.title = 'Look at this item: photos, notes, stock';
      const remove = right.lastElementChild;
      remove.hidden = it.status !== 'queued';
      remove.dataset.skip = it.upc;
      remove.title = `Take it off the ${storeName(state.platform)} list`;
    };
    if (list.firstElementChild && !list.firstElementChild.dataset.upc) list.textContent = '';  // the empty message was here
    const kept = new Map([...list.children].map(el => [el.dataset.upc, el]));
    let previous = null;
    for (const it of rows) {
      let el = kept.get(it.upc);
      if (el) kept.delete(it.upc); else el = newRow(it);
      patchRow(el, it);
      const place = previous ? previous.nextSibling : list.firstChild;
      if (el !== place) list.insertBefore(el, place);   // moved only when it is in the wrong place
      previous = el;
    }
    for (const el of kept.values()) el.remove();

    // One handler for the whole list, wired once: the rows themselves come and go.
    if (!list.dataset.wired) {
      list.dataset.wired = '1';
      list.onclick = event => {
        const skip = event.target.closest('button[data-skip]');
        if (skip) { event.stopPropagation(); const it = state.items.find(x => x.upc === skip.dataset.skip); if (it) void skipItem(it); return; }
        // Details opens the item view without starting anything.
        const open = event.target.closest('button[data-open]');
        if (open) { event.stopPropagation(); pickItem(open.dataset.open); return; }
        const el = event.target.closest('.item');
        if (!el || event.target.closest('a, button')) return;
        // First click selects and highlights the row; a click on the selected row lists it.
        if (state.armedUpc === el.dataset.upc && state.currentUpc === el.dataset.upc) { void startOn(state.platform); return; }
        pickItem(el.dataset.upc, { stay: true });
      };
    }
  }

  // Picking a queue row: this item becomes the one the panel is working. Details (open) hands the
  // panel to the item view; a plain row click (stay) only selects it, ready for the second click.
  function pickItem(upc, { stay = false } = {}) {
    if (!upc) return;
    const changed = state.currentUpc !== upc;
    state.armedUpc = upc;
    if (changed) { state.currentUpc = upc; state.report = null; state.guide = null; state.selectedPhotos = new Set(); state.promptDraft = null; remember(); }
    if (!stay) setView('item');
    renderItems(); renderConfirm();
    if (changed || !stay) void loadDetail(upc).then(() => maybeAssist());
    void preloadItem(upc);
    if (state.page?.store) bindTab(upc, state.platform);
    if (stay) return;
    // Opened while the store's search page is open: search this UPC right away. Arm it only when that
    // page is actually in front - otherwise a search page opened an hour later would search on its own.
    if (state.page?.kind === 'listing-start' || state.page?.kind === 'product-search') {
      state.pendingSearch = { upc, platform: state.platform };
      void maybeAutoSearch({ force: true });
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
      // The waiting card keeps the room the real one will need, so nothing jumps when it arrives.
      setHtml(el, `<div class="hero">${item.thumb ? `<img src="${esc(item.thumb)}" alt="">` : '<div class="noimg"></div>'}<div><div class="name">${esc(item.title || item.upc)}</div><div class="facts">${esc(item.upc)}</div></div></div><p class="muted loading"><span class="spin"></span> Loading item\u2026</p>`);
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
    const storeUrlFor = platform => {
      const link = (info.links || []).find(l => l.platform === platform && l.url);
      if (link) return link.url;
      const entry = ((info.existing || {})[platform] || []).find(e => e.listingId || e.asin);
      if (!entry) return '';
      if (platform === 'ebay' && entry.listingId) return 'https://www.ebay.com/itm/' + encodeURIComponent(entry.listingId);
      if (platform === 'amazon' && entry.asin) return 'https://www.amazon.com/dp/' + encodeURIComponent(entry.asin);
      return '';
    };
    // One cell per store: green when it is on there, grey when it is not. No colour without meaning.
    const storeCell = platform => {
      // A linked listing the store already carried says "On store", not "Listed": the panel did not list it.
      const link = (info.links || []).find(l => l.platform === platform);
      const linkedExisting = !!link && !isOwnListing(link);
      const linked = !linkedExisting && (!!link || info.queue?.listed?.[platform]);
      const onStore = linkedExisting || ((info.existing || {})[platform] || []).length > 0;
      const skipped = info.queue?.skipped?.includes(platform);
      const [cls, text] = linked ? ['good', 'Listed'] : onStore ? ['good', 'On store'] : skipped ? ['idle', 'Skipped'] : ['idle', 'Not listed'];
      const url = (linked || onStore) ? storeUrlFor(platform) : '';
      const live = platform === 'ebay' ? gate.liveEbay : gate.liveAmazon;
      return `<div class="cell ${platform} ${cls}"><div class="k">${storeName(platform)}</div>
        <div class="v">${url ? `<a href="${esc(url)}" target="_blank" rel="noopener" title="Open the ${storeName(platform)} listing">${text} \u2197</a>` : text}</div>
        <div class="s">${esc(live ?? 0)} live</div></div>`;
    };
    const rackQty = gate.rackQty ?? stock.quantity ?? 0;
    const listable = gate.listable ?? (stock.quantity || 0);
    const warehouseUrl = serverBase() + '/unified-search?q=' + encodeURIComponent(info.upc);
    const prepUrl = serverBase() + '/items-to-list?direct_search=1&q=' + encodeURIComponent(info.upc);
    const where = stock.positions?.length
      ? `@ ${stock.positions.map(p => `<button type="button" class="loc" data-locpv="${esc(p)}" title="Show the shelf photo for ${esc(p)}">${esc(p)}</button>`).join(', ')}`
      : 'no rack position';
    // The thing you must act on, said once, in words - not a chip in a row of chips.
    const alerts = [];
    const wrong = (prep.status === 'bad' ? (prep.reason || 'marked bad in Item Prep') : '') || info.defect || '';
    if (wrong) alerts.push(`<div class="alert warn">\u26a0 ${esc(wrong)} \u2014 say so in the condition note</div>`);
    if (!rackQty) alerts.push('<div class="alert block">\u26a0 Nothing on the rack for this UPC</div>');
    if (gate.mismatch) alerts.push(`<div class="alert warn">Item Prep counted ${esc(gate.prepQty)}, the rack holds ${esc(rackQty)} \u2014 <a href="${esc(prepUrl)}" target="_blank" rel="noopener">open Items to List</a></div>`);
    if (existing.length && !linkedHere) alerts.push(`<div class="alert warn"><span class="grow">Already on ${storeName(state.platform)}: ${existing.map(x => esc(x.listingId || x.asin || x.sku) + (x.state ? ' (' + esc(x.state) + ')' : '')).join(', ')}</span>
      <button id="markExisting" class="tiny" type="button">Use that listing</button></div>`);
    const bubble = p => (p.source === 'ai' ? 'ai' : p.source === 'prep' ? 'prep' : p.source === 'catalog' ? 'catalog' : '');
    const tile = p => { const used = state.usedPhotos.has(p.url); return `<div class="photo ${p.source} ${state.selectedPhotos.has(p.url) ? 'selected' : ''} ${used ? 'used' : ''}" data-url="${esc(p.url)}" draggable="true" title="${esc(p.name)}${used ? ' \u00b7 already on the page' : ' \u00b7 drag onto the store page'}">
        <img src="${esc(p.url)}" alt="" loading="lazy" draggable="false"><input type="checkbox" data-select="${esc(p.url)}" ${state.selectedPhotos.has(p.url) ? 'checked' : ''}>
        ${bubble(p) ? `<span class="tag ${bubble(p)}">${bubble(p)}</span>` : ''}<span class="tag small" hidden title="Under ${MIN_PHOTO_SIDE} px \u2014 never sent automatically">too small</span>${used ? '<span class="tag used" title="This photo already went into the page">used</span>' : ''}</div>`; };
    const noteRow = (icon, lt, en, extra = '', plain = false) => `<div class="note2${plain ? ' plain' : ''}"><span class="ico" title="${icon === '\ud83c\udfa4' ? 'Voice note' : 'Written note'}">${icon}</span>
        <div class="lt">${lt ? esc(lt) : '<span class="muted">\u2014</span>'}</div><div class="en">${en ? esc(en) : '<span class="muted">\u2014</span>'}</div>${extra}</div>`;
    const html = `
      <div class="hero">${(photos[0] || {}).url || item.thumb ? `<img src="${esc((photos[0] || {}).url || item.thumb)}" alt="">` : '<div class="noimg"></div>'}
        <div><div class="name">${esc(v.title || item.title || item.upc)}</div>
          <div class="facts">${esc(info.upc.split('-')[0])}${info.suffixed ? ` \u00b7 <span class="unit">unit ${esc(info.upc.split('-')[1])}</span>` : ''}${info.cost != null ? ' \u00b7 $' + money(info.cost) : ''}${v.price != null ? ' \u2192 <b>$' + esc(money(v.price)) + '</b>' : ''} \u00b7 qty ${esc(v.quantity ?? '?')}</div>
        </div></div>
      ${alerts.join('')}
      <div class="strip">
        <div class="cell rack ${rackQty ? 'good' : 'bad'} ${gate.mismatch ? 'mismatch' : ''}"><div class="k">Rack</div>
          <div class="v"><a href="${esc(warehouseUrl)}" target="_blank" rel="noopener" title="Open the warehouse search"><span class="num">${esc(listable)}</span> to list</a>${gate.mismatch ? ' <span class="neq">\u2260</span>' : ''}</div>
          <div class="s">${where}</div></div>
        ${storeCell('ebay')}${storeCell('amazon')}
      </div>

      <div class="sect notes">
        <div class="sect-h"><span class="lbl">Notes</span><span class="n">${notes.length || voice.length || info.defect ? 'LT \u00b7 EN' : 'none'}</span><span class="sp"></span></div>
        ${voice.map(n => noteRow('\ud83c\udfa4', n.lithuanian, n.english, `<div class="acts">
          ${n.status === 'complete' || n.english ? '' : (state.voiceBusy.has(info.upc + ':' + n.id) || n.status === 'processing' ? '<span class="spin"></span>' : `<button class="tiny" data-transcribe="${n.id}" type="button" title="Transcribe and translate">text</button>`)}
          <button class="tiny" data-play="${esc(n.url)}" type="button" title="Play the recording">\u25b6</button>
          ${n.english ? `<button class="tiny" data-usenote="${n.id}" type="button" title="Add to the condition note on the listing">\u2192 listing</button>` : ''}
          ${n.error && !n.english ? `<span class="chip bad" title="${esc(n.error)}">failed</span>` : ''}</div>`)).join('')}
        ${info.defect ? noteRow('\ud83d\udce6', '', 'BOL reason: ' + info.defect, '', true) : ''}
        ${notes.map(n => { const h = noteHalves(n); return noteRow('\ud83d\udcdd', h.lt, h.en, `<div class="acts muted small">${esc((n.createdAt || '').slice(0, 10))}</div>`, true); }).join('')}
        ${!voice.length && !notes.length && !info.defect ? '<div class="muted small">No notes on this one.</div>' : ''}
        <audio id="notePlayer" preload="none" hidden></audio>
      </div>

      <div class="sect">
        <div class="sect-h"><span class="lbl">Photos</span><span class="n">${photos.length}${state.usedPhotos.size ? ` \u00b7 ${state.usedPhotos.size} sent` : ''}</span><span class="sp"></span>
          <button id="photoLink" class="tiny phone-btn" type="button" title="Telegram the camera link to the phone \u2014 no scanning" ${state.photoLinkBusy ? 'disabled' : ''}>${state.photoLinkBusy ? '<span class="spin"></span>' : '<span class="phone-ico" aria-hidden="true">\ud83d\udcf1</span>Phone'}</button>
          <button id="addPhoto" class="tiny" type="button" title="Show a QR code for the phone camera page">${QR_ICON}${state.qrOpen ? 'Hide QR' : 'QR'}</button>
          <button id="photoRefresh" class="tiny" type="button" title="Reload photos (after taking new ones on the phone)">\u21bb</button></div>
        ${state.qrOpen ? `<div class="qr"><img src="${esc(serverBase() + '/api/lister/qr?text=' + encodeURIComponent(info.mobilePhotosUrl))}" alt="QR code for the phone photo page"><div class="small">Scan with the phone \u2014 it opens straight into the camera. Press \u21bb when the photos are in.<br><a href="${esc(info.mobilePhotosUrl)}" target="_blank" rel="noopener">open the page</a></div></div>` : ''}
        ${photos.length ? `<div class="photos">${photos.map(tile).join('')}</div>` : '<div class="muted small">No photos yet. Use QR or Phone to shoot some.</div>'}
        <div class="row tight">
          <button id="sendPhotos" class="primary mini" type="button" ${store && !state.busy ? '' : 'disabled'} title="Puts the ticked photos into the page's photo uploader; with nothing ticked, ${state.settings.autoSendAiOnly ? 'the AI generated ones' : 'ours (AI version preferred)'} minus too-small ones">${state.busy === 'photos' ? '<span class="spin"></span> Sending\u2026' : '\u2b05 Send to page'}</button>
          <button id="aiPhotos" class="mini" type="button" ${state.aiBusy || !photos.length ? 'disabled' : ''}>${state.aiBusy ? '<span class="spin"></span> ' + esc(state.aiBusy) : '\u2728 AI photoshop'}</button>
        </div>
      </div>

      <div class="gearrow"><button id="gearBtn" class="gear" type="button" aria-expanded="${state.gearOpen ? 'true' : 'false'}" title="What happens by itself, prompts and specifics, and the manual Confirm &amp; link">⚙ Options${state.gearOpen ? '' : ' · <span class="gearsum">' + esc(autoSummary()) + '</span>'}</button></div>

      <div class="gearbox" ${state.gearOpen ? '' : 'hidden'}>
      <div class="autoline">By itself: <b>${esc(autoSummary())}</b><button class="edit" id="autoEdit" type="button" aria-expanded="${state.autoEditOpen ? 'true' : 'false'}">${state.autoEditOpen ? 'Done' : 'Change'}</button></div>
      ${state.autoEditOpen ? `<div class="toggles-body autoinline" id="autoInline">${autoRows('i_')}</div>` : ''}

      <details class="adv" id="advBox" ${state.advOpen ? 'open' : ''}><summary>Advanced \u2014 prompts, specifics, manual link</summary>
        <div class="inner">
          <div><h3>AI photoshop prompt <span class="muted" id="aiPromptWhere">${state.promptDraft?.upc === info.upc ? 'this item only' : (state.aiPrompt ? 'saved for all items' : 'the default prompt')}</span></h3>
            <textarea id="aiPrompt" rows="3">${esc(promptFor(info))}</textarea>
            <div class="row tight"><button id="aiPromptSave" class="tiny" type="button" title="Keep this prompt for every item and every session">Save for all</button><button id="aiPromptReset" class="tiny" type="button" title="Back to the default prompt for every item">Reset</button></div></div>
          <div><h3>AI title prompt${state.titlePrompt ? (state.titlePrompt === state.savedTitlePrompt ? ' <span class="muted">saved</span>' : ' <span class="muted">this session</span>') : ''}</h3>
            <textarea id="titlePrompt" rows="3" placeholder="Extra instructions for the AI title, e.g. always put the size at the end">${esc(state.titlePrompt)}</textarea>
            <div class="row tight"><button id="titlePromptSave" class="tiny" type="button" title="Keep this prompt for every session, on every item">Save for all</button><button id="titlePromptReset" class="tiny" type="button" title="Back to the built-in title prompt">Reset</button></div></div>
          ${aspects.length ? `<div><h3>Item specifics (${aspects.length})</h3><div class="chips">${aspects.map(([k, vals]) => `<code title="click to copy" data-copy="${esc(vals[0])}">${esc(k)}: ${esc(vals.join(' / '))}</code>`).join('')}</div></div>` : ''}
        </div></details>
      </div>
      `;
    // Nothing about the item changed: leave the card alone, photos, open boxes, caret and all.
    if (!setHtml(el, html)) return;

    if ($('markExisting')) $('markExisting').onclick = () => markExisting(item);
    $('gearBtn').onclick = () => { state.gearOpen = !state.gearOpen; renderDetail(); renderConfirm(); };
    $('advBox').ontoggle = () => { state.advOpen = $('advBox').open; };
    $('autoEdit').onclick = () => { state.autoEditOpen = !state.autoEditOpen; renderDetail(); if (state.autoEditOpen) $('autoInline')?.scrollIntoView({ block: 'nearest' }); };
    if ($('autoInline')) wireAutoChecks($('autoInline'));
    for (const button of el.querySelectorAll('button[data-transcribe]')) button.onclick = () => transcribe(info.upc, Number(button.dataset.transcribe), Boolean(button.dataset.again));
    for (const button of el.querySelectorAll('button[data-locpv]')) button.onclick = () => locPreview.open(button.dataset.locpv);
    for (const button of el.querySelectorAll('button[data-play]')) button.onclick = () => {
      const player = $('notePlayer');
      if (player.src === button.dataset.play && !player.paused) { player.pause(); button.textContent = '\u25b6'; return; }
      for (const b of el.querySelectorAll('button[data-play]')) b.textContent = '\u25b6';
      player.src = button.dataset.play; void player.play().catch(() => toast('Could not play the recording', true)); button.textContent = '\u23f8';
      player.onended = () => { button.textContent = '\u25b6'; };
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
    $('photoRefresh').onclick = () => { delete state.details[info.upc]; void loadDetail(info.upc, { force: true }); };
    for (const box of el.querySelectorAll('input[data-select]')) box.onchange = () => { if (box.checked) state.selectedPhotos.add(box.dataset.select); else state.selectedPhotos.delete(box.dataset.select); box.closest('.photo').classList.toggle('selected', box.checked); };
    for (const tileEl of el.querySelectorAll('.photo')) {
      const img = tileEl.querySelector('img');
      const flagSize = () => { if (!img.naturalWidth) return; const size = { w: img.naturalWidth, h: img.naturalHeight }; state.photoSizes[tileEl.dataset.url] = size; if (isTooSmall(size)) { tileEl.querySelector('.tag.small').hidden = false; tileEl.classList.add('too-small'); } };
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
    $('aiPrompt').oninput = () => {
      state.promptDraft = { upc: info.upc, text: $('aiPrompt').value };
      $('aiPromptWhere').textContent = 'this item only';
    };
    $('aiPromptSave').onclick = () => {
      const text = ($('aiPrompt').value || '').trim();
      state.aiPrompt = text === (info.aiPhotoPrompt || '') ? '' : text;
      state.promptDraft = null; remember(); renderDetail();
      toast(state.aiPrompt ? 'Photoshop prompt saved for every item and session' : 'Prompt back to the default for every item');
    };
    $('aiPromptReset').onclick = () => { state.aiPrompt = ''; state.promptDraft = null; remember(); renderDetail(); };
    $('titlePrompt').oninput = () => { state.titlePrompt = $('titlePrompt').value; };
    $('titlePromptSave').onclick = async () => {
      state.titlePrompt = $('titlePrompt').value.trim();
      state.savedTitlePrompt = state.titlePrompt;
      await chrome.storage.local.set({ [TITLE_PROMPT_KEY]: state.savedTitlePrompt });
      toast(state.savedTitlePrompt ? 'Title prompt saved for every session' : 'Back to the built-in title prompt');
      renderDetail();
    };
    $('titlePromptReset').onclick = async () => {
      state.titlePrompt = ''; state.savedTitlePrompt = '';
      await chrome.storage.local.set({ [TITLE_PROMPT_KEY]: '' });
      renderDetail();
    };
    $('addPhoto').onclick = () => { state.qrOpen = !state.qrOpen; renderDetail(); };
    $('photoLink').onclick = () => sendPhotoLink(info);
    for (const code of el.querySelectorAll('code[data-copy]')) code.onclick = () => copy(code.dataset.copy, 'Copied ' + code.dataset.copy);
    renderActionBar();
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
    if (!item || !info || state.view !== 'item') { el.hidden = true; return; }
    const page = state.page || {};
    const platform = page.store || state.platform;
    const v = values(info);
    const linked = info.links || [];
    const lastLink = state.lastLink && state.lastLink.upc === info.upc ? state.lastLink : null;
    const successPage = page.kind === 'listing-success' || page.kind === 'offer-success' || page.kind === 'listing-live';
    // The store just confirmed (or we recorded a link): the manual card is the thing to look at,
    // so open the gear for them instead of leaving it behind the button.
    if ((successPage || lastLink) && !state.gearOpen) { state.gearOpen = true; queueMicrotask(renderDetail); }
    el.hidden = !item || !info || state.view !== 'item' || !state.gearOpen;
    if (el.hidden) return;
    const html = `
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
    // Same card as a moment ago: keep the boxes, and whatever is half-typed in them.
    if (!setHtml(el, html)) return;
    for (const radio of el.querySelectorAll('input[name="cfPlatform"]')) radio.onchange = () => { $('cfListingIdLabel').hidden = radio.value === 'amazon'; $('cfAsinLabel').hidden = radio.value === 'ebay'; };
    for (const id of ['cfPrice', 'cfQuantity']) $(id).oninput = () => { $(id).dataset.touched = '1'; };
    $('confirmBtn').onclick = () => confirmLink();
    if ($('undoLink')) $('undoLink').onclick = () => undoLink(lastLink.id);
  }

  async function copy(text, message) {
    try { await navigator.clipboard.writeText(text); toast(message || 'Copied'); }
    catch { toast('Clipboard blocked; select the text under "Text for manual paste"', true); }
  }

  // -- "+ NEW": an item that is on no BOL yet -------------------------------------------------
  // Two people fill one draft. The phone says what the thing is and photographs it; the panel gives
  // it a barcode, circles what is wrong with it and submits. Submitting is an Item Prep pass: the
  // name, the picture, the photos and the notes land on the barcode, and the unit joins Items to
  // List, so from the next render it is an ordinary queued item.

  const newDraft = () => state.newItem.draft;

  async function newLoad(id, { quiet = false } = {}) {
    const done = quiet ? null : busy('Loading the new item…');
    try {
      const data = await api('/api/lister/new/' + id);
      state.newItem.draft = data.draft;
      state.newItem.id = data.draft.id;
      remember();
    } catch (error) {
      if (error.status === 404) { state.newItem.draft = null; state.newItem.id = null; remember(); }
      else if (!quiet) toast(error.message, true);
    }
    if (done) done();
    renderNew();
  }

  async function newStart() {
    setView('new');
    if (newDraft() && newDraft().status === 'draft') { renderNew(); newPoll(); return; }
    const done = busy('Opening a new item…');
    state.newItem.opening = true;
    renderNew();
    try {
      // The draft opens without the phone link, so the card is ready to scan at once; the link is a
      // round trip to Telegram and goes out beside it.
      const data = await api('/api/lister/new', { method: 'POST', body: { send: false } });
      state.newItem.draft = data.draft;
      state.newItem.id = data.draft.id;
      state.newItem.barcodeDraft = '';
      remember();
      state.newItem.linking = newSendLink(data.draft.id);
    } catch (error) {
      toast(error.message, true);
    }
    state.newItem.opening = false;
    done();
    renderNew();
    newPoll();
  }

  // stage: undefined = wherever the draft needs the phone next; 'title' = from the beginning;
  // 'photos' = straight to the camera.
  async function newSendLink(id, stage) {
    try {
      const data = await api('/api/lister/new/' + id + '/link', { method: 'POST', body: stage ? { stage } : {} });
      if (newDraft() && newDraft().id === id && data.draft) { state.newItem.draft = data.draft; renderNew(); }
      toast('\uD83D\uDCF2 Link sent' + ((data.sent || []).length ? ' to ' + data.sent.join(', ') : '') + ' on Telegram' + everyoneHint(data));
    } catch (error) {
      if (newDraft() && newDraft().id === id) toast('The phone was not sent a link: ' + error.message, true);
    }
  }

  // The phone fills the same draft from the other end, so the card has to keep looking.
  function newPoll() {
    clearTimeout(state.newItem.timer);
    state.newItem.timer = setTimeout(async () => {
      const draft = newDraft();
      if (state.view !== 'new' || !draft || draft.status !== 'draft') return;
      await newLoad(draft.id, { quiet: true });
      newPoll();
    }, 4000);
  }

  async function newPost(path, body, label) {
    const draft = newDraft();
    if (!draft) return null;
    const done = busy(label);
    try {
      const data = await api('/api/lister/new/' + draft.id + path, { method: 'POST', body: body || {} });
      if (data.draft) state.newItem.draft = data.draft;
      return data;
    } catch (error) {
      toast(error.message, true);
      return null;
    } finally {
      done();
      renderNew();
    }
  }

  async function newSetBarcode(barcode, kind) {
    // A known code swaps the phone's first link for a camera one, so that first link has to be out.
    if (state.newItem.linking) await state.newItem.linking;
    const data = await newPost('/barcode', { barcode, kind: kind || 'scanned' }, 'Saving the barcode…');
    if (!data) return false;
    state.newItem.barcodeDraft = '';
    state.newItem.recode = false;
    renderNew();
    if (data.systemTitle) toast('We already know this code: ' + data.systemTitle);
    if (data.link && data.link.kind === 'photos') toast('Name found — the phone was re-sent a photos-only link');
    if (data.linkError) toast('The new link did not send: ' + data.linkError, true);
    return true;
  }

  // The blue Phone pills. The one by Name & details sends a link that starts the phone from the
  // beginning (name, details, photos); the one by Photos sends it straight to the camera. The pill
  // pressed spins while Telegram answers.
  async function newPhoneLink(stage) {
    const draft = newDraft();
    if (!draft || state.newItem.linkBusy) return;
    state.newItem.linkBusy = stage;
    renderNew();
    const done = busy(stage === 'photos' ? 'Sending the phone the camera link\u2026' : 'Sending the phone its link\u2026');
    try { await newSendLink(draft.id, stage); } finally { done(); state.newItem.linkBusy = false; renderNew(); }
  }

  function newPhonePill(id, stage, title) {
    const spinning = state.newItem.linkBusy === stage;
    return '<button id="' + id + '" class="tiny phone-btn" type="button" title="' + esc(title) + '"' +
      (state.newItem.linkBusy ? ' disabled' : '') + '>' +
      (spinning ? '<span class="spin"></span>' : '<span class="phone-ico" aria-hidden="true">\ud83d\udcf1</span>Phone') + '</button>';
  }

  // Item Prep's status and defect bubbles. The phone fills them from what was said; a tap here
  // decides. Good clears the defects, a defect on a Good item makes it Bad.
  const NEW_STATUSES = [['good', 'Good'], ['bad', 'Bad'], ['return', 'Return']];
  const NEW_DEFECTS = ['Missing pieces', 'Broken', 'Box damage', 'Replacement', 'Other'];

  function newVerdictHtml(draft) {
    const status = draft.prepStatus || 'good';
    const chosen = new Set(draft.defects || []);
    const choices = (draft.defectChoices && draft.defectChoices.length) ? draft.defectChoices : NEW_DEFECTS;
    return '<span class="lbl">Status</span><div class="vbubbles" id="newStatus">' +
      NEW_STATUSES.map(([value, label]) => '<button type="button" class="vb st-' + value + (value === status ? ' on' : '') +
        '" data-status="' + value + '" aria-pressed="' + (value === status) + '">' + label + '</button>').join('') + '</div>' +
      '<span class="lbl">Defects</span><div class="vbubbles" id="newDefects">' +
      choices.map(name => '<button type="button" class="vb df' + (chosen.has(name) ? ' on' : '') +
        '" data-defect="' + esc(name) + '" aria-pressed="' + chosen.has(name) + '">' + esc(name) + '</button>').join('') + '</div>';
  }

  function newWireVerdict() {
    const save = (prepStatus, defects) => void newPost('/fields', { prepStatus, defects }, 'Saving the status\u2026');
    $('newStatus').onclick = event => {
      const button = event.target.closest('[data-status]');
      const draft = newDraft();
      if (!button || !draft) return;
      const status = button.dataset.status;
      save(status, status === 'good' ? [] : (draft.defects || []));
    };
    $('newDefects').onclick = event => {
      const button = event.target.closest('[data-defect]');
      const draft = newDraft();
      if (!button || !draft) return;
      const picked = new Set(draft.defects || []);
      const name = button.dataset.defect;
      if (picked.has(name)) picked.delete(name); else picked.add(name);
      const status = draft.prepStatus || 'good';
      save(picked.size && status === 'good' ? 'bad' : status, [...picked]);
    };
  }

  async function newPrintLabel(code, description) {
    const done = busy('Sending the label…');
    try {
      await api('/api/printer/print-barcode', { method: 'POST', body: { upc: code, item_description: description || '', quantity: 1 } });
      state.newItem.printed[code] = 'printed';
      renderNew();
      toast('Label sent to the printer');
      return true;
    } catch (error) {
      // A queued label still prints later from the print queue page, the way Prep + does it.
      try {
        await api('/api/print-queue', { method: 'POST', body: { title: description || 'New item', barcode: code } });
        if (state.newItem.printed[code] !== 'printed') state.newItem.printed[code] = 'queued';
        renderNew();
        toast((error.message || 'The printer did not answer.') + ' Added to the print queue instead.', true);
      } catch {
        toast(error.message, true);
      }
      return false;
    } finally {
      done();
    }
  }

  // Generate: the next code of ours goes on the item and its label prints straight away.
  async function newGenerateCode() {
    const done = busy('Taking the next code\u2026');
    let code = '';
    try {
      code = (await api('/api/items-prep/generate-barcode', { method: 'POST', body: {} })).barcode || '';
    } catch (error) {
      toast(error.message, true);
    } finally {
      done();
    }
    if (!code || !(await newSetBarcode(code, 'generated'))) return;
    await newPrintLabel(code, (newDraft() || {}).title);
  }

  // Print for a code typed into the box: it becomes the item's code, then its label prints.
  async function newPrintTyped(code) {
    code = String(code || '').trim();
    if (!code) { toast('Type or scan a code first, or press Generate.', true); return; }
    const draft = newDraft();
    if (!draft || draft.upc !== code) {
      // Typed by hand it is still the item's own code; a 777 one is ours.
      if (!(await newSetBarcode(code, /^777\d{9}$/.test(code) ? 'generated' : 'scanned'))) return;
    }
    await newPrintLabel(code, (newDraft() || {}).title);
  }

  async function newSubmit() {
    const data = await newPost('/submit', {}, 'Submitting…');
    if (!data) return;
    clearTimeout(state.newItem.timer);
    state.newItem.draft = null;
    state.newItem.id = null;
    state.currentUpc = data.upc;
    remember();
    toast('"' + (data.title || data.upc) + '" is on Items to List');
    setView('list');
    await loadQueue();
  }

  async function newCancel() {
    if (!newDraft()) return;
    const ok = await confirmModal({ title: 'Throw this new item away?', text: 'The photos the phone already sent go with it. The barcode stays reserved, so it is never handed out twice.', okLabel: 'Throw away' });
    if (!ok) return;
    await newPost('/cancel', {}, 'Closing the draft…');
    clearTimeout(state.newItem.timer);
    state.newItem.draft = null;
    state.newItem.id = null;
    remember();
    setView('list');
  }

  // Start over: the old draft (last item's barcode, name, photos) is thrown away and a blank one
  // opens, with a fresh link to the phone.
  async function newRestart() {
    const draft = newDraft();
    const hasInfo = draft && (draft.upc || draft.title || draft.description || (draft.photos || []).length);
    if (hasInfo) {
      const ok = await confirmModal({ title: 'Start over with a blank item?', text: 'What is on this card now, and the photos the phone sent, is thrown away. A used barcode stays reserved.', okLabel: 'Start over' });
      if (!ok) return;
    }
    // Closing the old draft is one quick write; its Telegram message comes down on the server
    // after the answer, so nothing here waits on Telegram.
    if (draft) await newPost('/cancel', {}, 'Clearing the old item…');
    clearTimeout(state.newItem.timer);
    state.newItem.draft = null;
    state.newItem.id = null;
    state.newItem.barcodeDraft = '';
    state.newItem.recode = false;
    // A blank card after a blank card is the same markup, and setHtml would keep the old boxes -
    // with the half-typed barcode and name still in them. Forget them so it draws afresh.
    for (const id of ['newHead', 'newFields', 'newPhotos']) written.delete($(id));
    remember();
    await newStart();
  }

  // -- marking a photo ------------------------------------------------------------------------
  // Circle the damage and say what it is. The photo the phone took is kept: the circled one is an
  // extra photo beside it, and the words become a prep note, which is where the listing reads its
  // condition and condition description from.

  function openEditor(photo) {
    const draft = newDraft();
    if (!draft) return;
    const box = $('editor');
    box.hidden = false;
    box.innerHTML = '<div class="edbox" role="dialog" aria-modal="true">' +
      '<div class="edhead"><strong class="grow">Mark this photo</strong>' +
      '<button id="edClose" class="icon" type="button" aria-label="Close">×</button></div>' +
      '<div class="edstage"><canvas id="edCanvas"></canvas></div>' +
      '<div class="edtools"><button id="edUndo" class="tiny" type="button">↶ Undo</button>' +
      '<button id="edClear" class="tiny" type="button">Clear</button>' +
      '<span class="muted small grow">Drag across the damage</span></div>' +
      '<label class="fieldlabel" for="edNote">What is wrong with it?</label>' +
      '<textarea id="edNote" rows="2" placeholder="e.g. Cracked corner, lid is scratched"></textarea>' +
      '<div class="row"><button id="edSave" class="primary grow" type="button">Save</button>' +
      '<button id="edDelete" class="danger" type="button">Delete photo</button></div></div>';
    $('edNote').value = photo.note || '';

    const canvas = $('edCanvas');
    const context = canvas.getContext('2d');
    const image = new Image();
    const shapes = [];
    let drawing = null;

    const paint = () => {
      if (!image.naturalWidth) return;
      context.clearRect(0, 0, canvas.width, canvas.height);
      context.drawImage(image, 0, 0, canvas.width, canvas.height);
      const width = Math.max(3, Math.round(canvas.width / 110));
      context.lineWidth = width;
      context.strokeStyle = '#e11d48';
      context.shadowColor = 'rgba(0, 0, 0, .35)';
      context.shadowBlur = width;
      for (const shape of drawing ? shapes.concat([drawing]) : shapes) {
        context.beginPath();
        context.ellipse(shape.x, shape.y, Math.max(shape.rx, 4), Math.max(shape.ry, 4), 0, 0, Math.PI * 2);
        context.stroke();
      }
      context.shadowBlur = 0;
    };

    image.onload = () => {
      // The saved file is this canvas, so cap it here rather than push a twelve megapixel JPEG
      // back out through the side panel.
      const cap = 1400;
      const scale = Math.min(1, cap / Math.max(image.naturalWidth, image.naturalHeight));
      canvas.width = Math.round(image.naturalWidth * scale);
      canvas.height = Math.round(image.naturalHeight * scale);
      paint();
    };
    image.onerror = () => toast('That photo could not be opened for marking', true);
    // The bytes come through the API rather than off the <img> URL: a picture loaded straight from
    // another origin taints the canvas, and a tainted canvas cannot hand back the marked copy.
    photoFile(photo.url)
      .then(data => { image.src = 'data:' + (data.type || 'image/jpeg') + ';base64,' + data.base64; })
      .catch(error => toast('That photo could not be opened for marking: ' + error.message, true));

    const at = event => {
      const rect = canvas.getBoundingClientRect();
      return { x: (event.clientX - rect.left) * (canvas.width / rect.width),
               y: (event.clientY - rect.top) * (canvas.height / rect.height) };
    };
    canvas.onpointerdown = event => {
      event.preventDefault();
      try { canvas.setPointerCapture(event.pointerId); } catch (error) { /* mouse without capture */ }
      const start = at(event);
      drawing = { x: start.x, y: start.y, rx: 0, ry: 0, from: start };
      paint();
    };
    canvas.onpointermove = event => {
      if (!drawing) return;
      const now = at(event);
      drawing.x = (drawing.from.x + now.x) / 2;
      drawing.y = (drawing.from.y + now.y) / 2;
      drawing.rx = Math.abs(now.x - drawing.from.x) / 2;
      drawing.ry = Math.abs(now.y - drawing.from.y) / 2;
      paint();
    };
    const finish = () => {
      // A tap is not a circle. The threshold is in screen pixels, not image ones, because what
      // decides whether it was a slip is how far the finger actually moved.
      const rect = canvas.getBoundingClientRect();
      const least = 4 * (canvas.width / (rect.width || canvas.width));
      if (drawing && drawing.rx > least && drawing.ry > least) shapes.push(drawing);
      drawing = null;
      paint();
    };
    canvas.onpointerup = finish;
    canvas.onpointercancel = finish;

    const close = () => { box.hidden = true; box.innerHTML = ''; };
    $('edClose').onclick = close;
    $('edUndo').onclick = () => { shapes.pop(); paint(); };
    $('edClear').onclick = () => { shapes.length = 0; paint(); };
    $('edDelete').onclick = async () => {
      const ok = await confirmModal({ title: 'Delete this photo?', text: 'It goes with the draft, and the phone can send another.' });
      if (!ok) return;
      close();
      const done = busy('Deleting the photo…');
      try {
        const data = await api('/api/lister/new/' + draft.id + '/photos/' + photo.id, { method: 'DELETE' });
        state.newItem.draft = data.draft;
      } catch (error) { toast(error.message, true); }
      done();
      renderNew();
    };
    $('edSave').onclick = async () => {
      $('edSave').disabled = true;
      // No circles means no marked copy: an empty image tells the server to drop the old one.
      const marked = shapes.length && image.naturalWidth ? canvas.toDataURL('image/jpeg', 0.92) : '';
      const data = await newPost('/photos/' + photo.id, { image: marked, note: $('edNote').value }, 'Saving the marks…');
      $('edSave').disabled = false;
      if (data) close();
    };
  }

  // -- the "+ NEW" card -----------------------------------------------------------------------
  // One card, three numbered steps, each saying whose turn it is: the barcode is yours, the name
  // and the photos are the phone's. A step shows its state as one coloured dot (red = do this,
  // amber = the phone is on it, green = done) and the action bar names what is next. Each step
  // renders into its own container, so a photo arriving never rebuilds a field being typed in.

  const STAGE_WORDS = { title: 'naming it', details: 'adding details', photos: 'taking photos', done: 'finished' };
  const TITLE_SOURCE = { system: 'from our own records', voice: 'dictated on the phone', typed: 'typed here' };
  const PRINTER_SVG = '<svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">' +
    '<path d="M6 9V3h12v6"/><rect x="3" y="9" width="18" height="8" rx="2"/><rect x="7" y="14" width="10" height="7"/><circle cx="17.5" cy="12" r=".6" fill="currentColor"/></svg>';

  // A step with a control on its right keeps its status on the next line, so a pill never eats it.
  function stepHtml({ n, tone, label, sub, right, body }) {
    const status = sub ? '<span class="sub' + (right ? ' below' : '') + '" title="' + esc(sub) + '">' + esc(sub) + '</span>' : '';
    return '<div class="step ' + tone + '"><div class="rail"><span class="dot">' + (tone === 'done' ? '\u2713' : n) + '</span></div>' +
      '<div class="head"><b>' + label + '</b>' + (right ? '<span class="sp"></span>' + right : status) + '</div>' +
      (right ? status : '') +
      '<div class="body">' + (body || '') + '</div></div>';
  }

  function renderNew() {
    $('newCard').hidden = state.view !== 'new';
    if (state.view !== 'new') return;
    const draft = newDraft();
    $('newEmpty').hidden = Boolean(draft);
    $('newSteps').hidden = !draft;
    if (!draft) {
      const empty = state.newItem.opening ? '<div class="empty"><strong>Opening a new item\u2026</strong></div>'
        : '<div class="empty"><strong>No new item open</strong>' +
          '<p class="muted small">For something that is on no BOL: you scan the barcode here, the phone names it and takes the photos.</p>' +
          '<button id="newOpen" class="primary" type="button">Start a new item</button></div>';
      if (setHtml($('newEmpty'), empty) && $('newOpen')) $('newOpen').onclick = () => void newStart();
      renderActionBar();
      return;
    }
    renderNewCode(draft);
    renderNewFields(draft);
    renderNewPhotos(draft);
    renderActionBar();
  }

  // The big label button, in the state its label is in: sent to the printer, parked in the print
  // queue, or not printed yet. It always prints again when pressed.
  function printButton(printed) {
    const look = printed === 'printed' ? { cls: ' printed', text: 'PRINTED \u2713', tip: 'The label went to the printer. Press to print it again.' }
      : printed === 'queued' ? { cls: ' queued', text: 'IN PRINT QUEUE', tip: 'The printer did not answer, so the label waits on the print queue page. Press to try the printer again.' }
      : { cls: '', text: 'Print label', tip: 'Print this barcode on the Item Prep printer' };
    return '<button id="newPrint" class="printbtn' + look.cls + '" type="button" title="' + look.tip + '">' + PRINTER_SVG + '<span>' + look.text + '</span></button>';
  }

  // Step 1, the only one done at the bench: scan the code, or Generate one of ours (it prints on its
  // own), or type one and press Print. No popup: everything sits on the card.
  function renderNewCode(draft) {
    const editing = !draft.upc || state.newItem.recode;
    const body = !editing
      ? '<div class="row tight"><b class="mono grow">' + esc(draft.upc) + '</b>' +
        '<button id="newRecode" class="tiny" type="button" title="Put a different code on it">Change</button></div>' +
        printButton(state.newItem.printed[draft.upc])
      : '<div class="row tight"><div class="search grow"><span aria-hidden="true">\u2337</span>' +
        '<input id="newBarcode" type="text" inputmode="numeric" autocomplete="off" spellcheck="false" placeholder="Scan or type the barcode"></div>' +
        '<button id="newGen" type="button" title="Take the next code of ours; its label prints on its own">Generate</button></div>' +
        '<button id="newPrintTyped" class="printbtn" type="button" title="Put the code typed above on the item and print its label" disabled>' +
        PRINTER_SVG + '<span>Print label</span></button>' +
        (draft.upc ? '<button id="newRecodeCancel" class="tiny" type="button">Keep ' + esc(draft.upc) + '</button>' : '');
    const html = stepHtml({
      n: 1, tone: draft.upc ? 'done' : 'need', label: 'Barcode',
      sub: draft.upc ? (draft.upcKind === 'generated' ? 'ours, printed' : 'the item\u2019s own') : 'scan it now',
      right: '<button id="newRestart" class="tiny" type="button" title="Throw this item away and start a blank one">\u21bb Start over</button>',
      body });
    if (!setHtml($('newHead'), html)) return;
    const field = $('newBarcode');
    if (field) {
      field.value = state.newItem.barcodeDraft || '';
      const printTyped = $('newPrintTyped');
      printTyped.disabled = !field.value.trim();
      field.oninput = () => { state.newItem.barcodeDraft = field.value; printTyped.disabled = !field.value.trim(); };
      printTyped.onclick = () => void newPrintTyped(field.value);
      // A scanner types the code and presses Enter for you; a person can too.
      field.onkeydown = event => {
        if (event.key !== 'Enter') return;
        event.preventDefault();
        const code = field.value.trim();
        if (code) void newSetBarcode(code, /^777\d{9}$/.test(code) ? 'generated' : 'scanned');
      };
      field.focus();
    }
    if ($('newGen')) $('newGen').onclick = () => void newGenerateCode();
    if ($('newPrint')) $('newPrint').onclick = () => void newPrintLabel(draft.upc, draft.title);
    if ($('newRecode')) $('newRecode').onclick = () => { state.newItem.recode = true; state.newItem.barcodeDraft = ''; renderNew(); };
    if ($('newRecodeCancel')) $('newRecodeCancel').onclick = () => { state.newItem.recode = false; renderNew(); };
    $('newRestart').onclick = () => void newRestart();
  }

  // Step 2 is the phone's: the pill messages it the link, the fields fill in as it dictates. They
  // stay editable here for a quick fix.
  function renderNewFields(draft) {
    const onPhone = draft.linkSent && draft.status === 'draft';
    const source = TITLE_SOURCE[draft.titleSource];
    const doing = onPhone && !draft.title && STAGE_WORDS[draft.stage] ? 'the phone is ' + STAGE_WORDS[draft.stage] + '\u2026' : '';
    const sub = draft.title ? (source || '') : (doing || (onPhone ? 'waiting for the phone' : 'press Phone to send the link'));
    const tone = draft.title ? 'done' : (onPhone ? 'wait' : 'need');
    const pill = newPhonePill('newPhoneBtn', 'title',
      'Telegram the phone a link that starts from the beginning: name, details, then photos');
    const body = '<label class="lbl" for="newTitle">Name</label>' +
      '<input id="newTitle" class="wide" type="text" placeholder="Dictated on the phone" value="' + esc(draft.title) + '">' +
      '<label class="lbl" for="newDesc">Details</label>' +
      '<textarea id="newDesc" rows="3" placeholder="Dictated on the phone: what it is, what is in the box">' + esc(draft.description) + '</textarea>' +
      newVerdictHtml(draft);
    const html = stepHtml({ n: 2, tone, label: 'Name & details', sub, right: pill, body });
    if (!setHtml($('newFields'), html)) return;
    $('newPhoneBtn').onclick = () => void newPhoneLink('title');
    newWireVerdict();
    for (const [id, key, label] of [['newTitle', 'title', 'Saving the name\u2026'], ['newDesc', 'description', 'Saving the details\u2026']]) {
      const box = $(id);
      box.onchange = () => { void newPost('/fields', { [key]: box.value, source: 'typed' }, label); };
    }
  }

  // Step 3: the phone's photos land here on their own; a tap on one opens the marker.
  function renderNewPhotos(draft) {
    const photos = draft.photos || [];
    const marked = photos.filter(p => p.markedUrl).length;
    const waiting = draft.linkSent && draft.status === 'draft' && draft.stage === 'photos';
    const tiles = photos.map(photo =>
      '<figure class="ptile' + (photo.markedUrl ? ' marked' : '') + '" data-photo="' + photo.id + '" title="' + esc(photo.note || 'Circle what is wrong') + '">' +
      '<img src="' + esc(serverBase() + (photo.markedUrl || photo.url)) + '" alt="" loading="lazy">' +
      '<figcaption>' + (photo.note ? esc(photo.note) : '<span class="muted">\u270e mark</span>') + '</figcaption></figure>').join('');
    const body = photos.length
      ? '<div class="ptiles">' + tiles + '</div>' + (marked < photos.length ? '<div class="hint">Tap a photo to circle what is wrong with it.</div>' : '')
      : '<div class="hint">' + (waiting ? 'They land here as the phone takes them.' : 'The phone takes them after the name.') + '</div>';
    const html = stepHtml({
      n: 3, tone: photos.length ? 'done' : (waiting ? 'wait' : 'todo'), label: 'Photos',
      sub: photos.length ? String(photos.length) + (marked ? ' \u00b7 ' + marked + ' marked' : '') : (waiting ? 'the phone is taking photos\u2026' : 'not yet'),
      right: newPhonePill('newPhotoPhoneBtn', 'photos', 'Telegram the phone a link straight to the camera'),
      body });
    if (!setHtml($('newPhotos'), html)) return;
    $('newPhotoPhoneBtn').onclick = event => { event.stopPropagation(); void newPhoneLink('photos'); };
    $('newPhotos').onclick = event => {
      const tile = event.target.closest('[data-photo]');
      if (!tile) return;
      const photo = ((newDraft() || {}).photos || []).find(p => String(p.id) === tile.dataset.photo);
      if (photo) openEditor(photo);
    };
  }

  // -- wiring --------------------------------------------------------------------------------

  function setPlatform(platform) {
    if (state.platform === platform) return;
    state.platform = platform; state.report = null; state.guide = null; remember();
    void loadQueue({ keep: false });
  }

  function setView(view) {
    state.view = view; renderStoreBar(); renderDetail(); renderConfirm(); renderNew();
    if (view !== 'new') clearTimeout(state.newItem.timer);
    if (view === 'item' && state.currentUpc) void loadDetail(state.currentUpc);
  }

  function wireStatic() {
    $('themeBtn').onclick = () => { void saveSettings({ ...state.settings, theme: state.settings.theme === 'dark' ? 'light' : 'dark' }); };
    $('settingsBtn').onclick = () => {
      const s = $('settings'); s.hidden = !s.hidden;
      $('setServer').value = state.settings.server; $('setActor').value = state.settings.actor;
      for (const key of ['autoFill', 'autoGuide', 'autoLink', 'autoPrepare']) $('set' + key[0].toUpperCase() + key.slice(1)).checked = state.settings[key] !== false;
      renderAutoMenu();
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
    $('storeNew').onclick = () => void newStart();
    $('backToQueue').onclick = () => { state.locked = null; setView('list'); };
    $('goBtn').onclick = () => { const action = nextAction(); if (!action.disabled && action.run) void action.run(); };
    $('moreBtn').onclick = () => openMore();
    $('listedBtn').onclick = () => openListed($('listedDrawer').hidden);
    $('listedClose').onclick = () => openListed(false);
    $('listedReload').onclick = () => void loadListed();
    for (const b of document.querySelectorAll('#listedDrawer [data-range]')) b.onclick = () => { state.listedRange = b.dataset.range; renderListed(); };
    for (const b of document.querySelectorAll('#listedDrawer [data-store]')) b.onclick = () => { state.listedStore = b.dataset.store; renderListed(); };
    document.addEventListener('keydown', event => {
      if (event.key !== 'Escape') return;
      if (!$('listedDrawer').hidden) openListed(false);
      else if (!$('editor').hidden) { $('editor').hidden = true; $('editor').innerHTML = ''; }
      else if (!$('modal').hidden) $('modal').hidden = true;
    });
    $('filter').oninput = () => { state.filter = $('filter').value; renderItems(); };
    for (const [id, value] of [['statusAll', 'all'], ['statusFlagged', 'flagged'], ['statusListed', 'listed'], ['statusBlocked', 'blocked']]) $(id).onclick = () => { state.statusFilter = value; renderItems(); };
    $('preloadAll').onclick = () => void preloadAll();
    $('clearAll').onclick = () => void clearAll();
    $('addItems').onclick = () => chrome.tabs.create({ url: serverBase() + '/items-to-list' });
    chrome.tabs.onActivated.addListener(scheduleRefresh);
    chrome.tabs.onUpdated.addListener((tabId, info, tab) => {
      if (info.status === 'loading' && !info.url) {
        for (const set of [state.autoFilled, state.autoText]) for (const key of [...set]) if (key.startsWith(tabId + '|')) set.delete(key);
        if (state.tab?.id === tabId) state.guide = null;
      }
      if (tab?.active && (info.status === 'complete' || info.url)) scheduleRefresh();
    });
    chrome.windows?.onFocusChanged?.addListener(() => scheduleRefresh());
    setInterval(renderBusy, 30000);
    // "4 min ago" on the unfinished listings goes stale on its own; keep it honest.
    setInterval(renderSessions, 60000);
    chrome.tabs.onRemoved.addListener(tabId => { delete state.tabItems[tabId]; try { void chrome.storage.session?.set({ ssListerTabs: state.tabItems }); } catch { /* ignore */ } });
    // Store pages render their forms after load; look again a little later.
    chrome.tabs.onUpdated.addListener((tabId, info, tab) => { if (tab?.active && info.status === 'complete') setTimeout(scheduleRefresh, 2500); });
  }

  async function main() {
    await loadStorage();
    wireStatic();
    applyTheme();
    renderAll();
    await connect();
    // The queue first, so a panel opened on the store's search page already has an item to search.
    await loadQueue();
    await refreshTab();
    watchPhonePhotos();
    // A draft left open in an earlier session is still being filled by somebody's phone.
    if (state.newItem.id) await newLoad(state.newItem.id, { quiet: true });
  }

  void main();
})();
