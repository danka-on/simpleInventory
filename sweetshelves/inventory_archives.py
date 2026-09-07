"""Inventory archives for Sweet Shelves."""

import os
import sqlite3
from flask import jsonify, render_template, request, url_for
from . import (
    errors as ss_errors, inventory_cleanup as ss_inventory_cleanup, inventory_history as
    ss_inventory_history, prep_diagnostics as ss_prep_diagnostics, prep_media as ss_prep_media,
    prep_schema as ss_prep_schema, runtime as ss_runtime, warehouse_locations as ss_warehouse_locations,
)


def trash_manager_page():
    return render_template('trash_manager.html')


def api_trash_settings():
    if request.method == 'GET':
        return jsonify({'retention_days': ss_prep_media._trash_retention_days()})
    try:
        data = request.get_json() or {}
        days = int(data.get('retention_days'))
        ok = ss_prep_media._set_trash_retention_days(days)
        return jsonify({'success': ok, 'retention_days': ss_prep_media._trash_retention_days()})
    except Exception as e:
        return jsonify({'success': False, 'error': ss_errors._safe_error(e)}), 500


def api_trash_list():
    conn = None
    try:
        page = int(request.args.get('page', 1))
        size = max(1, min(200, int(request.args.get('size', 50))))
        offset = (page-1) * size
        ss_prep_schema._ensure_items_prep_tables()
        conn = sqlite3.connect('bol.db')
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute('SELECT COUNT(*) FROM items_prep_images WHERE deleted_at IS NOT NULL')
        total = cur.fetchone()[0]
        cur.execute('''
            SELECT id, upc, trash_path, image_path, deleted_at, expires_at
            FROM items_prep_images
            WHERE deleted_at IS NOT NULL
            ORDER BY deleted_at DESC, id DESC
            LIMIT ? OFFSET ?
        ''', (size, offset))
        rows = [dict(r) for r in cur.fetchall()]
        # add absolute-ish URLs for preview (served from static)
        for r in rows:
            r['url'] = url_for('static', filename=(r.get('trash_path') or r.get('image_path') or ''))
        return jsonify({'success': True, 'results': rows, 'total': total, 'page': page, 'size': size})
    except Exception as e:
        return jsonify({'success': False, 'error': ss_errors._safe_error(e)}), 500
    finally:
        if conn is not None:
            conn.close()


def api_trash_restore():
    try:
        data = request.get_json() or {}
        ids = data.get('ids') or []
        upc = data.get('upc')
        if upc and not ids:
            # restore all for UPC
            return ss_prep_diagnostics.api_items_prep_trash_restore(upc)
        if not ids:
            return jsonify({'success': False, 'error': 'No ids provided'}), 400
        # Group ids by UPC, then restore only the specific ids per UPC
        from DBmanager import connect_db
        with connect_db('bol.db') as conn:
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()
            placeholders = ','.join('?' for _ in ids)
            cur.execute(f'SELECT id, upc FROM items_prep_images WHERE id IN ({placeholders})', tuple(ids))
            rows = cur.fetchall()
        # Group by UPC
        upc_ids = {}
        for r in rows:
            upc_ids.setdefault(r['upc'], []).append(r['id'])
        restored_total = 0
        for group_upc, group_ids in upc_ids.items():
            # Build a fake request context with the specific ids
            with ss_runtime.app.test_request_context(json={'ids': group_ids}):
                resp = ss_prep_diagnostics.api_items_prep_trash_restore(group_upc)
                restored_total += len(group_ids)
        return jsonify({'success': True, 'restored': restored_total})
    except Exception as e:
        return jsonify({'success': False, 'error': ss_errors._safe_error(e)}), 500


