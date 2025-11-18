import sqlite3

conn = sqlite3.connect('searchRack.db')
cur = conn.cursor()

# Check all zero-quantity items
cur.execute('SELECT ID, BARCODE, TITLE, QUANTITY FROM SEARCHRACK WHERE QUANTITY = 0')
items = cur.fetchall()
print(f'\n=== Zero Quantity Items: {len(items)} ===')
for item in items:
    print(f'  ID: {item[0]}, Barcode: {item[1]}, Title: {item[2][:50]}, Qty: {item[3]}')

# Check pending deletions
cur.execute('SELECT * FROM zero_qty_pending_deletion')
pending = cur.fetchall()
print(f'\n=== Pending Deletions: {len(pending)} ===')
for p in pending:
    print(f'  ID: {p[0]}, SearchRack ID: {p[1]}, Marked: {p[2]}, Delete At: {p[3]}, Cancelled: {p[4]}')

# Check if zero-qty items are in pending deletion
zero_qty_ids = [item[0] for item in items]
pending_ids = [p[1] for p in pending]

print(f'\n=== Analysis ===')
print(f'Zero-qty item IDs: {zero_qty_ids}')
print(f'Pending deletion IDs: {pending_ids}')

missing = [id for id in zero_qty_ids if id not in pending_ids]
if missing:
    print(f'\n⚠️  Items at 0 quantity but NOT marked for deletion: {missing}')
    for id in missing:
        cur.execute('SELECT ID, BARCODE, TITLE FROM SEARCHRACK WHERE ID = ?', (id,))
        item = cur.fetchone()
        if item:
            print(f'    - ID {item[0]}: {item[2][:50]} (barcode: {item[1]})')
else:
    print('✓ All zero-quantity items are properly marked for deletion')

conn.close()
