"""Prep undo for Sweet Shelves."""

import sqlite3
from flask import jsonify, request
from . import (
    caching as ss_caching, errors as ss_errors, normalization as ss_normalization, prep_log as
    ss_prep_log, prep_media as ss_prep_media, prep_schema as ss_prep_schema, warehouse_allocations as
    ss_warehouse_allocations, warehouse_matching as ss_warehouse_matching,
)


def _items_prep_undo_core(*, upc, status, qty=1, base_upc=None, lot_number=None, action=None):
    """Shared undo logic for Item Prep actions.

    Returns: (ok: bool, error: str|None, http_status: int)
    """
    try:
        upc = (upc or '').strip()
        status = (status or '').strip().lower()
        action = (action or '').strip().lower()
        base_upc = (base_upc or '').strip() or None
        lot_number = ss_normalization._normalize_lot_number(lot_number)

        if not upc or status not in ('good', 'bad', 'return'):
            return False, 'Missing upc or invalid status', 400

        try:
            qty = int(qty or 1)
        except Exception:
            qty = 1
        if qty < 1:
            qty = 1

        upc = ss_normalization._normalize_upc_preserve_suffix_for_match(ss_normalization._normalize_upc(upc))
        if base_upc:
            base_upc = ss_normalization._strip_leading_zeros_numeric(ss_normalization._normalize_upc(base_upc))
        elif '-' in upc and upc.rsplit('-', 1)[-1].isdigit():
            base_upc = upc.split('-', 1)[0]

        ss_prep_schema._ensure_items_prep_tables()
        conn = sqlite3.connect('bol.db')
        cur = conn.cursor()
        try:
            if not lot_number and ss_warehouse_allocations._has_multiple_lot_rows(cur, upc):
                return False, (
                    f'Cannot undo {upc} without LOT context. '
                    'This UPC exists in multiple LOTs.'
                ), 400

            def _pick_bol_row(target_upc, cols):
                if lot_number:
                    cur.execute(f'''
                        SELECT {cols}
                        FROM bol_items
                        WHERE upc = ? COLLATE NOCASE
                          AND lot_number = ? COLLATE NOCASE
                        ORDER BY import_date DESC, id DESC
                        LIMIT 1
                    ''', (target_upc, lot_number))
                    row = cur.fetchone()
                    if row:
                        return row
                    cur.execute(f'''
                        SELECT {cols}
                        FROM bol_items
                        WHERE upc = ? COLLATE NOCASE
                          AND COALESCE(lot_number, '') = ''
                        ORDER BY import_date DESC, id DESC
                        LIMIT 1
                    ''', (target_upc,))
                    row = cur.fetchone()
                    if row:
                        return row
                    # When lot context is provided, do not mutate a different lot row.
                    return None
                cur.execute(f'''
                    SELECT {cols}
                    FROM bol_items
                    WHERE upc = ? COLLATE NOCASE
                    ORDER BY import_date DESC, id DESC
                    LIMIT 1
                ''', (target_upc,))
                return cur.fetchone()

            if status == 'good':
                is_suffixed = ('-' in upc and upc.rsplit('-', 1)[-1].isdigit())
                if is_suffixed and action in ('saved_good_suffixed', 'special_good_suffixed', 'good_suffixed', 'history_delete'):
                    row = _pick_bol_row(upc, 'id, lot_number, good_qty, unchecked_qty, quantity')
                    if not row:
                        return False, f'UPC {upc} not found', 404

                    bol_id = row[0]
                    row_lot = ss_normalization._normalize_lot_number(row[1])
                    current_good = int(row[2] or 0)
                    current_unchecked = int(row[3] or 0)
                    row_qty = int(row[4] or 0)
                    if current_good <= 0:
                        current_good = row_qty

                    if qty > current_good:
                        return False, f'Cannot undo {qty} - only {current_good} marked as good', 400

                    undo_qty = qty
                    cur.execute('DELETE FROM bol_items WHERE id = ?', (bol_id,))
                    cur.execute('''
                        DELETE FROM items_prep_status
                        WHERE upc = ? COLLATE NOCASE
                          AND (
                            COALESCE(lot_number, '') = ? COLLATE NOCASE
                            OR (? <> '' AND COALESCE(lot_number, '') = '')
                          )
                    ''', (upc, row_lot, row_lot))
                    ss_prep_media._items_prep_delete_diagnostic_assets(cur, upc)

                    base_key = (base_upc or '').strip() or upc.split('-', 1)[0]
                    if row_lot:
                        cur.execute('''
                            SELECT id, good_qty, unchecked_qty
                            FROM bol_items
                            WHERE upc = ? COLLATE NOCASE
                              AND lot_number = ? COLLATE NOCASE
                            ORDER BY import_date DESC, id DESC
                            LIMIT 1
                        ''', (base_key, row_lot))
                        base_row = cur.fetchone()
                    else:
                        cur.execute('''
                            SELECT id, good_qty, unchecked_qty
                            FROM bol_items
                            WHERE upc = ? COLLATE NOCASE
                            ORDER BY import_date DESC, id DESC
                            LIMIT 1
                        ''', (base_key,))
                        base_row = cur.fetchone()

                    if base_row:
                        base_id = int(base_row[0])
                        base_good = int(base_row[1] or 0)
                        base_unchecked = int(base_row[2] or 0)
                        new_base_good = max(0, base_good - undo_qty)
                        new_base_unchecked = base_unchecked + undo_qty
                        cur.execute('''
                            UPDATE bol_items
                            SET good_qty = ?, unchecked_qty = ?
                            WHERE id = ?
                        ''', (new_base_good, new_base_unchecked, base_id))
                        print(
                            f'[UNDO GOOD SUFFIX] Deleted {upc} (lot={row_lot}); '
                            f'base {base_key}: good {base_good}->{new_base_good}, '
                            f'unchecked {base_unchecked}->{new_base_unchecked}'
                        )
                    else:
                        print(f'[UNDO GOOD SUFFIX] Warning: base UPC {base_key} not found to restore quantity')
                else:
                    row = _pick_bol_row(upc, 'id, lot_number, good_qty, unchecked_qty')
                    if not row:
                        return False, f'UPC {upc} not found', 404

                    bol_id = row[0]
                    row_lot = ss_normalization._normalize_lot_number(row[1])
                    current_good = row[2] or 0
                    current_unchecked = row[3] or 0

                    if qty > current_good:
                        return False, f'Cannot undo {qty} - only {current_good} marked as good', 400

                    new_good = current_good - qty
                    new_unchecked = current_unchecked + qty

                    cur.execute('''
                        UPDATE bol_items
                        SET good_qty = ?, unchecked_qty = ?
                        WHERE id = ?
                    ''', (new_good, new_unchecked, bol_id))

                    if new_good > 0:
                        cur.execute('''
                            UPDATE items_prep_status
                            SET quantity = ?
                            WHERE upc = ? COLLATE NOCASE
                              AND COALESCE(lot_number, '') = ? COLLATE NOCASE
                        ''', (new_good, upc, row_lot))
                        if cur.rowcount == 0 and row_lot:
                            cur.execute('''
                                UPDATE items_prep_status
                                SET quantity = ?
                                WHERE upc = ? COLLATE NOCASE
                                  AND COALESCE(lot_number, '') = ''
                            ''', (new_good, upc))
                    else:
                        cur.execute('''
                            DELETE FROM items_prep_status
                            WHERE upc = ? COLLATE NOCASE
                              AND (
                                COALESCE(lot_number, '') = ? COLLATE NOCASE
                                OR (? <> '' AND COALESCE(lot_number, '') = '')
                              )
                        ''', (upc, row_lot, row_lot))

                    print(f'[UNDO GOOD] {upc} (lot={row_lot}): moved {qty} from good to unchecked (good: {current_good}→{new_good}, unchecked: {current_unchecked}→{new_unchecked})')

            elif status == 'bad':
                row = _pick_bol_row(upc, 'id, lot_number, quantity, temporary')
                if not row:
                    return False, f'UPC {upc} not found', 404

                bol_id = row[0]
                row_lot = ss_normalization._normalize_lot_number(row[1])
                bad_qty_for_this_entry = row[2] if row[2] else 1
                temporary = row[3]

                cur.execute('DELETE FROM bol_items WHERE id = ?', (bol_id,))
                cur.execute('''
                    DELETE FROM items_prep_status
                    WHERE upc = ? COLLATE NOCASE
                      AND (
                        COALESCE(lot_number, '') = ? COLLATE NOCASE
                        OR (? <> '' AND COALESCE(lot_number, '') = '')
                      )
                ''', (upc, row_lot, row_lot))

                if temporary == 0:
                    ss_prep_media._items_prep_delete_diagnostic_assets(cur, upc)

                if base_upc:
                    base_lot = ss_normalization._normalize_lot_number(row_lot or lot_number)
                    if base_lot:
                        cur.execute('''
                            SELECT id, bad_qty, unchecked_qty
                            FROM bol_items
                            WHERE upc = ? COLLATE NOCASE
                              AND lot_number = ? COLLATE NOCASE
                            ORDER BY import_date DESC, id DESC
                            LIMIT 1
                        ''', (base_upc, base_lot))
                        base_row = cur.fetchone()
                    else:
                        cur.execute('''
                            SELECT id, bad_qty, unchecked_qty
                            FROM bol_items
                            WHERE upc = ? COLLATE NOCASE
                            ORDER BY import_date DESC, id DESC
                            LIMIT 1
                        ''', (base_upc,))
                        base_row = cur.fetchone()

                    if base_row:
                        base_id = base_row[0]
                        current_bad = base_row[1] or 0
                        current_unchecked = base_row[2] or 0
                        new_bad = max(0, current_bad - bad_qty_for_this_entry)
                        new_unchecked = current_unchecked + bad_qty_for_this_entry
                        cur.execute('''
                            UPDATE bol_items
                            SET bad_qty = ?, unchecked_qty = ?
                            WHERE id = ?
                        ''', (new_bad, new_unchecked, base_id))
                        print(f'[UNDO BAD] Deleted {upc}, {base_upc} (lot={base_lot}): moved {bad_qty_for_this_entry} from bad to unchecked (bad: {current_bad}→{new_bad}, unchecked: {current_unchecked}→{new_unchecked})')
                else:
                    print(f'[UNDO BAD] Warning: No base_upc provided for {upc}, could not restore qty')

            elif status == 'return':
                row = _pick_bol_row(upc, 'id, lot_number, temporary, quantity, good_qty, original_qty')
                if not row:
                    return False, f'UPC {upc} not found', 404

                bol_id = row[0]
                row_lot = ss_normalization._normalize_lot_number(row[1])
                temporary = row[2]
                entry_qty = int(row[3] or 0)
                if entry_qty <= 0:
                    entry_qty = int(row[4] or 0)
                if entry_qty <= 0:
                    entry_qty = int(row[5] or 0)
                if entry_qty <= 0:
                    entry_qty = int(qty or 1)
                if entry_qty < 1:
                    entry_qty = 1
                cur.execute('DELETE FROM bol_items WHERE id = ?', (bol_id,))
                cur.execute('''
                    DELETE FROM items_prep_status
                    WHERE upc = ? COLLATE NOCASE
                      AND (
                        COALESCE(lot_number, '') = ? COLLATE NOCASE
                        OR (? <> '' AND COALESCE(lot_number, '') = '')
                      )
                ''', (upc, row_lot, row_lot))
                if temporary == 0:
                    ss_prep_media._items_prep_delete_diagnostic_assets(cur, upc)

                if base_upc:
                    base_lot = ss_normalization._normalize_lot_number(row_lot or lot_number)
                    if base_lot:
                        cur.execute('''
                            SELECT id, unchecked_qty, original_qty, good_qty, bad_qty
                            FROM bol_items
                            WHERE upc = ? COLLATE NOCASE
                              AND lot_number = ? COLLATE NOCASE
                            ORDER BY import_date DESC, id DESC
                            LIMIT 1
                        ''', (base_upc, base_lot))
                        base_row = cur.fetchone()
                    else:
                        cur.execute('''
                            SELECT id, unchecked_qty, original_qty, good_qty, bad_qty
                            FROM bol_items
                            WHERE upc = ? COLLATE NOCASE
                            ORDER BY import_date DESC, id DESC
                            LIMIT 1
                        ''', (base_upc,))
                        base_row = cur.fetchone()

                    if base_row:
                        base_id = int(base_row[0])
                        base_unchecked_raw = base_row[1]
                        if base_unchecked_raw is None:
                            base_original = int(base_row[2] or 0)
                            base_good = int(base_row[3] or 0)
                            base_bad = int(base_row[4] or 0)
                            base_unchecked = max(0, base_original - base_good - base_bad)
                        else:
                            base_unchecked = int(base_unchecked_raw or 0)
                        new_base_unchecked = base_unchecked + entry_qty
                        cur.execute('''
                            UPDATE bol_items
                            SET unchecked_qty = ?
                            WHERE id = ?
                        ''', (new_base_unchecked, base_id))
                        print(
                            f'[UNDO RETURN] Deleted {upc}, restored {entry_qty} unchecked to {base_upc} '
                            f'(lot={base_lot}, unchecked {base_unchecked}->{new_base_unchecked})'
                        )
                    else:
                        print(f'[UNDO RETURN] Warning: base UPC {base_upc} not found to restore unchecked quantity')
                else:
                    print(f'[UNDO RETURN] Warning: No base_upc provided for {upc}, could not restore unchecked qty')

            conn.commit()
            try:
                ss_caching.update_data_version()
            except Exception:
                pass
            return True, None, 200
        except Exception as e:
            try:
                conn.rollback()
            except Exception:
                pass
            return False, ss_errors._safe_error(e, 'items_prep:undo_core'), 500
        finally:
            conn.close()
    except Exception as e:
        return False, ss_errors._safe_error(e, 'items_prep:undo_core_outer'), 500


