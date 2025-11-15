#!/usr/bin/env python3
"""
Test script to check if we can fetch payout/settlement data from Amazon and eBay
"""

import os
from datetime import datetime, timedelta
from dotenv import load_dotenv

load_dotenv()

print("=" * 80)
print("TESTING PAYOUT DATA AVAILABILITY")
print("=" * 80)

# ==================== AMAZON SETTLEMENTS ====================
print("\n" + "=" * 80)
print("1. TESTING AMAZON SETTLEMENT/PAYOUT DATA")
print("=" * 80)

try:
    from amazon_manager import AmazonManager
    from sp_api.api import Finances
    import time
    
    amazon = AmazonManager()
    finances_api = Finances(credentials=amazon.credentials, marketplace=amazon.marketplace)
    
    # Get settlements from last 90 days
    started_after = (datetime.utcnow() - timedelta(days=90)).isoformat()
    
    print(f"\n🔄 Fetching Amazon settlement groups (payouts) from last 90 days...")
    response = finances_api.list_financial_event_groups(
        FinancialEventGroupStartedAfter=started_after,
        MaxResultsPerPage=10
    )
    
    if response.errors:
        print(f"❌ Errors: {response.errors}")
    else:
        groups = response.payload.get('FinancialEventGroupList', [])
        print(f"\n✅ Found {len(groups)} settlement groups (payouts)\n")
        
        if groups:
            print("Settlement Details:")
            print("-" * 80)
            for i, group in enumerate(groups[:5], 1):  # Show first 5
                group_id = group.get('FinancialEventGroupId')
                start_date = group.get('FinancialEventGroupStart', 'N/A')
                end_date = group.get('FinancialEventGroupEnd', 'N/A')
                status = group.get('ProcessingStatus', 'N/A')
                
                # Get converted amounts
                original_total = group.get('OriginalTotal', {})
                converted_total = group.get('ConvertedTotal', {})
                
                amount = converted_total.get('CurrencyAmount', original_total.get('CurrencyAmount', 0))
                currency = converted_total.get('CurrencyCode', original_total.get('CurrencyCode', 'USD'))
                
                print(f"\n{i}. Settlement ID: {group_id}")
                print(f"   Period: {start_date[:10]} to {end_date[:10]}")
                print(f"   Amount: {currency} ${amount}")
                print(f"   Status: {status}")
                
                # Try to get transaction count for this settlement
                try:
                    print(f"   Fetching transactions for this settlement...")
                    events_response = finances_api.list_financial_events_by_group_id(
                        EventGroupId=group_id,
                        MaxResultsPerPage=100
                    )
                    
                    if not events_response.errors:
                        events = events_response.payload.get('FinancialEvents', {})
                        shipment_count = len(events.get('ShipmentEventList', []))
                        refund_count = len(events.get('RefundEventList', []))
                        adjustment_count = len(events.get('AdjustmentEventList', []))
                        service_fee_count = len(events.get('ServiceFeeEventList', []))
                        
                        print(f"   Transactions: {shipment_count} shipments, {refund_count} refunds, {adjustment_count} adjustments, {service_fee_count} service fees")
                    
                    time.sleep(1)  # Rate limiting
                except Exception as e:
                    print(f"   ⚠️  Could not fetch transactions: {e}")
            
            print("\n✅ AMAZON SETTLEMENT DATA IS AVAILABLE!")
            print("   We can fetch:")
            print("   - Settlement ID")
            print("   - Payout date range")
            print("   - Total payout amount")
            print("   - Processing status")
            print("   - All transactions within each settlement")
        else:
            print("⚠️  No settlements found (might be too recent or no sales)")
            
except ImportError as e:
    print(f"❌ Amazon integration not available: {e}")
except Exception as e:
    print(f"❌ Error testing Amazon settlements: {e}")
    import traceback
    traceback.print_exc()

# ==================== EBAY PAYOUTS ====================
print("\n" + "=" * 80)
print("2. TESTING EBAY PAYOUT DATA")
print("=" * 80)

