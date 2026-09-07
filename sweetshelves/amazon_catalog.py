"""Amazon catalog for Sweet Shelves."""

import json
import requests
from flask import jsonify, request
from . import amazon_listing as ss_amazon_listing, config as ss_config, database as ss_database, errors as ss_errors, listing_settings as ss_listing_settings


def _amazon_marketplace_from_id(marketplace_id: str):
    try:
        from sp_api.base import Marketplaces
        if not marketplace_id:
            return Marketplaces.US
        for name in dir(Marketplaces):
            if not name.isupper():
                continue
            mp = getattr(Marketplaces, name)
            if getattr(mp, 'marketplace_id', None) == marketplace_id:
                return mp
        return Marketplaces.US
    except Exception:
        # If SP-API isn't installed or something is misconfigured, default to US.
        return None


def _amazon_spapi_context():
    """Load SP-API credentials + seller/marketplace from amazon_credentials.json."""
    creds_path = ss_config.BASE_DIR / 'amazon_credentials.json'
    if not creds_path.exists():
        raise ss_errors._ListingAgentUserError('amazon_credentials.json not found', status_code=503)

    with open(creds_path, 'r', encoding='utf-8') as f:
        c = json.load(f)

    seller_id = (c.get('seller_id') or '').strip()
    marketplace_id = (c.get('marketplace_id') or '').strip() or 'ATVPDKIKX0DER'
    if not seller_id:
        raise ss_errors._ListingAgentUserError('Amazon seller_id missing in amazon_credentials.json', status_code=503)

    credentials = {
        'refresh_token': c.get('refresh_token'),
        'lwa_app_id': c.get('lwa_app_id'),
        'lwa_client_secret': c.get('lwa_client_secret', ''),
    }
    if c.get('aws_access_key') and c.get('aws_secret_key'):
        credentials['aws_access_key'] = c.get('aws_access_key')
        credentials['aws_secret_key'] = c.get('aws_secret_key')
    if c.get('role_arn'):
        credentials['role_arn'] = c.get('role_arn')

    marketplace = _amazon_marketplace_from_id(marketplace_id)
    return credentials, seller_id, marketplace_id, marketplace


def _amazon_get_listing_product_type(li, seller_id, sku, marketplace_id, cache=None):
    cache = cache if cache is not None else {}
    if sku in cache:
        return cache.get(sku)
    try:
        resp = li.get_listings_item(
            seller_id,
            sku,
            marketplaceIds=[marketplace_id],
            includedData=['summaries']
        )
        if getattr(resp, 'errors', None):
            raise Exception(str(resp.errors))
        payload = resp.payload or {}
        summaries = payload.get('summaries') or []
        pt = None
        for s in summaries:
            pt = s.get('productType')
            if pt:
                break
        if not pt:
            pt = payload.get('productType')
        if pt:
            cache[sku] = pt
        return pt
    except Exception:
        return None


def _amazon_get_catalog_product_type(credentials, marketplace, marketplace_id, asin):
    if not asin:
        return None
    try:
        from sp_api.api import CatalogItems
        ci = CatalogItems(credentials=credentials, marketplace=marketplace, version='2022-04-01')
        resp = ci.get_catalog_item(
            asin,
            marketplaceIds=[marketplace_id],
            includedData=['productTypes', 'summaries']
        )
        if getattr(resp, 'errors', None):
            return None
        payload = resp.payload or {}
        pts = payload.get('productTypes') or []
        for pt in pts:
            if isinstance(pt, str) and pt.strip():
                return pt.strip()
            if isinstance(pt, dict):
                v = (pt.get('productType') or pt.get('product_type') or pt.get('name') or '').strip()
                if v:
                    return v

        summaries = payload.get('summaries') or []
        for s in summaries:
            if not isinstance(s, dict):
                continue
            v = (s.get('productType') or s.get('product_type') or '').strip()
            if v:
                return v

        # Some responses may nest product types elsewhere
        pt = payload.get('productType')
        if isinstance(pt, str) and pt.strip():
            return pt.strip()
        if isinstance(pt, dict):
            v = (pt.get('productType') or pt.get('product_type') or '').strip()
            if v:
                return v
        return None
    except Exception:
        return None


