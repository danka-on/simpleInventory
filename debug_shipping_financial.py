"""Debug shipping costs from Financial Events API"""
from amazon_manager import AmazonManager
import json

amazon = AmazonManager()

print("="*70)
print("Checking Financial Events for Shipping Data")
print("="*70)

financial_data = amazon.get_financial_events(days_back=7)

print(f"\n✅ Got financial data for {len(financial_data)} orders\n")

# Show detailed breakdown
for order_id, data in list(financial_data.items())[:5]:
    print(f"\n{'='*70}")
    print(f"Order: {order_id}")
    print(f"{'='*70}")
    print(f"  Seller Fee: ${data['seller_fee']:.2f}")
    print(f"  Shipping Cost: ${data['shipping_cost']:.2f}")
    print(f"  Taxes: ${data['taxes']:.2f}")
    print(f"  Total Fees: ${data['total_fees']:.2f}")

# Now let's get raw financial events to see the structure
print("\n\n" + "="*70)
print("RAW FINANCIAL EVENT STRUCTURE (First Order)")
print("="*70)

from sp_api.api import Finances
from datetime import datetime, timedelta

finances_api = Finances(credentials=amazon.credentials, marketplace=amazon.marketplace)
posted_after = (datetime.utcnow() - timedelta(days=7)).isoformat()

response = finances_api.list_financial_events(PostedAfter=posted_after, MaxResultsPerPage=2)
payload = response.payload
events = payload.get('FinancialEvents', {})
shipment_events = events.get('ShipmentEventList', [])

if shipment_events:
    # Show first complete event
    print(f"\nFound {len(shipment_events)} shipment events")
    print(f"\nFirst Event Full Structure:")
    print(json.dumps(shipment_events[0], indent=2, default=str))
else:
    print("No shipment events found")
