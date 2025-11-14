"""
Analyze unmatched eBay returns
"""
from ebay_manager import EbayManager
import json

em = EbayManager()
returns = em.get_returns(days_back=90)

print(f"Total returns found: {len(returns)}\n")
print("=" * 80)

import sqlite3
conn = sqlite3.connect('sold.db')
cur = conn.cursor()

matched = []
unmatched = []

for ret in returns:
    order_id = ret['order_id']
    
    # Check if order exists
    cur.execute('SELECT id FROM orders WHERE order_id = ? AND store = "ebay"', (order_id,))
    if cur.fetchone():
        matched.append(ret)
    else:
        unmatched.append(ret)

print(f"✅ Matched: {len(matched)}")
print(f"⚠️  Unmatched: {len(unmatched)}")

if unmatched:
    print("\n" + "=" * 80)
    print("UNMATCHED RETURNS (Full Details):")
    print("=" * 80)
    
    for ret in unmatched:
        print(f"\n📦 Order ID: {ret['order_id']}")
        print(f"   Return Date: {ret['return_date']}")
        print(f"   Refund Amount: ${ret['refund_amount']:.2f}")
        print(f"   Title: {ret['title'][:60]}")
        print(f"   Item ID/SKU: {ret['item_id']}")
        print(f"   Quantity: {ret['quantity']}")
        print(f"   Reason: {ret['return_reason']}")
        print(f"   Status: {ret['status']}")

conn.close()

print("\n" + "=" * 80)
print("COMPLETE DATA:")
print("=" * 80)
print(json.dumps(unmatched, indent=2))
