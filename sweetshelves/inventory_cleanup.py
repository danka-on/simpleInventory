"""Inventory cleanup for Sweet Shelves."""

import os
import sqlite3
import threading
import time
from flask import jsonify, make_response, render_template, request
from . import (
    errors as ss_errors, inventory_history as ss_inventory_history, normalization as ss_normalization,
    prep_schema as ss_prep_schema, runtime as ss_runtime, shipping_identity as ss_shipping_identity,
    warehouse_allocations as ss_warehouse_allocations, warehouse_matching as ss_warehouse_matching,
)


def _purge_expired_trash():
    conn = None
    try:
        ss_prep_schema._ensure_items_prep_tables()
        conn = sqlite3.connect('bol.db')
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute("SELECT id, trash_path FROM items_prep_images WHERE deleted_at IS NOT NULL AND expires_at IS NOT NULL AND expires_at <= ?", (ss_normalization._now_iso(),))
        rows = cur.fetchall()
        count = 0
        for r in rows:
            tr = r['trash_path']
            if tr:
                abs_path = os.path.join(ss_runtime.app.root_path, 'static', tr)
                try:
                    if os.path.isfile(abs_path):
                        os.remove(abs_path)
                except Exception as fe:
                    print('Purge remove failed:', fe)
            try:
                cur.execute('DELETE FROM items_prep_images WHERE id = ?', (r['id'],))
                count += 1
            except Exception as de:
                print('Purge delete row failed:', de)
        conn.commit()
        return count
    except Exception as e:
        print('Purge error:', e)
        return 0
    finally:
        if conn is not None:
            conn.close()


_trash_purger_started = False


def _start_trash_purger_thread():
    global _trash_purger_started
    if _trash_purger_started:
        return
    _trash_purger_started = True
    def _runner():
        import time as _time
        while True:
            try:
                _purge_expired_trash()
            except Exception as e:
                print('Trash purge tick error:', e)
            _time.sleep(24*60*60)
    t = threading.Thread(target=_runner, daemon=True)
    t.start()


_zero_qty_deleter_started = False


_automatic_removal_started = False


def _purge_zero_qty_items():
    """Enforce active-stock-only SEARCHRACK and safely archive any legacy zero rows."""
    try:
        summary = ss_inventory_history._initialize_searchrack_history_guard()
        print(
            "✅ Active inventory guard ready: "
            f"normalized={summary.get('normalized', 0)}, "
            f"removed_zero_rows={summary.get('deleted', 0)}, "
            f"history_events_mirrored={summary.get('mirrored', 0)}"
        )
        return summary
    except Exception as e:
        print(f"❌ Error enforcing zero-quantity cleanup: {e}")
        return {'installed': False, 'error': str(e)}


def _start_zero_qty_deleter_thread():
    """Install the guard and keep retrying any rack-history outbox events."""
    global _zero_qty_deleter_started
    if _zero_qty_deleter_started:
        return
    _zero_qty_deleter_started = True
    _purge_zero_qty_items()

    def _outbox_runner():
        while True:
            try:
                ss_inventory_history._flush_searchrack_history_outbox(limit=250, settle_seconds=2)
            except Exception as flush_error:
                print(f"Warning: rack-history outbox retry failed: {flush_error}")
            time.sleep(60)

    threading.Thread(target=_outbox_runner, daemon=True).start()
    print("🚀 Active-inventory guard and rack-history outbox worker started")


