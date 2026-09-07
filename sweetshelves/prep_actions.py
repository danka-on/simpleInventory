"""Prep actions for Sweet Shelves."""

import json
import re
import sqlite3
from flask import jsonify, request
from . import (
    caching as ss_caching, database as ss_database, errors as ss_errors, listing_log as ss_listing_log,
    normalization as ss_normalization, prep_context as ss_prep_context, prep_log as ss_prep_log,
    prep_media as ss_prep_media, prep_schema as ss_prep_schema, runtime as ss_runtime,
    warehouse_allocations as ss_warehouse_allocations, warehouse_matching as ss_warehouse_matching,
)


def api_items_prep_status_get(upc):
    """Get preparation status for a UPC."""
    conn = None
    try:
        upc_norm = ss_normalization._normalize_upc(upc)
        # Strip leading zeros to match item manager behavior
        upc_n = ss_normalization._strip_leading_zeros_numeric(upc_norm)
        lot_number = ss_warehouse_allocations._preferred_lot_from_request()
        ss_prep_schema._ensure_items_prep_tables()
        conn = sqlite3.connect('bol.db')
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        row = ss_warehouse_allocations._select_prep_status_row(cur, upc_n, lot_number, columns='status, reason, note, updated_at, lot_number')
        if row:
            return jsonify({
                'success': True,
                'status': row['status'],
                'reason': row['reason'],
                'note': row['note'],
                'updated_at': row['updated_at'],
                'lot_number': (row['lot_number'] if isinstance(row, sqlite3.Row) and 'lot_number' in row.keys() else None)
            })
        else:
            return jsonify({'success': True, 'status': 'unchecked', 'reason': None, 'note': None, 'updated_at': None})
    except Exception as e:
        return jsonify({'success': False, 'error': ss_errors._safe_error(e)}), 500
    finally:
        if conn is not None:
            conn.close()


