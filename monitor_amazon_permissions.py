"""
Continuously test Catalog API access until permissions activate
"""

import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

from amazon_manager import AmazonManager
import sqlite3
import time
from datetime import datetime

def test_catalog_access():
    """Test if Catalog API is accessible"""
    
    manager = AmazonManager()
    
    # Get a test ASIN
    conn = sqlite3.connect('amazonStore.db')
    cur = conn.cursor()
    cur.execute('SELECT ASIN, SKU FROM ITEMS LIMIT 1')
    test_asin, test_sku = cur.fetchone()
    conn.close()
    
    try:
        from sp_api.api import CatalogItems
        catalog_api = CatalogItems(credentials=manager.credentials, marketplace=manager.marketplace)
        
        response = catalog_api.get_catalog_item(test_asin, includedData=['identifiers'])
        
        if response.errors:
            return False, response.errors[0]['code']
        else:
            return True, response.payload
            
    except Exception as e:
        error_msg = str(e)
        if 'Unauthorized' in error_msg:
            return False, 'Unauthorized'
        return False, str(e)[:50]

def monitor_permissions(max_attempts=12, wait_seconds=600):
    """Monitor API access, testing every 10 minutes"""
    
    print("Monitoring Amazon Catalog API Permissions...")
    print("=" * 80)
    print("This will test the API every 10 minutes until access is granted")
    print("or 12 attempts have been made (~2 hours)")
    print("\nPress Ctrl+C to stop monitoring\n")
    print("=" * 80)
    
    for attempt in range(1, max_attempts + 1):
        timestamp = datetime.now().strftime("%H:%M:%S")
        print(f"\n[{timestamp}] Attempt {attempt}/{max_attempts}:")
        
        success, result = test_catalog_access()
        
        if success:
            print("✅ SUCCESS! Catalog API is now accessible!")
            print("\nSample response:")
            print(result)
            
            print("\n" + "=" * 80)
            print("🎉 PERMISSIONS ARE ACTIVE!")
            print("=" * 80)
            print("You can now fetch UPC codes. Run:")
            print("  python fetch_amazon_upcs.py")
            return True
        else:
            print(f"❌ Still not accessible: {result}")
            
            if attempt < max_attempts:
                print(f"⏳ Waiting 10 minutes before next attempt...")
                print(f"   Next test at: {(datetime.now() + __import__('datetime').timedelta(seconds=wait_seconds)).strftime('%H:%M:%S')}")
                try:
                    time.sleep(wait_seconds)
                except KeyboardInterrupt:
                    print("\n\n⚠️ Monitoring stopped by user")
                    return False
    
    print("\n" + "=" * 80)
    print("⚠️ PERMISSIONS NOT ACTIVATED YET")
    print("=" * 80)
    print("Amazon permissions can sometimes take up to an hour to activate.")
    print("\nOptions:")
    print("1. Wait longer and run this script again")
    print("2. Check that you regenerated the refresh token AFTER enabling permissions")
    print("3. Verify the permissions are actually enabled in your Amazon app settings")
    print("4. Contact Amazon Seller Support if it's been more than 24 hours")
    return False

if __name__ == "__main__":
    print("\nTesting immediately first...")
    success, result = test_catalog_access()
    
    if success:
        print("✅ Catalog API is already working!")
        print(f"\nResponse: {result}")
        print("\nYou can now run: python fetch_amazon_upcs.py")
    else:
        print(f"❌ Not yet accessible: {result}")
        print("\nStarting monitoring...\n")
        monitor_permissions()
