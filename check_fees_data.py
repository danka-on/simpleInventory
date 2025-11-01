import sqlite3

conn = sqlite3.connect('sold.db')
cur = conn.cursor()

# Check fee coverage by store
cur.execute('''
    SELECT 
        store, 
        COUNT(*) as total_orders,
        COUNT(seller_fee) as orders_with_fees,
        SUM(CASE WHEN seller_fee > 0 THEN 1 ELSE 0 END) as orders_with_nonzero_fees,
        AVG(CASE WHEN seller_fee > 0 THEN seller_fee ELSE NULL END) as avg_nonzero_fee
    FROM orders 
    WHERE store IS NOT NULL
    GROUP BY store
''')

print('Fee Coverage by Store:')
print('-' * 80)
for row in cur.fetchall():
    store, total, with_fees, nonzero_fees, avg_fee = row
    print(f'{store.upper()}:')
    print(f'  Total orders: {total}')
    print(f'  Orders with fee data: {with_fees} ({with_fees/total*100:.1f}%)')
    print(f'  Orders with non-zero fees: {nonzero_fees} ({nonzero_fees/total*100:.1f}%)')
    print(f'  Average non-zero fee: ${avg_fee:.2f}' if avg_fee else '  Average non-zero fee: N/A')
    print()

# Show sample of recent orders with all fee-related data
cur.execute('''
    SELECT order_id, title, store, price, seller_fee, taxes, shipping_cost, paid_time
    FROM orders
    ORDER BY paid_time DESC
    LIMIT 10
''')

print('\nRecent 10 Orders - Fee Details:')
print('-' * 80)
for row in cur.fetchall():
    order_id, title, store, price, seller_fee, taxes, shipping, paid_time = row
    title_short = title[:40] if title else 'No title'
    print(f'{order_id} ({store}):')
    print(f'  Title: {title_short}...')
    print(f'  Price: ${price:.2f} | Seller Fee: ${seller_fee if seller_fee else 0:.2f} | Taxes: ${taxes if taxes else 0:.2f} | Shipping: ${shipping if shipping else 0:.2f}')
    print(f'  Date: {paid_time}')
    print()

conn.close()
