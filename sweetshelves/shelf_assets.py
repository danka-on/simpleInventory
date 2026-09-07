"""Shelf assets for Sweet Shelves."""

import os
import re as _re
from PIL import Image, ImageOps
from flask import send_file
from pathlib import Path
from . import config as ss_config


_SHELF_ROOT_DIR = ss_config.BASE_DIR / 'static' / 'shelves'


_OFFICE_AREA_DIR = _SHELF_ROOT_DIR / 'office'


_GARAGE_AREA_DIR = _SHELF_ROOT_DIR / 'garage'


_HALLWAY_AREA_DIR = _SHELF_ROOT_DIR / 'hallway'


_MISC_AREA_DIR = _SHELF_ROOT_DIR / 'misc'


_OFFICE_MAP_DIR = _OFFICE_AREA_DIR / 'maps'


_GARAGE_MAP_DIR = _GARAGE_AREA_DIR / 'maps'


_HALLWAY_MAP_DIR = _HALLWAY_AREA_DIR / 'maps'


_MISC_MAP_DIR = _MISC_AREA_DIR / 'maps'


_OFFICE_ORIGINALS_DIR = _OFFICE_AREA_DIR / 'originals'


_GARAGE_ORIGINALS_DIR = _GARAGE_AREA_DIR / 'originals'


_HALLWAY_ORIGINALS_DIR = _HALLWAY_AREA_DIR / 'originals'


_MISC_ORIGINALS_DIR = _MISC_AREA_DIR / 'originals'


_OFFICE_SHELF_PICS_DIR = _OFFICE_AREA_DIR / 'shelf_pics'


_GARAGE_SHELF_PICS_DIR = _GARAGE_AREA_DIR / 'shelf_pics'


_HALLWAY_SHELF_PICS_DIR = _HALLWAY_AREA_DIR / 'shelf_pics'


_MISC_SHELF_PICS_DIR = _MISC_AREA_DIR / 'shelf_pics'


_OFFICE_BASES_DIR = _OFFICE_SHELF_PICS_DIR


_GARAGE_BASES_DIR = _GARAGE_SHELF_PICS_DIR


_LEGACY_MAPS_DIR = _SHELF_ROOT_DIR / 'maps'


_LEGACY_OFFICE_MAP_DIR = _LEGACY_MAPS_DIR / 'office'


_LEGACY_GARAGE_MAP_DIR = _LEGACY_MAPS_DIR / 'garage'


_LEGACY_HALLWAY_MAP_DIR = _LEGACY_MAPS_DIR / 'hallway'


_LEGACY_MISC_MAP_DIR = _LEGACY_MAPS_DIR / 'misc'


_LEGACY_OFFICE_BASES_DIR = _LEGACY_OFFICE_MAP_DIR / 'bases'


_LEGACY_GARAGE_BASES_DIR = _LEGACY_GARAGE_MAP_DIR / 'bases'


_LEGACY_SHELF_ORIGINALS_DIR = _SHELF_ROOT_DIR / 'originals'


_LEGACY_MARKED_DIR = _SHELF_ROOT_DIR / 'marked'


_LEGACY_OFFICE_SHELF_PICS_DIR = _LEGACY_MARKED_DIR / 'office'


_LEGACY_GARAGE_SHELF_PICS_DIR = _LEGACY_MARKED_DIR / 'garage'


_LEGACY_HALLWAY_SHELF_PICS_DIR = _LEGACY_MARKED_DIR / 'hallway'


_LEGACY_MISC_SHELF_PICS_DIR = _LEGACY_MARKED_DIR / 'misc'


_OFFICE_PREVIEW_BASE_PAT = _re.compile(r'^(?:or[1-6]s\d+|omr[12]s\d+|ofloor[1-6])$', _re.IGNORECASE)


_GARAGE_PREVIEW_BASE_PAT = _re.compile(r'^(?:gfloor[1-7]|gmid[12]|misc1|gr(?:[1-4]|6|7)(?:s\d+)?)$', _re.IGNORECASE)


_OFFICE_SHELF_PAT = _re.compile(r'^(?:or\d+s\d+(?:b\d+)?|omr\d+s\d+(?:b\d+)?|ofloor\d+(?:b\d+)?)$', _re.IGNORECASE)


