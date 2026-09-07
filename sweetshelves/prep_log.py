"""Prep log for Sweet Shelves."""

import datetime
import json
import sqlite3
from flask import jsonify, request
from . import (
    caching as ss_caching, database as ss_database, errors as ss_errors, listing_checks as
    ss_listing_checks, listing_settings as ss_listing_settings, normalization as ss_normalization,
    prep_schema as ss_prep_schema, prep_undo as ss_prep_undo, warehouse_allocations as
    ss_warehouse_allocations,
)


def _preplog_init_tables(cur):
    # preplog.db might be created after startup; ensure WAL gets enabled.
    try:
        cur.execute('PRAGMA journal_mode=WAL')
    except Exception:
        pass

    cur.execute('''
        CREATE TABLE IF NOT EXISTS prep_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL,
            upc TEXT NOT NULL,
            base_upc TEXT,
            status TEXT NOT NULL,
            quantity INTEGER,
            note TEXT,
            reason TEXT,
            source TEXT,
            meta_json TEXT,
            undone INTEGER NOT NULL DEFAULT 0,
            undone_at TEXT,
            undo_error TEXT
        )
    ''')
    cur.execute('CREATE INDEX IF NOT EXISTS idx_prep_log_created_at ON prep_log(created_at, id)')
    cur.execute('CREATE INDEX IF NOT EXISTS idx_prep_log_upc_created_at ON prep_log(upc, created_at, id)')
    cur.execute('CREATE INDEX IF NOT EXISTS idx_prep_log_undone_created_at ON prep_log(undone, created_at, id)')


def _preplog_add_entry(*,
                       upc,
                       status,
                       quantity=1,
                       created_at=None,
                       base_upc=None,
                       note=None,
                       reason=None,
                       source='item-prep',
                       meta=None,
                       dedupe=False):
    upc = (upc or '').strip()
    if not upc:
        raise ValueError('upc is required')

    status = (status or '').strip().lower()
    if status not in ('good', 'bad', 'unchecked', 'return'):
        raise ValueError('status must be good/bad/unchecked/return')

    created_at = (created_at or ss_listing_checks._listagent_now_iso()).strip()
    base_upc = (base_upc or '').strip() or None
    note = (note or '').strip() or None
    reason = (reason or '').strip() or None
    source = (source or '').strip() or None

    meta_json = None
    try:
        if meta is not None:
            meta_json = json.dumps(meta, ensure_ascii=False)
    except Exception:
        meta_json = None

    try:
        quantity = int(quantity) if quantity is not None else None
    except Exception:
        quantity = None

    with ss_database.db_connection('preplog.db') as conn:
        cur = conn.cursor()
        _preplog_init_tables(cur)

        if dedupe:
            cur.execute('''
                SELECT id, meta_json
                FROM prep_log
                WHERE upc = ? COLLATE NOCASE
                  AND status = ?
                  AND undone = 0
                ORDER BY id DESC
                LIMIT 1
            ''', (upc, status))
            existing = cur.fetchone()
            if existing and (existing[0] is not None):
                rid = int(existing[0])
                existing_meta_json = existing[1] if len(existing) > 1 else None
                merged_meta_json = meta_json
                if existing_meta_json and meta_json:
                    try:
                        existing_meta = json.loads(existing_meta_json)
                        incoming_meta = json.loads(meta_json)
                        if isinstance(existing_meta, dict) and isinstance(incoming_meta, dict):
                            merged_meta = dict(existing_meta)
                            merged_meta.update(incoming_meta)
                            merged_meta_json = json.dumps(merged_meta, ensure_ascii=False)
                    except Exception:
                        merged_meta_json = meta_json
                cur.execute('''
                    UPDATE prep_log
                    SET created_at=?,
                        base_upc=COALESCE(?, base_upc),
                        quantity=COALESCE(?, quantity),
                        note=COALESCE(?, note),
                        reason=COALESCE(?, reason),
                        source=COALESCE(?, source),
                        meta_json=COALESCE(?, meta_json),
                        undo_error=NULL
                    WHERE id = ?
                ''', (created_at, base_upc, quantity, note, reason, source, merged_meta_json, rid))
                cur.execute('SELECT * FROM prep_log WHERE id = ? LIMIT 1', (rid,))
                return ss_listing_checks._listagent_row_to_dict(cur.fetchone())

        cur.execute('''
            INSERT INTO prep_log (
                created_at, upc, base_upc, status, quantity, note, reason, source, meta_json, undone
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0)
        ''', (created_at, upc, base_upc, status, quantity, note, reason, source, meta_json))
        rid = cur.lastrowid
        cur.execute('SELECT * FROM prep_log WHERE id = ? LIMIT 1', (rid,))
        return ss_listing_checks._listagent_row_to_dict(cur.fetchone())


def _preplog_recent(*, limit=200, include_undone=True):
    limit = int(limit or 200)
    limit = max(1, min(limit, 2000))
    with ss_database.db_connection('preplog.db') as conn:
        cur = conn.cursor()
        _preplog_init_tables(cur)

        sql = 'SELECT * FROM prep_log WHERE 1=1'
        params = []
        if not include_undone:
            sql += ' AND undone = 0'
        sql += ' ORDER BY created_at DESC, id DESC LIMIT ?'
        params.append(limit)

        cur.execute(sql, params)
        return [ss_listing_checks._listagent_row_to_dict(r) for r in cur.fetchall()]


def _preplog_mark_undone(log_id: int, *, undone_at=None, undo_error=None):
    log_id = int(log_id)
    undone_at = (undone_at or ss_listing_checks._listagent_now_iso()).strip()
    undo_error = (undo_error or '').strip() or None

    with ss_database.db_connection('preplog.db') as conn:
        cur = conn.cursor()
        _preplog_init_tables(cur)
        cur.execute('''
            UPDATE prep_log
            SET undone = 1,
                undone_at = ?,
                undo_error = ?
            WHERE id = ?
        ''', (undone_at, undo_error, log_id))
        cur.execute('SELECT * FROM prep_log WHERE id = ? LIMIT 1', (log_id,))
        return ss_listing_checks._listagent_row_to_dict(cur.fetchone())


