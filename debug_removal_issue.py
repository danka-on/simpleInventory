"""
Debug why automatic inventory removal isn't working
"""
import sqlite3
from datetime import datetime

print("=" * 80)
print("DEBUGGING AUTOMATIC INVENTORY REMOVAL")
print("=" * 80)

# Check grace period setting
sold_conn = sqlite3.connect('sold.db')
sold_conn.row_factory = sqlite3.Row
sold_cur = sold_conn.cursor()

sold_cur.execute("SELECT value FROM settings WHERE key = 'removal_grace_hours'")
row = sold_cur.fetchone()
grace_hours = float(row[0]) if row else 48
print(f"\n✓ Grace period: {grace_hours} hours ({grace_hours * 60:.1f} minutes)")

# Get pending orders
sold_cur.execute('''
    SELECT id, order_id, barcode, quantity, title, shipped_time, rackupdated, removal_cancelled
    FROM orders 
    WHERE rackupdated = 0 
    AND shipped_time IS NOT NULL 
    AND shipped_time != ''
    AND barcode IS NOT NULL
    AND barcode != ''
    LIMIT 10
''')

orders = sold_cur.fetchall()
print(f"\n✓ Found {len(orders)} orders with shipped_time and not yet processed")

if not orders:
    print("\n❌ No orders found to process!")
    print("   Make sure you have orders with:")
    print("   - rackupdated = 0")
    print("   - shipped_time IS NOT NULL")
    print("   - barcode IS NOT NULL")
    sold_conn.close()
    exit()

now = datetime.now()
print(f"\n✓ Current time: {now}")

# Check searchRack connection
searchrack_conn = sqlite3.connect('searchRack.db')
searchrack_cur = searchrack_conn.cursor()

print("\n" + "=" * 80)
print("CHECKING EACH ORDER:")
print("=" * 80)

for order in orders:
    print(f"\n📦 Order ID: {order['id']} ({order['order_id']})")
    print(f"   Barcode: {order['barcode']}")
    print(f"   Title: {(order['title'] or 'N/A')[:50]}")
    print(f"   Quantity: {order['quantity']}")
    print(f"   Shipped time: {order['shipped_time']}")
    print(f"   Rack updated: {order['rackupdated']}")
    print(f"   Removal cancelled: {order['removal_cancelled']}")
    
    # Check if eligible
    try:
        shipped_str = order['shipped_time']
        if 'T' in shipped_str:
            if shipped_str.endswith('Z'):
                shipped_dt = datetime.fromisoformat(shipped_str.replace('Z', '+00:00'))
            else:
                shipped_dt = datetime.fromisoformat(shipped_str)
        else:
            shipped_dt = datetime.fromisoformat(shipped_str)
        
        if shipped_dt.tzinfo:
            shipped_dt = shipped_dt.replace(tzinfo=None)
        
        hours_since = (now - shipped_dt).total_seconds() / 3600
        print(f"   ⏰ Hours since shipped: {hours_since:.4f}")
        print(f"   ⏰ Grace period: {grace_hours:.4f}")
        
        if hours_since >= grace_hours:
            print(f"   ✅ ELIGIBLE for removal (past grace period)")
        else:
            print(f"   ⏳ NOT ELIGIBLE yet ({grace_hours - hours_since:.4f} hours remaining)")
            continue
            
    except Exception as e:
        print(f"   ❌ Error parsing shipped_time: {e}")
        continue
    
    # Check searchRack
    barcode = order['barcode']
    barcode_padded = barcode.zfill(12) if barcode and barcode.isdigit() else barcode
    print(f"   🔍 Looking up in searchRack with barcode: {barcode_padded}")
    
    searchrack_cur.execute('SELECT ID, QUANTITY, ITEM_POSITION FROM SEARCHRACK WHERE BARCODE = ?', (barcode_padded,))
    item_row = searchrack_cur.fetchone()
    
    if not item_row:
        print(f"   ❌ NOT FOUND in searchRack!")
        # Try without padding
        searchrack_cur.execute('SELECT ID, QUANTITY, ITEM_POSITION, BARCODE FROM SEARCHRACK WHERE BARCODE = ?', (barcode,))
        item_row2 = searchrack_cur.fetchone()
        if item_row2:
            print(f"   ⚠️  Found with original barcode (no padding): {item_row2}")
        else:
            print(f"   ℹ️  Item truly not in searchRack")
    else:
        item_id, current_qty, position = item_row
        print(f"   ✅ FOUND in searchRack:")
        print(f"      - ID: {item_id}")
        print(f"      - Current quantity: {current_qty}")
        print(f"      - Position: {position}")
        print(f"      - Would reduce to: {max(0, (current_qty or 0) - (order['quantity'] or 1))}")

searchrack_conn.close()
sold_conn.close()

print("\n" + "=" * 80)
print("DEBUG COMPLETE")
print("=" * 80)
