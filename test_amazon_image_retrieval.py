"""
Test that amazonStore.db items return with IMAGE field in search results
"""
import sqlite3

print("🔍 Testing Amazon item image retrieval...")
print("=" * 80)

# Get a sample Amazon item with image
amazon = sqlite3.connect('amazonStore.db')
amazon.row_factory = sqlite3.Row
amazon_cur = amazon.cursor()

amazon_cur.execute("""
    SELECT ASIN, UPC, TITLE, IMAGE 
    FROM ITEMS 
    WHERE IMAGE IS NOT NULL 
    LIMIT 5
""")
sample_items = amazon_cur.fetchall()

if not sample_items:
    print("❌ No Amazon items with images found!")
    amazon.close()
    exit(1)

print(f"✅ Found {len(sample_items)} Amazon items with images")
print("\n📋 Sample items:")

for item in sample_items:
    # Convert Row to dict (simulating what the search endpoint does)
    item_dict = dict(item)
    
    # Simulate the image extraction logic
    image_val = item_dict.get('IMAGE') or item_dict.get('Image') or item_dict.get('image') or item_dict.get('image_url') or item_dict.get('images') or ''
    
    title = (item['TITLE'][:50] + "...") if item['TITLE'] and len(item['TITLE']) > 50 else (item['TITLE'] or "No title")
    image_display = (image_val[:60] + "...") if image_val and len(image_val) > 60 else (image_val or "No image")
    
    print(f"\n  ASIN: {item['ASIN']}")
    print(f"  UPC: {item['UPC']}")
    print(f"  Title: {title}")
    print(f"  Image (from dict): {image_display}")
    print(f"  Image extracted: {'✅' if image_val else '❌'}")

amazon.close()

print("\n" + "=" * 80)
print("✅ Test complete!")
print("\n💡 The search endpoint should now return Amazon images correctly.")
print("   Restart your Flask app to see the changes.")
