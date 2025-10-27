"""
Amazon SP-API Integration Module
Handles authentication and data fetching from Amazon Seller Central
"""

import json
import sqlite3
from datetime import datetime, timedelta
from sp_api.api import Orders, Reports, CatalogItems
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
