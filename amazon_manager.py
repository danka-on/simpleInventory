"""
Amazon SP-API Integration Module
Handles authentication and data fetching from Amazon Seller Central
"""

import json
import sqlite3
from datetime import datetime, timedelta
from sp_api.api import Orders, Reports, CatalogItems, ListingsItems
from sp_api.base import Marketplaces
from sp_api.base.exceptions import SellingApiException

class AmazonManager:
    def __init__(self, credentials_path='amazon_credentials.json'):
        """Initialize Amazon SP-API manager with credentials"""
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
    
    def get_orders(self, days_back=30, max_results=100):
        """
        Fetch recent orders from Amazon
        
        Args:
            days_back: Number of days to look back for orders
            max_results: Maximum number of orders to retrieve
            
        Returns:
            List of order dictionaries
        """
        try:
            orders_api = Orders(credentials=self.credentials, marketplace=self.marketplace)
            
            # Calculate date range
            created_after = (datetime.utcnow() - timedelta(days=days_back)).isoformat()
            
            print(f"🔄 Fetching Amazon orders from last {days_back} days...")
            
            # Fetch orders
            response = orders_api.get_orders(
                CreatedAfter=created_after,
                MaxResultsPerPage=max_results
            )
            
            if response.errors:
                print(f"❌ Error fetching orders: {response.errors}")
                return []
            
            orders = response.payload.get('Orders', [])
            print(f"✅ Retrieved {len(orders)} orders from Amazon")
            
            return orders
            
        except SellingApiException as e:
            print(f"❌ Amazon API Error: {e}")
            return []
        except Exception as e:
            print(f"❌ Error fetching orders: {e}")
            return []
    
    def get_order_items(self, order_id):
        """
        Fetch line items for a specific order
        
        Args:
            order_id: Amazon Order ID
            
        Returns:
            List of order items
        """
        try:
            orders_api = Orders(credentials=self.credentials, marketplace=self.marketplace)
            
            response = orders_api.get_order_items(order_id=order_id)
            
            if response.errors:
                print(f"❌ Error fetching order items: {response.errors}")
                return []
            
            items = response.payload.get('OrderItems', [])
            return items
            
        except SellingApiException as e:
            print(f"❌ Amazon API Error: {e}")
            return []
        except Exception as e:
            print(f"❌ Error fetching order items: {e}")
            return []
    
    def sync_orders_to_db(self, days_back=30):
        """
        Fetch orders from Amazon and sync to local sold.db
        Similar to eBay sold order handling
        
        Returns:
            Number of orders synced
        """
        print("🔄 Starting Amazon order sync...")
        
        orders = self.get_orders(days_back=days_back)
        
        if not orders:
            print("ℹ️ No orders to sync")
            return 0
        
        # Connect to sold.db (same as eBay uses)
        conn = sqlite3.connect('sold.db')
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
        except:
            pass
        
        synced_count = 0
        
        for order in orders:
            try:
                amazon_order_id = order.get('AmazonOrderId')
                purchase_date = order.get('PurchaseDate')
                last_update_date = order.get('LastUpdateDate')
                order_status = order.get('OrderStatus')
                
                # Skip pending/cancelled orders
                if order_status in ['Pending', 'Canceled']:
                    continue
                
                # Get order items
                items = self.get_order_items(amazon_order_id)
                
                for item in items:
                    asin = item.get('ASIN')
                    sku = item.get('SellerSKU')
                    title = item.get('Title')
                    quantity = item.get('QuantityOrdered', 1)
                    price = float(item.get('ItemPrice', {}).get('Amount', 0))
                    
                    # Try to find barcode from amazonStore.db
                    barcode = None
                    try:
                        store_conn = sqlite3.connect('amazonStore.db')
                        store_cur = store_conn.cursor()
                        store_cur.execute('SELECT upc FROM ITEMS WHERE ASIN = ? OR SKU = ?', (asin, sku))
                        result = store_cur.fetchone()
                        if result:
                            barcode = result[0]
                        store_conn.close()
                    except:
                        pass
                    
                    # Determine shipped time
                    shipped_time = None
                    if order_status == 'Shipped':
                        shipped_time = last_update_date
                    
                    # Check if order already exists
                    cur.execute('SELECT id FROM orders WHERE order_id = ?', (amazon_order_id,))
                    existing = cur.fetchone()
                    
                    if existing:
                        # Update existing order
                        cur.execute('''
                            UPDATE orders 
                            SET barcode = ?, title = ?, quantity = ?, price = ?, 
                                shipped_time = ?, paid_time = ?, store = 'amazon'
                            WHERE order_id = ?
                        ''', (barcode, title, quantity, price, shipped_time, purchase_date, amazon_order_id))
                    else:
                        # Insert new order
                        cur.execute('''
                            INSERT INTO orders 
                            (order_id, item_id, barcode, title, quantity, price, shipped_time, paid_time, store)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'amazon')
                        ''', (amazon_order_id, asin, barcode, title, quantity, price, shipped_time, purchase_date))
                        synced_count += 1
                
                conn.commit()
                
            except Exception as e:
                print(f"⚠️ Error syncing order {amazon_order_id}: {e}")
                continue
        
        conn.close()
        print(f"✅ Synced {synced_count} Amazon orders to database")
        return synced_count
    
    def get_active_listings(self):
        """
        Fetch active listings from Amazon using Reports API
        Returns list of inventory items
        """
        try:
            print("🔄 Fetching active Amazon listings...")
            
            from sp_api.api import Reports
            reports_api = Reports(credentials=self.credentials, marketplace=self.marketplace)
            
            # Request an inventory report
            # ReportType: GET_MERCHANT_LISTINGS_ALL_DATA gets all active listings
            response = reports_api.create_report(
                reportType='GET_MERCHANT_LISTINGS_ALL_DATA'
            )
            
            if response.errors:
                print(f"❌ Error creating report: {response.errors}")
                return []
            
            report_id = response.payload.get('reportId')
            print(f"📄 Report requested: {report_id}")
            print("⏳ Waiting for report to be generated (this may take 1-2 minutes)...")
            
            # Poll for report completion
            import time
            max_attempts = 60  # 5 minutes max
            for attempt in range(max_attempts):
                time.sleep(5)  # Wait 5 seconds between checks
                
                status_response = reports_api.get_report(report_id)
                if status_response.errors:
                    print(f"❌ Error checking report status: {status_response.errors}")
                    return []
                
                processing_status = status_response.payload.get('processingStatus')
                
                if processing_status == 'DONE':
                    print("✅ Report ready!")
                    
                    # Get the report document
                    document_id = status_response.payload.get('reportDocumentId')
                    doc_response = reports_api.get_report_document(document_id, download=True)
                    
                    if doc_response.errors:
                        print(f"❌ Error downloading report: {doc_response.errors}")
                        return []
                    
                    # The payload contains the actual report content
                    # It could be a string, bytes, or dict with url/content
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
        
        Returns:
            Number of listings synced
        """
        print("🔄 Starting Amazon listings sync...\n")
        
        listings = self.get_active_listings()
        
        if not listings:
            print("ℹ️ No listings to sync")
            return 0
        
        # Connect to amazonStore.db
        conn = sqlite3.connect('amazonStore.db')
        cur = conn.cursor()
        
        # Create table if it doesn't exist (similar to ebayStore structure)
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
                LAST_UPDATED TEXT
            )
        ''')
        conn.commit()
        
        synced_count = 0
        updated_count = 0
        
        for listing in listings:
            try:
                asin = listing.get('asin', '').strip()
                sku = listing.get('sku', '').strip()
                
                if not asin:
                    continue
                
                # Parse price and quantity
                try:
                    price = float(listing.get('price', 0) or 0)
                except:
                    price = 0.0
                
                try:
                    quantity = int(listing.get('quantity', 0) or 0)
                except:
                    quantity = 0
                
                # Get UPC - from product-id column
                upc = listing.get('upc', '').strip()
                
                # Check if listing exists
                cur.execute('SELECT ID FROM ITEMS WHERE ASIN = ?', (asin,))
                existing = cur.fetchone()
                
                current_time = datetime.utcnow().isoformat() + 'Z'
                
                if existing:
                    # Update existing listing
                    cur.execute('''
                        UPDATE ITEMS 
                        SET SKU = ?, TITLE = ?, PRICE = ?, QUANTITY = ?, 
                            STATUS = ?, IMAGE = ?, UPC = ?, CONDITION = ?, 
                            FULFILLMENT_CHANNEL = ?, LAST_UPDATED = ?
                        WHERE ASIN = ?
                    ''', (
                        sku, listing.get('title', ''), price, quantity,
                        listing.get('status', ''), listing.get('image', ''),
                        upc, listing.get('condition', ''),
                        listing.get('fulfillment_channel', ''), current_time,
                        asin
                    ))
                    updated_count += 1
                else:
                    # Insert new listing
                    cur.execute('''
                        INSERT INTO ITEMS 
                        (ASIN, SKU, TITLE, PRICE, QUANTITY, STATUS, IMAGE, UPC, CONDITION, FULFILLMENT_CHANNEL, LAST_UPDATED)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ''', (
                        asin, sku, listing.get('title', ''), price, quantity,
                        listing.get('status', ''), listing.get('image', ''),
                        upc, listing.get('condition', ''),
                        listing.get('fulfillment_channel', ''), current_time
                    ))
                    synced_count += 1
                
                conn.commit()
                
            except Exception as e:
                print(f"⚠️ Error syncing listing {listing.get('asin')}: {e}")
                continue
        
        conn.close()
        
        print(f"\n✅ Amazon listings sync complete:")
        print(f"   - New: {synced_count} items")
        print(f"   - Updated: {updated_count} items")
        print(f"   - Total: {synced_count + updated_count} items")
        
        return synced_count + updated_count
    
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
