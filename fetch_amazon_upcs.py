"""
Fetch UPC codes from Amazon Catalog API using ASINs
The inventory report doesn't contain UPCs - we need to query the catalog separately
"""

import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

from sp_api.api import CatalogItems
from amazon_manager import AmazonManager
import sqlite3
import time

def fetch_upcs_from_catalog():
    """Fetch UPC/EAN codes from Amazon Catalog API for all items"""
    
    print("Fetching UPCs from Amazon Catalog API...")
    print("=" * 80)
    
    manager = AmazonManager()
    catalog_api = CatalogItems(credentials=manager.credentials, marketplace=manager.marketplace)
    
    # Get all ASINs from amazonStore.db
    conn = sqlite3.connect('amazonStore.db')
    cur = conn.cursor()
    
    cur.execute('SELECT ID, ASIN, SKU, TITLE FROM ITEMS WHERE ASIN IS NOT NULL')
    items = cur.fetchall()
    
    print(f"Found {len(items)} items in amazonStore.db")
    print("Fetching UPC codes from Amazon Catalog API...")
    print("(This may take a while - API rate limit is ~2 requests/second)\n")
    
    upc_found_count = 0
    upc_not_found_count = 0
    error_count = 0
    
    # Track updates
    updates = []
    
    for i, (item_id, asin, sku, title) in enumerate(items, 1):
        if i % 10 == 0:
            print(f"Progress: {i}/{len(items)} items processed...")
        
        try:
            # Query catalog for this ASIN
            response = catalog_api.get_catalog_item(asin, includedData=['identifiers', 'attributes'])
            
            if response.errors:
                print(f"  ❌ Error for {asin} ({sku}): {response.errors}")
                error_count += 1
                continue
            
            # Extract UPC/EAN from identifiers and attributes
            item_data = response.payload
            
            upc = None
            ean = None
            
            # Check identifiers section first
            identifiers = item_data.get('identifiers', [])
            for identifier in identifiers:
                id_type = identifier.get('identifierType', '')
                values = identifier.get('identifiers', [])
                
                if id_type == 'UPC' and values:
                    upc = values[0]['identifier'] if isinstance(values[0], dict) else values[0]
                elif id_type == 'EAN' and values:
                    ean = values[0]['identifier'] if isinstance(values[0], dict) else values[0]
            
            # Also check attributes section for externally_assigned_product_identifier
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
            
            # Prefer UPC, fall back to EAN
            barcode = upc or ean
            
            if barcode:
                updates.append((barcode, asin))
                upc_found_count += 1
                print(f"  ✅ {asin} ({sku[:20]}): {barcode}")
            else:
                upc_not_found_count += 1
                if i <= 10:  # Show first 10 without UPC
                    print(f"  ⚠️  {asin} ({sku[:20]}): No UPC/EAN found")
            
            # Rate limiting: Amazon allows ~2 requests per second
            time.sleep(0.5)
            
        except Exception as e:
            print(f"  ❌ Exception for {asin} ({sku}): {e}")
            error_count += 1
            continue
    
    # Update database with UPCs
    if updates:
        print(f"\n📊 Updating database with {len(updates)} UPC codes...")
        for upc, asin in updates:
            cur.execute('UPDATE ITEMS SET UPC = ? WHERE ASIN = ?', (upc, asin))
        conn.commit()
        print("✅ Database updated!")
    
    conn.close()
    
    # Summary
    print("\n" + "=" * 80)
    print("SUMMARY:")
    print("=" * 80)
    print(f"Total items processed: {len(items)}")
    print(f"UPCs found: {upc_found_count} ({upc_found_count/len(items)*100:.1f}%)")
    print(f"No UPC/EAN: {upc_not_found_count} ({upc_not_found_count/len(items)*100:.1f}%)")
    print(f"Errors: {error_count}")
    
    if upc_found_count > 0:
        print(f"\n✅ Successfully retrieved {upc_found_count} UPC/EAN codes from Amazon Catalog API")
        print("   These have been saved to amazonStore.db in the UPC column")

if __name__ == "__main__":
    fetch_upcs_from_catalog()
