"""Fba shipments for Sweet Shelves."""

from . import amazon_listing as ss_amazon_listing
from fba_inbound import reduce_missing_plan_quantity
from fba_inbound import remap_recovery_boxes

import json
import math
import re
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
    seen_tokens = set()
    while len(rows) < limit:
        kwargs = {'pageSize': page_size}
        if token:
            kwargs['paginationToken'] = token
        response = callable_fn(**kwargs)
        payload = _fba_amazon_payload(response)
        page = payload.get(result_key) if isinstance(payload.get(result_key), list) else []
        rows.extend(page)
        pagination = payload.get('pagination') if isinstance(payload.get('pagination'), dict) else {}
        # python-amazon-sp-api extracts pagination from the payload onto the response.
        response_pagination = getattr(response, 'pagination', None)
        response_pagination = response_pagination if isinstance(response_pagination, dict) else {}
        token = (pagination.get('nextToken') or pagination.get('next_token')
                 or response_pagination.get('nextToken') or response_pagination.get('next_token')
                 or getattr(response, 'next_token', None))
        if not token:
            break
        if token in seen_tokens:
            raise FbaInboundValidationError('Amazon repeated a pagination token; refresh to load the complete item list')
        seen_tokens.add(token)
        if len(rows) >= limit:
            raise FbaInboundValidationError('Amazon returned more results than the supported limit; the incomplete list was not saved')
    if len(rows) > limit:
        raise FbaInboundValidationError('Amazon returned more results than the supported limit; the incomplete list was not saved')
    return rows[:limit]


def _fba_plan_approval_issues(state):
    operation = (state or {}).get('operation') or {}
    if operation.get('kind') != 'create_plan' or operation.get('status') != 'FAILED':
        return {}
    issues = {}
    for problem in operation.get('problems') or []:
        if problem.get('code') != 'FBA_INB_0021':
            continue
        match = re.search(r"resource\s+['\"]([^'\"]+)['\"]", str(problem.get('details') or ''), re.I)
        if match:
            issues[match.group(1).strip().casefold()] = str(problem.get('message') or 'Amazon approval is required before this item can be sent.')
    return issues


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


# Amazon workflow order. A failed operation blocks only the steps that depend on it.
_FBA_ACTION_ORDER = (
    'create_plan', 'generate_packing', 'confirm_packing', 'submit_boxes',
    'generate_placement', 'confirm_placement', 'generate_transportation',
    'confirm_transportation',
)


def _fba_action_repeats_or_precedes(action_kind, failed_kind):
    """True when action_kind is the failed step itself or an earlier one."""
    if action_kind == failed_kind:
        return True
    if action_kind not in _FBA_ACTION_ORDER or failed_kind not in _FBA_ACTION_ORDER:
        return False
    return _FBA_ACTION_ORDER.index(action_kind) < _FBA_ACTION_ORDER.index(failed_kind)


# Amazon states a carton minimum only in the warning it raises about that carton.
_FBA_BOX_ID_PATTERN = re.compile(r'\[boxId:\s*([^\]]+)\]', re.I)
_FBA_BOX_MIN_WEIGHT_PATTERN = re.compile(r'expected minimum\s+([0-9]+(?:\.[0-9]+)?)\s*lb', re.I)
_FBA_BOX_MIN_VOLUME_PATTERN = re.compile(r'expected minimum cubic inch\s+([0-9]+(?:\.[0-9]+)?)', re.I)


def _fba_box_minimums_from_warnings(problems, box_names):
    """Pull the weight and cubic-inch minimums Amazon quotes, keyed by local carton."""
    minimums = {}
    for problem in problems or []:
        if not isinstance(problem, dict):
            continue
        message = str(problem.get('message') or problem.get('details') or '')
        named = _FBA_BOX_ID_PATTERN.search(message)
        if not named:
            continue
        template = named.group(1).strip()
        entry = minimums.setdefault((box_names or {}).get(template) or template, {})
        volume = _FBA_BOX_MIN_VOLUME_PATTERN.search(message)
        if volume:
            entry['volume_in3'] = float(volume.group(1))
            continue
        weight = _FBA_BOX_MIN_WEIGHT_PATTERN.search(message)
        if weight:
            entry['weight_lb'] = float(weight.group(1))
    return minimums


def _fba_local_box_signature(box):
    """Measurements plus unit count, used to line a carton up with Amazon's copy."""
    try:
        dims = tuple(round(float(box.get(field) or 0), 2) for field in ('length_in', 'width_in', 'height_in'))
        weight = round(float(box.get('weight_lb') or 0), 2)
    except (TypeError, ValueError):
        return None
    if not all(dims) or not weight:
        return None
    units = sum(int(item.get('quantity') or 0) for item in (box.get('contents') or []) if isinstance(item, dict))
    return dims + (weight, units)


def _fba_amazon_box_signature(box):
    dimensions = box.get('dimensions') if isinstance(box.get('dimensions'), dict) else {}
    weight = box.get('weight') if isinstance(box.get('weight'), dict) else {}
    try:
        dims = tuple(round(float(dimensions.get(field) or 0), 2) for field in ('length', 'width', 'height'))
        pounds = round(float(weight.get('value') or 0), 2)
    except (TypeError, ValueError):
        return None
    if not all(dims) or not pounds:
        return None
    units = sum(int(item.get('quantity') or 0) for item in (box.get('items') or []) if isinstance(item, dict))
    return dims + (pounds, units)


def _fba_amazon_box_name_map(api, plan_id, local_boxes):
    """Amazon names cartons 'P1 - B6'; map those onto the operator's own carton ids.

    Matching is by measurements and unit count, so an ambiguous carton is left
    unmapped rather than guessed at.
    """
    amazon_boxes = _fba_amazon_collect(
        lambda **kwargs: api.list_inbound_plan_boxes(plan_id, **kwargs), 'boxes', page_size=100, limit=2000
    )
    local_by_signature = {}
    for box in local_boxes or []:
        if not isinstance(box, dict):
            continue
        signature = _fba_local_box_signature(box)
        local_id = ss_fba_schema._fba_trim(box.get('local_id'), 40)
        if signature and local_id:
            local_by_signature.setdefault(signature, []).append(local_id)
    names = {}
    for box in amazon_boxes:
        template = ss_fba_schema._fba_trim(box.get('templateName'), 60)
        signature = _fba_amazon_box_signature(box)
        matches = local_by_signature.get(signature) or []
        if template and len(matches) == 1:
            names[template] = matches[0]
    return names


_FBA_PLAN_HEALTH_DIAGNOSED_KINDS = (
    'submit_boxes', 'generate_placement', 'confirm_placement',
    'generate_transportation', 'confirm_transportation',
)
_FBA_SCAN_LISTING_CACHE = {}
_FBA_SCAN_LISTING_CACHE_TTL = 600


def _fba_plan_health_message(code, msku, *, title='', barcode='', box_ids=None):
    label = msku + (f' ({title})' if title else '') + (f', barcode {barcode}' if barcode else '')
    packed = ''
    if box_ids:
        packed = ' It is packed in ' + ', '.join(box_ids) + '.'
    if code == 'listing_missing':
        return (
            f'Amazon no longer has a listing for Seller SKU {label}. Its inbound service answers '
            '"The following MSKUs are not valid", which is why the placement step fails with a bare '
            f'InternalServerError.{packed} Restore the listing in Seller Central with exactly this Seller '
            'SKU, or remove the item from the plan and rebuild.'
        )
    if code == 'inbound_unavailable':
        return (
            f'Amazon says Seller SKU {label} is not available for inbound.{packed} If its FBA offer was '
            'created within the last hour, wait and retry; otherwise the offer was removed and the item '
            'must be restored or taken out of the plan.'
        )
    if code == 'merchant_fulfilled':
        return (
            f'Seller SKU {label} is still merchant-fulfilled on Amazon (no FBA offer).{packed} Amazon '
            'accepted it in this plan, but switch it to Fulfilled by Amazon in Seller Central before '
            'the cartons ship.'
        )
    return f'Amazon reported a problem with Seller SKU {label}.'


