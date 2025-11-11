"""
Fix bol.db quantities to match rawbol.db (source of truth)
This script resets quantities in bol.db to exactly match what's in rawbol.db
"""
import sqlite3

def fix_bol_quantities():
    """Reset bol.db quantities to match rawbol.db"""
    
    rawbol_conn = sqlite3.connect('rawbol.db')
    rawbol_conn.row_factory = sqlite3.Row
    rawbol_cur = rawbol_conn.cursor()
    
    bol_conn = sqlite3.connect('bol.db')
    bol_cur = bol_conn.cursor()
    
    print("=== Analyzing bol.db vs rawbol.db quantities ===\n")
    
    # Get all UPCs from rawbol with their correct quantities
    rawbol_cur.execute('''
        SELECT upc, SUM(quantity) as correct_qty
        FROM raw_bol_items
        GROUP BY upc
    ''')
    rawbol_items = {row['upc']: row['correct_qty'] for row in rawbol_cur.fetchall()}
    
    print(f"rawbol.db: {len(rawbol_items)} unique UPCs")
    
    # Get all UPCs from bol.db with lot numbers
    bol_cur.execute('''
        SELECT upc, quantity
        FROM bol_items
        WHERE lot_number IS NOT NULL AND lot_number != '' AND lot_number != 'nan'
    ''')
    bol_items = bol_cur.fetchall()
    
    print(f"bol.db (with LOT numbers): {len(bol_items)} items\n")
    
    # Compare and find discrepancies
    to_update = []
    correct_count = 0
    
    for upc, current_qty in bol_items:
        if upc in rawbol_items:
            correct_qty = rawbol_items[upc]
            if current_qty != correct_qty:
                to_update.append((upc, current_qty, correct_qty))
            else:
                correct_count += 1
    
    print(f"Analysis:")
    print(f"  ✓ {correct_count} items already have correct quantities")
    print(f"  ✗ {len(to_update)} items need quantity correction")
    
    if to_update:
        print(f"\nSample discrepancies (first 10):")
        for upc, current, correct in to_update[:10]:
            print(f"  UPC {upc}: currently {current}, should be {correct} (diff: {current - correct})")
    
    if not to_update:
        print("\n✓ All quantities are correct!")
        rawbol_conn.close()
        bol_conn.close()
        return
    
    # Ask for confirmation
    total_current = sum(item[1] for item in to_update)
    total_correct = sum(item[2] for item in to_update)
    print(f"\nTotal quantity change: {total_current} → {total_correct} (reduction of {total_current - total_correct})")
    
    response = input("\nDo you want to fix these quantities? (yes/no): ").strip().lower()
    
    if response != 'yes':
        print("Aborted.")
        rawbol_conn.close()
        bol_conn.close()
        return
    
    # Update quantities
    print("\nUpdating quantities...")
    for i, (upc, current_qty, correct_qty) in enumerate(to_update, 1):
        bol_cur.execute('UPDATE bol_items SET quantity = ? WHERE upc = ?', (correct_qty, upc))
        if i % 100 == 0:
            print(f"  Updated {i}/{len(to_update)}...")
    
    bol_conn.commit()
    print(f"\n✓ Successfully updated {len(to_update)} items!")
    
    # Verify the fix
    bol_cur.execute('''
        SELECT SUM(quantity) FROM bol_items
        WHERE lot_number IS NOT NULL AND lot_number != '' AND lot_number != 'nan'
    ''')
    new_total = bol_cur.fetchone()[0]
    
    rawbol_cur.execute('SELECT SUM(quantity) FROM raw_bol_items')
    rawbol_total = rawbol_cur.fetchone()[0]
    
    print(f"\nVerification:")
    print(f"  rawbol.db total quantity: {rawbol_total}")
    print(f"  bol.db total quantity (with LOT numbers): {new_total}")
    print(f"  Difference: {abs(new_total - rawbol_total)}")
    
    if new_total == rawbol_total:
        print("\n✓ Quantities are now in sync!")
    else:
        print(f"\n⚠️ Still {abs(new_total - rawbol_total)} difference - some items may not be in rawbol.db")
    
    rawbol_conn.close()
    bol_conn.close()

if __name__ == '__main__':
    fix_bol_quantities()
