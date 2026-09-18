"""tools/release_lister.py against a throwaway repository: a bare "GitHub" and a working clone.

The rules it holds for many agent sessions sharing one worktree: the version comes from the live
feed, a release commit only lands on top of what GitHub has, and a publish never takes back out a
change the live feed already shipped.
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

TOOL = Path(__file__).resolve().parent / 'release_lister.py'
BRANCH = 'codex/main-working-project'
MANIFEST = '{\n  "manifest_version": 3,\n  "name": "Sweet Shelves Lister",\n  "version": "0.2.58",\n  "permissions": []\n}\n'


def run(cwd, *args):
    result = subprocess.run(list(args), cwd=cwd, capture_output=True, text=True)
    if result.returncode != 0:
        raise AssertionError(f"{' '.join(args)}: {result.stderr}")
    return result.stdout.strip()


class ReleaseListerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.tmp.cleanup)
        base = Path(self.tmp.name)
        self.remote = base / 'github.git'
        self.work = base / 'work'
        run(base, 'git', 'init', '--bare', '-q', str(self.remote))
        run(base, 'git', 'init', '-q', '-b', BRANCH, str(self.work))
        for key, value in (('user.name', 'Test'), ('user.email', 'test@example.com'), ('core.autocrlf', 'false')):
            run(self.work, 'git', 'config', key, value)
        (self.work / 'lister-extension').mkdir()
        (self.work / 'lister-extension' / 'manifest.json').write_text(MANIFEST, encoding='utf-8', newline='\n')
        (self.work / 'lister-extension' / 'sidepanel.js').write_text('// panel\n', encoding='utf-8', newline='\n')
        run(self.work, 'git', 'add', '-A')
        run(self.work, 'git', 'commit', '-q', '-m', 'start')
        run(self.work, 'git', 'push', '-q', str(self.remote), BRANCH)

    def release(self, *args):
        env = {**os.environ, 'LISTER_RELEASE_ROOT': str(self.work), 'LISTER_RELEASE_REMOTE': str(self.remote),
               'LISTER_RELEASE_BRANCH': BRANCH}
        result = subprocess.run([sys.executable, str(TOOL), *args], cwd=self.work, capture_output=True, text=True, env=env)
        return json.loads(result.stdout.strip().splitlines()[-1])

    def feed(self, **fields):
        path = Path(self.tmp.name) / 'latest.json'
        path.write_text(json.dumps(fields), encoding='utf-8')
        return str(path)

    def head(self):
        return run(self.work, 'git', 'rev-parse', 'HEAD')

    def test_the_version_is_the_live_feed_plus_one_and_the_release_commit_is_pushed(self):
        # Another session's unfinished edit sits in the extension folder, staged and on disk.
        manifest = self.work / 'lister-extension' / 'manifest.json'
        manifest.write_text(MANIFEST.replace('"permissions": []', '"permissions": ["tabs"]'), encoding='utf-8', newline='\n')
        (self.work / 'lister-extension' / 'fb-inbox.js').write_text('// wip\n', encoding='utf-8')
        run(self.work, 'git', 'add', 'lister-extension/manifest.json')
        check = self.release('check', '--feed', self.feed(version='0.2.60'))
        self.assertTrue(check['ok'], check)
        self.assertEqual(check['version'], '0.2.61', 'the feed is ahead of the manifest: its number wins')
        self.assertIn('lister-extension/fb-inbox.js', check['uncommitted'])
        # The export is HEAD, not the folder: no unfinished file, and the new version.
        out = Path(self.tmp.name) / 'export'
        self.assertTrue(self.release('export', '--version', '0.2.61', '--out', str(out))['ok'])
        self.assertFalse((out / 'lister-extension' / 'fb-inbox.js').exists())
        self.assertIn('"version": "0.2.61"', (out / 'lister-extension' / 'manifest.json').read_text())
        self.assertIn('"permissions": []', (out / 'lister-extension' / 'manifest.json').read_text())

        base = self.head()
        done = self.release('commit', '--version', '0.2.61', '--base', base)
        self.assertTrue(done['ok'], done)
        self.assertEqual(self.head(), done['commit'])
        self.assertEqual(run(self.remote, 'git', 'rev-parse', BRANCH), done['commit'], 'on GitHub before any upload')
        self.assertEqual(run(self.work, 'git', 'show', f'{done["commit"]}:lister-extension/manifest.json'), MANIFEST.replace('0.2.58', '0.2.61').strip())
        self.assertEqual(run(self.work, 'git', 'diff', '--name-only', base, done['commit']), 'lister-extension/manifest.json', 'the version line only')
        # The other session keeps its edit (staged copy and file), now carrying the new version.
        on_disk = manifest.read_text(encoding='utf-8')
        self.assertIn('"tabs"', on_disk)
        self.assertIn('"version": "0.2.58"', run(self.work, 'git', 'show', ':lister-extension/manifest.json'), 'their staged copy is left alone')
        self.assertIn('"version": "0.2.61"', on_disk)

    def test_the_manifest_wins_when_it_is_ahead_of_the_feed(self):
        self.assertEqual(self.release('check', '--feed', self.feed(version='0.2.50'))['version'], '0.2.59')
        self.assertEqual(self.release('check', '--feed', self.feed())['version'], '0.2.59', 'no feed yet')

    def test_a_worktree_behind_github_may_not_release(self):
        other = Path(self.tmp.name) / 'other'
        run(Path(self.tmp.name), 'git', 'clone', '-q', '-b', BRANCH, str(self.remote), str(other))
        run(other, 'git', 'config', 'user.name', 'Peer'); run(other, 'git', 'config', 'user.email', 'peer@example.com')
        (other / 'lister-extension' / 'sidepanel.js').write_text('// peer change\n', encoding='utf-8')
        run(other, 'git', 'commit', '-qam', 'peer')
        run(other, 'git', 'push', '-q', 'origin', BRANCH)
        answer = self.release('check', '--feed', self.feed(version='0.2.58'))
        self.assertFalse(answer['ok'])
        self.assertIn('Pull or rebase', answer['error'])

    def test_a_head_without_the_live_feeds_commit_may_not_release(self):
        run(self.work, 'git', 'checkout', '-q', '-b', 'side')
        (self.work / 'lister-extension' / 'sidepanel.js').write_text('// shipped from elsewhere\n', encoding='utf-8')
        run(self.work, 'git', 'commit', '-qam', 'side release')
        shipped = self.head()
        run(self.work, 'git', 'checkout', '-q', BRANCH)
        answer = self.release('check', '--feed', self.feed(version='0.2.59', commit=shipped))
        self.assertFalse(answer['ok'])
        self.assertIn('does not contain', answer['error'])
        # Once HEAD has it, the release may go ahead.
        run(self.work, 'git', 'merge', '-q', 'side')
        self.assertTrue(self.release('check', '--feed', self.feed(version='0.2.59', commit=shipped))['ok'])

    def test_a_commit_made_while_testing_stops_the_release(self):
        base = self.head()
        (self.work / 'lister-extension' / 'sidepanel.js').write_text('// landed meanwhile\n', encoding='utf-8')
        run(self.work, 'git', 'commit', '-qam', 'meanwhile')
        answer = self.release('commit', '--version', '0.2.59', '--base', base)
        self.assertFalse(answer['ok'])
        self.assertIn('publish again', answer['error'])

    def test_a_push_github_refuses_rolls_the_branch_back(self):
        other = Path(self.tmp.name) / 'other'
        run(Path(self.tmp.name), 'git', 'clone', '-q', '-b', BRANCH, str(self.remote), str(other))
        run(other, 'git', 'config', 'user.name', 'Peer'); run(other, 'git', 'config', 'user.email', 'peer@example.com')
        (other / 'lister-extension' / 'sidepanel.js').write_text('// raced\n', encoding='utf-8')
        run(other, 'git', 'commit', '-qam', 'raced')
        run(other, 'git', 'push', '-q', 'origin', BRANCH)
        base = self.head()
        answer = self.release('commit', '--version', '0.2.59', '--base', base)
        self.assertFalse(answer['ok'])
        self.assertIn('someone pushed first', answer['error'])
        self.assertEqual(self.head(), base, 'the local branch is back where it was')


if __name__ == '__main__':
    unittest.main()
