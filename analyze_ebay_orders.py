"""
Deep dive into eBay Fulfillment API order structure to find returns
"""
import requests
from token_manager import get_access_token
from datetime import datetime, timedelta
import json

token = get_access_token()
headers = {
    'Authorization': f'Bearer {token}',
    'Content-Type': 'application/json',
    'Accept': 'application/json'
}

# Get recent orders (last 90 days)
from_date = (datetime.now() - timedelta(days=90)).strftime('%Y-%m-%dT%H:%M:%S.000Z')

url = "https://api.ebay.com/sell/fulfillment/v1/order"
params = {
    'filter': f'lastmodifieddate:[{from_date}..]',
    'limit': 50  # Get more orders to find returns
}

print("🔍 Fetching recent eBay orders to analyze structure...\n")

response = requests.get(url, headers=headers, params=params, timeout=30)

if response.status_code == 200:
    data = response.json()
    orders = data.get('orders', [])
    
    print(f"✅ Retrieved {len(orders)} orders\n")
    print("=" * 80)
    
    # Analyze for patterns related to returns/cancellations
    cancelled_orders = []
    orders_with_refunds = []
    
    for order in orders:
        order_id = order.get('legacyOrderId', order.get('orderId'))
        status = order.get('orderFulfillmentStatus')
        cancel_status = order.get('cancelStatus', {})
        cancel_state = cancel_status.get('cancelState', 'NONE_REQUESTED')
        
        # Check for cancellations
        if cancel_state != 'NONE_REQUESTED':
            cancelled_orders.append(order)
            print(f"\n🔴 CANCELLED ORDER FOUND: {order_id}")
            print(f"   Cancel State: {cancel_state}")
            print(f"   Fulfillment Status: {status}")
            if 'cancelRequests' in cancel_status:
                print(f"   Cancel Requests: {cancel_status['cancelRequests']}")
        
        # Check payment summary for refunds
        payment_summary = order.get('paymentSummary', {})
        if 'refunds' in payment_summary:
            orders_with_refunds.append(order)
            print(f"\n💰 ORDER WITH REFUNDS: {order_id}")
            print(f"   Refunds: {payment_summary['refunds']}")
        
        # Check for returns in line items
        line_items = order.get('lineItems', [])
        for item in line_items:
            if 'returnRequests' in item or 'cancelStatus' in item or 'refunds' in item:
                print(f"\n📦 ITEM WITH RETURN DATA: {order_id}")
                print(f"   Item: {item.get('title', 'Unknown')[:50]}")
                if 'returnRequests' in item:
                    print(f"   Return Requests: {item['returnRequests']}")
                if 'cancelStatus' in item:
                    print(f"   Cancel Status: {item['cancelStatus']}")
                if 'refunds' in item:
                    print(f"   Refunds: {item['refunds']}")
    
    print("\n" + "=" * 80)
    print(f"\n📊 SUMMARY:")
    print(f"   Total orders checked: {len(orders)}")
    print(f"   Cancelled orders: {len(cancelled_orders)}")
    print(f"   Orders with refunds: {len(orders_with_refunds)}")
    
    # Show a sample order structure
    if orders:
        print("\n" + "=" * 80)
        print("📋 SAMPLE ORDER STRUCTURE (first order):")
        print("=" * 80)
        sample = orders[0]
        print(json.dumps({
            'orderId': sample.get('orderId'),
            'legacyOrderId': sample.get('legacyOrderId'),
            'orderFulfillmentStatus': sample.get('orderFulfillmentStatus'),
            'cancelStatus': sample.get('cancelStatus'),
            'paymentSummary': sample.get('paymentSummary'),
            'lineItems_structure': [
                {k: v for k, v in item.items() if k in ['title', 'sku', 'lineItemId', 'quantity', 'returnRequests', 'cancelStatus', 'refunds']}
                for item in sample.get('lineItems', [])[:1]  # Just first item
            ]
        }, indent=2))
        
        # Check if we need to query individual order details
        print("\n" + "=" * 80)
        print("💡 NEXT STEP: Try fetching individual order details")
        print("=" * 80)
        first_order_id = sample.get('orderId')
        detail_url = f"https://api.ebay.com/sell/fulfillment/v1/order/{first_order_id}"
        detail_response = requests.get(detail_url, headers=headers, timeout=30)
        
        if detail_response.status_code == 200:
            detail_data = detail_response.json()
            print(f"\n✅ Individual order detail retrieved")
            print(f"   Keys: {list(detail_data.keys())}")
            
            # Look for return-related fields
            return_fields = [k for k in detail_data.keys() if 'return' in k.lower() or 'refund' in k.lower()]
            if return_fields:
                print(f"   Return-related fields: {return_fields}")
                for field in return_fields:
                    print(f"   {field}: {detail_data[field]}")

else:
    print(f"❌ Error: {response.status_code}")
    print(response.text)
