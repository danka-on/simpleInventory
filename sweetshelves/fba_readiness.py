"""Fba readiness for Sweet Shelves."""

from decimal import Decimal
from decimal import InvalidOperation
import math

import datetime
import json
import re
import sqlite3
import threading
import time
from fba_inbound import FbaInboundValidationError
from flask import jsonify, request
from . import (
    amazon_catalog as ss_amazon_catalog, amazon_listing as ss_amazon_listing, config as ss_config, errors
    as ss_errors, fba_inventory as ss_fba_inventory, fba_schema as ss_fba_schema, fba_shipments as
    ss_fba_shipments, listing_checks as ss_listing_checks, listing_settings as ss_listing_settings,
    normalization as ss_normalization,
)


_FBA_AMAZON_BARCODE_GUIDANCE_CACHE = {}


_FBA_AMAZON_BARCODE_GUIDANCE_LOCK = threading.Lock()


_FBA_AMAZON_BARCODE_GUIDANCE_TTL = 30 * 60


_FBA_ITEM_LABEL_PRINT_LOCK = threading.Lock()


_FBA_LISTING_READINESS_CACHE = {}


_FBA_LISTING_READINESS_CACHE_LOCK = threading.Lock()


_FBA_LISTING_READINESS_CACHE_TTL = 5 * 60


def _fba_inbound_unavailable_skus(exc):
    text = str(exc or '')
    match = re.search(r'MSKUs?\s*:\s*\[([^\]]+)\]', text, re.IGNORECASE)
    if not match:
        return []
    return [ss_fba_schema._fba_trim(value.strip(" '\""), 255) for value in match.group(1).split(',') if value.strip(" '\"")]


def _fba_inbound_activation_message(unavailable_mskus, items):
    """Explain Amazon's temporary Listings-to-Inbound propagation delay."""
    item_by_msku = {
        ss_fba_schema._fba_trim(item.get('seller_sku') or item.get('msku'), 255).casefold(): item
        for item in items or [] if isinstance(item, dict)
    }
    details = []
    seen = set()
    for raw_msku in unavailable_mskus or []:
        msku = ss_fba_schema._fba_trim(raw_msku, 255)
        key = msku.casefold()
        if not msku or key in seen:
            continue
        seen.add(key)
        item = item_by_msku.get(key) or {}
        barcode = ss_fba_schema._fba_trim(item.get('barcode'), 160)
        title = ss_fba_schema._fba_trim(item.get('title'), 120)
        details.append(
            f'{msku}'
            + (f' · barcode {barcode}' if barcode else '')
            + (f' · {title}' if title else '')
        )
        if len(details) >= 20:
            break
    subject = '; '.join(details) or 'one or more newly activated Seller SKUs'
    return (
        'Amazon has assigned the FNSKUs, but its inbound service is still activating: '
        + subject
        + '. These items are not rejected and your scanned list is saved. '
        + 'Wait 1–2 minutes, then press Create Amazon plan again; no rescan is needed.'
    )


def _fba_missing_prep_mskus(mskus, prep_by_msku):
    """Return MSKUs for which Amazon has not assigned a prep classification."""
    prep_by_msku = prep_by_msku if isinstance(prep_by_msku, dict) else {}
    missing = []
    for raw_msku in mskus or []:
        msku = ss_fba_schema._fba_trim(raw_msku, 255)
        if not msku:
            continue
        detail = prep_by_msku.get(msku.casefold())
        category = ss_fba_schema._fba_trim(
            detail.get('prepCategory') if isinstance(detail, dict) else '', 80
        ).upper()
        # listPrepDetails may omit a newly activated MSKU altogether instead of
        # returning prepCategory=UNKNOWN. Both mean setPrepDetails is required.
        if not category or category == 'UNKNOWN':
            missing.append(msku)
    return missing


def _fba_listings_client():
    credentials, seller_id, marketplace_id, marketplace = ss_amazon_catalog._amazon_spapi_context()
    if marketplace is None:
        raise FbaInboundValidationError('Amazon SP-API marketplace is not available')
    from sp_api.api import ListingsItems
    return ListingsItems(credentials=credentials, marketplace=marketplace), seller_id, marketplace_id


def _fba_listing_issue_message(issue):
    if not isinstance(issue, dict):
        return ss_fba_schema._fba_trim(issue, 500)
    message = ss_fba_schema._fba_trim(issue.get('message') or issue.get('code'), 500)
    attribute_names = issue.get('attributeNames') if isinstance(issue.get('attributeNames'), list) else []
    if attribute_names:
        suffix = ', '.join(ss_fba_schema._fba_trim(value, 80) for value in attribute_names if ss_fba_schema._fba_trim(value, 80))
        if suffix:
            message = (message + f' ({suffix})').strip()
    return message


_FBA_REQUIRED_SAFETY_FIELDS = {
    'supplier_declared_dg_hz_regulation',
    'batteries_required',
}


def _fba_issue_is_missing_safety_info(issue):
    if not isinstance(issue, dict):
        return False
    values = []
    if ss_fba_schema._fba_trim(issue.get('attributeName'), 120):
        values.append(ss_fba_schema._fba_trim(issue.get('attributeName'), 120).lower())
    if isinstance(issue.get('attributeNames'), list):
        values.extend(ss_fba_schema._fba_trim(value, 120).lower() for value in issue['attributeNames'])
    # Amazon does not always put attribute names in attributeNames. Some
    # product types return them in a nested issue payload or only in message.
    try:
        serialized = json.dumps(issue, ensure_ascii=False).lower()
    except Exception:
        serialized = ss_fba_schema._fba_trim(issue.get('message'), 1000).lower()
    mentioned = {field for field in _FBA_REQUIRED_SAFETY_FIELDS if field in serialized or field in values}
    if mentioned:
        return True
    return _fba_error_is_missing_safety_info(issue.get('message'))


def _fba_error_is_missing_safety_info(value):
    """Recognize Amazon's missing FBA prerequisite attributes."""
    text = ss_fba_schema._fba_trim(value, 4000).lower().replace('\\_', '_')
    if not text:
        return False
    aliases = (
        'supplier_declared_dg_hz_regulation',
        'dangerous goods regulations',
        'dangerous goods regulation',
        'batteries_required',
        'are batteries required',
    )
    parts = [part.strip() for part in re.split(r'\s*;\s*', text) if part.strip()]
    return bool(parts) and all(any(alias in part for alias in aliases) for part in parts)


_FBA_OFFER_BLOCKING_TOKENS = ('qualification_required', 'approval', 'restricted', 'fba_inb_0021')


def _fba_issue_blocks_fba_offer(issue):
    """Only approval/qualification errors keep Amazon from switching an offer to FBA.

    Other ERROR-severity listing issues (variation brand conflicts, missing
    description, invalid color, suppressed main image) describe catalog
    quality. Seller Central still lets those offers change to Fulfilled by
    Amazon, so they must not fail setup before Amazon has been asked.
    """
    if not isinstance(issue, dict):
        return False
    try:
        serialized = json.dumps(issue, ensure_ascii=False).lower()
    except Exception:
        serialized = ss_fba_schema._fba_trim(issue.get('message'), 1000).lower()
    return any(token in serialized for token in _FBA_OFFER_BLOCKING_TOKENS)


def _fba_split_listing_issues(issues):
    """Separate FBA-offer blockers, answerable safety questions, and advisory catalog issues."""
    blocking, safety, advisory = [], [], []
    for issue in issues:
        if _fba_issue_blocks_fba_offer(issue):
            blocking.append(issue)
        elif _fba_issue_is_missing_safety_info(issue):
            safety.append(issue)
        else:
            advisory.append(issue)
    return blocking, safety, advisory


def _fba_listing_issue_notes(issues):
    return '; '.join(filter(None, (_fba_listing_issue_message(issue) for issue in issues[:3])))


def _fba_prep_error_is_inbound_pending(value):
    """Amazon's prep lookup rejects SKUs whose new FBA offer is still propagating."""
    return 'not available for inbound' in ss_fba_schema._fba_trim(value, 1000).lower()


def _fba_inventory_fnsku(msku):
    """Read the FNSKU from FBA inventory, which often leads the Listings Items summary."""
    seller_sku = ss_fba_schema._fba_trim(msku, 255)
    if not seller_sku:
        return ''
    try:
        credentials, _seller_id, marketplace_id, marketplace = ss_amazon_catalog._amazon_spapi_context()
        if marketplace is None:
            return ''
        from sp_api.api import Inventories
        payload = ss_fba_shipments._fba_amazon_payload(
            Inventories(credentials=credentials, marketplace=marketplace).get_inventory_summary_marketplace(
                details=False, marketplaceIds=[marketplace_id], sellerSkus=[seller_sku],
            )
        )
        rows = payload.get('inventorySummaries') if isinstance(payload.get('inventorySummaries'), list) else []
        for row in rows:
            if (isinstance(row, dict)
                    and ss_fba_schema._fba_trim(row.get('sellerSku'), 255).casefold() == seller_sku.casefold()):
                return ss_fba_schema._fba_trim(row.get('fnSku') or row.get('fnsku'), 80)
    except Exception as exc:
        ss_config.logger.warning('FBA inventory FNSKU lookup failed for %s: %s', seller_sku, exc)
    return ''