_GARAGE_SHELF_PAT = _re.compile(r'^(?:gr\d+s\d+(?:b\d+)?|gmid\d+(?:b\d+)?|gfloor\d+(?:b\d+)?|misc\d+(?:b\d+)?)$', _re.IGNORECASE)


_HALLWAY_SHELF_PAT = _re.compile(r'^(?:h(?:[1-9]|1[0-4])|ho[1-3])$', _re.IGNORECASE)


_MISC_SHELF_PAT = _re.compile(r'^mb\d+$', _re.IGNORECASE)


def _shelf_area_for_code(code):
    normalized = (code or '').strip()
    if not normalized:
        return None
    if _OFFICE_SHELF_PAT.fullmatch(normalized):
        return 'office'
    if _GARAGE_SHELF_PAT.fullmatch(normalized):
        return 'garage'
    if _HALLWAY_SHELF_PAT.fullmatch(normalized):
        return 'hallway'
    if _MISC_SHELF_PAT.fullmatch(normalized):
        return 'misc'
    return None


def _area_storage_dir(area, kind):
    mapping = {
        'office': {
            'maps': _OFFICE_MAP_DIR,
            'originals': _OFFICE_ORIGINALS_DIR,
            'shelf_pics': _OFFICE_SHELF_PICS_DIR,
            'bases': _OFFICE_BASES_DIR,
        },
        'garage': {
            'maps': _GARAGE_MAP_DIR,
            'originals': _GARAGE_ORIGINALS_DIR,
            'shelf_pics': _GARAGE_SHELF_PICS_DIR,
            'bases': _GARAGE_BASES_DIR,
        },
        'hallway': {
            'maps': _HALLWAY_MAP_DIR,
            'originals': _HALLWAY_ORIGINALS_DIR,
            'shelf_pics': _HALLWAY_SHELF_PICS_DIR,
            'bases': None,
        },
        'misc': {
            'maps': _MISC_MAP_DIR,
            'originals': _MISC_ORIGINALS_DIR,
            'shelf_pics': _MISC_SHELF_PICS_DIR,
            'bases': None,
        },
    }
    return (mapping.get(area) or {}).get(kind)


def _add_unique_path(candidates, path):
    if path is not None and path not in candidates:
        candidates.append(path)


_SHELF_CODE_INPUT_RE = _re.compile(r'^[A-Za-z0-9][A-Za-z0-9 ._-]{0,79}$')


def _validate_shelf_code_input(value, *, required=True):
    code = str(value or '').strip()
    if not code:
        return ('', 'Shelf code is required') if required else ('', '')
    if not _SHELF_CODE_INPUT_RE.fullmatch(code):
        return '', (
            'Shelf code must start with a letter or number and contain only '
            'letters, numbers, spaces, dots, underscores, or hyphens.'
        )
    return code, ''


def _safe_shelf_lookup_code(value):
    code = str(value or '').strip()
    if not code or '\x00' in code or '/' in code or '\\' in code:
        return ''
    if any(part == '..' for part in Path(code).parts):
        return ''
    return code


def _shelf_code_exists(cur, code, *, exclude_code=''):
    params = [str(code or '').strip()]
    sql = 'SELECT id, shelf_name FROM shelves WHERE LOWER(TRIM(shelf_name)) = LOWER(TRIM(?))'
    if exclude_code:
        sql += ' AND LOWER(TRIM(shelf_name)) != LOWER(TRIM(?))'
        params.append(str(exclude_code).strip())
    sql += ' ORDER BY id LIMIT 1'
    cur.execute(sql, params)
    return cur.fetchone()


def _preview_base_path_for_code(code):
    normalized = (code or '').strip().lower()
    if not normalized:
        return None
    if _OFFICE_PREVIEW_BASE_PAT.fullmatch(normalized):
        return _OFFICE_BASES_DIR / f'{normalized}.png'
    if _GARAGE_PREVIEW_BASE_PAT.fullmatch(normalized):
        return _GARAGE_BASES_DIR / f'{normalized}.png'
    return None


def _legacy_preview_base_path_for_code(code):
    normalized = (code or '').strip().lower()
    if not normalized:
        return None
    if _OFFICE_PREVIEW_BASE_PAT.fullmatch(normalized):
        return _LEGACY_OFFICE_BASES_DIR / f'{normalized}.png'
    if _GARAGE_PREVIEW_BASE_PAT.fullmatch(normalized):
        return _LEGACY_GARAGE_BASES_DIR / f'{normalized}.png'
    return None


