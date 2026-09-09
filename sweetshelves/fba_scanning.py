"""Fba scanning for Sweet Shelves."""

import json
import re
import sqlite3
from fba_inbound import FbaInboundValidationError, build_item_label_job
from flask import jsonify, request
from printer_manager import printer_manager
from . import (
    amazon_catalog as ss_amazon_catalog, caching as ss_caching, config as ss_config, database as
    ss_database, errors as ss_errors, fba_inventory as ss_fba_inventory, fba_readiness as
    ss_fba_readiness, fba_schema as ss_fba_schema, fba_shipments as ss_fba_shipments, inventory_age as
    ss_inventory_age, inventory_history as ss_inventory_history, listing_checks as ss_listing_checks,
    listing_settings as ss_listing_settings, normalization as ss_normalization, warehouse_locations as
    ss_warehouse_locations, warehouse_matching as ss_warehouse_matching,
)


def _fba_scan_rejection(session, *, barcode='', msku=''):
    """Return a packing stop for a current or removed setup rejection."""
    key = lambda value: ss_warehouse_locations._movelocation_barcode_key(value) or str(value or '').strip().casefold()
    current = session.get('items') or []
    current_keys = {key(item.get('barcode') or item.get('seller_sku')) for item in current if isinstance(item, dict)}
    archived = [item for item in session.get('rejected_items') or [] if isinstance(item, dict)
                and key(item.get('barcode') or item.get('seller_sku')) not in current_keys]
    for item in current + archived:
        if not isinstance(item, dict) or (item.get('fba_enablement_status') != 'failed' and not item.get('fba_set_aside')):
            continue
        error = ss_fba_schema._fba_trim(item.get('fba_enablement_error'), 700)
        safety_pending = ss_fba_readiness._fba_error_is_missing_safety_info(error)
        if safety_pending and not item.get('fba_set_aside'):
            continue
        if not error and item.get('fba_set_aside'):
            error = 'This item was set aside from this shipment and is not included in the Amazon plan.'
        listing = (item.get('fba') or {}).get('amazon_listing') or {}
        values = [item.get(field) for field in ('barcode', 'barcode_key', 'barcode_display', 'asin', 'seller_sku', 'fnsku', 'amazon_fnsku')]
        values += [listing.get('seller_sku'), listing.get('fnsku')]
        if not any(value and ((barcode and key(value) == key(barcode)) or (msku and key(value) == key(msku))) for value in values):
            continue
        retryable = item.get('fba_enablement_status') != 'failed' or safety_pending or any(token in error.lower() for token in (
            'internal error', 'has no attribute', 'traceback', 'database is locked',
            'timed out', 'timeout', 'quotaexceeded', 'throttl', 'connection error',
        ))
        headline = 'CHECK NEEDED — DO NOT PACK' if retryable else 'REJECTED — DO NOT PACK'
        voice = 'Check needed. Set this item aside.' if retryable else 'Rejected. Do not pack. Set this item aside.'
        return {
            'success': False, 'rejected': not retryable,
            'error': error or 'This item failed Amazon FBA setup.',
            'voice_message': voice,
            'sort_result': {'kind': 'error' if retryable else 'rejected', 'headline': headline,
                            'instruction': (error + ' ' if error else '') + 'Set this item aside. Do not label or pack it.'},
        }
    return None


def _fba_planned_label_match(barcode, session_rows):
    """Resolve a warehouse barcode to the newest printable item in an open FBA plan."""
    target_variants = {
        str(value or '').strip().casefold()
        for value in ss_warehouse_locations._movelocation_barcode_variants(barcode)
        if str(value or '').strip()
    }
    if not target_variants:
        return None

    waiting_match = None
    for raw_row in session_rows or []:
        session = ss_fba_inventory._fba_session_row_payload(raw_row, include_items=True)
        rejection = _fba_scan_rejection(session, barcode=barcode)
        if rejection:
            return {**rejection, 'printable': False, 'session_id': session.get('id'), 'barcode': barcode}
        state = ss_fba_shipments._fba_amazon_state(raw_row)
        plan_items = state.get('plan_items') if isinstance(state.get('plan_items'), list) else []
        if not state.get('inbound_plan_id') or not plan_items:
            continue

        plan_by_msku = {}
        for plan_item in plan_items:
            if not isinstance(plan_item, dict):
                continue
            msku = ss_fba_schema._fba_trim(plan_item.get('msku') or plan_item.get('seller_sku'), 255)
            if msku:
                plan_by_msku[msku.casefold()] = plan_item

        for item in session.get('items') or []:
            if not isinstance(item, dict):
                continue
            item_variants = {
                str(value or '').strip().casefold()
                for value in ss_warehouse_locations._movelocation_barcode_variants(item.get('barcode'))
                if str(value or '').strip()
            }
            if not target_variants.intersection(item_variants):
                continue

            msku = ss_fba_schema._fba_trim(
                item.get('seller_sku')
                or ((item.get('fba') or {}).get('amazon_listing') or {}).get('seller_sku'),
                255,
            )
            plan_item = plan_by_msku.get(msku.casefold()) if msku else None
            fnsku = ss_fba_schema._fba_trim((plan_item or {}).get('fnsku'), 30).upper()
            box_assignments = []
            for raw_box in state.get('boxes') or []:
                if not isinstance(raw_box, dict):
                    continue
                box_id = ss_fba_schema._fba_trim(
                    raw_box.get('local_id') or raw_box.get('box_id') or raw_box.get('box_number'),
                    40,
                ).upper()
                if not box_id:
                    continue
                packed_quantity = sum(
                    max(0, ss_listing_settings._listingagent_parse_int(content.get('quantity'), 0) or 0)
                    for content in (raw_box.get('contents') or [])
                    if isinstance(content, dict)
                    and ss_fba_schema._fba_trim(
                        content.get('msku') or content.get('seller_sku'), 255
                    ).casefold() == msku.casefold()
                )
                if packed_quantity > 0:
                    box_assignments.append({
                        'box_id': box_id,
                        'quantity': packed_quantity,
                    })
            box_assignments.sort(key=lambda assignment: assignment['box_id'])
            match = {
                'session_id': int(session.get('id') or 0),
                'session_name': ss_fba_schema._fba_trim(session.get('session_name') or session.get('batch_name'), 120),
                # The operator-facing shipment is the FBA prep session. Keep a
                # separate named field so the scanner does not confuse it with
                # Amazon's shipment IDs after placement splits are selected.
                'shipment_name': ss_fba_schema._fba_trim(session.get('session_name') or session.get('batch_name'), 120),
                'inbound_plan_id': ss_fba_schema._fba_trim(state.get('inbound_plan_id'), 38),
                'barcode': ss_fba_schema._fba_trim(item.get('barcode'), 160),
                'title': ss_fba_schema._fba_trim(item.get('title') or item.get('barcode'), 500),
                'image': ss_fba_schema._fba_trim(item.get('image'), 1500),
                'msku': msku,
                'fnsku': fnsku,
                'box_assignments': box_assignments,
                'box_ids': [assignment['box_id'] for assignment in box_assignments],
                'packed_quantity': sum(assignment['quantity'] for assignment in box_assignments),
                'packing_status': 'packed' if box_assignments else 'not_packed',
                'printable': bool(msku and len(fnsku) == 10 and fnsku.isalnum()),
            }
            if match['printable']:
                return match
            if waiting_match is None:
                waiting_match = match
    return waiting_match


