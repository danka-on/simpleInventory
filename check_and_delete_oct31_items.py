"""
Check and delete items added to searchRack.db on 2025-10-31 around 10:11am
"""
import sqlite3

def check_and_delete_items():
    conn = sqlite3.connect('searchRack.db')
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    
    # First, check what items exist with that timestamp
    print("Checking for items added on 2025-10-31...")
    cur.execute("SELECT COUNT(*) FROM SEARCHRACK WHERE CREATED_AT LIKE '2025-10-31%'")
    count = cur.fetchone()[0]
    print(f"Found {count} items with CREATED_AT starting with 2025-10-31")
    
    if count == 0:
        print("No items found to delete")
        conn.close()
        return
    
    # Show some sample items
    print("\nShowing first 10 items:")
    cur.execute("SELECT * FROM SEARCHRACK WHERE CREATED_AT LIKE '2025-10-31%' ORDER BY CREATED_AT LIMIT 10")
    rows = cur.fetchall()
    
    for row in rows:
        row_dict = dict(row)
        barcode = row_dict.get('BARCODE', 'N/A')
        title = row_dict.get('TITLE', 'N/A')
        if title and title != 'N/A':
            title_display = title[:50] + '...' if len(title) > 50 else title
        else:
            title_display = 'N/A'
        created = row_dict.get('CREATED_AT', 'N/A')
        item_id = row_dict.get('id', row_dict.get('ID', 'N/A'))
        print(f"  ID: {item_id}, BARCODE: {barcode}, TITLE: {title_display}, CREATED_AT: {created}")
    
    # Ask for confirmation
    print(f"\nReady to delete {count} items from searchRack.db")
    confirm = input("Type 'yes' to proceed with deletion: ")
    
    if confirm.lower() != 'yes':
        print("Deletion cancelled")
        conn.close()
        return
    
    # Delete the items
    cur.execute("DELETE FROM SEARCHRACK WHERE CREATED_AT LIKE '2025-10-31%'")
    deleted = cur.rowcount
    conn.commit()
    
    print(f"\n✅ Deleted {deleted} items from searchRack.db")
    
    # Verify deletion
    cur.execute("SELECT COUNT(*) FROM SEARCHRACK WHERE CREATED_AT LIKE '2025-10-31%'")
    remaining = cur.fetchone()[0]
    print(f"Remaining items with that date: {remaining}")
    
    conn.close()

if __name__ == '__main__':
    check_and_delete_items()
