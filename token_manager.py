import json, time, os, requests, base64
from dotenv import load_dotenv
load_dotenv()

# Define base directory for cross-platform compatibility
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
TOKEN_FILE = os.path.join(BASE_DIR, "tokens.json")

CLIENT_ID = os.getenv("EBAY_CLIENT_ID", "your_id")
CLIENT_SECRET = os.getenv("EBAY_CLIENT_SECRET", "your_secret")
TOKEN_URL = "https://api.ebay.com/identity/v1/oauth2/token"
GENERIC_SCOPE = "https://api.ebay.com/oauth/api_scope"
DEFAULT_USER_SCOPES = [
    "https://api.ebay.com/oauth/api_scope/sell.inventory",
    "https://api.ebay.com/oauth/api_scope/sell.fulfillment",
    "https://api.ebay.com/oauth/api_scope/sell.logistics",
    "https://api.ebay.com/oauth/api_scope/sell.finances",
    "https://api.ebay.com/oauth/api_scope/sell.account",
    "https://api.ebay.com/oauth/api_scope/sell.marketing",
    "https://api.ebay.com/oauth/api_scope/sell.analytics.readonly",
]

def _scope_tokens(value):
    seen = set()
    out = []
    for token in str(value or "").replace(",", " ").split():
        scope = token.strip()
        if not scope or scope in seen:
            continue
        seen.add(scope)
        out.append(scope)
    return out

def _normalize_user_scope(value):
    scopes = _scope_tokens(value)
    if GENERIC_SCOPE in scopes and any(scope != GENERIC_SCOPE for scope in scopes):
        scopes = [scope for scope in scopes if scope != GENERIC_SCOPE]
    return " ".join(scopes)

SCOPE = _normalize_user_scope(os.getenv("EBAY_SCOPE") or " ".join(DEFAULT_USER_SCOPES))

def load_tokens():
    with open(TOKEN_FILE) as f:
        return json.load(f)

def save_tokens(data):
    with open(TOKEN_FILE, "w") as f:
        json.dump(data, f, indent=2)

def is_expired(tokens):
    return time.time() > tokens.get("expires_at", 0)

def refresh_access_token(refresh_token, previous_scope=""):
    encoded = base64.b64encode(f"{CLIENT_ID}:{CLIENT_SECRET}".encode()).decode()
    headers = {
        "Content-Type": "application/x-www-form-urlencoded",
        "Authorization": f"Basic {encoded}"
    }
    requested_scope = (SCOPE or "").strip()
    data = {
        "grant_type": "refresh_token",
        "refresh_token": refresh_token
    }
    used_scope = ""
    if requested_scope:
        data["scope"] = requested_scope
        used_scope = requested_scope

    res = requests.post(TOKEN_URL, headers=headers, data=data)
    if res.status_code >= 400 and requested_scope:
        body = ""
        try:
            body = res.text or ""
        except Exception:
            body = ""
        if "invalid_scope" in body.lower():
            # Fallback for refresh tokens that have not yet been re-authorized for newly added scopes.
            data = {
                "grant_type": "refresh_token",
                "refresh_token": refresh_token
            }
            used_scope = ""
            res = requests.post(TOKEN_URL, headers=headers, data=data)

    res.raise_for_status()
    new_tokens = res.json()
    new_tokens["expires_at"] = time.time() + new_tokens["expires_in"]
    new_tokens["refresh_token"] = refresh_token
    if not new_tokens.get("scope"):
        # eBay refresh responses may omit scope; keep best-known scope metadata for downstream capability checks.
        if used_scope:
            new_tokens["scope"] = used_scope
        elif previous_scope:
            new_tokens["scope"] = previous_scope
    save_tokens(new_tokens)
    return new_tokens["access_token"]

def get_access_token():
    tokens = load_tokens()
    if is_expired(tokens):
        return refresh_access_token(tokens["refresh_token"], previous_scope=tokens.get("scope", ""))
    return tokens["access_token"]
