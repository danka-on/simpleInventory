import sqlite3

# Check rack.db
print("=== Checking rack.db ===")
conn = sqlite3.connect('rack.db')
cur = conn.cursor()
cur.execute('SELECT * FROM INVENTORY WHERE BARCODE = "719978859014"')
rows = cur.fetchall()
if rows:
    print(f"Found {len(rows)} row(s) in rack.db:")
    for row in rows:
        print(row)
else:
    print("No rows found in rack.db for barcode 719978859014")
conn.close()

print("\n=== Checking searchRack.db ===")
# Check searchRack.db
conn = sqlite3.connect('searchRack.db')
cur = conn.cursor()
cur.execute('SELECT * FROM SEARCHRACK WHERE BARCODE = "719978859014"')
rows = cur.fetchall()
if rows:
    print(f"Found {len(rows)} row(s) in searchRack.db:")
    for row in rows:
        print(row)
else:
    print("No rows found in searchRack.db for barcode 719978859014")

# Also check table structure
cur.execute('PRAGMA table_info(SEARCHRACK)')
cols = cur.fetchall()
print("\nsearchRack.db columns:")
for col in cols:
    print(f"  {col[1]} ({col[2]})")
conn.close()