try:
    import requests
    
    # Check if we have eBay OAuth token (needed for Finances API)
    client_id = os.getenv("EBAY_CLIENT_ID")
    client_secret = os.getenv("EBAY_CLIENT_SECRET")
    
    if not client_id or not client_secret:
        print("❌ eBay OAuth credentials not found")
        print("   Need EBAY_CLIENT_ID and EBAY_CLIENT_SECRET for Finances API")
    else:
        print("✅ eBay OAuth credentials found")
        
        # Get OAuth token
        print("\n🔄 Getting eBay OAuth token...")
        token_url = "https://api.ebay.com/identity/v1/oauth2/token"
        auth_response = requests.post(
            token_url,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            auth=(client_id, client_secret),
            data={"grant_type": "client_credentials", "scope": "https://api.ebay.com/oauth/api_scope"}
        )
        
        if auth_response.status_code == 200:
            access_token = auth_response.json()["access_token"]
            print("✅ OAuth token obtained")
            
            # Try to fetch payout data using Finances API
            print("\n🔄 Fetching eBay payout summaries...")
            
            # eBay Finances API endpoint for payouts
            payouts_url = "https://apiz.ebay.com/sell/finances/v1/payout_summary"
            
            headers = {
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json"
            }
            
            # Get payouts from last 90 days
            filter_date = (datetime.utcnow() - timedelta(days=90)).strftime("%Y-%m-%dT%H:%M:%S.000Z")
            params = {
                "filter": f"payoutDate:[{filter_date}..]",
                "limit": 10
            }
            
            payout_response = requests.get(payouts_url, headers=headers, params=params)
            
            if payout_response.status_code == 200:
                payout_data = payout_response.json()
                payouts = payout_data.get('payouts', [])
                
                print(f"\n✅ Found {len(payouts)} eBay payouts\n")
                
                if payouts:
                    print("Payout Details:")
                    print("-" * 80)
                    for i, payout in enumerate(payouts[:5], 1):  # Show first 5
                        payout_id = payout.get('payoutId', 'N/A')
                        payout_date = payout.get('payoutDate', 'N/A')
                        payout_status = payout.get('payoutStatus', 'N/A')
                        
                        amount_obj = payout.get('amount', {})
                        amount = amount_obj.get('value', '0')
                        currency = amount_obj.get('currency', 'USD')
                        
                        print(f"\n{i}. Payout ID: {payout_id}")
                        print(f"   Date: {payout_date[:10] if payout_date != 'N/A' else 'N/A'}")
                        print(f"   Amount: {currency} ${amount}")
                        print(f"   Status: {payout_status}")
                    
                    print("\n✅ EBAY PAYOUT DATA IS AVAILABLE!")
                    print("   We can fetch:")
                    print("   - Payout ID")
                    print("   - Payout date")
                    print("   - Total payout amount")
                    print("   - Payout status")
                else:
                    print("⚠️  No payouts found (might be too recent or no sales)")
                    
            elif payout_response.status_code == 403:
                print(f"❌ Access forbidden: {payout_response.text}")
                print("   You may need to:")
                print("   - Enable Finances API in your eBay developer account")
                print("   - Use a User OAuth token instead of Application token")
                print("   - Accept eBay's Managed Payments terms")
            else:
                print(f"⚠️  API returned status {payout_response.status_code}")
                print(f"   Response: {payout_response.text}")
                
        else:
            print(f"❌ Failed to get OAuth token: {auth_response.status_code}")
            print(f"   Response: {auth_response.text}")
            
except Exception as e:
    print(f"❌ Error testing eBay payouts: {e}")
    import traceback
    traceback.print_exc()

# ==================== SUMMARY ====================
print("\n" + "=" * 80)
print("SUMMARY")
print("=" * 80)
print("\nAmazon: Settlement data available via SP-API Finances")
print("eBay: Payout data available via Finances API (requires proper OAuth scope)")
print("\nNext steps:")
print("1. Create payouts database table")
print("2. Add sync functions for both platforms")
print("3. Build payouts UI page")
print("4. Integrate with existing auto-sync system")
