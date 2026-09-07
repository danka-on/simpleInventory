"""Bulk manifest for Sweet Shelves."""

import datetime
import os
import random
import sqlite3
import time
from PIL import Image, ImageDraw, ImageFont, ImageOps
from flask import jsonify, render_template, request
from . import (
    config as ss_config, database as ss_database, errors as ss_errors, manifest_google as
    ss_manifest_google, normalization as ss_normalization, runtime as ss_runtime, warehouse_locations as
    ss_warehouse_locations,
)


def bulk_manifest_page():
    """Bulk manifest builder for scanned barcode sale sheets."""
    return render_template('bulk_manifest.html')


def bulk_manifest_pictures_page(manifest_id):
    """Simple gallery/download page for one bulk manifest's saved pictures."""
    try:
        with ss_database.db_connection('marketplace.db') as conn:
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()
            _ensure_bulk_manifest_tables(cur)
            cur.execute('SELECT id, manifest_title, manifest_date, created_at FROM bulk_manifests WHERE id = ?', (manifest_id,))
            manifest_row = cur.fetchone()
            if not manifest_row:
                return render_template(
                    'bulk_manifest_photos.html',
                    manifest={'id': manifest_id, 'manifest_title': f'Bulk Manifest {manifest_id}', 'manifest_date': '', 'created_at': ''},
                    photos=[],
                    photo_count=0,
                    error='Manifest not found'
                ), 404
            photos = _bulk_manifest_load_photos(cur, manifest_id)

        manifest = {
            'id': manifest_id,
            'manifest_title': str(manifest_row['manifest_title'] or '').strip() or f'Bulk Manifest {manifest_id}',
            'manifest_date': str(manifest_row['manifest_date'] or '').strip(),
            'created_at': str(manifest_row['created_at'] or '').strip()
        }
        return render_template(
            'bulk_manifest_photos.html',
            manifest=manifest,
            photos=photos,
            photo_count=len(photos),
            error=''
        )
    except Exception as e:
        return render_template(
            'bulk_manifest_photos.html',
            manifest={'id': manifest_id, 'manifest_title': f'Bulk Manifest {manifest_id}', 'manifest_date': '', 'created_at': ''},
            photos=[],
            photo_count=0,
            error=ss_errors._safe_error(e, 'bulk_manifest_photos_page')
        ), 500


def _bulk_manifest_candidate_barcodes(raw_barcode):
    """Build a prioritized set of barcode candidates for raw_bol_items lookup."""
    normalized = ss_normalization._normalize_upc_preserve_suffix_for_match(ss_normalization._normalize_scanned_upc(raw_barcode))
    if not normalized:
        return []

    candidates = []
    seen = set()

    def _add(value):
        v = ss_normalization._normalize_upc_preserve_suffix_for_match(ss_normalization._normalize_upc(value))
        v = str(v or '').strip()
        if not v:
            return
        key = v.lower()
        if key in seen:
            return
        seen.add(key)
        candidates.append(v)

    _add(normalized)
    base = normalized.split('-', 1)[0].strip()
    stripped_base = ss_normalization._strip_leading_zeros_numeric(base)
    _add(base)
    _add(stripped_base)

    if base.isdigit():
        for width in (12, 13):
            if len(base) <= width:
                _add(base.zfill(width))
            if stripped_base and stripped_base.isdigit() and len(stripped_base) <= width:
                _add(stripped_base.zfill(width))

    return candidates


def _bulk_manifest_pick_price_column(raw_cols):
    raw_cols = raw_cols or {}
    preferred_price_keys = (
        'original retail',
        'original_retail',
        'original retail price',
        'original_retail_price',
        'originalretail',
        'retail price',
        'retail_price'
    )
    for key in preferred_price_keys:
        if key in raw_cols:
            return raw_cols[key]

    for key, raw_name in raw_cols.items():
        compact = key.replace(' ', '').replace('_', '').replace('-', '')
        if 'original' in compact and 'retail' in compact:
            return raw_name

    return raw_cols.get('unit_price')


def _bulk_manifest_rawbol_price_column(cur):
    cur.execute('PRAGMA table_info(raw_bol_items)')
    raw_cols = {str(r[1]).lower(): str(r[1]) for r in cur.fetchall()}
    return _bulk_manifest_pick_price_column(raw_cols)


def _bulk_manifest_price_numeric_expr(price_col):
    safe_col = '"' + str(price_col or '').replace('"', '""') + '"'
    return (
        "CAST(REPLACE(REPLACE(REPLACE(TRIM(CAST(COALESCE("
        + safe_col +
        ", '') AS TEXT)), '$', ''), ',', ''), ' ', '') AS REAL)"
    )


def _bulk_manifest_lot_average_price(cur, lot_number, price_col=None, cache=None):
    lot_n = ss_normalization._normalize_lot_number(lot_number)
    if not lot_n or cur is None:
        return 0.0

    cache_key = lot_n.lower()
    if isinstance(cache, dict) and cache_key in cache:
        return cache[cache_key]

    avg_price = 0.0
    try:
        price_col = price_col or _bulk_manifest_rawbol_price_column(cur)
        if price_col:
            numeric_expr = _bulk_manifest_price_numeric_expr(price_col)
            cur.execute(f'''
                SELECT AVG({numeric_expr}) AS lot_avg
                FROM raw_bol_items
                WHERE lot_number = ? COLLATE NOCASE
                  AND {numeric_expr} > 0
            ''', (lot_n,))
            row = cur.fetchone()
            if row is not None:
                if isinstance(row, sqlite3.Row):
                    avg_price = _bulk_manifest_money(row.get('lot_avg'), default=0.0)
                else:
                    avg_price = _bulk_manifest_money(row[0], default=0.0)
    except Exception:
        avg_price = 0.0

    avg_price = _bulk_manifest_money(avg_price, default=0.0)
    if avg_price <= 0:
        avg_price = 0.0

    if isinstance(cache, dict):
        cache[cache_key] = avg_price
    return avg_price


def _bulk_manifest_random_unit_price(min_cents=999, max_cents=2999):
    lower = max(1, ss_normalization._coerce_int(min_cents, 999))
    upper = max(lower, ss_normalization._coerce_int(max_cents, 2999))
    return round(random.randint(lower, upper) / 100.0, 2)


def _bulk_manifest_resolve_unit_price(raw_value, *, lot_avg_price=0.0, default=None):
    unit_price = _bulk_manifest_money(raw_value, default=0.0)
    if unit_price > 0:
        return unit_price

    lot_avg = _bulk_manifest_money(lot_avg_price, default=0.0)
    if lot_avg > 0:
        return lot_avg

    fallback = _bulk_manifest_money(default, default=0.0)
    if fallback <= 0:
        fallback = _bulk_manifest_random_unit_price()
    return fallback


def _bulk_manifest_lookup_rawbol(raw_barcode):
    """Return best raw_bol_items match for barcode, or None."""
    candidates = _bulk_manifest_candidate_barcodes(raw_barcode)
    if not candidates:
        return None

    conn = None
    try:
        conn = sqlite3.connect('rawbol.db')
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        price_col = _bulk_manifest_rawbol_price_column(cur)
        if price_col:
            safe_price_col = '"' + price_col.replace('"', '""') + '"'
            price_expr = f'{safe_price_col} AS unit_price'
        else:
            price_expr = '0 AS unit_price'
        select_cols = f'upc, item_description, image_url, lot_number, quantity, import_date, {price_expr}'
        lot_avg_cache = {}

        def _with_lot_avg(raw_row):
            if not raw_row:
                return None
            out = dict(raw_row)
            lot_n = ss_normalization._normalize_lot_number(out.get('lot_number'))
            out['lot_number'] = lot_n
            out['lot_avg_price'] = _bulk_manifest_lot_average_price(
                cur,
                lot_n,
                price_col=price_col,
                cache=lot_avg_cache
            )
            return out

        for candidate in candidates:
            cur.execute('''
                SELECT ''' + select_cols + '''
                FROM raw_bol_items
                WHERE upc = ? COLLATE NOCASE
                ORDER BY COALESCE(import_date, '') DESC, rowid DESC
                LIMIT 1
            ''', (candidate,))
            row = cur.fetchone()
            if row:
                return _with_lot_avg(row)

        stripped = ss_normalization._strip_leading_zeros_numeric(candidates[0])
        if stripped and len(stripped) >= 8:
            cur.execute('''
                SELECT ''' + select_cols + '''
                FROM raw_bol_items
                WHERE REPLACE(COALESCE(upc, ''), '.0', '') LIKE ? COLLATE NOCASE
                ORDER BY COALESCE(import_date, '') DESC, rowid DESC
                LIMIT 1
            ''', (f'%{stripped}',))
            row = cur.fetchone()
            if row:
                return _with_lot_avg(row)

        if stripped and stripped.isdigit():
            cur.execute('''
                SELECT ''' + select_cols + '''
                FROM raw_bol_items
                WHERE CAST(CAST(REPLACE(COALESCE(upc, ''), '.0', '') AS INTEGER) AS TEXT) = ?
                ORDER BY COALESCE(import_date, '') DESC, rowid DESC
                LIMIT 1
            ''', (stripped,))
            row = cur.fetchone()
            if row:
                return _with_lot_avg(row)
    finally:
        if conn is not None:
            conn.close()

    return None


