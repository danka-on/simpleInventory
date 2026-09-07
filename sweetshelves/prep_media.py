"""Prep media for Sweet Shelves."""

import io
import os
import sqlite3
import time
from flask import jsonify, request, send_file
from . import (
    caching as ss_caching, errors as ss_errors, listing_log as ss_listing_log, normalization as
    ss_normalization, prep_context as ss_prep_context, prep_schema as ss_prep_schema, runtime as
    ss_runtime, warehouse_allocations as ss_warehouse_allocations,
)


def _trash_retention_days():
    # Prefer app_settings table value; fallback to env var; then default 7
    conn = None
    try:
        conn = sqlite3.connect('bol.db')
        cur = conn.cursor()
        cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='app_settings'")
        if cur.fetchone():
            cur.execute("SELECT value FROM app_settings WHERE key='TRASH_RETENTION_DAYS'")
            row = cur.fetchone()
            if row and row[0] is not None:
                return int(row[0])
    except Exception:
        pass
    finally:
        if conn is not None:
            conn.close()
    try:
        return int(os.getenv('TRASH_RETENTION_DAYS', '7'))
    except Exception:
        return 7


def _ensure_app_settings():
    conn = None
    try:
        conn = sqlite3.connect('bol.db')
        cur = conn.cursor()
        cur.execute('''CREATE TABLE IF NOT EXISTS app_settings (
            key TEXT PRIMARY KEY,
            value TEXT
        )''')
        conn.commit()
    except Exception as e:
        print('Failed ensuring app_settings:', e)
    finally:
        if conn is not None:
            conn.close()


