import sqlite3
conn = sqlite3.connect('searchRack.db')
c = conn.cursor()
c.execute('SELECT barcode, item_position, pictureposition FROM SEARCHRACK WHERE item_position IS NOT NULL OR pictureposition IS NOT NULL LIMIT 5')
print('Items with location data:')
for row in c.fetchall():
    print(row)
conn.close()