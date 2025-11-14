import sqlite3

conn = sqlite3.connect('sold.db')
conn.row_factory = sqlite3.Row
cur = conn.cursor()

# Search for this barcode in orders
barcode = '085081037831'
cur.execute('SELECT id, order_id, title, price, store, paid_time FROM orders WHERE barcode = ?', (barcode,))
orders = cur.fetchall()

print(f'=== ORDERS with barcode {barcode} ===')
for order in orders:
    print(f'ID: {order["id"]}, Order ID: {order["order_id"]}, Title: {order["title"]}')
    print(f'  Price: ${order["price"]:.2f}, Store: {order["store"]}, Paid: {order["paid_time"]}')
    
    # Check if this order has a return
    cur.execute('SELECT * FROM returns WHERE original_order_id = ?', (order['id'],))
    ret = cur.fetchone()
    
    if ret:
        print(f'  HAS RETURN:')
        print(f'    Return Date: {ret["return_date"]}')
        print(f'    Refund Amount: ${ret["refund_amount"]:.2f}')
        print(f'    Original Shipping: ${ret["original_shipping_cost"]:.2f}')
        print(f'    Return Shipping: ${ret["return_shipping_cost"]:.2f}')
        total = ret['refund_amount'] + ret['original_shipping_cost'] + ret['return_shipping_cost']
        print(f'    TOTAL RETURN COST: ${total:.2f}')
    else:
        print(f'  No return found')
    print()

conn.close()
