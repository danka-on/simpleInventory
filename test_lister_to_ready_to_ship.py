"""One unit, end to end: listed through the Lister, sold, then found on Ready to Ship.

The unit is a one-of-one suffixed warehouse row (`<upc>-1`), the kind the Lister lists as its own
SKU. Every step runs against the real code on temporary databases: the panel's link, the queue and
inventory-match rows it writes, the sold order the marketplace reports, and the `/sold-orders` feed
Ready to Ship renders. Decoy rows (a second unit and the plain base barcode) sit on other shelves,
so a match that is merely "close enough" fails these tests.
"""
from contextlib import closing
import datetime
import gc
import os
import sqlite3
import unittest
from unittest.mock import patch

import DBmanager
from sweetshelves import (runtime as ss_runtime, sales as ss_sales, sales_actions as ss_sales_actions,
                          shipping_orders as ss_shipping_orders, warehouse_locations as ss_warehouse_locations)
import test_lister

UPC = '761323062839'
UNIT = UPC + '-1'          # the one of one we list and sell
DECOY_UNIT = UPC + '-2'    # another unit of the same product, a different shelf
LISTING_ID = '187885365480'
SHELF = 'or1s3b4'
DECOY_SHELF = 'gr2s1b1'
BASE_SHELF = 'ba1s1b1'


def _now():
    return datetime.datetime.now().replace(microsecond=0)


