"""
Update Amazon refresh token in credentials file
"""

import json
import os

def update_refresh_token():
    """Update the refresh token in amazon_credentials.json"""
    
    creds_file = 'amazon_credentials.json'
    
    if not os.path.exists(creds_file):
        print(f"❌ Credentials file not found: {creds_file}")
        return
    
    # Load current credentials
    with open(creds_file, 'r') as f:
        creds = json.load(f)
    
    print("Current Amazon Credentials:")
    print("=" * 80)
    print(f"LWA App ID: {creds.get('lwa_app_id', 'N/A')}")
    print(f"Marketplace: {creds.get('marketplace_id', 'N/A')}")
    print(f"Current Refresh Token: {creds.get('refresh_token', 'N/A')[:20]}...")
    
    print("\n" + "=" * 80)
    print("To generate a new refresh token with Product Listing permission:")
    print("=" * 80)
    print("1. Go to: Seller Central → Apps & Services → Develop Apps")
    print("2. Click on your app")
    print("3. Look for 'Authorize' or 'View Authorization Instructions'")
    print("4. Click the authorization URL (it will ask for Product Listing permission)")
    print("5. Authorize and copy the new refresh token")
    print("\n" + "=" * 80)
    
    new_token = input("\nEnter new refresh token (or press Enter to cancel): ").strip()
    
    if new_token:
        # Backup old credentials
        backup_file = 'amazon_credentials.json.bak'
        with open(backup_file, 'w') as f:
            json.dump(creds, f, indent=2)
        print(f"✅ Backed up old credentials to: {backup_file}")
        
        # Update token
        creds['refresh_token'] = new_token
        
        # Save updated credentials
        with open(creds_file, 'w') as f:
            json.dump(creds, f, indent=2)
        
        print(f"✅ Updated refresh token in: {creds_file}")
        print("\nNow test the connection with:")
        print("  python check_amazon_permissions.py")
    else:
        print("❌ Cancelled - no changes made")

if __name__ == "__main__":
    update_refresh_token()
