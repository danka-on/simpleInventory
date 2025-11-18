import sqlite3
from datetime import datetime

conn = sqlite3.connect('searchRack.db')
cur = conn.cursor()

cur.execute('SELECT delete_at FROM zero_qty_pending_deletion WHERE id = 5')
row = cur.fetchone()

if row:
    delete_at = datetime.fromisoformat(row[0])
    now = datetime.now()
    hours_remaining = (delete_at - now).total_seconds() / 3600
    print(f'Delete at: {delete_at}')
    print(f'Hours remaining: {hours_remaining:.1f}h')

conn.close()
