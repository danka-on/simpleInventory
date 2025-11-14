import sqlite3

# Check returns data
conn = sqlite3.connect('sold.db')
conn.row_factory = sqlite3.Row
cur = conn.cursor()

print("=" * 60)
print("RETURNS DATA TEST")
print("=" * 60)

# Get total returns
cur.execute('SELECT COUNT(*) as count FROM returns')
count = cur.fetchone()['count']
print(f"\nTotal returns: {count}")

# Get cost breakdown
cur.execute('''
    SELECT 
        SUM(refund_amount) as total_refunds,
        SUM(original_shipping_cost) as total_orig_ship,
        SUM(return_shipping_cost) as total_return_ship
    FROM returns
''')
row = cur.fetchone()

total_refunds = row['total_refunds'] or 0
total_orig_ship = row['total_orig_ship'] or 0
total_return_ship = row['total_return_ship'] or 0
total_cost = total_refunds + total_orig_ship + total_return_ship

print(f"\nReturn Cost Breakdown:")
print(f"  Refunds:           ${total_refunds:,.2f}")
print(f"  Original Shipping: ${total_orig_ship:,.2f}")
print(f"  Return Shipping:   ${total_return_ship:,.2f}")
print(f"  TOTAL:             ${total_cost:,.2f}")

# Check how many orders have returns
cur.execute('SELECT COUNT(DISTINCT original_order_id) FROM returns')
orders_with_returns = cur.fetchone()[0]
print(f"\nOrders with returns: {orders_with_returns}")

# Sample return
cur.execute('SELECT * FROM returns LIMIT 1')
sample = cur.fetchone()
if sample:
    print(f"\nSample return:")
    print(f"  Order ID: {sample['order_id']}")
    print(f"  Title: {sample['title']}")
    print(f"  Refund: ${sample['refund_amount']:.2f}")
    print(f"  Original Ship: ${sample['original_shipping_cost']:.2f}")
    print(f"  Return Ship: ${sample['return_shipping_cost']:.2f}")
    total = sample['refund_amount'] + sample['original_shipping_cost'] + sample['return_shipping_cost']
    print(f"  Total Cost: ${total:.2f}")

conn.close()

print("\n" + "=" * 60)
print("Test complete!")
print("=" * 60)
