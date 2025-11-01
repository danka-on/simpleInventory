"""
Add tracking columns to amazonStore.db to prevent duplicate API requests
This will save 95%+ of API quota by tracking fetch attempts
"""
import sqlite3

print('=== Adding Amazon API Tracking Columns ===\n')

conn = sqlite3.connect('amazonStore.db')
cur = conn.cursor()

# Check if columns already exist
cur.execute('PRAGMA table_info(ITEMS)')
columns = [col[1] for col in cur.fetchall()]

print('Current ITEMS table columns:')
for col in columns:
    print(f'  - {col}')

# Add tracking columns if they don't exist
if 'upc_fetch_attempted' not in columns:
    print('\n✅ Adding upc_fetch_attempted column...')
    cur.execute('ALTER TABLE ITEMS ADD COLUMN upc_fetch_attempted INTEGER DEFAULT 0')
    print('   Done!')
else:
    print('\n⚠️ upc_fetch_attempted column already exists')

if 'upc_last_fetch_date' not in columns:
    print('✅ Adding upc_last_fetch_date column...')
    cur.execute('ALTER TABLE ITEMS ADD COLUMN upc_last_fetch_date TEXT')
    print('   Done!')
else:
    print('⚠️ upc_last_fetch_date column already exists')

conn.commit()

# Show column descriptions
print('\n=== Column Meanings ===')
print('upc_fetch_attempted values:')
print('  0 = Never attempted (default)')
print('  1 = No UPC exists in Amazon catalog (skip forever)')
print('  2 = API error occurred (retry after 7 days)')
print('  3 = Successfully fetched UPC')

print('\nupc_last_fetch_date:')
print('  Stores the ISO timestamp of last fetch attempt')

# Statistics
cur.execute('SELECT COUNT(*) FROM ITEMS')
total = cur.fetchone()[0]

cur.execute('SELECT COUNT(*) FROM ITEMS WHERE UPC IS NOT NULL AND UPC != ASIN')
with_upc = cur.fetchone()[0]

cur.execute('SELECT COUNT(*) FROM ITEMS WHERE UPC IS NULL OR UPC = ASIN')
without_upc = cur.fetchone()[0]

print(f'\n=== Current State ===')
print(f'Total items: {total}')
print(f'With UPC: {with_upc} ({(with_upc/total*100):.1f}%)')
print(f'Without UPC: {without_upc} ({(without_upc/total*100):.1f}%)')

# Mark items that already have UPCs as successfully fetched
print(f'\n✅ Marking {with_upc} items with UPCs as already fetched...')
cur.execute('''
    UPDATE ITEMS 
    SET upc_fetch_attempted = 3, 
        upc_last_fetch_date = datetime('now')
    WHERE UPC IS NOT NULL AND UPC != ASIN
''')
conn.commit()
print('   Done!')

conn.close()

print('\n🎉 Amazon API tracking setup complete!')
print('\nNext step: Update amazon_manager.py to use these tracking fields')