def _items_prep_delete_orphan_history_status(*, upc, status, lot_number='', undo_error=''):
    """Delete an old prep-status row only when there is no live BOL state to restore."""
    upc_n = ss_normalization._normalize_upc_preserve_suffix_for_match(ss_normalization._normalize_upc(upc))
    status_n = ss_normalization._normalize_prep_row_status(status)
    lot_n = ss_normalization._normalize_lot_number(lot_number)
    if not upc_n or status_n not in ('good', 'bad', 'return'):
        return {'success': False, 'error': 'Missing upc or invalid status', 'status_code': 400}

    conn = None
    try:
        ss_prep_schema._ensure_items_prep_tables()
        conn = sqlite3.connect('bol.db')
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()

        status_row = None
        if lot_n:
            cur.execute('''
                SELECT id, upc, status, quantity, lot_number
                FROM items_prep_status
                WHERE upc = ? COLLATE NOCASE
                  AND COALESCE(status, '') = ? COLLATE NOCASE
                  AND COALESCE(lot_number, '') = ? COLLATE NOCASE
                ORDER BY updated_at DESC, id DESC
                LIMIT 1
            ''', (upc_n, status_n, lot_n))
            status_row = cur.fetchone()
            if not status_row:
                cur.execute('''
                    SELECT id, upc, status, quantity, lot_number
                    FROM items_prep_status
                    WHERE upc = ? COLLATE NOCASE
                      AND COALESCE(status, '') = ? COLLATE NOCASE
                      AND COALESCE(lot_number, '') = ''
                    ORDER BY updated_at DESC, id DESC
                    LIMIT 1
                ''', (upc_n, status_n))
                status_row = cur.fetchone()
        else:
            cur.execute('''
                SELECT id, upc, status, quantity, lot_number
                FROM items_prep_status
                WHERE upc = ? COLLATE NOCASE
                  AND COALESCE(status, '') = ? COLLATE NOCASE
                ORDER BY updated_at DESC, id DESC
                LIMIT 1
            ''', (upc_n, status_n))
            status_row = cur.fetchone()

        if not status_row:
            return {'success': False, 'error': undo_error or 'Prep history row not found', 'status_code': 404}

        row_lot = ss_normalization._normalize_lot_number(status_row['lot_number'])

        def _pick_bol_restore_row(cols):
            if row_lot:
                cur.execute(f'''
                    SELECT {cols}
                    FROM bol_items
                    WHERE upc = ? COLLATE NOCASE
                      AND lot_number = ? COLLATE NOCASE
                    ORDER BY import_date DESC, id DESC
                    LIMIT 1
                ''', (upc_n, row_lot))
                row = cur.fetchone()
                if row:
                    return row
                cur.execute(f'''
                    SELECT {cols}
                    FROM bol_items
                    WHERE upc = ? COLLATE NOCASE
                      AND COALESCE(lot_number, '') = ''
                    ORDER BY import_date DESC, id DESC
                    LIMIT 1
                ''', (upc_n,))
                return cur.fetchone()
            cur.execute(f'''
                SELECT {cols}
                FROM bol_items
                WHERE upc = ? COLLATE NOCASE
                ORDER BY import_date DESC, id DESC
                LIMIT 1
            ''', (upc_n,))
            return cur.fetchone()

        has_live_restore_state = False
        if status_n == 'good':
            bol_row = _pick_bol_restore_row('id, good_qty, quantity')
            has_live_restore_state = bool(
                bol_row and (
                    int(bol_row['good_qty'] or 0) > 0 or
                    ('-' in upc_n and int(bol_row['quantity'] or 0) > 0)
                )
            )
        else:
            has_live_restore_state = bool(_pick_bol_restore_row('id'))

        if has_live_restore_state:
            return {
                'success': False,
                'error': undo_error or 'Prep history row still has live quantity/state to restore',
                'status_code': 400
            }

        cur.execute('DELETE FROM items_prep_status WHERE id = ?', (int(status_row['id']),))
        conn.commit()
        try:
            ss_caching.update_data_version()
        except Exception:
            pass
        return {
            'success': True,
            'status_row_deleted_only': True,
            'undo_error': undo_error or '',
            'deleted_status_id': int(status_row['id']),
            'lot_number': row_lot
        }
    except Exception as e:
        try:
            if conn is not None:
                conn.rollback()
        except Exception:
            pass
        return {
            'success': False,
            'error': ss_errors._safe_error(e, 'items_prep:orphan_history_status_delete'),
            'status_code': 500
        }
    finally:
        if conn is not None:
            conn.close()


