"""Listing checks for Sweet Shelves."""

import datetime
from flask import url_for
from . import (
    database as ss_database, listing_queue as ss_listing_queue, listing_settings as ss_listing_settings,
)


def _listagent_init_tables(cur):
    # listagent.db might be created after startup; ensure WAL gets enabled.
    try:
        cur.execute('PRAGMA journal_mode=WAL')
    except Exception:
        pass

    cur.execute('''
        CREATE TABLE IF NOT EXISTS listing_queue (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            upc TEXT NOT NULL,
            title TEXT,
            source TEXT,
            added_mode TEXT NOT NULL DEFAULT 'my',
            item_status TEXT,
            status TEXT NOT NULL DEFAULT 'queued',
            added_at TEXT NOT NULL,
            listed_at TEXT,
            listed_platform TEXT,
            listed_listing_id TEXT,
            listed_offer_id TEXT,
            listed_sku TEXT,
            listed_asin TEXT,
            listed_url TEXT,
            listed_ebay_at TEXT,
            listed_amazon_at TEXT,
            removed_at TEXT
        )
    ''')
    cur.execute('CREATE INDEX IF NOT EXISTS idx_listing_queue_status_added_at ON listing_queue(status, added_at)')
    try:
        # Enforce only one active (queued) entry per UPC.
        cur.execute("CREATE UNIQUE INDEX IF NOT EXISTS uq_listing_queue_active_upc ON listing_queue(upc) WHERE status='queued'")
    except Exception:
        # Partial indexes require newer SQLite; degrade gracefully.
        pass
    try:
        # Keep done entries unique too (so queue stays clean).
        cur.execute("CREATE UNIQUE INDEX IF NOT EXISTS uq_listing_queue_done_upc ON listing_queue(upc) WHERE status='done'")
    except Exception:
        pass

    # Schema migration: add item_status for queue provenance (e.g., good/bad from list manager)
    try:
        cur.execute('PRAGMA table_info(listing_queue)')
        cols = []
        for r in cur.fetchall() or []:
            try:
                cols.append((r['name'] or '').lower())
            except Exception:
                try:
                    cols.append((r[1] or '').lower())
                except Exception:
                    pass
        if 'item_status' not in cols:
            cur.execute('ALTER TABLE listing_queue ADD COLUMN item_status TEXT')
        if 'listed_ebay_at' not in cols:
            cur.execute('ALTER TABLE listing_queue ADD COLUMN listed_ebay_at TEXT')
        if 'listed_amazon_at' not in cols:
            cur.execute('ALTER TABLE listing_queue ADD COLUMN listed_amazon_at TEXT')
        if 'removed_at' not in cols:
            cur.execute('ALTER TABLE listing_queue ADD COLUMN removed_at TEXT')
        if 'added_mode' not in cols:
            cur.execute("ALTER TABLE listing_queue ADD COLUMN added_mode TEXT NOT NULL DEFAULT 'my'")
    except Exception:
        pass

    # Legacy fix: manual queue adds from Item Manager were incorrectly tagged as auto.
    try:
        cur.execute("""
            UPDATE listing_queue
            SET added_mode = 'my'
            WHERE status IN ('queued', 'done')
              AND COALESCE(NULLIF(TRIM(source), ''), '') = 'list_manager'
              AND COALESCE(NULLIF(TRIM(added_mode), ''), 'my') = 'auto'
        """)
    except Exception:
        pass

    # Uploaded listing photos (mobile -> desktop)
    cur.execute('''
        CREATE TABLE IF NOT EXISTS listing_photos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            upc TEXT NOT NULL,
            image_path TEXT NOT NULL,
            original_filename TEXT,
            size_bytes INTEGER,
            created_at TEXT NOT NULL
        )
    ''')
    cur.execute('CREATE INDEX IF NOT EXISTS idx_listing_photos_upc_created_at ON listing_photos(upc, created_at, id)')

    cur.execute('''
        CREATE TABLE IF NOT EXISTS listing_agent_working_drafts (
            upc TEXT PRIMARY KEY,
            draft_json TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
    ''')
    cur.execute('CREATE INDEX IF NOT EXISTS idx_listing_agent_working_drafts_updated_at ON listing_agent_working_drafts(updated_at)')

    cur.execute('''
        CREATE TABLE IF NOT EXISTS listing_agent_check_requests (
            upc TEXT PRIMARY KEY,
            check_quantity INTEGER DEFAULT 1,
            take_pictures INTEGER DEFAULT 0,
            check_color INTEGER DEFAULT 0,
            check_condition INTEGER DEFAULT 0,
            custom_note TEXT,
            status TEXT NOT NULL DEFAULT 'open',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            completed_at TEXT,
            completed_by TEXT,
            source TEXT
        )
    ''')
    try:
        cur.execute('ALTER TABLE listing_agent_check_requests ADD COLUMN check_color INTEGER DEFAULT 0')
    except Exception:
        pass
    try:
        cur.execute('ALTER TABLE listing_agent_check_requests ADD COLUMN check_condition INTEGER DEFAULT 0')
    except Exception:
        pass
    cur.execute('CREATE INDEX IF NOT EXISTS idx_listing_agent_check_requests_status_updated_at ON listing_agent_check_requests(status, updated_at, created_at)')

    cur.execute('''
        CREATE TABLE IF NOT EXISTS listing_agent_checker_notes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            upc TEXT NOT NULL,
            note TEXT NOT NULL,
            note_type TEXT NOT NULL DEFAULT 'checker',
            created_at TEXT NOT NULL
        )
    ''')
    cur.execute('CREATE INDEX IF NOT EXISTS idx_listing_agent_checker_notes_upc_created_at ON listing_agent_checker_notes(upc, created_at, id)')

    cur.execute('''
        CREATE TABLE IF NOT EXISTS listing_agent_checker_media (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            upc TEXT NOT NULL,
            media_type TEXT NOT NULL DEFAULT 'image',
            file_path TEXT NOT NULL,
            original_filename TEXT,
            size_bytes INTEGER,
            note_type TEXT NOT NULL DEFAULT 'checker',
            created_at TEXT NOT NULL
        )
    ''')
    cur.execute('CREATE INDEX IF NOT EXISTS idx_listing_agent_checker_media_upc_created_at ON listing_agent_checker_media(upc, created_at, id)')


