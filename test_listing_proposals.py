"""Listing agent proposals: the eligibility gate, pricing rule, proposal build, and review actions.

Runs against temporary databases; every eBay and Claude call is stubbed.
"""
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault('DISABLE_BACKGROUND_SERVICES', '1')

from sweetshelves import config, listing_proposals as lp, runtime
from sweetshelves import listing_checks, listing_queue


UPC = '883049370897'


class ProposalTestCase(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.root = Path(self.folder.name)
        self._patch = patch.object(config, 'BASE_DIR', self.root)
        self._patch.start()
        self.addCleanup(self._patch.stop)
        self.ctx = runtime.app.app_context()
        self.ctx.push()
        self.addCleanup(self.ctx.pop)
        self.addCleanup(self.close_connections)
        self.seed()

    @staticmethod
    def close_connections():
        from flask import g
        for conn in getattr(g, '_db_connections', {}).values():
            try:
                conn.close()
            except Exception:
                pass
        g._db_connections = {}

    def run_script(self, name, script):
        conn = sqlite3.connect(self.root / name)
        try:
            conn.executescript(script)
            conn.commit()
        finally:
            conn.close()

    def seed(self, *, prep_qty=2, rack_qty=2, prep_status='good', ebay_live=False, prep_note=''):
        self.run_script('bol.db', f'''
            CREATE TABLE items_prep_status (id INTEGER PRIMARY KEY, upc TEXT, lot_number TEXT DEFAULT '', status TEXT,
                reason TEXT, note TEXT, updated_at TEXT, location TEXT, pictureposition TEXT, quantity INTEGER DEFAULT 1);
            INSERT INTO items_prep_status (upc, lot_number, status, reason, note, updated_at, location, quantity)
                VALUES ('{UPC}', 'LOT-A', '{prep_status}', '', '{prep_note}', '2026-09-10T10:00:00', 'B-07', {prep_qty});
            CREATE TABLE bol_items (id INTEGER PRIMARY KEY, upc TEXT, item_description TEXT, image_url TEXT, lot_number TEXT, import_date TEXT);
            CREATE TABLE items_prep_notes (id INTEGER PRIMARY KEY, upc TEXT, note TEXT, created_at TEXT);
            CREATE TABLE items_prep_images (id INTEGER PRIMARY KEY, upc TEXT, image_path TEXT, created_at TEXT, deleted_at TEXT, expires_at TEXT, trash_path TEXT, rotation INTEGER);
            CREATE TABLE items_prep_media (id INTEGER PRIMARY KEY, upc TEXT, row_status TEXT DEFAULT '', media_type TEXT, file_path TEXT, mime_type TEXT, created_at TEXT);
        ''')
        rack_rows = ''.join(f"INSERT INTO SEARCHRACK (TITLE, BARCODE, ITEM_POSITION, QUANTITY) VALUES ('Mixer', '{UPC}', 'B-07', 1);\n" for _ in range(rack_qty))
        self.run_script('searchRack.db', f'''
            CREATE TABLE SEARCHRACK (ID INTEGER PRIMARY KEY AUTOINCREMENT, TITLE TEXT, BARCODE TEXT, ITEM_POSITION TEXT,
                IMAGES TEXT, PICTUREPOSITION TEXT, QUANTITY INTEGER, IMAGE TEXT, ITEMID TEXT, CREATED_AT TEXT);
            {rack_rows}
        ''')
        live = f"INSERT INTO INVENTORY (Title, ItemID, SKU, Price, Quantity, UPC, List_State) VALUES ('Mixer live', '1234567890', '{UPC}', 250, 1, '{UPC}', 'Active');" if ebay_live else ''
        self.run_script('ebayStore.db', f'''
            CREATE TABLE INVENTORY (ID INTEGER PRIMARY KEY, Title TEXT, ItemID TEXT, SKU TEXT, Price REAL, Quantity INTEGER,
                Image TEXT, URL TEXT, List_State TEXT, Sold_Date TEXT, List_Date TEXT, UPC TEXT);
            {live}
        ''')
        self.run_script('amazonStore.db', '''
            CREATE TABLE ITEMS (ID INTEGER PRIMARY KEY, ASIN TEXT, SKU TEXT, TITLE TEXT, PRICE REAL, QUANTITY INTEGER, STATUS TEXT,
                IMAGE TEXT, UPC TEXT, CONDITION TEXT, FULFILLMENT_CHANNEL TEXT, LAST_UPDATED TEXT);
        ''')
        self.run_script('rawbol.db', f'''
            CREATE TABLE raw_bol_items (id INTEGER PRIMARY KEY, upc TEXT, item_description TEXT, avg_cost REAL, image_url TEXT,
                quantity INTEGER, lot_number TEXT, import_date TEXT, original_retail REAL, prep_reason TEXT);
            INSERT INTO raw_bol_items VALUES (1, '{UPC}', 'KitchenAid 5-Qt Stand Mixer Empire Red', 120.0, 'https://img.example/mixer.jpg', 2, 'LOT-A', '2026-09-01', 449.99, '');
        ''')
        self.run_script('sync_settings.db', '''
            CREATE TABLE listing_agent_settings (key TEXT PRIMARY KEY, value TEXT, updated_at TEXT);
            INSERT INTO listing_agent_settings VALUES ('ebay_fulfillment_policy_id', 'F1', ''), ('ebay_payment_policy_id', 'P1', ''),
                ('ebay_return_policy_id', 'R1', ''), ('ebay_marketplace_id', 'EBAY_US', '');
        ''')
        listing_queue._listagent_add_to_queue(UPC, title='KitchenAid mixer', source='list_manager', added_mode='my')

    def reseed(self, **kwargs):
        from flask import g
        for conn in getattr(g, '_db_connections', {}).values():
            conn.close()
        g._db_connections = {}
        for name in ('bol.db', 'searchRack.db', 'ebayStore.db', 'amazonStore.db', 'rawbol.db', 'sync_settings.db', 'listagent.db'):
            path = self.root / name
            if path.exists():
                path.unlink()
        self.seed(**kwargs)


def comps(*prices):
    return [{'itemId': f'v1|{i}|0', 'title': f'Comp {i}', 'price': {'value': str(p), 'currency': 'USD'},
             'buyingOptions': ['FIXED_PRICE'], 'itemWebUrl': 'https://www.ebay.com/itm/1', 'image': '', 'condition': 'New',
             'seller': 's', 'shipping': None} for i, p in enumerate(prices)]


class StubbedNetwork:
    """Everything the builder fetches, with a record of what it was asked."""

    def __init__(self, test, *, comp_prices=(280, 300, 320), required=('Brand', 'Model')):
        self.calls = []
        detail = {'upc': UPC, 'title': 'KitchenAid 5-Qt Stand Mixer Empire Red',
                  'images': ['https://img.example/mixer.jpg'], 'defect': '',
                  'prep': {'notes': [], 'images': ['http://localhost/static/items_prep/mixer_front.jpg'], 'voice_notes': [], 'videos': []}}
        candidate = {'title': 'KitchenAid KSM150PS 5 Qt Stand Mixer Empire Red', 'aspects': {'Brand': 'KitchenAid', 'Color': 'Empire Red'},
                     'categoryId': '133701', 'categoryPath': 'Home & Garden > Kitchen > Mixers', 'epid': '999', 'brand': 'KitchenAid'}
        meta = [{'name': n, 'required': True, 'values': []} for n in required] + [{'name': 'Color', 'required': False, 'values': []}]
        stubs = {
            '_agent_upc_detail': lambda upc, base_url=None: detail,
            '_agent_catalog_candidates': lambda upc, title, mp, base_url=None: [candidate],
            '_agent_category_suggestions': lambda q, mp, base_url=None: [{'categoryId': '133701', 'path': 'Home & Garden > Kitchen > Mixers', 'categoryName': 'Mixers'}],
            '_agent_category_aspects': lambda cid, mp: meta,
            '_agent_comps': lambda upc, mp, base_url=None: (comps(*comp_prices), ''),
            '_agent_generate_title': lambda payload, base_url=None: 'KitchenAid KSM150PS 5 Qt Tilt-Head Stand Mixer Empire Red',
            '_agent_generate_description': lambda payload, base_url=None: '<h2>Mixer</h2><p>Great.</p>',
            '_agent_claude_json': self.claude,
            '_agent_dry_run': lambda payload, base_url=None: ({'offer': {'sku': payload.get('sku')}, 'inventoryItem': {}}, ''),
            '_agent_publish': self.publish,
        }
        for name, fn in stubs.items():
            p = patch.object(lp, name, fn)
            p.start()
            test.addCleanup(p.stop)
        self.publish_result = {'success': True, 'listingId': '5550001', 'offerId': 'OFF1'}

    def claude(self, prompt, max_tokens=700):
        self.calls.append(('claude', prompt))
        return {'Model': 'KSM150PS'}

    def publish(self, payload, base_url=None):
        self.calls.append(('publish', payload))
        return self.publish_result, 200


class EligibilityTests(ProposalTestCase):
    def test_prepped_and_shelved_item_passes_with_listable_quantity(self):
        e = lp._agent_eligibility(UPC)
        self.assertTrue(e['ok'])
        self.assertEqual(e['listable_quantity'], 2)
        self.assertEqual(e['prep']['lots'], ['LOT-A'])
        self.assertIn('B-07', e['rack']['locations'])
        self.assertFalse(e['quantity_mismatch'])
        statuses = {c['key']: c['status'] for c in e['checks']}
        self.assertEqual(statuses['prepped'], 'pass')
        self.assertEqual(statuses['traceable'], 'pass')
        self.assertEqual(statuses['duplicate'], 'pass')

    def test_unprepped_item_is_blocked(self):
        self.reseed(prep_status='bad')
        e = lp._agent_eligibility(UPC)
        self.assertTrue(e['blocked'])
        self.assertEqual(e['listable_quantity'], 0)
        self.assertEqual([c['key'] for c in e['checks'] if c['status'] == 'block'], ['prepped', 'quantity'])

    def test_missing_rack_row_is_blocked(self):
        self.reseed(rack_qty=0)
        e = lp._agent_eligibility(UPC)
        self.assertTrue(e['blocked'])
        self.assertIn('traceable', [c['key'] for c in e['checks'] if c['status'] == 'block'])

    def test_quantity_mismatch_lists_lower_number_and_requests_recount(self):
        self.reseed(prep_qty=3, rack_qty=2)
        e = lp._agent_eligibility(UPC)
        self.assertTrue(e['ok'])
        self.assertTrue(e['quantity_mismatch'])
        self.assertEqual(e['listable_quantity'], 2)
        self.assertTrue(lp._agent_ensure_recount_request(UPC, e))
        req = listing_checks._listagent_get_check_request(UPC)
        self.assertEqual(req['status'], 'open')
        self.assertEqual(req['check_quantity'], 1)
        self.assertIn('prep says 3, rack says 2', req['custom_note'])
        self.assertFalse(lp._agent_ensure_recount_request(UPC, e), 'an open request is not duplicated')

    def test_live_ebay_listing_is_flagged_not_blocked(self):
        self.reseed(ebay_live=True)
        e = lp._agent_eligibility(UPC)
        self.assertTrue(e['ok'])
        self.assertTrue(e['already_on_ebay'])
        dup = next(c for c in e['checks'] if c['key'] == 'duplicate')
        self.assertEqual(dup['status'], 'flag')
        self.assertIn('1234567890', dup['detail'])


class PricingTests(unittest.TestCase):
    def test_median_of_fixed_price_comps(self):
        p = lp._agent_price_from_comps(comps(250, 300, 320, 900), cost=100, floor_multiplier=1.25)
        self.assertEqual(p['median'], 310.0)
        self.assertEqual(p['proposed'], 310.0)
        self.assertEqual((p['low'], p['high'], p['count']), (250.0, 900.0, 4))
        self.assertEqual(p['floor'], 125.0)
        self.assertEqual(p['rule'], 'median of current comps')

    def test_never_below_cost_floor(self):
        p = lp._agent_price_from_comps(comps(40, 45), cost=100, floor_multiplier=1.25)
        self.assertEqual(p['proposed'], 125.0)
        self.assertIn('cost floor', p['rule'])

    def test_no_comps_uses_floor_and_no_cost_gives_nothing(self):
        self.assertEqual(lp._agent_price_from_comps([], cost=80, floor_multiplier=1.0)['proposed'], 80.0)
        self.assertIsNone(lp._agent_price_from_comps([], cost=None)['proposed'])

    def test_auction_only_comps_are_ignored(self):
        rows = comps(300)
        rows[0]['buyingOptions'] = ['AUCTION']
        self.assertEqual(lp._agent_price_from_comps(rows)['count'], 0)


class JudgmentTests(unittest.TestCase):
    def test_condition_from_damage_words_else_default(self):
        used = lp._agent_condition({'defect': ''}, [{'note': 'small scratch on bowl rim', 'reason': ''}])
        self.assertEqual(used['condition'], 'USED_GOOD')
        self.assertIn('scratch', used['conditionDescription'])
        clean = lp._agent_condition({'defect': '', 'prep': {'notes': [{'note': 'boxed'}]}}, [], default_condition='NEW_OTHER')
        self.assertEqual(clean['condition'], 'NEW_OTHER')
        self.assertTrue(clean['assumed'])

    def test_aspects_fill_from_candidate_then_claude_for_required_only(self):
        meta = [{'name': 'Brand', 'required': True}, {'name': 'Model', 'required': True}, {'name': 'Color', 'required': False}]
        asked = []

        def claude(prompt, max_tokens=700):
            asked.append(prompt)
            return {'Model': 'KSM150PS'}

        with patch.object(lp, '_agent_claude_json', claude):
            filled, missing, inferred = lp._agent_fill_aspects(meta, {'brand': 'KitchenAid', 'Color': ['Red']}, title='KitchenAid KSM150PS mixer', upc=UPC)
        self.assertEqual(filled, {'Brand': 'KitchenAid', 'Model': 'KSM150PS', 'Color': 'Red'})
        self.assertEqual(missing, [])
        self.assertEqual(inferred, ['Model'])
        self.assertEqual(len(asked), 1)
        self.assertIn('"Model"', asked[0])
        self.assertNotIn('"Brand"', asked[0].split('Requested specifics:')[1])

    def test_publish_payload_rebases_local_images_and_skips_disabled(self):
        proposal = {'upc': UPC, 'sku': UPC, 'title': 'T', 'price': 10, 'quantity': 1, 'aspects': {'Brand': 'X'},
                    'categoryPath': 'ignored',
                    'images': [{'url': '/static/items_prep/a.jpg', 'enabled': True}, {'url': 'https://img.example/b.jpg', 'enabled': False}, {'url': '/static/c.jpg', 'enabled': True}]}
        payload = lp._agent_publish_payload(proposal, base_url='https://shop.example/')
        self.assertEqual(payload['images'], ['https://shop.example/static/items_prep/a.jpg', 'https://shop.example/static/c.jpg'])
        self.assertNotIn('categoryPath', payload)
        self.assertEqual(payload['description'], 'T')


class BuildAndReviewTests(ProposalTestCase):
    def test_build_creates_reviewable_proposal_from_existing_endpoints(self):
        net = StubbedNetwork(self)
        item = lp._agent_build_proposal(UPC, actor='agent', base_url='http://localhost/')
        self.assertEqual(item['status'], 'proposed')
        p = item['proposal']
        self.assertEqual(p['title'], 'KitchenAid KSM150PS 5 Qt Tilt-Head Stand Mixer Empire Red')
        self.assertEqual(p['price'], 300.0)
        self.assertEqual(p['quantity'], 2)
        self.assertEqual(p['categoryId'], '133701')
        self.assertEqual(p['aspects'], {'Brand': 'KitchenAid', 'Model': 'KSM150PS', 'Color': 'Empire Red'})
        self.assertEqual(p['condition'], 'NEW', 'prep passed it good and wrote nothing about it')
        self.assertEqual(p['fulfillmentPolicyId'], 'F1')
        self.assertEqual(p['lot_number'], 'LOT-A')
        self.assertEqual([im['source'] for im in p['images']], ['prep', 'catalog'])
        self.assertEqual(p['images'][0]['url'], '/static/items_prep/mixer_front.jpg', 'local photos are stored as paths')
        self.assertEqual(item['comps']['pricing']['median'], 300.0)
        self.assertEqual(item['comps']['pricing']['cost'], 120.0)
        self.assertEqual(len(item['comps']['results']), 3)
        codes = {f['code'] for f in item['flags']}
        self.assertIn('aspects_inferred', codes)
        self.assertIn('condition_new', codes)
        self.assertNotIn('condition_assumed', codes)
        self.assertNotIn('missing_required_aspects', codes)
        self.assertEqual([ev['event'] for ev in item['events']], ['proposed'])
        self.assertTrue(item['sources']['dry_run']['ok'])
        self.assertEqual(item['problems'] if 'problems' in item else lp._agent_validate_for_publish(p, item['sources']), [])

    def test_blocked_item_stores_blocked_proposal_without_network(self):
        self.reseed(prep_status='bad')
        with patch.object(lp, '_agent_upc_detail', side_effect=AssertionError('must not fetch')):
            item = lp._agent_build_proposal(UPC)
        self.assertEqual(item['status'], 'blocked')
        self.assertEqual(item['proposal'], {})
        self.assertTrue(any(f['level'] == 'block' for f in item['flags']))

    def test_rebuild_supersedes_the_open_proposal(self):
        StubbedNetwork(self)
        first = lp._agent_build_proposal(UPC)
        second = lp._agent_build_proposal(UPC)
        self.assertNotEqual(first['id'], second['id'])
        self.assertEqual(lp._proposal_get(first['id'])['status'], 'superseded')
        rows, counts = lp._proposal_list(status='open')
        self.assertEqual([r['id'] for r in rows], [second['id']])
        self.assertEqual(counts, {'superseded': 1, 'proposed': 1})
        self.assertEqual(lp._agent_queued_candidates(), [], 'queued item with an open proposal is not rebuilt')

    def test_live_ebay_listing_holds_the_proposal(self):
        self.reseed(ebay_live=True)
        StubbedNetwork(self)
        item = lp._agent_build_proposal(UPC)
        self.assertEqual(item['status'], 'held')

    def test_missing_required_aspect_blocks_approval_until_filled(self):
        net = StubbedNetwork(self, required=('Brand', 'Model', 'Capacity'))
        item = lp._agent_build_proposal(UPC)
        self.assertIn('missing_required_aspects', {f['code'] for f in item['flags']})
        with self.assertRaises(lp.ss_errors._ListingAgentUserError) as raised:
            lp._agent_approve(item['id'], actor='dan')
        self.assertIn("'Capacity'", str(raised.exception))
        self.assertEqual([c[0] for c in net.calls if c[0] == 'publish'], [])
        listed = lp._agent_approve(item['id'], actor='dan', edits={'aspects': {'Brand': 'KitchenAid', 'Model': 'KSM150PS', 'Capacity': '5 qt'}})
        self.assertEqual(listed['status'], 'listed')

    def test_approve_publishes_edited_proposal_and_records_ids(self):
        net = StubbedNetwork(self)
        item = lp._agent_build_proposal(UPC)
        listed = lp._agent_approve(item['id'], actor='dan', note='looks good',
                                   edits={'price': '289.00', 'title': 'KitchenAid 5-Qt Tilt-Head Stand Mixer, Empire Red', 'quantity': '5'},
                                   base_url='https://shop.example/')
        self.assertEqual(listed['status'], 'listed')
        self.assertEqual(listed['listed_listing_id'], '5550001')
        self.assertEqual(listed['listed_offer_id'], 'OFF1')
        self.assertEqual(listed['reviewed_by'], 'dan')
        self.assertEqual(listed['reviewer_note'], 'looks good')
        payload = next(c[1] for c in net.calls if c[0] == 'publish')
        self.assertEqual(payload['price'], 289.0)
        self.assertEqual(payload['quantity'], 2, 'quantity is capped at what the gate says is listable')
        self.assertEqual(payload['title'], 'KitchenAid 5-Qt Tilt-Head Stand Mixer, Empire Red')
        self.assertEqual(payload['images'][0], 'https://shop.example/static/items_prep/mixer_front.jpg')
        self.assertEqual(payload['fulfillmentPolicyId'], 'F1')
        self.assertFalse(payload['dry_run'])
        self.assertEqual([ev['event'] for ev in listed['events']], ['proposed', 'edited', 'approved', 'published'])

    def test_failed_publish_keeps_proposal_open_with_the_error(self):
        net = StubbedNetwork(self)
        net.publish_result = {'success': False, 'error': 'eBay API error (400): Missing Brand'}
        item = lp._agent_build_proposal(UPC)
        with self.assertRaises(lp.ss_errors._ListingAgentUserError):
            lp._agent_approve(item['id'], actor='dan')
        after = lp._proposal_get(item['id'])
        self.assertEqual(after['status'], 'proposed')
        self.assertIn('Missing Brand', after['error'])
        self.assertEqual(after['events'][-1]['event'], 'publish_failed')

    def test_gate_recheck_on_approve_blocks_when_stock_vanished(self):
        net = StubbedNetwork(self)
        item = lp._agent_build_proposal(UPC)
        conn = sqlite3.connect(self.root / 'searchRack.db')
        conn.execute('UPDATE SEARCHRACK SET QUANTITY = 0')
        conn.commit()
        conn.close()
        with self.assertRaises(lp.ss_errors._ListingAgentUserError):
            lp._agent_approve(item['id'], actor='dan')
        self.assertEqual(lp._proposal_get(item['id'])['status'], 'blocked')
        self.assertEqual([c[0] for c in net.calls if c[0] == 'publish'], [])

    def test_hold_release_photos_and_reject(self):
        StubbedNetwork(self)
        item = lp._agent_build_proposal(UPC)
        self.assertEqual(lp._agent_set_status(item['id'], 'held', actor='dan', note='wait')['status'], 'held')
        self.assertEqual(lp._agent_set_status(item['id'], 'proposed', actor='dan', event='released')['status'], 'proposed')
        photos = lp._agent_request_photos(item['id'], actor='dan', note='show the bowl rim')
        self.assertEqual(photos['status'], 'needs_photos')
        req = listing_checks._listagent_get_check_request(UPC)
        self.assertEqual(req['take_pictures'], 1)
        self.assertIn('front, back, label or UPC, any flaw', req['custom_note'])
        self.assertIn('show the bowl rim', req['custom_note'])
        rejected = lp._agent_reject(item['id'], actor='dan', note='not worth it')
        self.assertEqual(rejected['status'], 'rejected')
        queue = listing_queue._listagent_get_queue_statuses([UPC])
        self.assertEqual(queue, {}, 'rejected item leaves the listing queue')

    def test_routes_round_trip_through_flask(self):
        net = StubbedNetwork(self)
        import token_manager
        with patch.object(token_manager, 'get_access_token', lambda: 'test-token'):
            from sweetshelves.bootstrap import app
        client = app.test_client()
        built = client.post('/api/listingagent/proposals/build', json={'upc': UPC, 'reviewed_by': 'dan'}).get_json()
        self.assertTrue(built['success'], built)
        pid = built['item']['id']
        listing = client.get('/api/listingagent/proposals?status=open').get_json()
        self.assertEqual([r['id'] for r in listing['items']], [pid])
        self.assertEqual(listing['shot_list'], list(lp.SHOT_LIST))
        saved = client.post(f'/api/listingagent/proposals/{pid}/save', json={'edits': {'price': 275}, 'note': 'lower', 'reviewed_by': 'dan'}).get_json()
        self.assertEqual(saved['item']['proposal']['price'], 275.0)
        done = client.post(f'/api/listingagent/proposals/{pid}/action', json={'action': 'approve', 'reviewed_by': 'dan'}).get_json()
        self.assertTrue(done['success'], done)
        self.assertEqual(done['item']['status'], 'listed')
        page = client.get('/listingagent/review')
        self.assertEqual(page.status_code, 200)
        self.assertIn(b'Listing Review', page.data)


if __name__ == '__main__':
    unittest.main()
