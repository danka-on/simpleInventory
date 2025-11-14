"""
Check eBay order data for fee information
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

# Get a recent order
from_date = (datetime.now() - timedelta(days=30)).strftime('%Y-%m-%dT%H:%M:%S.000Z')

url = "https://api.ebay.com/sell/fulfillment/v1/order"
params = {
    'filter': f'lastmodifieddate:[{from_date}..]',
    'limit': 5
}

response = requests.get(url, headers=headers, params=params, timeout=30)

if response.status_code == 200:
    data = response.json()
    orders = data.get('orders', [])
    
    if orders:
        print("SAMPLE ORDER - LOOKING FOR FEE DATA")
        print("=" * 80)
        
        order = orders[0]
        order_id = order.get('legacyOrderId', order.get('orderId'))
        
        print(f"\nOrder ID: {order_id}")
        print("\nChecking for fee-related fields:")
        
        # Check top-level fields
        fee_keywords = ['fee', 'commission', 'marketplace', 'ebay']
        for key in order.keys():
            if any(kw in key.lower() for kw in fee_keywords):
                print(f"  Found: {key} = {order[key]}")
        
        # Check pricingSummary
        if 'pricingSummary' in order:
            print("\npricingSummary:")
            pricing = order['pricingSummary']
            print(json.dumps(pricing, indent=2))
        
        # Check paymentSummary
        if 'paymentSummary' in order:
            print("\npaymentSummary:")
            payment = order['paymentSummary']
            print(json.dumps(payment, indent=2))
        
        # Check totalFeeBasisAmount and totalMarketplaceFee
        if 'totalFeeBasisAmount' in order:
            print(f"\ntotalFeeBasisAmount: {order['totalFeeBasisAmount']}")
        
        if 'totalMarketplaceFee' in order:
            print(f"totalMarketplaceFee: {order['totalMarketplaceFee']}")
        
        # Check line items for fees
        if 'lineItems' in order:
            print("\nLine Items (checking for fees):")
            for i, item in enumerate(order['lineItems'][:1]):  # Just first item
                print(f"\n  Item {i+1}:")
                for key in item.keys():
                    if any(kw in key.lower() for kw in fee_keywords):
                        print(f"    {key} = {item[key]}")
                
                # Check if there's a total or pricing breakdown
                if 'total' in item:
                    print(f"    Total: {item['total']}")
                if 'lineItemCost' in item:
                    print(f"    Line Item Cost: {item['lineItemCost']}")
                if 'ebayCollectedCharges' in item:
                    print(f"    eBay Collected Charges: {item['ebayCollectedCharges']}")
        
        # Full order dump
        print("\n" + "=" * 80)
        print("FULL ORDER STRUCTURE:")
        print("=" * 80)
        print(json.dumps(order, indent=2))
        
else:
    print(f"❌ Error: {response.status_code}")
    print(response.text)
