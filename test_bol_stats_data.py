"""
Test BOL Statistics APIs
"""
import sqlite3
import json

print("=== Testing BOL Statistics Data Sources ===\n")

# Test 1: rawbol logs
print("1. Testing /api/rawbol/logs data:")
conn = sqlite3.connect('rawbol.db')
conn.row_factory = sqlite3.Row
cur = conn.cursor()
cur.execute('SELECT lot_number, import_date, total_client_cost, rows_imported FROM upload_logs ORDER BY import_date DESC')
logs = [dict(row) for row in cur.fetchall()]
print(f"   Found {len(logs)} LOT entries")
for log in logs[:3]:
    print(f"   - LOT {log['lot_number']}: ${log['total_client_cost']:.2f}, {log['rows_imported']} items, {log['import_date']}")
conn.close()

# Test 2: sold orders with bol_number
print("\n2. Testing sold orders with bol_number:")
conn = sqlite3.connect('sold.db')
conn.row_factory = sqlite3.Row
cur = conn.cursor()
cur.execute('SELECT COUNT(*) as total FROM orders')
total = cur.fetchone()['total']
cur.execute('SELECT COUNT(*) as with_bol FROM orders WHERE lot_number IS NOT NULL AND lot_number != ""')
with_bol = cur.fetchone()['with_bol']
print(f"   Total orders: {total}")
print(f"   Orders with LOT#: {with_bol}")

cur.execute('SELECT order_id, lot_number, price, barcode FROM orders WHERE lot_number IS NOT NULL AND lot_number != "" LIMIT 3')
orders = cur.fetchall()
print(f"   Sample orders with LOT#:")
for order in orders:
    print(f"   - Order {order['order_id']}: LOT# {order['lot_number']}, Price ${order['price']}")
conn.close()

# Test 3: Check if financial analytics would work
print("\n3. Simulating financial analytics API:")
conn = sqlite3.connect('sold.db')
conn.row_factory = sqlite3.Row
cur = conn.cursor()
cur.execute('SELECT order_id, lot_number, price, barcode FROM orders WHERE paid_time IS NOT NULL LIMIT 5')
orders = cur.fetchall()

bol_conn = sqlite3.connect('bol.db')
bol_conn.row_factory = sqlite3.Row
bol_cur = bol_conn.cursor()

for order in orders:
    upc = order['barcode']
    cost = None
    if upc:
        bol_cur.execute('SELECT client_cost FROM bol_items WHERE upc = ? COLLATE NOCASE', (upc,))
        bol_row = bol_cur.fetchone()
        if bol_row:
            cost = bol_row['client_cost']
    
    bol_number = order['lot_number'] if order['lot_number'] else None
    print(f"   Order {order['order_id']}: LOT# {bol_number}, Cost ${cost if cost else 'N/A'}, Price ${order['price']}")

conn.close()
bol_conn.close()

print("\n✅ Data source test complete")
