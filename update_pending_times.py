import sqlite3
from datetime import datetime, timedelta

conn = sqlite3.connect('searchRack.db')
cur = conn.cursor()

cur.execute('SELECT id, marked_at FROM zero_qty_pending_deletion')
rows = cur.fetchall()

for row in rows:
    marked_at = datetime.fromisoformat(row[1])
    new_delete_at = marked_at + timedelta(minutes=2880)
    cur.execute('UPDATE zero_qty_pending_deletion SET delete_at = ? WHERE id = ?', 
                (new_delete_at.isoformat(), row[0]))
    print(f'Updated pending deletion {row[0]}: delete_at = {new_delete_at}')

conn.commit()
conn.close()
print('Done!')
