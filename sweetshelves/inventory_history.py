"""Inventory history for Sweet Shelves."""

import datetime
import hashlib
import json
import sqlite3
import threading
import uuid
from . import (
    caching as ss_caching, config as ss_config, database as ss_database, inventory_age as
    ss_inventory_age, listing_alerts as ss_listing_alerts, normalization as ss_normalization,
)


def _ensure_removed_items_table(cur):
    """Create/migrate the durable rack-history table."""
    cur.execute('''
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
            item_position TEXT,
            undone_at TEXT,
            event_id TEXT,
            source_row_json TEXT,
            result_row_json TEXT,
            from_position TEXT,
            to_position TEXT,
            inventory_row_deleted INTEGER DEFAULT 0,
            event_status TEXT DEFAULT 'applied',
            applied_at TEXT
        )
    ''')
    cur.execute("PRAGMA table_info(removed_items)")
    existing = {str(row[1]).lower() for row in cur.fetchall()}
    migrations = {
        'order_id': 'TEXT',
        'barcode': 'TEXT',
        'title': 'TEXT',
        'quantity_removed': 'INTEGER',
        'removed_at': 'TEXT',
        'searchrack_id': 'INTEGER',
        'old_quantity': 'INTEGER',
        'new_quantity': 'INTEGER',
        'removal_type': 'TEXT',
        'item_position': 'TEXT',
        'undone_at': 'TEXT',
        'event_id': 'TEXT',
        'source_row_json': 'TEXT',
        'result_row_json': 'TEXT',
        'from_position': 'TEXT',
        'to_position': 'TEXT',
        'inventory_row_deleted': 'INTEGER DEFAULT 0',
        'event_status': "TEXT DEFAULT 'applied'",
        'applied_at': 'TEXT',
    }
    for column, definition in migrations.items():
        if column not in existing:
            cur.execute(f'ALTER TABLE removed_items ADD COLUMN "{column}" {definition}')

    cur.execute('''
        CREATE UNIQUE INDEX IF NOT EXISTS idx_removed_items_event_id
        ON removed_items(event_id)
        WHERE event_id IS NOT NULL AND event_id != ''
    ''')
    cur.execute('CREATE INDEX IF NOT EXISTS idx_removed_items_barcode ON removed_items(barcode)')
    cur.execute('CREATE INDEX IF NOT EXISTS idx_removed_items_searchrack_id ON removed_items(searchrack_id)')
    cur.execute('CREATE INDEX IF NOT EXISTS idx_removed_items_position ON removed_items(item_position)')
    cur.execute('CREATE INDEX IF NOT EXISTS idx_removed_items_removed_at ON removed_items(removed_at)')
    cur.execute('CREATE INDEX IF NOT EXISTS idx_removed_items_order_id ON removed_items(order_id)')


def _ensure_legacy_removed_table(cur):
    """Keep the retired removal log linked to its authoritative detailed event."""
    cur.execute('''
        CREATE TABLE IF NOT EXISTS removed (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT,
            barcode TEXT,
            qty INTEGER,
            time_removed TEXT,
            undone_at TEXT,
            detail_history_id INTEGER,
            source_location TEXT
        )
    ''')
    cur.execute('PRAGMA table_info(removed)')
    existing = {str(row[1]).lower() for row in cur.fetchall()}
    if 'undone_at' not in existing:
        cur.execute('ALTER TABLE removed ADD COLUMN undone_at TEXT')
    if 'detail_history_id' not in existing:
        cur.execute('ALTER TABLE removed ADD COLUMN detail_history_id INTEGER')
    if 'source_location' not in existing:
        cur.execute('ALTER TABLE removed ADD COLUMN source_location TEXT')
    cur.execute('CREATE INDEX IF NOT EXISTS idx_removed_detail_history_id ON removed(detail_history_id)')




def _row_casefold_dict(row):
    if row is None:
        return {}
    data = dict(row) if not isinstance(row, dict) else dict(row)
    return {str(key).lower(): value for key, value in data.items()}


def _searchrack_snapshot_location(snapshot):
    data = _row_casefold_dict(snapshot)
    item_position = str(data.get('item_position') or data.get('itemposition') or data.get('position') or '').strip()
    picture_position = str(data.get('pictureposition') or data.get('picture_position') or '').strip()
    if picture_position and item_position.lower() in ('', 'picture'):
        return picture_position
    return item_position or picture_position


def _searchrack_snapshot_value(snapshot, *names, default=''):
    data = _row_casefold_dict(snapshot)
    for name in names:
        value = data.get(str(name).lower())
        if value is not None:
            return value
    return default


def _ensure_searchrack_history_outbox(cur):
    """Outbox lives beside SEARCHRACK so its snapshot and mutation are one WAL transaction."""
    cur.execute('''
        CREATE TABLE IF NOT EXISTS searchrack_history_outbox (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_id TEXT NOT NULL UNIQUE,
            event_type TEXT NOT NULL,
            captured_at TEXT NOT NULL,
            searchrack_id INTEGER,
            barcode TEXT,
            title TEXT,
            from_position TEXT,
            to_position TEXT,
            old_quantity INTEGER,
            new_quantity INTEGER,
            source_row_json TEXT,
            result_row_json TEXT,
            processed_at TEXT,
            history_id INTEGER,
            last_error TEXT
        )
    ''')
    cur.execute('''
        CREATE INDEX IF NOT EXISTS idx_searchrack_history_outbox_pending
        ON searchrack_history_outbox(processed_at, id)
    ''')


def _ensure_searchrack_undo_claims(cur):
    """Idempotency claims committed in the same transaction as inventory restoration."""
    cur.execute('''
        CREATE TABLE IF NOT EXISTS searchrack_undo_claims (
            history_source TEXT NOT NULL,
            history_id INTEGER NOT NULL,
            claimed_at TEXT NOT NULL,
            searchrack_id INTEGER,
            restore_quantity INTEGER NOT NULL,
            old_quantity INTEGER,
            new_quantity INTEGER,
            recreated INTEGER DEFAULT 0,
            outbox_event_id TEXT,
            PRIMARY KEY (history_source, history_id)
        )
    ''')
    cur.execute('PRAGMA table_info(searchrack_undo_claims)')
    existing = {str(row[1]).lower() for row in cur.fetchall()}
    if 'outbox_event_id' not in existing:
        cur.execute('ALTER TABLE searchrack_undo_claims ADD COLUMN outbox_event_id TEXT')


