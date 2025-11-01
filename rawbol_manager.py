"""
Raw BOL database manager - handles rawbol.db operations
"""
import sqlite3
import datetime

def ensure_rawbol_db():
    """Create rawbol.db and raw_bol_items table if not exists."""
    conn = sqlite3.connect('rawbol.db')
    cur = conn.cursor()
    cur.execute('''CREATE TABLE IF NOT EXISTS raw_bol_items (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        upc TEXT,
        item_description TEXT,
        avg_cost REAL,
        image_url TEXT,
        quantity INTEGER DEFAULT 1,
        lot_number TEXT,
        bol_location TEXT,
        import_date TEXT,
        created_at TEXT
    )''')
    
    # Create upload log table
    cur.execute('''CREATE TABLE IF NOT EXISTS upload_logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        filename TEXT,
        lot_number TEXT,
        bol_location TEXT,
        import_date TEXT,
        rows_imported INTEGER,
        uploaded_at TEXT,
        total_client_cost REAL
    )''')
    
    # Create sync history table
    cur.execute('''CREATE TABLE IF NOT EXISTS sync_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        sync_date TEXT,
        items_updated INTEGER,
        items_inserted INTEGER,
        changes_json TEXT
    )''')
    
    # Create synced lots tracking table
    cur.execute('''CREATE TABLE IF NOT EXISTS synced_lots (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        lot_number TEXT UNIQUE,
        sync_date TEXT,
        items_count INTEGER
    )''')
    
    # Migration: Add total_client_cost column to upload_logs if it doesn't exist
    try:
        cur.execute("SELECT total_client_cost FROM upload_logs LIMIT 1")
    except sqlite3.OperationalError:
        print("Adding total_client_cost column to upload_logs table...")
        cur.execute("ALTER TABLE upload_logs ADD COLUMN total_client_cost REAL")
        conn.commit()
        print("Column added successfully!")
    
    # Migration: Remove old cost columns and add avg_cost to raw_bol_items if needed
    cur.execute("PRAGMA table_info(raw_bol_items)")
    columns = [col[1] for col in cur.fetchall()]
    
    if 'client_cost' in columns or 'total_client_cost' in columns:
        print("Migrating raw_bol_items table structure...")
        # Create new table with correct schema
        cur.execute('''CREATE TABLE IF NOT EXISTS raw_bol_items_new (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            upc TEXT,
            item_description TEXT,
            avg_cost REAL,
            image_url TEXT,
            quantity INTEGER DEFAULT 1,
            lot_number TEXT,
            bol_number TEXT,
            bol_location TEXT,
            import_date TEXT,
            created_at TEXT
        )''')
        
        # Copy data from old table (client_cost becomes avg_cost as fallback)
        cur.execute('''INSERT INTO raw_bol_items_new 
            (id, upc, item_description, avg_cost, image_url, quantity, lot_number, bol_number, bol_location, import_date, created_at)
            SELECT id, upc, item_description, 
                   CASE WHEN client_cost IS NOT NULL THEN client_cost ELSE 0 END,
                   image_url, quantity, lot_number, bol_number, NULL, import_date, created_at
            FROM raw_bol_items''')
        
        # Drop old table and rename new one
        cur.execute("DROP TABLE raw_bol_items")
        cur.execute("ALTER TABLE raw_bol_items_new RENAME TO raw_bol_items")
        conn.commit()
        print("Table migration completed successfully!")
    elif 'avg_cost' not in columns:
        print("Adding avg_cost column to raw_bol_items table...")
        cur.execute("ALTER TABLE raw_bol_items ADD COLUMN avg_cost REAL")
        conn.commit()
        print("Column added successfully!")
    
    # Migration: Remove bol_number column from raw_bol_items if it exists
    if 'bol_number' in columns:
        print("Removing bol_number column from raw_bol_items table...")
        # Create new table without bol_number
        cur.execute('''CREATE TABLE IF NOT EXISTS raw_bol_items_temp (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            upc TEXT,
            item_description TEXT,
            avg_cost REAL,
            image_url TEXT,
            quantity INTEGER DEFAULT 1,
            lot_number TEXT,
            bol_location TEXT,
            import_date TEXT,
            created_at TEXT
        )''')
        
        # Copy data
        cur.execute('''INSERT INTO raw_bol_items_temp 
            (id, upc, item_description, avg_cost, image_url, quantity, lot_number, bol_location, import_date, created_at)
            SELECT id, upc, item_description, avg_cost, image_url, quantity, lot_number, bol_location, import_date, created_at
            FROM raw_bol_items''')
        
        # Drop old and rename
        cur.execute("DROP TABLE raw_bol_items")
        cur.execute("ALTER TABLE raw_bol_items_temp RENAME TO raw_bol_items")
        conn.commit()
        print("Column removed successfully!")
    
    # Migration: Add bol_location column if it doesn't exist
    if 'bol_location' not in columns and 'bol_number' not in columns:
        cur.execute("PRAGMA table_info(raw_bol_items)")
        columns = [col[1] for col in cur.fetchall()]
        if 'bol_location' not in columns:
            print("Adding bol_location column to raw_bol_items table...")
            cur.execute("ALTER TABLE raw_bol_items ADD COLUMN bol_location TEXT")
            conn.commit()
            print("Column added successfully!")
    
    # Migration: Remove bol_number and ensure bol_location in upload_logs
    cur.execute("PRAGMA table_info(upload_logs)")
    log_columns = [col[1] for col in cur.fetchall()]
    
    if 'bol_number' in log_columns:
        print("Removing bol_number column from upload_logs table...")
        # Create new table without bol_number
        cur.execute('''CREATE TABLE IF NOT EXISTS upload_logs_temp (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            filename TEXT,
            lot_number TEXT,
            bol_location TEXT,
            import_date TEXT,
            rows_imported INTEGER,
            uploaded_at TEXT,
            total_client_cost REAL
        )''')
        
        # Copy data
        cur.execute('''INSERT INTO upload_logs_temp 
            (id, filename, lot_number, bol_location, import_date, rows_imported, uploaded_at, total_client_cost)
            SELECT id, filename, lot_number, bol_location, import_date, rows_imported, uploaded_at, total_client_cost
            FROM upload_logs''')
        
        # Drop old and rename
        cur.execute("DROP TABLE upload_logs")
        cur.execute("ALTER TABLE upload_logs_temp RENAME TO upload_logs")
        conn.commit()
        print("Column removed successfully!")
    
    if 'bol_location' not in log_columns and 'bol_number' not in log_columns:
        cur.execute("PRAGMA table_info(upload_logs)")
        log_columns = [col[1] for col in cur.fetchall()]
        if 'bol_location' not in log_columns:
            print("Adding bol_location column to upload_logs table...")
            cur.execute("ALTER TABLE upload_logs ADD COLUMN bol_location TEXT")
            conn.commit()
            print("Column added successfully!")
    
    conn.commit()
    conn.close()

