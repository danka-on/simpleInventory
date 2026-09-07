from contextlib import contextmanager
from pathlib import Path
import sqlite3
import tempfile
import unittest

from finder_records import collect_records


class FinderRecordsTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.root = Path(self.folder.name)

    @contextmanager
    def connect(self, name):
        conn = sqlite3.connect(self.root / name)
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def query(self, upc='012345678901', **kwargs):
        return collect_records(self.root, self.connect, upc, **kwargs)

    def test_sales_prep_suffixes_and_pagination(self):
        with self.connect('sold.db') as conn:
            conn.execute('CREATE TABLE orders (id INTEGER PRIMARY KEY, barcode TEXT, paid_time TEXT, price REAL)')
            conn.executemany('INSERT INTO orders VALUES (?, ?, ?, 12.5)', [(i, '12345678901.0', f'2026-08-{i:02}') for i in range(1, 29)])
            conn.execute('INSERT INTO orders VALUES (29, "012345678901-2", "2026-09-01", 20)')
            conn.execute('INSERT INTO orders VALUES (30, "999999999999", "2026-09-02", 30)')
        with self.connect('preplog.db') as conn:
            conn.execute('CREATE TABLE prep_log (id INTEGER PRIMARY KEY, upc TEXT, base_upc TEXT, note TEXT, undone INTEGER)')
            conn.execute('INSERT INTO prep_log VALUES (1, "012345678901-2", "012345678901", "Damaged corner", 1)')
        result = self.query()
        self.assertEqual(len(result['sources']), 1)
        orders = result['sources'][0]
        self.assertEqual(orders['total'], 28)
        self.assertEqual(orders['next_offset'], 25)
        self.assertEqual(orders['records'][0]['id'], 28)
        second = self.query(source=orders['id'], offset=25)['sources'][0]
        self.assertEqual([row['id'] for row in second['records']], [3, 2, 1])
        self.assertIsNone(second['next_offset'])
        exact = self.query('12345678901-2')['sources']
        self.assertEqual(exact[0]['total'], 1)
        self.assertEqual(exact[1]['records'][0]['undone'], 1)
        self.assertEqual(self.query(family=True)['sources'][0]['total'], 29)
        self.assertFalse(self.query('1234567')['sources'])

    def test_linked_returns_notes_and_archived_snapshots(self):
        with self.connect('sold.db') as conn:
            conn.executescript('''
                CREATE TABLE orders (id INTEGER PRIMARY KEY, barcode TEXT);
                INSERT INTO orders VALUES (1, '012345678901');
                CREATE TABLE ready_to_ship_notes (order_row_id INTEGER, note TEXT);
                INSERT INTO ready_to_ship_notes VALUES (1, 'Fragile');
                INSERT INTO ready_to_ship_notes VALUES (2, 'Other item');
                CREATE TABLE returns (id INTEGER PRIMARY KEY, barcode TEXT);
                INSERT INTO returns VALUES (4, '012345678901');
                CREATE TABLE return_lifecycle_events (id INTEGER PRIMARY KEY, return_id INTEGER, notes TEXT);
                INSERT INTO return_lifecycle_events VALUES (5, 4, 'Restocked');
            ''')
        with self.connect('deleted.db') as conn:
            conn.execute('CREATE TABLE deleted_items (id INTEGER PRIMARY KEY, data_json TEXT)')
            conn.executemany('INSERT INTO deleted_items VALUES (?, ?)', [(1, '{"BARCODE":"012345678901","QUANTITY":0}'), (2, 'invalid JSON')])
        sources = {s['id']: s for s in self.query()['sources']}
        self.assertEqual(sources['sold.db:ready_to_ship_notes']['records'][0]['note'], 'Fragile')
        self.assertEqual(sources['sold.db:return_lifecycle_events']['records'][0]['notes'], 'Restocked')
        self.assertEqual(sources['deleted.db:deleted_items']['total'], 1)

    def test_marketplace_price_links_are_platform_specific(self):
        with self.connect('listinglog.db') as conn:
            conn.executescript('''CREATE TABLE listing_log (upc TEXT, platform TEXT, sku TEXT, listing_id TEXT);
                INSERT INTO listing_log VALUES ('012345678901', 'amazon', 'archived-sku', NULL);''')
        with self.connect('amazonStore.db') as conn:
            conn.executescript('''CREATE TABLE ITEMS (UPC TEXT, SKU TEXT);
                INSERT INTO ITEMS VALUES ('012345678901', 'sku-a');''')
        with self.connect('pricemaster.db') as conn:
            conn.executescript('''CREATE TABLE price_changes (id INTEGER PRIMARY KEY, platform TEXT, listing_key TEXT, new_price REAL);
                INSERT INTO price_changes VALUES (1, 'amazon', 'sku-a', 15);
                INSERT INTO price_changes VALUES (2, 'ebay', 'sku-a', 16);
                INSERT INTO price_changes VALUES (3, 'amazon', 'archived-sku', 17);''')
        source = next(s for s in self.query()['sources'] if s['id'] == 'pricemaster.db:price_changes')
        self.assertEqual([r['id'] for r in source['records']], [3, 1])
        self.assertEqual(self.query(source=source['id'])['sources'][0]['total'], 2)

    def test_missing_and_broken_databases_and_read_only(self):
        self.assertTrue(self.query()['unavailable'])
        self.assertEqual(list(self.root.iterdir()), [])
        (self.root / 'sold.db').write_bytes(b'broken database')
        self.assertTrue(self.query()['errors'])
        with self.connect('bol.db') as conn:
            conn.execute('CREATE TABLE notes (upc TEXT, note TEXT)')
            conn.execute('INSERT INTO notes VALUES (?, ?)', ("custom' OR 1=1 --", '<script>alert(1)</script>'))
        before = (self.root / 'bol.db').read_bytes()
        self.assertEqual(self.query("custom' OR 1=1 --")['sources'][0]['total'], 1)
        self.assertEqual((self.root / 'bol.db').read_bytes(), before)
        self.assertFalse(self.query('unrelated')['sources'])
        with self.assertRaises(ValueError):
            self.query(source='../secrets.db:notes')


if __name__ == '__main__':
    unittest.main()