def _fba_plan_health(api, marketplace_id, plan_items, *, session_items=None, boxes=None, listings=None):
    """Ask Amazon whether every MSKU in the plan still exists as an inbound-able FBA offer.

    Amazon's placement engine answers a plan that contains a deleted or
    de-listed MSKU with a bare InternalServerError. This check is what turns
    that into an exact message naming the Seller SKU and the carton it is in.
    """
    mskus = []
    plan_by_msku = {}
    for row in plan_items if isinstance(plan_items, list) else []:
        if not isinstance(row, dict):
            continue
        msku = ss_fba_schema._fba_trim(row.get('msku') or row.get('seller_sku'), 255)
        if msku and msku.casefold() not in plan_by_msku:
            mskus.append(msku)
            plan_by_msku[msku.casefold()] = row
    session_by_msku = {
        ss_fba_schema._fba_trim(item.get('seller_sku') or item.get('msku'), 255).casefold(): item
        for item in session_items if isinstance(session_items, list) and isinstance(item, dict)
    } if session_items else {}
    boxes_by_msku = {}
    for box in boxes if isinstance(boxes, list) else []:
        if not isinstance(box, dict):
            continue
        local_id = ss_fba_schema._fba_trim(box.get('local_id'), 40)
        for content in box.get('contents') if isinstance(box.get('contents'), list) else []:
            if isinstance(content, dict) and int(content.get('quantity') or 0) > 0:
                key = ss_fba_schema._fba_trim(content.get('msku'), 255).casefold()
                if key and local_id and local_id not in boxes_by_msku.setdefault(key, []):
                    boxes_by_msku[key].append(local_id)

    findings = {}

    def add(msku, code, severity):
        key = msku.casefold()
        if key in findings:
            return
        plan_row = plan_by_msku.get(key) or {}
        item = session_by_msku.get(key) or {}
        title = ss_fba_schema._fba_trim(item.get('title'), 120)
        barcode = ss_fba_schema._fba_trim(item.get('barcode_display') or item.get('barcode'), 160)
        box_ids = boxes_by_msku.get(key) or []
        findings[key] = {
            'msku': msku, 'code': code, 'severity': severity,
            'asin': ss_fba_schema._fba_trim(plan_row.get('asin'), 30),
            'fnsku': ss_fba_schema._fba_trim(plan_row.get('fnsku'), 80),
            'quantity': int(plan_row.get('quantity') or 0),
            'title': title, 'barcode': barcode, 'box_ids': box_ids,
            'message': _fba_plan_health_message(code, msku, title=title, barcode=barcode, box_ids=box_ids),
        }

    # Amazon rejects a whole prep-details batch when one MSKU is bad and names
    # the culprits, so keep re-asking about the remainder until a batch passes.
    for offset in range(0, len(mskus), 100):
        chunk = list(mskus[offset:offset + 100])
        for _attempt in range(8):
            if not chunk:
                break
            try:
                _fba_amazon_payload(api.list_prep_details(marketplaceId=marketplace_id, mskus=chunk))
                break
            except Exception as exc:
                kind, named = ss_fba_readiness._fba_prep_error_mskus(exc)
                chunk_keys = {value.casefold() for value in chunk}
                bad_keys = {value.casefold() for value in named} & chunk_keys
                if not bad_keys:
                    ss_config.logger.warning('fba plan health: prep check skipped: %s', exc)
                    break
                code = 'listing_missing' if kind == 'invalid' else 'inbound_unavailable'
                for msku in chunk:
                    if msku.casefold() in bad_keys:
                        add(msku, code, 'blocking')
                chunk = [value for value in chunk if value.casefold() not in bad_keys]

    try:
        client, seller_id, listing_marketplace = listings or ss_fba_readiness._fba_listings_client()
        for offset in range(0, len(mskus), 20):
            chunk = list(mskus[offset:offset + 20])
            payload = _fba_amazon_payload(client.search_listings_items(
                seller_id, marketplaceIds=[listing_marketplace or marketplace_id],
                identifiers=','.join(chunk), identifiersType='SKU',
                includedData=['summaries', 'fulfillmentAvailability'], pageSize=20,
            ))
            found = {}
            for row in payload.get('items') if isinstance(payload.get('items'), list) else []:
                if isinstance(row, dict) and ss_fba_schema._fba_trim(row.get('sku'), 255):
                    found[ss_fba_schema._fba_trim(row.get('sku'), 255).casefold()] = row
            for msku in chunk:
                row = found.get(msku.casefold())
                if row is None:
                    add(msku, 'listing_missing', 'blocking')
                    continue
                channels = {
                    ss_fba_schema._fba_trim(
                        entry.get('fulfillmentChannelCode') or entry.get('fulfillment_channel_code'), 40
                    ).upper()
                    for entry in row.get('fulfillmentAvailability') or [] if isinstance(entry, dict)
                }
                channels.discard('')
                if channels and 'AMAZON_NA' not in channels:
                    add(msku, 'merchant_fulfilled', 'warning')
    except Exception as exc:
        ss_config.logger.warning('fba plan health: listing check skipped: %s', exc)

    rows = list(findings.values())
    rows.sort(key=lambda row: (0 if row['severity'] == 'blocking' else 1, row['msku']))
    blocking = [row for row in rows if row['severity'] == 'blocking']
    summary = ''
    if blocking:
        summary = (
            f'Plan check found {len(blocking)} item{"" if len(blocking) == 1 else "s"} Amazon can no '
            'longer ship: ' + ' '.join(row['message'] for row in blocking[:3])
        )
        if len(blocking) > 3:
            summary += f' ({len(blocking) - 3} more listed on the page.)'
    return {
        'checked_at': ss_listing_checks._listagent_now_iso(),
        'checked_count': len(mskus),
        'findings': rows,
        'blocking_count': len(blocking),
        'summary': summary,
    }


def _fba_diagnose_failed_operation(api, state):
    """Explain a generic Amazon failure once per operation by checking the plan items."""
    operation = state.get('operation') if isinstance(state.get('operation'), dict) else {}
    kind = ss_fba_schema._fba_trim(operation.get('kind'), 80)
    operation_id = ss_fba_schema._fba_trim(operation.get('id'), 38)
    if str(operation.get('status') or '').upper() != 'FAILED' or kind not in _FBA_PLAN_HEALTH_DIAGNOSED_KINDS:
        return state
    health = state.get('plan_health') if isinstance(state.get('plan_health'), dict) else {}
    if operation_id and health.get('operation_id') == operation_id:
        return state
    marketplace_ids = (state.get('plan') or {}).get('marketplaceIds') if isinstance(state.get('plan'), dict) else None
    marketplace_id = ss_fba_schema._fba_trim((marketplace_ids or [''])[0], 20) if isinstance(marketplace_ids, list) else ''
    if not marketplace_id:
        try:
            marketplace_id = ss_amazon_catalog._amazon_spapi_context()[2]
        except Exception:
            return state
    try:
        health = _fba_plan_health(
            api, marketplace_id, state.get('plan_items') or [], boxes=state.get('boxes') or []
        )
    except Exception:
        ss_config.logger.warning('fba plan health check failed', exc_info=True)
        return state
    health['operation_id'] = operation_id
    health['trigger'] = kind
    state['plan_health'] = health
    if health.get('blocking_count'):
        state['last_error'] = health['summary']
    return state


