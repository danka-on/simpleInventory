"""Sales repair for Sweet Shelves."""

import sqlite3
from flask import jsonify, request
from . import (
    errors as ss_errors, inventory_history as ss_inventory_history, normalization as ss_normalization,
    runtime as ss_runtime, shipping_identity as ss_shipping_identity, warehouse_matching as
    ss_warehouse_matching,
)


def get_pending_removals():
    """Automatic sold-order removal is disabled; Ready to Ship is authoritative."""
    return jsonify({
        'success': True,
        'orders': [],
        'count': 0,
        'manual_mode': True,
        'message': 'Automatic sold-order removal is disabled. Confirm removals from Ready to Ship.'
    })


def cancel_automatic_removal(order_id):
    """Legacy endpoint: sold-order removals are manual in Ready to Ship."""
    return jsonify({
        'success': False,
        'manual_mode': True,
        'error': 'Automatic sold-order removal is disabled. Manage removals from Ready to Ship instead.',
        'redirect_url': '/ready_to_ship'
    }), 409


def allow_automatic_removal(order_id):
    """Legacy endpoint: sold-order removals are manual in Ready to Ship."""
    return jsonify({
        'success': False,
        'manual_mode': True,
        'error': 'Automatic sold-order removal is disabled. Manage removals from Ready to Ship instead.',
        'redirect_url': '/ready_to_ship'
    }), 409


def trigger_automatic_removal():
    """Legacy endpoint: sold-order removals now require Ready to Ship confirmation."""
    return jsonify({
        'success': True,
        'manual_mode': True,
        'message': 'Automatic sold-order removal is disabled. Use Ready to Ship confirmation instead.'
    })


