"""Returns for Sweet Shelves."""

import sqlite3
from flask import jsonify, render_template, request
from . import errors as ss_errors


def returns_page():
    """Returns management page."""
    return render_template('returns.html')


def _normalize_return_text(value):
    if value is None:
        return ''
    return str(value).strip()


def _return_signature_from_row(row):
    original_order_id = row['original_order_id'] if isinstance(row, sqlite3.Row) else row.get('original_order_id')
    if original_order_id is None or str(original_order_id).strip() == '':
        original_key = None
    else:
        original_key = str(original_order_id).strip()

    order_id = _normalize_return_text(row['order_id'] if isinstance(row, sqlite3.Row) else row.get('order_id'))
    item_id = _normalize_return_text(row['item_id'] if isinstance(row, sqlite3.Row) else row.get('item_id'))
    barcode = _normalize_return_text(row['barcode'] if isinstance(row, sqlite3.Row) else row.get('barcode'))
    title = _normalize_return_text(row['title'] if isinstance(row, sqlite3.Row) else row.get('title'))
    return_date = _normalize_return_text(row['return_date'] if isinstance(row, sqlite3.Row) else row.get('return_date'))
    store = _normalize_return_text(row['store'] if isinstance(row, sqlite3.Row) else row.get('store')).lower()

    return (
        original_key,
        order_id,
        item_id,
        barcode,
        title,
        return_date,
        store
    )


def _returns_find_duplicate_ids(cur, source_row):
    source_sig = _return_signature_from_row(source_row)
    cur.execute('''
        SELECT id, original_order_id, order_id, item_id, barcode, title, return_date, store
        FROM returns
    ''')
    ids = set()
    for row in cur.fetchall():
        if _return_signature_from_row(row) == source_sig:
            ids.add(int(row['id']))
    return sorted(ids)


def _returns_row_value(row, key, default=None):
    if row is None:
        return default
    if isinstance(row, sqlite3.Row):
        return row[key] if key in row.keys() else default
    if isinstance(row, dict):
        return row.get(key, default)
    return default


def _returns_group_ids_for_return(cur, return_id):
    cur.execute('''
        SELECT id, original_order_id, order_id, item_id, barcode, title, return_date, store,
               received_date, restocked, relisted, relisted_date, relisted_store, relisted_item_id,
               resold, resold_date, resold_order_id, lifecycle_count, location
        FROM returns
        WHERE id = ?
    ''', (return_id,))
    row = cur.fetchone()
    if not row:
        return None, []

    group_ids = _returns_find_duplicate_ids(cur, row)
    if not group_ids:
        group_ids = [int(return_id)]
    return row, sorted({int(x) for x in group_ids})


def _returns_query_events_for_ids(cur, return_ids):
    ids = sorted({int(x) for x in return_ids if x is not None})
    if not ids:
        return []

    placeholders = ','.join('?' for _ in ids)
    cur.execute(f'''
        SELECT id, return_id, event_type, event_date, store, item_id, order_id, notes
        FROM return_lifecycle_events
        WHERE return_id IN ({placeholders})
        ORDER BY event_date ASC, id ASC
    ''', ids)
    return cur.fetchall()


def _returns_status_from_row(row):
    def _as_int(value, default=0):
        try:
            return int(float(value or 0))
        except (TypeError, ValueError):
            return default

    if _as_int(_returns_row_value(row, 'resold', 0)) == 1:
        return 'resold'
    if _as_int(_returns_row_value(row, 'relisted', 0)) == 1:
        return 'relisted'
    if _as_int(_returns_row_value(row, 'restocked', 0)) == 1:
        return 'restocked'
    if _returns_row_value(row, 'received_date'):
        return 'received'
    return 'pending'


