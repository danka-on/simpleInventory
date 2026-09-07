"""Ebay policies for Sweet Shelves."""

import json
import requests
import threading
import time
from flask import jsonify, request
from token_manager import get_access_token
from . import errors as ss_errors, listing_settings as ss_listing_settings


def _ebay_api_request(method, path, *, params=None, payload=None, timeout=30):
    token = get_access_token()  # refreshes if expired
    headers_local = {
        'Authorization': f'Bearer {token}',
        'Content-Type': 'application/json',
        'Accept': 'application/json',
        'Content-Language': 'en-US'
    }
    url = f"https://api.ebay.com{path}"
    resp = requests.request(method, url, headers=headers_local, params=params, json=payload, timeout=timeout)
    return resp


def _ebay_extract_error(resp):
    try:
        data = resp.json()
        # Common eBay error schema: { errors: [ {message, errorId, ...} ] }
        errors = data.get('errors') if isinstance(data, dict) else None
        if errors and isinstance(errors, list):
            first = errors[0] or {}
            msg = first.get('message') or first.get('longMessage') or json.dumps(first)
            return f"eBay API error ({resp.status_code}): {msg}"
        if isinstance(data, dict) and data.get('message'):
            return f"eBay API error ({resp.status_code}): {data.get('message')}"
        return f"eBay API error ({resp.status_code}): {resp.text[:300]}"
    except Exception:
        return f"eBay API error ({resp.status_code}): {resp.text[:300]}"


def _extract_missing_ebay_aspect(error_text):
    """
    Best-effort parser for eBay errors like:
    'The item specific Brand is missing...'
    Returns the aspect name (e.g. 'Brand') or ''.
    """
    s = (error_text or '').strip()
    if not s:
        return ''
    low = s.lower()
    marker = 'item specific '
    end_marker = ' is missing'
    i = low.find(marker)
    if i >= 0:
        j = low.find(end_marker, i + len(marker))
        if j > i:
            raw = s[i + len(marker):j].strip(" .:;,-")
            if raw:
                return raw
    # Fallback pattern: "Add Brand to this listing..."
    marker2 = 'add '
    marker3 = ' to this listing'
    i2 = low.find(marker2)
    if i2 >= 0:
        j2 = low.find(marker3, i2 + len(marker2))
        if j2 > i2:
            raw2 = s[i2 + len(marker2):j2].strip(" .:;,-")
            if raw2:
                return raw2
    return ''


def api_listingagent_ebay_locations():
    """Fetch inventory locations (locationKey) from eBay (requires sell.inventory scope)."""
    try:
        force_refresh = (request.args.get('refresh') or '').strip().lower() in ('1', 'true', 'yes')
        locations = _listingagent_get_ebay_locations(force_refresh=force_refresh)
        return jsonify({
            'success': True,
            'data': {
                'locations': locations,
                'total': len(locations),
            }
        })
    except ss_errors._ListingAgentUserError as e:
        payload = {'success': False, 'error': str(e)}
        payload.update(e.extra or {})
        return jsonify(payload), e.status_code
    except Exception as e:
        return jsonify({'success': False, 'error': ss_errors._safe_error(e, 'listingagent:ebay_locations')}), 500


_LISTINGAGENT_EBAY_POLICIES_CACHE = {}


_LISTINGAGENT_EBAY_POLICIES_LOCK = threading.Lock()


_LISTINGAGENT_EBAY_LOCATIONS_CACHE = {'ts': 0.0, 'keys': [], 'locations': []}


_LISTINGAGENT_EBAY_LOCATIONS_LOCK = threading.Lock()


_LISTINGAGENT_EBAY_CONDITIONS_CACHE = {}


_LISTINGAGENT_EBAY_CONDITIONS_LOCK = threading.Lock()


