"""Sales actions for Sweet Shelves."""

import datetime
import sqlite3
from flask import jsonify, request
from . import (
    errors as ss_errors, inventory_history as ss_inventory_history, marketplace_removal as
    ss_marketplace_removal, normalization as ss_normalization, shipping_identity as ss_shipping_identity,
    shipping_orders as ss_shipping_orders, warehouse_matching as ss_warehouse_matching,
)


def mark_order_handled():
    """Confirm a ready-to-ship order and remove inventory exactly once."""
    data = request.get_json() or {}
    order_id = data.get('id')
    incoming_allocations = data.get('allocations')
    skip_inventory = bool(data.get('skip_inventory'))
    suggested_barcode = str(data.get('matched_barcode') or '').strip()

    if not order_id:
        return jsonify({'success': False, 'error': 'Missing order id'}), 400

    conn = None
    removal_result = {'removed': False, 'removed_units': 0, 'locations': []}
    order_ref = ''
    location_summary = ''
    try:
        conn = sqlite3.connect('sold.db')
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        ss_shipping_identity._ensure_order_removal_allocations_table(cur)
        ss_warehouse_matching._ensure_order_processing_claim_column(cur)

        cur.execute(
            'SELECT id, order_id, item_id, sku, store, barcode, source_upc, quantity, title, location, '
            'listing_listing_id, listing_sku, listing_asin, rackupdated, rackupdated_claimed_at, isHandled '
            'FROM orders WHERE id = ?',
            (order_id,)
        )
        order = cur.fetchone()
        if not order:
            return jsonify({'success': False, 'error': 'Order not found'}), 404

        current_rack = int(order['rackupdated'] or 0)
        if current_rack == 1:
            return jsonify({
                'success': False,
                'error': 'Inventory for this order has already been removed.',
                'already_handled': True
            }), 409
        if current_rack == -1:
            if ss_warehouse_matching._is_order_processing_claim_stale(order['rackupdated_claimed_at']):
                cur.execute(
                    'UPDATE orders SET rackupdated = 0, rackupdated_claimed_at = NULL WHERE id = ? AND rackupdated = -1',
                    (order_id,)
                )
                conn.commit()
                current_rack = 0
            else:
                return jsonify({
                    'success': False,
                    'error': 'This order is currently being processed — please wait a moment.',
                    'already_handled': True
                }), 409

        def _release_claim(payload, status_code=400):
            cur.execute(
                'UPDATE orders SET rackupdated = 0, rackupdated_claimed_at = NULL WHERE id = ? AND rackupdated = -1',
                (order_id,)
            )
            conn.commit()
            return jsonify(payload), status_code

        # Atomic claim: only succeeds if rackupdated is still 0.
        # Any concurrent request will see rowcount=0 and get a 409.
        cur.execute(
            "UPDATE orders SET rackupdated = -1, rackupdated_claimed_at = datetime('now') WHERE id = ? AND rackupdated = 0",
            (order_id,)
        )
        conn.commit()
        if cur.rowcount == 0:
            return jsonify({
                'success': False,
                'error': 'Another request is already processing this order.',
                'already_handled': True
            }), 409

        barcode = ss_warehouse_matching._effective_sold_order_barcode(order, prefer_manual_override=True)
        valid_fallback_barcode = (
            suggested_barcode
            if ss_warehouse_matching._ready_to_ship_valid_requested_match(order, suggested_barcode)
            else ''
        )
        sold_qty = max(1, ss_normalization._coerce_int(order['quantity'], 1))
        order_ref = (order['order_id'] or '').strip()
        title = (order['title'] or '').strip()

        # Normalise incoming allocations
        if incoming_allocations is not None:
            if not isinstance(incoming_allocations, list):
                return _release_claim({'success': False, 'error': 'allocations must be an array'}, 400)

        normalized_allocations = ss_marketplace_removal._normalize_marketplace_allocations(incoming_allocations)

        # User explicitly chose to complete without inventory removal (no barcode or no stock)
        if skip_inventory:
            cur.execute("""
                UPDATE orders
                SET isHandled = '1',
                    isHandledDate = datetime('now'),
                    shipped_time = COALESCE(shipped_time, datetime('now')),
                    rackupdated = 1,
                    rackupdated_claimed_at = NULL,
                    removal_cancelled = 0
                WHERE id = ?
            """, (order_id,))
            cur.execute('DELETE FROM order_removal_allocations WHERE order_row_id = ?', (order_id,))
            conn.commit()
            handled_row = cur.execute('SELECT COALESCE(isHandledDate, "") AS isHandledDate FROM orders WHERE id = ?', (order_id,)).fetchone()
            ss_shipping_orders._invalidate_ready_to_ship_cache()
            return jsonify({
                'success': True,
                'removed': False,
                'removed_units': 0,
                'locations': [],
                'handled_at': (handled_row['isHandledDate'] if handled_row else '')
            })

        if not barcode:
            return _release_claim({
                'success': False,
                'error': 'Order has no barcode yet. Match it first, then confirm it from Ready to Ship.'
            }, 409)

        fallback_barcodes = []
        raw_stored_barcode = str(order['barcode'] or '').strip()
        if raw_stored_barcode and raw_stored_barcode != barcode:
            fallback_barcodes.append(raw_stored_barcode)

        plan = ss_marketplace_removal._build_marketplace_removal_plan(
            barcode,
            sold_qty,
            allocations=normalized_allocations if incoming_allocations is not None else None,
            fallback_barcodes=fallback_barcodes,
            preferred_location=str(order['location'] or '').strip()
        )
        if plan.get('success', True) and not plan.get('can_fulfill') and valid_fallback_barcode:
            barcode = valid_fallback_barcode
            plan = ss_marketplace_removal._build_marketplace_removal_plan(
                barcode,
                sold_qty,
                allocations=normalized_allocations if incoming_allocations is not None else None,
                preferred_location=str(order['location'] or '').strip()
            )
        if not plan.get('success', True):
            return _release_claim({
                'success': False,
                'error': plan.get('error') or 'Unable to build an inventory removal plan'
            }, 409 if plan.get('can_fulfill') is False else 400)

        if plan.get('needs_choice'):
            return _release_claim({
                    'success': False,
                    'error': 'Location selection required for this order',
                    'location_selection_required': True,
                    'order_id': order_id,
                    'quantity': sold_qty,
                    'locations': list(plan.get('locations') or [])
                }, 400)

        if not plan.get('can_fulfill'):
            return _release_claim({
                'success': False,
                'error': f"Not enough inventory available to remove {sold_qty} unit(s)."
            }, 409)

        planned_total = sum(max(0, ss_normalization._coerce_int(step.get('quantity'), 0)) for step in (plan.get('planned_steps') or []))
        if planned_total != sold_qty:
            return _release_claim({
                'success': False,
                'error': f'Inventory plan only covers {planned_total} of {sold_qty} required unit(s).'
            }, 409)

        removal_result = ss_marketplace_removal._apply_marketplace_removal_plan(
            plan,
            order_ref=order_ref,
            barcode=barcode,
            title=title,
            removal_type='manual_handled'
        )
        if removal_result.get('error'):
            return _release_claim({'success': False, 'error': removal_result['error']}, 409)
        if removal_result.get('removed_units', 0) != sold_qty:
            return _release_claim({
                'success': False,
                'error': f"Removed {removal_result.get('removed_units', 0)} of {sold_qty} unit(s); order was not finalized."
            }, 409)

        location_summary = ', '.join([str(loc).strip() for loc in (removal_result.get('locations') or []) if str(loc).strip()])

        # Finalise order: handled + rackupdated=1 so auto-thread skips it
        cur.execute("""
            UPDATE orders
            SET isHandled = '1',
                isHandledDate = datetime('now'),
                shipped_time = COALESCE(shipped_time, datetime('now')),
                rackupdated = 1,
                rackupdated_claimed_at = NULL,
                removal_cancelled = 0,
                location = CASE WHEN ? != '' THEN ? ELSE location END
            WHERE id = ?
        """, (location_summary, location_summary, order_id))
        cur.execute('DELETE FROM order_removal_allocations WHERE order_row_id = ?', (order_id,))
        conn.commit()
        handled_row = cur.execute('SELECT COALESCE(isHandledDate, "") AS isHandledDate FROM orders WHERE id = ?', (order_id,)).fetchone()

        ss_shipping_orders._invalidate_ready_to_ship_cache()

        return jsonify({
            'success': True,
            'removed': removal_result.get('removed', False),
            'removed_units': removal_result.get('removed_units', 0),
            'locations': removal_result.get('locations', []),
            'handled_at': (handled_row['isHandledDate'] if handled_row else '')
        })

    except Exception as e:
        # If inventory was already removed, keep the sold order finalized so it
        # cannot be removed a second time on retry.
        try:
            if conn:
                if removal_result.get('removed'):
                    conn.execute("""
                        UPDATE orders
                        SET isHandled = '1',
                            isHandledDate = COALESCE(isHandledDate, datetime('now')),
                            shipped_time = COALESCE(shipped_time, datetime('now')),
                            rackupdated = 1,
                            rackupdated_claimed_at = NULL,
                            removal_cancelled = 0,
                            location = CASE WHEN ? != '' THEN ? ELSE location END
                        WHERE id = ? AND rackupdated = -1
                    """, (location_summary, location_summary, order_id))
                else:
                    conn.execute(
                        'UPDATE orders SET rackupdated = 0, rackupdated_claimed_at = NULL WHERE id = ? AND rackupdated = -1',
                        (order_id,)
                    )
                conn.commit()
        except Exception:
            pass
        return jsonify({'success': False, 'error': ss_errors._safe_error(e)}), 500
    finally:
        try:
            if conn is not None:
                conn.close()
        except Exception:
            pass


