"""Analytics for Sweet Shelves."""

import datetime
import json
import sqlite3
import time
from flask import jsonify, request
from . import (
    caching as ss_caching, config as ss_config, database as ss_database, errors as ss_errors,
    integrations as ss_integrations, inventory_age_rescan as ss_inventory_age_rescan, inventory_history
    as ss_inventory_history, normalization as ss_normalization, runtime as ss_runtime,
)


@ss_runtime.cache.cached(timeout=120, query_string=True)
def api_inventory_cleanup():
    try:
        from seller_analytics import build_inventory_cleanup

        def bounded_int(name, default, minimum, maximum):
            try:
                value = int(request.args.get(name, default))
            except (TypeError, ValueError):
                value = default
            return max(minimum, min(maximum, value))

        clearance_age = bounded_int('clearance_age', 180, 90, 365)
        disposal_age = bounded_int('disposal_age', 240, clearance_age + 30, 730)
        markdown_lead = bounded_int(
            'markdown_lead', 60, 15, max(15, clearance_age - 30)
        )
        return jsonify(build_inventory_cleanup(
            ss_config.BASE_DIR,
            clearance_age=clearance_age,
            disposal_age=disposal_age,
            markdown_lead=markdown_lead,
        ))
    except Exception as exc:
        ss_config.logger.exception('Inventory cleanup analytics failed')
        return jsonify({
            'success': False,
            'error': ss_errors._safe_error(exc, 'inventory_cleanup')
        }), 500


def api_inventory_age_summary():
    try:
        from seller_analytics import build_inventory_age_summary
        payload = build_inventory_age_summary(ss_config.BASE_DIR)
        try:
            with sqlite3.connect(
                str(ss_config.BASE_DIR / 'searchRack.db'),
                timeout=30.0,
            ) as age_conn:
                age_cur = age_conn.cursor()
                age_cur.execute('''
                    SELECT value
                    FROM inventory_age_meta
                    WHERE key = 'last_comprehensive_rebuild'
                    LIMIT 1
                ''')
                meta_row = age_cur.fetchone()
                if meta_row:
                    parsed = json.loads(meta_row[0] or '{}')
                    if isinstance(parsed, dict):
                        payload['last_comprehensive_rebuild'] = parsed
        except Exception:
            # Age bands remain available even on a legacy database that has
            # not run the comprehensive rebuild yet.
            pass
        return jsonify(payload)
    except Exception as exc:
        ss_config.logger.exception('Inventory age summary failed')
        return jsonify({
            'success': False,
            'error': ss_errors._safe_error(exc, 'inventory_age_summary')
        }), 500


def api_inventory_age_rescan():
    if not ss_inventory_age_rescan._inventory_age_rescan_lock.acquire(blocking=False):
        return jsonify({
            'success': False,
            'error': 'An inventory age rescan is already running.'
        }), 409

    conn = None
    try:
        mirrored_events = 0
        pending_history_events = None
        for _ in range(40):
            processed = ss_inventory_history._flush_searchrack_history_outbox(limit=250)
            mirrored_events += processed
            with sqlite3.connect(
                str(ss_config.BASE_DIR / 'searchRack.db'),
                timeout=30.0
            ) as pending_conn:
                pending_cur = pending_conn.cursor()
                ss_inventory_history._ensure_searchrack_history_outbox(pending_cur)
                pending_cur.execute('''
                    SELECT COUNT(*)
                    FROM searchrack_history_outbox
                    WHERE processed_at IS NULL
                ''')
                pending_history_events = max(
                    0, ss_normalization._coerce_int(pending_cur.fetchone()[0], 0)
                )
            if pending_history_events == 0:
                break
            if processed == 0:
                time.sleep(0.05)

        if pending_history_events:
            return jsonify({
                'success': False,
                'error': (
                    'Rack history is still catching up. '
                    'Please wait a moment and run the age re-scan again.'
                ),
                'pending_history_events': pending_history_events,
            }), 503

        conn = sqlite3.connect(str(ss_config.BASE_DIR / 'searchRack.db'), timeout=60.0)
        result = ss_inventory_age_rescan._rescan_inventory_age_from_history(conn)
        ss_caching._invalidate_searchrack_cache()
        return jsonify({
            'success': True,
            'mirrored_history_events': mirrored_events,
            **result,
        })
    except RuntimeError as exc:
        message = str(exc)
        if message.startswith('Comprehensive age evidence is unavailable:'):
            return jsonify({'success': False, 'error': message}), 503
        if message in {
            'Warehouse inventory changed during the age rescan; please retry',
            'Inventory ages changed during the rescan; please retry',
            'Warehouse inventory changed during the age rebuild; please retry',
            'Inventory ages changed during the age rebuild; please retry',
            'BOL or Item Manager data changed during the age rebuild; please retry',
            'Rack history changed during the age rebuild; please retry',
        }:
            return jsonify({'success': False, 'error': message}), 409
        ss_config.logger.exception('Comprehensive inventory age rebuild failed')
        return jsonify({
            'success': False,
            'error': ss_errors._safe_error(exc, 'inventory_age_rescan')
        }), 500
    except Exception as exc:
        ss_config.logger.exception('Comprehensive inventory age rebuild failed')
        return jsonify({
            'success': False,
            'error': ss_errors._safe_error(exc, 'inventory_age_rescan')
        }), 500
    finally:
        if conn is not None:
            conn.close()
        ss_inventory_age_rescan._inventory_age_rescan_lock.release()


