import sqlite3

# Check bol.db structure
print("=== BOL Database Structure ===\n")
conn = sqlite3.connect('bol.db')
conn.row_factory = sqlite3.Row
cur = conn.cursor()

# Get table schema
cur.execute('PRAGMA table_info(bol_items)')
print("bol_items columns:")
for row in cur.fetchall():
    print(f"  {row['name']}: {row['type']}")

print("\n=== Sample BOL Items ===")
cur.execute('SELECT * FROM bol_items LIMIT 5')
for row in cur.fetchall():
    print(f"UPC: {row['upc']} | Desc: {row['item_description'][:40] if row['item_description'] else 'N/A'}... | Cost: ${row['client_cost'] if row['client_cost'] else 0}")

# Check if there's a bol_number column
cur.execute('SELECT DISTINCT bol_number FROM bol_items WHERE bol_number IS NOT NULL ORDER BY bol_number LIMIT 20')
bols = cur.fetchall()
print(f"\n=== Available BOLs (found {len(bols)}) ===")
for bol in bols:
    bol_number = bol['bol_number']
    # Get stats for this BOL
    cur.execute('SELECT COUNT(*) as count, SUM(client_cost * quantity) as total_cost FROM bol_items WHERE bol_number = ?', (bol_number,))
    stats = cur.fetchone()
    total_cost = stats['total_cost'] if stats['total_cost'] else 0
    print(f"  {bol_number}: {stats['count']} items, Total Cost: ${total_cost:.2f}")

conn.close()

# Check sold.db to see how many sold items have BOL cost data
print("\n=== Sold Items with BOL Cost Data ===")
sold_conn = sqlite3.connect('sold.db')
sold_cur = sold_conn.cursor()

sold_cur.execute('''
    SELECT 
        COUNT(*) as total,
        COUNT(CASE WHEN barcode IS NOT NULL THEN 1 END) as with_barcode
    FROM orders
''')
stats = sold_cur.fetchone()
print(f"Total orders: {stats[0]}")
print(f"Orders with barcode: {stats[1]}")

sold_conn.close()
