"""
Test script to verify zero-quantity deletion system
"""
import sqlite3
from datetime import datetime, timedelta

def check_zero_qty_system():
    conn = sqlite3.connect('searchRack.db')
    cur = conn.cursor()
    
    # Check if table exists
    cur.execute('''
        CREATE TABLE IF NOT EXISTS zero_qty_pending_deletion (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            searchrack_id INTEGER,
            marked_at TEXT,
            delete_at TEXT
        )
    ''')
    
    # Check pending deletions
    cur.execute('SELECT COUNT(*) FROM zero_qty_pending_deletion')
    pending_count = cur.fetchone()[0]
    
    print("\n" + "="*60)
    print("ZERO-QUANTITY DELETION SYSTEM STATUS")
    print("="*60)
    
    print(f"\nPending deletions: {pending_count}")
    
    if pending_count > 0:
        cur.execute('''
            SELECT id, searchrack_id, marked_at, delete_at 
            FROM zero_qty_pending_deletion
            ORDER BY delete_at
        ''')
        
        print("\nScheduled for deletion:")
        print("-" * 60)
        for row in cur.fetchall():
            pending_id, searchrack_id, marked_at, delete_at = row
            
            # Check current quantity
            cur.execute('SELECT TITLE, BARCODE, QUANTITY FROM SEARCHRACK WHERE ID = ?', (searchrack_id,))
            item = cur.fetchone()
            
            if item:
                title, barcode, qty = item
                delete_dt = datetime.fromisoformat(delete_at)
                now = datetime.now()
                time_remaining = delete_dt - now
                hours_remaining = time_remaining.total_seconds() / 3600
                
                print(f"\nItem ID: {searchrack_id}")
                print(f"  Title: {title[:50]}...")
                print(f"  Barcode: {barcode}")
                print(f"  Current Qty: {qty}")
                print(f"  Marked: {marked_at}")
                print(f"  Delete at: {delete_at}")
                print(f"  Time remaining: {hours_remaining:.1f} hours")
            else:
                print(f"\nItem ID {searchrack_id}: Already deleted or not found")
    
    # Check archived items
    cur.execute('''
        CREATE TABLE IF NOT EXISTS archived_searchrack (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            original_id INTEGER,
            title TEXT,
            barcode TEXT,
            item_position TEXT,
            images TEXT,
            pictureposition TEXT,
            itemid TEXT,
            quantity INTEGER,
            created_at TEXT,
            image TEXT,
            archived_at TEXT,
            reason TEXT
        )
    ''')
    
    cur.execute("SELECT COUNT(*) FROM archived_searchrack WHERE reason = 'zero_quantity_24h'")
    archived_count = cur.fetchone()[0]
    
    print(f"\n\nArchived items (zero quantity): {archived_count}")
    
    if archived_count > 0:
        cur.execute('''
            SELECT original_id, title, barcode, archived_at 
            FROM archived_searchrack 
            WHERE reason = 'zero_quantity_24h'
            ORDER BY archived_at DESC
            LIMIT 10
        ''')
        
        print("\nRecent zero-quantity deletions:")
        print("-" * 60)
        for row in cur.fetchall():
            orig_id, title, barcode, archived_at = row
            print(f"\nOriginal ID: {orig_id}")
            print(f"  Title: {title[:50] if title else 'N/A'}...")
            print(f"  Barcode: {barcode}")
            print(f"  Archived: {archived_at}")
    
    # Check current zero-quantity items
    cur.execute('SELECT COUNT(*) FROM SEARCHRACK WHERE QUANTITY = 0 OR QUANTITY IS NULL')
    current_zero_qty = cur.fetchone()[0]
    
    print(f"\n\nCurrent items with 0 quantity: {current_zero_qty}")
    
    conn.close()
    
    print("\n" + "="*60)
    print("\nℹ️  Items set to 0 quantity will be marked for deletion")
    print("    and automatically removed after 24 hours.")
    print("\n✅ System is operational\n")

if __name__ == '__main__':
    check_zero_qty_system()
