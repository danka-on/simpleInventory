"""Prepared Items store scan only ever adds marketplace checks for live listings."""
from contextlib import closing
import os
import sqlite3
import tempfile
import unittest

from sweetshelves.bootstrap import app
from sweetshelves import listing_lifecycle


class ItemsToListStoreScanTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.cwd = os.getcwd()
        os.chdir(self.temp.name)
        self.client = app.test_client()
        self.sql('bol.db', '''
            CREATE TABLE bol_items (id INTEGER PRIMARY KEY, upc TEXT, lot_number TEXT, item_description TEXT,
                quantity INTEGER DEFAULT 1, import_date TEXT,
                good_qty INTEGER DEFAULT 0, bad_qty INTEGER DEFAULT 0, itemprepped INTEGER DEFAULT 0,
                list_status TEXT, listed_amazon INTEGER DEFAULT 0, listed_amazon_date TEXT, listed_amazon_source TEXT,
                listed_ebay INTEGER DEFAULT 0, listed_ebay_date TEXT, listed_ebay_source TEXT,
                listed_facebook INTEGER DEFAULT 0, listed_facebook_date TEXT, listed_facebook_qty INTEGER,
                listed_facebook_source TEXT)''')
        self.sql('bol.db', '''CREATE TABLE items_prep_status (id INTEGER PRIMARY KEY, upc TEXT, lot_number TEXT,
            status TEXT, reason TEXT, note TEXT, quantity INTEGER, updated_at TEXT)''')
        self.sql('ebayStore.db', '''CREATE TABLE INVENTORY (ID INTEGER PRIMARY KEY, Title TEXT, ItemID TEXT,
            Quantity TEXT, URL TEXT, List_State TEXT, UPC TEXT)''')
        self.sql('amazonStore.db', '''CREATE TABLE ITEMS (ID INTEGER PRIMARY KEY, ASIN TEXT, SKU TEXT, TITLE TEXT,
            QUANTITY INTEGER, STATUS TEXT, UPC TEXT, FULFILLMENT_CHANNEL TEXT, LAST_UPDATED TEXT)''')
        self.sql('sync_settings.db', 'CREATE TABLE sync_status (key TEXT PRIMARY KEY, value TEXT)')
        self.sql('sync_settings.db', "INSERT INTO sync_status VALUES ('ebay_listings_last', '2026-09-12T17:33:33-04:00')")

    def tearDown(self):
        os.chdir(self.cwd)
        self.temp.cleanup()

    def sql(self, db, statement, args=()):
        with closing(sqlite3.connect(db)) as conn:
            conn.execute(statement, args)
            conn.commit()

    def bol(self, row_id, upc, good=1, bad=0, status='good', lot='L1', **flags):
        cols = ', '.join(['id', 'upc', 'lot_number', 'good_qty', 'bad_qty', *flags])
        marks = ', '.join('?' for _ in range(5 + len(flags)))
        self.sql('bol.db', f'INSERT INTO bol_items ({cols}) VALUES ({marks})', (row_id, upc, lot, good, bad, *flags.values()))
        if status:
            self.sql('bol.db', 'INSERT INTO items_prep_status (upc, lot_number, status) VALUES (?, ?, ?)', (upc, lot, status))

    def row(self, row_id):
        with closing(sqlite3.connect('bol.db')) as conn:
            conn.row_factory = sqlite3.Row
            return dict(conn.execute('SELECT * FROM bol_items WHERE id = ?', (row_id,)).fetchone())

    def scan(self):
        response = self.client.post('/api/bol_items/scan_stores', json={})
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        return response.get_json()

    def test_live_listings_check_boxes_and_record_store_scan_source(self):
        self.bol(1, '719978859014')
        self.bol(2, '885837012345')
        self.sql('ebayStore.db', "INSERT INTO INVENTORY (ItemID, Quantity, List_State, UPC) VALUES ('1880001', '2', 'Active', '0719978859014')")
        self.sql('amazonStore.db', "INSERT INTO ITEMS (ASIN, QUANTITY, STATUS, UPC, FULFILLMENT_CHANNEL) VALUES ('B0FBA1', 0, 'Active', '885837012345', 'AMAZON_NA')")

        payload = self.scan()

        self.assertEqual(payload['added'], {'ebay': 1, 'amazon': 1})
        self.assertEqual(payload['synced_at']['ebay'], '2026-09-12T17:33:33-04:00')
        first, second = self.row(1), self.row(2)
        self.assertEqual((first['listed_ebay'], first['listed_ebay_source'], first['list_status']), (1, 'store_scan', 'listed'))
        self.assertEqual(first['listed_amazon'], 0)
        self.assertEqual((second['listed_amazon'], second['listed_amazon_source']), (1, 'store_scan'))
        self.assertEqual(listing_lifecycle._listing_source_label('store_scan'), 'Store scan')

        self.assertEqual(self.scan()['added'], {'ebay': 0, 'amazon': 0})

    def test_scan_never_unchecks_and_ignores_dead_or_unprepared_listings(self):
        self.bol(1, '111111111111', listed_ebay=1, listed_ebay_source='user', listed_amazon=1, listed_amazon_source='user')
        self.bol(2, '222222222222', good=0, status='')
        self.bol(3, '333333333333')
        self.bol(4, '444444444444', itemprepped=1)
        for upc in ('222222222222', '444444444444'):
            self.sql('ebayStore.db', "INSERT INTO INVENTORY (ItemID, Quantity, List_State, UPC) VALUES ('9', '1', 'Active', ?)", (upc,))
        self.sql('ebayStore.db', "INSERT INTO INVENTORY (ItemID, Quantity, List_State, UPC) VALUES ('8', '1', 'Unsold', '333333333333')")
        self.sql('ebayStore.db', "INSERT INTO INVENTORY (ItemID, Quantity, List_State, UPC) VALUES ('7', '0', 'Active', '333333333333')")
        self.sql('amazonStore.db', "INSERT INTO ITEMS (ASIN, QUANTITY, STATUS, UPC, FULFILLMENT_CHANNEL) VALUES ('B0MFN', 0, 'Active', '333333333333', 'DEFAULT')")

        payload = self.scan()

        self.assertEqual(payload['added'], {'ebay': 0, 'amazon': 0})
        kept = self.row(1)
        self.assertEqual((kept['listed_ebay'], kept['listed_amazon'], kept['listed_ebay_source']), (1, 1, 'user'))
        for row_id in (2, 3, 4):
            self.assertEqual((self.row(row_id)['listed_ebay'], self.row(row_id)['listed_amazon']), (0, 0))

    def test_suffixed_unit_needs_its_own_listing(self):
        self.bol(1, '719978859014-3', good=0, bad=1, status='bad')
        self.bol(2, '719978859015-2', good=0, bad=1, status='bad')
        self.sql('ebayStore.db', "INSERT INTO INVENTORY (ItemID, Quantity, List_State, UPC) VALUES ('1', '1', 'Active', '719978859014')")
        self.sql('ebayStore.db', "INSERT INTO INVENTORY (ItemID, Quantity, List_State, UPC) VALUES ('2', '1', 'Active', '719978859015-2')")

        self.assertEqual(self.scan()['added']['ebay'], 1)
        self.assertEqual(self.row(1)['listed_ebay'], 0)
        self.assertEqual(self.row(2)['listed_ebay'], 1)

    def test_missing_store_table_is_reported_not_guessed(self):
        os.remove('amazonStore.db')
        self.bol(1, '555555555555')
        payload = self.scan()
        self.assertFalse(payload['checked']['amazon'])
        self.assertEqual(self.row(1)['listed_amazon'], 0)


if __name__ == '__main__':
    unittest.main()
