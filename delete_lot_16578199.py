#!/usr/bin/env python3
"""
Delete LOT# 16578199 from rawbol.db database.
"""
import sqlite3

def delete_lot():
    """Delete all records for LOT# 16578199"""
    conn = sqlite3.connect('rawbol.db')
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    
    lot_number = '16578199'
    
    # Check what exists first
    print(f"=== Checking for LOT# {lot_number} ===")
    
    # Check raw_bol_items
    cur.execute("SELECT COUNT(*) as count FROM raw_bol_items WHERE lot_number = ?", (lot_number,))
    items_count = cur.fetchone()['count']
    print(f"raw_bol_items: {items_count} items")
    
    # Check upload_logs
    cur.execute("SELECT COUNT(*) as count FROM upload_logs WHERE lot_number = ?", (lot_number,))
    logs_count = cur.fetchone()['count']
    print(f"upload_logs: {logs_count} logs")
    
    if items_count == 0 and logs_count == 0:
        print(f"\nLOT# {lot_number} not found in database.")
        conn.close()
        return
    
    # Delete from raw_bol_items
    print(f"\n=== Deleting LOT# {lot_number} ===")
    cur.execute("DELETE FROM raw_bol_items WHERE lot_number = ?", (lot_number,))
    print(f"Deleted {cur.rowcount} items from raw_bol_items")
    
    # Delete from upload_logs
    cur.execute("DELETE FROM upload_logs WHERE lot_number = ?", (lot_number,))
    print(f"Deleted {cur.rowcount} logs from upload_logs")
    
    conn.commit()
    conn.close()
    
    print(f"\n✅ LOT# {lot_number} successfully deleted from database!")

if __name__ == '__main__':
    try:
        delete_lot()
    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()