def insert_raw_bol_items(df, lot_number, import_date, avg_cost=None, bol_location=None):
    """
    Insert raw BOL items from DataFrame into rawbol.db.
    Only extracts: UPC, ITEM DESCRIPTION, ORIGINAL QTY, IMAGE
    avg_cost is calculated as total_bol_cost / total_bol_qty and applied to all items
    lot_number is the extracted LOT # from the file
    Does NOT check for duplicates - allows re-imports.
    Returns: {'success': True, 'inserted': n} or error dict.
    """
    try:
        ensure_rawbol_db()
        conn = sqlite3.connect('rawbol.db')
        cur = conn.cursor()
        
        inserted = 0
        created_at = datetime.datetime.utcnow().isoformat()
        
        for _, row in df.iterrows():
            upc = str(row.get('UPC', '')).strip()
            if not upc or upc.lower() == 'nan':
                continue
            
            # Extract quantity if present, default to 1
            qty = 1
            if 'QUANTITY' in row:
                try:
                    qty = int(row['QUANTITY'])
                except:
                    qty = 1
            elif 'QTY' in row:
                try:
                    qty = int(row['QTY'])
                except:
                    qty = 1
            elif 'ORIGINAL QTY' in row:
                try:
                    qty = int(row['ORIGINAL QTY'])
                except:
                    qty = 1
            
            # Extract image URL from Excel HYPERLINK formula if present
            image_raw = str(row.get('IMAGE', '')).strip()
            image_url = image_raw
            
            # Check if it's an Excel HYPERLINK formula: =HYPERLINK("url")
            if image_raw.startswith('=HYPERLINK('):
                import re
                # Extract URL from =HYPERLINK("url") or =HYPERLINK("url", "text")
                match = re.search(r'=HYPERLINK\("([^"]+)"', image_raw)
                if match:
                    image_url = match.group(1).strip()
            
            cur.execute('''INSERT INTO raw_bol_items 
                (upc, item_description, avg_cost, image_url, quantity, lot_number, bol_location, import_date, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)''',
                (upc,
                 str(row.get('ITEM DESCRIPTION', '')).strip(),
                 avg_cost,
                 image_url,
                 qty,
                 lot_number,
                 bol_location,
                 import_date,
                 created_at))
            inserted += 1
        
        conn.commit()
        conn.close()
        return {'success': True, 'inserted': inserted}
    except Exception as e:
        return {'success': False, 'error': str(e)}

