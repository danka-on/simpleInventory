"""Ebay catalog for Sweet Shelves."""

import base64
import ebay_mapping
import os
import re
import requests
import threading
import time
from flask import jsonify, request
from token_manager import get_access_token
from . import (
    config as ss_config, ebay_policies as ss_ebay_policies, errors as ss_errors, listing_settings as
    ss_listing_settings,
)


_LISTINGAGENT_EBAY_APP_TOKEN = {'access_token': None, 'expires_at': 0.0, 'scope': ''}


_LISTINGAGENT_EBAY_APP_TOKEN_LOCK = threading.Lock()


def _listingagent_get_ebay_app_token(scope=None):
    scope = (scope or os.getenv('EBAY_BUY_SCOPE') or 'https://api.ebay.com/oauth/api_scope').strip()
    now = time.time()
    cached = _LISTINGAGENT_EBAY_APP_TOKEN
    if cached.get('access_token') and cached.get('scope') == scope and now < float(cached.get('expires_at') or 0) - 60:
        return cached['access_token']

    if not ss_config.CLIENT_ID or not ss_config.CLIENT_SECRET:
        raise ss_errors._ListingAgentUserError('Missing EBAY_CLIENT_ID / EBAY_CLIENT_SECRET in environment (.env)', status_code=503)

    with _LISTINGAGENT_EBAY_APP_TOKEN_LOCK:
        cached = _LISTINGAGENT_EBAY_APP_TOKEN
        now = time.time()
        if cached.get('access_token') and cached.get('scope') == scope and now < float(cached.get('expires_at') or 0) - 60:
            return cached['access_token']

        encoded = base64.b64encode(f"{ss_config.CLIENT_ID}:{ss_config.CLIENT_SECRET}".encode('utf-8')).decode('utf-8')
        headers = {
            'Content-Type': 'application/x-www-form-urlencoded',
            'Authorization': f"Basic {encoded}",
        }
        data = {
            'grant_type': 'client_credentials',
            'scope': scope
        }
        resp = requests.post('https://api.ebay.com/identity/v1/oauth2/token', headers=headers, data=data, timeout=30)
        if resp.status_code >= 400:
            try:
                j = resp.json()
                msg = j.get('error_description') or j.get('error') or resp.text[:200]
            except Exception:
                msg = resp.text[:200]
            raise ss_errors._ListingAgentUserError(f"eBay app token failed: {msg}", status_code=503)

        j = resp.json() if resp.text else {}
        access_token = (j.get('access_token') or '').strip()
        expires_in = int(j.get('expires_in') or 0)
        if not access_token:
            raise ss_errors._ListingAgentUserError('eBay app token response missing access_token', status_code=503)

        _LISTINGAGENT_EBAY_APP_TOKEN.update({
            'access_token': access_token,
            'expires_at': time.time() + max(60, expires_in),
            'scope': scope
        })
        return access_token


def _ebay_buy_api_request(method, path, *, params=None, timeout=30, marketplace_id='EBAY_US', scope=None):
    token = _listingagent_get_ebay_app_token(scope=scope)
    headers_local = {
        'Authorization': f'Bearer {token}',
        'Content-Type': 'application/json',
        'Accept': 'application/json',
        'Accept-Language': 'en-US',
        # Strongly recommended for Browse APIs.
        'X-EBAY-C-MARKETPLACE-ID': marketplace_id or 'EBAY_US',
    }
    url = f"https://api.ebay.com{path}"
    resp = requests.request(method, url, headers=headers_local, params=params, timeout=timeout)
    return resp


def api_listingagent_ebay_comps():
    """Fetch live eBay marketplace comps by UPC (Buy Browse API)."""
    try:
        upc = (request.args.get('upc') or request.args.get('gtin') or '').strip()
        if not upc:
            raise ss_errors._ListingAgentUserError('upc is required', status_code=400)

        limit = ss_listing_settings._listingagent_parse_int(request.args.get('limit'), 12) or 12
        limit = max(1, min(limit, 50))
        include_raw = (request.args.get('raw') or '').lower() == 'true'

        settings = ss_listing_settings._listingagent_get_settings()
        marketplace_id = (request.args.get('marketplaceId') or settings.get('ebay_marketplace_id') or 'EBAY_US').strip()
        sort = (request.args.get('sort') or '').strip()

        params = {'q': upc, 'limit': limit}
        if sort:
            params['sort'] = sort

        resp = _ebay_buy_api_request('GET', '/buy/browse/v1/item_summary/search', params=params, marketplace_id=marketplace_id)
        if resp.status_code >= 400:
            raise ss_errors._ListingAgentUserError(ss_ebay_policies._ebay_extract_error(resp), status_code=400)

        payload = resp.json() if resp.text else {}
        items = payload.get('itemSummaries') or payload.get('item_summaries') or []

        results = []
        for it in items[:limit]:
            price = it.get('price') or {}

            # shipping cost (best-effort; often empty)
            shipping = None
            ship_opts = it.get('shippingOptions') or it.get('shipping_options') or []
            if ship_opts and isinstance(ship_opts, list):
                sc = (ship_opts[0] or {}).get('shippingCost') or (ship_opts[0] or {}).get('shipping_cost') or {}
                if isinstance(sc, dict) and sc.get('value') is not None:
                    shipping = {'value': sc.get('value'), 'currency': sc.get('currency')}

            image_url = ''
            img = it.get('image') or {}
            if isinstance(img, dict):
                image_url = (img.get('imageUrl') or img.get('image_url') or '').strip()
            if not image_url:
                thumbs = it.get('thumbnailImages') or it.get('thumbnail_images') or []
                if thumbs and isinstance(thumbs, list):
                    t0 = thumbs[0] or {}
                    if isinstance(t0, dict):
                        image_url = (t0.get('imageUrl') or t0.get('image_url') or '').strip()

            seller = ''
            seller_obj = it.get('seller') or {}
            if isinstance(seller_obj, dict):
                seller = (seller_obj.get('username') or '').strip()

            results.append({
                'itemId': it.get('itemId') or it.get('item_id') or '',
                'title': it.get('title') or '',
                'price': {'value': price.get('value'), 'currency': price.get('currency')},
                'shipping': shipping,
                'condition': it.get('condition') or '',
                'conditionId': it.get('conditionId') or it.get('condition_id') or '',
                'itemWebUrl': it.get('itemWebUrl') or it.get('item_web_url') or '',
                'image': image_url,
                'seller': seller,
                'buyingOptions': it.get('buyingOptions') or it.get('buying_options') or [],
            })

        out = {'success': True, 'total': payload.get('total') or len(results), 'results': results}
        if include_raw:
            out['raw'] = payload
        return jsonify(out)

    except ss_errors._ListingAgentUserError as e:
        payload = {'success': False, 'error': str(e)}
        payload.update(e.extra or {})
        return jsonify(payload), e.status_code
    except Exception as e:
        return jsonify({'success': False, 'error': ss_errors._safe_error(e, 'listingagent:ebay_comps')}), 500


