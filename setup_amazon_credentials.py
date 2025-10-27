"""
Helper script to update Amazon credentials
Use this to add your LWA Client Secret
"""

import json

print("🔐 Amazon Credentials Setup\n")

# Load existing credentials
try:
    with open('amazon_credentials.json', 'r') as f:
        creds = json.load(f)
except FileNotFoundError:
    creds = {
        "lwa_app_id": "",
        "lwa_client_secret": "",
        "refresh_token": "",
        "marketplace_id": "ATVPDKIKX0DER",
        "region": "us-east-1"
    }

print("Current credentials:")
print(f"  LWA App ID: {creds.get('lwa_app_id', 'NOT SET')}")
print(f"  Client Secret: {'*' * 20 if creds.get('lwa_client_secret') else 'NOT SET'}")
print(f"  Refresh Token: {'*' * 20 if creds.get('refresh_token') else 'NOT SET'}")

print("\n" + "="*60)
print("To add your LWA Client Secret:")
print("1. Go to: https://sellercentral.amazon.com/apps/manage")
print("2. Find your app with Client ID: " + creds.get('lwa_app_id', ''))
print("3. Click 'View' and copy the 'LWA Client Secret'")
print("4. Paste it below")
print("="*60 + "\n")

# Get client secret
client_secret = input("Enter your LWA Client Secret (or press Enter to skip): ").strip()

if client_secret:
    creds['lwa_client_secret'] = client_secret
    
    # Save updated credentials
    with open('amazon_credentials.json', 'w') as f:
        json.dump(creds, f, indent=2)
    
    print("\n✅ Credentials updated successfully!")
    print("Run 'python test_amazon_connection.py' to test the connection")
else:
    print("\n⚠️  Skipped - Client Secret not updated")
    print("Note: The client secret is REQUIRED for Amazon SP-API to work")
