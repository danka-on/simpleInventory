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
        self.stack.enter_context(patch.dict(recon._alert_snapshot, {'generated_at': None, 'listings': None, 'unlisted': None}))
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
        self.assertEqual(row['suggestions'][0]['reason'], 'same title as a live Amazon listing')

    def test_duplicate_listings_live_or_ended_lend_rack_rows_despite_listing_noise(self):
        # Rack text shares nothing with the listings, so only a duplicate listing can find these rows.
        self.rack(1, 'Assorted Kitchen Goods', '860000000001')
        self.rack(2, 'Assorted Home Decor', '860000000002')
        self.rack(3, 'Assorted Wall Art', '860000000003')
        self.ebay('801', '1 x Harborview Walnut Serving Board Black $45', '860000000001', state='Unsold')
        self.ebay('802', 'Harborview Walnut Serving Board, Black', None)
        self.amazon('FBA-FEN', 'Fenwick Gilded Photo Frame', '860000000002', channel='AMAZON_NA', asin='B0FENWICK1')
        self.ebay('803', 'FENWICK gilded photo frame', None)
        self.ebay('804', 'Wrenfield Botanical Canvas Print', '860000000003')
        self.ebay('805', 'Wrenfield Botanical Canvas Print', None)
        rows = self.by_key(self.scan())
        brief = lambda key: [(s['searchrack_id'], s['score'], s['reason'], s.get('claimed_by')) for s in rows[('ebay', key)]['suggestions']]
        self.assertEqual(rows[('ebay', '802')]['state'], 'suggested')
        self.assertEqual(brief('802'), [(1, .95, 'same title as an ended eBay listing', None)])
        # A live duplicate on either store, FBA included, lends the rows it already has, labelled.
        self.assertEqual(brief('803'), [(2, .95, 'same title as a live Amazon listing', 'Amazon FBA listing "Fenwick Gilded Photo Frame"')])
        self.assertEqual(brief('805'), [(3, .95, 'same title as a live eBay listing', 'eBay listing "Wrenfield Botanical Canvas Print"')])

    def test_duplicate_titles_never_vouch_for_themselves_or_a_variant(self):
        self.rack(1, 'Assorted Mugs', '860000000011')
        self.rack(2, 'Assorted Ornaments', '860000000024')
        self.rack(3, 'Assorted Flatware', '860000000031')
        self.rack(4, 'Assorted Bowls', '860000000041')
        self.rack(5, 'Assorted Tumblers', '860000000051')
        self.ebay('901', 'Harborview Stoneware Mug Set of 4', '860000000011', state='Unsold')
        self.ebay('902', 'Quillmere Annual Edition Ornament 2024', '860000000024', state='Sold')
        self.ebay('903', 'Wrenfield 20-Piece Flatware Set Monogram Letter G', '860000000031', state='Unsold')
        self.amazon('ALD-1', 'Alderbrook Glazed Serving Bowl Blue', '860000000041', asin='B0ALDERBK1')
        self.ebay('904', 'Harborview Stoneware Tumbler', '860000000051', state='Unsold')
        with app.app_context():
            _, index, _ = recon._load_warehouse_index()
        ask = lambda title, store='ebay', key='Q', item_id='Q': [(s['searchrack_id'], s['score']) for s in index.twin_suggestions(
            {'store': store, 'listing_key': key, 'item_id': item_id, 'sku': '', 'hint': '', 'title': title})]
        self.assertEqual(ask('Harborview Stoneware Mug Set of 4', key='901', item_id='901'), [])  # its own ended copy
        self.assertEqual(ask('NEW Harborview Stoneware Mug, Set of 4'), [(1, .95)])
        self.assertEqual(ask('Harborview Stoneware Mug Set of 4, 12oz'), [])  # a size the other lacks may be another variant
        self.assertEqual(ask('Harborview Stoneware Mug'), [])  # a single is not the set
        self.assertEqual(ask('harborview stoneware tumbler'), [(5, .95)])
        self.assertEqual(ask('Harborview Stoneware Tumbler Set'), [])  # nor is a set without a count
        self.assertEqual(ask('Harborview Stoneware Mug Set of 4 Blue'), [])  # nor another color
        self.assertEqual(ask('Quillmere Annual Edition Ornament'), [])  # nor another year's edition
        self.assertEqual(ask('Wrenfield 20-Piece Flatware Set Monogram Letter G'), [(3, .95)])
        self.assertEqual(ask('Wrenfield 20-Piece Flatware Set Monogram Letter H'), [])  # nor another monogram
        self.assertEqual(ask('Alderbrook Glazed Serving Bowl Blue'), [(4, .95)])  # a live listing on the other store
        self.assertEqual(ask('Alderbrook Glazed Serving Bowl Blue', store='amazon', key='ALD-1', item_id='B0ALDERBK1'), [])

    def test_a_row_lent_by_a_live_and_an_ended_twin_names_the_live_one(self):
        self.rack(1, 'Assorted Vases', '860000000061')
        self.ebay('910', 'Thornbury Crystal Bud Vase', '860000000061', state='Unsold')
        self.amazon('TCB-1', 'Thornbury Crystal Bud Vase', '860000000061', asin='B0THORNBR1')
        self.ebay('911', 'Thornbury Crystal Bud Vase', None)
        top = self.by_key(self.scan())[('ebay', '911')]['suggestions'][0]
        # The reason and the "Already matched" line point at the same listing.
        self.assertEqual((top['searchrack_id'], top['reason'], top['claimed_by']),
                         (1, 'same title as a live Amazon listing', 'Amazon listing "Thornbury Crystal Bud Vase"'))

    def test_a_twin_with_another_barcode_never_outweighs_the_listings_own_gone_stock(self):
        self.rack(1, 'Assorted Vases', '860000000601')
        self.ebay('920', 'Wexcombe Crystal Bud Vase', '860000000601', state='Unsold')
        self.ebay('921', 'Wexcombe Crystal Bud Vase', '860000000699')
        self.sql('bol.db', "INSERT INTO bol_items VALUES ('860000000699', 'Bud vase', 'L-3')")
        self.ebay('922', 'Wexcombe Crystal Bud Vase', None)
        rows = self.by_key(self.scan())
        brief = lambda key: (rows[('ebay', key)]['state'], [(s['searchrack_id'], s['score']) for s in rows[('ebay', key)]['suggestions']])
        # Its own barcode was received and is gone; a same-title listing with another barcode may be another variant.
        self.assertEqual(brief('921'), ('sold_out', [(1, recon.BORROWED_NAME_CAP)]))
        # With no barcode of its own the twin still vouches for the row.
        self.assertEqual(brief('922'), ('suggested', [(1, recon.TWIN_SCORE)]))

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

    def test_an_ended_listing_name_completes_a_cut_short_rack_title(self):
        self.rack(1, 'Harbor Lane Stoneware Spk Rmn Bw', '860000000011')
        self.rack(2, 'Harbor Lane Stoneware Speckled Soup Bowls, Grey', '860000000012')
        self.ebay('OLD', 'Harbor Lane Stoneware Speckled Ramen Bowl, Set of 2', '860000000011', state='Unsold')
        # "Bowls": not the same title, so the old listing lends a name, not its row.
        self.ebay('NEW', 'Harbor Lane Stoneware Speckled Ramen Bowls, Set of 2', None)
        row = self.by_key(self.scan())[('ebay', 'NEW')]
        self.assertEqual([s['searchrack_id'] for s in row['suggestions']][:1], [1])
        # A borrowed name ranks rows only: never strong, never shown as the rack title or saved as an alias.
        self.assertLess(row['suggestions'][0]['score'], recon.STRONG_SUGGESTION)
        self.assertEqual(row['suggestions'][0]['title'], 'Harbor Lane Stoneware Spk Rmn Bw')
        self.assertEqual(row['suggestions'][0]['alternate_titles'], [])

    def test_a_sister_models_old_name_does_not_stand_in_for_the_row(self):
        self.rack(1, 'Pinecrest Home Wooden Jewe Brown', '860000000021')
        self.rack(2, 'Pinecrest Birchwood Jew', '860000000022')
        self.ebay('NEW', 'Pinecrest Home Birchwood Wooden Jewelry Box', None)
        ranked = lambda: [(s['searchrack_id'], s['score']) for s in self.by_key(self.scan())[('ebay', 'NEW')]['suggestions']]
        before = ranked()
        # Only the old name says "Alder", so the rack words cannot show the sibling check two models differ.
        self.ebay('OLD', 'Pinecrest Home Alder Wooden Jewelry Box', '860000000021', state='Sold')
        self.assertEqual(ranked(), before)
        self.assertEqual(before[0][0], 2)

    def cove_rack(self):
        self.rack(1, 'Cove 4PC SET BASIC', '860000000031')
        for row_id, title in enumerate(['Harbor Freight Tool Box Red', 'Dotted Swiss Curtain Panel Blue', 'Place Card Holders Gold',
                                        'Setting Powder Compact Pink', 'Jigsaw Piece Puzzle Ocean'], 2):
            self.rack(row_id, title, f'86000000003{row_id}')

    def test_a_listing_is_never_matched_by_its_own_old_name(self):
        self.cove_rack()
        # eBay keeps the ended copy of a relisted item under the same ItemID.
        self.ebay('SELF', 'Cove Harbor Dotted 4 Piece Place Setting', '860000000031', state='Unsold')
        self.ebay('SELF', 'Cove Harbor Dotted 4 Piece Place Setting', None)
        self.amazon('OTHER', 'Cove Harbor Dotted 4 Piece Place Setting', None, asin='B0COVEOTHR')
        rows = self.by_key(self.scan())
        self.assertEqual(rows[('ebay', 'SELF')]['suggestions'], [])
        self.assertEqual([s['searchrack_id'] for s in rows[('amazon', 'OTHER')]['suggestions']], [1])

    def test_sale_return_and_removal_titles_are_never_read(self):
        title = 'Cove Harbor Dotted 4 Piece Place Setting'
        self.cove_rack()
        self.amazon('CH-SELF', title, None, asin='B0COVESELF')
        self.ebay('OTHER', title, None)
        self.sql('sold.db', 'ALTER TABLE orders ADD COLUMN order_id TEXT')
        self.sql('sold.db', "INSERT INTO orders (order_id, barcode, quantity, rackupdated, store, sku, listing_asin)"
                 " VALUES ('ORD-1', '860000000031', 1, 1, 'amazon', 'CH-SELF', 'B0COVESELF')")
        self.sql('sold.db', 'CREATE TABLE returns (barcode TEXT, title TEXT, item_id TEXT, order_id TEXT, original_order_id TEXT)')
        self.sql('sold.db', "INSERT INTO returns VALUES ('860000000031', ?, '', 'ORD-1', NULL)", (title,))
        self.sql('rackhistory.db', '''CREATE TABLE removed_items (id INTEGER PRIMARY KEY, order_id TEXT, barcode TEXT, title TEXT,
            removal_type TEXT, source_row_json TEXT, result_row_json TEXT)''')
        for order_id, snapshot in (('ORD-1', None), (None, '{"TITLE": "Cove 4PC SET BASIC", "ITEMID": null}')):
            self.sql('rackhistory.db', "INSERT INTO removed_items (order_id, barcode, title, removal_type, source_row_json)"
                     " VALUES (?, '860000000031', ?, 'inventoryremoved', ?)", (order_id, title, snapshot))
        rows = self.by_key(self.scan())
        # These titles may be either listing's own words; only an ended listing under another ItemID lends a name.
        self.assertEqual(rows[('amazon', 'CH-SELF')]['suggestions'], [])
        self.assertEqual(rows[('ebay', 'OTHER')]['suggestions'], [])

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

    def test_truncated_run_together_and_plural_rack_words_still_rank_first(self):
        # BOL text is cut at a fixed width and runs words together; the rare words decide.
        self.rack(1, 'French Home French Home Belcourt Steak Kni Navy', '810000000001')
        self.rack(2, 'French Home Faux Birch Steak Knives, Set o', '810000000002')
        self.rack(5, 'Vietri Mug Cup Snowglobe', '810000000005')
        self.rack(6, 'BLUE HERON MUG', '810000000006')
        self.ebay('KNIFE', 'French Home Belcourt 4-Piece Steak Knife Set,Navy Blue', None)
        self.ebay('MUG', 'VIETRI Snowglobes Blue Mug', None)
        rows = self.by_key(self.scan())
        self.assertEqual(rows[('ebay', 'KNIFE')]['suggestions'][0]['searchrack_id'], 1)
        self.assertEqual(rows[('ebay', 'MUG')]['suggestions'][0]['searchrack_id'], 5)

    def test_rare_shared_words_outrank_brand_and_color_agreement(self):
        self.rack(1, 'Michael Aram Michael Aram Quince Paper Silver', '820000000001')
        self.rack(2, 'Michael Aram Michael Aram Heron Cocktail Black, Gold No Size', '820000000002')
        self.rack(3, 'Mele Co Mele Co. Ashford Wooden Jew Brown', '820000000003')
        self.rack(4, 'Mele Co Mele Co. Corisande Locking Wood Dark Brown', '820000000004')
        self.ebay('TOWEL', 'Michael Aram Quince Paper Towel Holder', None)
        self.ebay('BOX', 'Mele & Co Corisande Locking Wooden Jewelry Box', None)
        rows = self.by_key(self.scan())
        self.assertEqual(rows[('ebay', 'TOWEL')]['suggestions'][0]['searchrack_id'], 1)
        self.assertEqual(rows[('ebay', 'BOX')]['suggestions'][0]['searchrack_id'], 4)

    def test_one_word_rack_title_is_not_explained_by_a_longer_listing(self):
        self.rack(1, 'Lantern', '840000000001')
        self.rack(2, 'Coastal Stripe Linen Table Runner Blue', '840000000002')
        self.ebay('RUNNER', 'Harborview Lantern Linen Table Runner', None)
        suggestions = self.by_key(self.scan())[('ebay', 'RUNNER')]['suggestions']
        self.assertEqual([s['searchrack_id'] for s in suggestions], [2])

    def test_word_order_never_changes_scores_or_the_shortlist(self):
        # Set order follows PYTHONHASHSEED, so every production restart would see a different order.
        index = recon._RackIndex([
            {'id': 1, 'barcode': '850000000001', 'title': 'Salad Pl Placemat Cotton', 'alternate_titles': []},
            {'id': 2, 'barcode': '850000000002', 'title': 'Salad Plate Linen', 'alternate_titles': []},
            {'id': 3, 'barcode': '850000000003', 'title': 'Dinner Plate Cotton', 'alternate_titles': []},
            {'id': 4, 'barcode': '850000000004', 'title': 'Oval Platter Linen', 'alternate_titles': []}], {})
        words = ['salad', 'plate', 'placemat', 'platter', 'linen', 'cotton']
        terms = index.variant_terms[1][0][1]
        self.assertEqual(index._text_score(words, terms), index._text_score(words[::-1], terms))
        self.assertEqual(index._shortlist(words), index._shortlist(words[::-1]))

    def test_word_matching_helpers(self):
        self.assertEqual({recon._stem(w) for w in ('nutcrackers', 'glasses', 'dishes', 'berries', 'glass')},
                         {'nutcracker', 'glass', 'dish', 'berry'})
        index = recon._RackIndex([{'id': 1, 'barcode': '830000000001', 'title': 'GRDN LF TRKT DISH', 'alternate_titles': []}], {})
        self.assertIn('grdn', index._expansions('garden'))
        self.assertIn('trkt', index._expansions('trinket'))
        self.assertEqual(index._expansions('1234'), {})

    def test_brands_the_bol_writes_twice_are_learned_from_warehouse_titles_only(self):
        names = recon.name_matching
        self.rack(1, 'Ostara Ostara Braid Condiment Server Silver', '777000000061')
        self.rack(2, 'Ostara Ostara Braid Centerpiece Bowl Silver', '777000000062')
        self.rack(3, 'Blue Blue Stripe Mug', '777000000063')
        self.rack(4, 'Garden Blue Stripe Mug', '777000000064')
        self.rack(5, 'Harbor Blue Stripe Bowl', '777000000065')
        self.ebay('BRAID', 'Ostara Braid Condiment Server', None)
        self.ebay('SELF', 'Velmora Velmora Glass Vase', None)
        self.ebay('SELF2', 'Velmora Velmora Crystal Vase', None)
        row = self.by_key(self.scan())[('ebay', 'BRAID')]
        self.assertEqual(row['attributes']['brands'], ['Ostara'])
        self.assertEqual(row['suggestions'][0]['searchrack_id'], 1)
        self.assertIn('same brand', row['suggestions'][0]['reason'])
        # A colour that happens to repeat is not a brand, and listing titles teach nothing.
        self.assertEqual(names.attributes('Blue Blue Stripe Mug')['brands'], [])
        self.assertEqual(names.attributes('Velmora Velmora Glass Vase')['brands'], [])
        # Another rebuild's vocabulary applies at once: lookups cached under the old one are never served.
        self.assertTrue(names.learn_brands(['Velmora Velmora Glass Vase', 'Velmora Velmora Crystal Vase']))
        self.assertEqual(names.attributes('Velmora Velmora Glass Vase')['brands'], ['Velmora'])
        self.assertEqual(names.attributes('Ostara Braid Condiment Server')['brands'], [])

    def test_cut_off_bol_words_colours_and_sets_are_read_without_false_types(self):
        names = recon.name_matching
        stub = names.attributes('Corvella Corvella Harbor Cereal Bow Navy')
        self.assertEqual((stub['types'], stub['colors']), (['bowl'], ['navy']))
        self.assertEqual(names.attributes('Corvella Corvella Tessa Gold 5-Pc. Place')['types'], [])
        self.assertEqual(names.attributes('Marlowe Marlowe Fennel Bla No Color No')['types'], [])
        self.assertEqual(names.attributes('Corvella Wren Champagne Flutes')['types'], ['glass'])
        query = names.attributes('Corvella Wren Dessert Bowls, Set Of 4')
        dessert_set = names.weighted_similarity(query, names.attributes('Corvella Wren 12-Pc. Dessert Set'), 1)[0]
        towels = names.weighted_similarity(query, names.attributes('Corvella Wren Kitchen Towels'), 1)[0]
        self.assertGreater(dessert_set, towels)  # a set may hold the bowls; towels never do

    def test_missing_brand_or_type_lowers_a_product_name_match_without_hiding_it(self):
        names = recon.name_matching
        query = names.attributes('Lenox Quillon Meadow Stacking Salad Plate')
        code_row = names.attributes('QUILLON MEADOW STACKING SALAD PLT BASIC')
        self.assertGreater(names.weighted_similarity(query, code_row, .6, shared_names=3)[0],
                           names.weighted_similarity(query, code_row, .6, shared_names=0)[0])
        # A typeless sibling from the same collection stays below the row whose type matches.
        typed = names.weighted_similarity(query, names.attributes('Lenox Quillon Meadow Salad Plate'), .8, shared_names=3)[0]
        typeless = names.weighted_similarity(query, names.attributes('Lenox Quillon Meadow 7-Piece Tea'), .8, shared_names=2)[0]
        self.assertGreater(typed, typeless)
        self.rack(1, 'QUILLON MEADOW STACKING SALAD PLT BASIC', '777000000071')
        self.rack(2, 'Lenox Arbor Salad Plate', '777000000072')
        self.ebay('CODE', 'Lenox Quillon Meadow Stacking Gray Salad Plate', None)
        row = self.by_key(self.scan())[('ebay', 'CODE')]
        self.assertIn(1, [s['searchrack_id'] for s in row['suggestions']])

    def rack_index(self, *titles):
        # Filler rows put everyday words in the rack vocabulary, as on a real rack.
        filler = ['Garden Hose Reel Green', 'Bamboo Bath Mat', 'Cotton Kitchen Towel Set', 'Glass Candle Holder Clear',
                  'Wool Throw Blanket Gray', 'Stoneware Pasta Bowl White', 'Linen Table Runner Beige', 'Oak Picture Frame 5x7']
        return recon._RackIndex([{'id': row_id, 'barcode': f'86000000{row_id:04d}', 'title': title, 'alternate_titles': [],
                                  'location': '', 'quantity': 1, 'image': ''}
                                 for row_id, title in enumerate([*titles, *filler], 1)], {})

    def query(self, title):
        return {'store': 'ebay', 'listing_key': 'Q', 'listing_id': 'ebay-Q', 'title': title, 'upc': '', 'sku': ''}

    def ranked(self, index, title):
        return [(s['searchrack_id'], s['score']) for s in index.suggestions(self.query(title), limit=None)]

    def test_a_sibling_naming_another_product_line_ranks_below_the_line_named(self):
        # The sibling shares more family words; the rare line word each title names tells them apart.
        index = self.rack_index('Hampton Forge Quellbrook Walnut Serving Tray Brown Large', 'Hampton Forge Varnel Walnut Tray')
        ranked = self.ranked(index, 'Hampton Forge Varnel Walnut Serving Tray Brown Large')
        self.assertEqual([row_id for row_id, _ in ranked[:2]], [2, 1])
        self.assertLess(ranked[1][1], recon.STRONG_SUGGESTION)

    def test_a_relative_sharing_no_line_word_only_fills_an_otherwise_empty_list(self):
        title = 'Godinger Varnel Vase'
        with patch.object(recon, 'SIBLING_PENALTY', .9):  # every sibling falls below the threshold
            index = self.rack_index('Godinger Quellbrook Vase', 'Godinger Varnel Vase Clear', 'Varnel Throw Pillow')
            both = self.ranked(index, title)
            alone = self.ranked(self.rack_index('Godinger Quellbrook Vase', 'Varnel Throw Pillow'), title)
            claims = {2: {'amazon-OTHER': 'Amazon listing "Godinger Varnel Vase Clear"'}}
            claimed = recon._all_suggestions(index, self.query(title), claims=claims)
            index.add_twins([{'store': 'amazon', 'listing_key': 'OTHER', 'item_id': '', 'sku': '', 'hint': '', 'live': True,
                              'title': title, 'upc': index.by_id[2]['barcode']}])
            twin = recon._all_suggestions(index, self.query(title), claims=claims)
        self.assertEqual([row_id for row_id, _ in both], [2])
        self.assertEqual(alone, [(1, recon.SUGGEST_THRESHOLD)])
        # A row another listing has follows every free row, so it leaves the free list empty and the relative stays first...
        self.assertEqual([(s['searchrack_id'], s.get('claimed_by')) for s in claimed], [(1, None), (2, claims[2]['amazon-OTHER'])])
        # ...unless an identical title on the other store lends it, which keeps its rank.
        self.assertEqual([s['searchrack_id'] for s in twin], [2])

    def test_shared_brand_words_alone_do_not_explain_a_rack_title(self):
        index = self.rack_index('Hampton Forge x No Color', 'Oakmere Serving Tray Walnut')
        self.assertEqual([row_id for row_id, _ in self.ranked(index, 'Hampton Forge Oakmere Serving Tray')], [2, 1])
        # Words the rack never uses still count against a maker-only match.
        ranked = self.ranked(self.rack_index('Hampton Forge Ostrel Teapot Blue'), 'Hampton Forge Quillmar Menorah')
        self.assertTrue(all(score < recon.STRONG_SUGGESTION for _, score in ranked))
        # Brands learned from warehouse titles are brand words too.
        names = recon.name_matching
        self.addCleanup(names.learn_brands, [])
        names.learn_brands(['Tovrin Tovrin Oak Tray', 'Tovrin Tovrin Oak Bowl'])
        self.assertEqual(names.brand_words('Tovrin Harbor Tray'), {'tovrin'})

    def test_one_checkable_word_does_not_explain_a_listing_the_rack_never_names(self):
        # "White" is the only word of the hat the rack uses, so no white bowl is offered for it.
        self.assertEqual(self.ranked(self.rack_index('Harbor Lane Serving Bowl White'), 'Velmora Cashmere Beanie Knit Hat White'), [])
        # A rack word that spells a listing word abbreviated ("THSTLWD") is a checkable word too.
        ranked = self.ranked(self.rack_index('THSTLWD KEEPSAKE ORNATE WALNUT'), 'Penhallow Thistlewood Quenby Keepsake Harrowgate Casket')
        self.assertEqual([row_id for row_id, _ in ranked], [1])

    def test_colours_and_sizes_are_not_line_words_and_cut_or_abbreviated_rack_words_cover_line_words(self):
        index = self.rack_index('Oakmere Linen Napkin Graphite Queen', 'Rowan Jewe Box', 'Rowan Jew Tray', 'Rowan PLMR Tray')
        title = 'Oakmere Linen Napkin Graphite Queen'
        self.assertEqual(index._profile(title, recon._terms(title))[1], {'oakmere', 'linen'})
        self.assertEqual(index._cover('jewelry'), {'jewelry', 'jewe'})
        self.assertIn('plmr', index._cover('palmer'))

    def test_cut_off_or_abbreviated_rack_words_name_the_type_the_listing_spells(self):
        names = recon.name_matching
        # The stub alone reads only a closing word the type list decides; "PLT" and a glass "Cereal Bow" need the listing.
        self.assertEqual(names.attributes('Marlowe Stoneware Salad PLT Sage')['types'], [])
        self.assertEqual(names.attributes('Marlowe Stoneware Salad PLT Sage', (('plt', 'plate'),))['types'], ['plate'])
        self.assertEqual(names.attributes('Marlowe Opal Glass Cereal Bow', (('bow', 'bowl'),))['types'], ['bowl', 'glass'])
        # A whole word is no cut: a place setting stays a set for a placemat listing.
        self.assertEqual(names.attributes('Marlowe Linen Place Setting', (('place', 'placemat'),))['types'], ['set'])
        index = self.rack_index('Marlowe Opal Glass Cereal Bow', 'Marlowe Opal Glass Cereal Bo')
        title = 'Marlowe Opal Glass Cereal Bowl'
        # "Bo" could as well be a bottle or a box, so only "Bow" earns the bowl.
        self.assertGreater(index._score(recon._tokens(title), title, index.by_id[1]),
                           index._score(recon._tokens(title), title, index.by_id[2]))
        words, found = ['marlowe', 'opal', 'glass', 'cereal', 'bowl'], [{}, {}]
        index._text_score(words, index.variant_terms[1][0][1], partial=found[0])
        index._text_score(words[::-1], index.variant_terms[1][0][1], partial=found[1])
        self.assertEqual(found, [{'bow': 'bowl'}, {'bow': 'bowl'}])

    def test_sizes_match_however_the_numbers_are_written(self):
        self.rack(1, 'Harbor Linen Tablecloth 60X84', '870000000001')
        self.rack(2, 'Harbor Linen Tablecloth 60 x 120', '870000000002')
        self.rack(3, 'Harbor Linen Runner 14X108', '870000000003')
        self.rack(4, 'Harbor Linen Runner 14 x 72', '870000000004')
        self.rack(5, 'Harbor Linen Cluster Frame, 4x6', '870000000005')
        self.ebay('CLOTH', 'Harbor Linen Tablecloth 60" x 84" Ivory', None)
        self.ebay('RUNNER', 'Harbor Linen Table Runner 14" x 108"', None)
        self.ebay('FRAME', 'Harbor Linen Cluster Frame, 5x7', None)
        rows = self.by_key(self.scan())
        self.assertEqual(rows[('ebay', 'CLOTH')]['suggestions'][0]['searchrack_id'], 1)
        self.assertEqual(rows[('ebay', 'RUNNER')]['suggestions'][0]['searchrack_id'], 3)
        # 4x6 and 5x7 share every word once split, so only the size check keeps the sibling from being strong.
        frame = {s['searchrack_id']: s['score'] for s in rows[('ebay', 'FRAME')]['suggestions']}
        self.assertLess(frame[5], recon.STRONG_SUGGESTION)
        self.assertEqual(recon._tokens('Napkins 20\u201dx20\u201d'), recon._tokens('Napkins 20 x 20'))

    def test_a_listing_word_the_rack_never_uses_keeps_a_short_rack_title_from_being_certain(self):
        self.rack(1, 'Quellmoor Harborview Stoneware', '860000000701')
        self.rack(2, 'Linen Table Runner Blue', '860000000702')
        self.rack(3, 'Lenox Aldwick Matte Berry Dinner Plate', '860000000703')
        self.ebay('TEA', 'Quellmoor Harborview Stoneware Teacups, Set of 6', '860000000799')
        self.ebay('SPICE', 'Lenox Aldwick Matte Spice Dinner Plate', '860000000798')
        for upc in ('860000000799', '860000000798'):
            self.sql('bol.db', "INSERT INTO bol_items VALUES (?, 'Received', 'L-4')", (upc,))
        rows = self.by_key(self.scan())
        # Their own barcodes were received and are gone. The short row could hold any piece of that line,
        # and the plate row names a colourway ("Berry") the listing does not.
        for key, row_id in (('TEA', 1), ('SPICE', 3)):
            row = rows[('ebay', key)]
            self.assertEqual([s['searchrack_id'] for s in row['suggestions']], [row_id])
            self.assertLess(row['suggestions'][0]['score'], recon.STRONG_SUGGESTION)
            self.assertEqual(row['state'], 'sold_out')

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

    def test_concurrent_page_loads_share_one_rebuild(self):
        import threading, time
        calls = []
        def slow_build():
            calls.append(1)
            time.sleep(0.2)
            return {'success': True, 'generated_at': '2026-09-14T10:00:00', 'listings': [], 'unlisted': [],
                    'build': len(calls)}
        def page_load():
            with app.app_context():  # every real caller is a request
                recon._cached_payload(False)
        with patch.object(recon, 'build_reconciliation', side_effect=slow_build):
            threads = [threading.Thread(target=page_load) for _ in range(4)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()
            self.assertEqual(len(calls), 1)
            with app.app_context():
                recon.ss_listing_alerts.ss_caching._listing_helper_scan_cache_clear()
                self.assertEqual(recon._cached_payload(False)['build'], 2)
                self.assertEqual(recon._cached_payload(True)['build'], 3)

    def test_badge_and_alerts_screen_never_rebuild(self):
        self.rack(1,'Lenox Mug','100000000001',quantity=3)
        self.ebay('STOCK','Lenox Mug','100000000001',qty='3')
        self.sql('sold.db',"INSERT INTO orders (barcode,quantity,store,paid_time) VALUES ('100000000001',2,'ebay',datetime('now'))")
        badge = lambda: self.client.get('/api/listing-helper/scan?counts=1').get_json()
        with patch.object(recon.ss_listing_alerts.ss_sync,'_sync_manager_overdue_alert',return_value=None):
            with patch.object(recon, 'build_reconciliation') as build:
                self.assertTrue(badge()['not_built'])
                self.assertTrue(self.client.get('/api/listing-helper/scan?refresh=1').get_json()['not_built'])
                build.assert_not_called()
            payload = self.scan()  # opening Listings & Stock is the only rebuild
            review_hash = next(l['review_hash'] for l in payload['listings'] if l['stock_status'] == 'short_stock')
            # An app restart empties memory; the saved snapshot still serves the badge.
            recon._alert_snapshot.update(generated_at=None, listings=None, unlisted=None)
            with patch.object(recon, 'build_reconciliation') as build:
                self.assertEqual(badge()['counts']['quantity_alert'], 1)
                self.assertNotIn('alerts', badge())
                # Dismissing and undismissing on the Alerts screen apply without a rebuild.
                self.sql('listing_alerts.db',"INSERT INTO dismissed_alerts (alert_type,upc,snapshot_hash) VALUES ('quantity_alert','100000000001',?)",(review_hash,))
                self.assertEqual(badge()['counts']['quantity_alert'], 0)
                self.sql('listing_alerts.db','DELETE FROM dismissed_alerts')
                full = self.client.get('/api/listing-helper/scan').get_json()
                self.assertEqual(full['alerts']['quantity_alert'][0]['effective_qty'], 1)
                self.assertEqual(full['generated_at'], payload['generated_at'])
                build.assert_not_called()

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

    # Rows another active listing already has: still offered, ranked last, labelled.
    def ids(self, suggestions):
        return [s['searchrack_id'] for s in suggestions]

    def link(self, store, key, row_id, barcode):
        self.sql('listing_alerts.db', 'INSERT INTO listing_inventory_matches (store, listing_key, searchrack_id, inventory_barcode)'
                 ' VALUES (?, ?, ?, ?)', (store, key, row_id, barcode))

    def test_row_matched_by_upc_elsewhere_ranks_after_a_weaker_free_row(self):
        old_title = 'Lenox Blue Dinnerware Set of 16 Service for 4 Stoneware Dishwasher and Microwave Safe'
        self.rack(1, 'Lenox Blue Dinnerware Set of 16', '100000000001')
        self.rack(2, 'Lenox Blue Dinnerware Set of 12', '100000000002')
        self.ebay('OLD', old_title, '100000000001')
        self.ebay('NEW', 'Lenox Blue Dinnerware Set of 16', None)
        row = self.by_key(self.scan())[('ebay', 'NEW')]
        self.assertEqual(self.ids(row['suggestions']), [2, 1])
        free, taken = row['suggestions']
        self.assertGreater(taken['score'], free['score'])
        self.assertNotIn('claimed_by', free)
        self.assertEqual(taken['claimed_by'], 'eBay listing "' + old_title[:59].rstrip() + '…"')
        with app.app_context():  # a listing's own match never pushes its row down
            _, index, _ = recon._load_warehouse_index()
            listings = recon._load_listings()
            claims = recon._claimed_rows(listings, index, recon._load_user_decisions()[0])
            new = next(l for l in listings if l['listing_key'] == 'NEW')
            old = next(l for l in listings if l['listing_key'] == 'OLD')
            self.assertEqual(recon._all_suggestions(index, new), recon._all_suggestions(index, new, claims={}))
            self.assertEqual(self.ids(recon._all_suggestions(index, new)), [1, 2])
            own = recon._all_suggestions(index, dict(old, upc=''), claims=claims)
            self.assertEqual(own[0]['searchrack_id'], 1)
            self.assertFalse(any('claimed_by' in s for s in own))

    def test_hand_linked_row_is_pushed_down_for_other_listings(self):
        self.rack(7, 'Yinka Ilori Dream Forever Mug', 'CUSTOM-7', location='mb1')
        self.rack(8, 'Yinka Ilori Love Always Mug', 'CUSTOM-8')
        self.ebay('LINKED', 'Dream Forever Gift', None)
        self.amazon('OTHER', 'Yinka Ilori Dream Forever Mug', None)
        self.scan()
        self.link('ebay', 'LINKED', 7, 'CUSTOM-7')
        rows = self.by_key(self.scan())
        self.assertEqual(rows[('ebay', 'LINKED')]['state'], 'accounted')
        other = rows[('amazon', 'OTHER')]
        self.assertEqual(self.ids(other['suggestions']), [8, 7])
        self.assertEqual(other['suggestions'][1]['claimed_by'], 'hand-linked to eBay listing "Dream Forever Gift"')
        # A link whose row ID now holds another barcode claims nothing.
        self.sql('searchRack.db', "UPDATE SEARCHRACK SET BARCODE = 'CUSTOM-9' WHERE ID = 7")
        self.assertEqual(self.ids(self.by_key(self.scan())[('amazon', 'OTHER')]['suggestions']), [7, 8])

    def test_fba_stock_is_pushed_down_but_still_offered(self):
        self.rack(3, 'Portmeirion Botanic Garden Teaspoons Set of 6', '0749151436145')
        self.rack(4, 'Portmeirion Botanic Garden Teaspoons Set of 4', '749151436152')
        self.amazon('FBA-1', 'Teaspoons at Amazon', '749151436145', channel='AMAZON_NA', asin='B0FBA00001')
        self.ebay('301', 'Portmeirion Botanic Garden Teaspoons, Set of 6', None)
        row = self.by_key(self.scan())[('ebay', '301')]
        self.assertEqual(self.ids(row['suggestions']), [4, 3])
        # The FBA listing is named, so its title shows whether that stock is this product.
        self.assertEqual(row['suggestions'][1]['claimed_by'], 'Amazon FBA listing "Teaspoons at Amazon"')

    def test_free_location_of_a_claimed_product_is_offered_instead(self):
        self.rack(5, 'Lenox Butterfly Meadow Mug 12 oz', '200000000005', location='a1')
        self.rack(6, 'Lenox Butterfly Meadow Mug 16 oz', '200000000005', location='b7')
        self.ebay('LINKED', 'Kitchen shelf mug', None)
        self.ebay('Q', 'Lenox Butterfly Meadow Mug 12 oz', '300000000001')
        self.sql('bol.db', "INSERT INTO bol_items VALUES ('300000000001', 'Butterfly mug', 'L-2')")
        self.scan()
        self.link('ebay', 'LINKED', 5, '200000000005')
        with patch.object(recon, '_claimed_rows', return_value={}):
            before = self.by_key(self.scan())[('ebay', 'Q')]
        after = self.by_key(self.scan())[('ebay', 'Q')]
        self.assertEqual(self.ids(before['suggestions']), [5])
        self.assertEqual(self.ids(after['suggestions']), [6])
        self.assertNotIn('claimed_by', after['suggestions'][0])
        self.assertEqual(after['suggestions'][0]['location'], 'b7')
        # Row 6 alone scores under STRONG_SUGGESTION; the product's evidence keeps the state.
        self.assertEqual((before['state'], after['state']), ('suggested', 'suggested'))

    def test_push_down_never_changes_a_listing_state(self):
        title = 'Hotel Collection Fluted Wood Serving Board 18 inch'
        self.rack(1, title, '762120414654')
        self.rack(2, 'Hotel Collection Fluted Wood Serving Board 12 inch', '762120414999')
        amazon_title = 'Hotel Collection Serving Board'  # not the same title, so no exact-twin exception
        self.amazon('SKU-C', amazon_title, '0762120414654')
        self.ebay('601', title, '762120414661')
        self.sql('bol.db', "INSERT INTO bol_items VALUES ('762120414661', 'Serving board', 'L-9')")
        self.ebay('602', 'Lenox Mug', '555555555555')
        self.sql('bol.db', "INSERT INTO bol_items VALUES ('555555555555', 'Lenox Mug', 'L-9')")
        with patch.object(recon, '_claimed_rows', return_value={}):
            before = self.scan()
        after = self.scan()
        self.assertEqual({k: r['state'] for k, r in self.by_key(before).items()},
                         {k: r['state'] for k, r in self.by_key(after).items()})
        self.assertEqual(before['totals']['by_state'], after['totals']['by_state'])
        row = self.by_key(after)[('ebay', '601')]
        # The free board is weaker than STRONG_SUGGESTION and now ranks first; the
        # claimed same-title board still keeps a received listing in "needs a look".
        self.assertEqual(self.ids(row['suggestions']), [2, 1])
        self.assertLess(row['suggestions'][0]['score'], recon.STRONG_SUGGESTION)
        self.assertGreaterEqual(row['suggestions'][1]['score'], recon.STRONG_SUGGESTION)
        self.assertEqual((row['state'], row['suggestions'][1]['claimed_by']), ('suggested', f'Amazon listing "{amazon_title}"'))

    def test_identical_title_on_the_other_store_keeps_its_rank(self):
        self.rack(1, 'Harborview Linen Table Runner 72 inch', '860000000101')
        self.rack(2, 'Harborview Linen Table Runner 90 inch', '860000000102')
        self.amazon('SKU-T', 'Harborview  LINEN Table Runner 72 Inch', '860000000101')
        self.ebay('TWIN', 'harborview linen table runner 72 inch', None)
        self.ebay('NEAR', 'Harborview Linen Table Runner 72 inch Set of 2', None)
        rows = self.by_key(self.scan())
        label = 'Amazon listing "Harborview LINEN Table Runner 72 Inch"'
        # Case and spacing aside the titles are the same, so the Amazon row is most likely this item: rank kept, still labelled.
        twin = rows[('ebay', 'TWIN')]['suggestions']
        self.assertEqual([(s['searchrack_id'], s['reason'], s.get('claimed_by')) for s in twin],
                         [(1, 'same title as a live Amazon listing', label), (2, twin[1]['reason'], None)])
        self.assertGreater(twin[0]['score'], twin[1]['score'])
        # One extra word is a near title, not a twin: the claimed row goes last as usual.
        near = rows[('ebay', 'NEAR')]['suggestions']
        self.assertEqual([(s['searchrack_id'], s.get('claimed_by')) for s in near], [(2, None), (1, label)])

    def test_rows_other_listings_have_never_crowd_a_free_row_out_of_the_shortlist(self):
        spot = {'location': '', 'quantity': 1, 'image': '', 'alternate_titles': []}
        rows = [dict(spot, id=i, barcode=str(860000000200 + i), title='Quillmere Pewter Lantern Tall') for i in range(1, 41)]
        index = recon._RackIndex(rows + [dict(spot, id=41, barcode='860000000300', title='Quillmere Lantern')], {})
        listing = {'store': 'ebay', 'listing_key': 'Q', 'listing_id': 'ebay-Q', 'title': 'Quillmere Pewter Lantern', 'upc': '', 'sku': ''}
        ids = lambda claims: {s['searchrack_id'] for s in index.suggestions(listing, limit=None, claims=claims)}
        # The 40 lanterns share one more word, so they alone fill SHORTLIST_SIZE and row 41 is never scored.
        self.assertNotIn(41, ids(None))
        others = {i: {'ebay-OTHER': 'eBay listing "Quillmere"'} for i in range(1, 41)}
        # Once other listings have them they rank last anyway: the free row gets a place and they keep theirs.
        self.assertEqual(ids(others), set(range(1, 42)))
        self.assertEqual(recon._all_suggestions(index, listing, claims=others)[0]['searchrack_id'], 41)
        # The listing's own rows are not "taken".
        self.assertNotIn(41, ids({i: {'ebay-Q': 'own'} for i in range(1, 41)}))

    def test_more_matches_keep_claimed_rows_last_across_pages(self):
        for i in range(1, 9):
            self.rack(i, 'Lenox Blue Mug', str(100000000000+i))
        self.ebay('OLD1', 'Lenox Blue Mug Gift', '100000000001')
        self.ebay('OLD2', 'Lenox Blue Mug Gift', '100000000002')
        self.ebay('MORE', 'Lenox Blue Mug', None)
        listing = self.by_key(self.scan())[('ebay', 'MORE')]
        shown = listing['suggestions'][:]
        self.assertEqual((self.ids(shown), listing['has_more_suggestions']), ([3, 4, 5], True))
        pages = []
        for _ in range(3):
            data = self.client.post('/api/listing-reconciliation', json={
                'action': 'more_matches', 'store': 'ebay', 'listing_key': 'MORE',
                'seen_ids': self.ids(shown), 'seen_barcodes': [s['barcode'] for s in shown]}).get_json()
            pages.append((self.ids(data['suggestions']), data['has_more_suggestions']))
            shown.extend(data['suggestions'])
        self.assertEqual(pages, [([6, 7, 8], True), ([1, 2], False), ([], False)])
        self.assertEqual([s.get('claimed_by') for s in shown], [None] * 6 + ['eBay listing "Lenox Blue Mug Gift"'] * 2)

    def test_claude_matches_keep_the_already_matched_label(self):
        for i in range(1, 5):
            self.rack(i, 'Lenox Blue Mug', str(100000000000+i))
        self.ebay('OLD1', 'Lenox Blue Mug Gift', '100000000001')
        self.ebay('OLD4', 'Lenox Blue Mug Gift', '100000000004')
        self.ebay('Q', 'Lenox Blue Mug', None)
        result = {'checked_at': 123, 'catalog_rows': 4, 'estimated_cost_usd': .01,
                  'matches': [{'searchrack_id': 4, 'verdict': 'likely', 'reason': 'same mug'},
                              {'searchrack_id': 2, 'verdict': 'possible', 'reason': 'maybe'}]}
        label = 'eBay listing "Lenox Blue Mug Gift"'
        # Claude's picks lead in its order; a claimed pick still carries its label.
        expected = [(4, 'likely', label), (2, 'possible', None), (3, None, None), (1, None, label)]
        brief = lambda suggestions: [(s['searchrack_id'], s.get('ai_verdict'), s.get('claimed_by')) for s in suggestions]
        with patch.object(recon.ai_matching, 'cached', side_effect=lambda conn, entry, catalog: result if entry['listing_key'] == 'Q' else None):
            self.assertEqual(brief(self.by_key(self.scan())[('ebay', 'Q')]['suggestions']), expected)
        with patch.object(recon.ai_matching, 'search', return_value=(result, False)):
            response = self.client.post('/api/listing-reconciliation', json={'action': 'search_warehouse', 'store': 'ebay', 'listing_key': 'Q'})
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        self.assertEqual(brief(response.get_json()['suggestions']), expected)

    def test_already_matched_label_names_two_listings_then_counts_the_rest(self):
        self.rack(1, 'Lenox Blue Mug', '100000000001')
        long_title = 'Lenox Blue Mug ' + 'very long marketplace keyword stuffing ' * 4
        for key, title in (('R1', 'Lenox Blue Mug relist'), ('R2', 'Lenox Blue Mug relist'), ('R3', long_title), ('R4', 'Lenox Mug other')):
            self.ebay(key, title, '100000000001')
        self.amazon('A1', 'Lenox Blue Mug on Amazon', '100000000001')
        self.ebay('H', 'Hand linked mug', None)
        self.ebay('Q', 'Lenox Blue Mug', None)
        self.scan()
        self.link('ebay', 'H', 1, '100000000001')
        label = self.by_key(self.scan())[('ebay', 'Q')]['suggestions'][0]['claimed_by']
        # Two relists with one title share a label; five distinct owners show as two names + 3 more.
        names = label.split('; ')
        self.assertEqual((len(names), names[-1]), (3, '+3 more'))
        self.assertLessEqual(len(label), 200)
        with app.app_context():
            _, index, _ = recon._load_warehouse_index()
            listings = recon._load_listings()
            claims = recon._claimed_rows(listings, index, recon._load_user_decisions()[0])
            r3 = next(l for l in listings if l['listing_key'] == 'R3')
            self.assertEqual(len(claims[1]), 6)
            self.assertEqual(recon._claim_label(claims, r3, 1).split('; ')[-1], '+2 more')  # its own claim never counts
            self.assertEqual(recon._claim_label(claims, r3, 99), '')


if __name__ == '__main__':
    unittest.main()
