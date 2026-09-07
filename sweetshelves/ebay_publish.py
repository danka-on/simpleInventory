"""Ebay publish for Sweet Shelves."""

import ebay_mapping
import os
import requests
from flask import jsonify, request
from . import (
    ebay_policies as ss_ebay_policies, errors as ss_errors, listing_lifecycle as ss_listing_lifecycle,
    listing_queue as ss_listing_queue, listing_settings as ss_listing_settings, normalization as
    ss_normalization,
)


_publish_mapped_ebay_listing = None  # Bound during route registration.


def _listingagent_ebay_create_draft_offer(data, *, dry_run=False):
    if data.get('mappingReferenceId') and not dry_run:
        raise ss_errors._ListingAgentUserError('Save this native eBay draft locally, then use Publish to retain its recommendation reference.')
    upc = (data.get('upc') or '').strip()
    sku = (data.get('sku') or upc).strip()
    if not upc:
        raise ss_errors._ListingAgentUserError('UPC is required')
    if not sku:
        raise ss_errors._ListingAgentUserError('SKU is required')

    title = (data.get('title') or '').strip()
    description = (data.get('description') or '').strip()
    condition = (data.get('condition') or '').strip() or 'USED_GOOD'

    quantity = ss_listing_settings._listingagent_parse_int(data.get('quantity'), 1) or 1
    price = ss_listing_settings._listingagent_parse_float(data.get('price'), None)

    settings = ss_listing_settings._listingagent_get_settings()
    marketplace_id = (data.get('marketplaceId') or settings.get('ebay_marketplace_id') or 'EBAY_US').strip()
    currency = (data.get('currency') or settings.get('ebay_currency') or 'USD').strip()
    category_id = (data.get('categoryId') or settings.get('ebay_category_id') or '').strip()
    listing_duration = (data.get('listingDuration') or settings.get('ebay_listing_duration') or 'GTC').strip()

    merchant_location_key_input = (data.get('merchantLocationKey') or settings.get('ebay_location_key') or '').strip()
    fulfillment_policy_id = (data.get('fulfillmentPolicyId') or settings.get('ebay_fulfillment_policy_id') or '').strip()
    payment_policy_id = (data.get('paymentPolicyId') or settings.get('ebay_payment_policy_id') or '').strip()
    return_policy_id = (data.get('returnPolicyId') or settings.get('ebay_return_policy_id') or '').strip()

    listing_description = (data.get('listingDescription') or '').strip()
    condition_description = (data.get('conditionDescription') or data.get('condition_description') or '').strip()

    images = data.get('images') or []
    if isinstance(images, str):
        images = [images]
    images = ss_listing_queue._listagent_localize_image_urls(upc, images)

    aspects = data.get('aspects') or {}
    if not isinstance(aspects, dict):
        aspects = {}
    catalog_epid = (data.get('catalogEpid') or data.get('epid') or '').strip()

    merchant_location_key, location_autofixed, all_location_keys = ss_ebay_policies._listingagent_resolve_ebay_location_key(merchant_location_key_input)
    if location_autofixed:
        # Persist auto-healed location key so subsequent publishes use a valid key.
        try:
            ss_listing_settings._listingagent_upsert_settings({'ebay_location_key': merchant_location_key})
        except Exception:
            pass

    inventory_item_payload = ss_ebay_policies._build_ebay_inventory_item_payload(
        upc,
        title,
        description,
        images,
        quantity,
        condition,
        aspects=aspects,
        epid=catalog_epid,
        condition_description=condition_description
    )
    offer_payload = ss_ebay_policies._build_ebay_offer_payload(
        sku, marketplace_id, currency, price, quantity, category_id, listing_description,
        merchant_location_key, fulfillment_policy_id, payment_policy_id, return_policy_id,
        listing_duration=listing_duration
    )

    if dry_run:
        return {
            'success': True,
            'dry_run': True,
            'inventoryItem': inventory_item_payload,
            'offer': offer_payload,
            'resolvedMerchantLocationKey': merchant_location_key,
            'locationAutoFixed': bool(location_autofixed),
            'availableLocationKeys': all_location_keys[:50]
        }

    condition_autofixed = False
    original_condition = (inventory_item_payload.get('condition') or '').strip()
    inventory_item_payload, condition_autofixed, original_condition, _condition_tried = ss_ebay_policies._listingagent_put_inventory_item_with_condition_fallback(
        sku,
        inventory_item_payload,
        category_id=category_id,
        marketplace_id=marketplace_id
    )

    resp_offer = ss_ebay_policies._ebay_api_request('POST', '/sell/inventory/v1/offer', payload=offer_payload)
    if resp_offer.status_code >= 400:
        err_text = ss_ebay_policies._ebay_extract_error(resp_offer)
        # Auto-recover once if configured location key became invalid in eBay.
        if 'location information not found' in err_text.lower():
            fresh_key, _fixed, keys = ss_ebay_policies._listingagent_resolve_ebay_location_key('', force_refresh=True)
            if fresh_key and fresh_key != str(offer_payload.get('merchantLocationKey') or '').strip():
                offer_payload_retry = dict(offer_payload)
                offer_payload_retry['merchantLocationKey'] = fresh_key
                resp_offer_retry = ss_ebay_policies._ebay_api_request('POST', '/sell/inventory/v1/offer', payload=offer_payload_retry)
                if resp_offer_retry.status_code < 400:
                    offer_payload = offer_payload_retry
                    try:
                        ss_listing_settings._listingagent_upsert_settings({'ebay_location_key': fresh_key})
                    except Exception:
                        pass
                    offer_data = resp_offer_retry.json() if resp_offer_retry.text else {}
                    return {
                        'success': True,
                        'offerId': offer_data.get('offerId'),
                        'inventoryItem': inventory_item_payload,
                        'offer': offer_payload,
                        'raw': offer_data,
                        'resolvedMerchantLocationKey': fresh_key,
                        'locationAutoFixed': True,
                        'availableLocationKeys': keys[:50]
                    }
                err_text = ss_ebay_policies._ebay_extract_error(resp_offer_retry)
        # If offer already exists for this SKU, reuse it.
        if 'offer entity already exists' in err_text.lower():
            existing = ss_ebay_policies._listingagent_find_existing_offer_for_sku(sku, marketplace_id=marketplace_id)
            if existing:
                existing_offer_id = (existing.get('offerId') or existing.get('offer_id') or '').strip()
                existing_offer_updated = False
                existing_location_autofixed = bool(location_autofixed)
                if existing_offer_id:
                    resp_put_existing = ss_ebay_policies._ebay_api_request('PUT', f'/sell/inventory/v1/offer/{existing_offer_id}', payload=offer_payload)
                    put_ok = resp_put_existing.status_code < 400
                    if not put_ok:
                        put_err = ss_ebay_policies._ebay_extract_error(resp_put_existing)
                        put_offer_payload = dict(offer_payload)
                        # Same stale-location recovery used for POST create path.
                        if 'location information not found' in put_err.lower():
                            fresh_key, _fixed, keys = ss_ebay_policies._listingagent_resolve_ebay_location_key('', force_refresh=True)
                            if fresh_key and fresh_key != str(offer_payload.get('merchantLocationKey') or '').strip():
                                put_offer_payload['merchantLocationKey'] = fresh_key
                                resp_put_retry = ss_ebay_policies._ebay_api_request('PUT', f'/sell/inventory/v1/offer/{existing_offer_id}', payload=put_offer_payload)
                                if resp_put_retry.status_code < 400:
                                    offer_payload = put_offer_payload
                                    existing_location_autofixed = True
                                    put_ok = True
                                    try:
                                        ss_listing_settings._listingagent_upsert_settings({'ebay_location_key': fresh_key})
                                    except Exception:
                                        pass
                                else:
                                    put_err = ss_ebay_policies._ebay_extract_error(resp_put_retry)
                        if not put_ok:
                            raise ss_errors._ListingAgentUserError(
                                f"Existing offer found for SKU, but updating it failed: {put_err}",
                                status_code=400,
                                extra={
                                    'offer': offer_payload,
                                    'resolvedMerchantLocationKey': merchant_location_key,
                                    'locationAutoFixed': existing_location_autofixed,
                                    'conditionAutoFixed': bool(condition_autofixed),
                                    'resolvedCondition': (inventory_item_payload.get('condition') or '').strip(),
                                    'originalCondition': original_condition,
                                    'missingAspect': ss_ebay_policies._extract_missing_ebay_aspect(put_err),
                                    'existingOfferId': existing_offer_id,
                                    'availableLocationKeys': all_location_keys[:50]
                                }
                            )
                    existing_offer_updated = True
                return {
                    'success': True,
                    'offerId': existing_offer_id or (existing.get('offerId') or existing.get('offer_id') or '').strip(),
                    'inventoryItem': inventory_item_payload,
                    'offer': offer_payload,
                    'raw': {'reusedExistingOffer': True, 'updatedExistingOffer': existing_offer_updated, 'existingOffer': existing},
                    'resolvedMerchantLocationKey': merchant_location_key,
                    'locationAutoFixed': existing_location_autofixed,
                    'conditionAutoFixed': bool(condition_autofixed),
                    'resolvedCondition': (inventory_item_payload.get('condition') or '').strip(),
                    'originalCondition': original_condition,
                    'availableLocationKeys': all_location_keys[:50]
                }
        missing_aspect = ss_ebay_policies._extract_missing_ebay_aspect(err_text)
        raise ss_errors._ListingAgentUserError(
            err_text,
            status_code=400,
            extra={
                'offer': offer_payload,
                'resolvedMerchantLocationKey': merchant_location_key,
                'locationAutoFixed': bool(location_autofixed),
                'conditionAutoFixed': bool(condition_autofixed),
                'resolvedCondition': (inventory_item_payload.get('condition') or '').strip(),
                'originalCondition': original_condition,
                'missingAspect': missing_aspect,
                'availableLocationKeys': all_location_keys[:50]
            }
        )

    offer_data = resp_offer.json() if resp_offer.text else {}
    return {
        'success': True,
        'offerId': offer_data.get('offerId'),
        'inventoryItem': inventory_item_payload,
        'offer': offer_payload,
        'raw': offer_data,
        'resolvedMerchantLocationKey': merchant_location_key,
        'locationAutoFixed': bool(location_autofixed),
        'conditionAutoFixed': bool(condition_autofixed),
        'resolvedCondition': (inventory_item_payload.get('condition') or '').strip(),
        'originalCondition': original_condition,
        'availableLocationKeys': all_location_keys[:50]
    }