@ss_runtime.cache.cached(timeout=300, query_string=True)
def api_inventory_seller_analytics():
    try:
        from seller_analytics import build_seller_analytics

        try:
            window_days = max(0, int(request.args.get('days', '0')))
        except (TypeError, ValueError):
            window_days = 0
        if window_days not in (0, 90, 180, 365):
            window_days = 0
        return jsonify(build_seller_analytics(ss_config.BASE_DIR, window_days))
    except Exception as exc:
        ss_config.logger.exception('Inventory seller analytics failed')
        return jsonify({
            'success': False,
            'error': ss_errors._safe_error(exc, 'inventory_seller_analytics')
        }), 500


@ss_runtime.cache.cached(timeout=300, query_string=True)
def api_financial_analytics():
    """
    Fetch all sold orders with cost data from BOL and calculate profits.
    Returns financial metrics for the dashboard.
    """
    print("DEBUG: Financial analytics API called")
    try:
        # Use connection pool for better performance
        sold_conn = ss_database.get_db_connection('sold.db')
        sold_cur = sold_conn.cursor()
        
        # Get all sold orders with lot_number
        sold_cur.execute('''
            SELECT 
                id,
                order_id,
                item_id,
                title,
                quantity,
                price,
                seller_fee,
                taxes,
                paid_time,
                shipped_time,
                barcode,
                store,
                location,
                shipping_cost,
                lot_number
            FROM orders
            WHERE paid_time IS NOT NULL
            ORDER BY paid_time DESC
        ''')
        orders = sold_cur.fetchall()
        
        # Use connection pool for other databases
        rawbol_conn = ss_database.get_db_connection('rawbol.db')
        rawbol_cur = rawbol_conn.cursor()
        
        ebay_conn = ss_database.get_db_connection('ebayStore.db')
        ebay_cur = ebay_conn.cursor()
        
        amazon_conn = ss_database.get_db_connection('amazonStore.db')
        amazon_cur = amazon_conn.cursor()
        
        # Get returns data from sold.db
        returns_data = {}
        seller_fee_refunds_by_order = {}
        unmatched_returns = []  # Store returns without original_order_id
        try:
            sold_cur.execute('''
                SELECT 
                    id,
                    original_order_id,
                    order_id,
                    item_id,
                    barcode,
                    title,
                    refund_amount,
                    original_shipping_cost,
                    return_shipping_cost,
                    seller_fee_refund,
                    return_date,
                    store,
                    return_reason
                FROM returns
                ORDER BY id DESC
            ''')
            
            returns_rows = sold_cur.fetchall()
            print(f"DEBUG: Found {len(returns_rows)} returns in database")

            def _to_float(value):
                try:
                    if value is None:
                        return 0.0
                    return float(value)
                except Exception:
                    return 0.0

            # Deduplicate returns that are re-synced with identical financial payload.
            # Keep the latest row (highest id) because rows are ordered by id DESC.
            deduped_returns = []
            seen_signatures = set()
            for row in returns_rows:
                signature = (
                    row['original_order_id'],
                    row['order_id'] or '',
                    row['item_id'] or '',
                    row['barcode'] or '',
                    row['title'] or '',
                    row['return_date'] or '',
                    row['store'] or ''
                )
                if signature in seen_signatures:
                    continue
                seen_signatures.add(signature)
                deduped_returns.append(row)

            matched_returns_count = 0
            for row in deduped_returns:
                original_order_id = row['original_order_id']
                refund_amount = _to_float(row['refund_amount'])
                original_shipping_cost = _to_float(row['original_shipping_cost'])
                return_shipping_cost = _to_float(row['return_shipping_cost'])
                seller_fee_refund = _to_float(row['seller_fee_refund'])

                # For matched returns, original shipping is already captured in orders.shipping_cost.
                # Excluding it here prevents double counting.
                matched_return_cost = refund_amount + return_shipping_cost

                # For unmatched returns, include original shipping because there is no matched order row.
                unmatched_return_cost = refund_amount + original_shipping_cost + return_shipping_cost

                if original_order_id is not None and str(original_order_id).strip() != '':
                    order_key = str(original_order_id).strip()
                    matched_returns_count += 1
                    returns_data[order_key] = returns_data.get(order_key, 0.0) + matched_return_cost
                    seller_fee_refunds_by_order[order_key] = seller_fee_refunds_by_order.get(order_key, 0.0) + seller_fee_refund
                else:
                    # Unmatched return - add as standalone transaction
                    unmatched_returns.append({
                        'order_id': row['order_id'],
                        'title': row['title'] or 'Unknown returned item',
                        'refund_amount': refund_amount,
                        'return_cost': unmatched_return_cost,
                        'seller_fee_refund': seller_fee_refund,
                        'return_date': row['return_date'],
                        'store': row['store'],
                        'return_reason': row['return_reason'],
                        'is_unmatched_return': True
                    })

            total_return_costs = sum(returns_data.values()) + sum(r['return_cost'] for r in unmatched_returns)
            deduped_count = len(returns_rows) - len(deduped_returns)
            print(
                f"DEBUG: Processed {matched_returns_count} matched returns across {len(returns_data)} orders, "
                f"{len(unmatched_returns)} unmatched returns, deduped={deduped_count}, "
                f"total return costs: ${total_return_costs:.2f}"
            )
        except Exception as e:
            print(f"Warning: Could not load returns data: {e}")
            import traceback
            traceback.print_exc()
        
        transactions = []
        
        for order in orders:
            item_data = dict(order)
            
            # Add return cost if this order has a return
            # Use .get() to safely access the id key
            order_pk_id = item_data.get('id', 0)
            order_pk_key = str(order_pk_id).strip() if order_pk_id is not None else ''
            item_data['return_cost'] = returns_data.get(order_pk_key, 0.0)
            item_data['seller_fee_refund'] = seller_fee_refunds_by_order.get(order_pk_key, 0.0)
            
            # Skip cost lookup for marketplace sales (no cost associated)
            if order['store'] == 'marketplace':
                item_data['cost'] = None
                item_data['cost_source'] = 'marketplace'
                item_data['upc'] = order['barcode'] or order['item_id']
                item_data['bol_number'] = None
                transactions.append(item_data)
                continue
            
            # Try to get cost from rawbol.db
            upc = order['barcode'] or order['item_id']
            cost = None
            cost_source = None
            # LOT number comes directly from sold.db (already enriched from rawbol.db)
            bol_number = order['lot_number'] if order['lot_number'] and str(order['lot_number']).lower() not in ['', 'nan', 'none', 'null'] else None
            
            if upc:
                # Check rawbol for avg_cost.
                # Prefer the sold order's assigned LOT when available, then fall back to UPC-only lookup.
                rawbol_row = None
                if bol_number:
                    rawbol_cur.execute('''
                        SELECT avg_cost, lot_number
                        FROM raw_bol_items
                        WHERE upc = ? COLLATE NOCASE
                          AND lot_number = ? COLLATE NOCASE
                        ORDER BY created_at DESC
                        LIMIT 1
                    ''', (upc, bol_number))
                    rawbol_row = rawbol_cur.fetchone()
                if not rawbol_row:
                    rawbol_cur.execute('''
                        SELECT avg_cost, lot_number
                        FROM raw_bol_items
                        WHERE upc = ? COLLATE NOCASE
                        ORDER BY created_at DESC
                        LIMIT 1
                    ''', (upc,))
                    rawbol_row = rawbol_cur.fetchone()
                if rawbol_row and rawbol_row['avg_cost']:
                    cost = float(rawbol_row['avg_cost'])
                    cost_source = 'rawbol'
                    # Update bol_number if we found it in rawbol and it wasn't set
                    if not bol_number and rawbol_row['lot_number']:
                        bol_number = rawbol_row['lot_number']
                
                # For Amazon items, if barcode looks like ASIN, try to get UPC from amazonStore
                # and re-lookup in rawbol (handles old items that had ASIN as barcode)
                if not cost and order['store'] == 'amazon' and upc and (upc.startswith('B0') or len(upc) == 10):
                    amazon_cur.execute('SELECT UPC FROM ITEMS WHERE ASIN = ? COLLATE NOCASE', (upc,))
                    amazon_row = amazon_cur.fetchone()
                    if amazon_row and amazon_row['UPC']:
                        actual_upc = amazon_row['UPC']
                        # Try rawbol again with the actual UPC, preferring assigned LOT.
                        rawbol_row = None
                        if bol_number:
                            rawbol_cur.execute('''
                                SELECT avg_cost, lot_number
                                FROM raw_bol_items
                                WHERE upc = ? COLLATE NOCASE
                                  AND lot_number = ? COLLATE NOCASE
                                ORDER BY created_at DESC
                                LIMIT 1
                            ''', (actual_upc, bol_number))
                            rawbol_row = rawbol_cur.fetchone()
                        if not rawbol_row:
                            rawbol_cur.execute('''
                                SELECT avg_cost, lot_number
                                FROM raw_bol_items
                                WHERE upc = ? COLLATE NOCASE
                                ORDER BY created_at DESC
                                LIMIT 1
                            ''', (actual_upc,))
                            rawbol_row = rawbol_cur.fetchone()
                        if rawbol_row and rawbol_row['avg_cost']:
                            cost = float(rawbol_row['avg_cost'])
                            cost_source = 'rawbol_via_asin'
                            if not bol_number and rawbol_row['lot_number']:
                                bol_number = rawbol_row['lot_number']
                
                # Fallback to eBay store data (could have cost in some cases)
                if not cost:
                    ebay_cur.execute('SELECT UPC FROM INVENTORY WHERE UPC = ? OR ItemID = ? COLLATE NOCASE LIMIT 1', (upc, upc))
                    if ebay_cur.fetchone():
                        cost_source = 'ebay'
                        # Note: ebayStore.db doesn't have cost data, just marking source
                
                # Fallback to Amazon store data
                if not cost:
                    amazon_cur.execute('SELECT UPC FROM ITEMS WHERE UPC = ? OR ASIN = ? COLLATE NOCASE LIMIT 1', (upc, upc))
                    if amazon_cur.fetchone():
                        cost_source = 'amazon'
                        # Note: amazonStore.db doesn't have cost data, just marking source
            
            item_data['cost'] = cost
            item_data['cost_source'] = cost_source
            item_data['upc'] = upc
            item_data['bol_number'] = bol_number
            
            transactions.append(item_data)
        
        # No need to close connections - they're pooled and reused
        
        # Get LOT # data from rawbol.db upload_logs
        lot_options = []
        try:
            # Reuse existing connection
            rawbol_cur.execute('''
                SELECT 
                    lot_number,
                    import_date,
                    total_client_cost
                FROM upload_logs
                WHERE lot_number IS NOT NULL 
                    AND lot_number != ''
                ORDER BY import_date DESC
            ''')
            
            rows = rawbol_cur.fetchall()
            print(f"DEBUG: Found {len(rows)} LOT entries in rawbol.db")
            
            for row in rows:
                lot_num = row['lot_number']
                import_date = row['import_date']
                total_cost = row['total_client_cost']
                
                # Format as "Date + LOT #"
                display_name = f"{import_date} + {lot_num}"
                lot_options.append({
                    'value': lot_num,
                    'label': display_name,
                    'date': import_date,
                    'cost': float(total_cost) if total_cost else 0
                })
                print(f"DEBUG: Added LOT option: {display_name} (${float(total_cost) if total_cost else 0:.2f})")
            
        except Exception as e:
            print(f"Warning: Could not fetch LOT # data from rawbol.db: {e}")
            import traceback
            traceback.print_exc()
        
        # Add unmatched returns as standalone transactions (for financial impact)
        for unmatched in unmatched_returns:
            # Create a transaction entry for unmatched return
            # Use negative values to represent a loss
            transactions.append({
                'id': None,
                'order_id': unmatched['order_id'],
                'title': unmatched['title'],
                'quantity': 0,
                'price': 0,  # No revenue from return
                'seller_fee': 0,
                'seller_fee_refund': unmatched.get('seller_fee_refund', 0.0),
                'taxes': 0,
                'paid_time': unmatched['return_date'],
                'shipped_time': None,
                'barcode': None,
                'store': unmatched['store'] or 'unknown',
                'location': None,
                'shipping_cost': 0,
                'lot_number': None,
                'return_cost': unmatched['return_cost'],
                'cost': 0,
                'cost_source': 'unmatched_return',
                'upc': None,
                'bol_number': None,
                'is_unmatched_return': True  # Flag for UI
            })
        
        print(f"DEBUG: Total transactions (including {len(unmatched_returns)} unmatched returns): {len(transactions)}")
        
        # Get BOL extract totals from rawbol.db (total_client_cost + shipping_cost per LOT)
        bol_extract_totals = {}
        try:
            rawbol_cur.execute('''
                SELECT 
                    lot_number,
                    total_client_cost,
                    shipping_cost
                FROM upload_logs
                WHERE lot_number IS NOT NULL
            ''')
            
            for row in rawbol_cur.fetchall():
                lot_num = row['lot_number']
                total_cost = float(row['total_client_cost']) if row['total_client_cost'] else 0
                shipping_cost = float(row['shipping_cost']) if row['shipping_cost'] else 0
                
                bol_extract_totals[lot_num] = {
                    'total_cost': total_cost,
                    'shipping_cost': shipping_cost
                }
            
            print(f"DEBUG: Loaded BOL extract totals for {len(bol_extract_totals)} LOTs")
        except Exception as e:
            print(f"Warning: Could not fetch BOL extract totals: {e}")
        
        return jsonify({
            'success': True,
            'transactions': transactions,
            'count': len(transactions),
            'lot_options': lot_options,
            'bol_extract_totals': bol_extract_totals
        })
        
    except Exception as e:
        print(f"Error in financial analytics API: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({
            'success': False,
            'error': ss_errors._safe_error(e)
        }), 500


