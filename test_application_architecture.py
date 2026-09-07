"""Contracts captured before modularizing app.py, plus startup boundary checks."""
import ast
from importlib import import_module
import inspect
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault('DISABLE_BACKGROUND_SERVICES', '1')

from sweetshelves.bootstrap import app, initialize
from sweetshelves import config, database, integrations, runtime
from sweetshelves.legacy import resolve, SYMBOL_MODULES

ROOT = Path(__file__).resolve().parent
PACKAGE = ROOT / 'sweetshelves'
CONTRACT = json.loads((ROOT / 'tests/fixtures/application_contract.json').read_text(encoding='utf-8'))


class ApplicationArchitectureTests(unittest.TestCase):
    def test_every_existing_route_preserves_order_endpoint_and_methods(self):
        actual = [dict(rule=rule.rule, endpoint=rule.endpoint,
                       methods=sorted(rule.methods), defaults=rule.defaults,
                       strict_slashes=rule.strict_slashes, subdomain=rule.subdomain)
                  for rule in app.url_map.iter_rules()]
        self.assertEqual(actual, CONTRACT['routes'])

    def test_request_hooks_preserve_order(self):
        for kind, expected in CONTRACT['hooks'].items():
            with self.subTest(kind=kind):
                hooks = getattr(app, kind)
                groups = hooks.values() if isinstance(hooks, dict) else [hooks]
                actual = [function.__name__ for functions in groups
                          for function in functions]
                self.assertEqual(actual, expected)

    def test_legacy_imports_preserve_all_function_signatures(self):
        import app as entrypoint
        for name, signature in CONTRACT['signatures'].items():
            with self.subTest(function=name):
                function = getattr(entrypoint, name)
                self.assertIs(function, resolve(name))
                self.assertEqual(str(inspect.signature(function)), signature)

    def test_legacy_imported_dependencies_remain_available(self):
        import app as entrypoint
        import requests
        import token_manager
        self.assertIs(entrypoint.requests, requests)
        self.assertIs(entrypoint.get_access_token, token_manager.get_access_token)
        self.assertIs(entrypoint.BASE_DIR, config.BASE_DIR)
        with self.assertRaises(AttributeError):
            getattr(entrypoint, '_this_symbol_does_not_exist')

    def test_upload_error_handler_keeps_its_json_response(self):
        from werkzeug.exceptions import RequestEntityTooLarge
        from sweetshelves import errors
        handler = app.error_handler_spec[None][413][RequestEntityTooLarge]
        self.assertIs(handler, errors.handle_file_too_large)
        with app.test_request_context('/'):
            response = app.make_response(app.handle_user_exception(RequestEntityTooLarge()))
        self.assertEqual(response.status_code, 413)
        self.assertEqual(response.get_json(), {
            'success': False,
            'error': 'Upload too large. Try fewer photos or enable Low res.',
        })

    def test_feature_imports_do_not_initialize_databases_network_or_workers(self):
        # A fresh process is necessary; the suite already imported the app.
        # Block effects before importing any feature and use no bootstrap.
        script = '''
from importlib import import_module
from pathlib import Path
import socket, sqlite3, threading
from unittest.mock import patch
def forbidden(*args, **kwargs):
    raise AssertionError('Feature import attempted a startup side effect')
with patch.object(sqlite3, 'connect', forbidden), \\
     patch.object(threading.Thread, 'start', forbidden), \\
     patch.object(socket.socket, 'connect', forbidden):
    for path in sorted(Path('sweetshelves').glob('*.py')):
        if path.stem not in {'bootstrap', '__init__'}:
            import_module('sweetshelves.' + path.stem)
'''
        result = subprocess.run([sys.executable, '-c', script], cwd=ROOT,
                                env=dict(os.environ, PYTHONDONTWRITEBYTECODE='1'),
                                capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_internal_references_have_real_owners(self):
        count = 0
        for path in PACKAGE.glob('*.py'):
            for node in ast.walk(ast.parse(path.read_text(encoding='utf-8'))):
                if not (isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name)
                        and node.value.id.startswith('ss_')):
                    continue
                owner = node.value.id[3:]
                if owner == 'integrations' and node.attr in {'AmazonManager', 'process_bol_excel', 'process_bol_retail_backfill'}:
                    available = integrations.AMAZON_AVAILABLE if node.attr == 'AmazonManager' else integrations.BOL_AVAILABLE
                    if not available:
                        continue
                with self.subTest(file=path.name, line=node.lineno):
                    module = import_module(f'sweetshelves.{owner}')
                    self.assertTrue(hasattr(module, node.attr), f'{owner}.{node.attr}')
                    if node.attr in SYMBOL_MODULES:
                        self.assertEqual(SYMBOL_MODULES[node.attr], owner)
                count += 1
        self.assertGreater(count, 2000)

    def test_features_do_not_import_entrypoint_or_execute_source(self):
        for path in PACKAGE.glob('*.py'):
            tree = ast.parse(path.read_text(encoding='utf-8'))
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom):
                    self.assertNotIn(node.module, {'app', 'sweetshelves.bootstrap', 'bootstrap'}, path.name)
                elif isinstance(node, ast.Import):
                    self.assertFalse({'app', 'sweetshelves.bootstrap'} & {alias.name for alias in node.names}, path.name)
                elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                    self.assertNotIn(node.func.id, {'exec', 'eval'}, path.name)

    def test_repeated_initialize_does_not_register_routes_or_start_workers(self):
        with patch('sweetshelves.bootstrap.register_routes') as register, \
             patch('threading.Thread.start') as start:
            self.assertIs(initialize(), app)
            self.assertIs(initialize(), app)
        register.assert_not_called()
        start.assert_not_called()

    def test_templates_static_files_and_data_still_use_project_root(self):
        self.assertEqual(config.BASE_DIR, ROOT)
        self.assertEqual(Path(app.root_path), ROOT)
        self.assertEqual(Path(app.static_folder), ROOT / 'static')
        self.assertIs(runtime.app, app)
        self.assertEqual(app.config['MAX_CONTENT_LENGTH'], 64 * 1024 * 1024)

    def test_request_scoped_connections_close_at_teardown(self):
        with tempfile.TemporaryDirectory() as folder:
            path = str(Path(folder) / 'request.db')
            with app.app_context():
                first = database.get_db_connection(path)
                self.assertIs(first, database.get_db_connection(path))
                first.execute('SELECT 1')
            import sqlite3
            with self.assertRaises(sqlite3.ProgrammingError):
                first.execute('SELECT 1')

    def test_representative_pages_render_and_static_assets_revalidate(self):
        endpoints = {'tools', 'finder_page', 'listingagent', 'movelocation_page',
                     'ready_to_ship_page', 'shelfmanager', 'mail_center_page',
                     'label_master_page', 'fb_listings_page', 'financial_analytics'}
        rules = {rule.endpoint: rule.rule for rule in app.url_map.iter_rules()}
        # Endpoint names are deliberately selected from the real route contract.
        self.assertTrue(endpoints.issubset(rules), endpoints - rules.keys())
        client = app.test_client()
        with patch.object(config, 'DEBUG_MODE', False):
            for endpoint in sorted(endpoints):
                with self.subTest(endpoint=endpoint):
                    response = client.get(rules[endpoint])
                    self.assertEqual(response.status_code, 200)
                    self.assertIn(b'<html', response.data.lower())
            with client.get('/static/i18n.js') as response:
                self.assertEqual(response.status_code, 200)
                self.assertNotIn('immutable', response.headers.get('Cache-Control', ''))


if __name__ == '__main__':
    unittest.main()