def _amazon_resolve_asin_from_upc(credentials, marketplace, marketplace_id, upc):
    """Best-effort Catalog lookup: resolve first ASIN for a UPC/EAN/GTIN."""
    upc = (upc or '').strip()
    if not upc:
        return None
    try:
        from sp_api.api import CatalogItems
        ci = CatalogItems(credentials=credentials, marketplace=marketplace, version='2022-04-01')
        id_type = 'UPC'
        if upc.isdigit():
            if len(upc) == 13:
                id_type = 'EAN'
            elif len(upc) == 14:
                id_type = 'GTIN'
        resp = ci.search_catalog_items(
            identifiers=[upc],
            identifiersType=id_type,
            marketplaceIds=[marketplace_id],
            includedData=['summaries'],
            pageSize=8
        )
        if getattr(resp, 'errors', None):
            return None
        payload = resp.payload or {}
        items = payload.get('items') or []
        if not isinstance(items, list):
            return None
        for it in items:
            if not isinstance(it, dict):
                continue
            asin = (it.get('asin') or '').strip()
            if asin:
                return asin
        return None
    except Exception:
        return None


def _amazon_find_local_sku_by_asin(asin):
    asin = (asin or '').strip()
    if not asin:
        return None
    try:
        with ss_database.db_connection('amazonStore.db') as conn:
            cur = conn.cursor()
            cur.execute('''
                SELECT TRIM(COALESCE(SKU, '')) AS sku
                FROM ITEMS
                WHERE TRIM(COALESCE(ASIN, '')) = ? COLLATE NOCASE
                ORDER BY COALESCE(LAST_UPDATED, '') DESC, ID DESC
                LIMIT 1
            ''', (asin,))
            row = cur.fetchone()
            if not row:
                return None
            sku = (row['sku'] or '').strip()
            return sku or None
    except Exception:
        return None


def _amazon_build_price_feed_xml(*, seller_id, jobs, currency='USD'):
    # Legacy XML feed for price updates (does not require product type).
    from xml.sax.saxutils import escape as _xesc
    lines = [
        '<?xml version="1.0" encoding="utf-8"?>',
        '<AmazonEnvelope xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xsi:noNamespaceSchemaLocation="amzn-envelope.xsd">',
        '  <Header>',
        '    <DocumentVersion>1.01</DocumentVersion>',
        f'    <MerchantIdentifier>{_xesc(str(seller_id))}</MerchantIdentifier>',
        '  </Header>',
        '  <MessageType>Price</MessageType>',
    ]
    msg_id = 1
    for j in jobs:
        sku = j.get('sku')
        price = j.get('new_price')
        if not sku or price is None:
            continue
        lines.extend([
            '  <Message>',
            f'    <MessageID>{msg_id}</MessageID>',
            '    <Price>',
            f'      <SKU>{_xesc(str(sku))}</SKU>',
            f'      <StandardPrice currency="{_xesc(str(currency).upper())}">{float(price):.2f}</StandardPrice>',
            '    </Price>',
            '  </Message>',
        ])
        msg_id += 1
    lines.append('</AmazonEnvelope>')
    return '\n'.join(lines).encode('utf-8')


def _amazon_submit_price_feed_json(*, credentials, marketplace, marketplace_id, seller_id, jobs, currency='USD', offer_audience='ALL'):
    from sp_api.api import Feeds
    feed = {
        'header': {
            'sellerId': seller_id,
            'version': '2.0',
            'issueLocale': 'en_US'
        },
        'messages': []
    }
    msg_id = 1
    for j in jobs:
        sku = j.get('sku')
        product_type = j.get('product_type')
        price = j.get('new_price')
        if not sku or not product_type:
            continue
        attrs = ss_amazon_listing._amazon_offer_price_attrs(currency, price, marketplace_id, offer_audience=offer_audience)
        feed['messages'].append({
            'messageId': msg_id,
            'sku': sku,
            'operationType': 'PATCH',
            'productType': product_type,
            'patches': [{
                'op': 'replace',
                'path': '/attributes/purchasable_offer',
                'value': attrs.get('purchasable_offer')
            }]
        })
        msg_id += 1

    if not feed['messages']:
        raise Exception('No feed messages to submit')

    feeds = Feeds(credentials=credentials, marketplace=marketplace)
    content_type = 'application/json; charset=UTF-8'
    body = json.dumps(feed, ensure_ascii=True).encode('utf-8')
    try:
        doc_id, url, uploaded = ss_amazon_listing._amazon_create_feed_document(feeds, content_type, body)
    except Exception as e:
        raise Exception(ss_amazon_listing._amazon_feeds_error(e, 'create_feed_document'))
    if not doc_id:
        raise Exception('Failed to create feed document')
    if not uploaded:
        if not url:
            raise Exception('Feed document upload URL missing')
        up = requests.put(url, data=body, headers={'Content-Type': content_type}, timeout=60)
        if up.status_code >= 400:
            raise Exception(f'Feed upload failed ({up.status_code})')

    try:
        feed_resp = ss_amazon_listing._amazon_create_feed(
            feeds,
            feed_type='JSON_LISTINGS_FEED',
            marketplace_ids=[marketplace_id],
            input_feed_document_id=doc_id
        )
        feed_payload = getattr(feed_resp, 'payload', None) or {}
    except Exception as e:
        raise Exception(ss_amazon_listing._amazon_feeds_error(e, 'create_feed'))
    feed_id = feed_payload.get('feedId') or feed_payload.get('feed_id')
    if not feed_id:
        raise Exception('Feed submission failed')
    return feed_id