@ss_runtime.cache.cached(timeout=300, query_string=True)  # Cache for 5 minutes
def api_amazon_financial_summary():
    """
    Get aggregate financial totals from Amazon Financial Events API.
    Returns total seller fees, shipping costs, taxes, and returns data across all orders.
    Query params: days (default 30), include_returns (default true)
    """
    if not ss_integrations.AMAZON_AVAILABLE:
        return jsonify({'success': False, 'error': 'Amazon integration not available'}), 503
    
    try:
        days = int(request.args.get('days', 30))
        include_returns = request.args.get('include_returns', 'true').lower() == 'true'
        
        amazon = ss_integrations.AmazonManager()
        financial_data = amazon.get_financial_events(days_back=days)
        
        # Calculate totals
        total_seller_fees = 0
        total_shipping_costs = 0
        total_taxes = 0
        orders_with_fees = 0
        orders_with_shipping = 0
        
        for order_id, data in financial_data.items():
            if data['seller_fee'] > 0:
                total_seller_fees += data['seller_fee']
                orders_with_fees += 1
            
            if data['shipping_cost'] > 0:
                total_shipping_costs += data['shipping_cost']
                orders_with_shipping += 1
            
            total_taxes += data['taxes']
        
        summary = {
            'total_orders': len(financial_data),
            'orders_with_fees': orders_with_fees,
            'orders_with_shipping': orders_with_shipping,
            'total_seller_fees': round(total_seller_fees, 2),
            'total_shipping_costs': round(total_shipping_costs, 2),
            'total_taxes': round(total_taxes, 2),
            'total_amazon_costs': round(total_seller_fees + total_shipping_costs + total_taxes, 2)
        }
        
        # Add returns data if requested
        if include_returns:
            conn = None
            try:
                conn = sqlite3.connect('sold.db')
                conn.row_factory = sqlite3.Row
                cur = conn.cursor()
                
                # Get returns within the same time period
                cur.execute('''
                    SELECT 
                        COUNT(*) as total_returns,
                        SUM(refund_amount) as total_refunded,
                        SUM(refund_amount + original_shipping_cost + return_shipping_cost) as total_return_cost
                    FROM returns
                    WHERE store = 'amazon'
                    AND return_date >= datetime('now', '-' || ? || ' days')
                ''', (days,))
                
                returns_row = cur.fetchone()
                
                summary['returns'] = {
                    'total_returns': returns_row['total_returns'] or 0,
                    'total_refunded': round(returns_row['total_refunded'] or 0, 2),
                    'total_return_cost': round(returns_row['total_return_cost'] or 0, 2),
                    'return_rate': round((returns_row['total_returns'] or 0) / len(financial_data) * 100, 2) if len(financial_data) > 0 else 0
                }
                
                # Calculate net profit impact
                summary['net_costs_with_returns'] = round(
                    summary['total_amazon_costs'] + summary['returns']['total_return_cost'], 2
                )
                
            except Exception as e:
                print(f"Warning: Could not include returns data: {e}")
                summary['returns'] = None
            finally:
                if conn is not None:
                    conn.close()
        
        return jsonify({
            'success': True,
            'days': days,
            'summary': summary
        })
        
    except Exception as e:
        print(f"Error fetching Amazon financial summary: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({
            'success': False,
            'error': ss_errors._safe_error(e)
        }), 500


