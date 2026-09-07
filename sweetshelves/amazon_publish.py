"""Amazon publish for Sweet Shelves."""

import json
from flask import jsonify, request
from . import (
    amazon_catalog as ss_amazon_catalog, amazon_listing as ss_amazon_listing, errors as ss_errors,
    listing_lifecycle as ss_listing_lifecycle, listing_queue as ss_listing_queue, listing_settings as
    ss_listing_settings, normalization as ss_normalization,
)


def api_listingagent_amazon_put_offer():
    """Create/update an Amazon offer using Listings Items API (PUT)."""
    try:
        data = request.json or {}
        dry_run = bool(data.get('dry_run', False))
        debug_mode = str(data.get('debug', '')).strip().lower() in ('1', 'true', 'yes', 'on')

        settings = ss_listing_settings._listingagent_get_settings()

        upc = (data.get('upc') or '').strip()
        requested_sku = (data.get('sku') or '').strip()
        sku = requested_sku
        effective_sku_source = 'requested'
        asin = (data.get('asin') or '').strip()
        resolved_asin = asin
        submitted_images = [u for u in (data.get('images') or []) if isinstance(u, str) and u.strip()]
        submitted_images = ss_listing_queue._listagent_localize_image_urls(upc or requested_sku or asin or 'amazon', submitted_images)
        manual_mode = bool(data.get('manualMode') or data.get('manual_mode'))
        manual_title = (data.get('title') or data.get('listingTitle') or '').strip()
        manual_description = (data.get('listingDescription') or data.get('description') or '').strip()
        manual_brand = (data.get('brand') or '').strip()

        if not sku:
            return jsonify({'success': False, 'error': 'SKU is required'}), 400
        if not asin and not upc:
            return jsonify({'success': False, 'error': 'ASIN or UPC is required'}), 400

        quantity = ss_listing_settings._listingagent_parse_int(data.get('quantity'), 1) or 1
        price = ss_listing_settings._listingagent_parse_float(data.get('price'), None)
        if price is None:
            return jsonify({'success': False, 'error': 'Price is required'}), 400
        if manual_mode and not asin and not manual_title:
            return jsonify({'success': False, 'error': 'Title is required for Amazon No Catalog mode'}), 400

        condition_type = (data.get('conditionType') or settings.get('amazon_condition_type') or 'used_good').strip()
        condition_note = (data.get('conditionNote') or data.get('condition_note') or '').strip()
        fulfillment_channel_code = (data.get('fulfillmentChannelCode') or settings.get('amazon_fulfillment_channel_code') or 'DEFAULT').strip()
        currency = (data.get('currency') or settings.get('amazon_currency') or 'USD').strip()
        product_type = (data.get('productType') or settings.get('amazon_product_type') or 'PRODUCT').strip()
        requirements = (data.get('requirements') or settings.get('amazon_requirements') or 'LISTING_OFFER_ONLY').strip()
        merchant_shipping_group = (data.get('merchantShippingGroup') or settings.get('amazon_merchant_shipping_group') or '').strip()
        offer_audience = ss_amazon_listing._amazon_offer_audience(settings)
        effective_requirements = requirements or 'LISTING_OFFER_ONLY'
        if manual_mode and not asin and effective_requirements.upper() == 'LISTING_OFFER_ONLY':
            effective_requirements = 'LISTING'

        marketplace_id_override = (data.get('marketplaceId') or '').strip()
        mp_id = marketplace_id_override or (settings.get('amazon_marketplace_id') or 'ATVPDKIKX0DER').strip() or 'ATVPDKIKX0DER'

        condition_type = ss_amazon_listing._amazon_normalize_condition_type(condition_type, settings.get('amazon_condition_type') or 'used_good')
        fulfillment_channel_code = ss_amazon_listing._amazon_normalize_fulfillment_channel(
            fulfillment_channel_code,
            settings.get('amazon_fulfillment_channel_code') or 'DEFAULT'
        )

        is_new_condition = condition_type.lower() in ('new_new', 'new')
        manual_create_mode = bool(manual_mode and not asin)

        def _build_amazon_runtime_attributes(
            *,
            asin_value,
            include_identifiers,
            price_value_key_override=None,
            include_identifier_marketplace=False,
            include_marketplace_fields=True,
            include_offer_audience=True,
            force_include_upc_when_asin=False,
            force_exclude_asin_identifier=False,
            price_model='purchasable_offer',
            condition_value=None,
            fulfillment_value=None,
            quantity_value=None,
            merchant_shipping_group_value=None
        ):
            cond = condition_type if condition_value is None else condition_value
            fc = fulfillment_channel_code if fulfillment_value is None else fulfillment_value
            qty_val = quantity if quantity_value is None else quantity_value
            shipping_group_val = merchant_shipping_group if merchant_shipping_group_value is None else merchant_shipping_group_value
            if manual_create_mode:
                return ss_amazon_listing._build_amazon_manual_listing_attributes(
                    upc=upc,
                    asin=asin_value,
                    title=manual_title,
                    description_html=manual_description,
                    brand=manual_brand,
                    condition_type=cond,
                    condition_note=condition_note,
                    fulfillment_channel_code=fc,
                    quantity=qty_val,
                    currency=currency,
                    price=price,
                    include_identifiers=include_identifiers,
                    marketplace_id=mp_id,
                    offer_audience=offer_audience,
                    merchant_shipping_group=shipping_group_val,
                    price_value_key_override=price_value_key_override,
                    include_identifier_marketplace=include_identifier_marketplace,
                    include_marketplace_fields=include_marketplace_fields,
                    include_offer_audience=include_offer_audience,
                    force_include_upc_when_asin=force_include_upc_when_asin,
                    force_exclude_asin_identifier=force_exclude_asin_identifier,
                    price_model=price_model,
                    submitted_images=submitted_images
                )
            attrs_out = ss_amazon_listing._build_amazon_offer_attributes(
                upc=upc,
                asin=asin_value,
                condition_type=cond,
                condition_note=condition_note,
                fulfillment_channel_code=fc,
                quantity=qty_val,
                currency=currency,
                price=price,
                include_identifiers=include_identifiers,
                marketplace_id=mp_id,
                offer_audience=offer_audience,
                merchant_shipping_group=shipping_group_val,
                price_value_key_override=price_value_key_override,
                include_identifier_marketplace=include_identifier_marketplace,
                include_marketplace_fields=include_marketplace_fields,
                include_offer_audience=include_offer_audience,
                force_include_upc_when_asin=force_include_upc_when_asin,
                force_exclude_asin_identifier=force_exclude_asin_identifier,
                price_model=price_model
            )
            if submitted_images and not is_new_condition:
                attrs_out['item_image_locator'] = [
                    {'media_location': u.strip()} for u in submitted_images[:9] if u.strip()
                ]
            return attrs_out

        attributes = _build_amazon_runtime_attributes(
            asin_value=resolved_asin,
            include_identifiers=True
        )

        body = {
            'productType': product_type,
            'requirements': effective_requirements,
            'attributes': attributes
        }

        if dry_run:
            return jsonify({'success': True, 'dry_run': True, 'body': body})

        credentials, seller_id, marketplace_id, marketplace = ss_amazon_catalog._amazon_spapi_context()
        if marketplace is None:
            return jsonify({'success': False, 'error': 'Amazon SP-API not available'}), 503

        mp_id = marketplace_id_override or marketplace_id
        from sp_api.api import ListingsItems
        from sp_api.base.exceptions import SellingApiException
        li = ListingsItems(credentials=credentials, marketplace=marketplace)

        existing_product_type = ss_amazon_catalog._amazon_get_listing_product_type(li, seller_id, sku, mp_id)
        sku_exists = bool(existing_product_type)
        if not resolved_asin and upc and not manual_create_mode:
            resolved_asin = ss_amazon_catalog._amazon_resolve_asin_from_upc(credentials, marketplace, mp_id, upc)
        if not sku_exists and resolved_asin:
            local_asin_sku = ss_amazon_catalog._amazon_find_local_sku_by_asin(resolved_asin)
            if local_asin_sku and local_asin_sku.lower() != sku.lower():
                mapped_pt = ss_amazon_catalog._amazon_get_listing_product_type(li, seller_id, local_asin_sku, mp_id)
                if mapped_pt:
                    sku = local_asin_sku
                    existing_product_type = mapped_pt
                    sku_exists = True
                    effective_sku_source = 'local_asin_map'
        manual_create_mode = bool(manual_mode and not resolved_asin and not sku_exists)
        if not resolved_asin and not sku_exists and not manual_create_mode:
            return jsonify({
                'success': False,
                'error': 'Could not resolve ASIN from UPC. Pick an ASIN from Current Store Catalog Select first.'
            }), 400

        catalog_product_type = ss_amazon_catalog._amazon_get_catalog_product_type(credentials, marketplace, mp_id, resolved_asin) if resolved_asin else None
        resolved_product_type = (
            existing_product_type
            or catalog_product_type
            or product_type
            or 'PRODUCT'
        )
        # Rebuild attributes/body with runtime marketplace id so price field semantics
        # match the destination marketplace (value vs value_with_tax).
        include_identifiers_primary = not sku_exists
        attributes = _build_amazon_runtime_attributes(
            asin_value=resolved_asin,
            include_identifiers=include_identifiers_primary
        )
        body = {
            'productType': resolved_product_type,
            'requirements': effective_requirements if manual_create_mode else ('LISTING_OFFER_ONLY' if not sku_exists else effective_requirements),
            'attributes': attributes
        }

        restriction_info = None
        if resolved_asin:
            restriction_info = ss_amazon_listing._amazon_check_listing_restrictions_cached(
                credentials,
                marketplace,
                seller_id,
                mp_id,
                resolved_asin,
                condition_type=condition_type
            )
            if restriction_info.get('checked') and restriction_info.get('restricted'):
                reasons = restriction_info.get('reasons') or []
                extra = f" Reasons: {' | '.join(reasons)}" if reasons else ''
                return jsonify({
                    'success': False,
                    'error': f"ASIN appears restricted/gated for this account/marketplace.{extra}",
                    'restriction': restriction_info
                }), 400
            # If restriction API itself reports account/config id errors, fail fast with a clear hint.
            if not restriction_info.get('checked'):
                restriction_hints = ss_amazon_listing._amazon_restriction_error_hints(restriction_info, mp_id)
                if restriction_hints:
                    hint_txt = ' '.join(restriction_hints)
                    return jsonify({
                        'success': False,
                        'error': f"Amazon account configuration issue. {hint_txt}",
                        'restriction': restriction_info,
                        'context': {
                            'requested_sku': requested_sku,
                            'effective_sku': sku,
                            'effective_sku_source': effective_sku_source,
                            'sku_exists': sku_exists,
                            'marketplace_id': mp_id,
                            'asin': resolved_asin,
                            'manual_mode': manual_mode,
                            'manual_create_mode': manual_create_mode,
                        }
                    }), 400

        attempt_trace = []

        def _trace_jsonable(value):
            try:
                return json.loads(json.dumps(value, ensure_ascii=True, default=str))
            except Exception:
                return str(value)

        def _trace_attempt(attempt_label, *, body_obj=None, resp_obj=None, detail='', exc=None):
            if not debug_mode:
                return
            entry = {'attempt': attempt_label}
            if body_obj is not None:
                entry['body'] = _trace_jsonable(body_obj)
            if detail:
                entry['detail'] = str(detail)
            if resp_obj is not None:
                errs = getattr(resp_obj, 'errors', None)
                if errs:
                    entry['errors'] = _trace_jsonable(errs)
                try:
                    payload = getattr(resp_obj, 'payload', None) or {}
                    if payload:
                        entry['payload_status'] = payload.get('status')
                        issues = payload.get('issues') or []
                        if issues:
                            entry['payload_issues'] = _trace_jsonable(issues)
                except Exception:
                    pass
            if exc is not None:
                entry['exception'] = ss_amazon_listing._amazon_format_spapi_error(exc)
            attempt_trace.append(entry)

        def _amazon_resp_error_detail(resp_obj):
            errs = getattr(resp_obj, 'errors', None)
            if errs:
                return str(errs)
            payload = getattr(resp_obj, 'payload', None) or {}
            issues = payload.get('issues') or []
            err_issues = []
            if isinstance(issues, list):
                for it in issues:
                    try:
                        sev = str((it or {}).get('severity') or '').strip().upper()
                        if sev == 'ERROR':
                            err_issues.append(it)
                    except Exception:
                        pass
            if err_issues:
                return str(err_issues)
            status = str(payload.get('status') or '').strip().upper()
            if status in ('INVALID', 'ERROR', 'REJECTED', 'FAILURE'):
                return f"status={status}"
            return ''

        def _put_offer_once(*, body_obj, attempt_label):
            try:
                r = li.put_listings_item(
                    seller_id,
                    sku,
                    marketplaceIds=[mp_id],
                    issueLocale='en_US',
                    body=body_obj
                )
                detail = _amazon_resp_error_detail(r)
                _trace_attempt(attempt_label, body_obj=body_obj, resp_obj=r, detail=detail)
                if detail:
                    return False, r, f"{attempt_label}: {detail}"
                return True, r, ''
            except Exception as ex:
                _trace_attempt(attempt_label, body_obj=body_obj, exc=ex)
                return False, None, f"{attempt_label}: {ss_amazon_listing._amazon_format_spapi_error(ex)}"

        def _unique_values(values):
            out = []
            seen = set()
            for v in values:
                s = str(v or '').strip()
                if not s:
                    continue
                key = s.lower()
                if key in seen:
                    continue
                seen.add(key)
                out.append(s)
            return out

        if (not manual_create_mode) and (not sku_exists) and (not str(catalog_product_type or '').strip()) and str(product_type or '').strip().upper() in ('', 'PRODUCT'):
            return jsonify({
                'success': False,
                'error': 'Could not resolve Amazon productType for this ASIN. Select a catalog item and set Product Type from that category.',
                'resolved_asin': resolved_asin,
                'context': {
                    'requested_sku': requested_sku,
                    'effective_sku': sku,
                    'effective_sku_source': effective_sku_source,
                    'sku_exists': sku_exists,
                    'marketplace_id': mp_id,
                    'manual_mode': manual_mode,
                    'manual_create_mode': manual_create_mode,
                    'restriction_checked': bool((restriction_info or {}).get('checked')) if 'restriction_info' in locals() else False,
                    'restriction_restricted': bool((restriction_info or {}).get('restricted')) if 'restriction_info' in locals() else False,
                }
            }), 400

        patch_result_payload = None
        ok, resp, err_detail = _put_offer_once(body_obj=body, attempt_label='primary')
        if not ok:
            err_low = (err_detail or '').lower()
            is_invalid_input = ('invalidinput' in err_low) or ('invalid parameters' in err_low) or ('invalid parameter' in err_low)
            is_schema_price_error = ('99022' in err_low) or ('invalid_attribute' in err_low) or ('value_with_tax' in err_low)
            is_missing_identifier_error = (
                ('90220' in err_low or 'missing_attribute' in err_low)
                and (
                    'merchant_suggested_asin' in err_low
                    or 'externally_assigned_product_identifier' in err_low
                    or 'merchant suggested asin' in err_low
                    or 'external product id' in err_low
                )
            )
            if is_missing_identifier_error:
                is_invalid_input = True
            resolved_pt = (
                ss_amazon_catalog._amazon_get_catalog_product_type(credentials, marketplace, mp_id, resolved_asin)
                or ss_amazon_catalog._amazon_get_listing_product_type(li, seller_id, sku, mp_id)
                or resolved_product_type
            )
            product_type_candidates = _unique_values([resolved_pt, resolved_product_type, catalog_product_type, product_type, 'PRODUCT'])
            if is_schema_price_error and 'value_with_tax' in err_low:
                price_value_key_candidates = _unique_values(['value_with_tax', 'both', 'value', ss_amazon_listing._amazon_price_value_key(mp_id)])
            else:
                price_value_key_candidates = _unique_values([ss_amazon_listing._amazon_price_value_key(mp_id), 'value', 'value_with_tax', 'both'])
            if not price_value_key_candidates:
                price_value_key_candidates = ['value']
            upc_digits_ctx = ''.join(ch for ch in str(upc or '') if ch.isdigit())
            preferred_id_mode = 'upc' if manual_create_mode else ('both' if upc_digits_ctx else 'asin')
            preferred_price_key = 'value_with_tax' if ('value_with_tax' in err_low or ss_amazon_listing._amazon_price_value_key(mp_id) == 'value_with_tax') else 'value'
            price_key_candidates = _unique_values([preferred_price_key, 'both', 'value', 'value_with_tax'])[:3]
            product_type_plan = _unique_values([resolved_pt, resolved_product_type, product_type, 'PRODUCT'])[:2]
            shipping_group_plan = _unique_values([merchant_shipping_group, 'legacy-template-id', '']) if not sku_exists else ['']
            condition_plan = _unique_values([condition_type, 'new_new'])

            if is_invalid_input or is_schema_price_error:
                fallback_specs = []

                if sku_exists:
                    for pt in product_type_plan:
                        for pkey in price_key_candidates:
                            fallback_specs.append({
                                'label': f'fallback:update:{pt}:price_only:{pkey}',
                                'product_type': pt,
                                'requirements': 'LISTING_OFFER_ONLY',
                                'include_identifiers': False,
                                'include_condition': False,
                                'condition_value': '',
                                'include_qty': False,
                                'merchant_shipping_group': '',
                                'price_value_key': pkey,
                                'price_model': 'purchasable_offer',
                                'include_marketplace_fields': False,
                                'include_offer_audience': False,
                                'identifier_mode': 'asin',
                                'include_identifier_marketplace': False,
                            })
                            fallback_specs.append({
                                'label': f'fallback:update:{pt}:with_condition:{pkey}',
                                'product_type': pt,
                                'requirements': 'LISTING_OFFER_ONLY',
                                'include_identifiers': False,
                                'include_condition': True,
                                'condition_value': condition_type,
                                'include_qty': True,
                                'merchant_shipping_group': '',
                                'price_value_key': pkey,
                                'price_model': 'purchasable_offer',
                                'include_marketplace_fields': False,
                                'include_offer_audience': False,
                                'identifier_mode': 'asin',
                                'include_identifier_marketplace': False,
                            })
                            if is_missing_identifier_error:
                                id_modes = ['both', 'asin'] if upc_digits_ctx else ['asin']
                                for id_mode in id_modes:
                                    fallback_specs.append({
                                        'label': f'fallback:update:{pt}:with_identifiers:{id_mode}:{pkey}',
                                        'product_type': pt,
                                        'requirements': 'LISTING_OFFER_ONLY',
                                        'include_identifiers': True,
                                        'include_condition': True,
                                        'condition_value': condition_type,
                                        'include_qty': True,
                                        'merchant_shipping_group': '',
                                        'price_value_key': pkey,
                                        'price_model': 'purchasable_offer',
                                        'include_marketplace_fields': False,
                                        'include_offer_audience': False,
                                        'identifier_mode': id_mode,
                                        'include_identifier_marketplace': False,
                                    })
                else:
                    for pt in product_type_plan:
                        for pkey in price_key_candidates:
                            for sgroup in shipping_group_plan[:2]:
                                for cval in condition_plan[:2]:
                                    fallback_specs.append({
                                        'label': f'fallback:create:{pt}:full:{cval}:{preferred_id_mode}:{pkey}',
                                        'product_type': pt,
                                        'requirements': 'LISTING' if manual_create_mode else 'LISTING_OFFER_ONLY',
                                        'include_identifiers': True,
                                        'include_condition': True,
                                        'condition_value': cval,
                                        'include_qty': True,
                                        'merchant_shipping_group': sgroup,
                                        'price_value_key': pkey,
                                        'price_model': 'purchasable_offer',
                                        'include_marketplace_fields': False,
                                        'include_offer_audience': False,
                                        'identifier_mode': preferred_id_mode,
                                        'include_identifier_marketplace': False,
                                    })
                                fallback_specs.append({
                                    'label': f'fallback:create:{pt}:list_price:{preferred_id_mode}:{pkey}',
                                    'product_type': pt,
                                    'requirements': 'LISTING',
                                    'include_identifiers': True,
                                    'include_condition': False,
                                    'condition_value': '',
                                    'include_qty': True,
                                    'merchant_shipping_group': sgroup,
                                    'price_value_key': pkey,
                                    'price_model': 'list_price',
                                    'include_marketplace_fields': False,
                                    'include_offer_audience': False,
                                    'identifier_mode': preferred_id_mode,
                                    'include_identifier_marketplace': False,
                                })

                # Deterministic and compact fallback set for production.
                max_fallback_attempts = 48 if debug_mode else 18
                fallback_specs = fallback_specs[:max_fallback_attempts]

                for spec in fallback_specs:
                    cond_val = (spec.get('condition_value') or '') if spec.get('include_condition') else ''
                    fc_val = fulfillment_channel_code if spec.get('include_qty') else ''
                    qty_val = quantity if spec.get('include_qty') else 0
                    id_mode = (spec.get('identifier_mode') or 'asin').strip().lower()
                    force_upc_with_asin = id_mode in ('upc', 'both')
                    force_exclude_asin = id_mode == 'upc'
                    attrs_fb = _build_amazon_runtime_attributes(
                        asin_value=resolved_asin,
                        include_identifiers=bool(spec.get('include_identifiers')),
                        price_value_key_override=(spec.get('price_value_key') or ''),
                        include_marketplace_fields=bool(spec.get('include_marketplace_fields', True)),
                        include_offer_audience=bool(spec.get('include_offer_audience', True)),
                        force_include_upc_when_asin=force_upc_with_asin,
                        force_exclude_asin_identifier=force_exclude_asin,
                        include_identifier_marketplace=bool(spec.get('include_identifier_marketplace', False)),
                        price_model=(spec.get('price_model') or 'purchasable_offer'),
                        condition_value=cond_val,
                        fulfillment_value=fc_val,
                        quantity_value=qty_val,
                        merchant_shipping_group_value=(spec.get('merchant_shipping_group') or '')
                    )
                    body_fb = {
                        'productType': (spec.get('product_type') or resolved_pt or product_type),
                        'requirements': (spec.get('requirements') or 'LISTING_OFFER_ONLY'),
                        'attributes': attrs_fb
                    }
                    ok_fb, resp_fb, err_fb = _put_offer_once(body_obj=body_fb, attempt_label=spec.get('label') or 'fallback')
                    if ok_fb:
                        body = body_fb
                        resp = resp_fb
                        err_detail = ''
                        ok = True
                        break
                    err_detail = err_fb or err_detail

                # Existing SKU fallback: patch price only to avoid create-offer validation paths.
                if not ok and sku_exists:
                    try:
                        live_pt = ss_amazon_catalog._amazon_get_listing_product_type(li, seller_id, sku, mp_id) or resolved_pt or product_type
                        attrs_patch = ss_amazon_listing._amazon_offer_price_attrs(currency, price, mp_id, offer_audience=offer_audience)
                        ok_patch, debug_patch, err_patch = ss_amazon_listing._amazon_update_price_spapi(
                            li,
                            seller_id,
                            sku,
                            mp_id,
                            product_type=live_pt,
                            requirements='LISTING_OFFER_ONLY',
                            attrs=attrs_patch
                        )
                        _trace_attempt(
                            'fallback:price_patch',
                            body_obj={
                                'productType': live_pt,
                                'requirements': 'LISTING_OFFER_ONLY',
                                'attributes': attrs_patch
                            },
                            detail=err_patch or '',
                            exc=None if ok_patch else Exception(str(err_patch or 'price_patch failed'))
                        )
                        if ok_patch:
                            ok = True
                            resp = None
                            patch_result_payload = {
                                'fallback': 'price_patch',
                                'productType': live_pt,
                                'debug': debug_patch
                            }
                            err_detail = ''
                        elif err_patch:
                            err_detail = f"{err_detail} | patch:{err_patch}" if err_detail else f"patch:{err_patch}"
                    except Exception as e_patch:
                        ep = ss_amazon_listing._amazon_format_spapi_error(e_patch)
                        err_detail = f"{err_detail} | patch:{ep}" if err_detail else f"patch:{ep}"

            if not ok:
                # Last chance: include restrictions signal if available.
                if resolved_asin:
                    try:
                        if not restriction_info:
                            restriction_info = ss_amazon_listing._amazon_check_listing_restrictions_cached(
                                credentials, marketplace, seller_id, mp_id, resolved_asin, condition_type=condition_type
                            )
                    except Exception:
                        restriction_info = restriction_info or {}
                payload = {
                    'success': False,
                    'error': err_detail or 'Amazon offer update failed',
                    'body': body,
                    'resolved_asin': resolved_asin,
                    'resolved_product_type': resolved_product_type,
                    'context': {
                        'requested_sku': requested_sku,
                        'effective_sku': sku,
                        'effective_sku_source': effective_sku_source,
                        'sku_exists': sku_exists,
                        'marketplace_id': mp_id,
                        'condition_type': condition_type,
                        'fulfillment_channel_code': fulfillment_channel_code,
                        'merchant_shipping_group': merchant_shipping_group,
                        'manual_mode': manual_mode,
                        'manual_create_mode': manual_create_mode,
                        'restriction_checked': bool((restriction_info or {}).get('checked')),
                        'restriction_restricted': bool((restriction_info or {}).get('restricted')),
                        'restriction_raw': ((restriction_info or {}).get('raw') if isinstance((restriction_info or {}).get('raw'), dict) else {}),
                    }
                }
                if debug_mode and attempt_trace:
                    payload['attempts'] = attempt_trace[-80:]
                if restriction_info:
                    payload['restriction'] = restriction_info
                    if restriction_info.get('checked') and restriction_info.get('restricted'):
                        reasons = restriction_info.get('reasons') or []
                        if reasons:
                            payload['error'] = f"{payload['error']} | gated: {' | '.join(reasons)}"
                if debug_mode:
                    # Include compact context in debug mode only.
                    ctx_bits = [
                        f"sku_exists={sku_exists}",
                        f"marketplace={mp_id}",
                        f"asin={resolved_asin or ''}",
                        f"productType={resolved_product_type or ''}",
                        f"restriction_checked={bool((restriction_info or {}).get('checked'))}",
                        f"restricted={bool((restriction_info or {}).get('restricted'))}",
                    ]
                    payload['error'] = f"{payload['error']} | ctx: {' | '.join(ctx_bits)}"
                return jsonify(payload), 400

        # Mark BOL as listed on Amazon so /items-to-list marketplace checkboxes stay in sync.
        target_lot = ss_normalization._normalize_lot_number(data.get('lot_number') or data.get('lot'))
        target_item_id = data.get('id')
        if target_item_id is None:
            target_item_id = data.get('item_id')
        if target_item_id is None:
            target_item_id = data.get('bol_id')
        if upc or target_item_id is not None:
            try:
                ss_listing_lifecycle._listing_center_mark_bol_listed(
                    'amazon',
                    upc=upc,
                    lot_number=target_lot,
                    item_id=target_item_id
                )
            except Exception:
                pass
 
        # Record listing completion in listagent.db (if UPC is available)
        try:
            if upc:
                ss_listing_queue._listagent_mark_listed(
                    upc,
                    platform='amazon',
                    sku=sku,
                    asin=resolved_asin or asin,
                    marketplace_id=mp_id,
                    price=price,
                    quantity=quantity,
                    source='listingagent'
                )
        except Exception:
            pass
 
        data_payload = {}
        try:
            if resp is not None:
                data_payload = getattr(resp, 'payload', None) or {}
        except Exception:
            data_payload = {}
        if patch_result_payload:
            data_payload = {**data_payload, **patch_result_payload}
        out_payload = {
            'success': True,
            'data': data_payload,
            'body': body,
            'resolved_asin': resolved_asin,
            'resolved_product_type': resolved_product_type,
            'context': {
                'requested_sku': requested_sku,
                'effective_sku': sku,
                'effective_sku_source': effective_sku_source,
                'sku_exists': sku_exists,
                'marketplace_id': mp_id,
                'manual_mode': manual_mode,
                'manual_create_mode': manual_create_mode,
                'merchant_shipping_group': merchant_shipping_group,
                'restriction_checked': bool((restriction_info or {}).get('checked')),
                'restriction_restricted': bool((restriction_info or {}).get('restricted')),
            },
        }
        if debug_mode and attempt_trace:
            out_payload['attempts'] = attempt_trace[-80:]
        return jsonify(out_payload)
 
    except Exception as e:
        from sp_api.base.exceptions import SellingApiException
        if isinstance(e, SellingApiException):
            return jsonify({'success': False, 'error': str(e)}), 400
        return jsonify({'success': False, 'error': ss_errors._safe_error(e, 'listingagent:amazon_put_offer')}), 500
