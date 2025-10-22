import sqlite3

# Check bol.db
conn = sqlite3.connect('bol.db')
cur = conn.cursor()

# Count total UPCs starting with 0
cur.execute("SELECT COUNT(*) FROM bol_items WHERE upc LIKE '0%'")
count = cur.fetchone()[0]
print(f"Total UPCs starting with 0 in bol.db: {count}")

# Show first 20 examples
cur.execute("SELECT upc FROM bol_items WHERE upc LIKE '0%' LIMIT 20")
results = cur.fetchall()
if results:
    print("\nFirst 20 examples:")
    for r in results:
        print(f"  {r[0]}")
else:
    print("\nNo UPCs starting with 0 found in bol.db.")

conn.close()

# Check rawbol.db
print("\n" + "="*50)
conn2 = sqlite3.connect('rawbol.db')
cur2 = conn2.cursor()

# Count total UPCs starting with 0
cur2.execute("SELECT COUNT(*) FROM raw_bol_items WHERE upc LIKE '0%'")
count2 = cur2.fetchone()[0]
print(f"Total UPCs starting with 0 in rawbol.db: {count2}")

# Show first 20 examples
cur2.execute("SELECT upc FROM raw_bol_items WHERE upc LIKE '0%' LIMIT 20")
results2 = cur2.fetchall()
if results2:
    print("\nFirst 20 examples:")
    for r in results2:
        print(f"  {r[0]}")
else:
    print("\nNo UPCs starting with 0 found in rawbol.db.")

conn2.close()
