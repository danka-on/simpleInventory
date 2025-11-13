import sqlite3

conn = sqlite3.connect('bol.db')
cur = conn.cursor()

# The 4 UPCs to move with their quantities
upcs_to_move = {
    '48552491662': 1,   # Good: 1 of 2
    '48552584265': 1,   # Good: 1 of 2
    '608084003797': 1,  # Good: 1 of 3
    '885991237136': 1   # Good: 1 of 5
}

print("Creating entries in LOT 16423315 and moving good quantities from LOT 16578199\n")
print("="*80)

for upc, good_qty_to_move in upcs_to_move.items():
    print(f"\n📦 {upc}:")
    
    # Get current state from LOT 16578199
    cur.execute('''
        SELECT id, item_description, image_url, good_qty, unchecked_qty, original_qty, import_date
        FROM bol_items 
        WHERE upc = ? AND lot_number = ?
    ''', (upc, '16578199'))
    
    lot_16578199 = cur.fetchone()
    
    if not lot_16578199:
        print(f"   ❌ Not found in LOT 16578199")
        continue
    
    id_16578199, desc, image, good_qty, unchecked_qty, original_qty, import_date = lot_16578199
    
    print(f"   From LOT 16578199: good={good_qty}, unchecked={unchecked_qty}, original={original_qty}")
    
    if good_qty < good_qty_to_move:
        print(f"   ❌ ERROR: Not enough good qty ({good_qty}) to move {good_qty_to_move}")
        continue
    
    # Create new entry in LOT 16423315 with the good quantity
    cur.execute('''
        INSERT INTO bol_items 
        (upc, item_description, image_url, quantity, original_qty, good_qty, bad_qty, unchecked_qty, 
         lot_number, import_date, temporary)
        VALUES (?, ?, ?, ?, ?, ?, 0, 0, ?, ?, 0)
    ''', (upc, desc, image, good_qty_to_move, good_qty_to_move, good_qty_to_move, '16423315', import_date))
    
    print(f"   ✅ Created in LOT 16423315: good={good_qty_to_move}, unchecked=0, original={good_qty_to_move}")
    
    # Update LOT 16578199: subtract from good_qty, add back to unchecked_qty
    new_good_16578199 = good_qty - good_qty_to_move
    new_unchecked_16578199 = unchecked_qty + good_qty_to_move
    new_qty_display = new_good_16578199  # quantity = good_qty
    
    cur.execute('''
        UPDATE bol_items 
        SET good_qty = ?, unchecked_qty = ?, quantity = ?
        WHERE id = ?
    ''', (new_good_16578199, new_unchecked_16578199, new_qty_display, id_16578199))
    
    print(f"   ✅ Updated LOT 16578199: good={new_good_16578199}, unchecked={new_unchecked_16578199}")
    
    # Verify invariant
    new_total = new_good_16578199 + 0 + new_unchecked_16578199  # good + bad + unchecked
    if new_total == original_qty:
        print(f"   ✅ Invariant maintained: {original_qty} = {new_good_16578199} + 0 + {new_unchecked_16578199}")
    else:
        print(f"   ⚠️ WARNING: Invariant broken! {original_qty} != {new_total}")

conn.commit()
conn.close()

print("\n" + "="*80)
print("✅ Transfer complete!")
print("\nNOTE: items_prep_status was NOT modified - those 'good' entries still reference")
print("the original UPC. The prep history remains intact, but the quantities are now")
print("correctly allocated to LOT 16423315.")
print("="*80)
