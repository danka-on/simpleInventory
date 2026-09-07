"""Warehouse search for Sweet Shelves."""

import re
import sqlite3
import time
from flask import jsonify, render_template, request
from . import (
    caching as ss_caching, config as ss_config, database as ss_database, errors as ss_errors,
    inventory_age as ss_inventory_age, inventory_history as ss_inventory_history, listing_alerts as
    ss_listing_alerts, listing_lifecycle as ss_listing_lifecycle, listing_settings as
    ss_listing_settings, normalization as ss_normalization, prep_context as ss_prep_context, prep_log as
    ss_prep_log, shipping_identity as ss_shipping_identity, warehouse_matching as ss_warehouse_matching,
)


def unified_search_page():
    """Unified search page that searches across all databases"""
    return render_template('unified_search.html')


def searchrack_api():
    q = request.args.get('q', '').strip()
    # Strip leading zeros from barcode searches
    q_stripped = ss_normalization._strip_leading_zeros_numeric(q)
    results = []
    if q_stripped:
        conn = sqlite3.connect('searchRack.db')
        try:
            cur = conn.cursor()
            tokens = []
            seen_tokens = set()
            for part in q_stripped.split():
                token = str(part or '').strip()
                if not token:
                    continue
                token_key = token.lower()
                if token_key in seen_tokens:
                    continue
                seen_tokens.add(token_key)
                tokens.append(token)
            if not tokens:
                tokens = [q_stripped]

            title_sql = ' AND '.join(['TITLE LIKE ? COLLATE NOCASE' for _ in tokens])
            barcode_sql = ' OR '.join(['BARCODE LIKE ? COLLATE NOCASE' for _ in tokens])
            position_sql = ' OR '.join([
                'ITEM_POSITION LIKE ? COLLATE NOCASE',
                'PICTUREPOSITION LIKE ? COLLATE NOCASE'
            ])
            params = tuple(
                [f'%{token}%' for token in tokens]
                + [f'%{token}%' for token in tokens]
                + [f'%{q_stripped}%'] * 2
            )

            cur.execute("PRAGMA table_info(SEARCHRACK)")
            searchrack_cols = [r[1] for r in cur.fetchall()]
            note_expr = 'WAREHOUSE_NOTE' if 'WAREHOUSE_NOTE' in searchrack_cols else "'' AS WAREHOUSE_NOTE"
            cur.execute(f'''
                SELECT TITLE, BARCODE, ITEM_POSITION, IMAGES, PICTUREPOSITION, QUANTITY, IMAGE, ITEMID, {note_expr}
                FROM SEARCHRACK
                WHERE (({title_sql}) OR ({barcode_sql}) OR ({position_sql}))
                  AND COALESCE(CAST(QUANTITY AS INTEGER), 0) > 0
            ''', params)
            for row in cur.fetchall():
                results.append({
                    'title': row[0],
                    'barcode': row[1],
                    'item_position': row[2],
                    'images': row[3],
                    'pictureposition': row[4],
                    'quantity': row[5],
                    'image': row[6],
                    'itemid': row[7],
                    'warehouse_note': row[8],
                })
        finally:
            conn.close()
        # Merge results that share same barcode + item_position by summing quantities
        merged = {}
        for r in results:
            bc = (r.get('barcode') or '').strip()
            pos = (r.get('item_position') or '').strip()
            # only merge when barcode present
            if not bc:
                # use a unique key to preserve as-is
                key = f"__{id(r)}"
            else:
                key = f"{bc.lower()}||{pos.lower()}"
            if key not in merged:
                # initialize quantity from database value
                merged[key] = r.copy()
                # Properly handle quantity: use value from DB if present, otherwise default to 1
                qty_val = r.get('quantity')
                if qty_val is not None:
                    try:
                        merged[key]['quantity'] = int(qty_val)
                    except (ValueError, TypeError):
                        merged[key]['quantity'] = 1
                else:
                    merged[key]['quantity'] = 1
            else:
                # sum quantities from duplicate rows
                qty_val = r.get('quantity')
                if qty_val is not None:
                    try:
                        add_q = int(qty_val)
                    except (ValueError, TypeError):
                        add_q = 1
                else:
                    add_q = 1
                merged[key]['quantity'] = merged[key].get('quantity', 0) + add_q
        # convert merged back to list
        results = list(merged.values())
    return jsonify({'results': results})


def searchbol_api():
    q = request.args.get('q', '').strip()
    # Strip leading zeros from barcode searches
    q_stripped = ss_normalization._strip_leading_zeros_numeric(q)
    results = []
    if q_stripped:
        conn = sqlite3.connect('rawbol.db')
        try:
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()
            cur.execute('''
            SELECT item_description as description, upc, avg_cost, 
                   lot_number, bol_location, quantity
            FROM raw_bol_items 
            WHERE item_description LIKE ? OR upc LIKE ? OR lot_number LIKE ? OR bol_location LIKE ?
        ''', (f'%{q_stripped}%', f'%{q_stripped}%', f'%{q_stripped}%', f'%{q_stripped}%'))
            results = [dict(row) for row in cur.fetchall()]
        finally:
            conn.close()
    return jsonify({'results': results})


def view_all(db_type):
    if db_type == 'rack':
        conn = sqlite3.connect('searchRack.db')
        try:
            cur = conn.cursor()
            cur.execute('SELECT * FROM SEARCHRACK WHERE COALESCE(CAST(QUANTITY AS INTEGER), 0) > 0')
            columns = [desc[0] for desc in cur.description]
            items = [dict(zip(columns, row)) for row in cur.fetchall()]
            title = 'All Inventory Items'
        finally:
            conn.close()
    elif db_type == 'bol':
        conn = sqlite3.connect('rawbol.db')
        try:
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()
            cur.execute('SELECT * FROM raw_bol_items')
            items = [dict(row) for row in cur.fetchall()]
            title = 'All BOL Items'
        finally:
            conn.close()
    else:
        return 'Invalid database type', 400
    
    return render_template('view_all.html', items=items, title=title, db_type=db_type)


