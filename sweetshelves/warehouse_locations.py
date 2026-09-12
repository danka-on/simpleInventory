"""Warehouse locations for Sweet Shelves."""

import datetime
import json
import re
import re as _re
import sqlite3
import time
from flask import jsonify, request
from . import (
    bulk_manifest as ss_bulk_manifest, caching as ss_caching, config as ss_config, database as
    ss_database, errors as ss_errors, fba_inventory as ss_fba_inventory, fba_readiness as
    ss_fba_readiness, fba_schema as ss_fba_schema, inventory_age as ss_inventory_age, inventory_history
    as ss_inventory_history, listing_alerts as ss_listing_alerts, listing_checks as ss_listing_checks,
    listing_lifecycle as ss_listing_lifecycle, listing_settings as ss_listing_settings, normalization as
    ss_normalization, shelf_assets as ss_shelf_assets, shipping_identity as ss_shipping_identity,
    warehouse_matching as ss_warehouse_matching,
)


def api_lookup_location():
    data = request.get_json() or {}
    barcode = data.get('barcode')
    item_id = data.get('item_id')
    conn = None
    try:
        conn = sqlite3.connect(str(ss_config.BASE_DIR / 'searchRack.db'))
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        row = None
        for candidate in (barcode, item_id):
            if row or not candidate:
                continue
            # Match the stored spelling first, then leading-zero variants of the
            # same unit (a -suffix is kept, so one unit never resolves to another).
            variants = [str(candidate).strip()] + [
                v for v in ss_normalization._barcode_identity_variants(candidate)
                if v != str(candidate).strip()
            ]
            for variant in variants:
                cur.execute('''
                    SELECT * FROM SEARCHRACK
                    WHERE TRIM(BARCODE) = ? COLLATE NOCASE
                      AND COALESCE(CAST(QUANTITY AS INTEGER), 0) > 0
                    ORDER BY ID
                    LIMIT 1
                ''', (variant,))
                row = cur.fetchone()
                if row:
                    break
        if not row:
            return jsonify({'found': False})
        r = dict(row)
        return jsonify({'found': True, 'item_position': r.get('ITEM_POSITION') or r.get('item_position'), 'pictureposition': r.get('PICTUREPOSITION') or r.get('pictureposition') or r.get('image')})
    except Exception as e:
        return jsonify({'error': ss_errors._safe_error(e)}), 500
    finally:
        if conn is not None:
            conn.close()


def api_set_rack_location():
    """Set ITEM_POSITION for a SEARCHRACK row. Expects JSON: { id: <id>, item_position: <pos> }"""
    data = request.get_json() or {}
    item_id_raw = data.get('id')
    pos = (data.get('item_position') or '').strip()
    pictureposition = (data.get('pictureposition') or '').strip()
    
    # WORKAROUND: Parse move_qty from item_id if encoded as "id:qty" (for cache issues)
    move_qty_from_id = None
    if item_id_raw and ':' in str(item_id_raw):
        parts = str(item_id_raw).split(':')
        if len(parts) == 2:
            item_id_raw = parts[0]
            try:
                move_qty_from_id = int(parts[1])
            except Exception:
                pass
    
    item_id = item_id_raw
    
    # Optional move quantity: how many items to move from this row's Quantity
    try:
        move_qty = int(data.get('move_qty')) if data.get('move_qty') is not None else None
    except Exception:
        move_qty = None
    
    # Use move_qty from encoded ID if not provided in data
    if move_qty is None and move_qty_from_id is not None:
        move_qty = move_qty_from_id
    
    # Require id and at least one of pos or pictureposition
    if not item_id or (not pos and not pictureposition):
        return jsonify({'success': False, 'error': 'Missing id or item_position/pictureposition'}), 400
    try:
        conn = sqlite3.connect('searchRack.db')
        cur = conn.cursor()
        # Try updating by id column; fall back to rowid if id column not present
        cur.execute("PRAGMA table_info('SEARCHRACK')")
        cols = [r[1] for r in cur.fetchall()]
        pk = None
        if 'id' in cols:
            pk = 'id'
        elif 'ID' in cols:
            pk = 'ID'
        else:
            pk = None
        # Load current row to inspect Quantity and other columns (we may need to split)
        # Determine how to address the row (by pk or rowid)
        id_col = pk
        id_val = item_id
        if not id_col:
            id_col = 'rowid'
            id_val = item_id

        cur.execute(f"SELECT * FROM SEARCHRACK WHERE {id_col} = ?", (id_val,))
        existing = cur.fetchone()
        # If we couldn't find a matching row, return error
        if not existing:
            return jsonify({'success': False, 'error': 'Row not found'}), 404

        # Extract current quantity (try common column names)
        existing_dict = {d[0]: existing[idx] for idx, d in enumerate(cur.description)} if cur.description else dict(zip([c[0] for c in cur.description], existing))
        
        # Get barcode and title for history logging
        barcode = existing_dict.get('BARCODE') or existing_dict.get('barcode') or existing_dict.get('Barcode') or ''
        title = existing_dict.get('TITLE') or existing_dict.get('title') or existing_dict.get('Title') or ''
        old_location = existing_dict.get('ITEM_POSITION') or existing_dict.get('item_position') or existing_dict.get('ItemPosition') or ''
        
        qty_cols = ['Quantity','quantity','Qty','QTY','QUANTITY']
        current_qty = None
        for qc in qty_cols:
            if qc in existing_dict and existing_dict.get(qc) is not None:
                try:
                    current_qty = int(existing_dict.get(qc))
                    break
                except Exception:
                    current_qty = None
        # default to 1 when quantity is not known
        if current_qty is None:
            current_qty = 1

        # If a pictureposition is provided, update PICTUREPOSITION and clear ITEM_POSITION
        if pictureposition:
            if 'PICTUREPOSITION' in cols or 'pictureposition' in [c.lower() for c in cols]:
                pic_col = 'PICTUREPOSITION' if 'PICTUREPOSITION' in cols else next((c for c in cols if c.lower() == 'pictureposition'), 'PICTUREPOSITION')
            else:
                pic_col = 'PICTUREPOSITION'
            # enforce single-location rule: set pictureposition and clear item_position
            # Handle splitting when move_qty provided and less than current_qty
            target_move = move_qty or current_qty
            if target_move < 1 or target_move > current_qty:
                return jsonify({'success': False, 'error': 'move_qty out of range'}), 400

            if target_move < current_qty:
                # decrement existing row's Quantity and insert a new row for moved qty
                # find quantity column name to update (if any)
                qty_col_name = None
                for qc in qty_cols:
                    if qc in cols:
                        qty_col_name = qc
                        break
                if qty_col_name:
                    # update original row quantity
                    if pk:
                        cur.execute(f"UPDATE SEARCHRACK SET {qty_col_name} = {qty_col_name} - ? WHERE {pk} = ?", (target_move, item_id))
                    else:
                        cur.execute(f"UPDATE SEARCHRACK SET {qty_col_name} = {qty_col_name} - ? WHERE rowid = ?", (target_move, item_id))
                else:
                    # no quantity column; treat as items of qty 1, so we will just insert new row and delete original
                    pass

                # prepare new row columns/values copied from existing, but with moved qty and new pictureposition/item_position
                # Exclude primary key column from INSERT so it auto-increments
                col_names = [c for c in cols if c not in (pk, 'id', 'ID', 'Id', 'rowid')]
                placeholders = ','.join('?' for _ in col_names)
                # build values copying existing row values, replacing quantity and picture/item position
                cur_vals = []
                for c in col_names:
                    # Find value from existing row (need to get column index from full cols list)
                    col_idx = cols.index(c)
                    val = existing[col_idx]
                    # replace quantity if applicable
                    if c == qty_col_name:
                        val = target_move
                    if c.lower() == 'pictureposition':
                        val = pictureposition
                    if c.lower() == 'item_position' or c == 'ITEM_POSITION' or c.lower() == 'itemposition':
                        # clear item position when pictureposition is set
                        val = ''
                    cur_vals.append(val)
                # Insert new row (without specifying rowid)
                cur.execute(f"INSERT INTO SEARCHRACK ({', '.join(col_names)}) VALUES ({placeholders})", tuple(cur_vals))
            else:
                # moving all items: update in-place
                if pk:
                    cur.execute(f"UPDATE SEARCHRACK SET {pic_col} = ?, ITEM_POSITION = ? WHERE {pk} = ?", (pictureposition, '', item_id))
                else:
                    cur.execute(f"UPDATE SEARCHRACK SET {pic_col} = ?, ITEM_POSITION = ? WHERE rowid = ?", (pictureposition, '', item_id))
        # Otherwise update only ITEM_POSITION and clear PICTUREPOSITION
        elif pos:
            # find picture column name if exists
            pic_col = None
            if 'PICTUREPOSITION' in cols:
                pic_col = 'PICTUREPOSITION'
            else:
                for c in cols:
                    if c.lower() == 'pictureposition':
                        pic_col = c
                        break
            # Handle splitting similar to pictureposition case
            target_move = move_qty or current_qty
            
            if target_move < 1 or target_move > current_qty:
                return jsonify({'success': False, 'error': 'move_qty out of range'}), 400

            if target_move < current_qty:
                # decrement existing row's Quantity
                qty_col_name = None
                for qc in qty_cols:
                    if qc in cols:
                        qty_col_name = qc
                        break
                if qty_col_name:
                    if pk:
                        cur.execute(f"UPDATE SEARCHRACK SET {qty_col_name} = {qty_col_name} - ? WHERE {pk} = ?", (target_move, item_id))
                    else:
                        cur.execute(f"UPDATE SEARCHRACK SET {qty_col_name} = {qty_col_name} - ? WHERE rowid = ?", (target_move, item_id))
                    
                    # Log the quantity reduction to history
                    removed_conn = None
                    try:
                        from datetime import datetime
                        now = datetime.now().isoformat()
                        removed_conn = sqlite3.connect('rackhistory.db')
                        removed_cur = removed_conn.cursor()
                        removed_cur.execute('''
                            INSERT INTO removed_items 
                            (barcode, title, quantity_removed, removed_at, searchrack_id, old_quantity, new_quantity, removal_type, item_position)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                        ''', (barcode, title, target_move, now, item_id, current_qty, current_qty - target_move, 'location_edit', old_location))
                        removed_conn.commit()
                    except Exception:
                        pass
                    finally:
                        if removed_conn is not None:
                            removed_conn.close()

                # insert new row for moved qty
                # Exclude primary key column from INSERT so it auto-increments
                col_names = [c for c in cols if c not in (pk, 'id', 'ID', 'Id', 'rowid')]
                placeholders = ','.join('?' for _ in col_names)
                cur_vals = []
                for c in col_names:
                    # Find value from existing row (need to get column index from full cols list)
                    col_idx = cols.index(c)
                    val = existing[col_idx]
                    if c == qty_col_name:
                        val = target_move
                    if c.lower() == 'pictureposition':
                        val = ''
                    if c.lower() == 'item_position' or c == 'ITEM_POSITION' or c.lower() == 'itemposition':
                        val = pos
                    cur_vals.append(val)
                cur.execute(f"INSERT INTO SEARCHRACK ({', '.join(col_names)}) VALUES ({placeholders})", tuple(cur_vals))
                
                # Log the new row creation to history
                try:
                    from datetime import datetime
                    now = datetime.now().isoformat()
                    removed_conn = sqlite3.connect('rackhistory.db')
                    removed_cur = removed_conn.cursor()
                    removed_cur.execute('''
                        INSERT INTO removed_items 
                        (barcode, title, quantity_removed, removed_at, old_quantity, new_quantity, removal_type, item_position)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ''', (barcode, title, target_move, now, 0, target_move, 'location_edit', pos))
                    removed_conn.commit()
                except Exception:
                    pass
                finally:
                    removed_conn.close()
            else:
                # update in-place - log location change
                if pk:
                    if pic_col:
                        cur.execute(f"UPDATE SEARCHRACK SET ITEM_POSITION = ?, {pic_col} = ? WHERE {pk} = ?", (pos, '', item_id))
                    else:
                        cur.execute(f"UPDATE SEARCHRACK SET ITEM_POSITION = ? WHERE {pk} = ?", (pos, item_id))
                else:
                    if pic_col:
                        cur.execute(f"UPDATE SEARCHRACK SET ITEM_POSITION = ?, {pic_col} = ? WHERE rowid = ?", (pos, '', item_id))
                    else:
                        cur.execute("UPDATE SEARCHRACK SET ITEM_POSITION = ? WHERE rowid = ?", (pos, item_id))
        conn.commit()
        updated = cur.rowcount
        ss_caching._invalidate_searchrack_cache()
        return jsonify({'success': True, 'updated': updated})
    except Exception as e:
        return jsonify({'success': False, 'error': ss_errors._safe_error(e)}), 500
    finally:
        if conn is not None:
            conn.close()


