import sqlite3

print("=" * 80)
print("DEEPER INVESTIGATION - LOT# 16578199 Checked Items")
print("=" * 80)

conn = sqlite3.connect('bol.db')
cur = conn.cursor()

# Get the checked items from LOT# 16578199
cur.execute('''
    SELECT upc, original_qty, good_qty, bad_qty, unchecked_qty, import_date
    FROM bol_items
    WHERE lot_number = '16578199'
    AND upc NOT LIKE '%-%'
    AND (good_qty > 0 OR bad_qty > 0)
    ORDER BY upc
''')

checked_items = cur.fetchall()
checked_upcs = [item[0] for item in checked_items]

print(f"\nFound {len(checked_items)} checked items in LOT# 16578199")
print("These should NOT have been processed (LOT not received yet)")
print("\n" + "-" * 80)

# Check if these UPCs exist in ANY other LOT
print("\n1. Searching for these UPCs in OTHER LOTs:")
print("-" * 80)

for upc in checked_upcs:
    cur.execute('''
        SELECT lot_number, original_qty, good_qty, bad_qty, unchecked_qty, import_date
        FROM bol_items
        WHERE upc = ? COLLATE NOCASE
        AND upc NOT LIKE '%-%'
        AND lot_number != '16578199'
        ORDER BY import_date DESC
    ''', (upc,))
    
    other_lots = cur.fetchall()
    
    print(f"\nUPC: {upc}")
    if other_lots:
        print(f"  Found in {len(other_lots)} other LOT(s):")
        for lot, orig, good, bad, unch, imp_date in other_lots:
            print(f"    LOT# {lot}: Original={orig}, Good={good}, Bad={bad}, Unchecked={unch}, Date={imp_date}")
    else:
        print(f"  NOT found in any other LOT")

# Check items_prep_status to see when these were marked
print("\n" + "=" * 80)
print("2. Checking items_prep_status (when were they marked?):")
print("-" * 80)

cur.execute('''
    SELECT upc, status, quantity, updated_at
    FROM items_prep_status
    WHERE upc IN ({})
    ORDER BY upc, updated_at DESC
'''.format(','.join('?' * len(checked_upcs))), checked_upcs)

prep_status = cur.fetchall()

if prep_status:
    for upc, status, qty, updated in prep_status:
        print(f"UPC: {upc} - Status: {status}, Qty: {qty}, Updated: {updated}")
else:
    print("No entries in items_prep_status (unexpected!)")

# Show the import dates
print("\n" + "=" * 80)
print("3. LOT Import Dates:")
print("-" * 80)

for lot_num in ['16578199', '16423315']:
    cur.execute('''
        SELECT MIN(import_date), MAX(import_date), COUNT(*)
        FROM bol_items
        WHERE lot_number = ? AND upc NOT LIKE '%-%'
    ''', (lot_num,))
    
    min_date, max_date, count = cur.fetchone()
    print(f"LOT# {lot_num}: {count} items, Dates: {min_date} to {max_date}")

# Recommendation
print("\n" + "=" * 80)
print("RECOMMENDATION:")
print("=" * 80)
print("""
Since these 4 items are ONLY in LOT# 16578199 and you haven't received it yet:

Option 1: RESET these items back to unchecked
  - Set good_qty = 0, unchecked_qty = original_qty
  - Keep them in LOT# 16578199 for when you receive it
  - They were marked by mistake

Option 2: DELETE these items from LOT# 16578199
  - Remove them entirely (they shouldn't exist yet)
  - Re-import when you actually receive the LOT

Option 3: MOVE to a different LOT (if they're actually from another shipment)
  - Specify which LOT they should belong to
""")

response = input("\nChoose action (1=Reset, 2=Delete, 3=Manual, 0=Cancel): ").strip()

if response == '1':
    # Reset to unchecked
    print("\nResetting items to unchecked state...")
    for upc in checked_upcs:
        cur.execute('''
            SELECT original_qty FROM bol_items
            WHERE lot_number = '16578199' AND upc = ? AND upc NOT LIKE '%-%'
        ''', (upc,))
        orig = cur.fetchone()[0]
        
        cur.execute('''
            UPDATE bol_items
            SET good_qty = 0,
                bad_qty = 0,
                unchecked_qty = ?,
                quantity = 0
            WHERE lot_number = '16578199' AND upc = ? AND upc NOT LIKE '%-%'
        ''', (orig, upc))
        
        print(f"  ✓ Reset {upc} to unchecked ({orig} items)")
    
    conn.commit()
    print("\n✅ Items reset successfully!")

elif response == '2':
    # Delete items
    print("\nDeleting items from LOT# 16578199...")
    for upc in checked_upcs:
        cur.execute('''
            DELETE FROM bol_items
            WHERE lot_number = '16578199' AND upc = ? AND upc NOT LIKE '%-%'
        ''', (upc,))
        print(f"  ✓ Deleted {upc}")
    
    conn.commit()
    print("\n✅ Items deleted successfully!")

elif response == '3':
    print("\n⚠️ Manual action needed - please specify target LOT number")

else:
    print("\n❌ Action cancelled")

conn.close()