def _preplog_sync_downstream_delete(*, upc, status, qty=1, lot_number='', base_upc=None):
    """Consume quantity from the newest matching prep-log rows without mutating live prep state.

    This keeps prep_log aligned when a downstream live row is deleted from prep history.
    Matching is exact on UPC + status and prefers exact LOT matches before lotless fallback rows.
    """
    upc_n = ss_normalization._normalize_upc_preserve_suffix_for_match(ss_normalization._normalize_upc(upc))
    status_n = (status or '').strip().lower()
    lot_n = ss_normalization._normalize_lot_number(lot_number)
    base_n = ss_normalization._strip_leading_zeros_numeric(ss_normalization._normalize_upc(base_upc)) if base_upc else ''
    try:
        remaining = int(qty or 1)
    except Exception:
        remaining = 1
    if remaining < 1:
        remaining = 1

    summary = {
        'requested_qty': remaining,
        'consumed_qty': 0,
        'remaining_qty': remaining,
        'matched_rows': 0,
        'reduced_rows': [],
        'undone_rows': []
    }
    if not upc_n or status_n not in ('good', 'bad', 'return'):
        return summary

    def _row_base_key(row):
        raw = ss_normalization._normalize_upc_preserve_suffix_for_match(ss_normalization._normalize_upc(row.get('base_upc')))
        if raw:
            return ss_normalization._strip_leading_zeros_numeric(raw)
        row_upc = ss_normalization._normalize_upc_preserve_suffix_for_match(ss_normalization._normalize_upc(row.get('upc')))
        if '-' in row_upc and row_upc.rsplit('-', 1)[-1].isdigit():
            return row_upc.split('-', 1)[0]
        return ss_normalization._strip_leading_zeros_numeric(row_upc)

    with ss_database.db_connection('preplog.db') as conn:
        cur = conn.cursor()
        _preplog_init_tables(cur)
        cur.execute('''
            SELECT *
            FROM prep_log
            WHERE upc = ? COLLATE NOCASE
              AND status = ?
              AND undone = 0
            ORDER BY created_at DESC, id DESC
        ''', (upc_n, status_n))
        candidate_rows = [_preplog_enrich_row(ss_listing_checks._listagent_row_to_dict(r)) for r in cur.fetchall()]

        exact_lot_rows = []
        lotless_fallback_rows = []
        for row in candidate_rows:
            row_base = _row_base_key(row)
            if base_n and row_base and row_base.lower() != base_n.lower():
                continue
            row_lot = ss_normalization._normalize_lot_number(row.get('lot_number'))
            if lot_n:
                if row_lot.lower() == lot_n.lower():
                    exact_lot_rows.append(row)
                elif not row_lot:
                    lotless_fallback_rows.append(row)
            else:
                if not row_lot:
                    exact_lot_rows.append(row)

        ordered_rows = exact_lot_rows + lotless_fallback_rows
        for row in ordered_rows:
            if remaining <= 0:
                break
            row_id = int(row.get('id'))
            try:
                row_qty = int(row.get('quantity') if row.get('quantity') is not None else 1)
            except Exception:
                row_qty = 1
            if row_qty < 1:
                row_qty = 1
            take_qty = min(remaining, row_qty)
            summary['matched_rows'] += 1
            summary['consumed_qty'] += take_qty
            remaining -= take_qty

            if take_qty >= row_qty:
                cur.execute('''
                    UPDATE prep_log
                    SET undone = 1,
                        undone_at = ?,
                        undo_error = NULL
                    WHERE id = ?
                ''', (ss_listing_checks._listagent_now_iso(), row_id))
                summary['undone_rows'].append(row_id)
            else:
                new_qty = row_qty - take_qty
                cur.execute('''
                    UPDATE prep_log
                    SET quantity = ?,
                        undo_error = NULL
                    WHERE id = ?
                ''', (new_qty, row_id))
                summary['reduced_rows'].append({
                    'id': row_id,
                    'from_qty': row_qty,
                    'to_qty': new_qty
                })

    summary['remaining_qty'] = max(0, remaining)
    return summary


def _preplog_set_undo_error(log_id: int, error: str):
    log_id = int(log_id)
    error = (error or '').strip() or None
    with ss_database.db_connection('preplog.db') as conn:
        cur = conn.cursor()
        _preplog_init_tables(cur)
        cur.execute('UPDATE prep_log SET undo_error = ? WHERE id = ?', (error, log_id))
        cur.execute('SELECT * FROM prep_log WHERE id = ? LIMIT 1', (log_id,))
        return ss_listing_checks._listagent_row_to_dict(cur.fetchone())


def _preplog_meta_dict(entry):
    if not isinstance(entry, dict):
        return {}
    raw = entry.get('meta_json')
    if raw is None:
        return {}
    if isinstance(raw, dict):
        return raw
    try:
        parsed = json.loads(str(raw))
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        return {}


def _listinglog_meta_dict(entry):
    if not isinstance(entry, dict):
        return {}
    raw = entry.get('meta_json')
    if raw is None:
        return {}
    if isinstance(raw, dict):
        return raw
    try:
        parsed = json.loads(str(raw))
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        return {}


def _preplog_enrich_row(entry):
    row = dict(entry or {})
    meta = _preplog_meta_dict(row)
    lot_number = ss_normalization._normalize_lot_number(
        meta.get('lot_number') or
        meta.get('assigned_lot') or
        meta.get('lot')
    )
    requested_lot = ss_normalization._normalize_lot_number(meta.get('requested_lot') or meta.get('requested'))

    auto_assigned = meta.get('auto_assigned')
    if isinstance(auto_assigned, str):
        auto_assigned = auto_assigned.strip().lower() in ('1', 'true', 'yes', 'y')
    else:
        auto_assigned = bool(auto_assigned)

    if not auto_assigned and lot_number:
        if not requested_lot:
            auto_assigned = bool(meta.get('auto_assign_reason'))
        elif requested_lot.lower() != lot_number.lower():
            auto_assigned = True

    row['meta'] = meta
    row['lot_number'] = lot_number
    row['requested_lot'] = requested_lot
    row['auto_assigned'] = bool(auto_assigned)
    return row


def _preplog_update_meta(log_id: int, meta: dict):
    log_id = int(log_id)
    payload = {}
    if isinstance(meta, dict):
        payload = meta
    meta_json = json.dumps(payload, ensure_ascii=False)
    with ss_database.db_connection('preplog.db') as conn:
        cur = conn.cursor()
        _preplog_init_tables(cur)
        cur.execute('UPDATE prep_log SET meta_json = ? WHERE id = ?', (meta_json, log_id))
        cur.execute('SELECT * FROM prep_log WHERE id = ? LIMIT 1', (log_id,))
        row = cur.fetchone()
        return _preplog_enrich_row(ss_listing_checks._listagent_row_to_dict(row))