def _fba_listing_readiness(msku, *, force_refresh=False, client=None):
    """Inspect one exact Seller SKU without confusing missing FNSKU with ineligibility."""
    seller_sku = ss_fba_schema._fba_trim(msku, 255)
    cache_key = seller_sku.casefold()
    if not cache_key:
        return {
            'status': 'failed', 'seller_sku': '', 'fnsku': '', 'product_type': '',
            'fulfillment_channels': [], 'error': 'Amazon Seller SKU is missing',
        }
    now = time.time()
    if not force_refresh:
        with _FBA_LISTING_READINESS_CACHE_LOCK:
            cached = _FBA_LISTING_READINESS_CACHE.get(cache_key)
            if cached and now - float(cached.get('ts') or 0) < _FBA_LISTING_READINESS_CACHE_TTL:
                return dict(cached.get('data') or {})

    listings, seller_id, marketplace_id = client or _fba_listings_client()
    response = listings.get_listings_item(
        seller_id,
        seller_sku,
        marketplaceIds=[marketplace_id],
        includedData=['summaries', 'issues', 'fulfillmentAvailability', 'attributes'],
    )
    payload = ss_fba_shipments._fba_amazon_payload(response)
    summaries = payload.get('summaries') if isinstance(payload.get('summaries'), list) else []
    issues = payload.get('issues') if isinstance(payload.get('issues'), list) else []
    availability = payload.get('fulfillmentAvailability')
    if not isinstance(availability, list):
        availability = payload.get('fulfillment_availability')
    if not isinstance(availability, list):
        availability = []
    attributes = payload.get('attributes') if isinstance(payload.get('attributes'), dict) else {}
    submitted = attributes.get('fulfillment_availability')
    if not isinstance(submitted, list):
        submitted = []

    fnsku = ''
    product_type = ss_fba_schema._fba_trim(payload.get('productType') or payload.get('product_type'), 120)
    listing_statuses = []
    for summary in summaries:
        if not isinstance(summary, dict):
            continue
        fnsku = fnsku or ss_fba_schema._fba_trim(summary.get('fnSku') or summary.get('fnsku'), 80)
        product_type = product_type or ss_fba_schema._fba_trim(summary.get('productType') or summary.get('product_type'), 120)
        raw_status = summary.get('status')
        if isinstance(raw_status, list):
            listing_statuses.extend(ss_fba_schema._fba_trim(value, 50).upper() for value in raw_status if ss_fba_schema._fba_trim(value, 50))
        elif ss_fba_schema._fba_trim(raw_status, 50):
            listing_statuses.append(ss_fba_schema._fba_trim(raw_status, 50).upper())

    fulfillment_channels = []
    for row in availability:
        if not isinstance(row, dict):
            continue
        channel = ss_fba_schema._fba_trim(
            row.get('fulfillmentChannelCode') or row.get('fulfillment_channel_code'), 40
        ).upper()
        if channel and channel not in fulfillment_channels:
            fulfillment_channels.append(channel)
    # The attribute is what the seller submitted; the live channel above lags
    # it while Amazon processes an accepted FBA switch.
    submitted_channels = []
    for row in submitted:
        if not isinstance(row, dict):
            continue
        channel = ss_fba_schema._fba_trim(
            row.get('fulfillment_channel_code') or row.get('fulfillmentChannelCode'), 40
        ).upper()
        if channel and channel not in submitted_channels:
            submitted_channels.append(channel)

    error_issues = [
        issue for issue in issues
        if isinstance(issue, dict) and ss_fba_schema._fba_trim(issue.get('severity'), 20).upper() == 'ERROR'
    ]
    blocking_issues, safety_issues, advisory_issues = _fba_split_listing_issues(error_issues)
    # Advisory issues are kept as notes: the actual FBA patch decides whether
    # Amazon accepts the offer change, and polling verifies the FNSKU.
    listing_notes = _fba_listing_issue_notes(advisory_issues)
    # Listings Items can report the FNSKU long after FBA inventory already has
    # it, so consult the inventory record before deciding the SKU is not set up.
    if not fnsku:
        fnsku = _fba_inventory_fnsku(seller_sku)
    prep_details = _fba_item_prep_details(seller_sku)
    inbound_pending = _fba_prep_error_is_inbound_pending((prep_details or {}).get('error'))
    activation_note = ''
    if fnsku and not inbound_pending:
        status = 'ready'
        error = ''
    elif blocking_issues:
        status = 'failed'
        error = _fba_listing_issue_notes(blocking_issues) or 'Amazon reports a blocking listing error.'
    elif safety_issues:
        status = 'needs_safety_info'
        error = _fba_listing_issue_notes(safety_issues)
    elif fnsku:
        status = 'enabling'
        error = ''
        activation_note = (
            f'Amazon assigned FNSKU {fnsku} and is still making this SKU available '
            'for inbound shipments (Amazon: try again later).'
        )
    elif 'AMAZON_NA' in fulfillment_channels or 'AMAZON_NA' in submitted_channels:
        status = 'enabling'
        error = ''
        activation_note = 'Amazon accepted the switch to FBA and is still activating the offer.'
    else:
        status = 'needs_enablement'
        error = ''

    result = {
        'status': status,
        'seller_sku': seller_sku,
        'fnsku': fnsku,
        'product_type': product_type or 'PRODUCT',
        'fulfillment_channels': fulfillment_channels,
        'submitted_channels': submitted_channels,
        'listing_statuses': list(dict.fromkeys(listing_statuses)),
        'error': error,
        'listing_notes': listing_notes,
        'activation_note': activation_note,
        'inbound_pending': inbound_pending,
    }
    result['prep_details'] = prep_details
    with _FBA_LISTING_READINESS_CACHE_LOCK:
        _FBA_LISTING_READINESS_CACHE[cache_key] = {'ts': now, 'data': dict(result)}
    return result


def _fba_apply_listing_readiness(item, readiness, *, checked_at=None):
    status = ss_fba_schema._fba_trim((readiness or {}).get('status'), 40).lower()
    error = ss_fba_schema._fba_trim((readiness or {}).get('error'), 700)
    if status == 'failed' and _fba_error_is_missing_safety_info(error):
        status = 'needs_safety_info'
    if status not in ('ready', 'needs_enablement', 'needs_safety_info', 'enabling', 'failed', 'checking'):
        status = 'needs_enablement'
    item['fba_enablement_status'] = status
    item['fba_enablement_error'] = error
    item['fba_listing_notes'] = ss_fba_schema._fba_trim((readiness or {}).get('listing_notes'), 700)
    item['fba_activation_note'] = ss_fba_schema._fba_trim((readiness or {}).get('activation_note'), 500)
    item['fba_enablement_checked_at'] = checked_at or ss_listing_checks._listagent_now_iso()
    item['amazon_product_type'] = ss_fba_schema._fba_trim((readiness or {}).get('product_type'), 120)
    if isinstance((readiness or {}).get('prep_details'), dict):
        item['amazon_prep'] = _fba_normalize_prep_detail(
            readiness['prep_details'], msku=item.get('seller_sku')
        )
        item['amazon_prep']['checked'] = bool(readiness['prep_details'].get('checked'))
        item['amazon_prep']['checked_at'] = ss_fba_schema._fba_trim(
            readiness['prep_details'].get('checked_at'), 40
        ) or item['amazon_prep']['checked_at']
        item['amazon_prep']['error'] = ss_fba_schema._fba_trim(readiness['prep_details'].get('error'), 500)
    fnsku = ss_fba_schema._fba_trim((readiness or {}).get('fnsku'), 80)
    if fnsku:
        item['amazon_fnsku'] = fnsku
        if not ss_fba_schema._fba_trim(item.get('fnsku'), 80):
            item['fnsku'] = fnsku
    item['inbound_eligible'] = True if status == 'ready' else None
    item['inbound_error'] = ''
    item['inbound_checked_at'] = item['fba_enablement_checked_at']
    return item


def _fba_patch_listing_to_fba(msku, readiness, *, client=None):
    listings, seller_id, marketplace_id = client or _fba_listings_client()
    channels = {
        ss_fba_schema._fba_trim(value, 40).upper()
        for value in (readiness or {}).get('fulfillment_channels') or []
        if ss_fba_schema._fba_trim(value, 40)
    }
    if 'AMAZON_NA' in channels:
        return {'status': 'enabling', 'issues': []}
    patches = [{
        'op': 'add',
        'path': '/attributes/batteries_required',
        'value': [{'value': False, 'marketplace_id': marketplace_id}],
    }, {
        'op': 'add',
        'path': '/attributes/supplier_declared_dg_hz_regulation',
        'value': [{'value': 'not_applicable', 'marketplace_id': marketplace_id}],
    }, {
        'op': 'add',
        'path': '/attributes/fulfillment_availability',
        'value': [{'fulfillment_channel_code': 'AMAZON_NA'}],
    }]
    if 'DEFAULT' in channels:
        patches.append({
            'op': 'delete',
            'path': '/attributes/fulfillment_availability',
            'value': [{'fulfillment_channel_code': 'DEFAULT'}],
        })
    response = listings.patch_listings_item(
        seller_id,
        ss_fba_schema._fba_trim(msku, 255),
        marketplaceIds=[marketplace_id],
        issueLocale='en_US',
        body={
            'productType': ss_fba_schema._fba_trim((readiness or {}).get('product_type'), 120) or 'PRODUCT',
            'patches': patches,
        },
    )
    payload = ss_fba_shipments._fba_amazon_payload(response)
    response_status = ss_fba_schema._fba_trim(payload.get('status'), 40).upper()
    issues = payload.get('issues') if isinstance(payload.get('issues'), list) else []
    blocking = [
        issue for issue in issues
        if isinstance(issue, dict) and ss_fba_schema._fba_trim(issue.get('severity'), 20).upper() == 'ERROR'
    ]
    offer_blockers, safety_issues, advisory_issues = _fba_split_listing_issues(blocking)
    if offer_blockers:
        return {'status': 'failed', 'error': _fba_listing_issue_notes(offer_blockers), 'issues': issues}
    rejected = response_status in ('INVALID', 'ERROR', 'REJECTED', 'FAILURE')
    if rejected or (blocking and response_status != 'ACCEPTED'):
        safety_only = bool(safety_issues) and not advisory_issues
        return {
            'status': 'needs_safety_info' if safety_only else 'failed',
            'error': _fba_listing_issue_notes(blocking)
                     or f'Amazon returned {response_status or "an invalid response"} for the FBA offer change.',
            'issues': issues,
        }
    # Listings Items commonly echoes the issue set from immediately before an
    # accepted asynchronous patch: stale prerequisite questions as well as
    # catalog-quality errors (missing description, variation brand conflicts)
    # that never stop an offer from switching to FBA. ACCEPTED means Amazon
    # queued the complete transition and polling must verify the FNSKU.
    with _FBA_LISTING_READINESS_CACHE_LOCK:
        _FBA_LISTING_READINESS_CACHE.pop(ss_fba_schema._fba_trim(msku, 255).casefold(), None)
    return {
        'status': 'enabling', 'error': '', 'issues': issues,
        'listing_notes': _fba_listing_issue_notes(advisory_issues),
        'activation_note': 'Amazon accepted the switch to FBA and is still activating the offer.',
    }


