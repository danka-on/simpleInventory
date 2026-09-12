"""Finder for Sweet Shelves."""

import datetime
import sqlite3
from DBmanager import connect_db
from flask import jsonify, make_response, render_template, request
from . import (
    config as ss_config, errors as ss_errors, inventory_history as ss_inventory_history, normalization as
    ss_normalization, prep_context as ss_prep_context, runtime as ss_runtime, sales as ss_sales,
)


def finder_page():
    response = make_response(render_template('finder.html'))
    response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    response.headers['Pragma'] = 'no-cache'
    response.headers['Expires'] = '0'
    return response


def _finder_barcode_search_variants(value):
    """Return padded/stripped barcode forms while preserving an item suffix."""
    raw = ss_normalization._normalize_upc(value)
    if not raw:
        return []

    variants = []

    def add(candidate):
        candidate = str(candidate or '').strip()
        if candidate and candidate not in variants:
            variants.append(candidate)

    add(raw)
    normalized = ss_normalization._normalize_upc_preserve_suffix_for_match(raw)
    add(normalized)

    if normalized and '-' in normalized:
        base, suffix = normalized.split('-', 1)
    else:
        base, suffix = normalized, ''

    if base and base.isdigit():
        stripped_base = base.lstrip('0') or '0'
        add(f'{stripped_base}-{suffix}' if suffix else stripped_base)
        for width in (8, 11, 12, 13, 14):
            if len(stripped_base) < width:
                padded_base = stripped_base.zfill(width)
                add(f'{padded_base}-{suffix}' if suffix else padded_base)

    return variants


