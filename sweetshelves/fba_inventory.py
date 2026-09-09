"""Fba inventory for Sweet Shelves."""

import re as _re
import sqlite3
import datetime
from . import fba_shipments as ss_fba_shipments
from . import config as ss_config, database as ss_database, fba_readiness as ss_fba_readiness, fba_schema as ss_fba_schema, listing_lifecycle as ss_listing_lifecycle, listing_settings as ss_listing_settings, normalization as ss_normalization, warehouse_locations as ss_warehouse_locations


_FBA_NAMED_BIN_RE = _re.compile(
    r'^(?P<parent>(?:or\d+s\d+|omr\d+s\d+|ofloor\d+|gr\d+s\d+|gmid\d+|gfloor\d+|misc\d+))b\d+$',
    _re.IGNORECASE,
)


_FBA_GENERIC_BIN_RE = _re.compile(r'^(?P<parent>.+?)(?:[-_. ]?b)(?P<number>\d+)$', _re.IGNORECASE)


def _fba_bin_parent_code(code, known_location_keys=None):
    value = str(code or '').strip()
    if not value:
        return ''
    named_match = _FBA_NAMED_BIN_RE.fullmatch(value)
    if named_match:
        return str(named_match.group('parent') or '').strip()
    generic_match = _FBA_GENERIC_BIN_RE.fullmatch(value)
    if not generic_match:
        return ''
    parent = str(generic_match.group('parent') or '').strip()
    known = known_location_keys or set()
    return parent if parent.casefold() in known else ''


def _fba_working_locations_payload(value):
    rows = value if isinstance(value, list) else ss_fba_schema._fba_json_list(value)
    locations = []
    seen = set()
    for raw_location in rows[:100]:
        if isinstance(raw_location, dict):
            code = ss_fba_schema._fba_trim(raw_location.get('code'), 120)
            active = bool(raw_location.get('active', True))
            items_count = max(0, ss_listing_settings._listingagent_parse_int(raw_location.get('items_count'), 0) or 0)
            total_units = max(0, ss_listing_settings._listingagent_parse_int(raw_location.get('total_units'), 0) or 0)
        else:
            code = ss_fba_schema._fba_trim(raw_location, 120)
            active = True
            items_count = 0
            total_units = 0
        key = code.casefold()
        if not code or key in seen:
            continue
        seen.add(key)
        locations.append({
            'code': code,
            'active': active,
            'items_count': items_count,
            'total_units': total_units,
        })
    return locations


