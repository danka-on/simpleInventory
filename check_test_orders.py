import sqlite3

conn = sqlite3.connect('sold.db')
conn.row_factory = sqlite3.Row
cur = conn.cursor()

# Get recent test orders
cur.execute('SELECT id, barcode, title, shipped_time, isHandled, rackupdated FROM orders WHERE id IN (391, 392) ORDER BY id')
rows = cur.fetchall()

print('Test orders status:')
for r in rows:
    print(f'  ID={r["id"]}, Barcode={r["barcode"]}, Shipped={r["shipped_time"]}, Handled={r["isHandled"]}, RackUpdated={r["rackupdated"]}')

conn.close()
