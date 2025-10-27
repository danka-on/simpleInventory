"""
Populate Amazon images from Amazon Catalog API for all items in amazonStore.db
"""
import sqlite3
from amazon_manager import AmazonManager
import time

print("Populating Amazon images from Amazon Catalog API...")
print("=" * 80)

conn = sqlite3.connect('amazonStore.db')
cur = conn.cursor()

# Get all items without images (NULL or 'None' string)
cur.execute("""
    SELECT ASIN, TITLE 
    FROM ITEMS 
    WHERE IMAGE IS NULL OR IMAGE = 'None' OR IMAGE = ''
""")
items = cur.fetchall()

print(f"Found {len(items)} items without images\n")

amazon = AmazonManager()
updated = 0
not_found = 0
api_calls = 0

for asin, title in items:
    try:
        # Fetch from Amazon Catalog API
        catalog_data = amazon.get_catalog_item(asin)
        api_calls += 1
        
        if catalog_data and 'images' in catalog_data:
            # Extract the MAIN variant image with largest size (>= 500px)
            image_url = None
            for image_group in catalog_data['images']:
                if 'images' in image_group:
                    for img in image_group['images']:
                        if img.get('variant') == 'MAIN' and img.get('height', 0) >= 500:
                            image_url = img.get('link', '')
                            break
                    if image_url:
                        break
            
            if image_url:
                cur.execute("UPDATE ITEMS SET IMAGE = ? WHERE ASIN = ?", (image_url, asin))
                conn.commit()
                updated += 1
                title_display = (title[:50] + "...") if title and len(title) > 50 else (title or "No title")
                print(f"OK {asin} | {title_display}")
            else:
                not_found += 1
                if not_found <= 10:
                    print(f"No image {asin} | {title[:50] if title else 'No title'}")
        else:
            not_found += 1
            if not_found <= 10:
                print(f"No catalog data {asin} | {title[:50] if title else 'No title'}")
        
        # Rate limiting: Amazon allows 5 req/sec for Catalog API
        # Sleep 0.2 seconds = max 5 per second
        if api_calls % 5 == 0:
            time.sleep(1)
            
    except Exception as e:
        print(f"Error {asin}: {e}")
        not_found += 1

conn.close()

print("\n" + "=" * 80)
print(f"Results:")
print(f"  Updated with Amazon images: {updated}")
print(f"  Not found: {not_found}")
print(f"  Total API calls: {api_calls}")

# Final stats
conn = sqlite3.connect('amazonStore.db')
cur = conn.cursor()
cur.execute("SELECT COUNT(*) FROM ITEMS WHERE IMAGE IS NOT NULL AND IMAGE != '' AND IMAGE != 'None'")
with_images = cur.fetchone()[0]
cur.execute("SELECT COUNT(*) FROM ITEMS")
total = cur.fetchone()[0]
conn.close()

print(f"\namazonStore.db Stats:")
print(f"  Total items: {total}")
print(f"  Items with images: {with_images} ({int(with_images/total*100)}%)")
print(f"  Items without images: {total - with_images}")

print("\nComplete!")
