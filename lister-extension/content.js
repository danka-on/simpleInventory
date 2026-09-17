// Sweet Shelves Lister page script. Injected on demand into eBay listing and Seller Central
// pages (after matcher.js). It never talks to the Sweet Shelves server; the side panel does.
(() => {
  if (globalThis.__ssLister) return;
  const M = globalThis.SSListerMatcher;
  const MAX_TEXT = 90;
  const picked = new Map();
  let pickState = null;
  let guide = null;

  function clip(text) {
    return String(text || '').replace(/\s+/g, ' ').trim().slice(0, MAX_TEXT);
  }

  function ownDocument(el) {
    return el.ownerDocument || document;
  }

  // Human-facing words around a field: <label for>, wrapping label, aria-labelledby, then the
  // closest preceding heading/label-like text in the same container.
  function labelTextFor(el) {
    const doc = ownDocument(el);
    const parts = [];
    if (el.id) {
      for (const label of doc.querySelectorAll('label[for="' + CSS.escape(el.id) + '"]')) parts.push(label.textContent);
    }
    const wrapping = el.closest('label');
    if (wrapping) parts.push(wrapping.textContent);
    const labelledBy = el.getAttribute('aria-labelledby');
    if (labelledBy) {
      for (const id of labelledBy.split(/\s+/)) {
        const node = doc.getElementById(id);
        if (node) parts.push(node.textContent);
      }
    }
    return clip(parts.join(' '));
  }

  function nearbyText(el) {
    let node = el;
    for (let depth = 0; depth < 5 && node && node.parentElement; depth++) {
      node = node.parentElement;
      const candidates = node.querySelectorAll('label, legend, h1, h2, h3, h4, h5, h6, [class*="label" i], [class*="title" i], [class*="heading" i], span, div');
      let best = '';
      for (const candidate of candidates) {
        if (candidate === el || candidate.contains(el)) continue;
        // Only text that appears before the field in document order counts as its heading.
        if (!(candidate.compareDocumentPosition(el) & Node.DOCUMENT_POSITION_FOLLOWING)) continue;
        const text = clip(candidate.childNodes.length && candidate.children.length < 3 ? candidate.textContent : '');
        if (text && text.length <= 60) best = text;
      }
      if (best) return best;
    }
    return '';
  }

  function describe(el) {
    const tag = el.tagName.toLowerCase();
    const contenteditable = el.isContentEditable && tag !== 'input' && tag !== 'textarea';
    // A Katal field's wording lives on the host element, not on the <input> inside its shadow root.
    const host = shadowHost(el);
    const attr = name => clip(el.getAttribute(name) || (host ? host.getAttribute(name) : '') || '');
    return {
      tag,
      type: tag === 'input' ? (el.getAttribute('type') || 'text').toLowerCase() : '',
      name: el.getAttribute('name') || (host ? host.getAttribute('name') : '') || '',
      id: el.id || (host ? host.id : '') || '',
      ariaLabel: attr('aria-label') || attr('label'),
      placeholder: attr('placeholder'),
      role: el.getAttribute('role') || '',
      labelText: labelTextFor(el) || (host ? labelTextFor(host) : ''),
      nearbyText: nearbyText(el) || (host ? nearbyText(host) : ''),
      maxLength: el.maxLength > 0 ? el.maxLength : 0,
      contenteditable,
      required: Boolean(el.required),
      ariaRequired: el.getAttribute('aria-required') === 'true',
      value: contenteditable ? clip(el.textContent) : clip(el.value || ''),
      options: tag === 'select' ? Array.from(el.options).map(o => o.textContent.trim()) : undefined,
      visible: isVisible(el),
      inFrame: ownDocument(el) !== document,
    };
  }

  function isVisible(el) {
    const style = ownDocument(el).defaultView.getComputedStyle(el);
    if (style.display === 'none' || style.visibility === 'hidden') return false;
    const rect = el.getBoundingClientRect();
    return rect.width > 0 && rect.height > 0;
  }

  const FIELD_SELECTOR = 'input, textarea, select, [contenteditable="true"], [contenteditable=""], [role="textbox"], [role="combobox"]';

  function documents() {
    const docs = [document];
    for (const frame of document.querySelectorAll('iframe')) {
      try {
        if (frame.contentDocument && frame.contentDocument.body) docs.push(frame.contentDocument);
      } catch { /* cross-origin frame */ }
    }
    return docs;
  }

  // Seller Central is built from Katal custom elements (kat-input, kat-button) that keep the real
  // <input>/<button> in an open shadow root, so nothing is found by querying the document alone.
  function shadowRootsUnder(node, out, depth) {
    if (depth > 8) return out;
    let nodes;
    try { nodes = node.querySelectorAll('*'); } catch { return out; }
    for (const el of nodes) {
      const shadow = el.shadowRoot;
      if (!shadow) continue;
      out.push(shadow);
      shadowRootsUnder(shadow, out, depth + 1);
    }
    return out;
  }

  // Walking every element is not free, so the list is reused for a moment (the guide re-reads often).
  let rootsCache = { at: 0, roots: null };
  function roots() {
    if (rootsCache.roots && Date.now() - rootsCache.at < 750) return rootsCache.roots;
    const out = [];
    for (const doc of documents()) {
      out.push(doc);
      shadowRootsUnder(doc, out, 0);
    }
    rootsCache = { at: Date.now(), roots: out };
    return out;
  }

  function deepQuery(selector, scope) {
    const out = [];
    const bases = scope ? [scope, ...shadowRootsUnder(scope, [], 0)] : roots();
    for (const base of bases) {
      try { for (const el of base.querySelectorAll(selector)) out.push(el); } catch { /* detached */ }
    }
    return out;
  }

  // The custom element a shadow-DOM field belongs to: it carries the label, placeholder and id.
  function shadowHost(el) {
    try {
      const node = el.getRootNode();
      return node && node.host ? node.host : null;
    } catch {
      return null;
    }
  }

  function collect() {
    const elements = [];
    for (const el of deepQuery(FIELD_SELECTOR)) {
      if (el.disabled || el.readOnly) continue;
      const type = (el.getAttribute('type') || '').toLowerCase();
      if (['hidden', 'checkbox', 'radio', 'file', 'submit', 'button', 'reset', 'image'].includes(type)) continue;
      if (!isVisible(el)) continue;
      elements.push(el);
    }
    return elements;
  }

  function setNativeValue(el, value) {
    const proto = el instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
    const descriptor = Object.getOwnPropertyDescriptor(proto, 'value');
    try { el.focus({ preventScroll: true }); } catch { el.focus(); }
    if (descriptor && descriptor.set) descriptor.set.call(el, value);
    else el.value = value;
    // composed: true so a Katal host outside the shadow root hears its own input.
    el.dispatchEvent(new Event('input', { bubbles: true, composed: true }));
    el.dispatchEvent(new Event('change', { bubbles: true, composed: true }));
    el.dispatchEvent(new KeyboardEvent('keyup', { bubbles: true, composed: true }));
  }

  function setContentEditable(el, html, text) {
    try { el.focus({ preventScroll: true }); } catch { el.focus(); }
    const doc = ownDocument(el);
    const selection = doc.getSelection();
    if (selection) {
      const range = doc.createRange();
      range.selectNodeContents(el);
      selection.removeAllRanges();
      selection.addRange(range);
    }
    let done = false;
    try { done = doc.execCommand('insertHTML', false, html || text); } catch { done = false; }
    if (!done) {
      if (html) el.innerHTML = html; else el.textContent = text;
    }
    el.dispatchEvent(new Event('input', { bubbles: true }));
    el.dispatchEvent(new Event('change', { bubbles: true }));
    return true;
  }

  function setSelect(el, labels) {
    const options = Array.from(el.options).map(o => o.textContent.trim());
    const index = M.chooseOption(options, labels);
    if (index < 0) return false;
    try { el.focus({ preventScroll: true }); } catch { el.focus(); }
    el.selectedIndex = index;
    el.dispatchEvent(new Event('input', { bubbles: true }));
    el.dispatchEvent(new Event('change', { bubbles: true }));
    return true;
  }

  // Set one field. `value` is a string; `labels` an optional label list for <select>s.
  function setField(el, value, { html = '', labels = null } = {}) {
    const tag = el.tagName.toLowerCase();
    if (tag === 'select') return setSelect(el, labels || [value]);
    if (el.isContentEditable && tag !== 'input' && tag !== 'textarea') return setContentEditable(el, html, value);
    if (tag === 'input' || tag === 'textarea') {
      const type = (el.getAttribute('type') || 'text').toLowerCase();
      let text = String(value ?? '');
      if (type === 'number') text = text.replace(/[^0-9.\-]/g, '');
      if (el.maxLength > 0 && text.length > el.maxLength) text = text.slice(0, el.maxLength);
      setNativeValue(el, text);
      return true;
    }
    return false;
  }

  function valueFor(target, values, store) {
    switch (target) {
      case 'title': return values.title || '';
      case 'price': return values.price != null ? String(values.price) : '';
      case 'quantity': return values.quantity != null ? String(values.quantity) : '';
      case 'sku': return values.sku || '';
      case 'upc': return values.upc || '';
      case 'asin': return values.asin || '';
      case 'brand': return values.brand || '';
      case 'conditionDescription': return values.conditionDescription || '';
      case 'description': return store === 'amazon' ? (values.descriptionText || '') : (values.descriptionText || '');
      default: return '';
    }
  }

  function fill({ values = {}, store = '', targets = null, aspects = {}, learned = {}, includeDescription = true } = {}) {
    const before = { x: window.scrollX, y: window.scrollY, active: document.activeElement };
    try { return fillNow({ values, store, targets, aspects, learned, includeDescription }); }
    finally {
      // Automatic work must not move the page around: put the scroll and the focus back.
      window.scrollTo(before.x, before.y);
      try { if (before.active && before.active !== document.body && before.active.isConnected) before.active.focus({ preventScroll: true }); else if (document.activeElement) document.activeElement.blur(); } catch { /* ignore */ }
      setTimeout(() => window.scrollTo(before.x, before.y), 50);
    }
  }

  function fillNow({ values = {}, store = '', targets = null, aspects = {}, learned = {}, includeDescription = true } = {}) {
    const elements = collect();
    const descriptors = elements.map(describe);
    const report = { filled: [], skipped: [], unmatched: [], aspects: [], fieldCount: elements.length };
    const wanted = (targets && targets.length ? targets : Object.keys(M.TARGETS)).filter(t => valueFor(t, values, store) !== '');
    const used = new Set();
    const chosen = {};

    // Fields the user taught with "Pick field" win over the heuristics.
    for (const target of wanted) {
      const sig = learned[target];
      if (!sig) continue;
      const index = descriptors.findIndex((d, i) => !used.has(i) && M.matchesSignature(d, sig));
      if (index >= 0) { chosen[target] = { index, score: 99, learned: true }; used.add(index); }
    }
    const remaining = wanted.filter(t => !chosen[t]);
    const free = descriptors.map((d, i) => used.has(i) ? null : d);
    const assigned = M.assign(free.map(d => d || { tag: 'input', type: 'hidden' }), remaining);
    for (const [target, hit] of Object.entries(assigned)) { chosen[target] = hit; used.add(hit.index); }

    for (const target of wanted) {
      const hit = chosen[target];
      if (!hit) { report.unmatched.push(target); continue; }
      if (target === 'description' && !includeDescription) { report.skipped.push({ target, reason: 'description left for manual paste' }); continue; }
      const el = elements[hit.index];
      const descriptor = descriptors[hit.index];
      const value = valueFor(target, values, store);
      const html = target === 'description' ? (values.descriptionHtml || '') : '';
      let ok = false;
      try { ok = setField(el, value, { html }); } catch (error) { report.skipped.push({ target, reason: String(error && error.message || error) }); continue; }
      (ok ? report.filled : report.skipped).push({ target, value: clip(value), label: descriptor.labelText || descriptor.ariaLabel || descriptor.name || descriptor.placeholder, score: hit.score, learned: Boolean(hit.learned), reason: ok ? '' : 'field type not supported' });
      if (ok) flash(el);
    }

    // Condition: a <select> or combobox whose label says condition.
    if (values.condition) {
      const labels = M.conditionLabels(values.condition, store);
      const index = descriptors.findIndex((d, i) => !used.has(i) && d.tag === 'select' && /condition/.test(M.normalize(d.labelText + ' ' + d.ariaLabel + ' ' + d.name + ' ' + d.nearbyText)));
      if (index >= 0 && labels.length) {
        const ok = setSelect(elements[index], labels);
        used.add(index);
        (ok ? report.filled : report.skipped).push({ target: 'condition', value: labels[0], label: descriptors[index].labelText || 'Condition', reason: ok ? '' : 'no matching option' });
        if (ok) flash(elements[index]);
      } else {
        report.unmatched.push('condition');
      }
    }

    // Who ships it (Amazon) and eBay's own lowest-price suggestion: always the same answer, so the
    // page gets it without being asked. Both stay checkpoints, so the user still walks past them.
    const fulfilment = chooseFulfilment(store);
    if (fulfilment) (fulfilment.ok ? report.filled : report.skipped).push({ target: 'fulfillment', value: fulfilment.label, label: 'Fulfilment', reason: fulfilment.ok ? '' : 'could not tick it' });
    const lowest = matchLowestPrice(store);
    if (lowest) (lowest.ok ? report.filled : report.skipped).push({ target: 'matchLowest', value: lowest.label, label: 'Match lowest price', reason: lowest.ok ? '' : 'could not click it' });

    // Item specifics by exact label (Brand, Color, Material, ...).
    const aspectHits = M.matchAspects(descriptors.map((d, i) => used.has(i) ? { tag: 'input', type: 'hidden' } : d), aspects);
    for (const [aspect, hit] of Object.entries(aspectHits)) {
      const el = elements[hit.index];
      let ok = false;
      try { ok = setField(el, hit.value, { labels: aspects[aspect] }); } catch { ok = false; }
      used.add(hit.index);
      report.aspects.push({ aspect, value: hit.value, ok, label: descriptors[hit.index].labelText || descriptors[hit.index].ariaLabel });
      if (ok) flash(el);
    }
    return report;
  }

  // Every word around a control, shadow host included: Amazon's choice is a long sentence in a label.
  function controlText(el) {
    const host = shadowHost(el);
    const label = el.closest ? el.closest('label') : null;
    const hostLabel = host && host.closest ? host.closest('label') : null;
    return M.normalize([labelTextFor(el), label && label.textContent, hostLabel && hostLabel.textContent,
      nearbyText(host || el), el.getAttribute && el.getAttribute('aria-label'), host && host.getAttribute('label'),
      el.value].filter(Boolean).join(' '));
  }

  // "I want to ship this item myself or use Amazon Easy Ship if it sells. (Merchant Fulfilled)".
  function chooseFulfilment(store) {
    if (store !== 'amazon') return null;
    for (const radio of deepQuery('input[type=radio], kat-radiobutton')) {
      if (!isVisible(radio)) continue;
      const text = controlText(radio);
      if (!/ship this item myself|merchant fulfill|easy ship/.test(text)) continue;
      if (/amazon (ships|will ship|fulfills)|fulfilled by amazon|\bfba\b/.test(text)) continue;
      const already = radio.checked === true || radio.getAttribute('checked') !== null;
      if (!already) { try { radio.click(); } catch { return { ok: false, label: 'Merchant Fulfilled' }; } }
      return { ok: true, label: 'Merchant Fulfilled' };
    }
    return null;
  }

  // eBay offers "Match lowest price" next to the price box; that is always the answer we want.
  function matchLowestPrice(store) {
    if (store !== 'ebay') return null;
    for (const el of deepQuery('button, [role=button], a, label, kat-button')) {
      if (!isVisible(el) || buttonDisabled(el)) continue;
      const text = M.normalize(el.textContent || '');
      if (!/^match (the )?lowest price/.test(text)) continue;
      try { el.click(); } catch { return { ok: false, label: 'Match lowest price' }; }
      return { ok: true, label: 'Match lowest price' };
    }
    return null;
  }

  function flash(el) {
    try {
      // Same soft glow as the guide, faded back out after a moment.
      ring(el, '#0a9c6c', true);
      setTimeout(() => unring(el), 1800);
    } catch { /* ignore */ }
  }

  // -- UPC search on the store's "what are you selling" page ---------------------------------------

  function findSearchBox(store) {
    const elements = collect();
    let best = null;
    let bestScore = 0;
    for (const el of elements) {
      const score = M.searchBoxScore(store, describe(el));
      if (score > bestScore) { bestScore = score; best = el; }
    }
    return bestScore >= 4 ? best : null;
  }

  // The search button next to the box, when it is usable. Seller Central keeps its "Next" button
  // disabled until React has processed our typing, so this is re-run until one answers.
  function buttonDisabled(b) {
    if (b.disabled || b.getAttribute('aria-disabled') === 'true' || b.hasAttribute('disabled')) return true;
    const host = shadowHost(b);
    return Boolean(host && (host.hasAttribute('disabled') || host.getAttribute('aria-disabled') === 'true'));
  }

  function searchButton(el) {
    // An anchor in the page's own tree: `closest` and document order stop at a shadow boundary.
    const anchor = shadowHost(el) || el;
    const form = el.form || anchor.closest('form');
    const doc = ownDocument(anchor);
    const scope = form || anchor.closest('[class*="search" i], [class*="prelist" i], section, main') || doc.body;
    // The submit button comes AFTER the box in the page: Seller Central also has a "Search" tab tile before it.
    const candidates = deepQuery('button, input[type=submit], [role=button], kat-button', scope).filter(b => {
      if (!isVisible(b) || buttonDisabled(b)) return false;
      const host = shadowHost(b);
      // Each wording on its own: a Katal button repeats its label on the host, and "next next"
      // would not match the exact list.
      const texts = [b.textContent, b.getAttribute('aria-label'), b.getAttribute('label'), b.value,
        host && host.getAttribute('label'), host && host.getAttribute('aria-label')]
        .map(text => M.normalize(text || '')).filter(Boolean);
      return texts.some(text => /^(search|get started|continue|find|go|next|submit|search now)$/.test(text) || /search|get started/.test(text));
    });
    // When a custom element and the real <button> inside it both match, click the real one:
    // the host has no default action of its own (Katal listens on the inner button).
    const inner = candidates.filter(b => !candidates.some(other => other !== b && shadowHost(other) === b));
    const after = inner.filter(b => {
      const target = shadowHost(b) || b;
      return anchor.compareDocumentPosition(target) & Node.DOCUMENT_POSITION_FOLLOWING;
    });
    return after[0] || inner[0] || null;
  }

  // Enter first (some pages submit on it), then the button as soon as the page enables it.
  function submitSearch(el, done) {
    const form = el.form || el.closest('form');
    for (const type of ['keydown', 'keypress', 'keyup']) {
      el.dispatchEvent(new KeyboardEvent(type, { key: 'Enter', code: 'Enter', keyCode: 13, which: 13, bubbles: true, cancelable: true, composed: true }));
    }
    const started = Date.now();
    const attempt = () => {
      let button = null;
      try { button = searchButton(el); } catch { /* page navigated away */ }
      if (button) { try { button.click(); } catch { /* gone */ } return done('button'); }
      if (Date.now() - started < 3000) return setTimeout(attempt, 200);
      if (form) { try { form.requestSubmit ? form.requestSubmit() : form.submit(); } catch { /* ignore */ } return done('form'); }
      done('enter');
    };
    attempt();
  }

  function searchNow(query, store, done) {
    const box = findSearchBox(store);
    if (!box) return false;
    const text = String(query || '');
    setNativeValue(box, text);
    // A controlled input can throw our value away; retype it as keystrokes before giving up.
    if (box.value !== text) {
      try { box.focus({ preventScroll: true }); document.execCommand('insertText', false, text); } catch { /* ignore */ }
      if (box.value !== text) setNativeValue(box, text);
    }
    flash(box);
    const label = describe(box).placeholder || describe(box).labelText || box.id;
    submitSearch(box, how => done({ ok: box.value === text, typed: box.value, label, submitted: how,
      reason: box.value === text ? '' : 'the page kept clearing the search box' }));
    return true;
  }

  // Store pages render the search box after load; Seller Central can take a while, so wait ~12 s.
  // A double-click reaches us twice (click + start); the second one must not submit a second time.
  let searchJob = null;
  function search({ query = '', store = '' } = {}, done) {
    const text = String(query || '');
    if (searchJob && searchJob.query === text && Date.now() - searchJob.at < 8000) {
      return done({ ok: true, submitted: 'already running', label: searchJob.label || '' });
    }
    searchJob = { query: text, at: Date.now(), label: '' };
    const started = Date.now();
    const attempt = () => {
      if (searchNow(text, store, result => { searchJob = { query: text, at: Date.now(), label: result.label }; done(result); })) return;
      if (Date.now() - started > 12000) { searchJob = null; return done({ ok: false, reason: 'no product search box on this page' }); }
      setTimeout(attempt, 300);
    };
    attempt();
  }

  // -- photos: hand our files to the page's uploader ------------------------------------------------

  function fileInputs() {
    const out = [];
    {
      for (const el of deepQuery('input[type=file]')) {
        if (el.disabled) continue;
        const accept = (el.getAttribute('accept') || '').toLowerCase();
        if (accept && !/image|jpg|jpeg|png|\*/.test(accept)) continue;
        out.push(el);
      }
    }
    // Multi-file, image-only inputs first; they are the photo uploader.
    return out.sort((a, b) => (Number(b.multiple) - Number(a.multiple)) || ((b.getAttribute('accept') || '').includes('image') ? 1 : 0) - ((a.getAttribute('accept') || '').includes('image') ? 1 : 0));
  }

  function dropZone() {
    for (const doc of documents()) {
      const zone = Array.from(doc.querySelectorAll('[class*="drop" i], [class*="upload" i], [data-testid*="upload" i], [aria-label*="photo" i], [class*="photo" i]')).find(isVisible);
      if (zone) return zone;
    }
    return null;
  }

  function toFiles(entries) {
    const files = [];
    for (const entry of entries || []) {
      try {
        const binary = atob(entry.base64 || '');
        const bytes = new Uint8Array(binary.length);
        for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
        files.push(new File([bytes], entry.name || 'photo.jpg', { type: entry.type || 'image/jpeg' }));
      } catch { /* skip a broken entry */ }
    }
    return files;
  }

  function addPhotos({ files: entries = [] } = {}) {
    const files = toFiles(entries);
    if (!files.length) return { ok: false, reason: 'no photos to add' };
    const transfer = new DataTransfer();
    for (const file of files) transfer.items.add(file);
    const input = fileInputs()[0];
    if (input) {
      try {
        input.files = transfer.files;
        input.dispatchEvent(new Event('input', { bubbles: true }));
        input.dispatchEvent(new Event('change', { bubbles: true }));
        return { ok: true, method: 'file-input', count: files.length, multiple: Boolean(input.multiple) };
      } catch (error) {
        return { ok: false, reason: 'the page refused the files: ' + (error.message || error) };
      }
    }
    const zone = dropZone();
    if (zone) {
      for (const type of ['dragenter', 'dragover', 'drop']) {
        zone.dispatchEvent(new DragEvent(type, { bubbles: true, cancelable: true, dataTransfer: transfer }));
      }
      return { ok: true, method: 'drop', count: files.length };
    }
    return { ok: false, reason: 'no photo uploader found on this page. Drag the photo onto the page instead.' };
  }

  // -- drag and drop from the side panel ---------------------------------------------------------------
  // A drag out of an extension page cannot carry a File into a web page; the panel puts a JSON
  // description of the photo on the drag instead, and this bridge turns the drop into a real file
  // for the uploader nearest to where it landed.

  const PHOTO_MIME = 'application/x-sweetshelves-photo';

  function photoFromDrag(dataTransfer) {
    try {
      const raw = dataTransfer.getData(PHOTO_MIME);
      return raw ? JSON.parse(raw) : null;
    } catch { return null; }
  }

  async function photoBytes(entry) {
    if (entry.base64) return entry;
    // Not cached in the panel yet: ask it for the bytes (it fetches them with the sign-in cookie).
    const reply = await chrome.runtime.sendMessage({ type: 'ss-lister-photo-bytes', url: entry.url });
    if (!reply?.ok) throw new Error(reply?.reason || 'could not load the photo');
    return { ...entry, name: reply.name, type: reply.type, base64: reply.base64 };
  }

  function uploaderNear(target) {
    let node = target;
    for (let depth = 0; node && depth < 8; depth++) {
      const input = node.querySelector ? node.querySelector('input[type=file]') : null;
      if (input && !input.disabled) return input;
      node = node.parentElement;
    }
    return fileInputs()[0] || null;
  }

  async function handlePhotoDrop(event) {
    const entry = photoFromDrag(event.dataTransfer);
    if (!entry) return false;
    event.preventDefault();
    event.stopPropagation();
    try {
      const photo = await photoBytes(entry);
      const files = toFiles([photo]);
      if (!files.length) throw new Error('empty photo');
      const transfer = new DataTransfer();
      transfer.items.add(files[0]);
      // The same uploader "Send to page" uses (the multi-image input), not whatever input sits nearest the drop.
      const input = fileInputs()[0] || uploaderNear(event.target);
      if (input) {
        input.files = transfer.files;
        input.dispatchEvent(new Event('input', { bubbles: true }));
        input.dispatchEvent(new Event('change', { bubbles: true }));
      } else {
        const zone = (event.target.closest && event.target.closest('[class*="drop" i], [class*="upload" i], [class*="photo" i]')) || dropZone();
        if (!zone) throw new Error('no photo uploader near the drop');
        for (const type of ['dragenter', 'dragover', 'drop']) zone.dispatchEvent(new DragEvent(type, { bubbles: true, cancelable: true, dataTransfer: transfer }));
      }
      chrome.runtime.sendMessage({ type: 'ss-lister-dropped', ok: true, name: files[0].name, url: entry.url }).catch(() => {});
    } catch (error) {
      chrome.runtime.sendMessage({ type: 'ss-lister-dropped', ok: false, reason: String(error && error.message || error) }).catch(() => {});
    }
    return true;
  }

  function installDropBridge() {
    const isOurs = event => Array.from(event.dataTransfer?.types || []).includes(PHOTO_MIME);
    document.addEventListener('dragover', event => { if (isOurs(event)) { event.preventDefault(); event.dataTransfer.dropEffect = 'copy'; } }, true);
    document.addEventListener('dragenter', event => { if (isOurs(event)) event.preventDefault(); }, true);
    document.addEventListener('drop', event => { if (isOurs(event)) void handlePhotoDrop(event); }, true);
  }
  installDropBridge();

  // -- guided fill: a checklist overlay of what the listing still needs, colour coded ----------------
  //   red   = the store or we require it and it is empty (title, price, quantity, condition, photos...)
  //   amber = one of our fields that is empty but optional
  //   green = has a value
  // Tab jumps to the next open field, Ctrl+Enter inserts the suggestion, Esc closes the overlay.

  const CORE_TARGETS = new Set(['title', 'price', 'quantity', 'sku', 'upc', 'description', 'conditionDescription']);
  const REQUIRED_TARGETS = new Set(['title', 'price', 'quantity']);
  const TARGET_LABELS = { title: 'Title', price: 'Price', quantity: 'Quantity', sku: 'SKU / custom label', upc: 'UPC', asin: 'ASIN', brand: 'Brand', conditionDescription: 'Condition description', description: 'Description', condition: 'Condition', photos: 'Photos' };

  function photoState() {
    // eBay shows "0/25" (or 0/24, 0/12) above the photo box; Seller Central shows its own counters.
    const text = document.body ? document.body.innerText.slice(0, 20000) : '';
    const counter = text.match(/\b(\d{1,2})\s*\/\s*(12|24|25)\b/);
    const input = fileInputs()[0];
    if (!counter && !input) return null;
    const count = counter ? Number(counter[1]) : 0;
    const zone = dropZone() || (input && input.closest('section, div')) || null;
    return { count, zone, el: zone || input };
  }

  // eBay's Preferences section: "Payment policy" is a dropdown/button, not a named input, so the
  // field matcher never sees it. Find the label, then the control next to it, and read what it shows.
  const POLICY_EMPTY = /^(|select|choose|select (a|one|payment policy)|choose (a|one)|--.*|none|add( a)? (payment )?policy|create( a)? (payment )?policy)$/i;
  function policyState() {
    if (!document.body) return null;
    const labels = Array.from(document.querySelectorAll('label, span, div, h3, h4, legend, p, dt'))
      .filter(el => el.offsetParent !== null && /^payment polic(y|ies)\s*\*?$/i.test((el.textContent || '').trim()));
    for (const label of labels) {
      let box = label.parentElement;
      for (let depth = 0; box && depth < 5; depth++, box = box.parentElement) {
        const control = box.querySelector('select, button[aria-haspopup], [role="combobox"], [role="listbox"], button, input:not([type=hidden])');
        if (!control || control === label || label.contains(control)) continue;
        let value = control.tagName === 'SELECT'
          ? (control.selectedIndex >= 0 ? control.options[control.selectedIndex].text : '')
          : control.tagName === 'INPUT' ? control.value : (control.textContent || '');
        if (control.contains(label)) value = value.replace(label.textContent || '', '');
        value = value.replace(/\s+/g, ' ').trim();
        const missing = POLICY_EMPTY.test(value) || /select a payment policy|payment policy is required/i.test(box.textContent || '');
        return { done: !missing, value: missing ? '' : value, el: control };
      }
    }
    return null;
  }

  function guideNeeded({ values = {}, store = '', aspects = {}, noteFields = [], confirmFields = [] } = {}) {
    const confirmSet = new Set(confirmFields || []);
    const elements = collect();
    const descriptors = elements.map(describe);
    const assigned = M.assign(descriptors, Object.keys(M.TARGETS));
    const byIndex = {};
    for (const [target, hit] of Object.entries(assigned)) byIndex[hit.index] = target;
    const aspectHits = M.matchAspects(descriptors, aspects);
    for (const [aspect, hit] of Object.entries(aspectHits)) byIndex[hit.index] = 'aspect:' + aspect;
    const rows = [];
    const noteSet = new Set(noteFields || []);
    descriptors.forEach((d, index) => {
      const target = byIndex[index] || '';
      if (target === 'upc') return;  // filled silently; not part of the checklist
      const conditionSelect = !target && d.tag === 'select' && /condition/.test(M.normalize(d.labelText + ' ' + d.ariaLabel + ' ' + d.name));
      const required = M.isRequired(d) || REQUIRED_TARGETS.has(target) || conditionSelect;
      if (!required && !target) return;
      let suggestion = '';
      if (target.startsWith('aspect:')) suggestion = ((aspects || {})[target.slice(7)] || [])[0] || '';
      else if (target) suggestion = valueFor(target, values, store);
      else if (conditionSelect && values.condition) suggestion = (M.conditionLabels(values.condition, store) || [])[0] || '';
      rows.push({ index, target: target || (conditionSelect ? 'condition' : ''), required, done: !M.isEmptyValue(d),
        confirm: confirmSet.has(target || (conditionSelect ? 'condition' : '')),
        label: (target && TARGET_LABELS[target]) || d.labelText || d.ariaLabel || d.placeholder || d.name || d.id || d.tag,
        suggestion: String(suggestion || ''), tag: d.tag, kind: 'field',
        value: target === 'quantity' || target === 'price' ? String(d.value || '') : '',
        fromNotes: noteSet.has(target) });
    });
    const photos = photoState();
    // A ready-made Amazon catalogue listing carries the catalogue's pictures: photos are optional there.
    if (photos) rows.unshift({ index: -1, target: 'photos', required: store !== 'amazon', done: photos.count > 0, label: 'Photos', suggestion: store === 'amazon' ? 'Optional on a catalogue listing' : 'Send to page or drag from the panel', tag: 'photos', kind: 'photos', el: photos.el });
    const payment = store === 'amazon' ? null : policyState();
    // Only worth a row while it is still empty: once a policy is picked there is nothing to do.
    if (payment && !payment.done) rows.push({ index: -1, target: 'paymentPolicy', required: true, done: false, label: 'Payment policy', suggestion: 'Pick a payment policy', tag: 'policy', kind: 'policy', el: payment.el, value: payment.value });
    // Required first, then our fields, keeping page order inside each group; price and then
    // quantity go last because that is where eBay's form ends, and the payment policy after them.
    // Seller Central's offer page is the other way round: quantity, price, then the condition.
    const tail = store === 'amazon'
      ? r => (r.target === 'quantity' ? -3 : r.target === 'price' ? -2 : r.target === 'condition' ? -1 : 0)
      : r => (r.target === 'price' ? 1 : r.target === 'quantity' ? 2 : r.target === 'paymentPolicy' ? 3 : 0);
    rows.sort((a, b) => (tail(a) - tail(b)) || (Number(b.required) - Number(a.required)));
    return { elements, rows };
  }

  // Soft rounded glow instead of a hard outline: page containers clip outlines into brackets.
  const RING_PROPS = ['boxShadow', 'borderRadius', 'backgroundColor', 'transition'];
  function ring(el, colour, fill) {
    if (el.dataset.ssRingPrev === undefined) el.dataset.ssRingPrev = JSON.stringify(RING_PROPS.map(k => el.style[k] || ''));
    const rgb = colour.match(/\w\w/g).map(h => parseInt(h, 16)).join(',');
    el.style.transition = 'box-shadow .25s ease, background-color .25s ease';
    el.style.boxShadow = `0 0 0 2px rgba(${rgb},.55), 0 0 0 6px rgba(${rgb},.14)`;
    if (!/[1-9]/.test(getComputedStyle(el).borderRadius)) el.style.borderRadius = '8px';
    if (fill) el.style.backgroundColor = `rgba(${rgb},.07)`;
  }
  function unring(el) {
    if (el.dataset.ssRingPrev === undefined) return;
    try {
      const prev = JSON.parse(el.dataset.ssRingPrev);
      // Keep our transition until the glow has faded, then put the page's own back.
      RING_PROPS.forEach((k, i) => { if (k !== 'transition') el.style[k] = prev[i]; });
      setTimeout(() => { if (el.dataset.ssRingPrev === undefined) el.style.transition = prev[3]; }, 300);
    } catch { /* ignore */ }
    delete el.dataset.ssRingPrev;
  }

  function guideElement(row) {
    return row.kind === 'photos' || row.kind === 'policy' ? row.el : guide.elements[row.index];
  }

  function guideStyle(row, state) {
    const el = guideElement(row);
    if (!el) return;
    try {
      // The 2 s refresh restyles every row: a field already done must not flash green again (it blinked).
      if (state === 'done' && el.dataset.ssGuideDone === '1') return;
      const colour = state === 'notes' ? '#2563eb' : (state === 'done' ? '#16a34a' : (state === 'required' ? '#dc2626' : '#f59e0b'));
      ring(el, colour);
      // A filled field fades back; one filled from the prep notes keeps its blue outline as a reminder to read it.
      if (state === 'done') setTimeout(() => { if (el.dataset.ssGuideDone === '1') unring(el); }, 2500);
      el.dataset.ssGuideDone = state === 'done' ? '1' : '';
    } catch { /* ignore */ }
  }

  function guideUnstyle(row) {
    const el = guideElement(row);
    if (!el) return;
    try { unring(el); delete el.dataset.ssGuideDone; } catch { /* ignore */ }
  }

  // The page's own "List it" / "Save and finish" button: scroll there and flash it (the user presses it).
  function findSubmitButton() {
    const words = /^(list it|list item|list your item|save and finish|save and continue|submit listing|publish|list now|continue to listing)$/i;
    const candidates = Array.from(document.querySelectorAll('button, input[type=submit], a[role=button], [role=button]'))
      .filter(isVisible)
      .filter(b => words.test(clip((b.textContent || '') + (b.value || '')).trim()));
    return candidates[candidates.length - 1] || null;
  }

  function goToSubmit() {
    const button = findSubmitButton();
    if (!button) { window.scrollTo({ top: document.body.scrollHeight, behavior: 'smooth' }); return false; }
    try { button.scrollIntoView({ block: 'center', behavior: 'smooth' }); button.focus({ preventScroll: true }); } catch { /* ignore */ }
    const previous = button.style.boxShadow;
    button.style.boxShadow = '0 0 0 4px rgba(22,163,74,.75)';
    setTimeout(() => { button.style.boxShadow = previous; }, 2500);
    return true;
  }

  // Buttons on the overlay that need the server go through the side panel.
  function panelAction(action, extra) {
    chrome.runtime.sendMessage({ type: 'ss-lister-action', action, ...(extra || {}) }).catch(() => {});
  }

  // The HUD's dragged position is remembered for this browser: localStorage answers instantly on the next
  // page, chrome.storage.local carries it across stores/tabs and survives the site's storage being cleared.
  const GUIDE_POS_KEY = 'ss-lister-guide-pos';
  let guidePos = null;

  function readGuidePos(raw) {
    try {
      const pos = typeof raw === 'string' ? JSON.parse(raw) : raw;
      if (pos && Number.isFinite(pos.left) && Number.isFinite(pos.top)) return { left: pos.left, top: pos.top };
    } catch { /* ignore */ }
    return null;
  }

  function applyGuidePos(panel, pos) {
    if (!panel || !pos) return;
    panel.style.left = Math.max(0, Math.min(pos.left, window.innerWidth - 80)) + 'px';
    panel.style.top = Math.max(0, Math.min(pos.top, window.innerHeight - 60)) + 'px';
    panel.style.bottom = 'auto';
  }

  function saveGuidePos(pos) {
    guidePos = pos;
    try { localStorage.setItem(GUIDE_POS_KEY, JSON.stringify(pos)); } catch { /* private mode */ }
    try { chrome.storage?.local?.set({ [GUIDE_POS_KEY]: pos }); } catch { /* ignore */ }
  }

  // Both stores share the position, so read the profile-wide copy too and move the HUD when it wins.
  function loadGuidePos(panel) {
    try {
      chrome.storage?.local?.get(GUIDE_POS_KEY).then(data => {
        const pos = readGuidePos(data?.[GUIDE_POS_KEY]);
        if (pos && !guidePos && panel.isConnected) { guidePos = pos; applyGuidePos(panel, pos); }
      }).catch(() => {});
    } catch { /* ignore */ }
  }

  // The HUD wears the side panel's theme: day by default, night when the panel's moon button says so.
  const GUIDE_THEMES = {
    light: {
      bg: '#ffffff', text: '#0f172a', head: '#f5f8f6', foot: '#ffffff', line: '#e3e9e5', rail: '#dfe6e2',
      muted: '#5b6a63', value: '#0f172a', hint: '#1d4ed8', good: '#15803d', req: '#b91c1c', current: '#e4f3ec',
      chip: '#eef2f0', btn: '#eef2f0', btnText: '#0f172a', shadow: '0 12px 40px rgba(15,23,42,.22)',
    },
    dark: {
      bg: '#0f172a', text: '#f8fafc', head: '#18212f', foot: '#0f172a', line: '#2c3a4d', rail: '#31415a',
      muted: '#cbd5e1', value: '#e2e8f0', hint: '#93c5fd', good: '#86efac', req: '#fca5a5', current: '#13312a',
      chip: '#22303f', btn: '#22303f', btnText: '#ffffff', shadow: '0 12px 40px rgba(0,0,0,.45)',
    },
  };
  const GUIDE_SETTINGS_KEY = 'ssListerSettings';
  let guideTheme = GUIDE_THEMES.light;

  function useGuideTheme(name) {
    const theme = GUIDE_THEMES[name === 'dark' ? 'dark' : 'light'];
    if (theme === guideTheme) return;
    guideTheme = theme;
    paintGuide();
  }

  function loadGuideTheme() {
    try {
      chrome.storage?.local?.get(GUIDE_SETTINGS_KEY)
        .then(data => useGuideTheme(data?.[GUIDE_SETTINGS_KEY]?.theme))
        .catch(() => {});
    } catch { /* ignore */ }
  }

  try {
    chrome.storage?.onChanged?.addListener((changes, area) => {
      if (area === 'local' && changes[GUIDE_SETTINGS_KEY]) useGuideTheme(changes[GUIDE_SETTINGS_KEY].newValue?.theme);
    });
  } catch { /* ignore */ }

  // Repaint the chrome that guideRender does not rebuild (the rows carry their own colours).
  function paintGuide() {
    const panel = guide?.panel;
    if (!panel) return;
    const t = guideTheme;
    panel.style.background = t.bg;
    panel.style.color = t.text;
    panel.style.boxShadow = t.shadow;
    panel.querySelector('[data-ss="head"]').style.background = t.head;
    panel.querySelector('[data-ss="bar"]').style.background = t.bg;
    panel.querySelector('[data-ss="railline"]').style.background = t.rail;
    panel.querySelector('[data-ss="foot"]').style.background = t.foot;
    for (const button of panel.querySelectorAll('[data-ss="collapse"],[data-ss="done"],[data-ss="steps"],[data-ss="next"]')) button.style.color = t.muted;
    guideRender(false);
  }

  // Red = required and empty, amber = optional and empty, green = filled, blue = filled from the prep notes.
  // We put the value in (warehouse quantity, eBay's lowest price): green would say "nothing to do
  // here", and the user asked to be walked past it anyway. So it is its own kind of checkpoint.
  function rowCheck(row) {
    return Boolean(row && row.confirm && row.done && guide && !guide.seen.has(row.target || row.label));
  }

  function rowOpen(row) {
    return !row.done || rowCheck(row);
  }

  function markSeen(row) {
    if (row && row.confirm && guide) guide.seen.add(row.target || row.label);
  }

  function rowColour(row) {
    if (rowCheck(row)) return '#7c3aed';
    if (row.done) return row.fromNotes ? '#2563eb' : '#16a34a';
    return row.required ? '#dc2626' : '#f59e0b';
  }

  // The one step you are on - or the checkpoint you are hovering - written out in full.
  function paintFocus() {
    const panel = guide?.panel;
    if (!panel || guide.steps || guide.collapsed) return;
    const t = guideTheme;
    const body = panel.querySelector('[data-ss="body"]');
    const previewing = guide.preview != null && guide.preview !== guide.index;
    const row = guide.rows[guide.preview != null ? guide.preview : guide.index];
    // Every focus card is the same height. It used to grow and shrink with whatever step was under
    // the cursor, and because the overlay is pinned by its bottom edge that moved the rail out from
    // under the pointer, which bounced hover on and off.
    const CARD = 'padding:9px 12px 12px;height:112px;box-sizing:border-box;display:flex;flex-direction:column';
    if (!row) {
      body.innerHTML = `<div style="${CARD};justify-content:center;color:${t.muted};font-size:12px">${guide.rows.length ? 'Pick a checkpoint above, or press Tab for the first one that needs a value.' : 'No listing fields found on this page yet.'}</div>`;
      return;
    }
    const colour = rowColour(row);
    const flag = rowCheck(row) ? 'check it' : row.done ? (row.fromNotes ? 'from the notes' : 'filled') : (row.required ? 'required' : 'optional');
    const ai = guide.options.aiFields?.[row.target];
    const said = row.done
      ? (row.value ? escapeHtml(row.value) : '<span style="opacity:.75">already on the page</span>') + (ai ? ` <span style="color:${t.good}">\u00b7 written by AI${ai === 'auto' ? ' automatically' : ''}</span>` : '')
      : (row.suggestion ? escapeHtml(row.suggestion) : `<span style="color:${t.muted}">Nothing prepared for this one \u2014 fill it on the page.</span>`);
    const canAi = row.target === 'title' || row.target === 'description';
    body.innerHTML = `<div style="${CARD}">
        <div style="display:flex;align-items:center;gap:8px;flex:none">
          <span style="width:8px;height:8px;border-radius:50%;background:${colour};flex:none"></span>
          <b style="font-size:13.5px;flex:1;min-width:0;white-space:nowrap;overflow:hidden;text-overflow:ellipsis">${escapeHtml(row.label)}</b>
          <span style="font:700 9px system-ui,sans-serif;letter-spacing:.1em;text-transform:uppercase;color:${colour};flex:none">${flag}</span>
        </div>
        <div style="margin-top:6px;color:${row.done ? t.value : t.muted};font-size:12px;height:34px;overflow:hidden;flex:none">${said}</div>
        <div style="margin-top:auto;height:26px;display:flex;align-items:center;flex:none">${previewing
          ? `<span style="color:${t.muted};font-size:11px">Click the dot to go here</span>`
          : (canAi ? `<button data-ss-ai="${row.target}" title="Write the ${row.target} with AI from the item and its notes" style="background:${t.chip};color:${t.text};border:0;border-radius:6px;padding:5px 9px;cursor:pointer;font:600 11px system-ui,sans-serif">Write it with AI</button>` : '')}</div>
      </div>`;
    const aiBtn = body.querySelector('[data-ss-ai]');
    if (aiBtn) aiBtn.onclick = event => { event.stopPropagation(); aiBtn.textContent = '\u2026'; panelAction('generate', { kind: aiBtn.dataset.ssAi }); };
  }

  function guidePanel() {
    if (guide.panel) return guide.panel;
    const panel = document.createElement('div');
    panel.id = 'ss-lister-guide';
    panel.style.cssText = `position:fixed;left:16px;bottom:16px;z-index:2147483647;width:min(300px,calc(100vw - 32px));max-height:min(70vh,560px);display:flex;flex-direction:column;background:${guideTheme.bg};color:${guideTheme.text};font:13px/1.4 system-ui,sans-serif;border-radius:13px;box-shadow:${guideTheme.shadow};overflow:hidden`;
    // Wherever the user dragged it last stays put for this browser (the header is the drag handle).
    try { guidePos = guidePos || readGuidePos(localStorage.getItem(GUIDE_POS_KEY)); } catch { /* private mode */ }
    applyGuidePos(panel, guidePos);
    panel.innerHTML = `
      <div data-ss="head" style="display:flex;align-items:center;gap:8px;padding:9px 10px;background:${guideTheme.head};cursor:pointer">
        <span data-ss="dot" style="width:9px;height:9px;border-radius:50%;background:#0a9c6c;flex:none"></span>
        <b data-ss="title" style="flex:1;min-width:0;font-size:12.5px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis">Sweet Shelves Lister</b>
        <button data-ss="steps" title="Every step" style="background:transparent;border:0;color:${guideTheme.muted};font-size:13px;cursor:pointer;padding:0 4px">\u2304</button>
        <button data-ss="collapse" title="Minimize" style="background:transparent;border:0;color:${guideTheme.muted};font-size:16px;cursor:pointer;padding:0 4px">\u2013</button>
        <button data-ss="done" title="Close the overlay (Esc)" style="background:transparent;border:0;color:${guideTheme.muted};font-size:16px;cursor:pointer;padding:0 4px">\u00d7</button>
      </div>
      <div data-ss="bar" style="position:relative;padding:11px 12px 10px;background:${guideTheme.bg}">
        <div data-ss="railline" style="position:absolute;left:14px;right:14px;top:50%;height:2px;margin-top:-1px;border-radius:2px;background:${guideTheme.rail}"></div>
        <div data-ss="rail" style="position:relative;display:flex;align-items:center;overflow-x:auto;overflow-y:hidden;scrollbar-width:none"></div>
      </div>
      <div data-ss="body" style="overflow:auto"></div>
      <div data-ss="foot" style="display:flex;gap:6px;align-items:center;padding:0 12px 12px;background:${guideTheme.foot}">
        <button data-ss="use" style="flex:1;background:#0a9c6c;color:#fff;border:0;border-radius:8px;padding:9px 11px;cursor:pointer;font:600 12.5px system-ui,sans-serif">Use this</button>
        <button data-ss="next" style="background:transparent;color:${guideTheme.muted};border:0;border-radius:8px;padding:9px 6px;cursor:pointer;font:500 12.5px system-ui,sans-serif">Skip</button>
        <button data-ss="ready" title="Everything has a value: jump to the page's List it button" style="display:none;flex:1 1 100%;background:#16a34a;color:#fff;border:0;border-radius:10px;padding:10px 14px;cursor:pointer;font:700 13px system-ui,sans-serif;box-shadow:0 4px 14px rgba(22,163,74,.4)">\u2713 All set \u2014 go to List it</button>
      </div>`;
    // The green button does whatever the step in front of you needs: take the suggestion, go to the
    // field when there is nothing to take, or move on when it already has a value.
    panel.querySelector('[data-ss="use"]').onclick = () => {
      const row = guide?.rows[guide.index];
      if (!row) { guideNext(); return; }
      if (!row.done && row.suggestion && row.kind === 'field') guideUse();
      else if (!row.done) guideGo(guide.index);
      else guideNext();
    };
    panel.querySelector('[data-ss="next"]').onclick = () => guideNext();
    panel.querySelector('[data-ss="ready"]').onclick = () => goToSubmit();
    panel.querySelector('[data-ss="done"]').onclick = () => { guideStop(); notifyGuide(); };
    panel.querySelector('[data-ss="steps"]').onclick = event => {
      event.stopPropagation();
      guide.steps = !guide.steps; guide.preview = null;
      if (guide.steps) guide.collapsed = false;
      guideRender();
    };
    panel.querySelector('[data-ss="collapse"]').onclick = event => { event.stopPropagation(); guide.collapsed = !guide.collapsed; guide.preview = null; guideRender(); };
    panel.querySelector('[data-ss="head"]').onclick = () => { if (guide.collapsed && !panel.dataset.ssDragged) { guide.collapsed = false; guideRender(); } delete panel.dataset.ssDragged; };
    // Drag the overlay by its header.
    const head = panel.querySelector('[data-ss="head"]');
    head.style.cursor = 'move';
    head.addEventListener('pointerdown', event => {
      if (event.target.closest('button')) return;
      const rect = panel.getBoundingClientRect();
      const offset = { x: event.clientX - rect.left, y: event.clientY - rect.top };
      let moved = false;
      const move = e => {
        moved = true;
        panel.style.left = Math.max(0, Math.min(e.clientX - offset.x, window.innerWidth - rect.width)) + 'px';
        panel.style.top = Math.max(0, Math.min(e.clientY - offset.y, window.innerHeight - 40)) + 'px';
        panel.style.bottom = 'auto';
      };
      const up = () => {
        document.removeEventListener('pointermove', move); document.removeEventListener('pointerup', up);
        if (moved) { panel.dataset.ssDragged = '1'; saveGuidePos({ left: parseFloat(panel.style.left), top: parseFloat(panel.style.top) }); }
      };
      document.addEventListener('pointermove', move); document.addEventListener('pointerup', up);
      event.preventDefault();
    });
    document.documentElement.appendChild(panel);
    guide.panel = panel;
    loadGuidePos(panel);
    loadGuideTheme();
    return panel;
  }

  function guidePointer() {
    if (guide.pointer) return guide.pointer;
    const tag = document.createElement('div');
    tag.style.cssText = 'position:absolute;z-index:2147483646;background:#dc2626;color:#fff;font:600 12px system-ui,sans-serif;padding:4px 10px;border-radius:999px;box-shadow:0 4px 14px rgba(0,0,0,.3);pointer-events:none;max-width:320px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis';
    document.documentElement.appendChild(tag);
    guide.pointer = tag;
    return tag;
  }

  // -- warehouse quantity badge --------------------------------------------------------------------
  // The store's quantity box is a promise; this pins what the rack actually holds right under it, so
  // nobody lists three of something we own one of. The side panel supplies the numbers at guide-start.
  function qtyBadge() {
    if (guide.qtyTag) return guide.qtyTag;
    const tag = document.createElement('div');
    tag.dataset.ssQty = '1';
    tag.style.cssText = 'position:absolute;z-index:2147483646;display:none;color:#fff;font:600 12px system-ui,sans-serif;padding:4px 10px;border-radius:999px;box-shadow:0 4px 14px rgba(0,0,0,.25);pointer-events:none;white-space:nowrap;max-width:min(360px,90vw);overflow:hidden;text-overflow:ellipsis';
    document.documentElement.appendChild(tag);
    guide.qtyTag = tag;
    return tag;
  }

  function placeQtyBadge() {
    if (!guide) return;
    const tag = qtyBadge();
    const stock = guide.options.stock || {};
    const rack = Number(stock.rack);
    const row = guide.rows.find(r => r.kind === 'field' && r.target === 'quantity');
    const el = row ? guide.elements[row.index] : null;
    if (!el || !el.isConnected || !Number.isFinite(rack) || !isVisible(el)) { tag.style.display = 'none'; return; }
    const listable = Number.isFinite(Number(stock.listable)) ? Number(stock.listable) : rack;
    const typed = Number(String(row.value || '').replace(/[^0-9.-]/g, ''));
    // Typing more than we can ship is the mistake worth shouting about.
    const over = Number.isFinite(typed) && typed > listable;
    const parts = [rack + ' on the rack'];
    if (listable !== rack) parts.push(listable + ' listable');
    if (Array.isArray(stock.positions) && stock.positions.length) parts.push('@ ' + stock.positions.slice(0, 3).join(', '));
    tag.textContent = (over ? '⚠ ' : '📦 ') + 'Warehouse: ' + parts.join(' · ') + (over ? ' — you typed ' + typed : '');
    tag.style.background = over ? '#dc2626' : (rack ? '#0a9c6c' : '#b45309');
    const rect = el.getBoundingClientRect();
    tag.style.display = '';
    tag.style.left = Math.max(8, rect.left + window.scrollX) + 'px';
    tag.style.top = (rect.bottom + window.scrollY + 6) + 'px';
  }

  // scroll=true only for an explicit jump (click, Next, Tab); refreshes never move the page.
  // scroll=true only for an explicit jump (click, Next, Tab); refreshes never move the page.
  function guideRender(scroll = false) {
    if (!guide) return;
    const t = guideTheme;
    const open = guide.rows.filter(r => !r.done);
    const requiredOpen = open.filter(r => r.required);
    const current = guide.rows[guide.index];
    const panel = guidePanel();
    const total = guide.rows.length;
    const allSet = Boolean(total) && !open.length;
    panel.querySelector('[data-ss="title"]').textContent = allSet
      ? `All ${total} have a value`
      : (open.length ? `${open.length} left \u00b7 ${requiredOpen.length} required` : 'Nothing to fill here yet');
    panel.querySelector('[data-ss="dot"]').style.background = allSet ? '#16a34a' : (requiredOpen.length ? '#dc2626' : '#f59e0b');
    panel.querySelector('[data-ss="collapse"]').textContent = guide.collapsed ? '+' : '\u2013';
    const stepsBtn = panel.querySelector('[data-ss="steps"]');
    stepsBtn.textContent = guide.steps ? '\u2303' : '\u2304';
    stepsBtn.title = guide.steps ? 'Back to the step you are on' : 'Every step';

    // The checkpoints ARE the progress bar: how much of it is green says how far along the listing is,
    // and every step stays one click away without a list in front of the field you are typing in.
    const rail = panel.querySelector('[data-ss="rail"]');
    rail.innerHTML = guide.rows.map((row, i) => {
      const colour = rowColour(row);
      const here = guide.index === i;
      const size = here ? 11 : 8;
      const ring = here ? `,0 0 0 2px ${t.bg},0 0 0 4px ${colour}` : '';
      const state = row.done ? (row.value || 'filled') : (row.required ? 'required' : 'optional');
      return `<button data-ss-cp="${i}" title="${escapeHtml(row.label + ' \u00b7 ' + state)}" aria-label="${escapeHtml(row.label)}" style="flex:1 1 0;min-width:15px;height:18px;display:flex;align-items:center;justify-content:center;background:none;border:0;padding:0;cursor:pointer">
        <span style="width:${size}px;height:${size}px;border-radius:50%;background:${colour};box-shadow:0 0 0 3px ${t.bg}${ring}"></span></button>`;
    }).join('');
    for (const el of rail.querySelectorAll('[data-ss-cp]')) {
      const i = Number(el.dataset.ssCp);
      el.onclick = () => { guide.preview = null; guideGo(i); };
      // Hovering reads a step out in the card below without leaving the field you are in.
      el.onpointerenter = () => { if (!guide.collapsed && !guide.steps) { guide.preview = i; paintFocus(); } };
      el.onpointerleave = () => { if (guide.preview != null) { guide.preview = null; paintFocus(); } };
    }

    const body = panel.querySelector('[data-ss="body"]');
    const foot = panel.querySelector('[data-ss="foot"]');
    body.style.display = guide.collapsed ? 'none' : '';
    foot.style.display = guide.collapsed || (guide.steps && !allSet) ? 'none' : '';
    if (guide.steps) {
      body.innerHTML = guide.rows.map((row, i) => {
        const colour = rowColour(row);
        const here = guide.index === i;
        const ai = guide.options.aiFields?.[row.target];
        const mark = row.done && row.fromNotes
          ? ` <sup title="Filled from the warehouse prep notes. Read it once before listing." style="color:#2563eb;font-size:8px;font-weight:700;letter-spacing:.04em">NOTE</sup>`
          : (row.done && ai ? ` <sup title="generated with AI${ai === 'auto' ? ' (automatically)' : ''}" style="color:${t.good};font-size:8px;font-weight:700;letter-spacing:.04em">AI</sup>` : '');
        const state = row.done ? (row.value || '') : (row.required ? 'required' : 'optional');
        return `<div data-ss-row="${i}" style="display:flex;align-items:center;gap:9px;padding:6px 12px;cursor:pointer;${here ? `background:${t.current};` : ''}">
          <span style="width:8px;height:8px;border-radius:50%;background:${colour};flex:none"></span>
          <span style="flex:1;min-width:0;white-space:nowrap;overflow:hidden;text-overflow:ellipsis"><span style="font-weight:${here ? 700 : 500}">${escapeHtml(row.label)}</span>${state ? ` <span style="color:${row.done ? t.muted : colour};font-size:11.5px">\u00b7 ${escapeHtml(state)}</span>` : ''}${mark}</span>
          ${row.target === 'title' || row.target === 'description' ? `<button data-ss-ai="${row.target}" title="Write the ${row.target} with AI from the item and its notes" style="background:${t.chip};color:${t.text};border:0;border-radius:5px;padding:3px 7px;cursor:pointer;font:700 9.5px system-ui,sans-serif;flex:none">AI</button>` : ''}</div>`;
      }).join('') || `<div style="padding:10px 12px;color:${t.muted}">No listing fields found on this page yet.</div>`;
      for (const el of body.querySelectorAll('[data-ss-row]')) el.onclick = () => { guide.steps = false; guideGo(Number(el.dataset.ssRow)); };
      for (const el of body.querySelectorAll('[data-ss-ai]')) el.onclick = event => { event.stopPropagation(); el.textContent = '\u2026'; panelAction('generate', { kind: el.dataset.ssAi }); };
    } else {
      paintFocus();
    }

    const use = panel.querySelector('[data-ss="use"]');
    const next = panel.querySelector('[data-ss="next"]');
    panel.querySelector('[data-ss="ready"]').style.display = allSet ? '' : 'none';
    use.style.display = allSet || guide.steps ? 'none' : '';
    next.style.display = allSet || guide.steps || !open.length ? 'none' : '';
    if (!allSet && !guide.steps) {
      const takes = current && !current.done && current.suggestion && current.kind === 'field';
      const label = !current ? 'Start at the first one' : (current.done ? 'Next field' : (takes ? 'Use this' : 'Go to it'));
      const key = !current ? 'Tab' : (current.done ? 'Tab' : (takes ? 'Ctrl+Enter' : ''));
      use.innerHTML = `${escapeHtml(label)}${key ? ` <span style="opacity:.72;font-size:11px;font-weight:500">${key}</span>` : ''}`;
    }

    for (const row of guide.rows) guideStyle(row, row.done ? (row.fromNotes ? 'notes' : 'done') : (row.required ? 'required' : 'optional'));
    const pointer = guidePointer();
    const el = current ? guideElement(current) : null;
    if (el) {
      // A green (filled) row still takes the user to that spot when clicked.
      if (scroll) { try { el.scrollIntoView({ block: 'center', behavior: 'smooth' }); if (current.kind === 'field') el.focus({ preventScroll: true }); } catch { /* ignore */ } }
      const rect = el.getBoundingClientRect();
      pointer.style.display = '';
      pointer.style.background = rowColour(current);
      // The suggestion is in the overlay a few centimetres away; saying it twice is the clutter.
      pointer.textContent = current.done ? `${current.label} \u2713` : current.label;
      pointer.style.left = Math.max(8, rect.left + window.scrollX) + 'px';
      pointer.style.top = Math.max(0, rect.top + window.scrollY - 30) + 'px';
    } else {
      pointer.style.display = 'none';
    }
    placeQtyBadge();
  }

  function escapeHtml(value) {
    return String(value ?? '').replace(/[&<>"']/g, ch => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[ch]));
  }

  function notifyGuide() {
    chrome.runtime.sendMessage({ type: 'ss-lister-guide', state: guideState() }).catch(() => {});
  }

  function guideState() {
    if (!guide) return { active: false, needed: [], rows: [], index: -1 };
    const rows = guide.rows.map(r => ({ index: r.index, target: r.target, required: r.required, done: r.done, label: r.label, suggestion: r.suggestion, kind: r.kind, value: r.value || '', fromNotes: Boolean(r.fromNotes), check: rowCheck(r) }));
    const open = rows.filter(r => !r.done || r.check);
    return { active: true, index: guide.index, rows, needed: open, open: open.length, total: rows.length };
  }

  // Seller Central's catalogue steps - the product search and the "Product information" flyout that
  // follows a UPC - are not the offer form: there is no price or quantity to set, so the HUD has
  // nothing to say and would only cover the page. The real form can render late, so keep looking.
  let guideWaitUntil = 0;
  let guideWaitTimer = null;
  function offerPage(rows) {
    return rows.some(r => r.target === 'price' || r.target === 'quantity');
  }

  function guideStart(options) {
    // Already up on this page (the URL changed, the fill ran again): refresh in place, never jump back to the first row.
    if (guide) { guide.options = options || guide.options; guideRefresh(); guideRender(false); notifyGuide(); return guideState(); }
    const { elements, rows } = guideNeeded(options || {});
    if (!offerPage(rows)) {
      if (!guideWaitUntil) guideWaitUntil = Date.now() + 20000;
      clearTimeout(guideWaitTimer);
      if (Date.now() < guideWaitUntil) guideWaitTimer = setTimeout(() => { if (!guide) guideStart(options); }, 1500);
      return guideState();
    }
    guideWaitUntil = 0;
    clearTimeout(guideWaitTimer);
    guide = { elements, rows, index: -1, options: options || {}, panel: null, pointer: null, qtyTag: null, collapsed: false, steps: false, preview: null, seen: new Set() };
    const handlers = {
      key(event) {
        if (!guide) return;
        if (event.key === 'Escape') { guideStop(); notifyGuide(); return; }
        const onGuided = guide.rows.some(r => r.kind === 'field' && (guide.elements[r.index] === event.target || guide.elements[r.index].contains(event.target)));
        if (event.key === 'Tab' && !event.shiftKey && (onGuided || event.target === document.body)) {
          event.preventDefault(); event.stopPropagation(); guideNext();
        } else if (event.key === 'Tab' && event.shiftKey && onGuided) {
          event.preventDefault(); event.stopPropagation(); guideGo(guide.index - 1);
        } else if (event.key === 'Enter' && (event.ctrlKey || event.metaKey)) {
          event.preventDefault(); guideUse();
        }
      },
      // The badge is glued to the quantity box, so it has to follow the page, not the 2 s refresh.
      reposition() { placeQtyBadge(); },
      change() { clearTimeout(guide?.timer); if (guide) guide.timer = setTimeout(() => { if (guide) { guideRefresh(); guideRender(); notifyGuide(); } }, 250); },
    };
    document.addEventListener('keydown', handlers.key, true);
    document.addEventListener('input', handlers.change, true);
    document.addEventListener('change', handlers.change, true);
    window.addEventListener('scroll', handlers.reposition, true);
    window.addEventListener('resize', handlers.reposition);
    guide.handlers = handlers;
    guide.ticker = setInterval(() => { if (guide) { guideRefresh(); guideRender(); } }, 2000);
    guide.index = rows.findIndex(rowOpen);
    markSeen(guide.rows[guide.index]);
    guideRender(false); notifyGuide();
    return guideState();
  }

  function guideRefresh() {
    if (!guide) return guideState();
    // eBay adds fields late (the condition description appears once a used condition is chosen):
    // rebuild the checklist when the page's fields changed, keeping the current row by target.
    const fresh = collect();
    if (fresh.length !== guide.elements.length || fresh.some((el, i) => el !== guide.elements[i])) {
      const currentTarget = guide.rows[guide.index]?.target;
      const built = guideNeeded(guide.options);
      for (const row of built.rows) guideUnstyle(row);
      for (const row of guide.rows) guideUnstyle(row);
      guide.elements = built.elements; guide.rows = built.rows;
      guide.index = currentTarget ? guide.rows.findIndex(r => r.target === currentTarget) : -1;
    }
    for (const row of guide.rows) {
      if (row.kind === 'photos') { const photos = photoState(); row.done = Boolean(photos && photos.count > 0); if (photos) row.el = photos.el; }
      else if (row.kind === 'policy') { const policy = policyState(); row.done = Boolean(policy && policy.done); row.value = policy ? policy.value : ''; if (policy) row.el = policy.el; }
      else {
        const d = describe(guide.elements[row.index]);
        row.done = !M.isEmptyValue(d);
        if (row.target === 'quantity' || row.target === 'price') row.value = String(d.value || '');
      }
    }
    return guideState();
  }

  function guideNext() {
    if (!guide) return guideState();
    guideRefresh();
    const open = guide.rows.map((r, i) => (rowOpen(r) ? i : -1)).filter(i => i >= 0);
    if (!open.length) { guide.index = -1; guideRender(); notifyGuide(); return guideState(); }
    const after = open.find(i => i > guide.index);
    guide.index = after !== undefined ? after : open[0];
    markSeen(guide.rows[guide.index]);  // landing on it is the look it was asking for
    guideRender(true);
    notifyGuide();
    return guideState();
  }

  function guideGo(index) {
    if (!guide) return guideState();
    guideRefresh();
    if (!guide.rows.length) { guideRender(); return guideState(); }
    guide.index = ((index % guide.rows.length) + guide.rows.length) % guide.rows.length;
    markSeen(guide.rows[guide.index]);
    guideRender(true);
    notifyGuide();
    return guideState();
  }

  function guideUse() {
    if (!guide) return guideState();
    const current = guide.rows[guide.index];
    if (!current || !current.suggestion || current.kind !== 'field') return guideState();
    const el = guide.elements[current.index];
    const options = {};
    if (current.target === 'description') options.html = (guide.options.values || {}).descriptionHtml || '';
    if (current.target.startsWith('aspect:')) options.labels = (guide.options.aspects || {})[current.target.slice(7)] || [current.suggestion];
    if (current.target === 'condition') options.labels = M.conditionLabels((guide.options.values || {}).condition, guide.options.store) || [current.suggestion];
    if (setField(el, current.suggestion, options)) flash(el);
    return guideNext();
  }

  function guideStop() {
    if (!guide) return false;
    for (const row of guide.rows) guideUnstyle(row);
    if (guide.handlers) {
      document.removeEventListener('keydown', guide.handlers.key, true);
      document.removeEventListener('input', guide.handlers.change, true);
      document.removeEventListener('change', guide.handlers.change, true);
      window.removeEventListener('scroll', guide.handlers.reposition, true);
      window.removeEventListener('resize', guide.handlers.reposition);
    }
    clearInterval(guide.ticker); clearTimeout(guide.timer);
    if (guide.panel) guide.panel.remove();
    if (guide.pointer) guide.pointer.remove();
    if (guide.qtyTag) guide.qtyTag.remove();
    guide = null;
    return true;
  }

  // -- assist: the store's steps before the form (category, catalog match, condition) ------------------
  // The user decides; the panel highlights its guess and reports what was clicked so it can learn.

  let assist = null;

  function categoryDialog() {
    for (const dialog of document.querySelectorAll('[role="dialog"], dialog, [class*="dialog" i], [class*="modal" i]')) {
      if (!isVisible(dialog)) continue;
      const heading = dialog.querySelector('h1, h2, h3, [role="heading"]');
      if (heading && /^\s*category\s*$/i.test(heading.textContent || '') && /suggested|all categories/i.test(dialog.innerText || '')) return dialog;
    }
    return null;
  }

  function pathTexts(root) {
    // Leaf elements whose text looks like "Home & Garden > Kitchen ... > Salt & Pepper".
    const out = [];
    for (const el of root.querySelectorAll('a, li, button, div, span, p')) {
      if (el.children.length > 2 || !isVisible(el)) continue;
      const text = clip(el.textContent).slice(0, MAX_TEXT * 3);
      if (/ > .+ > /.test(text) && text.length < 220) out.push({ el, text });
    }
    // Only the innermost matches: a wrapper whose text also holds the dialog title is not a path.
    return out.filter(entry => !out.some(other => other !== entry && entry.el !== other.el && entry.el.contains(other.el)));
  }

  function matchCards() {
    const cards = [];
    const seen = new Set();
    for (const link of document.querySelectorAll('a[href*="/itm/"], a[href*="itemId="], [role="listitem"], li, article')) {
      const href = link.getAttribute('href') || '';
      const id = (href.match(/\/itm\/(?:[^/]+\/)?(\d{9,15})/) || href.match(/itemId=(\d{9,15})/) || [])[1] || '';
      const card = link.closest('li, article, [class*="card" i], [class*="item" i]') || link;
      if (!isVisible(card) || seen.has(card)) continue;
      const heading = card.querySelector('h1, h2, h3, h4, [class*="title" i], b, strong') || card;
      const title = clip(heading.textContent).slice(0, 160);
      if (!title || title.length < 8) continue;
      seen.add(card);
      cards.push({ el: card, title, itemId: id });
    }
    return cards;
  }

  function badge(el, label, kind) {
    const tag = document.createElement('span');
    tag.textContent = label;
    tag.dataset.ssBadge = '1';
    tag.style.cssText = 'position:absolute;z-index:2147483646;background:' + (kind === 'best' ? '#0a9c6c' : '#64748b') + ';color:#fff;font:600 11px system-ui,sans-serif;padding:2px 8px;border-radius:999px;box-shadow:0 2px 8px rgba(0,0,0,.25);pointer-events:none';
    document.documentElement.appendChild(tag);
    const rect = el.getBoundingClientRect();
    tag.style.left = (rect.left + window.scrollX) + 'px';
    tag.style.top = Math.max(0, rect.top + window.scrollY - 10) + 'px';
    return tag;
  }

  function highlight(el, kind) {
    ring(el, kind === 'best' ? '#0a9c6c' : '#94a3b8', kind === 'best');
    assist.marks.push(el);
  }

  function clearAssistMarks() {
    if (!assist) return;
    for (const el of assist.marks) { try { unring(el); } catch { /* ignore */ } }
    for (const tag of assist.badges) tag.remove();
    assist.marks = []; assist.badges = [];
  }

  function reportChoice(step, chosen, extra) {
    chrome.runtime.sendMessage({ type: 'ss-lister-choice', step, chosen, suggested: assist?.suggested?.[step] || '', url: location.href, ...extra }).catch(() => {});
  }

  function assistRun() {
    if (!assist) return { active: false };
    clearAssistMarks();
    const options = assist.options;
    const kind = detect().kind;
    const result = { active: true, kind, suggested: '', candidates: 0 };
    if (kind === 'listing-category') {
      const dialog = categoryDialog();
      const wanted = options.categoryPath || '';
      const paths = dialog ? pathTexts(dialog) : [];
      result.candidates = paths.length;
      if (paths.length && wanted) {
        const scored = paths.map(p => ({ ...p, score: M.categoryScore(p.text, wanted) })).sort((a, b) => b.score - a.score);
        if (scored[0].score >= 0.3) {
          highlight(scored[0].el, 'best');
          assist.badges.push(badge(scored[0].el, 'Sweet Shelves pick', 'best'));
          result.suggested = scored[0].text;
        }
      }
      assist.suggested.category = result.suggested;
      if (dialog && assist.clickTarget !== dialog) {
        assist.clickTarget = dialog;
        dialog.addEventListener('click', event => {
          const hit = event.target.closest ? pathTexts(dialog).find(p => p.el === event.target || p.el.contains(event.target)) : null;
          if (hit) reportChoice('category', hit.text);
        }, true);
      }
    } else if (kind === 'listing-match') {
      const cards = matchCards();
      result.candidates = cards.length;
      if (cards.length && options.title) {
        const ranked = M.rankCandidates(cards.map(c => c.title), options.title, options.brand);
        const best = ranked[0];
        if (best && best.score >= 0.35) {
          highlight(cards[best.index].el, 'best');
          assist.badges.push(badge(cards[best.index].el, `Sweet Shelves pick · ${Math.round(best.score * 100)}%`, 'best'));
          result.suggested = cards[best.index].title;
        }
        for (const entry of ranked.slice(1, 3)) if (entry.score >= 0.35) highlight(cards[entry.index].el, 'alt');
      }
      assist.suggested.match = result.suggested;
      if (assist.clickTarget !== document.body) {
        assist.clickTarget = document.body;
        document.body.addEventListener('click', event => {
          if (!assist) return;
          const card = matchCards().find(c => c.el === event.target || c.el.contains(event.target));
          if (card) reportChoice('match', card.title, { itemId: card.itemId });
          else if (event.target.closest && /continue without match/i.test(event.target.closest('button, a')?.textContent || '')) reportChoice('match', 'Continue without match');
        }, true);
      }
    } else if (kind === 'listing-confirm') {
      const labels = M.prelistCondition(options.condition);
      const radios = Array.from(document.querySelectorAll('input[type="radio"]')).filter(isVisible);
      result.candidates = radios.length;
      const named = radios.map(r => ({ r, label: labelTextFor(r) || clip(r.parentElement?.textContent) }));
      const pick = labels.map(l => named.find(n => M.normalize(n.label) === M.normalize(l) || M.normalize(n.label).startsWith(M.normalize(l)))).find(Boolean);
      if (pick) {
        result.suggested = pick.label;
        // Highlight the whole option row (radio + its text), not the tiny wrapper around the radio.
        let box = pick.r.closest('label') || pick.r.parentElement || pick.r;
        while (box.parentElement && box.parentElement !== document.body && !/\w/.test(box.textContent || '') && box.parentElement.querySelectorAll('input[type="radio"]').length === 1) box = box.parentElement;
        highlight(box, 'best');
        if (!pick.r.checked && options.preselectCondition !== false && !assist.conditionClicked) { assist.conditionClicked = true; pick.r.click(); }
      }
      assist.suggested.condition = result.suggested;
    }
    assist.last = result;
    if (JSON.stringify(result) !== assist.reported) {
      assist.reported = JSON.stringify(result);
      chrome.runtime.sendMessage({ type: 'ss-lister-assist', state: result }).catch(() => {});
    }
    return result;
  }

  function assistStart(options) {
    if (assist) { assist.options = options || {}; return assistRun(); }
    assist = { options: options || {}, marks: [], badges: [], suggested: {}, clickTarget: null, reported: '', conditionClicked: false, started: Date.now() };
    const rerun = () => { clearTimeout(assist?.timer); if (assist) assist.timer = setTimeout(() => { if (assist && Date.now() - assist.started < 10 * 60 * 1000) assistRun(); }, 400); };
    assist.observer = new MutationObserver(rerun);
    assist.observer.observe(document.body, { childList: true, subtree: true, attributes: true, attributeFilter: ['class', 'style', 'hidden'] });
    window.addEventListener('scroll', rerun, { passive: true });
    window.addEventListener('resize', rerun);
    return assistRun();
  }

  function assistStop() {
    if (!assist) return false;
    clearAssistMarks();
    assist.observer.disconnect();
    clearTimeout(assist.timer);
    assist = null;
    return true;
  }

  // What the page itself says about the live listing (after publishing), beyond the URL.
  function detect() {
    const page = M.detectPage(location.href);
    const text = document.body ? document.body.innerText.slice(0, 200000) : '';
    const fields = collect();
    const descriptors = fields.map(describe);
    const success = M.successInfo(page.store, text).success;
    if (page.store === 'ebay' && !page.listingId && (success || page.kind === 'listing-success')) {
      // Only a confirmed listing may take its number from the page text or a "view listing" link;
      // the prelist steps are full of other sellers' /itm/ links.
      const match = text.match(/(?:item(?: number| id| #)?)[:\s#]*?(\d{12,13})\b/i) || (document.querySelector('a[href*="/itm/"]') || {}).href?.match(/\/itm\/(?:[^/]+\/)?(\d{9,15})/);
      if (match) page.listingId = match[1];
    }
    // eBay's category chooser is a dialog on the prelist page, not a URL of its own.
    if (page.store === 'ebay' && page.kind !== 'listing-form' && categoryDialog()) page.kind = 'listing-category';
    if (page.store === 'amazon') {
      if (!page.asin) {
        const match = text.match(/\bASIN[:\s]*([A-Z0-9]{10})\b/) || text.match(/\b(B0[A-Z0-9]{8})\b/);
        if (match) { page.asin = match[1]; page.listingId = page.asin; }
      }
      if (!page.sku) {
        const skuField = fields.find(el => /sku/.test(M.normalize(el.getAttribute('name') + ' ' + el.getAttribute('aria-label') + ' ' + labelTextFor(el))));
        if (skuField && skuField.value) page.sku = clip(skuField.value);
        else {
          const match = text.match(/\b(?:Seller SKU|SKU)[:\s]+([A-Za-z0-9._\-]{3,60})\b/);
          if (match) page.sku = match[1];
        }
      }
    }
    if (page.store === 'ebay' && !page.sku) {
      const skuField = fields.find(el => /custom label|sku/.test(M.normalize(el.getAttribute('name') + ' ' + el.getAttribute('aria-label') + ' ' + labelTextFor(el))));
      if (skuField && skuField.value) page.sku = clip(skuField.value);
    }
    const upcField = fields.find(el => /\b(upc|ean|gtin|product id)\b/.test(M.normalize(el.getAttribute('name') + ' ' + el.getAttribute('aria-label') + ' ' + labelTextFor(el))));
    page.upc = upcField && upcField.value ? clip(upcField.value) : '';

    // The URL alone cannot tell a search page from the form on some flows; the fields can.
    const searchBox = findSearchBox(page.store);
    // The store's own search box can read like a title field (Seller Central's "List Your Products" box says
    // "Enter product title, description, or keywords"): on its own it must never make a page look like the form.
    const boxIndex = searchBox ? fields.indexOf(searchBox) : -1;
    const assigned = M.assign(descriptors.map((d, i) => (i === boxIndex ? { tag: 'input', type: 'hidden' } : d)), ['title', 'price', 'sku', 'quantity']);
    const formish = Boolean(assigned.title || (assigned.price && (assigned.sku || assigned.quantity)));
    if (page.store && page.kind === 'listing-start' && formish) page.kind = page.store === 'amazon' ? 'offer-form' : 'listing-form';
    else if (page.store && (page.kind === 'listing-form' || page.kind === 'offer-form') && !formish && searchBox) page.kind = 'listing-start';
    page.hasSearchBox = Boolean(searchBox);

    // Success pages: eBay confirms with the item number, Seller Central with a saved-offer message.
    // Never on the prelist steps (search, category, match, confirm), whatever the text says.
    const prelistStep = /^\/sl\/prelist/.test(location.pathname) || ['listing-start', 'listing-category', 'listing-match', 'listing-confirm'].includes(page.kind);
    if (page.store === 'ebay' && !prelistStep && (page.kind === 'listing-success' || (success && page.listingId && !formish))) page.kind = 'listing-success';
    if (page.store === 'amazon' && success && !formish) page.kind = 'offer-success';
    page.successText = success;
    page.title = clip(document.title);
    page.url = location.href;
    page.fieldCount = fields.length;
    page.guide = guide ? { active: true, index: guide.index, count: guide.rows.filter(r => !r.done).length, total: guide.rows.length } : { active: false };
    return page;
  }

  // Pick mode: highlight fields under the pointer; a click sends the field to the panel.
  function startPick() {
    stopPick();
    const overlay = document.createElement('div');
    overlay.style.cssText = 'position:fixed;pointer-events:none;z-index:2147483647;border:2px solid #0a9c6c;background:rgba(10,156,108,.12);border-radius:4px;transition:all .05s;display:none';
    document.documentElement.appendChild(overlay);
    const banner = document.createElement('div');
    banner.textContent = 'Sweet Shelves Lister: click the field to fill. Esc cancels.';
    banner.style.cssText = 'position:fixed;top:8px;left:50%;transform:translateX(-50%);z-index:2147483647;background:#0a9c6c;color:#fff;font:600 13px system-ui,sans-serif;padding:8px 14px;border-radius:999px;box-shadow:0 4px 16px rgba(0,0,0,.25)';
    document.documentElement.appendChild(banner);
    const handlers = {
      move(event) {
        const el = fieldAt(event);
        if (!el) { overlay.style.display = 'none'; return; }
        const rect = el.getBoundingClientRect();
        overlay.style.display = 'block';
        overlay.style.left = rect.left - 2 + 'px';
        overlay.style.top = rect.top - 2 + 'px';
        overlay.style.width = rect.width + 'px';
        overlay.style.height = rect.height + 'px';
      },
      click(event) {
        const el = fieldAt(event);
        if (!el) return;
        event.preventDefault();
        event.stopPropagation();
        const pickId = 'pick-' + Date.now().toString(36) + Math.random().toString(36).slice(2, 7);
        picked.set(pickId, el);
        const descriptor = describe(el);
        stopPick();
        chrome.runtime.sendMessage({ type: 'ss-lister-picked', pickId, field: descriptor, suggestions: M.suggestTargets(descriptor), url: location.href }).catch(() => {});
      },
      key(event) {
        if (event.key === 'Escape') { stopPick(); chrome.runtime.sendMessage({ type: 'ss-lister-pick-cancelled' }).catch(() => {}); }
      },
    };
    document.addEventListener('mousemove', handlers.move, true);
    document.addEventListener('click', handlers.click, true);
    document.addEventListener('keydown', handlers.key, true);
    pickState = { overlay, banner, handlers };
    return true;
  }

  function fieldAt(event) {
    const el = event.target && event.target.closest ? event.target.closest(FIELD_SELECTOR) : null;
    return el && !el.disabled ? el : null;
  }

  function stopPick() {
    if (!pickState) return false;
    document.removeEventListener('mousemove', pickState.handlers.move, true);
    document.removeEventListener('click', pickState.handlers.click, true);
    document.removeEventListener('keydown', pickState.handlers.key, true);
    pickState.overlay.remove();
    pickState.banner.remove();
    pickState = null;
    return true;
  }

  function setPicked(pickId, value, options) {
    const el = picked.get(pickId);
    if (!el || !el.isConnected) return { ok: false, reason: 'that field is no longer on the page' };
    const ok = setField(el, value, options || {});
    if (ok) flash(el);
    return { ok, field: describe(el) };
  }

  const api = { describe, collect: () => collect().map(describe), fill, detect, startPick, stopPick, setPicked, search, addPhotos,
    guideStart, guideNext, guideGo, guideUse, guideStop, guideState, assistStart, assistStop, version: 3 };
  globalThis.__ssLister = api;

  chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
    if (!message || message.target !== 'ss-lister-page') return false;
    try {
      switch (message.type) {
        case 'detect': sendResponse({ ok: true, page: detect() }); break;
        case 'collect': sendResponse({ ok: true, fields: api.collect() }); break;
        case 'fill': sendResponse({ ok: true, report: fill(message.options || {}) }); break;
        case 'search': search(message.options || {}, sendResponse); return true;
        case 'add-photos': sendResponse(addPhotos(message.options || {})); break;
        case 'guide-start': sendResponse({ ok: true, state: guideStart(message.options || {}) }); break;
        case 'guide-next': sendResponse({ ok: true, state: guideNext() }); break;
        case 'guide-go': sendResponse({ ok: true, state: guideGo(Number(message.index) || 0) }); break;
        case 'guide-use': sendResponse({ ok: true, state: guideUse() }); break;
        case 'guide-stop': sendResponse({ ok: guideStop(), state: guideState() }); break;
        case 'guide-state': sendResponse({ ok: true, state: guideState() }); break;
        case 'assist-start': sendResponse({ ok: true, state: assistStart(message.options || {}) }); break;
        case 'assist-stop': sendResponse({ ok: assistStop() }); break;
        case 'pick-start': sendResponse({ ok: startPick() }); break;
        case 'pick-stop': sendResponse({ ok: stopPick() }); break;
        case 'set-picked': sendResponse(setPicked(message.pickId, message.value, message.options)); break;
        case 'ping': sendResponse({ ok: true, version: api.version }); break;
        default: sendResponse({ ok: false, reason: 'unknown request' });
      }
    } catch (error) {
      sendResponse({ ok: false, reason: String(error && error.message || error) });
    }
    return false;
  });
})();
