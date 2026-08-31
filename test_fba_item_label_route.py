import os
import unittest
from unittest.mock import patch

os.environ.setdefault("DISABLE_BACKGROUND_SERVICES", "1")

import app as app_module


SESSION = {
    "items": [{
        "seller_sku": "SKU-1",
        "title": "Test item",
        "condition": "New",
        "fba": {"barcode_guidance": {
            "status": "amazon_barcode", "checked": True,
            "source": "amazon_sp_api", "instruction": "RequiresFNSKULabel",
            "identifier_type": "seller_sku", "identifier": "SKU-1",
        }},
    }],
}
LIVE_GUIDANCE = {
    "status": "amazon_barcode", "checked": True,
    "source": "amazon_sp_api", "instruction": "RequiresFNSKULabel",
    "identifier_type": "seller_sku", "identifier": "SKU-1",
}
WORKFLOW = {
    "stage": "packing",
    "inbound_plan_id": "wf-test-plan",
    "plan_items": [{
        "msku": "SKU-1",
        "fnsku": "X001234567",
        "quantity": 1,
        "labelOwner": "SELLER",
    }],
}


class FbaItemLabelRouteTest(unittest.TestCase):
    def setUp(self):
        app_module.app.testing = True
        self.client = app_module.app.test_client()

    def test_prints_authoritative_fnsku_on_item_prep_printer(self):
        with (
            patch.object(app_module, "_fba_amazon_session", return_value=({}, SESSION, dict(WORKFLOW))),
            patch.object(app_module, "_fba_amazon_barcode_guidance", return_value=dict(LIVE_GUIDANCE)) as guidance,
            patch.object(app_module, "_fba_save_amazon_state"),
            patch.object(app_module.printer_manager, "get_config_snapshot", return_value={
                "print_method": "escpos", "label_height": 12, "printer_name": "Brother",
            }),
            patch.object(app_module.printer_manager, "get_connection_status", return_value={
                "can_print": True, "display_name": "Brother QL-820NWBc",
            }),
            patch.object(app_module.printer_manager, "print_barcode", return_value=True) as print_barcode,
        ):
            response = self.client.post(
                "/api/fba-prep/sessions/1/amazon/print-item-label",
                json={"msku": "SKU-1", "fnsku": "CLIENT-VALUE-IS-IGNORED"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.get_json()["printed"])
        args, kwargs = print_barcode.call_args
        self.assertEqual(args[0], "X001234567")
        self.assertEqual(args[1], "New | Test item")
        self.assertEqual(kwargs["quantity"], 1)
        layout = kwargs["layout_override"]
        self.assertGreaterEqual(layout["label_height"], 26)
        self.assertEqual(layout["label_barcode_height_mm"], 10)
        self.assertEqual(layout["label_padding_x_mm"], 6.35)
        self.assertEqual(layout["label_padding_y_mm"], 3.175)
        self.assertTrue(layout["label_barcode_first"])
        self.assertTrue(layout["label_barcode_protected"])
        guidance.assert_called_once_with(
            {"seller_sku": "SKU-1", "asin": ""}, force_refresh=True
        )

    def test_browser_print_mode_is_rejected_before_printing(self):
        with (
            patch.object(app_module, "_fba_amazon_session", return_value=({}, SESSION, dict(WORKFLOW))),
            patch.object(app_module, "_fba_amazon_barcode_guidance", return_value=dict(LIVE_GUIDANCE)),
            patch.object(app_module.printer_manager, "get_config_snapshot", return_value={
                "print_method": "browser", "label_height": 30,
            }),
            patch.object(app_module.printer_manager, "print_barcode", return_value=True) as print_barcode,
        ):
            response = self.client.post(
                "/api/fba-prep/sessions/1/amazon/print-item-label",
                json={"msku": "SKU-1"},
            )

        self.assertEqual(response.status_code, 409)
        self.assertIn("direct Item Prep printer mode", response.get_json()["error"])
        print_barcode.assert_not_called()

    def test_live_amazon_check_blocks_a_stale_required_state(self):
        manufacturer_guidance = {
            "status": "manufacturer_barcode", "checked": True,
            "source": "amazon_sp_api", "instruction": "CanUseOriginalBarcode",
            "identifier_type": "seller_sku", "identifier": "SKU-1",
        }
        with (
            patch.object(app_module, "_fba_amazon_session", return_value=({}, SESSION, dict(WORKFLOW))),
            patch.object(app_module, "_fba_amazon_barcode_guidance", return_value=manufacturer_guidance),
            patch.object(app_module.printer_manager, "print_barcode", return_value=True) as print_barcode,
        ):
            response = self.client.post(
                "/api/fba-prep/sessions/1/amazon/print-item-label",
                json={"msku": "SKU-1"},
            )

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.get_json()["barcode_guidance"]["status"], "manufacturer_barcode")
        print_barcode.assert_not_called()

    def test_inbound_plan_label_instruction_skips_redundant_live_lookup(self):
        workflow = dict(WORKFLOW)
        workflow["plan_items"] = [{
            "msku": "SKU-1", "fnsku": "X001234567", "quantity": 1,
            "labelOwner": "SELLER",
            "prepInstructions": [{"prepType": "ITEM_LABELING", "prepOwner": "SELLER"}],
        }]
        with (
            patch.object(app_module, "_fba_amazon_session", return_value=({}, SESSION, workflow)),
            patch.object(app_module, "_fba_amazon_barcode_guidance") as guidance,
            patch.object(app_module, "_fba_save_amazon_state"),
            patch.object(app_module.printer_manager, "get_config_snapshot", return_value={
                "print_method": "escpos", "label_height": 30,
            }),
            patch.object(app_module.printer_manager, "get_connection_status", return_value={
                "can_print": True, "display_name": "Item Prep printer",
            }),
            patch.object(app_module.printer_manager, "print_barcode", return_value=True),
        ):
            response = self.client.post(
                "/api/fba-prep/sessions/1/amazon/print-item-label", json={"msku": "SKU-1"}
            )

        self.assertEqual(response.status_code, 200)
        guidance.assert_not_called()


if __name__ == "__main__":
    unittest.main()