def api_trash_hard_delete():
    try:
        data = request.get_json() or {}
        ids = data.get('ids') or []
        upc = data.get('upc')
        if upc and not ids:
            # hard delete all for UPC
            with ss_runtime.app.test_request_context(query_string={'hard': '1'}):
                return ss_prep_diagnostics.api_items_prep_diagnostic_delete_photos(upc)
        if not ids:
            return jsonify({'success': False, 'error': 'No ids provided'}), 400, 400
        # Delete only the specific IDs requested
        from DBmanager import connect_db
        with connect_db('bol.db') as conn:
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()
            placeholders = ','.join('?' for _ in ids)
            cur.execute(f'SELECT id, image_path, trash_path FROM items_prep_images WHERE id IN ({placeholders})', tuple(ids))
            rows = cur.fetchall()
            deleted = 0
            for r in rows:
                for rel in [r['trash_path'], r['image_path']]:
                    if rel:
                        abs_path = os.path.join(ss_runtime.app.root_path, 'static', rel) if not os.path.isabs(rel) else rel
                        try:
                            if os.path.isfile(abs_path):
                                os.remove(abs_path)
                        except Exception as fe:
                            print('Failed hard remove photo', abs_path, fe)
                cur.execute('DELETE FROM items_prep_images WHERE id = ?', (r['id'],))
                deleted += 1
        return jsonify({'success': True, 'deleted': deleted, 'hard': True})
    except Exception as e:
        return jsonify({'success': False, 'error': ss_errors._safe_error(e)}), 500


def api_trash_hard_delete_all():
    conn = None
    try:
        # Hard delete everything currently in trash (deleted_at not null)
        ss_prep_schema._ensure_items_prep_tables()
        conn = sqlite3.connect('bol.db')
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute('SELECT DISTINCT upc FROM items_prep_images WHERE deleted_at IS NOT NULL')
        upcs = [r['upc'] for r in cur.fetchall()]
        total = 0
        for u in upcs:
            # hard=1 ensures permanent deletion
            with ss_runtime.app.test_request_context(query_string={'hard':'1'}):
                resp = ss_prep_diagnostics.api_items_prep_diagnostic_delete_photos(u)
                total += 1
        return jsonify({'success': True, 'deleted_groups': total})
    except Exception as e:
        return jsonify({'success': False, 'error': ss_errors._safe_error(e)}), 500
    finally:
        if conn is not None:
            conn.close()


def api_trash_purge_expired():
    try:
        count = ss_inventory_cleanup._purge_expired_trash()
        return jsonify({'success': True, 'purged': count})
    except Exception as e:
        return jsonify({'success': False, 'error': ss_errors._safe_error(e)}), 500


@ss_errors.require_debug_mode
def api_debug_db_check():
    """Debug endpoint to check DB columns and sample data."""
    conn = None
    try:
        conn = sqlite3.connect('bol.db')
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        
        # Check columns
        cur.execute("PRAGMA table_info(bol_items)")
        cols = [dict(r) for r in cur.fetchall()]
        
        # Check sample listed item
        cur.execute("SELECT upc, list_status, listed_amazon, listed_ebay, listed_facebook FROM bol_items WHERE list_status IS NOT NULL AND list_status != '' LIMIT 5")
        rows = [dict(r) for r in cur.fetchall()]
        
        return jsonify({'columns': cols, 'sample_listed': rows})
    except Exception as e:
        return jsonify({'error': ss_errors._safe_error(e)}), 500
    finally:
        if conn is not None:
            conn.close()