def update_item(db_type, item_id):
    if db_type not in ['rack', 'bol']:
        return jsonify({'success': False, 'error': 'Invalid database type'}), 400
    
    data = request.get_json()
    if not data:
        return jsonify({'success': False, 'error': 'No data provided'}), 400
    
    conn = None
    try:
        if db_type == 'rack':
            conn = sqlite3.connect('searchRack.db')
        else:  # bol
            conn = sqlite3.connect('rawbol.db')
        
        cur = conn.cursor()
        
        # Get column names to validate fields
        cur.execute(f'PRAGMA table_info({"SEARCHRACK" if db_type == "rack" else "raw_bol_items"})')
        columns = [col[1] for col in cur.fetchall()]
        
        # Normalize data keys to uppercase for searchRack (case-insensitive matching)
        if db_type == 'rack':
            normalized_data = {k.upper(): v for k, v in data.items()}
        else:
            normalized_data = data

        if db_type == 'rack' and 'QUANTITY' in normalized_data:
            parsed_quantity = ss_normalization._strict_inventory_quantity(normalized_data['QUANTITY'])
            if parsed_quantity is None or parsed_quantity < 0:
                return jsonify({
                    'success': False,
                    'error': 'Quantity must be a whole number of 0 or more; 0 archives and removes the row'
                }), 400
            normalized_data['QUANTITY'] = parsed_quantity
        
        tracker_upc_key = None
        tracker_prev_total = None

        # If updating quantity in searchRack, log to removed_items for history
        if db_type == 'rack' and 'QUANTITY' in normalized_data:
            try:
                # Get old quantity and item details
                cur.execute('SELECT QUANTITY, BARCODE, TITLE, ITEM_POSITION FROM SEARCHRACK WHERE ID = ?', (item_id,))
                old_row = cur.fetchone()
                if old_row:
                    old_qty, barcode, title, item_location = old_row
                    old_qty = ss_normalization._coerce_int(old_qty, 0)
                    new_qty = ss_normalization._coerce_int(normalized_data['QUANTITY'], old_qty)
                    qty_change = new_qty - old_qty
                    tracker_upc_key = ss_listing_alerts._listing_alert_upc_key(barcode)
                    if tracker_upc_key:
                        tracker_prev_total = ss_warehouse_matching._searchrack_total_qty_for_key(conn, tracker_upc_key)
                    
                    print(f"📝 Manual edit detected: {title} (ID: {item_id}) - Qty change: {old_qty} → {new_qty} (change: {qty_change})")
                    
                    if qty_change == 0:
                        print(f"⚠️ Quantity unchanged, not logging to history")
                    else:
                        # The SEARCHRACK trigger records this in the same transaction as the update.
                        print(f"✅ Manual edit queued in durable rack-history outbox: {title}")
            except Exception as log_err:
                print(f"❌ Error logging quantity change to removed_items: {log_err}")
                import traceback
                traceback.print_exc()
        
        # Build update query using normalized data
        set_clause = ', '.join([f'"{k}"=?' for k in normalized_data.keys() if k in columns])
        values = [v for k, v in normalized_data.items() if k in columns]
        values.append(item_id)
        
        if not set_clause:
            return jsonify({'success': False, 'error': 'No valid fields to update'}), 400
        
        query = f'UPDATE {"SEARCHRACK" if db_type == "rack" else "raw_bol_items"} SET {set_clause} WHERE id=?'
        cur.execute(query, values)
        conn.commit()

        if db_type == 'rack' and tracker_upc_key and tracker_prev_total is not None:
            try:
                tracker_current_total = ss_warehouse_matching._searchrack_total_qty_for_key(conn, tracker_upc_key)
                ss_inventory_history._record_inventory_zero_transition(tracker_upc_key, tracker_prev_total, tracker_current_total)
            except Exception as tracker_err:
                print(f"Warning: zero-transition write tracking failed in update_item: {tracker_err}")

        if db_type == 'rack':
            ss_caching._invalidate_searchrack_cache()
        
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': ss_errors._safe_error(e)}), 500
    finally:
        if conn is not None:
            conn.close()


