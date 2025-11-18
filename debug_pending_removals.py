"""
Debug pending removals to see actual calculation
"""
import sqlite3
from datetime import datetime

conn = sqlite3.connect('sold.db')
conn.row_factory = sqlite3.Row
cur = conn.cursor()

# Get grace period
cur.execute("SELECT value FROM settings WHERE key = 'removal_grace_hours'")
row = cur.fetchone()
grace_hours = float(row[0]) if row else 48
print(f"Grace period: {grace_hours} hours ({grace_hours * 60} minutes)")

# Get pending items
cur.execute('''
    SELECT id, order_id, barcode, title, shipped_time, rackupdated
    FROM orders 
    WHERE rackupdated = 0 
    AND shipped_time IS NOT NULL 
    AND shipped_time != ''
    LIMIT 5
''')

now = datetime.now()
print(f"\nCurrent time: {now}")
print("\nPending removals:")
print("-" * 80)

for order in cur.fetchall():
    shipped_str = order['shipped_time']
    try:
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
        hours_remaining = max(0, grace_hours - hours_since)
        is_eligible = hours_since >= grace_hours
        
        print(f"\nOrder: {order['order_id']}")
        print(f"  Barcode: {order['barcode']}")
        print(f"  Title: {order['title'][:50] if order['title'] else 'N/A'}")
        print(f"  Shipped: {shipped_dt}")
        print(f"  Hours since shipped: {hours_since:.4f}")
        print(f"  Hours remaining: {hours_remaining:.4f}")
        print(f"  Eligible for removal: {is_eligible}")
        
    except Exception as e:
        print(f"\nOrder: {order['order_id']} - Error: {e}")

conn.close()
