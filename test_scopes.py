import json
import requests
from token_manager import get_access_token, load_tokens

def check_token_scopes():
    """Check the scopes of the current OAuth token"""
    tokens = load_tokens()
    
    # Extract and print token info for debugging
    print("=== Token Information ===")
    
    # Print token expiry info
    expires_at = tokens.get("expires_at", 0)
    import time
    current_time = time.time()
    time_until_expiry = expires_at - current_time
    
    print(f"Token expires at: {time.ctime(expires_at)}")
    print(f"Current time: {time.ctime(current_time)}")
    print(f"Time until expiry: {time_until_expiry:.2f} seconds")
    
    # Get fresh token
    access_token = get_access_token()
    print(f"Access token (first 20 chars): {access_token[:20]}...")
    
    # Check if we can access basic account info
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
        "Accept": "application/json"
    }
    
    # Try calling a simple API endpoint
    print("\n=== Testing API Access ===")
    
    # 1. Try accessing the user endpoint
    try:
        print("Testing access to user endpoint...")
        response = requests.get(
            "https://api.sandbox.ebay.com/ws/api.dll",
            headers={
                "X-EBAY-API-SITEID": "0",
                "X-EBAY-API-COMPATIBILITY-LEVEL": "967",
                "X-EBAY-API-CALL-NAME": "GetUser",
                "X-EBAY-API-IAF-TOKEN": access_token,
                "Content-Type": "text/xml"
            },
            data="""<?xml version="1.0" encoding="utf-8"?>
            <GetUserRequest xmlns="urn:ebay:apis:eBLBaseComponents">
                <DetailLevel>ReturnAll</DetailLevel>
            </GetUserRequest>"""
        )
        print(f"Response status: {response.status_code}")
        print(f"Response snippet: {response.text[:200]}...")
    except Exception as e:
        print(f"Error accessing user endpoint: {str(e)}")
    
    # 2. Try a simple inventory API call
    try:
        print("\nTesting access to inventory API...")
        response = requests.get(
            "https://api.sandbox.ebay.com/sell/inventory/v1/inventory_location",
            headers=headers
        )
        print(f"Response status: {response.status_code}")
        print(f"Response body: {response.text}")
    except Exception as e:
        print(f"Error accessing inventory API: {str(e)}")
    
    # 3. Check token directly
    print("\n=== Checking Token Details ===")
    print("Token type:", tokens.get("token_type"))
    print("Refresh token exists:", "refresh_token" in tokens)
    
    # Print guidance
    print("\n=== Recommendations ===")
    print("1. Verify your Application OAuth scopes include:")
    print("   - https://api.ebay.com/oauth/api_scope")
    print("   - https://api.ebay.com/oauth/api_scope/sell.inventory")
    print("   - https://api.ebay.com/oauth/api_scope/sell.account")
    print("2. Make sure you're using the correct Client ID and Secret")
    print("3. Ensure your Sandbox account is fully set up for selling")
    print("4. Try refreshing your token with the tokenrefresh.py script")
    print("5. Check if your eBay Sandbox is opted into Business Policies")

if __name__ == "__main__":
    print("=== eBay API Scope and Authentication Test ===")
    check_token_scopes() 