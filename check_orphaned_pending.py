import sqlite3

conn = sqlite3.connect('searchRack.db')
cur = conn.cursor()

# Find orphaned entries
cur.execute('''
    SELECT 
        p.id,
        p.searchrack_id,
        p.marked_at,
        p.delete_at,
        s.ID as item_exists
    FROM zero_qty_pending_deletion p
    LEFT JOIN SEARCHRACK s ON p.searchrack_id = s.ID
''')

rows = cur.fetchall()
print(f'\nFound {len(rows)} pending deletions:\n')

orphaned = []
for r in rows:
    pending_id, searchrack_id, marked_at, delete_at, item_exists = r
    status = "EXISTS" if item_exists else "ORPHANED"
    print(f'  Pending ID: {pending_id}, SearchRack ID: {searchrack_id}, Status: {status}')
    if not item_exists:
        orphaned.append(pending_id)

if orphaned:
    print(f'\n⚠️  Found {len(orphaned)} orphaned entries!')
    print('These are entries in zero_qty_pending_deletion that reference deleted SearchRack items.')
    
    response = input('\nDelete orphaned entries? (y/n): ')
    if response.lower() == 'y':
        for pid in orphaned:
            cur.execute('DELETE FROM zero_qty_pending_deletion WHERE id = ?', (pid,))
        conn.commit()
        print(f'✅ Deleted {len(orphaned)} orphaned entries!')
else:
    print('\n✅ No orphaned entries found!')

conn.close()