def _listingagent_get_ebay_business_policies(marketplace_id: str):
    """Fetch eBay business policies (fulfillment/payment/return). Cached in-memory."""
    marketplace_id = (marketplace_id or 'EBAY_US').strip() or 'EBAY_US'
    cache_key = marketplace_id
    now = time.time()

    cached = _LISTINGAGENT_EBAY_POLICIES_CACHE.get(cache_key)
    if cached and (now - float(cached.get('ts') or 0)) < 6 * 3600:
        return cached.get('data') or {}

    def _fetch(path):
        # Sell Account API endpoints only take marketplace_id; passing unknown params can 400.
        resp = _ebay_api_request('GET', path, params={'marketplace_id': marketplace_id})
        if resp.status_code == 403:
            # Most common cause: missing OAuth scope for Sell Account API.
            raise ss_errors._ListingAgentUserError(
                "eBay API error (403): Forbidden. Your eBay OAuth token likely does not include the "
                "`sell.account` scope required to load business policies. Re-authorize your eBay app "
                "with `https://api.ebay.com/oauth/api_scope/sell.account` (or "
                "`https://api.ebay.com/oauth/api_scope/sell.account.readonly`) and regenerate "
                "`tokens.json`.",
                status_code=400,
                extra={'needs_scope': 'sell.account'}
            )
        if resp.status_code == 401:
            raise ss_errors._ListingAgentUserError(
                "eBay API error (401): Unauthorized. Your token may be expired or invalid. "
                "Try re-authorizing and regenerating `tokens.json`.",
                status_code=400
            )
        if resp.status_code >= 400:
            raise ss_errors._ListingAgentUserError(_ebay_extract_error(resp), status_code=400)
        return resp.json() if resp.text else {}

    with _LISTINGAGENT_EBAY_POLICIES_LOCK:
        cached = _LISTINGAGENT_EBAY_POLICIES_CACHE.get(cache_key)
        now = time.time()
        if cached and (now - float(cached.get('ts') or 0)) < 6 * 3600:
            return cached.get('data') or {}

        ful = _fetch('/sell/account/v1/fulfillment_policy')
        pay = _fetch('/sell/account/v1/payment_policy')
        ret = _fetch('/sell/account/v1/return_policy')

        def _norm_list(payload, list_key, id_key):
            items = payload.get(list_key) or payload.get(list_key[0].upper() + list_key[1:]) or []
            out = []
            if isinstance(items, list):
                for it in items:
                    if not isinstance(it, dict):
                        continue
                    pid = (it.get(id_key) or it.get(id_key[0].lower() + id_key[1:]) or '').strip()
                    if not pid:
                        continue
                    name = (it.get('name') or it.get('policyName') or it.get('policy_name') or '').strip()
                    out.append({
                        'id': pid,
                        'name': name or pid,
                        'categoryTypes': it.get('categoryTypes') or it.get('category_types') or [],
                    })
            out.sort(key=lambda x: (x.get('name') or '').lower())
            return out

        data = {
            'fulfillment': _norm_list(ful, 'fulfillmentPolicies', 'fulfillmentPolicyId'),
            'payment': _norm_list(pay, 'paymentPolicies', 'paymentPolicyId'),
            'returns': _norm_list(ret, 'returnPolicies', 'returnPolicyId'),
        }

        _LISTINGAGENT_EBAY_POLICIES_CACHE[cache_key] = {'ts': now, 'data': data}
        return data


def _listingagent_get_ebay_locations(*, force_refresh=False):
    """Fetch seller inventory locations from eBay Inventory API and cache them in-memory."""
    now = time.time()
    cached = _LISTINGAGENT_EBAY_LOCATIONS_CACHE
    if (not force_refresh) and cached.get('locations') and (now - float(cached.get('ts') or 0)) < 10 * 60:
        return [dict(loc) for loc in (cached.get('locations') or [])]

    with _LISTINGAGENT_EBAY_LOCATIONS_LOCK:
        cached = _LISTINGAGENT_EBAY_LOCATIONS_CACHE
        if (not force_refresh) and cached.get('locations') and (now - float(cached.get('ts') or 0)) < 10 * 60:
            return [dict(loc) for loc in (cached.get('locations') or [])]

        locations_out = []
        keys = []
        offset = 0
        limit = 200
        seen = set()
        # Inventory API pagination via offset/limit
        for _ in range(5):
            resp = _ebay_api_request('GET', '/sell/inventory/v1/location', params={'limit': limit, 'offset': offset})
            if resp.status_code >= 400:
                raise ss_errors._ListingAgentUserError(_ebay_extract_error(resp), status_code=400)
            payload = resp.json() if resp.text else {}
            locations = payload.get('locations') or payload.get('Locations') or []
            if not isinstance(locations, list) or not locations:
                break
            for loc in locations:
                if not isinstance(loc, dict):
                    continue
                key = (loc.get('merchantLocationKey') or loc.get('locationKey') or '').strip()
                if not key or key in seen:
                    continue
                seen.add(key)
                keys.append(key)
                normalized = dict(loc)
                if not (normalized.get('locationKey') or '').strip():
                    normalized['locationKey'] = key
                if not (normalized.get('merchantLocationKey') or '').strip():
                    normalized['merchantLocationKey'] = key
                locations_out.append(normalized)
            if len(locations) < limit:
                break
            offset += limit

        _LISTINGAGENT_EBAY_LOCATIONS_CACHE['ts'] = time.time()
        _LISTINGAGENT_EBAY_LOCATIONS_CACHE['keys'] = list(keys)
        _LISTINGAGENT_EBAY_LOCATIONS_CACHE['locations'] = [dict(loc) for loc in locations_out]
        return [dict(loc) for loc in locations_out]


