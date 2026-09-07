"""Prep context for Sweet Shelves."""

import datetime
import sqlite3
from flask import jsonify, request
from . import (
    caching as ss_caching, database as ss_database, errors as ss_errors, listing_checks as
    ss_listing_checks, listing_queue as ss_listing_queue, normalization as ss_normalization, prep_schema
    as ss_prep_schema, runtime as ss_runtime, warehouse_allocations as ss_warehouse_allocations,
)


def _items_prep_scope_has_actionable_status(cur, upc, row_status='', lot_number=''):
    """
    Notes only make sense on actionable prep rows.
    Unchecked rows should not carry notes, even if legacy data exists.
    """
    scope_upc, _scope_status = ss_normalization._items_to_list_asset_scope(upc, row_status)
    if not scope_upc:
        return False

    explicit_status = ss_normalization._normalize_prep_row_status(row_status)
    if ss_normalization._items_to_list_effective_status(scope_upc, explicit_status) in ('good', 'bad', 'return'):
        return True
    if explicit_status == 'unchecked':
        return False

    lot_n = ss_normalization._normalize_lot_number(lot_number)

    try:
        status_row = ss_warehouse_allocations._select_prep_status_row(cur, scope_upc, lot_n, columns='status')
        status_value = ss_normalization._items_to_list_effective_status(scope_upc, status_row[0]) if status_row else ''
        if status_value in ('good', 'bad', 'return'):
            return True
    except Exception:
        pass

    try:
        if lot_n:
            cur.execute('''
                SELECT COALESCE(good_qty, 0), COALESCE(bad_qty, 0)
                FROM bol_items
                WHERE upc = ? COLLATE NOCASE
                  AND lot_number = ? COLLATE NOCASE
                ORDER BY import_date DESC, id DESC
                LIMIT 1
            ''', (scope_upc, lot_n))
            bol_row = cur.fetchone()
        else:
            cur.execute('''
                SELECT COALESCE(good_qty, 0), COALESCE(bad_qty, 0)
                FROM bol_items
                WHERE upc = ? COLLATE NOCASE
                ORDER BY import_date DESC, id DESC
                LIMIT 1
            ''', (scope_upc,))
            bol_row = cur.fetchone()
        if bol_row:
            return bool(ss_normalization._items_to_list_bucket_status(scope_upc, good_qty=bol_row[0], bad_qty=bol_row[1]))
    except Exception:
        pass

    return False


def _ready_to_ship_prep_related_like(base_upc):
    safe = str(base_upc or '').replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')
    return f'{safe}-%'


def _ready_to_ship_prep_sort_value(value):
    raw = str(value or '').strip()
    if not raw:
        return 0.0
    try:
        cleaned = raw.replace('Z', '+00:00')
        dt = datetime.datetime.fromisoformat(cleaned)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=datetime.timezone.utc)
        return dt.timestamp()
    except Exception:
        return 0.0


def _ready_to_ship_prep_relevance(requested_upc, scope_upc):
    requested = ss_normalization._normalize_upc_preserve_suffix_for_match(requested_upc)
    scope = ss_normalization._normalize_upc_preserve_suffix_for_match(scope_upc)
    if not requested or not scope:
        return 99
    requested_base = requested.split('-', 1)[0]
    scope_base = scope.split('-', 1)[0]
    requested_has_suffix = '-' in requested
    scope_has_suffix = '-' in scope

    if requested_has_suffix:
        if scope.lower() == requested.lower():
            return 0
        if scope.lower() == requested_base.lower():
            return 1
        if scope_base.lower() == requested_base.lower():
            return 2
        return 3

    if scope_has_suffix and scope_base.lower() == requested_base.lower():
        return 0
    if scope.lower() == requested_base.lower():
        return 1
    if scope_base.lower() == requested_base.lower():
        return 2
    return 3


def _ready_to_ship_prep_relevance_label(requested_upc, scope_upc):
    requested = ss_normalization._normalize_upc_preserve_suffix_for_match(requested_upc)
    scope = ss_normalization._normalize_upc_preserve_suffix_for_match(scope_upc)
    if not requested or not scope:
        return 'Related entry'
    requested_base = requested.split('-', 1)[0]
    scope_base = scope.split('-', 1)[0]
    requested_has_suffix = '-' in requested
    scope_has_suffix = '-' in scope

    if requested_has_suffix:
        if scope.lower() == requested.lower():
            return 'Direct item'
        if scope.lower() == requested_base.lower():
            return 'Base item'
        if scope_base.lower() == requested_base.lower():
            return 'Related suffixed item'
        return 'Related entry'

    if scope_has_suffix and scope_base.lower() == requested_base.lower():
        return 'Possible suffixed item'
    if scope.lower() == requested_base.lower():
        return 'Direct item'
    if scope_base.lower() == requested_base.lower():
        return 'Related item'
    return 'Related entry'