def api_items_prep_status():
    """Upsert preparation status for a UPC.
    
    NEW LOGIC:
    - GOOD flow: Base UPC is default; if a note or voice note is provided, create a suffixed GOOD special-case entry
    - BAD flow: UPC should already be suffixed (created by Bad button), just update status
    - Base qty is only decremented when status is finalized (Good immediately, Bad on Complete)
    """
    conn = None
    try:
        data = request.get_json() or {}
        upc = ss_normalization._normalize_upc(data.get('upc'))
        status = (data.get('status') or '').strip().lower()
        reason = (data.get('reason') or '').strip()
        note = (data.get('note') or '').strip()
        has_media_note = ss_normalization._coerce_bool(data.get('has_media_note'))
        force_good_suffix = ss_normalization._coerce_bool(data.get('force_good_suffix'))
        exception_note = (data.get('exception_note') or '').strip()
        raw_qty = data.get('qty', 1)
        try:
            qty = int(raw_qty if raw_qty is not None else 1)
        except Exception:
            return jsonify({'success': False, 'error': 'Invalid qty'}), 400
        if qty < 1:
            return jsonify({'success': False, 'error': 'qty must be at least 1'}), 400
        
        if not upc or status not in ('good', 'bad', 'unchecked', 'return'):
            return jsonify({'success': False, 'error': 'Missing upc or invalid status'}), 400

        if status == 'unchecked':
            reason = ''
            note = ''
        
        # Strip leading zeros but preserve suffix
        upc = ss_normalization._normalize_upc_preserve_suffix_for_match(upc)
        
        base_upc = upc.split('-')[0] if '-' in upc else upc
        requested_lot = ss_warehouse_allocations._preferred_lot_from_request(data)
        allow_no_lot = ss_normalization._coerce_bool(data.get('allow_no_lot'))
        force_requested_lot = ss_normalization._coerce_bool(
            data.get('force_requested_lot') if data.get('force_requested_lot') is not None else data.get('override_lot')
        )
        allow_overage_exception = ss_normalization._coerce_bool(
            data.get('allow_overage_exception') if data.get('allow_overage_exception') is not None else data.get('force_exception')
        )
        exception_overage_applied = False
        if allow_no_lot:
            requested_lot = ''
            force_requested_lot = False
        requested_lot_norm = ss_normalization._normalize_lot_number(requested_lot)
        
        print(f'[STATUS API] Received UPC: {data.get("upc")}, Normalized: {upc}, Base: {base_upc}, Status: {status}')
        print(f'[STATUS API] UPC has suffix: {upc != base_upc}')
        print(f'[STATUS API] Reason: "{reason}", Note: "{note}", MediaNote: {has_media_note}, Qty: {qty}')
        print(f'[STATUS API] Requested LOT: {requested_lot or "(none)"}')
        
        conn = sqlite3.connect('bol.db', isolation_level='IMMEDIATE')
        cur = conn.cursor()
        ss_prep_schema._ensure_items_prep_tables()
        lot_mismatch, suggested_lot = ss_warehouse_allocations._check_requested_lot_mismatch(cur, base_upc, requested_lot_norm)
        if lot_mismatch and not allow_no_lot and not force_requested_lot:
            return jsonify(ss_warehouse_allocations._lot_mismatch_payload(
                upc=base_upc,
                requested_lot=requested_lot_norm,
                suggested_lot=suggested_lot
            )), 409
        
        import datetime
        ts = datetime.datetime.now(datetime.UTC).isoformat()

        def _next_clean_suffix(base_upc_value):
            """Find next suffix not used by bol_items or prep artifacts."""
            suffix_num = 1
            max_suffix_attempts = 500
            while suffix_num <= max_suffix_attempts:
                candidate = f"{base_upc_value}-{suffix_num}"
                cur.execute('SELECT 1 FROM bol_items WHERE upc = ? COLLATE NOCASE LIMIT 1', (candidate,))
                if cur.fetchone():
                    suffix_num += 1
                    continue
                cur.execute('SELECT 1 FROM items_prep_status WHERE upc = ? COLLATE NOCASE LIMIT 1', (candidate,))
                if cur.fetchone():
                    suffix_num += 1
                    continue
                cur.execute('SELECT 1 FROM items_prep_images WHERE upc = ? COLLATE NOCASE LIMIT 1', (candidate,))
                if cur.fetchone():
                    suffix_num += 1
                    continue
                try:
                    cur.execute('SELECT 1 FROM items_prep_notes WHERE upc = ? COLLATE NOCASE LIMIT 1', (candidate,))
                    if cur.fetchone():
                        suffix_num += 1
                        continue
                except Exception:
                    pass
                try:
                    cur.execute('SELECT 1 FROM items_prep_media WHERE upc = ? COLLATE NOCASE LIMIT 1', (candidate,))
                    if cur.fetchone():
                        suffix_num += 1
                        continue
                except Exception:
                    pass
                return candidate
            raise RuntimeError(f'Unable to allocate suffix for {base_upc_value} after {max_suffix_attempts} attempts')
        
        # GOOD flow - base UPC by default; note-driven special cases are suffixed
        if status == 'good':
            # GOOD items cannot have defect reasons
            if reason:
                return jsonify({'success': False, 'error': 'GOOD items cannot have defect reasons. Please clear the defect field or select BAD status.'}), 400
            
            # Resolve lot context first so status/quantity updates are scoped to one manifest lot.
            if allow_no_lot:
                selected_lot = ''
            elif force_requested_lot and requested_lot_norm:
                selected_lot = requested_lot_norm
            else:
                selected_lot = ss_warehouse_allocations._resolve_bol_lot_for_upc(cur, base_upc, requested_lot)
            forced_lot_override = bool(force_requested_lot and selected_lot)
            print(f'[STATUS API] Resolved LOT for GOOD flow: {selected_lot or "(none)"}')

            # Special-case GOOD with typed note or voice note: create a dedicated suffixed GOOD entry.
            # This preserves per-unit notes/photos/media without collapsing into base UPC state.
            has_special_good_note = bool(force_good_suffix)  # Only create suffix when explicitly requested via "New Unique Entry"
            temp_note_suffix_row = None
            if has_special_good_note and upc != base_upc:
                # Lookup can return a temporary suffixed duplicate for already-prepped UPCs.
                # If user adds a note/media on that row, keep it as a distinct suffixed GOOD entry
                # instead of collapsing back into the base UPC.
                existing_suffixed_status = ss_warehouse_allocations._select_prep_status_row(cur, upc, selected_lot, columns='status')
                existing_suffixed_status_val = (
                    str(existing_suffixed_status[0] or '').strip().lower()
                    if existing_suffixed_status else ''
                )
                if existing_suffixed_status_val != 'bad':
                    cur.execute('''
                        SELECT id, temporary
                        FROM bol_items
                        WHERE upc = ? COLLATE NOCASE
                        ORDER BY import_date DESC, id DESC
                        LIMIT 1
                    ''', (upc,))
                    _tmp_row = cur.fetchone()
                    if _tmp_row and int(_tmp_row[1] or 0) == 1:
                        temp_note_suffix_row = _tmp_row

            if has_special_good_note and (upc == base_upc or temp_note_suffix_row):
                suffixed_upc = upc if temp_note_suffix_row else _next_clean_suffix(base_upc)
                print(f'[GOOD] Creating noted special-case suffix {suffixed_upc} for base {base_upc}')

                # Use lot-specific base row when possible; fallback to latest base row.
                if selected_lot:
                    cur.execute('''
                        SELECT id, item_description, image_url, lot_number, bol_number, good_qty, bad_qty, unchecked_qty, original_qty
                        FROM bol_items
                        WHERE upc = ? COLLATE NOCASE
                          AND lot_number = ? COLLATE NOCASE
                        ORDER BY import_date DESC, id DESC
                        LIMIT 1
                    ''', (base_upc, selected_lot))
                    base_item = cur.fetchone()
                else:
                    base_item = None
                if not base_item:
                    cur.execute('''
                        SELECT id, item_description, image_url, lot_number, bol_number, good_qty, bad_qty, unchecked_qty, original_qty
                        FROM bol_items
                        WHERE upc = ? COLLATE NOCASE
                        ORDER BY import_date DESC, id DESC
                        LIMIT 1
                    ''', (base_upc,))
                    base_item = cur.fetchone()
                if not base_item:
                    return jsonify({'success': False, 'error': f'Base UPC {base_upc} not found in bol_items'}), 404

                base_row_id = int(base_item[0])
                base_desc = base_item[1] or ''
                base_img = base_item[2] or ''
                resolved_base_lot = ss_normalization._normalize_lot_number(base_item[3])
                base_bol = base_item[4] or ''
                current_good = int(base_item[5] or 0)
                current_bad = int(base_item[6] or 0)
                current_unchecked = base_item[7]
                original_qty = int(base_item[8] or 0)
                if current_unchecked is None:
                    current_unchecked = max(0, original_qty - current_good - current_bad)
                else:
                    current_unchecked = int(current_unchecked or 0)

                if allow_no_lot:
                    selected_lot = ''
                elif force_requested_lot and requested_lot_norm:
                    selected_lot = requested_lot_norm
                else:
                    selected_lot = resolved_base_lot

                if qty > current_unchecked:
                    exception_overage_applied = True
                    if allow_overage_exception:
                        print(
                            f'[GOOD][EXCEPTION] Allowing noted suffix overage for {base_upc}: '
                            f'requested={qty}, unchecked={current_unchecked}'
                        )
                    else:
                        print(
                            f'[GOOD][EXCEPTION][AUTO] Unchecked gate bypassed for noted suffix {base_upc}: '
                            f'requested={qty}, unchecked={current_unchecked}'
                        )

                new_base_good = current_good + qty
                new_base_unchecked = max(0, current_unchecked - qty)
                if force_requested_lot and selected_lot:
                    cur.execute('''
                        UPDATE bol_items
                        SET good_qty = ?, unchecked_qty = ?, lot_number = ?
                        WHERE id = ?
                    ''', (new_base_good, new_base_unchecked, selected_lot, base_row_id))
                else:
                    cur.execute('''
                        UPDATE bol_items
                        SET good_qty = ?, unchecked_qty = ?
                        WHERE id = ?
                    ''', (new_base_good, new_base_unchecked, base_row_id))
                print(
                    f'[GOOD] Updated base {base_upc}: good_qty {current_good}->{new_base_good}, '
                    f'unchecked_qty {current_unchecked}->{new_base_unchecked}'
                )

                if temp_note_suffix_row:
                    temp_row_id = int(temp_note_suffix_row[0])
                    cur.execute('''
                        UPDATE bol_items
                        SET upc = ?,
                            item_description = ?,
                            image_url = ?,
                            lot_number = ?,
                            bol_number = ?,
                            import_date = ?,
                            temporary = 0,
                            original_qty = ?,
                            unchecked_qty = 0,
                            good_qty = ?,
                            bad_qty = 0,
                            quantity = ?
                        WHERE id = ?
                    ''', (suffixed_upc, base_desc, base_img, selected_lot, base_bol, ts, qty, qty, qty, temp_row_id))
                else:
                    cur.execute('''
                        INSERT INTO bol_items (
                            upc, item_description, image_url, lot_number, bol_number, import_date,
                            temporary, original_qty, unchecked_qty, good_qty, bad_qty, quantity
                        )
                        VALUES (?, ?, ?, ?, ?, ?, 0, ?, 0, ?, 0, ?)
                    ''', (suffixed_upc, base_desc, base_img, selected_lot, base_bol, ts, qty, qty, qty))

                existing_note_status = ss_warehouse_allocations._select_prep_status_row(cur, suffixed_upc, selected_lot, columns='upc')
                suffixed_status_lot = ss_warehouse_allocations._resolve_prep_status_lot(cur, suffixed_upc, selected_lot)
                if existing_note_status:
                    cur.execute('''
                        UPDATE items_prep_status
                        SET status = 'good',
                            reason = '',
                            note = ?,
                            quantity = ?,
                            updated_at = ?
                        WHERE upc = ? COLLATE NOCASE
                          AND COALESCE(lot_number, '') = ? COLLATE NOCASE
                    ''', (note, qty, ts, suffixed_upc, suffixed_status_lot))
                else:
                    cur.execute('''
                        INSERT INTO items_prep_status (upc, lot_number, status, reason, note, quantity, updated_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                    ''', (suffixed_upc, ss_normalization._normalize_lot_number(selected_lot), 'good', '', note, qty, ts))

                if exception_overage_applied:
                    ss_prep_schema._items_prep_record_exception(
                        cur,
                        upc=suffixed_upc,
                        base_upc=base_upc,
                        lot_number=selected_lot,
                        action='good_overage_override',
                        quantity=qty,
                        unchecked_remaining=current_unchecked,
                        source='item-prep',
                        note=exception_note or 'Noted GOOD suffix added as exception after unchecked quantity was exhausted.',
                        meta={
                            'status': 'good',
                            'requested_qty': qty,
                            'unchecked_before': current_unchecked,
                            'allow_overage_exception': True,
                            'requested_lot': requested_lot_norm,
                            'assigned_lot': selected_lot,
                            'forced_lot_override': bool(forced_lot_override),
                            'special_case_note': True,
                            'special_case_media_note': bool(has_media_note)
                        }
                    )

                conn.commit()
                ss_caching.update_data_version()

                try:
                    good_meta = {
                        'action': 'saved_good_suffixed',
                        'requested_lot': requested_lot_norm,
                        'lot_number': selected_lot,
                        'assigned_lot': selected_lot,
                        'photo_upc': suffixed_upc,
                        'special_case_note': True,
                        'special_case_media_note': bool(has_media_note),
                        'affects_base_good': True
                    }
                    if selected_lot and (not requested_lot_norm or selected_lot.lower() != requested_lot_norm.lower()):
                        good_meta['auto_assigned'] = True
                    if forced_lot_override:
                        good_meta['forced_lot_override'] = True
                        good_meta['needs_review'] = True
                    if exception_overage_applied:
                        good_meta['exception_overage'] = True
                        good_meta['unchecked_before'] = current_unchecked
                        good_meta['requested_qty'] = qty
                    ss_prep_log._preplog_add_entry(
                        upc=suffixed_upc,
                        base_upc=base_upc,
                        status='good',
                        quantity=qty,
                        note=note or None,
                        reason=None,
                        source='item-prep',
                        meta=good_meta,
                        dedupe=False
                    )
                except Exception:
                    pass

                try:
                    ss_runtime.cache.delete(f"view//api/bol_lookup?upc={base_upc}")
                    ss_runtime.cache.delete_memoized(ss_prep_context.api_bol_lookup)
                except Exception:
                    pass

                return jsonify({
                    'success': True,
                    'action': 'saved_good_suffixed',
                    'upc': suffixed_upc,
                    'base_upc': base_upc,
                    'lot_number': selected_lot,
                    'exception_overage': bool(exception_overage_applied),
                    'forced_lot_override': bool(forced_lot_override),
                    'special_case_note': True
                })

            # Check if the incoming UPC itself is a suffixed BAD entry
            suffixed_upc_found = None
            suffixed_reason = None
            
            if upc != base_upc:
                # UPC is already suffixed, check if it's a BAD entry
                row = ss_warehouse_allocations._select_prep_status_row(cur, upc, selected_lot, columns='status, reason, quantity')
                print(f'[GOOD] Checking suffixed UPC {upc} in items_prep_status: {row}')
                if row and row[0] == 'bad':
                    suffixed_upc_found = upc
                    suffixed_reason = (row[1] or '').strip()
                    print(f'[GOOD] Found suffixed BAD entry directly: {suffixed_upc_found}')
                elif row:
                    # Suffix exists and is not BAD: keep this suffixed row as GOOD without touching base UPC aggregates.
                    existing_qty = int(row[2] or 0)
                    next_qty = max(existing_qty, qty)
                    status_update_lot = ss_warehouse_allocations._resolve_prep_status_lot(cur, upc, selected_lot)
                    cur.execute('''
                        UPDATE items_prep_status
                        SET status = 'good',
                            reason = '',
                            note = ?,
                            quantity = ?,
                            updated_at = ?
                        WHERE upc = ? COLLATE NOCASE
                          AND COALESCE(lot_number, '') = ? COLLATE NOCASE
                    ''', (note, next_qty, ts, upc, status_update_lot))

                    bol_row = ss_warehouse_allocations._select_canonical_bol_row(
                        cur,
                        upc,
                        selected_lot,
                        columns='id, good_qty, quantity'
                    )
                    if bol_row:
                        bol_id = int(bol_row[0])
                        bol_good = int((bol_row[1] if bol_row else 0) or 0)
                        bol_qty = int((bol_row[2] if bol_row else 0) or 0)
                        merged_qty = max(bol_good, bol_qty, next_qty)
                        cur.execute('''
                            UPDATE bol_items
                            SET good_qty = ?, bad_qty = 0, unchecked_qty = 0, quantity = ?, temporary = 0
                            WHERE id = ?
                        ''', (merged_qty, merged_qty, bol_id))

                    conn.commit()
                    ss_caching.update_data_version()
                    try:
                        ss_runtime.cache.delete(f"view//api/bol_lookup?upc={base_upc}")
                        ss_runtime.cache.delete_memoized(ss_prep_context.api_bol_lookup)
                    except Exception:
                        pass
                    return jsonify({
                        'success': True,
                        'action': 'updated_suffixed_good',
                        'upc': upc,
                        'base_upc': base_upc,
                        'lot_number': selected_lot,
                        'forced_lot_override': bool(forced_lot_override)
                    })
                else:
                    # No items_prep_status entry yet - check if this is a bad entry in bol_items
                    # (created by Bad button but diagnostic not completed yet)
                    bol_row = ss_warehouse_allocations._select_canonical_bol_row(
                        cur,
                        upc,
                        selected_lot,
                        columns='bad_qty, original_qty'
                    )
                    print(f'[GOOD] No prep_status entry, checking bol_items for {upc}: {bol_row}')
                    if bol_row and bol_row[0] and bol_row[0] > 0:
                        # This suffixed entry has bad_qty > 0, meaning it was created from Bad flow
                        suffixed_upc_found = upc
                        suffixed_reason = ''
                        print(f'[GOOD] Found suffixed BAD entry in bol_items (no prep_status yet): {suffixed_upc_found}')
            
            # If not found yet, search for suffixed entries starting from base_upc
            if not suffixed_upc_found:
                suffix_num = 1
                while True:
                    test_suffixed = f"{base_upc}-{suffix_num}"
                    row = ss_warehouse_allocations._select_prep_status_row(cur, test_suffixed, selected_lot, columns='status, reason')
                    if row and row[0] == 'bad':
                        suffixed_upc_found = test_suffixed
                        suffixed_reason = (row[1] or '').strip()
                        print(f'[GOOD] Found suffixed BAD entry by search: {suffixed_upc_found}')
                        break
                    elif not row:
                        # No more suffixes exist
                        break
                    suffix_num += 1
            
            # If we found a BAD suffixed entry, this is a BAD→GOOD conversion
            if suffixed_upc_found:
                print(f'[GOOD] Converting BAD item {suffixed_upc_found} to GOOD')
                
                # Keep the suffixed entry but change status to GOOD (don't merge back to base)
                # This preserves any notes or distinguishing information added during BAD flow
                
                # UPSERT items_prep_status (might not exist yet if diagnostic not completed)
                existing_suffixed = ss_warehouse_allocations._select_prep_status_row(cur, suffixed_upc_found, selected_lot, columns='upc')
                suffixed_status_lot = ss_warehouse_allocations._resolve_prep_status_lot(cur, suffixed_upc_found, selected_lot)
                if existing_suffixed:
                    # Update existing entry
                    cur.execute('''
                        UPDATE items_prep_status
                        SET status = ?, reason = ?, note = ?, updated_at = ?, quantity = ?
                        WHERE upc = ? COLLATE NOCASE
                          AND COALESCE(lot_number, '') = ? COLLATE NOCASE
                    ''', ('good', reason or '', note, ts, qty, suffixed_upc_found, suffixed_status_lot))
                    print(f'[GOOD] Updated items_prep_status {suffixed_upc_found}: status=good, reason="{reason or ""}"')
                else:
                    # Insert new entry
                    cur.execute('''
                        INSERT INTO items_prep_status (upc, lot_number, status, reason, note, updated_at, quantity)
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                    ''', (suffixed_upc_found, ss_normalization._normalize_lot_number(selected_lot), 'good', reason or '', note, ts, qty))
                    print(f'[GOOD] Inserted items_prep_status {suffixed_upc_found}: status=good, reason="{reason or ""}"')
                
                # Update bol_items quantities: move from bad_qty to good_qty
                bol_row = ss_warehouse_allocations._select_canonical_bol_row(
                    cur,
                    suffixed_upc_found,
                    selected_lot,
                    columns='id, bad_qty, good_qty'
                )
                if bol_row:
                    suffixed_bol_id = int(bol_row[0])
                    current_bad = bol_row[1] or 0
                    current_good = bol_row[2] or 0
                    # Move all bad_qty to good_qty
                    new_bad = 0
                    new_good = current_good + current_bad
                    cur.execute('''
                        UPDATE bol_items
                        SET bad_qty = ?, good_qty = ?, temporary = 0
                        WHERE id = ?
                    ''', (new_bad, new_good, suffixed_bol_id))
                    print(f'[GOOD] Updated bol_items {suffixed_upc_found}: bad_qty {current_bad}→{new_bad}, good_qty {current_good}→{new_good}')
                    
                    # Also update base UPC: decrement bad_qty
                    base_row = ss_warehouse_allocations._select_canonical_bol_row(
                        cur,
                        base_upc,
                        selected_lot,
                        columns='id, bad_qty'
                    )
                    if base_row:
                        base_bol_id = int(base_row[0])
                        base_bad = base_row[1] or 0
                        new_base_bad = max(0, base_bad - current_bad)
                        cur.execute('''
                            UPDATE bol_items
                            SET bad_qty = ?
                            WHERE id = ?
                        ''', (new_base_bad, base_bol_id))
                        print(f'[GOOD] Updated base {base_upc}: bad_qty {base_bad}→{new_base_bad}')
                
                conn.commit()

                # Update data version for cache invalidation
                ss_caching.update_data_version()
                try:
                    ss_runtime.cache.delete(f"view//api/bol_lookup?upc={base_upc}")
                    ss_runtime.cache.delete_memoized(ss_prep_context.api_bol_lookup)
                except Exception:
                    pass

                return jsonify({
                    'success': True,
                    'action': 'converted_bad_to_good_kept_suffix',
                    'upc': suffixed_upc_found,
                    'base_upc': base_upc,
                    'lot_number': selected_lot,
                    'forced_lot_override': bool(forced_lot_override)
                })
            
            # Not a BAD→GOOD conversion - proceed with normal GOOD flow for base UPC
            # Update items_prep_status for the base UPC
            # Check if ANY entry exists for this UPC (regardless of status)
            existing_prep_row = ss_warehouse_allocations._select_prep_status_row(cur, base_upc, selected_lot, columns='status, quantity')
            status_update_lot = ss_warehouse_allocations._resolve_prep_status_lot(cur, base_upc, selected_lot)
            if existing_prep_row:
                existing_status = existing_prep_row[0]
                existing_qty = existing_prep_row[1] if existing_prep_row[1] is not None else 0
                
                if existing_status == 'good':
                    # Add to existing GOOD quantity
                    total_prep_good = existing_qty + qty
                    cur.execute('''
                        UPDATE items_prep_status
                        SET quantity = ?, reason = ?, note = ?, updated_at = ?
                        WHERE upc = ? COLLATE NOCASE
                          AND COALESCE(lot_number, '') = ? COLLATE NOCASE
                    ''', (total_prep_good, reason, note, ts, base_upc, status_update_lot))
                    print(f'[GOOD] Updated items_prep_status {base_upc}: qty {existing_qty}→{total_prep_good}')
                else:
                    # Replace existing status (was unchecked/bad) with GOOD
                    cur.execute('''
                        UPDATE items_prep_status
                        SET status = ?, quantity = ?, reason = ?, note = ?, updated_at = ?
                        WHERE upc = ? COLLATE NOCASE
                          AND COALESCE(lot_number, '') = ? COLLATE NOCASE
                    ''', ('good', qty, reason, note, ts, base_upc, status_update_lot))
                    print(f'[GOOD] Changed items_prep_status {base_upc} from {existing_status} to good, qty={qty}')
            else:
                # No existing entry, insert new GOOD status
                cur.execute('''
                    INSERT INTO items_prep_status (upc, lot_number, status, reason, note, quantity, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                ''', (base_upc, ss_normalization._normalize_lot_number(selected_lot), 'good', reason, note, qty, ts))
                print(f'[GOOD] Created items_prep_status {base_upc} with status=good, qty={qty}')
            
            # Update bol_items quantities (good and unchecked) on the best-matching LOT row.
            if selected_lot:
                cur.execute('''
                    SELECT id, good_qty, bad_qty, unchecked_qty, original_qty
                    FROM bol_items
                    WHERE upc = ? COLLATE NOCASE
                      AND lot_number = ? COLLATE NOCASE
                    ORDER BY import_date DESC, id DESC
                    LIMIT 1
                ''', (base_upc, selected_lot))
                bol_row = cur.fetchone()
                if not bol_row:
                    print(f'[GOOD] WARNING: No bol_items row for {base_upc} in LOT {selected_lot}, falling back to newest row')
                    cur.execute('''
                        SELECT id, good_qty, bad_qty, unchecked_qty, original_qty
                        FROM bol_items
                        WHERE upc = ? COLLATE NOCASE
                        ORDER BY import_date DESC, id DESC
                        LIMIT 1
                    ''', (base_upc,))
                    bol_row = cur.fetchone()
            else:
                cur.execute('''
                    SELECT id, good_qty, bad_qty, unchecked_qty, original_qty
                    FROM bol_items
                    WHERE upc = ? COLLATE NOCASE
                    ORDER BY import_date DESC, id DESC
                    LIMIT 1
                ''', (base_upc,))
                bol_row = cur.fetchone()

            if bol_row:
                bol_id = bol_row[0]
                current_good = bol_row[1] or 0
                current_bad = bol_row[2] or 0
                current_unchecked = bol_row[3]
                original_qty = bol_row[4] or 0

                if current_unchecked is None:
                    current_unchecked = max(0, original_qty - current_good - current_bad)

                if qty > current_unchecked:
                    exception_overage_applied = True
                    if allow_overage_exception:
                        print(
                            f'[GOOD][EXCEPTION] Allowing overage add for {base_upc}: '
                            f'requested={qty}, unchecked={current_unchecked}'
                        )
                    else:
                        print(
                            f'[GOOD][EXCEPTION][AUTO] Unchecked gate bypassed for {base_upc}: '
                            f'requested={qty}, unchecked={current_unchecked}'
                        )

                new_good = current_good + qty
                new_unchecked = max(0, current_unchecked - qty)

                if selected_lot:
                    cur.execute('''
                        UPDATE bol_items
                        SET good_qty = ?, unchecked_qty = ?, lot_number = ?
                        WHERE id = ?
                    ''', (new_good, new_unchecked, selected_lot, bol_id))
                    print(f'[GOOD] Updated bol_items {base_upc} (id={bol_id}): good_qty {current_good}→{new_good}, unchecked_qty {current_unchecked}→{new_unchecked}, lot_number={selected_lot}')
                else:
                    cur.execute('''
                        UPDATE bol_items
                        SET good_qty = ?, unchecked_qty = ?
                        WHERE id = ?
                    ''', (new_good, new_unchecked, bol_id))
                    print(f'[GOOD] Updated bol_items {base_upc} (id={bol_id}): good_qty {current_good}→{new_good}, unchecked_qty {current_unchecked}→{new_unchecked}')

                if exception_overage_applied:
                    ss_prep_schema._items_prep_record_exception(
                        cur,
                        upc=base_upc,
                        base_upc=base_upc,
                        lot_number=selected_lot,
                        action='good_overage_override',
                        quantity=qty,
                        unchecked_remaining=current_unchecked,
                        source='item-prep',
                        note=exception_note or 'Added as exception after unchecked quantity was exhausted.',
                        meta={
                            'status': 'good',
                            'requested_qty': qty,
                            'unchecked_before': current_unchecked,
                            'allow_overage_exception': True,
                            'requested_lot': requested_lot_norm,
                            'assigned_lot': selected_lot,
                            'forced_lot_override': bool(forced_lot_override)
                        }
                    )
            else:
                print(f'[GOOD] WARNING: Could not find bol_items entry for {base_upc} to update quantities')

            
            conn.commit()
            print(f'[GOOD] Transaction committed for {base_upc}')
            
            # Update data version for cache invalidation
            ss_caching.update_data_version()

            # Prep log (preplog.db)
            try:
                good_meta = {
                    'action': 'converted_to_good' if suffixed_upc_found else 'saved_good',
                    'requested_lot': requested_lot_norm,
                    'lot_number': selected_lot,
                    'assigned_lot': selected_lot
                }
                if suffixed_upc_found:
                    # Keep original diagnostic UPC so log photo viewer can open exact-image rows.
                    good_meta['photo_upc'] = suffixed_upc_found
                if selected_lot and (not requested_lot_norm or selected_lot.lower() != requested_lot_norm.lower()):
                    good_meta['auto_assigned'] = True
                if forced_lot_override:
                    good_meta['forced_lot_override'] = True
                    good_meta['needs_review'] = True
                if exception_overage_applied:
                    good_meta['exception_overage'] = True
                    good_meta['unchecked_before'] = current_unchecked
                    good_meta['requested_qty'] = qty
                ss_prep_log._preplog_add_entry(
                    upc=base_upc,
                    base_upc=base_upc,
                    status='good',
                    quantity=qty,
                    note=note or None,
                    reason=None,
                    source='item-prep',
                    meta=good_meta,
                    dedupe=False
                )
            except Exception:
                pass

            try:
                ss_runtime.cache.delete(f"view//api/bol_lookup?upc={base_upc}")
                ss_runtime.cache.delete_memoized(ss_prep_context.api_bol_lookup)
            except Exception:
                pass

            return jsonify({
                'success': True,
                'action': 'converted_to_good' if suffixed_upc_found else 'saved_good',
                'upc': base_upc,
                'base_upc': base_upc,
                'lot_number': selected_lot,
                'exception_overage': bool(exception_overage_applied),
                'forced_lot_override': bool(forced_lot_override)
            })

        # BAD/UNCHECKED flow - upc should already be suffixed if coming from Bad button
        # Just upsert the status (no qty changes here, that happens in diagnostic Complete)
        
        # Special case: If changing a base UPC to BAD, create a finalized suffixed row
        # in both items_prep_status and bol_items so /items-to-list can surface it.
        if status == 'bad' and '-' not in str(upc):
            print(f'[BAD] Converting base item {base_upc} to BAD')
            
            if allow_no_lot:
                selected_lot = ''
            elif force_requested_lot and requested_lot_norm:
                selected_lot = requested_lot_norm
            else:
                selected_lot = ss_warehouse_allocations._resolve_bol_lot_for_upc(cur, base_upc, requested_lot)

            if selected_lot:
                cur.execute('''
                    SELECT id, quantity, item_description, image_url, lot_number, bol_number,
                           good_qty, bad_qty, unchecked_qty, original_qty
                    FROM bol_items 
                    WHERE upc = ? COLLATE NOCASE 
                      AND lot_number = ? COLLATE NOCASE
                      AND (itemprepped IS NULL OR itemprepped = 0)
                    ORDER BY import_date DESC, id DESC
                    LIMIT 1
                ''', (base_upc, selected_lot))
            else:
                cur.execute('''
                    SELECT id, quantity, item_description, image_url, lot_number, bol_number,
                           good_qty, bad_qty, unchecked_qty, original_qty
                    FROM bol_items
                    WHERE upc = ? COLLATE NOCASE
                      AND (itemprepped IS NULL OR itemprepped = 0)
                    ORDER BY import_date DESC, id DESC
                    LIMIT 1
                ''', (base_upc,))
            
            bol_row = cur.fetchone()
            if not bol_row:
                conn.rollback()
                return jsonify({'success': False, 'error': f'Base UPC {base_upc} not found in bol_items'}), 404

            bol_id = int(bol_row[0])
            current_bol_qty = int(bol_row[1] or 1)
            base_desc = bol_row[2] or ''
            base_img = bol_row[3] or ''
            resolved_base_lot = ss_normalization._normalize_lot_number(bol_row[4])
            base_bol = bol_row[5] or ''
            current_good = int(bol_row[6] or 0)
            current_bad = int(bol_row[7] or 0)
            current_unchecked = bol_row[8]
            original_qty = int(bol_row[9] or 0)
            if current_unchecked is None:
                current_unchecked = max(0, original_qty - current_good - current_bad)
            else:
                current_unchecked = int(current_unchecked or 0)

            if not selected_lot and not allow_no_lot:
                selected_lot = resolved_base_lot

            base_status_row = ss_warehouse_allocations._select_prep_status_row(cur, base_upc, selected_lot, columns='status, quantity')
            base_status_value = (
                str(base_status_row[0] or '').strip().lower()
                if base_status_row else ''
            )
            move_from_good = (base_status_value == 'good' and current_good > 0)
            source_bucket = 'good' if move_from_good else 'unchecked'
            source_available = current_good if move_from_good else current_unchecked

            if qty > source_available:
                exception_overage_applied = True
                if allow_overage_exception:
                    print(
                        f'[BAD][EXCEPTION] Allowing base-to-bad overage for {base_upc}: '
                        f'source={source_bucket}, requested={qty}, available={source_available}'
                    )
                else:
                    print(
                        f'[BAD][EXCEPTION][AUTO] Base-to-bad gate bypassed for {base_upc}: '
                        f'source={source_bucket}, requested={qty}, available={source_available}'
                    )

            remaining_good = max(0, current_good - qty) if move_from_good else current_good
            remaining_unchecked = current_unchecked if move_from_good else max(0, current_unchecked - qty)
            new_bad = current_bad + qty

            if selected_lot:
                cur.execute('''
                    UPDATE bol_items
                    SET good_qty = ?, bad_qty = ?, unchecked_qty = ?, lot_number = ?
                    WHERE id = ?
                ''', (remaining_good, new_bad, remaining_unchecked, selected_lot, bol_id))
            else:
                cur.execute('''
                    UPDATE bol_items
                    SET good_qty = ?, bad_qty = ?, unchecked_qty = ?
                    WHERE id = ?
                ''', (remaining_good, new_bad, remaining_unchecked, bol_id))

            print(
                f'[BAD] Updated base {base_upc}: good_qty {current_good}->{remaining_good}, '
                f'bad_qty {current_bad}->{new_bad}, unchecked_qty {current_unchecked}->{remaining_unchecked}'
            )

            base_status_lot = ss_warehouse_allocations._resolve_prep_status_lot(cur, base_upc, selected_lot)
            base_remaining_status = 'good' if remaining_good > 0 else ('unchecked' if remaining_unchecked > 0 else '')
            base_remaining_qty = remaining_good if remaining_good > 0 else remaining_unchecked
            existing_base_status = ss_warehouse_allocations._select_prep_status_row(cur, base_upc, selected_lot, columns='upc')

            if base_remaining_status:
                if existing_base_status:
                    cur.execute('''
                        UPDATE items_prep_status
                        SET status = ?, reason = '', note = '', quantity = ?, updated_at = ?
                        WHERE upc = ? COLLATE NOCASE
                          AND COALESCE(lot_number, '') = ? COLLATE NOCASE
                    ''', (base_remaining_status, base_remaining_qty, ts, base_upc, base_status_lot))
                else:
                    cur.execute('''
                        INSERT INTO items_prep_status (upc, lot_number, status, reason, note, quantity, updated_at)
                        VALUES (?,?,?,?,?,?,?)
                    ''', (base_upc, ss_normalization._normalize_lot_number(selected_lot), base_remaining_status, '', '', base_remaining_qty, ts))
            elif existing_base_status:
                cur.execute('''
                    DELETE FROM items_prep_status
                    WHERE upc = ? COLLATE NOCASE
                      AND COALESCE(lot_number, '') = ? COLLATE NOCASE
                ''', (base_upc, base_status_lot))

            suffixed_upc = _next_clean_suffix(base_upc)
            print(f'[BAD] Allocated suffix {suffixed_upc} for base-to-bad conversion')

            cur.execute('''
                INSERT INTO bol_items (
                    upc, item_description, image_url, lot_number, bol_number, import_date,
                    temporary, original_qty, unchecked_qty, good_qty, bad_qty, quantity
                )
                VALUES (?, ?, ?, ?, ?, ?, 0, ?, 0, 0, ?, ?)
            ''', (suffixed_upc, base_desc, base_img, selected_lot, base_bol, ts, qty, qty, qty))

            cur.execute('''
                INSERT INTO items_prep_status (upc, lot_number, status, reason, note, quantity, updated_at)
                VALUES (?,?,?,?,?,?,?)
            ''', (suffixed_upc, ss_normalization._normalize_lot_number(selected_lot), status, reason, note, qty, ts))
            print(f'[BAD] Created BAD rows for {suffixed_upc} with qty={qty}')

            if exception_overage_applied:
                ss_prep_schema._items_prep_record_exception(
                    cur,
                    upc=suffixed_upc,
                    base_upc=base_upc,
                    lot_number=selected_lot,
                    action='base_to_bad_overage_override',
                    quantity=qty,
                    unchecked_remaining=source_available,
                    source='item-prep',
                    note=exception_note or 'Base item converted to BAD as exception after source quantity was exhausted.',
                    meta={
                        'status': 'bad',
                        'requested_qty': qty,
                        'source_bucket': source_bucket,
                        'source_available': source_available,
                        'requested_lot': requested_lot_norm,
                        'assigned_lot': selected_lot,
                        'forced_lot_override': bool(force_requested_lot and selected_lot),
                        'legacy_quantity_before': current_bol_qty
                    }
                )
            
            # Commit all changes atomically
            conn.commit()

            # Update data version for cache invalidation
            ss_caching.update_data_version()

            # Prep log (preplog.db)
            try:
                log_note = note or ''
                try:
                    cur.execute('''
                        SELECT note
                        FROM items_prep_notes
                        WHERE upc = ? COLLATE NOCASE
                        ORDER BY created_at DESC, id DESC
                        LIMIT 1
                    ''', (suffixed_upc,))
                    nrow = cur.fetchone()
                    if nrow and nrow[0] and str(nrow[0]).strip():
                        log_note = str(nrow[0]).strip()
                except Exception:
                    pass
                converted_bad_meta = {
                    'action': 'converted_to_bad',
                    'requested_lot': requested_lot_norm,
                    'lot_number': selected_lot,
                    'assigned_lot': selected_lot
                }
                if selected_lot and (not requested_lot_norm or selected_lot.lower() != requested_lot_norm.lower()):
                    converted_bad_meta['auto_assigned'] = True
                ss_prep_log._preplog_add_entry(
                    upc=suffixed_upc,
                    base_upc=base_upc,
                    status='bad',
                    quantity=qty,
                    note=log_note or None,
                    reason=reason or None,
                    source='item-prep',
                    meta=converted_bad_meta,
                    dedupe=True
                )
            except Exception:
                pass

            return jsonify({
                'success': True,
                'upc': suffixed_upc,
                'action': 'converted_to_bad',
                'quantity': qty,
                'lot_number': selected_lot,
                'exception_overage': bool(exception_overage_applied)
            })

        if status == 'return' and '-' not in str(upc):
            print(f'[RETURN] Converting base item {base_upc} to RETURN')

            cur.execute('''
                SELECT id, item_description, image_url, lot_number, bol_number, unchecked_qty, bad_qty, original_qty, good_qty
                FROM bol_items
                WHERE upc = ? COLLATE NOCASE
                ORDER BY import_date DESC, id DESC
                LIMIT 1
            ''', (base_upc,))
            base_item = cur.fetchone()

            if not base_item:
                return jsonify({'success': False, 'error': f'Base UPC {base_upc} not found in bol_items'}), 404

            resolved_base_lot = ss_normalization._normalize_lot_number(base_item[3])
            base_row_id = int(base_item[0])
            unchecked = base_item[5]
            current_bad = int(base_item[6] or 0)
            original_qty = int(base_item[7] or 0)
            current_good = int(base_item[8] or 0)
            if unchecked is None:
                unchecked = max(0, original_qty - current_good - current_bad)
            else:
                unchecked = int(unchecked or 0)

            if qty > unchecked:
                exception_overage_applied = True
                if allow_overage_exception:
                    print(
                        f'[RETURN][EXCEPTION] Allowing overage return-entry for {base_upc}: '
                        f'requested={qty}, unchecked={unchecked}'
                    )
                else:
                    print(
                        f'[RETURN][EXCEPTION][AUTO] Unchecked gate bypassed for {base_upc}: '
                        f'requested={qty}, unchecked={unchecked}'
                    )

            suffixed_upc = _next_clean_suffix(base_upc)
            return_reason = reason or 'return'

            cur.execute('''
                INSERT INTO bol_items (
                    upc, item_description, image_url, lot_number, bol_number, import_date,
                    temporary, original_qty, unchecked_qty, good_qty, bad_qty, quantity
                )
                VALUES (?, ?, ?, ?, ?, ?, 0, ?, 0, ?, 0, ?)
            ''', (suffixed_upc, base_item[1], base_item[2], '', base_item[4], ts, qty, qty, qty))

            cur.execute('''
                INSERT INTO items_prep_status (upc, lot_number, status, reason, note, quantity, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            ''', (suffixed_upc, '', 'return', return_reason, note, qty, ts))

            new_unchecked = max(0, unchecked - qty)
            cur.execute('''
                UPDATE bol_items
                SET unchecked_qty = ?
                WHERE id = ?
            ''', (new_unchecked, base_row_id))

            if exception_overage_applied:
                ss_prep_schema._items_prep_record_exception(
                    cur,
                    upc=suffixed_upc,
                    base_upc=base_upc,
                    lot_number='',
                    action='return_overage_override',
                    quantity=qty,
                    unchecked_remaining=unchecked,
                    source='item-prep',
                    note=exception_note or 'Return entry created as exception after unchecked quantity was exhausted.',
                    meta={
                        'status': 'return',
                        'requested_qty': qty,
                        'unchecked_before': unchecked,
                        'allow_overage_exception': True,
                        'requested_lot': requested_lot_norm,
                        'assigned_lot': '',
                        'forced_lot_override': False,
                        'source_lot': resolved_base_lot
                    }
                )

            conn.commit()
            ss_caching.update_data_version()

            try:
                ss_prep_log._preplog_add_entry(
                    upc=suffixed_upc,
                    base_upc=base_upc,
                    status='return',
                    quantity=qty,
                    note=note or None,
                    reason=return_reason,
                    source='item-prep',
                    meta={
                        'action': 'converted_to_return',
                        'requested_lot': requested_lot_norm,
                        'lot_number': '',
                        'assigned_lot': '',
                        'continued_without_lot': True,
                        'source_lot': resolved_base_lot,
                        'exception_overage': bool(exception_overage_applied),
                        'unchecked_before': unchecked if exception_overage_applied else None
                    },
                    dedupe=True
                )
            except Exception:
                pass

            return jsonify({
                'success': True,
                'upc': suffixed_upc,
                'base_upc': base_upc,
                'action': 'converted_to_return',
                'quantity': qty,
                'lot_number': '',
                'continued_without_lot': True,
                'exception_overage': bool(exception_overage_applied)
            })

        # Normal BAD/UNCHECKED flow - UPC already has suffix or is being updated
        # Just update the existing entry directly
        if allow_no_lot:
            status_lot = ''
        elif force_requested_lot and requested_lot_norm:
            status_lot = requested_lot_norm
        else:
            status_lot = ss_warehouse_allocations._resolve_bol_lot_for_upc(cur, upc, requested_lot)
            if not status_lot and upc != base_upc and status == 'bad':
                status_lot = ss_warehouse_allocations._resolve_bol_lot_for_upc(cur, base_upc, requested_lot)
        status_update_lot = ss_warehouse_allocations._resolve_prep_status_lot(cur, upc, status_lot)
        existing_row = ss_warehouse_allocations._select_prep_status_row(cur, upc, status_lot, columns='upc')
        if existing_row:
            cur.execute('''
                UPDATE items_prep_status
                SET status=?, reason=?, note=?, quantity=?, updated_at=?
                WHERE upc=? COLLATE NOCASE
                  AND COALESCE(lot_number, '') = ? COLLATE NOCASE
            ''', (status, reason, note, qty, ts, upc, status_update_lot))
            print(f'[{status.upper()}] Updated existing entry {upc}')
        else:
            cur.execute('''
                INSERT INTO items_prep_status (upc, lot_number, status, reason, note, quantity, updated_at)
                VALUES (?,?,?,?,?,?,?)
            ''', (upc, ss_normalization._normalize_lot_number(status_lot), status, reason, note, qty, ts))
            print(f'[{status.upper()}] Created new entry {upc}')
        conn.commit()
        

        # Update data version for cache invalidation
        ss_caching.update_data_version()

        # Prep log (preplog.db) — only for BAD (we don't log unchecked).
        if status == 'bad':
            try:
                log_note = note or ''
                try:
                    cur.execute('''
                        SELECT note
                        FROM items_prep_notes
                        WHERE upc = ? COLLATE NOCASE
                        ORDER BY created_at DESC, id DESC
                        LIMIT 1
                    ''', (upc,))
                    nrow = cur.fetchone()
                    if nrow and nrow[0] and str(nrow[0]).strip():
                        log_note = str(nrow[0]).strip()
                except Exception:
                    pass
                updated_bad_meta = {
                    'action': 'updated',
                    'requested_lot': requested_lot_norm,
                    'lot_number': status_lot,
                    'assigned_lot': status_lot
                }
                if status_lot and (not requested_lot_norm or status_lot.lower() != requested_lot_norm.lower()):
                    updated_bad_meta['auto_assigned'] = True
                ss_prep_log._preplog_add_entry(
                    upc=upc,
                    base_upc=base_upc,
                    status='bad',
                    quantity=qty,
                    note=log_note or None,
                    reason=reason or None,
                    source='item-prep',
                    meta=updated_bad_meta,
                    dedupe=True
                )
            except Exception:
                pass

        return jsonify({'success': True, 'upc': upc, 'action': 'updated', 'quantity': qty})
        
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': ss_errors._safe_error(e)}), 500
    finally:
        if conn is not None:
            conn.close()


