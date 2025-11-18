"""
Test to verify marketplace sale calculation logic.
This demonstrates how marketplace vs regular sales should be calculated.
"""

# Test data
marketplace_sale = {
    'store': 'marketplace',
    'price': 10.00,  # Total price for 2 items
    'quantity': 2,
    'cost': None,
    'seller_fee': 0,
    'taxes': 0,
    'shipping_cost': 0
}

regular_sale = {
    'store': 'ebay',
    'price': 5.00,  # Per-unit price
    'quantity': 2,
    'cost': 2.00,  # Per-unit cost
    'seller_fee': 1.50,
    'taxes': 0.50,
    'shipping_cost': 3.00
}

def get_revenue(item):
    """For marketplace, price is total. For others, multiply by quantity."""
    return item['price'] if item['store'] == 'marketplace' else (item['price'] * item['quantity'])

def get_cost(item):
    """Marketplace has no cost. For others, multiply by quantity."""
    return 0 if item['store'] == 'marketplace' else ((item['cost'] or 0) * item['quantity'])

# Test marketplace sale
marketplace_revenue = get_revenue(marketplace_sale)
marketplace_cost = get_cost(marketplace_sale)
marketplace_profit = marketplace_revenue - marketplace_cost - marketplace_sale['seller_fee'] - marketplace_sale['taxes'] - marketplace_sale['shipping_cost']

print("Marketplace Sale (quantity=2, price=$10 total):")
print(f"  Revenue: ${marketplace_revenue:.2f}  (should be $10.00, not $20.00)")
print(f"  Cost: ${marketplace_cost:.2f}  (should be $0.00)")
print(f"  Profit: ${marketplace_profit:.2f}")
print()

# Test regular sale
regular_revenue = get_revenue(regular_sale)
regular_cost = get_cost(regular_sale)
regular_profit = regular_revenue - regular_cost - regular_sale['seller_fee'] - regular_sale['taxes'] - regular_sale['shipping_cost']

print("Regular Sale (quantity=2, price=$5 each):")
print(f"  Revenue: ${regular_revenue:.2f}  (should be $10.00 = $5 x 2)")
print(f"  Cost: ${regular_cost:.2f}  (should be $4.00 = $2 x 2)")
print(f"  Profit: ${regular_profit:.2f}  (should be $1.00 = $10 - $4 - $1.50 - $0.50 - $3)")
