"""Offline tests of the review fixes. All writes use temporary directories."""
from test_feature_support import isolated_functions, feature_overrides
from sweetshelves.legacy import resolve
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing, contextmanager
import datetime
import gzip
import io
import os
from pathlib import Path
import sqlite3
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import marketplace_manager
import ebay_manager
import rawbol_manager
import rotating_backup
import token_manager


ROOT = Path(__file__).resolve().parent


def app_functions(names, **scope):
    return isolated_functions(names, scope)


class StartupTests(unittest.TestCase):
    def test_indexes_accept_empty_directory(self):
        with tempfile.TemporaryDirectory() as folder:
            scope = app_functions({'create_database_indexes'}, BASE_DIR=Path(folder), sqlite3=sqlite3, print=lambda *a: None)
            scope['create_database_indexes']()
            self.assertEqual(list(Path(folder).iterdir()), [])

    def test_index_connection_failure_is_not_masked(self):
        with tempfile.TemporaryDirectory() as folder:
            (Path(folder) / 'bol.db').touch()
            db = SimpleNamespace(connect=Mock(side_effect=sqlite3.OperationalError('locked')))
            scope = app_functions({'create_database_indexes'}, BASE_DIR=Path(folder), sqlite3=db, print=lambda *a: None)
            scope['create_database_indexes']()

    def test_no_duplicate_route_methods(self):
        from sweetshelves.bootstrap import app
        seen = set()
        self.assertGreater(len(list(app.url_map.iter_rules())), 400)
        for rule in app.url_map.iter_rules():
            for method in rule.methods - {'OPTIONS', 'HEAD'}:
                self.assertNotIn((rule.rule, method), seen)
                seen.add((rule.rule, method))

    def test_no_tunnel_flag(self):
        launcher = Mock()
        scope = app_functions({'start_tunnel'}, os=os, subprocess=launcher)
        with patch.dict(os.environ, SWEETSHELVES_NO_TUNNEL='1'):
            scope['start_tunnel']()
        launcher.Popen.assert_not_called()

    def test_long_barcodes_are_never_rounded(self):
        normalize = app_functions({'_normalize_upc'})['_normalize_upc']
        for value in ('123456789012345678901', '123456789012345678901.0', '000123456789012345678901'):
            self.assertEqual(normalize(value), '123456789012345678901')
        for value, expected in ((None, ''), ('000123', '123'), ('123.000', '123'),
                                ('123.25', '123.25'), ('000123-2', '000123-2'), ('0', '0')):
            self.assertEqual(normalize(value), expected)

    def test_failed_print_queue_connection_returns_fallback(self):
        db = SimpleNamespace(connect=Mock(side_effect=sqlite3.OperationalError('locked')))
        scope = app_functions({'get_print_queue'}, sqlite3=db, print=lambda *a: None,
                              jsonify=lambda value: value, _safe_error=lambda e: 'Internal error',
                              _ensure_items_prep_tables=lambda: None)
        self.assertEqual(scope['get_print_queue'](), ({'success': False, 'error': 'Internal error'}, 500))
        db.connect.assert_called_once()