def _preplog_auto_assigned_entries(*, include_undone=False, lot_number='', limit=None):
    lot_filter = ss_normalization._normalize_lot_number(lot_number)
    with ss_database.db_connection('preplog.db') as conn:
        cur = conn.cursor()
        _preplog_init_tables(cur)
        sql = 'SELECT * FROM prep_log WHERE 1=1'
        params = []
        if not include_undone:
            sql += ' AND undone = 0'
        sql += ' ORDER BY created_at DESC, id DESC'
        if limit is not None:
            try:
                lim = int(limit)
            except Exception:
                lim = 500
            lim = max(1, min(lim, 5000))
            sql += ' LIMIT ?'
            params.append(lim)
        cur.execute(sql, params)
        rows = [_preplog_enrich_row(ss_listing_checks._listagent_row_to_dict(r)) for r in cur.fetchall()]

    filtered = []
    for row in rows:
        if not row.get('auto_assigned'):
            continue
        if lot_filter and ss_normalization._normalize_lot_number(row.get('lot_number')).lower() != lot_filter.lower():
            continue
        filtered.append(row)
    return filtered


def _preplog_attach_voice_notes(items):
    """Attach voice_notes list to each prep-log row from items_prep_media in bol.db."""
    rows = [dict(it or {}) for it in (items or [])]
    if not rows:
        return rows
    all_upcs = list({str(row.get('upc') or '').strip() for row in rows if row.get('upc')})
    voice_by_upc = {}
    if all_upcs:
        try:
            with ss_database.db_connection('bol.db') as conn:
                conn.row_factory = sqlite3.Row
                cur = conn.cursor()
                for i in range(0, len(all_upcs), 350):
                    chunk = all_upcs[i:i + 350]
                    placeholders = ','.join('?' * len(chunk))
                    cur.execute(
                        f"SELECT upc, id, file_path, mime_type, created_at FROM items_prep_media "
                        f"WHERE upc IN ({placeholders}) COLLATE NOCASE AND COALESCE(media_type,'') = 'audio' "
                        f"ORDER BY created_at DESC, id DESC",
                        tuple(chunk)
                    )
                    for vr in cur.fetchall():
                        rel = (vr['file_path'] or '').strip()
                        if not rel:
                            continue
                        uk = (vr['upc'] or '').upper()
                        audio_url = rel if rel.startswith(('http://', 'https://', '/')) else f"/static/{rel}"
                        voice_by_upc.setdefault(uk, []).append({
                            'id': vr['id'],
                            'url': audio_url,
                            'mime_type': vr['mime_type'] or '',
                            'created_at': vr['created_at'] or ''
                        })
        except Exception:
            pass
    for row in rows:
        uk = (str(row.get('upc') or '')).upper()
        row['voice_notes'] = voice_by_upc.get(uk, [])
    return rows


def _preplog_attach_titles(items):
    """Attach `item_title` to prep-log rows, preferring bol.db then rawbol.db."""
    rows = [dict(it or {}) for it in (items or [])]
    if not rows:
        return rows

    def _norm_upc(value):
        try:
            return ss_normalization._normalize_upc_preserve_suffix_for_match(ss_normalization._normalize_upc(value))
        except Exception:
            return str(value or '').strip()

    upcs = []
    seen = set()

    def _add_upc(v):
        u = _norm_upc(v)
        if u and u not in seen:
            seen.add(u)
            upcs.append(u)

    for row in rows:
        upc = _norm_upc(row.get('upc'))
        if not upc:
            continue
        _add_upc(upc)
        base_upc = (str(row.get('base_upc') or '').strip() or (upc.split('-', 1)[0] if '-' in upc else upc))
        _add_upc(base_upc)

    if not upcs:
        return rows

    def _chunked(seq, size=350):
        for i in range(0, len(seq), size):
            yield seq[i:i+size]

    bol_title_map = {}
    try:
        with ss_database.db_connection('bol.db') as conn:
            cur = conn.cursor()
            for chunk in _chunked(upcs):
                placeholders = ','.join('?' for _ in chunk)
                cur.execute(f'''
                    SELECT upc, item_description
                    FROM bol_items
                    WHERE upc IN ({placeholders})
                    ORDER BY import_date DESC, id DESC
                ''', tuple(chunk))
                for rr in cur.fetchall():
                    key = _norm_upc(rr['upc'] if isinstance(rr, sqlite3.Row) else rr[0])
                    title = ((rr['item_description'] if isinstance(rr, sqlite3.Row) else rr[1]) or '').strip()
                    if key and title and key not in bol_title_map:
                        bol_title_map[key] = title
    except Exception:
        bol_title_map = {}

    raw_title_map = {}
    try:
        with ss_database.db_connection('rawbol.db') as conn:
            cur = conn.cursor()
            for chunk in _chunked(upcs):
                placeholders = ','.join('?' for _ in chunk)
                cur.execute(f'''
                    SELECT upc, item_description
                    FROM raw_bol_items
                    WHERE upc IN ({placeholders})
                ''', tuple(chunk))
                for rr in cur.fetchall():
                    key = _norm_upc(rr['upc'] if isinstance(rr, sqlite3.Row) else rr[0])
                    title = ((rr['item_description'] if isinstance(rr, sqlite3.Row) else rr[1]) or '').strip()
                    if key and title and key not in raw_title_map:
                        raw_title_map[key] = title
    except Exception:
        raw_title_map = {}

    for row in rows:
        upc = _norm_upc(row.get('upc'))
        base_upc = (str(row.get('base_upc') or '').strip() or (upc.split('-', 1)[0] if '-' in upc else upc))
        meta = row.get('meta') if isinstance(row.get('meta'), dict) else {}
        meta_title = str(meta.get('title') or meta.get('item_description') or '').strip()
        item_title = (
            bol_title_map.get(upc) or
            bol_title_map.get(base_upc) or
            raw_title_map.get(upc) or
            raw_title_map.get(base_upc) or
            meta_title or
            upc
        )
        row['item_title'] = item_title

    return rows


