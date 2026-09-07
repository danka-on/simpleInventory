"""Fba shipments for Sweet Shelves."""

import json
import sqlite3
import time
from decimal import Decimal, InvalidOperation
from fba_inbound import (
    FbaInboundValidationError, US_MARKETPLACE_ID, apply_owner_corrections_from_amazon_error,
    build_create_plan_request, build_set_packing_request, build_transportation_request,
    missing_source_address_fields, normalize_box_drafts, normalize_source_address, option_state,
    summarize_money,
)
from flask import jsonify, redirect, request
from . import (
    amazon_catalog as ss_amazon_catalog, config as ss_config, errors as ss_errors, fba_inventory as
    ss_fba_inventory, fba_readiness as ss_fba_readiness, fba_schema as ss_fba_schema, listing_checks as
    ss_listing_checks, listing_settings as ss_listing_settings, shipping_labels as ss_shipping_labels,
)


def _fba_amazon_source_from_settings(settings=None):
    settings = settings if isinstance(settings, dict) else ss_shipping_labels._labelmaster_get_settings()
    return normalize_source_address({
        'name': settings.get('label_from_name'),
        'companyName': settings.get('label_from_company'),
        'addressLine1': settings.get('label_from_address1'),
        'addressLine2': settings.get('label_from_address2'),
        'city': settings.get('label_from_city'),
        'districtOrCounty': settings.get('label_from_county'),
        'stateOrProvinceCode': settings.get('label_from_state'),
        'postalCode': settings.get('label_from_postal'),
        'countryCode': settings.get('label_from_country') or 'US',
        'phoneNumber': settings.get('label_from_phone'),
        'email': settings.get('label_from_email'),
    })


def _fba_amazon_client(*, legacy=False):
    credentials, _seller_id, marketplace_id, marketplace = ss_amazon_catalog._amazon_spapi_context()
    if marketplace is None:
        raise FbaInboundValidationError('Amazon SP-API marketplace is not available')
    from sp_api.api import FulfillmentInbound, FulfillmentInboundVersion
    version = FulfillmentInboundVersion.V_v0 if legacy else FulfillmentInboundVersion.V_2024_03_20
    return (
        FulfillmentInbound(credentials=credentials, marketplace=marketplace, version=version),
        marketplace_id,
    )


def _fba_amazon_payload(response):
    errors = getattr(response, 'errors', None)
    if errors:
        problems = errors if isinstance(errors, list) else [errors]
        fatal = next((
            problem for problem in problems
            if not (
                isinstance(problem, dict)
                and (
                    ss_fba_schema._fba_trim(problem.get('severity'), 20).upper() == 'WARNING'
                    or ss_fba_schema._fba_trim(problem.get('message'), 700).upper().startswith('WARNING:')
                )
            )
        ), None)
        if fatal is not None:
            if isinstance(fatal, dict):
                message = fatal.get('message') or fatal.get('code') or str(fatal)
            else:
                message = str(fatal)
            raise FbaInboundValidationError('Amazon rejected the request: ' + ss_fba_schema._fba_trim(message, 700))
    payload = getattr(response, 'payload', None)
    if payload is None:
        return {}
    try:
        return json.loads(json.dumps(payload, ensure_ascii=False, default=str))
    except Exception:
        return dict(payload) if isinstance(payload, dict) else {}


def _fba_amazon_collect(callable_fn, result_key, *, page_size=100, limit=5000):
    rows = []
    token = None
    while len(rows) < limit:
        kwargs = {'pageSize': page_size}
        if token:
            kwargs['paginationToken'] = token
        payload = _fba_amazon_payload(callable_fn(**kwargs))
        page = payload.get(result_key) if isinstance(payload.get(result_key), list) else []
        rows.extend(page)
        pagination = payload.get('pagination') if isinstance(payload.get('pagination'), dict) else {}
        token = pagination.get('nextToken') or pagination.get('next_token')
        if not token or not page:
            break
    return rows[:limit]


def _fba_amazon_state(row):
    raw = row['amazon_state_json'] if row and 'amazon_state_json' in row.keys() else '{}'
    state = ss_fba_schema._fba_json_dict(raw)
    state.setdefault('stage', str((row['amazon_stage'] if row and 'amazon_stage' in row.keys() else '') or 'draft'))
    state.setdefault('inbound_plan_id', str((row['amazon_inbound_plan_id'] if row and 'amazon_inbound_plan_id' in row.keys() else '') or ''))
    state.setdefault('boxes', [])
    state.setdefault('packing_options', [])
    state.setdefault('packing_groups', [])
    state.setdefault('placement_options', [])
    state.setdefault('shipments', [])
    state.setdefault('transportation_options', [])
    state.setdefault('revision', 0)
    return state


