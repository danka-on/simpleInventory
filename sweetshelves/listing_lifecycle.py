"""Listing lifecycle for Sweet Shelves."""

import datetime
import os
import sqlite3
from . import errors as ss_errors, normalization as ss_normalization


def _listing_source_label(value):
    norm = ss_normalization._normalize_listing_source(value, default='system')
    if norm == 'listing_center':
        return 'Listing Agent'
    if norm == 'user':
        return 'User'
    return 'System'


def _marketplace_upc_lookup_variants(raw_upc):
    """
    UPC matching helper for cross-db marketplace checks:
    supports suffix variants and zero-padded/stripped numeric forms.
    """
    upc = ss_normalization._normalize_upc(raw_upc)
    if not upc:
        return []
    out = []

    def add(v):
        vv = str(v or '').strip().lower()
        if vv and vv not in out:
            out.append(vv)

    add(upc)
    if '-' in upc:
        base, suffix = upc.split('-', 1)
    else:
        base, suffix = upc, ''

    base = (base or '').strip()
    suffix = (suffix or '').strip()
    stripped_base = ss_normalization._strip_leading_zeros_numeric(base) or base

    add(base)
    add(stripped_base)
    if suffix:
        add(f'{base}-{suffix}')
        add(f'{stripped_base}-{suffix}')

    if base.isdigit() and len(base) <= 12:
        add(base.zfill(12))
        add(base.zfill(13))  # EAN-13 zero-padded form
    if stripped_base.isdigit() and len(stripped_base) <= 12:
        add(stripped_base.zfill(12))
        add(stripped_base.zfill(13))  # EAN-13 zero-padded form
    return out


def _is_marketplace_live_for_upc(upc, live_key_set):
    if not upc or not live_key_set:
        return False
    for key in _marketplace_upc_lookup_variants(upc):
        if key in live_key_set:
            return True
    return False


def _chunk_list(items, size):
    for i in range(0, len(items), size):
        yield items[i:i + size]


def _fetch_live_marketplace_upc_keys(upcs, *, max_lookup_keys=4000):
    """
    Return active listing key sets for eBay/Amazon from store DBs.
    Keeps /items-to-list checkboxes aligned with currently live listings.
    """
    key_pool = set()
    for upc in upcs or []:
        for key in _marketplace_upc_lookup_variants(upc):
            key_pool.add(key)

    payload = {
        'ebay': set(),
        'amazon': set(),
        'ebay_checked': False,
        'amazon_checked': False,
    }
    if not key_pool:
        return payload

    # Avoid expensive cross-db scans on huge pages (ex: analytics calls with limit=50000).
    if len(key_pool) > max_lookup_keys:
        return payload

    keys = sorted(key_pool)
    chunk_size = 700  # stay below SQLite parameter limits

    try:
        with sqlite3.connect('ebayStore.db') as conn:
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()
            for part in _chunk_list(keys, chunk_size):
                placeholders = ','.join('?' for _ in part)
                cur.execute(f'''
                    SELECT UPC
                    FROM INVENTORY
                    WHERE UPC IS NOT NULL
                      AND TRIM(UPC) != ''
                      AND LOWER(TRIM(COALESCE(List_State, ''))) IN ('active', 'live', 'listed')
                      AND TRIM(COALESCE(ItemID, '')) != ''
                      AND COALESCE(CAST(Quantity AS INTEGER), 0) > 0
                      AND LOWER(TRIM(UPC)) IN ({placeholders})
                ''', tuple(part))
                for row in cur.fetchall():
                    for key in _marketplace_upc_lookup_variants(row['UPC']):
                        payload['ebay'].add(key)
            payload['ebay_checked'] = True
    except Exception as e:
        print(f"[_fetch_live_marketplace_upc_keys] eBay lookup failed: {e}")

    try:
        with sqlite3.connect('amazonStore.db') as conn:
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()
            for part in _chunk_list(keys, chunk_size):
                placeholders = ','.join('?' for _ in part)
                cur.execute(f'''
                    SELECT UPC
                    FROM ITEMS
                    WHERE UPC IS NOT NULL
                      AND TRIM(UPC) != ''
                      AND LOWER(TRIM(COALESCE(STATUS, ''))) IN ('active', 'live', 'listed')
                      AND TRIM(COALESCE(ASIN, '')) != ''
                      AND COALESCE(CAST(QUANTITY AS INTEGER), 0) > 0
                      AND LOWER(TRIM(UPC)) IN ({placeholders})
                ''', tuple(part))
                for row in cur.fetchall():
                    for key in _marketplace_upc_lookup_variants(row['UPC']):
                        payload['amazon'].add(key)
            payload['amazon_checked'] = True
    except Exception as e:
        print(f"[_fetch_live_marketplace_upc_keys] Amazon lookup failed: {e}")

    return payload


