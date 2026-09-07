"""Amazon listing for Sweet Shelves."""

import io
import re
import re as _re
import threading
import time
from . import mail_center as ss_mail_center


_LISTINGAGENT_AMAZON_RESTRICTIONS_CACHE = {}


_LISTINGAGENT_AMAZON_RESTRICTIONS_LOCK = threading.Lock()


_LISTINGAGENT_AMAZON_RESTRICTIONS_TTL = 300


def _amazon_normalize_condition_type(raw, default_condition='used_good'):
    allowed = {
        'new_new',
        'used_like_new',
        'used_very_good',
        'used_good',
        'used_acceptable',
        'collectible_like_new',
        'collectible_very_good',
        'collectible_good',
        'collectible_acceptable',
        'refurbished_refurbished',
        'club_club',
    }
    if raw is None:
        return default_condition
    s = str(raw).strip()
    if not s:
        return default_condition
    if s.isdigit():
        code_map = {
            11: 'new_new',
            1: 'used_like_new',
            2: 'used_very_good',
            3: 'used_good',
            4: 'used_acceptable',
            5: 'collectible_like_new',
            6: 'collectible_very_good',
            7: 'collectible_good',
            8: 'collectible_acceptable',
            10: 'refurbished_refurbished',
        }
        return code_map.get(int(s), default_condition)

    lowered = s.lower().strip()
    label_map = {
        'new': 'new_new',
        'brand_new': 'new_new',
        'used_like_new': 'used_like_new',
        'used_very_good': 'used_very_good',
        'used_good': 'used_good',
        'used_acceptable': 'used_acceptable',
        'collectible_like_new': 'collectible_like_new',
        'collectible_very_good': 'collectible_very_good',
        'collectible_good': 'collectible_good',
        'collectible_acceptable': 'collectible_acceptable',
        'refurbished': 'refurbished_refurbished',
        'refurbished_refurbished': 'refurbished_refurbished',
        'club': 'club_club',
        'club_club': 'club_club',
    }
    cleaned = _re.sub(r'[^a-z0-9]+', '_', lowered).strip('_')
    if cleaned in label_map:
        return label_map[cleaned]
    if cleaned in allowed:
        return cleaned
    return default_condition


def _amazon_normalize_fulfillment_channel(raw, default_fc='DEFAULT'):
    if raw is None:
        return default_fc
    s = str(raw).strip().upper()
    if not s:
        return default_fc
    if s in ('DEFAULT', 'AMAZON_NA'):
        return s
    if s == 'AFN':
        return 'AMAZON_NA'
    if s == 'MFN':
        return 'DEFAULT'
    return default_fc


def _amazon_format_spapi_error(err):
    try:
        from sp_api.base.exceptions import SellingApiException
        if isinstance(err, SellingApiException):
            payload = getattr(err, 'payload', None) or getattr(err, 'errors', None)
            if payload:
                return f"{err} | payload={payload}"
    except Exception:
        pass
    return str(err)


def _amazon_feeds_error(err, step=''):
    msg = _amazon_format_spapi_error(err)
    if step:
        return f"{step}: {msg}"
    return msg


def _amazon_create_feed_document(feeds, content_type, body_bytes):
    import inspect as _inspect
    f = io.BytesIO(body_bytes)
    params = {}
    try:
        params = _inspect.signature(feeds.create_feed_document).parameters
    except Exception:
        params = {}

    # Try file-based signature (newer sp-api versions)
    if 'file' in params or any(p.kind == _inspect.Parameter.VAR_KEYWORD for p in params.values()):
        for kwargs in (
            {'file': f, 'content_type': content_type},
            {'file': f, 'contentType': content_type},
        ):
            try:
                f.seek(0)
                resp = feeds.create_feed_document(**kwargs)
                payload = getattr(resp, 'payload', None) or {}
                doc_id = payload.get('feedDocumentId') or payload.get('feed_document_id')
                url = payload.get('url')
                return doc_id, url, True
            except TypeError:
                continue
    # Fallback: legacy signature (no file upload)
    resp = feeds.create_feed_document(contentType=content_type)
    payload = getattr(resp, 'payload', None) or {}
    doc_id = payload.get('feedDocumentId') or payload.get('feed_document_id')
    url = payload.get('url')
    return doc_id, url, False