_EBAY_STORE_POLICIES_HTML = """
<div style="font-family: Arial, Helvetica, sans-serif; font-size: 14px; line-height: 1.6; color: #222; max-width: 900px; margin: 0 auto;">
  <div style="border: 1px solid #ddd; padding: 24px; background: #fff;">
    <h2 style="margin: 0 0 20px; font-size: 24px; color: #111; border-bottom: 2px solid #111; padding-bottom: 10px;">Store Policies</h2>
    <h3 style="margin: 24px 0 10px; font-size: 18px; color: #111;">Payment Policy</h3>
    <p style="margin: 0 0 14px;">Payment is required within <strong>7 days</strong> of purchase or auction end. We follow <strong>eBay's Non-Paying Bidder Policies</strong>. By placing a bid or completing a purchase, the buyer agrees to all terms and policies listed on this page.</p>
    <p style="margin: 0 0 14px;">We ship <strong>only to the PayPal payment address provided at checkout</strong>. No exceptions.</p>
    <h3 style="margin: 24px 0 10px; font-size: 18px; color: #111;">Sales Terms</h3>
    <p style="margin: 0 0 14px;">Due to the nature of our merchandise, <strong>all sales are considered final</strong> unless an item is found to be <strong>materially not as described</strong>.</p>
    <h3 style="margin: 24px 0 10px; font-size: 18px; color: #111;">Shipping Policy</h3>
    <p style="margin: 0 0 14px;">Because many of our listings begin at very low starting prices, we <strong>do not offer combined shipping</strong>.</p>
    <h3 style="margin: 24px 0 10px; font-size: 18px; color: #111;">Return and Refund Policy</h3>
    <p style="margin: 0 0 14px;">We offer a <strong>30-day return policy</strong> only in cases where an item has been <strong>incorrectly described</strong> in the listing.</p>
    <p style="margin: 0 0 8px;"><strong>To qualify for a return:</strong></p>
    <ul style="margin: 0 0 14px 20px; padding: 0;">
      <li>The item must be returned in its <strong>original condition</strong></li>
      <li>All <strong>original packaging</strong> must be included, such as boxes, cartons, tags, and inserts</li>
      <li>The item must <strong>not</strong> have been cleaned, washed, or laundered</li>
      <li>The return must be <strong>securely packaged</strong> for shipment</li>
    </ul>
    <p style="margin: 0 0 14px;">Returns must be initiated within <strong>30 days of delivery</strong>.</p>
    <h3 style="margin: 24px 0 10px; font-size: 18px; color: #111;">Refund Terms</h3>
    <p style="margin: 0 0 14px;">For approved returns, we will provide a <strong>prepaid return shipping label</strong>. Once the item is received and inspected, the refund will be processed within <strong>5 business days</strong>.</p>
    <p style="margin: 0 0 8px;">If the returned item is found to be <strong>as described in the original listing</strong>, the refund may be reduced by:</p>
    <ul style="margin: 0 0 14px 20px; padding: 0;">
      <li>the original shipping cost</li>
      <li>the return shipping cost</li>
      <li>a <strong>20% restocking fee</strong></li>
    </ul>
    <p style="margin: 0 0 14px;">We may, at our sole discretion, accept returns that fall outside of our standard return policy. In such cases, a restocking fee may still apply.</p>
    <h3 style="margin: 24px 0 10px; font-size: 18px; color: #111;">Special Return Conditions</h3>
    <ul style="margin: 0 0 14px 20px; padding: 0;">
      <li><strong>Buyer's remorse returns:</strong> buyer is responsible for return shipping and a <strong>20% restocking fee</strong></li>
      <li><strong>Damaged or defective items:</strong> must be reported within <strong>48 hours of delivery</strong></li>
      <li><strong>Undeliverable packages or incorrect shipping addresses:</strong> subject to return shipping charges plus a <strong>20% restocking fee</strong></li>
    </ul>
    <h3 style="margin: 24px 0 10px; font-size: 18px; color: #111;">Display / Color Disclaimer</h3>
    <p style="margin: 0 0 14px;">We are not responsible for differences in color, tone, or appearance caused by individual monitor or screen settings.</p>
    <h3 style="margin: 24px 0 10px; font-size: 18px; color: #111;">Feedback and Customer Service</h3>
    <p style="margin: 0 0 14px;">We are committed to providing excellent service and earning <strong>5-star feedback</strong> from our customers.</p>
    <p style="margin: 0 0 14px;">If you are satisfied with your purchase, we would appreciate your positive feedback. If you experience any issue with your order, please contact us before leaving neutral or negative feedback. We will make every reasonable effort to resolve the matter promptly and professionally.</p>
    <p style="margin: 0 0 14px;">Once your order has shipped, tracking information will be provided.</p>
    <h3 style="margin: 24px 0 10px; font-size: 18px; color: #111;">Business Hours</h3>
    <p style="margin: 0 0 14px;">Please note that we are <strong>closed on weekends</strong>. Messages received outside business days will be answered as soon as possible during the week.</p>
    <h3 style="margin: 24px 0 10px; font-size: 18px; color: #111;">Item Condition Notice</h3>
    <p style="margin: 0 0 14px;">Many of our items are <strong>new major department store shelf pulls</strong>. As a result, they may show minor signs of handling from in-store display or customer inspection.</p>
    <p style="margin: 0 0 14px;">Any significant flaws, including stains, tears, or other defects, are disclosed in the listing and photographed to the best of our ability.</p>
    <p style="margin: 0 0 14px;">We provide detailed descriptions and multiple photos for every item. Buyers are encouraged to review <strong>all listing photos and the full description carefully</strong> before purchasing.</p>
    <p style="margin: 0 0 14px;">All items are inspected prior to listing for presentation and accuracy.</p>
    <h3 style="margin: 24px 0 10px; font-size: 18px; color: #111;">Authenticity Guarantee</h3>
    <p style="margin: 0 0 14px;">The authenticity of our merchandise is <strong>100% guaranteed</strong>.</p>
    <p style="margin: 0 0 14px;">Our high-end and designer brand items are carefully inspected, and we make every effort to describe them accurately and honestly.</p>
    <h3 style="margin: 24px 0 10px; font-size: 18px; color: #111;">Product Sourcing</h3>
    <p style="margin: 0;">Our inventory is acquired through <strong>closeout purchases from major department stores across the United States</strong>.</p>
  </div>
</div>
"""