def _preplog_apply_quantity_change(entry, *, old_qty, new_qty):
    """Apply prep-log quantity edits to backing bol/items_prep rows."""
    try:
        old_qty_n = int(old_qty if old_qty is not None else 1)
    except Exception:
        old_qty_n = 1
    try:
        new_qty_n = int(new_qty if new_qty is not None else old_qty_n)
    except Exception:
        return False, 'Invalid quantity'
    if old_qty_n < 1:
        old_qty_n = 1
    if new_qty_n < 1:
        return False, 'Quantity must be at least 1'
    if new_qty_n == old_qty_n:
        return True, None

    row = _preplog_enrich_row(entry or {})
    status = str(row.get('status') or '').strip().lower()
    if status not in ('good', 'bad', 'return'):
        return False, 'Unsupported status for quantity edit'

    upc = ss_normalization._normalize_upc_preserve_suffix_for_match(ss_normalization._normalize_upc(row.get('upc')))
    if not upc:
        return False, 'Missing UPC on log entry'
    base_upc = (
        ss_normalization._normalize_upc_preserve_suffix_for_match(ss_normalization._normalize_upc(row.get('base_upc')))
        or (upc.split('-', 1)[0] if '-' in upc else upc)
    )
    lot_hint = ss_normalization._normalize_lot_number(row.get('lot_number'))
    meta = dict(row.get('meta') or {})
    action = str(meta.get('action') or '').strip().lower()
    affects_base_good = bool(ss_normalization._coerce_bool(meta.get('affects_base_good'))) or action in (
        'saved_good_suffixed',
        'special_good_suffixed',
        'good_suffixed',
    )
    note = str(row.get('note') or '').strip()
    reason = str(row.get('reason') or '').strip()
    delta = new_qty_n - old_qty_n

    conn = None
    try:
        conn = sqlite3.connect('bol.db')
        cur = conn.cursor()
        ss_prep_schema._ensure_items_prep_tables()
        ts = ss_listing_checks._listagent_now_iso()

        if not lot_hint and ss_warehouse_allocations._has_multiple_lot_rows(cur, upc):
            return False, (
                f'Cannot edit quantity for {upc} without LOT context. '
                'This UPC exists in multiple LOTs.'
            )

        def _pick_bol_row(target_upc, preferred_lot=''):
            lot_n = ss_normalization._normalize_lot_number(preferred_lot)
            if lot_n:
                cur.execute('''
                    SELECT id, upc, lot_number, original_qty, good_qty, bad_qty, unchecked_qty, quantity
                    FROM bol_items
                    WHERE upc = ? COLLATE NOCASE
                      AND lot_number = ? COLLATE NOCASE
                    ORDER BY import_date DESC, id DESC
                    LIMIT 1
                ''', (target_upc, lot_n))
                row_local = cur.fetchone()
                if row_local:
                    return row_local
                # Legacy fallback only: lot-less row for this UPC.
                cur.execute('''
                    SELECT id, upc, lot_number, original_qty, good_qty, bad_qty, unchecked_qty, quantity
                    FROM bol_items
                    WHERE upc = ? COLLATE NOCASE
                      AND COALESCE(lot_number, '') = ''
                    ORDER BY import_date DESC, id DESC
                    LIMIT 1
                ''', (target_upc,))
                row_local = cur.fetchone()
                if row_local:
                    return row_local
                # Lot-aware edits must not spill into a different lot.
                return None
            cur.execute('''
                SELECT id, upc, lot_number, original_qty, good_qty, bad_qty, unchecked_qty, quantity
                FROM bol_items
                WHERE upc = ? COLLATE NOCASE
                ORDER BY import_date DESC, id DESC
                LIMIT 1
            ''', (target_upc,))
            return cur.fetchone()

        def _effective_unchecked(row_local):
            unchecked_local = row_local[6]
            if unchecked_local is not None:
                return int(unchecked_local or 0)
            original_local = int(row_local[3] or 0)
            good_local = int(row_local[4] or 0)
            bad_local = int(row_local[5] or 0)
            return max(0, original_local - good_local - bad_local)

        def _upsert_status_qty(target_upc, target_lot, target_status, target_reason, target_note, qty_local):
            lot_key = ss_warehouse_allocations._resolve_prep_status_lot(cur, target_upc, target_lot)
            cur.execute('''
                UPDATE items_prep_status
                SET status = ?, reason = ?, note = ?, quantity = ?, updated_at = ?
                WHERE upc = ? COLLATE NOCASE
                  AND COALESCE(lot_number, '') = ? COLLATE NOCASE
            ''', (target_status, target_reason, target_note, qty_local, ts, target_upc, lot_key))
            if cur.rowcount == 0:
                cur.execute('''
                    INSERT INTO items_prep_status (upc, lot_number, status, reason, note, quantity, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                ''', (target_upc, ss_normalization._normalize_lot_number(target_lot), target_status, target_reason, target_note, qty_local, ts))

        target_row = _pick_bol_row(upc, lot_hint)
        if not target_row:
            return False, f'Item {upc} not found'
        target_id = int(target_row[0])
        target_lot = ss_normalization._normalize_lot_number(target_row[2]) or lot_hint
        is_suffixed = ss_normalization._is_items_to_list_suffixed_upc(upc)

        if status in ('bad', 'return') and not is_suffixed:
            return False, 'BAD and RETURN quantity edits require a suffixed UPC. Base UPC rows cannot be special entries.'

        if status == 'good':
            if is_suffixed:
                # Independent suffixed GOOD rows (including noted GOOD) hold their own qty.
                cur.execute('''
                    UPDATE bol_items
                    SET original_qty = ?, good_qty = ?, bad_qty = 0, unchecked_qty = 0, quantity = ?, temporary = 0
                    WHERE id = ?
                ''', (new_qty_n, new_qty_n, new_qty_n, target_id))

            # Base-tracked GOOD quantity (base rows + noted suffix rows that consume base unchecked).
            if (not is_suffixed) or affects_base_good:
                base_row = target_row if not is_suffixed else _pick_bol_row(base_upc, target_lot)
                if not base_row:
                    return False, f'Base UPC {base_upc} not found'
                base_id = int(base_row[0])
                base_good = int(base_row[4] or 0)
                base_unchecked = _effective_unchecked(base_row)
                if delta > 0 and base_unchecked < delta:
                    return False, f'Cannot increase quantity by {delta}: only {base_unchecked} unchecked available'
                new_base_good = max(0, base_good + delta)
                new_base_unchecked = max(0, base_unchecked - delta)
                cur.execute('''
                    UPDATE bol_items
                    SET good_qty = ?, unchecked_qty = ?
                    WHERE id = ?
                ''', (new_base_good, new_base_unchecked, base_id))

            _upsert_status_qty(upc, target_lot, 'good', reason, note, new_qty_n)

        elif status == 'bad':
            cur.execute('''
                UPDATE bol_items
                SET original_qty = ?, bad_qty = ?, good_qty = 0, unchecked_qty = 0, quantity = ?, temporary = 0
                WHERE id = ?
            ''', (new_qty_n, new_qty_n, new_qty_n, target_id))

            base_row = _pick_bol_row(base_upc, target_lot)
            if not base_row:
                return False, f'Base UPC {base_upc} not found'
            base_id = int(base_row[0])
            base_bad = int(base_row[5] or 0)
            base_unchecked = _effective_unchecked(base_row)
            if delta > 0 and base_unchecked < delta:
                return False, f'Cannot increase BAD quantity by {delta}: only {base_unchecked} unchecked available'
            new_base_bad = max(0, base_bad + delta)
            new_base_unchecked = max(0, base_unchecked - delta)
            cur.execute('''
                UPDATE bol_items
                SET bad_qty = ?, unchecked_qty = ?
                WHERE id = ?
            ''', (new_base_bad, new_base_unchecked, base_id))

            _upsert_status_qty(upc, target_lot, 'bad', reason, note, new_qty_n)

        elif status == 'return':
            cur.execute('''
                UPDATE bol_items
                SET original_qty = ?, good_qty = ?, bad_qty = 0, unchecked_qty = 0, quantity = ?, temporary = 0
                WHERE id = ?
            ''', (new_qty_n, new_qty_n, new_qty_n, target_id))

            base_row = _pick_bol_row(base_upc, target_lot)
            if not base_row:
                return False, f'Base UPC {base_upc} not found'
            base_id = int(base_row[0])
            base_unchecked = _effective_unchecked(base_row)
            if delta > 0 and base_unchecked < delta:
                return False, f'Cannot increase RETURN quantity by {delta}: only {base_unchecked} unchecked available'
            new_base_unchecked = max(0, base_unchecked - delta)
            cur.execute('''
                UPDATE bol_items
                SET unchecked_qty = ?
                WHERE id = ?
            ''', (new_base_unchecked, base_id))

            _upsert_status_qty(upc, target_lot, 'return', (reason or 'return'), note, new_qty_n)

        conn.commit()
        try:
            ss_caching.update_data_version()
        except Exception:
            pass
        return True, None
    except Exception as e:
        try:
            if conn:
                conn.rollback()
        except Exception:
            pass
        return False, ss_errors._safe_error(e, 'preplog:apply_qty')
    finally:
        try:
            if conn:
                conn.close()
        except Exception:
            pass