def _amazon_create_feed(feeds, *, feed_type, marketplace_ids, input_feed_document_id):
    import inspect as _inspect
    try:
        params = _inspect.signature(feeds.create_feed).parameters
    except Exception:
        params = {}

    # Newer sp-api supports keyword args
    if not params or 'feedType' in params or 'feed_type' in params or any(p.kind == _inspect.Parameter.VAR_KEYWORD for p in params.values()):
        try:
            return feeds.create_feed(feedType=feed_type, marketplaceIds=marketplace_ids, inputFeedDocumentId=input_feed_document_id)
        except TypeError:
            try:
                return feeds.create_feed(feed_type=feed_type, marketplace_ids=marketplace_ids, input_feed_document_id=input_feed_document_id)
            except Exception:
                pass

    # Legacy signature: (feed_type, input_feed_document_id, marketplace_ids)
    return feeds.create_feed(feed_type, input_feed_document_id, marketplace_ids)


def _amazon_update_price_spapi(li, seller_id, sku, mp_id, *, product_type, requirements, attrs):
    debug = {'attempts': []}

    def _record_attempt(method, body, resp=None, exc=None):
        entry = {'method': method, 'body': body}
        if resp is not None:
            entry['errors'] = getattr(resp, 'errors', None)
            try:
                payload = getattr(resp, 'payload', None) or {}
                if payload:
                    entry['payload_status'] = payload.get('status')
                    issues = payload.get('issues')
                    if issues:
                        entry['payload_issues'] = issues
            except Exception:
                pass
        if exc is not None:
            entry['exception'] = _amazon_format_spapi_error(exc)
        debug['attempts'].append(entry)

    def _payload_has_error(resp):
        try:
            payload = getattr(resp, 'payload', None) or {}
        except Exception:
            payload = {}
        if not payload:
            return False, None
        try:
            status = str(payload.get('status') or '').strip().upper()
        except Exception:
            status = ''
        issues = []
        try:
            issues = payload.get('issues') or []
        except Exception:
            issues = []
        err_issues = []
        try:
            for it in issues:
                sev = str(it.get('severity') or '').strip().upper()
                if sev == 'ERROR':
                    err_issues.append(it)
        except Exception:
            pass
        if err_issues:
            return True, f"issues={err_issues}"
        if status in ('INVALID', 'ERROR', 'REJECTED', 'FAILURE'):
            return True, f"status={status}"
        return False, None

    patch_failed = False
    if hasattr(li, 'patch_listings_item'):
        patch_body = {
            'productType': product_type,
            'patches': []
        }
        if attrs.get('purchasable_offer'):
            patch_body['patches'].append({
                'op': 'replace',
                'path': '/attributes/purchasable_offer',
                'value': attrs['purchasable_offer']
            })
        if attrs.get('condition_type'):
            patch_body['patches'].append({
                'op': 'replace',
                'path': '/attributes/condition_type',
                'value': attrs['condition_type']
            })
        if patch_body['patches']:
            try:
                resp = li.patch_listings_item(
                    seller_id,
                    sku,
                    marketplaceIds=[mp_id],
                    issueLocale='en_US',
                    body=patch_body
                )
                _record_attempt('PATCH', patch_body, resp=resp)
                if not getattr(resp, 'errors', None):
                    payload_err, payload_detail = _payload_has_error(resp)
                    if payload_err:
                        return False, debug, payload_detail
                    return True, debug, None
            except Exception as e:
                _record_attempt('PATCH', patch_body, exc=e)
                patch_failed = True

    put_body = {
        'productType': product_type,
        'requirements': requirements,
        'attributes': attrs
    }
    try:
        resp = li.put_listings_item(
            seller_id,
            sku,
            marketplaceIds=[mp_id],
            issueLocale='en_US',
            body=put_body
        )
        _record_attempt('PUT', put_body, resp=resp)
        if getattr(resp, 'errors', None):
            return False, debug, resp.errors
        payload_err, payload_detail = _payload_has_error(resp)
        if payload_err:
            return False, debug, payload_detail
        return True, debug, None
    except Exception as e:
        _record_attempt('PUT', put_body, exc=e)
        return False, debug, e