def api_finder():
    """Search inventory, BOL items, and optionally rack history."""
    data = request.get_json() or {}
    q = data.get('q', '').strip()
    custom_only = bool(data.get('custom_only'))
    if not q and not custom_only:
        return jsonify({'searchrack': [], 'rawbol': []})

    from finder_search import name_scorer, is_barcode_query
    from finder_records import upc_key
    score_name = name_scorer(q, any_words=data.get('match_mode') == 'any')
    barcode_query = is_barcode_query(q)
    # A scanned code can stand for every -suffix unit of the same product.
    family = bool(data.get('family')) and barcode_query
    family_base = upc_key(q).split('-', 1)[0] if family else ''
    from finder_aliases import load_aliases, barcode_key
    aliases = load_aliases(connect_db)

    def warehouse_score(title, barcode):
        return max([score_name(title)] + [score_name(alias['title'])
                   for alias in aliases.get(barcode_key(barcode), [])])

    def family_matches(value):
        key = upc_key(value)
        return bool(key) and key.split('-', 1)[0] == family_base

    searchrack_results = []
    rawbol_results = []
    rackhistory_results = []
    rackhistory_error = None

    def build_where(title_col, barcode_col):
        if family:
            return f'finder_family_matches({barcode_col})', []
        if barcode_query:
            variants = _finder_barcode_search_variants(q)
            variants += [v + '.0' for v in variants if v.isdigit()]
            placeholders = ','.join('?' for _ in variants)
            return f"CAST({barcode_col} AS TEXT) COLLATE NOCASE IN ({placeholders})", variants
        return f"finder_name_score({title_col}) > 0", []

    # Search searchRack.db
    try:
        with connect_db('searchRack.db') as conn:
            conn.row_factory = sqlite3.Row
            conn.create_function('finder_name_score', 1, score_name, deterministic=True)
            conn.create_function('finder_family_matches', 1, family_matches, deterministic=True)
            conn.create_function('finder_warehouse_score', 2, warehouse_score, deterministic=True)
            cur = conn.cursor()
            params = []
            conditions = ['COALESCE(CAST(QUANTITY AS INTEGER), 0) > 0']
            if q:
                where, params = build_where('TITLE', 'BARCODE')
                if not barcode_query:
                    where = 'finder_warehouse_score(TITLE, BARCODE) > 0'
                conditions.insert(0, f'({where})')
            if custom_only:
                conditions.append(
                    "(COALESCE(CUSTOM_TITLE, 0) = 1 "
                    "OR COALESCE(IMAGE, '') LIKE '/static/custom_items/%')"
                )
            cur.execute(f'''SELECT ID, TITLE, BARCODE, ITEM_POSITION, IMAGES, PICTUREPOSITION, QUANTITY, IMAGE, ITEMID,
                                  COALESCE(WAREHOUSE_NOTE, '') AS WAREHOUSE_NOTE
                           FROM SEARCHRACK
                           WHERE {' AND '.join(conditions)}
                           ORDER BY finder_warehouse_score(TITLE, BARCODE) DESC, finder_name_score(TITLE) DESC, TITLE COLLATE NOCASE, ID''', params)
            for row in cur.fetchall():
                barcode_raw = row['BARCODE']
                searchrack_results.append({
                    'id': row['ID'],
                    'title': row['TITLE'],
                    'barcode': barcode_raw,
                    'barcode_display': ss_normalization._format_upc_display(barcode_raw),
                    'item_position': row['ITEM_POSITION'],
                    'pictureposition': row['PICTUREPOSITION'],
                    'quantity': row['QUANTITY'],
                    'image': row['IMAGE'],
                    'itemid': row['ITEMID'],
                    'warehouse_note': row['WAREHOUSE_NOTE'],
                    'aliases': [dict(alias, matched=bool(q and not barcode_query and score_name(alias['title'])))
                                for alias in aliases.get(barcode_key(barcode_raw), [])],
                })
    except Exception as e:
        print(f"Finder searchRack error: {e}")

    if searchrack_results:
        prep_lookup = ss_prep_context._load_suffixed_item_prep_summary_lookup(
            row.get('barcode') for row in searchrack_results
        )
        for row in searchrack_results:
            prep_key = ss_normalization._normalize_upc_preserve_suffix_for_match(row.get('barcode'))
            row['item_prep_summary'] = prep_lookup.get(
                prep_key,
                ss_prep_context._empty_suffixed_item_prep_summary(prep_key)
            )

    if data.get('include_history') and q:
        try:
            with connect_db('rackhistory.db') as conn:
                conn.row_factory = sqlite3.Row
                conn.create_function('finder_name_score', 1, score_name, deterministic=True)
                conn.create_function('finder_family_matches', 1, family_matches, deterministic=True)
                where, params = build_where('title', 'barcode')
                rows = conn.execute(f'''
                    SELECT id, title, barcode, quantity_removed, removed_at,
                           removal_type, item_position, undone_at
                    FROM removed_items WHERE {where}
                    ORDER BY removed_at DESC, id DESC
                ''', params).fetchall()
                rackhistory_results = [dict(row) for row in rows]
        except Exception as e:
            rackhistory_error = ss_errors._safe_error(e)

    # Custom-only mode filters warehouse inventory; history is independently opt-in.
    if custom_only:
        return jsonify({'searchrack': searchrack_results, 'rawbol': [],
                        'rackhistory': rackhistory_results, 'rackhistory_error': rackhistory_error})

    # Search product descriptions using the same rules as warehouse names.
    try:
        with connect_db('rawbol.db') as conn:
            conn.row_factory = sqlite3.Row
            conn.create_function('finder_name_score', 1, score_name, deterministic=True)
            conn.create_function('finder_family_matches', 1, family_matches, deterministic=True)
            cur = conn.cursor()

            where, params = build_where('item_description', 'upc')
            where_clause = 'WHERE ' + where

            cur.execute(
                f'''
                    SELECT upc, item_description, image_url, lot_number, quantity, import_date
                    FROM raw_bol_items
                    {where_clause}
                    ORDER BY import_date DESC, id DESC
                ''',
                params
            )
            rows = cur.fetchall()

            grouped = {}
            for row in rows:
                upc_raw = row['upc']
                upc = str(upc_raw or '').strip()
                if upc.endswith('.0') and upc.replace('.0', '').isdigit():
                    upc = upc[:-2]
                if not upc:
                    continue

                lot_number = str(row['lot_number'] or '').strip()
                if not lot_number or lot_number.lower() in ('nan', 'none', 'null'):
                    lot_number = '(No LOT)'

                qty_raw = row['quantity']
                try:
                    qty = int(qty_raw)
                except Exception:
                    qty = 1
                if qty < 0:
                    qty = 0

                import_date = str(row['import_date'] or '').strip()
                if not import_date or import_date.lower() in ('nan', 'none', 'null'):
                    import_date = '(No Date)'

                entry = grouped.get(upc)
                if not entry:
                    entry = {
                        'upc': upc,
                        'item_description': row['item_description'] or '',
                        'image_url': row['image_url'] or '',
                        'quantity': 0,
                        'lot_data': {},
                    }
                    grouped[upc] = entry
                else:
                    if not entry['item_description'] and row['item_description']:
                        entry['item_description'] = row['item_description']
                    if not entry['image_url'] and row['image_url']:
                        entry['image_url'] = row['image_url']

                entry['quantity'] += qty
                lot_entry = entry['lot_data'].get(lot_number)
                if not lot_entry:
                    entry['lot_data'][lot_number] = {
                        'lot_number': lot_number,
                        'qty': qty,
                        'import_date': import_date,
                    }
                else:
                    lot_entry['qty'] += qty
                    if lot_entry.get('import_date') in ('', '(No Date)') and import_date != '(No Date)':
                        lot_entry['import_date'] = import_date

            for upc, entry in grouped.items():
                lot_data = list(entry['lot_data'].values())
                lot_data.sort(key=lambda ld: str(ld.get('import_date') or ''), reverse=True)
                unique_lots = [ld['lot_number'] for ld in lot_data if ld.get('lot_number') != '(No LOT)']
                unique_lots = list(dict.fromkeys(unique_lots))

                if not unique_lots:
                    lot_display = '(No LOT)'
                    has_multiple = False
                elif len(unique_lots) == 1:
                    lot_display = unique_lots[0]
                    has_multiple = False
                else:
                    lot_display = 'multiple LOTS'
                    has_multiple = True

                rawbol_results.append({
                    'upc': upc,
                    'upc_display': ss_normalization._format_upc_display(upc),
                    'item_description': entry['item_description'],
                    'image_url': entry['image_url'],
                    'lot_number': lot_display,
                    'quantity': entry['quantity'],
                    'has_multiple_lots': has_multiple,
                    'lot_data': lot_data,
                })

            rawbol_results.sort(key=lambda item: score_name(item['item_description']), reverse=True)
    except Exception as e:
        print(f"Finder rawbol error: {e}")

    return jsonify({'searchrack': searchrack_results, 'rawbol': rawbol_results,
                    'rackhistory': rackhistory_results, 'rackhistory_error': rackhistory_error})


