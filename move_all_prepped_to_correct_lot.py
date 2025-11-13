import sqlite3

conn = sqlite3.connect('bol.db')
cur = conn.cursor()

print("="*80)
print("FINDING ALL PREPPED ITEMS IN WRONG LOTS")
print("="*80)
print("\nSearching for items with good_qty > 0 that are NOT in LOT 16423315...\n")

# Find all items with good_qty > 0 that are NOT in LOT 16423315
cur.execute('''
    SELECT id, upc, item_description, lot_number, good_qty, unchecked_qty, original_qty, image_url, import_date
    FROM bol_items
    WHERE good_qty > 0 
    AND lot_number != '16423315'
    ORDER BY lot_number, upc
''')

wrong_lot_items = cur.fetchall()

if not wrong_lot_items:
    print("✅ No items found! All prepped items are already in LOT 16423315.")
    conn.close()
    exit(0)

print(f"Found {len(wrong_lot_items)} items prepped in wrong LOTs:\n")
print("-"*80)

# Group by LOT for reporting
from collections import defaultdict
by_lot = defaultdict(list)

for item in wrong_lot_items:
    id_val, upc, desc, lot, good, unch, orig, image, imp_date = item
    by_lot[lot].append({
        'id': id_val,
        'upc': upc,
        'desc': desc,
        'good': good,
        'unchecked': unch,
        'original': orig,
        'image': image,
        'import_date': imp_date
    })

# Show summary
for lot, items in sorted(by_lot.items()):
    total_good = sum(item['good'] for item in items)
    print(f"\nLOT {lot}: {len(items)} items, {total_good} total good qty")
    for item in items:
        desc_short = (item['desc'][:40] + '...') if item['desc'] and len(item['desc']) > 40 else (item['desc'] or 'N/A')
        print(f"  {item['upc']}: good={item['good']}, unch={item['unchecked']}, orig={item['original']}")
        print(f"    {desc_short}")

print("\n" + "="*80)
response = input(f"\nMove all {len(wrong_lot_items)} items to LOT 16423315? (yes/no): ").strip().lower()

if response != 'yes':
    print("❌ Cancelled")
    conn.close()
    exit(0)

print("\n" + "="*80)
print("MOVING ITEMS TO LOT 16423315")
print("="*80)

moved_count = 0

for item in wrong_lot_items:
    id_val, upc, desc, old_lot, good, unch, orig, image, imp_date = item
    
    print(f"\n📦 {upc} (from LOT {old_lot}):")
    print(f"   Current: good={good}, unchecked={unch}, original={orig}")
    
    # Check if UPC already exists in LOT 16423315
    cur.execute('''
        SELECT id, good_qty, unchecked_qty, original_qty
        FROM bol_items
        WHERE upc = ? AND lot_number = ?
    ''', (upc, '16423315'))
    
    existing_16423315 = cur.fetchone()
    
    if existing_16423315:
        # UPC already exists in LOT 16423315 - add to existing entry
        existing_id, existing_good, existing_unch, existing_orig = existing_16423315
        
        new_good = existing_good + good
        new_orig = existing_orig + good  # Add the good qty to original
        
        cur.execute('''
            UPDATE bol_items
            SET good_qty = ?, original_qty = ?, quantity = ?
            WHERE id = ?
        ''', (new_good, new_orig, new_good, existing_id))
        
        print(f"   ✅ Added to existing LOT 16423315 entry: good {existing_good}→{new_good}, orig {existing_orig}→{new_orig}")
    else:
        # Create new entry in LOT 16423315
        cur.execute('''
            INSERT INTO bol_items
            (upc, item_description, image_url, quantity, original_qty, good_qty, bad_qty, unchecked_qty,
             lot_number, import_date, temporary)
            VALUES (?, ?, ?, ?, ?, ?, 0, 0, ?, ?, 0)
        ''', (upc, desc, image, good, good, good, '16423315', imp_date))
        
        print(f"   ✅ Created new entry in LOT 16423315: good={good}, unchecked=0, original={good}")
    
    # Update original LOT: remove from good_qty, restore to unchecked_qty
    new_good_old = 0  # Set to 0
    new_unch_old = unch + good  # Restore good qty back to unchecked
    
    cur.execute('''
        UPDATE bol_items
        SET good_qty = ?, unchecked_qty = ?, quantity = ?
        WHERE id = ?
    ''', (new_good_old, new_unch_old, new_good_old, id_val))
    
    print(f"   ✅ Updated LOT {old_lot}: good={good}→0, unchecked={unch}→{new_unch_old}")
    
    # Verify invariant
    total_check = new_good_old + 0 + new_unch_old
    if total_check == orig:
        print(f"   ✅ Invariant OK: {orig} = {new_good_old} + 0 + {new_unch_old}")
    else:
        print(f"   ⚠️ WARNING: Invariant issue! {orig} != {total_check}")
    
    moved_count += 1

conn.commit()
conn.close()

print("\n" + "="*80)
print(f"✅ Successfully moved {moved_count} items to LOT 16423315!")
print("="*80)
