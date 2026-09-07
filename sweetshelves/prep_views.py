"""Prep views for Sweet Shelves."""

import sqlite3
from flask import jsonify, render_template, request, session
from . import (
    errors as ss_errors, listing_lifecycle as ss_listing_lifecycle, normalization as ss_normalization,
    prep_schema as ss_prep_schema, warehouse_allocations as ss_warehouse_allocations,
)


def item_prep_page():
    return render_template('item_prep.html')


def item_prep_log_page():
    return render_template('item_prep_log.html')


def item_prep_diagnostic_page():
    upc_raw = (request.args.get('upc') or '').strip()
    upc = ss_normalization._normalize_upc_preserve_suffix_for_match(upc_raw)
    
    # If coming from Item Manager with lot parameter, set it in session
    lot = request.args.get('lot', '').strip()
    if lot:
        session['selected_lot'] = lot
        print(f'[DEBUG] Set selected_lot in session: {lot}')
    
    print(f'[DEBUG] Diagnostic page - URL param: {upc_raw} -> Rendered UPC: {upc}')
    return render_template('item_prep_diagnostic.html', upc=upc)


def item_prep_diagnostic_view_page():
    upc_raw = request.args.get('upc', '').strip()
    upc = ss_normalization._normalize_upc_preserve_suffix_for_match(upc_raw)
    selected_status = ss_normalization._normalize_prep_row_status(request.args.get('row_status') or request.args.get('status'))
    
    # If coming from Item Manager with lot parameter, set it in session
    lot = request.args.get('lot', '').strip()
    if lot:
        session['selected_lot'] = lot
        print(f'[DEBUG] Set selected_lot in session: {lot}')
    
    # Load status, images, and (optionally) bol item details
    status = None
    images = []
    bol = None
    selected_lot = ss_normalization._normalize_lot_number(request.args.get('lot') or '')
    all_lots_mode = ss_warehouse_allocations._request_lot_scope_is_all()
    lot_rows = []
    show_top_reasons = True
    conn = None
    try:
        ss_prep_schema._ensure_items_prep_tables()
        ss_listing_lifecycle._ensure_bol_list_status_column()
        conn = sqlite3.connect('bol.db', isolation_level='IMMEDIATE')
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()

        def _diag_lot_key(value):
            lot_value = ss_normalization._normalize_lot_number(value)
            return lot_value.lower() if lot_value else '__lotless__'

        def _diag_lot_label(value):
            lot_value = ss_normalization._normalize_lot_number(value)
            return lot_value or 'LOTLESS'

        selected_lot = ss_warehouse_allocations._preferred_lot_from_request()
        asset_scope_upc, asset_scope_status = ss_normalization._items_to_list_asset_scope(upc, selected_status)
        if selected_status or ('-' in asset_scope_upc):
            cur.execute('''
                SELECT id, image_path, created_at, COALESCE(row_status, '') AS row_status
                FROM items_prep_images
                WHERE upc = ? COLLATE NOCASE
                  AND COALESCE(row_status, '') = ? COLLATE NOCASE
                  AND (deleted_at IS NULL OR TRIM(COALESCE(deleted_at,'')) = '')
                ORDER BY created_at DESC, id DESC
            ''', (asset_scope_upc, asset_scope_status))
        else:
            cur.execute('''
                SELECT id, image_path, created_at, COALESCE(row_status, '') AS row_status
                FROM items_prep_images
                WHERE upc = ? COLLATE NOCASE
                  AND (deleted_at IS NULL OR TRIM(COALESCE(deleted_at,'')) = '')
                ORDER BY created_at DESC, id DESC
            ''', (asset_scope_upc,))
        images = [dict(r) for r in cur.fetchall()]
        if all_lots_mode:
            cur.execute('''
                SELECT id, upc, item_description, image_url, lot_number, bol_number, import_date, list_status, quantity,
                       listed_amazon, listed_amazon_date, listed_amazon_source,
                       listed_ebay, listed_ebay_date, listed_ebay_source,
                       listed_facebook, listed_facebook_date, listed_facebook_source
                FROM bol_items
                WHERE upc = ? COLLATE NOCASE
                ORDER BY import_date DESC, id DESC
            ''', (upc,))
            bol_rows_all = [dict(r) for r in cur.fetchall()]

            cur.execute('''
                SELECT upc, COALESCE(lot_number, '') AS lot_number, status, reason, note, updated_at, quantity
                FROM items_prep_status
                WHERE upc = ? COLLATE NOCASE
                ORDER BY COALESCE(updated_at, '') DESC, rowid DESC
            ''', (upc,))
            status_rows_all = [dict(r) for r in cur.fetchall()]

            status_by_lot = {}
            for raw_row in status_rows_all:
                key = _diag_lot_key(raw_row.get('lot_number'))
                if key in status_by_lot:
                    continue
                row_status_value = ss_normalization._normalize_prep_row_status(raw_row.get('status'))
                if selected_status and row_status_value and row_status_value != selected_status:
                    continue
                status_by_lot[key] = raw_row

            bol_by_lot = {}
            lot_order = []
            for raw_row in bol_rows_all:
                key = _diag_lot_key(raw_row.get('lot_number'))
                if key in bol_by_lot:
                    continue
                status_row = status_by_lot.get(key)
                if selected_status and status_row:
                    row_status_value = ss_normalization._normalize_prep_row_status(status_row.get('status'))
                    if row_status_value and row_status_value != selected_status:
                        continue
                bol_by_lot[key] = dict(raw_row)
                lot_order.append(key)

            for key in status_by_lot:
                if key not in bol_by_lot:
                    lot_order.append(key)

            for key in lot_order:
                bol_row = dict(bol_by_lot.get(key) or {})
                status_row = dict(status_by_lot.get(key) or {})
                lot_value = ss_normalization._normalize_lot_number(status_row.get('lot_number') or bol_row.get('lot_number'))
                qty_source = status_row.get('quantity')
                if qty_source is None or str(qty_source).strip() == '':
                    qty_value = max(0, ss_normalization._coerce_int(bol_row.get('quantity'), 0))
                else:
                    qty_value = max(0, ss_normalization._coerce_int(qty_source, 0))
                lot_rows.append({
                    'id': bol_row.get('id'),
                    'upc': bol_row.get('upc') or upc,
                    'item_description': bol_row.get('item_description') or '',
                    'image_url': bol_row.get('image_url') or '',
                    'lot_number': lot_value,
                    'lot_label': _diag_lot_label(lot_value),
                    'bol_number': bol_row.get('bol_number') or '',
                    'import_date': bol_row.get('import_date') or '',
                    'list_status': bol_row.get('list_status') or '',
                    'quantity': qty_value,
                    'status': ss_normalization._normalize_prep_row_status(status_row.get('status') or selected_status or ''),
                    'reason': str(status_row.get('reason') or '').strip(),
                    'note': str(status_row.get('note') or '').strip(),
                    'updated_at': str(status_row.get('updated_at') or '').strip(),
                    'listed_amazon': bol_row.get('listed_amazon'),
                    'listed_amazon_date': bol_row.get('listed_amazon_date'),
                    'listed_amazon_source': bol_row.get('listed_amazon_source'),
                    'listed_ebay': bol_row.get('listed_ebay'),
                    'listed_ebay_date': bol_row.get('listed_ebay_date'),
                    'listed_ebay_source': bol_row.get('listed_ebay_source'),
                    'listed_facebook': bol_row.get('listed_facebook'),
                    'listed_facebook_date': bol_row.get('listed_facebook_date'),
                    'listed_facebook_source': bol_row.get('listed_facebook_source')
                })

            ss_listing_lifecycle._apply_marketplace_status_overlay(lot_rows)

            total_qty = sum(max(0, ss_normalization._coerce_int(entry.get('quantity'), 0)) for entry in lot_rows)
            latest_updated = max((str(entry.get('updated_at') or '') for entry in lot_rows), default='')
            unique_reasons = []
            seen_reasons = set()
            for entry in lot_rows:
                reason_text = str(entry.get('reason') or '').strip()
                if not reason_text:
                    continue
                reason_key = reason_text.lower()
                if reason_key in seen_reasons:
                    continue
                seen_reasons.add(reason_key)
                unique_reasons.append(reason_text)

            if bol_rows_all:
                bol = dict(bol_rows_all[0])
            elif lot_rows:
                bol = {
                    'id': None,
                    'upc': upc,
                    'item_description': lot_rows[0].get('item_description') or '',
                    'image_url': lot_rows[0].get('image_url') or '',
                    'lot_number': '',
                    'bol_number': '',
                    'import_date': lot_rows[0].get('import_date') or '',
                    'list_status': lot_rows[0].get('list_status') or '',
                    'quantity': total_qty
                }
            if bol:
                bol['quantity'] = total_qty or max(0, ss_normalization._coerce_int(bol.get('quantity'), 0)) or 1
                bol['lot_number'] = ''
                ss_listing_lifecycle._apply_marketplace_status_overlay([bol])

            if lot_rows:
                first_status = next((str(entry.get('status') or '').strip() for entry in lot_rows if str(entry.get('status') or '').strip()), '')
                status = {
                    'status': selected_status or first_status,
                    'reason': unique_reasons[0] if len(unique_reasons) == 1 else '',
                    'note': '',
                    'updated_at': latest_updated,
                    'quantity': total_qty,
                    'lot_number': ''
                }
                show_top_reasons = len(unique_reasons) <= 1
            else:
                show_top_reasons = False
        else:
            row = ss_warehouse_allocations._select_prep_status_row(cur, upc, selected_lot, columns='status, reason, note, updated_at, quantity, lot_number')
            status = dict(row) if row else None
            b = None
            if selected_lot:
                cur.execute('''
                    SELECT id, upc, item_description, image_url, lot_number, bol_number, import_date, list_status, quantity,
                           listed_amazon, listed_amazon_date, listed_amazon_source,
                           listed_ebay, listed_ebay_date, listed_ebay_source,
                           listed_facebook, listed_facebook_date, listed_facebook_source
                    FROM bol_items
                    WHERE upc = ? COLLATE NOCASE
                      AND lot_number = ? COLLATE NOCASE
                    ORDER BY import_date DESC, id DESC
                    LIMIT 1
                ''', (upc, selected_lot))
                b = cur.fetchone()
                if not b:
                    cur.execute('''
                        SELECT id, upc, item_description, image_url, lot_number, bol_number, import_date, list_status, quantity,
                               listed_amazon, listed_amazon_date, listed_amazon_source,
                               listed_ebay, listed_ebay_date, listed_ebay_source,
                               listed_facebook, listed_facebook_date, listed_facebook_source
                        FROM bol_items
                        WHERE upc = ? COLLATE NOCASE
                        ORDER BY import_date DESC, id DESC
                        LIMIT 1
                    ''', (upc,))
                    b = cur.fetchone()
            else:
                cur.execute('''
                    SELECT id, upc, item_description, image_url, lot_number, bol_number, import_date, list_status, quantity,
                           listed_amazon, listed_amazon_date, listed_amazon_source,
                           listed_ebay, listed_ebay_date, listed_ebay_source,
                           listed_facebook, listed_facebook_date, listed_facebook_source
                    FROM bol_items 
                    WHERE upc = ? COLLATE NOCASE
                    ORDER BY import_date DESC, id DESC
                    LIMIT 1
                ''', (upc,))
                b = cur.fetchone()
            bol = dict(b) if b else None
            
            # If item has prep status with quantity, use that instead of bol quantity
            if bol and status and status.get('quantity') is not None:
                bol['quantity'] = status['quantity']
            if bol:
                ss_listing_lifecycle._apply_marketplace_status_overlay([bol])
        
    except Exception as e:
        print('Diagnostic view load error:', e)
    finally:
        if conn is not None:
            conn.close()
    return render_template(
        'item_prep_diagnostic_view.html',
        upc=upc,
        status=status,
        images=images,
        bol=bol,
        selected_lot=selected_lot,
        selected_status=selected_status,
        all_lots_mode=all_lots_mode,
        lot_rows=lot_rows,
        show_top_reasons=show_top_reasons
    )