def _fba_scan_listing_problem(msku, *, api=None, marketplace_id=''):
    """Return an exact stop message when Amazon no longer accepts this MSKU for inbound.

    Checked at scan time so a deleted listing is caught while the unit is in
    hand, not after the cartons are sealed. Amazon outages never block a scan:
    only a definite rejection naming this MSKU does.
    """
    seller_sku = ss_fba_schema._fba_trim(msku, 255)
    key = seller_sku.casefold()
    if not key:
        return ''
    now = time.time()
    cached = _FBA_SCAN_LISTING_CACHE.get(key)
    if cached and now - cached[0] < _FBA_SCAN_LISTING_CACHE_TTL:
        return cached[1]
    message = ''
    try:
        if api is None or not marketplace_id:
            api, marketplace_id = _fba_amazon_client()
        _fba_amazon_payload(api.list_prep_details(marketplaceId=marketplace_id, mskus=[seller_sku]))
    except Exception as exc:
        kind, named = ss_fba_readiness._fba_prep_error_mskus(exc)
        if not any(value.casefold() == key for value in named):
            ss_config.logger.warning('fba scan listing check skipped for %s: %s', seller_sku, exc)
            return ''
        if kind == 'invalid':
            message = (
                f'STOP: Amazon no longer recognizes Seller SKU {seller_sku}. Its listing was removed or '
                'never finished activating (Amazon: "The following MSKUs are not valid"). Do not pack '
                'this unit. Restore the listing in Seller Central with exactly this Seller SKU, or remove '
                'the item from the plan and rebuild.'
            )
        elif kind == 'unavailable':
            message = (
                f'STOP: Amazon says Seller SKU {seller_sku} is not available for inbound right now. Do not '
                'pack it until Amazon accepts it, usually within an hour of enabling FBA.'
            )
    _FBA_SCAN_LISTING_CACHE[key] = (now, message)
    return message


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
    # Amazon reports non-fatal box problems as WARNING; keep the newest set for the
    # step that raised them so a later operation does not silently discard them.
    warnings = [
        problem for problem in (operation.get('problems') or [])
        if isinstance(problem, dict)
        and ss_fba_schema._fba_trim(problem.get('severity'), 20).upper() == 'WARNING'
    ]
    kind = ss_fba_schema._fba_trim(operation.get('kind'), 80)
    if warnings:
        box_names = {}
        plan_id = ss_fba_schema._fba_trim(state.get('inbound_plan_id'), 38)
        if kind == 'submit_boxes' and plan_id:
            try:
                box_names = _fba_amazon_box_name_map(api, plan_id, state.get('boxes') or [])
            except Exception:  # A naming aid must never break the sync.
                ss_config.logger.warning('fba box name map failed', exc_info=True)
        state['operation_warnings'] = {
            'kind': kind, 'problems': warnings, 'box_names': box_names,
            'box_minimums': _fba_box_minimums_from_warnings(warnings, box_names),
            'checked_at': operation.get('checked_at'),
        }
    elif (state.get('operation_warnings') or {}).get('kind') == kind:
        state.pop('operation_warnings', None)
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
    state = _fba_diagnose_failed_operation(api, state)

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
    # Amazon is one workflow: when an API operation will not go through, the operator
    # can finish a step in Seller Central. Adopt whatever Amazon reports as already done.
    if not state.get('transport_confirmed') and accepted_placement:
        booked = [
            row for row in shipments
            if row.get('placementOptionId') == accepted_placement.get('placementOptionId')
        ] or shipments
        if booked and all(row.get('selectedTransportationOptionId') for row in booked):
            state['transport_confirmed'] = True
            state['stage'] = 'transport_confirmed'
    if state.get('transport_confirmed'):
        for shipment in shipments:
            amazon_boxes = _fba_amazon_collect(
                lambda **kwargs: api.list_shipment_boxes(plan_id, shipment['shipmentId'], **kwargs),
                'boxes', page_size=1000, limit=5000,
            )
            shipment['label_boxes'] = _fba_match_label_boxes(amazon_boxes, state.get('boxes') or [])

    shipment_by_id = {row.get('shipmentId'): row for row in shipments if row.get('shipmentId')}
    recovery = state.get('recovery') if isinstance(state.get('recovery'), dict) else {}
    preferred_destinations = {
        ss_fba_schema._fba_trim(value, 40).upper()
        for value in recovery.get('preferred_destinations') or [] if ss_fba_schema._fba_trim(value, 40)
    }
    for option in state.get('placement_options') or []:
        option_destinations = []
        for shipment_id in option.get('shipmentIds') or []:
            warehouse_id = ss_fba_schema._fba_trim(
                ((shipment_by_id.get(shipment_id) or {}).get('destination') or {}).get('warehouseId'), 40
            ).upper()
            if warehouse_id and warehouse_id not in option_destinations:
                option_destinations.append(warehouse_id)
        option['destination_ids'] = option_destinations
        if preferred_destinations:
            destination_set = set(option_destinations)
            option['recovery_destination_match'] = destination_set == preferred_destinations
            option['recovery_destination_overlap'] = len(destination_set & preferred_destinations)
    if preferred_destinations:
        state['placement_options'].sort(key=lambda option: (
            not bool(option.get('recovery_destination_match')),
            -int(option.get('recovery_destination_overlap') or 0),
        ))

    if selected_placement:
        transportation = []
        selected_shipment_ids = selected_placement.get('shipmentIds') or []
        for shipment_id in selected_shipment_ids:
            transportation.extend(_fba_amazon_collect(
                lambda shipment_id=shipment_id, **kwargs: api.list_transportation_options(
                    plan_id, shipmentId=shipment_id, **kwargs
                ), 'transportationOptions', page_size=20, limit=500,
            ))
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
        _row, session_data, state = _fba_amazon_session(conn, session_id)
        if state.get('inbound_plan_id'):
            api, marketplace_id = _fba_amazon_client()
            if (state.get('recovery') or {}).get('active'):
                state, session_data = _fba_amazon_sync_recovery(
                    api, marketplace_id, conn, session_id, session_data, state
                )
            else:
                state = _fba_amazon_sync_snapshot(api, state)
            _fba_save_amazon_state(conn, session_id, state, preserve_concurrent_pack=True)
            conn.commit()
        return jsonify({
            'success': True, 'amazon_workflow': state,
            'items': session_data.get('items') or [],
        })
    except FbaInboundValidationError as exc:
        if conn is not None:
            conn.rollback()
        return jsonify({'success': False, 'error': str(exc)}), 400
    except Exception as exc:
        if conn is not None:
            conn.rollback()
        amazon_detail = _fba_amazon_exception_detail(exc)
        if amazon_detail:
            ss_config.logger.error('fba amazon sync: %s', exc, exc_info=True)
            return jsonify({'success': False, 'error': amazon_detail}), 502
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
        'recover-remove-item', 'retry-recovery', 'package-measurements',
        'package-measurement-status',
        'set-aside-approval-items', 'check-plan',
    }
    if action not in allowed:
        return jsonify({'success': False, 'error': 'Unknown Amazon workflow action'}), 404
    conn = None
    try:
        data = request.get_json(silent=True) or {}
        conn = sqlite3.connect(str(ss_config.BASE_DIR / 'searchRack.db'), timeout=30.0)
        conn.row_factory = sqlite3.Row
        if action == 'recover-remove-item':
            # Packed units may need their carton scans reversed, which restores
            # shelf stock through the warehouse-removal history.
            conn.execute('ATTACH DATABASE ? AS rackhist', (str(ss_config.BASE_DIR / 'rackhistory.db'),))
        if action in ('save-boxes', 'create-box', 'remove-box', 'recover-remove-item', 'retry-recovery', 'set-aside-approval-items'):
            conn.execute('BEGIN IMMEDIATE')
        _row, session_data, state = _fba_amazon_session(conn, session_id)
        plan_id = ss_fba_schema._fba_trim(state.get('inbound_plan_id'), 38)

        if action == 'set-aside-approval-items':
            issues = _fba_plan_approval_issues(state)
            if data.get('confirm') is not True or not issues or state.get('packing_confirmed') or state.get('boxes'):
                raise FbaInboundValidationError('Only approval-blocked items in a failed, unpacked plan can be set aside here')
            if conn.execute('SELECT COUNT(*) FROM fba_pack_scans WHERE session_id=?', (session_id,)).fetchone()[0]:
                raise FbaInboundValidationError('This session has packing scans and cannot be reset here')
            items = session_data.get('items') or []
            removed = [item for item in items if str(item.get('seller_sku') or '').casefold() in issues]
            remaining = [item for item in items if str(item.get('seller_sku') or '').casefold() not in issues]
            if not removed or not remaining:
                raise FbaInboundValidationError('At least one remaining item is needed to continue this shipment')
            history = session_data.get('rejected_items') or []
            for item in removed:
                item = dict(item, fba_set_aside=True, fba_enablement_status='failed',
                            fba_enablement_error=issues[str(item.get('seller_sku') or '').casefold()],
                            inbound_eligible=False,
                            inbound_error=issues[str(item.get('seller_sku') or '').casefold()])
                history = [saved for saved in history if saved.get('seller_sku') != item.get('seller_sku')]
                history.append(item)
            conn.execute('''UPDATE fba_prep_sessions SET items_json=?, rejected_items_json=?,
                item_count=?, total_units=?, count_revision=COALESCE(count_revision,0)+1 WHERE id=?''',
                (json.dumps(remaining, ensure_ascii=False), json.dumps(history, ensure_ascii=False),
                 len(remaining), sum(int(item.get('quantity') or 0) for item in remaining), session_id))
            state = {'stage': 'draft', 'inbound_plan_id': '', 'boxes': [], 'packing_groups': [],
                     'packing_options': [], 'placement_options': [], 'shipments': [], 'transportation_options': []}
            _fba_save_amazon_state(conn, session_id, state)
            conn.commit()
            return jsonify({'success': True, 'amazon_workflow': state, 'items': remaining,
                            'removed_count': len(removed), 'local_only': True})

        if action == 'package-measurement-status':
            measurement_issues = ss_fba_readiness._fba_package_measurement_issues(state)
            if not measurement_issues:
                raise FbaInboundValidationError(
                    'Amazon is not currently requesting package measurements for this plan'
                )
            session_items = session_data.get('items') if isinstance(session_data.get('items'), list) else []
            item_by_msku = {
                ss_fba_schema._fba_trim(item.get('seller_sku') or item.get('msku'), 255).casefold(): item
                for item in session_items if isinstance(item, dict)
                and ss_fba_schema._fba_trim(item.get('seller_sku') or item.get('msku'), 255)
            }
            credentials, _seller_id, _marketplace_id, marketplace = ss_amazon_catalog._amazon_spapi_context()
            if marketplace is None:
                raise FbaInboundValidationError('Amazon SP-API marketplace is not available')
            from sp_api.api import CatalogItems
            catalog = CatalogItems(
                credentials=credentials, marketplace=marketplace, version='2022-04-01'
            )
            references = []
            listings, seller_id, listing_marketplace_id = ss_fba_readiness._fba_listings_client()
            for index, issue in enumerate(measurement_issues[:50]):
                item = item_by_msku.get(issue['msku'].casefold()) or {}
                asin = ss_fba_schema._fba_trim(item.get('asin') or (item.get('fba') or {}).get('amazon_listing', {}).get('asin'), 30)
                reference = {
                    **issue,
                    'title': ss_fba_schema._fba_trim(item.get('title'), 500),
                    'barcode': ss_fba_schema._fba_trim(item.get('barcode_display') or item.get('barcode'), 160),
                    'asin': asin,
                    'item': {}, 'package': {}, 'package_ready': False,
                }
                if asin:
                    try:
                        reference.update(ss_fba_readiness._fba_catalog_measurement_reference(asin, client=catalog))
                    except Exception as catalog_error:
                        reference['error'] = ss_fba_schema._fba_trim(
                            _fba_amazon_exception_detail(catalog_error)
                            or ss_amazon_listing._amazon_format_spapi_error(catalog_error), 500
                        )
                else:
                    reference['error'] = 'Amazon ASIN is missing'
                reference['catalog_ready'] = reference['package_ready']
                try:
                    listing_payload = _fba_amazon_payload(listings.get_listings_item(
                        seller_id, issue['msku'], marketplaceIds=[listing_marketplace_id],
                        includedData=['attributes', 'issues'],
                    ))
                    reference.update(ss_fba_readiness._fba_listing_measurement_status(
                        listing_payload, item.get('amazon_package_measurements') or {},
                        listing_marketplace_id,
                    ))
                except Exception as listing_error:
                    reference['listing_error'] = ss_fba_schema._fba_trim(
                        ss_amazon_listing._amazon_format_spapi_error(listing_error), 500)
                # This authorizes an operator retry, not a claim of FBA acceptance.
                reference['retry_ready'] = bool(reference.get('listing_ready'))
                references.append(reference)
                if index + 1 < len(measurement_issues[:50]):
                    time.sleep(0.3)
            return jsonify({
                'success': True,
                'all_ready': bool(references) and len(references) == len(measurement_issues)
                    and all(row.get('retry_ready') for row in references),
                'references': references,
                'checked_at': ss_listing_checks._listagent_now_iso(),
            })

        if action == 'package-measurements':
            measurement_issues = ss_fba_readiness._fba_package_measurement_issues(state)
            if not measurement_issues:
                raise FbaInboundValidationError(
                    'Amazon is not currently requesting package measurements for this plan'
                )
            session_items = session_data.get('items') if isinstance(session_data.get('items'), list) else []
            item_by_msku = {
                ss_fba_schema._fba_trim(item.get('seller_sku') or item.get('msku'), 255).casefold(): item
                for item in session_items if isinstance(item, dict)
                and ss_fba_schema._fba_trim(item.get('seller_sku') or item.get('msku'), 255)
            }
            requested_rows = data.get('items') if isinstance(data.get('items'), list) else []
            requested_by_msku = {
                ss_fba_schema._fba_trim(row.get('msku') or row.get('seller_sku'), 255).casefold(): row
                for row in requested_rows[:100] if isinstance(row, dict)
                and ss_fba_schema._fba_trim(row.get('msku') or row.get('seller_sku'), 255)
            }
            validated = []
            for issue in measurement_issues:
                key = issue['msku'].casefold()
                item = item_by_msku.get(key)
                incoming = requested_by_msku.get(key)
                if not item or not incoming:
                    raise FbaInboundValidationError(
                        f'Enter package dimensions and weight for {issue["msku"]}'
                    )
                validated.append((issue, item, {
                    'length_in': ss_fba_readiness._fba_positive_measurement(incoming.get('length_in'), 'package length'),
                    'width_in': ss_fba_readiness._fba_positive_measurement(incoming.get('width_in'), 'package width'),
                    'height_in': ss_fba_readiness._fba_positive_measurement(incoming.get('height_in'), 'package height'),
                    'weight_lb': ss_fba_readiness._fba_positive_measurement(incoming.get('weight_lb'), 'package weight'),
                }))

            listing_client = ss_fba_readiness._fba_listings_client()
            saved_measurements = {}
            errors = []
            for issue, item, measurements in validated:
                try:
                    saved_measurements[issue['msku'].casefold()] = (
                        ss_fba_readiness._fba_patch_listing_package_measurements(
                            issue['msku'], item, measurements, client=listing_client,
                        )
                    )
                except Exception as measurement_error:
                    detail = ss_fba_schema._fba_trim(
                        _fba_amazon_exception_detail(measurement_error)
                        or ss_amazon_listing._amazon_format_spapi_error(measurement_error), 700
                    )
                    errors.append({
                        'msku': issue['msku'],
                        'error': detail or 'Amazon did not accept these measurements',
                    })

            if saved_measurements:
                conn.execute('BEGIN IMMEDIATE')
                latest_row = conn.execute(
                    "SELECT items_json FROM fba_prep_sessions WHERE id = ? AND status = 'open'",
                    (session_id,),
                ).fetchone()
                latest_items = [
                    ss_fba_inventory._fba_session_item_payload(item)
                    for item in ss_fba_schema._fba_json_list(latest_row['items_json'] if latest_row else '[]')
                    if isinstance(item, dict)
                ]
                for item in latest_items:
                    key = ss_fba_schema._fba_trim(item.get('seller_sku') or item.get('msku'), 255).casefold()
                    if key in saved_measurements:
                        item['amazon_package_measurements'] = saved_measurements[key]
                conn.execute('''
                    UPDATE fba_prep_sessions
                    SET items_json = ?, count_revision = COALESCE(count_revision, 0) + 1,
                        updated_at = ?
                    WHERE id = ? AND status = 'open'
                ''', (json.dumps(latest_items, ensure_ascii=False), ss_listing_checks._listagent_now_iso(), session_id))
                conn.commit()
                session_items = latest_items

            if errors:
                return jsonify({
                    'success': False,
                    'error': '; '.join(f"{row['msku']}: {row['error']}" for row in errors),
                    'errors': errors,
                    'saved_count': len(saved_measurements),
                    'items': session_items,
                    'amazon_workflow': state,
                }), 400
            return jsonify({
                'success': True,
                'message': (
                    f'Amazon accepted package measurements for {len(saved_measurements)} '
                    f'item{"" if len(saved_measurements) == 1 else "s"}.'
                ),
                'saved_count': len(saved_measurements),
                'items': session_items,
                'amazon_workflow': state,
            })

        if action == 'recover-remove-item':
            if data.get('confirm') is not True:
                raise FbaInboundValidationError('Confirm the missing-item plan rebuild')
            if not plan_id or not state.get('packing_confirmed'):
                raise FbaInboundValidationError('Missing items can be removed during physical carton packing')
            if state.get('placement_confirmed'):
                raise FbaInboundValidationError(
                    'A placement option is already confirmed; Amazon has locked this plan. '
                    'Use Seller Central shipment-content editing'
                )
            if (state.get('recovery') or {}).get('active'):
                raise FbaInboundValidationError('A missing-item recovery is already running')
            pending = state.get('operation') if isinstance(state.get('operation'), dict) else {}
            if ss_fba_schema._fba_trim(pending.get('status'), 30).upper() not in ('', 'SUCCESS', 'FAILED'):
                raise FbaInboundValidationError('Wait for the current Amazon operation to finish')

            msku = ss_fba_schema._fba_trim(data.get('msku') or data.get('seller_sku'), 255)
            requested_quantity = ss_listing_settings._listingagent_parse_int(data.get('quantity'), 0) or 1
            packed_quantities = _fba_packed_quantities(conn, session_id)
            packed_quantity = packed_quantities.get(msku.casefold(), 0)
            reversed_scans = []
            if data.get('include_packed') is True and packed_quantity:
                # A listing Amazon no longer accepts must leave the shipment even
                # when its units are already in a carton: reverse those scans
                # (stock returns to the shelf) before shrinking the plan.
                from . import fba_scanning as ss_fba_scanning
                cur = conn.cursor()
                for _index in range(min(packed_quantity, requested_quantity)):
                    reversed_scans.append(
                        ss_fba_scanning._fba_undo_newest_pack_scan(cur, session_id, state, msku)
                    )
                packed_quantity -= len(reversed_scans)
            updated_items, reduction = reduce_missing_plan_quantity(
                session_data.get('items') or [], msku, requested_quantity,
                packed_quantity=packed_quantity,
                barcode_key=data.get('barcode_key') or '',
            )
            reduction['reversed_scans'] = [
                {key: row.get(key) for key in ('scan_token', 'box_id', 'source_location', 'inventory_restored')}
                for row in reversed_scans
            ]
            if isinstance(state.get('plan_health'), dict):
                state['plan_health']['findings'] = [
                    row for row in state['plan_health'].get('findings') or []
                    if ss_fba_schema._fba_trim((row or {}).get('msku'), 255).casefold() != msku.casefold()
                ]
                state['plan_health']['blocking_count'] = sum(
                    1 for row in state['plan_health']['findings'] if (row or {}).get('severity') == 'blocking'
                )
            if not updated_items:
                raise FbaInboundValidationError('An Amazon plan must contain at least one item')
            fnsku_by_msku = {
                ss_fba_schema._fba_trim(item.get('msku') or item.get('seller_sku'), 255).casefold():
                    ss_fba_schema._fba_trim(item.get('fnsku'), 80).upper()
                for item in (state.get('plan_items') or []) if isinstance(item, dict)
                and ss_fba_schema._fba_trim(item.get('fnsku'), 80)
            }
            for item in session_data.get('items') or []:
                if not isinstance(item, dict):
                    continue
                item_msku = ss_fba_schema._fba_trim(item.get('seller_sku') or item.get('msku'), 255).casefold()
                item_fnsku = ss_fba_schema._fba_trim(item.get('amazon_fnsku') or item.get('fnsku'), 80).upper()
                if item_msku and item_fnsku and item_msku not in fnsku_by_msku:
                    fnsku_by_msku[item_msku] = item_fnsku
            preferred_destinations = []
            for shipment in state.get('shipments') or []:
                destination = shipment.get('destination') if isinstance(shipment, dict) else {}
                warehouse_id = ss_fba_schema._fba_trim((destination or {}).get('warehouseId'), 40)
                if warehouse_id and warehouse_id not in preferred_destinations:
                    preferred_destinations.append(warehouse_id)
            recovery = {
                'active': True,
                'phase': 'canceling',
                'started_at': ss_listing_checks._listagent_now_iso(),
                'old_plan_id': plan_id,
                'old_stage': state.get('stage') or 'packing',
                'removed_item': reduction,
                'updated_items': updated_items,
                'saved_boxes': json.loads(json.dumps(state.get('boxes') or [])),
                'preferred_destinations': preferred_destinations,
                'fnsku_by_msku': fnsku_by_msku,
                'packed_scan_count': int(conn.execute(
                    'SELECT COUNT(*) FROM fba_pack_scans WHERE session_id = ?', (session_id,)
                ).fetchone()[0] or 0),
            }
            api, marketplace_id = _fba_amazon_client()
            response_payload = _fba_amazon_payload(api.cancel_inbound_plan(plan_id))
            state['recovery'] = recovery
            state['stage'] = 'recovery_canceling'
            operation_id = _fba_set_amazon_operation(
                state, response_payload, 'recovery_cancel_plan', 'recovery_rebuilding'
            )
            if not operation_id:
                state['operation'] = {
                    'kind': 'recovery_cancel_plan', 'status': 'SUCCESS',
                    'next_stage': 'recovery_rebuilding', 'started_at': ss_listing_checks._listagent_now_iso(),
                }
                state, session_data = _fba_amazon_sync_recovery(
                    api, marketplace_id, conn, session_id, session_data, state
                )
            _fba_save_amazon_state(conn, session_id, state, preserve_concurrent_pack=True)
            conn.commit()
            if any(row.get('inventory_restored') for row in reversed_scans):
                from . import caching as ss_caching
                ss_caching.update_data_version()
                ss_caching._invalidate_searchrack_cache()
            return jsonify({
                'success': True, 'amazon_workflow': state,
                'operation_id': ss_fba_schema._fba_trim((state.get('operation') or {}).get('id'), 38),
                'recovery': state.get('recovery'), 'items': session_data.get('items') or [],
            })

        if action == 'retry-recovery':
            recovery = state.get('recovery') if isinstance(state.get('recovery'), dict) else {}
            if recovery.get('phase') != 'failed':
                raise FbaInboundValidationError('There is no failed plan recovery to retry')
            api, marketplace_id = _fba_amazon_client()
            recovery['active'] = True
            recovery.pop('error', None)
            if recovery.get('old_plan_cancelled'):
                recovery['phase'] = 'creating_plan'
                preserved = {
                    key: state.get(key) for key in (
                        'inventory_removed_by_msku', 'removed_locations_by_msku',
                        'printed_item_label_counts', 'last_item_label_print', 'last_pack_scan',
                    ) if key in state
                }
                state = {
                    'stage': 'draft', 'inbound_plan_id': '', 'boxes': [],
                    'packing_options': [], 'packing_groups': [], 'placement_options': [],
                    'shipments': [], 'transportation_options': [], 'recovery': recovery,
                    **preserved,
                }
                state, response_payload = _fba_prepare_and_create_plan(
                    api, marketplace_id, session_data, state
                )
                operation_id = _fba_set_amazon_operation(
                    state, response_payload, 'recovery_create_plan', 'plan_created'
                )
            else:
                recovery['phase'] = 'canceling'
                state['recovery'] = recovery
                state['stage'] = 'recovery_canceling'
                response_payload = _fba_amazon_payload(
                    api.cancel_inbound_plan(recovery.get('old_plan_id') or plan_id)
                )
                operation_id = _fba_set_amazon_operation(
                    state, response_payload, 'recovery_cancel_plan', 'recovery_rebuilding'
                )
                if not operation_id:
                    state['operation'] = {
                        'kind': 'recovery_cancel_plan', 'status': 'SUCCESS',
                        'next_stage': 'recovery_rebuilding', 'started_at': ss_listing_checks._listagent_now_iso(),
                    }
            _fba_save_amazon_state(conn, session_id, state, preserve_concurrent_pack=True)
            conn.commit()
            return jsonify({
                'success': True, 'amazon_workflow': state, 'operation_id': operation_id,
                'items': session_data.get('items') or [],
            })

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
            state, response_payload = _fba_prepare_and_create_plan(
                api, marketplace_id, session_data, state
            )
            next_stage = 'plan_created'
        else:
            if not plan_id:
                raise FbaInboundValidationError('Create the Amazon inbound plan first')
            if action == 'check-plan':
                health = _fba_plan_health(
                    api, marketplace_id, state.get('plan_items') or [],
                    session_items=session_data.get('items') or [], boxes=state.get('boxes') or [],
                )
                health['trigger'] = 'check_plan'
                state['plan_health'] = health
                _fba_save_amazon_state(conn, session_id, state, preserve_concurrent_pack=True)
                conn.commit()
                return jsonify({'success': True, 'amazon_workflow': state, 'plan_health': health})
            state = _fba_amazon_refresh_operation(api, state)
            pending = state.get('operation') if isinstance(state.get('operation'), dict) else {}
            if str(pending.get('status') or '').upper() == 'FAILED':
                # Redoing the failed step, or any step before it, is how the operator
                # recovers; only a later step is refused so a failure cannot be skipped.
                if not _fba_action_repeats_or_precedes(operation_kind, pending.get('kind')):
                    raise FbaInboundValidationError(
                        state.get('last_error') or 'The previous Amazon operation failed; refresh before continuing'
                    )
                state['operation'] = {}
                state['last_error'] = ''
                pending = {}
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
                recovery = state.get('recovery') if isinstance(state.get('recovery'), dict) else {}
                if recovery.get('active') and recovery.get('phase') == 'choose_packing':
                    recovery['phase'] = 'confirming_packing'
                    recovery['manually_selected_packing_option_id'] = option_id
                    state['recovery'] = recovery
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
                # Amazon answers a plan with a deleted MSKU with a bare
                # InternalServerError, so name the culprit before asking.
                health = _fba_plan_health(
                    api, marketplace_id, state.get('plan_items') or [],
                    session_items=session_data.get('items') or [], boxes=state.get('boxes') or [],
                )
                health['trigger'] = 'generate_placement_preflight'
                state['plan_health'] = health
                if health.get('blocking_count'):
                    _fba_save_amazon_state(conn, session_id, state, preserve_concurrent_pack=True)
                    conn.commit()
                    raise FbaInboundValidationError(health['summary'])
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
                selected_placement_id = ss_fba_schema._fba_trim(state.get('selected_placement_option_id'), 38)
                selected_placement = next((
                    row for row in state.get('placement_options') or []
                    if row.get('placementOptionId') == selected_placement_id
                    or option_state(row) == 'ACCEPTED'
                ), None)
                expected_shipments = {
                    shipment_id for shipment_id in (selected_placement or {}).get('shipmentIds') or []
                    if shipment_id
                }
                available = {
                    (row.get('shipmentId'), row.get('transportationOptionId'))
                    for row in state.get('transportation_options') or []
                    if row.get('can_purchase') or _fba_transport_option_can_purchase(row)
                }
                quoted_shipments = {shipment_id for shipment_id, _option_id in available if shipment_id}
                missing_quotes = expected_shipments - quoted_shipments
                if missing_quotes:
                    raise FbaInboundValidationError(
                        'Amazon did not return a purchasable partnered-carrier quote for every destination. '
                        'Regenerate shipping options or complete the unquoted shipment in Send to Amazon.'
                    )
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
                        raise FbaInboundValidationError('Select a quoted Amazon-partnered carrier for every destination')
                    if shipment_id in seen_shipments:
                        raise FbaInboundValidationError('Select exactly one carrier option per shipment')
                    seen_shipments.add(shipment_id)
                    chosen.append({
                        'shipmentId': shipment_id,
                        'transportationOptionId': option_id,
                        'contactInformation': contact,
                    })
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
        amazon_detail = _fba_amazon_exception_detail(exc)
        if amazon_detail:
            ss_config.logger.error('fba amazon action: %s', exc, exc_info=True)
            return jsonify({'success': False, 'error': amazon_detail}), 400
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
        plan_id = ss_fba_schema._fba_trim(state.get('inbound_plan_id'), 38)
        api, _marketplace_id = _fba_amazon_client()
        legacy_api, _legacy_marketplace_id = _fba_amazon_client(legacy=True)
        download_url, _box_ids = _fba_amazon_box_label_document(
            api, legacy_api, plan_id, shipment_id, confirmation_id,
            local_boxes=state.get('boxes') or [],
            local_box_id=ss_fba_schema._fba_trim(request.args.get('box_id'), 40),
        )
        return redirect(download_url)
    except FbaInboundValidationError as exc:
        return jsonify({'success': False, 'error': str(exc)}), 409
    except Exception as exc:
        amazon_detail = _fba_amazon_exception_detail(exc)
        if amazon_detail:
            ss_config.logger.error('fba amazon labels: %s', exc, exc_info=True)
            return jsonify({'success': False, 'error': amazon_detail}), 502
        return jsonify({'success': False, 'error': ss_errors._safe_error(exc, 'fba amazon labels')}), 502


