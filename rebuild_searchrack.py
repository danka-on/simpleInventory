"""
Rebuild/refresh searchRack.db enrichment from store databases
Note: searchRack.db is now the primary database. This script only refreshes enrichment data.
"""
import sys
sys.path.insert(0, '.')

from DBmanager import createSearchRackDB, enrich_searchrack_db

print("🔄 Refreshing searchRack.db enrichment...")
print("=" * 60)

# Step 1: Ensure schema exists
print("\n1️⃣ Verifying searchRack.db schema...")
createSearchRackDB()

# Step 2: Enrich with titles/images from eBay, BOL, Amazon
print("\n2️⃣ Enriching with titles and images from store databases...")
enrich_searchrack_db(batch_size=500, do_backup=False)

print("\n✅ SearchRack enrichment complete!")

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