_LISTINGAGENT_EBAY_CATEGORY_TREE_ID = {}


_LISTINGAGENT_EBAY_CATEGORY_TREE_LOCK = threading.Lock()


def _listingagent_get_ebay_category_tree_id(marketplace_id: str):
    mp = (marketplace_id or 'EBAY_US').strip() or 'EBAY_US'
    cached = _LISTINGAGENT_EBAY_CATEGORY_TREE_ID.get(mp)
    if cached:
        return cached
    with _LISTINGAGENT_EBAY_CATEGORY_TREE_LOCK:
        cached = _LISTINGAGENT_EBAY_CATEGORY_TREE_ID.get(mp)
        if cached:
            return cached
        resp = _ebay_buy_api_request(
            'GET',
            '/commerce/taxonomy/v1/get_default_category_tree_id',
            params={'marketplace_id': mp},
            marketplace_id=mp,
            scope='https://api.ebay.com/oauth/api_scope'
        )
        if resp.status_code >= 400:
            raise ss_errors._ListingAgentUserError(ss_ebay_policies._ebay_extract_error(resp), status_code=400)
        data = resp.json() if resp.text else {}
        tree_id = str(data.get('categoryTreeId') or data.get('category_tree_id') or '').strip()
        if not tree_id:
            raise ss_errors._ListingAgentUserError('eBay taxonomy did not return categoryTreeId', status_code=502, extra={'raw': data})
        _LISTINGAGENT_EBAY_CATEGORY_TREE_ID[mp] = tree_id
        return tree_id


def api_listingagent_ebay_category_suggestions():
    """Suggest eBay categories for a given query using Commerce Taxonomy API."""
    try:
        q = (request.args.get('q') or request.args.get('query') or '').strip()
        if not q:
            raise ss_errors._ListingAgentUserError('q is required', status_code=400)

        limit = ss_listing_settings._listingagent_parse_int(request.args.get('limit'), 12) or 12
        limit = max(1, min(limit, 50))
        include_raw = (request.args.get('raw') or '').lower() == 'true'

        settings = ss_listing_settings._listingagent_get_settings()
        marketplace_id = (request.args.get('marketplaceId') or settings.get('ebay_marketplace_id') or 'EBAY_US').strip()

        category_tree_id = _listingagent_get_ebay_category_tree_id(marketplace_id)

        resp = _ebay_buy_api_request(
            'GET',
            f'/commerce/taxonomy/v1/category_tree/{category_tree_id}/get_category_suggestions',
            params={'q': q},
            marketplace_id=marketplace_id,
            scope='https://api.ebay.com/oauth/api_scope'
        )
        if resp.status_code >= 400:
            raise ss_errors._ListingAgentUserError(ss_ebay_policies._ebay_extract_error(resp), status_code=400)

        payload = resp.json() if resp.text else {}
        suggestions = payload.get('categorySuggestions') or payload.get('category_suggestions') or []

        results = []
        for s in (suggestions or [])[:limit]:
            cat = s.get('category') or {}
            cat_id = str(cat.get('categoryId') or cat.get('category_id') or '').strip()
            cat_name = (cat.get('categoryName') or cat.get('category_name') or '').strip()

            ancestors = s.get('categoryTreeNodeAncestors') or s.get('category_tree_node_ancestors') or []
            path_parts = []
            if isinstance(ancestors, list):
                for a in ancestors:
                    if not isinstance(a, dict):
                        continue
                    nm = (a.get('categoryName') or a.get('category_name') or '').strip()
                    if nm:
                        path_parts.append(nm)
            if cat_name:
                path_parts.append(cat_name)
            path = ' > '.join(path_parts) if path_parts else cat_name

            relevancy = s.get('relevancy')
            try:
                relevancy = float(relevancy) if relevancy is not None else None
            except Exception:
                relevancy = None

            if not cat_id:
                continue
            results.append({
                'categoryId': cat_id,
                'categoryName': cat_name,
                'path': path or cat_name or cat_id,
                'relevancy': relevancy,
            })

        out = {
            'success': True,
            'marketplaceId': marketplace_id,
            'categoryTreeId': category_tree_id,
            'recommended': results[0] if results else None,
            'results': results,
        }
        if include_raw:
            out['raw'] = payload
        return jsonify(out)

    except ss_errors._ListingAgentUserError as e:
        payload = {'success': False, 'error': str(e)}
        payload.update(e.extra or {})
        return jsonify(payload), e.status_code
    except Exception as e:
        return jsonify({'success': False, 'error': ss_errors._safe_error(e, 'listingagent:ebay_category_suggestions')}), 500


