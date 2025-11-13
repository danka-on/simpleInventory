import sqlite3
import re

print("=" * 80)
print("SCANNING FOR ORPHANED SUFFIXED ENTRIES ACROSS ALL UPCS")
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
        'temporary': temp,
        'original_qty': orig,
        'good_qty': good,
        'bad_qty': bad,
        'unchecked_qty': unch
    })

print(f"\nFound {len(orphaned)} orphaned suffixed entries across all UPCs:")
print("-" * 80)
for base_upc, entries in orphans_by_base.items():
    print(f"Base UPC: {base_upc} - {len(entries)} orphaned suffixed entries:")
    for entry in entries:
        print(f"  ID {entry['id']}: {entry['upc']} (LOT {entry['lot']}) - All quantities zero, temporary={entry['temporary']}")
    print()

if not orphaned:
    print("No orphaned entries found.")

conn.close()