def _get_table_and_pk(db_path, table_hint=None):
    conn = sqlite3.connect(db_path)
    try:
        cur = conn.cursor()
        cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")
        tables = [r[0] for r in cur.fetchall()]
        if not tables:
            return None, None
        # Prefer common application tables if present (avoid picking auxiliary tables like ENRICH_META)
        prefer_order = ['orders', 'INVENTORY', 'SEARCHRACK', 'searchrack', 'rack', 'items', 'bol_items']
        prefer = None
        for p in prefer_order:
            if p in tables:
                prefer = p
                break
        table = table_hint if table_hint in tables else (prefer or (tables[0] if tables else None))
        cur.execute(f"PRAGMA table_info('{table}')")
        cols = cur.fetchall()
        pk = None
        colnames = [c[1] for c in cols]
        for c in cols:
            if c[5] == 1:
                pk = c[1]
                break
        if not pk:
            if 'id' in colnames:
                pk = 'id'
            elif 'ID' in colnames:
                pk = 'ID'
            else:
                pk = colnames[0]
    finally:
        conn.close()
    return table, pk


def api_update_row(db_key, item_id):
    data = request.get_json() or {}
    mapping = {
        'ebayStore': 'ebayStore.db',
        'amazonStore': 'amazonStore.db',
        'sold': 'sold.db',
        'searchRack': 'searchRack.db',
        'found': 'found.db',
        'bol': 'bol.db'
    }
    if db_key not in mapping:
        return jsonify({'error': 'Unknown db_key'}), 400
    if db_key == 'searchRack':
        quantity_key = next((key for key in data if key.lower() == 'quantity'), None)
        if quantity_key is not None:
            parsed_quantity = ss_normalization._strict_inventory_quantity(data.get(quantity_key))
            if parsed_quantity is None or parsed_quantity < 0:
                return jsonify({
                    'error': 'Quantity must be a whole number of 0 or more; 0 archives and removes the row'
                }), 400
            data[quantity_key] = parsed_quantity
    db_path = mapping[db_key]
    conn = None
    try:
        table, pk = _get_table_and_pk(db_path)
        if not table:
            return jsonify({'error': 'No table found in DB'}), 400
        conn = sqlite3.connect(db_path)
        cur = conn.cursor()
        cur.execute(f"PRAGMA table_info('{table}')")
        cols = [r[1] for r in cur.fetchall()]
        
        # Debug logging
        print(f"DEBUG UPDATE: db_key={db_key}, table={table}, columns={cols}")
        print(f"DEBUG UPDATE: incoming data={data}")
        
        # If updating searchRack quantity, log old value for history tracking
        old_qty = None
        old_barcode = None
        old_title = None
        old_location = None
        tracker_upc_key = None
        tracker_prev_total = None
        if db_key == 'searchRack' and any(k.lower() == 'quantity' for k in data.keys()):
            try:
                cur.execute(f'SELECT QUANTITY, BARCODE, TITLE, ITEM_POSITION FROM {table} WHERE {pk} = ?', (item_id,))
                old_row = cur.fetchone()
                if old_row:
                    old_qty, old_barcode, old_title, old_location = old_row
                    old_qty = ss_normalization._coerce_int(old_qty, 0)
                    tracker_upc_key = ss_listing_alerts._listing_alert_upc_key(old_barcode)
                    if tracker_upc_key:
                        tracker_prev_total = ss_warehouse_matching._searchrack_total_qty_for_key(conn, tracker_upc_key)
            except Exception as e:
                print(f"Warning: Could not fetch old values for history: {e}")
        
        set_parts = []
        params = []
        
        # Create case-insensitive column mapping
        cols_lower = {c.lower(): c for c in cols}
        
        for k, v in data.items():
            k_lower = k.lower()
            # Find matching column name (case-insensitive)
            if k_lower in cols_lower:
                actual_col = cols_lower[k_lower]
                set_parts.append(f"{actual_col} = ?")
                params.append(v)
            else:
                print(f"DEBUG UPDATE: Field '{k}' not found in columns (tried lowercase '{k_lower}')")
        
        if not set_parts:
            print(f"ERROR UPDATE: No updatable fields. Data keys: {list(data.keys())}, Table cols: {cols}")
            return jsonify({
                'error': 'No updatable fields provided',
                'data_keys': list(data.keys()),
                'table_columns': cols
            }), 400
        
        params.append(item_id)
        sql = f"UPDATE {table} SET {', '.join(set_parts)} WHERE {pk} = ?"
        print(f"DEBUG UPDATE: SQL={sql}, params={params}")
        
        cur.execute(sql, params)
        conn.commit()
        updated = cur.rowcount
        
        # Log quantity changes to history for searchRack
        if db_key == 'searchRack' and old_qty is not None:
            new_qty_raw = next((v for k, v in data.items() if k.lower() == 'quantity'), None)
            if new_qty_raw is not None:
                new_qty = ss_normalization._coerce_int(new_qty_raw, old_qty)
            else:
                new_qty = None
            if new_qty is not None and new_qty != old_qty:
                removed_conn = None
                try:
                    from datetime import datetime
                    now = datetime.now()
                    
                    removed_conn = sqlite3.connect('rackhistory.db')
                    removed_cur = removed_conn.cursor()
                    ss_inventory_history._ensure_removed_items_table(removed_cur)
                    removed_cur.execute('''
                        INSERT INTO removed_items 
                        (order_id, barcode, title, quantity_removed, removed_at, searchrack_id, old_quantity, new_quantity, removal_type, item_position)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ''', (None, old_barcode, old_title, abs(new_qty - old_qty), now.isoformat(), item_id, old_qty, new_qty, 'manual_edit', old_location or ''))
                    removed_conn.commit()
                    print(f"✅ Logged manual edit to history: {old_title} ({old_qty} → {new_qty})")
                except Exception as log_err:
                    print(f"❌ Error logging to history: {log_err}")
                    import traceback
                    traceback.print_exc()
                finally:
                    if removed_conn is not None:
                        removed_conn.close()
        
        # The SEARCHRACK trigger snapshots and physically deletes a row that reaches zero.
        if db_key == 'searchRack' and 'quantity' in [k.lower() for k in data.keys()]:
            qty_value_raw = next((v for k, v in data.items() if k.lower() == 'quantity'), None)
            qty_value = ss_normalization._coerce_int(qty_value_raw, 0)
            if qty_value <= 0:
                ss_inventory_history._clear_zero_qty_pending_deletions(item_id)
                print(f"✅ searchRack item {item_id} was archived to Rack History and removed at quantity 0")
                conn.commit()
            elif qty_value > 0:
                ss_inventory_history._clear_zero_qty_pending_deletions(item_id)
                conn.commit()

        if db_key == 'searchRack' and tracker_upc_key and tracker_prev_total is not None:
            try:
                tracker_current_total = ss_warehouse_matching._searchrack_total_qty_for_key(conn, tracker_upc_key)
                ss_inventory_history._record_inventory_zero_transition(tracker_upc_key, tracker_prev_total, tracker_current_total)
            except Exception as tracker_err:
                print(f"Warning: zero-transition write tracking failed in api_update_row: {tracker_err}")

        if db_key == 'searchRack':
            ss_caching._invalidate_searchrack_cache()

        print(f"DEBUG UPDATE: Updated {updated} rows")
        return jsonify({'success': True, 'updated': updated})
    except Exception as e:
        import traceback
        print(f"ERROR UPDATE: {e}")
        print(traceback.format_exc())
        return jsonify({'error': ss_errors._safe_error(e)}), 500
    finally:
        if conn is not None:
            conn.close()


def _movelocation_barcode_key(value):
    return ss_normalization._normalize_upc_preserve_suffix_for_match(ss_normalization._normalize_upc(value))


def _movelocation_barcode_variants(value):
    raw = ss_normalization._normalize_upc(value)
    key = _movelocation_barcode_key(raw)
    variants = set()
    for candidate in (raw, key):
        s = str(candidate or '').strip()
        if s:
            variants.add(s)

    if key and '-' in key:
        base, suffix = key.split('-', 1)
        if base.isdigit():
            for width in (8, 11, 12, 13, 14):
                if len(base) < width:
                    variants.add(f"{base.zfill(width)}-{suffix}")
    elif key and key.isdigit():
        for width in (8, 11, 12, 13, 14):
            if len(key) < width:
                variants.add(key.zfill(width))

    if raw and raw.isdigit():
        stripped = raw.lstrip('0') or '0'
        variants.add(stripped)
        for width in (8, 11, 12, 13, 14):
            if len(stripped) < width:
                variants.add(stripped.zfill(width))

    return {str(v).strip() for v in variants if str(v).strip()}


def _fba_macy_bol_lookup(barcode):
    """Return the best normalized Macy BOL match for an FBA scan."""
    variants = []
    seen = set()

    def add_variant(value):
        normalized = str(value or '').strip().casefold()
        if normalized and normalized not in seen:
            seen.add(normalized)
            variants.append(normalized)

    # Marketplace variants intentionally include the unsuffixed product UPC so
    # an individually suffixed warehouse label can still inherit Macy metadata.
    for variant in ss_listing_lifecycle._marketplace_upc_lookup_variants(barcode):
        add_variant(variant)
    for variant in sorted(_movelocation_barcode_variants(barcode)):
        add_variant(variant)
    if not variants:
        return None

    conn = None
    try:
        conn = sqlite3.connect(str(ss_config.BASE_DIR / 'rawbol.db'))
        conn.row_factory = sqlite3.Row
        placeholders = ','.join('?' for _ in variants)
        rows = conn.execute(f'''
            SELECT rowid AS _rowid_, upc, item_description, image_url
            FROM raw_bol_items
            WHERE LOWER(TRIM(upc)) IN ({placeholders})
            ORDER BY rowid DESC
        ''', tuple(variants)).fetchall()
        if not rows:
            return None

        variant_rank = {value: index for index, value in enumerate(variants)}
        best = min(
            rows,
            key=lambda row: (
                variant_rank.get(str(row['upc'] or '').strip().casefold(), len(variants)),
                -int(row['_rowid_'] or 0),
            ),
        )
        return {
            'found': True,
            'upc': str(best['upc'] or '').strip(),
            'title': str(best['item_description'] or '').strip(),
            'image': ss_shipping_identity._ready_to_ship_image_value(best['image_url']),
        }
    except Exception as exc:
        print(f'Warning: FBA Macy BOL lookup failed for {barcode}: {exc}')
        return None
    finally:
        if conn is not None:
            conn.close()


def _movelocation_parse_qty(value, default=1):
    try:
        if value is None:
            return default
        s = str(value).strip()
        if not s:
            return default
        return int(float(s))
    except Exception:
        return default


def _movelocation_pick_image(row_dict, image_col=None, images_col=None):
    try:
        image_val = ''
        if image_col:
            image_val = str(row_dict.get(image_col) or '').strip()
        if image_val:
            return image_val

        images_raw = row_dict.get(images_col) if images_col else ''
        if not images_raw:
            return ''

        if isinstance(images_raw, list):
            for item in images_raw:
                s = str(item or '').strip()
                if s:
                    return s
            return ''

        images_text = str(images_raw).strip()
        if not images_text:
            return ''
        if images_text.startswith('['):
            try:
                parsed = json.loads(images_text)
                if isinstance(parsed, list):
                    for item in parsed:
                        s = str(item or '').strip()
                        if s:
                            return s
            except Exception:
                pass
        for sep in ('|', ',', ';'):
            if sep in images_text:
                for part in images_text.split(sep):
                    s = part.strip()
                    if s:
                        return s
        return images_text
    except Exception:
        return ''