def _amazon_price_value_key(marketplace_id):
    # US marketplace expects "value". VAT marketplaces often use "value_with_tax".
    if not marketplace_id:
        return 'value'
    mp = str(marketplace_id).strip().upper()
    if mp == 'ATVPDKIKX0DER':
        return 'value'
    return 'value_with_tax'


def _amazon_offer_audience(settings=None):
    val = None
    try:
        if settings and settings.get('amazon_offer_audience'):
            val = str(settings.get('amazon_offer_audience')).strip()
    except Exception:
        val = None
    return val or 'ALL'


def _amazon_restriction_error_hints(restriction_info, marketplace_id):
    hints = []
    try:
        raw = (restriction_info or {}).get('raw') or {}
        attempts = raw.get('call_attempts') or []
        texts = []
        for a in attempts:
            if not isinstance(a, dict):
                continue
            err = (a.get('error') or '').strip()
            if err:
                texts.append(err.lower())
        joined = ' | '.join(texts)
        if "invalid 'sellerid' provided" in joined or 'invalid \"sellerid\" provided' in joined:
            hints.append('Amazon restriction check reports invalid sellerId for these credentials.')
        if "invalid 'marketplaceids' provided" in joined or 'invalid \"marketplaceids\" provided' in joined:
            hints.append(f"Amazon restriction check reports invalid marketplaceIds. Verify marketplace `{marketplace_id}` is enabled on this seller account.")
    except Exception:
        pass
    return hints


