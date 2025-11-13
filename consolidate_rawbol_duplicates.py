import sqlite3
from collections import defaultdict

print("=" * 100)
print("CONSOLIDATING SAME-LOT DUPLICATES IN RAWBOL.DB")
print("=" * 100)

conn = sqlite3.connect('rawbol.db')
cur = conn.cursor()

# Find all same-LOT duplicates
cur.execute('''
    SELECT upc, lot_number, COUNT(*) as count
    FROM raw_bol_items
    WHERE upc IS NOT NULL AND upc != ''
    GROUP BY upc, lot_number
    HAVING COUNT(*) > 1
    ORDER BY COUNT(*) DESC
''')

duplicates = cur.fetchall()
print(f"\nFound {len(duplicates)} UPC+LOT combinations with duplicates")

# For each duplicate group, consolidate
consolidated_count = 0
deleted_count = 0

for upc, lot_number, count in duplicates:
    # Get all entries for this UPC+LOT
    cur.execute('''
        SELECT id, quantity, item_description, avg_cost, image_url, bol_location, import_date, created_at
        FROM raw_bol_items
        WHERE upc = ? AND lot_number = ?
        ORDER BY id
    ''', (upc, lot_number))
    
    entries = cur.fetchall()
    
    if len(entries) <= 1:
        continue
    
    # Keep the first entry, sum quantities from all
    keep_id = entries[0][0]
    total_qty = sum(e[1] for e in entries if e[1] is not None)
    
    # Get best non-null values for other fields (prefer from first entry)
    best_desc = next((e[2] for e in entries if e[2]), None)
    best_cost = next((e[3] for e in entries if e[3]), None)
    best_image = next((e[4] for e in entries if e[4]), None)
    best_location = next((e[5] for e in entries if e[5]), None)
    best_import_date = entries[0][6]
    best_created_at = entries[0][7]
    
    # Update the kept entry with consolidated quantity
    cur.execute('''
        UPDATE raw_bol_items
        SET quantity = ?,
            item_description = ?,
            avg_cost = ?,
            image_url = ?,
            bol_location = ?,
            import_date = ?,
            created_at = ?
        WHERE id = ?
    ''', (total_qty, best_desc, best_cost, best_image, best_location, best_import_date, best_created_at, keep_id))
    
    # Delete the duplicate entries (all except the first)
    for entry in entries[1:]:
        delete_id = entry[0]
        cur.execute('DELETE FROM raw_bol_items WHERE id = ?', (delete_id,))
        deleted_count += 1
    
    consolidated_count += 1
    print(f"  ✓ Consolidated {upc} LOT {lot_number}: {count} entries → 1 (total qty: {total_qty})")

conn.commit()

print("\n" + "=" * 100)
print(f"\n✅ CONSOLIDATION COMPLETE:")
print(f"  Consolidated {consolidated_count} UPC+LOT combinations")
print(f"  Deleted {deleted_count} duplicate rows")

# Verify results
cur.execute('SELECT COUNT(DISTINCT upc) as unique_upcs, COUNT(*) as total_rows, SUM(quantity) as total_qty FROM raw_bol_items')
row = cur.fetchone()
print(f"\n📊 NEW STATS:")
print(f"  Unique UPCs: {row[0]}")
print(f"  Total rows: {row[1]}")
print(f"  Total quantity: {row[2]}")

# Check for remaining same-LOT duplicates
cur.execute('''
    SELECT COUNT(*)
    FROM (
        SELECT upc, lot_number, COUNT(*) as count
        FROM raw_bol_items
        WHERE upc IS NOT NULL AND upc != ''
        GROUP BY upc, lot_number
        HAVING COUNT(*) > 1
    )
''')
remaining = cur.fetchone()[0]
print(f"  Remaining same-LOT duplicates: {remaining}")

conn.close()
