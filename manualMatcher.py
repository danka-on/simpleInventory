import sqlite3




# Helper to get items from bol.db and ebayStore.db
def get_bol_items():
    conn = sqlite3.connect('bol.db')
    cur = conn.cursor()
    try:
        cur.execute("SELECT rowid, item_description, image_url FROM bol_items WHERE IFNULL(isFound, '') = ''")
        items = [
            {'id': row[0], 'title': row[1], 'image': row[2], 'source': 'bol'}
            for row in cur.fetchall()
        ]
        conn.close()
        return items
    except sqlite3.OperationalError as e:
        print(e)

def get_ebay_items():
    conn = sqlite3.connect('ebayStore.db')
    cur = conn.cursor()
    try:
        cur.execute("SELECT rowid, title, image FROM INVENTORY WHERE IFNULL(isFound, '') = ''")
        items = [
            {'id': row[0], 'title': row[1], 'image': row[2], 'source': 'ebay'}
            for row in cur.fetchall()
        ]
        conn.close()
        return items
    except sqlite3.OperationalError as e:
        print(e)
def fuse_and_store_match(bol_id, ebay_id):
    # Fetch bol item
    bol_conn = sqlite3.connect('bol.db')
    bol_cur = bol_conn.cursor()
    try:
        bol_cur.execute("SELECT upc, import_date FROM bol_items WHERE rowid=?", (bol_id,))
        bol_row = bol_cur.fetchone()
        if not bol_row:
            raise Exception(f"No BOL item found for rowid={bol_id}")
        # Mark as found
        bol_cur.execute("UPDATE bol_items SET isFound='True' WHERE rowid=?", (bol_id,))
        bol_conn.commit()
        bol_conn.close()
    except sqlite3.OperationalError as e:
        print(e)
    # Fetch ebay item
    ebay_conn = sqlite3.connect('ebayStore.db')
    ebay_cur = ebay_conn.cursor()
    try:

        ebay_cur.execute("SELECT title, image FROM INVENTORY WHERE rowid=?", (ebay_id,))
        ebay_row = ebay_cur.fetchone()
        if not ebay_row:
            raise Exception(f"No eBay item found for rowid={ebay_id}")
        # Mark as found
        ebay_cur.execute("UPDATE INVENTORY SET isFound='True' WHERE rowid=?", (ebay_id,))
        ebay_conn.commit()
        ebay_conn.close()
    except sqlite3.OperationalError as e:
        print(e)
    # Insert into found.db
    print(f"DEBUG: bol_row={bol_row}, ebay_row={ebay_row}, bol_id={bol_id}, ebay_id={ebay_id}")
    found_conn = sqlite3.connect('found.db')
    found_cur = found_conn.cursor()
    try:
        found_cur.execute('''CREATE TABLE IF NOT EXISTS matches (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            upc TEXT,
            date_added TEXT,
            ebay_id TEXT, ebay_title TEXT, ebay_image TEXT
            )''')
        found_cur.execute('''INSERT INTO matches (upc, date_added, ebay_id, ebay_title, ebay_image)
            VALUES (?, ?, ?, ?, ?)''',
            (bol_row[0], bol_row[1], ebay_id, ebay_row[0], ebay_row[1]))
        found_conn.commit()
        found_conn.close()
    except sqlite3.OperationalError as e:
        print(e)