def log_upload(filename, lot_number, import_date, rows_imported, total_client_cost=None, bol_location=None):
    """Log an upload to upload_logs table. lot_number is the extracted LOT # from file."""
    try:
        ensure_rawbol_db()
        conn = sqlite3.connect('rawbol.db')
        cur = conn.cursor()
        uploaded_at = datetime.datetime.utcnow().isoformat()
        cur.execute('''INSERT INTO upload_logs 
            (filename, lot_number, bol_location, import_date, rows_imported, uploaded_at, total_client_cost)
            VALUES (?, ?, ?, ?, ?, ?, ?)''',
            (filename, lot_number, bol_location, import_date, rows_imported, uploaded_at, total_client_cost))
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        print(f'Failed to log upload: {e}')
        return False

def get_all_raw_bol_items():
    """Get all raw BOL items for viewing."""
    try:
        ensure_rawbol_db()
        conn = sqlite3.connect('rawbol.db')
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute('SELECT * FROM raw_bol_items ORDER BY created_at DESC')
        rows = [dict(r) for r in cur.fetchall()]
        conn.close()
        return {'success': True, 'items': rows}
    except Exception as e:
        return {'success': False, 'error': str(e)}

def get_upload_logs():
    """Get all upload logs with total quantity for each lot."""
    try:
        ensure_rawbol_db()
        conn = sqlite3.connect('rawbol.db')
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        # Join with a subquery to get total quantity per lot
        cur.execute('''
            SELECT ul.*, COALESCE(lot_totals.total_quantity, 0) as total_quantity
            FROM upload_logs ul
            LEFT JOIN (
                SELECT lot_number, SUM(quantity) as total_quantity
                FROM raw_bol_items
                GROUP BY lot_number
            ) lot_totals ON ul.lot_number = lot_totals.lot_number
            ORDER BY ul.uploaded_at DESC LIMIT 50
        ''')
        rows = [dict(r) for r in cur.fetchall()]
        conn.close()
        # Note: total_client_cost is already included via ul.* in the SELECT
        return {'success': True, 'logs': rows}
    except Exception as e:
        return {'success': False, 'error': str(e)}

