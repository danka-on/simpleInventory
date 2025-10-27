"""
Re-enrich searchRack.db with titles and images from ebayStore and bol databases
"""
import sys
sys.path.insert(0, '.')

from DBmanager import enrich_searchrack_db

print("🔄 Re-enriching searchRack.db...")
print("=" * 60)

try:
    enrich_searchrack_db(batch_size=500, do_backup=True)
    print("\n✅ SearchRack enrichment complete!")
    print("The inventory tab should now show titles and images.")
except Exception as e:
    print(f"\n❌ Error: {e}")
    import traceback
    traceback.print_exc()