def _fba_session_item_payload(raw_item):
    raw = raw_item if isinstance(raw_item, dict) else {}
    quantity = ss_normalization._strict_inventory_quantity(raw.get('quantity'))
    if quantity is None or quantity <= 0:
        quantity = 1
    total_available = ss_normalization._strict_inventory_quantity(raw.get('total_available'))
    if total_available is None or total_available < 0:
        total_available = 0

    locations = []
    for raw_location in (raw.get('locations') if isinstance(raw.get('locations'), list) else [])[:100]:
        if not isinstance(raw_location, dict):
            continue
        code = ss_fba_schema._fba_trim(raw_location.get('code'), 120)
        if not code:
            continue
        location_quantity = ss_normalization._strict_inventory_quantity(raw_location.get('quantity'))
        locations.append({
            'location_key': ss_fba_schema._fba_trim(raw_location.get('location_key') or code.casefold(), 160),
            'code': code,
            'quantity': max(0, location_quantity or 0),
            'preview_key': ss_fba_schema._fba_trim(raw_location.get('preview_key') or code, 160),
        })

    selected_locations = []
    for value in (raw.get('selectedLocations') if isinstance(raw.get('selectedLocations'), list) else [])[:100]:
        key = ss_fba_schema._fba_trim(value, 160)
        if key and key not in selected_locations:
            selected_locations.append(key)

    fba_context = raw.get('fba') if isinstance(raw.get('fba'), dict) else {}
    amazon_listing = fba_context.get('amazon_listing') if isinstance(fba_context.get('amazon_listing'), dict) else {}
    barcode_guidance = fba_context.get('barcode_guidance') if isinstance(fba_context.get('barcode_guidance'), dict) else {}
    clean_fba = {
        'listed_ebay': bool(fba_context.get('listed_ebay')),
        'listed_amazon': bool(fba_context.get('listed_amazon')),
        'listing_links': (fba_context.get('listing_links') if isinstance(fba_context.get('listing_links'), list) else [])[:40],
        'amazon_listing': {
            'asin': ss_fba_schema._fba_trim(amazon_listing.get('asin'), 30),
            'seller_sku': ss_fba_schema._fba_trim(amazon_listing.get('seller_sku'), 120),
            'condition': ss_fba_schema._fba_trim(amazon_listing.get('condition'), 80),
            'fulfillment_channel': ss_fba_schema._fba_trim(amazon_listing.get('fulfillment_channel'), 40),
        },
        'barcode_guidance': {
            'status': ss_fba_schema._fba_trim(barcode_guidance.get('status'), 40),
            'label': ss_fba_schema._fba_trim(barcode_guidance.get('label'), 120),
            'detail': ss_fba_schema._fba_trim(barcode_guidance.get('detail'), 500),
            'instruction': ss_fba_schema._fba_trim(barcode_guidance.get('instruction'), 80),
            'checked': bool(barcode_guidance.get('checked')),
            'source': ss_fba_schema._fba_trim(barcode_guidance.get('source'), 40),
            'identifier_type': ss_fba_schema._fba_trim(barcode_guidance.get('identifier_type'), 30),
            'identifier': ss_fba_schema._fba_trim(barcode_guidance.get('identifier'), 160),
        },
    }
    raw_enablement_status = ss_fba_schema._fba_trim(raw.get('fba_enablement_status'), 40).lower()
    raw_enablement_error = ss_fba_schema._fba_trim(raw.get('fba_enablement_error'), 700)
    # Sessions saved before the safety-answer workflow treated Amazon's two
    # answerable compliance questions as a permanent rejection. Migrate those
    # rows as they are read so opening an existing session immediately turns
    # them amber instead of leaving them in the red/remove pile.
    if raw_enablement_status == 'failed' and ss_fba_readiness._fba_error_is_missing_safety_info(raw_enablement_error):
        raw_enablement_status = 'needs_safety_info'
    # A previous version saved the worker's confirmation but could leave the
    # local row stuck on Amazon's pre-patch safety issue. Once both local
    # answers are confirmed, advance it to the normal live FBA recheck. The
    # enablement endpoint still verifies Amazon before changing fulfillment.
    if (raw_enablement_status == 'needs_safety_info'
            and ss_normalization._coerce_bool(raw.get('dg_not_applicable_confirmed'))
            and not ss_normalization._coerce_bool(raw.get('batteries_required'))):
        raw_enablement_status = 'needs_enablement'
        if ss_fba_readiness._fba_error_is_missing_safety_info(raw_enablement_error):
            raw_enablement_error = ''
    if raw_enablement_status not in ('ready', 'needs_enablement', 'needs_safety_info', 'enabling', 'failed', 'checking'):
        if raw.get('inbound_eligible') is True:
            raw_enablement_status = 'ready'
        elif raw.get('inbound_eligible') is False:
            # Older sessions used "inbound unavailable" for Seller SKUs that merely
            # had not been converted to FBA yet. Preserve the item and migrate it.
            raw_enablement_status = 'needs_enablement'
        elif ss_fba_schema._fba_trim(raw.get('seller_sku'), 120):
            raw_enablement_status = 'needs_enablement'
        else:
            raw_enablement_status = ''

    return {
        'barcode': ss_fba_schema._fba_trim(raw.get('barcode'), 160),
        'barcode_display': ss_fba_schema._fba_trim(raw.get('barcode_display') or raw.get('barcode'), 160),
        'barcode_key': ss_fba_schema._fba_trim(raw.get('barcode_key') or ss_warehouse_locations._movelocation_barcode_key(raw.get('barcode')), 160),
        'title': ss_fba_schema._fba_trim(raw.get('title'), 500),
        'image': ss_fba_schema._fba_trim(raw.get('image'), 1500),
        'inventory_found': bool(raw.get('inventory_found', bool(locations))),
        'macy_bol_found': bool(raw.get('macy_bol_found')),
        'macy_bol': {
            'upc': ss_fba_schema._fba_trim((raw.get('macy_bol') or {}).get('upc'), 160),
            'title': ss_fba_schema._fba_trim((raw.get('macy_bol') or {}).get('title'), 500),
            'image': ss_fba_schema._fba_trim((raw.get('macy_bol') or {}).get('image'), 1500),
        } if isinstance(raw.get('macy_bol'), dict) else {},
        'total_available': total_available,
        'locations': locations,
        'selectedLocations': selected_locations,
        'quantity': quantity,
        'fba': clean_fba,
        'asin': ss_fba_schema._fba_trim(raw.get('asin'), 30),
        'seller_sku': ss_fba_schema._fba_trim(raw.get('seller_sku'), 120),
        'fnsku': ss_fba_schema._fba_trim(raw.get('fnsku'), 80),
        'condition': ss_fba_schema._fba_trim(raw.get('condition'), 80),
        'prep_type': ss_fba_schema._fba_trim(raw.get('prep_type') or 'none', 40).lower(),
        'label_owner': ss_fba_schema._fba_trim(raw.get('label_owner') or 'SELLER', 20).upper(),
        'expiration_date': ss_fba_schema._fba_trim(raw.get('expiration_date'), 10),
        'box_number': ss_fba_schema._fba_trim(raw.get('box_number'), 40),
        'notes': ss_fba_schema._fba_trim(raw.get('notes'), 1000),
        'inbound_eligible': True if raw_enablement_status == 'ready' else None,
        'inbound_error': '',
        'inbound_checked_at': ss_fba_schema._fba_trim(raw.get('inbound_checked_at'), 40),
        'fba_enablement_status': raw_enablement_status,
        'fba_enablement_error': raw_enablement_error,
        'fba_listing_notes': ss_fba_schema._fba_trim(raw.get('fba_listing_notes'), 700),
        'fba_activation_note': ss_fba_schema._fba_trim(raw.get('fba_activation_note'), 500),
        'fba_enablement_checked_at': ss_fba_schema._fba_trim(raw.get('fba_enablement_checked_at'), 40),
        'amazon_fnsku': ss_fba_schema._fba_trim(raw.get('amazon_fnsku') or raw.get('fnsku'), 80),
        'amazon_product_type': ss_fba_schema._fba_trim(raw.get('amazon_product_type'), 120),
        'amazon_prep': ss_fba_readiness._fba_normalize_prep_detail(
            raw.get('amazon_prep'), msku=raw.get('seller_sku')
        ) if isinstance(raw.get('amazon_prep'), dict) else {},
        'amazon_package_measurements': {
            'length_in': ss_listing_settings._listingagent_parse_float((raw.get('amazon_package_measurements') or {}).get('length_in'), 0.0),
            'width_in': ss_listing_settings._listingagent_parse_float((raw.get('amazon_package_measurements') or {}).get('width_in'), 0.0),
            'height_in': ss_listing_settings._listingagent_parse_float((raw.get('amazon_package_measurements') or {}).get('height_in'), 0.0),
            'weight_lb': ss_listing_settings._listingagent_parse_float((raw.get('amazon_package_measurements') or {}).get('weight_lb'), 0.0),
            'updated_at': ss_fba_schema._fba_trim((raw.get('amazon_package_measurements') or {}).get('updated_at'), 40),
            'source': ss_fba_schema._fba_trim((raw.get('amazon_package_measurements') or {}).get('source'), 40),
            'amazon_status': ss_fba_schema._fba_trim((raw.get('amazon_package_measurements') or {}).get('amazon_status'), 40),
        } if isinstance(raw.get('amazon_package_measurements'), dict) else {},
        'batteries_required': ss_normalization._coerce_bool(raw.get('batteries_required')),
        'dg_not_applicable_confirmed': bool(raw.get('dg_not_applicable_confirmed')),
        'fbm_seller_sku': ss_fba_schema._fba_trim(raw.get('fbm_seller_sku'), 255),
        'fba_offer_strategy': ss_fba_schema._fba_trim(raw.get('fba_offer_strategy'), 40).lower(),
        'proposed_fba_seller_sku': ss_fba_schema._fba_trim(raw.get('proposed_fba_seller_sku'), 255),
        'local_inventory_before_fba': max(0, ss_listing_settings._listingagent_parse_int(raw.get('local_inventory_before_fba'), 0) or 0),
        'planned_fba_quantity': max(0, ss_listing_settings._listingagent_parse_int(raw.get('planned_fba_quantity'), quantity) or quantity),
        'local_inventory_remaining_after_fba': max(0, ss_listing_settings._listingagent_parse_int(raw.get('local_inventory_remaining_after_fba'), 0) or 0),
        'fba_activation_started_at': ss_fba_schema._fba_trim(raw.get('fba_activation_started_at'), 40),
        'fba_activation_retry_at': ss_fba_schema._fba_trim(raw.get('fba_activation_retry_at'), 40),
        'fba_activation_retry_count': max(0, ss_listing_settings._listingagent_parse_int(raw.get('fba_activation_retry_count'), 0) or 0),
    }