def get_rawbol_stats():
    """Get statistics from rawbol.db - total unique items, total quantity, and total cost."""
    try:
        ensure_rawbol_db()
        conn = sqlite3.connect('rawbol.db')
        cur = conn.cursor()
        
        # Get total unique items (count distinct UPCs)
        cur.execute('SELECT COUNT(DISTINCT upc) FROM raw_bol_items')
        unique_items = cur.fetchone()[0] or 0
        
        # Get total quantity (sum all quantities)
        cur.execute('SELECT SUM(quantity) FROM raw_bol_items')
        total_quantity = cur.fetchone()[0] or 0
        
        # Get total cost from all BOLs (sum from upload_logs)
        cur.execute('SELECT SUM(total_client_cost) FROM upload_logs WHERE total_client_cost IS NOT NULL')
        total_cost = cur.fetchone()[0] or 0
        
        conn.close()
        return {'success': True, 'unique_items': unique_items, 'total_quantity': total_quantity, 'total_cost': total_cost}
    except Exception as e:
        return {'success': False, 'error': str(e)}

def sync_rawbol_to_bol(specific_lot=None):
    """
    Sync rawbol.db to bol.db with duplicate lot protection:
    - If specific_lot is provided, only sync that lot
    - Checks if lot numbers have already been synced
    - If UPC exists in bol.db, ADD the quantity from rawbol
    - If UPC is new, insert the item with quantity
    - Records synced lots to prevent duplicate syncing
    Returns: {'success': True, 'updated': n, 'inserted': m, 'skipped_lots': []} or error
    """
    try:
        ensure_rawbol_db()
        
        # First ensure bol.db has quantity column
        bol_conn = sqlite3.connect('bol.db')
        bol_cur = bol_conn.cursor()
        
        # Check if quantity column exists, add if not
        bol_cur.execute("PRAGMA table_info(bol_items)")
        cols = [r[1] for r in bol_cur.fetchall()]
        if 'quantity' not in cols:
            bol_cur.execute('ALTER TABLE bol_items ADD COLUMN quantity INTEGER DEFAULT 0')
            bol_conn.commit()
        
        # Get all raw items grouped by lot
        raw_conn = sqlite3.connect('rawbol.db')
        raw_conn.row_factory = sqlite3.Row
        raw_cur = raw_conn.cursor()
        
        # Get unique lot numbers from raw_bol_items
        if specific_lot:
            # Only sync the specific lot
            raw_cur.execute('SELECT DISTINCT lot_number FROM raw_bol_items WHERE lot_number = ?', (specific_lot,))
        else:
            # Sync all lots
            raw_cur.execute('SELECT DISTINCT lot_number FROM raw_bol_items WHERE lot_number IS NOT NULL AND lot_number != ""')
        lots = [r['lot_number'] for r in raw_cur.fetchall()]
        
        if not lots:
            bol_conn.close()
            raw_conn.close()
            return {'success': False, 'error': f'Lot {specific_lot} not found' if specific_lot else 'No lots found'}
        
        # Check which lots have already been synced
        raw_cur.execute('SELECT lot_number FROM synced_lots')
        synced_lots = set(r['lot_number'] for r in raw_cur.fetchall())
        
        # Filter out already synced lots
        lots_to_sync = [lot for lot in lots if lot not in synced_lots]
        skipped_lots = [lot for lot in lots if lot in synced_lots]
        
        if not lots_to_sync:
            bol_conn.close()
            raw_conn.close()
            return {'success': False, 'error': f'All lots already synced. Skipped: {", ".join(skipped_lots)}', 'skipped_lots': skipped_lots}
        
        # Get items only from lots that haven't been synced
        placeholders = ','.join('?' for _ in lots_to_sync)
        raw_cur.execute(f'SELECT * FROM raw_bol_items WHERE lot_number IN ({placeholders})', tuple(lots_to_sync))
        raw_items = raw_cur.fetchall()
        
        updated = 0
        inserted = 0
        import json
        changes = []
        
        for item in raw_items:
            upc = item['upc']
            qty = item['quantity'] or 1
            
            # Check if exists in bol.db
            bol_cur.execute('SELECT id, quantity FROM bol_items WHERE upc = ? COLLATE NOCASE', (upc,))
            existing = bol_cur.fetchone()
            
            if existing:
                # Update: add quantity
                existing_qty = existing[1] or 0
                new_qty = existing_qty + qty
                bol_cur.execute('UPDATE bol_items SET quantity = ? WHERE upc = ? COLLATE NOCASE', (new_qty, upc))
                updated += 1
                changes.append({'upc': upc, 'action': 'updated', 'old_qty': existing_qty, 'added_qty': qty, 'new_qty': new_qty})
            else:
                # Insert new item
                bol_cur.execute('''INSERT INTO bol_items 
                    (upc, item_description, client_cost, total_client_cost, image_url, quantity, lot_number, bol_number, import_date)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)''',
                    (upc,
                     item['item_description'],
                     item['client_cost'],
                     item['total_client_cost'],
                     item['image_url'],
                     qty,
                     item['lot_number'],
                     item['bol_number'],
                     item['import_date']))
                inserted += 1
                changes.append({'upc': upc, 'action': 'inserted', 'qty': qty})
        
        # Record synced lots
        sync_date = datetime.datetime.utcnow().isoformat()
        for lot in lots_to_sync:
            raw_cur.execute('SELECT COUNT(*) FROM raw_bol_items WHERE lot_number = ?', (lot,))
            count = raw_cur.fetchone()[0]
            raw_cur.execute('INSERT INTO synced_lots (lot_number, sync_date, items_count) VALUES (?, ?, ?)',
                          (lot, sync_date, count))
        
        # Record sync history
        raw_cur.execute('''INSERT INTO sync_history (sync_date, items_updated, items_inserted, changes_json)
                          VALUES (?, ?, ?, ?)''',
                       (sync_date, updated, inserted, json.dumps(changes)))
        
        bol_conn.commit()
        raw_conn.commit()
        bol_conn.close()
        raw_conn.close()
        
        return {'success': True, 'updated': updated, 'inserted': inserted, 'synced_lots': lots_to_sync, 'skipped_lots': skipped_lots}
    except Exception as e:
        return {'success': False, 'error': str(e)}

