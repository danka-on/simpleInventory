"""Marketplace sales for Sweet Shelves."""

import datetime
import json
import sqlite3
import uuid
from flask import jsonify, redirect, render_template, request
from . import (
    bulk_manifest as ss_bulk_manifest, config as ss_config, database as ss_database, errors as ss_errors,
    inventory_history as ss_inventory_history, marketplace_removal as ss_marketplace_removal,
    normalization as ss_normalization, shipping_identity as ss_shipping_identity, warehouse_allocations
    as ss_warehouse_allocations,
)


def marketplace_sale():
    """Marketplace sale entry page (deprecated)."""
    return redirect('/marketplace-session')


def marketplace_session():
    """Marketplace bulk sale session page."""
    return render_template('marketplace_session.html')


def marketplace_stats():
    """Marketplace sales statistics page."""
    return render_template('marketplace_stats.html')


def marketplace_schedule_pickup():
    """Marketplace pickup scheduling page."""
    return render_template('schedule_pickup.html')


def marketplace_live_pickups():
    """Live scheduled marketplace pickups board."""
    return render_template('live_pickups.html')


def marketplace_generate_barcode():
    """Generate auto-incremented 888 prefix barcode for marketplace manual entries."""
    try:
        conn = sqlite3.connect('bol.db')
        cur = conn.cursor()

        cur.execute('''
            CREATE TABLE IF NOT EXISTS temp_items (
                upc TEXT PRIMARY KEY,
                item_description TEXT,
                image_url TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')

        # Get the highest 888000xxxxxx barcode from bol_items, temp_items, and marketplace_sales
        # Use '888000%' prefix to avoid colliding with real UPCs that start with 888
        cur.execute("SELECT MAX(CAST(upc AS INTEGER)) FROM bol_items WHERE upc LIKE '888000%' AND LENGTH(upc) = 12")
        result = cur.fetchone()
        cur.execute("SELECT MAX(CAST(upc AS INTEGER)) FROM temp_items WHERE upc LIKE '888000%' AND LENGTH(upc) = 12")
        temp_result = cur.fetchone()

        max_barcode = result[0] if result[0] else None
        temp_max = temp_result[0] if temp_result[0] else None
        if temp_max and (not max_barcode or temp_max > max_barcode):
            max_barcode = temp_max

        # Also check marketplace.db for existing 888000 barcodes
        try:
            mkt_conn = sqlite3.connect('marketplace.db')
            mkt_cur = mkt_conn.cursor()
            mkt_cur.execute("SELECT MAX(CAST(barcode AS INTEGER)) FROM marketplace_sales WHERE barcode LIKE '888000%' AND LENGTH(barcode) = 12")
            mkt_result = mkt_cur.fetchone()
            mkt_max = mkt_result[0] if mkt_result[0] else None
            mkt_conn.close()
            if mkt_max and (not max_barcode or mkt_max > max_barcode):
                max_barcode = mkt_max
        except Exception:
            pass

        attempts = 0
        while attempts < 100:
            if max_barcode:
                new_barcode = str(int(max_barcode) + 1)
                if not new_barcode.startswith('888'):
                    numeric_part = int(str(max_barcode)[3:]) + 1
                    new_barcode = f"888{str(numeric_part).zfill(9)}"
            else:
                new_barcode = '888000000001'

            if len(new_barcode) < 12 and new_barcode.startswith('888'):
                new_barcode = f"888{new_barcode[3:].zfill(9)}"

            cur.execute('SELECT upc FROM bol_items WHERE upc = ? COLLATE NOCASE', (new_barcode,))
            exists_bol = cur.fetchone()
            cur.execute('SELECT upc FROM temp_items WHERE upc = ? COLLATE NOCASE', (new_barcode,))
            exists_temp = cur.fetchone()

            if not exists_bol and not exists_temp:
                conn.close()
                return jsonify({'success': True, 'barcode': new_barcode.strip()})

            max_barcode = int(new_barcode)
            attempts += 1

        conn.close()
        return jsonify({'success': False, 'error': 'Could not generate unique barcode'}), 500
    except Exception as e:
        print(f'Error generating marketplace barcode: {e}')
        return jsonify({'success': False, 'error': ss_errors._safe_error(e)}), 500


def api_marketplace_lookup():
    """Lookup item details from rawbol.db by barcode."""
    try:
        barcode = request.args.get('barcode', '').strip()
        if not barcode:
            return jsonify({'success': False, 'error': 'Missing barcode'}), 400

        row = ss_marketplace_removal._marketplace_lookup_rawbol_item(barcode)
        if row:
            return jsonify({
                'success': True,
                'found': True,
                'title': row.get('title') or '',
                'image_url': row.get('image_url') or ''
            })
        return jsonify({'success': True, 'found': False, 'title': '', 'image_url': ''})
    except Exception as e:
        return jsonify({'success': False, 'error': ss_errors._safe_error(e)}), 500


def api_marketplace_search():
    """Search items by name for marketplace session."""
    try:
        q = (request.args.get('q') or '').strip()
        if not q:
            return jsonify({'success': False, 'error': 'Missing q'}), 400
        tokens = []
        seen_tokens = set()
        for part in q.split():
            token = str(part or '').strip()
            if not token:
                continue
            token_key = token.lower()
            if token_key in seen_tokens:
                continue
            seen_tokens.add(token_key)
            tokens.append(token)
        if not tokens:
            return jsonify({'success': False, 'error': 'Missing q'}), 400
        conn = sqlite3.connect('rawbol.db')
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        where_parts = ['item_description LIKE ? COLLATE NOCASE' for _ in tokens]
        where_sql = ' AND '.join(where_parts)
        params = tuple(f'%{token}%' for token in tokens)
        cur.execute(f'''
            SELECT upc, item_description, image_url
            FROM raw_bol_items
            WHERE {where_sql}
            ORDER BY created_at DESC
            LIMIT 30
        ''', params)
        results = [dict(r) for r in cur.fetchall()]
        conn.close()
        return jsonify({'success': True, 'results': results})
    except Exception as e:
        return jsonify({'success': False, 'error': ss_errors._safe_error(e)}), 500


def api_marketplace_pickups_search():
    """Search rawbol.db for pickup scheduling by title or barcode."""
    try:
        q = (request.args.get('q') or '').strip()
        if not q:
            return jsonify({'success': False, 'error': 'Missing q'}), 400

        q_upc = ss_normalization._normalize_upc(q)
        like_q = f'%{q}%'
        like_upc = f'%{q_upc}%'

        with ss_database.db_connection('rawbol.db') as conn:
            cur = conn.cursor()
            cur.execute('''
                SELECT upc, item_description, image_url, lot_number, import_date, created_at
                FROM raw_bol_items
                WHERE (
                    item_description LIKE ? COLLATE NOCASE
                    OR upc = ? COLLATE NOCASE
                    OR REPLACE(COALESCE(upc, ''), '.0', '') = ? COLLATE NOCASE
                    OR upc LIKE ? COLLATE NOCASE
                )
                ORDER BY created_at DESC, import_date DESC, id DESC
                LIMIT 80
            ''', (like_q, q_upc, q_upc, like_upc))
            rows = cur.fetchall()

        seen = set()
        results = []
        for row in rows:
            barcode = ss_normalization._normalize_upc(row['upc'])
            title = str(row['item_description'] or '').strip()
            key = (barcode or title).lower()
            if not key or key in seen:
                continue
            seen.add(key)
            results.append({
                'barcode': barcode,
                'title': title,
                'image_url': str(row['image_url'] or '').strip(),
                'lot_number': str(row['lot_number'] or '').strip(),
                'import_date': str(row['import_date'] or '').strip(),
            })
            if len(results) >= 25:
                break

        return jsonify({'success': True, 'results': results})
    except Exception as e:
        return jsonify({'success': False, 'error': ss_errors._safe_error(e, 'marketplace-pickup-search')}), 500


def api_marketplace_pickups():
    """List scheduled marketplace pickups."""
    try:
        from marketplace_manager import get_marketplace_pickups

        status = (request.args.get('status') or 'scheduled').strip() or None
        limit = request.args.get('limit', type=int)
        result = get_marketplace_pickups(status=status, limit=limit)
        return jsonify(result)
    except Exception as e:
        return jsonify({'success': False, 'error': ss_errors._safe_error(e, 'marketplace-pickups:list')}), 500


def api_marketplace_pickups_create():
    """Create a scheduled marketplace pickup."""
    try:
        from marketplace_manager import add_marketplace_pickup

        data = request.get_json() or {}
        title = str(data.get('title') or '').strip()
        barcode = ss_normalization._normalize_upc(data.get('barcode'))
        pickup_date = str(data.get('pickup_date') or '').strip() or datetime.datetime.now().strftime('%Y-%m-%d')
        pickup_time_raw = str(data.get('pickup_time') or '').strip()
        pickup_time = pickup_time_raw[:5] if len(pickup_time_raw) >= 5 else pickup_time_raw
        image_url = str(data.get('image_url') or '').strip()
        source = str(data.get('source') or 'manual').strip() or 'manual'
        notes = str(data.get('notes') or '').strip()

        if not pickup_time:
            return jsonify({'success': False, 'error': 'Missing pickup time'}), 400

        rawbol_item = ss_marketplace_removal._marketplace_lookup_rawbol_item(barcode) if barcode else None
        if not title and rawbol_item:
            title = str(rawbol_item.get('title') or '').strip()
        if not image_url and rawbol_item:
            image_url = str(rawbol_item.get('image_url') or '').strip()
        if not title and barcode:
            title = barcode

        result = add_marketplace_pickup(
            title=title,
            pickup_date=pickup_date,
            pickup_time=pickup_time,
            barcode=barcode,
            image_url=image_url,
            source=source,
            notes=notes
        )

        status_code = 200 if result.get('success') else 400
        return jsonify(result), status_code
    except Exception as e:
        return jsonify({'success': False, 'error': ss_errors._safe_error(e, 'marketplace-pickups:create')}), 500


def api_marketplace_sale():
    """Create a new marketplace sale and add to sold.db."""
    try:
        from marketplace_manager import add_marketplace_sale
        
        data = request.get_json() or {}
        barcode = data.get('barcode', '').strip()
        title = data.get('title', '').strip()
        quantity = max(1, ss_normalization._coerce_int(data.get('quantity'), 1))
        price = ss_bulk_manifest._bulk_manifest_money(data.get('price'), default=0.0)
        session_id = (data.get('session_id') or '').strip() or None
        price_auto = data.get('price_auto', False)
        store_key = ss_bulk_manifest._marketplace_sale_store_key(data.get('store'))
        incoming_allocations = data.get('allocations')

        if not barcode:
            return jsonify({'success': False, 'error': 'Missing barcode'}), 400

        rawbol_item = ss_marketplace_removal._marketplace_lookup_rawbol_item(barcode)
        if not title and rawbol_item:
            title = str(rawbol_item.get('title') or '').strip()

        removal_plan = ss_marketplace_removal._build_marketplace_removal_plan(barcode, quantity, incoming_allocations)
        if not removal_plan.get('success', False):
            return jsonify({
                'success': False,
                'error': removal_plan.get('error') or 'Invalid location selection',
                'location_selection_required': False,
                'locations': removal_plan.get('locations') or [],
                'total_available': removal_plan.get('total_available', 0),
                'can_fulfill': removal_plan.get('can_fulfill', False)
            }), 400

        if removal_plan.get('needs_choice'):
            return jsonify({
                'success': False,
                'error': 'Location selection required for this item',
                'location_selection_required': True,
                'barcode': barcode,
                'title': title,
                'quantity': quantity,
                'locations': removal_plan.get('locations') or [],
                'total_available': removal_plan.get('total_available', 0),
                'can_fulfill': removal_plan.get('can_fulfill', False)
            }), 409

        # Add to marketplace.db
        result = add_marketplace_sale(barcode, title, quantity, price, session_id=session_id, price_auto=price_auto)
        if not result['success']:
            return jsonify(result), 500
        
        # Add to sold.db (orders table). Marketplace rows are treated as already handled.
        sold_conn = None
        sale_order_ref = f'{store_key}-{result["id"]}'
        try:
            sold_conn = sqlite3.connect(str(ss_config.BASE_DIR / 'sold.db'), timeout=30)
            sold_cur = sold_conn.cursor()
            
            sale_date = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            sale_order_ref = f'{store_key}-{result["id"]}'
            
            # Insert ONE row with the quantity value
            # Marketplace orders are immediately handled (isHandled='1') and don't need inventory removal
            sold_cur.execute('''INSERT INTO orders 
                (order_id, barcode, title, price, paid_time, store, quantity, isHandled, isHandledDate, rackupdated)
                VALUES (?, ?, ?, ?, ?, ?, ?, '1', ?, 1)''',
                (sale_order_ref, barcode, title, price, sale_date, store_key, quantity, sale_date))
            sold_order_id = sold_cur.lastrowid
            ss_shipping_identity._ensure_order_removal_allocations_table(sold_cur)
            ss_warehouse_allocations._save_order_removal_allocations(sold_cur, sold_order_id, removal_plan.get('normalized_allocations') or [])
            
            # Auto-detect if this is a resold return
            if barcode:
                sold_cur.execute('''
                    SELECT id, relisted_store, relisted_item_id 
                    FROM returns 
                    WHERE barcode = ? 
                    AND relisted = 1 
                    AND resold = 0
                    ORDER BY relisted_date DESC
                    LIMIT 1
                ''', (barcode,))
                return_row = sold_cur.fetchone()
                
                if return_row:
                    return_id = return_row[0]
                    
                    # Mark as resold
                    sold_cur.execute('''
                        UPDATE returns 
                        SET resold = 1, resold_date = ?, resold_order_id = ?,
                            lifecycle_count = lifecycle_count + 1
                        WHERE id = ?
                    ''', (sale_date, sale_order_ref, return_id))
                    
                    # Add auto-detected lifecycle event
                    sold_cur.execute('''
                        INSERT INTO return_lifecycle_events 
                        (return_id, event_type, event_date, auto_detected, order_id, store, notes)
                        VALUES (?, 'resold', ?, 1, ?, ?, ?)
                    ''', (return_id, sale_date, sale_order_ref, store_key, 'Auto-detected from marketplace sale'))
                    
                    print(f'[AUTO-DETECT] Return #{return_id} marked as resold (Marketplace sale #{result["id"]})')
            
            sold_conn.commit()
        except Exception as e:
            if sold_conn is not None:
                sold_conn.rollback()
            with ss_database.db_connection('marketplace.db') as marketplace_conn:
                marketplace_conn.execute('DELETE FROM marketplace_sales WHERE id = ?', (result['id'],))
            return jsonify({'success': False, 'error': ss_errors._safe_error(e, 'marketplace:sold_order')}), 500
        finally:
            if sold_conn is not None:
                sold_conn.close()

        removal_result = ss_marketplace_removal._apply_marketplace_removal_plan(
            removal_plan,
            order_ref=sale_order_ref,
            barcode=barcode,
            title=title or (rawbol_item or {}).get('title') or ''
        )

        response = {
            'success': True,
            'sale_id': result['id'],
            'store': store_key,
            'inventory_removed': bool(removal_result.get('removed')),
            'removed_units': max(0, ss_normalization._coerce_int(removal_result.get('removed_units'), 0)),
            'removed_locations': removal_result.get('locations') or [],
            'needs_location_choice': False
        }
        removal_error = str(removal_result.get('error') or '').strip()
        if removal_error:
            response['inventory_removal_error'] = removal_error
        elif removal_plan.get('locations') and not removal_plan.get('can_fulfill', True):
            response['inventory_removal_error'] = (
                f"Not enough inventory available to remove {quantity} unit(s); "
                f"only {removal_plan.get('total_available', 0)} available."
            )
        return jsonify(response)
        
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': ss_errors._safe_error(e)}), 500


def api_marketplace_decrement():
    """Decrement the legacy rack table and remove its row when it is depleted."""
    rack_conn = None
    history_conn = None
    event_id = None
    try:
        data = request.get_json() or {}
        barcode = data.get('barcode', '').strip()
        area_code = data.get('area_code', '').strip()
        quantity = ss_normalization._strict_inventory_quantity(data.get('quantity', 1))
        if quantity is None:
            return jsonify({'success': False, 'error': 'Invalid quantity'}), 400
        
        if not barcode or not area_code:
            return jsonify({'success': False, 'error': 'Missing barcode or area_code'}), 400
        if quantity <= 0:
            return jsonify({'success': False, 'error': 'Quantity must be positive'}), 400
        
        rack_conn = sqlite3.connect('searchRack.db')
        rack_conn.row_factory = sqlite3.Row
        rack_cur = rack_conn.cursor()
        
        rack_cur.execute('SELECT rowid AS _rowid_, * FROM rack WHERE barcode = ? COLLATE NOCASE AND area_code = ?',
                        (barcode, area_code))
        row = rack_cur.fetchone()
        
        if not row:
            return jsonify({'success': False, 'error': 'Item not found in this location'}), 404
        
        snapshot = dict(row)
        current_qty = max(0, ss_normalization._coerce_int(snapshot.get('quantity'), 0))
        if current_qty <= 0:
            return jsonify({'success': False, 'error': 'Item has no active inventory'}), 409
        if quantity > current_qty:
            return jsonify({
                'success': False,
                'error': f'Only {current_qty} unit(s) are available in this location'
            }), 409
        new_qty = current_qty - quantity

        event_id = uuid.uuid4().hex
        now_iso = datetime.datetime.now().isoformat()
        history_conn = sqlite3.connect('rackhistory.db')
        history_cur = history_conn.cursor()
        ss_inventory_history._ensure_removed_items_table(history_cur)
        history_cur.execute('''
            INSERT INTO removed_items (
                order_id, barcode, title, quantity_removed, removed_at,
                searchrack_id, old_quantity, new_quantity, removal_type,
                item_position, event_id, source_row_json, from_position,
                to_position, inventory_row_deleted, event_status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '', ?, 'pending')
        ''', (
            None,
            barcode,
            str(snapshot.get('title') or snapshot.get('name') or '').strip(),
            quantity,
            now_iso,
            snapshot.get('_rowid_'),
            current_qty,
            new_qty,
            'legacy_marketplace_removal',
            area_code,
            event_id,
            json.dumps(snapshot, ensure_ascii=False, default=str),
            area_code,
            1 if new_qty <= 0 else 0,
        ))
        history_conn.commit()
        
        if new_qty <= 0:
            rack_cur.execute(
                'DELETE FROM rack WHERE rowid = ? AND quantity = ?',
                (snapshot.get('_rowid_'), current_qty)
            )
        else:
            rack_cur.execute(
                'UPDATE rack SET quantity = ? WHERE rowid = ? AND quantity = ?',
                (new_qty, snapshot.get('_rowid_'), current_qty)
            )
        if rack_cur.rowcount != 1:
            raise RuntimeError('Inventory changed before the decrement could be applied')
        rack_conn.commit()

        history_warning = ''
        try:
            history_cur.execute('''
                UPDATE removed_items
                SET event_status = 'applied', applied_at = ?
                WHERE event_id = ?
            ''', (datetime.datetime.now().isoformat(), event_id))
            history_conn.commit()
        except Exception as history_error:
            history_warning = 'Inventory was updated; Rack History finalization will be retried.'
            ss_config.logger.warning('Legacy marketplace history finalization deferred: %s', history_error)
        
        return jsonify({
            'success': True,
            'new_quantity': new_qty,
            'row_deleted': new_qty <= 0,
            'warning': history_warning,
        })
    except Exception as e:
        for connection in (history_conn, rack_conn):
            try:
                if connection is not None:
                    connection.rollback()
            except Exception:
                pass
        return jsonify({'success': False, 'error': ss_errors._safe_error(e)}), 500
    finally:
        if rack_conn is not None:
            rack_conn.close()
        if history_conn is not None:
            history_conn.close()


def api_marketplace_sales():
    """Get all marketplace sales."""
    try:
        from marketplace_manager import get_marketplace_sales

        limit = request.args.get('limit', type=int)
        offset = request.args.get('offset', type=int)

        result = get_marketplace_sales(limit=limit, offset=offset)

        # Enrich sales with thumbnail images from rawbol.db
        if result.get('success') and result.get('sales'):
            barcodes = list({str(s.get('barcode') or '').strip() for s in result['sales'] if s.get('barcode')})
            image_map = {}
            if barcodes:
                try:
                    _rawbol = sqlite3.connect('rawbol.db')
                    _rawbol.row_factory = sqlite3.Row
                    _rc = _rawbol.cursor()
                    # Build all variants (with and without leading zero)
                    variants = list({b for b in barcodes} |
                                    {b.lstrip('0') for b in barcodes if b.startswith('0')})
                    if variants:
                        ph = ','.join('?' * len(variants))
                        _rc.execute(
                            f"SELECT upc, image_url FROM raw_bol_items "
                            f"WHERE LOWER(TRIM(upc)) IN ({ph}) "
                            f"AND image_url IS NOT NULL AND TRIM(image_url) != '' "
                            f"ORDER BY rowid DESC",
                            [v.lower() for v in variants]
                        )
                        for row in _rc.fetchall():
                            key = str(row['upc'] or '').strip().lower()
                            if key not in image_map:
                                image_map[key] = row['image_url']
                    _rawbol.close()
                except Exception:
                    pass
            for sale in result['sales']:
                bc = str(sale.get('barcode') or '').strip()
                img = image_map.get(bc.lower()) or image_map.get(bc.lstrip('0').lower() if bc.startswith('0') else '')
                sale['image'] = img or ''

        return jsonify(result)
    except Exception as e:
        return jsonify({'success': False, 'error': ss_errors._safe_error(e)}), 500


def api_marketplace_sale_delete(sale_id):
    """Delete a marketplace sale."""
    try:
        from marketplace_manager import delete_marketplace_sale
        
        result = delete_marketplace_sale(sale_id)
        return jsonify(result)
    except Exception as e:
        return jsonify({'success': False, 'error': ss_errors._safe_error(e)}), 500


def api_marketplace_sale_update(sale_id):
    """Update a marketplace sale."""
    try:
        from marketplace_manager import update_marketplace_sale
        
        data = request.get_json() or {}
        result = update_marketplace_sale(
            sale_id,
            barcode=data.get('barcode'),
            title=data.get('title'),
            quantity=data.get('quantity'),
            price=data.get('price')
        )
        return jsonify(result)
    except Exception as e:
        return jsonify({'success': False, 'error': ss_errors._safe_error(e)}), 500