def api_listingagent_ebay_generate_description():
    """Generate an AI-powered listing description using Claude API (Anthropic) if configured, else template."""
    try:
        data = request.get_json(force=True) or {}
        title = (data.get('listing_title') or data.get('title') or '').strip()
        system_title = (data.get('system_title') or '').strip()
        category_id = (data.get('category_id') or data.get('categoryId') or '').strip()
        aspects = data.get('aspects') or {}
        upc = (data.get('upc') or '').strip()

        if not title:
            raise ss_errors._ListingAgentUserError('title is required', status_code=400)

        anthropic_key = os.getenv('ANTHROPIC_API_KEY', '').strip()
        if not anthropic_key:
            raise ss_errors._ListingAgentUserError('ANTHROPIC_API_KEY not found in environment — check .env on the Pi', status_code=503)

        if anthropic_key:
            # Build a concise prompt from available product data
            aspect_lines = '\n'.join(f'- {k}: {v}' for k, v in aspects.items() if k and v) if aspects else ''
            prompt = (
                f"Write a compelling eBay listing description for the following product. "
                f"Use plain HTML (h2, p, ul/li only). Be concise, highlight key features and condition. "
                f"Do not include price. Do not mention eBay by name.\n\n"
                f"Listing title: {title}\n"
                + (f"Inventory system title: {system_title}\n" if system_title else "")
                + (f"Category ID: {category_id}\n" if category_id else "")
                + (f"UPC: {upc}\n" if upc else "")
                + (f"Item specifics:\n{aspect_lines}\n" if aspect_lines else "")
            )
            resp = requests.post(
                'https://api.anthropic.com/v1/messages',
                headers={
                    'x-api-key': anthropic_key,
                    'anthropic-version': '2023-06-01',
                    'content-type': 'application/json',
                },
                json={
                    'model': 'claude-haiku-4-5-20251001',
                    'max_tokens': 1024,
                    'messages': [{'role': 'user', 'content': prompt}],
                },
                timeout=30,
            )
            if resp.status_code >= 400:
                try:
                    err_body = resp.json()
                    err_msg = (err_body.get('error') or {}).get('message') or resp.text[:300]
                except Exception:
                    err_msg = resp.text[:300]
                raise ss_errors._ListingAgentUserError(f'Anthropic API error ({resp.status_code}): {err_msg}', status_code=502)
            result = resp.json()
            description = (result.get('content') or [{}])[0].get('text', '').strip()
            if not description:
                raise ss_errors._ListingAgentUserError(f'Anthropic API returned empty response. Raw: {str(result)[:200]}', status_code=502)
            # Strip markdown code fences Claude sometimes adds
            import re as _re
            description = _re.sub(r'^```[^\n]*\n?', '', description).rstrip('`').strip()
            return jsonify({'success': True, 'description': description + _EBAY_STORE_POLICIES_HTML, 'source': 'claude'})

        # Fallback: structured template description
        aspect_html = ''
        if aspects:
            rows = ''.join(f'<li><strong>{k}:</strong> {v}</li>' for k, v in aspects.items() if k and v)
            if rows:
                aspect_html = f'<ul>{rows}</ul>'
        description = (
            f'<h2>{title}</h2>'
            f'<p>You are purchasing a <strong>{title}</strong>. '
            f'This item ships fast and is carefully packaged.</p>'
            + (f'<h3>Item Details</h3>{aspect_html}' if aspect_html else '')
            + (f'<p><strong>UPC:</strong> {upc}</p>' if upc else '')
            + '<p>Please message us with any questions before purchasing. Thank you!</p>'
        )
        return jsonify({'success': True, 'description': description + _EBAY_STORE_POLICIES_HTML, 'source': 'template'})

    except ss_errors._ListingAgentUserError as e:
        # Always return 200 so Cloudflare tunnel doesn't swallow the response body
        return jsonify({'success': False, 'error': str(e)})
    except Exception as e:
        return jsonify({'success': False, 'error': ss_errors._safe_error(e, 'listingagent:ebay_generate_description')})


