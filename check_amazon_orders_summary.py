"""
Get summary of Amazon orders in sold.db
"""
import sqlite3

conn = sqlite3.connect('sold.db')
cur = conn.cursor()

# Get counts
cur.execute('''
    SELECT 
        COUNT(*) as total,
        SUM(CASE WHEN isHandled = 0 OR isHandled = '' OR isHandled IS NULL THEN 1 ELSE 0 END) as pending,
        SUM(CASE WHEN isHandled = 1 OR isHandled = '1' THEN 1 ELSE 0 END) as handled
    FROM orders 
    WHERE store = 'amazon'
''')
result = cur.fetchone()

print('=' * 60)
print('Amazon Orders Summary:')
print('=' * 60)
print(f'Total:   {result[0]}')
print(f'Pending: {result[1]} (ready to ship)')
print(f'Handled: {result[2]} (already processed)')
print()

# Show some sample pending orders if any
cur.execute('''
    SELECT id, order_id, title, barcode, image
    FROM orders
    WHERE store = 'amazon' AND (isHandled = 0 OR isHandled = '' OR isHandled IS NULL)
    LIMIT 5
''')
pending = cur.fetchall()

if pending:
    print('Sample Pending Orders:')
    print('-' * 60)
    for p in pending:
        has_title = '✓' if p[2] else '✗'
        has_barcode = '✓' if p[3] else '✗'
        has_image = '✓' if p[4] else '✗'
        print(f'ID {p[0]}: {p[1][:30]}')
        print(f'  Title: {has_title} | Barcode: {has_barcode} | Image: {has_image}')

conn.close()
