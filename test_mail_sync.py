#!/usr/bin/env python3
"""Diagnostic script to test mail-center Amazon message syncing."""

import os
import sys
import json
import sqlite3
from datetime import datetime, timedelta

# Add workspace to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

def test_amazon_mail_sync():
    """Test if Amazon mail sync works."""
    print("=" * 70)
    print("AMAZON MAIL-CENTER DIAGNOSTIC")
    print("=" * 70)
    
    # Check credentials
    print("\n1. Checking Amazon credentials...")
    try:
        with open('amazon_credentials.json', 'r') as f:
            creds = json.load(f)
            print(f"   ✓ Credentials found")
            print(f"   - Has refresh_token: {'refresh_token' in creds}")
            print(f"   - Has lwa_app_id: {'lwa_app_id' in creds}")
            print(f"   - Has lwa_client_secret: {'lwa_client_secret' in creds}")
    except Exception as e:
        print(f"   ✗ Error loading credentials: {e}")
        return
    
    # Check sold.db for Amazon orders
    print("\n2. Checking for Amazon orders in sold.db...")
    try:
        conn = sqlite3.connect('sold.db')
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        
        # Check if orders table exists
        cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='orders'")
        if not cur.fetchone():
            print("   ✗ 'orders' table not found in sold.db")
            conn.close()
            return
        
        # Get Amazon orders from last 7 days
        rows = cur.execute('''
            SELECT order_id, MAX(COALESCE(paid_time, shipped_time)) AS ts
            FROM orders
            WHERE store = 'amazon'
              AND order_id IS NOT NULL
              AND TRIM(order_id) != ''
              AND date(COALESCE(paid_time, shipped_time, date('now'))) >= date('now', '-7 days')
            GROUP BY order_id
            ORDER BY datetime(ts) DESC
            LIMIT 10
        ''').fetchall()
        
        print(f"   ✓ Found {len(rows)} Amazon orders from last 7 days:")
        for r in rows[:5]:
            print(f"     - {r['order_id']} (ts: {r['ts']})")
        
        if not rows:
            print("   ⚠ No Amazon orders found in the last 7 days - check may be too strict")
            # List all Amazon orders
            all_orders = cur.execute('''
                SELECT COUNT(*) as cnt FROM orders WHERE store = 'amazon' AND order_id IS NOT NULL
            ''').fetchone()
            print(f"   - Total Amazon orders in database: {all_orders['cnt']}")
        
        conn.close()
    except Exception as e:
        print(f"   ✗ Error checking orders: {e}")
        import traceback
        traceback.print_exc()
        return
    
    # Check storemail.db
    print("\n3. Checking storemail.db...")
    try:
        conn = sqlite3.connect('storemail.db')
        cur = conn.cursor()
        
        # Check table exists
        cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='store_messages'")
        if not cur.fetchone():
            print("   ⚠ 'store_messages' table not created yet")
        else:
            # Count Amazon messages
            count = cur.execute("SELECT COUNT(*) FROM store_messages WHERE store = 'Amazon'").fetchone()[0]
            print(f"   ✓ Found {count} Amazon messages in storemail.db")
            
            # Show recent ones
            recent = cur.execute('''
                SELECT id, subject, sender_name, created_at 
                FROM store_messages 
                WHERE store = 'Amazon'
                ORDER BY created_at DESC
                LIMIT 5
            ''').fetchall()
            if recent:
                print("   Recent Amazon messages:")
                for r in recent:
                    print(f"     - [{r[0]}] {r[1][:40]}... from {r[2]} ({r[3]})")
        
        conn.close()
    except Exception as e:
        print(f"   ✗ Error checking storemail.db: {e}")
        import traceback
        traceback.print_exc()
        return
    
    # Test SP-API Messaging availability
    print("\n4. Testing Amazon SP-API Messaging module...")
    try:
        from sp_api.api import Messaging
        print(f"   ✓ sp_api.api.Messaging module available")
        
        # Try to initialize
        from sp_api.base import Marketplaces
        marketplace = Marketplaces.US
        print(f"   ✓ Marketplaces module available ({marketplace})")
        
    except ImportError as e:
        print(f"   ✗ SP-API Messaging module not available: {e}")
    except Exception as e:
        print(f"   ✗ Error testing SP-API: {e}")
    
    # Show the actual sync implementation
    print("\n5. Current Amazon mail sync implementation:")
    print("   Uses: get_messaging_actions_for_order()")
    print("   This fetches 'messaging actions' (links/URLs) not actual messages")
    print("   ⚠ LIMITATION: This is not actual customer messages!")
    
    print("\n" + "=" * 70)
    print("FINDINGS:")
    print("=" * 70)
    print("""
The current implementation has a fundamental limitation:

• It uses get_messaging_actions_for_order() to fetch messaging action links
• These are NOT actual customer messages/conversations
• It's storing just the available action links as mock messages
• Real Amazon messaging requires the actual Messaging API endpoint

RECOMMENDED FIX:
Use the actual Amazon SP-API Messaging endpoints to fetch real messages:
  1. CreateConfidentialListingID (for secure communications)
  2. GetMessages (fetch actual customer messages)
  3. CreateListing for returns/messaging flow

Check Amazon's SP-API documentation for the Messaging section.
    """)

if __name__ == '__main__':
    test_amazon_mail_sync()
