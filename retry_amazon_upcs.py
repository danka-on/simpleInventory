"""
Retry fetching UPCs for items that don't have them yet
(useful after rate limit errors)
"""

import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

from sp_api.api import CatalogItems
from amazon_manager import AmazonManager
import sqlite3
import time

def retry_missing_upcs():
    """Retry fetching UPCs for items without them"""
    
    print("Checking for items without UPC codes...")
    print("=" * 80)
    
    manager = AmazonManager()
    catalog_api = CatalogItems(credentials=manager.credentials, marketplace=manager.marketplace)
    
    # Find items without UPCs (where UPC equals ASIN)
    conn = sqlite3.connect('amazonStore.db')
    cur = conn.cursor()
    
    cur.execute('''
        SELECT ID, ASIN, SKU, TITLE 
        FROM ITEMS 
        WHERE UPC = ASIN OR UPC IS NULL OR UPC = ''
    ''')
    
    items = cur.fetchall()
    
    if not items:
        print("✅ All items already have UPC codes!")
        conn.close()
        return
    
    print(f"Found {len(items)} items without UPC codes")
    print("Retrying...\n")
    
    updates = []
    success_count = 0
    error_count = 0
    
    for i, (item_id, asin, sku, title) in enumerate(items, 1):
        print(f"{i}. {asin} ({sku})")
        
        try:
            response = catalog_api.get_catalog_item(asin, includedData=['identifiers', 'attributes'])
            
            if response.errors:
                print(f"   ❌ Error: {response.errors[0]['code']}")
                error_count += 1
                continue
            
            # Extract UPC
            item_data = response.payload
            upc = None
            ean = None
            
            # Check identifiers
            identifiers = item_data.get('identifiers', [])
            for identifier in identifiers:
                id_type = identifier.get('identifierType', '')
                values = identifier.get('identifiers', [])
                
                if id_type == 'UPC' and values:
                    upc = values[0]['identifier'] if isinstance(values[0], dict) else values[0]
                elif id_type == 'EAN' and values:
                    ean = values[0]['identifier'] if isinstance(values[0], dict) else values[0]
            
            # Check attributes
            if not upc and not ean:
                attributes = item_data.get('attributes', {})
                external_ids = attributes.get('externally_assigned_product_identifier', [])
                
                for ext_id in external_ids:
                    if isinstance(ext_id, dict):
                        id_type = ext_id.get('type', '').lower()
                        value = ext_id.get('value', '')
                        
                        if id_type == 'upc' and value:
                            upc = value
                        elif id_type == 'ean' and value:
                            ean = value
            
            barcode = upc or ean
            
            if barcode:
                updates.append((barcode, asin))
                success_count += 1
                print(f"   ✅ Found: {barcode}")
            else:
                print(f"   ⚠️  No UPC/EAN in catalog")
            
            # Rate limit: 2 requests/second to be safe
            time.sleep(0.5)
            
        except Exception as e:
            print(f"   ❌ Exception: {str(e)[:50]}")
            error_count += 1
    
    # Update database
    if updates:
        print(f"\n📊 Updating {len(updates)} items...")
        for upc, asin in updates:
            cur.execute('UPDATE ITEMS SET UPC = ? WHERE ASIN = ?', (upc, asin))
        conn.commit()
        print("✅ Database updated!")
    
    conn.close()
    
    # Summary
    print("\n" + "=" * 80)
    print("SUMMARY:")
    print("=" * 80)
    print(f"Items attempted: {len(items)}")
    print(f"UPCs found: {success_count}")
    print(f"Errors: {error_count}")

if __name__ == "__main__":
    print("This script will retry fetching UPCs for items that don't have them.")
    print("Amazon's rate limit typically resets within 10-15 minutes.\n")
    
    retry_missing_upcs()
