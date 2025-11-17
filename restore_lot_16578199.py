"""
Restore LOT# 16578199 to rawbol.db upload_logs table
"""
import sqlite3
from datetime import datetime

conn = sqlite3.connect('rawbol.db')
cur = conn.cursor()

print("=" * 80)
print("Restoring LOT# 16578199 to upload_logs")
print("=" * 80)

# Get sync date from synced_lots
cur.execute("SELECT sync_date, items_count FROM synced_lots WHERE lot_number = '16578199'")
synced = cur.fetchone()

if not synced:
    print("❌ LOT not found in synced_lots. Cannot restore.")
    conn.close()
    exit(1)

sync_date = synced[0]
items_count = synced[1]

# Get import date from bol.db
bol_conn = sqlite3.connect('bol.db')
bol_cur = bol_conn.cursor()
bol_cur.execute("SELECT MAX(import_date) FROM bol_items WHERE lot_number = '16578199'")
import_date = bol_cur.fetchone()[0] or '2025-11-01'
bol_conn.close()

# Insert into upload_logs
print(f"\nRestoring LOT# 16578199:")
print(f"   Filename: april.xls (original upload)")
print(f"   Import Date: {import_date}")
print(f"   Items Count: {items_count}")
print(f"   Uploaded At: {sync_date}")

cur.execute('''
    INSERT INTO upload_logs 
    (filename, import_date, uploaded_at, lot_number, bol_location, total_client_cost, shipping_cost)
    VALUES (?, ?, ?, ?, ?, ?, ?)
''', ('april.xls', import_date, sync_date, '16578199', None, None, None))

conn.commit()
print(f"\n✅ Successfully restored LOT# 16578199 to upload_logs!")

# Verify
cur.execute("SELECT * FROM upload_logs WHERE lot_number = '16578199'")
result = cur.fetchone()
if result:
    print(f"\n✅ Verified: LOT# 16578199 now exists in upload_logs")

conn.close()

print("\n" + "=" * 80)
print("Restoration complete! LOT# 16578199 will now appear in /extractor page.")
print("=" * 80)