_LISTINGAGENT_EBAY_ASPECTS_CACHE = {}


_LISTINGAGENT_EBAY_ASPECTS_LOCK = threading.Lock()


def _listingagent_get_ebay_category_aspects(category_id: str, marketplace_id: str, *, values_limit: int = 140):
    """Fetch item aspects metadata for an eBay category (Commerce Taxonomy). Cached in-memory."""
    category_id = (category_id or '').strip()
    marketplace_id = (marketplace_id or 'EBAY_US').strip() or 'EBAY_US'
    values_limit = int(values_limit or 140)
    values_limit = max(0, min(values_limit, 5000))

    cache_key = (marketplace_id, category_id, values_limit)
    now = time.time()
    cached = _LISTINGAGENT_EBAY_ASPECTS_CACHE.get(cache_key)
    if cached and (now - float(cached.get('ts') or 0)) < 6 * 3600:
        return cached.get('data') or []

    with _LISTINGAGENT_EBAY_ASPECTS_LOCK:
        cached = _LISTINGAGENT_EBAY_ASPECTS_CACHE.get(cache_key)
        if cached and (now - float(cached.get('ts') or 0)) < 6 * 3600:
            return cached.get('data') or []

        category_tree_id = _listingagent_get_ebay_category_tree_id(marketplace_id)
        resp = _ebay_buy_api_request(
            'GET',
            f'/commerce/taxonomy/v1/category_tree/{category_tree_id}/get_item_aspects_for_category',
            params={'category_id': category_id},
            marketplace_id=marketplace_id,
            scope='https://api.ebay.com/oauth/api_scope'
        )
        if resp.status_code >= 400:
            raise ss_errors._ListingAgentUserError(ss_ebay_policies._ebay_extract_error(resp), status_code=400)

        payload = resp.json() if resp.text else {}
        aspects = payload.get('aspects') or payload.get('Aspects') or []

        results = []
        if isinstance(aspects, list):
            for a in aspects:
                if not isinstance(a, dict):
                    continue
                name = (
                    a.get('aspectName')
                    or a.get('localizedAspectName')
                    or a.get('aspect_name')
                    or a.get('localized_aspect_name')
                    or ''
                ).strip()
                if not name:
                    continue
                constraint = a.get('aspectConstraint') or a.get('aspect_constraint') or {}
                if not isinstance(constraint, dict):
                    constraint = {}

                required = bool(constraint.get('aspectRequired') or constraint.get('aspect_required') or False)
                mode = (constraint.get('aspectMode') or constraint.get('aspect_mode') or '').strip()
                max_values = constraint.get('aspectMaxValues')
                if max_values is None:
                    max_values = constraint.get('aspect_max_values')
                try:
                    max_values = int(max_values) if max_values is not None else 1
                except Exception:
                    max_values = 1
                if max_values < 1:
                    max_values = 1

                values_raw = a.get('aspectValues') or a.get('aspect_values') or []
                values_count = len(values_raw) if isinstance(values_raw, list) else 0
                values = []
                if values_limit and isinstance(values_raw, list):
                    for v in values_raw[:values_limit]:
                        if not isinstance(v, dict):
                            continue
                        if isinstance(v, dict):
                            val = (
                                v.get('localizedAspectValue')
                                or v.get('localized_aspect_value')
                                or v.get('localizedValue')
                                or v.get('localized_value')
                                or v.get('value')
                                or ''
                            ).strip()
                        else:
                            val = str(v or '').strip()
                        if val and val not in values:
                            values.append(val)

                results.append({
                    'name': name,
                    'required': required,
                    'mode': mode,
                    'maxValues': max_values,
                    'values': values,
                    'valuesCount': values_count,
                    'valuesTruncated': bool(values_limit and values_count > len(values))
                })

        # Required first, then A-Z
        results.sort(key=lambda x: (0 if x.get('required') else 1, (x.get('name') or '').lower()))

        _LISTINGAGENT_EBAY_ASPECTS_CACHE[cache_key] = {'ts': now, 'data': results}
        return results


