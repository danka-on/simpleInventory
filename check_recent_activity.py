"""
Check what happened in the last hour - check all databases for recent additions
"""
import sqlite3
from datetime import datetime, timedelta

# Check rack.db for recent additions (if CREATED_AT exists)
print("📦 Checking rack.db INVENTORY for recent additions...")
print("=" * 80)
rack = sqlite3.connect('rack.db')
rack_cur = rack.cursor()
rack_cur.execute('PRAGMA table_info(INVENTORY)')
cols = [r[1] for r in rack_cur.fetchall()]

if 'CREATED_AT' in cols:
    one_hour_ago = (datetime.utcnow() - timedelta(hours=1)).isoformat()
    rack_cur.execute('SELECT COUNT(*) FROM INVENTORY WHERE CREATED_AT >= ?', (one_hour_ago,))
    recent_count = rack_cur.fetchone()[0]
    print(f"  Items added in last hour: {recent_count}")
    
    if recent_count > 0:
        rack_cur.execute('SELECT BARCODE, ITEM_POSITION, CREATED_AT FROM INVENTORY WHERE CREATED_AT >= ? ORDER BY CREATED_AT DESC', (one_hour_ago,))
        for row in rack_cur.fetchall():
            print(f"    {row[0]} | {row[1]} | {row[2]}")
else:
    print("  ⚠ No CREATED_AT column - can't check recent additions")
    rack_cur.execute('SELECT COUNT(*) FROM INVENTORY')
    total = rack_cur.fetchone()[0]
    print(f"  Total items in rack.db: {total}")

rack.close()

# Check searchRack.db for recent additions
print("\n🔍 Checking searchRack.db for recent additions...")
print("=" * 80)
search = sqlite3.connect('searchRack.db')
search_cur = search.cursor()
search_cur.execute('PRAGMA table_info(SEARCHRACK)')
cols = [r[1] for r in search_cur.fetchall()]

if 'CREATED_AT' in cols:
    one_hour_ago = (datetime.utcnow() - timedelta(hours=1)).isoformat()
    search_cur.execute('SELECT COUNT(*) FROM SEARCHRACK WHERE CREATED_AT >= ?', (one_hour_ago,))
    recent_count = search_cur.fetchone()[0]
    print(f"  Items added in last hour: {recent_count}")
    
    if recent_count > 0:
        search_cur.execute('SELECT BARCODE, TITLE, CREATED_AT FROM SEARCHRACK WHERE CREATED_AT >= ? ORDER BY CREATED_AT DESC LIMIT 20', (one_hour_ago,))
        for row in search_cur.fetchall():
            title = (row[1][:40] + "...") if row[1] and len(row[1]) > 40 else (row[1] or "None")
            print(f"    {row[0]} | {title} | {row[2]}")
else:
    print("  ⚠ No CREATED_AT column - can't check recent additions")

search_cur.execute('SELECT COUNT(*) FROM SEARCHRACK')
total = search_cur.fetchone()[0]
print(f"  Total items in searchRack.db: {total}")

search.close()

# Check sold.db for recent shipped items (might trigger inventory removal)
print("\n📮 Checking sold.db for recently shipped items...")
print("=" * 80)
sold = sqlite3.connect('sold.db')
sold_cur = sold.cursor()

one_hour_ago = (datetime.utcnow() - timedelta(hours=1)).isoformat()
sold_cur.execute('''
    SELECT order_id, barcode, title, shipped_time, rackupdated 
    FROM orders 
    WHERE shipped_time >= ? 
    ORDER BY shipped_time DESC 
    LIMIT 20
''', (one_hour_ago,))
recent_shipped = sold_cur.fetchall()

if recent_shipped:
    print(f"  Items shipped in last hour: {len(recent_shipped)}")
    for row in recent_shipped:
        title = (row[2][:40] + "...") if row[2] and len(row[2]) > 40 else (row[2] or "None")
        rack_status = "✅ Removed" if row[4] == 1 else "⏳ Pending"
        print(f"    {row[0]} | {row[1]} | {rack_status} | {row[3]}")
else:
    print("  No items shipped in last hour")

sold.close()