def api_preplog_recent():
    """Recent item-prep events (preplog.db)."""
    try:
        limit = ss_listing_settings._listingagent_parse_int(request.args.get('limit'), 200) or 200
        limit = max(1, min(limit, 2000))
        include_undone = (request.args.get('include_undone') or '1').strip().lower() in ('1', 'true', 'yes', 'y')
        auto_assigned_only = (request.args.get('auto_assigned_only') or '0').strip().lower() in ('1', 'true', 'yes', 'y')
        lot_filter = ss_normalization._normalize_lot_number(request.args.get('lot_number') or request.args.get('lot'))

        items = [_preplog_enrich_row(it) for it in _preplog_recent(limit=limit, include_undone=include_undone)]
        if auto_assigned_only:
            items = [it for it in items if it.get('auto_assigned')]
        if lot_filter:
            items = [it for it in items if ss_normalization._normalize_lot_number(it.get('lot_number')).lower() == lot_filter.lower()]
        items = _preplog_attach_titles(items)
        items = _preplog_attach_voice_notes(items)

        return jsonify({'success': True, 'items': items})
    except Exception as e:
        return jsonify({'success': False, 'error': ss_errors._safe_error(e, 'preplog:recent')}), 500


def api_preplog_update():
    """Update prep-log note/quantity by id. JSON: { id, note?, quantity? }"""
    try:
        data = request.get_json() or {}
        log_id = data.get('id')
        if log_id is None:
            return jsonify({'success': False, 'error': 'Missing id'}), 400
        try:
            log_id = int(log_id)
        except Exception:
            return jsonify({'success': False, 'error': 'Invalid id'}), 400

        has_note = ('note' in data)
        has_qty = ('quantity' in data or 'qty' in data)
        if not has_note and not has_qty:
            return jsonify({'success': False, 'error': 'Nothing to update'}), 400

        with ss_database.db_connection('preplog.db') as conn:
            cur = conn.cursor()
            _preplog_init_tables(cur)
            cur.execute('SELECT * FROM prep_log WHERE id = ? LIMIT 1', (log_id,))
            row = cur.fetchone()
            entry = ss_listing_checks._listagent_row_to_dict(row)
            if not entry:
                return jsonify({'success': False, 'error': 'Log entry not found'}), 404
            if entry.get('undone'):
                return jsonify({'success': False, 'error': 'Cannot edit a deleted log entry'}), 400

            updates = []
            params = []
            cleaned_note = (entry.get('note') or '').strip()
            previous_note = cleaned_note
            new_qty = entry.get('quantity')
            try:
                old_qty = int(entry.get('quantity') if entry.get('quantity') is not None else 1)
            except Exception:
                old_qty = 1
            if old_qty < 1:
                old_qty = 1

            if has_note:
                cleaned_note = (data.get('note') or '').strip()
                updates.append('note = ?')
                params.append(cleaned_note or None)

            if has_qty:
                raw_qty = data.get('quantity', data.get('qty'))
                try:
                    new_qty = int(raw_qty)
                except Exception:
                    return jsonify({'success': False, 'error': 'Invalid quantity'}), 400
                if new_qty < 1:
                    return jsonify({'success': False, 'error': 'Quantity must be at least 1'}), 400
                updates.append('quantity = ?')
                params.append(new_qty)

            if has_qty and int(new_qty if new_qty is not None else old_qty) != old_qty:
                ok_qty, qty_err = _preplog_apply_quantity_change(entry, old_qty=old_qty, new_qty=new_qty)
                if not ok_qty:
                    return jsonify({'success': False, 'error': qty_err or 'Failed to update quantity'}), 400

            if not updates:
                return jsonify({'success': False, 'error': 'Nothing to update'}), 400

            params.append(log_id)
            cur.execute(f'''
                UPDATE prep_log
                SET {', '.join(updates)},
                    undo_error = NULL
                WHERE id = ?
            ''', tuple(params))
            cur.execute('SELECT * FROM prep_log WHERE id = ? LIMIT 1', (log_id,))
            updated = _preplog_enrich_row(ss_listing_checks._listagent_row_to_dict(cur.fetchone()))

        # Keep live prep status note in sync for convenience.
        if has_note:
            try:
                upc = ss_normalization._normalize_upc_preserve_suffix_for_match(ss_normalization._normalize_upc(updated.get('upc')))
                lot_number = ss_normalization._normalize_lot_number(updated.get('lot_number'))
                status_value = ss_normalization._normalize_prep_row_status(updated.get('status'))
                note_allowed = status_value in ('good', 'bad', 'return')
                synced_note = cleaned_note if note_allowed else ''
                # Keep prep status timestamps in UTC format for stable last_edited sorting.
                ts = datetime.datetime.now(datetime.UTC).isoformat()
                conn_b = None
                updated_any = False
                try:
                    conn_b = sqlite3.connect('bol.db')
                    cur_b = conn_b.cursor()
                    ss_prep_schema._ensure_items_prep_tables()
                    previous_note_n = (previous_note or '').strip()
                    # Keep note scoped to the exact log UPC. For suffixed entries we do NOT
                    # mirror to base UPC, otherwise distinct noted units can overwrite base notes.
                    for target_upc in (upc,):
                        if not target_upc:
                            continue
                        status_lot = ss_warehouse_allocations._resolve_prep_status_lot(cur_b, target_upc, lot_number)
                        cur_b.execute('''
                            UPDATE items_prep_status
                            SET note = ?, updated_at = ?
                            WHERE upc = ? COLLATE NOCASE
                              AND COALESCE(lot_number, '') = ? COLLATE NOCASE
                        ''', (synced_note or None, ts, target_upc, status_lot))
                        updated_any = updated_any or bool(cur_b.rowcount)
                    if upc:
                        try:
                            target_note_row = None
                            if previous_note_n:
                                cur_b.execute('''
                                    SELECT id, note
                                    FROM items_prep_notes
                                    WHERE upc = ? COLLATE NOCASE
                                      AND TRIM(COALESCE(note, '')) = ?
                                    ORDER BY created_at DESC, id DESC
                                    LIMIT 1
                                ''', (upc, previous_note_n))
                                target_note_row = cur_b.fetchone()
                            if not target_note_row:
                                cur_b.execute('''
                                    SELECT id, note
                                    FROM items_prep_notes
                                    WHERE upc = ? COLLATE NOCASE
                                    ORDER BY created_at DESC, id DESC
                                    LIMIT 1
                                ''', (upc,))
                                target_note_row = cur_b.fetchone()

                            if synced_note:
                                if target_note_row:
                                    target_note_id = int(target_note_row[0])
                                    target_note_text = (target_note_row[1] or '').strip()
                                    if target_note_text != synced_note:
                                        cur_b.execute('''
                                            UPDATE items_prep_notes
                                            SET note = ?, created_at = ?
                                            WHERE id = ?
                                        ''', (synced_note, ts, target_note_id))
                                        updated_any = updated_any or bool(cur_b.rowcount)
                                else:
                                    cur_b.execute('INSERT INTO items_prep_notes (upc, note, created_at) VALUES (?,?,?)', (upc, synced_note, ts))
                                    updated_any = True
                            elif target_note_row:
                                target_note_id = int(target_note_row[0])
                                cur_b.execute('DELETE FROM items_prep_notes WHERE id = ?', (target_note_id,))
                                updated_any = updated_any or bool(cur_b.rowcount)
                        except Exception:
                            pass
                    conn_b.commit()
                finally:
                    if conn_b is not None:
                        conn_b.close()
                if updated_any:
                    try:
                        ss_caching.update_data_version()
                    except Exception:
                        pass
            except Exception:
                pass

        updated_with_titles = _preplog_attach_titles([updated])
        return jsonify({'success': True, 'item': (updated_with_titles[0] if updated_with_titles else updated)})
    except Exception as e:
        return jsonify({'success': False, 'error': ss_errors._safe_error(e, 'preplog:update')}), 500


