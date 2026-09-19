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

    def test_unfulfillable_item_text_survives_a_generic_retry_failure(self):
        # Session 12 (2026-09-19): the first failure named DU-0SWH-1M7L; a retry failed
        # with "Something went wrong" and the remove-and-rebuild offer disappeared.
        named = ("ERROR: Something went wrong with placement. Please regenerate the packing options. "
                 "Validation failed due to: Units in the request are not fulfillable., for items: "
                 "Item{asin=B0DMKQFYPW, mSku=DU-0SWH-1M7L, fnsku=X005BAXWUP, condition=NewItem, numberOfUnits=1}")
        generic = "ERROR: Something went wrong. Please try again later."

        class Api:
            def __init__(self, message):
                self.message = message

            def get_inbound_operation_status(self, _operation_id):
                return SimpleNamespace(payload={"operationStatus": "FAILED", "operationProblems": [
                    {"severity": "ERROR", "code": "FBA_INB_0364", "message": self.message}]}, errors=None)

        state = {"inbound_plan_id": "wf-plan", "stage": "boxes_submitting",
                 "operation": {"id": "op-1", "kind": "submit_boxes", "status": "IN_PROGRESS"}}
        state = ss_fba_shipments._fba_amazon_refresh_operation(Api(named), state)
        self.assertEqual(state["unfulfillable_error"], named)
        state["operation"] = {"id": "op-2", "kind": "recovery_generate_packing", "status": "IN_PROGRESS"}
        state = ss_fba_shipments._fba_amazon_refresh_operation(Api(generic), state)
        self.assertEqual(state["last_error"], generic)
        self.assertEqual(state["unfulfillable_error"], named)

        class Accepted:
            def get_inbound_operation_status(self, _operation_id):
                return SimpleNamespace(payload={"operationStatus": "SUCCESS", "operationProblems": []}, errors=None)

        state["operation"] = {"id": "op-3", "kind": "submit_boxes", "status": "IN_PROGRESS",
                              "next_stage": "boxes_submitted", "success_flag": "boxes_submitted"}
        state = ss_fba_shipments._fba_amazon_refresh_operation(Accepted(), state)
        self.assertNotIn("unfulfillable_error", state)
        self.assertTrue(state["boxes_submitted"])

    def stale_group_state(self, phase):
        db = sqlite3.connect(self.base_dir / "searchRack.db")
        state = json.loads(db.execute("SELECT amazon_state_json FROM fba_prep_sessions").fetchone()[0])
        state.update({
            "stage": "packing", "packing_confirmed": True, "selected_packing_option_id": "po-new",
            "packing_options": [{"packingOptionId": "po-new", "status": "ACCEPTED", "packingGroups": ["pg-new"]}],
            "packing_groups": [{"packing_group_id": "pg-new", "label": "Group 1",
                                "items": [{"msku": "SKU-1", "quantity": 2}]}],
            "operation": {"id": "op-confirm", "kind": "confirm_packing", "status": "SUCCESS",
                          "next_stage": "packing", "success_flag": "packing_confirmed"},
            "last_error": "",
            "recovery": {"active": True, "phase": phase, "old_plan_id": "wf-old",
                         "saved_boxes": json.loads(json.dumps(state["boxes"]))},
        })
        db.execute("UPDATE fba_prep_sessions SET amazon_state_json = ?", (json.dumps(state),))
        db.commit()
        db.close()
        self.api.regenerated = True
        self.api.confirmed = "po-new"

    def test_sync_remaps_cartons_when_the_manual_confirm_lost_its_phase_flip(self):
        # Session 12 (2026-09-19): confirm-packing ran during 'choose_packing' but the phase
        # stayed put, so the cartons kept the old plan's packing group ids.
        self.stale_group_state("choose_packing")
        state = self.request("get", "/api/fba-prep/sessions/1/amazon/sync")
        self.assertEqual({box["packing_group_id"] for box in state["boxes"]}, {"pg-new"})
        self.assertEqual(state["recovery"]["phase"], "complete")
        self.assertFalse(state["recovery"]["active"])
        self.assertNotIn("warning", state["recovery"])

    def test_submit_boxes_never_sends_stale_packing_group_ids(self):
        # Amazon: "Provided package grouping ids are incorrect. Expected all of [...]".
        self.stale_group_state("choose_packing")
        state = self.request("post", "/api/fba-prep/sessions/1/amazon/submit-boxes")
        self.assertEqual(self.api.calls, ["set_packing_information"])
        sent_groups = {box["packingGroupId"] for box in self.api.last_packing_body["packageGroupings"]}
        self.assertEqual(sent_groups, {"pg-new"})
        self.assertEqual({box["packing_group_id"] for box in state["boxes"]}, {"pg-new"})
        self.assertEqual(state["stage"], "boxes_submitting")

    def test_submit_boxes_stops_when_the_re_sort_moves_units_between_cartons(self):
        self.stale_group_state("choose_packing")
        db = sqlite3.connect(self.base_dir / "searchRack.db")
        state = json.loads(db.execute("SELECT amazon_state_json FROM fba_prep_sessions").fetchone()[0])
        state["packing_groups"] = [
            {"packing_group_id": "pg-new", "label": "Group 1", "items": [{"msku": "SKU-1", "quantity": 1}]},
            {"packing_group_id": "pg-two", "label": "Group 2", "items": [{"msku": "SKU-2", "quantity": 1}]},
        ]
        state["boxes"][0]["contents"] = [{"msku": "SKU-1", "quantity": 1}, {"msku": "SKU-2", "quantity": 1}]
        state["plan_items"] = [{"msku": "SKU-1", "quantity": 2, "fnsku": "X001234567"},
                               {"msku": "SKU-2", "quantity": 1, "fnsku": "X001234568"}]
        db.execute("UPDATE fba_prep_sessions SET amazon_state_json = ?", (json.dumps(state),))
        db.commit()
        db.close()
        with patch.object(ss_config, "BASE_DIR", self.base_dir), \
                patch.object(ss_fba_shipments, "_fba_amazon_client", return_value=(self.api, "ATVPDKIKX0DER")):
            response = self.client.post("/api/fba-prep/sessions/1/amazon/submit-boxes", json={})
        body = response.get_json()
        self.assertEqual(response.status_code, 400, body)
        self.assertIn("re-sorted", body["error"])
        self.assertIn("SKU-2: BOX-01 -> BOX-03", body["error"])
        self.assertEqual(self.api.calls, [])
        db = sqlite3.connect(self.base_dir / "searchRack.db")
        saved = json.loads(db.execute("SELECT amazon_state_json FROM fba_prep_sessions").fetchone()[0])
        db.close()
        self.assertEqual([(box["local_id"], box["packing_group_id"]) for box in saved["boxes"]],
                         [("BOX-01", "pg-new"), ("BOX-02", "pg-new"), ("BOX-03", "pg-two")])


if __name__ == "__main__":
    unittest.main()
