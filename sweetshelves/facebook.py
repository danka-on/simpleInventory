"""Facebook for Sweet Shelves."""

import datetime
import sqlite3
from flask import jsonify, request
from . import (
    caching as ss_caching, errors as ss_errors, listing_lifecycle as ss_listing_lifecycle, normalization
    as ss_normalization, runtime as ss_runtime,
)


def _ensure_fbstore_tables():
    """Create fbstore.db tables for Facebook Marketplace tracking."""
    try:
        conn = sqlite3.connect('fbstore.db')
        cur = conn.cursor()
        cur.execute('''
            CREATE TABLE IF NOT EXISTS fb_listings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                upc TEXT NOT NULL,
                title TEXT,
                image TEXT,
                quantity INTEGER DEFAULT 1,
                listed_at TEXT DEFAULT CURRENT_TIMESTAMP,
                unlisted_at TEXT,
                is_active INTEGER DEFAULT 1,
                notes TEXT
            )
        ''')
        cur.execute('CREATE INDEX IF NOT EXISTS idx_fb_upc ON fb_listings(upc)')
        cur.execute('CREATE INDEX IF NOT EXISTS idx_fb_active ON fb_listings(is_active)')
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"Error initializing fbstore.db: {e}")


def _ensure_fbstore_log_tables():
    """Create fbstore.db tables for FB listing sync logs."""
    try:
        conn = sqlite3.connect('fbstore.db')
        cur = conn.cursor()
        cur.execute('''
            CREATE TABLE IF NOT EXISTS fb_listing_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                action TEXT NOT NULL,
                upc TEXT,
                delta_qty INTEGER,
                prev_qty INTEGER,
                new_qty INTEGER,
                prev_listed INTEGER,
                prev_listed_date TEXT,
                order_id TEXT,
                item_id TEXT,
                store TEXT,
                title TEXT,
                note TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                undone INTEGER DEFAULT 0,
                undone_at TEXT
            )
        ''')
        cur.execute('CREATE INDEX IF NOT EXISTS idx_fb_log_created ON fb_listing_log(created_at)')
        cur.execute('''
            CREATE TABLE IF NOT EXISTS fb_listing_sold_sync (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                order_id TEXT,
                item_id TEXT,
                upc TEXT,
                qty INTEGER,
                paid_time TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(order_id, item_id, upc, qty, paid_time)
            )
        ''')
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"Error initializing fbstore log tables: {e}")


def _ensure_fbstore_notes_tables():
    """Create fbstore.db tables for FB listing notes."""
    try:
        conn = sqlite3.connect('fbstore.db')
        cur = conn.cursor()
        cur.execute('''
            CREATE TABLE IF NOT EXISTS fb_listing_notes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                upc TEXT NOT NULL,
                note TEXT NOT NULL,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT
            )
        ''')
        cur.execute('CREATE INDEX IF NOT EXISTS idx_fb_notes_upc ON fb_listing_notes(upc)')
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"Error initializing fbstore notes tables: {e}")


