import os
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from unittest.mock import Mock

os.environ.setdefault("DISABLE_BACKGROUND_SERVICES", "1")

from sweetshelves.bootstrap import app  # Initialize routes once for Flask client tests.
from sweetshelves import amazon_catalog as ss_amazon_catalog
from sweetshelves import config as ss_config
from sweetshelves import fba_inventory as ss_fba_inventory
from sweetshelves import fba_readiness as ss_fba_readiness
from sweetshelves import fba_schema as ss_fba_schema
from sweetshelves import runtime as ss_runtime
import time


class FbaCountScanTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base_dir = Path(self.temp.name)
        db = sqlite3.connect(self.base_dir / "searchRack.db")
        ss_fba_schema._ensure_fba_prep_tables(db.cursor())
        db.execute("""INSERT INTO fba_prep_sessions
                    (id, session_name, items_json, item_count, total_units, status, created_at, updated_at)
                    VALUES (1, 'Count', '[]', 0, 0, 'open', 'now', 'now')""")
        db.commit()
        db.close()
        ss_runtime.app.testing = True
        self.client = ss_runtime.app.test_client()

    def tearDown(self):
        self.temp.cleanup()

    def scan(self, barcode="225934704567"):
        return self.client.post("/api/fba-prep/sessions/1/count-scan", json={
            "barcode": barcode, "scan_token": "count:test:scanner:one",
            "client_id": "scanner-test-01", "operator_name": "Test",
        })

    def test_unmatched_item_is_rejected_and_not_counted(self):
        with (patch.object(ss_config, "BASE_DIR", self.base_dir),
              patch.object(ss_fba_inventory, "_fba_local_amazon_listing", return_value={}),
              patch.object(ss_amazon_catalog, "_amazon_spapi_context", return_value=(None, "seller", "market", None)),
              patch.object(ss_amazon_catalog, "_amazon_catalog_product_from_barcode", return_value={})):
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
        with (patch.object(ss_config, "BASE_DIR", self.base_dir),
              patch.object(ss_fba_inventory, "_fba_local_amazon_listing", return_value={}),
              patch.object(ss_amazon_catalog, "_amazon_spapi_context", return_value=(object(), "seller", "market", object())),
              patch.object(ss_amazon_catalog, "_amazon_catalog_product_from_barcode", return_value={"asin": "B000U67JMQ", "title": "Tablecloth"}),
              patch.object(ss_fba_inventory, "_fba_local_amazon_listing_by_asin", return_value=listing),
              patch.object(ss_fba_readiness, "_fba_listing_readiness", return_value={
                  "status": "ready", "fnsku": "X001234567", "product_type": "HOME"
              })):
            response = self.scan("047596190524")
        self.assertEqual(response.status_code, 200)
        item = response.get_json()["session"]["items"][0]
        self.assertEqual(item["seller_sku"], "PL-8ALB-G8IZ")
        self.assertEqual(item["quantity"], 1)
        self.assertEqual(item["fba_enablement_status"], "needs_enablement")
        self.assertEqual(response.get_json()["sort_result"]["kind"], "ready")

    def test_catalog_product_without_seller_offer_counts_and_survives_reload(self):
        with (patch.object(ss_config, "BASE_DIR", self.base_dir),
              patch.object(ss_fba_inventory, "_fba_local_amazon_listing", return_value={}),
              patch.object(ss_fba_inventory, "_fba_local_amazon_listing_by_asin", return_value={}),
              patch.object(ss_amazon_catalog, "_amazon_spapi_context", return_value=(None, "seller", "market", None)),
              patch("sp_api.api.CatalogItems") as catalog,
              patch.object(ss_fba_readiness, "_fba_listing_readiness") as readiness):
            catalog.return_value.search_catalog_items.return_value = Mock(errors=None, payload={
                "items": [{"asin": "B000U67JMQ", "summaries": [
                    {"marketplaceId": "market", "itemName": "Catalog-only tablecloth"}]}]})
            response = self.scan("047596190524")
            self.assertEqual(response.status_code, 200)
            item = response.get_json()["session"]["items"][0]
            self.assertEqual(item["seller_sku"], "")
            self.assertEqual(item["asin"], "B000U67JMQ")
            self.assertEqual(item["title"], "Catalog-only tablecloth")
            self.assertEqual(item["quantity"], 1)
            self.assertEqual(response.get_json()["sort_result"]["headline"], "COUNTED")
            readiness.assert_not_called()
            # Retrying the same scan token must not count the unit twice.
            duplicate = self.scan("047596190524")
            self.assertTrue(duplicate.get_json()["duplicate"])
            self.assertEqual(duplicate.get_json()["session"]["total_units"], 1)
            reloaded = self.client.get("/api/fba-prep/sessions/1")
            self.assertEqual(reloaded.status_code, 200)
            self.assertEqual(reloaded.get_json()["session"]["items"][0]["asin"], "B000U67JMQ")

    def test_catalog_lookup_restores_omitted_upc_zero(self):
        with patch("sp_api.api.CatalogItems") as catalog:
            catalog.return_value.search_catalog_items.return_value = Mock(errors=None, payload={"items": []})
            result = ss_amazon_catalog._amazon_catalog_product_from_barcode(None, None, "market", "47596190524", strict=True)
            self.assertEqual(result, {})
            self.assertEqual(catalog.return_value.search_catalog_items.call_args.kwargs["identifiers"], ["047596190524"])

    def test_listing_without_fnsku_is_counted_for_enablement(self):
        listing = {"asin": "B07XV1JMRF", "seller_sku": "W2-NGC9-BAZS", "title": "Tablecloth"}
        with (patch.object(ss_config, "BASE_DIR", self.base_dir),
              patch.object(ss_fba_inventory, "_fba_local_amazon_listing", return_value=listing),
              patch.object(ss_fba_readiness, "_fba_listing_readiness", return_value={
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

    def test_listing_is_counted_without_calling_readiness(self):
        listing = {"asin": "B07XV1JMRF", "seller_sku": "SKU-BLOCKED", "title": "Item"}
        with (patch.object(ss_config, "BASE_DIR", self.base_dir),
              patch.object(ss_fba_inventory, "_fba_local_amazon_listing", return_value=listing),
              patch.object(ss_fba_readiness, "_fba_listing_readiness",
                           side_effect=AssertionError("Step one must not check readiness")) as readiness):
            response = self.scan()
        self.assertEqual(response.status_code, 200)
        readiness.assert_not_called()
        self.assertEqual(response.get_json()["session"]["total_units"], 1)

    def test_lookup_outage_is_retryable_and_not_counted(self):
        for failure in ("database", "catalog"):
            with self.subTest(failure=failure):
                with (patch.object(ss_config, "BASE_DIR", self.base_dir),
                      patch.object(ss_fba_inventory, "_fba_local_amazon_listing",
                                   side_effect=RuntimeError("offline") if failure == "database" else None,
                                   return_value={}),
                      patch.object(ss_amazon_catalog, "_amazon_spapi_context",
                                   return_value=(None, "seller", "market", None)),
                      patch.object(ss_amazon_catalog, "_amazon_catalog_product_from_barcode",
                                   side_effect=RuntimeError("offline"))):
                    response = self.scan()
                self.assertEqual(response.status_code, 503)
                payload = response.get_json()
                self.assertFalse(payload.get("not_added", False))
                self.assertEqual(payload["sort_result"]["kind"], "error")
                with sqlite3.connect(self.base_dir / "searchRack.db") as db:
                    self.assertEqual(db.execute("SELECT total_units FROM fba_prep_sessions WHERE id=1").fetchone()[0], 0)
                    self.assertEqual(db.execute("SELECT COUNT(*) FROM fba_count_scans").fetchone()[0], 0)
                db.close()

    def test_strict_local_lookup_propagates_database_errors(self):
        with patch.object(ss_config, "BASE_DIR", self.base_dir):
            with self.assertRaises(sqlite3.OperationalError):
                ss_fba_inventory._fba_local_amazon_listing("123456789012", strict=True)
            with self.assertRaises(sqlite3.OperationalError):
                ss_fba_inventory._fba_local_amazon_listing_by_asin("B012345678", strict=True)

    def test_strict_catalog_lookup_propagates_api_errors(self):
        with patch("sp_api.api.CatalogItems") as catalog:
            catalog.return_value.search_catalog_items.return_value = Mock(errors=[{"code": "QuotaExceeded"}])
            with self.assertRaises(RuntimeError):
                ss_amazon_catalog._amazon_resolve_asin_from_upc(None, None, "market", "123456789012", strict=True)
            self.assertIsNone(ss_amazon_catalog._amazon_resolve_asin_from_upc(None, None, "market", "123456789012"))

    def test_missing_battery_and_dangerous_goods_answers_are_amber_and_counted(self):
        listing = {"asin": "B07XV1JMRF", "seller_sku": "SKU-SAFETY", "title": "Household item"}
        message = ("'Dangerous Goods Regulations' is required but missing. "
                   "(supplier_declared_dg_hz_regulation); 'Are batteries required?' is required "
                   "but missing. (batteries_required)")
        with (patch.object(ss_config, "BASE_DIR", self.base_dir),
              patch.object(ss_fba_inventory, "_fba_local_amazon_listing", return_value=listing),
              patch.object(ss_fba_readiness, "_fba_listing_readiness", return_value={
                  "status": "needs_safety_info", "error": message, "product_type": "HOME",
              })):
            response = self.scan("026865983135")
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["sort_result"]["kind"], "ready")
        self.assertEqual(payload["sort_result"]["headline"], "COUNTED")
        item = payload["session"]["items"][0]
        self.assertEqual(item["fba_enablement_status"], "needs_enablement")
        self.assertFalse(item["batteries_required"])

    def test_flat_safety_error_reported_as_failed_is_still_amber_and_counted(self):
        listing = {"asin": "B07XV1JMRF", "seller_sku": "SKU-TABLECLOTH", "title": "Tablecloth"}
        message = ("'Dangerous Goods Regulations' is required but missing. "
                   "(supplier_declared_dg_hz_regulation); 'Are batteries required?' is required "
                   "but missing. (batteries_required)")
        with (patch.object(ss_config, "BASE_DIR", self.base_dir),
              patch.object(ss_fba_inventory, "_fba_local_amazon_listing", return_value=listing),
              patch.object(ss_fba_readiness, "_fba_listing_readiness", return_value={
                  "status": "failed", "error": message, "product_type": "HOME",
              })):
            response = self.scan("047596044292")
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["sort_result"]["kind"], "ready")
        self.assertEqual(payload["sort_result"]["headline"], "COUNTED")
        self.assertEqual(payload["session"]["items"][0]["fba_enablement_status"], "needs_enablement")
        self.assertFalse(payload["session"]["items"][0]["batteries_required"])

    def test_removed_reject_remains_recognizable_for_label_sorting(self):
        item = {"barcode": "025398232475", "seller_sku": "SKU-REJECTED", "quantity": 1,
                "fba_enablement_status": "failed", "fba_enablement_error": "You need approval to list in this brand."}
        with patch.object(ss_config, "BASE_DIR", self.base_dir):
            saved = self.client.post("/api/fba-prep/sessions", json={"id": 1, "items": [item]})
            self.assertEqual(saved.status_code, 200)
            removed = self.client.post("/api/fba-prep/sessions", json={"id": 1, "items": []})
            self.assertEqual(removed.status_code, 200)
            self.assertEqual(removed.get_json()["session"]["total_units"], 0)
            self.assertEqual(len(removed.get_json()["session"]["rejected_items"]), 1)
            rejected = self.client.get("/api/fba-labels/resolve?barcode=25398232475")
            self.assertEqual(rejected.status_code, 422)
            self.assertTrue(rejected.get_json()["rejected"])
            # Once repaired and explicitly saved, the old rejection is cleared.
            item["fba_enablement_status"] = "ready"
            item["fba_enablement_error"] = ""
            repaired = self.client.post("/api/fba-prep/sessions", json={"id": 1, "items": [item]})
            self.assertEqual(repaired.get_json()["session"]["rejected_items"], [])

    def test_continue_ready_preserves_all_excluded_rows_and_plan_quantities(self):
        from fba_inbound import build_create_plan_request, US_MARKETPLACE_ID
        rows = [
            {"barcode": "111111111111", "seller_sku": "READY", "quantity": 2, "fba_enablement_status": "ready"},
            {"barcode": "222222222222", "seller_sku": "REJECT", "quantity": 1, "fba_enablement_status": "failed", "fba_enablement_error": "Brand approval required"},
            {"barcode": "333333333333", "seller_sku": "PENDING", "quantity": 1, "fba_enablement_status": "needs_enablement"},
            {"barcode": "444444444444", "asin": "B000U67JMQ", "seller_sku": "", "quantity": 1},
            {"barcode": "555555555555", "seller_sku": "REJECT", "quantity": 1, "fba_enablement_status": "ready"},
        ]
        with patch.object(ss_config, "BASE_DIR", self.base_dir):
            self.assertEqual(self.client.post("/api/fba-prep/sessions", json={"id": 1, "items": rows}).status_code, 200)
            response = self.client.post("/api/fba-prep/sessions", json={"id": 1, "items": rows[:1], "preserve_excluded_items": True})
            self.assertEqual(response.status_code, 200)
            session = response.get_json()["session"]
            self.assertEqual(session["total_units"], 2)
            self.assertEqual(len(session["rejected_items"]), 4)
            self.assertTrue(ss_fba_readiness._fba_enablement_progress(session["items"])["all_ready"])
            for barcode, rejected in [("222222222222", True), ("333333333333", False), ("444444444444", False), ("555555555555", False)]:
                result = self.client.get("/api/fba-labels/resolve?barcode=" + barcode)
                self.assertEqual(result.status_code, 422)
                self.assertEqual(result.get_json()["rejected"], rejected)
            reloaded = self.client.get("/api/fba-prep/sessions/1").get_json()["session"]
            self.assertEqual(len(reloaded["rejected_items"]), 4)
        source = {"name": "Warehouse", "addressLine1": "1 Main St", "city": "Miami", "stateOrProvinceCode": "FL", "postalCode": "33101", "countryCode": "US", "phoneNumber": "3055550100", "email": "shipping@example.com"}
        body = build_create_plan_request(session, source, US_MARKETPLACE_ID)
        self.assertEqual([(item["msku"], item["quantity"]) for item in body["items"]], [("READY", 2)])

    def test_save_catalog_count_with_empty_prep_data(self):
        payload = {"id": 1, "session_name": "Catalog count", "items": [
            {"barcode": "047596190524", "asin": "B000U67JMQ", "quantity": 2,
             "seller_sku": "", "amazon_prep": {}},
            {"barcode": "026865983135", "asin": "B07XV1JMRF", "quantity": 1,
             "seller_sku": "SKU-EXISTING", "amazon_prep": {"prepTypes": ["polybagging"]}},
        ]}
        with patch.object(ss_config, "BASE_DIR", self.base_dir):
            response = self.client.post("/api/fba-prep/sessions", json=payload)
            self.assertEqual(response.status_code, 200, response.get_json())
            saved = self.client.get("/api/fba-prep/sessions/1").get_json()["session"]
        self.assertEqual(saved["total_units"], 3)
        self.assertEqual(saved["items"][0]["asin"], "B000U67JMQ")
        self.assertEqual(saved["items"][0]["seller_sku"], "")
        self.assertEqual(saved["items"][1]["amazon_prep"]["prep_types"], ["POLYBAGGING"])

    def test_save_then_enable_fba_with_empty_prep_data(self):
        listings = Mock()
        listings.get_listings_item.return_value = Mock(errors=None, payload={
            "summaries": [{"productType": "HOME", "status": ["BUYABLE", "DISCOVERABLE"]}],
            "issues": [], "fulfillmentAvailability": [{"fulfillmentChannelCode": "DEFAULT"}],
        })
        listings.patch_listings_item.return_value = Mock(errors=None, payload={"status": "ACCEPTED", "issues": []})
        with (patch.object(ss_config, "BASE_DIR", self.base_dir),
              patch.object(ss_fba_readiness, "_fba_listings_client", return_value=(listings, "SELLER", "market"))):
            saved = self.client.post("/api/fba-prep/sessions", json={
                "id": 1, "session_name": "FBA setup", "items": [
                    {"barcode": "026865983135", "asin": "B07XV1JMRF", "quantity": 1,
                     "seller_sku": "SKU-SAVE-SETUP", "amazon_prep": {}},
                ],
            })
            self.assertEqual(saved.status_code, 200, saved.get_json())
            response = self.client.post("/api/fba-prep/sessions/1/enable-fba", json={"confirm": True})
        self.assertEqual(response.status_code, 200, response.get_json())
        self.assertEqual(response.get_json()["enablement"]["enabling"], 1)
        listings.patch_listings_item.assert_called_once()

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
        with patch.object(ss_config, "BASE_DIR", self.base_dir):
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
        with patch.object(ss_config, "BASE_DIR", self.base_dir):
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
        with patch.object(ss_config, "BASE_DIR", self.base_dir):
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
        with (patch.object(ss_config, "BASE_DIR", self.base_dir),
              patch.object(ss_fba_readiness, "_fba_listings_client", return_value=(listings, "SELLER", "ATVPDKIKX0DER"))):
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

    BRAND_FAMILY_ISSUE = {
        "code": "100898", "severity": "ERROR", "categories": ["INVALID_ATTRIBUTE"],
        "attributeNames": ["brand", "global_catalog_owner"],
        "message": "Your variation child ASIN B0CB9BQF6P can't be added to parent ASIN B0CBM8W1RZ "
                   "because the brand value was not consistent across the family.",
    }
    APPROVAL_ISSUE = {
        "code": "18299", "severity": "ERROR", "categories": ["QUALIFICATION_REQUIRED"],
        "message": "You need approval to list in this brand.",
    }

    def test_listing_readiness_keeps_catalog_quality_errors_as_notes(self):
        # Seller Central still converts a BUYABLE offer to FBA when the listing
        # only carries catalog-quality errors, so the read-only check must not
        # fail the row before Amazon has been asked.
        listings = Mock()
        listings.get_listings_item.return_value = Mock(errors=None, payload={
            "summaries": [{"productType": "TABLECLOTH", "status": ["BUYABLE", "DISCOVERABLE"]}],
            "issues": [self.BRAND_FAMILY_ISSUE],
            "fulfillmentAvailability": [{"fulfillmentChannelCode": "DEFAULT"}],
        })
        with patch.object(ss_fba_readiness, "_fba_item_prep_details", return_value={}):
            result = ss_fba_readiness._fba_listing_readiness(
                "UV-SO4Z-K3CC", force_refresh=True, client=(listings, "SELLER", "ATVPDKIKX0DER"))
        self.assertEqual(result["status"], "needs_enablement")
        self.assertEqual(result["error"], "")
        self.assertIn("brand value was not consistent", result["listing_notes"])
        self.assertIn("(brand, global_catalog_owner)", result["listing_notes"])

    def test_listing_readiness_keeps_approval_errors_blocking(self):
        listings = Mock()
        listings.get_listings_item.return_value = Mock(errors=None, payload={
            "summaries": [{"productType": "PILLOW_SHAM", "status": ["DISCOVERABLE"]}],
            "issues": [self.APPROVAL_ISSUE],
            "fulfillmentAvailability": [{"fulfillmentChannelCode": "AMAZON_NA"}],
        })
        with patch.object(ss_fba_readiness, "_fba_item_prep_details", return_value={}):
            result = ss_fba_readiness._fba_listing_readiness(
                "A1-GX7S-49LJ", force_refresh=True, client=(listings, "SELLER", "ATVPDKIKX0DER"))
        self.assertEqual(result["status"], "failed")
        self.assertIn("approval", result["error"])
        self.assertEqual(result["listing_notes"], "")

    def test_fba_patch_follows_amazon_acceptance_not_echoed_catalog_errors(self):
        description_issue = {
            "code": "90220", "severity": "ERROR", "attributeNames": ["product_description"],
            "message": "'Product Description' is required but missing.",
        }
        readiness = {"status": "needs_enablement", "product_type": "DISHWARE_PLATE",
                     "fulfillment_channels": ["DEFAULT"]}
        client = (Mock(), "SELLER", "ATVPDKIKX0DER")
        client[0].patch_listings_item.return_value = Mock(errors=None, payload={
            "status": "ACCEPTED", "issues": [description_issue]})
        result = ss_fba_readiness._fba_patch_listing_to_fba("2M-RBXO-SHCC", readiness, client=client)
        self.assertEqual(result["status"], "enabling")
        self.assertEqual(result["error"], "")
        self.assertIn("Product Description", result["listing_notes"])
        client[0].patch_listings_item.return_value = Mock(errors=None, payload={
            "status": "INVALID", "issues": [description_issue]})
        result = ss_fba_readiness._fba_patch_listing_to_fba("2M-RBXO-SHCC", readiness, client=client)
        self.assertEqual(result["status"], "failed")
        self.assertIn("Product Description", result["error"])
        client[0].patch_listings_item.return_value = Mock(errors=None, payload={
            "status": "ACCEPTED", "issues": [self.APPROVAL_ISSUE]})
        result = ss_fba_readiness._fba_patch_listing_to_fba("2M-RBXO-SHCC", readiness, client=client)
        self.assertEqual(result["status"], "failed")
        self.assertIn("approval", result["error"])

    def test_enable_fba_retries_rows_that_failed_on_catalog_quality_errors(self):
        db = sqlite3.connect(self.base_dir / "searchRack.db")
        db.execute("UPDATE fba_prep_sessions SET items_json=?, item_count=1, total_units=1 WHERE id=1", (
            '[{"barcode":"194590090586","seller_sku":"UV-SO4Z-K3CC","quantity":1,'
            '"fba_enablement_status":"failed","fba_enablement_error":"brand value was not consistent"}]',
        ))
        db.commit()
        db.close()
        listings = Mock()
        listings.get_listings_item.return_value = Mock(errors=None, payload={
            "summaries": [{"productType": "TABLECLOTH", "status": ["BUYABLE", "DISCOVERABLE"]}],
            "issues": [self.BRAND_FAMILY_ISSUE],
            "fulfillmentAvailability": [{"fulfillmentChannelCode": "DEFAULT", "quantity": 1}],
        })
        listings.patch_listings_item.return_value = Mock(errors=None, payload={
            "status": "ACCEPTED", "issues": [self.BRAND_FAMILY_ISSUE]})
        with (patch.object(ss_config, "BASE_DIR", self.base_dir),
              patch.object(ss_fba_readiness, "_fba_item_prep_details", return_value={}),
              patch.object(ss_fba_readiness, "_fba_listings_client", return_value=(listings, "SELLER", "ATVPDKIKX0DER"))):
            response = self.client.post("/api/fba-prep/sessions/1/enable-fba", json={
                "confirm": True, "seller_skus": ["UV-SO4Z-K3CC"]})
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["enablement"]["enabling"], 1)
        self.assertEqual(payload["enablement"]["failed"], 0)
        item = payload["session"]["items"][0]
        self.assertEqual(item["fba_enablement_status"], "enabling")
        self.assertEqual(item["fba_enablement_error"], "")
        self.assertIn("brand value was not consistent", item["fba_listing_notes"])
        paths = [patch_row["path"] for patch_row in
                 listings.patch_listings_item.call_args.kwargs["body"]["patches"]]
        self.assertIn("/attributes/fulfillment_availability", paths)

    INBOUND_PENDING_PREP = {"checked": False, "error": "[{'code': 'BadRequest', 'message': 'ERROR: The following "
                            "MSKUs are not available for inbound. If these were recently added, please try again later.'}]"}

    def test_listing_readiness_uses_fba_inventory_fnsku_while_inbound_propagates(self):
        # Listings Items still shows the merchant channel and no FNSKU minutes
        # after an accepted FBA switch, while FBA inventory already has the FNSKU.
        listings = Mock()
        listings.get_listings_item.return_value = Mock(errors=None, payload={
            "summaries": [{"productType": "DRINKING_CUP", "status": ["BUYABLE", "DISCOVERABLE"]}],
            "issues": [],
            "fulfillmentAvailability": [{"fulfillmentChannelCode": "DEFAULT", "quantity": 7}],
            "attributes": {"fulfillment_availability": [{"fulfillment_channel_code": "AMAZON_NA"}]},
        })
        client = (listings, "SELLER", "ATVPDKIKX0DER")
        with (patch.object(ss_fba_readiness, "_fba_inventory_fnsku", return_value="X005ASUE9F"),
              patch.object(ss_fba_readiness, "_fba_item_prep_details", return_value=self.INBOUND_PENDING_PREP)):
            result = ss_fba_readiness._fba_listing_readiness("OW-BUK2-TVB7", force_refresh=True, client=client)
        self.assertEqual(result["status"], "enabling")
        self.assertEqual(result["fnsku"], "X005ASUE9F")
        self.assertEqual(result["submitted_channels"], ["AMAZON_NA"])
        self.assertIn("X005ASUE9F", result["activation_note"])
        self.assertEqual(result["error"], "")
        with (patch.object(ss_fba_readiness, "_fba_inventory_fnsku", return_value="X005ASUE9F"),
              patch.object(ss_fba_readiness, "_fba_item_prep_details", return_value={"checked": True, "error": ""})):
            result = ss_fba_readiness._fba_listing_readiness("OW-BUK2-TVB7", force_refresh=True, client=client)
        self.assertEqual(result["status"], "ready")
        self.assertEqual(result["fnsku"], "X005ASUE9F")

    def test_listing_readiness_treats_submitted_fba_channel_as_activating(self):
        listings = Mock()
        listings.get_listings_item.return_value = Mock(errors=None, payload={
            "summaries": [{"productType": "PITCHER", "status": ["BUYABLE"]}],
            "issues": [],
            "fulfillmentAvailability": [{"fulfillmentChannelCode": "DEFAULT", "quantity": 13}],
            "attributes": {"fulfillment_availability": [{"fulfillment_channel_code": "AMAZON_NA"}]},
        })
        with (patch.object(ss_fba_readiness, "_fba_inventory_fnsku", return_value=""),
              patch.object(ss_fba_readiness, "_fba_item_prep_details", return_value={"checked": False, "error": ""})):
            result = ss_fba_readiness._fba_listing_readiness(
                "XK-PQ59-TA3O", force_refresh=True, client=(listings, "SELLER", "ATVPDKIKX0DER"))
        self.assertEqual(result["status"], "enabling")
        self.assertIn("still activating", result["activation_note"])
        listings.get_listings_item.return_value = Mock(errors=None, payload={
            "summaries": [{"productType": "PITCHER", "status": ["BUYABLE"]}], "issues": [],
            "fulfillmentAvailability": [{"fulfillmentChannelCode": "DEFAULT", "quantity": 13}],
            "attributes": {"fulfillment_availability": [{"fulfillment_channel_code": "DEFAULT", "quantity": 13}]},
        })
        with (patch.object(ss_fba_readiness, "_fba_inventory_fnsku", return_value=""),
              patch.object(ss_fba_readiness, "_fba_item_prep_details", return_value={"checked": False, "error": ""})):
            result = ss_fba_readiness._fba_listing_readiness(
                "XK-PQ59-TA3O", force_refresh=True, client=(listings, "SELLER", "ATVPDKIKX0DER"))
        self.assertEqual(result["status"], "needs_enablement")
        self.assertEqual(result["activation_note"], "")

    def test_enablement_status_waits_in_local_time_before_giving_up(self):
        import datetime as _dt
        started = (_dt.datetime.now() - _dt.timedelta(seconds=60)).isoformat()
        db = sqlite3.connect(self.base_dir / "searchRack.db")
        db.execute("UPDATE fba_prep_sessions SET items_json=?, item_count=1, total_units=1 WHERE id=1", (
            json.dumps([{
                "barcode": "026865983135", "seller_sku": "OW-BUK2-TVB7", "quantity": 1,
                "fba_enablement_status": "enabling", "fba_activation_started_at": started,
                "fba_activation_retry_count": 1,
            }]),
        ))
        db.commit()
        db.close()
        listings_client = (Mock(), "SELLER", "ATVPDKIKX0DER")
        readiness = {"status": "needs_enablement", "product_type": "DRINKING_CUP",
                     "fulfillment_channels": ["DEFAULT"], "error": ""}
        with (patch.object(ss_config, "BASE_DIR", self.base_dir),
              patch.object(ss_fba_readiness, "_fba_listings_client", return_value=listings_client),
              patch.object(ss_fba_readiness, "_fba_listing_readiness", return_value=readiness),
              patch.object(ss_fba_readiness, "_fba_patch_listing_to_fba") as retry_patch):
            response = self.client.post("/api/fba-prep/sessions/1/enable-fba-status")
        self.assertEqual(response.status_code, 200)
        item = response.get_json()["session"]["items"][0]
        self.assertEqual(item["fba_enablement_status"], "enabling")
        self.assertEqual(item["fba_enablement_error"], "")
        retry_patch.assert_not_called()

    def test_enablement_status_saves_activation_note_and_early_fnsku(self):
        db = sqlite3.connect(self.base_dir / "searchRack.db")
        db.execute("UPDATE fba_prep_sessions SET items_json=?, item_count=1, total_units=1 WHERE id=1", (
            '[{"barcode":"026865983135","seller_sku":"OW-BUK2-TVB7","quantity":1,'
            '"fba_enablement_status":"enabling","fba_activation_started_at":"2026-09-09T13:23:56"}]',
        ))
        db.commit()
        db.close()
        readiness = {"status": "enabling", "product_type": "DRINKING_CUP", "fnsku": "X005ASUE9F",
                     "fulfillment_channels": ["DEFAULT"], "submitted_channels": ["AMAZON_NA"], "error": "",
                     "activation_note": "Amazon assigned FNSKU X005ASUE9F and is still making this SKU available for inbound shipments."}
        with (patch.object(ss_config, "BASE_DIR", self.base_dir),
              patch.object(ss_fba_readiness, "_fba_listings_client", return_value=(Mock(), "SELLER", "ATVPDKIKX0DER")),
              patch.object(ss_fba_readiness, "_fba_listing_readiness", return_value=readiness)):
            response = self.client.post("/api/fba-prep/sessions/1/enable-fba-status")
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        item = payload["session"]["items"][0]
        self.assertEqual(item["fba_enablement_status"], "enabling")
        self.assertEqual(item["amazon_fnsku"], "X005ASUE9F")
        self.assertIn("X005ASUE9F", item["fba_activation_note"])
        self.assertEqual(payload["enablement"]["enabling"], 1)

    def test_enable_fba_explains_stale_seller_sku_not_found(self):
        db = sqlite3.connect(self.base_dir / "searchRack.db")
        db.execute("UPDATE fba_prep_sessions SET items_json=?, item_count=1, total_units=1 WHERE id=1", (
            '[{"barcode":"026865983135","seller_sku":"K0-MGPQ-SUNX","quantity":1,'
            '"fba_enablement_status":"needs_enablement"}]',
        ))
        db.commit()
        db.close()
        listings = Mock()
        listings.get_listings_item.side_effect = Exception(
            "[{'code': 'NOT_FOUND', 'message': \"SKU 'K0-MGPQ-SUNX' not found\"}]")
        with (patch.object(ss_config, "BASE_DIR", self.base_dir),
              patch.object(ss_fba_readiness, "_fba_listings_client", return_value=(listings, "SELLER", "ATVPDKIKX0DER"))):
            response = self.client.post("/api/fba-prep/sessions/1/enable-fba", json={"confirm": True})
        self.assertEqual(response.status_code, 200)
        item = response.get_json()["session"]["items"][0]
        self.assertEqual(item["fba_enablement_status"], "failed")
        self.assertIn("Amazon has no listing for Seller SKU K0-MGPQ-SUNX", item["fba_enablement_error"])
        self.assertIn("Count step", item["fba_enablement_error"])
        listings.patch_listings_item.assert_not_called()

    def test_enable_fba_switches_to_current_seller_sku_after_relisting(self):
        db = sqlite3.connect(self.base_dir / "searchRack.db")
        db.execute("UPDATE fba_prep_sessions SET items_json=?, item_count=1, total_units=1 WHERE id=1", (
            '[{"barcode":"194137216844","seller_sku":"K0-MGPQ-SUNX","asin":"B0HDDM9FJW","quantity":1,'
            '"fba_enablement_status":"failed","fba_enablement_error":"NOT_FOUND"}]',
        ))
        db.commit()
        db.close()
        listings = Mock()

        def get_item(seller, sku, **kwargs):
            if sku == "K0-MGPQ-SUNX":
                raise Exception("[{'code': 'NOT_FOUND', 'message': \"SKU 'K0-MGPQ-SUNX' not found\"}]")
            return Mock(errors=None, payload={
                "summaries": [{"productType": "DRINKING_CUP", "asin": "B0HDDM9FJW", "status": ["BUYABLE", "DISCOVERABLE"]}],
                "issues": [], "fulfillmentAvailability": [{"fulfillmentChannelCode": "DEFAULT", "quantity": 4}],
                "attributes": {},
            })
        listings.get_listings_item.side_effect = get_item
        listings.search_listings_items.return_value = Mock(errors=None, payload={"items": [{
            "sku": "GA-JHZ9-YNS2",
            "summaries": [{"asin": "B0HDDM9FJW", "status": ["BUYABLE", "DISCOVERABLE"]}],
            "fulfillmentAvailability": [{"fulfillmentChannelCode": "DEFAULT"}],
        }]})
        listings.patch_listings_item.return_value = Mock(errors=None, payload={"status": "ACCEPTED", "issues": []})
        with (patch.object(ss_config, "BASE_DIR", self.base_dir),
              patch.object(ss_fba_readiness, "_fba_listings_client", return_value=(listings, "SELLER", "ATVPDKIKX0DER")),
              patch.object(ss_fba_readiness, "_fba_inventory_fnsku", return_value=""),
              patch.object(ss_fba_readiness, "_fba_item_prep_details", return_value={}),
              patch.object(ss_fba_inventory, "_fba_remember_replacement_listing", return_value=True) as remember):
            response = self.client.post("/api/fba-prep/sessions/1/enable-fba", json={"confirm": True})
        self.assertEqual(response.status_code, 200)
        item = response.get_json()["session"]["items"][0]
        self.assertEqual(item["seller_sku"], "GA-JHZ9-YNS2")
        self.assertEqual(item["fba_enablement_status"], "enabling")
        self.assertEqual(item["fba_enablement_error"], "")
        self.assertIn("switched to your current listing GA-JHZ9-YNS2", item["fba_listing_notes"])
        self.assertEqual(listings.patch_listings_item.call_args.args[1], "GA-JHZ9-YNS2")
        remember.assert_called_once()
        self.assertEqual(remember.call_args.args, ("194137216844", "B0HDDM9FJW", "K0-MGPQ-SUNX", "GA-JHZ9-YNS2"))

    def test_remember_replacement_listing_updates_local_cache(self):
        db = sqlite3.connect(self.base_dir / "amazonStore.db")
        db.execute("""CREATE TABLE ITEMS (ID INTEGER PRIMARY KEY, ASIN TEXT, SKU TEXT, TITLE TEXT, PRICE REAL,
                    QUANTITY INTEGER, STATUS TEXT, IMAGE TEXT, UPC TEXT, CONDITION TEXT, CONDITION_NOTE TEXT,
                    FULFILLMENT_CHANNEL TEXT, LAST_UPDATED TEXT)""")
        db.execute("INSERT INTO ITEMS (ASIN, SKU, UPC, STATUS, FULFILLMENT_CHANNEL) VALUES "
                   "('B0HDDM9FJW', 'K0-MGPQ-SUNX', '194137216844', 'Inactive', 'AMAZON_NA')")
        db.commit()
        db.close()
        with patch.object(ss_config, "BASE_DIR", self.base_dir):
            self.assertTrue(ss_fba_inventory._fba_remember_replacement_listing(
                "194137216844", "B0HDDM9FJW", "K0-MGPQ-SUNX", "GA-JHZ9-YNS2", fulfillment_channel="DEFAULT"))
            cached = ss_fba_inventory._fba_local_amazon_listing("194137216844")
        self.assertEqual(cached["seller_sku"], "GA-JHZ9-YNS2")
        self.assertEqual(cached["status"], "Active")

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
        result = ss_fba_readiness._fba_patch_listing_to_fba(
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
        with (patch.object(ss_config, "BASE_DIR", self.base_dir),
              patch.object(ss_fba_readiness, "_fba_listings_client", return_value=listings_client),
              patch.object(ss_fba_readiness, "_fba_listing_readiness", return_value=readiness),
              patch.object(ss_fba_readiness, "_fba_create_separate_fba_offer", return_value=created) as create_offer,
              patch.object(ss_fba_readiness, "_fba_patch_listing_to_fba") as convert_offer):
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
            ss_fba_inventory._fba_apply_inventory_offer_strategy(db.cursor(), items)
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
        result = ss_fba_readiness._fba_create_separate_fba_offer(
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

        with (patch.object(ss_config, "BASE_DIR", self.base_dir),
              patch.object(ss_fba_readiness, "_fba_listings_client", return_value=listings_client),
              patch.object(ss_fba_readiness, "_fba_listing_readiness", side_effect=readiness),
              patch.object(ss_fba_readiness, "_fba_patch_listing_to_fba",
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

        with (patch.object(ss_config, "BASE_DIR", self.base_dir),
              patch.object(ss_fba_readiness, "_fba_listings_client", return_value=listings_client),
              patch.object(ss_fba_readiness, "_fba_listing_readiness", side_effect=readiness),
              patch.object(ss_fba_readiness, "_fba_patch_listing_to_fba",
                           return_value={"status": "enabling", "issues": []}),
              patch.object(time, "sleep")):
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
        with patch.object(ss_config, "BASE_DIR", self.base_dir):
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
        with (patch.object(ss_config, "BASE_DIR", self.base_dir),
              patch.object(ss_fba_readiness, "_fba_listings_client", return_value=(listings, "SELLER", "ATVPDKIKX0DER"))):
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
        with (patch.object(ss_config, "BASE_DIR", self.base_dir),
              patch.object(ss_fba_readiness, "_fba_listings_client") as listings_client):
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
        with (patch.object(ss_config, "BASE_DIR", self.base_dir),
              patch.object(ss_fba_readiness, "_fba_listings_client", return_value=(listings, "SELLER", "ATVPDKIKX0DER"))):
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
        with (patch.object(ss_config, "BASE_DIR", self.base_dir),
              patch.object(ss_fba_readiness, "_fba_listings_client", return_value=listings_client),
              patch.object(ss_fba_readiness, "_fba_listing_readiness", return_value=readiness),
              patch.object(ss_fba_readiness, "_fba_patch_listing_to_fba",
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
        with (patch.object(ss_config, "BASE_DIR", self.base_dir),
              patch.object(ss_fba_readiness, "_fba_listings_client", return_value=listings_client),
              patch.object(ss_fba_readiness, "_fba_listing_readiness", return_value=readiness),
              patch.object(ss_fba_readiness, "_fba_patch_listing_to_fba") as retry_patch):
            response = self.client.post("/api/fba-prep/sessions/1/enable-fba-status")
        self.assertEqual(response.status_code, 200)
        item = response.get_json()["session"]["items"][0]
        self.assertEqual(item["fba_enablement_status"], "needs_enablement")
        self.assertIn("Tap Set up again", item["fba_enablement_error"])
        retry_patch.assert_not_called()


if __name__ == "__main__":
    unittest.main()
