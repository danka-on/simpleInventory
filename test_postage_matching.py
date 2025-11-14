#!/usr/bin/env python3
"""
Test PostageBilling_Postage matching logic
"""

from amazon_manager import AmazonManager

amazon = AmazonManager()

print("=" * 80)
print("TESTING PostageBilling_Postage EXTRACTION")
print("=" * 80)

# Get financial data
financial_data = amazon.get_financial_events(days_back=30)

print("\n" + "=" * 80)
print("RESULTS:")
print("=" * 80)

orders_with_shipping = []
orders_without_shipping = []

for order_id, data in financial_data.items():
    if data['shipping_cost'] > 0:
        orders_with_shipping.append((order_id, data['shipping_cost']))
    else:
        orders_without_shipping.append(order_id)

print(f"\n✅ Orders with shipping costs: {len(orders_with_shipping)}/{len(financial_data)}")

if orders_with_shipping:
    print("\nOrders with shipping:")
    for order_id, cost in orders_with_shipping[:10]:
        print(f"  {order_id}: ${cost:.2f}")
    if len(orders_with_shipping) > 10:
        print(f"  ... and {len(orders_with_shipping) - 10} more")
else:
    print("\n⚠️ NO ORDERS WITH SHIPPING COSTS!")
    print("\nDebugging: Let me check if dates are matching...")
    
    # Show sample order IDs and their posted dates
    from sp_api.api import Finances
    from datetime import datetime, timedelta
    import time
    
    finances_api = Finances(credentials=amazon.credentials, marketplace=amazon.marketplace)
    posted_after = (datetime.utcnow() - timedelta(days=30)).isoformat()
    
    response = finances_api.list_financial_events(PostedAfter=posted_after, MaxResultsPerPage=100)
    
    if not response.errors:
        financial_events = response.payload.get('FinancialEvents', {})
        shipment_events = financial_events.get('ShipmentEventList', [])
        adjustment_events = financial_events.get('AdjustmentEventList', [])
        
        # Get PostageBilling_Postage dates
        postage_dates = set()
        for adj in adjustment_events:
            if adj.get('AdjustmentType') == 'PostageBilling_Postage':
                amount = float(adj.get('AdjustmentAmount', {}).get('CurrencyAmount', 0))
                if amount < 0:
                    postage_dates.add(adj.get('PostedDate', ''))
        
        # Get shipment event dates
        shipment_dates = set()
        for event in shipment_events[:5]:
            shipment_dates.add(event.get('PostedDate', ''))
        
        print("\n📅 Sample shipment event dates:")
        for date in list(shipment_dates)[:5]:
            print(f"  {date}")
        
        print("\n📅 Sample PostageBilling_Postage dates:")
        for date in list(postage_dates)[:5]:
            print(f"  {date}")
        
        # Check for overlap
        overlap = shipment_dates & postage_dates
        print(f"\n🔗 Date matches: {len(overlap)}")
        if overlap:
            print("  Matching dates found:")
            for date in list(overlap)[:5]:
                print(f"    {date}")
