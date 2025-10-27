"""
Clean up 'nan' values in amazonStore.db IMAGE column
"""
import sqlite3

amazon = sqlite3.connect('amazonStore.db')
amazon_cur = amazon.cursor()

# Find and fix 'nan' values (case insensitive)
amazon_cur.execute("SELECT COUNT(*) FROM ITEMS WHERE LOWER(IMAGE) = 'nan' OR IMAGE = 'nan'")
nan_count = amazon_cur.fetchone()[0]

if nan_count > 0:
    print(f"🔧 Fixing {nan_count} items with 'nan' image values...")
    amazon_cur.execute("UPDATE ITEMS SET IMAGE = NULL WHERE LOWER(IMAGE) = 'nan' OR IMAGE = 'nan'")
    amazon.commit()
    print(f"✅ Fixed {nan_count} items")
else:
    print("✅ No 'nan' values found")

# Also fix empty strings
amazon_cur.execute("SELECT COUNT(*) FROM ITEMS WHERE IMAGE = '' OR TRIM(IMAGE) = ''")
empty_count = amazon_cur.fetchone()[0]

if empty_count > 0:
    print(f"🔧 Fixing {empty_count} items with empty image values...")
    amazon_cur.execute("UPDATE ITEMS SET IMAGE = NULL WHERE IMAGE = '' OR TRIM(IMAGE) = ''")
    amazon.commit()
    print(f"✅ Fixed {empty_count} items")

# Get final stats
amazon_cur.execute('SELECT COUNT(*) FROM ITEMS WHERE IMAGE IS NOT NULL')
with_images = amazon_cur.fetchone()[0]
amazon_cur.execute('SELECT COUNT(*) FROM ITEMS')
total = amazon_cur.fetchone()[0]

print(f"\n📊 Final Stats:")
print(f"  Total items: {total}")
print(f"  Items with valid images: {with_images}")
print(f"  Items without images: {total - with_images}")

amazon.close()
print("\n✅ Cleanup complete!")