def _listingagent_get_ebay_location_keys(*, force_refresh=False):
    """Fetch seller inventory location keys from eBay Inventory API."""
    locations = _listingagent_get_ebay_locations(force_refresh=force_refresh)
    return [
        (loc.get('merchantLocationKey') or loc.get('locationKey') or '').strip()
        for loc in locations
        if (loc.get('merchantLocationKey') or loc.get('locationKey') or '').strip()
    ]


def _listingagent_resolve_ebay_location_key(preferred_key, *, force_refresh=False):
    """
    Resolve a usable merchant location key.
    Returns: (resolved_key, auto_fixed_bool, all_keys)
    """
    preferred = (preferred_key or '').strip()
    keys = _listingagent_get_ebay_location_keys(force_refresh=force_refresh)
    if not keys:
        raise ss_errors._ListingAgentUserError(
            "No eBay inventory locations found. Create one in eBay Seller Hub (Inventory Locations), then retry.",
            status_code=400,
            extra={'needs_setup': 'ebay_inventory_location'}
        )
    key_set = {k.lower(): k for k in keys}
    if preferred and preferred.lower() in key_set:
        return key_set[preferred.lower()], False, keys
    return keys[0], True, keys


def api_listingagent_ebay_business_policies():
    """Get eBay business policies (fulfillment/payment/return) for dropdowns."""
    try:
        settings = ss_listing_settings._listingagent_get_settings()
        marketplace_id = (request.args.get('marketplaceId') or settings.get('ebay_marketplace_id') or 'EBAY_US').strip() or 'EBAY_US'
        data = _listingagent_get_ebay_business_policies(marketplace_id)
        out = {'success': True, 'marketplaceId': marketplace_id}
        out.update(data or {})
        return jsonify(out)
    except ss_errors._ListingAgentUserError as e:
        payload = {'success': False, 'error': str(e)}
        payload.update(e.extra or {})
        return jsonify(payload), e.status_code
    except Exception as e:
        return jsonify({'success': False, 'error': ss_errors._safe_error(e, 'listingagent:ebay_business_policies')}), 500


def api_listingagent_ebay_offers_for_sku():
    """Lookup Inventory offers for a SKU (helps edit existing listings)."""
    try:
        sku = (request.args.get('sku') or '').strip()
        if not sku:
            return jsonify({'success': False, 'error': 'sku is required'}), 400

        include_raw = (request.args.get('raw') or '').lower() == 'true'
        resp = _ebay_api_request('GET', '/sell/inventory/v1/offer', params={'sku': sku, 'limit': 50})
        if resp.status_code >= 400:
            return jsonify({'success': False, 'error': _ebay_extract_error(resp)}), 400

        payload = resp.json() if resp.text else {}
        offers = payload.get('offers') or payload.get('Offers') or []
        results = []
        if isinstance(offers, list):
            for o in offers:
                if not isinstance(o, dict):
                    continue
                results.append({
                    'offerId': o.get('offerId') or o.get('offer_id') or '',
                    'listingId': o.get('listingId') or o.get('listing_id') or '',
                    'marketplaceId': o.get('marketplaceId') or o.get('marketplace_id') or '',
                    'status': o.get('status') or '',
                    'format': o.get('format') or '',
                    'availableQuantity': o.get('availableQuantity') if o.get('availableQuantity') is not None else o.get('available_quantity'),
                    'categoryId': o.get('categoryId') or o.get('category_id') or '',
                    'pricingSummary': o.get('pricingSummary') or o.get('pricing_summary') or {},
                })

        out = {'success': True, 'data': {'sku': sku, 'offers': results}}
        if include_raw:
            out['raw'] = payload
        return jsonify(out)
    except Exception as e:
        return jsonify({'success': False, 'error': ss_errors._safe_error(e, 'listingagent:ebay_offers_for_sku')}), 500


