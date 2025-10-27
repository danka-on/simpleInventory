"""
Fix Amazon sold orders - populate missing images from amazonStore.db
Matches by barcode (UPC) or ASIN (item_id)
"""

import sqlite3

def fix_amazon_order_images():
    """Update sold.db Amazon orders with images from amazonStore.db"""
    
    print("🔄 Starting Amazon order image fix...")
    print("=" * 60)
    
    # Connect to both databases
    sold_conn = sqlite3.connect('sold.db')
    store_conn = sqlite3.connect('amazonStore.db')
    
    sold_cur = sold_conn.cursor()
    store_cur = store_conn.cursor()
    
    # Find Amazon orders with missing images
    sold_cur.execute("""
        SELECT id, order_id, barcode, item_id, title
        FROM orders 
        WHERE store = 'amazon' 
        AND (image IS NULL OR image = '' OR image = 'None')
        ORDER BY id
    """)
    
    orders_without_images = sold_cur.fetchall()
    
    if not orders_without_images:
        print("✅ No Amazon orders found without images!")
        sold_conn.close()
        store_conn.close()
        return
    
    print(f"📦 Found {len(orders_without_images)} Amazon orders without images")
    print()
    
    updated_count = 0
    not_found_count = 0
    
    for order_id, amazon_order_id, barcode, asin, title in orders_without_images:
        # Try to find image by barcode first, then by ASIN
        image = None
        
        if barcode:
            store_cur.execute("SELECT IMAGE FROM ITEMS WHERE UPC = ?", (barcode,))
            result = store_cur.fetchone()
            if result and result[0]:
                image = result[0]
        
        # If not found by barcode, try ASIN
        if not image and asin:
            store_cur.execute("SELECT IMAGE FROM ITEMS WHERE ASIN = ?", (asin,))
            result = store_cur.fetchone()
            if result and result[0]:
                image = result[0]
        
        if image:
            # Update the order with the image
            sold_cur.execute(
                "UPDATE orders SET image = ? WHERE id = ?",
                (image, order_id)
            )
            
            title_display = (title or '')[:50]
            print(f"✅ [{order_id:3}] {barcode or asin:14} → Image added")
            print(f"    {title_display}")
            updated_count += 1
        else:
            print(f"⚠️  [{order_id:3}] {barcode or asin:14} - No image found")
            not_found_count += 1
    
    # Commit all changes
    sold_conn.commit()
    
    print()
    print("=" * 60)
    print(f"✅ Image fix complete!")
    print(f"   - Updated: {updated_count} orders")
    print(f"   - No image found: {not_found_count} orders")
    print(f"   - Total processed: {len(orders_without_images)} orders")
    
    # Close connections
    sold_conn.close()
    store_conn.close()

if __name__ == '__main__':
    try:
        fix_amazon_order_images()
    except Exception as e:
        print(f"❌ Error: {e}")
        import traceback
        traceback.print_exc()
