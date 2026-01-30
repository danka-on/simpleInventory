import sqlite3
from DBmanager import connect_db

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
        sold_cur.execute('SELECT id, item_id FROM orders WHERE barcode IS NULL OR barcode = "" OR image IS NULL OR image = ""')
        rows = sold_cur.fetchall()
        print(f"Found {len(rows)} sold orders to enrich...")
        for order_id, item_id in rows:
            barcode_val = None
            image_val = None
            title_val = None
            # Lookup barcode from ebayStore.db
            if item_id:
                try:
                    with connect_db('ebayStore.db') as ebay_conn:
                        ebay_cur = ebay_conn.cursor()
                        ebay_cur.execute('SELECT UPC FROM INVENTORY WHERE ItemID = ?', (item_id,))
                        row = ebay_cur.fetchone()
                        if row and row[0]:
                            barcode_val = row[0]
                except Exception as e:
                    print(f"ebayStore lookup failed for item_id {item_id}: {e}")
            # Lookup title/image from rawbol.db
            if barcode_val:
                try:
                    with connect_db('rawbol.db') as bol_conn:
                        bol_conn.row_factory = sqlite3.Row
                        bol_cur = bol_conn.cursor()
                        bol_cur.execute('SELECT item_description, image_url FROM raw_bol_items WHERE upc = ? COLLATE NOCASE LIMIT 1', (barcode_val,))
                        row_bol = bol_cur.fetchone()
                        if row_bol:
                            title_val = row_bol['item_description']
                            image_val = row_bol['image_url']
                except Exception as e:
                    print(f"rawbol lookup failed for barcode {barcode_val}: {e}")
            # Update the sold order
            sold_cur.execute('UPDATE orders SET barcode = COALESCE(?, barcode), image = COALESCE(?, image), title = COALESCE(?, title) WHERE id = ?', (barcode_val, image_val, title_val, order_id))
    print("Enrichment complete.")

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
        for order_id, barcode in rows:
            image_val = None
            if barcode:
                try:
                    with connect_db('rawbol.db') as bol_conn:
                        bol_conn.row_factory = sqlite3.Row
                        bol_cur = bol_conn.cursor()
                        bol_cur.execute('SELECT image_url FROM raw_bol_items WHERE upc = ? COLLATE NOCASE LIMIT 1', (barcode,))
                        row_bol = bol_cur.fetchone()
                        if row_bol and row_bol['image_url']:
                            image_val = row_bol['image_url']
                except Exception as e:
                    print(f"rawbol lookup failed for barcode {barcode}: {e}")
            if image_val:
                sold_cur.execute('UPDATE orders SET image = ? WHERE id = ?', (image_val, order_id))
    print("Image enrichment complete.")

if __name__ == "__main__":
    enrich_sold_db()
    enrich_sold_db_image()
