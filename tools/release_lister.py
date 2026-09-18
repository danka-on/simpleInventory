"""Release bookkeeping for the Sweet Shelves Lister, run by tools/publish-lister.ps1.

Several agent sessions edit the extension in one shared worktree, so a release never trusts the
working folder: it is built from a commit, gets the next version number from the live feed, and
that commit is on GitHub before a single file is uploaded.

    check   --feed latest.json            refuse if HEAD is behind GitHub or lacks the feed's commit;
                                          print the next version and list uncommitted extension files
    export  --version V --out DIR         HEAD's tree in DIR, manifest.json saying V (for tests + packaging)
    commit  --version V --base SHA        the one-line release commit on top of SHA, pushed (fast-forward only)

Agents never bump manifest.json by hand: the version is max(feed, HEAD manifest) + 1.
"""

import argparse
import io
import json
import os
import re
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = 'lister-extension/manifest.json'
# Overridable so tools/test_release_lister.py can run a release against a throwaway repository.
ROOT = Path(os.environ.get('LISTER_RELEASE_ROOT') or ROOT)
BRANCH = os.environ.get('LISTER_RELEASE_BRANCH') or 'codex/main-working-project'
REMOTE = os.environ.get('LISTER_RELEASE_REMOTE') or 'https://github.com/danka-on/simpleInventory.git'
VERSION_RE = re.compile(r'("version"\s*:\s*")([0-9]+(?:\.[0-9]+)*)(")')


class ReleaseError(Exception):
    pass


def git(*args, env=None, input_bytes=None, check=True):
    result = subprocess.run(['git', *args], cwd=ROOT, capture_output=True, input=input_bytes,
                            env={**os.environ, **(env or {})})
    if check and result.returncode != 0:
        raise ReleaseError(f"git {' '.join(args)} failed: {result.stderr.decode('utf-8', 'replace').strip()}")
    return result


def out(*args, **kw):
    return git(*args, **kw).stdout.decode('utf-8', 'replace').strip()


def parse_version(text):
    parts = [int(p) for p in str(text or '0').split('.') if p.isdigit()]
    return tuple(parts) or (0,)


def next_version(feed_version, manifest_version):
    top = max(parse_version(feed_version), parse_version(manifest_version))
    top = (list(top) + [0, 0, 0])[:max(3, len(top))]
    top[-1] += 1
    return '.'.join(str(p) for p in top)


def manifest_version(rev='HEAD'):
    text = out('show', f'{rev}:{MANIFEST}')
    match = VERSION_RE.search(text)
    if not match:
        raise ReleaseError('manifest.json has no version')
    return match.group(2)


def with_version(text, version):
    new, count = VERSION_RE.subn(lambda m: m.group(1) + version + m.group(3), text, count=1)
    if count != 1:
        raise ReleaseError('manifest.json has no version to set')
    return new


def current_branch():
    return out('rev-parse', '--abbrev-ref', 'HEAD')


def is_ancestor(older, newer):
    return git('merge-base', '--is-ancestor', older, newer, check=False).returncode == 0


def check(feed_path):
    if current_branch() != BRANCH:
        raise ReleaseError(f'Release from {BRANCH}; this worktree is on {current_branch()}.')
    feed = json.loads(Path(feed_path).read_text(encoding='utf-8')) if feed_path and Path(feed_path).exists() else {}
    head = out('rev-parse', 'HEAD')
    git('fetch', '--quiet', REMOTE, BRANCH)
    remote = out('rev-parse', 'FETCH_HEAD')
    if not is_ancestor(remote, head):
        raise ReleaseError(f'GitHub has commits this worktree lacks ({remote[:9]}). Pull or rebase first; nothing published.')
    feed_commit = feed.get('commit') or ''
    if feed_commit:
        if git('cat-file', '-e', feed_commit + '^{commit}', check=False).returncode != 0 or not is_ancestor(feed_commit, head):
            raise ReleaseError(f"The live feed ({feed.get('version')}) was built from {feed_commit[:9]}, which HEAD does not contain. "
                               'Publishing now would take changes back out of the extension; nothing published.')
    # Not out(): its strip() would eat the status column's leading space and the path's first letter.
    porcelain = git('status', '--porcelain', '--', 'lister-extension').stdout.decode('utf-8', 'replace')
    dirty = [line[3:] for line in porcelain.splitlines() if line.strip()]
    return {'head': head, 'feedVersion': feed.get('version') or '', 'feedCommit': feed_commit,
            'manifestVersion': manifest_version('HEAD'),
            'version': next_version(feed.get('version'), manifest_version('HEAD')), 'uncommitted': dirty}


