import sqlite3

conn = sqlite3.connect('sold.db')
cur = conn.cursor()

cur.execute('PRAGMA table_info(orders)')
cols = cur.fetchall()

print('sold.db orders table columns:')
for col in cols:
    print(f'  {col[1]} ({col[2]})')

conn.close()
