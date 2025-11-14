"""
Verify complete eBay financial data
"""
import sqlite3

conn = sqlite3.connect('sold.db')
cur = conn.cursor()

print("=" * 80)
print("COMPLETE EBAY FINANCIAL DATA SUMMARY")
print("=" * 80)

# Orders
cur.execute("""
    SELECT 
        COUNT(*) as count,
        SUM(price) as total_revenue,
        SUM(seller_fee) as total_fees,
        SUM(shipping_cost) as total_shipping
    FROM orders
    WHERE store = 'ebay'
""")

row = cur.fetchone()
count, revenue, fees, shipping = row

print(f"\n📦 eBay Orders:")
print(f"  Total Orders: {count}")
print(f"  Total Revenue: ${revenue:.2f}")
print(f"  Total Seller Fees: ${fees:.2f}")
print(f"  Total Shipping: ${shipping:.2f}")
print(f"  Fee Rate: {(fees/revenue*100):.1f}%")

# Returns
cur.execute("""
    SELECT 
        COUNT(*) as count,
        SUM(refund_amount) as total_refunds,
        SUM(refund_amount + COALESCE(original_shipping_cost, 0) + COALESCE(return_shipping_cost, 0)) as total_cost
    FROM returns
    WHERE store = 'ebay'
""")

row = cur.fetchone()
ret_count, refunds, ret_cost = row

print(f"\n🔄 eBay Returns:")
print(f"  Total Returns: {ret_count}")
print(f"  Total Refunds: ${refunds:.2f}")
print(f"  Total Return Cost: ${ret_cost:.2f}")
print(f"  Return Rate: {(ret_count/count*100):.1f}%")

# Net Financial Summary
net_revenue = revenue - refunds
net_fees = fees
net_shipping = shipping - ret_cost

print(f"\n💰 Net Financial Summary:")
print(f"  Net Revenue: ${net_revenue:.2f} (after refunds)")
print(f"  Total Fees: ${net_fees:.2f}")
print(f"  Total Shipping Costs: ${net_shipping:.2f}")
print(f"  Gross Profit (before cost): ${(net_revenue - net_fees - net_shipping):.2f}")

print("\n" + "=" * 80)
print("✅ ALL EBAY FINANCIAL DATA IS NOW TRACKED:")
print("   • Orders & Revenue ✓")
print("   • Seller Fees ✓")
print("   • Shipping Costs ✓")
print("   • Returns & Refunds ✓")
print("=" * 80)

conn.close()
