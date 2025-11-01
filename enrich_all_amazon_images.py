"""
Fetch and update images for all Amazon items that don't have them
"""
import sqlite3
from amazon_manager import AmazonManager
import time

print("=== Enriching Amazon Store Items with Images ===\n")

# Connect to amazonStore.db
conn = sqlite3.connect('amazonStore.db')
conn.row_factory = sqlite3.Row
cur = conn.cursor()

# Find items without images
cur.execute("""
    SELECT ASIN, UPC, Title 
    FROM ITEMS 
    WHERE IMAGE IS NULL OR IMAGE = ''
    ORDER BY ASIN
""")

items_without_images = cur.fetchall()
total = len(items_without_images)

print(f"📸 Found {total} Amazon items without images\n")

if total == 0:
    print("✅ All items already have images!")
    conn.close()
    exit(0)

# Initialize Amazon manager
amazon = AmazonManager()

updated = 0
failed = 0
skipped = 0

for idx, row in enumerate(items_without_images, 1):
    asin = row['ASIN']
    upc = row['UPC']
    title = row['Title'][:50] if row['Title'] else 'N/A'
    
    print(f"[{idx}/{total}] Processing {asin} - {title}...")
    
    try:
        # Fetch from Amazon Catalog API
        result = amazon.get_catalog_item(asin=asin)
        
        if result and 'images' in result:
            # Extract MAIN image with largest size
            image_url = None
            max_height = 0
            
            for image_group in result['images']:
                if 'images' in image_group:
                    for img in image_group['images']:
                        if img.get('variant') == 'MAIN':
                            height = img.get('height', 0)
                            link = img.get('link', '')
                            
                            # Prefer high-res images (>= 500px)
                            if height >= 500 and height > max_height:
                                image_url = link
                                max_height = height
            
            if image_url:
                # Update database
                cur.execute("UPDATE ITEMS SET IMAGE = ? WHERE ASIN = ?", (image_url, asin))
                conn.commit()
                updated += 1
                print(f"   ✅ Updated with {max_height}px image")
            else:
                skipped += 1
                print(f"   ⚠️ No suitable image found in API response")
        else:
            skipped += 1
            print(f"   ⚠️ No images in API response")
        
        # Rate limiting - be nice to Amazon API
        time.sleep(0.5)
        
    except Exception as e:
        failed += 1
        print(f"   ❌ Error: {e}")
        time.sleep(1)  # Longer wait after error
    
    # Progress update every 10 items
    if idx % 10 == 0:
        print(f"\n--- Progress: {idx}/{total} processed ({updated} updated, {skipped} skipped, {failed} failed) ---\n")

conn.close()

print(f"\n{'='*70}")
print(f"✅ Image Enrichment Complete!")
print(f"   Total processed: {total}")
print(f"   Successfully updated: {updated}")
print(f"   Skipped (no image): {skipped}")
print(f"   Failed: {failed}")
print(f"{'='*70}")