def delete_lot(lot_number):
    """
    Delete a specific lot from rawbol.db and desync from bol.db:
    - Removes lot data from raw_bol_items
    - Removes from synced_lots
    - Removes from upload_logs
    - Subtracts quantities from bol.db for items in this lot
    - Removes items from bol.db if quantity reaches 0
    Returns: {'success': True, 'updated': n, 'removed': m} or error
    """
    try:
        ensure_rawbol_db()
        
        bol_conn = sqlite3.connect('bol.db')
        bol_cur = bol_conn.cursor()
        
        raw_conn = sqlite3.connect('rawbol.db')
        raw_conn.row_factory = sqlite3.Row
        raw_cur = raw_conn.cursor()
        
        # Get all items from this lot before deleting
        raw_cur.execute('SELECT * FROM raw_bol_items WHERE lot_number = ?', (lot_number,))
        lot_items = raw_cur.fetchall()
        
        if not lot_items:
            raw_conn.close()
            bol_conn.close()
            return {'success': False, 'error': f'Lot {lot_number} not found'}
        
        updated = 0
        removed = 0
        
        # Desync from bol.db
        for item in lot_items:
            upc = item['upc']
            qty = item['quantity'] or 1
            
            # Check if exists in bol.db
            bol_cur.execute('SELECT id, quantity FROM bol_items WHERE upc = ? COLLATE NOCASE', (upc,))
            existing = bol_cur.fetchone()
            
            if existing:
                existing_qty = existing[1] or 0
                new_qty = existing_qty - qty
                
                if new_qty <= 0:
                    # Remove item if quantity goes to 0 or negative
                    bol_cur.execute('DELETE FROM bol_items WHERE upc = ? COLLATE NOCASE', (upc,))
                    removed += 1
                else:
                    # Subtract quantity
                    bol_cur.execute('UPDATE bol_items SET quantity = ? WHERE upc = ? COLLATE NOCASE', (new_qty, upc))
                    updated += 1
        
        # Delete from rawbol.db tables
        raw_cur.execute('DELETE FROM raw_bol_items WHERE lot_number = ?', (lot_number,))
        raw_cur.execute('DELETE FROM synced_lots WHERE lot_number = ?', (lot_number,))
        raw_cur.execute('DELETE FROM upload_logs WHERE lot_number = ?', (lot_number,))
        
        bol_conn.commit()
        raw_conn.commit()
        bol_conn.close()
        raw_conn.close()
        
        return {'success': True, 'updated': updated, 'removed': removed, 'lot': lot_number}
    except Exception as e:
        return {'success': False, 'error': str(e)}