def _fba_plan_item_signature(items):
    """Fields that Amazon freezes when an inbound plan is created."""
    signature = []
    for raw in items if isinstance(items, list) else []:
        if not isinstance(raw, dict):
            continue
        signature.append((
            ss_fba_schema._fba_trim(raw.get('seller_sku') or raw.get('msku'), 255).casefold(),
            int(ss_normalization._strict_inventory_quantity(raw.get('quantity')) or 0),
            ss_fba_schema._fba_trim(raw.get('expiration_date') or raw.get('expiration'), 10),
        ))
    return sorted(signature)


def _fba_session_row_payload(row, *, include_items=False):
    data = dict(row) if row else {}
    working_locations = _fba_working_locations_payload(data.pop('working_locations_json', '[]'))
    amazon_workflow = ss_fba_schema._fba_json_dict(data.pop('amazon_state_json', '{}'))
    amazon_workflow.setdefault('stage', str(data.get('amazon_stage') or 'draft'))
    amazon_workflow.setdefault('inbound_plan_id', str(data.get('amazon_inbound_plan_id') or ''))
    data['working_location_count'] = len(working_locations)
    rejected_items = ss_fba_schema._fba_json_list(data.pop('rejected_items_json', '[]'))
    if include_items:
        data['rejected_items'] = rejected_items
        data['items'] = ss_fba_schema._fba_json_list(data.pop('items_json', '[]'))
        approval_issues = ss_fba_shipments._fba_plan_approval_issues(amazon_workflow)
        for item in data['items']:
            error = approval_issues.get(str(item.get('seller_sku') or '').casefold())
            if error:
                item.update(fba_enablement_status='failed', fba_enablement_error=error,
                            inbound_eligible=False, inbound_error=error)
        data['working_locations'] = working_locations
        data['amazon_workflow'] = amazon_workflow
    else:
        data.pop('items_json', None)
        data['amazon_summary'] = {
            'stage': amazon_workflow.get('stage') or 'draft',
            'inbound_plan_id': amazon_workflow.get('inbound_plan_id') or '',
            'box_count': len(amazon_workflow.get('boxes') or []),
        }
    return data