def _fetch_auto_marketplace_listing_links(upcs, *, max_lookup_keys=4000, max_links_per_platform=40):
    """
    Return live marketplace listing links keyed by normalized UPC-variant key.
    This is display-only enrichment for /items-to-list auto listing preview.
    """
    key_pool = set()
    for upc in upcs or []:
        for key in _marketplace_upc_lookup_variants(upc):
            key_pool.add(key)

    if not key_pool:
        return {}
    if len(key_pool) > max_lookup_keys:
        return {}

    listing_map = {}
    dedupe_map = {}
    keys_sorted = sorted(key_pool)
    chunk_size = 700
    amazon_live_max_age_days = ss_normalization._coerce_int(os.getenv('ITEMS_TO_LIST_AMAZON_LIVE_MAX_AGE_DAYS', 45), 45)
    if amazon_live_max_age_days < 0:
        amazon_live_max_age_days = 0
    now_utc = datetime.datetime.utcnow()

    def _is_recent_amazon_listing(last_updated_raw):
        if amazon_live_max_age_days <= 0:
            return True
        dt = ss_normalization._parse_iso_utc_naive(last_updated_raw)
        if dt is None:
            return False
        age_seconds = (now_utc - dt).total_seconds()
        return age_seconds <= (amazon_live_max_age_days * 86400)

    def _add_listing(raw_upc, platform, listing_id='', title='', url=''):
        upc_variants = _marketplace_upc_lookup_variants(raw_upc)
        if not upc_variants:
            return
        listing_id = str(listing_id or '').strip()
        title = str(title or '').strip()
        url = str(url or '').strip()
        if not url and platform == 'ebay' and listing_id:
            url = f'https://www.ebay.com/itm/{listing_id}'
        if not url and platform == 'amazon' and listing_id and listing_id.upper().startswith('B'):
            url = f'https://www.amazon.com/dp/{listing_id}'
        if not listing_id and not url:
            return

        token = f"{listing_id.lower()}|{url.lower()}"
        for key in upc_variants:
            if key not in key_pool:
                continue
            bucket = listing_map.get(key)
            if bucket is None:
                bucket = {'ebay': [], 'amazon': []}
                listing_map[key] = bucket
            dedupe_bucket = dedupe_map.get(key)
            if dedupe_bucket is None:
                dedupe_bucket = {'ebay': set(), 'amazon': set()}
                dedupe_map[key] = dedupe_bucket
            if token in dedupe_bucket[platform]:
                continue
            if len(bucket[platform]) >= max_links_per_platform:
                continue
            dedupe_bucket[platform].add(token)
            bucket[platform].append({
                'platform': platform,
                'listing_id': listing_id,
                'title': title,
                'url': url
            })

    try:
        with sqlite3.connect('ebayStore.db') as conn:
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()
            cur.execute("PRAGMA table_info('INVENTORY')")
            cols = [r[1] for r in cur.fetchall()]
            cols_lower = {c.lower(): c for c in cols}
            upc_col = cols_lower.get('upc') or cols_lower.get('barcode')
            state_col = cols_lower.get('list_state') or cols_lower.get('status')
            item_id_col = cols_lower.get('itemid') or cols_lower.get('item_id')
            url_col = cols_lower.get('url') or cols_lower.get('viewitemurl')
            title_col = cols_lower.get('title')
            qty_col = cols_lower.get('quantity')
            order_col = cols_lower.get('list_date') or cols_lower.get('created_at')

            if upc_col:
                upc_where = f"{upc_col} IS NOT NULL AND TRIM({upc_col}) != ''"
                state_where = f"LOWER(TRIM(COALESCE({state_col}, ''))) IN ('active', 'live', 'listed')" if state_col else "1=1"
                item_where = f"TRIM(COALESCE({item_id_col}, '')) != ''" if item_id_col else "1=1"
                qty_where = f"COALESCE(CAST({qty_col} AS INTEGER), 0) > 0" if qty_col else "1=1"
                item_expr = f"{item_id_col} AS ITEM_ID" if item_id_col else "'' AS ITEM_ID"
                url_expr = f"{url_col} AS URL" if url_col else "'' AS URL"
                title_expr = f"{title_col} AS TITLE" if title_col else "'' AS TITLE"
                order_sql = f" ORDER BY {order_col} DESC" if order_col else ""

                for part in _chunk_list(keys_sorted, chunk_size):
                    placeholders = ','.join('?' for _ in part)
                    cur.execute(f'''
                        SELECT {upc_col} AS UPC_RAW, {item_expr}, {url_expr}, {title_expr}
                        FROM INVENTORY
                        WHERE {upc_where}
                          AND {state_where}
                          AND {item_where}
                          AND {qty_where}
                          AND LOWER(TRIM({upc_col})) IN ({placeholders})
                        {order_sql}
                    ''', tuple(part))
                    for row in cur.fetchall():
                        _add_listing(
                            row['UPC_RAW'],
                            'ebay',
                            listing_id=row['ITEM_ID'],
                            title=row['TITLE'],
                            url=row['URL']
                        )
    except Exception as e:
        print(f"[_fetch_auto_marketplace_listing_links] eBay lookup failed: {e}")

    try:
        with sqlite3.connect('amazonStore.db') as conn:
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()
            cur.execute("PRAGMA table_info('ITEMS')")
            cols = [r[1] for r in cur.fetchall()]
            cols_lower = {c.lower(): c for c in cols}
            upc_col = cols_lower.get('upc') or cols_lower.get('barcode')
            status_col = cols_lower.get('status')
            asin_col = cols_lower.get('asin')
            sku_col = cols_lower.get('sku')
            url_col = cols_lower.get('product_url') or cols_lower.get('url')
            title_col = cols_lower.get('title')
            qty_col = cols_lower.get('quantity')
            order_col = cols_lower.get('last_updated') or cols_lower.get('created_at')

            if upc_col:
                upc_where = f"{upc_col} IS NOT NULL AND TRIM({upc_col}) != ''"
                status_where = f"LOWER(TRIM(COALESCE({status_col}, ''))) IN ('active', 'live', 'listed')" if status_col else "1=1"
                qty_where = f"COALESCE(CAST({qty_col} AS INTEGER), 0) > 0" if qty_col else "1=1"
                if asin_col:
                    live_id_where = f"TRIM(COALESCE({asin_col}, '')) != ''"
                elif sku_col:
                    live_id_where = f"TRIM(COALESCE({sku_col}, '')) != ''"
                else:
                    live_id_where = "1=1"
                asin_expr = f"{asin_col} AS ASIN" if asin_col else "'' AS ASIN"
                sku_expr = f"{sku_col} AS SKU" if sku_col else "'' AS SKU"
                url_expr = f"{url_col} AS URL" if url_col else "'' AS URL"
                title_expr = f"{title_col} AS TITLE" if title_col else "'' AS TITLE"
                updated_expr = f"{order_col} AS LAST_UPDATED_RAW" if order_col else "NULL AS LAST_UPDATED_RAW"
                order_sql = f" ORDER BY {order_col} DESC" if order_col else ""

                for part in _chunk_list(keys_sorted, chunk_size):
                    placeholders = ','.join('?' for _ in part)
                    cur.execute(f'''
                        SELECT {upc_col} AS UPC_RAW, {asin_expr}, {sku_expr}, {url_expr}, {title_expr}, {updated_expr}
                        FROM ITEMS
                        WHERE {upc_where}
                          AND {status_where}
                          AND {qty_where}
                          AND {live_id_where}
                          AND LOWER(TRIM({upc_col})) IN ({placeholders})
                        {order_sql}
                    ''', tuple(part))
                    for row in cur.fetchall():
                        if not _is_recent_amazon_listing(row['LAST_UPDATED_RAW']):
                            continue
                        asin = str(row['ASIN'] or '').strip()
                        sku = str(row['SKU'] or '').strip()
                        listing_id = asin or sku
                        listing_url = str(row['URL'] or '').strip()
                        if not listing_url and asin:
                            listing_url = f"https://www.amazon.com/dp/{asin}"
                        _add_listing(
                            row['UPC_RAW'],
                            'amazon',
                            listing_id=listing_id,
                            title=row['TITLE'],
                            url=listing_url
                        )
    except Exception as e:
        print(f"[_fetch_auto_marketplace_listing_links] Amazon lookup failed: {e}")

    return listing_map


