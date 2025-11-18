import sqlite3

conn = sqlite3.connect('searchRack.db')
cur = conn.cursor()

# Check if zero_qty tables exist
cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE '%zero%'")
tables = cur.fetchall()

print('Zero-qty related tables:', tables)

# Check if there are any pending items
if tables:
    cur.execute('SELECT COUNT(*) FROM zero_qty_pending_deletion')
    count = cur.fetchone()[0]
    print(f'Pending deletions: {count}')

conn.close()
