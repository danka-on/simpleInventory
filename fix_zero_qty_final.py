import sqlite3

bol_conn = sqlite3.connect('bol.db')
bol_cur = bol_conn.cursor()

print("=" * 80)
print("ANALYZING ITEMS WITH ZERO ORIGINAL_QTY")
print("=" * 80)

# Get items with zero original_qty
bol_cur.execute('''
    SELECT upc, lot_number, import_date, temporary
    FROM bol_items
    WHERE upc NOT LIKE '%-%'
    AND (original_qty = 0 OR original_qty IS NULL)
    LIMIT 20
''')

zero_items = bol_cur.fetchall()

print(f"\nFound {len(zero_items)} items with zero original_qty")
print(f"\n{'UPC':<15} | {'LOT':<15} | {'Import Date':<12} | Temp")
print("-" * 70)

for upc, lot, imp_date, temp in zero_items:
    lot_display = (lot or 'NULL')[:15]
    date_display = (imp_date or 'NULL')[:12]
    print(f"{upc[:15]:<15} | {lot_display:<15} | {date_display:<12} | {temp or 0}")

# Check if these are temporary items
bol_cur.execute('''
    SELECT 
        COUNT(*) as total_zero,
        SUM(CASE WHEN temporary = 1 THEN 1 ELSE 0 END) as temp_items,
        SUM(CASE WHEN lot_number IS NULL OR lot_number = 'nan' THEN 1 ELSE 0 END) as no_lot
    FROM bol_items
    WHERE upc NOT LIKE '%-%'
    AND (original_qty = 0 OR original_qty IS NULL)
''')

stats = bol_cur.fetchone()
print(f"\nOf {stats[0]} items with zero qty:")
print(f"  - Temporary items: {stats[1]}")
print(f"  - No LOT number: {stats[2]}")

# Decision: Set default quantity for items not in rawbol
print("\n" + "=" * 80)
print("SOLUTION:")
print("=" * 80)
print("These items are NOT in rawbol.db (likely old/deleted LOTs or manual entries)")
print("Options:")
print("  1. Delete them (they're orphaned data)")
print("  2. Set original_qty = 1 as default")
print("  3. Leave them as-is (they won't show in stats)")
print("\nRecommendation: Set original_qty = 1 for non-temporary items")
print("                Delete temporary items with zero qty")

# Apply fix
print("\n" + "=" * 80)
print("APPLYING FIX:")
print("=" * 80)

# Delete temporary items with zero qty (these are leftover from old scans)
bol_cur.execute('''
    DELETE FROM bol_items
    WHERE upc NOT LIKE '%-%'
    AND (original_qty = 0 OR original_qty IS NULL)
    AND temporary = 1
''')
deleted_temp = bol_cur.rowcount
print(f"✓ Deleted {deleted_temp} temporary items with zero qty")

# Set original_qty = 1 for remaining permanent items
bol_cur.execute('''
    UPDATE bol_items
    SET original_qty = 1,
        unchecked_qty = 1,
        quantity = COALESCE(good_qty, 0)
    WHERE upc NOT LIKE '%-%'
    AND (original_qty = 0 OR original_qty IS NULL)
    AND (temporary IS NULL OR temporary = 0)
''')
updated = bol_cur.rowcount
print(f"✓ Set original_qty = 1 for {updated} permanent items")

bol_conn.commit()

# Final check
bol_cur.execute('''
    SELECT COUNT(*)
    FROM bol_items
    WHERE upc NOT LIKE '%-%'
    AND (original_qty = 0 OR original_qty IS NULL)
''')
remaining = bol_cur.fetchone()[0]

print(f"\nRemaining items with zero original_qty: {remaining}")

if remaining == 0:
    print("✅ All items now have valid original_qty!")

bol_conn.close()
