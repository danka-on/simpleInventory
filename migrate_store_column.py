"""
Migration script to add 'store' column to sold.db and set default values
"""

import sqlite3

print("🔄 Migrating sold.db to add 'store' column...\n")

conn = sqlite3.connect('sold.db')
cur = conn.cursor()

# Check if store column exists
cur.execute('PRAGMA table_info(orders)')
cols = [r[1] for r in cur.fetchall()]

if 'store' in cols:
    print("✅ 'store' column already exists")
    
    # Count records by store
    cur.execute("SELECT store, COUNT(*) FROM orders GROUP BY store")
    results = cur.fetchall()
    print("\nCurrent distribution:")
    for store, count in results:
        print(f"  {store or 'NULL'}: {count} orders")
    
    # Update NULL values to 'ebay' (default for existing records)
    cur.execute("UPDATE orders SET store = 'ebay' WHERE store IS NULL OR store = ''")
    updated = cur.rowcount
    if updated > 0:
        print(f"\n✅ Updated {updated} records with NULL/empty store to 'ebay'")
    
else:
    print("⚠️  'store' column does not exist, adding it...")
    
    # Add column with default 'ebay' for existing records
    cur.execute('ALTER TABLE orders ADD COLUMN store TEXT DEFAULT "ebay"')
    conn.commit()
    
    # Update all existing records to 'ebay'
    cur.execute("UPDATE orders SET store = 'ebay' WHERE store IS NULL OR store = ''")
    updated = cur.rowcount
    
    print(f"✅ Added 'store' column and set {updated} existing records to 'ebay'")

conn.commit()

# Final count
cur.execute("SELECT COUNT(*) FROM orders")
total = cur.fetchone()[0]

cur.execute("SELECT store, COUNT(*) FROM orders GROUP BY store")
results = cur.fetchall()

print(f"\n📊 Final status ({total} total orders):")
for store, count in results:
    print(f"  {store}: {count} orders")

conn.close()

print("\n✅ Migration complete!")
