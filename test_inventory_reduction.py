#!/usr/bin/env python3
"""
Test script to verify sold orders inventory reduction functionality.
This will show what would happen when processing sold orders.
"""
import sqlite3

def test_inventory_reduction():
    print("=" * 70)
    print("TESTING SOLD ORDERS INVENTORY REDUCTION")
    print("=" * 70)
    
    # Check sold.db structure
    print("\n1. Checking sold.db structure...")
    sold_conn = sqlite3.connect('sold.db')
    sold_cur = sold_conn.cursor()
    
    # Check if rackupdated column exists
    sold_cur.execute('PRAGMA table_info(orders)')
    cols = [r[1] for r in sold_cur.fetchall()]
    print(f"   Columns in orders table: {cols}")
    
    if 'rackupdated' in cols:
        print("   ✓ rackupdated column exists")
    else:
        print("   ✗ rackupdated column missing - will be added automatically")
    
    # Count total orders
    sold_cur.execute('SELECT COUNT(*) FROM orders')
    total_orders = sold_cur.fetchone()[0]
    print(f"   Total orders in sold.db: {total_orders}")
    
    # Count orders with barcodes
    sold_cur.execute("SELECT COUNT(*) FROM orders WHERE barcode IS NOT NULL AND barcode != ''")
    orders_with_barcode = sold_cur.fetchone()[0]
    print(f"   Orders with barcodes: {orders_with_barcode}")
    
    # Count unprocessed orders
    if 'rackupdated' in cols:
        sold_cur.execute("SELECT COUNT(*) FROM orders WHERE rackupdated = 0 AND barcode IS NOT NULL AND barcode != ''")
        unprocessed = sold_cur.fetchone()[0]
        print(f"   Unprocessed orders (rackupdated=0): {unprocessed}")
    
    # Show sample of unprocessed orders
    print("\n2. Sample of orders to be processed:")
    sold_cur.execute('''
        SELECT id, barcode, quantity, order_id, title 
        FROM orders 
        WHERE barcode IS NOT NULL AND barcode != ''
        LIMIT 10
    ''')
    sample_orders = sold_cur.fetchall()
    
    for order in sample_orders:
        print(f"   Order #{order[0]}: Barcode={order[1]}, Qty={order[2]}, Title={order[4][:50]}...")
    
    # Check searchRack.db
    print("\n3. Checking searchRack.db...")
    rack_conn = sqlite3.connect('searchRack.db')
    rack_cur = rack_conn.cursor()
    
    # Get column info
    rack_cur.execute("PRAGMA table_info(SEARCHRACK)")
    rack_cols = [row[1] for row in rack_cur.fetchall()]
    qty_col = 'QUANTITY' if 'QUANTITY' in rack_cols else 'QTY'
    print(f"   Quantity column: {qty_col}")
    
    # Count items in searchRack
    rack_cur.execute('SELECT COUNT(*) FROM SEARCHRACK')
    total_rack_items = rack_cur.fetchone()[0]
    print(f"   Total items in searchRack: {total_rack_items}")
    
    # Check for matches
    print("\n4. Checking for matches between sold orders and searchRack...")
    matches = 0
    no_matches = 0
    
    for order in sample_orders:
        barcode = order[1]
        rack_cur.execute(f"SELECT ID, TITLE, {qty_col} FROM SEARCHRACK WHERE ITEMID = ? COLLATE NOCASE", (barcode,))
        rack_item = rack_cur.fetchone()
        
        if rack_item:
            print(f"   ✓ MATCH: {barcode} - Current qty: {rack_item[2]} (will reduce by {order[2]})")
            matches += 1
        else:
            print(f"   ✗ NO MATCH: {barcode}")
            no_matches += 1
    
    print(f"\n   Matches: {matches}/{len(sample_orders)}")
    print(f"   No matches: {no_matches}/{len(sample_orders)}")
    
    sold_conn.close()
    rack_conn.close()
    
    print("\n" + "=" * 70)
    print("TEST COMPLETE")
    print("=" * 70)
    print("\nTo actually process the orders, trigger 'Get Sold Orders' from the UI")
    print("or call the /get-sold-orders API endpoint.")

if __name__ == '__main__':
    test_inventory_reduction()