def _listagent_row_to_dict(row):
    if not row:
        return None
    try:
        return {k: row[k] for k in row.keys()}
    except Exception:
        try:
            return dict(row)
        except Exception:
            return None


def _listagent_now_iso():
    return datetime.datetime.now().isoformat()


def _listagent_check_request_defaults():
    return {
        'check_quantity': 1,
        'take_pictures': 0,
        'check_color': 0,
        'check_condition': 0,
        'custom_note': '',
        'status': 'open',
    }


def _listagent_get_check_request(upc):
    upc = (upc or '').strip()
    if not upc:
        return None
    with ss_database.db_connection('listagent.db') as conn:
        cur = conn.cursor()
        _listagent_init_tables(cur)
        variants = ss_listing_queue._listagent_upc_variants(upc)
        placeholders = ','.join('?' for _ in variants)
        cur.execute(
            f'''
                SELECT *
                FROM listing_agent_check_requests
                WHERE upc IN ({placeholders})
                ORDER BY CASE WHEN COALESCE(NULLIF(TRIM(status), ''), 'open') = 'complete' THEN 1 ELSE 0 END,
                         updated_at DESC
                LIMIT 1
            ''',
            tuple(variants)
        )
        row = cur.fetchone()
        if not row:
            return None
        item = _listagent_row_to_dict(row)
        if not item:
            return None
        item['check_quantity'] = max(0, ss_listing_settings._listingagent_parse_int(item.get('check_quantity'), 0) or 0)
        item['take_pictures'] = 1 if ss_listing_settings._listingagent_parse_int(item.get('take_pictures'), 0) else 0
        item['check_color'] = 1 if ss_listing_settings._listingagent_parse_int(item.get('check_color'), 0) else 0
        item['check_condition'] = 1 if ss_listing_settings._listingagent_parse_int(item.get('check_condition'), 0) else 0
        item['custom_note'] = (item.get('custom_note') or '').strip()
        item['status'] = (item.get('status') or 'open').strip().lower() or 'open'
        return item


