"""
Consolidate duplicate entries in searchRack.db
Combines rows with same barcode and same location into single row with summed quantity
"""
import sqlite3
from datetime import datetime

def consolidate_duplicates():
    conn = sqlite3.connect('searchRack.db')
    cur = conn.cursor()
    
    # Find duplicates (same barcode, same location, no picture position)
    cur.execute('''
        SELECT 
            BARCODE,
            ITEM_POSITION,
            COUNT(*) as count,
            SUM(QUANTITY) as total_qty,
            MIN(CREATED_AT) as oldest_date,
            MAX(CREATED_AT) as newest_date,
            GROUP_CONCAT(ID) as ids
        FROM SEARCHRACK
        WHERE BARCODE IS NOT NULL 
        AND TRIM(BARCODE) != ''
        AND ITEM_POSITION IS NOT NULL
        AND TRIM(ITEM_POSITION) != ''
        AND (PICTUREPOSITION IS NULL OR TRIM(PICTUREPOSITION) = '')
        GROUP BY BARCODE, ITEM_POSITION
        HAVING COUNT(*) > 1
        ORDER BY COUNT(*) DESC
    ''')
    
    duplicates = cur.fetchall()
    
    if not duplicates:
        print("✅ No duplicates found!")
        conn.close()
        return
    
    print(f"Found {len(duplicates)} sets of duplicates:\n")
    
    for dup in duplicates:
        barcode, location, count, total_qty, oldest, newest, ids = dup
        id_list = [int(x) for x in ids.split(',')]
        
        print(f"Barcode: {barcode}, Location: {location}")
        print(f"  {count} separate entries with total quantity: {total_qty}")
        print(f"  IDs: {ids}")
        
        # Get the first entry (we'll keep this one and delete the others)
        cur.execute('''
            SELECT ID, TITLE, ITEMID, IMAGE, IMAGES 
            FROM SEARCHRACK 
            WHERE ID = ?
        ''', (id_list[0],))
        keeper = cur.fetchone()
        keeper_id, title, itemid, image, images = keeper
        
        # Update the keeper with the total quantity and newest timestamp
        cur.execute('''
            UPDATE SEARCHRACK 
            SET QUANTITY = ?,
                CREATED_AT = ?
            WHERE ID = ?
        ''', (total_qty, newest, keeper_id))
        
        print(f"  ✓ Updated ID {keeper_id} to quantity {total_qty}")
        
        # Delete the duplicate entries
        for dup_id in id_list[1:]:
            cur.execute('DELETE FROM SEARCHRACK WHERE ID = ?', (dup_id,))
            print(f"  ✓ Deleted duplicate ID {dup_id}")
        
        print()
    
    conn.commit()
    conn.close()
    
    print(f"\n✅ Consolidated {len(duplicates)} sets of duplicates!")
    print("Your searchRack.db now has single entries per barcode+location combination.")

if __name__ == '__main__':
    print("=" * 70)
    print("SearchRack Duplicate Consolidation")
    print("=" * 70)
    print("\nThis will combine duplicate entries with same barcode + location")
    print("into single rows with summed quantities.\n")
    
    response = input("Continue? (yes/no): ").strip().lower()
    if response == 'yes':
        consolidate_duplicates()
    else:
        print("Cancelled.")
