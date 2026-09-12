from test_feature_support import isolated_functions
from contextlib import contextmanager
from pathlib import Path
import re
import sqlite3
import tempfile
from types import SimpleNamespace
import unittest

from finder_search import is_barcode_query, name_scorer


class FinderTests(unittest.TestCase):
    def test_sizes_and_dimensions(self):
        for query in ('8 5', '8x5', '8 × 5'):
            self.assertGreater(name_scorer(query)('Frame 8 x 5'), 0)
        for title in ('Frame 18 x 5', 'Frame 8.5 x 5'):
            self.assertEqual(name_scorer('8')(title), 0)
        self.assertGreater(name_scorer('frame 8')('8-inch picture frames'), 0)

    def test_broader_ranking_and_literals(self):
        score = name_scorer('blue frame', True)
        self.assertGreater(score('Blue frame'), score('Wooden frame'))
        self.assertEqual(name_scorer('blue frame')('Wooden frame'), 0)
        self.assertEqual(name_scorer('%_')('anything'), 0)
        self.assertGreater(name_scorer('cafe')('Café'), 0)

    def test_barcode_detection(self):
        for query in ('8', '123456', 'frame 123456789012', '18x24inch'):
            self.assertFalse(is_barcode_query(query))
        for query in ('1234567', '012345678901', '012345678901-2'):
            self.assertTrue(is_barcode_query(query))

    def test_real_route_sql_with_isolated_databases(self):
        # Load only the route and its normalization helpers to avoid application
        # startup/background services and any access to live inventory.
        names = {'api_finder', '_finder_barcode_search_variants', '_normalize_upc',
                 '_normalize_upc_preserve_suffix_for_match', '_strip_leading_zeros_numeric'}
        with tempfile.TemporaryDirectory() as folder:
            @contextmanager
            def connect_db(name):
                conn = sqlite3.connect(str(Path(folder) / name))
                try:
                    yield conn
                    conn.commit()
                finally:
                    conn.close()
            with connect_db('searchRack.db') as conn:
                conn.execute('CREATE TABLE SEARCHRACK (ID INTEGER, TITLE TEXT, BARCODE TEXT, ITEM_POSITION TEXT, IMAGES TEXT, PICTUREPOSITION TEXT, QUANTITY INTEGER, IMAGE TEXT, ITEMID TEXT, WAREHOUSE_NOTE TEXT, CUSTOM_TITLE INTEGER)')
                conn.executemany('INSERT INTO SEARCHRACK VALUES (?, ?, ?, "A1", "", "", 1, "", "", "", ?)', [(1, 'Frame 8 x 5', '111111111111', 1), (2, 'Blue vase', '888888888888', 0), (3, 'Frame 18 x 5', '222222222222', 0)])
            with connect_db('rawbol.db') as conn:
                conn.execute('CREATE TABLE raw_bol_items (id INTEGER, upc TEXT, item_description TEXT, image_url TEXT, lot_number TEXT, quantity INTEGER, import_date TEXT)')
                conn.executemany('INSERT INTO raw_bol_items VALUES (?, ?, ?, "", "LOT8", 8, "2026-08-08")', [(1, '111111111111', 'Frame 8 x 5'), (2, '888888888888', 'Blue vase')])
            with connect_db('rackhistory.db') as conn:
                conn.execute('CREATE TABLE removed_items (id INTEGER, title TEXT, barcode TEXT, quantity_removed INTEGER, removed_at TEXT, removal_type TEXT, item_position TEXT, undone_at TEXT)')
                conn.executemany('INSERT INTO removed_items VALUES (?, ?, ?, 1, ?, "manual", "A1", NULL)', [
                    (1, 'Frame 8 x 5', '0111111111111', '2026-08-08'),
                    (2, 'Blue vase', '888888888888', '2026-08-09')])
            payload = {}
            scope = dict(re=re, sqlite3=sqlite3, connect_db=connect_db,
                         request=SimpleNamespace(get_json=lambda: payload), jsonify=lambda value: value,
                         _format_upc_display=str, _load_suffixed_item_prep_summary_lookup=lambda rows: {},
                         _empty_suffixed_item_prep_summary=lambda key: {})
            isolated_functions(names, scope)
            def search(**data):
                payload.clear()
                payload.update(data)
                return scope['api_finder']()
            for query in ('8', '5 frame 8', '8x5'):
                result = search(q=query)
                self.assertEqual([r['id'] for r in result['searchrack']], [1])
                self.assertEqual([r['upc'] for r in result['rawbol']], ['111111111111'])
            self.assertEqual([r['id'] for r in search(q='888888888888')['searchrack']], [2])
            self.assertEqual(search(q='8888888')['searchrack'], [])
            # A scanned code can stand for the whole -suffix family, in every panel.
            with connect_db('searchRack.db') as conn:
                conn.execute('INSERT INTO SEARCHRACK VALUES (4, "Frame 8 x 5 unit 2", "111111111111-2", "B2", "", "", 1, "", "", "", 0)')
            with connect_db('rawbol.db') as conn:
                conn.execute('INSERT INTO raw_bol_items VALUES (3, "111111111111-2", "Frame 8 x 5", "", "LOT9", 1, "2026-08-09")')
            self.assertEqual([r['id'] for r in search(q='111111111111')['searchrack']], [1])
            self.assertEqual([r['id'] for r in search(q='111111111111', family=True)['searchrack']], [1, 4])
            self.assertEqual([r['id'] for r in search(q='111111111111-2', family=True)['searchrack']], [1, 4])
            self.assertEqual(sorted(r['upc'] for r in search(q='111111111111', family=True)['rawbol']),
                             ['111111111111', '111111111111-2'])
            self.assertEqual([r['id'] for r in search(q='111111111111', family=True, include_history=True)['rackhistory']], [1])
            # Family mode never widens a name search into unrelated products.
            self.assertEqual([r['id'] for r in search(q='frame 8', family=True)['searchrack']], [1, 4])
            self.assertEqual(search(q='blue vase', family=True)['searchrack'][0]['id'], 2)
            with connect_db('searchRack.db') as conn:
                conn.execute('DELETE FROM SEARCHRACK WHERE ID = 4')
            with connect_db('rawbol.db') as conn:
                conn.execute('DELETE FROM raw_bol_items WHERE id = 3')
            result = search(q='', custom_only=True)
            self.assertEqual([r['id'] for r in result['searchrack']], [1])
            self.assertEqual(result['rawbol'], [])
            self.assertEqual(len(search(q='blue frame', match_mode='any')['searchrack']), 3)
            self.assertEqual(search(q='frame')['rackhistory'], [])
            self.assertEqual([r['id'] for r in search(q='8 frame', include_history=True)['rackhistory']], [1])
            self.assertEqual([r['id'] for r in search(q='111111111111', include_history=True)['rackhistory']], [1])
            self.assertEqual(search(q='1111111', include_history=True)['rackhistory'], [])
            self.assertEqual([r['id'] for r in search(q='blue frame', include_history=True, match_mode='any')['rackhistory']], [2, 1])
            self.assertEqual([r['id'] for r in search(q='blue', custom_only=True, include_history=True)['rackhistory']], [2])
            from finder_aliases import update_alias, forget_alias
            with connect_db('sold.db') as conn:
                undo_token = update_alias(conn, 'order-1', '111111111111', 'Gallery Essentials Natural 8 x 5')
            result = search(q='gallery essentials', custom_only=True)
            self.assertEqual([r['id'] for r in result['searchrack']], [1])
            self.assertTrue(result['searchrack'][0]['aliases'][0]['matched'])
            self.assertEqual(result['searchrack'][0]['title'], 'Frame 8 x 5')
            # Names do not leak into exact barcode searches or unrelated BOL rows.
            self.assertEqual(search(q='gallery')['rawbol'], [])
            self.assertEqual(search(q='1111111')['searchrack'], [])
            with connect_db('sold.db') as conn:
                update_alias(conn, 'order-1', undo_token=undo_token)
            self.assertEqual(search(q='gallery')['searchrack'], [])
            with connect_db('listing_alerts.db') as conn:
                update_alias(conn, 'amazon:sku1', '111111111111', 'Gallery Essentials')
            result = search(q='gallery')
            alias = result['searchrack'][0]['aliases'][0]
            self.assertEqual(alias['source'], 'listing')
            with connect_db('listing_alerts.db') as conn:
                forget_alias(conn, alias['id'])
            self.assertEqual(search(q='gallery')['searchrack'], [])



if __name__ == '__main__':
    unittest.main()
