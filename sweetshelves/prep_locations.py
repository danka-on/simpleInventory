"""Prep locations for Sweet Shelves."""

import datetime
import sqlite3
from flask import jsonify, request
from . import (
    caching as ss_caching, errors as ss_errors, inventory_age as ss_inventory_age, listing_log as
    ss_listing_log, normalization as ss_normalization, prep_media as ss_prep_media, prep_schema as
    ss_prep_schema, warehouse_allocations as ss_warehouse_allocations, warehouse_matching as
    ss_warehouse_matching,
)


def api_items_prep_location_get(upc):
    """Get location for a UPC."""
    conn = None
    try:
        upc_norm = ss_normalization._normalize_upc(upc)
        upc = ss_normalization._normalize_upc_preserve_suffix_for_match(upc_norm)
        lot_number = ss_warehouse_allocations._preferred_lot_from_request()
        ss_prep_schema._ensure_items_prep_tables()
        conn = sqlite3.connect('bol.db')
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        row = ss_warehouse_allocations._select_prep_status_row(cur, upc, lot_number, columns='location, pictureposition, lot_number')
        saved_location = ''
        saved_pictureposition = ''
        if row:
            saved_location = str(row['location'] or '').strip()
            saved_pictureposition = str(row['pictureposition'] or '').strip()

        inventory_pick = ss_warehouse_matching._prep_select_inventory_match(upc, preferred_location=saved_location, preferred_preview=saved_pictureposition)
        clear_target = inventory_pick.get('match') or {}
        clear_target_row_id = clear_target.get('id')
        clearable_inventory = bool(inventory_pick.get('clearable'))
        inventory_entries = [
            ss_warehouse_matching._serialize_prep_location_entry(group)
            for group in list(inventory_pick.get('location_groups') or [])
        ]

        if row and (saved_location or saved_pictureposition):
            saved_label_source = saved_pictureposition if (saved_pictureposition and str(saved_location or '').strip().lower() in ('', 'picture')) else (saved_location or saved_pictureposition)
            saved_label = ss_warehouse_matching._sold_location_label(saved_label_source)
            display_entry = None
            if inventory_entries:
                pref_values = {
                    str(v or '').strip().lower()
                    for v in (saved_location, saved_pictureposition, saved_label)
                    if str(v or '').strip()
                }
                for entry in inventory_entries:
                    entry_values = {
                        str(entry.get('location_key') or '').strip().lower(),
                        str(entry.get('location_code') or '').strip().lower(),
                        str(entry.get('preview_key') or '').strip().lower()
                    }
                    entry_values.discard('')
                    if pref_values and (entry_values & pref_values):
                        display_entry = entry
                        break
                if display_entry is None:
                    display_entry = inventory_entries[0]
            else:
                display_entry = {
                    'location_key': ss_warehouse_matching._sold_location_key(saved_label),
                    'location_code': saved_label,
                    'preview_key': saved_pictureposition or saved_location or saved_label,
                    'available_qty': 1,
                    'clear_target_row_id': clear_target_row_id,
                    'clearable': bool(saved_location or saved_pictureposition) or clearable_inventory
                }
            display_location_code = str(display_entry.get('location_code') or saved_label).strip()
            display_preview_key = str(display_entry.get('preview_key') or saved_pictureposition or saved_location or saved_label).strip()
            display_pictureposition = display_preview_key if display_preview_key and ('/' in display_preview_key or '\\' in display_preview_key or '.' in display_preview_key) else ''
            return jsonify({
                'success': True,
                'location': saved_location or display_location_code,
                'pictureposition': saved_pictureposition or display_pictureposition,
                'location_code': display_location_code,
                'preview_key': display_preview_key,
                'location_entries': inventory_entries or [display_entry],
                'source': 'prep_status',
                'clearable': (len(inventory_entries) == 1 and bool((inventory_entries[0] or {}).get('clearable'))) or (not inventory_entries and bool(display_entry.get('clearable'))),
                'clear_target_row_id': display_entry.get('clear_target_row_id') or clear_target_row_id,
                'lot_number': ss_normalization._normalize_lot_number(row['lot_number'])
            })
        first_location = inventory_entries[0] if inventory_entries else {}
        location_code = str(first_location.get('location_code') or '').strip()
        preview_key = str(first_location.get('preview_key') or '').strip()
        pictureposition = preview_key if preview_key and ('/' in preview_key or '\\' in preview_key or '.' in preview_key) else ''
        return jsonify({
            'success': True,
            'location': location_code or None,
            'pictureposition': pictureposition or None,
            'location_code': location_code,
            'preview_key': preview_key,
            'location_entries': inventory_entries,
            'source': 'inventory' if inventory_entries else '',
            'clearable': clearable_inventory,
            'clear_target_row_id': first_location.get('clear_target_row_id') or clear_target_row_id,
            'lot_number': ss_normalization._normalize_lot_number(lot_number)
        })
    except Exception as e:
        return jsonify({'success': False, 'error': ss_errors._safe_error(e)}), 500
    finally:
        if conn is not None:
            conn.close()