def _movelocation_row_location(row_dict, pos_col=None, pic_col=None):
    pos_val = str(row_dict.get(pos_col) or '').strip() if pos_col else ''
    pic_val = str(row_dict.get(pic_col) or '').strip() if pic_col else ''
    code = pos_val or pic_val
    preview = pic_val or pos_val
    return code, pos_val, pic_val, preview


def api_movelocation_lookup():
    """Lookup a scanned barcode in warehouse inventory and Macy BOL."""
    conn = None
    try:
        barcode_raw = (request.args.get('barcode') or '').strip()
        if not barcode_raw:
            return jsonify({'success': False, 'error': 'Missing barcode'}), 400

        barcode_key = _movelocation_barcode_key(barcode_raw)
        if not barcode_key:
            return jsonify({'success': False, 'error': 'Invalid barcode'}), 400

        conn = sqlite3.connect('searchRack.db')
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()

        cur.execute("PRAGMA table_info('SEARCHRACK')")
        cols = [r[1] for r in cur.fetchall()]
        cols_lower = {c.lower(): c for c in cols}
        id_col = cols_lower.get('id')
        barcode_col = cols_lower.get('barcode') or cols_lower.get('upc')
        title_col = cols_lower.get('title')
        image_col = cols_lower.get('image')
        images_col = cols_lower.get('images')
        qty_col = cols_lower.get('quantity') or cols_lower.get('qty')
        pos_col = cols_lower.get('item_position') or cols_lower.get('itemposition') or cols_lower.get('position')
        pic_col = cols_lower.get('pictureposition')

        if not barcode_col:
            return jsonify({'success': False, 'error': 'SEARCHRACK barcode column not found'}), 500

        base_sql = f'''
            SELECT rowid AS _rowid_, *
            FROM SEARCHRACK
            WHERE {barcode_col} IS NOT NULL
              AND TRIM({barcode_col}) != ''
        '''
        if qty_col:
            base_sql += f' AND COALESCE(CAST({ss_database._sqlite_ident(qty_col)} AS INTEGER), 0) > 0'

        variants = sorted(v.lower() for v in _movelocation_barcode_variants(barcode_raw))
        candidate_rows = []
        if variants:
            placeholders = ','.join('?' for _ in variants)
            cur.execute(base_sql + f" AND LOWER(TRIM({barcode_col})) IN ({placeholders})", tuple(variants))
            candidate_rows = [dict(r) for r in cur.fetchall()]

        matched_rows = []
        for row in candidate_rows:
            row_barcode = row.get(barcode_col)
            if _movelocation_barcode_key(row_barcode) == barcode_key:
                matched_rows.append(row)

        macy_bol = _fba_macy_bol_lookup(barcode_raw)

        def macy_only_response():
            canonical = ss_normalization._normalize_upc(barcode_raw) or barcode_raw
            payload = {
                'barcode': canonical,
                'barcode_display': ss_normalization._format_upc_display(canonical),
                'barcode_key': barcode_key,
                'title': macy_bol.get('title') or canonical,
                'image': macy_bol.get('image') or '',
                'inventory_found': False,
                'macy_bol_found': True,
                'macy_bol': macy_bol,
                'total_available': 0,
                'locations_count': 0,
                'locations': [],
            }
            include_fba_mode = str(request.args.get('include_fba') or '').strip().lower()
            if include_fba_mode in ('1', 'true', 'yes', 'listing_only'):
                marketplace = {} if include_fba_mode == 'listing_only' else (ss_fba_inventory._fba_marketplace_status_for_barcodes([canonical]).get(barcode_key) or {})
                amazon_listing = ss_fba_inventory._fba_local_amazon_listing(canonical)
                payload['fba'] = {
                    'amazon_listing': amazon_listing,
                    'barcode_guidance': (
                        ss_fba_readiness._fba_amazon_barcode_guidance_unavailable('The accepted inbound plan controls labeling during packing.')
                        if include_fba_mode == 'listing_only'
                        else ss_fba_readiness._fba_amazon_barcode_guidance(amazon_listing)
                    ),
                    'listed_ebay': bool(marketplace.get('listed_ebay')),
                    'listed_amazon': bool(marketplace.get('listed_amazon')),
                    'listing_links': marketplace.get('links') or [],
                }
            return jsonify({
                'success': True,
                'found': True,
                'inventory_found': False,
                'macy_bol_found': True,
                'message': 'Item found.',
                'item': payload,
            })

        if not matched_rows:
            if macy_bol:
                return macy_only_response()
            return jsonify({
                'success': True,
                'found': False,
                'inventory_found': False,
                'macy_bol_found': False,
                'error': f'No warehouse inventory or Macy BOL record found for barcode {barcode_raw}'
            })

        title_val = ''
        image_val = ''
        canonical_barcode = ''
        locations_map = {}
        total_available = 0

        for row in matched_rows:
            if not canonical_barcode:
                canonical_barcode = str(row.get(barcode_col) or '').strip()
            if not title_val and title_col:
                title_val = str(row.get(title_col) or '').strip()
            if not image_val:
                image_val = _movelocation_pick_image(row, image_col=image_col, images_col=images_col)

            code, pos_val, pic_val, preview_val = _movelocation_row_location(row, pos_col=pos_col, pic_col=pic_col)
            if not code:
                continue

            qty_val = max(0, _movelocation_parse_qty(row.get(qty_col) if qty_col else None, default=1))
            total_available += qty_val

            loc_key = code.lower()
            bucket = locations_map.get(loc_key)
            if bucket is None:
                bucket = {
                    'location_key': loc_key,
                    'code': code,
                    'item_position': pos_val,
                    'pictureposition': pic_val,
                    'preview_key': preview_val or code,
                    'quantity': 0,
                    'row_ids': []
                }
                locations_map[loc_key] = bucket

            bucket['quantity'] += qty_val
            row_id = row.get(id_col) if id_col else row.get('_rowid_')
            if row_id is None:
                row_id = row.get('_rowid_')
            if row_id is not None and row_id not in bucket['row_ids']:
                bucket['row_ids'].append(row_id)

        locations = sorted(
            locations_map.values(),
            key=lambda x: (-int(x.get('quantity') or 0), str(x.get('code') or '').lower())
        )

        if not locations:
            if macy_bol:
                return macy_only_response()
            return jsonify({
                'success': True,
                'found': False,
                'error': f'No location code found for barcode {barcode_raw}'
            })

        canonical_barcode = canonical_barcode or ss_normalization._normalize_upc(barcode_raw)
        if not title_val and macy_bol:
            title_val = str(macy_bol.get('title') or '').strip()
        if not image_val and macy_bol:
            image_val = str(macy_bol.get('image') or '').strip()
        if not title_val:
            title_val = canonical_barcode or barcode_raw

        item_payload = {
            'barcode': canonical_barcode,
            'barcode_display': ss_normalization._format_upc_display(canonical_barcode),
            'barcode_key': barcode_key,
            'title': title_val,
            'image': image_val,
            'inventory_found': True,
            'macy_bol_found': bool(macy_bol),
            'macy_bol': macy_bol or {},
            'total_available': max(0, int(total_available or 0)),
            'locations_count': len(locations),
            'locations': locations
        }
        include_fba_mode = str(request.args.get('include_fba') or '').strip().lower()
        if include_fba_mode in ('1', 'true', 'yes', 'listing_only'):
            marketplace = {} if include_fba_mode == 'listing_only' else (ss_fba_inventory._fba_marketplace_status_for_barcodes([canonical_barcode]).get(barcode_key) or {})
            amazon_listing = ss_fba_inventory._fba_local_amazon_listing(canonical_barcode)
            item_payload['fba'] = {
                'amazon_listing': amazon_listing,
                'barcode_guidance': (
                    ss_fba_readiness._fba_amazon_barcode_guidance_unavailable('The accepted inbound plan controls labeling during packing.')
                    if include_fba_mode == 'listing_only'
                    else ss_fba_readiness._fba_amazon_barcode_guidance(amazon_listing)
                ),
                'listed_ebay': bool(marketplace.get('listed_ebay')),
                'listed_amazon': bool(marketplace.get('listed_amazon')),
                'listing_links': marketplace.get('links') or [],
            }

        return jsonify({
            'success': True,
            'found': True,
            'item': item_payload
        })
    except Exception as e:
        return jsonify({'success': False, 'error': ss_errors._safe_error(e)}), 500
    finally:
        try:
            if conn is not None:
                conn.close()
        except Exception:
            pass


def api_fba_prep_location_family():
    """Expand a scanned parent shelf into its registered or occupied child bins."""
    requested = ss_shelf_assets._safe_shelf_lookup_code(request.args.get('location') or request.args.get('shelf'))
    if not requested:
        return jsonify({'success': False, 'error': 'Missing or invalid shelf/bin code'}), 400

    conn = None
    try:
        conn = sqlite3.connect('searchRack.db', timeout=30.0)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        known_by_key = {}
        stats = {}

        cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND LOWER(name) = 'shelves' LIMIT 1")
        if cur.fetchone():
            for row in cur.execute('SELECT shelf_name FROM shelves WHERE shelf_name IS NOT NULL'):
                code = str(row['shelf_name'] or '').strip()
                if code:
                    known_by_key.setdefault(code.casefold(), code)

        cur.execute("PRAGMA table_info('SEARCHRACK')")
        columns = [row[1] for row in cur.fetchall()]
        lower = {str(column).lower(): column for column in columns}
        barcode_col = lower.get('barcode') or lower.get('upc')
        qty_col = lower.get('quantity') or lower.get('qty')
        pos_col = lower.get('item_position') or lower.get('itemposition') or lower.get('position')
        pic_col = lower.get('pictureposition')

        if columns and qty_col and (pos_col or pic_col):
            if pos_col and pic_col:
                location_expr = (
                    f"COALESCE(NULLIF(TRIM({ss_database._sqlite_ident(pos_col)}), ''), "
                    f"NULLIF(TRIM({ss_database._sqlite_ident(pic_col)}), ''))"
                )
            elif pos_col:
                location_expr = f"NULLIF(TRIM({ss_database._sqlite_ident(pos_col)}), '')"
            else:
                location_expr = f"NULLIF(TRIM({ss_database._sqlite_ident(pic_col)}), '')"
            barcode_expr = ss_database._sqlite_ident(barcode_col) if barcode_col else "''"
            cur.execute(f'''
                SELECT {location_expr} AS location_code,
                       {barcode_expr} AS barcode,
                       SUM(COALESCE(CAST({ss_database._sqlite_ident(qty_col)} AS INTEGER), 0)) AS quantity
                FROM SEARCHRACK
                WHERE {location_expr} IS NOT NULL
                  AND COALESCE(CAST({ss_database._sqlite_ident(qty_col)} AS INTEGER), 0) > 0
                GROUP BY LOWER({location_expr}), LOWER(TRIM({barcode_expr}))
            ''')
            for row in cur.fetchall():
                code = str(row['location_code'] or '').strip()
                if not code:
                    continue
                key = code.casefold()
                known_by_key.setdefault(key, code)
                bucket = stats.setdefault(key, {'barcodes': set(), 'total_units': 0})
                barcode_key = _movelocation_barcode_key(row['barcode'])
                if barcode_key:
                    bucket['barcodes'].add(barcode_key)
                bucket['total_units'] += max(0, _movelocation_parse_qty(row['quantity'], default=0))

        known_keys = set(known_by_key)
        bin_parent = ss_fba_inventory._fba_bin_parent_code(requested, known_keys)
        requested_key = requested.casefold()
        canonical_requested = known_by_key.get(requested_key, requested)

        if bin_parent:
            family_codes = [canonical_requested]
            parent_code = known_by_key.get(bin_parent.casefold(), bin_parent)
            is_parent_scan = False
        else:
            parent_code = canonical_requested
            child_pattern = _re.compile(
                r'^' + _re.escape(parent_code) + r'(?:[-_. ]?b)(\d+)$',
                _re.IGNORECASE,
            )
            children = []
            for code in known_by_key.values():
                match = child_pattern.fullmatch(code)
                if match:
                    children.append((int(match.group(1)), code.casefold(), code))
            children.sort(key=lambda row: (row[0], row[1]))
            family_codes = [parent_code] + [row[2] for row in children]
            is_parent_scan = True

        result = []
        seen = set()
        for code in family_codes:
            key = str(code or '').strip().casefold()
            if not key or key in seen:
                continue
            seen.add(key)
            counts = stats.get(key) or {'barcodes': set(), 'total_units': 0}
            result.append({
                'code': str(code or '').strip(),
                'active': True,
                'is_parent': bool(is_parent_scan and key == parent_code.casefold()),
                'parent_code': parent_code,
                'items_count': len(counts['barcodes']),
                'total_units': int(counts['total_units'] or 0),
            })

        return jsonify({
            'success': True,
            'scanned': requested,
            'parent_code': parent_code,
            'is_parent_scan': is_parent_scan,
            'expanded': bool(is_parent_scan and len(result) > 1),
            'bin_count': max(0, len(result) - 1) if is_parent_scan else 0,
            'locations': result,
        })
    except Exception as exc:
        return jsonify({'success': False, 'error': ss_errors._safe_error(exc, 'fba location family')}), 500
    finally:
        if conn is not None:
            conn.close()