def _process_automatic_inventory_removals():
    """Automatic sold-order removal is disabled; Ready to Ship confirmation is authoritative."""
    print("ℹ️ Automatic sold-order inventory removal is disabled; waiting for Ready to Ship confirmation.")
    return
    from datetime import datetime, timedelta
    sold_conn = None
    searchrack_conn = None

    try:
        print("🔄 Checking for automatic inventory removals...")
        sold_conn = sqlite3.connect('sold.db')
        sold_conn.row_factory = sqlite3.Row
        sold_cur = sold_conn.cursor()
        ss_shipping_identity._ensure_order_removal_allocations_table(sold_cur)
        
        # Get grace period setting
        sold_cur.execute("CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT)")
        sold_conn.commit()
        sold_cur.execute("SELECT value FROM settings WHERE key = 'removal_grace_hours'")
        row = sold_cur.fetchone()
        grace_period_hours = float(row[0]) if row and str(row[0]).strip() else 48
        
        print(f"   Grace period: {grace_period_hours} hours ({grace_period_hours * 60:.1f} minutes)")
        
        # Get orders eligible for automatic removal - MUST include shipped_time
        sold_cur.execute('''
            SELECT id, order_id, barcode, source_upc, quantity, title, shipped_time
            FROM orders 
            WHERE rackupdated = 0 
            AND shipped_time IS NOT NULL 
            AND shipped_time != ''
            AND barcode IS NOT NULL
            AND barcode != ''
            AND COALESCE(removal_cancelled, 0) = 0
        ''')
        orders = sold_cur.fetchall()
        
        print(f"   Found {len(orders)} orders with shipped_time")
        
        now = datetime.now()
        
        searchrack_conn = sqlite3.connect('searchRack.db')
        searchrack_conn.row_factory = sqlite3.Row
        searchrack_cur = searchrack_conn.cursor()
        searchrack_schema = ss_warehouse_matching._searchrack_removal_schema(searchrack_cur)
        barcode_col = searchrack_schema.get('barcode_col')
        qty_col = searchrack_schema.get('qty_col')
        id_lookup_col = searchrack_schema.get('id_col') or 'rowid'

        if not barcode_col or not qty_col:
            print("❌ Automatic removal skipped: SEARCHRACK is missing BARCODE/UPC or QUANTITY/QTY columns")
            return

        def _mark_zero_qty_for_deletion(item_id):
            try:
                searchrack_cur.execute("SELECT value FROM zero_qty_settings WHERE key = 'interval_minutes'")
                settings = searchrack_cur.fetchone()
                interval = int(settings[0]) if settings else 60
                delete_at = datetime.now() + timedelta(minutes=interval)
                searchrack_cur.execute(
                    'SELECT id, deletion_cancelled FROM zero_qty_pending_deletion WHERE searchrack_id = ?',
                    (item_id,)
                )
                existing = searchrack_cur.fetchone()
                if not existing:
                    searchrack_cur.execute('''
                        INSERT INTO zero_qty_pending_deletion (searchrack_id, marked_at, delete_at, deletion_cancelled)
                        VALUES (?, ?, ?, 0)
                    ''', (item_id, datetime.now().isoformat(), delete_at.isoformat()))
                elif existing[1] == 1:
                    searchrack_cur.execute('''
                        UPDATE zero_qty_pending_deletion
                        SET deletion_cancelled = 0, marked_at = ?, delete_at = ?
                        WHERE searchrack_id = ?
                    ''', (datetime.now().isoformat(), delete_at.isoformat(), item_id))
            except Exception as mark_error:
                print(f"  ❌ Error marking item {item_id} for zero-qty deletion: {mark_error}")
                import traceback
                traceback.print_exc()
        
        # ── Crash-recovery guard ──────────────────────────────────────────────
        # If the process crashed after writing to rackhistory.db but before
        # marking rackupdated=1 in sold.db, the order would loop forever and
        # risk a double-removal on the next run.  Detect this by cross-checking
        # the audit log: any order_id that already has a removal record but
        # still shows rackupdated=0 is assumed complete — mark it now.
        try:
            with sqlite3.connect('rackhistory.db') as _rh_conn:
                _rh_conn.row_factory = sqlite3.Row
                _rh_cur = _rh_conn.cursor()
                ss_inventory_history._ensure_removed_items_table(_rh_cur)
                _rh_cur.execute('SELECT DISTINCT order_id FROM removed_items')
                _already_in_audit = {r['order_id'] for r in _rh_cur.fetchall()}
        except Exception:
            _already_in_audit = set()

        _recovered = 0
        for _o in orders:
            if _o['order_id'] in _already_in_audit:
                sold_cur.execute(
                    'UPDATE orders SET rackupdated = 1 WHERE id = ? AND rackupdated = 0',
                    (_o['id'],)
                )
                _recovered += 1
                print(f"  🔧 Crash-recovery: order {_o['order_id']} found in audit log — marking complete")
        if _recovered:
            sold_conn.commit()
            orders = [o for o in orders if o['order_id'] not in _already_in_audit]
            print(f"  🔧 Crash-recovered {_recovered} order(s); {len(orders)} remain to process")
        # ─────────────────────────────────────────────────────────────────────

        processed_count = 0

        for order in orders:
            try:
                shipped_str = order['shipped_time']
                
                # Parse shipped time
                if 'T' in shipped_str:
                    if shipped_str.endswith('Z'):
                        shipped_dt = datetime.fromisoformat(shipped_str.replace('Z', '+00:00'))
                    else:
                        shipped_dt = datetime.fromisoformat(shipped_str)
                else:
                    shipped_dt = datetime.fromisoformat(shipped_str)
                
                if shipped_dt.tzinfo:
                    shipped_dt = shipped_dt.replace(tzinfo=None)
                
                hours_since_shipped = (now - shipped_dt).total_seconds() / 3600
                
                # Check if eligible for removal (past grace period)
                if hours_since_shipped < grace_period_hours:
                    continue
                
                barcode = ss_warehouse_matching._effective_sold_order_barcode(order)
                sold_qty = max(1, ss_normalization._coerce_int(order['quantity'], 1))

                matches = ss_warehouse_matching._searchrack_matches_for_barcode(
                    searchrack_cur,
                    barcode,
                    schema=searchrack_schema,
                    include_zero=False
                )
                # For suffixed barcodes (e.g. "035886267162-1") that come from
                # eBay/Amazon SKUs, inventory may be stored under the base barcode
                # ("035886267162").  Fall back to base-only search when no suffix
                # match is found so these orders can still be fulfilled.
                _used_base_fallback = False
                if not matches and '-' in str(barcode or ''):
                    _base_only = str(barcode).split('-', 1)[0].strip()
                    if _base_only:
                        matches = ss_warehouse_matching._searchrack_matches_for_barcode(
                            searchrack_cur,
                            _base_only,
                            schema=searchrack_schema,
                            include_zero=False
                        )
                        if matches:
                            _used_base_fallback = True
                            print(f"  ℹ️  Order {order['order_id']}: suffixed barcode {barcode} not in inventory; matched base {_base_only}")
                if not matches:
                    print(f"  ⏭️  Order {order['order_id']}: Item not in inventory yet (barcode: {barcode}) - will retry later")
                    continue

                location_groups = ss_warehouse_matching._group_searchrack_matches_by_location(matches)
                saved_allocations = ss_warehouse_allocations._load_order_removal_allocations(sold_cur, order['id'])

                planned_steps = []
                using_saved_allocations = False

                if len(location_groups) > 1:
                    if not saved_allocations:
                        print(f"  ⏭️  Order {order['order_id']}: multiple locations found; awaiting explicit location selection")
                        continue

                    # Check if the allocation is stale (older than the grace period).
                    # A stale allocation whose location is now gone or under-stocked
                    # would deadlock forever — clear it so the user is re-prompted.
                    def _alloc_is_stale(allocs):
                        for a in allocs:
                            ts = (a.get('created_at') or '').strip()
                            if not ts:
                                return True
                            try:
                                alloc_age_hours = (now - datetime.fromisoformat(ts)).total_seconds() / 3600
                                if alloc_age_hours > grace_period_hours:
                                    return True
                            except Exception:
                                return True
                        return False

                    using_saved_allocations = True
                    group_by_key = {g['location_key']: g for g in location_groups}
                    _alloc_validation_failed = False

                    for alloc in saved_allocations:
                        alloc_qty = max(0, ss_normalization._coerce_int(alloc.get('quantity'), 0))
                        if alloc_qty <= 0:
                            continue
                        alloc_key = ss_warehouse_matching._sold_location_key(alloc.get('location_key') or alloc.get('location_code'))
                        group = group_by_key.get(alloc_key)
                        if not group:
                            planned_steps = []
                            _alloc_validation_failed = True
                            print(
                                f"  ⏭️  Order {order['order_id']}: saved location {alloc.get('location_code')} not found in current inventory; waiting"
                            )
                            break
                        if alloc_qty > max(0, ss_normalization._coerce_int(group.get('available_qty'), 0)):
                            planned_steps = []
                            _alloc_validation_failed = True
                            print(
                                f"  ⏭️  Order {order['order_id']}: saved qty for {group['location_code']} exceeds available inventory; waiting"
                            )
                            break

                    # If validation failed AND allocation is stale, clear it so user
                    # is re-prompted rather than having the order deadlock forever.
                    if _alloc_validation_failed and _alloc_is_stale(saved_allocations):
                        sold_cur.execute(
                            'DELETE FROM order_removal_allocations WHERE order_row_id = ?',
                            (order['id'],)
                        )
                        sold_conn.commit()
                        print(f"  🗑️  Order {order['order_id']}: stale allocation cleared — user will be re-prompted")

                        remaining = alloc_qty
                        for row_match in group['rows']:
                            if remaining <= 0:
                                break
                            available = max(0, ss_normalization._coerce_int(row_match.get('quantity'), 0))
                            if available <= 0:
                                continue
                            take = min(available, remaining)
                            if take > 0:
                                planned_steps.append({
                                    'searchrack_id': int(row_match['id']),
                                    'location_code': group['location_code'],
                                    'quantity': int(take)
                                })
                                remaining -= take
                        if remaining > 0:
                            planned_steps = []
                            print(
                                f"  ⏭️  Order {order['order_id']}: insufficient inventory in selected location {group['location_code']}; waiting"
                            )
                            break

                    if not planned_steps:
                        continue

                    # Respect sold quantity exactly even if stale allocations sum higher.
                    keep = sold_qty
                    trimmed = []
                    for step in planned_steps:
                        if keep <= 0:
                            break
                        take = min(int(step['quantity']), keep)
                        if take > 0:
                            trimmed.append({
                                'searchrack_id': step['searchrack_id'],
                                'location_code': step['location_code'],
                                'quantity': take
                            })
                            keep -= take
                    if keep > 0:
                        print(f"  ⏭️  Order {order['order_id']}: selected location quantities do not cover sold qty ({sold_qty})")
                        continue
                    planned_steps = trimmed
                else:
                    if not location_groups:
                        print(f"  ⏭️  Order {order['order_id']}: no matching location rows with quantity > 0; will retry later")
                        continue
                    remaining = sold_qty
                    for row_match in location_groups[0]['rows']:
                        if remaining <= 0:
                            break
                        available = max(0, ss_normalization._coerce_int(row_match.get('quantity'), 0))
                        if available <= 0:
                            continue
                        take = min(available, remaining)
                        if take > 0:
                            planned_steps.append({
                                'searchrack_id': int(row_match['id']),
                                'location_code': location_groups[0]['location_code'],
                                'quantity': int(take)
                            })
                            remaining -= take
                    if remaining > 0:
                        print(f"  ⏭️  Order {order['order_id']}: insufficient inventory to remove {sold_qty}; will retry later")
                        continue

                # Merge by SEARCHRACK row id in case stale data contains duplicates.
                merged_steps = []
                merged_index = {}
                for step in planned_steps:
                    sid = int(step['searchrack_id'])
                    if sid in merged_index:
                        idx = merged_index[sid]
                        merged_steps[idx]['quantity'] += int(step['quantity'])
                    else:
                        merged_index[sid] = len(merged_steps)
                        merged_steps.append({
                            'searchrack_id': sid,
                            'location_code': step['location_code'],
                            'quantity': int(step['quantity'])
                        })

                # Re-read row quantities to ensure nothing changed before applying updates.
                recheck = {}
                recheck_ok = True
                for step in merged_steps:
                    sid = int(step['searchrack_id'])
                    searchrack_cur.execute(
                        f'''
                            SELECT {qty_col} AS qty,
                                   COALESCE({searchrack_schema.get('pos_col') or "''"}, '') AS item_position,
                                   COALESCE({searchrack_schema.get('pic_col') or "''"}, '') AS pictureposition
                            FROM SEARCHRACK
                            WHERE {id_lookup_col} = ?
                        ''',
                        (sid,)
                    )
                    row_now = searchrack_cur.fetchone()
                    if not row_now:
                        recheck_ok = False
                        print(f"  ⏭️  Order {order['order_id']}: selected inventory row {sid} no longer exists; waiting")
                        break
                    current_qty = max(0, ss_normalization._coerce_int(row_now['qty'], 0))
                    remove_qty = max(0, ss_normalization._coerce_int(step['quantity'], 0))
                    if remove_qty <= 0 or current_qty < remove_qty:
                        recheck_ok = False
                        print(
                            f"  ⏭️  Order {order['order_id']}: selected row {sid} has qty {current_qty}, cannot remove {remove_qty}; waiting"
                        )
                        break
                    recheck[sid] = {
                        'current_qty': current_qty,
                        'item_position': str(row_now['item_position'] or row_now['pictureposition'] or step['location_code'] or '').strip()
                    }

                if not recheck_ok:
                    continue

                removed_conn = sqlite3.connect('rackhistory.db')
                try:
                    removed_cur = removed_conn.cursor()
                    ss_inventory_history._ensure_removed_items_table(removed_cur)

                    for step in merged_steps:
                        sid = int(step['searchrack_id'])
                        remove_qty = max(0, ss_normalization._coerce_int(step['quantity'], 0))
                        if remove_qty <= 0:
                            continue
                        old_qty = recheck[sid]['current_qty']
                        new_qty = max(0, old_qty - remove_qty)

                        searchrack_cur.execute(
                            f'UPDATE SEARCHRACK SET {qty_col} = ? WHERE {id_lookup_col} = ?',
                            (new_qty, sid)
                        )

                        removed_cur.execute('''
                            INSERT INTO removed_items
                            (order_id, barcode, title, quantity_removed, removed_at, searchrack_id,
                             old_quantity, new_quantity, removal_type, item_position, event_status)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending')
                        ''', (
                            order['order_id'],
                            barcode,
                            order['title'],
                            remove_qty,
                            now.isoformat(),
                            sid,
                            old_qty,
                            new_qty,
                            'automatic_allocated' if using_saved_allocations else 'automatic',
                            recheck[sid]['item_position']
                        ))

                        if new_qty == 0:
                            _mark_zero_qty_for_deletion(sid)

                    removed_conn.commit()
                finally:
                    removed_conn.close()

                sold_cur.execute('UPDATE orders SET rackupdated = 1 WHERE id = ?', (order['id'],))
                sold_cur.execute('DELETE FROM order_removal_allocations WHERE order_row_id = ?', (order['id'],))
                processed_count += 1
                print(
                    f"  ✓ Order {order['order_id']}: removed {sold_qty} from "
                    f"{'selected locations' if using_saved_allocations else 'single location inventory'}"
                )
                
            except Exception as e:
                print(f"  ❌ Error processing order {order['order_id']}: {e}")
                continue
        
        searchrack_conn.commit()
        
        sold_conn.commit()
        
        if processed_count > 0:
            print(f"✅ Automatically processed {processed_count} inventory removals")
    
    except Exception as e:
        print(f"❌ Error in automatic inventory removal: {e}")
        import traceback
        traceback.print_exc()
    finally:
        try:
            if sold_conn is not None:
                sold_conn.close()
        except Exception:
            pass
        try:
            if searchrack_conn is not None:
                searchrack_conn.close()
        except Exception:
            pass


