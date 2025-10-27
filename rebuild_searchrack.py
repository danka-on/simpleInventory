"""
Rebuild searchRack.db properly from rack.db
"""
import sys
sys.path.insert(0, '.')

from DBmanager import createSearchRackDB, updateSearchRackDB, enrich_searchrack_db

print("🔄 Rebuilding searchRack.db...")
print("=" * 60)

# Step 1: Create/recreate the schema
print("\n1️⃣ Creating searchRack.db schema...")
createSearchRackDB()

# Step 2: Populate from rack.db
print("\n2️⃣ Populating from rack.db...")
updateSearchRackDB()

# Step 3: Enrich with titles/images
print("\n3️⃣ Enriching with titles and images...")
enrich_searchrack_db(batch_size=500, do_backup=False)

print("\n✅ SearchRack rebuild complete!")

# Verify
import sqlite3
conn = sqlite3.connect('searchRack.db')
cur = conn.cursor()
cur.execute('SELECT COUNT(*) FROM SEARCHRACK')
total = cur.fetchone()[0]
cur.execute('SELECT COUNT(*) FROM SEARCHRACK WHERE TITLE IS NOT NULL')
with_titles = cur.fetchone()[0]
conn.close()

print(f"\n📊 Results:")
print(f"  Total items: {total}")
print(f"  Items with titles: {with_titles}")