def _amazon_submit_price_feed_xml(*, credentials, marketplace, marketplace_id, seller_id, jobs, currency='USD'):
    from sp_api.api import Feeds
    feeds = Feeds(credentials=credentials, marketplace=marketplace)
    content_type = 'text/xml; charset=UTF-8'
    body = _amazon_build_price_feed_xml(seller_id=seller_id, jobs=jobs, currency=currency)
    try:
        doc_id, url, uploaded = ss_amazon_listing._amazon_create_feed_document(feeds, content_type, body)
    except Exception as e:
        raise Exception(ss_amazon_listing._amazon_feeds_error(e, 'create_feed_document'))
    if not doc_id:
        raise Exception('Failed to create feed document')
    if not uploaded:
        if not url:
            raise Exception('Feed document upload URL missing')
        up = requests.put(url, data=body, headers={'Content-Type': content_type}, timeout=60)
        if up.status_code >= 400:
            raise Exception(f'Feed upload failed ({up.status_code})')

    try:
        feed_resp = ss_amazon_listing._amazon_create_feed(
            feeds,
            feed_type='POST_PRODUCT_PRICING_DATA',
            marketplace_ids=[marketplace_id],
            input_feed_document_id=doc_id
        )
        feed_payload = getattr(feed_resp, 'payload', None) or {}
    except Exception as e:
        raise Exception(ss_amazon_listing._amazon_feeds_error(e, 'create_feed'))
    feed_id = feed_payload.get('feedId') or feed_payload.get('feed_id')
    if not feed_id:
        raise Exception('Feed submission failed')
    return feed_id


def _amazon_submit_price_feed(*, credentials, marketplace, marketplace_id, seller_id, jobs, currency='USD', offer_audience='ALL'):
    try:
        return _amazon_submit_price_feed_json(
            credentials=credentials,
            marketplace=marketplace,
            marketplace_id=marketplace_id,
            seller_id=seller_id,
            jobs=jobs,
            currency=currency,
            offer_audience=offer_audience
        )
    except Exception as e:
        json_err = ss_amazon_listing._amazon_format_spapi_error(e)
        try:
            return _amazon_submit_price_feed_xml(
                credentials=credentials,
                marketplace=marketplace,
                marketplace_id=marketplace_id,
                seller_id=seller_id,
                jobs=jobs,
                currency=currency
            )
        except Exception as e2:
            xml_err = ss_amazon_listing._amazon_format_spapi_error(e2)
            raise Exception(f"JSON feed failed: {json_err} | XML feed failed: {xml_err}")


