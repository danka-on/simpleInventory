#!/usr/bin/env python3
"""Test Amazon SP-API Messaging API to understand available endpoints."""

import os
import sys
import json
from sp_api.api import Messaging
from sp_api.base import Marketplaces

# Load credentials
with open('amazon_credentials.json', 'r') as f:
    creds = json.load(f)

credentials = {
    'refresh_token': creds['refresh_token'],
    'lwa_app_id': creds['lwa_app_id'],
    'lwa_client_secret': creds.get('lwa_client_secret', ''),
}

marketplace = Marketplaces.US

print("=" * 70)
print("AMAZON SP-API MESSAGING ENDPOINT EXPLORATION")
print("=" * 70)

try:
    msg_api = Messaging(credentials=credentials, marketplace=marketplace)
    print("\n✓ Messaging API initialized successfully")
    
    # Print available methods
    print("\nAvailable methods on Messaging API:")
    methods = [m for m in dir(msg_api) if not m.startswith('_') and callable(getattr(msg_api, m))]
    for method in sorted(methods):
        print(f"  - {method}()")
        
    # Check documentation
    print("\nMessaging class docstring:")
    print(msg_api.__doc__ if msg_api.__doc__ else "No docstring available")
    
except Exception as e:
    print(f"✗ Error initializing API: {e}")
    import traceback
    traceback.print_exc()
