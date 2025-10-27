"""
Check if Amazon orders have shipping info populated
"""
import sqlite3
import json

conn = sqlite3.connect('sold.db')
cur = conn.cursor()

cur.execute('''
    SELECT order_id, shipping_name, shipping_city, shipping_state, 
           shipping_postal_code, shipping_country, price, paid_time 
    FROM orders 
    WHERE store = 'amazon'
    LIMIT 5
''')

results = []
for row in cur.fetchall():
    results.append({
        'order_id': row[0],
        'name': row[1],
        'city': row[2],
        'state': row[3],
        'postal': row[4],
        'country': row[5],
        'price': row[6],
        'paid': row[7]
    })

print(json.dumps(results, indent=2))

conn.close()
