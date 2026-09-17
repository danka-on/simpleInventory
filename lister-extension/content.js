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
    return {
      tag,
      type: tag === 'input' ? (el.getAttribute('type') || 'text').toLowerCase() : '',
      name: el.getAttribute('name') || '',
      id: el.id || '',
      ariaLabel: clip(el.getAttribute('aria-label') || ''),
      placeholder: clip(el.getAttribute('placeholder') || ''),
      role: el.getAttribute('role') || '',
      labelText: labelTextFor(el),
      nearbyText: nearbyText(el),
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

  function collect() {
    const elements = [];
    for (const doc of documents()) {
      for (const el of doc.querySelectorAll(FIELD_SELECTOR)) {
        if (el.disabled || el.readOnly) continue;
        const type = (el.getAttribute('type') || '').toLowerCase();
        if (['hidden', 'checkbox', 'radio', 'file', 'submit', 'button', 'reset', 'image'].includes(type)) continue;
        if (!isVisible(el)) continue;
        elements.push(el);
      }
    }
    return elements;
  }

  function setNativeValue(el, value) {
    const proto = el instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
    const descriptor = Object.getOwnPropertyDescriptor(proto, 'value');
    el.focus();
    if (descriptor && descriptor.set) descriptor.set.call(el, value);
    else el.value = value;
    el.dispatchEvent(new Event('input', { bubbles: true }));
    el.dispatchEvent(new Event('change', { bubbles: true }));
    el.dispatchEvent(new KeyboardEvent('keyup', { bubbles: true }));
  }

  function setContentEditable(el, html, text) {
    el.focus();
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
    el.focus();
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

  function flash(el) {
    try {
      const previous = el.style.boxShadow;
      el.style.boxShadow = '0 0 0 3px rgba(10, 156, 108, 0.7)';
      setTimeout(() => { el.style.boxShadow = previous; }, 1800);
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

  function submitSearch(el) {
    const form = el.form || el.closest('form');
    const doc = ownDocument(el);
    // A visible search/continue button next to the box is the most reliable trigger.
    const scope = form || el.closest('[class*="search" i], [class*="prelist" i], section, main') || doc.body;
    // The submit button comes AFTER the box in the page: Seller Central also has a "Search" tab tile before it.
    const candidates = Array.from(scope.querySelectorAll('button, input[type=submit], [role=button]')).filter(b => {
      if (!isVisible(b)) return false;
      const text = M.normalize((b.textContent || '') + ' ' + (b.getAttribute('aria-label') || '') + ' ' + (b.value || ''));
      return /^(search|get started|continue|find|go|next|submit|search now)$/.test(text) || /search|get started/.test(text);
    });
    const button = candidates.find(b => el.compareDocumentPosition(b) & Node.DOCUMENT_POSITION_FOLLOWING) || candidates[0];
    for (const type of ['keydown', 'keypress', 'keyup']) {
      el.dispatchEvent(new KeyboardEvent(type, { key: 'Enter', code: 'Enter', keyCode: 13, which: 13, bubbles: true, cancelable: true }));
    }
    if (button) { button.click(); return 'button'; }
    if (form) { form.requestSubmit ? form.requestSubmit() : form.submit(); return 'form'; }
    return 'enter';
  }

  function searchNow(query, store) {
    const box = findSearchBox(store);
    if (!box) return null;
    setNativeValue(box, String(query || ''));
    flash(box);
    const how = submitSearch(box);
    return { ok: true, label: describe(box).placeholder || describe(box).labelText || box.id, submitted: how };
  }

  // Store pages render the search box after load; wait up to ~4 s for it.
  function search({ query = '', store = '' } = {}, done) {
    const started = Date.now();
    const attempt = () => {
      const result = searchNow(query, store);
      if (result) return done(result);
      if (Date.now() - started > 4000) return done({ ok: false, reason: 'no product search box on this page' });
      setTimeout(attempt, 300);
    };
    attempt();
  }

  // -- photos: hand our files to the page's uploader ------------------------------------------------

  function fileInputs() {
    const out = [];
    for (const doc of documents()) {
      for (const el of doc.querySelectorAll('input[type=file]')) {
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
      const input = uploaderNear(event.target);
      if (input) {
        input.files = transfer.files;
        input.dispatchEvent(new Event('input', { bubbles: true }));
        input.dispatchEvent(new Event('change', { bubbles: true }));
      } else {
        const zone = (event.target.closest && event.target.closest('[class*="drop" i], [class*="upload" i], [class*="photo" i]')) || dropZone();
        if (!zone) throw new Error('no photo uploader near the drop');
        for (const type of ['dragenter', 'dragover', 'drop']) zone.dispatchEvent(new DragEvent(type, { bubbles: true, cancelable: true, dataTransfer: transfer }));
      }
      chrome.runtime.sendMessage({ type: 'ss-lister-dropped', ok: true, name: files[0].name }).catch(() => {});
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

  function guideNeeded({ values = {}, store = '', aspects = {}, noteFields = [] } = {}) {
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
        label: (target && TARGET_LABELS[target]) || d.labelText || d.ariaLabel || d.placeholder || d.name || d.id || d.tag,
        suggestion: String(suggestion || ''), tag: d.tag, kind: 'field',
        value: target === 'quantity' || target === 'price' ? String(d.value || '') : '',
        fromNotes: noteSet.has(target) });
    });
    const photos = photoState();
    if (photos) rows.unshift({ index: -1, target: 'photos', required: true, done: photos.count > 0, label: 'Photos', suggestion: 'Send to page or drag from the panel', tag: 'photos', kind: 'photos', el: photos.el });
    // Required first, then our fields, keeping page order inside each group.
    rows.sort((a, b) => Number(b.required) - Number(a.required));
    return { elements, rows };
  }

  function guideElement(row) {
    return row.kind === 'photos' ? row.el : guide.elements[row.index];
  }

  function guideStyle(row, state) {
    const el = guideElement(row);
    if (!el) return;
    try {
      if (el.dataset.ssGuidePrev === undefined) el.dataset.ssGuidePrev = el.style.outline || '';
      const colour = state === 'notes' ? '#2563eb' : (state === 'done' ? '#16a34a' : (state === 'required' ? '#dc2626' : '#f59e0b'));
      el.style.outline = (state === 'done' ? '2px solid ' : '3px solid ') + colour;
      el.style.outlineOffset = '2px';
      // A filled field fades back; one filled from the prep notes keeps its blue outline as a reminder to read it.
      if (state === 'done') setTimeout(() => { if (el.dataset.ssGuideDone === '1') { el.style.outline = el.dataset.ssGuidePrev || ''; el.style.outlineOffset = ''; } }, 2500);
      el.dataset.ssGuideDone = state === 'done' ? '1' : '';
    } catch { /* ignore */ }
  }

  function guideUnstyle(row) {
    const el = guideElement(row);
    if (!el) return;
    try { el.style.outline = el.dataset.ssGuidePrev || ''; el.style.outlineOffset = ''; delete el.dataset.ssGuidePrev; delete el.dataset.ssGuideDone; } catch { /* ignore */ }
  }

  // Buttons on the overlay that need the server go through the side panel.
  function panelAction(action, extra) {
    chrome.runtime.sendMessage({ type: 'ss-lister-action', action, ...(extra || {}) }).catch(() => {});
  }

  function guidePanel() {
    if (guide.panel) return guide.panel;
    const panel = document.createElement('div');
    panel.id = 'ss-lister-guide';
    panel.style.cssText = 'position:fixed;left:16px;bottom:16px;z-index:2147483647;width:min(360px,calc(100vw - 32px));max-height:min(70vh,560px);display:flex;flex-direction:column;background:#0f172a;color:#f8fafc;font:13px/1.4 system-ui,sans-serif;border-radius:14px;box-shadow:0 12px 40px rgba(0,0,0,.45);overflow:hidden';
    // Remembered position for this site (the user can drag it by the header).
    try {
      const saved = JSON.parse(sessionStorage.getItem('ss-lister-guide-pos') || 'null');
      if (saved && Number.isFinite(saved.left) && Number.isFinite(saved.top)) { panel.style.left = Math.max(0, Math.min(saved.left, window.innerWidth - 80)) + 'px'; panel.style.top = Math.max(0, Math.min(saved.top, window.innerHeight - 60)) + 'px'; panel.style.bottom = 'auto'; }
    } catch { /* ignore */ }
    panel.innerHTML = `
      <div data-ss="head" style="display:flex;align-items:center;gap:8px;padding:10px 12px;background:#1e293b;cursor:pointer">
        <span style="width:10px;height:10px;border-radius:50%;background:#0a9c6c;flex:none"></span>
        <b data-ss="title" style="flex:1">Sweet Shelves Lister</b>
        <button data-ss="collapse" title="Collapse" style="background:transparent;border:0;color:#cbd5e1;font-size:16px;cursor:pointer;padding:0 4px">–</button>
        <button data-ss="done" title="Close the overlay (Esc)" style="background:transparent;border:0;color:#cbd5e1;font-size:16px;cursor:pointer;padding:0 4px">×</button>
      </div>
      <div data-ss="bar" style="height:5px;background:#334155"><div data-ss="fill" style="height:100%;width:0;background:linear-gradient(90deg,#f59e0b,#16a34a);transition:width .3s"></div></div>
      <div data-ss="body" style="overflow:auto;padding:6px 0"></div>
      <div data-ss="foot" style="display:flex;gap:6px;flex-wrap:wrap;padding:8px 12px;border-top:1px solid #334155;background:#111827">
        <button data-ss="use" style="background:#0a9c6c;color:#fff;border:0;border-radius:8px;padding:6px 12px;cursor:pointer;font:inherit;font-weight:600">Use suggestion (Ctrl+Enter)</button>
        <button data-ss="next" style="background:#334155;color:#fff;border:0;border-radius:8px;padding:6px 12px;cursor:pointer;font:inherit">Next (Tab)</button>
        <button data-ss="fill" title="Fill the form from the item's values" style="background:#1d4ed8;color:#fff;border:0;border-radius:8px;padding:6px 12px;cursor:pointer;font:inherit">Fill page</button>

      </div>`;
    panel.querySelector('[data-ss="use"]').onclick = () => guideUse();
    panel.querySelector('[data-ss="next"]').onclick = () => guideNext();
    panel.querySelector('[data-ss="fill"]').onclick = () => panelAction('fill');

    panel.querySelector('[data-ss="done"]').onclick = () => { guideStop(); notifyGuide(); };
    panel.querySelector('[data-ss="collapse"]').onclick = event => { event.stopPropagation(); guide.collapsed = !guide.collapsed; guideRender(); };
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
        if (moved) { panel.dataset.ssDragged = '1'; try { sessionStorage.setItem('ss-lister-guide-pos', JSON.stringify({ left: parseFloat(panel.style.left), top: parseFloat(panel.style.top) })); } catch { /* ignore */ } }
      };
      document.addEventListener('pointermove', move); document.addEventListener('pointerup', up);
      event.preventDefault();
    });
    document.documentElement.appendChild(panel);
    guide.panel = panel;
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

  // scroll=true only for an explicit jump (click, Next, Tab); refreshes never move the page.
  function guideRender(scroll = false) {
    if (!guide) return;
    const open = guide.rows.filter(r => !r.done);
    const requiredOpen = open.filter(r => r.required);
    const current = guide.rows[guide.index];
    const panel = guidePanel();
    const total = guide.rows.length;
    const done = total - open.length;
    panel.querySelector('[data-ss="title"]').textContent = open.length
      ? `${open.length} to fill · ${requiredOpen.length} required`
      : 'Everything on the checklist has a value';
    panel.querySelector('[data-ss="fill"]').style.width = (total ? Math.round(done / total * 100) : 100) + '%';
    panel.querySelector('[data-ss="body"]').style.display = guide.collapsed ? 'none' : '';
    panel.querySelector('[data-ss="foot"]').style.display = guide.collapsed ? 'none' : '';
    panel.querySelector('[data-ss="collapse"]').textContent = guide.collapsed ? '+' : '–';
    const body = panel.querySelector('[data-ss="body"]');
    body.innerHTML = guide.rows.map((row, i) => {
      const fromNotes = row.fromNotes && row.done;
      const colour = fromNotes ? '#2563eb' : (row.done ? '#16a34a' : (row.required ? '#dc2626' : '#f59e0b'));
      const isCurrent = current === row;
      return `<div data-ss-row="${i}" style="display:flex;align-items:center;gap:8px;padding:6px 12px;cursor:pointer;${isCurrent ? 'background:#1e293b;' : ''}">
        <span style="width:10px;height:10px;border-radius:50%;background:${colour};flex:none"></span>
        <span style="flex:1;min-width:0"><span style="font-weight:${isCurrent ? 700 : 500}">${escapeHtml(row.label)}</span>${row.value ? ` <span style="color:#e2e8f0;font-weight:700">· ${escapeHtml(row.value)}</span>` : ''}${row.required && !row.done ? ' <span style="color:#fca5a5;font-size:11px">required</span>' : ''}
          ${!row.done && row.suggestion ? `<div style="color:#cbd5e1;font-size:12px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis">→ ${escapeHtml(row.suggestion.slice(0, 90))}</div>` : ''}
          ${fromNotes ? '<div style="color:#93c5fd;font-size:11px">Filled from the warehouse prep notes. Read it once before listing.</div>' : ''}
          ${row.done && guide.options.aiFields?.[row.target] ? `<div style="color:#86efac;font-size:11px">generated with AI${guide.options.aiFields[row.target] === 'auto' ? ' (automatically)' : ''}</div>` : ''}</span>
        ${row.target === 'title' || row.target === 'description' ? `<button data-ss-ai="${row.target}" title="Write the ${row.target} with AI from the item and its notes" style="background:#0a9c6c;color:#fff;border:0;border-radius:6px;padding:2px 8px;cursor:pointer;font:600 11px system-ui,sans-serif">AI</button>` : ''}
        <span style="color:${colour};font-weight:700">${row.done ? (fromNotes ? 'ⓘ' : '✓') : (row.required ? '!' : '·')}</span></div>`;
    }).join('') || '<div style="padding:10px 12px;color:#cbd5e1">No listing fields found on this page yet.</div>';
    for (const el of body.querySelectorAll('[data-ss-row]')) el.onclick = () => guideGo(Number(el.dataset.ssRow));
    for (const el of body.querySelectorAll('[data-ss-ai]')) el.onclick = event => { event.stopPropagation(); el.textContent = '…'; panelAction('generate', { kind: el.dataset.ssAi }); };
    panel.querySelector('[data-ss="use"]').style.display = current && !current.done && current.suggestion && current.kind === 'field' ? '' : 'none';
    for (const row of guide.rows) guideStyle(row, row.done ? (row.fromNotes ? 'notes' : 'done') : (row.required ? 'required' : 'optional'));
    const pointer = guidePointer();
    const el = current ? guideElement(current) : null;
    if (el) {
      // A green (filled) row still takes the user to that spot when clicked.
      if (scroll) { try { el.scrollIntoView({ block: 'center', behavior: 'smooth' }); if (current.kind === 'field') el.focus({ preventScroll: true }); } catch { /* ignore */ } }
      const rect = el.getBoundingClientRect();
      pointer.style.display = '';
      pointer.style.background = current.done ? '#16a34a' : (current.required ? '#dc2626' : '#f59e0b');
      pointer.textContent = current.done ? `${current.label} ✓` : `${current.label}${current.suggestion ? ' → ' + current.suggestion.slice(0, 60) : ''}`;
      pointer.style.left = Math.max(8, rect.left + window.scrollX) + 'px';
      pointer.style.top = Math.max(0, rect.top + window.scrollY - 30) + 'px';
    } else {
      pointer.style.display = 'none';
    }
  }

  function escapeHtml(value) {
    return String(value ?? '').replace(/[&<>"']/g, ch => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[ch]));
  }

  function notifyGuide() {
    chrome.runtime.sendMessage({ type: 'ss-lister-guide', state: guideState() }).catch(() => {});
  }

  function guideState() {
    if (!guide) return { active: false, needed: [], rows: [], index: -1 };
    const rows = guide.rows.map(r => ({ index: r.index, target: r.target, required: r.required, done: r.done, label: r.label, suggestion: r.suggestion, kind: r.kind, value: r.value || '', fromNotes: Boolean(r.fromNotes) }));
    return { active: true, index: guide.index, rows, needed: rows.filter(r => !r.done), open: rows.filter(r => !r.done).length, total: rows.length };
  }

  function guideStart(options) {
    // Already up on this page (the URL changed, the fill ran again): refresh in place, never jump back to the first row.
    if (guide) { guide.options = options || guide.options; guideRefresh(); guideRender(false); notifyGuide(); return guideState(); }
    const { elements, rows } = guideNeeded(options || {});
    guide = { elements, rows, index: -1, options: options || {}, panel: null, pointer: null, collapsed: false };
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
      change() { clearTimeout(guide?.timer); if (guide) guide.timer = setTimeout(() => { if (guide) { guideRefresh(); guideRender(); notifyGuide(); } }, 250); },
    };
    document.addEventListener('keydown', handlers.key, true);
    document.addEventListener('input', handlers.change, true);
    document.addEventListener('change', handlers.change, true);
    guide.handlers = handlers;
    guide.ticker = setInterval(() => { if (guide) { guideRefresh(); guideRender(); } }, 2000);
    if (rows.some(r => !r.done)) guideNext(); else { guideRender(); notifyGuide(); }
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
    const open = guide.rows.map((r, i) => (r.done ? -1 : i)).filter(i => i >= 0);
    if (!open.length) { guide.index = -1; guideRender(); notifyGuide(); return guideState(); }
    const after = open.find(i => i > guide.index);
    guide.index = after !== undefined ? after : open[0];
    guideRender(true);
    notifyGuide();
    return guideState();
  }

  function guideGo(index) {
    if (!guide) return guideState();
    guideRefresh();
    if (!guide.rows.length) { guideRender(); return guideState(); }
    guide.index = ((index % guide.rows.length) + guide.rows.length) % guide.rows.length;
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
    }
    clearInterval(guide.ticker); clearTimeout(guide.timer);
    if (guide.panel) guide.panel.remove();
    if (guide.pointer) guide.pointer.remove();
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
    el.dataset.ssAssistPrev = el.style.outline || '';
    el.style.outline = kind === 'best' ? '3px solid #0a9c6c' : '2px dashed #94a3b8';
    el.style.outlineOffset = '2px';
    assist.marks.push(el);
  }

  function clearAssistMarks() {
    if (!assist) return;
    for (const el of assist.marks) { try { el.style.outline = el.dataset.ssAssistPrev || ''; el.style.outlineOffset = ''; delete el.dataset.ssAssistPrev; } catch { /* ignore */ } }
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
        const box = pick.r.closest('label') || pick.r.parentElement || pick.r;
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
    const assigned = M.assign(descriptors, ['title', 'price', 'sku', 'quantity']);
    const formish = Boolean(assigned.title || (assigned.price && (assigned.sku || assigned.quantity)));
    const searchBox = findSearchBox(page.store);
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
