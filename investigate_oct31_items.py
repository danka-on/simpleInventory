"""
Check what caused the 21 items to be added to searchRack.db on 2025-10-31T14:11:59
"""
import sqlite3

print("Investigating the 21 deleted items from 2025-10-31T14:11:59...")

# Check if there were BOL uploads around that time
print("\n1. Checking for BOL uploads on 2025-10-31:")
try:
    conn = sqlite3.connect('rawbol.db')
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("SELECT * FROM upload_logs WHERE uploaded_at LIKE '2025-10-31%' ORDER BY uploaded_at")
    rows = cur.fetchall()
    print(f"   Found {len(rows)} BOL uploads on 2025-10-31")
    for r in rows:
        print(f"   - Filename: {r['filename']}, Lot: {r['lot_number']}, Uploaded: {r['uploaded_at']}, Rows: {r['rows_imported']}")
    conn.close()
except Exception as e:
    print(f"   Error checking rawbol.db: {e}")

# Check if there were any barcode scans around that time (from rack.db)
print("\n2. Checking for barcode scans in rack.db around 14:11:")
try:
    conn = sqlite3.connect('rack.db')
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    # Check if rack.db has a timestamp column
    cur.execute("PRAGMA table_info(INVENTORY)")
    cols = [r[1] for r in cur.fetchall()]
    print(f"   Columns in rack.db INVENTORY: {cols}")
    
    if 'created_at' in cols or 'CREATED_AT' in cols:
        cur.execute("SELECT COUNT(*) FROM INVENTORY WHERE created_at LIKE '2025-10-31 14:1%' OR CREATED_AT LIKE '2025-10-31 14:1%'")
        count = cur.fetchone()[0]
        print(f"   Found {count} items added around 14:1x in rack.db")
    else:
        print("   rack.db doesn't have timestamp column")
    conn.close()
except Exception as e:
    print(f"   Error checking rack.db: {e}")

# Check the characteristics of the deleted items
print("\n3. Analyzing the deleted items characteristics:")
print("   - All had CREATED_AT: 2025-10-31T14:11:59.081542 (exact same timestamp)")
print("   - All had no TITLE (NULL or empty)")
print("   - Barcodes varied: 4291206417082, 086279084392 (multiple), empty strings")
print("   - This suggests they were added in a batch operation")

print("\n4. Most likely causes:")
print("   a) updateSearchRackDB() was called - this syncs from ebayStore.db")
print("   b) A batch barcode scanning operation")
print("   c) enrich_searchrack_db() was called without proper data")
print("   d) A script or API call that inserted items directly")

print("\n5. The exact same timestamp (down to microseconds) indicates:")
print("   - They were inserted in a tight loop or batch operation")
print("   - NOT individual barcode scans (which would have different timestamps)")
print("   - Likely updateSearchRackDB() or a similar batch sync function")

print("\n6. Recommendation:")
print("   - Check if updateSearchRackDB() is being called on app startup")
print("   - These items probably came from ebayStore.db or another source")
print("   - They had no titles because the source data was incomplete")
