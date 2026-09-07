"""Raw bol for Sweet Shelves."""

import sqlite3
from flask import jsonify, request
from . import errors as ss_errors, integrations as ss_integrations


def extractor_upload():
    """Legacy route - kept for backward compatibility but not used by new UI"""
    if not ss_integrations.BOL_AVAILABLE:
        return jsonify({'success': False, 'error': 'BOL extractor not available (pandas not installed)'}), 500
    if 'excel_file' not in request.files or 'import_date' not in request.form:
        return jsonify({'success': False, 'error': 'Missing file or import date.'}), 400
    file = request.files['excel_file']
    import_date = request.form['import_date']
    if file.filename == '':
        return jsonify({'success': False, 'error': 'No file selected.'}), 400
    if file:
        print('DEBUG: Received file:', file.filename, 'Content-Type:', file.content_type, 'Size:', file.content_length)
        result = ss_integrations.process_bol_excel(file, import_date)
        return jsonify(result)
    return jsonify({'success': False, 'error': 'Unknown error during file upload.'}), 500


def api_rawbol_upload():
    """Upload .xls file to raw BOL database and auto-sync"""
    if not ss_integrations.BOL_AVAILABLE:
        return jsonify({'success': False, 'error': 'BOL extractor not available (pandas not installed)'}), 500
    
    if 'excel_file' not in request.files or 'import_date' not in request.form:
        return jsonify({'success': False, 'error': 'Missing file or import date.'}), 400
    
    file = request.files['excel_file']
    import_date = request.form['import_date'].strip()
    shipping_cost_str = request.form.get('shipping_cost', '').strip()
    
    # Parse shipping cost if provided
    shipping_cost = None
    if shipping_cost_str:
        try:
            shipping_cost = float(shipping_cost_str)
        except ValueError:
            return jsonify({'success': False, 'error': 'Invalid shipping cost value.'}), 400
    
    if file.filename == '':
        return jsonify({'success': False, 'error': 'No file selected.'}), 400
    
    print(f'DEBUG: Received file: {file.filename}, Date: {import_date}, Shipping: {shipping_cost}')
    
    # Upload to rawbol.db - lot_number will be extracted from file
    # Use filename without extension as temporary lot_number for database storage
    import os
    temp_lot_number = os.path.splitext(file.filename)[0]
    
    result = ss_integrations.process_bol_excel(file, temp_lot_number, import_date, shipping_cost)
    
    # If upload successful, auto-sync ONLY THIS LOT to bol.db
    if result.get('success'):
        from rawbol_manager import sync_rawbol_to_bol
        # Use the lot_number from result (which is the extracted LOT #)
        lot_to_sync = result.get('lot_number')
        sync_result = sync_rawbol_to_bol(specific_lot=lot_to_sync)
        
        if sync_result.get('success'):
            # Combine results
            result['synced'] = True
            result['sync_updated'] = sync_result.get('updated', 0)
            result['sync_inserted'] = sync_result.get('inserted', 0)
        else:
            result['synced'] = False
            result['sync_error'] = sync_result.get('error', 'Unknown sync error')
    
    return jsonify(result)


def api_rawbol_backfill_retail():
    """Backfill ONLY raw_bol_items.original_retail for an existing LOT from uploaded BOL file."""
    if not ss_integrations.BOL_AVAILABLE:
        return jsonify({'success': False, 'error': 'BOL extractor not available (pandas not installed)'}), 500

    if 'excel_file' not in request.files:
        return jsonify({'success': False, 'error': 'Missing file.'}), 400

    file = request.files['excel_file']
    if file.filename == '':
        return jsonify({'success': False, 'error': 'No file selected.'}), 400

    overwrite_existing = str(request.form.get('overwrite_existing') or '').strip().lower() in ('1', 'true', 'yes', 'on')
    lot_override = (request.form.get('lot_override') or '').strip()

    result = ss_integrations.process_bol_retail_backfill(
        file,
        overwrite_existing=overwrite_existing,
        forced_lot_number=lot_override
    )

    if not result.get('success'):
        return jsonify(result), 400
    return jsonify(result)