def _amazon_parse_feed_report(body_bytes):
    """Parse Amazon feed processing report (JSON or XML). Returns dict with errors/warnings."""
    out = {
        'error_count': 0,
        'warning_count': 0,
        'errors': [],
        'warnings': [],
        'raw_excerpt': ''
    }
    if not body_bytes:
        return out
    try:
        raw = body_bytes.decode('utf-8', errors='replace')
    except Exception:
        try:
            raw = body_bytes.decode('latin-1', errors='replace')
        except Exception:
            raw = ''
    out['raw_excerpt'] = (raw[:1200] + '...') if len(raw) > 1200 else raw

    # Try JSON first
    try:
        import json as _json
        js = _json.loads(raw)
        issues = []
        if isinstance(js, dict):
            issues = js.get('issues') or js.get('issuesWithAttributes') or js.get('messagesWithIssues') or []
        if isinstance(issues, dict):
            issues = [issues]
        for it in issues:
            sev = str(it.get('severity') or it.get('Severity') or '').upper()
            msg = it.get('message') or it.get('messageText') or it.get('description') or it.get('Message') or ''
            code = it.get('code') or it.get('Code') or ''
            if sev == 'ERROR':
                out['error_count'] += 1
                out['errors'].append({'code': code, 'message': msg})
            elif sev == 'WARNING':
                out['warning_count'] += 1
                out['warnings'].append({'code': code, 'message': msg})
        return out
    except Exception:
        pass

    # Fallback: XML
    try:
        import xml.etree.ElementTree as _ET
        root = _ET.fromstring(raw)
        for result in root.findall('.//Result'):
            code = (result.findtext('ResultCode') or '').strip().lower()
            msg = (result.findtext('ResultMessage') or '').strip()
            if code == 'error':
                out['error_count'] += 1
                out['errors'].append({'code': 'Error', 'message': msg})
            elif code == 'warning':
                out['warning_count'] += 1
                out['warnings'].append({'code': 'Warning', 'message': msg})
        return out
    except Exception:
        return out


def _amazon_get_feed_document_info(feeds, feed_document_id):
    import inspect as _inspect
    try:
        params = _inspect.signature(feeds.get_feed_document).parameters
    except Exception:
        params = {}
    if not params or 'feedDocumentId' in params or any(p.kind == _inspect.Parameter.VAR_KEYWORD for p in params.values()):
        try:
            return feeds.get_feed_document(feedDocumentId=feed_document_id)
        except TypeError:
            pass
    return feeds.get_feed_document(feed_document_id)


def _amazon_get_feed_status(credentials, marketplace, feed_id):
    from sp_api.api import Feeds
    feeds = Feeds(credentials=credentials, marketplace=marketplace)
    try:
        resp = feeds.get_feed(feedId=feed_id)
    except TypeError:
        resp = feeds.get_feed(feed_id)
    payload = getattr(resp, 'payload', None) or {}
    status = payload.get('processingStatus') or payload.get('processing_status') or payload.get('status')
    result_doc = payload.get('resultFeedDocumentId') or payload.get('result_feed_document_id')
    return {
        'feed_id': feed_id,
        'status': status,
        'result_feed_document_id': result_doc,
        'raw': payload
    }


def _amazon_download_feed_document(url):
    import gzip as _gzip
    if not url:
        return None
    resp = requests.get(url, timeout=30)
    if resp.status_code != 200:
        return None
    data = resp.content or b''
    # Decompress if gzipped
    try:
        if data[:2] == b'\x1f\x8b':
            data = _gzip.decompress(data)
    except Exception:
        pass
    return data


