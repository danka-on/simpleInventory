#!/usr/bin/env python3
"""
Search for ShipmentFeeList with ShippingLabel fees across extended time period
"""

from amazon_manager import AmazonManager
from sp_api.api import Finances
from datetime import datetime, timedelta
import time

amazon = AmazonManager()

print("=" * 80)
print("SEARCHING FOR ShippingLabel FEES IN ShipmentFeeList")
print("=" * 80)

finances_api = Finances(credentials=amazon.credentials, marketplace=amazon.marketplace)

# Try 90 days to find some shipping label purchases
posted_after = (datetime.utcnow() - timedelta(days=90)).isoformat()

print(f"\n🔄 Fetching financial events from last 90 days...")

all_shipment_events = []
next_token = None
page = 1

# Paginate through all events
while True:
    try:
        if next_token:
            response = finances_api.list_financial_events(NextToken=next_token)
        else:
            response = finances_api.list_financial_events(PostedAfter=posted_after, MaxResultsPerPage=100)
        
        if response.errors:
            print(f"Error: {response.errors}")
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
        time.sleep(1)
    except Exception as e:
        print(f"Error: {e}")
        break

print(f"\n✅ Total shipment events collected: {len(all_shipment_events)}")

# Search for ShipmentFeeList at event level
events_with_shipment_fees = []
total_with_fee_list = 0
total_with_shipping_label = 0

for event in all_shipment_events:
    order_id = event.get('AmazonOrderId')
    shipment_fee_list = event.get('ShipmentFeeList', [])
    
    if shipment_fee_list:
        total_with_fee_list += 1
        
        # Check for ShippingLabel fee type
        for fee in shipment_fee_list:
            fee_type = fee.get('FeeType')
            if fee_type == 'ShippingLabel':
                amount = float(fee.get('Amount', {}).get('CurrencyAmount', 0))
                events_with_shipment_fees.append({
                    'order_id': order_id,
                    'posted_date': event.get('PostedDate'),
                    'amount': abs(amount),
                    'fee': fee
                })
                total_with_shipping_label += 1
                break

print(f"\n📦 Events with ShipmentFeeList: {total_with_fee_list}")
print(f"📦 Events with ShippingLabel fee: {total_with_shipping_label}")

if events_with_shipment_fees:
    print("\n" + "=" * 80)
    print(f"FOUND {len(events_with_shipment_fees)} ORDERS WITH SHIPPING LABEL COSTS:")
    print("=" * 80)
    
    for item in events_with_shipment_fees[:20]:
        posted = item['posted_date'][:10] if item['posted_date'] else 'Unknown'
        print(f"  {item['order_id']}: ${item['amount']:.2f} (Posted: {posted})")
    
    if len(events_with_shipment_fees) > 20:
        print(f"  ... and {len(events_with_shipment_fees) - 20} more")
    
    # Show full structure of first one
    print("\n" + "=" * 80)
    print("EXAMPLE SHIPMENT FEE STRUCTURE:")
    print("=" * 80)
    import json
    print(json.dumps(events_with_shipment_fees[0]['fee'], indent=2))
else:
    print("\n⚠️  NO ShippingLabel FEES FOUND!")
    print("\nThis means:")
    print("  1. You haven't purchased shipping labels through Amazon Buy Shipping")
    print("  2. Or labels were purchased outside the 90-day window")
    print("  3. Or labels are purchased through a different method")
