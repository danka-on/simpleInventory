"""Shipping labels for Sweet Shelves."""

import base64
import datetime
import gzip
import json
import os
import re as _re
import requests
import sqlite3
from flask import jsonify, request, send_file, url_for
from . import (
    amazon_catalog as ss_amazon_catalog, config as ss_config, errors as ss_errors, integrations as
    ss_integrations, listing_settings as ss_listing_settings,
)


_LABELMASTER_REQUIRED_SETTINGS = [
    'label_from_name',
    'label_from_address1',
    'label_from_city',
    'label_from_state',
    'label_from_postal',
    'label_from_country',
    'label_pkg_weight_oz',
    'label_pkg_length_in',
    'label_pkg_width_in',
    'label_pkg_height_in',
]


def _labelmaster_get_settings():
    settings = ss_listing_settings._listingagent_get_settings()
    out = {k: v for k, v in (settings or {}).items() if str(k).startswith('label_')}
    if not out.get('label_from_country'):
        out['label_from_country'] = 'US'
    if not out.get('label_amazon_delivery_experience'):
        out['label_amazon_delivery_experience'] = 'NoTracking'
    if not out.get('label_amazon_carrier_pickup_option'):
        out['label_amazon_carrier_pickup_option'] = 'ShipperWillDropOff'
    if not out.get('label_ebay_label_size'):
        out['label_ebay_label_size'] = '4"x6"'
    return out


def _labelmaster_missing_settings(settings: dict):
    missing = []
    for k in _LABELMASTER_REQUIRED_SETTINGS:
        v = (settings or {}).get(k)
        if v is None or str(v).strip() == '':
            missing.append(k)
    return missing


def _labelmaster_init_label_tables(cur):
    cur.execute('''
        CREATE TABLE IF NOT EXISTS shipping_labels (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            store TEXT NOT NULL,
            order_id TEXT NOT NULL,
            label_id TEXT,
            label_url TEXT,
            label_format TEXT,
            label_path TEXT,
            cost REAL,
            currency TEXT,
            created_at TEXT,
            status TEXT,
            raw_response TEXT
        )
    ''')
    cur.execute('CREATE INDEX IF NOT EXISTS idx_shipping_labels_order ON shipping_labels(store, order_id, id)')


def _labelmaster_safe_name(s):
    try:
        return _re.sub(r'[^A-Za-z0-9._-]+', '_', str(s or '').strip()) or 'label'
    except Exception:
        return 'label'