def api_search_db(db_key):
    data = request.get_json() or {}
    q = (data.get('q') or '').strip()
    print(f"DEBUG: api_search_db hit. db={db_key}, q='{q}'")
    # Strip leading zeros from barcode searches
    q_stripped = ss_normalization._strip_leading_zeros_numeric(q)
    # Accept limit from client. If limit is 0 or None, we will NOT apply a SQL LIMIT (i.e., return all rows).
    limit_raw = data.get('limit')
    try:
        limit = int(limit_raw) if limit_raw is not None else 50
    except Exception:
        limit = 50
    try:
        print(f"[SEARCH DEBUG] db_key={db_key}, query={q}, query_stripped={q_stripped}")
        mapping = {
            'ebayStore': 'ebayStore.db',
            'amazonStore': 'amazonStore.db',
            'sold': 'sold.db',
            'returns': 'sold.db',  # Returns table in sold.db
            'searchRack': 'searchRack.db',
            'found': 'found.db',
            'bol': 'rawbol.db'  # Search rawbol.db to see original imported amounts across all LOTs
        }
        if db_key not in mapping:
            return jsonify({'error': 'Unknown db_key'}), 400
        db_path = mapping[db_key]
        # Ensure created_at and custom_title exist for searchRack
        if db_key == 'searchRack':
            conn_m = None
            try:
                conn_m = sqlite3.connect(db_path)
                cur_m = conn_m.cursor()
                cur_m.execute("PRAGMA table_info(SEARCHRACK)")
                cols_m = [r[1] for r in cur_m.fetchall()]
                schema_changed = False
                if 'CREATED_AT' not in cols_m:
                    cur_m.execute('ALTER TABLE SEARCHRACK ADD COLUMN CREATED_AT TEXT')
                    schema_changed = True
                if 'CUSTOM_TITLE' not in cols_m:
                    cur_m.execute('ALTER TABLE SEARCHRACK ADD COLUMN CUSTOM_TITLE INTEGER DEFAULT 0')
                    schema_changed = True
                if 'WAREHOUSE_NOTE' not in cols_m:
                    cur_m.execute('ALTER TABLE SEARCHRACK ADD COLUMN WAREHOUSE_NOTE TEXT DEFAULT ""')
                    schema_changed = True
                # Set CREATED_AT for any missing rows to current UTC so timestamps appear
                import datetime as _dt
                now_iso = _dt.datetime.now(_dt.UTC).isoformat()
                cur_m.execute("UPDATE SEARCHRACK SET CREATED_AT = ? WHERE CREATED_AT IS NULL OR TRIM(COALESCE(CREATED_AT,'')) = ''", (now_iso,))
                conn_m.commit()
                if schema_changed:
                    ss_inventory_history._install_searchrack_history_guard(conn_m)
                ss_inventory_age._reconcile_inventory_age_batches(conn_m)
            except Exception:
                # Don't block search if migration fails
                pass
            finally:
                if conn_m is not None:
                    conn_m.close()
        
        # Use cached connection for better performance
        conn = ss_database.get_db_connection(db_path)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")
        tables = [r[0] for r in cur.fetchall()]
        if not tables:
            conn.close()
            return jsonify({'results': []})
        prefer = None
        # Special case for returns: use returns table
        if db_key == 'returns':
            prefer = 'returns' if 'returns' in tables else None
        else:
            for t in ['orders','INVENTORY','SEARCHRACK','searchrack','rack','items','bol_items']:
                if t in tables:
                    prefer = t
                    break
        table = prefer or tables[0]
        print(f"[SEARCH DEBUG] db_key={db_key}, db_path={db_path}, table={table}, tables={tables}")
        cur.execute(f"PRAGMA table_info('{table}')")
        cols = [r[1] for r in cur.fetchall()]
        print(f"[SEARCH DEBUG] columns={cols}")
        where_clause = ''
        params = []
        # Accept optional location filter (from client UI) to search Item_Position specifically
        location = (data.get('location') or '').strip()
        # Accept optional LOT filter for BOL database
        lot_filter = (data.get('lot_filter') or '').strip() if db_key == 'bol' else ''
        # Accept optional location_group_id for filtering by shelf group (from location browser)
        location_group_id = data.get('location_group_id')
        # Accept optional custom_only filter for searchRack (user-filled title/image)
        custom_only = bool(data.get('custom_only')) if db_key == 'searchRack' else False
        
        if q_stripped:
            tokens = []
            seen_tokens = set()
            for part in q_stripped.split():
                token = str(part or '').strip()
                if not token:
                    continue
                token_key = token.lower()
                if token_key in seen_tokens:
                    continue
                seen_tokens.add(token_key)
                tokens.append(token)
            if not tokens:
                tokens = [q_stripped]

            if db_key == 'searchRack':
                title_col = next((c for c in cols if c.lower() == 'title'), None)
                barcode_col = next((c for c in cols if c.lower() in ('barcode', 'upc')), None)
                item_id_col = next((c for c in cols if c.lower() in ('itemid', 'item_id', 'sku')), None)
                item_position_col = next((c for c in cols if c.lower() in ('item_position', 'itemposition', 'position')), None)
                picture_position_col = next((c for c in cols if c.lower() in ('pictureposition', 'picture_position')), None)

                disjuncts = []

                if title_col:
                    title_col_sql = '"' + str(title_col).replace('"', '""') + '"'
                    title_parts = [f"LOWER(COALESCE({title_col_sql},'')) LIKE ?" for _ in tokens]
                    disjuncts.append('(' + ' AND '.join(title_parts) + ')')
                    params.extend(f"%{token.lower()}%" for token in tokens)

                aux_parts = []
                aux_params = []
                for col in [barcode_col, item_id_col]:
                    if not col:
                        continue
                    col_sql = '"' + str(col).replace('"', '""') + '"'
                    aux_parts.append(f"LOWER(COALESCE({col_sql},'')) LIKE ?")
                    aux_params.append(f"%{q_stripped.lower()}%")

                location_parts = []
                location_params = []
                for col in [item_position_col, picture_position_col]:
                    if not col:
                        continue
                    col_sql = '"' + str(col).replace('"', '""') + '"'
                    # Exact match for position columns — avoids "h1" matching h10, h11, etc.
                    location_parts.append(f"LOWER(TRIM(COALESCE({col_sql},''))) = ?")
                    location_params.append(q_stripped.lower())

                if aux_parts:
                    disjuncts.append('(' + ' OR '.join(aux_parts) + ')')
                    params.extend(aux_params)

                if location_parts:
                    disjuncts.append('(' + ' OR '.join(location_parts) + ')')
                    params.extend(location_params)

                if disjuncts:
                    where_clause = ' WHERE ' + ' OR '.join(disjuncts)
                else:
                    likes = []
                    for c in cols:
                        col_sql = '"' + str(c).replace('"', '""') + '"'
                        likes.append(f"LOWER(COALESCE({col_sql},'')) LIKE ?")
                        params.append(f"%{q_stripped.lower()}%")
                    where_clause = ' WHERE ' + ' OR '.join(likes)
            else:
                likes = []
                for c in cols:
                    col_sql = '"' + str(c).replace('"', '""') + '"'
                    likes.append(f"LOWER(COALESCE({col_sql},'')) LIKE ?")
                    params.append(f"%{q_stripped.lower()}%")
                where_clause = ' WHERE ' + ' OR '.join(likes)
        
        # If a specific LOT was provided (BOL database only), add filter
        if db_key == 'bol' and lot_filter:
            lot_condition = "lot_number = ?"
            params.append(lot_filter)
            if where_clause:
                where_clause += f' AND {lot_condition}'
            else:
                where_clause = f' WHERE {lot_condition}'
        
        # If a specific location was provided, add an AND clause to filter by item position columns
        if location:
            # try common column names for location
            loc_cols = [c for c in cols if c.lower() in ('item_position','itemposition','position','item_position')]
            if not loc_cols:
                # fallback to any column that looks like position
                loc_cols = [c for c in cols if 'position' in c.lower()]
            if loc_cols:
                loc_likes = []
                for lc in loc_cols:
                    loc_likes.append(f"LOWER(COALESCE({lc},'')) LIKE ?")
                    params.append(f"%{location.lower()}%")
                if where_clause:
                    where_clause += ' AND (' + ' OR '.join(loc_likes) + ')'
                else:
                    where_clause = ' WHERE ' + ' OR '.join(loc_likes)
        
        # If location_group_id is provided (searchRack only), filter by all shelves in that group
        if db_key == 'searchRack' and location_group_id:
            group_conn = None
            try:
                # Get all shelf codes in this group
                group_conn = sqlite3.connect(str(ss_config.BASE_DIR / 'searchRack.db'))
                group_cur = group_conn.cursor()
                group_cur.execute('SELECT shelf_name FROM shelves WHERE group_id = ?', (location_group_id,))
                shelf_codes = [row[0] for row in group_cur.fetchall()]

                if shelf_codes:
                    # Find item_position column
                    loc_cols = [c for c in cols if c.lower() in ('item_position','itemposition','position')]
                    if not loc_cols:
                        loc_cols = [c for c in cols if 'position' in c.lower()]

                    if loc_cols:
                        loc_col = loc_cols[0]
                        # Build OR condition for all shelves in group
                        shelf_conditions = []
                        for shelf_code in shelf_codes:
                            shelf_conditions.append(f"LOWER(TRIM({loc_col})) = LOWER(TRIM(?))")
                            params.append(shelf_code)

                        if where_clause:
                            where_clause += ' AND (' + ' OR '.join(shelf_conditions) + ')'
                        else:
                            where_clause = ' WHERE (' + ' OR '.join(shelf_conditions) + ')'
            except Exception as e:
                print(f"[SEARCH DEBUG] Error filtering by location_group_id: {e}")
            finally:
                if group_conn:
                    group_conn.close()
        
        # If custom_only is set, filter to items with user-filled title or custom image
        if custom_only:
            custom_cond = "(COALESCE(CUSTOM_TITLE, 0) = 1 OR COALESCE(IMAGE, '') LIKE '/static/custom_items/%')"
            if where_clause:
                # The text search is a series of OR expressions. Group all of
                # them before applying custom_only so a title/barcode match
                # cannot bypass the Custom Items filter through SQL precedence.
                where_clause = f" WHERE ({where_clause[7:]}) AND {custom_cond}"
            else:
                where_clause = f' WHERE {custom_cond}'

        if db_key == 'searchRack':
            active_qty_col = next((column for column in cols if column.lower() in ('quantity', 'qty')), None)
            if active_qty_col:
                active_condition = (
                    f'COALESCE(CAST({ss_database._sqlite_ident(active_qty_col)} AS INTEGER), 0) > 0'
                )
                if where_clause:
                    where_clause = f" WHERE ({where_clause[7:]}) AND {active_condition}"
                else:
                    where_clause = f' WHERE {active_condition}'

        # compute total matching count for pagination
        count_sql = f"SELECT COUNT(*) FROM {table} {where_clause}"
        print(f"[SEARCH DEBUG] count_sql={count_sql}, params={params}")
        cur.execute(count_sql, params)
        total_count = cur.fetchone()[0]
        print(f"[SEARCH DEBUG] total_count={total_count}")

        # If limit <= 0, return all rows; otherwise apply LIMIT/OFFSET
        offset = int(data.get('offset') or 0)
        if limit and int(limit) > 0:
            sql = f"SELECT * FROM {table} {where_clause} LIMIT ? OFFSET ?"
            exec_params = params + [limit, offset]
            cur.execute(sql, exec_params)
        else:
            sql = f"SELECT * FROM {table} {where_clause}"
            cur.execute(sql, params)
        rows = [dict(r) for r in cur.fetchall()]

        # Pre-load enrichment data in batch for searchRack results (avoids N+1 queries)
        _enrichment_cache = {}
        _inventory_age_cache = {}
        if db_key == 'searchRack' and rows:
            _inventory_age_cache = ss_inventory_history._inventory_age_lookup(
                conn,
                [r.get('ID') if r.get('ID') is not None else r.get('id') for r in rows]
            )
        if db_key == 'searchRack' and rows:
            # Collect all barcodes that need enrichment
            _barcodes_to_enrich = set()
            for r in rows:
                bc = r.get('BARCODE') or r.get('barcode') or r.get('Barcode') or ''
                if bc:
                    base = str(bc).split('-')[0]
                    base = ss_normalization._strip_leading_zeros_numeric(base)
                    if base:
                        _barcodes_to_enrich.add(base)
            if _barcodes_to_enrich:
                from DBmanager import connect_db
                placeholders = ','.join('?' for _ in _barcodes_to_enrich)
                bc_tuple = tuple(_barcodes_to_enrich)
                # Batch lookup: ebayStore
                try:
                    with connect_db('ebayStore.db') as _ec:
                        _ec.row_factory = sqlite3.Row
                        for r in _ec.cursor().execute(f"SELECT Title, Image, ItemID, UPC FROM INVENTORY WHERE UPC IN ({placeholders}) COLLATE NOCASE", bc_tuple):
                            upc_key = ss_normalization._strip_leading_zeros_numeric(r['UPC'] or '')
                            _enrichment_cache.setdefault(upc_key, {}).update({'title': r['Title'], 'image': r['Image'], 'item_id': r['ItemID']})
                except Exception:
                    pass
                # Batch lookup: amazonStore
                try:
                    with connect_db('amazonStore.db') as _ac:
                        _ac.row_factory = sqlite3.Row
                        for r in _ac.cursor().execute(f"SELECT TITLE, IMAGE, ASIN, UPC FROM ITEMS WHERE UPC IN ({placeholders}) COLLATE NOCASE", bc_tuple):
                            upc_key = ss_normalization._strip_leading_zeros_numeric(r['UPC'] or '')
                            cached = _enrichment_cache.setdefault(upc_key, {})
                            cached.setdefault('title', r['TITLE'])
                            cached.setdefault('image', r['IMAGE'])
                            cached.setdefault('item_id', r['ASIN'])
                except Exception:
                    pass
                # Batch lookup: rawbol
                try:
                    with connect_db('rawbol.db') as _rc:
                        _rc.row_factory = sqlite3.Row
                        for r in _rc.cursor().execute(f"SELECT item_description, image_url, upc FROM raw_bol_items WHERE upc IN ({placeholders}) COLLATE NOCASE", bc_tuple):
                            upc_key = ss_normalization._strip_leading_zeros_numeric(r['upc'] or '')
                            cached = _enrichment_cache.setdefault(upc_key, {})
                            cached.setdefault('title', r['item_description'])
                            cached.setdefault('image', r['image_url'])
                            cached.setdefault('item_id', r['upc'])
                except Exception:
                    pass
                # Batch lookup: bol
                try:
                    with connect_db('bol.db') as _bc:
                        _bc.row_factory = sqlite3.Row
                        for r in _bc.cursor().execute(f"SELECT item_description, image_url, upc FROM bol_items WHERE upc IN ({placeholders}) COLLATE NOCASE", bc_tuple):
                            upc_key = ss_normalization._strip_leading_zeros_numeric(r['upc'] or '')
                            cached = _enrichment_cache.setdefault(upc_key, {})
                            cached.setdefault('title', r['item_description'])
                            cached.setdefault('image', r['image_url'])
                            cached.setdefault('item_id', r['upc'])
                except Exception:
                    pass

        results = []
        for r in rows:
            item = dict(r)
            # default mappings
            if db_key == 'ebayStore':
                barcode_val = item.get('UPC') or item.get('upc') or item.get('BARCODE') or item.get('barcode') or item.get('Barcode') or ''
            elif db_key == 'amazonStore':
                barcode_val = item.get('UPC') or item.get('upc') or item.get('BARCODE') or item.get('barcode') or item.get('Barcode') or ''
            elif db_key == 'sold':
                # Be robust to all case variants and explicit barcode field
                barcode_val = item.get('barcode') or item.get('BARCODE') or item.get('Barcode') or item.get('upc') or item.get('UPC') or ''
            else:
                barcode_val = item.get('BARCODE') or item.get('barcode') or item.get('Barcode') or ''

            # bol.db normalization: different column names (item_description, upc, image_url)
            title_val = item.get('Title') or item.get('title') or item.get('TITLE') or item.get('name') or item.get('Name') or ''
            if db_key == 'sold':
                image_val = item.get('image') or item.get('Image') or item.get('IMAGE') or item.get('image_url') or item.get('images') or ''
            elif db_key == 'amazonStore':
                # amazonStore uses all-caps IMAGE column
                image_val = item.get('IMAGE') or item.get('Image') or item.get('image') or item.get('image_url') or item.get('images') or ''
            else:
                image_val = item.get('Image') or item.get('image') or item.get('IMAGE') or item.get('image_url') or item.get('images') or ''
            if db_key == 'bol':
                # barcode from upc field
                b = item.get('upc') or item.get('UPC') or item.get('Upc') or barcode_val
                # normalize numeric-like UPCs (e.g., '16094950.0') to '16094950'
                if isinstance(b, float) or (isinstance(b, str) and b.endswith('.0') and b.replace('.0','').isdigit()):
                    try:
                        b = str(int(float(b)))
                    except Exception:
                        b = str(b)
                elif b is None:
                    b = ''
                else:
                    b = str(b)
                barcode_val = b

                # title from item_description or description
                t = item.get('item_description') or item.get('description') or title_val
                if not t:
                    # fallback: pick the longest non-URL text field from the row
                    longest = ''
                    for v in item.values():
                        try:
                            s = str(v or '')
                        except Exception:
                            continue
                        if s and not s.lower().startswith('http') and len(s) > len(longest):
                            longest = s
                    t = longest
                title_val = t or ''

                # image from image_url if present
                image_val = item.get('image_url') or item.get('image') or image_val or ''

            # Quantity extraction that properly handles 0 values
            qty_raw = None
            for qkey in ['QUANTITY', 'Quantity', 'quantity', 'qty']:
                if qkey in item and item[qkey] is not None:
                    qty_raw = item[qkey]
                    if db_key == 'searchRack':
                        print(f"[QTY EXTRACT] Found {qkey}={qty_raw}, type={type(qty_raw)}, barcode={item.get('BARCODE') or item.get('barcode')}")
                    break
            if qty_raw is None:
                qty_raw = ''
                if db_key == 'searchRack':
                    print(f"[QTY EXTRACT] No quantity found, defaulting to empty, barcode={item.get('BARCODE') or item.get('barcode')}")
            
            # Debug logging for zero quantity items
            if qty_raw == 0 and db_key == 'searchRack':
                print(f"[DEBUG] Zero quantity item found: barcode={barcode_val}, qty_raw={qty_raw}, type={type(qty_raw)}")
            
            item_out = {
                'source_db': db_key,
                'source_table': table,
                'id': item.get('id') or item.get('ID') or item.get('rowid'),
                'title': title_val,
                'image': image_val,
                'barcode': barcode_val,
                'item_id': item.get('ItemID') or item.get('item_id') or item.get('ItemId') or item.get('ASIN') or item.get('asin') or (barcode_val if barcode_val else ''),
                'pictureposition': item.get('PICTUREPOSITION') or item.get('pictureposition') or item.get('picture_position') or '',
                'item_position': item.get('ITEM_POSITION') or item.get('item_position') or item.get('position') or '',
                'quantity': qty_raw,
                'warehouse_note': item.get('WAREHOUSE_NOTE') or item.get('warehouse_note') or '',
                'is_custom_title': bool(ss_normalization._coerce_int(item.get('CUSTOM_TITLE') if item.get('CUSTOM_TITLE') is not None else item.get('custom_title'), 0)) if db_key == 'searchRack' else False,
                # created_at available on SEARCHRACK rows populated by DBmanager
                'created_at': item.get('CREATED_AT') or item.get('created_at') or '',
                # store field for returns (amazon/ebay)
                'store': item.get('store') or item.get('Store') or item.get('STORE') or '',
                'raw': item
            }
            if db_key == 'searchRack':
                age_row_id = item.get('ID') if item.get('ID') is not None else item.get('id')
                try:
                    age_row_id = int(age_row_id)
                except Exception:
                    age_row_id = None
                age_batches = list(_inventory_age_cache.get(age_row_id, []))
                item_out['age_batches'] = age_batches
                item_out['oldest_received_at'] = (
                    age_batches[0].get('received_at') if age_batches else item_out['created_at']
                )
            # If this row comes from searchRack, enrich from pre-loaded batch cache
            try:
                if db_key == 'searchRack' and item_out.get('barcode'):
                    lookup_barcode = item_out.get('barcode')
                    base_barcode = str(lookup_barcode).split('-')[0] if lookup_barcode else lookup_barcode
                    base_barcode = ss_normalization._strip_leading_zeros_numeric(base_barcode)
                    cached = _enrichment_cache.get(base_barcode, {})
                    if cached:
                        cached_title = str(cached.get('title') or '').strip()
                        current_title = str(item_out.get('title') or '').strip()
                        if item_out.get('is_custom_title'):
                            item_out['custom_title'] = current_title
                            if cached_title and cached_title.casefold() != current_title.casefold():
                                item_out['database_title'] = cached_title
                        item_out['title'] = item_out.get('title') or cached.get('title')
                        item_out['image'] = item_out.get('image') or cached.get('image')
                        item_out['item_id'] = item_out.get('item_id') or cached.get('item_id')
                # If this row comes from sold.db, the data should already be enriched during sync
                # but we can still do a fallback enrichment if needed
                elif db_key == 'sold':
                    # If barcode is missing, try to get it from ebayStore.db
                    if not item_out.get('barcode'):
                        lookup_item_id = item_out.get('item_id')
                        if lookup_item_id:
                            es_conn = None
                            try:
                                es_conn = sqlite3.connect('ebayStore.db')
                                es_conn.row_factory = sqlite3.Row
                                es_cur = es_conn.cursor()
                                es_cur.execute("SELECT UPC FROM INVENTORY WHERE ItemID = ? LIMIT 1", (lookup_item_id,))
                                row_es = es_cur.fetchone()
                                if row_es and row_es['UPC']:
                                    item_out['barcode'] = row_es['UPC']
                            except Exception as e:
                                print(f"Debug: sold barcode lookup error: {e}")
                                pass
                            finally:
                                if es_conn is not None:
                                    es_conn.close()
                    
                    # If title or image is missing, try to get from rawbol.db using barcode
                    if item_out.get('barcode') and (not item_out.get('title') or not item_out.get('image')):
                        bol_conn = None
                        try:
                            bol_conn = sqlite3.connect('rawbol.db')
                            bol_conn.row_factory = sqlite3.Row
                            bol_cur = bol_conn.cursor()

                            barcode_to_lookup = item_out['barcode']
                            # Normalize barcode (strip .0 artifacts, ensure string)
                            if barcode_to_lookup is None:
                                barcode_to_lookup = ''
                            elif isinstance(barcode_to_lookup, float):
                                try:
                                    barcode_to_lookup = str(int(barcode_to_lookup))
                                except Exception:
                                    barcode_to_lookup = str(barcode_to_lookup)
                            else:
                                barcode_to_lookup = str(barcode_to_lookup).strip()
                                if barcode_to_lookup.endswith('.0') and barcode_to_lookup.replace('.0', '').isdigit():
                                    barcode_to_lookup = barcode_to_lookup[:-2]
                            barcode_stripped = ss_normalization._strip_leading_zeros_numeric(barcode_to_lookup)
                            row_bol = None

                            # Try exact match first
                            bol_cur.execute('SELECT item_description, image_url FROM raw_bol_items WHERE upc = ? COLLATE NOCASE LIMIT 1', (barcode_to_lookup,))
                            row_bol = bol_cur.fetchone()

                            # Try stripped zeros if no match
                            if not row_bol and barcode_to_lookup.isdigit():
                                stripped = barcode_stripped
                                if stripped != barcode_to_lookup:
                                    bol_cur.execute('SELECT item_description, image_url FROM raw_bol_items WHERE upc = ? COLLATE NOCASE LIMIT 1', (stripped,))
                                    row_bol = bol_cur.fetchone()

                            # Try padded to 12 digits if no match
                            if not row_bol and barcode_to_lookup.isdigit():
                                padded = barcode_to_lookup.zfill(12)
                                if padded != barcode_to_lookup:
                                    bol_cur.execute('SELECT item_description, image_url FROM raw_bol_items WHERE upc = ? COLLATE NOCASE LIMIT 1', (padded,))
                                    row_bol = bol_cur.fetchone()

                            # Try padded to 13 digits (EAN) if no match
                            if not row_bol and barcode_to_lookup.isdigit():
                                padded13 = barcode_to_lookup.zfill(13)
                                if padded13 != barcode_to_lookup:
                                    bol_cur.execute('SELECT item_description, image_url FROM raw_bol_items WHERE upc = ? COLLATE NOCASE LIMIT 1', (padded13,))
                                    row_bol = bol_cur.fetchone()

                            # Try suffix match (rawbol UPC has extra leading zeros)
                            if not row_bol and barcode_stripped and barcode_stripped.isdigit() and len(barcode_stripped) >= 8:
                                bol_cur.execute("SELECT item_description, image_url FROM raw_bol_items WHERE REPLACE(upc, '.0', '') LIKE ? COLLATE NOCASE LIMIT 1", ('%' + barcode_stripped,))
                                row_bol = bol_cur.fetchone()

                            # Try integer-cast comparison to normalize leading zeros
                            if not row_bol and barcode_stripped and barcode_stripped.isdigit() and len(barcode_stripped) >= 8:
                                bol_cur.execute("""
                                    SELECT item_description, image_url FROM raw_bol_items
                                    WHERE CAST(CAST(REPLACE(upc, '.0', '') AS INTEGER) AS TEXT) = ?
                                    COLLATE NOCASE LIMIT 1
                                """, (barcode_stripped,))
                                row_bol = bol_cur.fetchone()

                            if row_bol:
                                if not item_out.get('title') and row_bol['item_description']:
                                    item_out['title'] = row_bol['item_description']
                                if not item_out.get('image') and row_bol['image_url']:
                                    item_out['image'] = row_bol['image_url']
                        except Exception as e:
                            print(f"Debug: rawbol enrichment error: {e}")
                            pass
                        finally:
                            if bol_conn is not None:
                                bol_conn.close()

                    # Compute inv_status for sold items
                    try:
                        rackupdated_val = ss_normalization._coerce_int(item.get('rackupdated'), 0)
                        order_id_val = str(item.get('order_id') or '').strip()
                        barcode_val = str(item.get('barcode') or '').strip()
                        has_removal_record = False
                        if order_id_val or barcode_val:
                            try:
                                hist_conn = sqlite3.connect('rackhistory.db')
                                hist_cur = hist_conn.cursor()
                                hist_cur.execute("""
                                    SELECT removed_at
                                    FROM removed_items
                                    WHERE (
                                        (? != '' AND COALESCE(order_id, '') = ?)
                                        OR
                                        (? != '' AND COALESCE(barcode, '') = ?)
                                    )
                                    AND removal_type IN (
                                        'automatic',
                                        'automatic_allocated',
                                        'repair_removal',
                                        'manual_sold_removal',
                                        'manual_immediate',
                                        'manual_sold_selection',
                                        'manual_handled',
                                        'finder_removal'
                                    )
                                    AND COALESCE(old_quantity, 0) > COALESCE(new_quantity, 0)
                                    AND (undone_at IS NULL OR undone_at = '')
                                """, (order_id_val, order_id_val, barcode_val, barcode_val))
                                has_removal_record = any(
                                    ss_normalization._sold_removal_event_is_current(item, {'removed_at': row[0]})
                                    for row in hist_cur.fetchall()
                                )
                                hist_conn.close()
                            except Exception:
                                pass

                        sale_time_known = ss_normalization._sold_order_sale_datetime(item) is not None
                        if has_removal_record or (rackupdated_val == 1 and not sale_time_known):
                            item_out['inv_status'] = 'COMPLETE'
                        else:
                            item_out['inv_status'] = 'NOT_REMOVED'
                    except Exception as e:
                        print(f"Debug: inv_status error: {e}")
                        item_out['inv_status'] = 'NOT_REMOVED'
            except Exception:
                pass
            results.append(item_out)
        
        # Note: Not closing connection - using connection pooling for performance
        
        # If searching bol (rawbol.db), aggregate by UPC and handle multiple LOTs
        if db_key == 'bol' and results:
            from collections import defaultdict
            import datetime as _dt
            
            # Group by UPC
            upc_groups = defaultdict(list)
            for r in results:
                upc = (r.get('barcode') or '').strip()
                if upc:
                    upc_groups[upc].append(r)
            
            aggregated = []
            for upc, items in upc_groups.items():
                # Extract LOT information from all items with this UPC
                lot_data = []
                total_qty = 0
                
                # Use first item as base for title, image, etc.
                base_item = items[0].copy()
                
                for item in items:
                    qty = item.get('quantity') or item.get('raw', {}).get('quantity') or 1
                    try:
                        qty = int(qty)
                    except (ValueError, TypeError):
                        qty = 1
                    
                    total_qty += qty
                    
                    lot_num = item.get('raw', {}).get('lot_number') or ''
                    lot_num = str(lot_num).strip() if lot_num else ''
                    if not lot_num or lot_num.lower() in ['nan', 'none', 'null', '']:
                        lot_num = '(No LOT)'
                    
                    import_date = item.get('raw', {}).get('import_date') or ''
                    if not import_date or str(import_date).strip().lower() in ['nan', 'none', 'null', '']:
                        import_date = '(No Date)'
                    
                    lot_data.append({
                        'lot_number': lot_num,
                        'qty': qty,
                        'import_date': import_date
                    })
                
                # Sort lots by import_date (newest first), handling "(No Date)" specially
                def parse_date(date_str):
                    if date_str == '(No Date)':
                        return _dt.datetime.min
                    try:
                        # Try parsing YYYY-MM-DD format
                        return _dt.datetime.strptime(str(date_str).split()[0], '%Y-%m-%d')
                    except Exception:
                        try:
                            # Try ISO format
                            return _dt.datetime.fromisoformat(str(date_str).replace('Z', '+00:00'))
                        except Exception:
                            return _dt.datetime.min
                
                lot_data.sort(key=lambda x: parse_date(x['import_date']), reverse=True)
                
                # Determine LOT display
                unique_lots = list(set([ld['lot_number'] for ld in lot_data]))
                unique_lots = [lot for lot in unique_lots if lot != '(No LOT)']
                
                if len(unique_lots) == 0:
                    lot_display = '(No LOT)'
                    has_multiple = False
                elif len(unique_lots) == 1:
                    lot_display = unique_lots[0]
                    has_multiple = False
                else:
                    lot_display = 'multiple LOTS'
                    has_multiple = True
                
                # Build aggregated item
                aggregated_item = {
                    'source_db': 'bol',
                    'source_table': base_item.get('source_table'),
                    'id': base_item.get('id'),
                    'title': base_item.get('title'),
                    'image': base_item.get('image'),
                    'barcode': upc,
                    'item_id': base_item.get('item_id'),
                    'quantity': total_qty,
                    'lot_number': lot_display,
                    'has_multiple_lots': has_multiple,
                    'lot_data': lot_data,  # Array of {lot_number, qty, import_date}
                    'raw': base_item.get('raw')
                }
                
                aggregated.append(aggregated_item)
            
            results = aggregated
            total_count = len(results)
        
        # If searching the searchRack snapshot, merge rows with same barcode+item_position and sum quantities
        # BUT: picture position items should NEVER be merged (each picture is unique)
        if db_key == 'searchRack' and results:
            merged = {}
            for r in results:
                bc = (r.get('barcode') or '').strip()
                pic = (r.get('pictureposition') or '').strip()
                
                # Picture position items are ALWAYS unique - use pictureposition + row ID for key
                if pic:
                    # Each picture position gets unique key (never merge)
                    key = f"{bc.lower()}||pic||{pic.lower()}||{r.get('id')}"
                else:
                    # Shelf code items: merge by barcode + item_position
                    pos = (r.get('item_position') or '').strip()
                    if not bc:
                        key = f"__{id(r)}_{len(merged)}"
                    else:
                        key = f"{bc.lower()}||shelf||{pos.lower()}"
                
                if key not in merged:
                    merged[key] = r.copy()
                    # normalize quantity - handle 0 values properly
                    qty_val = r.get('quantity')
                    if qty_val is None or qty_val == '':
                        merged[key]['quantity'] = 1
                    elif isinstance(qty_val, (int, float)):
                        merged[key]['quantity'] = int(qty_val)
                    elif str(qty_val).isdigit():
                        merged[key]['quantity'] = int(qty_val)
                    else:
                        merged[key]['quantity'] = 1
                else:
                    qty_val = r.get('quantity')
                    if qty_val is None or qty_val == '':
                        add_q = 1
                    elif isinstance(qty_val, (int, float)):
                        add_q = int(qty_val)
                    elif str(qty_val).isdigit():
                        add_q = int(qty_val)
                    else:
                        add_q = 1
                    merged[key]['quantity'] = merged[key].get('quantity', 0) + add_q
                    merged[key].setdefault('age_batches', []).extend(r.get('age_batches') or [])

            for merged_row in merged.values():
                grouped_batches = {}
                for batch in merged_row.get('age_batches') or []:
                    received_at = str(batch.get('received_at') or '').strip()
                    if not received_at:
                        continue
                    estimated = bool(batch.get('estimated'))
                    batch_key = (received_at, estimated)
                    grouped_batches[batch_key] = grouped_batches.get(batch_key, 0) + max(
                        0, ss_normalization._coerce_int(batch.get('quantity'), 0)
                    )
                merged_row['age_batches'] = [
                    {
                        'received_at': received_at,
                        'quantity': quantity,
                        'estimated': estimated,
                    }
                    for (received_at, estimated), quantity in sorted(
                        grouped_batches.items(), key=lambda entry: entry[0][0]
                    )
                    if quantity > 0
                ]
                merged_row['oldest_received_at'] = (
                    merged_row['age_batches'][0]['received_at']
                    if merged_row['age_batches']
                    else merged_row.get('created_at', '')
                )
            results = list(merged.values())
            # adjust total_count to reflect merged items count
            total_count = len(results)

            prep_lookup = ss_prep_context._load_suffixed_item_prep_summary_lookup(
                row.get('barcode') for row in results
            )
            for row in results:
                prep_key = ss_normalization._normalize_upc_preserve_suffix_for_match(row.get('barcode'))
                row['item_prep_summary'] = prep_lookup.get(
                    prep_key,
                    ss_prep_context._empty_suffixed_item_prep_summary(prep_key)
                )

        # If client requested debug info, return table/schema/samples plus normalized results
        if data.get('debug'):
            debug_samples = rows[:10] if isinstance(rows, list) else []
            return jsonify({
                'debug': True,
                'db_key': db_key,
                'db_path': db_path,
                'table': table,
                'columns': cols,
                'samples_raw': debug_samples,
                'results': results,
                'total': total_count
            })

        # Calculate total quantity for all results
        total_quantity = 0
        for r in results:
            try:
                qty = r.get('quantity', 0)
                if isinstance(qty, (int, float)):
                    total_quantity += int(qty)
                elif isinstance(qty, str) and qty.isdigit():
                    total_quantity += int(qty)
                else:
                    total_quantity += 1
            except Exception:
                total_quantity += 1

        # Debug: Check what quantities are in results before sending
        if db_key == 'searchRack':
            for r in results:
                if r.get('barcode') == '882864825810':
                    print(f"[BEFORE JSONIFY] barcode={r.get('barcode')}, quantity={r.get('quantity')}, type={type(r.get('quantity'))}")

        return jsonify({'results': results, 'total': total_count, 'total_quantity': total_quantity})
    except Exception as e:
        return jsonify({'error': ss_errors._safe_error(e)}), 500


