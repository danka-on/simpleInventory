// Sweet Shelves Lister field matcher. Pure functions over field descriptors, shared by the
// injected page script, the side panel and the Node tests (no DOM access here).
(function (root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  root.SSListerMatcher = api;
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  'use strict';

  // Which page-field words point at which of our values. "not" words veto a candidate.
  const TARGETS = {
    title: {
      any: ['title', 'item title', 'listing title', 'product name', 'item name', 'product title'],
      not: ['subtitle', 'sub title', 'condition', 'search', 'meta', 'keyword'],
      types: ['text', 'textarea', 'search', ''],
    },
    price: {
      any: ['price', 'your price', 'buy it now price', 'item price', 'standard price', 'list price'],
      not: ['shipping', 'compare', 'msrp', 'minimum', 'maximum', 'discount', 'sale price', 'business price',
        'reserve', 'starting bid', 'best offer', 'cost', 'per unit', 'map'],
      types: ['text', 'number', 'tel', ''],
    },
    quantity: {
      any: ['quantity', 'qty', 'available quantity', 'quantity available', 'fulfillable quantity'],
      not: ['minimum', 'lot', 'per package', 'package', 'unit count', 'number of items', 'maximum', 'handling'],
      types: ['text', 'number', 'tel', ''],
    },
    sku: {
      any: ['sku', 'custom label', 'seller sku', 'contribution sku', 'merchant sku', 'custom label sku', 'inventory id'],
      not: ['asin', 'search'],
      types: ['text', 'search', ''],
    },
    upc: {
      any: ['upc', 'ean', 'gtin', 'barcode', 'product id', 'external product id', 'externally assigned product identifier',
        'product identifier', 'isbn'],
      not: ['type', 'exemption', 'does not apply'],
      types: ['text', 'search', 'tel', 'number', ''],
    },
    conditionDescription: {
      any: ['condition description', 'condition note', 'condition notes', 'describe the condition', 'seller notes',
        'offer condition note'],
      not: [],
      types: ['text', 'textarea', ''],
    },
    description: {
      any: ['description', 'item description', 'product description', 'listing description', 'details'],
      not: ['condition', 'short', 'meta', 'search', 'keyword', 'bullet'],
      types: ['textarea', 'contenteditable', 'text', ''],
    },
    brand: {
      any: ['brand', 'brand name', 'manufacturer'],
      not: ['part number', 'model'],
      types: ['text', 'search', 'select', 'combobox', ''],
    },
    asin: {
      any: ['asin', 'amazon standard identification number'],
      not: [],
      types: ['text', 'search', ''],
    },
  };

  // eBay inventory condition enum -> wording seen on eBay / Amazon forms.
  const CONDITION_LABELS = {
    NEW: { ebay: ['New', 'Brand New', 'New with tags', 'New with box'], amazon: ['New'] },
    NEW_OTHER: { ebay: ['New (Other)', 'New other', 'Open box', 'New without tags', 'New without box'], amazon: ['New - Open Box', 'Open Box', 'New'] },
    NEW_WITH_DEFECTS: { ebay: ['New with defects'], amazon: ['New - Open Box', 'Used - Like New'] },
    USED_EXCELLENT: { ebay: ['Excellent', 'Used - Excellent', 'Pre-owned - Excellent', 'Like New'], amazon: ['Used - Like New', 'Like New'] },
    USED_VERY_GOOD: { ebay: ['Very Good', 'Pre-owned - Very Good', 'Used'], amazon: ['Used - Very Good', 'Very Good'] },
    USED_GOOD: { ebay: ['Good', 'Pre-owned - Good', 'Used'], amazon: ['Used - Good', 'Good'] },
    USED_ACCEPTABLE: { ebay: ['Acceptable', 'Pre-owned - Fair', 'Used'], amazon: ['Used - Acceptable', 'Acceptable'] },
    FOR_PARTS_OR_NOT_WORKING: { ebay: ['For parts or not working'], amazon: [] },
  };

  const SKIP_TYPES = new Set(['hidden', 'checkbox', 'radio', 'file', 'submit', 'button', 'reset', 'image', 'color', 'range', 'date', 'password']);

  function normalize(value) {
    return String(value || '')
      .toLowerCase()
      .replace(/[_\-\/.:]+/g, ' ')
      .replace(/[^a-z0-9 ]+/g, ' ')
      .replace(/\s+/g, ' ')
      .trim();
  }

  function hasWord(haystack, needle) {
    if (!haystack || !needle) return false;
    if (haystack === needle) return true;
    return new RegExp('(^| )' + needle.replace(/[.*+?^${}()|[\]\\]/g, '\\$&') + '( |$)').test(haystack);
  }

  // Descriptor fields and how much a hit on each is worth.
  const WEIGHTS = [['labelText', 6], ['ariaLabel', 6], ['name', 5], ['id', 4], ['placeholder', 3], ['nearbyText', 2]];

  function typeAllowed(target, descriptor) {
    const type = descriptor.contenteditable ? 'contenteditable' : (descriptor.tag === 'select' ? 'select'
      : descriptor.tag === 'textarea' ? 'textarea' : (descriptor.role === 'combobox' ? 'combobox' : normalize(descriptor.type || '')));
    if (SKIP_TYPES.has(type)) return false;
    const allowed = TARGETS[target].types;
    if (allowed.includes(type)) return true;
    // role=textbox inputs and unknown tags behave like text inputs.
    return type === '' || (type === 'combobox' && allowed.includes('text')) || (type === 'select' && target === 'brand');
  }

  function scoreTarget(target, descriptor) {
    const spec = TARGETS[target];
    if (!spec || !typeAllowed(target, descriptor)) return 0;
    let score = 0;
    let hit = false;
    for (const [field, weight] of WEIGHTS) {
      const text = normalize(descriptor[field]);
      if (!text) continue;
      for (const word of spec.any) {
        const needle = normalize(word);
        if (text === needle) { score += weight + 4; hit = true; }
        else if (hasWord(text, needle)) { score += weight; hit = true; }
      }
      for (const word of spec.not) {
        if (hasWord(text, normalize(word))) score -= weight + 2;
      }
    }
    if (!hit) return 0;
    if (target === 'title' && descriptor.maxLength && descriptor.maxLength >= 60 && descriptor.maxLength <= 100) score += 3;
    if ((target === 'price' || target === 'quantity') && normalize(descriptor.type) === 'number') score += 1;
    if (target === 'description' && descriptor.contenteditable) score += 4;
    if (target === 'description' && descriptor.tag === 'textarea') score += 2;
    return Math.max(0, score);
  }

  // Assign each target to at most one descriptor and each descriptor to at most one target.
  function assign(descriptors, wanted, minimum) {
    const threshold = typeof minimum === 'number' ? minimum : 5;
    const targets = wanted && wanted.length ? wanted : Object.keys(TARGETS);
    const pairs = [];
    descriptors.forEach((descriptor, index) => {
      for (const target of targets) {
        const score = scoreTarget(target, descriptor);
        if (score >= threshold) pairs.push({ target, index, score });
      }
    });
    pairs.sort((a, b) => b.score - a.score || a.index - b.index);
    const usedTargets = new Set();
    const usedIndexes = new Set();
    const result = {};
    for (const pair of pairs) {
      if (usedTargets.has(pair.target) || usedIndexes.has(pair.index)) continue;
      usedTargets.add(pair.target);
      usedIndexes.add(pair.index);
      result[pair.target] = { index: pair.index, score: pair.score };
    }
    return result;
  }

  // Which of our values fits a field the user pointed at (best first).
  function suggestTargets(descriptor) {
    return Object.keys(TARGETS)
      .map(target => ({ target, score: scoreTarget(target, descriptor) }))
      .filter(entry => entry.score > 0)
      .sort((a, b) => b.score - a.score);
  }

  // Item specifics: a field whose label is exactly the aspect name (Brand, Color, Material...).
  function matchAspects(descriptors, aspects) {
    const result = {};
    const used = new Set();
    for (const [aspect, values] of Object.entries(aspects || {})) {
      const wanted = normalize(aspect);
      if (!wanted || !values || !values.length) continue;
      let best = -1;
      let bestScore = 0;
      descriptors.forEach((descriptor, index) => {
        if (used.has(index)) return;
        const type = normalize(descriptor.type);
        if (SKIP_TYPES.has(type)) return;
        let score = 0;
        for (const [field, weight] of WEIGHTS) {
          const text = normalize(descriptor[field]);
          if (!text) continue;
          if (text === wanted) score += weight + 6;
          else if (text.startsWith(wanted + ' ') || text.endsWith(' ' + wanted) && field !== 'nearbyText') score += weight;
        }
        if (score > bestScore) { bestScore = score; best = index; }
      });
      if (best >= 0 && bestScore >= 8) {
        used.add(best);
        result[aspect] = { index: best, score: bestScore, value: values[0] };
      }
    }
    return result;
  }

  // Signature stored when the user teaches a field with "Pick field"; matched exactly later.
  function signature(descriptor) {
    return {
      name: descriptor.name || '',
      id: descriptor.id || '',
      ariaLabel: descriptor.ariaLabel || '',
      labelText: normalize(descriptor.labelText || ''),
      tag: descriptor.tag || '',
    };
  }

  function matchesSignature(descriptor, sig) {
    if (!sig) return false;
    const d = signature(descriptor);
    if (sig.id && d.id === sig.id) return true;
    if (sig.name && d.name === sig.name && d.tag === sig.tag) return true;
    if (sig.ariaLabel && d.ariaLabel === sig.ariaLabel && d.tag === sig.tag) return true;
    return Boolean(sig.labelText) && d.labelText === sig.labelText && d.tag === sig.tag && !sig.id && !sig.name;
  }

  // What kind of store page is this URL?
  function detectPage(href) {
    let url;
    try { url = new URL(href || ''); } catch { return { store: '', kind: '', listingId: '', asin: '', sku: '' }; }
    const host = url.hostname.toLowerCase();
    const path = url.pathname;
    const params = url.searchParams;
    const out = { store: '', kind: '', listingId: '', asin: '', sku: '' };
    if (/(^|\.)ebay\.[a-z.]+$/.test(host)) {
      out.store = 'ebay';
      const item = path.match(/\/itm\/(?:[^/]+\/)?(\d{9,15})/) || (params.get('itemId') || params.get('item') || '').match(/^(\d{9,15})$/);
      if (item) out.listingId = item[1];
      if (out.listingId && !/\/sl\//.test(path)) out.kind = 'listing-live';
      else if (/^\/sl\/(prelist|list|sell)/.test(path) || /^\/lstng/.test(path) || host.startsWith('bulksell.')) out.kind = 'listing-form';
      else if (/^\/sh\/lst\/(active|drafts|ended)/.test(path) || /^\/sh\//.test(path)) out.kind = 'seller-hub';
      else if (/^\/sl\//.test(path)) out.kind = 'listing-form';
      if (out.kind === 'seller-hub' && out.listingId) out.kind = 'listing-live';
      const draftSku = params.get('sku') || params.get('customLabel');
      if (draftSku) out.sku = draftSku;
      return out;
    }
    if (/(^|\.)sellercentral(-europe)?\.amazon\.[a-z.]+$/.test(host) || /(^|\.)sellercentral\.amazon\.[a-z.]+$/.test(host)) {
      out.store = 'amazon';
      const asin = path.match(/\/(B0[A-Z0-9]{8}|[0-9]{9}[0-9X])(\/|$)/i) || (params.get('asin') || '').match(/^([A-Z0-9]{10})$/i);
      if (asin) out.asin = asin[1].toUpperCase();
      out.sku = params.get('sku') || params.get('mSku') || params.get('sellerSku') || '';
      if (/\/abis\/(listing|Display|syh|product-search|display)/i.test(path) || /\/abis\//i.test(path)) {
        out.kind = /product-search|search/i.test(path) ? 'product-search' : 'offer-form';
      } else if (/\/inventory/i.test(path) || /\/myinventory/i.test(path)) out.kind = 'inventory';
      else if (/\/productsearch|\/product-search/i.test(path)) out.kind = 'product-search';
      if (out.asin) out.listingId = out.asin;
      return out;
    }
    return out;
  }

  function conditionLabels(condition, store) {
    const entry = CONDITION_LABELS[String(condition || '').toUpperCase()];
    if (!entry) return [];
    return store === 'amazon' ? entry.amazon : entry.ebay;
  }

  // Pick the <option> whose text best matches one of the wanted labels (exact, then contains).
  function chooseOption(options, wanted) {
    const labels = (wanted || []).map(normalize).filter(Boolean);
    if (!labels.length) return -1;
    const normalized = options.map(normalize);
    for (const label of labels) {
      const exact = normalized.indexOf(label);
      if (exact >= 0) return exact;
    }
    for (const label of labels) {
      const partial = normalized.findIndex(text => text && (text.includes(label) || label.includes(text)) && text !== 'select' && !text.startsWith('choose'));
      if (partial >= 0) return partial;
    }
    return -1;
  }

  return {
    TARGETS, CONDITION_LABELS, normalize, scoreTarget, assign, suggestTargets, matchAspects,
    signature, matchesSignature, detectPage, conditionLabels, chooseOption,
  };
});
