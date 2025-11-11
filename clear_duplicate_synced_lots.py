"""
Clear synced_lots table to allow re-syncing from scratch if needed.
This is a one-time cleanup after fixing the quantity issue.
"""
import sqlite3

def clear_synced_lots():
    """Clear the synced_lots table"""
    conn = sqlite3.connect('rawbol.db')
    cur = conn.cursor()
    
    # Check current state
    cur.execute('SELECT COUNT(*) FROM synced_lots')
    count = cur.fetchone()[0]
    
    print(f"=== Current synced_lots table ===")
    print(f"  Total entries: {count}")
    
    cur.execute('SELECT * FROM synced_lots ORDER BY sync_date DESC')
    for row in cur.fetchall():
        print(f"    LOT {row[1]}: {row[3]} items, synced on {row[2]}")
    
    print("\nNOTE: Since we've fixed the quantities manually, the synced_lots table")
    print("      is now accurate and should NOT be cleared.")
    print("      The sync protection is working correctly now.")
    print("\n✓ No action needed!")
    
    conn.close()

if __name__ == '__main__':
    clear_synced_lots()