def api_rawbol_view():
    """Get all raw BOL items"""
    from rawbol_manager import get_all_raw_bol_items
    result = get_all_raw_bol_items()
    return jsonify(result)


def api_rawbol_logs():
    """Get upload logs"""
    from rawbol_manager import get_upload_logs
    result = get_upload_logs()
    return jsonify(result)


def api_rawbol_stats():
    """Get rawbol.db statistics"""
    from rawbol_manager import get_rawbol_stats
    result = get_rawbol_stats()
    return jsonify(result)


def api_rawbol_delete_lot(lot_number):
    """Delete a specific lot and desync from bol.db"""
    from rawbol_manager import delete_lot
    result = delete_lot(lot_number)
    return jsonify(result)


def api_rawbol_update_lot(lot_number):
    """Update LOT information (name, date, shipping cost)"""
    conn = None
    try:
        data = request.get_json() or {}
        new_lot_number = data.get('lot_number', '').strip()
        import_date = data.get('import_date', '').strip()
        shipping_cost_str = data.get('shipping_cost', '')
        warnings = []
        
        # Parse shipping cost
        shipping_cost = None
        if shipping_cost_str != '' and shipping_cost_str is not None:
            try:
                shipping_cost = float(shipping_cost_str)
            except ValueError:
                return jsonify({'success': False, 'error': 'Invalid shipping cost value.'}), 400
        
        conn = sqlite3.connect('rawbol.db')
        cur = conn.cursor()
        
        # Update upload_logs
        update_fields = []
        update_values = []
        
        if new_lot_number and new_lot_number != lot_number:
            # Check if new lot number already exists
            cur.execute('SELECT COUNT(*) as count FROM upload_logs WHERE lot_number = ?', (new_lot_number,))
            if cur.fetchone()[0] > 0:
                return jsonify({'success': False, 'error': f'LOT # "{new_lot_number}" already exists.'}), 400
            update_fields.append('lot_number = ?')
            update_values.append(new_lot_number)
        
        if import_date:
            update_fields.append('import_date = ?')
            update_values.append(import_date)
        
        if shipping_cost is not None:
            update_fields.append('shipping_cost = ?')
            update_values.append(shipping_cost)
        
        if not update_fields:
            return jsonify({'success': False, 'error': 'No fields to update.'}), 400
        
        # Update upload_logs
        update_values.append(lot_number)  # WHERE clause
        cur.execute(f"UPDATE upload_logs SET {', '.join(update_fields)} WHERE lot_number = ?", update_values)
        
        # If lot_number changed, also update raw_bol_items
        if new_lot_number and new_lot_number != lot_number:
            cur.execute('UPDATE raw_bol_items SET lot_number = ? WHERE lot_number = ?', (new_lot_number, lot_number))
            # Keep sync tracking aligned with renamed LOT.
            cur.execute('UPDATE synced_lots SET lot_number = ? WHERE lot_number = ?', (new_lot_number, lot_number))
        
        if import_date and (new_lot_number and new_lot_number != lot_number or not new_lot_number):
            # Update import_date in raw_bol_items
            cur.execute('UPDATE raw_bol_items SET import_date = ? WHERE lot_number = ?', 
                       (import_date, new_lot_number if new_lot_number else lot_number))
        
        conn.commit()

        # Propagate LOT rename/date changes to bol.db and sold.db so downstream analytics remain consistent.
        target_lot = new_lot_number if new_lot_number else lot_number
        if new_lot_number and new_lot_number != lot_number:
            bol_conn = None
            try:
                bol_conn = sqlite3.connect('bol.db')
                bol_cur = bol_conn.cursor()
                bol_cur.execute('UPDATE bol_items SET lot_number = ? WHERE lot_number = ?', (new_lot_number, lot_number))
                bol_conn.commit()
            except Exception as e:
                warnings.append(f'bol.db lot rename sync warning: {e}')
            finally:
                try:
                    bol_conn.close()
                except Exception:
                    pass
            sold_conn = None
            try:
                sold_conn = sqlite3.connect('sold.db')
                sold_cur = sold_conn.cursor()
                sold_cur.execute('UPDATE orders SET lot_number = ? WHERE lot_number = ?', (new_lot_number, lot_number))
                sold_conn.commit()
            except Exception as e:
                warnings.append(f'sold.db lot rename sync warning: {e}')
            finally:
                try:
                    sold_conn.close()
                except Exception:
                    pass

        if import_date:
            bol_conn = None
            try:
                bol_conn = sqlite3.connect('bol.db')
                bol_cur = bol_conn.cursor()
                bol_cur.execute('UPDATE bol_items SET import_date = ? WHERE lot_number = ?', (import_date, target_lot))
                bol_conn.commit()
            except Exception as e:
                warnings.append(f'bol.db import_date sync warning: {e}')
            finally:
                try:
                    bol_conn.close()
                except Exception:
                    pass
        
        return jsonify({
            'success': True,
            'message': 'LOT information updated successfully.',
            'new_lot_number': target_lot,
            'warnings': warnings
        })
        
    except Exception as e:
        return jsonify({'success': False, 'error': ss_errors._safe_error(e)}), 500
    finally:
        if conn:
            conn.close()


