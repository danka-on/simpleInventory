import sqlite3

print("=" * 100)
print("DETAILED REPORT: DUPLICATES THAT WERE CONSOLIDATED")
print("=" * 100)
print("\nThis shows what was merged during the consolidation process.")
print("Review these to verify if they are legitimate duplicates or Excel file errors.\n")

# We need to query the original data before consolidation
# Since we already consolidated, we'll show what WOULD have been duplicates
# by looking at the upload logs and checking the Excel files

conn = sqlite3.connect('rawbol.db')
cur = conn.cursor()

# Get the consolidated items we identified earlier
duplicates_consolidated = {
    '400013532749': {
        'LOT 16264617': {'entries': 8, 'quantities': [1, 4, 4, 27, 4, 4, 12, 27], 'total': 83},
        'LOT 16448708': {'entries': 8, 'quantities': [9, 8, 11, 2, 9, 10, 7, 23], 'total': 79},
        'LOT 16578199': {'entries': 8, 'quantities': [11, 10, 12, 23, 4, 7, 6, 7], 'total': 80}
    },
    '48552491662': {
        'LOT 16578199': {'entries': 5, 'quantities': [1, 1, 4, 1, 5], 'total': 12},
        'LOT 16448708': {'entries': 2, 'quantities': [1, 1], 'total': 2}
    },
    '48552584265': {
        'LOT 16448708': {'entries': 4, 'quantities': [1, 1, 1, 1], 'total': 4},
        'LOT 16578199': {'entries': 4, 'quantities': [1, 1, 1, 1], 'total': 4}
    },
    '48552590518': {
        'LOT 16448708': {'entries': 4, 'quantities': [4, 2, 1, 1], 'total': 8}
    },
    '28199274910': {
        'LOT 16264617': {'entries': 3, 'quantities': [1, 1, 1], 'total': 3},
        'LOT 16578199': {'entries': 2, 'quantities': [3, 1], 'total': 4}
    },
    '400011639440': {
        'LOT 16448708': {'entries': 3, 'quantities': [1, 15, 1], 'total': 17}
    },
    '48552722612': {
        'LOT 16578199': {'entries': 3, 'quantities': [6, 2, 1], 'total': 9}
    },
    '48552733311': {
        'LOT 16578199': {'entries': 3, 'quantities': [1, 1, 1], 'total': 3}
    },
    '86279178893': {
        'LOT 16448708': {'entries': 3, 'quantities': [1, 1, 1], 'total': 3}
    }
}

# Get item descriptions for each UPC
print("TOP DUPLICATES (showing first 20 with most entries):\n")
print("-" * 100)

count = 0
for upc, lots_data in duplicates_consolidated.items():
    # Get description from current database
    cur.execute('SELECT item_description, quantity FROM raw_bol_items WHERE upc = ? LIMIT 1', (upc,))
    row = cur.fetchone()
    desc = row[0] if row else 'N/A'
    current_qty = row[1] if row else 0
    
    for lot, dup_data in lots_data.items():
        count += 1
        if count > 20:
            break
        
        print(f"\n{count}. UPC: {upc}")
        print(f"   Description: {desc}")
        print(f"   {lot}")
        print(f"   Original entries: {dup_data['entries']}")
        print(f"   Individual quantities: {dup_data['quantities']}")
        print(f"   Consolidated to: {dup_data['total']}")
        print(f"   Current quantity in DB: {current_qty}")
        
        # Analysis
        all_same = len(set(dup_data['quantities'])) == 1
        if all_same:
            print(f"   ⚠️  SUSPICIOUS: All {dup_data['entries']} entries had SAME quantity ({dup_data['quantities'][0]})")
            print(f"       → Likely Excel error - same item listed {dup_data['entries']} times")
        else:
            print(f"   ℹ️  VARIED: Different quantities on each row")
            print(f"       → Could be legitimate (multiple boxes/pallets) or Excel error")
    
    if count > 20:
        break

print("\n" + "=" * 100)
print("\nREMAINING DUPLICATES (showing summary):")
print("-" * 100)

