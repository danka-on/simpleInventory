import json
import os
import unittest

os.environ.setdefault("DISABLE_BACKGROUND_SERVICES", "1")

import app as app_module


def session_row(*, session_id=7, barcode="025398232475", msku="SKU-1", fnsku="X001234567"):
    return {
        "id": session_id,
        "session_name": "Friday FBA plan",
        "batch_name": "Friday batch",
        "status": "open",
        "working_locations_json": "[]",
        "items_json": json.dumps([{
            "barcode": barcode,
            "title": "Test planned item",
            "image": "https://example.test/item.jpg",
            "seller_sku": msku,
        }]),
        "amazon_inbound_plan_id": "wf-plan-1",
        "amazon_stage": "packing",
        "amazon_state_json": json.dumps({
            "stage": "packing",
            "inbound_plan_id": "wf-plan-1",
            "plan_items": [{"msku": msku, "fnsku": fnsku, "quantity": 1}],
        }),
    }


class FbaLabelScannerTest(unittest.TestCase):
    def setUp(self):
        app_module.app.testing = True
        self.client = app_module.app.test_client()

    def test_resolves_scanned_barcode_to_open_plan_fnsku(self):
        match = app_module._fba_planned_label_match(
            "025398232475", [session_row()]
        )
        self.assertTrue(match["printable"])
        self.assertEqual(match["session_id"], 7)
        self.assertEqual(match["msku"], "SKU-1")
        self.assertEqual(match["fnsku"], "X001234567")

    def test_returns_waiting_match_when_amazon_has_not_assigned_fnsku(self):
        match = app_module._fba_planned_label_match(
            "025398232475", [session_row(fnsku="")]
        )
        self.assertFalse(match["printable"])
        self.assertEqual(match["session_id"], 7)

    def test_uses_newest_printable_plan_when_newest_match_is_not_ready(self):
        match = app_module._fba_planned_label_match(
            "025398232475",
            [session_row(session_id=9, fnsku=""), session_row(session_id=7)],
        )
        self.assertTrue(match["printable"])
        self.assertEqual(match["session_id"], 7)

    def test_label_scanner_page_renders(self):
        response = self.client.get("/fba-labels")
        self.assertEqual(response.status_code, 200)
        body = response.get_data(as_text=True)
        self.assertIn("Amazon Label Scanner", body)
        self.assertIn("Auto print is always on", body)

    def test_resolve_requires_a_barcode(self):
        response = self.client.get("/api/fba-labels/resolve")
        self.assertEqual(response.status_code, 400)
        self.assertIn("Scan a barcode", response.get_json()["error"])


if __name__ == "__main__":
    unittest.main()