def api_listingagent_ebay_category_aspects():
    """Get eBay item specifics (aspects) for a categoryId (Commerce Taxonomy)."""
    try:
        category_id = (request.args.get('categoryId') or request.args.get('category_id') or '').strip()
        if not category_id:
            raise ss_errors._ListingAgentUserError('categoryId is required', status_code=400)

        values_limit = ss_listing_settings._listingagent_parse_int(request.args.get('valuesLimit'), 140) or 140
        include_raw = (request.args.get('raw') or '').lower() == 'true'

        settings = ss_listing_settings._listingagent_get_settings()
        marketplace_id = (request.args.get('marketplaceId') or settings.get('ebay_marketplace_id') or 'EBAY_US').strip()

        aspects = _listingagent_get_ebay_category_aspects(category_id, marketplace_id, values_limit=values_limit)

        out = {
            'success': True,
            'marketplaceId': marketplace_id,
            'categoryId': category_id,
            'aspects': aspects
        }
        if include_raw:
            # Raw is not cached; fetch again with full payload when requested.
            category_tree_id = _listingagent_get_ebay_category_tree_id(marketplace_id)
            resp = _ebay_buy_api_request(
                'GET',
                f'/commerce/taxonomy/v1/category_tree/{category_tree_id}/get_item_aspects_for_category',
                params={'category_id': category_id},
                marketplace_id=marketplace_id,
                scope='https://api.ebay.com/oauth/api_scope'
            )
            out['raw'] = resp.json() if resp.text else {}
        return jsonify(out)
    except ss_errors._ListingAgentUserError as e:
        payload = {'success': False, 'error': str(e)}
        payload.update(e.extra or {})
        return jsonify(payload), e.status_code
    except Exception as e:
        return jsonify({'success': False, 'error': ss_errors._safe_error(e, 'listingagent:ebay_category_aspects')}), 500


def _ebay_catalog_aspects_to_dict(aspects_raw):
    """Normalise eBay catalog aspects to {name: first_value} across Catalog/Browse shapes."""
    out = {}
    if not aspects_raw:
        return out

    def _first_text(value):
        if value is None:
            return ''
        if isinstance(value, (list, tuple)):
            for item in value:
                text = _first_text(item)
                if text:
                    return text
            return ''
        if isinstance(value, dict):
            for key in (
                'localizedAspectValue',
                'localized_aspect_value',
                'localizedValue',
                'localized_value',
                'value',
                'name',
            ):
                text = str(value.get(key) or '').strip()
                if text:
                    return text
            return ''
        return str(value).strip()

    if isinstance(aspects_raw, dict):
        for k, v in aspects_raw.items():
            name = str(k or '').strip()
            val = _first_text(v)
            if name and val:
                out[name] = val
        return out

    if isinstance(aspects_raw, list):
        for item in aspects_raw:
            if not isinstance(item, dict):
                continue
            name = (
                item.get('localizedAspectName')
                or item.get('localized_aspect_name')
                or item.get('localizedName')
                or item.get('localized_name')
                or item.get('name')
                or ''
            ).strip()
            val = _first_text(
                item.get('localizedValues')
                or item.get('localized_values')
                or item.get('values')
                or item.get('aspectValues')
                or item.get('aspect_values')
                or item.get('value')
            )
            if name and val:
                out[name] = val
    return out


def _listingagent_ebay_user_request(method, path, *, params=None, payload=None, timeout=30, marketplace_id=''):
    """Call eBay APIs with the seller user token (Authorization Code flow)."""
    token = get_access_token()
    headers_local = {
        'Authorization': f'Bearer {token}',
        'Content-Type': 'application/json',
        'Accept': 'application/json',
        'Accept-Language': 'en-US',
    }
    if marketplace_id:
        headers_local['X-EBAY-C-MARKETPLACE-ID'] = marketplace_id
    url = f"https://api.ebay.com{path}"
    return requests.request(method, url, headers=headers_local, params=params, json=payload, timeout=timeout)


def _listingagent_ebay_browse_category_fields(item):
    category_id = str(item.get('categoryId') or item.get('category_id') or '').strip()
    category_path = str(item.get('categoryPath') or item.get('category_path') or '').strip()
    category_id_path = str(item.get('categoryIdPath') or item.get('category_id_path') or '').strip()
    leaf_ids = item.get('leafCategoryIds') or item.get('leaf_category_ids') or []

    if not category_id and category_id_path:
        parts = [p.strip() for p in category_id_path.split('|') if p and str(p).strip()]
        if parts:
            category_id = parts[-1]
    if not category_id and isinstance(leaf_ids, list) and leaf_ids:
        category_id = str(leaf_ids[0] or '').strip()

    if not category_path:
        categories = item.get('categories') or []
        if isinstance(categories, list) and categories:
            first = categories[0] if isinstance(categories[0], dict) else {}
            first_name = str(first.get('categoryName') or first.get('category_name') or '').strip()
            if first_name:
                category_path = first_name

    if category_path and '|' in category_path:
        category_path = ' > '.join(part.strip() for part in category_path.split('|') if part and part.strip())

    category_name = ''
    if category_path:
        parts = [p.strip() for p in category_path.split('>') if p and p.strip()]
        if parts:
            category_name = parts[-1]

    return {
        'categoryId': category_id,
        'categoryName': category_name,
        'categoryPath': category_path,
    }


def _listingagent_ebay_browse_aspects_to_dict(item):
    junk_values = {
        'unbranded', 'does not apply', 'n/a', 'na', 'unknown',
        'not applicable', 'see description', 'other', 'generic'
    }
    aspects = _ebay_catalog_aspects_to_dict(
        item.get('localizedAspects')
        or item.get('localized_aspects')
        or item.get('aspects')
    )
    preferred_fields = {
        'Brand': item.get('brand'),
        'Color': item.get('color'),
        'MPN': item.get('mpn'),
        'Model': item.get('model'),
        'Size': item.get('size'),
        'Material': item.get('material'),
        'Pattern': item.get('pattern'),
        'Style': item.get('style'),
        'Department': item.get('department'),
        'Type': item.get('type'),
    }
    fallback_fields = {
        'UPC': item.get('gtin') or item.get('upc'),
        'EAN': item.get('ean'),
        'ISBN': item.get('isbn'),
    }
    for name, value in preferred_fields.items():
        text = str(value or '').strip()
        if text and text.lower() not in junk_values:
            aspects[name] = text
    for name, value in fallback_fields.items():
        text = str(value or '').strip()
        if text and text.lower() not in junk_values and not aspects.get(name):
            aspects[name] = text
    out = {}
    for name, value in aspects.items():
        safe_name = str(name or '').strip()
        safe_value = str(value or '').strip()
        if not safe_name or not safe_value or safe_value.lower() in junk_values:
            continue
        out[safe_name] = safe_value
    return out


