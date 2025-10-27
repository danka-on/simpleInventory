"""
Test the backfill query
"""
import sqlite3

sold = sqlite3.connect('sold.db')
sold_cur = sold.cursor()

# Test the query
sold_cur.execute("""
    SELECT COUNT(*) 
    FROM orders 
    WHERE (image IS NULL OR image = '' OR LOWER(image) = 'no image')
    AND (isHandled IS NULL OR isHandled = '')
""")
count = sold_cur.fetchone()[0]
print(f"Orders matching criteria: {count}")

# Get specific ones with 'No image'
sold_cur.execute("""
    SELECT order_id, item_id, barcode, store
    FROM orders 
    WHERE LOWER(image) = 'no image'
    AND (isHandled IS NULL OR isHandled = '')
""")
results = sold_cur.fetchall()
print(f"\nOrders with 'No image' string: {len(results)}")
for r in results:
    print(f"  {r[0]} | ItemID: {r[1]} | Barcode: {r[2]} | Store: {r[3]}")

sold.close()
