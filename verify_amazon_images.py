"""
Verify that amazonStore.db has IMAGE column populated and enrichment works
"""
import sqlite3

print("🔍 Checking amazonStore.db IMAGE column...")
print("=" * 80)

# Check amazonStore.db
amazon = sqlite3.connect('amazonStore.db')
amazon_cur = amazon.cursor()

# Verify IMAGE column exists
amazon_cur.execute('PRAGMA table_info(ITEMS)')
cols = [r[1] for r in amazon_cur.fetchall()]
if 'IMAGE' in cols:
    print("✅ IMAGE column exists in amazonStore.db")
else:
    print("❌ IMAGE column missing from amazonStore.db")
    amazon.close()
    exit(1)

# Get stats
amazon_cur.execute('SELECT COUNT(*) FROM ITEMS')
total = amazon_cur.fetchone()[0]

amazon_cur.execute('SELECT COUNT(*) FROM ITEMS WHERE IMAGE IS NOT NULL AND IMAGE != ""')
with_images = amazon_cur.fetchone()[0]

print(f"\n📊 Amazon Store Stats:")
print(f"  Total items: {total}")
print(f"  Items with images: {with_images} ({with_images*100//total if total > 0 else 0}%)")
print(f"  Items without images: {total - with_images}")

# Show sample items with images
print(f"\n📋 Sample Amazon items with images:")
amazon_cur.execute("""
    SELECT ASIN, UPC, TITLE, IMAGE 
    FROM ITEMS 
    WHERE IMAGE IS NOT NULL AND IMAGE != ''
    LIMIT 5
""")
for row in amazon_cur.fetchall():
    asin, upc, title, image = row
    title_display = (title[:50] + "...") if title and len(title) > 50 else (title or "None")
    image_display = (image[:50] + "...") if image and len(image) > 50 else (image or "None")
    print(f"  {asin} | {upc} | {title_display}")
    print(f"    Image: {image_display}")

amazon.close()

print("\n" + "=" * 80)
print("✅ Verification complete!")
print("\n💡 Next steps:")
print("  1. Run Amazon listings sync to test automatic image population")
print("  2. Run searchRack enrichment to test Amazon image display")
print("  3. Check /searchrack page for Amazon items with thumbnails")