def api_refresh_sold_data():
    """
    Re-enrich sold.db orders with latest barcode and LOT number data.
    This updates old Amazon items that may have ASIN as barcode to use proper UPC.
    """
    try:
        print("DEBUG: Refreshing sold data...")
        
        # Use connection pool
        sold_conn = ss_database.get_db_connection('sold.db')
        sold_cur = sold_conn.cursor()
        
        amazon_conn = ss_database.get_db_connection('amazonStore.db')
        amazon_cur = amazon_conn.cursor()
        
        rawbol_conn = ss_database.get_db_connection('rawbol.db')
        rawbol_cur = rawbol_conn.cursor()
        
        # Get Amazon orders that might need updating
        sold_cur.execute('''
            SELECT id, order_id, item_id, barcode, lot_number
            FROM orders
            WHERE store = 'amazon' AND barcode IS NOT NULL
        ''')
        amazon_orders = sold_cur.fetchall()
        
        updated_barcodes = 0
        updated_lot_numbers = 0
        ambiguous_lot_matches = 0
        
        for order in amazon_orders:
            order_id = order['order_id']
            current_barcode = order['barcode']
            current_lot = order['lot_number']
            needs_update = False
            new_barcode = current_barcode
            new_lot = current_lot
            
            # Check if barcode looks like ASIN (old format)
            if current_barcode and (current_barcode.startswith('B0') or len(current_barcode) == 10):
                # Try to get UPC from amazonStore
                amazon_cur.execute('SELECT UPC FROM ITEMS WHERE ASIN = ? COLLATE NOCASE', (current_barcode,))
                amazon_row = amazon_cur.fetchone()
                if amazon_row and amazon_row['UPC'] and amazon_row['UPC'] != current_barcode:
                    new_barcode = amazon_row['UPC']
                    needs_update = True
                    updated_barcodes += 1
            
            # Check if we can enrich lot_number from rawbol using the (possibly new) barcode
            if not current_lot or str(current_lot).lower() in ['', 'nan', 'none', 'null']:
                lot_candidates = []
                rawbol_cur.execute('''
                    SELECT DISTINCT lot_number
                    FROM raw_bol_items
                    WHERE upc = ? COLLATE NOCASE
                      AND lot_number IS NOT NULL
                      AND TRIM(COALESCE(lot_number, '')) != ''
                ''', (new_barcode,))
                lot_candidates = [r['lot_number'] for r in rawbol_cur.fetchall() if r['lot_number']]

                # Try a stripped-numeric fallback for leading-zero mismatches.
                if not lot_candidates and new_barcode:
                    stripped = str(new_barcode).lstrip('0')
                    if stripped and stripped != str(new_barcode):
                        rawbol_cur.execute('''
                            SELECT DISTINCT lot_number
                            FROM raw_bol_items
                            WHERE upc = ? COLLATE NOCASE
                              AND lot_number IS NOT NULL
                              AND TRIM(COALESCE(lot_number, '')) != ''
                        ''', (stripped,))
                        lot_candidates = [r['lot_number'] for r in rawbol_cur.fetchall() if r['lot_number']]

                if len(lot_candidates) == 1:
                    new_lot = lot_candidates[0]
                    needs_update = True
                    updated_lot_numbers += 1
                elif len(lot_candidates) > 1:
                    ambiguous_lot_matches += 1
                    print(f"⚠️ Ambiguous LOT match for order {order_id} barcode {new_barcode}: {lot_candidates}")
            
            # Update if changes were made
            if needs_update:
                sold_cur.execute('UPDATE orders SET barcode = ?, lot_number = ? WHERE id = ?', 
                               (new_barcode, new_lot, order['id']))
                print(f"✅ Updated {order_id}: barcode={new_barcode}, lot={new_lot}")
        
        sold_conn.commit()
        
        # No need to close - connections are pooled
        
        return jsonify({
            'success': True,
            'updated_barcodes': updated_barcodes,
            'updated_lot_numbers': updated_lot_numbers,
            'ambiguous_lot_matches': ambiguous_lot_matches,
            'message': f'Updated {updated_barcodes} barcodes, {updated_lot_numbers} LOT numbers, {ambiguous_lot_matches} ambiguous LOT matches skipped'
        })
        
    except Exception as e:
        print(f"Error refreshing sold data: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({
            'success': False,
            'error': ss_errors._safe_error(e)
        }), 500