def _searchrack_trigger_value(prefix, column, fallback='NULL'):
    return f'{prefix}.{ss_database._sqlite_ident(column)}' if column else fallback


def _searchrack_trigger_location(prefix, pos_col, pic_col):
    pos = _searchrack_trigger_value(prefix, pos_col, "''")
    pic = _searchrack_trigger_value(prefix, pic_col, "''")
    return f'''(
        CASE
            WHEN LOWER(TRIM(COALESCE({pos}, ''))) IN ('', 'picture')
                 AND TRIM(COALESCE({pic}, '')) != ''
            THEN TRIM(COALESCE({pic}, ''))
            ELSE COALESCE(NULLIF(TRIM(COALESCE({pos}, '')), ''), TRIM(COALESCE({pic}, '')), '')
        END
    )'''


def _searchrack_trigger_snapshot(prefix, columns):
    pairs = []
    for column in columns:
        label = "'" + str(column).replace("'", "''") + "'"
        pairs.extend([label, f'{prefix}.{ss_database._sqlite_ident(column)}'])
    return 'json_object(' + ', '.join(pairs) + ')'


def _searchrack_trigger_integral_quantity(value_sql):
    """SQLite expression accepting only integer-valued quantities, including legacy digit text."""
    text_sql = f'TRIM(CAST({value_sql} AS TEXT))'
    return f'''(
        CASE
            WHEN typeof({value_sql}) = 'integer' THEN 1
            WHEN typeof({value_sql}) = 'real'
                 AND {value_sql} = CAST({value_sql} AS INTEGER) THEN 1
            WHEN typeof({value_sql}) = 'text'
                 AND {text_sql} != ''
                 AND (
                     {text_sql} NOT GLOB '*[^0-9]*'
                     OR (
                         SUBSTR({text_sql}, 1, 1) IN ('-', '+')
                         AND LENGTH({text_sql}) > 1
                         AND SUBSTR({text_sql}, 2) NOT GLOB '*[^0-9]*'
                     )
                 ) THEN 1
            ELSE 0
        END
    )'''


