"""
Fix Amazon sold orders - populate missing images from rawbol.db
Matches by UPC barcode
"""

import sqlite3

def fix_amazon_order_images_from_rawbol():
    """Update sold.db Amazon orders with images from rawbol.db"""
    
    print("🔄 Starting Amazon order image fix from rawbol.db...")
    print("=" * 60)
    
    # Connect to both databases
    sold_conn = sqlite3.connect('sold.db')
    rawbol_conn = sqlite3.connect('rawbol.db')
    
    sold_cur = sold_conn.cursor()
    rawbol_cur = rawbol_conn.cursor()
    
    # Find Amazon orders with missing images
    sold_cur.execute("""
        SELECT id, order_id, barcode, item_id, title
        FROM orders 
        WHERE store = 'amazon' 
        AND (image IS NULL OR image = '' OR image = 'None')
        AND barcode IS NOT NULL
        AND barcode != ''
        ORDER BY id
    """)
    
    orders_without_images = sold_cur.fetchall()
    
    if not orders_without_images:
        print("✅ No Amazon orders found without images!")
        sold_conn.close()
        rawbol_conn.close()
        return
    
    print(f"📦 Found {len(orders_without_images)} Amazon orders without images")
    print()
    
    updated_count = 0
    not_found_count = 0
    
    for order_id, amazon_order_id, barcode, asin, title in orders_without_images:
        # Try to find image by UPC in rawbol.db
        image = None
        
        rawbol_cur.execute("SELECT image_url FROM raw_bol_items WHERE upc = ?", (barcode,))
        result = rawbol_cur.fetchone()
        
        if result and result[0]:
            image = result[0]
        
        if image:
            # Update the order with the image
            sold_cur.execute(
                "UPDATE orders SET image = ? WHERE id = ?",
                (image, order_id)
            )
            
            title_display = (title or '')[:50]
            print(f"✅ [{order_id:3}] {barcode:14} → Image added")
            print(f"    {title_display}")
            updated_count += 1
        else:
            title_display = (title or '')[:50]
            print(f"⚠️  [{order_id:3}] {barcode:14} - No image in rawbol.db")
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
    rawbol_conn.close()

if __name__ == '__main__':
    try:
        fix_amazon_order_images_from_rawbol()
    except Exception as e:
        print(f"❌ Error: {e}")
        import traceback
        traceback.print_exc()
