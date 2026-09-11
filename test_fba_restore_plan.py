"""Restoring a shipment whose Amazon plan was cancelled outside this app.

Cancelling shipping in Seller Central voids the Amazon plan, not the physical
work. These tests cover the bookkeeping that has to survive a restore: the
session reopens, the cartons and carton scans are carried into the replacement
plan, and the ledger batch written by the first completion is parked so the
same units cannot be counted twice.
"""

import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault("DISABLE_BACKGROUND_SERVICES", "1")

from sweetshelves.bootstrap import app  # noqa: F401  Initialize routes for Flask client tests.
from sweetshelves import config as ss_config
from sweetshelves import fba_schema as ss_fba_schema
from sweetshelves import fba_shipments as ss_fba_shipments
from sweetshelves import runtime as ss_runtime


class FakeInboundApi:
    """Amazon as it answers after a seller cancels the shipment in Seller Central."""

    def __init__(self, *, plan_status="VOIDED", shipment_status="CANCELLED"):
        self.plan_status = plan_status
        self.shipment_status = shipment_status
        self.label_box_calls = []

    @staticmethod
    def response(**payload):
        return SimpleNamespace(payload=payload, errors=None)

    def get_inbound_plan(self, _plan_id):
        return self.response(inboundPlanId="wf-old-plan", status=self.plan_status)

    def list_inbound_plan_items(self, _plan_id, **_kwargs):
        return self.response(items=[{"msku": "SKU-1", "quantity": 2, "fnsku": "X001234567"}])

    def list_packing_options(self, _plan_id, **_kwargs):
        return self.response(packingOptions=[{
            "packingOptionId": "packing-1", "status": "ACCEPTED", "packingGroups": ["pg-1"],
        }])

    def list_packing_group_items(self, _plan_id, _group_id, **_kwargs):
        return self.response(items=[{"msku": "SKU-1", "quantity": 2}])

    def list_placement_options(self, _plan_id, **_kwargs):
        return self.response(placementOptions=[{
            "placementOptionId": "placement-1", "status": "ACCEPTED", "shipmentIds": ["shipment-1"],
        }])

    def get_shipment(self, _plan_id, shipment_id):
        return self.response(
            shipmentId=shipment_id,
            shipmentConfirmationId="FBA19PL7W2P5",
            status=self.shipment_status,
            placementOptionId="placement-1",
            destination={"warehouseId": "SCK8", "city": "Stockton", "stateOrProvinceCode": "CA"},
            selectedTransportationOptionId="to-1",
        )

    def list_transportation_options(self, _plan_id, **_kwargs):
        return self.response(transportationOptions=[])

    def list_shipment_boxes(self, _plan_id, shipment_id, **_kwargs):
        self.label_box_calls.append(shipment_id)
        return self.response(boxes=[])


