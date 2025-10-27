from amazon_manager import AmazonManager

print("📦 Fetching recent Amazon orders...\n")

manager = AmazonManager()
orders = manager.get_orders(days_back=7, max_results=10)

print(f"Found {len(orders)} orders\n")

if orders:
    print("Recent orders:")
    for i, order in enumerate(orders[:5], 1):
        order_id = order.get('AmazonOrderId', 'N/A')
        status = order.get('OrderStatus', 'N/A')
        total = order.get('OrderTotal', {})
        amount = total.get('Amount', 'N/A')
        currency = total.get('CurrencyCode', '')
        purchase_date = order.get('PurchaseDate', 'N/A')
        
        print(f"{i}. Order: {order_id}")
        print(f"   Status: {status}")
        print(f"   Total: {currency} {amount}")
        print(f"   Date: {purchase_date}")
        print()
else:
    print("No orders found in the last 7 days")

print("\n" + "="*60)
print("To sync these orders to your database, use the app:")
print("1. Start Flask: python app.py")
print("2. Go to /tools")
print("3. Click 'Sync Amazon Orders'")
print("="*60)
