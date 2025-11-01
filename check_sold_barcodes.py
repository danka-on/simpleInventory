"""
Check sold.db for missing barcodes and attempt to enrich them from other databases.
"""
import sqlite3

def check_and_enrich_barcodes():
    """Check sold.db for missing barcodes and enrich from ebayStore and amazonStore"""
    
    # Connect to databases
    sold_conn = sqlite3.connect('sold.db')
    sold_conn.row_factory = sqlite3.Row
    sold_cur = sold_conn.cursor()
    
    ebay_conn = sqlite3.connect('ebayStore.db')
    ebay_conn.row_factory = sqlite3.Row
    ebay_cur = ebay_conn.cursor()
    
    amazon_conn = sqlite3.connect('amazonStore.db')
    amazon_conn.row_factory = sqlite3.Row
    amazon_cur = amazon_conn.cursor()
    
    print("\n=== Checking Sold Orders for Missing Barcodes ===\n")
    
    # Get total counts
    sold_cur.execute('SELECT COUNT(*) as total FROM orders')
    total_orders = sold_cur.fetchone()['total']
    
    sold_cur.execute('SELECT COUNT(*) FROM orders WHERE barcode IS NULL OR barcode = ""')
    missing_count = sold_cur.fetchone()[0]
    
    print(f"📊 Total orders: {total_orders}")
    print(f"❌ Missing barcodes: {missing_count}")
    print(f"✅ Have barcodes: {total_orders - missing_count}")
    
    if missing_count == 0:
        print("\n✅ All orders have barcodes!")
        sold_conn.close()
        ebay_conn.close()
        amazon_conn.close()
        return
    
    # Get orders with missing barcodes
    print(f"\n🔍 Checking {missing_count} orders for enrichment possibilities...\n")
    
    sold_cur.execute('''
        SELECT order_id, item_id, title, store
        FROM orders 
        WHERE barcode IS NULL OR barcode = ""
        ORDER BY paid_time DESC
        LIMIT 10
    ''')
    
    missing_orders = sold_cur.fetchall()
    
    enriched_count = 0
    
    for order in missing_orders:
        order_id = order['order_id']
        item_id = order['item_id']
        title = order['title'][:50] if order['title'] else 'N/A'
        store = order['store']
        
        barcode_found = None
        source = None
        
        # Try to find barcode in ebayStore
        if store == 'ebay':
            ebay_cur.execute('SELECT UPC FROM INVENTORY WHERE ItemID = ? COLLATE NOCASE', (item_id,))
            ebay_row = ebay_cur.fetchone()
            if ebay_row and ebay_row['UPC']:
                barcode_found = ebay_row['UPC']
                source = 'ebayStore'
        
        # Try to find barcode in amazonStore
        elif store == 'amazon':
            amazon_cur.execute('SELECT UPC FROM ITEMS WHERE ASIN = ? COLLATE NOCASE', (item_id,))
            amazon_row = amazon_cur.fetchone()
            if amazon_row and amazon_row['UPC']:
                barcode_found = amazon_row['UPC']
                source = 'amazonStore'
        
        if barcode_found:
            print(f"✅ {order_id} | {item_id} | {title}...")
            print(f"   Found UPC: {barcode_found} (from {source})")
            enriched_count += 1
            
            # Update the barcode
            sold_cur.execute('UPDATE orders SET barcode = ? WHERE order_id = ?', (barcode_found, order_id))
        else:
            print(f"❌ {order_id} | {item_id} | {title}...")
            print(f"   No UPC found in {store}Store")
    
    # Commit changes
    sold_conn.commit()
    
    print(f"\n{'='*60}")
    print(f"✅ Enriched {enriched_count} orders with barcodes")
    print(f"❌ {missing_count - enriched_count} orders still missing barcodes")
    
    # Check if there are more to process
    if missing_count > 10:
        print(f"\n💡 Only processed first 10 orders. Run again to process more.")
    
    # Close connections
    sold_conn.close()
    ebay_conn.close()
    amazon_conn.close()

if __name__ == '__main__':
    check_and_enrich_barcodes()
