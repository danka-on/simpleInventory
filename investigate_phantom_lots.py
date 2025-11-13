import sqlite3

conn_bol = sqlite3.connect('bol.db')
cur_bol = conn_bol.cursor()

conn_raw = sqlite3.connect('rawbol.db')
cur_raw = conn_raw.cursor()

phantom_lots = ['15705590.0', '15614847.0', '15719744.0']

print("="*80)
print("INVESTIGATING PHANTOM LOT NUMBERS")
print("="*80)

for lot in phantom_lots:
    print(f"\n{'='*80}")
    print(f"LOT# {lot}")
    print("="*80)
    
    # Check bol_items
    cur_bol.execute('''
        SELECT id, upc, item_description, quantity, original_qty, good_qty, bad_qty, 
               unchecked_qty, temporary, import_date
        FROM bol_items
        WHERE lot_number = ?
    ''', (lot,))
    
    items = cur_bol.fetchall()
    
    if items:
        print(f"\nFound {len(items)} items in bol_items:")
        for item in items:
            id_val, upc, desc, qty, orig, good, bad, unch, temp, imp_date = item
            desc_short = (desc[:50] + '...') if desc and len(desc) > 50 else (desc or 'N/A')
            print(f"\n  ID: {id_val}")
            print(f"  UPC: {upc}")
            print(f"  Description: {desc_short}")
            print(f"  Quantities: qty={qty}, orig={orig}, good={good}, bad={bad}, unch={unch}")
            print(f"  Temporary: {temp}")
            print(f"  Import Date: {imp_date}")
    else:
        print(f"\n✓ No items found in bol_items")
    
    # Check raw_bol_items
    cur_raw.execute('''
        SELECT id, upc, item_description, quantity, import_date
        FROM raw_bol_items
        WHERE lot_number = ?
    ''', (lot,))
    
    raw_items = cur_raw.fetchall()
    
    if raw_items:
        print(f"\nFound {len(raw_items)} items in raw_bol_items:")
        for item in raw_items[:5]:  # Show first 5
            id_val, upc, desc, quantity, imp_date = item
            desc_short = (desc[:50] + '...') if desc and len(desc) > 50 else (desc or 'N/A')
            print(f"  {upc}: qty={quantity}, import={imp_date}")
        if len(raw_items) > 5:
            print(f"  ... and {len(raw_items) - 5} more")
    else:
        print(f"\n✓ No items found in raw_bol_items")

print("\n" + "="*80)
print("SUMMARY")
print("="*80)

total_bol_items = 0
total_raw_items = 0

for lot in phantom_lots:
    cur_bol.execute('SELECT COUNT(*) FROM bol_items WHERE lot_number = ?', (lot,))
    bol_count = cur_bol.fetchone()[0]
    
    cur_raw.execute('SELECT COUNT(*) FROM raw_bol_items WHERE lot_number = ?', (lot,))
    raw_count = cur_raw.fetchone()[0]
    
    total_bol_items += bol_count
    total_raw_items += raw_count
    
    print(f"\nLOT# {lot}:")
    print(f"  bol_items: {bol_count}")
    print(f"  raw_bol_items: {raw_count}")

print(f"\nTotal items to delete:")
print(f"  bol_items: {total_bol_items}")
print(f"  raw_bol_items: {total_raw_items}")

if total_bol_items > 0 or total_raw_items > 0:
    print("\n" + "="*80)
    response = input("Delete all these phantom LOT entries? (yes/no): ").strip().lower()
    
    if response == 'yes':
        print("\n" + "="*80)
        print("DELETING PHANTOM LOT ENTRIES")
        print("="*80)
        
        for lot in phantom_lots:
            # Delete from bol_items
            cur_bol.execute('DELETE FROM bol_items WHERE lot_number = ?', (lot,))
            deleted_bol = cur_bol.rowcount
            
            # Delete from raw_bol_items
            cur_raw.execute('DELETE FROM raw_bol_items WHERE lot_number = ?', (lot,))
            deleted_raw = cur_raw.rowcount
            
            if deleted_bol > 0 or deleted_raw > 0:
                print(f"\nLOT# {lot}:")
                if deleted_bol > 0:
                    print(f"  ✅ Deleted {deleted_bol} from bol_items")
                if deleted_raw > 0:
                    print(f"  ✅ Deleted {deleted_raw} from raw_bol_items")
        
        conn_bol.commit()
        conn_raw.commit()
        print("\n" + "="*80)
        print("✅ All phantom LOT entries deleted!")
        print("="*80)
    else:
        print("\n❌ Cancelled")
else:
    print("\n✅ No phantom LOT entries found to delete!")

conn_bol.close()
conn_raw.close()
