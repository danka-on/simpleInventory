import sqlite3

print('=== Checking Amazon Sold Items with ASIN as Barcode ===\n')

# Connect to sold.db
sold_conn = sqlite3.connect('sold.db')
sold_cur = sold_conn.cursor()

# Find Amazon items where barcode is still an ASIN (starts with B0 or is 10 chars)
sold_cur.execute('''
    SELECT order_id, title, barcode, item_id, store, paid_time
    FROM orders
    WHERE store = "amazon" 
    AND (barcode LIKE "B0%" OR LENGTH(barcode) = 10)
    ORDER BY paid_time DESC
''')

asin_items = sold_cur.fetchall()

print(f'Found {len(asin_items)} Amazon items with ASIN as barcode:\n')

for i, row in enumerate(asin_items[:10], 1):
    order_id, title, barcode, item_id, store, paid_time = row
    print(f'{i}. Order: {order_id}')
    print(f'   Title: {title[:50]}...')
    print(f'   Barcode: {barcode} (ASIN)')
    print(f'   ItemID: {item_id}')
    print(f'   Date: {paid_time}')
    print()

# Check if these ASINs have UPCs in amazonStore.db
amazon_conn = sqlite3.connect('amazonStore.db')
amazon_cur = amazon_conn.cursor()

print('\n=== Checking amazonStore.db for UPCs ===\n')

asins_to_check = [row[2] for row in asin_items[:5]]  # Check first 5

for asin in asins_to_check:
    amazon_cur.execute('SELECT ASIN, UPC, TITLE FROM ITEMS WHERE ASIN = ?', (asin,))
    amazon_row = amazon_cur.fetchone()
    
    if amazon_row:
        asin_found, upc, title = amazon_row
        print(f'ASIN: {asin}')
        print(f'  UPC in amazonStore: {upc}')
        print(f'  Match: {"✅ Can update!" if upc and upc != asin else "❌ No UPC available"}')
        print()
    else:
        print(f'ASIN: {asin}')
        print(f'  ❌ Not found in amazonStore.db')
        print()

sold_conn.close()
amazon_conn.close()

print('\n=== Summary ===')
print(f'Total Amazon sold items with ASIN as barcode: {len(asin_items)}')
print('These need to be backfilled with UPCs from amazonStore.db')
