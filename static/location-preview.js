(function () {
  if (window.SSLocationPreview) return;

  const STYLE_ID = 'ss-location-preview-style';
  const ROOT_ID = 'ss-location-preview-root';
  const state = {
    token: 0,
    items: [],
    expandedIndex: null,
    title: 'Location Preview'
  };

  let root = null;
  let titleEl = null;
  let noteEl = null;
  let bodyEl = null;
  let closeEl = null;

  function escHtml(value) {
    return String(value == null ? '' : value)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;');
  }

  function trimValue(value) {
    return String(value == null ? '' : value).trim();
  }

  function compactCode(value) {
    return trimValue(value).replace(/\s+/g, '');
  }

  function looksLikePicturePath(value) {
    const raw = trimValue(value);
    if (!raw) return false;
    return /^https?:\/\//i.test(raw) ||
      raw.startsWith('/') ||
      raw.includes('/') ||
      /\.[a-z0-9]{2,5}$/i.test(raw);
  }

  function deriveMapKey(code) {
    const compact = compactCode(code).toLowerCase();
    const baseCode = compact.replace(/b\d+$/i, '');
    if (!baseCode) return '';
    if (/^ofloor\d+$/i.test(baseCode)) return baseCode;
    const shelfMatch = baseCode.match(/^(.*)s\d+$/i);
    if (shelfMatch) return shelfMatch[1];
    return baseCode;
  }

  function ensureStyles() {
    if (document.getElementById(STYLE_ID)) return;
    const style = document.createElement('style');
    style.id = STYLE_ID;
    style.textContent = [
      '.sslp-root{position:fixed;inset:0;display:none;align-items:center;justify-content:center;padding:18px;background:rgba(2,6,23,0.72);backdrop-filter:blur(2px);z-index:2147483646;}',
      '.sslp-root.is-open{display:flex;}',
      '.sslp-dialog{width:min(1040px,96vw);max-height:min(90vh,920px);background:#fff;border:1px solid rgba(15,23,42,0.12);border-radius:18px;box-shadow:0 28px 90px rgba(15,23,42,0.35);overflow:hidden;display:flex;flex-direction:column;}',
      'html.ss-theme-night .sslp-dialog{background:#0f172a;border-color:rgba(148,163,184,0.24);box-shadow:0 28px 90px rgba(0,0,0,0.52);}',
      '.sslp-head{display:flex;align-items:flex-start;justify-content:space-between;gap:12px;padding:16px 18px 12px;border-bottom:1px solid rgba(15,23,42,0.08);}',
      'html.ss-theme-night .sslp-head{border-bottom-color:rgba(148,163,184,0.18);}',
      '.sslp-title{font-size:1rem;font-weight:700;color:#0f172a;}',
      'html.ss-theme-night .sslp-title{color:#f8fafc;}',
      '.sslp-note{margin-top:4px;font-size:0.84rem;color:#64748b;}',
      'html.ss-theme-night .sslp-note{color:#94a3b8;}',
      '.sslp-close{border:1px solid rgba(220,38,38,0.18);background:#dc2626;color:#fff;border-radius:10px;width:38px;height:38px;font-size:1.2rem;line-height:1;cursor:pointer;flex:0 0 auto;box-shadow:0 8px 18px rgba(220,38,38,0.22);}',
      '.sslp-close:hover{background:#b91c1c;color:#fff;}',
      'html.ss-theme-night .sslp-close{background:#dc2626;border-color:rgba(248,113,113,0.3);color:#fff;box-shadow:0 8px 18px rgba(220,38,38,0.28);}',
      'html.ss-theme-night .sslp-close:hover{background:#b91c1c;color:#fff;}',
      '.sslp-body{padding:18px;overflow:auto;}',
      '.sslp-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:14px;}',
      '.sslp-card{display:flex;flex-direction:column;gap:10px;width:100%;appearance:none;border:1px solid rgba(15,23,42,0.1);border-radius:14px;background:#f8fafc;padding:12px;cursor:zoom-in;text-align:left;font:inherit;color:inherit;}',
      '.sslp-card:hover{border-color:#3b82f6;box-shadow:0 10px 26px rgba(59,130,246,0.12);}',
      'html.ss-theme-night .sslp-card{background:#111827;border-color:rgba(148,163,184,0.18);}',
      'html.ss-theme-night .sslp-card:hover{border-color:#60a5fa;box-shadow:0 10px 30px rgba(37,99,235,0.24);}',
      '.sslp-label{font-size:0.82rem;font-weight:700;letter-spacing:0.04em;text-transform:uppercase;color:#64748b;}',
      'html.ss-theme-night .sslp-label{color:#94a3b8;}',
      '.sslp-image-wrap{display:flex;align-items:center;justify-content:center;min-height:220px;max-height:360px;border-radius:12px;background:#fff;border:1px solid rgba(15,23,42,0.08);overflow:hidden;}',
      'html.ss-theme-night .sslp-image-wrap{background:#0b1220;border-color:rgba(148,163,184,0.18);}',
      '.sslp-image{display:block;max-width:100%;max-height:100%;object-fit:contain;}',
      '.sslp-expanded{display:flex;flex-direction:column;gap:12px;}',
      '.sslp-expanded .sslp-image-wrap{min-height:320px;max-height:none;height:min(72vh,860px);cursor:pointer;}',
      '.sslp-empty{padding:24px 8px;text-align:center;font-size:0.92rem;color:#64748b;}',
      'html.ss-theme-night .sslp-empty{color:#94a3b8;}',
      '.sslp-back-btn{display:inline-flex;align-items:center;gap:7px;margin-top:12px;padding:9px 16px;border:none;border-radius:10px;background:#334155;color:#e2e8f0;font-size:0.9rem;font-weight:700;cursor:pointer;transition:background .15s,color .15s;}',
      '.sslp-back-btn:hover{background:#475569;color:#fff;}',
      'html.ss-theme-night .sslp-back-btn{background:#1e293b;border:1px solid rgba(148,163,184,.2);color:#cbd5e1;}',
      'html.ss-theme-night .sslp-back-btn:hover{background:#334155;color:#f8fafc;}',
      '.sslp-close-row{display:none;padding-top:4px;}',
      '@media (max-width: 640px){' +
        '.sslp-root{align-items:flex-start;overflow:auto;padding:calc(env(safe-area-inset-top, 0px) + 72px) 8px calc(env(safe-area-inset-bottom, 0px) + 8px);}' +
        '.sslp-dialog{width:min(100%,560px);max-height:none;min-height:0;}' +
        '.sslp-head{position:sticky;top:0;z-index:3;background:#fff;}' +
        'html.ss-theme-night .sslp-head{background:#0f172a;}' +
        '.sslp-head{padding:12px 12px 10px;}' +
        '.sslp-body{padding:12px;}' +
        '.sslp-grid{grid-template-columns:1fr;}' +
        '.sslp-expanded .sslp-image-wrap{height:min(52vh,520px);}' +
        '.sslp-back-btn{width:100%;justify-content:center;padding:14px 16px;font-size:1rem;margin-top:0;}' +
        '.sslp-expanded .sslp-back-btn{position:sticky;bottom:0;z-index:2;background:#1d4ed8;color:#fff;border-radius:0 0 12px 12px;box-shadow:0 -3px 14px rgba(0,0,0,.35);border-top:1px solid rgba(255,255,255,.12);}' +
        '.sslp-expanded .sslp-back-btn:hover{background:#1e40af;}' +
        '.sslp-close-row{display:flex;justify-content:center;padding-top:8px;}' +
        '.sslp-close-row button{display:inline-flex;align-items:center;justify-content:center;gap:7px;padding:13px 28px;border:none;border-radius:10px;background:#1d4ed8;color:#fff;font-size:1rem;font-weight:700;cursor:pointer;width:100%;}' +
      '}'
    ].join('');
    document.head.appendChild(style);
  }

  function ensureDom() {
    ensureStyles();
    if (root) return;

    root = document.createElement('div');
    root.id = ROOT_ID;
    root.className = 'sslp-root';
    root.setAttribute('aria-hidden', 'true');
    root.innerHTML = [
      '<div class="sslp-dialog" role="dialog" aria-modal="true" aria-labelledby="sslp-title">',
      '  <div class="sslp-head">',
      '    <div>',
      '      <div id="sslp-title" class="sslp-title">Location Preview</div>',
      '      <div class="sslp-note"></div>',
      '    </div>',
      '    <button type="button" class="sslp-close" aria-label="Close preview">&times;</button>',
      '  </div>',
      '  <div class="sslp-body"></div>',
      '</div>'
    ].join('');

    document.body.appendChild(root);
    titleEl = root.querySelector('.sslp-title');
    noteEl = root.querySelector('.sslp-note');
    bodyEl = root.querySelector('.sslp-body');
    closeEl = root.querySelector('.sslp-close');

    closeEl.addEventListener('click', close);
    root.addEventListener('click', function (event) {
      if (event.target === root) {
        if (state.expandedIndex !== null) {
          state.expandedIndex = null;
          render();
          return;
        }
        close();
      }
    });

    bodyEl.addEventListener('click', function (event) {
      if (event.target.closest('[data-sslp-close]')) {
        close();
        return;
      }
      if (event.target.closest('[data-sslp-back]')) {
        if (state.expandedIndex !== null && state.items.length > 1) {
          state.expandedIndex = null;
          render();
        } else {
          close();
        }
        return;
      }
      const card = event.target.closest('[data-sslp-index]');
      if (!card) return;
      if (state.expandedIndex !== null) return;
      if (state.items.length <= 1) return;
      const index = parseInt(card.getAttribute('data-sslp-index'), 10);
      if (!Number.isFinite(index) || !state.items[index]) return;
      state.expandedIndex = index;
      render();
    });

    document.addEventListener('keydown', function (event) {
      if (!root || !root.classList.contains('is-open')) return;
      if (event.key !== 'Escape') return;
      event.preventDefault();
      if (state.expandedIndex !== null) {
        state.expandedIndex = null;
        render();
        return;
      }
      close();
    });
  }

  function addCandidate(list, src) {
    const value = trimValue(src);
    if (!value || list.includes(value)) return;
    list.push(value);
  }

  function withBust(list) {
    const stamp = Date.now();
    return list.map(function (src) {
      return src + (src.includes('?') ? '&' : '?') + 't=' + stamp;
    });
  }

  function shelfCandidates(code) {
    const raw = trimValue(code);
    const compact = compactCode(raw);
    const lower = compact.toLowerCase();
    const upper = compact.toUpperCase();
    const rawUpper = raw.toUpperCase();
    // Bin-stripped variants (e.g. gr1s1b2 → gr1s1) for shelf photos named without bin suffix
    const nobin = lower.replace(/b\d+$/i, '');
    const nobinUpper = upper.replace(/b\d+$/i, '');
    const nobinRaw = raw.replace(/b\d+$/i, '');
    const hasBin = nobin !== lower;
    const candidates = [];
    const mapKey = deriveMapKey(raw);
    const normalizedMapKey = mapKey || lower;
    const isGarageCode = /^(gr|gmid|gfloor|misc)/i.test(normalizedMapKey);
    const isOfficeCode = /^(or|omr|ofloor)/i.test(normalizedMapKey);
    if (isGarageCode) {
      addCandidate(candidates, '/shelf-base/' + encodeURIComponent(lower) + '.png');
      addCandidate(candidates, '/shelf-base/' + encodeURIComponent(compact) + '.png');
      addCandidate(candidates, '/shelf-base/' + encodeURIComponent(raw.toLowerCase()) + '.png');
      addCandidate(candidates, '/shelf-base/' + encodeURIComponent(raw) + '.png');
      addCandidate(candidates, '/shelf-base/' + encodeURIComponent(upper) + '.png');
      addCandidate(candidates, '/shelf-base/' + encodeURIComponent(rawUpper) + '.png');
      if (hasBin) {
        addCandidate(candidates, '/shelf-base/' + encodeURIComponent(nobin) + '.png');
        addCandidate(candidates, '/shelf-base/' + encodeURIComponent(nobinUpper) + '.png');
        addCandidate(candidates, '/shelf-base/' + encodeURIComponent(nobinRaw) + '.png');
      }
      // Also try standard shelf-image / shelf-original routes as fallback
      addCandidate(candidates, '/shelf-image/' + encodeURIComponent(lower) + '.png');
      addCandidate(candidates, '/shelf-image/' + encodeURIComponent(compact) + '.png');
      addCandidate(candidates, '/shelf-image/' + encodeURIComponent(upper) + '.png');
      if (hasBin) {
        addCandidate(candidates, '/shelf-image/' + encodeURIComponent(nobin) + '.png');
        addCandidate(candidates, '/shelf-image/' + encodeURIComponent(nobinUpper) + '.png');
      }
      addCandidate(candidates, '/shelf-original/' + encodeURIComponent(lower) + '.png');
      addCandidate(candidates, '/shelf-original/' + encodeURIComponent(compact) + '.png');
      addCandidate(candidates, '/shelf-original/' + encodeURIComponent(upper) + '.png');
      if (hasBin) {
        addCandidate(candidates, '/shelf-original/' + encodeURIComponent(nobin) + '.png');
        addCandidate(candidates, '/shelf-original/' + encodeURIComponent(nobinUpper) + '.png');
      }
    }
    if (isOfficeCode) {
      addCandidate(candidates, '/shelf-base/' + encodeURIComponent(lower) + '.png');
      addCandidate(candidates, '/shelf-base/' + encodeURIComponent(compact) + '.png');
      addCandidate(candidates, '/shelf-base/' + encodeURIComponent(raw.toLowerCase()) + '.png');
      addCandidate(candidates, '/shelf-base/' + encodeURIComponent(raw) + '.png');
      addCandidate(candidates, '/shelf-base/' + encodeURIComponent(upper) + '.png');
      addCandidate(candidates, '/shelf-base/' + encodeURIComponent(rawUpper) + '.png');
      if (hasBin) {
        addCandidate(candidates, '/shelf-base/' + encodeURIComponent(nobin) + '.png');
        addCandidate(candidates, '/shelf-base/' + encodeURIComponent(nobinUpper) + '.png');
        addCandidate(candidates, '/shelf-base/' + encodeURIComponent(nobinRaw) + '.png');
      }
      addCandidate(candidates, '/shelf-image/' + encodeURIComponent(lower) + '.png');
      addCandidate(candidates, '/shelf-image/' + encodeURIComponent(compact) + '.png');
      addCandidate(candidates, '/shelf-image/' + encodeURIComponent(raw.toLowerCase()) + '.png');
      addCandidate(candidates, '/shelf-image/' + encodeURIComponent(raw) + '.png');
      addCandidate(candidates, '/shelf-image/' + encodeURIComponent(upper) + '.png');
      addCandidate(candidates, '/shelf-image/' + encodeURIComponent(rawUpper) + '.png');
      if (hasBin) {
        addCandidate(candidates, '/shelf-image/' + encodeURIComponent(nobin) + '.png');
        addCandidate(candidates, '/shelf-image/' + encodeURIComponent(nobinUpper) + '.png');
        addCandidate(candidates, '/shelf-image/' + encodeURIComponent(nobinRaw) + '.png');
      }
      addCandidate(candidates, '/shelf-original/' + encodeURIComponent(lower) + '.png');
      addCandidate(candidates, '/shelf-original/' + encodeURIComponent(compact) + '.png');
      addCandidate(candidates, '/shelf-original/' + encodeURIComponent(raw.toLowerCase()) + '.png');
      addCandidate(candidates, '/shelf-original/' + encodeURIComponent(raw) + '.png');
      addCandidate(candidates, '/shelf-original/' + encodeURIComponent(upper) + '.png');
      addCandidate(candidates, '/shelf-original/' + encodeURIComponent(rawUpper) + '.png');
      if (hasBin) {
        addCandidate(candidates, '/shelf-original/' + encodeURIComponent(nobin) + '.png');
        addCandidate(candidates, '/shelf-original/' + encodeURIComponent(nobinUpper) + '.png');
        addCandidate(candidates, '/shelf-original/' + encodeURIComponent(nobinRaw) + '.png');
      }
    } else {
      addCandidate(candidates, '/shelf-image/' + encodeURIComponent(lower) + '.png');
      addCandidate(candidates, '/shelf-image/' + encodeURIComponent(compact) + '.png');
      addCandidate(candidates, '/shelf-image/' + encodeURIComponent(raw.toLowerCase()) + '.png');
      addCandidate(candidates, '/shelf-image/' + encodeURIComponent(raw) + '.png');
      addCandidate(candidates, '/shelf-image/' + encodeURIComponent(upper) + '.png');
      addCandidate(candidates, '/shelf-image/' + encodeURIComponent(rawUpper) + '.png');
      if (hasBin) {
        addCandidate(candidates, '/shelf-image/' + encodeURIComponent(nobin) + '.png');
        addCandidate(candidates, '/shelf-image/' + encodeURIComponent(nobinUpper) + '.png');
        addCandidate(candidates, '/shelf-image/' + encodeURIComponent(nobinRaw) + '.png');
      }
      addCandidate(candidates, '/shelf-original/' + encodeURIComponent(lower) + '.png');
      addCandidate(candidates, '/shelf-original/' + encodeURIComponent(compact) + '.png');
      addCandidate(candidates, '/shelf-original/' + encodeURIComponent(raw.toLowerCase()) + '.png');
      addCandidate(candidates, '/shelf-original/' + encodeURIComponent(raw) + '.png');
      addCandidate(candidates, '/shelf-original/' + encodeURIComponent(upper) + '.png');
      addCandidate(candidates, '/shelf-original/' + encodeURIComponent(rawUpper) + '.png');
      if (hasBin) {
        addCandidate(candidates, '/shelf-original/' + encodeURIComponent(nobin) + '.png');
        addCandidate(candidates, '/shelf-original/' + encodeURIComponent(nobinUpper) + '.png');
        addCandidate(candidates, '/shelf-original/' + encodeURIComponent(nobinRaw) + '.png');
      }
    }
    return withBust(candidates);
  }

  function pictureCandidates(pathValue) {
    const raw = trimValue(pathValue);
    const compact = compactCode(raw);
    const candidates = [];
    if (!raw) return candidates;
    if (/^https?:\/\//i.test(raw) || raw.startsWith('/')) {
      addCandidate(candidates, raw);
    } else {
      addCandidate(candidates, '/static/pictureposition/' + encodeURIComponent(raw));
      addCandidate(candidates, '/static/pictureposition/' + encodeURIComponent(compact));
      addCandidate(candidates, '/static/pictureposition/' + encodeURIComponent(raw) + '.jpg');
      addCandidate(candidates, '/static/pictureposition/' + encodeURIComponent(raw) + '.png');
      addCandidate(candidates, '/static/pictureposition/' + encodeURIComponent(compact) + '.jpg');
      addCandidate(candidates, '/static/pictureposition/' + encodeURIComponent(compact) + '.png');
    }
    return withBust(candidates);
  }

  function mapCandidates(code) {
    const key = deriveMapKey(code);
    const candidates = [];
    if (!key) return candidates;
    const lower = key.toLowerCase();
    if (/^(or|omr|ofloor)/i.test(lower)) {
      addCandidate(candidates, '/static/shelves/office/maps/officemap_' + encodeURIComponent(key) + '.png');
      addCandidate(candidates, '/static/shelves/maps/office/officemap_' + encodeURIComponent(key) + '.png');
      addCandidate(candidates, '/static/shelves/garage/maps/garagemap_' + encodeURIComponent(key) + '.png');
      addCandidate(candidates, '/static/shelves/maps/garage/garagemap_' + encodeURIComponent(key) + '.png');
    } else if (/^(gr|gmid|gfloor|misc)/i.test(lower)) {
      addCandidate(candidates, '/static/shelves/garage/maps/garagemap_' + encodeURIComponent(key) + '.png');
      addCandidate(candidates, '/static/shelves/maps/garage/garagemap_' + encodeURIComponent(key) + '.png');
      addCandidate(candidates, '/static/shelves/office/maps/officemap_' + encodeURIComponent(key) + '.png');
      addCandidate(candidates, '/static/shelves/maps/office/officemap_' + encodeURIComponent(key) + '.png');
    } else {
      addCandidate(candidates, '/static/shelves/office/maps/officemap_' + encodeURIComponent(key) + '.png');
      addCandidate(candidates, '/static/shelves/maps/office/officemap_' + encodeURIComponent(key) + '.png');
      addCandidate(candidates, '/static/shelves/garage/maps/garagemap_' + encodeURIComponent(key) + '.png');
      addCandidate(candidates, '/static/shelves/maps/garage/garagemap_' + encodeURIComponent(key) + '.png');
    }
    return withBust(candidates);
  }

  function loadFirstAvailable(candidates) {
    return new Promise(function (resolve, reject) {
      if (!Array.isArray(candidates) || !candidates.length) {
        reject(new Error('No candidates'));
        return;
      }
      let index = 0;
      const tryNext = function () {
        if (index >= candidates.length) {
          reject(new Error('Image not found'));
          return;
        }
        const src = candidates[index++];
        const img = new Image();
        img.onload = function () { resolve(src); };
        img.onerror = function () { tryNext(); };
        img.src = src;
      };
      tryNext();
    });
  }

  async function buildItems(options) {
    const code = trimValue(options && options.code);
    const picturePath = trimValue(options && options.picturePath);
    const items = [];

    if (picturePath && looksLikePicturePath(picturePath)) {
      const pictureSrc = await loadFirstAvailable(pictureCandidates(picturePath)).catch(function () { return null; });
      if (pictureSrc) {
        items.push({ label: 'Location image', src: pictureSrc });
        return items;
      }
      // Picture not found — fall through to shelf/map lookup using the location code
    }

    const effectiveCode = code;
    if (!effectiveCode) return items;

    const shelfSrc = await loadFirstAvailable(shelfCandidates(effectiveCode)).catch(function () { return null; });
    if (shelfSrc) {
      items.push({ label: 'Shelf photo', src: shelfSrc });
    }

    const mapSrc = await loadFirstAvailable(mapCandidates(effectiveCode)).catch(function () { return null; });
    if (mapSrc) {
      items.push({ label: 'Position map', src: mapSrc });
    }

    const seen = new Set();
    return items.filter(function (item) {
      if (!item || !item.src || seen.has(item.src)) return false;
      seen.add(item.src);
      return true;
    });
  }

  function setOpen(open) {
    ensureDom();
    root.classList.toggle('is-open', open);
    root.setAttribute('aria-hidden', open ? 'false' : 'true');
  }

  function render() {
    if (!bodyEl || !titleEl || !noteEl) return;
    titleEl.textContent = state.title || 'Location Preview';

    if (!state.items.length) {
      noteEl.textContent = '';
      bodyEl.innerHTML = '<div class="sslp-empty">No location preview found.</div>';
      return;
    }

    if (state.expandedIndex !== null && state.items[state.expandedIndex]) {
      const item = state.items[state.expandedIndex];
      const backLabel = state.items.length > 1 ? '← Back to previews' : '✕ Close';
      noteEl.textContent = '';
      bodyEl.innerHTML = [
        '<div class="sslp-expanded">',
        '  <div class="sslp-label">' + escHtml(item.label) + '</div>',
        '  <div class="sslp-image-wrap" data-sslp-back="1">',
        '    <img class="sslp-image" src="' + escHtml(item.src) + '" alt="' + escHtml(item.label) + '" loading="lazy">',
        '  </div>',
        '  <button type="button" class="sslp-back-btn" data-sslp-back="1">' + backLabel + '</button>',
        '</div>'
      ].join('');
      return;
    }

    noteEl.textContent = state.items.length > 1
      ? 'Tap an image to enlarge it.'
      : '';
    bodyEl.innerHTML = '<div class="sslp-grid">' + state.items.map(function (item, index) {
      return [
        '<button type="button" class="sslp-card" data-sslp-index="' + index + '">',
        '  <div class="sslp-label">' + escHtml(item.label) + '</div>',
        '  <div class="sslp-image-wrap">',
        '    <img class="sslp-image" src="' + escHtml(item.src) + '" alt="' + escHtml(item.label) + '" loading="lazy">',
        '  </div>',
        '</button>'
      ].join('');
    }).join('') + '</div>' +
    '<div class="sslp-close-row"><button type="button" data-sslp-close="1">✕ Close</button></div>';
  }

  async function open(options) {
    ensureDom();
    state.token += 1;
    const token = state.token;
    state.title = trimValue(options && options.title) || 'Location Preview';
    state.items = [];
    state.expandedIndex = null;
    titleEl.textContent = state.title;
    noteEl.textContent = 'Loading location preview...';
    bodyEl.innerHTML = '<div class="sslp-empty">Loading location preview...</div>';
    setOpen(true);

    const items = await buildItems(options || {});
    if (token !== state.token) return;
    state.items = items;
    const requestedIndex = parseInt(options && options.startExpandedIndex, 10);
    state.expandedIndex = Number.isFinite(requestedIndex) && requestedIndex >= 0 && requestedIndex < items.length
      ? requestedIndex
      : null;
    render();
  }

  function close() {
    if (!root) return;
    state.expandedIndex = null;
    state.items = [];
    setOpen(false);
    if (bodyEl) bodyEl.innerHTML = '';
    if (noteEl) noteEl.textContent = '';
  }

  function openForLocation(locationCode, previewKey, title, startExpandedIndex) {
    const code = trimValue(locationCode);
    const key = trimValue(previewKey);
    if (key && looksLikePicturePath(key)) {
      return open({
        code: code,
        picturePath: key,
        title: trimValue(title) || ('Location: ' + (code || key)),
        startExpandedIndex: startExpandedIndex
      });
    }
    return open({
      code: code || key,
      picturePath: '',
      title: trimValue(title) || ('Location: ' + (code || key)),
      startExpandedIndex: startExpandedIndex
    });
  }

  window.SSLocationPreview = {
    open: open,
    close: close,
    openForLocation: openForLocation,
    looksLikePicturePath: looksLikePicturePath
  };
})();
