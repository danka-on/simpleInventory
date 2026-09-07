"""Exercise the production match routes with temporary databases only."""
from test_feature_support import isolated_functions
from contextlib import contextmanager
from pathlib import Path
import sqlite3
import tempfile
from types import SimpleNamespace
import unittest


class MatchLearningTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.names = {'match_barcode_to_sold', 'api_listing_helper_inventory_match',
                 '_ensure_order_finder_matches_table', 'api_finder_forget_alias'}

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.payload = {}
        self.connections = []
        def connect(path):
            conn = sqlite3.connect(self.folder / Path(path).name)
            self.connections.append(conn)
            return conn
        self.connect = connect
        @contextmanager
        def connect_db(path):
            conn = connect(path)
            try:
                yield conn
                conn.commit()
            finally:
                conn.close()
        self.scope = dict(
            sqlite3=SimpleNamespace(connect=connect, Row=sqlite3.Row), BASE_DIR=self.folder,
            connect_db=connect_db, request=SimpleNamespace(get_json=lambda: self.payload),
            jsonify=lambda data: data, _coerce_int=lambda value, default=0: int(value or default),
            _safe_error=lambda error, *args: str(error), cache=SimpleNamespace(delete=lambda key: None),
            _normalize_store_listing_identity=lambda store, key: (store, key),
            _ensure_listing_alerts_tables=lambda: None, _listing_helper_scan_cache_clear=lambda: None,
            _ready_to_ship_searchrack_match_by_id=lambda cur, row_id: (
                {'id': row_id, 'barcode': '123456789012', 'quantity': 1, 'location': 'A1'}
                if row_id == 1 else None),
            _ready_to_ship_resolve_searchrack_location_match=lambda *args, **kwargs: None,
            _ready_to_ship_match_display_location=lambda row: row['location'],
        )
        isolated_functions(self.names, self.scope)
        with connect_db('sold.db') as conn:
            conn.execute('CREATE TABLE orders (id INTEGER PRIMARY KEY, title TEXT, barcode TEXT, location TEXT)')
            conn.execute("INSERT INTO orders VALUES (1, 'Gallery Essentials', '999999999999', '')")
        with connect_db('listing_alerts.db') as conn:
            conn.execute('CREATE TABLE listing_inventory_matches (id INTEGER PRIMARY KEY, store TEXT, listing_key TEXT, listing_id TEXT, marketplace_barcode TEXT, listing_title TEXT, searchrack_id INTEGER, inventory_barcode TEXT, inventory_location TEXT, updated_at TEXT, UNIQUE(store, listing_key))')
        self.addCleanup(self.close_connections)

    def close_connections(self):
        for conn in self.connections:
            conn.close()

    def call(self, route, data, *args):
        self.payload = data
        return self.scope[route](*args)

    def aliases(self, database):
        with self.connect(database) as conn:
            return conn.execute('SELECT id, title FROM finder_aliases').fetchall()

    def test_order_confirm_uses_stored_title_and_undo(self):
        result = self.call('match_barcode_to_sold', dict(barcode='123456789012', searchrack_id=1,
                           finder_learn=True, title='Untrusted different title'), 1)
        self.assertTrue(result['success'])
        self.assertEqual(self.aliases('sold.db')[0][1], 'Gallery Essentials')
        undone = self.call('match_barcode_to_sold', dict(barcode=result['old_barcode'],
                           location=result['old_location'], finder_alias_undo=result['finder_alias_undo']), 1)
        self.assertTrue(undone['success'])
        self.assertEqual(self.aliases('sold.db'), [])
        with self.connect('sold.db') as conn:
            self.assertEqual(conn.execute('SELECT barcode FROM orders').fetchone()[0], '999999999999')

    def test_listing_confirm_undo_clear(self):
        result = self.call('api_listing_helper_inventory_match', dict(store='amazon', listing_key='SKU1',
                           searchrack_id=1, listing_title='Gallery Essentials', finder_learn=True))
        self.assertTrue(result['success'])
        self.assertEqual(self.aliases('listing_alerts.db')[0][1], 'Gallery Essentials')
        undone = self.call('api_listing_helper_inventory_match', dict(store='amazon', listing_key='SKU1',
                           clear=True, finder_alias_undo=result['finder_alias_undo']))
        self.assertTrue(undone['success'])
        self.assertEqual(self.aliases('listing_alerts.db'), [])

    def test_no_learning_without_finder_confirmation(self):
        self.call('match_barcode_to_sold', dict(barcode='123456789012', searchrack_id=1), 1)
        self.assertEqual(self.aliases('sold.db'), [])
        self.call('api_listing_helper_inventory_match', dict(store='amazon', listing_key='SKU1',
                  searchrack_id=1, listing_title='Gallery Essentials'))
        self.assertEqual(self.aliases('listing_alerts.db'), [])

    def test_forget_removes_both_sources_without_changing_matches(self):
        self.call('match_barcode_to_sold', dict(barcode='123456789012', searchrack_id=1, finder_learn=True), 1)
        self.call('api_listing_helper_inventory_match', dict(store='amazon', listing_key='SKU1',
                  searchrack_id=1, listing_title='Gallery Essentials', finder_learn=True))
        alias_id = self.aliases('sold.db')[0][0]
        self.assertTrue(self.call('api_finder_forget_alias', dict(source='order', id=alias_id))['success'])
        self.assertEqual(self.aliases('sold.db'), [])
        self.assertEqual(self.aliases('listing_alerts.db'), [])
        with self.connect('sold.db') as conn:
            self.assertEqual(conn.execute('SELECT searchrack_id FROM order_finder_matches').fetchone()[0], 1)
        with self.connect('listing_alerts.db') as conn:
            self.assertEqual(conn.execute('SELECT searchrack_id FROM listing_inventory_matches').fetchone()[0], 1)


if __name__ == '__main__':
    unittest.main()