def _amazon_check_listing_restrictions(credentials, marketplace, seller_id, marketplace_id, asin, condition_type=''):
    """
    Best-effort check for Amazon gated/restricted listings.
    Returns:
      {
        'checked': bool,
        'restricted': bool,
        'reasons': [str...],
        'raw': dict
      }
    """
    out = {'checked': False, 'restricted': False, 'reasons': [], 'raw': {}}
    asin = (asin or '').strip()
    if not asin:
        return out
    try:
        from sp_api.api import ListingsRestrictions
        import inspect as _inspect
        lr = ListingsRestrictions(credentials=credentials, marketplace=marketplace)
        fn = getattr(lr, 'get_listings_restrictions', None)
        if not callable(fn):
            out['raw'] = {'error': 'get_listings_restrictions not available'}
            return out

        cond = (condition_type or '').strip()
        debug_attempts = []
        resp = None
        fatal_identity_error = ''

        # Keyword attempts first (different client versions use different names).
        kw_attempts = [
            {'asin': asin, 'sellerId': seller_id, 'marketplaceIds': [marketplace_id], 'conditionType': cond, 'reasonLocale': 'en_US'},
            {'asin': asin, 'sellerId': seller_id, 'marketplaceIds': marketplace_id, 'conditionType': cond, 'reasonLocale': 'en_US'},
            {'asin': asin, 'sellerId': seller_id, 'marketplaceIds': [marketplace_id], 'itemCondition': cond, 'reasonLocale': 'en_US'},
            {'asin': asin, 'sellerId': seller_id, 'marketplaceIds': [marketplace_id], 'reasonLocale': 'en_US'},
            {'asin': asin, 'sellerId': seller_id, 'conditionType': cond, 'reasonLocale': 'en_US'},
            {'asin': asin, 'sellerId': seller_id, 'conditionType': cond},
            {'asin': asin, 'sellerId': seller_id},
            {'asin': asin, 'seller_id': seller_id, 'marketplace_ids': [marketplace_id], 'condition_type': cond, 'reason_locale': 'en_US'},
            {'asin': asin, 'seller_id': seller_id, 'marketplace_ids': marketplace_id, 'condition_type': cond, 'reason_locale': 'en_US'},
            {'asin': asin, 'seller_id': seller_id, 'marketplace_ids': [marketplace_id], 'reason_locale': 'en_US'},
            {'asin': asin, 'seller_id': seller_id, 'condition_type': cond, 'reason_locale': 'en_US'},
            {'asin': asin, 'seller_id': seller_id, 'condition_type': cond},
            {'asin': asin, 'seller_id': seller_id},
        ]
        try:
            sig = _inspect.signature(fn)
            params = sig.parameters
        except Exception:
            sig = None
            params = {}
        has_var_kw = False
        allowed_names = set()
        has_positional = False
        if params:
            for name, p in params.items():
                if name == 'self':
                    continue
                allowed_names.add(name)
                if p.kind == _inspect.Parameter.VAR_KEYWORD:
                    has_var_kw = True
                if p.kind in (_inspect.Parameter.POSITIONAL_ONLY, _inspect.Parameter.POSITIONAL_OR_KEYWORD, _inspect.Parameter.VAR_POSITIONAL):
                    has_positional = True

        for kwargs in kw_attempts:
            try:
                candidate_kwargs = {k: v for k, v in kwargs.items() if v not in (None, '', [])}
                if has_var_kw:
                    # Signature exposes **kwargs: do not pre-filter keys.
                    use_kwargs = candidate_kwargs
                elif allowed_names:
                    use_kwargs = {k: v for k, v in candidate_kwargs.items() if k in allowed_names}
                else:
                    use_kwargs = candidate_kwargs
                if not use_kwargs:
                    debug_attempts.append({'mode': 'kwargs', 'keys': sorted(list(candidate_kwargs.keys())), 'error': 'filtered_empty'})
                    continue
                resp = fn(**use_kwargs)
                out['checked'] = True
                debug_attempts.append({'mode': 'kwargs', 'keys': sorted(list(use_kwargs.keys())), 'ok': True})
                break
            except TypeError as te:
                debug_attempts.append({'mode': 'kwargs', 'keys': sorted(list(kwargs.keys())), 'error': f'TypeError: {te}'})
                continue
            except Exception as e:
                err_txt = str(e)
                debug_attempts.append({'mode': 'kwargs', 'keys': sorted(list(kwargs.keys())), 'error': err_txt})
                low = err_txt.lower()
                if ("invalid 'sellerid' provided" in low) or ("invalid 'marketplaceids' provided" in low):
                    fatal_identity_error = err_txt
                    break
                continue

        # Positional attempts only when signature indicates positional args are supported.
        if resp is None and has_positional and not fatal_identity_error:
            pos_attempts = [
                (asin, seller_id, [marketplace_id], cond, 'en_US'),
                (asin, seller_id, [marketplace_id], cond),
                (asin, seller_id, [marketplace_id]),
            ]
            for args in pos_attempts:
                try:
                    use_args = [a for a in args if a not in (None, '', [])]
                    resp = fn(*use_args)
                    out['checked'] = True
                    debug_attempts.append({'mode': 'args', 'argc': len(use_args), 'ok': True})
                    break
                except TypeError as te:
                    debug_attempts.append({'mode': 'args', 'argc': len(args), 'error': f'TypeError: {te}'})
                    continue
                except Exception as e:
                    debug_attempts.append({'mode': 'args', 'argc': len(args), 'error': str(e)})
                    continue
        elif resp is None and not has_positional and not fatal_identity_error:
            debug_attempts.append({'mode': 'args', 'error': 'skipped_no_positional_signature'})

        if resp is None:
            raw_out = {'call_attempts': debug_attempts[-12:]}
            if fatal_identity_error:
                raw_out['fatal_error'] = fatal_identity_error
            out['raw'] = raw_out
            return out

        out['checked'] = True
        if getattr(resp, 'errors', None):
            # If API returns errors, keep checked=True but don't force restricted.
            out['raw'] = {'errors': resp.errors, 'call_attempts': debug_attempts[-12:]}
            return out

        payload = getattr(resp, 'payload', None) or {}
        out['raw'] = {'payload': payload, 'call_attempts': debug_attempts[-12:]}
        restrictions = payload.get('restrictions') or []
        reasons = []
        restricted = False
        if isinstance(restrictions, list):
            for r in restrictions:
                if not isinstance(r, dict):
                    continue
                rr = r.get('reasons') or []
                if isinstance(rr, list) and rr:
                    restricted = True
                    for reason in rr:
                        if not isinstance(reason, dict):
                            continue
                        code = (reason.get('reasonCode') or reason.get('reason_code') or '').strip()
                        msg = (reason.get('message') or '').strip()
                        txt = f"{code}: {msg}".strip(': ').strip()
                        if txt:
                            reasons.append(txt)
        out['restricted'] = bool(restricted)
        out['reasons'] = reasons[:12]
        return out
    except Exception as e:
        out['raw'] = {'error': str(e)}
        return out


