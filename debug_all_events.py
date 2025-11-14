"""Check ALL Financial Event types for shipping costs"""
from amazon_manager import AmazonManager
import json

amazon = AmazonManager()

print("="*80)
print("CHECKING ALL FINANCIAL EVENT TYPES")
print("="*80)

from sp_api.api import Finances
from datetime import datetime, timedelta

finances_api = Finances(credentials=amazon.credentials, marketplace=amazon.marketplace)
posted_after = (datetime.utcnow() - timedelta(days=7)).isoformat()

response = finances_api.list_financial_events(PostedAfter=posted_after, MaxResultsPerPage=100)
payload = response.payload
events = payload.get('FinancialEvents', {})

print("\n📊 Available Event Types:")
print("-"*80)

for key, value in events.items():
    if value:  # Only show non-empty lists
        count = len(value) if isinstance(value, list) else 1
        print(f"  ✅ {key}: {count} events")
    else:
        print(f"  ⚪ {key}: (empty)")

# Check ServiceFeeEventList specifically
service_fees = events.get('ServiceFeeEventList', [])
if service_fees:
    print(f"\n{'='*80}")
    print(f"SERVICE FEE EVENTS (Shipping Labels?)")
    print(f"{'='*80}")
    for i, event in enumerate(service_fees[:5], 1):
        print(f"\nEvent #{i}:")
        print(json.dumps(event, indent=2, default=str))

# Check AdjustmentEventList
adjustments = events.get('AdjustmentEventList', [])
if adjustments:
    print(f"\n{'='*80}")
    print(f"ADJUSTMENT EVENTS")
    print(f"{'='*80}")
    for i, event in enumerate(adjustments[:3], 1):
        print(f"\nEvent #{i}:")
        print(json.dumps(event, indent=2, default=str))

# Check any other relevant lists
for event_type in ['CouponPaymentEventList', 'DebtRecoveryEventList', 
                   'LoanServicingEventList', 'NetworkComminglingTransactionEventList']:
    event_list = events.get(event_type, [])
    if event_list:
        print(f"\n{'='*80}")
        print(f"{event_type}")
        print(f"{'='*80}")
        print(json.dumps(event_list[:2], indent=2, default=str))