def _install_searchrack_history_guard(conn):
    """Install persistent triggers that prevent non-positive rows and capture every stock/location change."""
    cur = conn.cursor()
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND LOWER(name)='searchrack'")
    if not cur.fetchone():
        return {'installed': False, 'reason': 'SEARCHRACK table not found'}

    cur.execute("PRAGMA table_info('SEARCHRACK')")
    columns = [row[1] for row in cur.fetchall()]
    lower = {str(column).lower(): column for column in columns}
    qty_col = lower.get('quantity') or lower.get('qty')
    if not qty_col:
        return {'installed': False, 'reason': 'SEARCHRACK quantity column not found'}

    id_col = lower.get('id')
    barcode_col = lower.get('barcode') or lower.get('upc')
    title_col = lower.get('title')
    pos_col = lower.get('item_position') or lower.get('itemposition') or lower.get('position')
    pic_col = lower.get('pictureposition') or lower.get('picture_position')
    _ensure_searchrack_history_outbox(cur)
    _ensure_searchrack_undo_claims(cur)
    cur.execute('''
        CREATE TABLE IF NOT EXISTS searchrack_history_guard_meta (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
    ''')
    conn.commit()

    qty_new = _searchrack_trigger_value('NEW', qty_col, '0')
    qty_old = _searchrack_trigger_value('OLD', qty_col, '0')
    row_id_new = _searchrack_trigger_value('NEW', id_col, 'NEW.rowid')
    row_id_old = _searchrack_trigger_value('OLD', id_col, 'OLD.rowid')
    barcode_new = _searchrack_trigger_value('NEW', barcode_col, "''")
    barcode_old = _searchrack_trigger_value('OLD', barcode_col, "''")
    title_new = _searchrack_trigger_value('NEW', title_col, "''")
    title_old = _searchrack_trigger_value('OLD', title_col, "''")
    from_location = _searchrack_trigger_location('OLD', pos_col, pic_col)
    to_location = _searchrack_trigger_location('NEW', pos_col, pic_col)
    old_snapshot = _searchrack_trigger_snapshot('OLD', columns)
    new_snapshot = _searchrack_trigger_snapshot('NEW', columns)

    watched_columns = [qty_col]
    for column in (pos_col, pic_col):
        if column and column not in watched_columns:
            watched_columns.append(column)
    watched_sql = ', '.join(ss_database._sqlite_ident(column) for column in watched_columns)
    changed_checks = [f'COALESCE(CAST({qty_new} AS INTEGER), 0) != COALESCE(CAST({qty_old} AS INTEGER), 0)']
    if pos_col:
        pos_new = _searchrack_trigger_value('NEW', pos_col, "''")
        pos_old = _searchrack_trigger_value('OLD', pos_col, "''")
        changed_checks.append(
            f"TRIM(COALESCE({pos_new}, '')) != TRIM(COALESCE({pos_old}, ''))"
        )
    if pic_col:
        pic_new = _searchrack_trigger_value('NEW', pic_col, "''")
        pic_old = _searchrack_trigger_value('OLD', pic_col, "''")
        changed_checks.append(
            f"TRIM(COALESCE({pic_new}, '')) != TRIM(COALESCE({pic_old}, ''))"
        )
    changed_when = ' OR '.join(changed_checks)
    integral_new = _searchrack_trigger_integral_quantity(qty_new)

    validate_insert_sql = f'''
        CREATE TRIGGER trg_searchrack_quantity_validate_insert_v3
        BEFORE INSERT ON SEARCHRACK
        WHEN {integral_new} = 0
        BEGIN
            SELECT RAISE(ABORT, 'SEARCHRACK quantity must be a finite whole number');
        END
    '''
    validate_update_sql = f'''
        CREATE TRIGGER trg_searchrack_quantity_validate_update_v3
        BEFORE UPDATE OF {ss_database._sqlite_ident(qty_col)} ON SEARCHRACK
        WHEN {integral_new} = 0
        BEGIN
            SELECT RAISE(ABORT, 'SEARCHRACK quantity must be a finite whole number');
        END
    '''
    update_sql = f'''
        CREATE TRIGGER trg_searchrack_history_update_v3
        AFTER UPDATE OF {watched_sql} ON SEARCHRACK
        WHEN {changed_when}
        BEGIN
            INSERT INTO searchrack_history_outbox (
                event_id, event_type, captured_at, searchrack_id, barcode, title,
                from_position, to_position, old_quantity, new_quantity,
                source_row_json, result_row_json
            ) VALUES (
                lower(hex(randomblob(16))),
                CASE
                    WHEN COALESCE(CAST({qty_new} AS INTEGER), 0) <= 0 THEN 'depletion'
                    WHEN COALESCE(CAST({qty_new} AS INTEGER), 0) != COALESCE(CAST({qty_old} AS INTEGER), 0) THEN 'quantity_change'
                    ELSE 'location_change'
                END,
                strftime('%Y-%m-%dT%H:%M:%f', 'now', 'localtime'),
                {row_id_old}, {barcode_old}, {title_old}, {from_location}, {to_location},
                COALESCE(CAST({qty_old} AS INTEGER), 0), COALESCE(CAST({qty_new} AS INTEGER), 0),
                {old_snapshot}, {new_snapshot}
            );
            DELETE FROM SEARCHRACK
            WHERE rowid = NEW.rowid AND COALESCE(CAST({qty_new} AS INTEGER), 0) <= 0;
        END
    '''

    insert_location = _searchrack_trigger_location('NEW', pos_col, pic_col)
    insert_sql = f'''
        CREATE TRIGGER trg_searchrack_history_insert_v3
        AFTER INSERT ON SEARCHRACK
        BEGIN
            INSERT INTO searchrack_history_outbox (
                event_id, event_type, captured_at, searchrack_id, barcode, title,
                from_position, to_position, old_quantity, new_quantity,
                source_row_json, result_row_json
            ) VALUES (
                lower(hex(randomblob(16))),
                CASE WHEN COALESCE(CAST({qty_new} AS INTEGER), 0) <= 0
                     THEN 'invalid_insert_cleanup' ELSE 'inventory_add' END,
                strftime('%Y-%m-%dT%H:%M:%f', 'now', 'localtime'),
                {row_id_new}, {barcode_new}, {title_new}, '', {insert_location},
                0, COALESCE(CAST({qty_new} AS INTEGER), 0),
                {new_snapshot}, {new_snapshot}
            );
            DELETE FROM SEARCHRACK
            WHERE rowid = NEW.rowid AND COALESCE(CAST({qty_new} AS INTEGER), 0) <= 0;
        END
    '''

    delete_location = _searchrack_trigger_location('OLD', pos_col, pic_col)
    delete_sql = f'''
        CREATE TRIGGER trg_searchrack_history_delete_v3
        AFTER DELETE ON SEARCHRACK
        WHEN NOT EXISTS (
            SELECT 1
            FROM searchrack_history_outbox
            WHERE searchrack_id = {row_id_old}
              AND processed_at IS NULL
              AND event_type IN (
                  'depletion', 'invalid_insert_cleanup',
                  'legacy_zero_cleanup', 'invalid_quantity_cleanup'
              )
              AND (
                  json(COALESCE(NULLIF(result_row_json, ''), '{{}}')) = json({old_snapshot})
                  OR (
                      event_type IN ('legacy_zero_cleanup', 'invalid_quantity_cleanup')
                      AND json(COALESCE(NULLIF(source_row_json, ''), '{{}}')) = json({old_snapshot})
                  )
              )
        )
        BEGIN
            INSERT INTO searchrack_history_outbox (
                event_id, event_type, captured_at, searchrack_id, barcode, title,
                from_position, to_position, old_quantity, new_quantity,
                source_row_json, result_row_json
            ) VALUES (
                lower(hex(randomblob(16))), 'inventory_delete',
                strftime('%Y-%m-%dT%H:%M:%f', 'now', 'localtime'),
                {row_id_old}, {barcode_old}, {title_old}, {delete_location}, '',
                COALESCE(CAST({qty_old} AS INTEGER), 0), 0, {old_snapshot}, NULL
            );
        END
    '''

    trigger_sql = [validate_insert_sql, validate_update_sql, update_sql, insert_sql, delete_sql]
    fingerprint = hashlib.sha256('\n'.join(trigger_sql).encode('utf-8')).hexdigest()
    current_names = {
        'trg_searchrack_quantity_validate_insert_v3',
        'trg_searchrack_quantity_validate_update_v3',
        'trg_searchrack_history_update_v3',
        'trg_searchrack_history_insert_v3',
        'trg_searchrack_history_delete_v3',
    }

    # Trigger replacement is transactional, so there is never an unguarded write window.
    cur.execute('BEGIN IMMEDIATE')
    try:
        cur.execute("SELECT value FROM searchrack_history_guard_meta WHERE key = 'trigger_fingerprint'")
        row = cur.fetchone()
        saved_fingerprint = str(row[0] or '') if row else ''
        placeholders = ','.join('?' for _ in current_names)
        cur.execute(
            f"SELECT name FROM sqlite_master WHERE type = 'trigger' AND name IN ({placeholders})",
            tuple(sorted(current_names))
        )
        installed_names = {str(row[0]) for row in cur.fetchall()}

        if saved_fingerprint != fingerprint or installed_names != current_names:
            for version in ('v1', 'v2', 'v3'):
                for kind in ('update', 'insert', 'delete'):
                    cur.execute(
                        f'DROP TRIGGER IF EXISTS {ss_database._sqlite_ident(f"trg_searchrack_history_{kind}_{version}")}'
                    )
            for name in (
                'trg_searchrack_quantity_validate_insert_v3',
                'trg_searchrack_quantity_validate_update_v3',
            ):
                cur.execute(f'DROP TRIGGER IF EXISTS {ss_database._sqlite_ident(name)}')
            for sql in trigger_sql:
                cur.execute(sql)
            cur.execute('''
                INSERT INTO searchrack_history_guard_meta(key, value)
                VALUES ('trigger_fingerprint', ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value
            ''', (fingerprint,))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return {'installed': True, 'qty_col': qty_col, 'id_col': id_col, 'columns': columns}




def _ensure_searchrack_snapshot_columns(conn):
    """Ensure known inventory metadata is present before snapshot triggers are generated."""
    cur = conn.cursor()
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND LOWER(name)='searchrack'")
    if not cur.fetchone():
        return False
    cur.execute("PRAGMA table_info('SEARCHRACK')")
    existing = {str(row[1]).lower() for row in cur.fetchall()}
    migrations = {
        'created_at': 'ALTER TABLE SEARCHRACK ADD COLUMN CREATED_AT TEXT',
        'image': 'ALTER TABLE SEARCHRACK ADD COLUMN IMAGE TEXT',
        'custom_title': 'ALTER TABLE SEARCHRACK ADD COLUMN CUSTOM_TITLE INTEGER DEFAULT 0',
        'warehouse_note': 'ALTER TABLE SEARCHRACK ADD COLUMN WAREHOUSE_NOTE TEXT DEFAULT ""',
    }
    changed = False
    for column, sql in migrations.items():
        if column not in existing:
            cur.execute(sql)
            changed = True
    conn.commit()
    return changed