def repair_missing_removals():
    """
    Repair orders marked as rackupdated=1 but have no removal record.
    Checks inventory and properly removes items that are still in stock.
    """
    return jsonify({
        'success': False,
        'manual_mode': True,
        'error': 'Automatic repair removals are disabled. Confirm inventory removals from Ready to Ship.',
        'redirect_url': '/ready_to_ship'
    }), 409

    from datetime import datetime

    try:
        results = {
            'total_checked': 0,
            'removed_from_inventory': 0,
            'already_removed_or_no_stock': 0,
            'no_barcode': 0,
            'reset_for_retry': 0,
            'details': []
        }

        # Connect to databases
        sold_conn = sqlite3.connect('sold.db')
        sold_conn.row_factory = sqlite3.Row
        sold_cur = sold_conn.cursor()

        hist_conn = sqlite3.connect('rackhistory.db')
        hist_conn.row_factory = sqlite3.Row
        hist_cur = hist_conn.cursor()
        ss_inventory_history._ensure_removed_items_table(hist_cur)

        rack_conn = sqlite3.connect('searchRack.db')
        rack_conn.row_factory = sqlite3.Row
        rack_cur = rack_conn.cursor()

        # Find all orders with rackupdated=1 and a barcode
        sold_cur.execute('''
            SELECT id, order_id, barcode, source_upc, quantity, title, shipped_time
            FROM orders
            WHERE rackupdated = 1
            AND barcode IS NOT NULL AND barcode != ''
            AND COALESCE(removal_cancelled, 0) = 0
        ''')
        orders = sold_cur.fetchall()

        for order in orders:
            results['total_checked'] += 1
            barcode = ss_warehouse_matching._effective_sold_order_barcode(order, prefer_manual_override=True)
            order_id = order['order_id']
            sold_qty = order['quantity'] or 1

            # Check if this order already has a removal record
            hist_cur.execute('''
                SELECT COUNT(*) FROM removed_items
                WHERE (order_id = ? OR barcode = ?)
                AND removal_type IN ('automatic', 'automatic_allocated', 'repair_removal', 'manual_handled', 'manual_sold_removal', 'manual_immediate', 'manual_sold_selection', 'finder_removal')
                AND old_quantity > new_quantity
            ''', (order_id, barcode))
            has_removal = hist_cur.fetchone()[0] > 0

            if has_removal:
                # Already has removal record - skip
                results['already_removed_or_no_stock'] += 1
                continue

            # Try to find item in searchRack with flexible matching
            barcode_stripped = ss_normalization._strip_leading_zeros_numeric(barcode)
            item_row = None

            # Try exact match
            rack_cur.execute('SELECT ID, QUANTITY, TITLE, ITEM_POSITION FROM SEARCHRACK WHERE BARCODE = ?', (barcode,))
            item_row = rack_cur.fetchone()

            # Try padded to 12
            if not item_row and barcode.isdigit():
                rack_cur.execute('SELECT ID, QUANTITY, TITLE, ITEM_POSITION FROM SEARCHRACK WHERE BARCODE = ?', (barcode.zfill(12),))
                item_row = rack_cur.fetchone()

            # Try padded to 13
            if not item_row and barcode.isdigit():
                rack_cur.execute('SELECT ID, QUANTITY, TITLE, ITEM_POSITION FROM SEARCHRACK WHERE BARCODE = ?', (barcode.zfill(13),))
                item_row = rack_cur.fetchone()

            # Try stripped zeros
            if not item_row and barcode_stripped != barcode:
                rack_cur.execute('SELECT ID, QUANTITY, TITLE, ITEM_POSITION FROM SEARCHRACK WHERE BARCODE = ?', (barcode_stripped,))
                item_row = rack_cur.fetchone()

            # Try integer comparison
            if not item_row and barcode_stripped and len(barcode_stripped) >= 8:
                rack_cur.execute('''
                    SELECT ID, QUANTITY, TITLE, ITEM_POSITION FROM SEARCHRACK
                    WHERE CAST(CAST(BARCODE AS INTEGER) AS TEXT) = ?
                    LIMIT 1
                ''', (barcode_stripped,))
                item_row = rack_cur.fetchone()

            if not item_row:
                # Item not in inventory - reset rackupdated so it can retry later if item is added
                sold_cur.execute('UPDATE orders SET rackupdated = 0 WHERE id = ?', (order['id'],))
                results['reset_for_retry'] += 1
                results['details'].append({
                    'order_id': order_id,
                    'barcode': barcode,
                    'action': 'reset_for_retry',
                    'reason': 'Item not in searchRack'
                })
                continue

            # Item found - check quantity and remove
            item_id = item_row['ID']
            current_qty = item_row['QUANTITY'] or 0
            item_title = item_row['TITLE'] or order['title'] or ''
            item_location = item_row['ITEM_POSITION'] or ''

            if current_qty <= 0:
                # Already at zero quantity
                results['already_removed_or_no_stock'] += 1
                results['details'].append({
                    'order_id': order_id,
                    'barcode': barcode,
                    'action': 'skipped',
                    'reason': f'Already at 0 quantity (searchRack ID: {item_id})'
                })
                continue

            # Perform the removal
            new_qty = max(0, current_qty - sold_qty)
            rack_cur.execute('UPDATE SEARCHRACK SET QUANTITY = ? WHERE ID = ?', (new_qty, item_id))

            # Log to rackhistory
            hist_cur.execute('''
                INSERT INTO removed_items
                (order_id, barcode, title, quantity_removed, removed_at, searchrack_id, old_quantity, new_quantity, removal_type, item_position)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (order_id, barcode, item_title, sold_qty, datetime.now().isoformat(), item_id, current_qty, new_qty, 'repair_removal', item_location))

            results['removed_from_inventory'] += 1
            results['details'].append({
                'order_id': order_id,
                'barcode': barcode,
                'action': 'removed',
                'old_qty': current_qty,
                'new_qty': new_qty,
                'location': item_location
            })

        # Commit all changes
        sold_conn.commit()
        rack_conn.commit()
        hist_conn.commit()

        sold_conn.close()
        rack_conn.close()
        hist_conn.close()

        return jsonify({
            'success': True,
            'results': results
        })

    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': ss_errors._safe_error(e)}), 500


def get_sold_removal_history(order_id):
    """Get removal history from rackhistory.db for a specific sold order"""
    try:
        # First get the order details from sold.db
        sold_conn = sqlite3.connect('sold.db')
        sold_conn.row_factory = sqlite3.Row
        sold_cur = sold_conn.cursor()
        sold_cur.execute('SELECT * FROM orders WHERE id = ?', (order_id,))
        order = sold_cur.fetchone()
        sold_conn.close()

        if not order:
            return jsonify({'success': False, 'error': 'Order not found'}), 404

        order_dict = dict(order)
        order_id_str = order_dict.get('order_id') or ''
        barcode = order_dict.get('barcode') or ''

        # Get removal history from rackhistory.db
        history_conn = sqlite3.connect('rackhistory.db')
        history_conn.row_factory = sqlite3.Row
        history_cur = history_conn.cursor()

        # Search by order_id or barcode
        history_cur.execute('''
            SELECT * FROM removed_items
            WHERE (order_id = ? OR (barcode = ? AND barcode != ''))
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
            ORDER BY removed_at DESC
        ''', (order_id_str, barcode))
        history_rows = [
            dict(row)
            for row in history_cur.fetchall()
            if ss_normalization._sold_removal_event_is_current(order_dict, dict(row))
        ]
        history_conn.close()

        return jsonify({
            'success': True,
            'order': order_dict,
            'history': history_rows,
            'sold_at': str(
                order_dict.get('paid_time')
                or order_dict.get('sold_date')
                or order_dict.get('sale_date')
                or order_dict.get('created_at')
                or ''
            )
        })
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': ss_errors._safe_error(e)}), 500


def find_inventory_for_sold(order_id):
    """Find matching inventory items in searchRack for a sold order by UPC/barcode"""
    try:
        # Get order details
        sold_conn = sqlite3.connect('sold.db')
        sold_conn.row_factory = sqlite3.Row
        sold_cur = sold_conn.cursor()
        sold_cur.execute('SELECT * FROM orders WHERE id = ?', (order_id,))
        order = sold_cur.fetchone()
        sold_conn.close()

        if not order:
            return jsonify({'success': False, 'error': 'Order not found'}), 404

        barcode = ss_warehouse_matching._effective_sold_order_barcode(order, prefer_manual_override=True)

        if not barcode:
            return jsonify({
                'success': True,
                'order': dict(order),
                'matches': [],
                'message': 'No barcode on order - use finder.html for manual search'
            })

        # Search searchRack for all matching items
        searchrack_conn = sqlite3.connect('searchRack.db')
        searchrack_conn.row_factory = sqlite3.Row
        searchrack_cur = searchrack_conn.cursor()

        matches = ss_warehouse_matching._searchrack_matches_for_barcode(searchrack_cur, barcode, include_zero=False)
        if not matches:
            base_barcode = ss_shipping_identity._barcode_base_without_suffix(barcode)
            if base_barcode and ss_normalization._normalize_upc_preserve_suffix_for_match(base_barcode) != ss_normalization._normalize_upc_preserve_suffix_for_match(barcode):
                matches = ss_warehouse_matching._searchrack_matches_for_barcode(searchrack_cur, base_barcode, include_zero=False)

        searchrack_conn.close()

        return jsonify({
            'success': True,
            'order': {**dict(order), 'barcode': barcode, 'stored_barcode': str(order['barcode'] or '').strip()},
            'matches': matches
        })
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': ss_errors._safe_error(e)}), 500


def match_barcode_to_sold(order_id):
    """Match/link an inventory barcode and location to a sold order. Pass empty string to clear."""
    sold_conn = None
    try:
        data = request.get_json() or {}

        # Allow empty string (for undo/clear), but key must be present
        if 'barcode' not in data:
            return jsonify({'success': False, 'error': 'barcode required'}), 400
        barcode = str(data.get('barcode') or '').strip()  # Normalize None to empty string
        location = str(data.get('location') or '').strip()  # Location from searchRack ITEM_POSITION
        searchrack_id = ss_normalization._coerce_int(data.get('searchrack_id'), 0)

        searchrack_match = None
        if searchrack_id > 0 or (barcode and location):
            rack_conn = None
            try:
                rack_conn = sqlite3.connect('searchRack.db')
                rack_conn.row_factory = sqlite3.Row
                rack_cur = rack_conn.cursor()
                if searchrack_id > 0:
                    searchrack_match = ss_warehouse_matching._ready_to_ship_searchrack_match_by_id(rack_cur, searchrack_id)
                if not searchrack_match and barcode and location:
                    searchrack_match = ss_warehouse_matching._ready_to_ship_resolve_searchrack_location_match(
                        rack_cur,
                        barcode,
                        location,
                        fallback_barcodes=[barcode]
                    )
                if searchrack_match:
                    searchrack_id = ss_normalization._coerce_int(searchrack_match.get('id'), 0)
                    if not barcode:
                        barcode = str(searchrack_match.get('barcode') or '').strip()
                    if not location:
                        location = ss_warehouse_matching._ready_to_ship_match_display_location(searchrack_match)
            except Exception as resolve_err:
                print(f"Warning: Failed to resolve Finder match row for order {order_id}: {resolve_err}")
            finally:
                try:
                    if rack_conn is not None:
                        rack_conn.close()
                except Exception:
                    pass

        # Get order details
        sold_conn = sqlite3.connect('sold.db')
        sold_conn.row_factory = sqlite3.Row
        sold_cur = sold_conn.cursor()
        ss_shipping_identity._ensure_order_finder_matches_table(sold_cur)
        sold_cur.execute('SELECT * FROM orders WHERE id = ?', (order_id,))
        order = sold_cur.fetchone()

        if not order:
            sold_conn.close()
            sold_conn = None
            return jsonify({'success': False, 'error': 'Order not found'}), 404

        old_barcode = order['barcode'] or ''
        old_location = order['location'] or ''
        old_match = sold_cur.execute(
            'SELECT searchrack_id FROM order_finder_matches WHERE order_row_id = ?',
            (order_id,)
        ).fetchone()
        old_searchrack_id = ss_normalization._coerce_int(old_match['searchrack_id'], 0) if old_match else 0

        # Update the order's barcode AND location directly
        sold_cur.execute('UPDATE orders SET barcode = ?, location = ? WHERE id = ?', (barcode, location, order_id))
        if barcode and searchrack_id > 0:
            existing = sold_cur.execute(
                'SELECT id FROM order_finder_matches WHERE order_row_id = ?',
                (order_id,)
            ).fetchone()
            if existing:
                sold_cur.execute('''
                    UPDATE order_finder_matches
                    SET searchrack_id = ?, barcode = ?, location = ?, updated_at = CURRENT_TIMESTAMP
                    WHERE order_row_id = ?
                ''', (searchrack_id, barcode, location, order_id))
            else:
                sold_cur.execute('''
                    INSERT INTO order_finder_matches (order_row_id, searchrack_id, barcode, location)
                    VALUES (?, ?, ?, ?)
                ''', (order_id, searchrack_id, barcode, location))
        else:
            sold_cur.execute('DELETE FROM order_finder_matches WHERE order_row_id = ?', (order_id,))
        from finder_aliases import update_alias
        alias_undo = update_alias(
            sold_conn, str(order_id),
            searchrack_match.get('barcode') if searchrack_match and barcode else '',
            order['title'] if data.get('finder_learn') else '',
            undo_token=data.get('finder_alias_undo')
        )
        sold_conn.commit()
        sold_conn.close()
        sold_conn = None

        # Invalidate sold-orders cache
        for days in [1, 2, 3, 5, 7, 14, 30, 60, 90, 120]:
            ss_runtime.cache.delete(f'view//sold-orders?days={days}')
        print(f"Matched order {order_id}: barcode={barcode}, location={location}")

        return jsonify({
            'success': True,
            'order_id': order_id,
            'finder_alias_undo': alias_undo,
            'old_barcode': old_barcode,
            'new_barcode': barcode,
            'old_location': old_location,
            'new_location': location,
            'old_searchrack_id': old_searchrack_id,
            'searchrack_id': searchrack_id if searchrack_id > 0 else ''
        })
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': ss_errors._safe_error(e)}), 500
    finally:
        try:
            if sold_conn is not None:
                sold_conn.close()
        except Exception:
            pass


def debug_sold_order(order_id):
    """Debug endpoint to check order state"""
    try:
        sold_conn = sqlite3.connect('sold.db')
        sold_conn.row_factory = sqlite3.Row
        sold_cur = sold_conn.cursor()
        sold_cur.execute('SELECT id, order_id, title, barcode, location, rackupdated FROM orders WHERE id = ?', (order_id,))
        order = sold_cur.fetchone()
        sold_conn.close()

        if not order:
            return jsonify({'success': False, 'error': 'Order not found'}), 404

        return jsonify({
            'success': True,
            'order': dict(order)
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


def debug_sold_order_by_barcode(barcode):
    """Debug endpoint to check orders by barcode"""
    try:
        sold_conn = sqlite3.connect('sold.db')
        sold_conn.row_factory = sqlite3.Row
        sold_cur = sold_conn.cursor()
        sold_cur.execute('SELECT id, order_id, title, barcode, location, rackupdated FROM orders WHERE barcode = ? COLLATE NOCASE ORDER BY id DESC LIMIT 10', (barcode,))
        orders = [dict(row) for row in sold_cur.fetchall()]
        sold_conn.close()

        # Also check searchRack for this barcode
        rack_conn = sqlite3.connect('searchRack.db')
        rack_conn.row_factory = sqlite3.Row
        rack_cur = rack_conn.cursor()
        rack_cur.execute('SELECT ID, TITLE, BARCODE, ITEM_POSITION, QUANTITY FROM SEARCHRACK WHERE BARCODE = ? COLLATE NOCASE', (barcode,))
        rack_items = [dict(row) for row in rack_cur.fetchall()]
        rack_conn.close()

        return jsonify({
            'success': True,
            'barcode': barcode,
            'sold_orders': orders,
            'searchrack_items': rack_items
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


def reset_sold_order(order_id):
    """Reset order's barcode and location to force fresh lookup"""
    try:
        sold_conn = sqlite3.connect('sold.db')
        sold_cur = sold_conn.cursor()
        sold_cur.execute('UPDATE orders SET location = NULL WHERE id = ?', (order_id,))
        sold_conn.commit()
        sold_conn.close()

        # Clear cache
        for days in [1, 2, 3, 5, 7, 14, 30, 60, 90, 120]:
            ss_runtime.cache.delete(f'view//sold-orders?days={days}')

        return jsonify({'success': True, 'message': f'Reset location for order {order_id}'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


def manual_remove_sold_inventory(order_id):
    """Manually remove inventory from a specific searchRack item for a sold order"""
    return jsonify({
        'success': False,
        'error': 'Manual sold removal is disabled here. Confirm the order from Ready to Ship instead.',
        'manual_mode': True,
        'redirect_url': '/ready_to_ship'
    }), 409

    try:
        from datetime import datetime

        data = request.get_json() or {}
        searchrack_id = data.get('searchrack_id')
        qty_to_remove = int(data.get('qty', 1))

        if not searchrack_id:
            return jsonify({'success': False, 'error': 'searchrack_id required'}), 400

        # Get order details
        sold_conn = sqlite3.connect('sold.db')
        sold_conn.row_factory = sqlite3.Row
        sold_cur = sold_conn.cursor()
        sold_cur.execute('SELECT * FROM orders WHERE id = ?', (order_id,))
        order = sold_cur.fetchone()

        if not order:
            sold_conn.close()
            return jsonify({'success': False, 'error': 'Order not found'}), 404

        if order['rackupdated'] == 1:
            sold_conn.close()
            return jsonify({'success': False, 'error': 'Already removed from inventory'}), 400

        # Get searchRack item
        searchrack_conn = sqlite3.connect('searchRack.db')
        searchrack_conn.row_factory = sqlite3.Row
        searchrack_cur = searchrack_conn.cursor()
        searchrack_cur.execute('SELECT ID, TITLE, BARCODE, ITEM_POSITION, QUANTITY FROM SEARCHRACK WHERE ID = ?', (searchrack_id,))
        rack_item = searchrack_cur.fetchone()

        if not rack_item:
            searchrack_conn.close()
            sold_conn.close()
            return jsonify({'success': False, 'error': 'SearchRack item not found'}), 404

        current_qty = rack_item['QUANTITY'] or 0
        new_qty = max(0, current_qty - qty_to_remove)

        # Update quantity
        searchrack_cur.execute('UPDATE SEARCHRACK SET QUANTITY = ? WHERE ID = ?', (new_qty, searchrack_id))
        searchrack_conn.commit()

        # Log to rackhistory.db
        removed_conn = sqlite3.connect('rackhistory.db')
        removed_cur = removed_conn.cursor()
        ss_inventory_history._ensure_removed_items_table(removed_cur)
        removed_cur.execute('''
            INSERT INTO removed_items
            (order_id, barcode, title, quantity_removed, removed_at, searchrack_id, old_quantity, new_quantity, removal_type, item_position)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (order['order_id'], rack_item['BARCODE'], rack_item['TITLE'] or order['title'], qty_to_remove,
              datetime.now().isoformat(), searchrack_id, current_qty, new_qty, 'manual_sold_selection', rack_item['ITEM_POSITION']))
        removed_conn.commit()
        removed_conn.close()

        # Mark order as processed
        sold_cur.execute('UPDATE orders SET rackupdated = 1 WHERE id = ?', (order_id,))
        sold_conn.commit()
        sold_conn.close()
        searchrack_conn.close()

        return jsonify({
            'success': True,
            'old_qty': current_qty,
            'new_qty': new_qty,
            'searchrack_id': searchrack_id
        })
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': ss_errors._safe_error(e)}), 500
