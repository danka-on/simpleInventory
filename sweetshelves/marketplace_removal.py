"""Marketplace removal for Sweet Shelves."""

import datetime
import requests
import sqlite3
from . import (
    inventory_history as ss_inventory_history, listing_lifecycle as ss_listing_lifecycle, normalization
    as ss_normalization, warehouse_matching as ss_warehouse_matching,
)


def _marketplace_lookup_rawbol_item(barcode):
    variants = ss_listing_lifecycle._marketplace_upc_lookup_variants(barcode)
    if not variants:
        return None

    conn = None
    try:
        conn = sqlite3.connect('rawbol.db')
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()

        placeholders = ','.join('?' for _ in variants)
        cur.execute(f'''
            SELECT rowid AS _rowid_, upc, item_description, image_url
            FROM raw_bol_items
            WHERE upc IS NOT NULL
              AND TRIM(upc) != ''
              AND LOWER(TRIM(upc)) IN ({placeholders})
            ORDER BY rowid DESC
        ''', tuple(variants))
        rows = cur.fetchall()
        if not rows:
            return None

        target = str(ss_normalization._normalize_upc(barcode) or '').strip().lower()

        def _score(row):
            raw_upc = str(row['upc'] or '').strip().lower()
            exact = 1 if raw_upc == target else 0
            return (exact, int(row['_rowid_'] or 0))

        best = max(rows, key=_score)
        return {
            'upc': str(best['upc'] or '').strip(),
            'title': str(best['item_description'] or '').strip(),
            'image_url': str(best['image_url'] or '').strip(),
        }
    except Exception:
        return None
    finally:
        if conn is not None:
            conn.close()


def _normalize_marketplace_allocations(incoming_allocations):
    grouped = {}
    if not isinstance(incoming_allocations, list):
        return []
    for alloc in incoming_allocations:
        if not isinstance(alloc, dict):
            continue
        qty = max(0, ss_normalization._coerce_int(alloc.get('quantity', alloc.get('qty')), 0))
        if qty <= 0:
            continue
        location_code = ss_warehouse_matching._sold_location_label(alloc.get('location_code') or alloc.get('code'))
        location_key = ss_warehouse_matching._sold_location_key(alloc.get('location_key') or location_code)
        bucket = grouped.get(location_key)
        if bucket is None:
            bucket = {
                'location_key': location_key,
                'location_code': location_code,
                'quantity': 0
            }
            grouped[location_key] = bucket
        bucket['quantity'] += qty
    return list(grouped.values())