def api_listingagent_ebay_generate_title():
    """Generate an AI-powered listing title using Claude API (Anthropic) if configured, else template."""
    try:
        data = request.get_json(force=True) or {}
        form_title = (data.get('title') or '').strip()
        system_title = (data.get('system_title') or '').strip()
        title = (system_title or form_title).strip()
        category_id = (data.get('category_id') or data.get('categoryId') or '').strip()
        aspects = data.get('aspects') or {}
        upc = (data.get('upc') or '').strip()

        if not title:
            raise ss_errors._ListingAgentUserError('title is required', status_code=400)

        anthropic_key = os.getenv('ANTHROPIC_API_KEY', '').strip()
        if not anthropic_key:
            raise ss_errors._ListingAgentUserError('ANTHROPIC_API_KEY not found in environment — check .env on the Pi', status_code=503)

        if anthropic_key:
            aspect_lines = '\n'.join(f'- {k}: {v}' for k, v in aspects.items() if k and v) if aspects else ''
            prompt = (
                "Write a concise eBay listing title for the following product.\n"
                "Rules:\n"
                "- Return only the title text.\n"
                "- Keep it under 80 characters if possible.\n"
                "- Do not use quotes, bullets, or explanations.\n"
                "- Include the most useful search terms first.\n"
                "- Prefer brand, model, size, and key identifiers.\n\n"
                f"Inventory system title: {title}\n"
                + (f"Current form title: {form_title}\n" if form_title and form_title != title else "")
                + (f"Category ID: {category_id}\n" if category_id else "")
                + (f"UPC: {upc}\n" if upc else "")
                + (f"Item specifics:\n{aspect_lines}\n" if aspect_lines else "")
            )
            resp = requests.post(
                'https://api.anthropic.com/v1/messages',
                headers={
                    'x-api-key': anthropic_key,
                    'anthropic-version': '2023-06-01',
                    'content-type': 'application/json',
                },
                json={
                    'model': 'claude-haiku-4-5-20251001',
                    'max_tokens': 128,
                    'messages': [{'role': 'user', 'content': prompt}],
                },
                timeout=30,
            )
            if resp.status_code >= 400:
                try:
                    err_body = resp.json()
                    err_msg = (err_body.get('error') or {}).get('message') or resp.text[:300]
                except Exception:
                    err_msg = resp.text[:300]
                raise ss_errors._ListingAgentUserError(f'Anthropic API error ({resp.status_code}): {err_msg}', status_code=502)
            result = resp.json()
            out_text = (result.get('content') or [{}])[0].get('text', '').strip()
            if not out_text:
                raise ss_errors._ListingAgentUserError(f'Anthropic API returned empty response. Raw: {str(result)[:200]}', status_code=502)
            import re as _re
            out_text = _re.sub(r'^```[^\n]*\n?', '', out_text).rstrip('`').strip()
            out_text = ' '.join(out_text.split())
            out_text = out_text[:80].strip()
            return jsonify({'success': True, 'title': out_text, 'source': 'claude'})

        # Fallback: trim the existing title and add a compact keyword order.
        fallback = ' '.join((title or upc or '').split())
        if aspects:
            brand = str(aspects.get('Brand') or aspects.get('brand') or '').strip()
            model = str(aspects.get('Model') or aspects.get('model') or '').strip()
            color = str(aspects.get('Color') or aspects.get('color') or '').strip()
            size = str(aspects.get('Size') or aspects.get('size') or '').strip()
            parts = [brand, model, color, size, fallback]
            fallback = ' '.join(p for p in parts if p)
        fallback = fallback[:80].strip()
        return jsonify({'success': True, 'title': fallback, 'source': 'template'})

    except ss_errors._ListingAgentUserError as e:
        return jsonify({'success': False, 'error': str(e)})
    except Exception as e:
        return jsonify({'success': False, 'error': ss_errors._safe_error(e, 'listingagent:ebay_generate_title')})


