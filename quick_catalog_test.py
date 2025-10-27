"""
Quick test of Catalog API with new permissions
"""

import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

from amazon_manager import AmazonManager
import sqlite3

print("Testing Amazon Catalog API Access...")
print("=" * 80)

manager = AmazonManager()

# Get a test ASIN
conn = sqlite3.connect('amazonStore.db')
cur = conn.cursor()
cur.execute('SELECT ASIN, SKU, TITLE FROM ITEMS LIMIT 1')
test_asin, test_sku, test_title = cur.fetchone()
conn.close()

print(f"\nTest Item:")
print(f"  ASIN: {test_asin}")
print(f"  SKU: {test_sku}")
print(f"  Title: {test_title[:50]}")

print("\n" + "=" * 80)
print("Testing Catalog Items API...")
print("=" * 80)

try:
    from sp_api.api import CatalogItems
    catalog_api = CatalogItems(credentials=manager.credentials, marketplace=manager.marketplace)
    
    response = catalog_api.get_catalog_item(test_asin, includedData=['identifiers', 'attributes'])
    
    if response.errors:
        print(f"❌ Error: {response.errors}")
        print("\nStill unauthorized - the permission may need time to activate.")
        print("Amazon sometimes takes 5-15 minutes for new permissions to propagate.")
    else:
        print("✅ SUCCESS! Catalog API is working!")
        print(f"\nFull Response: {response.payload}")
        
        # Extract UPC
        identifiers = response.payload.get('identifiers', [])
        print(f"\nIdentifiers found: {len(identifiers)}")
        
        for identifier in identifiers:
            id_type = identifier.get('identifierType', '')
            values = identifier.get('identifiers', [])
            print(f"  {id_type}: {values}")
            
            if id_type in ['UPC', 'EAN'] and values:
                print(f"\n🎯 Found {id_type}: {values[0]}")
        
except Exception as e:
    print(f"❌ Exception: {e}")
    import traceback
    traceback.print_exc()
