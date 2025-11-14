"""
Test eBay API endpoints for returns/cancellations
"""
import requests
from token_manager import get_access_token
from datetime import datetime, timedelta

def test_ebay_endpoint(endpoint, params=None):
    """Test an eBay API endpoint"""
    token = get_access_token()
    headers = {
        'Authorization': f'Bearer {token}',
        'Content-Type': 'application/json',
        'Accept': 'application/json'
    }
    
    url = f"https://api.ebay.com{endpoint}"
    print(f"\n🔍 Testing: {endpoint}")
    print(f"   Params: {params}")
    
    try:
        response = requests.get(url, headers=headers, params=params, timeout=30)
        print(f"   Status: {response.status_code}")
        
        if response.status_code == 200:
            data = response.json()
            print(f"   ✅ Success! Keys: {list(data.keys())}")
            return data
        else:
            print(f"   ❌ Error: {response.text[:300]}")
            return None
    except Exception as e:
        print(f"   ❌ Exception: {e}")
        return None

# Calculate date range (last 90 days)
from_date = (datetime.now() - timedelta(days=90)).strftime('%Y-%m-%dT%H:%M:%S.000Z')

print("=" * 70)
print("TESTING EBAY API ENDPOINTS FOR RETURNS DATA")
print("=" * 70)

# Test 1: Fulfillment API - Get orders
print("\n### TEST 1: Fulfillment API - Recent Orders ###")
data = test_ebay_endpoint('/sell/fulfillment/v1/order', {
    'filter': f'lastmodifieddate:[{from_date}..]',
    'limit': 5
})

if data and 'orders' in data:
    print(f"\n   Found {len(data['orders'])} orders")
    if data['orders']:
        first_order = data['orders'][0]
        print(f"   First order keys: {list(first_order.keys())}")
        print(f"   Order status: {first_order.get('orderFulfillmentStatus')}")
        if 'cancelStatus' in first_order:
            print(f"   Cancel status: {first_order['cancelStatus']}")

# Test 2: Fulfillment API - Search for cancelled orders
print("\n### TEST 2: Fulfillment API - Cancelled Orders ###")
data = test_ebay_endpoint('/sell/fulfillment/v1/order', {
    'filter': 'orderfulfillmentstatus:{CANCELLED}',
    'limit': 10
})

# Test 3: Post-Order Case API
print("\n### TEST 3: Post-Order Case Management API ###")
data = test_ebay_endpoint('/post-order/v2/casemanagement/search', {
    'creation_date_range_from': from_date,
    'limit': 10
})

# Test 4: Post-Order Inquiry API
print("\n### TEST 4: Post-Order Inquiry API ###")
data = test_ebay_endpoint('/post-order/v2/inquiry/search', {
    'creation_date_range_from': from_date,
    'limit': 10
})

# Test 5: Post-Order Return API
print("\n### TEST 5: Post-Order Return API ###")
data = test_ebay_endpoint('/post-order/v2/return/search', {
    'creation_date_range_from': from_date,
    'limit': 10
})

# Test 6: Sell Analytics API
print("\n### TEST 6: Sell Analytics - Traffic Report ###")
data = test_ebay_endpoint('/sell/analytics/v1/traffic_report', {
    'dimension': 'LISTING',
    'limit': 5
})

# Test 7: Try Trading API (legacy) - Get orders
print("\n### TEST 7: Trading API (Legacy) - GetOrders ###")
# Note: Trading API uses XML and different auth, skipping for now

print("\n" + "=" * 70)
print("TESTING COMPLETE")
print("=" * 70)
