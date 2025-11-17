"""
Update LOT# 16578199 filename to nov.xls
"""
import sqlite3

conn = sqlite3.connect('rawbol.db')
cur = conn.cursor()

cur.execute("UPDATE upload_logs SET filename = 'nov.xls' WHERE lot_number = '16578199'")
conn.commit()

# Verify
cur.execute("SELECT filename FROM upload_logs WHERE lot_number = '16578199'")
result = cur.fetchone()

if result:
    print(f"✅ Updated filename to: {result[0]}")
else:
    print("❌ Failed to update")

conn.close()
