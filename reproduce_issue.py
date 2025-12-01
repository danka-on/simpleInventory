import sqlite3
import requests
import json
import time

# Setup
conn = sqlite3.connect('bol.db')
cur = conn.cursor()

TEST_UPC = '999999999999'
TEST_LOT = 'TEST_LOT_1'

# Clean up previous run
cur.execute('DELETE FROM bol_items WHERE upc = ?', (TEST_UPC,))
cur.execute('DELETE FROM items_prep_status WHERE upc = ?', (TEST_UPC,))
conn.commit()

# Insert test item
cur.execute('''
    INSERT INTO bol_items (upc, item_description, lot_number, quantity, original_qty, good_qty, bad_qty, unchecked_qty)
    VALUES (?, 'Test Item', ?, 1, 1, 0, 0, 1)
''', (TEST_UPC, TEST_LOT))
conn.commit()
conn.close()

print(f"Inserted test item {TEST_UPC}")

# Create session
s = requests.Session()

# Set active lot
url_lot = 'http://localhost:5000/api/lots/select'
resp_lot = s.post(url_lot, json={'lot_number': TEST_LOT})
print(f"Set lot response: {resp_lot.status_code} {resp_lot.text}")

# Call API
url = 'http://localhost:5000/api/items_prep/status'
data = {
    'upc': TEST_UPC,
    'status': 'good',
    'qty': 1,
    'reason': '',
    'note': ''
}

resp = s.post(url, json=data)
print(f"Status response: {resp.status_code} {resp.text}")

# Verify DB
conn = sqlite3.connect('bol.db')
cur = conn.cursor()
cur.execute('SELECT * FROM items_prep_status WHERE upc = ?', (TEST_UPC,))
row = cur.fetchone()
print(f"items_prep_status row: {row}")

cur.execute('SELECT good_qty FROM bol_items WHERE upc = ?', (TEST_UPC,))
bol_row = cur.fetchone()
print(f"bol_items good_qty: {bol_row}")

conn.close()
