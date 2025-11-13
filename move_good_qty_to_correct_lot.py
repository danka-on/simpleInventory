import sqlite3

conn = sqlite3.connect('bol.db')
cur = conn.cursor()

# The 4 UPCs to update
upcs_to_move = [
    '48552491662',   # Good: 1
    '48552584265',   # Good: 1
    '608084003797',  # Good: 1
    '885991237136'   # Good: 1
]

print("Moving good quantities from LOT 16578199 → LOT 16423315\n")

for upc in upcs_to_move:
    # Check if UPC exists in LOT 16423315
    cur.execute('''
        SELECT id, good_qty, unchecked_qty, original_qty, lot_number
        FROM bol_items 
        WHERE upc = ? AND lot_number = ?
    ''', (upc, '16423315'))
    
    lot_16423315 = cur.fetchone()
    
    # Get current state from LOT 16578199
    cur.execute('''
        SELECT id, good_qty, unchecked_qty, original_qty, lot_number
        FROM bol_items 
        WHERE upc = ? AND lot_number = ?
    ''', (upc, '16578199'))
    
    lot_16578199 = cur.fetchone()
    
    if not lot_16578199:
        print(f"⚠️ {upc}: Not found in LOT 16578199")
        continue
    
    good_qty_to_move = lot_16578199[1] or 0
    
    if good_qty_to_move == 0:
        print(f"⚠️ {upc}: No good qty in LOT 16578199 to move")
        continue
    
    print(f"📦 {upc}:")
    print(f"   LOT 16578199: good={lot_16578199[1]}, unchecked={lot_16578199[2]}, original={lot_16578199[3]}")
    
    if lot_16423315:
        print(f"   LOT 16423315: good={lot_16423315[1]}, unchecked={lot_16423315[2]}, original={lot_16423315[3]}")
        
        # Update LOT 16423315: add to good_qty, subtract from unchecked_qty
        new_good = (lot_16423315[1] or 0) + good_qty_to_move
        new_unchecked = (lot_16423315[2] or 0) - good_qty_to_move
        
        if new_unchecked < 0:
            print(f"   ❌ ERROR: LOT 16423315 doesn't have enough unchecked qty ({lot_16423315[2]}) to move {good_qty_to_move}")
            continue
        
        cur.execute('''
            UPDATE bol_items 
            SET good_qty = ?, unchecked_qty = ?, quantity = ?
            WHERE id = ?
        ''', (new_good, new_unchecked, new_good, lot_16423315[0]))
        
        print(f"   ✅ LOT 16423315 updated: good={new_good}, unchecked={new_unchecked}")
        
    else:
        print(f"   ⚠️ LOT 16423315 doesn't exist for this UPC - cannot move quantities")
        continue
    
    # Update LOT 16578199: reset good_qty to 0, restore to unchecked_qty
    new_unchecked_16578199 = (lot_16578199[2] or 0) + good_qty_to_move
    
    cur.execute('''
        UPDATE bol_items 
        SET good_qty = 0, unchecked_qty = ?, quantity = 0
        WHERE id = ?
    ''', (new_unchecked_16578199, lot_16578199[0]))
    
    print(f"   ✅ LOT 16578199 updated: good=0, unchecked={new_unchecked_16578199}")
    
    # Update items_prep_status table if exists
    cur.execute('''
        SELECT upc, status, quantity FROM items_prep_status 
        WHERE upc = ? AND status = 'good'
    ''', (upc,))
    
    prep_status = cur.fetchone()
    if prep_status:
        cur.execute('''
            UPDATE items_prep_status 
            SET quantity = 0
            WHERE upc = ? AND status = 'good'
        ''', (upc,))
        print(f"   ✅ items_prep_status reset for {upc}")
    
    print()

conn.commit()
conn.close()

print("\n✅ Quantity transfer complete!")
