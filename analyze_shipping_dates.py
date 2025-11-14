"""Check order dates and shipping status to understand shipping cost coverage"""
from amazon_manager import AmazonManager
from datetime import datetime

amazon = AmazonManager()

print("="*80)
print("ANALYZING ORDER DATES AND SHIPPING STATUS")
print("="*80)

# Get orders from last 30 days
orders = amazon.get_orders(days_back=30, max_results=100)

print(f"\n✅ Found {len(orders)} orders\n")

# Get financial data
financial_data = amazon.get_financial_events(days_back=30)

# Analyze each order
orders_with_shipping = []
orders_without_shipping = []

for order in orders:
    order_id = order.get('AmazonOrderId')
    order_status = order.get('OrderStatus')
    purchase_date = order.get('PurchaseDate')
    last_update = order.get('LastUpdateDate')
    
    # Check if we have shipping cost for this order
    has_shipping = financial_data.get(order_id, {}).get('shipping_cost', 0) > 0
    
    # Parse dates
    try:
        purchase_dt = datetime.fromisoformat(purchase_date.replace('Z', '+00:00'))
        days_ago = (datetime.now(purchase_dt.tzinfo) - purchase_dt).days
    except:
        days_ago = -1
    
    order_info = {
        'order_id': order_id,
        'status': order_status,
        'days_ago': days_ago,
        'purchase_date': purchase_date[:10],
        'has_shipping': has_shipping,
        'shipping_cost': financial_data.get(order_id, {}).get('shipping_cost', 0)
    }
    
    if has_shipping:
        orders_with_shipping.append(order_info)
    else:
        orders_without_shipping.append(order_info)

# Print summary
print("="*80)
print(f"ORDERS WITH SHIPPING COSTS: {len(orders_with_shipping)}")
print("="*80)
for order in orders_with_shipping:
    print(f"  {order['purchase_date']} ({order['days_ago']}d ago) - {order['status']:<10} - ${order['shipping_cost']:.2f} - {order['order_id']}")

print(f"\n{'='*80}")
print(f"ORDERS WITHOUT SHIPPING COSTS: {len(orders_without_shipping)}")
print("="*80)

# Group by status
by_status = {}
for order in orders_without_shipping:
    status = order['status']
    if status not in by_status:
        by_status[status] = []
    by_status[status].append(order)

for status, status_orders in sorted(by_status.items()):
    print(f"\n{status}: {len(status_orders)} orders")
    # Show first 5 of each status
    for order in status_orders[:5]:
        print(f"  {order['purchase_date']} ({order['days_ago']}d ago) - {order['order_id']}")
    if len(status_orders) > 5:
        print(f"  ... and {len(status_orders) - 5} more")

# Date range analysis
print(f"\n{'='*80}")
print("DATE RANGE ANALYSIS")
print("="*80)
all_orders = orders_with_shipping + orders_without_shipping
days_list = [o['days_ago'] for o in all_orders if o['days_ago'] >= 0]
if days_list:
    print(f"  Oldest order: {max(days_list)} days ago")
    print(f"  Newest order: {min(days_list)} days ago")
    print(f"  Orders 0-7 days: {len([d for d in days_list if d <= 7])}")
    print(f"  Orders 8-14 days: {len([d for d in days_list if 7 < d <= 14])}")
    print(f"  Orders 15-30 days: {len([d for d in days_list if 14 < d <= 30])}")

shipped_orders = [o for o in orders_without_shipping if o['status'] == 'Shipped']
pending_orders = [o for o in orders_without_shipping if o['status'] in ['Pending', 'Unshipped']]

print(f"\n{'='*80}")
print("WHY NO SHIPPING COSTS?")
print("="*80)
print(f"  Shipped but no cost: {len(shipped_orders)} orders")
print(f"  Not yet shipped: {len(pending_orders)} orders")
print(f"  Have shipping costs: {len(orders_with_shipping)} orders")
print(f"\n  Theory: Shipping costs appear in Financial Events when labels are")
print(f"          purchased. Shipped orders without costs may have had labels")
print(f"          purchased before the 30-day window.")
