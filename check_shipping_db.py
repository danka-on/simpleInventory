"""Check shipping data in database"""
import sqlite3

conn = sqlite3.connect('sold.db')
cur = conn.cursor()

cur.execute('''
    SELECT order_id, title, price, shipping_cost, taxes, seller_fee, store 
    FROM orders 
    WHERE store="amazon" 
    ORDER BY paid_time DESC 
    LIMIT 20
''')

print("Recent Amazon Orders:")
print(f"{'Order ID':<25} {'Price':<10} {'Shipping':<10} {'Tax':<10} {'Fee':<10} {'Title':<30}")
print("-"*100)

for row in cur.fetchall():
    order_id, title, price, shipping, tax, fee, store = row
    shipping = shipping if shipping else 0
    tax = tax if tax else 0
    fee = fee if fee else 0
    print(f"{order_id:<25} ${price:<9.2f} ${shipping:<9.2f} ${tax:<9.2f} ${fee:<9.2f} {title[:30]}")

conn.close()
