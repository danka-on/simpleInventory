"""
Recreate rawbol.db with a clean slate
Deletes the existing database and creates a fresh one with all tables
"""
import os
import sqlite3
from rawbol_manager import ensure_rawbol_db

def recreate_rawbol_db():
    """Delete and recreate rawbol.db"""
    db_path = 'rawbol.db'
    
    # Delete the existing database if it exists
    if os.path.exists(db_path):
        print(f"Deleting existing {db_path}...")
        os.remove(db_path)
        print("✅ Deleted")
    else:
        print(f"{db_path} does not exist, creating fresh...")
    
    # Create fresh database with all tables
    print("Creating fresh rawbol.db with all tables...")
    ensure_rawbol_db()
    
    # Verify the structure
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    
    # Check raw_bol_items table
    cur.execute("PRAGMA table_info(raw_bol_items)")
    cols = cur.fetchall()
    print("\n✅ raw_bol_items table created with columns:")
    for col in cols:
        print(f"  - {col[1]} ({col[2]})")
    
    # Check upload_logs table
    cur.execute("PRAGMA table_info(upload_logs)")
    cols = cur.fetchall()
    print("\n✅ upload_logs table created with columns:")
    for col in cols:
        print(f"  - {col[1]} ({col[2]})")
    
    # Check sync_history table
    cur.execute("PRAGMA table_info(sync_history)")
    cols = cur.fetchall()
    print("\n✅ sync_history table created with columns:")
    for col in cols:
        print(f"  - {col[1]} ({col[2]})")
    
    # Check synced_lots table
    cur.execute("PRAGMA table_info(synced_lots)")
    cols = cur.fetchall()
    print("\n✅ synced_lots table created with columns:")
    for col in cols:
        print(f"  - {col[1]} ({col[2]})")
    
    conn.close()
    
    print("\n✅ rawbol.db has been recreated successfully!")
    print("You can now upload BOL files through the extractor interface.")

if __name__ == '__main__':
    recreate_rawbol_db()
