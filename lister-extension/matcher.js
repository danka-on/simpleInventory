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
      types: ['text', 'textarea', 'contenteditable', ''],
      ownLabelOnly: true,  // never by nearby text: the item description editor sits under a "Condition" heading on eBay
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

  // The lowest offer as the offer page writes it, for when Seller Central draws no "Match lowest
  // price" link to press. Our own boxes (your price, minimum price) are never it, and the figure
  // has to sit right next to the wording. Best source first.
  const LOWEST_SHAPES = [
    { label: 'lowest price', re: /lowest[a-z ]{0,20}price[^$]{0,30}\$\s*([0-9][0-9,]*(?:\.[0-9]{1,2})?)/i },
    { label: 'lowest offer', re: /lowest[a-z ]{0,20}offer[^$]{0,30}\$\s*([0-9][0-9,]*(?:\.[0-9]{1,2})?)/i },
    // The item panel beside the form: "Competing Marketplace Offers: 2 New from $29.40 + $0.00 shipping".
    { label: 'competing offers', re: /competing[a-z ]{0,20}offers[^$]{0,40}\$\s*([0-9][0-9,]*(?:\.[0-9]{1,2})?)/i },
    { label: 'featured offer price', re: /(?:featured offer|buy box)[a-z ]{0,30}[^$]{0,30}\$\s*([0-9][0-9,]*(?:\.[0-9]{1,2})?)/i },
  ];

  function lowestPrice(text) {
    const sample = String(text || '').replace(/\s+/g, ' ').slice(0, 40000);
    for (const shape of LOWEST_SHAPES) {
      const match = sample.match(shape.re);
      if (!match) continue;
      const price = Number(String(match[1]).replace(/,/g, ''));
      if (Number.isFinite(price) && price > 0) return { price, label: shape.label };
    }
    return null;
  }

  // eBay's own price advice beside the price box ("Recommended: $24.99", "eBay recommends $24.99",
  // "Suggested price $24.99", "Pricing recommendation ... $24.99"). A sold range ("Similar items sold
  // for $20 - $35") is not advice, so it is never read as one.
  const SUGGESTED_SHAPES = [
    { label: 'recommended', re: /(?:ebay\s+)?recommend(?:ed|s)[a-z ]{0,30}?(?:price)?[^$\d]{0,30}\$\s*([0-9][0-9,]*(?:\.[0-9]{1,2})?)/i },
    { label: 'price recommendation', re: /pric(?:e|ing)\s+recommendation[^$]{0,60}\$\s*([0-9][0-9,]*(?:\.[0-9]{1,2})?)/i },
    { label: 'suggested', re: /suggested[a-z ]{0,20}price[^$\d]{0,30}\$\s*([0-9][0-9,]*(?:\.[0-9]{1,2})?)/i },
    { label: 'price guidance', re: /price\s+guidance[^$]{0,60}\$\s*([0-9][0-9,]*(?:\.[0-9]{1,2})?)/i },
  ];

  function suggestedPrice(text) {
    const sample = String(text || '').replace(/\s+/g, ' ').slice(0, 40000);
    for (const shape of SUGGESTED_SHAPES) {
      for (const match of sample.matchAll(new RegExp(shape.re.source, 'gi'))) {
        // eBay also "recommends" shipping services with a price: those are not the item's price.
        const around = sample.slice(Math.max(0, match.index - 25), match.index + match[0].length);
        if (/shipping|postage|delivery|usps|fedex|\bups\b|ground advantage|carrier/i.test(around)) continue;
        const price = Number(String(match[1]).replace(/,/g, ''));
        if (Number.isFinite(price) && price > 0) return { price, label: shape.label };
      }
    }
    return null;
  }

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
      if (spec.ownLabelOnly && field === 'nearbyText') continue;
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
  //   listing-start   the store's "what are you selling" search (eBay prelist, Seller Central product search)
  //   listing-form    the form where title/price/quantity are typed (eBay list, Seller Central offer)
  //   listing-success eBay's "your item is listed" page (item number in the URL)
  //   listing-live    a live eBay listing
  function detectPage(href) {
    let url;
    try { url = new URL(href || ''); } catch { return { store: '', kind: '', listingId: '', asin: '', sku: '' }; }
    const host = url.hostname.toLowerCase();
    const path = url.pathname;
    const params = url.searchParams;
    const out = { store: '', kind: '', listingId: '', asin: '', sku: '' };
    if (/(^|\.)ebay\.[a-z.]+$/.test(host)) {
      out.store = 'ebay';
      // The prelist steps carry ANOTHER seller's item in itemId (mode=SellLikeItem): never our listing.
      const prelist = /^\/sl\/prelist/.test(path) || params.getAll('mode').includes('SellLikeItem');
      const paramItem = (params.get('itemId') || params.get('item') || params.get('itemid') || '').match(/^(\d{9,15})$/);
      const item = path.match(/\/itm\/(?:[^/]+\/)?(\d{9,15})/) || (!prelist && paramItem);
      if (item) out.listingId = item[1];
      if (prelist && paramItem) out.matchId = paramItem[1];
      if (out.listingId && !/\/sl\//.test(path)) out.kind = 'listing-live';
      else if (/^\/sl\/[a-z]+\/success/.test(path) || (out.listingId && /^\/sl\//.test(path) && /success|congrat/.test(path))) out.kind = 'listing-success';
      else if (/^\/sl\/prelist\/identify/.test(path)) out.kind = params.get('view') === 'sellnode-condition' ? 'listing-confirm' : 'listing-match';
      else if (/^\/sl\/prelist\/catalog/.test(path)) out.kind = 'listing-match';
      else if (/^\/sl\/(prelist|sell)(\/|$)/.test(path)) out.kind = 'listing-start';
      else if (/^\/sl\/list/.test(path) || /^\/lstng/.test(path) || host.startsWith('bulksell.')) out.kind = 'listing-form';
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
      if (/\/product-search|\/productsearch/i.test(path)) out.kind = 'listing-start';
      else if (/\/abis\/(listing|Display|syh|display)/i.test(path) || /\/abis\//i.test(path)) {
        // "List Your Products" (abis/listing/syh with no product yet) is the search step; with an ASIN it is the offer form.
        out.kind = /product-search|search/i.test(path) || (!out.asin && !out.sku && /\/abis\/listing\/syh/i.test(path)) ? 'listing-start' : 'offer-form';
      } else if (/\/interactive\/listing\/workflow/i.test(path)) {
        // The new listing workflow (also where a resumed draft lands): offer/price step.
        out.kind = 'offer-form';
      } else if (/\/inventory/i.test(path) || /\/myinventory/i.test(path)) out.kind = 'inventory';
      if (out.asin) out.listingId = out.asin;
      return out;
    }
    return out;
  }

  // Did the page just confirm a new listing? Pure text heuristics; the page script adds ids.
  // Strict on purpose: the prelist steps show other sellers' listings and "View listing" links.
  const EBAY_SUCCESS = /(your (item|listing) (is|was|has been) (listed|live|published)|you(?:'ve| have) (successfully )?listed (your|this|an|the) (item|listing)|your listing is (now )?live|listed successfully)/i;
  const AMAZON_SUCCESS = /((listing|offer|product|your changes?) (was|has been|is being|will be|were|have been) (saved|created|submitted|processed|added|updated)|your (listing|product|offer) is (now )?(live|active|being processed)|successfully (saved|created|submitted|listed)|listing (submitted|created) successfully|(it|this) (may|can) take up to \d+ (minutes|hours) (for|before|until))/i;

  function successInfo(store, text) {
    const sample = String(text || '').slice(0, 20000);
    if (store === 'ebay') return { success: EBAY_SUCCESS.test(sample) };
    if (store === 'amazon') return { success: AMAZON_SUCCESS.test(sample) };
    return { success: false };
  }

  // Fields the store insists on (required marker) that are still empty.
  function isRequired(descriptor) {
    if (descriptor.required || descriptor.ariaRequired) return true;
    const label = String(descriptor.labelText || '') + ' ' + String(descriptor.nearbyText || '');
    return /\*|\brequired\b/i.test(label);
  }

  function isEmptyValue(descriptor) {
    const value = String(descriptor.value || '').trim();
    if (!value) return true;
    if (descriptor.tag === 'select') return /^(select|choose|pick|-+|please select)/i.test(value);
    return false;
  }

  // The store's product search box on a listing-start page (never the site-wide header search).
  function searchBoxScore(store, descriptor) {
    const type = normalize(descriptor.type);
    if (SKIP_TYPES.has(type) || descriptor.tag === 'select' || descriptor.contenteditable) return 0;
    const text = normalize([descriptor.labelText, descriptor.ariaLabel, descriptor.placeholder, descriptor.name, descriptor.id, descriptor.nearbyText].join(' '));
    if (!text) return 0;
    if (/search for anything|gh ac|site search|search ebay/.test(text)) return 0;
    let score = 0;
    if (store === 'amazon') {
      if (/search term|product name|upc|ean|isbn|asin|gtin/.test(text)) score += 6;
      if (/product title|keywords|keyword|title description|your catalog|amazon s catalog/.test(text)) score += 6;  // "List Your Products" search box
      if (/search products|search for products|find products|search by|product id/.test(text)) score += 6;  // /product-search
      if (descriptor.id === 'search-term' || descriptor.name === 'search-term') score += 6;
    } else {
      if (/what are you selling|what you re selling|tell us what|brand model|upc|isbn|ean|product|find your item|item you re selling|search/.test(text)) score += 4;
      if (/what are you selling|tell us what|brand model|find your item/.test(text)) score += 4;
      if (descriptor.id === 's0-1-1-24-7-@keyword-@box-@input-textbox' || /keyword/.test(text)) score += 4;
    }
    if (type === 'search') score += 2;
    return score;
  }

  function conditionLabels(condition, store) {
    const entry = CONDITION_LABELS[String(condition || '').toUpperCase()];
    if (!entry) return [];
    return store === 'amazon' ? entry.amazon : entry.ebay;
  }

  // eBay's prelist "Confirm details" step offers radios: either New / Open box / Used / For parts,
  // or Brand New / Like New / Very Good / Good / Acceptable. Best label first, then fallbacks.
  function prelistCondition(condition) {
    const value = String(condition || '').toUpperCase();
    if (value === 'NEW') return ['New', 'Brand New'];
    if (value === 'NEW_OTHER') return ['Open box', 'New (Other)', 'Like New', 'New', 'Brand New'];
    if (value === 'NEW_WITH_DEFECTS') return ['Open box', 'Very Good', 'Like New', 'New'];
    if (value === 'FOR_PARTS_OR_NOT_WORKING') return ['For parts or not working', 'For parts', 'Acceptable'];
    if (value === 'USED_EXCELLENT') return ['Like New', 'Used', 'Pre-owned', 'Very Good'];
    if (value === 'USED_VERY_GOOD') return ['Very Good', 'Used', 'Pre-owned', 'Good'];
    if (value === 'USED_GOOD') return ['Good', 'Used', 'Pre-owned', 'Very Good'];
    if (value === 'USED_ACCEPTABLE') return ['Acceptable', 'Used', 'Pre-owned', 'Good'];
    if (value.startsWith('USED')) return ['Used', 'Pre-owned', 'Good'];
    return [];
  }

  const STOP_WORDS = new Set(['the', 'and', 'for', 'with', 'of', 'a', 'an', 'in', 'set', 'new', 'pc', 'pcs', 'piece', 'pieces', 'x']);

  function tokens(value) {
    return new Set(normalize(value).split(' ').filter(word => word.length > 1 && !STOP_WORDS.has(word)));
  }

  // How well a store candidate (catalog match title) fits our item: shared words over our words,
  // with a bonus when the brand (our first word) appears and a penalty for very long candidates.
  function candidateScore(candidate, target, brand) {
    const ours = tokens(target);
    const theirs = tokens(candidate);
    if (!ours.size || !theirs.size) return 0;
    let shared = 0;
    for (const word of ours) if (theirs.has(word)) shared += 1;
    let score = shared / ours.size;
    const brandWord = normalize(brand || '').split(' ')[0] || [...ours][0];
    if (brandWord && theirs.has(brandWord)) score += 0.25;
    if (theirs.size > ours.size * 3) score -= 0.1;
    return Math.max(0, Math.round(score * 1000) / 1000);
  }

  function rankCandidates(candidates, target, brand) {
    return candidates
      .map((candidate, index) => ({ index, candidate, score: candidateScore(candidate, target, brand) }))
      .sort((a, b) => b.score - a.score || a.index - b.index);
  }

  // How well a category path on the page matches the one we want (segments compared from the leaf up).
  function categoryScore(candidatePath, wantedPath) {
    const split = value => String(value || '').split(/\s*(?:>|›|\/)\s*/).map(normalize).filter(Boolean);
    const ours = split(wantedPath);
    const theirs = split(candidatePath);
    if (!ours.length || !theirs.length) return 0;
    if (ours.join('>') === theirs.join('>')) return 1;
    let score = 0;
    const leaf = ours[ours.length - 1];
    if (theirs[theirs.length - 1] === leaf) score += 0.6;
    else if (theirs.includes(leaf)) score += 0.3;
    const shared = theirs.filter(segment => ours.includes(segment)).length;
    score += 0.4 * (shared / Math.max(ours.length, theirs.length));
    return Math.round(score * 1000) / 1000;
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

  // Which Seller Hub row (after List it eBay lands on its active listings, no success page) is the listing
  // just submitted: the row showing our custom label (the unit code), else the one with our title.
  // Item numbers already linked are skipped; among several, the newest (highest) item number wins.
  function hubMatch(rows, { code = '', title = '', taken = [] } = {}) {
    const skip = new Set((taken || []).map(String));
    const open = (rows || []).filter(r => r && /^\d{9,15}$/.test(String(r.itemId)) && !skip.has(String(r.itemId)));
    const newest = list => list.reduce((a, b) => (BigInt(b.itemId) > BigInt(a.itemId) ? b : a));
    const unit = String(code || '').toLowerCase().replace(/[^0-9a-z-]/g, '');
    const bySku = unit ? open.filter(r => new RegExp('(^|[^0-9a-z-])' + unit + '($|[^0-9a-z-])', 'i').test(r.text || '')) : [];
    if (bySku.length) return { ...newest(bySku), how: 'custom label' };
    const key = s => ' ' + String(s || '').toLowerCase().replace(/[^0-9a-z]+/g, ' ').trim() + ' ';
    const wanted = key(title);
    const byTitle = wanted.trim().length >= 20 ? open.filter(r => key(r.text).includes(wanted)) : [];
    return byTitle.length ? { ...newest(byTitle), how: 'title' } : null;
  }

  return {
    TARGETS, CONDITION_LABELS, normalize, scoreTarget, assign, suggestTargets, matchAspects,
    signature, matchesSignature, detectPage, successInfo, isRequired, isEmptyValue, searchBoxScore,
    conditionLabels, prelistCondition, lowestPrice, suggestedPrice, tokens, candidateScore, rankCandidates, categoryScore, chooseOption, hubMatch,
  };
});
