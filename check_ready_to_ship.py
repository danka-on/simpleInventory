"""
Check current state of ready-to-ship Amazon orders
"""
import sqlite3

conn = sqlite3.connect('sold.db')
cur = conn.cursor()
cur.execute('''SELECT id, order_id, barcode, title, image, store 
               FROM orders 
               WHERE isHandled = 0 
               AND store = 'amazon'
               ORDER BY id
               LIMIT 10''')
orders = cur.fetchall()

print('Amazon Ready-to-Ship Orders (first 10):')
print('=' * 80)
for o in orders:
    barcode_display = o[2] if o[2] else "MISSING"
    title_display = (o[3] if o[3] else "MISSING")[:60]
    image_status = "✓" if o[4] else "✗"
    
    print(f'ID: {o[0]:3} | OrderID: {o[1][:20]:20} | Barcode: {barcode_display:14}')
    print(f'    Title: {title_display}')
    print(f'    Image: {image_status} | Store: {o[5]}')
    print()
    
print(f'Total ready-to-ship Amazon orders: {len(orders)}')
conn.close()