def _start_automatic_removal_thread():
    """Automatic sold-order removal is disabled; no background worker is started."""
    global _automatic_removal_started
    if _automatic_removal_started:
        return
    _automatic_removal_started = True
    print("ℹ️ Automatic inventory removal worker disabled; sold orders must be confirmed in Ready to Ship.")


def api_get_grace_period():
    conn = None
    try:
        conn = sqlite3.connect('sold.db')
        cur = conn.cursor()
        cur.execute("CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT)")
        conn.commit()
        cur.execute("SELECT value FROM settings WHERE key = 'removal_grace_hours'")
        row = cur.fetchone()
        hours = float(row[0]) if row and str(row[0]).strip() else 48
        return jsonify({'success': True, 'hours': hours})
    except Exception as e:
        return jsonify({'success': False, 'error': ss_errors._safe_error(e)}), 500
    finally:
        if conn is not None:
            conn.close()


def api_set_grace_period():
    conn = None
    try:
        data = request.get_json(force=True) if request.is_json else {}
        hours = float(data.get('hours', 48))  # Allow fractional hours for testing (e.g., 0.0167 = 1 minute)
        if hours < 0 or hours > 240:
            return jsonify({'success': False, 'error': 'hours out of range (0-240)'}), 400
        conn = sqlite3.connect('sold.db')
        cur = conn.cursor()
        cur.execute("CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT)")
        conn.commit()
        cur.execute("INSERT INTO settings (key, value) VALUES ('removal_grace_hours', ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value", (str(hours),))
        conn.commit()
        return jsonify({'success': True, 'hours': hours})
    except Exception as e:
        return jsonify({'success': False, 'error': ss_errors._safe_error(e)}), 500
    finally:
        if conn is not None:
            conn.close()


