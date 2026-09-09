"""Fba schema for Sweet Shelves."""

import json
import re
import sqlite3
import time
from fba_inbound import FbaInboundValidationError
from . import listing_checks as ss_listing_checks


def _ensure_fba_prep_tables(cur):
    """Create the Amazon FBA prep ledger beside SEARCHRACK for atomic stock changes."""
    def add_column_if_missing(existing_columns, column_name, alter_sql):
        if column_name in existing_columns:
            return
        try:
            cur.execute(alter_sql)
        except sqlite3.OperationalError as exc:
            # A second request may have completed the same migration after this
            # request read PRAGMA table_info. Treat only that exact race as success.
            if 'duplicate column name' not in str(exc).lower():
                raise
        existing_columns.add(column_name)

    cur.execute('''
        CREATE TABLE IF NOT EXISTS fba_prep_sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_name TEXT NOT NULL,
            batch_name TEXT,
            shipment_id TEXT,
            destination_fc TEXT,
            prep_owner TEXT NOT NULL DEFAULT 'SELLER',
            label_owner TEXT NOT NULL DEFAULT 'SELLER',
            boxes_planned INTEGER,
            notes TEXT,
            working_locations_json TEXT NOT NULL DEFAULT '[]',
            items_json TEXT NOT NULL DEFAULT '[]',
            item_count INTEGER NOT NULL DEFAULT 0,
            total_units INTEGER NOT NULL DEFAULT 0,
            status TEXT NOT NULL DEFAULT 'open',
            completed_batch_id INTEGER,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            completed_at TEXT,
            deleted_at TEXT
        )
    ''')
    cur.execute("PRAGMA table_info('fba_prep_sessions')")
    session_columns = {str(row[1]).lower() for row in cur.fetchall()}
    add_column_if_missing(
        session_columns, 'working_locations_json',
        "ALTER TABLE fba_prep_sessions ADD COLUMN working_locations_json TEXT NOT NULL DEFAULT '[]'",
    )
    session_additions = {
        'rejected_items_json': "ALTER TABLE fba_prep_sessions ADD COLUMN rejected_items_json TEXT NOT NULL DEFAULT '[]'",
        'amazon_inbound_plan_id': "ALTER TABLE fba_prep_sessions ADD COLUMN amazon_inbound_plan_id TEXT",
        'amazon_stage': "ALTER TABLE fba_prep_sessions ADD COLUMN amazon_stage TEXT NOT NULL DEFAULT 'draft'",
        'amazon_state_json': "ALTER TABLE fba_prep_sessions ADD COLUMN amazon_state_json TEXT NOT NULL DEFAULT '{}'",
        'amazon_updated_at': "ALTER TABLE fba_prep_sessions ADD COLUMN amazon_updated_at TEXT",
        'count_revision': "ALTER TABLE fba_prep_sessions ADD COLUMN count_revision INTEGER NOT NULL DEFAULT 0",
    }
    for column_name, alter_sql in session_additions.items():
        add_column_if_missing(session_columns, column_name, alter_sql)
    cur.execute('CREATE INDEX IF NOT EXISTS idx_fba_prep_sessions_status_updated ON fba_prep_sessions(status, updated_at DESC, id DESC)')

    cur.execute('''
        CREATE TABLE IF NOT EXISTS fba_prep_batches (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            client_token TEXT,
            session_id INTEGER,
            batch_name TEXT NOT NULL,
            shipment_id TEXT,
            destination_fc TEXT,
            prep_owner TEXT NOT NULL DEFAULT 'SELLER',
            label_owner TEXT NOT NULL DEFAULT 'SELLER',
            boxes_planned INTEGER,
            notes TEXT,
            status TEXT NOT NULL DEFAULT 'completed',
            total_skus INTEGER NOT NULL DEFAULT 0,
            total_units INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            completed_at TEXT
        )
    ''')
    cur.execute("PRAGMA table_info('fba_prep_batches')")
    batch_columns = {str(row[1]).lower() for row in cur.fetchall()}
    add_column_if_missing(batch_columns, 'session_id', 'ALTER TABLE fba_prep_batches ADD COLUMN session_id INTEGER')
    add_column_if_missing(batch_columns, 'amazon_inbound_plan_id', 'ALTER TABLE fba_prep_batches ADD COLUMN amazon_inbound_plan_id TEXT')
    cur.execute('''
        CREATE UNIQUE INDEX IF NOT EXISTS uq_fba_prep_batches_client_token
        ON fba_prep_batches(client_token)
        WHERE client_token IS NOT NULL AND TRIM(client_token) != ''
    ''')
    cur.execute('CREATE INDEX IF NOT EXISTS idx_fba_prep_batches_created_at ON fba_prep_batches(created_at DESC, id DESC)')

    cur.execute('''
        CREATE TABLE IF NOT EXISTS fba_prep_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            batch_id INTEGER NOT NULL,
            item_index INTEGER NOT NULL DEFAULT 0,
            barcode TEXT NOT NULL,
            title TEXT,
            image TEXT,
            requested_quantity INTEGER NOT NULL,
            quantity_removed INTEGER NOT NULL,
            source_locations_json TEXT NOT NULL DEFAULT '[]',
            remaining_inventory_qty INTEGER NOT NULL DEFAULT 0,
            remaining_locations_json TEXT NOT NULL DEFAULT '[]',
            asin TEXT,
            seller_sku TEXT,
            fnsku TEXT,
            item_condition TEXT,
            prep_type TEXT,
            label_owner TEXT,
            expiration_date TEXT,
            box_number TEXT,
            notes TEXT,
            listed_ebay INTEGER NOT NULL DEFAULT 0,
            listed_amazon INTEGER NOT NULL DEFAULT 0,
            listing_links_json TEXT NOT NULL DEFAULT '[]',
            created_at TEXT NOT NULL,
            FOREIGN KEY(batch_id) REFERENCES fba_prep_batches(id)
        )
    ''')
    cur.execute('CREATE INDEX IF NOT EXISTS idx_fba_prep_items_batch ON fba_prep_items(batch_id, item_index, id)')
    cur.execute('CREATE INDEX IF NOT EXISTS idx_fba_prep_items_barcode ON fba_prep_items(barcode, created_at DESC)')
    cur.execute('CREATE INDEX IF NOT EXISTS idx_fba_prep_items_seller_sku ON fba_prep_items(seller_sku, created_at DESC)')
    cur.execute('CREATE INDEX IF NOT EXISTS idx_fba_prep_items_fnsku ON fba_prep_items(fnsku, created_at DESC)')

    cur.execute('''
        CREATE TABLE IF NOT EXISTS fba_listing_reviews (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            fba_item_id INTEGER NOT NULL UNIQUE,
            batch_id INTEGER NOT NULL,
            barcode TEXT NOT NULL,
            title TEXT,
            quantity_removed INTEGER NOT NULL,
            remaining_inventory_qty INTEGER NOT NULL DEFAULT 0,
            remaining_locations_json TEXT NOT NULL DEFAULT '[]',
            listed_ebay INTEGER NOT NULL DEFAULT 0,
            listed_amazon INTEGER NOT NULL DEFAULT 0,
            listing_links_json TEXT NOT NULL DEFAULT '[]',
            reasons_json TEXT NOT NULL DEFAULT '[]',
            status TEXT NOT NULL DEFAULT 'open',
            reviewer_note TEXT,
            created_at TEXT NOT NULL,
            reviewed_at TEXT,
            FOREIGN KEY(fba_item_id) REFERENCES fba_prep_items(id),
            FOREIGN KEY(batch_id) REFERENCES fba_prep_batches(id)
        )
    ''')
    cur.execute('CREATE INDEX IF NOT EXISTS idx_fba_listing_reviews_status ON fba_listing_reviews(status, created_at DESC, id DESC)')

    cur.execute('''
        CREATE TABLE IF NOT EXISTS fba_pack_scans (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id INTEGER NOT NULL,
            scan_token TEXT NOT NULL UNIQUE,
            barcode TEXT NOT NULL,
            msku TEXT NOT NULL,
            title TEXT,
            packing_group_id TEXT NOT NULL,
            box_id TEXT NOT NULL,
            source_location TEXT,
            source_searchrack_id INTEGER,
            inventory_removed INTEGER NOT NULL DEFAULT 0,
            label_printed INTEGER NOT NULL DEFAULT 0,
            scanned_at TEXT NOT NULL,
            FOREIGN KEY(session_id) REFERENCES fba_prep_sessions(id)
        )
    ''')
    cur.execute("PRAGMA table_info('fba_pack_scans')")
    pack_scan_columns = {str(row[1]).lower() for row in cur.fetchall()}
    add_column_if_missing(pack_scan_columns, 'client_id', 'ALTER TABLE fba_pack_scans ADD COLUMN client_id TEXT')
    add_column_if_missing(pack_scan_columns, 'operator_name', 'ALTER TABLE fba_pack_scans ADD COLUMN operator_name TEXT')
    cur.execute('CREATE INDEX IF NOT EXISTS idx_fba_pack_scans_session ON fba_pack_scans(session_id, id)')

    cur.execute('''
        CREATE TABLE IF NOT EXISTS fba_box_move_scans (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id INTEGER NOT NULL,
            move_token TEXT NOT NULL UNIQUE,
            pack_scan_id INTEGER NOT NULL,
            barcode TEXT NOT NULL,
            msku TEXT NOT NULL,
            from_box_id TEXT NOT NULL,
            to_box_id TEXT NOT NULL,
            client_id TEXT,
            operator_name TEXT,
            moved_at TEXT NOT NULL,
            FOREIGN KEY(session_id) REFERENCES fba_prep_sessions(id),
            FOREIGN KEY(pack_scan_id) REFERENCES fba_pack_scans(id)
        )
    ''')
    cur.execute('CREATE INDEX IF NOT EXISTS idx_fba_box_move_scans_session ON fba_box_move_scans(session_id, id)')

    cur.execute('''
        CREATE TABLE IF NOT EXISTS fba_count_scans (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id INTEGER NOT NULL,
            scan_token TEXT NOT NULL UNIQUE,
            barcode TEXT NOT NULL,
            barcode_key TEXT NOT NULL,
            client_id TEXT,
            operator_name TEXT,
            scanned_at TEXT NOT NULL,
            FOREIGN KEY(session_id) REFERENCES fba_prep_sessions(id)
        )
    ''')
    cur.execute('CREATE INDEX IF NOT EXISTS idx_fba_count_scans_session ON fba_count_scans(session_id, id)')

    cur.execute('''
        CREATE TABLE IF NOT EXISTS fba_session_workers (
            session_id INTEGER NOT NULL,
            client_id TEXT NOT NULL,
            operator_name TEXT,
            active_box_id TEXT,
            last_seen_at TEXT NOT NULL,
            last_seen_epoch REAL NOT NULL DEFAULT 0,
            PRIMARY KEY(session_id, client_id),
            FOREIGN KEY(session_id) REFERENCES fba_prep_sessions(id)
        )
    ''')
    cur.execute("PRAGMA table_info('fba_session_workers')")
    worker_columns = {str(row[1]).lower() for row in cur.fetchall()}
    add_column_if_missing(
        worker_columns, 'last_seen_epoch',
        'ALTER TABLE fba_session_workers ADD COLUMN last_seen_epoch REAL NOT NULL DEFAULT 0',
    )
    cur.execute('CREATE INDEX IF NOT EXISTS idx_fba_session_workers_seen ON fba_session_workers(session_id, last_seen_at DESC)')