def _fba_save_amazon_state(conn, session_id, state, *, preserve_concurrent_pack=False):
    state = state if isinstance(state, dict) else {}
    latest_row = conn.execute(
        'SELECT amazon_state_json, amazon_stage, amazon_inbound_plan_id FROM fba_prep_sessions WHERE id = ?',
        (session_id,),
    ).fetchone()
    latest_state = _fba_amazon_state(latest_row) if latest_row else {}
    latest_revision = max(0, ss_listing_settings._listingagent_parse_int(latest_state.get('revision'), 0) or 0)
    incoming_revision = max(0, ss_listing_settings._listingagent_parse_int(state.get('revision'), 0) or 0)
    if preserve_concurrent_pack and latest_revision > incoming_revision:
        for key in (
            'boxes', 'inventory_removed_by_msku', 'removed_locations_by_msku',
            'last_pack_scan', 'printed_item_label_counts', 'last_item_label_print',
        ):
            if key in latest_state:
                state[key] = latest_state[key]
    stage = ss_fba_schema._fba_trim(state.get('stage') or 'draft', 60).lower()
    inbound_plan_id = ss_fba_schema._fba_trim(state.get('inbound_plan_id'), 38)
    state['stage'] = stage
    state['inbound_plan_id'] = inbound_plan_id
    state['revision'] = max(latest_revision, incoming_revision) + 1
    state['updated_at'] = ss_listing_checks._listagent_now_iso()
    state_json = json.dumps(state, ensure_ascii=False, default=str)
    if len(state_json.encode('utf-8')) > 4 * 1024 * 1024:
        raise FbaInboundValidationError('The Amazon workflow response is too large to save')
    result = conn.execute('''
        UPDATE fba_prep_sessions
        SET amazon_inbound_plan_id = ?, amazon_stage = ?, amazon_state_json = ?,
            amazon_updated_at = ?, updated_at = ?
        WHERE id = ? AND status = 'open'
    ''', (inbound_plan_id or None, stage, state_json, state['updated_at'], state['updated_at'], session_id))
    if result.rowcount <= 0:
        raise FbaInboundValidationError('Open FBA session not found')


def _fba_amazon_session(conn, session_id):
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    ss_fba_schema._ensure_fba_prep_tables(cur)
    row = cur.execute('SELECT * FROM fba_prep_sessions WHERE id = ?', (session_id,)).fetchone()
    if not row or str(row['status'] or '') != 'open':
        raise FbaInboundValidationError('Open FBA session not found')
    return row, ss_fba_inventory._fba_session_row_payload(row, include_items=True), _fba_amazon_state(row)


def _fba_amazon_option_summary(option, kind):
    option = option if isinstance(option, dict) else {}
    id_key = 'packingOptionId' if kind == 'packing' else 'placementOptionId'
    result = {
        id_key: ss_fba_schema._fba_trim(option.get(id_key), 38),
        'status': ss_fba_schema._fba_trim(option.get('status'), 40).upper(),
        'expiration': ss_fba_schema._fba_trim(option.get('expiration'), 50),
        'fees': option.get('fees') if isinstance(option.get('fees'), list) else [],
        'discounts': option.get('discounts') if isinstance(option.get('discounts'), list) else [],
    }
    result['fee_totals'] = summarize_money(result['fees'])
    result['discount_totals'] = summarize_money(result['discounts'])
    if kind == 'packing':
        result['packingGroups'] = [
            ss_fba_schema._fba_trim(value, 38) for value in (option.get('packingGroups') or []) if ss_fba_schema._fba_trim(value, 38)
        ]
    else:
        result['shipmentIds'] = [
            ss_fba_schema._fba_trim(value, 38) for value in (option.get('shipmentIds') or []) if ss_fba_schema._fba_trim(value, 38)
        ]
    return result


def _fba_transport_option_can_purchase(option):
    option = option if isinstance(option, dict) else {}
    if ss_fba_schema._fba_trim(option.get('shippingSolution'), 80).upper() != 'AMAZON_PARTNERED_CARRIER':
        return False
    if ss_fba_schema._fba_trim(option.get('shippingMode'), 80).upper() != 'GROUND_SMALL_PARCEL':
        return False
    if option.get('preconditions'):
        return False
    quote = option.get('quote') if isinstance(option.get('quote'), dict) else {}
    cost = quote.get('cost') if isinstance(quote.get('cost'), dict) else {}
    try:
        amount = Decimal(str(cost.get('amount')))
    except (InvalidOperation, TypeError, ValueError):
        return False
    return amount.is_finite() and amount >= 0


def _fba_amazon_transport_summary(option):
    option = option if isinstance(option, dict) else {}
    quote = option.get('quote') if isinstance(option.get('quote'), dict) else {}
    carrier = option.get('carrier') if isinstance(option.get('carrier'), dict) else {}
    return {
        'transportationOptionId': ss_fba_schema._fba_trim(option.get('transportationOptionId'), 38),
        'shipmentId': ss_fba_schema._fba_trim(option.get('shipmentId'), 38),
        'shippingMode': ss_fba_schema._fba_trim(option.get('shippingMode'), 80),
        'shippingSolution': ss_fba_schema._fba_trim(option.get('shippingSolution'), 80),
        'carrier': {
            'name': ss_fba_schema._fba_trim(carrier.get('name'), 120),
            'alphaCode': ss_fba_schema._fba_trim(carrier.get('alphaCode'), 40),
        },
        'quote': quote,
        'preconditions': option.get('preconditions') if isinstance(option.get('preconditions'), list) else [],
        'can_purchase': _fba_transport_option_can_purchase(option),
    }