def _listingagent_normalize_ebay_catalog_summary(summary):
    """Shape Catalog API product summaries into the UI payload used by listing-agent."""
    if not isinstance(summary, dict):
        return None

    title = str(summary.get('title') or summary.get('name') or '').strip()
    epid = str(summary.get('epid') or summary.get('ePID') or '').strip()

    image_obj = summary.get('image') or {}
    image = ''
    if isinstance(image_obj, dict):
        image = str(image_obj.get('imageUrl') or image_obj.get('image_url') or '').strip()
    if not image:
        image = str(summary.get('imageUrl') or summary.get('image_url') or '').strip()

    aspects = _ebay_catalog_aspects_to_dict(
        summary.get('aspects')
        or summary.get('localizedAspects')
        or summary.get('localized_aspects')
    )
    brand = (
        str(summary.get('brand') or '').strip()
        or str(summary.get('manufacturer') or '').strip()
        or aspects.get('Brand')
        or aspects.get('brand')
        or ''
    )

    category_id = str(summary.get('categoryId') or summary.get('category_id') or '').strip()
    category_name = str(summary.get('categoryName') or summary.get('category_name') or '').strip()
    category_path = str(summary.get('categoryPath') or summary.get('category_path') or '').strip()

    categories = summary.get('categories') or []
    if not category_id and isinstance(categories, list):
        for cat in categories:
            if not isinstance(cat, dict):
                continue
            category_id = str(cat.get('categoryId') or cat.get('category_id') or '').strip()
            category_name = str(cat.get('categoryName') or cat.get('category_name') or '').strip()
            category_path = str(cat.get('categoryPath') or cat.get('category_path') or '').strip()
            if category_id or category_name or category_path:
                break

    if not category_path and category_name:
        category_path = category_name

    gtins = []
    for key in ('gtins', 'gtin'):
        raw = summary.get(key)
        if isinstance(raw, (list, tuple)):
            gtins.extend(str(x or '').strip() for x in raw)
        elif raw:
            gtins.append(str(raw).strip())
    gtins = [g for g in gtins if g]

    return {
        'epid': epid,
        'title': title,
        'image': image,
        'brand': brand,
        'aspects': aspects,
        'categoryId': category_id,
        'categoryName': category_name,
        'categoryPath': category_path,
        'gtins': gtins,
        'price_min': None,
        'price_max': None,
        'price_count': 0,
        'source': 'catalog',
    }


def _listingagent_ebay_candidate_key(candidate):
    if not isinstance(candidate, dict):
        return ''
    epid = str(candidate.get('epid') or '').strip().lower()
    if epid:
        return f"epid:{epid}"
    gtins = [str(x or '').strip().lower() for x in (candidate.get('gtins') or []) if str(x or '').strip()]
    if gtins:
        return f"gtin:{'|'.join(gtins)}"
    title = str(candidate.get('title') or '').strip().lower()
    source = str(candidate.get('source') or '').strip().lower()
    if title:
        return f"{source}:{title}"
    return ''


def _listingagent_merge_ebay_candidates(existing, incoming, *, limit=10):
    results = list(existing or [])
    seen = {_listingagent_ebay_candidate_key(item) for item in results if _listingagent_ebay_candidate_key(item)}
    for item in incoming or []:
        key = _listingagent_ebay_candidate_key(item)
        if not key or key in seen:
            continue
        seen.add(key)
        results.append(item)
        if len(results) >= limit:
            break
    return results


