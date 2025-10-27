import sqlite3

print("Checking bol.db...")
conn = sqlite3.connect('bol.db')
cur = conn.cursor()

cur.execute('SELECT name FROM sqlite_master WHERE type="table"')
tables = [t[0] for t in cur.fetchall()]
print(f'Tables: {tables}')

cur.execute('SELECT COUNT(*) FROM bol_items')
count = cur.fetchone()[0]
print(f'bol_items count: {count}')

# Sample a few items
cur.execute('SELECT upc, item_description FROM bol_items LIMIT 5')
samples = cur.fetchall()
print('\nSample items:')
for s in samples:
    print(f'  UPC: {s[0]}, Description: {s[1][:50] if s[1] else "None"}')

conn.close()
