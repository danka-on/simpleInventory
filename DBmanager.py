import sqlite3
import datetime
import re
try:
    import pandas as pd
except Exception:
    pd = None
import os
from contextlib import contextmanager

# Define base directory for cross-platform compatibility
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

@contextmanager
def connect_db(db_name):
    """Context manager for database connections. Auto-commits on success, rolls back on error, always closes."""
    conn = sqlite3.connect(os.path.join(BASE_DIR, db_name), timeout=30.0)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

def _normalize_marketplace_upc(value):
    s = str(value or '').strip()
    if not s:
        return ''
    if '-' in s:
        base, suffix = s.split('-', 1)
    else:
        base, suffix = s, ''
    base = (base or '').strip()
    suffix = (suffix or '').strip()
    if base.isdigit():
        stripped = base.lstrip('0')
        base = stripped if stripped else '0'
    return f'{base}-{suffix}' if suffix else base

def _marketplace_upc_variants(value):
    raw = str(value or '').strip()
    if not raw:
        return []
    out = []

    def add(v):
        vv = str(v or '').strip()
        if vv and vv not in out:
            out.append(vv)

    add(raw)
    if '-' in raw:
        base, suffix = raw.split('-', 1)
    else:
        base, suffix = raw, ''
    base = (base or '').strip()
    suffix = (suffix or '').strip()

    normalized = _normalize_marketplace_upc(raw)
    add(normalized)

    if base.isdigit():
        stripped = base.lstrip('0') or '0'
        padded = base.zfill(12) if len(base) <= 12 else base
        add(f'{stripped}-{suffix}' if suffix else stripped)
        add(f'{padded}-{suffix}' if suffix else padded)

    return out

def _extract_upc_candidates_from_sku(sku):
    s = str(sku or '').strip()
    if not s:
        return []
    out = []

    def add(v):
        vv = str(v or '').strip()
        if vv and vv not in out:
            out.append(vv)

    if re.fullmatch(r'\d+(?:-\d+)?', s):
        add(s)

    sep_match = re.fullmatch(r'(\d{1,18})[-_ ]+(\d+)', s)
    if sep_match:
        add(f'{sep_match.group(1)}-{sep_match.group(2)}')

    suffix_match = re.fullmatch(r'(\d{1,18})[-_ ]*(?:s|suffix)[-_ ]*(\d+)', s, flags=re.IGNORECASE)
    if suffix_match:
        add(f'{suffix_match.group(1)}-{suffix_match.group(2)}')

    return out

def _lookup_first_value(db_name, query, params):
    try:
        with connect_db(db_name) as conn:
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()
            cur.execute(query, params)
            row = cur.fetchone()
            if not row:
                return None
            try:
                return row[0]
            except Exception:
                try:
                    return next(iter(dict(row).values()))
                except Exception:
                    return None
    except Exception:
        return None

def _upc_exists_in_local_sources(candidate):
    variants = _marketplace_upc_variants(candidate)
    if not variants:
        return False

    variant_keys = [str(v).strip().lower() for v in variants if str(v).strip()]
    placeholders = ','.join('?' for _ in variant_keys)
    checks = [
        ('listagent.db', f"SELECT upc FROM listing_queue WHERE LOWER(TRIM(COALESCE(upc, ''))) IN ({placeholders}) ORDER BY id DESC LIMIT 1"),
        ('listinglog.db', f"SELECT upc FROM listing_log WHERE LOWER(TRIM(COALESCE(upc, ''))) IN ({placeholders}) ORDER BY id DESC LIMIT 1"),
        ('ebayStore.db', f"SELECT UPC FROM INVENTORY WHERE LOWER(TRIM(COALESCE(UPC, ''))) IN ({placeholders}) ORDER BY ID DESC LIMIT 1"),
        ('amazonStore.db', f"SELECT UPC FROM ITEMS WHERE LOWER(TRIM(COALESCE(UPC, ''))) IN ({placeholders}) ORDER BY rowid DESC LIMIT 1"),
        ('bol.db', f"SELECT upc FROM bol_items WHERE LOWER(TRIM(COALESCE(upc, ''))) IN ({placeholders}) ORDER BY id DESC LIMIT 1"),
        ('rawbol.db', f"SELECT upc FROM raw_bol_items WHERE LOWER(TRIM(COALESCE(upc, ''))) IN ({placeholders}) ORDER BY rowid DESC LIMIT 1"),
        ('searchRack.db', f"SELECT BARCODE FROM SEARCHRACK WHERE LOWER(TRIM(COALESCE(BARCODE, ''))) IN ({placeholders}) ORDER BY ID DESC LIMIT 1"),
    ]
    for db_name, query in checks:
        value = _lookup_first_value(db_name, query, tuple(variant_keys))
        if value:
            return True
    return False