def _fba_inventory_state_for_barcode(cur, barcode):
    """Return live units and shelf/bin totals for one normalized inventory barcode."""
    barcode_key = ss_warehouse_locations._movelocation_barcode_key(barcode)
    empty = {'quantity': 0, 'locations': []}
    if not barcode_key:
        return empty

    cur.execute("PRAGMA table_info('SEARCHRACK')")
    columns = [row[1] for row in cur.fetchall()]
    lower = {str(column).lower(): column for column in columns}
    barcode_col = lower.get('barcode') or lower.get('upc')
    qty_col = lower.get('quantity') or lower.get('qty')
    pos_col = lower.get('item_position') or lower.get('itemposition') or lower.get('position')
    pic_col = lower.get('pictureposition')
    if not barcode_col:
        return empty

    variants = sorted(v.lower() for v in ss_warehouse_locations._movelocation_barcode_variants(barcode))
    if not variants:
        return empty
    placeholders = ','.join('?' for _ in variants)
    active_clause = (
        f'COALESCE(CAST({ss_database._sqlite_ident(qty_col)} AS INTEGER), 0) > 0'
        if qty_col else '1 = 1'
    )
    cur.execute(f'''
        SELECT rowid AS _rowid_, *
        FROM SEARCHRACK
        WHERE LOWER(TRIM({ss_database._sqlite_ident(barcode_col)})) IN ({placeholders})
          AND {active_clause}
    ''', tuple(variants))

    location_map = {}
    total = 0
    for raw_row in cur.fetchall():
        row = dict(raw_row)
        if ss_warehouse_locations._movelocation_barcode_key(row.get(barcode_col)) != barcode_key:
            continue
        qty = max(0, ss_warehouse_locations._movelocation_parse_qty(row.get(qty_col) if qty_col else None, default=1))
        if qty <= 0:
            continue
        code, _pos, _pic, preview = ss_warehouse_locations._movelocation_row_location(row, pos_col=pos_col, pic_col=pic_col)
        code = str(code or '').strip() or 'Unassigned'
        key = code.casefold()
        bucket = location_map.setdefault(key, {
            'code': code,
            'preview_key': str(preview or code).strip(),
            'quantity': 0,
        })
        bucket['quantity'] += qty
        total += qty
    return {
        'quantity': int(total),
        'locations': sorted(
            location_map.values(),
            key=lambda row: (-int(row.get('quantity') or 0), str(row.get('code') or '').casefold())
        )
    }


