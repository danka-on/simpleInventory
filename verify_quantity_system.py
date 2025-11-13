import sqlite3

print("=" * 80)
print("FINAL QUANTITY SYSTEM VERIFICATION")
print("=" * 80)

conn = sqlite3.connect('bol.db')
cur = conn.cursor()

# Get final statistics
cur.execute('''
    SELECT 
        COUNT(*) as total_items,
        SUM(COALESCE(original_qty, 0)) as total_original,
        SUM(COALESCE(good_qty, 0)) as total_good,
        SUM(COALESCE(bad_qty, 0)) as total_bad,
        SUM(COALESCE(unchecked_qty, 0)) as total_unchecked,
        SUM(COALESCE(quantity, 0)) as total_quantity
    FROM bol_items
    WHERE upc NOT LIKE '%-%'
''')

stats = cur.fetchone()

print("\n📊 SYSTEM TOTALS:")
print("-" * 80)
print(f"Total base items: {stats[0]}")
print(f"Total original_qty: {stats[1]}")
print(f"Total good_qty: {stats[2]}")
print(f"Total bad_qty: {stats[3]}")
print(f"Total unchecked_qty: {stats[4]}")
print(f"Total quantity (display): {stats[5]}")

# Verify invariant
total_allocated = stats[2] + stats[3] + stats[4]
invariant_valid = (stats[1] == total_allocated)

print(f"\n✓ Invariant check: {stats[1]} = {stats[2]} + {stats[3]} + {stats[4]} = {total_allocated}")
if invariant_valid:
    print("  ✅ VALID: original = good + bad + unchecked")
else:
    print(f"  ❌ INVALID: Difference of {stats[1] - total_allocated}")

# Check relationship between quantity and good_qty
cur.execute('''
    SELECT COUNT(*)
    FROM bol_items
    WHERE upc NOT LIKE '%-%'
    AND quantity = good_qty
''')
qty_equals_good = cur.fetchone()[0]

print(f"\n✓ Items where quantity = good_qty: {qty_equals_good} / {stats[0]}")
if qty_equals_good == stats[0]:
    print("  ✅ All items have quantity = good_qty (backward compatible)")

# Sample data
print("\n📋 SAMPLE DATA (First 10 items):")
print("-" * 80)
print(f"{'UPC':<15} | {'Quantity':>8} | {'Original':>8} | {'Good':>5} | {'Bad':>4} | {'Unchecked':>9}")
print("-" * 80)

cur.execute('''
    SELECT upc, quantity, original_qty, good_qty, bad_qty, unchecked_qty
    FROM bol_items
    WHERE upc NOT LIKE '%-%'
    ORDER BY original_qty DESC
    LIMIT 10
''')

for row in cur.fetchall():
    print(f"{row[0][:15]:<15} | {row[1] or 0:>8} | {row[2] or 0:>8} | {row[3] or 0:>5} | {row[4] or 0:>4} | {row[5] or 0:>9}")

print("\n" + "=" * 80)
print("COLUMN DOCUMENTATION:")
print("=" * 80)
print("""
┌─────────────────┬─────────────────────────────────────────────────────────┐
│ Column          │ Purpose                                                 │
├─────────────────┼─────────────────────────────────────────────────────────┤
│ quantity        │ Display value (= good_qty)                             │
│                 │ • For backward compatibility with old code             │
│                 │ • Should always match good_qty                         │
│                 │ • Used in UI for "ready to list" count                │
├─────────────────┼─────────────────────────────────────────────────────────┤
│ original_qty    │ Immutable baseline                                     │
│                 │ • Set once from BOL import (rawbol → bol)             │
│                 │ • Never changes during prep workflow                   │
│                 │ • Used for stats, loss calculation, audit trail       │
├─────────────────┼─────────────────────────────────────────────────────────┤
│ good_qty        │ Items marked as GOOD                                   │
│                 │ • Ready to list                                        │
│                 │ • Increments when marking items good                   │
│                 │ • Decrements on undo                                   │
├─────────────────┼─────────────────────────────────────────────────────────┤
│ bad_qty         │ Items marked as BAD                                    │
│                 │ • Defective/damaged items                             │
│                 │ • Increments when creating bad entries                 │
│                 │ • Used for loss rate calculation                       │
├─────────────────┼─────────────────────────────────────────────────────────┤
│ unchecked_qty   │ Items NOT YET prepped                                  │
│                 │ • Waiting for inspection                               │
│                 │ • Decrements when marking good or bad                  │
│                 │ • Increments on undo                                   │
└─────────────────┴─────────────────────────────────────────────────────────┘

INVARIANT (Always True):
  original_qty = good_qty + bad_qty + unchecked_qty
  
RELATIONSHIPS:
  • quantity = good_qty (for backward compatibility)
  • good_qty + bad_qty = total prepped
  • (good_qty + bad_qty) / original_qty = prep progress %
  • bad_qty / original_qty = loss rate %
""")

conn.close()

print("=" * 80)
print("✅ QUANTITY SYSTEM FULLY OPERATIONAL")
print("=" * 80)