def api_items_prep_location_set():
    """Set location for a UPC. JSON: { upc, location, pictureposition }"""
    conn = None
    try:
        data = request.get_json() or {}
        upc_norm = ss_normalization._normalize_upc(data.get('upc'))
        upc = ss_normalization._normalize_upc_preserve_suffix_for_match(upc_norm)
        location = (data.get('location') or '').strip()
        pictureposition = (data.get('pictureposition') or '').strip()
        lot_number = ss_normalization._normalize_lot_number(ss_warehouse_allocations._preferred_lot_from_request(data))
        source = ss_normalization._normalize_listing_source(data.get('source'), default='user')
        if not upc:
            return jsonify({'success': False, 'error': 'Missing upc'}), 400
        ss_prep_schema._ensure_items_prep_tables()
        import datetime
        ts = datetime.datetime.now(datetime.UTC).isoformat()
        conn = sqlite3.connect('bol.db')
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        existing = ss_warehouse_allocations._select_prep_status_row(cur, upc, lot_number, columns='upc, lot_number, location, pictureposition')
        if not existing and not location and not pictureposition:
            return jsonify({
                'success': True,
                'location': None,
                'pictureposition': None,
                'lot_number': ss_normalization._normalize_lot_number(lot_number),
                'cleared': True,
                'status_row_updated': False
            })
        old_location = (existing[2] or '').strip() if existing and len(existing) > 2 else ''
        old_pictureposition = (existing[3] or '').strip() if existing and len(existing) > 3 else ''
        status_update_lot = ss_warehouse_allocations._resolve_prep_status_lot(cur, upc, lot_number)
        if existing:
            cur.execute('''
                UPDATE items_prep_status
                SET location = ?, pictureposition = ?, updated_at = ?
                WHERE upc = ? COLLATE NOCASE
                  AND COALESCE(lot_number, '') = ? COLLATE NOCASE
            ''', (location, pictureposition, ts, upc, status_update_lot))
        else:
            cur.execute('''
                INSERT INTO items_prep_status (upc, lot_number, location, pictureposition, updated_at)
                VALUES (?,?,?,?,?)
            ''', (upc, lot_number, location, pictureposition, ts))
        
        # Get item description for searchRack
        bol_row = ss_warehouse_allocations._select_canonical_bol_row(
            cur,
            upc,
            lot_number,
            columns='item_description'
        )
        title = bol_row[0] if bol_row else None
        conn.commit()
        
        # Update searchRack.db
        if location or pictureposition:
            search_conn = None
            try:
                search_conn = sqlite3.connect('searchRack.db')
                ss_inventory_age._reconcile_inventory_age_batches(search_conn)
                search_cur = search_conn.cursor()
                # Ensure table exists
                search_cur.execute('''
                    CREATE TABLE IF NOT EXISTS SEARCHRACK (
                        ID INTEGER PRIMARY KEY AUTOINCREMENT,
                        TITLE TEXT,
                        BARCODE TEXT,
                        ITEM_POSITION TEXT,
                        IMAGES TEXT,
                        PICTUREPOSITION TEXT,
                        ITEMID TEXT,
                        QUANTITY INTEGER DEFAULT 1,
                        CREATED_AT TEXT
                    )
                ''')
                # Check if entry exists
                search_cur.execute('''
                    SELECT ID FROM SEARCHRACK
                    WHERE BARCODE = ? COLLATE NOCASE
                      AND COALESCE(CAST(QUANTITY AS INTEGER), 0) > 0
                ''', (upc,))
                existing = search_cur.fetchone()
                
                if existing:
                    # Update existing
                    search_cur.execute('''
                        UPDATE SEARCHRACK 
                        SET ITEM_POSITION = ?, PICTUREPOSITION = ?, TITLE = ?, CREATED_AT = ?
                        WHERE BARCODE = ? COLLATE NOCASE
                          AND COALESCE(CAST(QUANTITY AS INTEGER), 0) > 0
                    ''', (location, pictureposition, title, ts, upc))
                else:
                    # Insert new
                    search_cur.execute('''
                        INSERT INTO SEARCHRACK
                        (TITLE, BARCODE, ITEM_POSITION, PICTUREPOSITION, QUANTITY, CREATED_AT)
                        VALUES (?, ?, ?, ?, 1, ?)
                    ''', (title, upc, location, pictureposition, ts))
                
                search_conn.commit()
                ss_inventory_age._reconcile_inventory_age_batches(search_conn)
                ss_caching._invalidate_searchrack_cache()
            except Exception as e:
                print(f'Warning: Failed to update searchRack: {e}')
            finally:
                if search_conn is not None:
                    search_conn.close()

        location_changes = {}
        if old_location != location:
            location_changes['location'] = {'from': old_location or None, 'to': location or None}
        if old_pictureposition != pictureposition:
            location_changes['pictureposition'] = {'from': old_pictureposition or None, 'to': pictureposition or None}
        if location_changes:
            try:
                ss_listing_log._listinglog_add_entry(
                    upc=upc,
                    platform='item_manager',
                    action='location_update',
                    source=source,
                    success=True,
                    meta={
                        'lot_number': status_update_lot or lot_number,
                        'changes': location_changes,
                        'via': 'api_items_prep_location_set'
                    }
                )
            except Exception:
                pass
        
        return jsonify({'success': True, 'lot_number': lot_number})
    except Exception as e:
        return jsonify({'success': False, 'error': ss_errors._safe_error(e)}), 500
    finally:
        if conn is not None:
            conn.close()