def _fba_patch_listing_no_battery_safety(msku, readiness, *, client=None):
    """Save the default prerequisite attributes for a non-battery listing."""
    listings, seller_id, marketplace_id = client or _fba_listings_client()
    patches = [{
        'op': 'add',
        'path': '/attributes/batteries_required',
        'value': [{'value': False, 'marketplace_id': marketplace_id}],
    }, {
        'op': 'add',
        'path': '/attributes/supplier_declared_dg_hz_regulation',
        'value': [{'value': 'not_applicable', 'marketplace_id': marketplace_id}],
    }]
    response = listings.patch_listings_item(
        seller_id,
        ss_fba_schema._fba_trim(msku, 255),
        marketplaceIds=[marketplace_id],
        issueLocale='en_US',
        body={
            'productType': ss_fba_schema._fba_trim((readiness or {}).get('product_type'), 120) or 'PRODUCT',
            'patches': patches,
        },
    )
    payload = ss_fba_shipments._fba_amazon_payload(response)
    response_status = ss_fba_schema._fba_trim(payload.get('status'), 40).upper()
    issues = payload.get('issues') if isinstance(payload.get('issues'), list) else []
    blocking = [
        issue for issue in issues
        if isinstance(issue, dict) and ss_fba_schema._fba_trim(issue.get('severity'), 20).upper() == 'ERROR'
    ]
    product_type = ss_fba_schema._fba_trim((readiness or {}).get('product_type'), 120)
    offer_blockers, safety_issues, advisory_issues = _fba_split_listing_issues(blocking)
    if offer_blockers:
        return {
            'status': 'failed', 'error': _fba_listing_issue_notes(offer_blockers),
            'issues': issues, 'product_type': product_type,
        }
    rejected = response_status in ('INVALID', 'ERROR', 'REJECTED', 'FAILURE')
    if rejected or (blocking and response_status != 'ACCEPTED'):
        safety_only = bool(safety_issues) and not advisory_issues
        return {
            'status': 'needs_safety_info' if safety_only else 'failed',
            'error': _fba_listing_issue_notes(blocking)
                     or f'Amazon returned {response_status or "an invalid response"} for the listing update.',
            'issues': issues,
            'product_type': product_type,
        }
    # Listings Items can echo the old issue set while returning ACCEPTED for
    # this patch. ACCEPTED means Amazon queued the new answers; the subsequent
    # FBA-enablement read verifies that they cleared.
    with _FBA_LISTING_READINESS_CACHE_LOCK:
        _FBA_LISTING_READINESS_CACHE.pop(ss_fba_schema._fba_trim(msku, 255).casefold(), None)
    return {
        'status': 'needs_enablement', 'error': '', 'issues': issues,
        'product_type': product_type,
        'listing_notes': _fba_listing_issue_notes(advisory_issues),
    }


def _fba_create_separate_fba_offer(source_sku, target_sku, readiness, *, client=None):
    """Create an FBA-only companion offer while leaving the source FBM SKU unchanged."""
    listings, seller_id, marketplace_id = client or _fba_listings_client()
    source = ss_fba_schema._fba_trim(source_sku, 255)
    target = ss_fba_schema._fba_trim(target_sku, 255)
    if not source or not target or source.casefold() == target.casefold():
        return {'status': 'failed', 'error': 'A distinct FBA Seller SKU could not be generated.'}

    source_response = listings.get_listings_item(
        seller_id, source, marketplaceIds=[marketplace_id],
        includedData=['summaries', 'attributes', 'offers', 'fulfillmentAvailability'],
    )
    source_payload = ss_fba_shipments._fba_amazon_payload(source_response)
    summaries = source_payload.get('summaries') if isinstance(source_payload.get('summaries'), list) else []
    source_summary = next((row for row in summaries if isinstance(row, dict)), {})
    asin = ss_fba_schema._fba_trim(source_summary.get('asin'), 30)
    product_type = ss_fba_schema._fba_trim(
        source_summary.get('productType') or source_summary.get('product_type')
        or (readiness or {}).get('product_type'), 120
    )
    attributes = source_payload.get('attributes') if isinstance(source_payload.get('attributes'), dict) else {}
    if not asin and isinstance(attributes.get('merchant_suggested_asin'), list):
        asin = ss_fba_schema._fba_trim(next((
            row.get('value') for row in attributes['merchant_suggested_asin']
            if isinstance(row, dict) and ss_fba_schema._fba_trim(row.get('value'), 30)
        ), ''), 30)
    if not asin or not product_type:
        return {'status': 'failed', 'error': 'Amazon did not return the ASIN/product type needed for the separate FBA SKU.'}

    # Retry safely if the deterministic companion SKU was already created.
    try:
        existing_response = listings.get_listings_item(
            seller_id, target, marketplaceIds=[marketplace_id],
            includedData=['summaries', 'issues', 'fulfillmentAvailability'],
        )
        existing_payload = ss_fba_shipments._fba_amazon_payload(existing_response)
        existing_summaries = existing_payload.get('summaries') if isinstance(existing_payload.get('summaries'), list) else []
        existing_asin = ss_fba_schema._fba_trim(next((
            row.get('asin') for row in existing_summaries
            if isinstance(row, dict) and ss_fba_schema._fba_trim(row.get('asin'), 30)
        ), ''), 30)
        if existing_asin and existing_asin != asin:
            return {'status': 'failed', 'error': f'Amazon Seller SKU {target} already belongs to a different ASIN.'}
        if existing_asin:
            target_readiness = _fba_listing_readiness(target, force_refresh=True, client=client)
            if target_readiness.get('status') == 'needs_enablement':
                target_readiness.update(_fba_patch_listing_to_fba(target, target_readiness, client=client))
            target_readiness.update({
                'target_sku': target, 'source_sku': source,
                'strategy': 'separate_fba_sku', 'asin': asin,
            })
            return target_readiness
    except Exception as exc:
        error_text = ss_amazon_listing._amazon_format_spapi_error(exc).lower()
        if not any(token in error_text for token in ('404', 'not found', 'does not exist', 'invalid sku')):
            raise

    clone_attributes = {}
    for name in ('condition_type', 'condition_note', 'purchasable_offer', 'list_price',
                 'product_tax_code', 'skip_offer', 'max_order_quantity', 'gift_options'):
        value = attributes.get(name)
        if isinstance(value, list) and value:
            clone_attributes[name] = value
    clone_attributes['merchant_suggested_asin'] = [{
        'value': asin, 'marketplace_id': marketplace_id,
    }]
    if 'condition_type' not in clone_attributes:
        clone_attributes['condition_type'] = [{
            'value': ss_amazon_listing._amazon_normalize_condition_type(source_summary.get('conditionType') or 'new_new', 'new_new'),
            'marketplace_id': marketplace_id,
        }]
    clone_attributes['batteries_required'] = [{
        'value': False, 'marketplace_id': marketplace_id,
    }]
    clone_attributes['supplier_declared_dg_hz_regulation'] = [{
        'value': 'not_applicable', 'marketplace_id': marketplace_id,
    }]
    clone_attributes['fulfillment_availability'] = [{
        'fulfillment_channel_code': 'AMAZON_NA',
    }]
    body = {
        'productType': product_type,
        'requirements': 'LISTING_OFFER_ONLY',
        'attributes': clone_attributes,
    }
    response = listings.put_listings_item(
        seller_id, target, marketplaceIds=[marketplace_id], issueLocale='en_US', body=body,
    )
    payload = ss_fba_shipments._fba_amazon_payload(response)
    issues = payload.get('issues') if isinstance(payload.get('issues'), list) else []
    blocking = [
        issue for issue in issues
        if isinstance(issue, dict) and ss_fba_schema._fba_trim(issue.get('severity'), 20).upper() == 'ERROR'
    ]
    response_status = ss_fba_schema._fba_trim(payload.get('status'), 40).upper()
    offer_blockers, _safety_issues, advisory_issues = _fba_split_listing_issues(blocking)
    rejected = response_status in ('INVALID', 'ERROR', 'REJECTED', 'FAILURE')
    if offer_blockers or rejected or (blocking and response_status != 'ACCEPTED'):
        return {
            'status': 'failed', 'target_sku': target, 'source_sku': source,
            'strategy': 'separate_fba_sku',
            'error': _fba_listing_issue_notes(offer_blockers or blocking)
                     or f'Amazon returned {response_status or "an invalid response"} for the separate FBA SKU.',
            'issues': issues,
        }
    with _FBA_LISTING_READINESS_CACHE_LOCK:
        _FBA_LISTING_READINESS_CACHE.pop(target.casefold(), None)
    return {
        'status': 'enabling', 'error': '', 'issues': issues,
        'target_sku': target, 'source_sku': source, 'strategy': 'separate_fba_sku',
        'asin': asin, 'product_type': product_type,
        'listing_notes': _fba_listing_issue_notes(advisory_issues),
    }


def _fba_amazon_barcode_guidance_result(
    instruction='',
    *,
    identifier_type='',
    identifier='',
    detail='',
):
    """Translate Amazon's BarcodeInstruction into a safe UI-facing result."""
    raw_instruction = str(instruction or '').strip()
    normalized = raw_instruction.casefold()
    if normalized == 'canuseoriginalbarcode':
        status = 'manufacturer_barcode'
        label = 'Use regular item UPC'
        default_detail = "Amazon says this item can use the original manufacturer's barcode."
        checked = True
    elif normalized == 'requiresfnskulabel':
        status = 'amazon_barcode'
        label = 'Amazon barcode required'
        default_detail = 'Apply a scannable FBA product label and cover every original barcode.'
        checked = True
    elif normalized == 'mustprovidesellersku':
        status = 'seller_sku_required'
        label = 'Seller SKU needed'
        default_detail = 'Amazon needs the seller SKU before it can determine the barcode requirement.'
        checked = False
    else:
        status = 'unavailable'
        label = 'Barcode rule unavailable'
        default_detail = 'Amazon did not return a recognized barcode instruction. Check in Send to Amazon before labeling.'
        checked = False
    return {
        'status': status,
        'label': label,
        'detail': str(detail or default_detail).strip(),
        'instruction': raw_instruction,
        'checked': checked,
        'source': 'amazon_sp_api' if raw_instruction else '',
        'identifier_type': str(identifier_type or '').strip(),
        'identifier': str(identifier or '').strip(),
    }


