"""
Check eBay listings image status
"""
import sqlite3

conn = sqlite3.connect('ebayStore.db')
cur = conn.cursor()

cur.execute("SELECT COUNT(*) FROM INVENTORY WHERE Image IS NOT NULL AND Image != '' AND Image != 'No image'")
with_images = cur.fetchone()[0]

cur.execute("SELECT COUNT(*) FROM INVENTORY WHERE Image = 'No image' OR Image IS NULL OR Image = ''")
without_images = cur.fetchone()[0]

cur.execute("SELECT COUNT(*) FROM INVENTORY")
total = cur.fetchone()[0]

print(f"eBay Store Inventory:")
print(f"  Total items: {total}")
print(f"  With valid images: {with_images}")
print(f"  Without images: {without_images}")

# Show some examples of items without images
cur.execute("SELECT ItemID, Title FROM INVENTORY WHERE Image = 'No image' LIMIT 10")
print(f"\nSample items with 'No image':")
for row in cur.fetchall():
    print(f"  {row[0]}: {row[1][:60]}")

conn.close()