def _listingagent_browse_items_to_candidates(items, *, limit=10):
    grouped = {}
    junk_values = {
        'unbranded', 'does not apply', 'n/a', 'na', 'unknown',
        'not applicable', 'see description', 'other', 'generic'
    }

    for item in items or []:
        if not isinstance(item, dict):
            continue
        title = str(item.get('title') or '').strip()
        if not title:
            continue
        epid = str(item.get('epid') or '').strip()
        # Keep each seller listing separate: an ePID may cover different variants.
        key = str(item.get('itemId') or item.get('item_id') or epid or title).strip().lower()
        if not key:
            continue
        group = grouped.get(key)
        if group is None:
            image_obj = item.get('image') or {}
            image = str((image_obj.get('imageUrl') or image_obj.get('image_url') or '')).strip() if isinstance(image_obj, dict) else ''
            category_fields = _listingagent_ebay_browse_category_fields(item)
            group = {
                'itemId': str(item.get('itemId') or item.get('item_id') or '').strip(),
                'legacyItemId': str(item.get('legacyItemId') or item.get('legacy_item_id') or '').strip(),
                'itemWebUrl': str(item.get('itemWebUrl') or item.get('item_web_url') or '').strip(),
                'epid': epid,
                'title': title,
                'image': image,
                'brand': '',
                'aspects': {},
                'categoryId': category_fields.get('categoryId') or '',
                'categoryName': category_fields.get('categoryName') or '',
                'categoryPath': category_fields.get('categoryPath') or '',
                'gtins': [],
                'price_min': None,
                'price_max': None,
                'price_count': 0,
                'source': 'browse',
                '_prices': [],
                '_aspect_votes': {},
            }
            grouped[key] = group

        if not group.get('image'):
            image_obj = item.get('image') or {}
            if isinstance(image_obj, dict):
                group['image'] = str(image_obj.get('imageUrl') or image_obj.get('image_url') or '').strip()

        if not group.get('itemId'):
            group['itemId'] = str(item.get('itemId') or item.get('item_id') or '').strip()
        if not group.get('legacyItemId'):
            group['legacyItemId'] = str(item.get('legacyItemId') or item.get('legacy_item_id') or '').strip()
        if not group.get('itemWebUrl'):
            group['itemWebUrl'] = str(item.get('itemWebUrl') or item.get('item_web_url') or '').strip()

        category_fields = _listingagent_ebay_browse_category_fields(item)
        if not group.get('categoryId') and category_fields.get('categoryId'):
            group['categoryId'] = category_fields.get('categoryId') or ''
        if not group.get('categoryName') and category_fields.get('categoryName'):
            group['categoryName'] = category_fields.get('categoryName') or ''
        if not group.get('categoryPath') and category_fields.get('categoryPath'):
            group['categoryPath'] = category_fields.get('categoryPath') or ''

        try:
            price_val = float((item.get('price') or {}).get('value') or 0)
            if price_val > 0:
                group['_prices'].append(price_val)
        except Exception:
            pass

        for name, value in _listingagent_ebay_browse_aspects_to_dict(item).items():
            if not name or not value or value.lower() in junk_values:
                continue
            norm = name.lower()
            votes = group['_aspect_votes'].setdefault(norm, {'_display': name})
            votes[value] = votes.get(value, 0) + 1

    results = []
    for group in grouped.values():
        aspects = {}
        for norm, votes in group.pop('_aspect_votes', {}).items():
            display = votes.pop('_display', norm)
            if votes:
                aspects[display] = max(votes, key=lambda v: votes[v])
        group['aspects'] = aspects
        group['brand'] = group.get('brand') or aspects.get('Brand') or aspects.get('brand') or ''
        prices = sorted(group.pop('_prices', []))
        if prices:
            group['price_min'] = prices[0]
            group['price_max'] = prices[-1]
            group['price_count'] = len(prices)
        results.append(group)

    results.sort(key=lambda r: (
        0 if r.get('epid') else 1,
        -(r.get('price_count') or 0),
        -(len(r.get('aspects') or {})),
        str(r.get('title') or '').lower()
    ))
    return results[:limit]


def _listingagent_search_ebay_browse_candidates(*, marketplace_id, queries, limit=12):
    results = []
    seen_queries = set()
    for raw_query in queries or []:
        query = str(raw_query or '').strip()
        key = query.lower()
        if not query or key in seen_queries:
            continue
        seen_queries.add(key)
        resp = _ebay_buy_api_request(
            'GET',
            '/buy/browse/v1/item_summary/search',
            params={'q': query, 'limit': min(max(limit * 3, 18), 60), 'fieldgroups': 'EXTENDED'},
            marketplace_id=marketplace_id,
        )
        if resp.status_code >= 400:
            continue
        payload = resp.json() if resp.text else {}
        items = payload.get('itemSummaries') or []
        if not items:
            continue
        browse_candidates = _listingagent_browse_items_to_candidates(items, limit=limit)
        results = _listingagent_merge_ebay_candidates(results, browse_candidates, limit=limit)
        if len(results) >= limit:
            break
    return results


def _listingagent_ebay_title_tokens(text):
    return [tok for tok in re.findall(r'[a-z0-9]+', str(text or '').lower()) if tok]


def _listingagent_detect_color_tokens(text):
    token_set = set(_listingagent_ebay_title_tokens(text))
    known_colors = {
        'black', 'white', 'red', 'blue', 'green', 'yellow', 'orange', 'pink', 'purple',
        'brown', 'beige', 'tan', 'gray', 'grey', 'gold', 'silver', 'ivory', 'navy',
        'teal', 'maroon', 'burgundy'
    }
    return token_set & known_colors


def _listingagent_score_ebay_candidate(candidate, *, upc='', preferred_title=''):
    score = 0.0
    safe_upc = str(upc or '').strip()
    gtins = [str(x or '').strip() for x in (candidate.get('gtins') or []) if str(x or '').strip()]
    if safe_upc and safe_upc in gtins:
        score += 150.0

    title = str(candidate.get('title') or '').strip().lower()
    preferred = str(preferred_title or '').strip().lower()
    title_tokens = set(_listingagent_ebay_title_tokens(title))
    preferred_tokens = set(_listingagent_ebay_title_tokens(preferred))
    overlap = title_tokens & preferred_tokens
    score += len(overlap) * 6.0

    preferred_numbers = {tok for tok in preferred_tokens if tok.isdigit()}
    candidate_numbers = {tok for tok in title_tokens if tok.isdigit()}
    if preferred_numbers and candidate_numbers:
        score += len(preferred_numbers & candidate_numbers) * 10.0

    preferred_colors = _listingagent_detect_color_tokens(preferred)
    candidate_colors = _listingagent_detect_color_tokens(title)
    if preferred_colors and candidate_colors:
        if preferred_colors & candidate_colors:
            score += 12.0
        else:
            score -= 8.0

    category_path = str(candidate.get('categoryPath') or '').lower()
    if 'tablecloth' in category_path and ('tablecloth' in preferred or 'oblong' in preferred):
        score += 6.0

    # Extra attributes must not outrank a closer product/model match.
    score += min(float(len(candidate.get('aspects') or {})), 5.0)
    preferred_models = {t for t in preferred_tokens if any(c.isdigit() for c in t) and any(c.isalpha() for c in t)}
    candidate_models = {t for t in title_tokens if any(c.isdigit() for c in t) and any(c.isalpha() for c in t)}
    if preferred_models and candidate_models and not preferred_models & candidate_models:
        score -= 35.0
    score += min(float(candidate.get('price_count') or 0), 5.0)
    return score