def _build_ready_to_ship_prep_context(cur, barcode, include_details=True, max_notes_per_entry=12, max_images_per_entry=8):
    requested_upc = ss_normalization._normalize_upc_preserve_suffix_for_match(barcode)
    if not requested_upc:
        return {
            'requested_barcode': '',
            'base_barcode': '',
            'has_alerts': False,
            'summary': {
                'has_alerts': False,
                'entry_count': 0,
                'note_count': 0,
                'defect_count': 0,
                'image_count': 0,
                'latest_at': '',
                'has_suffixed': False
            },
            'entries': []
        }

    requested_base = requested_upc.split('-', 1)[0]
    like_related = _ready_to_ship_prep_related_like(requested_base)

    entry_map = {}

    def ensure_entry(scope_upc, scope_status=''):
        key = (str(scope_upc or '').strip().lower(), str(scope_status or '').strip().lower())
        if key not in entry_map:
            entry_map[key] = {
                'scope_upc': scope_upc,
                'row_status': scope_status,
                'status': scope_status or '',
                'reason': '',
                'latest_at': '',
                'notes': [],
                'images': [],
                'voice_notes': [],
                'note_count': 0,
                'image_count': 0,
                'voice_note_count': 0,
                'has_suffixed': '-' in str(scope_upc or ''),
                '_note_keys': set(),
                '_image_ids': set(),
                '_audio_ids': set(),
            }
        return entry_map[key]

    def push_timestamp(entry, value):
        raw = str(value or '').strip()
        if not raw:
            return
        if _ready_to_ship_prep_sort_value(raw) >= _ready_to_ship_prep_sort_value(entry.get('latest_at')):
            entry['latest_at'] = raw

    bol_visible_status_by_scope = {}

    def entry_scope_key(entry):
        return ss_normalization._normalize_upc_preserve_suffix_for_match(entry.get('scope_upc'))

    def entry_base_key(entry):
        scope_key = entry_scope_key(entry)
        return scope_key.split('-', 1)[0] if scope_key else ''

    def entry_display_status(entry):
        return str(entry.get('display_status') or entry.get('row_status') or entry.get('status') or '').strip().lower()

    def entry_visible_status(entry):
        scope_key = entry_scope_key(entry)
        status = ss_normalization._items_to_list_effective_status(scope_key, entry_display_status(entry))
        if status in ('good', 'bad', 'return'):
            return status
        return str(bol_visible_status_by_scope.get(scope_key) or '').strip().lower()

    def is_visible_items_to_list_entry(entry):
        return entry_visible_status(entry) in ('good', 'bad', 'return')

    def merge_orphan_entry_into(target, source):
        push_timestamp(target, source.get('latest_at'))
        target['has_suffixed'] = bool(target.get('has_suffixed') or source.get('has_suffixed'))

        source_note_keys = source.get('_note_keys') or set()
        target_note_keys = target.get('_note_keys')
        if not isinstance(target_note_keys, set):
            target_note_keys = set(target_note_keys or [])
            target['_note_keys'] = target_note_keys
        existing_target_note_keys = set(target_note_keys)
        added_note_count = 0
        for note_key in source_note_keys:
            if note_key in target_note_keys:
                continue
            target_note_keys.add(note_key)
            added_note_count += 1
        if added_note_count:
            target['note_count'] = int(target.get('note_count') or 0) + added_note_count
        if include_details:
            existing_note_ids = {
                str(note.get('id') or '')
                for note in (target.get('notes') or [])
            }
            for note in (source.get('notes') or []):
                note_id = str(note.get('id') or '')
                dedupe_key = (
                    note.get('kind'),
                    str(note.get('note') or '').strip().lower(),
                    str(note.get('created_at') or '').strip()
                )
                if note_id in existing_note_ids or dedupe_key in existing_target_note_keys:
                    continue
                target.setdefault('notes', []).append(note)
                existing_note_ids.add(note_id)

        source_image_ids = source.get('_image_ids') or set()
        target_image_ids = target.get('_image_ids')
        if not isinstance(target_image_ids, set):
            target_image_ids = set(target_image_ids or [])
            target['_image_ids'] = target_image_ids
        added_image_count = 0
        for image_id in source_image_ids:
            if image_id in target_image_ids:
                continue
            target_image_ids.add(image_id)
            added_image_count += 1
        if added_image_count:
            target['image_count'] = int(target.get('image_count') or 0) + added_image_count
        if include_details:
            existing_image_ids = {
                int(img.get('id') or 0)
                for img in (target.get('images') or [])
                if str(img.get('id') or '').isdigit()
            }
            for image in (source.get('images') or []):
                image_id = image.get('id')
                if image_id in existing_image_ids:
                    continue
                target.setdefault('images', []).append(image)
                if str(image_id or '').isdigit():
                    existing_image_ids.add(int(image_id))

        source_audio_ids = source.get('_audio_ids') or set()
        target_audio_ids = target.get('_audio_ids')
        if not isinstance(target_audio_ids, set):
            target_audio_ids = set(target_audio_ids or [])
            target['_audio_ids'] = target_audio_ids
        added_audio_count = 0
        for audio_id in source_audio_ids:
            if audio_id in target_audio_ids:
                continue
            target_audio_ids.add(audio_id)
            added_audio_count += 1
        if added_audio_count:
            target['voice_note_count'] = int(target.get('voice_note_count') or 0) + added_audio_count
        if include_details:
            existing_audio_ids = {int(vn.get('id') or 0) for vn in (target.get('voice_notes') or []) if str(vn.get('id') or '').isdigit()}
            for vn in (source.get('voice_notes') or []):
                vn_id = vn.get('id')
                if str(vn_id or '').isdigit() and int(vn_id) in existing_audio_ids:
                    continue
                target.setdefault('voice_notes', []).append(vn)
                if str(vn_id or '').isdigit():
                    existing_audio_ids.add(int(vn_id))

    try:
        cur.execute('''
            SELECT upc, COALESCE(status, '') AS status, COALESCE(reason, '') AS reason,
                   COALESCE(note, '') AS note, COALESCE(updated_at, '') AS updated_at,
                   COALESCE(quantity, 0) AS quantity
            FROM items_prep_status
            WHERE upc = ? COLLATE NOCASE
               OR upc LIKE ? ESCAPE '\\'
            ORDER BY updated_at DESC, id DESC
        ''', (requested_base, like_related))
        status_rows = [dict(r) for r in cur.fetchall()]
    except Exception:
        status_rows = []

    for row in status_rows:
        raw_upc = ss_normalization._normalize_upc_preserve_suffix_for_match(row.get('upc'))
        status_value = ss_normalization._normalize_prep_row_status(row.get('status'))
        scope_upc, scope_status = ss_normalization._items_to_list_asset_scope(raw_upc, status_value)
        if not scope_upc:
            continue
        entry = ensure_entry(scope_upc, scope_status)
        if status_value and not entry.get('status'):
            entry['status'] = status_value
        reason_text = str(row.get('reason') or '').strip()
        if reason_text and (
            not entry.get('reason')
            or _ready_to_ship_prep_sort_value(row.get('updated_at')) >= _ready_to_ship_prep_sort_value(entry.get('latest_at'))
        ):
            entry['reason'] = reason_text
        push_timestamp(entry, row.get('updated_at'))
        note_text = str(row.get('note') or '').strip()
        note_at = str(row.get('updated_at') or '').strip()
        if note_text:
            note_key = ('status', note_text.lower(), note_at)
            if note_key not in entry['_note_keys']:
                entry['_note_keys'].add(note_key)
                entry['note_count'] += 1
                if include_details and len(entry['notes']) < max_notes_per_entry:
                    entry['notes'].append({
                        'id': f"status-{scope_upc}-{scope_status or 'none'}-{len(entry['notes'])}",
                        'note': note_text,
                        'created_at': note_at,
                        'kind': 'status'
                    })

    try:
        cur.execute('''
            SELECT id, upc, COALESCE(row_status, '') AS row_status,
                   COALESCE(note, '') AS note, COALESCE(created_at, '') AS created_at
            FROM items_prep_notes
            WHERE upc = ? COLLATE NOCASE
               OR upc LIKE ? ESCAPE '\\'
            ORDER BY created_at DESC, id DESC
        ''', (requested_base, like_related))
        note_rows = [dict(r) for r in cur.fetchall()]
    except Exception:
        note_rows = []

    for row in note_rows:
        raw_upc = ss_normalization._normalize_upc_preserve_suffix_for_match(row.get('upc'))
        scope_upc, scope_status = ss_normalization._items_to_list_asset_scope(raw_upc, row.get('row_status'))
        if not scope_upc:
            continue
        entry = ensure_entry(scope_upc, scope_status)
        note_text = str(row.get('note') or '').strip()
        note_at = str(row.get('created_at') or '').strip()
        if not note_text:
            continue
        note_key = ('note', note_text.lower(), note_at)
        if note_key in entry['_note_keys']:
            continue
        entry['_note_keys'].add(note_key)
        entry['note_count'] += 1
        push_timestamp(entry, note_at)
        if include_details and len(entry['notes']) < max_notes_per_entry:
            entry['notes'].append({
                'id': row.get('id'),
                'note': note_text,
                'created_at': note_at,
                'kind': 'note'
            })

    try:
        cur.execute('''
            SELECT id, upc, COALESCE(row_status, '') AS row_status,
                   COALESCE(image_path, '') AS image_path, COALESCE(created_at, '') AS created_at,
                   COALESCE(rotation, 0) AS rotation
            FROM items_prep_images
            WHERE (upc = ? COLLATE NOCASE OR upc LIKE ? ESCAPE '\\')
              AND (deleted_at IS NULL OR TRIM(COALESCE(deleted_at, '')) = '')
            ORDER BY created_at DESC, id DESC
        ''', (requested_base, like_related))
        image_rows = [dict(r) for r in cur.fetchall()]
    except Exception:
        image_rows = []

    for row in image_rows:
        raw_upc = ss_normalization._normalize_upc_preserve_suffix_for_match(row.get('upc'))
        scope_upc, scope_status = ss_normalization._items_to_list_asset_scope(raw_upc, row.get('row_status'))
        if not scope_upc:
            continue
        entry = ensure_entry(scope_upc, scope_status)
        image_id = row.get('id')
        if image_id in entry['_image_ids']:
            continue
        entry['_image_ids'].add(image_id)
        entry['image_count'] += 1
        push_timestamp(entry, row.get('created_at'))
        image_path = str(row.get('image_path') or '').strip()
        if include_details and image_path and len(entry['images']) < max_images_per_entry:
            image_url = image_path if image_path.startswith(('http://', 'https://', '/')) else f"/static/{image_path}"
            entry['images'].append({
                'id': image_id,
                'image_path': image_path,
                'image_url': image_url,
                'created_at': row.get('created_at') or '',
                'rotation': int(row.get('rotation') or 0)
            })

    try:
        cur.execute('''
            SELECT id, upc, COALESCE(row_status, '') AS row_status,
                   file_path, COALESCE(mime_type,'') AS mime_type, created_at
            FROM items_prep_media
            WHERE (upc = ? COLLATE NOCASE OR upc LIKE ? ESCAPE '\\')
              AND COALESCE(media_type,'') = 'audio'
            ORDER BY created_at DESC, id DESC
            LIMIT 20
        ''', (requested_base, like_related))
        audio_rows = [dict(r) for r in cur.fetchall()]
    except Exception:
        audio_rows = []

    for row in audio_rows:
        raw_upc = ss_normalization._normalize_upc_preserve_suffix_for_match(row.get('upc'))
        scope_upc, scope_status = ss_normalization._items_to_list_asset_scope(raw_upc, row.get('row_status'))
        if not scope_upc:
            continue
        entry = ensure_entry(scope_upc, scope_status)
        file_path = str(row.get('file_path') or '').strip()
        if not file_path:
            continue
        audio_url = file_path if file_path.startswith(('http://', 'https://', '/')) else f"/static/{file_path}"
        audio_id = row.get('id')
        audio_ids = entry.setdefault('_audio_ids', set())
        if audio_id not in audio_ids:
            audio_ids.add(audio_id)
            entry['voice_note_count'] = int(entry.get('voice_note_count') or 0) + 1
            if include_details:
                entry.setdefault('voice_notes', []).append({
                    'id': audio_id,
                    'url': audio_url,
                    'mime_type': row.get('mime_type') or '',
                    'created_at': row.get('created_at') or ''
                })
            push_timestamp(entry, row.get('created_at'))

    try:
        cur.execute('''
            SELECT upc,
                   MAX(
                       CASE
                           WHEN INSTR(COALESCE(upc, ''), '-') > 0 AND COALESCE(bad_qty, 0) > 0 THEN 2
                           WHEN COALESCE(good_qty, 0) > 0 THEN 1
                           ELSE 0
                       END
                   ) AS visibility_rank
            FROM bol_items
            WHERE upc = ? COLLATE NOCASE
               OR upc LIKE ? ESCAPE '\\'
            GROUP BY upc
        ''', (requested_base, like_related))
        for row in cur.fetchall():
            scope_upc = ss_normalization._normalize_upc_preserve_suffix_for_match(row['upc'])
            if not scope_upc:
                continue
            rank = int(row['visibility_rank'] or 0)
            if rank >= 2:
                bol_visible_status_by_scope[scope_upc] = 'bad'
            elif rank == 1:
                bol_visible_status_by_scope[scope_upc] = 'good'
    except Exception:
        bol_visible_status_by_scope = {}

    working_entries = []

    for entry in entry_map.values():
        if entry['note_count'] <= 0 and not str(entry.get('reason') or '').strip() and entry['image_count'] <= 0 and int(entry.get('voice_note_count') or 0) <= 0:
            continue
        entry['relevance_rank'] = _ready_to_ship_prep_relevance(requested_upc, entry.get('scope_upc'))
        entry['relevance_label'] = _ready_to_ship_prep_relevance_label(requested_upc, entry.get('scope_upc'))
        entry['display_status'] = entry_visible_status(entry)
        if entry['display_status'] and not str(entry.get('status') or '').strip():
            entry['status'] = entry['display_status']
        entry['display_label'] = entry.get('scope_upc') or ''
        working_entries.append(entry)

    # Notes/photos can exist on a base UPC without a visible /items-to-list row.
    # When that happens, fold those orphan assets into the related visible row so
    # Ready to Ship doesn't report a fake second "entry" for the same prep item.
    merged_entries = []
    for entry in working_entries:
        if is_visible_items_to_list_entry(entry):
            merged_entries.append(entry)
            continue

        entry_base = entry_base_key(entry)
        if not entry_base:
            merged_entries.append(entry)
            continue

        candidate_entries = [
            candidate for candidate in working_entries
            if candidate is not entry
            and is_visible_items_to_list_entry(candidate)
            and entry_base_key(candidate).lower() == entry_base.lower()
        ]
        if not candidate_entries:
            continue

        source_scope = entry_scope_key(entry).lower()
        exact_scope = [
            candidate for candidate in candidate_entries
            if entry_scope_key(candidate).lower() == source_scope
        ]
        if exact_scope:
            candidate_entries = exact_scope

        requested_key = requested_upc.lower()
        if len(candidate_entries) > 1:
            exact_requested = [
                candidate for candidate in candidate_entries
                if entry_scope_key(candidate).lower() == requested_key
            ]
            if exact_requested:
                candidate_entries = exact_requested

        candidate_entries.sort(
            key=lambda candidate: (
                int(candidate.get('relevance_rank', 99)),
                -_ready_to_ship_prep_sort_value(candidate.get('latest_at')),
                0 if '-' in str(candidate.get('scope_upc') or '') else 1,
                str(candidate.get('scope_upc') or '').lower()
            )
        )
        merge_orphan_entry_into(candidate_entries[0], entry)

    entries = []
    note_total = 0
    defect_total = 0
    image_total = 0
    latest_at = ''
    has_suffixed = False

    for entry in merged_entries:
        if entry['note_count'] <= 0 and not str(entry.get('reason') or '').strip() and entry['image_count'] <= 0 and int(entry.get('voice_note_count') or 0) <= 0:
            continue
        note_total += int(entry.get('note_count') or 0)
        image_total += int(entry.get('image_count') or 0)
        if str(entry.get('reason') or '').strip():
            defect_total += 1
        if entry.get('has_suffixed'):
            has_suffixed = True
        if _ready_to_ship_prep_sort_value(entry.get('latest_at')) >= _ready_to_ship_prep_sort_value(latest_at):
            latest_at = entry.get('latest_at') or latest_at
        if include_details:
            entry['notes'] = sorted(
                entry['notes'],
                key=lambda n: (_ready_to_ship_prep_sort_value(n.get('created_at')), int(n.get('id') or 0) if str(n.get('id') or '').isdigit() else 0),
                reverse=True
            )[:max_notes_per_entry]
            entry['images'] = sorted(
                entry['images'],
                key=lambda img: (_ready_to_ship_prep_sort_value(img.get('created_at')), int(img.get('id') or 0) if str(img.get('id') or '').isdigit() else 0),
                reverse=True
            )[:max_images_per_entry]
            entry['voice_notes'] = sorted(
                entry.get('voice_notes') or [],
                key=lambda vn: (_ready_to_ship_prep_sort_value(vn.get('created_at')), int(vn.get('id') or 0) if str(vn.get('id') or '').isdigit() else 0),
                reverse=True
            )[:10]
        else:
            entry.pop('notes', None)
            entry.pop('images', None)
            entry.pop('voice_notes', None)
        entry.pop('_note_keys', None)
        entry.pop('_image_ids', None)
        entry.pop('_audio_ids', None)
        entries.append(entry)

    entries.sort(
        key=lambda e: (
            int(e.get('relevance_rank', 99)),
            -_ready_to_ship_prep_sort_value(e.get('latest_at')),
            str(e.get('scope_upc') or '').lower(),
            str(e.get('row_status') or '').lower()
        )
    )

    summary = {
        'has_alerts': bool(entries),
        'entry_count': len(entries),
        'note_count': note_total,
        'defect_count': defect_total,
        'image_count': image_total,
        'latest_at': latest_at,
        'has_suffixed': has_suffixed
    }
    payload = {
        'requested_barcode': requested_upc,
        'base_barcode': requested_base,
        'has_alerts': summary['has_alerts'],
        'summary': summary,
        'entries': entries if include_details else []
    }
    return payload


