"""
Check what scopes the current eBay token has
"""
import json
import base64

# Read current token
with open('tokens.json') as f:
    tokens = json.load(f)

access_token = tokens['access_token']

# eBay tokens are JWT-like, decode the payload
try:
    # Split token into parts (v^1.1#i^1#...)
    # eBay tokens are custom format, not standard JWT
    print("Current Token Info:")
    print("-" * 80)
    print(f"Token Type: {tokens.get('token_type', 'Unknown')}")
    print(f"Expires In: {tokens.get('expires_in', 'Unknown')} seconds")
    print(f"Has Refresh Token: {'Yes' if tokens.get('refresh_token') else 'No'}")
    
    # Try to make an API call to check permissions
    import requests
    from token_manager import get_access_token
    
    token = get_access_token()
    print(f"\nToken retrieved successfully: {token[:50]}...")
    
    # Test Finances API access
    print("\n" + "=" * 80)
    print("Testing Finances API Access...")
    print("=" * 80)
    
    headers = {
        'Authorization': f'Bearer {token}',
        'Content-Type': 'application/json',
        'Accept': 'application/json'
    }
    
    # Try to access a Finances API endpoint
    url = "https://apiz.ebay.com/sell/finances/v1/payout_summary"
    params = {'limit': 1}
    
    response = requests.get(url, headers=headers, params=params)
    
    print(f"Status Code: {response.status_code}")
    print(f"Response: {response.text[:500]}")
    
    if response.status_code == 200:
        print("\n✅ SUCCESS! Your token HAS access to the Finances API!")
        print("   You can fetch payouts with your current token.")
    elif response.status_code == 403:
        print("\n❌ FORBIDDEN: Your token does NOT have Finances API scope.")
        print("   You need to generate a new token with sell.finances scope.")
    elif response.status_code == 204:
        print("\n✅ SUCCESS! API accessible (no content = no payouts in range)")
    else:
        print(f"\n⚠️  Unexpected response: {response.status_code}")
        
except Exception as e:
    print(f"\n❌ Error checking token: {e}")
    import traceback
    traceback.print_exc()