def api_items_prep_allocate_lots():
    """Handle multi-LOT good marking with qty distribution across LOTs.
    JSON: { upc, allocations: [{lot_id, qty}], reason, note }
    """
    conn = None
    try:
        data = request.get_json() or {}
        upc = ss_normalization._normalize_upc(data.get('upc'))
        allocations = data.get('allocations', [])  # [{lot_id, qty}, ...]
        reason = (data.get('reason') or '').strip()
        note = (data.get('note') or '').strip()
        
        if not upc or not allocations:
            return jsonify({'success': False, 'error': 'Missing upc or allocations'}), 400
        
        # Strip leading zeros
        base_upc = ss_normalization._strip_leading_zeros_numeric(upc)
        
        conn = sqlite3.connect('bol.db')
        cur = conn.cursor()
        ss_prep_schema._ensure_items_prep_tables()
        
        import datetime
        ts = datetime.datetime.now(datetime.UTC).isoformat()
        
        total_qty_allocated = 0
        results = []
        
        # Process each LOT allocation
        for alloc in allocations:
            lot_id = alloc.get('id')
            qty = int(alloc.get('qty', 0))
            
            if qty <= 0:
                continue
            
            # Get LOT details and validate
            cur.execute('''
                SELECT lot_number, good_qty, unchecked_qty, original_qty 
                FROM bol_items 
                WHERE id = ? AND upc = ? COLLATE NOCASE
            ''', (lot_id, base_upc))
            
            lot_row = cur.fetchone()
            if not lot_row:
                return jsonify({'success': False, 'error': f'LOT with id {lot_id} not found'}), 404
            
            lot_number, current_good, current_unchecked, original_qty = lot_row
            current_good = current_good or 0
            current_unchecked = current_unchecked or 0
            original_qty = original_qty or 0
            
            # Validate sufficient unchecked qty
            if qty > current_unchecked:
                return jsonify({
                    'success': False,
                    'error': f'LOT {lot_number}: Cannot mark {qty} as good - only {current_unchecked} unchecked'
                }), 400
            
            # Update quantities for this LOT
            new_good = current_good + qty
            new_unchecked = current_unchecked - qty
            
            cur.execute('''
                UPDATE bol_items 
                SET good_qty = ?, unchecked_qty = ?, quantity = ?
                WHERE id = ?
            ''', (new_good, new_unchecked, new_good, lot_id))
            
            total_qty_allocated += qty
            results.append({
                'lot_number': lot_number,
                'qty': qty,
                'good_qty': new_good,
                'unchecked_qty': new_unchecked
            })
            
            print(f'[GOOD-LOT] {base_upc} LOT {lot_number}: moved {qty} from unchecked to good (good: {current_good}→{new_good}, unchecked: {current_unchecked}→{new_unchecked})')
        
        # Update prep status per lot so one UPC can be tracked independently across multiple lots.
        for lot_update in results:
            lot_number = ss_normalization._normalize_lot_number(lot_update.get('lot_number'))
            lot_qty = int(lot_update.get('qty') or 0)
            if lot_qty <= 0:
                continue

            existing_row = ss_warehouse_allocations._select_prep_status_row(cur, base_upc, lot_number, columns='status, quantity')
            status_update_lot = ss_warehouse_allocations._resolve_prep_status_lot(cur, base_upc, lot_number)
            if existing_row:
                existing_status = (existing_row[0] or '').strip().lower()
                existing_qty = int(existing_row[1] or 0)
                if existing_status == 'good':
                    next_qty = existing_qty + lot_qty
                else:
                    next_qty = lot_qty
                cur.execute('''
                    UPDATE items_prep_status
                    SET status = 'good',
                        reason = ?,
                        note = ?,
                        quantity = ?,
                        updated_at = ?
                    WHERE upc = ? COLLATE NOCASE
                      AND COALESCE(lot_number, '') = ? COLLATE NOCASE
                ''', (reason, note, next_qty, ts, base_upc, status_update_lot))
            else:
                cur.execute('''
                    INSERT INTO items_prep_status (upc, lot_number, status, reason, note, quantity, updated_at)
                    VALUES (?,?,?,?,?,?,?)
                ''', (base_upc, lot_number, 'good', reason, note, lot_qty, ts))

        cur.execute('''
            SELECT SUM(COALESCE(quantity, 0))
            FROM items_prep_status
            WHERE upc = ? COLLATE NOCASE
              AND status = 'good'
        ''', (base_upc,))
        new_prep_qty = cur.fetchone()[0] or 0
        
        conn.commit()

        # Update data version for cache invalidation
        try:
            ss_caching.update_data_version()
        except Exception:
            pass

        # Prep log (preplog.db)
        try:
            ss_prep_log._preplog_add_entry(
                upc=base_upc,
                base_upc=base_upc,
                status='good',
                quantity=total_qty_allocated,
                note=note or None,
                reason=reason or None,
                source='item-prep',
                meta={'action': 'allocate_lots', 'lots_updated': results},
                dedupe=False
            )
        except Exception:
            pass
        
        return jsonify({
            'success': True,
            'upc': base_upc,
            'total_qty': total_qty_allocated,
            'total_good': new_prep_qty,
            'lots_updated': results
        })
        
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': ss_errors._safe_error(e)}), 500
    finally:
        if conn is not None:
            conn.close()