def _listingagent_pick_best_ebay_candidate(candidates, *, upc='', preferred_title=''):
    ranked = []
    for idx, candidate in enumerate(candidates or []):
        ranked.append((
            _listingagent_score_ebay_candidate(candidate, upc=upc, preferred_title=preferred_title),
            -idx,
            candidate,
        ))
    if not ranked:
        return None
    ranked.sort(reverse=True)
    return ranked[0][2]


def _listingagent_get_ebay_browse_item_detail(item_id, marketplace_id):
    item_id = str(item_id or '').strip()
    if not item_id:
        return None
    from urllib.parse import quote

    resp = _listingagent_ebay_user_request(
        'GET',
        f"/buy/browse/v1/item/{quote(item_id, safe='')}",
        marketplace_id=marketplace_id,
    )
    if resp.status_code >= 400:
        raise ss_errors._ListingAgentUserError(ss_ebay_policies._ebay_extract_error(resp), status_code=resp.status_code)

    detail = resp.json() if resp.text else {}
    if not isinstance(detail, dict) or not detail:
        return None

    category_fields = _listingagent_ebay_browse_category_fields(detail)
    aspects = _listingagent_ebay_browse_aspects_to_dict(detail)
    image_obj = detail.get('image') or {}
    image = ''
    if isinstance(image_obj, dict):
        image = str(image_obj.get('imageUrl') or image_obj.get('image_url') or '').strip()

    gtins = []
    for key in ('gtin', 'upc', 'ean', 'isbn'):
        value = detail.get(key)
        if isinstance(value, (list, tuple)):
            gtins.extend(str(v or '').strip() for v in value if str(v or '').strip())
        else:
            text = str(value or '').strip()
            if text:
                gtins.append(text)

    price_min = price_max = None
    price_count = 0
    try:
        price_val = float((detail.get('price') or {}).get('value') or 0)
        if price_val > 0:
            price_min = price_max = price_val
            price_count = 1
    except Exception:
        pass

    return {
        'itemId': str(detail.get('itemId') or '').strip(),
        'legacyItemId': str(detail.get('legacyItemId') or detail.get('legacy_item_id') or '').strip(),
        'itemWebUrl': str(detail.get('itemWebUrl') or detail.get('item_web_url') or '').strip(),
        'epid': str(detail.get('epid') or detail.get('ePID') or '').strip(),
        'title': str(detail.get('title') or '').strip(),
        'image': image,
        'brand': str(detail.get('brand') or aspects.get('Brand') or aspects.get('brand') or '').strip(),
        'aspects': aspects,
        'categoryId': category_fields.get('categoryId') or '',
        'categoryName': category_fields.get('categoryName') or '',
        'categoryPath': category_fields.get('categoryPath') or '',
        'gtins': [g for g in gtins if g],
        'price_min': price_min,
        'price_max': price_max,
        'price_count': price_count,
        'source': 'browse_detail',
    }