def resolve_barcode_from_marketplace_sku(sku, *, platform=None, item_id=None, fallback_barcode=None):
    sku_val = str(sku or '').strip()
    fallback = str(fallback_barcode or '').strip()
    item_id_val = str(item_id or '').strip()
    if not sku_val and not item_id_val:
        return fallback

    candidate_upcs = _extract_upc_candidates_from_sku(sku_val) if sku_val else []
    fallback_key = _normalize_marketplace_upc(fallback)
    for candidate in candidate_upcs:
        candidate_key = _normalize_marketplace_upc(candidate)
        if not fallback_key and '-' in candidate_key:
            return candidate
        if fallback_key and candidate_key:
            fallback_base = fallback_key.split('-', 1)[0]
            candidate_base = candidate_key.split('-', 1)[0]
            if candidate_key == fallback_key or candidate_base == fallback_base:
                return candidate

    search_plan = []
    platform_key = str(platform or '').strip().lower()
    if platform_key == 'amazon':
        if sku_val:
            search_plan.extend([
                ('listagent.db', "SELECT upc FROM listing_queue WHERE TRIM(COALESCE(listed_sku, '')) = ? COLLATE NOCASE ORDER BY COALESCE(listed_at, added_at) DESC, id DESC LIMIT 1", (sku_val,)),
                ('listinglog.db', "SELECT upc FROM listing_log WHERE TRIM(COALESCE(sku, '')) = ? COLLATE NOCASE ORDER BY created_at DESC, id DESC LIMIT 1", (sku_val,)),
                ('amazonStore.db', "SELECT UPC FROM ITEMS WHERE TRIM(COALESCE(SKU, '')) = ? COLLATE NOCASE ORDER BY LAST_UPDATED DESC LIMIT 1", (sku_val,)),
            ])
    elif platform_key == 'ebay':
        if sku_val:
            search_plan.extend([
                ('listagent.db', "SELECT upc FROM listing_queue WHERE TRIM(COALESCE(listed_sku, '')) = ? COLLATE NOCASE ORDER BY COALESCE(listed_at, added_at) DESC, id DESC LIMIT 1", (sku_val,)),
                ('listinglog.db', "SELECT upc FROM listing_log WHERE TRIM(COALESCE(sku, '')) = ? COLLATE NOCASE ORDER BY created_at DESC, id DESC LIMIT 1", (sku_val,)),
                ('ebayStore.db', "SELECT UPC FROM INVENTORY WHERE TRIM(COALESCE(SKU, '')) = ? COLLATE NOCASE ORDER BY ID DESC LIMIT 1", (sku_val,)),
            ])
    else:
        if sku_val:
            search_plan.extend([
                ('listagent.db', "SELECT upc FROM listing_queue WHERE TRIM(COALESCE(listed_sku, '')) = ? COLLATE NOCASE ORDER BY COALESCE(listed_at, added_at) DESC, id DESC LIMIT 1", (sku_val,)),
                ('listinglog.db', "SELECT upc FROM listing_log WHERE TRIM(COALESCE(sku, '')) = ? COLLATE NOCASE ORDER BY created_at DESC, id DESC LIMIT 1", (sku_val,)),
                ('ebayStore.db', "SELECT UPC FROM INVENTORY WHERE TRIM(COALESCE(SKU, '')) = ? COLLATE NOCASE ORDER BY ID DESC LIMIT 1", (sku_val,)),
                ('amazonStore.db', "SELECT UPC FROM ITEMS WHERE TRIM(COALESCE(SKU, '')) = ? COLLATE NOCASE ORDER BY LAST_UPDATED DESC LIMIT 1", (sku_val,)),
            ])

    if item_id_val:
        if platform_key == 'amazon':
            search_plan.append(('listagent.db', "SELECT upc FROM listing_queue WHERE TRIM(COALESCE(listed_asin, '')) = ? COLLATE NOCASE ORDER BY COALESCE(listed_at, added_at) DESC, id DESC LIMIT 1", (item_id_val,)))
            search_plan.append(('listinglog.db', "SELECT upc FROM listing_log WHERE TRIM(COALESCE(asin, '')) = ? COLLATE NOCASE ORDER BY created_at DESC, id DESC LIMIT 1", (item_id_val,)))
        elif platform_key == 'ebay':
            search_plan.append(('listagent.db', "SELECT upc FROM listing_queue WHERE (TRIM(COALESCE(listed_listing_id, '')) = ? COLLATE NOCASE OR TRIM(COALESCE(listed_offer_id, '')) = ? COLLATE NOCASE) ORDER BY COALESCE(listed_at, added_at) DESC, id DESC LIMIT 1", (item_id_val, item_id_val)))
            search_plan.append(('listinglog.db', "SELECT upc FROM listing_log WHERE (TRIM(COALESCE(listing_id, '')) = ? COLLATE NOCASE OR TRIM(COALESCE(offer_id, '')) = ? COLLATE NOCASE) ORDER BY created_at DESC, id DESC LIMIT 1", (item_id_val, item_id_val)))

    for db_name, query, params in search_plan:
        mapped = _lookup_first_value(db_name, query, params)
        if mapped:
            return str(mapped).strip()

    for candidate in candidate_upcs:
        if _upc_exists_in_local_sources(candidate):
            return candidate

    return fallback

def ensure_sold_orders_schema(cur, conn, default_store='ebay'):
    default_store = str(default_store or 'ebay').strip() or 'ebay'
    cur.execute('''CREATE TABLE IF NOT EXISTS orders (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        order_id TEXT,
        item_id TEXT,
        sku TEXT,
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
        store TEXT DEFAULT 'ebay',
        shipping_cost REAL,
        lot_number TEXT
    )''')

    try:
        cur.execute('PRAGMA table_info(orders)')
        cols = [r[1] for r in cur.fetchall()]
        for col_name, col_def in [
            ('barcode', 'TEXT'),
            ('rackupdated', 'INTEGER DEFAULT 0'),
            ('removal_cancelled', 'INTEGER DEFAULT 0'),
            ('store', f'TEXT DEFAULT "{default_store}"'),
            ('shipping_cost', 'REAL'),
            ('lot_number', 'TEXT'),
            ('sku', 'TEXT'),
        ]:
            if col_name not in cols:
                cur.execute(f'ALTER TABLE orders ADD COLUMN {col_name} {col_def}')
                conn.commit()
    except sqlite3.Error:
        pass

