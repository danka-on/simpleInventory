"""Inventory history views for Sweet Shelves."""

import pytz
import sqlite3
from flask import jsonify, render_template, request
from . import (
    errors as ss_errors, inventory_history as ss_inventory_history, normalization as ss_normalization,
    warehouse_matching as ss_warehouse_matching,
)


def api_inventory_history():
    """Get comprehensive history of inventory changes (additions, removals, edits)"""
    try:
        search = (request.args.get('search') or '').strip().lower()
        sort_order = request.args.get('sort', 'desc')  # 'asc' or 'desc'
        method_filter = (request.args.get('method') or '').strip()  # Filter by removal method

        history_items = []
        total_count = 0

        # Get removal history from rackhistory.db (removed_items table has more detail)
        rem_conn = None
        try:
            ss_inventory_history._flush_searchrack_history_outbox()
            rem_conn = sqlite3.connect('rackhistory.db')
            rem_conn.row_factory = sqlite3.Row
            rem_cur = rem_conn.cursor()
            ss_inventory_history._ensure_removed_items_table(rem_cur)
            rem_conn.commit()
            
            # Check if removed_items table exists (more detailed than removed table)
            rem_cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='removed_items'")
            if rem_cur.fetchone():
                # Check if item_position column exists, add it if not
                rem_cur.execute("PRAGMA table_info(removed_items)")
                columns = [col[1] for col in rem_cur.fetchall()]
                if 'item_position' not in columns:
                    rem_cur.execute('ALTER TABLE removed_items ADD COLUMN item_position TEXT')
                    rem_conn.commit()
                
                # Build query with optional search filter in SQL for better performance
                params = []
                where_clauses = ["COALESCE(event_status, 'applied') = 'applied'"]

                if search:
                    where_clauses.append("""(
                        LOWER(COALESCE(title, '')) LIKE ?
                        OR LOWER(COALESCE(barcode, '')) LIKE ?
                        OR LOWER(COALESCE(item_position, '')) LIKE ?
                        OR LOWER(COALESCE(from_position, '')) LIKE ?
                        OR LOWER(COALESCE(to_position, '')) LIKE ?
                    )""")
                    params.extend([f'%{search}%'] * 5)

                if method_filter:
                    where_clauses.append("removal_type = ?")
                    params.append(method_filter)

                where_sql = ''
                if where_clauses:
                    where_sql = ' WHERE ' + ' AND '.join(where_clauses)

                # Get total count (before LIMIT)
                count_query = 'SELECT COUNT(*) FROM removed_items' + where_sql
                rem_cur.execute(count_query, params)
                total_count = rem_cur.fetchone()[0]

                # Main data query
                query = '''
                    SELECT
                        id, order_id, barcode, title, quantity_removed,
                        removed_at, searchrack_id, old_quantity, new_quantity, removal_type,
                        item_position, from_position, to_position, inventory_row_deleted
                    FROM removed_items
                ''' + where_sql + ' ORDER BY removed_at DESC LIMIT 1000'

                rem_cur.execute(query, params)

                for row in rem_cur.fetchall():
                    title = row['title'] or 'Unknown'
                    barcode = row['barcode'] or ''
                    from_position = row['from_position'] or row['item_position'] or ''
                    to_position = row['to_position'] or ''
                    if from_position and to_position and from_position.casefold() != to_position.casefold():
                        location = f'{from_position} → {to_position}'
                    else:
                        location = to_position or from_position

                    # For manual_edit type, show as addition or removal based on qty_change
                    removal_type = row['removal_type'] or 'unknown'

                    # Determine if this is addition or removal based on old/new quantity
                    old_qty = row['old_quantity'] or 0
                    new_qty = row['new_quantity'] or 0
                    qty_change = new_qty - old_qty

                    # Normalize timestamp for display (convert UTC to local time)
                    display_timestamp = row['removed_at']
                    try:
                        from datetime import datetime, timedelta
                        dt = datetime.fromisoformat(str(display_timestamp))
                        if dt.tzinfo is not None:
                            # Convert UTC to US/Eastern (handles EST/EDT automatically)
                            eastern = pytz.timezone('US/Eastern')
                            dt = dt.astimezone(eastern).replace(tzinfo=None)
                            display_timestamp = dt.isoformat()
                    except Exception:
                        pass  # Keep original if parsing fails
                    
                    # Check if barcode has a suffix (diagnostic flow)
                    is_diagnostic = '-' in barcode and barcode.split('-')[-1].isdigit()
                    
                    if removal_type == 'add_to_shelf':
                        action = 'Added'
                        source = 'Diagnostic' if is_diagnostic else 'Add to Shelf'
                    elif removal_type == 'manual_edit':
                        if qty_change > 0:
                            action = 'Added'
                            source = 'Diagnostic' if is_diagnostic else 'Manual Edit (Addition)'
                        else:
                            action = 'Removed'
                            source = 'Diagnostic' if is_diagnostic else 'Manual Edit (Removal)'
                    elif removal_type == 'manual':
                        action = 'Removed'
                        source = 'Diagnostic' if is_diagnostic else 'Manual Deletion'
                    elif removal_type == 'manual_handled':
                        action = 'Removed'
                        source = 'Ready to Ship Confirmation'
                    elif removal_type == 'manual_handled_undo':
                        action = 'Restored'
                        source = 'Ready to Ship Undo'
                    elif removal_type == 'manual_sold_removal':
                        action = 'Removed'
                        source = 'Manual Sold Removal (Remove Now)'
                    elif removal_type == 'automatic_allocated':
                        action = 'Removed'
                        source = 'Automatic Removal (Allocated)'
                    elif removal_type == 'manual_multidb_delete':
                        action = 'Removed'
                        source = 'Multi-DB Search (Gear Icon Delete)'
                    elif removal_type == 'prep_history_delete':
                        action = 'Removed'
                        source = 'Item Prep History Delete'
                    elif removal_type == 'location_edit':
                        if qty_change > 0:
                            action = 'Added'
                            source = 'Location Change (Moved Here)'
                        else:
                            action = 'Removed'
                            source = 'Location Change (Moved Away)'
                    elif removal_type == 'locationmoved':
                        if qty_change > 0:
                            action = 'Added'
                            source = 'Location Move (Moved Here)'
                        elif qty_change < 0:
                            action = 'Removed'
                            source = 'Location Move (Moved Away)'
                        else:
                            action = 'Moved'
                            source = 'Location Move'
                    elif removal_type == 'locationcleared':
                        if qty_change > 0:
                            action = 'Added'
                            source = 'Location Cleared (Now Unassigned)'
                        elif qty_change < 0:
                            action = 'Removed'
                            source = 'Location Cleared (Removed From Shelf)'
                        else:
                            action = 'Changed'
                            source = 'Location Cleared'
                    elif removal_type == 'finder_removal':
                        action = 'Removed'
                        source = 'Finder Removal'
                    elif removal_type == 'finder_removal_undo':
                        action = 'Restored'
                        source = 'Finder Undo'
                    elif removal_type == 'repair_removal':
                        action = 'Removed'
                        source = 'Sold Inventory Repair'
                    elif removal_type == 'inventoryremoved':
                        action = 'Removed'
                        source = 'Move Location (Removed From Shelf)'
                    elif removal_type in ('fba_prep', 'fba_removed'):
                        action = 'Removed'
                        source = 'FBA Removed'
                    elif removal_type == 'bulk_manifest_cleanup':
                        action = 'Removed'
                        source = 'Bulk Manifest Shelf Cleanup'
                    elif removal_type in (
                        'inventory_depleted', 'legacy_zero_cleanup',
                        'invalid_quantity_cleanup', 'invalid_zero_insert'
                    ):
                        action = 'Removed'
                        source = {
                            'inventory_depleted': 'Inventory Depleted',
                            'legacy_zero_cleanup': 'Legacy Zero-Quantity Cleanup',
                            'invalid_quantity_cleanup': 'Invalid Quantity Cleanup',
                            'invalid_zero_insert': 'Invalid Zero-Quantity Insert Cleanup'
                        }[removal_type]
                    elif removal_type == 'quantity_adjustment':
                        action = 'Added' if qty_change > 0 else 'Removed'
                        source = 'Inventory Quantity Adjustment'
                    elif removal_type == 'location_change':
                        action = 'Moved'
                        source = 'Location Change'
                    elif removal_type == 'inventory_added':
                        action = 'Added'
                        source = 'Inventory Added'
                    elif removal_type == 'inventory_deleted':
                        action = 'Removed'
                        source = 'Inventory Row Deleted'
                    elif removal_type == 'legacy_marketplace_removal':
                        action = 'Removed'
                        source = 'Marketplace Location Removal'
                    elif removal_type == 'automatic':
                        action = 'Removed'
                        source = 'Automatic Removal'
                    else:
                        action = 'Removed'
                        source = 'Unknown'
                    
                    history_items.append({
                        'id': f"removed_{row['id']}",
                        'type': 'addition' if qty_change > 0 else 'removal',
                        'action': action,
                        'title': title,
                        'barcode': barcode,
                        'location': location,
                        'from_location': from_position,
                        'to_location': to_position,
                        'quantity_change': qty_change,
                        'old_quantity': old_qty,
                        'new_quantity': new_qty,
                        'timestamp': display_timestamp,
                        'method': removal_type,
                        'source': source,
                        'order_id': row['order_id'],
                        'can_undo': False  # Can't undo from this detailed log
                    })
            
        except Exception as e:
            print(f"Error reading removed_items: {e}")
        finally:
            if rem_conn is not None:
                rem_conn.close()
        
        # Sort by timestamp (handle both UTC and local timestamps)
        def parse_timestamp_for_sort(ts):
            """Parse timestamp to datetime, converting UTC to local time for consistent sorting"""
            from datetime import datetime, timedelta
            try:
                if not ts:
                    return datetime.min
                
                # Parse ISO format timestamp
                ts_str = str(ts)
                dt = datetime.fromisoformat(ts_str)
                
                # If timezone-aware (UTC like +00:00), convert to US/Eastern (handles EST/EDT)
                if dt.tzinfo is not None:
                    eastern = pytz.timezone('US/Eastern')
                    dt = dt.astimezone(eastern).replace(tzinfo=None)
                
                return dt
            except Exception:
                return datetime.min
        
        history_items.sort(key=lambda x: parse_timestamp_for_sort(x['timestamp']), reverse=(sort_order == 'desc'))
        
        return jsonify({'success': True, 'items': history_items[:500], 'total_count': total_count})  # Limit to 500 for performance
        
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': ss_errors._safe_error(e)}), 500