def api_inventory_quantity_history():
    """Return a move-safe reconstruction of total active warehouse units over time."""
    chart_start = datetime.datetime(2025, 11, 19)
    rack_conn = None
    history_conn = None
    rawbol_conn = None
    sold_conn = None
    try:
        ss_inventory_history._flush_searchrack_history_outbox()

        rack_conn = sqlite3.connect(str(ss_config.BASE_DIR / 'searchRack.db'), timeout=30.0)
        rack_cur = rack_conn.cursor()
        rack_cur.execute('''
            SELECT COALESCE(SUM(COALESCE(CAST(QUANTITY AS INTEGER), 0)), 0)
            FROM SEARCHRACK
            WHERE COALESCE(CAST(QUANTITY AS INTEGER), 0) > 0
        ''')
        current_quantity = max(0, ss_normalization._coerce_int(rack_cur.fetchone()[0], 0))

        sold_points = []
        try:
            sold_conn = sqlite3.connect(str(ss_config.BASE_DIR / 'sold.db'), timeout=30.0)
            sold_conn.row_factory = sqlite3.Row
            sold_cur = sold_conn.cursor()
            sold_cur.execute('''
                SELECT id, paid_time, COALESCE(CAST(quantity AS INTEGER), 1) AS quantity
                FROM orders
                WHERE paid_time IS NOT NULL
                  AND TRIM(paid_time) != ''
                ORDER BY paid_time, id
            ''')
            sold_events = []
            for sold_row in sold_cur.fetchall():
                sold_at = ss_inventory_history._history_timestamp(sold_row['paid_time'])
                if sold_at is None or sold_at < chart_start:
                    continue
                sold_events.append((
                    sold_at,
                    max(0, ss_normalization._coerce_int(sold_row['quantity'], 1)),
                    max(0, ss_normalization._coerce_int(sold_row['id'], 0)),
                ))
            sold_events.sort(key=lambda event: (event[0], event[2]))
            sold_points.append({
                'timestamp': chart_start.isoformat(),
                'quantity': 0,
            })
            if sold_events:
                running_sold = 0
                for sold_at, sold_quantity, _sold_id in sold_events:
                    running_sold += sold_quantity
                    sold_points.append({
                        'timestamp': sold_at.isoformat(),
                        'quantity': running_sold,
                    })
        except Exception as sold_error:
            ss_config.logger.warning('Sold-items chart series unavailable: %s', sold_error)

        bol_uploads = []
        try:
            rawbol_conn = sqlite3.connect(str(ss_config.BASE_DIR / 'rawbol.db'), timeout=30.0)
            rawbol_conn.row_factory = sqlite3.Row
            rawbol_cur = rawbol_conn.cursor()
            rawbol_cur.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='upload_logs'"
            )
            if rawbol_cur.fetchone():
                rawbol_cur.execute('''
                    SELECT u.id, u.filename, u.lot_number, u.bol_location, u.import_date,
                           u.rows_imported, u.uploaded_at, u.total_client_cost, u.shipping_cost,
                           COALESCE((
                               SELECT SUM(
                                   CASE
                                       WHEN COALESCE(CAST(items.quantity AS INTEGER), 0) > 0
                                       THEN CAST(items.quantity AS INTEGER)
                                       ELSE 0
                                   END
                               )
                               FROM raw_bol_items AS items
                               WHERE items.lot_number = u.lot_number
                           ), 0) AS total_quantity
                    FROM upload_logs AS u
                    ORDER BY COALESCE(NULLIF(u.uploaded_at, ''), u.import_date), u.id
                ''')
                for upload_row in rawbol_cur.fetchall():
                    upload = dict(upload_row)
                    uploaded_at = ss_inventory_history._history_timestamp(
                        upload.get('uploaded_at') or upload.get('import_date')
                    )
                    if uploaded_at is None:
                        continue
                    if uploaded_at < chart_start:
                        continue
                    total_client_cost = upload.get('total_client_cost')
                    shipping_cost = upload.get('shipping_cost')
                    bol_uploads.append({
                        'id': upload.get('id'),
                        'timestamp': uploaded_at.isoformat(),
                        'filename': str(upload.get('filename') or '').strip(),
                        'lot_number': str(upload.get('lot_number') or '').strip(),
                        'bol_location': str(upload.get('bol_location') or '').strip(),
                        'import_date': str(upload.get('import_date') or '').strip(),
                        'rows_imported': (
                            max(0, ss_normalization._coerce_int(upload.get('rows_imported'), 0))
                            if upload.get('rows_imported') not in (None, '') else None
                        ),
                        'total_quantity': max(
                            0, ss_normalization._coerce_int(upload.get('total_quantity'), 0)
                        ),
                        'total_client_cost': (
                            float(total_client_cost)
                            if total_client_cost not in (None, '') else None
                        ),
                        'shipping_cost': (
                            float(shipping_cost)
                            if shipping_cost not in (None, '') else None
                        ),
                    })
        except Exception as bol_error:
            ss_config.logger.warning('BOL chart markers unavailable: %s', bol_error)

        history_conn = sqlite3.connect(str(ss_config.BASE_DIR / 'rackhistory.db'), timeout=30.0)
        history_conn.row_factory = sqlite3.Row
        history_cur = history_conn.cursor()
        ss_inventory_history._ensure_removed_items_table(history_cur)
        history_conn.commit()
        history_cur.execute('''
            SELECT id, removed_at, searchrack_id, old_quantity, new_quantity,
                   removal_type
            FROM removed_items
            WHERE old_quantity IS NOT NULL
              AND new_quantity IS NOT NULL
              AND COALESCE(event_status, 'applied') != 'superseded'
            ORDER BY removed_at, id
        ''')

        events = []
        for raw_row in history_cur.fetchall():
            row = dict(raw_row)
            occurred_at = ss_inventory_history._history_timestamp(row.get('removed_at'))
            if occurred_at is None:
                continue
            old_quantity = max(0, ss_normalization._coerce_int(row.get('old_quantity'), 0))
            new_quantity = max(0, ss_normalization._coerce_int(row.get('new_quantity'), 0))
            row_id = row.get('searchrack_id')
            row_key = str(row_id) if row_id not in (None, '') else f"event:{row.get('id')}"
            events.append({
                'timestamp': occurred_at,
                'delta': new_quantity - old_quantity,
                'old_quantity': old_quantity,
                'row_key': row_key,
                'event_id': max(0, ss_normalization._coerce_int(row.get('id'), 0)),
            })
        events.sort(key=lambda event: (event['timestamp'], event['event_id']))

        first_old_by_row = {}
        for event in events:
            if event['row_key'] not in first_old_by_row:
                first_old_by_row[event['row_key']] = event['old_quantity']

        now = datetime.datetime.now()
        if not events:
            points = [
                {
                    'timestamp': chart_start.isoformat(),
                    'quantity': current_quantity,
                },
                {
                    'timestamp': now.isoformat(),
                    'quantity': current_quantity,
                },
            ]
            return jsonify({
                'success': True,
                'points': points,
                'peak_quantity': current_quantity,
                'peak_timestamp': now.isoformat(),
                'current_quantity': current_quantity,
                'reconstructed': False,
                'bol_uploads': bol_uploads,
                'sold_points': sold_points,
                'chart_start': chart_start.isoformat(),
            })

        # A row whose first recorded event starts above zero was already active
        # when retained rack history began.
        initial_quantity = sum(first_old_by_row.values())
        cumulative = 0
        minimum_cumulative = 0
        for event in events:
            cumulative += event['delta']
            minimum_cumulative = min(minimum_cumulative, cumulative)
        if initial_quantity + minimum_cumulative < 0:
            initial_quantity += -(initial_quantity + minimum_cumulative)

        running_quantity = initial_quantity
        for event in events:
            if event['timestamp'] >= chart_start:
                break
            running_quantity = max(
                0, running_quantity + event['delta']
            )

        points = [{
            'timestamp': chart_start.isoformat(),
            'quantity': running_quantity,
        }]
        for event in events:
            if event['timestamp'] < chart_start:
                continue
            running_quantity = max(0, running_quantity + event['delta'])
            points.append({
                'timestamp': event['timestamp'].isoformat(),
                'quantity': running_quantity,
            })

        reconstructed = running_quantity != current_quantity
        # SEARCHRACK is authoritative. The final anchor accounts for inventory
        # outside the retained history window while guaranteeing an exact current value.
        points.append({
            'timestamp': now.isoformat(),
            'quantity': current_quantity,
        })

        peak_point = max(
            points,
            key=lambda point: (
                max(0, ss_normalization._coerce_int(point.get('quantity'), 0)),
                point.get('timestamp') or ''
            )
        )
        return jsonify({
            'success': True,
            'points': points,
            'peak_quantity': max(0, ss_normalization._coerce_int(peak_point.get('quantity'), 0)),
            'peak_timestamp': str(peak_point.get('timestamp') or ''),
            'current_quantity': current_quantity,
            'reconstructed': reconstructed,
            'history_start': points[0]['timestamp'],
            'bol_uploads': bol_uploads,
            'sold_points': sold_points,
            'chart_start': chart_start.isoformat(),
        })
    except Exception as exc:
        return jsonify({
            'success': False,
            'error': ss_errors._safe_error(exc, 'inventory_quantity_history')
        }), 500
    finally:
        if history_conn is not None:
            history_conn.close()
        if rack_conn is not None:
            rack_conn.close()
        if rawbol_conn is not None:
            rawbol_conn.close()
        if sold_conn is not None:
            sold_conn.close()


