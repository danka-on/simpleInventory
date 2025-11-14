#!/usr/bin/env python3
"""
Test the smart LOT matching logic.
"""
import sqlite3
from datetime import datetime
from lot_matcher import match_sold_item_to_lot, backfill_all_sold_orders

def test_lot_matching():
    """Test LOT matching with sample data"""
    print("=== Testing LOT Matching Logic ===\n")
    
    # Get some real sold items to test
    sold_conn = sqlite3.connect('sold.db')
    sold_conn.row_factory = sqlite3.Row
    sold_cur = sold_conn.cursor()
    
    # Get a few sold items with barcodes
    sold_cur.execute('''
        SELECT id, barcode, paid_time, lot_number
        FROM orders
        WHERE barcode IS NOT NULL
        AND barcode != ''
        AND paid_time IS NOT NULL
        ORDER BY paid_time DESC
        LIMIT 10
    ''')
    
    test_orders = sold_cur.fetchall()
    sold_conn.close()
    
    print(f"Testing with {len(test_orders)} recent orders:\n")
    
    for order in test_orders:
        order_id = order['id']
        barcode = order['barcode']
        paid_time = order['paid_time']
        current_lot = order['lot_number']
        
        # Try to match
        matched_lot = match_sold_item_to_lot(barcode, paid_time)
        
        status = "✅ MATCH" if matched_lot else "❌ NO MATCH"
        print(f"{status}")
        print(f"  Order ID: {order_id}")
        print(f"  UPC: {barcode}")
        print(f"  Sold Date: {paid_time}")
        print(f"  Current LOT: {current_lot or 'None'}")
        print(f"  Matched LOT: {matched_lot or 'None'}")
        
        if matched_lot and current_lot and matched_lot != current_lot:
            print(f"  ⚠️  LOT would change from '{current_lot}' to '{matched_lot}'")
        
        print()

def check_rawbol_lots():
    """Check available LOTs in rawbol.db"""
    print("\n=== Available LOTs in rawbol.db ===\n")
    
    conn = sqlite3.connect('rawbol.db')
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    
    cur.execute('''
        SELECT 
            lot_number,
            import_date,
            COUNT(*) as item_count,
            SUM(quantity) as total_quantity
        FROM raw_bol_items
        WHERE lot_number IS NOT NULL
        AND lot_number != ''
        GROUP BY lot_number, import_date
        ORDER BY import_date DESC
    ''')
    
    lots = cur.fetchall()
    conn.close()
    
    print(f"Total LOTs: {len(lots)}\n")
    
    for lot in lots[:10]:  # Show first 10
        print(f"LOT: {lot['lot_number']}")
        print(f"  Date: {lot['import_date']}")
        print(f"  Items: {lot['item_count']}")
        print(f"  Total Qty: {lot['total_quantity']}")
        print()

def preview_backfill():
    """Preview what backfill would do without actually doing it"""
    print("\n=== Backfill Preview ===\n")
    
    sold_conn = sqlite3.connect('sold.db')
    sold_conn.row_factory = sqlite3.Row
    sold_cur = sold_conn.cursor()
    
    # Count orders without LOT numbers
    sold_cur.execute('''
        SELECT COUNT(*) as count
        FROM orders
        WHERE paid_time IS NOT NULL
        AND barcode IS NOT NULL
        AND barcode != ''
        AND (lot_number IS NULL OR lot_number = '')
    ''')
    
    no_lot_count = sold_cur.fetchone()['count']
    
    # Count orders with LOT numbers
    sold_cur.execute('''
        SELECT COUNT(*) as count
        FROM orders
        WHERE paid_time IS NOT NULL
        AND lot_number IS NOT NULL
        AND lot_number != ''
    ''')
    
    has_lot_count = sold_cur.fetchone()['count']
    
    # Total sold orders
    sold_cur.execute('''
        SELECT COUNT(*) as count
        FROM orders
        WHERE paid_time IS NOT NULL
    ''')
    
    total_count = sold_cur.fetchone()['count']
    
    sold_conn.close()
    
    print(f"Total Sold Orders: {total_count}")
    print(f"Orders WITH LOT #: {has_lot_count} ({has_lot_count/total_count*100:.1f}%)")
    print(f"Orders WITHOUT LOT #: {no_lot_count} ({no_lot_count/total_count*100:.1f}%)")
    print(f"\nBackfill would process: {total_count} orders")

if __name__ == '__main__':
    try:
        check_rawbol_lots()
        preview_backfill()
        test_lot_matching()
        
        print("\n" + "="*50)
        print("Test complete! Run backfill from BOL Stats page or use:")
        print("  python -c \"from lot_matcher import backfill_all_sold_orders; backfill_all_sold_orders()\"")
        print("="*50)
        
    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()
