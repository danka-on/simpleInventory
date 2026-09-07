"""Diagnostics for Sweet Shelves."""

import json
import sqlite3
from DBmanager import ensure_sold_orders_schema
from flask import jsonify, render_template, request
from . import (
    caching as ss_caching, errors as ss_errors, listing_lifecycle as ss_listing_lifecycle, normalization
    as ss_normalization, prep_media as ss_prep_media, shipping_identity as ss_shipping_identity,
)


@ss_errors.require_debug_mode
def api_debug_migrate_listings():
    """
    Legacy migration endpoint.
    By default this is read-only and does not run migration.
    Add `?force=1` to run the old list_status -> listed_amazon backfill once.
    """
    conn = None
    try:
        ss_listing_lifecycle._ensure_bol_list_status_column()
        force = str(request.args.get('force') or '').strip().lower() in ('1', 'true', 'yes', 'on')

        conn = sqlite3.connect('bol.db')
        cur = conn.cursor()
        migrated = 0
        if force:
            migrated = ss_listing_lifecycle._legacy_migrate_list_status_to_amazon(cur)
            conn.commit()
            ss_caching.update_data_version()

        cur.execute("SELECT COUNT(*) FROM bol_items WHERE listed_amazon = 1")
        amazon_count = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM bol_items WHERE list_status = 'listed'")
        legacy_count = cur.fetchone()[0]
        
        return jsonify({
            'success': True, 
            'message': (
                f'Legacy migration ran: migrated={migrated}.'
                if force else
                'Legacy migration is disabled by default. Use ?force=1 to run it explicitly.'
            ),
            'forced': force,
            'migrated': migrated,
            'amazon_listed_count': amazon_count,
            'legacy_listed_count': legacy_count
        })
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': ss_errors._safe_error(e)}), 500
    finally:
        if conn is not None:
            conn.close()


@ss_errors.require_debug_mode
def api_debug_check_item(upc):
    """Check the current marketplace state of a specific item."""
    conn = None
    try:
        upc = ss_normalization._normalize_upc(upc)
        conn = sqlite3.connect('bol.db')
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute('''
            SELECT upc, list_status, listed_amazon, listed_amazon_date, 
                   listed_ebay, listed_ebay_date, listed_facebook, listed_facebook_date
            FROM bol_items WHERE upc = ? COLLATE NOCASE
        ''', (upc,))
        row = cur.fetchone()
        
        if not row:
            return jsonify({'success': False, 'error': 'Item not found'}), 404
        
        return jsonify({
            'success': True,
            'upc': row['upc'],
            'list_status': row['list_status'],
            'listed_amazon': row['listed_amazon'],
            'listed_amazon_date': row['listed_amazon_date'],
            'listed_ebay': row['listed_ebay'],
            'listed_ebay_date': row['listed_ebay_date'],
            'listed_facebook': row['listed_facebook'],
            'listed_facebook_date': row['listed_facebook_date']
        })
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': ss_errors._safe_error(e)}), 500
    finally:
        if conn is not None:
            conn.close()


def api_cleanup_temporary_entry():
    """Delete a temporary bol_items entry that was created but not completed."""
    conn = None
    try:
        # Handle both JSON and form data (sendBeacon can send either)
        if request.is_json:
            data = request.get_json() or {}
        else:
            # Try to parse as JSON from raw data
            try:
                data = json.loads(request.data.decode('utf-8'))
            except Exception:
                data = {}
        
        upc = ss_normalization._normalize_upc(data.get('upc'))
        if not upc:
            print('[CLEANUP] No UPC provided')
            return jsonify({'success': False, 'error': 'Missing upc'}), 400
        
        print(f'[CLEANUP] Attempting to delete temporary entry: {upc}')
        
        conn = sqlite3.connect('bol.db')
        cur = conn.cursor()
        # Only delete if it's marked as temporary
        cur.execute('DELETE FROM bol_items WHERE upc = ? COLLATE NOCASE AND temporary = 1', (upc,))
        deleted = cur.rowcount
        if deleted:
            # Keep prep status in sync when a temporary BAD entry is abandoned.
            cur.execute('DELETE FROM items_prep_status WHERE upc = ? COLLATE NOCASE', (upc,))
            ss_prep_media._items_prep_delete_diagnostic_assets(cur, upc)
        conn.commit()
        
        print(f'[CLEANUP] Deleted {deleted} temporary entries for UPC: {upc}')
        return jsonify({'success': True, 'deleted': deleted})
    except Exception as e:
        print(f'[CLEANUP] Error: {e}')
        return jsonify({'success': False, 'error': ss_errors._safe_error(e)}), 500
    finally:
        if conn is not None:
            conn.close()


def test_sold_orders_page():
    """Test sold orders page for creating test orders."""
    return render_template('test_sold_orders.html')


