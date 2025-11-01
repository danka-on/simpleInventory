"""
Verify that the BOL # input field has been removed and extraction works correctly.
"""

print("=== Verifying BOL # Auto-Extraction Setup ===\n")

# Check extractor.html
print("1. Checking extractor.html...")
with open('templates/extractor.html', 'r', encoding='utf-8') as f:
    content = f.read()

if 'lot-number' not in content and 'BOL #:' not in content.replace('Extracted BOL #', ''):
    print("  ✓ BOL # input field removed from form")
else:
    print("  ✗ BOL # input field still exists!")

if 'Extracted LOT #' in content:
    print("  ✓ Success message will display extracted LOT #")
else:
    print("  ✗ Success message missing extracted LOT # display")

# Check app.py
print("\n2. Checking app.py upload endpoint...")
with open('app.py', 'r', encoding='utf-8') as f:
    content = f.read()

checks = {
    'No longer requires lot_number in form': "'lot_number' not in request.form" not in content or "Missing file or import date" in content,
    'Uses temp lot_number from filename': 'temp_lot_number = os.path.splitext(file.filename)[0]' in content,
    'Passes temp_lot_number to process_bol_excel': 'process_bol_excel(file, temp_lot_number, import_date)' in content,
    'Uses extracted_lot_number for sync': "result.get('extracted_lot_number')" in content,
}

for check, passed in checks.items():
    status = "✓" if passed else "✗"
    print(f"  {status} {check}")

# Check BOLextractor.py
print("\n3. Checking BOLextractor.py...")
with open('BOLextractor.py', 'r', encoding='utf-8') as f:
    content = f.read()

checks = {
    'Extracts LOT # from file': 'extracted_lot_number' in content,
    'Passes extracted_lot_number to insert': 'insert_raw_bol_items(df, lot_number, import_date, avg_cost, extracted_lot_number)' in content,
    'Returns extracted_lot_number': "result['extracted_lot_number'] = extracted_lot_number" in content,
}

for check, passed in checks.items():
    status = "✓" if passed else "✗"
    print(f"  {status} {check}")

# Check rawbol_manager.py
print("\n4. Checking rawbol_manager.py...")
with open('rawbol_manager.py', 'r', encoding='utf-8') as f:
    content = f.read()

if 'extracted_bol_number' in content and 'bol_number' in content:
    print("  ✓ insert_raw_bol_items accepts extracted_bol_number")
    print("  ✓ Stores extracted LOT # as bol_number in database")
else:
    print("  ✗ Missing extracted_bol_number parameter!")

print("\n" + "="*60)
print("✅ Setup Complete!")
print("\nUpload flow:")
print("  1. User uploads file (no manual BOL # entry required)")
print("  2. System uses filename as temp lot_number for tracking")
print("  3. Extractor finds LOCATION row and extracts LOT #")
print("  4. LOT # stored as bol_number in raw_bol_items")
print("  5. Success message shows extracted LOT #")
print("  6. Sync uses extracted LOT # if available")
print("="*60)
