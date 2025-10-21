import sqlite3

# Test barcode from user's report
barcode = "5060285600291"

print("=" * 60)
print(f"Testing location flow for barcode: {barcode}")
print("=" * 60)

# 1. Check searchRack.db
print("\n1. Checking searchRack.db:")
conn = sqlite3.connect('searchRack.db')
conn.row_factory = sqlite3.Row
cur = conn.cursor()
cur.execute('SELECT ID, BARCODE, ITEM_POSITION, PICTUREPOSITION FROM SEARCHRACK WHERE BARCODE = ? COLLATE NOCASE', (barcode,))
rows = cur.fetchall()
if rows:
    for r in rows:
        print(f"   ID: {r['ID']}, BARCODE: {r['BARCODE']}, ITEM_POSITION: '{r['ITEM_POSITION']}', PICTUREPOSITION: '{r['PICTUREPOSITION']}'")
else:
    print(f"   No entries found for {barcode}")
conn.close()

# 2. Check bol.db items_prep_status
print("\n2. Checking bol.db items_prep_status:")
conn = sqlite3.connect('bol.db')
conn.row_factory = sqlite3.Row
cur = conn.cursor()
cur.execute('SELECT upc, location, pictureposition, updated_at FROM items_prep_status WHERE upc = ? COLLATE NOCASE', (barcode,))
row = cur.fetchone()
if row:
    print(f"   UPC: {row['upc']}, location: '{row['location']}', pictureposition: '{row['pictureposition']}', updated_at: {row['updated_at']}")
else:
    print(f"   No entry found for {barcode}")
conn.close()

# 3. Test what the search API would return
print("\n3. Simulating /api/search/searchRack response:")
conn = sqlite3.connect('searchRack.db')
conn.row_factory = sqlite3.Row
cur = conn.cursor()
cur.execute('SELECT * FROM SEARCHRACK WHERE BARCODE = ? COLLATE NOCASE', (barcode,))
rows = cur.fetchall()
if rows:
    for r in rows:
        result = dict(r)
        print(f"   Result: {result}")
        # This is what gets sent to the frontend
        print(f"   - item_position would be: '{result.get('ITEM_POSITION', '')}' (lowercased: '{(result.get('ITEM_POSITION', '') or '').lower()}')")
        print(f"   - picture would be: '{result.get('PICTUREPOSITION', '')}'")
else:
    print(f"   No results would be returned")
conn.close()

print("\n" + "=" * 60)
print("DIAGNOSIS:")
print("=" * 60)
print("If ITEM_POSITION is empty/None, shelf code won't display.")
print("If PICTUREPOSITION has a value, picture position should display.")
print("Check if the data-item_position attribute is being set correctly")
print("in searchrack.html from the API response.")
