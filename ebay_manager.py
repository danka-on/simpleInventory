"""
eBay Returns Manager
Handles fetching and syncing eBay returns data
"""

import os
import sqlite3
import time
from datetime import datetime, timedelta
import requests
from token_manager import get_access_token

class EbayManager:
    def __init__(self):
        self.base_url = "https://api.ebay.com"
        
    def get_access_token(self):
        """Get OAuth token using existing token_manager"""
        try:
            return get_access_token()
        except Exception as e:
            print(f"❌ Error getting eBay access token: {e}")
            return None
    
    def get_order_fees(self, days_back=90):
        """
        Fetch seller fees from eBay Fulfillment API
        eBay includes totalMarketplaceFee in each order
        """
        try:
            token = self.get_access_token()
            if not token:
                print("❌ No eBay access token available")
                return {}
            
            headers = {
                'Authorization': f'Bearer {token}',
                'Content-Type': 'application/json',
                'Accept': 'application/json'
            }
            
            # Calculate date range
            from_date = (datetime.now() - timedelta(days=days_back)).strftime('%Y-%m-%dT%H:%M:%S.000Z')
            
            print(f"🔄 Fetching eBay seller fees from last {days_back} days...")
            
            url = f"{self.base_url}/sell/fulfillment/v1/order"
            
            params = {
                'filter': f'lastmodifieddate:[{from_date}..]',
                'limit': 200
            }
            
            fees_data = {}
            offset = 0
            
            while True:
                params['offset'] = offset
                
                try:
                    response = requests.get(url, headers=headers, params=params, timeout=30)
                    
                    if response.status_code == 200:
                        data = response.json()
                        orders = data.get('orders', [])
                        
                        if not orders:
                            break
                        
                        print(f"  📄 Processing {len(orders)} orders (offset {offset})...")
                        
                        for order in orders:
                            order_id = order.get('legacyOrderId', order.get('orderId'))
                            
                            # Get marketplace fee
                            marketplace_fee = order.get('totalMarketplaceFee', {})
                            fee_amount = float(marketplace_fee.get('value', 0))
                            
                            if fee_amount > 0:
                                fees_data[order_id] = fee_amount
                        
                        # Check if there are more results
                        total = data.get('total', 0)
                        if offset + len(orders) >= total:
                            break
                        
                        offset += len(orders)
                        time.sleep(0.5)  # Rate limiting
                        
                    elif response.status_code == 204:
                        break
                    else:
                        print(f"❌ Error fetching eBay orders: {response.status_code}")
                        break
                        
                except Exception as e:
                    print(f"❌ Error in eBay request: {e}")
                    break
            
            print(f"\n✅ Retrieved fees for {len(fees_data)} eBay orders")
            return fees_data
            
        except Exception as e:
            print(f"❌ Error in get_order_fees: {e}")
            import traceback
            traceback.print_exc()
            return {}
    
    def sync_fees_to_db(self, days_back=90):
        """
        Sync eBay seller fees to sold.db orders table
        """
        print("🔄 Starting eBay fees sync...")
        
        fees_data = self.get_order_fees(days_back=days_back)
        
        if not fees_data:
            print("ℹ️  No eBay fees to sync")
            return 0
        
        conn = sqlite3.connect('sold.db')
        cur = conn.cursor()
        
        updated_count = 0
        
        for order_id, fee_amount in fees_data.items():
            # Update seller_fee for this order
            cur.execute('''
                UPDATE orders
                SET seller_fee = ?
                WHERE order_id = ? AND store = 'ebay'
            ''', (fee_amount, order_id))
            
            if cur.rowcount > 0:
                updated_count += 1
                print(f"  ✅ {order_id}: ${fee_amount:.2f} seller fee")
        
        conn.commit()
        conn.close()
        
        print(f"\n✅ Updated {updated_count} eBay orders with seller fees")
        return updated_count
    
    def get_returns(self, days_back=90):
        """
        Fetch returns/refunds from eBay Fulfillment API
        eBay includes refund data in the paymentSummary and lineItems of orders
        """
        try:
            token = self.get_access_token()
            if not token:
                print("❌ No eBay access token available")
                return []
            
            headers = {
                'Authorization': f'Bearer {token}',
                'Content-Type': 'application/json',
                'Accept': 'application/json'
            }
            
            # Calculate date range
            from_date = (datetime.now() - timedelta(days=days_back)).strftime('%Y-%m-%dT%H:%M:%S.000Z')
            
            print(f"🔄 Fetching eBay returns/refunds from last {days_back} days...")
            
            # eBay Sell Fulfillment API v1 endpoint for orders
            url = f"{self.base_url}/sell/fulfillment/v1/order"
            
            params = {
                'filter': f'lastmodifieddate:[{from_date}..]',
                'limit': 200
            }
            
            all_returns = []
            offset = 0
            
            while True:
                params['offset'] = offset
                
                try:
                    response = requests.get(url, headers=headers, params=params, timeout=30)
                    
                    if response.status_code == 200:
                        data = response.json()
                        orders = data.get('orders', [])
                        
                        if not orders:
                            break
                        
                        print(f"  📄 Checking {len(orders)} orders (offset {offset})...")
                        
                        # Process each order for refunds
                        for order in orders:
                            order_id = order.get('legacyOrderId', order.get('orderId'))
                            payment_summary = order.get('paymentSummary', {})
                            cancel_status = order.get('cancelStatus', {})
                            
                            # Check for refunds in payment summary
                            refunds = payment_summary.get('refunds', [])
                            
                            if not refunds:
                                # Skip orders without refunds
                                continue
                            
                            # Get line items to extract detailed refund info
                            line_items = order.get('lineItems', [])
                            
                            for item in line_items:
                                item_refunds = item.get('refunds', [])
                                
                                if not item_refunds:
                                    continue
                                
                                # Extract item details
                                title = item.get('title', '')
                                sku = item.get('sku', '')
                                line_item_id = item.get('lineItemId', '')
                                quantity = int(item.get('quantity', 1))
                                
                                # Get the refund info
                                for refund in item_refunds:
                                    refund_date = refund.get('refundDate', '')
                                    refund_amount = float(refund.get('amount', {}).get('value', 0))
                                    
                                    # Check if this was a cancellation
                                    cancel_state = cancel_status.get('cancelState', 'NONE_REQUESTED')
                                    cancel_requests = cancel_status.get('cancelRequests', [])
                                    
                                    return_reason = 'Refund/Return'
                                    if cancel_state == 'CANCELED' and cancel_requests:
                                        cancel_req = cancel_requests[0]
                                        reason_code = cancel_req.get('cancelReason', '')
                                        return_reason = f"Cancelled: {reason_code}"
                                    
                                    all_returns.append({
                                        'return_id': line_item_id,
                                        'order_id': order_id,
                                        'item_id': sku or line_item_id,
                                        'title': title,
                                        'return_date': refund_date,
                                        'refund_amount': refund_amount,
                                        'original_shipping_cost': 0,  # Not separately available
                                        'return_shipping_cost': 0,
                                        'return_reason': return_reason,
                                        'status': 'refunded',
                                        'quantity': quantity
                                    })
                        
                        # Check if there are more results
                        total = data.get('total', 0)
                        if offset + len(orders) >= total:
                            break
                        
                        offset += len(orders)
                        time.sleep(0.5)  # Rate limiting
                        
                    elif response.status_code == 204:
                        break
                    else:
                        print(f"❌ Error fetching eBay orders: {response.status_code}")
                        print(f"   Response: {response.text[:200]}")
                        break
                        
                except Exception as e:
                    print(f"❌ Error in eBay request: {e}")
                    break
            
            print(f"\n✅ Retrieved {len(all_returns)} eBay returns/refunds")
            return all_returns
            
        except Exception as e:
            print(f"❌ Error in get_returns: {e}")
            import traceback
            traceback.print_exc()
            return []
    
    def sync_returns_to_db(self, days_back=90):
        """
        Sync eBay returns to sold.db returns table
        Matches returns with original orders when possible, but also includes
        unmatched returns so they appear in financial analytics
        """
        print("🔄 Starting eBay returns sync...")
        
        returns = self.get_returns(days_back=days_back)
        
        if not returns:
            print("ℹ️  No eBay returns to sync")
            return 0
        
        conn = sqlite3.connect('sold.db')
        cur = conn.cursor()
        
        synced_count = 0
        matched_count = 0
        unmatched_count = 0
        
        for return_data in returns:
            order_id = return_data['order_id']
            
            # Try to find original order in sold.db
            cur.execute('''
                SELECT id, barcode, title, price, shipping_cost, seller_fee, lot_number, location
                FROM orders
                WHERE order_id = ? AND store = 'ebay'
            ''', (order_id,))
            
            original_order = cur.fetchone()
            
            if original_order:
                # Matched - use data from original order
                original_order_id, barcode, orig_title, original_price, original_shipping, original_fee, lot_number, location = original_order
                
                # Use return title if original is missing
                title = orig_title or return_data['title']
                
                matched_count += 1
            else:
                # Unmatched - use data from return API
                original_order_id = None
                barcode = None
                title = return_data['title']
                original_price = return_data['refund_amount']  # Best estimate
                original_shipping = 0
                original_fee = 0
                lot_number = None
                location = None
                
                unmatched_count += 1
                print(f"  ⚠️  Unmatched return (no original order): {order_id} - ${return_data['refund_amount']:.2f}")
            
            # Calculate total return cost
            total_return_cost = (
                return_data['refund_amount'] +
                (original_shipping or 0) +
                return_data['return_shipping_cost']
            )
            
            # Insert or update return
            cur.execute('''
                INSERT OR REPLACE INTO returns (
                    original_order_id, order_id, item_id, barcode, title, quantity,
                    original_price, refund_amount, original_shipping_cost, return_shipping_cost,
                    original_seller_fee, seller_fee_refund,
                    return_date, store, return_reason, lot_number, location
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                original_order_id, order_id, return_data['item_id'], barcode, title,
                return_data['quantity'], original_price, return_data['refund_amount'],
                original_shipping, return_data['return_shipping_cost'],
                original_fee, 0,  # eBay doesn't provide fee refund separately
                return_data['return_date'], 'ebay', return_data['return_reason'],
                lot_number, location
            ))
            
            synced_count += 1
            if original_order:
                print(f"  ✅ {order_id}: ${total_return_cost:.2f} total return cost")
        
        conn.commit()
        conn.close()
        
        print(f"\n✅ Synced {synced_count} eBay returns to database")
        print(f"   • {matched_count} matched to original orders")
        print(f"   • {unmatched_count} without original order (still included for analytics)")
        return synced_count
    
    def sync_payouts_to_db(self, days_back=90):
        """
        Fetch eBay payout data and sync to payouts table
        Uses eBay Finances API to get payout summaries
        """
        try:
            token = self.get_access_token()
            if not token:
                print("❌ No eBay access token available")
                return 0
            
            headers = {
                'Authorization': f'Bearer {token}',
                'Content-Type': 'application/json',
                'Accept': 'application/json'
            }
            
            # Calculate date range
            filter_date = (datetime.now() - timedelta(days=days_back)).strftime("%Y-%m-%dT%H:%M:%S.000Z")
            
            print(f"🔄 Fetching eBay payouts from last {days_back} days...")
            
            # eBay Finances API endpoint for payouts (must use apiz.ebay.com)
            url = "https://apiz.ebay.com/sell/finances/v1/payout_summary"
            
            params = {
                'filter': f'payoutDate:[{filter_date}..]',
                'limit': 200
            }
            
            all_payouts = []
            
            # Paginate through results
            while True:
                response = requests.get(url, headers=headers, params=params, timeout=30)
                
                if response.status_code == 403:
                    print("⚠️ eBay Finances API access denied - need User OAuth token or proper permissions")
                    return 0
                elif response.status_code != 200:
                    print(f"❌ eBay API error: {response.status_code}")
                    print(f"Response: {response.text}")
                    return 0
                
                data = response.json()
                payouts = data.get('payouts', [])
                all_payouts.extend(payouts)
                
                print(f"  📄 Retrieved {len(payouts)} payouts")
                
                # Check for next page
                next_url = data.get('next')
                if not next_url:
                    break
                
                url = next_url
                params = {}  # Next URL has params embedded
                time.sleep(0.5)  # Rate limiting
            
            print(f"📊 Found {len(all_payouts)} total eBay payouts")
            
            # Connect to database
            conn = sqlite3.connect('sold.db')
            cur = conn.cursor()
            
            synced_count = 0
            
            for payout in all_payouts:
                payout_id = payout.get('payoutId')
                payout_date = payout.get('payoutDate')
                payout_status = payout.get('payoutStatus', 'UNKNOWN')
                
                # Get amount
                amount_obj = payout.get('amount', {})
                amount = float(amount_obj.get('value', 0))
                currency = amount_obj.get('currency', 'USD')
                
                # Skip $0 payouts
                if amount <= 0:
                    continue
                
                # eBay payouts are single-day, use payout_date for all date fields
                start_date = payout_date
                end_date = payout_date
                
                # Map eBay status to our status
                status = 'Closed' if payout_status == 'SUCCEEDED' else payout_status.title()
                
                # Insert or update payout
                cur.execute('''
                    INSERT INTO payouts (store, settlement_id, start_date, end_date, payout_date, amount, currency, status, synced_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                    ON CONFLICT(settlement_id) DO UPDATE SET
                        payout_date = excluded.payout_date,
                        amount = excluded.amount,
                        status = excluded.status,
                        synced_at = CURRENT_TIMESTAMP
                ''', ('ebay', payout_id, start_date, end_date, payout_date, amount, currency, status))
                
                synced_count += 1
                print(f"  ✅ {payout_id}: {currency} ${amount:.2f} ({status})")
            
            conn.commit()
            conn.close()
            
            print(f"\n✅ Synced {synced_count} eBay payouts to database")
            return synced_count
            
        except Exception as e:
            print(f"❌ Error syncing eBay payouts: {e}")
            import traceback
            traceback.print_exc()
            return 0


if __name__ == '__main__':
    # Test connection
    em = EbayManager()
    token = em.get_access_token()
    if token:
        print("✅ eBay API connection successful")
        # Test returns fetch
        returns = em.get_returns(days_back=30)
        print(f"Found {len(returns)} returns")
    else:
        print("❌ eBay API connection failed")