def api_listingagent_ebay_draft():
    """Create/replace an inventory item and create a draft offer (not published)."""
    try:
        data = request.json or {}
        dry_run = bool(data.get('dry_run', False))
        result = _listingagent_ebay_create_draft_offer(data, dry_run=dry_run)
        return jsonify(result)
    except ss_errors._ListingAgentUserError as e:
        payload = {'success': False, 'error': str(e)}
        payload.update(e.extra or {})
        return jsonify(payload), e.status_code
    except Exception as e:
        return jsonify({'success': False, 'error': ss_errors._safe_error(e, 'listingagent:ebay_draft')}), 500


def api_listingagent_ebay_publish():
    """Publish an offer (creates inventory item + offer if needed)."""
    try:
        data = request.json or {}
        if data.get('mappingReferenceId'):
            try:
                return jsonify(_publish_mapped_ebay_listing(data))
            except ebay_mapping.MappingError as exc:
                return jsonify(success=False, error=str(exc)), 400
        settings = ss_listing_settings._listingagent_get_settings()
        dry_run = bool(data.get('dry_run', False))

        offer_id = (data.get('offerId') or '').strip()
 
        inventory_payload = None
        offer_payload = None
        # If an offerId was provided, treat this as an edit/revise flow:
        # update inventory item + offer (if we have full fields) and then publish to apply changes.
        if offer_id:
            wants_update = bool(data.get('updateExisting', True))
            upc_for_update = (data.get('upc') or '').strip()
            if wants_update and upc_for_update:
                required = ['upc', 'sku', 'title', 'listingDescription', 'price', 'quantity', 'categoryId',
                            'fulfillmentPolicyId', 'paymentPolicyId', 'returnPolicyId']
                missing = [k for k in required if not str(data.get(k) or '').strip()]
                if missing and not dry_run:
                    return jsonify({'success': False, 'error': f"Missing required fields to update: {', '.join(missing)}"}), 400
  
                # Reuse the draft builder to generate payloads without creating a new offer.
                preview = _listingagent_ebay_create_draft_offer(data, dry_run=True)
                inventory_payload = preview.get('inventoryItem')
                offer_payload = preview.get('offer')
  
                if dry_run:
                    return jsonify({'success': True, 'dry_run': True, 'offerId': offer_id, 'inventoryItem': inventory_payload, 'offer': offer_payload, 'wouldPublish': True, 'wouldUpdate': True})
  
                sku_local = (data.get('sku') or upc_for_update).strip()
                category_for_update = (data.get('categoryId') or (offer_payload or {}).get('categoryId') or '').strip()
                marketplace_for_update = (data.get('marketplaceId') or (offer_payload or {}).get('marketplaceId') or settings.get('ebay_marketplace_id') or 'EBAY_US').strip()
                try:
                    inventory_payload, cond_fixed, orig_cond, _tried = ss_ebay_policies._listingagent_put_inventory_item_with_condition_fallback(
                        sku_local,
                        inventory_payload or {},
                        category_id=category_for_update,
                        marketplace_id=marketplace_for_update
                    )
                except ss_errors._ListingAgentUserError as e:
                    payload = {'success': False, 'error': str(e)}
                    payload.update(e.extra or {})
                    return jsonify(payload), e.status_code
 
                resp_offer = ss_ebay_policies._ebay_api_request('PUT', f'/sell/inventory/v1/offer/{offer_id}', payload=offer_payload)
                if resp_offer.status_code >= 400:
                    return jsonify({'success': False, 'error': ss_ebay_policies._ebay_extract_error(resp_offer), 'offer': offer_payload}), 400
  
        if not offer_id:
            # Enforce publish-required fields (unless dry_run)
            required = ['upc', 'sku', 'title', 'listingDescription', 'price', 'quantity', 'categoryId',
                        'fulfillmentPolicyId', 'paymentPolicyId', 'returnPolicyId']
            missing = [k for k in required if not str(data.get(k) or '').strip()]
            if missing and not dry_run:
                return jsonify({'success': False, 'error': f"Missing required fields: {', '.join(missing)}"}), 400

            draft_result = _listingagent_ebay_create_draft_offer(data, dry_run=dry_run)
            if dry_run:
                # Tell the UI what would happen next
                return jsonify({**draft_result, 'wouldPublish': True})

            offer_id = (draft_result.get('offerId') or '').strip()
            inventory_payload = draft_result.get('inventoryItem')
            offer_payload = draft_result.get('offer')
            if not offer_id:
                return jsonify({'success': False, 'error': 'Draft offer created but no offerId returned'}), 500

        if dry_run:
            return jsonify({'success': True, 'dry_run': True, 'offerId': offer_id, 'inventoryItem': inventory_payload, 'offer': offer_payload, 'wouldPublish': True})

        def _finish_ebay_publish_success(*, listing_id=None, pub_data=None, extra=None):
            upc = (data.get('upc') or '').strip()
            target_lot = ss_normalization._normalize_lot_number(data.get('lot_number') or data.get('lot'))
            target_item_id = data.get('id')
            if target_item_id is None:
                target_item_id = data.get('item_id')
            if target_item_id is None:
                target_item_id = data.get('bol_id')
            if upc or target_item_id is not None:
                try:
                    ss_listing_lifecycle._listing_center_mark_bol_listed(
                        'ebay',
                        upc=upc,
                        lot_number=target_lot,
                        item_id=target_item_id
                    )
                except Exception:
                    pass

            try:
                if upc:
                    sku_local = (data.get('sku') or upc).strip() or None
                    try:
                        settings_local = ss_listing_settings._listingagent_get_settings()
                        marketplace_id_local = (data.get('marketplaceId') or settings_local.get('ebay_marketplace_id') or 'EBAY_US').strip() or 'EBAY_US'
                    except Exception:
                        marketplace_id_local = (data.get('marketplaceId') or 'EBAY_US').strip() or 'EBAY_US'
                    title_local = (data.get('title') or '').strip() or None
                    qty_local = ss_listing_settings._listingagent_parse_int(data.get('quantity'), None)
                    price_local = ss_listing_settings._listingagent_parse_float(data.get('price'), None)
                    listing_url_local = f"https://www.ebay.com/itm/{listing_id}" if str(listing_id or '').strip() else None
                    ss_listing_queue._listagent_mark_listed(
                        upc,
                        platform='ebay',
                        listing_id=listing_id,
                        offer_id=offer_id,
                        sku=sku_local,
                        marketplace_id=marketplace_id_local,
                        title=title_local,
                        price=price_local,
                        quantity=qty_local,
                        url=listing_url_local,
                        source='listingagent'
                    )
            except Exception:
                pass

            payload = {'success': True, 'listingId': listing_id, 'offerId': offer_id, 'raw': pub_data or {}}
            if isinstance(extra, dict):
                payload.update(extra)
            return jsonify(payload)

        resp_pub = ss_ebay_policies._ebay_api_request('POST', f'/sell/inventory/v1/offer/{offer_id}/publish')
        if resp_pub.status_code >= 400:
            err_pub = ss_ebay_policies._ebay_extract_error(resp_pub)
            low = err_pub.lower()
            # Publish-time mixed image source recovery:
            # eBay can reject at publish even if the prior inventory PUT accepted the payload.
            if ss_ebay_policies._listingagent_is_likely_ebay_image_mix_error(err_pub) and offer_id:
                try:
                    from urllib.parse import quote

                    sku_local = (data.get('sku') or (offer_payload or {}).get('sku') or data.get('upc') or '').strip()
                    if not sku_local:
                        # Resolve SKU from the offer when client payload omitted it.
                        resp_offer_state = ss_ebay_policies._ebay_api_request('GET', f'/sell/inventory/v1/offer/{offer_id}')
                        if resp_offer_state.status_code < 400:
                            offer_state = resp_offer_state.json() if resp_offer_state.text else {}
                            sku_local = (offer_state.get('sku') or '').strip()

                    if sku_local:
                        sku_encoded = quote(sku_local, safe='')
                        inv_payload = dict(inventory_payload or {})
                        if not inv_payload:
                            # No local copy (e.g. offerId publish without update path); fetch inventory item.
                            resp_inv_get = ss_ebay_policies._ebay_api_request('GET', f'/sell/inventory/v1/inventory_item/{sku_encoded}')
                            if resp_inv_get.status_code < 400:
                                inv_payload = resp_inv_get.json() if resp_inv_get.text else {}

                        product_obj = inv_payload.get('product') if isinstance(inv_payload.get('product'), dict) else {}
                        image_urls = ss_ebay_policies._listingagent_dedupe_image_urls((product_obj or {}).get('imageUrls') or [])
                        if image_urls and ss_ebay_policies._listingagent_has_mixed_ebay_image_sources(image_urls):
                            fallback_images = ss_ebay_policies._listingagent_prefer_single_ebay_image_source(image_urls)[:12]
                            if fallback_images and fallback_images != image_urls:
                                retry_inv_payload = dict(inv_payload)
                                product_retry = dict(product_obj or {})
                                product_retry['imageUrls'] = fallback_images
                                retry_inv_payload['product'] = product_retry
                                resp_item_retry = ss_ebay_policies._ebay_api_request(
                                    'PUT',
                                    f'/sell/inventory/v1/inventory_item/{sku_encoded}',
                                    payload=retry_inv_payload
                                )
                                if resp_item_retry.status_code < 400:
                                    inventory_payload = retry_inv_payload
                                    resp_pub_retry = ss_ebay_policies._ebay_api_request('POST', f'/sell/inventory/v1/offer/{offer_id}/publish')
                                    if resp_pub_retry.status_code < 400:
                                        pub_data = resp_pub_retry.json() if resp_pub_retry.text else {}
                                        listing_id = pub_data.get('listingId')
                                        return _finish_ebay_publish_success(
                                            listing_id=listing_id,
                                            pub_data=pub_data,
                                            extra={
                                                'imageSourceAutoFixed': True,
                                                'imageCount': len(fallback_images)
                                            }
                                        )
                                    err_pub = ss_ebay_policies._ebay_extract_error(resp_pub_retry)
                                    low = err_pub.lower()
                                else:
                                    err_pub = ss_ebay_policies._ebay_extract_error(resp_item_retry)
                                    low = err_pub.lower()
                except Exception:
                    pass
            # Publish-time condition/category mismatch recovery:
            # try fallback condition updates even when initial inventory PUT succeeded.
            if (('invalid item condition information' in low) or ('condition id is invalid' in low)) and offer_id and inventory_payload:
                tried_conditions = []
                try:
                    sku_local = (data.get('sku') or (offer_payload or {}).get('sku') or data.get('upc') or '').strip()
                    category_for_recovery = (data.get('categoryId') or (offer_payload or {}).get('categoryId') or '').strip()
                    marketplace_for_recovery = (data.get('marketplaceId') or (offer_payload or {}).get('marketplaceId') or settings.get('ebay_marketplace_id') or 'EBAY_US').strip()
                    if sku_local:
                        inv_payload = dict(inventory_payload or {})
                        from urllib.parse import quote
                        sku_encoded = quote(sku_local, safe='')

                        # First, run the standard helper once.
                        inv_payload, _cond_fixed2, _orig_cond2, _tried2 = ss_ebay_policies._listingagent_put_inventory_item_with_condition_fallback(
                            sku_local,
                            inv_payload,
                            category_id=category_for_recovery,
                            marketplace_id=marketplace_for_recovery
                        )
                        if isinstance(_tried2, list):
                            tried_conditions.extend([c for c in _tried2 if c])
                        resp_pub_retry = ss_ebay_policies._ebay_api_request('POST', f'/sell/inventory/v1/offer/{offer_id}/publish')
                        if resp_pub_retry.status_code < 400:
                            pub_data = resp_pub_retry.json() if resp_pub_retry.text else {}
                            listing_id = pub_data.get('listingId')
                            return _finish_ebay_publish_success(
                                listing_id=listing_id,
                                pub_data=pub_data,
                                extra={
                                    'conditionAutoFixed': True,
                                    'resolvedCondition': (inv_payload.get('condition') or '').strip(),
                                    'conditionTried': tried_conditions
                                }
                            )
                        err_pub = ss_ebay_policies._ebay_extract_error(resp_pub_retry)
                        low = err_pub.lower()

                        # If still condition-related, force a condition sweep.
                        if ('invalid item condition information' in low) or ('condition id is invalid' in low):
                            fallback_conditions = ss_ebay_policies._listingagent_get_condition_candidates(
                                inv_payload.get('condition') or '',
                                category_id=category_for_recovery,
                                marketplace_id=marketplace_for_recovery
                            )
                            current = (inv_payload.get('condition') or '').strip().upper()
                            for cond in fallback_conditions:
                                if cond == current:
                                    continue
                                if cond not in tried_conditions:
                                    tried_conditions.append(cond)
                                retry_payload = dict(inv_payload)
                                retry_payload['condition'] = cond
                                resp_item_retry = ss_ebay_policies._ebay_api_request('PUT', f'/sell/inventory/v1/inventory_item/{sku_encoded}', payload=retry_payload)
                                if resp_item_retry.status_code >= 400:
                                    continue
                                inv_payload = retry_payload
                                resp_pub_retry2 = ss_ebay_policies._ebay_api_request('POST', f'/sell/inventory/v1/offer/{offer_id}/publish')
                                if resp_pub_retry2.status_code < 400:
                                    pub_data = resp_pub_retry2.json() if resp_pub_retry2.text else {}
                                    listing_id = pub_data.get('listingId')
                                    return _finish_ebay_publish_success(
                                        listing_id=listing_id,
                                        pub_data=pub_data,
                                        extra={
                                            'conditionAutoFixed': True,
                                            'resolvedCondition': cond,
                                            'conditionTried': tried_conditions
                                        }
                                    )
                                err_pub = ss_ebay_policies._ebay_extract_error(resp_pub_retry2)
                                low = err_pub.lower()
                                if ('invalid item condition information' not in low) and ('condition id is invalid' not in low):
                                    break
                except ss_errors._ListingAgentUserError:
                    pass
            # Treat idempotent "already published" style responses as success.
            if ('already published' in low) or ('already listed' in low):
                listing_id = None
                try:
                    resp_offer_state = ss_ebay_policies._ebay_api_request('GET', f'/sell/inventory/v1/offer/{offer_id}')
                    if resp_offer_state.status_code < 400:
                        offer_state = resp_offer_state.json() if resp_offer_state.text else {}
                        listing_id = offer_state.get('listingId') or offer_state.get('listing_id')
                except Exception:
                    pass
                return _finish_ebay_publish_success(
                    listing_id=listing_id,
                    pub_data={'alreadyPublished': True, 'offerId': offer_id}
                )
            return jsonify({'success': False, 'error': err_pub, 'missingAspect': ss_ebay_policies._extract_missing_ebay_aspect(err_pub)}), 400

        pub_data = resp_pub.json() if resp_pub.text else {}
        listing_id = pub_data.get('listingId')
        return _finish_ebay_publish_success(listing_id=listing_id, pub_data=pub_data)
    except ss_errors._ListingAgentUserError as e:
        payload = {'success': False, 'error': str(e)}
        payload.update(e.extra or {})
        return jsonify(payload), e.status_code
    except Exception as e:
        return jsonify({'success': False, 'error': ss_errors._safe_error(e, 'listingagent:ebay_publish')}), 500
