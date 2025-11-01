"""
Check if the missing ASINs are in amazonStore
"""
import sqlite3

conn = sqlite3.connect('amazonStore.db')
conn.row_factory = sqlite3.Row
cur = conn.cursor()

asins = ['B000H5LUTI', 'B000H5IGJK']

for asin in asins:
    cur.execute('SELECT IMAGE, Title FROM ITEMS WHERE ASIN = ?', (asin,))
    row = cur.fetchone()
    
    print(f"\nASIN: {asin}")
    if row:
        print(f"  In Store: YES")
        print(f"  Title: {row['Title'][:60] if row['Title'] else 'N/A'}")
        print(f"  Image: {row['IMAGE'] if row['IMAGE'] else 'NO IMAGE'}")
    else:
        print(f"  In Store: NO - This item is not in amazonStore.db!")

conn.close()