def _fba_proposed_seller_sku(source_sku):
    """Stable companion SKU used when one ASIN must retain both FBM and FBA offers."""
    source = ss_fba_schema._fba_trim(source_sku, 255)
    if not source:
        return ''
    if source.upper().endswith('-FBA'):
        return ss_fba_schema._fba_trim(source + '-2', 255)
    return ss_fba_schema._fba_trim(source[:251] + '-FBA', 255)


def _fba_apply_inventory_offer_strategy(cur, items):
    """Annotate each Seller SKU with convert-vs-separate based on live local stock."""
    groups = {}
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict):
            continue
        target_sku = ss_fba_schema._fba_trim(item.get('seller_sku') or item.get('msku'), 255)
        source_sku = ss_fba_schema._fba_trim(item.get('fbm_seller_sku') or target_sku, 255)
        if not source_sku:
            continue
        bucket = groups.setdefault(source_sku.casefold(), {
            'source_sku': source_sku, 'planned': 0, 'barcodes': {}, 'items': [],
        })
        bucket['planned'] += max(0, ss_listing_settings._listingagent_parse_int(item.get('quantity'), 0) or 0)
        barcode = ss_fba_schema._fba_trim(item.get('barcode'), 160)
        barcode_key = ss_warehouse_locations._movelocation_barcode_key(barcode)
        if barcode and barcode_key and barcode_key not in bucket['barcodes']:
            bucket['barcodes'][barcode_key] = barcode
        bucket['items'].append(item)

    for bucket in groups.values():
        local_qty = 0
        for barcode in bucket['barcodes'].values():
            local_qty += int(_fba_inventory_state_for_barcode(cur, barcode).get('quantity') or 0)
        planned = int(bucket['planned'] or 0)
        remaining = max(0, local_qty - planned)
        existing_strategy = next((
            ss_fba_schema._fba_trim(item.get('fba_offer_strategy'), 40).lower()
            for item in bucket['items']
            if ss_fba_schema._fba_trim(item.get('fba_offer_strategy'), 40).lower() in ('convert_existing', 'separate_fba_sku')
            and (
                ss_fba_schema._fba_trim(item.get('fbm_seller_sku'), 255)
                or ss_fba_schema._fba_trim(item.get('fba_enablement_status'), 40).lower() in ('enabling', 'ready')
            )
        ), '')
        strategy = existing_strategy or ('separate_fba_sku' if remaining > 0 else 'convert_existing')
        proposed = next((
            ss_fba_schema._fba_trim(item.get('proposed_fba_seller_sku'), 255)
            for item in bucket['items'] if ss_fba_schema._fba_trim(item.get('proposed_fba_seller_sku'), 255)
        ), '')
        if strategy == 'separate_fba_sku' and not proposed:
            proposed = _fba_proposed_seller_sku(bucket['source_sku'])
        for item in bucket['items']:
            item['local_inventory_before_fba'] = local_qty
            item['planned_fba_quantity'] = planned
            item['local_inventory_remaining_after_fba'] = remaining
            item['fba_offer_strategy'] = strategy
            item['proposed_fba_seller_sku'] = proposed if strategy == 'separate_fba_sku' else ''
    return items