def _fba_amazon_barcode_guidance_unavailable(
    detail,
    *,
    status='unavailable',
    label='Barcode rule unavailable',
    identifier_type='',
    identifier='',
):
    return {
        'status': status,
        'label': label,
        'detail': str(detail or '').strip(),
        'instruction': '',
        'checked': False,
        'source': '',
        'identifier_type': str(identifier_type or '').strip(),
        'identifier': str(identifier or '').strip(),
    }


def _fba_amazon_barcode_guidance_for_listings(listings, *, force_refresh=False):
    """Fetch Amazon labeling guidance for local listings, batching up to 50 IDs per call."""
    rows = list(listings or [])
    results = [None] * len(rows)
    pending = {}
    now = time.time()

    for index, listing in enumerate(rows):
        listing = listing if isinstance(listing, dict) else {}
        seller_sku = str(listing.get('seller_sku') or '').strip()
        asin = str(listing.get('asin') or '').strip().upper()
        identifier_type = 'seller_sku' if seller_sku else ('asin' if asin else '')
        identifier = seller_sku or asin
        if not identifier:
            results[index] = _fba_amazon_barcode_guidance_unavailable(
                'Match this UPC to an Amazon listing, then recheck the barcode rule.',
                status='no_listing',
                label='No Amazon SKU to check',
            )
            continue

        cache_key = f'{identifier_type}|{identifier.casefold()}'
        cached = _FBA_AMAZON_BARCODE_GUIDANCE_CACHE.get(cache_key)
        if (
            not force_refresh
            and cached
            and (now - float(cached.get('ts') or 0)) < _FBA_AMAZON_BARCODE_GUIDANCE_TTL
        ):
            results[index] = dict(cached.get('data') or {})
            results[index]['cached'] = True
            continue
        pending.setdefault((identifier_type, identifier), []).append(index)

    if not pending:
        return results

    with _FBA_AMAZON_BARCODE_GUIDANCE_LOCK:
        now = time.time()
        still_pending = {}
        for pending_key, indexes in pending.items():
            identifier_type, identifier = pending_key
            cache_key = f'{identifier_type}|{identifier.casefold()}'
            cached = _FBA_AMAZON_BARCODE_GUIDANCE_CACHE.get(cache_key)
            if (
                not force_refresh
                and cached
                and (now - float(cached.get('ts') or 0)) < _FBA_AMAZON_BARCODE_GUIDANCE_TTL
            ):
                value = dict(cached.get('data') or {})
                value['cached'] = True
                for index in indexes:
                    results[index] = dict(value)
            else:
                still_pending[pending_key] = indexes

        if not still_pending:
            return results

        try:
            credentials, _seller_id, marketplace_id, marketplace = ss_amazon_catalog._amazon_spapi_context()
            if marketplace is None:
                raise RuntimeError('Amazon SP-API is not available')
            from sp_api.api import FulfillmentInbound
            inbound = FulfillmentInbound(
                credentials=credentials,
                marketplace=marketplace,
                version='v0',
            )
            ship_to_country = {
                'ATVPDKIKX0DER': 'US',
                'A2EUQ1WTGCTBG2': 'CA',
                'A1AM78C64UM0Y8': 'MX',
            }.get(str(marketplace_id or '').strip(), 'US')

            for identifier_type in ('seller_sku', 'asin'):
                identifiers = [
                    identifier
                    for kind, identifier in still_pending
                    if kind == identifier_type
                ]
                for start in range(0, len(identifiers), 50):
                    chunk = identifiers[start:start + 50]
                    if not chunk:
                        continue
                    list_param = 'SellerSKUList' if identifier_type == 'seller_sku' else 'ASINList'
                    response_list = (
                        'SKUPrepInstructionsList'
                        if identifier_type == 'seller_sku'
                        else 'ASINPrepInstructionsList'
                    )
                    response_id = 'SellerSKU' if identifier_type == 'seller_sku' else 'ASIN'
                    invalid_list = 'InvalidSKUList' if identifier_type == 'seller_sku' else 'InvalidASINList'
                    response = inbound.prep_instruction({
                        'ShipToCountryCode': ship_to_country,
                        list_param: chunk,
                    })
                    if getattr(response, 'errors', None):
                        raise RuntimeError('Amazon returned an error while checking barcode requirements')
                    payload = getattr(response, 'payload', None) or {}
                    response_list_camel = (
                        'skuPrepInstructionsList'
                        if identifier_type == 'seller_sku'
                        else 'asinPrepInstructionsList'
                    )
                    returned = payload.get(response_list) or payload.get(response_list_camel) or []
                    invalid = payload.get(invalid_list) or payload.get(invalid_list[0].lower() + invalid_list[1:]) or []
                    values_by_id = {}
                    for instruction_row in returned if isinstance(returned, list) else []:
                        if not isinstance(instruction_row, dict):
                            continue
                        returned_id = str(
                            instruction_row.get(response_id)
                            or instruction_row.get(response_id[0].lower() + response_id[1:])
                            or instruction_row.get('sellerSku' if identifier_type == 'seller_sku' else 'asin')
                            or ''
                        ).strip()
                        instruction = (
                            instruction_row.get('BarcodeInstruction')
                            or instruction_row.get('barcodeInstruction')
                            or instruction_row.get('barcode_instruction')
                            or ''
                        )
                        if returned_id:
                            values_by_id[returned_id.casefold()] = _fba_amazon_barcode_guidance_result(
                                instruction,
                                identifier_type=identifier_type,
                                identifier=returned_id,
                            )
                    for invalid_row in invalid if isinstance(invalid, list) else []:
                        if not isinstance(invalid_row, dict):
                            continue
                        returned_id = str(
                            invalid_row.get(response_id)
                            or invalid_row.get(response_id[0].lower() + response_id[1:])
                            or invalid_row.get('sellerSku' if identifier_type == 'seller_sku' else 'asin')
                            or ''
                        ).strip()
                        reason = str(
                            invalid_row.get('ErrorReason')
                            or invalid_row.get('errorReason')
                            or ''
                        ).strip()
                        if returned_id:
                            detail = f'Amazon did not recognize this {"seller SKU" if identifier_type == "seller_sku" else "ASIN"}'
                            if reason:
                                detail += f' ({reason})'
                            values_by_id[returned_id.casefold()] = _fba_amazon_barcode_guidance_unavailable(
                                detail + '. Check the listing, then recheck the barcode rule.',
                                status='invalid_listing',
                                label='Amazon listing not recognized',
                                identifier_type=identifier_type,
                                identifier=returned_id,
                            )

                    for identifier in chunk:
                        value = values_by_id.get(identifier.casefold())
                        if not value:
                            value = _fba_amazon_barcode_guidance_unavailable(
                                'Amazon returned no barcode instruction for this listing. Check in Send to Amazon before labeling.',
                                identifier_type=identifier_type,
                                identifier=identifier,
                            )
                        cache_key = f'{identifier_type}|{identifier.casefold()}'
                        _FBA_AMAZON_BARCODE_GUIDANCE_CACHE[cache_key] = {
                            'ts': time.time(),
                            'data': dict(value),
                        }
                        for index in still_pending.get((identifier_type, identifier), []):
                            results[index] = dict(value)
        except Exception as exc:
            ss_config.logger.warning('FBA Amazon barcode guidance lookup failed: %s', exc)
            for (identifier_type, identifier), indexes in still_pending.items():
                value = _fba_amazon_barcode_guidance_unavailable(
                    'Could not ask Amazon for the barcode rule. Check in Send to Amazon before labeling.',
                    identifier_type=identifier_type,
                    identifier=identifier,
                )
                for index in indexes:
                    results[index] = dict(value)

    return [
        value or _fba_amazon_barcode_guidance_unavailable(
            'Amazon barcode guidance is not available. Check in Send to Amazon before labeling.'
        )
        for value in results
    ]


def _fba_amazon_barcode_guidance(listing, *, force_refresh=False):
    values = _fba_amazon_barcode_guidance_for_listings(
        [listing if isinstance(listing, dict) else {}],
        force_refresh=force_refresh,
    )
    return values[0]


def api_fba_prep_barcode_guidance():
    """Return Amazon's item-label rule for a seller SKU, with ASIN as fallback."""
    seller_sku = ss_fba_schema._fba_trim(request.args.get('seller_sku') or request.args.get('sku'), 120)
    asin = ss_fba_schema._fba_trim(request.args.get('asin'), 30).upper()
    force_refresh = str(request.args.get('refresh') or '').strip().lower() in ('1', 'true', 'yes', 'on')
    guidance = _fba_amazon_barcode_guidance(
        {'seller_sku': seller_sku, 'asin': asin},
        force_refresh=force_refresh,
    )
    return jsonify({
        'success': True,
        'seller_sku': seller_sku,
        'asin': asin,
        'barcode_guidance': guidance,
    })


