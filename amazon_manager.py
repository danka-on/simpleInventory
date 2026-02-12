"""
Amazon SP-API Integration Module
Handles authentication and data fetching from Amazon Seller Central
"""

import json
import sqlite3
import time
import os
from datetime import datetime, timedelta
from sp_api.api import Orders, Reports, CatalogItems, ListingsItems, Finances, MerchantFulfillment
from sp_api.base import Marketplaces
from sp_api.base.exceptions import SellingApiException
from DBmanager import connect_db

# Define base directory for cross-platform compatibility
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

class AmazonManager:
    def __init__(self, credentials_path='amazon_credentials.json'):
        """Initialize Amazon SP-API manager with credentials"""
        # Ensure path is absolute
        if not os.path.isabs(credentials_path):
            credentials_path = os.path.join(BASE_DIR, credentials_path)
            
        with open(credentials_path, 'r') as f:
            self.creds = json.load(f)
        
        # Set up credentials for SP-API
        self.credentials = {
            'refresh_token': self.creds['refresh_token'],
            'lwa_app_id': self.creds['lwa_app_id'],
            'lwa_client_secret': self.creds.get('lwa_client_secret', ''),
        }
        
        # Optional AWS credentials if using role-based access
        if self.creds.get('aws_access_key') and self.creds.get('aws_secret_key'):
            self.credentials['aws_access_key'] = self.creds['aws_access_key']
            self.credentials['aws_secret_key'] = self.creds['aws_secret_key']
        
        # Marketplace configuration (default to US)
        self.marketplace = Marketplaces.US
        self.region = self.creds.get('region', 'us-east-1')
        
        # Rate limiting for Catalog API (max 2 requests per second to be safe)
        self.last_catalog_call_time = 0
        self.catalog_api_delay = 1.0  # 1 second between calls = 1 request/second
        
        # Rate limiting for Order Items API (even more conservative)
        self.last_order_items_call_time = 0
        self.order_items_api_delay = 1.0  # 1 second between calls = 1 request/second
    
    def get_orders(self, days_back=30, max_results=100):
        """
        Fetch recent orders from Amazon with pagination support
        
        Args:
            days_back: Number of days to look back for orders
            max_results: Maximum number of orders to retrieve PER PAGE (Amazon max: 100)
            
        Returns:
            List of order dictionaries
        """
        try:
            orders_api = Orders(credentials=self.credentials, marketplace=self.marketplace)
            
            # Calculate date range
            created_after = (datetime.utcnow() - timedelta(days=days_back)).isoformat()
            
            print(f"🔄 Fetching Amazon orders from last {days_back} days...")
            
            all_orders = []
            next_token = None
            page = 1
            
            # Paginate through all orders
            while True:
                # Fetch orders page
                if next_token:
                    response = orders_api.get_orders(NextToken=next_token)
                else:
                    response = orders_api.get_orders(
                        CreatedAfter=created_after,
                        MaxResultsPerPage=max_results
                    )
                
                if response.errors:
                    print(f"❌ Error fetching orders (page {page}): {response.errors}")
                    break
                
                orders = response.payload.get('Orders', [])
                all_orders.extend(orders)
                print(f"  📄 Page {page}: Retrieved {len(orders)} orders (total: {len(all_orders)})")
                
                # Check for next page
                next_token = response.payload.get('NextToken')
                if not next_token:
                    break  # No more pages
                
                page += 1
                
                # Rate limiting: Amazon allows 0.0167 requests/second (1 per minute) for Orders API
                # Wait 2 seconds between pagination calls to be safe
                import time
                time.sleep(2)
            
            print(f"✅ Retrieved {len(all_orders)} total orders from Amazon")
            return all_orders
            
        except SellingApiException as e:
            print(f"❌ Amazon API Error: {e}")
            return []
        except Exception as e:
            print(f"❌ Error fetching orders: {e}")
            return []
    
    def get_order_items(self, order_id):
        """
        Fetch line items for a specific order
        Rate limited to avoid QuotaExceeded errors.
        Includes retry logic with exponential backoff.
        
        Args:
            order_id: Amazon Order ID
            
        Returns:
            List of order items
        """
        max_retries = 3
        base_wait = 5  # Start with 5 second wait on quota error
        
        for attempt in range(max_retries):
            try:
                # Rate limiting: ensure we don't exceed Amazon's quota
                current_time = time.time()
                time_since_last_call = current_time - self.last_order_items_call_time
                
                if time_since_last_call < self.order_items_api_delay:
                    sleep_time = self.order_items_api_delay - time_since_last_call
                    time.sleep(sleep_time)
                
                self.last_order_items_call_time = time.time()
                
                orders_api = Orders(credentials=self.credentials, marketplace=self.marketplace)
                
                response = orders_api.get_order_items(order_id=order_id)
                
                if response.errors:
                    print(f"❌ Error fetching order items: {response.errors}")
                    return []
                
                items = response.payload.get('OrderItems', [])
                return items
                
            except SellingApiException as e:
                error_str = str(e)
                # Check if it's a quota error
                if 'QuotaExceeded' in error_str:
                    if attempt < max_retries - 1:
                        wait_time = base_wait * (2 ** attempt)  # Exponential backoff
                        print(f"⏳ Quota exceeded, waiting {wait_time}s before retry {attempt + 2}/{max_retries}...")
                        time.sleep(wait_time)
                        continue
                    else:
                        print(f"❌ Amazon API quota exhausted after {max_retries} attempts")
                        return []
                else:
                    print(f"❌ Amazon API Error: {e}")
                    return []
            except Exception as e:
                print(f"❌ Error fetching order items: {e}")
                return []
        
        return []
    
    def get_shipping_costs(self, order_ids):
        """
        Fetch actual shipping costs from Amazon Buy Shipping / MerchantFulfillment API
        Returns a dictionary mapping order_id to shipping cost
        """
        try:
            mf_api = MerchantFulfillment(credentials=self.credentials, marketplace=self.marketplace)
            shipping_costs = {}
            
            print(f"🚚 Fetching shipping costs for {len(order_ids)} orders...")
            
            for i, order_id in enumerate(order_ids, 1):
                try:
                    # Get shipment info for this order
                    response = mf_api.get_shipment(ShipmentId=order_id)
                    
                    if response.errors:
                        continue
                    
                    shipment = response.payload.get('Shipment', {})
                    shipping_service = shipment.get('ShippingService', {})
                    
                    # Get the shipping cost
                    rate = shipping_service.get('Rate', {})
                    amount = rate.get('Amount')
                    
                    if amount:
                        shipping_costs[order_id] = float(amount)
                        print(f"  ✅ {order_id}: ${float(amount):.2f}")
                    
                    # Rate limiting
                    if i % 10 == 0:
                        time.sleep(1)
                        
                except SellingApiException as e:
                    # Order might not have shipping purchased through Amazon
                    continue
                except Exception as e:
                    continue
            
            print(f"✅ Retrieved shipping costs for {len(shipping_costs)} orders")
            return shipping_costs
            
        except Exception as e:
            print(f"⚠️  Could not fetch shipping costs: {e}")
            return {}
    
    def get_financial_events(self, days_back=30):
        """
        Fetch financial events (fees, shipping, taxes) from Amazon
        Returns a dictionary mapping order_id to financial data
        """
        try:
            finances_api = Finances(credentials=self.credentials, marketplace=self.marketplace)
            
            # Calculate date range
            posted_after = (datetime.utcnow() - timedelta(days=days_back)).isoformat()
            
            print(f"🔄 Fetching Amazon financial events from last {days_back} days...")
            
            # Dictionary to store financial data by order ID
            financial_data = {}
            all_shipment_events = []
            all_adjustment_events = []
            next_token = None
            page = 1
            
            # Paginate through financial events
            while True:
                try:
                    if next_token:
                        response = finances_api.list_financial_events(NextToken=next_token)
                    else:
                        response = finances_api.list_financial_events(
                            PostedAfter=posted_after,
                            MaxResultsPerPage=100
                        )
                    
                    if response.errors:
                        print(f"❌ Error fetching financial events (page {page}): {response.errors}")
                        break
                    
                    payload = response.payload
                    financial_events = payload.get('FinancialEvents', {})
                    shipment_events = financial_events.get('ShipmentEventList', [])
                    adjustment_events = financial_events.get('AdjustmentEventList', [])
                    
                    # Collect all events across pages
                    all_shipment_events.extend(shipment_events)
                    all_adjustment_events.extend(adjustment_events)
                    
                    print(f"  📄 Page {page}: Retrieved {len(shipment_events)} shipment events, {len(adjustment_events)} adjustments")
                    
                    # Check for next page
                    next_token = payload.get('NextToken')
                    if not next_token:
                        break
                    
                    page += 1
                    time.sleep(1)  # Rate limiting
                    
                except SellingApiException as e:
                    if 'QuotaExceeded' in str(e):
                        print(f"⚠️  Rate limit hit, waiting 5 seconds...")
                        time.sleep(5)
                        continue
                    else:
                        print(f"❌ Error fetching financial events: {e}")
                        break
            
            # Process all shipment events
            print(f"\n💰 Processing {len(all_shipment_events)} shipment events...")
            for event in all_shipment_events:
                amazon_order_id = event.get('AmazonOrderId')
                if not amazon_order_id:
                    continue
                
                # Initialize data for this order
                if amazon_order_id not in financial_data:
                    financial_data[amazon_order_id] = {
                        'seller_fee': 0,
                        'shipping_cost': 0,
                        'taxes': 0,
                        'total_fees': 0
                    }
                
                # Extract shipment-level fees (ShippingLabel from Buy Shipping)
                # This is where USPS label costs appear when purchased through Amazon
                shipment_fees = event.get('ShipmentFeeList', [])
                for fee in shipment_fees:
                    fee_type = fee.get('FeeType', '')
                    fee_amount = float(fee.get('Amount', {}).get('CurrencyAmount', 0))
                    
                    # ShippingLabel is the USPS label cost paid through Amazon Buy Shipping
                    if fee_type == 'ShippingLabel':
                        shipping_cost = abs(fee_amount)
                        financial_data[amazon_order_id]['shipping_cost'] = shipping_cost
                        financial_data[amazon_order_id]['total_fees'] += shipping_cost
                        print(f"  ✅ {amazon_order_id}: ${shipping_cost:.2f} (USPS label via Buy Shipping)")
                    else:
                        # Add other shipment fees to total
                        financial_data[amazon_order_id]['total_fees'] += abs(fee_amount)
                
                # Extract item fees and charges
                item_list = event.get('ShipmentItemList', [])
                for item in item_list:
                    # Get all fee components
                    item_fees = item.get('ItemFeeList', [])
                    for fee in item_fees:
                        fee_type = fee.get('FeeType', '')
                        fee_amount = float(fee.get('FeeAmount', {}).get('CurrencyAmount', 0))
                        
                        # Sum up all fees (they're negative values)
                        financial_data[amazon_order_id]['total_fees'] += abs(fee_amount)
                        
                        # Track specific fee types
                        if fee_type in ['Commission', 'RefundCommission', 'ReferralFee']:
                            financial_data[amazon_order_id]['seller_fee'] += abs(fee_amount)
                    
                    # Get item tax - try multiple possible structures
                    item_tax_list = item.get('ItemTaxWithheldList', [])
                    if item_tax_list:
                        try:
                            for tax_item in item_tax_list:
                                if isinstance(tax_item, dict):
                                    # Try TaxesWithheld field
                                    if 'TaxesWithheld' in tax_item:
                                        for tax_component in tax_item.get('TaxesWithheld', []):
                                            if isinstance(tax_component, dict):
                                                tax_amount = float(tax_component.get('ChargeAmount', {}).get('CurrencyAmount', 0))
                                                financial_data[amazon_order_id]['taxes'] += abs(tax_amount)
                                    # Try ChargeComponent field
                                    elif 'ChargeComponent' in tax_item:
                                        for charge in tax_item.get('ChargeComponent', []):
                                            if isinstance(charge, dict):
                                                tax_amount = float(charge.get('ChargeAmount', {}).get('CurrencyAmount', 0))
                                                financial_data[amazon_order_id]['taxes'] += abs(tax_amount)
                        except Exception as tax_err:
                            # Tax parsing failed, skip silently (we get tax from Orders API anyway)
                            pass
            
            # Process PostageBilling_Postage adjustments (actual USPS label costs)
            print(f"\n📦 Processing {len(all_adjustment_events)} adjustment events for shipping costs...")
            
            # Collect all PostageBilling_Postage with timestamps
            from datetime import datetime as dt
            postage_billings = []
            for adj in all_adjustment_events:
                adj_type = adj.get('AdjustmentType', '')
                if adj_type == 'PostageBilling_Postage':
                    posted_date = adj.get('PostedDate', '')
                    amount = float(adj.get('AdjustmentAmount', {}).get('CurrencyAmount', 0))
                    if amount < 0:  # Costs are negative
                        try:
                            timestamp = dt.fromisoformat(posted_date.replace('Z', '+00:00'))
                            postage_billings.append({
                                'timestamp': timestamp,
                                'amount': abs(amount),
                                'used': False
                            })
                        except:
                            pass
            
            # Sort by timestamp
            postage_billings.sort(key=lambda x: x['timestamp'])
            
            # Create list of orders with timestamps
            order_list = []
            for event in all_shipment_events:
                order_id = event.get('AmazonOrderId')
                posted_date = event.get('PostedDate', '')
                if order_id and posted_date:
                    try:
                        timestamp = dt.fromisoformat(posted_date.replace('Z', '+00:00'))
                        order_list.append({
                            'order_id': order_id,
                            'timestamp': timestamp
                        })
                    except:
                        pass
            
            # Sort orders by timestamp
            order_list.sort(key=lambda x: x['timestamp'])
            
            # Match each order to the nearest unused postage billing within 48 hours
            shipping_matched = 0
            for order in order_list:
                order_id = order['order_id']
                order_time = order['timestamp']
                
                # Find closest unused postage billing (before or after, within 48 hours)
                best_match = None
                best_diff = None
                
                for pb in postage_billings:
                    if pb['used']:
                        continue
                    
                    time_diff = abs((pb['timestamp'] - order_time).total_seconds())
                    
                    # Within 48 hours (172800 seconds)
                    if time_diff <= 172800:
                        if best_diff is None or time_diff < best_diff:
                            best_diff = time_diff
                            best_match = pb
                
                if best_match:
                    best_match['used'] = True
                    shipping_cost = best_match['amount']
                    financial_data[order_id]['shipping_cost'] = shipping_cost
                    financial_data[order_id]['total_fees'] += shipping_cost
                    shipping_matched += 1
                    hours_diff = best_diff / 3600
                    print(f"  ✅ {order_id}: ${shipping_cost:.2f} (±{hours_diff:.1f}h match)")
            
            print(f"\n✅ Retrieved financial data for {len(financial_data)} orders")
            print(f"   Orders with shipping costs: {shipping_matched}/{len(financial_data)}")
            return financial_data
            
        except Exception as e:
            print(f"❌ Error in get_financial_events: {e}")
            import traceback
            traceback.print_exc()
            return {}
    
    def sync_orders_to_db(self, days_back=30, financial_days_back=None):
        """
        Fetch orders from Amazon and sync to local sold.db
        Similar to eBay sold order handling
        
        Args:
            days_back: How many days of orders to fetch (default 30)
            financial_days_back: How many days of financial events to fetch
                               (default: same as days_back, but can be extended 
                                to capture older shipping label purchases)
        
        Returns:
            Number of orders synced
        """
        print("🔄 Starting Amazon order sync...")
        
        # If financial_days_back not specified, use same as orders
        if financial_days_back is None:
            financial_days_back = days_back
        
        orders = self.get_orders(days_back=days_back)
        
        if not orders:
            print("ℹ️ No orders to sync")
            return 0
        
        # Connect to sold.db (same as eBay uses)
        with connect_db('sold.db') as conn:
            cur = conn.cursor()

            # Ensure table exists with necessary columns
            cur.execute('''
                CREATE TABLE IF NOT EXISTS orders (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    order_id TEXT UNIQUE,
                    item_id TEXT,
                    barcode TEXT,
                    title TEXT,
                    quantity INTEGER,
                    price REAL,
                    shipped_time TEXT,
                    paid_time TEXT,
                    image TEXT,
                    rackupdated INTEGER DEFAULT 0,
                    removal_cancelled INTEGER DEFAULT 0,
                    store TEXT DEFAULT 'amazon'
                )
            ''')

            # Add store column if it doesn't exist
            try:
                cur.execute('ALTER TABLE orders ADD COLUMN store TEXT DEFAULT "amazon"')
                conn.commit()
            except Exception:
                pass

            synced_count = 0
            order_items_api_calls = 0
            orders_with_actual_fees = 0
            orders_with_estimated_fees = 0
            orders_with_shipping_costs = 0

            # Fetch financial events (fees, shipping, taxes) for these orders
            print(f"\n📊 Fetching financial data (fees, shipping, taxes) - {financial_days_back} day window...")
            financial_data = self.get_financial_events(days_back=financial_days_back)

            # Get all order IDs to fetch shipping costs
            order_ids = [order.get('AmazonOrderId') for order in orders]

            # Fetch shipping costs from Buy Shipping API
            print("\n🚚 Fetching shipping costs from Buy Shipping API...")
            shipping_costs = self.get_shipping_costs(order_ids)

            for order in orders:
                try:
                    amazon_order_id = order.get('AmazonOrderId')
                    purchase_date = order.get('PurchaseDate')
                    last_update_date = order.get('LastUpdateDate')
                    order_status = order.get('OrderStatus')

                    # Extract shipping address (limited by Amazon API - no name or street)
                    shipping_address = order.get('ShippingAddress', {})
                    shipping_city = shipping_address.get('City', '')
                    shipping_state = shipping_address.get('StateOrRegion', '')
                    shipping_postal = shipping_address.get('PostalCode', '')
                    shipping_country = shipping_address.get('CountryCode', '')

                    # Amazon doesn't provide buyer name or street address for privacy
                    # We'll use "Amazon Buyer" as placeholder for shipping_name
                    shipping_name = "Amazon Buyer"

                    # Skip pending/cancelled orders
                    if order_status in ['Pending', 'Canceled']:
                        continue

                    # Get order items
                    order_items_api_calls += 1
                    items = self.get_order_items(amazon_order_id)

                    for item in items:
                        asin = item.get('ASIN')
                        sku = item.get('SellerSKU')
                        title = item.get('Title')
                        quantity = item.get('QuantityOrdered', 1)
                        price = float(item.get('ItemPrice', {}).get('Amount', 0))

                        # Get actual financial data from Financial Events API
                        order_financial_data = financial_data.get(amazon_order_id, {})

                        # Get actual shipping cost - Priority order:
                        # 1. From Financial Events (PostageBilling_Postage adjustments)
                        # 2. From Buy Shipping API
                        # 3. From Orders API (customer's charge - often $0 for free shipping)
                        shipping_cost = 0
                        if order_financial_data and order_financial_data.get('shipping_cost', 0) > 0:
                            shipping_cost = order_financial_data.get('shipping_cost', 0)
                            orders_with_shipping_costs += 1
                        elif shipping_costs.get(amazon_order_id, 0) > 0:
                            shipping_cost = shipping_costs.get(amazon_order_id, 0)
                            orders_with_shipping_costs += 1
                        else:
                            # Fallback to Orders API
                            shipping_price = item.get('ShippingPrice', {})
                            shipping_cost = float(shipping_price.get('Amount', 0)) if shipping_price else 0

                        # Use actual fees and taxes from Financial Events API (or fallback to estimates)
                        if order_financial_data:
                            seller_fee = order_financial_data.get('seller_fee', 0)
                            # Use Financial Events tax if available, otherwise Orders API
                            taxes = order_financial_data.get('taxes', 0)
                            if taxes == 0:
                                item_tax = item.get('ItemTax', {})
                                taxes = float(item_tax.get('Amount', 0)) if item_tax else 0
                            orders_with_actual_fees += 1
                            print(f"  💰 Using actual financial data for {amazon_order_id}: Fee=${seller_fee:.2f}, Ship=${shipping_cost:.2f}, Tax=${taxes:.2f}")
                        else:
                            # Fallback to estimates for fees
                            seller_fee = price * 0.15

                            # Get tax from Orders API
                            item_tax = item.get('ItemTax', {})
                            taxes = float(item_tax.get('Amount', 0)) if item_tax else 0

                            orders_with_estimated_fees += 1
                            print(f"  ⚠️  Using estimated fees for {amazon_order_id}: Fee=${seller_fee:.2f} (15% est), Ship=${shipping_cost:.2f}, Tax=${taxes:.2f}")

                        # Try to find barcode and image from amazonStore.db
                        barcode = None
                        image = None
                        try:
                            with connect_db('amazonStore.db') as store_conn:
                                store_cur = store_conn.cursor()
                                store_cur.execute('SELECT UPC, IMAGE FROM ITEMS WHERE ASIN = ? OR SKU = ?', (asin, sku))
                                result = store_cur.fetchone()
                                if result:
                                    potential_barcode = result[0]
                                    # Only use this barcode if it's NOT an ASIN format
                                    # ASIN format: starts with B0 and is 10 characters (e.g., B0XXXXXXXXX)
                                    if potential_barcode and not (str(potential_barcode).startswith('B0') and len(str(potential_barcode)) == 10):
                                        barcode = potential_barcode
                                    # Get image from amazonStore if available
                                    if result[1] and str(result[1]).lower() not in ['none', 'null', '']:
                                        image = result[1]
                        except Exception:
                            pass

                        # Fallback to rawbol.db if we have barcode but no image yet
                        if barcode and not image:
                            try:
                                with connect_db('rawbol.db') as rawbol_conn:
                                    rawbol_cur = rawbol_conn.cursor()

                                    # Try exact match first
                                    rawbol_cur.execute('SELECT image_url FROM raw_bol_items WHERE upc = ?', (barcode,))
                                    result = rawbol_cur.fetchone()

                                    # If not found and barcode has leading zeros, try without them
                                    if not result and barcode.startswith('0'):
                                        barcode_no_zero = barcode.lstrip('0') if str(barcode).isdigit() else barcode
                                        rawbol_cur.execute('SELECT image_url FROM raw_bol_items WHERE upc = ?', (barcode_no_zero,))
                                        result = rawbol_cur.fetchone()

                                    if result and result[0]:
                                        # Skip 'nan' values
                                        img_val = str(result[0])
                                        if img_val.lower() not in ['nan', 'none', 'null', '']:
                                            image = img_val
                            except Exception:
                                pass

                        # Determine shipped time
                        shipped_time = None
                        if order_status == 'Shipped':
                            shipped_time = last_update_date

                        # Check if order already exists
                        cur.execute('SELECT id, barcode FROM orders WHERE order_id = ?', (amazon_order_id,))
                        existing = cur.fetchone()

                        if existing:
                            existing_barcode = existing[1]

                            # Determine which barcode to use:
                            # - If we found a new valid barcode (UPC), use it
                            # - If no new barcode found but existing has one, keep existing
                            # - If existing is an ASIN and we have nothing better, keep existing
                            final_barcode = barcode if barcode else existing_barcode

                            # Update existing order
                            cur.execute('''
                                UPDATE orders
                                SET barcode = ?, title = ?, quantity = ?, price = ?,
                                    shipped_time = ?, paid_time = ?, image = ?, store = 'amazon',
                                    shipping_name = ?, shipping_city = ?, shipping_state = ?,
                                    shipping_postal_code = ?, shipping_country = ?,
                                    shipping_cost = ?, seller_fee = ?, taxes = ?
                                WHERE order_id = ?
                            ''', (final_barcode, title, quantity, price, shipped_time, purchase_date, image,
                                  shipping_name, shipping_city, shipping_state, shipping_postal, shipping_country,
                                  shipping_cost, seller_fee, taxes,
                                  amazon_order_id))
                        else:
                            # Insert new order
                            cur.execute('''
                                INSERT INTO orders
                                (order_id, item_id, barcode, title, quantity, price, shipped_time, paid_time, image, store,
                                 shipping_name, shipping_city, shipping_state, shipping_postal_code, shipping_country,
                                 shipping_cost, seller_fee, taxes)
                                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'amazon', ?, ?, ?, ?, ?, ?, ?, ?)
                            ''', (amazon_order_id, asin, barcode, title, quantity, price, shipped_time, purchase_date, image,
                                  shipping_name, shipping_city, shipping_state, shipping_postal, shipping_country,
                                  shipping_cost, seller_fee, taxes))
                            synced_count += 1

                    conn.commit()

                except Exception as e:
                    print(f"⚠️ Error syncing order {amazon_order_id}: {e}")
                    continue
        print(f"✅ Synced {synced_count} Amazon orders to database")
        if order_items_api_calls > 0:
            print(f"   Order Items API calls: {order_items_api_calls} (rate limited)")
        print(f"\n💰 Financial Data Summary:")
        print(f"   Orders with actual fees: {orders_with_actual_fees}")
        print(f"   Orders with estimated fees: {orders_with_estimated_fees}")
        print(f"   Orders with shipping costs: {orders_with_shipping_costs}")
        if orders_with_actual_fees > 0:
            print(f"   ✅ {orders_with_actual_fees}/{orders_with_actual_fees + orders_with_estimated_fees} orders have accurate Amazon fee data!")
        if orders_with_shipping_costs > 0:
            print(f"   ✅ {orders_with_shipping_costs}/{len(orders)} orders have actual shipping cost data!")
        return synced_count
    
    def get_active_listings(self):
        """
        Fetch active listings from Amazon using Reports API with quota protection
        Returns list of inventory items or None if quota exceeded
        """
        try:
            print("🔄 Fetching active Amazon listings...")
            
            from sp_api.api import Reports
            reports_api = Reports(credentials=self.credentials, marketplace=self.marketplace)
            
            # Request an inventory report
            # ReportType: GET_MERCHANT_LISTINGS_ALL_DATA gets all active listings
            try:
                response = reports_api.create_report(
                    reportType='GET_MERCHANT_LISTINGS_ALL_DATA'
                )
            except SellingApiException as e:
                error_str = str(e)
                if 'QuotaExceeded' in error_str or 'Throttled' in error_str:
                    print("⚠️ Amazon Listings Report quota exceeded - will retry later")
                    print("   Quota typically resets after 24 hours")
                    return None  # Signal quota exceeded
                raise
            
            if response.errors:
                print(f"❌ Error creating report: {response.errors}")
                return []
            
            report_id = response.payload.get('reportId')
            print(f"📄 Report requested: {report_id}")
            print("⏳ Waiting for report to be generated (this may take 1-2 minutes)...")
            
            # Poll for report completion with rate limiting
            import time
            max_attempts = 60  # 5 minutes max
            for attempt in range(max_attempts):
                time.sleep(5)  # Wait 5 seconds between checks (rate limiting)
                
                try:
                    status_response = reports_api.get_report(report_id)
                except SellingApiException as e:
                    error_str = str(e)
                    if 'QuotaExceeded' in error_str or 'Throttled' in error_str:
                        print("⚠️ Quota exceeded while checking report status - waiting longer...")
                        time.sleep(10)  # Wait longer and retry
                        continue
                    raise
                
                if status_response.errors:
                    print(f"❌ Error checking report status: {status_response.errors}")
                    return []
                
                processing_status = status_response.payload.get('processingStatus')
                
                if processing_status == 'DONE':
                    print("✅ Report ready!")
                    
                    # Get the report document with rate limiting
                    document_id = status_response.payload.get('reportDocumentId')
                    time.sleep(2)  # Rate limit before downloading
                    
                    try:
                        doc_response = reports_api.get_report_document(document_id, download=True)
                    except SellingApiException as e:
                        error_str = str(e)
                        if 'QuotaExceeded' in error_str or 'Throttled' in error_str:
                            print("⚠️ Quota exceeded while downloading report - will retry later")
                            return None
                        raise
                    
                    if doc_response.errors:
                        print(f"❌ Error downloading report: {doc_response.errors}")
                        return []
                    
                    # The payload contains the actual report content
                    report_content = doc_response.payload
                    
                    # Parse the report (TSV format)
                    listings = self._parse_inventory_report(report_content)
                    print(f"✅ Retrieved {len(listings)} active listings")
                    return listings
                    
                elif processing_status in ['CANCELLED', 'FATAL']:
                    print(f"❌ Report generation failed: {processing_status}")
                    return []
                
                if attempt % 6 == 0:  # Print status every 30 seconds
                    print(f"⏳ Still waiting... ({processing_status})")
            
            print("❌ Report generation timed out")
            return []
            
        except SellingApiException as e:
            error_str = str(e)
            if 'QuotaExceeded' in error_str or 'Throttled' in error_str:
                print("⚠️ Amazon Listings API quota exceeded")
                print("   Your quota will reset in approximately 24 hours")
                return None
            print(f"❌ Amazon API Error: {e}")
            return []
        except Exception as e:
            print(f"❌ Error fetching listings: {e}")
            import traceback
            traceback.print_exc()
            return []
    
    def _parse_inventory_report(self, report_data):
        """Parse Amazon inventory report TSV data"""
        try:
            import csv
            import io
            
            listings = []
            
            # Handle different payload formats
            if isinstance(report_data, dict):
                # Sometimes the response contains a 'document' or 'url' key
                if 'document' in report_data:
                    report_data = report_data['document']
                elif 'url' in report_data:
                    # Need to download from URL
                    import requests
                    response = requests.get(report_data['url'])
                    report_data = response.text
                else:
                    # Try to get the actual content
                    print(f"⚠️ Unexpected dict format: {list(report_data.keys())}")
                    return []
            
            # Convert bytes to string if needed
            if isinstance(report_data, bytes):
                report_data = report_data.decode('utf-8')
            
            if not isinstance(report_data, str):
                print(f"⚠️ Unexpected data type: {type(report_data)}")
                return []
            
            # Parse TSV
            reader = csv.DictReader(io.StringIO(report_data), delimiter='\t')
            
            for row in reader:
                listing = {
                    'asin': row.get('asin1', ''),
                    'sku': row.get('seller-sku', ''),
                    'title': row.get('item-name', ''),
                    'price': row.get('price', ''),
                    'quantity': row.get('quantity', ''),
                    'status': row.get('status', ''),
                    'image': row.get('image-url', ''),
                    'upc': row.get('product-id', ''),  # This is the UPC/barcode
                    'product_id_type': row.get('product-id-type', ''),
                    'condition': row.get('item-condition', ''),
                    'fulfillment_channel': row.get('fulfillment-channel', ''),
                }
                listings.append(listing)
            
            return listings
            
        except Exception as e:
            print(f"❌ Error parsing report: {e}")
            import traceback
            traceback.print_exc()
            return []
    
    def sync_listings_to_db(self):
        """
        Fetch active listings from Amazon and sync to amazonStore.db
        Similar to eBay's ebayStore.db
        With quota protection and smart syncing
        
        Returns:
            Number of listings synced, or -1 if quota exceeded
        """
        print("🔄 Starting Amazon listings sync...\n")
        
        # Connect to amazonStore.db first to check last sync
        with connect_db('amazonStore.db') as conn:
            cur = conn.cursor()

            # Create table if it doesn't exist
            cur.execute('''
                CREATE TABLE IF NOT EXISTS ITEMS (
                    ID INTEGER PRIMARY KEY AUTOINCREMENT,
                    ASIN TEXT UNIQUE,
                    SKU TEXT,
                    TITLE TEXT,
                    PRICE REAL,
                    QUANTITY INTEGER,
                    STATUS TEXT,
                    IMAGE TEXT,
                    UPC TEXT,
                    CONDITION TEXT,
                    FULFILLMENT_CHANNEL TEXT,
                    LAST_UPDATED TEXT,
                    upc_fetch_attempted INTEGER DEFAULT 0,
                    upc_last_fetch_date TEXT
                )
            ''')

            # Create sync metadata table to track quota usage
            cur.execute('''
                CREATE TABLE IF NOT EXISTS sync_metadata (
                    key TEXT PRIMARY KEY,
                    value TEXT,
                    updated_at TEXT
                )
            ''')
            conn.commit()

            # Check last successful sync time to avoid unnecessary API calls
            cur.execute('SELECT value, updated_at FROM sync_metadata WHERE key = ?', ('last_listings_sync',))
            last_sync_row = cur.fetchone()

            if last_sync_row:
                last_sync_time = datetime.fromisoformat(last_sync_row[1])
                time_since_sync = (datetime.now() - last_sync_time).total_seconds() / 3600  # hours

                # Only sync if it's been more than 4 hours (reduce quota usage)
                if time_since_sync < 4:
                    print(f"ℹ️ Last sync was {time_since_sync:.1f} hours ago - skipping to preserve quota")
                    print(f"   Next sync available in {4 - time_since_sync:.1f} hours")
                    return 0

            # Fetch listings from Amazon
            listings = self.get_active_listings()

            # Handle quota exceeded
            if listings is None:
                print("⚠️ Amazon listings sync skipped due to quota limits")
                # Record quota exceeded event
                cur.execute('''
                    INSERT OR REPLACE INTO sync_metadata (key, value, updated_at)
                    VALUES (?, ?, ?)
                ''', ('last_quota_exceeded', 'listings_report', datetime.utcnow().isoformat()))
                conn.commit()
                return -1  # Signal quota exceeded

            if not listings:
                print("ℹ️ No listings to sync")
                return 0
        
            synced_count = 0
            updated_count = 0
            skipped_count = 0
            catalog_api_calls = 0  # Track Catalog API usage

            for listing in listings:
                try:
                    asin = listing.get('asin', '').strip()
                    sku = listing.get('sku', '').strip()

                    if not asin:
                        continue

                    # Parse price and quantity
                    try:
                        price = float(listing.get('price', 0) or 0)
                    except Exception:
                        price = 0.0

                    try:
                        quantity = int(listing.get('quantity', 0) or 0)
                    except Exception:
                        quantity = 0

                    # Get UPC - from product-id column
                    upc_from_report = listing.get('upc', '').strip()

                    # UPC PRESERVATION: Check existing UPC before overwriting
                    # If we already have a valid UPC (not ASIN, status=3), preserve it
                    cur.execute('SELECT UPC, upc_fetch_attempted FROM ITEMS WHERE ASIN = ?', (asin,))
                    existing_item = cur.fetchone()
                    existing_upc = existing_item[0] if existing_item else None
                    existing_status = existing_item[1] if existing_item and len(existing_item) > 1 else None

                    # Determine final UPC to use:
                    # 1. If existing UPC is valid (not ASIN and successfully fetched), keep it
                    # 2. Otherwise, use report UPC if it's valid (not ASIN)
                    # 3. Otherwise, keep existing UPC or fallback to ASIN
                    if existing_upc and existing_upc != asin and existing_status == 3:
                        upc = existing_upc
                    elif upc_from_report and upc_from_report != asin:
                        upc = upc_from_report
                    elif existing_upc:
                        upc = existing_upc
                    else:
                        upc = asin

                    # Get image: Try Amazon Catalog API first, then fallback to rawbol.db
                    image_url = listing.get('image', '')
                    if not image_url:
                        try:
                            catalog_api_calls += 1
                            catalog_data = self.get_catalog_item(asin)
                            if catalog_data and 'images' in catalog_data:
                                for image_group in catalog_data['images']:
                                    if 'images' in image_group:
                                        for img in image_group['images']:
                                            if img.get('variant') == 'MAIN' and img.get('height', 0) >= 500:
                                                image_url = img.get('link', '')
                                                break
                                        if image_url:
                                            break
                        except Exception as e:
                            print(f"Could not fetch Amazon image for {asin}: {e}")

                    # Fallback to rawbol.db if still no image and we have a UPC
                    if upc and not image_url:
                        try:
                            with connect_db('rawbol.db') as rawbol_conn:
                                rawbol_cur = rawbol_conn.cursor()

                                rawbol_cur.execute("SELECT image_url FROM raw_bol_items WHERE upc = ?", (upc,))
                                rawbol_row = rawbol_cur.fetchone()

                                if not rawbol_row and upc.startswith('0'):
                                    upc_no_zero = upc.lstrip('0') if str(upc).isdigit() else upc
                                    rawbol_cur.execute("SELECT image_url FROM raw_bol_items WHERE upc = ?", (upc_no_zero,))
                                    rawbol_row = rawbol_cur.fetchone()

                                if rawbol_row and rawbol_row[0]:
                                    img_val = str(rawbol_row[0])
                                    if img_val.lower() not in ['nan', 'none', '', 'null']:
                                        image_url = img_val
                        except Exception as e:
                            print(f"Could not lookup image for UPC {upc}: {e}")

                    # Check if listing exists
                    cur.execute('SELECT ID FROM ITEMS WHERE ASIN = ?', (asin,))
                    existing = cur.fetchone()

                    current_time = datetime.utcnow().isoformat() + 'Z'

                    if existing:
                        cur.execute('''
                            UPDATE ITEMS
                            SET SKU = ?, TITLE = ?, PRICE = ?, QUANTITY = ?,
                                STATUS = ?, IMAGE = ?, UPC = ?, CONDITION = ?,
                                FULFILLMENT_CHANNEL = ?, LAST_UPDATED = ?
                            WHERE ASIN = ?
                        ''', (
                            sku, listing.get('title', ''), price, quantity,
                            listing.get('status', ''), image_url,
                            upc, listing.get('condition', ''),
                            listing.get('fulfillment_channel', ''), current_time,
                            asin
                        ))
                        updated_count += 1
                    else:
                        cur.execute('''
                            INSERT INTO ITEMS
                            (ASIN, SKU, TITLE, PRICE, QUANTITY, STATUS, IMAGE, UPC, CONDITION, FULFILLMENT_CHANNEL, LAST_UPDATED)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        ''', (
                            asin, sku, listing.get('title', ''), price, quantity,
                            listing.get('status', ''), image_url,
                            upc, listing.get('condition', ''),
                            listing.get('fulfillment_channel', ''), current_time
                        ))
                        synced_count += 1

                    conn.commit()

                except Exception as e:
                    print(f"Error syncing listing {listing.get('asin')}: {e}")
                    continue
        
            # Record successful sync
            cur.execute('''
                INSERT OR REPLACE INTO sync_metadata (key, value, updated_at)
                VALUES (?, ?, ?)
            ''', ('last_listings_sync', str(synced_count + updated_count), datetime.utcnow().isoformat()))
            conn.commit()

        print(f"\n✅ Amazon listings sync complete:")
        print(f"   - New: {synced_count} items")
        print(f"   - Updated: {updated_count} items")
        if skipped_count > 0:
            print(f"   - Skipped (unchanged): {skipped_count} items")
        print(f"   - Total: {synced_count + updated_count} items")
        if catalog_api_calls > 0:
            print(f"   - Catalog API calls: {catalog_api_calls} (rate limited)")
        
        return synced_count + updated_count

    def get_catalog_item(self, asin: str):
        """Fetch a single catalog item (2022-04-01) and return payload dict.
        Includes attributes and identifiers so we can extract UPC/EAN.
        Rate limited to avoid QuotaExceeded errors with retry logic.
        """
        max_retries = 3
        base_wait = 5  # Start with 5 second wait on quota error
        
        for attempt in range(max_retries):
            try:
                # Rate limiting: ensure we don't exceed Amazon's quota
                current_time = time.time()
                time_since_last_call = current_time - self.last_catalog_call_time
                
                if time_since_last_call < self.catalog_api_delay:
                    sleep_time = self.catalog_api_delay - time_since_last_call
                    time.sleep(sleep_time)
                
                self.last_catalog_call_time = time.time()
                
                ci = CatalogItems(credentials=self.credentials, marketplace=self.marketplace)
                # Some library versions prefer marketplaceIds param
                try:
                    resp = ci.get_catalog_item(
                        asin=asin,
                        marketplaceIds=[self.marketplace.marketplace_id],
                        includedData=["attributes", "identifiers", "images", "summaries"],
                    )
                except TypeError:
                    # Fallback signature without keyword args in some versions
                    resp = ci.get_catalog_item(
                        asin,
                        marketplaceIds=[self.marketplace.marketplace_id],
                        includedData=["attributes", "identifiers", "images", "summaries"],
                    )
                return resp.payload if hasattr(resp, 'payload') else resp
                
            except SellingApiException as e:
                error_str = str(e)
                # Check if it's a quota error
                if 'QuotaExceeded' in error_str:
                    if attempt < max_retries - 1:
                        wait_time = base_wait * (2 ** attempt)  # Exponential backoff: 5s, 10s, 20s
                        print(f"⏳ Catalog quota exceeded for {asin}, waiting {wait_time}s before retry {attempt + 2}/{max_retries}...")
                        time.sleep(wait_time)
                        continue
                    else:
                        print(f"❌ Catalog API quota exhausted for {asin} after {max_retries} attempts")
                        return None
                else:
                    print(f"❌ Amazon Catalog API error for {asin}: {e}")
                    return None
            except Exception as e:
                print(f"❌ Error fetching catalog item {asin}: {e}")
                return None
        
        return None
    
    def get_inventory_summary(self):
        """
        Get inventory summary from Amazon
        This requires additional API permissions
        """
        # This would use the FBA Inventory API
        # For now, placeholder for future implementation
        print("ℹ️ Inventory summary API not yet implemented")
        return []
    
    def test_connection(self):
        """Test if credentials are working"""
        try:
            print("🔍 Testing Amazon SP-API connection...")
            orders_api = Orders(credentials=self.credentials, marketplace=self.marketplace)
            
            # Try to fetch just 1 order from last 7 days
            created_after = (datetime.utcnow() - timedelta(days=7)).isoformat()
            response = orders_api.get_orders(
                CreatedAfter=created_after,
                MaxResultsPerPage=1
            )
            
            if response.errors:
                print(f"❌ Connection failed: {response.errors}")
                return False
            
            print("✅ Amazon SP-API connection successful!")
            print(f"   Marketplace: {self.marketplace.marketplace_id}")
            print(f"   Region: {self.region}")
            return True
            
        except SellingApiException as e:
            print(f"❌ API Error: {e}")
            return False
        except Exception as e:
            print(f"❌ Connection Error: {e}")
            return False
    
    def get_returns(self, days_back=90):
        """
        Fetch returns from Amazon Financial Events API (RefundEventList)
        Returns list of return items with financial details
        """
        try:
            finances_api = Finances(credentials=self.credentials, marketplace=self.marketplace)
            
            posted_after = (datetime.utcnow() - timedelta(days=days_back)).isoformat()
            
            print(f"🔄 Fetching Amazon returns from last {days_back} days...")
            
            all_refund_events = []
            next_token = None
            page = 1
            
            while True:
                try:
                    if next_token:
                        response = finances_api.list_financial_events(NextToken=next_token)
                    else:
                        response = finances_api.list_financial_events(
                            PostedAfter=posted_after,
                            MaxResultsPerPage=100
                        )
                    
                    if response.errors:
                        print(f"❌ Error fetching returns (page {page}): {response.errors}")
                        break
                    
                    payload = response.payload
                    financial_events = payload.get('FinancialEvents', {})
                    refund_events = financial_events.get('RefundEventList', [])
                    
                    all_refund_events.extend(refund_events)
                    
                    print(f"  📄 Page {page}: Retrieved {len(refund_events)} refund events")
                    
                    next_token = payload.get('NextToken')
                    if not next_token:
                        break
                    
                    page += 1
                    time.sleep(1)
                    
                except SellingApiException as e:
                    if 'QuotaExceeded' in str(e):
                        print(f"⚠️  Rate limit hit, waiting 5 seconds...")
                        time.sleep(5)
                        continue
                    else:
                        print(f"❌ Error fetching returns: {e}")
                        break
            
            print(f"\n✅ Retrieved {len(all_refund_events)} return events")
            
            # Process return events to extract key data
            returns_data = []
            
            for event in all_refund_events:
                amazon_order_id = event.get('AmazonOrderId')
                posted_date = event.get('PostedDate')
                
                # Get item details from ShipmentItemAdjustmentList
                items = event.get('ShipmentItemAdjustmentList', [])
                
                for item in items:
                    order_item_id = item.get('OrderItemId')
                    seller_sku = item.get('SellerSKU', '')
                    quantity_shipped = item.get('QuantityShipped', 0)
                    
                    # Extract refund amount from ItemChargeAdjustmentList
                    refund_amount = 0
                    item_charges = item.get('ItemChargeAdjustmentList', [])
                    for charge in item_charges:
                        charge_type = charge.get('ChargeType', '')
                        if charge_type == 'Principal':  # Main refund amount
                            amount = charge.get('ChargeAmount', {}).get('CurrencyAmount', 0)
                            refund_amount += abs(float(amount))
                    
                    # Extract fees refunded
                    seller_fee_refund = 0
                    item_fees = item.get('ItemFeeAdjustmentList', [])
                    for fee in item_fees:
                        fee_type = fee.get('FeeType', '')
                        if fee_type in ['Commission', 'RefundCommission', 'ReferralFee']:
                            fee_amount = fee.get('FeeAmount', {}).get('CurrencyAmount', 0)
                            seller_fee_refund += abs(float(fee_amount))
                    
                    # Extract return shipping cost (if seller paid for return label)
                    return_shipping_cost = 0
                    for charge in item_charges:
                        charge_type = charge.get('ChargeType', '')
                        if charge_type in ['Shipping', 'ShippingCharge']:
                            amount = charge.get('ChargeAmount', {}).get('CurrencyAmount', 0)
                            return_shipping_cost += abs(float(amount))
                    
                    returns_data.append({
                        'order_id': amazon_order_id,
                        'item_id': order_item_id,
                        'seller_sku': seller_sku,
                        'return_date': posted_date,
                        'quantity': quantity_shipped,
                        'refund_amount': refund_amount,
                        'seller_fee_refund': seller_fee_refund,
                        'return_shipping_cost': return_shipping_cost
                    })
            
            return returns_data
            
        except Exception as e:
            print(f"❌ Error in get_returns: {e}")
            import traceback
            traceback.print_exc()
            return []
    
    def sync_returns_to_db(self, days_back=90):
        """
        Sync Amazon returns to sold.db returns table
        Matches returns with original orders to get full details
        """
        print("🔄 Starting Amazon returns sync...")
        
        returns = self.get_returns(days_back=days_back)
        
        if not returns:
            print("ℹ️  No returns to sync")
            return 0
        
        with connect_db('sold.db') as conn:
            cur = conn.cursor()

            synced_count = 0

            for return_data in returns:
                order_id = return_data['order_id']

                cur.execute('''
                    SELECT id, barcode, title, price, shipping_cost, seller_fee, lot_number, location
                    FROM orders
                    WHERE order_id = ? AND store = 'amazon'
                ''', (order_id,))

                original_order = cur.fetchone()

                if not original_order:
                    print(f"Original order not found for return: {order_id}")
                    continue

                original_order_id, barcode, title, original_price, original_shipping, original_fee, lot_number, location = original_order

                # Calculate total return cost: refund + original shipping + return shipping
                total_return_cost = (
                    return_data['refund_amount'] +
                    (original_shipping or 0) +
                    return_data['return_shipping_cost']
                )

                # Update existing logical return when found; otherwise insert a new one.
                cur.execute('''
                    SELECT id
                    FROM returns
                    WHERE store = 'amazon'
                      AND COALESCE(order_id, '') = COALESCE(?, '')
                      AND COALESCE(item_id, '') = COALESCE(?, '')
                      AND COALESCE(barcode, '') = COALESCE(?, '')
                      AND COALESCE(title, '') = COALESCE(?, '')
                      AND COALESCE(return_date, '') = COALESCE(?, '')
                    ORDER BY id DESC
                    LIMIT 1
                ''', (
                    order_id, return_data['item_id'], barcode, title, return_data['return_date']
                ))
                existing_return = cur.fetchone()

                if existing_return:
                    existing_id = existing_return[0]
                    cur.execute('''
                        UPDATE returns
                        SET original_order_id = ?,
                            order_id = ?,
                            item_id = ?,
                            barcode = ?,
                            title = ?,
                            quantity = ?,
                            original_price = ?,
                            refund_amount = ?,
                            original_shipping_cost = ?,
                            return_shipping_cost = ?,
                            original_seller_fee = ?,
                            seller_fee_refund = ?,
                            return_date = ?,
                            store = 'amazon',
                            lot_number = COALESCE(lot_number, ?),
                            location = COALESCE(location, ?)
                        WHERE id = ?
                    ''', (
                        original_order_id, order_id, return_data['item_id'], barcode, title,
                        return_data['quantity'], original_price, return_data['refund_amount'],
                        original_shipping, return_data['return_shipping_cost'],
                        original_fee, return_data['seller_fee_refund'],
                        return_data['return_date'], lot_number, location, existing_id
                    ))
                else:
                    cur.execute('''
                        INSERT INTO returns (
                            original_order_id, order_id, item_id, barcode, title, quantity,
                            original_price, refund_amount, original_shipping_cost, return_shipping_cost,
                            original_seller_fee, seller_fee_refund,
                            return_date, store, lot_number, location
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ''', (
                        original_order_id, order_id, return_data['item_id'], barcode, title,
                        return_data['quantity'], original_price, return_data['refund_amount'],
                        original_shipping, return_data['return_shipping_cost'],
                        original_fee, return_data['seller_fee_refund'],
                        return_data['return_date'], 'amazon', lot_number, location
                    ))

                synced_count += 1
                print(f"  {order_id}: ${total_return_cost:.2f} total return cost")

        print(f"\nSynced {synced_count} returns to database")
        return synced_count

    def sync_settlements_to_db(self, days_back=90):
        """
        Fetch Amazon settlement data and sync to payouts table
        Settlements represent actual payouts from Amazon
        """
        try:
            from sp_api.api import Finances
            import sqlite3
            
            finances_api = Finances(credentials=self.credentials, marketplace=self.marketplace)
            
            # Calculate date range
            started_after = (datetime.utcnow() - timedelta(days=days_back)).isoformat()
            
            print(f"🔄 Fetching Amazon settlements from last {days_back} days...")
            
            # Get settlement groups (payouts)
            response = finances_api.list_financial_event_groups(
                FinancialEventGroupStartedAfter=started_after,
                MaxResultsPerPage=100
            )
            
            if response.errors:
                print(f"❌ Error fetching settlements: {response.errors}")
                return 0
            
            groups = response.payload.get('FinancialEventGroupList', [])
            print(f"📊 Found {len(groups)} settlements")
            
            # Connect to database
            with connect_db('sold.db') as conn:
                cur = conn.cursor()

                synced_count = 0

                for group in groups:
                    settlement_id = group.get('FinancialEventGroupId')
                    start_date = group.get('FinancialEventGroupStart')
                    end_date = group.get('FinancialEventGroupEnd')
                    status = group.get('ProcessingStatus', 'Unknown')

                    # Get payout amount
                    original_total = group.get('OriginalTotal', {})
                    converted_total = group.get('ConvertedTotal', {})

                    amount = float(converted_total.get('CurrencyAmount', original_total.get('CurrencyAmount', 0)))
                    currency = converted_total.get('CurrencyCode', original_total.get('CurrencyCode', 'USD'))

                    # Determine payout date (use end_date for closed settlements)
                    payout_date = end_date if status == 'Closed' else None

                    # Insert or update settlement
                    cur.execute('''
                        INSERT INTO payouts (store, settlement_id, start_date, end_date, payout_date, amount, currency, status, synced_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                        ON CONFLICT(settlement_id) DO UPDATE SET
                            end_date = excluded.end_date,
                            payout_date = excluded.payout_date,
                            amount = excluded.amount,
                            status = excluded.status,
                            synced_at = CURRENT_TIMESTAMP
                    ''', ('amazon', settlement_id, start_date, end_date, payout_date, amount, currency, status))

                    synced_count += 1
                    print(f"  {settlement_id[:20]}...: {currency} ${amount:.2f} ({status})")

            print(f"\nSynced {synced_count} settlements to database")
            return synced_count
            
        except Exception as e:
            print(f"❌ Error syncing settlements: {e}")
            import traceback
            traceback.print_exc()
            return 0


if __name__ == '__main__':
    # Test the connection
    manager = AmazonManager()
    if manager.test_connection():
        print("\n📦 Fetching recent orders...")
        orders = manager.get_orders(days_back=7, max_results=10)
        print(f"Found {len(orders)} orders")
        
        if orders:
            print("\nSample order:")
            print(json.dumps(orders[0], indent=2, default=str))