def api_sold_performance_history():
    """Return cumulative sold-unit and payout/proceeds series by store."""
    store_keys = ('ebay', 'amazon', 'facebook')
    sold_conn = None
    rawbol_conn = None
    marketplace_conn = None
    warnings = []

    def normalized_store(value):
        text = str(value or '').strip().casefold()
        if 'ebay' in text:
            return 'ebay'
        if 'amazon' in text:
            return 'amazon'
        if 'facebook' in text or 'marketplace' in text:
            return 'facebook'
        return ''

    def cumulative_series(events, value_name):
        running = {
            'all': 0.0,
            'ebay': 0.0,
            'amazon': 0.0,
            'facebook': 0.0,
        }
        points = {key: [] for key in running}
        for occurred_at, store, value, _event_id in sorted(
            events, key=lambda item: (item[0], item[3])
        ):
            if store not in store_keys:
                continue
            running[store] += value
            running['all'] += value
            points[store].append({
                'timestamp': occurred_at.isoformat(),
                value_name: round(running[store], 2),
            })
            points['all'].append({
                'timestamp': occurred_at.isoformat(),
                value_name: round(running['all'], 2),
            })
        return points, {
            key: round(value, 2) for key, value in running.items()
        }

    try:
        sold_conn = sqlite3.connect(
            str(ss_config.BASE_DIR / 'sold.db'), timeout=30.0
        )
        sold_conn.row_factory = sqlite3.Row
        sold_cur = sold_conn.cursor()
        sold_cur.execute('''
            SELECT id, paid_time, store,
                   COALESCE(CAST(quantity AS INTEGER), 1) AS quantity
            FROM orders
            WHERE paid_time IS NOT NULL
              AND TRIM(paid_time) != ''
            ORDER BY paid_time, id
        ''')
        sale_events = []
        ignored_store_units = 0
        for row in sold_cur.fetchall():
            sold_at = ss_inventory_history._history_timestamp(row['paid_time'])
            quantity = max(0, ss_normalization._coerce_int(row['quantity'], 1))
            if sold_at is None or quantity <= 0:
                continue
            store = normalized_store(row['store'])
            if not store:
                ignored_store_units += quantity
                continue
            sale_events.append((
                sold_at,
                store,
                float(quantity),
                max(0, ss_normalization._coerce_int(row['id'], 0)),
            ))
        if ignored_store_units:
            warnings.append(
                f'{ignored_store_units} sold units with an unknown/test store '
                'were excluded from the All line.'
            )

        profit_events = []
        try:
            sold_cur.execute('''
                SELECT id, store, payout_date, end_date, start_date,
                       amount, currency
                FROM payouts
                WHERE COALESCE(amount, 0) != 0
                ORDER BY COALESCE(
                    NULLIF(payout_date, ''),
                    NULLIF(end_date, ''),
                    start_date
                ), id
            ''')
            for row in sold_cur.fetchall():
                store = normalized_store(row['store'])
                currency = str(row['currency'] or 'USD').strip().upper()
                payout_at = ss_inventory_history._history_timestamp(
                    row['payout_date']
                    or row['end_date']
                    or row['start_date']
                )
                if (
                    store not in {'ebay', 'amazon'}
                    or currency != 'USD'
                    or payout_at is None
                ):
                    continue
                profit_events.append((
                    payout_at,
                    store,
                    float(row['amount'] or 0),
                    max(0, ss_normalization._coerce_int(row['id'], 0)),
                ))
        except sqlite3.Error as payout_error:
            warnings.append(
                f'Payout profit data unavailable: {payout_error}'
            )

        try:
            marketplace_conn = sqlite3.connect(
                str(ss_config.BASE_DIR / 'marketplace.db'), timeout=30.0
            )
            marketplace_conn.row_factory = sqlite3.Row
            marketplace_cur = marketplace_conn.cursor()
            marketplace_cur.execute('''
                SELECT id, sale_date, created_at, price
                FROM marketplace_sales
                WHERE COALESCE(price, 0) != 0
                ORDER BY COALESCE(
                    NULLIF(created_at, ''),
                    sale_date
                ), id
            ''')
            for row in marketplace_cur.fetchall():
                sale_at = ss_inventory_history._history_timestamp(
                    row['sale_date'] or row['created_at']
                )
                if sale_at is None:
                    continue
                profit_events.append((
                    sale_at,
                    'facebook',
                    float(row['price'] or 0),
                    1000000000 + max(
                        0, ss_normalization._coerce_int(row['id'], 0)
                    ),
                ))
        except sqlite3.Error as marketplace_error:
            warnings.append(
                f'Facebook Marketplace profit data unavailable: '
                f'{marketplace_error}'
            )

        bol_uploads = []
        try:
            rawbol_conn = sqlite3.connect(
                str(ss_config.BASE_DIR / 'rawbol.db'), timeout=30.0
            )
            rawbol_conn.row_factory = sqlite3.Row
            rawbol_cur = rawbol_conn.cursor()
            rawbol_cur.execute('''
                SELECT u.id, u.filename, u.lot_number, u.bol_location,
                       u.import_date, u.uploaded_at,
                       u.total_client_cost, u.shipping_cost,
                       COALESCE((
                           SELECT SUM(
                               CASE
                                   WHEN COALESCE(
                                       CAST(items.quantity AS INTEGER), 0
                                   ) > 0
                                   THEN CAST(items.quantity AS INTEGER)
                                   ELSE 0
                               END
                           )
                           FROM raw_bol_items AS items
                           WHERE items.lot_number = u.lot_number
                       ), 0) AS total_quantity
                FROM upload_logs AS u
                ORDER BY COALESCE(
                    NULLIF(u.uploaded_at, ''),
                    u.import_date
                ), u.id
            ''')
            for row in rawbol_cur.fetchall():
                uploaded_at = ss_inventory_history._history_timestamp(
                    row['uploaded_at'] or row['import_date']
                )
                if uploaded_at is None:
                    continue
                bol_uploads.append({
                    'id': max(0, ss_normalization._coerce_int(row['id'], 0)),
                    'timestamp': uploaded_at.isoformat(),
                    'filename': str(row['filename'] or '').strip(),
                    'lot_number': str(row['lot_number'] or '').strip(),
                    'bol_location': str(
                        row['bol_location'] or ''
                    ).strip(),
                    'import_date': str(row['import_date'] or '').strip(),
                    'total_quantity': max(
                        0, ss_normalization._coerce_int(row['total_quantity'], 0)
                    ),
                    'total_client_cost': (
                        float(row['total_client_cost'])
                        if row['total_client_cost'] not in (None, '')
                        else None
                    ),
                    'shipping_cost': (
                        float(row['shipping_cost'])
                        if row['shipping_cost'] not in (None, '')
                        else None
                    ),
                })
        except sqlite3.Error as bol_error:
            warnings.append(f'BOL flags unavailable: {bol_error}')

        timeline = (
            [event[0] for event in sale_events]
            + [event[0] for event in profit_events]
            + [
                ss_inventory_history._history_timestamp(upload['timestamp'])
                for upload in bol_uploads
            ]
        )
        timeline = [value for value in timeline if value is not None]
        now = datetime.datetime.now()
        chart_start = (
            min(timeline)
            if timeline
            else now - datetime.timedelta(days=30)
        )
        chart_end = max(timeline + [now])

        sales_points, sales_totals = cumulative_series(
            sale_events, 'quantity'
        )
        profit_points, profit_totals = cumulative_series(
            profit_events, 'amount'
        )
        for key in ('all', *store_keys):
            sales_points[key].insert(0, {
                'timestamp': chart_start.isoformat(),
                'quantity': 0,
            })
            profit_points[key].insert(0, {
                'timestamp': chart_start.isoformat(),
                'amount': 0.0,
            })
            sales_points[key].append({
                'timestamp': chart_end.isoformat(),
                'quantity': int(round(sales_totals[key])),
            })
            profit_points[key].append({
                'timestamp': chart_end.isoformat(),
                'amount': profit_totals[key],
            })

        return jsonify({
            'success': True,
            'chart_start': chart_start.isoformat(),
            'chart_end': chart_end.isoformat(),
            'series': {
                key: {
                    'sales_points': sales_points[key],
                    'profit_points': profit_points[key],
                    'sold_units': int(round(sales_totals[key])),
                    'profit': profit_totals[key],
                }
                for key in ('all', *store_keys)
            },
            'bol_uploads': bol_uploads,
            'profit_definition': {
                'ebay': 'USD payouts from /payouts',
                'amazon': 'USD settlements from /payouts',
                'facebook': 'Marketplace sale proceeds from /marketplace-stats',
                'all': 'eBay + Amazon payouts and Facebook sale proceeds',
            },
            'warnings': warnings,
        })
    except Exception as exc:
        ss_config.logger.exception('Sold performance history failed')
        return jsonify({
            'success': False,
            'error': ss_errors._safe_error(exc, 'sold_performance_history')
        }), 500
    finally:
        if sold_conn is not None:
            sold_conn.close()
        if rawbol_conn is not None:
            rawbol_conn.close()
        if marketplace_conn is not None:
            marketplace_conn.close()
