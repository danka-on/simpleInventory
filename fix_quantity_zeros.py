import sqlite3

print("=" * 80)
print("FIXING QUANTITY ISSUES IN bol.db")
print("=" * 80)

# Connect to databases
bol_conn = sqlite3.connect('bol.db')
bol_cur = bol_conn.cursor()

rawbol_conn = sqlite3.connect('rawbol.db')
rawbol_cur = rawbol_conn.cursor()

# Step 1: Check current state
print("\n1. Current state analysis:")
print("-" * 80)

bol_cur.execute('''
    SELECT 
        COUNT(*) as total,
        SUM(CASE WHEN original_qty = 0 OR original_qty IS NULL THEN 1 ELSE 0 END) as zero_original,
        SUM(CASE WHEN quantity = original_qty THEN 1 ELSE 0 END) as qty_equals_original
    FROM bol_items
    WHERE upc NOT LIKE '%-%'
''')

stats = bol_cur.fetchone()
print(f"Total base items: {stats[0]}")
print(f"Items with original_qty = 0: {stats[1]}")
print(f"Items where quantity = original_qty: {stats[2]}")

# Step 2: Understand quantity vs original_qty purpose
print("\n2. Column purposes:")
print("-" * 80)
print("✓ original_qty: Immutable baseline - what was received in BOL")
print("✓ quantity: Current display value (used by old code, = good_qty or unchecked_qty)")
print("✓ Recommendation: Keep both, but make quantity = good_qty (for backward compatibility)")

# Step 3: Fix zero original_qty by backfilling from rawbol
print("\n3. Fixing zero original_qty values:")
print("-" * 80)

bol_cur.execute('''
    SELECT upc, lot_number, quantity, original_qty 
    FROM bol_items 
    WHERE upc NOT LIKE '%-%' 
    AND (original_qty = 0 OR original_qty IS NULL)
''')

zero_items = bol_cur.fetchall()
print(f"Found {len(zero_items)} items with zero original_qty")

fixed_count = 0
not_found_count = 0

for upc, lot_number, current_qty, original_qty in zero_items:
    # Try to find in rawbol by UPC and LOT
    if lot_number and lot_number.lower() not in ('nan', 'none', 'null'):
        rawbol_cur.execute('''
            SELECT COALESCE(quantity, 1) as qty
            FROM raw_bol_items 
            WHERE upc = ? COLLATE NOCASE 
            AND lot_number = ? COLLATE NOCASE
            LIMIT 1
        ''', (upc, lot_number))
    else:
        # If no valid LOT, try just by UPC
        rawbol_cur.execute('''
            SELECT COALESCE(quantity, 1) as qty
            FROM raw_bol_items 
            WHERE upc = ? COLLATE NOCASE
            ORDER BY import_date DESC
            LIMIT 1
        ''', (upc,))
    
    raw_row = rawbol_cur.fetchone()
    
    if raw_row:
        rawbol_qty = raw_row[0] or 1
        
        # Update bol_items with rawbol quantity
        bol_cur.execute('''
            UPDATE bol_items 
            SET original_qty = ?,
                unchecked_qty = ?,
                quantity = ?
            WHERE upc = ? COLLATE NOCASE
        ''', (rawbol_qty, rawbol_qty, rawbol_qty, upc))
        
        fixed_count += 1
        if fixed_count <= 10:
            print(f"  ✓ Fixed {upc}: original_qty = {rawbol_qty}")
    else:
        not_found_count += 1
        if not_found_count <= 5:
            print(f"  ✗ Not found in rawbol: {upc}")

print(f"\nFixed: {fixed_count}")
print(f"Not found in rawbol: {not_found_count}")

# Step 4: Standardize quantity column to match good_qty (for backward compatibility)
print("\n4. Standardizing 'quantity' column:")
print("-" * 80)
print("Setting quantity = good_qty for consistency with old code...")

bol_cur.execute('''
    UPDATE bol_items
    SET quantity = COALESCE(good_qty, 0)
    WHERE upc NOT LIKE '%-%'
    AND quantity != COALESCE(good_qty, 0)
''')

updated = bol_cur.rowcount
print(f"Updated {updated} items where quantity != good_qty")

# Step 5: Verify invariant
print("\n5. Verifying invariant (original = good + bad + unchecked):")
print("-" * 80)

bol_cur.execute('''
    SELECT upc, original_qty, good_qty, bad_qty, unchecked_qty
    FROM bol_items
    WHERE upc NOT LIKE '%-%'
    AND (original_qty != COALESCE(good_qty, 0) + COALESCE(bad_qty, 0) + COALESCE(unchecked_qty, 0))
    LIMIT 10
''')

invalid = bol_cur.fetchall()
if invalid:
    print(f"⚠️ Found {len(invalid)} items with invalid invariant:")
    for row in invalid[:5]:
        upc, orig, good, bad, unch = row
        print(f"  {upc}: {orig} ≠ {good} + {bad} + {unch} = {good + bad + unch}")
else:
    print("✓ All items have valid invariant!")

# Commit changes
bol_conn.commit()

# Step 6: Final summary
print("\n" + "=" * 80)
print("FINAL SUMMARY")
print("=" * 80)

bol_cur.execute('''
    SELECT 
        COUNT(*) as total,
        SUM(COALESCE(original_qty, 0)) as total_original,
        SUM(COALESCE(good_qty, 0)) as total_good,
        SUM(COALESCE(bad_qty, 0)) as total_bad,
        SUM(COALESCE(unchecked_qty, 0)) as total_unchecked,
        SUM(CASE WHEN original_qty = 0 OR original_qty IS NULL THEN 1 ELSE 0 END) as still_zero
    FROM bol_items
    WHERE upc NOT LIKE '%-%'
''')

final_stats = bol_cur.fetchone()
print(f"Total items: {final_stats[0]}")
print(f"Total original qty: {final_stats[1]}")
print(f"Total good: {final_stats[2]}")
print(f"Total bad: {final_stats[3]}")
print(f"Total unchecked: {final_stats[4]}")
print(f"Items still with zero original_qty: {final_stats[5]}")

print("\n" + "=" * 80)
print("Column Roles:")
print("=" * 80)
print("• quantity: Display value (= good_qty) - for backward compatibility")
print("• original_qty: Immutable baseline from BOL import")
print("• good_qty: Items marked as good (ready to list)")
print("• bad_qty: Items marked as bad/defective")
print("• unchecked_qty: Items not yet prepped")
print("=" * 80)

# Close connections
bol_conn.close()
rawbol_conn.close()

print("\n✅ Fix complete!")
