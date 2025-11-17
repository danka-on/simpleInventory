"""
Check details of LOT# 16578199 in bol.db to see if it would appear in stats
"""
import sqlite3

conn = sqlite3.connect('bol.db')
conn.row_factory = sqlite3.Row
cur = conn.cursor()

print("=" * 80)
print("Checking LOT# 16578199 Details")
print("=" * 80)

# Check if columns exist
cur.execute('PRAGMA table_info(bol_items)')
cols = [col[1] for col in cur.fetchall()]
print(f"\nAvailable columns: {', '.join(cols)}")

# Check the actual data for this LOT
cur.execute('''
    SELECT 
        COUNT(*) as count,
        COUNT(DISTINCT upc) as unique_upcs,
        MAX(import_date) as latest_date,
        SUM(COALESCE(original_qty, quantity, 0)) as total_qty,
        SUM(COALESCE(good_qty, 0)) as good_qty,
        SUM(COALESCE(bad_qty, 0)) as bad_qty,
        SUM(COALESCE(unchecked_qty, 0)) as unchecked_qty,
        MIN(temporary) as min_temp,
        MAX(temporary) as max_temp
    FROM bol_items
    WHERE lot_number = '16578199'
''')

result = cur.fetchone()

print(f"\n📊 LOT# 16578199 Stats:")
print(f"   Total rows: {result['count']}")
print(f"   Unique UPCs: {result['unique_upcs']}")
print(f"   Latest import date: {result['latest_date']}")
print(f"   Total original qty: {result['total_qty']}")
print(f"   Good qty: {result['good_qty']}")
print(f"   Bad qty: {result['bad_qty']}")
print(f"   Unchecked qty: {result['unchecked_qty']}")
print(f"   Temporary flag range: {result['min_temp']} to {result['max_temp']}")

# Check if any have UPC with dash (suffixed)
cur.execute('''
    SELECT COUNT(*) as suffixed_count
    FROM bol_items
    WHERE lot_number = '16578199'
    AND upc LIKE '%-%'
''')
suffixed = cur.fetchone()['suffixed_count']
print(f"   Items with suffixed UPC: {suffixed}")

# Sample a few items
print(f"\n📦 Sample items (first 5):")
cur.execute('''
    SELECT upc, item_description, quantity, original_qty, good_qty, bad_qty, unchecked_qty, temporary, import_date
    FROM bol_items
    WHERE lot_number = '16578199'
    LIMIT 5
''')
for i, row in enumerate(cur.fetchall(), 1):
    print(f"\n   {i}. UPC: {row['upc']}")
    print(f"      Title: {row['item_description'][:60] if row['item_description'] else 'N/A'}")
    print(f"      Qty: {row['quantity']}, Original: {row['original_qty']}, Good: {row['good_qty']}, Bad: {row['bad_qty']}, Unchecked: {row['unchecked_qty']}")
    print(f"      Temporary: {row['temporary']}, Import Date: {row['import_date']}")

# Check what the API query would return
print(f"\n🔍 Checking if LOT would appear in API response:")
cur.execute('''
    SELECT 
        lot_number,
        MAX(import_date) as latest_date,
        COUNT(DISTINCT upc) as unique_items
    FROM bol_items
    WHERE (temporary IS NULL OR temporary = 0)
        AND upc NOT LIKE '%-%'
        AND lot_number = '16578199'
    GROUP BY lot_number
''')
api_result = cur.fetchone()

if api_result:
    print(f"✅ LOT WOULD APPEAR in stats page!")
    print(f"   Lot Number: {api_result['lot_number']}")
    print(f"   Latest Date: {api_result['latest_date']}")
    print(f"   Unique Items: {api_result['unique_items']}")
else:
    print(f"❌ LOT WOULD NOT APPEAR in stats page")
    print(f"   Possible reasons:")
    print(f"   - All items have temporary=1")
    print(f"   - All UPCs have dashes (suffixed)")
    print(f"   - Lot number is NULL, empty, or invalid")

conn.close()

print("\n" + "=" * 80)
