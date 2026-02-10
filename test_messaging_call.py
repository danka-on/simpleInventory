#!/usr/bin/env python3
"""Test actual Amazon SP-API messaging call with detailed error reporting."""

import os
import sys
import json
import sqlite3
from datetime import datetime, timedelta

# Load credentials
with open('amazon_credentials.json', 'r') as f:
    creds = json.load(f)

credentials = {
    'refresh_token': creds['refresh_token'],
    'lwa_app_id': creds['lwa_app_id'],
    'lwa_client_secret': creds.get('lwa_client_secret', ''),
}

print("=" * 70)
print("AMAZON SP-API MESSAGING API TEST")
print("=" * 70)

print("\n1. Initializing Messaging API...")
try:
    from sp_api.api import Messaging
    from sp_api.base import Marketplaces
    
    marketplace = Marketplaces.US
    marketplace_id = 'ATVPDKIKX0DER'  # US marketplace
    
    msg_api = Messaging(credentials=credentials, marketplace=marketplace)
    print(f"   ✓ Messaging API initialized")
except Exception as e:
    print(f"   ✗ Error: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)

print("\n2. Getting Amazon orders from sold.db...")
try:
    conn = sqlite3.connect('sold.db')
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    
    rows = cur.execute('''
        SELECT order_id
        FROM orders
        WHERE store = 'amazon'
          AND order_id IS NOT NULL
          AND TRIM(order_id) != ''
        LIMIT 5
    ''').fetchall()
    
    if not rows:
        print("   ✗ No Amazon orders found")
        sys.exit(1)
    
    print(f"   ✓ Found {len(rows)} Amazon orders:")
    for r in rows:
        print(f"     - {r['order_id']}")
    
    conn.close()
except Exception as e:
    print(f"   ✗ Error: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)

print("\n3. Testing get_messaging_actions_for_order() with first order...")
try:
    order_id = rows[0]['order_id']
    print(f"   Calling with order_id: {order_id}")
    print(f"   Marketplace ID: {marketplace_id}")
    
    resp = msg_api.get_messaging_actions_for_order(order_id, marketplaceIds=[marketplace_id])
    
    print(f"   ✓ API call succeeded!")
    print(f"   Response type: {type(resp)}")
    print(f"   Response: {resp}")
    
    if hasattr(resp, 'payload'):
        print(f"   Payload: {resp.payload}")
    
    if hasattr(resp, 'errors'):
        print(f"   Errors: {resp.errors}")
        
except Exception as e:
    print(f"   ✗ Error: {e}")
    print(f"   Error type: {type(e).__name__}")
    import traceback
    traceback.print_exc()
