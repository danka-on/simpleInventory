"""
Fix Amazon sold orders (ready to ship) that are missing image/title data
Looks up info from amazonStore.db using the barcode/UPC
"""

import sqlite3

def fix_amazon_sold_order_details():
    """Update sold.db Amazon orders with images and titles from amazonStore.db"""
    
    print("🔄 Starting Amazon sold orders detail fix...")
    print("=" * 60)
    
    # Connect to both databases
    sold_conn = sqlite3.connect('sold.db')
    store_conn = sqlite3.connect('amazonStore.db')
    
    sold_cur = sold_conn.cursor()
    store_cur = store_conn.cursor()
    
    # Find ready-to-ship Amazon orders with missing data
    sold_cur.execute("""
        SELECT id, order_id, barcode, title, image, item_id
        FROM orders 
        WHERE store = 'amazon' 
        AND isHandled = 0
        AND barcode IS NOT NULL 
        AND barcode != ''
        AND (
            image IS NULL OR image = '' OR
            title IS NULL OR title = ''
        )
        ORDER BY id
    """)
    
    orders_needing_update = sold_cur.fetchall()
    
    if not orders_needing_update:
        print("✅ No Amazon orders found needing updates!")
        sold_conn.close()
        store_conn.close()
        return
    
    print(f"📦 Found {len(orders_needing_update)} Amazon orders needing image/title")
    print()
    
    updated_count = 0
    not_found_count = 0
    
    for order_id, amazon_order_id, barcode, current_title, current_image, asin in orders_needing_update:
        # Look up the barcode/UPC in amazonStore to get title and image
        store_cur.execute(
            "SELECT TITLE, IMAGE FROM ITEMS WHERE UPC = ? OR ASIN = ?", 
            (barcode, asin)
        )
        result = store_cur.fetchone()
        
        if result:
            new_title, new_image = result
            
            # Determine what to update
            needs_title = not current_title or current_title.strip() == ''
            needs_image = not current_image or current_image.strip() == ''
            
            if needs_title and needs_image:
                sold_cur.execute(
                    "UPDATE orders SET title = ?, image = ? WHERE id = ?",
                    (new_title, new_image, order_id)
                )
                status = "📸+📝"
            elif needs_title:
                sold_cur.execute(
                    "UPDATE orders SET title = ? WHERE id = ?",
                    (new_title, order_id)
                )
                status = "📝"
            elif needs_image:
                sold_cur.execute(
                    "UPDATE orders SET image = ? WHERE id = ?",
                    (new_image, order_id)
                )
                status = "📸"
            else:
                status = "✓"
            
            title_display = (new_title or current_title or '')[:50]
            print(f"{status} [{order_id:3}] {barcode:14} | {title_display}")
            updated_count += 1
        else:
            print(f"⚠️  [{order_id:3}] {barcode:14} - Not found in amazonStore (ASIN: {asin})")
            not_found_count += 1
    
    # Commit all changes
    sold_conn.commit()
    
    print()
    print("=" * 60)
    print(f"✅ Detail fix complete!")
    print(f"   - Updated: {updated_count} orders")
    print(f"   - Not found in store: {not_found_count} orders")
    print(f"   - Total processed: {len(orders_needing_update)} orders")
    print()
    print("Legend: 📸 = image added, 📝 = title added, 📸+📝 = both added")
    
    # Close connections
    sold_conn.close()
    store_conn.close()

if __name__ == '__main__':
    try:
        fix_amazon_sold_order_details()
    except Exception as e:
        print(f"❌ Error: {e}")
        import traceback
        traceback.print_exc()
