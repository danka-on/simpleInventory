import sqlite3

conn = sqlite3.connect('amazonStore.db')
cur = conn.cursor()

cur.execute('SELECT COUNT(*) FROM ITEMS')
total = cur.fetchone()[0]

cur.execute('SELECT COUNT(*) FROM ITEMS WHERE UPC IS NOT NULL AND UPC != ""')
with_upc = cur.fetchone()[0]

cur.execute('SELECT COUNT(*) FROM ITEMS WHERE UPC IS NULL OR UPC = ""')
without_upc = cur.fetchone()[0]

cur.execute('SELECT COUNT(*) FROM ITEMS WHERE UPC = ASIN')
upc_equals_asin = cur.fetchone()[0]

print(f'Total items: {total}')
print(f'With UPC: {with_upc}')
print(f'Without UPC (NULL or empty): {without_upc}')
print(f'UPC = ASIN: {upc_equals_asin}')

print('\nSample items:')
cur.execute('SELECT ASIN, UPC FROM ITEMS LIMIT 5')
for row in cur.fetchall():
    print(f'  ASIN: {row[0]}, UPC: {row[1]}')

conn.close()
