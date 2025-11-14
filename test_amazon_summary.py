#!/usr/bin/env python3
"""
Test Amazon financial summary calculation
"""

from amazon_manager import AmazonManager

amazon = AmazonManager()

print("=" * 80)
print("AMAZON FINANCIAL SUMMARY TEST")
print("=" * 80)

print("\n🔄 Fetching 30-day financial data...")
financial_data = amazon.get_financial_events(days_back=30)

# Calculate totals
total_seller_fees = 0
total_shipping_costs = 0
total_taxes = 0
orders_with_fees = 0
orders_with_shipping = 0

for order_id, data in financial_data.items():
    if data['seller_fee'] > 0:
        total_seller_fees += data['seller_fee']
        orders_with_fees += 1
    
    if data['shipping_cost'] > 0:
        total_shipping_costs += data['shipping_cost']
        orders_with_shipping += 1
    
    total_taxes += data['taxes']

print("\n" + "=" * 80)
print("📊 FINANCIAL SUMMARY (30 days)")
print("=" * 80)
print(f"Total Orders: {len(financial_data)}")
print(f"  - Orders with seller fees: {orders_with_fees}")
print(f"  - Orders with shipping costs: {orders_with_shipping}")
print()
print(f"💰 Seller Fees: ${total_seller_fees:,.2f}")
print(f"📦 Shipping Costs: ${total_shipping_costs:,.2f}")
print(f"📄 Taxes: ${total_taxes:,.2f}")
print("─" * 80)
print(f"🔸 Total Amazon Costs: ${total_seller_fees + total_shipping_costs + total_taxes:,.2f}")
print("=" * 80)