def api_fba_prep_resolve_amazon_skus(session_id):
    """Repair draft counts using amazonStore only; never query MacyBol or warehouse stock."""
    conn = None
    try:
        conn = sqlite3.connect(str(ss_config.BASE_DIR / 'searchRack.db'), timeout=30.0)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        ss_fba_schema._ensure_fba_prep_tables(cur)
        conn.commit()
        cur.execute('BEGIN IMMEDIATE')
        row = cur.execute("SELECT * FROM fba_prep_sessions WHERE id = ? AND status = 'open'", (session_id,)).fetchone()
        if not row:
            return jsonify({'success': False, 'error': 'Open FBA session not found'}), 404
        items = ss_fba_schema._fba_json_list(row['items_json'])
        changed = False
        sp_context = None
        for item in items:
            if ss_fba_schema._fba_trim(item.get('seller_sku'), 120) or not ss_fba_schema._fba_trim(item.get('barcode'), 160):
                continue
            listing = ss_fba_inventory._fba_local_amazon_listing(item.get('barcode'))
            if not ss_fba_schema._fba_trim(listing.get('seller_sku'), 120):
                try:
                    if sp_context is None:
                        sp_context = ss_amazon_catalog._amazon_spapi_context()
                    credentials, _seller_id, marketplace_id, marketplace = sp_context
                    asin = ss_amazon_catalog._amazon_resolve_asin_from_upc(
                        credentials, marketplace, marketplace_id, item.get('barcode')
                    )
                    listing = ss_fba_inventory._fba_local_amazon_listing_by_asin(asin)
                except Exception as exc:
                    ss_config.logger.warning('FBA Amazon catalog-to-SKU match failed for %s: %s', item.get('barcode'), exc)
            if not ss_fba_schema._fba_trim(listing.get('seller_sku'), 120):
                continue
            item['seller_sku'] = ss_fba_schema._fba_trim(listing.get('seller_sku'), 120)
            item['asin'] = ss_fba_schema._fba_trim(listing.get('asin'), 30)
            item['title'] = ss_fba_schema._fba_trim(listing.get('title'), 500)
            item['condition'] = ss_fba_schema._fba_trim(listing.get('condition') or 'New', 80)
            item.setdefault('fba', {})['amazon_listing'] = listing
            item['fba_enablement_status'] = 'needs_enablement'
            item['fba_enablement_error'] = ''
            item['inbound_eligible'] = None
            item['inbound_error'] = ''
            changed = True
        if changed:
            cur.execute('''UPDATE fba_prep_sessions SET items_json = ?, count_revision = COALESCE(count_revision, 0) + 1,
                           updated_at = ? WHERE id = ?''', (json.dumps(items, ensure_ascii=False), ss_listing_checks._listagent_now_iso(), session_id))
        conn.commit()
        row = cur.execute('SELECT * FROM fba_prep_sessions WHERE id = ?', (session_id,)).fetchone()
        return jsonify({'success': True, 'matched': sum(bool(ss_fba_schema._fba_trim(item.get('seller_sku'), 120)) for item in items),
                        'session': ss_fba_inventory._fba_session_row_payload(row, include_items=True)})
    except Exception as exc:
        if conn is not None:
            conn.rollback()
        return jsonify({'success': False, 'error': ss_errors._safe_error(exc, 'resolve Amazon SKUs')}), 500
    finally:
        if conn is not None:
            conn.close()


def api_fba_prep_validate_inbound(session_id):
    """Migrate legacy rejection flags into the explicit FBA-enablement workflow."""
    conn = None
    try:
        conn = sqlite3.connect(str(ss_config.BASE_DIR / 'searchRack.db'), timeout=30.0)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        ss_fba_schema._ensure_fba_prep_tables(cur)
        conn.commit()
        row = cur.execute("SELECT * FROM fba_prep_sessions WHERE id = ? AND status = 'open'", (session_id,)).fetchone()
        if not row:
            return jsonify({'success': False, 'error': 'Open FBA session not found'}), 404
        raw_items = ss_fba_schema._fba_json_list(row['items_json'])
        expected_revision = int(row['count_revision'] or 0)
        items = [ss_fba_inventory._fba_session_item_payload(item) for item in raw_items if isinstance(item, dict)]
        if not ss_fba_schema._fba_trim(row['amazon_inbound_plan_id'], 80):
            ss_fba_inventory._fba_apply_inventory_offer_strategy(cur, items)
            # Backfill a few exact Amazon prep records per request. The page
            # continues in short passes, keeping large saved sessions responsive.
            prep_pending = [
                item for item in items
                if ss_fba_schema._fba_trim(item.get('seller_sku'), 255)
                and not ss_fba_schema._fba_trim((item.get('amazon_prep') or {}).get('checked_at'), 40)
            ]
            if prep_pending:
                try:
                    prep_api, prep_marketplace_id = ss_fba_shipments._fba_amazon_client()
                    prep_batch = prep_pending[:4]
                    for prep_index, item in enumerate(prep_batch):
                        item['amazon_prep'] = _fba_item_prep_details(
                            item.get('seller_sku'), api=prep_api,
                            marketplace_id=prep_marketplace_id,
                        )
                        if prep_index + 1 < len(prep_batch):
                            time.sleep(0.52)
                except Exception as prep_error:
                    ss_config.logger.warning('Amazon prep backfill could not start: %s', prep_error)
                    for item in prep_pending[:4]:
                        item['amazon_prep'] = _fba_normalize_prep_detail(
                            {}, msku=item.get('seller_sku')
                        )
                        item['amazon_prep'].update({
                            'checked': False,
                            'checked_at': ss_listing_checks._listagent_now_iso(),
                            'error': ss_fba_schema._fba_trim(ss_amazon_listing._amazon_format_spapi_error(prep_error), 500),
                        })
        now = ss_listing_checks._listagent_now_iso()
        changed = items != raw_items
        if changed:
            cur.execute('BEGIN IMMEDIATE')
            current = cur.execute("SELECT amazon_inbound_plan_id FROM fba_prep_sessions WHERE id = ? AND status = 'open'", (session_id,)).fetchone()
            if current and not ss_fba_schema._fba_trim(current['amazon_inbound_plan_id'], 80):
                cur.execute('''UPDATE fba_prep_sessions SET items_json = ?, count_revision = COALESCE(count_revision, 0) + 1,
                               updated_at = ? WHERE id = ? AND COALESCE(count_revision, 0) = ?''',
                            (json.dumps(items, ensure_ascii=False), now, session_id, expected_revision))
            conn.commit()
        row = cur.execute('SELECT * FROM fba_prep_sessions WHERE id = ?', (session_id,)).fetchone()
        items = [
            ss_fba_inventory._fba_session_item_payload(item)
            for item in ss_fba_schema._fba_json_list(row['items_json']) if isinstance(item, dict)
        ]
        failed_items = [{
            'seller_sku': ss_fba_schema._fba_trim(item.get('seller_sku'), 255),
            'barcode': ss_fba_schema._fba_trim(item.get('barcode'), 160),
            'title': ss_fba_schema._fba_trim(item.get('title'), 160),
            'error': ss_fba_schema._fba_trim(item.get('fba_enablement_error'), 700),
        } for item in items if item.get('fba_enablement_status') == 'failed']
        prep_lookup_remaining = sum(
            1 for item in items if ss_fba_schema._fba_trim(item.get('seller_sku'), 255)
            and not ss_fba_schema._fba_trim((item.get('amazon_prep') or {}).get('checked_at'), 40)
        )
        return jsonify({'success': True, 'invalid_items': failed_items,
                        'failed_items': failed_items, 'enablement': _fba_enablement_progress(items),
                        'prep_lookup_remaining': prep_lookup_remaining,
                        'session': ss_fba_inventory._fba_session_row_payload(row, include_items=True)})
    except Exception as exc:
        if conn is not None:
            conn.rollback()
        return jsonify({'success': False, 'error': ss_errors._safe_error(exc, 'validate inbound SKUs')}), 500
    finally:
        if conn is not None:
            conn.close()


def _fba_enablement_progress(items):
    priority = {'failed': 6, 'needs_safety_info': 5, 'needs_enablement': 4, 'checking': 3, 'enabling': 2, 'ready': 1, '': 0}
    by_sku = {}
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict):
            continue
        seller_sku = ss_fba_schema._fba_trim(item.get('seller_sku') or item.get('msku'), 255)
        if not seller_sku:
            continue
        status = ss_fba_schema._fba_trim(item.get('fba_enablement_status'), 40).lower()
        if status not in priority:
            status = 'needs_enablement'
        key = seller_sku.casefold()
        current = by_sku.get(key)
        if current is None or priority[status] > priority[current['status']]:
            by_sku[key] = {
                'seller_sku': seller_sku,
                'status': status,
                'error': ss_fba_schema._fba_trim(item.get('fba_enablement_error'), 700),
            }
    counts = {name: 0 for name in ('ready', 'needs_enablement', 'needs_safety_info', 'enabling', 'failed', 'checking')}
    for row in by_sku.values():
        counts[row['status']] = counts.get(row['status'], 0) + 1
    return {
        'total': len(by_sku), **counts,
        'all_ready': bool(by_sku) and counts['ready'] == len(by_sku),
        'items': list(by_sku.values()),
    }


def _fba_update_enablement_results(items, results):
    now = ss_listing_checks._listagent_now_iso()
    for item in items:
        if not isinstance(item, dict):
            continue
        key = ss_fba_schema._fba_trim(item.get('seller_sku') or item.get('msku'), 255).casefold()
        result = results.get(key)
        if result:
            _fba_apply_listing_readiness(item, result, checked_at=now)
    return items


