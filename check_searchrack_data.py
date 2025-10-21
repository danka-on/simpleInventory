import sqlite3

conn = sqlite3.connect('searchRack.db')
c = conn.cursor()
c.execute('SELECT BARCODE, ITEM_POSITION, PICTUREPOSITION FROM SEARCHRACK WHERE BARCODE = "5060285600291" COLLATE NOCASE')
rows = c.fetchall()
print('SearchRack data for 5060285600291:')
for row in rows:
    print(f'  Barcode: {row[0]}, Item_Position: {row[1]}, PicturePosition: {row[2]}')
conn.close()

# Also check bol.db
conn2 = sqlite3.connect('bol.db')
c2 = conn2.cursor()
c2.execute('SELECT upc, location, pictureposition FROM items_prep_status WHERE upc = "5060285600291" COLLATE NOCASE')
rows2 = c2.fetchall()
print('\nbol.db items_prep_status for 5060285600291:')
for row in rows2:
    print(f'  UPC: {row[0]}, Location: {row[1]}, PicturePosition: {row[2]}')
conn2.close()