def api_delete_row(db_key, item_id):
    mapping = {
        'ebayStore': 'ebayStore.db',
        'sold': 'sold.db',
        'searchRack': 'searchRack.db',
        'found': 'found.db',
        'bol': 'bol.db'
    }
    if db_key not in mapping:
        return jsonify({'error': 'Unknown db_key'}), 400
    src_db = mapping[db_key]
    conn = None
    dconn = None
    try:
        table, pk = ss_warehouse_locations._get_table_and_pk(src_db)
        if not table:
            return jsonify({'error': 'No table in source DB'}), 400
        conn = sqlite3.connect(src_db)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute(f"SELECT * FROM {table} WHERE {pk} = ?", (item_id,))
        row = cur.fetchone()
        if not row:
            return jsonify({'error': 'Row not found'}), 404
        rowdict = dict(row)
        dconn = sqlite3.connect('deleted.db')
        dcur = dconn.cursor()
        dcur.execute('''CREATE TABLE IF NOT EXISTS deleted_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_db TEXT,
            source_table TEXT,
            source_pk TEXT,
            source_id TEXT,
            deleted_at TEXT,
            data_json TEXT
        )''')
        import datetime, json
        # Store deletion time in ISO8601 UTC
        deleted_at = datetime.datetime.now(datetime.UTC).isoformat()
        dcur.execute('INSERT INTO deleted_items (source_db, source_table, source_pk, source_id, deleted_at, data_json) VALUES (?,?,?,?,?,?)',
                     (db_key, table, pk, str(item_id), deleted_at, json.dumps(rowdict)))
        dconn.commit()
        archive_id = dcur.lastrowid
        # Log to rackhistory if deleting from searchRack
        if db_key == 'searchRack':
            removed_conn = None
            try:
                removed_conn = sqlite3.connect('rackhistory.db')
                removed_cur = removed_conn.cursor()
                ss_inventory_history._ensure_removed_items_table(removed_cur)
                
                # Extract details from the archived row data
                item_title = rowdict.get('TITLE') or rowdict.get('title', '')
                item_barcode = rowdict.get('BARCODE') or rowdict.get('barcode', '')
                old_qty = rowdict.get('QUANTITY') or rowdict.get('quantity', 0)
                item_location = rowdict.get('ITEM_POSITION') or rowdict.get('item_position', '')
                
                removed_cur.execute('''
                    INSERT INTO removed_items 
                    (order_id, barcode, title, quantity_removed, removed_at, searchrack_id, old_quantity, new_quantity, removal_type, item_position)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''', (None, item_barcode, item_title, old_qty, deleted_at, item_id, old_qty, 0, 'manual_multidb_delete', item_location))
                removed_conn.commit()
            except Exception as log_err:
                print(f"Warning: Could not log multi-db deletion to removed_items: {log_err}")
            finally:
                if removed_conn is not None:
                    removed_conn.close()
        
        conn2 = sqlite3.connect(src_db)
        try:
            cur2 = conn2.cursor()
            cur2.execute(f"DELETE FROM {table} WHERE {pk} = ?", (item_id,))
            conn2.commit()
        finally:
            conn2.close()
        return jsonify({'success': True, 'archived_id': archive_id})
    except Exception as e:
        return jsonify({'error': ss_errors._safe_error(e)}), 500
    finally:
        if conn is not None:
            conn.close()
        if dconn is not None:
            dconn.close()


def api_undelete(archive_id):
    conn = None
    dconn = None
    try:
        dconn = sqlite3.connect('deleted.db')
        dconn.row_factory = sqlite3.Row
        dcur = dconn.cursor()
        dcur.execute('SELECT * FROM deleted_items WHERE id = ?', (archive_id,))
        row = dcur.fetchone()
        if not row:
            return jsonify({'error': 'Archive not found'}), 404
        rec = dict(row)
        import json
        data = json.loads(rec['data_json'])
        src_db_key = rec['source_db']
        mapping = {
            'ebayStore': 'ebayStore.db',
            'sold': 'sold.db',
            'searchRack': 'searchRack.db',
            'found': 'found.db',
            'bol': 'bol.db'
        }
        if src_db_key not in mapping:
            return jsonify({'error': 'Unknown source db'}), 400
        src_db = mapping[src_db_key]
        table = rec['source_table']
        conn = sqlite3.connect(src_db)
        cur = conn.cursor()
        cur.execute(f"PRAGMA table_info('{table}')")
        cols = [r[1] for r in cur.fetchall()]
        insert_cols = [c for c in cols if c in data and c != rec['source_pk']]
        vals = [data[c] for c in insert_cols]
        placeholders = ','.join(['?'] * len(vals))
        if insert_cols:
            sql = f"INSERT INTO {table} ({','.join(insert_cols)}) VALUES ({placeholders})"
            cur.execute(sql, vals)
            conn.commit()
            dcur.execute('DELETE FROM deleted_items WHERE id = ?', (archive_id,))
            dconn.commit()
            return jsonify({'success': True})
        else:
            return jsonify({'error': 'No insertable columns found'}), 400
    except Exception as e:
        return jsonify({'error': ss_errors._safe_error(e)}), 500
    finally:
        if dconn is not None:
            dconn.close()
        if conn is not None:
            conn.close()


