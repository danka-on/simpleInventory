"""
Backfill images for sold.db orders from ebayStore.db and rawbol.db
"""
import sqlite3

print("🔄 Backfilling images for sold orders...")
print("=" * 80)

sold = sqlite3.connect('sold.db')
sold_cur = sold.cursor()

# Get all orders without images
sold_cur.execute("""
    SELECT id, order_id, item_id, barcode, title, store
    FROM orders 
    WHERE (image IS NULL OR image = '')
    AND isHandled IS NULL OR isHandled = ''
""")
orders_without_images = sold_cur.fetchall()

print(f"Found {len(orders_without_images)} orders without images\n")

updated_from_ebay = 0
updated_from_rawbol = 0
not_found = 0

ebay_conn = sqlite3.connect('ebayStore.db')
ebay_cur = ebay_conn.cursor()

rawbol_conn = sqlite3.connect('rawbol.db')
rawbol_cur = rawbol_conn.cursor()

for order_id_pk, order_id, item_id, barcode, title, store in orders_without_images:
    image_found = None
    source = None
    
    # Try ebayStore.db first if it's an eBay order
    if store == 'ebay' and item_id:
        ebay_cur.execute("SELECT Image FROM INVENTORY WHERE ItemID = ?", (item_id,))
        result = ebay_cur.fetchone()
        if result and result[0]:
            image_found = result[0]
            source = "ebayStore"
    
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
        else:
            updated_from_rawbol += 1
        
        title_display = (title[:50] + "...") if title and len(title) > 50 else (title or "No title")
        print(f"✅ {order_id} | {barcode or 'No barcode'} | {source} | {title_display}")
    else:
        not_found += 1
        if not_found <= 10:
            title_display = (title[:50] + "...") if title and len(title) > 50 else (title or "No title")
            print(f"❌ {order_id} | {barcode or 'No barcode'} | {title_display}")

# Commit changes
sold.commit()

print("\n" + "=" * 80)
print(f"📊 Results:")
print(f"  ✅ Updated from ebayStore.db: {updated_from_ebay}")
print(f"  ✅ Updated from rawbol.db: {updated_from_rawbol}")
print(f"  ✅ Total updated: {updated_from_ebay + updated_from_rawbol}")
print(f"  ❌ Not found: {not_found}")

# Final stats
sold_cur.execute("SELECT COUNT(*) FROM orders WHERE image IS NOT NULL AND image != ''")
with_images = sold_cur.fetchone()[0]
sold_cur.execute("SELECT COUNT(*) FROM orders WHERE isHandled IS NULL OR isHandled = ''")
total_active = sold_cur.fetchone()[0]

print(f"\n📦 Sold Orders Stats (active orders):")
print(f"  Total active orders: {total_active}")
print(f"  Orders with images: {with_images}")

sold.close()
ebay_conn.close()
rawbol_conn.close()

print("\n✅ Backfill complete!")
