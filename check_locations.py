import sqlite3

# Check if test order barcodes exist in searchRack.db (with leading zero stripping)
barcodes = ['48552397896', '21241169547']

conn = sqlite3.connect('searchRack.db')
cur = conn.cursor()

print("Testing with leading zero stripping:")
for bc in barcodes:
    bc_stripped = bc.lstrip('0')
    cur.execute('SELECT ITEM_POSITION, PICTUREPOSITION, ITEMID FROM SEARCHRACK WHERE ITEMID = ?', (bc_stripped,))
    row = cur.fetchone()
    if row:
        print(f'Barcode {bc} (stripped: {bc_stripped}): Position={row[0]}, Picture={row[1]}')
    else:
        print(f'Barcode {bc} (stripped: {bc_stripped}): NOT FOUND')

conn.close()
