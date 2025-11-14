"""
Test eBay fees sync
"""
from ebay_manager import EbayManager
import sqlite3

# Test fee fetching
em = EbayManager()
fees_count = em.sync_fees_to_db(days_back=90)

print(f"\n✅ Sync complete: {fees_count} orders updated with fees")

# Check the database
conn = sqlite3.connect('sold.db')
cur = conn.cursor()

# Check eBay orders with fees
cur.execute("""
    SELECT COUNT(*) as count,
           SUM(seller_fee) as total_fees,
           AVG(seller_fee) as avg_fee
    FROM orders
    WHERE store = 'ebay' AND seller_fee > 0
""")

row = cur.fetchone()
count, total_fees, avg_fee = row

print(f"\n📊 eBay Orders with Fees:")
print(f"  Orders with fees: {count}")
print(f"  Total fees: ${total_fees:.2f}")
print(f"  Average fee: ${avg_fee:.2f}")

# Show sample orders
print("\n📋 Sample eBay Orders with Fees:")
cur.execute("""
    SELECT order_id, title, price, seller_fee
    FROM orders
    WHERE store = 'ebay' AND seller_fee > 0
    ORDER BY paid_time DESC
    LIMIT 10
""")

for row in cur.fetchall():
    order_id, title, price, fee = row
    print(f"  {order_id}: ${price:.2f} | Fee: ${fee:.2f}")
    print(f"    {title[:50]}")

conn.close()

print("\n✅ eBay fees are now synced and will appear in financial analytics!")