def _capture_legacy_zero_searchrack_rows(conn, guard_info):
    """Snapshot and remove pre-guard zero rows; normalize historical blank quantities to one."""
    qty_col = guard_info.get('qty_col')
    id_col = guard_info.get('id_col')
    columns = list(guard_info.get('columns') or [])
    if not qty_col or not columns:
        return {'normalized': 0, 'deleted': 0}

    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    _ensure_searchrack_history_outbox(cur)
    conn.commit()
    cur.execute('BEGIN IMMEDIATE')
    cur.execute('SELECT rowid AS _guard_rowid_, * FROM SEARCHRACK')
    rows = list(cur.fetchall())
    normalized = 0
    deleted = 0

    for row in rows:
        row_dict = dict(row)
        raw_qty = row_dict.get(qty_col)
        parsed_qty = ss_normalization._strict_inventory_quantity(raw_qty)
        rowid = row_dict.get('_guard_rowid_')
        searchrack_id = row_dict.get(id_col) if id_col else rowid

        cleanup_event_type = 'legacy_zero_cleanup'
        if parsed_qty is None:
            if raw_qty is None or str(raw_qty).strip() == '':
                cur.execute(
                    f'UPDATE SEARCHRACK SET {ss_database._sqlite_ident(qty_col)} = 1 WHERE rowid = ?',
                    (rowid,)
                )
                normalized += max(0, cur.rowcount)
                continue
            parsed_qty = 0
            cleanup_event_type = 'invalid_quantity_cleanup'
        if parsed_qty > 0:
            continue

        snapshot = {column: row_dict.get(column) for column in columns}
        event_id = uuid.uuid4().hex
        location = _searchrack_snapshot_location(snapshot)
        cur.execute('''
            INSERT OR IGNORE INTO searchrack_history_outbox (
                event_id, event_type, captured_at, searchrack_id, barcode, title,
                from_position, to_position, old_quantity, new_quantity,
                source_row_json, result_row_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, '', ?, 0, ?, NULL)
        ''', (
            event_id,
            cleanup_event_type,
            datetime.datetime.now().isoformat(),
            searchrack_id,
            str(_searchrack_snapshot_value(snapshot, 'barcode', 'upc') or '').strip(),
            str(_searchrack_snapshot_value(snapshot, 'title') or '').strip(),
            location,
            parsed_qty,
            json.dumps(snapshot, ensure_ascii=False, default=str)
        ))
        cur.execute(
            'DELETE FROM SEARCHRACK WHERE rowid = ?',
            (rowid,)
        )
        deleted += max(0, cur.rowcount)

    try:
        cur.execute('DELETE FROM zero_qty_pending_deletion')
    except sqlite3.Error:
        pass
    conn.commit()
    return {'normalized': normalized, 'deleted': deleted}


def _history_timestamp(value):
    text = str(value or '').strip()
    if not text:
        return None
    try:
        parsed = datetime.datetime.fromisoformat(text.replace('Z', '+00:00'))
        if parsed.tzinfo is not None:
            parsed = parsed.astimezone().replace(tzinfo=None)
        return parsed
    except Exception:
        return None


def _history_event_candidate(rem_cur, event):
    searchrack_id = event.get('searchrack_id')
    if searchrack_id is None:
        return None
    rem_cur.execute('''
        SELECT *
        FROM removed_items
        WHERE searchrack_id = ?
          AND COALESCE(event_id, '') = ''
          AND COALESCE(event_status, 'applied') != 'superseded'
        ORDER BY id DESC
        LIMIT 30
    ''', (searchrack_id,))
    candidates = [dict(row) for row in rem_cur.fetchall()]
    if not candidates:
        return None

    event_time = _history_timestamp(event.get('captured_at'))
    event_barcode = str(event.get('barcode') or '').strip().lower()
    event_from = str(event.get('from_position') or '').strip().lower()
    event_to = str(event.get('to_position') or '').strip().lower()
    event_old = ss_normalization._coerce_int(event.get('old_quantity'), 0)
    event_new = ss_normalization._coerce_int(event.get('new_quantity'), 0)
    event_type = str(event.get('event_type') or '')
    best = None
    best_score = -1

    for candidate in candidates:
        candidate_time = _history_timestamp(candidate.get('removed_at'))
        if event_time is not None and candidate_time is not None:
            seconds = abs((event_time - candidate_time).total_seconds())
            if seconds > 600:
                continue
            time_score = 5 if seconds <= 120 else 2
        else:
            continue

        score = time_score
        candidate_old = ss_normalization._coerce_int(candidate.get('old_quantity'), 0)
        candidate_new = ss_normalization._coerce_int(candidate.get('new_quantity'), 0)
        if candidate_old == event_old and candidate_new == event_new:
            score += 8
        elif event_type == 'location_change':
            score += 2

        candidate_barcode = str(candidate.get('barcode') or '').strip().lower()
        if event_barcode and candidate_barcode == event_barcode:
            score += 3

        candidate_position = str(candidate.get('item_position') or '').strip().lower()
        if candidate_position and candidate_position in {event_from, event_to}:
            score += 3

        removal_type = str(candidate.get('removal_type') or '').lower()
        if event_type == 'location_change' and ('location' in removal_type or 'shelf' in removal_type):
            score += 4
        if event_type == 'inventory_add' and candidate_old == 0 and candidate_new == event_new:
            score += 4

        if score > best_score:
            best = candidate
            best_score = score

    return best if best_score >= 8 else None


_searchrack_history_flush_lock = threading.Lock()


