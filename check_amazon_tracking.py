import sqlite3

conn = sqlite3.connect('amazonStore.db')
cur = conn.cursor()

print("=== Tables in amazonStore.db ===")
cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
tables = cur.fetchall()
for table in tables:
    print(f"  {table[0]}")

print("\n=== ITEMS table schema ===")
cur.execute("PRAGMA table_info(ITEMS)")
cols = cur.fetchall()
for col in cols:
    print(f"  {col[1]} ({col[2]})")

print("\n=== Check for items that have been API-fetched ===")
# Items where UPC != ASIN means we successfully fetched UPC from API
cur.execute("SELECT COUNT(*) FROM ITEMS WHERE UPC IS NOT NULL AND UPC != ASIN")
fetched = cur.fetchone()[0]
cur.execute("SELECT COUNT(*) FROM ITEMS WHERE UPC IS NULL OR UPC = ASIN")
not_fetched = cur.fetchone()[0]

print(f"Items with UPC fetched: {fetched}")
print(f"Items needing UPC fetch: {not_fetched}")

conn.close()