def api_items_prep_create_bad_entry():
    """Create a temporary suffixed entry for Bad flow.
    This creates the suffix and temporary bol_items entry and immediately moves qty from
    the base lot's unchecked bucket into bad bucket.
    JSON: { upc, qty }
    Returns: { success, suffixed_upc }
    """
    conn = None
    try:
        data = request.get_json() or {}
        upc = ss_normalization._strip_leading_zeros_numeric(ss_normalization._normalize_upc(data.get('upc')))
        exception_note = (data.get('exception_note') or '').strip()
        try:
            qty = int(data.get('qty', 1))
        except Exception:
            return jsonify({'success': False, 'error': 'Invalid qty'}), 400
        if qty < 1:
            return jsonify({'success': False, 'error': 'qty must be at least 1'}), 400
        
        if not upc:
            return jsonify({'success': False, 'error': 'Missing upc'}), 400
        # Strip leading zeros
        upc = ss_normalization._normalize_upc_preserve_suffix_for_match(upc)
        
        base_upc = upc.split('-')[0] if '-' in upc else upc
        requested_lot_input = ss_normalization._normalize_lot_number(ss_warehouse_allocations._preferred_lot_from_request(data))
        requested_lot = requested_lot_input
        allow_no_lot = ss_normalization._coerce_bool(data.get('allow_no_lot'))
        force_requested_lot = ss_normalization._coerce_bool(
            data.get('force_requested_lot') if data.get('force_requested_lot') is not None else data.get('override_lot')
        )
        allow_overage_exception = ss_normalization._coerce_bool(
            data.get('allow_overage_exception') if data.get('allow_overage_exception') is not None else data.get('force_exception')
        )
        exception_overage_applied = False
        if allow_no_lot:
            requested_lot = ''
            force_requested_lot = False
        
        conn = sqlite3.connect('bol.db', isolation_level='IMMEDIATE')
        cur = conn.cursor()
        ss_prep_schema._ensure_items_prep_tables()
        requested_lot_norm = ss_normalization._normalize_lot_number(requested_lot)
        lot_info = {
            'requested_lot': requested_lot_norm,
            'assigned_lot': '',
            'auto_assigned': False,
            'auto_assign_reason': ''
        }
        selected_lot = ''
        if not allow_no_lot:
            lot_mismatch, suggested_lot = ss_warehouse_allocations._check_requested_lot_mismatch(cur, base_upc, requested_lot_norm)
            if lot_mismatch and not force_requested_lot:
                return jsonify(ss_warehouse_allocations._lot_mismatch_payload(
                    upc=base_upc,
                    requested_lot=requested_lot_norm,
                    suggested_lot=suggested_lot
                )), 409
            if force_requested_lot and requested_lot_norm:
                selected_lot = requested_lot_norm
                lot_info = {
                    'requested_lot': requested_lot_norm,
                    'assigned_lot': selected_lot,
                    'auto_assigned': False,
                    'auto_assign_reason': ''
                }
            else:
                lot_info = ss_warehouse_allocations._resolve_action_lot_assignment(cur, base_upc, requested_lot_norm)
                selected_lot = ss_normalization._normalize_lot_number(lot_info.get('assigned_lot'))
        
        # Find next available suffix for bad items
        # Check BOTH bol_items AND prep tables to avoid reusing deleted suffixes with orphaned data
        suffix_num = 1
        while True:
            suffixed_upc = f"{base_upc}-{suffix_num}"
            
            # Check if exists in bol_items
            cur.execute('SELECT upc FROM bol_items WHERE upc = ? COLLATE NOCASE', (suffixed_upc,))
            if cur.fetchone():
                suffix_num += 1
                continue
            
            # Also check if prep data exists (images, status, notes) to avoid reusing old suffixes
            cur.execute('SELECT upc FROM items_prep_images WHERE upc = ? COLLATE NOCASE LIMIT 1', (suffixed_upc,))
            if cur.fetchone():
                suffix_num += 1
                continue
            
            cur.execute('SELECT upc FROM items_prep_status WHERE upc = ? COLLATE NOCASE LIMIT 1', (suffixed_upc,))
            if cur.fetchone():
                suffix_num += 1
                continue
            
            try:
                cur.execute('SELECT upc FROM items_prep_notes WHERE upc = ? COLLATE NOCASE LIMIT 1', (suffixed_upc,))
                if cur.fetchone():
                    suffix_num += 1
                    continue
            except Exception:
                pass
            try:
                cur.execute('SELECT upc FROM items_prep_media WHERE upc = ? COLLATE NOCASE LIMIT 1', (suffixed_upc,))
                if cur.fetchone():
                    suffix_num += 1
                    continue
            except Exception:
                pass
            
            # Suffix is clean - no bol_items entry and no orphaned prep data
            break
        
        # Get base item details to copy and check unchecked quantity.
        if selected_lot:
            cur.execute('''
                SELECT id, item_description, image_url, lot_number, bol_number, unchecked_qty, bad_qty, original_qty, good_qty
                FROM bol_items
                WHERE upc = ? COLLATE NOCASE
                  AND lot_number = ? COLLATE NOCASE
                ORDER BY import_date DESC, id DESC
                LIMIT 1
            ''', (base_upc, selected_lot))
            base_item = cur.fetchone()
        else:
            base_item = None
        if not base_item:
            cur.execute('''
                SELECT id, item_description, image_url, lot_number, bol_number, unchecked_qty, bad_qty, original_qty, good_qty
                FROM bol_items
                WHERE upc = ? COLLATE NOCASE
                ORDER BY import_date DESC, id DESC
                LIMIT 1
            ''', (base_upc,))
            base_item = cur.fetchone()
        
        if not base_item:
            return jsonify({'success': False, 'error': f'Base UPC {base_upc} not found in bol_items'}), 404

        resolved_base_lot = ss_normalization._normalize_lot_number(base_item[3])
        if not selected_lot and not allow_no_lot:
            selected_lot = resolved_base_lot
            if selected_lot:
                lot_info = {
                    'requested_lot': requested_lot_norm,
                    'assigned_lot': selected_lot,
                    'auto_assigned': bool(selected_lot and (not requested_lot_norm or selected_lot.lower() != requested_lot_norm.lower())),
                    'auto_assign_reason': (
                        'requested_lot_unavailable_used_latest'
                        if requested_lot_norm and selected_lot.lower() != requested_lot_norm.lower()
                        else ('no_lot_requested_used_latest' if selected_lot and not requested_lot_norm else '')
                    )
                }
        requested_lot_norm = ss_normalization._normalize_lot_number(lot_info.get('requested_lot') or requested_lot_norm)
        auto_assigned = bool(lot_info.get('auto_assigned')) or bool(
            selected_lot and (not requested_lot_norm or selected_lot.lower() != requested_lot_norm.lower())
        )
        forced_lot_override = bool(force_requested_lot and selected_lot)
        auto_assign_reason = lot_info.get('auto_assign_reason') or (
            'requested_lot_unavailable_used_latest' if requested_lot_norm and auto_assigned else (
                'no_lot_requested_used_latest' if auto_assigned else ''
            )
        )
        
        base_row_id = base_item[0]
        unchecked = base_item[5]
        current_bad = base_item[6] or 0
        original_qty = base_item[7] or 0
        current_good = base_item[8] or 0

        if unchecked is None:
            unchecked = max(0, original_qty - current_good - current_bad)
        if qty > unchecked:
            exception_overage_applied = True
            if allow_overage_exception:
                print(
                    f'[BAD][EXCEPTION] Allowing overage bad-entry for {base_upc}: '
                    f'requested={qty}, unchecked={unchecked}'
                )
            else:
                print(
                    f'[BAD][EXCEPTION][AUTO] Unchecked gate bypassed for {base_upc}: '
                    f'requested={qty}, unchecked={unchecked}'
                )
        
        # Create temporary suffixed entry with new quantity columns
        import datetime
        import_date = datetime.datetime.now(datetime.UTC).isoformat()
        
        cur.execute('''
            INSERT INTO bol_items (
                upc, item_description, image_url, lot_number, bol_number, import_date, 
                temporary, original_qty, unchecked_qty, bad_qty, good_qty, quantity
            )
            VALUES (?, ?, ?, ?, ?, ?, 1, ?, 0, ?, 0, ?)
        ''', (suffixed_upc, base_item[1], base_item[2], selected_lot, base_item[4], import_date, 
              qty, qty, qty))  # original_qty=qty, bad_qty=qty, quantity=qty for this suffixed entry
        
        # Seed prep status immediately so Items-to-List visibility stays in sync
        # even before diagnostic completion posts the final reason/note payload.
        cur.execute('''
            INSERT INTO items_prep_status (upc, lot_number, status, reason, note, quantity, updated_at)
            VALUES (?, ?, 'bad', '', '', ?, ?)
        ''', (suffixed_upc, ss_normalization._normalize_lot_number(selected_lot), qty, import_date))
        
        # Update base item: move qty from unchecked to bad
        new_unchecked = max(0, unchecked - qty)
        new_bad = current_bad + qty
        
        if force_requested_lot and selected_lot:
            cur.execute('''
                UPDATE bol_items 
                SET unchecked_qty = ?, bad_qty = ?, lot_number = ?
                WHERE id = ?
            ''', (new_unchecked, new_bad, selected_lot, base_row_id))
        else:
            cur.execute('''
                UPDATE bol_items 
                SET unchecked_qty = ?, bad_qty = ?
                WHERE id = ?
            ''', (new_unchecked, new_bad, base_row_id))

        if exception_overage_applied:
            ss_prep_schema._items_prep_record_exception(
                cur,
                upc=suffixed_upc,
                base_upc=base_upc,
                lot_number=selected_lot,
                action='bad_overage_override',
                quantity=qty,
                unchecked_remaining=unchecked,
                source='item-prep',
                note=exception_note or 'BAD entry created as exception after unchecked quantity was exhausted.',
                meta={
                    'status': 'bad',
                    'requested_qty': qty,
                    'unchecked_before': unchecked,
                    'allow_overage_exception': True,
                    'requested_lot': requested_lot_norm,
                    'assigned_lot': selected_lot,
                    'forced_lot_override': bool(forced_lot_override)
                }
            )
        
        conn.commit()
        
        print(f'[BAD] Created temporary suffixed entry: {suffixed_upc} (temporary=1, qty={qty})')
        print(f'[BAD] {base_upc}: moved {qty} from unchecked to bad (bad: {current_bad}→{new_bad}, unchecked: {unchecked}→{new_unchecked})')
        

        # Update data version for cache invalidation
        ss_caching.update_data_version()

        # Prep log (preplog.db): keep lot-assignment details so auto-assigned items can be reviewed.
        try:
            ss_prep_log._preplog_add_entry(
                upc=suffixed_upc,
                base_upc=base_upc,
                status='bad',
                quantity=qty,
                note=None,
                reason=None,
                source='item-prep',
                meta={
                    'action': 'create_bad_entry',
                    'requested_lot': requested_lot_norm,
                    'lot_number': selected_lot,
                    'assigned_lot': selected_lot,
                    'auto_assigned': bool(auto_assigned),
                    'auto_assign_reason': auto_assign_reason,
                    'continued_without_lot': bool(allow_no_lot),
                    'source_lot': resolved_base_lot,
                    'forced_lot_override': bool(forced_lot_override),
                    'needs_review': bool(auto_assigned or forced_lot_override),
                    'exception_overage': bool(exception_overage_applied),
                    'unchecked_before': unchecked if exception_overage_applied else None
                },
                dedupe=True
            )
        except Exception:
            pass

        return jsonify({
            'success': True, 
            'suffixed_upc': suffixed_upc, 
            'base_upc': base_upc,
            'lot_number': selected_lot,
            'requested_lot': requested_lot_norm,
            'auto_assigned': bool(auto_assigned),
            'auto_assign_reason': auto_assign_reason,
            'forced_lot_override': bool(forced_lot_override),
            'continued_without_lot': bool(allow_no_lot),
            'unchecked_qty': new_unchecked,
            'bad_qty': new_bad,
            'exception_overage': bool(exception_overage_applied),
            'items_to_list_url': ss_warehouse_allocations._items_prep_items_to_list_url(base_upc, selected_lot)
        })
        
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': ss_errors._safe_error(e)}), 500
    finally:
        try:
            if conn:
                conn.close()
        except Exception:
            pass


