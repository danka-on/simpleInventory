"""
Test Listings Items API for UPC/product identifiers
This API might include product identifiers that the Catalog API has
"""

import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

from amazon_manager import AmazonManager
import sqlite3

print("Testing Amazon Listings Items API for Product Identifiers...")
print("=" * 80)

manager = AmazonManager()

# Get seller_id
seller_id = manager.creds.get('seller_id', '')
if not seller_id:
    print("❌ Seller ID not found in credentials")
    exit(1)

print(f"Seller ID: {seller_id}")

# Get test items
conn = sqlite3.connect('amazonStore.db')
cur = conn.cursor()
cur.execute('SELECT ASIN, SKU, TITLE FROM ITEMS LIMIT 3')
test_items = cur.fetchall()
conn.close()

print(f"\nTesting with {len(test_items)} items...")
print("=" * 80)

try:
    from sp_api.api import ListingsItems
    listings_api = ListingsItems(credentials=manager.credentials, marketplace=manager.marketplace)
    
    for i, (asin, sku, title) in enumerate(test_items, 1):
        print(f"\n{i}. Testing SKU: {sku}")
        print(f"   ASIN: {asin}")
        print(f"   Title: {title[:50]}")
        
        try:
            # Get listing item details
            response = listings_api.get_listings_item(
                sellerId=seller_id,
                sku=sku,
                marketplaceIds=[manager.marketplace.marketplace_id],
                includedData=['summaries', 'attributes', 'identifiers']
            )
            
            if response.errors:
                print(f"   ❌ Error: {response.errors[0]['code']} - {response.errors[0]['message']}")
            else:
                print(f"   ✅ Success! Retrieved listing data")
                
                payload = response.payload
                print(f"   Response keys: {list(payload.keys())}")
                
                # Check for identifiers
                if 'identifiers' in payload:
                    identifiers = payload['identifiers']
                    print(f"   Identifiers: {identifiers}")
                
                # Check summaries
                if 'summaries' in payload:
                    summaries = payload['summaries']
                    for summary in summaries:
                        print(f"   Summary keys: {list(summary.keys())}")
                        
                        # Look for product identifiers
                        if 'itemIdentifier' in summary:
                            print(f"   Item Identifier: {summary['itemIdentifier']}")
                        if 'productId' in summary:
                            print(f"   Product ID: {summary['productId']}")
                        if 'upc' in summary:
                            print(f"   🎯 UPC: {summary['upc']}")
                
                # Check attributes
                if 'attributes' in payload:
                    attributes = payload['attributes']
                    print(f"   Attributes keys: {list(attributes.keys())}")
                    
                    # Look for external_product_id or similar
                    for key in attributes.keys():
                        if 'product' in key.lower() or 'upc' in key.lower() or 'ean' in key.lower():
                            print(f"   {key}: {attributes[key]}")
                
                # Print full response for first item
                if i == 1:
                    print(f"\n   Full Response for debugging:")
                    print(f"   {payload}")
                
        except Exception as e:
            print(f"   ❌ Exception: {e}")
            if i == 1:
                import traceback
                traceback.print_exc()
        
        print()
    
    print("=" * 80)
    print("SUMMARY:")
    print("=" * 80)
    print("""
If Listings Items API works but doesn't have UPCs:
- The UPC data might not be stored in your Amazon listings
- Amazon may only have ASINs for these products
- You might need to add UPCs manually to your listings in Seller Central

If API is Unauthorized:
- The "Product Listing" permission might not grant read access
- You may need a different permission like "Inventory and Order Tracking"
""")
    
except Exception as e:
    print(f"❌ Failed to initialize API: {e}")
    import traceback
    traceback.print_exc()
