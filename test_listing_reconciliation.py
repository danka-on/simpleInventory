"""Every active listing must land in exactly one honest warehouse state."""
from contextlib import ExitStack, closing
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

import DBmanager
from sweetshelves.bootstrap import app
from sweetshelves import config
from sweetshelves import listing_reconciliation as recon


class ListingReconciliationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.stack = ExitStack()
        self.stack.enter_context(patch.object(config, 'BASE_DIR', self.root))
        self.stack.enter_context(patch.object(DBmanager, 'BASE_DIR', str(self.root)))
        self.stack.enter_context(patch.dict(recon._cache_state, {'ts': 0.0, 'payload': None}))
        self.client = app.test_client()
        self.sql('searchRack.db', '''
            CREATE TABLE SEARCHRACK (ID INTEGER PRIMARY KEY, TITLE TEXT, BARCODE TEXT, ITEM_POSITION TEXT,
                PICTUREPOSITION TEXT, IMAGE TEXT, ITEMID TEXT, QUANTITY INTEGER, CREATED_AT TEXT,
                CUSTOM_TITLE INTEGER DEFAULT 0, WAREHOUSE_NOTE TEXT DEFAULT '')''')
        self.sql('searchRack.db', 'CREATE TABLE archived_searchrack (barcode TEXT, reason TEXT)')
        self.sql('ebayStore.db', '''
            CREATE TABLE INVENTORY (ID INTEGER PRIMARY KEY, Title TEXT, ItemID TEXT, SKU TEXT, Quantity TEXT,
                Image TEXT, URL TEXT, List_State TEXT, UPC TEXT)''')
        self.sql('amazonStore.db', '''
            CREATE TABLE ITEMS (ID INTEGER PRIMARY KEY, ASIN TEXT, SKU TEXT, TITLE TEXT, QUANTITY INTEGER,
                STATUS TEXT, IMAGE TEXT, UPC TEXT, FULFILLMENT_CHANNEL TEXT)''')
        self.sql('rawbol.db', 'CREATE TABLE raw_bol_items (upc TEXT, item_description TEXT, lot_number TEXT)')
        self.sql('bol.db', 'CREATE TABLE bol_items (upc TEXT, item_description TEXT, lot_number TEXT)')
        self.sql('sold.db', '''CREATE TABLE orders (barcode TEXT, quantity INTEGER, rackupdated INTEGER DEFAULT 0,
            removal_cancelled INTEGER DEFAULT 0, store TEXT, paid_time TEXT, shipped_time TEXT,
            listing_listing_id TEXT, item_id TEXT, listing_sku TEXT, sku TEXT, listing_asin TEXT)''')

    def tearDown(self):
        self.stack.close()
        self.temp.cleanup()

    def sql(self, db, statement, args=()):
        with closing(sqlite3.connect(self.root / db)) as conn:
            conn.execute(statement, args)
            conn.commit()

    def rack(self, row_id, title, barcode, quantity=1, location='', note=''):
        self.sql('searchRack.db', '''
            INSERT INTO SEARCHRACK (ID, TITLE, BARCODE, ITEM_POSITION, PICTUREPOSITION, IMAGE, ITEMID, QUANTITY,
                CREATED_AT, CUSTOM_TITLE, WAREHOUSE_NOTE)
            VALUES (?, ?, ?, ?, '', '', '', ?, '2026-01-05', 0, ?)''', (row_id, title, barcode, location, quantity, note))

    def ebay(self, item_id, title, upc, sku='None', qty='1', state='Active'):
        self.sql('ebayStore.db', '''
            INSERT INTO INVENTORY (Title, ItemID, SKU, Quantity, Image, URL, List_State, UPC)
            VALUES (?, ?, ?, ?, '', '', ?, ?)''', (title, item_id, sku, qty, state, upc))

    def amazon(self, sku, title, upc, qty=1, status='Active', channel='DEFAULT', asin='B000000001'):
        self.sql('amazonStore.db', '''
            INSERT INTO ITEMS (ASIN, SKU, TITLE, QUANTITY, STATUS, IMAGE, UPC, FULFILLMENT_CHANNEL)
            VALUES (?, ?, ?, ?, ?, '', ?, ?)''', (asin, sku, title, qty, status, upc, channel))

    def scan(self):
        response = self.client.get('/api/listing-reconciliation?refresh=1')
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        payload = response.get_json()
        self.assertTrue(payload['success'], payload)
        return payload

    def by_key(self, payload):
        return {(row['store'], row['listing_key']): row for row in payload['listings']}

    def test_barcode_spellings_all_count_as_accounted(self):
        self.rack(1, 'Lenox plate', '882864344090', quantity=2)
        self.rack(2, 'Sango set', '727870181140-1', quantity=1)
        self.rack(3, 'Sango set', '727870181140-2', quantity=1)
        self.rack(4, 'Noritake bowl', '37725567235', quantity=1)
        self.ebay('101', 'Lenox plate', '882864344090', qty='3')
        self.ebay('102', 'Sango set', '727870181140')
        self.amazon('SKU-A', 'Noritake bowl', '0037725567235')
        rows = self.by_key(self.scan())
        exact = rows[('ebay', '101')]
        self.assertEqual((exact['state'], exact['match_kind'], exact['warehouse_qty'], exact['short_by']),
                         ('accounted', 'exact', 2, 1))
        suffix = rows[('ebay', '102')]
        self.assertEqual((suffix['state'], suffix['match_kind'], suffix['warehouse_qty']), ('accounted', 'suffix', 2))
        padded = rows[('amazon', 'SKU-A')]
        self.assertEqual((padded['state'], padded['match_kind']), ('accounted', 'zero-pad'))

    def test_inactive_unsold_and_fba_listings_are_not_reconciled(self):
        self.ebay('201', 'Ended thing', '111111111111', state='Unsold')
        self.ebay('202', 'Sold thing', '111111111111', state='Sold')
        self.amazon('SKU-FBA', 'At Amazon', '222222222222', channel='AMAZON_NA')
        self.amazon('SKU-OFF', 'Inactive thing', '333333333333', status='Inactive')
        payload = self.scan()
        self.assertEqual(payload['listings'], [])
        self.assertEqual(payload['totals']['live'], 0)

    def test_stock_for_an_fba_listing_is_not_reported_as_unlisted(self):
        self.rack(1, 'Waiting for the FBA shipment', '0222222222222', quantity=3)
        self.rack(2, 'By ASIN', 'B0FBA00001')
        self.rack(3, 'Truly unlisted', '444444444444')
        self.amazon('SKU-FBA', 'At Amazon', '222222222222', channel='AMAZON_NA', asin='B0FBA00001')
        self.amazon('SKU-OFF-FBA', 'Old FBA', '444444444444', status='Inactive', channel='AMAZON_NA')
        payload = self.scan()
        self.assertEqual(payload['listings'], [])
        self.assertEqual([row['upc'] for row in payload['unlisted']], ['444444444444'])
        self.assertEqual(payload['stock_counts'], {'unlisted': 1})

    def test_no_upc_listing_gets_a_title_suggestion_and_its_location_note(self):
        self.rack(1, 'Portmeirion Botanic Garden Teaspoons Set of 6', '749151436145', location='or3s1b1')
        self.rack(2, 'Lawrence Frames Silver Cluster Frame 5x7', '751148079136')
        self.ebay('301', 'Portmeirion Botanic Garden Teaspoons, Set of 6', None, sku='office')
        row = self.by_key(self.scan())[('ebay', '301')]
        self.assertEqual(row['state'], 'suggested')
        self.assertEqual(row['hint'], 'office')
        self.assertEqual(row['suggestions'][0]['searchrack_id'], 1)
        self.assertEqual(row['suggestions'][0]['location'], 'or3s1b1')
        self.assertNotIn(2, [s['searchrack_id'] for s in row['suggestions']])

    def test_sibling_products_do_not_become_suggestions(self):
        # Same family, different item: the words that differ are the product.
        self.rack(1, 'Marquis by Waterford Raymond Bowl 10 inch', '701587469579')
        self.ebay('401', 'Marquis by Waterford Marquis Raymond Vase 10"', '701587469586')
        self.sql('rawbol.db', "INSERT INTO raw_bol_items VALUES ('701587469586', 'Raymond Vase', 'L-7')")
        row = self.by_key(self.scan())[('ebay', '401')]
        # The vase's own UPC was received and is gone. A bowl is a conflicting
        # product type, so it must not be offered as a match at all.
        self.assertEqual(row['state'], 'sold_out')
        self.assertTrue(row['received'])
        self.assertEqual(row['received_lot'], 'L-7')
        self.assertEqual(row['suggestions'], [])

    def test_conflicting_product_type_without_history_is_unaccounted(self):
        self.rack(1, 'Marquis by Waterford Raymond Bowl 10 inch', '701587469579')
        self.ebay('402', 'Marquis by Waterford Raymond Vase 10"', '701587469586')
        row = self.by_key(self.scan())[('ebay', '402')]
        self.assertEqual(row['state'], 'unaccounted')
        self.assertEqual(row['suggestions'], [])

    def test_sharing_only_the_brand_under_one_maker_prefix_is_not_a_suggestion(self):
        self.rack(1, 'Godinger Cake Tray Platter Holiday', '028199123456')
        self.rack(2, 'Godinger Cake Tray Platter Holiday', '028199123456', location='b2')
        self.rack(3, 'Godinger Beverage Carafe 2.5 Qt', '028199555555')
        self.amazon('SKU-G', 'Beverage Pitcher Water Jug by Godinger 2.5 Qt', '0028199976029')
        row = self.by_key(self.scan())[('amazon', 'SKU-G')]
        self.assertEqual(row['state'], 'suggested')
        # The carafe shares "godinger" and "qt"; the cake tray shares only the brand.
        self.assertEqual([s['searchrack_id'] for s in row['suggestions']], [3])
        self.assertGreaterEqual(row['suggestions'][0]['score'], recon.SUGGEST_THRESHOLD)

    def test_one_item_at_two_locations_is_one_suggestion(self):
        self.rack(1, 'Portmeirion Botanic Garden Teaspoons Set of 6', '749151436145', location='a1')
        self.rack(2, 'Portmeirion Botanic Garden Teaspoons Set of 6', '749151436145', location='b7')
        self.ebay('301', 'Portmeirion Botanic Garden Teaspoons, Set of 6', None)
        row = self.by_key(self.scan())[('ebay', '301')]
        self.assertEqual(len(row['suggestions']), 1)

    def test_received_but_gone_is_sold_out_and_nothing_known_is_unaccounted(self):
        self.ebay('501', 'Godinger pitcher', '028199976029')
        self.ebay('502', 'Something new', '999999999999')
        self.amazon('SKU-ARCH', 'Archived thing', '840191206702')
        self.sql('bol.db', "INSERT INTO bol_items VALUES ('28199976029', 'Godinger pitcher', '16423315')")
        self.sql('searchRack.db', "INSERT INTO archived_searchrack VALUES ('840191206702', 'zero_quantity_auto_delete')")
        rows = self.by_key(self.scan())
        self.assertEqual(rows[('ebay', '501')]['state'], 'sold_out')
        self.assertEqual(rows[('ebay', '501')]['received_lot'], '16423315')
        self.assertEqual(rows[('amazon', 'SKU-ARCH')]['state'], 'sold_out')
        self.assertEqual(rows[('ebay', '502')]['state'], 'unaccounted')

    def test_blank_rack_titles_borrow_the_bol_description_for_suggestions(self):
        self.rack(1, '', '025398215393', location='gfloor3')
        self.sql('rawbol.db', "INSERT INTO raw_bol_items VALUES ('25398215393', 'Lifetime Blake 16 Pc Round Black Dinnerware', 'L-1')")
        self.amazon('SKU-B', 'Lifetime Blake 16 Piece Round Dinnerware Set, Black', 'B00NOUPC00')
        row = self.by_key(self.scan())[('amazon', 'SKU-B')]
        self.assertEqual(row['state'], 'suggested')
        self.assertEqual(row['suggestions'][0]['searchrack_id'], 1)
        self.assertEqual(row['suggestions'][0]['title'], 'Lifetime Blake 16 Pc Round Black Dinnerware')

    def test_sister_listing_on_the_other_store_lends_its_rack_rows(self):
        self.rack(1, 'Hotel Collection Fluted Wood Serving Board', '762120414654')
        self.amazon('SKU-C', 'Hotel Collection Fluted Wood Serving Board', '0762120414654')
        self.ebay('601', 'Hotel Collection Fluted Wood Serving Board', None)
        row = self.by_key(self.scan())[('ebay', '601')]
        self.assertEqual(row['state'], 'suggested')
        self.assertEqual(row['suggestions'][0]['searchrack_id'], 1)
        self.assertEqual(row['suggestions'][0]['reason'], 'same title listed on amazon')

    def test_manual_links_and_dismissals_are_honoured(self):
        self.rack(7, 'Mystery mug', '555555555555', quantity=4, location='mb1')
        self.ebay('701', 'Linked mug', None)
        self.ebay('702', 'Handled listing', None)
        self.ebay('703', 'Handled but now on rack', '555555555555')
        self.sql('listing_alerts.db', '''
            CREATE TABLE listing_inventory_matches (id INTEGER PRIMARY KEY, store TEXT, listing_key TEXT COLLATE NOCASE,
                listing_id TEXT, marketplace_barcode TEXT, listing_title TEXT, searchrack_id INTEGER,
                inventory_barcode TEXT, inventory_location TEXT, created_at TEXT, updated_at TEXT)''')
        self.sql('listing_alerts.db', '''INSERT INTO listing_inventory_matches
            (store, listing_key, searchrack_id, inventory_barcode, inventory_location)
            VALUES ('ebay', '701', 7, '555555555555', 'mb1')''')
        self.sql('listing_alerts.db', '''CREATE TABLE dismissed_alerts (id INTEGER PRIMARY KEY, alert_type TEXT, upc TEXT,
            store TEXT, listing_ids TEXT, dismissed_at TEXT, snapshot_hash TEXT)''')
        for key in ('702', '703'):
            self.sql('listing_alerts.db', "INSERT INTO dismissed_alerts (alert_type, upc, snapshot_hash) VALUES ('reconcile', '', ?)",
                     (recon.listing_hash('ebay', key),))
        payload = self.scan()
        rows = self.by_key(payload)
        linked = rows[('ebay', '701')]
        self.assertEqual((linked['state'], linked['match_kind'], linked['warehouse_qty']), ('accounted', 'linked', 4))
        self.assertEqual(linked['warehouse'][0]['location'], 'mb1')
        self.assertEqual(rows[('ebay', '702')]['state'], 'handled')
        self.assertEqual(rows[('ebay', '703')]['state'], 'accounted')
        self.assertEqual(payload['totals']['live'], 2)
        self.assertEqual(payload['totals']['coverage_pct'], 66.7)

    def test_exact_and_suffix_stock_are_both_counted(self):
        self.rack(1, 'Plate', '882864344090', quantity=2)
        self.rack(2, 'Plate', '882864344090-1', quantity=1)
        self.ebay('801', 'Plate', '882864344090', qty='3')
        row = self.by_key(self.scan())[('ebay', '801')]
        self.assertEqual((row['warehouse_qty'], row['short_by']), (3, 0))

    def test_embedded_numbers_and_custom_codes_do_not_auto_match(self):
        self.rack(1, 'Unknown', 'CUSTOM882864344090')
        self.rack(2, 'Other', '727870181140')
        self.ebay('802', 'Nothing similar', '882864344090')
        self.ebay('803', 'Nothing similar', None, sku='X727870181140Z')
        self.ebay('804', 'Nothing similar', '727870181140999999')
        for row in self.scan()['listings']:
            self.assertNotEqual(row['state'], 'accounted')

    def test_missing_reused_or_empty_link_is_not_accounted(self):
        self.rack(1, 'Linked mug', '555555555555')
        self.ebay('805', 'Linked mug', None)
        # Exercise the real save route and its shared match table.
        with patch.object(recon.ss_listing_alerts, '_ensure_listing_alerts_tables', lambda: None):
            with app.app_context():
                recon._load_user_decisions()
            response = self.client.post('/api/listing-helper/inventory-match', json={
                'store': 'ebay', 'listing_key': '805', 'searchrack_id': 1,
                'listing_title': 'Linked mug', 'finder_learn': True})
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        self.assertEqual(self.scan()['listings'][0]['state'], 'accounted')
        for statement in (
            'UPDATE SEARCHRACK SET QUANTITY = 0 WHERE ID = 1',
            "UPDATE SEARCHRACK SET QUANTITY = 1, BARCODE = '666666666666' WHERE ID = 1",
            'DELETE FROM SEARCHRACK WHERE ID = 1',
        ):
            self.sql('searchRack.db', statement)
            row = self.scan()['listings'][0]
            self.assertNotEqual(row['state'], 'accounted')
            self.assertTrue(row['link']['stale'])
            self.assertEqual(row['warehouse_qty'], 0)
        with patch.object(recon.ss_listing_alerts, '_ensure_listing_alerts_tables', lambda: None):
            response = self.client.post('/api/listing-helper/inventory-match', json={
                'store': 'ebay', 'listing_key': '805', 'clear': True})
        self.assertEqual(response.status_code, 200)
        self.assertNotIn('link', self.scan()['listings'][0])
        with closing(sqlite3.connect(self.root / 'listing_alerts.db')) as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM finder_aliases').fetchone()[0], 0)

    def test_store_read_failure_does_not_report_complete_coverage(self):
        self.sql('amazonStore.db', 'DROP TABLE ITEMS')
        response = self.client.get('/api/listing-reconciliation?refresh=1')
        self.assertEqual(response.status_code, 500)
        self.assertFalse(response.get_json()['success'])

    def test_cross_store_suggestions_deduplicate_locations(self):
        for row_id in range(1, 5):
            self.rack(row_id, 'Fluted wood serving board', '762120414654', location=str(row_id))
        self.amazon('SKU-C', 'Fluted wood serving board', '0762120414654')
        self.ebay('806', 'Fluted wood serving board', None)
        row = self.by_key(self.scan())[('ebay', '806')]
        self.assertEqual(len(row['suggestions']), 1)

    def test_custom_and_learned_names_help_only_stocked_rows(self):
        self.sql('bol.db', 'CREATE TABLE custom_item_registry (upc TEXT, item_description TEXT)')
        self.sql('bol.db', "INSERT INTO custom_item_registry VALUES ('777000000051', 'Yinka Lori Dream Forever Mug')")
        self.amazon('MUG', 'Yinka Ilori Objects DREAM FOREVER Mug', 'B0HFTDZV8K')
        self.rack(1, 'Yinka Ilori Objects Woven Placemats', '197300363949')
        row = self.by_key(self.scan())[('amazon', 'MUG')]
        self.assertEqual(row['suggestions'], [])
        self.assertEqual(row['state'], 'unaccounted')
        self.rack(2, 'Custom item', '777000000051-1', location='ofloor2')
        row = self.by_key(self.scan())[('amazon', 'MUG')]
        self.assertEqual(row['state'], 'suggested')
        self.assertEqual(row['suggestions'][0]['searchrack_id'], 2)
        self.assertIn('Yinka Lori Dream Forever Mug', row['suggestions'][0]['alternate_titles'])
        self.sql('sold.db', 'CREATE TABLE finder_aliases (barcode_key TEXT, title TEXT)')
        self.sql('sold.db', "INSERT INTO finder_aliases VALUES ('777000000051-1', 'Dream Forever Ceramic Coffee Mug')")
        self.ebay('ALIAS', 'Ceramic Coffee Dream Forever Mug', None)
        row = self.by_key(self.scan())[('ebay', 'ALIAS')]
        self.assertEqual(row['suggestions'][0]['searchrack_id'], 2)

    def test_reordered_names_and_accents_rank_the_correct_item_first(self):
        self.rack(1, 'Cafe Dream Forever Mug', '777000000051')
        self.rack(2, 'Cafe Love Always Mug', '777000000052')
        self.rack(3, 'Cafe Dream Forever Napkins', '777000000053')
        self.ebay('NAMES', 'Forever Dream Café Mug', None)
        row = self.by_key(self.scan())[('ebay', 'NAMES')]
        self.assertEqual(row['suggestions'][0]['searchrack_id'], 1)
        self.assertNotIn(3, [candidate['searchrack_id'] for candidate in row['suggestions']])

    def test_pack_size_difference_cannot_override_missing_stock_history(self):
        self.rack(1, 'Premium Garden Dinnerware Set 12 Piece', '882864344090')
        self.ebay('PACK', 'Premium Garden Dinnerware Set 16 Piece', '882864344091')
        self.sql('bol.db', "INSERT INTO bol_items VALUES ('882864344091','Dinnerware 16 Piece','L-1')")
        row = self.by_key(self.scan())[('ebay', 'PACK')]
        self.assertEqual(row['state'], 'sold_out')
        self.assertTrue(row['suggestions'])
        self.assertLess(row['suggestions'][0]['score'], recon.STRONG_SUGGESTION)

    def test_brand_type_color_piece_priority_is_weighted(self):
        names = recon.name_matching
        query = names.attributes('Lenox Blue Dinnerware Set of 16')
        def score(title):
            return names.weighted_similarity(query, names.attributes(title), 1)[0]
        self.assertGreater(score('Lenox Blue Dinnerware Set of 12'), score('Mikasa Blue Dinnerware Set of 16'))
        self.assertGreater(score('Lenox Red Dinnerware Set of 16'), score('Lenox Blue Towels Set of 16'))
        self.assertGreater(score('Lenox Blue Dinnerware Set of 12'), score('Lenox Red Dinnerware Set of 16'))
        self.assertGreater(score('Lenox Blue Dinnerware Set of 16'), score('Lenox Blue Dinnerware Set of 12'))
        self.assertGreater(score('Blue Dinnerware Set of 16'), recon.SUGGEST_THRESHOLD)
        self.assertEqual(names.attributes('Yinka Lori Dream Forever Mug')['brands'], ['Yinka Ilori'])
        self.assertEqual(names.attributes('Villeroy & Boch White 12-pc Dinnerware')['pieces'], 12)
        self.assertEqual(names.attributes('Lenox Mug 16 oz')['pieces'], None)

    def test_brand_candidates_are_not_lost_when_brand_tokens_are_common(self):
        for row_id in range(1, 70):
            self.rack(row_id, 'Lenox Blue Dinnerware Set of 12', str(882864344000+row_id))
        self.ebay('BRAND', 'Lenox Blue Dinnerware Set of 16', None)
        row = self.by_key(self.scan())[('ebay', 'BRAND')]
        self.assertTrue(row['suggestions'])
        self.assertIn('same brand', row['suggestions'][0]['reason'])

    def test_claude_route_searches_full_stock_not_client_candidates(self):
        self.rack(1, 'Lenox Blue Mug', '777000000051')
        self.rack(2, 'Custom ceramic coffee vessel', 'CUSTOM-2')
        self.rack(3, 'No stock', 'CUSTOM-3', quantity=0)
        self.ebay('AI', 'Lenox Blue Mug', None)
        result={'checked_at':123,'catalog_rows':2,'estimated_cost_usd':.003,
                'matches':[{'searchrack_id':2,'verdict':'possible','reason':'Name may describe the same item'}]}
        with patch.object(recon.ai_matching,'search',return_value=(result,False)) as search:
            response=self.client.post('/api/listing-reconciliation',json={'action':'search_warehouse',
                'store':'ebay','listing_key':'AI','title':'UNTRUSTED','suggestions':[],'catalog':[]})
        self.assertEqual(response.status_code,200,response.get_data(as_text=True))
        passed=search.call_args.args[1]
        self.assertEqual(passed['title'],'Lenox Blue Mug')
        self.assertEqual(search.call_args.args[2].ids,{1,2})
        self.assertEqual(response.get_json()['suggestions'][0]['searchrack_id'],2)
        self.assertEqual(response.get_json()['suggestions'][0]['ai_verdict'],'possible')
        self.assertEqual(self.scan()['listings'][0]['state'],'suggested')

    def test_claude_search_handles_zero_suggestions_and_rejects_stale_stock(self):
        self.rack(2, 'Unrelated custom title', 'CUSTOM-2')
        self.ebay('AI', 'Yinka Dream Mug', None)
        self.assertEqual(self.scan()['listings'][0]['suggestions'],[])
        def changed(conn,listing,catalog):
            self.sql('searchRack.db','UPDATE SEARCHRACK SET QUANTITY=0 WHERE ID=2')
            return {'matches':[]},False
        with patch.object(recon.ai_matching,'search',side_effect=changed):
            response=self.client.post('/api/listing-reconciliation',json={'action':'search_warehouse','store':'ebay','listing_key':'AI'})
        self.assertEqual(response.status_code,409)

    def test_removed_photo_action_and_get_never_call_claude(self):
        with patch.object(recon.ai_matching,'search') as search:
            response=self.client.post('/api/listing-reconciliation',json={'action':'compare_photos'})
            self.assertEqual(response.status_code,400)
            self.scan()
            search.assert_not_called()

    def test_page_renders(self):
        response = self.client.get('/listing-reconciliation')
        self.assertEqual(response.status_code,302)
        self.assertEqual(response.headers['Location'],'/store-listing-helper')
        response = self.client.get('/store-listing-helper')
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'Listings &amp; Stock',response.data)
        self.assertNotIn(b'>Store Doctor<',response.data)
        self.assertIn(b'/api/listing-reconciliation', response.data)
        self.assertIn(b'/api/listing-helper/inventory-match', response.data)

    def test_physical_pending_available_and_alerts_share_one_calculation(self):
        self.rack(1,'Lenox Mug','100000000001',quantity=3)
        self.ebay('STOCK','Lenox Mug','100000000001',qty='3')
        self.amazon('SAME','Lenox Mug','100000000001',qty=1)
        self.sql('sold.db',"INSERT INTO orders (barcode,quantity,store,paid_time) VALUES ('100000000001',2,'ebay',datetime('now'))")
        payload=self.scan()
        rows=self.by_key(payload)
        ebay=rows['ebay','STOCK']; amazon=rows['amazon','SAME']
        self.assertEqual((ebay['physical_qty'],ebay['pending_sale_qty'],ebay['available_qty']),(3,2,1))
        self.assertEqual(ebay['stock_status'],'short_stock')
        self.assertEqual(amazon['stock_status'],'in_stock')
        # The same two sold units are shared across offers, not subtracted twice.
        self.assertEqual(amazon['available_qty'],1)
        with patch.object(recon.ss_listing_alerts.ss_sync,'_sync_manager_overdue_alert',return_value=None):
            alerts=self.client.get('/api/listing-helper/scan?refresh=1').get_json()
        self.assertEqual(alerts['counts']['quantity_alert'],payload['stock_counts']['short_stock'])
        self.assertEqual(alerts['alerts']['quantity_alert'][0]['effective_qty'],1)
        self.sql('listing_alerts.db',"INSERT INTO dismissed_alerts (alert_type,upc,snapshot_hash) VALUES ('quantity_alert','100000000001',?)",(ebay['review_hash'],))
        recon.ss_listing_alerts.ss_caching._listing_helper_scan_cache_clear()
        with patch.object(recon.ss_listing_alerts.ss_sync,'_sync_manager_overdue_alert',return_value=None):
            self.assertEqual(self.client.get('/api/listing-helper/scan').get_json()['counts']['quantity_alert'],0)
        self.sql('sold.db',"INSERT INTO orders (barcode,quantity,store,paid_time) VALUES ('100000000001',1,'amazon',datetime('now'))")
        self.assertTrue(all(l['stock_status']=='out_of_stock' for l in self.scan()['listings']))

    def test_removed_cancelled_old_and_test_sales_do_not_reduce_available_stock(self):
        self.rack(1,'Lenox Mug','100000000001',quantity=5)
        self.ebay('STOCK','Lenox Mug','100000000001',qty='5')
        self.sql('sold.db',"INSERT INTO orders VALUES ('100000000001',1,1,0,'ebay',datetime('now'),NULL,NULL,NULL,NULL,NULL,NULL)")
        self.sql('sold.db',"INSERT INTO orders VALUES ('100000000001',1,0,1,'ebay',datetime('now'),NULL,NULL,NULL,NULL,NULL,NULL)")
        self.sql('sold.db',"INSERT INTO orders VALUES ('100000000001',1,0,0,'ebay',datetime('now','-8 days'),NULL,NULL,NULL,NULL,NULL,NULL)")
        self.sql('sold.db',"INSERT INTO orders VALUES ('100000000001',1,0,0,'test',datetime('now'),NULL,NULL,NULL,NULL,NULL,NULL)")
        row=self.scan()['listings'][0]
        self.assertEqual((row['available_qty'],row['stock_status']),(5,'in_stock'))

    def test_pending_sales_follow_confirmed_custom_match_and_review_reopens_when_stock_changes(self):
        self.rack(1,'Custom Mug','CUSTOM-MUG',quantity=2)
        self.ebay('CUSTOM','Lenox Mug','100000000001',qty='2')
        self.scan()
        self.sql('listing_alerts.db',"INSERT INTO listing_inventory_matches (store,listing_key,searchrack_id,inventory_barcode) VALUES ('ebay','CUSTOM',1,'CUSTOM-MUG')")
        self.sql('sold.db',"INSERT INTO orders (barcode,quantity,store,listing_listing_id,paid_time) VALUES ('100000000001',1,'ebay','CUSTOM',datetime('now'))")
        row=self.scan()['listings'][0]
        self.assertEqual((row['available_qty'],row['stock_status']),(1,'short_stock'))
        self.sql('listing_alerts.db',"INSERT INTO dismissed_alerts (alert_type,upc,snapshot_hash) VALUES ('stock_review','CUSTOM-MUG',?)",(row['review_hash'],))
        self.assertEqual(self.scan()['listings'][0]['stock_status'],'reviewed')
        self.sql('searchRack.db','UPDATE SEARCHRACK SET QUANTITY=1 WHERE ID=1')
        row=self.scan()['listings'][0]
        self.assertEqual(row['stock_status'],'out_of_stock')

    def test_unlisted_stock_respects_real_links_facebook_and_numeric_suffixes(self):
        self.rack(1,'Stocked mug','100000000001-1')
        self.rack(2,'Stocked mug','100000000001-2')
        self.rack(3,'Custom mug','CUSTOM-MUG')
        self.rack(4,'Facebook plate','100000000004')
        self.ebay('CUSTOM','Other name',None)
        self.scan()
        self.sql('listing_alerts.db',"INSERT INTO listing_inventory_matches (store,listing_key,searchrack_id,inventory_barcode) VALUES ('ebay','CUSTOM',3,'CUSTOM-MUG')")
        self.sql('bol.db','ALTER TABLE bol_items ADD COLUMN listed_facebook INTEGER DEFAULT 0')
        self.sql('bol.db',"INSERT INTO bol_items VALUES ('100000000004','Plate','LOT',1)")
        payload=self.scan()
        self.assertEqual(len(payload['unlisted']),1)
        row=payload['unlisted'][0]
        self.assertEqual(row['physical_qty'],2)
        self.assertEqual(len(row['warehouse']),2)
        self.assertEqual(row['stock_status'],'unlisted')
        self.assertEqual(payload['stock_counts']['unlisted'],1)

    def test_unknown_stock_is_not_reported_as_zero_available(self):
        self.ebay('UNKNOWN','Mystery Mug',None)
        row=self.scan()['listings'][0]
        self.assertEqual(row['stock_status'],'needs_matching')
        self.assertIsNone(row['physical_qty'])
        self.assertIsNone(row['available_qty'])

    def test_pending_suffix_unit_does_not_consume_another_linked_unit(self):
        self.rack(1,'Mug','100000000001-1')
        self.rack(2,'Mug','100000000001-2')
        self.ebay('ONE','Mug','DIFFERENT')
        self.scan()
        self.sql('listing_alerts.db',"INSERT INTO listing_inventory_matches (store,listing_key,searchrack_id,inventory_barcode) VALUES ('ebay','ONE',1,'100000000001-1')")
        self.sql('sold.db',"INSERT INTO orders (barcode,quantity,store,paid_time) VALUES ('100000000001-2',1,'ebay',datetime('now'))")
        row=self.scan()['listings'][0]
        self.assertEqual((row['physical_qty'],row['available_qty']),(1,1))

    def test_more_matches_pages_distinct_products_without_claude(self):
        for i in range(1,9):
            self.rack(i, 'Lenox Blue Mug', str(100000000000+i))
        self.rack(90, 'Lenox Blue Mug', '100000000001-2')
        self.rack(91, 'Lenox Blue Mug', '0100000000001')
        self.ebay('MORE', 'Lenox Blue Mug', None)
        listing=self.scan()['listings'][0]
        shown=listing['suggestions'][:]
        self.assertEqual(len(shown),3)
        self.assertTrue(listing['has_more_suggestions'])
        with patch.object(recon.ai_matching,'search') as claude:
            for expected_count,has_more in [(3,True),(2,False),(0,False)]:
                response=self.client.post('/api/listing-reconciliation',json={
                    'action':'more_matches','store':'ebay','listing_key':'MORE',
                    'seen_ids':[s['searchrack_id'] for s in shown],
                    'seen_barcodes':[s['barcode'] for s in shown]})
                self.assertEqual(response.status_code,200,response.get_data(as_text=True))
                data=response.get_json()
                self.assertEqual(len(data['suggestions']),expected_count)
                self.assertEqual(data['has_more_suggestions'],has_more)
                self.assertFalse({s['searchrack_id'] for s in shown}&{s['searchrack_id'] for s in data['suggestions']})
                shown.extend(data['suggestions'])
            claude.assert_not_called()
        self.assertEqual({s['searchrack_id'] for s in shown},set(range(1,9)))
        self.assertEqual(self.scan()['totals']['accounted'],0)

    def test_more_matches_rechecks_stock_and_rejects_resolved_or_invalid_input(self):
        for i in range(1,6):
            self.rack(i, 'Lenox Blue Mug', str(100000000000+i))
        self.ebay('MORE', 'Lenox Blue Mug', None)
        shown=self.scan()['listings'][0]['suggestions']
        body={'action':'more_matches','store':'ebay','listing_key':'MORE',
              'seen_ids':[s['searchrack_id'] for s in shown]}
        self.sql('searchRack.db','UPDATE SEARCHRACK SET QUANTITY=0 WHERE ID=4')
        response=self.client.post('/api/listing-reconciliation',json=body)
        self.assertEqual([s['searchrack_id'] for s in response.get_json()['suggestions']],[5])
        self.assertEqual(self.client.post('/api/listing-reconciliation',json=dict(body,seen_ids='bad')).status_code,400)
        self.sql('ebayStore.db',"UPDATE INVENTORY SET UPC='100000000001'")
        self.assertEqual(self.client.post('/api/listing-reconciliation',json=body).status_code,409)


if __name__ == '__main__':
    unittest.main()