def api_items_prep_create_return_entry():
    """Create a suffixed entry for return flow.
    Similar to bad flow but tracks status as 'return'.
    JSON: { upc, qty }
    Returns: { success, suffixed_upc }
    """
    conn = None
    try:
        data = request.get_json() or {}
        upc = ss_normalization._strip_leading_zeros_numeric(ss_normalization._normalize_upc(data.get('upc')))
        submission_id = str(data.get('submission_id') or '').strip()
        exception_note = (data.get('exception_note') or '').strip()
        try:
            qty = int(data.get('qty', 1))
        except Exception:
            return jsonify({'success': False, 'error': 'Invalid qty'}), 400
        if qty < 1:
            return jsonify({'success': False, 'error': 'qty must be at least 1'}), 400
        
        if not upc:
            return jsonify({'success': False, 'error': 'Missing upc'}), 400
        if not submission_id or len(submission_id) > 128 or not re.fullmatch(r'[A-Za-z0-9._:-]+', submission_id):
            return jsonify({'success': False, 'error': 'Missing or invalid submission_id'}), 400
        
        # Strip leading zeros
        upc = ss_normalization._normalize_upc_preserve_suffix_for_match(upc)
        
        base_upc = upc.split('-')[0] if '-' in upc else upc
        requested_lot_input = ss_normalization._normalize_lot_number(ss_warehouse_allocations._preferred_lot_from_request(data))
        requested_lot = ''
        allow_no_lot = True
        force_requested_lot = False
        allow_overage_exception = ss_normalization._coerce_bool(
            data.get('allow_overage_exception') if data.get('allow_overage_exception') is not None else data.get('force_exception')
        )
        exception_overage_applied = False
        
        conn = sqlite3.connect('bol.db', isolation_level='IMMEDIATE')
        cur = conn.cursor()
        ss_prep_schema._ensure_items_prep_tables()
        cur.execute('BEGIN IMMEDIATE')
        cur.execute('''
            CREATE TABLE IF NOT EXISTS items_prep_submission_keys (
                submission_id TEXT PRIMARY KEY,
                action TEXT NOT NULL,
                base_upc TEXT NOT NULL,
                response_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
        ''')
        cur.execute('''
            SELECT action, base_upc, response_json
            FROM items_prep_submission_keys
            WHERE submission_id = ?
            LIMIT 1
        ''', (submission_id,))
        prior_submission = cur.fetchone()
        if prior_submission:
            if prior_submission[0] != 'return' or prior_submission[1].casefold() != base_upc.casefold():
                return jsonify({'success': False, 'error': 'submission_id was already used for another action'}), 409
            try:
                prior_payload = json.loads(prior_submission[2])
            except Exception:
                prior_payload = {'success': False, 'error': 'Stored submission result is invalid'}
            prior_payload['idempotent_replay'] = True
            return jsonify(prior_payload)
        requested_lot_norm = ''
        selected_lot = ''
        
        # Find next available suffix for return items
        suffix_num = 1
        while True:
            suffixed_upc = f"{base_upc}-{suffix_num}"
            
            # Check if exists in bol_items
            cur.execute('SELECT upc FROM bol_items WHERE upc = ? COLLATE NOCASE', (suffixed_upc,))
            if cur.fetchone():
                suffix_num += 1
                continue
            
            # Also check prep tables
            cur.execute('SELECT upc FROM items_prep_status WHERE upc = ? COLLATE NOCASE LIMIT 1', (suffixed_upc,))
            if cur.fetchone():
                suffix_num += 1
                continue
            
            cur.execute('SELECT upc FROM items_prep_images WHERE upc = ? COLLATE NOCASE LIMIT 1', (suffixed_upc,))
            if cur.fetchone():
                suffix_num += 1
                continue
            try:
                cur.execute('SELECT upc FROM items_prep_notes WHERE upc = ? COLLATE NOCASE LIMIT 1', (suffixed_upc,))
                if cur.fetchone():
                    suffix_num += 1
                    continue
            except Exception:
                pass
            try:
                cur.execute('SELECT upc FROM items_prep_media WHERE upc = ? COLLATE NOCASE LIMIT 1', (suffixed_upc,))
                if cur.fetchone():
                    suffix_num += 1
                    continue
            except Exception:
                pass
            
            # Suffix is available
            break
        
        # Return rows stay lotless; use the latest base UPC row without lot matching.
        cur.execute('''
            SELECT id, item_description, image_url, lot_number, bol_number, unchecked_qty, bad_qty, original_qty, good_qty
            FROM bol_items
            WHERE upc = ? COLLATE NOCASE
            ORDER BY import_date DESC, id DESC
            LIMIT 1
        ''', (base_upc,))
        base_item = cur.fetchone()
        
        if not base_item:
            return jsonify({'success': False, 'error': f'Base UPC {base_upc} not found in bol_items'}), 404

        resolved_base_lot = ss_normalization._normalize_lot_number(base_item[3])
        selected_lot = ''
        requested_lot_norm = ''
        auto_assigned = False
        forced_lot_override = False
        auto_assign_reason = ''

        base_row_id = int(base_item[0])
        unchecked = base_item[5]
        current_bad = int(base_item[6] or 0)
        original_qty = int(base_item[7] or 0)
        current_good = int(base_item[8] or 0)
        if unchecked is None:
            unchecked = max(0, original_qty - current_good - current_bad)
        else:
            unchecked = int(unchecked or 0)
        if qty > unchecked:
            exception_overage_applied = True
            if allow_overage_exception:
                print(
                    f'[RETURN][EXCEPTION] Allowing overage return-entry for {base_upc}: '
                    f'requested={qty}, unchecked={unchecked}'
                )
            else:
                print(
                    f'[RETURN][EXCEPTION][AUTO] Unchecked gate bypassed for {base_upc}: '
                    f'requested={qty}, unchecked={unchecked}'
                )
        
        # Create suffixed entry in bol_items as a permanent RETURN row.
        import datetime
        import_date = datetime.datetime.now(datetime.UTC).isoformat()
        
        cur.execute('''
            INSERT INTO bol_items (
                upc, item_description, image_url, lot_number, bol_number, import_date, 
                temporary, original_qty, unchecked_qty, good_qty, bad_qty, quantity
            )
            VALUES (?, ?, ?, ?, ?, ?, 0, ?, 0, ?, 0, ?)
        ''', (suffixed_upc, base_item[1], base_item[2], selected_lot, base_item[4], import_date, 
              qty, qty, qty))  # original_qty=qty, good_qty=qty, quantity=qty for this suffixed entry
        
        # Seed prep status so return rows are visible and can be finalized in diagnostic.
        ts = datetime.datetime.now(datetime.UTC).isoformat()
        cur.execute('''
            INSERT INTO items_prep_status (upc, lot_number, status, reason, note, updated_at, quantity)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        ''', (suffixed_upc, ss_normalization._normalize_lot_number(selected_lot), 'return', 'return', '', ts, qty))

        # Return flow consumes unchecked units from the base row.
        new_unchecked = max(0, unchecked - qty)
        cur.execute('''
            UPDATE bol_items
            SET unchecked_qty = ?
            WHERE id = ?
        ''', (new_unchecked, base_row_id))

        if exception_overage_applied:
            ss_prep_schema._items_prep_record_exception(
                cur,
                upc=suffixed_upc,
                base_upc=base_upc,
                lot_number=selected_lot,
                action='return_overage_override',
                quantity=qty,
                unchecked_remaining=unchecked,
                source='item-prep',
                note=exception_note or 'Return entry created as exception after unchecked quantity was exhausted.',
                meta={
                    'status': 'return',
                    'requested_qty': qty,
                    'unchecked_before': unchecked,
                    'allow_overage_exception': True,
                    'requested_lot': '',
                    'assigned_lot': selected_lot,
                    'forced_lot_override': False,
                    'source_lot': resolved_base_lot,
                    'ignored_requested_lot': requested_lot_input
                }
            )
        
        response_payload = {
            'success': True,
            'suffixed_upc': suffixed_upc,
            'base_upc': base_upc,
            'lot_number': selected_lot,
            'requested_lot': requested_lot_norm,
            'auto_assigned': False,
            'auto_assign_reason': auto_assign_reason,
            'forced_lot_override': False,
            'continued_without_lot': True,
            'unchecked_qty': new_unchecked,
            'exception_overage': bool(exception_overage_applied),
            'items_to_list_url': ss_warehouse_allocations._items_prep_items_to_list_url(base_upc, '')
        }
        cur.execute('''
            INSERT INTO items_prep_submission_keys
                (submission_id, action, base_upc, response_json, created_at)
            VALUES (?, 'return', ?, ?, ?)
        ''', (submission_id, base_upc, json.dumps(response_payload, separators=(',', ':')), ts))
        conn.commit()
        
        print(
            f'[RETURN] Created return entry {suffixed_upc}: '
            f'consumed {qty} unchecked from {base_upc} '
            f'(unchecked {unchecked}->{new_unchecked})'
        )
        

        # Update data version for cache invalidation
        ss_caching.update_data_version()

        # Prep log (preplog.db)
        try:
            ss_prep_log._preplog_add_entry(
                upc=suffixed_upc,
                base_upc=base_upc,
                status='return',
                quantity=qty,
                note=None,
                reason='return',
                source='item-prep',
                meta={
                    'action': 'create_return_entry',
                    'requested_lot': '',
                    'lot_number': selected_lot,
                    'assigned_lot': selected_lot,
                    'auto_assigned': False,
                    'auto_assign_reason': auto_assign_reason,
                    'continued_without_lot': True,
                    'source_lot': resolved_base_lot,
                    'forced_lot_override': False,
                    'needs_review': False,
                    'exception_overage': bool(exception_overage_applied),
                    'unchecked_before': unchecked if exception_overage_applied else None,
                    'ignored_requested_lot': requested_lot_input
                },
                dedupe=True
            )
        except Exception:
            pass

        return jsonify(response_payload)
        
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': ss_errors._safe_error(e)}), 500
    finally:
        try:
            if conn:
                conn.close()
        except Exception:
            pass


