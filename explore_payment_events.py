#!/usr/bin/env python3
"""
Search for shipping costs in Finances API transaction/payment events
The /payments/events page suggests there's transaction-level data
"""

from amazon_manager import AmazonManager
from sp_api.api import Finances
from datetime import datetime, timedelta
import json
import time

amazon = AmazonManager()

print("=" * 80)
print("EXPLORING FINANCES API FOR PAYMENT/TRANSACTION DATA")
print("=" * 80)

finances_api = Finances(credentials=amazon.credentials, marketplace=amazon.marketplace)
posted_after = (datetime.utcnow() - timedelta(days=30)).isoformat()

# Try to get financial events with all available data
print(f"\n🔄 Fetching first page of financial events...")
response = finances_api.list_financial_events(PostedAfter=posted_after, MaxResultsPerPage=100)

if response.errors:
    print(f"❌ Errors: {response.errors}")
else:
    payload = response.payload
    financial_events = payload.get('FinancialEvents', {})
    
    print("\n📦 All available event types in FinancialEvents:")
    print("=" * 80)
    
    for key, value in financial_events.items():
        if isinstance(value, list):
            print(f"\n{key}: {len(value)} events")
            
            # Show structure of first item if available
            if value:
                print(f"  First item keys: {list(value[0].keys())}")
                
                # Look specifically for payment/charge/service fee related events
                if any(word in key.lower() for word in ['service', 'charge', 'fee', 'adjustment', 'debt']):
                    print(f"\n  📋 Showing first 3 items from {key}:")
                    for i, item in enumerate(value[:3], 1):
                        print(f"\n  --- Item {i} ---")
                        print(json.dumps(item, indent=4, default=str))

# Also try list_financial_event_groups which groups transactions
print("\n" + "=" * 80)
print("CHECKING FINANCIAL EVENT GROUPS (Settlement periods)")
print("=" * 80)

try:
    groups_response = finances_api.list_financial_event_groups(
        FinancialEventGroupStartedAfter=posted_after,
        MaxResultsPerPage=10
    )
    
    if groups_response.errors:
        print(f"❌ Errors: {groups_response.errors}")
    else:
        groups = groups_response.payload.get('FinancialEventGroupList', [])
        print(f"\n✅ Found {len(groups)} financial event groups (settlement periods)")
        
        if groups:
            # Get events for first group
            first_group = groups[0]
            group_id = first_group.get('FinancialEventGroupId')
            print(f"\n📊 Getting events for group: {group_id}")
            
            group_events = finances_api.list_financial_events_by_group_id(
                EventGroupId=group_id,
                MaxResultsPerPage=100
            )
            
            if not group_events.errors:
                group_financial_events = group_events.payload.get('FinancialEvents', {})
                
                print("\n  Event types in this settlement:")
                for key, value in group_financial_events.items():
                    if isinstance(value, list) and value:
                        print(f"    - {key}: {len(value)} events")
                        
                        # Look for service fee events
                        if 'service' in key.lower() or 'charge' in key.lower():
                            print(f"\n      Sample from {key}:")
                            print(json.dumps(value[0], indent=6, default=str))
                            
except Exception as e:
    print(f"⚠️ Error checking event groups: {e}")

print("\n" + "=" * 80)
print("SUMMARY")
print("=" * 80)
print("\nThe /payments/events page you mentioned shows shipping charges.")
print("We need to find which API endpoint contains that transaction data.")
print("\nPossible locations:")
print("  1. ServiceFeeEventList in FinancialEvents")
print("  2. ChargebackEventList")
print("  3. SellerDealPaymentEventList") 
print("  4. Different settlement-grouped events")
