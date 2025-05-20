from flask import Flask, render_template, request, jsonify
import sqlite3
import os

app = Flask(__name__)

# Helper to get items from bol.db and ebayStore.db
def get_bol_items():
    conn = sqlite3.connect('bol.db')
    cur = conn.cursor()
    cur.execute("SELECT rowid, item_description, image_url FROM bol_items")
    items = [
        {'id': row[0], 'title': row[1], 'image': row[2], 'source': 'bol'}
        for row in cur.fetchall()
    ]
    conn.close()
    return items

def get_ebay_items():
    conn = sqlite3.connect('ebayStore.db')
    cur = conn.cursor()
    cur.execute("SELECT rowid, title, image FROM INVENTORY")
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
    bol_cur.execute("SELECT item_description, image_url FROM bol_items WHERE rowid=?", (bol_id,))
    bol_row = bol_cur.fetchone()
    bol_conn.close()
    # Fetch ebay item
    ebay_conn = sqlite3.connect('ebayStore.db')
    ebay_cur = ebay_conn.cursor()
    ebay_cur.execute("SELECT title, image FROM INVENTORY WHERE rowid=?", (ebay_id,))
    ebay_row = ebay_cur.fetchone()
    ebay_conn.close()
    # Insert into found.db
    found_conn = sqlite3.connect('found.db')
    found_cur = found_conn.cursor()
    found_cur.execute('''CREATE TABLE IF NOT EXISTS matches (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        bol_id INTEGER, bol_description TEXT, bol_image TEXT,
        ebay_id INTEGER, ebay_title TEXT, ebay_image TEXT
    )''')
    found_cur.execute('''INSERT INTO matches (bol_id, bol_description, bol_image, ebay_id, ebay_title, ebay_image)
        VALUES (?, ?, ?, ?, ?, ?)''',
        (bol_id, bol_row[0], bol_row[1], ebay_id, ebay_row[0], ebay_row[1]))
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
    # TODO: Fuse data and insert into found.db
    return jsonify({'success': True})

if __name__ == '__main__':
    app.run(debug=True)
