"""User-reviewed Amazon brand corrections; never remap ASINs or shipments."""
from contextlib import contextmanager
import hashlib
import json
import re
import sqlite3
from datetime import datetime, timezone
from flask import abort, jsonify, render_template, request


@contextmanager
def database(path, timeout=30):
    conn = sqlite3.connect(str(path), timeout=timeout)
    try:
        with conn:
            yield conn
    finally:
        conn.close()


def catalog_card(payload, marketplace):
    attrs = payload.get('attributes') or {}
    summary = next((s for s in payload.get('summaries', []) if s.get('marketplaceId') == marketplace), {})
    def value(key):
        return next((str(x.get('value', '')) for x in attrs.get(key, []) if x.get('marketplace_id', marketplace) == marketplace), '')
    images = [i for group in payload.get('images', []) if group.get('marketplaceId') == marketplace for i in group.get('images', []) if i.get('variant') == 'MAIN']
    image = max(images, key=lambda i: i.get('height', 0), default={}).get('link', '')
    if not image.startswith('https://'):
        image = ''
    return dict(asin=payload['asin'], title=summary.get('itemName') or value('item_name'), brand=value('brand') or summary.get('brand', ''), size=value('size') or summary.get('size', ''), color=value('color') or summary.get('color', ''), image=image)


def related_asins(payload, marketplace, key):
    return [a for group in payload.get('relationships', []) if group.get('marketplaceId') == marketplace for rel in group.get('relationships', []) for a in rel.get(key, []) if re.fullmatch(r'[A-Z0-9]{10}', a)]


