"""
Check if LOT# 16578199 exists in rawbol.db upload_logs table
"""
import sqlite3

conn = sqlite3.connect('rawbol.db')
conn.row_factory = sqlite3.Row
cur = conn.cursor()

print("=" * 80)
print("Checking LOT# 16578199 in rawbol.db upload_logs")
print("=" * 80)

# Check if it exists
cur.execute("SELECT * FROM upload_logs WHERE lot_number = '16578199'")
result = cur.fetchone()

if result:
    print("\n✅ LOT# 16578199 EXISTS in upload_logs!")
    print(f"\nDetails:")
    for key in result.keys():
        print(f"   {key}: {result[key]}")
else:
    print("\n❌ LOT# 16578199 NOT FOUND in upload_logs table")
    print("\nThis means it was deleted or never added to upload_logs.")
    print("We need to restore it based on the synced_lots data.")
    
    # Check synced_lots for reference
    cur.execute("SELECT * FROM synced_lots WHERE lot_number = '16578199'")
    synced = cur.fetchone()
    
    if synced:
        print(f"\n📋 Found in synced_lots:")
        print(f"   Sync Date: {synced['sync_date']}")
        print(f"   Items Count: {synced['items_count']}")
        
        # Get total quantity from raw_bol_items
        cur.execute("SELECT SUM(quantity) as total_qty FROM raw_bol_items WHERE lot_number = '16578199'")
        qty_row = cur.fetchone()
        total_qty = qty_row['total_qty'] if qty_row else 0
        
        print(f"   Total Quantity in raw_bol_items: {total_qty}")

conn.close()

print("\n" + "=" * 80)