def api_bulk_manifest_lookup():
    """Lookup title metadata in rawbol.db for bulk manifest scanned barcodes."""
    try:
        barcode_raw = (request.args.get('barcode') or '').strip()
        if not barcode_raw:
            return jsonify({'success': False, 'error': 'Missing barcode'}), 400

        normalized_barcode = ss_normalization._normalize_upc_preserve_suffix_for_match(ss_normalization._normalize_scanned_upc(barcode_raw))
        row = _bulk_manifest_lookup_rawbol(barcode_raw)
        if not row:
            fallback_price = _bulk_manifest_random_unit_price()
            return jsonify({
                'success': True,
                'found': False,
                'barcode': normalized_barcode,
                'upc': normalized_barcode,
                'title': 'item',
                'image_url': '',
                'lot_number': '',
                'quantity': 0,
                'unit_price': fallback_price,
                'price_source': 'random'
            })

        upc = ss_normalization._normalize_upc_preserve_suffix_for_match(row.get('upc') or normalized_barcode)
        title = str(row.get('item_description') or '').strip() or 'item'
        image_url = str(row.get('image_url') or '').strip()
        lot_number = ss_normalization._normalize_lot_number(row.get('lot_number'))
        quantity = max(0, ss_normalization._coerce_int(row.get('quantity'), 0))
        lot_avg_price = _bulk_manifest_money(row.get('lot_avg_price'), default=0.0)
        unit_price = _bulk_manifest_resolve_unit_price(
            row.get('unit_price'),
            lot_avg_price=lot_avg_price,
            default=None
        )

        return jsonify({
            'success': True,
            'found': True,
            'barcode': upc,
            'upc': upc,
            'title': title,
            'image_url': image_url,
            'lot_number': lot_number,
            'quantity': quantity,
            'unit_price': unit_price,
            'price_source': ('exact' if _bulk_manifest_money(row.get('unit_price'), default=0.0) > 0 else ('lot_average' if lot_avg_price > 0 else 'random'))
        })
    except Exception as e:
        return jsonify({'success': False, 'error': ss_errors._safe_error(e)}), 500


