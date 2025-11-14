#!/usr/bin/env python3
"""
Debug script to examine ShipmentFeeList structure in Financial Events
"""

from amazon_manager import AmazonManager
from sp_api.api import Finances
from datetime import datetime, timedelta
import json

amazon = AmazonManager()

print("=" * 80)
print("DEBUGGING ShipmentFeeList STRUCTURE")
print("=" * 80)

finances_api = Finances(credentials=amazon.credentials, marketplace=amazon.marketplace)
posted_after = (datetime.utcnow() - timedelta(days=30)).isoformat()

print(f"\n🔄 Fetching financial events from last 30 days...")
response = finances_api.list_financial_events(PostedAfter=posted_after, MaxResultsPerPage=100)

if response.errors:
    print(f"❌ Errors: {response.errors}")
else:
    payload = response.payload
    financial_events = payload.get('FinancialEvents', {})
    shipment_events = financial_events.get('ShipmentEventList', [])
    
    print(f"✅ Retrieved {len(shipment_events)} shipment events\n")
    
    # Look for events with ShipmentFeeList
    events_with_fees = []
    for event in shipment_events:
        shipment_fee_list = event.get('ShipmentFeeList', [])
        if shipment_fee_list:
            events_with_fees.append(event)
    
    print(f"📦 Found {len(events_with_fees)} events with ShipmentFeeList")
    
    if events_with_fees:
        print("\n" + "=" * 80)
        print("SAMPLE EVENT WITH SHIPMENT FEES:")
        print("=" * 80)
        
        # Show first 3 events with fees
        for i, event in enumerate(events_with_fees[:3], 1):
            order_id = event.get('AmazonOrderId', 'Unknown')
            print(f"\n--- Event {i}: {order_id} ---")
            print(f"PostedDate: {event.get('PostedDate')}")
            
            shipment_fee_list = event.get('ShipmentFeeList', [])
            print(f"ShipmentFeeList ({len(shipment_fee_list)} fees):")
            for fee in shipment_fee_list:
                print(f"  - FeeType: {fee.get('FeeType')}")
                amount = fee.get('Amount', {})
                print(f"    Amount: {json.dumps(amount, indent=6)}")
    else:
        print("\n⚠️  NO EVENTS FOUND WITH ShipmentFeeList!")
        print("\nLet me show you a sample shipment event structure:")
        if shipment_events:
            sample = shipment_events[0]
            print(f"\n--- Sample Event: {sample.get('AmazonOrderId')} ---")
            print("Available keys:")
            for key in sample.keys():
                print(f"  - {key}")
            
            # Show item-level structure
            items = sample.get('ShipmentItemList', [])
            if items:
                print(f"\nShipmentItemList[0] keys:")
                for key in items[0].keys():
                    print(f"  - {key}")
                
                # Check for fees at item level
                item_fees = items[0].get('ItemFeeList', [])
                if item_fees:
                    print(f"\nItemFeeList ({len(item_fees)} fees):")
                    for fee in item_fees:
                        print(f"  - FeeType: {fee.get('FeeType')}")
                        fee_amount = fee.get('FeeAmount', {})
                        print(f"    FeeAmount: {json.dumps(fee_amount, indent=6)}")
