from contextlib import contextmanager
from pathlib import Path
import json
import sqlite3
import tempfile
import unittest

from finder_trail import collect_trail, display_upc, sort_key


class FinderTrailTests(unittest.TestCase):
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

    def trail(self, upc='028199270349', **kwargs):
        return collect_trail(self.root, self.connect, upc, **kwargs)

    def seed(self):
        with self.connect('searchRack.db') as conn:
            conn.executescript('''
                CREATE TABLE SEARCHRACK (ID INTEGER PRIMARY KEY, TITLE TEXT, BARCODE TEXT, ITEM_POSITION TEXT,
                    PICTUREPOSITION TEXT, QUANTITY INTEGER, IMAGE TEXT, CUSTOM_TITLE INTEGER, WAREHOUSE_NOTE TEXT, CREATED_AT TEXT);
                INSERT INTO SEARCHRACK VALUES (10, 'Gallery frame', '28199270349', 'hr2s3', '', 2, '/img/frame.jpg', 0, 'Top shelf', '2026-08-03');
                INSERT INTO SEARCHRACK VALUES (11, 'Gallery frame', '028199270349', '', '', 1, '', 0, '', '2026-08-04');
                INSERT INTO SEARCHRACK VALUES (12, 'Gallery frame custom', '028199270349-2', 'or1s1', '', 1, '', 1, '', '2026-08-05');
                INSERT INTO SEARCHRACK VALUES (13, 'Other', '999999999999', 'x1', '', 5, '', 0, '', '2026-08-05');
                CREATE TABLE zero_qty_pending_deletion (id INTEGER PRIMARY KEY, searchrack_id INTEGER, marked_at TEXT, delete_at TEXT, deletion_cancelled INTEGER);
                INSERT INTO zero_qty_pending_deletion VALUES (1, 11, '2026-09-01', '2026-09-08', 0);
                CREATE TABLE fba_prep_sessions (id INTEGER PRIMARY KEY, session_name TEXT, shipment_id TEXT);
                INSERT INTO fba_prep_sessions VALUES (5, 'shipment 4', 'FBA123');
                CREATE TABLE fba_prep_batches (id INTEGER PRIMARY KEY, batch_name TEXT, shipment_id TEXT, session_id INTEGER, status TEXT);
                INSERT INTO fba_prep_batches VALUES (7, 'batch a', '', 5, 'packing');
                CREATE TABLE fba_prep_items (id INTEGER PRIMARY KEY, batch_id INTEGER, barcode TEXT, requested_quantity INTEGER,
                    quantity_removed INTEGER, source_locations_json TEXT, box_number TEXT, created_at TEXT);
                INSERT INTO fba_prep_items VALUES (1, 7, '028199270349', 3, 3, '[{"location": "hr2s3", "qty": 3}]', 'BOX-02', '2026-08-20T10:00:00');
            ''')
        with self.connect('rackhistory.db') as conn:
            conn.executescript('''
                CREATE TABLE removed_items (id INTEGER PRIMARY KEY, order_id TEXT, barcode TEXT, title TEXT, quantity_removed INTEGER,
                    removed_at TEXT, searchrack_id INTEGER, old_quantity INTEGER, new_quantity INTEGER, removal_type TEXT,
                    item_position TEXT, undone_at TEXT, from_position TEXT, to_position TEXT, inventory_row_deleted INTEGER, event_status TEXT);
                INSERT INTO removed_items VALUES (1, '', '028199270349', 'Gallery frame', 1, '2026-08-03T13:58:25', 10, 0, 1, 'add_to_shelf', 'hr2s3', NULL, '', 'hr2s3', 0, 'applied');
                INSERT INTO removed_items VALUES (2, '', '028199270349', 'Gallery frame', 1, '2026-08-03T13:58:26', 10, 1, 2, 'add_to_shelf', 'hr2s3', NULL, '', 'hr2s3', 0, 'applied');
                INSERT INTO removed_items VALUES (3, '', '028199270349', 'Gallery frame', 1, '2026-08-10 09:00:00', 10, 2, 2, 'locationmoved', 'hr2s3', NULL, 'hr1s1', 'hr2s3', 0, 'applied');
                INSERT INTO removed_items VALUES (4, '111-3101896', '028199270349', 'Gallery frame', 2, '2026-09-12T10:06:31', 10, 2, 0, 'manual_handled', 'hr2s3', NULL, '', '', 0, 'applied');
                INSERT INTO removed_items VALUES (5, '', '028199270349', 'Gallery frame', 1, '2026-09-12T11:00:00', 10, 0, 1, 'add_to_shelf', 'hr2s3', '2026-09-12T11:05:00', '', 'hr2s3', 0, 'applied');
                INSERT INTO removed_items VALUES (6, '', '028199270349', 'Gallery frame', 1, '2026-09-12T12:00:00', 10, 1, 0, 'finder_removal', 'hr2s3', NULL, '', '', 0, 'pending');
                INSERT INTO removed_items VALUES (7, '', '028199270349', 'Gallery frame', 0, '2026-07-15T22:49:58', 11, 0, 0, 'legacy_zero_cleanup', 'h1', NULL, 'h1', '', 1, 'applied');
                INSERT INTO removed_items VALUES (8, '', '028199270349-2', 'Custom', 1, '2026-08-06T10:00:00', 12, 0, 1, 'add_to_shelf', 'or1s1', NULL, '', 'or1s1', 0, 'applied');
                INSERT INTO removed_items VALUES (9, '', '028199270349', 'Gallery frame', 4, '2025-11-19T13:25:50', 10, 0, 4, 'manual_edit', 'Or1s6', NULL, '', '', 0, 'applied');
            ''')
        with self.connect('sold.db') as conn:
            conn.executescript('''
                CREATE TABLE orders (id INTEGER PRIMARY KEY, order_id TEXT, title TEXT, quantity INTEGER, price REAL, checkout_status TEXT,
                    paid_time TEXT, shipped_time TEXT, image TEXT, location TEXT, barcode TEXT, rackupdated INTEGER, removal_cancelled INTEGER,
                    store TEXT, lot_number TEXT, source_upc TEXT, item_condition TEXT);
                INSERT INTO orders VALUES (1614, '111-3101896', 'Gallery frame', 2, 30, 'PAID', '2026-09-11T20:27:58Z', '', '', 'hr2s3', '028199270349', 1, 0, 'amazon', '16981955', '', 'New');
                INSERT INTO orders VALUES (1615, '112-9043165', 'Gallery frame', 1, 30, '', '2025-12-03T12:42:33Z', '', '', '', '028199270349', 0, 0, 'amazon', '', '', '');
                INSERT INTO orders VALUES (1616, '113-3858921', 'Gallery frame', 1, 30, '', '2025-11-24T11:34:45Z', '', '', '', '', 0, 1, 'ebay', '', '28199270349.0', '');
                CREATE TABLE returns (id INTEGER PRIMARY KEY, order_id TEXT, barcode TEXT, quantity INTEGER, refund_amount REAL, return_date TEXT,
                    created_at TEXT, store TEXT, return_reason TEXT, condition_received TEXT, restocked INTEGER, location TEXT, notes TEXT, relisted INTEGER, resold INTEGER);
                INSERT INTO returns VALUES (1, '112-9043165', '028199270349', 1, 30, '2026-03-10T15:39:00.024Z', '2026-04-29 18:31:00', 'amazon', 'Damaged', 'Used', 0, '', 'corner dent', 0, 0);
                CREATE TABLE order_finder_matches (id INTEGER PRIMARY KEY, order_row_id INTEGER, searchrack_id INTEGER, barcode TEXT, location TEXT, created_at TEXT, updated_at TEXT);
                INSERT INTO order_finder_matches VALUES (40, 1614, 10, '028199270349', 'hr2s3', '2026-09-12 13:56:24', '2026-09-12 13:56:24');
                CREATE TABLE finder_aliases (id TEXT PRIMARY KEY, source_key TEXT NOT NULL UNIQUE, barcode_key TEXT NOT NULL, title TEXT NOT NULL);
                INSERT INTO finder_aliases VALUES ('a1', 'order:1', '28199270349', 'Amazon gallery frame 8x10');
            ''')
        with self.connect('preplog.db') as conn:
            conn.executescript('''
                CREATE TABLE prep_log (id INTEGER PRIMARY KEY, created_at TEXT, upc TEXT, base_upc TEXT, status TEXT, quantity INTEGER, note TEXT, reason TEXT, source TEXT, undone INTEGER, undone_at TEXT);
                INSERT INTO prep_log VALUES (1, '2026-07-12T10:00:00', '028199270349', '028199270349', 'good', 2, 'Checked corners', '', 'item-prep', 0, NULL);
                INSERT INTO prep_log VALUES (2, '2026-08-06T10:00:00', '028199270349-2', '028199270349', 'bad', 1, '', 'Scratched', 'item-prep', 0, NULL);
            ''')
        with self.connect('rawbol.db') as conn:
            conn.executescript('''
                CREATE TABLE raw_bol_items (id INTEGER PRIMARY KEY, upc TEXT, item_description TEXT, avg_cost REAL, image_url TEXT, quantity INTEGER,
                    lot_number TEXT, bol_location TEXT, import_date TEXT, created_at TEXT, original_retail REAL);
                INSERT INTO raw_bol_items VALUES (1, '28199270349.0', 'GALLERY FRAME 8X10', 4.5, 'https://img/bol.jpg', 4, '16981955', 'P3', '2026-07-11', '2026-07-11T09:00:00', 19.99);
            ''')
        with self.connect('deleted.db') as conn:
            conn.executescript('''
                CREATE TABLE deleted_items (id INTEGER PRIMARY KEY, source_db TEXT, source_table TEXT, source_pk TEXT, source_id INTEGER, deleted_at TEXT, data_json TEXT);
            ''')
            conn.execute('INSERT INTO deleted_items VALUES (1, "searchRack", "SEARCHRACK", "ID", 9, "2026-06-01T17:06:57+00:00", ?)',
                         (json.dumps({'ID': 9, 'TITLE': 'Gallery frame', 'BARCODE': '028199270349', 'ITEM_POSITION': 'g1', 'QUANTITY': 1}),))
            conn.execute('INSERT INTO deleted_items VALUES (2, "sold", "orders", "id", 3, "2026-06-02T17:06:57+00:00", ?)',
                         (json.dumps({'id': 3, 'barcode': '555555555555'}),))
        with self.connect('listinglog.db') as conn:
            conn.executescript('''
                CREATE TABLE listing_log (id INTEGER PRIMARY KEY, created_at TEXT, upc TEXT, platform TEXT, action TEXT, sku TEXT, price REAL, quantity INTEGER, success INTEGER, error TEXT, url TEXT);
                INSERT INTO listing_log VALUES (1, '2026-07-20T09:22:09', '028199270349', 'ebay', 'listed', 'SKU-1', 24.99, 1, 1, '', 'https://ebay.example/1');
                INSERT INTO listing_log VALUES (2, '2026-07-21T09:22:09', '028199270349', 'amazon', 'listed', 'SKU-1', 24.99, 1, 0, 'Missing image', '');
            ''')

    def test_merged_timeline_ledger_and_clues(self):
        self.seed()
        result = self.trail()
        self.assertEqual(result['upc_display'], '028199270349')
        self.assertEqual(result['identity']['title'], 'Gallery frame')
        self.assertEqual(result['identity']['image'], '/img/frame.jpg')
        self.assertEqual(result['identity']['aliases'], ['Amazon gallery frame 8x10'])
        self.assertEqual(result['identity']['lots'], ['16981955'])
        self.assertFalse(result['identity']['custom'])
        self.assertEqual([row['searchrack_id'] for row in result['stock']], [10, 11])
        self.assertEqual(result['stock'][1]['pending_deletion'], '2026-09-08')
        kinds = [event['kind'] for event in result['events']]
        self.assertEqual(kinds[0], 'matched')
        self.assertEqual(result['events'][0]['order_id'], '111-3101896')
        self.assertIn('pulled', kinds)
        self.assertNotIn('028199270349-2', [event['upc'] for event in result['events']])
        sorted_keys = [event['sort'] for event in result['events']]
        self.assertEqual(sorted_keys, sorted(sorted_keys, reverse=True))
        pulled = next(event for event in result['events'] if event['kind'] == 'pulled' and event['order_id'])
        self.assertEqual(pulled['title'], 'Pulled 2 units from hr2s3 for order 111-3101896')
        self.assertEqual(pulled['delta'], -2)
        moved = next(event for event in result['events'] if event['kind'] == 'moved')
        self.assertEqual(moved['title'], 'Moved 1 unit hr1s1 → hr2s3')
        undone = next(event for event in result['events'] if event['undone'])
        self.assertEqual(undone['kind'], 'added')
        pending = next(event for event in result['events'] if event['pending'])
        self.assertEqual(pending['record']['id'], 6)
        sold = [event for event in result['events'] if event['kind'] == 'sold']
        self.assertEqual({event['status'] for event in sold}, {'pulled', 'unpulled', 'cancelled'})
        self.assertEqual(sold[0]['price'], 30.0)
        self.assertEqual(next(event for event in result['events'] if event['kind'] == 'returned')['status'], 'not restocked')
        received = next(event for event in result['events'] if event['kind'] == 'received')
        self.assertEqual(received['title'], 'Received 4 units · LOT 16981955')
        # A BOL origin is not a shelf: it stays in the detail so it never becomes "last seen".
        self.assertEqual(received['position'], '')
        self.assertIn('BOL location P3', received['detail'])
        deleted = [event for event in result['events'] if event['kind'] == 'deleted']
        self.assertEqual(len(deleted), 2)
        self.assertTrue(any(event['position'] == 'g1' for event in deleted))
        fba = next(event for event in result['events'] if event['kind'] == 'fba')
        self.assertEqual(fba['title'], 'Sent 3 units to FBA · shipment 4')
        self.assertIn('box BOX-02', fba['detail'])
        self.assertEqual(fba['position'], 'hr2s3')
        listed = [event for event in result['events'] if event['kind'] == 'listed']
        self.assertEqual([event['status'] for event in listed], ['failed', 'ok'])
        self.assertEqual(listed[1]['url'], 'https://ebay.example/1')
        ledger = result['ledger']
        self.assertEqual(ledger['on_shelf'], 3)
        self.assertEqual(ledger['no_location_units'], 1)
        self.assertEqual(ledger['pending_deletion_rows'], 1)
        self.assertEqual(ledger['received'], 4)
        self.assertEqual(ledger['shelf_adds'], 2)
        self.assertEqual(ledger['pulled'], 2)
        self.assertEqual(ledger['moves'], 1)
        self.assertEqual(ledger['adjustments'], 4)
        self.assertEqual(ledger['sold_units'], 4)
        self.assertEqual(ledger['sold_orders'], 3)
        self.assertEqual(ledger['sold_unpulled_units'], 1)
        self.assertEqual(ledger['sold_unpulled_orders'], ['112-9043165'])
        self.assertEqual(ledger['returned'], 1)
        self.assertEqual(ledger['restocked'], 0)
        self.assertEqual(ledger['fba_units'], 3)
        self.assertEqual(ledger['fba_sessions'], ['shipment 4'])
        self.assertEqual(ledger['deleted_rows'], 2)
        self.assertEqual(ledger['pending_events'], 1)
        self.assertEqual(ledger['undone_events'], 1)
        self.assertEqual(result['last_seen']['position'], 'hr2s3')
        self.assertEqual(result['last_exit']['kind'], 'pulled')
        self.assertEqual(result['last_exit']['order_id'], '111-3101896')
        texts = [flag['text'] for flag in result['flags']]
        self.assertTrue(any(text.startswith('1 unit sold but never pulled') and '112-9043165' in text for text in texts))
        self.assertTrue(any('pending or failed' in text for text in texts))
        self.assertTrue(any(text.startswith('Shelf record deleted or archived 2 time(s)') for text in texts))
        self.assertTrue(any('no location recorded' in text for text in texts))
        self.assertTrue(any('scheduled for automatic deletion' in text for text in texts))
        self.assertTrue(any('sent to Amazon FBA (shipment 4)' in text for text in texts))
        self.assertTrue(any('Received 4 on BOL but only 2 shelf add(s)' in text and '2 never scanned' in text for text in texts))
        self.assertTrue(any('returned and not restocked' in text for text in texts))
        self.assertTrue(any('were undone' in text for text in texts))
        alert = next(flag for flag in result['flags'] if flag['level'] == 'alert' and 'sold but never pulled' in flag['text'])
        self.assertEqual(alert['event_ids'], ['sold.db:orders:1615'])
        self.assertEqual(result['errors'], [])
        self.assertEqual(result['unavailable'], ['Prep details and BOL'])

    def test_family_includes_suffixed_units_and_prep_base_upc(self):
        self.seed()
        result = self.trail(family=True)
        self.assertTrue(result['identity']['custom'])
        self.assertEqual([row['searchrack_id'] for row in result['stock']], [10, 11, 12])
        self.assertEqual(result['ledger']['on_shelf'], 4)
        self.assertIn('028199270349-2', [event['upc'] for event in result['events']])
        self.assertEqual(sorted(event['status'] for event in result['events'] if event['kind'] == 'prep'), ['bad', 'good'])
        exact = self.trail('028199270349-2')
        self.assertEqual([row['searchrack_id'] for row in exact['stock']], [12])
        self.assertEqual([event['status'] for event in exact['events'] if event['kind'] == 'prep'], ['bad'])

    def test_missing_databases_and_tables_are_reported_not_created(self):
        with self.connect('searchRack.db') as conn:
            conn.execute('CREATE TABLE SEARCHRACK (ID INTEGER PRIMARY KEY, TITLE TEXT, BARCODE TEXT, QUANTITY INTEGER)')
            conn.execute('INSERT INTO SEARCHRACK VALUES (1, "Lamp", "111111111111", 1)')
        result = self.trail('111111111111')
        self.assertEqual(result['identity']['title'], 'Lamp')
        self.assertEqual(result['stock'][0]['position'], '')
        self.assertEqual(result['ledger']['on_shelf'], 1)
        self.assertEqual(len(result['unavailable']), 7)
        self.assertEqual(result['events'], [])
        self.assertFalse((self.root / 'sold.db').exists())
        with self.assertRaises(ValueError):
            self.trail('')
        with self.assertRaises(ValueError):
            self.trail('1' * 161)

    def test_read_only_and_unreadable_database(self):
        (self.root / 'rackhistory.db').write_bytes(b'not a database')
        with self.connect('searchRack.db') as conn:
            conn.execute('CREATE TABLE SEARCHRACK (ID INTEGER PRIMARY KEY, TITLE TEXT, BARCODE TEXT, QUANTITY INTEGER)')
        result = self.trail('111111111111')
        self.assertEqual(result['errors'], ['Rack history: could not be read.'])
        self.assertEqual(result['flags'][0]['text'], 'No records anywhere for this UPC')

    def test_helpers(self):
        self.assertEqual(display_upc('28199270349.0'), '028199270349')
        self.assertEqual(display_upc('0719978859014-24'), '719978859014-24')
        self.assertEqual(display_upc('ABC-1'), 'ABC-1')
        self.assertEqual(sort_key('2026-09-03'), '2026-09-03T00:00:00')
        self.assertEqual(sort_key('2026-04-29 18:31:00'), '2026-04-29T18:31:00')
        self.assertEqual(sort_key('2026-09-11T20:27:58Z'), '2026-09-11T20:27:58')
        self.assertEqual(sort_key('2026-09-11T17:06:57.446995+00:00'), '2026-09-11T17:06:57.446995')
        self.assertEqual(sort_key('nan'), '')


if __name__ == '__main__':
    unittest.main()