def _listingagent_is_ebay_eps_url(url: str) -> bool:
    """
    eBay EPS-hosted image URLs are typically on ebayimg/ebaystatic domains.
    """
    s = (url or '').strip()
    if not s:
        return False
    try:
        from urllib.parse import urlparse
        u = urlparse(s)
        host = (u.netloc or '').lower()
        if ('ebayimg.com' in host) or ('ebaystatic.com' in host):
            return True
        # Catch proxied/encoded EPS URLs where host is not directly ebayimg.
        low = s.lower()
        return ('ebayimg.com' in low) or ('ebaystatic.com' in low)
    except Exception:
        low = s.lower()
        return ('ebayimg.com' in low) or ('ebaystatic.com' in low)


def _listingagent_is_amazon_image_url(url: str) -> bool:
    """
    Amazon-hosted catalog/listing image host detection.
    """
    try:
        from urllib.parse import urlparse
        u = urlparse((url or '').strip())
        host = (u.netloc or '').lower()
        path = (u.path or '').lower()
        return (
            ('media-amazon.com' in host)
            or ('ssl-images-amazon' in host)
            or ('images-na.ssl-images-amazon' in host)
            or ('images-amazon' in host)
            or (('amazon.' in host) and ('/images/' in path))
        )
    except Exception:
        return False


def _listingagent_dedupe_image_urls(images):
    urls = [i.strip() for i in (images or []) if isinstance(i, str) and i.strip()]
    deduped = []
    seen = set()
    for u in urls:
        if u in seen:
            continue
        seen.add(u)
        deduped.append(u)
    return deduped


def _listingagent_has_mixed_ebay_image_sources(images):
    urls = _listingagent_dedupe_image_urls(images)
    eps = [u for u in urls if _listingagent_is_ebay_eps_url(u)]
    non_eps = [u for u in urls if not _listingagent_is_ebay_eps_url(u)]
    return bool(eps and non_eps)


def _listingagent_prefer_single_ebay_image_source(images):
    """
    Fallback selector when eBay rejects mixed EPS + non-EPS image sets.
    """
    urls = _listingagent_dedupe_image_urls(images)
    eps = [u for u in urls if _listingagent_is_ebay_eps_url(u)]
    non_eps = [u for u in urls if not _listingagent_is_ebay_eps_url(u)]
    if not eps or not non_eps:
        return urls
    # Prefer self-hosted/non-EPS when mixed so local/uploaded images win.
    return non_eps


def _listingagent_is_likely_ebay_image_mix_error(error_text: str) -> bool:
    s = (error_text or '').strip().lower()
    if not s:
        return False
    if not any(k in s for k in ('image', 'picture', 'imageurl', 'image urls')):
        return False
    return any(k in s for k in (
        'only one',
        'one type',
        'mixed',
        'self-hosted',
        'self hosted',
        'not supported',
        'not allowed',
        'invalid'
    ))


