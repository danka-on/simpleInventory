"""
Find orders with 'No image' literal string
"""
import sqlite3

sold = sqlite3.connect('sold.db')
sold_cur = sold.cursor()

sold_cur.execute("""
    SELECT order_id, barcode, image, store 
    FROM orders 
    WHERE image = 'No image' 
    AND (isHandled IS NULL OR isHandled = '')
""")
results = sold_cur.fetchall()

print(f"Found {len(results)} orders with literal 'No image' string:")
for r in results:
    print(f"  Order: {r[0]} | Barcode: {r[1]} | Image: {r[2]} | Store: {r[3]}")

sold.close()