def _fba_marketplace_status_for_barcodes(barcodes):
    cleaned = []
    seen = set()
    for value in barcodes or []:
        barcode = str(value or '').strip()
        key = ss_warehouse_locations._movelocation_barcode_key(barcode)
        if not barcode or not key or key in seen:
            continue
        seen.add(key)
        cleaned.append(barcode)

    live = ss_listing_lifecycle._fetch_live_marketplace_upc_keys(cleaned)
    link_map = ss_listing_lifecycle._fetch_auto_marketplace_listing_links(cleaned)
    result = {}
    for barcode in cleaned:
        links = []
        link_seen = set()
        for variant in ss_listing_lifecycle._marketplace_upc_lookup_variants(barcode):
            bucket = link_map.get(variant) or {}
            for platform in ('ebay', 'amazon'):
                for link in bucket.get(platform) or []:
                    token = (
                        str(link.get('platform') or platform).casefold(),
                        str(link.get('listing_id') or '').casefold(),
                        str(link.get('url') or '').casefold(),
                    )
                    if token in link_seen:
                        continue
                    link_seen.add(token)
                    links.append(link)
        result[ss_warehouse_locations._movelocation_barcode_key(barcode)] = {
            'listed_ebay': ss_listing_lifecycle._is_marketplace_live_for_upc(barcode, live.get('ebay') or set()),
            'listed_amazon': ss_listing_lifecycle._is_marketplace_live_for_upc(barcode, live.get('amazon') or set()),
            'ebay_checked': bool(live.get('ebay_checked')),
            'amazon_checked': bool(live.get('amazon_checked')),
            'links': links,
        }
    return result