def _find_legacy_removed_detail(rem_cur, legacy_row):
    """Resolve a retired removal row to one authoritative snapshot-backed event."""
    legacy = dict(legacy_row) if not isinstance(legacy_row, dict) else dict(legacy_row)
    linked_id = ss_normalization._coerce_int(legacy.get('detail_history_id'), 0)
    if linked_id > 0:
        rem_cur.execute('''
            SELECT * FROM removed_items
            WHERE id = ?
              AND COALESCE(source_row_json, '') != ''
              AND (undone_at IS NULL OR undone_at = '')
              AND COALESCE(event_status, 'applied') = 'applied'
        ''', (linked_id,))
        linked = rem_cur.fetchone()
        if linked:
            return linked, ''

    barcode = str(legacy.get('barcode') or '').strip()
    variants = sorted({
        str(value).strip().casefold()
        for value in ss_warehouse_matching._sold_removal_barcode_variants(barcode)
        if str(value).strip()
    })
    if not variants:
        return None, 'The legacy removal has no usable barcode.'
    placeholders = ','.join('?' for _ in variants)
    rem_cur.execute(f'''
        SELECT * FROM removed_items
        WHERE LOWER(TRIM(COALESCE(barcode, ''))) IN ({placeholders})
          AND COALESCE(source_row_json, '') != ''
          AND (undone_at IS NULL OR undone_at = '')
          AND COALESCE(event_status, 'applied') = 'applied'
          AND COALESCE(old_quantity, 0) > COALESCE(new_quantity, 0)
        ORDER BY id DESC
        LIMIT 100
    ''', tuple(variants))

    legacy_time = ss_inventory_history._history_timestamp(legacy.get('time_removed'))
    legacy_qty = max(0, ss_normalization._coerce_int(legacy.get('qty'), 0))
    source_location = str(legacy.get('source_location') or '').strip().casefold()
    ranked = []
    for candidate in rem_cur.fetchall():
        candidate_dict = dict(candidate)
        candidate_qty = ss_inventory_history._history_restore_quantity(candidate_dict)
        if candidate_qty <= 0:
            continue
        if legacy_qty and candidate_qty != legacy_qty:
            continue
        candidate_location = str(
            candidate_dict.get('from_position') or candidate_dict.get('item_position') or ''
        ).strip().casefold()
        if source_location and candidate_location != source_location:
            continue
        candidate_time = ss_inventory_history._history_timestamp(
            candidate_dict.get('removed_at') or candidate_dict.get('applied_at')
        )
        if legacy_time is None or candidate_time is None:
            continue
        seconds = abs((candidate_time - legacy_time).total_seconds())
        if seconds > 600:
            continue
        ranked.append((seconds, candidate))

    ranked.sort(key=lambda item: item[0])
    if not ranked:
        return None, 'No matching snapshot-backed Rack History event was found.'
    if len(ranked) > 1 and abs(ranked[1][0] - ranked[0][0]) < 1:
        first = dict(ranked[0][1])
        second = dict(ranked[1][1])
        first_location = str(first.get('from_position') or first.get('item_position') or '').casefold()
        second_location = str(second.get('from_position') or second.get('item_position') or '').casefold()
        if first_location != second_location:
            return None, 'Multiple shelves match this legacy removal; use Rack History to choose the exact event.'
    return ranked[0][1], ''


