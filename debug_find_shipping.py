"""Debug script to find shipping label costs in Financial Events"""
from amazon_manager import AmazonManager
import json

amazon = AmazonManager()

print("="*80)
print("DETAILED FINANCIAL EVENT ANALYSIS - Looking for Shipping Label Costs")
print("="*80)

from sp_api.api import Finances
from datetime import datetime, timedelta

finances_api = Finances(credentials=amazon.credentials, marketplace=amazon.marketplace)
posted_after = (datetime.utcnow() - timedelta(days=7)).isoformat()

response = finances_api.list_financial_events(PostedAfter=posted_after, MaxResultsPerPage=3)
payload = response.payload
events = payload.get('FinancialEvents', {})
shipment_events = events.get('ShipmentEventList', [])

if shipment_events:
    print(f"\n✅ Found {len(shipment_events)} shipment events\n")
    
    for i, event in enumerate(shipment_events, 1):
        order_id = event.get('AmazonOrderId')
        print(f"\n{'='*80}")
        print(f"EVENT #{i}: Order {order_id}")
        print(f"{'='*80}")
        
        # Check all possible fee types
        print("\n📋 ALL FEES:")
        for item in event.get('ShipmentItemList', []):
            item_fees = item.get('ItemFeeList', [])
            for fee in item_fees:
                fee_type = fee.get('FeeType', '')
                fee_amount = fee.get('FeeAmount', {}).get('CurrencyAmount', 0)
                print(f"  {fee_type}: ${fee_amount}")
        
        # Check all charge types
        print("\n💰 ALL CHARGES:")
        for item in event.get('ShipmentItemList', []):
            item_charges = item.get('ItemChargeList', [])
            for charge in item_charges:
                charge_type = charge.get('ChargeType', '')
                charge_amount = charge.get('ChargeAmount', {}).get('CurrencyAmount', 0)
                print(f"  {charge_type}: ${charge_amount}")
        
        # Check for adjustments
        print("\n🔄 ADJUSTMENTS:")
        for item in event.get('ShipmentItemList', []):
            adjustments = item.get('ItemChargeAdjustmentList', [])
            if adjustments:
                for adj in adjustments:
                    adj_type = adj.get('ChargeType', '')
                    adj_amount = adj.get('ChargeAmount', {}).get('CurrencyAmount', 0)
                    print(f"  {adj_type}: ${adj_amount}")
            else:
                print("  (none)")
        
        # Show FULL event structure for first event
        if i == 1:
            print(f"\n{'='*80}")
            print("COMPLETE RAW STRUCTURE (Event #1):")
            print(f"{'='*80}")
            print(json.dumps(event, indent=2, default=str))
        
        if i >= 3:  # Show first 3
            break
else:
    print("❌ No shipment events found")
