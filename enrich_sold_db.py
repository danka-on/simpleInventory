import sqlite3
from DBmanager import connect_db

def _clean_barcode(value):
    if value is None:
        return ''
    if isinstance(value, float):
        try:
            return str(int(value))
        except Exception:
            return str(value)
    value = str(value).strip()
    if value.endswith('.0') and value.replace('.0', '').isdigit():
        value = value[:-2]
    return value

def _lookup_rawbol(bol_cur, barcode, columns):
    barcode_clean = _clean_barcode(barcode)
    if not barcode_clean:
        return None
    barcode_stripped = barcode_clean.lstrip('0') if barcode_clean.isdigit() else barcode_clean

    row_bol = None

    # Try exact match first
    bol_cur.execute(f'SELECT {columns} FROM raw_bol_items WHERE upc = ? COLLATE NOCASE LIMIT 1', (barcode_clean,))
    row_bol = bol_cur.fetchone()

    # Try stripped zeros if no match
    if not row_bol and barcode_clean.isdigit():
        stripped = barcode_stripped
        if stripped != barcode_clean:
            bol_cur.execute(f'SELECT {columns} FROM raw_bol_items WHERE upc = ? COLLATE NOCASE LIMIT 1', (stripped,))
            row_bol = bol_cur.fetchone()

    # Try padded to 12 digits if no match
    if not row_bol and barcode_clean.isdigit():
        padded = barcode_clean.zfill(12)
        if padded != barcode_clean:
            bol_cur.execute(f'SELECT {columns} FROM raw_bol_items WHERE upc = ? COLLATE NOCASE LIMIT 1', (padded,))
            row_bol = bol_cur.fetchone()

    # Try padded to 13 digits (EAN) if no match
    if not row_bol and barcode_clean.isdigit():
        padded13 = barcode_clean.zfill(13)
        if padded13 != barcode_clean:
            bol_cur.execute(f'SELECT {columns} FROM raw_bol_items WHERE upc = ? COLLATE NOCASE LIMIT 1', (padded13,))
            row_bol = bol_cur.fetchone()

    # Try suffix match (rawbol UPC has extra leading zeros)
    if not row_bol and barcode_stripped and barcode_stripped.isdigit() and len(barcode_stripped) >= 8:
        bol_cur.execute(f"SELECT {columns} FROM raw_bol_items WHERE REPLACE(upc, '.0', '') LIKE ? COLLATE NOCASE LIMIT 1", ('%' + barcode_stripped,))
        row_bol = bol_cur.fetchone()

    # Try integer-cast comparison to normalize leading zeros
    if not row_bol and barcode_stripped and barcode_stripped.isdigit() and len(barcode_stripped) >= 8:
        bol_cur.execute(f"""
            SELECT {columns} FROM raw_bol_items
            WHERE CAST(CAST(REPLACE(upc, '.0', '') AS INTEGER) AS TEXT) = ?
            COLLATE NOCASE LIMIT 1
        """, (barcode_stripped,))
        row_bol = bol_cur.fetchone()

    return row_bol

def enrich_sold_db():
    with connect_db('sold.db') as sold_conn:
        sold_cur = sold_conn.cursor()
        # Ensure barcode column exists
        sold_cur.execute('PRAGMA table_info(orders)')
        cols = [r[1] for r in sold_cur.fetchall()]
        if 'barcode' not in cols:
            sold_cur.execute('ALTER TABLE orders ADD COLUMN barcode TEXT')
            sold_conn.commit()
        # Get all orders missing barcode or image
        sold_cur.execute('SELECT id, item_id, barcode FROM orders WHERE barcode IS NULL OR barcode = "" OR image IS NULL OR image = ""')
        rows = sold_cur.fetchall()
        print(f"Found {len(rows)} sold orders to enrich...")
        enriched_count = 0
        for row in rows:
            order_id = row[0]
            item_id = row[1]
            existing_barcode = row[2] if len(row) > 2 else None
            barcode_val = existing_barcode
            image_val = None
            title_val = None
            # Lookup barcode from ebayStore.db if missing
            if not barcode_val and item_id:
                try:
                    with connect_db('ebayStore.db') as ebay_conn:
                        ebay_cur = ebay_conn.cursor()
                        ebay_cur.execute('SELECT UPC FROM INVENTORY WHERE ItemID = ?', (item_id,))
                        row_ebay = ebay_cur.fetchone()
                        if row_ebay and row_ebay[0]:
                            barcode_val = row_ebay[0]
                except Exception as e:
                    print(f"ebayStore lookup failed for item_id {item_id}: {e}")
            # Lookup title/image from rawbol.db using barcode with leading zero handling
            if barcode_val:
                try:
                    with connect_db('rawbol.db') as bol_conn:
                        bol_conn.row_factory = sqlite3.Row
                        bol_cur = bol_conn.cursor()

                        row_bol = _lookup_rawbol(bol_cur, barcode_val, 'item_description, image_url')

                        if row_bol:
                            title_val = row_bol['item_description']
                            image_val = row_bol['image_url']
                except Exception as e:
                    print(f"rawbol lookup failed for barcode {barcode_val}: {e}")
            # Update the sold order
            if barcode_val or image_val or title_val:
                sold_cur.execute('UPDATE orders SET barcode = COALESCE(?, barcode), image = COALESCE(?, image), title = COALESCE(?, title) WHERE id = ?', (barcode_val, image_val, title_val, order_id))
                enriched_count += 1
    print(f"Enrichment complete. Enriched {enriched_count} orders.")

def enrich_sold_db_image():
    with connect_db('sold.db') as sold_conn:
        sold_cur = sold_conn.cursor()
        # Ensure barcode column exists
        sold_cur.execute('PRAGMA table_info(orders)')
        cols = [r[1] for r in sold_cur.fetchall()]
        if 'barcode' not in cols:
            sold_cur.execute('ALTER TABLE orders ADD COLUMN barcode TEXT')
            sold_conn.commit()
        # Get all orders with a barcode but missing image
        sold_cur.execute('SELECT id, barcode FROM orders WHERE (image IS NULL OR image = "") AND (barcode IS NOT NULL AND barcode != "")')
        rows = sold_cur.fetchall()
        print(f"Found {len(rows)} sold orders to enrich image...")
        enriched_count = 0
        for order_id, barcode in rows:
            image_val = None
            if barcode:
                try:
                    with connect_db('rawbol.db') as bol_conn:
                        bol_conn.row_factory = sqlite3.Row
                        bol_cur = bol_conn.cursor()

                        row_bol = _lookup_rawbol(bol_cur, barcode, 'image_url')

                        if row_bol and row_bol['image_url']:
                            image_val = row_bol['image_url']
                except Exception as e:
                    print(f"rawbol lookup failed for barcode {barcode}: {e}")
            if image_val:
                sold_cur.execute('UPDATE orders SET image = ? WHERE id = ?', (image_val, order_id))
                enriched_count += 1
    print(f"Image enrichment complete. Enriched {enriched_count} orders.")

if __name__ == "__main__":
    enrich_sold_db()
    enrich_sold_db_image()