def desync_all_rawbol():
    """
    Desync (undo) ALL rawbol items from bol.db:
    - Subtracts quantities that were added from rawbol
    - Removes items that were inserted (quantity becomes 0 or negative)
    - Clears synced_lots tracking
    Returns: {'success': True, 'updated': n, 'removed': m} or error
    """
    try:
        ensure_rawbol_db()
        
        bol_conn = sqlite3.connect('bol.db')
        bol_cur = bol_conn.cursor()
        
        raw_conn = sqlite3.connect('rawbol.db')
        raw_conn.row_factory = sqlite3.Row
        raw_cur = raw_conn.cursor()
        
        # Get all raw items
        raw_cur.execute('SELECT * FROM raw_bol_items')
        raw_items = raw_cur.fetchall()
        
        updated = 0
        removed = 0
        
        for item in raw_items:
            upc = item['upc']
            qty = item['quantity'] or 1
            
            # Check if exists in bol.db
            bol_cur.execute('SELECT id, quantity FROM bol_items WHERE upc = ? COLLATE NOCASE', (upc,))
            existing = bol_cur.fetchone()
            
            if existing:
                existing_qty = existing[1] or 0
                new_qty = existing_qty - qty
                
                if new_qty <= 0:
                    # Remove item if quantity goes to 0 or negative
                    bol_cur.execute('DELETE FROM bol_items WHERE upc = ? COLLATE NOCASE', (upc,))
                    removed += 1
                else:
                    # Subtract quantity
                    bol_cur.execute('UPDATE bol_items SET quantity = ? WHERE upc = ? COLLATE NOCASE', (new_qty, upc))
                    updated += 1
        
        # Clear synced lots tracking
        raw_cur.execute('DELETE FROM synced_lots')
        
        bol_conn.commit()
        raw_conn.commit()
        bol_conn.close()
        raw_conn.close()
        
        return {'success': True, 'updated': updated, 'removed': removed}
    except Exception as e:
        return {'success': False, 'error': str(e)}

def check_lot_exists(lot_number):
    """
    Check if a lot has already been uploaded/synced
    Returns: {'exists': True/False, 'in_rawbol': True/False, 'synced': True/False}
    """
    try:
        ensure_rawbol_db()
        conn = sqlite3.connect('rawbol.db')
        cur = conn.cursor()
        
        # Check if lot exists in raw_bol_items
        cur.execute('SELECT COUNT(*) FROM raw_bol_items WHERE lot_number = ?', (lot_number,))
        in_rawbol = cur.fetchone()[0] > 0
        
        # Check if lot has been synced
        cur.execute('SELECT COUNT(*) FROM synced_lots WHERE lot_number = ?', (lot_number,))
        synced = cur.fetchone()[0] > 0
        
        conn.close()
        
        return {
            'exists': in_rawbol or synced,
            'in_rawbol': in_rawbol,
            'synced': synced
        }
    except Exception as e:
        return {'exists': False, 'error': str(e)}
