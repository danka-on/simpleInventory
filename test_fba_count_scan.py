import os
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from unittest.mock import Mock

os.environ.setdefault("DISABLE_BACKGROUND_SERVICES", "1")

import app as app_module


class FbaCountScanTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base_dir = Path(self.temp.name)
        db = sqlite3.connect(self.base_dir / "searchRack.db")
        app_module._ensure_fba_prep_tables(db.cursor())
        db.execute("""INSERT INTO fba_prep_sessions
                    (id, session_name, items_json, item_count, total_units, status, created_at, updated_at)
                    VALUES (1, 'Count', '[]', 0, 0, 'open', 'now', 'now')""")
        db.commit()
        db.close()
        app_module.app.testing = True
        self.client = app_module.app.test_client()

    def tearDown(self):
        self.temp.cleanup()

    def scan(self, barcode="225934704567"):
        return self.client.post("/api/fba-prep/sessions/1/count-scan", json={
            "barcode": barcode, "scan_token": "count:test:scanner:one",
            "client_id": "scanner-test-01", "operator_name": "Test",
        })

    def test_unmatched_item_is_rejected_and_not_counted(self):
        with (patch.object(app_module, "BASE_DIR", self.base_dir),
              patch.object(app_module, "_fba_local_amazon_listing", return_value={}),
              patch.object(app_module, "_amazon_spapi_context", side_effect=RuntimeError("offline"))):
            response = self.scan()
        self.assertEqual(response.status_code, 422)
        payload = response.get_json()
        self.assertTrue(payload["not_added"])
        self.assertEqual(payload["sort_result"]["kind"], "rejected")
        db = sqlite3.connect(self.base_dir / "searchRack.db")
        self.assertEqual(db.execute("SELECT total_units FROM fba_prep_sessions WHERE id=1").fetchone()[0], 0)
        self.assertEqual(db.execute("SELECT COUNT(*) FROM fba_count_scans").fetchone()[0], 0)
        db.close()

    def test_asin_fallback_is_counted_with_seller_sku(self):
        listing = {"asin": "B000U67JMQ", "seller_sku": "PL-8ALB-G8IZ", "title": "Tablecloth"}
        with (patch.object(app_module, "BASE_DIR", self.base_dir),
              patch.object(app_module, "_fba_local_amazon_listing", return_value={}),
              patch.object(app_module, "_amazon_spapi_context", return_value=(object(), "seller", "market", object())),
              patch.object(app_module, "_amazon_resolve_asin_from_upc", return_value="B000U67JMQ"),
              patch.object(app_module, "_fba_local_amazon_listing_by_asin", return_value=listing),
              patch.object(app_module, "_fba_listing_readiness", return_value={
                  "status": "ready", "fnsku": "X001234567", "product_type": "HOME"
              })):
            response = self.scan("047596190524")
        self.assertEqual(response.status_code, 200)
        item = response.get_json()["session"]["items"][0]
        self.assertEqual(item["seller_sku"], "PL-8ALB-G8IZ")
        self.assertEqual(item["quantity"], 1)
        self.assertEqual(item["fba_enablement_status"], "ready")
        self.assertEqual(item["amazon_fnsku"], "X001234567")
        self.assertEqual(response.get_json()["sort_result"]["kind"], "ready")

    def test_listing_without_fnsku_is_counted_for_enablement(self):
        listing = {"asin": "B07XV1JMRF", "seller_sku": "W2-NGC9-BAZS", "title": "Tablecloth"}
        with (patch.object(app_module, "BASE_DIR", self.base_dir),
              patch.object(app_module, "_fba_local_amazon_listing", return_value=listing),
              patch.object(app_module, "_fba_listing_readiness", return_value={
                  "status": "needs_enablement", "fnsku": "", "product_type": "HOME",
                  "fulfillment_channels": ["DEFAULT"], "error": "",
              })):
            response = self.scan("026865983135")
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["count_feedback"]["kind"], "needs_enablement")
        self.assertEqual(payload["sort_result"]["kind"], "ready")
        self.assertEqual(payload["sort_result"]["headline"], "COUNTED")
        self.assertEqual(payload["count_feedback"]["voice_message"], "")
        self.assertEqual(payload["session"]["items"][0]["fba_enablement_status"], "needs_enablement")
        db = sqlite3.connect(self.base_dir / "searchRack.db")
        self.assertEqual(db.execute("SELECT total_units FROM fba_prep_sessions WHERE id=1").fetchone()[0], 1)
        db.close()

    def test_blocking_amazon_listing_error_is_rejected_and_not_counted(self):
        listing = {"asin": "B07XV1JMRF", "seller_sku": "SKU-BLOCKED", "title": "Blocked item"}
        with (patch.object(app_module, "BASE_DIR", self.base_dir),
              patch.object(app_module, "_fba_local_amazon_listing", return_value=listing),
              patch.object(app_module, "_fba_listing_readiness", return_value={
                  "status": "failed", "error": "Missing required product attribute",
              })):
            response = self.scan("026865983135")
        self.assertEqual(response.status_code, 422)
        payload = response.get_json()
        self.assertTrue(payload["not_added"])
        self.assertEqual(payload["sort_result"]["kind"], "rejected")
        self.assertIn("Missing required product attribute", payload["sort_result"]["instruction"])
        db = sqlite3.connect(self.base_dir / "searchRack.db")
        self.assertEqual(db.execute("SELECT total_units FROM fba_prep_sessions WHERE id=1").fetchone()[0], 0)
        db.close()

    def test_missing_battery_and_dangerous_goods_answers_are_amber_and_counted(self):
        listing = {"asin": "B07XV1JMRF", "seller_sku": "SKU-SAFETY", "title": "Household item"}
        message = ("'Dangerous Goods Regulations' is required but missing. "
                   "(supplier_declared_dg_hz_regulation); 'Are batteries required?' is required "
                   "but missing. (batteries_required)")
        with (patch.object(app_module, "BASE_DIR", self.base_dir),
              patch.object(app_module, "_fba_local_amazon_listing", return_value=listing),
              patch.object(app_module, "_fba_listing_readiness", return_value={
                  "status": "needs_safety_info", "error": message, "product_type": "HOME",
              })):
            response = self.scan("026865983135")
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["sort_result"]["kind"], "ready")
        self.assertEqual(payload["sort_result"]["headline"], "COUNTED")
        item = payload["session"]["items"][0]
        self.assertEqual(item["fba_enablement_status"], "needs_safety_info")
        self.assertFalse(item["batteries_required"])

    def test_flat_safety_error_reported_as_failed_is_still_amber_and_counted(self):
        listing = {"asin": "B07XV1JMRF", "seller_sku": "SKU-TABLECLOTH", "title": "Tablecloth"}
        message = ("'Dangerous Goods Regulations' is required but missing. "
                   "(supplier_declared_dg_hz_regulation); 'Are batteries required?' is required "
                   "but missing. (batteries_required)")
        with (patch.object(app_module, "BASE_DIR", self.base_dir),
              patch.object(app_module, "_fba_local_amazon_listing", return_value=listing),
              patch.object(app_module, "_fba_listing_readiness", return_value={
                  "status": "failed", "error": message, "product_type": "HOME",
              })):
            response = self.scan("047596044292")
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["sort_result"]["kind"], "ready")
        self.assertEqual(payload["sort_result"]["headline"], "COUNTED")
        self.assertEqual(payload["session"]["items"][0]["fba_enablement_status"], "needs_safety_info")
        self.assertFalse(payload["session"]["items"][0]["batteries_required"])

    def test_saved_safety_rejection_and_string_false_are_migrated_to_amber(self):
        message = ("'Dangerous Goods Regulations' is required but missing. "
                   "(supplier_declared_dg_hz_regulation); 'Are batteries required?' is required "
                   "but missing. (batteries_required)")
        db = sqlite3.connect(self.base_dir / "searchRack.db")
        db.execute("UPDATE fba_prep_sessions SET items_json=?, item_count=1, total_units=1 WHERE id=1", (
            json.dumps([{
                "barcode": "047596044292", "seller_sku": "SKU-TABLECLOTH", "quantity": 1,
                "fba_enablement_status": "failed", "batteries_required": "false",
                "fba_enablement_error": message,
            }]),
        ))
        db.commit()
        db.close()
        with patch.object(app_module, "BASE_DIR", self.base_dir):
            response = self.client.post("/api/fba-prep/sessions/1/validate-inbound")
        self.assertEqual(response.status_code, 200)
        item = response.get_json()["session"]["items"][0]
        self.assertEqual(item["fba_enablement_status"], "needs_safety_info")
        self.assertFalse(item["batteries_required"])

    def test_saved_legacy_rejections_are_migrated_to_needs_enablement(self):
        db = sqlite3.connect(self.base_dir / "searchRack.db")
        db.execute("UPDATE fba_prep_sessions SET items_json=?, item_count=2, total_units=2 WHERE id=1", (
            '[{"barcode":"047596044292","seller_sku":"J2-QSB1-7GVB","quantity":1},'
            '{"barcode":"026865983135","seller_sku":"W2-NGC9-BAZS","quantity":1}]',
        ))
        db.commit()
        db.close()
        with patch.object(app_module, "BASE_DIR", self.base_dir):
            response = self.client.post("/api/fba-prep/sessions/1/validate-inbound")
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["invalid_items"], [])
        self.assertEqual(payload["enablement"]["needs_enablement"], 2)
        self.assertTrue(all(
            item["fba_enablement_status"] == "needs_enablement"
            for item in payload["session"]["items"]
        ))

    def test_enable_fba_requires_explicit_confirmation(self):
        with patch.object(app_module, "BASE_DIR", self.base_dir):
            response = self.client.post("/api/fba-prep/sessions/1/enable-fba", json={})
        self.assertEqual(response.status_code, 400)
        self.assertIn("inventory-aware", response.get_json()["error"])

    def test_enable_fba_uses_existing_sku_and_removes_default_channel(self):
        db = sqlite3.connect(self.base_dir / "searchRack.db")
        db.execute("UPDATE fba_prep_sessions SET items_json=?, item_count=1, total_units=1 WHERE id=1", (
            '[{"barcode":"026865983135","seller_sku":"W2-NGC9-BAZS","quantity":1,'
            '"fba_enablement_status":"needs_enablement"}]',
        ))
        db.commit()
        db.close()
        listings = Mock()
        listings.get_listings_item.return_value = Mock(errors=None, payload={
            "summaries": [{"productType": "HOME", "status": ["BUYABLE", "DISCOVERABLE"]}],
            "issues": [],
            "fulfillmentAvailability": [{"fulfillmentChannelCode": "DEFAULT", "quantity": 1}],
        })
        listings.patch_listings_item.return_value = Mock(errors=None, payload={"status": "ACCEPTED", "issues": []})
        with (patch.object(app_module, "BASE_DIR", self.base_dir),
              patch.object(app_module, "_fba_listings_client", return_value=(listings, "SELLER", "ATVPDKIKX0DER"))):
            response = self.client.post("/api/fba-prep/sessions/1/enable-fba", json={"confirm": True})
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["enablement"]["enabling"], 1)
        body = listings.patch_listings_item.call_args.kwargs["body"]
        self.assertEqual(body["productType"], "HOME")
        self.assertEqual(body["patches"], [
            {"op": "add", "path": "/attributes/batteries_required",
             "value": [{"value": False, "marketplace_id": "ATVPDKIKX0DER"}]},
            {"op": "add", "path": "/attributes/supplier_declared_dg_hz_regulation",
             "value": [{"value": "not_applicable", "marketplace_id": "ATVPDKIKX0DER"}]},
            {"op": "add", "path": "/attributes/fulfillment_availability",
             "value": [{"fulfillment_channel_code": "AMAZON_NA"}]},
            {"op": "delete", "path": "/attributes/fulfillment_availability",
             "value": [{"fulfillment_channel_code": "DEFAULT"}]},
        ])

    def test_accepted_combined_fba_patch_does_not_loop_on_stale_prerequisite_issue(self):
        listings = Mock()
        stale_issue = {
            "severity": "ERROR",
            "message": "supplier_declared_dg_hz_regulation is required",
            "attributeNames": ["supplier_declared_dg_hz_regulation"],
        }
        listings.patch_listings_item.return_value = Mock(errors=None, payload={
            "status": "ACCEPTED", "issues": [stale_issue],
        })
        result = app_module._fba_patch_listing_to_fba(
            "SKU-LOOP",
            {"status": "needs_enablement", "product_type": "TABLE_RUNNER",
             "fulfillment_channels": ["DEFAULT"]},
            client=(listings, "SELLER", "ATVPDKIKX0DER"),
        )
        self.assertEqual(result["status"], "enabling")
        self.assertEqual(result["error"], "")
        paths = [patch_row["path"] for patch_row in
                 listings.patch_listings_item.call_args.kwargs["body"]["patches"]]
        self.assertIn("/attributes/supplier_declared_dg_hz_regulation", paths)
        self.assertIn("/attributes/fulfillment_availability", paths)

    def test_enable_fba_preserves_fbm_and_creates_companion_when_local_stock_remains(self):
        db = sqlite3.connect(self.base_dir / "searchRack.db")
        db.execute("""CREATE TABLE SEARCHRACK (
                    ID INTEGER PRIMARY KEY, BARCODE TEXT, ITEM_POSITION TEXT, QUANTITY INTEGER)""")
        db.execute("INSERT INTO SEARCHRACK (BARCODE, ITEM_POSITION, QUANTITY) VALUES (?, ?, ?)",
                   ("026865983135", "OR1S1B1", 3))
        db.execute("UPDATE fba_prep_sessions SET items_json=?, item_count=1, total_units=1 WHERE id=1", (
            '[{"barcode":"026865983135","seller_sku":"FBM-SKU","quantity":1,'
            '"fba_enablement_status":"needs_enablement"}]',
        ))
        db.commit()
        db.close()
        listings_client = (Mock(), "SELLER", "ATVPDKIKX0DER")
        readiness = {
            "status": "needs_enablement", "product_type": "HOME",
            "fulfillment_channels": ["DEFAULT"], "error": "",
        }
        created = {
            "status": "enabling", "product_type": "HOME", "error": "",
            "source_sku": "FBM-SKU", "target_sku": "FBM-SKU-FBA",
            "strategy": "separate_fba_sku",
        }
        with (patch.object(app_module, "BASE_DIR", self.base_dir),
              patch.object(app_module, "_fba_listings_client", return_value=listings_client),
              patch.object(app_module, "_fba_listing_readiness", return_value=readiness),
              patch.object(app_module, "_fba_create_separate_fba_offer", return_value=created) as create_offer,
              patch.object(app_module, "_fba_patch_listing_to_fba") as convert_offer):
            response = self.client.post("/api/fba-prep/sessions/1/enable-fba", json={"confirm": True})
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["offer_strategies"], {
            "convert_existing": 0, "separate_fba_sku": 1,
        })
        item = payload["session"]["items"][0]
        self.assertEqual(item["fbm_seller_sku"], "FBM-SKU")
        self.assertEqual(item["seller_sku"], "FBM-SKU-FBA")
        self.assertEqual(item["local_inventory_before_fba"], 3)
        self.assertEqual(item["planned_fba_quantity"], 1)
        self.assertEqual(item["local_inventory_remaining_after_fba"], 2)
        self.assertEqual(item["fba_offer_strategy"], "separate_fba_sku")
        create_offer.assert_called_once_with(
            "FBM-SKU", "FBM-SKU-FBA", readiness, client=listings_client,
        )
        convert_offer.assert_not_called()

    def test_inventory_equal_to_fba_count_converts_existing_sku(self):
        db = sqlite3.connect(self.base_dir / "searchRack.db")
        db.row_factory = sqlite3.Row
        db.execute("""CREATE TABLE SEARCHRACK (
                    ID INTEGER PRIMARY KEY, BARCODE TEXT, ITEM_POSITION TEXT, QUANTITY INTEGER)""")
        db.execute("INSERT INTO SEARCHRACK (BARCODE, ITEM_POSITION, QUANTITY) VALUES (?, ?, ?)",
                   ("026865983135", "OR1S1B1", 1))
        items = [{"barcode": "026865983135", "seller_sku": "ONLY-SKU", "quantity": 1}]
        try:
            app_module._fba_apply_inventory_offer_strategy(db.cursor(), items)
        finally:
            db.close()
        self.assertEqual(items[0]["local_inventory_remaining_after_fba"], 0)
        self.assertEqual(items[0]["fba_offer_strategy"], "convert_existing")
        self.assertEqual(items[0]["proposed_fba_seller_sku"], "")

    def test_companion_fba_offer_clones_sales_terms_without_changing_source(self):
        listings = Mock()

        def get_item(_seller_id, sku, **_kwargs):
            if sku == "FBM-SKU":
                return Mock(errors=None, payload={
                    "summaries": [{
                        "asin": "B000TEST01", "productType": "TABLECLOTH",
                        "conditionType": "new_new",
                    }],
                    "attributes": {
                        "condition_type": [{
                            "value": "new_new", "marketplace_id": "ATVPDKIKX0DER",
                        }],
                        "purchasable_offer": [{
                            "currency": "USD", "our_price": [{
                                "schedule": [{"value_with_tax": 49.99}],
                            }], "marketplace_id": "ATVPDKIKX0DER",
                        }],
                        "merchant_shipping_group": [{
                            "value": "legacy-fbm-template", "marketplace_id": "ATVPDKIKX0DER",
                        }],
                    },
                })
            raise RuntimeError("404 Not Found")

        listings.get_listings_item.side_effect = get_item
        listings.put_listings_item.return_value = Mock(
            errors=None, payload={"status": "ACCEPTED", "issues": []},
        )
        client = (listings, "SELLER", "ATVPDKIKX0DER")
        result = app_module._fba_create_separate_fba_offer(
            "FBM-SKU", "FBM-SKU-FBA",
            {"status": "needs_enablement", "product_type": "TABLECLOTH"},
            client=client,
        )
        self.assertEqual(result["status"], "enabling")
        self.assertEqual(result["target_sku"], "FBM-SKU-FBA")
        listings.patch_listings_item.assert_not_called()
        args = listings.put_listings_item.call_args.args
        self.assertEqual(args[:2], ("SELLER", "FBM-SKU-FBA"))
        body = listings.put_listings_item.call_args.kwargs["body"]
        self.assertEqual(body["productType"], "TABLECLOTH")
        self.assertEqual(body["requirements"], "LISTING_OFFER_ONLY")
        attrs = body["attributes"]
        self.assertEqual(attrs["merchant_suggested_asin"][0]["value"], "B000TEST01")
        self.assertEqual(attrs["purchasable_offer"][0]["our_price"][0]["schedule"][0]["value_with_tax"], 49.99)
        self.assertNotIn("merchant_shipping_group", attrs)
        self.assertEqual(attrs["fulfillment_availability"], [{
            "fulfillment_channel_code": "AMAZON_NA",
        }])

    def test_enable_fba_honors_requested_sku_batch(self):
        items = [{
            "barcode": f"02686598313{index}", "seller_sku": f"SKU-{index}",
            "quantity": 1, "fba_enablement_status": "needs_enablement",
        } for index in range(1, 4)]
        db = sqlite3.connect(self.base_dir / "searchRack.db")
        db.execute("UPDATE fba_prep_sessions SET items_json=?, item_count=3, total_units=3 WHERE id=1",
                   (json.dumps(items),))
        db.commit()
        db.close()
        listings_client = (Mock(), "SELLER", "ATVPDKIKX0DER")

        def readiness(sku, **_kwargs):
            return {"status": "needs_enablement", "seller_sku": sku,
                    "product_type": "HOME", "fulfillment_channels": ["DEFAULT"], "error": ""}

        with (patch.object(app_module, "BASE_DIR", self.base_dir),
              patch.object(app_module, "_fba_listings_client", return_value=listings_client),
              patch.object(app_module, "_fba_listing_readiness", side_effect=readiness),
              patch.object(app_module, "_fba_patch_listing_to_fba",
                           return_value={"status": "enabling", "issues": []}) as enable_patch):
            response = self.client.post("/api/fba-prep/sessions/1/enable-fba", json={
                "confirm": True, "seller_skus": ["SKU-2"],
            })
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["processed_skus"], ["SKU-2"])
        statuses = {item["seller_sku"]: item["fba_enablement_status"]
                    for item in payload["session"]["items"]}
        self.assertEqual(statuses["SKU-2"], "enabling")
        self.assertEqual(statuses["SKU-1"], "needs_enablement")
        self.assertEqual(statuses["SKU-3"], "needs_enablement")
        self.assertEqual(enable_patch.call_count, 1)

    def test_enable_fba_caps_large_legacy_request_at_six_skus(self):
        items = [{
            "barcode": f"0475960442{index:02d}", "seller_sku": f"BULK-{index}",
            "quantity": 1, "fba_enablement_status": "needs_enablement",
        } for index in range(1, 8)]
        db = sqlite3.connect(self.base_dir / "searchRack.db")
        db.execute("UPDATE fba_prep_sessions SET items_json=?, item_count=7, total_units=7 WHERE id=1",
                   (json.dumps(items),))
        db.commit()
        db.close()
        listings_client = (Mock(), "SELLER", "ATVPDKIKX0DER")

        def readiness(sku, **_kwargs):
            return {"status": "needs_enablement", "seller_sku": sku,
                    "product_type": "HOME", "fulfillment_channels": ["DEFAULT"], "error": ""}

        with (patch.object(app_module, "BASE_DIR", self.base_dir),
              patch.object(app_module, "_fba_listings_client", return_value=listings_client),
              patch.object(app_module, "_fba_listing_readiness", side_effect=readiness),
              patch.object(app_module, "_fba_patch_listing_to_fba",
                           return_value={"status": "enabling", "issues": []}),
              patch.object(app_module.time, "sleep")):
            response = self.client.post("/api/fba-prep/sessions/1/enable-fba", json={"confirm": True})
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(len(payload["processed_skus"]), 6)
        self.assertEqual(payload["enablement"]["enabling"], 6)
        self.assertEqual(payload["enablement"]["needs_enablement"], 1)

    def test_confirmed_saved_safety_row_advances_to_enablement_on_reload(self):
        message = ("'Dangerous Goods Regulations' is required but missing. "
                   "(supplier_declared_dg_hz_regulation)")
        db = sqlite3.connect(self.base_dir / "searchRack.db")
        db.execute("UPDATE fba_prep_sessions SET items_json=?, item_count=1, total_units=1 WHERE id=1", (
            json.dumps([{
                "barcode": "047596044292", "seller_sku": "SKU-TABLECLOTH", "quantity": 1,
                "fba_enablement_status": "needs_safety_info", "batteries_required": False,
                "dg_not_applicable_confirmed": True, "fba_enablement_error": message,
            }]),
        ))
        db.commit()
        db.close()
        with patch.object(app_module, "BASE_DIR", self.base_dir):
            response = self.client.post("/api/fba-prep/sessions/1/validate-inbound")
        self.assertEqual(response.status_code, 200)
        item = response.get_json()["session"]["items"][0]
        self.assertEqual(item["fba_enablement_status"], "needs_enablement")
        self.assertEqual(item["fba_enablement_error"], "")

    def test_confirm_not_dangerous_goods_saves_default_no_battery_answers(self):
        db = sqlite3.connect(self.base_dir / "searchRack.db")
        db.execute("UPDATE fba_prep_sessions SET items_json=?, item_count=1, total_units=1 WHERE id=1", (
            '[{"barcode":"026865983135","seller_sku":"SKU-SAFETY","quantity":1,'
            '"fba_enablement_status":"needs_safety_info","batteries_required":false}]',
        ))
        db.commit()
        db.close()
        safety_issues = [{
            "severity": "ERROR",
            "message": "supplier_declared_dg_hz_regulation and batteries_required are required",
            "attributeNames": ["supplier_declared_dg_hz_regulation", "batteries_required"],
        }]
        listings = Mock()
        listings.get_listings_item.return_value = Mock(errors=None, payload={
            "summaries": [{"productType": "HOME"}], "issues": safety_issues,
            "fulfillmentAvailability": [{"fulfillmentChannelCode": "DEFAULT"}],
        })
        # Amazon can accept the patch while echoing the issue set from before
        # the asynchronous listing update. Accepted safety-only issues must not
        # leave the local session stuck on the same step.
        listings.patch_listings_item.return_value = Mock(errors=None, payload={
            "status": "ACCEPTED", "issues": safety_issues,
        })
        with (patch.object(app_module, "BASE_DIR", self.base_dir),
              patch.object(app_module, "_fba_listings_client", return_value=(listings, "SELLER", "ATVPDKIKX0DER"))):
            response = self.client.post("/api/fba-prep/sessions/1/resolve-fba-safety", json={
                "confirm_not_dangerous_goods": True, "seller_skus": ["SKU-SAFETY"],
            })
        self.assertEqual(response.status_code, 200)
        item = response.get_json()["session"]["items"][0]
        self.assertEqual(item["fba_enablement_status"], "needs_enablement")
        self.assertFalse(item["batteries_required"])
        self.assertTrue(item["dg_not_applicable_confirmed"])
        body = listings.patch_listings_item.call_args.kwargs["body"]
        self.assertEqual(body["patches"], [
            {"op": "add", "path": "/attributes/batteries_required",
             "value": [{"value": False, "marketplace_id": "ATVPDKIKX0DER"}]},
            {"op": "add", "path": "/attributes/supplier_declared_dg_hz_regulation",
             "value": [{"value": "not_applicable", "marketplace_id": "ATVPDKIKX0DER"}]},
        ])

    def test_battery_item_is_excluded_from_automatic_no_battery_declaration(self):
        db = sqlite3.connect(self.base_dir / "searchRack.db")
        db.execute("UPDATE fba_prep_sessions SET items_json=?, item_count=1, total_units=1 WHERE id=1", (
            '[{"barcode":"026865983135","seller_sku":"SKU-BATTERY","quantity":1,'
            '"fba_enablement_status":"needs_safety_info","batteries_required":true}]',
        ))
        db.commit()
        db.close()
        with (patch.object(app_module, "BASE_DIR", self.base_dir),
              patch.object(app_module, "_fba_listings_client") as listings_client):
            response = self.client.post("/api/fba-prep/sessions/1/resolve-fba-safety", json={
                "confirm_not_dangerous_goods": True, "seller_skus": ["SKU-BATTERY"],
            })
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["skipped_battery_skus"], ["SKU-BATTERY"])
        self.assertTrue(payload["session"]["items"][0]["batteries_required"])
        listings_client.assert_not_called()

    def test_enablement_status_saves_assigned_fnsku(self):
        db = sqlite3.connect(self.base_dir / "searchRack.db")
        db.execute("UPDATE fba_prep_sessions SET items_json=?, item_count=1, total_units=1 WHERE id=1", (
            '[{"barcode":"026865983135","seller_sku":"W2-NGC9-BAZS","quantity":1,'
            '"fba_enablement_status":"enabling"}]',
        ))
        db.commit()
        db.close()
        listings = Mock()
        listings.get_listings_item.return_value = Mock(errors=None, payload={
            "summaries": [{"productType": "HOME", "fnSku": "X0099ABC12"}],
            "issues": [], "fulfillmentAvailability": [{"fulfillmentChannelCode": "AMAZON_NA"}],
        })
        with (patch.object(app_module, "BASE_DIR", self.base_dir),
              patch.object(app_module, "_fba_listings_client", return_value=(listings, "SELLER", "ATVPDKIKX0DER"))):
            response = self.client.post("/api/fba-prep/sessions/1/enable-fba-status")
        self.assertEqual(response.status_code, 200)
        item = response.get_json()["session"]["items"][0]
        self.assertEqual(item["fba_enablement_status"], "ready")
        self.assertEqual(item["amazon_fnsku"], "X0099ABC12")
        self.assertEqual(item["fnsku"], "X0099ABC12")

    def test_enablement_status_retries_missing_fba_channel_once(self):
        db = sqlite3.connect(self.base_dir / "searchRack.db")
        db.execute("UPDATE fba_prep_sessions SET items_json=?, item_count=1, total_units=1 WHERE id=1", (
            json.dumps([{
                "barcode": "026865983135", "seller_sku": "FBM-SKU-FBA",
                "fbm_seller_sku": "FBM-SKU", "fba_offer_strategy": "separate_fba_sku",
                "quantity": 1, "fba_enablement_status": "enabling",
                "fba_activation_started_at": "2026-08-29T16:40:00",
            }]),
        ))
        db.commit()
        db.close()
        listings_client = (Mock(), "SELLER", "ATVPDKIKX0DER")
        readiness = {
            "status": "needs_enablement", "product_type": "HOME",
            "fulfillment_channels": [], "error": "",
        }
        with (patch.object(app_module, "BASE_DIR", self.base_dir),
              patch.object(app_module, "_fba_listings_client", return_value=listings_client),
              patch.object(app_module, "_fba_listing_readiness", return_value=readiness),
              patch.object(app_module, "_fba_patch_listing_to_fba",
                           return_value={"status": "enabling", "issues": []}) as retry_patch):
            response = self.client.post("/api/fba-prep/sessions/1/enable-fba-status")
        self.assertEqual(response.status_code, 200)
        item = response.get_json()["session"]["items"][0]
        self.assertEqual(item["fba_enablement_status"], "enabling")
        self.assertEqual(item["fba_activation_retry_count"], 1)
        self.assertTrue(item["fba_activation_retry_at"])
        retry_patch.assert_called_once_with("FBM-SKU-FBA", readiness, client=listings_client)

    def test_enablement_status_stops_waiting_after_retry_timeout(self):
        db = sqlite3.connect(self.base_dir / "searchRack.db")
        db.execute("UPDATE fba_prep_sessions SET items_json=?, item_count=1, total_units=1 WHERE id=1", (
            json.dumps([{
                "barcode": "026865983135", "seller_sku": "FBM-SKU-FBA",
                "fbm_seller_sku": "FBM-SKU", "fba_offer_strategy": "separate_fba_sku",
                "quantity": 1, "fba_enablement_status": "enabling",
                "fba_activation_started_at": "2020-01-01T00:00:00",
                "fba_activation_retry_count": 1,
            }]),
        ))
        db.commit()
        db.close()
        listings_client = (Mock(), "SELLER", "ATVPDKIKX0DER")
        readiness = {
            "status": "needs_enablement", "product_type": "HOME",
            "fulfillment_channels": [], "error": "",
        }
        with (patch.object(app_module, "BASE_DIR", self.base_dir),
              patch.object(app_module, "_fba_listings_client", return_value=listings_client),
              patch.object(app_module, "_fba_listing_readiness", return_value=readiness),
              patch.object(app_module, "_fba_patch_listing_to_fba") as retry_patch):
            response = self.client.post("/api/fba-prep/sessions/1/enable-fba-status")
        self.assertEqual(response.status_code, 200)
        item = response.get_json()["session"]["items"][0]
        self.assertEqual(item["fba_enablement_status"], "needs_enablement")
        self.assertIn("Tap Set up again", item["fba_enablement_error"])
        retry_patch.assert_not_called()


if __name__ == "__main__":
    unittest.main()