def api_items_prep_location_clear():
    """Clear the current prep/inventory location for exactly one unit of a UPC when possible."""
    conn = None
    try:
        data = request.get_json() or {}
        upc_norm = ss_normalization._normalize_upc(data.get('upc'))
        upc = ss_normalization._normalize_upc_preserve_suffix_for_match(upc_norm)
        lot_number = ss_normalization._normalize_lot_number(ss_warehouse_allocations._preferred_lot_from_request(data))
        preferred_location = str(data.get('location') or data.get('location_code') or '').strip()
        preferred_preview = str(data.get('preview_key') or data.get('pictureposition') or '').strip()
        clear_target_row_id = data.get('clear_target_row_id')
        if not upc:
            return jsonify({'success': False, 'error': 'Missing upc'}), 400

        inventory_result = None
        inventory_error = ''
        matched_row_id = None
        if clear_target_row_id not in (None, ''):
            try:
                matched_row_id = int(clear_target_row_id)
            except Exception:
                matched_row_id = None
        if matched_row_id is None:
            inventory_pick = ss_warehouse_matching._prep_select_inventory_match(
                upc,
                preferred_location=preferred_location,
                preferred_preview=preferred_preview
            )
            matched = inventory_pick.get('match') or {}
            matched_row_id = matched.get('id')

        if matched_row_id is not None:
            inventory_result = ss_warehouse_matching._clear_searchrack_row_location(matched_row_id)
            if not inventory_result.get('success'):
                inventory_error = inventory_result.get('error') or 'Failed to clear inventory location'
                inventory_result = None

        ss_prep_schema._ensure_items_prep_tables()
        conn = sqlite3.connect('bol.db')
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        existing = ss_warehouse_allocations._select_prep_status_row(cur, upc, lot_number, columns='upc, lot_number, location, pictureposition')
        status_row_updated = False
        if existing:
            ts = datetime.datetime.now().isoformat()
            status_update_lot = ss_warehouse_allocations._resolve_prep_status_lot(cur, upc, lot_number)
            cur.execute('''
                UPDATE items_prep_status
                SET location = ?, pictureposition = ?, updated_at = ?
                WHERE upc = ? COLLATE NOCASE
                  AND COALESCE(lot_number, '') = ? COLLATE NOCASE
            ''', ('', '', ts, upc, status_update_lot))
            conn.commit()
            status_row_updated = True

        if not status_row_updated and not inventory_result:
            return jsonify({'success': False, 'error': inventory_error or 'No clearable location was found for this item'}), 404

        try:
            ss_caching.update_data_version()
        except Exception:
            pass

        return jsonify({
            'success': True,
            'inventory_cleared': bool(inventory_result and inventory_result.get('success')),
            'status_row_updated': status_row_updated,
            'source_location': (inventory_result or {}).get('source_location') or preferred_location or preferred_preview,
            'clear_target_row_id': matched_row_id,
            'warning': inventory_error or ''
        })
    except Exception as e:
        return jsonify({'success': False, 'error': ss_errors._safe_error(e)}), 500
    finally:
        if conn is not None:
            conn.close()


