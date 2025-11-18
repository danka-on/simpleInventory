import sqlite3

conn = sqlite3.connect('searchRack.db')
cur = conn.cursor()

cur.execute('SELECT ITEMID, ITEM_POSITION, PICTUREPOSITION, QUANTITY FROM SEARCHRACK')
rows = cur.fetchall()

print(f'All {len(rows)} items in searchRack.db:\n')
for row in rows:
    print(f'ITEMID: {row[0]}, Position: {row[1]}, Picture: {row[2]}, Qty: {row[3]}')

conn.close()
