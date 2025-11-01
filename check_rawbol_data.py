import sqlite3

# Check rawbol.db structure
conn = sqlite3.connect('rawbol.db')
cur = conn.cursor()

print('=== rawbol.db Tables ===')
cur.execute('SELECT name FROM sqlite_master WHERE type="table"')
tables = [t[0] for t in cur.fetchall()]
for table in tables:
    print(f'\n{table}:')
    cur.execute(f'PRAGMA table_info({table})')
    columns = cur.fetchall()
    for col in columns:
        print(f'  {col[1]} ({col[2]})')

print('\n=== Sample Data ===')
cur.execute('SELECT * FROM raw_bol_items LIMIT 3')
rows = cur.fetchall()
for row in rows:
    print(row)

print('\n=== Upload Logs ===')
cur.execute('SELECT lot_number, import_date, total_client_cost, rows_imported FROM upload_logs ORDER BY import_date DESC LIMIT 5')
logs = cur.fetchall()
for log in logs:
    print(f'LOT: {log[0]}, Date: {log[1]}, Cost: ${log[2]}, Items: {log[3]}')

conn.close()