def api_create_test_sold_order():
    """Create a test order in sold.db for testing purposes."""
    conn = None
    try:
        data = request.get_json() or {}

        # Validate required fields
        for field in ['barcode', 'title']:
            if not data.get(field):
                return jsonify({'success': False, 'error': f'Missing required field: {field}'}), 400

        barcode = ss_normalization._normalize_upc(data.get('barcode', '').strip())
        title   = data.get('title', '').strip()
        store   = (data.get('store') or 'test').strip().lower()
        try:
            quantity = max(1, int(data.get('quantity', 1)))
        except (ValueError, TypeError):
            quantity = 1

        def _f(key, default=0.0):
            try:
                v = float(data.get(key, default))
                return max(0.0, v)
            except (ValueError, TypeError):
                return float(default)

        price         = _f('price', 10.0)
        seller_fee    = _f('seller_fee', 0.0)
        taxes         = _f('taxes', 0.0)
        shipping_cost = _f('shipping_cost', 0.0)

        # Use provided order_id/item_id if given, otherwise generate realistic ones
        import random, time as _time
        order_id = (data.get('order_id') or '').strip()
        item_id  = (data.get('item_id') or '').strip()
        if not order_id:
            if store == 'amazon':
                order_id = f"114-{random.randint(1000000,9999999)}-{random.randint(1000000,9999999)}"
            elif store == 'ebay':
                order_id = f"{random.randint(10,99)}-{random.randint(10000,99999)}-{random.randint(10000,99999)}"
            else:
                order_id = f"TEST-{int(_time.time())}-{random.randint(1000,9999)}"
        if not item_id:
            item_id = order_id

        sku              = (data.get('sku') or '').strip()
        checkout_status  = (data.get('checkout_status') or 'PAID').strip()
        shipping_name    = (data.get('shipping_name') or ('Amazon Buyer' if store == 'amazon' else 'Test Customer')).strip()
        shipping_street1 = (data.get('shipping_street1') or '').strip()
        shipping_street2 = (data.get('shipping_street2') or '').strip()
        shipping_city    = (data.get('shipping_city') or '').strip()
        shipping_state   = (data.get('shipping_state') or '').strip()
        shipping_postal  = (data.get('shipping_postal_code') or '').strip()
        shipping_country = (data.get('shipping_country') or 'US').strip().upper() or 'US'
        set_shipped      = bool(data.get('set_shipped'))
        exact_trace_mode = bool(data.get('exact_trace_mode')) or ('-' in barcode)
        source_upc_input = (data.get('source_upc') or barcode or '').strip()
        source_upc = ss_normalization._normalize_upc_preserve_suffix_for_match(source_upc_input)
        if exact_trace_mode and not source_upc:
            source_upc = barcode
        source_base_upc = ss_shipping_identity._barcode_base_without_suffix(source_upc)
        listing_trace_source = (data.get('listing_trace_source') or '').strip().lower()
        if exact_trace_mode and not listing_trace_source:
            listing_trace_source = 'listingagent'
        listing_trace_id = (data.get('listing_trace_id') or '').strip()
        if exact_trace_mode and not listing_trace_id:
            import time as _time
            listing_trace_id = f"TEST-LISTING-{int(_time.time())}-{random.randint(1000,9999)}"
        listing_listing_id = (data.get('listing_listing_id') or '').strip()
        listing_offer_id = (data.get('listing_offer_id') or '').strip()
        listing_sku = (data.get('listing_sku') or sku or '').strip()
        listing_asin = (data.get('listing_asin') or item_id or '').strip()
        if exact_trace_mode and source_upc and source_upc != barcode:
            barcode = source_upc

        import datetime
        paid_time     = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        shipped_time  = paid_time if set_shipped else None

        conn = sqlite3.connect('sold.db')
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        ensure_sold_orders_schema(cur, conn, default_store=store)

        cur.execute('''
            INSERT INTO orders (
                order_id, item_id, sku, title, quantity, price,
                checkout_status,
                shipping_name, shipping_street1, shipping_street2,
                shipping_city, shipping_state, shipping_postal_code, shipping_country,
                paid_time, shipped_time,
                seller_fee, taxes, shipping_cost,
                barcode, source_upc, source_base_upc, listing_trace_id, listing_trace_created_at,
                listing_trace_source, listing_listing_id, listing_offer_id, listing_sku, listing_asin,
                store, isHandled, rackupdated
            ) VALUES (?,?,?,?,?,?, ?,  ?,?,?,?,?,?,?,  ?,?,  ?,?,?,  ?,?,?,?,?,?,?,?,?,?,?, '',0)
        ''', (
            order_id, item_id, sku, title, quantity, price,
            checkout_status,
            shipping_name, shipping_street1, shipping_street2,
            shipping_city, shipping_state, shipping_postal, shipping_country,
            paid_time, shipped_time,
            seller_fee, taxes, shipping_cost,
            barcode, source_upc, source_base_upc, listing_trace_id, paid_time if listing_trace_id else '',
            listing_trace_source, listing_listing_id, listing_offer_id, listing_sku, listing_asin,
            store
        ))

        conn.commit()
        inserted_id = cur.lastrowid
        exact_traced = ss_shipping_identity._is_exact_traced_suffixed_sold_order({
            'source_upc': source_upc,
            'listing_trace_source': listing_trace_source,
            'listing_trace_id': listing_trace_id
        })

        return jsonify({
            'success': True,
            'order_id': order_id,
            'id': inserted_id,
            'message': 'Test order created successfully',
            'barcode': barcode,
            'source_upc': source_upc,
            'source_base_upc': source_base_upc,
            'listing_trace_id': listing_trace_id,
            'listing_trace_source': listing_trace_source,
            'exact_traced': exact_traced
        })

    except Exception as e:
        return jsonify({'success': False, 'error': ss_errors._safe_error(e)}), 500
    finally:
        if conn:
            conn.close()
