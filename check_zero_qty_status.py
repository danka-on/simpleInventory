import sqlite3
from datetime import datetime

conn = sqlite3.connect('searchRack.db')
cur = conn.cursor()

# Check zero-quantity items
cur.execute('SELECT ID, BARCODE, TITLE, QUANTITY, ITEM_POSITION FROM SEARCHRACK WHERE QUANTITY = 0')
items = cur.fetchall()
print(f'Zero quantity items in SEARCHRACK: {len(items)}')
for i in items:
    title = i[2][:30] if i[2] else 'N/A'
    print(f'  ID={i[0]}, Barcode={i[1]}, Title={title}, Qty={i[3]}, Pos={i[4]}')

# Check pending deletions
cur.execute('''
    SELECT p.id, p.searchrack_id, p.marked_at, p.delete_at, p.deletion_cancelled, s.BARCODE 
    FROM zero_qty_pending_deletion p 
    LEFT JOIN SEARCHRACK s ON p.searchrack_id = s.ID
''')
pending = cur.fetchall()
print(f'\nPending deletions: {len(pending)}')
for p in pending:
    print(f'  ID={p[0]}, SearchRack_ID={p[1]}, Marked={p[2]}, Delete_at={p[3]}, Cancelled={p[4]}, Barcode={p[5]}')

# Check settings
cur.execute('SELECT key, value FROM zero_qty_settings')
settings = cur.fetchall()
print(f'\nSettings:')
for s in settings:
    print(f'  {s[0]} = {s[1]}')

now = datetime.now()
print(f'\nCurrent time: {now}')

# Check if any are eligible
if pending:
    print('\nEligibility check:')
    for p in pending:
        delete_at = datetime.fromisoformat(p[3])
        if delete_at <= now:
            print(f'  Item {p[1]}: ELIGIBLE (delete_at={delete_at})')
        else:
            hours_remaining = (delete_at - now).total_seconds() / 3600
            print(f'  Item {p[1]}: NOT YET ({hours_remaining:.2f} hours remaining)')

conn.close()
