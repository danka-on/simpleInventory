"""Exercise the Lister packaging: reproducible, checksummed releases and the update page."""

import base64
import hashlib
import json
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from package_lister import ROOT, build_feed, package_extension  # noqa: E402


class ListerReleaseTests(unittest.TestCase):
    def setUp(self):
        temp_root = ROOT / 'build' / '.test-tmp'
        temp_root.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(prefix='lister update ', dir=temp_root)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.dist = self.root / 'built dist'
        self.dist.mkdir()
        (self.dist / 'manifest.json').write_text(
            json.dumps({'name': 'Sweet Shelves Lister', 'manifest_version': 3, 'version': '0.1.0'}))
        (self.dist / 'background.js').write_text("console.log('new build');")
        (self.dist / 'sidepanel.html').write_text('<html>New sidepanel</html>')
        (self.dist / 'icons').mkdir()
        (self.dist / 'icons' / 'icon16.png').write_bytes(b'png')
        self.feed = self.root / 'feed'
        self.metadata = package_extension(self.dist, self.feed)

    def test_reproducible_release_and_layout(self):
        first_zip = (self.feed / self.metadata['archive']).read_bytes()
        again = package_extension(self.dist, self.feed)
        self.assertEqual(self.metadata['release'], again['release'])
        self.assertEqual(first_zip, (self.feed / again['archive']).read_bytes())
        with zipfile.ZipFile(self.feed / again['archive']) as archive:
            self.assertEqual(set(archive.namelist()), set(self.metadata['files']))
        self.assertIn('icons/icon16.png', self.metadata['files'])
        (self.dist / 'background.js').write_text('newer')
        self.assertNotEqual(again['release'], package_extension(self.dist, self.feed)['release'])

    def test_browser_payload_matches_archive_and_file_checksums(self):
        payload = (self.feed / self.metadata['payload']).read_bytes()
        self.assertEqual(hashlib.sha256(payload).hexdigest(), self.metadata['payloadSha256'])
        decoded = json.loads(payload)
        self.assertEqual(set(decoded), set(self.metadata['files']))
        for name, data in decoded.items():
            self.assertEqual(base64.b64decode(data), (self.dist / name).read_bytes())

    def test_reject_incomplete_or_foreign_build(self):
        (self.dist / 'background.js').unlink()
        with self.assertRaisesRegex(ValueError, 'Missing extension build'):
            package_extension(self.dist, self.feed)
        (self.dist / 'background.js').write_text('back')
        (self.dist / 'manifest.json').write_text(json.dumps({'name': 'AmazingScout', 'manifest_version': 3, 'version': '1'}))
        with self.assertRaisesRegex(ValueError, 'not a Sweet Shelves Lister'):
            package_extension(self.dist, self.feed)

    def test_real_extension_folder_packages_with_update_page(self):
        output = self.root / 'real feed'
        result = build_feed(ROOT / 'lister-extension', output, ROOT / 'deploy' / 'lister')
        self.assertEqual(result['name'], 'Sweet Shelves Lister')
        page = (output / 'index.html').read_text(encoding='utf-8')
        self.assertIn(result['updaterModule'], page)
        self.assertNotIn('__UPDATER_MODULE__', page)
        self.assertTrue((output / result['updaterModule']).is_file())
        self.assertFalse((output / 'update.ps1').exists(), 'no Windows updater any more (it needed Tailscale)')
        for required in ('manifest.json', 'background.js', 'sidepanel.html', 'sidepanel.js', 'content.js', 'matcher.js'):
            self.assertIn(required, result['files'])
        self.assertFalse(any('__pycache__' in name for name in result['files']))


if __name__ == '__main__':
    unittest.main()