def api_location_duplicates():
    """Return items that have the same barcode in multiple locations - ONE ROW per barcode with locations array"""
    conn = None
    try:
        conn = sqlite3.connect('searchRack.db')
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        
        # Get quantity column name
        cur.execute('PRAGMA table_info(SEARCHRACK)')
        cols = [r[1] for r in cur.fetchall()]
        qty_col = 'QUANTITY' if 'QUANTITY' in cols else ('QTY' if 'QTY' in cols else 'quantity')
        
        # Find barcodes that appear in multiple locations (non-suffixed barcodes only)
        cur.execute(f'''
            SELECT 
                BARCODE,
                MAX(TITLE) as TITLE,
                MAX(IMAGE) as IMAGE,
                MAX(PICTUREPOSITION) as PICTUREPOSITION,
                MAX(CREATED_AT) as CREATED_AT,
                GROUP_CONCAT(ID || ':' || ITEM_POSITION || ':' || {qty_col} || ':' || COALESCE(PICTUREPOSITION, ''), '|') as location_data,
                SUM(CAST({qty_col} AS INTEGER)) as total_quantity
            FROM SEARCHRACK
            WHERE BARCODE IS NOT NULL 
            AND TRIM(BARCODE) != ''
            AND BARCODE NOT LIKE '%-%'
            AND ITEM_POSITION IS NOT NULL
            AND TRIM(ITEM_POSITION) != ''
            AND COALESCE(CAST({qty_col} AS INTEGER), 0) > 0
            GROUP BY BARCODE
            HAVING COUNT(DISTINCT ITEM_POSITION) > 1
            ORDER BY BARCODE
        ''')
        
        grouped_rows = cur.fetchall()
        results = []
        total_qty = 0
        
        for group_row in grouped_rows:
            barcode = group_row['BARCODE']
            title = group_row['TITLE'] or 'Unknown'
            image = group_row['IMAGE'] or ''
            created_at = group_row['CREATED_AT']
            location_data = group_row['location_data']
            group_total_qty = group_row['total_quantity'] or 0
            
            # Parse location data: "id:location:qty:picturepos|..."
            locations_list = []
            
            for loc_entry in location_data.split('|'):
                parts = loc_entry.split(':')
                if len(parts) >= 3:
                    item_id = parts[0]
                    location = parts[1]
                    qty_str = parts[2]
                    picturepos = parts[3] if len(parts) > 3 else ''
                    
                    try:
                        qty = int(float(qty_str)) if qty_str else 0
                    except Exception:
                        qty = 0
                    
                    locations_list.append({
                        'id': item_id,
                        'code': location,
                        'quantity': qty,
                        'image': picturepos if picturepos else location
                    })
            
            total_qty += group_total_qty
            
            # Create ONE result entry per barcode with locations array
            result = {
                'id': locations_list[0]['id'] if locations_list else None,  # Use first ID as primary
                'barcode': barcode,
                'title': title,
                'quantity': group_total_qty,
                'locations': locations_list,  # Array of all locations
                'created_at': created_at,
                'image': image,
                'source_db': 'searchRack',
                'is_duplicate_group': True
            }
            results.append(result)
        
        
        return jsonify({
            'results': results,
            'total_quantity': total_qty,
            'total_groups': len(grouped_rows)
        })
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'error': ss_errors._safe_error(e), 'results': []}), 500
    finally:
        if conn is not None:
            conn.close()


