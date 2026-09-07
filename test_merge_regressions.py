"""Exercise the overlapping receiving changes from both saved branches."""
from contextlib import ExitStack
import json
from pathlib import Path
import sqlite3
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from sweetshelves.bootstrap import app
from sweetshelves import caching, config, errors, inventory_age, warehouse_receiving


class ReceivingMergeTests(unittest.TestCase):
    def receive(self, *, added=True, age_failure=False):
        with tempfile.TemporaryDirectory() as folder, ExitStack() as stack:
            direct_connections = []
            def connect(*args, **kwargs):
                connection = sqlite3.connect(*args, **kwargs)
                direct_connections.append(connection)
                return connection

            metadata = {'title': 'Resolved catalog title', 'title_source': 'rawbol',
                        'image_url': '/catalog.jpg'}
            stack.enter_context(patch.object(config, 'BASE_DIR', Path(folder)))
            stack.enter_context(patch.object(warehouse_receiving, 'sqlite3', SimpleNamespace(connect=connect)))
            stack.enter_context(patch.object(warehouse_receiving, '_add_item_screening_lookup', return_value=metadata))
            writer = stack.enter_context(patch.object(warehouse_receiving, 'addToSearchRack', return_value=added))
            invalidate = stack.enter_context(patch.object(caching, '_invalidate_searchrack_cache'))
            stack.enter_context(patch.object(errors, '_safe_error', return_value='An internal error occurred'))
            stack.enter_context(patch.object(inventory_age, '_reconcile_inventory_age_batches',
                                            side_effect=sqlite3.OperationalError('busy') if age_failure else None))
            client = app.test_client()
            with client.session_transaction() as session:
                session['inv_barcode'] = '123456789012'
                session['inv_position_code'] = 'A1'
            response = client.post('/additemtrue', data={
                'barcode': '123456789012', 'item_position': 'A1',
                'title_overrides': json.dumps({'123456789012': 'Browser title'}),
            }, headers={'Accept': '*/*'})
            writer.assert_called_once()
            self.assertIs(writer.call_args.args[-1], metadata)
            for connection in direct_connections:
                with self.assertRaises(sqlite3.ProgrammingError):
                    connection.execute('SELECT 1')
            if added:
                self.assertEqual(response.status_code, 200)
                self.assertTrue(response.get_json()['success'])
                invalidate.assert_called_once()
                with client.session_transaction() as session:
                    self.assertNotIn('inv_barcode', session)
            else:
                self.assertEqual(response.status_code, 500)
                invalidate.assert_not_called()
                with client.session_transaction() as session:
                    self.assertEqual(session['inv_barcode'], '123456789012')

    def test_success_preserves_resolved_metadata(self):
        self.receive()

    def test_failed_write_is_not_reported_as_success_and_closes_connection(self):
        self.receive(added=False)

    def test_age_ledger_lock_does_not_fail_an_already_saved_receipt(self):
        self.receive(age_failure=True)


if __name__ == '__main__':
    unittest.main()