def _build_marketplace_removal_plan(barcode, quantity, allocations=None, fallback_barcodes=None, preferred_location=''):
    rack_conn = None
    try:
        rack_conn = sqlite3.connect('searchRack.db')
        rack_conn.row_factory = sqlite3.Row
        rack_cur = rack_conn.cursor()

        matches = ss_warehouse_matching._searchrack_matches_for_removal_context(
            rack_cur,
            barcode,
            fallback_barcodes=fallback_barcodes,
            preferred_location=preferred_location
        )
        location_groups = ss_warehouse_matching._group_searchrack_matches_by_location(matches)
        locations = [
            {
                'location_key': g['location_key'],
                'location_code': g['location_code'],
                'available_qty': max(0, ss_normalization._coerce_int(g.get('available_qty'), 0)),
                'preview_key': str(g.get('preview_key') or g.get('location_code') or '').strip()
            }
            for g in location_groups
        ]
        total_available = sum(max(0, ss_normalization._coerce_int(x.get('available_qty'), 0)) for x in locations)
        normalized_allocations = _normalize_marketplace_allocations(allocations)

        plan = {
            'success': True,
            'needs_choice': len(locations) > 1 and not normalized_allocations,
            'locations': locations,
            'total_available': total_available,
            'can_fulfill': total_available >= max(1, ss_normalization._coerce_int(quantity, 1)),
            'normalized_allocations': normalized_allocations,
            'planned_steps': []
        }

        sold_qty = max(1, ss_normalization._coerce_int(quantity, 1))
        if not location_groups:
            return plan

        group_map = {g['location_key']: g for g in location_groups}

        if normalized_allocations:
            alloc_total = 0
            for alloc in normalized_allocations:
                loc_key = alloc['location_key']
                qty = max(0, ss_normalization._coerce_int(alloc['quantity'], 0))
                group = group_map.get(loc_key)
                if not group:
                    return {
                        'success': False,
                        'error': f"Unknown location selected: {alloc['location_code']}",
                        'locations': locations,
                        'total_available': total_available,
                        'can_fulfill': total_available >= sold_qty
                    }
                group_available = max(0, ss_normalization._coerce_int(group.get('available_qty'), 0))
                if qty > group_available:
                    return {
                        'success': False,
                        'error': f"Selected quantity exceeds available for {alloc['location_code']}",
                        'locations': locations,
                        'total_available': total_available,
                        'can_fulfill': total_available >= sold_qty
                    }
                alloc_total += qty
            if alloc_total != sold_qty:
                return {
                    'success': False,
                    'error': f'Selected location quantities must total {sold_qty}',
                    'locations': locations,
                    'total_available': total_available,
                    'can_fulfill': total_available >= sold_qty
                }

            for alloc in normalized_allocations:
                remaining = max(0, ss_normalization._coerce_int(alloc['quantity'], 0))
                group = group_map.get(alloc['location_key']) or {}
                for row_match in group.get('rows') or []:
                    if remaining <= 0:
                        break
                    available = max(0, ss_normalization._coerce_int(row_match.get('quantity'), 0))
                    if available <= 0:
                        continue
                    take = min(available, remaining)
                    if take <= 0:
                        continue
                    plan['planned_steps'].append({
                        'searchrack_id': int(row_match['id']),
                        'location_code': group.get('location_code') or row_match.get('location_code'),
                        'quantity': int(take)
                    })
                    remaining -= take
                if remaining > 0:
                    return {
                        'success': False,
                        'error': f"Insufficient inventory in selected location {alloc['location_code']}",
                        'locations': locations,
                        'total_available': total_available,
                        'can_fulfill': total_available >= sold_qty
                    }
            return plan

        if len(location_groups) == 1:
            remaining = sold_qty
            group = location_groups[0]
            for row_match in group.get('rows') or []:
                if remaining <= 0:
                    break
                available = max(0, ss_normalization._coerce_int(row_match.get('quantity'), 0))
                if available <= 0:
                    continue
                take = min(available, remaining)
                if take <= 0:
                    continue
                plan['planned_steps'].append({
                    'searchrack_id': int(row_match['id']),
                    'location_code': group.get('location_code') or row_match.get('location_code'),
                    'quantity': int(take)
                })
                remaining -= take
            if remaining > 0:
                plan['planned_steps'] = []
            return plan

        return plan
    finally:
        if rack_conn is not None:
            rack_conn.close()


