"""Pricing for Sweet Shelves."""

import datetime
import os
import requests
import xml.etree.ElementTree as ET
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from flask import jsonify, request
from . import (
    amazon_catalog as ss_amazon_catalog, amazon_listing as ss_amazon_listing, database as ss_database,
    errors as ss_errors, listing_settings as ss_listing_settings,
)


def _pricemaster_now_iso():
    # UTC, second precision keeps it readable and stable for sorting.
    return datetime.datetime.utcnow().replace(microsecond=0).isoformat() + 'Z'


def _pricemaster_parse_decimal(val):
    try:
        if val is None:
            return None
        if isinstance(val, Decimal):
            return val
        if isinstance(val, (int, float)) and not isinstance(val, bool):
            return Decimal(str(val))
        s = str(val).strip()
        if s == '':
            return None
        s = s.replace('$', '').replace(',', '')
        return Decimal(s)
    except (InvalidOperation, Exception):
        return None


def _pricemaster_money_2dp(val: Decimal):
    try:
        if val is None:
            return None
        return val.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    except Exception:
        return None


def _pricemaster_compute_new_price(old_price: Decimal, mode: str, value: Decimal):
    if old_price is None or value is None:
        return None
    mode = (mode or '').strip().lower()
    if mode not in ('delta', 'percent'):
        mode = 'delta'

    try:
        if mode == 'delta':
            nxt = old_price + value
        else:
            nxt = old_price * (Decimal('1') + (value / Decimal('100')))
        if nxt < 0:
            nxt = Decimal('0')
        return _pricemaster_money_2dp(nxt)
    except Exception:
        return None


def _pricemaster_init_tables(cur):
    try:
        cur.execute('PRAGMA journal_mode=WAL')
    except Exception:
        pass

    cur.execute('''
        CREATE TABLE IF NOT EXISTS listing_meta (
            platform TEXT NOT NULL,
            listing_key TEXT NOT NULL,
            first_seen_at TEXT,
            last_price_change_at TEXT,
            last_price_change_price REAL,
            updated_at TEXT,
            PRIMARY KEY (platform, listing_key)
        )
    ''')

    cur.execute('''
        CREATE TABLE IF NOT EXISTS price_changes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            platform TEXT NOT NULL,
            listing_key TEXT NOT NULL,
            old_price REAL,
            new_price REAL,
            changed_at TEXT NOT NULL,
            adjustment_mode TEXT,
            adjustment_value REAL,
            success INTEGER NOT NULL DEFAULT 1,
            error TEXT
        )
    ''')

    cur.execute('CREATE INDEX IF NOT EXISTS idx_price_changes_key_time ON price_changes(platform, listing_key, changed_at)')

    cur.execute('''
        CREATE TABLE IF NOT EXISTS price_feed_jobs (
            feed_id TEXT PRIMARY KEY,
            marketplace_id TEXT,
            submitted_at TEXT,
            status TEXT,
            last_checked_at TEXT,
            result_document_id TEXT,
            items_json TEXT,
            error TEXT
        )
    ''')


def _pricemaster_touch_meta(rows):
    """Insert first_seen_at for listings if missing (platform, key, first_seen_at)."""
    if not rows:
        return
    now = _pricemaster_now_iso()
    with ss_database.db_connection('pricemaster.db') as conn:
        cur = conn.cursor()
        _pricemaster_init_tables(cur)
        cur.executemany('''
            INSERT OR IGNORE INTO listing_meta (platform, listing_key, first_seen_at, updated_at)
            VALUES (?, ?, ?, ?)
        ''', [(p, k, fs or now, now) for (p, k, fs) in rows if p and k])


def _pricemaster_get_meta_map(platform: str, keys: list):
    """Return dict: listing_key -> meta row."""
    out = {}
    platform = (platform or '').strip().lower()
    keys = [k for k in (keys or []) if k]
    if not platform or not keys:
        return out

    # SQLite default max vars is commonly 999; batch to be safe.
    CHUNK = 900
    with ss_database.db_connection('pricemaster.db') as conn:
        cur = conn.cursor()
        _pricemaster_init_tables(cur)
        for i in range(0, len(keys), CHUNK):
            batch = keys[i:i+CHUNK]
            ph = ','.join(['?'] * len(batch))
            cur.execute(f'''
                SELECT listing_key, first_seen_at, last_price_change_at, last_price_change_price
                FROM listing_meta
                WHERE platform = ? AND listing_key IN ({ph})
            ''', [platform] + batch)
            for r in cur.fetchall():
                out[r['listing_key']] = {
                    'first_seen_at': r['first_seen_at'],
                    'last_price_change_at': r['last_price_change_at'],
                    'last_price_change_price': r['last_price_change_price'],
                }
    return out


def _pricemaster_save_feed_job(feed_id, marketplace_id, items, error=None, status='SUBMITTED'):
    if not feed_id:
        return
    now = _pricemaster_now_iso()
    try:
        import json as _json
        items_json = _json.dumps(items or [], ensure_ascii=True, default=str)
    except Exception:
        items_json = '[]'
    with ss_database.db_connection('pricemaster.db') as conn:
        cur = conn.cursor()
        _pricemaster_init_tables(cur)
        cur.execute('''
            INSERT INTO price_feed_jobs (feed_id, marketplace_id, submitted_at, status, last_checked_at, result_document_id, items_json, error)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(feed_id) DO UPDATE SET
                marketplace_id = excluded.marketplace_id,
                status = excluded.status,
                last_checked_at = excluded.last_checked_at,
                result_document_id = excluded.result_document_id,
                items_json = excluded.items_json,
                error = excluded.error
        ''', (feed_id, marketplace_id, now, status or 'SUBMITTED', now, None, items_json, error))


