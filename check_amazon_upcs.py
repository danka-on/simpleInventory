import sqlite3

conn = sqlite3.connect('amazonStore.db')
cur = conn.cursor()

cur.execute('SELECT COUNT(*) as total FROM ITEMS')
total = cur.fetchone()[0]

cur.execute('SELECT COUNT(*) FROM ITEMS WHERE UPC IS NOT NULL AND UPC != ""')
with_upc = cur.fetchone()[0]

print(f'Total items: {total}')
print(f'Items with UPC: {with_upc}')
print(f'Items without UPC: {total - with_upc}')

if with_upc > 0:
    cur.execute('SELECT TITLE, ASIN, SKU, UPC FROM ITEMS WHERE UPC IS NOT NULL AND UPC != "" LIMIT 5')
    items = cur.fetchall()
    print('\nSample items with UPC:')
    for item in items:
        print(f'  - {item[0][:50]}...')
        print(f'    ASIN: {item[1]}, SKU: {item[2]}')
        print(f'    UPC: {item[3]}')
        print()

conn.close()
