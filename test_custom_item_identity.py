"""/api/custom-item/identity must never store a placeholder as an item's name."""
from contextlib import ExitStack, closing
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

import DBmanager
from sweetshelves.bootstrap import app
from sweetshelves import config, warehouse_receiving


class CustomItemIdentityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.stack = ExitStack()
        self.stack.enter_context(patch.object(config, 'BASE_DIR', self.root))
        self.stack.enter_context(patch.object(DBmanager, 'BASE_DIR', str(self.root)))
        self.stack.enter_context(patch.object(
            warehouse_receiving, '_add_item_screening_lookup',
            return_value={'title': '', 'title_source': '', 'image_url': ''}))
        self.client = app.test_client()

    def tearDown(self):
        self.stack.close()
        self.temp.cleanup()

    def save(self, title, upc='123456789012'):
        return self.client.post('/api/custom-item/identity',
                                json={'upc': upc, 'item_description': title})

    def stored(self):
        with closing(sqlite3.connect(self.root / 'bol.db')) as conn:
            exists = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE name = 'custom_item_registry'").fetchone()
            if not exists:
                return []
            return conn.execute(
                'SELECT upc, item_description FROM custom_item_registry ORDER BY upc').fetchall()

    def test_placeholder_titles_are_refused(self):
        # 'No barcode item' was the /barcode and /multibarcode modal's own default,
        # so it reached the registry and overrode the real catalog name.
        for title in ('No barcode item', 'no barcode', 'Unknown', 'warehouse item', 'Custom item', 'n/a'):
            response = self.save(title)
            self.assertEqual(response.status_code, 400, f'{title!r} should be refused')
            self.assertFalse(response.get_json()['success'])
        self.assertEqual(self.stored(), [])

    def test_a_real_name_is_stored(self):
        response = self.save('Red ceramic mug 12oz')
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        self.assertTrue(response.get_json()['success'])
        self.assertEqual(self.stored(), [('123456789012', 'Red ceramic mug 12oz')])


if __name__ == '__main__':
    unittest.main()
