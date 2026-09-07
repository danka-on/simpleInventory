"""Prep schema for Sweet Shelves."""

import json
import sqlite3
import threading
import time
from . import listing_checks as ss_listing_checks, normalization as ss_normalization


_items_prep_ensure_lock = threading.Lock()


_items_prep_ensure_state = {
    'running': False,
    'last_success': 0.0
}


_ITEMS_PREP_ENSURE_MIN_INTERVAL_SECONDS = 300


def _items_prep_record_exception(cur, *, upc, base_upc=None, lot_number='', action='overage_override',
                                 quantity=1, unchecked_remaining=None, source='item-prep',
                                 note=None, meta=None):
    if cur is None:
        return
    upc_n = ss_normalization._normalize_upc_preserve_suffix_for_match(upc)
    base_upc_n = ss_normalization._normalize_upc_preserve_suffix_for_match(base_upc or upc_n)
    lot_n = ss_normalization._normalize_lot_number(lot_number)
    action_n = (action or 'overage_override').strip().lower()
    source_n = (source or 'item-prep').strip() or 'item-prep'
    note_n = (note or '').strip() or None
    try:
        qty_n = int(quantity or 1)
    except Exception:
        qty_n = 1
    try:
        unchecked_n = int(unchecked_remaining) if unchecked_remaining is not None else None
    except Exception:
        unchecked_n = None

    meta_json = None
    try:
        if meta is not None:
            meta_json = json.dumps(meta, ensure_ascii=False)
    except Exception:
        meta_json = None

    cur.execute('''
        CREATE TABLE IF NOT EXISTS items_prep_exceptions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL,
            upc TEXT NOT NULL,
            base_upc TEXT NOT NULL,
            lot_number TEXT,
            action TEXT NOT NULL,
            quantity INTEGER,
            unchecked_remaining INTEGER,
            source TEXT,
            note TEXT,
            meta_json TEXT
        )
    ''')
    cur.execute('''
        INSERT INTO items_prep_exceptions (
            created_at, upc, base_upc, lot_number, action,
            quantity, unchecked_remaining, source, note, meta_json
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ''', (
        ss_listing_checks._listagent_now_iso(), upc_n, base_upc_n, lot_n, action_n,
        qty_n, unchecked_n, source_n, note_n, meta_json
    ))


