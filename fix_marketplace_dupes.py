import sqlite3

conn = sqlite3.connect('sold.db')
cur = conn.cursor()

barcode = '719978859014'

# Delete the duplicate entry (ID=393)
cur.execute('DELETE FROM orders WHERE id = 393')
print('Deleted duplicate entry ID=393')

# Update the remaining entry (ID=394) to have quantity=2
cur.execute('UPDATE orders SET quantity = 2 WHERE id = 394')
print('Updated ID=394 to quantity=2')

conn.commit()

# Verify
cur.execute('SELECT id, barcode, title, quantity, store, paid_time FROM orders WHERE barcode = ? ORDER BY id DESC', (barcode,))
rows = cur.fetchall()

print(f'\nAfter cleanup - {len(rows)} orders for barcode {barcode}:')
for r in rows:
    print(f'  ID={r[0]}, Qty={r[3]}, Store={r[4]}, Time={r[5]}')

conn.close()