def _suffixed_item_prep_variants(barcode):
    normalized = ss_normalization._normalize_upc_preserve_suffix_for_match(barcode)
    if not ss_normalization._is_items_to_list_suffixed_upc(normalized):
        return []
    variants = []
    for candidate in ss_listing_queue._items_to_list_upc_search_variants(str(barcode or '').strip()):
        value = str(candidate or '').strip()
        if value and value.casefold() not in {v.casefold() for v in variants}:
            variants.append(value)
    if normalized and normalized.casefold() not in {v.casefold() for v in variants}:
        variants.append(normalized)
    base, suffix = normalized.rsplit('-', 1)
    if base.isdigit():
        stripped_base = base.lstrip('0') or '0'
        for width in (8, 11, 12, 13, 14):
            if len(stripped_base) < width:
                candidate = f'{stripped_base.zfill(width)}-{suffix}'
                if candidate.casefold() not in {v.casefold() for v in variants}:
                    variants.append(candidate)
    return variants


def _empty_suffixed_item_prep_summary(barcode=''):
    return {
        'barcode': ss_normalization._normalize_upc_preserve_suffix_for_match(barcode),
        'has_content': False,
        'note_count': 0,
        'reason_count': 0,
        'image_count': 0,
        'voice_note_count': 0
    }