def api_items_prep_good_entries(base_upc):
    """Return prep-history entries for a base UPC (including suffixes), each with its first image."""
    try:
        upc_n = ss_normalization._normalize_upc_preserve_suffix_for_match(ss_normalization._normalize_upc(base_upc))
        base = upc_n.split('-', 1)[0] if '-' in upc_n else upc_n
        if not base:
            return jsonify({'success': False, 'error': 'Missing base UPC'}), 400
        ss_prep_schema._ensure_items_prep_tables()
        inventory_fallback = ss_warehouse_matching._prep_history_inventory_fallback(base)
        with ss_database.db_connection('bol.db') as conn:
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()
            safe_base = base.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')
            cur.execute(
                "SELECT upc, status, note, updated_at, lot_number, quantity, "
                "       COALESCE(location, '') AS location, COALESCE(pictureposition, '') AS pictureposition "
                "FROM items_prep_status "
                "WHERE (upc = ? COLLATE NOCASE OR upc LIKE ? ESCAPE '\\') "
                "AND COALESCE(status, '') IN ('good', 'bad', 'return') "
                "ORDER BY updated_at DESC, id DESC",
                (base, f"{safe_base}-%")
            )
            rows = [dict(r) for r in cur.fetchall()]
            entries = []
            seen_keys = set()
            for row in rows:
                upc_entry = row['upc']
                upc_key = ss_warehouse_matching._sold_removal_barcode_key(upc_entry)
                if upc_key:
                    seen_keys.add(upc_key)
                status_value = ss_normalization._normalize_prep_row_status(row.get('status'))
                scope_upc, scope_status = ss_normalization._items_to_list_asset_scope(upc_entry, status_value)
                cur.execute(
                    "SELECT image_path FROM items_prep_images "
                    "WHERE upc = ? COLLATE NOCASE "
                    "AND COALESCE(row_status, '') = ? COLLATE NOCASE "
                    "AND (deleted_at IS NULL OR TRIM(COALESCE(deleted_at,'')) = '') "
                    "ORDER BY created_at DESC, id DESC LIMIT 1",
                    (scope_upc, scope_status)
                )
                img_row = cur.fetchone()
                image_url = f"/static/{img_row['image_path']}" if img_row and img_row['image_path'] else ''
                saved_location = str(row.get('location') or '').strip()
                saved_pictureposition = str(row.get('pictureposition') or '').strip()
                fallback_info = inventory_fallback.get(upc_key or '') or {}
                location_entries = []
                if saved_location or saved_pictureposition:
                    saved_label = ss_warehouse_matching._sold_location_label(saved_location or saved_pictureposition)
                    location_entries.append({
                        'location_code': saved_label,
                        'preview_key': saved_pictureposition or saved_location or saved_label,
                        'available_qty': max(0, ss_normalization._coerce_int(row.get('quantity'), 0))
                    })
                else:
                    location_entries = list(fallback_info.get('locations') or [])
                first_location = location_entries[0] if location_entries else {}
                entries.append({
                    'upc': upc_entry,
                    'status': status_value,
                    'note': row.get('note') or '',
                    'updated_at': row.get('updated_at') or '',
                    'lot_number': row.get('lot_number') or '',
                    'quantity': int(row.get('quantity') or 1),
                    'location_code': first_location.get('location_code') or '',
                    'location_preview': first_location.get('preview_key') or '',
                    'location_entries': location_entries,
                    'inventory_row_ids': list(fallback_info.get('row_ids') or []),
                    'pictureposition': saved_pictureposition or first_location.get('preview_key') or '',
                    'image_url': image_url,
                    'is_suffix': '-' in upc_entry
                })
            if not entries and inventory_fallback:
                for bucket in sorted(
                    inventory_fallback.values(),
                    key=lambda item: (str(item.get('updated_at') or ''), str(item.get('upc') or '')),
                    reverse=True
                ):
                    upc_entry = str(bucket.get('upc') or '').strip()
                    upc_key = ss_warehouse_matching._sold_removal_barcode_key(upc_entry)
                    if not upc_entry or (upc_key and upc_key in seen_keys):
                        continue
                    location_entries = list(bucket.get('locations') or [])
                    first_location = location_entries[0] if location_entries else {}
                    entries.append({
                        'upc': upc_entry,
                        'status': 'good',
                        'note': 'Already in inventory',
                        'updated_at': bucket.get('updated_at') or '',
                        'lot_number': '',
                        'quantity': max(1, ss_normalization._coerce_int(bucket.get('quantity'), 1)),
                        'location_code': first_location.get('location_code') or '',
                        'location_preview': first_location.get('preview_key') or '',
                        'location_entries': location_entries,
                        'inventory_row_ids': list(bucket.get('row_ids') or []),
                        'pictureposition': first_location.get('preview_key') or '',
                        'image_url': '',
                        'is_suffix': '-' in upc_entry,
                        'legacy_inventory': True
                    })
        # Bulk-fetch voice notes for all entries
        if entries:
            all_upcs = [e['upc'] for e in entries]
            voice_by_upc = {}
            try:
                placeholders = ','.join('?' * len(all_upcs))
                cur.execute(
                    f"SELECT upc, id, file_path, mime_type, created_at FROM items_prep_media "
                    f"WHERE upc IN ({placeholders}) COLLATE NOCASE AND COALESCE(media_type,'') = 'audio' "
                    f"ORDER BY created_at DESC, id DESC LIMIT 50",
                    all_upcs
                )
                for vr in cur.fetchall():
                    vr_dict = dict(vr)
                    rel = (vr_dict.get('file_path') or '').strip()
                    if not rel:
                        continue
                    uk = (vr_dict.get('upc') or '').upper()
                    audio_url = rel if rel.startswith(('http://', 'https://', '/')) else f"/static/{rel}"
                    voice_by_upc.setdefault(uk, []).append({
                        'id': vr_dict.get('id'),
                        'url': audio_url,
                        'mime_type': vr_dict.get('mime_type') or '',
                        'created_at': vr_dict.get('created_at') or ''
                    })
            except Exception:
                pass
            for e in entries:
                e['voice_notes'] = voice_by_upc.get((e.get('upc') or '').upper(), [])
        else:
            for e in entries:
                e['voice_notes'] = []
        return jsonify({'success': True, 'entries': entries})
    except Exception as e:
        return jsonify({'success': False, 'error': ss_errors._safe_error(e)}), 500


