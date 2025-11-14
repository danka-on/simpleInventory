import sqlite3

conn = sqlite3.connect('sold.db')
cur = conn.cursor()

cur.execute("SELECT COUNT(*) FROM orders WHERE store='ebay'")
ebay_orders = cur.fetchone()[0]
print(f"eBay orders in sold.db: {ebay_orders}")

cur.execute("SELECT COUNT(*) FROM returns WHERE store='ebay'")
ebay_returns = cur.fetchone()[0]
print(f"eBay returns in returns table: {ebay_returns}")

cur.execute("SELECT COUNT(*) FROM orders WHERE store='amazon'")
amazon_orders = cur.fetchone()[0]
print(f"\nAmazon orders in sold.db: {amazon_orders}")

cur.execute("SELECT COUNT(*) FROM returns WHERE store='amazon'")
amazon_returns = cur.fetchone()[0]
print(f"Amazon returns in returns table: {amazon_returns}")

# Check if there are any orders with buyer-initiated cancellations or returns
cur.execute("SELECT COUNT(*) FROM orders WHERE store='ebay' AND (order_status LIKE '%cancel%' OR order_status LIKE '%return%')")
potential_returns = cur.fetchone()[0]
print(f"\nPotential eBay returns/cancellations (by status): {potential_returns}")

conn.close()
