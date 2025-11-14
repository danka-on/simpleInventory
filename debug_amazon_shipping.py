"""
Debug script to check Amazon order shipping data
"""

from amazon_manager import AmazonManager
import json

def debug_shipping():
    """Check what shipping data Amazon provides"""
    print("=" * 60)
    print("Debugging Amazon Shipping Data")
    print("=" * 60)
    
    amazon = AmazonManager()
    
    # Get recent orders
    print("\n1. Fetching orders...")
    orders = amazon.get_orders(days_back=7, max_results=5)
    
    if not orders:
        print("No orders found")
        return
    
    print(f"\n✅ Found {len(orders)} orders\n")
    
    # Check each order's items for shipping data
    for i, order in enumerate(orders[:3], 1):  # Check first 3 orders
        order_id = order.get('AmazonOrderId')
        print(f"\n{'='*60}")
        print(f"Order {i}: {order_id}")
        print(f"{'='*60}")
        
        # Get order-level shipping data
        order_total = order.get('OrderTotal', {})
        print(f"\n📦 Order-Level Data:")
        print(f"  Order Total: ${order_total.get('Amount', 0)}")
        print(f"  Currency: {order_total.get('CurrencyCode', 'N/A')}")
        print(f"  Order Status: {order.get('OrderStatus')}")
        print(f"  Fulfillment Channel: {order.get('FulfillmentChannel', 'N/A')}")
        print(f"  Is Business Order: {order.get('IsBusinessOrder', False)}")
        print(f"  Is Prime: {order.get('IsPrime', False)}")
        
        # Get items
        print(f"\n📋 Item-Level Data:")
        items = amazon.get_order_items(order_id)
        
        for j, item in enumerate(items, 1):
            print(f"\n  Item {j}: {item.get('Title', 'N/A')[:50]}")
            print(f"  ASIN: {item.get('ASIN')}")
            
            # Item price
            item_price = item.get('ItemPrice', {})
            print(f"  Item Price: ${item_price.get('Amount', 0)} {item_price.get('CurrencyCode', '')}")
            
            # Shipping price
            shipping_price = item.get('ShippingPrice', {})
            print(f"  Shipping Price: ${shipping_price.get('Amount', 0)} {shipping_price.get('CurrencyCode', '')}")
            
            # Shipping discount
            shipping_discount = item.get('ShippingDiscount', {})
            print(f"  Shipping Discount: ${shipping_discount.get('Amount', 0)} {shipping_discount.get('CurrencyCode', '')}")
            
            # Tax
            item_tax = item.get('ItemTax', {})
            print(f"  Item Tax: ${item_tax.get('Amount', 0)} {item_tax.get('CurrencyCode', '')}")
            
            # Promotion discount
            promotion_discount = item.get('PromotionDiscount', {})
            print(f"  Promotion Discount: ${promotion_discount.get('Amount', 0)} {promotion_discount.get('CurrencyCode', '')}")
            
            # Show full item structure for first item
            if i == 1 and j == 1:
                print(f"\n  📄 Full Item Structure:")
                print(f"  {json.dumps(item, indent=4, default=str)}")
    
    print("\n" + "=" * 60)
    print("Debug completed!")
    print("=" * 60)

if __name__ == "__main__":
    debug_shipping()