def _apply_marketplace_status_overlay(rows):
    """
    Normalize marketplace status + source payload for UI rows.
    Manual list-status in bol.db is the source of truth for /items-to-list.
    Live marketplace DBs are intentionally not used here.
    """
    if not rows:
        return

    for row in rows:
        row['listed_ebay_live'] = None
        row['listed_amazon_live'] = None

        for mp in ('amazon', 'ebay', 'facebook'):
            listed_key = f'listed_{mp}'
            source_key = f'listed_{mp}_source'
            source_label_key = f'{source_key}_label'
            listed = int(row.get(listed_key) or 0) == 1
            source = (row.get(source_key) or '').strip()
            if listed and not source:
                source = 'system'
            row[source_key] = source
            row[source_label_key] = _listing_source_label(source) if listed else ''


def _listing_center_mark_bol_listed(marketplace, *, upc='', lot_number='', item_id=None):
    """
    Mark exactly one bol_items row as listed for Listing Center flows.
    Resolution order: explicit id, then (upc, lot), then newest by upc.
    """
    mp = (marketplace or '').strip().lower()
    if mp not in ('amazon', 'ebay', 'facebook'):
        return {'success': False, 'error': 'invalid marketplace'}

    upc_n = ss_normalization._strip_leading_zeros_numeric(ss_normalization._normalize_upc(upc))
    lot_n = ss_normalization._normalize_lot_number(lot_number)

    target_id = None
    if item_id is not None and str(item_id).strip() != '':
        try:
            target_id = int(item_id)
        except Exception:
            target_id = None
        if target_id is not None and target_id <= 0:
            target_id = None

    if not upc_n and target_id is None:
        return {'success': False, 'error': 'missing upc or id'}

    _ensure_bol_list_status_column()
    conn = None
    try:
        conn = sqlite3.connect('bol.db', isolation_level='IMMEDIATE')
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()

        row = None
        if target_id is not None:
            cur.execute('''
                SELECT id, upc, COALESCE(lot_number, '') AS lot_number
                FROM bol_items
                WHERE id = ?
                LIMIT 1
            ''', (target_id,))
            row = cur.fetchone()
        else:
            if lot_n:
                cur.execute('''
                    SELECT id, upc, COALESCE(lot_number, '') AS lot_number
                    FROM bol_items
                    WHERE TRIM(upc) = ? COLLATE NOCASE
                      AND lot_number = ? COLLATE NOCASE
                    ORDER BY import_date DESC, id DESC
                    LIMIT 1
                ''', (upc_n, lot_n))
                row = cur.fetchone()
            if not row:
                cur.execute('''
                    SELECT id, upc, COALESCE(lot_number, '') AS lot_number
                    FROM bol_items
                    WHERE TRIM(upc) = ? COLLATE NOCASE
                    ORDER BY import_date DESC, id DESC
                    LIMIT 1
                ''', (upc_n,))
                row = cur.fetchone()

        if not row:
            return {'success': False, 'error': 'target row not found'}

        resolved_id = int(row['id'])
        resolved_upc = ss_normalization._strip_leading_zeros_numeric(ss_normalization._normalize_upc(row['upc'])) or upc_n
        resolved_lot = ss_normalization._normalize_lot_number(row['lot_number'])
        now_iso = datetime.datetime.now(datetime.UTC).isoformat()

        if mp == 'amazon':
            cur.execute('''
                UPDATE bol_items
                SET listed_amazon = 1,
                    listed_amazon_date = ?,
                    listed_amazon_source = 'listing_center'
                WHERE id = ?
            ''', (now_iso, resolved_id))
        elif mp == 'ebay':
            cur.execute('''
                UPDATE bol_items
                SET listed_ebay = 1,
                    listed_ebay_date = ?,
                    listed_ebay_source = 'listing_center'
                WHERE id = ?
            ''', (now_iso, resolved_id))
        else:
            cur.execute('''
                UPDATE bol_items
                SET listed_facebook = 1,
                    listed_facebook_date = ?,
                    listed_facebook_source = 'listing_center'
                WHERE id = ?
            ''', (now_iso, resolved_id))

        cur.execute('''
            UPDATE bol_items
            SET list_status = CASE
                WHEN COALESCE(listed_amazon, 0) = 1
                  OR COALESCE(listed_ebay, 0) = 1
                  OR COALESCE(listed_facebook, 0) = 1
                THEN 'listed'
                ELSE NULL
            END
            WHERE id = ?
        ''', (resolved_id,))

        conn.commit()
        return {
            'success': True,
            'id': resolved_id,
            'upc': resolved_upc,
            'lot_number': resolved_lot
        }
    except Exception as e:
        try:
            if conn:
                conn.rollback()
        except Exception:
            pass
        return {'success': False, 'error': ss_errors._safe_error(e, 'listing_center:mark_listed')}
    finally:
        try:
            if conn:
                conn.close()
        except Exception:
            pass


