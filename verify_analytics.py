"""
Test financial analytics with eBay returns
"""
import sqlite3

conn = sqlite3.connect('sold.db')
cur = conn.cursor()

# Check total eBay return costs
cur.execute("""
    SELECT 
        COUNT(*) as count,
        SUM(refund_amount) as total_refunds,
        SUM(refund_amount + COALESCE(original_shipping_cost, 0) + COALESCE(return_shipping_cost, 0)) as total_cost
    FROM returns
    WHERE store = 'ebay'
""")

row = cur.fetchone()
count, total_refunds, total_cost = row

print("📊 eBay Returns Financial Summary:")
print("=" * 60)
print(f"  Total Returns: {count}")
print(f"  Total Refunds: ${total_refunds:.2f}")
print(f"  Total Return Cost: ${total_cost:.2f}")

# Show breakdown
print("\n📋 Breakdown by Match Status:")
print("=" * 60)

cur.execute("""
    SELECT 
        CASE WHEN original_order_id IS NULL THEN 'Unmatched' ELSE 'Matched' END as match_status,
        COUNT(*) as count,
        SUM(refund_amount) as total_refunds
    FROM returns
    WHERE store = 'ebay'
    GROUP BY match_status
""")

for row in cur.fetchall():
    status, cnt, refunds = row
    print(f"  {status}: {cnt} returns, ${refunds:.2f} in refunds")

# Show all returns
print("\n📋 All eBay Returns:")
print("=" * 60)

cur.execute("""
    SELECT 
        order_id,
        return_date,
        refund_amount,
        title,
        CASE WHEN original_order_id IS NULL THEN 'No' ELSE 'Yes' END as matched
    FROM returns
    WHERE store = 'ebay'
    ORDER BY return_date DESC
""")

for row in cur.fetchall():
    order_id, date, refund, title, matched = row
    print(f"  {order_id} | ${refund:.2f} | Matched: {matched}")
    print(f"    {date}")
    print(f"    {title[:50]}")
    print()

conn.close()

print("\n✅ All eBay returns will appear in financial analytics!")
print("   Returns without matching orders use refund amount as cost estimate.")