def api_finder_records():
    from finder_records import collect_records
    try:
        offset = int(request.args.get('offset', 0))
        if offset < 0:
            raise ValueError('Invalid page offset.')
        return jsonify(collect_records(
            ss_config.BASE_DIR, connect_db, (request.args.get('upc') or '').strip(),
            family=request.args.get('family') == '1',
            source=request.args.get('source') or None, offset=offset,
        ))
    except ValueError as e:
        return jsonify({'error': str(e)}), 400
    except Exception as e:
        return jsonify({'error': ss_errors._safe_error(e)}), 500


def api_finder_trail():
    """One merged, read-only timeline with ledger totals and clues for a UPC."""
    from finder_trail import collect_trail
    try:
        return jsonify(collect_trail(
            ss_config.BASE_DIR, connect_db, (request.args.get('upc') or '').strip(),
            family=request.args.get('family') == '1',
        ))
    except ValueError as e:
        return jsonify({'error': str(e)}), 400
    except Exception as e:
        return jsonify({'error': ss_errors._safe_error(e)}), 500


def api_finder_forget_alias():
    from finder_aliases import DATABASES, forget_alias, forget_name
    data = request.get_json() or {}
    source = data.get('source')
    alias_id = str(data.get('id') or '')
    if source not in DATABASES or not alias_id:
        return jsonify({'success': False, 'error': 'An alternate name is required'}), 400
    try:
        with connect_db(DATABASES[source]) as conn:
            forgotten = forget_alias(conn, alias_id)
        if forgotten:
            for other_source, database in DATABASES.items():
                if other_source != source:
                    with connect_db(database) as conn:
                        forget_name(conn, *forgotten)
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': ss_errors._safe_error(e)}), 500


