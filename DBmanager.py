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
                List_Date TEXT
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

def addToRack(ITEM_POSITION = None, BARCODE = None, IMAGES = None):
    conn = sqlite3.connect('rack.db')
    cursor = conn.cursor()
    try:
        cursor.execute("INSERT INTO INVENTORY (ITEM_POSITION, BARCODE, IMAGES ) VALUES (?,?,?)", (ITEM_POSITION, BARCODE, IMAGES))
        conn.commit()
        print(f"added",{ITEM_POSITION},{BARCODE})
    except sqlite3.Error as e:
        print("something went wrong", e)
        try:
            conn.close()
            print("Closed successfully from ebayStoreDB")
        except sqlite3.Error as e:
            print("Failed to close connection:", e)

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
        List_Date TEXT
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

# No top-level code or __main__ block
