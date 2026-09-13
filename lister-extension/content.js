// Sweet Shelves Lister page script. Injected on demand into eBay listing and Seller Central
// pages (after matcher.js). It never talks to the Sweet Shelves server; the side panel does.
(() => {
  if (globalThis.__ssLister) return;
  const M = globalThis.SSListerMatcher;
  const MAX_TEXT = 90;
  const picked = new Map();
  let pickState = null;

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

  // What the page itself says about the live listing (after publishing), beyond the URL.
  function detect() {
    const page = M.detectPage(location.href);
    const text = document.body ? document.body.innerText.slice(0, 200000) : '';
    if (page.store === 'ebay' && !page.listingId) {
      const match = text.match(/(?:item(?: number| id| #)?|listing)[:\s#]*?(\d{12,13})\b/i) || (document.querySelector('a[href*="/itm/"]') || {}).href?.match(/\/itm\/(?:[^/]+\/)?(\d{9,15})/);
      if (match) page.listingId = match[1];
    }
    if (page.store === 'amazon') {
      if (!page.asin) {
        const match = text.match(/\bASIN[:\s]*([A-Z0-9]{10})\b/) || text.match(/\b(B0[A-Z0-9]{8})\b/);
        if (match) { page.asin = match[1]; page.listingId = page.asin; }
      }
      if (!page.sku) {
        const skuField = collect().find(el => /sku/.test(M.normalize(el.getAttribute('name') + ' ' + el.getAttribute('aria-label') + ' ' + labelTextFor(el))));
        if (skuField && skuField.value) page.sku = clip(skuField.value);
        else {
          const match = text.match(/\b(?:Seller SKU|SKU)[:\s]+([A-Za-z0-9._\-]{3,60})\b/);
          if (match) page.sku = match[1];
        }
      }
    }
    if (page.store === 'ebay' && !page.sku) {
      const skuField = collect().find(el => /custom label|sku/.test(M.normalize(el.getAttribute('name') + ' ' + el.getAttribute('aria-label') + ' ' + labelTextFor(el))));
      if (skuField && skuField.value) page.sku = clip(skuField.value);
    }
    const upcField = collect().find(el => /\b(upc|ean|gtin|product id)\b/.test(M.normalize(el.getAttribute('name') + ' ' + el.getAttribute('aria-label') + ' ' + labelTextFor(el))));
    page.upc = upcField && upcField.value ? clip(upcField.value) : '';
    page.title = clip(document.title);
    page.url = location.href;
    page.fieldCount = collect().length;
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

  const api = { describe, collect: () => collect().map(describe), fill, detect, startPick, stopPick, setPicked, version: 1 };
  globalThis.__ssLister = api;

  chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
    if (!message || message.target !== 'ss-lister-page') return false;
    try {
      switch (message.type) {
        case 'detect': sendResponse({ ok: true, page: detect() }); break;
        case 'collect': sendResponse({ ok: true, fields: api.collect() }); break;
        case 'fill': sendResponse({ ok: true, report: fill(message.options || {}) }); break;
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