_search_all_cache = {}


_search_cache_timeout = 300  # 5 minutes


def api_search_all():
    """Search across all databases simultaneously with cached quick lookups for UPCs"""
    try:
        data = request.get_json() or {}
        query = (data.get('q') or '').strip()
        
        if not query:
            return jsonify({'error': 'No search query provided'}), 400
        
        # Strip leading zeros for numeric barcode searches
        query_stripped = ss_normalization._strip_leading_zeros_numeric(query)
        query_upc = ss_normalization._normalize_upc_preserve_suffix_for_match(query)

        # Determine search type (UPC searches are cached)
        is_upc_search = bool(query_upc and re.fullmatch(r'\d+(?:-\d+)?', query_upc))
        
        # Check cache for UPC searches only
        cache_key = f"search_all:v3:{query_upc or query_stripped}"
        if is_upc_search and cache_key in _search_all_cache:
            cached_data, cached_time = _search_all_cache[cache_key]
            if time.time() - cached_time < _search_cache_timeout:
                cached_data['from_cache'] = True
                return jsonify(cached_data)
        
        # Define databases to search with display info (ordered: warehouse, item-manager, Macy BOL, sold, amazon, ebay)
        databases = [
            {'key': 'shelves', 'path': 'searchRack.db', 'table': 'SEARCHRACK', 'name': 'Warehouse', 'color': '#9b59b6', 'icon': '📦'},
            {'key': 'processed', 'path': 'bol.db', 'table': 'bol_items', 'name': 'Item Manager (processed items)', 'color': '#2ecc71', 'icon': '✅', 'filter': 'checked_only'},
            {'key': 'bol', 'path': 'rawbol.db', 'table': 'raw_bol_items', 'name': 'Macy BOL', 'color': '#3498db', 'icon': '📦'},
            {'key': 'sold', 'path': 'sold.db', 'table': 'sold_items', 'name': 'Sold Items', 'color': '#e74c3c', 'icon': '💰'},
            {'key': 'amazon', 'path': 'amazonStore.db', 'table': 'INVENTORY', 'name': 'Amazon Store', 'color': '#1abc9c', 'icon': '📦'},
            {'key': 'ebay', 'path': 'ebayStore.db', 'table': 'INVENTORY', 'name': 'eBay Store', 'color': '#f39c12', 'icon': '🛒'},
        ]
        
        results = {
            'query': query,
            'search_type': 'upc' if is_upc_search else 'text',
            'databases': {},
            'from_cache': False
        }
        
        # Search each database
        for db_info in databases:
            db_key = db_info['key']
            db_path = ss_config.BASE_DIR / db_info['path']
            
            if not db_path.exists():
                results['databases'][db_key] = {
                    'name': db_info['name'],
                    'color': db_info['color'],
                    'icon': db_info['icon'],
                    'found': False,
                    'count': 0,
                    'error': 'Database not found'
                }
                continue
            
            try:
                conn = ss_database.get_db_connection(str(db_path))
                conn.row_factory = sqlite3.Row
                cur = conn.cursor()
                
                # Check if table exists
                cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (db_info['table'],))
                if not cur.fetchone():
                    results['databases'][db_key] = {
                        'name': db_info['name'],
                        'color': db_info['color'],
                        'icon': db_info['icon'],
                        'found': False,
                        'count': 0,
                        'error': 'Table not found'
                    }
                    continue
                
                # Get table columns
                cur.execute(f"PRAGMA table_info({db_info['table']})")
                cols = [r[1] for r in cur.fetchall()]
                
                # Build search query based on type
                if is_upc_search:
                    db_query_upc = query_upc
                    if db_key == 'bol':
                        db_query_upc = ss_shipping_identity._barcode_base_without_suffix(query_upc) or query_upc
                    # Fast UPC search on indexed columns
                    upc_cols = [c for c in cols if c.lower() in ('upc', 'barcode', 'sku')]
                    if not upc_cols:
                        upc_cols = [c for c in cols if 'upc' in c.lower() or 'barcode' in c.lower()]
                    
                    if upc_cols:
                        where_parts = [f"{col} LIKE ?" for col in upc_cols]
                        where_clause = " OR ".join(where_parts)
                        params = [f"%{db_query_upc}%"] * len(upc_cols)
                    else:
                        where_clause = "1=0"  # No UPC columns found
                        params = []
                else:
                    # Text search across all columns
                    where_parts = [f"LOWER(COALESCE({col},'')) LIKE ?" for col in cols]
                    where_clause = " OR ".join(where_parts)
                    params = [f"%{query_stripped.lower()}%"] * len(cols)
                
                # Apply filters for specific databases
                if db_info.get('filter') == 'checked_only':
                    # For Item Manager (processed), only show checked items (good or bad)
                    # Check for items_prep_status table (bol.db)
                    cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='items_prep_status'")
                    if cur.fetchone():
                        where_clause = f"({where_clause}) AND upc IN (SELECT upc FROM items_prep_status WHERE status IN ('good', 'bad'))"

                if db_key == 'shelves':
                    active_qty_col = next((c for c in cols if c.lower() in ('quantity', 'qty')), None)
                    if active_qty_col:
                        where_clause = (
                            f"({where_clause}) AND "
                            f"COALESCE(CAST({ss_database._sqlite_ident(active_qty_col)} AS INTEGER), 0) > 0"
                        )
                
                # Get count and sample results
                count_sql = f"SELECT COUNT(*) as count FROM {db_info['table']} WHERE {where_clause}"
                cur.execute(count_sql, params)
                count = cur.fetchone()['count']

                # For warehouse results, also provide total matched quantity.
                total_quantity = None
                if db_key == 'shelves':
                    qty_col = next((c for c in cols if c.lower() == 'quantity'), None)
                    if qty_col:
                        qty_sql = f"SELECT COALESCE(SUM(CAST({qty_col} AS REAL)), 0) AS total_quantity FROM {db_info['table']} WHERE {where_clause}"
                        cur.execute(qty_sql, params)
                        qty_row = cur.fetchone()
                        try:
                            total_quantity = max(0, int(round(float((qty_row['total_quantity'] if qty_row else 0) or 0))))
                        except Exception:
                            total_quantity = 0
                
                # Get sample results (up to 20)
                sample_results = []
                if count > 0:
                    sample_sql = f"SELECT * FROM {db_info['table']} WHERE {where_clause} LIMIT 20"
                    cur.execute(sample_sql, params)
                    sample_results = [dict(row) for row in cur.fetchall()]

                results['databases'][db_key] = {
                    'name': db_info['name'],
                    'color': db_info['color'],
                    'icon': db_info['icon'],
                    'found': count > 0,
                    'count': count,
                    'total_quantity': total_quantity,
                    'samples': sample_results if count > 0 else []
                }
                
            except Exception as e:
                results['databases'][db_key] = {
                    'name': db_info['name'],
                    'color': db_info['color'],
                    'icon': db_info['icon'],
                    'found': False,
                    'count': 0,
                    'error': ss_errors._safe_error(e)
                }
        
        # Cache UPC searches
        if is_upc_search:
            _search_all_cache[cache_key] = (results, time.time())
            # Clean old cache entries (keep cache size under control)
            current_time = time.time()
            expired_keys = [k for k, (_, t) in _search_all_cache.items() if current_time - t > _search_cache_timeout]
            for k in expired_keys:
                del _search_all_cache[k]
        
        return jsonify(results)
        
    except Exception as e:
        return jsonify({'error': ss_errors._safe_error(e)}), 500


