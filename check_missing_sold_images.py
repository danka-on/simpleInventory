"""
Check which Amazon sold orders still don't have images
"""
import sqlite3

sold_conn = sqlite3.connect('sold.db')
sold_conn.row_factory = sqlite3.Row
sold_cur = sold_conn.cursor()

sold_cur.execute("""
    SELECT item_id, title 
    FROM orders 
    WHERE store = 'amazon' 
    AND (image IS NULL OR image = '')
""")

rows = sold_cur.fetchall()

print(f"\nAmazon sold orders without images: {len(rows)}\n")

for row in rows:
    print(f"  ASIN: {row['item_id']}")
    print(f"  Title: {row['title'][:80]}")
    print()

sold_conn.close()
