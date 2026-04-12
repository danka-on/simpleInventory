
function esc(s){ return String(s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;'); }
function missingThumb(width=48,height=48,text='No Img'){ return 'data:image/svg+xml;utf8,'+encodeURIComponent('<svg xmlns="http://www.w3.org/2000/svg" width="'+width+'" height="'+height+'"><rect width="100%" height="100%" fill="#f8f8f8"/><text x="50%" y="50%" dominant-baseline="middle" text-anchor="middle" fill="#b0b0b0" font-size="10">'+text+'</text></svg>'); }
function resolveStaticAssetUrl(rawPath){
  const raw = String(rawPath || '').trim();
  if(!raw) return '';
  if(/^https?:\/\//i.test(raw) || raw.startsWith('/')) return raw;
  if(raw.startsWith('static/')) return '/' + raw;
  return '/static/' + raw.replace(/^\/+/, '');
}
function formatEasternDateTime(value){
  const raw = String(value || '').trim();
  if(!raw) return '';
  const dt = new Date(raw);
  if(Number.isNaN(dt.getTime())) return raw;
  return dt.toLocaleString('en-US', {
    timeZone: 'America/New_York',
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: 'numeric',
    minute: '2-digit',
    hour12: true
  });
}
function formatDateOnly(value){
  const raw = String(value || '').trim();
  if(!raw) return '';
  const dt = new Date(raw);
  if(Number.isNaN(dt.getTime())){
    const isoLike = raw.match(/^(\d{4}-\d{2}-\d{2})/);
    return isoLike ? isoLike[1] : raw;
  }
  const year = dt.getFullYear();
  const month = String(dt.getMonth() + 1).padStart(2, '0');
  const day = String(dt.getDate()).padStart(2, '0');
  return `${year}-${month}-${day}`;
}
function _decodePrepBreakdown(encodedPayload){
  if(!encodedPayload) return [];
  try{
    const raw = decodeURIComponent(encodedPayload);
    const parsed = JSON.parse(raw || '[]');
    return Array.isArray(parsed) ? parsed.filter(entry => entry && typeof entry === 'object') : [];
  }catch(err){
    return [];
  }
}
function onOpenPrepBreakdown(e){
  e.preventDefault();
  const btn = e.currentTarget;
  if(!btn) return;

  const upc = String(btn.dataset.upc || '').trim();
  const currentLot = String(btn.dataset.currentLot || '').trim();
  const currentLabel = String(btn.dataset.currentLabel || '').trim() || (currentLot || 'LOTLESS');
  const qty = Math.max(0, Number(btn.dataset.qty || 0) || 0);
  const totalQty = Math.max(0, Number(btn.dataset.totalQty || qty) || 0);
  const hasLotless = btn.dataset.hasLotless === '1';
  const groupedAllLots = btn.dataset.groupedAllLots === '1';
  const breakdown = _decodePrepBreakdown(btn.dataset.breakdown || '');

  const modal = document.getElementById('prepBreakdownModal');
  const title = document.getElementById('prepBreakdownTitle');
  const meta = document.getElementById('prepBreakdownMeta');
  const content = document.getElementById('prepBreakdownContent');
  if(!modal || !title || !meta || !content) return;

  title.textContent = 'Prep by LOT';
  meta.textContent = [
    upc ? `UPC: ${upc}` : '',
    groupedAllLots ? 'Current row: ALL LOTS' : `Current row: ${currentLabel}`,
    `Visible PREP qty: ${qty}`,
    (!groupedAllLots && totalQty > qty) ? `All LOTS total: ${totalQty}` : '',
    hasLotless ? 'Contains LOTLESS prep' : ''
  ].filter(Boolean).join(' | ');

  if(!breakdown.length){
    content.innerHTML = '<div class="small-muted">No prep LOT details found for this item.</div>';
    modal.style.display = 'flex';
    return;
  }

  const currentKey = String(currentLot || '').trim().toLowerCase();
  const rowsHtml = breakdown.map(entry => {
    const lotRaw = String(entry.lot_label || entry.lot_number || '').trim() || 'LOTLESS';
    const entryKey = String(entry.lot_number || '').trim().toLowerCase();
    const isCurrent = entryKey === currentKey;
    const isLotless = String(entry.is_lotless ? '1' : '0') === '1';
    const prepQty = Math.max(0, Number(entry.prep_quantity || 0) || 0);
    const lotQty = Math.max(0, Number(entry.lot_quantity || 0) || 0);
    const prepDate = formatEasternDateTime(entry.prep_updated_at || '');
    const importDate = formatDateOnly(entry.import_date || '');
    const status = String(entry.prep_status || '').trim().toLowerCase();
    const rowClass = [
      isCurrent ? 'prep-breakdown-current' : '',
      isLotless ? 'prep-breakdown-lotless' : ''
    ].filter(Boolean).join(' ');
    const statusBadge = status
      ? `<span class="prep-breakdown-status ${esc(status)}">${esc(status)}</span>`
      : '<span class="small-muted">-</span>';
    return `
      <tr class="${rowClass}">
        <td>
          <div class="prep-breakdown-lot-badge">
            <span>${esc(lotRaw)}</span>
            ${isCurrent ? '<span class="small-muted">(current)</span>' : ''}
          </div>
        </td>
        <td>${statusBadge}</td>
        <td>${esc(String(prepQty))}</td>
        <td>${esc(prepDate || '-')}</td>
        <td>${esc(importDate || '-')}</td>
        <td>${isLotless ? '<span class="small-muted">-</span>' : esc(String(lotQty))}</td>
      </tr>
    `;
  }).join('');

  content.innerHTML = `
    <table class="prep-breakdown-table">
      <thead>
        <tr>
          <th>LOT</th>
          <th>Status</th>
          <th>Prep Qty</th>
          <th>Prep Date</th>
          <th>Import Date</th>
          <th>Lot Qty</th>
        </tr>
      </thead>
      <tbody>${rowsHtml}</tbody>
    </table>
  `;
  modal.style.display = 'flex';
}
function renderLargeMediaHtml(item){
  if(!item) return '<div class="small-muted">No media selected</div>';
  const src = esc(item.src || '');
  const label = item.media_type === 'video' ? 'VIDEO' : 'PHOTO';
  const metaBits = [label];
  if(item.created_at) metaBits.push(formatEasternDateTime(item.created_at));
  if(item.media_type === 'video'){
    return `<div class="mixed-media-badge">${label}</div><video src="${src}" controls playsinline preload="metadata"></video><div class="small-muted">${esc(metaBits.join(' | '))}</div>`;
  }
  return `<div class="mixed-media-badge">${label}</div><img src="${src}" alt="${label}"><div class="small-muted">${esc(metaBits.join(' | '))}</div>`;
}

function inferNotesMediaFocusFromEvent(e){
  const target = e && e.target;
  if(target && target.closest){
    if(target.closest('.media-trigger-part.video')) return 'video';
    if(target.closest('.media-trigger-part')) return 'media';
    if(target.closest('.note-audio-flag')) return 'voice';
  }
  return 'notes';
}

function _notesMediaPreferredSection(focus, availability){
  const wants = String(focus || '').trim().toLowerCase();
  const hasNotes = !!availability.hasNotes;
  const hasVoice = !!availability.hasVoice;
  const hasMedia = !!availability.hasMedia;
  const hasVideo = !!availability.hasVideo;

  if(wants === 'video'){
    if(hasVideo) return 'media';
    if(hasMedia) return 'media';
    if(hasVoice) return 'voice';
    if(hasNotes) return 'notes';
    return '';
  }
  if(wants === 'media' || wants === 'photo'){
    if(hasMedia) return 'media';
    if(hasVoice) return 'voice';
    if(hasNotes) return 'notes';
    return '';
  }
  if(wants === 'voice'){
    if(hasVoice) return 'voice';
    if(hasNotes) return 'notes';
    if(hasMedia) return 'media';
    return '';
  }
  if(hasNotes) return 'notes';
  if(hasVoice) return 'voice';
  if(hasMedia) return 'media';
  return '';
}

function _notesMediaBuildMeta(upc, notesCount, audioCount, photoCount, videoCount){
  const bits = [];
  if(upc) bits.push(`UPC: ${upc}`);
  if(notesCount > 0) bits.push(`${notesCount} text note${notesCount===1?'':'s'}`);
  if(audioCount > 0) bits.push(`${audioCount} voice note${audioCount===1?'':'s'}`);
  if(photoCount > 0) bits.push(`${photoCount} photo${photoCount===1?'':'s'}`);
  if(videoCount > 0) bits.push(`${videoCount} video${videoCount===1?'':'s'}`);
  return bits.join(' | ');
}

const GEAR_SETTINGS_KEY = 'items_to_list_gear_settings_v1';
const FILTER_UI_SETTINGS_KEY = 'items_to_list_filter_ui_v1';
const GEAR_DEFAULTS = Object.freeze({
  status_good: true,
  status_bad: true,
  status_return: true,
  lot: '',
  sort: 'last_edited',
  defect: '',
  listed: '',
  auto_listed: '',
  warehouse: '',
  listed_amazon: true,
  listed_ebay: true,
  listed_facebook: true,
  auto_listed_amazon: false,
  auto_listed_ebay: false
});
let pendingLotSelection = '';
let selectMode = false;
let currentPage = 1;
let currentLimit = 25;
let lastRenderedResults = []; // Cache last results for re-rendering
let pendingMarketplaceSaves = 0;
let columnSort = { key: '', dir: 'desc' };
let fullColumnSortLoaded = false;
let fullColumnSortCacheKey = '';
let fullColumnSortResults = [];
let fullColumnSortStats = null;
let listingAgentQueueState = Object.create(null);
let listingAgentQueueLookupToken = 0;
const FULL_COLUMN_SORT_LIMIT = 50000;
const PENDING_MARKETPLACE_OPS_KEY = 'items_to_list_pending_marketplace_ops_v1';
const PENDING_MARKETPLACE_OP_MAX_AGE_MS = 10 * 60 * 1000;

function _isGlobalColumnSortActive(){
  return columnSort && ['quantity', 'warehouse_available'].includes(String(columnSort.key || '').trim());
}

function _clearGlobalColumnSortCache(){
  fullColumnSortLoaded = false;
  fullColumnSortCacheKey = '';
  fullColumnSortResults = [];
  fullColumnSortStats = null;
}

function _listagentQueueKey(rawValue){
  const raw = String(rawValue || '').trim();
  if(!raw) return '';
  // Preserve suffix (e.g. "123456789012-1") so listing agent SKU auto-fills with it.
  return raw;
}

function _listagentQueueUiState(queueItem){
  if(!queueItem){
    return {
      label: '+',
      title: 'Add to Listing Agent queue',
      disabled: false,
      active: false
    };
  }
  const status = String(queueItem.status || '').trim().toLowerCase();
  const hasListedState = !!(
    queueItem.listed_at ||
    queueItem.listed_platform ||
    queueItem.listed_ebay_at ||
    queueItem.listed_amazon_at ||
    queueItem.listed_listing_id ||
    queueItem.listed_offer_id ||
    queueItem.listed_sku ||
    queueItem.listed_asin ||
    queueItem.listed_url
  );
  if(hasListedState){
    const hasEbay = !!(queueItem.listed_ebay_at || (queueItem.listed_platform || '').toString().toLowerCase().includes('ebay'));
    const hasAmazon = !!(queueItem.listed_amazon_at || (queueItem.listed_platform || '').toString().toLowerCase().includes('amazon'));
    let label, title;
    if(hasEbay && hasAmazon){
      label = 'Listed (eBay/Amazon) ✓';
      title = 'Listed on eBay and Amazon via Listing Agent';
    } else if(hasEbay){
      label = 'Listed (eBay) ✓';
      title = 'Listed on eBay via Listing Agent';
    } else if(hasAmazon){
      label = 'Listed (Amazon) ✓';
      title = 'Listed on Amazon via Listing Agent';
    } else {
      label = 'Listed ✓';
      title = 'Listed through Listing Agent';
    }
    return { label, title, disabled: true, active: true };
  }
  if(status === 'done'){
    return {
      label: 'Listed ✓',
      title: 'Listed through Listing Agent',
      disabled: true,
      active: true
    };
  }
  return {
    label: 'In queue',
    title: 'In Listing Agent queue',
    disabled: true,
    active: true
  };
}

async function hydrateListingAgentQueueState(rows){
  const upcs = Array.from(new Set(
    (Array.isArray(rows) ? rows : [])
      .map(row => _listagentQueueKey(row && row.upc))
      .filter(Boolean)
  ));
  if(!upcs.length) return;

  const token = ++listingAgentQueueLookupToken;
  try{
    const resp = await fetch('/api/listingagent/queue/statuses', {
      method: 'POST',
      headers: {'Content-Type':'application/json'},
      body: JSON.stringify({upcs}),
      cache: 'no-store'
    });
    const j = await resp.json().catch(() => ({}));
    if(token !== listingAgentQueueLookupToken) return;
    if(!resp.ok || !j || !j.success){
      console.error('Failed to hydrate Listing Agent queue state', j && j.error ? j.error : ('HTTP ' + resp.status));
      return;
    }
    const items = (j.items && typeof j.items === 'object') ? j.items : {};
    upcs.forEach(upc => {
      listingAgentQueueState[upc] = items[upc] || null;
    });
  }catch(err){
    if(token !== listingAgentQueueLookupToken) return;
    console.error('Error hydrating Listing Agent queue state:', err);
  }
}

function _buildBolItemsQueryContext(){
  let lot = document.getElementById('filter-lot').value.trim();
  if(!lot && pendingLotSelection) lot = pendingLotSelection;
  const q = document.getElementById('filter-q').value.trim();
  const sort = document.getElementById('sort-select').value;
  const checkedStatuses = Array.from(document.querySelectorAll('.status-checkbox:checked')).map(cb => cb.value);
  const status = checkedStatuses.length > 0 ? checkedStatuses.join(',') : '';
  const defectFilter = (document.getElementById('filter-defect') && document.getElementById('filter-defect').value) || '';
  const listedVal = document.getElementById('filter-listed-select').value;
  const autoListedVal = (document.getElementById('filter-auto-listed-select') && document.getElementById('filter-auto-listed-select').value) || '';
  const warehouseFilterVal = (document.getElementById('filter-warehouse-select') && document.getElementById('filter-warehouse-select').value) || '';

  const listedStores = [];
  const amazonCb = document.getElementById('filter-listed-amazon');
  const ebayCb = document.getElementById('filter-listed-ebay');
  const fbCb = document.getElementById('filter-listed-fb');
  if (amazonCb && amazonCb.checked) listedStores.push('amazon');
  if (ebayCb && ebayCb.checked) listedStores.push('ebay');
  if (fbCb && fbCb.checked) listedStores.push('facebook');
  const autoListedStores = [];
  const autoAmazonCb = document.getElementById('filter-auto-listed-amazon');
  const autoEbayCb = document.getElementById('filter-auto-listed-ebay');
  if (autoAmazonCb && autoAmazonCb.checked) autoListedStores.push('amazon');
  if (autoEbayCb && autoEbayCb.checked) autoListedStores.push('ebay');

  const params = new URLSearchParams();
  if(lot) params.set('lot', lot);
  if(q) params.set('q', q);
  if(sort) params.set('sort', sort); else params.set('sort', 'last_edited');
  if(status) params.set('status', status);
  if(listedVal === 'listed') params.set('listed', 'true');
  else if(listedVal === 'not_listed') params.set('not_listed', 'true');
  if(autoListedVal === 'listed') params.set('auto_listed', 'true');
  else if(autoListedVal === 'not_listed') params.set('auto_not_listed', 'true');
  if(['with_quantity', 'without_quantity', 'multiple_locations'].includes(warehouseFilterVal)){
    params.set('warehouse_filter', warehouseFilterVal);
  }
  let listedStoresKey = 'all';
  if (listedStores.length > 0 && listedStores.length < 3) {
    params.set('listed_stores', listedStores.join(','));
    listedStoresKey = listedStores.slice().sort().join(',');
  }
  let autoListedStoresKey = 'all';
  if (autoListedStores.length > 0 && autoListedStores.length < 2) {
    params.set('auto_listed_stores', autoListedStores.join(','));
    autoListedStoresKey = autoListedStores.slice().sort().join(',');
  }
  if(defectFilter) params.set('defect', defectFilter);

  const cacheKey = JSON.stringify({
    lot,
    q,
    sort: sort || 'last_edited',
    status,
    defect: defectFilter,
    listed: listedVal || '',
    auto_listed: autoListedVal || '',
    warehouse_filter: warehouseFilterVal || '',
    listed_stores: listedStoresKey,
    auto_listed_stores: autoListedStoresKey
  });

  return {
    params,
    cacheKey
  };
}

function _safeJsonParse(raw, fallback){
  try{ return JSON.parse(raw); }catch(e){ return fallback; }
}

function _readPendingMarketplaceOps(){
  try{
    const raw = localStorage.getItem(PENDING_MARKETPLACE_OPS_KEY);
    const parsed = _safeJsonParse(raw, []);
    const list = Array.isArray(parsed) ? parsed : [];
    const now = Date.now();
    const cleaned = list.filter(op => {
      if(!op || typeof op !== 'object') return false;
      if(!op.key || !op.payload) return false;
      if((now - Number(op.created_at || 0)) > PENDING_MARKETPLACE_OP_MAX_AGE_MS) return false;
      return _isValidPendingMarketplacePayload(op.payload);
    });
    if(cleaned.length !== list.length){
      _writePendingMarketplaceOps(cleaned);
    }
    return cleaned;
  }catch(e){
    return [];
  }
}

function _writePendingMarketplaceOps(ops){
  try{
    localStorage.setItem(PENDING_MARKETPLACE_OPS_KEY, JSON.stringify(Array.isArray(ops) ? ops : []));
  }catch(e){}
}

function _marketplaceOpKey(payload){
  const id = payload && payload.id ? String(payload.id) : '';
  const upc = payload && payload.upc ? String(payload.upc) : '';
  const lot = payload && payload.lot_number ? String(payload.lot_number) : '';
  const marketplace = payload && payload.marketplace ? String(payload.marketplace) : '';
  return `${id}|${upc}|${lot}|${marketplace}`;
}

function _isValidPendingMarketplacePayload(payload){
  if(!payload || typeof payload !== 'object') return false;
  const id = Number.parseInt(payload.id, 10);
  if(!Number.isFinite(id) || id <= 0) return false;
  const upc = String(payload.upc || '').trim();
  const marketplace = String(payload.marketplace || '').trim().toLowerCase();
  if(!upc) return false;
  if(!['amazon', 'ebay', 'facebook'].includes(marketplace)) return false;
  if(typeof payload.listed !== 'boolean') return false;
  return true;
}

function _queuePendingMarketplaceOp(payload){
  // Manual-only mode: do not enqueue deferred marketplace operations.
  return '';
}

function _clearPendingMarketplaceOp(key){
  // Manual-only mode: no-op.
  return;
}

function _markMarketplaceSaveStarted(){
  // Manual-only mode: no-op.
  return;
}

function _markMarketplaceSaveFinished(){
  // Manual-only mode: no-op.
  return;
}

async function flushPendingMarketplaceOps(){
  // Manual-only mode: clear any stale deferred operations from older builds.
  _writePendingMarketplaceOps([]);
}

function getCurrentFilterUiSettings(){
  const qEl = document.getElementById('filter-q');
  const perPageEl = document.getElementById('per-page-select');
  const perPageVal = perPageEl ? parseInt(perPageEl.value, 10) : currentLimit;
  const validPerPage = [25, 50, 100].includes(perPageVal) ? perPageVal : currentLimit;
  return {
    q: qEl ? (qEl.value || '') : '',
    per_page: validPerPage
  };
}

function saveFilterUiSettings(){
  try{
    localStorage.setItem(FILTER_UI_SETTINGS_KEY, JSON.stringify(getCurrentFilterUiSettings()));
  }catch(e){}
}

function restoreFilterUiSettings(){
  try{
    const raw = localStorage.getItem(FILTER_UI_SETTINGS_KEY);
    if(!raw) return;
    const parsed = JSON.parse(raw) || {};
    const qEl = document.getElementById('filter-q');
    if(qEl && typeof parsed.q === 'string'){
      qEl.value = parsed.q;
    }
    const parsedPerPage = parseInt(parsed.per_page, 10);
    if([25, 50, 100].includes(parsedPerPage)){
      currentLimit = parsedPerPage;
      const perPageEl = document.getElementById('per-page-select');
      if(perPageEl) perPageEl.value = String(parsedPerPage);
    }
  }catch(e){}
}

function normalizeGearSettings(raw){
  const src = (raw && typeof raw === 'object') ? raw : {};
  const parseBool = (v, fallback) => {
    if (typeof v === 'boolean') return v;
    if (v === 1 || v === '1' || v === 'true') return true;
    if (v === 0 || v === '0' || v === 'false') return false;
    return fallback;
  };
  const allowedSorts = new Set(['last_edited', 'last_edited_asc', 'date_desc', 'date_asc', 'name']);
  const sortRaw = String(src.sort || GEAR_DEFAULTS.sort);
  const listedRaw = String(src.listed || '');
  const autoListedRaw = String(src.auto_listed || '');
  const warehouseRaw = String(src.warehouse || '');
  return {
    status_good: parseBool(src.status_good, GEAR_DEFAULTS.status_good),
    status_bad: parseBool(src.status_bad, GEAR_DEFAULTS.status_bad),
    status_return: parseBool(src.status_return, GEAR_DEFAULTS.status_return),
    lot: String(src.lot || ''),
    sort: allowedSorts.has(sortRaw) ? sortRaw : GEAR_DEFAULTS.sort,
    defect: String(src.defect || ''),
    listed: (listedRaw === 'listed' || listedRaw === 'not_listed') ? listedRaw : '',
    auto_listed: (autoListedRaw === 'listed' || autoListedRaw === 'not_listed') ? autoListedRaw : '',
    warehouse: ['with_quantity', 'without_quantity', 'multiple_locations'].includes(warehouseRaw) ? warehouseRaw : '',
    listed_amazon: parseBool(src.listed_amazon, GEAR_DEFAULTS.listed_amazon),
    listed_ebay: parseBool(src.listed_ebay, GEAR_DEFAULTS.listed_ebay),
    listed_facebook: parseBool(src.listed_facebook, GEAR_DEFAULTS.listed_facebook),
    auto_listed_amazon: parseBool(src.auto_listed_amazon, GEAR_DEFAULTS.auto_listed_amazon),
    auto_listed_ebay: parseBool(src.auto_listed_ebay, GEAR_DEFAULTS.auto_listed_ebay)
  };
}

function getCurrentGearSettings(){
  const statusGoodCb = document.querySelector('.status-checkbox[value="good"]');
  const statusBadCb = document.querySelector('.status-checkbox[value="bad"]');
  const statusReturnCb = document.querySelector('.status-checkbox[value="return"]');
  const lotEl = document.getElementById('filter-lot');
  const sortEl = document.getElementById('sort-select');
  const defectEl = document.getElementById('filter-defect');
  const listedEl = document.getElementById('filter-listed-select');
  const autoListedEl = document.getElementById('filter-auto-listed-select');
  const warehouseEl = document.getElementById('filter-warehouse-select');
  const amazonCb = document.getElementById('filter-listed-amazon');
  const ebayCb = document.getElementById('filter-listed-ebay');
  const fbCb = document.getElementById('filter-listed-fb');
  const autoAmazonCb = document.getElementById('filter-auto-listed-amazon');
  const autoEbayCb = document.getElementById('filter-auto-listed-ebay');
  return normalizeGearSettings({
    status_good: !!(statusGoodCb && statusGoodCb.checked),
    status_bad: !!(statusBadCb && statusBadCb.checked),
    status_return: !!(statusReturnCb && statusReturnCb.checked),
    lot: lotEl ? lotEl.value : '',
    sort: sortEl ? sortEl.value : GEAR_DEFAULTS.sort,
    defect: defectEl ? defectEl.value : '',
    listed: listedEl ? listedEl.value : '',
    auto_listed: autoListedEl ? autoListedEl.value : '',
    warehouse: warehouseEl ? warehouseEl.value : '',
    listed_amazon: !!(amazonCb && amazonCb.checked),
    listed_ebay: !!(ebayCb && ebayCb.checked),
    listed_facebook: !!(fbCb && fbCb.checked),
    auto_listed_amazon: !!(autoAmazonCb && autoAmazonCb.checked),
    auto_listed_ebay: !!(autoEbayCb && autoEbayCb.checked)
  });
}

function applyGearSettings(raw){
  const settings = normalizeGearSettings(raw);
  const statusGoodCb = document.querySelector('.status-checkbox[value="good"]');
  const statusBadCb = document.querySelector('.status-checkbox[value="bad"]');
  const statusReturnCb = document.querySelector('.status-checkbox[value="return"]');
  const lotEl = document.getElementById('filter-lot');
  const sortEl = document.getElementById('sort-select');
  const defectEl = document.getElementById('filter-defect');
  const listedEl = document.getElementById('filter-listed-select');
  const autoListedEl = document.getElementById('filter-auto-listed-select');
  const warehouseEl = document.getElementById('filter-warehouse-select');
  const amazonCb = document.getElementById('filter-listed-amazon');
  const ebayCb = document.getElementById('filter-listed-ebay');
  const fbCb = document.getElementById('filter-listed-fb');
  const autoAmazonCb = document.getElementById('filter-auto-listed-amazon');
  const autoEbayCb = document.getElementById('filter-auto-listed-ebay');

  if (statusGoodCb) statusGoodCb.checked = settings.status_good;
  if (statusBadCb) statusBadCb.checked = settings.status_bad;
  if (statusReturnCb) statusReturnCb.checked = settings.status_return;
  if (lotEl) {
    pendingLotSelection = settings.lot || '';
    lotEl.value = settings.lot || '';
  }
  if (sortEl) sortEl.value = settings.sort;
  if (defectEl) defectEl.value = settings.defect;
  if (listedEl) listedEl.value = settings.listed;
  if (autoListedEl) autoListedEl.value = settings.auto_listed;
  if (warehouseEl) warehouseEl.value = settings.warehouse;
  if (amazonCb) amazonCb.checked = settings.listed_amazon;
  if (ebayCb) ebayCb.checked = settings.listed_ebay;
  if (fbCb) fbCb.checked = settings.listed_facebook;
  if (autoAmazonCb) autoAmazonCb.checked = settings.auto_listed_amazon;
  if (autoEbayCb) autoEbayCb.checked = settings.auto_listed_ebay;
  updateStatusFilterHighlight();
  updateMarketplaceFilterHighlight();
  updateAutoMarketplaceFilterHighlight();
}

function saveGearSettings(){
  try{
    localStorage.setItem(GEAR_SETTINGS_KEY, JSON.stringify(getCurrentGearSettings()));
  }catch(e){}
}

function restoreGearSettings(){
  try{
    const raw = localStorage.getItem(GEAR_SETTINGS_KEY);
    if(!raw) return;
    const parsed = JSON.parse(raw);
    applyGearSettings(parsed);
  }catch(e){}
}

function gearSettingsDifferFromDefaults(raw){
  const settings = normalizeGearSettings(raw);
  return (
    settings.status_good !== GEAR_DEFAULTS.status_good ||
    settings.status_bad !== GEAR_DEFAULTS.status_bad ||
    settings.status_return !== GEAR_DEFAULTS.status_return ||
    settings.lot !== GEAR_DEFAULTS.lot ||
    settings.sort !== GEAR_DEFAULTS.sort ||
    settings.defect !== GEAR_DEFAULTS.defect ||
    settings.listed !== GEAR_DEFAULTS.listed ||
    settings.auto_listed !== GEAR_DEFAULTS.auto_listed ||
    settings.warehouse !== GEAR_DEFAULTS.warehouse ||
    settings.listed_amazon !== GEAR_DEFAULTS.listed_amazon ||
    settings.listed_ebay !== GEAR_DEFAULTS.listed_ebay ||
    settings.listed_facebook !== GEAR_DEFAULTS.listed_facebook ||
    settings.auto_listed_amazon !== GEAR_DEFAULTS.auto_listed_amazon ||
    settings.auto_listed_ebay !== GEAR_DEFAULTS.auto_listed_ebay
  );
}

function updateGearResetButton(){
  const wrap = document.getElementById('gear-reset-wrap');
  if(!wrap) return;
  wrap.style.display = gearSettingsDifferFromDefaults(getCurrentGearSettings()) ? 'block' : 'none';
}

function onGearSettingChanged(){
  const lotEl = document.getElementById('filter-lot');
  if(lotEl) pendingLotSelection = lotEl.value || '';
  saveGearSettings();
  updateGearResetButton();
}

function _isTruthyParamValue(v){
  const value = String(v || '').trim().toLowerCase();
  return ['1', 'true', 'yes', 'y', 'on'].includes(value);
}

function isDirectSearchActivated(params){
  try{
    const p = (params instanceof URLSearchParams) ? params : new URLSearchParams(window.location.search);
    return _isTruthyParamValue(p.get('direct_search'));
  }catch(e){
    return false;
  }
}

function applyDirectSearchFromURL(params){
  const p = (params instanceof URLSearchParams) ? params : new URLSearchParams(window.location.search);
  const q = (p.get('q') || '').trim();
  const qEl = document.getElementById('filter-q');
  if(qEl) qEl.value = q;
  pendingLotSelection = '';
  applyGearSettings(GEAR_DEFAULTS);
  updateGearResetButton();
}

function setFiltersFromURL(params){
  const p = (params instanceof URLSearchParams) ? params : new URLSearchParams(window.location.search);
  const lot = p.get('lot')||''; const q = p.get('q')||''; const sort = p.get('sort')||''; const status = p.get('status')||''; const defect = p.get('defect')||'';
  if(p.has('q')) document.getElementById('filter-q').value = q;
  if(p.has('lot')){
    pendingLotSelection = lot;
    document.getElementById('filter-lot').value = lot;
  }
  if(p.has('sort') && sort) document.getElementById('sort-select').value = sort;
  // If status is explicitly set in URL, use it; otherwise keep default status mix
  if(status && status !== 'listed' && status !== 'not_listed') {
    // Uncheck all first
    document.querySelectorAll('.status-checkbox').forEach(cb => cb.checked = false);
    // Check the ones in the URL (comma-separated)
    const statuses = status.split(',').map(s => s.trim());
    statuses.forEach(s => {
      const cb = document.querySelector(`.status-checkbox[value="${s}"]`);
      if(cb) cb.checked = true;
    });
  }
  updateStatusFilterHighlight();
  if(p.has('defect')) document.getElementById('filter-defect').value = defect;
  const listedFlag = (p.get('listed') === 'true') || (p.get('status') === 'listed');
  const notListedFlag = (p.get('not_listed') === 'true') || (p.get('status') === 'not_listed');
  const hasListedFilterParam = p.has('listed') || p.has('not_listed') || (p.get('status') === 'listed') || (p.get('status') === 'not_listed');
  if(hasListedFilterParam){
    if(listedFlag){ document.getElementById('filter-listed-select').value = 'listed'; }
    else if(notListedFlag){ document.getElementById('filter-listed-select').value = 'not_listed'; }
    else { document.getElementById('filter-listed-select').value = ''; }
  }
  const autoListedFlag = p.get('auto_listed') === 'true';
  const autoNotListedFlag = p.get('auto_not_listed') === 'true';
  const hasAutoListedParam = p.has('auto_listed') || p.has('auto_not_listed');
  if(hasAutoListedParam){
    const autoListedEl = document.getElementById('filter-auto-listed-select');
    if(autoListedEl){
      if(autoListedFlag) autoListedEl.value = 'listed';
      else if(autoNotListedFlag) autoListedEl.value = 'not_listed';
      else autoListedEl.value = '';
    }
  }
  if (p.has('auto_listed_stores')) {
    const stores = (p.get('auto_listed_stores') || '').split(',').map(s => s.trim()).filter(Boolean);
    const autoAmazonCb = document.getElementById('filter-auto-listed-amazon');
    const autoEbayCb = document.getElementById('filter-auto-listed-ebay');
    if (stores.length === 0) {
      if (autoAmazonCb) autoAmazonCb.checked = true;
      if (autoEbayCb) autoEbayCb.checked = true;
    } else {
      if (autoAmazonCb) autoAmazonCb.checked = stores.includes('amazon');
      if (autoEbayCb) autoEbayCb.checked = stores.includes('ebay');
    }
  }
  if(p.has('warehouse_filter')){
    const rawWarehouseVal = (p.get('warehouse_filter') || '').trim();
    const warehouseVal = rawWarehouseVal === 'multi_locations' ? 'multiple_locations' : rawWarehouseVal;
    const warehouseEl = document.getElementById('filter-warehouse-select');
    if(warehouseEl && ['with_quantity', 'without_quantity', 'multiple_locations'].includes(warehouseVal)){
      warehouseEl.value = warehouseVal;
    }
  }

  // Marketplace filter (amazon, ebay, facebook)
  if (p.has('listed_stores')) {
    const stores = (p.get('listed_stores') || '').split(',').map(s => s.trim()).filter(Boolean);
    const amazonCb = document.getElementById('filter-listed-amazon');
    const ebayCb = document.getElementById('filter-listed-ebay');
    const fbCb = document.getElementById('filter-listed-fb');
    if (stores.length === 0) {
      if (amazonCb) amazonCb.checked = true;
      if (ebayCb) ebayCb.checked = true;
      if (fbCb) fbCb.checked = true;
    } else {
      if (amazonCb) amazonCb.checked = stores.includes('amazon');
      if (ebayCb) ebayCb.checked = stores.includes('ebay');
      if (fbCb) fbCb.checked = stores.includes('facebook');
    }
  }
  updateMarketplaceFilterHighlight();
  updateAutoMarketplaceFilterHighlight();
  updateGearResetButton();
}

function _normalizeItemsToListPage(value, fallback = 1){
  const parsed = parseInt(value, 10);
  return Number.isFinite(parsed) && parsed > 0 ? parsed : fallback;
}

function _normalizeItemsToListLimit(value, fallback = 25){
  const parsed = parseInt(value, 10);
  return [25, 50, 100].includes(parsed) ? parsed : fallback;
}

function _setListPagingAndSortFromURL(params){
  const p = (params instanceof URLSearchParams) ? params : new URLSearchParams(window.location.search);
  currentPage = _normalizeItemsToListPage(p.get('page'), 1);
  currentLimit = _normalizeItemsToListLimit(p.get('limit') || p.get('per_page'), currentLimit || 25);
  const perPageEl = document.getElementById('per-page-select');
  if(perPageEl) perPageEl.value = String(currentLimit);

  const sortKey = String(p.get('column_sort_key') || '').trim();
  const sortDir = String(p.get('column_sort_dir') || '').trim().toLowerCase();
  if(['quantity', 'warehouse_available'].includes(sortKey)){
    columnSort.key = sortKey;
    columnSort.dir = sortDir === 'asc' ? 'asc' : 'desc';
  } else {
    columnSort.key = '';
    columnSort.dir = 'desc';
  }
}

function buildCurrentItemsToListUrl(){
  const context = _buildBolItemsQueryContext();
  const params = new URLSearchParams(context.params.toString());
  params.set('page', String(_normalizeItemsToListPage(currentPage, 1)));
  params.set('limit', String(_normalizeItemsToListLimit(currentLimit, 25)));
  if(columnSort && ['quantity', 'warehouse_available'].includes(String(columnSort.key || '').trim())){
    params.set('column_sort_key', String(columnSort.key).trim());
    params.set('column_sort_dir', columnSort.dir === 'asc' ? 'asc' : 'desc');
  } else {
    params.delete('column_sort_key');
    params.delete('column_sort_dir');
  }
  if(isDirectSearchActivated()){
    params.set('direct_search', '1');
  } else {
    params.delete('direct_search');
  }
  const query = params.toString();
  return '/items-to-list' + (query ? ('?' + query) : '');
}

function updateListingAgentReturnLink(returnUrl){
  const link = document.getElementById('listing-agent-link');
  if(!link) return;
  const exactReturn = returnUrl || buildCurrentItemsToListUrl();
  link.href = '/listingagent?return=' + encodeURIComponent(exactReturn);
}

function syncItemsToListHistory(mode = 'replace'){
  if(mode === 'skip') return;
  const nextUrl = buildCurrentItemsToListUrl();
  updateListingAgentReturnLink(nextUrl);
  try{
    const currentUrl = window.location.pathname + window.location.search;
    if(mode === 'push'){
      const currentState = Object.assign({}, window.history.state || {});
      currentState.scrollY = window.scrollY;
      window.history.replaceState(currentState, '', currentUrl);
      if(currentUrl !== nextUrl){
        window.history.pushState({ url: nextUrl, scrollY: window.scrollY }, '', nextUrl);
      } else {
        window.history.replaceState({ url: nextUrl, scrollY: window.scrollY }, '', nextUrl);
      }
      return;
    }
    window.history.replaceState({ url: nextUrl, scrollY: window.scrollY }, '', nextUrl);
  }catch(e){
    updateListingAgentReturnLink(nextUrl);
  }
}

function restoreItemsToListStateFromURL(params){
  const p = (params instanceof URLSearchParams) ? params : new URLSearchParams(window.location.search);
  const directSearch = isDirectSearchActivated(p);

  restoreFilterUiSettings();
  if(directSearch){
    applyDirectSearchFromURL(p);
  } else {
    restoreGearSettings();
    setFiltersFromURL(p);
  }
  _setListPagingAndSortFromURL(p);
  updateListingAgentReturnLink();
  updateGearResetButton();
  return directSearch;
}

// Format a timestamp into Eastern Time like: 2025-10-18 9:20am
function formatLastEdited(raw){
  if(!raw) return '';
  function parseISO(s){
    if(!s) return new Date(NaN);
    try{
      let str = String(s).trim();
      if(/\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}/.test(str) && !(/[zZ]|[+-]\d{2}:?\d{2}$/.test(str))){
        str = str.replace(' ', 'T');
        if(!str.endsWith('Z')) str = str + 'Z';
      }
      return new Date(str);
    }catch(e){ return new Date(NaN); }
  }
  let dt = parseISO(raw);
  if(isNaN(dt.getTime())){
    try{ dt = parseISO(String(raw).replace(' ', 'T')); }catch(e){ return raw; }
    if(isNaN(dt.getTime())) return raw;
  }
  try{
    const parts = new Intl.DateTimeFormat('en-US', {
      timeZone: 'America/New_York',
      year: 'numeric', month: '2-digit', day: '2-digit',
      hour: 'numeric', minute: '2-digit', hour12: true
    }).formatToParts(dt);
    const vals = {};
    parts.forEach(p=> vals[p.type] = p.value);
    const year = vals.year || dt.getFullYear();
    const month = vals.month || String(dt.getMonth()+1).padStart(2,'0');
    const day = vals.day || String(dt.getDate()).padStart(2,'0');
    let hour = vals.hour || dt.getHours();
    let minute = vals.minute || String(dt.getMinutes()).padStart(2,'0');
    const dayPeriod = (vals.dayPeriod || '').toLowerCase();
    hour = String(hour).replace(/^0/, '');
    return `${year}-${month}-${day} ${hour}:${minute}${dayPeriod}`;
  }catch(e){
    return raw;
  }
}

function _parseSortableNumber(value){
  const n = Number(value);
  return Number.isFinite(n) ? n : 0;
}

function _sortIndicator(key){
  if(columnSort.key !== key) return '';
  return columnSort.dir === 'asc' ? '▲' : '▼';
}

function _sortedRenderResults(results){
  const src = Array.isArray(results) ? [...results] : [];
  if(!columnSort.key) return src;
  const key = columnSort.key;
  const dirMult = columnSort.dir === 'asc' ? 1 : -1;
  src.sort((a, b) => {
    const av = _parseSortableNumber(a && a[key]);
    const bv = _parseSortableNumber(b && b[key]);
    if(av !== bv) return (av - bv) * dirMult;
    const at = String((a && a.title) || '').toLowerCase();
    const bt = String((b && b.title) || '').toLowerCase();
    return at.localeCompare(bt);
  });
  return src;
}

function toggleColumnSort(key){
  const sortKey = String(key || '').trim();
  if(!['quantity', 'warehouse_available'].includes(sortKey)) return;
  if(columnSort.key === sortKey){
    columnSort.dir = columnSort.dir === 'asc' ? 'desc' : 'asc';
  } else {
    columnSort.key = sortKey;
    columnSort.dir = 'desc';
  }
  currentPage = 1;
  loadItems(null, { historyMode: 'push' });
}

function _locationImageCandidates(location){
  const raw = String(location || '').trim();
  if(!raw) return [];
  const candidates = [];
  const compact = raw.replace(/\s+/g, '');
  const lower = raw.toLowerCase();
  const compactLower = compact.toLowerCase();
  const compactUpper = compact.toUpperCase();
  const add = (src) => {
    if(!src) return;
    if(!candidates.includes(src)) candidates.push(src);
  };

  add('/shelf-image/' + encodeURIComponent(raw) + '.png');
  add('/shelf-image/' + encodeURIComponent(compact) + '.png');
  add('/shelf-image/' + encodeURIComponent(lower) + '.png');
  add('/shelf-image/' + encodeURIComponent(compactLower) + '.png');
  add('/shelf-image/' + encodeURIComponent(compactUpper) + '.png');

  add('/static/pictureposition/' + encodeURIComponent(raw));
  add('/static/pictureposition/' + encodeURIComponent(compact));
  add('/static/pictureposition/' + encodeURIComponent(raw) + '.jpg');
  add('/static/pictureposition/' + encodeURIComponent(raw) + '.png');
  add('/static/pictureposition/' + encodeURIComponent(compact) + '.jpg');
  add('/static/pictureposition/' + encodeURIComponent(compact) + '.png');

  const bust = Date.now();
  return candidates.map(src => `${src}${src.includes('?') ? '&' : '?'}t=${bust}`);
}

function _normalizeWarehouseLocationDetails(rawDetails){
  const map = new Map();
  const addRow = (rawLoc, rawQty) => {
    const location = String(rawLoc || '').trim();
    if(!location) return;
    let quantity = Number(rawQty);
    if(!Number.isFinite(quantity)) quantity = 0;
    quantity = Math.max(0, Math.floor(quantity));
    const current = map.get(location);
    if(current){
      current.quantity = Math.max(0, Number(current.quantity || 0) + quantity);
      return;
    }
    map.set(location, { location, quantity });
  };

  if(Array.isArray(rawDetails)){
    rawDetails.forEach(entry => {
      if(entry && typeof entry === 'object' && !Array.isArray(entry)){
        addRow(entry.location || entry.code, entry.quantity ?? entry.qty ?? 0);
      } else {
        addRow(entry, 0);
      }
    });
  }

  return Array.from(map.values()).sort((a, b) => {
    if(a.quantity !== b.quantity) return b.quantity - a.quantity;
    return String(a.location).localeCompare(String(b.location), undefined, { sensitivity: 'base' });
  });
}

function _decodeWarehouseLocationDetails(encodedPayload){
  if(!encodedPayload) return [];
  try{
    const raw = decodeURIComponent(encodedPayload);
    const parsed = JSON.parse(raw || '[]');
    return _normalizeWarehouseLocationDetails(parsed);
  }catch(err){
    return [];
  }
}

function _warehouseRenderInlinePreview(location, quantity){
  const loc = String(location || '').trim();
  const meta = document.getElementById('warehousePreviewMeta');
  const pane = document.getElementById('warehousePreviewPane');
  if(!loc || !meta || !pane) return;

  pane.classList.remove('expanded');
  const qtyLabel = Number.isFinite(Number(quantity)) ? `Qty: ${Math.max(0, Math.floor(Number(quantity || 0)))}` : '';
  meta.textContent = [ `Location: ${loc}`, qtyLabel, 'Click image to expand, click away to retract' ].filter(Boolean).join(' | ');

  const candidates = _locationImageCandidates(loc);
  if(!candidates.length){
    pane.innerHTML = '<div class="small-muted">No location picture found.</div>';
    return;
  }

  pane.innerHTML = '<div class="small-muted">Loading location image...</div>';

  const img = document.createElement('img');
  img.id = 'warehousePreviewImage';
  img.alt = 'Location image';

  let idx = 0;
  const tryNext = () => {
    if(idx >= candidates.length){
      pane.innerHTML = '<div class="small-muted">No location picture found.</div>';
      return;
    }
    img.src = candidates[idx++];
  };

  img.onerror = tryNext;
  img.onload = () => {
    pane.innerHTML = '';
    pane.appendChild(img);
  };
  img.addEventListener('click', (ev) => {
    ev.preventDefault();
    ev.stopPropagation();
    if (window.SSLocationPreview) {
      window.SSLocationPreview.openForLocation(loc, '', `Location: ${loc}`);
      return;
    }
    pane.classList.toggle('expanded');
  });

  tryNext();
}

function onOpenWarehouseDetails(e){
  e.preventDefault();
  const btn = e.currentTarget;
  if(!btn) return;

  const baseUpc = String(btn.dataset.baseUpc || '').trim();
  const locationDetails = _decodeWarehouseLocationDetails(btn.dataset.locations || '');

  const modal = document.getElementById('warehouseModal');
  const meta = document.getElementById('warehouseModalMeta');
  const body = document.getElementById('warehouseModalContent');
  if(!modal || !body || !meta) return;

  meta.textContent = baseUpc ? `Base UPC: ${baseUpc}` : '';
  if(!locationDetails.length){
    body.innerHTML = '<div class="small-muted">No item locations found in warehouse rows.</div>';
  } else {
    body.innerHTML = `
      <div class="warehouse-loc-list">
        ${locationDetails.map(row => `
          <button type="button" class="warehouse-loc-btn" data-location="${encodeURIComponent(row.location)}" data-qty="${row.quantity}">
            ${esc(row.location)}
            <span class="warehouse-loc-qty">${esc(row.quantity)}</span>
          </button>
        `).join('')}
      </div>
      <div class="warehouse-preview-wrap">
        <div id="warehousePreviewMeta" class="small-muted warehouse-preview-meta">Select a location bubble to preview image.</div>
        <div id="warehousePreviewPane" class="warehouse-preview-pane"><div class="small-muted">Select a location bubble above.</div></div>
      </div>
    `;

    body.querySelectorAll('.warehouse-loc-btn').forEach(el => {
      el.addEventListener('click', (ev) => {
        ev.preventDefault();
        body.querySelectorAll('.warehouse-loc-btn.active').forEach(activeBtn => activeBtn.classList.remove('active'));
        el.classList.add('active');

        const encoded = el.dataset.location || '';
        const qty = Number(el.dataset.qty || 0);
        let loc = '';
        try{ loc = decodeURIComponent(encoded); }catch(_){ loc = encoded; }
        _warehouseRenderInlinePreview(loc, qty);
      });
    });

    const firstBtn = body.querySelector('.warehouse-loc-btn');
    if(firstBtn){
      firstBtn.classList.add('active');
      const encoded = firstBtn.dataset.location || '';
      const qty = Number(firstBtn.dataset.qty || 0);
      let loc = '';
      try{ loc = decodeURIComponent(encoded); }catch(_){ loc = encoded; }
      _warehouseRenderInlinePreview(loc, qty);
    }
  }

  modal.style.display = 'flex';
}

function _decodeAutoListings(encodedPayload){
  if(!encodedPayload) return [];
  try{
    const raw = decodeURIComponent(encodedPayload);
    const parsed = JSON.parse(raw || '[]');
    if(!Array.isArray(parsed)) return [];
    return parsed
      .filter(entry => entry && typeof entry === 'object')
      .map(entry => ({
        platform: String(entry.platform || '').trim().toLowerCase(),
        listing_id: String(entry.listing_id || '').trim(),
        title: String(entry.title || '').trim(),
        url: String(entry.url || '').trim()
      }))
      .filter(entry => ['ebay', 'amazon'].includes(entry.platform) && (entry.url || entry.listing_id));
  }catch(err){
    return [];
  }
}

function onOpenAutoListings(e){
  e.preventDefault();
  const btn = e.currentTarget;
  if(!btn) return;

  const upc = String(btn.dataset.upc || '').trim();
  const ebayCount = Number(btn.dataset.ebayCount || 0);
  const amazonCount = Number(btn.dataset.amazonCount || 0);
  const listings = _decodeAutoListings(btn.dataset.listings || '');

  const modal = document.getElementById('autoListingsModal');
  const meta = document.getElementById('autoListingsMeta');
  const body = document.getElementById('autoListingsContent');
  if(!modal || !meta || !body) return;

  const groups = { ebay: [], amazon: [] };
  listings.forEach(entry => {
    if(groups[entry.platform]) groups[entry.platform].push(entry);
  });

  const total = groups.ebay.length + groups.amazon.length;
  meta.textContent = [upc ? `UPC: ${upc}` : '', `${total} listing${total === 1 ? '' : 's'} detected`].filter(Boolean).join(' | ');

  body.innerHTML = '';
  if(total <= 0){
    body.innerHTML = '<div class="small-muted">No live listing links detected for this row.</div>';
    modal.style.display = 'flex';
    return;
  }

  const sectionTitle = (label, count, badgeClass) => {
    const wrap = document.createElement('div');
    wrap.style.marginBottom = '8px';
    const badge = document.createElement('span');
    badge.className = `auto-listings-meta-badge ${badgeClass}`;
    badge.textContent = `${label}: ${count}`;
    wrap.appendChild(badge);
    return wrap;
  };

  const renderRows = (platform, rows) => {
    if(!rows || rows.length === 0) return;
    const label = platform === 'ebay' ? 'eBay' : 'Amazon';
    const badgeClass = platform === 'ebay' ? 'auto-badge-ebay' : 'auto-badge-amazon';
    body.appendChild(sectionTitle(label, rows.length, badgeClass));
    rows.forEach((entry, idx) => {
      const row = document.createElement('div');
      row.className = 'auto-listings-row';

      const platformEl = document.createElement('div');
      platformEl.className = 'auto-listings-platform';
      platformEl.textContent = label;
      row.appendChild(platformEl);

      const main = document.createElement('div');
      main.style.flex = '1';
      const titleText = entry.title || entry.listing_id || `${label} listing ${idx + 1}`;
      const titleLine = document.createElement('div');
      titleLine.style.marginBottom = '4px';
      titleLine.textContent = titleText;
      main.appendChild(titleLine);

      if(entry.url){
        const link = document.createElement('a');
        link.className = 'auto-listings-link';
        link.href = entry.url;
        link.target = '_blank';
        link.rel = 'noopener noreferrer';
        link.textContent = entry.url;
        main.appendChild(link);
      } else {
        const noLink = document.createElement('div');
        noLink.className = 'small-muted';
        noLink.textContent = 'No direct URL available';
        main.appendChild(noLink);
      }

      if(entry.listing_id){
        const idLine = document.createElement('div');
        idLine.className = 'small-muted';
        idLine.style.marginTop = '4px';
        idLine.textContent = `ID: ${entry.listing_id}`;
        main.appendChild(idLine);
      }

      row.appendChild(main);
      body.appendChild(row);
    });
  };

  renderRows('ebay', groups.ebay);
  renderRows('amazon', groups.amazon);

  if((ebayCount > 0 || amazonCount > 0) && (ebayCount !== groups.ebay.length || amazonCount !== groups.amazon.length)){
    const note = document.createElement('div');
    note.className = 'small-muted';
    note.style.marginTop = '8px';
    note.textContent = `Detected counts: eBay ${groups.ebay.length}, Amazon ${groups.amazon.length}.`;
    body.appendChild(note);
  }

  modal.style.display = 'flex';
}

function render(results){
  let html = '<table><thead><tr>';
  if (selectMode) html += '<th>Select</th>';
  html += `<th>Info</th><th>Status</th><th>Notes and Media</th><th>Defect</th><th class="th-sortable" data-sort-key="quantity" title="Sort by quantity" role="button" tabindex="0">PREP <span class="sort-ind">${_sortIndicator('quantity')}</span></th><th class="th-sortable" data-sort-key="warehouse_available" title="Sort by warehouse quantity" role="button" tabindex="0">Warehouse <span class="sort-ind">${_sortIndicator('warehouse_available')}</span></th><th>Title</th><th>UPC</th><th>Marketplaces</th><th>Last Edited</th><th>+ Listing Agent</th></tr></thead><tbody>`;
  let rowIndex = 0;
  const viewResults = _sortedRenderResults(results);
  for (const r of viewResults) {
    const title = esc(r.title||'');
    const img = esc(r.image||'');
    const thumb = img ? img.replace('s-l1600.jpg', 's-l140.jpg') : '';
    const upcRaw = r.upc||'';
    // Pad UPCs that are 11 digits or less to 12 digits for display/copy
    // (Internal system still uses stripped version)
    const upcPadded = (upcRaw.length > 0 && upcRaw.length <= 11 && /^\d+$/.test(upcRaw)) 
      ? upcRaw.padStart(12, '0') 
      : upcRaw;
    const upcDisplayRaw = String(upcPadded || '');
    const upcSuffixMatch = upcDisplayRaw.match(/^(.*?)(-\d+)$/);
    const upcMainRaw = upcSuffixMatch ? upcSuffixMatch[1] : upcDisplayRaw;
    const upcSuffixRaw = upcSuffixMatch ? upcSuffixMatch[2] : '';
    const upcDisplayHtml = upcSuffixRaw
      ? `<span class="upc-main">${esc(upcMainRaw)}</span><span class="upc-suffix">${esc(upcSuffixRaw)}</span>`
      : `<span class="upc-main">${esc(upcMainRaw)}</span>`;
    const apiUpc = esc((upcRaw || '').toString());
    // Extract base UPC without suffix for copy button (with padding)
    const baseUpc = esc(upcMainRaw);
    // Listing Agent queue should use the raw base UPC (no padding) so /api/listingagent/upc/<upc> matches DB rows.
    const queueUpc = _listagentQueueKey(upcRaw);
    const queueTitleEnc = encodeURIComponent((r.title || '').toString());
    const last_edited = esc(r.last_edited||'');
    const status = (r.status||'').toLowerCase();
    const queueUi = _listagentQueueUiState(listingAgentQueueState[queueUpc] || null);
    const queueBtn = `<button type="button" class="btn btn-sm btn-primary btn-listagent-queue${queueUi.active ? ' btn-copied' : ''}" data-upc="${queueUpc}" data-title="${queueTitleEnc}" data-item-status="${esc(status)}" title="${esc(queueUi.title)}" ${queueUi.disabled ? 'disabled' : ''}>${esc(queueUi.label)}</button>`;
    let pill = '<span class="status-box" style="background:#64748b" title="Status">status</span>';
    if(status==='good') pill = '<span class="status-box" style="background:#2ecc71" title="Good">good</span>';
    if(status==='bad') pill = '<span class="status-box" style="background:#e74c3c" title="Bad">bad</span>';
    if(status==='return') pill = '<span class="status-box" style="background:#8b4513" title="Return">return</span>';
    
    // DEBUG: Log marketplace data for first item
    if (rowIndex === 0) {
      console.log('[RENDER] First item marketplace data:', {
        upc: r.upc,
        listed_amazon: r.listed_amazon,
        listed_ebay: r.listed_ebay,
        listed_facebook: r.listed_facebook,
        listed_amazon_date: r.listed_amazon_date,
        listed_ebay_date: r.listed_ebay_date,
        listed_facebook_date: r.listed_facebook_date
      });
    }
    
    // Marketplace listing checkboxes
    const listedAmazon = Number(r.listed_amazon) === 1;
    const listedEbay = Number(r.listed_ebay) === 1;
    const listedFacebook = Number(r.listed_facebook) === 1;
    const listedAmazonMixed = Number(r.listed_amazon_mixed) === 1 || r.listed_amazon_mixed === true;
    const listedEbayMixed = Number(r.listed_ebay_mixed) === 1 || r.listed_ebay_mixed === true;
    const listedFacebookMixed = Number(r.listed_facebook_mixed) === 1 || r.listed_facebook_mixed === true;
    const lotNumber = esc(r.lot_number||'');
    const prepGroupedAllLots = Number(r.prep_grouped_all_lots) === 1 || r.prep_grouped_all_lots === true;
    const selectionLocked = Number(r.selection_locked) === 1 || r.selection_locked === true;
    const diagLocked = Number(r.diag_locked) === 1 || r.diag_locked === true;
    const marketplaceLocked = Number(r.marketplace_edit_locked) === 1 || r.marketplace_edit_locked === true;
    const amazonTitle = [
      (listedAmazon && r.listed_amazon_date) ? ('Listed: ' + String(r.listed_amazon_date)) : '',
      (listedAmazon && r.listed_amazon_source_label) ? ('Set by: ' + String(r.listed_amazon_source_label)) : '',
      listedAmazonMixed ? 'Mixed across LOTS' : '',
      marketplaceLocked ? 'Filter to a single LOT to edit' : ''
    ].filter(Boolean).join(' | ');
    const ebayTitle = [
      (listedEbay && r.listed_ebay_date) ? ('Listed: ' + String(r.listed_ebay_date)) : '',
      (listedEbay && r.listed_ebay_source_label) ? ('Set by: ' + String(r.listed_ebay_source_label)) : '',
      listedEbayMixed ? 'Mixed across LOTS' : '',
      marketplaceLocked ? 'Filter to a single LOT to edit' : ''
    ].filter(Boolean).join(' | ');
    const facebookTitle = [
      (listedFacebook && r.listed_facebook_date) ? ('Listed: ' + String(r.listed_facebook_date)) : '',
      (listedFacebook && r.listed_facebook_source_label) ? ('Set by: ' + String(r.listed_facebook_source_label)) : '',
      listedFacebookMixed ? 'Mixed across LOTS' : '',
      marketplaceLocked ? 'Filter to a single LOT to edit' : ''
    ].filter(Boolean).join(' | ');
    
    rowIndex++;
    
    const rowIdAttr = esc(String(r.id || ''));
    const amazonCheckbox = `<label style="display:flex;align-items:center;gap:4px;cursor:pointer;white-space:nowrap" title="${esc(amazonTitle)}"><input type="checkbox" class="marketplace-checkbox" data-id="${rowIdAttr}" data-upc="${apiUpc}" data-lot="${lotNumber}" data-marketplace="amazon" data-indeterminate="${listedAmazonMixed ? '1' : '0'}" ${listedAmazon ? 'checked' : ''} ${marketplaceLocked ? 'disabled' : ''}><span style="font-size:0.85rem">Amazon</span></label>`;
    const ebayCheckbox = `<label style="display:flex;align-items:center;gap:4px;cursor:pointer;white-space:nowrap" title="${esc(ebayTitle)}"><input type="checkbox" class="marketplace-checkbox" data-id="${rowIdAttr}" data-upc="${apiUpc}" data-lot="${lotNumber}" data-marketplace="ebay" data-indeterminate="${listedEbayMixed ? '1' : '0'}" ${listedEbay ? 'checked' : ''} ${marketplaceLocked ? 'disabled' : ''}><span style="font-size:0.85rem">eBay</span></label>`;
    const fbCheckbox = `<label style="display:flex;align-items:center;gap:4px;cursor:pointer;white-space:nowrap" title="${esc(facebookTitle)}"><input type="checkbox" class="marketplace-checkbox" data-id="${rowIdAttr}" data-upc="${apiUpc}" data-lot="${lotNumber}" data-marketplace="facebook" data-indeterminate="${listedFacebookMixed ? '1' : '0'}" ${listedFacebook ? 'checked' : ''} ${marketplaceLocked ? 'disabled' : ''}><span style="font-size:0.85rem">Facebook</span></label>`;
    
    const actionBtn = `<div style="display:flex;flex-direction:column;gap:4px;align-items:flex-start">${amazonCheckbox}${ebayCheckbox}${fbCheckbox}</div>`;
    
    // Diagnostic link moved into its own Info column (first column)
    const diagLinkTitle = diagLocked ? 'Filter to a single LOT to open diagnostic edit' : 'Open';
    const diagLink = `<button class="btn btn-sm diag-link" style="background:transparent;border:1px solid #0066cc;color:#0066cc;border-radius:6px;padding:6px 10px;cursor:pointer" data-upc="${apiUpc}" data-lot="${esc(r.lot_number||'')}" data-status="${esc(status)}" title="${esc(diagLinkTitle)}" ${diagLocked ? 'disabled' : ''}>Open</button>`;
    const photoCount = Number(r.photo_count || 0);
    const videoCount = Number(r.video_count || 0);
    const hasPhotos = photoCount > 0;
    const hasVideos = videoCount > 0;
    const hasAnyMedia = hasPhotos || hasVideos;
    const mediaTitleParts = [];
    if(hasPhotos) mediaTitleParts.push(`${photoCount} photo${photoCount===1?'':'s'}`);
    if(hasVideos) mediaTitleParts.push(`${videoCount} video${videoCount===1?'':'s'}`);
    const picsTitle = mediaTitleParts.join(' | ');
    const picsBtn = hasAnyMedia
      ? `<button type="button" class="pics-count media-trigger" data-upc="${apiUpc}" data-lot="${esc(r.lot_number||'')}" data-status="${esc(status)}" title="${esc(picsTitle || 'Open media')}">`+
          `${hasPhotos ? `<span class="media-trigger-part"><span>📷</span><span>${photoCount}</span></span>` : ''}`+
          `${hasVideos ? `<span class="media-trigger-part video"><span>🎥</span><span>${videoCount}</span></span>` : ''}`+
        `</button>`
      : '';
    const whAvail = Number(r.warehouse_available || 0);
    const qtyRaw = Number(r.quantity);
    const qty = Number.isFinite(qtyRaw) ? qtyRaw : 0;
    const prepQtyWithoutWarehouse = qty > 0 && whAvail <= 0;
    const prepBreakdown = Array.isArray(r.prep_lot_breakdown)
      ? r.prep_lot_breakdown.filter(entry => entry && typeof entry === 'object')
      : [];
    const prepBreakdownEncoded = encodeURIComponent(JSON.stringify(prepBreakdown));
    const prepHasLotless = Number(r.prep_lotless_present) === 1 || prepBreakdown.some(entry => String(entry.lot_label || '').trim().toUpperCase() === 'LOTLESS');
    const prepLotsCount = prepBreakdown.filter(entry => String(entry.lot_label || entry.lot_number || '').trim().toUpperCase() !== 'LOTLESS').length;
    const prepTotalQtyRaw = Number(r.prep_total_quantity);
    const prepTotalQty = Number.isFinite(prepTotalQtyRaw) ? prepTotalQtyRaw : qty;
    const prepCurrentQtyRaw = Number(r.prep_current_lot_quantity);
    const prepCurrentQty = Number.isFinite(prepCurrentQtyRaw) ? prepCurrentQtyRaw : qty;
    const prepCurrentLabel = String(r.prep_current_label || r.lot_number || '').trim() || 'LOTLESS';
    const prepMultiLot = Number(r.prep_multi_lot) === 1 || r.prep_multi_lot === true || prepTotalQty > prepCurrentQty;
    const prepBtnTitle = [
      'Click to view prep by LOT',
      prepGroupedAllLots ? `Merged ALL LOTS row: ${prepTotalQty} total prepped` : '',
      (!prepGroupedAllLots && prepMultiLot && prepTotalQty > prepCurrentQty) ? `This LOT: ${prepCurrentQty} | All LOTS total: ${prepTotalQty}` : '',
      prepLotsCount > 0 ? `${prepLotsCount} LOT${prepLotsCount === 1 ? '' : 'S'} in prep history` : '',
      prepHasLotless ? 'LOTLESS prep row present' : ''
    ].filter(Boolean).join(' | ');
    const qtyStyle = (qty > 1) ? 'font-weight:700' : '';
    const prepQtyHtml = (prepMultiLot && !prepGroupedAllLots && prepTotalQty > prepCurrentQty)
      ? `<span style="${qtyStyle}">${prepCurrentQty}</span><span class="prep-total-hint">(${prepTotalQty})</span>`
      : `<span style="${qtyStyle}">${qty}</span>`;
    const qtyCell = `<td style="text-align:center"><button type="button" class="prep-qty-btn${prepHasLotless ? ' has-lotless' : ''}${prepMultiLot ? ' has-multi-lots' : ''}" data-upc="${apiUpc}" data-current-lot="${lotNumber}" data-current-label="${esc(prepCurrentLabel)}" data-qty="${prepGroupedAllLots ? prepTotalQty : qty}" data-total-qty="${prepTotalQty}" data-grouped-all-lots="${prepGroupedAllLots ? '1' : '0'}" data-has-lotless="${prepHasLotless ? '1' : '0'}" data-breakdown="${esc(prepBreakdownEncoded)}" title="${esc(prepBtnTitle)}">${prepQtyHtml}${prepHasLotless ? '<span class="prep-lotless-flag" title="LOTLESS prep row exists">!</span>' : ''}</button></td>`;
    const whBase = String(r.warehouse_base_upc || '').trim();
    const whLocations = Array.isArray(r.warehouse_locations)
      ? r.warehouse_locations.map(v => String(v || '').trim()).filter(Boolean)
      : [];
    let whLocationDetails = Array.isArray(r.warehouse_location_details)
      ? r.warehouse_location_details
          .map(v => ({
            location: String((v && (v.location || v.code)) || '').trim(),
            quantity: Number(v && (v.quantity ?? v.qty) || 0)
          }))
          .filter(v => v.location)
      : [];
    if(!whLocationDetails.length && whLocations.length){
      whLocationDetails = whLocations.map(loc => ({ location: loc, quantity: 0 }));
    }
    const whExact = Number(r.warehouse_exact ?? 0);
    const whStyle = prepQtyWithoutWarehouse
      ? 'color:#b91c1c;font-weight:800'
      : (whAvail > 0 ? 'color:#15803d;font-weight:700' : 'color:#b91c1c;font-weight:600');
    const whExactDisplay = Math.max(0, Number.isFinite(whExact) ? whExact : 0);
    const whOtherDisplay = Math.max(0, whAvail - whExactDisplay);
    const whHasSplitDisplay = whOtherDisplay > 0;
    const whExactSearchHref = `/searchrack?q=${encodeURIComponent(upcRaw)}`;
    const whBaseSearchHref = `/searchrack?q=${encodeURIComponent(whBase || baseUpc || upcRaw)}`;
    const whDisplayHtml = whHasSplitDisplay
      ? `<a href="${esc(whExactSearchHref)}" class="warehouse-split-link" style="${whStyle}" title="Search exact warehouse barcode">${whExactDisplay}</a><a href="${esc(whBaseSearchHref)}" class="warehouse-split-link" style="color:#64748b;font-size:0.84em;font-weight:700;" title="Search all same-base warehouse barcodes">(+${whOtherDisplay})</a>`
      : `<span style="${whStyle}">${whAvail}</span>`;
    const whTitleBase = whBase ? `Base UPC: ${whBase}` : '';
    const whLocCountLabel = `${whLocationDetails.length} location${whLocationDetails.length === 1 ? '' : 's'}`;
    const whSplitLabel = whHasSplitDisplay
      ? `Exact barcode qty: ${whExactDisplay} | Other same-base inventory: ${whOtherDisplay} | Combined total: ${whAvail}`
      : `Exact barcode qty: ${whExactDisplay}`;
    const whTitle = whAvail > 0
      ? [whTitleBase, whSplitLabel, whLocCountLabel, 'Click to view locations'].filter(Boolean).join(' | ')
      : whTitleBase;
    const whLocationsEncoded = encodeURIComponent(JSON.stringify(whLocationDetails));
    const isSuffixedUpc = !!upcSuffixRaw;
    let whCell;
    if(whHasSplitDisplay){
      const splitTitle = [whTitleBase, whSplitLabel, 'Green searches exact barcode', 'Gray searches base barcode'].filter(Boolean).join(' | ');
      whCell = `<td style="text-align:center" title="${esc(splitTitle)}">${whDisplayHtml}</td>`;
    } else if(isSuffixedUpc){
      const sfxTitle = [whTitleBase, whSplitLabel, 'Click to search exact barcode'].filter(Boolean).join(' | ');
      whCell = `<td style="text-align:center" title="${esc(sfxTitle)}"><a href="${esc(whExactSearchHref)}" style="text-decoration:none">${whDisplayHtml}</a></td>`;
    } else {
      whCell = whAvail > 0
        ? `<td style="text-align:center" title="${esc(whTitle)}"><button type="button" class="warehouse-info-btn" data-base-upc="${esc(whBase)}" data-locations="${esc(whLocationsEncoded)}">${whDisplayHtml}</button></td>`
        : `<td style="text-align:center" title="${esc(whTitle)}">${whDisplayHtml}</td>`;
    }
    const defectTextRaw = String(r.defect || '').trim();
    const defectDisplay = defectTextRaw
      ? defectTextRaw.replace(/\s*\|\s*/g, ', ')
      : (Number(r.set_note_flag) === 1 ? 'SET' : '');
    const audioCount = Number(r.audio_count || 0);
    const hasTextNote = r.note && String(r.note).trim().length > 0;
    const hasVoiceNote = audioCount > 0;
    const hasNote = hasTextNote || hasVoiceNote;
    const noteTitleParts = [];
    if(hasTextNote) noteTitleParts.push('Text notes');
    if(hasVoiceNote) noteTitleParts.push(`${audioCount} voice note${audioCount===1?'':'s'}`);
    const noteCell = hasNote
      ? `<td style="text-align:center;font-size:1.05rem">`+
          `<button type="button" class="note-icon note-icon-btn" data-upc="${apiUpc}" data-lot="${esc(r.lot_number||'')}" data-status="${esc(status)}" title="${esc(noteTitleParts.join(' | ') || 'Open notes')}">`+
            `<span>📝</span>`+
            `${hasVoiceNote ? '<span class="note-audio-flag">🔊</span>' : ''}`+
          `</button>`+
        `</td>`
      : `<td style="text-align:center"></td>`;
    const notesMediaCell = (hasNote || hasAnyMedia)
      ? `<td style="text-align:center"><div class="notes-media-cell">${hasNote ? noteCell.replace(/^<td[^>]*>|<\/td>$/g, '') : ''}${picsBtn || ''}</div></td>`
      : `<td style="text-align:center"></td>`;
    html += `<tr>`;
    if (selectMode) {
      // Allow selection of all items (base barcodes will be reset instead of deleted)
      html += `<td><input type="checkbox" class="item-checkbox" data-id="${r.id}" data-upc="${apiUpc}" ${selectionLocked ? 'disabled title="Filter to a single LOT to select/delete this row"' : ''}></td>`;
    }
    html += `<td style="width:64px;text-align:center">${diagLink}</td>`+
      `<td><div style="display:flex;flex-direction:column;gap:4px;align-items:flex-start">`+
            `<div>${pill}</div>`+
          `</div></td>`+
            notesMediaCell+
            `<td>${esc(defectDisplay)}</td>`+
             qtyCell+
             whCell+
             `<td><div class="title-cell"><img class="thumb" src="${thumb}" onerror="this.src='${missingThumb(48,48,'No Img')}'"><a href="#" class="img-link" data-img="${img}" data-title="${title}">${title}</a></div></td>`+
             `<td><div class="upc-wrap"><span class="upc-text">${upcDisplayHtml}</span><button type=\"button\" class=\"btn btn-sm btn-copy\" data-upc=\"${baseUpc}\" title=\"Copy UPC\">Copy</button></div></td>`+
             `<td>${actionBtn}</td>`+
             `<td class="last-edited-cell" data-raw="${last_edited}">${formatLastEdited(last_edited)}</td>`+
             `<td style="text-align:center;white-space:nowrap">${queueBtn}</td>`+
             `</tr>`;
  }
  html += '</tbody></table>';
  const container = document.getElementById('results');
  container.innerHTML = html;
  document.querySelectorAll('th.th-sortable[data-sort-key]').forEach(el => {
    const triggerSort = () => toggleColumnSort(el.dataset.sortKey);
    el.addEventListener('click', triggerSort);
    el.addEventListener('keydown', (e) => {
      if(e.key === 'Enter' || e.key === ' '){
        e.preventDefault();
        triggerSort();
      }
    });
  });
  // Add select all listener
  const selectAllEl = document.getElementById('select-all');
  if (selectAllEl) {
    selectAllEl.addEventListener('change', (e) => {
      const checked = e.target.checked;
      document.querySelectorAll('.item-checkbox:not(:disabled)').forEach(cb => cb.checked = checked);
    });
  }
  document.querySelectorAll('.img-link').forEach(el=> el.addEventListener('click', onOpenImg));
  document.querySelectorAll('.prep-qty-btn').forEach(el=> el.addEventListener('click', onOpenPrepBreakdown));
  document.querySelectorAll('.warehouse-info-btn').forEach(el=> el.addEventListener('click', onOpenWarehouseDetails));
  document.querySelectorAll('.auto-listings-btn').forEach(el=> el.addEventListener('click', onOpenAutoListings));
  document.querySelectorAll('.diag-link, button.diag-link').forEach(el=> el.addEventListener('click', onOpenDiag));
  // Wire marketplace checkbox handlers
  const marketplaceCheckboxes = document.querySelectorAll('.marketplace-checkbox');
  console.log('[render] Found', marketplaceCheckboxes.length, 'marketplace checkboxes, attaching listeners');
  marketplaceCheckboxes.forEach(el=> {
    el.indeterminate = el.dataset.indeterminate === '1';
    el.dataset.prevChecked = el.checked ? '1' : '0';
    el.addEventListener('change', onMarketplaceToggle);
  });
  document.querySelectorAll('.btn-copy').forEach(el=> el.addEventListener('click', onCopyUpc));
  document.querySelectorAll('.btn-listagent-queue').forEach(el=> el.addEventListener('click', onAddToListingAgentQueue));
  document.querySelectorAll('.pics-count').forEach(el=> el.addEventListener('click', onOpenNotesMedia));
  document.querySelectorAll('.note-icon').forEach(el=> el.addEventListener('click', onOpenNotesMedia));
}

function onOpenImg(e){
  e.preventDefault();
  const img = e.currentTarget.dataset.img;
  const title = e.currentTarget.dataset.title;
  const modal = document.getElementById('imgModal');
  const content = document.getElementById('imgContent');
  content.innerHTML = `<div><div class="small-muted" style="margin-bottom:8px">${esc(title)}</div>`+
                      (img?`<img src="${esc(img)}" style="max-width:100%">`:'<div class="small-muted">No image</div>')+
                      `</div>`;
  modal.style.display = 'flex';
}

document.getElementById('closeImg').addEventListener('click', ()=>{ document.getElementById('imgModal').style.display='none'; });
document.getElementById('closePics').addEventListener('click', ()=>{ document.getElementById('picsModal').style.display='none'; });
document.getElementById('closeNotes').addEventListener('click', ()=>{ document.getElementById('notesModal').style.display='none'; });
document.getElementById('closeWarehouse').addEventListener('click', ()=>{ document.getElementById('warehouseModal').style.display='none'; });
document.getElementById('closePrepBreakdown').addEventListener('click', ()=>{ document.getElementById('prepBreakdownModal').style.display='none'; });
document.getElementById('closeAutoListings').addEventListener('click', ()=>{ document.getElementById('autoListingsModal').style.display='none'; });
document.getElementById('warehouseModal').addEventListener('click', (e)=>{
  const pane = document.getElementById('warehousePreviewPane');
  const clickedImage = !!(e.target && (e.target.id === 'warehousePreviewImage' || (e.target.closest && e.target.closest('#warehousePreviewImage'))));
  if(pane && pane.classList.contains('expanded') && !clickedImage){
    pane.classList.remove('expanded');
  }
  if(e.target && e.target.id === 'warehouseModal'){
    e.currentTarget.style.display = 'none';
  }
});
document.getElementById('prepBreakdownModal').addEventListener('click', (e)=>{
  if(e.target && e.target.id === 'prepBreakdownModal'){
    e.currentTarget.style.display = 'none';
  }
});
document.getElementById('autoListingsModal').addEventListener('click', (e)=>{
  if(e.target && e.target.id === 'autoListingsModal'){
    e.currentTarget.style.display = 'none';
  }
});
// Apply button removed; filters auto-refresh on change and Enter in search
// Auto-refresh on dropdown changes
document.getElementById('filter-lot').addEventListener('change', (e)=>{ onGearSettingChanged(); loadItems(e, { historyMode: 'push' }); });
document.getElementById('sort-select').addEventListener('change', (e)=>{ onGearSettingChanged(); loadItems(e, { historyMode: 'push' }); });
document.getElementById('refresh-btn').addEventListener('click', (e)=> loadItems(e, { forceRefetch: true, historyMode: 'replace' }));

function updateStatusFilterHighlight(){
  const map = {
    good: document.querySelector('.status-checkbox[value="good"]'),
    bad: document.querySelector('.status-checkbox[value="bad"]'),
    return: document.querySelector('.status-checkbox[value="return"]')
  };
  Object.entries(map).forEach(([key, el]) => {
    if(!el) return;
    const label = el.closest(`label[data-status-filter="${key}"]`);
    if(label){
      label.classList.toggle('active', !!el.checked);
    }
  });
}
updateStatusFilterHighlight();

// Status checkboxes auto-refresh
document.querySelectorAll('.status-checkbox').forEach(cb => {
  cb.addEventListener('change', ()=>{
    updateStatusFilterHighlight();
    onGearSettingChanged();
    loadItems(null, { historyMode: 'push' });
  });
});

// defect filter should behave like other filters
const defectEl = document.getElementById('filter-defect'); if(defectEl) defectEl.addEventListener('change', (e)=>{ onGearSettingChanged(); loadItems(e, { historyMode: 'push' }); });
// radio buttons for listed filter
const listedEl = document.getElementById('filter-listed-select');
if(listedEl) listedEl.addEventListener('change', (e)=>{ onGearSettingChanged(); loadItems(e, { historyMode: 'push' }); });
const autoListedEl = document.getElementById('filter-auto-listed-select');
if(autoListedEl) autoListedEl.addEventListener('change', (e)=>{ onGearSettingChanged(); loadItems(e, { historyMode: 'push' }); });
const autoListedAmazonEl = document.getElementById('filter-auto-listed-amazon');
if(autoListedAmazonEl) autoListedAmazonEl.addEventListener('change', (e)=>{ onGearSettingChanged(); loadItems(e, { historyMode: 'push' }); });
const autoListedEbayEl = document.getElementById('filter-auto-listed-ebay');
if(autoListedEbayEl) autoListedEbayEl.addEventListener('change', (e)=>{ onGearSettingChanged(); loadItems(e, { historyMode: 'push' }); });
const warehouseFilterEl = document.getElementById('filter-warehouse-select');
if(warehouseFilterEl) warehouseFilterEl.addEventListener('change', (e)=>{ onGearSettingChanged(); loadItems(e, { historyMode: 'push' }); });
const listedAmazonEl = document.getElementById('filter-listed-amazon');
if(listedAmazonEl) listedAmazonEl.addEventListener('change', (e)=>{ onGearSettingChanged(); loadItems(e, { historyMode: 'push' }); });
const listedEbayEl = document.getElementById('filter-listed-ebay');
if(listedEbayEl) listedEbayEl.addEventListener('change', (e)=>{ onGearSettingChanged(); loadItems(e, { historyMode: 'push' }); });
const listedFbEl = document.getElementById('filter-listed-fb');
if(listedFbEl) listedFbEl.addEventListener('change', (e)=>{ onGearSettingChanged(); loadItems(e, { historyMode: 'push' }); });
const resetGearBtn = document.getElementById('reset-gear-filters');
if(resetGearBtn){
  resetGearBtn.addEventListener('click', (e)=>{
    e.preventDefault();
    applyGearSettings(GEAR_DEFAULTS);
    saveGearSettings();
    updateGearResetButton();
    loadItems(null, { historyMode: 'push' });
  });
}

function updateMarketplaceFilterHighlight(){
  const map = {
    amazon: document.getElementById('filter-listed-amazon'),
    ebay: document.getElementById('filter-listed-ebay'),
    facebook: document.getElementById('filter-listed-fb')
  };
  Object.entries(map).forEach(([key, el]) => {
    if(!el) return;
    const label = el.closest('label[data-marketplace-filter]');
    if(label){
      label.classList.toggle('active', !!el.checked);
    }
  });
}
updateMarketplaceFilterHighlight();
if(listedAmazonEl) listedAmazonEl.addEventListener('change', updateMarketplaceFilterHighlight);
if(listedEbayEl) listedEbayEl.addEventListener('change', updateMarketplaceFilterHighlight);
if(listedFbEl) listedFbEl.addEventListener('change', updateMarketplaceFilterHighlight);

function updateAutoMarketplaceFilterHighlight(){
  const map = {
    amazon: document.getElementById('filter-auto-listed-amazon'),
    ebay: document.getElementById('filter-auto-listed-ebay')
  };
  Object.entries(map).forEach(([key, el]) => {
    if(!el) return;
    const label = el.closest('label[data-auto-marketplace-filter]');
    if(label){
      label.classList.toggle('active', !!el.checked);
    }
  });
}
updateAutoMarketplaceFilterHighlight();
if(autoListedAmazonEl) autoListedAmazonEl.addEventListener('change', updateAutoMarketplaceFilterHighlight);
if(autoListedEbayEl) autoListedEbayEl.addEventListener('change', updateAutoMarketplaceFilterHighlight);

// Gear dropdown toggle and positioning
const gearBtn = document.getElementById('filter-gear');
const dropdown = document.getElementById('filter-dropdown');
if (gearBtn && dropdown){
  function positionDropdown(){
    const wasOpen = dropdown.style.display === 'block';
    if(!wasOpen){
      dropdown.style.visibility = 'hidden';
      dropdown.style.display = 'block';
    }
    const rect = gearBtn.getBoundingClientRect();
    const vw = window.innerWidth || document.documentElement.clientWidth || 1024;
    const vh = window.innerHeight || document.documentElement.clientHeight || 768;
    const gap = 6;
    const margin = 8;

    dropdown.style.maxHeight = '';
    dropdown.style.overflowY = 'auto';

    const panelW = dropdown.offsetWidth || 320;
    const panelH = dropdown.offsetHeight || 320;

    let left = rect.right - panelW; // keep panel aligned to gear button's right edge
    if(!Number.isFinite(left)) left = rect.left;
    left = Math.max(margin, Math.min(left, vw - panelW - margin));

    let top = rect.bottom + gap;
    const fitsBelow = (top + panelH) <= (vh - margin);
    const topAbove = rect.top - panelH - gap;

    if(!fitsBelow && topAbove >= margin){
      top = topAbove; // flip above button when bottom space is tight
    } else if(!fitsBelow){
      top = margin; // clamp to viewport and allow internal scrolling
      const maxH = Math.max(180, vh - (margin * 2));
      dropdown.style.maxHeight = maxH + 'px';
    }

    dropdown.style.left = Math.round(left) + 'px';
    dropdown.style.top = Math.round(top) + 'px';

    if(!wasOpen){
      dropdown.style.display = 'none';
      dropdown.style.visibility = '';
    }
  }
  gearBtn.addEventListener('click', (e)=>{
    e.preventDefault();
    const open = dropdown.style.display === 'block';
    if(open){
      dropdown.style.display = 'none';
      gearBtn.setAttribute('aria-expanded','false');
    } else {
      positionDropdown();
      dropdown.style.display = 'block';
      gearBtn.setAttribute('aria-expanded','true');
    }
  });
  window.addEventListener('resize', ()=>{ if(dropdown.style.display==='block') positionDropdown(); });
  window.addEventListener('scroll', ()=>{ if(dropdown.style.display==='block') positionDropdown(); }, true);
  document.addEventListener('click', (e)=>{
    if (dropdown.style.display==='block'){
      if(!dropdown.contains(e.target) && e.target!==gearBtn){
        dropdown.style.display='none';
        gearBtn.setAttribute('aria-expanded','false');
      }
    }
  });
}
function onOpenDiag(e){
  e.preventDefault();
  const upc = e.currentTarget.dataset.upc; if(!upc) return;
  const lot = e.currentTarget.dataset.lot || '';
  const rowStatus = (e.currentTarget.dataset.status || '').trim();
  const ret = buildCurrentItemsToListUrl();
  let url = '/item-prep/diagnostic/view?upc='+encodeURIComponent(upc)+'&from=items&return='+encodeURIComponent(ret);
  if(lot) url += '&lot='+encodeURIComponent(lot);
  if(rowStatus) url += '&row_status='+encodeURIComponent(rowStatus);
  window.location.href = url;
}

async function openNotesMediaModal({ upc, lot = '', rowStatus = '', focus = 'notes' } = {}){
  const upcValue = String(upc || '').trim();
  if(!upcValue) return;
  const modal = document.getElementById('notesModal');
  const titleEl = document.getElementById('notesModalTitle');
  const metaEl = document.getElementById('notesModalMeta');
  const content = document.getElementById('notesModalContent');
  if(!modal || !content) return;

  if(titleEl) titleEl.textContent = 'Notes and Media';
  if(metaEl) metaEl.textContent = 'Loading...';
  content.innerHTML = '<div class="small-muted">Loading notes, voice notes, photos, and videos...</div>';
  modal.style.display = 'flex';

  const ret = buildCurrentItemsToListUrl();
  let diagUrl = '/item-prep/diagnostic?upc='+encodeURIComponent(upcValue)+'&from=items&return='+encodeURIComponent(ret);
  if(lot) diagUrl += '&lot='+encodeURIComponent(lot);
  if(rowStatus) diagUrl += '&row_status='+encodeURIComponent(rowStatus);

  try{
    const params = new URLSearchParams();
    if(lot) params.set('lot', lot);
    if(rowStatus) params.set('row_status', rowStatus);

    const notesQs = params.toString();
    const photoParams = new URLSearchParams(params.toString());
    photoParams.set('exact_scope', '1');

    const audioParams = new URLSearchParams(params.toString());
    audioParams.set('media_type', 'audio');

    const videoParams = new URLSearchParams(params.toString());
    videoParams.set('media_type', 'video');

    const [notesRes, audioRes, photoRes, videoRes] = await Promise.all([
      fetch('/api/items_prep/notes/'+encodeURIComponent(upcValue)+(notesQs ? ('?'+notesQs) : '')),
      fetch('/api/items_prep/media/'+encodeURIComponent(upcValue)+'?'+audioParams.toString()),
      fetch('/api/items_prep/diagnostic/'+encodeURIComponent(upcValue)+'?'+photoParams.toString()),
      fetch('/api/items_prep/media/'+encodeURIComponent(upcValue)+'?'+videoParams.toString())
    ]);

    const notesJson = notesRes.ok ? await notesRes.json() : null;
    const audioJson = audioRes.ok ? await audioRes.json() : null;
    const photoJson = photoRes.ok ? await photoRes.json() : null;
    const videoJson = videoRes.ok ? await videoRes.json() : null;

    const notes = Array.isArray(notesJson && notesJson.notes) ? notesJson.notes : [];
    const audioItems = Array.isArray(audioJson && audioJson.items) ? audioJson.items : [];
    const imgs = Array.isArray(photoJson && photoJson.images) ? photoJson.images : [];
    const videos = Array.isArray(videoJson && videoJson.items) ? videoJson.items : [];

    if(metaEl){
      metaEl.textContent = _notesMediaBuildMeta(upcValue, notes.length, audioItems.length, imgs.length, videos.length) || 'No notes or media found.';
    }

    const mediaItems = [];
    imgs.forEach((im, idx) => {
      const src = resolveStaticAssetUrl(im && im.image_path);
      if(!src) return;
      mediaItems.push({
        id: 'photo-' + (im && im.id ? im.id : idx),
        media_type: 'photo',
        src,
        created_at: im && im.created_at ? im.created_at : ''
      });
    });
    videos.forEach((item, idx) => {
      const src = resolveStaticAssetUrl(item && item.file_path);
      if(!src) return;
      mediaItems.push({
        id: 'video-' + (item && item.id ? item.id : idx),
        media_type: 'video',
        src,
        created_at: item && item.created_at ? item.created_at : ''
      });
    });

    const sectionBlocks = [];
    sectionBlocks.push(
      `<div class="notes-media-actions">`+
        `<a class="btn btn-sm" href="${esc(diagUrl)}" style="background:#e74c3c;color:#fff">Edit Diagnostic</a>`+
        `${imgs.length ? `<a class="btn btn-sm" href="/api/items_prep/diagnostic/${encodeURIComponent(upcValue)}/photos.zip${rowStatus ? ('?row_status=' + encodeURIComponent(rowStatus)) : ''}" style="background:#3498db;color:#fff">Download Photos</a>` : ''}`+
      `</div>`
    );

    if(notes.length){
      sectionBlocks.push(
        `<div class="notes-section" id="notesMediaSection-notes">`+
          `<div class="notes-section-title">Text Notes</div>`+
          notes.map(n=>{
            const date = formatEasternDateTime(n && n.created_at);
            return `<div class="text-note-card">`+
              `<div class="small-muted" style="margin-bottom:4px">${esc(date)}</div>`+
              `<div>${esc(n && n.note)}</div>`+
            `</div>`;
          }).join('')+
        `</div>`
      );
    }

    if(audioItems.length){
      sectionBlocks.push(
        `<div class="notes-section" id="notesMediaSection-voice">`+
          `<div class="notes-section-title">Voice Notes</div>`+
          audioItems.map((item, idx)=>{
            const src = resolveStaticAssetUrl(item && item.file_path);
            const date = formatEasternDateTime(item && item.created_at);
            return `<div class="voice-note-card">`+
              `<div style="display:flex;justify-content:space-between;gap:10px;align-items:center;flex-wrap:wrap">`+
                `<strong>Voice note ${idx + 1}</strong>`+
                `<span class="small-muted">${esc(date)}</span>`+
              `</div>`+
              `<audio controls preload="metadata" src="${esc(src)}"></audio>`+
            `</div>`;
          }).join('')+
        `</div>`
      );
    }

    if(mediaItems.length){
      sectionBlocks.push(
        `<div class="notes-section" id="notesMediaSection-media">`+
          `<div class="notes-section-title">Photos and Videos</div>`+
          `<div id="notesMediaLarge" class="mixed-media-stage"></div>`+
          `<div id="notesMediaGrid" class="mixed-media-grid"></div>`+
        `</div>`
      );
    }

    if(sectionBlocks.length === 1){
      content.innerHTML = '<div class="small-muted">No notes, voice notes, photos, or videos found.</div>';
      return;
    }

    content.innerHTML = sectionBlocks.join('');

    if(mediaItems.length){
      const stage = document.getElementById('notesMediaLarge');
      const grid = document.getElementById('notesMediaGrid');
      let activeThumb = null;
      let activeItem = null;
      const setActiveMedia = (item, thumbEl) => {
        activeItem = item;
        stage.innerHTML = renderLargeMediaHtml(item);
        if(activeThumb) activeThumb.classList.remove('active');
        if(thumbEl){
          thumbEl.classList.add('active');
          activeThumb = thumbEl;
          thumbEl.scrollIntoView({ block: 'nearest', inline: 'center' });
        }
      };

      mediaItems.forEach((item, idx) => {
        const wrap = document.createElement('button');
        wrap.type = 'button';
        wrap.className = 'mixed-media-thumb';
        wrap.title = item.media_type === 'video' ? 'Open video' : 'Open photo';
        wrap.dataset.mediaType = item.media_type;
        if(item.media_type === 'video'){
          wrap.innerHTML = `<video src="${esc(item.src)}" muted playsinline preload="metadata"></video><div class="mixed-media-badge">VIDEO</div>`;
        } else {
          wrap.innerHTML = `<img src="${esc(item.src)}" alt="Photo"><div class="mixed-media-badge">PHOTO</div>`;
        }
        wrap.addEventListener('click', ()=> setActiveMedia(item, wrap));
        grid.appendChild(wrap);
        if(idx === 0){
          setActiveMedia(item, wrap);
        }
      });

      if(focus === 'video'){
        const preferredThumb = Array.from(grid.querySelectorAll('.mixed-media-thumb')).find(el => el.dataset.mediaType === 'video');
        if(preferredThumb){
          preferredThumb.click();
        }
      } else if(focus === 'media'){
        const preferredThumb = Array.from(grid.querySelectorAll('.mixed-media-thumb')).find(el => el.dataset.mediaType === 'photo') || grid.querySelector('.mixed-media-thumb');
        if(preferredThumb && (!activeItem || preferredThumb !== activeThumb)){
          preferredThumb.click();
        }
      }
    }

    const targetSectionName = _notesMediaPreferredSection(focus, {
      hasNotes: notes.length > 0,
      hasVoice: audioItems.length > 0,
      hasMedia: mediaItems.length > 0,
      hasVideo: videos.length > 0
    });
    if(targetSectionName){
      const sectionEl = document.getElementById(`notesMediaSection-${targetSectionName}`);
      if(sectionEl){
        requestAnimationFrame(() => {
          sectionEl.scrollIntoView({ behavior: 'smooth', block: 'start' });
        });
      }
    } else {
      content.scrollTop = 0;
    }
  }catch(err){
    console.error('Error loading combined notes/media:', err);
    if(metaEl) metaEl.textContent = '';
    content.innerHTML = '<div class="small-muted">Error loading notes or media</div>';
  }
}

async function onOpenNotesMedia(e){
  e.preventDefault();
  const el = e.currentTarget;
  const upc = el.dataset.upc || el.getAttribute('data-upc');
  const lot = el.dataset.lot || '';
  const rowStatus = (el.dataset.status || '').trim();
  const focus = inferNotesMediaFocusFromEvent(e);
  return openNotesMediaModal({ upc, lot, rowStatus, focus });
}

async function onOpenNotes(e){
  return onOpenNotesMedia(e);
}

async function onMarketplaceToggle(e){
  const checkbox = e.currentTarget;
  const row = checkbox.closest('tr');
  const rowCheckboxes = row ? Array.from(row.querySelectorAll('.marketplace-checkbox')) : [checkbox];
  const itemIdRaw = checkbox.dataset.id || '';
  const itemId = parseInt(itemIdRaw, 10);
  const upc = checkbox.dataset.upc;
  const lot = checkbox.dataset.lot || '';
  const marketplace = checkbox.dataset.marketplace;
  const listed = checkbox.checked;
  const previousChecked = checkbox.dataset.prevChecked === '1';

  if(!upc || !marketplace) return;
  if(!Number.isFinite(itemId) || itemId <= 0){
    checkbox.checked = previousChecked;
    alert('Row id missing. Refresh the page and try again.');
    return;
  }
  if(row && row.dataset.marketplaceSaving === '1') {
    checkbox.checked = previousChecked;
    return;
  }

  console.log(`[onMarketplaceToggle] ${listed ? 'Listing' : 'Unlisting'} ${upc} lot=${lot} on ${marketplace}`);

  // Prompt for quantity when checking Facebook
  let quantity = 1;
  if(marketplace === 'facebook' && listed) {
    const input = prompt('Facebook Marketplace quantity:', '1');
    if(input === null) {
      // User cancelled - revert checkbox
      checkbox.checked = previousChecked;
      return;
    }
    quantity = parseInt(input, 10) || 1;
    if(quantity < 1) quantity = 1;
  }

  try{
    if(row) row.dataset.marketplaceSaving = '1';
    pendingMarketplaceSaves += 1;
    rowCheckboxes.forEach(cb => cb.disabled = true);

    const payload = { upc, lot_number: lot, marketplace, listed, quantity, source: 'user' };
    if(Number.isFinite(itemId) && itemId > 0) payload.id = itemId;

    const response = await fetch('/api/bol_items/list_status', {
      method:'POST',
      headers:{'Content-Type':'application/json'},
      body: JSON.stringify(payload),
      cache: 'no-store',
      keepalive: true
    });
    const result = await response.json();
    console.log('[onMarketplaceToggle] API response:', result);
    if(!result.success) {
      console.error('[onMarketplaceToggle] API returned error:', result.error);
      // Revert checkbox on error
      checkbox.checked = previousChecked;
      checkbox.dataset.prevChecked = checkbox.checked ? '1' : '0';
      return;
    }
    
    // CRITICAL: Update all checkboxes with the ACTUAL database state
    // Don't trust cached page data - use the freshly queried state from server
    if(row && result.current_state) {
      const amazonCb = row.querySelector('.marketplace-checkbox[data-marketplace="amazon"]');
      const ebayCb = row.querySelector('.marketplace-checkbox[data-marketplace="ebay"]');
      const facebookCb = row.querySelector('.marketplace-checkbox[data-marketplace="facebook"]');
      const amazonOn = Number(result.current_state.listed_amazon) === 1;
      const ebayOn = Number(result.current_state.listed_ebay) === 1;
      const facebookOn = Number(result.current_state.listed_facebook) === 1;
      
      if(amazonCb) amazonCb.checked = amazonOn;
      if(ebayCb) ebayCb.checked = ebayOn;
      if(facebookCb) facebookCb.checked = facebookOn;

      rowCheckboxes.forEach(cb => {
        cb.dataset.prevChecked = cb.checked ? '1' : '0';
      });
    } else {
      checkbox.dataset.prevChecked = checkbox.checked ? '1' : '0';
    }
    
    // Update checkbox title with timestamp
    if(listed) {
      const now = new Date().toISOString();
      const sourceLabel = (result.current_state && result.current_state.source_label) ? result.current_state.source_label : 'User';
      checkbox.parentElement.title = `Listed: ${now} | Set by: ${sourceLabel}`;
    } else {
      checkbox.parentElement.title = '';
    }

  }catch(err){ 
    console.error('[onMarketplaceToggle] Fetch error:', err);
    // Revert checkbox on error
    checkbox.checked = previousChecked;
    checkbox.dataset.prevChecked = checkbox.checked ? '1' : '0';
  }finally{
    rowCheckboxes.forEach(cb => cb.disabled = false);
    if(row) delete row.dataset.marketplaceSaving;
    pendingMarketplaceSaves = Math.max(0, pendingMarketplaceSaves - 1);
  }
}

window.addEventListener('beforeunload', (e)=>{
  if(pendingMarketplaceSaves > 0){
    e.preventDefault();
    e.returnValue = '';
  }
});

// Copy UPC to clipboard
async function onCopyUpc(e){
  console.log('[onCopyUpc] Click handler fired');
  e.preventDefault();
  // Capture button reference immediately before any async operations
  const btn = e.currentTarget;
  const upc = btn.dataset.upc || '';
  const oldText = btn.textContent;
  console.log('[onCopyUpc] UPC:', upc);
  if(!upc) return;
  let copied = false;
  try{
    if(navigator.clipboard && navigator.clipboard.writeText){
      console.log('[onCopyUpc] Attempting clipboard API...');
      await navigator.clipboard.writeText(upc);
      copied = true;
      console.log('[onCopyUpc] Clipboard API success');
    }
  }catch(err){
    console.log('[onCopyUpc] Clipboard API failed:', err);
  }
  if(!copied){
    console.log('[onCopyUpc] Attempting fallback execCommand...');
    try{
      const ta = document.createElement('textarea');
      ta.value = upc;
      ta.style.position = 'fixed'; ta.style.opacity = '0'; ta.style.pointerEvents = 'none';
      document.body.appendChild(ta);
      ta.focus(); ta.select();
      copied = document.execCommand && document.execCommand('copy');
      document.body.removeChild(ta);
      console.log('[onCopyUpc] Fallback result:', copied);
    }catch(err2){
      console.log('[onCopyUpc] Fallback error:', err2);
    }
  }
  // Show feedback clearly: change text, color, and disable briefly
  console.log('[onCopyUpc] Changing button from', oldText, 'to "Copied"');
  btn.textContent = 'Copied';
  btn.classList.add('btn-copied');
  btn.disabled = true;
  setTimeout(()=>{ 
    console.log('[onCopyUpc] Reverting button to', oldText);
    btn.textContent = oldText; 
    btn.classList.remove('btn-copied'); 
    btn.disabled = false; 
  }, 1200);
}

// Add UPC to Listing Agent queue (for /listingagent bubbles)
async function onAddToListingAgentQueue(e){
  e.preventDefault();
  const btn = e.currentTarget;
  const upc = (btn.dataset.upc || '').toString().trim();
  if(!upc) return;

  const oldText = btn.textContent;
  btn.disabled = true;
  btn.textContent = '...';

  try{
    const title = btn.dataset.title ? decodeURIComponent(btn.dataset.title) : '';
    const item_status = (btn.dataset.itemStatus || '').toString().trim();
    const resp = await fetch('/api/listingagent/queue/add', {
      method: 'POST',
      headers: {'Content-Type':'application/json'},
      body: JSON.stringify({upc, title, source: 'list_manager', item_status, added_mode: 'my'})
    });
    const j = await resp.json().catch(()=> ({}));
    if(resp.ok && j && j.success){
      listingAgentQueueState[upc] = j.item || { upc, status: 'queued', added_mode: 'my' };
      const queueUi = _listagentQueueUiState(listingAgentQueueState[upc]);
      btn.textContent = queueUi.label;
      btn.title = queueUi.title;
      btn.classList.add('btn-copied');
      btn.disabled = true;
      return;
    }
    btn.textContent = oldText;
    btn.disabled = false;
    alert('Error: ' + ((j && j.error) ? j.error : ('HTTP ' + resp.status)));
  }catch(err){
    btn.textContent = oldText;
    btn.disabled = false;
    alert('Error: ' + err);
  }
}

// Populate lot dropdown with newest-first lots and import dates
async function loadLots(){
  try{
    const r = await fetch('/api/bol_lots');
    const j = await r.json();
    const sel = document.getElementById('filter-lot');
    const cur = sel.value;
    sel.innerHTML = '<option value="">All lots</option>';
    (j.lots||[]).forEach(x=>{
      const opt = document.createElement('option');
      const lotKey = String(x.lot_number || '').trim().toUpperCase();
      const lotLabel = (lotKey === 'LOTLESS' || lotKey === 'RETURNS')
        ? lotKey
        : String(x.lot_number || '').trim();
      opt.value = x.lot_number;
      opt.textContent = x.import_date ? `${lotLabel} — ${x.import_date}` : lotLabel;
      sel.appendChild(opt);
    });
    const preferredLot = pendingLotSelection || cur;
    if(preferredLot) sel.value = preferredLot;
    pendingLotSelection = sel.value || '';
    updateGearResetButton();
  }catch{}
}

// Initial load: restore persisted/URL filters and auto-fetch page 1.
// Load lots first so any persisted lot filter is resolved before the first query.
async function initializeItemsToList(){
  const initParams = new URLSearchParams(window.location.search);
  const directSearch = restoreItemsToListStateFromURL(initParams);
  await flushPendingMarketplaceOps();
  await loadLots();
  if(directSearch){
    const lotEl = document.getElementById('filter-lot');
    if(lotEl) lotEl.value = '';
    pendingLotSelection = '';
  }
  await loadItems(null, { historyMode: 'replace' });
  const qInput = document.getElementById('filter-q');
  if(qInput) qInput.focus();
}
initializeItemsToList();
// Back button navigates home; using button confines clickable area to the button
const backBtn = document.getElementById('back-btn');
if (backBtn) {
  backBtn.addEventListener('click', ()=>{ 
    window.location.href = '/'; 
  });
}
// Trigger load on Enter in search
document.getElementById('filter-q').addEventListener('keydown', (e)=>{ if(e.key==='Enter'){ e.preventDefault(); loadItems(e, { historyMode: 'push' }); } });
// Search button click handler
document.getElementById('search-btn').addEventListener('click', (e)=>{ loadItems(e, { historyMode: 'push' }); });

// Select mode and delete handlers

document.getElementById('select-btn').addEventListener('click', () => {
  selectMode = !selectMode;
  document.getElementById('select-btn').textContent = selectMode ? 'Cancel Select' : 'Select';
  document.getElementById('delete-selected').style.display = selectMode ? 'inline-block' : 'none';
  document.getElementById('select-all-label').style.display = selectMode ? 'inline' : 'none';
  document.getElementById('select-all').checked = false;
  // Re-render with cached results to show/hide checkboxes
  if (lastRenderedResults.length > 0) {
    render(lastRenderedResults);
  }
});

document.getElementById('delete-selected').addEventListener('click', async () => {
  const checked = document.querySelectorAll('.item-checkbox:checked');
  const items = Array.from(checked).map(cb => ({
    id: cb.dataset.id,
    upc: cb.dataset.upc
  }));
  if (!items.length) return alert('No items selected');
  if (!confirm(`Process ${items.length} selected items?\n\nBase barcodes will be reset, duplicates will be deleted.`)) return;
  try {
    const resp = await fetch('/api/bulk_delete_bol_items', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({items})});
    const j = await resp.json();
    if (j.success) {
      const parts = [];
      if (j.deleted > 0) parts.push(`Deleted ${j.deleted} item${j.deleted===1?'':'s'}`);
      if (j.reset > 0) parts.push(`Reset ${j.reset} base barcode${j.reset===1?'':'s'}`);
      alert(parts.join('\n') || 'No changes made');
      loadItems();
    } else {
      alert('Error: ' + j.error);
    }
  } catch (e) {
    alert('Error: ' + e);
  }
});

function renderPagination(total, page, limit) {
  const totalPages = Math.ceil(total / limit);
  let html = '';
  if (totalPages > 1) {
    // Prev button (red, rounded, with icon)
    if (page > 1) {
      html += `<button class="btn" style="background:#e74c3c;color:#fff;border-radius:8px;padding:8px 16px;font-weight:600;border:none;cursor:pointer;transition:all 0.2s;box-shadow:0 2px 4px rgba(0,0,0,0.1)" onmouseover="this.style.background='#c0392b'" onmouseout="this.style.background='#e74c3c'" onclick="changePage(${page-1})">← Prev</button>`;
    } else {
      html += `<button class="btn" style="background:#ddd;color:#999;border-radius:8px;padding:8px 16px;font-weight:600;border:none;cursor:not-allowed;box-shadow:0 2px 4px rgba(0,0,0,0.05)" disabled>← Prev</button>`;
    }
    
    // Always show page 1
    html += `<button class="btn ${page===1?'btn-primary':''}" style="border-radius:8px;padding:8px 14px;min-width:42px;font-weight:${page===1?'700':'500'};border:${page===1?'none':'1px solid #ddd'};background:${page===1?'#3498db':'#fff'};color:${page===1?'#fff':'#333'};cursor:pointer;transition:all 0.2s;box-shadow:0 2px 4px rgba(0,0,0,${page===1?'0.15':'0.05'})" ${page!==1?`onmouseover="this.style.background='#f0f0f0'" onmouseout="this.style.background='#fff'"`:''}  onclick="changePage(1)">1</button>`;
    
    // Show ellipsis if there's a gap between page 1 and the current range
    if (page > 4) {
      html += `<span style="padding:0 4px;color:#999;font-size:1.2rem">⋯</span>`;
    }
    
    // Show pages around current (but not page 1 or last page, we handle those separately)
    for (let p = Math.max(2, page-2); p <= Math.min(totalPages-1, page+2); p++) {
      // Skip if this is page 1 (already shown) or overlaps with page 1
      if (p === 1) continue;
      const isActive = p === page;
      html += `<button class="btn" style="border-radius:8px;padding:8px 14px;min-width:42px;font-weight:${isActive?'700':'500'};border:${isActive?'none':'1px solid #ddd'};background:${isActive?'#3498db':'#fff'};color:${isActive?'#fff':'#333'};cursor:pointer;transition:all 0.2s;box-shadow:0 2px 4px rgba(0,0,0,${isActive?'0.15':'0.05'})" ${!isActive?`onmouseover="this.style.background='#f0f0f0'" onmouseout="this.style.background='#fff'"`:''}  onclick="changePage(${p})">${p}</button>`;
    }
    
    // Show ellipsis if there's a gap between current range and last page
    if (page < totalPages - 3) {
      html += `<span style="padding:0 4px;color:#999;font-size:1.2rem">⋯</span>`;
    }
    
    // Always show last page (if more than 1 page)
    if (totalPages > 1) {
      const isActive = page === totalPages;
      html += `<button class="btn" style="border-radius:8px;padding:8px 14px;min-width:42px;font-weight:${isActive?'700':'500'};border:${isActive?'none':'1px solid #ddd'};background:${isActive?'#3498db':'#fff'};color:${isActive?'#fff':'#333'};cursor:pointer;transition:all 0.2s;box-shadow:0 2px 4px rgba(0,0,0,${isActive?'0.15':'0.05'})" ${!isActive?`onmouseover="this.style.background='#f0f0f0'" onmouseout="this.style.background='#fff'"`:''}  onclick="changePage(${totalPages})">${totalPages}</button>`;
    }
    
    // Next button (green, rounded, with icon)
    if (page < totalPages) {
      html += `<button class="btn" style="background:#2ecc71;color:#fff;border-radius:8px;padding:8px 16px;font-weight:600;border:none;cursor:pointer;transition:all 0.2s;box-shadow:0 2px 4px rgba(0,0,0,0.1)" onmouseover="this.style.background='#27ae60'" onmouseout="this.style.background='#2ecc71'" onclick="changePage(${page+1})">Next →</button>`;
    } else {
      html += `<button class="btn" style="background:#ddd;color:#999;border-radius:8px;padding:8px 16px;font-weight:600;border:none;cursor:not-allowed;box-shadow:0 2px 4px rgba(0,0,0,0.05)" disabled>Next →</button>`;
    }
  }
  
  // Update top pagination
  let pagDiv = document.getElementById('top-pagination');
  if (!pagDiv) {
    pagDiv = document.createElement('div');
    pagDiv.id = 'top-pagination';
    pagDiv.style.cssText = 'display:flex;align-items:center;gap:8px;flex-wrap:wrap';
    const host = document.querySelector('.filters');
    if (host) host.appendChild(pagDiv);
  }
  pagDiv.innerHTML = html;
  
  // Update bottom pagination
  const bottomPagDiv = document.getElementById('bottom-pagination');
  if (bottomPagDiv) {
    bottomPagDiv.innerHTML = html;
  }
}

function changePage(p) {
  currentPage = p;
  loadItems(null, { historyMode: 'push' });
}

// Update loadItems to include page and limit
async function loadItems(e, opts = {}){
  const overlay = document.getElementById('loading-overlay');
  if(overlay) overlay.style.display = 'flex';
  saveFilterUiSettings();

  const forceRefetch = !!(opts && opts.forceRefetch);
  const historyMode = (opts && opts.historyMode) || 'replace';
  const context = _buildBolItemsQueryContext();
  const baseParams = context.params;
  const cacheKey = context.cacheKey;
  const globalSortMode = _isGlobalColumnSortActive();

  const syncPerPageDropdown = () => {
    const perPageEl = document.getElementById('per-page-select');
    if (perPageEl) perPageEl.value = String(currentLimit);
  };
  const updateSummary = (count, unique, qty) => {
    const summaryEl = document.getElementById('results-summary');
    if (summaryEl) {
      summaryEl.textContent = `Results: ${count} items — ${unique} unique items — ${qty} total quantity`;
    }
  };

  if(globalSortMode){
    const hasMatchingCache = fullColumnSortLoaded && fullColumnSortCacheKey === cacheKey;
    const shouldFetchFull = forceRefetch || !hasMatchingCache;
    if(shouldFetchFull){
      const fullParams = new URLSearchParams(baseParams.toString());
      fullParams.set('page', '1');
      fullParams.set('limit', String(FULL_COLUMN_SORT_LIMIT));
      fullParams.set('_t', Date.now());
      try{
        const resp = await fetch('/api/bol_items?' + fullParams.toString(), { cache: 'no-store' });
        if(!resp.ok){
          console.error('Failed to load full sorted items', resp.status);
          if(!hasMatchingCache){
            fullColumnSortLoaded = true;
            fullColumnSortCacheKey = cacheKey;
            fullColumnSortResults = [];
            fullColumnSortStats = { total: 0, unique_items: 0, total_quantity: 0 };
          }
        } else {
          const j = await resp.json();
          fullColumnSortLoaded = true;
          fullColumnSortCacheKey = cacheKey;
          fullColumnSortResults = Array.isArray(j.results) ? j.results : [];
          fullColumnSortStats = {
            total: Number(j.total || fullColumnSortResults.length || 0),
            unique_items: Number(j.unique_items || 0),
            total_quantity: Number(j.total_quantity || 0)
          };
          console.log('[LOAD_ITEMS] Full sort fetched', fullColumnSortResults.length, 'rows');
        }
      }catch(err){
        console.error('Error fetching full sorted /api/bol_items:', err);
        if(!hasMatchingCache){
          fullColumnSortLoaded = true;
          fullColumnSortCacheKey = cacheKey;
          fullColumnSortResults = [];
          fullColumnSortStats = { total: 0, unique_items: 0, total_quantity: 0 };
        }
      }
    }

    const allResults = Array.isArray(fullColumnSortResults) ? fullColumnSortResults : [];
    const sortedAll = _sortedRenderResults(allResults);
    const totalCount = sortedAll.length;
    const limit = Math.max(1, Number(currentLimit || 25));
    const totalPages = Math.max(1, Math.ceil(Math.max(totalCount, 1) / limit));
    if(currentPage > totalPages) currentPage = totalPages;
    if(currentPage < 1) currentPage = 1;
    const page = currentPage;
    const start = (page - 1) * limit;
    const pageResults = sortedAll.slice(start, start + limit);

    const uniqueCount = Number((fullColumnSortStats && fullColumnSortStats.unique_items) || 0);
    const totalQty = Number((fullColumnSortStats && fullColumnSortStats.total_quantity) || 0);

    currentLimit = limit;
    syncPerPageDropdown();
    updateSummary(totalCount, uniqueCount, totalQty);
    lastRenderedResults = pageResults;
    await hydrateListingAgentQueueState(pageResults);
    render(pageResults);
    renderPagination(totalCount, page, limit);
    syncItemsToListHistory(historyMode);
    if(overlay) overlay.style.display = 'none';
    return;
  }

  // Normal mode (fast page fetch): clear full-sort cache so first click re-fetches full list.
  _clearGlobalColumnSortCache();

  const params = new URLSearchParams(baseParams.toString());
  params.set('page', currentPage);
  params.set('limit', currentLimit);
  params.set('_t', Date.now()); // Cache-busting timestamp

  let j = {results:[], total:0, unique_items:0, total_quantity:0, page:1, limit:currentLimit};
  try{
    const resp = await fetch('/api/bol_items?'+params.toString(), {
      cache: 'no-store' // Disable browser cache
    });
    if(!resp.ok){
      console.error('Failed to load items', resp.status);
    } else {
      j = await resp.json();
      console.log('[LOAD_ITEMS] Fetched', j.results?.length, 'items from API');
      const testItem = j.results?.find(r => r.upc === '777000000013');
      if (testItem) {
        console.log('[LOAD_ITEMS] UPC 777000000013 data:', {
          listed_amazon: testItem.listed_amazon,
          listed_ebay: testItem.listed_ebay,
          listed_facebook: testItem.listed_facebook,
          listed_amazon_date: testItem.listed_amazon_date
        });
      }
    }
  }catch(err){
    console.error('Error fetching /api/bol_items:', err);
  }

  const results = Array.isArray(j.results) ? j.results : [];
  const page = Number(j.page || 1);
  currentPage = page;
  const limit = Number(j.limit || 25);
  currentLimit = limit;
  syncPerPageDropdown();

  const filtered_count = Number(j.total || 0);
  const filtered_unique_upcs = Number(j.unique_items || 0);
  const filtered_total_quantity = Number(j.total_quantity || 0);

  updateSummary(filtered_count, filtered_unique_upcs, filtered_total_quantity);
  lastRenderedResults = results;
  await hydrateListingAgentQueueState(results);
  render(results);
  renderPagination(filtered_count, page, limit);
  syncItemsToListHistory(historyMode);

  if(overlay) overlay.style.display = 'none';
}

// per-page selector wiring
const perPageSel = document.getElementById('per-page-select');
if (perPageSel) {
  perPageSel.addEventListener('change', (e)=>{
    const val = parseInt(e.target.value, 10);
    currentLimit = (!isNaN(val) && val>0) ? val : 25;
    currentPage = 1; // reset to first page when page size changes
    loadItems(e, { historyMode: 'push' });
  });
}

window.addEventListener('popstate', async (e) => {
  restoreItemsToListStateFromURL(new URLSearchParams(window.location.search));
  await loadItems(null, { historyMode: 'replace' });
  const scrollY = e && e.state ? Number(e.state.scrollY) : NaN;
  if(Number.isFinite(scrollY)){
    window.requestAnimationFrame(() => window.scrollTo(0, scrollY));
  }
});