def api_rawbol_desync_all():
    """Desync (undo) ALL raw BOL from main BOL database"""
    from rawbol_manager import desync_all_rawbol
    result = desync_all_rawbol()
    return jsonify(result)


def api_sold_enrich_lot_numbers():
    """Backfill LOT numbers for all sold orders using smart matching"""
    try:
        from lot_matcher import backfill_all_sold_orders
        result = backfill_all_sold_orders()
        return jsonify(result)
    except Exception as e:
        return jsonify({'success': False, 'error': ss_errors._safe_error(e)}), 500


def api_sold_enrich_recent():
    """Enrich recent orders (last 7 days) with LOT numbers"""
    try:
        from lot_matcher import enrich_new_orders
        days_back = int(request.args.get('days', 7))
        result = enrich_new_orders(days_back=days_back)
        return jsonify(result)
    except Exception as e:
        return jsonify({'success': False, 'error': ss_errors._safe_error(e)}), 500


def api_rawbol_items(lot_number):
    """Get items for a specific LOT with calculated average cost."""
    conn = None
    try:
        conn = sqlite3.connect('rawbol.db')
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        
        # Get BOL total cost from upload_logs
        cur.execute('SELECT total_client_cost FROM upload_logs WHERE lot_number = ?', (lot_number,))
        log_row = cur.fetchone()
        total_bol_cost = log_row['total_client_cost'] if log_row and log_row['total_client_cost'] else 0
        
        # Get items from raw_bol_items
        cur.execute('''
            SELECT 
                upc,
                item_description,
                quantity as original_qty,
                image_url,
                avg_cost
            FROM raw_bol_items 
            WHERE lot_number = ?
            ORDER BY id
        ''', (lot_number,))
        items = [dict(row) for row in cur.fetchall()]
        
        # Calculate total quantity for avg cost calculation
        total_qty = sum(item['original_qty'] or 0 for item in items)
        avg_cost = (total_bol_cost / total_qty) if total_qty > 0 and total_bol_cost else 0
        
        
        return jsonify({
            'success': True,
            'items': items,
            'total_bol_cost': total_bol_cost,
            'total_qty': total_qty,
            'avg_cost': avg_cost
        })
    except Exception as e:
        return jsonify({'success': False, 'error': ss_errors._safe_error(e)}), 500
    finally:
        if conn is not None:
            conn.close()
