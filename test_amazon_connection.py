"""
Test script to verify Amazon SP-API setup
Run this to test your Amazon credentials before using in the app
"""

import sys
import json

print("🔍 Testing Amazon SP-API Configuration...\n")

# Check if credentials file exists
try:
    with open('amazon_credentials.json', 'r') as f:
        creds = json.load(f)
    print("✅ Credentials file found")
except FileNotFoundError:
    print("❌ amazon_credentials.json not found!")
    sys.exit(1)

# Check required fields
required_fields = ['lwa_app_id', 'refresh_token']
missing = []
for field in required_fields:
    if not creds.get(field):
        missing.append(field)

if missing:
    print(f"⚠️  Missing required fields: {', '.join(missing)}")
    print("\nRequired fields:")
    print("  - lwa_app_id: Your LWA Application ID")
    print("  - lwa_client_secret: Your LWA Client Secret (IMPORTANT!)")
    print("  - refresh_token: Your refresh token")
else:
    print("✅ All required fields present")

print(f"\nCurrent credentials:")
print(f"  LWA App ID: {creds.get('lwa_app_id', 'NOT SET')}")
print(f"  Client Secret: {'SET' if creds.get('lwa_client_secret') else 'NOT SET (REQUIRED!)'}")
print(f"  Refresh Token: {'SET' if creds.get('refresh_token') else 'NOT SET'}")
print(f"  Marketplace: {creds.get('marketplace_id', 'ATVPDKIKX0DER (US)')}")

# Check if SP-API library is installed
try:
    from sp_api.api import Orders
    from sp_api.base import Marketplaces
    print("\n✅ python-amazon-sp-api library installed")
except ImportError as e:
    print(f"\n❌ python-amazon-sp-api not installed: {e}")
    print("Install it with: pip install python-amazon-sp-api")
    sys.exit(1)

# Try to initialize Amazon Manager
try:
    from amazon_manager import AmazonManager
    print("✅ AmazonManager module loaded")
    
    # Check if we have client secret
    if not creds.get('lwa_client_secret'):
        print("\n⚠️  WARNING: lwa_client_secret is required!")
        print("You need to add your LWA Client Secret to amazon_credentials.json")
        print("\nTo get your client secret:")
        print("1. Go to https://sellercentral.amazon.com/apps/manage")
        print("2. Find your app (the one with client ID: " + creds.get('lwa_app_id', '') + ")")
        print("3. Click 'View' and copy the 'LWA Client Secret'")
        print("4. Add it to amazon_credentials.json as 'lwa_client_secret'")
        sys.exit(1)
    
    # Try to connect
    print("\n🔄 Testing API connection...")
    manager = AmazonManager()
    success = manager.test_connection()
    
    if success:
        print("\n🎉 Amazon SP-API connection successful!")
        print("You can now use the Amazon integration in your app.")
    else:
        print("\n❌ Connection test failed")
        print("Check the error messages above for details")
        
except Exception as e:
    print(f"\n❌ Error: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)
