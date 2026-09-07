"""
eBay OAuth Setup Script
Run this to re-authorize your eBay app with expanded scopes.

Usage:
  1. Run: python ebay_oauth_setup.py
  2. Click the authorization URL (or copy/paste into browser)
  3. Sign in to eBay and grant permissions
  4. Copy the 'code' parameter from the redirect URL
  5. Paste it when prompted
  6. Your tokens.json will be updated with the new refresh token
"""

import os
import json
import time
import base64
import requests
import webbrowser
from urllib.parse import urlencode, quote, urlparse, parse_qs, unquote
from dotenv import load_dotenv

load_dotenv()

# Configuration from .env
CLIENT_ID = os.getenv("EBAY_CLIENT_ID")
CLIENT_SECRET = os.getenv("EBAY_CLIENT_SECRET")
RUNAME = os.getenv("EBAY_RUNAME")

# User OAuth scopes for authorization-code flow.
# eBay support confirmed the generic api_scope should be omitted once Logistics
# access is assigned to the application.
BASE_SCOPES = [
    "https://api.ebay.com/oauth/api_scope/sell.inventory.mapping",
    "https://api.ebay.com/oauth/api_scope/sell.inventory",
    "https://api.ebay.com/oauth/api_scope/sell.fulfillment",
    "https://api.ebay.com/oauth/api_scope/sell.finances",
    "https://api.ebay.com/oauth/api_scope/sell.account",
    "https://api.ebay.com/oauth/api_scope/sell.marketing",
    "https://api.ebay.com/oauth/api_scope/sell.analytics.readonly",
]
LOGISTICS_SCOPE = "https://api.ebay.com/oauth/api_scope/sell.logistics"

TOKEN_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tokens.json")


def _extract_auth_result(user_input):
    """
    Accept either:
    - raw auth code
    - full redirect URL containing code/error in query or fragment
    Returns: (code, error, error_description)
    """
    raw = (user_input or "").strip()
    if not raw:
        return "", "", ""

    # If a full URL was pasted, parse both query and hash fragment.
    if "://" in raw and ("?" in raw or "#" in raw):
        parsed = urlparse(raw)
        query = parse_qs(parsed.query or "")
        frag = parse_qs(parsed.fragment or "")

        def pick(name):
            return (
                (query.get(name, [""])[0] or "").strip()
                or (frag.get(name, [""])[0] or "").strip()
            )

        code = pick("code")
        err = pick("error")
        err_desc = pick("error_description")
        return unquote(code), unquote(err), unquote(err_desc)

    # If caller pasted "code=..." directly, normalize it.
    if raw.lower().startswith("code="):
        return unquote(raw.split("=", 1)[1].strip()), "", ""

    # Fallback: assume raw code.
    return unquote(raw), "", ""


def build_scopes(include_logistics=True):
    scopes = list(BASE_SCOPES)
    if include_logistics and LOGISTICS_SCOPE not in scopes:
        scopes.append(LOGISTICS_SCOPE)
    return scopes


def get_auth_url(scopes):
    """Generate the eBay authorization URL"""
    base_url = "https://auth.ebay.com/oauth2/authorize"

    params = {
        "client_id": CLIENT_ID,
        "redirect_uri": RUNAME,
        "response_type": "code",
        "scope": " ".join(scopes),
    }

    return f"{base_url}?{urlencode(params, quote_via=quote)}"


def exchange_code_for_tokens(auth_code):
    """Exchange authorization code for access and refresh tokens"""
    token_url = "https://api.ebay.com/identity/v1/oauth2/token"

    # Create Basic auth header
    credentials = f"{CLIENT_ID}:{CLIENT_SECRET}"
    encoded_credentials = base64.b64encode(credentials.encode()).decode()

    headers = {
        "Content-Type": "application/x-www-form-urlencoded",
        "Authorization": f"Basic {encoded_credentials}"
    }

    data = {
        "grant_type": "authorization_code",
        "code": auth_code,
        "redirect_uri": RUNAME,
    }

    response = requests.post(token_url, headers=headers, data=data, timeout=30)

    if response.status_code == 200:
        return response.json()
    else:
        print(f"Error: {response.status_code}")
        print(response.text)
        return None