def _fba_remember_replacement_listing(barcode, asin, old_sku, new_sku, *, fulfillment_channel='', status='Active'):
    """Point the local Amazon listing cache at the seller's current SKU after a relisting."""
    old = ss_fba_schema._fba_trim(old_sku, 255)
    new = ss_fba_schema._fba_trim(new_sku, 255)
    asin = ss_fba_schema._fba_trim(asin, 30).upper()
    if not new:
        return False
    conn = None
    try:
        conn = sqlite3.connect(str(ss_config.BASE_DIR / 'amazonStore.db'), timeout=10.0)
        now = datetime.datetime.now(datetime.timezone.utc).isoformat().replace('+00:00', 'Z')
        cur = conn.execute(
            """UPDATE ITEMS SET SKU = ?, STATUS = ?, FULFILLMENT_CHANNEL = ?, LAST_UPDATED = ?
               WHERE TRIM(COALESCE(SKU, '')) = ? COLLATE NOCASE""",
            (new, status, fulfillment_channel, now, old),
        )
        if not cur.rowcount:
            conn.execute(
                """INSERT INTO ITEMS (ASIN, SKU, TITLE, PRICE, QUANTITY, STATUS, IMAGE, UPC, CONDITION,
                                      CONDITION_NOTE, FULFILLMENT_CHANNEL, LAST_UPDATED)
                   VALUES (?, ?, '', 0, 0, ?, '', ?, '', '', ?, ?)""",
                (asin, new, status, ss_fba_schema._fba_trim(barcode, 40), fulfillment_channel, now),
            )
        conn.commit()
        return True
    except Exception as exc:
        ss_config.logger.warning('Could not update the local Amazon listing cache for %s -> %s: %s', old, new, exc)
        return False
    finally:
        if conn is not None:
            conn.close()


def _fba_local_amazon_listing(barcode, *, strict=False):
    variants = ss_listing_lifecycle._marketplace_upc_lookup_variants(barcode)
    if not variants:
        return {}
    conn = None
    try:
        conn = sqlite3.connect(str(ss_config.BASE_DIR / 'amazonStore.db'), timeout=10.0)
        conn.row_factory = sqlite3.Row
        placeholders = ','.join('?' for _ in variants)
        row = conn.execute(f'''
            SELECT ASIN, SKU, TITLE, CONDITION, FULFILLMENT_CHANNEL, STATUS, QUANTITY, LAST_UPDATED
            FROM ITEMS
            WHERE LOWER(TRIM(COALESCE(UPC, ''))) IN ({placeholders})
              AND TRIM(COALESCE(SKU, '')) != ''
            ORDER BY
                CASE WHEN LOWER(TRIM(COALESCE(STATUS, ''))) IN ('active', 'live', 'listed') THEN 0 ELSE 1 END,
                COALESCE(LAST_UPDATED, '') DESC,
                ID DESC
            LIMIT 1
        ''', tuple(variants)).fetchone()
        if not row:
            return {}
        return {
            'asin': str(row['ASIN'] or '').strip(),
            'seller_sku': str(row['SKU'] or '').strip(),
            'title': str(row['TITLE'] or '').strip(),
            'condition': str(row['CONDITION'] or '').strip(),
            'fulfillment_channel': str(row['FULFILLMENT_CHANNEL'] or '').strip(),
            'status': str(row['STATUS'] or '').strip(),
            'quantity': max(0, ss_warehouse_locations._movelocation_parse_qty(row['QUANTITY'], default=0)),
        }
    except Exception as exc:
        ss_config.logger.warning('FBA local Amazon lookup failed for %s: %s', barcode, exc)
        if strict:
            raise
        return {}
    finally:
        if conn is not None:
            conn.close()