def export(version, dest, rev='HEAD'):
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    archive = git('archive', '--format=tar', rev).stdout
    with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
        tar.extractall(dest, filter='data')
    manifest = dest / MANIFEST
    raw = manifest.read_bytes().decode('utf-8')
    manifest.write_bytes(with_version(raw, version).encode('utf-8'))
    return {'export': str(dest), 'version': version}


def commit(version, base):
    """Commit the new version on top of base without touching the shared index, then push it."""
    ref = f'refs/heads/{BRANCH}'
    if out('rev-parse', ref) != base:
        raise ReleaseError('Someone committed while the release was being tested; publish again. Nothing published.')
    old_blob = out('rev-parse', f'{base}:{MANIFEST}')
    old_text = git('cat-file', 'blob', old_blob).stdout.decode('utf-8')
    new_text = with_version(old_text, version)
    if new_text == old_text:
        return {'commit': base, 'pushed': True}
    new_blob = git('hash-object', '-w', '--stdin', input_bytes=new_text.encode('utf-8')).stdout.decode().strip()
    with tempfile.TemporaryDirectory() as tmp:
        side = {'GIT_INDEX_FILE': str(Path(tmp) / 'release.index')}
        git('read-tree', base, env=side)
        git('update-index', '--cacheinfo', f'100644,{new_blob},{MANIFEST}', env=side)
        tree = out('write-tree', env=side)
    message = f'Lister {version}: release\n\nVersion set by tools/publish-lister.ps1 (next after the live feed).\n'
    sha = out('commit-tree', tree, '-p', base, input_bytes=message.encode('utf-8'))
    git('update-ref', ref, sha, base)
    push = git('push', '--quiet', REMOTE, f'{sha}:{ref}', check=False)
    if push.returncode != 0:
        git('update-ref', ref, base, sha, check=False)
        raise ReleaseError('GitHub refused the release commit (someone pushed first). Pull or rebase and publish again; '
                           'nothing published. ' + push.stderr.decode('utf-8', 'replace').strip())
    # Keep other sessions from committing the old version back: move the shared index entry and the
    # working file along only where they still hold the old version (their own edits stay).
    staged = git('ls-files', '-s', '--', MANIFEST).stdout.decode().split()
    if len(staged) >= 2 and staged[1] == old_blob:
        git('update-index', '--cacheinfo', f'100644,{new_blob},{MANIFEST}')
    work = ROOT / MANIFEST
    try:
        text = work.read_bytes().decode('utf-8')
        match = VERSION_RE.search(text)
        if match and parse_version(match.group(2)) < parse_version(version):
            updated = with_version(text, version).encode('utf-8')
            tmp_path = work.with_suffix('.json.tmp')
            tmp_path.write_bytes(updated)
            os.replace(tmp_path, work)
    except OSError:
        pass
    return {'commit': sha, 'pushed': True}


def main(argv=None):
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest='cmd', required=True)
    c = sub.add_parser('check'); c.add_argument('--feed', default='')
    e = sub.add_parser('export'); e.add_argument('--version', required=True); e.add_argument('--out', required=True); e.add_argument('--rev', default='HEAD')
    m = sub.add_parser('commit'); m.add_argument('--version', required=True); m.add_argument('--base', required=True)
    args = parser.parse_args(argv)
    try:
        if args.cmd == 'check':
            result = check(args.feed)
        elif args.cmd == 'export':
            result = export(args.version, args.out, args.rev)
        else:
            result = commit(args.version, args.base)
    except ReleaseError as exc:
        print(json.dumps({'ok': False, 'error': str(exc)}))
        return 2
    print(json.dumps({'ok': True, **result}))
    return 0


if __name__ == '__main__':
    sys.exit(main())