def _listagent_set_check_request(*, upc, check_quantity=None, take_pictures=None, check_color=None, check_condition=None, custom_note=None, status=None, source=None):
    upc = (upc or '').strip()
    if not upc:
        raise ValueError('upc is required')
    note = (custom_note or '').strip()
    qty = None
    if check_quantity is not None and str(check_quantity).strip() != '':
        try:
            qty = max(0, int(float(check_quantity)))
        except Exception:
            qty = 0
    take = None if take_pictures is None else (1 if bool(take_pictures) else 0)
    color = None if check_color is None else (1 if bool(check_color) else 0)
    condition = None if check_condition is None else (1 if bool(check_condition) else 0)
    st = (status or 'open').strip().lower() or 'open'
    if st not in ('open', 'complete'):
        st = 'open'
    src = (source or '').strip() or None

    has_payload = bool(
        note or
        (qty is not None and qty > 0) or
        (take is not None and take == 1) or
        (color is not None and color == 1) or
        (condition is not None and condition == 1) or
        st == 'complete'
    )
    now = _listagent_now_iso()
    with ss_database.db_connection('listagent.db') as conn:
        cur = conn.cursor()
        _listagent_init_tables(cur)
        if not has_payload:
            cur.execute('DELETE FROM listing_agent_check_requests WHERE upc = ? COLLATE NOCASE', (upc,))
            return None
        cur.execute('''
            SELECT *
            FROM listing_agent_check_requests
            WHERE upc = ? COLLATE NOCASE
            LIMIT 1
        ''', (upc,))
        existing = cur.fetchone()
        created_at = now
        completed_at = None
        completed_by = None
        if existing:
            created_at = (existing['created_at'] or now) if hasattr(existing, '__getitem__') else now
            if (existing['status'] or '').strip().lower() == 'complete':
                completed_at = existing['completed_at']
                completed_by = existing['completed_by']
        if st == 'complete':
            completed_at = completed_at or now
        elif st != 'complete':
            completed_at = None
            completed_by = None
        cur.execute('''
            INSERT INTO listing_agent_check_requests (
                upc, check_quantity, take_pictures, check_color, check_condition, custom_note, status,
                created_at, updated_at, completed_at, completed_by, source
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(upc) DO UPDATE SET
                check_quantity = excluded.check_quantity,
                take_pictures = excluded.take_pictures,
                check_color = excluded.check_color,
                check_condition = excluded.check_condition,
                custom_note = excluded.custom_note,
                status = excluded.status,
                updated_at = excluded.updated_at,
                completed_at = excluded.completed_at,
                completed_by = excluded.completed_by,
                source = excluded.source
        ''', (
            upc,
            qty if qty is not None else 1,
            1 if take else 0,
            1 if color else 0,
            1 if condition else 0,
            note or None,
            st,
            created_at,
            now,
            completed_at,
            completed_by,
            src
        ))
        cur.execute('SELECT * FROM listing_agent_check_requests WHERE upc = ? COLLATE NOCASE LIMIT 1', (upc,))
        row = cur.fetchone()
        return _listagent_row_to_dict(row)


