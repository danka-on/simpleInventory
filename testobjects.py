import sqlite3
from datetime import datetime

# Connect to the sold.db database
conn = sqlite3.connect('sold.db')
cursor = conn.cursor()

# Insert a test order
cursor.execute('''
    INSERT INTO orders (
        title, quantity, image, shipping_name, shipping_street1, 
        shipping_city, shipping_state, shipping_postal_code, shipping_country, 
        checkout_status, isHandled, paid_time
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
''', (
    'item test',
    2,
    'https://i.ebayimg.com/images/g/vJgAAOSwNFNmT1Xx/s-l1600.jpg',
    'test',
    'test address',
    '', '', '', '',
    'Complete',
    '0',
    datetime.now().strftime('%Y-%m-%d %H:%M:%S')
))

conn.commit()
conn.close()
print('Test order inserted.')
