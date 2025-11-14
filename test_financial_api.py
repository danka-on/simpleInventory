import sqlite3
import traceback

try:
    conn = sqlite3.connect('sold.db')
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    
    cur.execute('''
        SELECT 
            id,
            order_id,
            item_id,
            title,
            quantity,
            price,
            seller_fee,
            taxes,
            paid_time,
            shipped_time,
            barcode,
            store,
            location,
            shipping_cost,
            lot_number
        FROM orders
        WHERE paid_time IS NOT NULL
        ORDER BY paid_time DESC
        LIMIT 1
    ''')
    
    orders = cur.fetchall()
    print(f'Found {len(orders)} orders')
    
    if orders:
        order = orders[0]
        print(f'Order keys: {list(order.keys())}')
        item_data = dict(order)
        print(f'item_data keys: {list(item_data.keys())}')
        print(f'item_data has id: {"id" in item_data}')
        
        # Try to access id
        try:
            order_id = order['id']
            print(f'Order ID (pk) via order[id]: {order_id}')
        except Exception as e:
            print(f'Error accessing order[id]: {e}')
            
        # Try dict access
        try:
            order_id = item_data['id']
            print(f'Order ID (pk) via item_data[id]: {order_id}')
        except Exception as e:
            print(f'Error accessing item_data[id]: {e}')
        
    # Test returns data
    print('\n--- Testing returns data ---')
    cur.execute('''
        SELECT 
            original_order_id,
            refund_amount,
            original_shipping_cost,
            return_shipping_cost
        FROM returns
        LIMIT 1
    ''')
    
    ret = cur.fetchone()
    if ret:
        print(f'Returns keys: {list(ret.keys())}')
        print(f'original_order_id: {ret["original_order_id"]}')
    
    conn.close()
    print('\nSUCCESS')
except Exception as e:
    print(f'ERROR: {e}')
    traceback.print_exc()
