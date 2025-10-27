import sqlite3

conn = sqlite3.connect('amazonStore.db')
cur = conn.cursor()

cur.execute('''
    SELECT COUNT(*), 
           SUM(CASE WHEN IMAGE IS NOT NULL AND IMAGE != "" THEN 1 ELSE 0 END) as with_images
    FROM ITEMS
''')
result = cur.fetchone()
print(f'amazonStore.db:')
print(f'  Total items: {result[0]}')
print(f'  With images: {result[1]}')
print(f'  Without images: {result[0] - result[1]}')

# Show sample
cur.execute('SELECT ASIN, UPC, IMAGE FROM ITEMS WHERE IMAGE IS NOT NULL AND IMAGE != "" LIMIT 3')
samples = cur.fetchall()
if samples:
    print('\nSample items with images:')
    for s in samples:
        print(f'  ASIN: {s[0]}, UPC: {s[1]}, Image: {s[2][:50]}...')

conn.close()
