import sqlite3

conn = sqlite3.connect('sold.db')
cur = conn.cursor()

barcode = '719978859014'
cur.execute('SELECT id, barcode, title, quantity, store, paid_time FROM orders WHERE barcode = ? ORDER BY id DESC', (barcode,))
rows = cur.fetchall()

print(f'Found {len(rows)} orders for barcode {barcode}:')
for r in rows:
    print(f'  ID={r[0]}, Qty={r[3]}, Store={r[4]}, Time={r[5]}')

conn.close()
