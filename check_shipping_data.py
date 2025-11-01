import sqlite3

conn = sqlite3.connect('sold.db')
cur = conn.cursor()

# Check how many orders have shipping cost data
cur.execute('SELECT COUNT(*) FROM orders WHERE shipping_cost IS NOT NULL AND shipping_cost > 0')
print(f'Orders with shipping cost: {cur.fetchone()[0]}')

cur.execute('SELECT COUNT(*) FROM orders')
print(f'Total orders: {cur.fetchone()[0]}')

# Show recent orders
cur.execute('''SELECT order_id, title, store, price, shipping_cost, paid_time 
               FROM orders 
               ORDER BY paid_time DESC 
               LIMIT 10''')
print('\nRecent 10 orders:')
for row in cur.fetchall():
    title = row[1][:40] if row[1] else 'No title'
    shipping = f'${row[4]:.2f}' if row[4] else 'NULL'
    print(f'  {row[0]}: {title}... | Store: {row[2]} | Price: ${row[3]} | Shipping: {shipping} | Date: {row[5]}')

conn.close()
