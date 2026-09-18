"""Create an immutable, checksummed Sweet Shelves Lister release using only the standard library.

Same feed layout as the AmazingScout extension publisher: releases/<id>.zip (+ .json browser
payload), latest.json with per-file SHA-256, update.ps1 for Windows PCs and index.html (the
update page) with the hashed browser updater module.
"""

import argparse
import base64
import hashlib
import json
import shutil
import zipfile
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXTENSION_NAME = 'Sweet Shelves Lister'
REQUIRED = ('manifest.json', 'background.js', 'sidepanel.html')


def package_extension(dist: Path, output: Path, commit: str = '') -> dict:
    manifest = json.loads((dist / 'manifest.json').read_text(encoding='utf-8'))
    if manifest.get('name') != EXTENSION_NAME or manifest.get('manifest_version') != 3:
        raise ValueError(f'The build is not a {EXTENSION_NAME} Manifest V3 extension.')
    if any(path.is_symlink() for path in dist.rglob('*')):
        raise ValueError('Extension releases cannot contain symbolic links.')
    paths = sorted(path for path in dist.rglob('*') if path.is_file() and '__pycache__' not in path.parts)
    files = {path.relative_to(dist).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}
    for required in REQUIRED:
        if required not in files:
            raise ValueError(f'Missing extension build file: {required}')
    release = hashlib.sha256(json.dumps(files, sort_keys=True).encode()).hexdigest()[:20]
    archive = output / 'releases' / f'{release}.zip'
    archive.parent.mkdir(parents=True, exist_ok=True)
    # Fixed timestamps and permissions keep identical builds byte-for-byte identical.
    with zipfile.ZipFile(archive, 'w', compression=zipfile.ZIP_DEFLATED) as bundle:
        for path in paths:
            entry = zipfile.ZipInfo(path.relative_to(dist).as_posix(), (2020, 1, 1, 0, 0, 0))
            entry.compress_type = zipfile.ZIP_DEFLATED
            entry.external_attr = 0o100644 << 16
            bundle.writestr(entry, path.read_bytes())
    metadata = {
        'schemaVersion': 1,
        'name': EXTENSION_NAME,
        'release': release,
        'version': manifest['version'],
        'publishedAt': datetime.now(UTC).isoformat(),
        'archive': f'releases/{release}.zip',
        'sha256': hashlib.sha256(archive.read_bytes()).hexdigest(),
        'files': files,
    }
    # The git commit the release was built from: the next publish refuses a HEAD that lacks it.
    if commit:
        metadata['commit'] = commit
    payload = output / 'releases' / f'{release}.json'
    payload.write_text(json.dumps({name: base64.b64encode((dist / name).read_bytes()).decode('ascii') for name in files}),
                       encoding='utf-8')
    metadata['payload'] = f'releases/{release}.json'
    metadata['payloadSha256'] = hashlib.sha256(payload.read_bytes()).hexdigest()
    (output / 'latest.json').write_text(json.dumps(metadata, indent=2) + '\n', encoding='utf-8')
    return metadata


def build_feed(dist: Path, output: Path, deploy: Path, commit: str = '') -> dict:
    result = package_extension(dist, output, commit)
    shutil.copyfile(deploy / 'update.ps1', output / 'update.ps1')
    module = (deploy / 'updater.mjs').read_bytes()
    module_name = 'updater-' + hashlib.sha256(module).hexdigest()[:20] + '.mjs'
    (output / module_name).write_bytes(module)
    page = (deploy / 'downloads.html').read_text(encoding='utf-8')
    (output / 'index.html').write_text(page.replace('__UPDATER_MODULE__', module_name), encoding='utf-8')
    result['updaterModule'] = module_name
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--source', default=str(ROOT), help='repository copy to build from (publish passes a clean export)')
    parser.add_argument('--commit', default='')
    args = parser.parse_args()
    source = Path(args.source)
    destination = ROOT / 'build' / 'lister-updates'
    result = build_feed(source / 'lister-extension', destination, source / 'deploy' / 'lister', args.commit)
    print(f"Packaged {EXTENSION_NAME} {result['version']}, release {result['release']} -> {destination}")
