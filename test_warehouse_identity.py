"""Isolated regression tests: no Flask startup and no real inventory databases."""
import ast
from test_feature_support import isolated_functions
import contextlib
from pathlib import Path
import sqlite3
import tempfile
import unittest

class WarehouseIdentityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        @contextlib.contextmanager
        def connection(name):
            conn = sqlite3.connect(self.root / name)
            conn.row_factory = sqlite3.Row
            try:
                yield conn
                conn.commit()
            finally:
                conn.close()
        self.connection = connection
        self.ns = dict(sqlite3=sqlite3, db_connection=connection, BASE_DIR=self.root)
        names = {'_normalize_upc', '_normalize_scanned_upc', '_strip_leading_zeros_numeric',
                 '_marketplace_upc_lookup_variants', '_ensure_custom_item_registry', '_add_item_screening_lookup'}
        isolated_functions(names, self.ns)
        schemas = {
            'rawbol.db': 'CREATE TABLE raw_bol_items (upc TEXT, item_description TEXT, image_url TEXT)',
            'bol.db': 'CREATE TABLE bol_items (upc TEXT, item_description TEXT, image_url TEXT)',
            'searchRack.db': 'CREATE TABLE SEARCHRACK (ID INTEGER PRIMARY KEY, BARCODE TEXT, TITLE TEXT, IMAGE TEXT, CUSTOM_TITLE INTEGER)',
            'ebayStore.db': 'CREATE TABLE INVENTORY (ID INTEGER PRIMARY KEY, UPC TEXT, Title TEXT, Image TEXT)',
            'amazonStore.db': 'CREATE TABLE ITEMS (UPC TEXT, TITLE TEXT, IMAGE TEXT, LAST_UPDATED TEXT)',
        }
        for db, sql in schemas.items():
            with connection(db) as conn:
                conn.execute(sql)
        with connection('bol.db') as conn:
            self.ns['_ensure_custom_item_registry'](conn.cursor())
        self.lookup = self.ns['_add_item_screening_lookup']

    def tearDown(self):
        self.temp.cleanup()

    def sql(self, db, sql, args=()):
        with self.connection(db) as conn:
            conn.execute(sql, args)

    def custom(self):
        self.sql('bol.db', 'INSERT INTO custom_item_registry (upc,item_description,image_url) VALUES (?,?,?)', ('123', 'My blue shirt', '/custom.jpg'))

    def test_custom_survives_inventory_deletion(self):
        self.custom()
        self.sql('searchRack.db', 'DELETE FROM SEARCHRACK')
        self.sql('bol.db', 'DELETE FROM bol_items')
        result = self.lookup('000000000123')
        self.assertEqual(result['title'], 'My blue shirt')
        self.assertEqual(result['image_url'], '/custom.jpg')
        self.assertFalse(result['needs_manual_title'])

    def test_new_store_then_macy_supersede_but_keep_custom(self):
        self.custom()
        self.sql('ebayStore.db', "INSERT INTO INVENTORY VALUES (1,'123','Store shirt','/store.jpg')")
        self.assertEqual(self.lookup('123')['title_source'], 'ebay')
        self.sql('rawbol.db', "INSERT INTO raw_bol_items VALUES ('123','Macy shirt','/macy.jpg')")
        result = self.lookup('123')
        self.assertEqual((result['title'], result['image_source']), ('Macy shirt', 'rawbol'))
        with self.connection('bol.db') as conn:
            self.assertEqual(conn.execute('SELECT item_description FROM custom_item_registry').fetchone()[0], 'My blue shirt')

    def test_partial_sources_fill_independent_fields(self):
        self.custom()
        self.sql('rawbol.db', "INSERT INTO raw_bol_items VALUES ('123','Macy shirt','')")
        self.sql('ebayStore.db', "INSERT INTO INVENTORY VALUES (1,'123','','/store.jpg')")
        result = self.lookup('123')
        self.assertEqual((result['title_source'], result['image_source']), ('rawbol','ebay'))

    def test_legacy_amazon_without_modern_table(self):
        self.sql('amazonStore.db', 'DROP TABLE ITEMS')
        self.sql('amazonStore.db', 'CREATE TABLE INVENTORY (ID INTEGER, UPC TEXT, Title TEXT, Image TEXT)')
        self.sql('amazonStore.db', "INSERT INTO INVENTORY VALUES (1,'123','Legacy shirt','/amazon.jpg')")
        self.assertEqual(self.lookup('123')['title'], 'Legacy shirt')

    def test_partial_amazon_uses_legacy_image(self):
        self.sql('amazonStore.db', "INSERT INTO ITEMS VALUES ('123','Modern shirt','','2026')")
        self.sql('amazonStore.db', 'CREATE TABLE INVENTORY (ID INTEGER, UPC TEXT, Title TEXT, Image TEXT)')
        self.sql('amazonStore.db', "INSERT INTO INVENTORY VALUES (1,'123','Old shirt','/amazon.jpg')")
        result = self.lookup('123')
        self.assertEqual((result['title'], result['image_url']), ('Modern shirt','/amazon.jpg'))

    def test_suffix_prefers_exact_macy_and_inherits_base_image(self):
        self.sql('rawbol.db', "INSERT INTO raw_bol_items VALUES ('123','Base shirt','/base.jpg')")
        self.sql('rawbol.db', "INSERT INTO raw_bol_items VALUES ('123-2','Exact shirt','')")
        result = self.lookup('123-2')
        self.assertEqual((result['title'], result['image_url']), ('Exact shirt','/base.jpg'))

    def test_latest_empty_row_does_not_hide_older_data(self):
        self.sql('rawbol.db', "INSERT INTO raw_bol_items VALUES ('123','Known shirt','/known.jpg')")
        self.sql('rawbol.db', "INSERT INTO raw_bol_items VALUES ('123','','')")
        self.assertEqual(self.lookup('123')['title'], 'Known shirt')

    def test_backfill_existing_non_generated_custom_barcode(self):
        self.sql('bol.db', "DELETE FROM custom_item_registry_meta WHERE key='warehouse_names_v2'")
        self.sql('searchRack.db', "INSERT INTO SEARCHRACK VALUES (1,'123','Old custom name','',1)")
        self.assertEqual(self.lookup('123')['title_source'], 'custom_registry')
        self.sql('searchRack.db', 'DELETE FROM SEARCHRACK')
        self.assertEqual(self.lookup('123')['title'], 'Old custom name')

    def test_inventory_add_uses_resolved_title_and_clears_custom_flag(self):
        import datetime
        self.sql('searchRack.db', 'DROP TABLE SEARCHRACK')
        self.sql('ebayStore.db', 'ALTER TABLE INVENTORY ADD COLUMN ItemID TEXT')
        self.sql('ebayStore.db', 'ALTER TABLE INVENTORY ADD COLUMN Quantity INTEGER')
        tree = ast.parse(Path('DBmanager.py').read_text(encoding='utf-8'))
        ns = dict(sqlite3=sqlite3, datetime=datetime, connect_db=self.connection,
                  _marketplace_upc_variants=self.ns['_marketplace_upc_lookup_variants'],
                  _log_rack_history=lambda *args: None)
        node = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'addToSearchRack')
        exec(compile(ast.Module(body=[node], type_ignores=[]), 'DBmanager.py', 'exec'), ns)
        add = ns['addToSearchRack']
        add('A1', '123', TITLE_OVERRIDE='Old custom', RESOLVED_METADATA={'title': 'Old custom', 'title_source': 'custom_registry'})
        add('A1', '123', TITLE_OVERRIDE='Stale browser title', RESOLVED_METADATA={'title': 'Macy shirt', 'title_source': 'rawbol', 'image_url': '/macy.jpg'})
        with self.connection('searchRack.db') as conn:
            row = conn.execute('SELECT TITLE, IMAGE, CUSTOM_TITLE, QUANTITY FROM SEARCHRACK').fetchone()
            self.assertEqual(tuple(row), ('Macy shirt', '/macy.jpg', 0, 2))

    def test_identity_endpoint_persists_title_without_photo_and_rejects_scan(self):
        from types import SimpleNamespace
        payload = {'upc': '000000000123', 'title': 'Blue cotton shirt medium'}
        ns = dict(self.ns, request=SimpleNamespace(get_json=lambda **kwargs: payload),
                  jsonify=lambda value: value, cache=SimpleNamespace(delete_memoized=lambda *args: None),
                  api_bol_lookup=None, _safe_error=lambda *args: str(args[0]))
        isolated_functions({'save_custom_item_identity'}, ns)
        self.assertTrue(ns['save_custom_item_identity']()['success'])
        self.assertEqual(self.lookup('123')['title'], payload['title'])
        payload['title'] = '123456789012'
        self.assertEqual(ns['save_custom_item_identity']()[1], 400)
        self.assertEqual(self.lookup('123')['title'], 'Blue cotton shirt medium')

    def test_explicit_name_edit_overrides_catalog_and_preserves_image(self):
        from types import SimpleNamespace
        self.sql('rawbol.db', "INSERT INTO raw_bol_items VALUES ('123','DIV 22/34 CRC REJECTS','/catalog.jpg')")
        payload = {'upc': '123', 'title': 'Ping Pong Paddle'}
        ns = dict(self.ns, request=SimpleNamespace(get_json=lambda **kwargs: payload),
                  jsonify=lambda value: value, cache=SimpleNamespace(delete_memoized=lambda *args: None),
                  api_bol_lookup=None, _safe_error=lambda *args: str(args[0]))
        isolated_functions({'save_custom_item_identity'}, ns)
        self.assertTrue(ns['save_custom_item_identity']()['success'])
        self.sql('rawbol.db', "UPDATE raw_bol_items SET item_description='Later import'")
        self.assertEqual(self.lookup('123')['title'], 'Ping Pong Paddle')
        self.assertEqual(self.lookup('123')['image_url'], '/catalog.jpg')
        self.assertTrue(ns['save_custom_item_identity']()['success'])
        self.assertEqual(self.lookup('123')['title_source'], 'custom_registry')

    def test_uploaded_image_wins_without_locking_prefilled_title(self):
        import os
        from types import SimpleNamespace
        from unittest.mock import mock_open
        self.sql('rawbol.db', "INSERT INTO raw_bol_items VALUES ('123','Catalog name','nan')")
        for column in ('lot_number', 'bol_number', 'import_date'):
            self.sql('bol.db', f'ALTER TABLE bol_items ADD COLUMN {column} TEXT')
        ns = dict(self.ns, request=SimpleNamespace(get_json=lambda: {'upc': '123', 'item_description': 'Catalog name', 'image_data': 'aW1hZ2U='}),
                  jsonify=lambda value: value, cache=SimpleNamespace(delete=lambda *args: None, delete_memoized=lambda *args: None),
                  api_bol_lookup=None, _safe_error=lambda *args: str(args[0]), open=mock_open(),
                  os=SimpleNamespace(path=os.path, makedirs=lambda *args, **kwargs: None))
        ns['sqlite3'] = SimpleNamespace(connect=lambda name: sqlite3.connect(self.root / name))
        isolated_functions({'save_temp_item'}, ns)
        self.assertTrue(ns['save_temp_item']()['success'])
        self.sql('rawbol.db', "UPDATE raw_bol_items SET item_description='Updated catalog name', image_url='/new.jpg'")
        result = self.lookup('123')
        self.assertEqual(result['title'], 'Updated catalog name')
        self.assertEqual((result['image_url'], result['image_source']), ('/static/custom_items/123.jpg', 'custom_registry'))

    def test_unknown_requires_name(self):
        result = self.lookup('999')
        self.assertTrue(result['needs_manual_title'])
        self.assertTrue(result['missing_image'])

if __name__ == '__main__':
    unittest.main()
