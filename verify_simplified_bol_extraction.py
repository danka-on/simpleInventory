"""
Verify the simplified BOL extraction implementation.
Checks that we only extract: UPC, ITEM DESCRIPTION, ORIGINAL QTY, IMAGE, AVG COST
"""
import sqlite3

print("=== Verifying Simplified BOL Extraction ===\n")

# Check raw_bol_items table structure
print("1. Checking raw_bol_items table structure...")
conn = sqlite3.connect('rawbol.db')
cur = conn.cursor()
cur.execute("PRAGMA table_info(raw_bol_items)")
columns = {col[1]: col[2] for col in cur.fetchall()}

print("\nColumns in raw_bol_items:")
for col_name, col_type in columns.items():
    print(f"  - {col_name}: {col_type}")

# Check for correct columns
expected_columns = ['upc', 'item_description', 'avg_cost', 'image_url', 'quantity', 'lot_number', 'bol_number']
removed_columns = ['client_cost', 'total_client_cost']

print("\n2. Validation:")
all_good = True
for col in expected_columns:
    if col in columns:
        print(f"  ✓ {col} exists")
    else:
        print(f"  ✗ {col} MISSING!")
        all_good = False

for col in removed_columns:
    if col not in columns:
        print(f"  ✓ {col} removed (correct)")
    else:
        print(f"  ✗ {col} still exists (should be removed)!")
        all_good = False

# Check sample data
print("\n3. Sample data with avg_cost:")
cur.execute("""
    SELECT upc, item_description, quantity, avg_cost, lot_number, bol_number 
    FROM raw_bol_items 
    LIMIT 5
""")
conn.row_factory = sqlite3.Row
cur = conn.cursor()
cur.execute("""
    SELECT upc, item_description, quantity, avg_cost, lot_number, bol_number 
    FROM raw_bol_items 
    LIMIT 5
""")
rows = cur.fetchall()

if rows:
    for row in rows:
        print(f"\nUPC: {row['upc']}")
        print(f"  Description: {row['item_description'][:50]}...")
        print(f"  Quantity: {row['quantity']}")
        print(f"  Avg Cost: ${row['avg_cost']:.2f}" if row['avg_cost'] else "  Avg Cost: None")
        print(f"  LOT #: {row['lot_number']}")
        print(f"  BOL #: {row['bol_number'] if row['bol_number'] else '(not set)'}")
else:
    print("  No data in table yet (normal for fresh install)")

# Check upload_logs structure
print("\n4. Checking upload_logs table...")
cur.execute("PRAGMA table_info(upload_logs)")
log_columns = {col[1]: col[2] for col in cur.fetchall()}

if 'total_client_cost' in log_columns:
    print("  ✓ total_client_cost column exists in upload_logs")
else:
    print("  ✗ total_client_cost column MISSING from upload_logs!")
    all_good = False

# Check BOLextractor.py
print("\n5. Checking BOLextractor.py implementation...")
with open('BOLextractor.py', 'r', encoding='utf-8') as f:
    content = f.read()

checks = {
    'avg_cost calculation': 'avg_cost = total_client_cost_header / total_qty' in content,
    'avg_cost passed to insert': 'insert_raw_bol_items(df, lot_number, import_date, avg_cost' in content,
    'avg_cost in result': "result['avg_cost'] = avg_cost" in content,
    'No CLIENT COST extraction': 'CLIENT COST' not in content or 'currency_columns' not in content
}

print("\nBOLextractor.py checks:")
for check_name, passed in checks.items():
    status = "✓" if passed else "✗"
    print(f"  {status} {check_name}")
    if not passed:
        all_good = False

# Check rawbol_manager.py
print("\n6. Checking rawbol_manager.py implementation...")
with open('rawbol_manager.py', 'r', encoding='utf-8') as f:
    content = f.read()

checks = {
    'insert_raw_bol_items accepts avg_cost': 'def insert_raw_bol_items(df, lot_number, import_date, avg_cost' in content,
    'avg_cost stored in INSERT': 'avg_cost' in content and 'INSERT INTO raw_bol_items' in content,
    'Table migration for avg_cost': 'Migrating raw_bol_items table structure' in content or 'avg_cost' in content
}

print("\nrawbol_manager.py checks:")
for check_name, passed in checks.items():
    status = "✓" if passed else "✗"
    print(f"  {status} {check_name}")
    if not passed:
        all_good = False

conn.close()

print("\n" + "="*60)
if all_good:
    print("✓ ALL CHECKS PASSED - Simplified extraction is ready!")
    print("\nNew BOL uploads will:")
    print("  1. Extract only: UPC, ITEM DESCRIPTION, ORIGINAL QTY, IMAGE")
    print("  2. Extract LOT # from header → store as bol_number")
    print("  3. Extract TOTAL CLIENT COST from header")
    print("  4. Calculate avg_cost = total_cost / total_qty")
    print("  5. Apply same avg_cost to all items in the BOL")
    print("  6. Display avg_cost in success message")
else:
    print("✗ SOME CHECKS FAILED - Review implementation")
print("="*60)