def _build_ebay_inventory_item_payload(upc, title, description, images, quantity, condition, *, aspects=None, epid='', condition_description=''):
    images = _listingagent_dedupe_image_urls(images)

    # Remove Amazon-hosted images for eBay listings when other candidates exist.
    non_amazon_images = [u for u in images if not _listingagent_is_amazon_image_url(u)]
    if non_amazon_images:
        images = non_amazon_images

    # eBay does not allow mixing EPS and self-hosted image URLs in one listing.
    # Prefer self-hosted/non-EPS when mixed.
    non_eps_images = [u for u in images if not _listingagent_is_ebay_eps_url(u)]
    if non_eps_images and len(non_eps_images) != len(images):
        images = non_eps_images

    # eBay limits imageUrls to 12
    images = images[:12]
    payload = {
        'availability': {
            'shipToLocationAvailability': {
                'quantity': int(quantity or 1)
            }
        },
        'condition': condition or 'USED_GOOD',
        'product': {
            'title': title or upc,
            'description': description or (title or upc),
            'upc': [upc],
        }
    }
    if images:
        payload['product']['imageUrls'] = images
    epid = str(epid or '').strip()
    if epid:
        payload['product']['epid'] = epid
    condition_description = str(condition_description or '').strip()
    if condition_description:
        payload['conditionDescription'] = condition_description
    if aspects and isinstance(aspects, dict):
        clean = {}
        for k, v in aspects.items():
            name = str(k or '').strip()
            if not name:
                continue
            vals = []
            if isinstance(v, str):
                vals = [v.strip()]
            elif isinstance(v, (list, tuple)):
                vals = [str(x).strip() for x in v if str(x).strip()]
            elif v is not None:
                vals = [str(v).strip()]
            vals = [x for x in vals if x]
            if vals:
                clean[name] = vals[:20]
        if clean:
            payload['product']['aspects'] = clean
    return payload


def _build_ebay_offer_payload(sku, marketplace_id, currency, price, quantity, category_id, listing_description,
                             merchant_location_key, fulfillment_policy_id, payment_policy_id, return_policy_id,
                             listing_duration='GTC', format_='FIXED_PRICE'):
    payload = {
        'sku': sku,
        'marketplaceId': marketplace_id,
        'format': format_,
    }
    if quantity is not None:
        payload['availableQuantity'] = int(quantity)
    if listing_duration:
        payload['listingDuration'] = listing_duration
    if category_id:
        payload['categoryId'] = str(category_id)
    if listing_description:
        payload['listingDescription'] = listing_description
    if merchant_location_key:
        payload['merchantLocationKey'] = str(merchant_location_key)
    if price is not None:
        payload['pricingSummary'] = {
            'price': {'currency': currency or 'USD', 'value': f"{float(price):.2f}"}
        }
    policies = {}
    if fulfillment_policy_id:
        policies['fulfillmentPolicyId'] = str(fulfillment_policy_id)
    if payment_policy_id:
        policies['paymentPolicyId'] = str(payment_policy_id)
    if return_policy_id:
        policies['returnPolicyId'] = str(return_policy_id)
    if policies:
        payload['listingPolicies'] = policies
    # Let eBay enrich if it can match a catalog product
    payload['includeCatalogProductDetails'] = True
    return payload


def _listingagent_find_existing_offer_for_sku(sku: str, marketplace_id: str = ''):
    """Find an existing offer for a SKU and optionally filter by marketplace."""
    sku = (sku or '').strip()
    marketplace_id = (marketplace_id or '').strip()
    if not sku:
        return None
    resp = _ebay_api_request('GET', '/sell/inventory/v1/offer', params={'sku': sku, 'limit': 50})
    if resp.status_code >= 400:
        return None
    payload = resp.json() if resp.text else {}
    offers = payload.get('offers') or payload.get('Offers') or []
    if not isinstance(offers, list):
        return None

    scored = []
    for o in offers:
        if not isinstance(o, dict):
            continue
        offer_id = (o.get('offerId') or o.get('offer_id') or '').strip()
        if not offer_id:
            continue
        mp = (o.get('marketplaceId') or o.get('marketplace_id') or '').strip()
        if marketplace_id and mp and mp != marketplace_id:
            continue
        status = (o.get('status') or '').strip().upper()
        score = 0
        if status == 'UNPUBLISHED':
            score = 3
        elif status == 'PUBLISHED':
            score = 2
        elif status:
            score = 1
        scored.append((score, o))
    if not scored:
        return None
    scored.sort(key=lambda x: x[0], reverse=True)
    return scored[0][1]


