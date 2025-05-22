import sqlite3
import pandas as pd

def createRack():
    conn = sqlite3.connect('rack.db')
    cursor = conn.cursor()
    print("Rack starting creation")
    try:
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS INVENTORY (
                ID INTEGER PRIMARY KEY AUTOINCREMENT,
                ITEM_POSITION TEXT,
                BARCODE TEXT,
                IMAGES TEXT
            )
        ''')
        print("Rack created successfully")
        conn.commit()
        print("Rack committed")
        conn.close()
        print("Rack closed successfully")
    except sqlite3.Error as e:
        print("something went wrong with my Rack ",e)
        conn.close()

def createEbayStoreDB():
    conn = sqlite3.connect('ebayStore.db')
    cursor = conn.cursor()
    print("Table starting creation")
    try:
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS INVENTORY (
                ID INTEGER PRIMARY KEY AUTOINCREMENT,
                Title TEXT,
                ItemID TEXT,
                SKU TEXT,
                Price TEXT,
                Quantity TEXT,
                Image TEXT,
                URL TEXT,
                List_State TEXT,
                Sold_Date TEXT,
                List_Date TEXT,
                isFound TEXT
            )
        ''')
        print("Table created successfully")
        conn.commit()
        print("table committed")
        conn.close()
        print("table closed successfully")
    except sqlite3.error as e:
        print("something went wrong with table ",e)
        conn.close()

def addToRack(ITEM_POSITION=None, BARCODE=None, IMAGES=None, PICTUREPOSITION=None):
    conn = sqlite3.connect('rack.db')
    cursor = conn.cursor()
    try:
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS INVENTORY (
                ID INTEGER PRIMARY KEY AUTOINCREMENT,
                ITEM_POSITION TEXT,
                BARCODE TEXT,
                IMAGES TEXT,
                PICTUREPOSITION TEXT
            )
        ''')
        cursor.execute("INSERT INTO INVENTORY (ITEM_POSITION, BARCODE, IMAGES, PICTUREPOSITION) VALUES (?,?,?,?)", (ITEM_POSITION, BARCODE, IMAGES, PICTUREPOSITION))
        conn.commit()
        print(f"added position: {ITEM_POSITION}, barcode: {BARCODE}, images: {IMAGES}, pictureposition: {PICTUREPOSITION}")
    except sqlite3.Error as e:
        print("something went wrong", e)
    finally:
        conn.close()

def ebayStoreDB(title, item_id, sku = None, price = None, quantity = None, image = None, List_State = None, Sold_Date = None, List_Date = None, URL = None):
    conn = sqlite3.connect('ebayStore.db')
    cursor = conn.cursor()
    # Ensure the INVENTORY table exists before querying
    cursor.execute('''CREATE TABLE IF NOT EXISTS INVENTORY (
        ID INTEGER PRIMARY KEY AUTOINCREMENT,
        Title TEXT,
        ItemID TEXT,
        SKU TEXT,
        Price TEXT,
        Quantity TEXT,
        Image TEXT,
        URL TEXT,
        List_State TEXT,
        Sold_Date TEXT,
        List_Date TEXT,
        isFound TEXT 
    )''')
    conn.commit()
    cursor.execute("SELECT 1 FROM INVENTORY WHERE ItemID = ?", (item_id,))
    item_exist = cursor.fetchone() is not None
    if item_exist:
        print("item already in inventory database, skipping")
        conn.close()
        return
    try:
        cursor.execute("INSERT INTO INVENTORY (Title, ItemID, SKU, Price, Quantity, Image, List_State, Sold_Date, List_Date, URL ) VALUES (?,?,?,?,?,?,?,?,?,?)", (title, item_id, sku, price, quantity, image, List_State, Sold_Date, List_Date, URL))
        conn.commit()
        print(f"Added {title} successfully")
    except sqlite3.Error as e:
        print("something went wrong", e)
        try:
            conn.close()
            print("Closed successfully from ebayStoreDB")
        except sqlite3.Error as e:
            print("Failed to close connection:", e)
    try:
        conn.close()
        print("Closed successfully from ebayStoreDB")
    except sqlite3.Error as e:
        print("Failed to close connection:", e)

def insert_bol_items(df, import_date):
    """
    Insert BOL items from DataFrame into bol.db (bol_items table).
    Skips duplicates by UPC. Returns {'success': True, 'inserted': n} or error dict.
    """
    try:
        conn = sqlite3.connect('bol.db')
        c = conn.cursor()
        c.execute('''CREATE TABLE IF NOT EXISTS bol_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            upc TEXT UNIQUE,
            item_description TEXT,
            client_cost REAL,
            total_client_cost REAL,
            image_url TEXT,
            lot_number TEXT,
            bol_number TEXT,
            import_date TEXT
        )''')
        conn.commit()
        inserted = 0
        for _, row in df.iterrows():
            upc = str(row['UPC']).strip()
            if not upc or upc.lower() == 'nan':
                continue
            c.execute('SELECT 1 FROM bol_items WHERE upc = ?', (upc,))
            if c.fetchone():
                continue  # duplicate
            c.execute('''INSERT INTO bol_items (upc, item_description, client_cost, total_client_cost, image_url, lot_number, bol_number, import_date)
                         VALUES (?, ?, ?, ?, ?, ?, ?, ?)''',
                      (upc,
                       str(row['ITEM DESCRIPTION']).strip(),
                       float(row['CLIENT COST']) if not pd.isna(row['CLIENT COST']) else None,
                       float(row['TOTAL CLIENT COST']) if not pd.isna(row['TOTAL CLIENT COST']) else None,
                       str(row['IMAGE']).strip(),
                       str(row['LOT #']).strip(),
                       str(row['BOL #']).strip(),
                       import_date))
            inserted += 1
        conn.commit()
        conn.close()
        return {'success': True, 'inserted': inserted}
    except Exception as e:
        return {'success': False, 'error': str(e)}

def store_ebay_order(order):
    """Insert a sold order into sold.db (orders table), skipping duplicates by order_id+item_id."""
    conn = sqlite3.connect('sold.db')
    cur = conn.cursor()
    cur.execute('''CREATE TABLE IF NOT EXISTS orders (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        order_id TEXT,
        item_id TEXT,
        title TEXT,
        quantity INTEGER,
        price REAL,
        checkout_status TEXT,
        shipping_name TEXT,
        shipping_street1 TEXT,
        shipping_street2 TEXT,
        shipping_city TEXT,
        shipping_state TEXT,
        shipping_postal_code TEXT,
        shipping_country TEXT,
        paid_time TEXT,
        shipped_time TEXT,
        seller_fee REAL,
        taxes REAL,
        fees TEXT,
        image TEXT,
        isHandled TEXT,
        isHandledDate TEXT
    )''')
    # Check for duplicate (order_id + item_id)
    cur.execute('SELECT 1 FROM orders WHERE order_id = ? AND item_id = ?', (order.get('order_id'), order.get('item_id')))
    if cur.fetchone():
        conn.close()
        return
    cur.execute('''INSERT INTO orders (
        order_id, item_id, title, quantity, price, checkout_status, shipping_name, shipping_street1, shipping_street2, shipping_city, shipping_state, shipping_postal_code, shipping_country, paid_time, shipped_time, seller_fee, taxes, fees, image, isHandled, isHandledDate
    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
        (
            order.get('order_id'),
            order.get('item_id'),
            order.get('title'),
            order.get('quantity'),
            order.get('price'),
            order.get('checkout_status'),
            order.get('shipping_name'),
            order.get('shipping_street1'),
            order.get('shipping_street2'),
            order.get('shipping_city'),
            order.get('shipping_state'),
            order.get('shipping_postal_code'),
            order.get('shipping_country'),
            order.get('paid_time'),
            order.get('shipped_time'),
            order.get('seller_fee'),
            order.get('taxes'),
            order.get('fees'),
            order.get('image'),
            order.get('isHandled'),
            order.get('isHandledDate')
        )
    )
    conn.commit()
    conn.close()