def register(app, base_dir, context):
    def item(session_id, barcode):
        with database(str(base_dir / 'searchRack.db')) as conn:
            row = conn.execute('SELECT items_json,rejected_items_json FROM fba_prep_sessions WHERE id=?', (session_id,)).fetchone()
        if not row:
            abort(404, 'Session not found')
        matches = [x for text in row for x in json.loads(text or '[]') if str(x.get('barcode', '')) == barcode]
        if not matches:
            abort(404, 'Item not found in this session')
        return matches[0]

    def clients():
        from sp_api.api import CatalogItems, ListingsItems
        credentials, seller, market, marketplace = context()
        return CatalogItems(credentials=credentials, marketplace=marketplace, version='2022-04-01'), ListingsItems(credentials=credentials, marketplace=marketplace), seller, market

    def inspect(row):
        ca, li, seller, market = clients()
        asin = str(row.get('asin') or row.get('fba', {}).get('amazon_listing', {}).get('asin') or '')
        sku = str(row.get('seller_sku') or '')
        if not re.fullmatch(r'[A-Z0-9]{10}', asin):
            abort(400, 'This item needs an Amazon catalog match first.')
        current = ca.get_catalog_item(asin, marketplaceIds=[market], includedData=['attributes', 'summaries', 'images', 'relationships']).payload
        listing = li.get_listings_item(seller, sku, marketplaceIds=[market], includedData=['summaries', 'attributes', 'issues']).payload if sku else {}
        if any(s.get('asin') and s['asin'] != asin for s in listing.get('summaries', [])):
            abort(409, 'Seller SKU belongs to another ASIN. Resolve its mapping first.')
        reasons = ' '.join(i.get('message', '') for i in listing.get('issues', [])) + ' ' + str(row.get('fba_enablement_error') or '')
        parents = list(dict.fromkeys(related_asins(current, market, 'parentAsins') + re.findall(r'parent ASIN ([A-Z0-9]{10})', reasons)))
        family = {asin: current}
        children = []
        for parent in parents[:5]:
            p = ca.get_catalog_item(parent, marketplaceIds=[market], includedData=['attributes', 'summaries', 'images', 'relationships']).payload
            family[parent] = p
            children.extend(related_asins(p, market, 'childAsins'))
        members = list(dict.fromkeys([asin] + parents[:5] + children))
        fingerprint = hashlib.sha256(json.dumps([sku, asin, listing.get('attributes', {}).get('brand'), members], sort_keys=True).encode()).hexdigest()
        return ca, li, seller, market, listing, family, members, fingerprint

    @app.get('/fba-prep/sessions/<int:session_id>/resolve')
    def amazon_resolution_page(session_id):
        barcode = request.args.get('barcode', '')
        row = item(session_id, barcode)
        return render_template('amazon_resolution.html', session_id=session_id, barcode=barcode, item_title=row.get('title', 'Amazon listing'))

    @app.get('/api/fba-prep/sessions/<int:session_id>/resolution')
    def amazon_resolution_read(session_id):
        row = item(session_id, request.args.get('barcode', ''))
        try:
            ca, li, seller, market, listing, family, members, fingerprint = inspect(row)
            offset = max(0, int(request.args.get('offset', 0)))
            cards, failures = [], []
            for asin in members[offset:offset + 8]:
                try:
                    p = family.get(asin) or ca.get_catalog_item(asin, marketplaceIds=[market], includedData=['attributes', 'summaries', 'images']).payload
                    cards.append(catalog_card(p, market))
                except Exception:
                    failures.append(asin)
            current = catalog_card(next(iter(family.values())), market)
            issues = listing.get('issues', [])
            return jsonify(success=True, current=current, sku=row.get('seller_sku', ''), issues=issues, saved_reason=row.get('fba_enablement_error', ''), cards=cards, failed_asins=failures, total=len(members), next_offset=offset + 8 if offset + 8 < len(members) else None, fingerprint=fingerprint,
                           brand_issue=any(i.get('code') == '100898' or 'brand' in i.get('attributeNames', []) for i in issues))
        except Exception as exc:
            if hasattr(exc, 'code') and isinstance(exc.code, int):
                raise
            app.logger.warning('Amazon resolution lookup failed: %s', type(exc).__name__)
            return jsonify(success=False, error='Amazon comparison could not load. Retry shortly; no listing was changed.'), 502

    @app.post('/api/fba-prep/sessions/<int:session_id>/resolution')
    def amazon_resolution_submit(session_id):
        data = request.get_json(silent=True) or {}
        if data.get('confirm_packaging') is not True:
            return jsonify(success=False, error='Confirm that the selected brand matches the product packaging.'), 400
        row = item(session_id, str(data.get('barcode', '')))
        try:
            ca, li, seller, market, listing, family, members, fingerprint = inspect(row)
            if data.get('fingerprint') != fingerprint:
                return jsonify(success=False, error='The listing changed. Refresh the comparison before submitting.'), 409
            chosen = str(data.get('selected_asin', ''))
            if chosen not in members:
                return jsonify(success=False, error='Choose a product from this variation family.'), 400
            if not any(i.get('code') == '100898' or 'brand' in i.get('attributeNames', []) for i in listing.get('issues', [])):
                return jsonify(success=False, error='Amazon no longer reports a brand error. Refresh its status.'), 409
            p = family.get(chosen) or ca.get_catalog_item(chosen, marketplaceIds=[market], includedData=['attributes', 'summaries']).payload
            brand = catalog_card(p, market)['brand']
            if data.get('selected_brand') != brand:
                return jsonify(success=False, error='The selected catalog brand changed. Refresh the comparison.'), 409
            product_type = next((s.get('productType') for s in listing.get('summaries', []) if s.get('productType')), '')
            if not brand or not product_type or not row.get('seller_sku'):
                return jsonify(success=False, error='Amazon has not supplied enough listing data. Complete this correction in Seller Central.'), 409
            body = {'productType': product_type, 'patches': [{'op': 'add', 'path': '/attributes/brand', 'value': [{'value': brand, 'language_tag': 'en_US', 'marketplace_id': market}]}]}
            # Persist the attempt before the network request; uncertain outcomes must not be resubmitted blindly.
            with database(str(base_dir / 'searchRack.db'), timeout=30) as conn:
                conn.execute('CREATE TABLE IF NOT EXISTS amazon_resolution_attempts (id INTEGER PRIMARY KEY, session_id INTEGER, sku TEXT, fingerprint TEXT, brand TEXT, request_json TEXT, response_json TEXT, created_at TEXT)')
                conn.execute('BEGIN IMMEDIATE')
                existing = conn.execute('SELECT response_json FROM amazon_resolution_attempts WHERE sku=? AND fingerprint=? AND brand=? ORDER BY id DESC LIMIT 1', (row['seller_sku'], fingerprint, brand)).fetchone()
                if existing:
                    return jsonify(success=True, already_submitted=True, response=json.loads(existing[0]) if existing[0] else None, message='This correction was already submitted or is awaiting a response. Refresh Amazon status before taking further action.')
                cursor = conn.execute('INSERT INTO amazon_resolution_attempts (session_id,sku,fingerprint,brand,request_json,created_at) VALUES (?,?,?,?,?,?)', (session_id, row['seller_sku'], fingerprint, brand, json.dumps(body), datetime.now(timezone.utc).isoformat()))
                attempt = cursor.lastrowid
            response = li.patch_listings_item(seller, row['seller_sku'], marketplaceIds=[market], body=body).payload
            with database(str(base_dir / 'searchRack.db'), timeout=30) as conn:
                conn.execute('UPDATE amazon_resolution_attempts SET response_json=? WHERE id=?', (json.dumps(response), attempt))
            accepted = response.get('status') == 'ACCEPTED'
            return jsonify(success=True, accepted=accepted, response=response, message='Submitted to Amazon for processing. Refresh to check whether the brand error clears. Shipment eligibility is unchanged.' if accepted else 'Amazon did not accept the correction. Review the returned issues; a catalog support case may be needed.')
        except Exception as exc:
            if hasattr(exc, 'code') and isinstance(exc.code, int):
                raise
            app.logger.warning('Amazon resolution submission failed: %s', type(exc).__name__)
            return jsonify(success=False, error='The correction could not be confirmed. Refresh Amazon status before retrying.'), 502
