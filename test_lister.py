"""Sweet Shelves Lister server side: proposal feed, selection, confirm-link bookkeeping, update feed.

Runs against temporary databases through the real sweetshelves helpers; nothing reaches a store.
"""
from contextlib import closing
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault('DISABLE_BACKGROUND_SERVICES', '1')

from flask import Flask

from sweetshelves import config, database, errors, listing_checks, listing_queue, runtime
import lister_routes
from lister_routes import fill_fields, html_to_text, validate_link, ListerError


UPC = '883049370897'


class ListerTestCase(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.root = Path(self.folder.name)
        (self.root / 'static').mkdir()
        self._patch = patch.object(config, 'BASE_DIR', self.root)
        self._patch.start()
        self.addCleanup(self._patch.stop)
        self.app = Flask('lister-test', static_folder=str(self.root / 'static'), template_folder=str(Path(__file__).resolve().parent / 'templates'))
        self.app.teardown_appcontext(database.close_db_connections)
        self.bol_marks = []
        self.built = []
        self.detail_payload = {}

        def fake_upc_detail(upc):
            from flask import jsonify, request as flask_request
            payload = json.loads(json.dumps(self.detail_payload)) if self.detail_payload else {}
            payload['upc'] = upc
            payload['base_url'] = flask_request.url_root
            return jsonify({'success': True, 'item': payload})

        def fake_build(upc, *, actor='agent', base_url=None):
            self.built.append((upc, actor, base_url))
            with closing(sqlite3.connect(self.root / 'listagent.db')) as conn:
                conn.execute('''INSERT INTO listing_proposals (upc, status, proposal_json, created_at, updated_at)
                                VALUES (?, 'proposed', ?, '2026-09-16T10:00:00', '2026-09-16T10:00:00')''',
                             (upc, json.dumps({'title': 'Built title', 'price': 12.5, 'quantity': 1, 'condition': 'NEW_OTHER'})))
                conn.commit()

        self.lister = lister_routes.register(self.app, {
            'db_connection': database.db_connection,
            '_safe_error': errors._safe_error,
            '_listagent_mark_listed': listing_queue._listagent_mark_listed,
            '_listagent_upc_variants': listing_queue._listagent_upc_variants,
            '_listagent_format_upc12': listing_queue._listagent_format_upc12,
            '_listagent_init_tables': listing_checks._listagent_init_tables,
            'api_listingagent_upc_detail': fake_upc_detail,
            '_agent_build_proposal': fake_build,
            '_listagent_remove_from_queue': listing_queue._listagent_remove_from_queue,
            '_listing_center_mark_bol_listed': lambda mp, **kw: (self.bol_marks.append((mp, kw.get('upc'))) or {'success': True}),
            '_listagent_add_photo': listing_queue._listagent_add_photo,
            'BASE_DIR': self.root,
        })
        self.client = self.app.test_client()
        self.seed()

    def sql(self, db, query, args=()):
        with closing(sqlite3.connect(self.root / db)) as conn:
            conn.row_factory = sqlite3.Row
            rows = [dict(r) for r in conn.execute(query, args).fetchall()]
            conn.commit()
            return rows

    def seed(self):
        proposal = {
            'sku': UPC, 'title': 'Lenox Butterfly Meadow Dinner Plate', 'price': 24.5, 'quantity': 5,
            'condition': 'NEW_OTHER', 'conditionDescription': 'Open box, never used',
            'listingDescription': '<p>Lenox <b>Butterfly Meadow</b> plate.</p><ul><li>Dishwasher safe</li></ul>',
            'categoryId': '36027', 'categoryPath': 'Home & Garden > Dinnerware',
            'aspects': {'Brand': ['Lenox'], 'Material': 'Porcelain', 'Color': []},
            'images': [{'url': '/static/listingagent_uploads/a.jpg', 'enabled': True},
                       {'url': 'https://cdn.example/b.jpg', 'enabled': False},
                       {'url': 'https://cdn.example/c.jpg', 'enabled': True}],
        }
        eligibility = {'listable_quantity': 2, 'prep': {'lots': ['L-1']}, 'rack': {'locations': ['A-3']}}
        with closing(sqlite3.connect(self.root / 'listagent.db')) as conn:
            cur = conn.cursor()
            listing_checks._listagent_init_tables(cur)
            cur.execute('''CREATE TABLE listing_proposals (id INTEGER PRIMARY KEY AUTOINCREMENT, upc TEXT NOT NULL,
                queue_id INTEGER, status TEXT NOT NULL DEFAULT 'proposed', agent_version TEXT, eligibility_json TEXT,
                proposal_json TEXT, comps_json TEXT, sources_json TEXT, flags_json TEXT, reviewer_note TEXT,
                reviewed_by TEXT, reviewed_at TEXT, error TEXT, listed_listing_id TEXT, listed_offer_id TEXT,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL)''')
            cur.execute('''CREATE TABLE listing_proposal_events (id INTEGER PRIMARY KEY AUTOINCREMENT,
                proposal_id INTEGER NOT NULL, upc TEXT NOT NULL, event TEXT NOT NULL, actor TEXT, note TEXT,
                payload_json TEXT, created_at TEXT NOT NULL)''')
            cur.execute('''INSERT INTO listing_proposals (upc, status, eligibility_json, proposal_json, flags_json, created_at, updated_at)
                           VALUES (?, 'proposed', ?, ?, ?, '2026-09-13T10:00:00', '2026-09-13T10:00:00')''',
                        (UPC, json.dumps(eligibility), json.dumps(proposal), json.dumps([{'level': 'warn', 'code': 'qty', 'message': 'Quantity mismatch'}])))
            cur.execute('''INSERT INTO listing_proposals (upc, status, proposal_json, created_at, updated_at)
                           VALUES ('012345678905', 'held', ?, '2026-09-13T09:00:00', '2026-09-13T09:00:00')''',
                        (json.dumps({'title': 'Other item', 'price': 9, 'quantity': 1, 'condition': 'NEW'}),))
            cur.execute("INSERT INTO listing_queue (upc, title, status, added_at) VALUES (?, 'Lenox plate', 'queued', '2026-09-13T08:00:00')", (UPC,))
            conn.commit()
        with closing(sqlite3.connect(self.root / 'searchRack.db')) as conn:
            conn.execute('''CREATE TABLE SEARCHRACK (ID INTEGER PRIMARY KEY, TITLE TEXT, BARCODE TEXT, ITEM_POSITION TEXT,
                            IMAGES TEXT, PICTUREPOSITION TEXT, ITEMID TEXT, QUANTITY INTEGER, CREATED_AT TEXT, IMAGE TEXT)''')
            conn.execute("INSERT INTO SEARCHRACK (ID, TITLE, BARCODE, ITEM_POSITION, QUANTITY) VALUES (1, 'Lenox plate', ?, 'A-3', 2)", (UPC,))
            conn.execute("INSERT INTO SEARCHRACK (ID, TITLE, BARCODE, ITEM_POSITION, QUANTITY) VALUES (2, 'Lenox plate damaged', ?, 'B-1', 1)", (UPC + '-1',))
            conn.execute("INSERT INTO SEARCHRACK (ID, TITLE, BARCODE, ITEM_POSITION, QUANTITY) VALUES (3, 'Gone', ?, 'C-1', 0)", (UPC,))
            conn.commit()
        with closing(sqlite3.connect(self.root / 'ebayStore.db')) as conn:
            conn.execute('CREATE TABLE INVENTORY (ID INTEGER PRIMARY KEY, Title TEXT, ItemID TEXT, SKU TEXT, UPC TEXT, List_State TEXT)')
            conn.execute("INSERT INTO INVENTORY (Title, ItemID, SKU, UPC, List_State) VALUES ('Old plate listing', '112233445566', 'office', ?, 'Active')", (UPC,))
            conn.commit()

    # -- pure helpers --------------------------------------------------------------------

    def test_html_to_text_flattens_markup(self):
        self.assertEqual(html_to_text('<p>Lenox <b>plate</b>.</p><ul><li>Safe &amp; sound</li></ul>'),
                         'Lenox plate.\n\nSafe & sound')

    def test_fill_fields_caps_quantity_and_absolutises_images(self):
        fields = fill_fields({'quantity': 5, 'condition': 'used_good', 'images': ['/static/x.jpg'], 'aspects': {'Brand': 'Lenox'}},
                             {'listable_quantity': 2}, upc=UPC, base_url='https://pi.example/')
        self.assertEqual(fields['quantity'], 2)
        self.assertEqual(fields['images'], ['https://pi.example/static/x.jpg'])
        self.assertEqual(fields['amazonCondition'], 'used_good')
        self.assertEqual(fields['brand'], 'Lenox')
        self.assertEqual(fields['sku'], UPC)

    def test_validate_link_rules(self):
        with self.assertRaises(ListerError):
            validate_link({'platform': 'etsy'})
        with self.assertRaises(ListerError):
            validate_link({'platform': 'ebay', 'listing_id': 'abc'})
        with self.assertRaises(ListerError):
            validate_link({'platform': 'amazon'})
        with self.assertRaises(ListerError):
            validate_link({'platform': 'amazon', 'sku': 'S1', 'url': 'http://insecure'})
        clean = validate_link({'platform': 'amazon', 'asin': 'b0abcdefgh', 'price': '12.345'})
        self.assertEqual(clean['asin'], 'B0ABCDEFGH')
        self.assertEqual(clean['listing_id'], 'B0ABCDEFGH')
        self.assertEqual(clean['price'], 12.35)

    # -- feed -----------------------------------------------------------------------------

    def test_items_default_to_selection_and_carry_fill_data(self):
        empty = self.client.get('/api/lister/items').get_json()
        self.assertTrue(empty['success'])
        self.assertEqual(empty['items'], [])

        res = self.client.post('/api/lister/select', json={'proposal_ids': [1], 'platform': 'ebay', 'actor': 'dan'})
        self.assertEqual(res.status_code, 200, res.get_json())
        self.assertEqual(res.get_json()['total_selected'], 1)

        data = self.client.get('/api/lister/items', base_url='https://pi.example').get_json()
        self.assertEqual([it['id'] for it in data['items']], [1])
        item = data['items'][0]
        self.assertTrue(item['selected'])
        self.assertEqual(item['platformHint'], 'ebay')
        fields = item['fields']
        self.assertEqual(fields['title'], 'Lenox Butterfly Meadow Dinner Plate')
        self.assertEqual(fields['quantity'], 2)  # capped by the gate's listable quantity
        self.assertEqual(fields['amazonCondition'], 'new_open_box')
        self.assertEqual(fields['images'], ['https://pi.example/static/listingagent_uploads/a.jpg', 'https://cdn.example/c.jpg'])
        self.assertEqual(fields['aspects'], {'Brand': ['Lenox'], 'Material': ['Porcelain']})
        self.assertEqual(fields['brand'], 'Lenox')
        self.assertEqual(fields['descriptionText'], 'Lenox Butterfly Meadow plate.\n\nDishwasher safe')
        self.assertEqual(fields['lots'], ['L-1'])
        self.assertEqual(item['existing']['ebay'][0]['listingId'], '112233445566')
        self.assertEqual(item['flags'][0]['code'], 'qty')

        events = self.sql('listagent.db', 'SELECT event, note FROM listing_proposal_events WHERE proposal_id = 1')
        self.assertEqual(events[0]['event'], 'helper_selected')

        both = self.client.get('/api/lister/items?scope=open').get_json()
        self.assertEqual([it['id'] for it in both['items']], [1, 2])  # selected first
        self.assertFalse(both['items'][1]['selected'])

        res = self.client.post('/api/lister/select', json={'proposal_ids': [1], 'selected': False})
        self.assertEqual(res.get_json()['total_selected'], 0)
        self.assertEqual(self.client.post('/api/lister/select', json={'proposal_ids': [99]}).status_code, 404)
        self.assertEqual(self.client.post('/api/lister/select', json={}).status_code, 400)

    def test_cross_site_mutations_need_the_extension_header(self):
        res = self.client.post('/api/lister/select', json={'proposal_ids': [1]}, headers={'Sec-Fetch-Site': 'cross-site'})
        self.assertEqual(res.status_code, 403)
        res = self.client.post('/api/lister/select', json={'proposal_ids': [1]},
                               headers={'Sec-Fetch-Site': 'cross-site', 'X-Sweet-Shelves-Lister': '1'})
        self.assertEqual(res.status_code, 200)

    # -- confirm & link ---------------------------------------------------------------------

    def test_confirm_ebay_link_records_every_hook(self):
        self.client.post('/api/lister/select', json={'proposal_ids': [1]})
        res = self.client.post('/api/lister/links', json={
            'proposal_id': 1, 'platform': 'ebay', 'listing_id': '335566778899', 'sku': 'SS-PLATE-1',
            'store_upc': '0883049370897', 'price': 24.5, 'quantity': 2, 'actor': 'dan',
            'url': 'https://www.ebay.com/itm/335566778899'})
        self.assertEqual(res.status_code, 201, res.get_json())
        body = res.get_json()
        link = body['link']
        self.assertFalse(body['duplicate'])
        self.assertEqual(link['upc'], UPC)
        self.assertEqual(link['listing_id'], '335566778899')
        self.assertEqual(link['store_upc'], '0883049370897')
        steps = ' | '.join(link['effects']['steps'])
        self.assertIn('listing queue marked listed on ebay', steps)
        self.assertIn('warehouse match -> A-3', steps)
        self.assertIn('proposal marked listed', steps)
        self.assertIn('store shows UPC 0883049370897', steps)
        self.assertEqual(link['effects']['rack_ids'], [1, 2])  # plain barcode first, suffixed after, zero stock skipped

        queue = self.sql('listagent.db', 'SELECT * FROM listing_queue WHERE upc = ?', (UPC,))[0]
        self.assertEqual(queue['listed_listing_id'], '335566778899')
        self.assertEqual(queue['listed_sku'], 'SS-PLATE-1')
        self.assertEqual(queue['listed_platform'], 'ebay')
        self.assertTrue(queue['listed_ebay_at'])

        proposal = self.sql('listagent.db', 'SELECT status, listed_listing_id, reviewed_by FROM listing_proposals WHERE id = 1')[0]
        self.assertEqual(proposal, {'status': 'listed', 'listed_listing_id': '335566778899', 'reviewed_by': 'dan'})
        events = [e['event'] for e in self.sql('listagent.db', 'SELECT event FROM listing_proposal_events WHERE proposal_id = 1 ORDER BY id')]
        self.assertEqual(events, ['helper_selected', 'published_via_browser'])
        self.assertEqual(self.sql('listagent.db', 'SELECT COUNT(*) AS n FROM listing_helper_selections')[0]['n'], 0)

        match = self.sql('listing_alerts.db', 'SELECT * FROM listing_inventory_matches')[0]
        self.assertEqual((match['store'], match['listing_key'], match['searchrack_id'], match['inventory_barcode'], match['inventory_location']),
                         ('ebay', '335566778899', 1, UPC, 'A-3'))
        self.assertEqual(match['marketplace_barcode'], '0883049370897')
        alias = self.sql('listing_alerts.db', 'SELECT source_key, barcode_key, title FROM finder_aliases')[0]
        self.assertEqual(alias['source_key'], 'ebay:335566778899')
        self.assertEqual(alias['barcode_key'], UPC)
        self.assertEqual(alias['title'], 'Lenox Butterfly Meadow Dinner Plate')

        # Ready to Ship style lookups: item number or SKU -> our UPC and rack rows.
        resolved = self.client.get('/api/lister/resolve?platform=ebay&listing_id=335566778899').get_json()
        self.assertEqual(resolved['upc'], UPC)
        self.assertEqual([r['location'] for r in resolved['rack']], ['A-3', 'B-1'])
        by_sku = self.client.get('/api/lister/resolve?platform=ebay&sku=ss-plate-1').get_json()
        self.assertEqual(by_sku['upc'], UPC)

        # Same live listing again is a no-op; the same item number on another UPC is refused.
        again = self.client.post('/api/lister/links', json={'upc': UPC, 'platform': 'ebay', 'listing_id': '335566778899'})
        self.assertEqual(again.status_code, 200)
        self.assertTrue(again.get_json()['duplicate'])
        clash = self.client.post('/api/lister/links', json={'upc': '012345678905', 'platform': 'ebay', 'listing_id': '335566778899'})
        self.assertEqual(clash.status_code, 409)

        items = self.client.get('/api/lister/items?scope=all').get_json()['items']
        listed = next(it for it in items if it['id'] == 1)
        self.assertEqual(listed['status'], 'listed')
        self.assertEqual(listed['links'][0]['platform'], 'ebay')

    def test_confirm_amazon_link_without_proposal_uses_sku_or_asin(self):
        res = self.client.post('/api/lister/links', json={'upc': '012345678905', 'platform': 'amazon', 'sku': 'AMZ-1',
                                                          'asin': 'B0TESTASIN', 'quantity': 3, 'price': 19})
        self.assertEqual(res.status_code, 201, res.get_json())
        link = res.get_json()['link']
        self.assertEqual(link['url'], 'https://www.amazon.com/dp/B0TESTASIN')
        self.assertIn('queue row created', link['effects']['steps'])
        self.assertIn('no warehouse rows with stock', ' '.join(link['effects']['steps']))
        queue = self.sql('listagent.db', "SELECT * FROM listing_queue WHERE upc = '012345678905'")[0]
        self.assertEqual((queue['listed_sku'], queue['listed_asin'], queue['listed_platform']), ('AMZ-1', 'B0TESTASIN', 'amazon'))
        resolved = self.client.get('/api/lister/resolve?platform=amazon&asin=B0TESTASIN').get_json()
        self.assertEqual(resolved['upc'], '012345678905')
        self.assertEqual(self.client.post('/api/lister/links', json={'upc': UPC, 'platform': 'amazon'}).status_code, 400)
        self.assertEqual(self.client.post('/api/lister/links', json={'proposal_id': 77, 'platform': 'ebay', 'listing_id': '123456789'}).status_code, 404)

    def test_delete_link_restores_proposal_and_match(self):
        link = self.client.post('/api/lister/links', json={'proposal_id': 1, 'platform': 'ebay', 'listing_id': '335566778899'}).get_json()['link']
        res = self.client.delete(f"/api/lister/links/{link['id']}")
        self.assertEqual(res.status_code, 200, res.get_json())
        self.assertEqual(self.sql('listagent.db', 'SELECT status, listed_listing_id FROM listing_proposals WHERE id = 1')[0],
                         {'status': 'proposed', 'listed_listing_id': None})
        self.assertEqual(self.sql('listing_alerts.db', 'SELECT COUNT(*) AS n FROM listing_inventory_matches')[0]['n'], 0)
        self.assertEqual(self.client.get('/api/lister/links?upc=' + UPC).get_json()['links'], [])
        self.assertEqual(self.client.delete('/api/lister/links/999').status_code, 404)

    def test_events_endpoint_writes_to_the_proposal_timeline(self):
        res = self.client.post('/api/lister/events', json={'proposal_id': 1, 'event': 'helper_filled', 'note': 'ebay form',
                                                           'payload': {'filled': ['title', 'price']}})
        self.assertEqual(res.status_code, 200, res.get_json())
        event = self.sql('listagent.db', 'SELECT event, note, payload_json FROM listing_proposal_events WHERE proposal_id = 1')[0]
        self.assertEqual(event['event'], 'helper_filled')
        self.assertEqual(json.loads(event['payload_json']), {'filled': ['title', 'price']})
        self.assertEqual(self.client.post('/api/lister/events', json={'proposal_id': 1, 'event': 'other'}).status_code, 400)

    # -- update feed ------------------------------------------------------------------------

    def test_ping_reports_the_signed_in_user(self):
        res = self.client.get('/api/lister/ping', headers={'Cf-Access-Authenticated-User-Email': 'dan@example.com'}).get_json()
        self.assertEqual((res['success'], res['user'], res['version']), (True, 'dan@example.com', lister_routes.VERSION))

    def test_feed_serves_published_release_or_a_fallback_page(self):
        res = self.client.get('/lister/')
        self.assertEqual(res.status_code, 200)
        self.assertIn(b'No extension release has been published', res.data)
        self.assertEqual(self.client.get('/lister/latest.json').status_code, 404)

        feed = self.root / 'static' / 'lister'
        (feed / 'releases').mkdir(parents=True)
        (feed / 'index.html').write_text('<h1>Lister updates</h1>', encoding='utf-8')
        (feed / 'latest.json').write_text('{"release": "abc"}', encoding='utf-8')
        (feed / 'releases' / 'abc.zip').write_bytes(b'zip')
        index = self.client.get('/lister/')
        self.assertIn(b'Lister updates', index.data)
        index.close()
        latest = self.client.get('/lister/latest.json')
        self.assertEqual(latest.get_json(), {'release': 'abc'})
        self.assertEqual(latest.headers['Cache-Control'], 'no-store')
        latest.close()
        archive = self.client.get('/lister/releases/abc.zip')
        self.assertEqual(archive.data, b'zip')
        self.assertIn('immutable', archive.headers['Cache-Control'])
        archive.close()
        self.assertEqual(self.client.get('/lister/../lister_routes.py').status_code, 404)

    # -- queue-driven panel (0.2) -----------------------------------------------------------

    def queue_add(self, upc, title, added_at):
        with closing(sqlite3.connect(self.root / 'listagent.db')) as conn:
            conn.execute("INSERT INTO listing_queue (upc, title, status, added_at) VALUES (?, ?, 'queued', ?)", (upc, title, added_at))
            conn.commit()

    def test_pure_helpers_for_conditions_and_notes(self):
        used = lister_routes.condition_from_notes(['Small scratch on the lid', 'LT: x | EN: box opened'])
        self.assertEqual(used['condition'], 'USED_GOOD')
        self.assertIn('scratch', used['conditionDescription'])
        self.assertFalse(used['assumed'])
        fine = lister_routes.condition_from_notes(['Color checked, all good'], suffixed=True)
        self.assertEqual((fine['condition'], fine['assumed']), ('NEW_OTHER', True))
        self.assertEqual(lister_routes.note_text_variants('LT: dėžė pažeista | EN: box damaged'), 'box damaged')
        self.assertEqual(lister_routes.note_text_variants('LT: tik lietuviškai'), '')
        self.assertEqual(lister_routes.note_text_variants('plain note'), 'plain note')
        self.assertEqual(lister_routes.fill_fields({}, {}, upc='035886267162-1', base_url='https://x/')['upc'], '035886267162')
        self.assertEqual(lister_routes.fill_fields({}, {}, upc='035886267162-1', base_url='https://x/')['sku'], '035886267162-1')

    def test_queue_feed_is_per_store_oldest_first_with_store_links(self):
        self.queue_add(UPC + '-1', 'Lenox plate damaged', '2026-09-14T08:00:00')
        self.queue_add('012345678905', 'Other item', '2026-09-15T08:00:00')
        data = self.client.get('/api/lister/queue?platform=ebay', base_url='https://pi.example').get_json()
        self.assertTrue(data['success'], data)
        self.assertEqual([it['upc'] for it in data['items']], [UPC, UPC + '-1', '012345678905'])
        first = data['items'][0]
        self.assertEqual((first['status'], first['platform'], first['suffixed']), ('queued', 'ebay', False))
        self.assertTrue(first['alreadyOnStore'], 'ebayStore carries this UPC')
        self.assertEqual(first['storeUrl'], 'https://www.ebay.com/itm/112233445566')
        self.assertEqual(first['existing'][0]['listingId'], '112233445566')
        self.assertEqual(first['proposal']['ready'], True)
        self.assertEqual(data['items'][1]['baseUpc'], UPC)
        self.assertTrue(data['items'][1]['suffixed'])
        self.assertEqual(data['items'][1]['otherStatus'], 'queued')
        amazon = self.client.get('/api/lister/queue?platform=amazon').get_json()
        self.assertFalse(amazon['items'][0]['alreadyOnStore'])
        self.assertEqual(amazon['items'][0]['storeUrl'], '')
        self.assertEqual(self.client.get('/api/lister/queue?platform=etsy').status_code, 400)

    def test_skip_hides_per_store_and_leaves_the_queue_once_both_stores_are_resolved(self):
        res = self.client.post(f'/api/lister/queue/{UPC}/skip', json={'platform': 'ebay'})
        self.assertEqual(res.status_code, 200, res.get_json())
        self.assertEqual(res.get_json()['queue'], 'queued', 'still queued for Amazon')
        ebay = self.client.get('/api/lister/queue?platform=ebay').get_json()
        self.assertEqual([it['upc'] for it in ebay['items']], [])
        self.assertEqual(ebay['counts']['hidden'], 1)
        amazon = self.client.get('/api/lister/queue?platform=amazon').get_json()
        self.assertEqual(amazon['items'][0]['skipped'], {'ebay': True, 'amazon': False})
        self.assertEqual(amazon['items'][0]['otherStatus'], 'skipped')

        # Undo puts it back on the eBay list.
        self.client.post(f'/api/lister/queue/{UPC}/skip', json={'platform': 'ebay', 'undo': True})
        self.assertEqual([it['upc'] for it in self.client.get('/api/lister/queue?platform=ebay').get_json()['items']], [UPC])

        # Skipped on both stores -> removed from the Listing Agent queue (nothing was listed).
        self.client.post(f'/api/lister/queue/{UPC}/skip', json={'platform': 'ebay'})
        res = self.client.post(f'/api/lister/queue/{UPC}/skip', json={'platform': 'amazon'})
        self.assertEqual(res.get_json()['queue'], 'removed')
        self.assertEqual(self.sql('listagent.db', 'SELECT status FROM listing_queue WHERE upc = ?', (UPC,))[0]['status'], 'removed')
        self.assertEqual(self.client.get('/api/lister/queue?platform=amazon').get_json()['items'], [])
        # ...and an undo on either store revives the queue row.
        self.client.post(f'/api/lister/queue/{UPC}/skip', json={'platform': 'amazon', 'undo': True})
        self.assertEqual(self.sql('listagent.db', 'SELECT status FROM listing_queue WHERE upc = ?', (UPC,))[0]['status'], 'queued')
        self.assertEqual(self.client.post(f'/api/lister/queue/{UPC}/skip', json={'platform': 'x'}).status_code, 400)

    def test_link_marks_items_to_list_and_finishes_the_queue_when_the_other_store_was_skipped(self):
        self.client.post(f'/api/lister/queue/{UPC}/skip', json={'platform': 'amazon'})
        res = self.client.post('/api/lister/links', json={'upc': UPC, 'platform': 'ebay', 'listing_id': '335566778899', 'sku': UPC})
        self.assertEqual(res.status_code, 201, res.get_json())
        steps = ' '.join(res.get_json()['link']['effects']['steps'])
        self.assertIn('Items to List marked listed on ebay', steps)
        self.assertIn('queue item done', steps)
        self.assertEqual(self.bol_marks, [('ebay', UPC)])
        row = self.sql('listagent.db', 'SELECT status, listed_ebay_at, listed_sku FROM listing_queue WHERE upc = ?', (UPC,))[0]
        self.assertEqual(row['status'], 'done')
        self.assertTrue(row['listed_ebay_at'])
        self.assertEqual(row['listed_sku'], UPC)
        # The listing log carries the lister source, which the sale trace accepts.
        log = self.sql('listinglog.db', 'SELECT upc, platform, sku, listing_id, source FROM listing_log')
        self.assertEqual(log[-1], {'upc': UPC, 'platform': 'ebay', 'sku': UPC, 'listing_id': '335566778899', 'source': 'lister'})
        # Listed on eBay: it moves to the eBay list's done section and drops off Amazon (skipped there).
        ebay = self.client.get('/api/lister/queue?platform=ebay').get_json()
        self.assertEqual((ebay['items'][0]['status'], ebay['items'][0]['listedAt'] != ''), ('listed', True))
        self.assertEqual(ebay['items'][0]['links'][0]['listing_id'], '335566778899')
        self.assertEqual(self.client.get('/api/lister/queue?platform=amazon').get_json()['items'], [])

    def test_mark_existing_links_a_store_listing_without_the_page(self):
        entry = {'listingId': '112233445566', 'sku': 'office', 'upc': UPC, 'title': 'Old plate listing'}
        res = self.client.post(f'/api/lister/queue/{UPC}/mark-existing', json={'platform': 'ebay', 'entry': entry})
        self.assertEqual(res.status_code, 201, res.get_json())
        link = res.get_json()['link']
        self.assertEqual((link['listing_id'], link['sku'], link['upc']), ('112233445566', 'office', UPC))
        self.assertIn('already on the store', link['note'])
        again = self.client.post(f'/api/lister/queue/{UPC}/mark-existing', json={'platform': 'ebay', 'entry': entry})
        self.assertEqual((again.status_code, again.get_json()['duplicate']), (200, True))

    def test_detail_merges_inventory_prep_notes_voice_text_and_photos(self):
        (self.root / 'static' / 'listingagent_uploads').mkdir()
        self.detail_payload = {
            'title': 'Lenox Butterfly Meadow Plate from inventory record that is longer than eighty characters for sure',
            'images': ['https://cdn.example/catalog.jpg'], 'defect': 'chip on rim',
            'inventory': {'total_quantity': 3, 'positions': ['A-3', 'B-1'], 'rows': []},
            'bol': {'description': 'Lenox plate', 'avg_cost': '4.5', 'lot_number': 'L-1'},
            'ebay_store': {'listings': [{'itemId': '112233445566'}]}, 'amazon_store': {'listings': []},
            'prep': {'notes': [{'id': 5, 'note': 'LT: nuotrauka | EN: box opened, item unused', 'created_at': '2026-09-15'}],
                     'images': ['https://pi.example/static/items_prep/p1.jpg'],
                     'voice_notes': [{'id': 41, 'url': 'https://pi.example/static/items_prep/v.webm', 'created_at': '2026-09-15'},
                                     {'id': 42, 'url': 'https://pi.example/static/items_prep/w.webm', 'created_at': '2026-09-15'}],
                     'videos': []},
        }
        with closing(sqlite3.connect(self.root / 'bol.db')) as conn:
            conn.execute('''CREATE TABLE voice_note_analysis (media_id INTEGER PRIMARY KEY, fingerprint TEXT, lithuanian TEXT,
                            english TEXT, updated_at REAL, lease_until REAL, token TEXT, error TEXT)''')
            conn.execute("INSERT INTO voice_note_analysis VALUES (41, 'f', 'subraižytas', 'scratched on the back', 1, 0, '', '')")
            conn.commit()
        with self.app.app_context():
            listing_queue._listagent_add_photo(UPC + '-1', image_path='listingagent_uploads/own.jpg')
        data = self.client.get(f'/api/lister/queue/{UPC}-1', base_url='https://pi.example').get_json()
        self.assertTrue(data['success'], data)
        item = data['item']
        self.assertEqual((item['upc'], item['baseUpc'], item['suffixed']), (UPC + '-1', UPC, True))
        fields = item['fields']
        self.assertEqual(len(fields['title']), 80)
        self.assertEqual((fields['sku'], fields['upc'], fields['quantity']), (UPC + '-1', UPC, 1))
        self.assertEqual(fields['source'], 'inventory', 'no proposal exists for the suffixed unit')
        self.assertEqual(fields['condition'], 'USED_GOOD')
        self.assertIn('chip on rim', fields['conditionDescription'])
        self.assertIn('box opened, item unused', fields['conditionDescription'])
        self.assertIn('scratched on the back', fields['conditionDescription'])
        self.assertEqual(fields['images'][0], 'https://pi.example/static/listingagent_uploads/own.jpg')
        self.assertEqual([p['source'] for p in item['photos']], ['listing', 'prep', 'catalog'])
        self.assertEqual([v['status'] for v in item['voiceNotes']], ['complete', 'pending'])
        self.assertEqual(item['voiceNotes'][0]['english'], 'scratched on the back')
        self.assertEqual(item['notes'][0]['english'], 'box opened, item unused')
        self.assertEqual(item['inventory']['positions'], ['A-3', 'B-1'])
        self.assertEqual(item['cost'], 4.5)
        self.assertIn('/items-to-list/mobile-photos?upc=' + UPC + '-1', item['mobilePhotosUrl'])
        self.assertEqual(item['aiPhotoPrompt'], lister_routes.DEFAULT_AI_PHOTO_PROMPT)

        # With a ready proposal the fields come from it; prep notes still fill an empty condition note.
        data = self.client.get(f'/api/lister/queue/{UPC}', base_url='https://pi.example').get_json()['item']
        self.assertEqual(data['fields']['source'], 'proposal')
        self.assertEqual(data['fields']['title'], 'Lenox Butterfly Meadow Dinner Plate')
        self.assertEqual(data['fields']['conditionDescription'], 'Open box, never used')
        self.assertEqual(data['proposal']['id'], 1)

    def test_prepare_builds_a_proposal_in_the_background_once(self):
        import time as _time
        res = self.client.post('/api/lister/queue/012345678905/prepare', json={}, base_url='https://pi.example')
        self.assertEqual(res.status_code, 200, res.get_json())
        self.assertEqual(res.get_json()['status'], 'ready', 'proposal 2 already carries a title')
        res = self.client.post('/api/lister/queue/012345678905/prepare', json={'force': True}, base_url='https://pi.example')
        self.assertEqual(res.get_json()['status'], 'preparing')
        for _ in range(50):
            if self.built:
                break
            _time.sleep(0.05)
        self.assertEqual(self.built[0][0], '012345678905')
        self.assertEqual(self.built[0][2], 'https://pi.example/')
        for _ in range(50):
            if not self.lister._job_state('012345678905').get('running'):
                break
            _time.sleep(0.05)
        self.assertEqual(self.lister._job_state('012345678905'), {'running': False, 'error': '', 'startedAt': self.lister._job_state('012345678905')['startedAt']})
        self.assertEqual(self.client.get('/api/lister/queue/012345678905').get_json()['item']['fields']['title'], 'Built title')

    def test_ai_photo_edit_stores_the_result_as_a_listing_photo(self):
        uploads = self.root / 'static' / 'listingagent_uploads'
        uploads.mkdir()
        (uploads / 'own.jpg').write_bytes(b'\xff\xd8jpegbytes')
        calls = []

        class FakeResponse:
            status_code = 200

            def json(self):
                return {'data': [{'b64_json': 'aGVsbG8='}], 'usage': {'input_tokens': 1}}

        def fake_post(url, **kwargs):
            calls.append((url, kwargs))
            return FakeResponse()

        import requests
        with patch.object(requests, 'post', fake_post), patch.dict(os.environ, {'OPENAI_API_KEY': 'test-openai'}):
            res = self.client.post('/api/lister/photos/ai', json={'upc': UPC, 'url': 'https://pi.example/static/listingagent_uploads/own.jpg',
                                                                'prompt': ''}, base_url='https://pi.example')
        self.assertEqual(res.status_code, 201, res.get_json())
        photo = res.get_json()['photo']
        self.assertTrue(photo['url'].startswith('https://pi.example/static/listingagent_uploads/' + UPC + '_ai_'))
        self.assertEqual(photo['source'], 'ai')
        self.assertEqual(res.get_json()['prompt'], lister_routes.DEFAULT_AI_PHOTO_PROMPT)
        self.assertEqual((uploads / photo['name']).read_bytes(), b'hello')
        self.assertEqual(calls[0][0], 'https://api.openai.com/v1/images/edits')
        self.assertEqual(calls[0][1]['data']['model'], 'gpt-image-1')
        self.assertEqual(calls[0][1]['files'][0][1][0], 'own.jpg')
        self.assertEqual(calls[0][1]['headers']['Authorization'], 'Bearer test-openai')
        self.assertEqual(self.sql('listagent.db', 'SELECT upc, image_path FROM listing_photos')[0], {'upc': UPC, 'image_path': 'listingagent_uploads/' + photo['name']})
        # The photo is now part of the item's detail as an AI photo, and can be fetched for drag-and-drop.
        detail = self.client.get(f'/api/lister/queue/{UPC}', base_url='https://pi.example').get_json()['item']
        self.assertEqual(detail['photos'][0]['source'], 'ai')
        fetched = self.client.get('/api/lister/photos/fetch?url=' + photo['url'], base_url='https://pi.example').get_json()
        self.assertEqual((fetched['name'], fetched['mime'], fetched['base64']), (photo['name'], 'image/jpeg', 'aGVsbG8='))
        with patch.dict(os.environ, {'OPENAI_API_KEY': ''}):
            self.assertEqual(self.client.post('/api/lister/photos/ai', json={'upc': UPC, 'url': photo['url']}).status_code, 503)
        self.assertEqual(self.client.get('/api/lister/photos/fetch?url=https://pi.example/static/../lister_routes.py', base_url='https://pi.example').status_code, 400)

    def test_voice_analysis_goes_through_the_shared_service(self):
        import voice_note_routes
        with patch.object(voice_note_routes.VoiceNotes, 'analyze', lambda self, media_id, reanalyze=False: {'english': 'ok', 'lithuanian': 'gerai', 'status': 'complete', 'media': media_id, 'reanalyze': reanalyze}):
            res = self.client.post('/api/lister/voice/41/analyze', json={'reanalyze': True})
        self.assertEqual(res.status_code, 200, res.get_json())
        self.assertEqual(res.get_json()['analysis'], {'english': 'ok', 'lithuanian': 'gerai', 'status': 'complete', 'media': 41, 'reanalyze': True})

        def failing(self, media_id, reanalyze=False):
            raise voice_note_routes.VoiceError('Voice note not found.', 404)
        with patch.object(voice_note_routes.VoiceNotes, 'analyze', failing):
            self.assertEqual(self.client.post('/api/lister/voice/99/analyze', json={}).status_code, 404)

    def test_ledger_lists_links_with_the_sales_that_traced_back(self):
        self.client.post('/api/lister/links', json={'upc': UPC + '-1', 'platform': 'ebay', 'listing_id': '335566778899', 'sku': UPC + '-1', 'title': 'Plate'})
        with closing(sqlite3.connect(self.root / 'sold.db')) as conn:
            conn.execute('''CREATE TABLE orders (id INTEGER PRIMARY KEY, order_id TEXT, store TEXT, item_id TEXT, sku TEXT, barcode TEXT,
                            source_upc TEXT, listing_sku TEXT, listing_listing_id TEXT, listing_asin TEXT, listing_trace_id TEXT,
                            paid_time TEXT, shipped_time TEXT, title TEXT, quantity INTEGER, price REAL)''')
            conn.execute("INSERT INTO orders (order_id, store, item_id, sku, barcode, source_upc, listing_sku, paid_time, title, quantity, price) VALUES ('O-1', 'ebay', '335566778899', ?, ?, ?, ?, '2026-09-16', 'Plate', 1, 24.5)", (UPC + '-1', UPC, UPC + '-1', UPC + '-1'))
            conn.execute("INSERT INTO orders (order_id, store, item_id, sku, barcode, paid_time, title, quantity, price) VALUES ('O-2', 'ebay', '999', 'x', 'y', '2026-09-16', 'Other', 1, 1)")
            conn.commit()
        data = self.client.get('/api/lister/ledger?upc=' + UPC + '-1').get_json()
        self.assertTrue(data['success'], data)
        self.assertEqual(len(data['links']), 1)
        entry = data['links'][0]
        self.assertEqual(entry['upc'], UPC + '-1')
        self.assertEqual([o['order_id'] for o in entry['sales']], ['O-1'])
        self.assertEqual([o['order_id'] for o in entry['traced']], ['O-1'])
        page = self.client.get('/lister-ledger')
        self.assertEqual(page.status_code, 200)
        self.assertIn(b'Lister Ledger', page.data)

    def test_qr_endpoint_renders_or_explains(self):
        res = self.client.get('/api/lister/qr?text=https://pi.example/items-to-list/mobile-photos?upc=1')
        self.assertIn(res.status_code, (200, 501))
        if res.status_code == 200:
            self.assertIn(b'<svg', res.data)
            self.assertEqual(res.headers['Content-Type'], 'image/svg+xml; charset=utf-8')
        self.assertEqual(self.client.get('/api/lister/qr').status_code, 400)


if __name__ == '__main__':
    unittest.main()