def api_listingagent_amazon_account_check():
    """Diagnostic endpoint: validate Amazon account linkage and seller-id usability."""
    try:
        credentials, seller_id, marketplace_id, marketplace = _amazon_spapi_context()
        if marketplace is None:
            return jsonify({'success': False, 'error': 'Amazon SP-API not available'}), 503

        mp_override = (request.args.get('marketplaceId') or '').strip()
        mp_id = mp_override or marketplace_id
        probe_sku = (request.args.get('sku') or '').strip()

        def _mask(v):
            s = (v or '').strip()
            if len(s) <= 8:
                return s
            return f"{s[:4]}...{s[-4:]}"

        out = {
            'success': True,
            'seller_id_hint': _mask(seller_id),
            'marketplace_id': mp_id,
            'sellers_api': {},
            'listings_probe': {},
        }

        # 1) Sellers API probe (does not require sellerId argument in most versions).
        try:
            from sp_api.api import Sellers
            sellers = Sellers(credentials=credentials, marketplace=marketplace)
            resp = sellers.get_marketplace_participations()
            if getattr(resp, 'errors', None):
                out['sellers_api']['errors'] = resp.errors
            else:
                payload = getattr(resp, 'payload', None) or {}
                mps = []
                parts = payload.get('marketplaceParticipations') or payload.get('marketplace_participations') or []
                if isinstance(parts, list):
                    for p in parts:
                        if not isinstance(p, dict):
                            continue
                        m = p.get('marketplace') or {}
                        pid = (m.get('id') or m.get('marketplaceId') or '').strip()
                        name = (m.get('name') or '').strip()
                        is_part = bool((p.get('participation') or {}).get('isParticipating'))
                        if pid:
                            mps.append({'id': pid, 'name': name, 'participating': is_part})
                out['sellers_api']['marketplaces'] = mps[:32]
                out['sellers_api']['marketplace_enabled'] = any((x.get('id') == mp_id and x.get('participating')) for x in mps)
        except Exception as e:
            out['sellers_api']['error'] = ss_amazon_listing._amazon_format_spapi_error(e)

        # 2) Listings probe using seller_id (this is what put_offer depends on).
        try:
            from sp_api.api import ListingsItems
            li = ListingsItems(credentials=credentials, marketplace=marketplace)

            if not probe_sku:
                try:
                    with ss_database.db_connection('amazonStore.db') as conn:
                        cur = conn.cursor()
                        cur.execute('''
                            SELECT TRIM(COALESCE(SKU,'')) AS sku
                            FROM ITEMS
                            WHERE TRIM(COALESCE(SKU,'')) != ''
                            ORDER BY COALESCE(LAST_UPDATED,'') DESC, ID DESC
                            LIMIT 1
                        ''')
                        r = cur.fetchone()
                        probe_sku = ((r['sku'] if r else '') or '').strip()
                except Exception:
                    probe_sku = ''

            out['listings_probe']['sku'] = probe_sku
            if probe_sku:
                resp = li.get_listings_item(
                    seller_id,
                    probe_sku,
                    marketplaceIds=[mp_id],
                    includedData=['summaries']
                )
                if getattr(resp, 'errors', None):
                    out['listings_probe']['errors'] = resp.errors
                else:
                    out['listings_probe']['ok'] = True
            else:
                out['listings_probe']['note'] = 'No SKU available for probe'
        except Exception as e:
            out['listings_probe']['error'] = ss_amazon_listing._amazon_format_spapi_error(e)

        return jsonify(out)
    except Exception as e:
        return jsonify({'success': False, 'error': ss_errors._safe_error(e, 'listingagent:amazon_account_check')}), 500


def api_listingagent_amazon_restriction_check():
    """Lightweight pre-check for ASIN gating/restrictions (cached)."""
    try:
        asin = (request.args.get('asin') or '').strip().upper()
        if not asin:
            return jsonify({'success': False, 'error': 'asin is required'}), 400

        condition_type = (request.args.get('conditionType') or '').strip()
        mp_override = (request.args.get('marketplaceId') or '').strip()
        force_refresh = (request.args.get('refresh') or '').strip().lower() in ('1', 'true', 'yes', 'on')

        credentials, seller_id, marketplace_id, marketplace = _amazon_spapi_context()
        if marketplace is None:
            return jsonify({'success': False, 'error': 'Amazon SP-API not available'}), 503

        mp_id = mp_override or marketplace_id
        info = ss_amazon_listing._amazon_check_listing_restrictions_cached(
            credentials,
            marketplace,
            seller_id,
            mp_id,
            asin,
            condition_type=condition_type,
            force_refresh=force_refresh
        ) or {}

        warning = ''
        if not bool(info.get('checked')):
            hints = ss_amazon_listing._amazon_restriction_error_hints(info, mp_id)
            warning = ' '.join(hints).strip()

        return jsonify({
            'success': True,
            'asin': asin,
            'marketplaceId': mp_id,
            'conditionType': condition_type,
            'restriction': {
                'checked': bool(info.get('checked')),
                'restricted': bool(info.get('restricted')),
                'reasons': (info.get('reasons') or [])[:12]
            },
            'cached': bool(info.get('_cached')),
            'warning': warning
        })
    except Exception as e:
        return jsonify({'success': False, 'error': ss_errors._safe_error(e, 'listingagent:amazon_restriction_check')}), 500