def _fba_amazon_refresh_operation(api, state):
    operation = state.get('operation') if isinstance(state.get('operation'), dict) else {}
    operation_id = ss_fba_schema._fba_trim(operation.get('id'), 38)
    if not operation_id or str(operation.get('status') or '').upper() in ('SUCCESS', 'FAILED'):
        return state
    payload = _fba_amazon_payload(api.get_inbound_operation_status(operation_id))
    status = ss_fba_schema._fba_trim(payload.get('operationStatus') or 'IN_PROGRESS', 30).upper()
    operation.update({
        'status': status,
        'problems': payload.get('operationProblems') if isinstance(payload.get('operationProblems'), list) else [],
        'checked_at': ss_listing_checks._listagent_now_iso(),
    })
    state['operation'] = operation
    if status == 'SUCCESS':
        state['stage'] = operation.get('next_stage') or state.get('stage') or 'plan_created'
        success_flag = ss_fba_schema._fba_trim(operation.get('success_flag'), 80)
        if success_flag:
            state[success_flag] = True
    elif status == 'FAILED':
        problems = operation.get('problems') if isinstance(operation.get('problems'), list) else []
        detail = next((
            ss_fba_schema._fba_trim(problem.get('message') or problem.get('details'), 700)
            for problem in problems if isinstance(problem, dict)
            and ss_fba_schema._fba_trim(problem.get('message') or problem.get('details'), 700)
        ), '')
        state['last_error'] = detail or (
            'Amazon could not complete ' + str(operation.get('kind') or 'the operation')
        )
        if operation.get('kind') == 'create_plan':
            state['stage'] = 'plan_failed'
    return state


def _fba_amazon_sync_snapshot(api, state):
    plan_id = ss_fba_schema._fba_trim(state.get('inbound_plan_id'), 38)
    if not plan_id:
        return state
    state = _fba_amazon_refresh_operation(api, state)
    operation = state.get('operation') if isinstance(state.get('operation'), dict) else {}
    if str(operation.get('status') or '').upper() not in ('', 'SUCCESS', 'FAILED'):
        return state

    plan = _fba_amazon_payload(api.get_inbound_plan(plan_id))
    state['plan'] = {
        key: plan.get(key)
        for key in ('inboundPlanId', 'name', 'status', 'createdAt', 'lastUpdatedAt', 'marketplaceIds')
        if key in plan
    }
    state['plan_items'] = _fba_amazon_collect(
        lambda **kwargs: api.list_inbound_plan_items(plan_id, **kwargs), 'items', page_size=100, limit=2500
    )
    packing_options = _fba_amazon_collect(
        lambda **kwargs: api.list_packing_options(plan_id, **kwargs), 'packingOptions', page_size=20, limit=100
    )
    state['packing_options'] = [_fba_amazon_option_summary(option, 'packing') for option in packing_options]
    accepted_packing = next((row for row in state['packing_options'] if option_state(row) == 'ACCEPTED'), None)
    selected_packing_id = ss_fba_schema._fba_trim(state.get('selected_packing_option_id'), 38)
    selected_packing = accepted_packing or next(
        (row for row in state['packing_options'] if row.get('packingOptionId') == selected_packing_id), None
    )
    if accepted_packing:
        state['selected_packing_option_id'] = accepted_packing.get('packingOptionId')
        state['packing_confirmed'] = True

    packing_groups = []
    for index, group_id in enumerate((selected_packing or {}).get('packingGroups') or [], 1):
        group_items = _fba_amazon_collect(
            lambda group_id=group_id, **kwargs: api.list_packing_group_items(plan_id, group_id, **kwargs),
            'items', page_size=100, limit=2500,
        )
        packing_groups.append({'packing_group_id': group_id, 'label': f'Group {index}', 'items': group_items})
    if packing_groups:
        state['packing_groups'] = packing_groups

    placement_options = _fba_amazon_collect(
        lambda **kwargs: api.list_placement_options(plan_id, **kwargs), 'placementOptions', page_size=20, limit=100
    )
    state['placement_options'] = [_fba_amazon_option_summary(option, 'placement') for option in placement_options]
    accepted_placement = next((row for row in state['placement_options'] if option_state(row) == 'ACCEPTED'), None)
    selected_placement_id = ss_fba_schema._fba_trim(state.get('selected_placement_option_id'), 38)
    selected_placement = accepted_placement or next(
        (row for row in state['placement_options'] if row.get('placementOptionId') == selected_placement_id), None
    )
    if accepted_placement:
        state['selected_placement_option_id'] = accepted_placement.get('placementOptionId')
        state['placement_confirmed'] = True

    shipment_ids = []
    for option in state['placement_options']:
        for shipment_id in option.get('shipmentIds') or []:
            if shipment_id not in shipment_ids:
                shipment_ids.append(shipment_id)
    shipments = []
    for shipment_id in shipment_ids[:40]:
        shipment = _fba_amazon_payload(api.get_shipment(plan_id, shipment_id))
        destination = shipment.get('destination') if isinstance(shipment.get('destination'), dict) else {}
        source = shipment.get('source') if isinstance(shipment.get('source'), dict) else {}
        shipments.append({
            'shipmentId': shipment_id,
            'shipmentConfirmationId': ss_fba_schema._fba_trim(shipment.get('shipmentConfirmationId'), 40),
            'placementOptionId': ss_fba_schema._fba_trim(shipment.get('placementOptionId'), 38),
            'name': ss_fba_schema._fba_trim(shipment.get('name'), 160),
            'status': ss_fba_schema._fba_trim(shipment.get('status'), 60),
            'destination': {
                'warehouseId': ss_fba_schema._fba_trim(destination.get('warehouseId'), 40),
                'city': ss_fba_schema._fba_trim(destination.get('city'), 80),
                'stateOrProvinceCode': ss_fba_schema._fba_trim(destination.get('stateOrProvinceCode'), 30),
            },
            'source': {'city': ss_fba_schema._fba_trim(source.get('city'), 80), 'stateOrProvinceCode': ss_fba_schema._fba_trim(source.get('stateOrProvinceCode'), 30)},
            'selectedTransportationOptionId': ss_fba_schema._fba_trim(shipment.get('selectedTransportationOptionId'), 38),
        })
    state['shipments'] = shipments

    if selected_placement:
        transportation = _fba_amazon_collect(
            lambda **kwargs: api.list_transportation_options(
                plan_id, placementOptionId=selected_placement.get('placementOptionId'), **kwargs
            ), 'transportationOptions', page_size=50, limit=500,
        )
        state['transportation_options'] = [_fba_amazon_transport_summary(row) for row in transportation]

    operation_failed = str((state.get('operation') or {}).get('status') or '').upper() == 'FAILED'
    plan_failed = str((state.get('plan') or {}).get('status') or '').upper() == 'ERRORED'
    if operation_failed or plan_failed:
        if (state.get('operation') or {}).get('kind') == 'create_plan' or plan_failed:
            state['stage'] = 'plan_failed'
    elif state.get('transport_confirmed'):
        state['stage'] = 'transport_confirmed'
    elif state.get('placement_confirmed'):
        state['stage'] = 'transport_options' if state.get('transportation_options') else 'placement_confirmed'
    elif state.get('boxes_submitted'):
        state['stage'] = 'placement_options' if state.get('placement_options') else 'boxes_submitted'
    elif state.get('packing_confirmed'):
        state['stage'] = 'packing'
    elif state.get('packing_options'):
        state['stage'] = 'packing_options'
    else:
        state['stage'] = 'plan_created'
    state['last_synced_at'] = ss_listing_checks._listagent_now_iso()
    return state