def _preview_base_lookup_codes(code):
    normalized = (code or '').strip().lower()
    if not normalized:
        return []
    results = [normalized]
    compact = _re.sub(r'b\d+$', '', normalized)
    if compact and compact not in results:
        results.append(compact)
    return results


def _original_shelf_image_path_for_code(code):
    normalized = (code or '').strip()
    if not normalized:
        return None
    area = _shelf_area_for_code(normalized)
    area_originals_dir = _area_storage_dir(area, 'originals') if area else None
    if area_originals_dir is not None:
        return area_originals_dir / f'{normalized}.png'
    return _LEGACY_SHELF_ORIGINALS_DIR / f'{normalized}.png'


def _shelf_original_path_candidates(code):
    raw = _safe_shelf_lookup_code(code)
    if not raw:
        return []
    variants = []
    for value in (raw, raw.lower(), raw.upper()):
        if value and value not in variants:
            variants.append(value)
    # Also try bin-stripped variants (e.g. gr1s1b2 → gr1s1)
    nobin = _re.sub(r'b\d+$', '', raw, flags=_re.IGNORECASE)
    if nobin and nobin.lower() != raw.lower():
        for value in (nobin, nobin.lower(), nobin.upper()):
            if value and value not in variants:
                variants.append(value)
    dirs = []
    preferred_area = _shelf_area_for_code(raw)
    for area in (preferred_area, 'office', 'garage', 'hallway', 'misc'):
        directory = _area_storage_dir(area, 'originals') if area else None
        if directory is not None and directory not in dirs:
            dirs.append(directory)
    if _LEGACY_SHELF_ORIGINALS_DIR not in dirs:
        dirs.append(_LEGACY_SHELF_ORIGINALS_DIR)
    candidates = []
    for directory in dirs:
        for value in variants:
            _add_unique_path(candidates, directory / f'{value}.png')
    return candidates


def _resolve_shelf_original_path(code, existing_only=False):
    for candidate in _shelf_original_path_candidates(code):
        if candidate.exists():
            return candidate
    if existing_only:
        return None
    raw = str(code or '').strip()
    if not raw:
        return None
    return _original_shelf_image_path_for_code(raw)


def _truthy_form_value(value):
    return str(value or '').strip().lower() in ('1', 'true', 'yes', 'on')


def _save_uploaded_shelf_png(upload, target_path):
    """Decode an uploaded image and atomically write a real PNG."""
    import uuid
    target = Path(target_path)
    temp_path = target.with_name(f'.{target.name}.upload-{uuid.uuid4().hex}')
    try:
        upload.stream.seek(0)
        with Image.open(upload.stream) as source:
            source.load()
            if source.width * source.height > 50_000_000:
                raise ValueError('Shelf image is too large')
            rendered = ImageOps.exif_transpose(source)
            if rendered.mode not in ('RGB', 'RGBA'):
                rendered = rendered.convert('RGBA' if 'transparency' in rendered.info else 'RGB')
            target.parent.mkdir(parents=True, exist_ok=True)
            rendered.save(str(temp_path), format='PNG')
        os.replace(str(temp_path), str(target))
    except ValueError:
        temp_path.unlink(missing_ok=True)
        raise
    except Exception as exc:
        temp_path.unlink(missing_ok=True)
        raise ValueError('The uploaded file is not a valid image') from exc


def _preferred_shelf_display_dir(code):
    normalized = (code or '').strip()
    if not normalized:
        return _SHELF_ROOT_DIR
    area = _shelf_area_for_code(normalized)
    preferred = _area_storage_dir(area, 'shelf_pics') if area else None
    if preferred is not None:
        return preferred
    return _SHELF_ROOT_DIR


