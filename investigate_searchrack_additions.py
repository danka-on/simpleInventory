"""
Check searchRack.db creation timestamps and investigate what triggered the new entries
"""
import sqlite3
from datetime import datetime, timedelta

# Check searchRack.db
searchrack = sqlite3.connect('searchRack.db')
cursor = searchrack.cursor()

# Get all items with their row IDs to see insertion order
cursor.execute("SELECT rowid, BARCODE, TITLE, ITEM_POSITION FROM SEARCHRACK ORDER BY rowid DESC LIMIT 40")
items = cursor.fetchall()

print("Recent searchRack.db entries (by rowid):")
print("=" * 80)
for row in items:
    rowid, barcode, title, pos = row
    title_display = (title[:40] + "...") if title and len(title) > 40 else (title or "None")
    print(f"Row {rowid:3d} | {barcode or 'No barcode':15s} | {title_display:43s} | Pos: {pos or 'None'}")

searchrack.close()

# Check rack.db INVENTORY to see what's there
print("\n" + "=" * 80)
print("Checking rack.db INVENTORY...")
rack = sqlite3.connect('rack.db')
rack_cursor = rack.cursor()
rack_cursor.execute("SELECT COUNT(*) FROM INVENTORY")
inv_count = rack_cursor.fetchone()[0]
print(f"Total items in rack.db INVENTORY: {inv_count}")

# Check for recent additions (if there's a timestamp column)
rack_cursor.execute("PRAGMA table_info(INVENTORY)")
columns = [col[1] for col in rack_cursor.fetchall()]
print(f"INVENTORY columns: {columns}")

rack.close()

# Check sold.db for recent items
print("\n" + "=" * 80)
print("Checking sold.db for recently handled items...")
sold = sqlite3.connect('sold.db')
sold_cursor = sold.cursor()

# Check for items with isHandled = 1 (removed from inventory)
sold_cursor.execute("""
    SELECT order_id, store, date_sold, title, isHandled 
    FROM orders 
    WHERE isHandled = 1 
    ORDER BY date_sold DESC 
    LIMIT 10
""")
recent_handled = sold_cursor.fetchall()

print(f"\nRecent handled (removed) orders:")
for order in recent_handled:
    order_id, store, date_sold, title, handled = order
    title_display = (title[:40] + "...") if title and len(title) > 40 else (title or "None")
    print(f"  {order_id} | {store:8s} | {date_sold} | {title_display}")

sold.close()
