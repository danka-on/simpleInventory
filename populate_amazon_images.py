"""
Add IMAGE column to amazonStore.db and populate from rawbol.db using UPC matching
"""
import sqlite3

# Connect to databases
amazon = sqlite3.connect('amazonStore.db')
rawbol = sqlite3.connect('rawbol.db')

amazon_cur = amazon.cursor()
rawbol_cur = rawbol.cursor()

print("📦 Updating amazonStore.db with images from rawbol.db...")
print("=" * 80)

# Step 1: Ensure IMAGE column exists
try:
    amazon_cur.execute('PRAGMA table_info(ITEMS)')
    cols = [r[1] for r in amazon_cur.fetchall()]
    if 'IMAGE' not in cols:
        print("➕ Adding IMAGE column to amazonStore.db ITEMS table...")
        amazon_cur.execute('ALTER TABLE ITEMS ADD COLUMN IMAGE TEXT')
        amazon.commit()
        print("✅ IMAGE column added")
    else:
        print("✅ IMAGE column already exists")
except Exception as e:
    print(f"❌ Error adding IMAGE column: {e}")
    amazon.close()
    rawbol.close()
    exit(1)

# Step 2: Get all Amazon items with UPC
amazon_cur.execute("""
    SELECT ASIN, UPC, TITLE 
    FROM ITEMS 
    WHERE UPC IS NOT NULL 
    AND UPC != ''
""")
amazon_items = amazon_cur.fetchall()

print(f"\n🔍 Found {len(amazon_items)} Amazon items with UPC")
print("=" * 80)

updated_count = 0
not_found_count = 0
already_has_image = 0

for asin, upc, title in amazon_items:
    # Check if already has image
    amazon_cur.execute("SELECT IMAGE FROM ITEMS WHERE ASIN = ?", (asin,))
    current_image = amazon_cur.fetchone()[0]
    
    if current_image:
        already_has_image += 1
        continue
    
    # Look up image in rawbol.db by UPC (try both with and without leading zeros)
    rawbol_cur.execute("SELECT image_url FROM raw_bol_items WHERE upc = ?", (upc,))
    result = rawbol_cur.fetchone()
    
    # If not found and UPC has leading zeros, try without them
    if not result and upc.startswith('0'):
        upc_no_zero = upc.lstrip('0')
        rawbol_cur.execute("SELECT image_url FROM raw_bol_items WHERE upc = ?", (upc_no_zero,))
        result = rawbol_cur.fetchone()
    
    if result and result[0]:
        image_url = result[0]
        # Skip if image_url is 'nan' string or similar
        if str(image_url).lower() in ['nan', 'none', '', 'null']:
            not_found_count += 1
            continue
        
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
print(f"  ⏭️  Already had images: {already_has_image}")
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
