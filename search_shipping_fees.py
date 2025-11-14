#!/usr/bin/env python3
"""
Search for non-zero ShippingHB fees in Financial Events
"""

from amazon_manager import AmazonManager
from sp_api.api import Finances
from datetime import datetime, timedelta
import json

amazon = AmazonManager()

print("=" * 80)
print("SEARCHING FOR NON-ZERO SHIPPING FEES")
print("=" * 80)

finances_api = Finances(credentials=amazon.credentials, marketplace=amazon.marketplace)
posted_after = (datetime.utcnow() - timedelta(days=30)).isoformat()

print(f"\n🔄 Fetching financial events from last 30 days...")

all_shipment_events = []
next_token = None
page = 1

# Paginate through all events
while True:
    if next_token:
        response = finances_api.list_financial_events(NextToken=next_token)
    else:
        response = finances_api.list_financial_events(PostedAfter=posted_after, MaxResultsPerPage=100)
    
    if response.errors:
        break
    
    payload = response.payload
    financial_events = payload.get('FinancialEvents', {})
    shipment_events = financial_events.get('ShipmentEventList', [])
    all_shipment_events.extend(shipment_events)
    
    print(f"  Page {page}: {len(shipment_events)} events")
    
    next_token = payload.get('NextToken')
    if not next_token:
        break
    page += 1

print(f"\n✅ Total shipment events: {len(all_shipment_events)}")

# Search for non-zero shipping fees
orders_with_shipping = []
shipping_fee_types = {}

for event in all_shipment_events:
    order_id = event.get('AmazonOrderId')
    items = event.get('ShipmentItemList', [])
    
    for item in items:
        fees = item.get('ItemFeeList', [])
        for fee in fees:
            fee_type = fee.get('FeeType')
            amount = float(fee.get('FeeAmount', {}).get('CurrencyAmount', 0))
            
            # Track all fee types we see
            if fee_type not in shipping_fee_types:
                shipping_fee_types[fee_type] = {'count': 0, 'non_zero': 0, 'example_amounts': []}
            
            shipping_fee_types[fee_type]['count'] += 1
            
            if amount != 0:
                shipping_fee_types[fee_type]['non_zero'] += 1
                if len(shipping_fee_types[fee_type]['example_amounts']) < 5:
                    shipping_fee_types[fee_type]['example_amounts'].append(abs(amount))
            
            # Look for shipping-related fees
            if 'ship' in fee_type.lower() and amount != 0:
                orders_with_shipping.append({
                    'order_id': order_id,
                    'fee_type': fee_type,
                    'amount': abs(amount)
                })

print("\n" + "=" * 80)
print("FEE TYPE SUMMARY:")
print("=" * 80)
for fee_type, stats in sorted(shipping_fee_types.items()):
    print(f"\n{fee_type}:")
    print(f"  Total occurrences: {stats['count']}")
    print(f"  Non-zero: {stats['non_zero']}")
    if stats['example_amounts']:
        amounts_str = ', '.join([f"${a:.2f}" for a in stats['example_amounts']])
        print(f"  Example amounts: {amounts_str}")

print("\n" + "=" * 80)
print(f"ORDERS WITH NON-ZERO SHIPPING FEES: {len(orders_with_shipping)}")
print("=" * 80)

if orders_with_shipping:
    for order in orders_with_shipping[:10]:
        print(f"  {order['order_id']}: {order['fee_type']} = ${order['amount']:.2f}")
else:
    print("  ⚠️  No non-zero shipping fees found in ItemFeeList!")
    print("\n  This suggests shipping costs might be:")
    print("  1. In a different API endpoint")
    print("  2. Not included in Financial Events API")
    print("  3. Recorded differently for Buy Shipping purchases")