def api_bulk_manifest_search():
    """Search raw_bol_items by keyword/UPC for manual bulk-manifest row selection."""
    try:
        query = str(request.args.get('q') or request.args.get('query') or '').strip()
        limit = max(1, min(60, ss_normalization._coerce_int(request.args.get('limit'), 20)))
        page = max(1, ss_normalization._coerce_int(request.args.get('page'), 1))
        if len(query) < 2:
            return jsonify({
                'success': True,
                'query': query,
                'page': 1,
                'limit': limit,
                'total': 0,
                'total_pages': 0,
                'results': []
            })

        conn = None
        lot_avg_by_lot = {}
        try:
            conn = sqlite3.connect('rawbol.db')
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()

            price_col = _bulk_manifest_rawbol_price_column(cur)
            if price_col:
                safe_price_col = '"' + price_col.replace('"', '""') + '"'
                price_expr = f'{safe_price_col} AS unit_price'
            else:
                price_expr = '0 AS unit_price'

            like_query = f'%{query}%'
            starts_query = f'{query}%'
            cur.execute('''
                SELECT COUNT(*) AS total_rows
                FROM raw_bol_items
                WHERE item_description LIKE ? COLLATE NOCASE
                   OR upc LIKE ? COLLATE NOCASE
            ''', (like_query, like_query))
            count_row = cur.fetchone()
            total_rows = max(0, ss_normalization._coerce_int(count_row['total_rows'] if count_row else 0, 0))
            total_pages = ((total_rows + limit - 1) // limit) if total_rows else 0
            if total_pages and page > total_pages:
                page = total_pages
            offset = ((page - 1) * limit) if total_pages else 0

            cur.execute('''
                SELECT upc, item_description, image_url, lot_number, quantity, import_date, ''' + price_expr + '''
                FROM raw_bol_items
                WHERE item_description LIKE ? COLLATE NOCASE
                   OR upc LIKE ? COLLATE NOCASE
                ORDER BY
                    CASE
                        WHEN item_description LIKE ? COLLATE NOCASE THEN 0
                        WHEN upc LIKE ? COLLATE NOCASE THEN 1
                        ELSE 2
                    END,
                    COALESCE(import_date, '') DESC,
                    rowid DESC
                LIMIT ?
                OFFSET ?
            ''', (like_query, like_query, starts_query, starts_query, limit, offset))
            raw_rows = [dict(r) for r in cur.fetchall()]

            if price_col and raw_rows:
                numeric_expr = _bulk_manifest_price_numeric_expr(price_col)
                lot_values = []
                seen_lots = set()
                for raw_row in raw_rows:
                    lot_n = ss_normalization._normalize_lot_number(raw_row.get('lot_number'))
                    if not lot_n:
                        continue
                    key = lot_n.lower()
                    if key in seen_lots:
                        continue
                    seen_lots.add(key)
                    lot_values.append(lot_n)

                if lot_values:
                    placeholders = ','.join('?' for _ in lot_values)
                    cur.execute(f'''
                        SELECT lot_number, AVG({numeric_expr}) AS lot_avg
                        FROM raw_bol_items
                        WHERE lot_number IN ({placeholders})
                          AND {numeric_expr} > 0
                        GROUP BY lot_number
                    ''', tuple(lot_values))
                    for lot_row in cur.fetchall():
                        lot_n = ss_normalization._normalize_lot_number(
                            lot_row['lot_number'] if isinstance(lot_row, sqlite3.Row) else lot_row[0]
                        )
                        if not lot_n:
                            continue
                        lot_avg = _bulk_manifest_money(
                            lot_row['lot_avg'] if isinstance(lot_row, sqlite3.Row) else lot_row[1],
                            default=0.0
                        )
                        if lot_avg > 0:
                            lot_avg_by_lot[lot_n.lower()] = lot_avg
        finally:
            if conn is not None:
                conn.close()

        results = []
        for row in raw_rows:
            barcode = ss_normalization._normalize_upc_preserve_suffix_for_match(ss_normalization._normalize_scanned_upc(row.get('upc')))
            barcode = str(barcode or '').strip()
            title = str(row.get('item_description') or '').strip() or 'item'
            image_url = str(row.get('image_url') or '').strip()
            lot_number = ss_normalization._normalize_lot_number(row.get('lot_number'))
            quantity = max(0, ss_normalization._coerce_int(row.get('quantity'), 0))
            lot_avg_price = lot_avg_by_lot.get(lot_number.lower(), 0.0) if lot_number else 0.0
            unit_price = _bulk_manifest_resolve_unit_price(
                row.get('unit_price'),
                lot_avg_price=lot_avg_price,
                default=None
            )

            results.append({
                'barcode': barcode,
                'upc': barcode,
                'title': title,
                'image_url': image_url,
                'thumbnail_url': image_url,
                'lot_number': lot_number,
                'quantity': quantity,
                'unit_price': unit_price,
                'price_source': ('exact' if _bulk_manifest_money(row.get('unit_price'), default=0.0) > 0 else ('lot_average' if lot_avg_price > 0 else 'random'))
            })

        return jsonify({
            'success': True,
            'query': query,
            'page': page,
            'limit': limit,
            'total': total_rows,
            'total_pages': total_pages,
            'results': results
        })
    except Exception as e:
        return jsonify({'success': False, 'error': ss_errors._safe_error(e, 'bulk_manifest_search')}), 500


def _marketplace_sale_store_key(value):
    raw = str(value or '').strip().lower().replace('_', '-')
    if raw in (
        'marketplace-sale-bulk',
        'marketplace sale - bulk',
        'marketplace sale bulk',
        'marketplace-bulk',
        'marketplace bulk',
        'bulk-marketplace'
    ):
        return 'marketplace-sale-bulk'
    return 'marketplace'


def _bulk_manifest_status_key(value):
    raw = str(value or '').strip().lower().replace(' ', '_').replace('-', '_')
    if raw in ('sold', 'processed_sale'):
        return 'sold'
    if raw in ('cleanup_complete', 'cleaned'):
        return 'cleanup_complete'
    if raw in ('cleanup_partial', 'partial_cleanup'):
        return 'cleanup_partial'
    return 'created'


def _bulk_manifest_parse_ids(raw_value):
    seen = set()
    out = []
    for token in str(raw_value or '').split(','):
        part = token.strip()
        if not part:
            continue
        try:
            val = int(part)
        except Exception:
            continue
        if val <= 0 or val in seen:
            continue
        seen.add(val)
        out.append(val)
    return out


def _bulk_manifest_money(value, default=0.0):
    try:
        num = float(value)
    except Exception:
        num = float(default or 0.0)
    return round(max(0.0, num), 2)


def _bulk_manifest_next_title(cur):
    """Return next auto-generated manifest title (Bulk Manifest N)."""
    cur.execute('''
        SELECT manifest_title
        FROM bulk_manifests
        WHERE manifest_title IS NOT NULL
          AND TRIM(manifest_title) != ''
    ''')
    rows = cur.fetchall()
    max_num = 0
    prefix = 'bulk manifest'
    for row in rows:
        raw_title = row[0] if not isinstance(row, sqlite3.Row) else row['manifest_title']
        title = str(raw_title or '').strip()
        lower = title.lower()
        if not lower.startswith(prefix):
            continue
        suffix = title[len(prefix):].strip()
        if not suffix:
            continue
        try:
            num = int(suffix)
        except Exception:
            continue
        if num > max_num:
            max_num = num
    return f'Bulk Manifest {max_num + 1}'


def _ensure_bulk_manifest_tables(cur):
    cur.execute('''
        CREATE TABLE IF NOT EXISTS bulk_manifests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            manifest_title TEXT NOT NULL,
            buyer TEXT,
            manifest_date TEXT,
            total_items INTEGER DEFAULT 0,
            total_units INTEGER DEFAULT 0,
            total_original_retail REAL DEFAULT 0,
            status TEXT DEFAULT 'created',
            sold_at TEXT,
            sold_session_id TEXT,
            sold_total REAL,
            sold_rows INTEGER DEFAULT 0,
            cleanup_at TEXT,
            cleanup_note TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    cur.execute('''
        CREATE TABLE IF NOT EXISTS bulk_manifest_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            manifest_id INTEGER NOT NULL,
            line_number INTEGER NOT NULL,
            barcode TEXT NOT NULL,
            title TEXT,
            quantity INTEGER DEFAULT 1,
            unit_price REAL DEFAULT 0,
            line_total REAL DEFAULT 0,
            image_url TEXT,
            lot_number TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (manifest_id) REFERENCES bulk_manifests(id) ON DELETE CASCADE
        )
    ''')
    cur.execute('''
        CREATE TABLE IF NOT EXISTS bulk_manifest_images (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            manifest_id INTEGER NOT NULL,
            image_path TEXT NOT NULL,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (manifest_id) REFERENCES bulk_manifests(id) ON DELETE CASCADE
        )
    ''')
    cur.execute('CREATE INDEX IF NOT EXISTS idx_bulk_manifests_status_created ON bulk_manifests(status, created_at DESC)')
    cur.execute('CREATE INDEX IF NOT EXISTS idx_bulk_manifest_items_manifest_id ON bulk_manifest_items(manifest_id)')
    cur.execute('CREATE INDEX IF NOT EXISTS idx_bulk_manifest_items_barcode ON bulk_manifest_items(barcode)')
    cur.execute('CREATE INDEX IF NOT EXISTS idx_bulk_manifest_images_manifest_id ON bulk_manifest_images(manifest_id, created_at DESC)')

    try:
        cur.execute('PRAGMA table_info(bulk_manifests)')
        existing = {str(r[1]).lower() for r in cur.fetchall()}
        migrations = {
            'status': "ALTER TABLE bulk_manifests ADD COLUMN status TEXT DEFAULT 'created'",
            'sold_at': 'ALTER TABLE bulk_manifests ADD COLUMN sold_at TEXT',
            'sold_session_id': 'ALTER TABLE bulk_manifests ADD COLUMN sold_session_id TEXT',
            'sold_total': 'ALTER TABLE bulk_manifests ADD COLUMN sold_total REAL',
            'sold_rows': 'ALTER TABLE bulk_manifests ADD COLUMN sold_rows INTEGER DEFAULT 0',
            'cleanup_at': 'ALTER TABLE bulk_manifests ADD COLUMN cleanup_at TEXT',
            'cleanup_note': 'ALTER TABLE bulk_manifests ADD COLUMN cleanup_note TEXT',
            'updated_at': 'ALTER TABLE bulk_manifests ADD COLUMN updated_at TEXT',
            'google_sheet_id': 'ALTER TABLE bulk_manifests ADD COLUMN google_sheet_id TEXT',
            'google_sheet_url': 'ALTER TABLE bulk_manifests ADD COLUMN google_sheet_url TEXT',
            'google_sheet_exported_at': 'ALTER TABLE bulk_manifests ADD COLUMN google_sheet_exported_at TEXT',
            'google_sheet_shared_with': 'ALTER TABLE bulk_manifests ADD COLUMN google_sheet_shared_with TEXT'
        }
        for col_name, ddl in migrations.items():
            if col_name not in existing:
                cur.execute(ddl)
    except Exception:
        pass


def _bulk_manifest_prepare_items(raw_items, *, force_lookup_refresh=False):
    items = []
    total_units = 0
    total_original_retail = 0.0
    rawbol_conn = None
    rawbol_cur = None
    rawbol_price_col = None
    rawbol_lot_avg_cache = {}
    try:
        rawbol_conn = sqlite3.connect('rawbol.db')
        rawbol_conn.row_factory = sqlite3.Row
        rawbol_cur = rawbol_conn.cursor()
        rawbol_price_col = _bulk_manifest_rawbol_price_column(rawbol_cur)
    except Exception:
        rawbol_cur = None
        rawbol_price_col = None

    try:
        for idx, raw_item in enumerate(raw_items or [], start=1):
            row = raw_item if isinstance(raw_item, dict) else {}
            barcode = ss_normalization._normalize_upc_preserve_suffix_for_match(
                ss_normalization._normalize_scanned_upc(row.get('barcode') or row.get('upc'))
            )
            barcode = str(barcode or '').strip()
            title = str(row.get('name') or row.get('title') or '').strip()
            quantity = max(1, ss_normalization._coerce_int(row.get('qty') if row.get('qty') is not None else row.get('quantity'), 1))
            unit_price = _bulk_manifest_money(
                row.get('unit_price') if row.get('unit_price') is not None else row.get('price'),
                default=0.0
            )
            image_url = str(row.get('image_url') or row.get('image') or '').strip()
            lot_number = ss_normalization._normalize_lot_number(row.get('lot_number'))
            lookup_row = None
            existing_unit_price = unit_price

            if not title:
                title = 'item'
            if barcode and (force_lookup_refresh or unit_price <= 0 or not lot_number or not image_url or title == 'item'):
                try:
                    lookup_row = _bulk_manifest_lookup_rawbol(barcode)
                except Exception:
                    lookup_row = None
                if lookup_row:
                    looked_up_title = str(lookup_row.get('item_description') or '').strip()
                    looked_up_image = str(lookup_row.get('image_url') or '').strip()
                    looked_up_lot = ss_normalization._normalize_lot_number(lookup_row.get('lot_number'))
                    if force_lookup_refresh:
                        if looked_up_title and title.lower() in ('', 'item', barcode.lower()):
                            title = looked_up_title
                        if looked_up_image:
                            image_url = looked_up_image
                        if looked_up_lot:
                            lot_number = looked_up_lot
                    else:
                        if title == 'item':
                            title = looked_up_title or title
                        if not image_url:
                            image_url = looked_up_image
                        if not lot_number:
                            lot_number = looked_up_lot
            if barcode and force_lookup_refresh:
                lot_avg_price = 0.0
                lookup_unit_price = 0.0
                if lookup_row:
                    lookup_unit_price = _bulk_manifest_money(lookup_row.get('unit_price'), default=0.0)
                    lot_avg_price = _bulk_manifest_money(lookup_row.get('lot_avg_price'), default=0.0)
                if lot_avg_price <= 0 and lot_number:
                    lot_avg_price = _bulk_manifest_lot_average_price(
                        rawbol_cur,
                        lot_number,
                        price_col=rawbol_price_col,
                        cache=rawbol_lot_avg_cache
                    )
                unit_price = _bulk_manifest_resolve_unit_price(
                    lookup_unit_price,
                    lot_avg_price=lot_avg_price,
                    default=(existing_unit_price if existing_unit_price > 0 else None)
                )
            elif unit_price <= 0:
                lot_avg_price = 0.0
                lookup_unit_price = 0.0
                if lookup_row:
                    lookup_unit_price = _bulk_manifest_money(lookup_row.get('unit_price'), default=0.0)
                    lot_avg_price = _bulk_manifest_money(lookup_row.get('lot_avg_price'), default=0.0)
                if barcode and lot_avg_price <= 0:
                    lot_avg_price = _bulk_manifest_lot_average_price(
                        rawbol_cur,
                        lot_number,
                        price_col=rawbol_price_col,
                        cache=rawbol_lot_avg_cache
                    )
                unit_price = _bulk_manifest_resolve_unit_price(
                    lookup_unit_price,
                    lot_avg_price=lot_avg_price,
                    default=None
                )

            line_total = round(quantity * unit_price, 2)
            items.append({
                'line_number': idx,
                'barcode': barcode,
                'title': title,
                'quantity': quantity,
                'unit_price': unit_price,
                'line_total': line_total,
                'image_url': image_url,
                'lot_number': lot_number
            })
            total_units += quantity
            total_original_retail += line_total
    finally:
        if rawbol_conn is not None:
            rawbol_conn.close()

    return items, total_units, round(total_original_retail, 2)


def _bulk_manifest_replace_items(cur, manifest_id, items, total_units, total_original_retail, now_iso):
    manifest_key = max(0, ss_normalization._coerce_int(manifest_id, 0))
    _ensure_bulk_manifest_tables(cur)
    cur.execute('DELETE FROM bulk_manifest_items WHERE manifest_id = ?', (manifest_key,))
    for item in items:
        cur.execute('''
            INSERT INTO bulk_manifest_items (
                manifest_id, line_number, barcode, title, quantity,
                unit_price, line_total, image_url, lot_number, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            manifest_key,
            item['line_number'],
            item['barcode'],
            item['title'],
            item['quantity'],
            item['unit_price'],
            item['line_total'],
            item['image_url'],
            item['lot_number'],
            now_iso
        ))

    cur.execute('''
        UPDATE bulk_manifests
        SET total_items = ?,
            total_units = ?,
            total_original_retail = ?,
            updated_at = ?
        WHERE id = ?
    ''', (
        len(items),
        total_units,
        round(total_original_retail, 2),
        now_iso,
        manifest_key
    ))


