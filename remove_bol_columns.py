import sqlite3

# Connect to bol.db
conn = sqlite3.connect('bol.db')
cur = conn.cursor()

print('Removing columns: bol_number, client_cost, total_client_cost from bol_items table...')

# SQLite doesn't support DROP COLUMN directly, so we need to:
# 1. Create a new table without those columns
# 2. Copy data from old table
# 3. Drop old table
# 4. Rename new table

# Create new table without the unwanted columns
cur.execute('''
    CREATE TABLE bol_items_new (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        upc TEXT,
        item_description TEXT,
        image_url TEXT,
        lot_number TEXT,
        import_date TEXT,
        isFound TEXT,
        list_status TEXT,
        temporary INTEGER,
        quantity INTEGER
    )
''')

# Copy data from old table to new table
cur.execute('''
    INSERT INTO bol_items_new (id, upc, item_description, image_url, lot_number, 
                                import_date, isFound, list_status, temporary, quantity)
    SELECT id, upc, item_description, image_url, lot_number, 
           import_date, isFound, list_status, temporary, quantity
    FROM bol_items
''')

# Drop old table
cur.execute('DROP TABLE bol_items')

# Rename new table to original name
cur.execute('ALTER TABLE bol_items_new RENAME TO bol_items')

# Commit changes
conn.commit()

# Verify the changes
print('\n✅ Columns removed successfully!')
print('\nNew bol_items structure:')
cur.execute('PRAGMA table_info(bol_items)')
columns = cur.fetchall()
for col in columns:
    print(f'  {col[1]} ({col[2]})')

# Show row count
cur.execute('SELECT COUNT(*) FROM bol_items')
count = cur.fetchone()[0]
print(f'\nTotal rows preserved: {count}')

conn.close()