def item_prep_no_barcode_page():
    """Page for searching items by name when there's no barcode"""
    return render_template('item_prep_no_barcode.html')


def search_rawbol_api():
    """Search rawbol.db by item description OR UPC with pagination"""
    conn = None
    try:
        data = request.get_json()
        query = data.get('query', '').strip()
        page = int(data.get('page', 1))
        per_page = 3  # Show only 3 items per page
        offset = (page - 1) * per_page
        
        if not query:
            return jsonify({'success': False, 'error': 'No search query provided'}), 400
        
        # Search rawbol.db
        conn = sqlite3.connect('rawbol.db')
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        
        # Check if query looks like a UPC (numeric)
        is_upc_query = query.isdigit()
        
        if is_upc_query:
            # Search by UPC (exact or partial match)
            search_pattern = f"%{query}%"
            
            # Get total count for pagination
            cur.execute('''
                SELECT COUNT(*) as total
                FROM raw_bol_items 
                WHERE upc LIKE ? COLLATE NOCASE
            ''', (search_pattern,))
            total = cur.fetchone()['total']
            
            # Get paginated results
            cur.execute('''
                SELECT upc, item_description, image_url 
                FROM raw_bol_items 
                WHERE upc LIKE ? COLLATE NOCASE
                LIMIT ? OFFSET ?
            ''', (search_pattern, per_page, offset))
        else:
            # Search by item description
            search_pattern = f"%{query}%"
            
            # Get total count for pagination
            cur.execute('''
                SELECT COUNT(*) as total
                FROM raw_bol_items 
                WHERE item_description LIKE ? COLLATE NOCASE
            ''', (search_pattern,))
            total = cur.fetchone()['total']
            
            # Get paginated results
            cur.execute('''
                SELECT upc, item_description, image_url 
                FROM raw_bol_items 
                WHERE item_description LIKE ? COLLATE NOCASE
                LIMIT ? OFFSET ?
            ''', (search_pattern, per_page, offset))
        
        results = [dict(row) for row in cur.fetchall()]
        
        total_pages = (total + per_page - 1) // per_page  # Ceiling division
        
        return jsonify({
            'success': True, 
            'results': results,
            'page': page,
            'per_page': per_page,
            'total': total,
            'total_pages': total_pages
        })
    except Exception as e:
        print(f'Error searching rawbol: {e}')
        return jsonify({'success': False, 'error': ss_errors._safe_error(e)}), 500
    finally:
        if conn is not None:
            conn.close()
