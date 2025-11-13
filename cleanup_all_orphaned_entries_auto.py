import sqlite3
import re

print("=" * 80)
print("CLEANING UP ALL ORPHANED SUFFIXED ENTRIES")
print("=" * 80)

conn = sqlite3.connect('bol.db')
cur = conn.cursor()

# Find all suffixed UPCs (ending in -1, -2, etc.) that are temporary and all quantities zero
cur.execute('''
    SELECT id, upc, lot_number, temporary, original_qty, good_qty, bad_qty, unchecked_qty
    FROM bol_items 
    WHERE upc LIKE '%-%'
    AND temporary = 1
    AND (original_qty = 0 OR original_qty IS NULL)
    AND (good_qty = 0 OR good_qty IS NULL)
    AND (bad_qty = 0 OR bad_qty IS NULL)
    AND (unchecked_qty = 0 OR unchecked_qty IS NULL)
''')

orphaned = cur.fetchall()

# Group by base UPC
orphans_by_base = {}
for row in orphaned:
    id_val, upc, lot, temp, orig, good, bad, unch = row
    base_upc = re.sub(r'-\d+$', '', upc)
    if base_upc not in orphans_by_base:
        orphans_by_base[base_upc] = []
    orphans_by_base[base_upc].append({
        'id': id_val,
        'upc': upc,
        'lot': lot,
        'temporary': temp
    })

print(f"\nFound {len(orphaned)} orphaned suffixed entries to delete:")
print("-" * 80)
for base_upc, entries in orphans_by_base.items():
    print(f"Base UPC: {base_upc} - {len(entries)} orphaned suffixed entries:")
    for entry in entries:
        print(f"  ID {entry['id']}: {entry['upc']} (LOT {entry['lot']})")
print()

if not orphaned:
    print("No orphaned entries found.")
    conn.close()
    exit(0)

print(f"\nDeleting all {len(orphaned)} orphaned entries...")

deleted_count = 0
for row in orphaned:
    id_val = row[0]
    upc = row[1]
    
    # Delete from bol_items
    cur.execute('DELETE FROM bol_items WHERE id = ?', (id_val,))
    
    # Also delete any related prep data
    cur.execute('DELETE FROM items_prep_status WHERE upc = ?', (upc,))
    cur.execute('DELETE FROM items_prep_images WHERE upc = ?', (upc,))
    try:
        cur.execute('DELETE FROM items_prep_notes WHERE upc = ?', (upc,))
    except:
        pass
    
    deleted_count += 1
    print(f"  ✓ Deleted {upc} (ID {id_val})")

conn.commit()

print(f"\n✅ Deleted {deleted_count} orphaned entries and their related prep data")

# Verify cleanup
cur.execute('''
    SELECT COUNT(*) FROM bol_items 
    WHERE upc LIKE '%-%'
    AND temporary = 1
    AND (original_qty = 0 OR original_qty IS NULL)
    AND (good_qty = 0 OR good_qty IS NULL)
    AND (bad_qty = 0 OR bad_qty IS NULL)
    AND (unchecked_qty = 0 OR unchecked_qty IS NULL)
''')
remaining = cur.fetchone()[0]
print(f"✓ Remaining orphaned suffixed entries: {remaining}")

conn.close()
