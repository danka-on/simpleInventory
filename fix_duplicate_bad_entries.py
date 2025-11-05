"""
Clean up duplicate bad entries in bol_items.
When the same suffixed barcode (e.g., 719978859014-4) exists multiple times,
keep the one with the highest quantity and delete the others.
"""
import sqlite3

def fix_duplicates():
    conn = sqlite3.connect('bol.db')
    cur = conn.cursor()
    
    # Find all duplicate UPCs
    cur.execute('''
        SELECT upc, COUNT(*) as cnt 
        FROM bol_items 
        GROUP BY upc 
        HAVING cnt > 1
    ''')
    duplicates = cur.fetchall()
    
    if not duplicates:
        print("No duplicates found!")
        conn.close()
        return
    
    print(f"Found {len(duplicates)} duplicate UPC(s)")
    
    total_deleted = 0
    for upc, count in duplicates:
        print(f"\nProcessing {upc} ({count} entries):")
        
        # Get all entries for this UPC
        cur.execute('''
            SELECT id, upc, quantity, temporary, import_date
            FROM bol_items 
            WHERE upc = ? COLLATE NOCASE
            ORDER BY quantity DESC NULLS LAST, temporary ASC, id DESC
        ''', (upc,))
        entries = cur.fetchall()
        
        # Keep the first one (highest qty, non-temporary, newest)
        keep_id = entries[0][0]
        keep_qty = entries[0][2] or 0
        
        print(f"  Keeping: ID={keep_id}, qty={keep_qty}")
        
        # Delete the rest
        for entry in entries[1:]:
            entry_id = entry[0]
            entry_qty = entry[2] or 0
            print(f"  Deleting: ID={entry_id}, qty={entry_qty}")
            cur.execute('DELETE FROM bol_items WHERE id = ?', (entry_id,))
            total_deleted += 1
    
    conn.commit()
    conn.close()
    
    print(f"\nTotal duplicates deleted: {total_deleted}")
    print("Cleanup complete!")

if __name__ == '__main__':
    fix_duplicates()