def _listagent_mark_check_request_complete(*, upc, complete=True, completed_by=None):
    upc = (upc or '').strip()
    if not upc:
        raise ValueError('upc is required')
    now = _listagent_now_iso()
    status = 'complete' if complete else 'open'
    with ss_database.db_connection('listagent.db') as conn:
        cur = conn.cursor()
        _listagent_init_tables(cur)
        cur.execute('''
            UPDATE listing_agent_check_requests
            SET status = ?,
                updated_at = ?,
                completed_at = CASE WHEN ? = 'complete' THEN COALESCE(completed_at, ?) ELSE NULL END,
                completed_by = CASE WHEN ? = 'complete' THEN COALESCE(?, completed_by) ELSE NULL END
            WHERE upc = ? COLLATE NOCASE
        ''', (
            status, now, status, now, status, (completed_by or None), upc
        ))
        if cur.rowcount == 0 and complete:
            cur.execute('''
                INSERT INTO listing_agent_check_requests (
                    upc, check_quantity, take_pictures, check_color, check_condition, custom_note, status,
                    created_at, updated_at, completed_at, completed_by, source
                )
                VALUES (?, 1, 0, 0, 0, NULL, 'complete', ?, ?, ?, ?, NULL)
            ''', (upc, now, now, now, completed_by or None))
        cur.execute('SELECT * FROM listing_agent_check_requests WHERE upc = ? COLLATE NOCASE LIMIT 1', (upc,))
        return _listagent_row_to_dict(cur.fetchone())


def _listagent_get_check_requests(*, limit=100, status='open'):
    limit = int(limit or 100)
    limit = max(1, min(limit, 500))
    status = (status or '').strip().lower()
    with ss_database.db_connection('listagent.db') as conn:
        cur = conn.cursor()
        _listagent_init_tables(cur)
        sql = 'SELECT * FROM listing_agent_check_requests WHERE 1=1'
        params = []
        if status in ('open', 'complete'):
            sql += ' AND COALESCE(NULLIF(TRIM(status), \'\'), \'open\') = ?'
            params.append(status)
        sql += ' ORDER BY CASE WHEN COALESCE(NULLIF(TRIM(status), \'\'), \'open\') = \'complete\' THEN 1 ELSE 0 END, updated_at DESC, created_at DESC LIMIT ?'
        params.append(limit)
        cur.execute(sql, tuple(params))
        rows = [_listagent_row_to_dict(r) for r in cur.fetchall()]
    return rows


