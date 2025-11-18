import sqlite3

conn = sqlite3.connect('sold.db')
conn.row_factory = sqlite3.Row
cur = conn.cursor()

cur.execute('''
    SELECT id, order_id, barcode, title, quantity, shipped_time, rackupdated
    FROM orders 
    WHERE rackupdated = 0 
    AND shipped_time IS NOT NULL 
    ORDER BY id DESC
    LIMIT 5
''')

print("Recent unprocessed orders:")
print("-" * 80)
for order in cur.fetchall():
    print(f"ID: {order['id']}")
    print(f"  Order ID: {order['order_id']}")
    print(f"  Barcode: '{order['barcode']}'")
    print(f"  Title: {order['title']}")
    print(f"  Quantity: {order['quantity']}")
    print(f"  Shipped: {order['shipped_time']}")
    print()

conn.close()