def api_fba_prep_enable_fba(session_id):
    """Enable FBA while preserving a separate FBM offer whenever local stock remains."""
    data = request.get_json(silent=True) or {}
    if data.get('confirm') is not True:
        return jsonify({
            'success': False,
            'error': 'Confirm the inventory-aware FBA offer changes first.',
        }), 400
    requested_skus = {
        ss_fba_schema._fba_trim(value, 255).casefold()
        for value in data.get('seller_skus') or [] if ss_fba_schema._fba_trim(value, 255)
    }
    conn = None
    try:
        conn = sqlite3.connect(str(ss_config.BASE_DIR / 'searchRack.db'), timeout=30.0)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        ss_fba_schema._ensure_fba_prep_tables(cur)
        row = cur.execute("SELECT * FROM fba_prep_sessions WHERE id = ? AND status = 'open'", (session_id,)).fetchone()
        if not row:
            return jsonify({'success': False, 'error': 'Open FBA session not found'}), 404
        if ss_fba_schema._fba_trim(row['amazon_inbound_plan_id'], 80):
            return jsonify({'success': False, 'error': 'Amazon plan quantities are already locked'}), 409
        snapshot = [ss_fba_inventory._fba_session_item_payload(item) for item in ss_fba_schema._fba_json_list(row['items_json'])]
        ss_fba_inventory._fba_apply_inventory_offer_strategy(cur, snapshot)
        battery_skus = {
            ss_fba_schema._fba_trim(item.get('seller_sku') or item.get('msku'), 255).casefold()
            for item in snapshot if item.get('batteries_required')
            and ss_fba_schema._fba_trim(item.get('seller_sku') or item.get('msku'), 255)
        }
        candidates = {}
        for item in snapshot:
            current_sku = ss_fba_schema._fba_trim(item.get('seller_sku') or item.get('msku'), 255)
            if (not current_sku or current_sku.casefold() in battery_skus
                    or ss_fba_schema._fba_trim(item.get('fba_enablement_status'), 40).lower()
                    not in ('needs_enablement', 'failed', 'checking')):
                continue
            source_sku = ss_fba_schema._fba_trim(item.get('fbm_seller_sku') or current_sku, 255)
            strategy = ss_fba_schema._fba_trim(item.get('fba_offer_strategy'), 40).lower() or 'convert_existing'
            target_sku = (
                ss_fba_schema._fba_trim(item.get('proposed_fba_seller_sku'), 255)
                if strategy == 'separate_fba_sku' else current_sku
            ) or ss_fba_inventory._fba_proposed_seller_sku(source_sku)
            if requested_skus and not {
                current_sku.casefold(), source_sku.casefold(), target_sku.casefold()
            }.intersection(requested_skus):
                continue
            candidates[current_sku.casefold()] = {
                'current_sku': current_sku,
                'source_sku': source_sku,
                'target_sku': target_sku,
                'strategy': strategy,
                'local_inventory_before_fba': int(item.get('local_inventory_before_fba') or 0),
                'planned_fba_quantity': int(item.get('planned_fba_quantity') or item.get('quantity') or 0),
                'local_inventory_remaining_after_fba': int(item.get('local_inventory_remaining_after_fba') or 0),
            }
        # Protect the web worker and save progress between groups. The page
        # deliberately sends four at a time; this server cap also keeps older
        # clients from putting an entire large session into one long request.
        candidates = dict(list(candidates.items())[:6])
        if not candidates:
            return jsonify({
                'success': True, 'message': 'All scanned Seller SKUs are already FBA ready or activating.',
                'enablement': _fba_enablement_progress(snapshot),
                'session': ss_fba_inventory._fba_session_row_payload(row, include_items=True),
            })

        listings_client = _fba_listings_client()
        actions = {}
        candidate_rows = list(candidates.values())
        for index, candidate in enumerate(candidate_rows):
            current_sku = candidate['current_sku']
            source_sku = candidate['source_sku']
            target_sku = candidate['target_sku']
            try:
                readiness = _fba_listing_readiness(
                    current_sku, force_refresh=True, client=listings_client
                )
                if candidate['strategy'] == 'separate_fba_sku':
                    if len(candidate_rows) > 1:
                        time.sleep(0.22)
                    readiness = _fba_create_separate_fba_offer(
                        source_sku, target_sku, readiness, client=listings_client
                    )
                elif readiness.get('status') == 'needs_enablement':
                    if len(candidate_rows) > 1:
                        time.sleep(0.22)
                    readiness.update(_fba_patch_listing_to_fba(
                        current_sku, readiness, client=listings_client
                    ))
                actions[current_sku.casefold()] = {**candidate, 'result': readiness}
            except Exception as exc:
                ss_config.logger.warning('FBA enablement failed for %s: %s', current_sku, exc)
                actions[current_sku.casefold()] = {
                    **candidate,
                    'result': {
                        'status': 'failed', 'seller_sku': current_sku,
                        'error': ss_fba_schema._fba_trim(ss_amazon_listing._amazon_format_spapi_error(exc), 700),
                    },
                }
            if len(candidate_rows) > 1 and index < len(candidate_rows) - 1:
                time.sleep(0.22)

        cur.execute('BEGIN IMMEDIATE')
        current = cur.execute("SELECT * FROM fba_prep_sessions WHERE id = ? AND status = 'open'", (session_id,)).fetchone()
        if not current or ss_fba_schema._fba_trim(current['amazon_inbound_plan_id'], 80):
            conn.rollback()
            return jsonify({'success': False, 'error': 'The session changed while Amazon was enabling FBA'}), 409
        current_items = ss_fba_schema._fba_json_list(current['items_json'])
        now = ss_listing_checks._listagent_now_iso()
        for item in current_items:
            if not isinstance(item, dict):
                continue
            current_key = ss_fba_schema._fba_trim(item.get('seller_sku') or item.get('msku'), 255).casefold()
            action = actions.get(current_key)
            if not action:
                continue
            result = action['result']
            strategy = action['strategy']
            item['fba_offer_strategy'] = strategy
            item['local_inventory_before_fba'] = action['local_inventory_before_fba']
            item['planned_fba_quantity'] = action['planned_fba_quantity']
            item['local_inventory_remaining_after_fba'] = action['local_inventory_remaining_after_fba']
            if strategy == 'separate_fba_sku':
                item['proposed_fba_seller_sku'] = action['target_sku']
                if result.get('status') != 'failed':
                    item['fbm_seller_sku'] = action['source_sku']
                    item['seller_sku'] = action['target_sku']
                    item.setdefault('fba', {}).setdefault('amazon_listing', {})['seller_sku'] = action['target_sku']
            _fba_apply_listing_readiness(item, result, checked_at=now)
            if result.get('status') == 'enabling' and not ss_fba_schema._fba_trim(item.get('fba_activation_started_at'), 40):
                item['fba_activation_started_at'] = now
        cur.execute('''UPDATE fba_prep_sessions SET items_json = ?, count_revision = COALESCE(count_revision, 0) + 1,
                       updated_at = ? WHERE id = ?''',
                    (json.dumps(current_items, ensure_ascii=False), now, session_id))
        conn.commit()
        row = cur.execute('SELECT * FROM fba_prep_sessions WHERE id = ?', (session_id,)).fetchone()
        return jsonify({
            'success': True,
            'processed_skus': [action['current_sku'] for action in actions.values()],
            'offer_strategies': {
                'convert_existing': sum(action['strategy'] == 'convert_existing' for action in actions.values()),
                'separate_fba_sku': sum(action['strategy'] == 'separate_fba_sku' for action in actions.values()),
            },
            'enablement': _fba_enablement_progress(current_items),
            'session': ss_fba_inventory._fba_session_row_payload(row, include_items=True),
        })
    except FbaInboundValidationError as exc:
        if conn is not None:
            conn.rollback()
        return jsonify({'success': False, 'error': str(exc)}), 400
    except Exception as exc:
        if conn is not None:
            conn.rollback()
        return jsonify({'success': False, 'error': ss_errors._safe_error(exc, 'enable FBA listings')}), 502
    finally:
        if conn is not None:
            conn.close()


def api_fba_prep_resolve_fba_safety(session_id):
    """Save the default Amazon prerequisite attributes for selected non-battery items."""
    data = request.get_json(silent=True) or {}
    if data.get('confirm_not_dangerous_goods') is not True:
        return jsonify({
            'success': False,
            'error': 'Confirm the Amazon listing update first.',
        }), 400
    requested_skus = {
        ss_fba_schema._fba_trim(value, 255).casefold()
        for value in data.get('seller_skus') or [] if ss_fba_schema._fba_trim(value, 255)
    }
    conn = None
    try:
        conn = sqlite3.connect(str(ss_config.BASE_DIR / 'searchRack.db'), timeout=30.0)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        ss_fba_schema._ensure_fba_prep_tables(cur)
        row = cur.execute("SELECT * FROM fba_prep_sessions WHERE id = ? AND status = 'open'", (session_id,)).fetchone()
        if not row:
            return jsonify({'success': False, 'error': 'Open FBA session not found'}), 404
        if ss_fba_schema._fba_trim(row['amazon_inbound_plan_id'], 80):
            return jsonify({'success': False, 'error': 'Amazon plan quantities are already locked'}), 409
        snapshot = [ss_fba_inventory._fba_session_item_payload(item) for item in ss_fba_schema._fba_json_list(row['items_json'])]
        candidates = {}
        skipped_battery_skus = []
        for item in snapshot:
            seller_sku = ss_fba_schema._fba_trim(item.get('seller_sku') or item.get('msku'), 255)
            if not seller_sku or item.get('fba_enablement_status') != 'needs_safety_info':
                continue
            key = seller_sku.casefold()
            if requested_skus and key not in requested_skus:
                continue
            if item.get('batteries_required'):
                skipped_battery_skus.append(seller_sku)
                continue
            candidates[key] = seller_sku
        candidates = dict(list(candidates.items())[:6])
        if not candidates:
            message = 'No non-battery items need this Amazon listing update.'
            if skipped_battery_skus:
                message += ' Battery items require their full battery details in Amazon.'
            return jsonify({
                'success': True, 'message': message,
                'processed_skus': [],
                'skipped_battery_skus': list(dict.fromkeys(skipped_battery_skus)),
                'enablement': _fba_enablement_progress(snapshot),
                'session': ss_fba_inventory._fba_session_row_payload(row, include_items=True),
            })

        listings_client = _fba_listings_client()
        results = {}
        for index, (key, seller_sku) in enumerate(candidates.items()):
            try:
                readiness = _fba_listing_readiness(
                    seller_sku, force_refresh=True, client=listings_client
                )
                if readiness.get('status') == 'needs_safety_info':
                    readiness.update(_fba_patch_listing_no_battery_safety(
                        seller_sku, readiness, client=listings_client
                    ))
                results[key] = readiness
            except Exception as exc:
                ss_config.logger.warning('FBA prerequisite update failed for %s: %s', seller_sku, exc)
                results[key] = {
                    'status': 'needs_safety_info', 'seller_sku': seller_sku,
                    'error': ss_fba_schema._fba_trim(ss_amazon_listing._amazon_format_spapi_error(exc), 700),
                }
            if len(candidates) > 1 and index < len(candidates) - 1:
                time.sleep(0.22)

        cur.execute('BEGIN IMMEDIATE')
        current = cur.execute("SELECT * FROM fba_prep_sessions WHERE id = ? AND status = 'open'", (session_id,)).fetchone()
        if not current or ss_fba_schema._fba_trim(current['amazon_inbound_plan_id'], 80):
            conn.rollback()
            return jsonify({'success': False, 'error': 'The session changed while Amazon saved the declarations'}), 409
        current_items = ss_fba_schema._fba_json_list(current['items_json'])
        _fba_update_enablement_results(current_items, results)
        for item in current_items:
            key = ss_fba_schema._fba_trim(item.get('seller_sku') or item.get('msku'), 255).casefold()
            result = results.get(key)
            if result and result.get('status') in ('needs_enablement', 'checking', 'enabling', 'ready'):
                item['batteries_required'] = False
                item['dg_not_applicable_confirmed'] = True
        cur.execute('''UPDATE fba_prep_sessions SET items_json = ?, count_revision = COALESCE(count_revision, 0) + 1,
                       updated_at = ? WHERE id = ?''',
                    (json.dumps(current_items, ensure_ascii=False), ss_listing_checks._listagent_now_iso(), session_id))
        conn.commit()
        row = cur.execute('SELECT * FROM fba_prep_sessions WHERE id = ?', (session_id,)).fetchone()
        return jsonify({
            'success': True,
            'processed_skus': list(candidates.values()),
            'skipped_battery_skus': list(dict.fromkeys(skipped_battery_skus)),
            'enablement': _fba_enablement_progress(current_items),
            'session': ss_fba_inventory._fba_session_row_payload(row, include_items=True),
        })
    except Exception as exc:
        if conn is not None:
            conn.rollback()
        return jsonify({'success': False, 'error': ss_errors._safe_error(exc, 'save Amazon listing setup')}), 502
    finally:
        if conn is not None:
            conn.close()