def _listagent_enrich_check_request_rows(rows, *, base_url=''):
    base_url = (base_url or '').rstrip('/')
    requested = []
    seen = set()
    variants = []
    variant_to_requested = {}

    for row in rows or []:
        key = (row.get('upc') or '').strip()
        if not key or key in seen:
            continue
        seen.add(key)
        requested.append(key)
        for variant in ss_listing_queue._listagent_upc_variants(key):
            if variant not in variants:
                variants.append(variant)
            owners = variant_to_requested.setdefault(variant, [])
            if key not in owners:
                owners.append(key)

    if not requested or not variants:
        return rows

    placeholders = ','.join('?' for _ in variants)
    photo_map = {}
    raw_map = {}
    bol_map = {}
    rack_map = {}

    try:
        with ss_database.db_connection('listagent.db') as conn:
            cur = conn.cursor()
            _listagent_init_tables(cur)
            cur.execute(
                f'''
                    SELECT upc, image_path
                    FROM listing_photos
                    WHERE upc COLLATE NOCASE IN ({placeholders})
                    ORDER BY created_at DESC, id DESC
                ''',
                tuple(variants)
            )
            for row in cur.fetchall():
                key = (row['upc'] or '').strip()
                rel = (row['image_path'] or '').strip()
                if not key or not rel or key in photo_map:
                    continue
                url = f"{base_url}{url_for('static', filename=rel)}" if base_url else url_for('static', filename=rel)
                photo_map[key] = url
    except Exception:
        photo_map = {}

    try:
        with ss_database.db_connection('rawbol.db') as conn:
            cur = conn.cursor()
            cur.execute(
                f'''
                    SELECT TRIM(upc) AS upc, item_description, image_url
                    FROM raw_bol_items
                    WHERE TRIM(upc) COLLATE NOCASE IN ({placeholders})
                    ORDER BY import_date DESC, id DESC
                ''',
                tuple(variants)
            )
            for row in cur.fetchall():
                key = (row['upc'] or '').strip()
                if not key or key in raw_map:
                    continue
                raw_map[key] = {
                    'title': (row['item_description'] or '').strip(),
                    'image': (row['image_url'] or '').strip()
                }
    except Exception:
        raw_map = {}

    try:
        with ss_database.db_connection('bol.db') as conn:
            cur = conn.cursor()
            cur.execute(
                f'''
                    SELECT TRIM(upc) AS upc, item_description, image_url
                    FROM bol_items
                    WHERE TRIM(upc) COLLATE NOCASE IN ({placeholders})
                    ORDER BY import_date DESC, id DESC
                ''',
                tuple(variants)
            )
            for row in cur.fetchall():
                key = (row['upc'] or '').strip()
                if not key or key in bol_map:
                    continue
                bol_map[key] = {
                    'title': (row['item_description'] or '').strip(),
                    'image': (row['image_url'] or '').strip()
                }
    except Exception:
        bol_map = {}

    try:
        with ss_database.db_connection('searchRack.db') as conn:
            cur = conn.cursor()
            cur.execute(
                f'''
                    SELECT TRIM(BARCODE) AS upc, TITLE, IMAGE, IMAGES
                    FROM SEARCHRACK
                    WHERE TRIM(BARCODE) COLLATE NOCASE IN ({placeholders})
                    ORDER BY CREATED_AT DESC, ID DESC
                ''',
                tuple(variants)
            )
            for row in cur.fetchall():
                key = (row['upc'] or '').strip()
                if not key or key in rack_map:
                    continue
                rack_map[key] = {
                    'title': (row['TITLE'] or '').strip(),
                    'image': (row['IMAGE'] or row['IMAGES'] or '').strip()
                }
    except Exception:
        rack_map = {}

    out = []
    for row in rows or []:
        item = dict(row or {})
        key = (item.get('upc') or '').strip()
        title = ''
        image = ''
        for variant in ss_listing_queue._listagent_upc_variants(key):
            if not image and photo_map.get(variant):
                image = photo_map.get(variant) or ''
            if not title and raw_map.get(variant, {}).get('title'):
                title = raw_map[variant]['title']
            if not image and raw_map.get(variant, {}).get('image'):
                image = raw_map[variant]['image']
            if not title and bol_map.get(variant, {}).get('title'):
                title = bol_map[variant]['title']
            if not image and bol_map.get(variant, {}).get('image'):
                image = bol_map[variant]['image']
            if not title and rack_map.get(variant, {}).get('title'):
                title = rack_map[variant]['title']
            if not image and rack_map.get(variant, {}).get('image'):
                image = rack_map[variant]['image']
            if title and image:
                break
        item['title'] = title or (item.get('title') or '').strip()
        item['image'] = image or (item.get('image') or '').strip()
        out.append(item)
    return out


def _listagent_get_check_notes(upc):
    upc = (upc or '').strip()
    if not upc:
        return []
    with ss_database.db_connection('listagent.db') as conn:
        cur = conn.cursor()
        _listagent_init_tables(cur)
        variants = ss_listing_queue._listagent_upc_variants(upc)
        placeholders = ','.join('?' for _ in variants)
        cur.execute(
            f'''
                SELECT *
                FROM listing_agent_checker_notes
                WHERE upc IN ({placeholders})
                ORDER BY created_at DESC, id DESC
            ''',
            tuple(variants)
        )
        return [_listagent_row_to_dict(r) for r in cur.fetchall()]


