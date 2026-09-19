"""Sweet Shelves Lister server side: proposal feed, selection, confirm-link bookkeeping, update feed.

Runs against temporary databases through the real sweetshelves helpers; nothing reaches a store.
"""
from contextlib import closing
import datetime
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import time
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
        # Windows keeps a handle on an open sqlite file, so the temp folder cannot always be
        # removed; a failed cleanup is the harness tidying up, never a fact about the code.
        self.folder = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
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

        self.build_error = None

        def fake_build(upc, *, actor='agent', base_url=None):
            self.built.append((upc, actor, base_url))
            if self.build_error:
                raise self.build_error
            with closing(sqlite3.connect(self.root / 'listagent.db')) as conn:
                conn.execute('''INSERT INTO listing_proposals (upc, status, proposal_json, created_at, updated_at)
                                VALUES (?, 'proposed', ?, '2026-09-16T10:00:00', '2026-09-16T10:00:00')''',
                             (upc, json.dumps({'title': 'Built title', 'price': 12.5, 'quantity': 1, 'condition': 'NEW_OTHER'})))
                conn.commit()

        self.amazon = {'search': {'success': True, 'results': [{'asin': 'B0TESTASIN', 'title': 'Lenox plate', 'brand': 'Lenox'}]},
                       'restriction': {'success': True, 'restriction': {'checked': True, 'restricted': True, 'reasons': [{'message': 'Approval required for Lenox'}]}}}
        self.amazon_calls = []

        def fake_amazon_search():
            from flask import jsonify, request as flask_request
            self.amazon_calls.append(('search', dict(flask_request.args)))
            return jsonify(self.amazon['search'])

        def fake_amazon_restriction():
            from flask import jsonify, request as flask_request
            self.amazon_calls.append(('restriction', dict(flask_request.args)))
            return jsonify(self.amazon['restriction'])

        # Telegram stand-in for "+ Photo link": one enabled chat, one switched off.
        self.telegram_rows = [{'chat_id': '111', 'display_name': 'Danka', 'enabled': 1},
                              {'chat_id': '111', 'display_name': 'Danka (backups)', 'enabled': 1},
                              {'chat_id': '222', 'display_name': 'Off', 'enabled': 0}]
        self.telegram_sent = []
        self.telegram_result = (True, {'message_id': 7})
        self.telegram_deleted = []
        # Logins that linked their own Telegram on the Telegram page.
        self.paired = {}

        def fake_telegram_send(chat_id, text, disable_notification=True):
            self.telegram_sent.append((chat_id, text, disable_notification))
            return self.telegram_result

        self.lister = lister_routes.register(self.app, {
            '_telegram_send_message': fake_telegram_send,
            '_telegram_collect_recipient_rows': lambda: self.telegram_rows,
            '_telegram_get_bot_token': lambda: 'test-bot-token',
            '_telegram_chat_for_email': lambda email: self.paired.get(email),
            'api_listingagent_amazon_catalog_search': fake_amazon_search,
            'api_listingagent_amazon_restriction_check': fake_amazon_restriction,
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
        # Prep passed it good and wrote nothing: NEW on eBay, new_new on Amazon, no guessing.
        new = lister_routes.condition_from_notes([], prep_status='good')
        self.assertEqual((new['condition'], new['assumed'], new['conditionDescription']), ('NEW', False, ''))
        self.assertEqual(lister_routes.AMAZON_CONDITIONS[new['condition']], 'new_new')
        # A note, a suffix or any other prep verdict keeps the assumed default.
        self.assertEqual(lister_routes.condition_from_notes(['Sealed, two in the box'], prep_status='good')['condition'], 'NEW_OTHER')
        self.assertEqual(lister_routes.condition_from_notes([], prep_status='good', suffixed=True)['condition'], 'NEW_OTHER')
        self.assertEqual(lister_routes.condition_from_notes([], prep_status='unchecked')['condition'], 'NEW_OTHER')
        self.assertEqual(lister_routes.note_text_variants('LT: dėžė pažeista | EN: box damaged'), 'box damaged')
        self.assertEqual(lister_routes.note_text_variants('LT: tik lietuviškai'), '')
        self.assertEqual(lister_routes.note_text_variants('plain note'), 'plain note')
        self.assertEqual(lister_routes.fill_fields({}, {}, upc='035886267162-1', base_url='https://x/')['upc'], '035886267162')
        self.assertEqual(lister_routes.fill_fields({}, {}, upc='035886267162-1', base_url='https://x/')['sku'], '035886267162-1')

    def test_good_unit_with_no_notes_is_new_on_both_stores(self):
        """Item Prep's own verdict answers the condition question: no note, nothing to confirm."""
        self.detail_payload = {'title': 'Lenox Butterfly Meadow Plate', 'images': [], 'defect': '',
                               'inventory': {'total_quantity': 2, 'positions': [], 'rows': []},
                               'prep': {'notes': [], 'images': [], 'voice_notes': [], 'videos': []}}
        clean = '012345678905'
        with closing(sqlite3.connect(self.root / 'bol.db')) as conn:
            conn.execute('CREATE TABLE items_prep_status (id INTEGER PRIMARY KEY, upc TEXT, lot_number TEXT, status TEXT, reason TEXT, note TEXT, updated_at TEXT, quantity INTEGER)')
            for upc in (clean, UPC):
                conn.execute("INSERT INTO items_prep_status (upc, lot_number, status, reason, updated_at, quantity) VALUES (?, 'L-1', 'good', '', '2026-09-15', 2)", (upc,))
            conn.commit()
        fields = self.client.get(f'/api/lister/queue/{clean}', base_url='https://pi.example').get_json()['item']['fields']
        self.assertEqual(fields['condition'], 'NEW')
        self.assertEqual(fields['amazonCondition'], 'new_new')
        self.assertEqual(fields.get('conditionDescription', ''), '')
        # The seeded proposal carries "Open box, never used": a note about this unit, so it stays as it is.
        noted = self.client.get(f'/api/lister/queue/{UPC}', base_url='https://pi.example').get_json()['item']['fields']
        self.assertEqual((noted['condition'], noted['amazonCondition']), ('NEW_OTHER', 'new_open_box'))

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

    def test_panel_reports_its_open_store_to_the_same_person_only(self):
        dan = {'Cf-Access-Authenticated-User-Email': 'dan@example.com'}
        self.assertFalse(self.client.get('/api/lister/panel', headers=dan).get_json()['active'], 'no panel yet')
        res = self.client.post('/api/lister/panel', json={'platform': 'ebay'}, headers=dan)
        self.assertEqual(res.status_code, 200, res.get_json())
        state = self.client.get('/api/lister/panel', headers=dan).get_json()
        self.assertEqual((state['active'], state['platform']), (True, 'ebay'))
        self.assertEqual(self.client.get('/api/lister/panel', headers=dan).headers['Cache-Control'], 'no-store')
        self.assertFalse(self.client.get('/api/lister/panel', headers={'Cf-Access-Authenticated-User-Email': 'x@example.com'}).get_json()['active'],
                         'a panel of another person says nothing about mine')
        self.client.post('/api/lister/panel', json={'platform': 'amazon'}, headers=dan)
        self.assertEqual(self.client.get('/api/lister/panel', headers=dan).get_json()['platform'], 'amazon')
        # The setting switched off, or the panel closed, and the "+" is the plain one again.
        self.client.post('/api/lister/panel', json={'platform': 'amazon', 'follow': False}, headers=dan)
        self.assertFalse(self.client.get('/api/lister/panel', headers=dan).get_json()['active'])
        self.client.post('/api/lister/panel', json={'platform': 'amazon', 'open': False}, headers=dan)
        self.assertFalse(self.client.get('/api/lister/panel', headers=dan).get_json()['active'])
        # A heartbeat that stopped coming means the panel went away without saying so.
        self.client.post('/api/lister/panel', json={'platform': 'ebay'}, headers=dan)
        with patch.object(lister_routes.time, 'time', return_value=time.time() + 120):
            self.assertFalse(self.client.get('/api/lister/panel', headers=dan).get_json()['active'])
        self.assertEqual(self.client.post('/api/lister/panel', json={'platform': 'etsy'}, headers=dan).status_code, 400)

    def test_plus_for_the_open_store_adds_to_that_store_only(self):
        new = '012345678905'
        stores = lambda: self.client.post('/api/lister/queue-stores', json={'upcs': [new]}).get_json()['items'][new]
        on = lambda p, **kw: self.client.post(f'/api/lister/queue/{new}/store', json={'platform': p, 'on': True, 'only': True, **kw}).get_json()['state']
        off = lambda p: self.client.post(f'/api/lister/queue/{new}/store', json={'platform': p, 'on': False}).get_json()['state']
        listed_on = lambda p: [it['upc'] for it in self.client.get(f'/api/lister/queue?platform={p}').get_json()['items'] if it['status'] == 'queued']
        self.assertEqual(stores(), {'queue': '', 'ebay': 'off', 'amazon': 'off'})
        # The eBay list is open: "+" queues it (the Items to List add) and keeps it off Amazon.
        self.queue_add(new, 'Other item', '2026-09-18T08:00:00')
        self.assertEqual(on('ebay', fresh=True), {'queue': 'queued', 'ebay': 'on', 'amazon': 'off'})
        self.assertIn(new, listed_on('ebay'))
        self.assertNotIn(new, listed_on('amazon'))
        # The panel moves to Amazon: the same "+" now puts it on the Amazon list as well.
        self.assertEqual(on('amazon'), {'queue': 'queued', 'ebay': 'on', 'amazon': 'on'})
        self.assertIn(new, listed_on('amazon'))
        # Pressed again it comes off that store only; off both, it leaves the queue.
        self.assertEqual(off('ebay'), {'queue': 'queued', 'ebay': 'off', 'amazon': 'on'})
        self.assertEqual(off('amazon'), {'queue': 'removed', 'ebay': 'off', 'amazon': 'off'})
        # Back for Amazon only: the old row comes back without the eBay side.
        self.assertEqual(on('amazon'), {'queue': 'queued', 'ebay': 'off', 'amazon': 'on'})
        self.assertEqual(self.client.post(f'/api/lister/queue/{new}/store', json={'platform': 'etsy', 'on': True}).status_code, 400)

    def test_a_submitted_listing_leaves_the_store_list_for_the_listed_list_and_undo_brings_it_back(self):
        unit = '012345678905'
        self.queue_add(unit, 'Other item', '2026-09-18T08:00:00')
        on = lambda p: [it['upc'] for it in self.client.get(f'/api/lister/queue?platform={p}').get_json()['items'] if it['status'] == 'queued']
        self.assertIn(unit, on('ebay'))
        res = self.client.post(f'/api/lister/queue/{unit}/submitted', json={'platform': 'ebay', 'sku': unit})
        self.assertEqual(res.status_code, 200, res.get_json())
        ebay = self.client.get('/api/lister/queue?platform=ebay').get_json()['items']
        listed = [it for it in ebay if it['upc'] == unit]
        self.assertEqual(listed[0]['status'], 'listed', 'off the eBay list, on its Listed list')
        self.assertTrue(listed[0]['submitted'], 'the item number is still to come')
        self.assertIn(unit, on('amazon'), 'still waiting on Amazon')
        self.assertEqual(self.client.post('/api/lister/queue-stores', json={'upcs': [unit]}).get_json()['items'][unit]['ebay'], 'listed')
        self.assertIn(('ebay', unit), self.bol_marks, 'Items to List ticks eBay')
        # Undo: back on the eBay list.
        self.client.post(f'/api/lister/queue/{unit}/submitted', json={'platform': 'ebay', 'undo': True})
        self.assertIn(unit, on('ebay'))
        self.assertEqual(self.client.post('/api/lister/queue-stores', json={'upcs': [unit]}).get_json()['items'][unit]['ebay'], 'on')
        self.assertEqual(self.client.post(f'/api/lister/queue/{unit}/submitted', json={'platform': 'etsy'}).status_code, 400)

    def test_a_store_listing_with_our_sku_counts_as_listed_even_without_a_upc(self):
        unit = '012345678905-1'
        self.queue_add(unit, 'Unit one', '2026-09-18T08:00:00')
        with closing(sqlite3.connect(self.root / 'ebayStore.db')) as conn:
            conn.execute("INSERT INTO INVENTORY (Title, ItemID, SKU, UPC, List_State) VALUES ('Unit one', '998877665544', ?, '', 'Active')", (unit,))
            conn.commit()
        ebay = {it['upc']: it for it in self.client.get('/api/lister/queue?platform=ebay').get_json()['items']}
        self.assertEqual(ebay[unit]['status'], 'listed', 'our SKU is live on eBay: listed, off the list')
        self.assertEqual(ebay[unit]['ownListing']['listingId'], '998877665544')
        self.assertFalse(ebay[unit]['alreadyOnStore'], 'not "already on the store": it is this unit\'s own listing')
        self.assertEqual(self.client.post('/api/lister/queue-stores', json={'upcs': [unit]}).get_json()['items'][unit]['ebay'], 'listed')
        # Somebody else's listing of the same product (another SKU) does not count as ours.
        self.assertTrue(ebay[UPC]['alreadyOnStore'])
        self.assertEqual(ebay[UPC]['status'], 'queued')

    def test_queue_stamp_moves_with_every_change_so_the_panel_reloads(self):
        dan = {'Cf-Access-Authenticated-User-Email': 'dan@example.com'}
        stamp = lambda: self.client.post('/api/lister/panel', json={'platform': 'ebay'}, headers=dan).get_json()['queueStamp']
        first = stamp()
        self.assertEqual(self.client.get('/api/lister/queue?platform=ebay').get_json()['stamp'], first, 'the list carries the same stamp')
        self.assertEqual(stamp(), first, 'nothing changed, nothing to reload')
        self.queue_add('012345678905', 'Added on Items to List', '2026-09-18T09:00:00')
        added = stamp()
        self.assertNotEqual(added, first)
        self.client.post('/api/lister/queue/012345678905/store', json={'platform': 'ebay', 'on': False})
        skipped = stamp()
        self.assertNotEqual(skipped, added)
        self.client.post('/api/lister/queue/012345678905/store', json={'platform': 'ebay', 'on': True})
        self.assertNotEqual(stamp(), skipped, 'put back on the list')
        self.assertIn('queueStamp', self.client.get('/api/lister/panel', headers=dan).get_json())

    def test_listed_on_both_stores_shows_both_even_after_the_queue_row_was_cleared(self):
        with closing(sqlite3.connect(self.root / 'listagent.db')) as conn:
            conn.execute("""INSERT INTO listing_queue (upc, title, status, added_at, listed_platform, listed_ebay_at, listed_amazon_at)
                            VALUES ('840115641220', 'Both', 'removed', '2026-03-18', 'amazon', '2026-03-18T16:10', '2026-03-27T13:58')""")
            conn.execute("INSERT INTO listing_queue (upc, title, status, added_at) VALUES ('042648477752', 'eBay only', 'queued', '2026-03-18')")
            conn.commit()
        self.client.post('/api/lister/links', json={'upc': '042648477752', 'platform': 'ebay', 'listing_id': '335566778899', 'sku': '042648477752'})
        items = self.client.post('/api/lister/queue-stores', json={'upcs': ['840115641220', '042648477752']}).get_json()['items']
        self.assertEqual((items['840115641220']['ebay'], items['840115641220']['amazon']), ('listed', 'listed'))
        self.assertEqual((items['042648477752']['ebay'], items['042648477752']['amazon']), ('listed', 'on'))

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

    def test_linking_a_listing_the_store_already_had_is_not_counted_as_listed_by_the_panel(self):
        entry = {'listingId': '112233445566', 'sku': 'office', 'upc': UPC, 'title': 'Old plate listing'}
        self.client.post(f'/api/lister/queue/{UPC}/mark-existing', json={'platform': 'ebay', 'entry': entry})
        own = self.client.post('/api/lister/links', json={'upc': '012345678905', 'platform': 'amazon', 'sku': 'AMZ-1', 'asin': 'B000TEST01'})
        self.assertEqual(own.status_code, 201, own.get_json())
        self.assertEqual(own.get_json()['link']['kind'], 'listed')
        kinds = {l['sku'] or l['listing_id']: l['kind'] for l in self.client.get('/api/lister/links').get_json()['links']}
        self.assertEqual(kinds, {'office': 'existing', 'AMZ-1': 'listed'})
        listed = self.client.get('/api/lister/links?kind=listed').get_json()['links']
        self.assertEqual([l['sku'] for l in listed], ['AMZ-1'])
        existing = self.client.get('/api/lister/links?kind=existing').get_json()['links']
        self.assertEqual([l['listing_id'] for l in existing], ['112233445566'])

    def test_amazon_is_asked_about_our_sku_when_the_synced_table_has_nothing(self):
        # The store sync runs once a day: a listing made today is only visible through a live ask.
        new = '012345678905'
        self.queue_add(new, 'Amelia set', '2026-09-18T08:00:00')
        with closing(sqlite3.connect(self.root / 'amazonStore.db')) as conn:
            conn.execute("""CREATE TABLE ITEMS (ID INTEGER PRIMARY KEY, ASIN TEXT, SKU TEXT, TITLE TEXT, PRICE REAL, QUANTITY INTEGER,
                            STATUS TEXT, IMAGE TEXT, UPC TEXT, CONDITION TEXT, FULFILLMENT_CHANNEL TEXT, LAST_UPDATED TEXT)""")
            conn.commit()
        calls = []

        def fake(sku):
            calls.append(sku)
            return {'asin': 'B0971PMYLN', 'title': 'Amelia set', 'condition': 'new_new', 'state': 'Active'} if sku == new else None
        self.lister.amazon_listing_by_sku = fake
        stores = lambda upc: self.client.post('/api/lister/queue-stores', json={'upcs': [upc]}).get_json()['items'][upc]
        self.assertEqual(stores(new)['amazon'], 'listed')
        self.assertEqual(calls, [new], 'one call for the unit code (the base is the same code)')
        # Remembered in the synced table, so nothing asks Amazon again - not even after the cache is gone.
        with closing(sqlite3.connect(self.root / 'amazonStore.db')) as conn:
            rows = conn.execute("SELECT ASIN, SKU, UPC, STATUS FROM ITEMS").fetchall()
        self.assertEqual(rows, [('B0971PMYLN', new, new, 'Active')])
        self.lister._live_sku_cache.clear()
        self.assertEqual(stores(new)['amazon'], 'listed')
        self.assertEqual(calls, [new])
        # The panel's own list for Amazon shows it as listed there as well.
        items = {it['upc']: it['status'] for it in self.client.get('/api/lister/queue?platform=amazon').get_json()['items']}
        self.assertEqual(items.get(new), 'listed')
        # Not on Amazon: asked once, the miss is cached, the item stays on the list.
        other = '042648477752'
        self.queue_add(other, 'Not there', '2026-09-18T09:00:00')
        self.assertEqual(stores(other)['amazon'], 'on')
        self.assertEqual(stores(other)['amazon'], 'on')
        self.assertEqual(calls, [new, other], 'a miss is asked about once per cache window')
        # A store hiccup never breaks the answer.
        self.lister._live_sku_cache.clear()

        def broken(sku):
            raise RuntimeError('SP-API down')
        self.lister.amazon_listing_by_sku = broken
        self.assertEqual(stores(other)['amazon'], 'on')

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
            conn.execute('CREATE TABLE items_prep_status (id INTEGER PRIMARY KEY, upc TEXT, lot_number TEXT, status TEXT, reason TEXT, note TEXT, updated_at TEXT, quantity INTEGER)')
            conn.execute("INSERT INTO items_prep_status (upc, lot_number, status, reason, updated_at, quantity) VALUES (?, 'L-1', 'bad', 'chip', '2026-09-15', 1)", (UPC + '-1',))
            conn.commit()
        with self.app.app_context():
            listing_queue._listagent_add_photo(UPC + '-1', image_path='listingagent_uploads/own.jpg')
            listing_queue._listagent_add_photo(UPC + '-1', image_path='listingagent_uploads/x_ai_1.jpg', original_filename='ai:own.jpg')
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
        self.assertEqual(fields['conditionDescriptionSource'], 'notes')
        self.assertEqual((item['prepStatus']['status'], item['prepStatus']['reason']), ('bad', 'chip'))
        self.assertEqual(item['gate'], {'prepQty': 1, 'rackQty': 3, 'liveEbay': 1, 'liveAmazon': 0, 'listable': 1, 'mismatch': True})
        self.assertEqual(fields['images'][0], 'https://pi.example/static/listingagent_uploads/x_ai_1.jpg')
        self.assertEqual([p['source'] for p in item['photos']], ['ai', 'listing', 'prep', 'catalog'])
        self.assertEqual([p['from'] for p in item['photos']], ['own.jpg', '', '', ''], 'an AI photo names the photo it was made from')
        self.assertEqual([v['status'] for v in item['voiceNotes']], ['complete', 'pending'])
        self.assertEqual(item['voiceNotes'][0]['english'], 'scratched on the back')
        self.assertEqual(item['notes'][0]['english'], 'box opened, item unused')
        self.assertEqual(item['inventory']['positions'], ['A-3', 'B-1'])
        self.assertEqual(item['cost'], 4.5)
        self.assertIn('/items-to-list/mobile-photos?upc=' + UPC + '-1', item['mobilePhotosUrl'])
        self.assertIn('camera=1', item['mobilePhotosUrl'], 'the QR code has to land in the camera')
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
        # A gate-blocked proposal is an answer, not something to keep polling for.
        with closing(sqlite3.connect(self.root / 'listagent.db')) as conn:
            conn.execute("INSERT INTO listing_queue (upc, title, status, added_at) VALUES ('000000000099', 'Blocked thing', 'queued', '2026-09-17T08:00:00')")
            conn.execute('''INSERT INTO listing_proposals (upc, status, proposal_json, flags_json, created_at, updated_at)
                            VALUES ('000000000099', 'blocked', '{}', ?, '2026-09-17T08:00:00', '2026-09-17T08:00:00')''',
                         (json.dumps([{'level': 'block', 'code': 'traceable', 'message': 'On a shelf: No rack row with quantity for this barcode'}]),))
            conn.commit()
        blocked = self.client.post('/api/lister/queue/000000000099/prepare', json={}, base_url='https://pi.example').get_json()
        self.assertEqual((blocked['status'], blocked['reasons']), ('blocked', ['On a shelf: No rack row with quantity for this barcode']))
        self.assertEqual(len(self.built), 1, 'no rebuild for a blocked proposal unless forced')

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
        self.assertEqual(calls[0][1]['data']['model'], 'gpt-image-2')
        self.assertEqual(res.get_json()['model'], 'gpt-image-2')
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
        # The panel's model picker: each listed model goes through, anything else is refused before any call.
        for model in lister_routes.OPENAI_IMAGE_MODELS:
            with patch.object(requests, 'post', fake_post), patch.dict(os.environ, {'OPENAI_API_KEY': 'test-openai'}):
                picked = self.client.post('/api/lister/photos/ai', json={'upc': UPC, 'url': photo['url'], 'model': model}, base_url='https://pi.example')
            self.assertEqual((picked.status_code, picked.get_json()['model'], calls[-1][1]['data']['model']), (201, model, model))
        self.assertEqual(lister_routes.OPENAI_IMAGE_MODELS, ('gpt-image-2', 'gpt-image-2.5-sunburst', 'gpt-image-2.5-flare', 'gpt-image-1.5'))
        sent = len(calls)
        with patch.object(requests, 'post', fake_post), patch.dict(os.environ, {'OPENAI_API_KEY': 'test-openai'}):
            bad = self.client.post('/api/lister/photos/ai', json={'upc': UPC, 'url': photo['url'], 'model': 'dall-e-2'}, base_url='https://pi.example')
        self.assertEqual((bad.status_code, len(calls)), (400, sent))
        self.assertEqual(detail['aiImageModels'][0], detail['aiImageModel'])
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

    def test_learn_records_store_step_choices_and_feeds_the_next_unit(self):
        res = self.client.post('/api/lister/learn', json={'upc': UPC + '-1', 'platform': 'ebay', 'step': 'category',
                                                          'chosen': 'Home & Garden > Dinnerware > Plates', 'suggested': 'Home & Garden > Dinnerware',
                                                          'url': 'https://www.ebay.com/sl/prelist/suggest'})
        self.assertEqual(res.status_code, 201, res.get_json())
        data = res.get_json()
        self.assertFalse(data['agreed'])
        self.assertEqual(data['learned']['ebay']['category']['chosen'], 'Home & Garden > Dinnerware > Plates')
        same = self.client.post('/api/lister/learn', json={'upc': UPC, 'platform': 'ebay', 'step': 'match', 'chosen': 'Lenox plate', 'suggested': 'lenox PLATE'}).get_json()
        self.assertTrue(same['agreed'])
        # Another unit of the same catalog UPC gets the learned category when no proposal names one.
        detail = self.client.get(f'/api/lister/queue/{UPC}-2', base_url='https://pi.example').get_json()['item']
        self.assertEqual(detail['fields']['categoryPath'], 'Home & Garden > Dinnerware > Plates')
        self.assertEqual(detail['fields']['categoryPathSource'], 'learned')
        self.assertEqual(detail['learned']['ebay']['match']['history'], ['Lenox plate'])
        self.assertEqual(self.client.post('/api/lister/learn', json={'upc': UPC, 'platform': 'ebay', 'step': 'other', 'chosen': 'x'}).status_code, 400)
        self.assertEqual(self.client.post('/api/lister/learn', json={'upc': UPC, 'platform': 'ebay', 'step': 'category'}).status_code, 400)

    def test_deleting_a_link_reverts_the_queue_items_to_list_and_listing_log(self):
        res = self.client.post('/api/lister/links', json={'upc': UPC + '-1', 'platform': 'ebay', 'listing_id': '168611515264', 'sku': UPC + '-1'})
        link = res.get_json()['link']
        with closing(sqlite3.connect(self.root / 'bol.db')) as conn:
            conn.execute('CREATE TABLE bol_items (id INTEGER PRIMARY KEY, upc TEXT, listed_ebay INTEGER, listed_ebay_date TEXT, listed_ebay_source TEXT)')
            conn.execute("INSERT INTO bol_items (upc, listed_ebay, listed_ebay_date, listed_ebay_source) VALUES (?, 1, 'x', 'listing_center')", (UPC + '-1',))
            conn.execute("INSERT INTO bol_items (upc, listed_ebay, listed_ebay_date, listed_ebay_source) VALUES (?, 1, 'x', 'user')", (UPC,))
            conn.commit()
        self.assertEqual(self.sql('listinglog.db', "SELECT COUNT(*) AS n FROM listing_log WHERE listing_id = '168611515264'")[0]['n'], 1)
        res = self.client.delete(f"/api/lister/links/{link['id']}")
        self.assertEqual(res.status_code, 200, res.get_json())
        removed = res.get_json()['removed']
        self.assertTrue(removed['queue_reverted'])
        self.assertTrue(removed['items_to_list_reverted'])
        row = self.sql('listagent.db', 'SELECT status, listed_ebay_at, listed_listing_id FROM listing_queue WHERE upc = ?', (UPC + '-1',))[0]
        self.assertEqual(row, {'status': 'queued', 'listed_ebay_at': None, 'listed_listing_id': None})
        self.assertEqual(self.sql('listinglog.db', "SELECT COUNT(*) AS n FROM listing_log WHERE listing_id = '168611515264'")[0]['n'], 0)
        bol = {r['upc']: r for r in self.sql('bol.db', 'SELECT upc, listed_ebay, listed_ebay_source FROM bol_items')}
        self.assertEqual((bol[UPC + '-1']['listed_ebay'], bol[UPC + '-1']['listed_ebay_source']), (0, None))
        self.assertEqual(bol[UPC]['listed_ebay'], 1, 'a box the user ticked stays')
        self.assertEqual(self.client.get('/api/lister/queue?platform=ebay').get_json()['items'][0]['status'], 'queued')

    def test_generate_writes_title_and_description_from_the_notes(self):
        calls = []

        class FakeResponse:
            status_code = 200

            def __init__(self, text):
                self._text = text

            def json(self):
                return {'content': [{'text': self._text}], 'usage': {'input_tokens': 5, 'output_tokens': 5}}

        def fake_post(url, **kwargs):
            calls.append(kwargs['json']['messages'][0]['content'])
            description = '```json\n{"intro": "A classic **Lenox Butterfly Meadow** plate.", "conditionNote": "A small chip on the rim. Please see the close-up photos.", "condition": "Used - Good. Small chip on the rim.", "details": [{"label": "Material", "value": "**Porcelain**"}, {"label": "", "value": "Imported"}]}\n```'
            return FakeResponse(description if 'description' in kwargs['json']['messages'][0]['content'][:60] else '"Lenox Butterfly Meadow Dinner Plate 10.75 in"')

        import requests
        with patch.object(requests, 'post', fake_post), patch.dict(os.environ, {'ANTHROPIC_API_KEY': 'test-claude'}):
            title = self.client.post(f'/api/lister/queue/{UPC}-1/generate', json={'kind': 'title', 'values': {'title': 'Lenox plate', 'notes': ['chip on rim'], 'brand': 'Lenox'}})
            self.assertEqual(title.status_code, 200, title.get_json())
            self.assertEqual(title.get_json()['title'], 'Lenox Butterfly Meadow Dinner Plate 10.75 in')
            desc = self.client.post(f'/api/lister/queue/{UPC}-1/generate', json={'kind': 'description', 'values': {'title': 'Lenox plate', 'conditionDescription': 'Small chip on the rim', 'notes': ['box opened'], 'aspects': {'Material': ['Porcelain']}}})
            self.assertEqual(desc.status_code, 200, desc.get_json())
            html = desc.get_json()['descriptionHtml']
            self.assertTrue(html.startswith('<div style="max-width: 900px; margin: 0px auto; line-height: 1.7;">'), html[:80])
            self.assertIn('background: rgb(250, 247, 240)', html)
            self.assertIn('>ITEM DESCRIPTION</div>', html)
            self.assertIn('<p>A classic <strong>Lenox Butterfly Meadow</strong> plate.</p>', html)
            self.assertIn('<p><strong>IMPORTANT CONDITION NOTE:</strong> A small chip on the rim. Please see the close-up photos.</p>', html)
            self.assertIn('<ul>\n<li>Material: <strong>Porcelain</strong></li>\n<li>Imported</li>\n<li>Quantity: 1</li>\n</ul>', html)
            self.assertIn('Please review all photos carefully, as they are part of the description.', html)
            self.assertIn('>ITEM CONDITION</div>', html)
            self.assertIn('padding: 20px 24px;">Used - Good. Small chip on the rim.</div>', html)
            # The shop's fixed blocks follow the item cards, unchanged, and the wrapper closes.
            for fixed in ('WELCOME TO OUR SHOP', 'CONDITION &amp; PHOTOS', 'AUTHENTICITY', 'PACKED WITH CARE', 'RETURNS', "WE'RE HERE TO HELP", 'THANK YOU FOR SHOPPING'):
                self.assertIn(fixed, html)
            self.assertLess(html.index('ITEM CONDITION'), html.index('WELCOME TO OUR SHOP'))
            self.assertEqual(html.count('<div'), html.count('</div>'))
            self.assertNotIn('**', html)
            text = desc.get_json()['descriptionText']
            self.assertIn('Used - Good. Small chip on the rim.', text)
            self.assertNotIn('WELCOME TO OUR SHOP', text, 'plain text carries the item only')
            self.assertEqual(lister_routes.render_description('T', lister_routes.parse_description_json('not json at all'), upc='1')
                             .count('<p>not json at all</p>'), 1, 'prose that is not JSON becomes the intro')
            self.assertIn('&lt;b&gt;', lister_routes.render_description('T', {'intro': '<b>x</b>'}), 'model text is escaped')
            self.assertEqual(self.client.post(f'/api/lister/queue/{UPC}/generate', json={'kind': 'poem', 'values': {'title': 'x'}}).status_code, 400)
            self.assertEqual(self.client.post(f'/api/lister/queue/{UPC}/generate', json={'kind': 'title', 'values': {}}).status_code, 400)
        self.assertIn('chip on rim', calls[0])
        self.assertIn('Small chip on the rim', calls[1])
        self.assertIn('Material: Porcelain', calls[1])
        with patch.dict(os.environ, {'ANTHROPIC_API_KEY': ''}):
            self.assertEqual(self.client.post(f'/api/lister/queue/{UPC}/generate', json={'kind': 'title', 'values': {'title': 'x'}}).status_code, 503)

    def test_amazon_check_finds_the_asin_checks_restrictions_and_caches_per_catalog_upc(self):
        res = self.client.post(f'/api/lister/queue/{UPC}-1/amazon-check', json={'conditionType': 'used_good'})
        self.assertEqual(res.status_code, 200, res.get_json())
        check = res.get_json()['check']
        self.assertEqual((check['status'], check['asin'], check['brand'], check['cached']), ('restricted', 'B0TESTASIN', 'Lenox', False))
        self.assertEqual(check['reasons'], ['Approval required for Lenox'])
        self.assertEqual(self.amazon_calls[0], ('search', {'upc': UPC, 'mode': 'upc', 'limit': '3'}), 'the catalog is searched by the base UPC')
        self.assertEqual(self.amazon_calls[1][1]['asin'], 'B0TESTASIN')
        # Another unit of the same UPC reuses the cached verdict; the queue rows carry it.
        again = self.client.post(f'/api/lister/queue/{UPC}/amazon-check', json={'conditionType': 'used_good'}).get_json()['check']
        self.assertTrue(again['cached'])
        self.assertEqual(len(self.amazon_calls), 2)
        row = self.client.get('/api/lister/queue?platform=ebay').get_json()['items'][0]
        self.assertEqual((row['upc'], row['amazonCheck']['status']), (UPC, 'restricted'))
        # No ASIN at all -> no_asin; SP-API down -> unavailable, neither cached as a verdict.
        self.amazon['search'] = {'success': True, 'results': []}
        self.assertEqual(self.client.post(f'/api/lister/queue/{UPC}/amazon-check', json={'force': True}).get_json()['check']['status'], 'no_asin')
        self.amazon['search'] = {'success': False, 'error': 'Amazon SP-API not available'}
        self.assertEqual(self.client.post(f'/api/lister/queue/{UPC}/amazon-check', json={'force': True}).get_json()['check']['status'], 'unavailable')

    def test_amazon_restrictions_are_judged_only_against_the_conditions_we_sell_in(self):
        """A condition-less check answers for every condition Amazon knows, including ones we never use."""
        from sweetshelves.amazon_listing import _amazon_summarize_restrictions as summarize
        cannot = [{'reasonCode': 'NOT_ELIGIBLE', 'message': 'You cannot list the product in this condition.'}]
        gated = [{'reasonCode': 'APPROVAL_REQUIRED', 'message': 'You need approval to list in this brand.',
                  'links': [{'resource': 'https://sellercentral.amazon.com/apply'}]}]

        # Real B0DGMVJJLJ payload: only collectible_* / refurbished are blocked, so nothing is.
        out = summarize({'restrictions': [{'conditionType': c, 'reasons': cannot} for c in
                                          ('collectible_like_new', 'collectible_very_good', 'collectible_acceptable',
                                           'collectible_good', 'refurbished_refurbished')]})
        self.assertEqual((out['restricted'], out['blockedConditions'], out['reasons']), (False, [], []))
        self.assertEqual(len(out['openConditions']), 5)
        self.assertEqual(out['ignoredConditions'][0], 'collectible_acceptable')

        # Every condition we sell in is gated, but only for approval: a door we can knock on.
        out = summarize({'restrictions': [{'conditionType': c, 'reasons': gated} for c in
                                          ('new_new', 'used_like_new', 'used_very_good',
                                           'used_good', 'used_acceptable')]})
        self.assertEqual((out['restricted'], out['approvalOnly'], out['openConditions']), (True, True, []))
        self.assertEqual(out['reasons'], ['APPROVAL_REQUIRED: You need approval to list in this brand.'],
                         'the same reason repeated per condition collapses to one')
        self.assertEqual(out['links'], ['https://sellercentral.amazon.com/apply'])

        # Used is closed and new is not mentioned: silence is not permission, so nothing reads as open.
        out = summarize({'restrictions': [{'conditionType': c, 'reasons': cannot} for c in
                                          ('used_good', 'used_acceptable')]})
        self.assertEqual((out['restricted'], out['blockedConditions'], out['openConditions']),
                         (True, ['used_good', 'used_acceptable'], []))

        # Used is closed and new is explicitly clear: listable as new, with the caveat carried.
        out = summarize({'restrictions': [{'conditionType': 'new_new', 'reasons': []},
                                          {'conditionType': 'used_good', 'reasons': cannot},
                                          {'conditionType': 'used_acceptable', 'reasons': cannot}]})
        self.assertEqual((out['restricted'], out['blockedConditions'], out['openConditions']),
                         (False, ['used_good', 'used_acceptable'], ['new_new']))

        # A named condition is the whole answer: Amazon already filtered.
        out = summarize({'restrictions': [{'conditionType': 'used_good', 'reasons': cannot}]}, condition_type='used_good')
        self.assertEqual((out['restricted'], out['approvalOnly'], list(out['conditions'])), (True, False, ['used_good']))

    def test_amazon_check_status_separates_approval_from_a_hard_no(self):
        def verdict(restriction, upc=UPC):
            self.amazon['restriction'] = {'success': True, 'restriction': restriction}
            return self.client.post(f'/api/lister/queue/{upc}/amazon-check', json={'force': True}).get_json()['check']

        check = verdict({'checked': True, 'restricted': True, 'approvalOnly': True,
                         'reasons': [{'message': 'You need approval to list in this brand.'}],
                         'blockedConditions': ['used_good'], 'openConditions': []})
        self.assertEqual(check['status'], 'approval')

        check = verdict({'checked': True, 'restricted': False, 'blockedConditions': ['used_acceptable'],
                         'openConditions': ['new_new', 'used_good']})
        self.assertEqual((check['status'], check['blockedConditions']), ('partial', ['used_acceptable']))
        row = self.client.get('/api/lister/queue?platform=amazon').get_json()['items'][0]
        self.assertEqual(row['amazonCheck']['openConditions'], ['new_new', 'used_good'], 'the caveat survives the cache')

        self.assertEqual(verdict({'checked': True, 'restricted': False, 'openConditions': ['new_new']})['status'], 'listable')
        self.assertEqual(verdict({'checked': False, 'restricted': False})['status'], 'error')

    def test_queue_rows_carry_the_prep_status(self):
        with closing(sqlite3.connect(self.root / 'bol.db')) as conn:
            conn.execute('CREATE TABLE items_prep_status (id INTEGER PRIMARY KEY, upc TEXT, lot_number TEXT, status TEXT, reason TEXT, note TEXT, updated_at TEXT, quantity INTEGER)')
            conn.execute("INSERT INTO items_prep_status (upc, lot_number, status, reason, updated_at, quantity) VALUES (?, 'L-1', 'bad', 'missing pieces', '2026-09-15', 1)", (UPC,))
            conn.execute('CREATE TABLE items_prep_notes (id INTEGER PRIMARY KEY, upc TEXT, note TEXT, created_at TEXT)')
            conn.execute("INSERT INTO items_prep_notes (upc, note, created_at) VALUES (?, 'LT: x | EN: box opened', '2026-09-15')", (UPC,))
            conn.execute('CREATE TABLE items_prep_media (id INTEGER PRIMARY KEY, upc TEXT, row_status TEXT, media_type TEXT, file_path TEXT, mime_type TEXT, created_at TEXT)')
            conn.execute("INSERT INTO items_prep_media (upc, media_type, file_path, created_at) VALUES (?, 'audio', 'items_prep/v.webm', '2026-09-15')", (UPC,))
            conn.execute("INSERT INTO items_prep_media (upc, media_type, file_path, created_at) VALUES (?, 'video', 'items_prep/v.mp4', '2026-09-15')", (UPC,))
            conn.commit()
        row = self.client.get('/api/lister/queue?platform=ebay').get_json()['items'][0]
        self.assertEqual(row['prepStatus'], {'status': 'bad', 'reason': 'missing pieces'})
        self.assertEqual(row['notes'], {'written': 1, 'voice': 1}, 'written and voice note counts, videos not counted')
        with closing(sqlite3.connect(self.root / 'rawbol.db')) as conn:
            conn.execute('CREATE TABLE raw_bol_items (id INTEGER PRIMARY KEY, upc TEXT, image_url TEXT, prep_reason TEXT)')
            conn.execute("INSERT INTO raw_bol_items (upc, prep_reason) VALUES (?, 'Missing pieces')", (UPC.lstrip('0'),))
            conn.commit()
        row = self.client.get('/api/lister/queue?platform=ebay').get_json()['items'][0]
        self.assertEqual(row['defect'], 'Missing pieces', 'the BOL defect rides along, matched on the catalog UPC')

    def test_preload_runs_prepare_ai_text_and_ai_photos_in_the_background_and_keeps_the_text(self):
        import time as _time
        (self.root / 'static' / 'listingagent_uploads').mkdir()
        (self.root / 'static' / 'listingagent_uploads' / 'own.jpg').write_bytes(b'\xff\xd8jpeg')
        with self.app.app_context():
            listing_queue._listagent_add_photo(UPC + '-1', image_path='listingagent_uploads/own.jpg')
        self.detail_payload = {'title': 'Lenox plate', 'images': [], 'inventory': {'total_quantity': 1, 'positions': ['B-1'], 'rows': []},
                               'bol': {}, 'ebay_store': {'listings': []}, 'amazon_store': {'listings': []},
                               'prep': {'notes': [{'id': 1, 'note': 'LT: x | EN: box opened', 'created_at': '2026-09-15'}], 'images': [], 'voice_notes': [], 'videos': []}}
        posted = []

        class FakeResponse:
            status_code = 200

            def __init__(self, body):
                self.body = body

            def json(self):
                return self.body

        def fake_post(url, **kwargs):
            posted.append(url)
            if 'openai' in url:
                return FakeResponse({'data': [{'b64_json': 'aGVsbG8='}], 'usage': {}})
            content = kwargs['json']['messages'][0]['content']
            if content.startswith('Write one eBay listing title'):
                return FakeResponse({'content': [{'text': 'Lenox Butterfly Meadow Plate AI'}], 'usage': {}})
            return FakeResponse({'content': [{'text': '{"intro": "A plate.", "details": [], "condition": "Used, box opened.", "conditionNote": ""}'}], 'usage': {}})

        import requests
        with patch.object(requests, 'post', fake_post), patch.dict(os.environ, {'ANTHROPIC_API_KEY': 'k', 'OPENAI_API_KEY': 'k'}):
            res = self.client.post(f'/api/lister/queue/{UPC}-1/preload', json={'steps': ['prepare', 'title', 'description', 'photos']}, base_url='https://pi.example')
            self.assertEqual(res.status_code, 200, res.get_json())
            self.assertTrue(res.get_json()['preload']['running'])
            for _ in range(100):
                status = self.client.get(f'/api/lister/queue/{UPC}-1/preload').get_json()['preload']
                if not status['running']:
                    break
                _time.sleep(0.05)
        self.assertFalse(status['running'], status)
        self.assertEqual(status['steps'], {'prepare': 'done', 'title': 'done', 'description': 'done', 'photos': 'done'}, status)
        self.assertEqual(status['photos'], {'done': 1, 'total': 1})
        self.assertEqual(self.built[0][0], UPC + '-1', 'the proposal was built')
        self.assertEqual(sum('anthropic' in u for u in posted), 2)
        self.assertEqual(sum('openai' in u for u in posted), 1)
        # The text is kept and wins in the item's fields, marked as automatic; the AI photo is on file.
        item = self.client.get(f'/api/lister/queue/{UPC}-1', base_url='https://pi.example').get_json()['item']
        self.assertEqual(item['fields']['title'], 'Lenox Butterfly Meadow Plate AI')
        self.assertIn('A plate.', item['fields']['descriptionText'])
        self.assertEqual(item['fields']['generated'], {'title': 'auto', 'description': 'auto'})
        self.assertEqual([p['source'] for p in item['photos']][:2], ['ai', 'listing'])
        self.assertEqual(item['preload']['steps']['photos'], 'done')
        # A second preload does not regenerate what exists.
        with patch.object(requests, 'post', fake_post), patch.dict(os.environ, {'ANTHROPIC_API_KEY': 'k', 'OPENAI_API_KEY': 'k'}):
            self.client.post(f'/api/lister/queue/{UPC}-1/preload', json={'steps': ['title', 'description', 'photos']}, base_url='https://pi.example')
            for _ in range(100):
                if not self.client.get(f'/api/lister/queue/{UPC}-1/preload').get_json()['preload']['running']:
                    break
                _time.sleep(0.05)
        self.assertEqual(len(posted), 3, 'nothing was generated twice')
        # Preload all: one sequential job with a percentage.
        with patch.object(requests, 'post', fake_post), patch.dict(os.environ, {'ANTHROPIC_API_KEY': 'k', 'OPENAI_API_KEY': 'k'}):
            res = self.client.post('/api/lister/preload', json={'upcs': [UPC + '-1', UPC], 'steps': ['title']}, base_url='https://pi.example')
            self.assertEqual(res.status_code, 200, res.get_json())
            for _ in range(100):
                overall = self.client.get('/api/lister/preload').get_json()
                if not overall['running']:
                    break
                _time.sleep(0.05)
        self.assertEqual((overall['total'], overall['done'], overall['percent']), (2, 2, 100))
        self.assertEqual(overall['items'][UPC]['steps'], {'title': 'done'})
        self.assertEqual(self.client.post('/api/lister/preload', json={'upcs': []}).status_code, 400)

    def test_preload_tolerates_held_proposals_and_junk_catalog_photos(self):
        import time as _time
        (self.root / 'static' / 'listingagent_uploads').mkdir()
        (self.root / 'static' / 'listingagent_uploads' / 'own.jpg').write_bytes(b'jpeg')
        with self.app.app_context():
            listing_queue._listagent_add_photo(UPC + '-1', image_path='listingagent_uploads/own.jpg')
        # Live Pi rows: a "No image" placeholder and our own file echoed back under another host.
        self.detail_payload = {'title': 'Lenox plate', 'inventory': {'total_quantity': 1, 'positions': [], 'rows': []},
                               'images': ['No image', 'http://127.0.0.1:5000/static/listingagent_uploads/own.jpg', 'https://cdn.example/c.jpg'],
                               'bol': {}, 'ebay_store': {'listings': []}, 'amazon_store': {'listings': []},
                               'prep': {'notes': [], 'images': [], 'voice_notes': [], 'videos': []}}
        item = self.client.get(f'/api/lister/queue/{UPC}-1', base_url='https://pi.example').get_json()['item']
        self.assertEqual([(p['source'], p['name']) for p in item['photos']], [('listing', 'own.jpg'), ('catalog', 'c.jpg')])

        class HeldError(Exception):
            status_code = 409
        self.build_error = HeldError('Release the held proposal or resolve the in-progress approval before rebuilding')
        res = self.client.post(f'/api/lister/queue/{UPC}-1/preload', json={'steps': ['prepare']}, base_url='https://pi.example')
        self.assertEqual(res.status_code, 200, res.get_json())
        for _ in range(100):
            status = self.client.get(f'/api/lister/queue/{UPC}-1/preload').get_json()['preload']
            if not status['running']:
                break
            _time.sleep(0.05)
        self.assertEqual(status['steps'], {'prepare': 'done'}, 'a held proposal already has its values: not a failure')

    def test_links_for_a_phone_are_https_behind_cloudflare(self):
        """Cloudflare speaks plain HTTP to gunicorn, but a phone opens these links cold."""
        res = self.client.post('/api/lister/photo-link', json={'upc': UPC + '-1'},
                               base_url='http://pi.example', headers={'X-Forwarded-Proto': 'https'})
        self.assertTrue(res.get_json()['url'].startswith('https://pi.example/'), res.get_json()['url'])
        item = self.client.get(f'/api/lister/queue/{UPC}-1', base_url='http://pi.example',
                               headers={'X-Forwarded-Proto': 'https'}).get_json()['item']
        self.assertTrue(item['mobilePhotosUrl'].startswith('https://pi.example/'), item['mobilePhotosUrl'])

    def test_photo_link_telegrams_the_same_page_the_qr_code_points_at(self):
        detail = self.client.get(f'/api/lister/queue/{UPC}-1', base_url='https://pi.example').get_json()['item']
        res = self.client.post('/api/lister/photo-link', json={'upc': UPC + '-1'}, base_url='https://pi.example')
        body = res.get_json()
        self.assertEqual(res.status_code, 200, body)
        self.assertEqual(body['sent'], ['Danka'], 'one message per chat, not per recipient row')
        self.assertEqual([chat for chat, _text, _quiet in self.telegram_sent], ['111'])
        chat, text, quiet = self.telegram_sent[0]
        self.assertFalse(quiet, 'the phone should buzz: that is the whole point of the button')

        # Read on a lock screen: a photo icon, the item name, the link, nothing else.
        self.assertEqual(text, '\U0001F4F7 Lenox Butterfly Meadow Dinner Plate\n' + body['url'], text)
        self.assertIn('camera=1', body['url'])
        self.assertNotIn('return=', body['url'], 'the Back link is panel-only noise in a chat message')
        self.assertTrue(body['url'].startswith(detail['mobilePhotosUrl'].split('?')[0] + '?upc='),
                        'the button and the QR code still open the same page')

    def test_opening_the_link_deletes_the_bot_message(self):
        url = self.client.post('/api/lister/photo-link', json={'upc': UPC + '-1'},
                               base_url='https://pi.example').get_json()['url']
        token = url.split('&t=')[1]

        calls = []

        class FakeResponse:
            ok = True

            @staticmethod
            def json():
                return {'ok': True}

        def fake_post(target, json=None, timeout=None):
            calls.append((target, json))
            return FakeResponse()

        with patch('requests.post', fake_post):
            res = self.client.post('/api/lister/photo-link/opened', json={'token': token})
            self.assertEqual(res.get_json(), {'success': True, 'deleted': 1}, res.get_json())
            self.assertEqual(len(calls), 1)
            self.assertTrue(calls[0][0].endswith('/bottest-bot-token/deleteMessage'), calls[0][0])
            self.assertEqual(calls[0][1], {'chat_id': '111', 'message_id': 7})

            # Opening it twice must not delete twice, and an unknown token is a no-op.
            self.assertEqual(self.client.post('/api/lister/photo-link/opened',
                                              json={'token': token}).get_json()['deleted'], 0)
            self.assertEqual(self.client.post('/api/lister/photo-link/opened',
                                              json={'token': 'nope'}).get_json()['deleted'], 0)
            self.assertEqual(len(calls), 1)
        self.assertEqual(self.client.post('/api/lister/photo-link/opened', json={}).status_code, 400)

    def test_queue_thumbnails_are_asked_for_over_https(self):
        """The BOL keeps its Macy's pictures as http:// links. The side panel is an https page, so a
        plain http picture is never loaded - the queue row showed a broken image instead."""
        with closing(sqlite3.connect(self.root / 'rawbol.db')) as conn:
            conn.execute('CREATE TABLE raw_bol_items (id INTEGER PRIMARY KEY, upc TEXT, image_url TEXT, prep_reason TEXT)')
            conn.execute("INSERT INTO raw_bol_items (upc, image_url) VALUES (?, 'http://slimages.macys.com/is/image/MCY/24513711')",
                         (UPC.lstrip('0'),))
            conn.commit()
        row = self.client.get('/api/lister/queue?platform=ebay').get_json()['items'][0]
        self.assertEqual(row['thumb'], 'https://slimages.macys.com/is/image/MCY/24513711')

    def test_a_queue_thumbnail_of_our_own_is_a_full_small_url(self):
        """A + NEW item's picture is a phone photo under /static/custom_items. Handed over as a bare
        /static path, the extension page resolved it against itself and showed a broken image."""
        with closing(sqlite3.connect(self.root / 'bol.db')) as conn:
            conn.execute('CREATE TABLE IF NOT EXISTS bol_items (id INTEGER PRIMARY KEY, upc TEXT, image_url TEXT)')
            conn.execute("INSERT INTO bol_items (upc, image_url) VALUES (?, ?)",
                         (UPC.lstrip('0'), f'/static/custom_items/{UPC}.jpg'))
            conn.commit()
        row = self.client.get('/api/lister/queue?platform=ebay').get_json()['items'][0]
        self.assertEqual(row['thumb'], f'http://localhost/static-thumb/custom_items/{UPC}.jpg?w=240')

    def test_the_photo_proxy_takes_the_http_link_the_bol_gave_us(self):
        """The fallback is what a refused picture falls back to; rejecting the BOL's own http link
        made it a dead end and left the broken image on screen."""
        grabbed = []

        class FakeResponse:
            status_code = 200
            headers = {'Content-Type': 'image/jpeg'}

            class raw:
                @staticmethod
                def read(_n):
                    return bytes([0xff, 0xd8]) + b'jpeg'

        def fake_get(url, **kwargs):
            grabbed.append(url)
            return FakeResponse()

        with patch('requests.get', fake_get):
            res = self.client.get('/api/lister/photos/fetch?url=http://slimages.macys.com/is/image/MCY/1',
                                  base_url='https://pi.example')
        self.assertEqual(res.status_code, 200, res.get_json())
        self.assertEqual(grabbed, ['https://slimages.macys.com/is/image/MCY/1'])

    def test_photo_link_goes_only_to_the_phone_of_whoever_asked_once_they_linked_it(self):
        self.paired['dan@example.com'] = {'chat_id': '333', 'name': 'Dan'}
        res = self.client.post('/api/lister/photo-link', json={'upc': UPC + '-1'}, base_url='https://pi.example',
                               headers={'Cf-Access-Authenticated-User-Email': 'Dan@Example.com'})
        body = res.get_json()
        self.assertEqual(res.status_code, 200, body)
        self.assertEqual([chat for chat, _text, _quiet in self.telegram_sent], ['333'], 'not the shared chats')
        self.assertEqual((body['sent'], body['routed']), (['Dan'], 'you'))

    def test_photo_link_from_someone_not_linked_still_reaches_every_enabled_chat(self):
        self.paired['dan@example.com'] = {'chat_id': '333', 'name': 'Dan'}
        res = self.client.post('/api/lister/photo-link', json={'upc': UPC + '-1'}, base_url='https://pi.example',
                               headers={'Cf-Access-Authenticated-User-Email': 'someone@else.com'})
        self.assertEqual(res.get_json()['routed'], 'everyone')
        self.assertEqual([chat for chat, _text, _quiet in self.telegram_sent], ['111'])

    def test_whose_phone_comes_from_cloudflare_not_from_the_request_body(self):
        """actor in the body names who listed something; it must not steer a link to someone's phone."""
        self.paired['dan@example.com'] = {'chat_id': '333', 'name': 'Dan'}
        res = self.client.post('/api/lister/photo-link', json={'upc': UPC + '-1', 'actor': 'dan@example.com'},
                               base_url='https://pi.example')
        self.assertEqual(res.get_json()['routed'], 'everyone')
        self.assertNotIn('333', [chat for chat, _text, _quiet in self.telegram_sent])

    def test_photo_link_says_what_is_missing(self):
        self.assertEqual(self.client.post('/api/lister/photo-link', json={}).status_code, 400)

        self.telegram_rows = [{'chat_id': '222', 'display_name': 'Off', 'enabled': 0}]
        res = self.client.post('/api/lister/photo-link', json={'upc': UPC + '-1'})
        self.assertEqual(res.status_code, 400)
        self.assertIn('Telegram', res.get_json()['error'])
        self.assertEqual(self.telegram_sent, [])

        self.telegram_rows = [{'chat_id': '111', 'display_name': 'Danka', 'enabled': 1}]
        self.telegram_result = (False, 'chat not found')
        res = self.client.post('/api/lister/photo-link', json={'upc': UPC + '-1'})
        self.assertEqual(res.status_code, 502)
        self.assertIn('chat not found', res.get_json()['error'])

    def test_photo_link_without_telegram_wired_up(self):
        app = Flask('lister-no-telegram', static_folder=str(self.root / 'static'))
        lister = lister_routes.register(app, {
            'db_connection': database.db_connection, '_safe_error': errors._safe_error,
            '_listagent_mark_listed': listing_queue._listagent_mark_listed,
            '_listagent_upc_variants': listing_queue._listagent_upc_variants,
            '_listagent_format_upc12': listing_queue._listagent_format_upc12,
            '_listagent_init_tables': listing_checks._listagent_init_tables,
        })
        res = app.test_client().post('/api/lister/photo-link', json={'upc': UPC})
        self.assertEqual(res.status_code, 501)

    def test_qr_endpoint_renders_or_explains(self):
        res = self.client.get('/api/lister/qr?text=https://pi.example/items-to-list/mobile-photos?upc=1')
        self.assertIn(res.status_code, (200, 501))
        if res.status_code == 200:
            self.assertIn(b'<svg', res.data)
            self.assertEqual(res.headers['Content-Type'], 'image/svg+xml; charset=utf-8')
        self.assertEqual(self.client.get('/api/lister/qr').status_code, 400)


    # -- traceability: the store sync closes the loop, suffixed units land on themselves -------------

    def store_listing(self, item_id, *, upc='', sku='None', listed='2026-09-18T13:06:54.000Z', title='Listed thing'):
        with closing(sqlite3.connect(self.root / 'ebayStore.db')) as conn:
            have = {r[1] for r in conn.execute('PRAGMA table_info(INVENTORY)')}
            for col in ('List_Date', 'URL', 'Price'):
                if col not in have:
                    conn.execute(f'ALTER TABLE INVENTORY ADD COLUMN {col} TEXT')
            conn.execute('''INSERT INTO INVENTORY (Title, ItemID, SKU, UPC, List_State, List_Date, URL, Price)
                            VALUES (?, ?, ?, ?, 'Active', ?, ?, '19.99')''',
                         (title, item_id, sku, upc, listed, f'https://www.ebay.com/itm/{item_id}'))
            conn.commit()

    def test_a_suffixed_unit_links_to_its_own_rack_row_and_ticks_the_base_upc_on_items_to_list(self):
        unit = UPC + '-1'
        self.queue_add(unit, 'Lenox plate damaged', '2026-09-18T08:00:00')
        res = self.client.post('/api/lister/links', json={'upc': unit, 'platform': 'ebay', 'listing_id': '334455667788'})
        self.assertEqual(res.status_code, 201, res.get_json())
        effects = res.get_json()['link']['effects']
        self.assertEqual(effects['inventory_match']['searchrack_id'], 2, 'the -1 unit, not the base row')
        self.assertEqual(effects['inventory_match']['inventory_barcode'], unit)
        self.assertEqual(effects['rack_ids'][0], 2)
        self.assertEqual(self.bol_marks, [('ebay', UPC)], 'Items to List rows hold the base UPC')
        # The base unit still resolves to its own row first.
        with self.app.app_context():
            self.assertEqual(self.lister.rack_rows(UPC)[0]['id'], 1)

    def test_a_submitted_suffixed_unit_ticks_the_base_upc(self):
        unit = UPC + '-1'
        self.queue_add(unit, 'Lenox plate damaged', '2026-09-18T08:00:00')
        self.client.post(f'/api/lister/queue/{unit}/submitted', json={'platform': 'ebay'})
        self.assertEqual(self.bol_marks, [('ebay', UPC)])

    def test_amazon_links_without_a_sku_are_guarded_by_the_asin(self):
        body = {'upc': UPC, 'platform': 'amazon', 'asin': 'B0ABCDEF12'}
        self.assertEqual(self.client.post('/api/lister/links', json=body).status_code, 201)
        again = self.client.post('/api/lister/links', json=body)
        self.assertEqual(again.status_code, 200)
        self.assertTrue(again.get_json()['duplicate'])
        other = self.client.post('/api/lister/links', json={**body, 'upc': '012345678905'})
        self.assertEqual(other.status_code, 409)

    def test_store_listings_find_a_13_digit_ebay_upc(self):
        self.store_listing('188947499008', upc='0810071427688')
        with self.app.app_context():
            found = self.lister._store_listings('810071427688')['ebay']
        self.assertEqual([e['listingId'] for e in found], ['188947499008'])

    def test_reconcile_links_a_listing_whose_queue_row_was_cleared_after_it_went_live(self):
        unit = '810071428487'
        with closing(sqlite3.connect(self.root / 'listagent.db')) as conn:
            conn.execute('''INSERT INTO listing_queue (upc, title, status, added_at, removed_at)
                            VALUES (?, 'JoyJolt espresso cups', 'removed', '2026-09-16T16:26:28', '2026-09-18T10:23:19')''', (unit,))
            conn.commit()
        self.store_listing('188947233082', upc='810071428487', listed=self.utc('2026-09-18T09:06:54'))
        dry = self.client.post('/api/lister/reconcile', json={'dry_run': True, 'days': 3650}).get_json()
        self.assertEqual([(e['upc'], e['listing_id']) for e in dry['linked']], [(unit, '188947233082')])
        self.assertEqual(self.sql('listagent.db', 'SELECT COUNT(*) AS n FROM listing_links')[0]['n'], 0, 'dry run writes nothing')

        res = self.client.post('/api/lister/reconcile', json={'days': 3650}).get_json()
        self.assertEqual(res['linked'][0]['upc'], unit)
        self.assertTrue(res['linked'][0]['revived'])
        link = self.sql('listagent.db', 'SELECT upc, platform, listing_id, source, kind FROM listing_links')[0]
        self.assertEqual(link, {'upc': unit, 'platform': 'ebay', 'listing_id': '188947233082', 'source': 'sync', 'kind': 'listed'})
        row = self.sql('listagent.db', 'SELECT status, listed_ebay_at FROM listing_queue WHERE upc = ?', (unit,))[0]
        self.assertEqual(row['status'], 'done')
        self.assertTrue(row['listed_ebay_at'])
        # Cleared from both lists: it must not come back on Amazon's.
        self.assertNotIn(unit, [it['upc'] for it in self.client.get('/api/lister/queue?platform=amazon').get_json()['items']])
        log = self.sql('listinglog.db', "SELECT source, listing_id FROM listing_log WHERE upc = ?", (unit,))
        self.assertIn({'source': 'lister', 'listing_id': '188947233082'}, log, 'the sale tracer can now find it')
        # Idempotent.
        self.assertEqual(self.client.post('/api/lister/reconcile', json={'days': 3650}).get_json()['linked'], [])

    def test_reconcile_leaves_listings_older_than_the_queue_row_and_shared_upcs_alone(self):
        self.store_listing('112200000001', upc=UPC, listed=self.utc('2026-09-12T09:00:00'))  # before the 09-13 queue add
        self.assertEqual(self.reconcile()['linked'], [])
        self.queue_add(UPC + '-1', 'Lenox plate damaged', '2026-09-18T08:00:00')
        self.store_listing('112200000002', upc=UPC, listed=self.utc('2026-09-18T12:00:00'))
        report = self.reconcile()
        self.assertEqual(report['linked'], [])
        self.assertEqual(report['ambiguous'][0]['listing_id'], '112200000002')
        # Our SKU on the listing says whose it is.
        self.store_listing('112200000003', upc=UPC, sku=UPC + '-1', listed=self.utc('2026-09-18T12:30:00'))
        report = self.reconcile()
        self.assertEqual([(e['upc'], e['listing_id']) for e in report['linked']], [(UPC + '-1', '112200000003')])

    def test_reconcile_backfills_the_item_number_of_a_submitted_listing(self):
        unit = '012345678905'
        self.queue_add(unit, 'Other item', '2026-09-18T08:00:00')
        self.client.post(f'/api/lister/queue/{unit}/submitted', json={'platform': 'ebay'})
        self.store_listing('199900000001', upc=unit, listed=self.utc('2026-09-18T09:30:00'))
        report = self.reconcile()
        self.assertEqual([(e['upc'], e['listing_id']) for e in report['linked']], [(unit, '199900000001')])
        ebay = [it for it in self.client.get('/api/lister/queue?platform=ebay').get_json()['items'] if it['upc'] == unit][0]
        self.assertFalse(ebay['submitted'], 'the item number arrived')
        self.assertEqual(ebay['links'][0]['listing_id'], '199900000001')

    def test_reconcile_links_an_amazon_sku_that_is_the_unit_code(self):
        unit = UPC + '-1'
        self.queue_add(unit, 'Lenox plate damaged', '2026-09-18T08:00:00')
        with closing(sqlite3.connect(self.root / 'amazonStore.db')) as conn:
            conn.execute('CREATE TABLE IF NOT EXISTS ITEMS (ID INTEGER PRIMARY KEY, ASIN TEXT, SKU TEXT, TITLE TEXT, PRICE REAL, STATUS TEXT, UPC TEXT, FULFILLMENT_CHANNEL TEXT)')
            conn.execute("INSERT INTO ITEMS (ASIN, SKU, TITLE, PRICE, STATUS, UPC) VALUES ('B0ABCDEF12', ?, 'Plate', 20, 'Active', ?)", (unit, UPC))
            conn.commit()
        report = self.reconcile()
        self.assertEqual([(e['upc'], e['platform'], e['sku'], e['kind']) for e in report['linked']], [(unit, 'amazon', unit, 'listed')])
        # A bare-UPC SKU the panel never submitted may be an older offer of ours: reported, not linked.
        self.queue_add('012345678905', 'Other item', '2026-09-18T08:00:00')
        with closing(sqlite3.connect(self.root / 'amazonStore.db')) as conn:
            conn.execute("INSERT INTO ITEMS (ASIN, SKU, TITLE, PRICE, STATUS, UPC) VALUES ('B0ZZZZZZZ1', '012345678905', 'Other', 9, 'Active', '012345678905')")
            conn.commit()
        report = self.reconcile()
        self.assertEqual(report['linked'], [])
        self.assertEqual([e['upc'] for e in report['existing']], ['012345678905'])

    def test_deleting_a_link_removes_its_log_row_even_when_the_queue_moved_on(self):
        res = self.client.post('/api/lister/links', json={'upc': UPC, 'platform': 'ebay', 'listing_id': '335566778800'})
        link_id = res.get_json()['link']['id']
        with closing(sqlite3.connect(self.root / 'listagent.db')) as conn:  # the queue now points at another listing
            conn.execute("UPDATE listing_queue SET listed_listing_id = '999' WHERE upc = ?", (UPC,))
            conn.commit()
        self.client.delete(f'/api/lister/links/{link_id}', json={})
        self.assertEqual(self.sql('listinglog.db', "SELECT COUNT(*) AS n FROM listing_log WHERE listing_id = '335566778800'")[0]['n'], 0)

    def test_breadcrumbs_record_where_the_store_tab_went(self):
        res = self.client.post('/api/lister/breadcrumbs', json={'upc': UPC, 'platform': 'ebay', 'stage': 'after-submit',
                                                                'url': 'https://www.ebay.com/sl/list/success?itemId=1', 'kind': 'listing-form',
                                                                'success': True, 'title': 'Listed', 'headline': 'Your listing is live'})
        self.assertEqual(res.status_code, 201, res.get_json())
        crumbs = self.client.get('/api/lister/breadcrumbs').get_json()['breadcrumbs']
        self.assertEqual((crumbs[0]['url'], crumbs[0]['success'], crumbs[0]['stage']),
                         ('https://www.ebay.com/sl/list/success?itemId=1', 1, 'after-submit'))
        self.assertEqual(self.client.post('/api/lister/breadcrumbs', json={}).status_code, 400)

    def test_ping_reports_the_published_feed_version(self):
        feed = self.root / 'static' / 'lister'
        feed.mkdir(parents=True, exist_ok=True)
        (feed / 'latest.json').write_text(json.dumps({'version': '9.9.9'}), encoding='utf-8')
        self.assertEqual(self.client.get('/api/lister/ping').get_json()['version'], '9.9.9')

    def reconcile(self):
        with self.app.app_context():
            return self.lister.reconcile_stores(days=3650)

    @staticmethod
    def utc(local_stamp):
        """A local wall-clock stamp as the eBay sync stores it (UTC, 'Z')."""
        value = datetime.datetime.fromisoformat(local_stamp).astimezone(datetime.timezone.utc)
        return value.strftime('%Y-%m-%dT%H:%M:%S.000Z')


if __name__ == '__main__':
    unittest.main()