def api_items_prep_undo():
    """Undo the last item prep action.
    For Good items: Decrement status qty (or delete if qty becomes 0), increment base UPC qty
    For Bad items: Delete suffixed entry from bol_items if temporary=1 (not completed yet)
                   or if temporary=0 (completed), restore base qty and delete suffixed entry
    """
    try:
        data = request.get_json() or {}
        upc = data.get('upc')
        status = data.get('status')
        action = data.get('action')
        base_upc = data.get('base_upc')
        qty = data.get('qty', 1)
        lot_number = ss_warehouse_allocations._preferred_lot_from_request(data)

        ok, err, code = _items_prep_undo_core(
            upc=upc,
            status=status,
            qty=qty,
            base_upc=base_upc,
            lot_number=lot_number,
            action=action
        )
        if not ok:
            return jsonify({'success': False, 'error': err or 'Undo failed'}), (code or 400)
        return jsonify({'success': True})
        
    except Exception as e:
        print(f'Undo error: {e}')
        return jsonify({'success': False, 'error': ss_errors._safe_error(e)}), 500


def api_items_prep_history_entry_delete():
    """Delete a prep-history row and restore its quantity/state."""
    conn = None
    try:
        data = request.get_json() or {}
        upc = ss_normalization._normalize_upc_preserve_suffix_for_match(ss_normalization._normalize_upc(data.get('upc')))
        status = ss_normalization._normalize_prep_row_status(data.get('status'))
        if status not in ('good', 'bad', 'return'):
            return jsonify({'success': False, 'error': 'Invalid prep status'}), 400
        if not upc:
            return jsonify({'success': False, 'error': 'Missing upc'}), 400

        try:
            qty = int(data.get('quantity', data.get('qty', 1)) or 1)
        except Exception:
            qty = 1
        if qty < 1:
            qty = 1

        base_upc = ss_normalization._strip_leading_zeros_numeric(ss_normalization._normalize_upc(data.get('base_upc')))
        if not base_upc:
            base_upc = upc.split('-', 1)[0] if '-' in upc else upc
        lot_number = ss_normalization._normalize_lot_number(ss_warehouse_allocations._preferred_lot_from_request(data))
        legacy_inventory = ss_normalization._coerce_bool(data.get('legacy_inventory'))

        if legacy_inventory:
            legacy_result = ss_warehouse_matching._items_prep_delete_legacy_inventory_entry(
                upc=upc,
                qty=qty,
                row_ids=data.get('inventory_row_ids') or data.get('row_ids') or []
            )
            if not legacy_result.get('success'):
                return jsonify({
                    'success': False,
                    'error': legacy_result.get('error') or 'Failed to delete older inventory entry'
                }), int(legacy_result.get('status_code') or 400)
            return jsonify({
                'success': True,
                'upc': upc,
                'status': status,
                'legacy_inventory_deleted': True,
                'inventory_removed_qty': legacy_result.get('removed_qty') or qty,
                'removed_rows': legacy_result.get('removed_rows') or [],
                'assets_deleted': {'images': 0, 'notes': 0, 'media': 0},
                'assets_preserved_shared': False,
                'preplog_sync': {
                    'requested_qty': qty,
                    'consumed_qty': 0,
                    'remaining_qty': 0,
                    'matched_rows': 0,
                    'reduced_rows': [],
                    'undone_rows': [],
                    'skipped': 'legacy_inventory'
                }
            })

        ok, err, code = _items_prep_undo_core(
            upc=upc,
            status=status,
            qty=qty,
            base_upc=base_upc,
            lot_number=lot_number,
            action='history_delete'
        )
        if not ok:
            orphan_delete = None
            if (code or 400) in (400, 404):
                orphan_delete = _items_prep_delete_orphan_history_status(
                    upc=upc,
                    status=status,
                    lot_number=lot_number,
                    undo_error=err or ''
                )
            if not orphan_delete or not orphan_delete.get('success'):
                return jsonify({
                    'success': False,
                    'error': (orphan_delete or {}).get('error') or err or 'Delete failed'
                }), int((orphan_delete or {}).get('status_code') or code or 400)
        else:
            orphan_delete = None

        ss_prep_schema._ensure_items_prep_tables()
        asset_scope_upc, asset_scope_status = ss_normalization._items_to_list_asset_scope(upc, status)
        assets_deleted = {'images': 0, 'notes': 0, 'media': 0}
        assets_preserved_shared = False
        preplog_sync = {
            'requested_qty': qty,
            'consumed_qty': 0,
            'remaining_qty': qty,
            'matched_rows': 0,
            'reduced_rows': [],
            'undone_rows': []
        }

        try:
            preplog_sync = ss_prep_log._preplog_sync_downstream_delete(
                upc=upc,
                status=status,
                qty=qty,
                lot_number=lot_number,
                base_upc=base_upc
            )
        except Exception:
            preplog_sync = {
                'requested_qty': qty,
                'consumed_qty': 0,
                'remaining_qty': qty,
                'matched_rows': 0,
                'reduced_rows': [],
                'undone_rows': [],
                'warning': 'prep_log sync failed'
            }

        if asset_scope_upc:
            conn = sqlite3.connect('bol.db')
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()

            should_delete_assets = True
            delete_scope_status = None
            if '-' not in asset_scope_upc and asset_scope_status:
                cur.execute('''
                    SELECT COUNT(*)
                    FROM items_prep_status
                    WHERE upc = ? COLLATE NOCASE
                      AND COALESCE(status, '') = ? COLLATE NOCASE
                ''', (asset_scope_upc, asset_scope_status))
                remaining_rows = int((cur.fetchone() or [0])[0] or 0)
                if remaining_rows > 0:
                    should_delete_assets = False
                    assets_preserved_shared = True
                else:
                    delete_scope_status = asset_scope_status

            if should_delete_assets:
                assets_deleted = ss_prep_media._items_prep_delete_diagnostic_assets(
                    cur,
                    asset_scope_upc,
                    row_status=delete_scope_status
                )
                conn.commit()
                try:
                    ss_caching.update_data_version()
                except Exception:
                    pass

        return jsonify({
            'success': True,
            'upc': upc,
            'status': status,
            'assets_deleted': assets_deleted,
            'assets_preserved_shared': assets_preserved_shared,
            'status_deleted_only': bool(orphan_delete and orphan_delete.get('status_row_deleted_only')),
            'orphan_delete': orphan_delete or {},
            'preplog_sync': preplog_sync
        })

    except Exception as e:
        return jsonify({'success': False, 'error': ss_errors._safe_error(e, 'items_prep_history_delete')}), 500
    finally:
        if conn is not None:
            conn.close()