def _amazon_check_listing_restrictions_cached(credentials, marketplace, seller_id, marketplace_id, asin, condition_type='', force_refresh=False):
    asin_clean = (asin or '').strip().upper()
    mp = (marketplace_id or '').strip()
    cond = (condition_type or '').strip().lower()
    sid = (seller_id or '').strip()
    if not asin_clean:
        return {'checked': False, 'restricted': False, 'reasons': [], 'raw': {}}

    key = f'{sid}|{mp}|{asin_clean}|{cond}'
    now = time.time()
    if not force_refresh:
        cached = _LISTINGAGENT_AMAZON_RESTRICTIONS_CACHE.get(key)
        if cached and (now - float(cached.get('ts') or 0)) < _LISTINGAGENT_AMAZON_RESTRICTIONS_TTL:
            out = dict(cached.get('data') or {})
            out['_cached'] = True
            return out

    with _LISTINGAGENT_AMAZON_RESTRICTIONS_LOCK:
        if not force_refresh:
            cached = _LISTINGAGENT_AMAZON_RESTRICTIONS_CACHE.get(key)
            now = time.time()
            if cached and (now - float(cached.get('ts') or 0)) < _LISTINGAGENT_AMAZON_RESTRICTIONS_TTL:
                out = dict(cached.get('data') or {})
                out['_cached'] = True
                return out

        result = _amazon_check_listing_restrictions(
            credentials,
            marketplace,
            seller_id,
            marketplace_id,
            asin_clean,
            condition_type=condition_type
        )
        now_store = time.time()
        if len(_LISTINGAGENT_AMAZON_RESTRICTIONS_CACHE) > 2000:
            expired = []
            for k, v in _LISTINGAGENT_AMAZON_RESTRICTIONS_CACHE.items():
                if (now_store - float((v or {}).get('ts') or 0)) > (_LISTINGAGENT_AMAZON_RESTRICTIONS_TTL * 3):
                    expired.append(k)
            for k in expired[:1000]:
                _LISTINGAGENT_AMAZON_RESTRICTIONS_CACHE.pop(k, None)
        _LISTINGAGENT_AMAZON_RESTRICTIONS_CACHE[key] = {
            'ts': now_store,
            'data': dict(result or {})
        }
        out = dict(result or {})
        out['_cached'] = False
        return out


