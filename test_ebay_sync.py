"""
Test eBay returns sync to database
"""
from ebay_manager import EbayManager

em = EbayManager()
synced_count = em.sync_returns_to_db(days_back=90)

print(f"\n✅ Sync complete: {synced_count} returns synced")

# Check the database
import sqlite3
conn = sqlite3.connect('sold.db')
cur = conn.cursor()

cur.execute("SELECT COUNT(*) FROM returns WHERE store='ebay'")
ebay_returns = cur.fetchone()[0]
print(f"📊 Total eBay returns in database: {ebay_returns}")

# Show the returns
cur.execute("""
    SELECT order_id, title, refund_amount, return_date, return_reason
    FROM returns 
    WHERE store='ebay'
    ORDER BY return_date DESC
    LIMIT 10
""")

print("\n📋 Recent eBay Returns:")
print("-" * 80)
for row in cur.fetchall():
    order_id, title, refund, date, reason = row
    print(f"  {order_id}: ${refund:.2f} - {title[:40]}")
    print(f"    Date: {date} | Reason: {reason}")
    print()

conn.close()
