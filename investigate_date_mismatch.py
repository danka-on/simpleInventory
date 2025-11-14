#!/usr/bin/env python3
"""
Investigate why only 6 of 83 PostageBilling_Postage charges match to orders
"""

from amazon_manager import AmazonManager
from sp_api.api import Finances
from datetime import datetime, timedelta
import time

amazon = AmazonManager()

print("=" * 80)
print("INVESTIGATING PostageBilling_Postage DATE MISMATCH")
print("=" * 80)

finances_api = Finances(credentials=amazon.credentials, marketplace=amazon.marketplace)
posted_after = (datetime.utcnow() - timedelta(days=30)).isoformat()

# Get all events
all_shipment_events = []
all_adjustment_events = []
next_token = None
page = 1

while True:
    if next_token:
        response = finances_api.list_financial_events(NextToken=next_token)
    else:
        response = finances_api.list_financial_events(PostedAfter=posted_after, MaxResultsPerPage=100)
    
    if response.errors:
        break
    
    payload = response.payload
    financial_events = payload.get('FinancialEvents', {})
    all_shipment_events.extend(financial_events.get('ShipmentEventList', []))
    all_adjustment_events.extend(financial_events.get('AdjustmentEventList', []))
    
    next_token = payload.get('NextToken')
    if not next_token:
        break
    page += 1
    time.sleep(1)

print(f"📦 Total shipment events: {len(all_shipment_events)}")
print(f"📦 Total adjustment events: {len(all_adjustment_events)}")

# Get all PostageBilling_Postage
postage_billings = []
for adj in all_adjustment_events:
    if adj.get('AdjustmentType') == 'PostageBilling_Postage':
        amount = float(adj.get('AdjustmentAmount', {}).get('CurrencyAmount', 0))
        if amount < 0:
            postage_billings.append({
                'date': adj.get('PostedDate', ''),
                'amount': abs(amount)
            })

print(f"💰 PostageBilling_Postage charges: {len(postage_billings)}")

# Get all shipment event dates with order IDs
shipment_dates = {}
for event in all_shipment_events:
    date = event.get('PostedDate', '')
    order_id = event.get('AmazonOrderId', '')
    if date not in shipment_dates:
        shipment_dates[date] = []
    shipment_dates[date].append(order_id)

# Check matches
postage_dates = {}
for pb in postage_billings:
    date = pb['date']
    if date not in postage_dates:
        postage_dates[date] = []
    postage_dates[date].append(pb['amount'])

# Find matching vs non-matching dates
matching_dates = set(shipment_dates.keys()) & set(postage_dates.keys())
postage_only_dates = set(postage_dates.keys()) - set(shipment_dates.keys())

print(f"\n📅 Shipment event unique dates: {len(shipment_dates)}")
print(f"📅 PostageBilling unique dates: {len(postage_dates)}")
print(f"🔗 Matching dates: {len(matching_dates)}")
print(f"⚠️  Postage dates with NO matching shipment: {len(postage_only_dates)}")

print("\n" + "=" * 80)
print("SAMPLE MATCHING DATES:")
print("=" * 80)
for date in list(matching_dates)[:5]:
    orders = shipment_dates[date]
    costs = postage_dates[date]
    print(f"\n{date}:")
    print(f"  Orders ({len(orders)}): {', '.join(orders[:3])}")
    print(f"  Costs ({len(costs)}): ${', $'.join([f'{c:.2f}' for c in costs[:3]])}")

print("\n" + "=" * 80)
print("SAMPLE NON-MATCHING POSTAGE DATES:")
print("=" * 80)
print("(These shipping costs have no corresponding shipment event)")
for date in list(postage_only_dates)[:10]:
    costs = postage_dates[date]
    print(f"  {date[:10]}: {len(costs)} charges (${sum(costs):.2f} total)")

print("\n" + "=" * 80)
print("CONCLUSION:")
print("=" * 80)
print(f"Only {len(matching_dates)} dates have both shipment events AND PostageBilling charges.")
print(f"This means {len(postage_only_dates)} PostageBilling dates don't match any shipments.")
print("\nPossible reasons:")
print("  1. Shipping labels purchased before orders were placed (bulk purchasing)")
print("  2. Labels for orders outside the 30-day window")
print("  3. Time zone differences causing date mismatches")
print("  4. Labels voided/refunded without being used")
