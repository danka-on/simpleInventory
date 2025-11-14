#!/usr/bin/env python3
"""
Examine PostageBilling_Postage structure for item/order identifiers
"""

from amazon_manager import AmazonManager
from sp_api.api import Finances
from datetime import datetime, timedelta
import json

amazon = AmazonManager()

finances_api = Finances(credentials=amazon.credentials, marketplace=amazon.marketplace)
posted_after = (datetime.utcnow() - timedelta(days=30)).isoformat()

print("=" * 80)
print("EXAMINING PostageBilling_Postage STRUCTURE")
print("=" * 80)

response = finances_api.list_financial_events(PostedAfter=posted_after, MaxResultsPerPage=100)

if not response.errors:
    financial_events = response.payload.get('FinancialEvents', {})
    adjustment_events = financial_events.get('AdjustmentEventList', [])
    
    # Find PostageBilling_Postage items
    postage_items = []
    for adj in adjustment_events:
        if adj.get('AdjustmentType') == 'PostageBilling_Postage':
            amount = float(adj.get('AdjustmentAmount', {}).get('CurrencyAmount', 0))
            if amount < 0:
                postage_items.append(adj)
    
    print(f"\n✅ Found {len(postage_items)} PostageBilling_Postage adjustments")
    
    if postage_items:
        print("\n" + "=" * 80)
        print("FULL STRUCTURE OF FIRST 3 ITEMS:")
        print("=" * 80)
        
        for i, item in enumerate(postage_items[:3], 1):
            print(f"\n--- Item {i} ---")
            print(json.dumps(item, indent=2, default=str))
            print("\nAvailable keys:")
            for key in item.keys():
                value = item[key]
                if isinstance(value, (str, int, float)):
                    print(f"  {key}: {value}")
                elif isinstance(value, dict):
                    print(f"  {key}: {{dict with {len(value)} keys}}")
                elif isinstance(value, list):
                    print(f"  {key}: [list with {len(value)} items]")
    else:
        print("⚠️ No PostageBilling_Postage items found")
else:
    print(f"Error: {response.errors}")