def _returns_calculate_state(events, fallback_row=None):
    def _as_int(value, default=0):
        try:
            return int(float(value or 0))
        except (TypeError, ValueError):
            return default

    fallback_received_date = _returns_row_value(fallback_row, 'received_date')
    fallback_restocked = _as_int(_returns_row_value(fallback_row, 'restocked', 0))
    fallback_relisted = _as_int(_returns_row_value(fallback_row, 'relisted', 0))
    fallback_relisted_date = _returns_row_value(fallback_row, 'relisted_date')
    fallback_relisted_store = _returns_row_value(fallback_row, 'relisted_store')
    fallback_relisted_item_id = _returns_row_value(fallback_row, 'relisted_item_id')
    fallback_resold = _as_int(_returns_row_value(fallback_row, 'resold', 0))
    fallback_resold_date = _returns_row_value(fallback_row, 'resold_date')
    fallback_resold_order_id = _returns_row_value(fallback_row, 'resold_order_id')
    fallback_location = _returns_row_value(fallback_row, 'location')
    fallback_lifecycle_count = max(1, _as_int(_returns_row_value(fallback_row, 'lifecycle_count', 1), 1))

    if events:
        received_date = None
        restocked = 0
        relisted = 0
        relisted_date = None
        relisted_store = None
        relisted_item_id = None
        resold = 0
        resold_date = None
        resold_order_id = None
        location = fallback_location
    else:
        received_date = fallback_received_date
        restocked = fallback_restocked
        relisted = fallback_relisted
        relisted_date = fallback_relisted_date
        relisted_store = fallback_relisted_store
        relisted_item_id = fallback_relisted_item_id
        resold = fallback_resold
        resold_date = fallback_resold_date
        resold_order_id = fallback_resold_order_id
        location = fallback_location

    restart_events = 0

    for event in events:
        event_type = _normalize_return_text(_returns_row_value(event, 'event_type')).lower()
        event_date = _returns_row_value(event, 'event_date')
        notes = _returns_row_value(event, 'notes', '') or ''

        if event_type == 'received':
            received_date = event_date or received_date
            continue
        if event_type == 'restocked':
            restocked = 1
            if not received_date:
                received_date = event_date
            prefix = 'Restocked to shelf:'
            if isinstance(notes, str) and notes.startswith(prefix):
                parsed_location = notes[len(prefix):].strip()
                if parsed_location:
                    location = parsed_location
            continue
        if event_type == 'relisted':
            relisted = 1
            restocked = 1
            if not received_date:
                received_date = event_date
            relisted_date = event_date
            relisted_store = _returns_row_value(event, 'store')
            relisted_item_id = _returns_row_value(event, 'item_id')
            continue
        if event_type == 'resold':
            resold = 1
            relisted = 1
            restocked = 1
            if not received_date:
                received_date = event_date
            if not relisted_date:
                relisted_date = event_date
            resold_date = event_date
            resold_order_id = _returns_row_value(event, 'order_id')
            continue
        if event_type in ('returned_again', 'lifecycle_restart'):
            restart_events += 1
            restocked = 0
            relisted = 0
            relisted_date = None
            relisted_store = None
            relisted_item_id = None
            resold = 0
            resold_date = None
            resold_order_id = None
            if event_date:
                received_date = event_date
            continue
        # Legacy synthetic event created by an old relist bug.
        if event_type == 'sold':
            continue

    if events:
        lifecycle_count = max(1, restart_events + 1)
    else:
        lifecycle_count = max(1, fallback_lifecycle_count)

    state = {
        'received_date': received_date,
        'restocked': 1 if restocked else 0,
        'relisted': 1 if relisted else 0,
        'relisted_date': relisted_date,
        'relisted_store': relisted_store,
        'relisted_item_id': relisted_item_id,
        'resold': 1 if resold else 0,
        'resold_date': resold_date,
        'resold_order_id': resold_order_id,
        'lifecycle_count': lifecycle_count,
        'location': location
    }
    state['status'] = _returns_status_from_row(state)
    return state