def api_movelocation_lookup_shelf():
    """Lookup all inventory rows for one shelf code and aggregate by barcode."""
    conn = None
    try:
        shelf_raw = (request.args.get('shelf') or '').strip()
        if not shelf_raw:
            return jsonify({'success': False, 'error': 'Missing shelf code'}), 400

        shelf_norm = shelf_raw.lower()

        conn = sqlite3.connect('searchRack.db')
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()

        cur.execute("PRAGMA table_info('SEARCHRACK')")
        cols = [r[1] for r in cur.fetchall()]
        cols_lower = {c.lower(): c for c in cols}
        id_col = cols_lower.get('id')
        barcode_col = cols_lower.get('barcode') or cols_lower.get('upc')
        title_col = cols_lower.get('title')
        image_col = cols_lower.get('image')
        images_col = cols_lower.get('images')
        qty_col = cols_lower.get('quantity') or cols_lower.get('qty')
        pos_col = cols_lower.get('item_position') or cols_lower.get('itemposition') or cols_lower.get('position')
        pic_col = cols_lower.get('pictureposition')
        date_col = cols_lower.get('created_at')

        if not barcode_col:
            return jsonify({'success': False, 'error': 'SEARCHRACK barcode column not found'}), 500
        if not qty_col:
            return jsonify({'success': False, 'error': 'SEARCHRACK quantity column not found'}), 500
        if not pos_col and not pic_col:
            return jsonify({'success': False, 'error': 'SEARCHRACK location columns not found'}), 500
        active_clause = (
            f'COALESCE(CAST({ss_database._sqlite_ident(qty_col)} AS INTEGER), 0) > 0'
            if qty_col else '1 = 1'
        )

        if pos_col and pic_col:
            cur.execute(f'''
                SELECT rowid AS _rowid_, *
                FROM SEARCHRACK
                WHERE LOWER(TRIM(COALESCE(NULLIF({pos_col}, ''), NULLIF({pic_col}, '')))) = ?
                  AND {active_clause}
            ''', (shelf_norm,))
        elif pos_col:
            cur.execute(f'''
                SELECT rowid AS _rowid_, *
                FROM SEARCHRACK
                WHERE LOWER(TRIM({pos_col})) = ?
                  AND {active_clause}
            ''', (shelf_norm,))
        else:
            cur.execute(f'''
                SELECT rowid AS _rowid_, *
                FROM SEARCHRACK
                WHERE LOWER(TRIM({pic_col})) = ?
                  AND {active_clause}
            ''', (shelf_norm,))

        matched_rows = [dict(r) for r in cur.fetchall()]
        if not matched_rows:
            return jsonify({
                'success': True,
                'found': False,
                'shelf': shelf_raw,
                'error': f'No inventory found for shelf {shelf_raw}',
                'items': [],
                'items_count': 0,
                'total_units': 0
            })

        buckets = {}
        total_units = 0

        for row in matched_rows:
            code, pos_val, pic_val, preview_val = _movelocation_row_location(row, pos_col=pos_col, pic_col=pic_col)
            if not code:
                continue

            code_norm = str(code).strip().lower()
            if code_norm != shelf_norm:
                pos_norm = str(pos_val or '').strip().lower()
                pic_norm = str(pic_val or '').strip().lower()
                if pos_norm != shelf_norm and pic_norm != shelf_norm:
                    continue

            barcode_val = str(row.get(barcode_col) or '').strip()
            barcode_key = _movelocation_barcode_key(barcode_val)
            if not barcode_key:
                continue

            qty_val = max(0, _movelocation_parse_qty(row.get(qty_col) if qty_col else None, default=1))
            if qty_val <= 0:
                continue

            bucket = buckets.get(barcode_key)
            if bucket is None:
                canonical_barcode = barcode_val or barcode_key
                title_val = str(row.get(title_col) or '').strip() if title_col else ''
                image_val = _movelocation_pick_image(row, image_col=image_col, images_col=images_col)
                bucket = {
                    'barcode': canonical_barcode,
                    'barcode_display': ss_normalization._format_upc_display(canonical_barcode),
                    'barcode_key': barcode_key,
                    'title': title_val or canonical_barcode,
                    'image': image_val,
                    'date_added': None,
                    'total_available': 0,
                    'locations': [{
                        'location_key': code_norm,
                        'code': str(code).strip(),
                        'item_position': pos_val,
                        'pictureposition': pic_val,
                        'preview_key': preview_val or code,
                        'quantity': 0,
                        'row_ids': []
                    }]
                }
                buckets[barcode_key] = bucket
            else:
                if not bucket.get('title') and title_col:
                    bucket['title'] = str(row.get(title_col) or '').strip()
                if not bucket.get('image'):
                    bucket['image'] = _movelocation_pick_image(row, image_col=image_col, images_col=images_col)

            added = ss_inventory_history._history_timestamp(row.get(date_col)) if date_col else None
            previous_added = ss_inventory_history._history_timestamp(bucket['date_added'])
            if added is not None and (previous_added is None or added < previous_added):
                bucket['date_added'] = added.isoformat()

            loc_entry = bucket['locations'][0]
            loc_entry['quantity'] += qty_val

            row_id = row.get(id_col) if id_col else row.get('_rowid_')
            if row_id is None:
                row_id = row.get('_rowid_')
            if row_id is not None and row_id not in loc_entry['row_ids']:
                loc_entry['row_ids'].append(row_id)

            bucket['total_available'] += qty_val
            total_units += qty_val

        items = sorted(
            [v for v in buckets.values() if int(v.get('total_available') or 0) > 0 and v.get('locations')],
            key=lambda x: (
                str(x.get('title') or '').lower(),
                str(x.get('barcode') or '').lower()
            )
        )

        if items and str(request.args.get('include_fba') or '').strip().lower() in ('1', 'true', 'yes'):
            marketplace_by_barcode = ss_fba_inventory._fba_marketplace_status_for_barcodes([
                item.get('barcode') for item in items
            ])
            amazon_listings = [
                ss_fba_inventory._fba_local_amazon_listing(item.get('barcode'))
                for item in items
            ]
            barcode_guidance = ss_fba_readiness._fba_amazon_barcode_guidance_for_listings(amazon_listings)
            for item, amazon_listing, guidance in zip(items, amazon_listings, barcode_guidance):
                marketplace = marketplace_by_barcode.get(item.get('barcode_key')) or {}
                item['fba'] = {
                    'amazon_listing': amazon_listing,
                    'barcode_guidance': guidance,
                    'listed_ebay': bool(marketplace.get('listed_ebay')),
                    'listed_amazon': bool(marketplace.get('listed_amazon')),
                    'listing_links': marketplace.get('links') or [],
                }

        if not items:
            return jsonify({
                'success': True,
                'found': False,
                'shelf': shelf_raw,
                'error': f'No movable inventory found for shelf {shelf_raw}',
                'items': [],
                'items_count': 0,
                'total_units': 0
            })

        return jsonify({
            'success': True,
            'found': True,
            'shelf': shelf_raw,
            'items': items,
            'items_count': len(items),
            'total_units': int(total_units or 0)
        })
    except Exception as e:
        return jsonify({'success': False, 'error': ss_errors._safe_error(e)}), 500
    finally:
        try:
            if conn is not None:
                conn.close()
        except Exception:
            pass


