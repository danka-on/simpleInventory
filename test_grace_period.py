"""
Test script to verify the 48-hour grace period for inventory reduction
"""
import sqlite3
import datetime

def test_grace_period():
    print("=" * 60)
    print("Testing 48-Hour Grace Period for Inventory Reduction")
    print("=" * 60)
    
    # Connect to sold.db
    conn = sqlite3.connect('sold.db')
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    
    # Check if removal_cancelled column exists
    cur.execute('PRAGMA table_info(orders)')
    cols = [r[1] for r in cur.fetchall()]
    print(f"\n✓ Orders table columns: {', '.join(cols)}")
    
    if 'removal_cancelled' in cols:
        print("✓ removal_cancelled column exists")
    else:
        print("⚠ removal_cancelled column does NOT exist (will be auto-created)")
    
    # Get all orders with shipped_time
    if 'removal_cancelled' in cols:
        cur.execute('''
            SELECT id, order_id, barcode, quantity, shipped_time, paid_time, rackupdated, removal_cancelled
            FROM orders 
            WHERE shipped_time IS NOT NULL AND shipped_time != ''
            ORDER BY shipped_time DESC
            LIMIT 10
        ''')
    else:
        cur.execute('''
            SELECT id, order_id, barcode, quantity, shipped_time, paid_time, rackupdated
            FROM orders 
            WHERE shipped_time IS NOT NULL AND shipped_time != ''
            ORDER BY shipped_time DESC
            LIMIT 10
        ''')
    shipped_orders = cur.fetchall()
    
    print(f"\n✓ Found {len(shipped_orders)} orders with shipping dates")
    
    if shipped_orders:
        print("\nShipped Orders (most recent 10):")
        print("-" * 60)
        
        now = datetime.datetime.utcnow()
        grace_period_hours = 48
        
        for order in shipped_orders:
            try:
                shipped_str = order['shipped_time']
                # Parse shipped time
                if 'T' in shipped_str:
                    if shipped_str.endswith('Z'):
                        shipped_dt = datetime.datetime.fromisoformat(shipped_str.replace('Z', '+00:00'))
                    elif '+' in shipped_str or shipped_str.count('-') > 2:
                        shipped_dt = datetime.datetime.fromisoformat(shipped_str)
                    else:
                        shipped_dt = datetime.datetime.fromisoformat(shipped_str)
                else:
                    shipped_dt = datetime.datetime.fromisoformat(shipped_str)
                
                if shipped_dt.tzinfo:
                    shipped_dt = shipped_dt.replace(tzinfo=None)
                
                hours_since_shipped = (now - shipped_dt).total_seconds() / 3600
                hours_remaining = max(0, grace_period_hours - hours_since_shipped)
                is_eligible = hours_since_shipped >= grace_period_hours
                
                status = "✓ ELIGIBLE" if is_eligible else f"⏰ {hours_remaining:.1f}h remaining"
                cancelled = "❌ CANCELLED" if ('removal_cancelled' in order.keys() and order['removal_cancelled'] == 1) else ""
                processed = "✓ PROCESSED" if order['rackupdated'] == 1 else "⏳ Pending"
                
                print(f"\nOrder: {order['order_id']}")
                print(f"  Barcode: {order['barcode']}")
                print(f"  Qty: {order['quantity'] or 1}")
                print(f"  Shipped: {shipped_str}")
                print(f"  Grace Period: {status}")
                print(f"  Processed: {processed}")
                if cancelled:
                    print(f"  Status: {cancelled}")
                    
            except Exception as e:
                print(f"\n⚠ Error parsing order {order['order_id']}: {e}")
    else:
        print("\n⚠ No orders with shipping dates found")
        print("   Orders need to be shipped before inventory reduction can occur")
    
    # Get unshipped orders with barcodes
    cur.execute('''
        SELECT COUNT(*) as count
        FROM orders 
        WHERE (shipped_time IS NULL OR shipped_time = '')
        AND barcode IS NOT NULL 
        AND barcode != ''
        AND rackupdated = 0
    ''')
    unshipped_count = cur.fetchone()['count']
    
    if unshipped_count > 0:
        print(f"\n⚠ {unshipped_count} orders have barcodes but NO shipping date")
        print("   These will NOT be processed until they are marked as shipped")
    
    conn.close()
    
    print("\n" + "=" * 60)
    print("Test complete!")
    print("=" * 60)
    print("\nKey Points:")
    print("1. Only SHIPPED orders are eligible for inventory reduction")
    print("2. 48-hour grace period from shipping date before removal")
    print("3. Users can cancel automatic removal from /searchrack UI")
    print("4. Cancelled orders won't reduce inventory automatically")
    print("=" * 60)

if __name__ == '__main__':
    test_grace_period()