def api_lookup_image_by_barcode():
    """Lookup image URL from rawbol.db by barcode with comprehensive matching"""
    barcode = request.args.get('barcode', '').strip()
    if not barcode:
        return jsonify({'success': False, 'error': 'No barcode provided'})

    # Clean barcode - remove .0 suffix if present (SQLite numeric artifact)
    if barcode.endswith('.0'):
        barcode = barcode[:-2]

    try:
        conn = sqlite3.connect('rawbol.db')
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()

        row = None
        barcode_stripped = ss_normalization._strip_leading_zeros_numeric(barcode)

        # Try exact match first
        cur.execute('SELECT image_url FROM raw_bol_items WHERE upc = ? COLLATE NOCASE LIMIT 1', (barcode,))
        row = cur.fetchone()

        # Try stripped zeros version
        if not row and barcode_stripped != barcode:
            cur.execute('SELECT image_url FROM raw_bol_items WHERE upc = ? COLLATE NOCASE LIMIT 1', (barcode_stripped,))
            row = cur.fetchone()

        # Try padded to 12 digits
        if not row and barcode.isdigit():
            padded = barcode.zfill(12)
            if padded != barcode:
                cur.execute('SELECT image_url FROM raw_bol_items WHERE upc = ? COLLATE NOCASE LIMIT 1', (padded,))
                row = cur.fetchone()

        # Try padded to 13 digits (EAN)
        if not row and barcode.isdigit():
            padded13 = barcode.zfill(13)
            if padded13 != barcode:
                cur.execute('SELECT image_url FROM raw_bol_items WHERE upc = ? COLLATE NOCASE LIMIT 1', (padded13,))
                row = cur.fetchone()

        # Try suffix match - find rawbol UPC that ends with our barcode (rawbol has more leading zeros)
        if not row and barcode_stripped and len(barcode_stripped) >= 8:
            cur.execute("SELECT image_url FROM raw_bol_items WHERE REPLACE(upc, '.0', '') LIKE ? COLLATE NOCASE LIMIT 1", ('%' + barcode_stripped,))
            row = cur.fetchone()

        # Try finding where rawbol's stripped UPC matches our stripped barcode
        if not row and barcode_stripped and len(barcode_stripped) >= 8:
            cur.execute("""
                SELECT image_url FROM raw_bol_items
                WHERE CAST(CAST(REPLACE(upc, '.0', '') AS INTEGER) AS TEXT) = ?
                COLLATE NOCASE LIMIT 1
            """, (barcode_stripped,))
            row = cur.fetchone()

        conn.close()

        if row and row['image_url']:
            return jsonify({'success': True, 'image_url': row['image_url']})
        else:
            return jsonify({'success': False, 'error': 'No image found'})
    except Exception as e:
        return jsonify({'success': False, 'error': ss_errors._safe_error(e)})


def api_finder_assign():
    """Assign a location to a sold order."""
    data = request.get_json() or {}
    order_id = data.get('order_id')
    location = data.get('location', '').strip()
    if not order_id or not location:
        return jsonify({'error': 'Missing order_id or location'}), 400
    try:
        conn = sqlite3.connect(str(ss_config.BASE_DIR / 'sold.db'))
        cur = conn.cursor()
        cur.execute('UPDATE orders SET location = ? WHERE id = ?', (location, order_id))
        conn.commit()
        conn.close()
        # Clear the sold-orders cache so the change is visible immediately
        try:
            ss_runtime.cache.delete_memoized(ss_sales.sold_orders)
        except Exception:
            pass
        return jsonify({'ok': True})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