def _log_fb_listing_action(action, upc=None, delta_qty=None, prev_qty=None, new_qty=None,
                           prev_listed=None, prev_listed_date=None, order_id=None,
                           item_id=None, store=None, title=None, note=None):
    """Write a FB listing log entry."""
    try:
        _ensure_fbstore_log_tables()
        conn = sqlite3.connect('fbstore.db')
        cur = conn.cursor()
        cur.execute('''
            INSERT INTO fb_listing_log (
                action, upc, delta_qty, prev_qty, new_qty,
                prev_listed, prev_listed_date, order_id, item_id,
                store, title, note
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            action, upc, delta_qty, prev_qty, new_qty,
            prev_listed, prev_listed_date, order_id, item_id,
            store, title, note
        ))
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"Error logging FB listing action: {e}")


def _track_fb_listing(upc, quantity=1, listed=True, title=None, image=None):
    """Track Facebook Marketplace listing in fbstore.db"""
    try:
        _ensure_fbstore_tables()
        conn = sqlite3.connect('fbstore.db')
        cur = conn.cursor()

        if listed:
            # Get title/image from bol.db if not provided
            if not title or not image:
                bol_conn = sqlite3.connect('bol.db')
                bol_conn.row_factory = sqlite3.Row
                bol_cur = bol_conn.cursor()
                bol_cur.execute('SELECT title, image FROM bol_items WHERE upc = ? COLLATE NOCASE LIMIT 1', (upc,))
                row = bol_cur.fetchone()
                if row:
                    title = title or row['title']
                    image = image or row['image']
                bol_conn.close()

            # Insert new listing record
            cur.execute('''
                INSERT INTO fb_listings (upc, title, image, quantity, listed_at, is_active)
                VALUES (?, ?, ?, ?, datetime("now"), 1)
            ''', (upc, title, image, quantity))
            print(f"[fbstore] Added FB listing: UPC={upc}, Qty={quantity}")
        else:
            # Mark most recent active listing as unlisted
            cur.execute('''
                UPDATE fb_listings
                SET is_active = 0, unlisted_at = datetime("now")
                WHERE upc = ? COLLATE NOCASE AND is_active = 1
            ''', (upc,))
            print(f"[fbstore] Marked FB listing unlisted: UPC={upc}")

        conn.commit()
        conn.close()
    except Exception as e:
        print(f"Error tracking FB listing: {e}")


def api_fb_listings():
    """Get current Facebook Marketplace listings (from bol.db list status)."""
    try:
        ss_listing_lifecycle._ensure_bol_list_status_column()
        conn = sqlite3.connect('bol.db')
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()

        # Simple: show items currently marked as listed on Facebook
        cur.execute('''
            SELECT upc,
                   item_description AS title,
                   image_url AS image,
                   COALESCE(listed_facebook_qty, quantity, 1) AS quantity,
                   listed_facebook_date AS listed_at
            FROM bol_items
            WHERE COALESCE(listed_facebook, 0) = 1 OR COALESCE(listed_facebook_qty, 0) > 0
            ORDER BY listed_facebook_date DESC
        ''')
        listings = []
        for row in cur.fetchall():
            item = dict(row)
            item['is_active'] = 1
            listings.append(item)
        conn.close()

        # Attach warehouse availability (searchRack)
        try:
            warehouse_stock = {}
            sr_conn = sqlite3.connect('searchRack.db')
            sr_conn.row_factory = sqlite3.Row
            sr_cur = sr_conn.cursor()
            sr_cur.execute('PRAGMA table_info(SEARCHRACK)')
            sr_cols = [c[1].lower() for c in sr_cur.fetchall()]
            upc_col = 'UPC' if 'upc' in sr_cols else ('BARCODE' if 'barcode' in sr_cols else None)
            qty_col = 'QUANTITY' if 'quantity' in sr_cols else ('QTY' if 'qty' in sr_cols else None)
            if upc_col and qty_col:
                sr_cur.execute(f'''
                    SELECT {upc_col} AS UPC, SUM({qty_col}) as total_qty
                    FROM SEARCHRACK
                    WHERE {upc_col} IS NOT NULL AND {upc_col} != ""
                      AND COALESCE(CAST({qty_col} AS INTEGER), 0) > 0
                    GROUP BY {upc_col} COLLATE NOCASE
                ''')
                for row in sr_cur.fetchall():
                    upc = (row['UPC'] or '').strip()
                    if upc:
                        warehouse_stock[upc.lower()] = int(row['total_qty'] or 0)
            sr_conn.close()
            for item in listings:
                upc_key = (str(item.get('upc') or '').strip()).lower()
                item['warehouse_qty'] = warehouse_stock.get(upc_key, 0)
        except Exception as e:
            print(f"Error reading searchRack for fb listings: {e}")

        # Attach notes flag from items_prep_notes (bol.db)
        try:
            if listings:
                _ensure_fbstore_notes_tables()
                note_upcs = [str(i.get('upc') or '').strip() for i in listings if i.get('upc')]
                note_upcs = [u for u in note_upcs if u]
                if note_upcs:
                    with sqlite3.connect('fbstore.db') as nconn:
                        nconn.row_factory = sqlite3.Row
                        ncur = nconn.cursor()
                        placeholders = ','.join('?' for _ in note_upcs)
                        ncur.execute(f'''
                            SELECT upc, COUNT(*) as note_count
                            FROM fb_listing_notes
                            WHERE upc IN ({placeholders})
                            GROUP BY upc
                        ''', tuple(note_upcs))
                        note_map = {str(r['upc']).lower(): int(r['note_count'] or 0) for r in ncur.fetchall()}
                    for item in listings:
                        upc_key = str(item.get('upc') or '').strip().lower()
                        item['has_notes'] = note_map.get(upc_key, 0) > 0
        except Exception as e:
            print(f"Error reading notes for fb listings: {e}")

        # If bol.db didn't return anything, fall back to fbstore.db active listings
        if not listings:
            try:
                fb_conn = sqlite3.connect('fbstore.db')
                fb_conn.row_factory = sqlite3.Row
                fb_cur = fb_conn.cursor()
                fb_cur.execute('''
                    SELECT upc, title, image, quantity, listed_at
                    FROM fb_listings
                    WHERE is_active = 1
                    ORDER BY listed_at DESC
                ''')
                for row in fb_cur.fetchall():
                    item = dict(row)
                    item['is_active'] = 1
                    item['warehouse_qty'] = item.get('warehouse_qty', 0)
                    item['has_notes'] = False
                    listings.append(item)
                fb_conn.close()
            except Exception as e:
                print(f"Error reading fbstore listings: {e}")

        return jsonify({'success': True, 'listings': listings})
    except Exception as e:
        return jsonify({'success': False, 'error': ss_errors._safe_error(e, 'fb-listings')}), 500


def api_fb_listings_add():
    """Add a Facebook Marketplace listing by UPC (lookup from rawbol.db)."""
    try:
        data = request.get_json() or {}
        upc = ss_normalization._normalize_upc(data.get('upc'))
        if not upc:
            return jsonify({'success': False, 'error': 'Missing upc'}), 400

        ss_listing_lifecycle._ensure_bol_list_status_column()

        # Lookup item details from rawbol.db
        raw_conn = sqlite3.connect('rawbol.db')
        raw_conn.row_factory = sqlite3.Row
        raw_cur = raw_conn.cursor()
        raw_cur.execute('''
            SELECT upc, item_description, image_url, quantity, lot_number, import_date, created_at
            FROM raw_bol_items
            WHERE upc = ? COLLATE NOCASE
            ORDER BY created_at DESC
            LIMIT 1
        ''', (upc,))
        raw_row = raw_cur.fetchone()
        raw_conn.close()

        if not raw_row:
            return jsonify({'success': False, 'error': 'UPC not found in rawbol.db'}), 404

        # Ensure item exists in bol.db
        conn = sqlite3.connect('bol.db')
        cur = conn.cursor()
        cur.execute('SELECT upc, COALESCE(listed_facebook,0), COALESCE(listed_facebook_qty,1), listed_facebook_date FROM bol_items WHERE upc = ? COLLATE NOCASE', (upc,))
        existing_row = cur.fetchone()
        exists = bool(existing_row)

        if not exists:
            cur.execute('PRAGMA table_info(bol_items)')
            cols = [c[1] for c in cur.fetchall()]
            colset = {c.lower() for c in cols}

            def _has(name):
                return name.lower() in colset

            qty_val = int(raw_row['quantity'] or 1)
            insert_cols = ['upc']
            values = [upc]

            if _has('item_description'):
                insert_cols.append('item_description')
                values.append(raw_row['item_description'])
            if _has('image_url'):
                insert_cols.append('image_url')
                values.append(raw_row['image_url'])
            if _has('lot_number'):
                insert_cols.append('lot_number')
                values.append(raw_row['lot_number'])
            if _has('bol_number'):
                insert_cols.append('bol_number')
                values.append('RAWBOL')
            if _has('import_date'):
                insert_cols.append('import_date')
                values.append(raw_row['import_date'] or datetime.datetime.now().isoformat())
            if _has('quantity'):
                insert_cols.append('quantity')
                values.append(qty_val)
            if _has('original_qty'):
                insert_cols.append('original_qty')
                values.append(qty_val)
            if _has('unchecked_qty'):
                insert_cols.append('unchecked_qty')
                values.append(qty_val)
            if _has('good_qty'):
                insert_cols.append('good_qty')
                values.append(0)
            if _has('bad_qty'):
                insert_cols.append('bad_qty')
                values.append(0)
            if _has('temporary'):
                insert_cols.append('temporary')
                values.append(0)

            placeholders = ','.join('?' for _ in insert_cols)
            cur.execute(
                f'INSERT INTO bol_items ({",".join(insert_cols)}) VALUES ({placeholders})',
                tuple(values)
            )

        # If already listed, keep existing qty and skip updating date
        prev_listed = int(existing_row[1] or 0) if existing_row else 0
        prev_qty = int(existing_row[2] or 0) if existing_row else 0
        prev_listed_date = existing_row[3] if existing_row else None

        if exists and prev_listed == 1:
            conn.commit()
            conn.close()
            return jsonify({'success': True, 'upc': upc, 'already_listed': True})

        # Mark as listed on Facebook with default qty 1 (editable later)
        cur.execute('''
            UPDATE bol_items
            SET listed_facebook = 1,
                listed_facebook_date = datetime("now"),
                listed_facebook_qty = ?,
                listed_facebook_source = 'user'
            WHERE upc = ? COLLATE NOCASE
        ''', (1, upc))

        # Update legacy list_status column
        cur.execute('''
            UPDATE bol_items
            SET list_status = CASE
                WHEN COALESCE(listed_amazon, 0) = 1
                  OR COALESCE(listed_ebay, 0) = 1
                  OR COALESCE(listed_facebook, 0) = 1
                THEN 'listed'
                ELSE NULL
            END
            WHERE upc = ? COLLATE NOCASE
        ''', (upc,))

        conn.commit()
        conn.close()
        ss_runtime.cache.clear()
        ss_caching.update_data_version()
        _log_fb_listing_action(
            action='manual_add',
            upc=upc,
            delta_qty=(1 - prev_qty),
            prev_qty=prev_qty,
            new_qty=1,
            prev_listed=prev_listed,
            prev_listed_date=prev_listed_date,
            title=raw_row['item_description'] if raw_row else None
        )
        return jsonify({'success': True, 'upc': upc})
    except Exception as e:
        return jsonify({'success': False, 'error': ss_errors._safe_error(e, 'fb-listings-add')}), 500


def api_fb_listings_search_rawbol():
    """Search rawbol.db by item_description for FB listing add flow."""
    try:
        q = (request.args.get('q') or '').strip()
        if not q:
            return jsonify({'success': False, 'error': 'Missing q'}), 400

        conn = sqlite3.connect('rawbol.db')
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute('''
            SELECT upc, item_description, image_url
            FROM raw_bol_items
            WHERE item_description LIKE ? COLLATE NOCASE
            ORDER BY created_at DESC
            LIMIT 20
        ''', (f'%{q}%',))
        results = [dict(r) for r in cur.fetchall()]
        conn.close()
        return jsonify({'success': True, 'results': results})
    except Exception as e:
        return jsonify({'success': False, 'error': ss_errors._safe_error(e, 'fb-listings-search')}), 500


def api_fb_listings_sync_sold():
    """Sync sold orders tagged as Facebook Marketplace and decrement FB listing qty."""
    try:
        ss_listing_lifecycle._ensure_bol_list_status_column()
        _ensure_fbstore_log_tables()

        sold_conn = sqlite3.connect('sold.db')
        sold_conn.row_factory = sqlite3.Row
        sold_cur = sold_conn.cursor()
        sold_cur.execute('''
            SELECT order_id, item_id, title, quantity, paid_time, barcode, store
            FROM orders
            WHERE store IN ('marketplace', 'facebook', 'fb')
        ''')
        sold_rows = sold_cur.fetchall()
        sold_conn.close()

        if not sold_rows:
            return jsonify({'success': True, 'updated': 0, 'skipped': 0, 'processed': 0})

        fb_conn = sqlite3.connect('fbstore.db')
        fb_cur = fb_conn.cursor()

        bol_conn = sqlite3.connect('bol.db')
        bol_conn.row_factory = sqlite3.Row
        bol_cur = bol_conn.cursor()

        processed = 0
        updated = 0
        skipped = 0

        for row in sold_rows:
            upc = ss_normalization._normalize_upc(row['barcode'] or '')
            if not upc:
                skipped += 1
                continue

            qty = int(row['quantity'] or 1)
            order_id = row['order_id']
            item_id = row['item_id']
            paid_time = row['paid_time']

            try:
                fb_cur.execute('''
                    INSERT INTO fb_listing_sold_sync (order_id, item_id, upc, qty, paid_time)
                    VALUES (?, ?, ?, ?, ?)
                ''', (order_id, item_id, upc, qty, paid_time))
                fb_conn.commit()
            except sqlite3.IntegrityError:
                skipped += 1
                continue

            processed += 1

            bol_cur.execute('''
                SELECT COALESCE(listed_facebook,0) AS listed_facebook,
                       COALESCE(listed_facebook_qty,1) AS listed_facebook_qty,
                       listed_facebook_date,
                       item_description
                FROM bol_items
                WHERE upc = ? COLLATE NOCASE
            ''', (upc,))
            b = bol_cur.fetchone()

            if not b or int(b['listed_facebook'] or 0) != 1:
                _log_fb_listing_action(
                    action='auto_sold_missing',
                    upc=upc,
                    delta_qty=-qty,
                    prev_qty=None,
                    new_qty=None,
                    prev_listed=int(b['listed_facebook']) if b else 0,
                    prev_listed_date=b['listed_facebook_date'] if b else None,
                    order_id=order_id,
                    item_id=item_id,
                    store=row['store'],
                    title=row['title'],
                    note='Not listed in FB'
                )
                skipped += 1
                continue

            prev_qty = int(b['listed_facebook_qty'] or 1)
            new_qty = max(0, prev_qty - qty)
            note = None

            if new_qty <= 0:
                bol_cur.execute('''
                    UPDATE bol_items
                    SET listed_facebook=0, listed_facebook_date=NULL, listed_facebook_qty=0, listed_facebook_source=NULL
                    WHERE upc = ? COLLATE NOCASE
                ''', (upc,))
                note = 'Auto-unlisted'
            else:
                bol_cur.execute('''
                    UPDATE bol_items
                    SET listed_facebook_qty=?,
                        listed_facebook_source=COALESCE(NULLIF(TRIM(listed_facebook_source), ''), 'system')
                    WHERE upc = ? COLLATE NOCASE
                ''', (new_qty, upc))

            bol_cur.execute('''
                UPDATE bol_items
                SET list_status = CASE
                    WHEN COALESCE(listed_amazon, 0) = 1
                      OR COALESCE(listed_ebay, 0) = 1
                      OR COALESCE(listed_facebook, 0) = 1
                    THEN 'listed'
                    ELSE NULL
                END
                WHERE upc = ? COLLATE NOCASE
            ''', (upc,))

            updated += 1

            _log_fb_listing_action(
                action='auto_sold',
                upc=upc,
                delta_qty=(0 - qty),
                prev_qty=prev_qty,
                new_qty=new_qty,
                prev_listed=1,
                prev_listed_date=b['listed_facebook_date'],
                order_id=order_id,
                item_id=item_id,
                store=row['store'],
                title=b['item_description'] or row['title'],
                note=note
            )

        bol_conn.commit()
        bol_conn.close()
        fb_conn.close()

        ss_runtime.cache.clear()
        ss_caching.update_data_version()

        return jsonify({'success': True, 'updated': updated, 'skipped': skipped, 'processed': processed})
    except Exception as e:
        return jsonify({'success': False, 'error': ss_errors._safe_error(e, 'fb-listings-sync')}), 500


def api_fb_listings_log():
    """Get FB listing auto/manual change logs."""
    try:
        _ensure_fbstore_log_tables()
        limit = int(request.args.get('limit', 50))
        limit = max(1, min(200, limit))
        conn = sqlite3.connect('fbstore.db')
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        # Only show log entries relevant to FB listings.
        allowed_actions = ('manual_add', 'manual_unlist', 'manual_qty_change', 'auto_sold')
        placeholders = ','.join('?' for _ in allowed_actions)
        cur.execute(
            f'''
                SELECT *
                FROM fb_listing_log
                WHERE action IN ({placeholders})
                ORDER BY created_at DESC
                LIMIT ?
            ''',
            (*allowed_actions, limit)
        )
        rows = [dict(r) for r in cur.fetchall()]
        conn.close()
        return jsonify({'success': True, 'logs': rows})
    except Exception as e:
        return jsonify({'success': False, 'error': ss_errors._safe_error(e, 'fb-listings-log')}), 500


def api_fb_listings_log_undo():
    """Undo a FB listing log entry by restoring previous state."""
    try:
        data = request.get_json() or {}
        log_id = data.get('id')
        if not log_id:
            return jsonify({'success': False, 'error': 'Missing id'}), 400

        _ensure_fbstore_log_tables()
        conn = sqlite3.connect('fbstore.db')
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute('SELECT * FROM fb_listing_log WHERE id = ?', (log_id,))
        log = cur.fetchone()
        if not log:
            conn.close()
            return jsonify({'success': False, 'error': 'Log entry not found'}), 404
        if int(log['undone'] or 0) == 1:
            conn.close()
            return jsonify({'success': True, 'already_undone': True})

        upc = log['upc']
        prev_listed = log['prev_listed']
        prev_qty = log['prev_qty']
        prev_date = log['prev_listed_date']

        if prev_listed is None:
            conn.close()
            return jsonify({'success': False, 'error': 'Cannot undo this entry'}), 400

        ss_listing_lifecycle._ensure_bol_list_status_column()
        bol_conn = sqlite3.connect('bol.db')
        bol_cur = bol_conn.cursor()

        if int(prev_listed or 0) == 1:
            qty_val = int(prev_qty or 1)
            bol_cur.execute('''
                UPDATE bol_items
                SET listed_facebook = 1,
                    listed_facebook_date = ?,
                    listed_facebook_qty = ?,
                    listed_facebook_source = 'user'
                WHERE upc = ? COLLATE NOCASE
            ''', (prev_date or datetime.datetime.now().isoformat(), qty_val, upc))
        else:
            bol_cur.execute('''
                UPDATE bol_items
                SET listed_facebook = 0,
                    listed_facebook_date = NULL,
                    listed_facebook_qty = NULL,
                    listed_facebook_source = NULL
                WHERE upc = ? COLLATE NOCASE
            ''', (upc,))

        bol_cur.execute('''
            UPDATE bol_items
            SET list_status = CASE
                WHEN COALESCE(listed_amazon, 0) = 1
                  OR COALESCE(listed_ebay, 0) = 1
                  OR COALESCE(listed_facebook, 0) = 1
                THEN 'listed'
                ELSE NULL
            END
            WHERE upc = ? COLLATE NOCASE
        ''', (upc,))

        bol_conn.commit()
        bol_conn.close()

        cur.execute('UPDATE fb_listing_log SET undone=1, undone_at=datetime("now") WHERE id = ?', (log_id,))
        conn.commit()
        conn.close()

        ss_runtime.cache.clear()
        ss_caching.update_data_version()

        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': ss_errors._safe_error(e, 'fb-listings-undo')}), 500


def api_fb_listings_notes_get(upc):
    """Get notes for a FB listing UPC."""
    try:
        upc_n = ss_normalization._normalize_upc(upc)
        if not upc_n:
            return jsonify({'success': False, 'error': 'Missing upc'}), 400
        _ensure_fbstore_notes_tables()
        conn = sqlite3.connect('fbstore.db')
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute('''
            SELECT id, note, created_at, updated_at
            FROM fb_listing_notes
            WHERE upc = ? COLLATE NOCASE
            ORDER BY created_at DESC
        ''', (upc_n,))
        notes = [dict(r) for r in cur.fetchall()]
        conn.close()
        return jsonify({'success': True, 'notes': notes})
    except Exception as e:
        return jsonify({'success': False, 'error': ss_errors._safe_error(e, 'fb-listings-notes-get')}), 500


def api_fb_listings_notes_add():
    """Add a note for a FB listing UPC."""
    try:
        data = request.get_json() or {}
        upc = ss_normalization._normalize_upc(data.get('upc'))
        note = (data.get('note') or '').strip()
        if not upc or not note:
            return jsonify({'success': False, 'error': 'Missing upc or note'}), 400
        _ensure_fbstore_notes_tables()
        conn = sqlite3.connect('fbstore.db')
        cur = conn.cursor()
        cur.execute('INSERT INTO fb_listing_notes (upc, note, created_at) VALUES (?,?,datetime("now"))', (upc, note))
        conn.commit()
        conn.close()
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': ss_errors._safe_error(e, 'fb-listings-notes-add')}), 500


def api_fb_listings_notes_update(note_id):
    """Update a FB listing note."""
    try:
        data = request.get_json() or {}
        note = (data.get('note') or '').strip()
        if not note:
            return jsonify({'success': False, 'error': 'Missing note'}), 400
        _ensure_fbstore_notes_tables()
        conn = sqlite3.connect('fbstore.db')
        cur = conn.cursor()
        cur.execute('UPDATE fb_listing_notes SET note = ?, updated_at = datetime("now") WHERE id = ?', (note, note_id))
        conn.commit()
        conn.close()
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': ss_errors._safe_error(e, 'fb-listings-notes-update')}), 500


def api_fb_listings_notes_delete(note_id):
    """Delete a FB listing note."""
    try:
        _ensure_fbstore_notes_tables()
        conn = sqlite3.connect('fbstore.db')
        cur = conn.cursor()
        cur.execute('DELETE FROM fb_listing_notes WHERE id = ?', (note_id,))
        conn.commit()
        conn.close()
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': ss_errors._safe_error(e, 'fb-listings-notes-delete')}), 500


def api_fb_listings_quantity():
    """Update Facebook listing quantity for a UPC."""
    try:
        data = request.get_json() or {}
        upc = ss_normalization._normalize_upc(data.get('upc'))
        qty = int(data.get('quantity', 1))
        if not upc:
            return jsonify({'success': False, 'error': 'Missing upc'}), 400
        if qty < 1:
            return jsonify({'success': False, 'error': 'Quantity must be at least 1'}), 400

        ss_listing_lifecycle._ensure_bol_list_status_column()
        conn = sqlite3.connect('bol.db')
        cur = conn.cursor()
        cur.execute('SELECT COALESCE(listed_facebook,0), COALESCE(listed_facebook_qty,1), listed_facebook_date FROM bol_items WHERE upc = ? COLLATE NOCASE', (upc,))
        row = cur.fetchone()
        if not row:
            conn.close()
            return jsonify({'success': False, 'error': 'UPC not found'}), 404
        if row[0] != 1:
            conn.close()
            return jsonify({'success': False, 'error': 'Item is not listed on Facebook'}), 400
        prev_qty = int(row[1] or 1)
        prev_listed_date = row[2]

        cur.execute('''
            UPDATE bol_items
            SET listed_facebook_qty=?,
                listed_facebook_source=COALESCE(NULLIF(TRIM(listed_facebook_source), ''), 'user')
            WHERE upc = ? COLLATE NOCASE
        ''', (qty, upc))
        conn.commit()
        conn.close()
        ss_runtime.cache.clear()
        ss_caching.update_data_version()
        _log_fb_listing_action(
            action='manual_qty_change',
            upc=upc,
            delta_qty=(qty - prev_qty),
            prev_qty=prev_qty,
            new_qty=qty,
            prev_listed=1,
            prev_listed_date=prev_listed_date
        )
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': ss_errors._safe_error(e, 'fb-listings-quantity')}), 500


def api_fb_listings_unlist():
    """Unlist a Facebook Marketplace item for a UPC."""
    try:
        data = request.get_json() or {}
        upc = ss_normalization._normalize_upc(data.get('upc'))
        if not upc:
            return jsonify({'success': False, 'error': 'Missing upc'}), 400

        ss_listing_lifecycle._ensure_bol_list_status_column()
        conn = sqlite3.connect('bol.db')
        cur = conn.cursor()
        cur.execute('SELECT COALESCE(listed_facebook,0), COALESCE(listed_facebook_qty,1), listed_facebook_date FROM bol_items WHERE upc = ? COLLATE NOCASE', (upc,))
        prev_row = cur.fetchone()
        prev_listed = int(prev_row[0] or 0) if prev_row else 0
        prev_qty = int(prev_row[1] or 0) if prev_row else 0
        prev_listed_date = prev_row[2] if prev_row else None
        cur.execute('''
            UPDATE bol_items
            SET listed_facebook=0, listed_facebook_date=NULL, listed_facebook_qty=NULL, listed_facebook_source=NULL
            WHERE upc = ? COLLATE NOCASE
        ''', (upc,))
        updated = cur.rowcount

        # Update legacy list_status column for backward compatibility
        cur.execute('''
            UPDATE bol_items
            SET list_status = CASE
                WHEN COALESCE(listed_amazon, 0) = 1 OR COALESCE(listed_ebay, 0) = 1 OR COALESCE(listed_facebook, 0) = 1
                THEN 'listed'
                ELSE NULL
            END
            WHERE upc = ? COLLATE NOCASE
        ''', (upc,))

        conn.commit()
        conn.close()
        ss_runtime.cache.clear()
        ss_caching.update_data_version()
        if prev_listed == 1:
            _log_fb_listing_action(
                action='manual_unlist',
                upc=upc,
                delta_qty=(0 - prev_qty),
                prev_qty=prev_qty,
                new_qty=0,
                prev_listed=prev_listed,
                prev_listed_date=prev_listed_date
            )
        return jsonify({'success': True, 'updated': updated})
    except Exception as e:
        return jsonify({'success': False, 'error': ss_errors._safe_error(e, 'fb-listings-unlist')}), 500