def _apply_marketplace_removal_plan(plan, *, order_ref, barcode, title, removal_type='manual_sold_removal'):
    steps = list((plan or {}).get('planned_steps') or [])
    if not steps:
        return {'removed': False, 'removed_units': 0, 'locations': []}

    rack_conn = None
    rem_conn = None
    try:
        rack_conn = sqlite3.connect('searchRack.db')
        rack_conn.row_factory = sqlite3.Row
        rack_cur = rack_conn.cursor()
        schema = ss_warehouse_matching._searchrack_removal_schema(rack_cur)
        qty_col = schema.get('qty_col')
        id_lookup_col = schema.get('id_col') or 'rowid'
        pos_col = schema.get('pos_col')
        if not qty_col:
            return {'removed': False, 'removed_units': 0, 'locations': [], 'error': 'SEARCHRACK quantity column not found'}

        rem_conn = sqlite3.connect('rackhistory.db')
        rem_cur = rem_conn.cursor()
        ss_inventory_history._ensure_removed_items_table(rem_cur)

        removed_units = 0
        used_locations = []
        touched_row_ids = set()
        now_iso = datetime.datetime.now().isoformat()

        def _abort(error_message):
            try:
                rack_conn.rollback()
            except Exception:
                pass
            try:
                rem_conn.rollback()
            except Exception:
                pass
            return {'removed': False, 'removed_units': 0, 'locations': [], 'error': error_message}

        for step in steps:
            row_id = max(0, ss_normalization._coerce_int(step.get('searchrack_id'), 0))
            take_qty = max(0, ss_normalization._coerce_int(step.get('quantity'), 0))
            if row_id <= 0 or take_qty <= 0:
                continue

            pos_expr = f", {pos_col} AS _item_position" if pos_col else ""
            rack_cur.execute(
                f"SELECT {qty_col} AS _qty{pos_expr} FROM SEARCHRACK WHERE {id_lookup_col} = ?",
                (row_id,)
            )
            row = rack_cur.fetchone()
            if not row:
                return _abort(f'Inventory row no longer exists for {step.get("location_code")}')

            current_qty = max(0, ss_normalization._coerce_int(row['_qty'], 0))
            if current_qty < take_qty:
                return _abort(f'Inventory changed before removal could complete for {step.get("location_code")}')

            new_qty = current_qty - take_qty
            rack_cur.execute(
                f'UPDATE SEARCHRACK SET {qty_col} = ? WHERE {id_lookup_col} = ?',
                (new_qty, row_id)
            )

            item_position = str((row['_item_position'] if '_item_position' in row.keys() else '') or '').strip()
            location_code = ss_warehouse_matching._sold_location_label(step.get('location_code') or item_position)
            rem_cur.execute('''
                INSERT INTO removed_items
                (order_id, barcode, title, quantity_removed, removed_at, searchrack_id,
                 old_quantity, new_quantity, removal_type, item_position, event_status)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending')
            ''', (
                order_ref,
                barcode,
                title or '',
                take_qty,
                now_iso,
                row_id,
                current_qty,
                new_qty,
                removal_type,
                item_position or location_code
            ))
            removed_units += take_qty
            touched_row_ids.add(row_id)
            if location_code not in used_locations:
                used_locations.append(location_code)

        rem_conn.commit()
        rack_conn.commit()
        for row_id in touched_row_ids:
            ss_inventory_history._clear_zero_qty_pending_deletions(row_id)
        return {'removed': removed_units > 0, 'removed_units': removed_units, 'locations': used_locations}
    except Exception as e:
        try:
            if rack_conn is not None:
                rack_conn.rollback()
        except Exception:
            pass
        try:
            if rem_conn is not None:
                rem_conn.rollback()
        except Exception:
            pass
        return {'removed': False, 'removed_units': 0, 'locations': [], 'error': str(e)}
    finally:
        if rack_conn is not None:
            rack_conn.close()
        if rem_conn is not None:
            rem_conn.close()


def _probe_ebay_listing_live(item_id, url=''):
    """
    Best-effort live check against eBay listing page.
    Returns: (is_active_or_none, note)
      - True: page looks active
      - False: page shows ended markers
      - None: network/parse uncertainty, caller should treat as unknown
    """
    iid = (str(item_id or '').strip())
    if not iid:
        return None, 'missing_item_id'

    target_url = (str(url or '').strip()) or f'https://www.ebay.com/itm/{iid}'
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0 Safari/537.36',
        'Accept-Language': 'en-US,en;q=0.9'
    }

    try:
        resp = requests.get(target_url, timeout=8, headers=headers)
    except Exception as e:
        return None, f'request_error:{type(e).__name__}'

    status = int(resp.status_code or 0)
    if status >= 500:
        return None, f'http_{status}'

    body = (resp.text or '').lower()
    ended_markers = (
        'this listing was ended',
        'this listing ended',
        'listing has ended',
        'you can no longer bid on this item',
        'this item is no longer available',
        'this item is out of stock'
    )
    for marker in ended_markers:
        if marker in body:
            return False, f'ended_marker:{marker}'

    return True, f'http_{status}'
