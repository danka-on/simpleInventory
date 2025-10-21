import sqlite3

conn = sqlite3.connect('searchRack.db')
c = conn.cursor()

print("All entries with shelf codes (ITEM_POSITION not empty):")
c.execute('SELECT ID, BARCODE, ITEM_POSITION, PICTUREPOSITION FROM SEARCHRACK WHERE ITEM_POSITION IS NOT NULL AND ITEM_POSITION != "" AND ITEM_POSITION != "picture" ORDER BY ID DESC LIMIT 20')
for row in c.fetchall():
    print(f'  ID: {row[0]}, Barcode: {row[1]}, Item_Position: "{row[2]}", PicturePosition: "{row[3]}"')

conn.close()
