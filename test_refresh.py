import requests
import base64
import json
import time
import os
from dotenv import load_dotenv

load_dotenv()

# Get API credentials from environment variables
CLIENT_ID = os.getenv("EBAY_CLIENT_ID")
CLIENT_SECRET = os.getenv("EBAY_CLIENT_SECRET")

# Token file location
TOKEN_FILE = "tokens.json"

# Required scopes for inventory API - simplified
SCOPES = [
    "https://api.ebay.com/oauth/api_scope",
    "https://api.ebay.com/oauth/api_scope/sell.inventory"
]

def refresh_token():
    """Refresh the OAuth token with explicit scopes"""
    
    if not CLIENT_ID or not CLIENT_SECRET:
        print("ERROR: Missing API credentials - set EBAY_CLIENT_ID and EBAY_CLIENT_SECRET in .env file")
        return False
        
    try:
        # Load existing tokens
        with open(TOKEN_FILE, "r") as f:
            tokens = json.load(f)
            refresh_token = tokens.get("refresh_token")
            
        if not refresh_token:
            print("ERROR: No refresh token found in tokens.json")
            return False
            
        print(f"Found refresh token: {refresh_token[:10]}...")
        
        # Encode credentials
        credentials = f"{CLIENT_ID}:{CLIENT_SECRET}"
        encoded_credentials = base64.b64encode(credentials.encode()).decode()
        
        # Set up request headers and data
        headers = {
            "Content-Type": "application/x-www-form-urlencoded",
            "Authorization": f"Basic {encoded_credentials}"
        }
        
        data = {
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "scope": " ".join(SCOPES)
        }
        
        print("Requesting token refresh with scopes:")
        for scope in SCOPES:
            print(f"  - {scope}")
        
        # Make token refresh request
        response = requests.post(
            "https://api.sandbox.ebay.com/identity/v1/oauth2/token",
            headers=headers,
            data=data
        )
        
        print(f"\nResponse status: {response.status_code}")
        
        if response.status_code == 200:
            new_tokens = response.json()
            
            # Add expiration time
            new_tokens["expires_at"] = time.time() + new_tokens["expires_in"]
            
            # Keep original refresh token if not provided (usually the case)
            if "refresh_token" not in new_tokens:
                new_tokens["refresh_token"] = refresh_token
            
            # Save tokens
            with open(TOKEN_FILE, "w") as f:
                json.dump(new_tokens, f, indent=2)
                
            print("✅ Success! Token refreshed and saved with expanded scopes.")
            print(f"Token expires at: {time.ctime(new_tokens['expires_at'])}")
            
            # Print summary
            token_summary = new_tokens.copy()
            if "access_token" in token_summary:
                token_summary["access_token"] = token_summary["access_token"][:15] + "..."
            if "refresh_token" in token_summary:
                token_summary["refresh_token"] = token_summary["refresh_token"][:15] + "..."
                
            print("\nToken details:")
            print(json.dumps(token_summary, indent=2))
            
            return True
        else:
            print(f"❌ Error refreshing token: {response.status_code}")
            print(response.text)
            return False
            
    except Exception as e:
        print(f"❌ Error: {str(e)}")
        return False

if __name__ == "__main__":
    print("=== eBay OAuth Token Refresh with Expanded Scopes ===")
    refresh_token() 