def _shelf_display_path_candidates(code):
    raw = _safe_shelf_lookup_code(code)
    if not raw:
        return []
    variants = []
    for value in (raw, raw.lower(), raw.upper()):
        if value and value not in variants:
            variants.append(value)
    # Also try bin-stripped variants (e.g. gr1s1b2 → gr1s1) so shelf photos without bin suffix are found
    nobin = _re.sub(r'b\d+$', '', raw, flags=_re.IGNORECASE)
    if nobin and nobin.lower() != raw.lower():
        for value in (nobin, nobin.lower(), nobin.upper()):
            if value and value not in variants:
                variants.append(value)
    dirs = []
    for directory in (
        _preferred_shelf_display_dir(raw),
        _SHELF_ROOT_DIR,
        _OFFICE_SHELF_PICS_DIR,
        _GARAGE_SHELF_PICS_DIR,
        _HALLWAY_SHELF_PICS_DIR,
        _MISC_SHELF_PICS_DIR,
        _LEGACY_OFFICE_SHELF_PICS_DIR,
        _LEGACY_GARAGE_SHELF_PICS_DIR,
        _LEGACY_HALLWAY_SHELF_PICS_DIR,
        _LEGACY_MISC_SHELF_PICS_DIR,
        _LEGACY_OFFICE_MAP_DIR,
        _LEGACY_GARAGE_MAP_DIR,
        _LEGACY_HALLWAY_MAP_DIR,
        _LEGACY_MISC_MAP_DIR,
    ):
        if directory not in dirs:
            dirs.append(directory)
    candidates = []
    for directory in dirs:
        for value in variants:
            path = directory / f'{value}.png'
            if path not in candidates:
                candidates.append(path)
    return candidates


def _shelf_exact_display_path_candidates(code):
    raw = _safe_shelf_lookup_code(code)
    if not raw:
        return []
    variants = []
    for value in (raw, raw.lower(), raw.upper()):
        if value and value not in variants:
            variants.append(value)
    directories = []
    for directory in (
        _preferred_shelf_display_dir(raw),
        _SHELF_ROOT_DIR,
        _OFFICE_SHELF_PICS_DIR,
        _GARAGE_SHELF_PICS_DIR,
        _HALLWAY_SHELF_PICS_DIR,
        _MISC_SHELF_PICS_DIR,
        _LEGACY_OFFICE_SHELF_PICS_DIR,
        _LEGACY_GARAGE_SHELF_PICS_DIR,
        _LEGACY_HALLWAY_SHELF_PICS_DIR,
        _LEGACY_MISC_SHELF_PICS_DIR,
        _LEGACY_OFFICE_MAP_DIR,
        _LEGACY_GARAGE_MAP_DIR,
        _LEGACY_HALLWAY_MAP_DIR,
        _LEGACY_MISC_MAP_DIR,
    ):
        if directory not in directories:
            directories.append(directory)
    return [directory / f'{value}.png' for directory in directories for value in variants]


def _resolve_exact_shelf_display_path(code, existing_only=False):
    for candidate in _shelf_exact_display_path_candidates(code):
        if candidate.exists():
            return candidate
    if existing_only:
        return None
    raw = _safe_shelf_lookup_code(code)
    return (_preferred_shelf_display_dir(raw) / f'{raw}.png') if raw else None


def _shelf_exact_original_path_candidates(code):
    raw = _safe_shelf_lookup_code(code)
    if not raw:
        return []
    variants = []
    for value in (raw, raw.lower(), raw.upper()):
        if value and value not in variants:
            variants.append(value)
    directories = []
    preferred_area = _shelf_area_for_code(raw)
    for area in (preferred_area, 'office', 'garage', 'hallway', 'misc'):
        directory = _area_storage_dir(area, 'originals') if area else None
        if directory is not None and directory not in directories:
            directories.append(directory)
    if _LEGACY_SHELF_ORIGINALS_DIR not in directories:
        directories.append(_LEGACY_SHELF_ORIGINALS_DIR)
    return [directory / f'{value}.png' for directory in directories for value in variants]


def _resolve_exact_shelf_original_path(code, existing_only=False):
    for candidate in _shelf_exact_original_path_candidates(code):
        if candidate.exists():
            return candidate
    if existing_only:
        return None
    raw = _safe_shelf_lookup_code(code)
    return _original_shelf_image_path_for_code(raw) if raw else None


def _resolve_shelf_display_path(code, existing_only=False):
    for candidate in _shelf_display_path_candidates(code):
        if candidate.exists():
            return candidate
    if existing_only:
        return None
    raw = str(code or '').strip()
    if not raw:
        return None
    return _preferred_shelf_display_dir(raw) / f'{raw}.png'


