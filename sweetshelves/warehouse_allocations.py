"""Warehouse allocations for Sweet Shelves."""

import sqlite3
from flask import request, session
from . import (
    normalization as ss_normalization, shipping_identity as ss_shipping_identity, warehouse_matching as
    ss_warehouse_matching,
)


def _load_order_removal_allocations(cur, order_row_id):
    ss_shipping_identity._ensure_order_removal_allocations_table(cur)
    cur.execute('''
        SELECT location_key, location_code, quantity
        FROM order_removal_allocations
        WHERE order_row_id = ?
        ORDER BY id ASC
    ''', (order_row_id,))
    rows = cur.fetchall()
    out = []
    for r in rows:
        location_code = ss_warehouse_matching._sold_location_label(r['location_code'] if isinstance(r, sqlite3.Row) else r[1])
        location_key_raw = (r['location_key'] if isinstance(r, sqlite3.Row) else r[0]) or location_code
        qty_raw = (r['quantity'] if isinstance(r, sqlite3.Row) else r[2])
        qty = max(0, ss_normalization._coerce_int(qty_raw, 0))
        if qty <= 0:
            continue
        out.append({
            'location_key': ss_warehouse_matching._sold_location_key(location_key_raw),
            'location_code': location_code,
            'quantity': qty
        })
    return out


def _save_order_removal_allocations(cur, order_row_id, allocations):
    ss_shipping_identity._ensure_order_removal_allocations_table(cur)
    cur.execute('DELETE FROM order_removal_allocations WHERE order_row_id = ?', (order_row_id,))
    for alloc in (allocations or []):
        qty = max(0, ss_normalization._coerce_int(alloc.get('quantity'), 0))
        if qty <= 0:
            continue
        location_code = ss_warehouse_matching._sold_location_label(alloc.get('location_code'))
        location_key = ss_warehouse_matching._sold_location_key(alloc.get('location_key') or location_code)
        cur.execute('''
            INSERT INTO order_removal_allocations (order_row_id, location_key, location_code, quantity)
            VALUES (?, ?, ?, ?)
        ''', (order_row_id, location_key, location_code, qty))


def _request_lot_scope_is_all(data=None):
    scope = ''
    if isinstance(data, dict):
        scope = str(data.get('lot_scope') or data.get('scope') or '').strip().lower()
    if not scope:
        try:
            scope = str(request.args.get('lot_scope') or request.form.get('lot_scope') or '').strip().lower()
        except Exception:
            scope = ''
    return scope == 'all'


def _preferred_lot_from_request(data=None):
    if _request_lot_scope_is_all(data):
        return ''
    lot = ''
    if isinstance(data, dict):
        lot = ss_normalization._normalize_lot_number(data.get('lot_number') or data.get('lot'))
    if not lot:
        try:
            lot = ss_normalization._normalize_lot_number(request.args.get('lot_number') or request.args.get('lot'))
        except Exception:
            lot = ''
    if not lot:
        try:
            lot = ss_normalization._normalize_lot_number(session.get('selected_lot'))
        except Exception:
            lot = ''
    return lot


def _select_prep_status_row(cur, upc, lot_number='', columns='*'):
    """Fetch prep status row for (upc, lot) with fallback to legacy lotless rows."""
    upc_n = ss_normalization._normalize_upc_preserve_suffix_for_match(upc)
    if not upc_n:
        return None
    lot_n = ss_normalization._normalize_lot_number(lot_number)
    if lot_n:
        cur.execute(
            f'''SELECT {columns}
                FROM items_prep_status
                WHERE upc = ? COLLATE NOCASE
                  AND COALESCE(lot_number, '') = ? COLLATE NOCASE
                LIMIT 1''',
            (upc_n, lot_n)
        )
        row = cur.fetchone()
        if row:
            return row
    cur.execute(
        f'''SELECT {columns}
            FROM items_prep_status
            WHERE upc = ? COLLATE NOCASE
              AND COALESCE(lot_number, '') = ''
            LIMIT 1''',
        (upc_n,)
    )
    return cur.fetchone()