def api_items_prep_cleanup_temp():
    """Delete temporary entries when user skips. JSON: { upc }"""
    conn = None
    try:
        data = request.get_json() or {}
        upc = ss_normalization._normalize_upc(data.get('upc'))
        if not upc:
            return jsonify({'success': False, 'error': 'Missing upc'}), 400
        
        conn = sqlite3.connect('bol.db')
        cur = conn.cursor()
        # Delete temporary entries with this UPC
        cur.execute('DELETE FROM bol_items WHERE upc = ? COLLATE NOCASE AND temporary = 1', (upc,))
        deleted = cur.rowcount
        if deleted:
            # Keep prep status in sync when a temporary BAD entry is abandoned.
            cur.execute('DELETE FROM items_prep_status WHERE upc = ? COLLATE NOCASE', (upc,))
            ss_prep_media._items_prep_delete_diagnostic_assets(cur, upc)
        conn.commit()
        return jsonify({'success': True, 'deleted': deleted})
    except Exception as e:
        return jsonify({'success': False, 'error': ss_errors._safe_error(e)}), 500
    finally:
        if conn is not None:
            conn.close()


def api_bol_items_update_quantity():
    """Update quantity for a BOL item. JSON: { id?, upc?, lot_number?, quantity }"""
    conn = None
    try:
        data = request.get_json() or {}
        upc = ss_normalization._normalize_upc(data.get('upc'))
        quantity = data.get('quantity')
        lot_number = ss_normalization._normalize_lot_number(ss_warehouse_allocations._preferred_lot_from_request(data))
        item_id = data.get('id')
        source = ss_normalization._normalize_listing_source(data.get('source'), default='user')
        if quantity is None:
            return jsonify({'success': False, 'error': 'Missing quantity'}), 400
        if item_id is not None and str(item_id).strip() != '':
            try:
                item_id = int(item_id)
            except Exception:
                return jsonify({'success': False, 'error': 'Invalid id'}), 400
            if item_id <= 0:
                return jsonify({'success': False, 'error': 'Invalid id'}), 400
        else:
            item_id = None
        
        try:
            quantity = int(quantity)
        except (ValueError, TypeError):
            return jsonify({'success': False, 'error': 'Invalid quantity'}), 400
        if quantity < 0:
            return jsonify({'success': False, 'error': 'Quantity must be 0 or greater'}), 400
        
        conn = sqlite3.connect('bol.db', isolation_level='IMMEDIATE')
        cur = conn.cursor()
        target_upc = upc
        target_lot = lot_number

        target_id = None
        if item_id is not None:
            cur.execute('''
                SELECT id, upc, lot_number
                FROM bol_items
                WHERE id = ?
                LIMIT 1
            ''', (item_id,))
            item_row = cur.fetchone()
            if not item_row:
                return jsonify({'success': False, 'error': f'Item id {item_id} not found'}), 404
            target_id = int(item_row[0])
            row_upc = ss_normalization._normalize_upc(item_row[1])
            row_lot = ss_normalization._normalize_lot_number(item_row[2])
            if upc and row_upc and row_upc.lower() != upc.lower():
                return jsonify({'success': False, 'error': 'Provided upc does not match item id'}), 400
            if target_lot and row_lot and target_lot.lower() != row_lot.lower():
                return jsonify({'success': False, 'error': 'Provided lot_number does not match item id'}), 400
            target_upc = row_upc
            target_lot = row_lot

        if not target_upc:
            return jsonify({'success': False, 'error': 'Missing upc or id'}), 400

        is_suffixed = ('-' in str(target_upc) and str(target_upc).rsplit('-', 1)[-1].isdigit())

        if item_id is None:
            if target_lot:
                cur.execute('''
                    SELECT COUNT(*)
                    FROM bol_items
                    WHERE upc = ? COLLATE NOCASE
                      AND lot_number = ? COLLATE NOCASE
                ''', (target_upc, target_lot))
                same_lot_rows = int((cur.fetchone() or [0])[0] or 0)
                if same_lot_rows > 1:
                    return jsonify({
                        'success': False,
                        'error': 'Multiple rows found for this UPC+LOT. Please update quantity from the latest Items-to-List page so row id is included.'
                    }), 400
            cur.execute('''
                SELECT COUNT(*), COUNT(DISTINCT COALESCE(lot_number, ''))
                FROM bol_items
                WHERE upc = ? COLLATE NOCASE
            ''', (target_upc,))
            row_total, lot_variants = cur.fetchone() or (0, 0)
            row_total = int(row_total or 0)
            lot_variants = int(lot_variants or 0)

            if not target_lot and row_total > 1:
                if not is_suffixed and lot_variants > 1:
                    return jsonify({
                        'success': False,
                        'error': 'Multiple LOT rows found for this UPC. Please specify a lot_number for quantity edits.'
                    }), 400
                return jsonify({
                    'success': False,
                    'error': 'Multiple rows found for this UPC. Please update quantity from the latest Items-to-List page so row id is included.'
                }), 400

            if target_lot:
                target_row = ss_warehouse_allocations._select_canonical_bol_row(
                    cur,
                    target_upc,
                    target_lot,
                    columns='id, upc, lot_number',
                    strict_lot=True
                )
                if not target_row:
                    return jsonify({'success': False, 'error': f'Item {target_upc} not found in lot {target_lot}'}), 404
            else:
                target_row = ss_warehouse_allocations._select_canonical_bol_row(
                    cur,
                    target_upc,
                    '',
                    columns='id, upc, lot_number'
                )
                if not target_row:
                    return jsonify({'success': False, 'error': f'Item {target_upc} not found'}), 404

            target_id = int(target_row[0])
            target_upc = ss_normalization._normalize_upc(target_row[1]) or target_upc
            target_lot = ss_normalization._normalize_lot_number(target_row[2]) or target_lot

        if target_id is None:
            return jsonify({'success': False, 'error': 'Missing target row id'}), 400

        # Check if new quantity columns exist
        cur.execute('PRAGMA table_info(bol_items)')
        cols = [col[1].lower() for col in cur.fetchall()]
        has_new_cols = 'original_qty' in cols and 'unchecked_qty' in cols
        has_good_bad_cols = 'good_qty' in cols and 'bad_qty' in cols

        if has_new_cols and has_good_bad_cols:
            cur.execute('''
                SELECT
                    COALESCE(quantity, 0),
                    COALESCE(original_qty, 0),
                    COALESCE(good_qty, 0),
                    COALESCE(bad_qty, 0),
                    COALESCE(unchecked_qty, 0)
                FROM bol_items
                WHERE id = ?
                LIMIT 1
            ''', (target_id,))
        elif has_good_bad_cols:
            cur.execute('''
                SELECT
                    COALESCE(quantity, 0),
                    COALESCE(quantity, 0),
                    COALESCE(good_qty, 0),
                    COALESCE(bad_qty, 0),
                    0
                FROM bol_items
                WHERE id = ?
                LIMIT 1
            ''', (target_id,))
        else:
            cur.execute('''
                SELECT
                    COALESCE(quantity, 0),
                    COALESCE(quantity, 0),
                    0,
                    0,
                    0
                FROM bol_items
                WHERE id = ?
                LIMIT 1
            ''', (target_id,))
        qty_before_row = cur.fetchone() or (0, 0, 0, 0, 0)
        old_qty = int(qty_before_row[0] or 0)
        old_original_qty = int(qty_before_row[1] or 0)
        old_good_qty = int(qty_before_row[2] or 0)
        old_bad_qty = int(qty_before_row[3] or 0)
        old_unchecked_qty = int(qty_before_row[4] or 0)
        
        if has_new_cols:
            # Update both quantity and unchecked_qty (manual edits adjust unchecked bucket).
            cur.execute('''
                UPDATE bol_items
                SET quantity = ?,
                    original_qty = ?,
                    unchecked_qty = MAX(0, ? - COALESCE(good_qty, 0) - COALESCE(bad_qty, 0))
                WHERE id = ?
            ''', (quantity, quantity, quantity, target_id))
        else:
            # Old behavior
            cur.execute('UPDATE bol_items SET quantity = ? WHERE id = ?', (quantity, target_id))

        if cur.rowcount == 0:
            if target_lot:
                return jsonify({'success': False, 'error': f'Item {target_upc} not found in lot {target_lot}'}), 404
            return jsonify({'success': False, 'error': f'Item {target_upc} not found'}), 404
        
        # Also update items_prep_status.quantity if the item has been prepped (status exists)
        ss_prep_schema._ensure_items_prep_tables()
        prep_row = ss_warehouse_allocations._select_prep_status_row(cur, target_upc, target_lot, columns='status')
        if prep_row:
            status_update_lot = ss_warehouse_allocations._resolve_prep_status_lot(cur, target_upc, target_lot)
            # Item has prep status, update its prep quantity too
            cur.execute('''
                UPDATE items_prep_status
                SET quantity = ?
                WHERE upc = ? COLLATE NOCASE
                  AND COALESCE(lot_number, '') = ? COLLATE NOCASE
            ''', (quantity, target_upc, status_update_lot))

        new_original_qty = quantity if has_new_cols else old_original_qty
        new_unchecked_qty = max(0, quantity - old_good_qty - old_bad_qty) if has_new_cols else old_unchecked_qty
        quantity_changes = {}
        if old_qty != quantity:
            quantity_changes['quantity'] = {'from': old_qty, 'to': quantity}
        if has_new_cols and old_original_qty != new_original_qty:
            quantity_changes['original_qty'] = {'from': old_original_qty, 'to': new_original_qty}
        if has_new_cols and old_unchecked_qty != new_unchecked_qty:
            quantity_changes['unchecked_qty'] = {'from': old_unchecked_qty, 'to': new_unchecked_qty}
        
        conn.commit()
        try:
            ss_caching.update_data_version()
        except Exception:
            pass

        if quantity_changes:
            try:
                ss_listing_log._listinglog_add_entry(
                    upc=target_upc,
                    platform='item_manager',
                    action='quantity_update',
                    source=source,
                    quantity=quantity,
                    success=True,
                    meta={
                        'item_id': target_id,
                        'lot_number': target_lot,
                        'changes': quantity_changes,
                        'via': 'api_bol_items_update_quantity'
                    }
                )
            except Exception:
                pass
        return jsonify({'success': True, 'upc': target_upc, 'lot_number': target_lot, 'id': target_id})
    except Exception as e:
        return jsonify({'success': False, 'error': ss_errors._safe_error(e)}), 500
    finally:
        if conn is not None:
            conn.close()


