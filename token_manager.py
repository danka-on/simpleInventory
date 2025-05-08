import json, time, os, requests, base64
from dotenv import load_dotenv
load_dotenv()


TOKEN_FILE = "tokens.json"
CLIENT_ID = os.getenv("EBAY_CLIENT_ID", "your_id")
CLIENT_SECRET = os.getenv("EBAY_CLIENT_SECRET", "your_secret")
<<<<<<< Updated upstream
# Update with required scopes for account and inventory
SCOPE = os.getenv("EBAY_SCOPE", "https://api.ebay.com/oauth/api_scope https://api.ebay.com/oauth/api_scope/sell.account https://api.ebay.com/oauth/api_scope/sell.inventory")
TOKEN_URL = "https://api.sandbox.ebay.com/identity/v1/oauth2/token"
=======
SCOPE = os.getenv("EBAY_SCOPE", "https://api.ebay.com/oauth/api_scope")
TOKEN_URL = "https://api.ebay.com/identity/v1/oauth2/token"
>>>>>>> Stashed changes

def load_tokens():
    try:
        with open(TOKEN_FILE) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError) as e:
        print(f"Error loading tokens: {e}")
        # Return default structure if file doesn't exist or is invalid
        return {"access_token": "", "refresh_token": "", "expires_at": 0}

def save_tokens(data):
    with open(TOKEN_FILE, "w") as f:
        json.dump(data, f, indent=2)

def is_expired(tokens):
    # Add 60 second buffer to ensure token is still valid
    return time.time() + 60 > tokens.get("expires_at", 0)

def refresh_access_token(refresh_token):
    encoded = base64.b64encode(f"{CLIENT_ID}:{CLIENT_SECRET}".encode()).decode()
    headers = {
        "Content-Type": "application/x-www-form-urlencoded",
        "Authorization": f"Basic {encoded}"
    }
    data = {
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "scope": SCOPE
    }
    
    print("Refreshing token...")
    res = requests.post(TOKEN_URL, headers=headers, data=data)
    
    if res.status_code != 200:
        print(f"Error refreshing token: {res.status_code}")
        print(res.text)
        raise Exception(f"Failed to refresh token: {res.text}")
        
    new_tokens = res.json()
    new_tokens["expires_at"] = time.time() + new_tokens["expires_in"]
    
    # Keep original refresh token if not provided in response
    if "refresh_token" not in new_tokens:
        new_tokens["refresh_token"] = refresh_token
        
    save_tokens(new_tokens)
    print(f"Token refreshed. New expiry: {time.ctime(new_tokens['expires_at'])}")
    return new_tokens["access_token"]

def get_access_token():
    tokens = load_tokens()
    if is_expired(tokens):
        try:
            return refresh_access_token(tokens["refresh_token"])
        except Exception as e:
            print(f"Failed to refresh token: {e}")
            # Return existing token even if expired as fallback
            return tokens["access_token"]
    return tokens["access_token"]
