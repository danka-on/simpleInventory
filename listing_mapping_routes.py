"""Flask integration for native eBay recommendations and referenced publishing."""
import json
import os
import time
from urllib.parse import quote
from flask import request, jsonify
import ebay_mapping as mapping


def register(app, *, db_connection, get_token, post, build_draft, ebay_request,
             mark_listed, mark_bol, safe_error):
    def tables(conn):
        conn.execute('''CREATE TABLE IF NOT EXISTS listing_mapping_tasks (
            task_id TEXT PRIMARY KEY, upc TEXT NOT NULL, fingerprint TEXT NOT NULL,
            result TEXT, created REAL NOT NULL)''')
        conn.execute('''CREATE TABLE IF NOT EXISTS listing_mapping_publications (
            sku TEXT PRIMARY KEY, upc TEXT NOT NULL, request_id TEXT NOT NULL,
            status TEXT NOT NULL, result TEXT, updated REAL NOT NULL)''')

    def failure(exc):
        if isinstance(exc, mapping.MappingError):
            return jsonify(success=False, error=str(exc)), 400
        return jsonify(success=False, error=safe_error(exc, 'listingagent:ebay_mapping')), 502

    @app.route('/api/listingagent/ebay/mapping/start', methods=['POST'])
    def mapping_start():
        try:
            data = request.get_json() or {}
            upc = str(data.get('upc') or '').strip()
            if not upc:
                raise mapping.MappingError('Choose an item first.')
            if data.get('offerId'):
                raise mapping.MappingError('Native recommendations support new listings. Use similar-listing search to update this existing offer.')
            product, skipped = mapping.product_input(data)
            digest = mapping.fingerprint(product)
            with db_connection('listagent.db') as conn:
                tables(conn)
                row = conn.execute('SELECT task_id,result FROM listing_mapping_tasks WHERE upc=? AND fingerprint=? AND created>? ORDER BY created DESC LIMIT 1',
                                   (upc, digest, time.time()-3600)).fetchone()
            if row and row[1] and json.loads(row[1]).get('retryable') and json.loads(row[1]).get('status') == 'no_match':
                row = None
            if row:
                task_id = row[0]
            else:
                task_id = mapping.start(post, get_token(), product)
                with db_connection('listagent.db') as conn:
                    tables(conn)
                    conn.execute('INSERT OR IGNORE INTO listing_mapping_tasks VALUES (?,?,?,?,?)',
                                 (task_id, upc, digest, None, time.time()))
            return jsonify(success=True, taskId=task_id, skippedImages=skipped,
                           publishingConnected=bool(os.getenv('EBAY_OLDAUTH_TOKEN')))
        except Exception as exc:
            return failure(exc)

    @app.route('/api/listingagent/ebay/mapping/task', methods=['GET'])
    def mapping_task():
        try:
            upc, task_id = request.args.get('upc', ''), request.args.get('taskId', '')
            with db_connection('listagent.db') as conn:
                tables(conn)
                row = conn.execute('SELECT result FROM listing_mapping_tasks WHERE task_id=? AND upc=?', (task_id, upc)).fetchone()
            if row is None:
                raise mapping.MappingError('This recommendation task does not belong to the selected item. Request fresh recommendations.')
            result = json.loads(row[0]) if row[0] else mapping.poll(post, get_token(), task_id)
            if result['status'] != 'processing' and not row[0]:
                with db_connection('listagent.db') as conn:
                    conn.execute('UPDATE listing_mapping_tasks SET result=? WHERE task_id=?', (json.dumps(result), task_id))
            return jsonify(success=True, **result)
        except Exception as exc:
            return failure(exc)

    def publish(data):
        """Called only by the existing publish endpoint, after an explicit publish click."""
        sku, upc = str(data.get('sku') or '').strip(), str(data.get('upc') or '').strip()
        if not sku or not upc:
            raise mapping.MappingError('SKU and UPC are required.')
        reference = str(data.get('mappingReferenceId') or '')
        with db_connection('listagent.db') as conn:
            tables(conn)
            rows = conn.execute('SELECT result FROM listing_mapping_tasks WHERE upc=? AND result IS NOT NULL', (upc,)).fetchall()
        if not any(p.get('mappingReferenceId') == reference for row in rows for p in json.loads(row[0]).get('previews', [])):
            raise mapping.MappingError('The eBay recommendation reference could not be verified for this item. Request fresh recommendations.')
        if data.get('offerId'):
            raise mapping.MappingError('This item already has an Inventory API offer. Native recommendations cannot change its publishing method.')
        def record(result):
            listing_id = result['listingId']
            try:
                mark_listed(upc, platform='ebay', listing_id=listing_id, offer_id=None, sku=sku,
                            marketplace_id='EBAY_US', title=data.get('title'), price=data.get('price'),
                            quantity=data.get('quantity'), url='https://www.ebay.com/itm/'+listing_id, source='listingagent')
                mark_bol('ebay', upc=upc, lot_number=data.get('lot_number') or data.get('lot'),
                         item_id=data.get('id') or data.get('item_id') or data.get('bol_id'))
            except Exception as exc:
                safe_error(exc, 'listingagent:mapped_publish_ledger')
                result = {**result, 'warnings': (result.get('warnings') or []) + ['Published on eBay; local listing status needs synchronization.']}
            return {'success':True, **result, 'mappingReferenceId':reference, 'publishingMethod':'Trading'}
        with db_connection('listagent.db') as conn:
            existing = conn.execute('SELECT upc,status,result FROM listing_mapping_publications WHERE sku=?', (sku,)).fetchone()
        if existing and existing[0] == upc and existing[1] == 'published':
            return record({**json.loads(existing[2]), 'alreadyPublished':True})
        offers = ebay_request('GET', '/sell/inventory/v1/offer', params={'sku':sku, 'marketplace_id':'EBAY_US'})
        if offers.status_code not in (200, 204, 404):
            raise mapping.MappingError('Could not check existing eBay offers. Try again before publishing.')
        if offers.status_code == 200 and offers.json().get('offers'):
            raise mapping.MappingError('An eBay Inventory offer already exists for this SKU. Open that offer to avoid a duplicate listing.')
        preview = build_draft(data, dry_run=True)
        offer, inventory = preview['offer'], preview['inventoryItem']
        location = ebay_request('GET', '/sell/inventory/v1/location/' + quote(offer.get('merchantLocationKey') or '', safe=''))
        if location.status_code >= 400:
            raise mapping.MappingError('Could not load the item location. Check eBay location settings.')
        address = (location.json().get('location') or {}).get('address') or {}
        request_id = mapping.request_uuid(upc, sku)
        call, xml = mapping.listing_xml(data, inventory, offer, address, reference, request_id)
        if data.get('dry_run'):
            return {'success':True, 'dry_run':True, 'wouldPublish':True, 'publishingMethod':'Trading',
                    'mappingReferenceId':reference, 'requestXml':xml}
        token = os.getenv('EBAY_OLDAUTH_TOKEN') or ''
        if not token:
            raise mapping.MappingError('Connect eBay Trading before publishing native recommendations (EBAY_OLDAUTH_TOKEN). Your draft is saved locally.')
        # Verify first: validation has no listing side effects.
        verify_call, verify_xml = mapping.listing_xml(data, inventory, offer, address, reference, request_id, verify=True)
        mapping.trading(post, token, verify_call, verify_xml)
        with db_connection('listagent.db') as conn:
            tables(conn)
            conn.execute('BEGIN IMMEDIATE')
            existing = conn.execute('SELECT upc,status,result,updated FROM listing_mapping_publications WHERE sku=?', (sku,)).fetchone()
            if existing:
                if existing[0] != upc:
                    raise mapping.MappingError('This SKU is already associated with another item.')
                if existing[1] == 'published':
                    result = json.loads(existing[2])
                    return {'success': True, **result, 'alreadyPublished':True}
                if time.time()-existing[3] < 90:
                    raise mapping.MappingError('This listing request is still processing. Check again shortly.')
                if time.time()-existing[3] > 86400:
                    raise mapping.MappingError('An earlier publish was not confirmed. Check this SKU in Seller Hub before trying again.')
            conn.execute('INSERT OR REPLACE INTO listing_mapping_publications VALUES (?,?,?,?,?,?)',
                         (sku, upc, request_id, 'processing', None, time.time()))
        # On transport uncertainty retain the claim and UUID; never fall back to another listing API.
        result = mapping.trading(post, token, call, xml)
        if not result.get('listingId'):
            raise mapping.MappingError('eBay did not confirm a listing ID. Check Seller Hub before retrying.')
        with db_connection('listagent.db') as conn:
            conn.execute('UPDATE listing_mapping_publications SET status=?,result=?,updated=? WHERE sku=?',
                         ('published', json.dumps(result), time.time(), sku))
        return record(result)
    return publish