def _log_rack_history(barcode, title, quantity_removed, searchrack_id, old_qty, new_qty, removal_type, position):
    """Helper to log add/remove events to rackhistory.db, avoiding duplicate CREATE TABLE blocks."""
    try:
        with connect_db('rackhistory.db') as rem_conn:
            rem_cur = rem_conn.cursor()
            rem_cur.execute('''
                CREATE TABLE IF NOT EXISTS removed_items (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    order_id TEXT,
                    barcode TEXT,
                    title TEXT,
                    quantity_removed INTEGER,
                    removed_at TEXT,
                    searchrack_id INTEGER,
                    old_quantity INTEGER,
                    new_quantity INTEGER,
                    removal_type TEXT,
                    item_position TEXT
                )
            ''')
            rem_cur.execute('''
                INSERT INTO removed_items
                (order_id, barcode, title, quantity_removed, removed_at, searchrack_id, old_quantity, new_quantity, removal_type, item_position)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (None, barcode, title, quantity_removed, datetime.datetime.now().isoformat(), searchrack_id, old_qty, new_qty, removal_type, position))
    except Exception as log_err:
        print(f"Error logging to rack history: {log_err}")

def createRack():
    with connect_db('rack.db') as conn:
        cursor = conn.cursor()
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
        except sqlite3.Error as e:
            print("something went wrong with my Rack ", e)

def createEbayStoreDB():
    with connect_db('ebayStore.db') as conn:
        cursor = conn.cursor()
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
        except sqlite3.Error as e:
            print("something went wrong with table ", e)

def addToSearchRack(ITEM_POSITION=None, BARCODE=None, IMAGES=None, PICTUREPOSITION=None):
    """Add item directly to searchRack.db with enrichment from ebayStore.db and bol.db"""
    # Validate barcode and position before doing any DB work
    if not BARCODE or not str(BARCODE).strip():
        print("Error: BARCODE is empty or None, skipping addToSearchRack")
        return
    if not ITEM_POSITION or not str(ITEM_POSITION).strip():
        print("Error: ITEM_POSITION is empty or None, skipping addToSearchRack")
        return

    # Get title and other enrichment data from other DBs
    title = None
    itemid = None
    quantity = None
    image = None

    # First try ebayStore.db
    try:
        with connect_db('ebayStore.db') as ebay_conn:
            ebay_conn.row_factory = sqlite3.Row
            ebay_cur = ebay_conn.cursor()
            ebay_cur.execute("SELECT Title, ItemID, Quantity, Image FROM INVENTORY WHERE UPC = ? COLLATE NOCASE LIMIT 1", (BARCODE,))
            ebay_row = ebay_cur.fetchone()
            if ebay_row:
                title = ebay_row['Title']
                itemid = ebay_row['ItemID']
                quantity = ebay_row['Quantity']
                image = ebay_row['Image']
    except Exception as e:
        print(f"Warning: Could not lookup in ebayStore.db: {e}")

    # If not found in ebayStore, try bol.db
    if not title:
        try:
            with connect_db('bol.db') as bol_conn:
                bol_conn.row_factory = sqlite3.Row
                bol_cur = bol_conn.cursor()
                bol_cur.execute('SELECT item_description, image_url FROM bol_items WHERE upc = ? COLLATE NOCASE LIMIT 1', (BARCODE,))
                bol_row = bol_cur.fetchone()
                if bol_row:
                    title = bol_row['item_description']
                    image = bol_row['image_url']
                    itemid = BARCODE
        except Exception as e:
            print(f"Warning: Could not lookup in bol.db: {e}")

    barcode_norm = str(BARCODE).strip()
    position_norm = str(ITEM_POSITION).strip()
    has_picture = PICTUREPOSITION and str(PICTUREPOSITION).strip()

    with connect_db('searchRack.db') as conn:
        cursor = conn.cursor()
        try:
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
            except sqlite3.Error:
                pass

            # Acquire write lock before check-then-update to prevent lost increments
            cursor.execute('BEGIN IMMEDIATE')

            existing_same_location = None
            if not has_picture:
                cursor.execute("""
                    SELECT ID, QUANTITY FROM SEARCHRACK
                    WHERE TRIM(BARCODE) = ? COLLATE NOCASE
                    AND TRIM(ITEM_POSITION) = ? COLLATE NOCASE
                    AND (PICTUREPOSITION IS NULL OR TRIM(PICTUREPOSITION) = '')
                """, (barcode_norm, position_norm))
                existing_same_location = cursor.fetchone()

            now_iso = datetime.datetime.now().isoformat()

            if existing_same_location:
                existing_id, existing_qty = existing_same_location
                new_qty = (existing_qty or 0) + 1

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

                _log_rack_history(barcode_norm, title, 1, existing_id, existing_qty, new_qty, 'add_to_shelf', position_norm)
            else:
                cursor.execute("""
                    INSERT INTO SEARCHRACK (TITLE, BARCODE, ITEM_POSITION, IMAGES, PICTUREPOSITION, ITEMID, QUANTITY, IMAGE, CREATED_AT)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (title, barcode_norm, position_norm, IMAGES, PICTUREPOSITION, itemid, 1, image, now_iso))
                new_id = cursor.lastrowid
                if has_picture:
                    action = f"added new picture position entry (always unique): barcode={barcode_norm}, picture={PICTUREPOSITION}"
                else:
                    action = f"added new shelf entry: barcode={barcode_norm}, position={position_norm}"

                _log_rack_history(barcode_norm, title, 1, new_id, 0, 1, 'add_to_shelf', PICTUREPOSITION if has_picture else position_norm)

            print(f"{action} to searchRack: position={ITEM_POSITION}, barcode={BARCODE}, title={title}")
        except sqlite3.Error as e:
            print("Error in addToSearchRack:", e)

def ebayStoreDB(title, item_id, sku=None, price=None, quantity=None, image=None, List_State=None, Sold_Date=None, List_Date=None, URL=None):
    with connect_db('ebayStore.db') as conn:
        cursor = conn.cursor()
        cursor.execute('''CREATE TABLE IF NOT EXISTS INVENTORY (
            ID INTEGER PRIMARY KEY AUTOINCREMENT, Title TEXT, ItemID TEXT, SKU TEXT,
            Price TEXT, Quantity TEXT, Image TEXT, URL TEXT,
            List_State TEXT, Sold_Date TEXT, List_Date TEXT, isFound TEXT
        )''')
        cursor.execute("SELECT ID FROM INVENTORY WHERE ItemID = ?", (item_id,))
        existing = cursor.fetchone()
        if existing is not None:
            try:
                cursor.execute("""
                    UPDATE INVENTORY
                    SET Title = ?,
                        SKU = ?,
                        Price = ?,
                        Quantity = ?,
                        Image = ?,
                        URL = ?,
                        List_State = ?,
                        Sold_Date = ?,
                        List_Date = ?
                    WHERE ItemID = ?
                """, (title, sku, price, quantity, image, URL, List_State, Sold_Date, List_Date, item_id))
                print(f"Updated existing eBay listing {item_id}")
            except sqlite3.Error as e:
                print("something went wrong during eBay update", e)
            return
        try:
            cursor.execute("INSERT INTO INVENTORY (Title, ItemID, SKU, Price, Quantity, Image, List_State, Sold_Date, List_Date, URL) VALUES (?,?,?,?,?,?,?,?,?,?)",
                           (title, item_id, sku, price, quantity, image, List_State, Sold_Date, List_Date, URL))
            print(f"Added {title} successfully")
        except sqlite3.Error as e:
            print("something went wrong", e)

def createAmazonStoreDB():
    with connect_db('amazonStore.db') as conn:
        cursor = conn.cursor()
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
        except sqlite3.Error as e:
            print("Something went wrong with Amazon Store table:", e)

def amazonStoreDB(title, asin, sku=None, price=None, quantity=None, image=None, List_State=None, Sold_Date=None, List_Date=None, URL=None, upc=None):
    """Add item to amazonStore.db - similar to ebayStoreDB but uses ASIN instead of ItemID"""
    with connect_db('amazonStore.db') as conn:
        cursor = conn.cursor()
        cursor.execute('''CREATE TABLE IF NOT EXISTS INVENTORY (
            ID INTEGER PRIMARY KEY AUTOINCREMENT, Title TEXT, ASIN TEXT, SKU TEXT,
            Price TEXT, Quantity TEXT, Image TEXT, URL TEXT,
            List_State TEXT, Sold_Date TEXT, List_Date TEXT, UPC TEXT, isFound TEXT
        )''')
        cursor.execute("SELECT 1 FROM INVENTORY WHERE ASIN = ?", (asin,))
        if cursor.fetchone() is not None:
            print("Item already in Amazon inventory database, skipping")
            return
        try:
            cursor.execute("INSERT INTO INVENTORY (Title, ASIN, SKU, Price, Quantity, Image, List_State, Sold_Date, List_Date, URL, UPC) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                           (title, asin, sku, price, quantity, image, List_State, Sold_Date, List_Date, URL, upc))
            print(f"Added {title} to Amazon Store successfully")
        except sqlite3.Error as e:
            print("Something went wrong adding to Amazon Store:", e)

def insert_bol_items(df, import_date):
    """
    Insert BOL items from DataFrame into bol.db (bol_items table).
    Skips duplicates by UPC. Returns {'success': True, 'inserted': n} or error dict.
    """
    try:
        with connect_db('bol.db') as conn:
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
                           float(row['CLIENT COST']) if pd and not pd.isna(row['CLIENT COST']) else None,
                           float(row['TOTAL CLIENT COST']) if pd and not pd.isna(row['TOTAL CLIENT COST']) else None,
                           str(row['IMAGE']).strip(),
                           str(row['LOT #']).strip(),
                           str(row['BOL #']).strip(),
                           import_date))
                inserted += 1
            return {'success': True, 'inserted': inserted}
    except Exception as e:
        return {'success': False, 'error': str(e)}

def store_ebay_order(order):
    """Insert a sold order into sold.db (orders table), skipping duplicates by order_id+item_id."""
    with connect_db('sold.db') as conn:
        cur = conn.cursor()
        ensure_sold_orders_schema(cur, conn, default_store='ebay')

        sku_val = str(order.get('sku') or '').strip() or None

        # Get barcode (UPC) from ebayStore.db using item_id
        barcode_val = order.get('barcode')
        if not barcode_val and order.get('item_id'):
            try:
                with connect_db('ebayStore.db') as ebay_conn:
                    ebay_cur = ebay_conn.cursor()
                    ebay_cur.execute('SELECT UPC, SKU FROM INVENTORY WHERE ItemID = ?', (order.get('item_id'),))
                    row = ebay_cur.fetchone()
                    if row:
                        if row[0]:
                            barcode_val = row[0]
                        if not sku_val and len(row) > 1 and row[1]:
                            sku_val = str(row[1]).strip() or sku_val
            except Exception:
                pass

        resolved_barcode = resolve_barcode_from_marketplace_sku(
            sku_val,
            platform='ebay',
            item_id=order.get('item_id'),
            fallback_barcode=barcode_val
        )
        if resolved_barcode:
            barcode_val = resolved_barcode

        # Enrich title, image, and lot_number
        title_val = order.get('title')
        image_val = order.get('image')
        lot_number_val = None

        # First try to get image from ebayStore.db using item_id
        if not image_val and order.get('item_id'):
            try:
                with connect_db('ebayStore.db') as ebay_conn:
                    ebay_cur = ebay_conn.cursor()
                    ebay_cur.execute('SELECT Image FROM INVENTORY WHERE ItemID = ?', (order.get('item_id'),))
                    row = ebay_cur.fetchone()
                    if row and row[0]:
                        image_val = row[0]
            except Exception:
                pass

        # Then try rawbol.db using barcode
        if barcode_val:
            try:
                with connect_db('rawbol.db') as bol_conn:
                    bol_conn.row_factory = sqlite3.Row
                    bol_cur = bol_conn.cursor()
                    bol_cur.execute('SELECT item_description, image_url FROM raw_bol_items WHERE upc = ? COLLATE NOCASE LIMIT 1', (barcode_val,))
                    row_bol = bol_cur.fetchone()
                    if not row_bol and barcode_val.startswith('0'):
                        barcode_no_zero = barcode_val.lstrip('0') if str(barcode_val).isdigit() else barcode_val
                        bol_cur.execute('SELECT item_description, image_url FROM raw_bol_items WHERE upc = ? COLLATE NOCASE LIMIT 1', (barcode_no_zero,))
                        row_bol = bol_cur.fetchone()
                    if row_bol:
                        if not title_val and row_bol['item_description']:
                            title_val = row_bol['item_description']
                        if not image_val and row_bol['image_url']:
                            img_url = row_bol['image_url']
                            if str(img_url).lower() not in ['nan', 'none', 'null', '']:
                                image_val = img_url
            except Exception:
                pass

        # Get lot_number from rawbol.db
        if barcode_val and not lot_number_val:
            try:
                with connect_db('rawbol.db') as rawbol_conn:
                    rawbol_conn.row_factory = sqlite3.Row
                    rawbol_cur = rawbol_conn.cursor()
                    rawbol_cur.execute('SELECT lot_number FROM raw_bol_items WHERE upc = ? COLLATE NOCASE LIMIT 1', (barcode_val,))
                    row_rawbol = rawbol_cur.fetchone()
                    if row_rawbol and row_rawbol['lot_number']:
                        lot_num = row_rawbol['lot_number']
                        if str(lot_num).lower() not in ['', 'nan', 'none', 'null']:
                            lot_number_val = lot_num
            except Exception:
                pass

        # Prepare location
        location_val = order.get('location')
        if not location_val and order.get('item_id'):
            try:
                with connect_db('searchRack.db') as rack_conn:
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
            except Exception:
                pass

        # Check for duplicate row before insert.
        # Some edge-case eBay payloads may omit item_id; use a stricter fallback key
        # so repeat sync passes do not insert clones.
        order_id_val = order.get('order_id')
        item_id_val = order.get('item_id')
        existing = None
        if item_id_val is not None and str(item_id_val).strip() != '':
            cur.execute(
                '''
                SELECT id
                FROM orders
                WHERE COALESCE(order_id, '') = COALESCE(?, '')
                  AND COALESCE(item_id, '') = COALESCE(?, '')
                ORDER BY id DESC
                LIMIT 1
                ''',
                (order_id_val, item_id_val)
            )
            existing = cur.fetchone()
        else:
            cur.execute(
                '''
                SELECT id
                FROM orders
                WHERE COALESCE(order_id, '') = COALESCE(?, '')
                  AND COALESCE(item_id, '') = ''
                  AND COALESCE(barcode, '') = COALESCE(?, '')
                  AND COALESCE(title, '') = COALESCE(?, '')
                  AND COALESCE(quantity, 0) = COALESCE(?, 0)
                ORDER BY id DESC
                LIMIT 1
                ''',
                (order_id_val, barcode_val, title_val, order.get('quantity'))
            )
            existing = cur.fetchone()
        if existing:
            cur.execute('''UPDATE orders SET
                checkout_status = COALESCE(?, checkout_status),
                shipping_name = COALESCE(?, shipping_name),
                shipping_street1 = COALESCE(?, shipping_street1),
                shipping_street2 = COALESCE(?, shipping_street2),
                shipping_city = COALESCE(?, shipping_city),
                shipping_state = COALESCE(?, shipping_state),
                shipping_postal_code = COALESCE(?, shipping_postal_code),
                shipping_country = COALESCE(?, shipping_country),
                paid_time = COALESCE(?, paid_time),
                shipped_time = COALESCE(?, shipped_time),
                title = COALESCE(?, title),
                sku = COALESCE(?, sku),
                quantity = COALESCE(?, quantity),
                price = COALESCE(?, price),
                seller_fee = COALESCE(?, seller_fee),
                taxes = COALESCE(?, taxes),
                image = COALESCE(?, image),
                barcode = COALESCE(?, barcode),
                location = COALESCE(?, location),
                shipping_cost = COALESCE(?, shipping_cost),
                lot_number = COALESCE(?, lot_number)
                WHERE id = ?''',
                (
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
                    title_val,
                    sku_val,
                    order.get('quantity'),
                    order.get('price'),
                    order.get('seller_fee'),
                    order.get('taxes'),
                    image_val,
                    barcode_val,
                    location_val,
                    order.get('shipping_cost'),
                    lot_number_val,
                    existing[0]
                )
            )
            return

        # If not a duplicate, proceed with INSERT
        cur.execute('''INSERT INTO orders (
            order_id, item_id, sku, title, quantity, price, checkout_status, shipping_name, shipping_street1, shipping_street2, shipping_city, shipping_state, shipping_postal_code, shipping_country, paid_time, shipped_time, seller_fee, taxes, fees, image, isHandled, isHandledDate, location, barcode, store, shipping_cost, lot_number
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
            (
                order.get('order_id'),
                order.get('item_id'),
                sku_val,
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
                'ebay',
                order.get('shipping_cost'),
                lot_number_val
            )
        )

def createSearchRackDB():
    """Create SEARCHRACK table structure if it doesn't exist"""
    with connect_db('searchRack.db') as search_conn:
        search_cur = search_conn.cursor()
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
        try:
            search_cur.execute('PRAGMA table_info(SEARCHRACK)')
            cols = [r[1] for r in search_cur.fetchall()]
            if 'CREATED_AT' not in cols:
                search_cur.execute('ALTER TABLE SEARCHRACK ADD COLUMN CREATED_AT TEXT')
                search_conn.commit()
        except sqlite3.Error:
            pass

def enrich_searchrack_db(batch_size=500, do_backup=True):
    """Enrich SEARCHRACK rows by looking up BARCODE/UPC in ebayStore.db (preferred) then bol.db.
    Updates SEARCHRACK.TITLE, ITEMID, QUANTITY, IMAGES when available.
    Runs in batches to avoid large transactions. Creates a timestamped backup if requested.
    """
    import time, shutil

    if do_backup:
        try:
            backup_src = os.path.join(BASE_DIR, 'searchRack.db')
            backup_path = os.path.join(BASE_DIR, 'searchRack.db.bak')
            if os.path.exists(backup_path):
                try:
                    os.remove(backup_path)
                except Exception:
                    pass
            shutil.copyfile(backup_src, backup_path)
            print(f'Backup created: {backup_path}')
        except Exception as e:
            print('Warning: failed to create backup of searchRack.db:', e)

    # This function manages its own connection lifecycle because it's long-running
    s_conn = sqlite3.connect(os.path.join(BASE_DIR, 'searchRack.db'), timeout=30.0)
    s_conn.row_factory = sqlite3.Row
    s_cur = s_conn.cursor()
    try:
        # Ensure enrichment columns exist
        try:
            s_cur.execute('PRAGMA table_info(SEARCHRACK)')
            cols = [r[1] for r in s_cur.fetchall()]
            if 'ITEMID' not in cols:
                s_cur.execute('ALTER TABLE SEARCHRACK ADD COLUMN ITEMID TEXT')
            if 'QUANTITY' not in cols:
                s_cur.execute('ALTER TABLE SEARCHRACK ADD COLUMN QUANTITY INTEGER')
            if 'IMAGE' not in cols and 'IMAGES' not in cols:
                s_cur.execute('ALTER TABLE SEARCHRACK ADD COLUMN IMAGE TEXT')
            s_conn.commit()
        except sqlite3.Error:
            pass

        # Ensure metadata table exists to track incremental progress
        try:
            s_cur.execute("CREATE TABLE IF NOT EXISTS ENRICH_META (k TEXT PRIMARY KEY, v TEXT)")
            s_conn.commit()
        except sqlite3.Error:
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

        # Gather barcodes to process incrementally
        barcodes_set = set()
        if current_searchrack_max > stored_searchrack_max:
            s_cur.execute('SELECT ID, BARCODE FROM SEARCHRACK WHERE ID > ?', (stored_searchrack_max,))
            new_rows = s_cur.fetchall()
            for r in new_rows:
                b = (r['BARCODE'] or '').strip()
                if b:
                    barcodes_set.add(b)

        # Include rows that lack a TITLE
        try:
            s_cur.execute("SELECT DISTINCT BARCODE FROM SEARCHRACK WHERE TITLE IS NULL OR TRIM(TITLE) = ''")
            for (b,) in s_cur.fetchall():
                if b:
                    barcodes_set.add(str(b).strip())
        except sqlite3.Error:
            pass

        # New UPCs from ebayStore
        current_ebay_max = stored_ebay_max
        try:
            with connect_db('ebayStore.db') as es_conn:
                es_cur = es_conn.cursor()
                es_cur.execute('SELECT COALESCE(MAX(rowid),0) FROM INVENTORY')
                current_ebay_max = int(es_cur.fetchone()[0] or 0)
                if current_ebay_max > stored_ebay_max:
                    es_cur.execute('SELECT UPC, ItemID FROM INVENTORY WHERE rowid > ?', (stored_ebay_max,))
                    for upc, itemid in es_cur.fetchall():
                        key = (upc or itemid or '')
                        if key:
                            barcodes_set.add(str(key).strip())
        except Exception:
            current_ebay_max = stored_ebay_max

        # New bol items
        current_bol_max = stored_bol_max
        try:
            with connect_db('bol.db') as bol_conn:
                bol_cur = bol_conn.cursor()
                bol_cur.execute('SELECT COALESCE(MAX(rowid),0) FROM bol_items')
                current_bol_max = int(bol_cur.fetchone()[0] or 0)
                if current_bol_max > stored_bol_max:
                    bol_cur.execute('SELECT upc FROM bol_items WHERE rowid > ?', (stored_bol_max,))
                    for (upc,) in bol_cur.fetchall():
                        if upc:
                            barcodes_set.add(str(upc).strip())
        except Exception:
            current_bol_max = stored_bol_max

        if not barcodes_set and current_searchrack_max <= stored_searchrack_max and current_ebay_max <= stored_ebay_max and current_bol_max <= stored_bol_max:
            print('No changes detected for enrichment; skipping work')
            return

        barcodes = list(barcodes_set)
        print(f'Incremental enrichment will process {len(barcodes)} unique barcodes (batches of {batch_size})')

        def chunks(lst, n):
            for i in range(0, len(lst), n):
                yield lst[i:i+n]

        # Build enrichment map from bol.db FIRST, then ebayStore, then Amazon
        enrichment = {}
        norm_barcodes = [str(b).strip() for b in barcodes]

        # PRIORITY 1: bol.db
        try:
            with connect_db('bol.db') as bol_conn:
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
        except Exception as e:
            print('Warning: bol lookup failed:', e)

        # PRIORITY 2: ebayStore for remaining
        remaining = [b for b in norm_barcodes if b not in enrichment and b.lower() not in enrichment]
        try:
            if remaining:
                with connect_db('ebayStore.db') as es_conn:
                    es_conn.row_factory = sqlite3.Row
                    es_cur = es_conn.cursor()
                    for batch in chunks(remaining, batch_size):
                        placeholders = ','.join(['?'] * len(batch))
                        sql = f"SELECT UPC, Title, ItemID, Quantity, Image FROM INVENTORY WHERE (UPC IN ({placeholders}) OR ItemID IN ({placeholders}))"
                        params = batch + batch
                        es_cur.execute(sql, params)
                        for r in es_cur.fetchall():
                            upc = (r['UPC'] or '')
                            itemid = (r['ItemID'] or '')
                            keys = set()
                            if upc:
                                keys.add(str(upc).strip())
                                keys.add(str(upc).strip().lower())
                            if itemid:
                                keys.add(str(itemid).strip())
                                keys.add(str(itemid).strip().lower())
                            for key in keys:
                                enrichment[key] = {'title': r['Title'], 'itemid': r['ItemID'], 'quantity': r['Quantity'], 'image': r['Image'], 'source': 'ebayStore'}
        except Exception as e:
            print('Warning: ebayStore lookup failed:', e)

        # PRIORITY 3: Amazon for still remaining
        still_remaining = [b for b in norm_barcodes if b not in enrichment and b.lower() not in enrichment]
        try:
            if still_remaining:
                with connect_db('amazonStore.db') as amazon_conn:
                    amazon_conn.row_factory = sqlite3.Row
                    amazon_cur = amazon_conn.cursor()

                    with connect_db('rawbol.db') as rawbol_conn:
                        rawbol_conn.row_factory = sqlite3.Row
                        rawbol_cur = rawbol_conn.cursor()

                        for batch in chunks(still_remaining, batch_size):
                            placeholders = ','.join(['?'] * len(batch))
                            amazon_cur.execute(f"SELECT UPC, TITLE, ASIN, IMAGE FROM ITEMS WHERE UPC IN ({placeholders})", batch)
                            for r in amazon_cur.fetchall():
                                upc = (r['UPC'] or '').strip()
                                if not upc:
                                    continue
                                image_url = r['IMAGE']
                                if not image_url:
                                    rawbol_cur.execute("SELECT image_url FROM raw_bol_items WHERE upc = ?", (upc,))
                                    rawbol_row = rawbol_cur.fetchone()
                                    if not rawbol_row and upc.startswith('0'):
                                        upc_no_zero = upc.lstrip('0') if str(upc).isdigit() else upc
                                        rawbol_cur.execute("SELECT image_url FROM raw_bol_items WHERE upc = ?", (upc_no_zero,))
                                        rawbol_row = rawbol_cur.fetchone()
                                    if rawbol_row:
                                        image_url = rawbol_row['image_url']
                                keys = {str(upc).strip(), str(upc).strip().lower()}
                                for key in keys:
                                    enrichment[key] = {
                                        'title': r['TITLE'],
                                        'itemid': r['ASIN'],
                                        'quantity': None,
                                        'image': image_url,
                                        'source': 'amazonStore'
                                    }
        except Exception as e:
            print('Warning: Amazon lookup failed:', e)

        # Build mapping of barcode -> [ids] from SEARCHRACK
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
                with s_conn:
                    for barcode, ids_list in batch_ids:
                        data = enrichment.get(barcode)
                        if not data:
                            continue
                        for rid in ids_list:
                            try:
                                s_cur.execute('SELECT TITLE, IMAGES, IMAGE, ITEMID, QUANTITY FROM SEARCHRACK WHERE ID = ?', (rid,))
                                currow = s_cur.fetchone()
                                curtitle = currow[0] if currow else None
                                curimages = currow[1] if currow else None
                                curimage = currow[2] if currow else None
                                curitemid = currow[3] if currow else None
                                src_title = data.get('title')
                                write_title = False
                                if src_title:
                                    if not curtitle or str(curtitle).strip() == '':
                                        write_title = True
                                    elif str(curtitle).strip().lower() != str(src_title).strip().lower():
                                        write_title = True
                                new_title = src_title if write_title else curtitle
                                new_image = curimage or curimages or data.get('image')
                                new_itemid = curitemid or data.get('itemid')
                                if write_title or new_image != curimage or new_itemid != curitemid:
                                    s_cur.execute('UPDATE SEARCHRACK SET TITLE = ?, IMAGE = ?, ITEMID = ? WHERE ID = ?', (new_title, new_image, new_itemid, rid))
                                    total_updates += 1
                            except Exception:
                                continue
            s_conn.commit()
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
    print("Processing sold orders for inventory reduction...")

    try:
        # Use raw connections here because this is a long-running cross-DB operation
        sold_conn = sqlite3.connect(os.path.join(BASE_DIR, 'sold.db'), timeout=30.0)
        sold_conn.row_factory = sqlite3.Row
        sold_cur = sold_conn.cursor()

        rack_conn = None
        rem_conn = None

        try:
            # Ensure settings table exists and read grace period
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
            except sqlite3.Error as e:
                print(f"  Could not add removal_cancelled column: {e}")

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
                print("  No unprocessed sold orders ready for inventory reduction")
                return

            print(f"  Found {len(unprocessed_orders)} shipped orders, checking grace period (grace={grace_period_hours}h)...")

            now = datetime.datetime.utcnow()
            eligible_orders = []

            for order in unprocessed_orders:
                try:
                    shipped_str = order['shipped_time']
                    if 'T' in shipped_str:
                        if shipped_str.endswith('Z'):
                            shipped_dt = datetime.datetime.fromisoformat(shipped_str.replace('Z', '+00:00'))
                        else:
                            shipped_dt = datetime.datetime.fromisoformat(shipped_str)
                    else:
                        shipped_dt = datetime.datetime.fromisoformat(shipped_str)
                    if shipped_dt.tzinfo:
                        shipped_dt = shipped_dt.replace(tzinfo=None)
                    hours_since_shipped = (now - shipped_dt).total_seconds() / 3600
                    if hours_since_shipped >= grace_period_hours:
                        eligible_orders.append(order)
                    else:
                        remaining = grace_period_hours - hours_since_shipped
                        print(f"  Order {order['order_id']}: {remaining:.1f}h remaining in grace period")
                except Exception as e:
                    print(f"  Could not parse shipped_time for order {order['order_id']}: {e}")
                    continue

            if not eligible_orders:
                print("  No orders past grace period")
                return

            print(f"  Found {len(eligible_orders)} orders eligible for inventory reduction")

            rack_conn = sqlite3.connect(os.path.join(BASE_DIR, 'searchRack.db'), timeout=30.0)
            rack_cur = rack_conn.cursor()

            rem_conn = sqlite3.connect(os.path.join(BASE_DIR, 'rackhistory.db'), timeout=30.0)
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
            try:
                rem_cur.execute('PRAGMA table_info(removed)')
                cols = [r[1] for r in rem_cur.fetchall()]
                if 'undone_at' not in cols:
                    rem_cur.execute('ALTER TABLE removed ADD COLUMN undone_at TEXT')
                    rem_conn.commit()
            except sqlite3.Error:
                pass

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
                    rack_cur.execute(f"SELECT ID, {qty_col} FROM SEARCHRACK WHERE ITEMID = ? COLLATE NOCASE", (barcode,))
                    rack_item = rack_cur.fetchone()

                    if rack_item:
                        rack_id = rack_item[0]
                        current_qty = rack_item[1] or 0
                        new_qty = max(0, current_qty - sold_qty)

                        rack_cur.execute(f"UPDATE SEARCHRACK SET {qty_col} = ? WHERE ID = ?", (new_qty, rack_id))
                        rack_conn.commit()

                        try:
                            name_val = order['title'] if 'title' in order.keys() else None
                            if not name_val:
                                rack_cur.execute("SELECT TITLE FROM SEARCHRACK WHERE ID = ?", (rack_id,))
                                rr = rack_cur.fetchone()
                                name_val = rr[0] if rr and rr[0] else ''
                            time_iso = datetime.datetime.utcnow().isoformat() + 'Z'
                            rem_cur.execute(
                                "INSERT INTO removed (name, barcode, qty, time_removed) VALUES (?,?,?,?)",
                                (name_val or '', barcode, int(sold_qty) if sold_qty else 1, time_iso)
                            )
                            rem_conn.commit()
                        except Exception:
                            pass

                        print(f"  Reduced {barcode}: {current_qty} -> {new_qty} (sold {sold_qty})")
                        updated_count += 1
                    else:
                        print(f"  Barcode {barcode} not found in searchRack (Order: {order['order_id']})")
                        not_found_count += 1
                        try:
                            time_iso = datetime.datetime.utcnow().isoformat() + 'Z'
                            name_val = order['title'] if 'title' in order.keys() else None
                            rem_cur.execute(
                                "INSERT INTO removed (name, barcode, qty, time_removed) VALUES (?,?,?,?)",
                                (name_val or '', barcode, int(sold_qty) if sold_qty else 1, time_iso)
                            )
                            rem_conn.commit()
                        except Exception:
                            pass

                except Exception as e:
                    print(f"  Error processing barcode {barcode}: {e}")

                finally:
                    sold_cur.execute("UPDATE orders SET rackupdated = 1 WHERE id = ?", (order_id,))
                    sold_conn.commit()

            print(f"Inventory reduction complete:")
            print(f"   - Updated: {updated_count} items")
            print(f"   - Not found: {not_found_count} items")
            print(f"   - Total processed: {len(eligible_orders)} orders")

        finally:
            sold_conn.close()
            if rack_conn:
                rack_conn.close()
            if rem_conn:
                rem_conn.close()

    except Exception as e:
        print(f"Error in process_sold_orders_inventory_reduction: {e}")
        import traceback
        traceback.print_exc()

def enrich_amazon_sold_images():
    """Enrich Amazon sold orders with images from amazonStore.db"""
    try:
        with connect_db('sold.db') as sold_conn:
            sold_cur = sold_conn.cursor()

            with connect_db('amazonStore.db') as amazon_conn:
                amazon_conn.row_factory = sqlite3.Row
                amazon_cur = amazon_conn.cursor()

                sold_cur.execute('''
                    SELECT id, item_id, barcode
                    FROM orders
                    WHERE store = 'amazon'
                    AND (image IS NULL OR image = '')
                ''')

                orders_without_images = sold_cur.fetchall()
                updated = 0

                print(f"Found {len(orders_without_images)} Amazon sold orders without images")

                for order_id, item_id, barcode in orders_without_images:
                    image = None
                    if item_id:
                        amazon_cur.execute('SELECT IMAGE FROM ITEMS WHERE ASIN = ? LIMIT 1', (item_id,))
                        row = amazon_cur.fetchone()
                        if row and row['IMAGE']:
                            image = row['IMAGE']
                    if not image and barcode:
                        amazon_cur.execute('SELECT IMAGE FROM ITEMS WHERE UPC = ? LIMIT 1', (barcode,))
                        row = amazon_cur.fetchone()
                        if row and row['IMAGE']:
                            image = row['IMAGE']
                    if image:
                        sold_cur.execute('UPDATE orders SET image = ? WHERE id = ?', (image, order_id))
                        updated += 1

                print(f"Enriched {updated}/{len(orders_without_images)} Amazon sold orders with images")
                return updated

    except Exception as e:
        print(f"Error enriching Amazon images: {e}")
        import traceback
        traceback.print_exc()
        return 0
