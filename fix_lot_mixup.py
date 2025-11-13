import sqlite3

print("=" * 80)
print("INVESTIGATING LOT# 16578199 vs LOT# 16423315")
print("=" * 80)

conn = sqlite3.connect('bol.db')
cur = conn.cursor()

# Step 1: Find items from LOT# 16578199 that have been checked (good_qty > 0 or bad_qty > 0)
print("\n1. Items from LOT# 16578199 that have been checked:")
print("-" * 80)

cur.execute('''
    SELECT upc, original_qty, good_qty, bad_qty, unchecked_qty, import_date
    FROM bol_items
    WHERE lot_number = '16578199'
    AND upc NOT LIKE '%-%'
    AND (good_qty > 0 OR bad_qty > 0)
    ORDER BY upc
''')

checked_items_16578199 = cur.fetchall()

print(f"Found {len(checked_items_16578199)} checked items in LOT# 16578199:")
print(f"\n{'UPC':<15} | {'Original':>8} | {'Good':>5} | {'Bad':>4} | {'Unchecked':>9} | Import Date")
print("-" * 80)

checked_upcs = []
for row in checked_items_16578199:
    upc, orig, good, bad, unch, imp_date = row
    checked_upcs.append(upc)
    print(f"{upc[:15]:<15} | {orig:>8} | {good:>5} | {bad:>4} | {unch:>9} | {imp_date or 'N/A'}")

if not checked_upcs:
    print("No checked items found in LOT# 16578199. No action needed.")
    conn.close()
    exit(0)

# Step 2: Check if these same UPCs exist in LOT# 16423315
print(f"\n2. Checking if these {len(checked_upcs)} UPCs exist in LOT# 16423315:")
print("-" * 80)

placeholders = ','.join('?' * len(checked_upcs))
cur.execute(f'''
    SELECT upc, original_qty, good_qty, bad_qty, unchecked_qty, import_date
    FROM bol_items
    WHERE lot_number = '16423315'
    AND upc NOT LIKE '%-%'
    AND upc IN ({placeholders})
    ORDER BY upc
''', checked_upcs)

items_in_16423315 = cur.fetchall()

print(f"Found {len(items_in_16423315)} matching items in LOT# 16423315:")
print(f"\n{'UPC':<15} | {'Original':>8} | {'Good':>5} | {'Bad':>4} | {'Unchecked':>9} | Import Date")
print("-" * 80)

matching_upcs = []
for row in items_in_16423315:
    upc, orig, good, bad, unch, imp_date = row
    matching_upcs.append(upc)
    print(f"{upc[:15]:<15} | {orig:>8} | {good:>5} | {bad:>4} | {unch:>9} | {imp_date or 'N/A'}")

if not matching_upcs:
    print("\n⚠️ No matching items found in LOT# 16423315")
    print("These items were only in LOT# 16578199 (not duplicates)")
    conn.close()
    exit(0)

# Step 3: Show what will be transferred
print(f"\n3. TRANSFER PLAN - Moving quantities from LOT# 16578199 to LOT# 16423315:")
print("=" * 80)

for upc in matching_upcs:
    # Get quantities from both LOTs
    cur.execute('''
        SELECT good_qty, bad_qty, original_qty
        FROM bol_items
        WHERE lot_number = '16578199' AND upc = ? AND upc NOT LIKE '%-%'
    ''', (upc,))
    lot_16578199 = cur.fetchone()
    
    cur.execute('''
        SELECT good_qty, bad_qty, unchecked_qty, original_qty
        FROM bol_items
        WHERE lot_number = '16423315' AND upc = ? AND upc NOT LIKE '%-%'
    ''', (upc,))
    lot_16423315 = cur.fetchone()
    
    if lot_16578199 and lot_16423315:
        good_to_move = lot_16578199[0] or 0
        bad_to_move = lot_16578199[1] or 0
        orig_16578199 = lot_16578199[2] or 0
        
        current_good_16423315 = lot_16423315[0] or 0
        current_bad_16423315 = lot_16423315[1] or 0
        current_unchecked_16423315 = lot_16423315[2] or 0
        orig_16423315 = lot_16423315[3] or 0
        
        new_good = current_good_16423315 + good_to_move
        new_bad = current_bad_16423315 + bad_to_move
        new_unchecked = current_unchecked_16423315 - (good_to_move + bad_to_move)
        
        print(f"\nUPC: {upc}")
        print(f"  LOT# 16578199 (source):")
        print(f"    Good: {good_to_move}, Bad: {bad_to_move}")
        print(f"  LOT# 16423315 (destination):")
        print(f"    Before: Good={current_good_16423315}, Bad={current_bad_16423315}, Unchecked={current_unchecked_16423315}")
        print(f"    After:  Good={new_good}, Bad={new_bad}, Unchecked={new_unchecked}")
        
        # Validate we have enough unchecked in LOT# 16423315
        if new_unchecked < 0:
            print(f"    ⚠️ WARNING: Not enough unchecked items in LOT# 16423315!")
            print(f"    Need {good_to_move + bad_to_move} but only have {current_unchecked_16423315}")