def api_fba_prep_amazon_readiness():
    try:
        if request.method == 'POST':
            data = request.get_json() or {}
            source = normalize_source_address(data.get('source_address') if isinstance(data.get('source_address'), dict) else data)
            missing = missing_source_address_fields(source)
            if missing:
                return jsonify({'success': False, 'error': 'Complete the ship-from ' + ', '.join(missing)}), 400
            ss_listing_settings._listingagent_upsert_settings({
                'label_from_name': source.get('name', ''),
                'label_from_company': source.get('companyName', ''),
                'label_from_address1': source.get('addressLine1', ''),
                'label_from_address2': source.get('addressLine2', ''),
                'label_from_city': source.get('city', ''),
                'label_from_county': source.get('districtOrCounty', ''),
                'label_from_state': source.get('stateOrProvinceCode', ''),
                'label_from_postal': source.get('postalCode', ''),
                'label_from_country': source.get('countryCode', 'US'),
                'label_from_phone': source.get('phoneNumber', ''),
                'label_from_email': source.get('email', ''),
            })
        label_settings = ss_shipping_labels._labelmaster_get_settings()
        source = _fba_amazon_source_from_settings(label_settings)
        _credentials, _seller_id, marketplace_id, marketplace = ss_amazon_catalog._amazon_spapi_context()
        return jsonify({
            'success': True,
            'configured': marketplace is not None,
            'marketplace_id': marketplace_id,
            'is_us_marketplace': marketplace_id == US_MARKETPLACE_ID,
            'source_address': source,
            'box_defaults': {
                'length_in': '',
                'width_in': '',
                'height_in': '',
                'weight_lb': '',
            },
            'missing_for_plan': missing_source_address_fields(source),
            'missing_for_transport': missing_source_address_fields(source, require_contact=True),
        })
    except Exception as exc:
        return jsonify({'success': False, 'error': ss_errors._safe_error(exc, 'fba amazon readiness')}), 500


def api_fba_prep_amazon_plans():
    try:
        api, _marketplace_id = _fba_amazon_client()
        plans = _fba_amazon_collect(
            lambda **kwargs: api.list_inbound_plans(status='ACTIVE', sortBy='LAST_UPDATED_TIME', sortOrder='DESC', **kwargs),
            'inboundPlans', page_size=30, limit=100,
        )
        return jsonify({'success': True, 'plans': [{
            key: row.get(key)
            for key in ('inboundPlanId', 'name', 'status', 'createdAt', 'lastUpdatedAt', 'marketplaceIds')
        } for row in plans]})
    except Exception as exc:
        return jsonify({'success': False, 'error': ss_errors._safe_error(exc, 'fba amazon plans')}), 502