def api_movelocation_execute():
    """Move scanned quantities to a destination shelf, or clear location codes when requested."""
    conn = None
    rem_conn = None
    try:
        data = request.get_json() or {}
        to_location = (data.get('to_location') or '').strip()
        clear_location = bool(data.get('clear_location'))
        items = data.get('items') or []
        workflow = str(data.get('workflow') or '').strip().lower()
        fba_mode = request.path.rstrip('/').endswith('/api/fba-prep/complete') or workflow in ('fba', 'amazon_fba', 'fba_prep')
        fba_meta = data.get('fba') if isinstance(data.get('fba'), dict) else {}
        fba_session_id = None
        amazon_inbound_plan_id = ''

        if fba_mode:
            clear_location = True
            if not isinstance(items, list) or not items:
                return jsonify({'success': False, 'error': 'Add at least one item to the FBA batch'}), 400
            if len(items) > 500:
                return jsonify({'success': False, 'error': 'An FBA batch cannot exceed 500 item rows'}), 400

            client_token = ss_fba_schema._fba_trim(fba_meta.get('client_token'), 100)
            batch_name = ss_fba_schema._fba_trim(fba_meta.get('batch_name'), 120)
            if not batch_name:
                batch_name = 'FBA Prep ' + time.strftime('%Y-%m-%d %H:%M')
            shipment_id = ss_fba_schema._fba_trim(fba_meta.get('shipment_id'), 120)
            destination_fc = ss_fba_schema._fba_trim(fba_meta.get('destination_fc'), 80).upper()
            prep_owner = ss_fba_schema._fba_trim(fba_meta.get('prep_owner') or 'SELLER', 20).upper()
            label_owner = ss_fba_schema._fba_trim(fba_meta.get('label_owner') or 'SELLER', 20).upper()
            if prep_owner not in ('SELLER', 'AMAZON'):
                return jsonify({'success': False, 'error': 'Prep owner must be Seller or Amazon'}), 400
            if label_owner not in ('SELLER', 'AMAZON'):
                return jsonify({'success': False, 'error': 'Label owner must be Seller or Amazon'}), 400
            boxes_raw = fba_meta.get('boxes_planned')
            boxes_planned = None if boxes_raw in (None, '') else ss_normalization._strict_inventory_quantity(boxes_raw)
            if boxes_planned is not None and boxes_planned <= 0:
                return jsonify({'success': False, 'error': 'Planned boxes must be a positive whole number'}), 400
            batch_notes = ss_fba_schema._fba_trim(fba_meta.get('notes'), 2000)
            fba_session_id = ss_listing_settings._listingagent_parse_int(fba_meta.get('session_id'), None)
            if fba_session_id is not None and fba_session_id <= 0:
                fba_session_id = None

        if not clear_location and not to_location:
            return jsonify({'success': False, 'error': 'Missing destination shelf code'}), 400
        if not isinstance(items, list) or not items:
            return jsonify({'success': False, 'error': 'Missing items list'}), 400

        conn = sqlite3.connect('searchRack.db')
        conn.row_factory = sqlite3.Row
        ss_inventory_age._reconcile_inventory_age_batches(conn)
        cur = conn.cursor()

        cur.execute("PRAGMA table_info('SEARCHRACK')")
        cols = [r[1] for r in cur.fetchall()]
        cols_lower = {c.lower(): c for c in cols}
        id_col = cols_lower.get('id')
        barcode_col = cols_lower.get('barcode') or cols_lower.get('upc')
        title_col = cols_lower.get('title')
        qty_col = cols_lower.get('quantity') or cols_lower.get('qty')
        pos_col = cols_lower.get('item_position') or cols_lower.get('itemposition') or cols_lower.get('position')
        pic_col = cols_lower.get('pictureposition')

        if not barcode_col:
            return jsonify({'success': False, 'error': 'SEARCHRACK barcode column not found'}), 500
        if not pos_col and not pic_col:
            return jsonify({'success': False, 'error': 'SEARCHRACK location columns not found'}), 500

        id_lookup_col = id_col or 'rowid'
        skip_pk_cols = {'id', 'rowid'}
        if id_col:
            skip_pk_cols.add(id_col.lower())
        insert_cols = [c for c in cols if c.lower() not in skip_pk_cols]

        fba_batch_id = None
        fba_marketplaces = {}
        if fba_mode:
            ss_fba_schema._ensure_fba_prep_tables(cur)
            conn.commit()

            if client_token:
                existing_batch = cur.execute(
                    'SELECT id FROM fba_prep_batches WHERE client_token = ? LIMIT 1',
                    (client_token,)
                ).fetchone()
                if existing_batch:
                    existing_payload = ss_fba_inventory._fba_batch_payload(conn, existing_batch['id'])
                    existing_items = (existing_payload or {}).get('items') or []
                    return jsonify({
                        'success': True,
                        'idempotent': True,
                        'moved_items': sum(1 for row in existing_items if int(row.get('quantity_removed') or 0) > 0),
                        'moved_units': sum(int(row.get('quantity_removed') or 0) for row in existing_items),
                        'completed_items': len(existing_items),
                        'completed_units': sum(int(row.get('requested_quantity') or 0) for row in existing_items),
                        'skipped_items': 0,
                        'clear_location': True,
                        'item_results': [{
                            'index': int(row.get('item_index') or 0),
                            'barcode': row.get('barcode') or '',
                            'requested_qty': int(row.get('requested_quantity') or 0),
                            'moved_qty': int(row.get('quantity_removed') or 0),
                            'completed_qty': int(row.get('requested_quantity') or 0),
                            'success': True,
                            'fba_item_id': row.get('id'),
                        } for row in existing_items],
                        'fba_batch': existing_payload,
                    })

            if fba_session_id:
                saved_session = cur.execute(
                    '''
                    SELECT id, status, amazon_inbound_plan_id, amazon_state_json
                    FROM fba_prep_sessions WHERE id = ? LIMIT 1
                    ''',
                    (fba_session_id,)
                ).fetchone()
                if not saved_session or str(saved_session['status'] or '') != 'open':
                    return jsonify({'success': False, 'error': 'The selected FBA session is no longer open'}), 409
                amazon_inbound_plan_id = ss_fba_schema._fba_trim(saved_session['amazon_inbound_plan_id'], 38)
                if amazon_inbound_plan_id:
                    amazon_workflow = ss_fba_schema._fba_json_dict(saved_session['amazon_state_json'])
                    if not amazon_workflow.get('transport_confirmed'):
                        return jsonify({
                            'success': False,
                            'error': 'Confirm transportation with Amazon before removing this shipment from local inventory',
                        }), 409
                    shipment_id = amazon_inbound_plan_id
                    destination_ids = []
                    for shipment in amazon_workflow.get('shipments') or []:
                        destination = shipment.get('destination') if isinstance(shipment, dict) else {}
                        warehouse_id = ss_fba_schema._fba_trim((destination or {}).get('warehouseId'), 40).upper()
                        if warehouse_id and warehouse_id not in destination_ids:
                            destination_ids.append(warehouse_id)
                    if destination_ids:
                        destination_fc = ', '.join(destination_ids)

            created_at = ss_listing_checks._listagent_now_iso()
            cur.execute('''
                INSERT INTO fba_prep_batches (
                    client_token, session_id, amazon_inbound_plan_id, batch_name, shipment_id, destination_fc,
                    prep_owner, label_owner, boxes_planned, notes,
                    status, total_skus, total_units, created_at, completed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'processing', 0, 0, ?, NULL)
            ''', (
                client_token or None,
                fba_session_id,
                amazon_inbound_plan_id or None,
                batch_name,
                shipment_id or None,
                destination_fc or None,
                prep_owner,
                label_owner,
                boxes_planned,
                batch_notes or None,
                created_at,
            ))
            fba_batch_id = cur.lastrowid
            fba_marketplaces = ss_fba_inventory._fba_marketplace_status_for_barcodes([
                str((item or {}).get('barcode') or '').strip()
                for item in items
            ])

        rem_conn = sqlite3.connect('rackhistory.db')
        rem_cur = rem_conn.cursor()
        ss_inventory_history._ensure_removed_items_table(rem_cur)
        try:
            rem_cur.execute("PRAGMA table_info(removed_items)")
            rem_cols = [c[1] for c in rem_cur.fetchall()]
            if 'item_position' not in rem_cols:
                rem_cur.execute('ALTER TABLE removed_items ADD COLUMN item_position TEXT')
                rem_conn.commit()
        except Exception:
            pass

        from datetime import datetime
        moved_items = 0
        moved_units = 0
        completed_items = 0
        completed_units = 0
        skipped_items = 0
        item_results = []
        to_location_norm = to_location.lower() if to_location else ''
        removal_type = 'fba_removed' if fba_mode else ('inventoryremoved' if clear_location else 'locationmoved')
        target_location_for_logs = '' if clear_location else to_location
        history_order_id = f'FBA-{fba_batch_id}' if fba_mode and fba_batch_id else None

        def _log_location_history(*, barcode, title, quantity, row_id, old_quantity,
                                  new_quantity, item_position, from_position='', to_position=''):
            rem_cur.execute('''
                INSERT INTO removed_items (
                    order_id, barcode, title, quantity_removed, removed_at,
                    searchrack_id, old_quantity, new_quantity, removal_type,
                    item_position, from_position, to_position, event_status
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending')
            ''', (
                history_order_id,
                barcode,
                title,
                quantity,
                datetime.now().isoformat(),
                row_id,
                old_quantity,
                new_quantity,
                removal_type,
                item_position,
                from_position,
                to_position,
            ))

        def _location_match_rows(source_location_norm):
            if pos_col and pic_col:
                cur.execute(f'''
                    SELECT rowid AS _rowid_, *
                    FROM SEARCHRACK
                    WHERE LOWER(TRIM(COALESCE(NULLIF({pos_col}, ''), NULLIF({pic_col}, '')))) = ?
                ''', (source_location_norm,))
            elif pos_col:
                cur.execute(f'''
                    SELECT rowid AS _rowid_, *
                    FROM SEARCHRACK
                    WHERE LOWER(TRIM({pos_col})) = ?
                ''', (source_location_norm,))
            else:
                cur.execute(f'''
                    SELECT rowid AS _rowid_, *
                    FROM SEARCHRACK
                    WHERE LOWER(TRIM({pic_col})) = ?
                ''', (source_location_norm,))
            return [dict(r) for r in cur.fetchall()]

        for idx, raw_item in enumerate(items):
            row_result = {
                'index': idx,
                'barcode': str((raw_item or {}).get('barcode') or '').strip(),
                'from_location': str((raw_item or {}).get('from_location') or '').strip(),
                'from_locations': [],
                'requested_qty': 0,
                'moved_qty': 0,
                'success': False
            }
            try:
                barcode_raw = row_result['barcode']
                barcode_val = barcode_raw
                now = datetime.now().isoformat()
                from_location = row_result['from_location']
                raw_from_locations = (raw_item or {}).get('from_locations')
                fba_item_title = ss_fba_schema._fba_trim((raw_item or {}).get('title'), 500)
                fba_item_image = ss_fba_schema._fba_trim((raw_item or {}).get('image'), 1500)
                requested_qty = ss_normalization._strict_inventory_quantity((raw_item or {}).get('quantity', 1))
                if requested_qty is None or requested_qty <= 0:
                    row_result['error'] = 'Quantity must be a positive whole number'
                    skipped_items += 1
                    item_results.append(row_result)
                    continue
                row_result['requested_qty'] = requested_qty

                barcode_key = _movelocation_barcode_key(barcode_raw)
                source_locations = []
                source_seen = set()

                def _add_source_location(raw_loc):
                    loc = str(raw_loc or '').strip()
                    if not loc:
                        return
                    norm = loc.lower()
                    if norm in source_seen:
                        return
                    source_seen.add(norm)
                    source_locations.append(loc)

                if isinstance(raw_from_locations, list):
                    for raw_loc in raw_from_locations:
                        _add_source_location(raw_loc)
                elif isinstance(raw_from_locations, str):
                    text = raw_from_locations.strip()
                    if text:
                        if ',' in text:
                            for part in text.split(','):
                                _add_source_location(part)
                        else:
                            _add_source_location(text)
                _add_source_location(from_location)
                if requested_qty > 0 and len(source_locations) > requested_qty:
                    source_locations = source_locations[:requested_qty]
                    row_result['selection_limited'] = True
                row_result['from_locations'] = source_locations
                if source_locations:
                    row_result['from_location'] = ', '.join(source_locations)

                if not barcode_key:
                    row_result['error'] = 'Invalid barcode'
                    skipped_items += 1
                    item_results.append(row_result)
                    continue
                if not source_locations and not fba_mode:
                    row_result['error'] = 'Missing source location'
                    skipped_items += 1
                    item_results.append(row_result)
                    continue
                remaining = requested_qty
                already_removed_qty = max(
                    0,
                    min(
                        requested_qty,
                        ss_listing_settings._listingagent_parse_int((raw_item or {}).get('inventory_already_removed'), 0) or 0,
                    ),
                ) if fba_mode else 0
                remaining = max(0, requested_qty - already_removed_qty)
                moved_this_item = already_removed_qty
                source_warnings = []
                used_locations = []
                used_location_details = []
                if fba_mode and isinstance((raw_item or {}).get('removed_location_details'), list):
                    for raw_detail in (raw_item or {}).get('removed_location_details')[:100]:
                        if not isinstance(raw_detail, dict):
                            continue
                        detail_code = ss_fba_schema._fba_trim(raw_detail.get('code'), 120)
                        detail_quantity = max(0, ss_listing_settings._listingagent_parse_int(raw_detail.get('quantity'), 0) or 0)
                        if detail_code and detail_quantity:
                            used_location_details.append({'code': detail_code, 'quantity': detail_quantity})
                            used_locations.append(detail_code)

                for source_location in source_locations:
                    if remaining <= 0:
                        break

                    source_location_norm = source_location.lower().strip()
                    if not source_location_norm:
                        continue
                    if (not clear_location) and source_location_norm == to_location_norm:
                        source_warnings.append(f'Source {source_location} matches destination')
                        continue

                    source_rows = _location_match_rows(source_location_norm)
                    if not source_rows:
                        source_warnings.append(f'No rows found at source location {source_location}')
                        continue

                    matched_rows = []
                    for source_row in source_rows:
                        if _movelocation_barcode_key(source_row.get(barcode_col)) == barcode_key:
                            qty_here = max(0, _movelocation_parse_qty(source_row.get(qty_col) if qty_col else None, default=1))
                            source_row['_qty_for_move'] = qty_here
                            matched_rows.append(source_row)

                    if not matched_rows:
                        source_warnings.append(f'Barcode not found at {source_location}')
                        continue

                    for matched_row in matched_rows:
                        matched_row_id = (
                            matched_row.get(id_col) if id_col else matched_row.get('_rowid_')
                        )
                        cur.execute('''
                            SELECT MIN(received_at)
                            FROM inventory_age_batches
                            WHERE searchrack_id = ? AND quantity > 0
                        ''', (matched_row_id,))
                        oldest_row = cur.fetchone()
                        matched_row['_oldest_received_at'] = (
                            str(oldest_row[0] or '') if oldest_row else ''
                        )
                    matched_rows.sort(key=lambda r: (
                        r.get('_oldest_received_at') or '9999-12-31T23:59:59',
                        int(r.get(id_col) or r.get('_rowid_') or 0),
                    ))
                    available_qty = sum(int(r.get('_qty_for_move') or 0) for r in matched_rows)
                    if available_qty <= 0:
                        source_warnings.append(f'No available quantity at {source_location}')
                        continue

                    move_target_qty = min(remaining, available_qty)
                    moved_from_source = 0

                    for source_row in matched_rows:
                        if move_target_qty <= 0:
                            break

                        row_qty = int(source_row.get('_qty_for_move') or 0)
                        if row_qty <= 0:
                            continue
                        take_qty = min(row_qty, move_target_qty)
                        if take_qty <= 0:
                            continue

                        row_id = source_row.get(id_col) if id_col else source_row.get('_rowid_')
                        if row_id is None:
                            row_id = source_row.get('_rowid_')
                        if row_id is None:
                            continue

                        barcode_val = str(source_row.get(barcode_col) or barcode_raw or '').strip()
                        title_val = str(source_row.get(title_col) or '').strip() if title_col else ''
                        if not fba_item_title and title_val:
                            fba_item_title = title_val
                        now = datetime.now().isoformat()

                        if take_qty < row_qty and qty_col:
                            new_qty = row_qty - take_qty
                            cur.execute(
                                f"UPDATE SEARCHRACK SET {qty_col} = ? WHERE {id_lookup_col} = ?",
                                (new_qty, row_id)
                            )

                            if clear_location:
                                # Partial remove: just decrement qty at source, no locationless ghost row
                                ss_inventory_age._inventory_age_consume_fifo(cur, row_id, take_qty)
                                _log_location_history(
                                    barcode=barcode_val, title=title_val, quantity=take_qty,
                                    row_id=row_id, old_quantity=row_qty, new_quantity=new_qty,
                                    item_position=source_location, from_position=source_location,
                                )
                            else:
                                # Partial move: insert a new row at the destination
                                placeholders = ','.join('?' for _ in insert_cols)
                                insert_values = []
                                for c in insert_cols:
                                    val = source_row.get(c)
                                    if qty_col and c.lower() == qty_col.lower():
                                        val = take_qty
                                    if pos_col and c.lower() == pos_col.lower():
                                        val = to_location
                                    if pic_col and c.lower() == pic_col.lower():
                                        val = ''
                                    insert_values.append(val)

                                cur.execute(
                                    f"INSERT INTO SEARCHRACK ({', '.join(insert_cols)}) VALUES ({placeholders})",
                                    tuple(insert_values)
                                )
                                new_id = cur.lastrowid
                                ss_inventory_age._inventory_age_transfer_fifo(
                                    cur, row_id, new_id, take_qty, barcode_val
                                )

                                _log_location_history(
                                    barcode=barcode_val, title=title_val, quantity=take_qty,
                                    row_id=row_id, old_quantity=row_qty, new_quantity=new_qty,
                                    item_position=source_location, from_position=source_location,
                                    to_position=target_location_for_logs,
                                )
                                _log_location_history(
                                    barcode=barcode_val, title=title_val, quantity=take_qty,
                                    row_id=new_id, old_quantity=0, new_quantity=take_qty,
                                    item_position=target_location_for_logs, from_position=source_location,
                                    to_position=target_location_for_logs,
                                )
                        else:
                            if clear_location:
                                # Full remove: the zero-quantity trigger snapshots and deletes this row.
                                if qty_col and pos_col and pic_col:
                                    cur.execute(
                                        f"UPDATE SEARCHRACK SET {qty_col} = 0, {pos_col} = '', {pic_col} = '' WHERE {id_lookup_col} = ?",
                                        (row_id,)
                                    )
                                elif qty_col and pos_col:
                                    cur.execute(
                                        f"UPDATE SEARCHRACK SET {qty_col} = 0, {pos_col} = '' WHERE {id_lookup_col} = ?",
                                        (row_id,)
                                    )
                                elif qty_col and pic_col:
                                    cur.execute(
                                        f"UPDATE SEARCHRACK SET {qty_col} = 0, {pic_col} = '' WHERE {id_lookup_col} = ?",
                                        (row_id,)
                                    )
                                elif qty_col:
                                    cur.execute(
                                        f"UPDATE SEARCHRACK SET {qty_col} = 0 WHERE {id_lookup_col} = ?",
                                        (row_id,)
                                    )
                                elif pos_col and pic_col:
                                    cur.execute(
                                        f"UPDATE SEARCHRACK SET {pos_col} = '', {pic_col} = '' WHERE {id_lookup_col} = ?",
                                        (row_id,)
                                    )
                                elif pos_col:
                                    cur.execute(
                                        f"UPDATE SEARCHRACK SET {pos_col} = '' WHERE {id_lookup_col} = ?",
                                        (row_id,)
                                    )
                                else:
                                    cur.execute(
                                        f"UPDATE SEARCHRACK SET {pic_col} = '' WHERE {id_lookup_col} = ?",
                                        (row_id,)
                                    )
                                ss_inventory_age._inventory_age_consume_fifo(cur, row_id, take_qty)
                                _log_location_history(
                                    barcode=barcode_val, title=title_val, quantity=take_qty,
                                    row_id=row_id, old_quantity=take_qty, new_quantity=0,
                                    item_position=source_location, from_position=source_location,
                                )
                            else:
                                # Full move: update location in-place
                                if pos_col and pic_col:
                                    cur.execute(
                                        f"UPDATE SEARCHRACK SET {pos_col} = ?, {pic_col} = ? WHERE {id_lookup_col} = ?",
                                        (to_location, '', row_id)
                                    )
                                elif pos_col:
                                    cur.execute(
                                        f"UPDATE SEARCHRACK SET {pos_col} = ? WHERE {id_lookup_col} = ?",
                                        (to_location, row_id)
                                    )
                                else:
                                    cur.execute(
                                        f"UPDATE SEARCHRACK SET {pic_col} = ? WHERE {id_lookup_col} = ?",
                                        (to_location, row_id)
                                    )
                                _log_location_history(
                                    barcode=barcode_val, title=title_val, quantity=take_qty,
                                    row_id=row_id, old_quantity=take_qty, new_quantity=0,
                                    item_position=source_location, from_position=source_location,
                                    to_position=target_location_for_logs,
                                )
                                _log_location_history(
                                    barcode=barcode_val, title=title_val, quantity=take_qty,
                                    row_id=row_id, old_quantity=0, new_quantity=take_qty,
                                    item_position=target_location_for_logs, from_position=source_location,
                                    to_position=target_location_for_logs,
                                )

                        move_target_qty -= take_qty
                        remaining -= take_qty
                        moved_this_item += take_qty
                        moved_from_source += take_qty

                    if moved_from_source > 0:
                        used_locations.append(source_location)
                        used_location_details.append({
                            'code': source_location,
                            'quantity': int(moved_from_source),
                        })

                row_result['moved_qty'] = moved_this_item
                if used_locations:
                    row_result['used_locations'] = used_locations
                if moved_this_item > 0 or fba_mode:
                    row_result['success'] = True
                    if moved_this_item > 0:
                        moved_items += 1
                        moved_units += moved_this_item

                    if fba_mode:
                        completed_items += 1
                        completed_units += requested_qty
                        row_result['completed_qty'] = requested_qty
                        row_result['untracked_qty'] = max(0, requested_qty - moved_this_item)
                        expiration_date = ss_fba_schema._fba_trim((raw_item or {}).get('expiration_date'), 10)
                        if expiration_date and not re.fullmatch(r'\d{4}-\d{2}-\d{2}', expiration_date):
                            raise ValueError('Expiration date must use YYYY-MM-DD')

                        prep_type = ss_fba_schema._fba_trim((raw_item or {}).get('prep_type') or 'none', 40).lower()
                        allowed_prep_types = {
                            'none', 'poly_bag', 'bubble_wrap', 'taping',
                            'opaque_bag', 'suffocation_label', 'set_bundle', 'other'
                        }
                        if prep_type not in allowed_prep_types:
                            raise ValueError('Invalid FBA prep type')

                        item_label_owner = ss_fba_schema._fba_trim(
                            (raw_item or {}).get('label_owner') or label_owner,
                            20
                        ).upper()
                        if item_label_owner not in ('SELLER', 'AMAZON'):
                            raise ValueError('Item label owner must be Seller or Amazon')

                        inventory_state = ss_fba_inventory._fba_inventory_state_for_barcode(cur, barcode_raw)
                        marketplace_state = fba_marketplaces.get(barcode_key) or {}
                        listed_ebay = bool(marketplace_state.get('listed_ebay'))
                        listed_amazon = bool(marketplace_state.get('listed_amazon'))
                        listing_links = marketplace_state.get('links') or []
                        amazon_defaults = {}
                        if not (raw_item or {}).get('asin') or not (raw_item or {}).get('seller_sku'):
                            amazon_defaults = ss_fba_inventory._fba_local_amazon_listing(barcode_raw)

                        cur.execute('''
                            INSERT INTO fba_prep_items (
                                batch_id, item_index, barcode, title, image,
                                requested_quantity, quantity_removed, source_locations_json,
                                remaining_inventory_qty, remaining_locations_json,
                                asin, seller_sku, fnsku, item_condition, prep_type,
                                label_owner, expiration_date, box_number, notes,
                                listed_ebay, listed_amazon, listing_links_json, created_at
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        ''', (
                            fba_batch_id,
                            idx,
                            barcode_val or barcode_raw,
                            fba_item_title or barcode_val or barcode_raw,
                            fba_item_image or None,
                            requested_qty,
                            moved_this_item,
                            json.dumps(used_location_details, ensure_ascii=False),
                            inventory_state['quantity'],
                            json.dumps(inventory_state['locations'], ensure_ascii=False),
                            ss_fba_schema._fba_trim((raw_item or {}).get('asin') or amazon_defaults.get('asin'), 30) or None,
                            ss_fba_schema._fba_trim((raw_item or {}).get('seller_sku') or amazon_defaults.get('seller_sku'), 120) or None,
                            ss_fba_schema._fba_trim((raw_item or {}).get('fnsku') or (raw_item or {}).get('amazon_fnsku'), 80) or None,
                            ss_fba_schema._fba_trim((raw_item or {}).get('condition') or amazon_defaults.get('condition'), 80) or None,
                            prep_type,
                            item_label_owner,
                            expiration_date or None,
                            ss_fba_schema._fba_trim((raw_item or {}).get('box_number'), 40) or None,
                            ss_fba_schema._fba_trim((raw_item or {}).get('notes'), 1000) or None,
                            1 if listed_ebay else 0,
                            1 if listed_amazon else 0,
                            json.dumps(listing_links, ensure_ascii=False),
                            now,
                        ))
                        fba_item_id = cur.lastrowid
                        review_reasons = ss_fba_inventory._fba_review_reasons(
                            remaining_quantity=inventory_state['quantity'],
                            listed_ebay=listed_ebay,
                            listed_amazon=listed_amazon,
                        )
                        review_id = None
                        if review_reasons:
                            cur.execute('''
                                INSERT INTO fba_listing_reviews (
                                    fba_item_id, batch_id, barcode, title, quantity_removed,
                                    remaining_inventory_qty, remaining_locations_json,
                                    listed_ebay, listed_amazon, listing_links_json,
                                    reasons_json, status, reviewer_note, created_at, reviewed_at
                                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'open', NULL, ?, NULL)
                            ''', (
                                fba_item_id,
                                fba_batch_id,
                                barcode_val or barcode_raw,
                                fba_item_title or barcode_val or barcode_raw,
                                moved_this_item,
                                inventory_state['quantity'],
                                json.dumps(inventory_state['locations'], ensure_ascii=False),
                                1 if listed_ebay else 0,
                                1 if listed_amazon else 0,
                                json.dumps(listing_links, ensure_ascii=False),
                                json.dumps(review_reasons, ensure_ascii=False),
                                now,
                            ))
                            review_id = cur.lastrowid

                        row_result.update({
                            'fba_item_id': fba_item_id,
                            'review_id': review_id,
                            'used_location_details': used_location_details,
                            'remaining_inventory_qty': inventory_state['quantity'],
                            'remaining_locations': inventory_state['locations'],
                            'listed_ebay': listed_ebay,
                            'listed_amazon': listed_amazon,
                            'listing_links': listing_links,
                            'review_reasons': review_reasons,
                        })

                    if moved_this_item < requested_qty:
                        detail = '; '.join(source_warnings[:3]) if source_warnings else ''
                        if fba_mode:
                            row_result['warning'] = (
                                f'{requested_qty} sent to FBA; {moved_this_item} removed from warehouse'
                                + (f' ({detail})' if detail else '')
                            )
                        else:
                            row_result['error'] = f'Only moved {moved_this_item} of {requested_qty}' + (f' ({detail})' if detail else '')
                else:
                    row_result['error'] = '; '.join(source_warnings) if source_warnings else ('Clear failed' if clear_location else 'Move failed')
                    skipped_items += 1
            except Exception as row_err:
                row_result['error'] = ss_errors._safe_error(row_err)
                skipped_items += 1
                raise RuntimeError(f'Move-location item {idx + 1} failed; all inventory changes were rolled back') from row_err

            item_results.append(row_result)

        if fba_mode and fba_batch_id:
            if completed_units > 0:
                cur.execute('''
                    UPDATE fba_prep_batches
                    SET status = 'completed', total_skus = ?, total_units = ?, completed_at = ?
                    WHERE id = ?
                ''', (completed_items, completed_units, ss_listing_checks._listagent_now_iso(), fba_batch_id))
                fully_completed = bool(
                    skipped_items == 0 and
                    item_results and
                    all(
                        bool(row.get('success')) and
                        int(row.get('completed_qty') or 0) >= int(row.get('requested_qty') or 0)
                        for row in item_results
                    )
                )
                if fba_session_id and fully_completed:
                    completed_at = ss_listing_checks._listagent_now_iso()
                    cur.execute('''
                        UPDATE fba_prep_sessions
                        SET status = 'completed', completed_batch_id = ?,
                            completed_at = ?, updated_at = ?
                        WHERE id = ? AND status = 'open'
                    ''', (fba_batch_id, completed_at, completed_at, fba_session_id))
            else:
                cur.execute('DELETE FROM fba_prep_batches WHERE id = ?', (fba_batch_id,))

        rem_conn.commit()
        conn.commit()
        if moved_units > 0:
            try:
                ss_caching.update_data_version()
                ss_caching._invalidate_searchrack_cache()
            except Exception as cache_error:
                ss_config.logger.warning('Move-location cache refresh deferred: %s', cache_error)

        if (fba_mode and completed_units <= 0) or (not fba_mode and moved_units <= 0):
            return jsonify({
                'success': False,
                'error': 'No FBA items were completed' if fba_mode else ('No items were updated' if clear_location else 'No items were moved'),
                'moved_items': moved_items,
                'moved_units': moved_units,
                'completed_items': completed_items,
                'completed_units': completed_units,
                'skipped_items': skipped_items,
                'item_results': item_results,
                'clear_location': clear_location
            }), 400

        response_payload = {
            'success': True,
            'moved_items': moved_items,
            'moved_units': moved_units,
            'completed_items': completed_items,
            'completed_units': completed_units,
            'skipped_items': skipped_items,
            'item_results': item_results,
            'clear_location': clear_location
        }
        if fba_mode and fba_batch_id:
            response_payload['fba_batch'] = ss_fba_inventory._fba_batch_payload(conn, fba_batch_id)
        return jsonify(response_payload)
    except Exception as e:
        for connection in (rem_conn, conn):
            try:
                if connection is not None:
                    connection.rollback()
            except Exception:
                pass
        return jsonify({'success': False, 'error': ss_errors._safe_error(e)}), 500
    finally:
        try:
            if conn is not None:
                conn.close()
        except Exception:
            pass
        try:
            if rem_conn is not None:
                rem_conn.close()
        except Exception:
            pass


