import sqlite3

print("Checking searchRack.db...")
conn = sqlite3.connect('searchRack.db')
cur = conn.cursor()

cur.execute('SELECT COUNT(*) FROM SEARCHRACK')
count = cur.fetchone()[0]
print(f'Total items in SEARCHRACK: {count}')

# Sample a few items
cur.execute('SELECT BARCODE, TITLE, ITEM_POSITION, IMAGES, IMAGE, ITEMID FROM SEARCHRACK LIMIT 10')
samples = cur.fetchall()
print('\nSample items:')
for s in samples:
    barcode = s[0] or 'None'
    title = (s[1] or 'None')[:40]
    position = s[2] or 'None'
    images = 'Yes' if s[3] else 'No'
    image = 'Yes' if s[4] else 'No'
    itemid = s[5] or 'None'
    print(f'  Barcode: {barcode:14} | Title: {title:40} | Pos: {position:8}')
    print(f'    Images: {images:3} | Image: {image:3} | ItemID: {itemid}')

conn.close()