def api_items_prep_status_delete(upc):
    """Delete preparation status for a UPC (for undo functionality).
    If UPC has a suffix (e.g., 110101-1), also delete the suffixed entry from bol_items.
    """
    conn = None
    try:
        upc_norm = ss_normalization._normalize_upc(upc)
        # Strip leading zeros to match item manager behavior (but preserve suffixes like -1)
        upc = ss_normalization._normalize_upc_preserve_suffix_for_match(upc_norm)
        if not upc:
            return jsonify({'success': False, 'error': 'Missing upc'}), 400
        lot_number = ss_warehouse_allocations._preferred_lot_from_request()
        
        ss_prep_schema._ensure_items_prep_tables()
        conn = sqlite3.connect('bol.db')
        cur = conn.cursor()
        
        # Delete from items_prep_status for the relevant lot
        if lot_number:
            cur.execute('''
                DELETE FROM items_prep_status
                WHERE upc = ? COLLATE NOCASE
                  AND COALESCE(lot_number, '') = ? COLLATE NOCASE
            ''', (upc, ss_normalization._normalize_lot_number(lot_number)))
            if cur.rowcount == 0:
                cur.execute('''
                    DELETE FROM items_prep_status
                    WHERE upc = ? COLLATE NOCASE
                      AND COALESCE(lot_number, '') = ''
                ''', (upc,))
        else:
            cur.execute('DELETE FROM items_prep_status WHERE upc = ? COLLATE NOCASE', (upc,))
        
        # If this is a suffixed UPC (e.g., 110101-1), delete it from bol_items too
        is_unique_suffix = ('-' in upc and upc.rsplit('-', 1)[-1].isdigit()) or upc.startswith('777')
        if is_unique_suffix:
            print(f'Deleting suffixed entry {upc} from bol_items')
            if lot_number:
                cur.execute('DELETE FROM bol_items WHERE upc = ? COLLATE NOCASE AND lot_number = ? COLLATE NOCASE', (upc, lot_number))
                if cur.rowcount == 0:
                    cur.execute('DELETE FROM bol_items WHERE upc = ? COLLATE NOCASE', (upc,))
            else:
                cur.execute('DELETE FROM bol_items WHERE upc = ? COLLATE NOCASE', (upc,))
            ss_prep_media._items_prep_delete_diagnostic_assets(cur, upc)
        
        conn.commit()
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': ss_errors._safe_error(e)}), 500
    finally:
        if conn is not None:
            conn.close()
