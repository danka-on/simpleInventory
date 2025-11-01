"""
Diagnostic script to understand why sold items aren't matching rawbol items
"""
import sqlite3

print('=== Analyzing Match Failures ===\n')

sold_conn = sqlite3.connect('sold.db')
sold_cur = sold_conn.cursor()

rawbol_conn = sqlite3.connect('rawbol.db')
rawbol_cur = rawbol_conn.cursor()

# Get sample sold orders without LOT numbers
sold_cur.execute('''
    SELECT order_id, item_id, barcode, title
    FROM orders
    WHERE paid_time IS NOT NULL
    AND (lot_number IS NULL OR lot_number = '')
    LIMIT 20
''')
unmatched = sold_cur.fetchall()

print(f'Sample of {len(unmatched)} unmatched orders:\n')

for order in unmatched[:10]:
    order_id, item_id, barcode, title = order
    upc = barcode or item_id
    print(f'Order: {order_id}')
    print(f'  UPC/ID: {upc}')
    print(f'  Title: {title[:60] if title else "N/A"}...')
    
    # Check if UPC exists in rawbol
    if upc:
        rawbol_cur.execute('SELECT COUNT(*) FROM raw_bol_items WHERE upc = ? COLLATE NOCASE', (upc,))
        count = rawbol_cur.fetchone()[0]
        print(f'  In rawbol: {"✅ YES" if count > 0 else "❌ NO"}')
    else:
        print(f'  In rawbol: ⚠️ No UPC to check')
    print()

# Check total UPCs in rawbol
rawbol_cur.execute('SELECT COUNT(DISTINCT upc) FROM raw_bol_items')
total_upcs = rawbol_cur.fetchone()[0]
print(f'\nTotal unique UPCs in rawbol.db: {total_upcs}')

# Check sold items
sold_cur.execute('SELECT COUNT(*) FROM orders WHERE paid_time IS NOT NULL AND (barcode IS NOT NULL OR item_id IS NOT NULL)')
sold_with_upc = sold_cur.fetchone()[0]
print(f'Sold orders with UPC/ID: {sold_with_upc}')

sold_conn.close()
rawbol_conn.close()