def api_preplog_undo():
    """Undo a prep log entry (and mark it undone in preplog.db). JSON: { id }"""
    try:
        data = request.get_json() or {}
        log_id = data.get('id')
        if log_id is None:
            return jsonify({'success': False, 'error': 'Missing id'}), 400
        try:
            log_id = int(log_id)
        except Exception:
            return jsonify({'success': False, 'error': 'Invalid id'}), 400

        # Fetch entry
        with ss_database.db_connection('preplog.db') as conn:
            cur = conn.cursor()
            _preplog_init_tables(cur)
            cur.execute('SELECT * FROM prep_log WHERE id = ? LIMIT 1', (log_id,))
            row = cur.fetchone()
            entry = ss_listing_checks._listagent_row_to_dict(row)

        if not entry:
            return jsonify({'success': False, 'error': 'Log entry not found'}), 404
        if entry.get('undone'):
            return jsonify({'success': False, 'error': 'Already undone'}), 400

        upc = (entry.get('upc') or '').strip()
        status = (entry.get('status') or '').strip().lower()
        qty = entry.get('quantity') if entry.get('quantity') is not None else 1
        base_upc = (entry.get('base_upc') or '').strip() or None
        enriched_entry = _preplog_enrich_row(entry)
        lot_number = ss_normalization._normalize_lot_number(enriched_entry.get('lot_number'))
        meta_action = ''
        try:
            meta_action = str((enriched_entry.get('meta') or {}).get('action') or '').strip().lower()
        except Exception:
            meta_action = ''

        ok, err, code = ss_prep_undo._items_prep_undo_core(
            upc=upc,
            status=status,
            qty=qty,
            base_upc=base_upc,
            lot_number=lot_number,
            action=meta_action
        )
        if not ok:
            err_text = (err or '').strip()
            err_l = err_text.lower()
            # Allow stale GOOD rows to be cleared from prep log even when backing inventory
            # was already zeroed/removed by previous corrections or migrations.
            stale_good_noop = (
                status == 'good' and (
                    'only 0 marked as good' in err_l
                    or 'cannot undo 0' in err_l
                    or ('not found' in err_l and 'upc' in err_l)
                )
            )
            stale_non_good_noop = (
                status in ('bad', 'return')
                and ('not found' in err_l and 'upc' in err_l)
            )
            if stale_good_noop or stale_non_good_noop:
                updated = _preplog_mark_undone(log_id, undone_at=ss_listing_checks._listagent_now_iso(), undo_error=None)
                return jsonify({
                    'success': True,
                    'item': _preplog_enrich_row(updated),
                    'noop_undo': True,
                    'warning': (
                        'Inventory was already at zero; log entry marked undone.'
                        if stale_good_noop
                        else 'Backing inventory row was already removed; log entry marked undone.'
                    )
                })
            try:
                _preplog_set_undo_error(log_id, err)
            except Exception:
                pass
            return jsonify({'success': False, 'error': err or 'Undo failed'}), (code or 400)

        updated = _preplog_mark_undone(log_id, undone_at=ss_listing_checks._listagent_now_iso(), undo_error=None)
        return jsonify({'success': True, 'item': _preplog_enrich_row(updated)})
    except Exception as e:
        return jsonify({'success': False, 'error': ss_errors._safe_error(e, 'preplog:undo')}), 500