# Step 4: Ask for confirmation
print("\n" + "=" * 80)
print("SUMMARY:")
print("=" * 80)
print(f"✓ Found {len(checked_upcs)} checked items in LOT# 16578199")
print(f"✓ {len(matching_upcs)} of these also exist in LOT# 16423315")
print(f"✓ Will transfer good/bad quantities from 16578199 → 16423315")
print(f"✓ Will reset LOT# 16578199 items to unchecked state")

response = input("\nProceed with transfer? (yes/no): ").strip().lower()

if response != 'yes':
    print("❌ Transfer cancelled.")
    conn.close()
    exit(0)

# Step 5: Perform the transfer
print("\n4. EXECUTING TRANSFER:")
print("-" * 80)

transfer_count = 0

for upc in matching_upcs:
    # Get quantities from LOT# 16578199
    cur.execute('''
        SELECT good_qty, bad_qty, original_qty
        FROM bol_items
        WHERE lot_number = '16578199' AND upc = ? AND upc NOT LIKE '%-%'
    ''', (upc,))
    lot_16578199 = cur.fetchone()
    
    # Get quantities from LOT# 16423315
    cur.execute('''
        SELECT good_qty, bad_qty, unchecked_qty
        FROM bol_items
        WHERE lot_number = '16423315' AND upc = ? AND upc NOT LIKE '%-%'
    ''', (upc,))
    lot_16423315 = cur.fetchone()
    
    if lot_16578199 and lot_16423315:
        good_to_move = lot_16578199[0] or 0
        bad_to_move = lot_16578199[1] or 0
        orig_16578199 = lot_16578199[2] or 0
        
        current_good = lot_16423315[0] or 0
        current_bad = lot_16423315[1] or 0
        current_unchecked = lot_16423315[2] or 0
        
        total_to_move = good_to_move + bad_to_move
        new_unchecked = current_unchecked - total_to_move
        
        # Skip if not enough unchecked
        if new_unchecked < 0:
            print(f"⚠️ Skipping {upc}: Not enough unchecked in LOT# 16423315")
            continue
        
        # Update LOT# 16423315: Add good/bad, reduce unchecked
        cur.execute('''
            UPDATE bol_items
            SET good_qty = ?,
                bad_qty = ?,
                unchecked_qty = ?,
                quantity = ?
            WHERE lot_number = '16423315' AND upc = ? AND upc NOT LIKE '%-%'
        ''', (current_good + good_to_move, 
              current_bad + bad_to_move, 
              new_unchecked,
              current_good + good_to_move,  # quantity = good_qty
              upc))
        
        # Reset LOT# 16578199: Set all back to unchecked
        cur.execute('''
            UPDATE bol_items
            SET good_qty = 0,
                bad_qty = 0,
                unchecked_qty = ?,
                quantity = 0
            WHERE lot_number = '16578199' AND upc = ? AND upc NOT LIKE '%-%'
        ''', (orig_16578199, upc))
        
        transfer_count += 1
        print(f"✓ Transferred {upc}: Good+{good_to_move}, Bad+{bad_to_move}")

conn.commit()

# Step 6: Update items_prep_status if needed
print("\n5. Checking items_prep_status table:")
print("-" * 80)

# The items_prep_status table tracks by UPC, not LOT
# So we don't need to update it - it's already correct for LOT# 16423315

print("✓ items_prep_status tracks by UPC (not LOT), so no changes needed")

print("\n" + "=" * 80)
print("TRANSFER COMPLETE!")
print("=" * 80)
print(f"✓ Transferred quantities for {transfer_count} items")
print(f"✓ LOT# 16578199 items reset to unchecked state")
print(f"✓ LOT# 16423315 items updated with correct quantities")

conn.close()