def api_removed_list():
    conn = None
    try:
        conn = sqlite3.connect('rackhistory.db')
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        ss_inventory_history._ensure_legacy_removed_table(cur)
        conn.commit()

        # Optional search filter
        q = (request.args.get('q') or '').strip()
        params = []
        where = ''
        if q:
            where = 'WHERE (LOWER(COALESCE(name, "")) LIKE ? OR LOWER(COALESCE(barcode, "")) LIKE ?)' 
            params.extend([f'%{q.lower()}%', f'%{q.lower()}%'])

        cur.execute(f"SELECT id, name, barcode, qty, time_removed, undone_at FROM removed {where} ORDER BY time_removed DESC LIMIT 1000", params)
        rows = [dict(r) for r in cur.fetchall()]
        return jsonify({'success': True, 'items': rows})
    except Exception as e:
        return jsonify({'success': False, 'error': ss_errors._safe_error(e)}), 500
    finally:
        if conn is not None:
            conn.close()


def removed_page():
    try:
        return render_template('removed.html')
    except Exception as e:
        ss_errors._safe_error(e, 'Error loading page')
        return 'Error loading page', 500


def api_removed_undo(rem_id: int):
    """Undo a legacy removal, recreating its SEARCHRACK row when a snapshot is available."""
    rem_conn = None
    rack_conn = None
    try:
        ss_inventory_history._flush_searchrack_history_outbox()
        # Open rackhistory.db and fetch entry
        rem_conn = sqlite3.connect('rackhistory.db')
        rem_conn.row_factory = sqlite3.Row
        rem_cur = rem_conn.cursor()
        ss_inventory_history._ensure_removed_items_table(rem_cur)
        ss_inventory_history._ensure_legacy_removed_table(rem_cur)
        rem_conn.commit()
        rem_cur.execute('''
            SELECT id, name, barcode, qty, time_removed, undone_at,
                   detail_history_id, source_location
            FROM removed WHERE id = ?
        ''', (rem_id,))
        row = rem_cur.fetchone()
        if not row:
            return jsonify({'success': False, 'error': 'Removed entry not found'}), 404
        if row['undone_at']:
            return jsonify({'success': False, 'error': 'Already undone'}), 400

        detail, detail_error = _find_legacy_removed_detail(rem_cur, row)
        if not detail:
            return jsonify({
                'success': False,
                'error': detail_error or 'No authoritative Rack History snapshot was found; inventory was not changed.'
            }), 409
        restore_quantity = ss_inventory_history._history_restore_quantity(detail)
        if restore_quantity <= 0:
            return jsonify({'success': False, 'error': 'The matched history event has no quantity to restore.'}), 409

        rack_conn = sqlite3.connect('searchRack.db')
        rack_conn.row_factory = sqlite3.Row
        rack_cur = rack_conn.cursor()
        ss_inventory_history._ensure_searchrack_undo_claims(rack_cur)
        rack_conn.commit()
        rack_cur.execute('BEGIN IMMEDIATE')
        restored = ss_inventory_history._restore_searchrack_with_undo_claim(rack_cur, detail, restore_quantity)
        rack_conn.commit()

        import datetime as _dt
        undone_at = _dt.datetime.now(_dt.UTC).isoformat().replace('+00:00', 'Z')
        rem_cur.execute(
            'UPDATE removed SET undone_at = ?, detail_history_id = ? WHERE id = ?',
            (undone_at, detail['id'], rem_id)
        )
        rem_cur.execute(
            'UPDATE removed_items SET undone_at = ? WHERE id = ? AND (undone_at IS NULL OR undone_at = "")',
            (undone_at, detail['id'])
        )
        ss_inventory_history._insert_inventory_undo_history(
            rem_cur, detail, restored, 'legacy_removed_undo', undone_at=undone_at
        )
        rem_conn.commit()

        return jsonify({
            'success': True,
            'new_qty': restored['new_quantity'],
            'restored_units': restored['restore_quantity'],
            'searchrack_id': restored['searchrack_id'],
            'recreated': restored['recreated']
        })
    except Exception as e:
        for connection in (rem_conn, rack_conn):
            try:
                if connection is not None:
                    connection.rollback()
            except Exception:
                pass
        return jsonify({'success': False, 'error': ss_errors._safe_error(e)}), 500
    finally:
        if rem_conn is not None:
            rem_conn.close()
        if rack_conn is not None:
            rack_conn.close()


