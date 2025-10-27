"""
Backfill shipping info for existing Amazon orders in sold.db
"""
import sqlite3
from amazon_manager import AmazonManager
import time

print("Backfilling Amazon order shipping info...")
print("=" * 80)

# Get all Amazon orders without shipping info
conn = sqlite3.connect('sold.db')
cur = conn.cursor()

cur.execute("""
    SELECT order_id 
    FROM orders 
    WHERE store = 'amazon' 
    AND (shipping_name IS NULL OR shipping_name = '')
""")
order_ids = [row[0] for row in cur.fetchall()]

print(f"Found {len(order_ids)} Amazon orders without shipping info\n")

amazon = AmazonManager()
updated = 0
not_found = 0
api_calls = 0

# We need to fetch the full order details to get shipping address
for order_id in order_ids:
    try:
        # Fetch order from Amazon API
        from sp_api.api import Orders
        orders_api = Orders(credentials=amazon.credentials, marketplace=amazon.marketplace)
        
        response = orders_api.get_order(order_id=order_id)
        api_calls += 1
        
        if response.payload:
            order = response.payload
            shipping_address = order.get('ShippingAddress', {})
            
            shipping_name = "Amazon Buyer"  # Amazon doesn't provide buyer name
            shipping_city = shipping_address.get('City', '')
            shipping_state = shipping_address.get('StateOrRegion', '')
            shipping_postal = shipping_address.get('PostalCode', '')
            shipping_country = shipping_address.get('CountryCode', '')
            
            # Update the order
            cur.execute('''
                UPDATE orders 
                SET shipping_name = ?, shipping_city = ?, shipping_state = ?, 
                    shipping_postal_code = ?, shipping_country = ?
                WHERE order_id = ?
            ''', (shipping_name, shipping_city, shipping_state, shipping_postal, shipping_country, order_id))
            conn.commit()
            
            updated += 1
            print(f"OK {order_id} | {shipping_city}, {shipping_state} {shipping_postal}")
        else:
            not_found += 1
            print(f"Not found {order_id}")
        
        # Rate limiting: Orders API allows 0.0055 req/sec = ~1 req per 200 seconds
        # To be safe, wait 1 second between requests
        time.sleep(1)
        
    except Exception as e:
        print(f"Error {order_id}: {e}")
        not_found += 1
        # If quota exceeded, stop
        if 'QuotaExceeded' in str(e):
            print("\n⚠️ API quota exceeded. Stopping backfill.")
            print("The remaining orders will be updated on the next sync.")
            break

conn.close()

print("\n" + "=" * 80)
print(f"Results:")
print(f"  Updated: {updated}")
print(f"  Not found: {not_found}")
print(f"  Total API calls: {api_calls}")
print(f"\n  Remaining to backfill: {len(order_ids) - updated - not_found}")

print("\nComplete!")
