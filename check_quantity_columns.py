import sqlite3

# Check difference between quantity and original_qty
conn = sqlite3.connect('bol.db')
cur = conn.cursor()

print("=" * 80)
print("Sample data: quantity vs original_qty")
print("=" * 80)

cur.execute('''
    SELECT upc, quantity, original_qty, good_qty, bad_qty, unchecked_qty 
    FROM bol_items 
    WHERE upc NOT LIKE '%-%' 
    LIMIT 20
''')

print(f"{'UPC':<15} | {'Qty':>5} | {'Original':>8} | {'Good':>4} | {'Bad':>3} | {'Unchecked':>9}")
print("-" * 80)

for row in cur.fetchall():
    print(f"{row[0][:15]:<15} | {row[1] or 0:>5} | {row[2] or 0:>8} | {row[3] or 0:>4} | {row[4] or 0:>3} | {row[5] or 0:>9}")

print("\n" + "=" * 80)
print("Statistics:")
print("=" * 80)

# Check how many have different values
cur.execute('''
    SELECT 
        COUNT(*) as total,
        SUM(CASE WHEN quantity = original_qty THEN 1 ELSE 0 END) as same,
        SUM(CASE WHEN quantity != original_qty THEN 1 ELSE 0 END) as different,
        SUM(CASE WHEN quantity = 0 AND original_qty = 0 THEN 1 ELSE 0 END) as both_zero
    FROM bol_items
    WHERE upc NOT LIKE '%-%'
''')

stats = cur.fetchone()
print(f"Total base items: {stats[0]}")
print(f"quantity = original_qty: {stats[1]}")
print(f"quantity ≠ original_qty: {stats[2]}")
print(f"Both are 0: {stats[3]}")

# Check relationship between quantity and good_qty
cur.execute('''
    SELECT 
        COUNT(*) as total,
        SUM(CASE WHEN quantity = good_qty THEN 1 ELSE 0 END) as qty_equals_good,
        SUM(CASE WHEN quantity = unchecked_qty THEN 1 ELSE 0 END) as qty_equals_unchecked
    FROM bol_items
    WHERE upc NOT LIKE '%-%'
''')

rel = cur.fetchone()
print(f"\nRelationship:")
print(f"quantity = good_qty: {rel[1]}")
print(f"quantity = unchecked_qty: {rel[2]}")

# Check items with zero original_qty
print("\n" + "=" * 80)
print("Items with zero quantities:")
print("=" * 80)

cur.execute('''
    SELECT COUNT(*) 
    FROM bol_items 
    WHERE upc NOT LIKE '%-%' 
    AND (original_qty IS NULL OR original_qty = 0)
''')
zero_count = cur.fetchone()[0]
print(f"Items with original_qty = 0 or NULL: {zero_count}")

# Sample of zero items
cur.execute('''
    SELECT upc, lot_number, quantity, original_qty 
    FROM bol_items 
    WHERE upc NOT LIKE '%-%' 
    AND (original_qty IS NULL OR original_qty = 0)
    LIMIT 10
''')

print(f"\n{'UPC':<15} | {'LOT':<12} | {'Qty':>5} | {'Original':>8}")
print("-" * 60)
for row in cur.fetchall():
    print(f"{row[0][:15]:<15} | {(row[1] or 'N/A')[:12]:<12} | {row[2] or 0:>5} | {row[3] or 0:>8}")

# Check if these items exist in rawbol.db with quantities
print("\n" + "=" * 80)
print("Checking rawbol.db for these items:")
print("=" * 80)

rawbol_conn = sqlite3.connect('rawbol.db')
rawbol_cur = rawbol_conn.cursor()

cur.execute('''
    SELECT upc 
    FROM bol_items 
    WHERE upc NOT LIKE '%-%' 
    AND (original_qty IS NULL OR original_qty = 0)
    LIMIT 5
''')

zero_upcs = [r[0] for r in cur.fetchall()]

for upc in zero_upcs:
    rawbol_cur.execute('SELECT UPC, QTY FROM rawbol WHERE UPC = ? COLLATE NOCASE', (upc,))
    raw_row = rawbol_cur.fetchone()
    if raw_row:
        print(f"UPC {upc}: Found in rawbol with QTY={raw_row[1]}")
    else:
        print(f"UPC {upc}: NOT FOUND in rawbol")

rawbol_conn.close()
conn.close()

print("\n" + "=" * 80)
print("CONCLUSION:")
print("=" * 80)
print("quantity column appears to track current 'display' quantity")
print("original_qty should be immutable baseline from import")
print("Recommendation: Keep both, but fix zeros by backfilling from rawbol.db")