def api_items_prep_reset():
    """Reset item back to raw manifest default state. JSON: { upc, lot_number? }"""
    conn = None
    rawbol_conn = None
    try:
        data = request.get_json() or {}
        upc = ss_normalization._normalize_upc_preserve_suffix_for_match(ss_normalization._normalize_upc(data.get('upc')))
        lot_number = ss_normalization._normalize_lot_number(ss_warehouse_allocations._preferred_lot_from_request(data))
        if not upc:
            return jsonify({'success': False, 'error': 'Missing upc'}), 400
        
        ss_prep_schema._ensure_items_prep_tables()

        base_upc = upc.split('-', 1)[0] if '-' in upc else upc
        is_suffixed = ('-' in upc and upc.rsplit('-', 1)[-1].isdigit())

        # Get original quantity from rawbol.db/raw_bol_items.
        rawbol_qty = None
        try:
            rawbol_conn = sqlite3.connect('rawbol.db')
            rawbol_cur = rawbol_conn.cursor()
            rawbol_cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='raw_bol_items'")
            has_raw_items = bool(rawbol_cur.fetchone())
            if has_raw_items:
                if lot_number:
                    rawbol_cur.execute('''
                        SELECT SUM(COALESCE(quantity, 0))
                        FROM raw_bol_items
                        WHERE upc = ? COLLATE NOCASE
                          AND lot_number = ? COLLATE NOCASE
                    ''', (base_upc, lot_number))
                else:
                    rawbol_cur.execute('''
                        SELECT SUM(COALESCE(quantity, 0))
                        FROM raw_bol_items
                        WHERE upc = ? COLLATE NOCASE
                    ''', (base_upc,))
                raw_row = rawbol_cur.fetchone()
                rawbol_qty = int((raw_row[0] if raw_row else 0) or 0)
            if not rawbol_qty:
                rawbol_cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='rawbol'")
                has_legacy = bool(rawbol_cur.fetchone())
                if has_legacy:
                    rawbol_cur.execute('SELECT QTY FROM rawbol WHERE UPC = ? COLLATE NOCASE LIMIT 1', (base_upc,))
                    raw_row = rawbol_cur.fetchone()
                    rawbol_qty = int((raw_row[0] if raw_row else 0) or 0)
            if not rawbol_qty:
                rawbol_qty = 1
        except Exception as e:
            print(f'Warning: Could not fetch from rawbol.db: {e}')
            rawbol_qty = 1
        finally:
            if rawbol_conn is not None:
                rawbol_conn.close()
        
        conn = sqlite3.connect('bol.db')
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()

        target_row = ss_warehouse_allocations._select_canonical_bol_row(
            cur,
            upc,
            lot_number,
            columns='id, upc, lot_number',
            strict_lot=bool(lot_number)
        )
        if not target_row:
            if lot_number:
                return jsonify({'success': False, 'error': f'Item {upc} not found in lot {lot_number}'}), 404
            return jsonify({'success': False, 'error': f'Item {upc} not found'}), 404

        target_id = int(target_row['id'] if isinstance(target_row, sqlite3.Row) else target_row[0])
        target_lot = ss_normalization._normalize_lot_number(
            target_row['lot_number'] if isinstance(target_row, sqlite3.Row) else target_row[2]
        ) or lot_number

        # 1) Clear prep status for scoped row/lot.
        status_lot = ss_warehouse_allocations._resolve_prep_status_lot(cur, upc, target_lot)
        cur.execute('''
            DELETE FROM items_prep_status
            WHERE upc = ? COLLATE NOCASE
              AND COALESCE(lot_number, '') = ? COLLATE NOCASE
        ''', (upc, status_lot))

        # 2) Clear photos/notes.
        # For base UPC with lot-specific reset, keep shared note/photo history intact.
        clear_shared_notes = (not lot_number) or is_suffixed
        if clear_shared_notes:
            ss_prep_media._items_prep_delete_diagnostic_assets(cur, upc)

        # 3) Restore quantity buckets in bol_items (id-scoped).
        cur.execute('PRAGMA table_info(bol_items)')
        cols = [col[1].lower() for col in cur.fetchall()]
        has_new_cols = 'original_qty' in cols and 'unchecked_qty' in cols
        
        if has_new_cols:
            cur.execute('''UPDATE bol_items 
                          SET quantity = ?, 
                              original_qty = ?,
                              good_qty = 0,
                              bad_qty = 0,
                              unchecked_qty = ?
                          WHERE id = ?''', 
                       (rawbol_qty, rawbol_qty, rawbol_qty, target_id))
        else:
            cur.execute('UPDATE bol_items SET quantity = ? WHERE id = ?', (rawbol_qty, target_id))
        
        # 4) Clear list status columns for this row.
        try:
            cur.execute('''
                UPDATE bol_items
                SET list_status = NULL,
                    listed_amazon = 0,
                    listed_amazon_date = NULL,
                    listed_amazon_source = NULL,
                    listed_ebay = 0,
                    listed_ebay_date = NULL,
                    listed_ebay_source = NULL,
                    listed_facebook = 0,
                    listed_facebook_date = NULL,
                    listed_facebook_qty = NULL,
                    listed_facebook_source = NULL
                WHERE id = ?
            ''', (target_id,))
        except Exception:
            pass  # Column may not exist
        
        conn.commit()
        try:
            ss_caching.update_data_version()
        except Exception:
            pass

        return jsonify({
            'success': True,
            'quantity': rawbol_qty,
            'lot_number': target_lot,
            'message': 'Item reset to default state'
        })
    except Exception as e:
        return jsonify({'success': False, 'error': ss_errors._safe_error(e)}), 500
    finally:
        if conn is not None:
            conn.close()