def _bulk_manifest_photo_label_text(manifest_id, manifest_title=''):
    title = str(manifest_title or '').strip() or f'Bulk Manifest {max(0, ss_normalization._coerce_int(manifest_id, 0))}'
    return title[:120]


def _bulk_manifest_photo_url(image_path):
    rel = str(image_path or '').replace('\\', '/').strip().lstrip('/')
    return f"/static/{rel}" if rel else ''


def _bulk_manifest_photo_payload(row):
    data = dict(row or {})
    image_path = str(data.get('image_path') or '').strip()
    return {
        'id': max(0, ss_normalization._coerce_int(data.get('id'), 0)),
        'image_path': image_path,
        'image_url': _bulk_manifest_photo_url(image_path),
        'created_at': str(data.get('created_at') or '').strip()
    }


def _bulk_manifest_load_photos(cur, manifest_id):
    _ensure_bulk_manifest_tables(cur)
    cur.execute('''
        SELECT id, image_path, created_at
        FROM bulk_manifest_images
        WHERE manifest_id = ?
        ORDER BY created_at DESC, id DESC
    ''', (max(0, ss_normalization._coerce_int(manifest_id, 0)),))
    return [_bulk_manifest_photo_payload(r) for r in cur.fetchall()]


def _bulk_manifest_photo_counts(cur, manifest_ids):
    _ensure_bulk_manifest_tables(cur)
    cleaned_ids = []
    seen = set()
    for value in (manifest_ids or []):
        manifest_id = max(0, ss_normalization._coerce_int(value, 0))
        if manifest_id <= 0 or manifest_id in seen:
            continue
        seen.add(manifest_id)
        cleaned_ids.append(manifest_id)
    if not cleaned_ids:
        return {}

    placeholders = ','.join('?' for _ in cleaned_ids)
    cur.execute(f'''
        SELECT manifest_id, COUNT(*) AS photo_count
        FROM bulk_manifest_images
        WHERE manifest_id IN ({placeholders})
        GROUP BY manifest_id
    ''', tuple(cleaned_ids))
    counts = {manifest_id: 0 for manifest_id in cleaned_ids}
    for row in cur.fetchall():
        row_data = dict(row or {})
        manifest_id = max(0, ss_normalization._coerce_int(row_data.get('manifest_id'), 0))
        if manifest_id <= 0:
            continue
        counts[manifest_id] = max(0, ss_normalization._coerce_int(row_data.get('photo_count'), 0))
    return counts


def _bulk_manifest_photo_abs_path(image_path):
    rel = str(image_path or '').replace('\\', '/').strip().lstrip('/\\')
    if not rel:
        return ''
    static_root = os.path.abspath(os.path.join(ss_runtime.app.root_path, 'static'))
    abs_path = os.path.abspath(os.path.join(static_root, rel))
    try:
        if os.path.commonpath([static_root, abs_path]) != static_root:
            return ''
    except Exception:
        return ''
    return abs_path


def _bulk_manifest_delete_photo_files(image_paths):
    seen = set()
    deleted_count = 0
    for raw_path in (image_paths or []):
        image_path = str(raw_path or '').strip()
        if not image_path:
            continue
        key = image_path.lower()
        if key in seen:
            continue
        seen.add(key)
        abs_path = _bulk_manifest_photo_abs_path(image_path)
        if not abs_path:
            continue
        try:
            if os.path.exists(abs_path):
                os.remove(abs_path)
                deleted_count += 1
        except FileNotFoundError:
            continue
        except Exception as exc:
            ss_config.logger.warning('bulk_manifest_delete_photo_files failed for %s: %s', abs_path, exc, exc_info=True)
    return deleted_count


def _bulk_manifest_delete_manifest(manifest_id):
    manifest_key = max(0, ss_normalization._coerce_int(manifest_id, 0))
    if manifest_key <= 0:
        raise LookupError('Manifest not found')

    with ss_database.db_connection('marketplace.db') as conn:
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        _ensure_bulk_manifest_tables(cur)
        cur.execute('''
            SELECT id, manifest_title, google_sheet_id, google_sheet_url
            FROM bulk_manifests
            WHERE id = ?
        ''', (manifest_key,))
        manifest_row = cur.fetchone()
        if not manifest_row:
            raise LookupError('Manifest not found')

        cur.execute('SELECT image_path FROM bulk_manifest_images WHERE manifest_id = ?', (manifest_key,))
        photo_paths = [str((row or {}).get('image_path') or '').strip() for row in cur.fetchall()]
        manifest_title = str(manifest_row['manifest_title'] or '').strip()

    google_sheet_deleted = False
    google_sheet_warning = ''
    try:
        google_result = ss_manifest_google._bulk_manifest_google_delete_manifest(manifest_key)
        google_sheet_deleted = bool(google_result.get('deleted'))
    except Exception as exc:
        google_sheet_warning = str(exc or '').strip()
        ss_config.logger.warning('bulk_manifest_delete_manifest google cleanup failed for manifest %s: %s', manifest_key, exc, exc_info=True)

    with ss_database.db_connection('marketplace.db') as conn:
        cur = conn.cursor()
        _ensure_bulk_manifest_tables(cur)
        cur.execute('DELETE FROM bulk_manifest_images WHERE manifest_id = ?', (manifest_key,))
        cur.execute('DELETE FROM bulk_manifest_items WHERE manifest_id = ?', (manifest_key,))
        cur.execute('DELETE FROM bulk_manifests WHERE id = ?', (manifest_key,))
        if cur.rowcount <= 0:
            raise LookupError('Manifest not found')

    ss_manifest_google._bulk_manifest_google_job_clear(manifest_key)
    deleted_photos = _bulk_manifest_delete_photo_files(photo_paths)
    return {
        'success': True,
        'manifest_id': manifest_key,
        'manifest_title': manifest_title,
        'deleted': True,
        'deleted_photo_files': deleted_photos,
        'google_sheet_deleted': google_sheet_deleted,
        'google_sheet_warning': google_sheet_warning
    }


