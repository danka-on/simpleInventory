from flask import Flask, render_template, request, jsonify
import sqlite3
import os

app = Flask(__name__)

# Helper to get items from bol.db and ebayStore.db
def get_bol_items():
    conn = sqlite3.connect('bol.db')
    cur = conn.cursor()
    cur.execute("SELECT rowid, item_description, image_url FROM bol_items WHERE IFNULL(isFound, '') != 'True'")
    items = [
        {'id': row[0], 'title': row[1], 'image': row[2], 'source': 'bol'}
        for row in cur.fetchall()
    ]
    conn.close()
    return items

def get_ebay_items():
    conn = sqlite3.connect('ebayStore.db')
    cur = conn.cursor()
    cur.execute("SELECT rowid, title, image FROM INVENTORY WHERE IFNULL(isFound, '') != 'True'")
    items = [
        {'id': row[0], 'title': row[1], 'image': row[2], 'source': 'ebay'}
        for row in cur.fetchall()
    ]
    conn.close()
    return items

def fuse_and_store_match(bol_id, ebay_id):
    # Fetch bol item
    bol_conn = sqlite3.connect('bol.db')
    bol_cur = bol_conn.cursor()
    bol_cur.execute("SELECT upc, date_added FROM bol_items WHERE rowid=?", (bol_id,))
    bol_row = bol_cur.fetchone()
    # Mark as found
    bol_cur.execute("UPDATE bol_items SET isFound='True' WHERE rowid=?", (bol_id,))
    bol_conn.commit()
    bol_conn.close()
    # Fetch ebay item
    ebay_conn = sqlite3.connect('ebayStore.db')
    ebay_cur = ebay_conn.cursor()
    ebay_cur.execute("SELECT title, image FROM INVENTORY WHERE rowid=?", (ebay_id,))
    ebay_row = ebay_cur.fetchone()
    # Mark as found
    ebay_cur.execute("UPDATE INVENTORY SET isFound='True' WHERE rowid=?", (ebay_id,))
    ebay_conn.commit()
    ebay_conn.close()
    # Insert into found.db
    found_conn = sqlite3.connect('found.db')
    found_cur = found_conn.cursor()
    found_cur.execute('''CREATE TABLE IF NOT EXISTS matches (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        upc TEXT,
        date_added TEXT,
        ebay_id INTEGER, ebay_title TEXT, ebay_image TEXT
    )''')
    found_cur.execute('''INSERT INTO matches (upc, date_added, ebay_id, ebay_title, ebay_image)
        VALUES (?, ?, ?, ?, ?)''',
        (bol_row[0], bol_row[1], ebay_id, ebay_row[0], ebay_row[1]))
    found_conn.commit()
    found_conn.close()

@app.route('/')
def index():
    bol_items = get_bol_items()
    ebay_items = get_ebay_items()
    return render_template('manualmatcher.html', bol_items=bol_items, ebay_items=ebay_items)

# API endpoint for matching (to be implemented)
@app.route('/match', methods=['POST'])
def match_items():
    data = request.json
    bol_id = data.get('bol_id')
    ebay_id = data.get('ebay_id')
    if bol_id is not None and ebay_id is not None:
        try:
            fuse_and_store_match(bol_id, ebay_id)
            return jsonify({'success': True})
        except Exception as e:
            print('Failed to match:', e)
            return jsonify({'success': False, 'error': str(e)})
    return jsonify({'success': False, 'error': 'Missing IDs'})

if __name__ == '__main__':
    app.run(debug=True)