def mark_order_unhandled():
    """Undo a handled order: restore inventory exactly from the audit breadcrumbs.

    Looks up removal_type='manual_handled' records in rackhistory.db where
    undone_at IS NULL and adds back the exact quantity_removed to each
    SEARCHRACK row.  Then stamps those records with undone_at so a second
    undo cannot double-restore.  Sets rackupdated=0 and removal_cancelled=1
    so the auto-removal thread won't silently re-remove while the order
    is back in the ready-to-ship queue.
    """
    data = request.get_json() or {}
    order_id = data.get('id')

    if not order_id:
        return jsonify({'success': False, 'error': 'Missing order id'}), 400

    conn = None
    rack_conn = None
    rh_conn = None
    try:
        conn = sqlite3.connect('sold.db')
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        ss_warehouse_matching._ensure_order_processing_claim_column(cur)

        cur.execute('SELECT id, order_id FROM orders WHERE id = ?', (order_id,))
        order = cur.fetchone()
        if not order:
            return jsonify({'success': False, 'error': 'Order not found'}), 404

        order_ref = (order['order_id'] or '').strip()
        now_iso = datetime.datetime.now().isoformat()

        # Look up the audit breadcrumbs for this order
        ss_inventory_history._flush_searchrack_history_outbox()
        rh_conn = sqlite3.connect('rackhistory.db')
        rh_conn.row_factory = sqlite3.Row
        rh_cur = rh_conn.cursor()
        ss_inventory_history._ensure_removed_items_table(rh_cur)

        rh_cur.execute('''
            SELECT id, searchrack_id, quantity_removed, barcode, title, item_position,
                   old_quantity, new_quantity,
                   source_row_json, from_position, to_position
            FROM removed_items
            WHERE order_id = ?
              AND removal_type = 'manual_handled'
              AND (undone_at IS NULL OR undone_at = '')
        ''', (order_ref,))
        audit_rows = list(rh_cur.fetchall())

        restored_count = 0
        if audit_rows:
            rack_conn = sqlite3.connect('searchRack.db')
            rack_conn.row_factory = sqlite3.Row
            rack_cur = rack_conn.cursor()
            ss_inventory_history._ensure_searchrack_undo_claims(rack_cur)
            rack_conn.commit()
            rack_cur.execute('BEGIN IMMEDIATE')
            restored_row_ids = set()
            restore_results = []

            for row in audit_rows:
                qty_to_restore = ss_inventory_history._history_restore_quantity(row)
                if qty_to_restore <= 0:
                    continue

                restored = ss_inventory_history._restore_searchrack_with_undo_claim(rack_cur, row, qty_to_restore)
                sid = restored['searchrack_id']
                restore_results.append((row, restored))
                restored_count += 1
                restored_row_ids.add(sid)

            rack_conn.commit()
            for row, restored in restore_results:
                rh_cur.execute(
                    'UPDATE removed_items SET undone_at = ? WHERE id = ? AND (undone_at IS NULL OR undone_at = "")',
                    (now_iso, row['id'])
                )
                ss_inventory_history._insert_inventory_undo_history(
                    rh_cur,
                    row,
                    restored,
                    'manual_handled_undo',
                    order_id=order_ref,
                    undone_at=now_iso,
                )
            rh_conn.commit()
            for sid in restored_row_ids:
                ss_inventory_history._clear_zero_qty_pending_deletions(sid)

        # Return order to ready-to-ship queue.
        # removal_cancelled=1 prevents the auto-thread from silently re-removing it.
        ss_shipping_identity._ensure_order_removal_allocations_table(cur)
        cur.execute("""
            UPDATE orders
            SET isHandled = '',
                isHandledDate = NULL,
                rackupdated = 0,
                rackupdated_claimed_at = NULL,
                removal_cancelled = 1
            WHERE id = ?
        """, (order_id,))
        cur.execute('DELETE FROM order_removal_allocations WHERE order_row_id = ?', (order_id,))
        conn.commit()

        ss_shipping_orders._invalidate_ready_to_ship_cache()

        return jsonify({'success': True, 'restored': restored_count, 'handled_at': ''})
    except Exception as e:
        for _c in (rh_conn, rack_conn, conn):
            try:
                if _c is not None:
                    _c.rollback()
            except Exception:
                pass
        return jsonify({'success': False, 'error': ss_errors._safe_error(e)}), 500
    finally:
        for _c in (rh_conn, rack_conn, conn):
            try:
                if _c is not None:
                    _c.close()
            except Exception:
                pass
