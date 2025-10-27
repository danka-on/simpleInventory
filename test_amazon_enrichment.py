"""
Test the updated enrich_searchrack_db function with Amazon support
"""
import sys
sys.path.insert(0, '.')

from DBmanager import enrich_searchrack_db

print("🔄 Testing searchRack enrichment with Amazon support...")
print("=" * 80)

try:
    enrich_searchrack_db(batch_size=500, do_backup=True)
    print("\n✅ Enrichment complete!")
except Exception as e:
    print(f"\n❌ Error: {e}")
    import traceback
    traceback.print_exc()

# Verify results
import sqlite3
conn = sqlite3.connect('searchRack.db')
cur = conn.cursor()

cur.execute('SELECT COUNT(*) FROM SEARCHRACK')
total = cur.fetchone()[0]

cur.execute('SELECT COUNT(*) FROM SEARCHRACK WHERE TITLE IS NOT NULL')
with_titles = cur.fetchone()[0]

cur.execute('SELECT COUNT(*) FROM SEARCHRACK WHERE IMAGE IS NOT NULL')
with_images = cur.fetchone()[0]

print(f"\n📊 SearchRack Stats:")
print(f"  Total items: {total}")
print(f"  Items with titles: {with_titles}")
print(f"  Items with images: {with_images}")

# Show sample of enriched items
print(f"\n📋 Sample enriched items:")
cur.execute("""
    SELECT BARCODE, TITLE, ITEMID, IMAGE 
    FROM SEARCHRACK 
    WHERE TITLE IS NOT NULL 
    LIMIT 10
""")
for row in cur.fetchall():
    barcode, title, itemid, image = row
    title_display = (title[:50] + "...") if title and len(title) > 50 else (title or "None")
    has_image = "✅" if image else "❌"
    print(f"  {has_image} {barcode} | {itemid or 'No ID'} | {title_display}")

conn.close()
