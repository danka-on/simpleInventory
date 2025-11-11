"""
Fix LOT numbers in bol.db by syncing with rawbol.db.

This script updates the lot_number in bol_items to match the original
lot_number from raw_bol_items based on UPC matching.
"""
import sqlite3
from datetime import datetime

def fix_lot_numbers():
    # Connect to both databases
    rawbol_conn = sqlite3.connect('rawbol.db')
    rawbol_cur = rawbol_conn.cursor()
    
    bol_conn = sqlite3.connect('bol.db')
    bol_cur = bol_conn.cursor()
    
    print("Starting LOT number correction...")
    print(f"Timestamp: {datetime.now()}\n")
    
    # Get all UPCs with their correct LOT numbers from rawbol.db
    # For UPCs that appear in multiple LOTs, we'll take the most recent import
    rawbol_cur.execute('''
        SELECT upc, lot_number, MAX(import_date) as latest_date
        FROM raw_bol_items
        WHERE lot_number IS NOT NULL 
            AND TRIM(COALESCE(lot_number, '')) != ''
            AND LOWER(lot_number) NOT IN ('nan', 'none', 'null')
        GROUP BY upc
        ORDER BY upc
    ''')
    
    correct_lots = {}
    for row in rawbol_cur.fetchall():
        upc, lot_number, import_date = row
        correct_lots[upc] = lot_number
    
    print(f"Found {len(correct_lots)} UPCs with correct LOT numbers in rawbol.db\n")
    
    # Check current state in bol.db
    bol_cur.execute('''
        SELECT upc, lot_number
        FROM bol_items
        WHERE upc NOT LIKE '%-%'  -- Only base UPCs, not suffixed ones
    ''')
    
    items_to_fix = []
    items_correct = 0
    items_not_in_rawbol = 0
    
    for row in bol_cur.fetchall():
        upc, current_lot = row
        
        if upc in correct_lots:
            correct_lot = correct_lots[upc]
            if current_lot != correct_lot:
                items_to_fix.append((correct_lot, upc, current_lot))
            else:
                items_correct += 1
        else:
            items_not_in_rawbol += 1
    
    print(f"Analysis:")
    print(f"  Items with correct LOT: {items_correct}")
    print(f"  Items needing correction: {len(items_to_fix)}")
    print(f"  Items not in rawbol.db: {items_not_in_rawbol}")
    print()
    
    if items_to_fix:
        print(f"Sample of corrections (first 10):")
        for correct_lot, upc, wrong_lot in items_to_fix[:10]:
            print(f"  UPC {upc}: {wrong_lot} → {correct_lot}")
        print()
        
        response = input(f"Fix {len(items_to_fix)} items? (yes/no): ")
        if response.lower() in ('yes', 'y'):
            print("\nApplying fixes...")
            fixed_count = 0
            
            for correct_lot, upc, wrong_lot in items_to_fix:
                bol_cur.execute('''
                    UPDATE bol_items
                    SET lot_number = ?
                    WHERE upc = ? AND upc NOT LIKE '%-%'
                ''', (correct_lot, upc))
                fixed_count += 1
                
                if fixed_count % 100 == 0:
                    print(f"  Fixed {fixed_count}/{len(items_to_fix)}...")
            
            bol_conn.commit()
            print(f"\n✓ Successfully fixed {fixed_count} items!")
        else:
            print("Cancelled - no changes made")
    else:
        print("No corrections needed - all LOT numbers are correct!")
    
    # Close connections
    rawbol_conn.close()
    bol_conn.close()
    
    print("\nDone!")

if __name__ == '__main__':
    fix_lot_numbers()