def _flush_searchrack_history_outbox(limit=250, settle_seconds=0):
    """Mirror durable SEARCHRACK events into rackhistory.db; retries are idempotent by event_id."""
    if not _searchrack_history_flush_lock.acquire(blocking=False):
        return 0
    rack_conn = None
    rem_conn = None
    processed = 0
    try:
        rack_conn = sqlite3.connect(str(ss_config.BASE_DIR / 'searchRack.db'), timeout=30.0)
        rack_conn.row_factory = sqlite3.Row
        rack_cur = rack_conn.cursor()
        _ensure_searchrack_history_outbox(rack_cur)
        rack_cur.execute('''
            SELECT * FROM searchrack_history_outbox
            WHERE processed_at IS NULL
            ORDER BY CASE WHEN COALESCE(last_error, '') = '' THEN 0 ELSE 1 END, id
            LIMIT ?
        ''', (max(1, int(limit or 250)),))
        events = [dict(row) for row in rack_cur.fetchall()]
        settle_seconds = max(0.0, float(settle_seconds or 0))
        if settle_seconds:
            cutoff = datetime.datetime.now() - datetime.timedelta(seconds=settle_seconds)
            settled_events = []
            for event in events:
                captured_at = _history_timestamp(event.get('captured_at'))
                if captured_at is not None and captured_at > cutoff:
                    break
                settled_events.append(event)
            events = settled_events
        if not events:
            rack_conn.commit()
            return 0

        rem_conn = sqlite3.connect(str(ss_config.BASE_DIR / 'rackhistory.db'), timeout=30.0)
        rem_conn.row_factory = sqlite3.Row
        rem_cur = rem_conn.cursor()
        _ensure_removed_items_table(rem_cur)
        rem_conn.commit()

        type_map = {
            'legacy_zero_cleanup': 'legacy_zero_cleanup',
            'invalid_quantity_cleanup': 'invalid_quantity_cleanup',
            'invalid_insert_cleanup': 'invalid_zero_insert',
            'depletion': 'inventory_depleted',
            'quantity_change': 'quantity_adjustment',
            'location_change': 'location_change',
            'inventory_add': 'inventory_added',
            'inventory_delete': 'inventory_deleted',
        }

        processed_marks = []
        failed_event = None
        event_savepoint_active = False
        for event in events:
            failed_event = event
            rem_cur.execute('SAVEPOINT searchrack_history_event')
            event_savepoint_active = True
            event_id = str(event.get('event_id') or '').strip()
            if not event_id:
                raise ValueError('Rack-history outbox event has no event_id')
            rem_cur.execute('SELECT id FROM removed_items WHERE event_id = ?', (event_id,))
            existing_event = rem_cur.fetchone()
            if existing_event:
                history_id = existing_event['id']
                rem_cur.execute('''
                    UPDATE removed_items
                    SET source_row_json = COALESCE(NULLIF(source_row_json, ''), ?),
                        result_row_json = COALESCE(NULLIF(result_row_json, ''), ?),
                        from_position = COALESCE(NULLIF(from_position, ''), ?),
                        to_position = COALESCE(NULLIF(to_position, ''), ?),
                        event_status = 'applied',
                        applied_at = COALESCE(NULLIF(applied_at, ''), ?)
                    WHERE id = ?
                ''', (
                    event.get('source_row_json'),
                    event.get('result_row_json'),
                    str(event.get('from_position') or '').strip(),
                    str(event.get('to_position') or '').strip(),
                    str(event.get('captured_at') or datetime.datetime.now().isoformat()),
                    history_id,
                ))
            else:
                candidate = _history_event_candidate(rem_cur, event)
                old_qty = ss_normalization._coerce_int(event.get('old_quantity'), 0)
                new_qty = ss_normalization._coerce_int(event.get('new_quantity'), 0)
                event_type = str(event.get('event_type') or '')
                actual_delta = abs(new_qty - old_qty)
                if event_type == 'location_change':
                    actual_delta = 0
                deleted = 1 if event_type in (
                    'legacy_zero_cleanup', 'invalid_quantity_cleanup',
                    'invalid_insert_cleanup', 'depletion', 'inventory_delete'
                ) else 0
                from_position = str(event.get('from_position') or '').strip()
                to_position = str(event.get('to_position') or '').strip()
                effective_position = from_position or to_position
                applied_at = str(event.get('captured_at') or datetime.datetime.now().isoformat())

                if candidate:
                    history_id = candidate['id']
                    if event_type == 'location_change':
                        rem_cur.execute('''
                            UPDATE removed_items
                            SET event_status = 'superseded'
                            WHERE id != ?
                              AND searchrack_id = ?
                              AND COALESCE(removed_at, '') = COALESCE(?, '')
                              AND COALESCE(removal_type, '') = COALESCE(?, '')
                              AND COALESCE(barcode, '') = COALESCE(?, '')
                              AND COALESCE(event_id, '') = ''
                        ''', (
                            history_id,
                            event.get('searchrack_id'),
                            candidate.get('removed_at'),
                            candidate.get('removal_type'),
                            candidate.get('barcode'),
                        ))
                    rem_cur.execute('''
                        UPDATE removed_items
                        SET event_id = ?, barcode = COALESCE(NULLIF(barcode, ''), ?),
                            title = COALESCE(NULLIF(title, ''), ?), quantity_removed = ?,
                            old_quantity = ?, new_quantity = ?,
                            item_position = COALESCE(NULLIF(item_position, ''), ?),
                            source_row_json = ?, result_row_json = ?,
                            from_position = ?, to_position = ?,
                            inventory_row_deleted = ?, event_status = 'applied', applied_at = ?
                        WHERE id = ?
                    ''', (
                        event_id,
                        str(event.get('barcode') or '').strip(),
                        str(event.get('title') or '').strip(),
                        actual_delta,
                        old_qty,
                        new_qty,
                        effective_position,
                        event.get('source_row_json'),
                        event.get('result_row_json'),
                        from_position,
                        to_position,
                        deleted,
                        applied_at,
                        history_id,
                    ))
                else:
                    rem_cur.execute('''
                        INSERT INTO removed_items (
                            order_id, barcode, title, quantity_removed, removed_at,
                            searchrack_id, old_quantity, new_quantity, removal_type,
                            item_position, event_id, source_row_json, result_row_json,
                            from_position, to_position, inventory_row_deleted,
                            event_status, applied_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'applied', ?)
                    ''', (
                        None,
                        str(event.get('barcode') or '').strip(),
                        str(event.get('title') or '').strip(),
                        actual_delta,
                        applied_at,
                        event.get('searchrack_id'),
                        old_qty,
                        new_qty,
                        type_map.get(event_type, event_type or 'inventory_change'),
                        effective_position,
                        event_id,
                        event.get('source_row_json'),
                        event.get('result_row_json'),
                        from_position,
                        to_position,
                        deleted,
                        applied_at,
                    ))
                    history_id = rem_cur.lastrowid

            rem_cur.execute('RELEASE SAVEPOINT searchrack_history_event')
            event_savepoint_active = False
            processed_marks.append((history_id, event['id']))

        # One history fsync and one outbox fsync per batch keeps this inexpensive on the Pi.
        rem_conn.commit()
        processed_at = datetime.datetime.now().isoformat()
        rack_cur.executemany('''
            UPDATE searchrack_history_outbox
            SET processed_at = ?, history_id = ?, last_error = NULL
            WHERE id = ? AND processed_at IS NULL
        ''', [(processed_at, history_id, event_id) for history_id, event_id in processed_marks])
        rack_conn.commit()
        processed = len(processed_marks)
        return processed
    except Exception as exc:
        # Preserve earlier good events in the batch and move one bad event behind fresh work.
        if event_savepoint_active and rem_conn is not None and rack_conn is not None:
            try:
                rem_cur.execute('ROLLBACK TO SAVEPOINT searchrack_history_event')
                rem_cur.execute('RELEASE SAVEPOINT searchrack_history_event')
                rem_conn.commit()
                processed_at = datetime.datetime.now().isoformat()
                if processed_marks:
                    rack_cur.executemany('''
                        UPDATE searchrack_history_outbox
                        SET processed_at = ?, history_id = ?, last_error = NULL
                        WHERE id = ? AND processed_at IS NULL
                    ''', [
                        (processed_at, history_id, outbox_id)
                        for history_id, outbox_id in processed_marks
                    ])
                if failed_event is not None:
                    rack_cur.execute('''
                        UPDATE searchrack_history_outbox
                        SET last_error = ?
                        WHERE id = ? AND processed_at IS NULL
                    ''', (str(exc)[:1000], failed_event.get('id')))
                rack_conn.commit()
                processed = len(processed_marks)
                print(f'Warning: one rack-history event was deferred without blocking the batch: {exc}')
                return processed
            except Exception:
                try:
                    rem_conn.rollback()
                except Exception:
                    pass
                try:
                    rack_conn.rollback()
                except Exception:
                    pass
        try:
            if rem_conn is not None:
                rem_conn.rollback()
        except Exception:
            pass
        try:
            if rack_conn is not None:
                rack_conn.rollback()
        except Exception:
            pass
        print(f'Warning: rack-history outbox flush deferred: {exc}')
        return processed
    finally:
        if rem_conn is not None:
            rem_conn.close()
        if rack_conn is not None:
            rack_conn.close()
        _searchrack_history_flush_lock.release()


