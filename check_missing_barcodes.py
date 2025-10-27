"""
Check which searchRack barcodes are missing titles and see if they exist in bol.db
"""
import sqlite3

# Get searchRack items without titles
searchrack = sqlite3.connect('searchRack.db')
cursor = searchrack.cursor()
cursor.execute("SELECT BARCODE FROM SEARCHRACK WHERE TITLE IS NULL AND BARCODE IS NOT NULL")
missing_barcodes = [row[0] for row in cursor.fetchall()]
searchrack.close()

print(f"Found {len(missing_barcodes)} searchRack items without titles")
print(f"Unique barcodes: {len(set(missing_barcodes))}")
print("\nChecking in bol.db...")

# Check if these exist in bol.db
bol = sqlite3.connect('bol.db')
bol_cursor = bol.cursor()

found_in_bol = 0
not_found = []

for barcode in set(missing_barcodes):
    bol_cursor.execute("SELECT upc, item_description FROM bol_items WHERE upc = ?", (barcode,))
    result = bol_cursor.fetchone()
    if result:
        found_in_bol += 1
        print(f"  ✅ {barcode}: {result[1][:50]}...")
    else:
        not_found.append(barcode)

print(f"\n📊 Results:")
print(f"  Found in bol.db: {found_in_bol}")
print(f"  Not found anywhere: {len(not_found)}")

if not_found:
    print(f"\n❌ Barcodes not in bol.db or ebayStore.db:")
    for bc in not_found:
        print(f"    {bc}")

bol.close()
