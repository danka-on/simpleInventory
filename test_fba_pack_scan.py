import json
import os
import sqlite3
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("DISABLE_BACKGROUND_SERVICES", "1")

from sweetshelves.bootstrap import app  # Initialize routes once for Flask client tests.
from sweetshelves import caching as ss_caching
from sweetshelves import config as ss_config
from sweetshelves import fba_schema as ss_fba_schema
from sweetshelves import inventory_age as ss_inventory_age
from sweetshelves import runtime as ss_runtime
from printer_manager import printer_manager


class FbaPackScanTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base_dir = Path(self.temp.name)
        db = sqlite3.connect(self.base_dir / "searchRack.db")
        db.row_factory = sqlite3.Row
        db.execute('''
            CREATE TABLE SEARCHRACK (
                ID INTEGER PRIMARY KEY, BARCODE TEXT, TITLE TEXT,
                QUANTITY INTEGER, item_position TEXT, pictureposition TEXT
            )
        ''')
        db.execute(
            "INSERT INTO SEARCHRACK VALUES (1, '025398232475', 'Test unit', 2, 'A1B1', '')"
        )
        ss_fba_schema._ensure_fba_prep_tables(db.cursor())
        workflow = {
            "stage": "packing",
            "inbound_plan_id": "wf-test-plan",
            "packing_confirmed": True,
            "plan_items": [{"msku": "SKU-1", "quantity": 2}],
            "packing_groups": [{
                "packing_group_id": "pg-1",
                "label": "Group 1",
                "items": [{"msku": "SKU-1", "quantity": 2}],
            }],
            "boxes": [],
        }
        now = "2026-08-29T12:00:00"
        db.execute('''
            INSERT INTO fba_prep_sessions (
                id, session_name, items_json, item_count, total_units, status,
                created_at, updated_at, amazon_inbound_plan_id, amazon_stage,
                amazon_state_json
            ) VALUES (1, 'Test plan', ?, 1, 2, 'open', ?, ?, ?, 'packing', ?)
        ''', (
            json.dumps([{"seller_sku": "SKU-1", "quantity": 2}]),
            now, now, "wf-test-plan", json.dumps(workflow),
        ))
        db.commit()
        db.close()
        ss_runtime.app.testing = True
        self.client = ss_runtime.app.test_client()

    def tearDown(self):
        self.temp.cleanup()

    def post_scan(self, token="fba:1:1:25398232475", locations=None, client=None, label_printed=True):
        client = client or self.client
        return client.post(
            "/api/fba-prep/sessions/1/amazon/pack-scan",
            json={
                "scan_token": token,
                "barcode": "025398232475",
                "msku": "SKU-1",
                "title": "Test unit",
                "source_locations": ["A1B1"] if locations is None else locations,
                "label_printed": label_printed,
                "client_id": "scanner-test-01",
                "operator_name": "Test Scanner",
            },
        )

    def reset_scan(self, msku="SKU-1"):
        return self.client.post(
            "/api/fba-prep/sessions/1/amazon/reset-pack-scan",
            json={
                "msku": msku,
                "client_id": "scanner-test-01",
                "operator_name": "Test Scanner",
            },
        )

    def test_scan_removes_one_unit_logs_location_and_routes_box(self):
        with (
            patch.object(ss_config, "BASE_DIR", self.base_dir),
            patch.object(ss_inventory_age, "_inventory_age_consume_fifo"),
            patch.object(ss_caching, "update_data_version"),
            patch.object(ss_caching, "_invalidate_searchrack_cache"),
        ):
            response = self.post_scan()

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["scan"]["inventory_removed"], 1)
        self.assertEqual(payload["scan"]["source_location"], "A1B1")
        self.assertEqual(payload["scan"]["box_id"], "BOX-01")
        self.assertEqual(payload["amazon_workflow"]["boxes"][0]["contents"][0]["quantity"], 1)

        db = sqlite3.connect(self.base_dir / "searchRack.db")
        self.assertEqual(db.execute("SELECT QUANTITY FROM SEARCHRACK WHERE ID = 1").fetchone()[0], 1)
        self.assertEqual(db.execute("SELECT COUNT(*) FROM fba_pack_scans").fetchone()[0], 1)
        db.close()
        history = sqlite3.connect(self.base_dir / "rackhistory.db")
        row = history.execute(
            "SELECT removal_type, item_position, from_position FROM removed_items"
        ).fetchone()
        history.close()
        self.assertEqual(row, ("fba_removed", "A1B1", "A1B1"))

    def test_retry_token_is_idempotent_and_does_not_remove_twice(self):
        with (
            patch.object(ss_config, "BASE_DIR", self.base_dir),
            patch.object(ss_inventory_age, "_inventory_age_consume_fifo"),
            patch.object(ss_caching, "update_data_version"),
            patch.object(ss_caching, "_invalidate_searchrack_cache"),
        ):
            first = self.post_scan()
            second = self.post_scan()

        self.assertEqual(first.status_code, 200)
        self.assertTrue(second.get_json()["idempotent"])
        db = sqlite3.connect(self.base_dir / "searchRack.db")
        self.assertEqual(db.execute("SELECT QUANTITY FROM SEARCHRACK WHERE ID = 1").fetchone()[0], 1)
        db.close()

    def test_reset_last_scan_restores_inventory_box_and_rack_history_then_allows_rescan(self):
        first_token = "fba:1:reset-test:first-unit"
        with (
            patch.object(ss_config, "BASE_DIR", self.base_dir),
            patch.object(ss_inventory_age, "_inventory_age_consume_fifo"),
            patch.object(ss_caching, "update_data_version"),
            patch.object(ss_caching, "_invalidate_searchrack_cache"),
        ):
            packed = self.post_scan(token=first_token, label_printed=False)
            reset = self.reset_scan()
            rescanned = self.post_scan(token="fba:1:reset-test:second-attempt", label_printed=False)

        self.assertEqual(packed.status_code, 200)
        self.assertEqual(reset.status_code, 200)
        reset_payload = reset.get_json()
        self.assertTrue(reset_payload["reset_scan"]["inventory_restored"])
        self.assertEqual(reset_payload["reset_scan"]["source_location"], "A1B1")
        self.assertEqual(reset_payload["amazon_workflow"]["boxes"][0]["contents"], [])
        self.assertEqual(rescanned.status_code, 200)

        db = sqlite3.connect(self.base_dir / "searchRack.db")
        self.assertEqual(db.execute("SELECT QUANTITY FROM SEARCHRACK WHERE ID = 1").fetchone()[0], 1)
        self.assertEqual(db.execute("SELECT COUNT(*) FROM fba_pack_scans").fetchone()[0], 1)
        state = json.loads(db.execute(
            "SELECT amazon_state_json FROM fba_prep_sessions WHERE id = 1"
        ).fetchone()[0])
        db.close()
        self.assertEqual(state["boxes"][0]["contents"][0]["quantity"], 1)
        self.assertEqual(state["inventory_removed_by_msku"]["SKU-1"], 1)

        history = sqlite3.connect(self.base_dir / "rackhistory.db")
        original_status = history.execute(
            "SELECT event_status FROM removed_items WHERE event_id = ?",
            ("fba-pack-" + first_token,),
        ).fetchone()[0]
        history.close()
        self.assertEqual(original_status, "reset")

    def test_reset_scan_without_inventory_removal_only_unpacks_the_unit(self):
        with (
            patch.object(ss_config, "BASE_DIR", self.base_dir),
            patch.object(ss_inventory_age, "_inventory_age_consume_fifo"),
        ):
            packed = self.post_scan(
                token="fba:1:reset-test:no-inventory", locations=["OTHER-BIN"],
                label_printed=False,
            )
            reset = self.reset_scan()

        self.assertEqual(packed.status_code, 200)
        self.assertEqual(reset.status_code, 200)
        self.assertFalse(reset.get_json()["reset_scan"]["inventory_restored"])
        db = sqlite3.connect(self.base_dir / "searchRack.db")
        self.assertEqual(db.execute("SELECT QUANTITY FROM SEARCHRACK WHERE ID = 1").fetchone()[0], 2)
        self.assertEqual(db.execute("SELECT COUNT(*) FROM fba_pack_scans").fetchone()[0], 0)
        state = json.loads(db.execute(
            "SELECT amazon_state_json FROM fba_prep_sessions WHERE id = 1"
        ).fetchone()[0])
        db.close()
        self.assertEqual(state["boxes"][0]["contents"], [])

    def test_two_workers_cannot_reset_and_restore_the_same_scan_twice(self):
        with (
            patch.object(ss_config, "BASE_DIR", self.base_dir),
            patch.object(ss_inventory_age, "_inventory_age_consume_fifo"),
            patch.object(ss_caching, "update_data_version"),
            patch.object(ss_caching, "_invalidate_searchrack_cache"),
        ):
            packed = self.post_scan(
                token="fba:1:reset-race:only-unit", label_printed=False
            )
            barrier = threading.Barrier(2)

            def reset(_index):
                client = ss_runtime.app.test_client()
                barrier.wait(timeout=5)
                return client.post(
                    "/api/fba-prep/sessions/1/amazon/reset-pack-scan",
                    json={"msku": "SKU-1", "client_id": f"reset-worker-{_index}"},
                )

            with ThreadPoolExecutor(max_workers=2) as pool:
                responses = list(pool.map(reset, (1, 2)))

        self.assertEqual(packed.status_code, 200)
        self.assertEqual(sorted(response.status_code for response in responses), [200, 409])
        db = sqlite3.connect(self.base_dir / "searchRack.db")
        self.assertEqual(db.execute("SELECT QUANTITY FROM SEARCHRACK WHERE ID = 1").fetchone()[0], 2)
        self.assertEqual(db.execute("SELECT COUNT(*) FROM fba_pack_scans").fetchone()[0], 0)
        db.close()

    def test_scan_still_packs_when_inventory_is_not_in_active_locations(self):
        with (
            patch.object(ss_config, "BASE_DIR", self.base_dir),
            patch.object(ss_inventory_age, "_inventory_age_consume_fifo"),
        ):
            response = self.post_scan(locations=["OTHER-BIN"])

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["scan"]["inventory_removed"], 0)
        self.assertIn("not found in the active working locations", payload["warning"])
        db = sqlite3.connect(self.base_dir / "searchRack.db")
        self.assertEqual(db.execute("SELECT QUANTITY FROM SEARCHRACK WHERE ID = 1").fetchone()[0], 2)
        db.close()

    def test_two_scanners_commit_without_losing_a_unit_or_box_progress(self):
        barrier = threading.Barrier(2)

        def scan(index):
            client = ss_runtime.app.test_client()
            barrier.wait(timeout=5)
            return client.post(
                "/api/fba-prep/sessions/1/amazon/pack-scan",
                json={
                    "scan_token": f"fba:1:scanner-{index}:unique-{index}",
                    "barcode": "025398232475",
                    "msku": "SKU-1",
                    "title": "Test unit",
                    "source_locations": ["A1B1"],
                    "client_id": f"scanner-test-{index}",
                    "operator_name": f"Scanner {index}",
                },
            )

        with (
            patch.object(ss_config, "BASE_DIR", self.base_dir),
            patch.object(ss_inventory_age, "_inventory_age_consume_fifo"),
            patch.object(ss_caching, "update_data_version"),
            patch.object(ss_caching, "_invalidate_searchrack_cache"),
            ThreadPoolExecutor(max_workers=2) as pool,
        ):
            responses = list(pool.map(scan, (1, 2)))

        self.assertEqual([response.status_code for response in responses], [200, 200])
        db = sqlite3.connect(self.base_dir / "searchRack.db")
        db.row_factory = sqlite3.Row
        self.assertEqual(db.execute("SELECT QUANTITY FROM SEARCHRACK WHERE ID = 1").fetchone()[0], 0)
        self.assertEqual(db.execute("SELECT COUNT(*) FROM fba_pack_scans").fetchone()[0], 2)
        state = json.loads(db.execute(
            "SELECT amazon_state_json FROM fba_prep_sessions WHERE id = 1"
        ).fetchone()[0])
        db.close()
        self.assertEqual(state["boxes"][0]["contents"][0]["quantity"], 2)
        self.assertGreaterEqual(state["revision"], 2)

    def test_concurrent_first_page_table_initialization_is_safe(self):
        migration_db = self.base_dir / "migration.db"
        barrier = threading.Barrier(2)

        def initialize(_index):
            connection = sqlite3.connect(migration_db, timeout=30)
            try:
                barrier.wait(timeout=5)
                ss_fba_schema._ensure_fba_prep_tables(connection.cursor())
                connection.commit()
            finally:
                connection.close()

        with ThreadPoolExecutor(max_workers=2) as pool:
            list(pool.map(initialize, (1, 2)))

        db = sqlite3.connect(migration_db)
        columns = {row[1] for row in db.execute("PRAGMA table_info('fba_pack_scans')")}
        db.close()
        self.assertIn("client_id", columns)
        self.assertIn("operator_name", columns)

    def test_live_session_reports_multiple_active_scanners(self):
        with patch.object(ss_config, "BASE_DIR", self.base_dir):
            first = self.client.post(
                "/api/fba-prep/sessions/1/live",
                json={"client_id": "scanner-live-01", "operator_name": "Alice"},
            )
            second = self.client.post(
                "/api/fba-prep/sessions/1/live",
                json={"client_id": "scanner-live-02", "operator_name": "Bob"},
            )

        self.assertEqual(first.status_code, 200)
        payload = second.get_json()
        self.assertEqual(payload["worker_count"], 2)
        self.assertEqual({row["operator_name"] for row in payload["workers"]}, {"Alice", "Bob"})

    def test_server_allocates_unique_boxes_and_stale_save_keeps_them(self):
        with patch.object(ss_config, "BASE_DIR", self.base_dir):
            first = self.client.post(
                "/api/fba-prep/sessions/1/amazon/create-box",
                json={"packing_group_id": "pg-1"},
            )
            second = self.client.post(
                "/api/fba-prep/sessions/1/amazon/create-box",
                json={"packing_group_id": "pg-1"},
            )
            stale_save = self.client.post(
                "/api/fba-prep/sessions/1/amazon/save-boxes",
                json={"boxes": [{
                    "local_id": "BOX-01", "packing_group_id": "pg-1",
                    "length_in": 12, "width_in": 10, "height_in": 8, "weight_lb": 5,
                    "contents": [],
                }]},
            )

        self.assertEqual(first.get_json()["created_box_id"], "BOX-01")
        self.assertEqual(second.get_json()["created_box_id"], "BOX-02")
        self.assertEqual(
            [box["local_id"] for box in stale_save.get_json()["amazon_workflow"]["boxes"]],
            ["BOX-01", "BOX-02"],
        )

    def test_label_retry_for_one_physical_scan_prints_only_once(self):
        token = "fba:1:scanner-label:unique-label-1"
        db = sqlite3.connect(self.base_dir / "searchRack.db")
        workflow = json.loads(db.execute(
            "SELECT amazon_state_json FROM fba_prep_sessions WHERE id = 1"
        ).fetchone()[0])
        workflow["plan_items"][0].update({
            "fnsku": "X001234567",
            "labelOwner": "SELLER",
            "prepInstructions": [{"prepType": "ITEM_LABELING", "prepOwner": "SELLER"}],
        })
        db.execute(
            "UPDATE fba_prep_sessions SET items_json = ?, amazon_state_json = ? WHERE id = 1",
            (json.dumps([{
                "seller_sku": "SKU-1", "quantity": 2, "title": "Test unit",
                "condition": "New",
            }]), json.dumps(workflow)),
        )
        db.commit()
        db.close()

        with (
            patch.object(ss_config, "BASE_DIR", self.base_dir),
            patch.object(ss_inventory_age, "_inventory_age_consume_fifo"),
            patch.object(ss_caching, "update_data_version"),
            patch.object(ss_caching, "_invalidate_searchrack_cache"),
            patch.object(printer_manager, "get_config_snapshot", return_value={
                "print_method": "escpos", "label_height": 30,
            }),
            patch.object(printer_manager, "get_connection_status", return_value={
                "can_print": True, "display_name": "Item Prep printer",
            }),
            patch.object(printer_manager, "print_barcode", return_value=True) as printer,
        ):
            packed = self.post_scan(token=token, label_printed=False)
            first = self.client.post(
                "/api/fba-prep/sessions/1/amazon/print-item-label",
                json={"msku": "SKU-1", "scan_token": token},
            )
            retry = self.client.post(
                "/api/fba-prep/sessions/1/amazon/print-item-label",
                json={"msku": "SKU-1", "scan_token": token},
            )

        self.assertEqual(packed.status_code, 200)
        self.assertEqual(first.status_code, 200)
        self.assertTrue(retry.get_json()["idempotent"])
        printer.assert_called_once()
        db = sqlite3.connect(self.base_dir / "searchRack.db")
        db.row_factory = sqlite3.Row
        self.assertEqual(db.execute(
            "SELECT label_printed FROM fba_pack_scans WHERE scan_token = ?", (token,)
        ).fetchone()[0], 1)
        state = json.loads(db.execute(
            "SELECT amazon_state_json FROM fba_prep_sessions WHERE id = 1"
        ).fetchone()[0])
        db.close()
        self.assertEqual(state["boxes"][0]["contents"][0]["quantity"], 1)


if __name__ == "__main__":
    unittest.main()