def _returns_recompute_group_state(cur, return_ids, fallback_row=None):
    group_ids = sorted({int(x) for x in return_ids if x is not None})
    if not group_ids:
        return None

    placeholders = ','.join('?' for _ in group_ids)
    cur.execute(f'''
        SELECT id, received_date, restocked, relisted, relisted_date, relisted_store, relisted_item_id,
               resold, resold_date, resold_order_id, lifecycle_count, location
        FROM returns
        WHERE id IN ({placeholders})
        ORDER BY id DESC
    ''', group_ids)
    group_rows = cur.fetchall()
    if not group_rows:
        return None

    latest_row = group_rows[0]
    if fallback_row:
        fallback_id = _returns_row_value(fallback_row, 'id')
        if fallback_id is not None and int(fallback_id) == int(group_rows[0]['id']):
            latest_row = fallback_row
    events = _returns_query_events_for_ids(cur, group_ids)
    state = _returns_calculate_state(events, latest_row)

    cur.execute(f'''
        UPDATE returns
        SET received_date = ?, restocked = ?, relisted = ?, relisted_date = ?,
            relisted_store = ?, relisted_item_id = ?, resold = ?, resold_date = ?,
            resold_order_id = ?, lifecycle_count = ?, location = COALESCE(?, location)
        WHERE id IN ({placeholders})
    ''', (
        state['received_date'], state['restocked'], state['relisted'], state['relisted_date'],
        state['relisted_store'], state['relisted_item_id'], state['resold'], state['resold_date'],
        state['resold_order_id'], state['lifecycle_count'], state['location'],
        *group_ids
    ))

    state['group_ids'] = group_ids
    state['event_count'] = len(events)
    return state


def _returns_recompute_state_from_events(cur, return_id):
    source_row, group_ids = _returns_group_ids_for_return(cur, return_id)
    if not source_row:
        return None
    return _returns_recompute_group_state(cur, group_ids, fallback_row=source_row)


def api_get_returns():
    """Get all returns with optional filters and lifecycle info."""
    conn = None
    try:
        store = request.args.get('store', '').strip()
        status = request.args.get('status', '').strip()
        
        conn = sqlite3.connect('sold.db')
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()

        query = '''
            SELECT *
            FROM returns
            WHERE 1=1
        '''
        params = []
        
        if store:
            query += ' AND store = ?'
            params.append(store)

        query += ' ORDER BY id DESC'
        
        cur.execute(query, params)
        raw_rows = cur.fetchall()

        grouped = {}
        all_return_ids = []
        for row in raw_rows:
            sig = _return_signature_from_row(row)
            if sig not in grouped:
                grouped[sig] = {'rows': [], 'latest_row': row}
            grouped[sig]['rows'].append(row)
            if int(row['id']) > int(grouped[sig]['latest_row']['id']):
                grouped[sig]['latest_row'] = row
            all_return_ids.append(int(row['id']))

        events_by_return_id = {}
        for event in _returns_query_events_for_ids(cur, all_return_ids):
            return_key = int(event['return_id'])
            if return_key not in events_by_return_id:
                events_by_return_id[return_key] = []
            events_by_return_id[return_key].append(event)

        def _to_int(value, default=0):
            try:
                return int(float(value))
            except (TypeError, ValueError):
                return default

        def _to_float(value, default=0.0):
            try:
                return float(value or 0)
            except (TypeError, ValueError):
                return default
        
        returns = []
        for group in grouped.values():
            latest_row = group['latest_row']
            group_ids = sorted({int(row['id']) for row in group['rows']})

            group_events = []
            for group_id in group_ids:
                group_events.extend(events_by_return_id.get(group_id, []))
            if len(group_events) > 1:
                group_events.sort(key=lambda e: (_normalize_return_text(e['event_date']), int(e['id'])))

            derived_state = _returns_calculate_state(group_events, latest_row)
            if status and derived_state['status'] != status:
                continue

            refund_amount = _to_float(latest_row['refund_amount'])
            original_shipping_cost = _to_float(latest_row['original_shipping_cost'])
            return_shipping_cost = _to_float(latest_row['return_shipping_cost'])
            original_seller_fee = _to_float(latest_row['original_seller_fee'])
            seller_fee_refund = _to_float(latest_row['seller_fee_refund'])

            returns.append({
                'id': int(latest_row['id']),
                'original_order_id': latest_row['original_order_id'],
                'order_id': latest_row['order_id'],
                'item_id': latest_row['item_id'],
                'barcode': latest_row['barcode'],
                'title': latest_row['title'],
                'quantity': _to_int(latest_row['quantity']),
                'original_price': _to_float(latest_row['original_price']),
                'refund_amount': refund_amount,
                'original_shipping_cost': original_shipping_cost,
                'return_shipping_cost': return_shipping_cost,
                'original_seller_fee': original_seller_fee,
                'seller_fee_refund': seller_fee_refund,
                'return_date': latest_row['return_date'],
                'received_date': derived_state['received_date'],
                'store': latest_row['store'],
                'return_reason': latest_row['return_reason'],
                'condition_received': latest_row['condition_received'],
                'restocked': derived_state['restocked'] == 1,
                'relisted': derived_state['relisted'] == 1,
                'resold': derived_state['resold'] == 1,
                'lifecycle_count': int(derived_state['lifecycle_count'] or 1),
                'event_count': len(group_events),
                'lot_number': latest_row['lot_number'],
                'location': derived_state['location'],
                'duplicate_count': max(0, len(group_ids) - 1)
            })

        returns.sort(key=lambda item: int(item['id']), reverse=True)
        
        # Calculate stats
        total_returns = len(returns)
        total_refunded = sum(r['refund_amount'] for r in returns)
        total_cost = sum(
            r['refund_amount'] + r['original_shipping_cost'] + r['return_shipping_cost']
            for r in returns
        )
        
        # Get total orders for return rate
        cur.execute('SELECT COUNT(*) FROM orders')
        total_orders = cur.fetchone()[0]
        return_rate = (total_returns / total_orders * 100) if total_orders > 0 else 0
        
        
        return jsonify({
            'success': True,
            'returns': returns,
            'stats': {
                'total_returns': total_returns,
                'total_refunded': total_refunded,
                'total_cost': total_cost,
                'return_rate': return_rate
            }
        })
        
    except Exception as e:
        return jsonify({'success': False, 'error': ss_errors._safe_error(e)}), 500
    finally:
        if conn:
            conn.close()