def _fba_amazon_exception_detail(exc):
    try:
        from sp_api.base.exceptions import SellingApiException
    except Exception:
        return ''
    if not isinstance(exc, SellingApiException):
        return ''
    problems = getattr(exc, 'error', None)
    problems = problems if isinstance(problems, list) else []
    fatal = next((
        problem for problem in problems
        if isinstance(problem, dict)
        and not (
            ss_fba_schema._fba_trim(problem.get('severity'), 20).upper() == 'WARNING'
            or ss_fba_schema._fba_trim(problem.get('message'), 700).upper().startswith('WARNING:')
        )
    ), None)
    if fatal:
        message = ss_fba_schema._fba_trim(fatal.get('message') or fatal.get('details') or fatal.get('code'), 700)
    else:
        message = ss_fba_schema._fba_trim(getattr(exc, 'message', '') or str(exc), 700)
    return ('Amazon rejected the request: ' + message) if message else 'Amazon rejected the request'


def _fba_prepare_and_create_plan(api, marketplace_id, session_data, state):
    """Create a plan from saved rows without changing any physical pack records."""
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
            'Amazon plan creation is waiting: ' + (
                ', '.join(parts) or 'check every Seller SKU for FBA readiness'
            )
        )

    source = _fba_amazon_source_from_settings()
    request_body = build_create_plan_request(session_data, source, marketplace_id)
    mskus = [row.get('msku') for row in request_body.get('items') or [] if row.get('msku')]
    prep_by_msku = {}
    try:
        # Both prep-details endpoints accept at most 100 SKUs per request.
        for offset in range(0, len(mskus), 100):
            prep_payload = _fba_amazon_payload(api.list_prep_details(
                marketplaceId=marketplace_id, mskus=mskus[offset:offset + 100]
            ))
            prep_by_msku.update({
                ss_fba_schema._fba_trim(row.get('msku'), 255).casefold(): row
                for row in prep_payload.get('mskuPrepDetails') or [] if isinstance(row, dict)
            })
    except Exception as prep_error:
        kind, named_mskus = ss_fba_readiness._fba_prep_error_mskus(prep_error)
        if kind == 'invalid' and named_mskus:
            raise FbaInboundValidationError(ss_fba_readiness._fba_invalid_msku_message(
                named_mskus, session_data.get('items') or []
            ))
        if kind != 'unavailable' or not named_mskus:
            raise
        raise FbaInboundValidationError(ss_fba_readiness._fba_inbound_activation_message(
            named_mskus, session_data.get('items') or []
        ))
    # Amazon's live listPrepDetails endpoint currently returns only the first
    # row for some multi-MSKU calls. Prefer the exact one-SKU details captured
    # while each item was counted so an existing category is never overwritten.
    for item in session_data.get('items') or []:
        if not isinstance(item, dict) or not isinstance(item.get('amazon_prep'), dict):
            continue
        seller_sku = ss_fba_schema._fba_trim(item.get('seller_sku') or item.get('msku'), 255)
        saved_prep = ss_fba_readiness._fba_normalize_prep_detail(item.get('amazon_prep'), msku=seller_sku)
        category = ss_fba_schema._fba_trim(saved_prep.get('prep_category'), 80).upper()
        if not seller_sku or not category or category == 'UNKNOWN':
            continue
        prep_by_msku[seller_sku.casefold()] = {
            'msku': seller_sku,
            'prepCategory': category,
            'prepTypes': saved_prep.get('prep_types') or [],
            'prepOwnerConstraint': saved_prep.get('prep_owner_constraint') or '',
            'labelOwnerConstraint': saved_prep.get('label_owner_constraint') or '',
            'allOwnersConstraint': saved_prep.get('all_owners_constraint') or '',
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
    pending_prep = list(missing_prep)
    while pending_prep:
        prep_batch = pending_prep[:100]
        prep_problem = None
        try:
            prep_response = _fba_amazon_payload(api.set_prep_details(
                marketplaceId=marketplace_id,
                mskuPrepDetails=[{
                    'msku': msku, 'prepCategory': 'NONE', 'prepTypes': ['ITEM_NO_PREP']
                } for msku in prep_batch],
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
                    message = next((
                        problem.get('message') for problem in problems
                        if isinstance(problem, dict) and problem.get('message')
                    ), '')
                    prep_problem = FbaInboundValidationError(
                        message or 'Amazon could not save the item prep category'
                    )
                    break
                time.sleep(0.5)
            else:
                raise FbaInboundValidationError(
                    'Amazon is still saving the item prep category; try Create Amazon plan again'
                )
        except FbaInboundValidationError as exc:
            prep_problem = exc

        if prep_problem is not None:
            existing = ss_fba_readiness._fba_existing_prep_conflict(prep_problem)
            existing_key = ss_fba_schema._fba_trim((existing or {}).get('msku'), 255).casefold()
            matched_msku = next((
                msku for msku in prep_batch if msku.casefold() == existing_key
            ), '')
            if not matched_msku:
                raise prep_problem
            prep_corrections.append({
                'msku': matched_msku,
                'prepCategory': existing['prepCategory'],
                'preserved': True,
            })
            pending_prep = [
                msku for msku in pending_prep if msku.casefold() != existing_key
            ]
            ss_config.logger.info(
                'Preserving Amazon prep category %s for %s before retrying setPrepDetails',
                existing['prepCategory'], matched_msku,
            )
            continue

        prep_corrections.extend(
            {'msku': msku, 'prepCategory': 'NONE'} for msku in prep_batch
        )
        pending_prep = pending_prep[len(prep_batch):]

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
                    f"{row['msku']} {row['field']}={row['to']}" for row in corrections
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
    return state, response_payload


def _fba_set_amazon_operation(state, response_payload, kind, next_stage, success_flag=''):
    operation_id = ss_fba_schema._fba_trim((response_payload or {}).get('operationId'), 38)
    if operation_id:
        state['operation'] = {
            'id': operation_id,
            'kind': kind,
            'status': 'IN_PROGRESS',
            'next_stage': next_stage,
            'success_flag': success_flag,
            'started_at': ss_listing_checks._listagent_now_iso(),
        }
    else:
        state.pop('operation', None)
        state['stage'] = next_stage
        if success_flag:
            state[success_flag] = True
    return operation_id


def _fba_packed_quantities(conn, session_id):
    return {
        ss_fba_schema._fba_trim(row['msku'], 255).casefold(): int(row['quantity'] or 0)
        for row in conn.execute('''
            SELECT msku, COUNT(*) AS quantity
            FROM fba_pack_scans WHERE session_id = ? GROUP BY msku COLLATE NOCASE
        ''', (session_id,)).fetchall()
    }


def _fba_recovery_fnsku_mismatches(recovery, plan_items):
    previous = recovery.get('fnsku_by_msku') if isinstance(recovery.get('fnsku_by_msku'), dict) else {}
    current = {
        ss_fba_schema._fba_trim(item.get('msku') or item.get('seller_sku'), 255).casefold():
            ss_fba_schema._fba_trim(item.get('fnsku'), 80).upper()
        for item in plan_items if isinstance(item, dict)
    }
    mismatches = []
    for key, old_fnsku in previous.items():
        new_fnsku = current.get(str(key).casefold(), '')
        if old_fnsku and new_fnsku and str(old_fnsku).upper() != new_fnsku:
            mismatches.append({
                'msku': key, 'previous_fnsku': str(old_fnsku).upper(), 'new_fnsku': new_fnsku,
            })
    return mismatches


def _fba_recovery_option_groups(api, plan_id, option):
    groups = []
    for index, group_id in enumerate(option.get('packingGroups') or [], 1):
        items = _fba_amazon_collect(
            lambda group_id=group_id, **kwargs: api.list_packing_group_items(
                plan_id, group_id, **kwargs
            ),
            'items', page_size=100, limit=2500,
        )
        groups.append({
            'packing_group_id': group_id, 'label': f'Group {index}', 'items': items,
        })
    return groups


def _fba_choose_compatible_recovery_packing(api, state):
    recovery = state.get('recovery') if isinstance(state.get('recovery'), dict) else {}
    saved_boxes = recovery.get('saved_boxes') if isinstance(recovery.get('saved_boxes'), list) else []
    plan_id = ss_fba_schema._fba_trim(state.get('inbound_plan_id'), 38)
    compatible = []
    for option in state.get('packing_options') or []:
        if option_state(option) not in ('', 'AVAILABLE', 'OFFERED'):
            continue
        groups = _fba_recovery_option_groups(api, plan_id, option)
        _boxes, conflicts = remap_recovery_boxes(saved_boxes, groups)
        option['recovery_conflict_boxes'] = [row.get('box_id') for row in conflicts]
        option['recovery_compatible'] = not conflicts
        if not conflicts:
            compatible.append((len(groups), ss_fba_schema._fba_trim(option.get('packingOptionId'), 38), groups))
    if not compatible:
        return '', []
    compatible.sort(key=lambda row: (row[0], row[1]))
    return compatible[0][1], compatible[0][2]


def _fba_recovery_save_updated_items(conn, session_id, updated_items):
    clean_items = [ss_fba_inventory._fba_session_item_payload(item) for item in updated_items if isinstance(item, dict)]
    now = ss_listing_checks._listagent_now_iso()
    result = conn.execute('''
        UPDATE fba_prep_sessions
        SET items_json = ?, item_count = ?, total_units = ?, count_revision = count_revision + 1,
            updated_at = ?
        WHERE id = ? AND status = 'open'
    ''', (
        json.dumps(clean_items, ensure_ascii=False), len(clean_items),
        sum(int(item.get('quantity') or 0) for item in clean_items), now, session_id,
    ))
    if result.rowcount != 1:
        raise FbaInboundValidationError('Open FBA session not found while rebuilding the plan')
    return clean_items


def _fba_amazon_sync_recovery(api, marketplace_id, conn, session_id, session_data, state):
    """Advance one safe recovery step per sync without repeating physical prep work."""
    recovery = state.get('recovery') if isinstance(state.get('recovery'), dict) else {}
    if not recovery.get('active'):
        return _fba_amazon_sync_snapshot(api, state), session_data

    state = _fba_amazon_refresh_operation(api, state)
    operation = state.get('operation') if isinstance(state.get('operation'), dict) else {}
    operation_kind = ss_fba_schema._fba_trim(operation.get('kind'), 80)
    operation_status = ss_fba_schema._fba_trim(operation.get('status'), 30).upper()
    if operation_status == 'FAILED':
        recovery['phase'] = 'failed'
        recovery['error'] = state.get('last_error') or 'Amazon could not rebuild the plan'
        state['stage'] = 'recovery_failed'
        state['recovery'] = recovery
        return state, session_data

    if recovery.get('phase') == 'canceling' and operation_kind == 'recovery_cancel_plan':
        if operation_status not in ('', 'SUCCESS'):
            return state, session_data
        updated_items = _fba_recovery_save_updated_items(
            conn, session_id, recovery.get('updated_items') or []
        )
        session_data['items'] = updated_items
        recovery['old_plan_cancelled'] = True
        recovery['phase'] = 'creating_plan'
        recovery['cancelled_at'] = ss_listing_checks._listagent_now_iso()
        preserved = {
            key: state.get(key) for key in (
                'inventory_removed_by_msku', 'removed_locations_by_msku',
                'printed_item_label_counts', 'last_item_label_print', 'last_pack_scan',
            ) if key in state
        }
        state = {
            'stage': 'draft', 'inbound_plan_id': '', 'boxes': [],
            'packing_options': [], 'packing_groups': [], 'placement_options': [],
            'shipments': [], 'transportation_options': [], 'recovery': recovery,
            **preserved,
        }
        try:
            state, response = _fba_prepare_and_create_plan(
                api, marketplace_id, session_data, state
            )
            _fba_set_amazon_operation(
                state, response, 'recovery_create_plan', 'plan_created'
            )
        except Exception as exc:
            recovery['phase'] = 'failed'
            recovery['error'] = ss_errors._safe_error(exc, 'FBA replacement plan')
            state['stage'] = 'recovery_failed'
            state['recovery'] = recovery
        return state, session_data

    if not ss_fba_schema._fba_trim(state.get('inbound_plan_id'), 38):
        recovery['phase'] = 'failed'
        recovery['error'] = recovery.get('error') or 'The old plan was canceled, but no replacement plan exists'
        state['stage'] = 'recovery_failed'
        state['recovery'] = recovery
        return state, session_data

    state = _fba_amazon_sync_snapshot(api, state)
    operation = state.get('operation') if isinstance(state.get('operation'), dict) else {}
    operation_status = ss_fba_schema._fba_trim(operation.get('status'), 30).upper()
    if operation_status not in ('', 'SUCCESS', 'FAILED'):
        return state, session_data

    phase = recovery.get('phase')
    if phase == 'creating_plan':
        mismatches = _fba_recovery_fnsku_mismatches(recovery, state.get('plan_items') or [])
        if mismatches:
            recovery['phase'] = 'label_mismatch'
            recovery['fnsku_mismatches'] = mismatches
            state['stage'] = 'recovery_label_mismatch'
        elif state.get('packing_options'):
            recovery['phase'] = 'choosing_packing'
        else:
            response = _fba_amazon_payload(
                api.generate_packing_options(state['inbound_plan_id'])
            )
            recovery['phase'] = 'generating_packing'
            state['stage'] = 'packing_generating'
            _fba_set_amazon_operation(
                state, response, 'recovery_generate_packing', 'packing_options'
            )
        state['recovery'] = recovery
        return state, session_data

    if phase in ('generating_packing', 'choosing_packing') and state.get('packing_options'):
        option_id, groups = _fba_choose_compatible_recovery_packing(api, state)
        if option_id:
            response = _fba_amazon_payload(
                api.confirm_packing_option(state['inbound_plan_id'], option_id)
            )
            recovery['phase'] = 'confirming_packing'
            recovery['auto_selected_packing_option_id'] = option_id
            recovery['replacement_groups'] = groups
            state['selected_packing_option_id'] = option_id
            state['stage'] = 'packing_confirming'
            _fba_set_amazon_operation(
                state, response, 'recovery_confirm_packing', 'packing', 'packing_confirmed'
            )
        else:
            recovery['phase'] = 'choose_packing'
            recovery['warning'] = (
                'Amazon changed the packing groups. Choose an option and re-sort the cartons shown.'
            )
        state['recovery'] = recovery
        return state, session_data

    if phase == 'confirming_packing' and state.get('packing_confirmed') and state.get('packing_groups'):
        restored_boxes, conflicts = remap_recovery_boxes(
            recovery.get('saved_boxes') or [], state.get('packing_groups') or []
        )
        unresolved = [row for row in conflicts if row.get('unknown_mskus')]
        resort_required = []
        for conflict in conflicts:
            moves = conflict.get('moves') if isinstance(conflict.get('moves'), list) else []
            if moves:
                resort_required.append({
                    'from_box_id': conflict.get('box_id'),
                    'box_ids': conflict.get('split_box_ids') or [],
                    'moves': moves,
                })
            for move in moves:
                conn.execute('''
                    UPDATE fba_pack_scans
                    SET box_id = ?, packing_group_id = ?
                    WHERE session_id = ? AND box_id = ? COLLATE NOCASE AND msku = ? COLLATE NOCASE
                ''', (
                    move.get('to_box_id'), move.get('packing_group_id'), session_id,
                    move.get('from_box_id'), move.get('msku'),
                ))
        state['boxes'] = restored_boxes
        recovery['box_conflicts'] = conflicts
        recovery['resort_required'] = resort_required
        recovery['phase'] = 'box_conflict' if unresolved else 'complete'
        recovery['active'] = bool(unresolved)
        recovery['completed_at'] = ss_listing_checks._listagent_now_iso() if not unresolved else ''
        state['stage'] = 'packing'
        state['recovery'] = recovery
    return state, session_data


def _fba_match_label_boxes(amazon_boxes, local_boxes):
    """Match original carton names by contents and measurements, never API order."""
    def contents(rows):
        totals = {}
        for item in rows or []:
            sku = str(item.get('msku') or item.get('seller_sku') or '').strip().casefold()
            totals[sku] = totals.get(sku, 0) + int(item.get('quantity') or 0)
        return totals

    def measurements_match(remote, local):
        dimensions = remote.get('dimensions') or {}
        weight = remote.get('weight') or {}
        dimension_factor = {'IN': 1, 'CM': 1 / 2.54}.get(dimensions.get('unitOfMeasurement'))
        weight_factor = {'LB': 1, 'KG': 2.20462262185}.get(weight.get('unit'))
        if not dimension_factor or not weight_factor:
            return False
        try:
            for axis in ('length', 'width', 'height'):
                actual = float(dimensions.get(axis)) * dimension_factor
                expected = float(local.get(axis + '_in'))
                if expected <= 0 or not math.isclose(actual, expected, rel_tol=.001, abs_tol=.01):
                    return False
            expected = float(local.get('weight_lb'))
            return expected > 0 and math.isclose(float(weight.get('value')) * weight_factor,
                                                 expected, rel_tol=.001, abs_tol=.01)
        except (TypeError, ValueError, OverflowError):
            return False

    rows = []
    for remote in amazon_boxes:
        content = contents(remote.get('items'))
        candidates = [local for local in local_boxes if content and
                      contents(local.get('contents')) == content and measurements_match(remote, local)]
        # A compressed set of identical cartons cannot identify a single physical box.
        local = candidates[0] if len(candidates) == 1 and int(remote.get('quantity') or 1) == 1 else {}
        rows.append({
            'amazon_box_id': ss_fba_schema._fba_trim(remote.get('boxId'), 80),
            'local_box_id': local.get('local_id') or '',
            'packing_group_id': local.get('packing_group_id') or '',
            'units': sum(content.values()),
            'match_error': '' if local else 'Original box number could not be uniquely confirmed. Compare carton contents before applying labels.',
        })
    for row in rows:
        if row['local_box_id'] and sum(other['local_box_id'] == row['local_box_id'] for other in rows) > 1:
            row['match_error'] = 'More than one Amazon carton matched this box. Refresh and verify carton contents.'
    return rows


def _fba_amazon_box_label_document(api, legacy_api, plan_id, shipment_id, confirmation_id,
                                 *, local_boxes=None, local_box_id=''):
    shipment_boxes = _fba_amazon_collect(
        lambda **kwargs: api.list_shipment_boxes(plan_id, shipment_id, **kwargs),
        'boxes', page_size=1000, limit=5000,
    )
    box_ids = []
    if local_box_id:
        matches = [row for row in _fba_match_label_boxes(shipment_boxes, local_boxes or [])
                   if row['local_box_id'] == local_box_id and not row['match_error'] and row['amazon_box_id']]
        if len(matches) != 1:
            raise FbaInboundValidationError('Could not uniquely match this box to an Amazon carton label. Refresh Amazon and verify the box contents.')
        shipment_boxes = [{'boxId': matches[0]['amazon_box_id']}]
    for box in shipment_boxes:
        box_id = ss_fba_schema._fba_trim((box or {}).get('boxId'), 80)
        if box_id and box_id not in box_ids:
            box_ids.append(box_id)
    if not box_ids:
        raise FbaInboundValidationError(
            'Amazon has not returned the carton IDs needed to print this shipment yet. Refresh Amazon and try again.'
        )
    response = _fba_amazon_payload(legacy_api.get_labels(
        confirmation_id,
        PageType='PackageLabel_Thermal',
        LabelType='UNIQUE',
        # Swagger 2 arrays default to CSV query encoding. requests encodes a
        # Python list as repeated keys, which Amazon accepts but silently uses
        # only one carton ID from. A comma-delimited value returns every label.
        PackageLabelsToPrint=','.join(box_ids),
    ))
    download_url = ss_fba_schema._fba_trim(response.get('DownloadURL') or response.get('downloadURL'), 2000)
    if not download_url:
        raise FbaInboundValidationError('Amazon did not return a box-label document')
    return download_url, box_ids
