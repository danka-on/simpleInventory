"""The No-Name Items screen must show exactly the stock nobody can identify."""
from contextlib import ExitStack, closing
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

import DBmanager
from sweetshelves.bootstrap import app
from sweetshelves import config


class WarehouseNamelessTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.stack = ExitStack()
        self.stack.enter_context(patch.object(config, 'BASE_DIR', self.root))
        self.stack.enter_context(patch.object(DBmanager, 'BASE_DIR', str(self.root)))
        self.client = app.test_client()
        self.sql('searchRack.db', '''
            CREATE TABLE SEARCHRACK (ID INTEGER PRIMARY KEY, TITLE TEXT, BARCODE TEXT, ITEM_POSITION TEXT,
                IMAGE TEXT, ITEMID TEXT, QUANTITY INTEGER, CREATED_AT TEXT, CUSTOM_TITLE INTEGER DEFAULT 0,
                WAREHOUSE_NOTE TEXT DEFAULT '')
        ''')
        self.sql('rawbol.db', 'CREATE TABLE raw_bol_items (upc TEXT, item_description TEXT, image_url TEXT)')
        self.sql('bol.db', 'CREATE TABLE bol_items (upc TEXT, item_description TEXT, image_url TEXT)')
        self.sql('ebayStore.db', 'CREATE TABLE INVENTORY (ID INTEGER PRIMARY KEY, UPC TEXT, Title TEXT, Image TEXT)')
        self.sql('amazonStore.db', 'CREATE TABLE ITEMS (UPC TEXT, TITLE TEXT, IMAGE TEXT, LAST_UPDATED TEXT)')

    def tearDown(self):
        self.stack.close()
        self.temp.cleanup()

    def sql(self, db, statement, args=()):
        with closing(sqlite3.connect(self.root / db)) as conn:
            conn.execute(statement, args)
            conn.commit()

    def rack(self, row_id, title, barcode, quantity=1, location='', created='2026-01-05T09:00:00', note=''):
        self.sql('searchRack.db', '''
            INSERT INTO SEARCHRACK (ID, TITLE, BARCODE, ITEM_POSITION, IMAGE, ITEMID, QUANTITY,
                CREATED_AT, CUSTOM_TITLE, WAREHOUSE_NOTE)
            VALUES (?, ?, ?, ?, '', '', ?, ?, 0, ?)
        ''', (row_id, title, barcode, location, quantity, created, note))

    def scan(self):
        response = self.client.get('/api/warehouse/nameless-items')
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        payload = response.get_json()
        self.assertTrue(payload['success'], payload)
        return payload

    def barcodes(self, payload, bucket):
        return sorted(group['barcode'] for group in payload[bucket])

    def stored_rows(self):
        with closing(sqlite3.connect(self.root / 'searchRack.db')) as conn:
            return conn.execute('SELECT ID, TITLE, CUSTOM_TITLE FROM SEARCHRACK ORDER BY ID').fetchall()

    def registry(self):
        with closing(sqlite3.connect(self.root / 'bol.db')) as conn:
            if not conn.execute("SELECT 1 FROM sqlite_master WHERE name = 'custom_item_registry'").fetchone():
                return []
            return conn.execute(
                'SELECT upc, item_description, title_override FROM custom_item_registry ORDER BY upc').fetchall()

    def test_only_barcode_rows_no_source_can_name_are_barcode_only(self):
        self.rack(1, '', '111111111111')
        self.rack(2, '', '222222222222')
        self.sql('rawbol.db', "INSERT INTO raw_bol_items VALUES ('222222222222', 'Wicker basket', '/b.jpg')")
        payload = self.scan()
        self.assertEqual(self.barcodes(payload, 'barcode_only'), ['111111111111'])
        self.assertEqual(self.barcodes(payload, 'nameable'), ['222222222222'])
        self.assertEqual(payload['nameable'][0]['suggested_title'], 'Wicker basket')
        self.assertEqual(payload['nameable'][0]['suggested_source'], 'rawbol')

    def test_named_stock_never_reaches_the_screen(self):
        self.rack(1, 'Blue ceramic table lamp', '111111111111')
        payload = self.scan()
        self.assertEqual(payload['barcode_only'], [])
        self.assertEqual(payload['nameable'], [])
        self.assertEqual(payload['totals']['scanned_rows'], 0)

    def test_a_barcode_or_placeholder_in_the_title_column_still_counts_as_no_name(self):
        # These read as a name in the warehouse list but identify nothing.
        self.rack(1, '111111111111', '111111111111')
        self.rack(2, 'Unknown', '222222222222')
        self.rack(3, 'No barcode item', '333333333333')
        self.rack(4, '   ', '444444444444')
        payload = self.scan()
        self.assertEqual(
            self.barcodes(payload, 'barcode_only'),
            ['111111111111', '222222222222', '333333333333', '444444444444'])

    def test_a_placeholder_override_does_not_hide_the_catalog_name(self):
        # 'No barcode item' reached the registry as an override, so the add-item
        # gate answers with the placeholder and buries the real BOL description.
        self.sql('bol.db', """
            CREATE TABLE custom_item_registry (upc TEXT PRIMARY KEY COLLATE NOCASE,
                item_description TEXT, image_url TEXT, title_override INTEGER DEFAULT 0,
                image_override INTEGER DEFAULT 0, reserved_at TEXT, updated_at TEXT)
        """)
        self.sql('bol.db', "INSERT INTO custom_item_registry (upc, item_description, title_override) "
                           "VALUES ('111111111111', 'No barcode item', 1)")
        self.sql('rawbol.db', "INSERT INTO raw_bol_items VALUES ('111111111111', 'Kosta Boda swizzle stick', '/k.jpg')")
        self.rack(1, 'No barcode item', '111111111111')
        payload = self.scan()
        self.assertEqual(payload['barcode_only'], [])
        self.assertEqual(self.barcodes(payload, 'nameable'), ['111111111111'])
        self.assertEqual(payload['nameable'][0]['suggested_title'], 'Kosta Boda swizzle stick')

    def test_a_named_sibling_unit_can_name_the_rest(self):
        self.rack(1, 'Wicker laundry basket', '111111111111', location='hr1s1')
        self.rack(2, '', '111111111111', location='or2s3')
        payload = self.scan()
        self.assertEqual(self.barcodes(payload, 'nameable'), ['111111111111'])
        group = payload['nameable'][0]
        self.assertEqual(group['suggested_title'], 'Wicker laundry basket')
        self.assertEqual(group['suggested_source'], 'searchrack')
        self.assertEqual([row['id'] for row in group['rows']], [2])

    def test_archived_rows_are_not_warehouse_stock(self):
        self.rack(1, '', '111111111111', quantity=0)
        self.rack(2, '', '222222222222', quantity=3)
        payload = self.scan()
        self.assertEqual(self.barcodes(payload, 'barcode_only'), ['222222222222'])
        self.assertEqual(payload['totals']['barcode_only']['units'], 3)

    def test_units_of_one_item_group_under_a_single_barcode(self):
        self.rack(1, '', '111111111111', quantity=2, location='hr1s1', created='2026-01-05T09:00:00')
        self.rack(2, '', '111111111111', quantity=1, location='or2s3', created='2026-02-05T09:00:00')
        payload = self.scan()
        self.assertEqual(len(payload['barcode_only']), 1)
        group = payload['barcode_only'][0]
        self.assertEqual(group['units'], 3)
        self.assertEqual(len(group['rows']), 2)
        self.assertEqual(group['locations'], ['hr1s1', 'or2s3'])

    def test_naming_writes_the_rows_and_the_barcode_for_later_scans(self):
        self.rack(1, '', '111111111111', quantity=2, location='hr1s1')
        self.rack(2, '', '111111111111', quantity=1)
        self.rack(3, '', '999999999999')
        response = self.client.post('/api/warehouse/nameless-items/name',
                                    json={'barcode': '111111111111', 'title': 'Wicker laundry basket'})
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        self.assertEqual(sorted(response.get_json()['named_ids']), [1, 2])
        self.assertEqual(self.stored_rows(), [
            (1, 'Wicker laundry basket', 1),
            (2, 'Wicker laundry basket', 1),
            (3, '', 0),
        ])
        self.assertEqual(self.registry(), [('111111111111', 'Wicker laundry basket', 1)])
        self.assertEqual(self.scan()['barcode_only'][0]['barcode'], '999999999999')

    def test_naming_one_unit_leaves_its_siblings_alone(self):
        self.rack(1, '', '111111111111')
        self.rack(2, '', '111111111111')
        response = self.client.post('/api/warehouse/nameless-items/name',
                                    json={'barcode': '111111111111', 'title': 'Wicker basket', 'ids': [2]})
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        self.assertEqual(response.get_json()['named_ids'], [2])
        self.assertEqual(self.stored_rows(), [(1, '', 0), (2, 'Wicker basket', 1)])

    def test_a_suffixed_unit_is_named_on_its_base_barcode(self):
        # Suffixes identify individual units; the product name belongs to the UPC.
        self.rack(1, '', '111111111111-2', location='hr1s1')
        payload = self.scan()
        self.assertEqual(self.barcodes(payload, 'barcode_only'), ['111111111111-2'])
        response = self.client.post('/api/warehouse/nameless-items/name',
                                    json={'barcode': '111111111111-2', 'title': 'Wicker basket'})
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        self.assertEqual(self.registry(), [('111111111111', 'Wicker basket', 1)])
        self.assertEqual(self.stored_rows(), [(1, 'Wicker basket', 1)])

    def test_a_placeholder_is_not_a_name(self):
        self.rack(1, '', '111111111111')
        for title in ('Unknown', 'no barcode item', 'item', '111111111111', 'n/a'):
            response = self.client.post('/api/warehouse/nameless-items/name',
                                        json={'barcode': '111111111111', 'title': title})
            self.assertEqual(response.status_code, 400, f'{title!r} should be refused')
            self.assertFalse(response.get_json()['success'])
        self.assertEqual(self.stored_rows(), [(1, '', 0)])
        self.assertEqual(self.registry(), [])

    def test_naming_an_already_named_barcode_reports_nothing_left_to_do(self):
        self.rack(1, 'Wicker basket', '111111111111')
        response = self.client.post('/api/warehouse/nameless-items/name',
                                    json={'barcode': '111111111111', 'title': 'Something else'})
        self.assertEqual(response.status_code, 404)
        self.assertEqual(self.stored_rows(), [(1, 'Wicker basket', 0)])

    def test_the_page_renders(self):
        response = self.client.get('/no-name-items')
        self.assertEqual(response.status_code, 200)
        self.assertIn('No-Name Items', response.get_data(as_text=True))


if __name__ == '__main__':
    unittest.main()