class BackupTests(unittest.TestCase):
    def test_backup_is_consistent_and_closes_files(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            database = root / 'warehouse-stock.db'
            with closing(sqlite3.connect(database)) as conn:
                conn.execute('PRAGMA journal_mode=WAL')
                conn.execute('CREATE TABLE SEARCHRACK (id INTEGER)')
                conn.execute('INSERT INTO SEARCHRACK VALUES (1)')
                conn.commit()
                first = rotating_backup.create_sqlite_backup(database, root / 'backups')
                second = rotating_backup.create_sqlite_backup(database, root / 'backups')
            self.assertNotEqual(first.output_path, second.output_path)
            self.assertEqual(first.row_count, 1)
            restored = root / 'restored.db'
            restored.write_bytes(gzip.decompress(first.output_path.read_bytes()))
            with closing(sqlite3.connect(restored)) as conn:
                self.assertEqual(conn.execute('PRAGMA integrity_check').fetchone()[0], 'ok')
                self.assertEqual(conn.execute('SELECT * FROM SEARCHRACK').fetchall(), [(1,)])
            self.assertEqual(len(list((root / 'backups').iterdir())), 2)
            with patch('sys.stdout', new_callable=io.StringIO) as output:
                rotating_backup.list_backups(root / 'backups', ['warehouse-stock.db'])
            self.assertIn(first.output_path.name, output.getvalue())

    def test_failed_compression_does_not_publish_backup(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            with closing(sqlite3.connect(root / 'test.db')):
                pass
            with patch.object(rotating_backup.shutil, 'copyfileobj', side_effect=OSError('disk full')):
                with self.assertRaises(OSError):
                    rotating_backup.create_sqlite_backup(root / 'test.db', root / 'backups')
            self.assertEqual(list((root / 'backups').iterdir()), [])

    def test_invalid_retention_cannot_delete_backups(self):
        with tempfile.TemporaryDirectory() as folder:
            backup = Path(folder) / 'test-1.sqlite3.gz'
            backup.touch()
            with self.assertRaises(ValueError):
                rotating_backup.prune_old_backups(Path(folder), 'test', 0)
            self.assertTrue(backup.exists())


class TokenTests(unittest.TestCase):
    def test_refresh_is_serialized_and_has_timeout(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(token_manager, 'TOKEN_FILE', str(Path(folder) / 'tokens.json')):
            token_manager.save_tokens({'refresh_token': 'test-refresh', 'expires_at': 0})
            response = Mock(status_code=200)
            response.json.return_value = {'access_token': 'test-access', 'expires_in': 3600}
            with patch.object(token_manager.requests, 'post', return_value=response) as post:
                with ThreadPoolExecutor(max_workers=8) as pool:
                    results = list(pool.map(lambda _: token_manager.get_access_token(), range(16)))
            self.assertEqual(results, ['test-access'] * 16)
            self.assertEqual(post.call_count, 1)
            self.assertEqual(post.call_args.kwargs['timeout'], 30)

    def test_failed_save_preserves_previous_credentials(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(token_manager, 'TOKEN_FILE', str(Path(folder) / 'tokens.json')):
            token_manager.save_tokens({'access_token': 'old'})
            with patch.object(token_manager.json, 'dump', side_effect=OSError('disk full')):
                with self.assertRaises(OSError):
                    token_manager.save_tokens({'access_token': 'new'})
            self.assertEqual(token_manager.load_tokens(), {'access_token': 'old'})
            self.assertEqual(len(list(Path(folder).iterdir())), 1)

    def test_refresh_before_expiry(self):
        with patch.object(token_manager.time, 'time', return_value=100):
            self.assertTrue(token_manager.is_expired({'expires_at': 150}))
            self.assertFalse(token_manager.is_expired({'expires_at': 200}))


class DatabaseTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.root = Path(self.folder.name)
        for module in (marketplace_manager, rawbol_manager):
            patcher = patch.object(module, 'BASE_DIR', self.root)
            patcher.start()
            self.addCleanup(patcher.stop)

    @contextmanager
    def connect(self, name):
        with closing(sqlite3.connect(self.root / name)) as conn:
            with conn:
                yield conn

    def seed_bol(self, *, synced=False, raw_qty=3):
        rawbol_manager.ensure_rawbol_db()
        with self.connect('rawbol.db') as conn:
            conn.execute("INSERT INTO raw_bol_items(upc,quantity,lot_number) VALUES ('123',?,'LOT')", (raw_qty,))
            if synced:
                conn.execute("INSERT INTO synced_lots(lot_number) VALUES ('LOT')")
        with self.connect('bol.db') as conn:
            # A legacy schema must work without optional prep columns.
            conn.execute('CREATE TABLE bol_items(id INTEGER PRIMARY KEY,upc TEXT,quantity INTEGER,lot_number TEXT,item_description TEXT,image_url TEXT,import_date TEXT)')
            conn.execute("INSERT INTO bol_items(id,upc,quantity,lot_number) VALUES (1,'123',10,'LOT')")

    def quantity(self):
        with self.connect('bol.db') as conn:
            return conn.execute('SELECT quantity FROM bol_items WHERE id=1').fetchone()[0]

    def test_deleting_unsynced_lot_keeps_bol_quantity(self):
        self.seed_bol()
        result = rawbol_manager.delete_lot('LOT')
        self.assertTrue(result['success'], result)
        self.assertEqual(self.quantity(), 10)

    def test_deleting_synced_lot_subtracts_once(self):
        self.seed_bol(synced=True)
        result = rawbol_manager.delete_lot('LOT')
        self.assertTrue(result['success'], result)
        self.assertEqual(self.quantity(), 7)

    def test_desync_ignores_unsynced_lots(self):
        self.seed_bol()
        result = rawbol_manager.desync_all_rawbol()
        self.assertTrue(result['success'], result)
        self.assertEqual(self.quantity(), 10)

    def test_zero_quantity_sync_does_not_invent_stock(self):
        self.seed_bol(raw_qty=0)
        result = rawbol_manager.sync_rawbol_to_bol('LOT')
        self.assertTrue(result['success'], result)
        self.assertEqual(self.quantity(), 10)

    def test_sync_failure_rolls_back_quantities_and_marker(self):
        self.seed_bol()
        with self.connect('rawbol.db') as conn:
            conn.execute("CREATE TRIGGER fail_sync BEFORE INSERT ON sync_history BEGIN SELECT RAISE(ABORT, 'test failure'); END")
        result = rawbol_manager.sync_rawbol_to_bol('LOT')
        self.assertFalse(result['success'])
        self.assertEqual(self.quantity(), 10)
        with self.connect('rawbol.db') as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM synced_lots').fetchone()[0], 0)

    def test_concurrent_sync_does_not_double_quantity(self):
        self.seed_bol()
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: rawbol_manager.sync_rawbol_to_bol('LOT'), range(2)))
        self.assertEqual(sum(bool(result['success']) for result in results), 1, results)
        self.assertEqual(self.quantity(), 13)

    def test_sync_lookup_uses_composite_index(self):
        self.seed_bol()
        self.assertTrue(rawbol_manager.sync_rawbol_to_bol('LOT')['success'])
        with self.connect('bol.db') as conn:
            plan = conn.execute("EXPLAIN QUERY PLAN SELECT id FROM bol_items WHERE upc = ? COLLATE NOCASE AND lot_number = ? COLLATE NOCASE ORDER BY import_date DESC, id DESC LIMIT 1", ('123', 'LOT')).fetchall()
        self.assertIn('idx_bol_upc_lot_sync', str(plan))
        self.assertNotIn('SCAN bol_items', str(plan))

    def test_rapid_data_updates_have_distinct_versions(self):
        # Install dependencies once, then run the real functions concurrently.
        get_version, update_version = resolve('get_data_version'), resolve('update_data_version')
        with feature_overrides(dict(connect_db=self.connect,
                                    time=SimpleNamespace(time=lambda: 100), print=lambda *a: None)):
            first = get_version()
            with ThreadPoolExecutor(max_workers=4) as pool:
                list(pool.map(lambda _: update_version(), range(8)))
            self.assertEqual(get_version(), first + 8)

    def sale_route(self, fail=False):
        from flask import Flask, request, jsonify
        with self.connect('sold.db') as conn:
            conn.execute('CREATE TABLE orders(id INTEGER PRIMARY KEY,order_id TEXT,barcode TEXT,title TEXT,price REAL,paid_time TEXT,store TEXT,quantity INTEGER,isHandled TEXT,isHandledDate TEXT,rackupdated INTEGER)')
            conn.execute('CREATE TABLE returns(id INTEGER,barcode TEXT,relisted INTEGER,resold INTEGER,relisted_date TEXT,relisted_store TEXT,relisted_item_id TEXT)')
            if fail:
                conn.execute("CREATE TRIGGER fail_sale BEFORE INSERT ON orders BEGIN SELECT RAISE(ABORT, 'test failure'); END")
        remove = Mock(return_value={'removed': True, 'removed_units': 3})
        scope = app_functions({'api_marketplace_sale'}, request=request, jsonify=jsonify, sqlite3=sqlite3,
            datetime=datetime, BASE_DIR=self.root, db_connection=self.connect,
            _coerce_int=lambda value, default: int(value or default),
            _bulk_manifest_money=lambda value, default: float(value or default),
            _marketplace_sale_store_key=lambda value: 'marketplace',
            _marketplace_lookup_rawbol_item=lambda barcode: None,
            _build_marketplace_removal_plan=lambda *a: {'success': True, 'locations': []},
            _ensure_order_removal_allocations_table=lambda *a: None,
            _save_order_removal_allocations=lambda *a: None,
            _apply_marketplace_removal_plan=remove,
            _safe_error=lambda *a: 'Internal error')
        app = Flask(__name__)
        app.add_url_rule('/sale', view_func=scope['api_marketplace_sale'], methods=['POST'])
        response = app.test_client().post('/sale', json={'barcode': '123', 'title': 'Item', 'quantity': 3, 'price': 12})
        return response, remove

    def test_sale_route_records_stable_order_reference(self):
        response, remove = self.sale_route()
        self.assertEqual(response.status_code, 200, response.get_json())
        with self.connect('sold.db') as conn:
            self.assertEqual(conn.execute('SELECT order_id,quantity FROM orders').fetchall(), [('marketplace-1', 3)])
        remove.assert_called_once()

    def test_failed_sale_ledger_does_not_remove_inventory(self):
        response, remove = self.sale_route(fail=True)
        self.assertEqual(response.status_code, 500)
        remove.assert_not_called()
        with self.connect('marketplace.db') as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM marketplace_sales').fetchone()[0], 0)

    def seed_sale(self, reference=True, duplicate=False):
        result = marketplace_manager.add_marketplace_sale('123', 'Item', 3, 12)
        self.assertTrue(result['success'], result)
        with self.connect('sold.db') as conn:
            conn.execute('CREATE TABLE orders(id INTEGER PRIMARY KEY,order_id TEXT,barcode TEXT,title TEXT,quantity INTEGER,price REAL,paid_time TEXT,store TEXT)')
            today = datetime.date.today().isoformat()
            conn.execute("INSERT INTO orders VALUES (1,?,'123','Item',3,12,?,'marketplace')", (f"marketplace-{result['id']}" if reference else None, today))
            conn.execute("INSERT INTO orders VALUES (2,NULL,'123','Item',?,12,?,'marketplace')", (3 if duplicate else 1, today))
        return result['id']

    def test_ebay_order_fee_is_allocated_once_across_lines(self):
        with self.connect('sold.db') as conn:
            conn.execute('CREATE TABLE orders(id INTEGER PRIMARY KEY,order_id TEXT,store TEXT,price REAL,quantity INTEGER,seller_fee REAL)')
            conn.executemany('INSERT INTO orders VALUES (?,?,?,?,?,NULL)', [
                (1, 'multi', 'ebay', 10, 2), (2, 'multi', 'ebay', 10, 1),
                (3, 'multi', 'amazon', 10, 1), (4, 'free', 'ebay', 0, 1),
                (5, 'free', 'ebay', 0, 1), (6, 'free', 'ebay', 0, 1)])
        manager = ebay_manager.EbayManager()
        with patch.object(ebay_manager, 'connect_db', self.connect), patch.object(manager, 'get_order_fees', return_value={'multi': 3, 'free': 0.01}):
            self.assertEqual(manager.sync_fees_to_db(), 2)
        with self.connect('sold.db') as conn:
            rows = conn.execute('SELECT id,seller_fee FROM orders ORDER BY id').fetchall()
        self.assertEqual(rows[:3], [(1, 2), (2, 1), (3, None)])
        self.assertAlmostEqual(sum(row[1] for row in rows[3:]), .01)

    def test_delete_exact_sale_preserves_other_same_day_orders(self):
        sale_id = self.seed_sale()
        result = marketplace_manager.delete_marketplace_sale(sale_id)
        self.assertTrue(result['success'], result)
        self.assertEqual(result['deleted_from_sold'], 1)
        with self.connect('sold.db') as conn:
            self.assertEqual(conn.execute('SELECT id FROM orders').fetchall(), [(2,)])

    def test_delete_legacy_sale_removes_one_order_not_quantity_rows(self):
        result = marketplace_manager.delete_marketplace_sale(self.seed_sale(reference=False))
        self.assertTrue(result['success'], result)
        self.assertEqual(result['deleted_from_sold'], 1)

    def test_ambiguous_legacy_sale_preserves_both_databases(self):
        result = marketplace_manager.delete_marketplace_sale(self.seed_sale(reference=False, duplicate=True))
        self.assertFalse(result['success'])
        with self.connect('sold.db') as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM orders').fetchone()[0], 2)
        with self.connect('marketplace.db') as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM marketplace_sales').fetchone()[0], 1)

    def test_sale_delete_failure_rolls_back_order_deletion(self):
        sale_id = self.seed_sale()
        with self.connect('marketplace.db') as conn:
            conn.execute("CREATE TRIGGER fail_delete BEFORE DELETE ON marketplace_sales BEGIN SELECT RAISE(ABORT, 'test failure'); END")
        self.assertFalse(marketplace_manager.delete_marketplace_sale(sale_id)['success'])
        with self.connect('sold.db') as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM orders').fetchone()[0], 2)


if __name__ == '__main__':
    unittest.main()