def _iter_shelf_display_files():
    by_code = {}
    scan_specs = (
        (_SHELF_ROOT_DIR, None, 0),
        (_LEGACY_OFFICE_MAP_DIR, _OFFICE_SHELF_PAT, 1),
        (_LEGACY_GARAGE_MAP_DIR, _GARAGE_SHELF_PAT, 1),
        (_LEGACY_HALLWAY_MAP_DIR, _HALLWAY_SHELF_PAT, 1),
        (_LEGACY_MISC_MAP_DIR, _MISC_SHELF_PAT, 1),
        (_LEGACY_OFFICE_SHELF_PICS_DIR, _OFFICE_SHELF_PAT, 2),
        (_LEGACY_GARAGE_SHELF_PICS_DIR, _GARAGE_SHELF_PAT, 2),
        (_LEGACY_HALLWAY_SHELF_PICS_DIR, _HALLWAY_SHELF_PAT, 2),
        (_LEGACY_MISC_SHELF_PICS_DIR, _MISC_SHELF_PAT, 2),
        (_OFFICE_SHELF_PICS_DIR, _OFFICE_SHELF_PAT, 3),
        (_GARAGE_SHELF_PICS_DIR, _GARAGE_SHELF_PAT, 3),
        (_HALLWAY_SHELF_PICS_DIR, _HALLWAY_SHELF_PAT, 3),
        (_MISC_SHELF_PICS_DIR, _MISC_SHELF_PAT, 3),
    )
    for directory, pattern, priority in scan_specs:
        if not directory.exists():
            continue
        for path in directory.iterdir():
            if not path.is_file() or path.suffix.lower() != '.png':
                continue
            if pattern and not pattern.fullmatch(path.stem):
                continue
            key = path.stem.lower()
            current = by_code.get(key)
            if current is None:
                by_code[key] = (path, priority)
                continue
            current_path, current_priority = current
            if priority > current_priority or (
                priority == current_priority and path.stat().st_mtime >= current_path.stat().st_mtime
            ):
                by_code[key] = (path, priority)
    return [item[0] for item in by_code.values()]


def shelf_image(code):
    image_path = _resolve_shelf_display_path(code, existing_only=True)
    if image_path is None or not image_path.exists():
        return '', 404
    return send_file(str(image_path), mimetype='image/png')


def _preview_base_path_candidates(code):
    candidates = []
    for lookup_code in _preview_base_lookup_codes(code):
        _add_unique_path(candidates, _preview_base_path_for_code(lookup_code))
        _add_unique_path(candidates, _legacy_preview_base_path_for_code(lookup_code))
        for original_candidate in _shelf_original_path_candidates(lookup_code):
            _add_unique_path(candidates, original_candidate)
    return candidates


def _resolve_preview_base_path(code):
    for candidate in _preview_base_path_candidates(code):
        if candidate.exists():
            return candidate
    return None


def shelf_original(code):
    image_path = _resolve_shelf_original_path(code, existing_only=True)
    if image_path is None or not image_path.exists():
        return '', 404
    return send_file(str(image_path), mimetype='image/png')


def shelf_base_image(code):
    image_path = _resolve_preview_base_path(code)
    if image_path is None or not image_path.exists():
        return '', 404
    return send_file(str(image_path), mimetype='image/png')


def _sync_preview_base_image(code, source_path):
    base_path = _preview_base_path_for_code(code)
    if not base_path:
        return
    source = Path(source_path)
    if not source.is_absolute():
        source = ss_config.BASE_DIR / source
    if not source.exists():
        return
    if source.resolve() == base_path.resolve():
        return
    base_path.parent.mkdir(parents=True, exist_ok=True)
    base_path.write_bytes(source.read_bytes())


def _sync_original_to_preview_base(code):
    orig_path = _resolve_shelf_original_path(code, existing_only=True)
    if orig_path is None or not orig_path.exists():
        return
    _sync_preview_base_image(code, orig_path)


def _rename_preview_base_image(old_code, new_code):
    old_path = _preview_base_path_for_code(old_code)
    if not old_path or not old_path.exists():
        return
    new_path = _preview_base_path_for_code(new_code)
    if not new_path:
        old_path.unlink(missing_ok=True)
        return
    if new_path == old_path:
        return
    new_path.parent.mkdir(parents=True, exist_ok=True)
    new_path.write_bytes(old_path.read_bytes())
    old_path.unlink(missing_ok=True)


def _delete_preview_base_image(code):
    base_path = _preview_base_path_for_code(code)
    if base_path and base_path.exists():
        base_path.unlink(missing_ok=True)
