"""
Backfill images for sold.db orders from ebayStore.db, amazonStore.db, and rawbol.db
"""
import sqlite3

print("Backfilling images for sold orders...")
print("=" * 80)

sold = sqlite3.connect('sold.db')
sold_cur = sold.cursor()

# Get all orders without images (including 'No image' strings)
sold_cur.execute("""
    SELECT id, order_id, item_id, barcode, title, store
    FROM orders 
    WHERE (image IS NULL OR image = '' OR LOWER(image) = 'no image')
    AND (isHandled IS NULL OR isHandled = '')
""")
orders_without_images = sold_cur.fetchall()

print(f"Found {len(orders_without_images)} orders without images\n")

updated_from_ebay = 0
updated_from_rawbol = 0
updated_from_amazon = 0
not_found = 0

ebay_conn = sqlite3.connect('ebayStore.db')
ebay_cur = ebay_conn.cursor()

rawbol_conn = sqlite3.connect('rawbol.db')
rawbol_cur = rawbol_conn.cursor()

amazon_conn = sqlite3.connect('amazonStore.db')
amazon_cur = amazon_conn.cursor()

for order_id_pk, order_id, item_id, barcode, title, store in orders_without_images:
    image_found = None
    source = None
    
    # Try ebayStore.db first if it's an eBay order
    if store == 'ebay' and item_id:
        ebay_cur.execute("SELECT Image FROM INVENTORY WHERE ItemID = ?", (item_id,))
        result = ebay_cur.fetchone()
        if result:
            img_val = result[0]
            print(f"  DEBUG: ebayStore returned for {item_id}: '{img_val}'")
            if img_val and str(img_val).strip().lower() not in ['no image', 'null', '', 'none']:
                image_found = img_val
                source = "ebayStore"
            else:
                print(f"  DEBUG: Skipped because value is '{img_val}'")
        else:
            print(f"  DEBUG: ItemID {item_id} not found in ebayStore")
    
    # Try amazonStore.db if it's an Amazon order (item_id is ASIN for Amazon orders)
    if not image_found and store == 'amazon' and item_id:
        amazon_cur.execute("SELECT IMAGE FROM ITEMS WHERE ASIN = ?", (item_id,))
        result = amazon_cur.fetchone()
        if result:
            img_val = result[0]
            print(f"  DEBUG: amazonStore returned for {item_id}: '{img_val}'")
            lowered = str(img_val).strip().lower() if img_val else ''
            print(f"  DEBUG: After lowering: '{lowered}', in list? {lowered in ['no image', 'null', '', 'none']}")
            if img_val and lowered not in ['no image', 'null', '', 'none']:
                image_found = img_val
                source = "amazonStore"
            else:
                print(f"  DEBUG: Skipped because value is '{img_val}'")
        else:
            print(f"  DEBUG: ASIN {item_id} not found in amazonStore")
    
    # If not found and we have a barcode, try rawbol.db
    if not image_found and barcode:
        # Try exact match
        rawbol_cur.execute("SELECT image_url FROM raw_bol_items WHERE upc = ?", (barcode,))
        result = rawbol_cur.fetchone()
        
        # Try without leading zeros
        if not result and barcode.startswith('0'):
            barcode_no_zero = barcode.lstrip('0')
            rawbol_cur.execute("SELECT image_url FROM raw_bol_items WHERE upc = ?", (barcode_no_zero,))
            result = rawbol_cur.fetchone()
        
        if result and result[0]:
            img_url = result[0]
            # Skip 'nan' values
            if str(img_url).lower() not in ['nan', 'none', 'null', '']:
                image_found = img_url
                source = "rawbol"
    
    # Update if image found
    if image_found:
        sold_cur.execute("UPDATE orders SET image = ? WHERE id = ?", (image_found, order_id_pk))
        if source == "ebayStore":
            updated_from_ebay += 1
        elif source == "amazonStore":
            updated_from_amazon += 1
        else:
            updated_from_rawbol += 1
        
        title_display = (title[:50] + "...") if title and len(title) > 50 else (title or "No title")
        print(f"OK {order_id} | {barcode or 'No barcode'} | {source} | {title_display}")
    else:
        not_found += 1
        if not_found <= 10:
            title_display = (title[:50] + "...") if title and len(title) > 50 else (title or "No title")
            print(f"NOT FOUND {order_id} | {barcode or 'No barcode'} | Store: {store} | {title_display}")

# Commit changes
sold.commit()

print("\n" + "=" * 80)
print(f"Results:")
print(f"  Updated from ebayStore.db: {updated_from_ebay}")
print(f"  Updated from amazonStore.db: {updated_from_amazon}")
print(f"  Updated from rawbol.db: {updated_from_rawbol}")
print(f"  Total updated: {updated_from_ebay + updated_from_amazon + updated_from_rawbol}")
print(f"  Not found: {not_found}")

# Final stats
sold_cur.execute("SELECT COUNT(*) FROM orders WHERE image IS NOT NULL AND image != '' AND LOWER(image) != 'no image'")
with_images = sold_cur.fetchone()[0]
sold_cur.execute("SELECT COUNT(*) FROM orders WHERE isHandled IS NULL OR isHandled = ''")
total_active = sold_cur.fetchone()[0]

print(f"\nSold Orders Stats (active orders):")
print(f"  Total active orders: {total_active}")
print(f"  Orders with images: {with_images}")

sold.close()
ebay_conn.close()
amazon_conn.close()
rawbol_conn.close()

print("\nBackfill complete!")