def save_tokens(token_data, scopes_used):
    """Save tokens to tokens.json"""
    # Add expiration timestamp
    issued_at = time.time()
    token_data["expires_at"] = issued_at + token_data.get("expires_in", 7200)
    refresh_lifetime = token_data.get("refresh_token_expires_in")
    if refresh_lifetime is not None:
        token_data["refresh_token_issued_at"] = issued_at
        token_data["refresh_token_expires_at"] = issued_at + float(refresh_lifetime)
    if not token_data.get("scope"):
        token_data["scope"] = " ".join(scopes_used or [])

    with open(TOKEN_FILE, "w") as f:
        json.dump(token_data, f, indent=2)

    print(f"\n✅ Tokens saved to {TOKEN_FILE}")


def main():
    print("=" * 60)
    print("eBay OAuth Setup - Expand Scopes")
    print("=" * 60)

    if not CLIENT_ID or not CLIENT_SECRET or not RUNAME:
        print("\n❌ Missing environment variables!")
        print("   Make sure EBAY_CLIENT_ID, EBAY_CLIENT_SECRET, and EBAY_RUNAME are set in .env")
        return

    include_logistics = str(os.getenv("EBAY_REQUEST_LOGISTICS_SCOPE", "1")).strip().lower() not in ("0", "false", "no")
    scopes = build_scopes(include_logistics=include_logistics)

    print(f"\nRequested logistics scope: {'yes' if include_logistics else 'no'}")
    print("\n📋 Scopes that will be requested:")
    for scope in scopes:
        scope_name = scope.split("/")[-1]
        print(f"   • {scope_name}")

    # Generate and display auth URL
    auth_url = get_auth_url(scopes)

    print("\n" + "=" * 60)
    print("STEP 1: Open this URL in your browser:")
    print("=" * 60)
    print(f"\n{auth_url}\n")

    # Try to open in browser
    try:
        open_browser = input("Open in browser automatically? (y/n): ").strip().lower()
        if open_browser == 'y':
            webbrowser.open(auth_url)
            print("Browser opened!")
    except:
        pass

    print("\n" + "=" * 60)
    print("STEP 2: After authorizing, eBay will redirect you.")
    print("        Paste either the full redirect URL OR just the code value.")
    print("=" * 60)

    print("\nPaste redirect URL or code:")
    auth_input = input("> ").strip()

    if not auth_input:
        print("❌ No input provided. Exiting.")
        return

    auth_code, auth_error, auth_error_desc = _extract_auth_result(auth_input)
    if auth_error:
        print("\n❌ eBay returned an authorization error:")
        print(f"   error={auth_error}")
        if auth_error_desc:
            print(f"   description={auth_error_desc}")
        if auth_error == "invalid_scope" and include_logistics:
            print("\nLikely cause: your keyset is not approved for sell.logistics (restricted API).")
            print("You can still reauthorize core scopes now, then request Logistics access separately.")
            retry = input("Retry now WITHOUT sell.logistics? (y/n): ").strip().lower()
            if retry != "y":
                print("Aborted.")
                return
            scopes = build_scopes(include_logistics=False)
            auth_url = get_auth_url(scopes)
            print("\nOpen this fallback URL:")
            print(auth_url)
            try:
                open_browser = input("Open fallback URL in browser automatically? (y/n): ").strip().lower()
                if open_browser == 'y':
                    webbrowser.open(auth_url)
            except Exception:
                pass
            print("\nPaste redirect URL or code from fallback authorization:")
            auth_input = input("> ").strip()
            auth_code, auth_error, auth_error_desc = _extract_auth_result(auth_input)
            if auth_error:
                print("\n❌ Fallback authorization also failed:")
                print(f"   error={auth_error}")
                if auth_error_desc:
                    print(f"   description={auth_error_desc}")
                return
            if not auth_code:
                print("\n❌ Could not find an authorization code in fallback input.")
                return
        else:
            print("   Fix the app permissions/Runame mismatch and try again.")
            return
    if not auth_code:
        print("\n❌ Could not find an authorization code in your input.")
        print("   Paste the full redirect URL from the browser address bar.")
        return

    print("\n🔄 Exchanging code for tokens...")

    token_data = exchange_code_for_tokens(auth_code)

    if token_data:
        save_tokens(token_data, scopes)

        print("\n✅ SUCCESS! Your eBay OAuth tokens have been updated.")
        print(f"   Access token expires in: {token_data.get('expires_in', 'unknown')} seconds")
        print(f"   Refresh token expires in: {token_data.get('refresh_token_expires_in', 'unknown')} seconds")

        # Also update .env with new scope
        print("\n📝 Don't forget to update EBAY_SCOPE in your .env file:")
        print(f'   EBAY_SCOPE={" ".join(scopes)}')
    else:
        print("\n❌ Failed to exchange code for tokens.")
        print("   Make sure you copied the complete 'code' parameter.")


if __name__ == "__main__":
    main()