def api_fba_prep_enable_fba_status(session_id):
    """Poll a small chunk of activating SKUs until Amazon assigns their FNSKUs."""
    conn = None
    try:
        conn = sqlite3.connect(str(ss_config.BASE_DIR / 'searchRack.db'), timeout=30.0)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        ss_fba_schema._ensure_fba_prep_tables(cur)
        row = cur.execute("SELECT * FROM fba_prep_sessions WHERE id = ? AND status = 'open'", (session_id,)).fetchone()
        if not row:
            return jsonify({'success': False, 'error': 'Open FBA session not found'}), 404
        items = ss_fba_schema._fba_json_list(row['items_json'])
        pending_items = {
            ss_fba_schema._fba_trim(item.get('seller_sku') or item.get('msku'), 255).casefold(): item
            for item in items if isinstance(item, dict)
            and ss_fba_schema._fba_trim(item.get('seller_sku') or item.get('msku'), 255)
        }
        pending = list(dict.fromkeys(
            ss_fba_schema._fba_trim(item.get('seller_sku') or item.get('msku'), 255)
            for item in items if isinstance(item, dict)
            and ss_fba_schema._fba_trim(item.get('fba_enablement_status'), 40).lower() == 'enabling'
            and ss_fba_schema._fba_trim(item.get('seller_sku') or item.get('msku'), 255)
        ))[:6]
        results = {}
        if pending:
            listings_client = _fba_listings_client()
            for index, seller_sku in enumerate(pending):
                try:
                    readiness = _fba_listing_readiness(
                        seller_sku, force_refresh=True, client=listings_client
                    )
                    if readiness.get('status') == 'needs_enablement':
                        saved_item = pending_items.get(seller_sku.casefold()) or {}
                        retry_count = max(0, ss_listing_settings._listingagent_parse_int(
                            saved_item.get('fba_activation_retry_count'), 0
                        ) or 0)
                        started = ss_normalization._parse_iso_utc_naive(saved_item.get('fba_activation_started_at'))
                        # fba_activation_started_at comes from _listagent_now_iso (local
                        # time). Comparing it with UTC made every wait look hours long
                        # and gave up after a single retry.
                        elapsed = (datetime.datetime.now() - started).total_seconds() if started else 0
                        if retry_count < 1:
                            # A newly created offer can become DISCOVERABLE before
                            # Amazon applies its fulfillment channel. Reapply the
                            # idempotent FBA patch once instead of waiting forever.
                            readiness.update(_fba_patch_listing_to_fba(
                                seller_sku, readiness, client=listings_client
                            ))
                            readiness['_activation_retry_count'] = retry_count + 1
                            readiness['_activation_retry_at'] = ss_listing_checks._listagent_now_iso()
                        elif elapsed < 300:
                            readiness['status'] = 'enabling'
                        else:
                            readiness['error'] = (
                                'Amazon created the SKU but has not activated its FBA channel. '
                                'Tap Set up again to retry.'
                            )
                    results[seller_sku.casefold()] = readiness
                except Exception as exc:
                    ss_config.logger.warning('FBA enablement status check failed for %s: %s', seller_sku, exc)
                if len(pending) > 1 and index < len(pending) - 1:
                    time.sleep(0.22)

        if results:
            cur.execute('BEGIN IMMEDIATE')
            current = cur.execute("SELECT * FROM fba_prep_sessions WHERE id = ? AND status = 'open'", (session_id,)).fetchone()
            if current:
                items = ss_fba_schema._fba_json_list(current['items_json'])
                _fba_update_enablement_results(items, results)
                for item in items:
                    if not isinstance(item, dict):
                        continue
                    result = results.get(ss_fba_schema._fba_trim(
                        item.get('seller_sku') or item.get('msku'), 255
                    ).casefold())
                    if not result:
                        continue
                    if result.get('_activation_retry_count') is not None:
                        item['fba_activation_retry_count'] = int(result['_activation_retry_count'])
                        item['fba_activation_retry_at'] = result.get('_activation_retry_at') or ss_listing_checks._listagent_now_iso()
                cur.execute('''UPDATE fba_prep_sessions SET items_json = ?, count_revision = COALESCE(count_revision, 0) + 1,
                               updated_at = ? WHERE id = ?''',
                            (json.dumps(items, ensure_ascii=False), ss_listing_checks._listagent_now_iso(), session_id))
            conn.commit()
        row = cur.execute('SELECT * FROM fba_prep_sessions WHERE id = ?', (session_id,)).fetchone()
        items = ss_fba_schema._fba_json_list(row['items_json']) if row else items
        return jsonify({
            'success': True, 'enablement': _fba_enablement_progress(items),
            'session': ss_fba_inventory._fba_session_row_payload(row, include_items=True),
        })
    except Exception as exc:
        if conn is not None:
            conn.rollback()
        return jsonify({'success': False, 'error': ss_errors._safe_error(exc, 'check FBA enablement')}), 502
    finally:
        if conn is not None:
            conn.close()


def _fba_normalize_prep_detail(raw, *, msku=''):
    detail = raw if isinstance(raw, dict) else {}
    raw_prep_types = detail.get('prepTypes')
    if not isinstance(raw_prep_types, list):
        raw_prep_types = detail.get('prep_types')
    prep_types = []
    for value in raw_prep_types if isinstance(raw_prep_types, list) else []:
        prep_type = ss_fba_schema._fba_trim(value, 80).upper()
        if prep_type and prep_type not in prep_types:
            prep_types.append(prep_type)
    return {
        'msku': ss_fba_schema._fba_trim(detail.get('msku') or msku, 255),
        'prep_category': ss_fba_schema._fba_trim(
            detail.get('prepCategory') or detail.get('prep_category'), 80
        ).upper(),
        'prep_types': prep_types[:30],
        'prep_owner_constraint': ss_fba_schema._fba_trim(
            detail.get('prepOwnerConstraint') or detail.get('prep_owner_constraint'), 40
        ).upper(),
        'label_owner_constraint': ss_fba_schema._fba_trim(
            detail.get('labelOwnerConstraint') or detail.get('label_owner_constraint'), 40
        ).upper(),
        'all_owners_constraint': ss_fba_schema._fba_trim(
            detail.get('allOwnersConstraint') or detail.get('all_owners_constraint'), 40
        ).upper(),
        'checked': bool(detail.get('checked', bool(detail))),
        'checked_at': ss_fba_schema._fba_trim(
            detail.get('checkedAt') or detail.get('checked_at'), 40
        ),
        'source': ss_fba_schema._fba_trim(detail.get('source') or 'amazon_sp_api', 40),
        'error': ss_fba_schema._fba_trim(detail.get('error'), 500),
    }


def _fba_item_prep_details(msku, *, api=None, marketplace_id=''):
    """Read one MSKU at a time because Amazon currently truncates multi-MSKU results."""
    seller_sku = ss_fba_schema._fba_trim(msku, 255)
    if not seller_sku:
        return _fba_normalize_prep_detail({}, msku=seller_sku)
    try:
        if api is None or not marketplace_id:
            api, marketplace_id = ss_fba_shipments._fba_amazon_client()
        payload = ss_fba_shipments._fba_amazon_payload(api.list_prep_details(
            marketplaceId=marketplace_id, mskus=[seller_sku]
        ))
        details = payload.get('mskuPrepDetails') if isinstance(payload.get('mskuPrepDetails'), list) else []
        match = next((
            row for row in details if isinstance(row, dict)
            and ss_fba_schema._fba_trim(row.get('msku'), 255).casefold() == seller_sku.casefold()
        ), None)
        normalized = _fba_normalize_prep_detail(match or {}, msku=seller_sku)
        normalized['checked'] = bool(match)
        normalized['checked_at'] = ss_listing_checks._listagent_now_iso()
        if not match:
            normalized['error'] = 'Amazon has not assigned prep instructions yet'
        return normalized
    except Exception as exc:
        ss_config.logger.warning('Amazon prep lookup failed for %s: %s', seller_sku, exc)
        normalized = _fba_normalize_prep_detail({}, msku=seller_sku)
        normalized.update({
            'checked': False,
            'checked_at': ss_listing_checks._listagent_now_iso(),
            'error': ss_fba_schema._fba_trim(ss_amazon_listing._amazon_format_spapi_error(exc), 500),
        })
        return normalized


def _fba_existing_prep_conflict(exc):
    """Extract an immutable prep assignment from Amazon's setPrepDetails rejection."""
    match = re.search(
        r'prep category and types cannot be updated for msku\s+(.+?)\s+'
        r'with existing prep category of\s+([A-Z0-9_]+)',
        str(exc or ''),
        re.IGNORECASE,
    )
    if not match:
        return None
    return {
        'msku': ss_fba_schema._fba_trim(match.group(1), 255),
        'prepCategory': ss_fba_schema._fba_trim(match.group(2), 80).upper(),
    }