def api_listingagent_amazon_catalog_search():
    """Search Amazon catalog by keyword/UPC (best-effort) to suggest ASINs."""
    try:
        q = (request.args.get('q') or request.args.get('upc') or '').strip()
        if not q:
            return jsonify({'success': False, 'error': 'q is required'}), 400

        limit = ss_listing_settings._listingagent_parse_int(request.args.get('limit'), 8) or 8
        limit = max(1, min(limit, 20))
        include_raw = (request.args.get('raw') or '').lower() == 'true'
        mode = (request.args.get('mode') or '').strip().lower()
        title_hint = (request.args.get('title') or '').strip()  # optional item title for supplemental keyword search
        identifiers_type = (request.args.get('identifiersType') or '').strip().upper()
        q_no_space = q.replace(' ', '')

        credentials, _seller_id, marketplace_id, marketplace = _amazon_spapi_context()
        if marketplace is None:
            return jsonify({'success': False, 'error': 'Amazon SP-API not available'}), 503

        from sp_api.api import CatalogItems
        from sp_api.base.exceptions import SellingApiException

        ci = CatalogItems(credentials=credentials, marketplace=marketplace, version='2022-04-01')

        # Prefer identifier search when q looks like a UPC/GTIN (or explicitly requested).
        use_identifiers = False
        if mode in ('upc', 'gtin', 'identifier'):
            use_identifiers = True
        elif identifiers_type:
            use_identifiers = True
        elif mode in ('asin',):
            use_identifiers = True
            if not identifiers_type:
                identifiers_type = 'ASIN'
        elif q_no_space.isalnum() and len(q_no_space) == 10 and any(ch.isdigit() for ch in q_no_space) and any(ch.isalpha() for ch in q_no_space):
            # Likely ASIN (avoid treating plain words as ASIN).
            use_identifiers = True
            if not identifiers_type:
                identifiers_type = 'ASIN'
        elif q.isdigit() and 8 <= len(q) <= 14:
            use_identifiers = True

        if use_identifiers:
            # Heuristic: UPC=8/10/11/12, EAN=13, GTIN=14. Allow override via identifiersType=...
            if not identifiers_type:
                if len(q) == 13:
                    identifiers_type = 'EAN'
                elif len(q) == 14:
                    identifiers_type = 'GTIN'
                elif len(q) == 10:
                    # Commonly ISBN-10 when numeric; if this is actually UPC/other, keyword fallback will still work.
                    identifiers_type = 'ISBN'
                else:
                    identifiers_type = 'UPC'

        def _do_keywords():
            return ci.search_catalog_items(
                keywords=[q],
                marketplaceIds=[marketplace_id],
                includedData=['summaries', 'images'],
                pageSize=limit
            )

        def _do_identifiers():
            return ci.search_catalog_items(
                identifiers=[q],
                identifiersType=identifiers_type,
                marketplaceIds=[marketplace_id],
                includedData=['summaries', 'images'],
                pageSize=limit
            )

        try:
            resp = _do_identifiers() if use_identifiers else _do_keywords()
        except SellingApiException:
            # Some accounts/marketplaces/product-types can be finicky; fall back to keyword search.
            if use_identifiers:
                resp = _do_keywords()
            else:
                raise
        if resp.errors:
            return jsonify({'success': False, 'error': str(resp.errors)}), 400

        payload = resp.payload or {}
        items = payload.get('items') or []
        # If identifier search returned nothing and the caller didn't force identifier mode, try keywords.
        if use_identifiers and not items and mode not in ('upc','gtin','identifier','asin') and not (request.args.get('identifiersType') or '').strip():
            try:
                resp_kw = _do_keywords()
                if not resp_kw.errors:
                    payload = resp_kw.payload or {}
                    items = payload.get('items') or []
            except Exception:
                pass

        # Supplement identifier results with a keyword search using the item title, to surface
        # more ASIN variants. Deduplicate by ASIN; identifier results take priority.
        if use_identifiers and title_hint and len(items) < limit:
            try:
                resp_title = ci.search_catalog_items(
                    keywords=[title_hint],
                    marketplaceIds=[marketplace_id],
                    includedData=['summaries', 'images'],
                    pageSize=limit
                )
                if not resp_title.errors:
                    existing_asins = {(it.get('asin') or '').strip().upper() for it in items}
                    for extra_it in (resp_title.payload or {}).get('items') or []:
                        asin_up = (extra_it.get('asin') or '').strip().upper()
                        if asin_up and asin_up not in existing_asins:
                            items.append(extra_it)
                            existing_asins.add(asin_up)
                            if len(items) >= limit:
                                break
            except Exception:
                pass

        # Pre-fetch prices from amazonStore.db for any matching ASINs
        asin_prices = {}
        try:
            raw_asins = [(it.get('asin') or '').strip() for it in items[:limit] if (it.get('asin') or '').strip()]
            if raw_asins:
                placeholders = ','.join('?' * len(raw_asins))
                with ss_database.db_connection('amazonStore.db') as aconn:
                    acur = aconn.cursor()
                    acur.execute(f'SELECT ASIN, PRICE FROM ITEMS WHERE ASIN IN ({placeholders})', raw_asins)
                    for row in acur.fetchall():
                        try:
                            asin_prices[str(row[0] or '').strip().upper()] = float(row[1]) if row[1] is not None else None
                        except Exception:
                            pass
        except Exception:
            pass

        results = []
        for it in items[:limit]:
            asin = (it.get('asin') or '').strip()
            title = ''
            brand = ''
            summaries = it.get('summaries') or []
            if summaries:
                s0 = summaries[0] or {}
                title = s0.get('itemName') or s0.get('item_name') or ''
                brand = s0.get('brandName') or s0.get('brand_name') or ''

            image_url = ''
            for grp in (it.get('images') or []):
                imgs = grp.get('images') or []
                if imgs:
                    image_url = (imgs[0].get('link') or '').strip()
                    if image_url:
                        break

            store_price = asin_prices.get(asin.upper())
            results.append({
                'asin': asin,
                'title': title or '',
                'brand': brand or '',
                'image': image_url or '',
                'store_price': store_price,
            })

        out = {'success': True, 'results': results}
        if include_raw:
            out['raw'] = payload
        return jsonify(out)

    except Exception as e:
        # SellingApiException string tends to be safe+useful (issues, codes, etc.)
        from sp_api.base.exceptions import SellingApiException
        if isinstance(e, SellingApiException):
            return jsonify({'success': False, 'error': str(e)}), 400
        return jsonify({'success': False, 'error': ss_errors._safe_error(e, 'listingagent:amazon_catalog_search')}), 500


