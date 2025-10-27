"""
Check what API permissions are actually working with current credentials
"""

import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

from amazon_manager import AmazonManager

def test_all_available_apis():
    """Test all possible Amazon SP-API endpoints to see what works"""
    
    print("Testing Available Amazon SP-API Permissions")
    print("=" * 80)
    print("This will test various API endpoints to see what your app can access\n")
    
    manager = AmazonManager()
    results = {}
    
    # Test 1: Orders API
    print("1. Orders API (getOrders)")
    try:
        from sp_api.api import Orders
        api = Orders(credentials=manager.credentials, marketplace=manager.marketplace)
        from datetime import datetime, timedelta
        created_after = (datetime.utcnow() - timedelta(days=7)).isoformat()
        response = api.get_orders(CreatedAfter=created_after, MaxResultsPerPage=1)
        if response.errors:
            results['Orders'] = f"❌ Error: {response.errors[0]['code']}"
        else:
            results['Orders'] = "✅ ACCESS GRANTED"
    except Exception as e:
        results['Orders'] = f"❌ {str(e)[:50]}"
    print(f"   {results['Orders']}\n")
    
    # Test 2: Reports API
    print("2. Reports API (getReports)")
    try:
        from sp_api.api import Reports
        api = Reports(credentials=manager.credentials, marketplace=manager.marketplace)
        response = api.get_reports(pageSize=1)
        if response.errors:
            results['Reports'] = f"❌ Error: {response.errors[0]['code']}"
        else:
            results['Reports'] = "✅ ACCESS GRANTED"
    except Exception as e:
        results['Reports'] = f"❌ {str(e)[:50]}"
    print(f"   {results['Reports']}\n")
    
    # Test 3: Catalog Items API 2022-04-01
    print("3. Catalog Items API 2022-04-01 (getCatalogItem)")
    try:
        from sp_api.api import CatalogItems
        api = CatalogItems(credentials=manager.credentials, marketplace=manager.marketplace)
        response = api.get_catalog_item("B0D951L5QB")
        if response.errors:
            results['CatalogItems_2022'] = f"❌ Error: {response.errors[0]['code']}"
        else:
            results['CatalogItems_2022'] = "✅ ACCESS GRANTED"
    except Exception as e:
        results['CatalogItems_2022'] = f"❌ {str(e)[:50]}"
    print(f"   {results['CatalogItems_2022']}\n")
    
    # Test 4: Catalog Items API 2020-12-01 (older version)
    print("4. Catalog Items API 2020-12-01 (listCatalogItems - older version)")
    try:
        from sp_api.api import CatalogItems
        api = CatalogItems(credentials=manager.credentials, marketplace=manager.marketplace)
        # Try the older version method
        response = api.list_catalog_items(MarketplaceId=manager.marketplace.marketplace_id, Query="test")
        if response.errors:
            results['CatalogItems_2020'] = f"❌ Error: {response.errors[0]['code']}"
        else:
            results['CatalogItems_2020'] = "✅ ACCESS GRANTED"
    except Exception as e:
        results['CatalogItems_2020'] = f"❌ {str(e)[:50]}"
    print(f"   {results['CatalogItems_2020']}\n")
    
    # Test 5: Listings Items API
    print("5. Listings Items API 2021-08-01 (putListingsItem)")
    try:
        from sp_api.api import ListingsItems
        api = ListingsItems(credentials=manager.credentials, marketplace=manager.marketplace)
        # Just check if we can initialize (actual call would require seller_id)
        results['ListingsItems'] = "⚠️ API available but needs seller_id"
    except Exception as e:
        results['ListingsItems'] = f"❌ {str(e)[:50]}"
    print(f"   {results['ListingsItems']}\n")
    
    # Test 6: Feeds API
    print("6. Feeds API (getFeeds)")
    try:
        from sp_api.api import Feeds
        api = Feeds(credentials=manager.credentials, marketplace=manager.marketplace)
        response = api.get_feeds(pageSize=1)
        if response.errors:
            results['Feeds'] = f"❌ Error: {response.errors[0]['code']}"
        else:
            results['Feeds'] = "✅ ACCESS GRANTED"
    except Exception as e:
        results['Feeds'] = f"❌ {str(e)[:50]}"
    print(f"   {results['Feeds']}\n")
    
    # Test 7: Finances API
    print("7. Finances API (listFinancialEvents)")
    try:
        from sp_api.api import Finances
        api = Finances(credentials=manager.credentials, marketplace=manager.marketplace)
        response = api.list_financial_events(MaxResultsPerPage=1)
        if response.errors:
            results['Finances'] = f"❌ Error: {response.errors[0]['code']}"
        else:
            results['Finances'] = "✅ ACCESS GRANTED"
    except Exception as e:
        results['Finances'] = f"❌ {str(e)[:50]}"
    print(f"   {results['Finances']}\n")
    
    # Test 8: FBA Inventory API
    print("8. FBA Inventory API (getInventorySummaries)")
    try:
        from sp_api.api import FbaInventory
        api = FbaInventory(credentials=manager.credentials, marketplace=manager.marketplace)
        response = api.get_inventory_summaries(granularityType="Marketplace", 
                                               granularityId=manager.marketplace.marketplace_id,
                                               marketplaceIds=[manager.marketplace.marketplace_id])
        if response.errors:
            results['FbaInventory'] = f"❌ Error: {response.errors[0]['code']}"
        else:
            results['FbaInventory'] = "✅ ACCESS GRANTED"
    except Exception as e:
        results['FbaInventory'] = f"❌ {str(e)[:50]}"
    print(f"   {results['FbaInventory']}\n")
    
    # Summary
    print("\n" + "=" * 80)
    print("SUMMARY OF AVAILABLE APIS:")
    print("=" * 80)
    
    granted = [k for k, v in results.items() if "✅" in v]
    denied = [k for k, v in results.items() if "❌" in v and "Unauthorized" in v]
    other = [k for k, v in results.items() if k not in granted and k not in denied]
    
    print(f"\n✅ Access Granted ({len(granted)}):")
    for api in granted:
        print(f"   • {api}")
    
    print(f"\n❌ Access Denied - Unauthorized ({len(denied)}):")
    for api in denied:
        print(f"   • {api}")
    
    if other:
        print(f"\n⚠️ Other Issues ({len(other)}):")
        for api in other:
            print(f"   • {api}: {results[api]}")
    
    print("\n" + "=" * 80)
    print("PERMISSION NAMES IN AMAZON DEVELOPER CONSOLE:")
    print("=" * 80)
    print("""
The APIs you're looking for might be named:

For Product/Catalog Data (UPC lookup):
  • "Product Listing" or "Item Data"
  • "Catalog Items API" or "Catalog API"
  • "Product Type Definitions API"
  • "A+ Content API"
  
Other common names:
  • "Sales Channel Orders" → Orders API
  • "Inventory and Order Tracking" → FBA Inventory
  • "Reports" → Reports API
  • "Financial Events" → Finances API
  • "Feeds" → Feeds API
  
In your Amazon Developer Console (Seller Central):
1. Go to: Apps & Services → Develop Apps
2. Click "View" or "Edit" on your app
3. Look for sections like:
   - "Roles" or "Data Access"
   - "API Access" or "Permissions"
   - "Authorized Operations"

If you see "Amazon Business" options, those are B2B-specific APIs.
The standard catalog is usually just "Catalog Items" or "Product Listing".
""")

if __name__ == "__main__":
    test_all_available_apis()