def api_create_return():
    """Create a manual return record."""
    conn = None
    try:
        data = request.get_json() or {}

        store = _normalize_return_text(data.get('store')).lower() or 'amazon'
        if store not in ('amazon', 'ebay'):
            store = 'amazon'

        order_id = _normalize_return_text(data.get('order_id'))
        title = _normalize_return_text(data.get('title'))
        if not order_id and not title:
            return jsonify({'success': False, 'error': 'Order ID or title is required'}), 400

        try:
            quantity = max(1, int(float(data.get('quantity', 1) or 1)))
        except (TypeError, ValueError):
            quantity = 1

        try:
            refund_amount = max(0.0, float(data.get('refund_amount', 0) or 0))
        except (TypeError, ValueError):
            refund_amount = 0.0

        try:
            original_price = float(data.get('original_price', refund_amount) or refund_amount)
        except (TypeError, ValueError):
            original_price = refund_amount

        return_date = _normalize_return_text(data.get('return_date'))
        if not return_date:
            import datetime
            return_date = datetime.datetime.now().isoformat()

        barcode = _normalize_return_text(data.get('barcode'))
        item_id = _normalize_return_text(data.get('item_id'))
        return_reason = _normalize_return_text(data.get('return_reason'))
        lot_number = _normalize_return_text(data.get('lot_number'))
        location = _normalize_return_text(data.get('location'))

        conn = sqlite3.connect('sold.db')
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()

        cur.execute('''
            INSERT INTO returns (
                original_order_id, order_id, item_id, barcode, title, quantity,
                original_price, refund_amount, original_shipping_cost, return_shipping_cost,
                original_seller_fee, seller_fee_refund,
                return_date, store, return_reason, lot_number, location
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, 0, 0, 0, ?, ?, ?, ?, ?)
        ''', (
            None, order_id, item_id, barcode, title, quantity,
            original_price, refund_amount,
            return_date, store, return_reason, lot_number, location
        ))

        new_id = int(cur.lastrowid)
        conn.commit()

        return jsonify({
            'success': True,
            'id': new_id,
            'message': 'Return added successfully'
        })

    except Exception as e:
        return jsonify({'success': False, 'error': ss_errors._safe_error(e)}), 500
    finally:
        if conn:
            conn.close()


