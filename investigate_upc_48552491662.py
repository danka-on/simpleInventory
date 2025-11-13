import sqlite3

print("=" * 80)
print("INVESTIGATING UPC: 48552491662")
print("=" * 80)

conn = sqlite3.connect('bol.db')
cur = conn.cursor()

# Find ALL entries for this UPC (including suffixed)
cur.execute('''
    SELECT 
        id,
        upc, 
        lot_number, 
        original_qty, 
        good_qty, 
        bad_qty, 
        unchecked_qty,
        quantity,
        temporary,
        import_date,
        list_status
    FROM bol_items 
    WHERE upc LIKE '48552491662%'
    ORDER BY upc
''')

entries = cur.fetchall()

print(f"\nFound {len(entries)} entries for UPC 48552491662:")
print("-" * 80)
print(f"{'ID':<6} | {'UPC':<20} | {'LOT':<12} | {'Orig':>4} | {'Good':>4} | {'Bad':>3} | {'Unch':>4} | {'Qty':>3} | {'Temp':>4} | Import Date")
print("-" * 80)

for row in entries:
    id_val, upc, lot, orig, good, bad, unch, qty, temp, imp_date, list_status = row
    print(f"{id_val:<6} | {upc:<20} | {(lot or 'NULL')[:12]:<12} | {orig or 0:>4} | {good or 0:>4} | {bad or 0:>3} | {unch or 0:>4} | {qty or 0:>3} | {temp or 0:>4} | {imp_date or 'N/A'}")

# Check items_prep_status
print("\n" + "=" * 80)
print("items_prep_status entries:")
print("-" * 80)

cur.execute('''
    SELECT upc, status, quantity, reason, note, updated_at
    FROM items_prep_status
    WHERE upc LIKE '48552491662%'
    ORDER BY upc, updated_at DESC
''')

prep_entries = cur.fetchall()

if prep_entries:
    for upc, status, qty, reason, note, updated in prep_entries:
        print(f"UPC: {upc}")
        print(f"  Status: {status}, Qty: {qty}, Updated: {updated}")
        if reason:
            print(f"  Reason: {reason}")
        if note:
            print(f"  Note: {note}")
        print()
else:
    print("No entries in items_prep_status")

# Check for duplicates in LOT
print("=" * 80)
print("Analysis:")
print("-" * 80)

base_entries = [e for e in entries if '-' not in e[1]]  # upc is index 1
suffixed_entries = [e for e in entries if '-' in e[1]]

print(f"Base entries (no suffix): {len(base_entries)}")
print(f"Suffixed entries (bad items): {len(suffixed_entries)}")

if len(base_entries) > 1:
    print("\n⚠️ ISSUE: Multiple base entries found!")
    print("This UPC exists in multiple LOTs:")
    for entry in base_entries:
        id_val, upc, lot, orig, good, bad, unch, qty, temp, imp_date, list_status = entry
        print(f"  - LOT {lot}: Original={orig}, Good={good}, Bad={bad}, Unchecked={unch}, Date={imp_date}")

# Check if this is showing up in Item Manager multiple times
print("\n" + "=" * 80)
print("Why showing in Item Manager multiple times:")
print("-" * 80)

if len(base_entries) > 1:
    print("✓ Multiple LOTs: This item appears in multiple shipments")
    print("  → Item Manager shows each LOT as a separate entry")
    print("  → This is EXPECTED behavior when same item received in multiple shipments")
    
    # Show which LOTs
    lots = {}
    for entry in base_entries:
        lot = entry[2]  # lot_number
        unch = entry[6]  # unchecked_qty
        if lot not in lots:
            lots[lot] = {'count': 0, 'unchecked': 0}
        lots[lot]['count'] += 1
        lots[lot]['unchecked'] += (unch or 0)
    
    print(f"\n  Found in {len(lots)} different LOTs:")
    for lot, data in lots.items():
        print(f"    LOT {lot}: {data['unchecked']} unchecked items")

if suffixed_entries:
    print(f"\n✓ Suffixed entries: {len(suffixed_entries)} bad item entries")
    print("  → These are separate from base entries")
    print("  → Created when items marked as bad")

# Recommendation
print("\n" + "=" * 80)
print("RECOMMENDATION:")
print("=" * 80)

if len(base_entries) > 1:
    print("""
This is NORMAL behavior! The same UPC can appear multiple times because:
1. You received it in different shipments (different LOTs)
2. Each LOT is tracked separately for inventory management
3. Item Manager shows all LOTs so you can prep each batch

This is actually CORRECT - it allows you to:
- Track which shipment each item came from
- Process items from newest LOT first (our new feature!)
- Calculate profit per LOT/shipment

No action needed unless you want to consolidate LOTs (not recommended).
""")
else:
    print("Only 1 base entry found - no duplicate issue detected.")

conn.close()