def api_sold_remove_now(order_id: int):
    """Legacy endpoint: sold-order removals are manual in Ready to Ship."""
    return jsonify({
        'success': False,
        'manual_mode': True,
        'error': 'Immediate sold-order removal is disabled. Confirm the order from Ready to Ship instead.',
        'redirect_url': '/ready_to_ship'
    }), 409


def undo_match():
    data = request.json
    bol_id = data.get('bol_id')
    ebay_id = data.get('ebay_id')
    if bol_id is None or ebay_id is None:
        return jsonify({'success': False, 'error': 'Missing IDs'}), 400
    bol_conn = None
    ebay_conn = None
    found_conn = None
    try:
        # Look up the upc for this bol_id
        bol_conn = sqlite3.connect('bol.db')
        bol_cur = bol_conn.cursor()
        bol_cur.execute('SELECT upc FROM bol_items WHERE rowid=?', (bol_id,))
        bol_row = bol_cur.fetchone()
        if not bol_row or not bol_row[0]:
            return jsonify({'success': False, 'error': 'No UPC found for BOL item'}), 404
        upc = bol_row[0]
        # Remove from found.db using upc and ebay_id
        found_conn = sqlite3.connect('found.db')
        found_cur = found_conn.cursor()
        found_cur.execute('DELETE FROM matches WHERE ebay_id=? AND upc=?', (ebay_id, upc))
        found_conn.commit()
        # Unmark bol item
        bol_conn = sqlite3.connect('bol.db')
        bol_cur = bol_conn.cursor()
        bol_cur.execute("UPDATE bol_items SET isFound='' WHERE rowid=?", (bol_id,))
        bol_conn.commit()
        # Unmark ebay item
        ebay_conn = sqlite3.connect('ebayStore.db')
        ebay_cur = ebay_conn.cursor()
        ebay_cur.execute("UPDATE INVENTORY SET isFound='' WHERE rowid=?", (ebay_id,))
        ebay_conn.commit()
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': ss_errors._safe_error(e)}), 500
    finally:
        if bol_conn is not None:
            bol_conn.close()
        if found_conn is not None:
            found_conn.close()
        if ebay_conn is not None:
            ebay_conn.close()
