import sqlite3
import datetime
try:
    import pandas as pd
except Exception:
    pd = None
import os

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

def addToSearchRack(ITEM_POSITION=None, BARCODE=None, IMAGES=None, PICTUREPOSITION=None):
    """Add item directly to searchRack.db with enrichment from ebayStore.db and bol.db"""
    conn = sqlite3.connect('searchRack.db')
    cursor = conn.cursor()
    try:
        # Ensure SEARCHRACK table exists with all columns
        cursor.execute('''CREATE TABLE IF NOT EXISTS SEARCHRACK (
            ID INTEGER PRIMARY KEY AUTOINCREMENT,
            TITLE TEXT,
            BARCODE TEXT,
            ITEM_POSITION TEXT,
            IMAGES TEXT,
            PICTUREPOSITION TEXT,
            ITEMID TEXT,
            QUANTITY INTEGER,
            CREATED_AT TEXT
        )''')
        
        # Ensure IMAGE column exists (for older databases)
        try:
            cursor.execute('PRAGMA table_info(SEARCHRACK)')
            cols = [r[1] for r in cursor.fetchall()]
            if 'IMAGE' not in cols:
                cursor.execute('ALTER TABLE SEARCHRACK ADD COLUMN IMAGE TEXT')
                conn.commit()
        except Exception:
            pass
        
        # Get title and other enrichment data
        title = None
        itemid = None
        quantity = None
        image = None
        
        # First try ebayStore.db
        try:
            ebay_conn = sqlite3.connect('ebayStore.db')
            ebay_conn.row_factory = sqlite3.Row
            ebay_cur = ebay_conn.cursor()
            ebay_cur.execute("SELECT Title, ItemID, Quantity, Image FROM INVENTORY WHERE UPC = ? COLLATE NOCASE LIMIT 1", (BARCODE,))
            ebay_row = ebay_cur.fetchone()
            if ebay_row:
                title = ebay_row['Title']
                itemid = ebay_row['ItemID']
                quantity = ebay_row['Quantity']
                image = ebay_row['Image']
            ebay_conn.close()
        except Exception as e:
            print(f"Warning: Could not lookup in ebayStore.db: {e}")
        
        # If not found in ebayStore, try bol.db
        if not title:
            try:
                bol_conn = sqlite3.connect('bol.db')
                bol_conn.row_factory = sqlite3.Row
                bol_cur = bol_conn.cursor()
                bol_cur.execute('SELECT item_description, image_url FROM bol_items WHERE upc = ? COLLATE NOCASE LIMIT 1', (BARCODE,))
                bol_row = bol_cur.fetchone()
                if bol_row:
                    title = bol_row['item_description']
                    image = bol_row['image_url']
                    itemid = BARCODE  # Use barcode as itemid for bol items
                bol_conn.close()
            except Exception as e:
                print(f"Warning: Could not lookup in bol.db: {e}")
        
        # Validate barcode and position are not empty
        if not BARCODE or not str(BARCODE).strip():
            print("Error: BARCODE is empty or None, skipping addToSearchRack")
            return
        if not ITEM_POSITION or not str(ITEM_POSITION).strip():
            print("Error: ITEM_POSITION is empty or None, skipping addToSearchRack")
            return
        
        # Normalize for comparison
        barcode_norm = str(BARCODE).strip()
        position_norm = str(ITEM_POSITION).strip()
        
        # Check if same barcode at same location exists
        cursor.execute("""
            SELECT ID, QUANTITY FROM SEARCHRACK 
            WHERE TRIM(BARCODE) = ? COLLATE NOCASE 
            AND TRIM(ITEM_POSITION) = ? COLLATE NOCASE
        """, (barcode_norm, position_norm))
        existing_same_location = cursor.fetchone()
        
        now_iso = datetime.datetime.utcnow().isoformat()
        
        if existing_same_location:
            # Same barcode at same location - increment quantity by 1
            existing_id, existing_qty = existing_same_location
            new_qty = (existing_qty or 0) + 1
            
            # When incrementing, update enrichment data and CREATED_AT timestamp
            # Position fields should already be correct since we matched on them
            cursor.execute("""
                UPDATE SEARCHRACK 
                SET QUANTITY = ?,
                    TITLE = COALESCE(?, TITLE),
                    ITEMID = COALESCE(?, ITEMID),
                    IMAGE = COALESCE(?, IMAGE),
                    CREATED_AT = ?
                WHERE ID = ?
            """, (new_qty, title, itemid, image, now_iso, existing_id))
            action = f"incremented quantity to {new_qty} for barcode={barcode_norm}, position={position_norm}"
        else:
            # Different location or new barcode - create new record
            cursor.execute("""
                INSERT INTO SEARCHRACK (TITLE, BARCODE, ITEM_POSITION, IMAGES, PICTUREPOSITION, ITEMID, QUANTITY, IMAGE, CREATED_AT) 
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (title, barcode_norm, position_norm, IMAGES, PICTUREPOSITION, itemid, 1, image, now_iso))
            action = "added new entry"
            
        conn.commit()
        print(f"{action} to searchRack: position={ITEM_POSITION}, barcode={BARCODE}, title={title}")
    except sqlite3.Error as e:
        print("Error in addToSearchRack:", e)
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

def createAmazonStoreDB():
    conn = sqlite3.connect('amazonStore.db')
    cursor = conn.cursor()
    print("Amazon Store table starting creation")
    try:
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS INVENTORY (
                ID INTEGER PRIMARY KEY AUTOINCREMENT,
                Title TEXT,
                ASIN TEXT,
                SKU TEXT,
                Price TEXT,
                Quantity TEXT,
                Image TEXT,
                URL TEXT,
                List_State TEXT,
                Sold_Date TEXT,
                List_Date TEXT,
                UPC TEXT,
                isFound TEXT
            )
        ''')
        print("Amazon Store table created successfully")
        conn.commit()
        conn.close()
        print("Amazon Store table closed successfully")
    except sqlite3.Error as e:
        print("Something went wrong with Amazon Store table:", e)
        conn.close()

def amazonStoreDB(title, asin, sku=None, price=None, quantity=None, image=None, List_State=None, Sold_Date=None, List_Date=None, URL=None, upc=None):
    """Add item to amazonStore.db - similar to ebayStoreDB but uses ASIN instead of ItemID"""
    conn = sqlite3.connect('amazonStore.db')
    cursor = conn.cursor()
    # Ensure the INVENTORY table exists before querying
    cursor.execute('''CREATE TABLE IF NOT EXISTS INVENTORY (
        ID INTEGER PRIMARY KEY AUTOINCREMENT,
        Title TEXT,
        ASIN TEXT,
        SKU TEXT,
        Price TEXT,
        Quantity TEXT,
        Image TEXT,
        URL TEXT,
        List_State TEXT,
        Sold_Date TEXT,
        List_Date TEXT,
        UPC TEXT,
        isFound TEXT 
    )''')
    conn.commit()
    cursor.execute("SELECT 1 FROM INVENTORY WHERE ASIN = ?", (asin,))
    item_exist = cursor.fetchone() is not None
    if item_exist:
        print("Item already in Amazon inventory database, skipping")
        conn.close()
        return
    try:
        cursor.execute("INSERT INTO INVENTORY (Title, ASIN, SKU, Price, Quantity, Image, List_State, Sold_Date, List_Date, URL, UPC) VALUES (?,?,?,?,?,?,?,?,?,?,?)", 
                      (title, asin, sku, price, quantity, image, List_State, Sold_Date, List_Date, URL, upc))
        conn.commit()
        print(f"Added {title} to Amazon Store successfully")
    except sqlite3.Error as e:
        print("Something went wrong adding to Amazon Store:", e)
        try:
            conn.close()
            print("Closed successfully from amazonStoreDB")
        except sqlite3.Error as e:
            print("Failed to close connection:", e)
    try:
        conn.close()
        print("Closed successfully from amazonStoreDB")
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
        isHandledDate TEXT,
        location TEXT,
        barcode TEXT,
        rackupdated INTEGER DEFAULT 0,
        store TEXT DEFAULT 'ebay'
    )''')
    
    # Ensure barcode, rackupdated, and store columns exist (for older databases)
    try:
        cur.execute('PRAGMA table_info(orders)')
        cols = [r[1] for r in cur.fetchall()]
        if 'barcode' not in cols:
            cur.execute('ALTER TABLE orders ADD COLUMN barcode TEXT')
            conn.commit()
        if 'rackupdated' not in cols:
            cur.execute('ALTER TABLE orders ADD COLUMN rackupdated INTEGER DEFAULT 0')
            conn.commit()
        if 'removal_cancelled' not in cols:
            cur.execute('ALTER TABLE orders ADD COLUMN removal_cancelled INTEGER DEFAULT 0')
            conn.commit()
        if 'store' not in cols:
            cur.execute('ALTER TABLE orders ADD COLUMN store TEXT DEFAULT "ebay"')
            conn.commit()
    except Exception:
        pass
    
    # Get barcode (UPC) from ebayStore.db using item_id
    barcode_val = order.get('barcode')
    if not barcode_val and order.get('item_id'):
        try:
            ebay_conn = sqlite3.connect('ebayStore.db')
            ebay_cur = ebay_conn.cursor()
            ebay_cur.execute('SELECT UPC FROM INVENTORY WHERE ItemID = ?', (order.get('item_id'),))
            row = ebay_cur.fetchone()
            if row and row[0]:
                barcode_val = row[0]
            ebay_conn.close()
        except Exception:
            pass
    
    # Enrich title and image from ebayStore.db and rawbol.db using barcode
    title_val = order.get('title')
    image_val = order.get('image')
    
    # First try to get image from ebayStore.db using item_id
    if not image_val and order.get('item_id'):
        try:
            ebay_conn = sqlite3.connect('ebayStore.db')
            ebay_cur = ebay_conn.cursor()
            ebay_cur.execute('SELECT Image FROM INVENTORY WHERE ItemID = ?', (order.get('item_id'),))
            row = ebay_cur.fetchone()
            if row and row[0]:
                image_val = row[0]
            ebay_conn.close()
        except Exception:
            pass
    
    # Then try rawbol.db using barcode
    if barcode_val:
        try:
            bol_conn = sqlite3.connect('rawbol.db')
            bol_conn.row_factory = sqlite3.Row
            bol_cur = bol_conn.cursor()
            
            # Try exact match first
            bol_cur.execute('SELECT item_description, image_url FROM raw_bol_items WHERE upc = ? COLLATE NOCASE LIMIT 1', (barcode_val,))
            row_bol = bol_cur.fetchone()
            
            # If not found and barcode has leading zeros, try without them
            if not row_bol and barcode_val.startswith('0'):
                barcode_no_zero = barcode_val.lstrip('0')
                bol_cur.execute('SELECT item_description, image_url FROM raw_bol_items WHERE upc = ? COLLATE NOCASE LIMIT 1', (barcode_no_zero,))
                row_bol = bol_cur.fetchone()
            
            if row_bol:
                # Use rawbol data if not already provided
                if not title_val and row_bol['item_description']:
                    title_val = row_bol['item_description']
                if not image_val and row_bol['image_url']:
                    img_url = row_bol['image_url']
                    # Skip 'nan' values
                    if str(img_url).lower() not in ['nan', 'none', 'null', '']:
                        image_val = img_url
            bol_conn.close()
        except Exception:
            pass

    # Check for duplicate (order_id + item_id)
    cur.execute('SELECT 1 FROM orders WHERE order_id = ? AND item_id = ?', (order.get('order_id'), order.get('item_id')))
    if cur.fetchone():
        conn.close()
        return
    # Prepare location: if order doesn't include location, try to fetch from searchRack.db by barcode==item_id
    location_val = order.get('location')
    if not location_val and order.get('item_id'):
        try:
            rack_conn = sqlite3.connect('searchRack.db')
            rack_cur = rack_conn.cursor()
            rack_cur.execute('SELECT ITEM_POSITION, PICTUREPOSITION FROM SEARCHRACK WHERE BARCODE = ?', (order.get('item_id'),))
            r = rack_cur.fetchone()
            if r:
                item_pos = r[0]
                picpos = r[1]
                if item_pos and str(item_pos).lower() == 'picture' and picpos:
                    location_val = picpos
                elif item_pos:
                    location_val = item_pos
            rack_conn.close()
        except Exception:
            pass
    cur.execute('''INSERT INTO orders (
        order_id, item_id, title, quantity, price, checkout_status, shipping_name, shipping_street1, shipping_street2, shipping_city, shipping_state, shipping_postal_code, shipping_country, paid_time, shipped_time, seller_fee, taxes, fees, image, isHandled, isHandledDate, location, barcode, store
    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
        (
            order.get('order_id'),
            order.get('item_id'),
            title_val,
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
            image_val,
            order.get('isHandled'),
            order.get('isHandledDate'),
            location_val,
            barcode_val,
            'ebay'
        )
    )
    conn.commit()
    conn.close()

def createSearchRackDB():
    """Create SEARCHRACK table structure if it doesn't exist"""
    search_conn = sqlite3.connect('searchRack.db')
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
    # Ensure CREATED_AT column exists
    try:
        search_cur.execute('PRAGMA table_info(SEARCHRACK)')
        cols = [r[1] for r in search_cur.fetchall()]
        if 'CREATED_AT' not in cols:
            search_cur.execute('ALTER TABLE SEARCHRACK ADD COLUMN CREATED_AT TEXT')
            search_conn.commit()
    except Exception:
        pass
    search_conn.commit()
    search_conn.close()

def enrich_searchrack_db(batch_size=500, do_backup=True):
    """Enrich SEARCHRACK rows by looking up BARCODE/UPC in ebayStore.db (preferred) then bol.db.
    Updates SEARCHRACK.TITLE, ITEMID, QUANTITY, IMAGES when available.
    Runs in batches to avoid large transactions. Creates a timestamped backup if requested.
    """
    import time, shutil

    if do_backup:
        try:
            backup_path = 'searchRack.db.bak'
            # remove previous single backup if exists
            if os.path.exists(backup_path):
                try:
                    os.remove(backup_path)
                except Exception:
                    pass
            shutil.copyfile('searchRack.db', backup_path)
            print(f'Backup created: {backup_path}')
        except Exception as e:
            print('Warning: failed to create backup of searchRack.db:', e)

    s_conn = sqlite3.connect('searchRack.db')
    s_conn.row_factory = sqlite3.Row
    s_cur = s_conn.cursor()
    # Ensure enrichment columns exist
    try:
        s_cur.execute('PRAGMA table_info(SEARCHRACK)')
        cols = [r[1] for r in s_cur.fetchall()]
        if 'ITEMID' not in cols:
            s_cur.execute('ALTER TABLE SEARCHRACK ADD COLUMN ITEMID TEXT')
        if 'QUANTITY' not in cols:
            s_cur.execute('ALTER TABLE SEARCHRACK ADD COLUMN QUANTITY INTEGER')
        if 'IMAGE' not in cols and 'IMAGES' not in cols:
            # keep IMAGES existing column; add IMAGE as single-image holder
            s_cur.execute('ALTER TABLE SEARCHRACK ADD COLUMN IMAGE TEXT')
        s_conn.commit()
    except Exception:
        # If ALTER fails (e.g., column exists in some form), continue
        pass

    # Ensure metadata table exists to track incremental progress
    try:
        s_cur.execute("CREATE TABLE IF NOT EXISTS ENRICH_META (k TEXT PRIMARY KEY, v TEXT)")
        s_conn.commit()
    except Exception:
        pass

    # Read metadata values
    def _meta_get(key, default=None):
        s_cur.execute('SELECT v FROM ENRICH_META WHERE k = ?', (key,))
        r = s_cur.fetchone()
        return r[0] if r else default

    def _meta_set(key, val):
        s_cur.execute('INSERT OR REPLACE INTO ENRICH_META (k,v) VALUES (?,?)', (key, str(val)))
        s_conn.commit()

    stored_searchrack_max = int(_meta_get('searchrack_max_id', '0') or 0)
    stored_ebay_max = int(_meta_get('ebay_max_rowid', '0') or 0)
    stored_bol_max = int(_meta_get('bol_max_rowid', '0') or 0)

    # Current max ids
    s_cur.execute('SELECT COALESCE(MAX(ID), 0) FROM SEARCHRACK')
    current_searchrack_max = int(s_cur.fetchone()[0] or 0)

    # Gather barcodes to process incrementally:
    # 1) new SEARCHRACK rows (ID > stored_searchrack_max)
    barcodes_set = set()
    if current_searchrack_max > stored_searchrack_max:
        s_cur.execute('SELECT ID, BARCODE FROM SEARCHRACK WHERE ID > ?', (stored_searchrack_max,))
        new_rows = s_cur.fetchall()
        for r in new_rows:
            b = (r['BARCODE'] or '').strip()
            if b:
                barcodes_set.add(b)

    # Additionally, include any SEARCHRACK rows that currently lack a TITLE so we can fill missing titles
    try:
        s_cur.execute("SELECT DISTINCT BARCODE FROM SEARCHRACK WHERE TITLE IS NULL OR TRIM(TITLE) = ''")
        for (b,) in s_cur.fetchall():
            if b:
                barcodes_set.add(str(b).strip())
    except Exception:
        pass

    # 2) new UPCs added to ebayStore since last run
    try:
        es_conn = sqlite3.connect('ebayStore.db')
        es_cur = es_conn.cursor()
        es_cur.execute('SELECT COALESCE(MAX(rowid),0) FROM INVENTORY')
        current_ebay_max = int(es_cur.fetchone()[0] or 0)
        if current_ebay_max > stored_ebay_max:
            # get UPCs for new rows
            es_cur.execute('SELECT UPC, ItemID FROM INVENTORY WHERE rowid > ?', (stored_ebay_max,))
            for upc, itemid in es_cur.fetchall():
                key = (upc or itemid or '')
                if key:
                    barcodes_set.add(str(key).strip())
        es_conn.close()
    except Exception:
        current_ebay_max = stored_ebay_max

    # 3) new bol items since last run
    try:
        bol_conn = sqlite3.connect('bol.db')
        bol_cur = bol_conn.cursor()
        bol_cur.execute('SELECT COALESCE(MAX(rowid),0) FROM bol_items')
        current_bol_max = int(bol_cur.fetchone()[0] or 0)
        if current_bol_max > stored_bol_max:
            bol_cur.execute('SELECT upc FROM bol_items WHERE rowid > ?', (stored_bol_max,))
            for (upc,) in bol_cur.fetchall():
                if upc:
                    barcodes_set.add(str(upc).strip())
        bol_conn.close()
    except Exception:
        current_bol_max = stored_bol_max

    # If nothing new and no force (do_backup parameter acts as no-force), exit early
    if not barcodes_set and current_searchrack_max <= stored_searchrack_max and current_ebay_max <= stored_ebay_max and current_bol_max <= stored_bol_max:
        print('No changes detected for enrichment; skipping work')
        s_conn.close()
        return

    barcodes = list(barcodes_set)
    print(f'Incremental enrichment will process {len(barcodes)} unique barcodes (batches of {batch_size})')

    # Helper to chunk
    def chunks(lst, n):
        for i in range(0, len(lst), n):
            yield lst[i:i+n]

    # Build enrichment map from bol.db FIRST (prioritized), then ebayStore, then Amazon for misses
    enrichment = {}
    norm_barcodes = [str(b).strip() for b in barcodes]
    
    # PRIORITY 1: Check bol.db first
    try:
        bol_conn = sqlite3.connect('bol.db')
        bol_conn.row_factory = sqlite3.Row
        bol_cur = bol_conn.cursor()
        for batch in chunks(barcodes, batch_size):
            placeholders = ','.join(['?'] * len(batch))
            bol_cur.execute(f"SELECT upc, item_description, image_url FROM bol_items WHERE upc IN ({placeholders})", batch)
            for r in bol_cur.fetchall():
                upc = (r['upc'] or '')
                if not upc:
                    continue
                keys = {str(upc).strip(), str(upc).strip().lower()}
                for key in keys:
                    enrichment[key] = {'title': r['item_description'], 'itemid': upc, 'quantity': None, 'image': r['image_url'], 'source': 'bol'}
        bol_conn.close()
    except Exception as e:
        print('Warning: bol lookup failed:', e)

    # PRIORITY 2: For barcodes not found in bol, query ebayStore
    remaining = [b for b in norm_barcodes if b not in enrichment and b.lower() not in enrichment]
    try:
        if remaining:
            es_conn = sqlite3.connect('ebayStore.db')
            es_conn.row_factory = sqlite3.Row
            es_cur = es_conn.cursor()
            for batch in chunks(remaining, batch_size):
                placeholders = ','.join(['?'] * len(batch))
                # Query by UPC or ItemID
                sql = f"SELECT UPC, Title, ItemID, Quantity, Image FROM INVENTORY WHERE (UPC IN ({placeholders}) OR ItemID IN ({placeholders}))"
                params = batch + batch
                es_cur.execute(sql, params)
                for r in es_cur.fetchall():
                    upc = (r['UPC'] or '')
                    itemid = (r['ItemID'] or '')
                    # normalize keys for matching
                    keys = set()
                    if upc:
                        keys.add(str(upc).strip())
                        keys.add(str(upc).strip().lower())
                    if itemid:
                        keys.add(str(itemid).strip())
                        keys.add(str(itemid).strip().lower())
                    for key in keys:
                        enrichment[key] = {'title': r['Title'], 'itemid': r['ItemID'], 'quantity': r['Quantity'], 'image': r['Image'], 'source': 'ebayStore'}
            es_conn.close()
    except Exception as e:
        print('Warning: ebayStore lookup failed:', e)

    # PRIORITY 3: For remaining barcodes not found in BOL or eBay, query Amazon (amazonStore.db + rawbol.db for images)
    still_remaining = [b for b in norm_barcodes if b not in enrichment and b.lower() not in enrichment]
    try:
        if still_remaining:
            amazon_conn = sqlite3.connect('amazonStore.db')
            amazon_conn.row_factory = sqlite3.Row
            amazon_cur = amazon_conn.cursor()
            
            # Also connect to rawbol for images
            rawbol_conn = sqlite3.connect('rawbol.db')
            rawbol_conn.row_factory = sqlite3.Row
            rawbol_cur = rawbol_conn.cursor()
            
            for batch in chunks(still_remaining, batch_size):
                placeholders = ','.join(['?'] * len(batch))
                # Query amazonStore.db by UPC
                amazon_cur.execute(f"SELECT UPC, TITLE, ASIN, IMAGE FROM ITEMS WHERE UPC IN ({placeholders})", batch)
                for r in amazon_cur.fetchall():
                    upc = (r['UPC'] or '').strip()
                    if not upc:
                        continue
                    
                    # Get image from rawbol.db if Amazon doesn't have it
                    image_url = r['IMAGE']
                    if not image_url:
                        # Try exact match first
                        rawbol_cur.execute("SELECT image_url FROM raw_bol_items WHERE upc = ?", (upc,))
                        rawbol_row = rawbol_cur.fetchone()
                        
                        # If not found and UPC has leading zeros, try without them
                        if not rawbol_row and upc.startswith('0'):
                            upc_no_zero = upc.lstrip('0')
                            rawbol_cur.execute("SELECT image_url FROM raw_bol_items WHERE upc = ?", (upc_no_zero,))
                            rawbol_row = rawbol_cur.fetchone()
                        
                        if rawbol_row:
                            image_url = rawbol_row['image_url']
                    
                    keys = {str(upc).strip(), str(upc).strip().lower()}
                    for key in keys:
                        enrichment[key] = {
                            'title': r['TITLE'], 
                            'itemid': r['ASIN'],  # Use ASIN as itemid for Amazon
                            'quantity': None, 
                            'image': image_url, 
                            'source': 'amazonStore'
                        }
            
            amazon_conn.close()
            rawbol_conn.close()
    except Exception as e:
        print('Warning: Amazon lookup failed:', e)

    # Build mapping of barcode -> [ids] from SEARCHRACK for the barcodes we're processing
    id_by_barcode = {}
    try:
        for batch in chunks(barcodes, batch_size):
            placeholders = ','.join(['?'] * len(batch))
            s_cur.execute(f"SELECT ID, BARCODE FROM SEARCHRACK WHERE BARCODE IN ({placeholders})", batch)
            for rid, bcode in s_cur.fetchall():
                b = (bcode or '').strip()
                if not b:
                    continue
                id_by_barcode.setdefault(b, []).append(rid)
    except Exception as e:
        print('Warning: failed to build id_by_barcode mapping:', e)

    # Apply enrichment to SEARCHRACK rows in batches
    total_updates = 0
    try:
        for batch_ids in chunks(list(id_by_barcode.items()), batch_size):
            # batch_ids is list of (barcode, [ids]) pairs
            with s_conn:
                for barcode, ids_list in batch_ids:
                    data = enrichment.get(barcode)
                    if not data:
                        continue
                    for rid in ids_list:
                        # Update TITLE if empty or different; set ITEMID/QUANTITY/IMAGE when available
                        try:
                            s_cur.execute('SELECT TITLE, IMAGES, IMAGE, ITEMID, QUANTITY FROM SEARCHRACK WHERE ID = ?', (rid,))
                            currow = s_cur.fetchone()
                            curtitle = currow[0] if currow else None
                            curimages = currow[1] if currow else None
                            curimage = currow[2] if currow else None
                            curitemid = currow[3] if currow else None
                            curqty = currow[4] if currow else None
                            src_title = data.get('title')
                            # decide if we should write title: if current title is empty or different
                            write_title = False
                            if src_title:
                                if not curtitle or str(curtitle).strip() == '':
                                    write_title = True
                                else:
                                    # compare normalized
                                    if str(curtitle).strip().lower() != str(src_title).strip().lower():
                                        write_title = True
                            new_title = src_title if write_title else curtitle
                            new_image = curimage or curimages or data.get('image')
                            new_itemid = curitemid or data.get('itemid')
                            new_qty = curqty or data.get('quantity')
                            if write_title or new_image or new_itemid or new_qty:
                                s_cur.execute('UPDATE SEARCHRACK SET TITLE = ?, IMAGE = ?, ITEMID = ?, QUANTITY = ? WHERE ID = ?', (new_title, new_image, new_itemid, new_qty, rid))
                                total_updates += 1
                        except Exception:
                            continue
        s_conn.commit()
        # Update metadata with current max positions so next run can be incremental
        try:
            _meta_set('searchrack_max_id', current_searchrack_max)
            _meta_set('ebay_max_rowid', current_ebay_max)
            _meta_set('bol_max_rowid', current_bol_max)
        except Exception:
            pass
    except Exception as e:
        print('Error applying enrichment to searchRack:', e)
    finally:
        s_conn.close()

    print(f'Enrichment complete: updated approximately {total_updates} rows')

def process_sold_orders_inventory_reduction():
    """
    Process sold orders and reduce searchRack.db quantities.
    Only processes orders where:
    - rackupdated = 0
    - Has a shipped_time (item has been shipped)
    - 48 hours have passed since shipped_time
    - removal_cancelled is not 1
    Marks orders as rackupdated = 1 after processing (whether found in searchRack or not).
    """
    print("🔄 Processing sold orders for inventory reduction...")
    
    try:
        # Connect to sold.db
        sold_conn = sqlite3.connect('sold.db')
        sold_conn.row_factory = sqlite3.Row
        sold_cur = sold_conn.cursor()
        # Ensure settings table exists and read grace period hours (default 48)
        try:
            sold_cur.execute("CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT)")
            sold_conn.commit()
            sold_cur.execute("SELECT value FROM settings WHERE key = 'removal_grace_hours'")
            row = sold_cur.fetchone()
            grace_period_hours = int(row[0]) if row and str(row[0]).strip().isdigit() else 48
        except Exception:
            grace_period_hours = 48
        
        # Ensure removal_cancelled column exists
        try:
            sold_cur.execute('PRAGMA table_info(orders)')
            cols = [r[1] for r in sold_cur.fetchall()]
            if 'removal_cancelled' not in cols:
                sold_cur.execute('ALTER TABLE orders ADD COLUMN removal_cancelled INTEGER DEFAULT 0')
                sold_conn.commit()
                print("  ✓ Added removal_cancelled column to orders table")
        except Exception as e:
            print(f"  ⚠ Could not add removal_cancelled column: {e}")
        
        # Get all unprocessed orders with barcodes that have been shipped
        # and are past the 48-hour grace period
        sold_cur.execute('''
            SELECT id, barcode, quantity, order_id, item_id, title, shipped_time 
            FROM orders 
            WHERE rackupdated = 0 
            AND barcode IS NOT NULL 
            AND barcode != ''
            AND shipped_time IS NOT NULL
            AND shipped_time != ''
            AND (removal_cancelled IS NULL OR removal_cancelled = 0)
        ''')
        unprocessed_orders = sold_cur.fetchall()
        
        if not unprocessed_orders:
            print("  ✓ No unprocessed sold orders ready for inventory reduction")
            sold_conn.close()
            return
        
        print(f"  Found {len(unprocessed_orders)} shipped orders, checking grace period (grace={grace_period_hours}h)...")
        
        # Filter orders by grace period
        now = datetime.datetime.utcnow()
        eligible_orders = []
        
        for order in unprocessed_orders:
            try:
                # Parse shipped_time (ISO format from eBay API)
                shipped_str = order['shipped_time']
                # Handle various datetime formats
                if 'T' in shipped_str:
                    if shipped_str.endswith('Z'):
                        shipped_dt = datetime.datetime.fromisoformat(shipped_str.replace('Z', '+00:00'))
                    elif '+' in shipped_str or shipped_str.count('-') > 2:
                        shipped_dt = datetime.datetime.fromisoformat(shipped_str)
                    else:
                        shipped_dt = datetime.datetime.fromisoformat(shipped_str)
                else:
                    shipped_dt = datetime.datetime.fromisoformat(shipped_str)
                
                # Remove timezone info for comparison
                if shipped_dt.tzinfo:
                    shipped_dt = shipped_dt.replace(tzinfo=None)
                
                # Check if 48 hours have passed
                hours_since_shipped = (now - shipped_dt).total_seconds() / 3600
                
                if hours_since_shipped >= grace_period_hours:
                    eligible_orders.append(order)
                else:
                    remaining = grace_period_hours - hours_since_shipped
                    print(f"  ⏱ Order {order['order_id']}: {remaining:.1f}h remaining in grace period")
                    
            except Exception as e:
                print(f"  ⚠ Could not parse shipped_time for order {order['order_id']}: {e}")
                continue
        
        if not eligible_orders:
            print("  ✓ No orders past 48-hour grace period")
            sold_conn.close()
            return
        
        print(f"  Found {len(eligible_orders)} orders eligible for inventory reduction")
        
        # Connect to searchRack.db
        rack_conn = sqlite3.connect('searchRack.db')
        rack_cur = rack_conn.cursor()

        # Prepare removed.db for logging removals
        rem_conn = sqlite3.connect('removed.db')
        rem_cur = rem_conn.cursor()
        rem_cur.execute('''
            CREATE TABLE IF NOT EXISTS removed (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT,
                barcode TEXT,
                qty INTEGER,
                time_removed TEXT,
                undone_at TEXT
            )
        ''')
        rem_conn.commit()
        # Ensure undone_at exists for older schemas
        try:
            rem_cur.execute('PRAGMA table_info(removed)')
            cols = [r[1] for r in rem_cur.fetchall()]
            if 'undone_at' not in cols:
                rem_cur.execute('ALTER TABLE removed ADD COLUMN undone_at TEXT')
                rem_conn.commit()
        except Exception:
            pass
        
        # Determine which quantity column to use
        rack_cur.execute("PRAGMA table_info(SEARCHRACK)")
        columns = [row[1] for row in rack_cur.fetchall()]
        qty_col = 'QUANTITY' if 'QUANTITY' in columns else 'QTY'
        
        updated_count = 0
        not_found_count = 0
        
        for order in eligible_orders:
            order_id = order['id']
            barcode = order['barcode']
            sold_qty = order['quantity'] or 1
            
            try:
                # Try to find item in searchRack by barcode (ITEMID column)
                rack_cur.execute(f"SELECT ID, {qty_col} FROM SEARCHRACK WHERE ITEMID = ? COLLATE NOCASE", (barcode,))
                rack_item = rack_cur.fetchone()
                
                if rack_item:
                    rack_id = rack_item[0]
                    current_qty = rack_item[1] or 0
                    new_qty = max(0, current_qty - sold_qty)  # Don't go below 0
                    
                    # Update the quantity in searchRack
                    rack_cur.execute(f"UPDATE SEARCHRACK SET {qty_col} = ? WHERE ID = ?", (new_qty, rack_id))
                    rack_conn.commit()
                    # Log removal entry
                    try:
                        # Try to capture a descriptive name
                        name_val = None
                        try:
                            # sqlite3.Row supports key access
                            if hasattr(order, 'keys') and 'title' in order.keys():
                                name_val = order['title']
                        except Exception:
                            name_val = None
                        # If empty, try to query SEARCHRACK title
                        if not name_val:
                            try:
                                rack_cur.execute("SELECT TITLE FROM SEARCHRACK WHERE ID = ?", (rack_id,))
                                rr = rack_cur.fetchone()
                                name_val = rr[0] if rr and rr[0] else ''
                            except Exception:
                                name_val = ''
                        time_iso = datetime.datetime.utcnow().isoformat() + 'Z'
                        rem_cur.execute(
                            "INSERT INTO removed (name, barcode, qty, time_removed) VALUES (?,?,?,?)",
                            (name_val or '', barcode, int(sold_qty) if sold_qty else 1, time_iso)
                        )
                        rem_conn.commit()
                    except Exception:
                        pass
                    
                    print(f"  ✓ Reduced {barcode}: {current_qty} → {new_qty} (sold {sold_qty})")
                    updated_count += 1
                else:
                    print(f"  ⚠ Barcode {barcode} not found in searchRack (Order: {order['order_id']})")
                    not_found_count += 1
                    # Still log this removal and mark order processed to avoid repeated attempts
                    try:
                        time_iso = datetime.datetime.utcnow().isoformat() + 'Z'
                        name_val = None
                        try:
                            if hasattr(order, 'keys') and 'title' in order.keys():
                                name_val = order['title']
                        except Exception:
                            name_val = None
                        rem_cur.execute(
                            "INSERT INTO removed (name, barcode, qty, time_removed) VALUES (?,?,?,?)",
                            (name_val or '', barcode, int(sold_qty) if sold_qty else 1, time_iso)
                        )
                        rem_conn.commit()
                    except Exception:
                        pass
                
            except Exception as e:
                print(f"  ✗ Error processing barcode {barcode}: {e}")
            
            finally:
                # Mark as processed regardless of whether found or not
                sold_cur.execute("UPDATE orders SET rackupdated = 1 WHERE id = ?", (order_id,))
                sold_conn.commit()
        
        rack_conn.close()
        try:
            rem_conn.close()
        except Exception:
            pass
        sold_conn.close()
        
        print(f"✅ Inventory reduction complete:")
        print(f"   - Updated: {updated_count} items")
        print(f"   - Not found: {not_found_count} items")
        print(f"   - Total processed: {len(eligible_orders)} orders")
        
    except Exception as e:
        print(f"❌ Error in process_sold_orders_inventory_reduction: {e}")
        import traceback
        traceback.print_exc()

# No top-level code or __main__ block

