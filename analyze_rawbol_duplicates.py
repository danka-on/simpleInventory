import sqlite3

conn = sqlite3.connect('rawbol.db')
cur = conn.cursor()

# Find UPCs that appear multiple times
cur.execute('''
    SELECT 
        upc,
        COUNT(*) as total_entries,
        COUNT(DISTINCT lot_number) as num_lots,
        GROUP_CONCAT(DISTINCT lot_number) as lots,
        SUM(quantity) as total_qty
    FROM raw_bol_items
    WHERE upc IS NOT NULL AND upc != ''
    GROUP BY upc
    HAVING COUNT(*) > 1
    ORDER BY COUNT(*) DESC
    LIMIT 50
''')

duplicates = cur.fetchall()

print(f"Found {len(duplicates)} UPCs with duplicates:\n")
print("=" * 100)

# Categorize duplicates
cross_lot_duplicates = 0
same_lot_duplicates = 0
mixed_duplicates = 0

for upc, total_entries, num_lots, lots, total_qty in duplicates[:20]:  # Show first 20
    print(f"\nUPC: {upc}")
    print(f"  Total entries: {total_entries}")
    print(f"  Appears in {num_lots} LOT(s): {lots}")
    print(f"  Total quantity: {total_qty}")
    
    if num_lots > 1:
        cross_lot_duplicates += 1
        print(f"  ✅ CROSS-LOT: Same item purchased multiple times")
    elif num_lots == 1:
        same_lot_duplicates += 1
        print(f"  ⚠️ SAME-LOT: Multiple entries in LOT {lots}")
    else:
        mixed_duplicates += 1

print("\n" + "=" * 100)
print("\n📊 SUMMARY:")
print(f"  Cross-LOT duplicates (intended): {cross_lot_duplicates}")
print(f"  Same-LOT duplicates (check): {same_lot_duplicates}")
print(f"  Mixed/Other: {mixed_duplicates}")

# Check a specific same-LOT duplicate in detail
cur.execute('''
    SELECT upc, COUNT(*) as entries, lot_number
    FROM raw_bol_items
    WHERE upc IS NOT NULL
    GROUP BY upc, lot_number
    HAVING COUNT(*) > 1
    LIMIT 5
''')

print("\n\n🔍 DETAILED LOOK at same-LOT duplicates:")
print("=" * 100)
same_lot_dups = cur.fetchall()

for upc, entries, lot in same_lot_dups:
    print(f"\n📦 UPC {upc} appears {entries} times in LOT {lot}")
    cur.execute('''
        SELECT id, item_description, quantity, import_date 
        FROM raw_bol_items 
        WHERE upc = ? AND lot_number = ?
    ''', (upc, lot))
    
    rows = cur.fetchall()
    for row_id, desc, qty, imp_date in rows:
        desc_display = (desc[:50] + '...') if desc and len(desc) > 50 else (desc or 'N/A')
        print(f"  - ID {row_id}: {desc_display} | Qty: {qty} | Date: {imp_date}")

conn.close()