def api_movelocation_bulk_cleanup():
    """Reduce SEARCHRACK quantities from selected source locations for sold bulk-manifest barcodes."""
    conn = None
    rem_conn = None
    try:
        data = request.get_json() or {}
        items = data.get('items') or []
        if not isinstance(items, list) or not items:
            return jsonify({'success': False, 'error': 'Missing items list'}), 400

        manifest_ids = []
        raw_manifest_ids = data.get('manifest_ids')
        if isinstance(raw_manifest_ids, list):
            seen_manifest_ids = set()
            for raw_id in raw_manifest_ids:
                manifest_id = ss_normalization._coerce_int(raw_id, 0)
                if manifest_id <= 0 or manifest_id in seen_manifest_ids:
                    continue
                seen_manifest_ids.add(manifest_id)
                manifest_ids.append(manifest_id)
        elif raw_manifest_ids is not None:
            manifest_ids = ss_bulk_manifest._bulk_manifest_parse_ids(raw_manifest_ids)

        conn = sqlite3.connect('searchRack.db')
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        schema = ss_warehouse_matching._searchrack_removal_schema(cur)

        id_lookup_col = schema.get('id_col') or 'rowid'
        qty_col = schema.get('qty_col')
        if not qty_col:
            return jsonify({'success': False, 'error': 'SEARCHRACK quantity column not found'}), 500

        rem_conn = sqlite3.connect('rackhistory.db')
        rem_cur = rem_conn.cursor()
        ss_inventory_history._ensure_removed_items_table(rem_cur)
        try:
            rem_cur.execute("PRAGMA table_info(removed_items)")
            rem_cols = [c[1] for c in rem_cur.fetchall()]
            if 'item_position' not in rem_cols:
                rem_cur.execute('ALTER TABLE removed_items ADD COLUMN item_position TEXT')
                rem_conn.commit()
        except Exception:
            pass

        now_iso = datetime.datetime.now().isoformat()
        order_ref = f"bulk-manifest:{','.join(str(x) for x in manifest_ids)}" if manifest_ids else None

        removed_items = 0
        removed_units = 0
        skipped_items = 0
        requested_units = 0
        item_results = []

        for idx, raw_item in enumerate(items):
            row_result = {
                'index': idx,
                'barcode': str((raw_item or {}).get('barcode') or '').strip(),
                'from_location': str((raw_item or {}).get('from_location') or '').strip(),
                'from_locations': [],
                'requested_qty': 0,
                'moved_qty': 0,
                'removed_qty': 0,
                'success': False
            }
            try:
                barcode_raw = row_result['barcode']
                raw_requested_qty = (
                    (raw_item or {}).get('quantity')
                    if (raw_item or {}).get('quantity') is not None
                    else (raw_item or {}).get('qty', 1)
                )
                requested_qty = ss_normalization._strict_inventory_quantity(raw_requested_qty)
                if requested_qty is None or requested_qty <= 0:
                    row_result['error'] = 'Quantity must be a positive whole number'
                    skipped_items += 1
                    item_results.append(row_result)
                    continue
                requested_units += requested_qty
                row_result['requested_qty'] = requested_qty

                raw_from_locations = (raw_item or {}).get('from_locations')
                source_locations = []
                source_seen = set()

                def _add_source_location(raw_loc):
                    loc = str(raw_loc or '').strip()
                    if not loc:
                        return
                    loc_key = ss_warehouse_matching._sold_location_key(loc)
                    if loc_key in source_seen:
                        return
                    source_seen.add(loc_key)
                    source_locations.append(loc)

                if isinstance(raw_from_locations, list):
                    for raw_loc in raw_from_locations:
                        _add_source_location(raw_loc)
                elif isinstance(raw_from_locations, str):
                    text = raw_from_locations.strip()
                    if text:
                        if ',' in text:
                            for part in text.split(','):
                                _add_source_location(part)
                        else:
                            _add_source_location(text)
                _add_source_location((raw_item or {}).get('from_location'))

                if requested_qty > 0 and len(source_locations) > requested_qty:
                    source_locations = source_locations[:requested_qty]
                    row_result['selection_limited'] = True
                row_result['from_locations'] = source_locations
                if source_locations:
                    row_result['from_location'] = ', '.join(source_locations)

                barcode_key = ss_warehouse_matching._sold_removal_barcode_key(barcode_raw)
                if not barcode_key:
                    row_result['error'] = 'Invalid barcode'
                    skipped_items += 1
                    item_results.append(row_result)
                    continue
                if not source_locations:
                    row_result['error'] = 'Missing source location'
                    skipped_items += 1
                    item_results.append(row_result)
                    continue

                matches = ss_warehouse_matching._searchrack_matches_for_barcode(
                    cur,
                    barcode_raw,
                    schema=schema,
                    include_zero=False
                )
                if not matches:
                    row_result['error'] = f'Barcode not found in SEARCHRACK: {barcode_raw}'
                    skipped_items += 1
                    item_results.append(row_result)
                    continue

                matches_by_loc = {}
                for match_row in matches:
                    loc_key = ss_warehouse_matching._sold_location_key(match_row.get('location_code'))
                    bucket = matches_by_loc.get(loc_key)
                    if bucket is None:
                        bucket = []
                        matches_by_loc[loc_key] = bucket
                    bucket.append(match_row)

                for loc_rows in matches_by_loc.values():
                    loc_rows.sort(key=lambda r: int(r.get('quantity') or 0), reverse=True)

                remaining = requested_qty
                removed_this_item = 0
                selected_keys = [ss_warehouse_matching._sold_location_key(loc) for loc in source_locations]
                source_warnings = []
                used_locations = []

                for source_location, source_key in zip(source_locations, selected_keys):
                    if remaining <= 0:
                        break

                    loc_rows = matches_by_loc.get(source_key) or []
                    if not loc_rows:
                        source_warnings.append(f'Barcode not found at {source_location}')
                        continue

                    removed_from_source = 0
                    for match_row in loc_rows:
                        if remaining <= 0:
                            break
                        current_qty = max(0, ss_normalization._coerce_int(match_row.get('quantity'), 0))
                        if current_qty <= 0:
                            continue
                        take_qty = min(current_qty, remaining)
                        if take_qty <= 0:
                            continue

                        row_id = max(0, ss_normalization._coerce_int(match_row.get('id'), 0))
                        if row_id <= 0:
                            continue

                        new_qty = max(0, current_qty - take_qty)
                        cur.execute(
                            f'UPDATE SEARCHRACK SET {qty_col} = ? WHERE {id_lookup_col} = ?',
                            (new_qty, row_id)
                        )

                        rem_cur.execute('''
                            INSERT INTO removed_items
                            (order_id, barcode, title, quantity_removed, removed_at, searchrack_id, old_quantity, new_quantity, removal_type, item_position, event_status)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending')
                        ''', (
                            order_ref,
                            str(match_row.get('barcode') or barcode_raw),
                            str(match_row.get('title') or match_row.get('barcode') or barcode_raw),
                            take_qty,
                            now_iso,
                            row_id,
                            current_qty,
                            new_qty,
                            'bulk_manifest_cleanup',
                            str(match_row.get('location_code') or source_location or '')
                        ))

                        match_row['quantity'] = new_qty
                        remaining -= take_qty
                        removed_this_item += take_qty
                        removed_from_source += take_qty

                    if removed_from_source > 0:
                        used_locations.append(source_location)

                row_result['moved_qty'] = removed_this_item
                row_result['removed_qty'] = removed_this_item
                if used_locations:
                    row_result['used_locations'] = used_locations
                if removed_this_item > 0:
                    row_result['success'] = True
                    removed_items += 1
                    removed_units += removed_this_item
                    if removed_this_item < requested_qty:
                        detail = '; '.join(source_warnings[:3]) if source_warnings else ''
                        row_result['error'] = f'Only removed {removed_this_item} of {requested_qty}' + (f' ({detail})' if detail else '')
                else:
                    row_result['error'] = '; '.join(source_warnings) if source_warnings else 'No quantity removed'
                    skipped_items += 1
            except Exception as row_err:
                row_result['error'] = ss_errors._safe_error(row_err, 'movelocation_bulk_cleanup_row')
                skipped_items += 1
                raise RuntimeError(f'Bulk cleanup item {idx + 1} failed; all inventory changes were rolled back') from row_err

            item_results.append(row_result)

        rem_conn.commit()
        conn.commit()
        if removed_units > 0:
            try:
                ss_caching.update_data_version()
                ss_caching._invalidate_searchrack_cache()
            except Exception as cache_error:
                ss_config.logger.warning('Bulk cleanup cache refresh deferred: %s', cache_error)

        cleanup_status = ''
        cleanup_status_error = ''
        if manifest_ids:
            cleanup_status = 'cleanup_complete' if removed_units >= requested_units and skipped_items == 0 else ('cleanup_partial' if removed_units > 0 else '')
            if cleanup_status:
                cleanup_note = f'Removed {removed_units}/{requested_units} unit(s) via move-location bulk cleanup'
                try:
                    with ss_database.db_connection('marketplace.db') as m_conn:
                        m_cur = m_conn.cursor()
                        ss_bulk_manifest._ensure_bulk_manifest_tables(m_cur)
                        placeholders = ','.join('?' for _ in manifest_ids)
                        params = [cleanup_status, now_iso, cleanup_note, now_iso]
                        params.extend(manifest_ids)
                        m_cur.execute(f'''
                            UPDATE bulk_manifests
                            SET status = ?,
                                cleanup_at = ?,
                                cleanup_note = ?,
                                updated_at = ?
                            WHERE id IN ({placeholders})
                        ''', tuple(params))
                except Exception as status_error:
                    cleanup_status_error = 'Inventory was updated, but the manifest status refresh was deferred.'
                    ss_config.logger.warning('Bulk manifest status refresh deferred: %s', status_error)

        if removed_units <= 0:
            return jsonify({
                'success': False,
                'error': 'No quantities were removed from inventory',
                'moved_items': removed_items,
                'moved_units': removed_units,
                'removed_items': removed_items,
                'removed_units': removed_units,
                'requested_units': requested_units,
                'skipped_items': skipped_items,
                'item_results': item_results,
                'manifest_ids': manifest_ids,
                'manifest_cleanup_status': cleanup_status,
                'warning': cleanup_status_error,
            }), 400

        return jsonify({
            'success': True,
            'moved_items': removed_items,
            'moved_units': removed_units,
            'removed_items': removed_items,
            'removed_units': removed_units,
            'requested_units': requested_units,
            'skipped_items': skipped_items,
            'item_results': item_results,
            'manifest_ids': manifest_ids,
            'manifest_cleanup_status': cleanup_status,
            'warning': cleanup_status_error,
        })
    except Exception as e:
        for connection in (rem_conn, conn):
            try:
                if connection is not None:
                    connection.rollback()
            except Exception:
                pass
        return jsonify({'success': False, 'error': ss_errors._safe_error(e, 'movelocation_bulk_cleanup')}), 500
    finally:
        try:
            if conn is not None:
                conn.close()
        except Exception:
            pass
        try:
            if rem_conn is not None:
                rem_conn.close()
        except Exception:
            pass


