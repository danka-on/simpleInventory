"""Backup name and image lookups for Item Prep when the BOL has no row."""

import datetime
import json
import sqlite3
import requests
from flask import jsonify, request
from . import (
    amazon_catalog as ss_amazon_catalog, database as ss_database, ebay_catalog as ss_ebay_catalog,
    errors as ss_errors, normalization as ss_normalization,
    runtime as ss_runtime, warehouse_nameless as ss_warehouse_nameless,
    warehouse_receiving as ss_warehouse_receiving,
)

UPCITEMDB_URL = 'https://api.upcitemdb.com/prod/trial/lookup'
EXTERNAL_TIMEOUT = 10

# UPCitemdb's free tier allows about 100 lookups a day per address, so every
# online answer is kept. A miss is asked again only after this long.
MISS_RETRY = datetime.timedelta(days=1)

SOURCE_LABELS = {
    'rawbol': 'Macy BOL',
    'bol': 'BOL',
    'ebay': 'eBay store',
    'amazon': 'Amazon store',
    'custom_registry': 'saved custom item',
    'searchrack': 'warehouse',
    'amazon_catalog': 'Amazon catalog',
    'ebay_browse': 'eBay listings',
    'upcitemdb': 'UPCitemdb',
}


def _is_valid_gtin(code):
    """True for a UPC-A/EAN/GTIN whose check digit adds up."""
    digits = str(code or '').strip()
    if not digits.isdigit() or len(digits) not in (8, 12, 13, 14):
        return False
    body, check = digits[:-1], int(digits[-1])
    total = sum(int(d) * (3 if i % 2 == 0 else 1) for i, d in enumerate(reversed(body)))
    return (10 - total % 10) % 10 == check


def _gtin_for(upc):
    """The code online catalogs file this barcode under, or '' when there is none."""
    base = str(upc or '').split('-')[0].strip()
    if base.isdigit() and len(base) < 12:
        base = base.zfill(12)
    return base if _is_valid_gtin(base) else ''


def _candidate(source, title, image_url):
    title = ' '.join(str(title or '').split())
    if not title or ss_warehouse_nameless._title_is_meaningless(title):
        return None
    return {
        'source': source,
        'source_label': SOURCE_LABELS.get(source, source),
        'title': title,
        'image_url': str(image_url or '').strip(),
    }


def _local_candidate(upc):
    """Names this app already holds outside bol_items: raw BOL, stores, registry, shelf."""
    lookup = ss_warehouse_receiving._add_item_screening_lookup(upc)
    return _candidate(lookup.get('title_source') or 'local', lookup.get('title'), lookup.get('image_url'))


def _amazon_candidate(gtin):
    from sp_api.api import CatalogItems
    credentials, _, marketplace_id, marketplace = ss_amazon_catalog._amazon_spapi_context()
    id_type = 'EAN' if len(gtin) == 13 else ('GTIN' if len(gtin) == 14 else 'UPC')
    catalog = CatalogItems(credentials=credentials, marketplace=marketplace, version='2022-04-01')
    response = catalog.search_catalog_items(
        identifiers=[gtin], identifiersType=id_type, marketplaceIds=[marketplace_id],
        includedData=['summaries', 'images'], pageSize=3)
    for item in (response.payload or {}).get('items') or []:
        summaries = item.get('summaries') or [{}]
        image_url = ''
        pictures = [picture for group in item.get('images') or [] for picture in group.get('images') or []]
        main = [picture for picture in pictures if picture.get('variant') == 'MAIN'] or pictures
        if main:
            image_url = max(main, key=lambda picture: picture.get('height') or 0).get('link') or ''
        found = _candidate('amazon_catalog', summaries[0].get('itemName'), image_url)
        if found:
            return found
    return None