def api_archived_list():
    """Return list of archived (deleted) items from deleted.db as normalized results."""
    conn = None
    try:
        conn = sqlite3.connect('deleted.db')
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute('SELECT * FROM deleted_items ORDER BY deleted_at DESC')
        rows = [dict(r) for r in cur.fetchall()]
        results = []
        import json
        for r in rows:
            data = {}
            try:
                data = json.loads(r.get('data_json') or '{}')
            except Exception:
                data = {}
            # Normalize common fields across mixed sources; include UPPERCASE keys from SEARCHRACK
            title_val = (
                data.get('Title') or data.get('title') or data.get('TITLE') or
                data.get('item_description') or data.get('DESCRIPTION') or data.get('description') or ''
            )
            image_val = (
                data.get('Image') or data.get('image') or data.get('image_url') or data.get('IMAGE') or ''
            )
            barcode_val = (
                data.get('BARCODE') or data.get('barcode') or data.get('upc') or
                data.get('UPC') or data.get('ItemID') or ''
            )
            itemid_val = (
                data.get('ItemID') or data.get('item_id') or data.get('ItemId') or
                data.get('ITEMID') or data.get('id') or data.get('ID') or data.get('upc') or ''
            )
            picturepos_val = (
                data.get('PICTUREPOSITION') or data.get('pictureposition') or data.get('picture_position') or ''
            )
            itempos_val = (
                data.get('ITEM_POSITION') or data.get('item_position') or data.get('position') or ''
            )
            quantity_val = (
                data.get('Quantity') or data.get('quantity') or data.get('qty') or data.get('QUANTITY') or ''
            )

            # Parse IMAGES field to extract a first URL if needed
            imgs_field = data.get('IMAGES') or data.get('images')
            if (not image_val) and imgs_field is not None:
                try:
                    if isinstance(imgs_field, list):
                        for u in imgs_field:
                            s = str(u or '')
                            if s.lower().startswith('http'):
                                image_val = s
                                break
                    elif isinstance(imgs_field, str):
                        s = imgs_field.strip()
                        if s.startswith('[') and s.endswith(']'):
                            arr = json.loads(s)
                            if isinstance(arr, list):
                                for u in arr:
                                    us = str(u or '')
                                    if us.lower().startswith('http'):
                                        image_val = us
                                        break
                        if not image_val:
                            for sep in [',',';','|','\n','\t',' ']:
                                if sep in s:
                                    for part in s.split(sep):
                                        ps = part.strip()
                                        if ps.lower().startswith('http'):
                                            image_val = ps
                                            break
                                    if image_val:
                                        break
                            if not image_val and s.lower().startswith('http'):
                                image_val = s
                except Exception:
                    pass

            # Enrich archived rows using UPC against ebayStore/bol for title/image/quantity if missing
            def _to_int_like(q):
                try:
                    if q is None:
                        return None
                    s = str(q).strip()
                    if not s:
                        return None
                    if s.isdigit():
                        return int(s)
                    if s.endswith('.0') and s.replace('.0','').isdigit():
                        return int(float(s))
                    f = float(s)
                    if abs(f - int(f)) < 1e-9:
                        return int(f)
                    return None
                except Exception:
                    return None

            if barcode_val:
                es_conn = None
                try:
                    es_conn = sqlite3.connect('ebayStore.db')
                    es_conn.row_factory = sqlite3.Row
                    es_cur = es_conn.cursor()
                    es_cur.execute("SELECT Title, Image, ItemID, Quantity FROM INVENTORY WHERE UPC = ? COLLATE NOCASE LIMIT 1", (barcode_val,))
                    row_es = es_cur.fetchone()
                    if row_es:
                        if not title_val:
                            title_val = row_es['Title']
                        if not image_val:
                            image_val = row_es['Image']
                        if not itemid_val:
                            itemid_val = row_es['ItemID']
                        if not quantity_val:
                            quantity_val = row_es['Quantity']
                except Exception:
                    pass
                finally:
                    if es_conn is not None:
                        es_conn.close()
                if (not title_val or not image_val):
                    bol_conn = None
                    try:
                        bol_conn = sqlite3.connect('bol.db')
                        bol_conn.row_factory = sqlite3.Row
                        bol_cur = bol_conn.cursor()
                        bol_cur.execute('SELECT item_description, image_url, upc FROM bol_items WHERE upc = ? COLLATE NOCASE LIMIT 1', (barcode_val,))
                        row_bol = bol_cur.fetchone()
                        if row_bol:
                            if not title_val:
                                title_val = row_bol['item_description']
                            if not image_val:
                                image_val = row_bol['image_url']
                            if not itemid_val:
                                itemid_val = row_bol['upc']
                    except Exception:
                        pass
                    finally:
                        if bol_conn is not None:
                            bol_conn.close()

            qn = _to_int_like(quantity_val)
            if qn is not None:
                quantity_val = qn
            item_out = {
                'archived': True,
                'archived_id': r.get('id'),
                'deleted_at': r.get('deleted_at'),
                'source_db': 'archived',
                'orig_source_db': r.get('source_db'),
                'orig_table': r.get('source_table'),
                'orig_pk': r.get('source_pk'),
                'orig_id': r.get('source_id'),
                'id': r.get('id'),
                'title': title_val,
                'image': image_val,
                'barcode': barcode_val,
                'item_id': itemid_val,
                'pictureposition': picturepos_val,
                'item_position': itempos_val,
                'quantity': quantity_val,
                'raw': data
            }
            results.append(item_out)
        # Merge archived rows similar to searchRack: group by barcode+item_position and sum quantities
        # IMPORTANT: Store ALL archive IDs in merged_archive_ids so we can delete all entries at once
        if results:
            merged = {}
            for r in results:
                bc = (r.get('barcode') or '').strip().lower()
                pos = (r.get('item_position') or '').strip().lower()
                if not bc:
                    key = f"__{id(r)}_{len(merged)}"
                else:
                    key = f"{bc}||{pos}"
                # normalize qty: if not int-like, treat as 1
                try:
                    q = r.get('quantity')
                    if isinstance(q, str):
                        q = q.strip()
                    if q is None or (isinstance(q, str) and not q):
                        n = 1
                    elif isinstance(q, int):
                        n = q
                    elif isinstance(q, float) and abs(q - int(q)) < 1e-9:
                        n = int(q)
                    elif isinstance(q, str) and q.isdigit():
                        n = int(q)
                    elif isinstance(q, str) and q.endswith('.0') and q.replace('.0','').isdigit():
                        n = int(float(q))
                    else:
                        n = 1
                except Exception:
                    n = 1
                if key not in merged:
                    r_copy = r.copy()
                    r_copy['quantity'] = n
                    # Store array of all archive IDs that were merged into this row
                    r_copy['merged_archive_ids'] = [r.get('id')]
                    merged[key] = r_copy
                else:
                    merged[key]['quantity'] = merged[key].get('quantity', 0) + n
                    # Append this archive ID to the list
                    if 'merged_archive_ids' not in merged[key]:
                        merged[key]['merged_archive_ids'] = []
                    merged[key]['merged_archive_ids'].append(r.get('id'))
            results = list(merged.values())
        return jsonify({'results': results})
    except Exception as e:
        return jsonify({'error': ss_errors._safe_error(e)}), 500
    finally:
        if conn is not None:
            conn.close()


def api_delete_archive(archive_id):
    conn = None
    try:
        conn = sqlite3.connect('deleted.db')
        cur = conn.cursor()
        cur.execute('DELETE FROM deleted_items WHERE id = ?', (archive_id,))
        conn.commit()
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'error': ss_errors._safe_error(e)}), 500
    finally:
        if conn is not None:
            conn.close()