def api_location_items():
    """Get items for a specific shelf/location from searchRack.db"""
    try:
        location = (request.args.get('location') or '').strip()
        if not location:
            return jsonify({'success': False, 'error': 'Missing location'}), 400

        conn = sqlite3.connect('searchRack.db')
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute('''
            SELECT ID, TITLE, BARCODE, QUANTITY, IMAGE, IMAGES
            FROM SEARCHRACK
            WHERE LOWER(TRIM(ITEM_POSITION)) = LOWER(TRIM(?))
              AND COALESCE(CAST(QUANTITY AS INTEGER), 0) > 0
            ORDER BY TITLE
        ''', (location,))
        items = []
        for row in cur.fetchall():
            qty_val = row['QUANTITY'] if 'QUANTITY' in row.keys() else None
            try:
                qty = int(qty_val) if qty_val is not None and str(qty_val).strip() != '' else 1
            except Exception:
                qty = 1
            image_val = row['IMAGE'] or ''
            if not image_val:
                images_raw = row['IMAGES'] if 'IMAGES' in row.keys() else ''
                try:
                    if images_raw and str(images_raw).strip().startswith('['):
                        parsed = json.loads(images_raw)
                        if isinstance(parsed, list) and parsed:
                            image_val = parsed[0]
                except Exception:
                    pass
            items.append({
                'id': row['ID'],
                'title': row['TITLE'] or '',
                'barcode': row['BARCODE'] or '',
                'quantity': qty,
                'image': image_val or row['IMAGES'] or ''
            })
        return jsonify({'success': True, 'items': items})
    except Exception as e:
        return jsonify({'success': False, 'error': ss_errors._safe_error(e)}), 500
    finally:
        try:
            conn.close()
        except Exception:
            pass


