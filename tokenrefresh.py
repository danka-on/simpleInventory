import requests, os
import base64
import time
import json
import webbrowser

from dotenv import load_dotenv
load_dotenv()

CLIENT_ID = os.getenv("EBAY_CLIENT_ID")
CLIENT_SECRET = os.getenv("EBAY_CLIENT_SECRET")
RUNAME = os.getenv("EBAY_RUNAME")

# Check if environment variables are missing
if not CLIENT_ID or not CLIENT_SECRET or not RUNAME:
    print("❌ Error: Missing required environment variables")
    print("Please make sure you have set EBAY_CLIENT_ID, EBAY_CLIENT_SECRET, and EBAY_RUNAME in your .env file")
    exit(1)

# Define the scopes needed for the application
SCOPES = [
    "https://api.ebay.com/oauth/api_scope",
    "https://api.ebay.com/oauth/api_scope/sell.account",
    "https://api.ebay.com/oauth/api_scope/sell.inventory"
]

# Step 1: Generate an authorization URL
def get_authorization_url():
    auth_url = "https://auth.sandbox.ebay.com/oauth2/authorize"
    params = {
        "client_id": CLIENT_ID,
        "response_type": "code",
        "redirect_uri": RUNAME,
        "scope": " ".join(SCOPES),
        "prompt": "login"
    }
    
    # Create the full URL with parameters
    url_parts = []
    for key, value in params.items():
        url_parts.append(f"{key}={requests.utils.quote(value)}")
    
    full_url = f"{auth_url}?{'&'.join(url_parts)}"
    return full_url

# Step 2: Exchange the authorization code for tokens
def exchange_code_for_tokens(auth_code):
    encoded_credentials = base64.b64encode(f"{CLIENT_ID}:{CLIENT_SECRET}".encode()).decode()
    
    headers = {
        "Content-Type": "application/x-www-form-urlencoded",
        "Authorization": f"Basic {encoded_credentials}"
    }
    
    data = {
        "grant_type": "authorization_code",
        "code": auth_code,
        "redirect_uri": RUNAME
    }
    
    response = requests.post(
        "https://api.sandbox.ebay.com/identity/v1/oauth2/token",
        headers=headers,
        data=data
    )
    
    if response.status_code == 200:
        tokens = response.json()
        tokens["expires_at"] = time.time() + tokens["expires_in"]
        
        # Save to file
        with open("tokens.json", "w") as f:
            json.dump(tokens, f, indent=2)
        
        print("✅ Token exchange successful! Tokens saved to tokens.json.")
        print(f"Token will expire at: {time.ctime(tokens['expires_at'])}")
        return True
    else:
        print(f"❌ Failed to exchange token: {response.status_code}")
        print(response.text)
        return False

# Main flow
def main():
    print("==== eBay OAuth Token Refresh Tool ====")
    print("1. Opening authorization URL in your browser...")
    auth_url = get_authorization_url()
    print(f"Authorization URL: {auth_url}")
    
    # Open the URL in the default browser
    try:
        webbrowser.open(auth_url)
    except Exception as e:
        print(f"Could not open browser automatically: {e}")
        print("Please copy and paste the URL into your browser manually.")
    
    print("\n2. After you authorize the application, eBay will redirect you to your RuName URL.")
    print("   Copy the entire URL from your browser's address bar after being redirected.")
    
    redirect_url = input("\nEnter the redirect URL: ")
    
    # Extract the authorization code from the redirect URL
    try:
        auth_code = redirect_url.split("code=")[1].split("&")[0]
        print(f"Authorization code extracted: {auth_code[:10]}...")
        
        # Exchange the code for tokens
        exchange_code_for_tokens(auth_code)
    except IndexError:
        print("❌ Could not extract authorization code from the URL.")
        print("Make sure you copied the entire redirect URL.")
        return

if __name__ == "__main__":
    main()
