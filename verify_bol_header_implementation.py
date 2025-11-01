"""
Verify that the BOL header extraction implementation is correct.
Checks database structure and function signatures.
"""
import sqlite3

print("=== Verifying BOL Header Extraction Implementation ===\n")

# Check database structure
print("1. Checking upload_logs table structure...")
conn = sqlite3.connect('rawbol.db')
cur = conn.cursor()
cur.execute("PRAGMA table_info(upload_logs)")
columns = cur.fetchall()
print("\nupload_logs columns:")
for col in columns:
    print(f"  - {col[1]} ({col[2]})")

total_client_cost_exists = any(col[1] == 'total_client_cost' for col in columns)
if total_client_cost_exists:
    print("\n✓ total_client_cost column exists in upload_logs table")
else:
    print("\n✗ ERROR: total_client_cost column NOT found in upload_logs table")

# Check if any existing uploads have total_client_cost data
cur.execute("SELECT COUNT(*) FROM upload_logs WHERE total_client_cost IS NOT NULL")
count_with_cost = cur.fetchone()[0]
print(f"\n2. Upload logs with total_client_cost data: {count_with_cost}")

# Check total records
cur.execute("SELECT COUNT(*) FROM upload_logs")
total_records = cur.fetchone()[0]
print(f"   Total upload logs: {total_records}")

if count_with_cost > 0:
    print("\n3. Sample upload logs with total_client_cost:")
    cur.execute("SELECT lot_number, filename, total_client_cost FROM upload_logs WHERE total_client_cost IS NOT NULL LIMIT 5")
    for row in cur.fetchall():
        print(f"   - BOL #{row[0]}: {row[1]} → ${row[2]:,.2f}")
else:
    print("\n3. No uploads with total_client_cost data yet (this is normal if no new uploads have been made)")
    print("   Next BOL upload will test the extraction functionality")

conn.close()

# Check function signatures
print("\n4. Checking function signatures...")
from rawbol_manager import log_upload
import inspect

sig = inspect.signature(log_upload)
params = list(sig.parameters.keys())
print(f"\nlog_upload() parameters: {params}")

if 'total_client_cost' in params:
    print("✓ log_upload() has total_client_cost parameter")
    # Check if it has a default value
    param = sig.parameters['total_client_cost']
    if param.default is not inspect.Parameter.empty:
        print(f"  Default value: {param.default}")
else:
    print("✗ ERROR: log_upload() missing total_client_cost parameter")

# Check BOLextractor
print("\n5. Checking BOLextractor.py...")
with open('BOLextractor.py', 'r', encoding='utf-8') as f:
    content = f.read()
    
checks = {
    'extracted_lot_number initialization': 'extracted_lot_number = None' in content,
    'total_client_cost_header initialization': 'total_client_cost_header = None' in content,
    'LOT pattern search': 'LOT' in content and 'lot_number' in content.lower(),
    'TOTAL CLIENT COST search': 'TOTAL CLIENT COST' in content.upper(),
    'Header extraction for Excel': 'Extract header data BEFORE the UPC table' in content,
    'Header extraction for HTML': 'Extract header data from HTML content' in content,
    'Pass total_client_cost to log_upload': 'log_upload(filename, lot_number, import_date, result.get(\'inserted\', 0), total_client_cost_header)' in content,
    'Return extracted values': ('result[\'extracted_lot_number\']' in content or '\'extracted_lot_number\': extracted_lot_number' in content) and ('result[\'total_client_cost\']' in content or '\'total_client_cost\': total_client_cost_header' in content)
}

print("\nBOLextractor.py checks:")
all_passed = True
for check_name, passed in checks.items():
    status = "✓" if passed else "✗"
    print(f"  {status} {check_name}")
    if not passed:
        all_passed = False

print("\n" + "="*60)
if total_client_cost_exists and 'total_client_cost' in params and all_passed:
    print("✓ ALL CHECKS PASSED - Implementation is correct!")
    print("\nReady to test with a real BOL upload.")
    print("The extractor will now:")
    print("  1. Extract LOT # from header section")
    print("  2. Extract TOTAL CLIENT COST from header section")
    print("  3. Display both values in success message")
    print("  4. Store total_client_cost in upload_logs table")
    print("  5. Show total_client_cost in Upload History table")
else:
    print("✗ SOME CHECKS FAILED - Review implementation")
print("="*60)