class FbaRestoreCancelledPlanTest(unittest.TestCase):
    maxDiff = None

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base_dir = Path(self.temp.name)
        db = sqlite3.connect(self.base_dir / "searchRack.db")
        db.row_factory = sqlite3.Row
        ss_fba_schema._ensure_fba_prep_tables(db.cursor())
        now = "2026-09-11T12:00:00"
        workflow = {
            "stage": "transport_confirmed",
            "inbound_plan_id": "wf-old-plan",
            "packing_confirmed": True,
            "boxes_submitted": True,
            "placement_confirmed": True,
            "transport_confirmed": True,
            "selected_placement_option_id": "placement-1",
            "selected_transportation": [{"shipmentId": "shipment-1", "transportationOptionId": "to-1"}],
            "operation": {"status": "SUCCESS"},
            "plan_items": [{"msku": "SKU-1", "quantity": 2, "fnsku": "X001234567"}],
            "inventory_removed_by_msku": {"SKU-1": 2},
            "printed_item_label_counts": {"SKU-1": 2},
            "boxes": [
                {"local_id": "BOX-01", "packing_group_id": "pg-1", "length_in": 12, "width_in": 10,
                 "height_in": 8, "weight_lb": 5, "contents": [{"msku": "SKU-1", "quantity": 1}]},
                {"local_id": "BOX-02", "packing_group_id": "pg-1", "length_in": 12, "width_in": 10,
                 "height_in": 8, "weight_lb": 5, "contents": [{"msku": "SKU-1", "quantity": 1}]},
            ],
        }
        db.execute('''
            INSERT INTO fba_prep_sessions (
                id, session_name, items_json, item_count, total_units, status,
                created_at, updated_at, completed_at, completed_batch_id,
                amazon_inbound_plan_id, amazon_stage, amazon_state_json
            ) VALUES (1, 'cellar 3', ?, 1, 2, 'completed', ?, ?, ?, 7, ?, 'transport_confirmed', ?)
        ''', (
            json.dumps([{"barcode": "025398232475", "seller_sku": "SKU-1", "quantity": 2,
                         "fba_enablement_status": "ready", "amazon_fnsku": "X001234567"}]),
            now, now, now, "wf-old-plan", json.dumps(workflow),
        ))
        db.execute('''
            INSERT INTO fba_prep_batches (id, client_token, batch_name, status, total_skus,
                                          total_units, created_at, completed_at, session_id)
            VALUES (7, 'token-7', 'cellar 3', 'completed', 1, 2, ?, ?, 1)
        ''', (now, now))
        for index in range(2):
            db.execute('''
                INSERT INTO fba_pack_scans (session_id, scan_token, barcode, msku, box_id,
                                            packing_group_id, inventory_removed, label_printed, scanned_at)
                VALUES (1, ?, '025398232475', 'SKU-1', 'BOX-0' || ?, 'pg-1', 1, 1, ?)
            ''', (f"scan-{index}", index + 1, now))
        db.commit()
        db.close()
        ss_runtime.app.testing = True
        self.client = ss_runtime.app.test_client()

    def tearDown(self):
        self.temp.cleanup()

    def query(self, sql, params=()):
        """Read committed state. Windows will not delete the temp dir with a handle open."""
        db = sqlite3.connect(self.base_dir / "searchRack.db")
        try:
            db.row_factory = sqlite3.Row
            return db.execute(sql, params).fetchone()
        finally:
            db.close()

    def session_row(self):
        return self.query("SELECT * FROM fba_prep_sessions WHERE id=1")

    def batch_status(self):
        return self.query("SELECT status FROM fba_prep_batches WHERE id=7")[0]

    def call(self, method="post", payload=None, api=None):
        api = api or FakeInboundApi()
        created = {}

        def fake_create_plan(_api, _marketplace_id, _session_data, state):
            created["called"] = True
            state["inbound_plan_id"] = "wf-replacement-plan"
            state["stage"] = "plan_created"
            return state, {"operationId": "op-replacement"}

        with patch.object(ss_config, "BASE_DIR", self.base_dir), \
                patch.object(ss_fba_shipments, "_fba_amazon_client", return_value=(api, "ATVPDKIKX0DER")), \
                patch.object(ss_fba_shipments, "_fba_prepare_and_create_plan", side_effect=fake_create_plan):
            if method == "get":
                response = self.client.get("/api/fba-prep/sessions/1/amazon-restore")
            else:
                response = self.client.post("/api/fba-prep/sessions/1/amazon-restore",
                                            json=payload if payload is not None else {"confirm": True})
        return response, created

    def test_get_reports_what_amazon_says_and_what_would_be_kept(self):
        response, created = self.call(method="get")
        self.assertEqual(response.status_code, 200, response.get_json())
        restore = response.get_json()["restore"]
        self.assertTrue(restore["cancellation"]["restorable"])
        self.assertEqual(restore["cancellation"]["plan_status"], "VOIDED")
        self.assertEqual(restore["session_status"], "completed")
        self.assertEqual(restore["box_count"], 2)
        self.assertEqual(restore["packed_units"], 2)
        self.assertEqual(restore["pack_scan_count"], 2)
        self.assertEqual(restore["completed_batch_id"], 7)
        # Reading Amazon must never change anything.
        self.assertNotIn("called", created)
        self.assertEqual(self.session_row()["status"], "completed")
        self.assertEqual(self.batch_status(), "completed")

    def test_restore_needs_an_explicit_confirmation(self):
        response, created = self.call(payload={})
        self.assertEqual(response.status_code, 400)
        self.assertIn("Confirm the restore", response.get_json()["error"])
        self.assertNotIn("called", created)
        self.assertEqual(self.session_row()["status"], "completed")

    def test_a_live_shipment_is_never_rebuilt(self):
        api = FakeInboundApi(plan_status="ACTIVE", shipment_status="IN_TRANSIT")
        response, created = self.call(api=api)
        self.assertEqual(response.status_code, 400)
        self.assertIn("still reports this plan as usable", response.get_json()["error"])
        self.assertNotIn("called", created)
        row = self.session_row()
        self.assertEqual(row["status"], "completed")
        self.assertEqual(row["completed_batch_id"], 7)
        self.assertEqual(self.batch_status(), "completed")

    def test_restore_reopens_the_session_and_carries_the_cartons_across(self):
        response, created = self.call()
        self.assertEqual(response.status_code, 200, response.get_json())
        self.assertTrue(created.get("called"))
        payload = response.get_json()
        self.assertEqual(payload["operation_id"], "op-replacement")

        row = self.session_row()
        self.assertEqual(row["status"], "open")
        self.assertIsNone(row["completed_batch_id"])
        self.assertIsNone(row["completed_at"])
        # The first completion's ledger batch is parked, not deleted.
        self.assertEqual(self.batch_status(), "reopened")

        state = json.loads(row["amazon_state_json"])
        self.assertEqual(state["inbound_plan_id"], "wf-replacement-plan")
        recovery = state["recovery"]
        self.assertEqual(recovery["kind"], "cancelled_plan")
        self.assertEqual(recovery["phase"], "creating_plan")
        self.assertTrue(recovery["active"])
        self.assertTrue(recovery["old_plan_cancelled"])
        self.assertEqual(recovery["old_plan_id"], "wf-old-plan")
        self.assertEqual(recovery["restored_from_batch_id"], 7)
        self.assertEqual(recovery["packed_scan_count"], 2)
        self.assertEqual(recovery["preferred_destinations"], ["SCK8"])
        self.assertEqual(recovery["fnsku_by_msku"], {"SKU-1": "X001234567"})
        self.assertEqual([box["local_id"] for box in recovery["saved_boxes"]], ["BOX-01", "BOX-02"])
        # Warehouse removals and printed labels are prep work, not plan data.
        self.assertEqual(state["inventory_removed_by_msku"], {"SKU-1": 2})
        self.assertEqual(state["printed_item_label_counts"], {"SKU-1": 2})
        # The dead plan's shipments and quotes are gone from the session.
        self.assertEqual(state["shipments"], [])
        self.assertEqual(state["transportation_options"], [])
        self.assertFalse(state.get("transport_confirmed"))

        # Carton scans stay attached to the session for the replacement plan.
        self.assertEqual(self.query("SELECT COUNT(*) FROM fba_pack_scans WHERE session_id=1")[0], 2)

    def test_a_deleted_session_cannot_be_restored(self):
        db = sqlite3.connect(self.base_dir / "searchRack.db")
        with db:
            db.execute("UPDATE fba_prep_sessions SET status='deleted' WHERE id=1")
        db.close()
        response, created = self.call()
        self.assertEqual(response.status_code, 400)
        self.assertIn("deleted", response.get_json()["error"])
        self.assertNotIn("called", created)


if __name__ == "__main__":
    unittest.main()
