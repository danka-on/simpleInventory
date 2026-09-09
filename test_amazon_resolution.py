import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from flask import Flask
from amazon_resolution_routes import register, database


class ResolutionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.asin, self.parent = 'B000000001', 'B000000002'
        self.row = {'barcode': '001234567890', 'seller_sku': 'SKU-1', 'asin': self.asin}
        with database(self.base / 'searchRack.db') as conn:
            conn.execute('CREATE TABLE fba_prep_sessions(id INTEGER,items_json TEXT,rejected_items_json TEXT)')
            conn.execute('INSERT INTO fba_prep_sessions VALUES(1,?,?)', (json.dumps([self.row]), '[]'))
        self.before = (self.base / 'searchRack.db').read_bytes()
        self.calls = []
        self.errors = [{'code': '100898', 'severity': 'ERROR', 'message': 'brand conflict', 'attributeNames': ['brand']}]
        def catalog(asin, **kw):
            return SimpleNamespace(payload={'asin': asin, 'attributes': {'brand': [{'value': 'Parent brand' if asin == self.parent else 'Child brand', 'marketplace_id': 'US'}]}, 'relationships': [{'marketplaceId': 'US', 'relationships': [{'parentAsins': [self.parent]} if asin == self.asin else {'childAsins': [self.asin]}]}]})
        def listing(*args, **kw):
            return SimpleNamespace(payload={'summaries': [{'asin': self.asin, 'productType': 'TABLECLOTH'}], 'issues': self.errors, 'attributes': {}})
        def submit(*args, **kw):
            self.calls.append(kw['body'])
            return SimpleNamespace(payload={'status': 'ACCEPTED', 'issues': []})
        self.api = patch.dict('sys.modules', {'sp_api': SimpleNamespace(), 'sp_api.api': SimpleNamespace(CatalogItems=lambda **kw: SimpleNamespace(get_catalog_item=catalog), ListingsItems=lambda **kw: SimpleNamespace(get_listings_item=listing, patch_listings_item=submit))})
        self.api.start()
        app = Flask(__name__, template_folder='templates', static_folder='static')
        app.testing = True
        register(app, self.base, lambda: ({}, 'seller', 'US', 'marketplace'))
        self.client = app.test_client()
        self.url = '/api/fba-prep/sessions/1/resolution'

    def tearDown(self):
        self.api.stop()
        self.tmp.cleanup()

    def proposal(self):
        r = self.client.get(self.url + '?barcode=001234567890')
        self.assertEqual(r.status_code, 200)
        self.assertEqual(len(r.json['cards']), 2)
        return {'barcode': self.row['barcode'], 'selected_asin': self.parent, 'selected_brand': 'Parent brand', 'fingerprint': r.json['fingerprint'], 'confirm_packaging': True}

    def test_read_does_not_mutate(self):
        self.proposal()
        self.assertEqual(self.before, (self.base / 'searchRack.db').read_bytes())

    def test_confirm_required(self):
        p = self.proposal(); p['confirm_packaging'] = False
        self.assertEqual(self.client.post(self.url, json=p).status_code, 400)
        self.assertEqual(self.calls, [])

    def test_unrelated_candidate_rejected(self):
        p = self.proposal(); p['selected_asin'] = 'B000000999'
        self.assertEqual(self.client.post(self.url, json=p).status_code, 400)
        self.assertEqual(self.calls, [])

    def test_stale_selection_rejected(self):
        p = self.proposal(); p['fingerprint'] = 'stale'
        self.assertEqual(self.client.post(self.url, json=p).status_code, 409)

    def test_brand_only_once_and_session_unchanged(self):
        p = self.proposal()
        result = self.client.post(self.url, json=p)
        self.assertTrue(result.json['accepted'])
        self.assertTrue(self.client.post(self.url, json=p).json['already_submitted'])
        self.assertEqual(len(self.calls), 1)
        self.assertEqual([p['path'] for p in self.calls[0]['patches']], ['/attributes/brand'])
        with database(self.base / 'searchRack.db') as conn:
            self.assertEqual(json.loads(conn.execute('SELECT items_json FROM fba_prep_sessions').fetchone()[0]), [self.row])

    def test_cleared_issue_blocks_edit(self):
        p = self.proposal(); self.errors.clear()
        self.assertEqual(self.client.post(self.url, json=p).status_code, 409)

    def test_unknown_item(self):
        self.assertEqual(self.client.get(self.url + '?barcode=wrong').status_code, 404)

    def test_page_renders(self):
        r = self.client.get('/fba-prep/sessions/1/resolve?barcode=001234567890')
        self.assertEqual(r.status_code, 200)
        self.assertIn(b'Review brand correction', r.data)


if __name__ == '__main__':
    unittest.main()
