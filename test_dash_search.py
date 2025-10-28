import sqlite3

print("=== Searching bol.db ===")
conn = sqlite3.connect('bol.db')
cur = conn.cursor()
cur.execute("SELECT upc, item_description FROM bol_items WHERE item_description LIKE '%Dash%Ice Cream%' COLLATE NOCASE LIMIT 5")
results = cur.fetchall()
print(f"Found {len(results)} results in bol.db:")
for r in results:
    print(f"  {r[0]} | {r[1]}")
conn.close()

print("\n=== Searching rawbol.db ===")
conn = sqlite3.connect('rawbol.db')
cur = conn.cursor()
cur.execute("SELECT upc, item_description FROM raw_bol_items WHERE item_description LIKE '%Dash%Ice Cream%' COLLATE NOCASE LIMIT 5")
results = cur.fetchall()
print(f"Found {len(results)} results in rawbol.db:")
for r in results:
    print(f"  {r[0]} | {r[1]}")
conn.close()

print("\n=== Checking table structure ===")
conn = sqlite3.connect('rawbol.db')
cur = conn.cursor()
cur.execute("PRAGMA table_info(raw_bol_items)")
cols = [r[1] for r in cur.fetchall()]
print(f"rawbol.db columns: {cols}")
conn.close()

print("\n=== Total count in rawbol.db ===")
conn = sqlite3.connect('rawbol.db')
cur = conn.cursor()
cur.execute("SELECT COUNT(*) FROM raw_bol_items")
print(f"Total items: {cur.fetchone()[0]}")
conn.close()
