import sqlite3

conn = sqlite3.connect('bol.db')
cur = conn.cursor()

# Get all prepped items from LOT# 16578199
cur.execute('''
    SELECT upc, good_qty, bad_qty, original_qty, unchecked_qty
    FROM bol_items 
    WHERE lot_number = '16578199' 
    AND upc NOT LIKE '%-%'
    AND (good_qty > 0 OR bad_qty > 0)
    ORDER BY upc
''')

items = cur.fetchall()

print("=" * 60)
print("UPCs PREPPED UNDER LOT# 16578199")
print("=" * 60)
print()

for upc, good, bad, original, unchecked in items:
    status = f"Good: {good}" if good > 0 else f"Bad: {bad}"
    print(f"{upc} ({status}, Original: {original})")

print()
print("=" * 60)
print(f"Total: {len(items)} items prepped from LOT# 16578199")
print("=" * 60)

conn.close()
