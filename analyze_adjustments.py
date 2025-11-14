#!/usr/bin/env python3
"""
Analyze all AdjustmentEventList items to find shipping charges
"""

from amazon_manager import AmazonManager
from sp_api.api import Finances
from datetime import datetime, timedelta
import time

amazon = AmazonManager()

print("=" * 80)
print("ANALYZING ADJUSTMENT EVENTS FOR SHIPPING CHARGES")
print("=" * 80)

finances_api = Finances(credentials=amazon.credentials, marketplace=amazon.marketplace)
posted_after = (datetime.utcnow() - timedelta(days=30)).isoformat()

all_adjustments = []
next_token = None
page = 1

print(f"\n🔄 Fetching all adjustment events from last 30 days...")

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
        adjustments = financial_events.get('AdjustmentEventList', [])
        all_adjustments.extend(adjustments)
        
        print(f"  Page {page}: {len(adjustments)} adjustments")
        
        next_token = payload.get('NextToken')
        if not next_token:
            break
        page += 1
        time.sleep(1)
    except Exception as e:
        print(f"Error: {e}")
        break

print(f"\n✅ Total adjustments collected: {len(all_adjustments)}")

# Categorize by adjustment type
adjustment_types = {}
shipping_related = []

for adj in all_adjustments:
    adj_type = adj.get('AdjustmentType', 'Unknown')
    amount = float(adj.get('AdjustmentAmount', {}).get('CurrencyAmount', 0))
    posted = adj.get('PostedDate', '')
    
    if adj_type not in adjustment_types:
        adjustment_types[adj_type] = {'count': 0, 'non_zero': 0, 'total': 0, 'examples': []}
    
    adjustment_types[adj_type]['count'] += 1
    
    if amount != 0:
        adjustment_types[adj_type]['non_zero'] += 1
        adjustment_types[adj_type]['total'] += amount
        
        if len(adjustment_types[adj_type]['examples']) < 3:
            adjustment_types[adj_type]['examples'].append({
                'amount': amount,
                'date': posted[:10]
            })
    
    # Look for shipping-related types
    if any(word in adj_type.lower() for word in ['ship', 'postage', 'label']):
        if amount != 0:
            shipping_related.append({
                'type': adj_type,
                'amount': amount,
                'date': posted[:10],
                'full_date': posted
            })

print("\n" + "=" * 80)
print("ADJUSTMENT TYPES SUMMARY:")
print("=" * 80)

for adj_type, stats in sorted(adjustment_types.items()):
    print(f"\n{adj_type}:")
    print(f"  Count: {stats['count']} | Non-zero: {stats['non_zero']}")
    if stats['non_zero'] > 0:
        print(f"  Total: ${stats['total']:.2f}")
        if stats['examples']:
            for ex in stats['examples']:
                print(f"    ${ex['amount']:.2f} on {ex['date']}")

print("\n" + "=" * 80)
print(f"SHIPPING-RELATED ADJUSTMENTS: {len(shipping_related)}")
print("=" * 80)

if shipping_related:
    print("\n📦 Found shipping-related charges:")
    for item in shipping_related[:20]:
        print(f"  {item['date']}: {item['type']} = ${item['amount']:.2f}")
    
    if len(shipping_related) > 20:
        print(f"\n  ... and {len(shipping_related) - 20} more")
    
    # Group by type
    print("\n📊 Breakdown by type:")
    type_counts = {}
    for item in shipping_related:
        t = item['type']
        if t not in type_counts:
            type_counts[t] = {'count': 0, 'total': 0}
        type_counts[t]['count'] += 1
        type_counts[t]['total'] += abs(item['amount'])
    
    for t, stats in sorted(type_counts.items()):
        print(f"  {t}: {stats['count']} charges, ${stats['total']:.2f} total")
else:
    print("\n⚠️ No shipping-related adjustments found")