def _fba_package_measurement_issues(state):
    """Extract per-MSKU package dimension/weight failures from an inbound operation."""
    workflow = state if isinstance(state, dict) else {}
    operation = workflow.get('operation') if isinstance(workflow.get('operation'), dict) else {}
    problems = operation.get('problems') if isinstance(operation.get('problems'), list) else []
    by_msku = {}
    for problem in problems:
        if not isinstance(problem, dict):
            continue
        code = ss_fba_schema._fba_trim(problem.get('code'), 80).upper()
        details = ss_fba_schema._fba_trim(problem.get('details'), 1000)
        message = ss_fba_schema._fba_trim(problem.get('message'), 1000)
        text = f'{details} {message}'.lower()
        resource = re.search(r"resource\s+['\"]([^'\"]+)['\"]", details, re.IGNORECASE)
        if not resource:
            resource = re.search(r'\bmsku\s+([A-Z0-9._:-]+)', text, re.IGNORECASE)
        msku = ss_fba_schema._fba_trim(resource.group(1) if resource else '', 255)
        missing_dimensions = code == 'FBA_INB_0004' or (
            'dimension' in text and 'manufacturer' in text
        )
        missing_weight = code == 'FBA_INB_0005' or (
            'weight' in text and 'manufacturer' in text
        )
        if not msku or not (missing_dimensions or missing_weight):
            continue
        key = msku.casefold()
        row = by_msku.setdefault(key, {
            'msku': msku, 'missing_dimensions': False, 'missing_weight': False,
        })
        row['missing_dimensions'] = row['missing_dimensions'] or missing_dimensions
        row['missing_weight'] = row['missing_weight'] or missing_weight
    return list(by_msku.values())


def _fba_positive_measurement(value, label, *, maximum=1000000):
    try:
        parsed = Decimal(str(value).strip())
    except (InvalidOperation, ValueError, TypeError, OverflowError):
        parsed = Decimal(0)
    if not parsed.is_finite() or parsed <= 0 or parsed > Decimal(str(maximum)):
        raise FbaInboundValidationError(f'Enter a valid {label} greater than zero')
    return float(parsed)


def _fba_patch_listing_package_measurements(msku, item, measurements, *, client=None):
    """Save one sellable unit's original-package measurements to its Amazon listing."""
    listings, seller_id, marketplace_id = client or _fba_listings_client()
    seller_sku = ss_fba_schema._fba_trim(msku, 255)
    product_type = ss_fba_schema._fba_trim((item or {}).get('amazon_product_type'), 120)
    if not product_type:
        response = listings.get_listings_item(
            seller_id, seller_sku, marketplaceIds=[marketplace_id],
            includedData=['summaries'],
        )
        payload = ss_fba_shipments._fba_amazon_payload(response)
        summaries = payload.get('summaries') if isinstance(payload.get('summaries'), list) else []
        product_type = ss_fba_schema._fba_trim(next((
            row.get('productType') or row.get('product_type')
            for row in summaries if isinstance(row, dict)
            and ss_fba_schema._fba_trim(row.get('productType') or row.get('product_type'), 120)
        ), ''), 120)
    if not product_type:
        raise FbaInboundValidationError(f'Amazon did not return the product type for {seller_sku}')

    length_in = _fba_positive_measurement(measurements.get('length_in'), 'package length')
    width_in = _fba_positive_measurement(measurements.get('width_in'), 'package width')
    height_in = _fba_positive_measurement(measurements.get('height_in'), 'package height')
    weight_lb = _fba_positive_measurement(measurements.get('weight_lb'), 'package weight')
    dimensions = [{
        'length': {'value': length_in, 'unit': 'inches'},
        'width': {'value': width_in, 'unit': 'inches'},
        'height': {'value': height_in, 'unit': 'inches'},
        'marketplace_id': marketplace_id,
    }]
    weight = [{
        'value': weight_lb, 'unit': 'pounds', 'marketplace_id': marketplace_id,
    }]
    body = {
        'productType': product_type,
        'patches': [{
            'op': 'add', 'path': '/attributes/item_package_dimensions',
            'value': dimensions,
        }, {
            'op': 'add', 'path': '/attributes/item_package_weight',
            'value': weight,
        }],
    }
    response = listings.patch_listings_item(
        seller_id, seller_sku, marketplaceIds=[marketplace_id],
        issueLocale='en_US', body=body,
    )
    payload = ss_fba_shipments._fba_amazon_payload(response)
    status = ss_fba_schema._fba_trim(payload.get('status'), 40).upper()
    issues = payload.get('issues') if isinstance(payload.get('issues'), list) else []
    blocking = [
        issue for issue in issues if isinstance(issue, dict)
        and ss_fba_schema._fba_trim(issue.get('severity'), 20).upper() == 'ERROR'
    ]
    if blocking or status in ('INVALID', 'ERROR', 'REJECTED', 'FAILURE'):
        detail = '; '.join(filter(None, (
            _fba_listing_issue_message(issue) for issue in blocking[:4]
        ))) or f'Amazon returned {status or "an invalid response"}'
        raise FbaInboundValidationError(f'{seller_sku}: {detail}')
    return {
        'length_in': length_in, 'width_in': width_in, 'height_in': height_in,
        'weight_lb': weight_lb, 'updated_at': ss_listing_checks._listagent_now_iso(),
        'source': 'operator', 'amazon_status': status or 'ACCEPTED',
    }


def _fba_listing_measurement_status(payload, saved, marketplace_id):
    """Confirm saved seller contributions without claiming inbound acceptance."""
    attributes = {
        key: [row for row in rows if isinstance(row, dict)
              and row.get('marketplace_id') == marketplace_id]
        for key, rows in (payload.get('attributes') or {}).items()
        if isinstance(rows, list)
    }
    package = _fba_catalog_measurement_reference_from_payload({'attributes': attributes})['package']
    errors = [_fba_listing_issue_message(issue) for issue in payload.get('issues', [])
              if isinstance(issue, dict) and str(issue.get('severity', '')).upper() == 'ERROR']
    factors = {'inches': 1, 'centimeters': 1 / 2.54, 'millimeters': 1 / 25.4,
               'pounds': 1, 'ounces': 1 / 16, 'kilograms': 2.20462262185, 'grams': .00220462262185}
    matches = []
    for name, field in [('length', 'length_in'), ('width', 'width_in'),
                        ('height', 'height_in'), ('weight', 'weight_lb')]:
        measurement = package.get(name, {})
        unit = measurement.get('unit')
        allowed = ('pounds', 'ounces', 'kilograms', 'grams') if name == 'weight' else ('inches', 'centimeters', 'millimeters')
        try:
            expected = float(saved.get(field) or 0)
            actual = float(measurement.get('value') or 0) * factors.get(unit, 0)
            matches.append(unit in allowed and expected > 0 and math.isclose(actual, expected, rel_tol=.001, abs_tol=.00001))
        except (TypeError, ValueError, OverflowError):
            matches.append(False)
    return {'listing_ready': all(matches) and not errors, 'listing_errors': errors,
            'listing_package': package}


def _fba_catalog_measurement_reference_from_payload(payload, marketplace_id=''):
    """Normalize Amazon catalog item/package measurements for operator guidance."""
    data = payload if isinstance(payload, dict) else {}
    attributes = data.get('attributes') if isinstance(data.get('attributes'), dict) else {}
    result = {'item': {}, 'package': {}}

    def normalize_measurement(value):
        value = value if isinstance(value, dict) else {}
        try:
            number = float(value.get('value'))
        except (TypeError, ValueError, OverflowError):
            return {}
        if not math.isfinite(number) or number <= 0:
            return {}
        return {'value': number, 'unit': ss_fba_schema._fba_trim(value.get('unit'), 30).lower()}

    def merge_dimensions(target, raw):
        raw = raw if isinstance(raw, dict) else {}
        for name in ('length', 'width', 'height', 'weight'):
            normalized = normalize_measurement(raw.get(name))
            if normalized and name not in target:
                target[name] = normalized

    dimensions = data.get('dimensions') if isinstance(data.get('dimensions'), list) else []
    selected = next((
        row for row in dimensions if isinstance(row, dict)
        and (not marketplace_id or ss_fba_schema._fba_trim(row.get('marketplaceId'), 40) == marketplace_id)
    ), next((row for row in dimensions if isinstance(row, dict)), {}))
    merge_dimensions(result['item'], selected.get('item'))
    merge_dimensions(result['package'], selected.get('package'))

    for key in ('item_dimensions', 'item_width_height'):
        rows = attributes.get(key) if isinstance(attributes.get(key), list) else []
        if rows:
            merge_dimensions(result['item'], rows[0])
    item_weight = attributes.get('item_weight') if isinstance(attributes.get('item_weight'), list) else []
    if item_weight and 'weight' not in result['item']:
        normalized = normalize_measurement(item_weight[0])
        if normalized:
            result['item']['weight'] = normalized

    package_dimensions = (
        attributes.get('item_package_dimensions')
        if isinstance(attributes.get('item_package_dimensions'), list) else []
    )
    if package_dimensions:
        merge_dimensions(result['package'], package_dimensions[0])
    package_weight = (
        attributes.get('item_package_weight')
        if isinstance(attributes.get('item_package_weight'), list) else []
    )
    if package_weight and 'weight' not in result['package']:
        normalized = normalize_measurement(package_weight[0])
        if normalized:
            result['package']['weight'] = normalized
    result['package_ready'] = all(
        name in result['package'] for name in ('length', 'width', 'height', 'weight')
    )
    return result


def _fba_catalog_measurement_reference(asin, *, client=None):
    credentials, _seller_id, marketplace_id, marketplace = ss_amazon_catalog._amazon_spapi_context()
    if marketplace is None:
        raise FbaInboundValidationError('Amazon SP-API marketplace is not available')
    if client is None:
        from sp_api.api import CatalogItems
        client = CatalogItems(
            credentials=credentials, marketplace=marketplace, version='2022-04-01'
        )
    response = client.get_catalog_item(
        ss_fba_schema._fba_trim(asin, 30), marketplaceIds=[marketplace_id],
        includedData=['dimensions', 'attributes', 'summaries'],
    )
    payload = ss_fba_shipments._fba_amazon_payload(response)
    reference = _fba_catalog_measurement_reference_from_payload(payload, marketplace_id)
    reference.update({'asin': ss_fba_schema._fba_trim(asin, 30), 'checked_at': ss_listing_checks._listagent_now_iso()})
    return reference