def api_sync_amazon_returns():
    """Sync returns from Amazon API."""
    try:
        from amazon_manager import AmazonManager
        
        am = AmazonManager()
        synced_count = am.sync_returns_to_db(days_back=90)
        
        return jsonify({
            'success': True,
            'synced_count': synced_count,
            'message': f'Successfully synced {synced_count} returns from Amazon'
        })
        
    except Exception as e:
        return jsonify({'success': False, 'error': ss_errors._safe_error(e)}), 500


def api_sync_ebay_returns():
    """Sync returns from eBay API."""
    try:
        from ebay_manager import EbayManager
        
        em = EbayManager()
        synced_count = em.sync_returns_to_db(days_back=90)
        
        return jsonify({
            'success': True,
            'synced_count': synced_count,
            'message': f'Successfully synced {synced_count} returns from eBay'
        })
        
    except Exception as e:
        return jsonify({'success': False, 'error': ss_errors._safe_error(e)}), 500


def api_mark_return_received(return_id):
    """Mark a return as received."""
    conn = None
    try:
        conn = sqlite3.connect('sold.db')
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()

        source_row, group_ids = _returns_group_ids_for_return(cur, return_id)
        if not source_row:
            return jsonify({'success': False, 'error': 'Return not found'}), 404

        import datetime
        now = datetime.datetime.now().isoformat()
        active_return_id = max(group_ids)

        cur.execute('''
            INSERT INTO return_lifecycle_events
            (return_id, event_type, event_date, auto_detected)
            VALUES (?, 'received', ?, 0)
        ''', (active_return_id, now))

        state = _returns_recompute_group_state(cur, group_ids, fallback_row=source_row)
        conn.commit()
        
        return jsonify({
            'success': True,
            'message': 'Return marked as received',
            'event_count': int((state or {}).get('event_count', 0))
        })
        
    except Exception as e:
        return jsonify({'success': False, 'error': ss_errors._safe_error(e)}), 500
    finally:
        if conn:
            conn.close()


def api_restock_return(return_id):
    """Mark return as restocked and update inventory."""
    conn = None
    try:
        data = request.get_json() or {}
        shelf_location = _normalize_return_text(data.get('location'))
        
        conn = sqlite3.connect('sold.db')
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()

        source_row, group_ids = _returns_group_ids_for_return(cur, return_id)
        if not source_row:
            return jsonify({'success': False, 'error': 'Return not found'}), 404

        existing_location = _normalize_return_text(_returns_row_value(source_row, 'location'))
        final_location = shelf_location or existing_location

        import datetime
        now = datetime.datetime.now().isoformat()
        active_return_id = max(group_ids)

        notes = f'Restocked to shelf: {final_location}' if final_location else 'Restocked to inventory'
        cur.execute('''
            INSERT INTO return_lifecycle_events 
            (return_id, event_type, event_date, auto_detected, notes)
            VALUES (?, 'restocked', ?, 0, ?)
        ''', (active_return_id, now, notes))

        state = _returns_recompute_group_state(cur, group_ids, fallback_row=source_row)
        conn.commit()
        
        return jsonify({
            'success': True,
            'message': 'Return marked as restocked',
            'event_count': int((state or {}).get('event_count', 0))
        })
        
    except Exception as e:
        return jsonify({'success': False, 'error': ss_errors._safe_error(e)}), 500
    finally:
        if conn:
            conn.close()