def _resolve_prep_status_lot(cur, upc, preferred_lot=''):
    """Return the lot key that should be used for UPDATE/DELETE on prep status."""
    upc_n = ss_normalization._normalize_upc_preserve_suffix_for_match(upc)
    lot_n = ss_normalization._normalize_lot_number(preferred_lot)
    if not upc_n:
        return lot_n
    if lot_n:
        cur.execute('''
            SELECT 1
            FROM items_prep_status
            WHERE upc = ? COLLATE NOCASE
              AND COALESCE(lot_number, '') = ? COLLATE NOCASE
            LIMIT 1
        ''', (upc_n, lot_n))
        if cur.fetchone():
            return lot_n
    cur.execute('''
        SELECT 1
        FROM items_prep_status
        WHERE upc = ? COLLATE NOCASE
          AND COALESCE(lot_number, '') = ''
        LIMIT 1
    ''', (upc_n,))
    if cur.fetchone():
        return ''
    return lot_n


def _resolve_bol_lot_for_upc(cur, upc, preferred_lot=''):
    """Resolve the most relevant lot for a UPC, preferring explicit/session lot."""
    upc_n = ss_normalization._normalize_upc_preserve_suffix_for_match(upc)
    if not upc_n:
        return ''
    lot_n = ss_normalization._normalize_lot_number(preferred_lot)
    if lot_n:
        cur.execute('''
            SELECT lot_number
            FROM bol_items
            WHERE upc = ? COLLATE NOCASE
              AND lot_number = ? COLLATE NOCASE
            ORDER BY import_date DESC, id DESC
            LIMIT 1
        ''', (upc_n, lot_n))
        row = cur.fetchone()
        if row and row[0]:
            return ss_normalization._normalize_lot_number(row[0])
    cur.execute('''
        SELECT lot_number
        FROM bol_items
        WHERE upc = ? COLLATE NOCASE
        ORDER BY import_date DESC, id DESC
        LIMIT 1
    ''', (upc_n,))
    row = cur.fetchone()
    return ss_normalization._normalize_lot_number(row[0] if row else lot_n)


def _select_canonical_bol_row(cur, upc, lot_number='', columns='id, upc, lot_number', strict_lot=False):
    """Return newest canonical bol_items row for a UPC (+ optional lot)."""
    upc_n = ss_normalization._normalize_upc_preserve_suffix_for_match(upc)
    if not upc_n:
        return None
    lot_n = ss_normalization._normalize_lot_number(lot_number)
    if lot_n:
        cur.execute(
            f'''SELECT {columns}
                FROM bol_items
                WHERE upc = ? COLLATE NOCASE
                  AND lot_number = ? COLLATE NOCASE
                ORDER BY import_date DESC, id DESC
                LIMIT 1''',
            (upc_n, lot_n)
        )
        row = cur.fetchone()
        if row:
            return row
        if strict_lot:
            return None
    cur.execute(
        f'''SELECT {columns}
            FROM bol_items
            WHERE upc = ? COLLATE NOCASE
            ORDER BY import_date DESC, id DESC
            LIMIT 1''',
        (upc_n,)
    )
    return cur.fetchone()


def _has_multiple_lot_rows(cur, upc):
    """True when a UPC exists in more than one distinct LOT in bol_items."""
    upc_n = ss_normalization._normalize_upc_preserve_suffix_for_match(upc)
    if not upc_n:
        return False
    cur.execute('''
        SELECT COUNT(DISTINCT COALESCE(lot_number, ''))
        FROM bol_items
        WHERE upc = ? COLLATE NOCASE
    ''', (upc_n,))
    row = cur.fetchone()
    lot_count = int((row[0] if row else 0) or 0)
    return lot_count > 1