def api_listingagent_amazon_catalog_item():
    """Fetch catalog details for an ASIN (public product page template data)."""
    try:
        asin = (request.args.get('asin') or '').strip()
        if not asin:
            return jsonify({'success': False, 'error': 'asin is required'}), 400

        included = (request.args.get('includedData') or 'summaries,images,attributes').strip()
        included_data = [x.strip() for x in included.split(',') if x.strip()]
        include_raw = (request.args.get('raw') or '').lower() == 'true'

        credentials, _seller_id, marketplace_id, marketplace = _amazon_spapi_context()
        if marketplace is None:
            return jsonify({'success': False, 'error': 'Amazon SP-API not available'}), 503

        from sp_api.api import CatalogItems
        from sp_api.base.exceptions import SellingApiException

        ci = CatalogItems(credentials=credentials, marketplace=marketplace, version='2022-04-01')
        resp = ci.get_catalog_item(
            asin,
            marketplaceIds=[marketplace_id],
            includedData=included_data
        )
        if resp.errors:
            return jsonify({'success': False, 'error': str(resp.errors)}), 400

        payload = resp.payload or {}
        out = {'success': True, 'data': payload}
        if include_raw:
            out['raw'] = payload
        return jsonify(out)

    except Exception as e:
        from sp_api.base.exceptions import SellingApiException
        if isinstance(e, SellingApiException):
            return jsonify({'success': False, 'error': str(e)}), 400
        return jsonify({'success': False, 'error': ss_errors._safe_error(e, 'listingagent:amazon_catalog_item')}), 500


def api_listingagent_amazon_get_listing():
    """Fetch live SKU details from Amazon Listings Items API."""
    try:
        sku = (request.args.get('sku') or '').strip()
        if not sku:
            return jsonify({'success': False, 'error': 'sku is required'}), 400

        included = (request.args.get('includedData') or 'summaries,attributes,issues').strip()
        included_data = [x.strip() for x in included.split(',') if x.strip()]

        credentials, seller_id, marketplace_id, marketplace = _amazon_spapi_context()
        if marketplace is None:
            return jsonify({'success': False, 'error': 'Amazon SP-API not available'}), 503

        from sp_api.api import ListingsItems
        from sp_api.base.exceptions import SellingApiException

        li = ListingsItems(credentials=credentials, marketplace=marketplace)
        resp = li.get_listings_item(
            seller_id,
            sku,
            marketplaceIds=[marketplace_id],
            includedData=included_data
        )
        if resp.errors:
            return jsonify({'success': False, 'error': str(resp.errors)}), 400
        return jsonify({'success': True, 'data': resp.payload or {}})

    except Exception as e:
        from sp_api.base.exceptions import SellingApiException
        if isinstance(e, SellingApiException):
            return jsonify({'success': False, 'error': str(e)}), 400
        return jsonify({'success': False, 'error': ss_errors._safe_error(e, 'listingagent:amazon_get_listing')}), 500