def _pricemaster_get_feed_job(feed_id):
    if not feed_id:
        return None
    with ss_database.db_connection('pricemaster.db') as conn:
        cur = conn.cursor()
        _pricemaster_init_tables(cur)
        cur.execute('''
            SELECT feed_id, marketplace_id, submitted_at, status, last_checked_at, result_document_id, items_json, error
            FROM price_feed_jobs
            WHERE feed_id = ?
        ''', (feed_id,))
        row = cur.fetchone()
        return dict(row) if row else None


def _pricemaster_update_feed_job(feed_id, *, status=None, error=None, result_document_id=None):
    if not feed_id:
        return
    now = _pricemaster_now_iso()
    with ss_database.db_connection('pricemaster.db') as conn:
        cur = conn.cursor()
        _pricemaster_init_tables(cur)
        cur.execute('''
            UPDATE price_feed_jobs
            SET status = COALESCE(?, status),
                last_checked_at = ?,
                result_document_id = COALESCE(?, result_document_id),
                error = COALESCE(?, error)
            WHERE feed_id = ?
        ''', (status, now, result_document_id, error, feed_id))


def _pricemaster_record_change(*, platform, listing_key, old_price, new_price, mode, value, success, error=None):
    now = _pricemaster_now_iso()
    with ss_database.db_connection('pricemaster.db') as conn:
        cur = conn.cursor()
        _pricemaster_init_tables(cur)
        cur.execute('''
            INSERT INTO price_changes (platform, listing_key, old_price, new_price, changed_at, adjustment_mode, adjustment_value, success, error)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            platform, listing_key,
            None if old_price is None else float(old_price),
            None if new_price is None else float(new_price),
            now,
            mode,
            None if value is None else float(value),
            1 if success else 0,
            (str(error) if error else None)
        ))

        if success:
            cur.execute('''
                INSERT INTO listing_meta (platform, listing_key, last_price_change_at, last_price_change_price, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(platform, listing_key) DO UPDATE SET
                    last_price_change_at = excluded.last_price_change_at,
                    last_price_change_price = excluded.last_price_change_price,
                    updated_at = excluded.updated_at
            ''', (
                platform, listing_key, now,
                None if new_price is None else float(new_price),
                now
            ))


def _pricemaster_ebay_trading_call(call_name: str, xml_payload: str, timeout=30):
    token = os.getenv("EBAY_OLDAUTH_TOKEN") or ''
    if not token.strip():
        raise Exception("Missing EBAY_OLDAUTH_TOKEN (Trading API token)")

    headers = {
        "X-EBAY-API-SITEID": "0",
        "X-EBAY-API-COMPATIBILITY-LEVEL": "967",
        "X-EBAY-API-CALL-NAME": call_name,
        "X-EBAY-API-DEV-NAME": os.getenv("EBAY_PROD_DEV_ID"),
        "X-EBAY-API-APP-NAME": os.getenv("EBAY_PROD_APP_ID"),
        "X-EBAY-API-CERT-NAME": os.getenv("EBAY_PROD_CERT_ID"),
        "Content-Type": "text/xml",
    }
    resp = requests.post("https://api.ebay.com/ws/api.dll", headers=headers, data=xml_payload, timeout=timeout)
    return resp


def _pricemaster_ebay_revise_prices_bulk(updates: list, currency='USD'):
    """
    updates: list of dicts {item_id, new_price}
    Returns: (ok_item_ids_set, failed_map[item_id]=error)
    """
    updates = [u for u in (updates or []) if u.get('item_id') and u.get('new_price') is not None]
    ok = set()
    failed = {}
    if not updates:
        return ok, failed

    token = os.getenv("EBAY_OLDAUTH_TOKEN") or ''
    token = token.strip()
    if not token:
        for u in updates:
            failed[u['item_id']] = 'Missing EBAY_OLDAUTH_TOKEN'
        return ok, failed

    # Build a single ReviseInventoryStatus call for a batch.
    import html as _html
    inv_chunks = []
    for u in updates:
        item_id = str(u['item_id']).strip()
        sku = str(u.get('sku') or '').strip()
        new_price = float(u['new_price'])
        sku_xml = f"<SKU>{_html.escape(sku)}</SKU>" if sku else ""
        inv_chunks.append(f'''
          <InventoryStatus>
            <ItemID>{item_id}</ItemID>
            {sku_xml}
            <StartPrice currencyID="{currency}">{new_price:.2f}</StartPrice>
          </InventoryStatus>
        ''')

    xml_payload = f'''<?xml version="1.0" encoding="utf-8"?>
      <ReviseInventoryStatusRequest xmlns="urn:ebay:apis:eBLBaseComponents">
        <RequesterCredentials>
          <eBayAuthToken>{token}</eBayAuthToken>
        </RequesterCredentials>
        <WarningLevel>High</WarningLevel>
        {''.join(inv_chunks)}
      </ReviseInventoryStatusRequest>
    '''

    resp = _pricemaster_ebay_trading_call('ReviseInventoryStatus', xml_payload, timeout=30)
    if resp.status_code != 200:
        msg = f"HTTP {resp.status_code}"
        try:
            msg = msg + f": {resp.text[:200]}"
        except Exception:
            pass
        for u in updates:
            failed[str(u['item_id']).strip()] = msg
        return ok, failed

    # Parse response
    ns = {'ebay': 'urn:ebay:apis:eBLBaseComponents'}
    try:
        root = ET.fromstring(resp.text)
    except Exception:
        for u in updates:
            failed[str(u['item_id']).strip()] = 'Invalid XML response from eBay'
        return ok, failed

    ack = (root.findtext('.//ebay:Ack', default='', namespaces=ns) or '').strip()
    errors = root.findall('.//ebay:Errors', ns) or []

    if ack in ('Success', 'Warning', 'PartialFailure'):
        # Best-effort map errors to ItemID via ErrorParameters.
        for err in errors:
            msg = (err.findtext('ebay:LongMessage', default='', namespaces=ns) or err.findtext('ebay:ShortMessage', default='', namespaces=ns) or 'eBay error').strip()
            mapped = False
            for ep in err.findall('ebay:ErrorParameters', ns) or []:
                v = (ep.findtext('ebay:Value', default='', namespaces=ns) or '').strip()
                if v and v.isdigit() and len(v) >= 8:  # item ids are typically long digits
                    failed[v] = msg
                    mapped = True
            if not mapped and ack != 'PartialFailure' and msg:
                # If we can't map and it's not partial, treat as a general failure.
                for u in updates:
                    failed[str(u['item_id']).strip()] = msg
                return ok, failed

        # Anything not marked failed is treated as success.
        for u in updates:
            item_id = str(u['item_id']).strip()
            if item_id and item_id not in failed:
                ok.add(item_id)
        return ok, failed

    # Failure
    msg = 'eBay API error'
    if errors:
        msg = (errors[0].findtext('ebay:LongMessage', default='', namespaces=ns) or errors[0].findtext('ebay:ShortMessage', default='', namespaces=ns) or msg).strip()
    for u in updates:
        failed[str(u['item_id']).strip()] = msg
    return ok, failed


def _pricemaster_ebay_get_item_price(item_id: str):
    """Fetch current price for an eBay listing via Trading API GetItem."""
    item_id = (item_id or '').strip()
    if not item_id:
        raise Exception('Missing item_id')

    token = os.getenv("EBAY_OLDAUTH_TOKEN") or ''
    token = token.strip()
    if not token:
        raise Exception("Missing EBAY_OLDAUTH_TOKEN (Trading API token)")

    xml_payload = f'''<?xml version="1.0" encoding="utf-8"?>
      <GetItemRequest xmlns="urn:ebay:apis:eBLBaseComponents">
        <RequesterCredentials>
          <eBayAuthToken>{token}</eBayAuthToken>
        </RequesterCredentials>
        <ItemID>{item_id}</ItemID>
        <DetailLevel>ReturnAll</DetailLevel>
        <IncludeItemSpecifics>false</IncludeItemSpecifics>
      </GetItemRequest>
    '''

    resp = _pricemaster_ebay_trading_call('GetItem', xml_payload, timeout=30)
    if resp.status_code != 200:
        raise Exception(f"HTTP {resp.status_code}: {resp.text[:200]}")

    ns = {'ebay': 'urn:ebay:apis:eBLBaseComponents'}
    try:
        root = ET.fromstring(resp.text)
    except Exception:
        raise Exception('Invalid XML response from eBay')

    ack = (root.findtext('.//ebay:Ack', default='', namespaces=ns) or '').strip()
    if ack not in ('Success', 'Warning', 'PartialFailure'):
        # Try to extract error
        err = root.find('.//ebay:Errors', ns)
        msg = 'eBay API error'
        if err is not None:
            msg = (err.findtext('ebay:LongMessage', default='', namespaces=ns) or err.findtext('ebay:ShortMessage', default='', namespaces=ns) or msg).strip()
        raise Exception(msg)

    price_el = (
        root.find('.//ebay:CurrentPrice', ns)
        or root.find('.//ebay:StartPrice', ns)
        or root.find('.//ebay:BuyItNowPrice', ns)
    )
    if price_el is None or price_el.text is None:
        raise Exception('Price not found in eBay response')

    currency = price_el.attrib.get('currencyID') or 'USD'
    price_d = _pricemaster_parse_decimal(price_el.text)
    if price_d is None:
        raise Exception('Invalid price value returned by eBay')

    return float(_pricemaster_money_2dp(price_d)), currency


def _pricemaster_fetch_ebay_rows(item_ids: list):
    out = {}
    item_ids = [str(x).strip() for x in (item_ids or []) if str(x).strip()]
    if not item_ids:
        return out
    CHUNK = 900
    with ss_database.db_connection('ebayStore.db') as conn:
        cur = conn.cursor()
        for i in range(0, len(item_ids), CHUNK):
            batch = item_ids[i:i+CHUNK]
            ph = ','.join(['?'] * len(batch))
            cur.execute(f'''
                SELECT ItemID, Title, UPC, Price, Quantity, URL, List_State, List_Date
                FROM INVENTORY
                WHERE TRIM(COALESCE(ItemID,'')) IN ({ph})
                LIMIT {len(batch)}
            ''', batch)
            for r in cur.fetchall():
                out[(r['ItemID'] or '').strip()] = dict(r)
    return out


def _pricemaster_fetch_amazon_rows(skus: list):
    out = {}
    skus = [str(x).strip() for x in (skus or []) if str(x).strip()]
    if not skus:
        return out
    CHUNK = 900
    with ss_database.db_connection('amazonStore.db') as conn:
        cur = conn.cursor()
        for i in range(0, len(skus), CHUNK):
            batch = skus[i:i+CHUNK]
            ph = ','.join(['?'] * len(batch))
            cur.execute(f'''
                SELECT SKU, ASIN, UPC, TITLE, PRICE, QUANTITY, STATUS, CONDITION, FULFILLMENT_CHANNEL, LAST_UPDATED
                FROM ITEMS
                WHERE TRIM(COALESCE(SKU,'')) IN ({ph})
                LIMIT {len(batch)}
            ''', batch)
            for r in cur.fetchall():
                out[(r['SKU'] or '').strip()] = dict(r)
    return out


def api_pricemaster_listings():
    """Return current store listings (eBay + Amazon) with local meta (first seen + last price change)."""
    try:
        platform_param = (request.args.get('platform') or '').strip().lower()
        include_inactive = (request.args.get('include_inactive') or '').strip() in ('1', 'true', 'yes')

        want_ebay = (platform_param in ('', 'all', 'ebay'))
        want_amazon = (platform_param in ('', 'all', 'amazon'))

        items = []
        ebay_keys = []
        amazon_keys = []
        touch_rows = []

        if want_ebay:
            with ss_database.db_connection('ebayStore.db') as conn:
                cur = conn.cursor()
                if include_inactive:
                    cur.execute('''
                        SELECT Title, ItemID, SKU, Price, Quantity, URL, List_State, List_Date, UPC
                        FROM INVENTORY
                        WHERE TRIM(COALESCE(ItemID,'')) != ''
                        ORDER BY ID DESC
                        LIMIT 6000
                    ''')
                else:
                    cur.execute('''
                        SELECT Title, ItemID, SKU, Price, Quantity, URL, List_State, List_Date, UPC
                        FROM INVENTORY
                        WHERE TRIM(COALESCE(ItemID,'')) != ''
                          AND (TRIM(COALESCE(List_State,'')) = 'Active')
                        ORDER BY ID DESC
                        LIMIT 6000
                    ''')
                for r in cur.fetchall():
                    item_id = (r['ItemID'] or '').strip()
                    if not item_id:
                        continue
                    price_d = _pricemaster_parse_decimal(r['Price'])
                    price = float(_pricemaster_money_2dp(price_d)) if price_d is not None else None
                    qty = ss_listing_settings._listingagent_parse_int(r['Quantity'], None)
                    sku = (r['SKU'] or '').strip()
                    if sku.lower() == 'none':
                        sku = ''
                    upc = (r['UPC'] or '').strip()
                    list_date = (r['List_Date'] or '').strip()

                    items.append({
                        'platform': 'ebay',
                        'listing_key': item_id,
                        'item_id': item_id,
                        'sku': sku,
                        'asin': '',
                        'upc': upc,
                        'title': (r['Title'] or '').strip(),
                        'price': price,
                        'currency': 'USD',
                        'quantity': qty,
                        'status': (r['List_State'] or '').strip(),
                        'url': (r['URL'] or '').strip(),
                        'list_date': list_date,
                        'last_updated': '',
                    })
                    ebay_keys.append(item_id)
                    touch_rows.append(('ebay', item_id, list_date or _pricemaster_now_iso()))

        if want_amazon:
            with ss_database.db_connection('amazonStore.db') as conn:
                cur = conn.cursor()
                if include_inactive:
                    cur.execute('''
                        SELECT TITLE, SKU, ASIN, UPC, PRICE, QUANTITY, STATUS, LAST_UPDATED
                        FROM ITEMS
                        WHERE TRIM(COALESCE(SKU,'')) != ''
                        ORDER BY ID DESC
                        LIMIT 6000
                    ''')
                else:
                    cur.execute('''
                        SELECT TITLE, SKU, ASIN, UPC, PRICE, QUANTITY, STATUS, LAST_UPDATED
                        FROM ITEMS
                        WHERE TRIM(COALESCE(SKU,'')) != ''
                          AND (TRIM(COALESCE(STATUS,'')) = 'Active')
                        ORDER BY ID DESC
                        LIMIT 6000
                    ''')
                for r in cur.fetchall():
                    sku = (r['SKU'] or '').strip()
                    if not sku:
                        continue
                    asin = (r['ASIN'] or '').strip()
                    upc = (r['UPC'] or '').strip()
                    last_updated = (r['LAST_UPDATED'] or '').strip()

                    price_d = _pricemaster_parse_decimal(r['PRICE'])
                    price = float(_pricemaster_money_2dp(price_d)) if price_d is not None else None
                    qty = ss_listing_settings._listingagent_parse_int(r['QUANTITY'], None)

                    items.append({
                        'platform': 'amazon',
                        'listing_key': sku,
                        'item_id': '',
                        'sku': sku,
                        'asin': asin,
                        'upc': upc,
                        'title': (r['TITLE'] or '').strip(),
                        'price': price,
                        'currency': 'USD',
                        'quantity': qty,
                        'status': (r['STATUS'] or '').strip(),
                        'url': '',
                        'list_date': '',
                        'last_updated': last_updated,
                    })
                    amazon_keys.append(sku)
                    touch_rows.append(('amazon', sku, last_updated or _pricemaster_now_iso()))

        # Ensure meta rows exist for sorting (first_seen_at), then attach meta data.
        _pricemaster_touch_meta(touch_rows)
        ebay_meta = _pricemaster_get_meta_map('ebay', ebay_keys)
        amazon_meta = _pricemaster_get_meta_map('amazon', amazon_keys)

        for it in items:
            key = it.get('listing_key') or ''
            meta = (ebay_meta.get(key) if it.get('platform') == 'ebay' else amazon_meta.get(key)) or {}
            it['first_seen_at'] = meta.get('first_seen_at') or ''
            it['last_price_change_at'] = meta.get('last_price_change_at') or ''
            it['last_price_change_price'] = meta.get('last_price_change_price')

        return jsonify({'success': True, 'items': items})
    except Exception as e:
        return jsonify({'success': False, 'error': ss_errors._safe_error(e, 'pricemaster:listings')}), 500


def api_pricemaster_bulk_update():
    """Bulk update prices on the marketplaces and record local price-change history."""
    try:
        data = request.json or {}
        adj = data.get('adjustment') or {}
        mode = (adj.get('mode') or 'delta').strip().lower()
        if mode not in ('delta', 'percent'):
            return jsonify({'success': False, 'error': 'Invalid adjustment mode'}), 400

        value_d = _pricemaster_parse_decimal(adj.get('value'))
        if value_d is None:
            return jsonify({'success': False, 'error': 'Adjustment value is required'}), 400

        targets = data.get('targets') or []
        if not isinstance(targets, list) or len(targets) == 0:
            return jsonify({'success': False, 'error': 'No targets provided'}), 400

        dry_run = bool(data.get('dry_run', False))

        ebay_ids = []
        amazon_skus = []
        for t in targets:
            p = (t.get('platform') or '').strip().lower()
            k = (t.get('listing_key') or '').strip()
            if not p or not k:
                continue
            if p == 'ebay':
                ebay_ids.append(k)
            elif p == 'amazon':
                amazon_skus.append(k)

        ebay_rows = _pricemaster_fetch_ebay_rows(ebay_ids) if ebay_ids else {}
        amazon_rows = _pricemaster_fetch_amazon_rows(amazon_skus) if amazon_skus else {}

        results = []

        # ---- eBay (Trading API) ----
        ebay_updates = []
        for item_id in ebay_ids:
            row = ebay_rows.get(item_id)
            if not row:
                results.append({'platform': 'ebay', 'listing_key': item_id, 'success': False, 'error': 'Listing not found in ebayStore.db'})
                continue

            old_d = _pricemaster_parse_decimal(row.get('Price'))
            old_d = _pricemaster_money_2dp(old_d) if old_d is not None else None
            if old_d is None:
                results.append({'platform': 'ebay', 'listing_key': item_id, 'success': False, 'error': 'Missing current price'})
                continue

            new_d = _pricemaster_compute_new_price(old_d, mode, value_d)
            if new_d is None:
                results.append({'platform': 'ebay', 'listing_key': item_id, 'success': False, 'error': 'Could not compute new price'})
                continue

            results.append({'platform': 'ebay', 'listing_key': item_id, 'success': True, 'old_price': float(old_d), 'new_price': float(new_d), '_dry': True})
            if not dry_run:
                sku = (row.get('SKU') or '').strip()
                ebay_updates.append({'item_id': item_id, 'sku': sku, 'new_price': float(new_d), 'old_price': float(old_d)})

        if not dry_run and ebay_updates:
            # Batch into modest chunks to avoid huge Trading API payloads.
            ok_ids = set()
            fail_map = {}
            BATCH = 25
            for i in range(0, len(ebay_updates), BATCH):
                batch = ebay_updates[i:i+BATCH]
                ok, failed = _pricemaster_ebay_revise_prices_bulk(batch, currency='USD')
                ok_ids |= set(ok)
                fail_map.update(failed or {})

            # Update local DB + record history
            with ss_database.db_connection('ebayStore.db') as conn:
                cur = conn.cursor()
                for u in ebay_updates:
                    item_id = str(u['item_id']).strip()
                    old_price = u.get('old_price')
                    new_price = u.get('new_price')
                    if item_id in ok_ids:
                        cur.execute('UPDATE INVENTORY SET Price = ? WHERE TRIM(COALESCE(ItemID,\'\')) = ? COLLATE NOCASE', (f"{float(new_price):.2f}", item_id))
                        _pricemaster_record_change(platform='ebay', listing_key=item_id, old_price=old_price, new_price=new_price, mode=mode, value=value_d, success=True)
                    else:
                        err = fail_map.get(item_id) or 'eBay price update failed'
                        _pricemaster_record_change(platform='ebay', listing_key=item_id, old_price=old_price, new_price=new_price, mode=mode, value=value_d, success=False, error=err)

            # Patch results list to reflect real outcomes (replace the earlier _dry placeholders)
            for r in results:
                if r.get('platform') == 'ebay' and r.get('_dry') and not dry_run:
                    item_id = r.get('listing_key')
                    if item_id in ok_ids:
                        r['success'] = True
                    else:
                        r['success'] = False
                        r['error'] = fail_map.get(item_id) or 'eBay price update failed'
                    r.pop('_dry', None)

        # ---- Amazon (SP-API) ----
        amazon_jobs = []
        for sku in amazon_skus:
            row = amazon_rows.get(sku)
            if not row:
                results.append({'platform': 'amazon', 'listing_key': sku, 'success': False, 'error': 'Listing not found in amazonStore.db'})
                continue

            old_d = _pricemaster_parse_decimal(row.get('PRICE'))
            old_d = _pricemaster_money_2dp(old_d) if old_d is not None else None
            if old_d is None:
                results.append({'platform': 'amazon', 'listing_key': sku, 'success': False, 'error': 'Missing current price'})
                continue

            new_d = _pricemaster_compute_new_price(old_d, mode, value_d)
            if new_d is None:
                results.append({'platform': 'amazon', 'listing_key': sku, 'success': False, 'error': 'Could not compute new price'})
                continue

            results.append({'platform': 'amazon', 'listing_key': sku, 'success': True, 'old_price': float(old_d), 'new_price': float(new_d), '_dry': True})
            if not dry_run:
                amazon_jobs.append({
                    'sku': sku,
                    'asin': (row.get('ASIN') or '').strip(),
                    'upc': (row.get('UPC') or '').strip(),
                    'quantity': ss_listing_settings._listingagent_parse_int(row.get('QUANTITY'), 1) or 1,
                    'condition': (row.get('CONDITION') or '').strip(),
                    'fulfillment_channel': (row.get('FULFILLMENT_CHANNEL') or '').strip(),
                    'old_price': float(old_d),
                    'new_price': float(new_d),
                })

        if not dry_run and amazon_jobs:
            settings = ss_listing_settings._listingagent_get_settings()
            credentials, seller_id, marketplace_id, marketplace = ss_amazon_catalog._amazon_spapi_context()
            if marketplace is None:
                # Record failures consistently
                for j in amazon_jobs:
                    _pricemaster_record_change(platform='amazon', listing_key=j['sku'], old_price=j['old_price'], new_price=j['new_price'], mode=mode, value=value_d, success=False, error='Amazon SP-API not available')
                for r in results:
                    if r.get('platform') == 'amazon' and r.get('_dry'):
                        r['success'] = False
                        r['error'] = 'Amazon SP-API not available'
                        r.pop('_dry', None)
            else:
                from sp_api.api import ListingsItems
                li = ListingsItems(credentials=credentials, marketplace=marketplace)

                mp_id = (settings.get('amazon_marketplace_id') or marketplace_id or 'ATVPDKIKX0DER').strip()
                currency = (settings.get('amazon_currency') or 'USD').strip()
                default_product_type = (settings.get('amazon_product_type') or 'PRODUCT').strip()
                requirements = (settings.get('amazon_requirements') or 'LISTING_OFFER_ONLY').strip()
                default_condition = (settings.get('amazon_condition_type') or 'used_good').strip()
                default_fc = (settings.get('amazon_fulfillment_channel_code') or 'DEFAULT').strip()
                offer_audience = ss_amazon_listing._amazon_offer_audience(settings)

                ok_skus = set()
                fail_skus = {}
                product_type_cache = {}
                feed_candidates = []
                for j in amazon_jobs:
                    sku = j['sku']
                    asin = j.get('asin') or ''
                    upc = j.get('upc') or ''
                    qty = int(j.get('quantity') or 1)
                    condition_type = ss_amazon_listing._amazon_normalize_condition_type(j.get('condition'), default_condition)
                    fc_code = ss_amazon_listing._amazon_normalize_fulfillment_channel(j.get('fulfillment_channel'), default_fc)
                    price = float(j.get('new_price'))

                    try:
                        debug = {'attempts': []}
                        product_type = default_product_type
                        product_type = ss_amazon_catalog._amazon_get_listing_product_type(li, seller_id, sku, mp_id, product_type_cache)
                        if not product_type:
                            product_type = ss_amazon_catalog._amazon_get_catalog_product_type(credentials, marketplace, mp_id, asin)
                        product_type = product_type or default_product_type

                        def _append_attempts(plan_name, req_used, pt_used, attempt_debug):
                            try:
                                at = (attempt_debug or {}).get('attempts') or []
                            except Exception:
                                at = []
                            for entry in at:
                                item = {
                                    'plan': plan_name,
                                    'requirements': req_used,
                                    'product_type': pt_used,
                                }
                                if isinstance(entry, dict):
                                    item.update(entry)
                                debug['attempts'].append(item)

                        def _format_err_text(err_obj):
                            try:
                                return ss_amazon_listing._amazon_format_spapi_error(err_obj)
                            except Exception:
                                return str(err_obj or '')

                        def _is_retryable_schema_error(err_text):
                            low = str(err_text or '').lower()
                            return (
                                ('invalidinput' in low)
                                or ('invalid parameters' in low)
                                or ('status=invalid' in low)
                                or ('issues=' in low)
                            )

                        def _is_non_retryable_error(err_text):
                            low = str(err_text or '').lower()
                            markers = (
                                'unauthorized',
                                'forbidden',
                                'accessdenied',
                                'access denied',
                                "invalid 'sellerid' provided",
                                'invalid "sellerid" provided',
                                "invalid 'marketplaceids' provided",
                                'invalid "marketplaceids" provided',
                                'quota',
                                'throttl',
                            )
                            return any(m in low for m in markers)

                        req_primary = requirements or 'LISTING_OFFER_ONLY'
                        req_fallback = 'LISTING_OFFER_ONLY'
                        pt_primary = product_type
                        pt_fallback = 'PRODUCT'

                        attrs_full = ss_amazon_listing._build_amazon_offer_attributes(
                            upc=upc,
                            asin=asin,
                            condition_type=condition_type,
                            fulfillment_channel_code=fc_code,
                            quantity=qty,
                            currency=currency,
                            price=price,
                            include_identifiers=False,
                            marketplace_id=mp_id,
                            offer_audience=offer_audience
                        )

                        attrs_list_price = ss_amazon_listing._build_amazon_offer_attributes(
                            upc=upc,
                            asin=asin,
                            condition_type='',
                            fulfillment_channel_code='',
                            quantity=qty,
                            currency=currency,
                            price=price,
                            include_identifiers=False,
                            marketplace_id=mp_id,
                            offer_audience=offer_audience,
                            include_marketplace_fields=False,
                            include_offer_audience=False,
                            price_model='list_price'
                        )

                        attempt_plans = [
                            {
                                'name': 'price_default',
                                'requirements': req_primary,
                                'product_type': pt_primary,
                                'attrs': ss_amazon_listing._amazon_offer_price_attrs(
                                    currency, price, mp_id,
                                    offer_audience=offer_audience,
                                    include_marketplace_fields=True,
                                    include_offer_audience=True
                                ),
                            },
                            {
                                'name': 'price_no_audience',
                                'requirements': req_primary,
                                'product_type': pt_primary,
                                'attrs': ss_amazon_listing._amazon_offer_price_attrs(
                                    currency, price, mp_id,
                                    offer_audience=offer_audience,
                                    include_marketplace_fields=True,
                                    include_offer_audience=False
                                ),
                            },
                            {
                                'name': 'price_no_marketplace',
                                'requirements': req_primary,
                                'product_type': pt_primary,
                                'attrs': ss_amazon_listing._amazon_offer_price_attrs(
                                    currency, price, mp_id,
                                    offer_audience=offer_audience,
                                    include_marketplace_fields=False,
                                    include_offer_audience=True
                                ),
                            },
                            {
                                'name': 'price_minimal_listing_offer',
                                'requirements': req_fallback,
                                'product_type': pt_primary,
                                'attrs': ss_amazon_listing._amazon_offer_price_attrs(
                                    currency, price, mp_id,
                                    offer_audience=offer_audience,
                                    include_marketplace_fields=False,
                                    include_offer_audience=False
                                ),
                            },
                            {
                                'name': 'full_offer_listing_offer',
                                'requirements': req_fallback,
                                'product_type': pt_primary,
                                'attrs': attrs_full,
                            },
                            {
                                'name': 'list_price_product',
                                'requirements': req_fallback,
                                'product_type': pt_fallback,
                                'attrs': attrs_list_price,
                            },
                        ]

                        ok = False
                        last_err = None
                        for plan in attempt_plans:
                            req_used = plan['requirements']
                            pt_used = plan['product_type']
                            attrs_used = plan['attrs']
                            ok_try, debug_try, err_try = ss_amazon_listing._amazon_update_price_spapi(
                                li,
                                seller_id,
                                sku,
                                mp_id,
                                product_type=pt_used,
                                requirements=req_used,
                                attrs=attrs_used
                            )
                            _append_attempts(plan.get('name') or 'attempt', req_used, pt_used, debug_try)
                            if ok_try:
                                ok = True
                                break

                            last_err = err_try
                            err_text = _format_err_text(err_try)
                            if _is_non_retryable_error(err_text):
                                break
                            if not _is_retryable_schema_error(err_text):
                                break

                        if not ok:
                            raise Exception(_format_err_text(last_err) if last_err else 'Amazon price update failed')

                        ok_skus.add(sku)
                        _pricemaster_record_change(platform='amazon', listing_key=sku, old_price=j['old_price'], new_price=j['new_price'], mode=mode, value=value_d, success=True)
                    except Exception as e:
                        msg = ss_amazon_listing._amazon_format_spapi_error(e)
                        try:
                            msg = f"{msg} | product_type={product_type}"
                        except Exception:
                            pass
                        try:
                            import json as json_module
                            msg = f"{msg} | debug={json_module.dumps(debug, ensure_ascii=True, default=str)}"
                        except Exception:
                            pass
                        fail_skus[sku] = msg
                        if 'InvalidInput' in msg:
                            feed_candidates.append({
                                'sku': sku,
                                'product_type': product_type,
                                'new_price': j.get('new_price'),
                                'old_price': j.get('old_price')
                            })
                        _pricemaster_record_change(platform='amazon', listing_key=sku, old_price=j['old_price'], new_price=j['new_price'], mode=mode, value=value_d, success=False, error=msg)

                # Feed fallback for InvalidInput failures
                feed_skus = set()
                pending_skus = set()
                feed_id = None
                if feed_candidates:
                    try:
                        # Attach adjustment info for later reconciliation
                        for fc in feed_candidates:
                            if 'mode' not in fc:
                                fc['mode'] = mode
                            if 'value' not in fc:
                                fc['value'] = float(value_d) if value_d is not None else None
                        feed_id = ss_amazon_catalog._amazon_submit_price_feed(
                            credentials=credentials,
                            marketplace=marketplace,
                            marketplace_id=mp_id,
                            seller_id=seller_id,
                            jobs=feed_candidates,
                            currency=currency,
                            offer_audience=offer_audience
                        )
                        _pricemaster_save_feed_job(feed_id, mp_id, feed_candidates, status='SUBMITTED')
                        for j in feed_candidates:
                            feed_skus.add(j['sku'])
                            pending_skus.add(j['sku'])
                            # overwrite failure if feed submitted
                            fail_skus.pop(j['sku'], None)
                    except Exception as e:
                        feed_err = ss_amazon_listing._amazon_format_spapi_error(e)
                        for j in feed_candidates:
                            fail_skus[j['sku']] = f"{fail_skus.get(j['sku'], '')} | feed_error={feed_err}"

                # Update local amazonStore.db for successes
                now_iso = _pricemaster_now_iso()
                with ss_database.db_connection('amazonStore.db') as conn:
                    cur = conn.cursor()
                    for j in amazon_jobs:
                        sku = j['sku']
                        if sku in ok_skus and sku not in pending_skus:
                            cur.execute('UPDATE ITEMS SET PRICE = ?, LAST_UPDATED = ? WHERE TRIM(COALESCE(SKU,\'\')) = ? COLLATE NOCASE', (float(j['new_price']), now_iso, sku))

                for r in results:
                    if r.get('platform') == 'amazon' and r.get('_dry'):
                        sku = r.get('listing_key')
                        if sku in pending_skus:
                            r['success'] = True
                            r['pending'] = True
                            if feed_id:
                                r['note'] = f"Submitted via Amazon feed {feed_id} (processing)"
                        elif sku in ok_skus:
                            r['success'] = True
                        else:
                            r['success'] = False
                            r['error'] = fail_skus.get(sku) or 'Amazon price update failed'
                        r.pop('_dry', None)

        # Clean up any remaining placeholders
        for r in results:
            r.pop('_dry', None)

        return jsonify({'success': True, 'dry_run': dry_run, 'results': results})

    except Exception as e:
        return jsonify({'success': False, 'error': ss_errors._safe_error(e, 'pricemaster:bulk_update')}), 500


def api_pricemaster_ebay_verify():
    """Verify eBay listing prices against live data and sync local DB."""
    try:
        data = request.json or {}
        items = data.get('items') or []
        if not items:
            item_id = (data.get('item_id') or '').strip()
            expected = data.get('expected_price')
            if item_id:
                items = [{'item_id': item_id, 'expected_price': expected}]

        if not items:
            return jsonify({'success': False, 'error': 'No item ids provided'}), 400

        results = []
        # Limit to a reasonable number to avoid rate limits
        items = items[:10]

        for it in items:
            item_id = (it.get('item_id') or '').strip()
            if not item_id:
                continue
            expected_d = _pricemaster_parse_decimal(it.get('expected_price'))
            expected = float(_pricemaster_money_2dp(expected_d)) if expected_d is not None else None
            try:
                price, currency = _pricemaster_ebay_get_item_price(item_id)
                applied = None
                if expected is not None and price is not None:
                    applied = abs(float(price) - float(expected)) <= 0.01
                # Sync local DB price
                with ss_database.db_connection('ebayStore.db') as conn:
                    cur = conn.cursor()
                    cur.execute('UPDATE INVENTORY SET Price = ? WHERE TRIM(COALESCE(ItemID,\'\')) = ? COLLATE NOCASE', (f"{float(price):.2f}", item_id))
                results.append({
                    'item_id': item_id,
                    'price': price,
                    'currency': currency,
                    'expected_price': expected,
                    'applied': applied,
                })
            except Exception as e:
                results.append({
                    'item_id': item_id,
                    'error': ss_errors._safe_error(e, 'pricemaster:ebay_verify:item')
                })

        return jsonify({'success': True, 'items': results})
    except Exception as e:
        return jsonify({'success': False, 'error': ss_errors._safe_error(e, 'pricemaster:ebay_verify')}), 500


def api_pricemaster_feed_status():
    """Check Amazon feed processing status and apply successful feed updates to local DB."""
    try:
        data = request.json or {}
        feed_id = (data.get('feed_id') or '').strip()
        if not feed_id:
            return jsonify({'success': False, 'error': 'feed_id is required'}), 400

        credentials, _seller_id, _marketplace_id, marketplace = ss_amazon_catalog._amazon_spapi_context()
        if marketplace is None:
            return jsonify({'success': False, 'error': 'Amazon SP-API not available'}), 503

        info = ss_amazon_catalog._amazon_get_feed_status(credentials, marketplace, feed_id)
        status = (info.get('status') or '').strip()
        result_doc_id = info.get('result_feed_document_id')

        report = None
        report_errors = []
        report_warnings = []
        if result_doc_id:
            try:
                from sp_api.api import Feeds
                feeds = Feeds(credentials=credentials, marketplace=marketplace)
                doc_resp = ss_amazon_catalog._amazon_get_feed_document_info(feeds, result_doc_id)
                doc_payload = getattr(doc_resp, 'payload', None) or {}
                url = doc_payload.get('url')
                raw = ss_amazon_catalog._amazon_download_feed_document(url)
                report = ss_amazon_catalog._amazon_parse_feed_report(raw)
                report_errors = report.get('errors') or []
                report_warnings = report.get('warnings') or []
            except Exception as e:
                report = {'error_count': 1, 'errors': [{'code': 'Report', 'message': ss_amazon_listing._amazon_format_spapi_error(e)}]}

        # Update feed job + apply if successful
        job = _pricemaster_get_feed_job(feed_id)
        if status:
            _pricemaster_update_feed_job(feed_id, status=status, result_document_id=result_doc_id)

        applied = False
        if status and status.upper() in ('DONE', 'DONE_NO_DATA', 'DONE_SUCCESS', 'DONE_WARNING', 'SUCCESS'):
            if report and report.get('error_count', 0) == 0:
                # Apply updates to local DB + record history
                items = []
                try:
                    import json as _json
                    items = _json.loads((job or {}).get('items_json') or '[]')
                except Exception:
                    items = []

                now_iso = _pricemaster_now_iso()
                with ss_database.db_connection('amazonStore.db') as conn:
                    cur = conn.cursor()
                    for it in items:
                        sku = (it.get('sku') or '').strip()
                        if not sku:
                            continue
                        new_price = it.get('new_price')
                        old_price = it.get('old_price')
                        mode = it.get('mode') or 'delta'
                        val = it.get('value')
                        try:
                            cur.execute('UPDATE ITEMS SET PRICE = ?, LAST_UPDATED = ? WHERE TRIM(COALESCE(SKU,\'\')) = ? COLLATE NOCASE', (float(new_price), now_iso, sku))
                        except Exception:
                            pass
                        try:
                            _pricemaster_record_change(platform='amazon', listing_key=sku, old_price=old_price, new_price=new_price, mode=mode, value=_pricemaster_parse_decimal(val), success=True)
                        except Exception:
                            pass
                applied = True
                _pricemaster_update_feed_job(feed_id, status=status, error=None, result_document_id=result_doc_id)
            elif report and report.get('error_count', 0) > 0:
                # Record failures
                items = []
                try:
                    import json as _json
                    items = _json.loads((job or {}).get('items_json') or '[]')
                except Exception:
                    items = []
                err_msg = report_errors[0].get('message') if report_errors else 'Feed processing error'
                for it in items:
                    sku = (it.get('sku') or '').strip()
                    if not sku:
                        continue
                    try:
                        _pricemaster_record_change(platform='amazon', listing_key=sku, old_price=it.get('old_price'), new_price=it.get('new_price'), mode=it.get('mode') or 'delta', value=_pricemaster_parse_decimal(it.get('value')), success=False, error=err_msg)
                    except Exception:
                        pass
                _pricemaster_update_feed_job(feed_id, status=status, error=err_msg, result_document_id=result_doc_id)

        return jsonify({
            'success': True,
            'feed_id': feed_id,
            'status': status,
            'result_feed_document_id': result_doc_id,
            'report': report,
            'report_errors': report_errors,
            'report_warnings': report_warnings,
            'applied': applied
        })
    except Exception as e:
        return jsonify({'success': False, 'error': ss_errors._safe_error(e, 'pricemaster:feed_status')}), 500