def api_fba_labels_resolve():
    """Find the newest open FBA plan item corresponding to a scanned barcode."""
    barcode = ss_fba_schema._fba_trim(request.args.get('barcode'), 160)
    if not barcode:
        return jsonify({'success': False, 'error': 'Scan a barcode first'}), 400

    conn = None
    try:
        conn = sqlite3.connect(str(ss_config.BASE_DIR / 'searchRack.db'), timeout=30.0)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        ss_fba_schema._ensure_fba_prep_tables(cur)
        rows = cur.execute('''
            SELECT *
            FROM fba_prep_sessions
            WHERE status = 'open'
              AND deleted_at IS NULL
            ORDER BY updated_at DESC, id DESC
            LIMIT 250
        ''').fetchall()
        match = _fba_planned_label_match(barcode, rows)
        conn.commit()
        if not match:
            return jsonify({
                'success': False,
                'error': 'This barcode is not in an open Amazon FBA plan. Create or open its plan first.',
            }), 404
        if match.get('sort_result'):
            return jsonify({**match, 'match': match}), 422
        if not match.get('printable'):
            return jsonify({
                'success': False,
                'error': 'Amazon found the planned item but has not assigned its printable FNSKU yet. Refresh the FBA plan and scan again.',
                'match': match,
            }), 409
        return jsonify({'success': True, 'match': match})
    except Exception as exc:
        if conn is not None:
            conn.rollback()
        return jsonify({'success': False, 'error': ss_errors._safe_error(exc, 'fba label scanner')}), 500
    finally:
        if conn is not None:
            conn.close()


def api_fba_prep_amazon_print_item_label(session_id):
    """Print one Amazon-required FNSKU on the Item Prep printer."""
    conn = None
    live_guidance = None
    try:
        data = request.get_json() or {}
        msku = ss_fba_schema._fba_trim(data.get('msku') or data.get('seller_sku'), 255)
        scan_token = ss_fba_schema._fba_trim(data.get('scan_token'), 100)
        if scan_token and not re.fullmatch(r'[A-Za-z0-9._:-]{8,100}', scan_token):
            raise FbaInboundValidationError('Invalid packing scan token for the Amazon label')
        if not msku:
            raise FbaInboundValidationError('Seller SKU is required for the Amazon label')
        conn = sqlite3.connect(str(ss_config.BASE_DIR / 'searchRack.db'), timeout=30.0)
        conn.row_factory = sqlite3.Row
        _row, session_data, state = ss_fba_shipments._fba_amazon_session(conn, session_id)
        rejection = _fba_scan_rejection(session_data, msku=msku)
        if rejection:
            return jsonify(rejection), 422
        if not state.get('inbound_plan_id'):
            raise FbaInboundValidationError('Create and sync the Amazon inbound plan first')
        if scan_token:
            scan_row = conn.execute(
                'SELECT session_id, msku FROM fba_pack_scans WHERE scan_token = ? LIMIT 1',
                (scan_token,),
            ).fetchone()
            if not scan_row or int(scan_row['session_id']) != int(session_id):
                raise FbaInboundValidationError('The physical packing scan was not saved for this label')
            if ss_fba_schema._fba_trim(scan_row['msku'], 255).casefold() != msku.casefold():
                raise FbaInboundValidationError('The saved packing scan belongs to a different Seller SKU')

        plan_items = state.get('plan_items') or []
        plan_item = next((
            row for row in plan_items if isinstance(row, dict)
            and ss_fba_schema._fba_trim(row.get('msku') or row.get('seller_sku'), 255).casefold() == msku.casefold()
        ), {})
        plan_requires_label = any(
            isinstance(instruction, dict)
            and ss_fba_schema._fba_trim(instruction.get('prepType') or instruction.get('prep_type'), 80).upper() == 'ITEM_LABELING'
            and ss_fba_schema._fba_trim(instruction.get('prepOwner') or instruction.get('prep_owner'), 20).upper() in ('', 'SELLER')
            for instruction in plan_item.get('prepInstructions') or plan_item.get('prep_instructions') or []
        )
        if plan_requires_label:
            live_guidance = {
                'status': 'amazon_barcode',
                'label': 'Amazon label required',
                'detail': 'The accepted inbound plan requires seller item labeling.',
                'instruction': 'RequiresFNSKULabel',
                'checked': True,
                'source': 'amazon_inbound_plan',
                'identifier_type': 'seller_sku',
                'identifier': msku,
            }
        else:
            live_guidance = ss_fba_readiness._fba_amazon_barcode_guidance({
                'seller_sku': msku,
                'asin': ss_fba_schema._fba_trim(plan_item.get('asin'), 30).upper(),
            }, force_refresh=True)
        label_job = build_item_label_job(
            plan_items,
            session_data.get('items') or [],
            msku,
            authoritative_guidance=live_guidance,
        )
        config = printer_manager.get_config_snapshot()
        # Amazon requires the product name and condition on an FNSKU label, but
        # they are supporting text.  Put the condition first so it cannot be
        # lost when a long product name is shortened to fit.
        description = ' | '.join(filter(None, [
            label_job.get('condition', 'New'),
            label_job.get('title'),
        ]))
        layout_override = {
            # Amazon's common item-label format is at least 1 inch tall.  The
            # live Brother uses a 62 mm continuous roll, so 26 mm gives a true
            # 1-inch label instead of inheriting its short warehouse-label cut.
            'label_height': max(26, int(float(config.get('label_height') or 30))),
            'label_show_name': True,
            'label_show_barcode': True,
            'label_show_barcode_text': True,
            'label_title_font_size_pt': 6,
            'label_barcode_font_size_pt': 8,
            'label_barcode_height_mm': 10,
            'label_title_lines': 2,
            # Amazon's published minimum whitespace is 0.25 inch at the sides
            # and 0.125 inch at the top and bottom.
            'label_padding_x_mm': 6.35,
            'label_padding_y_mm': 3.175,
            'label_block_gap_mm': 0.55,
            'label_text_gap_mm': 0.3,
            'label_barcode_first': True,
            'label_barcode_protected': True,
        }
        with ss_fba_readiness._FBA_ITEM_LABEL_PRINT_LOCK:
            # Recheck after acquiring the physical-printer lock so an HTTP retry
            # for the same packing scan cannot print a second label.
            conn.rollback()
            _latest_row, _latest_session, latest_state = ss_fba_shipments._fba_amazon_session(conn, session_id)
            if scan_token:
                latest_scan = conn.execute(
                    'SELECT label_printed FROM fba_pack_scans WHERE scan_token = ? AND session_id = ?',
                    (scan_token, session_id),
                ).fetchone()
                if latest_scan and int(latest_scan['label_printed'] or 0) == 1:
                    counts = latest_state.get('printed_item_label_counts')
                    counts = counts if isinstance(counts, dict) else {}
                    return jsonify({
                        'success': True, 'printed': True, 'idempotent': True,
                        'msku': label_job['msku'], 'fnsku': label_job['fnsku'],
                        'printed_count': max(0, int(counts.get(label_job['msku']) or 0)),
                        'printer': 'Item Prep printer',
                        'barcode_guidance': live_guidance,
                        'amazon_workflow': latest_state,
                    })

            if str(config.get('print_method') or '').strip().lower() == 'browser':
                raise FbaInboundValidationError(
                    'Amazon FNSKU printing requires the direct Item Prep printer mode'
                )
            printer_status = printer_manager.get_connection_status()
            if not printer_status.get('can_print'):
                raise RuntimeError(
                    printer_status.get('detail') or 'The Item Prep printer is not ready'
                )
            printer_manager.print_barcode(
                label_job['fnsku'],
                description,
                quantity=1,
                layout_override=layout_override,
            )
            # Printing can take several seconds. Reload and update the newest
            # packing state rather than saving the snapshot from before printing.
            conn.rollback()
            conn.execute('BEGIN IMMEDIATE')
            _latest_row, _latest_session, state = ss_fba_shipments._fba_amazon_session(conn, session_id)
            printed_counts = state.get('printed_item_label_counts')
            printed_counts = printed_counts if isinstance(printed_counts, dict) else {}
            count_key = label_job['msku']
            printed_counts[count_key] = max(0, int(printed_counts.get(count_key) or 0)) + 1
            state['printed_item_label_counts'] = printed_counts
            state['last_item_label_print'] = {
                'msku': label_job['msku'],
                'fnsku': label_job['fnsku'],
                'scan_token': scan_token,
                'printed_at': ss_listing_checks._listagent_now_iso(),
            }
            if scan_token:
                conn.execute(
                    'UPDATE fba_pack_scans SET label_printed = 1 WHERE session_id = ? AND scan_token = ?',
                    (session_id, scan_token),
                )
                if isinstance(state.get('last_pack_scan'), dict) and state['last_pack_scan'].get('scan_token') == scan_token:
                    state['last_pack_scan']['label_printed'] = True
            ss_fba_shipments._fba_save_amazon_state(conn, session_id, state)
            conn.commit()
        return jsonify({
            'success': True,
            'printed': True,
            'msku': label_job['msku'],
            'fnsku': label_job['fnsku'],
            'printed_count': printed_counts[count_key],
            'printer': printer_status.get('display_name') or config.get('printer_name') or 'Item Prep printer',
            'barcode_guidance': live_guidance,
            'amazon_workflow': state,
        })
    except FbaInboundValidationError as exc:
        if conn is not None:
            conn.rollback()
        payload = {'success': False, 'error': str(exc)}
        if isinstance(live_guidance, dict):
            payload['barcode_guidance'] = live_guidance
        return jsonify(payload), 409
    except Exception as exc:
        if conn is not None:
            conn.rollback()
        return jsonify({'success': False, 'error': ss_errors._safe_error(exc, 'fba amazon item label print')}), 502
    finally:
        if conn is not None:
            conn.close()


