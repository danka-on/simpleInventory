import json
from pathlib import Path
import sqlite3
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from flask import Flask
from fba_attention_routes import register, identity


def schema(cur):
    cur.execute("""CREATE TABLE IF NOT EXISTS fba_prep_sessions(id INTEGER PRIMARY KEY,
        session_name TEXT, items_json TEXT DEFAULT '[]', rejected_items_json TEXT DEFAULT '[]',
        amazon_state_json TEXT DEFAULT '{}', amazon_stage TEXT DEFAULT 'draft',
        amazon_inbound_plan_id TEXT, status TEXT DEFAULT 'open', item_count INTEGER DEFAULT 0,
        total_units INTEGER DEFAULT 0, count_revision INTEGER DEFAULT 0, created_at TEXT, updated_at TEXT)""")
    cur.execute('CREATE TABLE IF NOT EXISTS fba_pack_scans(session_id INTEGER)')


class AttentionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        app = Flask(__name__)
        app.testing = True
        self.service = register(app, self.base, schema, lambda item: dict(item),
                                lambda state: state.get('approval', {}), lambda: ({}, 'seller', 'US', 'market'))
        self.client = app.test_client()
        self.item = dict(barcode='0012345', seller_sku='SKU', asin='B000000001', quantity=3,
                         title='Test item', fba_set_aside=True, fba_enablement_status='failed', fba_enablement_error='Missing description')
        self.add_session(1, rejected=[self.item])
        self.payload = dict(summaries=[dict(marketplaceId='US', asin='B000000001', fnSku='X123')], issues=[], fulfillmentAvailability=[dict(fulfillmentChannelCode='AMAZON_NA')])
        self.eligible = True
        self.checked_skus = []
        self.fail = False
        def listing(seller, sku, **kwargs):
            self.checked_skus.append(sku)
            if self.fail:
                raise RuntimeError('private credential response')
            return SimpleNamespace(payload=self.payload)
        self.api = patch.dict('sys.modules', {'sp_api': SimpleNamespace(), 'sp_api.api': SimpleNamespace(
            ListingsItems=lambda **kw: SimpleNamespace(get_listings_item=listing),
            FbaInboundEligibility=lambda **kw: SimpleNamespace(get_item_eligibility_preview=lambda **args: SimpleNamespace(payload={'isEligibleForProgram': self.eligible})))})
        self.api.start()

    def tearDown(self):
        self.api.stop()
        self.tmp.cleanup()

    def add_session(self, id, items=None, rejected=None, **fields):
        with self.service.database() as conn:
            conn.execute('INSERT INTO fba_prep_sessions(id,session_name,items_json,rejected_items_json) VALUES(?,?,?,?)', (id, 'Batch ' + str(id), json.dumps(items or []), json.dumps(rejected or [])))
            for key, value in fields.items():
                conn.execute('UPDATE fba_prep_sessions SET ' + key + '=? WHERE id=?', (value, id))

    def rows(self):
        response = self.client.get('/api/fba-prep/attention')
        self.assertEqual(response.status_code, 200)
        return response.json['items']

    def action(self, action, **data):
        return self.client.post('/api/fba-prep/attention/1/' + action, json=data)

    def test_latest_count_deduplicated_and_persists(self):
        self.rows()
        self.add_session(2, rejected=[dict(self.item, quantity=2)])
        rows = self.rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['item']['quantity'], 2)
        self.assertEqual(rows[0]['source_session_id'], 2)
        self.assertEqual(identity(self.item), identity(dict(self.item, barcode='12345')))
        self.action('recheck')
        self.assertEqual(self.rows()[0]['status'], 'ready_to_retry')

    def test_newer_active_item_suppresses_old_rejection(self):
        self.rows()
        self.add_session(2, items=[dict(self.item, fba_set_aside=False, fba_enablement_status='ready', fba_enablement_error='')])
        self.assertEqual(self.rows()[0]['status'], 'assigned')
        self.assertEqual(self.action('add').status_code, 409)

    def test_errors_override_fnsku_and_preserve_original_reason(self):
        self.payload['issues'] = [dict(code='18299', severity='ERROR', message='Approval is required')]
        self.rows()
        row = self.action('recheck').json['item']
        self.assertEqual(row['category'], 'approval')
        self.assertEqual(row['original_reason'], 'Missing description')
        self.assertEqual(self.action('add').status_code, 409)

    def test_previous_approval_requires_inbound_confirmation(self):
        self.payload['issues'] = [dict(severity='ERROR', message='Approval is required')]
        self.rows(); self.action('recheck')
        self.payload['issues'] = []
        self.eligible = False
        self.assertEqual(self.action('recheck').json['item']['category'], 'approval')
        self.eligible = True
        self.assertEqual(self.action('recheck').json['item']['status'], 'ready_to_retry')

    def test_checks_saved_fba_companion(self):
        self.add_session(2, rejected=[dict(self.item, proposed_fba_seller_sku='SKU-FBA', fba_offer_strategy='separate_fba_sku')])
        self.rows(); self.action('recheck')
        self.assertEqual(self.checked_skus, ['SKU-FBA'])
        self.assertEqual(self.rows()[0]['item']['seller_sku'], 'SKU-FBA')

    def test_network_failure_keeps_reason_and_blocks_transfer(self):
        self.rows(); self.action('recheck')
        self.fail = True
        row = self.action('recheck').json['item']
        self.assertTrue(row['check_error'])
        self.assertNotIn('private', row['check_error'])
        self.assertEqual(row['original_reason'], 'Missing description')
        self.assertNotEqual(row['status'], 'ready_to_retry')
        self.assertNotEqual(row['category'], 'ready')
        self.assertEqual(row['current_reason'], 'Missing description')
        self.assertEqual(self.action('add').status_code, 409)

    def test_waiting_activation_and_asin_mismatch(self):
        self.rows()
        self.payload['fulfillmentAvailability'] = []
        self.assertEqual(self.action('recheck').json['item']['category'], 'activation')
        self.payload['summaries'][0]['asin'] = 'B000000002'
        self.assertEqual(self.action('recheck').json['item']['category'], 'catalog')

    def test_transfer_preserves_units_and_is_not_repeatable(self):
        self.rows()
        result = self.action('add')
        self.assertEqual(result.status_code, 200, result.json)
        target = result.json['session_id']
        with self.service.database() as conn:
            row = conn.execute('SELECT * FROM fba_prep_sessions WHERE id=?', (target,)).fetchone()
            self.assertEqual(row['total_units'], 3)
            self.assertEqual(row['item_count'], 1)
            self.assertEqual(row['count_revision'], 1)
            self.assertFalse(json.loads(row['items_json'])[0]['fba_set_aside'])
            source = conn.execute('SELECT rejected_items_json FROM fba_prep_sessions WHERE id=1').fetchone()[0]
            self.assertEqual(json.loads(source), [self.item])
        self.assertEqual(self.rows()[0]['status'], 'assigned')
        self.assertEqual(self.action('add').status_code, 409)

    def test_locked_destination_and_packed_destination_blocked(self):
        self.rows()
        self.add_session(2, amazon_inbound_plan_id='PLAN')
        self.assertEqual(self.action('add', session_id=2).status_code, 409)
        self.add_session(3)
        with self.service.database() as conn:
            conn.execute('INSERT INTO fba_pack_scans VALUES(3)')
        self.assertEqual(self.action('add', session_id=3).status_code, 409)

    def test_active_failed_source_must_be_set_aside(self):
        self.add_session(2, items=[self.item], amazon_stage='plan_failed')
        self.rows()
        response = self.action('add')
        self.assertEqual(response.status_code, 409)
        self.assertIn('still in Batch 2', response.json['error'])

    def test_active_plan_approval_imported_even_if_listing_ready(self):
        self.add_session(2, items=[dict(self.item, fba_set_aside=False, fba_enablement_status='ready', fba_enablement_error='')], amazon_state_json=json.dumps({'approval': {'sku': 'Approval is required'}}))
        self.assertEqual(self.rows()[0]['category'], 'approval')

    def test_changed_item_during_recheck_cannot_be_overwritten(self):
        self.rows()
        original = self.service.inspect
        def changed(row):
            self.add_session(2, rejected=[dict(self.item, quantity=5)])
            return original(row)
        with patch.object(self.service, 'inspect', side_effect=changed):
            self.assertEqual(self.action('recheck').status_code, 409)
        self.assertEqual(self.rows()[0]['item']['quantity'], 5)

    def test_existing_draft_keeps_other_items_and_quantities(self):
        self.rows()
        other = dict(self.item, barcode='222222', seller_sku='OTHER', quantity=7, fba_set_aside=False, fba_enablement_status='ready', fba_enablement_error='')
        self.add_session(2, items=[other])
        result = self.action('add', session_id=2)
        self.assertEqual(result.status_code, 200, result.json)
        with self.service.database() as conn:
            row = conn.execute('SELECT * FROM fba_prep_sessions WHERE id=2').fetchone()
            self.assertEqual(row['total_units'], 10)
            self.assertEqual(json.loads(row['items_json'])[0], other)

    def test_real_schema_and_normalization_transfer(self):
        try:
            from sweetshelves.fba_schema import _ensure_fba_prep_tables
            from sweetshelves.fba_inventory import _fba_session_item_payload
        except ImportError:
            self.skipTest('Modular schema integration is tested in the main checkout.')
        self.service.path = self.base / 'integration.db'
        self.service.ensure_schema = _ensure_fba_prep_tables
        self.service.normalize_item = _fba_session_item_payload
        with self.service.database() as conn:
            conn.execute("INSERT INTO fba_prep_sessions(session_name,rejected_items_json,created_at,updated_at) VALUES('Aside',?,'now','now')", (json.dumps([self.item]),))
        self.rows()
        response = self.action('add')
        self.assertEqual(response.status_code, 200, response.json)
        with self.service.database() as conn:
            row = conn.execute('SELECT * FROM fba_prep_sessions WHERE id=?', (response.json['session_id'],)).fetchone()
            item = json.loads(row['items_json'])[0]
            self.assertEqual(item['quantity'], 3)
            self.assertEqual(item['fnsku'], 'X123')
            self.assertEqual(item['fba_enablement_status'], 'ready')


if __name__ == '__main__':
    unittest.main()
