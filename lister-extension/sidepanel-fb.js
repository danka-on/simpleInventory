// FB tab of the Sweet Shelves Lister: the Facebook Marketplace list.
//
// Facebook has no listing API for individual sellers, so nothing is filled on a store page here.
// Items land on this list when "+" is pressed on Items to List while this tab is open. Each one is
// reviewed (title, whole-dollar price, one of Facebook's four conditions, a Facebook category,
// a plain-text description) and marked Ready. "Build workbook" puts up to 50 ready items into
// Facebook's own bulk-upload file, which is downloaded and uploaded on Facebook by hand. Facebook's
// file has no photos: they are added on each listing afterwards ("Save photos" downloads them).
// "Mark listed" then records the whole workbook as listed on Facebook.
//
// Works through the panel's own helpers (globalThis.SSListerPanel, set up in sidepanel.js).
(() => {
  'use strict';
  const OPEN_KEY = 'ssListerFbOpen';
  const P = () => globalThis.SSListerPanel;
  const $ = id => document.getElementById(id);
  const esc = value => P().esc(value);
  const STATUS = { queued: 'To review', ready: 'Ready', in_template: 'In workbook' };

  const fb = {
    open: false, items: [], counts: {}, batches: [], listedToday: 0, uploadUrl: '', helpUrl: '', maxRows: 50,
    upc: null, item: null, info: null, aiBusy: '', itemError: '', loaded: false, error: '', armed: null, armTimer: null, searchTimer: null,
    prices: {},  // upc -> { loading, error, searchUrl, sources } (asking prices on eBay / Amazon)
    uploading: 0,  // workbook id being sent to Facebook
  };

  // -- data ---------------------------------------------------------------------------------

  async function load({ quiet = false } = {}) {
    if (!P() || P().connected === false) return;
    const done = quiet ? () => {} : P().busy('Loading the Facebook list…');
    try {
      const data = await P().api('/api/lister/fb/queue');
      Object.assign(fb, { items: data.items || [], counts: data.counts || {}, batches: data.batches || [],
        listedToday: data.listedToday || 0, uploadUrl: data.uploadUrl || '', helpUrl: data.helpUrl || '',
        maxRows: data.maxRows || 50, loaded: true, error: '' });
    } catch (error) {
      fb.error = error.message || String(error);
    } finally {
      done();
    }
    renderCount();
    if (fb.open && !fb.upc) renderList();
    P().renderActionBar();
  }

  async function openItem(upc) {
    fb.upc = upc; fb.item = null; fb.info = null; fb.itemError = '';
    P().clearPhotoSelection();
    renderItem();
    // The full Lister item (the same one the eBay/Amazon view shows): photos, rack and store status.
    void loadInfo(upc);
    void loadPrices(upc);
    P().renderActionBar();
    const done = P().busy('Opening the item…');
    try {
      fb.item = await P().api('/api/lister/fb/item/' + encodeURIComponent(upc));
    } catch (error) {
      fb.itemError = error.message || String(error);
    } finally {
      done();
    }
    if (fb.upc === upc) { renderItem(); P().renderActionBar(); }
  }

  async function loadInfo(upc) {
    try {
      const info = await P().loadItem(upc);
      if (fb.upc === upc) { fb.info = info; renderItem(); }
    } catch { /* the review card works without it */ }
  }

  // "AI" beside Title and Description: the Lister's own writer, from the item, its notes and this draft.
  async function aiText(kind) {
    if (!fb.item || fb.aiBusy) return;
    const full = fb.info || {}, fields = full.fields || {}, v = values();
    const title = v.title || fields.title || full.title || '';
    if (!title) { P().toast('Put a title in first: the AI starts from it', true); return; }
    fb.aiBusy = kind; renderItem();
    const done = P().busy(kind === 'title' ? 'Writing the title with AI\u2026' : 'Writing the description with AI\u2026');
    try {
      const notes = [...(full.notes || []).map(n => n.english || n.text), ...(full.voiceNotes || []).map(n => n.english),
        full.defect || fb.item.defect].filter(Boolean);
      const data = await P().api('/api/lister/queue/' + encodeURIComponent(fb.upc) + '/generate', { method: 'POST', body: { kind, values: {
        title, systemTitle: full.title || '', brand: fields.brand || '', categoryPath: (v.category || '').replace(/\/\//g, ' > '),
        condition: v.condition || '', conditionDescription: fields.conditionDescription || '', notes, aspects: fields.aspects || {},
      }, instructions: kind === 'title' ? P().titlePrompt : '' } });
      const box = $(kind === 'title' ? 'fbTitle' : 'fbDesc');
      if (box) {
        box.value = String(kind === 'title' ? data.title : data.descriptionText || '').slice(0, box.maxLength > 0 ? box.maxLength : undefined);
        box.dispatchEvent(new Event('input', { bubbles: true }));
      }
      P().toast((kind === 'title' ? 'AI title written' : 'AI description written') + ' \u2014 press Save to keep it');
    } catch (error) {
      P().toast('AI ' + kind + ': ' + (error.message || error), true);
    } finally {
      fb.aiBusy = ''; done(); renderItem();
    }
  }

  // What the item goes for on eBay (live comps) and Amazon (current offers); cached per item for the session.
  async function loadPrices(upc, { force = false } = {}) {
    const have = fb.prices[upc];
    if (have && !force && (have.loading || !have.error)) { if (!have.loading) renderPrices(); return; }
    fb.prices[upc] = { loading: true, error: '', searchUrl: '', sources: [] };
    renderPrices();
    try {
      const data = await P().api('/api/lister/fb/item/' + encodeURIComponent(upc) + '/prices');
      fb.prices[upc] = { loading: false, error: '', searchUrl: data.searchUrl || '', sources: data.sources || [] };
    } catch (error) {
      fb.prices[upc] = { loading: false, error: error.message || String(error), searchUrl: '', sources: [] };
    }
    if (fb.upc === upc) renderPrices();
  }

  function searchUrlFor(upc) {
    const cached = fb.prices[upc];
    return (cached && cached.searchUrl) || 'https://www.google.com/search?q=' + encodeURIComponent(String(upc || '').split('-')[0]);
  }

  function money(value) {
    if (value == null || value === '') return '';
    const n = Number(value);
    return Number.isFinite(n) ? '$' + (Number.isInteger(n) ? n : n.toFixed(2)) : '';
  }

  // One chip per source: press it to put that price in the box; the small text says what it came from.
  function pricesHtml() {
    const upc = fb.upc;
    const p = upc ? fb.prices[upc] : null;
    const locked = fb.item && fb.item.status === 'in_template';
    const search = `<button class="mini fb-search" type="button" data-act="search" title="Search the web for this barcode in a new tab">\u{1F50D} Search</button>`;
    if (!p || p.loading) return `<span class="muted small"><span class="spin"></span> Asking eBay and Amazon\u2026</span>${search}`;
    const chips = [];
    for (const s of p.sources) {
      if (s.source === 'ebay') {
        if (s.suggested) {
          chips.push(`<button class="chip fb-price" type="button" data-act="useprice" data-price="${esc(s.suggested)}"${locked ? ' disabled' : ''} title="${esc(s.count)} eBay listings with this barcode, ${esc(money(s.low))}\u2013${esc(money(s.high))}; the middle one">` +
            `<b>eBay</b> ${esc(money(s.suggested))} <small>${esc(s.count)} listed \u00b7 ${esc(money(s.low))}\u2013${esc(money(s.high))}</small></button>`);
        } else {
          chips.push(`<span class="chip fb-price off" title="${esc(s.error || 'No eBay listing carries this barcode')}"><b>eBay</b> <small>${esc(s.error ? 'unavailable' : 'none')}</small></span>`);
        }
      } else if (s.source === 'amazon') {
        if (s.suggested) {
          const what = s.buyBox ? 'Buy Box' : 'lowest new offer';
          chips.push(`<button class="chip fb-price" type="button" data-act="useprice" data-price="${esc(s.suggested)}"${locked ? ' disabled' : ''} title="${esc(s.title || s.asin)}: ${what}${s.count ? `, ${esc(s.count)} new offers` : ''}">` +
            `<b>Amazon</b> ${esc(money(s.suggested))} <small>${what}${s.lowest && s.buyBox && s.lowest !== s.buyBox ? ` \u00b7 low ${esc(money(s.lowest))}` : ''}</small></button>`);
        } else {
          chips.push(`<span class="chip fb-price off" title="${esc(s.error || 'No Amazon price')}"><b>Amazon</b> <small>${esc(s.error ? (s.asin ? 'no offers' : 'not found') : 'none')}</small></span>`);
        }
      }
      if (s.url) chips[chips.length - 1] = chips[chips.length - 1].replace(/<\/(button|span)>$/, ` <a href="${esc(s.url)}" target="_blank" rel="noopener" title="Open on ${esc(s.label)}" aria-label="Open on ${esc(s.label)}">\u2197</a></$1>`);
    }
    if (p.error) chips.push(`<span class="small fb-bad">${esc(p.error)}</span>`);
    return chips.join('') + search + `<button class="link small fb-reprice" type="button" data-act="reprice" title="Ask eBay and Amazon again">refresh</button>`;
  }

  function renderPrices() {
    const box = $('fbPrices');
    if (box) box.innerHTML = pricesHtml();
  }

  function usePrice(value) {
    const input = $('fbPrice');
    if (!input || input.disabled) return;
    input.value = String(Math.max(1, Math.round(Number(value) || 0)));
    input.dispatchEvent(new Event('input', { bubbles: true }));
    input.focus();
  }

  function backToList() {
    fb.upc = null; fb.item = null; fb.info = null;
    renderList();
    P().renderActionBar();
    void load({ quiet: true });
  }

  function values() {
    return {
      title: $('fbTitle').value.trim(), price: $('fbPrice').value, condition: $('fbCond').value,
      category: $('fbCat').value, description: $('fbDesc').value,
    };
  }

  async function save({ ready }) {
    const upc = fb.upc;
    if (!upc || !fb.item) return false;
    const done = P().busy(ready ? 'Marking it ready…' : 'Saving…');
    try {
      await P().api('/api/lister/fb/item/' + encodeURIComponent(upc), { method: 'POST', body: { ...values(), ready } });
      P().toast(ready ? 'Ready for the Facebook workbook' : 'Saved');
      return true;
    } catch (error) {
      P().toast(error.message || String(error), true);
      return false;
    } finally {
      done();
    }
  }

  async function readyAndNext() {
    if (!(await save({ ready: true }))) return;
    await load({ quiet: true });
    const next = fb.items.find(it => it.status === 'queued' && it.upc !== fb.upc);
    if (next) await openItem(next.upc);
    else backToList();
  }

  async function clearAll() {
    const open = fb.items.filter(it => it.status === 'queued' || it.status === 'ready');
    if (!open.length) return;
    const done = P().busy(`Clearing ${open.length} from the Facebook list\u2026`);
    let cleared = 0;
    try {
      for (const it of open) {
        try { await P().api('/api/lister/fb/item/' + encodeURIComponent(it.upc) + '/remove', { method: 'POST', body: {} }); cleared++; }
        catch (error) { /* counted below */ }
      }
    } finally {
      done();
    }
    const missed = open.length - cleared;
    P().toast(`Cleared ${cleared} from Facebook` + (missed ? ` \u2014 ${missed} could not be removed` : ''), Boolean(missed));
    backToList();
  }

  async function remove(upc) {
    const done = P().busy('Taking it off the Facebook list…');
    try {
      await P().api('/api/lister/fb/item/' + encodeURIComponent(upc) + '/remove', { method: 'POST', body: {} });
      P().toast('Off the Facebook list');
      backToList();
    } catch (error) {
      P().toast(error.message || String(error), true);
    } finally {
      done();
    }
  }

  // A file from the server, saved through the panel (the fetch carries the Cloudflare sign-in).
  async function download(path, name) {
    const response = await fetch(P().serverBase() + path, {
      credentials: 'include', cache: 'no-store', redirect: 'manual', headers: { 'X-Sweet-Shelves-Lister': '1' } });
    if (!response.ok) {
      let message = 'HTTP ' + response.status;
      try { message = (await response.json()).error || message; } catch { /* not json */ }
      throw new Error(message);
    }
    const link = document.createElement('a');
    link.href = URL.createObjectURL(await response.blob());
    link.download = name;
    document.body.appendChild(link);
    link.click();
    setTimeout(() => { URL.revokeObjectURL(link.href); link.remove(); }, 10000);
  }

  // -- upload to Facebook ---------------------------------------------------------------------
  // Facebook's "Create multiple listings" page takes a CSV through a file box; there is no API.
  // The panel fetches the batch as CSV (the fetch carries the Cloudflare sign-in), opens the page
  // and drops the file into that box from a page script. Photos still go on each listing by hand.

  // Runs inside the Facebook tab (serialised by chrome.scripting, so it must stand on its own).
  async function fbUploadInPage(payload) {
    const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
    const text = el => String(el.innerText || el.getAttribute('aria-label') || '').replace(/\s+/g, ' ').trim();
    const seen = el => { const r = el.getBoundingClientRect(); return r.width > 0 && r.height > 0; };
    if (window.__ssFbUploading) return { ok: false, error: 'An upload is already running in this tab.' };
    window.__ssFbUploading = true;
    try {
      const bytes = Uint8Array.from(atob(payload.base64), c => c.charCodeAt(0));
      const file = new File([bytes], payload.name, { type: payload.mime });
      const opener = /\b(multiple listings|bulk|spreadsheet|csv|upload (a |your )?file)\b/i;
      let clicked = 0;
      for (let tick = 0; tick < 90; tick++) {  // up to 45 s for the page to build itself
        if (/\/(login|checkpoint)/.test(location.pathname)) return { ok: false, error: 'Facebook wants a sign-in in this tab first.' };
        const inputs = [...document.querySelectorAll('input[type="file"]')]
          .filter(input => !/image|video/i.test(input.accept || '') || /csv|sheet|excel|text/i.test(input.accept || ''));
        const input = inputs.find(i => /csv|sheet|excel|text/i.test(i.accept || '')) || inputs[0];
        if (input) {
          const transfer = new DataTransfer();
          transfer.items.add(file);
          input.files = transfer.files;
          input.dispatchEvent(new Event('input', { bubbles: true }));
          input.dispatchEvent(new Event('change', { bubbles: true }));
          return { ok: true, accept: input.accept || '', clicked };
        }
        // No file box yet: the page may want the multiple-listings option pressed first.
        if (tick % 4 === 0) {
          const control = [...document.querySelectorAll('a, button, [role="button"], [role="link"], [role="menuitem"], label')]
            .find(el => seen(el) && opener.test(text(el)) && !/photo|video/i.test(text(el)));
          if (control) { control.click(); clicked++; }
        }
        await sleep(500);
      }
      return { ok: false, error: 'No file box turned up on this Facebook page. Use "⬇ File again" and choose the file there.' };
    } catch (error) {
      return { ok: false, error: String(error && error.message || error) };
    } finally {
      window.__ssFbUploading = false;
    }
  }

  function waitForTab(tabId, timeoutMs = 30000) {
    return new Promise(resolve => {
      let settled = false;
      const finish = () => { if (settled) return; settled = true; chrome.tabs.onUpdated.removeListener(onUpdated); resolve(); };
      const onUpdated = (id, change) => { if (id === tabId && change.status === 'complete') finish(); };
      chrome.tabs.onUpdated.addListener(onUpdated);
      chrome.tabs.get(tabId).then(tab => { if (tab && tab.status === 'complete') finish(); }).catch(finish);
      setTimeout(finish, timeoutMs);
    });
  }

  async function fetchBase64(path) {
    const response = await fetch(P().serverBase() + path, {
      credentials: 'include', cache: 'no-store', redirect: 'manual', headers: { 'X-Sweet-Shelves-Lister': '1' } });
    if (!response.ok) {
      let message = 'HTTP ' + response.status;
      try { message = (await response.json()).error || message; } catch { /* not json */ }
      throw new Error(message);
    }
    const bytes = new Uint8Array(await response.arrayBuffer());
    let binary = '';
    for (let i = 0; i < bytes.length; i += 0x8000) binary += String.fromCharCode.apply(null, bytes.subarray(i, i + 0x8000));
    return btoa(binary);
  }

  async function uploadToFacebook(id) {
    if (fb.uploading) return;
    fb.uploading = id;
    renderList();
    const done = P().busy(`Sending workbook #${id} to Facebook…`);
    try {
      const name = `facebook-marketplace-${id}.csv`;
      const base64 = await fetchBase64(`/api/lister/fb/batch/${id}.csv`);
      const tab = await chrome.tabs.create({ url: fb.uploadUrl || 'https://www.facebook.com/marketplace/create/bulk', active: true });
      await waitForTab(tab.id);
      await new Promise(resolve => setTimeout(resolve, 1500));  // Facebook draws the form after "complete"
      const results = await chrome.scripting.executeScript({ target: { tabId: tab.id }, func: fbUploadInPage,
        args: [{ name, base64, mime: 'text/csv' }] });
      const result = (results && results[0] && results[0].result) || { ok: false, error: 'The Facebook page gave no answer.' };
      if (!result.ok) throw new Error(result.error || 'Upload failed');
      P().toast(`Workbook #${id} is in Facebook's file box. Check the rows there, publish, then add photos and press Mark listed here.`);
    } catch (error) {
      P().toast(error.message || String(error), true);
    } finally {
      fb.uploading = 0;
      done();
      renderList();
    }
  }

  async function build() {
    const done = P().busy('Building the Facebook workbook…');
    try {
      const data = await P().api('/api/lister/fb/build', { method: 'POST', body: {} });
      await download(data.download, `facebook-marketplace-${data.batchId}.xlsx`);
      P().toast(`Workbook #${data.batchId} saved: ${data.count} item${data.count === 1 ? '' : 's'}. Upload it on Facebook next.`);
    } catch (error) {
      P().toast(error.message || String(error), true);
    } finally {
      done();
    }
    await load({ quiet: true });
  }

  async function batchAction(id, what) {
    const done = P().busy(what === 'listed' ? 'Recording the listings…' : 'Putting the items back…');
    try {
      const data = await P().api(`/api/lister/fb/batch/${id}/${what}`, { method: 'POST', body: {} });
      if (what === 'listed') {
        P().toast(`Listed on Facebook: ${data.listed} item${data.listed === 1 ? '' : 's'}` +
          (data.problems?.length ? ` (${data.problems.length} could not be recorded)` : ''), Boolean(data.problems?.length));
      } else {
        P().toast('Back to Ready: build a new workbook when you want');
      }
    } catch (error) {
      P().toast(error.message || String(error), true);
    } finally {
      done();
    }
    await load({ quiet: true });
  }

  async function savePhotos() {
    const done = P().busy('Collecting the photos…');
    try {
      await download('/api/lister/fb/item/' + encodeURIComponent(fb.upc) + '/photos.zip', `${fb.upc}-photos.zip`);
      P().toast('Photos saved: add them to the listing on Facebook');
    } catch (error) {
      P().toast(error.message || String(error), true);
    } finally {
      done();
    }
  }

  // A second click within 4 s confirms: no dialogs in the panel.
  function armed(key, run) {
    if (fb.armed === key) { fb.armed = null; clearTimeout(fb.armTimer); void run(); return; }
    fb.armed = key;
    clearTimeout(fb.armTimer);
    fb.armTimer = setTimeout(() => { fb.armed = null; renderList(); }, 4000);
    renderList();
  }

  // -- rendering ------------------------------------------------------------------------------

  function renderCount() {
    const el = $('countFb');
    if (el) el.textContent = fb.loaded ? String((fb.counts.queued || 0) + (fb.counts.ready || 0)) : '';
  }

  function renderList() {
    const card = $('fbCard');
    if (!card || !fb.open || fb.upc) return;
    const c = fb.counts;
    const summary = fb.loaded
      ? `<b>${c.queued || 0}</b> to review · <b>${c.ready || 0}</b> ready · <b>${c.in_template || 0}</b> in a workbook` +
        (fb.listedToday ? ` · ${fb.listedToday} listed today` : '')
      : 'Loading…';
    const batches = fb.batches.map(b => `
      <div class="fb-batch">
        <div><b>Workbook #${b.id}</b> · ${b.count} item${b.count === 1 ? '' : 's'} <span class="muted small">${esc((b.createdAt || '').replace('T', ' ').slice(0, 16))}</span></div>
        <ol class="fb-steps small">
          <li>Press <b>Upload to Facebook</b>: it opens Facebook's multiple-listings page and drops the file in. Check the rows there and publish.</li>
          <li>Add the photos to each new listing (open an item here → Save photos).</li>
          <li>Come back and press Mark listed.</li>
        </ol>
        <div class="row tight">
          <button class="mini primary" type="button" data-act="upload" data-id="${b.id}"${fb.uploading ? ' disabled' : ''}>${fb.uploading === b.id ? '<span class="spin"></span> Sending…' : '⬆ Upload to Facebook'}</button>
          <button class="mini" type="button" data-act="download" data-id="${b.id}">⬇ File again</button>
          <button class="mini" type="button" data-act="facebook">↗ Facebook</button>
          <button class="mini primary" type="button" data-act="listed" data-id="${b.id}">${fb.armed === 'listed' + b.id ? 'Sure? Click again' : '✓ Mark listed'}</button>
          <button class="mini" type="button" data-act="cancel" data-id="${b.id}">${fb.armed === 'cancel' + b.id ? 'Sure? Click again' : '↩ Put back'}</button>
        </div>
      </div>`).join('');
    const open = fb.items.filter(it => it.status === 'queued' || it.status === 'ready');
    const rows = fb.items.map(it => {
      const bad = (it.problems || []).length;
      return `
      <div class="item fb-row${it.status === 'ready' ? ' ready' : ''}" data-upc="${esc(it.upc)}" role="button" tabindex="0">
        <span class="stripe"></span>
        ${it.thumb ? `<img src="${esc(it.thumb)}" alt="" loading="lazy">` : '<span class="noimg"></span>'}
        <div class="body">
          <div class="title">${esc(it.title || it.upc)}</div>
          <div class="sub"><span class="fb-status ${esc(it.status)}">${esc(STATUS[it.status] || it.status)}${it.batchId ? ' #' + it.batchId : ''}</span>
            ${it.price != null ? `<span>$${esc(it.price)}</span>` : ''}${bad ? `<span class="fb-bad">fix ${esc(it.problems.join(', '))}</span>` : ''}
            <span class="upc">${esc(it.upc)}</span></div>
        </div>
        <span class="state">${it.status === 'in_template' ? '' : `<button class="remove" type="button" data-act="fbremove" data-upc="${esc(it.upc)}" aria-label="Remove" title="Take it off the Facebook list">\u00d7</button>`}</span>
      </div>`;
    }).join('');
    const empty = fb.loaded && !fb.items.length
      ? '<div class="muted fb-empty">Nothing on the Facebook list. On Items to List, press <b>+</b> while this FB tab is open.</div>' : '';
    P().setHtml(card, `
      <div class="card fb-head">
        <div class="fb-title"><span class="fb-mark" aria-hidden="true">f</span> Facebook Marketplace</div>
        <div class="small muted">${summary}</div>
        ${fb.error ? `<div class="small fb-bad">${esc(fb.error)}</div>` : ''}
      </div>
      <div class="listacts fb-acts">
        <button id="fbAddItems" class="addbtn" type="button" data-act="additems" title="Opens Items to List in a new tab. Press + there while this FB tab is open and the item shows up here right away."><span class="addplus" aria-hidden="true">+</span><span class="addtxt"><b>Add items</b><small>from Items to List &#x2197;</small></span></button>
        ${open.length ? `<div class="lstack fb-clear"><button class="lact danger" type="button" data-act="clearall" title="Take every item still to review or ready off the Facebook list">${fb.armed === 'clearall' ? `Sure? Click again to clear ${open.length}` : 'Clear all'}</button></div>` : ''}
      </div>
      ${batches ? `<div class="card fb-batches">${batches}</div>` : ''}
      <div class="list fb-list">${rows}${empty}</div>`);
  }

  function renderItem() {
    const card = $('fbCard');
    if (!card || !fb.open || !fb.upc) return;
    const info = fb.item;
    if (!info) {
      P().setHtml(card, `<div class="card"><button class="link" type="button" data-act="back">← Facebook list</button>
        <div class="muted" style="margin-top:8px">${fb.itemError ? esc(fb.itemError) : 'Loading…'}</div></div>`);
      return;
    }
    // A redraw (photos changed, AI busy) keeps what was typed but not saved yet.
    const d = $('fbTitle') ? { ...info.draft, ...values() } : info.draft;
    const locked = info.status === 'in_template';
    const full = fb.info;
    const aiBtn = (kind, label) => `<button class="mini fb-ai" type="button" data-act="ai-${kind}" title="Write the ${kind} with AI from the item and its notes"${locked || fb.aiBusy ? ' disabled' : ''}>${fb.aiBusy === kind ? '<span class="spin"></span>' : '\u2728'} ${label}</button>`;
    const categories = [...new Set([d.category, ...(d.suggestions || [])].filter(Boolean))];
    const photos = (info.photos || []).slice(0, 8).map(p => `<img src="${esc(p.thumb || p.url)}" alt="" loading="lazy">`).join('');
    const note = [info.defect, info.notes].filter(Boolean).join(' · ');
    P().setHtml(card, `
      <div class="card fb-edit">
        <div class="fb-editbar"><button class="link" type="button" data-act="back">← Facebook list</button>
          <span class="muted small">${esc(info.upc)} · ${esc(STATUS[info.status] || info.status)}</span></div>
        ${locked ? `<div class="small fb-lock">In workbook #${esc(info.batchId)}. Put that workbook back to change it.</div>` : ''}
        ${full ? P().statusStripHtml(full) : ''}
        ${full ? P().photoCardHtml(full) : `<div class="fb-photos">${photos || '<span class="muted small">No photos yet.</span>'}</div>`}
        <div class="row tight"><button class="mini" type="button" data-act="photos"${photos ? '' : ' disabled'}>⬇ Save photos</button>
          <span class="muted small">Facebook's file has no photos; add them on the listing.</span></div>
        ${note ? `<div class="small fb-note">${esc(note)}</div>` : ''}
        <label>Title ${aiBtn('title', 'AI title')}<span class="fb-n" id="fbTitleN"></span><input id="fbTitle" maxlength="${info.limits.title}" value="${esc(d.title)}"></label>
        <div class="fb-two">
          <label>Price (whole $)<input id="fbPrice" type="number" min="1" step="1" inputmode="numeric" value="${esc(d.price ?? '')}"></label>
          <label>Condition<select id="fbCond">${info.conditions.map(c => `<option${c === d.condition ? ' selected' : ''}>${esc(c)}</option>`).join('')}</select></label>
        </div>
        <div id="fbPrices" class="fb-prices" aria-label="Asking prices elsewhere">${pricesHtml()}</div>
        <label>Category<select id="fbCat"><option value="">(none)</option>${categories.map(c => `<option value="${esc(c)}"${c === d.category ? ' selected' : ''}>${esc(c.replace(/\/\//g, ' › '))}</option>`).join('')}</select></label>
        <input id="fbCatSearch" type="search" placeholder="Search all Facebook categories…" aria-label="Search Facebook categories">
        <div id="fbCatResults" class="fb-results"></div>
        <label>Description ${aiBtn('description', 'AI description')}<span class="fb-n" id="fbDescN"></span><textarea id="fbDesc" rows="8" maxlength="${info.limits.description}">${esc(d.description)}</textarea></label>
        <div class="row">
          <button type="button" data-act="save"${locked ? ' disabled' : ''}>Save</button>
          <button class="danger" type="button" data-act="remove">Remove from FB</button>
        </div>
      </div>`);
    for (const el of card.querySelectorAll('.fb-edit > label input, .fb-edit > label select, .fb-edit > label textarea, .fb-two input, .fb-two select')) el.disabled = locked;
    if (full) P().wirePhotoCard(card, full, { rerender: renderItem, refresh: () => loadInfo(fb.upc) });
    counters();
  }

  function counters() {
    const title = $('fbTitle'), desc = $('fbDesc');
    if (title) $('fbTitleN').textContent = `${title.value.length}/${title.maxLength}`;
    if (desc) $('fbDescN').textContent = `${desc.value.length}/${desc.maxLength}`;
  }

  function searchCategories(q) {
    clearTimeout(fb.searchTimer);
    fb.searchTimer = setTimeout(async () => {
      const box = $('fbCatResults');
      if (!box) return;
      if (q.trim().length < 2) { box.innerHTML = ''; return; }
      try {
        const data = await P().api('/api/lister/fb/categories?q=' + encodeURIComponent(q.trim()));
        const found = data.categories.length ? data.categories : data.suggested;
        box.innerHTML = found.length
          ? found.map(c => `<button type="button" class="fb-cat" data-cat="${esc(c)}">${esc(c.replace(/\/\//g, ' › '))}</button>`).join('')
          : '<div class="muted small">No Facebook category has those words.</div>';
      } catch (error) {
        box.innerHTML = `<div class="small fb-bad">${esc(error.message)}</div>`;
      }
    }, 250);
  }

  function pickCategory(category) {
    const select = $('fbCat');
    if (![...select.options].some(o => o.value === category)) {
      const option = document.createElement('option');
      option.value = category; option.textContent = category.replace(/\/\//g, ' › ');
      select.appendChild(option);
    }
    select.value = category;
    $('fbCatResults').innerHTML = '';
    $('fbCatSearch').value = '';
  }

  function openFacebook() {
    void chrome.tabs.create({ url: fb.uploadUrl || 'https://www.facebook.com/marketplace/create/item' });
  }

  // -- wiring -----------------------------------------------------------------------------------

  function wire() {
    const card = $('fbCard');
    if (!card || card.dataset.wired) return;
    card.dataset.wired = '1';
    // AI photoshop, a new phone photo or the Phone link busy state redraws the open item too.
    P().onPhotosChanged(() => { if (fb.open && fb.upc && fb.item) renderItem(); });
    card.addEventListener('click', event => {
      const act = event.target.closest('[data-act]');
      if (act) {
        const id = act.dataset.id;
        switch (act.dataset.act) {
          case 'back': backToList(); break;
          case 'save': void save({ ready: false }).then(ok => ok && load({ quiet: true })); break;
          case 'remove': void remove(fb.upc); break;
          case 'fbremove': void remove(act.dataset.upc); break;
          case 'clearall': armed('clearall', () => clearAll()); break;
          case 'additems': chrome.tabs.create({ url: P().serverBase() + '/items-to-list' }); break;
          case 'photos': void savePhotos(); break;
          case 'search': chrome.tabs.create({ url: searchUrlFor(fb.upc) }); break;
          case 'reprice': void loadPrices(fb.upc, { force: true }); break;
          case 'useprice': usePrice(act.dataset.price); break;
          case 'upload': void uploadToFacebook(Number(id)); break;
          case 'ai-title': void aiText('title'); break;
          case 'ai-description': void aiText('description'); break;
          case 'facebook': openFacebook(); break;
          case 'download': void download(`/api/lister/fb/batch/${id}.xlsx`, `facebook-marketplace-${id}.xlsx`)
            .catch(error => P().toast(error.message || String(error), true)); break;
          case 'listed': armed('listed' + id, () => batchAction(id, 'listed')); break;
          case 'cancel': armed('cancel' + id, () => batchAction(id, 'cancel')); break;
        }
        return;
      }
      const cat = event.target.closest('[data-cat]');
      if (cat) { pickCategory(cat.dataset.cat); return; }
      const row = event.target.closest('.fb-row[data-upc]');
      if (row) void openItem(row.dataset.upc);
    });
    card.addEventListener('keydown', event => {
      const row = event.target.closest('.fb-row[data-upc]');
      if (row && (event.key === 'Enter' || event.key === ' ')) { event.preventDefault(); void openItem(row.dataset.upc); }
    });
    card.addEventListener('input', event => {
      if (event.target.id === 'fbCatSearch') searchCategories(event.target.value);
      else counters();
    });
    setInterval(() => { if (fb.open && !fb.upc && document.visibilityState === 'visible') void load({ quiet: true }); }, 20000);
  }

  // -- what sidepanel.js calls ------------------------------------------------------------------

  globalThis.SSListerFb = {
    show(on) {
      wire();
      const was = fb.open;
      fb.open = Boolean(on);
      const card = $('fbCard');
      if (card) card.hidden = !fb.open;
      try { void chrome.storage.local.set({ [OPEN_KEY]: fb.open }); } catch { /* storage unavailable */ }
      if (fb.open && !was) { fb.upc = null; fb.item = null; renderList(); void load(); }
    },
    async wasOpen() {
      try { return Boolean((await chrome.storage.local.get(OPEN_KEY))[OPEN_KEY]); } catch { return false; }
    },
    changed() { if (fb.open && !fb.upc) void load({ quiet: true }); else void load({ quiet: true }); },
    action() {
      if (fb.upc) {
        if (!fb.item) return { label: 'Opening…', disabled: true };
        if (fb.item.status === 'in_template') return { label: `In workbook #${fb.item.batchId}`, disabled: true };
        return { label: 'Ready ✓ · next item', run: () => readyAndNext(), hint: 'Saves, marks it ready for the Facebook workbook, opens the next item to review' };
      }
      const ready = fb.counts.ready || 0;
      if (ready) {
        const n = Math.min(ready, fb.maxRows);
        return { label: `Build Facebook workbook (${n} ready)`, run: () => build(),
                 hint: `Fills Facebook's bulk-upload file with ${n} item${n === 1 ? '' : 's'} (at most ${fb.maxRows}) and saves it` };
      }
      const next = fb.items.find(it => it.status === 'queued');
      if (next) return { label: 'Review the next item', run: () => openItem(next.upc) };
      return { label: fb.loaded ? 'Nothing to list on Facebook' : 'Loading…', disabled: true };
    },
    more() {
      const list = [{ label: '↻ Reload the Facebook list', run: () => load() },
                    { label: '↗ Facebook: create listings', run: () => openFacebook() }];
      if (fb.helpUrl) list.push({ label: '↗ Facebook help: listings from a spreadsheet', run: () => chrome.tabs.create({ url: fb.helpUrl }) });
      if (fb.upc) list.push({ label: '✕ Remove this item from the FB list', run: () => remove(fb.upc), danger: true });
      return list;
    },
  };

  // The tab's count shows before the tab is opened.
  setTimeout(() => { if (!fb.loaded) void load({ quiet: true }); }, 2500);
})();
