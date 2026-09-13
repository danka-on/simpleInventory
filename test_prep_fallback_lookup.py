"""Item Prep's backup lookups: local first, then online catalogs, never wasting the free quota."""
from contextlib import ExitStack, closing
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

import DBmanager
from sweetshelves.bootstrap import app
from sweetshelves import config, prep_fallback_lookup as fallback, runtime

VALID_UPC = '035886415884'
STORE_CODE = '014137189599'  # check digit fails: a store-internal code


def _never(*_args, **_kwargs):
    raise AssertionError('an online catalog was asked when it should not have been')


class PrepFallbackLookupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.root = Path(self.temp.name)
        self.stack = ExitStack()
        self.stack.enter_context(patch.object(config, 'BASE_DIR', self.root))
        self.stack.enter_context(patch.object(DBmanager, 'BASE_DIR', str(self.root)))
        self.client = app.test_client()
        runtime.cache.clear()
        self.sql('searchRack.db', 'CREATE TABLE SEARCHRACK (ID INTEGER PRIMARY KEY, TITLE TEXT, BARCODE TEXT, IMAGE TEXT, QUANTITY INTEGER)')
        self.sql('rawbol.db', 'CREATE TABLE raw_bol_items (upc TEXT, item_description TEXT, image_url TEXT)')
        self.sql('bol.db', '''CREATE TABLE bol_items (id INTEGER PRIMARY KEY, upc TEXT, item_description TEXT,
            image_url TEXT, lot_number TEXT, bol_number TEXT, import_date TEXT, temporary INTEGER DEFAULT 0,
            unchecked_qty INTEGER DEFAULT 0, original_qty INTEGER DEFAULT 0, good_qty INTEGER DEFAULT 0,
            bad_qty INTEGER DEFAULT 0)''')
        self.sql('ebayStore.db', 'CREATE TABLE INVENTORY (ID INTEGER PRIMARY KEY, UPC TEXT, Title TEXT, Image TEXT)')
        self.sql('amazonStore.db', 'CREATE TABLE ITEMS (UPC TEXT, TITLE TEXT, IMAGE TEXT, LAST_UPDATED TEXT)')

    def tearDown(self):
        self.stack.close()
        self.temp.cleanup()

    def sql(self, db, statement, args=()):
        with closing(sqlite3.connect(self.root / db)) as conn:
            conn.execute(statement, args)
            conn.commit()

    def rows(self, db, statement):
        with closing(sqlite3.connect(self.root / db)) as conn:
            return conn.execute(statement).fetchall()

    def providers(self, amazon=_never, ebay=_never, upcitemdb=_never):
        self.stack.enter_context(patch.object(fallback, '_amazon_candidate', amazon))
        self.stack.enter_context(patch.object(fallback, '_ebay_candidate', ebay))
        self.stack.enter_context(patch.object(fallback, '_upcitemdb_candidate', upcitemdb))

    def lookup(self, upc):
        response = self.client.get(f'/api/items-prep/fallback-lookup?upc={upc}')
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        return response.get_json()

    def test_check_digit(self):
        self.assertTrue(fallback._is_valid_gtin(VALID_UPC))
        self.assertTrue(fallback._is_valid_gtin('4003686419049'))
        self.assertFalse(fallback._is_valid_gtin(STORE_CODE))
        self.assertEqual(fallback._gtin_for('35886415884'), VALID_UPC)
        self.assertEqual(fallback._gtin_for(STORE_CODE), '')

    def test_a_name_this_app_already_holds_is_used_without_going_online(self):
        self.providers()
        self.sql('rawbol.db', "INSERT INTO raw_bol_items VALUES (?, 'Henckels knife block', 'https://img/k.jpg')", (VALID_UPC,))
        payload = self.lookup(VALID_UPC)
        self.assertTrue(payload['found'])
        self.assertEqual(payload['candidate']['title'], 'Henckels knife block')
        self.assertEqual(payload['candidate']['source_label'], 'Macy BOL')

    def test_a_store_internal_code_never_reaches_the_online_catalogs(self):
        self.providers()
        payload = self.lookup(STORE_CODE)
        self.assertFalse(payload['found'])
        self.assertEqual(payload['reason'], 'not_a_upc')

    def test_falls_through_to_the_next_catalog_and_remembers_the_answer(self):
        calls = []

        def amazon(gtin):
            calls.append('amazon')
            return None

        def ebay(gtin):
            calls.append('ebay')
            raise RuntimeError('eBay returned HTTP 500')

        def upcitemdb(gtin):
            calls.append('upcitemdb')
            return fallback._candidate('upcitemdb', 'Henckels 14-pc block set', 'https://img/h.jpg')

        self.providers(amazon, ebay, upcitemdb)
        payload = self.lookup('35886415884')
        self.assertTrue(payload['found'])
        self.assertEqual(payload['candidate']['source'], 'upcitemdb')
        self.assertEqual([t['result'] for t in payload['tried']], ['no match', 'error', 'found'])
        self.assertEqual(calls, ['amazon', 'ebay', 'upcitemdb'])

        again = self.lookup(VALID_UPC)
        self.assertTrue(again['cached'])
        self.assertEqual(again['candidate']['title'], 'Henckels 14-pc block set')
        self.assertEqual(calls, ['amazon', 'ebay', 'upcitemdb'])

    def test_a_clean_miss_is_not_asked_again_the_same_day(self):
        calls = []
        miss = lambda gtin: calls.append(gtin)
        self.providers(miss, miss, miss)
        self.assertFalse(self.lookup(VALID_UPC)['found'])
        self.assertFalse(self.lookup(VALID_UPC)['found'])
        self.assertEqual(len(calls), 3)

    def test_a_miss_with_an_error_is_retried(self):
        calls = []

        def broken(gtin):
            calls.append(gtin)
            raise RuntimeError('daily free limit reached')

        self.providers(broken, broken, broken)
        self.lookup(VALID_UPC)
        self.lookup(VALID_UPC)
        self.assertEqual(len(calls), 6)

    def test_adopting_creates_a_custom_bol_row_for_prep(self):
        response = self.client.post('/api/items-prep/fallback-adopt', json={
            'upc': VALID_UPC, 'title': 'Henckels 14-pc block set', 'image_url': 'https://img/h.jpg'})
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        self.assertEqual(self.rows('bol.db', 'SELECT upc, item_description, image_url, bol_number FROM bol_items'),
                         [('35886415884', 'Henckels 14-pc block set', 'https://img/h.jpg', 'CUSTOM')])
        self.assertEqual(self.rows('bol.db', 'SELECT upc, item_description, title_override FROM custom_item_registry'),
                         [('35886415884', 'Henckels 14-pc block set', 0)])

    def test_the_scan_that_missed_does_not_hide_the_adopted_item(self):
        # /api/bol_lookup caches per query string for ten minutes; the page's
        # re-lookup right after Use this item must not get that stale miss.
        self.stack.callback(os.chdir, os.getcwd())
        os.chdir(self.root)  # api_bol_lookup opens bol.db relative to the working directory
        url = '/api/bol_lookup?upc=035886415884&lot_number=17098519'
        self.assertFalse(self.client.get(url).get_json()['found'])
        adopted = self.client.post('/api/items-prep/fallback-adopt', json={
            'upc': VALID_UPC, 'title': 'Henckels 14-pc block set', 'image_url': 'https://img/h.jpg'})
        self.assertEqual(adopted.status_code, 200, adopted.get_data(as_text=True))
        payload = self.client.get(url).get_json()
        self.assertTrue(payload['found'], payload)
        self.assertEqual(payload['item']['bol_number'], 'CUSTOM')
        self.assertEqual(payload['item']['item_description'], 'Henckels 14-pc block set')

    def test_adopting_never_overwrites_a_manifest_row(self):
        self.sql('bol.db', "INSERT INTO bol_items (upc, item_description, image_url, bol_number) VALUES ('35886415884', 'Macy title', '', 'BOL1')")
        self.client.post('/api/items-prep/fallback-adopt', json={
            'upc': VALID_UPC, 'title': 'Online title', 'image_url': 'https://img/h.jpg'})
        self.assertEqual(self.rows('bol.db', 'SELECT item_description, image_url, bol_number FROM bol_items'),
                         [('Macy title', 'https://img/h.jpg', 'BOL1')])

    def test_adopting_a_placeholder_or_a_script_image_is_refused(self):
        for body in ({'upc': VALID_UPC, 'title': 'Unknown'},
                     {'upc': VALID_UPC, 'title': 'Knife block', 'image_url': 'javascript:alert(1)'}):
            response = self.client.post('/api/items-prep/fallback-adopt', json=body)
            self.assertEqual(response.status_code, 400)
        self.assertEqual(self.rows('bol.db', 'SELECT COUNT(*) FROM bol_items'), [(0,)])


if __name__ == '__main__':
    unittest.main()
