"""Run offline regression tests against a disposable copy, never live databases.

Usage: python tools/run_checks.py [--javascript] [unittest module names ...]
The manual marketplace/API diagnostic scripts are deliberately not discovered.
"""
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[1]
TESTS = [
    'test_ebay_mapping', 'test_finder_aliases', 'test_finder_match_learning',
    'test_finder_records', 'test_finder_search', 'test_finder_trail', 'test_listing_similar',
    'test_warehouse_identity', 'test_fba_inbound', 'test_fba_count_scan',
    'test_fba_pack_scan', 'test_fba_label_scanner', 'test_fba_item_label_route',
    'test_codebase_regressions', 'test_token_expiry', 'test_merge_regressions',
    'test_application_architecture', 'test_fba_attention', 'test_amazon_resolution',
    'test_receiving_batch', 'test_custom_item_identity', 'test_warehouse_nameless',
]


def main():
    arguments = list(sys.argv[1:])
    javascript = '--javascript' in arguments
    if javascript:
        arguments.remove('--javascript')
    with tempfile.TemporaryDirectory(prefix='sweetshelves-checks-') as folder:
        destination = Path(folder)
        for source in ROOT.glob('*.py'):
            shutil.copy2(source, destination / source.name)
        shutil.copytree(ROOT / 'sweetshelves', destination / 'sweetshelves',
                        ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
        if (ROOT / 'tests' / 'fixtures').is_dir():
            shutil.copytree(ROOT / 'tests' / 'fixtures', destination / 'tests' / 'fixtures')
        shutil.copytree(ROOT / 'templates', destination / 'templates')
        (destination / 'static').mkdir()
        for source in (ROOT / 'static').iterdir():
            if source.suffix in {'.js', '.css'}:
                shutil.copy2(source, destination / 'static' / source.name)
        # dotenv can otherwise walk up to a user's .env outside this checkout.
        (destination / '.env').write_text('', encoding='utf-8')
        env = dict(os.environ, DISABLE_BACKGROUND_SERVICES='1',
                   SWEETSHELVES_NO_TUNNEL='1', PYTHONDONTWRITEBYTECODE='1',
                   PYTHONUTF8='1')
        result = subprocess.run(
            [sys.executable, '-m', 'unittest', *(arguments or TESTS)],
            cwd=destination, env=env,
        )
        if result.returncode or not javascript:
            return result.returncode
        (destination / 'tools').mkdir()
        shutil.copy2(ROOT / 'tools' / 'check_js_syntax.cjs', destination / 'tools' / 'check_js_syntax.cjs')
        for source in ROOT.glob('test_*.cjs'):
            shutil.copy2(source, destination / source.name)
        scripts = ['tools/check_js_syntax.cjs'] + [source.name for source in sorted(ROOT.glob('test_*.cjs'))]
        for script in scripts:
            result = subprocess.run(['node', script], cwd=destination, env=env)
            if result.returncode:
                return result.returncode
        return 0


if __name__ == '__main__':
    raise SystemExit(main())