def _initialize_searchrack_history_guard():
    conn = None
    try:
        conn = sqlite3.connect(str(ss_config.BASE_DIR / 'searchRack.db'), timeout=30.0)
        _ensure_searchrack_snapshot_columns(conn)
        guard = _install_searchrack_history_guard(conn)
        if not guard.get('installed'):
            return guard
        cleanup = _capture_legacy_zero_searchrack_rows(conn, guard)
        with sqlite3.connect(str(ss_config.BASE_DIR / 'rackhistory.db'), timeout=30.0) as history_conn:
            _ensure_removed_items_table(history_conn.cursor())
        mirrored = _flush_searchrack_history_outbox()
        age_summary = ss_inventory_age._reconcile_inventory_age_batches(conn)
        if cleanup.get('normalized') or cleanup.get('deleted'):
            try:
                ss_caching._invalidate_searchrack_cache()
            except Exception:
                pass
        return {**guard, **cleanup, 'mirrored': mirrored, 'inventory_age': age_summary}
    finally:
        if conn is not None:
            conn.close()


def _inventory_age_lookup(conn, searchrack_ids):
    ids = sorted({
        int(value) for value in (searchrack_ids or [])
        if value not in (None, '') and str(value).lstrip('-').isdigit()
    })
    if not ids:
        return {}
    placeholders = ','.join('?' for _ in ids)
    cur = conn.cursor()
    cur.execute(f'''
        SELECT searchrack_id, received_at, quantity, estimated
        FROM inventory_age_batches
        WHERE searchrack_id IN ({placeholders}) AND quantity > 0
        ORDER BY received_at, id
    ''', tuple(ids))
    result = {row_id: [] for row_id in ids}
    for row_id, received_at, quantity, estimated in cur.fetchall():
        result.setdefault(int(row_id), []).append({
            'received_at': str(received_at or ''),
            'quantity': max(0, ss_normalization._coerce_int(quantity, 0)),
            'estimated': bool(estimated),
        })
    return result


def _history_restore_quantity(history_row):
    """Derive an undo amount from authoritative history, never from browser state."""
    history = dict(history_row) if not isinstance(history_row, dict) else dict(history_row)
    quantity_removed = max(0, ss_normalization._coerce_int(history.get('quantity_removed'), 0))
    old_quantity = ss_normalization._coerce_int(history.get('old_quantity'), 0)
    new_quantity = ss_normalization._coerce_int(history.get('new_quantity'), 0)
    return max(quantity_removed, max(0, old_quantity - new_quantity))