def _ebay_candidate(gtin):
    # The Browse API's gtin filter misses most listings; the barcode as a
    # keyword finds the sellers who typed it into the listing.
    response = ss_ebay_catalog._ebay_buy_api_request(
        'GET', '/buy/browse/v1/item_summary/search', params={'q': gtin, 'limit': 3}, timeout=EXTERNAL_TIMEOUT)
    if response.status_code >= 400:
        raise RuntimeError(f'eBay returned HTTP {response.status_code}')
    for item in (response.json() if response.text else {}).get('itemSummaries') or []:
        image = (item.get('image') or {}).get('imageUrl') or ''
        found = _candidate('ebay_browse', item.get('title'), image)
        if found:
            return found
    return None


def _upcitemdb_candidate(gtin):
    response = requests.get(UPCITEMDB_URL, params={'upc': gtin}, timeout=EXTERNAL_TIMEOUT)
    if response.status_code == 429:
        raise RuntimeError('daily free limit reached')
    if response.status_code == 400:
        return None
    if response.status_code >= 400:
        raise RuntimeError(f'UPCitemdb returned HTTP {response.status_code}')
    for item in (response.json() if response.text else {}).get('items') or []:
        images = item.get('images') or []
        found = _candidate('upcitemdb', item.get('title'), images[0] if images else '')
        if found:
            return found
    return None


EXTERNAL_PROVIDERS = (
    ('amazon_catalog', '_amazon_candidate'),
    ('ebay_browse', '_ebay_candidate'),
    ('upcitemdb', '_upcitemdb_candidate'),
)


def _lookup_external(gtin):
    """Ask each online catalog in turn; stop at the first real name."""
    import sys
    module = sys.modules[__name__]
    tried = []
    for source, function_name in EXTERNAL_PROVIDERS:
        label = SOURCE_LABELS[source]
        try:
            found = getattr(module, function_name)(gtin)
        except Exception as e:
            tried.append({'source': source, 'label': label, 'result': 'error', 'detail': str(e)[:120]})
            continue
        if found:
            tried.append({'source': source, 'label': label, 'result': 'found'})
            return found, tried
        tried.append({'source': source, 'label': label, 'result': 'no match'})
    return None, tried


def _ensure_lookup_cache(cur):
    cur.execute('''
        CREATE TABLE IF NOT EXISTS prep_fallback_lookups (
            gtin TEXT PRIMARY KEY,
            source TEXT NOT NULL DEFAULT '',
            title TEXT NOT NULL DEFAULT '',
            image_url TEXT NOT NULL DEFAULT '',
            tried TEXT NOT NULL DEFAULT '[]',
            looked_up_at TEXT NOT NULL
        )
    ''')


def _cached_lookup(gtin):
    with ss_database.db_connection('bol.db') as conn:
        cur = conn.cursor()
        _ensure_lookup_cache(cur)
        row = cur.execute(
            'SELECT source, title, image_url, tried, looked_up_at FROM prep_fallback_lookups WHERE gtin = ?',
            (gtin,)).fetchone()
    if not row:
        return None
    source, title, image_url, tried, looked_up_at = row
    try:
        tried = json.loads(tried or '[]')
    except ValueError:
        tried = []
    found = _candidate(source, title, image_url) if source else None
    if not found:
        age = datetime.datetime.utcnow() - (ss_normalization._parse_iso_utc_naive(looked_up_at) or datetime.datetime.min)
        if age > MISS_RETRY:
            return None
    return {'candidate': found, 'tried': tried}


