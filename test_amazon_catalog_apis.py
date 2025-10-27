"""
Test different Amazon Catalog API options to see what's accessible
"""

import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

from amazon_manager import AmazonManager

def test_catalog_access():
    """Test different catalog API endpoints"""
    
    print("Testing Amazon Catalog API Access...")
    print("=" * 80)
    
    manager = AmazonManager()
    
    # Get a sample ASIN from database
    import sqlite3
    conn = sqlite3.connect('amazonStore.db')
    cur = conn.cursor()
    cur.execute('SELECT ASIN, SKU, TITLE FROM ITEMS LIMIT 1')
    item = cur.fetchone()
    conn.close()
    
    if not item:
        print("No items in database to test with")
        return
    
    test_asin, test_sku, test_title = item
    print(f"\nTest Item:")
    print(f"  ASIN: {test_asin}")
    print(f"  SKU: {test_sku}")
    print(f"  Title: {test_title[:50]}")
    
    # Test 1: Standard Catalog Items API
    print("\n" + "=" * 80)
    print("TEST 1: Standard Catalog Items API (getCatalogItem)")
    print("=" * 80)
    try:
        from sp_api.api import CatalogItems
        catalog_api = CatalogItems(credentials=manager.credentials, marketplace=manager.marketplace)
        
        response = catalog_api.get_catalog_item(test_asin, includedData=['identifiers', 'attributes'])
        
        if response.errors:
            print(f"❌ Error: {response.errors}")
        else:
            print("✅ Success! Access granted to Catalog Items API")
            print(f"Response: {response.payload}")
    except Exception as e:
        print(f"❌ Exception: {e}")
    
    # Test 2: Search Catalog Items
    print("\n" + "=" * 80)
    print("TEST 2: Search Catalog Items (searchCatalogItems)")
    print("=" * 80)
    try:
        from sp_api.api import CatalogItems
        catalog_api = CatalogItems(credentials=manager.credentials, marketplace=manager.marketplace)
        
        response = catalog_api.search_catalog_items(
            keywords=[test_title[:30]],
            includedData=['identifiers'],
            pageSize=1
        )
        
        if response.errors:
            print(f"❌ Error: {response.errors}")
        else:
            print("✅ Success! Access granted to Search Catalog Items")
            print(f"Found items: {len(response.payload.get('items', []))}")
            if response.payload.get('items'):
                item = response.payload['items'][0]
                print(f"First result: {item}")
    except Exception as e:
        print(f"❌ Exception: {e}")
    
    # Test 3: Listings Items API (alternative)
    print("\n" + "=" * 80)
    print("TEST 3: Listings Items API (getListingsItem)")
    print("=" * 80)
    try:
        from sp_api.api import ListingsItems
        listings_api = ListingsItems(credentials=manager.credentials, marketplace=manager.marketplace)
        
        response = listings_api.get_listings_item(
            sellerId=manager.creds.get('seller_id', 'YOUR_SELLER_ID'),
            sku=test_sku,
            marketplaceIds=[manager.marketplace.marketplace_id],
            includedData=['summaries', 'attributes', 'identifiers']
        )
        
        if response.errors:
            print(f"❌ Error: {response.errors}")
        else:
            print("✅ Success! Access granted to Listings Items API")
            print(f"Response: {response.payload}")
    except Exception as e:
        print(f"❌ Exception: {e}")
    
    # Test 4: Product Pricing API (sometimes has UPC in attributes)
    print("\n" + "=" * 80)
    print("TEST 4: Product Pricing API (getItemOffersBatch)")
    print("=" * 80)
    try:
        from sp_api.api import ProductPricing
        pricing_api = ProductPricing(credentials=manager.credentials, marketplace=manager.marketplace)
        
        response = pricing_api.get_competitive_pricing(
            marketplace_id=manager.marketplace.marketplace_id,
            asins=[test_asin]
        )
        
        if response.errors:
            print(f"❌ Error: {response.errors}")
        else:
            print("✅ Success! Access granted to Product Pricing API")
            print(f"Response keys: {response.payload.keys() if hasattr(response.payload, 'keys') else 'N/A'}")
    except Exception as e:
        print(f"❌ Exception: {e}")
    
    # Summary
    print("\n" + "=" * 80)
    print("SUMMARY & RECOMMENDATIONS:")
    print("=" * 80)
    print("""
If all APIs return Unauthorized:
- Your app needs additional API permissions enabled
- Go to: Seller Central → Apps & Services → Develop Apps
- Edit your app and request these permissions:
  • Catalog Items API 2022-04-01 (for UPC lookups)
  • Listings Items API 2021-08-01 (alternative method)

Alternative approaches:
1. Use Amazon's product-id field (currently has ASINs)
2. Manually add UPCs via a CSV import
3. Use external UPC lookup services (upcitemdb.com, barcodelookup.com)
4. Extract UPCs from your own inventory records if you have them
""")

if __name__ == "__main__":
    test_catalog_access()