def _restore_searchrack_from_history(rack_cur, history_row, restore_quantity):
    """Restore only to the archived barcode/location identity, recreating the row if needed."""
    restore_quantity = max(0, ss_normalization._coerce_int(restore_quantity, 0))
    if restore_quantity <= 0:
        raise ValueError('Restore quantity must be positive')

    history = dict(history_row) if not isinstance(history_row, dict) else dict(history_row)
    try:
        snapshot = json.loads(history.get('source_row_json') or '{}')
        if not isinstance(snapshot, dict):
            snapshot = {}
    except Exception:
        snapshot = {}

    rack_cur.execute("PRAGMA table_info('SEARCHRACK')")
    columns = [row[1] for row in rack_cur.fetchall()]
    lower = {str(column).lower(): column for column in columns}
    id_col = lower.get('id')
    qty_col = lower.get('quantity') or lower.get('qty')
    barcode_col = lower.get('barcode') or lower.get('upc')
    title_col = lower.get('title')
    pos_col = lower.get('item_position') or lower.get('itemposition') or lower.get('position')
    pic_col = lower.get('pictureposition') or lower.get('picture_position')
    if not qty_col:
        raise RuntimeError('SEARCHRACK quantity column not found')

    snapshot_folded = _row_casefold_dict(snapshot)
    snapshot_barcode = snapshot_folded.get(str(barcode_col).lower()) if barcode_col else ''
    barcode = str(snapshot_barcode or '').strip()
    if not barcode:
        barcode = str(history.get('barcode') or '').strip()
    source_location = _searchrack_snapshot_location(snapshot) or str(
        history.get('from_position') or history.get('item_position') or ''
    ).strip()
    barcode_key = barcode.casefold()
    location_key = source_location.casefold()
    sid = history.get('searchrack_id')
    id_expr = ss_database._sqlite_ident(id_col) if id_col else 'rowid'
    sid_reusable = sid not in (None, '')

    if sid_reusable:
        rack_cur.execute(
            f'SELECT rowid AS _rowid_, * FROM SEARCHRACK WHERE {id_expr} = ?',
            (sid,)
        )
        existing = rack_cur.fetchone()
        if existing:
            existing_dict = dict(existing)
            existing_barcode = str(
                _searchrack_snapshot_value(existing_dict, barcode_col or 'barcode', 'barcode', 'upc') or ''
            ).strip().casefold()
            existing_location = _searchrack_snapshot_location(existing_dict).casefold()
            if barcode_key and existing_barcode == barcode_key and existing_location == location_key:
                current_qty = max(0, ss_normalization._coerce_int(existing_dict.get(qty_col), 0))
                new_qty = current_qty + restore_quantity
                rack_cur.execute(
                    f'UPDATE SEARCHRACK SET {ss_database._sqlite_ident(qty_col)} = ? WHERE {id_expr} = ?',
                    (new_qty, sid)
                )
                return {
                    'searchrack_id': existing_dict.get(id_col) if id_col else existing_dict.get('_rowid_'),
                    'old_quantity': current_qty,
                    'new_quantity': new_qty,
                    'recreated': False,
                }
            # That numeric ID now belongs to another inventory identity; never overwrite it.
            sid_reusable = False

    if barcode_col and barcode:
        rack_cur.execute(
            f'''SELECT rowid AS _rowid_, * FROM SEARCHRACK
                WHERE {ss_database._sqlite_ident(barcode_col)} = ? COLLATE NOCASE
                  AND COALESCE(CAST({ss_database._sqlite_ident(qty_col)} AS INTEGER), 0) > 0''',
            (barcode,)
        )
        for row in rack_cur.fetchall():
            row_dict = dict(row)
            if _searchrack_snapshot_location(row_dict).casefold() != location_key:
                continue
            row_id = row_dict.get(id_col) if id_col else row_dict.get('_rowid_')
            current_qty = max(0, ss_normalization._coerce_int(row_dict.get(qty_col), 0))
            new_qty = current_qty + restore_quantity
            rack_cur.execute(
                f'UPDATE SEARCHRACK SET {ss_database._sqlite_ident(qty_col)} = ? WHERE {id_expr} = ?',
                (new_qty, row_id)
            )
            return {
                'searchrack_id': row_id, 'old_quantity': current_qty,
                'new_quantity': new_qty, 'recreated': False
            }

    values_by_column = {}
    for column in columns:
        if id_col and column.lower() == id_col.lower():
            continue
        values_by_column[column] = snapshot_folded.get(column.lower())
    values_by_column[qty_col] = restore_quantity
    if barcode_col:
        values_by_column[barcode_col] = barcode
    if title_col and not values_by_column.get(title_col):
        values_by_column[title_col] = str(history.get('title') or '').strip()
    if source_location and pos_col and not values_by_column.get(pos_col):
        values_by_column[pos_col] = source_location
    if pic_col and str(values_by_column.get(pos_col) or '').strip().lower() != 'picture':
        values_by_column[pic_col] = values_by_column.get(pic_col) or ''

    insert_columns = list(values_by_column.keys())
    insert_values = [values_by_column[column] for column in insert_columns]
    if id_col and sid_reusable:
        insert_columns.insert(0, id_col)
        insert_values.insert(0, sid)
    placeholders = ','.join('?' for _ in insert_columns)
    rack_cur.execute(
        f'''INSERT INTO SEARCHRACK ({', '.join(ss_database._sqlite_ident(c) for c in insert_columns)})
            VALUES ({placeholders})''',
        tuple(insert_values)
    )
    new_id = sid if id_col and sid_reusable else rack_cur.lastrowid
    return {
        'searchrack_id': new_id, 'old_quantity': 0,
        'new_quantity': restore_quantity, 'recreated': True
    }


def _restore_searchrack_with_undo_claim(
    rack_cur, history_row, restore_quantity, *, history_source='removed_items'
):
    """Restore once; the claim and stock mutation share the SEARCHRACK transaction."""
    history = dict(history_row) if not isinstance(history_row, dict) else dict(history_row)
    history_id = ss_normalization._coerce_int(history.get('id'), 0)
    if history_id <= 0:
        raise ValueError('History event has no stable ID')
    restore_quantity = max(0, ss_normalization._coerce_int(restore_quantity, 0))
    if restore_quantity <= 0:
        raise ValueError('History event has no positive quantity to restore')

    _ensure_searchrack_undo_claims(rack_cur)
    rack_cur.execute('''
        SELECT searchrack_id, restore_quantity, old_quantity, new_quantity, recreated,
               outbox_event_id
        FROM searchrack_undo_claims
        WHERE history_source = ? AND history_id = ?
    ''', (history_source, history_id))
    claimed = rack_cur.fetchone()
    if claimed:
        return {
            'searchrack_id': claimed[0],
            'restore_quantity': claimed[1],
            'old_quantity': claimed[2],
            'new_quantity': claimed[3],
            'recreated': bool(claimed[4]),
            'outbox_event_id': str(claimed[5] or '').strip(),
            'already_claimed': True,
        }

    _ensure_searchrack_history_outbox(rack_cur)
    rack_cur.execute('SELECT COALESCE(MAX(id), 0) FROM searchrack_history_outbox')
    outbox_id_before = ss_normalization._coerce_int(rack_cur.fetchone()[0], 0)
    restored = _restore_searchrack_from_history(rack_cur, history, restore_quantity)
    rack_cur.execute('''
        SELECT event_id
        FROM searchrack_history_outbox
        WHERE id > ? AND searchrack_id = ?
        ORDER BY id DESC
        LIMIT 1
    ''', (outbox_id_before, restored['searchrack_id']))
    outbox_row = rack_cur.fetchone()
    outbox_event_id = str(outbox_row[0] or '').strip() if outbox_row else ''
    rack_cur.execute('''
        INSERT INTO searchrack_undo_claims (
            history_source, history_id, claimed_at, searchrack_id,
            restore_quantity, old_quantity, new_quantity, recreated, outbox_event_id
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    ''', (
        history_source,
        history_id,
        datetime.datetime.now().isoformat(),
        restored['searchrack_id'],
        restore_quantity,
        restored['old_quantity'],
        restored['new_quantity'],
        1 if restored['recreated'] else 0,
        outbox_event_id,
    ))
    restored['restore_quantity'] = restore_quantity
    restored['outbox_event_id'] = outbox_event_id
    restored['already_claimed'] = False
    return restored


