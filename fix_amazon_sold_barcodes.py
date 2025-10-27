"""
One-time script to backfill barcodes for Amazon sold orders in sold.db
Matches ASIN (item_id) from sold orders to amazonStore.db to get UPC
"""

import sqlite3

def fix_amazon_sold_barcodes():
    """Update sold.db Amazon orders with barcodes from amazonStore.db"""
    
    print("🔄 Starting Amazon sold orders barcode fix...")
    print("=" * 60)
    
    # Connect to both databases
    sold_conn = sqlite3.connect('sold.db')
    store_conn = sqlite3.connect('amazonStore.db')
    
    sold_cur = sold_conn.cursor()
    store_cur = store_conn.cursor()
    
    # Find Amazon orders without barcodes
    sold_cur.execute("""
        SELECT id, order_id, item_id, title 
        FROM orders 
        WHERE store = 'amazon' 
        AND (barcode IS NULL OR barcode = '' OR barcode = 'None')
    """)
    
    orders_without_barcodes = sold_cur.fetchall()
    
    if not orders_without_barcodes:
        print("✅ No Amazon orders found without barcodes!")
        sold_conn.close()
        store_conn.close()
        return
    
    print(f"📦 Found {len(orders_without_barcodes)} Amazon orders without barcodes")
    print()
    
    updated_count = 0
    not_found_count = 0
    
    for order_id, amazon_order_id, asin, title in orders_without_barcodes:
        # Look up the ASIN in amazonStore to get UPC
        store_cur.execute("SELECT UPC FROM ITEMS WHERE ASIN = ?", (asin,))
        result = store_cur.fetchone()
        
        if result and result[0] and result[0].strip():
            upc = result[0].strip()
            
            # Skip if UPC is same as ASIN (means no real UPC was found)
            if upc == asin:
                print(f"⚠️  [{order_id}] {asin[:12]:12} - UPC same as ASIN, skipping")
                not_found_count += 1
                continue
            
            # Update the sold order with the barcode
            sold_cur.execute(
                "UPDATE orders SET barcode = ? WHERE id = ?",
                (upc, order_id)
            )
            
            print(f"✅ [{order_id}] {asin[:12]:12} → UPC: {upc} | {title[:40]}")
            updated_count += 1
        else:
            print(f"⚠️  [{order_id}] {asin[:12]:12} - No UPC found in amazonStore")
            not_found_count += 1
    
    # Commit all changes
    sold_conn.commit()
    
    print()
    print("=" * 60)
    print(f"✅ Barcode fix complete!")
    print(f"   - Updated: {updated_count} orders")
    print(f"   - No UPC found: {not_found_count} orders")
    print(f"   - Total processed: {len(orders_without_barcodes)} orders")
    
    # Close connections
    sold_conn.close()
    store_conn.close()

if __name__ == '__main__':
    try:
        fix_amazon_sold_barcodes()
    except Exception as e:
        print(f"❌ Error: {e}")
        import traceback
        traceback.print_exc()