def api_fba_prep_amazon_sync(session_id):
    conn = None
    try:
        conn = sqlite3.connect(str(ss_config.BASE_DIR / 'searchRack.db'), timeout=30.0)
        conn.row_factory = sqlite3.Row
        _row, _session, state = _fba_amazon_session(conn, session_id)
        if state.get('inbound_plan_id'):
            api, _marketplace_id = _fba_amazon_client()
            state = _fba_amazon_sync_snapshot(api, state)
            _fba_save_amazon_state(conn, session_id, state, preserve_concurrent_pack=True)
            conn.commit()
        return jsonify({'success': True, 'amazon_workflow': state})
    except FbaInboundValidationError as exc:
        if conn is not None:
            conn.rollback()
        return jsonify({'success': False, 'error': str(exc)}), 400
    except Exception as exc:
        if conn is not None:
            conn.rollback()
        return jsonify({'success': False, 'error': ss_errors._safe_error(exc, 'fba amazon sync')}), 502
    finally:
        if conn is not None:
            conn.close()


def api_fba_prep_amazon_action(session_id, action):
    allowed = {
        'create-plan', 'generate-packing', 'confirm-packing', 'save-boxes',
        'create-box', 'remove-box',
        'submit-boxes', 'generate-placement', 'confirm-placement',
        'generate-transportation', 'confirm-transportation', 'reset-failed-plan',
    }
    if action not in allowed:
        return jsonify({'success': False, 'error': 'Unknown Amazon workflow action'}), 404
    conn = None
    try:
        data = request.get_json() or {}
        conn = sqlite3.connect(str(ss_config.BASE_DIR / 'searchRack.db'), timeout=30.0)
        conn.row_factory = sqlite3.Row
        if action in ('save-boxes', 'create-box', 'remove-box'):
            conn.execute('BEGIN IMMEDIATE')
        _row, session_data, state = _fba_amazon_session(conn, session_id)
        plan_id = ss_fba_schema._fba_trim(state.get('inbound_plan_id'), 38)

        if action == 'reset-failed-plan':
            operation = state.get('operation') if isinstance(state.get('operation'), dict) else {}
            plan = state.get('plan') if isinstance(state.get('plan'), dict) else {}
            if str(operation.get('status') or '').upper() != 'FAILED' and str(plan.get('status') or '').upper() != 'ERRORED':
                raise FbaInboundValidationError('Only an Amazon plan that failed can be reset')
            state = {
                'stage': 'draft', 'inbound_plan_id': '', 'boxes': [],
                'packing_options': [], 'packing_groups': [], 'placement_options': [],
                'shipments': [], 'transportation_options': [],
            }
            _fba_save_amazon_state(conn, session_id, state)
            conn.commit()
            return jsonify({'success': True, 'amazon_workflow': state, 'local_only': True})

        if action == 'save-boxes':
            incoming_boxes = normalize_box_drafts(
                data.get('boxes'), state.get('plan_items') or session_data.get('items'),
                allow_incomplete=True,
            )
            current_boxes = state.get('boxes') if isinstance(state.get('boxes'), list) else []
            current_by_id = {
                ss_fba_schema._fba_trim(box.get('local_id'), 40).upper(): box
                for box in current_boxes if isinstance(box, dict) and ss_fba_schema._fba_trim(box.get('local_id'), 40)
            }
            merged_boxes = []
            merged_ids = set()
            for incoming in incoming_boxes:
                box_id = ss_fba_schema._fba_trim(incoming.get('local_id'), 40).upper()
                current = current_by_id.get(box_id)
                if current is not None:
                    incoming['contents'] = current.get('contents') if isinstance(current.get('contents'), list) else []
                merged_boxes.append(incoming)
                merged_ids.add(box_id)
            # A stale browser may not know about cartons created by another scanner.
            # Retain those cartons; removal is an explicit, atomic action below.
            merged_boxes.extend(
                box for box_id, box in current_by_id.items() if box_id not in merged_ids
            )
            state['boxes'] = merged_boxes
            state['stage'] = 'packing'
            _fba_save_amazon_state(conn, session_id, state)
            conn.commit()
            return jsonify({'success': True, 'amazon_workflow': state, 'local_only': True})

        if action == 'create-box':
            if not state.get('packing_confirmed') or state.get('boxes_submitted'):
                raise FbaInboundValidationError('Cartons can only be added during physical packing')
            group_id = ss_fba_schema._fba_trim(data.get('packing_group_id'), 38)
            valid_groups = {
                ss_fba_schema._fba_trim(group.get('packing_group_id'), 38)
                for group in state.get('packing_groups') or [] if isinstance(group, dict)
            }
            if not group_id or group_id not in valid_groups:
                raise FbaInboundValidationError('Choose a current Amazon packing group for the carton')
            boxes = state.get('boxes') if isinstance(state.get('boxes'), list) else []
            used_box_ids = {
                ss_fba_schema._fba_trim(box.get('local_id'), 40).upper()
                for box in boxes if isinstance(box, dict)
            }
            next_number = 1
            while f'BOX-{next_number:02d}' in used_box_ids:
                next_number += 1
            box_id = f'BOX-{next_number:02d}'
            defaults = data.get('defaults') if isinstance(data.get('defaults'), dict) else {}
            created_box = normalize_box_drafts([{
                'local_id': box_id,
                'packing_group_id': group_id,
                'length_in': defaults.get('length_in'),
                'width_in': defaults.get('width_in'),
                'height_in': defaults.get('height_in'),
                'weight_lb': defaults.get('weight_lb'),
                'contents': [],
            }], state.get('plan_items') or session_data.get('items'), allow_incomplete=True)[0]
            boxes.append(created_box)
            state['boxes'] = boxes
            state['stage'] = 'packing'
            _fba_save_amazon_state(conn, session_id, state)
            conn.commit()
            return jsonify({
                'success': True, 'amazon_workflow': state, 'local_only': True,
                'created_box_id': box_id,
            })

        if action == 'remove-box':
            box_id = ss_fba_schema._fba_trim(data.get('box_id') or data.get('local_id'), 40).upper()
            boxes = state.get('boxes') if isinstance(state.get('boxes'), list) else []
            selected = next((
                box for box in boxes if isinstance(box, dict)
                and ss_fba_schema._fba_trim(box.get('local_id'), 40).upper() == box_id
            ), None)
            if selected is None:
                raise FbaInboundValidationError('That carton no longer exists')
            if selected.get('contents'):
                raise FbaInboundValidationError(f'{box_id} contains physical scans and cannot be removed')
            state['boxes'] = [box for box in boxes if box is not selected]
            state['stage'] = 'packing'
            _fba_save_amazon_state(conn, session_id, state)
            conn.commit()
            return jsonify({
                'success': True, 'amazon_workflow': state, 'local_only': True,
                'removed_box_id': box_id,
            })

        api, marketplace_id = _fba_amazon_client()
        response_payload = {}
        operation_kind = action.replace('-', '_')
        next_stage = state.get('stage') or 'draft'
        success_flag = ''

        if action == 'create-plan':
            if plan_id:
                raise FbaInboundValidationError('This session already has an Amazon inbound plan')
            if data.get('confirm') is not True:
                raise FbaInboundValidationError('Review the item quantities and confirm plan creation')
            enablement = ss_fba_readiness._fba_enablement_progress(session_data.get('items') or [])
            if not enablement.get('all_ready'):
                needs = enablement.get('needs_enablement', 0)
                safety = enablement.get('needs_safety_info', 0)
                enabling = enablement.get('enabling', 0) + enablement.get('checking', 0)
                failed = enablement.get('failed', 0)
                parts = []
                if needs:
                    parts.append(f'{needs} need FBA enablement')
                if safety:
                    parts.append(f'{safety} need battery and dangerous-goods answers')
                if enabling:
                    parts.append(f'{enabling} are still activating with Amazon')
                if failed:
                    parts.append(f'{failed} need listing attention')
                raise FbaInboundValidationError(
                    'Amazon plan creation is waiting: ' + (', '.join(parts) or 'check every Seller SKU for FBA readiness')
                )
            source = _fba_amazon_source_from_settings()
            request_body = build_create_plan_request(session_data, source, marketplace_id)
            mskus = [row.get('msku') for row in request_body.get('items') or [] if row.get('msku')]
            try:
                prep_payload = _fba_amazon_payload(api.list_prep_details(
                    marketplaceId=marketplace_id, mskus=mskus
                ))
            except Exception as prep_error:
                unavailable_mskus = ss_fba_readiness._fba_inbound_unavailable_skus(prep_error)
                if not unavailable_mskus:
                    raise
                raise FbaInboundValidationError(ss_fba_readiness._fba_inbound_activation_message(
                    unavailable_mskus, session_data.get('items') or []
                ))
            prep_by_msku = {
                ss_fba_schema._fba_trim(row.get('msku'), 255).casefold(): row
                for row in prep_payload.get('mskuPrepDetails') or [] if isinstance(row, dict)
            }
            no_prep_mskus = {
                ss_fba_schema._fba_trim(row.get('seller_sku') or row.get('msku'), 255).casefold()
                for row in session_data.get('items') or [] if isinstance(row, dict)
                and ss_fba_schema._fba_trim(row.get('prep_type') or 'none', 40).lower() == 'none'
            }
            missing_prep = ss_fba_readiness._fba_missing_prep_mskus(mskus, prep_by_msku)
            unsupported_missing = [msku for msku in missing_prep if msku.casefold() not in no_prep_mskus]
            if unsupported_missing:
                raise FbaInboundValidationError(
                    'Choose an Amazon prep category for: ' + ', '.join(unsupported_missing[:8])
                )
            prep_corrections = []
            if missing_prep:
                prep_response = _fba_amazon_payload(api.set_prep_details(
                    marketplaceId=marketplace_id,
                    mskuPrepDetails=[{
                        'msku': msku, 'prepCategory': 'NONE', 'prepTypes': ['ITEM_NO_PREP']
                    } for msku in missing_prep],
                ))
                prep_operation_id = ss_fba_schema._fba_trim(prep_response.get('operationId'), 38)
                for _attempt in range(20):
                    if not prep_operation_id:
                        break
                    prep_operation = _fba_amazon_payload(api.get_inbound_operation_status(prep_operation_id))
                    prep_status = ss_fba_schema._fba_trim(prep_operation.get('operationStatus'), 30).upper()
                    if prep_status == 'SUCCESS':
                        break
                    if prep_status == 'FAILED':
                        problems = prep_operation.get('operationProblems') or []
                        message = next((p.get('message') for p in problems if isinstance(p, dict) and p.get('message')), '')
                        raise FbaInboundValidationError(message or 'Amazon could not save the item prep category')
                    time.sleep(0.5)
                else:
                    raise FbaInboundValidationError('Amazon is still saving the item prep category; try Create Amazon plan again')
                prep_corrections = [{'msku': msku, 'prepCategory': 'NONE'} for msku in missing_prep]
            owner_corrections = []
            for attempt in range(3):
                try:
                    response_payload = _fba_amazon_payload(api.create_inbound_plan(**request_body))
                    break
                except Exception as create_error:
                    unavailable_mskus = ss_fba_readiness._fba_inbound_unavailable_skus(create_error)
                    if unavailable_mskus:
                        raise FbaInboundValidationError(ss_fba_readiness._fba_inbound_activation_message(
                            unavailable_mskus, session_data.get('items') or []
                        ))
                    corrections = apply_owner_corrections_from_amazon_error(request_body, create_error)
                    if not corrections or attempt >= 2:
                        raise
                    owner_corrections.extend(corrections)
                    ss_config.logger.warning(
                        'Retrying FBA plan after Amazon owner correction: %s',
                        ', '.join(
                            f"{row['msku']} {row['field']}={row['to']}"
                            for row in corrections
                        ),
                    )
            plan_id = ss_fba_schema._fba_trim(response_payload.get('inboundPlanId'), 38)
            if not plan_id:
                raise FbaInboundValidationError('Amazon did not return an inbound plan ID')
            state.update({
                'inbound_plan_id': plan_id,
                'stage': 'plan_creating',
                'create_request_summary': {
                    'name': request_body.get('name'),
                    'sku_count': len(request_body.get('items') or []),
                    'unit_count': sum(int(row.get('quantity') or 0) for row in request_body.get('items') or []),
                    'owner_corrections': owner_corrections,
                    'prep_corrections': prep_corrections,
                },
            })
            next_stage = 'plan_created'
        else:
            if not plan_id:
                raise FbaInboundValidationError('Create the Amazon inbound plan first')
            state = _fba_amazon_refresh_operation(api, state)
            pending = state.get('operation') if isinstance(state.get('operation'), dict) else {}
            if str(pending.get('status') or '').upper() == 'FAILED':
                raise FbaInboundValidationError(
                    state.get('last_error') or 'The previous Amazon operation failed; refresh before continuing'
                )
            if str((state.get('plan') or {}).get('status') or '').upper() == 'ERRORED':
                raise FbaInboundValidationError(
                    state.get('last_error') or 'Amazon marked this inbound plan as errored; it cannot generate packing options'
                )
            if str(pending.get('status') or '').upper() not in ('', 'SUCCESS', 'FAILED'):
                raise FbaInboundValidationError('Wait for the current Amazon operation to finish')

            if action == 'generate-packing':
                response_payload = _fba_amazon_payload(api.generate_packing_options(plan_id))
                state['stage'] = 'packing_generating'
                next_stage = 'packing_options'
            elif action == 'confirm-packing':
                option_id = ss_fba_schema._fba_trim(data.get('packing_option_id'), 38)
                if data.get('confirm') is not True or not option_id:
                    raise FbaInboundValidationError('Select and confirm a packing option')
                if option_id not in {row.get('packingOptionId') for row in state.get('packing_options') or []}:
                    raise FbaInboundValidationError('That packing option is no longer available')
                response_payload = _fba_amazon_payload(api.confirm_packing_option(plan_id, option_id))
                state['selected_packing_option_id'] = option_id
                state['stage'] = 'packing_confirming'
                next_stage = 'packing'
                success_flag = 'packing_confirmed'
            elif action == 'submit-boxes':
                boxes = data.get('boxes') if isinstance(data.get('boxes'), list) else state.get('boxes')
                request_body, boxes = build_set_packing_request(
                    boxes, state.get('plan_items') or session_data.get('items'), marketplace_id=marketplace_id
                )
                response_payload = _fba_amazon_payload(api.set_packing_information(plan_id, **request_body))
                state['boxes'] = boxes
                state['stage'] = 'boxes_submitting'
                next_stage = 'boxes_submitted'
                success_flag = 'boxes_submitted'
            elif action == 'generate-placement':
                if not state.get('boxes_submitted'):
                    raise FbaInboundValidationError('Submit complete box contents to Amazon first')
                response_payload = _fba_amazon_payload(api.generate_placement_options(plan_id))
                state['stage'] = 'placement_generating'
                next_stage = 'placement_options'
            elif action == 'confirm-placement':
                option_id = ss_fba_schema._fba_trim(data.get('placement_option_id'), 38)
                if ss_fba_schema._fba_trim(data.get('confirmation'), 40).upper() != 'CONFIRM SPLIT':
                    raise FbaInboundValidationError('Type CONFIRM SPLIT to accept the irreversible destination split')
                if option_id not in {row.get('placementOptionId') for row in state.get('placement_options') or []}:
                    raise FbaInboundValidationError('That placement option is no longer available')
                response_payload = _fba_amazon_payload(api.confirm_placement_option(plan_id, option_id))
                state['selected_placement_option_id'] = option_id
                state['stage'] = 'placement_confirming'
                next_stage = 'placement_confirmed'
                success_flag = 'placement_confirmed'
            elif action == 'generate-transportation':
                if not state.get('placement_confirmed'):
                    raise FbaInboundValidationError('Confirm a placement option first')
                placement_id = ss_fba_schema._fba_trim(state.get('selected_placement_option_id'), 38)
                selected = next((row for row in state.get('placement_options') or [] if row.get('placementOptionId') == placement_id), None)
                shipment_ids = (selected or {}).get('shipmentIds') or []
                ready_date = ss_fba_schema._fba_trim(data.get('ready_date'), 10)
                request_body = build_transportation_request(
                    placement_id, shipment_ids, ready_date, _fba_amazon_source_from_settings()
                )
                response_payload = _fba_amazon_payload(api.generate_transportation_options(plan_id, **request_body))
                state['ready_date'] = ready_date
                state['stage'] = 'transport_generating'
                next_stage = 'transport_options'
            elif action == 'confirm-transportation':
                if ss_fba_schema._fba_trim(data.get('confirmation'), 40).upper() != 'BUY SHIPPING':
                    raise FbaInboundValidationError('Type BUY SHIPPING to accept the carrier charges')
                selections = data.get('selections') if isinstance(data.get('selections'), list) else []
                available = {
                    (row.get('shipmentId'), row.get('transportationOptionId'))
                    for row in state.get('transportation_options') or []
                    if row.get('can_purchase') or _fba_transport_option_can_purchase(row)
                }
                chosen = []
                seen_shipments = set()
                source = _fba_amazon_source_from_settings()
                contact = {
                    'name': source.get('name'),
                    'phoneNumber': source.get('phoneNumber'),
                    'email': source.get('email'),
                }
                for selection in selections:
                    shipment_id = ss_fba_schema._fba_trim((selection or {}).get('shipment_id') or (selection or {}).get('shipmentId'), 38)
                    option_id = ss_fba_schema._fba_trim((selection or {}).get('transportation_option_id') or (selection or {}).get('transportationOptionId'), 38)
                    if not shipment_id or (shipment_id, option_id) not in available:
                        raise FbaInboundValidationError('One selected carrier option is no longer available')
                    if shipment_id in seen_shipments:
                        raise FbaInboundValidationError('Select exactly one carrier option per shipment')
                    seen_shipments.add(shipment_id)
                    chosen.append({
                        'shipmentId': shipment_id,
                        'transportationOptionId': option_id,
                        'contactInformation': contact,
                    })
                expected_shipments = {
                    row.get('shipmentId') for row in state.get('transportation_options') or [] if row.get('shipmentId')
                }
                if not chosen or seen_shipments != expected_shipments:
                    raise FbaInboundValidationError('Select one carrier option for every Amazon shipment')
                response_payload = _fba_amazon_payload(
                    api.confirm_transportation_options(plan_id, transportationSelections=chosen)
                )
                state['selected_transportation'] = chosen
                state['stage'] = 'transport_confirming'
                next_stage = 'transport_confirmed'
                success_flag = 'transport_confirmed'

        operation_id = ss_fba_schema._fba_trim(response_payload.get('operationId'), 38)
        if operation_id:
            state['operation'] = {
                'id': operation_id,
                'kind': operation_kind,
                'status': 'IN_PROGRESS',
                'next_stage': next_stage,
                'success_flag': success_flag,
                'started_at': ss_listing_checks._listagent_now_iso(),
            }
        else:
            state['stage'] = next_stage
            if success_flag:
                state[success_flag] = True
        _fba_save_amazon_state(conn, session_id, state, preserve_concurrent_pack=True)
        conn.commit()
        return jsonify({
            'success': True,
            'amazon_workflow': state,
            'operation_id': operation_id,
            'amazon_response': response_payload,
        })
    except FbaInboundValidationError as exc:
        if conn is not None:
            conn.rollback()
        return jsonify({'success': False, 'error': str(exc)}), 400
    except Exception as exc:
        if conn is not None:
            conn.rollback()
        return jsonify({'success': False, 'error': ss_errors._safe_error(exc, 'fba amazon action')}), 502
    finally:
        if conn is not None:
            conn.close()


