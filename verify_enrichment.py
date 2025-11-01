import sqlite3

conn = sqlite3.connect('sold.db')
cur = conn.cursor()

print("=== Verifying Enriched Barcodes ===\n")

test_orders = ["16-13749-18220", "20-13726-00378", "22-13646-37046"]

for order_id in test_orders:
    cur.execute('SELECT order_id, barcode, title FROM orders WHERE order_id = ?', (order_id,))
    row = cur.fetchone()
    if row:
        print(f"✅ {row[0]}")
        print(f"   Barcode: {row[1]}")
        print(f"   Title: {row[2][:60]}...\n")

# Get summary stats
cur.execute('''
    SELECT 
        COUNT(*) as total,
        SUM(CASE WHEN barcode IS NOT NULL AND barcode != "" THEN 1 ELSE 0 END) as with_barcode,
        SUM(CASE WHEN lot_number IS NOT NULL AND lot_number != "" THEN 1 ELSE 0 END) as with_lot
    FROM orders
''')

stats = cur.fetchone()
print("=== Summary ===")
print(f"Total orders: {stats[0]}")
print(f"With barcode: {stats[1]} ({stats[1]/stats[0]*100:.1f}%)")
print(f"With LOT #: {stats[2]} ({stats[2]/stats[0]*100:.1f}%)")
print(f"Missing barcode: {stats[0] - stats[1]}")

conn.close()