def api_get_zero_qty_settings():
    conn = None
    try:
        conn = sqlite3.connect('searchRack.db')
        cur = conn.cursor()
        cur.execute('''
            CREATE TABLE IF NOT EXISTS zero_qty_settings (
                key TEXT PRIMARY KEY,
                value TEXT
            )
        ''')
        
        # Get show_display setting
        cur.execute("SELECT value FROM zero_qty_settings WHERE key = 'show_display'")
        row = cur.fetchone()
        show_display = row and row[0] == 'true'
        
        # Get interval_minutes setting
        cur.execute("SELECT value FROM zero_qty_settings WHERE key = 'interval_minutes'")
        row = cur.fetchone()
        interval_minutes = int(row[0]) if row else 1440
        
        
        return jsonify({
            'success': True,
            'show_display': show_display,
            'interval_minutes': interval_minutes
        })
    except Exception as e:
        return jsonify({'success': False, 'error': ss_errors._safe_error(e)}), 500
    finally:
        if conn is not None:
            conn.close()


def api_set_zero_qty_settings():
    conn = None
    try:
        data = request.get_json() or {}
        show_display = data.get('show_display', False)
        interval_minutes = int(data.get('interval_minutes', 1440))
        
        conn = sqlite3.connect('searchRack.db')
        cur = conn.cursor()
        cur.execute('''
            CREATE TABLE IF NOT EXISTS zero_qty_settings (
                key TEXT PRIMARY KEY,
                value TEXT
            )
        ''')
        
        cur.execute('''
            INSERT INTO zero_qty_settings (key, value) 
            VALUES ('show_display', ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
        ''', ('true' if show_display else 'false',))
        
        cur.execute('''
            INSERT INTO zero_qty_settings (key, value) 
            VALUES ('interval_minutes', ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
        ''', (str(interval_minutes),))
        
        conn.commit()
        
        # Update the deletion intervals for items already in the queue
        if interval_minutes != 1440:  # If not default 24 hours
            from datetime import datetime, timedelta
            cur.execute('SELECT id, marked_at FROM zero_qty_pending_deletion')
            pending_items = cur.fetchall()
            
            for item_id, marked_at in pending_items:
                marked_dt = datetime.fromisoformat(marked_at)
                new_delete_at = marked_dt + timedelta(minutes=interval_minutes)
                cur.execute('UPDATE zero_qty_pending_deletion SET delete_at = ? WHERE id = ?',
                           (new_delete_at.isoformat(), item_id))
            
            conn.commit()
        
        
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': ss_errors._safe_error(e)}), 500
    finally:
        if conn is not None:
            conn.close()


