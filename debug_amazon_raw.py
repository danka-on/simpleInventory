"""Debug Amazon API raw response for shipping data"""
from amazon_manager import AmazonManager
import json

amazon = AmazonManager()

# Get one recent order
print("Fetching recent order...")
orders = amazon.get_orders(days_back=7, max_results=3)

if orders:
    print(f"\nFound {len(orders)} orders\n")
    
    for order in orders[:2]:
        order_id = order.get('AmazonOrderId')
        print(f"\n{'='*70}")
        print(f"Order: {order_id}")
        print(f"{'='*70}")
        
        # Show order-level data
        print("\n📦 ORDER LEVEL RAW:")
        print(json.dumps(order, indent=4, default=str))
        
        print("\n📦 ORDER LEVEL SUMMARY:")
        print(f"  Order Status: {order.get('OrderStatus')}")
        print(f"  Order Total: {order.get('OrderTotal')}")
        print(f"  Is Prime: {order.get('IsPrime', False)}")
        print(f"  Fulfillment Channel: {order.get('FulfillmentChannel')}")
        
        # Get items
        print("\n📋 ITEM LEVEL:")
        items = amazon.get_order_items(order_id)
        
        for i, item in enumerate(items, 1):
            print(f"\n  --- Item {i} ---")
            print(f"  Title: {item.get('Title', 'N/A')[:50]}")
            
            # Show all price-related fields
            for key in ['ItemPrice', 'ShippingPrice', 'ShippingDiscount', 'ShippingTax', 
                       'ItemTax', 'PromotionDiscount']:
                if key in item:
                    value = item[key]
                    if isinstance(value, dict):
                        amount = value.get('Amount', 0)
                        currency = value.get('CurrencyCode', '')
                        print(f"  {key}: ${amount} {currency}")
                    else:
                        print(f"  {key}: {value}")
            
            # Show raw item for first one
            if i == 1:
                print(f"\n  📄 RAW ITEM DATA:")
                print(json.dumps(item, indent=4, default=str))
        
        print()
else:
    print("No orders found")
