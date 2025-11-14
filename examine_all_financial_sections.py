#!/usr/bin/env python3
"""
Examine ALL sections of Financial Events API response
"""

from amazon_manager import AmazonManager
from sp_api.api import Finances
from datetime import datetime, timedelta
import json

amazon = AmazonManager()

print("=" * 80)
print("FULL FINANCIAL EVENTS STRUCTURE ANALYSIS")
print("=" * 80)

finances_api = Finances(credentials=amazon.credentials, marketplace=amazon.marketplace)
posted_after = (datetime.utcnow() - timedelta(days=30)).isoformat()

print(f"\n🔄 Fetching financial events...")
response = finances_api.list_financial_events(PostedAfter=posted_after, MaxResultsPerPage=100)

if response.errors:
    print(f"❌ Errors: {response.errors}")
else:
    payload = response.payload
    financial_events = payload.get('FinancialEvents', {})
    
    print("\n📦 Top-level FinancialEvents keys:")
    for key in financial_events.keys():
        event_list = financial_events[key]
        if isinstance(event_list, list):
            print(f"  - {key}: {len(event_list)} items")
        else:
            print(f"  - {key}: {type(event_list)}")
    
    # Check each event list type
    for key, event_list in financial_events.items():
        if isinstance(event_list, list) and len(event_list) > 0:
            print(f"\n{'=' * 80}")
            print(f"{key} - First item structure:")
            print('=' * 80)
            first_item = event_list[0]
            print(json.dumps(first_item, indent=2, default=str))
            
            # If there are many items, look for any with shipping-related data
            if 'shipping' in key.lower() or 'service' in key.lower():
                print(f"\n  Examining all {len(event_list)} items in {key}...")
                for i, item in enumerate(event_list):
                    print(f"\n  Item {i+1}:")
                    print(json.dumps(item, indent=4, default=str))
