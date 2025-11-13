import sqlite3
from datetime import datetime

conn = sqlite3.connect('bol.db')
cur = conn.cursor()

# Check existing columns
cur.execute('PRAGMA table_info(bol_items)')
cols = [r[1] for r in cur.fetchall()]
print('Existing columns:', cols)

# Add new columns if they don't exist
if 'original_qty' not in cols:
    print('\n📊 Adding original_qty column...')
    cur.execute('ALTER TABLE bol_items ADD COLUMN original_qty INTEGER')
    cur.execute('UPDATE bol_items SET original_qty = quantity WHERE original_qty IS NULL')
    print('✅ Added and backfilled original_qty from quantity')

if 'good_qty' not in cols:
    print('\n✅ Adding good_qty column...')
    cur.execute('ALTER TABLE bol_items ADD COLUMN good_qty INTEGER DEFAULT 0')
    # Backfill from items_prep_status
    cur.execute('''
        UPDATE bol_items 
        SET good_qty = (
            SELECT COALESCE(quantity, 0) 
            FROM items_prep_status 
            WHERE items_prep_status.upc = bol_items.upc 
            AND items_prep_status.status = 'good'
        )
        WHERE EXISTS (
            SELECT 1 FROM items_prep_status 
            WHERE items_prep_status.upc = bol_items.upc 
            AND items_prep_status.status = 'good'
        )
    ''')
    updated = cur.rowcount
    print(f'✅ Added good_qty and backfilled {updated} items from items_prep_status')

if 'bad_qty' not in cols:
    print('\n❌ Adding bad_qty column...')
    cur.execute('ALTER TABLE bol_items ADD COLUMN bad_qty INTEGER DEFAULT 0')
    # Calculate from suffixed entries
    cur.execute('''
        UPDATE bol_items 
        SET bad_qty = (
            SELECT COALESCE(SUM(quantity), 0)
            FROM bol_items AS suffixed
            WHERE suffixed.upc LIKE bol_items.upc || '-%'
            AND suffixed.temporary = 1
        )
        WHERE upc NOT LIKE '%-%'
    ''')
    updated = cur.rowcount
    print(f'✅ Added bad_qty and calculated from {updated} base items')

if 'unchecked_qty' not in cols:
    print('\n📦 Adding unchecked_qty column...')
    cur.execute('ALTER TABLE bol_items ADD COLUMN unchecked_qty INTEGER')
    # Calculate: original - good - bad
    cur.execute('''
        UPDATE bol_items 
        SET unchecked_qty = COALESCE(original_qty, 0) - COALESCE(good_qty, 0) - COALESCE(bad_qty, 0)
        WHERE unchecked_qty IS NULL
    ''')
    updated = cur.rowcount
    print(f'✅ Added unchecked_qty and calculated for {updated} items')

conn.commit()

# Show sample of results
print('\n' + '='*80)
print('Sample items with new quantity tracking:')
print('='*80)
cur.execute('''
    SELECT upc, original_qty, good_qty, bad_qty, unchecked_qty, quantity 
    FROM bol_items 
    WHERE upc NOT LIKE '%-%' 
    LIMIT 10
''')
print(f"{'UPC':<15} | {'Original':>8} | {'Good':>5} | {'Bad':>4} | {'Unchecked':>9} | {'Old Qty':>7}")
print('-' * 80)
for row in cur.fetchall():
    print(f"{row[0]:<15} | {row[1]:>8} | {row[2]:>5} | {row[3]:>4} | {row[4]:>9} | {row[5]:>7}")

# Show summary
cur.execute('SELECT COUNT(*) FROM bol_items WHERE upc NOT LIKE "%-%"')
total_base = cur.fetchone()[0]
cur.execute('SELECT COUNT(*) FROM bol_items WHERE upc LIKE "%-%"')
total_suffixed = cur.fetchone()[0]

print('\n' + '='*80)
print(f'Migration Summary:')
print(f'  Base items: {total_base}')
print(f'  Suffixed (bad) items: {total_suffixed}')
print(f'  Total: {total_base + total_suffixed}')
print('='*80)

conn.close()
print('\n✅ Migration complete! New columns added to bol.db -> bol_items table')