def _build_amazon_offer_attributes(
    *,
    upc,
    asin,
    condition_type,
    fulfillment_channel_code,
    quantity,
    currency,
    price,
    include_identifiers=True,
    marketplace_id=None,
    offer_audience='ALL',
    merchant_shipping_group=None,
    price_value_key_override=None,
    include_identifier_marketplace=False,
    include_marketplace_fields=True,
    include_offer_audience=True,
    force_include_upc_when_asin=False,
    force_exclude_asin_identifier=False,
    price_model='purchasable_offer',
    condition_note=''
):
    attrs = {}
    asin_clean = (asin or '').strip().upper()
    upc_digits = ''.join(ch for ch in str(upc or '') if ch.isdigit())
    upc_type = ''
    if upc_digits:
        if len(upc_digits) in (8, 11, 12):
            upc_type = 'UPC'
        elif len(upc_digits) == 13:
            upc_type = 'EAN'
        elif len(upc_digits) == 14:
            upc_type = 'GTIN'

    if include_identifiers:
        if asin_clean and not force_exclude_asin_identifier:
            asin_entry = {'value': asin_clean}
            if marketplace_id and include_identifier_marketplace:
                asin_entry['marketplace_id'] = marketplace_id
            attrs['merchant_suggested_asin'] = [asin_entry]

        # Optional: attach UPC to help Amazon match when ASIN is not explicitly supplied.
        # Sending both ASIN and external UPC can trigger generic InvalidInput on some product types.
        if upc_digits and upc_type and (force_include_upc_when_asin or not asin_clean):
            upc_entry = {'value': upc_digits}
            upc_type_entry = {'value': upc_type}
            if marketplace_id and include_identifier_marketplace:
                upc_entry['marketplace_id'] = marketplace_id
                upc_type_entry['marketplace_id'] = marketplace_id
            attrs['externally_assigned_product_identifier'] = [upc_entry]
            attrs['externally_assigned_product_identifier_type'] = [upc_type_entry]

    if condition_type:
        entry = {'value': condition_type}
        if marketplace_id and include_marketplace_fields:
            entry['marketplace_id'] = marketplace_id
        attrs['condition_type'] = [entry]

    condition_note = str(condition_note or '').strip()
    if condition_note:
        note_entry = {'value': condition_note}
        if marketplace_id and include_marketplace_fields:
            note_entry['marketplace_id'] = marketplace_id
        attrs['condition_note'] = [note_entry]

    if fulfillment_channel_code:
        entry = {
            'fulfillment_channel_code': fulfillment_channel_code,
            'quantity': int(quantity or 1)
        }
        if marketplace_id and include_marketplace_fields:
            entry['marketplace_id'] = marketplace_id
        attrs['fulfillment_availability'] = [entry]

    # MFN create-offer flows often require a merchant shipping template id.
    msg = (merchant_shipping_group or '').strip()
    if msg:
        sg = {'value': msg}
        if marketplace_id and include_marketplace_fields:
            sg['marketplace_id'] = marketplace_id
        attrs['merchant_shipping_group'] = [sg]

    if price is not None:
        value_key = (price_value_key_override or '').strip() or _amazon_price_value_key(marketplace_id)
        model = (price_model or 'purchasable_offer').strip().lower()
        if model == 'list_price':
            lp = {
                'currency': (currency or 'USD').upper(),
                'value': float(price)
            }
            if marketplace_id and include_marketplace_fields:
                lp['marketplace_id'] = marketplace_id
            attrs['list_price'] = [lp]
        else:
            price_num = float(price)
            schedule_entry = {}
            # Some Amazon product-type schemas require value_with_tax; include both keys for compatibility.
            if value_key == 'both':
                schedule_entry['value'] = price_num
                schedule_entry['value_with_tax'] = price_num
            elif value_key == 'value_with_tax':
                schedule_entry['value_with_tax'] = price_num
                schedule_entry['value'] = price_num
            else:
                schedule_entry['value'] = price_num
                schedule_entry['value_with_tax'] = price_num
            offer = {
                'currency': (currency or 'USD').upper(),
                'our_price': [{
                    'schedule': [schedule_entry]
                }]
            }
            if include_offer_audience and (offer_audience or '').strip():
                offer['audience'] = offer_audience
            if marketplace_id and include_marketplace_fields:
                offer['marketplace_id'] = marketplace_id
            attrs['purchasable_offer'] = [{
                **offer
            }]

    return attrs


def _amazon_manual_listing_text(value, limit=2000):
    raw = str(value or '').strip()
    if not raw:
        return ''
    try:
        text = ss_mail_center._mail_html_to_text(raw)
    except Exception:
        text = raw
    text = re.sub(r'\s+', ' ', str(text or '')).strip()
    if limit and len(text) > limit:
        text = text[:limit].rstrip()
    return text