def _resolve_action_lot_assignment(cur, upc, requested_lot=''):
    """Resolve lot for mutable item-prep actions and report whether fallback was auto-assigned."""
    requested = ss_normalization._normalize_lot_number(requested_lot)
    assigned = _resolve_bol_lot_for_upc(cur, upc, requested)
    auto_assigned = False
    auto_reason = ''

    if assigned:
        if not requested:
            auto_assigned = True
            auto_reason = 'no_lot_requested_used_latest'
        elif assigned.lower() != requested.lower():
            auto_assigned = True
            auto_reason = 'requested_lot_unavailable_used_latest'

    return {
        'requested_lot': requested,
        'assigned_lot': assigned,
        'auto_assigned': auto_assigned,
        'auto_assign_reason': auto_reason
    }


def _check_requested_lot_mismatch(cur, upc, requested_lot=''):
    """Detect whether requested lot is invalid for an existing UPC and return suggested latest lot."""
    upc_n = ss_normalization._normalize_upc_preserve_suffix_for_match(upc)
    requested = ss_normalization._normalize_lot_number(requested_lot)
    if not upc_n or not requested:
        return False, ''

    cur.execute('''
        SELECT 1
        FROM bol_items
        WHERE upc = ? COLLATE NOCASE
        LIMIT 1
    ''', (upc_n,))
    if not cur.fetchone():
        return False, ''

    cur.execute('''
        SELECT 1
        FROM bol_items
        WHERE upc = ? COLLATE NOCASE
          AND lot_number = ? COLLATE NOCASE
        LIMIT 1
    ''', (upc_n, requested))
    if cur.fetchone():
        return False, requested

    suggested = _resolve_bol_lot_for_upc(cur, upc_n, '')
    return True, ss_normalization._normalize_lot_number(suggested)


def _lot_mismatch_payload(*, upc, requested_lot, suggested_lot=''):
    upc_n = ss_normalization._normalize_upc_preserve_suffix_for_match(upc)
    requested = ss_normalization._normalize_lot_number(requested_lot)
    suggested = ss_normalization._normalize_lot_number(suggested_lot)
    msg = f'LOT match failed for UPC {upc_n} in LOT {requested}.'
    if suggested:
        msg += f' Most recent available LOT: {suggested}.'
    msg += ' Continue without LOT or cancel.'
    return {
        'success': False,
        'error': msg,
        'lot_match_error': True,
        'requested_lot': requested,
        'suggested_lot': suggested,
        'can_continue_without_lot': True,
        'can_override_this_lot': True,
        'can_auto_assign': True
    }


def _items_prep_items_to_list_url(upc, lot_number=''):
    try:
        from urllib.parse import quote
        upc_n = ss_normalization._normalize_upc_preserve_suffix_for_match(upc)
        lot_n = ss_normalization._normalize_lot_number(lot_number)
        url = f"/items-to-list?q={quote(upc_n)}&status=good,bad,return&direct_search=1"
        if lot_n:
            url += f"&lot={quote(lot_n)}"
        return url
    except Exception:
        return '/items-to-list'


def _items_prep_overage_payload(*, upc, lot_number='', unchecked_remaining=0, requested_qty=1, action='add'):
    upc_n = ss_normalization._normalize_upc_preserve_suffix_for_match(upc)
    lot_n = ss_normalization._normalize_lot_number(lot_number)
    try:
        unchecked_n = int(unchecked_remaining or 0)
    except Exception:
        unchecked_n = 0
    try:
        req_qty_n = int(requested_qty or 1)
    except Exception:
        req_qty_n = 1
    return {
        'success': False,
        'overage_blocked': True,
        'requires_exception_override': True,
        'error': (
            f'Item {upc_n} appears fully added already '
            f'(unchecked remaining: {unchecked_n}).'
        ),
        'message': (
            'This item already appears fully added for the current BOL quantity. '
            'Use Add Anyway to save as an exception, or skip.'
        ),
        'upc': upc_n,
        'lot_number': lot_n,
        'unchecked_remaining': unchecked_n,
        'requested_qty': req_qty_n,
        'action': (action or 'add'),
        'items_to_list_url': _items_prep_items_to_list_url(upc_n, lot_n)
    }