def _insert_inventory_undo_history(
    history_cur, source_row, restored, removal_type, *, order_id=None, undone_at=None
):
    """Insert one idempotent forward audit row for a completed undo."""
    source = dict(source_row) if not isinstance(source_row, dict) else dict(source_row)
    history_id = ss_normalization._coerce_int(source.get('id'), 0)
    if history_id <= 0:
        raise ValueError('History event has no stable ID')
    undone_at = undone_at or datetime.datetime.now().isoformat()
    location = str(source.get('from_position') or source.get('item_position') or '').strip()
    event_id = str(restored.get('outbox_event_id') or '').strip() or f'undo:removed_items:{history_id}'
    history_cur.execute('''
        INSERT OR IGNORE INTO removed_items (
            order_id, barcode, title, quantity_removed, removed_at, searchrack_id,
            old_quantity, new_quantity, removal_type, item_position,
            event_id, from_position, to_position, inventory_row_deleted,
            event_status, applied_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '', ?, 0, 'applied', ?)
    ''', (
        order_id,
        str(source.get('barcode') or '').strip(),
        str(source.get('title') or '').strip(),
        max(0, ss_normalization._coerce_int(restored.get('restore_quantity'), 0)),
        undone_at,
        restored.get('searchrack_id'),
        restored.get('old_quantity'),
        restored.get('new_quantity'),
        removal_type,
        location,
        event_id,
        location,
        undone_at,
    ))
    history_cur.execute('''
        UPDATE removed_items
        SET order_id = ?, barcode = ?, title = ?, quantity_removed = ?,
            removed_at = ?, searchrack_id = ?, old_quantity = ?, new_quantity = ?,
            removal_type = ?, item_position = ?, from_position = '', to_position = ?,
            inventory_row_deleted = 0, event_status = 'applied', applied_at = ?
        WHERE event_id = ?
    ''', (
        order_id,
        str(source.get('barcode') or '').strip(),
        str(source.get('title') or '').strip(),
        max(0, ss_normalization._coerce_int(restored.get('restore_quantity'), 0)),
        undone_at,
        restored.get('searchrack_id'),
        restored.get('old_quantity'),
        restored.get('new_quantity'),
        removal_type,
        location,
        location,
        undone_at,
        event_id,
    ))


def _mark_zero_qty_for_deletion_external(item_id):
    """Legacy compatibility helper; the database trigger now deletes depleted rows immediately."""
    _clear_zero_qty_pending_deletions(item_id)


def _clear_zero_qty_pending_deletions(searchrack_id=None):
    """Remove obsolete delayed-deletion queue entries."""
    try:
        with ss_database.db_connection('searchRack.db') as _conn:
            _cur = _conn.cursor()
            _cur.execute('''
                CREATE TABLE IF NOT EXISTS zero_qty_pending_deletion (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    searchrack_id INTEGER,
                    marked_at TEXT,
                    delete_at TEXT,
                    deletion_cancelled INTEGER DEFAULT 0
                )
            ''')
            if searchrack_id is None:
                _cur.execute('DELETE FROM zero_qty_pending_deletion')
            else:
                _cur.execute('DELETE FROM zero_qty_pending_deletion WHERE searchrack_id = ?', (searchrack_id,))
            cleared = _cur.rowcount
            _conn.commit()
            return cleared
    except Exception:
        return 0


def _record_inventory_zero_transition(upc_key, prev_total_qty, current_total_qty, observed_at=None):
    """Persist >0 -> 0 transitions immediately on inventory writes."""
    invalid_upc_values = {'null', 'n/a', 'does not apply'}
    if not upc_key or upc_key in invalid_upc_values:
        return

    prev_total_qty = max(0, ss_normalization._coerce_int(prev_total_qty, 0))
    current_total_qty = max(0, ss_normalization._coerce_int(current_total_qty, 0))
    ts = observed_at or (datetime.datetime.utcnow().isoformat() + 'Z')

    try:
        ss_listing_alerts._ensure_listing_alerts_tables()
        with sqlite3.connect('listing_alerts.db') as conn:
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()
            cur.execute('''
                SELECT ever_in_inventory, last_qty, last_nonzero_at, zero_triggered_at
                FROM inventory_zero_tracker
                WHERE upc = ?
            ''', (upc_key,))
            row = cur.fetchone()

            if row is None:
                ever_in_inventory = 1 if (prev_total_qty > 0 or current_total_qty > 0) else 0
                last_nonzero_at = ts if (prev_total_qty > 0 or current_total_qty > 0) else None
                zero_triggered_at = ts if (prev_total_qty > 0 and current_total_qty == 0 and ever_in_inventory == 1) else None
                cur.execute('''
                    INSERT INTO inventory_zero_tracker (
                        upc, ever_in_inventory, last_qty, last_nonzero_at, zero_triggered_at, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ''', (
                    upc_key,
                    ever_in_inventory,
                    current_total_qty,
                    last_nonzero_at,
                    zero_triggered_at,
                    ts,
                    ts
                ))
            else:
                ever_in_inventory = ss_normalization._coerce_int(row['ever_in_inventory'], 0)
                if prev_total_qty > 0 or current_total_qty > 0:
                    ever_in_inventory = 1

                last_nonzero_at = row['last_nonzero_at']
                zero_triggered_at = row['zero_triggered_at']

                if current_total_qty > 0:
                    last_nonzero_at = ts
                    zero_triggered_at = None
                elif prev_total_qty > 0 and ever_in_inventory == 1:
                    zero_triggered_at = ts

                cur.execute('''
                    UPDATE inventory_zero_tracker
                    SET ever_in_inventory = ?,
                        last_qty = ?,
                        last_nonzero_at = ?,
                        zero_triggered_at = ?,
                        updated_at = ?
                    WHERE upc = ?
                ''', (
                    ever_in_inventory,
                    current_total_qty,
                    last_nonzero_at,
                    zero_triggered_at,
                    ts,
                    upc_key
                ))

            conn.commit()
    except Exception as e:
        print(f"Warning: failed to update inventory_zero_tracker from write event: {e}")