def _load_suffixed_item_prep_summary_lookup(barcodes):
    targets = {}
    variants = set()
    for barcode in barcodes or []:
        normalized = ss_normalization._normalize_upc_preserve_suffix_for_match(barcode)
        if not ss_normalization._is_items_to_list_suffixed_upc(normalized):
            continue
        targets.setdefault(normalized, _empty_suffixed_item_prep_summary(normalized))
        variants.update(v.casefold() for v in _suffixed_item_prep_variants(barcode))

    if not targets or not variants:
        return targets

    conn = ss_database.get_db_connection('bol.db')
    cur = conn.cursor()
    variant_list = sorted(variants)

    def iter_rows(select_sql):
        for start in range(0, len(variant_list), 300):
            chunk = variant_list[start:start + 300]
            placeholders = ','.join('?' for _ in chunk)
            try:
                cur.execute(
                    select_sql.format(placeholders=placeholders),
                    tuple(chunk)
                )
                yield from cur.fetchall()
            except sqlite3.Error:
                continue

    def summary_for_row(row):
        key = ss_normalization._normalize_upc_preserve_suffix_for_match(row['upc'])
        return targets.get(key)

    for row in iter_rows('''
        SELECT upc, COALESCE(note, '') AS note, COALESCE(reason, '') AS reason
        FROM items_prep_status
        WHERE LOWER(TRIM(COALESCE(upc, ''))) IN ({placeholders})
    '''):
        summary = summary_for_row(row)
        if not summary:
            continue
        if str(row['note'] or '').strip():
            summary['note_count'] += 1
        if str(row['reason'] or '').strip():
            summary['reason_count'] += 1

    for row in iter_rows('''
        SELECT upc
        FROM items_prep_notes
        WHERE LOWER(TRIM(COALESCE(upc, ''))) IN ({placeholders})
          AND TRIM(COALESCE(note, '')) != ''
    '''):
        summary = summary_for_row(row)
        if summary:
            summary['note_count'] += 1

    for row in iter_rows('''
        SELECT upc
        FROM items_prep_images
        WHERE LOWER(TRIM(COALESCE(upc, ''))) IN ({placeholders})
          AND (deleted_at IS NULL OR TRIM(COALESCE(deleted_at, '')) = '')
          AND TRIM(COALESCE(image_path, '')) != ''
    '''):
        summary = summary_for_row(row)
        if summary:
            summary['image_count'] += 1

    for row in iter_rows('''
        SELECT upc
        FROM items_prep_media
        WHERE LOWER(TRIM(COALESCE(upc, ''))) IN ({placeholders})
          AND LOWER(TRIM(COALESCE(media_type, ''))) = 'audio'
          AND TRIM(COALESCE(file_path, '')) != ''
    '''):
        summary = summary_for_row(row)
        if summary:
            summary['voice_note_count'] += 1

    for summary in targets.values():
        summary['has_content'] = any(
            int(summary.get(key) or 0) > 0
            for key in ('note_count', 'reason_count', 'image_count', 'voice_note_count')
        )
    return targets