def api_update_return(return_id):
    """Update return details (for editing)."""
    conn = None
    try:
        data = request.get_json() or {}
        conn = sqlite3.connect('sold.db')
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()

        source_row, group_ids = _returns_group_ids_for_return(cur, return_id)
        if not source_row:
            return jsonify({'success': False, 'error': 'Return not found'}), 404

        update_fields = []
        values = []
        
        if 'received' in data:
            if data['received']:
                update_fields.append('received_date = COALESCE(received_date, datetime("now"))')
            else:
                update_fields.append('received_date = NULL')
        
        if 'restocked' in data:
            update_fields.append('restocked = ?')
            values.append(1 if data['restocked'] else 0)
        
        if 'location' in data:
            update_fields.append('location = ?')
            values.append(_normalize_return_text(data['location']))
        
        if 'return_reason' in data:
            update_fields.append('return_reason = ?')
            values.append(_normalize_return_text(data['return_reason']))
        
        if not update_fields:
            return jsonify({'success': False, 'error': 'No fields to update'}), 400

        placeholders = ','.join('?' for _ in group_ids)
        query = f"UPDATE returns SET {', '.join(update_fields)} WHERE id IN ({placeholders})"
        cur.execute(query, [*values, *group_ids])
        conn.commit()
        
        return jsonify({'success': True})
        
    except Exception as e:
        return jsonify({'success': False, 'error': ss_errors._safe_error(e)}), 500
    finally:
        if conn:
            conn.close()


def api_delete_return(return_id):
    """Delete a return record."""
    conn = None
    try:
        conn = sqlite3.connect('sold.db')
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()

        source_row, group_ids = _returns_group_ids_for_return(cur, return_id)
        if not source_row:
            return jsonify({'success': False, 'error': 'Return not found'}), 404

        placeholders = ','.join('?' for _ in group_ids)
        cur.execute(f'DELETE FROM return_lifecycle_events WHERE return_id IN ({placeholders})', group_ids)
        cur.execute(f'DELETE FROM returns WHERE id IN ({placeholders})', group_ids)
        conn.commit()
        
        return jsonify({'success': True, 'deleted_count': len(group_ids)})
        
    except Exception as e:
        return jsonify({'success': False, 'error': ss_errors._safe_error(e)}), 500
    finally:
        if conn:
            conn.close()


def api_relist_return(return_id):
    """Mark return as relisted."""
    conn = None
    try:
        data = request.get_json() or {}
        
        conn = sqlite3.connect('sold.db')
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()

        source_row, group_ids = _returns_group_ids_for_return(cur, return_id)
        if not source_row:
            return jsonify({'success': False, 'error': 'Return not found'}), 404

        store = _normalize_return_text(data.get('store')).lower() or _normalize_return_text(source_row['store']).lower() or 'amazon'
        item_id = _normalize_return_text(data.get('item_id'))
        notes = _normalize_return_text(data.get('notes'))
        
        import datetime
        now = datetime.datetime.now().isoformat()

        active_return_id = max(group_ids)
        cur.execute('''
            INSERT INTO return_lifecycle_events 
            (return_id, event_type, event_date, auto_detected, store, item_id, notes)
            VALUES (?, 'relisted', ?, 0, ?, ?, ?)
        ''', (active_return_id, now, store, item_id, notes))

        state = _returns_recompute_group_state(cur, group_ids, fallback_row=source_row)
        conn.commit()
        
        return jsonify({'success': True, 'event_count': int((state or {}).get('event_count', 0))})
        
    except Exception as e:
        return jsonify({'success': False, 'error': ss_errors._safe_error(e)}), 500
    finally:
        if conn:
            conn.close()


def api_resold_return(return_id):
    """Mark return as resold."""
    conn = None
    try:
        data = request.get_json() or {}
        order_id = _normalize_return_text(data.get('order_id'))
        notes = _normalize_return_text(data.get('notes'))
        
        conn = sqlite3.connect('sold.db')
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()

        source_row, group_ids = _returns_group_ids_for_return(cur, return_id)
        if not source_row:
            return jsonify({'success': False, 'error': 'Return not found'}), 404
        
        import datetime
        now = datetime.datetime.now().isoformat()

        active_return_id = max(group_ids)
        cur.execute('''
            INSERT INTO return_lifecycle_events 
            (return_id, event_type, event_date, auto_detected, order_id, notes)
            VALUES (?, 'resold', ?, 0, ?, ?)
        ''', (active_return_id, now, order_id, notes))

        state = _returns_recompute_group_state(cur, group_ids, fallback_row=source_row)
        conn.commit()
        
        return jsonify({'success': True, 'event_count': int((state or {}).get('event_count', 0))})
        
    except Exception as e:
        return jsonify({'success': False, 'error': ss_errors._safe_error(e)}), 500
    finally:
        if conn:
            conn.close()