remaining = [
    ('13302451029', 'LOT 16264617', 2, [1, 2]),
    ('194137133059', 'LOT 16578199', 2, [2, 1]),
    ('194137133066', 'LOT 16264617', 2, [2, 2]),
    ('194137133080', 'LOT 16578199', 2, [4, 1]),
    ('194137133431', 'LOT 16578199', 2, [1, 1]),
    ('194137198225', 'LOT 16448708', 2, [6, 5]),
    ('194590060978', 'LOT 16448708', 2, [2, 1]),
    ('194590199524', 'LOT 16578199', 2, [2, 1]),
    ('21864337323', 'LOT 16264617', 2, [1, 1]),
    ('25398232475', 'LOT 16264617', 2, [2, 1]),
    ('25398243808', 'LOT 16264617', 2, [1, 1]),
    ('28199256657', 'LOT 16448708', 2, [1, 1]),
    ('28199257296', 'LOT 16448708', 2, [1, 1]),
    ('28199260364', 'LOT 16264617', 2, [1, 2]),
    ('28199291566', 'LOT 16448708', 2, [1, 1]),
    ('28225897489', 'LOT 16578199', 2, [1, 1]),
    ('28332846226', 'LOT 16264617', 2, [1, 1]),
    ('35886129972', 'LOT 16448708', 2, [1, 1]),
    ('35886214388', 'LOT 16264617', 2, [1, 1]),
    ('35886385323', 'LOT 16264617', 2, [1, 1]),
    ('35886385330', 'LOT 16578199', 2, [1, 1]),
    ('35886387624', 'LOT 16578199', 2, [1, 1]),
    ('35886439255', 'LOT 16448708', 2, [1, 1]),
    ('35886482893', 'LOT 16448708', 2, [2, 2]),
    ('35886645564', 'LOT 16264617', 2, [2, 1]),
    ('37725606613', 'LOT 16264617', 2, [1, 1]),
    ('37725607597', 'LOT 16264617', 2, [1, 1]),
    ('45908144173', 'LOT 16448708', 2, [1, 1]),
    ('47596030349', 'LOT 16448708', 2, [1, 3]),
    ('47596043677', 'LOT 16448708', 2, [1, 1]),
    ('47596142387', 'LOT 16264617', 2, [1, 1]),
    ('47596162637', 'LOT 16264617', 2, [1, 1]),
    ('48552397896', 'LOT 16448708', 2, [1, 1]),
    ('48552488846', 'LOT 16578199', 2, [1, 1]),
    ('48552527729', 'LOT 16264617', 2, [1, 1]),
    ('48552604673', 'LOT 16578199', 2, [1, 1]),
    ('48552703161', 'LOT 16448708', 2, [1, 1]),
    ('48552722650', 'LOT 16578199', 2, [3, 1]),
    ('692786820790', 'LOT 16264617', 2, [2, 1]),
    ('701587159432', 'LOT 16264617', 2, [1, 1]),
    ('701587447119', 'LOT 16264617', 2, [1, 0]),
    ('715021869726', 'LOT 16578199', 2, [1, 1]),
    ('715021871606', 'LOT 16264617', 2, [1, 1]),
    ('72456117281', 'LOT 16578199', 2, [1, 1]),
    ('730384895007', 'LOT 16264617', 2, [1, 2]),
    ('731742133144', 'LOT 16578199', 2, [1, 1]),
    ('733652135973', 'LOT 16264617', 2, [1, 2]),
    ('76440141368', 'LOT 16578199', 2, [1, 1]),
    ('790824227911', 'LOT 16578199', 2, [1, 1]),
    ('790824320919', 'LOT 16448708', 2, [1, 1]),
    ('790824462220', 'LOT 16578199', 2, [1, 1]),
    ('810071428500', 'LOT 16448708', 2, [1, 2]),
    ('840191207679', 'LOT 16578199', 2, [1, 1]),
    ('88235775368', 'LOT 16448708', 2, [1, 1]),
    ('882864264909', 'LOT 16264617', 2, [1, 1]),
    ('882864277947', 'LOT 16264617', 2, [1, 1]),
    ('882864391216', 'LOT 16264617', 2, [2, 1]),
    ('882864435019', 'LOT 16264617', 2, [1, 1]),
    ('882864646866', 'LOT 16448708', 2, [1, 1]),
    ('882864841001', 'LOT 16448708', 2, [1, 1]),
    ('885991045229', 'LOT 16578199', 2, [1, 1]),
    ('885991196938', 'LOT 16264617', 2, [1, 1]),
    ('885991245780', 'LOT 16264617', 2, [1, 1]),
    ('99967248372', 'LOT 16578199', 2, [1, 2])
]

suspicious_count = 0
for upc, lot, entries, qtys in remaining:
    all_same = len(set(qtys)) == 1 and qtys[0] == 1
    if all_same:
        suspicious_count += 1

print(f"\nRemaining {len(remaining)} UPCs with 2 entries each:")
print(f"  - {suspicious_count} with identical qty=1 (likely Excel errors)")
print(f"  - {len(remaining) - suspicious_count} with varied quantities (possibly legitimate)")

print("\n" + "=" * 100)
print("\nRECOMMENDATION:")
print("  1. UPCs with IDENTICAL quantities (esp. all 1s) → Likely Excel errors")
print("  2. UPCs with VARIED quantities → Could be legitimate (check your Excel files)")
print("  3. Review your original Excel files to confirm")
print("=" * 100)

conn.close()
