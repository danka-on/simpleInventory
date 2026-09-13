"""Exercise the Lister packaging and the real Windows PowerShell updater with a local transport."""

import base64
import hashlib
import json
import os
import shutil
import subprocess
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
        self.target = self.root / 'existing extension'
        shutil.copytree(self.dist, self.target)
        (self.target / 'background.js').write_text("console.log('old build');")
        (self.target / 'icons' / 'stale.png').write_bytes(b'old')
        self.old_files = self.files(self.target)

    @staticmethod
    def files(folder):
        return {p.relative_to(folder).as_posix(): p.read_bytes() for p in folder.rglob('*') if p.is_file()}

    def save_metadata(self):
        (self.feed / 'latest.json').write_text(json.dumps(self.metadata))

    def run_updater(self, mode='normal', extra=''):
        powershell = shutil.which('powershell.exe')
        if not powershell:
            self.skipTest('Windows PowerShell is required for the updater integration tests')
        wrapper = self.root / 'run-updater.ps1'
        wrapper.write_text(
            """
$ErrorActionPreference = 'Stop'
function Invoke-RestMethod {
    param($Uri, $TimeoutSec)
    if ($env:UPDATE_TEST_MODE -eq 'offline') { throw 'Simulated offline server' }
    Get-Content -LiteralPath (Join-Path $env:UPDATE_TEST_FEED 'latest.json') -Raw | ConvertFrom-Json
}
function Invoke-WebRequest {
    param([switch]$UseBasicParsing, $Uri, $OutFile, $TimeoutSec)
    $releasePath = ([Uri]$Uri).AbsolutePath.Substring('/lister/'.Length)
    Copy-Item -LiteralPath (Join-Path $env:UPDATE_TEST_FEED $releasePath) -Destination $OutFile
}
function Get-Process {
    [CmdletBinding()]param($Name)
    if ($env:UPDATE_TEST_MODE -eq 'browser-running') { [pscustomobject]@{ Name = 'chrome' } }
}
function Move-Item {
    [CmdletBinding()]param($LiteralPath, $Destination)
    if ($env:UPDATE_TEST_MODE -eq 'replacement-fails' -and (Split-Path -Leaf $LiteralPath) -eq 'payload') {
        throw 'Simulated replacement failure'
    }
    Microsoft.PowerShell.Management\\Move-Item @PSBoundParameters
}
& $env:UPDATE_TEST_SCRIPT -ExtensionDir $env:UPDATE_TEST_TARGET -Scheduled
if ($LASTEXITCODE) { exit $LASTEXITCODE }
""".replace('-Scheduled\n', extra + '\n' if extra else '-Scheduled\n'),
            encoding='utf-8-sig',
        )
        env = os.environ | {
            'UPDATE_TEST_MODE': mode,
            'UPDATE_TEST_FEED': str(self.feed),
            'UPDATE_TEST_TARGET': str(self.target),
            'UPDATE_TEST_SCRIPT': str(ROOT / 'deploy' / 'lister' / 'update.ps1'),
            'LOCALAPPDATA': str(self.root / 'appdata'),
            'PSModulePath': str(Path(os.environ['SystemRoot']) / 'System32' / 'WindowsPowerShell' / 'v1.0' / 'Modules'),
        }
        result = subprocess.run(
            [powershell, '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', str(wrapper)],
            env=env, capture_output=True, text=True, timeout=60, check=False,
        )
        result.stderr = ' '.join(result.stderr.split())
        return result

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
        self.assertTrue((output / 'update.ps1').is_file())
        for required in ('manifest.json', 'background.js', 'sidepanel.html', 'sidepanel.js', 'content.js', 'matcher.js'):
            self.assertIn(required, result['files'])
        self.assertFalse(any('__pycache__' in name for name in result['files']))

    def test_update_replaces_all_files_and_removes_stale_ones(self):
        result = self.run_updater()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.files(self.target), self.files(self.dist))
        self.assertFalse(list(self.root.glob('.sweetshelves-lister-update-*')))
        result = self.run_updater()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('is current', result.stdout)

    def test_first_install(self):
        self.target = self.root / 'new install' / 'extension'
        result = self.run_updater()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.files(self.target), self.files(self.dist))

    def test_browser_running_defers_without_changing_files(self):
        result = self.run_updater('browser-running')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('Waiting', result.stdout)
        self.assertEqual(self.files(self.target), self.old_files)

    def test_bad_archive_hash_preserves_old_extension(self):
        (self.feed / self.metadata['archive']).write_bytes(b'broken download')
        result = self.run_updater()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('checksum failed', result.stderr)
        self.assertEqual(self.files(self.target), self.old_files)

    def test_unsafe_path_rejected_before_download(self):
        self.metadata['files']['../escape.js'] = '0' * 64
        self.save_metadata()
        result = self.run_updater()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('unsafe file name', result.stderr)
        self.assertEqual(self.files(self.target), self.old_files)

    def test_offline_preserves_old_extension_and_logs(self):
        result = self.run_updater('offline')
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.files(self.target), self.old_files)
        self.assertTrue((self.root / 'appdata' / 'SweetShelvesLister' / 'updater' / 'last-error.log').is_file())

    def test_failed_replacement_rolls_back(self):
        result = self.run_updater('replacement-fails')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Simulated replacement failure', result.stderr)
        self.assertEqual(self.files(self.target), self.old_files)

    def test_unrelated_destination_refused(self):
        (self.target / 'manifest.json').write_text(json.dumps({'name': 'AmazingScout', 'manifest_version': 3}))
        before = self.files(self.target)
        result = self.run_updater()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('different application', result.stderr)
        self.assertEqual(self.files(self.target), before)

    def test_manual_setup_saves_settings(self):
        result = self.run_updater(extra="-ServerUrl 'https://debby.taila97a84.ts.net'")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.files(self.target), self.files(self.dist))
        settings = self.root / 'appdata' / 'SweetShelvesLister' / 'updater' / 'settings.json'
        self.assertTrue(settings.is_file())
        saved = json.loads(settings.read_text(encoding='utf-8-sig'))
        self.assertEqual(saved['serverUrl'], 'https://debby.taila97a84.ts.net')
        self.assertTrue((self.root / 'appdata' / 'SweetShelvesLister' / 'Update Sweet Shelves Lister.cmd').is_file())


if __name__ == '__main__':
    unittest.main()
