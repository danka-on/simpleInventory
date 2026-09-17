"""Listing alerts for Sweet Shelves."""

import datetime
import json
import os
import sqlite3
import threading
from DBmanager import connect_db
from flask import jsonify, request
from . import (
    caching as ss_caching, config as ss_config, errors as ss_errors, listing_lifecycle as
    ss_listing_lifecycle, marketplace_removal as ss_marketplace_removal, normalization as
    ss_normalization, sync as ss_sync, warehouse_matching as ss_warehouse_matching,
)


def _ensure_listing_alerts_tables():
    """Create listing_alerts.db tables if they don't exist."""
    try:
        conn = sqlite3.connect('listing_alerts.db')
        cur = conn.cursor()
        cur.execute('''
            CREATE TABLE IF NOT EXISTS dismissed_alerts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                alert_type TEXT NOT NULL,
                upc TEXT NOT NULL,
                store TEXT,
                listing_ids TEXT,
                dismissed_at TEXT DEFAULT CURRENT_TIMESTAMP,
                snapshot_hash TEXT NOT NULL,
                UNIQUE(alert_type, snapshot_hash)
            )
        ''')
        cur.execute('CREATE INDEX IF NOT EXISTS idx_dismissed_hash ON dismissed_alerts(alert_type, snapshot_hash)')
        cur.execute('''
            CREATE TABLE IF NOT EXISTS inventory_zero_tracker (
                upc TEXT PRIMARY KEY,
                ever_in_inventory INTEGER DEFAULT 0,
                last_qty INTEGER DEFAULT 0,
                last_nonzero_at TEXT,
                zero_triggered_at TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        cur.execute('CREATE INDEX IF NOT EXISTS idx_zero_tracker_zero ON inventory_zero_tracker(zero_triggered_at)')
        cur.execute('''
            CREATE TABLE IF NOT EXISTS ebay_listing_health_cache (
                item_id TEXT PRIMARY KEY,
                is_active INTEGER NOT NULL DEFAULT 1,
                checked_at TEXT DEFAULT CURRENT_TIMESTAMP,
                source TEXT,
                note TEXT
            )
        ''')
        cur.execute('CREATE INDEX IF NOT EXISTS idx_ebay_listing_health_checked_at ON ebay_listing_health_cache(checked_at)')
        cur.execute('''
            CREATE TABLE IF NOT EXISTS listing_inventory_matches (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                store TEXT NOT NULL,
                listing_key TEXT NOT NULL COLLATE NOCASE,
                listing_id TEXT,
                marketplace_barcode TEXT,
                listing_title TEXT,
                searchrack_id INTEGER NOT NULL,
                inventory_barcode TEXT NOT NULL,
                inventory_location TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(store, listing_key)
            )
        ''')
        cur.execute('CREATE INDEX IF NOT EXISTS idx_listing_inventory_match_rack ON listing_inventory_matches(searchrack_id)')
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"Error initializing listing_alerts.db: {e}")


def _listing_alert_upc_key(raw_upc):
    """Normalize UPC/Barcode for cross-db matching in listing alert logic."""
    upc = (str(raw_upc or '').strip())
    if not upc:
        return ''
    normalized = ss_normalization._strip_leading_zeros_numeric(upc)
    return (normalized or upc).lower()


def _normalize_store_listing_identity(store, listing_key):
    normalized_store = str(store or '').strip().lower()
    normalized_key = str(listing_key or '').strip()
    if normalized_store not in {'ebay', 'amazon', 'facebook'} or not normalized_key:
        return '', ''
    prefix = f'{normalized_store}:'
    if normalized_key.lower().startswith(prefix):
        normalized_key = normalized_key[len(prefix):].strip()
    return normalized_store, normalized_key


def _store_listing_order_identity_candidates(order):
    store = str(ss_warehouse_matching._sold_order_value(order, 'store', '') or '').strip().lower()
    values = []
    if store == 'ebay':
        values = [
            ss_warehouse_matching._sold_order_value(order, 'listing_listing_id', ''),
            ss_warehouse_matching._sold_order_value(order, 'item_id', ''),
            ss_warehouse_matching._sold_order_value(order, 'listing_sku', ''),
            ss_warehouse_matching._sold_order_value(order, 'sku', '')
        ]
    elif store == 'amazon':
        values = [
            ss_warehouse_matching._sold_order_value(order, 'listing_sku', ''),
            ss_warehouse_matching._sold_order_value(order, 'sku', ''),
            ss_warehouse_matching._sold_order_value(order, 'listing_asin', ''),
            ss_warehouse_matching._sold_order_value(order, 'item_id', '')
        ]

    out = []
    for value in values:
        _, key = _normalize_store_listing_identity(store, value)
        key_lower = key.lower()
        if key and key_lower not in out:
            out.append(key_lower)
    return store, out


def _load_listing_inventory_match_lookup():
    _ensure_listing_alerts_tables()
    lookup = {}
    try:
        with sqlite3.connect(str(ss_config.BASE_DIR / 'listing_alerts.db')) as conn:
            conn.row_factory = sqlite3.Row
            for row in conn.execute('SELECT * FROM listing_inventory_matches'):
                store, key = _normalize_store_listing_identity(row['store'], row['listing_key'])
                if store and key:
                    lookup[(store, key.lower())] = dict(row)
    except Exception as e:
        print(f'Warning: Could not load store-listing inventory matches: {e}')
    return lookup


def _listing_inventory_match_for_order(order, lookup=None):
    mapping_lookup = lookup if lookup is not None else _load_listing_inventory_match_lookup()
    store, candidates = _store_listing_order_identity_candidates(order)
    for candidate in candidates:
        match = mapping_lookup.get((store, candidate))
        if match:
            return match
    return None


def _listing_alert_base_upc_key(raw_upc):
    """Normalize base UPC (strip -suffix + leading zeros) for broader matching."""
    upc = (str(raw_upc or '').strip())
    if not upc:
        return ''
    base = upc.split('-', 1)[0].strip()
    if not base:
        return ''
    normalized = ss_normalization._strip_leading_zeros_numeric(base)
    return (normalized or base).lower()


_listing_helper_scan_cache_lock = threading.Lock()


_listing_helper_scan_cache_state = {
    'ts': 0.0,
    'payload': None
}


def api_listing_helper_scan():
    """Scan for listing issues with low-noise zero inventory gating."""
    import hashlib
    force_refresh = (request.args.get('refresh') or '').strip().lower() in ('1', 'true', 'yes', 'y')
    if not force_refresh:
        cached_payload = ss_caching._listing_helper_scan_cache_get(max_age_seconds=20)
        if cached_payload:
            return jsonify(cached_payload)

    alerts = {
        'no_warehouse': [],      # Listings with UPC not in warehouse
        'quantity_alert': [],    # Any single listing qty > total warehouse qty
        'no_listings': [],       # Warehouse stock with no active listing on any store
        'sync_overdue': [],      # Sync manager overdue beyond configured interval
        'listing_matches': []    # Listings needing or already using a manual inventory match
    }

    try:
        _ensure_listing_alerts_tables()
        ss_listing_lifecycle._ensure_bol_list_status_column()

        def _match_upc_key(raw_upc):
            upc = (str(raw_upc or '').strip())
            if not upc:
                return ''
            normalized = ss_normalization._strip_leading_zeros_numeric(upc)
            return (normalized or upc).lower()

        def _safe_int(val, default=1):
            try:
                if val is None:
                    return default
                if isinstance(val, str):
                    s = val.strip().lower()
                    if s in ('', 'n/a', 'na', 'null'):
                        return default
                return int(float(val))
            except Exception:
                return default

        def _extract_image_url(raw_value):
            raw = (str(raw_value or '').strip())
            if not raw:
                return ''

            # Some rows store image lists (JSON or delimiter-separated).
            if raw.startswith('[') and raw.endswith(']'):
                try:
                    parsed = json.loads(raw)
                    if isinstance(parsed, list):
                        for item in parsed:
                            candidate = (str(item or '').strip().strip('"').strip("'"))
                            if candidate:
                                return candidate
                    elif isinstance(parsed, str):
                        candidate = parsed.strip().strip('"').strip("'")
                        if candidate:
                            return candidate
                except Exception:
                    pass

            for delim in ('|', ',', ';'):
                if delim in raw:
                    for part in raw.split(delim):
                        candidate = (part or '').strip().strip('"').strip("'")
                        if candidate:
                            return candidate
                    return ''

            return raw.strip('"').strip("'")

        def _parse_iso_utc(raw):
            txt = (str(raw or '').strip())
            if not txt:
                return None
            try:
                if 'T' in txt:
                    if txt.endswith('Z'):
                        dt = datetime.datetime.fromisoformat(txt.replace('Z', '+00:00'))
                    else:
                        dt = datetime.datetime.fromisoformat(txt)
                else:
                    dt = datetime.datetime.fromisoformat(txt)
                if dt.tzinfo is not None:
                    dt = dt.astimezone(datetime.timezone.utc).replace(tzinfo=None)
                return dt
            except Exception:
                return None

        invalid_upc_values = {'null', 'n/a', 'does not apply'}
        scan_now_utc = datetime.datetime.utcnow()
        enable_ebay_live_check = (os.getenv('LISTING_HELPER_EBAY_LIVE_CHECK', '0').strip().lower() in ('1', 'true', 'yes', 'y'))

        # Guard against stale "Active" rows in ebayStore.db when sync reconciliation misses a cycle.
        # We only live-check older listings, cache results, and cap checks per scan.
        ebay_live_cache = {}
        ebay_live_cache_updates = []
        ebay_live_check_budget = 30 if enable_ebay_live_check else 0
        ebay_live_checks_used = 0
        ebay_live_cache_ttl_seconds = 24 * 3600
        ebay_live_verify_min_age_days = 120
        ebay_live_filtered_item_ids = set()

        def _ebay_cache_is_fresh(checked_at_raw):
            dt = _parse_iso_utc(checked_at_raw) or ss_normalization._parse_iso_utc_naive(checked_at_raw)
            if dt is None:
                return False
            return (scan_now_utc - dt).total_seconds() <= ebay_live_cache_ttl_seconds

        def _ebay_listing_allowed_for_alert(item_id_raw, list_date_raw, url_raw):
            nonlocal ebay_live_checks_used
            if not enable_ebay_live_check:
                return True
            item_id = (str(item_id_raw or '').strip())
            if not item_id:
                return True

            list_dt = _parse_iso_utc(list_date_raw) or ss_normalization._parse_iso_utc_naive(list_date_raw)
            if list_dt is not None:
                age_days = (scan_now_utc - list_dt).total_seconds() / 86400.0
                if age_days < ebay_live_verify_min_age_days:
                    return True

            cached = ebay_live_cache.get(item_id)
            if cached and _ebay_cache_is_fresh(cached.get('checked_at')):
                return bool(int(cached.get('is_active') or 0))

            if ebay_live_checks_used >= ebay_live_check_budget:
                return True

            ebay_live_checks_used += 1
            is_active, note = ss_marketplace_removal._probe_ebay_listing_live(item_id, url_raw)
            if is_active is None:
                return True

            checked_at = datetime.datetime.utcnow().isoformat() + 'Z'
            cache_row = {
                'item_id': item_id,
                'is_active': 1 if is_active else 0,
                'checked_at': checked_at,
                'note': note or ''
            }
            ebay_live_cache[item_id] = cache_row
            ebay_live_cache_updates.append(cache_row)

            if not is_active:
                ebay_live_filtered_item_ids.add(item_id)
            return bool(is_active)

        if enable_ebay_live_check:
            try:
                with sqlite3.connect('listing_alerts.db') as cache_conn:
                    cache_conn.row_factory = sqlite3.Row
                    cache_cur = cache_conn.cursor()
                    cache_cur.execute('SELECT item_id, is_active, checked_at FROM ebay_listing_health_cache')
                    for row in cache_cur.fetchall():
                        item_id = (row['item_id'] or '').strip()
                        if not item_id:
                            continue
                        ebay_live_cache[item_id] = {
                            'item_id': item_id,
                            'is_active': int(row['is_active'] or 0),
                            'checked_at': row['checked_at'] or '',
                            'note': ''
                        }
            except Exception as e:
                print(f"Error reading ebay listing health cache: {e}")

        # Get dismissed alerts
        with sqlite3.connect('listing_alerts.db') as alerts_conn:
            alerts_cur = alerts_conn.cursor()
            alerts_cur.execute('SELECT alert_type, snapshot_hash FROM dismissed_alerts')
            dismissed = {(r[0], r[1]) for r in alerts_cur.fetchall()}

        sync_overdue_alert = ss_sync._sync_manager_overdue_alert()
        if sync_overdue_alert and ('sync_overdue', sync_overdue_alert.get('hash')) not in dismissed:
            alerts['sync_overdue'].append(sync_overdue_alert)

        # Get warehouse stock (sum qty by UPC/Barcode) + locations where items exist.
        warehouse_stock = {}
        warehouse_locations = {}
        warehouse_stock_by_base = {}
        warehouse_locations_by_base = {}
        warehouse_title_by_base = {}
        warehouse_image_by_base = {}
        warehouse_display_upc_by_base = {}
        warehouse_tokens_by_base = {}
        rawbol_title_by_base = {}
        rawbol_image_by_base = {}
        rawbol_display_upc_by_base = {}
        try:
            with sqlite3.connect('searchRack.db') as sr_conn:
                sr_conn.row_factory = sqlite3.Row
                sr_cur = sr_conn.cursor()
                sr_cur.execute('PRAGMA table_info(SEARCHRACK)')
                sr_cols = [c[1] for c in sr_cur.fetchall()]
                sr_cols_lower = {c.lower(): c for c in sr_cols}
                upc_col = sr_cols_lower.get('upc') or sr_cols_lower.get('barcode')
                qty_col = sr_cols_lower.get('quantity') or sr_cols_lower.get('qty')
                pos_col = sr_cols_lower.get('item_position') or sr_cols_lower.get('itemposition') or sr_cols_lower.get('position')
                pic_col = sr_cols_lower.get('pictureposition')
                title_col = sr_cols_lower.get('title')
                img_col = sr_cols_lower.get('image') or sr_cols_lower.get('images')
                if upc_col and qty_col:
                    pos_expr = f"{pos_col} AS LOC" if pos_col else "NULL AS LOC"
                    pic_expr = f"{pic_col} AS PIC" if pic_col else "NULL AS PIC"
                    title_expr = f"{title_col} AS TITLE" if title_col else "NULL AS TITLE"
                    img_expr = f"{img_col} AS IMG" if img_col else "NULL AS IMG"
                    sr_cur.execute(f'''
                        SELECT rowid AS RID, {upc_col} AS UPC, {qty_col} AS QTY, {pos_expr}, {pic_expr}, {title_expr}, {img_expr}
                        FROM SEARCHRACK
                        WHERE {upc_col} IS NOT NULL AND {upc_col} != ""
                          AND COALESCE(CAST({qty_col} AS INTEGER), 0) > 0
                    ''')
                    for row in sr_cur.fetchall():
                        try:
                            upc_raw = (row['UPC'] or '').strip()
                            upc_key = _match_upc_key(upc_raw)
                            base_upc_key = _listing_alert_base_upc_key(upc_raw)
                            if not upc_key or upc_key in invalid_upc_values:
                                continue

                            qty_val = max(0, _safe_int(row['QTY'], 0))
                            warehouse_stock[upc_key] = warehouse_stock.get(upc_key, 0) + qty_val
                            if base_upc_key and base_upc_key not in invalid_upc_values:
                                warehouse_stock_by_base[base_upc_key] = warehouse_stock_by_base.get(base_upc_key, 0) + qty_val

                            loc = (row['LOC'] or '').strip()
                            pic = (row['PIC'] or '').strip()
                            location_label = loc or pic
                            if location_label:
                                existing_locs = warehouse_locations.get(upc_key)
                                if existing_locs is None:
                                    existing_locs = []
                                    warehouse_locations[upc_key] = existing_locs
                                if location_label not in existing_locs:
                                    existing_locs.append(location_label)
                                if base_upc_key and base_upc_key not in invalid_upc_values:
                                    existing_base_locs = warehouse_locations_by_base.get(base_upc_key)
                                    if existing_base_locs is None:
                                        existing_base_locs = []
                                        warehouse_locations_by_base[base_upc_key] = existing_base_locs
                                    if location_label not in existing_base_locs:
                                        existing_base_locs.append(location_label)

                            if base_upc_key and base_upc_key not in invalid_upc_values:
                                if upc_raw and base_upc_key not in warehouse_display_upc_by_base:
                                    warehouse_display_upc_by_base[base_upc_key] = upc_raw
                                title_val = (row['TITLE'] or '').strip()
                                if title_val and base_upc_key not in warehouse_title_by_base:
                                    warehouse_title_by_base[base_upc_key] = title_val
                                image_val = _extract_image_url(row['IMG'])
                                if image_val and base_upc_key not in warehouse_image_by_base:
                                    warehouse_image_by_base[base_upc_key] = image_val

                                token = str(row['RID'] if row['RID'] is not None else f"{upc_raw}:{location_label}:{qty_val}")
                                token_list = warehouse_tokens_by_base.get(base_upc_key)
                                if token_list is None:
                                    token_list = []
                                    warehouse_tokens_by_base[base_upc_key] = token_list
                                if token not in token_list:
                                    token_list.append(token)
                        except Exception:
                            continue
                else:
                    print("Error reading searchRack: missing UPC/BARCODE or QUANTITY columns")
        except Exception as e:
            print(f"Error reading searchRack: {e}")

        # Raw BOL is source of truth for Store Doctor no-listing card metadata.
        try:
            warehouse_base_keys = {
                key for key in warehouse_stock_by_base.keys()
                if key and key not in invalid_upc_values
            }
            if warehouse_base_keys:
                with sqlite3.connect('rawbol.db') as rb_conn:
                    rb_conn.row_factory = sqlite3.Row
                    rb_cur = rb_conn.cursor()
                    rb_cur.execute('''
                        SELECT upc, item_description, image_url
                        FROM raw_bol_items
                        WHERE upc IS NOT NULL
                          AND TRIM(upc) != ''
                        ORDER BY COALESCE(import_date, '') DESC, rowid DESC
                    ''')
                    for row in rb_cur.fetchall():
                        upc_raw = (row['upc'] or '').strip()
                        base_upc_key = _listing_alert_base_upc_key(upc_raw)
                        if (not base_upc_key) or (base_upc_key in invalid_upc_values) or (base_upc_key not in warehouse_base_keys):
                            continue
                        if upc_raw and base_upc_key not in rawbol_display_upc_by_base:
                            rawbol_display_upc_by_base[base_upc_key] = upc_raw

                        title_val = (row['item_description'] or '').strip()
                        if title_val and base_upc_key not in rawbol_title_by_base:
                            rawbol_title_by_base[base_upc_key] = title_val

                        image_val = _extract_image_url(row['image_url'])
                        if image_val and base_upc_key not in rawbol_image_by_base:
                            rawbol_image_by_base[base_upc_key] = image_val
        except Exception as e:
            print(f"Error reading rawbol metadata for listing helper: {e}")

        # Pending sold orders (rackupdated=0) are inventory that will be auto-removed later.
        # We subtract these immediately so alerts fire right after sale sync, not 24-48h later.
        pending_sale_qty_by_upc = {}
        try:
            recent_window_days = 7
            now_utc = datetime.datetime.utcnow()
            with sqlite3.connect('sold.db') as sold_conn:
                sold_conn.row_factory = sqlite3.Row
                sold_cur = sold_conn.cursor()
                sold_cur.execute('PRAGMA table_info(orders)')
                sold_cols = {c[1].lower() for c in sold_cur.fetchall()}
                has_removal_cancelled = 'removal_cancelled' in sold_cols
                has_store = 'store' in sold_cols

                where_parts = [
                    'rackupdated = 0',
                    "barcode IS NOT NULL",
                    "TRIM(barcode) != ''"
                ]
                if has_removal_cancelled:
                    where_parts.append('COALESCE(removal_cancelled, 0) = 0')
                if has_store:
                    where_parts.append("LOWER(COALESCE(store, '')) != 'test'")

                sold_cur.execute(f'''
                    SELECT barcode, quantity, paid_time, shipped_time
                    FROM orders
                    WHERE {' AND '.join(where_parts)}
                ''')

                for row in sold_cur.fetchall():
                    ts = _parse_iso_utc(row['shipped_time']) or _parse_iso_utc(row['paid_time'])
                    if ts is not None:
                        age_days = (now_utc - ts).total_seconds() / 86400.0
                        if age_days > recent_window_days:
                            continue

                    upc_key = _match_upc_key(row['barcode'])
                    if not upc_key or upc_key in invalid_upc_values:
                        continue

                    sold_qty = _safe_int(row['quantity'], 1)
                    if sold_qty < 1:
                        sold_qty = 1
                    pending_sale_qty_by_upc[upc_key] = pending_sale_qty_by_upc.get(upc_key, 0) + sold_qty
        except Exception as e:
            print(f"Error reading pending sold orders: {e}")

        # Effective stock = physical warehouse stock minus recent pending sold qty.
        effective_stock = {}
        all_effective_upcs = set(warehouse_stock.keys()) | set(pending_sale_qty_by_upc.keys())
        for upc_key in all_effective_upcs:
            physical_qty = int(warehouse_stock.get(upc_key, 0) or 0)
            pending_qty = int(pending_sale_qty_by_upc.get(upc_key, 0) or 0)
            effective_stock[upc_key] = max(0, physical_qty - pending_qty)

        # Track effective qty transitions so no_warehouse only fires after a real >0 -> 0 transition.
        zero_triggered = {}  # upc_key -> zero_triggered_at
        try:
            now_iso = datetime.datetime.utcnow().isoformat() + 'Z'
            with sqlite3.connect('listing_alerts.db') as tracker_conn:
                tracker_conn.row_factory = sqlite3.Row
                tcur = tracker_conn.cursor()
                tcur.execute('''
                    SELECT upc, ever_in_inventory, last_qty, last_nonzero_at, zero_triggered_at
                    FROM inventory_zero_tracker
                ''')
                tracker_rows = tcur.fetchall()
                tracker_map = {}
                for row in tracker_rows:
                    key = _match_upc_key(row['upc'])
                    if key:
                        tracker_map[key] = row

                all_tracked_upcs = set(tracker_map.keys()) | set(effective_stock.keys())
                for upc_key in all_tracked_upcs:
                    physical_qty = int(warehouse_stock.get(upc_key, 0) or 0)
                    current_qty = int(effective_stock.get(upc_key, 0) or 0)
                    row = tracker_map.get(upc_key)

                    if row is None:
                        ever_in_inventory = 1 if (physical_qty > 0 or current_qty > 0) else 0
                        last_nonzero_at = now_iso if current_qty > 0 else (now_iso if physical_qty > 0 else None)
                        zero_triggered_at = None
                        tcur.execute('''
                            INSERT INTO inventory_zero_tracker (
                                upc, ever_in_inventory, last_qty, last_nonzero_at, zero_triggered_at, created_at, updated_at
                            ) VALUES (?, ?, ?, ?, ?, ?, ?)
                        ''', (
                            upc_key,
                            ever_in_inventory,
                            current_qty,
                            last_nonzero_at,
                            zero_triggered_at,
                            now_iso,
                            now_iso
                        ))
                    else:
                        prev_qty = int(row['last_qty'] or 0)
                        ever_in_inventory = int(row['ever_in_inventory'] or 0)
                        if physical_qty > 0 or current_qty > 0:
                            ever_in_inventory = 1

                        last_nonzero_at = row['last_nonzero_at']
                        zero_triggered_at = row['zero_triggered_at']

                        if current_qty > 0:
                            # Reset any previous zero event once stock is replenished.
                            last_nonzero_at = now_iso
                            zero_triggered_at = None
                        elif prev_qty > 0 and ever_in_inventory == 1:
                            # Fire only on transition from positive inventory to zero.
                            zero_triggered_at = now_iso

                        tcur.execute('''
                            UPDATE inventory_zero_tracker
                            SET ever_in_inventory = ?,
                                last_qty = ?,
                                last_nonzero_at = ?,
                                zero_triggered_at = ?,
                                updated_at = ?
                            WHERE upc = ?
                        ''', (
                            ever_in_inventory,
                            current_qty,
                            last_nonzero_at,
                            zero_triggered_at,
                            now_iso,
                            upc_key
                        ))

                    if current_qty == 0 and ever_in_inventory == 1 and zero_triggered_at:
                        zero_triggered[upc_key] = zero_triggered_at

                tracker_conn.commit()
        except Exception as e:
            print(f"Error updating inventory_zero_tracker: {e}")

        # Helper to safely get an ID from a sqlite Row
        def _row_id(row):
            if row is None:
                return None
            keys = row.keys() if hasattr(row, 'keys') else []
            if 'ID' in keys:
                return row['ID']
            if 'rowid' in keys:
                return row['rowid']
            try:
                return row[0]
            except Exception:
                return None

        # Get eBay active listings
        ebay_listings = {}  # upc -> [{id, title, qty, image}, ...]
        try:
            with sqlite3.connect('ebayStore.db') as eb_conn:
                eb_conn.row_factory = sqlite3.Row
                eb_cur = eb_conn.cursor()
                eb_cur.execute('''
                    SELECT ID, ItemID, Title, UPC, Quantity, Image, URL, List_Date
                    FROM INVENTORY
                    WHERE UPC IS NOT NULL AND UPC != ""
                      AND (Quantity > 0 OR Quantity IS NULL)
                      AND LOWER(TRIM(COALESCE(List_State, ''))) = 'active'
                      AND TRIM(COALESCE(ItemID, '')) != ''
                    ORDER BY COALESCE(List_Date, '') ASC, ID ASC
                ''')
                for row in eb_cur.fetchall():
                    if not _ebay_listing_allowed_for_alert(row['ItemID'], row['List_Date'], row['URL']):
                        continue
                    upc = (row['UPC'] or '').strip()
                    upc_key = _match_upc_key(upc)
                    if upc_key and upc_key not in invalid_upc_values:
                        if upc_key not in ebay_listings:
                            ebay_listings[upc_key] = []
                        row_id = _row_id(row)
                        listing_id = row['ItemID'] if row['ItemID'] else f"ebay_row:{row_id}"
                        ebay_listings[upc_key].append({
                            'id': row_id,
                            'listing_id': f"ebay:{listing_id}",
                            'listing_key': str(row['ItemID'] or listing_id),
                            'item_id': row['ItemID'],
                            'title': row['Title'],
                            'qty': _safe_int(row['Quantity'], 1),
                            'image': row['Image'],
                            'url': row['URL'],
                            'list_date': row['List_Date'],
                            'store': 'ebay',
                            'upc': upc
                        })
                if enable_ebay_live_check and ebay_live_filtered_item_ids:
                    ended_ids = sorted(ebay_live_filtered_item_ids)
                    chunk = 500
                    for i in range(0, len(ended_ids), chunk):
                        part = ended_ids[i:i + chunk]
                        placeholders = ','.join('?' for _ in part)
                        eb_cur.execute(f'''
                            UPDATE INVENTORY
                            SET List_State = 'Unsold'
                            WHERE ItemID IN ({placeholders})
                              AND LOWER(TRIM(COALESCE(List_State, ''))) = 'active'
                        ''', tuple(part))
        except Exception as e:
            print(f"Error reading ebayStore: {e}")

        if enable_ebay_live_check and ebay_live_cache_updates:
            try:
                with sqlite3.connect('listing_alerts.db') as cache_conn:
                    cache_cur = cache_conn.cursor()
                    cache_cur.executemany('''
                        INSERT INTO ebay_listing_health_cache (item_id, is_active, checked_at, source, note)
                        VALUES (?, ?, ?, ?, ?)
                        ON CONFLICT(item_id) DO UPDATE SET
                            is_active = excluded.is_active,
                            checked_at = excluded.checked_at,
                            source = excluded.source,
                            note = excluded.note
                    ''', [
                        (
                            row['item_id'],
                            int(row['is_active'] or 0),
                            row['checked_at'],
                            'scan',
                            row.get('note') or ''
                        )
                        for row in ebay_live_cache_updates
                    ])
                    cache_conn.commit()
            except Exception as e:
                print(f"Error writing ebay listing health cache: {e}")

        # Get Amazon active listings
        amazon_listings = {}  # upc -> [{id, title, qty, image}, ...]
        try:
            with sqlite3.connect('amazonStore.db') as am_conn:
                am_conn.row_factory = sqlite3.Row
                am_cur = am_conn.cursor()
                am_cur.execute('''
                    SELECT ID, ASIN, SKU, TITLE, UPC, QUANTITY, IMAGE
                    FROM ITEMS
                    WHERE UPC IS NOT NULL AND UPC != ""
                      AND (QUANTITY > 0 OR QUANTITY IS NULL)
                      AND LOWER(TRIM(COALESCE(STATUS, ''))) = 'active'
                      AND TRIM(COALESCE(ASIN, '')) != ''
                ''')
                for row in am_cur.fetchall():
                    upc = (row['UPC'] or '').strip()
                    upc_key = _match_upc_key(upc)
                    if upc_key and upc_key not in invalid_upc_values:
                        if upc_key not in amazon_listings:
                            amazon_listings[upc_key] = []
                        row_id = _row_id(row)
                        listing_key = row['SKU'] or row['ASIN'] or f"amazon_row:{row_id}"
                        amazon_listings[upc_key].append({
                            'id': row_id,
                            'listing_id': f"amazon:{listing_key}",
                            'listing_key': str(listing_key),
                            'sku': row['SKU'],
                            'asin': row['ASIN'],
                            'title': row['TITLE'],
                            'qty': _safe_int(row['QUANTITY'], 1),
                            'image': row['IMAGE'],
                            'store': 'amazon',
                            'upc': upc
                        })
        except Exception as e:
            print(f"Error reading amazonStore: {e}")

        # Get Facebook active listings (tracked in bol.db)
        facebook_listings = {}  # upc -> [{id, title, qty, image}, ...]
        try:
            with sqlite3.connect('bol.db') as fb_conn:
                fb_conn.row_factory = sqlite3.Row
                fb_cur = fb_conn.cursor()
                fb_cur.execute('''
                    SELECT id,
                           upc,
                           item_description AS title,
                           image_url AS image,
                           COALESCE(listed_facebook_qty, quantity, 1) AS qty
                    FROM bol_items
                    WHERE upc IS NOT NULL
                      AND TRIM(upc) != ''
                      AND (COALESCE(listed_facebook, 0) = 1 OR COALESCE(listed_facebook_qty, 0) > 0)
                ''')
                for row in fb_cur.fetchall():
                    upc = (row['upc'] or '').strip()
                    upc_key = _match_upc_key(upc)
                    if upc_key and upc_key not in invalid_upc_values:
                        if upc_key not in facebook_listings:
                            facebook_listings[upc_key] = []
                        row_id = _row_id(row)
                        listing_id = f"facebook_row:{row_id}" if row_id is not None else f"facebook_upc:{upc_key}"
                        facebook_listings[upc_key].append({
                            'id': row_id,
                            'listing_id': f"facebook:{listing_id}",
                            'listing_key': listing_id,
                            'title': row['title'],
                            'qty': _safe_int(row['qty'], 1),
                            'image': row['image'],
                            'store': 'facebook',
                            'upc': upc
                        })
        except Exception as e:
            print(f"Error reading Facebook listings from bol.db: {e}")

        # Helper to create hash for dismissal tracking
        def make_hash(alert_type, upc, listing_ids, event_token=''):
            data = f"{alert_type}:{upc.lower()}:{','.join(sorted(str(x) for x in listing_ids))}:{event_token or ''}"
            return hashlib.md5(data.encode('utf-8')).hexdigest()

        listing_match_lookup = _load_listing_inventory_match_lookup()
        for listing_map in (ebay_listings, amazon_listings, facebook_listings):
            for listed_upc_key, listing_rows in listing_map.items():
                for listing_row in listing_rows:
                    store, listing_key = _normalize_store_listing_identity(
                        listing_row.get('store'),
                        listing_row.get('listing_key') or listing_row.get('listing_id')
                    )
                    listing_row['listing_key'] = listing_key
                    saved_match = listing_match_lookup.get((store, listing_key.lower())) if store and listing_key else None
                    if saved_match:
                        listing_row['inventory_match'] = {
                            'searchrack_id': saved_match.get('searchrack_id'),
                            'barcode': saved_match.get('inventory_barcode'),
                            'location': saved_match.get('inventory_location')
                        }
                    if int(warehouse_stock.get(listed_upc_key, 0) or 0) <= 0 or saved_match:
                        alerts['listing_matches'].append(listing_row)

        alerts['listing_matches'].sort(key=lambda listing: (
            0 if listing.get('inventory_match') else 1,
            str(listing.get('store') or ''),
            str(listing.get('title') or '').lower(),
            str(listing.get('listing_key') or '').lower()
        ))

        all_upcs = set(ebay_listings.keys()) | set(amazon_listings.keys()) | set(facebook_listings.keys())
        listed_base_upcs = set()
        for listing_map in (ebay_listings, amazon_listings, facebook_listings):
            for listed_upc_key, listing_rows in listing_map.items():
                base_from_key = _listing_alert_base_upc_key(listed_upc_key)
                if base_from_key and base_from_key not in invalid_upc_values:
                    listed_base_upcs.add(base_from_key)
                for listing_row in (listing_rows or []):
                    base_from_row = _listing_alert_base_upc_key(listing_row.get('upc'))
                    if base_from_row and base_from_row not in invalid_upc_values:
                        listed_base_upcs.add(base_from_row)

        for upc_key in all_upcs:
            ebay_items = ebay_listings.get(upc_key, [])
            amazon_items = amazon_listings.get(upc_key, [])
            facebook_items = facebook_listings.get(upc_key, [])
            warehouse_qty = int(warehouse_stock.get(upc_key, 0) or 0)
            pending_sale_qty = int(pending_sale_qty_by_upc.get(upc_key, 0) or 0)
            effective_qty = int(effective_stock.get(upc_key, warehouse_qty) or 0)

            all_listings = ebay_items + amazon_items + facebook_items
            listing_ids = [l.get('listing_id') or f"{l['store']}:{l.get('id')}" for l in all_listings]

            # 1. No warehouse match
            # Gate by transition: item must have been in inventory and then hit zero.
            zero_event_token = zero_triggered.get(upc_key)
            if zero_event_token and effective_qty == 0 and all_listings:
                stores_with_listing = []
                if ebay_items:
                    stores_with_listing.append('ebay')
                if amazon_items:
                    stores_with_listing.append('amazon')
                if facebook_items:
                    stores_with_listing.append('facebook')

                snap_hash = make_hash('no_warehouse', upc_key, listing_ids, event_token=zero_event_token)
                if ('no_warehouse', snap_hash) not in dismissed:
                    # Red if 2 stores, yellow if 1
                    severity = 'red' if len(stores_with_listing) >= 2 else 'yellow'
                    alerts['no_warehouse'].append({
                        'upc': all_listings[0]['upc'],  # Use original case
                        'stores': stores_with_listing,
                        'listings': all_listings,
                        'warehouse_qty': warehouse_qty,
                        'warehouse_locations': warehouse_locations.get(upc_key, []),
                        'pending_sale_qty': pending_sale_qty,
                        'effective_qty': effective_qty,
                        'zero_triggered_at': zero_event_token,
                        'severity': severity,
                        'hash': snap_hash
                    })

            # 2. Quantity alert (per-listing quantity check)
            # Trigger only when warehouse has stock, and one listing's own qty exceeds that stock.
            # This intentionally does NOT sum listing qtys across stores.
            if warehouse_qty > 0:
                for listing in all_listings:
                    listing_qty = int(listing.get('qty') or 0)
                    if listing_qty <= warehouse_qty:
                        continue
                    listing_token = f"{listing.get('listing_id')}:{listing_qty}:warehouse={warehouse_qty}"
                    snap_hash = make_hash('quantity_alert', upc_key, [listing_token])
                    if ('quantity_alert', snap_hash) in dismissed:
                        continue

                    alerts['quantity_alert'].append({
                        'upc': listing.get('upc') or all_listings[0]['upc'],
                        'store': listing.get('store'),
                        'listing': listing,
                        'warehouse_qty': warehouse_qty,
                        'warehouse_locations': warehouse_locations.get(upc_key, []),
                        'effective_qty': effective_qty,
                        'pending_sale_qty': pending_sale_qty,
                        'listing_qty': listing_qty,
                        'overage': listing_qty - warehouse_qty,
                        'severity': 'red',
                        'hash': snap_hash
                    })

        # 3. Warehouse has stock but item is not listed on eBay/Amazon/Facebook.
        for base_upc_key, raw_qty in warehouse_stock_by_base.items():
            if not base_upc_key or base_upc_key in invalid_upc_values:
                continue
            warehouse_qty = max(0, _safe_int(raw_qty, 0))
            if warehouse_qty <= 0:
                continue
            if base_upc_key in listed_base_upcs:
                continue

            source_tokens = warehouse_tokens_by_base.get(base_upc_key) or [base_upc_key]
            snap_hash = make_hash('no_listings', base_upc_key, source_tokens)
            if ('no_listings', snap_hash) in dismissed:
                continue

            title_val = rawbol_title_by_base.get(base_upc_key) or warehouse_title_by_base.get(base_upc_key) or ''
            image_val = rawbol_image_by_base.get(base_upc_key) or warehouse_image_by_base.get(base_upc_key) or ''
            alerts['no_listings'].append({
                'upc': rawbol_display_upc_by_base.get(base_upc_key) or warehouse_display_upc_by_base.get(base_upc_key) or base_upc_key,
                'warehouse_base_upc': base_upc_key,
                'title': title_val,
                'image': image_val,
                'warehouse_qty': warehouse_qty,
                'warehouse_locations': warehouse_locations_by_base.get(base_upc_key, []),
                'severity': 'yellow',
                'hash': snap_hash
            })

        # Keep Quantity Alert cards ordered by the largest overage first.
        alerts['quantity_alert'].sort(
            key=lambda a: (
                -_safe_int(a.get('overage'), 0),
                -_safe_int(a.get('listing_qty'), 0),
                str(a.get('upc') or '').lower(),
                str((a.get('listing') or {}).get('title') or '').lower()
            )
        )
        alerts['no_listings'].sort(
            key=lambda a: (
                -_safe_int(a.get('warehouse_qty'), 0),
                str(a.get('upc') or '').lower(),
                str(a.get('title') or '').lower()
            )
        )

        # Count totals
        counts = {
            'no_warehouse': len(alerts['no_warehouse']),
            'no_listings': len(alerts['no_listings']),
            'quantity_alert': len(alerts['quantity_alert']),
            'listing_matches': len(alerts['listing_matches']),
            'sync_overdue': int((alerts['sync_overdue'][0] or {}).get('overdue_count') or 0) if alerts['sync_overdue'] else 0
        }
        counts['total'] = counts['no_warehouse'] + counts['no_listings'] + counts['quantity_alert'] + counts['sync_overdue']

        payload = {'success': True, 'alerts': alerts, 'counts': counts}
        ss_caching._listing_helper_scan_cache_set(payload)
        return jsonify(payload)

    except Exception as e:
        return jsonify({'success': False, 'error': ss_errors._safe_error(e, 'listing-helper-scan')}), 500


def api_listing_helper_inventory_match():
    """Persist or clear a store-listing fallback to one exact warehouse row."""
    data = request.get_json() or {}
    store, listing_key = _normalize_store_listing_identity(
        data.get('store'), data.get('listing_key') or data.get('listing_id')
    )
    if not store or not listing_key:
        return jsonify({'success': False, 'error': 'Valid store and listing key are required'}), 400

    _ensure_listing_alerts_tables()
    clear_match = bool(data.get('clear'))
    from finder_aliases import update_alias
    alias_source = f'{store}:{listing_key.lower()}'
    try:
        with connect_db('listing_alerts.db') as match_conn:
            match_conn.row_factory = sqlite3.Row
            match_cur = match_conn.cursor()
            previous = match_cur.execute(
                'SELECT * FROM listing_inventory_matches WHERE store = ? AND LOWER(listing_key) = LOWER(?)',
                (store, listing_key)
            ).fetchone()

            if clear_match:
                match_cur.execute(
                    'DELETE FROM listing_inventory_matches WHERE store = ? AND LOWER(listing_key) = LOWER(?)',
                    (store, listing_key)
                )
                alias_undo = update_alias(match_conn, alias_source, undo_token=data.get('finder_alias_undo'))
                match_conn.commit()
                ss_caching._listing_helper_scan_cache_clear()
                return jsonify({
                    'success': True,
                    'cleared': True,
                    'finder_alias_undo': alias_undo,
                    'old_match': dict(previous) if previous else None
                })

            searchrack_id = ss_normalization._coerce_int(data.get('searchrack_id'), 0)
            if searchrack_id <= 0:
                return jsonify({'success': False, 'error': 'A warehouse item is required'}), 400

            with connect_db('searchRack.db') as rack_conn:
                rack_conn.row_factory = sqlite3.Row
                rack_match = ss_warehouse_matching._ready_to_ship_searchrack_match_by_id(rack_conn.cursor(), searchrack_id)
            if not rack_match or ss_normalization._coerce_int(rack_match.get('quantity'), 0) <= 0:
                return jsonify({'success': False, 'error': 'The selected warehouse item is unavailable'}), 409

            inventory_barcode = str(rack_match.get('barcode') or '').strip()
            inventory_location = ss_warehouse_matching._ready_to_ship_match_display_location(rack_match)
            match_cur.execute('''
                INSERT INTO listing_inventory_matches (
                    store, listing_key, listing_id, marketplace_barcode,
                    listing_title, searchrack_id, inventory_barcode,
                    inventory_location, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(store, listing_key) DO UPDATE SET
                    listing_id = excluded.listing_id,
                    marketplace_barcode = excluded.marketplace_barcode,
                    listing_title = excluded.listing_title,
                    searchrack_id = excluded.searchrack_id,
                    inventory_barcode = excluded.inventory_barcode,
                    inventory_location = excluded.inventory_location,
                    updated_at = CURRENT_TIMESTAMP
            ''', (
                store,
                listing_key,
                str(data.get('listing_id') or '').strip(),
                str(data.get('marketplace_barcode') or '').strip(),
                str(data.get('listing_title') or '').strip(),
                searchrack_id,
                inventory_barcode,
                inventory_location
            ))
            alias_undo = update_alias(
                match_conn, alias_source, inventory_barcode,
                data.get('listing_title') if data.get('finder_learn') else '',
                undo_token=data.get('finder_alias_undo')
            )
            match_conn.commit()
            saved = match_cur.execute(
                'SELECT * FROM listing_inventory_matches WHERE store = ? AND listing_key = ?',
                (store, listing_key)
            ).fetchone()

        ss_caching._listing_helper_scan_cache_clear()
        return jsonify({
            'success': True,
            'finder_alias_undo': alias_undo,
            'match': dict(saved) if saved else None,
            'old_match': dict(previous) if previous else None
        })
    except Exception as e:
        return jsonify({'success': False, 'error': ss_errors._safe_error(e, 'listing-helper:inventory-match')}), 500


def api_listing_helper_dismiss():
    """Dismiss an alert by storing its hash"""
    try:
        data = request.get_json() or {}
        alert_type = data.get('alert_type')
        snap_hash = data.get('hash')
        upc = data.get('upc', '')
        store = data.get('store', '')
        listing_ids = data.get('listing_ids', [])

        if not alert_type or not snap_hash:
            return jsonify({'success': False, 'error': 'alert_type and hash required'}), 400

        _ensure_listing_alerts_tables()
        conn = sqlite3.connect('listing_alerts.db')
        cur = conn.cursor()

        import json as json_module
        cur.execute('''
            INSERT OR REPLACE INTO dismissed_alerts (alert_type, upc, store, listing_ids, snapshot_hash)
            VALUES (?, ?, ?, ?, ?)
        ''', (alert_type, upc, store, json_module.dumps(listing_ids), snap_hash))
        conn.commit()
        conn.close()
        ss_caching._listing_helper_scan_cache_clear()

        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': ss_errors._safe_error(e, 'listing-helper-dismiss')}), 500


def api_listing_helper_undismiss():
    """Remove a dismissal to re-show an alert"""
    try:
        data = request.get_json() or {}
        snap_hash = data.get('hash')

        if not snap_hash:
            return jsonify({'success': False, 'error': 'hash required'}), 400

        conn = sqlite3.connect('listing_alerts.db')
        cur = conn.cursor()
        cur.execute('DELETE FROM dismissed_alerts WHERE snapshot_hash = ?', (snap_hash,))
        conn.commit()
        conn.close()
        ss_caching._listing_helper_scan_cache_clear()

        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': ss_errors._safe_error(e, 'listing-helper-undismiss')}), 500


def api_listing_helper_dismissed():
    """Get list of dismissed alerts"""
    try:
        _ensure_listing_alerts_tables()
        conn = sqlite3.connect('listing_alerts.db')
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute('SELECT * FROM dismissed_alerts ORDER BY dismissed_at DESC')
        rows = [dict(r) for r in cur.fetchall()]
        conn.close()
        return jsonify({'success': True, 'dismissed': rows})
    except Exception as e:
        return jsonify({'success': False, 'error': ss_errors._safe_error(e, 'listing-helper-dismissed')}), 500
