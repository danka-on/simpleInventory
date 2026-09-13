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
        self.app = Flask('lister-test', static_folder=str(self.root / 'static'))
        self.app.teardown_appcontext(database.close_db_connections)
        self.lister = lister_routes.register(self.app, {
            'db_connection': database.db_connection,
            '_safe_error': errors._safe_error,
            '_listagent_mark_listed': listing_queue._listagent_mark_listed,
            '_listagent_upc_variants': listing_queue._listagent_upc_variants,
            '_listagent_format_upc12': listing_queue._listagent_format_upc12,
            '_listagent_init_tables': listing_checks._listagent_init_tables,
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


if __name__ == '__main__':
    unittest.main()
