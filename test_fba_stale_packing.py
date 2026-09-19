"""Amazon rejecting box contents with FBA_INB_0364 ("regenerate the packing options").

Repeating setPackingInformation cannot clear that error; only fresh packing
options can. Resending the boxes must regenerate packing on the same plan, keep
the measured cartons, re-confirm a compatible option and resend automatically.
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

STALE_PROBLEM = {
    "severity": "ERROR", "code": "FBA_INB_0364",
    "message": "ERROR: Something went wrong with placement. Please regenerate the packing options. {details}",
}


class FakeInboundApi:
    def __init__(self):
        self.calls = []
        self.regenerated = False
        self.confirmed = ""

    @staticmethod
    def response(**payload):
        return SimpleNamespace(payload=payload, errors=None)

    def get_inbound_operation_status(self, operation_id):
        return self.response(operationStatus="SUCCESS", operationProblems=[])

    def get_inbound_plan(self, _plan_id):
        return self.response(inboundPlanId="wf-plan", status="ACTIVE")

    def list_inbound_plan_items(self, _plan_id, **_kwargs):
        return self.response(items=[{"msku": "SKU-1", "quantity": 2, "fnsku": "X001234567"}])

    def list_packing_options(self, _plan_id, **_kwargs):
        if not self.regenerated:
            return self.response(packingOptions=[
                {"packingOptionId": "po-old", "status": "ACCEPTED", "packingGroups": ["pg-old"]}])
        return self.response(packingOptions=[
            {"packingOptionId": "po-old", "status": "EXPIRED", "packingGroups": ["pg-old"]},
            {"packingOptionId": "po-new", "status": "ACCEPTED" if self.confirmed == "po-new" else "OFFERED",
             "packingGroups": ["pg-new"]},
        ])

    def list_packing_group_items(self, _plan_id, _group_id, **_kwargs):
        return self.response(items=[{"msku": "SKU-1", "quantity": 2}])

    def list_placement_options(self, _plan_id, **_kwargs):
        return self.response(placementOptions=[])

    def generate_packing_options(self, plan_id):
        self.calls.append("generate_packing_options")
        self.regenerated = True
        return self.response(operationId="op-generate")

    def confirm_packing_option(self, plan_id, option_id):
        self.calls.append("confirm_packing_option:" + option_id)
        self.confirmed = option_id
        return self.response(operationId="op-confirm")

    def set_packing_information(self, plan_id, **body):
        self.calls.append("set_packing_information")
        self.last_packing_body = body
        return self.response(operationId="op-submit")


class FbaStalePackingTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base_dir = Path(self.temp.name)
        db = sqlite3.connect(self.base_dir / "searchRack.db")
        ss_fba_schema._ensure_fba_prep_tables(db.cursor())
        now = "2026-09-19T11:00:00"
        box = {"packing_group_id": "pg-old", "length_in": 12, "width_in": 10, "height_in": 8, "weight_lb": 5,
               "contents": [{"msku": "SKU-1", "quantity": 1}]}
        workflow = {
            "stage": "boxes_submitting", "inbound_plan_id": "wf-plan",
            "packing_confirmed": True, "selected_packing_option_id": "po-old",
            "packing_options": [{"packingOptionId": "po-old", "status": "ACCEPTED", "packingGroups": ["pg-old"]}],
            "packing_groups": [{"packing_group_id": "pg-old", "label": "Group 1",
                                "items": [{"msku": "SKU-1", "quantity": 2}]}],
            "plan_items": [{"msku": "SKU-1", "quantity": 2, "fnsku": "X001234567"}],
            "plan": {"status": "ACTIVE"},
            "operation": {"id": "op-failed", "kind": "submit_boxes", "status": "FAILED",
                          "next_stage": "boxes_submitted", "success_flag": "boxes_submitted",
                          "problems": [STALE_PROBLEM]},
            "last_error": STALE_PROBLEM["message"],
            "boxes": [dict(box, local_id="BOX-01"), dict(box, local_id="BOX-02")],
        }
        db.execute('''
            INSERT INTO fba_prep_sessions (
                id, session_name, items_json, item_count, total_units, status,
                created_at, updated_at, amazon_inbound_plan_id, amazon_stage, amazon_state_json
            ) VALUES (1, 'stale packing', ?, 1, 2, 'open', ?, ?, 'wf-plan', 'boxes_submitting', ?)
        ''', (json.dumps([{"barcode": "025398232475", "seller_sku": "SKU-1", "quantity": 2}]),
              now, now, json.dumps(workflow)))
        db.commit()
        db.close()
        ss_runtime.app.testing = True
        self.client = ss_runtime.app.test_client()
        self.api = FakeInboundApi()

    def tearDown(self):
        self.temp.cleanup()

    def request(self, method, path, payload=None):
        with patch.object(ss_config, "BASE_DIR", self.base_dir), \
                patch.object(ss_fba_shipments, "_fba_amazon_client", return_value=(self.api, "ATVPDKIKX0DER")):
            if method == "get":
                response = self.client.get(path)
            else:
                response = self.client.post(path, json=payload or {})
        body = response.get_json()
        self.assertEqual(response.status_code, 200, body)
        return body["amazon_workflow"]

    def test_resend_regenerates_packing_and_resubmits_saved_cartons(self):
        state = self.request("post", "/api/fba-prep/sessions/1/amazon/submit-boxes")
        self.assertEqual(self.api.calls, ["generate_packing_options"])
        self.assertEqual(state["recovery"]["kind"], "regenerate_packing")
        self.assertEqual(state["operation"]["kind"], "recovery_generate_packing")
        self.assertEqual(len(state["boxes"]), 2)

        state = self.request("get", "/api/fba-prep/sessions/1/amazon/sync")
        self.assertEqual(self.api.calls[-1], "confirm_packing_option:po-new")

        state = self.request("get", "/api/fba-prep/sessions/1/amazon/sync")
        self.assertEqual(self.api.calls[-1], "set_packing_information")
        self.assertEqual({box["packing_group_id"] for box in state["boxes"]}, {"pg-new"})
        self.assertEqual([box["local_id"] for box in state["boxes"]], ["BOX-01", "BOX-02"])
        self.assertEqual(state["recovery"]["phase"], "complete")
        self.assertFalse(state["recovery"]["active"])
        self.assertEqual(state["operation"]["kind"], "submit_boxes")

        state = self.request("get", "/api/fba-prep/sessions/1/amazon/sync")
        self.assertTrue(state["boxes_submitted"])
        self.assertEqual(state["stage"], "boxes_submitted")

    def test_other_submit_failures_still_just_resend(self):
        db = sqlite3.connect(self.base_dir / "searchRack.db")
        state = json.loads(db.execute("SELECT amazon_state_json FROM fba_prep_sessions").fetchone()[0])
        state["operation"]["problems"] = [{"severity": "ERROR", "code": "InvalidInput", "message": "bad weight"}]
        db.execute("UPDATE fba_prep_sessions SET amazon_state_json = ?", (json.dumps(state),))
        db.commit()
        db.close()
        state = self.request("post", "/api/fba-prep/sessions/1/amazon/submit-boxes")
        self.assertEqual(self.api.calls, ["set_packing_information"])
        self.assertNotIn("recovery", state)


    def test_unfulfillable_item_is_not_treated_as_stale_packing(self):
        operation = {"status": "FAILED", "problems": [dict(STALE_PROBLEM, message=(
            "ERROR: Something went wrong with placement. Please regenerate the packing options. "
            "Validation failed due to: Units in the request are not fulfillable., for items: "
            "Item{asin=B0DMKQFYPW, mSku=DU-0SWH-1M7L, fnsku=X005BAXWUP, condition=NewItem, numberOfUnits=1}"))]}
        self.assertFalse(ss_fba_shipments._fba_operation_needs_new_packing(operation))
        self.assertTrue(ss_fba_shipments._fba_operation_needs_new_packing(
            {"status": "FAILED", "problems": [STALE_PROBLEM]}))


if __name__ == "__main__":
    unittest.main()
