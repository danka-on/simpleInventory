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
from urllib.parse import urlencode, quote
from dotenv import load_dotenv

load_dotenv()

# Configuration from .env
CLIENT_ID = os.getenv("EBAY_CLIENT_ID")
CLIENT_SECRET = os.getenv("EBAY_CLIENT_SECRET")
RUNAME = os.getenv("EBAY_RUNAME")

# Define the scopes you want - add/remove as needed
SCOPES = [
    "https://api.ebay.com/oauth/api_scope",                        # Basic access
    "https://api.ebay.com/oauth/api_scope/sell.inventory",         # Inventory/listings management
    "https://api.ebay.com/oauth/api_scope/sell.fulfillment",       # Orders/fulfillment
    "https://api.ebay.com/oauth/api_scope/sell.finances",          # Payouts & financial data
    "https://api.ebay.com/oauth/api_scope/sell.account",           # Business policies (read/write)
    "https://api.ebay.com/oauth/api_scope/sell.marketing",         # Promoted listings
    "https://api.ebay.com/oauth/api_scope/sell.analytics.readonly", # Seller analytics
]

TOKEN_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tokens.json")


def get_auth_url():
    """Generate the eBay authorization URL"""
    base_url = "https://auth.ebay.com/oauth2/authorize"

    params = {
        "client_id": CLIENT_ID,
        "redirect_uri": RUNAME,
        "response_type": "code",
        "scope": " ".join(SCOPES),
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

    response = requests.post(token_url, headers=headers, data=data)

    if response.status_code == 200:
        return response.json()
    else:
        print(f"Error: {response.status_code}")
        print(response.text)
        return None


def save_tokens(token_data):
    """Save tokens to tokens.json"""
    # Add expiration timestamp
    token_data["expires_at"] = time.time() + token_data.get("expires_in", 7200)

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

    print("\n📋 Scopes that will be requested:")
    for scope in SCOPES:
        scope_name = scope.split("/")[-1]
        print(f"   • {scope_name}")

    # Generate and display auth URL
    auth_url = get_auth_url()

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
    print("        The URL will look like:")
    print("        https://your-redirect-uri?code=v^1.1#i^...&expires_in=...")
    print("=" * 60)

    # Get the authorization code
    print("\nPaste the 'code' parameter from the redirect URL:")
    auth_code = input("> ").strip()

    if not auth_code:
        print("❌ No code provided. Exiting.")
        return

    print("\n🔄 Exchanging code for tokens...")

    token_data = exchange_code_for_tokens(auth_code)

    if token_data:
        save_tokens(token_data)

        print("\n✅ SUCCESS! Your eBay OAuth tokens have been updated.")
        print(f"   Access token expires in: {token_data.get('expires_in', 'unknown')} seconds")
        print(f"   Refresh token expires in: {token_data.get('refresh_token_expires_in', 'unknown')} seconds")

        # Also update .env with new scope
        print("\n📝 Don't forget to update EBAY_SCOPE in your .env file:")
        print(f'   EBAY_SCOPE={" ".join(SCOPES)}')
    else:
        print("\n❌ Failed to exchange code for tokens.")
        print("   Make sure you copied the complete 'code' parameter.")


if __name__ == "__main__":
    main()