def api_get_zero_qty_pending():
    summary = _purge_zero_qty_items()
    return jsonify({
        'success': True,
        'items': [],
        'manual_mode': False,
        'immediate_cleanup': True,
        'removed': summary.get('deleted', 0),
        'mirrored': summary.get('mirrored', 0),
        'message': 'Zero-quantity rows are archived to Rack History and removed from active inventory immediately.'
    })


def api_zero_qty_delete_now(barcode):
    """Compatibility endpoint: run the immediate active-inventory cleanup."""
    summary = _purge_zero_qty_items()
    return jsonify({
        'success': bool(summary.get('installed')),
        'removed': summary.get('deleted', 0),
        'message': 'Zero-quantity cleanup completed; history snapshots were preserved.'
    })


def api_zero_qty_cancel(barcode):
    """The retired delayed queue can no longer cancel immediate cleanup."""
    return jsonify({
        'success': False,
        'immediate_cleanup': True,
        'error': 'Zero-quantity cleanup is immediate and cannot be cancelled; use Rack History to restore an eligible removal.'
    }), 409


def api_zero_qty_allow(barcode):
    """Compatibility endpoint for the retired delayed queue."""
    return jsonify({
        'success': True,
        'immediate_cleanup': True,
        'message': 'Immediate cleanup is already enabled.'
    })


def api_trigger_zero_qty_purge():
    """Run legacy-row cleanup now and mirror its durable snapshots to Rack History."""
    summary = _purge_zero_qty_items()
    return jsonify({
        'success': bool(summary.get('installed')),
        'immediate_cleanup': True,
        'removed': summary.get('deleted', 0),
        'normalized': summary.get('normalized', 0),
        'mirrored': summary.get('mirrored', 0),
        'message': 'Zero-quantity rows were archived and removed from active inventory.'
    })


def cleanup_page():
    response = make_response(render_template('cleanup.html'))
    response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    response.headers['Pragma'] = 'no-cache'
    response.headers['Expires'] = '0'
    return response