def api_move_location():
    """Move searchRack items from one location to another and log history"""
    data = request.get_json() or {}
    from_location = (data.get('from_location') or '').strip()
    to_location = (data.get('to_location') or '').strip()
    item_ids = data.get('item_ids') or []

    if not from_location or not to_location or not item_ids:
        return jsonify({'success': False, 'error': 'Missing from_location, to_location, or item_ids'}), 400
    if not isinstance(item_ids, list):
        return jsonify({'success': False, 'error': 'item_ids must be a list'}), 400
    if from_location.lower() == to_location.lower():
        return jsonify({'success': False, 'error': 'Start and destination locations are the same'}), 400

    moved = 0
    skipped = 0
    conn = None
    rem_conn = None

    try:
        conn = sqlite3.connect('searchRack.db')
        conn.row_factory = sqlite3.Row
        ss_inventory_age._reconcile_inventory_age_batches(conn)
        cur = conn.cursor()

        # Determine PK and picture column names
        cur.execute("PRAGMA table_info('SEARCHRACK')")
        cols = [r[1] for r in cur.fetchall()]
        id_col = 'ID' if 'ID' in cols else ('id' if 'id' in cols else 'rowid')
        pic_col = 'PICTUREPOSITION' if 'PICTUREPOSITION' in cols else None
        if not pic_col:
            for c in cols:
                if c.lower() == 'pictureposition':
                    pic_col = c
                    break

        # History logging
        rem_conn = sqlite3.connect('rackhistory.db')
        rem_cur = rem_conn.cursor()
        ss_inventory_history._ensure_removed_items_table(rem_cur)
        # Ensure item_position column exists for older schemas
        try:
            rem_cur.execute("PRAGMA table_info(removed_items)")
            rem_cols = [c[1] for c in rem_cur.fetchall()]
            if 'item_position' not in rem_cols:
                rem_cur.execute('ALTER TABLE removed_items ADD COLUMN item_position TEXT')
                rem_conn.commit()
        except Exception:
            pass

        def _parse_qty(row_dict):
            for qc in ['QUANTITY', 'Quantity', 'quantity', 'Qty', 'QTY']:
                if qc in row_dict and row_dict.get(qc) is not None:
                    parsed = ss_normalization._strict_inventory_quantity(row_dict.get(qc))
                    return parsed if parsed is not None else 0
            return 0

        def _find_qty_col():
            for qc in ['QUANTITY', 'Quantity', 'quantity', 'Qty', 'QTY']:
                if qc in cols:
                    return qc
            return None

        from_norm = from_location.strip().lower()

        from datetime import datetime
        qty_col_name = _find_qty_col()
        if not qty_col_name:
            return jsonify({'success': False, 'error': 'SEARCHRACK quantity column not found'}), 500
        for raw_entry in item_ids:
            try:
                move_qty = None
                raw_id = raw_entry
                if isinstance(raw_entry, dict):
                    raw_id = raw_entry.get('id') or raw_entry.get('item_id') or raw_entry.get('ID')
                    move_qty = raw_entry.get('qty') if raw_entry.get('qty') is not None else raw_entry.get('quantity')
                if raw_id is None or raw_id == '':
                    skipped += 1
                    continue

                cur.execute(f"SELECT * FROM SEARCHRACK WHERE {id_col} = ?", (raw_id,))
                row = cur.fetchone()
                if not row:
                    skipped += 1
                    continue

                row_dict = dict(row)
                row_loc = (row_dict.get('ITEM_POSITION') or row_dict.get('item_position') or '').strip()
                if row_loc.lower() != from_norm:
                    skipped += 1
                    continue

                barcode = row_dict.get('BARCODE') or row_dict.get('barcode') or ''
                title = row_dict.get('TITLE') or row_dict.get('title') or ''
                qty = _parse_qty(row_dict)
                move_qty_int = ss_normalization._strict_inventory_quantity(move_qty) if move_qty is not None else qty
                if move_qty_int is None:
                    skipped += 1
                    continue
                if move_qty_int < 1 or move_qty_int > qty:
                    skipped += 1
                    continue
                now = datetime.now().isoformat()

                if move_qty_int < qty and qty_col_name:
                    # Partial move: decrement original qty and insert new row at destination
                    new_qty = qty - move_qty_int
                    cur.execute(f"UPDATE SEARCHRACK SET {qty_col_name} = ? WHERE {id_col} = ?", (new_qty, raw_id))

                    # Build new row from existing with updated quantity and location
                    col_names = [c for c in cols if c not in (id_col, 'id', 'ID', 'Id', 'rowid')]
                    placeholders = ','.join('?' for _ in col_names)
                    vals = []
                    for c in col_names:
                        val = row_dict.get(c)
                        if c == qty_col_name:
                            val = move_qty_int
                        if c.lower() == 'pictureposition':
                            val = ''
                        if c.lower() == 'item_position' or c == 'ITEM_POSITION' or c.lower() == 'itemposition':
                            val = to_location
                        vals.append(val)
                    cur.execute(f"INSERT INTO SEARCHRACK ({', '.join(col_names)}) VALUES ({placeholders})", tuple(vals))
                    new_id = cur.lastrowid
                    ss_inventory_age._inventory_age_transfer_fifo(
                        cur, raw_id, new_id, move_qty_int, barcode
                    )

                    # Log as a location move (removed from old + added to new)
                    rem_cur.execute('''
                        INSERT INTO removed_items
                        (order_id, barcode, title, quantity_removed, removed_at, searchrack_id,
                         old_quantity, new_quantity, removal_type, item_position, event_status)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending')
                    ''', (None, barcode, title, move_qty_int, now, raw_id, qty, new_qty, 'locationmoved', from_location))

                    rem_cur.execute('''
                        INSERT INTO removed_items
                        (order_id, barcode, title, quantity_removed, removed_at, searchrack_id,
                         old_quantity, new_quantity, removal_type, item_position, event_status)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending')
                    ''', (None, barcode, title, move_qty_int, now, new_id, 0, move_qty_int, 'locationmoved', to_location))
                else:
                    # Full move: update row location (clear pictureposition if present)
                    if pic_col:
                        cur.execute(f"UPDATE SEARCHRACK SET ITEM_POSITION = ?, {pic_col} = ? WHERE {id_col} = ?", (to_location, '', raw_id))
                    else:
                        cur.execute(f"UPDATE SEARCHRACK SET ITEM_POSITION = ? WHERE {id_col} = ?", (to_location, raw_id))

                    # Log as a location move (removed from old + added to new)
                    rem_cur.execute('''
                        INSERT INTO removed_items
                        (order_id, barcode, title, quantity_removed, removed_at, searchrack_id,
                         old_quantity, new_quantity, removal_type, item_position, event_status)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending')
                    ''', (None, barcode, title, qty, now, raw_id, qty, 0, 'locationmoved', from_location))

                    rem_cur.execute('''
                        INSERT INTO removed_items
                        (order_id, barcode, title, quantity_removed, removed_at, searchrack_id,
                         old_quantity, new_quantity, removal_type, item_position, event_status)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending')
                    ''', (None, barcode, title, qty, now, raw_id, 0, qty, 'locationmoved', to_location))

                moved += 1
            except Exception as row_error:
                skipped += 1
                raise RuntimeError('Legacy move-location failed; all inventory changes were rolled back') from row_error

        rem_conn.commit()
        conn.commit()

        # Update data version for cache invalidation
        try:
            ss_caching.update_data_version()
            ss_caching._invalidate_searchrack_cache()
        except Exception as cache_error:
            ss_config.logger.warning('Legacy move-location cache refresh deferred: %s', cache_error)

        return jsonify({'success': True, 'moved': moved, 'skipped': skipped})
    except Exception as e:
        for connection in (rem_conn, conn):
            try:
                if connection is not None:
                    connection.rollback()
            except Exception:
                pass
        return jsonify({'success': False, 'error': ss_errors._safe_error(e)}), 500
    finally:
        try:
            conn.close()
        except Exception:
            pass
        try:
            rem_conn.close()
        except Exception:
            pass



