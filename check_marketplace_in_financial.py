#!/usr/bin/env python3
"""
Check if marketplace sales are in sold.db and would appear in financial analytics.
"""
import sqlite3

def check_marketplace_sales():
    """Check marketplace sales in sold.db"""
    conn = sqlite3.connect('sold.db')
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    
    # Check order counts by store
    print("=== Orders by Store ===")
    cur.execute("SELECT store, COUNT(*) as count FROM orders GROUP BY store")
    for row in cur.fetchall():
        print(f"{row['store']}: {row['count']} orders")
    
    print("\n=== Marketplace Sales Details ===")
    # Check marketplace orders specifically
    cur.execute("""
        SELECT 
            id, order_id, barcode, title, price, paid_time, store
        FROM orders 
        WHERE store = 'marketplace'
        ORDER BY paid_time DESC
        LIMIT 10
    """)
    
    marketplace_orders = cur.fetchall()
    if marketplace_orders:
        print(f"Found {len(marketplace_orders)} marketplace orders (showing first 10):")
        for order in marketplace_orders:
            print(f"  ID: {order['id']}, Barcode: {order['barcode']}, Price: ${order['price']:.2f}, Date: {order['paid_time']}")
    else:
        print("No marketplace orders found in sold.db")
    
    # Calculate marketplace totals
    cur.execute("""
        SELECT 
            COUNT(*) as count,
            SUM(price) as total_revenue
        FROM orders 
        WHERE store = 'marketplace' AND paid_time IS NOT NULL
    """)
    
    totals = cur.fetchone()
    if totals['count'] > 0:
        print(f"\n=== Marketplace Totals ===")
        print(f"Total Orders: {totals['count']}")
        print(f"Total Revenue: ${totals['total_revenue']:.2f}")
    
    conn.close()

if __name__ == '__main__':
    try:
        check_marketplace_sales()
    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()