def _bulk_manifest_save_labeled_photo(file_storage, label_text, abs_path):
    if file_storage is None:
        raise ValueError('Missing file upload')

    stream = getattr(file_storage, 'stream', file_storage)
    try:
        if hasattr(stream, 'seek'):
            stream.seek(0)
        with Image.open(stream) as raw_img:
            img = ImageOps.exif_transpose(raw_img)
            if img.mode not in ('RGB', 'RGBA'):
                img = img.convert('RGBA')
            else:
                img = img.copy()

            if img.size[0] > 2400 or img.size[1] > 2400:
                img.thumbnail((2400, 2400), Image.Resampling.LANCZOS)

            base = img.convert('RGBA')
            overlay = Image.new('RGBA', base.size, (0, 0, 0, 0))
            draw = ImageDraw.Draw(overlay)

            font_size = max(24, min(72, base.size[0] // 14))
            try:
                font = ImageFont.truetype('DejaVuSans-Bold.ttf', font_size)
            except Exception:
                font = ImageFont.load_default()

            text = str(label_text or '').strip() or 'Bulk Manifest'
            bbox = draw.textbbox((0, 0), text, font=font)
            text_w = max(1, bbox[2] - bbox[0])
            text_h = max(1, bbox[3] - bbox[1])
            pad_x = max(16, font_size // 2)
            pad_y = max(10, font_size // 3)
            banner_h = text_h + (pad_y * 2)
            bottom_gap = max(18, base.size[1] // 36)
            rect_x0 = max(12, (base.size[0] - (text_w + (pad_x * 2))) // 2)
            rect_y0 = max(12, base.size[1] - banner_h - bottom_gap)
            rect_x1 = min(base.size[0] - 12, rect_x0 + text_w + (pad_x * 2))
            rect_y1 = min(base.size[1] - 12, rect_y0 + banner_h)

            draw.rounded_rectangle(
                (rect_x0, rect_y0, rect_x1, rect_y1),
                radius=max(10, font_size // 3),
                fill=(10, 10, 10, 168)
            )
            text_x = rect_x0 + pad_x
            text_y = rect_y0 + pad_y - bbox[1]
            draw.text((text_x, text_y), text, font=font, fill=(255, 255, 255, 236))

            final_img = Image.alpha_composite(base, overlay).convert('RGB')
            final_img.save(abs_path, format='JPEG', quality=88, optimize=True)
    finally:
        try:
            if hasattr(stream, 'seek'):
                stream.seek(0)
        except Exception:
            pass


def _bulk_manifest_normalize_image_url(raw_value, origin_base=''):
    url = str(raw_value or '').strip()
    if not url:
        return ''
    low = url.lower()
    if low in ('nan', 'none', 'null', 'undefined'):
        return ''
    base = str(origin_base or '').strip().rstrip('/')
    if url.startswith('/') and base:
        url = base + url
    if url.startswith('//'):
        url = 'https:' + url
    if url.startswith('http://'):
        url = 'https://' + url[len('http://'):]
    return url


def _bulk_manifest_is_macy_image_host(hostname):
    host = str(hostname or '').strip().lower()
    return ('macys.com' in host) or ('bloomingdales.com' in host)


def _listagent_is_macy_image_url(image_url, origin_base=''):
    normalized = _bulk_manifest_normalize_image_url(image_url, origin_base=origin_base)
    if not normalized:
        return False
    try:
        from urllib.parse import urlparse
        parsed = urlparse(normalized)
        return _bulk_manifest_is_macy_image_host(parsed.hostname)
    except Exception:
        return False


def _listagent_prioritize_macy_thumbnail(images, origin_base=''):
    ordered = []
    seen = set()
    first_macy = None
    for raw in images or []:
        normalized = _bulk_manifest_normalize_image_url(raw, origin_base=origin_base)
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        if first_macy is None and _listagent_is_macy_image_url(normalized, origin_base=origin_base):
            first_macy = normalized
        ordered.append(normalized)
    if first_macy and ordered and ordered[0] != first_macy:
        ordered = [first_macy] + [img for img in ordered if img != first_macy]
    return ordered


def _bulk_manifest_macy_thumb_url(image_url, origin_base=''):
    normalized = _bulk_manifest_normalize_image_url(image_url, origin_base=origin_base)
    if not normalized:
        return ''
    try:
        from urllib.parse import urlparse, parse_qsl, urlencode, urlunparse
        parsed = urlparse(normalized)
        if _bulk_manifest_is_macy_image_host(parsed.hostname):
            query = dict(parse_qsl(parsed.query, keep_blank_values=True))
            query['wid'] = '180'
            query['hei'] = '220'
            query['fmt'] = 'jpeg'
            query['qlt'] = '85'
            query['op_sharpen'] = '1'
            return urlunparse(parsed._replace(query=urlencode(query)))
    except Exception:
        pass
    return normalized


def _bulk_manifest_macy_full_image_url(image_url, origin_base=''):
    normalized = _bulk_manifest_normalize_image_url(image_url, origin_base=origin_base)
    if not normalized:
        return ''
    try:
        from urllib.parse import urlparse, urlunparse
        parsed = urlparse(normalized)
        if _bulk_manifest_is_macy_image_host(parsed.hostname):
            return urlunparse(parsed._replace(query=''))
    except Exception:
        pass
    return normalized


def _bulk_manifest_google_image_formula(url, origin_base='', width=None, height=None):
    normalized = _bulk_manifest_normalize_image_url(url, origin_base=origin_base)
    if not normalized:
        return ''
    escaped = normalized.replace('"', '""')
    width = ss_normalization._coerce_int(width, 0)
    height = ss_normalization._coerce_int(height, 0)
    if width > 0 and height > 0:
        return f'=IMAGE("{escaped}",4,{height},{width})'
    return f'=IMAGE("{escaped}")'


def _bulk_manifest_google_link_formula(url, label='Open', origin_base=''):
    normalized = _bulk_manifest_normalize_image_url(url, origin_base=origin_base)
    if not normalized:
        return ''
    escaped_url = normalized.replace('"', '""')
    escaped_label = str(label or 'Open').replace('"', '""')
    return f'=HYPERLINK("{escaped_url}","{escaped_label}")'


def _bulk_manifest_load_detail(cur, manifest_id):
    _ensure_bulk_manifest_tables(cur)

    cur.execute('SELECT * FROM bulk_manifests WHERE id = ?', (manifest_id,))
    manifest_row = cur.fetchone()
    if not manifest_row:
        raise LookupError('Manifest not found')

    cur.execute('''
        SELECT
            id,
            line_number,
            barcode,
            title,
            quantity,
            unit_price,
            line_total,
            image_url,
            lot_number,
            created_at
        FROM bulk_manifest_items
        WHERE manifest_id = ?
        ORDER BY line_number ASC, id ASC
    ''', (manifest_id,))
    item_rows = [dict(r) for r in cur.fetchall()]

    manifest = dict(manifest_row)
    out_manifest = {
        'id': manifest.get('id'),
        'manifest_title': str(manifest.get('manifest_title') or '').strip(),
        'buyer': str(manifest.get('buyer') or '').strip(),
        'manifest_date': str(manifest.get('manifest_date') or '').strip(),
        'status': _bulk_manifest_status_key(manifest.get('status')),
        'total_items': max(0, ss_normalization._coerce_int(manifest.get('total_items'), 0)),
        'total_units': max(0, ss_normalization._coerce_int(manifest.get('total_units'), 0)),
        'total_original_retail': _bulk_manifest_money(manifest.get('total_original_retail'), default=0.0),
        'sold_total': _bulk_manifest_money(manifest.get('sold_total'), default=0.0),
        'sold_rows': max(0, ss_normalization._coerce_int(manifest.get('sold_rows'), 0)),
        'sold_at': str(manifest.get('sold_at') or '').strip(),
        'sold_session_id': str(manifest.get('sold_session_id') or '').strip(),
        'cleanup_at': str(manifest.get('cleanup_at') or '').strip(),
        'cleanup_note': str(manifest.get('cleanup_note') or '').strip(),
        'created_at': str(manifest.get('created_at') or '').strip(),
        'updated_at': str(manifest.get('updated_at') or '').strip(),
        'google_sheet_id': str(manifest.get('google_sheet_id') or '').strip(),
        'google_sheet_url': str(manifest.get('google_sheet_url') or '').strip(),
        'google_sheet_exported_at': str(manifest.get('google_sheet_exported_at') or '').strip(),
        'google_sheet_shared_with': str(manifest.get('google_sheet_shared_with') or '').strip()
    }
    sheet_state = ss_manifest_google._bulk_manifest_google_state(out_manifest.get('id'), row=out_manifest)
    out_manifest.update({
        'google_sheet_id': sheet_state['google_sheet_id'],
        'google_sheet_url': sheet_state['google_sheet_url'],
        'google_sheet_exported_at': sheet_state['google_sheet_exported_at'],
        'google_sheet_shared_with': sheet_state['google_sheet_shared_with'],
        'google_sheet_status': sheet_state['google_sheet_status'],
        'google_sheet_available': sheet_state['google_sheet_available'],
        'google_sheet_button_label': sheet_state['google_sheet_button_label'],
        'google_sheet_button_tone': sheet_state['google_sheet_button_tone'],
        'google_sheet_error': sheet_state['google_sheet_error']
    })

    items = []
    for row in item_rows:
        barcode = str(row.get('barcode') or '').strip()
        qty = max(1, ss_normalization._coerce_int(row.get('quantity'), 1))
        unit_price = _bulk_manifest_money(row.get('unit_price'), default=0.0)
        items.append({
            'id': row.get('id'),
            'line_number': max(1, ss_normalization._coerce_int(row.get('line_number'), len(items) + 1)),
            'barcode': barcode,
            'barcode_display': ss_normalization._format_upc_display(barcode),
            'title': str(row.get('title') or '').strip() or barcode,
            'quantity': qty,
            'unit_price': unit_price,
            'line_total': _bulk_manifest_money(row.get('line_total'), default=(qty * unit_price)),
            'image_url': str(row.get('image_url') or '').strip(),
            'lot_number': ss_normalization._normalize_lot_number(row.get('lot_number')),
            'created_at': str(row.get('created_at') or '').strip()
        })

    return out_manifest, items


def api_bulk_manifest_create():
    """Persist a bulk manifest and its line items into marketplace.db."""
    try:
        data = request.get_json() or {}
        buyer = ''
        manifest_date = datetime.date.today().isoformat()
        raw_items = data.get('items') or []

        if not isinstance(raw_items, list) or not raw_items:
            return jsonify({'success': False, 'error': 'Manifest items are required'}), 400

        items, total_units, total_original_retail = _bulk_manifest_prepare_items(raw_items)

        if not items:
            return jsonify({'success': False, 'error': 'No valid manifest items to save'}), 400

        now_iso = datetime.datetime.now().isoformat()
        with ss_database.db_connection('marketplace.db') as conn:
            cur = conn.cursor()
            _ensure_bulk_manifest_tables(cur)
            manifest_title = _bulk_manifest_next_title(cur)
            cur.execute('''
                INSERT INTO bulk_manifests (
                    manifest_title, buyer, manifest_date,
                    total_items, total_units, total_original_retail,
                    status, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, 'created', ?, ?)
            ''', (
                manifest_title,
                buyer,
                manifest_date,
                len(items),
                total_units,
                round(total_original_retail, 2),
                now_iso,
                now_iso
            ))
            manifest_id = cur.lastrowid

            for item in items:
                cur.execute('''
                    INSERT INTO bulk_manifest_items (
                        manifest_id, line_number, barcode, title, quantity,
                        unit_price, line_total, image_url, lot_number, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''', (
                    manifest_id,
                    item['line_number'],
                    item['barcode'],
                    item['title'],
                    item['quantity'],
                    item['unit_price'],
                    item['line_total'],
                    item['image_url'],
                    item['lot_number'],
                    now_iso
                ))

        try:
            ss_manifest_google._bulk_manifest_queue_google_export(
                manifest_id,
                origin_base=str(request.host_url or '').strip().rstrip('/'),
                manifest_title=manifest_title,
                force_refresh=True
            )
        except Exception as queue_exc:
            ss_config.logger.warning('bulk_manifest_create: failed to queue Google Sheet export for manifest %s: %s', manifest_id, queue_exc, exc_info=True)
        sheet_view = ss_manifest_google._bulk_manifest_google_state(manifest_id)

        return jsonify({
            'success': True,
            'manifest': {
                'id': manifest_id,
                'manifest_title': manifest_title,
                'buyer': buyer,
                'manifest_date': manifest_date,
                'status': 'created',
                'total_items': len(items),
                'total_units': total_units,
                'total_original_retail': round(total_original_retail, 2),
                'created_at': now_iso,
                'updated_at': now_iso,
                'google_sheet_id': sheet_view['google_sheet_id'],
                'google_sheet_status': sheet_view['google_sheet_status'],
                'google_sheet_url': sheet_view['google_sheet_url'],
                'google_sheet_exported_at': sheet_view['google_sheet_exported_at'],
                'google_sheet_shared_with': sheet_view['google_sheet_shared_with'],
                'google_sheet_button_label': sheet_view['google_sheet_button_label'],
                'google_sheet_button_tone': sheet_view['google_sheet_button_tone'],
                'google_sheet_available': sheet_view['google_sheet_available'],
                'google_sheet_error': sheet_view['google_sheet_error']
            }
        })
    except Exception as e:
        return jsonify({'success': False, 'error': ss_errors._safe_error(e, 'bulk_manifest_create')}), 500


def api_bulk_manifest_update(manifest_id):
    """Update a saved bulk manifest in place."""
    try:
        data = request.get_json() or {}
        raw_items = data.get('items') or []

        if not isinstance(raw_items, list) or not raw_items:
            return jsonify({'success': False, 'error': 'Manifest items are required'}), 400

        items, total_units, total_original_retail = _bulk_manifest_prepare_items(raw_items)
        if not items:
            return jsonify({'success': False, 'error': 'No valid manifest items to save'}), 400

        now_iso = datetime.datetime.now().isoformat()
        with ss_database.db_connection('marketplace.db') as conn:
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()
            _ensure_bulk_manifest_tables(cur)

            cur.execute('SELECT * FROM bulk_manifests WHERE id = ?', (manifest_id,))
            manifest_row = cur.fetchone()
            if not manifest_row:
                return jsonify({'success': False, 'error': 'Manifest not found'}), 404

            manifest = dict(manifest_row)
            _bulk_manifest_replace_items(
                cur,
                manifest_id,
                items,
                total_units,
                total_original_retail,
                now_iso
            )

        try:
            ss_manifest_google._bulk_manifest_queue_google_export(
                manifest_id,
                origin_base=str(request.host_url or '').strip().rstrip('/'),
                manifest_title=str(manifest.get('manifest_title') or '').strip(),
                force_refresh=True
            )
        except Exception as queue_exc:
            ss_config.logger.warning('bulk_manifest_update: failed to queue Google Sheet export for manifest %s: %s', manifest_id, queue_exc, exc_info=True)
        sheet_view = ss_manifest_google._bulk_manifest_google_state(manifest_id, row=manifest)

        return jsonify({
            'success': True,
            'manifest': {
                'id': manifest_id,
                'manifest_title': str(manifest.get('manifest_title') or '').strip(),
                'buyer': str(manifest.get('buyer') or '').strip(),
                'manifest_date': str(manifest.get('manifest_date') or '').strip(),
                'status': _bulk_manifest_status_key(manifest.get('status')),
                'total_items': len(items),
                'total_units': total_units,
                'total_original_retail': round(total_original_retail, 2),
                'created_at': str(manifest.get('created_at') or '').strip(),
                'updated_at': now_iso,
                'google_sheet_id': sheet_view['google_sheet_id'],
                'google_sheet_status': sheet_view['google_sheet_status'],
                'google_sheet_url': sheet_view['google_sheet_url'],
                'google_sheet_exported_at': sheet_view['google_sheet_exported_at'],
                'google_sheet_shared_with': sheet_view['google_sheet_shared_with'],
                'google_sheet_button_label': sheet_view['google_sheet_button_label'],
                'google_sheet_button_tone': sheet_view['google_sheet_button_tone'],
                'google_sheet_available': sheet_view['google_sheet_available'],
                'google_sheet_error': sheet_view['google_sheet_error']
            }
        })
    except Exception as e:
        return jsonify({'success': False, 'error': ss_errors._safe_error(e, 'bulk_manifest_update')}), 500


def api_bulk_manifest_refresh():
    """Re-resolve bulk manifest barcode rows against rawbol.db and optionally persist."""
    try:
        data = request.get_json() or {}
        raw_items = data.get('items') or []
        manifest_id = max(0, ss_normalization._coerce_int(data.get('manifest_id'), 0))

        if not isinstance(raw_items, list) or not raw_items:
            return jsonify({'success': False, 'error': 'Manifest items are required'}), 400

        items, total_units, total_original_retail = _bulk_manifest_prepare_items(
            raw_items,
            force_lookup_refresh=True
        )
        if not items:
            return jsonify({'success': False, 'error': 'No valid manifest items to refresh'}), 400

        payload = {
            'success': True,
            'persisted': False,
            'manifest_id': manifest_id,
            'items': items,
            'total_items': len(items),
            'total_units': total_units,
            'total_original_retail': round(total_original_retail, 2)
        }

        if manifest_id <= 0:
            return jsonify(payload)

        now_iso = datetime.datetime.now().isoformat()
        with ss_database.db_connection('marketplace.db') as conn:
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()
            _ensure_bulk_manifest_tables(cur)
            cur.execute('SELECT * FROM bulk_manifests WHERE id = ?', (manifest_id,))
            manifest_row = cur.fetchone()
            if not manifest_row:
                return jsonify({'success': False, 'error': 'Manifest not found'}), 404
            manifest = dict(manifest_row)
            _bulk_manifest_replace_items(
                cur,
                manifest_id,
                items,
                total_units,
                total_original_retail,
                now_iso
            )

        try:
            ss_manifest_google._bulk_manifest_queue_google_export(
                manifest_id,
                origin_base=str(request.host_url or '').strip().rstrip('/'),
                manifest_title=str(manifest.get('manifest_title') or '').strip(),
                force_refresh=True
            )
        except Exception as queue_exc:
            ss_config.logger.warning('bulk_manifest_refresh: failed to queue Google Sheet export for manifest %s: %s', manifest_id, queue_exc, exc_info=True)
        sheet_view = ss_manifest_google._bulk_manifest_google_state(manifest_id, row=manifest)

        payload.update({
            'persisted': True,
            'manifest': {
                'id': manifest_id,
                'manifest_title': str(manifest.get('manifest_title') or '').strip(),
                'buyer': str(manifest.get('buyer') or '').strip(),
                'manifest_date': str(manifest.get('manifest_date') or '').strip(),
                'status': _bulk_manifest_status_key(manifest.get('status')),
                'total_items': len(items),
                'total_units': total_units,
                'total_original_retail': round(total_original_retail, 2),
                'created_at': str(manifest.get('created_at') or '').strip(),
                'updated_at': now_iso,
                'google_sheet_id': sheet_view['google_sheet_id'],
                'google_sheet_status': sheet_view['google_sheet_status'],
                'google_sheet_url': sheet_view['google_sheet_url'],
                'google_sheet_exported_at': sheet_view['google_sheet_exported_at'],
                'google_sheet_shared_with': sheet_view['google_sheet_shared_with'],
                'google_sheet_button_label': sheet_view['google_sheet_button_label'],
                'google_sheet_button_tone': sheet_view['google_sheet_button_tone'],
                'google_sheet_available': sheet_view['google_sheet_available'],
                'google_sheet_error': sheet_view['google_sheet_error']
            }
        })
        return jsonify(payload)
    except Exception as e:
        return jsonify({'success': False, 'error': ss_errors._safe_error(e, 'bulk_manifest_refresh')}), 500


def api_bulk_manifest_history():
    """List saved bulk manifests for history and downstream integrations."""
    try:
        raw_statuses = str(request.args.get('status') or '').strip()
        limit = max(1, min(300, ss_normalization._coerce_int(request.args.get('limit'), 120)))
        status_filters = []
        if raw_statuses:
            seen = set()
            for part in raw_statuses.split(','):
                if not part.strip():
                    continue
                key = _bulk_manifest_status_key(part)
                if key in seen:
                    continue
                seen.add(key)
                status_filters.append(key)

        with ss_database.db_connection('marketplace.db') as conn:
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()
            _ensure_bulk_manifest_tables(cur)

            sql = '''
                SELECT
                    id,
                    manifest_title,
                    buyer,
                    manifest_date,
                    total_items,
                    total_units,
                    total_original_retail,
                    status,
                    sold_at,
                    sold_session_id,
                    sold_total,
                    sold_rows,
                    cleanup_at,
                    cleanup_note,
                    google_sheet_id,
                    google_sheet_url,
                    google_sheet_exported_at,
                    google_sheet_shared_with,
                    created_at,
                    updated_at
                FROM bulk_manifests
            '''
            params = []
            if status_filters:
                placeholders = ','.join('?' for _ in status_filters)
                sql += f' WHERE status IN ({placeholders})'
                params.extend(status_filters)
            sql += ' ORDER BY COALESCE(created_at, "") DESC, id DESC LIMIT ?'
            params.append(limit)
            cur.execute(sql, tuple(params))
            rows = [dict(r) for r in cur.fetchall()]
            photo_counts = _bulk_manifest_photo_counts(cur, [row.get('id') for row in rows])

        manifests = []
        for row in rows:
            manifest_id = max(0, ss_normalization._coerce_int(row.get('id'), 0))
            sheet_state = ss_manifest_google._bulk_manifest_google_state(row.get('id'), row=row)
            manifests.append({
                'id': manifest_id,
                'manifest_title': str(row.get('manifest_title') or '').strip(),
                'buyer': str(row.get('buyer') or '').strip(),
                'manifest_date': str(row.get('manifest_date') or '').strip(),
                'status': _bulk_manifest_status_key(row.get('status')),
                'total_items': max(0, ss_normalization._coerce_int(row.get('total_items'), 0)),
                'total_units': max(0, ss_normalization._coerce_int(row.get('total_units'), 0)),
                'total_original_retail': _bulk_manifest_money(row.get('total_original_retail'), default=0.0),
                'sold_total': _bulk_manifest_money(row.get('sold_total'), default=0.0),
                'sold_rows': max(0, ss_normalization._coerce_int(row.get('sold_rows'), 0)),
                'sold_at': str(row.get('sold_at') or '').strip(),
                'sold_session_id': str(row.get('sold_session_id') or '').strip(),
                'cleanup_at': str(row.get('cleanup_at') or '').strip(),
                'cleanup_note': str(row.get('cleanup_note') or '').strip(),
                'google_sheet_id': sheet_state['google_sheet_id'],
                'google_sheet_url': sheet_state['google_sheet_url'],
                'google_sheet_exported_at': sheet_state['google_sheet_exported_at'],
                'google_sheet_shared_with': sheet_state['google_sheet_shared_with'],
                'google_sheet_status': sheet_state['google_sheet_status'],
                'google_sheet_available': sheet_state['google_sheet_available'],
                'google_sheet_button_label': sheet_state['google_sheet_button_label'],
                'google_sheet_button_tone': sheet_state['google_sheet_button_tone'],
                'google_sheet_error': sheet_state['google_sheet_error'],
                'photo_count': photo_counts.get(manifest_id, 0),
                'photos_available': photo_counts.get(manifest_id, 0) > 0,
                'created_at': str(row.get('created_at') or '').strip(),
                'updated_at': str(row.get('updated_at') or '').strip()
            })

        return jsonify({'success': True, 'manifests': manifests})
    except Exception as e:
        return jsonify({'success': False, 'error': ss_errors._safe_error(e, 'bulk_manifest_history')}), 500


def api_bulk_manifest_detail(manifest_id):
    """Return one manifest plus line items."""
    try:
        with ss_database.db_connection('marketplace.db') as conn:
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()
            try:
                out_manifest, items = _bulk_manifest_load_detail(cur, manifest_id)
            except LookupError:
                return jsonify({'success': False, 'error': 'Manifest not found'}), 404
            out_manifest['photo_count'] = _bulk_manifest_photo_counts(cur, [manifest_id]).get(manifest_id, 0)
            out_manifest['photos_available'] = out_manifest['photo_count'] > 0

        return jsonify({
            'success': True,
            'manifest': out_manifest,
            'items': items
        })
    except Exception as e:
        return jsonify({'success': False, 'error': ss_errors._safe_error(e, 'bulk_manifest_detail')}), 500


def api_bulk_manifest_delete(manifest_id):
    """Delete one bulk manifest and clean up saved assets."""
    try:
        deleted = _bulk_manifest_delete_manifest(manifest_id)
        return jsonify(deleted)
    except LookupError:
        return jsonify({'success': False, 'error': 'Manifest not found'}), 404
    except Exception as e:
        return jsonify({'success': False, 'error': ss_errors._safe_error(e, 'bulk_manifest_delete')}), 500


def api_bulk_manifest_photos(manifest_id):
    """Return saved photos for one bulk manifest."""
    try:
        with ss_database.db_connection('marketplace.db') as conn:
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()
            _ensure_bulk_manifest_tables(cur)
            cur.execute('SELECT id, manifest_title FROM bulk_manifests WHERE id = ?', (manifest_id,))
            manifest_row = cur.fetchone()
            if not manifest_row:
                return jsonify({'success': False, 'error': 'Manifest not found'}), 404
            photos = _bulk_manifest_load_photos(cur, manifest_id)
            photo_count = len(photos)

        return jsonify({
            'success': True,
            'manifest_id': manifest_id,
            'manifest_title': str(manifest_row['manifest_title'] or '').strip(),
            'photo_count': photo_count,
            'photos_available': photo_count > 0,
            'photos': photos
        })
    except Exception as e:
        return jsonify({'success': False, 'error': ss_errors._safe_error(e, 'bulk_manifest_photos')}), 500


def api_bulk_manifest_add_photos(manifest_id):
    """Upload labeled photos for an existing bulk manifest."""
    try:
        files = request.files.getlist('photos[]') or request.files.getlist('photos') or ([] if 'photo' not in request.files else [request.files['photo']])
        if not files:
            return jsonify({'success': False, 'error': 'No photos uploaded'}), 400

        from werkzeug.utils import secure_filename

        with ss_database.db_connection('marketplace.db') as conn:
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()
            _ensure_bulk_manifest_tables(cur)
            cur.execute('SELECT id, manifest_title FROM bulk_manifests WHERE id = ?', (manifest_id,))
            manifest_row = cur.fetchone()
            if not manifest_row:
                return jsonify({'success': False, 'error': 'Manifest not found'}), 404

            manifest_title = _bulk_manifest_photo_label_text(manifest_id, manifest_row['manifest_title'])
            save_dir = os.path.join(ss_runtime.app.root_path, 'static', 'bulk_manifests')
            os.makedirs(save_dir, exist_ok=True)
            now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()

            saved_any = False
            for idx, file_storage in enumerate(files):
                if not file_storage or not getattr(file_storage, 'filename', ''):
                    continue
                original_name = secure_filename(file_storage.filename)
                base_name, _ = os.path.splitext(original_name)
                safe_stub = secure_filename(base_name) or f'bulk_manifest_{manifest_id}'
                unique_name = f'{safe_stub}_{manifest_id}_{int(time.time() * 1000)}_{idx + 1}.jpg'
                abs_path = os.path.join(save_dir, unique_name)
                rel_path = f'bulk_manifests/{unique_name}'
                _bulk_manifest_save_labeled_photo(file_storage, manifest_title, abs_path)
                cur.execute('''
                    INSERT INTO bulk_manifest_images (manifest_id, image_path, created_at)
                    VALUES (?, ?, ?)
                ''', (manifest_id, rel_path, now_iso))
                saved_any = True

            if not saved_any:
                return jsonify({'success': False, 'error': 'No valid photos uploaded'}), 400

            photos = _bulk_manifest_load_photos(cur, manifest_id)
            photo_count = len(photos)

        return jsonify({
            'success': True,
            'manifest_id': manifest_id,
            'manifest_title': manifest_title,
            'photo_count': photo_count,
            'photos_available': photo_count > 0,
            'photos': photos
        })
    except Exception as e:
        return jsonify({'success': False, 'error': ss_errors._safe_error(e, 'bulk_manifest_add_photos')}), 500


def api_bulk_manifest_google_sheet(manifest_id):
    """Queue or return Google Sheets export state for a saved bulk manifest."""
    try:
        ss_config.logger.info('bulk_manifest_google_sheet route hit for manifest %s', manifest_id)
        refresh = str(request.args.get('refresh') or '').strip().lower() in ('1', 'true', 'yes', 'on')
        payload = ss_manifest_google._bulk_manifest_google_status_payload(manifest_id)
        if not refresh and payload.get('sheet_url') and str(payload.get('status') or '').strip().lower() == 'ready':
            payload['success'] = True
            return jsonify(payload)

        queued = ss_manifest_google._bulk_manifest_queue_google_export(
            manifest_id,
            origin_base=str(request.host_url or '').strip().rstrip('/'),
            manifest_title=str(payload.get('manifest_title') or '').strip(),
            force_refresh=refresh
        )
        status = str(queued.get('status') or '').strip().lower()
        http_status = 202 if status in ('queued', 'processing') else 200
        return jsonify(queued), http_status
    except LookupError:
        return jsonify({'success': False, 'error': 'Manifest not found'}), 404
    except RuntimeError as e:
        ss_config.logger.warning('bulk_manifest_google_sheet: %s', e)
        return jsonify({'success': False, 'error': str(e) or 'Google Sheet export failed'}), 502
    except Exception as e:
        return jsonify({'success': False, 'error': ss_errors._safe_error(e, 'bulk_manifest_google_sheet')}), 500


def api_bulk_manifest_google_sheet_delete(manifest_id):
    """Delete the Google Sheet resource associated with a manifest and clear saved linkage."""
    try:
        result = ss_manifest_google._bulk_manifest_google_delete_manifest(manifest_id)
        state = ss_manifest_google._bulk_manifest_google_state(manifest_id)
        payload = {
            'success': True,
            'manifest_id': manifest_id,
            'deleted': bool(result.get('deleted')),
            'status': state['google_sheet_status'],
            'sheet_id': state['google_sheet_id'],
            'sheet_url': state['google_sheet_url'],
            'button_label': state['google_sheet_button_label'],
            'button_tone': state['google_sheet_button_tone'],
            'sheet_available': state['google_sheet_available']
        }
        return jsonify(payload)
    except LookupError:
        return jsonify({'success': False, 'error': 'Manifest not found'}), 404
    except RuntimeError as e:
        ss_config.logger.warning('bulk_manifest_google_sheet_delete: %s', e)
        return jsonify({'success': False, 'error': str(e) or 'Failed to delete Google Sheet'}), 502
    except Exception as e:
        return jsonify({'success': False, 'error': ss_errors._safe_error(e, 'bulk_manifest_google_sheet_delete')}), 500


def api_bulk_manifest_google_sheet_status(manifest_id):
    """Return background Google Sheet export status for one manifest."""
    try:
        payload = ss_manifest_google._bulk_manifest_google_status_payload(manifest_id)
        status = str(payload.get('status') or '').strip().lower()
        http_status = 202 if status in ('queued', 'processing') else 200
        return jsonify(payload), http_status
    except LookupError:
        return jsonify({'success': False, 'error': 'Manifest not found'}), 404
    except Exception as e:
        return jsonify({'success': False, 'error': ss_errors._safe_error(e, 'bulk_manifest_google_sheet_status')}), 500


def api_bulk_manifest_google_sheet_health():
    """Lightweight diagnostics for Pi-side Google Sheets export issues."""
    try:
        probe = str(request.args.get('probe') or '').strip().lower() in ('1', 'true', 'yes', 'on')
        health = ss_manifest_google._bulk_manifest_google_health(include_token_probe=probe)
        ok = (
            health.get('credentials_present')
            and health.get('service_account')
            and health.get('client_email_present')
            and health.get('cryptography_ok')
            and (health.get('token_ok') is not False)
        )
        return jsonify({
            'success': bool(ok),
            'health': health
        }), (200 if ok else 503)
    except Exception as e:
        return jsonify({'success': False, 'error': ss_errors._safe_error(e, 'bulk_manifest_google_sheet_health')}), 500


def api_bulk_manifest_mark_sold(manifest_id):
    """Mark one manifest as sold through marketplace sale processing."""
    try:
        data = request.get_json() or {}
        session_id = str(data.get('session_id') or '').strip()
        sold_total = _bulk_manifest_money(data.get('sold_total'), default=0.0)
        sold_rows = max(0, ss_normalization._coerce_int(data.get('sold_rows'), 0))
        status = _bulk_manifest_status_key(data.get('status') or 'sold')
        if status == 'created':
            status = 'sold'
        now_iso = datetime.datetime.now().isoformat()

        with ss_database.db_connection('marketplace.db') as conn:
            cur = conn.cursor()
            _ensure_bulk_manifest_tables(cur)
            cur.execute('SELECT id FROM bulk_manifests WHERE id = ?', (manifest_id,))
            exists = cur.fetchone()
            if not exists:
                return jsonify({'success': False, 'error': 'Manifest not found'}), 404

            cur.execute('''
                UPDATE bulk_manifests
                SET status = ?,
                    sold_at = ?,
                    sold_session_id = ?,
                    sold_total = ?,
                    sold_rows = ?,
                    updated_at = ?
                WHERE id = ?
            ''', (status, now_iso, session_id, sold_total, sold_rows, now_iso, manifest_id))

        return jsonify({
            'success': True,
            'manifest_id': manifest_id,
            'status': status,
            'sold_at': now_iso
        })
    except Exception as e:
        return jsonify({'success': False, 'error': ss_errors._safe_error(e, 'bulk_manifest_mark_sold')}), 500


def api_bulk_manifest_cleanup_items():
    """Return merged manifest line items (barcode + qty) for move-location cleanup queues."""
    try:
        manifest_ids = _bulk_manifest_parse_ids(request.args.get('manifest_ids'))
        if not manifest_ids:
            return jsonify({'success': False, 'error': 'manifest_ids is required'}), 400

        with ss_database.db_connection('marketplace.db') as conn:
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()
            _ensure_bulk_manifest_tables(cur)

            placeholders = ','.join('?' for _ in manifest_ids)
            cur.execute(f'''
                SELECT id, manifest_title, buyer, manifest_date, status, sold_at, created_at
                FROM bulk_manifests
                WHERE id IN ({placeholders})
                ORDER BY COALESCE(created_at, '') DESC, id DESC
            ''', tuple(manifest_ids))
            manifests = [dict(r) for r in cur.fetchall()]
            if not manifests:
                return jsonify({'success': False, 'error': 'No manifests found'}), 404

            cur.execute(f'''
                SELECT manifest_id, barcode, title, quantity, unit_price, line_total, image_url, lot_number
                FROM bulk_manifest_items
                WHERE manifest_id IN ({placeholders})
                ORDER BY manifest_id ASC, line_number ASC, id ASC
            ''', tuple(manifest_ids))
            item_rows = [dict(r) for r in cur.fetchall()]

        manifest_by_id = {}
        for manifest in manifests:
            manifest_id = max(0, ss_normalization._coerce_int(manifest.get('id'), 0))
            if manifest_id <= 0:
                continue
            manifest_by_id[manifest_id] = {
                'id': manifest_id,
                'manifest_title': str(manifest.get('manifest_title') or '').strip(),
                'buyer': str(manifest.get('buyer') or '').strip(),
                'manifest_date': str(manifest.get('manifest_date') or '').strip(),
                'status': _bulk_manifest_status_key(manifest.get('status')),
                'sold_at': str(manifest.get('sold_at') or '').strip(),
                'created_at': str(manifest.get('created_at') or '').strip()
            }

        merged = {}
        total_units = 0
        for row in item_rows:
            manifest_id = max(0, ss_normalization._coerce_int(row.get('manifest_id'), 0))
            barcode = ss_normalization._normalize_upc_preserve_suffix_for_match(
                ss_normalization._normalize_scanned_upc(row.get('barcode'))
            )
            barcode = str(barcode or '').strip()
            if manifest_id <= 0 or not barcode:
                continue

            qty = max(1, ss_normalization._coerce_int(row.get('quantity'), 1))
            key = ss_warehouse_locations._movelocation_barcode_key(barcode) or barcode.lower()
            bucket = merged.get(key)
            if bucket is None:
                bucket = {
                    'barcode': barcode,
                    'barcode_display': ss_normalization._format_upc_display(barcode),
                    'barcode_key': key,
                    'title': str(row.get('title') or '').strip() or barcode,
                    'image': str(row.get('image_url') or '').strip(),
                    'quantity': 0,
                    'unit_price_total': 0.0,
                    'manifest_ids': [],
                    'manifest_titles': [],
                    'lot_numbers': []
                }
                merged[key] = bucket

            bucket['quantity'] += qty
            bucket['unit_price_total'] = round(
                bucket['unit_price_total'] + _bulk_manifest_money(row.get('line_total'), default=0.0),
                2
            )
            if manifest_id not in bucket['manifest_ids']:
                bucket['manifest_ids'].append(manifest_id)
                manifest_title = manifest_by_id.get(manifest_id, {}).get('manifest_title') or f'Manifest #{manifest_id}'
                bucket['manifest_titles'].append(manifest_title)
            lot_number = ss_normalization._normalize_lot_number(row.get('lot_number'))
            if lot_number and lot_number not in bucket['lot_numbers']:
                bucket['lot_numbers'].append(lot_number)
            if not bucket.get('title'):
                bucket['title'] = str(row.get('title') or '').strip() or barcode
            if not bucket.get('image'):
                bucket['image'] = str(row.get('image_url') or '').strip()

            total_units += qty

        merged_items = sorted(
            merged.values(),
            key=lambda x: (str(x.get('title') or '').lower(), str(x.get('barcode') or '').lower())
        )

        ordered_manifests = []
        for manifest_id in manifest_ids:
            if manifest_id in manifest_by_id:
                ordered_manifests.append(manifest_by_id[manifest_id])

        return jsonify({
            'success': True,
            'manifest_ids': [m['id'] for m in ordered_manifests],
            'manifests': ordered_manifests,
            'items': merged_items,
            'items_count': len(merged_items),
            'total_units': int(total_units or 0)
        })
    except Exception as e:
        return jsonify({'success': False, 'error': ss_errors._safe_error(e, 'bulk_manifest_cleanup_items')}), 500
