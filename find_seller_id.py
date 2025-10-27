"""
Find Amazon Seller ID and test Business Catalog API
"""

import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

from amazon_manager import AmazonManager

def find_seller_id():
    """Try to find the seller ID from various sources"""
    
    print("Finding Amazon Seller ID...")
    print("=" * 80)
    
    manager = AmazonManager()
    
    # Method 1: From Orders API
    print("\nMethod 1: From recent orders")
    try:
        from sp_api.api import Orders
        from datetime import datetime, timedelta
        
        orders_api = Orders(credentials=manager.credentials, marketplace=manager.marketplace)
        created_after = (datetime.utcnow() - timedelta(days=30)).isoformat()
        
        response = orders_api.get_orders(CreatedAfter=created_after, MaxResultsPerPage=1)
        
        if not response.errors and response.payload.get('Orders'):
            order = response.payload['Orders'][0]
            seller_id = order.get('SellerOrderId', '').split('-')[0] if order.get('SellerOrderId') else None
            
            # The marketplace participant ID is often in the order
            if 'MarketplaceId' in response.payload:
                print(f"  Marketplace ID: {response.payload.get('MarketplaceId')}")
            
            # Try to extract from AmazonOrderId pattern
            amazon_order_id = order.get('AmazonOrderId', '')
            print(f"  Sample Order ID: {amazon_order_id}")
            
            # Seller ID is usually a 13-14 character alphanumeric string starting with A
            # It's typically found in seller central URL or API responses
            
    except Exception as e:
        print(f"  ❌ Error: {e}")
    
    # Method 2: From Marketplace Participations
    print("\nMethod 2: From Marketplace Participations API")
    try:
        from sp_api.api import Sellers
        
        sellers_api = Sellers(credentials=manager.credentials)
        response = sellers_api.get_marketplace_participations()
        
        if not response.errors and response.payload:
            print("  ✅ Found seller information!")
            
            # The response contains seller ID
            payload = response.payload
            print(f"  Payload keys: {list(payload.keys())}")
            
            if 'payload' in payload:
                for participation in payload.get('payload', []):
                    if 'marketplace' in participation:
                        print(f"  Marketplace: {participation['marketplace']}")
                    if 'seller' in participation:
                        seller_info = participation['seller']
                        seller_id = seller_info.get('sellerId', '')
                        print(f"  🎯 Seller ID: {seller_id}")
                        return seller_id
            
    except Exception as e:
        print(f"  ❌ Error: {e}")
    
    # Method 3: Check Seller Central URL pattern
    print("\nMethod 3: Manual lookup")
    print("  Your Seller ID can be found in Seller Central:")
    print("  1. Go to Settings (gear icon) → Account Info")
    print("  2. Look for 'Merchant Token' or 'Seller ID'")
    print("  3. It's a string like: A2ABCDEFGHIJK")
    
    return None

def test_with_seller_id(seller_id):
    """Test Catalog API with seller ID"""
    
    print(f"\nTesting APIs with Seller ID: {seller_id}")
    print("=" * 80)
    
    manager = AmazonManager()
    
    # Update credentials file with seller_id
    import json
    with open('amazon_credentials.json', 'r') as f:
        creds = json.load(f)
    
    creds['seller_id'] = seller_id
    
    with open('amazon_credentials.json', 'w') as f:
        json.dump(creds, f, indent=2)
    
    print("✅ Updated amazon_credentials.json with seller_id")
    
    # Now test Catalog API
    print("\nTesting Catalog Items API...")
    try:
        from sp_api.api import CatalogItems
        catalog_api = CatalogItems(credentials=manager.credentials, marketplace=manager.marketplace)
        
        # Get a test ASIN from database
        import sqlite3
        conn = sqlite3.connect('amazonStore.db')
        cur = conn.cursor()
        cur.execute('SELECT ASIN FROM ITEMS LIMIT 1')
        test_asin = cur.fetchone()[0]
        conn.close()
        
        print(f"  Testing with ASIN: {test_asin}")
        
        response = catalog_api.get_catalog_item(test_asin, includedData=['identifiers', 'attributes'])
        
        if response.errors:
            print(f"  ❌ Error: {response.errors}")
        else:
            print(f"  ✅ SUCCESS! Catalog API is working!")
            print(f"  Response: {response.payload}")
            
            # Check for UPC
            identifiers = response.payload.get('identifiers', [])
            for identifier in identifiers:
                if identifier.get('identifierType') in ['UPC', 'EAN']:
                    print(f"  🎯 Found {identifier['identifierType']}: {identifier.get('identifiers', [])}")
        
    except Exception as e:
        print(f"  ❌ Exception: {e}")

if __name__ == "__main__":
    seller_id = find_seller_id()
    
    if not seller_id:
        print("\n" + "=" * 80)
        seller_id = input("\nEnter your Seller ID manually (or press Enter to skip): ").strip()
    
    if seller_id:
        test_with_seller_id(seller_id)
    else:
        print("\n⚠️ Skipping API test without Seller ID")
