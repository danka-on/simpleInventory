"""
Test Amazon UPC Sync Function Directly
"""
import sys
import os

# Add current directory to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

print("=== Testing Amazon UPC Sync ===\n")

# Test 1: Check if Amazon is available
try:
    from amazon_manager import AmazonManager
    print("✅ AmazonManager imported successfully")
except Exception as e:
    print(f"❌ Failed to import AmazonManager: {e}")
    sys.exit(1)

# Test 2: Initialize AmazonManager
try:
    amazon = AmazonManager()
    print("✅ AmazonManager initialized")
except Exception as e:
    print(f"❌ Failed to initialize AmazonManager: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)

# Test 3: Check database
try:
    import sqlite3
    conn = sqlite3.connect('amazonStore.db')
    cur = conn.cursor()
    
    cur.execute('SELECT COUNT(*) FROM ITEMS WHERE UPC = ASIN OR UPC IS NULL')
    count = cur.fetchone()[0]
    print(f"✅ Found {count} items without UPCs in database")
    
    # Get a sample ASIN
    cur.execute('SELECT ASIN FROM ITEMS WHERE UPC = ASIN OR UPC IS NULL LIMIT 1')
    sample = cur.fetchone()
    if sample:
        sample_asin = sample[0]
        print(f"📦 Sample ASIN to test: {sample_asin}")
        
        # Test 4: Try to fetch catalog item
        try:
            print(f"\n🔍 Testing API call for {sample_asin}...")
            catalog_data = amazon.get_catalog_item(sample_asin)
            
            if catalog_data:
                print("✅ API call successful!")
                print(f"Response keys: {list(catalog_data.keys())}")
                
                # Try to extract UPC
                upc = None
                if 'attributes' in catalog_data:
                    attrs = catalog_data['attributes']
                    if 'externally_assigned_product_identifier' in attrs:
                        for identifier in attrs['externally_assigned_product_identifier']:
                            if identifier.get('type') in ['upc', 'ean']:
                                upc = identifier.get('value')
                                break
                
                if upc:
                    print(f"✅ Found UPC: {upc}")
                else:
                    print("⚠️ No UPC found in catalog data")
            else:
                print("❌ API call returned None")
                
        except Exception as e:
            print(f"❌ API call failed: {e}")
            import traceback
            traceback.print_exc()
    
    conn.close()
    
except Exception as e:
    print(f"❌ Database error: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)

print("\n✅ All tests completed")
