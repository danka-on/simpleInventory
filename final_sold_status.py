"""
Check final sold orders image status
"""
import sqlite3

conn = sqlite3.connect('sold.db')
cur = conn.cursor()

cur.execute("SELECT COUNT(*) FROM orders WHERE (image IS NULL OR image = '' OR LOWER(image) = 'no image') AND (isHandled IS NULL OR isHandled = '')")
without_images = cur.fetchone()[0]

cur.execute("SELECT COUNT(*) FROM orders WHERE image IS NOT NULL AND image != '' AND LOWER(image) != 'no image' AND (isHandled IS NULL OR isHandled = '')")
with_images = cur.fetchone()[0]

cur.execute("SELECT COUNT(*) FROM orders WHERE isHandled IS NULL OR isHandled = ''")
total = cur.fetchone()[0]

print("Sold Orders Status:")
print(f"  Total active orders: {total}")
print(f"  With images: {with_images} ({int(with_images/total*100)}%)")
print(f"  Without images: {without_images}")

# Show which orders still don't have images
if without_images > 0:
    cur.execute("""
        SELECT order_id, title, store 
        FROM orders 
        WHERE (image IS NULL OR image = '' OR LOWER(image) = 'no image') 
        AND (isHandled IS NULL OR isHandled = '')
        LIMIT 20
    """)
    print(f"\nOrders still without images:")
    for row in cur.fetchall():
        print(f"  {row[0]} | {row[2]} | {row[1][:60] if row[1] else 'No title'}")

conn.close()
