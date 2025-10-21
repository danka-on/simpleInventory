import sqlite3

barcode = "858557007115"

print("=" * 60)
print(f"Testing shelf code for barcode: {barcode}")
print("=" * 60)

# 1. Check searchRack.db
print("\n1. Checking searchRack.db:")
conn = sqlite3.connect('searchRack.db')
conn.row_factory = sqlite3.Row
cur = conn.cursor()
cur.execute('SELECT ID, BARCODE, ITEM_POSITION, PICTUREPOSITION, TITLE FROM SEARCHRACK WHERE BARCODE = ? COLLATE NOCASE', (barcode,))
rows = cur.fetchall()
if rows:
    for r in rows:
        print(f"   ID: {r['ID']}")
        print(f"   BARCODE: {r['BARCODE']}")
        print(f"   ITEM_POSITION: '{r['ITEM_POSITION']}'")
        print(f"   PICTUREPOSITION: '{r['PICTUREPOSITION']}'")
        print(f"   TITLE: {r['TITLE']}")
        print()
else:
    print(f"   No entries found for {barcode}")
conn.close()

# 2. Check bol.db items_prep_status
print("2. Checking bol.db items_prep_status:")
conn = sqlite3.connect('bol.db')
conn.row_factory = sqlite3.Row
cur = conn.cursor()
cur.execute('SELECT upc, location, pictureposition, updated_at FROM items_prep_status WHERE upc = ? COLLATE NOCASE', (barcode,))
row = cur.fetchone()
if row:
    print(f"   UPC: {row['upc']}")
    print(f"   location: '{row['location']}'")
    print(f"   pictureposition: '{row['pictureposition']}'")
    print(f"   updated_at: {row['updated_at']}")
else:
    print(f"   No entry found for {barcode}")
conn.close()

# 3. Check if shelf images exist
print("\n3. Checking shelf images:")
import os
shelf_codes = []
conn = sqlite3.connect('searchRack.db')
cur = conn.cursor()
cur.execute('SELECT DISTINCT ITEM_POSITION FROM SEARCHRACK WHERE BARCODE = ? COLLATE NOCASE', (barcode,))
for row in cur.fetchall():
    if row[0]:
        shelf_codes.append(row[0].lower())
conn.close()

if shelf_codes:
    for code in shelf_codes:
        png_path = f"static/shelves/{code}.png"
        exists = os.path.exists(png_path)
        status = "✓ EXISTS" if exists else "✗ MISSING"
        print(f"   {status}: {png_path}")
        if exists:
            size = os.path.getsize(png_path)
            print(f"            Size: {size:,} bytes")
else:
    print("   No shelf codes to check")

print("\n" + "=" * 60)
print("READY TO TEST:")
print("=" * 60)
print("1. Go to diagnostic view for this barcode")
print("2. Click 'Set Position' and change shelf code to a different one")
print("3. Submit and return to diagnostic")
print("4. Go to searchrack, search for this barcode")
print("5. Click the location link and verify the NEW shelf image loads")