def _store_lookup(gtin, found, tried):
    with ss_database.db_connection('bol.db') as conn:
        cur = conn.cursor()
        _ensure_lookup_cache(cur)
        cur.execute('''
            INSERT OR REPLACE INTO prep_fallback_lookups (gtin, source, title, image_url, tried, looked_up_at)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (gtin, (found or {}).get('source', ''), (found or {}).get('title', ''),
              (found or {}).get('image_url', ''), json.dumps(tried), datetime.datetime.utcnow().isoformat()))


def api_prep_fallback_lookup():
    """Find a name and image for a barcode the BOL does not know."""
    try:
        upc = ss_normalization._normalize_upc(request.args.get('upc'))
        if not upc:
            return jsonify({'success': False, 'found': False, 'error': 'Missing upc'}), 400

        local = _local_candidate(upc)
        if local:
            return jsonify({'success': True, 'found': True, 'candidate': local, 'tried': [], 'cached': False})

        gtin = _gtin_for(upc)
        if not gtin:
            return jsonify({
                'success': True, 'found': False, 'reason': 'not_a_upc', 'tried': [],
                'message': 'Not a standard UPC (the check digit fails) - probably a store-internal code, '
                           'so online catalogs cannot know it.',
            })

        cached = _cached_lookup(gtin)
        if cached is not None:
            found, tried, was_cached = cached['candidate'], cached['tried'], True
        else:
            found, tried = _lookup_external(gtin)
            was_cached = False
            # Errors are not answers; only store a result every provider gave.
            if found or not any(attempt['result'] == 'error' for attempt in tried):
                _store_lookup(gtin, found, tried)

        payload = {'success': True, 'found': bool(found), 'candidate': found, 'tried': tried, 'cached': was_cached}
        if not found:
            payload['message'] = 'No backup catalog knows this barcode.'
        return jsonify(payload)
    except Exception as e:
        return jsonify({'success': False, 'found': False, 'error': ss_errors._safe_error(e, 'items_prep:fallback_lookup')}), 500


def api_prep_fallback_adopt():
    """Keep a backup catalog's name and image as a custom BOL item so prep can continue."""
    try:
        data = request.get_json(silent=True) or {}
        upc = ss_normalization._normalize_upc(data.get('upc'))
        title = ' '.join(str(data.get('title') or '').split())
        image_url = str(data.get('image_url') or '').strip()
        if not upc or not title:
            return jsonify({'success': False, 'error': 'Barcode and title are required'}), 400
        rejection = ss_warehouse_nameless._manual_title_rejection(title)
        if rejection:
            return jsonify({'success': False, 'error': rejection}), 400
        if image_url and not image_url.lower().startswith(('https://', 'http://', '/static/')):
            return jsonify({'success': False, 'error': 'Image must be a web address'}), 400

        with ss_database.db_connection('bol.db') as conn:
            cur = conn.cursor()
            ss_warehouse_receiving._ensure_custom_item_registry(cur)
            exists = cur.execute('SELECT 1 FROM bol_items WHERE upc = ? COLLATE NOCASE', (upc,)).fetchone()
            if exists:
                # Never overwrite a manifest row; only fill what it is missing.
                cur.execute('''
                    UPDATE bol_items
                    SET item_description = CASE WHEN TRIM(COALESCE(item_description, '')) = '' THEN ? ELSE item_description END,
                        image_url = CASE WHEN TRIM(COALESCE(image_url, '')) IN ('', 'nan', 'None') THEN ? ELSE image_url END
                    WHERE upc = ? COLLATE NOCASE
                ''', (title, image_url, upc))
            else:
                cur.execute('''
                    INSERT INTO bol_items (upc, item_description, image_url, lot_number, bol_number, import_date)
                    VALUES (?, ?, ?, NULL, 'CUSTOM', datetime('now'))
                ''', (upc, title, image_url))
            # A catalog answer is a default, not a deliberate override.
            cur.execute('''
                INSERT INTO custom_item_registry (upc, item_description, image_url, reserved_at, updated_at)
                VALUES (?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                ON CONFLICT(upc) DO NOTHING
            ''', (upc, title, image_url))

        # api_bol_lookup is cached per full query string (lot_number included),
        # so the scan that led here left a "not found" the page would get back
        # on its very next lookup. Its keys are hashed, so clear the cache.
        try:
            ss_runtime.cache.clear()
        except Exception:
            pass
        return jsonify({'success': True, 'upc': upc, 'title': title, 'image_url': image_url})
    except Exception as e:
        return jsonify({'success': False, 'error': ss_errors._safe_error(e, 'items_prep:fallback_adopt')}), 500