def _ensure_bol_list_status_column():
    """Ensure bol_items has list_status, temporary, and quantity tracking columns."""
    print("[_ensure_bol_list_status_column] Starting migration check...")
    conn = None
    try:
        conn = sqlite3.connect('bol.db')
        cur = conn.cursor()
        cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='bol_items'")
        if not cur.fetchone():
            print("[_ensure_bol_list_status_column] bol_items table not found")
            return
        cur.execute("PRAGMA table_info(bol_items)")
        # Use lowercase for case-insensitive comparison
        cols = [r[1].lower() for r in cur.fetchall()]
        print(f"[_ensure_bol_list_status_column] Found {len(cols)} columns in bol_items")
        
        # Add list_status and temporary columns
        if 'list_status' not in cols:
            print("[_ensure_bol_list_status_column] Adding list_status column")
            cur.execute("ALTER TABLE bol_items ADD COLUMN list_status TEXT")
        if 'temporary' not in cols:
            print("[_ensure_bol_list_status_column] Adding temporary column")
            cur.execute("ALTER TABLE bol_items ADD COLUMN temporary INTEGER DEFAULT 0")
        
        # NEW: Add quantity tracking columns for robust prep workflow
        if 'original_qty' not in cols:
            print("📊 Adding original_qty column and backfilling from quantity...")
            cur.execute("ALTER TABLE bol_items ADD COLUMN original_qty INTEGER")
            # Backfill from quantity column
            cur.execute("UPDATE bol_items SET original_qty = quantity WHERE original_qty IS NULL")
            
        if 'good_qty' not in cols:
            print("✅ Adding good_qty column...")
            cur.execute("ALTER TABLE bol_items ADD COLUMN good_qty INTEGER DEFAULT 0")
            # Backfill from items_prep_status for items already marked good
            cur.execute('''
                UPDATE bol_items 
                SET good_qty = (
                    SELECT COALESCE(quantity, 0) 
                    FROM items_prep_status 
                    WHERE items_prep_status.upc = bol_items.upc 
                    AND COALESCE(items_prep_status.lot_number, '') = COALESCE(bol_items.lot_number, '')
                    AND items_prep_status.status = 'good'
                )
                WHERE EXISTS (
                    SELECT 1 FROM items_prep_status 
                    WHERE items_prep_status.upc = bol_items.upc 
                    AND COALESCE(items_prep_status.lot_number, '') = COALESCE(bol_items.lot_number, '')
                    AND items_prep_status.status = 'good'
                )
            ''')
            
        if 'bad_qty' not in cols:
            print("❌ Adding bad_qty column...")
            cur.execute("ALTER TABLE bol_items ADD COLUMN bad_qty INTEGER DEFAULT 0")
            # Calculate bad_qty from suffixed entries
            cur.execute('''
                UPDATE bol_items 
                SET bad_qty = (
                    SELECT COALESCE(SUM(quantity), 0)
                    FROM bol_items AS suffixed
                    WHERE suffixed.upc LIKE bol_items.upc || '-%'
                    AND suffixed.temporary = 1
                )
                WHERE upc NOT LIKE '%-%'
            ''')
            
        if 'unchecked_qty' not in cols:
            print("📦 Adding unchecked_qty column and calculating from original - good - bad...")
            cur.execute("ALTER TABLE bol_items ADD COLUMN unchecked_qty INTEGER")
            # Backfill: original - good - bad = unchecked
            cur.execute('''
                UPDATE bol_items 
                SET unchecked_qty = COALESCE(original_qty, 0) - COALESCE(good_qty, 0) - COALESCE(bad_qty, 0)
                WHERE unchecked_qty IS NULL
            ''')
        
        # NEW: Add marketplace-specific listing columns with timestamps
        if 'listed_amazon' not in cols:
            print("🛒 Adding Amazon listing tracking columns...")
            cur.execute("ALTER TABLE bol_items ADD COLUMN listed_amazon INTEGER DEFAULT 0")
        else:
            print("[_ensure_bol_list_status_column] listed_amazon column already exists")
            
        if 'listed_amazon_date' not in cols:
            print("🛒 Adding Amazon listing date column...")
            cur.execute("ALTER TABLE bol_items ADD COLUMN listed_amazon_date TEXT")
        else:
            print("[_ensure_bol_list_status_column] listed_amazon_date column already exists")
            
        if 'listed_ebay' not in cols:
            print("🏷️ Adding eBay listing tracking columns...")
            cur.execute("ALTER TABLE bol_items ADD COLUMN listed_ebay INTEGER DEFAULT 0")
        else:
            print("[_ensure_bol_list_status_column] listed_ebay column already exists")
            
        if 'listed_ebay_date' not in cols:
            print("🏷️ Adding eBay listing date column...")
            cur.execute("ALTER TABLE bol_items ADD COLUMN listed_ebay_date TEXT")
        else:
            print("[_ensure_bol_list_status_column] listed_ebay_date column already exists")
            
        if 'listed_facebook' not in cols:
            print("📘 Adding Facebook listing tracking columns...")
            cur.execute("ALTER TABLE bol_items ADD COLUMN listed_facebook INTEGER DEFAULT 0")
        else:
            print("[_ensure_bol_list_status_column] listed_facebook column already exists")
            
        if 'listed_facebook_date' not in cols:
            print("📘 Adding Facebook listing date column...")
            cur.execute("ALTER TABLE bol_items ADD COLUMN listed_facebook_date TEXT")
        else:
            print("[_ensure_bol_list_status_column] listed_facebook_date column already exists")
        
        if 'listed_facebook_qty' not in cols:
            print("📘 Adding Facebook listing quantity column...")
            cur.execute("ALTER TABLE bol_items ADD COLUMN listed_facebook_qty INTEGER")
            # Backfill quantity for existing FB listings
            try:
                cur.execute('''
                    UPDATE bol_items
                    SET listed_facebook_qty = COALESCE(quantity, 1)
                    WHERE COALESCE(listed_facebook, 0) = 1
                    AND listed_facebook_qty IS NULL
                ''')
            except Exception:
                pass

        if 'listed_amazon_source' not in cols:
            print("🛒 Adding Amazon listing source column...")
            cur.execute("ALTER TABLE bol_items ADD COLUMN listed_amazon_source TEXT")
        if 'listed_ebay_source' not in cols:
            print("🏷️ Adding eBay listing source column...")
            cur.execute("ALTER TABLE bol_items ADD COLUMN listed_ebay_source TEXT")
        if 'listed_facebook_source' not in cols:
            print("📘 Adding Facebook listing source column...")
            cur.execute("ALTER TABLE bol_items ADD COLUMN listed_facebook_source TEXT")

        # One-time source backfill so future requests avoid full-table updates.
        try:
            cur.execute('CREATE TABLE IF NOT EXISTS app_metadata (key TEXT PRIMARY KEY, value TEXT)')
            cur.execute('SELECT value FROM app_metadata WHERE key = ? LIMIT 1', ('listing_source_backfill_v1',))
            marker_row = cur.fetchone()
            marker = (marker_row[0] if marker_row else '')
            if str(marker) != '1':
                cur.execute('''
                    UPDATE bol_items
                    SET listed_amazon_source = 'system'
                    WHERE COALESCE(listed_amazon, 0) = 1
                      AND (listed_amazon_source IS NULL OR TRIM(listed_amazon_source) = '')
                ''')
                cur.execute('''
                    UPDATE bol_items
                    SET listed_ebay_source = 'system'
                    WHERE COALESCE(listed_ebay, 0) = 1
                      AND (listed_ebay_source IS NULL OR TRIM(listed_ebay_source) = '')
                ''')
                cur.execute('''
                    UPDATE bol_items
                    SET listed_facebook_source = 'system'
                    WHERE COALESCE(listed_facebook, 0) = 1
                      AND (listed_facebook_source IS NULL OR TRIM(listed_facebook_source) = '')
                ''')
                cur.execute(
                    'INSERT OR REPLACE INTO app_metadata (key, value) VALUES (?, ?)',
                    ('listing_source_backfill_v1', '1')
                )
        except Exception as src_backfill_err:
            print(f"Warning: Could not backfill listing source columns: {src_backfill_err}")

        # Legacy migration disabled in normal request path.
        # Reason: list_status is still maintained as "listed if any marketplace is listed".
        # Auto-converting list_status='listed' -> listed_amazon=1 can incorrectly re-enable
        # Amazon for eBay/Facebook-only rows.
        try:
            cur.execute('CREATE TABLE IF NOT EXISTS app_metadata (key TEXT PRIMARY KEY, value TEXT)')
            cur.execute(
                'INSERT OR REPLACE INTO app_metadata (key, value) VALUES (?, ?)',
                ('legacy_list_status_auto_migration_disabled_v1', '1')
            )
        except Exception as e:
            print(f"Warning: Could not write legacy migration disable marker: {e}")
        
        conn.commit()
        # print("✅ Quantity tracking columns migrated successfully") # Reduce noise
    except Exception as e:
        print('Failed ensuring quantity columns in bol_items:', e)
        import traceback
        traceback.print_exc()
    finally:
        if conn is not None:
            conn.close()


