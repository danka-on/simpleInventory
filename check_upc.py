import sqlite3

upc = "858557007115"

# Check bol.db
print("Checking bol.db...")
conn = sqlite3.connect('bol.db')
cur = conn.cursor()
cur.execute('''
    SELECT upc, item_description, lot_number, good_qty, unchecked_qty, quantity, temporary 
    FROM bol_items 
    WHERE upc = ? OR upc LIKE ?
''', (upc, f'{upc}%'))
rows = cur.fetchall()
print(f'Found {len(rows)} items in bol.db:')
for r in rows:
    print(f'  UPC: {r[0]}, Desc: {r[1][:50]}, LOT: {r[2]}, Good: {r[3]}, Unchecked: {r[4]}, Qty: {r[5]}, Temp: {r[6]}')

# Check items_prep_status
cur.execute('SELECT upc, status, quantity FROM items_prep_status WHERE upc = ? OR upc LIKE ?', (upc, f'{upc}%'))
prep_rows = cur.fetchall()
print(f'\nFound {len(prep_rows)} prep status entries:')
for r in prep_rows:
    print(f'  UPC: {r[0]}, Status: {r[1]}, Qty: {r[2]}')

conn.close()

# Check rawbol.db
print("\nChecking rawbol.db...")
conn2 = sqlite3.connect('rawbol.db')
cur2 = conn2.cursor()
cur2.execute('SELECT upc, item_description, lot_number, quantity FROM raw_bol_items WHERE upc = ?', (upc,))
raw_rows = cur2.fetchall()
print(f'Found {len(raw_rows)} items in rawbol.db:')
for r in raw_rows:
    print(f'  UPC: {r[0]}, Desc: {r[1][:50]}, LOT: {r[2]}, Qty: {r[3]}')
conn2.close()