def api_finder_remove():
    """Remove 1 (or sold qty) from inventory for a specific searchRack row."""
    import datetime as _dt
    data = request.get_json() or {}
    searchrack_id = data.get('searchrack_id')
    order_id = data.get('order_id')  # optional — sold order context
    qty_to_remove = ss_normalization._strict_inventory_quantity(data.get('qty', 1))
    if qty_to_remove is None:
        return jsonify({'ok': False, 'error': 'Invalid quantity'}), 400
    if not searchrack_id:
        return jsonify({'ok': False, 'error': 'Missing searchrack_id'}), 400
    if qty_to_remove <= 0:
        return jsonify({'ok': False, 'error': 'Quantity must be positive'}), 400
    if order_id:
        return jsonify({
            'ok': False,
            'error': 'Finder cannot remove sold-order inventory directly anymore. Match the item here if needed, then confirm it from Ready to Ship.',
            'manual_mode': True,
            'redirect_url': '/ready_to_ship'
        }), 409
    try:
        r_conn = sqlite3.connect(str(ss_config.BASE_DIR / 'searchRack.db'))
        r_conn.row_factory = sqlite3.Row
        r_cur = r_conn.cursor()
        r_cur.execute('SELECT ID, TITLE, BARCODE, QUANTITY, ITEM_POSITION FROM SEARCHRACK WHERE ID = ?', (searchrack_id,))
        row = r_cur.fetchone()
        if not row:
            r_conn.close()
            return jsonify({'ok': False, 'error': 'Item not found'}), 404
        current_qty = int(row['QUANTITY'] or 0)
        if current_qty <= 0:
            r_conn.close()
            return jsonify({'ok': False, 'error': 'Item has no active inventory'}), 409
        if qty_to_remove > current_qty:
            r_conn.close()
            return jsonify({
                'ok': False,
                'error': f'Only {current_qty} unit(s) are available at this location'
            }), 409
        new_qty = max(0, current_qty - qty_to_remove)
        r_cur.execute('UPDATE SEARCHRACK SET QUANTITY = ? WHERE ID = ?', (new_qty, searchrack_id))
        r_conn.commit()

        # The SEARCHRACK trigger already made a durable same-transaction snapshot.
        # Add the legacy breadcrumb best-effort; a failure here must not invite a second removal.
        rem_conn = None
        try:
            rem_conn = sqlite3.connect(str(ss_config.BASE_DIR / 'rackhistory.db'))
            rem_cur = rem_conn.cursor()
            ss_inventory_history._ensure_removed_items_table(rem_cur)
            ss_inventory_history._ensure_legacy_removed_table(rem_cur)
            removed_at = _dt.datetime.now().isoformat()
            rem_cur.execute('''
                INSERT INTO removed_items
                (order_id, barcode, title, quantity_removed, removed_at, searchrack_id,
                 old_quantity, new_quantity, removal_type, item_position, event_status)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending')
            ''', (
                order_id or '', row['BARCODE'] or '', row['TITLE'] or '', qty_to_remove,
                removed_at, searchrack_id, current_qty, new_qty, 'finder_removal', row['ITEM_POSITION']
            ))
            detail_history_id = rem_cur.lastrowid
            rem_cur.execute('''
                INSERT INTO removed (
                    name, barcode, qty, time_removed, detail_history_id, source_location
                ) VALUES (?, ?, ?, ?, ?, ?)
            ''', (
                row['TITLE'] or '', row['BARCODE'] or '', qty_to_remove, removed_at,
                detail_history_id, row['ITEM_POSITION'] or ''
            ))
            rem_conn.commit()
        except Exception as history_error:
            try:
                if rem_conn is not None:
                    rem_conn.rollback()
            except Exception:
                pass
            ss_config.logger.warning('Finder removal history breadcrumb deferred: %s', history_error)
        finally:
            try:
                if rem_conn is not None:
                    rem_conn.close()
            except Exception:
                pass

        # If order context, mark order as rackupdated
        if order_id:
            s_conn = sqlite3.connect(str(ss_config.BASE_DIR / 'sold.db'))
            s_cur = s_conn.cursor()
            s_cur.execute('UPDATE orders SET rackupdated = 1 WHERE id = ?', (order_id,))
            s_conn.commit()
            s_conn.close()

        r_conn.close()
        return jsonify({'ok': True, 'new_qty': new_qty, 'old_qty': current_qty, 'searchrack_id': searchrack_id, 'order_id': order_id or None})
    except Exception as e:
        return jsonify({'ok': False, 'error': ss_errors._safe_error(e)}), 500