def _ensure_items_prep_tables(force=False):
    while True:
        now_ts = time.time()
        with _items_prep_ensure_lock:
            last_success = float(_items_prep_ensure_state.get('last_success') or 0.0)
            running = bool(_items_prep_ensure_state.get('running'))
            if (
                (not force)
                and last_success > 0
                and (now_ts - last_success) < _ITEMS_PREP_ENSURE_MIN_INTERVAL_SECONDS
            ):
                return
            if not running:
                _items_prep_ensure_state['running'] = True
                break
        time.sleep(0.01)

    conn = None
    success = False
    try:
        conn = sqlite3.connect('bol.db')
        cur = conn.cursor()
        cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='items_prep_status'")
        has_status_table = cur.fetchone() is not None

        if not has_status_table:
            cur.execute('''
                CREATE TABLE IF NOT EXISTS items_prep_status (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    upc TEXT NOT NULL,
                    lot_number TEXT NOT NULL DEFAULT '',
                    status TEXT,
                    reason TEXT,
                    note TEXT,
                    updated_at TEXT,
                    location TEXT,
                    pictureposition TEXT,
                    quantity INTEGER DEFAULT 1,
                    UNIQUE(upc, lot_number)
                )
            ''')
        else:
            cur.execute("PRAGMA table_info(items_prep_status)")
            status_info = cur.fetchall()
            status_cols = [r[1] for r in status_info]
            has_upc_pk = any((r[1] == 'upc' and int(r[5] or 0) == 1) for r in status_info)
            needs_migration = (
                'id' not in status_cols or
                'lot_number' not in status_cols or
                has_upc_pk
            )

            if needs_migration:
                select_location = 'location' if 'location' in status_cols else 'NULL'
                select_pictureposition = 'pictureposition' if 'pictureposition' in status_cols else 'NULL'
                select_quantity = 'quantity' if 'quantity' in status_cols else '1'
                cur.execute('''
                    CREATE TABLE IF NOT EXISTS items_prep_status_new (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        upc TEXT NOT NULL,
                        lot_number TEXT NOT NULL DEFAULT '',
                        status TEXT,
                        reason TEXT,
                        note TEXT,
                        updated_at TEXT,
                        location TEXT,
                        pictureposition TEXT,
                        quantity INTEGER DEFAULT 1,
                        UNIQUE(upc, lot_number)
                    )
                ''')
                cur.execute(f'''
                    INSERT OR REPLACE INTO items_prep_status_new
                    (upc, lot_number, status, reason, note, updated_at, location, pictureposition, quantity)
                    SELECT
                        upc,
                        '',
                        status,
                        reason,
                        note,
                        updated_at,
                        {select_location},
                        {select_pictureposition},
                        COALESCE({select_quantity}, 1)
                    FROM items_prep_status
                    WHERE upc IS NOT NULL
                      AND TRIM(COALESCE(upc, '')) != ''
                ''')
                cur.execute('DROP TABLE items_prep_status')
                cur.execute('ALTER TABLE items_prep_status_new RENAME TO items_prep_status')
            else:
                if 'lot_number' not in status_cols:
                    cur.execute("ALTER TABLE items_prep_status ADD COLUMN lot_number TEXT NOT NULL DEFAULT ''")
                if 'location' not in status_cols:
                    cur.execute('ALTER TABLE items_prep_status ADD COLUMN location TEXT')
                if 'pictureposition' not in status_cols:
                    cur.execute('ALTER TABLE items_prep_status ADD COLUMN pictureposition TEXT')
                if 'quantity' not in status_cols:
                    cur.execute('ALTER TABLE items_prep_status ADD COLUMN quantity INTEGER DEFAULT 1')
                # Keep lot_number normalized for reliable matching.
                cur.execute("UPDATE items_prep_status SET lot_number = COALESCE(TRIM(lot_number), '')")

        cur.execute('CREATE UNIQUE INDEX IF NOT EXISTS idx_items_prep_status_upc_lot ON items_prep_status(upc, lot_number)')
        cur.execute('CREATE INDEX IF NOT EXISTS idx_items_prep_status_upc ON items_prep_status(upc)')
        cur.execute('CREATE INDEX IF NOT EXISTS idx_items_prep_status_lot ON items_prep_status(lot_number)')

        # One-time compatibility backfill:
        # if a UPC maps to exactly one lot in bol_items, assign that lot to legacy lotless prep rows.
        cur.execute('CREATE TABLE IF NOT EXISTS app_metadata (key TEXT PRIMARY KEY, value TEXT)')
        cur.execute('SELECT value FROM app_metadata WHERE key = ? LIMIT 1', ('items_prep_lot_backfill_v1',))
        lot_backfill_marker_row = cur.fetchone()
        lot_backfill_marker = (lot_backfill_marker_row[0] if lot_backfill_marker_row else '')
        if str(lot_backfill_marker) != '1':
            cur.execute('''
                SELECT upc, MIN(COALESCE(lot_number, '')) AS lot_number
                FROM bol_items
                WHERE lot_number IS NOT NULL
                  AND TRIM(COALESCE(lot_number, '')) != ''
                GROUP BY upc
                HAVING COUNT(DISTINCT COALESCE(lot_number, '')) = 1
            ''')
            for upc, only_lot in cur.fetchall():
                cur.execute('''
                    SELECT 1
                    FROM items_prep_status
                    WHERE upc = ? COLLATE NOCASE
                      AND COALESCE(lot_number, '') = ? COLLATE NOCASE
                    LIMIT 1
                ''', (upc, only_lot))
                if cur.fetchone():
                    continue
                cur.execute('''
                    UPDATE items_prep_status
                    SET lot_number = ?
                    WHERE upc = ? COLLATE NOCASE
                      AND COALESCE(lot_number, '') = ''
                ''', (only_lot, upc))
            cur.execute(
                'INSERT OR REPLACE INTO app_metadata (key, value) VALUES (?, ?)',
                ('items_prep_lot_backfill_v1', '1')
            )

        cur.execute('''
            CREATE TABLE IF NOT EXISTS items_prep_images (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                upc TEXT,
                image_path TEXT,
                created_at TEXT,
                deleted_at TEXT,
                expires_at TEXT,
                trash_path TEXT
            )
        ''')
        cur.execute('''
            CREATE TABLE IF NOT EXISTS items_prep_notes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                upc TEXT,
                note TEXT,
                created_at TEXT
            )
        ''')
        cur.execute('''
            CREATE TABLE IF NOT EXISTS items_prep_media (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                upc TEXT,
                row_status TEXT DEFAULT '',
                media_type TEXT,
                file_path TEXT,
                mime_type TEXT,
                created_at TEXT
            )
        ''')

        cur.execute("PRAGMA table_info(items_prep_images)")
        cols = [r[1] for r in cur.fetchall()]
        if 'deleted_at' not in cols:
            cur.execute("ALTER TABLE items_prep_images ADD COLUMN deleted_at TEXT")
        if 'expires_at' not in cols:
            cur.execute("ALTER TABLE items_prep_images ADD COLUMN expires_at TEXT")
        if 'trash_path' not in cols:
            cur.execute("ALTER TABLE items_prep_images ADD COLUMN trash_path TEXT")
        if 'row_status' not in cols:
            cur.execute("ALTER TABLE items_prep_images ADD COLUMN row_status TEXT DEFAULT ''")
        # Add rotation column to keep track of image orientation (degrees)
        if 'rotation' not in cols:
            try:
                cur.execute("ALTER TABLE items_prep_images ADD COLUMN rotation INTEGER DEFAULT 0")
            except Exception:
                # some older sqlite versions may behave differently; ignore errors
                pass

        cur.execute("PRAGMA table_info(items_prep_notes)")
        note_cols = [r[1] for r in cur.fetchall()]
        if 'row_status' not in note_cols:
            cur.execute("ALTER TABLE items_prep_notes ADD COLUMN row_status TEXT DEFAULT ''")

        cur.execute("PRAGMA table_info(items_prep_media)")
        media_cols = [r[1] for r in cur.fetchall()]
        if media_cols:
            if 'row_status' not in media_cols:
                cur.execute("ALTER TABLE items_prep_media ADD COLUMN row_status TEXT DEFAULT ''")
            if 'media_type' not in media_cols:
                cur.execute("ALTER TABLE items_prep_media ADD COLUMN media_type TEXT DEFAULT ''")
            if 'mime_type' not in media_cols:
                cur.execute("ALTER TABLE items_prep_media ADD COLUMN mime_type TEXT DEFAULT ''")

        # Speed up per-UPC image/note lookups used by list views.
        cur.execute('CREATE INDEX IF NOT EXISTS idx_items_prep_images_upc ON items_prep_images(upc)')
        cur.execute('CREATE INDEX IF NOT EXISTS idx_items_prep_images_upc_deleted ON items_prep_images(upc, deleted_at)')
        cur.execute('CREATE INDEX IF NOT EXISTS idx_items_prep_images_upc_status_deleted ON items_prep_images(upc, row_status, deleted_at)')
        cur.execute('CREATE INDEX IF NOT EXISTS idx_items_prep_notes_upc ON items_prep_notes(upc)')
        cur.execute('CREATE INDEX IF NOT EXISTS idx_items_prep_notes_upc_status ON items_prep_notes(upc, row_status)')
        cur.execute('CREATE INDEX IF NOT EXISTS idx_items_prep_media_upc ON items_prep_media(upc)')
        cur.execute('CREATE INDEX IF NOT EXISTS idx_items_prep_media_scope ON items_prep_media(upc, row_status, media_type)')

        # Exception audit trail for intentional overage adds.
        cur.execute('''
            CREATE TABLE IF NOT EXISTS items_prep_exceptions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                upc TEXT NOT NULL,
                base_upc TEXT NOT NULL,
                lot_number TEXT,
                action TEXT NOT NULL,
                quantity INTEGER,
                unchecked_remaining INTEGER,
                source TEXT,
                note TEXT,
                meta_json TEXT
            )
        ''')
        cur.execute('CREATE INDEX IF NOT EXISTS idx_items_prep_exceptions_created_at ON items_prep_exceptions(created_at, id)')
        cur.execute('CREATE INDEX IF NOT EXISTS idx_items_prep_exceptions_upc ON items_prep_exceptions(upc)')
        cur.execute('CREATE INDEX IF NOT EXISTS idx_items_prep_exceptions_base_upc ON items_prep_exceptions(base_upc)')
        
        # Create print queue table for cross-device synchronization
        cur.execute('''CREATE TABLE IF NOT EXISTS print_queue (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            barcode TEXT NOT NULL,
            added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(title, barcode)
        )''')
        
        conn.commit()
        success = True
    except Exception as e:
        print('Failed ensuring items_prep tables:', e)
    finally:
        try:
            if conn is not None:
                conn.close()
        except Exception:
            pass
        with _items_prep_ensure_lock:
            _items_prep_ensure_state['running'] = False
            if success:
                _items_prep_ensure_state['last_success'] = time.time()