def api_restart_return_lifecycle(return_id):
    """Restart lifecycle - mark as received again after being resold."""
    conn = None
    try:
        data = request.get_json() or {}
        notes = _normalize_return_text(data.get('notes'))
        
        conn = sqlite3.connect('sold.db')
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()

        source_row, group_ids = _returns_group_ids_for_return(cur, return_id)
        if not source_row:
            return jsonify({'success': False, 'error': 'Return not found'}), 404
        
        import datetime
        now = datetime.datetime.now().isoformat()

        active_return_id = max(group_ids)
        cur.execute('''
            INSERT INTO return_lifecycle_events 
            (return_id, event_type, event_date, auto_detected, notes)
            VALUES (?, 'returned_again', ?, 0, ?)
        ''', (active_return_id, now, notes))

        state = _returns_recompute_group_state(cur, group_ids, fallback_row=source_row)
        conn.commit()
        
        return jsonify({'success': True, 'event_count': int((state or {}).get('event_count', 0))})
        
    except Exception as e:
        return jsonify({'success': False, 'error': ss_errors._safe_error(e)}), 500
    finally:
        if conn:
            conn.close()


def api_get_return_history(return_id):
    """Get lifecycle history for a return."""
    conn = None
    try:
        conn = sqlite3.connect('sold.db')
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()

        source_row, group_ids = _returns_group_ids_for_return(cur, return_id)
        if not source_row:
            return jsonify({'success': False, 'error': 'Return not found'}), 404

        active_return_id = max(group_ids)
        cur.execute('SELECT * FROM returns WHERE id = ?', (active_return_id,))
        return_data = cur.fetchone()

        events = [dict(row) for row in _returns_query_events_for_ids(cur, group_ids)]
        derived_state = _returns_calculate_state(events, return_data)

        return_payload = dict(return_data)
        return_payload.update({
            'received_date': derived_state['received_date'],
            'restocked': derived_state['restocked'],
            'relisted': derived_state['relisted'],
            'relisted_date': derived_state['relisted_date'],
            'relisted_store': derived_state['relisted_store'],
            'relisted_item_id': derived_state['relisted_item_id'],
            'resold': derived_state['resold'],
            'resold_date': derived_state['resold_date'],
            'resold_order_id': derived_state['resold_order_id'],
            'lifecycle_count': derived_state['lifecycle_count'],
            'location': derived_state['location'],
            'duplicate_count': max(0, len(group_ids) - 1)
        })

        return jsonify({
            'success': True,
            'return': return_payload,
            'events': events,
            'group_ids': group_ids
        })
        
    except Exception as e:
        return jsonify({'success': False, 'error': ss_errors._safe_error(e)}), 500
    finally:
        if conn:
            conn.close()


def api_undo_last_return_event(return_id):
    """Undo the most recently recorded lifecycle event for a return group."""
    conn = None
    try:
        conn = sqlite3.connect('sold.db')
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()

        source_row, group_ids = _returns_group_ids_for_return(cur, return_id)
        if not source_row:
            return jsonify({'success': False, 'error': 'Return not found'}), 404

        placeholders = ','.join('?' for _ in group_ids)
        cur.execute(f'''
            SELECT id, event_type, return_id
            FROM return_lifecycle_events
            WHERE return_id IN ({placeholders})
            ORDER BY id DESC
            LIMIT 1
        ''', group_ids)
        latest_event = cur.fetchone()

        if not latest_event:
            return jsonify({'success': False, 'error': 'No lifecycle events to undo'}), 400

        cur.execute('DELETE FROM return_lifecycle_events WHERE id = ?', (latest_event['id'],))
        state = _returns_recompute_group_state(cur, group_ids, fallback_row=source_row)
        conn.commit()

        return jsonify({
            'success': True,
            'undone_event': latest_event['event_type'],
            'event_count': int((state or {}).get('event_count', 0))
        })

    except Exception as e:
        return jsonify({'success': False, 'error': ss_errors._safe_error(e)}), 500
    finally:
        if conn:
            conn.close()