def api_fba_prep_amazon_pack_scan(session_id):
    """Persist one authoritative physical scan: carton route plus optional stock removal."""
    conn = None
    history_conn = None
    event_id = ''
    inventory_committed = False
    try:
        data = request.get_json() or {}
        scan_token = ss_fba_schema._fba_trim(data.get('scan_token'), 100)
        barcode = ss_fba_schema._fba_trim(data.get('barcode'), 160)
        msku = ss_fba_schema._fba_trim(data.get('msku') or data.get('seller_sku'), 255)
        active_box_id = ss_fba_schema._fba_trim(data.get('active_box_id'), 40).upper()
        label_printed = bool(data.get('label_printed'))
        client_id, operator_name = ss_fba_schema._fba_client_identity(data)
        source_locations = []
        source_seen = set()
        for raw_location in data.get('source_locations') if isinstance(data.get('source_locations'), list) else []:
            location = ss_fba_schema._fba_trim(raw_location, 120)
            key = ss_warehouse_matching._sold_location_key(location)
            if location and key not in source_seen:
                source_seen.add(key)
                source_locations.append(location)
        if not scan_token or not re.fullmatch(r'[A-Za-z0-9._:-]{8,100}', scan_token):
            raise FbaInboundValidationError('A valid packing scan token is required')
        if not barcode:
            raise FbaInboundValidationError('Scan an item barcode')
        if not msku:
            raise FbaInboundValidationError('The scanned item is not matched to an Amazon Seller SKU')

        conn = sqlite3.connect(str(ss_config.BASE_DIR / 'searchRack.db'), timeout=30.0)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        ss_fba_schema._ensure_fba_prep_tables(cur)
        conn.commit()
        # Serialize the full read/check/remove/pack transaction. Every scanner
        # therefore sees the units committed by the scanner immediately before it.
        conn.execute('BEGIN IMMEDIATE')
        cur = conn.cursor()
        existing = cur.execute(
            'SELECT * FROM fba_pack_scans WHERE scan_token = ? LIMIT 1', (scan_token,)
        ).fetchone()
        if existing:
            if int(existing['session_id']) != int(session_id):
                raise FbaInboundValidationError('That packing scan token belongs to another session')
            session_row = cur.execute('SELECT * FROM fba_prep_sessions WHERE id = ?', (session_id,)).fetchone()
            return jsonify({
                'success': True, 'idempotent': True, 'scan': dict(existing),
                'amazon_workflow': ss_fba_shipments._fba_amazon_state(session_row),
            })

        _row, session_data, state = ss_fba_shipments._fba_amazon_session(conn, session_id)
        rejection = _fba_scan_rejection(session_data, msku=msku, barcode=barcode)
        if rejection:
            return jsonify(rejection), 422
        ss_fba_schema._fba_touch_session_worker(cur, session_id, client_id, operator_name, active_box_id)
        if not state.get('packing_confirmed'):
            raise FbaInboundValidationError('Confirm an Amazon packing option before scanning physical units')
        if state.get('boxes_submitted'):
            raise FbaInboundValidationError('Box contents have already been submitted to Amazon')

        plan_items = state.get('plan_items') if isinstance(state.get('plan_items'), list) else []
        plan_item = next((
            row for row in plan_items if isinstance(row, dict)
            and ss_fba_schema._fba_trim(row.get('msku') or row.get('seller_sku'), 255).casefold() == msku.casefold()
        ), None)
        if not plan_item:
            raise FbaInboundValidationError(f'{msku} is not part of this Amazon plan')
        expected = max(0, ss_listing_settings._listingagent_parse_int(plan_item.get('quantity'), 0) or 0)
        packed = sum(
            max(0, ss_listing_settings._listingagent_parse_int(item.get('quantity'), 0) or 0)
            for box in state.get('boxes') or [] if isinstance(box, dict)
            for item in box.get('contents') or [] if isinstance(item, dict)
            and ss_fba_schema._fba_trim(item.get('msku'), 255).casefold() == msku.casefold()
        )
        if packed >= expected:
            raise FbaInboundValidationError(f'{msku} is already fully packed ({packed}/{expected})')

        group = next((
            row for row in state.get('packing_groups') or [] if isinstance(row, dict)
            and any(
                isinstance(item, dict)
                and ss_fba_schema._fba_trim(item.get('msku') or item.get('seller_sku'), 255).casefold() == msku.casefold()
                for item in row.get('items') or []
            )
        ), None)
        if not group:
            raise FbaInboundValidationError(f'Amazon did not assign {msku} to a packing group')
        group_id = ss_fba_schema._fba_trim(group.get('packing_group_id'), 38)

        boxes = state.get('boxes') if isinstance(state.get('boxes'), list) else []
        box = next((
            row for row in boxes if isinstance(row, dict)
            and ss_fba_schema._fba_trim(row.get('local_id'), 40).upper() == active_box_id
            and ss_fba_schema._fba_trim(row.get('packing_group_id'), 38) == group_id
        ), None)
        if box is None:
            candidates = [
                row for row in boxes if isinstance(row, dict)
                and ss_fba_schema._fba_trim(row.get('packing_group_id'), 38) == group_id
            ]
            box = candidates[-1] if candidates else None
        if box is None:
            used_box_ids = {ss_fba_schema._fba_trim(row.get('local_id'), 40).upper() for row in boxes if isinstance(row, dict)}
            next_number = 1
            while f'BOX-{next_number:02d}' in used_box_ids:
                next_number += 1
            box = {
                'local_id': f'BOX-{next_number:02d}', 'packing_group_id': group_id,
                'length_in': '', 'width_in': '', 'height_in': '', 'weight_lb': '',
                'single_oversize_exception': False, 'contains_jewelry_or_watches': False,
                'contents': [],
            }
            boxes.append(box)
        box_id = ss_fba_schema._fba_trim(box.get('local_id'), 40).upper()

        inventory_removed = 0
        removed_location = ''
        removed_row_id = None
        warning = ''
        available_elsewhere = []
        schema = ss_warehouse_matching._searchrack_removal_schema(cur)
        qty_col = schema.get('qty_col')
        id_lookup_col = schema.get('id_col') or 'rowid'
        matches = ss_warehouse_matching._searchrack_matches_for_barcode(cur, barcode, schema=schema, include_zero=False)
        source_priority = {ss_warehouse_matching._sold_location_key(code): index for index, code in enumerate(source_locations)}
        eligible = [row for row in matches if row.get('location_key') in source_priority]
        eligible.sort(key=lambda row: (source_priority.get(row.get('location_key'), 9999), int(row.get('id') or 0)))
        if eligible and qty_col:
            chosen = eligible[0]
            removed_row_id = int(chosen.get('id') or 0)
            snapshot_row = cur.execute(
                f'SELECT rowid AS _rowid_, * FROM SEARCHRACK WHERE {ss_database._sqlite_ident(id_lookup_col)} = ?',
                (removed_row_id,),
            ).fetchone()
            if not snapshot_row:
                raise FbaInboundValidationError('Warehouse inventory changed before this scan could be saved')
            snapshot = dict(snapshot_row)
            old_quantity = max(0, ss_normalization._coerce_int(snapshot.get(qty_col), 0))
            if old_quantity <= 0:
                raise FbaInboundValidationError('The selected warehouse stock is no longer available')
            new_quantity = old_quantity - 1
            cur.execute(
                f'UPDATE SEARCHRACK SET {ss_database._sqlite_ident(qty_col)} = ? WHERE {ss_database._sqlite_ident(id_lookup_col)} = ? AND CAST({ss_database._sqlite_ident(qty_col)} AS INTEGER) = ?',
                (new_quantity, removed_row_id, old_quantity),
            )
            if cur.rowcount != 1:
                raise FbaInboundValidationError('Warehouse inventory changed; scan the unit again')
            ss_inventory_age._inventory_age_consume_fifo(cur, removed_row_id, 1)
            inventory_removed = 1
            removed_location = ss_fba_schema._fba_trim(chosen.get('location_code'), 120)
            event_id = 'fba-pack-' + scan_token
            history_conn = sqlite3.connect(str(ss_config.BASE_DIR / 'rackhistory.db'), timeout=30.0)
            history_cur = history_conn.cursor()
            ss_inventory_history._ensure_removed_items_table(history_cur)
            history_cur.execute('''
                INSERT OR IGNORE INTO removed_items (
                    order_id, barcode, title, quantity_removed, removed_at,
                    searchrack_id, old_quantity, new_quantity, removal_type,
                    item_position, event_id, source_row_json, from_position,
                    to_position, inventory_row_deleted, event_status
                ) VALUES (?, ?, ?, 1, ?, ?, ?, ?, 'fba_removed', ?, ?, ?, ?, '', ?, 'pending')
            ''', (
                f'FBA-SESSION-{session_id}',
                ss_fba_schema._fba_trim(snapshot.get(schema.get('barcode_col')) or barcode, 160),
                ss_fba_schema._fba_trim(snapshot.get(schema.get('title_col')) or barcode, 500),
                ss_listing_checks._listagent_now_iso(), removed_row_id, old_quantity, new_quantity,
                removed_location, event_id,
                json.dumps(snapshot, ensure_ascii=False, default=str), removed_location,
                1 if new_quantity <= 0 else 0,
            ))
            history_conn.commit()
        else:
            available_elsewhere = [
                {'code': row.get('location_code'), 'quantity': row.get('quantity')}
                for row in matches[:20]
            ]
            if matches and source_locations:
                warning = 'No unit was removed because this item was not found in the active working locations.'
            elif matches:
                warning = 'No unit was removed because no working shelf/bin is active.'
            else:
                warning = 'No tracked warehouse inventory was found; the unit was still packed.'

        contents = box.get('contents') if isinstance(box.get('contents'), list) else []
        content = next((
            item for item in contents if isinstance(item, dict)
            and ss_fba_schema._fba_trim(item.get('msku'), 255).casefold() == msku.casefold()
        ), None)
        if content is None:
            content = {'msku': msku, 'quantity': 0}
            contents.append(content)
        content['quantity'] = max(0, ss_listing_settings._listingagent_parse_int(content.get('quantity'), 0) or 0) + 1
        box['contents'] = contents
        state['boxes'] = boxes
        removed_by_msku = state.get('inventory_removed_by_msku') if isinstance(state.get('inventory_removed_by_msku'), dict) else {}
        removed_by_msku[msku] = max(0, ss_listing_settings._listingagent_parse_int(removed_by_msku.get(msku), 0) or 0) + inventory_removed
        state['inventory_removed_by_msku'] = removed_by_msku
        removed_locations = state.get('removed_locations_by_msku') if isinstance(state.get('removed_locations_by_msku'), dict) else {}
        msku_locations = removed_locations.get(msku) if isinstance(removed_locations.get(msku), list) else []
        if inventory_removed:
            location_row = next((row for row in msku_locations if row.get('code') == removed_location), None)
            if location_row is None:
                location_row = {'code': removed_location, 'quantity': 0}
                msku_locations.append(location_row)
            location_row['quantity'] = max(0, ss_listing_settings._listingagent_parse_int(location_row.get('quantity'), 0) or 0) + 1
        removed_locations[msku] = msku_locations
        state['removed_locations_by_msku'] = removed_locations
        state['last_pack_scan'] = {
            'scan_token': scan_token, 'barcode': barcode, 'msku': msku, 'box_id': box_id,
            'packing_group_id': group_id, 'source_location': removed_location,
            'inventory_removed': inventory_removed, 'label_printed': label_printed,
            'client_id': client_id, 'operator_name': operator_name,
            'scanned_at': ss_listing_checks._listagent_now_iso(),
        }
        cur.execute('''
            INSERT INTO fba_pack_scans (
                session_id, scan_token, barcode, msku, title, packing_group_id,
                box_id, source_location, source_searchrack_id, inventory_removed,
                label_printed, scanned_at, client_id, operator_name
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            session_id, scan_token, barcode, msku,
            ss_fba_schema._fba_trim(data.get('title'), 500) or None, group_id, box_id,
            removed_location or None, removed_row_id, inventory_removed,
            1 if label_printed else 0, state['last_pack_scan']['scanned_at'],
            client_id or None, operator_name or None,
        ))
        state['last_pack_scan']['id'] = int(cur.lastrowid or 0)
        ss_fba_shipments._fba_save_amazon_state(conn, session_id, state)
        conn.commit()
        inventory_committed = True
        if history_conn is not None and event_id:
            try:
                history_conn.execute(
                    "UPDATE removed_items SET event_status = 'applied', applied_at = ? WHERE event_id = ?",
                    (ss_listing_checks._listagent_now_iso(), event_id),
                )
                history_conn.commit()
            except Exception as history_error:
                ss_config.logger.warning('FBA packing-scan Rack History finalization deferred: %s', history_error)
                warning = (warning + ' ' if warning else '') + 'Rack History finalization will retry automatically.'
        if inventory_removed:
            try:
                ss_caching.update_data_version()
                ss_caching._invalidate_searchrack_cache()
            except Exception as cache_error:
                ss_config.logger.warning('FBA packing-scan cache refresh deferred: %s', cache_error)
        return jsonify({
            'success': True, 'amazon_workflow': state,
            'scan': state['last_pack_scan'], 'warning': warning,
            'available_elsewhere': available_elsewhere,
        })
    except FbaInboundValidationError as exc:
        if history_conn is not None and event_id and not inventory_committed:
            try:
                history_conn.execute(
                    "DELETE FROM removed_items WHERE event_id = ? AND event_status = 'pending'", (event_id,)
                )
                history_conn.commit()
            except Exception:
                pass
        for connection in (conn, history_conn):
            try:
                if connection is not None:
                    connection.rollback()
            except Exception:
                pass
        return jsonify({'success': False, 'error': str(exc)}), 409
    except Exception as exc:
        if history_conn is not None and event_id and not inventory_committed:
            try:
                history_conn.execute(
                    "DELETE FROM removed_items WHERE event_id = ? AND event_status = 'pending'", (event_id,)
                )
                history_conn.commit()
            except Exception:
                pass
        for connection in (conn, history_conn):
            try:
                if connection is not None:
                    connection.rollback()
            except Exception:
                pass
        return jsonify({'success': False, 'error': ss_errors._safe_error(exc, 'fba amazon pack scan')}), 500
    finally:
        for connection in (conn, history_conn):
            try:
                if connection is not None:
                    connection.close()
            except Exception:
                pass


def api_fba_prep_amazon_reset_pack_scan(session_id):
    """Undo the newest packed unit for one MSKU so the physical unit can be rescanned."""
    conn = None
    inventory_restored = False
    try:
        data = request.get_json() or {}
        msku = ss_fba_schema._fba_trim(data.get('msku') or data.get('seller_sku'), 255)
        client_id, operator_name = ss_fba_schema._fba_client_identity(data)
        if not msku:
            raise FbaInboundValidationError('Choose an item with a saved packing scan to reset')

        conn = sqlite3.connect(str(ss_config.BASE_DIR / 'searchRack.db'), timeout=30.0)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        ss_fba_schema._ensure_fba_prep_tables(cur)
        conn.commit()
        conn.execute('ATTACH DATABASE ? AS rackhist', (str(ss_config.BASE_DIR / 'rackhistory.db'),))
        conn.execute('BEGIN IMMEDIATE')
        cur = conn.cursor()
        _row, _session_data, state = ss_fba_shipments._fba_amazon_session(conn, session_id)
        ss_fba_schema._fba_touch_session_worker(cur, session_id, client_id, operator_name)
        if not state.get('packing_confirmed'):
            raise FbaInboundValidationError('Packing has not started for this Amazon plan')
        if state.get('boxes_submitted'):
            raise FbaInboundValidationError('Box contents have already been submitted to Amazon and cannot be reset')

        scan = cur.execute('''
            SELECT * FROM fba_pack_scans
            WHERE session_id = ? AND msku = ? COLLATE NOCASE
            ORDER BY id DESC LIMIT 1
        ''', (session_id, msku)).fetchone()
        if not scan:
            raise FbaInboundValidationError(f'{msku} has no saved packing scan to reset')
        scan = dict(scan)
        scan_msku = ss_fba_schema._fba_trim(scan.get('msku'), 255) or msku
        scan_token = ss_fba_schema._fba_trim(scan.get('scan_token'), 100)
        box_id = ss_fba_schema._fba_trim(scan.get('box_id'), 40).upper()

        boxes = state.get('boxes') if isinstance(state.get('boxes'), list) else []
        box = next((
            row for row in boxes if isinstance(row, dict)
            and ss_fba_schema._fba_trim(row.get('local_id'), 40).upper() == box_id
        ), None)
        if box is None:
            raise FbaInboundValidationError('The saved packing carton could not be found; refresh before resetting')
        contents = box.get('contents') if isinstance(box.get('contents'), list) else []
        content = next((
            row for row in contents if isinstance(row, dict)
            and ss_fba_schema._fba_trim(row.get('msku'), 255).casefold() == scan_msku.casefold()
        ), None)
        packed_quantity = max(0, ss_listing_settings._listingagent_parse_int((content or {}).get('quantity'), 0) or 0)
        if content is None or packed_quantity <= 0:
            raise FbaInboundValidationError('The saved packing count changed; refresh before resetting')
        if packed_quantity == 1:
            box['contents'] = [row for row in contents if row is not content]
        else:
            content['quantity'] = packed_quantity - 1
            box['contents'] = contents
        state['boxes'] = boxes

        restored = None
        source_location = ss_fba_schema._fba_trim(scan.get('source_location'), 120)
        if max(0, ss_normalization._coerce_int(scan.get('inventory_removed'), 0)):
            event_id = 'fba-pack-' + scan_token
            history = cur.execute(
                'SELECT * FROM rackhist.removed_items WHERE event_id = ? LIMIT 1', (event_id,)
            ).fetchone()
            if not history:
                raise FbaInboundValidationError(
                    'The warehouse-removal history for this scan is missing; inventory was not changed'
                )
            restored = ss_inventory_history._restore_searchrack_with_undo_claim(cur, history, 1)
            if restored.get('already_claimed'):
                raise FbaInboundValidationError('This packing scan was already restored by another worker')
            cur.execute('''
                UPDATE rackhist.removed_items
                SET event_status = 'reset', undone_at = ?
                WHERE id = ?
            ''', (ss_listing_checks._listagent_now_iso(), int(history['id'])))
            inventory_restored = True

        def state_key(mapping):
            return next((key for key in mapping if str(key).casefold() == scan_msku.casefold()), scan_msku)

        removed_by_msku = state.get('inventory_removed_by_msku')
        removed_by_msku = removed_by_msku if isinstance(removed_by_msku, dict) else {}
        removed_key = state_key(removed_by_msku)
        removed_by_msku[removed_key] = max(
            0, (ss_listing_settings._listingagent_parse_int(removed_by_msku.get(removed_key), 0) or 0)
            - (1 if inventory_restored else 0)
        )
        state['inventory_removed_by_msku'] = removed_by_msku

        removed_locations = state.get('removed_locations_by_msku')
        removed_locations = removed_locations if isinstance(removed_locations, dict) else {}
        location_key = state_key(removed_locations)
        location_rows = removed_locations.get(location_key)
        location_rows = location_rows if isinstance(location_rows, list) else []
        if inventory_restored and source_location:
            location_row = next((
                row for row in location_rows if isinstance(row, dict)
                and ss_warehouse_matching._sold_location_key(row.get('code')) == ss_warehouse_matching._sold_location_key(source_location)
            ), None)
            if location_row:
                location_row['quantity'] = max(
                    0, (ss_listing_settings._listingagent_parse_int(location_row.get('quantity'), 0) or 0) - 1
                )
                location_rows = [
                    row for row in location_rows
                    if max(0, ss_listing_settings._listingagent_parse_int((row or {}).get('quantity'), 0) or 0) > 0
                ]
        removed_locations[location_key] = location_rows
        state['removed_locations_by_msku'] = removed_locations

        if max(0, ss_normalization._coerce_int(scan.get('label_printed'), 0)):
            printed_counts = state.get('printed_item_label_counts')
            printed_counts = printed_counts if isinstance(printed_counts, dict) else {}
            printed_key = state_key(printed_counts)
            printed_counts[printed_key] = max(
                0, (ss_listing_settings._listingagent_parse_int(printed_counts.get(printed_key), 0) or 0) - 1
            )
            state['printed_item_label_counts'] = printed_counts
        if isinstance(state.get('last_item_label_print'), dict) and state['last_item_label_print'].get('scan_token') == scan_token:
            state.pop('last_item_label_print', None)
        if isinstance(state.get('last_pack_scan'), dict) and state['last_pack_scan'].get('scan_token') == scan_token:
            state.pop('last_pack_scan', None)

        cur.execute('DELETE FROM fba_pack_scans WHERE id = ? AND session_id = ?', (scan['id'], session_id))
        if cur.rowcount != 1:
            raise FbaInboundValidationError('Another worker already reset this packing scan')
        ss_fba_shipments._fba_save_amazon_state(conn, session_id, state)
        conn.commit()
        if inventory_restored:
            ss_caching.update_data_version()
            ss_caching._invalidate_searchrack_cache()
        return jsonify({
            'success': True,
            'amazon_workflow': state,
            'reset_scan': {
                'id': scan.get('id'), 'scan_token': scan_token, 'msku': scan_msku,
                'barcode': scan.get('barcode'), 'box_id': box_id,
                'source_location': source_location,
                'inventory_restored': inventory_restored,
                'label_had_printed': bool(scan.get('label_printed')),
            },
            'restored_inventory': restored,
        })
    except FbaInboundValidationError as exc:
        if conn is not None:
            conn.rollback()
        return jsonify({'success': False, 'error': str(exc)}), 409
    except Exception as exc:
        if conn is not None:
            conn.rollback()
        return jsonify({'success': False, 'error': ss_errors._safe_error(exc, 'fba reset packing scan')}), 500
    finally:
        if conn is not None:
            conn.close()


def api_fba_prep_count_scan(session_id):
    """Count products found in Amazon's catalog; a seller offer is optional here."""
    conn = None
    try:
        data = request.get_json(silent=True) or {}
        barcode = ss_fba_schema._fba_trim(data.get('barcode'), 160)
        barcode_key = ss_fba_schema._fba_trim(ss_warehouse_locations._movelocation_barcode_key(barcode) or barcode.casefold(), 160)
        scan_token = ss_fba_schema._fba_trim(data.get('scan_token'), 160)
        client_id, operator_name = ss_fba_schema._fba_client_identity(data)
        if not barcode or not barcode_key:
            return jsonify({'success': False, 'error': 'Scan a barcode first'}), 400
        if not scan_token:
            return jsonify({'success': False, 'error': 'Missing scan token'}), 400
        try:
            amazon_listing = ss_fba_inventory._fba_local_amazon_listing(barcode, strict=True)
            if not ss_fba_schema._fba_trim(amazon_listing.get('asin'), 30):
                credentials, _seller_id, marketplace_id, marketplace = ss_amazon_catalog._amazon_spapi_context()
                catalog_product = ss_amazon_catalog._amazon_catalog_product_from_barcode(
                    credentials, marketplace, marketplace_id, barcode, strict=True)
                amazon_listing = dict(catalog_product)
                # An existing offer can enrich the result, but is never required
                # once the product has been found in Amazon's catalog.
                if catalog_product.get('asin'):
                    local_offer = ss_fba_inventory._fba_local_amazon_listing_by_asin(catalog_product['asin'])
                    if local_offer:
                        amazon_listing.update(local_offer)
        except Exception as exc:
            ss_config.logger.warning('FBA count lookup unavailable for %s: %s', barcode, exc)
            return jsonify({
                'success': False,
                'barcode': barcode,
                'sort_result': {
                    'kind': 'error', 'headline': 'LOOKUP UNAVAILABLE — SCAN AGAIN',
                    'instruction': 'Listing could not be checked. Set this item aside and retry.',
                },
                'error': 'Amazon listing lookup is unavailable. This item was not counted; scan it again.',
            }), 503
        if not ss_fba_schema._fba_trim(amazon_listing.get('asin'), 30):
            ss_config.logger.warning('FBA count rejected: no catalog product for barcode %s', barcode)
            return jsonify({
                'success': False,
                'not_added': True,
                'barcode': barcode,
                'voice_message': 'Rejected.',
                'sort_result': {
                    'kind': 'rejected',
                    'headline': 'REJECTED — DO NOT INCLUDE',
                    'instruction': 'No product was found in Amazon’s catalog for this barcode. Set this item aside.',
                },
                'error': 'Item not added: no product was found in Amazon’s catalog for this barcode.',
            }), 422
        # Step one verifies catalog existence only. FBA eligibility, safety
        # requirements and labels are checked in the later enablement step.
        conn = sqlite3.connect(str(ss_config.BASE_DIR / 'searchRack.db'), timeout=30.0)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        ss_fba_schema._ensure_fba_prep_tables(cur)
        conn.commit()
        cur.execute('BEGIN IMMEDIATE')
        row = cur.execute("SELECT * FROM fba_prep_sessions WHERE id = ? AND status = 'open'", (session_id,)).fetchone()
        if not row:
            return jsonify({'success': False, 'error': 'Open FBA session not found'}), 404
        if ss_fba_schema._fba_trim(row['amazon_inbound_plan_id'], 80):
            return jsonify({'success': False, 'error': 'Amazon plan quantities are already locked'}), 409
        duplicate = cur.execute('SELECT id FROM fba_count_scans WHERE scan_token = ?', (scan_token,)).fetchone()
        items = ss_fba_schema._fba_json_list(row['items_json'])
        if not duplicate:
            match = next((item for item in items if ss_fba_schema._fba_trim(item.get('barcode_key') or ss_warehouse_locations._movelocation_barcode_key(item.get('barcode')), 160).casefold() == barcode_key.casefold()), None)
            if match:
                match['quantity'] = min(10000, max(0, int(match.get('quantity') or 0)) + 1)
                if amazon_listing and not ss_fba_schema._fba_trim(match.get('seller_sku'), 120):
                    match['seller_sku'] = ss_fba_schema._fba_trim(amazon_listing.get('seller_sku'), 120)
                    match['asin'] = ss_fba_schema._fba_trim(amazon_listing.get('asin'), 30)
                    match['title'] = ss_fba_schema._fba_trim(amazon_listing.get('title'), 500)
                    match['condition'] = ss_fba_schema._fba_trim(amazon_listing.get('condition') or 'New', 80)
                    match.setdefault('fba', {})['amazon_listing'] = amazon_listing
                # The UI renders this persisted list in reverse order, so move a
                # rescanned item to the end to keep the latest scan at the top.
                items.append(items.pop(items.index(match)))
            else:
                new_item = ss_fba_inventory._fba_session_item_payload({
                    'barcode': barcode, 'barcode_display': barcode, 'barcode_key': barcode_key,
                    'title': amazon_listing.get('title'), 'quantity': 1,
                    'condition': amazon_listing.get('condition') or 'New',
                    'seller_sku': amazon_listing.get('seller_sku'), 'asin': amazon_listing.get('asin'),
                    'fba': {'amazon_listing': amazon_listing},
                })
                items.append(new_item)
            now = ss_listing_checks._listagent_now_iso()
            cur.execute('''UPDATE fba_prep_sessions SET items_json = ?, item_count = ?, total_units = ?,
                           count_revision = COALESCE(count_revision, 0) + 1, updated_at = ? WHERE id = ?''',
                        (json.dumps(items, ensure_ascii=False), len(items), sum(int(item.get('quantity') or 0) for item in items), now, session_id))
            cur.execute('''INSERT INTO fba_count_scans
                           (session_id, scan_token, barcode, barcode_key, client_id, operator_name, scanned_at)
                           VALUES (?, ?, ?, ?, ?, ?, ?)''',
                        (session_id, scan_token, barcode, barcode_key, client_id or None, operator_name or None, now))
            ss_fba_schema._fba_touch_session_worker(cur, session_id, client_id, operator_name, '')
        conn.commit()
        row = cur.execute('SELECT * FROM fba_prep_sessions WHERE id = ?', (session_id,)).fetchone()
        sort_result = {
            'kind': 'ready', 'headline': 'COUNTED',
            'instruction': 'Unit added to this FBA count.',
        }
        feedback = {
            'kind': 'needs_enablement',
            'message': 'Amazon catalog product found. Seller listing and FBA setup are checked later.',
            'voice_message': '',
        }
        return jsonify({
            'success': True, 'duplicate': bool(duplicate), 'count_feedback': feedback,
            'sort_result': sort_result,
            'session': ss_fba_inventory._fba_session_row_payload(row, include_items=True),
        })
    except Exception as exc:
        if conn is not None:
            conn.rollback()
        return jsonify({'success': False, 'error': ss_errors._safe_error(exc, 'fba count scan')}), 500
    finally:
        if conn is not None:
            conn.close()


def api_fba_prep_amazon_move_box_scan(session_id):
    """Move one previously packed physical unit between compatible cartons."""
    conn = None
    try:
        data = request.get_json() or {}
        move_token = ss_fba_schema._fba_trim(data.get('move_token'), 100)
        barcode = ss_fba_schema._fba_trim(data.get('barcode'), 160)
        msku = ss_fba_schema._fba_trim(data.get('msku') or data.get('seller_sku'), 255)
        from_box_id = ss_fba_schema._fba_trim(data.get('from_box_id'), 40).upper()
        to_box_id = ss_fba_schema._fba_trim(data.get('to_box_id'), 40).upper()
        client_id, operator_name = ss_fba_schema._fba_client_identity(data)
        active_box_id = ss_fba_schema._fba_trim(data.get('active_box_id'), 40).upper()
        if not move_token or not re.fullmatch(r'[A-Za-z0-9._:-]{8,100}', move_token):
            raise FbaInboundValidationError('A valid box-move scan token is required')
        if not barcode:
            raise FbaInboundValidationError('Scan an item barcode to move')
        if not msku:
            raise FbaInboundValidationError('The scanned item is not matched to an Amazon Seller SKU')
        if not from_box_id or not to_box_id:
            raise FbaInboundValidationError('Choose both the source and destination cartons')
        if from_box_id == to_box_id:
            raise FbaInboundValidationError('Choose two different cartons')

        conn = sqlite3.connect(str(ss_config.BASE_DIR / 'searchRack.db'), timeout=30.0)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        ss_fba_schema._ensure_fba_prep_tables(cur)
        conn.commit()
        conn.execute('BEGIN IMMEDIATE')
        cur = conn.cursor()

        existing = cur.execute(
            'SELECT * FROM fba_box_move_scans WHERE move_token = ? LIMIT 1', (move_token,)
        ).fetchone()
        if existing:
            if int(existing['session_id']) != int(session_id):
                raise FbaInboundValidationError('That box-move token belongs to another session')
            session_row = cur.execute(
                'SELECT * FROM fba_prep_sessions WHERE id = ?', (session_id,)
            ).fetchone()
            return jsonify({
                'success': True, 'idempotent': True, 'move': dict(existing),
                'amazon_workflow': ss_fba_shipments._fba_amazon_state(session_row),
            })

        _row, _session_data, state = ss_fba_shipments._fba_amazon_session(conn, session_id)
        ss_fba_schema._fba_touch_session_worker(cur, session_id, client_id, operator_name, active_box_id)
        if not state.get('packing_confirmed'):
            raise FbaInboundValidationError('Packing has not started for this Amazon plan')
        if state.get('boxes_submitted'):
            raise FbaInboundValidationError('Box contents have already been submitted to Amazon and cannot be moved')

        boxes = state.get('boxes') if isinstance(state.get('boxes'), list) else []
        source_box = next((
            box for box in boxes if isinstance(box, dict)
            and ss_fba_schema._fba_trim(box.get('local_id'), 40).upper() == from_box_id
        ), None)
        destination_box = next((
            box for box in boxes if isinstance(box, dict)
            and ss_fba_schema._fba_trim(box.get('local_id'), 40).upper() == to_box_id
        ), None)
        if source_box is None or destination_box is None:
            raise FbaInboundValidationError('One of the selected cartons no longer exists; refresh and choose again')
        source_group_id = ss_fba_schema._fba_trim(source_box.get('packing_group_id'), 38)
        destination_group_id = ss_fba_schema._fba_trim(destination_box.get('packing_group_id'), 38)
        if not source_group_id or source_group_id != destination_group_id:
            raise FbaInboundValidationError('Items can only move between cartons in the same Amazon packing group')

        packing_group = next((
            group for group in state.get('packing_groups') or [] if isinstance(group, dict)
            and ss_fba_schema._fba_trim(group.get('packing_group_id'), 38) == source_group_id
            and any(
                isinstance(item, dict)
                and ss_fba_schema._fba_trim(item.get('msku') or item.get('seller_sku'), 255).casefold() == msku.casefold()
                for item in group.get('items') or []
            )
        ), None)
        if packing_group is None:
            raise FbaInboundValidationError(f'{msku} cannot be placed in the selected destination carton')

        source_contents = source_box.get('contents') if isinstance(source_box.get('contents'), list) else []
        source_item = next((
            item for item in source_contents if isinstance(item, dict)
            and ss_fba_schema._fba_trim(item.get('msku'), 255).casefold() == msku.casefold()
        ), None)
        source_quantity = max(0, ss_listing_settings._listingagent_parse_int((source_item or {}).get('quantity'), 0) or 0)
        if source_item is None or source_quantity <= 0:
            raise FbaInboundValidationError(f'{msku} is not packed in {from_box_id}')

        packed_scan = cur.execute('''
            SELECT * FROM fba_pack_scans
            WHERE session_id = ? AND box_id = ? COLLATE NOCASE AND msku = ? COLLATE NOCASE
            ORDER BY id DESC LIMIT 1
        ''', (session_id, from_box_id, msku)).fetchone()
        if not packed_scan:
            raise FbaInboundValidationError(
                f'The saved physical scan for {msku} in {from_box_id} could not be found'
            )
        packed_scan = dict(packed_scan)

        if source_quantity == 1:
            source_box['contents'] = [item for item in source_contents if item is not source_item]
        else:
            source_item['quantity'] = source_quantity - 1
            source_box['contents'] = source_contents
        destination_contents = (
            destination_box.get('contents') if isinstance(destination_box.get('contents'), list) else []
        )
        destination_item = next((
            item for item in destination_contents if isinstance(item, dict)
            and ss_fba_schema._fba_trim(item.get('msku'), 255).casefold() == msku.casefold()
        ), None)
        if destination_item is None:
            destination_item = {'msku': msku, 'quantity': 0}
            destination_contents.append(destination_item)
        destination_item['quantity'] = max(
            0, ss_listing_settings._listingagent_parse_int(destination_item.get('quantity'), 0) or 0
        ) + 1
        destination_box['contents'] = destination_contents
        # The physical contents changed, so both cartons must be weighed again.
        source_box['weight_lb'] = ''
        destination_box['weight_lb'] = ''
        state['boxes'] = boxes
        moved_at = ss_listing_checks._listagent_now_iso()
        state['last_box_move'] = {
            'move_token': move_token, 'barcode': barcode, 'msku': msku,
            'from_box_id': from_box_id, 'to_box_id': to_box_id,
            'pack_scan_id': int(packed_scan.get('id') or 0),
            'client_id': client_id, 'operator_name': operator_name, 'moved_at': moved_at,
        }

        cur.execute('''
            UPDATE fba_pack_scans
            SET box_id = ?, packing_group_id = ?
            WHERE id = ? AND session_id = ? AND box_id = ? COLLATE NOCASE
        ''', (
            to_box_id, destination_group_id, int(packed_scan.get('id') or 0),
            session_id, from_box_id,
        ))
        if cur.rowcount != 1:
            raise FbaInboundValidationError('Another scanner moved this unit first; refresh the cartons')
        cur.execute('''
            INSERT INTO fba_box_move_scans (
                session_id, move_token, pack_scan_id, barcode, msku,
                from_box_id, to_box_id, client_id, operator_name, moved_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            session_id, move_token, int(packed_scan.get('id') or 0), barcode, msku,
            from_box_id, to_box_id, client_id or None, operator_name or None, moved_at,
        ))
        state['last_box_move']['id'] = int(cur.lastrowid or 0)
        ss_fba_shipments._fba_save_amazon_state(conn, session_id, state)
        conn.commit()
        return jsonify({
            'success': True, 'amazon_workflow': state, 'move': state['last_box_move'],
            'reweigh_box_ids': [from_box_id, to_box_id],
        })
    except FbaInboundValidationError as exc:
        if conn is not None:
            conn.rollback()
        return jsonify({'success': False, 'error': str(exc)}), 409
    except Exception as exc:
        if conn is not None:
            conn.rollback()
        return jsonify({'success': False, 'error': ss_errors._safe_error(exc, 'fba box move scan')}), 500
    finally:
        if conn is not None:
            conn.close()