def _amazon_manual_listing_bullets(description_text, limit=5):
    text = str(description_text or '').replace('\r', '\n')
    parts = []
    for chunk in re.split(r'(?:\n+|[.;]\s+)', text):
        clean = re.sub(r'\s+', ' ', str(chunk or '')).strip(' -\t\r\n')
        if not clean:
            continue
        if len(clean) > 220:
            clean = clean[:220].rstrip()
        parts.append(clean)
        if len(parts) >= max(1, int(limit or 5)):
            break
    return parts


def _build_amazon_manual_listing_attributes(
    *,
    upc,
    asin,
    title,
    description_html,
    brand,
    condition_type,
    condition_note,
    fulfillment_channel_code,
    quantity,
    currency,
    price,
    include_identifiers=True,
    marketplace_id=None,
    offer_audience='ALL',
    merchant_shipping_group=None,
    price_value_key_override=None,
    include_identifier_marketplace=False,
    include_marketplace_fields=True,
    include_offer_audience=True,
    force_include_upc_when_asin=False,
    force_exclude_asin_identifier=False,
    price_model='purchasable_offer',
    submitted_images=None
):
    attrs = _build_amazon_offer_attributes(
        upc=upc,
        asin=asin,
        condition_type=condition_type,
        condition_note=condition_note,
        fulfillment_channel_code=fulfillment_channel_code,
        quantity=quantity,
        currency=currency,
        price=price,
        include_identifiers=include_identifiers,
        marketplace_id=marketplace_id,
        offer_audience=offer_audience,
        merchant_shipping_group=merchant_shipping_group,
        price_value_key_override=price_value_key_override,
        include_identifier_marketplace=include_identifier_marketplace,
        include_marketplace_fields=include_marketplace_fields,
        include_offer_audience=include_offer_audience,
        force_include_upc_when_asin=force_include_upc_when_asin,
        force_exclude_asin_identifier=force_exclude_asin_identifier,
        price_model=price_model
    )

    clean_title = _amazon_manual_listing_text(title, limit=200)
    clean_desc = _amazon_manual_listing_text(description_html, limit=4000)
    clean_brand = _amazon_manual_listing_text(brand, limit=100)

    if clean_title:
        entry = {'value': clean_title}
        if marketplace_id and include_marketplace_fields:
            entry['marketplace_id'] = marketplace_id
        attrs['item_name'] = [entry]

    if clean_desc:
        entry = {'value': clean_desc}
        if marketplace_id and include_marketplace_fields:
            entry['marketplace_id'] = marketplace_id
        attrs['product_description'] = [entry]

        bullets = _amazon_manual_listing_bullets(clean_desc, limit=5)
        if bullets:
            attrs['bullet_point'] = []
            for bullet in bullets:
                bullet_entry = {'value': bullet}
                if marketplace_id and include_marketplace_fields:
                    bullet_entry['marketplace_id'] = marketplace_id
                attrs['bullet_point'].append(bullet_entry)

    if clean_brand:
        entry = {'value': clean_brand}
        if marketplace_id and include_marketplace_fields:
            entry['marketplace_id'] = marketplace_id
        attrs['brand'] = [entry]

    image_urls = [u.strip() for u in (submitted_images or []) if isinstance(u, str) and u.strip()]
    if image_urls:
        attrs['item_image_locator'] = [{'media_location': u} for u in image_urls[:9]]

    return attrs


def _amazon_offer_price_attrs(currency, price, marketplace_id=None, offer_audience='ALL', include_marketplace_fields=True, include_offer_audience=True):
    value_key = _amazon_price_value_key(marketplace_id)
    price_num = float(price)
    schedule_entry = {}
    if value_key == 'value_with_tax':
        schedule_entry['value_with_tax'] = price_num
        schedule_entry['value'] = price_num
    else:
        schedule_entry['value'] = price_num
        schedule_entry['value_with_tax'] = price_num
    offer = {
        'currency': (currency or 'USD').upper(),
        'our_price': [{
            'schedule': [schedule_entry]
        }]
    }
    if include_offer_audience and (offer_audience or '').strip():
        offer['audience'] = offer_audience
    if marketplace_id and include_marketplace_fields:
        offer['marketplace_id'] = marketplace_id
    return {'purchasable_offer': [offer]}
