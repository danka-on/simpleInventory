"""Inventory age for Sweet Shelves."""

import datetime
import json
import sqlite3
from . import (
    config as ss_config, database as ss_database, inventory_history as ss_inventory_history,
    normalization as ss_normalization,
)


def _ensure_inventory_age_schema(conn):
    """Create the persistent FIFO receiving-batch ledger in searchRack.db."""
    cur = conn.cursor()
    cur.execute('''
        CREATE TABLE IF NOT EXISTS inventory_age_batches (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            searchrack_id INTEGER NOT NULL,
            barcode TEXT,
            received_at TEXT NOT NULL,
            quantity INTEGER NOT NULL CHECK(quantity > 0),
            source TEXT NOT NULL DEFAULT 'inventory',
            source_history_id INTEGER,
            estimated INTEGER NOT NULL DEFAULT 0,
            lot_number TEXT NOT NULL DEFAULT '',
            evidence_source TEXT NOT NULL DEFAULT '',
            confidence TEXT NOT NULL DEFAULT 'low',
            confidence_score INTEGER NOT NULL DEFAULT 0,
            evidence_json TEXT NOT NULL DEFAULT '{}',
            rebuild_run_id TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    cur.execute("PRAGMA table_info('inventory_age_batches')")
    existing_columns = {
        str(row[1]).strip().casefold()
        for row in cur.fetchall()
    }
    migration_columns = {
        'lot_number': "TEXT NOT NULL DEFAULT ''",
        'evidence_source': "TEXT NOT NULL DEFAULT ''",
        'confidence': "TEXT NOT NULL DEFAULT 'low'",
        'confidence_score': 'INTEGER NOT NULL DEFAULT 0',
        'evidence_json': "TEXT NOT NULL DEFAULT '{}'",
        'rebuild_run_id': 'TEXT',
    }
    for column, declaration in migration_columns.items():
        if column not in existing_columns:
            cur.execute(
                f'ALTER TABLE inventory_age_batches '
                f'ADD COLUMN {ss_database._sqlite_ident(column)} {declaration}'
            )
    cur.execute('''
        CREATE TABLE IF NOT EXISTS inventory_age_meta (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
    ''')
    cur.execute('''
        CREATE TABLE IF NOT EXISTS inventory_age_rebuild_runs (
            run_id TEXT PRIMARY KEY,
            started_at TEXT NOT NULL,
            completed_at TEXT,
            input_digest TEXT NOT NULL,
            plan_digest TEXT NOT NULL,
            mode TEXT NOT NULL DEFAULT 'comprehensive',
            status TEXT NOT NULL,
            summary_json TEXT NOT NULL DEFAULT '{}',
            before_ledger_json TEXT NOT NULL DEFAULT '[]',
            after_ledger_json TEXT NOT NULL DEFAULT '[]'
        )
    ''')
    cur.execute('''
        CREATE INDEX IF NOT EXISTS idx_inventory_age_row_fifo
        ON inventory_age_batches(searchrack_id, received_at, id)
    ''')
    cur.execute('''
        CREATE UNIQUE INDEX IF NOT EXISTS idx_inventory_age_history_event
        ON inventory_age_batches(source_history_id)
        WHERE source_history_id IS NOT NULL
    ''')
    conn.commit()


def _inventory_age_received_at(value, fallback=None):
    text = str(value or '').strip()
    if text:
        try:
            parsed = datetime.datetime.fromisoformat(text.replace('Z', '+00:00'))
            if parsed.tzinfo is not None:
                parsed = parsed.astimezone().replace(tzinfo=None)
            return parsed.isoformat()
        except Exception:
            pass
    fallback_text = str(fallback or '').strip()
    if fallback_text:
        try:
            parsed = datetime.datetime.fromisoformat(fallback_text.replace('Z', '+00:00'))
            if parsed.tzinfo is not None:
                parsed = parsed.astimezone().replace(tzinfo=None)
            return parsed.isoformat()
        except Exception:
            pass
    return datetime.datetime.now().isoformat()


def _inventory_age_insert_batch(
    cur, searchrack_id, barcode, received_at, quantity, *,
    source='inventory', source_history_id=None, estimated=False,
    lot_number='', evidence_source='', confidence='low',
    confidence_score=0, evidence_json='{}', rebuild_run_id=None
):
    quantity = max(0, ss_normalization._coerce_int(quantity, 0))
    if quantity <= 0 or searchrack_id in (None, ''):
        return None
    cur.execute('''
        INSERT OR IGNORE INTO inventory_age_batches (
            searchrack_id, barcode, received_at, quantity,
            source, source_history_id, estimated, lot_number,
            evidence_source, confidence, confidence_score,
            evidence_json, rebuild_run_id
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ''', (
        int(searchrack_id),
        str(barcode or '').strip(),
        _inventory_age_received_at(received_at),
        quantity,
        str(source or 'inventory'),
        source_history_id,
        1 if estimated else 0,
        str(lot_number or '').strip(),
        str(evidence_source or '').strip(),
        str(confidence or 'low').strip().casefold(),
        max(0, min(100, ss_normalization._coerce_int(confidence_score, 0))),
        (
            json.dumps(evidence_json, sort_keys=True, separators=(',', ':'))
            if isinstance(evidence_json, (dict, list))
            else str(evidence_json or '{}')
        ),
        str(rebuild_run_id or '').strip() or None,
    ))
    return cur.lastrowid


def _inventory_age_consume_fifo(cur, searchrack_id, quantity):
    """Consume and return the oldest batches for one SEARCHRACK row."""
    remaining = max(0, ss_normalization._coerce_int(quantity, 0))
    consumed = []
    if remaining <= 0:
        return consumed
    cur.execute('''
        SELECT id, barcode, received_at, quantity, source, source_history_id,
               estimated, lot_number, evidence_source, confidence,
               confidence_score, evidence_json, rebuild_run_id
        FROM inventory_age_batches
        WHERE searchrack_id = ? AND quantity > 0
        ORDER BY received_at, id
    ''', (int(searchrack_id),))
    for row in cur.fetchall():
        if remaining <= 0:
            break
        (
            batch_id, barcode, received_at, batch_qty, source, history_id,
            estimated, lot_number, evidence_source, confidence,
            confidence_score, evidence_json, rebuild_run_id
        ) = row
        batch_qty = max(0, ss_normalization._coerce_int(batch_qty, 0))
        take = min(batch_qty, remaining)
        if take <= 0:
            continue
        consumed.append({
            'barcode': barcode,
            'received_at': received_at,
            'quantity': take,
            'source': source,
            'source_history_id': history_id,
            'estimated': bool(estimated),
            'lot_number': str(lot_number or ''),
            'evidence_source': str(evidence_source or ''),
            'confidence': str(confidence or 'low'),
            'confidence_score': max(0, ss_normalization._coerce_int(confidence_score, 0)),
            'evidence_json': str(evidence_json or '{}'),
            'rebuild_run_id': rebuild_run_id,
        })
        if take >= batch_qty:
            cur.execute('DELETE FROM inventory_age_batches WHERE id = ?', (batch_id,))
        else:
            cur.execute(
                'UPDATE inventory_age_batches SET quantity = quantity - ? WHERE id = ?',
                (take, batch_id)
            )
        remaining -= take
    return consumed


def _inventory_age_transfer_fifo(cur, source_row_id, destination_row_id, quantity, barcode=''):
    """Move the oldest units and retain their immutable received timestamps."""
    if int(source_row_id) == int(destination_row_id):
        return max(0, ss_normalization._coerce_int(quantity, 0))
    consumed = _inventory_age_consume_fifo(cur, source_row_id, quantity)
    moved = 0
    for batch in consumed:
        batch_qty = max(0, ss_normalization._coerce_int(batch.get('quantity'), 0))
        if batch_qty <= 0:
            continue
        _inventory_age_insert_batch(
            cur,
            destination_row_id,
            batch.get('barcode') or barcode,
            batch.get('received_at'),
            batch_qty,
            source='fifo_move',
            estimated=bool(batch.get('estimated')),
            lot_number=batch.get('lot_number'),
            evidence_source=batch.get('evidence_source'),
            confidence=batch.get('confidence'),
            confidence_score=batch.get('confidence_score'),
            evidence_json=batch.get('evidence_json'),
            rebuild_run_id=batch.get('rebuild_run_id'),
        )
        moved += batch_qty
    return moved


def _backfill_inventory_age_batches(conn):
    """Seed active inventory from add-to-shelf history, with explicit legacy estimates."""
    _ensure_inventory_age_schema(conn)
    cur = conn.cursor()
    cur.execute("SELECT value FROM inventory_age_meta WHERE key = 'backfill_v1'")
    if cur.fetchone():
        return {'backfilled': False, 'rows': 0, 'units': 0}

    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND LOWER(name)='searchrack'")
    if not cur.fetchone():
        cur.execute(
            "INSERT OR REPLACE INTO inventory_age_meta(key, value) VALUES ('backfill_v1', ?)",
            (datetime.datetime.now().isoformat(),)
        )
        conn.commit()
        return {'backfilled': True, 'rows': 0, 'units': 0}

    cur.execute("PRAGMA table_info('SEARCHRACK')")
    cols = {str(row[1]).lower(): row[1] for row in cur.fetchall()}
    id_col = cols.get('id')
    qty_col = cols.get('quantity') or cols.get('qty')
    barcode_col = cols.get('barcode') or cols.get('upc')
    created_col = cols.get('created_at')
    if not qty_col:
        return {'backfilled': False, 'rows': 0, 'units': 0}

    id_expr = ss_database._sqlite_ident(id_col) if id_col else 'rowid'
    barcode_expr = ss_database._sqlite_ident(barcode_col) if barcode_col else "''"
    created_expr = ss_database._sqlite_ident(created_col) if created_col else "''"
    cur.execute(f'''
        SELECT {id_expr} AS searchrack_id,
               {barcode_expr} AS barcode,
               COALESCE(CAST({ss_database._sqlite_ident(qty_col)} AS INTEGER), 0) AS quantity,
               {created_expr} AS created_at
        FROM SEARCHRACK
        WHERE COALESCE(CAST({ss_database._sqlite_ident(qty_col)} AS INTEGER), 0) > 0
    ''')
    active_rows = [dict(row) for row in cur.fetchall()]

    history_conn = None
    history_cur = None
    try:
        history_conn = sqlite3.connect(str(ss_config.BASE_DIR / 'rackhistory.db'), timeout=30.0)
        history_conn.row_factory = sqlite3.Row
        history_cur = history_conn.cursor()
        ss_inventory_history._ensure_removed_items_table(history_cur)
        history_conn.commit()
    except Exception:
        if history_conn is not None:
            history_conn.close()
        history_conn = None
        history_cur = None

    inserted_rows = 0
    inserted_units = 0
    cur.execute('BEGIN IMMEDIATE')
    try:
        for active in active_rows:
            row_id = active['searchrack_id']
            target_qty = max(0, ss_normalization._coerce_int(active['quantity'], 0))
            barcode = str(active.get('barcode') or '').strip()
            event_qty = 0
            if history_cur is not None:
                history_cur.execute('''
                    SELECT id, removed_at, old_quantity, new_quantity
                    FROM removed_items
                    WHERE searchrack_id = ?
                      AND removal_type = 'add_to_shelf'
                      AND COALESCE(new_quantity, 0) > COALESCE(old_quantity, 0)
                      AND (undone_at IS NULL OR undone_at = '')
                      AND COALESCE(event_status, 'applied') != 'superseded'
                    ORDER BY removed_at, id
                ''', (row_id,))
                for event in history_cur.fetchall():
                    delta = max(
                        0,
                        ss_normalization._coerce_int(event['new_quantity'], 0)
                        - ss_normalization._coerce_int(event['old_quantity'], 0)
                    )
                    if delta <= 0:
                        continue
                    _inventory_age_insert_batch(
                        cur, row_id, barcode, event['removed_at'], delta,
                        source='rack_history',
                        source_history_id=event['id'],
                        estimated=False,
                    )
                    event_qty += delta
                    inserted_rows += 1
                    inserted_units += delta

            if event_qty > target_qty:
                _inventory_age_consume_fifo(cur, row_id, event_qty - target_qty)
            elif event_qty < target_qty:
                missing = target_qty - event_qty
                _inventory_age_insert_batch(
                    cur,
                    row_id,
                    barcode,
                    active.get('created_at'),
                    missing,
                    source='legacy_backfill',
                    estimated=True,
                )
                inserted_rows += 1
                inserted_units += missing

        cur.execute(
            "INSERT OR REPLACE INTO inventory_age_meta(key, value) VALUES ('backfill_v1', ?)",
            (datetime.datetime.now().isoformat(),)
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        if history_conn is not None:
            history_conn.close()
    return {'backfilled': True, 'rows': inserted_rows, 'units': inserted_units}


def _reconcile_inventory_age_batches(conn):
    """Keep the batch ledger aligned with active quantities after every mutation path."""
    backfill = _backfill_inventory_age_batches(conn)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("PRAGMA table_info('SEARCHRACK')")
    cols = {str(row[1]).lower(): row[1] for row in cur.fetchall()}
    id_col = cols.get('id')
    qty_col = cols.get('quantity') or cols.get('qty')
    barcode_col = cols.get('barcode') or cols.get('upc')
    created_col = cols.get('created_at')
    if not qty_col:
        return {**backfill, 'added_units': 0, 'consumed_units': 0}

    id_expr = ss_database._sqlite_ident(id_col) if id_col else 'rowid'
    barcode_expr = ss_database._sqlite_ident(barcode_col) if barcode_col else "''"
    created_expr = ss_database._sqlite_ident(created_col) if created_col else "''"
    cur.execute(f'''
        SELECT {id_expr} AS searchrack_id,
               {barcode_expr} AS barcode,
               COALESCE(CAST({ss_database._sqlite_ident(qty_col)} AS INTEGER), 0) AS quantity,
               {created_expr} AS created_at
        FROM SEARCHRACK
        WHERE COALESCE(CAST({ss_database._sqlite_ident(qty_col)} AS INTEGER), 0) > 0
    ''')
    active = [dict(row) for row in cur.fetchall()]
    active_ids = {int(row['searchrack_id']) for row in active}
    added_units = 0
    consumed_units = 0

    cur.execute('BEGIN IMMEDIATE')
    try:
        for row in active:
            row_id = int(row['searchrack_id'])
            target = max(0, ss_normalization._coerce_int(row['quantity'], 0))
            cur.execute(
                'SELECT COALESCE(SUM(quantity), 0) FROM inventory_age_batches WHERE searchrack_id = ?',
                (row_id,)
            )
            ledger_qty = max(0, ss_normalization._coerce_int(cur.fetchone()[0], 0))
            if ledger_qty < target:
                delta = target - ledger_qty
                _inventory_age_insert_batch(
                    cur,
                    row_id,
                    row.get('barcode'),
                    row.get('created_at'),
                    delta,
                    source='quantity_reconcile',
                    estimated=False,
                )
                added_units += delta
            elif ledger_qty > target:
                delta = ledger_qty - target
                _inventory_age_consume_fifo(cur, row_id, delta)
                consumed_units += delta

        if active_ids:
            placeholders = ','.join('?' for _ in active_ids)
            cur.execute(
                f'DELETE FROM inventory_age_batches WHERE searchrack_id NOT IN ({placeholders})',
                tuple(sorted(active_ids))
            )
        else:
            cur.execute('DELETE FROM inventory_age_batches')
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return {**backfill, 'added_units': added_units, 'consumed_units': consumed_units}