class ListerToReadyToShipTest(unittest.TestCase):
    """Two keys tie a sale to the shelf: the SKU the panel listed under, and the listing the panel
    logged. Each of these tests takes one of them away to see whether the other still holds."""

    setUp = test_lister.ListerTestCase.setUp
    seed = test_lister.ListerTestCase.seed
    sql = test_lister.ListerTestCase.sql

    def setUpFixture(self):
        """Ready to Ship's feed, the temp databases and the one-of-one rack unit."""
        # Ready to Ship's feed opens connections it leaves to the garbage collector; on Windows the
        # temporary folder cannot be removed while one is alive, so collect before it is cleaned up.
        self.addCleanup(gc.collect)
        self._dbm = patch.object(DBmanager, 'BASE_DIR', str(self.root))
        self._dbm.start()
        self.addCleanup(self._dbm.stop)
        ss_runtime.cache.init_app(self.app)
        self.app.add_url_rule('/sold-orders', 'sold_orders', ss_sales.sold_orders)
        self.app.add_url_rule('/mark-order-handled', 'mark_order_handled', ss_sales_actions.mark_order_handled, methods=['POST'])
        self.app.add_url_rule('/api/ready-to-ship/location-options/<int:order_id>', 'location_options',
                              ss_shipping_orders.ready_to_ship_location_options)
        self.app.add_url_rule('/api/move_location', 'move_location', ss_warehouse_locations.api_move_location, methods=['POST'])
        # Confirming an order opens sold.db by a relative path, so the test runs from the temp folder.
        cwd = os.getcwd()
        os.chdir(self.root)
        self.addCleanup(os.chdir, cwd)
        with closing(sqlite3.connect(self.root / 'searchRack.db')) as conn:
            conn.execute('DELETE FROM SEARCHRACK')
            conn.executemany('''INSERT INTO SEARCHRACK (ID, TITLE, BARCODE, ITEM_POSITION, QUANTITY)
                                VALUES (?, ?, ?, ?, ?)''', [
                (101, 'NEW Nambe Hug Salt & Pepper Shakers | 2-Piece Set', UNIT, SHELF, 1),
                (102, 'NEW Nambe Hug Salt & Pepper Shakers | 2-Piece Set', DECOY_UNIT, DECOY_SHELF, 1),
                (103, 'NEW Nambe Hug Salt & Pepper Shakers | 2-Piece Set', UPC, BASE_SHELF, 4),
            ])
            conn.commit()
        with closing(sqlite3.connect(self.root / 'listagent.db')) as conn:
            conn.execute("INSERT INTO listing_queue (upc, title, status, added_at) VALUES (?, 'Nambe shakers', 'queued', ?)",
                         (UNIT, '2026-09-17T08:00:00'))
            conn.commit()

    def list_it(self, **overrides):
        """What the panel posts when the store's success page is reached: our unit is the SKU."""
        body = {'upc': UNIT, 'platform': 'ebay', 'listing_id': LISTING_ID, 'sku': UNIT,
                'title': 'NEW Nambe Hug Salt & Pepper Shakers | 2-Piece Set', 'price': 39.99, 'quantity': 1}
        body.update(overrides)
        res = self.client.post('/api/lister/links', json=body)
        self.assertEqual(res.status_code, 201, res.get_json())
        return res.get_json()['link']

    def store_listing(self, sku=UNIT, upc=UPC):
        """The row the eBay store sync keeps for the live listing ('None' is eBay's empty SKU)."""
        with closing(sqlite3.connect(self.root / 'ebayStore.db')) as conn:
            conn.execute('''INSERT INTO INVENTORY (Title, ItemID, SKU, UPC, List_State)
                            VALUES ('NEW Nambe Hug Salt & Pepper Shakers | 2-Piece Set', ?, ?, ?, 'Active')''',
                         (LISTING_ID, sku, upc))
            conn.commit()

    def sell_it(self, **overrides):
        """The sale, through the real eBay ingest: an order line with no warehouse knowledge of its own."""
        order = {'order_id': '02-13579-24680', 'item_id': LISTING_ID, 'sku': UNIT, 'store': 'ebay',
                 'title': 'NEW Nambe Hug Salt & Pepper Shakers | 2-Piece Set', 'quantity': 1, 'price': 39.99,
                 'paid_time': _now().isoformat(sep=' '), 'barcode': None}
        order.update(overrides)
        DBmanager.store_ebay_order(order)
        return self.sql('sold.db', 'SELECT * FROM orders ORDER BY id DESC LIMIT 1')[0]

    def sell_raw(self, **overrides):
        """A sold row written without the ingest's enrichment (a legacy or hand-made row)."""
        order = {'order_id': '02-13579-24680', 'item_id': LISTING_ID, 'sku': '', 'store': 'ebay',
                 'title': 'NEW Nambe Hug Salt & Pepper Shakers | 2-Piece Set', 'quantity': 1, 'price': 39.99,
                 'paid_time': _now().isoformat(sep=' '), 'barcode': '', 'source_upc': ''}
        order.update(overrides)
        with closing(sqlite3.connect(self.root / 'sold.db')) as conn:
            cur = conn.cursor()
            DBmanager.ensure_sold_orders_schema(cur, conn)
            columns = ', '.join(order)
            cur.execute(f"INSERT INTO orders ({columns}) VALUES ({', '.join('?' for _ in order)})", tuple(order.values()))
            conn.commit()
            return cur.lastrowid

    def ready_to_ship(self):
        ss_runtime.cache.clear()
        res = self.client.get('/sold-orders?days=7')
        self.assertEqual(res.status_code, 200, res.data[:400])
        return res.get_json()

    # -- the chain ---------------------------------------------------------------------------

    def test_listing_the_unit_writes_every_hop_of_the_trail(self):
        self.setUpFixture()
        link = self.list_it()
        self.assertEqual((link['upc'], link['sku'], link['listing_id'], link['kind']), (UNIT, UNIT, LISTING_ID, 'listed'))

        # 1. The queue row carries the store's keys, so a SKU or item number resolves back to our unit.
        queue = self.sql('listagent.db', 'SELECT * FROM listing_queue WHERE upc = ?', (UNIT,))[0]
        self.assertEqual((queue['listed_sku'], queue['listed_listing_id'], queue['listed_platform']), (UNIT, LISTING_ID, 'ebay'))
        self.assertTrue(queue['listed_ebay_at'])

        # 2. The inventory match points at the exact rack row, not at the product.
        match = self.sql('listing_alerts.db', 'SELECT * FROM listing_inventory_matches')[0]
        self.assertEqual((match['store'], match['listing_key']), ('ebay', LISTING_ID))
        self.assertEqual((match['searchrack_id'], match['inventory_barcode'], match['inventory_location']), (101, UNIT, SHELF))

        # 3. The Finder learns the same pairing, keyed by the store listing.
        alias = self.sql('listing_alerts.db', 'SELECT * FROM finder_aliases')[0]
        self.assertEqual(alias['source_key'], f'ebay:{LISTING_ID}')
        self.assertEqual(alias['barcode_key'].lower().replace(' ', ''), UNIT)

        # 4. Both directions of the panel's own lookup.
        resolved = self.client.get(f'/api/lister/resolve?platform=ebay&listing_id={LISTING_ID}').get_json()
        self.assertEqual(resolved['upc'], UNIT)
        self.assertEqual([l['listing_id'] for l in self.client.get('/api/lister/links?upc=' + UNIT).get_json()['links']], [LISTING_ID])

    def test_the_sold_order_lands_on_that_exact_shelf_unit(self):
        self.setUpFixture()
        self.store_listing()
        self.list_it()
        self.sell_it()
        orders = self.ready_to_ship()
        self.assertEqual(len(orders), 1, orders)
        order = orders[0]
        self.assertEqual(order['barcode'], UNIT, 'the sold SKU resolves to our suffixed unit')
        self.assertEqual(order['location'], SHELF)
        self.assertEqual(order['finder_searchrack_id'], 101)
        self.assertEqual(order['finder_matched_barcode'], UNIT)
        self.assertNotIn(DECOY_SHELF, str(order.get('locations') or order.get('location')))

    def test_an_order_with_no_sku_at_all_still_reaches_the_unit(self):
        """The live shape of an eBay listing with an empty SKU: the item number is the only key.

        The ingest resolves it through the listing log the panel wrote, and stamps the order with our
        suffixed UPC, so the rest of the chain runs as if the SKU had been there.
        """
        self.setUpFixture()
        self.store_listing(sku='None')  # eBay's empty SKU
        self.list_it()
        row = self.sell_it(sku='')
        self.assertEqual((row['source_upc'], row['barcode']), (UNIT, UNIT))
        self.assertEqual(row['listing_listing_id'], LISTING_ID)
        self.assertTrue(row['listing_trace_id'], 'the sale points back at the listing the panel logged')
        order = self.ready_to_ship()[0]
        self.assertEqual((order['location'], order['finder_searchrack_id']), (SHELF, 101))
        options, data, status = self.confirm(order)
        self.assertEqual((status, data.get('success')), (200, True), data)
        self.assertEqual(self.rack(), {101: 0, 102: 1, 103: 4})

    def test_a_store_sku_that_is_not_our_barcode_resolves_through_the_queue_row(self):
        """The listing's SKU need not be the UPC: listed_sku on the queue row carries the trail."""
        self.setUpFixture()
        self.store_listing(sku='NAMBE-HUG-SET')
        self.list_it(sku='NAMBE-HUG-SET')
        self.sell_it(sku='NAMBE-HUG-SET')
        order = self.ready_to_ship()[0]
        self.assertEqual(order['barcode'], UNIT)
        self.assertEqual((order['location'], order['finder_searchrack_id']), (SHELF, 101))
        options, data, status = self.confirm(order)
        self.assertEqual((status, data.get('success')), (200, True), data)
        self.assertEqual(self.rack(), {101: 0, 102: 1, 103: 4})

    def test_the_unit_can_change_shelves_after_it_is_listed(self):
        """The barcode is the identity; the shelf is only where it sits today."""
        self.setUpFixture()
        self.store_listing()
        self.list_it()
        moved = self.client.post('/api/move_location', json={'from_location': SHELF, 'to_location': 'or4s2b2',
                                                             'item_ids': [101]}).get_json()
        self.assertEqual(moved.get('moved'), 1, moved)
        self.sell_it()
        order = self.ready_to_ship()[0]
        self.assertEqual((order['barcode'], order['location']), (UNIT, 'or4s2b2'), 'the sale follows the unit')
        self.assertEqual(order['finder_searchrack_id'], 101, 'a whole move keeps the row, it only changes shelf')
        options, data, status = self.confirm(order)
        self.assertEqual((status, data.get('success')), (200, True), data)
        self.assertEqual(self.rack(), {101: 0, 102: 1, 103: 4})

    def test_a_sale_of_the_other_unit_does_not_borrow_this_link(self):
        self.setUpFixture()
        self.store_listing()
        self.list_it()
        self.sell_it(order_id='02-99999-11111', item_id='999888777666', sku=DECOY_UNIT)
        order = self.ready_to_ship()[0]
        self.assertEqual(order['barcode'], DECOY_UNIT)
        self.assertEqual(order['location'], DECOY_SHELF)
        self.assertEqual(order['finder_searchrack_id'], 102)

    def confirm(self, order):
        """What Ready to Ship posts when the order is confirmed: the same two fields the page sends."""
        suggested = order.get('location_match_barcode', '') if order.get('location_match_suggested') else ''
        query = f'?matched_barcode={suggested}' if suggested else ''
        options = self.client.get(f"/api/ready-to-ship/location-options/{order['id']}{query}").get_json()
        payload = {'id': order['id'], 'allocations': [], 'matched_barcode': suggested}
        res = self.client.post('/mark-order-handled', json=payload)
        return options, res.get_json(), res.status_code

    def rack(self):
        return {r['ID']: r['QUANTITY'] for r in self.sql('searchRack.db', 'SELECT ID, QUANTITY FROM SEARCHRACK')}

    def test_confirming_the_order_takes_the_unit_off_that_shelf_and_no_other(self):
        self.setUpFixture()
        self.store_listing()
        self.list_it()
        self.sell_it()
        order = self.ready_to_ship()[0]
        options, data, status = self.confirm(order)
        self.assertEqual(options.get('barcode'), UNIT, options)
        self.assertEqual((status, data.get('success')), (200, True), data)
        removed_from = [l.get('location_code') if isinstance(l, dict) else l for l in data.get('locations') or []]
        self.assertEqual(removed_from, [SHELF])
        self.assertEqual(self.rack(), {101: 0, 102: 1, 103: 4}, 'only the listed unit left the shelf')

    def test_a_row_that_missed_the_ingest_shows_the_shelf_but_cannot_be_confirmed_from_it(self):
        """The one thin spot: a sold row with no barcode of its own, written around the ingest.

        `/sold-orders` still finds the unit through the inventory match the panel wrote, so the page
        shows the right shelf. The location options and `/mark-order-handled` return early on the
        order's own empty barcode, before that suggested one is considered, so the operator has to
        match the order by hand or complete it without taking the unit off the shelf.
        """
        self.setUpFixture()
        self.store_listing(sku='None')
        self.list_it()
        self.sell_raw()
        order = self.ready_to_ship()[0]
        self.assertEqual((order['location'], order['finder_matched_barcode']), (SHELF, UNIT))
        options, data, status = self.confirm(order)
        self.assertEqual((options['barcode'], options['can_fulfill']), ('', False))
        self.assertEqual(status, 409)
        self.assertIn('no barcode yet', data['error'])
        self.assertEqual(self.rack(), {101: 1, 102: 1, 103: 4}, 'nothing left the shelf')

    def test_the_ledger_ties_the_order_back_to_the_listing_the_panel_made(self):
        self.setUpFixture()
        self.store_listing()
        self.list_it()
        self.sell_it()
        entries = self.client.get('/api/lister/ledger').get_json()['links']
        self.assertEqual(len(entries), 1, entries)
        entry = entries[0]
        self.assertEqual((entry['upc'], entry['listing_id']), (UNIT, LISTING_ID))
        self.assertEqual([s['order_id'] for s in entry['sales']], ['02-13579-24680'])

    def test_unlinking_takes_the_shelf_mapping_back_out(self):
        self.setUpFixture()
        link = self.list_it()
        self.store_listing(sku='None')
        self.assertEqual(self.client.delete(f"/api/lister/links/{link['id']}").status_code, 200)
        self.assertEqual(self.sql('listing_alerts.db', 'SELECT COUNT(*) AS n FROM listing_inventory_matches')[0]['n'], 0)
        queue = self.sql('listagent.db', 'SELECT * FROM listing_queue WHERE upc = ?', (UNIT,))[0]
        self.assertEqual((queue['status'], queue['listed_ebay_at'], queue['listed_sku']), ('queued', None, None))
        self.sell_it(sku='')
        order = self.ready_to_ship()[0]
        # Without the panel's link the store listing only says "761323062839", so the sale lands on the
        # plain-barcode shelf instead of the unit: identifying the exact one of one is what the link adds.
        self.assertEqual((order['barcode'], order['location'], order['finder_searchrack_id']), (UPC, BASE_SHELF, 103))


if __name__ == '__main__':
    unittest.main()