def _set_trash_retention_days(days: int):
    conn = None
    try:
        days = int(days)
        _ensure_app_settings()
        conn = sqlite3.connect('bol.db')
        cur = conn.cursor()
        cur.execute("INSERT INTO app_settings(key, value) VALUES('TRASH_RETENTION_DAYS', ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (str(days),))
        # Update expires_at for existing trashed items
        cur.execute("SELECT id, deleted_at FROM items_prep_images WHERE deleted_at IS NOT NULL")
        rows = cur.fetchall()
        import datetime as _dt
        for rid, del_at in rows:
            try:
                base = _dt.datetime.fromisoformat(del_at)
            except Exception:
                base = _dt.datetime.now(_dt.UTC)
            new_exp = (base + _dt.timedelta(days=days)).isoformat()
            cur.execute('UPDATE items_prep_images SET expires_at=? WHERE id=?', (new_exp, rid))
        conn.commit()
        return True
    except Exception as e:
        print('Failed setting retention days:', e)
        return False
    finally:
        if conn is not None:
            conn.close()


def _move_to_trash(abs_path, upc):
    try:
        # Build trash path under static/items_prep_trash/YYYY/MM/UPC
        import datetime as _dt
        base = os.path.join(ss_runtime.app.root_path, 'static', 'items_prep_trash')
        now = _dt.datetime.now(_dt.UTC)
        year = str(now.year)
        month = f"{now.month:02d}"
        target_dir = os.path.join(base, year, month, str(upc))
        os.makedirs(target_dir, exist_ok=True)
        fname = os.path.basename(abs_path)
        target = os.path.join(target_dir, fname)
        # If name exists, add counter
        if os.path.exists(target):
            name, ext = os.path.splitext(fname)
            k = 1
            while os.path.exists(target):
                target = os.path.join(target_dir, f"{name}_{k}{ext}")
                k += 1
        os.replace(abs_path, target)
        # Return relative path under static
        rel = os.path.relpath(target, os.path.join(ss_runtime.app.root_path, 'static')).replace('\\','/')
        return rel
    except Exception as e:
        print('Failed move to trash:', e)
        return None


def _items_prep_delete_media_rows(cur, upc, row_status=None, media_type=None):
    """Hard-delete prep media rows and files for one scoped UPC."""
    try:
        conditions = ['upc = ? COLLATE NOCASE']
        params = [upc]
        if row_status is not None:
            conditions.append("COALESCE(row_status, '') = ? COLLATE NOCASE")
            params.append(ss_normalization._normalize_prep_row_status(row_status))
        if media_type:
            conditions.append("COALESCE(media_type, '') = ? COLLATE NOCASE")
            params.append(str(media_type).strip().lower())
        where_sql = ' AND '.join(conditions)
        cur.execute(f'SELECT id, file_path FROM items_prep_media WHERE {where_sql}', tuple(params))
        rows = cur.fetchall()
        for row in rows:
            rel = row['file_path'] if isinstance(row, sqlite3.Row) else row[1]
            if not rel:
                continue
            abs_path = os.path.join(ss_runtime.app.root_path, 'static', rel) if not os.path.isabs(rel) else rel
            try:
                if os.path.isfile(abs_path):
                    os.remove(abs_path)
            except Exception as fe:
                print('Failed hard remove media', abs_path, fe)
        cur.execute(f'DELETE FROM items_prep_media WHERE {where_sql}', tuple(params))
        return int(cur.rowcount or 0)
    except Exception as e:
        print('Failed deleting prep media rows:', e)
        return 0


def _items_prep_delete_image_rows(cur, upc, row_status=None):
    """Hard-delete prep photo rows and files for one scoped UPC."""
    try:
        conditions = ['upc = ? COLLATE NOCASE']
        params = [upc]
        if row_status is not None:
            conditions.append("COALESCE(row_status, '') = ? COLLATE NOCASE")
            params.append(ss_normalization._normalize_prep_row_status(row_status))
        where_sql = ' AND '.join(conditions)
        cur.execute(f'SELECT id, image_path, trash_path FROM items_prep_images WHERE {where_sql}', tuple(params))
        rows = cur.fetchall()
        for row in rows:
            image_rel = row['image_path'] if isinstance(row, sqlite3.Row) else row[1]
            trash_rel = row['trash_path'] if isinstance(row, sqlite3.Row) else row[2]
            seen_paths = set()
            for rel in (trash_rel, image_rel):
                rel = str(rel or '').strip()
                if not rel or rel in seen_paths:
                    continue
                seen_paths.add(rel)
                abs_path = os.path.join(ss_runtime.app.root_path, 'static', rel) if not os.path.isabs(rel) else rel
                try:
                    if os.path.isfile(abs_path):
                        os.remove(abs_path)
                except Exception as fe:
                    print('Failed hard remove photo', abs_path, fe)
        cur.execute(f'DELETE FROM items_prep_images WHERE {where_sql}', tuple(params))
        return int(cur.rowcount or 0)
    except Exception as e:
        print('Failed deleting prep image rows:', e)
        return 0


def _items_prep_delete_note_rows(cur, upc, row_status=None):
    """Hard-delete prep note rows for one scoped UPC."""
    try:
        conditions = ['upc = ? COLLATE NOCASE']
        params = [upc]
        if row_status is not None:
            conditions.append("COALESCE(row_status, '') = ? COLLATE NOCASE")
            params.append(ss_normalization._normalize_prep_row_status(row_status))
        where_sql = ' AND '.join(conditions)
        cur.execute(f'DELETE FROM items_prep_notes WHERE {where_sql}', tuple(params))
        return int(cur.rowcount or 0)
    except Exception as e:
        print('Failed deleting prep note rows:', e)
        return 0


def _items_prep_delete_diagnostic_assets(cur, upc, row_status=None, delete_notes=True):
    """Hard-delete exact diagnostic assets for one UPC so reused suffixes start clean."""
    deleted = {
        'images': _items_prep_delete_image_rows(cur, upc, row_status=row_status),
        'notes': 0,
        'media': _items_prep_delete_media_rows(cur, upc, row_status=row_status)
    }
    if delete_notes:
        deleted['notes'] = _items_prep_delete_note_rows(cur, upc, row_status=row_status)
    return deleted


def _prep_media_cleaner_abs_path(raw_path):
    raw = str(raw_path or '').strip()
    if not raw:
        return ''
    if os.path.isabs(raw):
        return raw
    if raw.startswith('/static/'):
        raw = raw[len('/static/'):]
    elif raw.startswith('static/'):
        raw = raw[len('static/'):]
    raw = raw.replace('/', os.sep).lstrip('\\/')
    return os.path.join(ss_runtime.app.root_path, 'static', raw)


def _prep_media_cleaner_format_bytes(size_bytes):
    try:
        value = float(size_bytes or 0)
    except Exception:
        value = 0.0
    units = ['B', 'KB', 'MB', 'GB', 'TB']
    idx = 0
    while value >= 1024.0 and idx < len(units) - 1:
        value /= 1024.0
        idx += 1
    if idx == 0:
        return f"{int(value)} {units[idx]}"
    return f"{value:.2f} {units[idx]}"


def _prep_media_cleaner_allowed_types(raw_types=None):
    allowed = {'image', 'audio', 'video'}
    if raw_types is None:
        return ['image', 'audio', 'video']
    if isinstance(raw_types, str):
        parts = [p.strip().lower() for p in raw_types.split(',') if p.strip()]
    elif isinstance(raw_types, (list, tuple, set)):
        parts = [str(p or '').strip().lower() for p in raw_types if str(p or '').strip()]
    else:
        parts = []
    resolved = [p for p in parts if p in allowed]
    return resolved or ['image', 'audio', 'video']


def _prep_media_cleaner_normalize_upc_filter(raw_upc):
    raw = str(raw_upc or '').strip()
    if not raw:
        return ''
    try:
        return ss_normalization._normalize_upc_preserve_suffix_for_match(ss_normalization._normalize_upc(raw))
    except Exception:
        return raw


def _prep_media_cleaner_row_lots(raw_upc, row_status, status_lot_map, any_status_lots, bol_lot_map):
    try:
        upc = ss_normalization._normalize_upc_preserve_suffix_for_match(ss_normalization._normalize_upc(raw_upc))
    except Exception:
        upc = str(raw_upc or '').strip()
    status_key = ss_normalization._normalize_prep_row_status(row_status)
    base_upc = upc.split('-', 1)[0].strip()
    lots = set()
    if status_key:
        lots.update(status_lot_map.get((upc, status_key), set()))
        if not lots and base_upc and base_upc != upc:
            lots.update(status_lot_map.get((base_upc, status_key), set()))
    if not lots:
        lots.update(any_status_lots.get(upc, set()))
    if not lots and base_upc and base_upc != upc:
        lots.update(any_status_lots.get(base_upc, set()))
    if not lots:
        lots.update(bol_lot_map.get(upc, set()))
    if not lots and base_upc and base_upc != upc:
        lots.update(bol_lot_map.get(base_upc, set()))
    return sorted(lots)


def _prep_media_cleaner_load_rows():
    """Return active prep media rows (photos/audio/video) with size and lot metadata."""
    ss_prep_schema._ensure_items_prep_tables()
    conn = sqlite3.connect('bol.db')
    conn.row_factory = sqlite3.Row
    try:
        cur = conn.cursor()

        status_lot_map = {}
        any_status_lots = {}
        lot_options = set()
        cur.execute('''
            SELECT upc, COALESCE(lot_number, '') AS lot_number, LOWER(COALESCE(status, '')) AS status
            FROM items_prep_status
            WHERE TRIM(COALESCE(lot_number, '')) != ''
        ''')
        for row in cur.fetchall():
            upc = ss_normalization._normalize_upc_preserve_suffix_for_match(ss_normalization._normalize_upc(row['upc']))
            lot = ss_normalization._normalize_lot_number(row['lot_number'])
            status = ss_normalization._normalize_prep_row_status(row['status'])
            if not upc or not lot:
                continue
            lot_options.add(lot)
            any_status_lots.setdefault(upc, set()).add(lot)
            if status:
                status_lot_map.setdefault((upc, status), set()).add(lot)

        bol_lot_map = {}
        cur.execute('''
            SELECT upc, COALESCE(lot_number, '') AS lot_number
            FROM bol_items
            WHERE TRIM(COALESCE(lot_number, '')) != ''
        ''')
        for row in cur.fetchall():
            upc = ss_normalization._normalize_upc_preserve_suffix_for_match(ss_normalization._normalize_upc(row['upc']))
            lot = ss_normalization._normalize_lot_number(row['lot_number'])
            if not upc or not lot:
                continue
            lot_options.add(lot)
            bol_lot_map.setdefault(upc, set()).add(lot)

        rows = []

        cur.execute('''
            SELECT id, upc, COALESCE(row_status, '') AS row_status, image_path, created_at
            FROM items_prep_images
            WHERE deleted_at IS NULL OR TRIM(COALESCE(deleted_at, '')) = ''
            ORDER BY created_at DESC, id DESC
        ''')
        for row in cur.fetchall():
            rel_path = str(row['image_path'] or '').strip()
            abs_path = _prep_media_cleaner_abs_path(rel_path)
            exists = bool(abs_path and os.path.isfile(abs_path))
            try:
                size_bytes = os.path.getsize(abs_path) if exists else 0
            except Exception:
                size_bytes = 0
            lots = _prep_media_cleaner_row_lots(row['upc'], row['row_status'], status_lot_map, any_status_lots, bol_lot_map)
            created_at = str(row['created_at'] or '').strip()
            rows.append({
                'row_id': int(row['id']),
                'source_table': 'items_prep_images',
                'media_type': 'image',
                'upc': str(row['upc'] or '').strip(),
                'row_status': ss_normalization._normalize_prep_row_status(row['row_status']),
                'created_at': created_at,
                'created_date': created_at[:10] if len(created_at) >= 10 else '',
                'relative_path': rel_path,
                'exists': exists,
                'size_bytes': int(size_bytes or 0),
                'size_label': _prep_media_cleaner_format_bytes(size_bytes),
                'lots': lots,
                'lot_label': lots[0] if len(lots) == 1 else (', '.join(lots[:3]) if lots else ''),
                'lot_match_safe': len(lots) == 1
            })

        cur.execute('''
            SELECT id, upc, COALESCE(row_status, '') AS row_status, COALESCE(media_type, '') AS media_type, file_path, created_at
            FROM items_prep_media
            WHERE COALESCE(media_type, '') IN ('audio', 'video')
            ORDER BY created_at DESC, id DESC
        ''')
        for row in cur.fetchall():
            media_type = str(row['media_type'] or '').strip().lower()
            rel_path = str(row['file_path'] or '').strip()
            abs_path = _prep_media_cleaner_abs_path(rel_path)
            exists = bool(abs_path and os.path.isfile(abs_path))
            try:
                size_bytes = os.path.getsize(abs_path) if exists else 0
            except Exception:
                size_bytes = 0
            lots = _prep_media_cleaner_row_lots(row['upc'], row['row_status'], status_lot_map, any_status_lots, bol_lot_map)
            created_at = str(row['created_at'] or '').strip()
            rows.append({
                'row_id': int(row['id']),
                'source_table': 'items_prep_media',
                'media_type': media_type,
                'upc': str(row['upc'] or '').strip(),
                'row_status': ss_normalization._normalize_prep_row_status(row['row_status']),
                'created_at': created_at,
                'created_date': created_at[:10] if len(created_at) >= 10 else '',
                'relative_path': rel_path,
                'exists': exists,
                'size_bytes': int(size_bytes or 0),
                'size_label': _prep_media_cleaner_format_bytes(size_bytes),
                'lots': lots,
                'lot_label': lots[0] if len(lots) == 1 else (', '.join(lots[:3]) if lots else ''),
                'lot_match_safe': len(lots) == 1
            })

        rows.sort(key=lambda item: (str(item.get('created_at') or ''), int(item.get('row_id') or 0)), reverse=True)
        return rows, sorted(lot_options, reverse=True)
    finally:
        conn.close()


def _prep_media_cleaner_filter_rows(rows, *, media_types=None, upc='', lot='', date_from='', date_to=''):
    selected_types = set(_prep_media_cleaner_allowed_types(media_types))
    upc_filter = _prep_media_cleaner_normalize_upc_filter(upc)
    upc_filter_is_exact = '-' in upc_filter if upc_filter else False
    lot_filter = ss_normalization._normalize_lot_number(lot)
    date_from = str(date_from or '').strip()
    date_to = str(date_to or '').strip()
    filtered = []
    skipped_ambiguous_lot = 0

    for row in rows:
        media_type = str(row.get('media_type') or '').strip().lower()
        if media_type not in selected_types:
            continue

        row_upc = _prep_media_cleaner_normalize_upc_filter(row.get('upc'))
        if upc_filter:
            if upc_filter_is_exact:
                if row_upc != upc_filter:
                    continue
            else:
                row_base = row_upc.split('-', 1)[0].strip()
                if row_base != upc_filter:
                    continue

        row_date = str(row.get('created_date') or '').strip()
        if date_from and (not row_date or row_date < date_from):
            continue
        if date_to and (not row_date or row_date > date_to):
            continue

        if lot_filter:
            lots = row.get('lots') or []
            if lot_filter not in lots:
                continue
            if len(lots) != 1:
                skipped_ambiguous_lot += 1
                continue

        filtered.append(row)

    return filtered, skipped_ambiguous_lot


def _prep_media_cleaner_summary(rows):
    summary = {
        'image': {'count': 0, 'bytes': 0},
        'audio': {'count': 0, 'bytes': 0},
        'video': {'count': 0, 'bytes': 0},
        'all': {'count': 0, 'bytes': 0}
    }
    for row in rows:
        media_type = str(row.get('media_type') or '').strip().lower()
        size_bytes = int(row.get('size_bytes') or 0)
        if media_type in summary:
            summary[media_type]['count'] += 1
            summary[media_type]['bytes'] += size_bytes
        summary['all']['count'] += 1
        summary['all']['bytes'] += size_bytes
    for payload in summary.values():
        payload['size_label'] = _prep_media_cleaner_format_bytes(payload['bytes'])
    return summary


def _prep_media_cleaner_delete_rows(rows):
    deleted_counts = {'image': 0, 'audio': 0, 'video': 0}
    deleted_bytes = {'image': 0, 'audio': 0, 'video': 0}
    missing_file_rows = 0
    image_ids = []
    media_ids = []

    for row in rows:
        media_type = str(row.get('media_type') or '').strip().lower()
        rel_path = str(row.get('relative_path') or '').strip()
        abs_path = _prep_media_cleaner_abs_path(rel_path)
        file_deleted = False
        if abs_path:
            try:
                if os.path.isfile(abs_path):
                    os.remove(abs_path)
                    file_deleted = True
            except Exception as exc:
                print('Failed deleting prep cleaner file', abs_path, exc)
        if file_deleted:
            deleted_bytes[media_type] = deleted_bytes.get(media_type, 0) + int(row.get('size_bytes') or 0)
        else:
            missing_file_rows += 1

        if row.get('source_table') == 'items_prep_images':
            image_ids.append(int(row['row_id']))
        elif row.get('source_table') == 'items_prep_media':
            media_ids.append(int(row['row_id']))
        deleted_counts[media_type] = deleted_counts.get(media_type, 0) + 1

    conn = sqlite3.connect('bol.db', isolation_level='IMMEDIATE')
    try:
        cur = conn.cursor()
        if image_ids:
            for chunk_start in range(0, len(image_ids), 500):
                chunk = image_ids[chunk_start:chunk_start + 500]
                placeholders = ','.join('?' for _ in chunk)
                cur.execute(f'DELETE FROM items_prep_images WHERE id IN ({placeholders})', tuple(chunk))
        if media_ids:
            for chunk_start in range(0, len(media_ids), 500):
                chunk = media_ids[chunk_start:chunk_start + 500]
                placeholders = ','.join('?' for _ in chunk)
                cur.execute(f'DELETE FROM items_prep_media WHERE id IN ({placeholders})', tuple(chunk))
        conn.commit()
    finally:
        conn.close()

    try:
        ss_caching.update_data_version()
    except Exception:
        pass

    total_deleted = sum(deleted_counts.values())
    total_bytes = sum(deleted_bytes.values())
    return {
        'deleted_count': total_deleted,
        'deleted_bytes': total_bytes,
        'deleted_size_label': _prep_media_cleaner_format_bytes(total_bytes),
        'deleted_by_type': {
            media_type: {
                'count': deleted_counts.get(media_type, 0),
                'bytes': deleted_bytes.get(media_type, 0),
                'size_label': _prep_media_cleaner_format_bytes(deleted_bytes.get(media_type, 0))
            }
            for media_type in ('image', 'audio', 'video')
        },
        'missing_file_rows': int(missing_file_rows or 0)
    }


def api_items_prep_notes_get(upc):
    """Get all notes for a UPC, ordered by created_at DESC."""
    conn = None
    try:
        upc_norm = ss_normalization._normalize_upc(upc)
        upc_n = ss_normalization._normalize_upc_preserve_suffix_for_match(upc_norm)
        row_status = ss_normalization._normalize_prep_row_status(request.args.get('row_status') or request.args.get('status'))
        scope_upc, scope_status = ss_normalization._items_to_list_asset_scope(upc_n, row_status)
        lot_number = ss_normalization._normalize_lot_number(ss_warehouse_allocations._preferred_lot_from_request(request.args))
        ss_prep_schema._ensure_items_prep_tables()
        conn = sqlite3.connect('bol.db')
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        if not ss_prep_context._items_prep_scope_has_actionable_status(cur, scope_upc, row_status, lot_number):
            return jsonify({'success': True, 'notes': []})
        if row_status or ('-' in scope_upc):
            cur.execute('''
                SELECT id, note, created_at, COALESCE(row_status, '') AS row_status
                FROM items_prep_notes
                WHERE upc = ? COLLATE NOCASE
                  AND COALESCE(row_status, '') = ? COLLATE NOCASE
                  AND TRIM(COALESCE(note, '')) != ''
                ORDER BY created_at DESC
            ''', (scope_upc, scope_status))
        else:
            cur.execute('''
                SELECT id, note, created_at, COALESCE(row_status, '') AS row_status
                FROM items_prep_notes
                WHERE upc = ? COLLATE NOCASE
                  AND TRIM(COALESCE(note, '')) != ''
                ORDER BY created_at DESC
            ''', (scope_upc,))
        notes = [dict(r) for r in cur.fetchall()]
        return jsonify({'success': True, 'notes': notes})
    except Exception as e:
        return jsonify({'success': False, 'error': ss_errors._safe_error(e)}), 500
    finally:
        if conn is not None:
            conn.close()


def api_items_prep_notes_add():
    """Add a new note for a UPC. JSON: { upc, note }"""
    conn = None
    try:
        data = request.get_json() or {}
        upc_norm = ss_normalization._normalize_upc(data.get('upc'))
        row_status = ss_normalization._normalize_prep_row_status(data.get('row_status') or data.get('status'))
        upc = ss_normalization._normalize_upc_preserve_suffix_for_match(upc_norm)
        scope_upc, scope_status = ss_normalization._items_to_list_asset_scope(upc, row_status)
        note = (data.get('note') or '').strip()
        lot_number = ss_normalization._normalize_lot_number(ss_warehouse_allocations._preferred_lot_from_request(data))
        source = ss_normalization._normalize_listing_source(data.get('source'), default='user')
        if not scope_upc or not note:
            return jsonify({'success': False, 'error': 'Missing upc or note'}), 400
        ss_prep_schema._ensure_items_prep_tables()
        import datetime
        ts = datetime.datetime.now(datetime.UTC).isoformat()
        conn = sqlite3.connect('bol.db')
        cur = conn.cursor()
        if not ss_prep_context._items_prep_scope_has_actionable_status(cur, scope_upc, row_status, lot_number):
            return jsonify({'success': False, 'error': 'Notes can only be added to Good, Bad, or Return rows.'}), 400
        # Avoid duplicate note rows for identical UPC + note payload.
        cur.execute('''
            SELECT id, created_at
            FROM items_prep_notes
            WHERE upc = ? COLLATE NOCASE
              AND COALESCE(row_status, '') = ? COLLATE NOCASE
              AND TRIM(COALESCE(note, '')) = ?
            ORDER BY created_at DESC, id DESC
            LIMIT 1
        ''', (scope_upc, scope_status, note))
        existing = cur.fetchone()
        if existing:
            return jsonify({
                'success': True,
                'id': int(existing[0]),
                'created_at': existing[1],
                'deduped': True
            })
        cur.execute(
            'INSERT INTO items_prep_notes (upc, row_status, note, created_at) VALUES (?,?,?,?)',
            (scope_upc, scope_status, note, ts)
        )
        note_id = cur.lastrowid
        conn.commit()
        try:
            ss_caching.update_data_version()
        except Exception:
            pass
        try:
            ss_listing_log._listinglog_add_entry(
                upc=scope_upc,
                platform='item_manager',
                action='note_add',
                source=source,
                success=True,
                meta={
                    'note_id': note_id,
                    'note': note,
                    'lot_number': lot_number,
                    'row_status': scope_status,
                    'changes': {'note': {'from': None, 'to': note}},
                    'via': 'api_items_prep_notes_add'
                }
            )
        except Exception:
            pass
        return jsonify({'success': True, 'id': note_id, 'created_at': ts})
    except Exception as e:
        return jsonify({'success': False, 'error': ss_errors._safe_error(e)}), 500
    finally:
        if conn is not None:
            conn.close()


def api_items_prep_notes_delete(note_id):
    """Delete a note by ID."""
    conn = None
    try:
        ss_prep_schema._ensure_items_prep_tables()
        source = ss_normalization._normalize_listing_source(request.args.get('source'), default='user')
        lot_number = ss_normalization._normalize_lot_number(ss_warehouse_allocations._preferred_lot_from_request())
        conn = sqlite3.connect('bol.db')
        cur = conn.cursor()
        cur.execute('SELECT upc, note, COALESCE(row_status, \'\') FROM items_prep_notes WHERE id = ? LIMIT 1', (note_id,))
        existing = cur.fetchone()
        note_upc = ss_normalization._normalize_upc_preserve_suffix_for_match(ss_normalization._normalize_upc(existing[0])) if existing else ''
        note_text = str(existing[1] or '').strip() if existing else ''
        note_status = ss_normalization._normalize_prep_row_status(existing[2]) if existing else ''
        cur.execute('DELETE FROM items_prep_notes WHERE id = ?', (note_id,))
        deleted = cur.rowcount
        conn.commit()
        if deleted:
            try:
                ss_caching.update_data_version()
            except Exception:
                pass
            if note_upc:
                try:
                    ss_listing_log._listinglog_add_entry(
                        upc=note_upc,
                        platform='item_manager',
                        action='note_delete',
                        source=source,
                        success=True,
                        meta={
                            'note_id': note_id,
                            'note': note_text,
                            'lot_number': lot_number,
                            'row_status': note_status,
                            'changes': {'note': {'from': note_text, 'to': None}},
                            'via': 'api_items_prep_notes_delete'
                        }
                    )
                except Exception:
                    pass
        if deleted == 0:
            return jsonify({'success': False, 'error': 'Note not found'}), 404
        return jsonify({'success': True, 'deleted': deleted})
    except Exception as e:
        return jsonify({'success': False, 'error': ss_errors._safe_error(e)}), 500
    finally:
        if conn is not None:
            conn.close()


def api_items_prep_media_get(upc):
    """Get audio/video attachments for a prep UPC."""
    conn = None
    try:
        upc_norm = ss_normalization._normalize_upc(upc)
        upc_n = ss_normalization._normalize_upc_preserve_suffix_for_match(upc_norm)
        row_status = ss_normalization._normalize_prep_row_status(request.args.get('row_status') or request.args.get('status'))
        media_type = str(request.args.get('media_type') or request.args.get('type') or '').strip().lower()
        if media_type and media_type not in ('audio', 'video'):
            return jsonify({'success': False, 'error': 'Invalid media type'}), 400
        scope_upc, scope_status = ss_normalization._items_to_list_asset_scope(upc_n, row_status)
        ss_prep_schema._ensure_items_prep_tables()
        conn = sqlite3.connect('bol.db')
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        params = []
        sql = '''
            SELECT id, upc, COALESCE(row_status, '') AS row_status, COALESCE(media_type, '') AS media_type,
                   file_path, COALESCE(mime_type, '') AS mime_type, created_at
            FROM items_prep_media
            WHERE upc = ? COLLATE NOCASE
        '''
        params.append(scope_upc)
        if row_status or ('-' in scope_upc):
            sql += " AND COALESCE(row_status, '') = ? COLLATE NOCASE"
            params.append(scope_status)
        if media_type:
            sql += " AND COALESCE(media_type, '') = ? COLLATE NOCASE"
            params.append(media_type)
        sql += " ORDER BY created_at DESC, id DESC"
        cur.execute(sql, tuple(params))
        items = [dict(r) for r in cur.fetchall()]
        return jsonify({'success': True, 'items': items})
    except Exception as e:
        return jsonify({'success': False, 'error': ss_errors._safe_error(e)}), 500
    finally:
        if conn is not None:
            conn.close()


def api_items_prep_media_add(upc):
    """Upload audio/video attachments for a prep UPC."""
    conn = None
    try:
        upc_norm = ss_normalization._normalize_upc(upc)
        upc_n = ss_normalization._normalize_upc_preserve_suffix_for_match(upc_norm)
        row_status = ss_normalization._normalize_prep_row_status(request.form.get('row_status') or request.form.get('status') or request.args.get('row_status') or request.args.get('status'))
        media_type = str(request.form.get('media_type') or request.form.get('type') or request.args.get('media_type') or request.args.get('type') or '').strip().lower()
        replace_existing = ss_normalization._coerce_bool(
            request.form.get('replace_existing') if request.form.get('replace_existing') is not None else request.args.get('replace_existing')
        )
        if media_type not in ('audio', 'video'):
            return jsonify({'success': False, 'error': 'Invalid media type'}), 400
        scope_upc, scope_status = ss_normalization._items_to_list_asset_scope(upc_n, row_status)
        if not scope_upc:
            return jsonify({'success': False, 'error': 'Missing upc'}), 400
        ss_prep_schema._ensure_items_prep_tables()
        from werkzeug.utils import secure_filename
        save_dir = os.path.join(ss_runtime.app.root_path, 'static', 'items_prep')
        os.makedirs(save_dir, exist_ok=True)
        files = request.files.getlist('media[]') or request.files.getlist('media') or ([] if 'file' not in request.files else [request.files['file']])
        if not files:
            return jsonify({'success': False, 'error': 'No files uploaded'}), 400
        import datetime
        ts = datetime.datetime.now(datetime.UTC).isoformat()
        conn = sqlite3.connect('bol.db')
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        exact_scope = row_status or ('-' in scope_upc)
        delete_scope_status = scope_status if exact_scope else None
        if replace_existing:
            _items_prep_delete_media_rows(cur, scope_upc, row_status=delete_scope_status, media_type=media_type)
        out = []
        for idx, f in enumerate(files):
            if not f or not getattr(f, 'filename', ''):
                continue
            fn = secure_filename(f.filename)
            _name, ext = os.path.splitext(fn)
            mime_type = str(getattr(f, 'mimetype', '') or '').strip()
            if not ext:
                if media_type == 'audio':
                    ext = '.webm' if 'webm' in mime_type else '.m4a'
                else:
                    ext = '.mp4' if 'mp4' in mime_type else '.webm'
            unique = f"{scope_upc}_{media_type}_{int(time.time()*1000)}_{idx}{ext}"
            abs_path = os.path.join(save_dir, unique)
            f.save(abs_path)
            rel = f"items_prep/{unique}"
            cur.execute(
                '''
                    INSERT INTO items_prep_media (upc, row_status, media_type, file_path, mime_type, created_at)
                    VALUES (?,?,?,?,?,?)
                ''',
                (scope_upc, scope_status if exact_scope else '', media_type, rel, mime_type, ts)
            )
            out.append({
                'id': int(cur.lastrowid),
                'upc': scope_upc,
                'row_status': scope_status if exact_scope else '',
                'media_type': media_type,
                'file_path': rel,
                'mime_type': mime_type,
                'created_at': ts
            })
        conn.commit()
        try:
            ss_caching.update_data_version()
        except Exception:
            pass
        return jsonify({'success': True, 'items': out})
    except Exception as e:
        return jsonify({'success': False, 'error': ss_errors._safe_error(e)}), 500
    finally:
        if conn is not None:
            conn.close()


def api_items_prep_media_delete(media_id):
    """Delete one prep audio/video attachment."""
    conn = None
    try:
        ss_prep_schema._ensure_items_prep_tables()
        conn = sqlite3.connect('bol.db')
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute('''
            SELECT id, upc, COALESCE(row_status, '') AS row_status, COALESCE(media_type, '') AS media_type, file_path
            FROM items_prep_media
            WHERE id = ?
        ''', (media_id,))
        row = cur.fetchone()
        if not row:
            return jsonify({'success': False, 'error': 'Media not found'}), 404
        rel = str(row['file_path'] or '').strip()
        if rel:
            abs_path = os.path.join(ss_runtime.app.root_path, 'static', rel) if not os.path.isabs(rel) else rel
            try:
                if os.path.isfile(abs_path):
                    os.remove(abs_path)
            except Exception as fe:
                print('Failed hard remove prep media', abs_path, fe)
        cur.execute('DELETE FROM items_prep_media WHERE id = ?', (media_id,))
        conn.commit()
        try:
            ss_caching.update_data_version()
        except Exception:
            pass
        return jsonify({'success': True, 'deleted': 1})
    except Exception as e:
        return jsonify({'success': False, 'error': ss_errors._safe_error(e)}), 500
    finally:
        if conn is not None:
            conn.close()


def api_prep_media_cleaner_overview():
    """Return prep media storage totals and lot suggestions for cleaner UI."""
    try:
        rows, lot_options = _prep_media_cleaner_load_rows()
        summary = _prep_media_cleaner_summary(rows)
        recent_by_type = {}
        for media_type in ('image', 'audio', 'video'):
            recent_rows = [row for row in rows if row.get('media_type') == media_type][:5]
            recent_by_type[media_type] = [
                {
                    'upc': row.get('upc') or '',
                    'lot_label': row.get('lot_label') or '',
                    'created_at': row.get('created_at') or '',
                    'size_bytes': int(row.get('size_bytes') or 0),
                    'size_label': row.get('size_label') or '0 B'
                }
                for row in recent_rows
            ]
        return jsonify({
            'success': True,
            'summary': summary,
            'lot_options': lot_options[:300],
            'recent_by_type': recent_by_type
        })
    except Exception as e:
        return jsonify({'success': False, 'error': ss_errors._safe_error(e, 'prep_media_cleaner_overview')}), 500


def api_prep_media_cleaner_query():
    """Return filtered prep media rows for preview/cleanup."""
    try:
        media_types = _prep_media_cleaner_allowed_types(request.args.get('media_types'))
        upc = request.args.get('upc') or ''
        lot = request.args.get('lot') or ''
        date_from = request.args.get('date_from') or ''
        date_to = request.args.get('date_to') or ''
        try:
            limit = max(1, min(500, int(request.args.get('limit') or 200)))
        except Exception:
            limit = 200

        rows, _lot_options = _prep_media_cleaner_load_rows()
        matched_rows, skipped_ambiguous_lot = _prep_media_cleaner_filter_rows(
            rows,
            media_types=media_types,
            upc=upc,
            lot=lot,
            date_from=date_from,
            date_to=date_to
        )
        summary = _prep_media_cleaner_summary(matched_rows)
        preview_rows = matched_rows[:limit]
        return jsonify({
            'success': True,
            'filters': {
                'media_types': media_types,
                'upc': str(upc or '').strip(),
                'lot': ss_normalization._normalize_lot_number(lot),
                'date_from': str(date_from or '').strip(),
                'date_to': str(date_to or '').strip()
            },
            'summary': summary,
            'total_matches': len(matched_rows),
            'displayed_matches': len(preview_rows),
            'skipped_ambiguous_lot': skipped_ambiguous_lot,
            'rows': [
                {
                    'row_id': int(row.get('row_id') or 0),
                    'source_table': row.get('source_table') or '',
                    'media_type': row.get('media_type') or '',
                    'upc': row.get('upc') or '',
                    'row_status': row.get('row_status') or '',
                    'created_at': row.get('created_at') or '',
                    'size_bytes': int(row.get('size_bytes') or 0),
                    'size_label': row.get('size_label') or '0 B',
                    'lot_label': row.get('lot_label') or '',
                    'lots': row.get('lots') or [],
                    'exists': bool(row.get('exists')),
                    'relative_path': row.get('relative_path') or ''
                }
                for row in preview_rows
            ]
        })
    except Exception as e:
        return jsonify({'success': False, 'error': ss_errors._safe_error(e, 'prep_media_cleaner_query')}), 500


def api_prep_media_cleaner_delete():
    """Hard-delete prep photos/audio/video by current filter scope."""
    try:
        data = request.get_json(silent=True) or {}
        media_types = _prep_media_cleaner_allowed_types(data.get('media_types'))
        upc = data.get('upc') or ''
        lot = data.get('lot') or ''
        date_from = data.get('date_from') or ''
        date_to = data.get('date_to') or ''

        rows, _lot_options = _prep_media_cleaner_load_rows()
        matched_rows, skipped_ambiguous_lot = _prep_media_cleaner_filter_rows(
            rows,
            media_types=media_types,
            upc=upc,
            lot=lot,
            date_from=date_from,
            date_to=date_to
        )
        if not matched_rows:
            return jsonify({
                'success': False,
                'error': 'No matching prep media found for this cleanup request.',
                'skipped_ambiguous_lot': skipped_ambiguous_lot
            }), 400

        delete_result = _prep_media_cleaner_delete_rows(matched_rows)
        return jsonify({
            'success': True,
            'filters': {
                'media_types': media_types,
                'upc': str(upc or '').strip(),
                'lot': ss_normalization._normalize_lot_number(lot),
                'date_from': str(date_from or '').strip(),
                'date_to': str(date_to or '').strip()
            },
            'skipped_ambiguous_lot': skipped_ambiguous_lot,
            **delete_result
        })
    except Exception as e:
        return jsonify({'success': False, 'error': ss_errors._safe_error(e, 'prep_media_cleaner_delete')}), 500


def api_items_prep_diagnostic_photos_zip(upc):
    """Bundle all diagnostic photos for a UPC into a zip and return as attachment."""
    conn = None
    try:
        upc_n = ss_normalization._normalize_upc_preserve_suffix_for_match(ss_normalization._normalize_upc(upc))
        if not upc_n:
            return jsonify({'error': 'Missing upc'}), 400
        row_status = ss_normalization._normalize_prep_row_status(request.args.get('row_status') or request.args.get('status'))
        scope_upc, scope_status = ss_normalization._items_to_list_asset_scope(upc_n, row_status)
        ss_prep_schema._ensure_items_prep_tables()
        conn = sqlite3.connect('bol.db')
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        if row_status or ('-' in scope_upc):
            cur.execute('''
                SELECT image_path
                FROM items_prep_images
                WHERE upc = ? COLLATE NOCASE
                  AND COALESCE(row_status, '') = ? COLLATE NOCASE
                  AND (deleted_at IS NULL OR TRIM(COALESCE(deleted_at,'')) = '')
                ORDER BY created_at DESC, id DESC
            ''', (scope_upc, scope_status))
        else:
            cur.execute('''
                SELECT image_path
                FROM items_prep_images
                WHERE upc = ? COLLATE NOCASE
                  AND (deleted_at IS NULL OR TRIM(COALESCE(deleted_at,'')) = '')
                ORDER BY created_at DESC, id DESC
            ''', (scope_upc,))
        rows = cur.fetchall()
        if not rows:
            return jsonify({'error': 'No photos for this UPC'}), 404
        import zipfile
        mem = io.BytesIO()
        with zipfile.ZipFile(mem, mode='w', compression=zipfile.ZIP_DEFLATED) as zf:
            for r in rows:
                rel = r['image_path']
                # Build absolute path
                abs_path = os.path.join(ss_runtime.app.root_path, 'static', rel.replace('..','').replace('\\','/').split('static/')[-1]) if not os.path.isabs(rel) else rel
                # Fix double static if rel already starts with items_prep/
                if not os.path.isabs(rel):
                    abs_path = os.path.join(ss_runtime.app.root_path, 'static', rel)
                try:
                    # Name inside zip: use basename
                    arcname = os.path.basename(abs_path)
                    if os.path.isfile(abs_path):
                        zf.write(abs_path, arcname)
                except Exception as e:
                    print('Zip add failed:', e)
        mem.seek(0)
        filename = f"diagnostic_{scope_upc}{('_' + scope_status) if scope_status else ''}.zip"
        return send_file(mem, mimetype='application/zip', as_attachment=True, download_name=filename)
    except Exception as e:
        return jsonify({'error': ss_errors._safe_error(e)}), 500
    finally:
        try:
            if conn is not None:
                conn.close()
        except Exception:
            pass
