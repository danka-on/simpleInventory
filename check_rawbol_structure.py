import sqlite3

conn = sqlite3.connect('rawbol.db')
cur = conn.cursor()

cur.execute('SELECT name FROM sqlite_master WHERE type="table"')
tables = cur.fetchall()

print('Tables in rawbol.db:')
for t in tables:
    print(f'  - {t[0]}')
    
    # Show structure
    cur.execute(f'PRAGMA table_info({t[0]})')
    columns = cur.fetchall()
    print('    Columns:')
    for col in columns:
        print(f'      - {col[1]} ({col[2]})')
    
    # Count rows
    cur.execute(f'SELECT COUNT(*) FROM {t[0]}')
    count = cur.fetchone()[0]
    print(f'    Rows: {count}')
    print()

conn.close()
