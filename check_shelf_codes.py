import sqlite3

# Check for your test UPC with the new shelf code
conn = sqlite3.connect('searchRack.db')
c = conn.cursor()

print("All searchRack entries (showing first 10):")
c.execute('SELECT ID, BARCODE, ITEM_POSITION, PICTUREPOSITION FROM SEARCHRACK LIMIT 10')
for row in c.fetchall():
    print(f'  ID: {row[0]}, Barcode: {row[1]}, Item_Position: "{row[2]}", PicturePosition: "{row[3]}"')

print("\n\nSearching for shelf codes 'or1s6' or 'or2s3':")
c.execute('SELECT ID, BARCODE, ITEM_POSITION, PICTUREPOSITION FROM SEARCHRACK WHERE ITEM_POSITION LIKE "%or1s6%" OR ITEM_POSITION LIKE "%or2s3%" COLLATE NOCASE')
for row in c.fetchall():
    print(f'  ID: {row[0]}, Barcode: {row[1]}, Item_Position: "{row[2]}", PicturePosition: "{row[3]}"')

conn.close()
