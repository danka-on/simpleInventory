"""Amazon orders for Sweet Shelves."""

import sqlite3
from flask import jsonify, request
from . import (
    caching as ss_caching, errors as ss_errors, integrations as ss_integrations, shipping_orders as
    ss_shipping_orders, warehouse_matching as ss_warehouse_matching,
)


def test_amazon_connection():
    """Test Amazon SP-API connection"""
    if not ss_integrations.AMAZON_AVAILABLE:
        return jsonify({'success': False, 'error': 'Amazon integration not available'}), 500
    
    try:
        amazon = ss_integrations.AmazonManager()
        success = amazon.test_connection()
        return jsonify({'success': success})
    except Exception as e:
        return jsonify({'success': False, 'error': ss_errors._safe_error(e)}), 500


def sync_amazon_orders():
    """Sync Amazon orders to sold.db"""
    if not ss_integrations.AMAZON_AVAILABLE:
        return jsonify({'success': False, 'error': 'Amazon integration not available'}), 500
    
    try:
        days = request.json.get('days', 30) if request.is_json else 30
        print(f"🔄 Syncing Amazon orders (last {days} days)...")
        
        amazon = ss_integrations.AmazonManager()
        count = amazon.sync_orders_to_db(days_back=days)
        
        # Process inventory reduction after fetching sold orders
        from DBmanager import process_sold_orders_inventory_reduction
        process_sold_orders_inventory_reduction()
        ss_shipping_orders._invalidate_ready_to_ship_cache()
        
        return jsonify({'success': True, 'orders_synced': count})
    except Exception as e:
        print(f"❌ Error syncing Amazon orders: {e}")
        return jsonify({'success': False, 'error': ss_errors._safe_error(e)}), 500


def sync_amazon_listings():
    """Sync Amazon active listings to amazonStore.db"""
    if not ss_integrations.AMAZON_AVAILABLE:
        return jsonify({'success': False, 'error': 'Amazon integration not available'}), 500
    
    try:
        print("🔄 Syncing Amazon listings...")
        
        amazon = ss_integrations.AmazonManager()
        count = amazon.sync_listings_to_db()
        
        return jsonify({'success': True, 'listings_synced': count})
    except Exception as e:
        print(f"❌ Error syncing Amazon listings: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': ss_errors._safe_error(e)}), 500


def repair_amazon_listing_titles():
    """Backfill blank Amazon listing titles without overwriting valid titles."""
    if not ss_integrations.AMAZON_AVAILABLE:
        return jsonify({'success': False, 'error': 'Amazon integration not available'}), 500
    try:
        data = request.get_json(silent=True) or {}
        max_catalog_items = max(0, min(100, int(data.get('max_catalog_items', 25))))
        amazon = ss_integrations.AmazonManager()
        result = amazon.repair_missing_listing_titles(max_catalog_items=max_catalog_items)
        ss_caching._listing_helper_scan_cache_clear()
        return jsonify({'success': True, **result})
    except Exception as e:
        return jsonify({'success': False, 'error': ss_errors._safe_error(e, 'amazon title repair')}), 500


def get_amazon_orders():
    """Get Amazon orders from sold.db"""
    conn = None
    try:
        days = int(request.args.get('days', 30))
        conn = sqlite3.connect('sold.db')
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        
        # Get orders from Amazon store
        cur.execute('''
            SELECT * FROM orders 
            WHERE store = 'amazon' 
            AND paid_time >= date('now', '-' || ? || ' days') 
            ORDER BY paid_time DESC
        ''', (days,))
        
        orders = cur.fetchall()
        order_payload = []
        for order in orders:
            order_dict = dict(order)
            effective_barcode = ss_warehouse_matching._effective_sold_order_barcode(order_dict)
            if effective_barcode:
                order_dict['stored_barcode'] = str(order_dict.get('barcode') or '').strip()
                order_dict['barcode'] = effective_barcode
            order_payload.append(order_dict)
        
        return jsonify({
            'success': True,
            'orders': order_payload
        })
    except Exception as e:
        return jsonify({'success': False, 'error': ss_errors._safe_error(e)}), 500
    finally:
        if conn is not None:
            conn.close()