def _fba_local_amazon_listing_by_asin(asin, *, strict=False):
    asin = ss_fba_schema._fba_trim(asin, 30).upper()
    if not asin:
        return {}
    conn = None
    try:
        conn = sqlite3.connect(str(ss_config.BASE_DIR / 'amazonStore.db'), timeout=10.0)
        conn.row_factory = sqlite3.Row
        row = conn.execute('''
            SELECT ASIN, SKU, TITLE, CONDITION, FULFILLMENT_CHANNEL, STATUS, QUANTITY, LAST_UPDATED
            FROM ITEMS
            WHERE UPPER(TRIM(COALESCE(ASIN, ''))) = ?
              AND TRIM(COALESCE(SKU, '')) != ''
            ORDER BY
                CASE WHEN LOWER(TRIM(COALESCE(STATUS, ''))) IN ('active', 'live', 'listed') THEN 0 ELSE 1 END,
                COALESCE(LAST_UPDATED, '') DESC, ID DESC
            LIMIT 1
        ''', (asin,)).fetchone()
        if not row:
            return {}
        return {
            'asin': str(row['ASIN'] or '').strip(),
            'seller_sku': str(row['SKU'] or '').strip(),
            'title': str(row['TITLE'] or '').strip(),
            'condition': str(row['CONDITION'] or '').strip(),
            'fulfillment_channel': str(row['FULFILLMENT_CHANNEL'] or '').strip(),
            'status': str(row['STATUS'] or '').strip(),
            'quantity': max(0, ss_warehouse_locations._movelocation_parse_qty(row['QUANTITY'], default=0)),
        }
    except Exception as exc:
        ss_config.logger.warning('FBA local Amazon ASIN lookup failed for %s: %s', asin, exc)
        if strict:
            raise
        return {}
    finally:
        if conn is not None:
            conn.close()


def _fba_review_reasons(*, remaining_quantity=0, listed_ebay=False, listed_amazon=False):
    reasons = []
    if int(remaining_quantity or 0) > 0:
        reasons.append('Inventory remains in the warehouse')
    if listed_ebay:
        reasons.append('Active eBay listing')
    if listed_amazon:
        reasons.append('Active Amazon listing')
    return reasons


def _fba_batch_payload(conn, batch_id, *, include_dynamic=True):
    from urllib.parse import quote

    cur = conn.cursor()
    ss_fba_schema._ensure_fba_prep_tables(cur)
    batch_row = cur.execute('SELECT * FROM fba_prep_batches WHERE id = ?', (batch_id,)).fetchone()
    if not batch_row:
        return None
    batch = dict(batch_row)
    item_rows = cur.execute('''
        SELECT i.*, r.id AS review_id, r.status AS review_status,
               r.reviewer_note, r.reviewed_at
        FROM fba_prep_items i
        LEFT JOIN fba_listing_reviews r ON r.fba_item_id = i.id
        WHERE i.batch_id = ?
        ORDER BY i.item_index, i.id
    ''', (batch_id,)).fetchall()
    items = [dict(row) for row in item_rows]

    dynamic_marketplaces = {}
    if include_dynamic and items:
        dynamic_marketplaces = _fba_marketplace_status_for_barcodes([row.get('barcode') for row in items])

    for item in items:
        item['source_locations'] = ss_fba_schema._fba_json_list(item.pop('source_locations_json', '[]'))
        item['remaining_locations'] = ss_fba_schema._fba_json_list(item.pop('remaining_locations_json', '[]'))
        item['listing_links'] = ss_fba_schema._fba_json_list(item.pop('listing_links_json', '[]'))
        if include_dynamic:
            inventory = _fba_inventory_state_for_barcode(cur, item.get('barcode'))
            marketplace = dynamic_marketplaces.get(ss_warehouse_locations._movelocation_barcode_key(item.get('barcode'))) or {}
            item['remaining_inventory_qty'] = inventory['quantity']
            item['remaining_locations'] = inventory['locations']
            if marketplace.get('ebay_checked'):
                item['listed_ebay'] = 1 if marketplace.get('listed_ebay') else 0
            if marketplace.get('amazon_checked'):
                item['listed_amazon'] = 1 if marketplace.get('listed_amazon') else 0
            if marketplace.get('links'):
                item['listing_links'] = marketplace['links']
        item['review_reasons'] = _fba_review_reasons(
            remaining_quantity=item.get('remaining_inventory_qty'),
            listed_ebay=bool(item.get('listed_ebay')),
            listed_amazon=bool(item.get('listed_amazon')),
        )
        item['listingagent_url'] = '/listingagent?upc=' + quote(str(item.get('barcode') or '').strip())
    batch['items'] = items
    return batch



