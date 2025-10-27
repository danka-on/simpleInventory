"""
Verify UPC matching between amazonStore.db and rawbol.db
"""
import sqlite3

print("🔍 Verifying UPC matching between amazonStore and rawbol...")
print("=" * 80)

amazon = sqlite3.connect('amazonStore.db')
rawbol = sqlite3.connect('rawbol.db')

amazon_cur = amazon.cursor()
rawbol_cur = rawbol.cursor()

# Get sample UPCs from Amazon
amazon_cur.execute("SELECT UPC FROM ITEMS WHERE UPC IS NOT NULL LIMIT 10")
sample_upcs = [row[0] for row in amazon_cur.fetchall()]

print(f"Testing {len(sample_upcs)} sample UPCs from amazonStore.db:\n")

matched = 0
not_matched = 0

for upc in sample_upcs:
    # Try to find in rawbol
    rawbol_cur.execute("SELECT upc, image_url FROM raw_bol_items WHERE upc = ?", (upc,))
    result = rawbol_cur.fetchone()
    
    if result:
        image_url = result[1]
        has_valid_image = image_url and str(image_url).lower() not in ['nan', 'none', '', 'null']
        status = "✅" if has_valid_image else "⚠️ (no valid image)"
        matched += 1
        print(f"{status} UPC: {upc}")
        if has_valid_image:
            img_display = (image_url[:60] + "...") if len(str(image_url)) > 60 else image_url
            print(f"   Image: {img_display}")
    else:
        not_matched += 1
        print(f"❌ UPC: {upc} - NOT FOUND in rawbol.db")

print("\n" + "=" * 80)
print(f"📊 Results:")
print(f"  Matched in rawbol.db: {matched}/{len(sample_upcs)}")
print(f"  Not found in rawbol.db: {not_matched}/{len(sample_upcs)}")
print(f"  Match rate: {matched*100//len(sample_upcs) if sample_upcs else 0}%")

amazon.close()
rawbol.close()