def _build_suffixed_item_prep_context(cur, barcode):
    normalized = ss_normalization._normalize_upc_preserve_suffix_for_match(barcode)
    summary = _empty_suffixed_item_prep_summary(normalized)
    payload = {
        'barcode': normalized,
        'has_content': False,
        'summary': summary,
        'status_entries': [],
        'notes': [],
        'images': [],
        'voice_notes': []
    }
    variants = _suffixed_item_prep_variants(barcode)
    if not variants:
        return payload

    placeholders = ','.join('?' for _ in variants)
    lower_variants = tuple(v.casefold() for v in variants)
    seen_note_text = set()

    try:
        cur.execute(f'''
            SELECT id, upc, COALESCE(status, '') AS status,
                   COALESCE(reason, '') AS reason, COALESCE(note, '') AS note,
                   COALESCE(updated_at, '') AS updated_at
            FROM items_prep_status
            WHERE LOWER(TRIM(COALESCE(upc, ''))) IN ({placeholders})
            ORDER BY updated_at DESC, id DESC
        ''', lower_variants)
        for row in cur.fetchall():
            item = dict(row)
            if str(item.get('reason') or '').strip():
                summary['reason_count'] += 1
            note_text = str(item.get('note') or '').strip()
            if note_text:
                if note_text.casefold() in seen_note_text:
                    item['note'] = ''
                else:
                    seen_note_text.add(note_text.casefold())
                    summary['note_count'] += 1
            payload['status_entries'].append(item)
    except sqlite3.Error:
        pass

    try:
        cur.execute(f'''
            SELECT id, upc, COALESCE(row_status, '') AS row_status,
                   COALESCE(note, '') AS note, COALESCE(created_at, '') AS created_at
            FROM items_prep_notes
            WHERE LOWER(TRIM(COALESCE(upc, ''))) IN ({placeholders})
              AND TRIM(COALESCE(note, '')) != ''
            ORDER BY created_at DESC, id DESC
        ''', lower_variants)
        for row in cur.fetchall():
            item = dict(row)
            note_text = str(item.get('note') or '').strip()
            if not note_text or note_text.casefold() in seen_note_text:
                continue
            seen_note_text.add(note_text.casefold())
            payload['notes'].append(item)
        summary['note_count'] += len(payload['notes'])
    except sqlite3.Error:
        pass

    try:
        cur.execute(f'''
            SELECT id, upc, COALESCE(row_status, '') AS row_status,
                   COALESCE(image_path, '') AS image_path,
                   COALESCE(created_at, '') AS created_at,
                   COALESCE(rotation, 0) AS rotation
            FROM items_prep_images
            WHERE LOWER(TRIM(COALESCE(upc, ''))) IN ({placeholders})
              AND (deleted_at IS NULL OR TRIM(COALESCE(deleted_at, '')) = '')
              AND TRIM(COALESCE(image_path, '')) != ''
            ORDER BY created_at DESC, id DESC
        ''', lower_variants)
        for row in cur.fetchall():
            item = dict(row)
            image_path = str(item.get('image_path') or '').strip()
            item['image_url'] = (
                image_path
                if image_path.startswith(('http://', 'https://', '/'))
                else f'/static/{image_path}'
            )
            payload['images'].append(item)
        summary['image_count'] = len(payload['images'])
    except sqlite3.Error:
        pass

    try:
        cur.execute(f'''
            SELECT id, upc, COALESCE(row_status, '') AS row_status,
                   COALESCE(file_path, '') AS file_path,
                   COALESCE(mime_type, '') AS mime_type,
                   COALESCE(created_at, '') AS created_at
            FROM items_prep_media
            WHERE LOWER(TRIM(COALESCE(upc, ''))) IN ({placeholders})
              AND LOWER(TRIM(COALESCE(media_type, ''))) = 'audio'
              AND TRIM(COALESCE(file_path, '')) != ''
            ORDER BY created_at DESC, id DESC
        ''', lower_variants)
        for row in cur.fetchall():
            item = dict(row)
            file_path = str(item.get('file_path') or '').strip()
            item['audio_url'] = (
                file_path
                if file_path.startswith(('http://', 'https://', '/'))
                else f'/static/{file_path}'
            )
            payload['voice_notes'].append(item)
        summary['voice_note_count'] = len(payload['voice_notes'])
    except sqlite3.Error:
        pass

    summary['has_content'] = any(
        int(summary.get(key) or 0) > 0
        for key in ('note_count', 'reason_count', 'image_count', 'voice_note_count')
    )
    payload['has_content'] = summary['has_content']
    return payload