def _fba_json_list(value):
    if isinstance(value, list):
        return value
    try:
        parsed = json.loads(str(value or '[]'))
        return parsed if isinstance(parsed, list) else []
    except Exception:
        return []


def _fba_json_dict(value):
    if isinstance(value, dict):
        return value
    try:
        parsed = json.loads(str(value or '{}'))
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        return {}


def _fba_trim(value, limit=250):
    return str(value or '').strip()[:max(1, int(limit or 250))]


def _fba_client_identity(data):
    data = data if isinstance(data, dict) else {}
    client_id = _fba_trim(data.get('client_id'), 64)
    if client_id and not re.fullmatch(r'[A-Za-z0-9._:-]{6,64}', client_id):
        raise FbaInboundValidationError('Invalid shared-session scanner ID')
    return client_id, _fba_trim(data.get('operator_name'), 80)


def _fba_touch_session_worker(cur, session_id, client_id, operator_name='', active_box_id=''):
    if not client_id:
        return
    cur.execute('''
        INSERT INTO fba_session_workers (
            session_id, client_id, operator_name, active_box_id, last_seen_at, last_seen_epoch
        ) VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(session_id, client_id) DO UPDATE SET
            operator_name = excluded.operator_name,
            active_box_id = excluded.active_box_id,
            last_seen_at = excluded.last_seen_at,
            last_seen_epoch = excluded.last_seen_epoch
    ''', (
        int(session_id), client_id, operator_name or None,
        _fba_trim(active_box_id, 40).upper() or None, ss_listing_checks._listagent_now_iso(), time.time(),
    ))