def _legacy_migrate_list_status_to_amazon(cur):
    """
    Legacy backfill helper:
    map old generic list_status='listed' rows to listed_amazon=1.

    IMPORTANT: This should be invoked explicitly (debug/maintenance only),
    not on the normal request path.
    """
    cur.execute("""
        UPDATE bol_items
        SET listed_amazon = 1,
            listed_amazon_date = datetime('now'),
            listed_amazon_source = COALESCE(NULLIF(TRIM(listed_amazon_source), ''), 'system')
        WHERE LOWER(TRIM(COALESCE(list_status, ''))) = 'listed'
          AND COALESCE(listed_amazon, 0) = 0
    """)
    return cur.rowcount or 0


def ensure_lifecycle_tables():
    """Ensure returns table has lifecycle columns and create lifecycle events table"""
    conn = None
    try:
        conn = sqlite3.connect('sold.db')
        cur = conn.cursor()
        
        # Add lifecycle columns to returns table if they don't exist
        try:
            cur.execute("ALTER TABLE returns ADD COLUMN relisted INTEGER DEFAULT 0")
        except sqlite3.OperationalError:
            pass  # Column already exists
        
        try:
            cur.execute("ALTER TABLE returns ADD COLUMN relisted_date TEXT")
        except sqlite3.OperationalError:
            pass
        
        try:
            cur.execute("ALTER TABLE returns ADD COLUMN relisted_store TEXT")
        except sqlite3.OperationalError:
            pass
        
        try:
            cur.execute("ALTER TABLE returns ADD COLUMN relisted_item_id TEXT")
        except sqlite3.OperationalError:
            pass
        
        try:
            cur.execute("ALTER TABLE returns ADD COLUMN resold INTEGER DEFAULT 0")
        except sqlite3.OperationalError:
            pass
        
        try:
            cur.execute("ALTER TABLE returns ADD COLUMN resold_date TEXT")
        except sqlite3.OperationalError:
            pass
        
        try:
            cur.execute("ALTER TABLE returns ADD COLUMN resold_order_id TEXT")
        except sqlite3.OperationalError:
            pass
        
        try:
            cur.execute("ALTER TABLE returns ADD COLUMN lifecycle_count INTEGER DEFAULT 1")
        except sqlite3.OperationalError:
            pass
        
        # Create lifecycle events table
        cur.execute('''
            CREATE TABLE IF NOT EXISTS return_lifecycle_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                return_id INTEGER NOT NULL,
                event_type TEXT NOT NULL,
                event_date TEXT NOT NULL,
                auto_detected INTEGER DEFAULT 0,
                store TEXT,
                item_id TEXT,
                order_id TEXT,
                notes TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (return_id) REFERENCES returns (id) ON DELETE CASCADE
            )
        ''')
        
        # Create payouts table
        cur.execute('''
            CREATE TABLE IF NOT EXISTS payouts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                store TEXT NOT NULL,
                settlement_id TEXT UNIQUE NOT NULL,
                start_date TEXT,
                end_date TEXT,
                payout_date TEXT,
                amount REAL DEFAULT 0,
                currency TEXT DEFAULT 'USD',
                status TEXT,
                transaction_count INTEGER DEFAULT 0,
                synced_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        conn.commit()
        print("✅ Lifecycle tables initialized")
        print("✅ Payouts table initialized")
    except Exception as e:
        print(f"⚠️ Error initializing lifecycle tables: {e}")
    finally:
        if conn is not None:
            conn.close()
