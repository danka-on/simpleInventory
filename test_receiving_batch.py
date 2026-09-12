"""Receiving batches: one transaction per receipt, idempotent retries, per-unit notes, variant matching."""
from contextlib import ExitStack, closing
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

import DBmanager
from sweetshelves.bootstrap import app
from sweetshelves import caching, config, inventory_age, inventory_history, warehouse_receiving


class ReceivingBatchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.stack = ExitStack()
        self.stack.enter_context(patch.object(config, 'BASE_DIR', self.root))
        self.stack.enter_context(patch.object(DBmanager, 'BASE_DIR', str(self.root)))
        self.stack.enter_context(patch.object(caching, '_invalidate_searchrack_cache'))
        self.stack.enter_context(patch.object(inventory_age, '_reconcile_inventory_age_batches'))
        self.stack.enter_context(patch.object(
            warehouse_receiving, '_add_item_screening_lookup',
            return_value={'title': 'Catalog title', 'title_source': 'rawbol', 'image_url': ''}))
        with closing(sqlite3.connect(self.root / 'searchRack.db')) as conn:
            conn.execute('CREATE TABLE shelves (shelf_name TEXT)')
            conn.executemany('INSERT INTO shelves VALUES (?)', [('a1',), ('b2',)])
            conn.commit()
        self.client = app.test_client()

    def tearDown(self):
        self.stack.close()
        self.temp.cleanup()

    def receive(self, **fields):
        data = {'item_position': 'a1', 'submission_id': 'receipt-0001'}
        data.update(fields)
        return self.client.post('/additemtrue', data=data,
                                headers={'Accept': '*/*', 'X-Requested-With': 'fetch'})

    def rows(self):
        with closing(sqlite3.connect(self.root / 'searchRack.db')) as conn:
            exists = conn.execute("SELECT 1 FROM sqlite_master WHERE name = 'SEARCHRACK'").fetchone()
            if not exists:
                return []
            return conn.execute(
                'SELECT BARCODE, ITEM_POSITION, QUANTITY, COALESCE(WAREHOUSE_NOTE, "") FROM SEARCHRACK ORDER BY ID'
            ).fetchall()

    def test_note_stays_on_the_unit_it_was_written_for(self):
        entries = [
            {'code': '123456789012', 'suffix': 0, 'quantity': 2, 'note': ''},
            {'code': '123456789012', 'suffix': 0, 'quantity': 1, 'note': 'Torn box'},
        ]
        response = self.receive(barcode='123456789012,123456789012,123456789012',
                                entries=json.dumps(entries),
                                warehouse_notes=json.dumps({'123456789012': 'Torn box'}))
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        payload = response.get_json()
        self.assertTrue(payload['success'])
        self.assertEqual([label['barcode'] for label in payload['labels_to_print']], ['123456789012-1'])
        self.assertEqual(self.rows(), [
            ('123456789012', 'a1', 2, ''),
            ('123456789012-1', 'a1', 1, 'Torn box'),
        ])

    def test_replayed_receipt_is_not_added_twice(self):
        entries = [{'code': '123456789012', 'suffix': 0, 'quantity': 1, 'note': 'Torn box'}]
        first = self.receive(barcode='123456789012', entries=json.dumps(entries))
        self.assertEqual(first.status_code, 200)
        second = self.receive(barcode='123456789012', entries=json.dumps(entries))
        self.assertEqual(second.status_code, 200)
        self.assertTrue(second.get_json()['duplicate'])
        self.assertEqual(second.get_json()['labels_to_print'], first.get_json()['labels_to_print'])
        self.assertEqual(self.rows(), [('123456789012-1', 'a1', 1, 'Torn box')])
        third = self.receive(barcode='123456789012', entries=json.dumps(entries), submission_id='receipt-0002')
        self.assertEqual(third.status_code, 200)
        self.assertEqual(len(self.rows()), 2)

    def test_failed_unit_rolls_back_the_whole_receipt(self):
        real = warehouse_receiving.addToSearchRack
        calls = []

        def flaky(*args, **kwargs):
            calls.append(args[1])
            if len(calls) == 2:
                return False
            return real(*args, **kwargs)

        with patch.object(warehouse_receiving, 'addToSearchRack', side_effect=flaky):
            response = self.receive(barcode='123456789012,223456789012')
        self.assertEqual(response.status_code, 500)
        self.assertEqual(self.rows(), [])
        # Nothing was recorded for the receipt, so the browser's retry lands cleanly.
        retry = self.receive(barcode='123456789012,223456789012')
        self.assertEqual(retry.status_code, 200)
        self.assertEqual([row[0] for row in self.rows()], ['123456789012', '223456789012'])

    def test_leading_zero_spelling_lands_on_the_existing_row(self):
        self.assertTrue(DBmanager.addToSearchRack('a1', '071997885901'))
        response = self.receive(barcode='71997885901')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.rows(), [('071997885901', 'a1', 2, '')])
        # A suffix names one unit: it never merges into the base row.
        self.assertTrue(DBmanager.addToSearchRack('a1', '71997885901-2'))
        self.assertEqual([row[0] for row in self.rows()], ['071997885901', '71997885901-2'])

    def test_unknown_shelf_is_rejected_and_legacy_pattern_allowed(self):
        rejected = self.receive(barcode='123456789012', item_position='zz9')
        self.assertEqual(rejected.status_code, 400)
        self.assertEqual(self.rows(), [])
        legacy = self.receive(barcode='123456789012', item_position='gr2s3')
        self.assertEqual(legacy.status_code, 200)
        self.assertEqual(self.rows(), [('123456789012', 'gr2s3', 1, '')])

    def test_suffixed_lookups_ignore_leading_zeros(self):
        self.assertTrue(DBmanager.addToSearchRack('b2', '719978859014-3'))
        # The age ledger tables are created by startup maintenance, not by this fixture.
        self.stack.enter_context(patch.object(inventory_history, '_inventory_age_lookup', return_value={}))
        search = self.client.post('/api/search/searchRack', json={'q': '0719978859014-3'})
        self.assertEqual(search.status_code, 200)
        self.assertIn('719978859014-3', search.get_data(as_text=True))
        location = self.client.post('/api/lookup_location', json={'barcode': '0719978859014-3'})
        self.assertEqual(location.get_json(), {'found': True, 'item_position': 'b2', 'pictureposition': None})
        base = self.client.post('/api/lookup_location', json={'barcode': '0719978859014'})
        self.assertEqual(base.get_json(), {'found': False})


if __name__ == '__main__':
    unittest.main()