def _labelmaster_store_label(store, order_id, *, label_id=None, label_url=None, label_format=None, label_bytes=None, cost=None, currency=None, raw=None):
    label_path = None
    if label_bytes:
        label_dir = ss_config.BASE_DIR / 'debug_uploads' / 'labels' / store
        label_dir.mkdir(parents=True, exist_ok=True)
        safe_order = _labelmaster_safe_name(order_id)
        ts = datetime.datetime.utcnow().strftime('%Y%m%d_%H%M%S')
        ext = (label_format or 'pdf').lower().replace('.', '')
        if ext not in ('pdf', 'png', 'zpl'):
            ext = 'pdf'
        label_path = str(label_dir / f"{safe_order}_{ts}.{ext}")
        with open(label_path, 'wb') as f:
            f.write(label_bytes)

    with sqlite3.connect('sold.db') as conn:
        cur = conn.cursor()
        _labelmaster_init_label_tables(cur)
        cur.execute('''
            INSERT INTO shipping_labels
                (store, order_id, label_id, label_url, label_format, label_path, cost, currency, created_at, status, raw_response)
            VALUES
                (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            store,
            order_id,
            label_id,
            label_url,
            label_format,
            label_path,
            cost,
            currency,
            datetime.datetime.utcnow().isoformat() + 'Z',
            'created',
            json.dumps(raw, ensure_ascii=True, default=str) if raw is not None else None
        ))
        conn.commit()
    return label_path


def _labelmaster_latest_label(store, order_id):
    with sqlite3.connect('sold.db') as conn:
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        _labelmaster_init_label_tables(cur)
        cur.execute('''
            SELECT *
            FROM shipping_labels
            WHERE store = ? AND order_id = ?
            ORDER BY id DESC
            LIMIT 1
        ''', (store, order_id))
        row = cur.fetchone()
    return dict(row) if row else None


def _labelmaster_find_order(order_id, store):
    with sqlite3.connect('sold.db') as conn:
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        params = [store, order_id]
        q = 'SELECT * FROM orders WHERE store = ? AND order_id = ? ORDER BY id DESC LIMIT 1'
        cur.execute(q, params)
        row = cur.fetchone()
        if row:
            return dict(row)
        # Fallback to numeric id
        try:
            oid = int(order_id)
            cur.execute('SELECT * FROM orders WHERE store = ? AND id = ? ORDER BY id DESC LIMIT 1', (store, oid))
            row = cur.fetchone()
            if row:
                return dict(row)
        except Exception:
            pass
    return None


def _labelmaster_amazon_ship_from(settings):
    return {
        'Name': (settings.get('label_from_name') or '').strip(),
        'AddressLine1': (settings.get('label_from_address1') or '').strip(),
        'AddressLine2': (settings.get('label_from_address2') or '').strip(),
        'City': (settings.get('label_from_city') or '').strip(),
        'StateOrProvinceCode': (settings.get('label_from_state') or '').strip(),
        'PostalCode': (settings.get('label_from_postal') or '').strip(),
        'CountryCode': (settings.get('label_from_country') or 'US').strip(),
        'Phone': (settings.get('label_from_phone') or '').strip(),
        'Email': (settings.get('label_from_email') or '').strip(),
    }


def _labelmaster_ebay_ship_from(settings):
    addr = {
        'addressLine1': (settings.get('label_from_address1') or '').strip(),
        'addressLine2': (settings.get('label_from_address2') or '').strip(),
        'city': (settings.get('label_from_city') or '').strip(),
        'stateOrProvince': (settings.get('label_from_state') or '').strip(),
        'postalCode': (settings.get('label_from_postal') or '').strip(),
        'countryCode': (settings.get('label_from_country') or 'US').strip(),
    }
    contact = {
        'fullName': (settings.get('label_from_name') or '').strip(),
        'companyName': (settings.get('label_from_company') or '').strip(),
        'contactAddress': addr,
    }
    phone = (settings.get('label_from_phone') or '').strip()
    if phone:
        contact['primaryPhone'] = {'phoneNumber': phone}
    email = (settings.get('label_from_email') or '').strip()
    if email:
        contact['email'] = email
    return contact


def _labelmaster_package_dims(settings):
    return {
        'length': ss_listing_settings._listingagent_parse_float(settings.get('label_pkg_length_in'), None),
        'width': ss_listing_settings._listingagent_parse_float(settings.get('label_pkg_width_in'), None),
        'height': ss_listing_settings._listingagent_parse_float(settings.get('label_pkg_height_in'), None),
        'unit': 'INCH'
    }


def _labelmaster_package_weight(settings):
    return {
        'value': ss_listing_settings._listingagent_parse_float(settings.get('label_pkg_weight_oz'), None),
        'unit': 'OUNCE'
    }


def _labelmaster_validate_package(settings):
    dims = _labelmaster_package_dims(settings)
    weight = _labelmaster_package_weight(settings)
    if not dims['length'] or not dims['width'] or not dims['height']:
        return None, None, 'Package dimensions are required'
    if not weight['value']:
        return None, None, 'Package weight is required'
    return dims, weight, None


def _labelmaster_amazon_decode_label(label):
    if not label:
        return None
    data = label.get('FileContents') or label.get('LabelStream')
    if not data:
        return None
    raw = base64.b64decode(data)
    try:
        return gzip.decompress(raw)
    except Exception:
        return raw


def _labelmaster_pick_cheapest_rate(rates):
    best = None
    best_amount = None
    for r in rates or []:
        rate = r.get('Rate') or {}
        amt = rate.get('Amount')
        try:
            val = float(amt)
        except Exception:
            continue
        if best is None or val < best_amount:
            best = r
            best_amount = val
    return best


def _labelmaster_collect_scope_tokens(*values):
    tokens = set()
    for value in values:
        if value is None:
            continue
        if isinstance(value, (list, tuple, set)):
            parts = value
        else:
            parts = str(value).replace(',', ' ').split()
        for p in parts:
            s = str(p or '').strip()
            if s:
                tokens.add(s.lower())
    return tokens


def _labelmaster_ebay_scope_state():
    ebay_connected = False
    token_scope = ''
    tokens_path = ss_config.BASE_DIR / 'tokens.json'
    if tokens_path.exists():
        try:
            with open(tokens_path, 'r', encoding='utf-8') as f:
                tokens = json.load(f)
            ebay_connected = bool(tokens.get('refresh_token'))
            token_scope = tokens.get('scope') or ''
        except Exception:
            ebay_connected = False

    env_scope = (os.getenv('EBAY_SCOPE') or '')
    env_scope_tokens = _labelmaster_collect_scope_tokens(env_scope)
    token_scope_tokens = _labelmaster_collect_scope_tokens(token_scope)

    scope_known = bool(token_scope_tokens)
    has_logistics_scope = any('sell.logistics' in s for s in token_scope_tokens)
    env_has_logistics_scope = any('sell.logistics' in s for s in env_scope_tokens)
    has_effective_logistics_scope = has_logistics_scope or not scope_known

    return {
        'connected': ebay_connected,
        'scope_known': scope_known,
        'has_sell_logistics_scope': has_logistics_scope,
        'env_has_sell_logistics_scope': env_has_logistics_scope,
        'has_effective_sell_logistics_scope': has_effective_logistics_scope
    }


def _labelmaster_ebay_error_message(payload, status_code, *, stage='request'):
    if isinstance(payload, dict):
        errors = payload.get('errors')
        if isinstance(errors, list) and errors:
            access_denied = False
            first_msg = ''
            for entry in errors:
                if not isinstance(entry, dict):
                    continue
                try:
                    err_id = int(entry.get('errorId'))
                except Exception:
                    err_id = None
                domain = str(entry.get('domain') or '').strip().upper()
                category = str(entry.get('category') or '').strip().upper()
                if err_id == 1100 or (domain == 'ACCESS' and category == 'REQUEST'):
                    access_denied = True
                if not first_msg:
                    first_msg = (entry.get('longMessage') or entry.get('message') or '').strip()
            if access_denied:
                return (
                    'eBay Logistics API access denied (ACCESS 1100). Re-authorize eBay with '
                    'https://api.ebay.com/oauth/api_scope/sell.logistics and include it in EBAY_SCOPE.'
                )
            if first_msg:
                return f"eBay {stage} failed: {first_msg}"
        msg = str(payload.get('message') or '').strip()
        if msg:
            return f"eBay {stage} failed: {msg}"
    if payload:
        return str(payload)
    return f"eBay {stage} failed (HTTP {status_code})"


def _labelmaster_ebay_request(method, path, token, *, json_payload=None, marketplace_id='EBAY_US', timeout=30):
    url = f"https://api.ebay.com/sell/logistics/v1_beta{path}"
    headers = {
        'Authorization': f'Bearer {token}',
        'Content-Type': 'application/json',
        'X-EBAY-C-MARKETPLACE-ID': marketplace_id
    }
    resp = requests.request(method, url, headers=headers, json=json_payload, timeout=timeout)
    return resp


def _labelmaster_buy_ebay(order, settings):
    from token_manager import get_access_token
    token = get_access_token()
    if not token:
        raise Exception('Missing eBay OAuth token (run ebay_oauth_setup.py)')

    dims, weight, err = _labelmaster_validate_package(settings)
    if err:
        raise Exception(err)

    ship_to_addr = {
        'addressLine1': (order.get('shipping_street1') or '').strip(),
        'addressLine2': (order.get('shipping_street2') or '').strip(),
        'city': (order.get('shipping_city') or '').strip(),
        'stateOrProvince': (order.get('shipping_state') or '').strip(),
        'postalCode': (order.get('shipping_postal_code') or '').strip(),
        'countryCode': (order.get('shipping_country') or 'US').strip(),
    }
    if not ship_to_addr['addressLine1'] or not ship_to_addr['postalCode']:
        raise Exception('Missing ship-to address on order. Re-sync orders and try again.')

    ship_to = {
        'fullName': (order.get('shipping_name') or 'eBay Buyer').strip(),
        'contactAddress': ship_to_addr
    }

    ship_from = _labelmaster_ebay_ship_from(settings)
    if not ship_from.get('fullName') or not ship_from.get('contactAddress', {}).get('addressLine1'):
        raise Exception('Ship-from defaults are missing. Fill in Label Master defaults.')

    payload = {
        'shipFrom': ship_from,
        'shipTo': ship_to,
        'packageSpecification': {
            'dimensions': dims,
            'weight': weight
        },
        'orders': [{
            'channel': 'EBAY',
            'orderId': (order.get('order_id') or '').strip()
        }]
    }

    settings_all = ss_listing_settings._listingagent_get_settings()
    marketplace_id = (settings_all.get('ebay_marketplace_id') or 'EBAY_US').strip()

    quote_resp = _labelmaster_ebay_request('POST', '/shipping_quote', token, json_payload=payload, marketplace_id=marketplace_id)
    try:
        quote_json = quote_resp.json()
    except Exception:
        quote_json = {}

    if quote_resp.status_code >= 400:
        raise Exception(_labelmaster_ebay_error_message(quote_json, quote_resp.status_code, stage='shipping quote'))

    quote_id = quote_json.get('shippingQuoteId')
    rates = quote_json.get('rates') or []
    if not quote_id or not rates:
        raise Exception('No shipping rates returned from eBay')

    best = None
    best_cost = None
    for r in rates:
        cost = (r.get('totalShippingCost') or r.get('baseShippingCost') or {}).get('value')
        try:
            val = float(cost)
        except Exception:
            continue
        if best is None or val < best_cost:
            best = r
            best_cost = val

    if not best:
        raise Exception('Could not select a shipping rate')

    shipment_payload = {
        'shippingQuoteId': quote_id,
        'rateId': best.get('rateId'),
        'labelSize': settings.get('label_ebay_label_size') or '4\"x6\"'
    }
    ship_resp = _labelmaster_ebay_request('POST', '/shipment', token, json_payload=shipment_payload, marketplace_id=marketplace_id)
    try:
        ship_json = ship_resp.json()
    except Exception:
        ship_json = {}

    if ship_resp.status_code >= 400:
        raise Exception(_labelmaster_ebay_error_message(ship_json, ship_resp.status_code, stage='shipment'))

    label_url = ship_json.get('labelDownloadUrl') or ship_json.get('labelUrl')
    label_bytes = None
    if label_url:
        try:
            dl = requests.get(label_url, headers={'Authorization': f'Bearer {token}'}, timeout=30)
            if dl.status_code == 200:
                label_bytes = dl.content
        except Exception:
            label_bytes = None

    label_path = _labelmaster_store_label(
        'ebay',
        order.get('order_id') or '',
        label_id=ship_json.get('shipmentId'),
        label_url=label_url,
        label_format='pdf',
        label_bytes=label_bytes,
        cost=best_cost,
        currency=(best.get('totalShippingCost') or best.get('baseShippingCost') or {}).get('currency') or 'USD',
        raw={'quote': quote_json, 'shipment': ship_json}
    )

    return {
        'label_id': ship_json.get('shipmentId'),
        'label_url': label_url,
        'label_path': label_path,
        'cost': best_cost,
        'currency': (best.get('totalShippingCost') or best.get('baseShippingCost') or {}).get('currency') or 'USD',
        'format': 'pdf'
    }


def _labelmaster_resolve_amazon_address(order_id, credentials, marketplace):
    """Try to get the buyer's full shipping address from Amazon APIs.
    Attempts: 1) RDT + get_order_address  2) Plain get_order_address
    Returns a ship_to dict or None.
    """
    from sp_api.api import Tokens, Orders

    # --- Attempt 1: Get RDT token, then use it for get_order_address ---
    try:
        ss_config.logger.error(f"labelmaster: [addr] Requesting RDT for order {order_id}...")
        tokens_api = Tokens(credentials=credentials, marketplace=marketplace)
        rdt_resp = tokens_api.create_restricted_data_token(
            restrictedResources=[{
                'method': 'GET',
                'path': f'/orders/v0/orders/{order_id}/address',
                'dataElements': ['shippingAddress']
            }]
        )
        rdt_payload = rdt_resp.payload or {}
        rdt_token = rdt_payload.get('restrictedDataToken') or ''
        ss_config.logger.error(f"labelmaster: [addr] RDT token received: {bool(rdt_token)} (expires: {rdt_payload.get('expiresIn', '?')}s)")

        if rdt_token:
            orders_api = Orders(credentials=credentials, marketplace=marketplace,
                               restricted_data_token=rdt_token)
            addr_resp = orders_api.get_order_address(order_id)
            addr_data = (addr_resp.payload or {}).get('ShippingAddress') or {}
            ss_config.logger.error(f"labelmaster: [addr] RDT address keys: {list(addr_data.keys())}")
            ss_config.logger.error(f"labelmaster: [addr] RDT address data: {json.dumps(addr_data, default=str)}")
            line1 = addr_data.get('AddressLine1') or ''
            postal = addr_data.get('PostalCode') or ''
            name = addr_data.get('Name') or ''
            if line1 and postal:
                ss_config.logger.error(f"labelmaster: [addr] Got FULL address via RDT!")
                return {
                    'name': name,
                    'addressLine1': line1,
                    'addressLine2': addr_data.get('AddressLine2') or '',
                    'city': addr_data.get('City') or '',
                    'stateOrRegion': addr_data.get('StateOrRegion') or '',
                    'postalCode': postal,
                    'countryCode': addr_data.get('CountryCode') or 'US',
                }
            else:
                ss_config.logger.error(f"labelmaster: [addr] RDT returned partial address still")
    except Exception as e:
        ss_config.logger.error(f"labelmaster: [addr] RDT attempt failed: {e}")

    # --- Attempt 2: Plain get_order_address (no RDT) ---
    try:
        ss_config.logger.error(f"labelmaster: [addr] Trying plain get_order_address...")
        orders_api = Orders(credentials=credentials, marketplace=marketplace)
        addr_resp = orders_api.get_order_address(order_id)
        addr_data = (addr_resp.payload or {}).get('ShippingAddress') or {}
        ss_config.logger.error(f"labelmaster: [addr] Plain address keys: {list(addr_data.keys())}")
        line1 = addr_data.get('AddressLine1') or ''
        postal = addr_data.get('PostalCode') or ''
        name = addr_data.get('Name') or ''
        if line1 and postal:
            ss_config.logger.error(f"labelmaster: [addr] Got full address without RDT!")
            return {
                'name': name,
                'addressLine1': line1,
                'addressLine2': addr_data.get('AddressLine2') or '',
                'city': addr_data.get('City') or '',
                'stateOrRegion': addr_data.get('StateOrRegion') or '',
                'postalCode': postal,
                'countryCode': addr_data.get('CountryCode') or 'US',
            }
        else:
            ss_config.logger.error(f"labelmaster: [addr] Plain address also partial")
    except Exception as e:
        ss_config.logger.error(f"labelmaster: [addr] Plain get_order_address failed: {e}")

    return None


def _labelmaster_buy_amazon(order, settings, ship_to=None):
    """Buy a shipping label via Amazon Shipping API v2 with channelDetails.

    Uses channelType=AMAZON so Amazon resolves the buyer address from the order ID.
    No PII / manual address needed.
    """
    if not ss_integrations.AMAZON_AVAILABLE:
        raise Exception('Amazon SP-API not available')

    dims, weight, err = _labelmaster_validate_package(settings)
    if err:
        raise Exception(err)

    credentials, seller_id, marketplace_id, marketplace = ss_amazon_catalog._amazon_spapi_context()
    if marketplace is None:
        raise Exception('Amazon SP-API not available')

    order_id = (order.get('order_id') or '').strip()

    # --- Build ship-from ---
    ship_from_raw = _labelmaster_amazon_ship_from(settings)
    if not ship_from_raw.get('Name') or not ship_from_raw.get('AddressLine1'):
        raise Exception('Ship-from defaults are missing. Fill in Label Master defaults.')

    ship_from = {
        'name': ship_from_raw.get('Name', ''),
        'addressLine1': ship_from_raw.get('AddressLine1', ''),
        'addressLine2': ship_from_raw.get('AddressLine2', ''),
        'city': ship_from_raw.get('City', ''),
        'stateOrRegion': ship_from_raw.get('StateOrProvinceCode', ''),
        'postalCode': ship_from_raw.get('PostalCode', ''),
        'countryCode': ship_from_raw.get('CountryCode', 'US'),
        'phoneNumber': ship_from_raw.get('Phone', ''),
        'email': ship_from_raw.get('Email', ''),
    }
    ship_from = {k: v for k, v in ship_from.items() if v}

    ship_date = datetime.datetime.utcnow().replace(microsecond=0).isoformat() + 'Z'

    # Convert weight from oz to pounds for Shipping v2
    weight_lb = round(weight['value'] / 16.0, 2)

    # --- Fetch order items for package item details ---
    from amazon_manager import AmazonManager
    am = AmazonManager()
    items = am.get_order_items(order_id)
    package_items = []
    total_value = 0
    for it in (items or []):
        qty = int(it.get('QuantityOrdered') or it.get('Quantity') or 1)
        title = it.get('Title') or 'Item'
        price_amount = 0
        try:
            price_amount = float((it.get('ItemPrice') or {}).get('Amount', 0))
        except (TypeError, ValueError):
            pass
        total_value += price_amount
        package_items.append({
            'itemValue': {'unit': 'USD', 'value': round(price_amount, 2)},
            'description': title[:80],
            'itemIdentifier': it.get('OrderItemId') or '',
            'quantity': qty,
            'weight': {'unit': 'LB', 'value': weight_lb},
        })
    if not package_items:
        package_items.append({
            'itemValue': {'unit': 'USD', 'value': 0},
            'description': 'Package',
            'itemIdentifier': order_id,
            'quantity': 1,
            'weight': {'unit': 'LB', 'value': weight_lb},
        })

    package = {
        'dimensions': {
            'length': dims['length'],
            'width': dims['width'],
            'height': dims['height'],
            'unit': 'INCH'
        },
        'weight': {
            'unit': 'LB',
            'value': weight_lb
        },
        'insuredValue': {
            'unit': 'USD',
            'value': round(total_value, 2)
        },
        'packageClientReferenceId': order_id,
        'items': package_items
    }

    channel_details = {
        'channelType': 'AMAZON',
        'amazonOrderDetails': {
            'orderId': order_id
        }
    }

    # --- Step 1: Get shipping rates (v2) ---
    ss_config.logger.info(f"labelmaster: [v2 step 1/2] Getting rates for order {order_id}")
    from sp_api.api.shipping.shippingV2 import Shipping as ShippingV2
    shipping_api = ShippingV2(credentials=credentials, marketplace=marketplace)

    rates_body = {
        'shipFrom': ship_from,
        'shipDate': ship_date,
        'packages': [package],
        'channelDetails': channel_details,
    }

    ss_config.logger.info(f"labelmaster: v2 endpoint={shipping_api.endpoint} business_id={shipping_api.amzn_shipping_business}")
    try:
        rates_resp = shipping_api.get_rates(body=rates_body)
    except Exception as e:
        err_str = str(e)
        ss_config.logger.error(f"labelmaster: v2 getRates failed: {err_str}")
        # If v2 is denied, try v1 as fallback
        if 'Unauthorized' in err_str or 'AccessDenied' in err_str or 'Forbidden' in err_str or '403' in err_str:
            ss_config.logger.info("labelmaster: v2 denied, trying to get buyer address via RDT + Orders API...")

            # Try to get buyer address automatically via RDT or Orders API
            if not ship_to:
                ship_to = _labelmaster_resolve_amazon_address(order_id, credentials, marketplace)

            from sp_api.api import Shipping as ShippingV1
            v1_api = ShippingV1(credentials=credentials, marketplace=marketplace)
            if not ship_to:
                raise Exception('NEED_ADDRESS')
            v1_body = {
                'shipTo': ship_to,
                'shipFrom': ship_from,
                'shipDate': ship_date,
                'serviceTypes': ['Amazon Shipping Ground', 'Amazon Shipping Standard', 'Amazon Shipping Premium'],
                'containerSpecifications': [{
                    'dimensions': {'length': dims['length'], 'width': dims['width'], 'height': dims['height'], 'unit': 'IN'},
                    'weight': {'unit': 'oz', 'value': weight['value']}
                }]
            }
            rates_resp = v1_api.get_rates(**v1_body)
            # If we get here, v1 works - continue with v1 flow
            ss_config.logger.info("labelmaster: v1 getRates succeeded!")
            v1_payload = rates_resp.payload or {}
            service_rates = v1_payload.get('serviceRates') or []
            if not service_rates:
                raise Exception('No shipping rates returned from v1.')
            # Pick cheapest
            chosen = None
            chosen_cost = None
            for sr in service_rates:
                total = sr.get('totalCharge') or {}
                try:
                    amt = float(total.get('value', 0))
                except (TypeError, ValueError):
                    continue
                if chosen is None or amt < chosen_cost:
                    chosen = sr
                    chosen_cost = amt
            if not chosen:
                raise Exception('Could not select a shipping rate')
            service_type = chosen.get('serviceType') or ''
            total_charge = chosen.get('totalCharge') or {}
            label_cost = chosen_cost
            label_currency = total_charge.get('unit') or 'USD'
            ss_config.logger.info(f"labelmaster: v1 selected: {service_type} @ ${label_cost:.2f}")

            # Build v1 container
            weight_grams = round(weight['value'] * 28.3495, 1)
            container_items = []
            for it in (items or []):
                qty = int(it.get('QuantityOrdered') or it.get('Quantity') or 1)
                title = it.get('Title') or 'Item'
                price_amount = 0
                try:
                    price_amount = float((it.get('ItemPrice') or {}).get('Amount', 0))
                except (TypeError, ValueError):
                    pass
                container_items.append({
                    'quantity': qty,
                    'unitPrice': {'value': price_amount, 'unit': 'USD'},
                    'unitWeight': {'unit': 'g', 'value': weight_grams},
                    'title': title[:80]
                })
            container = {
                'containerType': 'PACKAGE',
                'containerReferenceId': order_id,
                'dimensions': {'length': dims['length'], 'width': dims['width'], 'height': dims['height'], 'unit': 'IN'},
                'weight': {'unit': 'g', 'value': weight_grams},
                'items': container_items or [{'quantity': 1, 'unitPrice': {'value': 0, 'unit': 'USD'}, 'unitWeight': {'unit': 'g', 'value': weight_grams}, 'title': 'Package'}],
                'value': total_charge
            }
            ss_config.logger.info(f"labelmaster: v1 purchasing shipment ({service_type})")
            purchase_resp = v1_api.purchase_shipment(
                clientReferenceId=order_id,
                shipTo=ship_to,
                shipFrom=ship_from,
                shipDate=ship_date,
                serviceType=service_type,
                containers=[container],
                labelSpecification={'labelFormat': 'PNG', 'labelStockSize': '4x6'}
            )
            if getattr(purchase_resp, 'errors', None):
                raise Exception(f"v1 purchase failed: {purchase_resp.errors}")
            p_payload = purchase_resp.payload or {}
            label_result = p_payload.get('labelResults') or [{}]
            first_label = label_result[0] if label_result else {}
            label_obj = first_label.get('label') or {}
            label_stream = label_obj.get('labelStream') or ''
            label_id = p_payload.get('shipmentId') or first_label.get('trackingId') or ''
            label_bytes = base64.b64decode(label_stream) if label_stream else None
            label_path = _labelmaster_store_label('amazon', order_id, label_id=label_id, label_url=None, label_format='png', label_bytes=label_bytes, cost=label_cost, currency=label_currency, raw={'rates': v1_payload, 'purchase': p_payload})
            return {'label_id': label_id, 'label_url': None, 'label_path': label_path, 'cost': label_cost, 'currency': label_currency or 'USD', 'format': 'png'}
        raise

    if getattr(rates_resp, 'errors', None):
        raise Exception(f"Get rates failed: {rates_resp.errors}")

    payload = rates_resp.payload or {}
    request_token = payload.get('requestToken')
    rates = payload.get('rates') or []
    ineligible = payload.get('ineligibleRates') or []

    if not rates:
        reasons = '; '.join(
            f"{r.get('serviceName','?')}: {', '.join(x.get('message','') for x in (r.get('ineligibilityReasons') or []))}"
            for r in ineligible
        ) if ineligible else 'none returned'
        raise Exception(f'No shipping rates available. Ineligible: {reasons}')

    ss_config.logger.info(f"labelmaster: Got {len(rates)} rate(s), {len(ineligible)} ineligible")
    for r in rates:
        tc = r.get('totalCharge') or {}
        ss_config.logger.info(f"  - {r.get('serviceName','')} ({r.get('carrierId','')}) ${tc.get('value',0)}")

    # Pick cheapest rate
    chosen = None
    chosen_cost = None
    for r in rates:
        tc = r.get('totalCharge') or {}
        try:
            amt = float(tc.get('value', 0))
        except (TypeError, ValueError):
            continue
        if chosen is None or amt < chosen_cost:
            chosen = r
            chosen_cost = amt

    if not chosen:
        raise Exception('Could not select a shipping rate')

    rate_id = chosen.get('rateId')
    label_cost = chosen_cost
    label_currency = (chosen.get('totalCharge') or {}).get('unit') or 'USD'
    ss_config.logger.info(f"labelmaster: Selected: {chosen.get('serviceName','')} @ ${label_cost:.2f} (rateId={rate_id})")

    # Figure out supported label format from the rate
    doc_specs = chosen.get('supportedDocumentSpecifications') or []
    doc_spec = None
    for preferred_fmt in ['PNG', 'PDF']:
        for ds in doc_specs:
            if ds.get('format') == preferred_fmt:
                doc_spec = ds
                break
        if doc_spec:
            break
    if not doc_spec and doc_specs:
        doc_spec = doc_specs[0]
    if not doc_spec:
        doc_spec = {'format': 'PNG', 'size': {'width': 4, 'length': 6, 'unit': 'INCH'}}

    label_format = (doc_spec.get('format') or 'PNG').lower()
    requested_doc = {
        'format': doc_spec.get('format', 'PNG'),
        'size': doc_spec.get('size', {'width': 4, 'length': 6, 'unit': 'INCH'}),
        'needFileJoining': False,
        'requestedDocumentTypes': ['LABEL']
    }
    print_opts = (doc_spec.get('printOptions') or [{}])
    if print_opts and print_opts[0].get('supportedPageLayouts'):
        requested_doc['pageLayout'] = print_opts[0]['supportedPageLayouts'][0]

    # --- Step 2: Purchase shipment (v2) ---
    ss_config.logger.info(f"labelmaster: [v2 step 2/2] Purchasing shipment (rateId={rate_id})")
    purchase_body = {
        'requestToken': request_token,
        'rateId': rate_id,
        'requestedDocumentSpecification': requested_doc,
    }

    purchase_resp = shipping_api.purchase_shipment(body=purchase_body)

    if getattr(purchase_resp, 'errors', None):
        raise Exception(f"Purchase failed: {purchase_resp.errors}")

    p_payload = purchase_resp.payload or {}
    shipment_id = p_payload.get('shipmentId') or ''
    pkg_details = (p_payload.get('packageDocumentDetails') or [{}])
    first_pkg = pkg_details[0] if pkg_details else {}
    tracking_id = first_pkg.get('trackingId') or ''
    pkg_docs = first_pkg.get('packageDocuments') or []
    label_doc = next((d for d in pkg_docs if d.get('type') == 'LABEL'), pkg_docs[0] if pkg_docs else {})
    label_contents = label_doc.get('contents') or ''

    label_id = shipment_id or tracking_id
    ss_config.logger.info(f"labelmaster: Purchased! shipmentId={shipment_id}, trackingId={tracking_id}, label format={label_format}")

    label_bytes = None
    if label_contents:
        label_bytes = base64.b64decode(label_contents)

    label_path = _labelmaster_store_label(
        'amazon',
        order_id,
        label_id=label_id,
        label_url=None,
        label_format=label_format,
        label_bytes=label_bytes,
        cost=label_cost,
        currency=label_currency,
        raw={'rates': payload, 'purchase': p_payload}
    )

    return {
        'label_id': label_id,
        'label_url': None,
        'label_path': label_path,
        'cost': label_cost,
        'currency': label_currency or 'USD',
        'format': label_format
    }


def api_labelmaster_get_settings():
    try:
        return jsonify({
            'success': True,
            'settings': _labelmaster_get_settings(),
            'required': _LABELMASTER_REQUIRED_SETTINGS
        })
    except Exception as e:
        return jsonify({'success': False, 'error': ss_errors._safe_error(e, 'labelmaster:get_settings')}), 500


def api_labelmaster_save_settings():
    try:
        payload = request.json or {}
        settings = payload.get('settings', payload)
        if not isinstance(settings, dict):
            return jsonify({'success': False, 'error': 'settings must be an object'}), 400
        filtered = {k: v for k, v in settings.items() if str(k).startswith('label_')}
        ss_listing_settings._listingagent_upsert_settings(filtered)
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': ss_errors._safe_error(e, 'labelmaster:save_settings')}), 500


def api_labelmaster_status():
    """Return a quick capability check for label buying/printing APIs."""
    settings = _labelmaster_get_settings()
    missing = _labelmaster_missing_settings(settings)

    scope_state = _labelmaster_ebay_scope_state()
    ebay_connected = bool(scope_state.get('connected'))
    ebay_reason = ''
    scope_known = bool(scope_state.get('scope_known'))
    has_logistics_scope = bool(scope_state.get('has_sell_logistics_scope'))
    env_has_logistics_scope = bool(scope_state.get('env_has_sell_logistics_scope'))
    has_effective_logistics_scope = bool(scope_state.get('has_effective_sell_logistics_scope'))

    if not ebay_connected:
        ebay_reason = 'eBay OAuth token missing (run ebay_oauth_setup.py)'
    elif scope_known and not has_logistics_scope:
        ebay_reason = 'Missing sell.logistics scope (reauthorize eBay app)'
    elif missing:
        ebay_reason = f"Missing defaults: {', '.join(missing)}"
    elif not env_has_logistics_scope:
        ebay_reason = 'EBAY_SCOPE does not advertise sell.logistics; proceeding because token scope metadata is unavailable'
    elif not scope_known:
        # Some refresh-token responses omit scope in tokens.json; treat as unknown instead of hard-failing.
        ebay_reason = 'Scope could not be introspected from token metadata (not blocking)'

    amazon_connected = False
    amazon_reason = ''
    if ss_integrations.AMAZON_AVAILABLE:
        try:
            credentials, seller_id, marketplace_id, marketplace = ss_amazon_catalog._amazon_spapi_context()
            amazon_connected = marketplace is not None
            if not amazon_connected:
                amazon_reason = 'Amazon SP-API not available'
            elif missing:
                amazon_reason = f"Missing defaults: {', '.join(missing)}"
        except Exception as e:
            amazon_connected = False
            amazon_reason = ss_errors._safe_error(e, 'labelmaster:amazon_status')
    else:
        amazon_reason = 'Amazon SP-API library not available'

    return jsonify({
        'success': True,
        'ebay': {
            'connected': ebay_connected,
            'supported': ebay_connected and has_effective_logistics_scope and not missing,
            'reason': ebay_reason,
            'scope_known': scope_known,
            'has_sell_logistics_scope': has_logistics_scope,
            'env_has_sell_logistics_scope': env_has_logistics_scope
        },
        'amazon': {
            'connected': amazon_connected,
            'supported': amazon_connected and not missing,
            'reason': amazon_reason
        },
        'missing_defaults': missing
    })


def api_labelmaster_orders():
    """Return sold orders for label purchasing/printing."""
    try:
        days = int(request.args.get('days', 7))
        store = (request.args.get('store') or '').strip().lower()
        q = (request.args.get('q') or '').strip().lower()

        params = [days]
        where = "paid_time >= date('now', '-' || ? || ' days')"
        if store in ('ebay', 'amazon'):
            where += " AND store = ?"
            params.append(store)

        with sqlite3.connect('sold.db') as conn:
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()
            _labelmaster_init_label_tables(cur)
            cur.execute(f'''
                SELECT o.*,
                       l.label_id,
                       l.label_url,
                       l.label_format,
                       l.label_path,
                       l.created_at AS label_created_at
                FROM orders o
                LEFT JOIN (
                    SELECT store, order_id, MAX(id) AS max_id
                    FROM shipping_labels
                    GROUP BY store, order_id
                ) last
                    ON last.store = o.store AND last.order_id = o.order_id
                LEFT JOIN shipping_labels l
                    ON l.id = last.max_id
                WHERE {where}
                ORDER BY paid_time DESC
                LIMIT 4000
            ''', params)
            rows = cur.fetchall()

        orders = [dict(r) for r in rows]
        for o in orders:
            o['label_ready'] = bool(o.get('label_path') or o.get('label_url'))

        if q:
            def _matches(o):
                hay = ' '.join([
                    str(o.get('order_id') or ''),
                    str(o.get('item_id') or ''),
                    str(o.get('title') or ''),
                    str(o.get('barcode') or ''),
                    str(o.get('shipping_name') or ''),
                    str(o.get('shipping_city') or ''),
                    str(o.get('shipping_state') or ''),
                    str(o.get('shipping_postal_code') or ''),
                    str(o.get('location') or ''),
                ]).lower()
                return q in hay
            orders = [o for o in orders if _matches(o)]

        return jsonify({'success': True, 'orders': orders})
    except Exception as e:
        return jsonify({'success': False, 'error': ss_errors._safe_error(e, 'labelmaster:orders')}), 500


def api_labelmaster_buy():
    """Buy a shipping label for an order."""
    try:
        data = request.json or {}
        order_id = (data.get('order_id') or '').strip()
        store = (data.get('store') or '').strip().lower()
        if not order_id or store not in ('ebay', 'amazon'):
            return jsonify({'success': False, 'error': 'order_id and store are required'}), 400

        settings = _labelmaster_get_settings()
        missing = _labelmaster_missing_settings(settings)
        if missing:
            return jsonify({'success': False, 'error': f"Missing defaults: {', '.join(missing)}"}), 400

        order = _labelmaster_find_order(order_id, store)
        if not order:
            return jsonify({'success': False, 'error': 'Order not found'}), 404

        if store == 'ebay':
            scope_state = _labelmaster_ebay_scope_state()
            if scope_state.get('scope_known') and not scope_state.get('has_sell_logistics_scope'):
                return jsonify({
                    'success': False,
                    'error': (
                        'eBay token does not include sell.logistics. Reauthorize eBay OAuth and retry.'
                    )
                }), 400
            if not scope_state.get('env_has_sell_logistics_scope'):
                ss_config.logger.warning(
                    'labelmaster: buy proceeding with unknown token scope while EBAY_SCOPE lacks sell.logistics'
                )
            result = _labelmaster_buy_ebay(order, settings)
        else:
            ship_to = data.get('ship_to')
            result = _labelmaster_buy_amazon(order, settings, ship_to=ship_to)

        label_url = result.get('label_url')
        if not label_url and result.get('label_path'):
            label_url = url_for('api_labelmaster_label_file', store=store, order_id=order.get('order_id') or order_id)

        return jsonify({
            'success': True,
            'label_url': label_url,
            'label_id': result.get('label_id'),
            'cost': result.get('cost'),
            'currency': result.get('currency'),
            'format': result.get('format')
        })
    except Exception as e:
        err_msg = str(e)
        if err_msg == 'NEED_ADDRESS':
            return jsonify({
                'success': False,
                'need_address': True,
                'error': 'Shipping address required. Please enter the buyer address.',
                'prefill': {
                    'city': order.get('shipping_city') or '',
                    'stateOrRegion': order.get('shipping_state') or '',
                    'postalCode': order.get('shipping_postal_code') or '',
                    'countryCode': order.get('shipping_country') or 'US',
                    'name': order.get('shipping_name') or order.get('buyer_name') or '',
                }
            }), 400
        if (
            'sell.logistics' in err_msg or
            'ACCESS 1100' in err_msg or
            err_msg.startswith('Missing ship-to address') or
            err_msg.startswith('Ship-from defaults are missing') or
            err_msg.startswith('Package dimensions are required') or
            err_msg.startswith('Package weight is required')
        ):
            return jsonify({'success': False, 'error': err_msg}), 400
        ss_config.logger.error(f"labelmaster:buy: {e}", exc_info=True)
        return jsonify({'success': False, 'error': err_msg}), 500


def api_labelmaster_print():
    """Return the label URL for printing if purchased."""
    try:
        data = request.json or {}
        order_id = (data.get('order_id') or '').strip()
        store = (data.get('store') or '').strip().lower()
        if not order_id or store not in ('ebay', 'amazon'):
            return jsonify({'success': False, 'error': 'order_id and store are required'}), 400
        order = _labelmaster_find_order(order_id, store)
        if not order:
            return jsonify({'success': False, 'error': 'Order not found'}), 404

        label = _labelmaster_latest_label(store, order.get('order_id') or order_id)
        if not label:
            return jsonify({'success': False, 'error': 'Label not purchased yet'}), 400

        if label.get('label_path'):
            label_url = url_for('api_labelmaster_label_file', store=store, order_id=order.get('order_id') or order_id)
            return jsonify({'success': True, 'label_url': label_url})

        if label.get('label_url'):
            return jsonify({'success': True, 'label_url': label.get('label_url')})

        return jsonify({'success': False, 'error': 'Label file missing'}), 404
    except Exception as e:
        return jsonify({'success': False, 'error': ss_errors._safe_error(e, 'labelmaster:print')}), 500


def api_labelmaster_label_file():
    try:
        store = (request.args.get('store') or '').strip().lower()
        order_id = (request.args.get('order_id') or '').strip()
        if not order_id or store not in ('ebay', 'amazon'):
            return jsonify({'success': False, 'error': 'order_id and store are required'}), 400

        label = _labelmaster_latest_label(store, order_id)
        if not label or not label.get('label_path'):
            return jsonify({'success': False, 'error': 'Label file not found'}), 404

        label_path = label.get('label_path')
        if not label_path or not os.path.exists(label_path):
            return jsonify({'success': False, 'error': 'Label file not found on disk'}), 404
        mime = 'application/pdf'
        ext = os.path.splitext(label_path)[1].lower()
        if ext == '.png':
            mime = 'image/png'
        elif ext == '.zpl':
            mime = 'application/octet-stream'
        return send_file(label_path, mimetype=mime)
    except Exception as e:
        return jsonify({'success': False, 'error': ss_errors._safe_error(e, 'labelmaster:label_file')}), 500