def api_finder_undo_remove():
    """Undo a finder removal, recreating a deleted terminal row from its snapshot."""
    data = request.get_json() or {}
    searchrack_id = data.get('searchrack_id')
    order_id = data.get('order_id')
    if not searchrack_id:
        return jsonify({'ok': False, 'error': 'Missing searchrack_id'}), 400

    r_conn = None
    h_conn = None
    s_conn = None
    try:
        ss_inventory_history._flush_searchrack_history_outbox()
        h_conn = sqlite3.connect(str(ss_config.BASE_DIR / 'rackhistory.db'))
        h_conn.row_factory = sqlite3.Row
        h_cur = h_conn.cursor()
        ss_inventory_history._ensure_removed_items_table(h_cur)
        h_cur.execute('''
            SELECT * FROM removed_items
            WHERE searchrack_id = ?
              AND removal_type IN ('finder_removal', 'inventory_depleted')
              AND (undone_at IS NULL OR undone_at = '')
              AND COALESCE(event_status, 'applied') = 'applied'
            ORDER BY id DESC
            LIMIT 1
        ''', (int(searchrack_id),))
        history_row = h_cur.fetchone()
        if not history_row:
            return jsonify({'ok': False, 'error': 'Finder removal history was not found; inventory was not changed'}), 409
        restore_quantity = ss_inventory_history._history_restore_quantity(history_row)
        if restore_quantity <= 0:
            return jsonify({'ok': False, 'error': 'Finder history has no quantity to restore'}), 409

        r_conn = sqlite3.connect(str(ss_config.BASE_DIR / 'searchRack.db'))
        r_conn.row_factory = sqlite3.Row
        r_cur = r_conn.cursor()
        ss_inventory_history._ensure_searchrack_undo_claims(r_cur)
        r_conn.commit()
        r_cur.execute('BEGIN IMMEDIATE')
        restored = ss_inventory_history._restore_searchrack_with_undo_claim(r_cur, history_row, restore_quantity)
        r_conn.commit()

        now_iso = datetime.datetime.now().isoformat()
        h_cur.execute(
            'UPDATE removed_items SET undone_at = ? WHERE id = ? AND (undone_at IS NULL OR undone_at = "")',
            (now_iso, history_row['id'])
        )
        ss_inventory_history._insert_inventory_undo_history(
            h_cur,
            history_row,
            restored,
            'finder_removal_undo',
            order_id=order_id or None,
            undone_at=now_iso,
        )
        h_conn.commit()

        if order_id:
            s_conn = sqlite3.connect(str(ss_config.BASE_DIR / 'sold.db'))
            s_cur = s_conn.cursor()
            s_cur.execute('UPDATE orders SET rackupdated = 0 WHERE id = ?', (order_id,))
            s_conn.commit()

        return jsonify({
            'ok': True,
            'restored_qty': restored['new_quantity'],
            'restored_units': restored['restore_quantity'],
            'searchrack_id': restored['searchrack_id'],
            'recreated': restored['recreated']
        })
    except Exception as e:
        for connection in (s_conn, h_conn, r_conn):
            try:
                if connection is not None:
                    connection.rollback()
            except Exception:
                pass
        return jsonify({'ok': False, 'error': ss_errors._safe_error(e)}), 500
    finally:
        for connection in (s_conn, h_conn, r_conn):
            try:
                if connection is not None:
                    connection.close()
            except Exception:
                pass
