import sqlite3

conn = sqlite3.connect('sold.db')
cur = conn.cursor()

# Check schema
cur.execute("PRAGMA table_info(orders)")
schema = cur.fetchall()
print(f"Orders table schema:")
for col in schema:
    print(f"  {col[1]} ({col[2]})")

# Check marketplace sales
cur.execute("SELECT COUNT(*) FROM orders WHERE store='marketplace'")
count = cur.fetchone()[0]
print(f"\nMarketplace orders: {count}")

# Show sample
if count > 0:
    cur.execute("SELECT * FROM orders WHERE store='marketplace' LIMIT 5")
    rows = cur.fetchall()
    print(f"\nSample rows:")
    for row in rows:
        print(f"  {row}")

conn.close()
