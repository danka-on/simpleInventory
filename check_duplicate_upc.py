import sqlite3

# Check for duplicate UPC 86279178893
conn = sqlite3.connect('searchRack.db')
cur = conn.cursor()

print('=== Checking UPC 86279178893 in searchRack.db ===\n')

cur.execute('''
    SELECT BARCODE, TITLE, ITEM_POSITION, QUANTITY, ITEMID, CREATED_AT 
    FROM SEARCHRACK 
    WHERE BARCODE = "86279178893"
    ORDER BY CREATED_AT DESC
''')

rows = cur.fetchall()

print(f'Found {len(rows)} entries:\n')

for i, row in enumerate(rows, 1):
    barcode, title, position, qty, itemid, date = row
    print(f'{i}. {title[:60] if title else "N/A"}...')
    print(f'   Location: {position}')
    print(f'   Quantity: {qty}')
    print(f'   ItemID: {itemid}')
    print(f'   Created: {date}')
    print()

# Check if this exists in rawbol.db (BOL data)
conn2 = sqlite3.connect('rawbol.db')
cur2 = conn2.cursor()

cur2.execute('''
    SELECT lot_number, quantity, import_date
    FROM raw_bol_items
    WHERE upc = "86279178893"
''')

bol_rows = cur2.fetchall()

print(f'\n=== BOL Data (rawbol.db) ===')
print(f'Found {len(bol_rows)} entries:\n')

for i, row in enumerate(bol_rows, 1):
    lot, qty, date = row
    print(f'{i}. LOT #{lot}, Qty: {qty}, Date: {date}')

conn.close()
conn2.close()

print('\n=== Analysis ===')
if len(rows) > 1:
    print('⚠️ DUPLICATE ENTRIES FOUND')
    print('This could be due to:')
    print('1. Item synced from multiple sources (eBay, Amazon, BOL)')
    print('2. Same item uploaded in different BOL batches')
    print('3. Manual additions')
    print('4. Sync ran multiple times without deduplication')
else:
    print('✅ No duplicates - single entry as expected')
