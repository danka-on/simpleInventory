import json, time, os, requests, base64
from dotenv import load_dotenv
load_dotenv()

# Define base directory for cross-platform compatibility
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
TOKEN_FILE = os.path.join(BASE_DIR, "tokens.json")

CLIENT_ID = os.getenv("EBAY_CLIENT_ID", "your_id")
CLIENT_SECRET = os.getenv("EBAY_CLIENT_SECRET", "your_secret")
SCOPE = os.getenv("EBAY_SCOPE", "https://api.ebay.com/oauth/api_scope")
TOKEN_URL = "https://api.ebay.com/identity/v1/oauth2/token"

def load_tokens():
    with open(TOKEN_FILE) as f:
        return json.load(f)

def save_tokens(data):
    with open(TOKEN_FILE, "w") as f:
        json.dump(data, f, indent=2)

def is_expired(tokens):
    return time.time() > tokens.get("expires_at", 0)

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
    res = requests.post(TOKEN_URL, headers=headers, data=data)
    res.raise_for_status()
    new_tokens = res.json()
    new_tokens["expires_at"] = time.time() + new_tokens["expires_in"]
    new_tokens["refresh_token"] = refresh_token
    save_tokens(new_tokens)
    return new_tokens["access_token"]

def get_access_token():
    tokens = load_tokens()
    if is_expired(tokens):
        return refresh_access_token(tokens["refresh_token"])
    return tokens["access_token"]