def _listingagent_condition_enum_from_policy(condition_id, condition_desc=''):
    cid = str(condition_id or '').strip()
    cdesc = (condition_desc or '').strip().lower()
    direct = {
        '1000': 'NEW',
        '1500': 'NEW_OTHER',
        '1750': 'NEW_WITH_DEFECTS',
        '2000': 'CERTIFIED_REFURBISHED',
        '2010': 'EXCELLENT_REFURBISHED',
        '2020': 'VERY_GOOD_REFURBISHED',
        '2030': 'GOOD_REFURBISHED',
        '2500': 'SELLER_REFURBISHED',
        '2750': 'LIKE_NEW',
        '3000': 'USED_EXCELLENT',
        '4000': 'USED_VERY_GOOD',
        '5000': 'USED_GOOD',
        '6000': 'USED_ACCEPTABLE',
        '7000': 'FOR_PARTS_OR_NOT_WORKING',
    }
    if cid in direct:
        return direct[cid]

    # Fallback by description text when id is unknown/new.
    if ('for parts' in cdesc) or ('not working' in cdesc):
        return 'FOR_PARTS_OR_NOT_WORKING'
    if 'certified refurbished' in cdesc:
        return 'CERTIFIED_REFURBISHED'
    if 'seller refurbished' in cdesc:
        return 'SELLER_REFURBISHED'
    if ('refurbished' in cdesc) and ('excellent' in cdesc):
        return 'EXCELLENT_REFURBISHED'
    if ('refurbished' in cdesc) and ('very good' in cdesc):
        return 'VERY_GOOD_REFURBISHED'
    if ('refurbished' in cdesc) and ('good' in cdesc):
        return 'GOOD_REFURBISHED'
    if ('new with defects' in cdesc) or ('new w/ defects' in cdesc):
        return 'NEW_WITH_DEFECTS'
    if ('new other' in cdesc) or ('open box' in cdesc):
        return 'NEW_OTHER'
    if 'like new' in cdesc:
        return 'LIKE_NEW'
    if 'excellent' in cdesc:
        return 'USED_EXCELLENT'
    if 'very good' in cdesc:
        return 'USED_VERY_GOOD'
    if 'acceptable' in cdesc:
        return 'USED_ACCEPTABLE'
    if 'good' in cdesc:
        return 'USED_GOOD'
    if 'new' in cdesc:
        return 'NEW'
    if 'used' in cdesc:
        return 'USED_GOOD'
    return ''


def _listingagent_get_ebay_allowed_condition_enums(category_id: str, marketplace_id: str):
    category_id = (category_id or '').strip()
    marketplace_id = (marketplace_id or 'EBAY_US').strip() or 'EBAY_US'
    if not category_id:
        return []

    cache_key = f'{marketplace_id}|{category_id}'
    now = time.time()
    cached = _LISTINGAGENT_EBAY_CONDITIONS_CACHE.get(cache_key)
    if cached and (now - float(cached.get('ts') or 0)) < 12 * 3600:
        return list(cached.get('data') or [])

    with _LISTINGAGENT_EBAY_CONDITIONS_LOCK:
        cached = _LISTINGAGENT_EBAY_CONDITIONS_CACHE.get(cache_key)
        now = time.time()
        if cached and (now - float(cached.get('ts') or 0)) < 12 * 3600:
            return list(cached.get('data') or [])

        out = []
        seen = set()
        try:
            resp = _ebay_api_request(
                'GET',
                f'/sell/metadata/v1/marketplace/{marketplace_id}/get_item_condition_policies',
                params={'filter': f'categoryIds:{{{category_id}}}'}
            )
            if resp.status_code < 400:
                payload = resp.json() if resp.text else {}
                policies = payload.get('itemConditionPolicies') or payload.get('item_condition_policies') or []
                if isinstance(policies, list):
                    for p in policies:
                        if not isinstance(p, dict):
                            continue
                        cat = str(p.get('categoryId') or p.get('category_id') or '').strip()
                        if cat and cat != category_id:
                            continue
                        conds = p.get('itemConditions') or p.get('item_conditions') or []
                        if not isinstance(conds, list):
                            continue
                        for c in conds:
                            if not isinstance(c, dict):
                                continue
                            enum_val = _listingagent_condition_enum_from_policy(
                                c.get('conditionId') or c.get('condition_id') or '',
                                c.get('conditionDescription') or c.get('condition_description') or ''
                            )
                            if enum_val and enum_val not in seen:
                                seen.add(enum_val)
                                out.append(enum_val)
        except Exception:
            out = []

        _LISTINGAGENT_EBAY_CONDITIONS_CACHE[cache_key] = {'ts': time.time(), 'data': list(out)}
        return list(out)