def api_listingagent_ebay_catalog_search():
    """Find similar live listings, with product-catalog fallback."""
    try:
        upc = (request.args.get('upc') or request.args.get('gtin') or '').strip()
        q = (request.args.get('q') or request.args.get('query') or '').strip()
        # Product lookup uses the base GTIN; draft/task/SKU identity keeps its suffix.
        upc = ebay_mapping.recommendation_gtin(upc)
        if not upc and ebay_mapping.recommendation_gtin(q):
            upc, q = ebay_mapping.recommendation_gtin(q), ''
        title = (request.args.get('title') or '').strip()
        limit = ss_listing_settings._listingagent_parse_int(request.args.get('limit'), 10) or 10
        limit = max(1, min(limit, 20))

        if not upc and not q and not title:
            raise ss_errors._ListingAgentUserError('upc or q is required', status_code=400)

        settings = ss_listing_settings._listingagent_get_settings()
        marketplace_id = (request.args.get('marketplaceId') or settings.get('ebay_marketplace_id') or 'EBAY_US').strip()

        # Similar live listings come first. Catalog results must never consume
        # the result limit before Browse has a chance to find seller listings.
        results = []
        warnings = []
        browse_searches = []
        if q:
            browse_searches.append({'q': q})
        elif upc:
            browse_searches.append({'gtin': upc})
        if title and title.casefold() != q.casefold():
            browse_searches.append({'q': title})
        if upc and not q:
            browse_searches.append({'q': upc})

        for search_params in browse_searches:
            if len(results) >= limit:
                break
            try:
                resp = _ebay_buy_api_request(
                    'GET', '/buy/browse/v1/item_summary/search',
                    params={**search_params, 'limit': min(limit * 3, 60), 'fieldgroups': 'EXTENDED'},
                    marketplace_id=marketplace_id,
                )
                if resp.status_code >= 400:
                    warnings.append('A live-listing search was unavailable.')
                    continue
                data = resp.json() if resp.text else {}
                candidates = _listingagent_browse_items_to_candidates(data.get('itemSummaries') or [], limit=60)
                for candidate in candidates:
                    if 'gtin' in search_params:
                        candidate['matchReason'] = 'Barcode search match'
                        candidate['gtins'] = list(set(candidate.get('gtins', []) + [upc]))
                    else:
                        candidate['matchReason'] = 'Similar title'
                results = _listingagent_merge_ebay_candidates(results, candidates, limit=100)
            except Exception:
                warnings.append('A live-listing search was unavailable.')

        # Fill remaining choices from the product catalog if Browse is sparse.
        if len(results) < limit:
            catalog_params = {'q': q or title} if (q or title) else {'gtin': upc}
            try:
                resp = _ebay_buy_api_request(
                    'GET', '/commerce/catalog/v1_beta/product_summary/search',
                    params={**catalog_params, 'fieldgroups': 'PRODUCT', 'limit': limit},
                    marketplace_id=marketplace_id,
                    scope='https://api.ebay.com/oauth/api_scope/commerce.catalog.readonly',
                )
                if resp.status_code < 400:
                    data = resp.json() if resp.text else {}
                    candidates = []
                    for summary in data.get('productSummaries') or data.get('products') or []:
                        candidate = _listingagent_normalize_ebay_catalog_summary(summary)
                        if candidate:
                            candidate['matchReason'] = 'Product catalog'
                            candidates.append(candidate)
                    results = _listingagent_merge_ebay_candidates(results, candidates, limit=100)
                else:
                    warnings.append('Product catalog search was unavailable.')
            except Exception:
                warnings.append('Product catalog search was unavailable.')

        results.sort(key=lambda candidate: _listingagent_score_ebay_candidate(
            candidate, upc=upc, preferred_title=q or title), reverse=True)
        results = results[:limit]
        sources = {r.get('source') for r in results}
        source = 'mixed' if len(sources) > 1 else next(iter(sources), 'browse')
        return jsonify({'success': True, 'results': results, 'total': len(results),
                        'source': source, 'warnings': sorted(set(warnings))})
    except ss_errors._ListingAgentUserError as e:
        payload = {'success': False, 'error': str(e)}
        payload.update(e.extra or {})
        return jsonify(payload), e.status_code
    except Exception as e:
        return jsonify({'success': False, 'error': ss_errors._safe_error(e, 'listingagent:ebay_catalog_search')}), 500


def api_listingagent_ebay_product_specifics():
    """Best-effort eBay specifics using live Browse candidates plus item detail."""
    try:
        upc = (request.args.get('upc') or request.args.get('gtin') or '').strip()
        title = (request.args.get('title') or request.args.get('q') or '').strip()
        if not upc and not title:
            return jsonify({'success': True, 'found': False, 'aspects': {}})

        marketplace_id = (request.args.get('marketplaceId') or
                          ss_listing_settings._listingagent_get_settings().get('ebay_marketplace_id') or 'EBAY_US').strip()

        queries = []
        if title:
            queries.append(title)
        if upc:
            queries.append(upc)
        candidates = _listingagent_search_ebay_browse_candidates(
            marketplace_id=marketplace_id,
            queries=queries,
            limit=8,
        )
        best = _listingagent_pick_best_ebay_candidate(candidates, upc=upc, preferred_title=title or upc)
        if not best:
            return jsonify({'success': True, 'found': False, 'aspects': {}})

        detail = None
        try:
            detail = _listingagent_get_ebay_browse_item_detail(best.get('itemId') or '', marketplace_id)
        except Exception:
            detail = None
        first = detail or best or {}
        aspects = {}
        for name, val in (first.get('aspects') or {}).items():
            value = str(val or '').strip()
            if value and value.lower() not in {'unbranded', 'does not apply', 'n/a', 'na', 'unknown'}:
                aspects[str(name)] = value

        return jsonify({
            'success': True,
            'found': bool(aspects or first.get('itemId') or first.get('epid') or first.get('title')),
            'itemId': str(first.get('itemId') or ''),
            'legacyItemId': str(first.get('legacyItemId') or ''),
            'epid': str(first.get('epid') or ''),
            'title': str(first.get('title') or ''),
            'image': str(first.get('image') or ''),
            'brand': str(first.get('brand') or ''),
            'categoryId': str(first.get('categoryId') or ''),
            'categoryName': str(first.get('categoryName') or ''),
            'categoryPath': str(first.get('categoryPath') or ''),
            'aspects': aspects,
        })
    except Exception as e:
        return jsonify({'success': True, 'found': False, 'aspects': {},
                        'note': ss_errors._safe_error(e, 'listingagent:ebay_product_specifics')})


def api_listingagent_ebay_catalog_item():
    """Fetch full Browse item detail for a selected eBay candidate row."""
    try:
        item_id = (request.args.get('itemId') or request.args.get('item_id') or '').strip()
        if not item_id:
            raise ss_errors._ListingAgentUserError('itemId is required', status_code=400)

        marketplace_id = (request.args.get('marketplaceId') or
                          ss_listing_settings._listingagent_get_settings().get('ebay_marketplace_id') or 'EBAY_US').strip()
        detail = _listingagent_get_ebay_browse_item_detail(item_id, marketplace_id)
        if not detail:
            return jsonify({'success': False, 'error': 'No detail returned for selected eBay item.'}), 404
        return jsonify({'success': True, 'result': detail})
    except ss_errors._ListingAgentUserError as e:
        payload = {'success': False, 'error': str(e)}
        payload.update(e.extra or {})
        return jsonify(payload), e.status_code
    except Exception as e:
        return jsonify({'success': False, 'error': ss_errors._safe_error(e, 'listingagent:ebay_catalog_item')}), 500