def api_warehouse_item_prep_context(barcode):
    try:
        with ss_database.db_connection('bol.db') as conn:
            context = _build_suffixed_item_prep_context(conn.cursor(), barcode)
        return jsonify({'success': True, **context})
    except Exception as e:
        return jsonify({'success': False, 'error': ss_errors._safe_error(e, 'warehouse item prep')}), 500


@ss_runtime.cache.cached(timeout=600, query_string=True)  # Cache for 10 minutes
def api_bol_lookup():
    """Lookup a BOL item by UPC in bol.db and return normalized fields.
    First checks temp_items for custom items, then bol_items.
    Suffix entries are created only by explicit prep actions, not during lookup.
    """
    conn = None
    try:
        upc = ss_normalization._normalize_upc(request.args.get('upc'))
        if not upc:
            return jsonify({'found': False, 'error': 'Missing upc'}), 400
        
        conn = sqlite3.connect('bol.db')
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        
        # First check temp_items table for custom created items
        cur.execute('''
            CREATE TABLE IF NOT EXISTS temp_items (
                upc TEXT PRIMARY KEY,
                item_description TEXT,
                image_url TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        cur.execute("SELECT upc, item_description, image_url FROM temp_items WHERE upc = ? COLLATE NOCASE", (upc,))
        temp_item = cur.fetchone()
        
        if temp_item:
            # Return the temp item in the expected format
            item = {
                'upc': temp_item['upc'],
                'item_description': temp_item['item_description'],
                'image_url': temp_item['image_url'],
                'lot_number': None,
                'bol_number': None,
                'import_date': None,
                'temporary': 1,
                'is_duplicate': False,
                'is_custom': True
            }
            return jsonify({'found': True, 'item': item})
        
        # Ensure temporary column exists in bol_items
        cur.execute('PRAGMA table_info(bol_items)')
        cols = [r[1] for r in cur.fetchall()]
        if 'temporary' not in cols:
            cur.execute('ALTER TABLE bol_items ADD COLUMN temporary INTEGER DEFAULT 0')
            conn.commit()
        
        selected_lot = ss_warehouse_allocations._preferred_lot_from_request()
        rows = []
        rows_in_selected = []
        scope_all = ss_warehouse_allocations._request_lot_scope_is_all()
        if selected_lot:
            cur.execute("""
                SELECT id, upc, item_description, image_url, lot_number, bol_number, import_date, temporary,
                       unchecked_qty, original_qty, good_qty, bad_qty
                FROM bol_items
                WHERE upc = ? COLLATE NOCASE
                  AND lot_number = ? COLLATE NOCASE
                ORDER BY import_date DESC, id DESC
            """, (upc, selected_lot))
            rows_in_selected = cur.fetchall()
            rows = rows_in_selected

        if not rows:
            # Fallback to newest row across lots
            cur.execute("""
                SELECT id, upc, item_description, image_url, lot_number, bol_number, import_date, temporary,
                       unchecked_qty, original_qty, good_qty, bad_qty
                FROM bol_items 
                WHERE upc = ? COLLATE NOCASE 
                ORDER BY import_date DESC, id DESC
            """, (upc,))
            rows = cur.fetchall()
        
        if not rows:
            return jsonify({
                'found': False,
                'requested_lot': selected_lot,
                'scope': ('all' if scope_all else 'current')
            })
        
        lot_mismatch = bool(selected_lot and not rows_in_selected)
        available_lots = []
        seen_lots = set()
        for rr in rows:
            lot_val = ss_normalization._normalize_lot_number(rr['lot_number'])
            if not lot_val:
                continue
            lot_key = lot_val.lower()
            if lot_key in seen_lots:
                continue
            seen_lots.add(lot_key)
            available_lots.append(lot_val)
        
        # Find the newest permanent entry (temporary = 0 or NULL)
        # Since rows are sorted by import_date DESC, the first permanent row is the newest LOT
        permanent_row = None
        for r in rows:
            if not r['temporary']:
                permanent_row = r
                break
        
        if not permanent_row:
            # All rows are temporary (shouldn't happen, but handle gracefully)
            permanent_row = rows[0]
        
        # Check if this item has already been prepped in the relevant lot.
        has_prep_record = False
        status_lot = ss_normalization._normalize_lot_number((permanent_row['lot_number'] if permanent_row else selected_lot))
        if permanent_row:
            ss_prep_schema._ensure_items_prep_tables()
            if status_lot:
                cur.execute('''
                    SELECT status
                    FROM items_prep_status
                    WHERE (upc = ? OR upc LIKE ? COLLATE NOCASE)
                      AND (
                        COALESCE(lot_number, '') = ? COLLATE NOCASE
                        OR COALESCE(lot_number, '') = ''
                      )
                    LIMIT 1
                ''', (upc, f'{upc}-%', status_lot))
            else:
                cur.execute('SELECT status FROM items_prep_status WHERE upc = ? OR upc LIKE ? COLLATE NOCASE LIMIT 1', (upc, f'{upc}-%'))
            status_row = cur.fetchone()
            has_prep_record = status_row is not None

        # Lookup no longer auto-creates temporary suffixed duplicates.
        # Suffix creation now only happens on explicit user actions (bad/return/noted-good).
        item = dict(permanent_row)
        item['is_duplicate'] = False
        item['has_prep_record'] = bool(has_prep_record)

        # Expose remaining unchecked quantity so the UI can warn before forced exception adds.
        try:
            unchecked_raw = item.get('unchecked_qty')
            if unchecked_raw is None:
                original_raw = int(item.get('original_qty') or 0)
                good_raw = int(item.get('good_qty') or 0)
                bad_raw = int(item.get('bad_qty') or 0)
                unchecked_remaining = max(0, original_raw - good_raw - bad_raw)
            else:
                unchecked_remaining = int(unchecked_raw or 0)
        except Exception:
            unchecked_remaining = 0
        base_lookup_upc = (upc.split('-')[0] if '-' in str(upc) else upc)
        item['unchecked_remaining'] = unchecked_remaining
        item['is_overage_candidate'] = bool(unchecked_remaining <= 0)
        item['items_to_list_url'] = ss_warehouse_allocations._items_prep_items_to_list_url(base_lookup_upc, item.get('lot_number') or selected_lot)
        item['requested_lot'] = selected_lot
        item['lot_mismatch'] = bool(lot_mismatch)
        item['resolved_lot'] = ss_normalization._normalize_lot_number(item.get('lot_number'))
        item['available_lots'] = available_lots
        
        
        # Also include current prep status if exists
        conn2 = None
        try:
            conn2 = sqlite3.connect('bol.db')
            conn2.row_factory = sqlite3.Row
            cur2 = conn2.cursor()
            prep_lot = ss_normalization._normalize_lot_number(item.get('lot_number') or selected_lot)
            srow = ss_warehouse_allocations._select_prep_status_row(cur2, item['upc'], prep_lot, columns='status, reason, note, updated_at, quantity, lot_number')
            if srow:
                item['prep_status'] = dict(srow)
        except Exception:
            item['prep_status'] = None
        finally:
            if conn2 is not None:
                conn2.close()

        return jsonify({
            'found': True,
            'item': item,
            'requested_lot': selected_lot,
            'lot_mismatch': bool(lot_mismatch),
            'resolved_lot': ss_normalization._normalize_lot_number(item.get('lot_number')),
            'available_lots': available_lots
        })
    except Exception as e:
        return jsonify({'found': False, 'error': ss_errors._safe_error(e)}), 500
    finally:
        if conn is not None:
            conn.close()


def api_items_prep_auto_assign_lot():
    """Resolve a lot for item-prep actions.
    - If UPC exists in bol_items: use newest available lot.
    - Else if UPC exists in raw_bol_items with lot: seed bol_items using that lot.
    - Else create a new uncategorized lot entry (UNCATEGORIZED-<UPC>).
    """
    conn = None
    raw_conn = None
    try:
        data = request.get_json() or {}
        upc_raw = ss_normalization._normalize_upc(data.get('upc'))
        upc = ss_normalization._normalize_upc_preserve_suffix_for_match(upc_raw)
        if not upc:
            return jsonify({'success': False, 'error': 'Missing upc'}), 400
        base_upc = upc.split('-', 1)[0] if '-' in upc else upc

        conn = sqlite3.connect('bol.db')
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        ss_prep_schema._ensure_items_prep_tables()

        # 1) Existing lot in bol_items.
        cur.execute('''
            SELECT id, lot_number
            FROM bol_items
            WHERE upc = ? COLLATE NOCASE
            ORDER BY import_date DESC, id DESC
            LIMIT 1
        ''', (base_upc,))
        row = cur.fetchone()
        if row:
            lot_number = ss_normalization._normalize_lot_number(row['lot_number'])
            if lot_number:
                return jsonify({
                    'success': True,
                    'upc': base_upc,
                    'assigned_lot': lot_number,
                    'created_entry': False,
                    'source': 'bol_items_existing'
                })

        # 2) Try rawbol metadata.
        raw_desc = ''
        raw_image = ''
        raw_lot = ''
        try:
            raw_conn = sqlite3.connect('rawbol.db')
            raw_conn.row_factory = sqlite3.Row
            raw_cur = raw_conn.cursor()
            raw_cur.execute('''
                SELECT upc, item_description, image_url, lot_number
                FROM raw_bol_items
                WHERE upc = ? COLLATE NOCASE
                ORDER BY import_date DESC, id DESC
                LIMIT 1
            ''', (base_upc,))
            raw_row = raw_cur.fetchone()
            if raw_row:
                raw_desc = (raw_row['item_description'] or '').strip()
                raw_image = (raw_row['image_url'] or '').strip()
                raw_lot = ss_normalization._normalize_lot_number(raw_row['lot_number'])
        except Exception:
            raw_desc = raw_desc or ''
            raw_image = raw_image or ''
            raw_lot = raw_lot or ''
        finally:
            if raw_conn is not None:
                raw_conn.close()
                raw_conn = None

        assigned_lot = raw_lot or f'UNCATEGORIZED-{base_upc}'

        # 3) If already seeded for that lot, reuse it.
        cur.execute('''
            SELECT id
            FROM bol_items
            WHERE upc = ? COLLATE NOCASE
              AND COALESCE(lot_number, '') = ? COLLATE NOCASE
            ORDER BY import_date DESC, id DESC
            LIMIT 1
        ''', (base_upc, assigned_lot))
        seeded = cur.fetchone()
        if seeded:
            return jsonify({
                'success': True,
                'upc': base_upc,
                'assigned_lot': assigned_lot,
                'created_entry': False,
                'source': 'bol_items_seeded'
            })

        # 4) Seed a new bol_items row.
        cur.execute('PRAGMA table_info(bol_items)')
        cols = {r[1] for r in cur.fetchall()}
        now_iso = ss_listing_checks._listagent_now_iso()
        payload = {
            'upc': base_upc,
            'item_description': raw_desc or f'Uncategorized Item {base_upc}',
            'image_url': raw_image or '',
            'lot_number': assigned_lot,
            'bol_number': assigned_lot,
            'import_date': now_iso,
            'temporary': 0,
            'original_qty': 1,
            'unchecked_qty': 1,
            'good_qty': 0,
            'bad_qty': 0,
            'quantity': 1
        }
        insert_cols = [k for k in payload.keys() if k in cols]
        if 'upc' not in insert_cols:
            return jsonify({'success': False, 'error': 'bol_items schema is missing upc'}), 500
        placeholders = ','.join(['?'] * len(insert_cols))
        cur.execute(
            f"INSERT INTO bol_items ({','.join(insert_cols)}) VALUES ({placeholders})",
            tuple(payload[k] for k in insert_cols)
        )
        conn.commit()
        try:
            ss_caching.update_data_version()
        except Exception:
            pass

        return jsonify({
            'success': True,
            'upc': base_upc,
            'assigned_lot': assigned_lot,
            'created_entry': True,
            'source': ('rawbol_seed' if raw_lot else 'uncategorized_created')
        })
    except Exception as e:
        return jsonify({'success': False, 'error': ss_errors._safe_error(e, 'items_prep:auto_assign_lot')}), 500
    finally:
        if raw_conn is not None:
            raw_conn.close()
        if conn is not None:
            conn.close()