def _listingagent_get_condition_candidates(current_condition='', *, category_id='', marketplace_id=''):
    default_order = [
        'NEW',
        'LIKE_NEW',
        'NEW_OTHER',
        'NEW_WITH_DEFECTS',
        'CERTIFIED_REFURBISHED',
        'EXCELLENT_REFURBISHED',
        'VERY_GOOD_REFURBISHED',
        'GOOD_REFURBISHED',
        'SELLER_REFURBISHED',
        'USED_EXCELLENT',
        'USED_VERY_GOOD',
        'USED_GOOD',
        'USED_ACCEPTABLE',
        'FOR_PARTS_OR_NOT_WORKING',
    ]
    allowed = _listingagent_get_ebay_allowed_condition_enums(category_id, marketplace_id)
    out = []
    seen = set()

    def _add(v):
        vv = (v or '').strip().upper()
        if not vv or vv in seen:
            return
        seen.add(vv)
        out.append(vv)

    _add(current_condition)
    for c in allowed:
        _add(c)
    for c in default_order:
        _add(c)
    return out


def _listingagent_put_inventory_item_with_condition_fallback(sku: str, inventory_item_payload: dict, *, category_id='', marketplace_id=''):
    """
    PUT inventory item and auto-retry with fallback conditions when category rejects condition.
    Returns: (resolved_payload, condition_autofixed_bool, original_condition, tried_conditions)
    """
    from urllib.parse import quote
    sku_encoded = quote((sku or '').strip(), safe='')
    payload = dict(inventory_item_payload or {})
    original_condition = (payload.get('condition') or '').strip()
    condition_autofixed = False
    tried = []

    resp_item = _ebay_api_request('PUT', f'/sell/inventory/v1/inventory_item/{sku_encoded}', payload=payload)
    if resp_item.status_code < 400:
        return payload, condition_autofixed, original_condition, tried

    err_item = _ebay_extract_error(resp_item)
    err_item_l = err_item.lower()
    if ('invalid item condition information' not in err_item_l) and ('condition id is invalid' not in err_item_l):
        # If mixed EPS + non-EPS images trigger a rejection, retry once with a single source set.
        product_obj = payload.get('product') if isinstance(payload.get('product'), dict) else {}
        image_urls = _listingagent_dedupe_image_urls((product_obj or {}).get('imageUrls') or [])
        if image_urls and _listingagent_has_mixed_ebay_image_sources(image_urls) and _listingagent_is_likely_ebay_image_mix_error(err_item):
            fallback_images = _listingagent_prefer_single_ebay_image_source(image_urls)[:12]
            if fallback_images and fallback_images != image_urls:
                retry_payload = dict(payload)
                product_retry = dict(product_obj or {})
                product_retry['imageUrls'] = fallback_images
                retry_payload['product'] = product_retry
                resp_item_retry = _ebay_api_request('PUT', f'/sell/inventory/v1/inventory_item/{sku_encoded}', payload=retry_payload)
                if resp_item_retry.status_code < 400:
                    payload = retry_payload
                    return payload, condition_autofixed, original_condition, tried
                err_item = _ebay_extract_error(resp_item_retry)

        missing_aspect = _extract_missing_ebay_aspect(err_item)
        raise ss_errors._ListingAgentUserError(
            err_item,
            status_code=400,
            extra={'inventoryItem': payload, 'missingAspect': missing_aspect}
        )

    # Prefer category-allowed condition candidates from metadata; fall back to defaults.
    fallback_conditions = _listingagent_get_condition_candidates(
        payload.get('condition') or '',
        category_id=category_id,
        marketplace_id=marketplace_id
    )
    current = (payload.get('condition') or '').strip().upper()
    if current:
        tried.append(current)
    for cond in fallback_conditions:
        if cond == current:
            continue
        retry_payload = dict(payload)
        retry_payload['condition'] = cond
        resp_item_retry = _ebay_api_request('PUT', f'/sell/inventory/v1/inventory_item/{sku_encoded}', payload=retry_payload)
        if resp_item_retry.status_code < 400:
            payload = retry_payload
            condition_autofixed = True
            return payload, condition_autofixed, original_condition, tried
        tried.append(cond)

    missing_aspect = _extract_missing_ebay_aspect(err_item)
    raise ss_errors._ListingAgentUserError(
        err_item,
        status_code=400,
        extra={
            'inventoryItem': payload,
            'conditionTried': tried,
            'missingAspect': missing_aspect
        }
    )