def _listagent_add_check_note(*, upc, note, note_type='checker'):
    upc = (upc or '').strip()
    note = (note or '').strip()
    if not upc or not note:
        raise ValueError('upc and note are required')
    now = _listagent_now_iso()
    with ss_database.db_connection('listagent.db') as conn:
        cur = conn.cursor()
        _listagent_init_tables(cur)
        cur.execute('''
            INSERT INTO listing_agent_checker_notes (upc, note, note_type, created_at)
            VALUES (?, ?, ?, ?)
        ''', (upc, note, (note_type or 'checker').strip() or 'checker', now))
        cur.execute('SELECT * FROM listing_agent_checker_notes WHERE id = ? LIMIT 1', (cur.lastrowid,))
        return _listagent_row_to_dict(cur.fetchone())


def _listagent_get_check_media(upc):
    upc = (upc or '').strip()
    if not upc:
        return []
    with ss_database.db_connection('listagent.db') as conn:
        cur = conn.cursor()
        _listagent_init_tables(cur)
        variants = ss_listing_queue._listagent_upc_variants(upc)
        placeholders = ','.join('?' for _ in variants)
        cur.execute(
            f'''
                SELECT *
                FROM listing_agent_checker_media
                WHERE upc IN ({placeholders})
                ORDER BY created_at DESC, id DESC
            ''',
            tuple(variants)
        )
        return [_listagent_row_to_dict(r) for r in cur.fetchall()]


def _listagent_get_check_request_statuses(upcs):
    requested = []
    seen = set()
    variants = []
    for raw in upcs or []:
        key = (raw or '').strip()
        if not key or key in seen:
            continue
        seen.add(key)
        requested.append(key)
        for variant in ss_listing_queue._listagent_upc_variants(key):
            if variant not in variants:
                variants.append(variant)
    if not variants:
        return {}
    placeholders = ','.join('?' for _ in variants)
    with ss_database.db_connection('listagent.db') as conn:
        cur = conn.cursor()
        _listagent_init_tables(cur)
        cur.execute(
            f'''
                SELECT *
                FROM listing_agent_check_requests
                WHERE upc IN ({placeholders})
            ''',
            tuple(variants)
        )
        rows = [_listagent_row_to_dict(r) for r in cur.fetchall()]
    by_upc = {}
    for row in rows:
        if not row:
            continue
        item = dict(row)
        item['check_quantity'] = max(0, ss_listing_settings._listingagent_parse_int(item.get('check_quantity'), 0) or 0)
        item['take_pictures'] = 1 if ss_listing_settings._listingagent_parse_int(item.get('take_pictures'), 0) else 0
        item['custom_note'] = (item.get('custom_note') or '').strip()
        item['status'] = (item.get('status') or 'open').strip().lower() or 'open'
        by_upc[(item.get('upc') or '').strip()] = item
    out = {}
    for req in requested:
        for variant in ss_listing_queue._listagent_upc_variants(req):
            if variant in by_upc:
                out[req] = by_upc[variant]
                break
    return out


def _listagent_add_check_media(*, upc, media_type='image', file_path='', original_filename='', size_bytes=None, note_type='checker'):
    upc = (upc or '').strip()
    media_type = (media_type or 'image').strip().lower() or 'image'
    file_path = (file_path or '').strip()
    if not upc or not file_path:
        raise ValueError('upc and file_path are required')
    now = _listagent_now_iso()
    with ss_database.db_connection('listagent.db') as conn:
        cur = conn.cursor()
        _listagent_init_tables(cur)
        cur.execute('''
            INSERT INTO listing_agent_checker_media (
                upc, media_type, file_path, original_filename, size_bytes, note_type, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
        ''', (upc, media_type, file_path, (original_filename or None), size_bytes, (note_type or 'checker').strip() or 'checker', now))
        cur.execute('SELECT * FROM listing_agent_checker_media WHERE id = ? LIMIT 1', (cur.lastrowid,))
        return _listagent_row_to_dict(cur.fetchone())
