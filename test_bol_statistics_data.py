import sqlite3

print('=== Testing BOL Statistics Data Sources ===\n')

# Test 1: rawbol.db upload_logs (for LOT cost data)
print('1. rawbol.db upload_logs:')
conn = sqlite3.connect('rawbol.db')
cur = conn.cursor()
cur.execute('SELECT lot_number, import_date, total_client_cost, rows_imported FROM upload_logs WHERE lot_number IS NOT NULL ORDER BY import_date DESC')
logs = cur.fetchall()
print(f'   Found {len(logs)} LOTs')
for i, log in enumerate(logs[:5]):
    print(f'   LOT {log[0]}: ${log[2]:.2f}, {log[3]} items, Date: {log[1]}')
conn.close()

# Test 2: rawbol.db raw_bol_items (for item costs)
print('\n2. rawbol.db raw_bol_items (sample):')
conn = sqlite3.connect('rawbol.db')
cur = conn.cursor()
cur.execute('SELECT upc, avg_cost, lot_number FROM raw_bol_items LIMIT 5')
items = cur.fetchall()
for item in items:
    print(f'   UPC {item[0]}: ${item[1]:.2f}, LOT {item[2]}')
conn.close()

# Test 3: sold.db orders with lot_number
print('\n3. sold.db orders (with lot_number):')
conn = sqlite3.connect('sold.db')
cur = conn.cursor()
cur.execute('SELECT order_id, barcode, price, quantity, lot_number FROM orders WHERE lot_number IS NOT NULL AND lot_number != "" LIMIT 5')
orders = cur.fetchall()
print(f'   Found {len(orders)} orders with LOT numbers')
for order in orders:
    print(f'   Order {order[0]}: UPC {order[1]}, ${order[2]}, Qty {order[3]}, LOT {order[4]}')

# Count total sold items with lot_number
cur.execute('SELECT COUNT(*) FROM orders WHERE lot_number IS NOT NULL AND lot_number != ""')
total_with_lot = cur.fetchone()[0]
cur.execute('SELECT COUNT(*) FROM orders WHERE paid_time IS NOT NULL')
total_sold = cur.fetchone()[0]
print(f'   Total sold orders: {total_sold}, With LOT#: {total_with_lot}')
conn.close()

print('\n✅ All data sources available for BOL Statistics!')
