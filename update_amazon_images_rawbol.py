"""
Update amazonStore.db IMAGE column with thumbnails from rawbol.db using barcode (UPC) match
"""
import sqlite3

# Connect to databases
amazon = sqlite3.connect('amazonStore.db')
rawbol = sqlite3.connect('rawbol.db')

amazon_cur = amazon.cursor()
rawbol_cur = rawbol.cursor()

# Get all Amazon items with UPC but no image
amazon_cur.execute("""
    SELECT ASIN, UPC, TITLE 
    FROM ITEMS 
    WHERE UPC IS NOT NULL 
    AND UPC != ''
    AND (IMAGE IS NULL OR IMAGE = '')
""")
amazon_items = amazon_cur.fetchall()

print(f"🔍 Found {len(amazon_items)} Amazon items with UPC but no image")
print("=" * 80)

updated_count = 0
not_found_count = 0

for asin, upc, title in amazon_items:
    # Look up image in rawbol.db by UPC
    rawbol_cur.execute("SELECT image_url FROM raw_bol_items WHERE upc = ?", (upc,))
    result = rawbol_cur.fetchone()
    
    if result and result[0]:
        image_url = result[0]
        # Update amazonStore.db with the image
        amazon_cur.execute("UPDATE ITEMS SET IMAGE = ? WHERE ASIN = ?", (image_url, asin))
        updated_count += 1
        title_display = (title[:50] + "...") if title and len(title) > 50 else (title or "No title")
        print(f"✅ {asin} | {upc} | {title_display}")
    else:
        not_found_count += 1
        if not_found_count <= 10:  # Show first 10 not found
            title_display = (title[:50] + "...") if title and len(title) > 50 else (title or "No title")
            print(f"❌ {asin} | {upc} | {title_display}")

# Commit changes
amazon.commit()

print("\n" + "=" * 80)
print(f"📊 Results:")
print(f"  ✅ Updated with images: {updated_count}")
print(f"  ❌ Not found in rawbol.db: {not_found_count}")

# Show final stats
amazon_cur.execute("SELECT COUNT(*) FROM ITEMS WHERE IMAGE IS NOT NULL AND IMAGE != ''")
with_images = amazon_cur.fetchone()[0]
amazon_cur.execute("SELECT COUNT(*) FROM ITEMS")
total = amazon_cur.fetchone()[0]

print(f"\n📦 Amazon Store Stats:")
print(f"  Total items: {total}")
print(f"  Items with images: {with_images}")
print(f"  Items without images: {total - with_images}")

# Close connections
amazon.close()
rawbol.close()

print("\n✅ Amazon thumbnail update complete!")
