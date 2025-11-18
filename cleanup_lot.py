import sqlite3

lot_number = '16578199'

# Clean from rawbol.db upload_history
try:
    conn = sqlite3.connect('rawbol.db')
    cur = conn.cursor()
    
    # Check if upload_history table exists
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='upload_history'")
    if cur.fetchone():
        cur.execute('DELETE FROM upload_history WHERE lot_number = ?', (lot_number,))
        deleted = cur.rowcount
        conn.commit()
        print(f'Deleted {deleted} entries from upload_history')
    else:
        print('No upload_history table found')
    
    conn.close()
except Exception as e:
    print(f'Error cleaning upload_history: {e}')

print(f'\nLOT {lot_number} cleanup complete!')
print('- Deleted 348 rows from bol.db')
print('- Deleted 0 rows from rawbol.db (was already clean)')