def createSearchRackDB():
    # Connect to all databases
    rack_conn = sqlite3.connect('rack.db')
    bol_conn = sqlite3.connect('bol.db')
    search_conn = sqlite3.connect('searchRack.db')
    rack_cur = rack_conn.cursor()
    bol_cur = bol_conn.cursor()
    search_cur = search_conn.cursor()
    # Create table
    search_cur.execute('''
        CREATE TABLE IF NOT EXISTS SEARCHRACK (
            ID INTEGER PRIMARY KEY AUTOINCREMENT,
            TITLE TEXT,
            BARCODE TEXT,
            ITEM_POSITION TEXT,
            IMAGES TEXT,
            PICTUREPOSITION TEXT
        )
    ''')
    # Get all rack items
    rack_cur.execute('SELECT BARCODE, ITEM_POSITION, IMAGES, PICTUREPOSITION FROM INVENTORY')
    rack_items = rack_cur.fetchall()
    for barcode, item_position, images, pictureposition in rack_items:
        # Get item_description from bol.db by upc (barcode)
        bol_cur.execute('SELECT item_description FROM bol_items WHERE upc=? COLLATE NOCASE', (barcode,))
        bol_row = bol_cur.fetchone()
        title = bol_row[0] if bol_row else None
        search_cur.execute('INSERT INTO SEARCHRACK (TITLE, BARCODE, ITEM_POSITION, IMAGES, PICTUREPOSITION) VALUES (?,?,?,?,?)',
            (title, barcode, item_position, images, pictureposition))
    search_conn.commit()
    rack_conn.close()
    bol_conn.close()
    search_conn.close()

def updateSearchRackDB():
    rack_conn = sqlite3.connect('rack.db')
    bol_conn = sqlite3.connect('bol.db')
    search_conn = sqlite3.connect('searchRack.db')
    rack_cur = rack_conn.cursor()
    bol_cur = bol_conn.cursor()
    search_cur = search_conn.cursor()
    # Clear existing data
    search_cur.execute('DELETE FROM SEARCHRACK')
    # Get all rack items
    rack_cur.execute('SELECT BARCODE, ITEM_POSITION, IMAGES, PICTUREPOSITION FROM INVENTORY')
    rack_items = rack_cur.fetchall()
    for barcode, item_position, images, pictureposition in rack_items:
        # Get item_description from bol.db by upc (barcode)
        bol_cur.execute('SELECT item_description FROM bol_items WHERE upc=? COLLATE NOCASE', (barcode,))
        bol_row = bol_cur.fetchone()
        title = bol_row[0] if bol_row else None
        search_cur.execute('INSERT INTO SEARCHRACK (TITLE, BARCODE, ITEM_POSITION, IMAGES, PICTUREPOSITION) VALUES (?,?,?,?,?)',
            (title, barcode, item_position, images, pictureposition))
    search_conn.commit()
    rack_conn.close()
    bol_conn.close()
    search_conn.close()

# No top-level code or __main__ block
