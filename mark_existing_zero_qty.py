"""
Mark existing zero-quantity items for deletion.
This is a one-time script to add existing zero-qty items to the pending deletion queue.
"""
import sqlite3
from datetime import datetime, timedelta

def mark_existing_zero_qty_items():
    conn = sqlite3.connect('searchRack.db')
    cur = conn.cursor()
    
    # Ensure tables exist
    cur.execute('''
        CREATE TABLE IF NOT EXISTS zero_qty_pending_deletion (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            searchrack_id INTEGER,
            marked_at TEXT,
            delete_at TEXT,
            deletion_cancelled INTEGER DEFAULT 0
        )
    ''')
    
    cur.execute('''
        CREATE TABLE IF NOT EXISTS zero_qty_settings (
            key TEXT PRIMARY KEY,
            value TEXT
        )
    ''')
    
    # Get settings
    cur.execute("SELECT value FROM zero_qty_settings WHERE key = 'interval_minutes'")
    interval_row = cur.fetchone()
    interval_minutes = int(interval_row[0]) if interval_row else 1440  # Default 24 hours
    
    # If no interval_minutes setting, insert default
    if not interval_row:
        cur.execute("INSERT OR REPLACE INTO zero_qty_settings (key, value) VALUES ('interval_minutes', '2880')")
        interval_minutes = 2880  # 48 hours
        print(f"✓ Set default interval to {interval_minutes} minutes (48 hours)")
    
    # Convert to grace_hours for display
    grace_hours = interval_minutes / 60
    
    # Find all zero-quantity items
    cur.execute('SELECT ID, BARCODE, TITLE, QUANTITY FROM SEARCHRACK WHERE QUANTITY = 0')
    zero_qty_items = cur.fetchall()
    
    if not zero_qty_items:
        print("No zero-quantity items found.")
        conn.close()
        return
    
    print(f"Found {len(zero_qty_items)} zero-quantity items")
    print(f"Grace period: {grace_hours} hours")
    
    now = datetime.now()
    delete_at = now + timedelta(minutes=interval_minutes)
    
    marked = 0
    skipped = 0
    
    for item_id, barcode, title, qty in zero_qty_items:
        # Check if already marked
        cur.execute('SELECT id FROM zero_qty_pending_deletion WHERE searchrack_id = ?', (item_id,))
        existing = cur.fetchone()
        
        if existing:
            print(f"  - Item {item_id} (barcode: {barcode}) already marked, skipping")
            skipped += 1
            continue
        
        # Mark for deletion
        cur.execute('''
            INSERT INTO zero_qty_pending_deletion (searchrack_id, marked_at, delete_at)
            VALUES (?, ?, ?)
        ''', (item_id, now.isoformat(), delete_at.isoformat()))
        
        print(f"  ✓ Marked item {item_id} (barcode: {barcode}, title: {(title or 'No title')[:40]})")
        marked += 1
    
    conn.commit()
    conn.close()
    
    print(f"\n✓ Marked {marked} items for deletion")
    if skipped > 0:
        print(f"  Skipped {skipped} already-marked items")

if __name__ == '__main__':
    mark_existing_zero_qty_items()