def api_get_lot_numbers():
    """Get all distinct LOT numbers from rawbol.db with item counts, sorted by most recent import_date."""
    conn = None
    try:
        conn = sqlite3.connect('rawbol.db')
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        
        # Get unique LOT numbers with counts and latest import date
        cur.execute('''
            SELECT 
                lot_number,
                COUNT(*) as item_count,
                MAX(import_date) as latest_date
            FROM raw_bol_items
            WHERE lot_number IS NOT NULL 
                AND TRIM(COALESCE(lot_number, '')) != ''
                AND LOWER(lot_number) NOT IN ('nan', 'none', 'null')
            GROUP BY lot_number
            ORDER BY latest_date DESC
        ''')
        
        rows = cur.fetchall()
        lots = [{'lot_number': r['lot_number'], 'item_count': r['item_count'], 'latest_date': r['latest_date']} for r in rows]
        
        return jsonify({'success': True, 'lots': lots})
    except Exception as e:
        return jsonify({'success': False, 'error': ss_errors._safe_error(e)}), 500
    finally:
        if conn is not None:
            conn.close()


def api_bol_stats():
    """Calculate BOL statistics using the new quantity tracking system.
    Now properly tracks original_qty, good_qty, bad_qty, and unchecked_qty per LOT.
    """
    conn = None
    try:
        ss_listing_lifecycle._ensure_bol_list_status_column()
        conn = sqlite3.connect('bol.db')
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        
        # Check if new quantity columns exist
        cur.execute('PRAGMA table_info(bol_items)')
        cols = [col[1].lower() for col in cur.fetchall()]
        has_new_columns = all(c in cols for c in ['original_qty', 'good_qty', 'bad_qty', 'unchecked_qty'])
        
        if not has_new_columns:
            return jsonify({
                'success': False, 
                'error': 'Quantity columns not yet migrated. Please restart Flask to run migration.'
            }), 500
        
        # Get all LOT numbers with their stats, ordered by import_date DESC (newest first)
        cur.execute('''
            SELECT 
                lot_number,
                MAX(import_date) as latest_date,
                COUNT(DISTINCT upc) as unique_items,
                SUM(COALESCE(original_qty, 0)) as total_original,
                SUM(COALESCE(good_qty, 0)) as total_good,
                SUM(COALESCE(bad_qty, 0)) as total_bad,
                SUM(COALESCE(unchecked_qty, 0)) as total_unchecked
            FROM bol_items
            WHERE (temporary IS NULL OR temporary = 0)
                AND upc NOT LIKE '%-%'
                AND lot_number IS NOT NULL 
                AND TRIM(COALESCE(lot_number, '')) != ''
                AND LOWER(lot_number) NOT IN ('nan', 'none', 'null')
            GROUP BY lot_number
            ORDER BY latest_date DESC
        ''')
        
        lots = cur.fetchall()
        stats = []
        auto_entries = ss_prep_log._preplog_auto_assigned_entries(include_undone=False, limit=None)
        auto_by_lot = {}
        for entry in auto_entries:
            lot_key = ss_normalization._normalize_lot_number(entry.get('lot_number'))
            if not lot_key:
                continue
            auto_by_lot[lot_key] = int(auto_by_lot.get(lot_key, 0) or 0) + 1
        
        for lot_row in lots:
            lot_number = lot_row['lot_number']
            import_date = lot_row['latest_date']
            unique_items = lot_row['unique_items'] or 0
            total_original = lot_row['total_original'] or 0
            total_good = lot_row['total_good'] or 0
            total_bad = lot_row['total_bad'] or 0
            total_unchecked = lot_row['total_unchecked'] or 0
            
            # Calculate prepped quantity (good + bad), then clamp display metrics to sane bounds.
            raw_total_prepped = total_good + total_bad
            total_prepped = max(0, min(raw_total_prepped, total_original)) if total_original > 0 else max(0, raw_total_prepped)
            quantity_delta = (total_good + total_bad + total_unchecked) - total_original
            has_data_issue = (raw_total_prepped > total_original and total_original > 0) or (total_unchecked < 0) or (quantity_delta != 0)

            # Calculate percentage done based on quantity (not item count), capped to [0, 100].
            if total_original > 0:
                percent_done = round((total_prepped / total_original) * 100, 1)
            else:
                percent_done = 0.0
            
            # Calculate loss rate (bad / original), capped to [0, 100].
            if total_original > 0:
                effective_bad = max(0, min(total_bad, total_original))
                loss_rate = round((effective_bad / total_original) * 100, 1)
            else:
                loss_rate = 0.0
            
            stats.append({
                'lot_number': lot_number,
                'import_date': import_date,
                'unique_items': unique_items,
                'total_original': total_original,
                'total_good': total_good,
                'total_bad': total_bad,
                'total_unchecked': total_unchecked,
                'total_prepped': total_prepped,
                'raw_total_prepped': raw_total_prepped,
                'percent_done': percent_done,
                'loss_rate': loss_rate,
                'has_data_issue': has_data_issue,
                'quantity_delta': quantity_delta,
                'auto_assigned_count': int(auto_by_lot.get(lot_number, 0) or 0),
                'review_url': f"/item-prep/log?auto_assigned=1&lot={lot_number}"
            })
        
        
        return jsonify({
            'success': True,
            'stats': stats,
            'auto_assigned_total': len(auto_entries),
            'auto_assigned_lots': len(auto_by_lot)
        })
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': ss_errors._safe_error(e)}), 500
    finally:
        try:
            if conn:
                conn.close()
        except Exception:
            pass


def api_bol_stats_auto_assigned():
    """Return prep-log rows flagged as lot auto-assigned for QA review."""
    try:
        lot_filter = ss_normalization._normalize_lot_number(request.args.get('lot_number') or request.args.get('lot'))
        include_undone = (request.args.get('include_undone') or '0').strip().lower() in ('1', 'true', 'yes', 'y')
        limit = ss_listing_settings._listingagent_parse_int(request.args.get('limit'), 500) or 500
        limit = max(1, min(limit, 5000))
        items = ss_prep_log._preplog_auto_assigned_entries(include_undone=include_undone, lot_number=lot_filter, limit=limit)
        return jsonify({
            'success': True,
            'items': items,
            'count': len(items),
            'lot_number': lot_filter
        })
    except Exception as e:
        return jsonify({'success': False, 'error': ss_errors._safe_error(e, 'bol_stats:auto_assigned')}), 500