def api_fba_prep_amazon_box_labels(session_id, shipment_id):
    try:
        with sqlite3.connect(str(ss_config.BASE_DIR / 'searchRack.db'), timeout=30.0) as conn:
            conn.row_factory = sqlite3.Row
            _row, _session, state = _fba_amazon_session(conn, session_id)
        shipment = next(
            (row for row in state.get('shipments') or [] if str(row.get('shipmentId') or '') == shipment_id), None
        )
        if not shipment or not state.get('transport_confirmed'):
            return jsonify({'success': False, 'error': 'Carrier confirmation is required before printing box labels'}), 409
        confirmation_id = ss_fba_schema._fba_trim(shipment.get('shipmentConfirmationId'), 40)
        if not confirmation_id:
            return jsonify({'success': False, 'error': 'Amazon has not assigned the printable shipment ID yet'}), 409
        api, _marketplace_id = _fba_amazon_client(legacy=True)
        response = _fba_amazon_payload(api.get_labels(
            confirmation_id,
            PageType='PackageLabel_Thermal',
            LabelType='UNIQUE',
        ))
        download_url = ss_fba_schema._fba_trim(response.get('DownloadURL') or response.get('downloadURL'), 2000)
        if not download_url:
            return jsonify({'success': False, 'error': 'Amazon did not return a box-label document'}), 502
        return redirect(download_url)
    except Exception as exc:
        return jsonify({'success': False, 'error': ss_errors._safe_error(exc, 'fba amazon labels')}), 502