def api_preplog_reassign_lot():
    """Reassign a prep-log entry to a different lot and move backing inventory state."""
    conn = None
    try:
        data = request.get_json() or {}
        log_id = data.get('id')
        if log_id is None:
            return jsonify({'success': False, 'error': 'Missing id'}), 400
        try:
            log_id = int(log_id)
        except Exception:
            return jsonify({'success': False, 'error': 'Invalid id'}), 400

        target_lot = ss_normalization._normalize_lot_number(data.get('lot_number') or data.get('lot'))
        if not target_lot:
            return jsonify({'success': False, 'error': 'Missing lot_number'}), 400

        with ss_database.db_connection('preplog.db') as preplog_conn:
            preplog_cur = preplog_conn.cursor()
            _preplog_init_tables(preplog_cur)
            preplog_cur.execute('SELECT * FROM prep_log WHERE id = ? LIMIT 1', (log_id,))
            entry = ss_listing_checks._listagent_row_to_dict(preplog_cur.fetchone())

        if not entry:
            return jsonify({'success': False, 'error': 'Log entry not found'}), 404
        if entry.get('undone'):
            return jsonify({'success': False, 'error': 'Cannot reassign an undone entry'}), 400

        entry = _preplog_enrich_row(entry)
        status = (entry.get('status') or '').strip().lower()
        upc_raw = (entry.get('upc') or '').strip()
        if not upc_raw or status not in ('good', 'bad', 'return'):
            return jsonify({'success': False, 'error': 'Unsupported log entry'}), 400

        upc = ss_normalization._normalize_upc_preserve_suffix_for_match(ss_normalization._normalize_upc(upc_raw))
        base_upc = (entry.get('base_upc') or '').strip() or upc.split('-', 1)[0]
        entry_meta = dict(entry.get('meta') or {})
        old_lot = ss_normalization._normalize_lot_number(entry.get('lot_number'))
        if old_lot and old_lot.lower() == target_lot.lower():
            return jsonify({'success': True, 'item': entry, 'moved': False})

        try:
            qty = int(entry.get('quantity') or 1)
        except Exception:
            qty = 1
        if qty < 1:
            qty = 1

        is_suffixed = ss_normalization._is_items_to_list_suffixed_upc(upc)
        if status in ('bad', 'return') and not is_suffixed:
            return jsonify({
                'success': False,
                'error': 'BAD and RETURN lot reassign require a suffixed UPC. Base UPC rows cannot be special entries.'
            }), 400

        conn = sqlite3.connect('bol.db', isolation_level='IMMEDIATE')
        cur = conn.cursor()
        ss_prep_schema._ensure_items_prep_tables()
        ts = ss_listing_checks._listagent_now_iso()

        def _pick_bol_row(target_upc, lot_number=''):
            lot_n = ss_normalization._normalize_lot_number(lot_number)
            if lot_n:
                cur.execute('''
                    SELECT id, upc, lot_number, bol_number, original_qty, good_qty, bad_qty, unchecked_qty, quantity
                    FROM bol_items
                    WHERE upc = ? COLLATE NOCASE
                      AND lot_number = ? COLLATE NOCASE
                    ORDER BY import_date DESC, id DESC
                    LIMIT 1
                ''', (target_upc, lot_n))
                row = cur.fetchone()
                if row:
                    return row
            cur.execute('''
                SELECT id, upc, lot_number, bol_number, original_qty, good_qty, bad_qty, unchecked_qty, quantity
                FROM bol_items
                WHERE upc = ? COLLATE NOCASE
                ORDER BY import_date DESC, id DESC
                LIMIT 1
            ''', (target_upc,))
            return cur.fetchone()

        def _effective_unchecked(row):
            unchecked = row[7]
            if unchecked is not None:
                return int(unchecked or 0)
            original_qty = int(row[4] or 0)
            good_qty = int(row[5] or 0)
            bad_qty = int(row[6] or 0)
            return max(0, original_qty - good_qty - bad_qty)

        def _move_status_lot(status_upc, from_lot, to_lot, move_qty):
            from_key = ss_normalization._normalize_lot_number(from_lot)
            to_key = ss_normalization._normalize_lot_number(to_lot)
            if from_key.lower() == to_key.lower():
                return

            cur.execute('''
                SELECT id, status, reason, note, quantity
                FROM items_prep_status
                WHERE upc = ? COLLATE NOCASE
                  AND COALESCE(lot_number, '') = ? COLLATE NOCASE
                LIMIT 1
            ''', (status_upc, from_key))
            source = cur.fetchone()
            if not source and from_key:
                cur.execute('''
                    SELECT id, status, reason, note, quantity
                    FROM items_prep_status
                    WHERE upc = ? COLLATE NOCASE
                      AND COALESCE(lot_number, '') = ''
                    LIMIT 1
                ''', (status_upc,))
                source = cur.fetchone()
                from_key = ''
            if not source:
                return

            source_id = int(source[0])
            source_status = (source[1] or '').strip()
            source_reason = source[2] or ''
            source_note = source[3] or ''
            source_qty = int(source[4] or 0)
            if source_qty <= 0:
                source_qty = int(move_qty or 0)
            shift_qty = int(move_qty or source_qty or 0)
            if shift_qty <= 0:
                return
            if source_qty > 0:
                shift_qty = min(shift_qty, source_qty)

            cur.execute('''
                SELECT id, quantity
                FROM items_prep_status
                WHERE upc = ? COLLATE NOCASE
                  AND COALESCE(lot_number, '') = ? COLLATE NOCASE
                LIMIT 1
            ''', (status_upc, to_key))
            target = cur.fetchone()

            if target:
                target_id = int(target[0])
                target_qty = int(target[1] or 0)
                cur.execute('''
                    UPDATE items_prep_status
                    SET quantity = ?, status = ?, reason = ?, note = ?, updated_at = ?
                    WHERE id = ?
                ''', (target_qty + shift_qty, source_status, source_reason, source_note, ts, target_id))
            else:
                cur.execute('''
                    INSERT INTO items_prep_status (upc, lot_number, status, reason, note, quantity, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                ''', (status_upc, to_key, source_status, source_reason, source_note, shift_qty, ts))

            if source_qty > shift_qty:
                cur.execute('''
                    UPDATE items_prep_status
                    SET quantity = ?, updated_at = ?
                    WHERE id = ?
                ''', (source_qty - shift_qty, ts, source_id))
            else:
                cur.execute('DELETE FROM items_prep_status WHERE id = ?', (source_id,))

        assigned_from_lot = old_lot

        if is_suffixed:
            item_row = _pick_bol_row(upc, old_lot)
            if not item_row:
                return jsonify({'success': False, 'error': f'Item {upc} not found'}), 404

            item_id = int(item_row[0])
            row_lot = ss_normalization._normalize_lot_number(item_row[2])
            assigned_from_lot = row_lot or old_lot
            item_qty = int(item_row[4] or item_row[8] or qty or 1)
            if item_qty < 1:
                item_qty = 1

            if status == 'bad':
                old_base = _pick_bol_row(base_upc, assigned_from_lot)
                if not old_base:
                    return jsonify({'success': False, 'error': f'Base UPC {base_upc} not found in source lot'}), 404
                new_base = _pick_bol_row(base_upc, target_lot)
                if not new_base or ss_normalization._normalize_lot_number(new_base[2]).lower() != target_lot.lower():
                    return jsonify({'success': False, 'error': f'Base UPC {base_upc} not found in target lot {target_lot}'}), 404

                old_bad = int(old_base[6] or 0)
                old_unchecked = _effective_unchecked(old_base)
                move_qty = min(item_qty, old_bad if old_bad > 0 else item_qty)

                new_bad = int(new_base[6] or 0)
                new_unchecked = _effective_unchecked(new_base)
                if move_qty > new_unchecked:
                    return jsonify({'success': False, 'error': f'Cannot move {move_qty} BAD items to lot {target_lot}: only {new_unchecked} unchecked available'}), 400

                cur.execute('''
                    UPDATE bol_items
                    SET bad_qty = ?, unchecked_qty = ?
                    WHERE id = ?
                ''', (max(0, old_bad - move_qty), old_unchecked + move_qty, int(old_base[0])))

                cur.execute('''
                    UPDATE bol_items
                    SET bad_qty = ?, unchecked_qty = ?
                    WHERE id = ?
                ''', (new_bad + move_qty, max(0, new_unchecked - move_qty), int(new_base[0])))
            elif status == 'return':
                old_base = _pick_bol_row(base_upc, assigned_from_lot)
                if not old_base:
                    return jsonify({'success': False, 'error': f'Base UPC {base_upc} not found in source lot'}), 404
                new_base = _pick_bol_row(base_upc, target_lot)
                if not new_base or ss_normalization._normalize_lot_number(new_base[2]).lower() != target_lot.lower():
                    return jsonify({'success': False, 'error': f'Base UPC {base_upc} not found in target lot {target_lot}'}), 404

                move_qty = item_qty
                old_unchecked = _effective_unchecked(old_base)
                new_unchecked = _effective_unchecked(new_base)
                if move_qty > new_unchecked:
                    return jsonify({'success': False, 'error': f'Cannot move {move_qty} RETURN items to lot {target_lot}: only {new_unchecked} unchecked available'}), 400

                cur.execute('''
                    UPDATE bol_items
                    SET unchecked_qty = ?
                    WHERE id = ?
                ''', (old_unchecked + move_qty, int(old_base[0])))

                cur.execute('''
                    UPDATE bol_items
                    SET unchecked_qty = ?
                    WHERE id = ?
                ''', (max(0, new_unchecked - move_qty), int(new_base[0])))
            elif status == 'good' and ss_normalization._coerce_bool(entry_meta.get('affects_base_good')):
                old_base = _pick_bol_row(base_upc, assigned_from_lot)
                if not old_base:
                    return jsonify({'success': False, 'error': f'Base UPC {base_upc} not found in source lot'}), 404
                new_base = _pick_bol_row(base_upc, target_lot)
                if not new_base or ss_normalization._normalize_lot_number(new_base[2]).lower() != target_lot.lower():
                    return jsonify({'success': False, 'error': f'Base UPC {base_upc} not found in target lot {target_lot}'}), 404

                old_good = int(old_base[5] or 0)
                old_unchecked = _effective_unchecked(old_base)
                move_qty = min(item_qty, old_good if old_good > 0 else item_qty)

                new_good = int(new_base[5] or 0)
                new_unchecked = _effective_unchecked(new_base)
                if move_qty > new_unchecked:
                    return jsonify({'success': False, 'error': f'Cannot move {move_qty} GOOD items to lot {target_lot}: only {new_unchecked} unchecked available'}), 400

                cur.execute('''
                    UPDATE bol_items
                    SET good_qty = ?, unchecked_qty = ?
                    WHERE id = ?
                ''', (max(0, old_good - move_qty), old_unchecked + move_qty, int(old_base[0])))

                cur.execute('''
                    UPDATE bol_items
                    SET good_qty = ?, unchecked_qty = ?
                    WHERE id = ?
                ''', (new_good + move_qty, max(0, new_unchecked - move_qty), int(new_base[0])))

            target_base = _pick_bol_row(base_upc, target_lot)
            target_bol_number = target_base[3] if target_base else item_row[3]
            cur.execute('''
                UPDATE bol_items
                SET lot_number = ?, bol_number = ?
                WHERE id = ?
            ''', (target_lot, target_bol_number, item_id))

            _move_status_lot(upc, assigned_from_lot, target_lot, item_qty)
        else:
            if status != 'good':
                return jsonify({'success': False, 'error': 'Only GOOD base UPC entries can be reassigned without suffix'}), 400
            if not old_lot:
                return jsonify({'success': False, 'error': 'Source lot missing on log entry; cannot reassign safely'}), 400

            old_base = _pick_bol_row(base_upc, old_lot)
            if not old_base or ss_normalization._normalize_lot_number(old_base[2]).lower() != old_lot.lower():
                return jsonify({'success': False, 'error': f'Base UPC {base_upc} not found in source lot {old_lot}'}), 404
            new_base = _pick_bol_row(base_upc, target_lot)
            if not new_base or ss_normalization._normalize_lot_number(new_base[2]).lower() != target_lot.lower():
                return jsonify({'success': False, 'error': f'Base UPC {base_upc} not found in target lot {target_lot}'}), 404

            old_good = int(old_base[5] or 0)
            old_unchecked = _effective_unchecked(old_base)
            if qty > old_good:
                return jsonify({'success': False, 'error': f'Cannot move {qty} GOOD items: only {old_good} good available in lot {old_lot}'}), 400

            new_good = int(new_base[5] or 0)
            new_unchecked = _effective_unchecked(new_base)
            if qty > new_unchecked:
                return jsonify({'success': False, 'error': f'Cannot move {qty} GOOD items to lot {target_lot}: only {new_unchecked} unchecked available'}), 400

            cur.execute('''
                UPDATE bol_items
                SET good_qty = ?, unchecked_qty = ?
                WHERE id = ?
            ''', (old_good - qty, old_unchecked + qty, int(old_base[0])))

            cur.execute('''
                UPDATE bol_items
                SET good_qty = ?, unchecked_qty = ?
                WHERE id = ?
            ''', (new_good + qty, max(0, new_unchecked - qty), int(new_base[0])))

            _move_status_lot(base_upc, old_lot, target_lot, qty)
            assigned_from_lot = old_lot

        conn.commit()
        try:
            ss_caching.update_data_version()
        except Exception:
            pass

        meta = dict(entry.get('meta') or {})
        if assigned_from_lot and not ss_normalization._normalize_lot_number(meta.get('original_lot_number')):
            meta['original_lot_number'] = assigned_from_lot
        meta['lot_number'] = target_lot
        meta['assigned_lot'] = target_lot
        meta['reassigned'] = True
        meta['reassigned_from'] = assigned_from_lot
        meta['reassigned_at'] = ss_listing_checks._listagent_now_iso()
        updated_entry = _preplog_update_meta(log_id, meta)
        return jsonify({'success': True, 'item': updated_entry, 'moved': True})
    except Exception as e:
        try:
            if conn:
                conn.rollback()
        except Exception:
            pass
        return jsonify({'success': False, 'error': ss_errors._safe_error(e, 'preplog:reassign_lot')}), 500
    finally:
        try:
            if conn:
                conn.close()
        except Exception:
            pass
