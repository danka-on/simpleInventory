"""Inventory age evidence for Sweet Shelves."""

import datetime
import hashlib
import json
import re
import sqlite3
from pathlib import Path
from . import (
    config as ss_config, inventory_age as ss_inventory_age, inventory_history as ss_inventory_history,
    normalization as ss_normalization,
)


def _inventory_age_exact_key(value):
    return str(
        ss_normalization._normalize_upc_preserve_suffix_for_match(value) or ''
    ).strip().casefold()


def _inventory_age_base_key(value):
    return _inventory_age_exact_key(value).split('-', 1)[0]


def _inventory_age_lot_key(value):
    lot = str(value or '').strip()
    if (
        not lot
        or lot.casefold() in {'nan', 'none', 'null', 'n/a', 'unknown'}
        or lot.casefold().startswith('uncategorized')
    ):
        return ''
    return lot.casefold()


def _inventory_age_confidence_label(score):
    score = max(0, min(100, ss_normalization._coerce_int(score, 0)))
    if score >= 90:
        return 'high'
    if score >= 70:
        return 'medium'
    return 'low'


def _inventory_age_evidence_paths(evidence_paths=None):
    provided = dict(evidence_paths or {})

    def selected(short_name, filename):
        value = provided.get(short_name) or provided.get(filename)
        return Path(value) if value else (ss_config.BASE_DIR / filename)

    return {
        'rawbol': selected('rawbol', 'rawbol.db'),
        'bol': selected('bol', 'bol.db'),
        'preplog': selected('preplog', 'preplog.db'),
    }


def _inventory_age_load_receipt_evidence(evidence_paths=None):
    """Load immutable receipt dates plus Item Manager/prep lot evidence."""
    from collections import defaultdict

    paths = _inventory_age_evidence_paths(evidence_paths)
    raw_groups = {}
    upload_dates_by_lot = defaultdict(list)
    bol_pairs = set()
    bol_suffix_lots = defaultdict(set)
    prep_by_exact = defaultdict(list)
    status_by_exact_lot = {}
    warnings = []
    fatal_errors = []
    fingerprint_rows = []

    raw_path = paths['rawbol']
    if raw_path.exists():
        raw_conn = sqlite3.connect(str(raw_path), timeout=30.0)
        raw_conn.row_factory = sqlite3.Row
        try:
            raw_cur = raw_conn.cursor()
            try:
                raw_cur.execute('''
                    SELECT id, filename, lot_number, import_date, uploaded_at
                    FROM upload_logs
                    ORDER BY id
                ''')
                for row in raw_cur.fetchall():
                    lot_key = _inventory_age_lot_key(row['lot_number'])
                    import_at = ss_inventory_history._history_timestamp(row['import_date'])
                    if lot_key and import_at is not None:
                        upload_dates_by_lot[lot_key].append(import_at)
                    fingerprint_rows.append((
                        'upload', row['id'], row['filename'], row['lot_number'],
                        row['import_date'], row['uploaded_at']
                    ))
            except sqlite3.Error as exc:
                fatal_errors.append(f'Raw BOL upload log unavailable: {exc}')

            raw_cur.execute('''
                SELECT id, upc, quantity, lot_number, import_date, created_at
                FROM raw_bol_items
                ORDER BY id
            ''')
            for row in raw_cur.fetchall():
                base_key = _inventory_age_base_key(row['upc'])
                lot_key = _inventory_age_lot_key(row['lot_number'])
                if not base_key or not lot_key:
                    continue
                group = raw_groups.setdefault((base_key, lot_key), {
                    'base_key': base_key,
                    'lot_key': lot_key,
                    'lot_number': str(row['lot_number'] or '').strip(),
                    'quantity': 0,
                    'raw_ids': [],
                    'import_dates': [],
                    'created_dates': [],
                })
                group['quantity'] += max(0, ss_normalization._coerce_int(row['quantity'], 0))
                group['raw_ids'].append(max(0, ss_normalization._coerce_int(row['id'], 0)))
                parsed_import = ss_inventory_history._history_timestamp(row['import_date'])
                if parsed_import is not None:
                    group['import_dates'].append(parsed_import)
                parsed_created = ss_inventory_history._history_timestamp(row['created_at'])
                if parsed_created is not None:
                    group['created_dates'].append(parsed_created)
                fingerprint_rows.append((
                    'raw', row['id'], row['upc'], row['quantity'],
                    row['lot_number'], row['import_date'], row['created_at']
                ))
        finally:
            raw_conn.close()
    else:
        fatal_errors.append(
            f'Raw BOL database not found: {raw_path.name}'
        )

    bol_path = paths['bol']
    if bol_path.exists():
        bol_conn = sqlite3.connect(str(bol_path), timeout=30.0)
        bol_conn.row_factory = sqlite3.Row
        try:
            bol_cur = bol_conn.cursor()
            try:
                bol_cur.execute('''
                    SELECT id, upc, lot_number
                    FROM bol_items
                    ORDER BY id
                ''')
                for row in bol_cur.fetchall():
                    exact_key = _inventory_age_exact_key(row['upc'])
                    base_key = _inventory_age_base_key(row['upc'])
                    lot_key = _inventory_age_lot_key(row['lot_number'])
                    if base_key and lot_key:
                        bol_pairs.add((base_key, lot_key))
                        if exact_key and '-' in exact_key:
                            bol_suffix_lots[exact_key].add(lot_key)
                    fingerprint_rows.append((
                        'bol', row['id'], row['upc'], row['lot_number']
                    ))
            except sqlite3.Error as exc:
                fatal_errors.append(f'BOL item evidence unavailable: {exc}')

            try:
                bol_cur.execute('''
                    SELECT id, upc, lot_number, status, quantity, updated_at
                    FROM items_prep_status
                    WHERE LOWER(TRIM(COALESCE(status, ''))) = 'good'
                      AND COALESCE(CAST(quantity AS INTEGER), 0) > 0
                    ORDER BY id
                ''')
                for row in bol_cur.fetchall():
                    exact_key = _inventory_age_exact_key(row['upc'])
                    lot_key = _inventory_age_lot_key(row['lot_number'])
                    if exact_key and lot_key:
                        key = (exact_key, lot_key)
                        status = status_by_exact_lot.setdefault(key, {
                            'exact_key': exact_key,
                            'lot_key': lot_key,
                            'quantity': 0,
                            'ids': [],
                            'updated_at': [],
                        })
                        status['quantity'] += max(
                            0, ss_normalization._coerce_int(row['quantity'], 0)
                        )
                        status['ids'].append(max(0, ss_normalization._coerce_int(row['id'], 0)))
                        parsed_updated = ss_inventory_history._history_timestamp(row['updated_at'])
                        if parsed_updated is not None:
                            status['updated_at'].append(parsed_updated)
                    fingerprint_rows.append((
                        'status', row['id'], row['upc'], row['lot_number'],
                        row['status'], row['quantity'], row['updated_at']
                    ))
            except sqlite3.Error as exc:
                fatal_errors.append(
                    f'Item Manager prep status unavailable: {exc}'
                )
        finally:
            bol_conn.close()
    else:
        fatal_errors.append(
            f'BOL/Item Manager database not found: {bol_path.name}'
        )

    preplog_path = paths['preplog']
    if preplog_path.exists():
        prep_conn = sqlite3.connect(str(preplog_path), timeout=30.0)
        prep_conn.row_factory = sqlite3.Row
        try:
            prep_cur = prep_conn.cursor()
            try:
                prep_cur.execute('''
                    SELECT id, created_at, upc, base_upc, status, quantity,
                           meta_json, undone
                    FROM prep_log
                    WHERE COALESCE(undone, 0) = 0
                      AND LOWER(TRIM(COALESCE(status, ''))) = 'good'
                      AND COALESCE(CAST(quantity AS INTEGER), 0) > 0
                    ORDER BY created_at, id
                ''')
                for row in prep_cur.fetchall():
                    try:
                        meta = json.loads(row['meta_json'] or '{}')
                    except Exception:
                        meta = {}
                    if not isinstance(meta, dict):
                        meta = {}
                    lot_number = (
                        meta.get('lot_number')
                        or meta.get('assigned_lot')
                        or meta.get('lot')
                    )
                    exact_key = _inventory_age_exact_key(row['upc'])
                    lot_key = _inventory_age_lot_key(lot_number)
                    created_at = ss_inventory_history._history_timestamp(row['created_at'])
                    if exact_key and lot_key and created_at is not None:
                        prep_by_exact[exact_key].append({
                            'id': max(0, ss_normalization._coerce_int(row['id'], 0)),
                            'exact_key': exact_key,
                            'lot_key': lot_key,
                            'created_at': created_at,
                            'quantity': max(
                                0, ss_normalization._coerce_int(row['quantity'], 0)
                            ),
                            'auto_assigned': bool(meta.get('auto_assigned')),
                            'forced_lot_override': bool(
                                meta.get('forced_lot_override')
                            ),
                            'exception_overage': bool(
                                meta.get('exception_overage')
                            ),
                            'needs_review': bool(meta.get('needs_review')),
                            'continued_without_lot': bool(
                                meta.get('continued_without_lot')
                            ),
                        })
                    fingerprint_rows.append((
                        'prep', row['id'], row['created_at'], row['upc'],
                        row['base_upc'], row['status'], row['quantity'],
                        row['meta_json'], row['undone']
                    ))
            except sqlite3.Error as exc:
                fatal_errors.append(f'Prep-log evidence unavailable: {exc}')
        finally:
            prep_conn.close()
    else:
        fatal_errors.append(
            f'Prep-log database not found: {preplog_path.name}'
        )

    catalog = defaultdict(dict)
    for (base_key, lot_key), group in raw_groups.items():
        import_dates = sorted(group['import_dates'])
        upload_dates = sorted(upload_dates_by_lot.get(lot_key, []))
        if import_dates:
            received_at = import_dates[0]
            date_source = 'raw_bol_import_date'
        elif upload_dates:
            received_at = upload_dates[0]
            date_source = 'upload_log_import_date'
        else:
            # A Raw-BOL ingestion timestamp is not a purchase date, so it is
            # intentionally excluded from the validated receipt catalog.
            continue
        upload_confirmed = any(
            value.date() == received_at.date()
            for value in upload_dates
        )
        catalog[base_key][lot_key] = {
            **group,
            'received_at': received_at,
            'date_source': date_source,
            'upload_confirmed': upload_confirmed,
            'bol_confirmed': (base_key, lot_key) in bol_pairs,
        }

    for exact_key in prep_by_exact:
        prep_by_exact[exact_key].sort(key=lambda item: (
            item['created_at'], item['id']
        ))

    fingerprint_payload = json.dumps(
        sorted(fingerprint_rows, key=lambda value: tuple(str(v) for v in value)),
        ensure_ascii=False,
        default=str,
        separators=(',', ':'),
    )
    fingerprint = hashlib.sha256(
        fingerprint_payload.encode('utf-8')
    ).hexdigest()
    return {
        'paths': paths,
        'catalog': {key: dict(value) for key, value in catalog.items()},
        'prep_by_exact': dict(prep_by_exact),
        'status_by_exact_lot': status_by_exact_lot,
        'bol_suffix_lots': {
            key: set(value) for key, value in bol_suffix_lots.items()
        },
        'warnings': warnings,
        'fatal_errors': fatal_errors,
        'complete': not fatal_errors,
        'fingerprint': fingerprint,
        'raw_pairs': sum(len(value) for value in catalog.values()),
    }


def _inventory_age_load_history(
    active_barcode_keys, history_path=None, active_base_keys=None
):
    """Read relevant rack events and suppress paired location-only snapshots."""
    from collections import defaultdict

    def normalized_location(value):
        text = str(value or '').strip().casefold()
        if not text or text == 'picture':
            return ''
        return re.sub(r'[^a-z0-9]', '', text)

    def snapshot_location(raw_snapshot):
        try:
            snapshot = json.loads(raw_snapshot or '{}')
        except Exception:
            snapshot = {}
        if not isinstance(snapshot, dict):
            return ''
        folded = {
            str(key).casefold(): value
            for key, value in snapshot.items()
        }
        item_location = normalized_location(
            folded.get('item_position')
        )
        if item_location:
            return item_location
        return normalized_location(folded.get('pictureposition'))

    def event_location(event, direction):
        if direction == 'source':
            candidates = (
                event.get('from_position'),
                snapshot_location(event.get('source_row_json')),
                event.get('item_position'),
            )
        else:
            candidates = (
                event.get('to_position'),
                snapshot_location(event.get('result_row_json')),
                event.get('item_position'),
            )
        for candidate in candidates:
            normalized = normalized_location(candidate)
            if normalized:
                return normalized
        return ''

    resolved_path = Path(history_path or (ss_config.BASE_DIR / 'rackhistory.db'))
    if not resolved_path.exists():
        return {
            'events': [],
            'fingerprint': hashlib.sha256(b'').hexdigest(),
            'movement_events_suppressed': 0,
            'path': resolved_path,
            'available': False,
            'error': f'Rack-history database not found: {resolved_path.name}',
        }

    history_conn = sqlite3.connect(str(resolved_path), timeout=30.0)
    history_conn.row_factory = sqlite3.Row
    active_base_keys = set(active_base_keys or ())
    fingerprint_rows = []
    events = []
    try:
        history_cur = history_conn.cursor()
        history_cur.execute(
            "SELECT 1 FROM sqlite_master "
            "WHERE type='table' AND name='removed_items'"
        )
        if not history_cur.fetchone():
            return {
                'events': [],
                'fingerprint': hashlib.sha256(b'').hexdigest(),
                'movement_events_suppressed': 0,
                'path': resolved_path,
                'available': False,
                'error': 'Rack-history event table is missing',
            }
        history_cur.execute('''
            SELECT id, barcode, removed_at, searchrack_id,
                   old_quantity, new_quantity, removal_type,
                   source_row_json, result_row_json, event_id,
                   event_status, undone_at, item_position,
                   from_position, to_position
            FROM removed_items
            WHERE COALESCE(event_status, 'applied') != 'superseded'
              AND (undone_at IS NULL OR undone_at = '')
            ORDER BY removed_at, id
        ''')
        for raw_event in history_cur.fetchall():
            event = dict(raw_event)
            barcode_key = _inventory_age_exact_key(event.get('barcode'))
            base_key = _inventory_age_base_key(barcode_key)
            occurred_at = ss_inventory_history._history_timestamp(event.get('removed_at'))
            row_id = event.get('searchrack_id')
            if (
                not barcode_key
                or (
                    barcode_key not in active_barcode_keys
                    and base_key not in active_base_keys
                )
                or occurred_at is None
                or row_id in (None, '')
            ):
                continue
            event['_barcode_key'] = barcode_key
            event['_base_key'] = base_key
            event['_occurred_at'] = occurred_at
            event['_row_id'] = int(row_id)
            event['_source_location'] = event_location(event, 'source')
            event['_destination_location'] = event_location(
                event, 'destination'
            )
            events.append(event)
            fingerprint_rows.append((
                event.get('id'), event.get('barcode'), event.get('removed_at'),
                event.get('searchrack_id'), event.get('old_quantity'),
                event.get('new_quantity'), event.get('removal_type'),
                event.get('source_row_json'), event.get('result_row_json'),
                event.get('event_id'), event.get('event_status'),
                event.get('undone_at'), event.get('item_position'),
                event.get('from_position'), event.get('to_position')
            ))
    finally:
        history_conn.close()

    movement_groups = defaultdict(list)
    for event in events:
        removal_type = str(
            event.get('removal_type') or ''
        ).strip().casefold()
        if removal_type not in {'locationmoved', 'locationcleared'}:
            continue
        group_key = (
            event['_row_id'],
            event['_barcode_key'],
            event['_occurred_at'].isoformat(),
            removal_type,
        )
        movement_groups[group_key].append(event)

    suppressed_ids = set()
    for grouped in movement_groups.values():
        deltas = [
            ss_normalization._coerce_int(event.get('new_quantity'), 0)
            - ss_normalization._coerce_int(event.get('old_quantity'), 0)
            for event in grouped
        ]
        if (
            sum(deltas) == 0
            and any(value > 0 for value in deltas)
            and any(value < 0 for value in deltas)
        ):
            suppressed_ids.update(
                max(0, ss_normalization._coerce_int(event.get('id'), 0))
                for event in grouped
            )
    if suppressed_ids:
        events = [
            event for event in events
            if max(0, ss_normalization._coerce_int(event.get('id'), 0)) not in suppressed_ids
        ]

    events.sort(key=lambda event: (
        event['_occurred_at'],
        max(0, ss_normalization._coerce_int(event.get('id'), 0)),
    ))
    fingerprint_payload = json.dumps(
        fingerprint_rows,
        ensure_ascii=False,
        default=str,
        separators=(',', ':'),
    )
    return {
        'events': events,
        'fingerprint': hashlib.sha256(
            fingerprint_payload.encode('utf-8')
        ).hexdigest(),
        'movement_events_suppressed': len(suppressed_ids),
        'path': resolved_path,
        'available': True,
        'error': '',
    }


def _inventory_age_replay_history(
    history_events, evidence, match_window_days=2
):
    """Rebuild per-unit warehouse lineages and preserve delete/rescan moves."""
    row_units = {}
    parked_by_barcode = {}
    restoring_rows = {}
    stats = {
        'history_events_used': 0,
        'matched_reorg_units': 0,
        'matched_short_gap_reorg_units': 0,
        'matched_historical_exception_units': 0,
        'inferred_history_units': 0,
        'reorg_units_blocked_by_new_receipt': 0,
        'reorg_units_blocked_by_confirmed_receipt': 0,
        'reorg_units_blocked_by_possible_receipt': 0,
        'pending_unmatched_clear_units': 0,
    }
    reusable_removal_types = {
        'inventoryremoved',
        'manual_multidb_delete',
    }
    restore_addition_types = {
        'add_to_shelf',
        'inventory_added',
    }
    match_window = datetime.timedelta(
        days=max(1, int(match_window_days or 2))
    )
    # Audited legacy exception: OR3 was bulk-cleared May 5–6, 2026 and
    # deliberately left empty until its July reinventory. Keep this bounded
    # to the observed operation; future rack clears use the normal 2-day
    # workflow window.
    historical_exception_window = datetime.timedelta(days=180)
    historical_or3_removed_start = datetime.datetime(2026, 5, 5)
    historical_or3_removed_end = datetime.datetime(2026, 5, 7)
    historical_or3_added_start = datetime.datetime(2026, 7, 15)
    historical_or3_added_end = datetime.datetime(2026, 8, 1)
    new_receipt_token_remaining = {
        (exact_key, token['id']): max(
            0, ss_normalization._coerce_int(token.get('quantity'), 0)
        )
        for exact_key, tokens in evidence.get('prep_by_exact', {}).items()
        for token in tokens
    }
    receipt_capacity_remaining = {
        (base_key, lot_key): max(
            0, ss_normalization._coerce_int(lot_info.get('quantity'), 0)
        )
        for base_key, lots in evidence.get('catalog', {}).items()
        for lot_key, lot_info in lots.items()
    }

    def snapshot_created_at(event):
        for raw_snapshot in (
            event.get('source_row_json'),
            event.get('result_row_json'),
        ):
            try:
                snapshot = json.loads(raw_snapshot or '{}')
            except Exception:
                snapshot = {}
            if not isinstance(snapshot, dict):
                continue
            folded = {
                str(key).casefold(): value
                for key, value in snapshot.items()
            }
            parsed = ss_inventory_history._history_timestamp(folded.get('created_at'))
            if parsed is not None:
                return parsed
        return event['_occurred_at']

    def normalized_location(value):
        text = str(value or '').strip().casefold()
        if not text or text == 'picture':
            return ''
        return re.sub(r'[^a-z0-9]', '', text)

    def snapshot_location(raw_snapshot):
        try:
            snapshot = json.loads(raw_snapshot or '{}')
        except Exception:
            snapshot = {}
        if not isinstance(snapshot, dict):
            return ''
        folded = {
            str(key).casefold(): value
            for key, value in snapshot.items()
        }
        item_location = normalized_location(
            folded.get('item_position')
        )
        if item_location:
            return item_location
        return normalized_location(folded.get('pictureposition'))

    def event_location(event, direction):
        derived_key = (
            '_source_location'
            if direction == 'source'
            else '_destination_location'
        )
        derived_location = normalized_location(event.get(derived_key))
        if derived_location:
            return derived_location
        if direction == 'source':
            candidates = (
                event.get('from_position'),
                snapshot_location(event.get('source_row_json')),
                event.get('item_position'),
            )
        else:
            candidates = (
                event.get('to_position'),
                snapshot_location(event.get('result_row_json')),
                event.get('item_position'),
            )
        for candidate in candidates:
            location = normalized_location(candidate)
            if location:
                return location
        return ''

    def historical_location_scope(location):
        normalized = normalized_location(location)
        if re.match(r'^or3(?:s|$)', normalized):
            return 'or3'
        if re.match(r'^or2s5(?:b\d+)?$', normalized):
            return 'or2s5'
        if re.match(r'^or2s6(?:b\d+)?$', normalized):
            return 'or2s6'
        return ''

    def reorg_match_type(event, parked):
        gap = event['_occurred_at'] - parked['parked_at']
        if not datetime.timedelta(0) <= gap:
            return ''
        if gap <= match_window:
            return 'short_gap'
        source_scope = historical_location_scope(
            parked.get('source_location')
        )
        if (
            source_scope == 'or3'
            and historical_or3_removed_start
            <= parked['parked_at']
            < historical_or3_removed_end
            and historical_or3_added_start
            <= event['_occurred_at']
            < historical_or3_added_end
            and gap <= historical_exception_window
        ):
            return 'known_historical_office_reinventory'
        return ''

    def receipt_conflict_between(event, parked):
        exact_key = event['_barcode_key']
        base_key = event['_base_key']
        catalog = evidence.get('catalog', {}).get(base_key, {})
        if not catalog:
            return ''
        parked_at = parked['parked_at']
        add_at = event['_occurred_at']
        parked_day = parked_at.date()
        add_day = add_at.date()
        for token in evidence.get('prep_by_exact', {}).get(
            exact_key, []
        ):
            token_key = (exact_key, token['id'])
            if new_receipt_token_remaining.get(token_key, 0) <= 0:
                continue
            lot_info = catalog.get(token['lot_key'])
            if not lot_info:
                continue
            capacity_key = (base_key, token['lot_key'])
            if receipt_capacity_remaining.get(capacity_key, 0) <= 0:
                continue
            received_at = lot_info['received_at']
            token_at = token['created_at']
            if (
                parked_day <= received_at.date() <= add_day
                and parked_at <= token_at
                <= add_at + datetime.timedelta(days=2)
            ):
                new_receipt_token_remaining[token_key] -= 1
                receipt_capacity_remaining[capacity_key] -= 1
                return 'confirmed'
        possible_lots = sorted(
            (
                lot_key,
                lot_info,
            )
            for lot_key, lot_info in catalog.items()
            if (
                lot_info.get('received_at') is not None
                and parked_day
                <= lot_info['received_at'].date()
                <= add_day
                and receipt_capacity_remaining.get(
                    (base_key, lot_key), 0
                ) > 0
            )
        )
        if possible_lots:
            possible_lot_key = possible_lots[0][0]
            receipt_capacity_remaining[
                (base_key, possible_lot_key)
            ] -= 1
            return 'possible'
        return ''

    def row_key(event):
        return (event['_row_id'], event['_barcode_key'])

    def unit_sort_key(unit):
        return (
            unit['received_at'],
            tuple(unit.get('history_ids') or []),
        )

    def sync_row_quantity(event, target_quantity):
        units = row_units.setdefault(row_key(event), [])
        target_quantity = max(0, ss_normalization._coerce_int(target_quantity, 0))
        if len(units) < target_quantity:
            missing = target_quantity - len(units)
            inferred_at = snapshot_created_at(event)
            for _ in range(missing):
                units.append({
                    'received_at': inferred_at,
                    'first_seen': inferred_at,
                    'lineage_source': 'rack_snapshot',
                    'lineage_confidence': 55,
                    'history_ids': [
                        max(0, ss_normalization._coerce_int(event.get('id'), 0))
                    ],
                })
            stats['inferred_history_units'] += missing
        elif len(units) > target_quantity:
            # Unrecorded decrements are treated as FIFO consumption.
            del units[:len(units) - target_quantity]
        units.sort(key=unit_sort_key)
        return units

    for event in history_events:
        old_quantity = max(0, ss_normalization._coerce_int(event.get('old_quantity'), 0))
        new_quantity = max(0, ss_normalization._coerce_int(event.get('new_quantity'), 0))
        if old_quantity == new_quantity:
            continue
        stats['history_events_used'] += 1
        units = sync_row_quantity(event, old_quantity)
        removal_type = str(
            event.get('removal_type') or ''
        ).strip().casefold()

        if new_quantity > old_quantity:
            add_quantity = new_quantity - old_quantity
            restored_units = []
            restore_key = row_key(event)
            last_restore_at = restoring_rows.get(restore_key)
            continuing_restore = (
                last_restore_at is not None
                and datetime.timedelta(0)
                <= event['_occurred_at'] - last_restore_at
                <= datetime.timedelta(days=1)
            )
            if (
                removal_type in restore_addition_types
                and (old_quantity == 0 or continuing_restore)
            ):
                parked = parked_by_barcode.setdefault(
                    event['_barcode_key'], []
                )
                eligible = []
                for item in parked:
                    if item['source_row_id'] == event['_row_id']:
                        continue
                    match_type = reorg_match_type(event, item)
                    if match_type:
                        eligible.append((item, match_type))
                eligible.sort(key=lambda match: (
                    0 if match[1] == 'short_gap' else 1,
                    -match[0]['parked_at'].timestamp(),
                    match[0]['unit']['received_at'],
                ))
                classified_fresh = 0
                for item, match_type in eligible:
                    if (
                        len(restored_units) + classified_fresh
                        >= add_quantity
                    ):
                        break
                    receipt_conflict = receipt_conflict_between(
                        event, item
                    )
                    if receipt_conflict:
                        stats['reorg_units_blocked_by_new_receipt'] += 1
                        stats[
                            'reorg_units_blocked_by_'
                            + receipt_conflict
                            + '_receipt'
                        ] += 1
                        classified_fresh += 1
                        continue
                    restored_unit = item['unit']
                    addition_history_id = max(
                        0, ss_normalization._coerce_int(event.get('id'), 0)
                    )
                    restored_unit['history_ids'] = sorted(set(
                        list(restored_unit.get('history_ids') or [])
                        + [item['history_id'], addition_history_id]
                    ))
                    restored_unit['reorg_match_type'] = match_type
                    continuity_score = (
                        96 if match_type == 'short_gap' else 82
                    )
                    restored_unit['lineage_confidence'] = min(
                        max(
                            0,
                            ss_normalization._coerce_int(
                                restored_unit.get(
                                    'lineage_confidence'
                                ),
                                55,
                            ),
                        ),
                        continuity_score,
                    )
                    restored_unit.setdefault(
                        'reorg_matches', []
                    ).append({
                        'match_type': match_type,
                        'from_location': item.get(
                            'source_location'
                        ) or '',
                        'to_location': event_location(
                            event, 'destination'
                        ),
                        'gap_hours': round(
                            (
                                event['_occurred_at']
                                - item['parked_at']
                            ).total_seconds() / 3600,
                            2,
                        ),
                        'continuity_score': continuity_score,
                        'removal_history_id': item['history_id'],
                        'addition_history_id': addition_history_id,
                    })
                    if match_type == 'known_historical_office_reinventory':
                        restored_unit['lineage_confidence'] = min(
                            55,
                            max(
                                0,
                                ss_normalization._coerce_int(
                                    restored_unit.get(
                                        'lineage_confidence'
                                    ),
                                    55,
                                ),
                            ),
                        )
                        stats[
                            'matched_historical_exception_units'
                        ] += 1
                    else:
                        stats['matched_short_gap_reorg_units'] += 1
                    restored_units.append(restored_unit)
                    parked.remove(item)
                stats['matched_reorg_units'] += len(restored_units)
                if restored_units or classified_fresh:
                    restoring_rows[restore_key] = event['_occurred_at']

            units.extend(restored_units)
            for _ in range(add_quantity - len(restored_units)):
                units.append({
                    'received_at': event['_occurred_at'],
                    'first_seen': event['_occurred_at'],
                    'lineage_source': 'rack_add_event',
                    'lineage_confidence': 65,
                    'history_ids': [
                        max(0, ss_normalization._coerce_int(event.get('id'), 0))
                    ],
                })
            units.sort(key=unit_sort_key)
        else:
            restoring_rows.pop(row_key(event), None)
            remove_quantity = old_quantity - new_quantity
            consumed = units[:remove_quantity]
            del units[:remove_quantity]
            if removal_type in reusable_removal_types and new_quantity == 0:
                parked = parked_by_barcode.setdefault(
                    event['_barcode_key'], []
                )
                for unit in consumed:
                    parked.append({
                        'unit': unit,
                        'parked_at': event['_occurred_at'],
                        'history_id': max(
                            0, ss_normalization._coerce_int(event.get('id'), 0)
                        ),
                        'source_row_id': event['_row_id'],
                        'source_location': event_location(
                            event, 'source'
                        ),
                    })

    stats['pending_unmatched_clear_units'] = sum(
        len(items) for items in parked_by_barcode.values()
    )
    return row_units, stats


def _inventory_age_depleted_raw_capacity(history_events, evidence):
    """Subtract known permanent warehouse exits from Raw-BOL supply FIFO."""
    remaining = {}
    depleted_by_pair = {}
    for base_key, lots in evidence['catalog'].items():
        for lot_key, lot_info in lots.items():
            pair = (base_key, lot_key)
            remaining[pair] = max(
                0, ss_normalization._coerce_int(lot_info.get('quantity'), 0)
            )
            depleted_by_pair[pair] = 0

    permanent_removal_types = {
        'manual_handled',
        'manual_sold_removal',
        'repair_removal',
        'automatic',
        'automatic_allocated',
        'finder_removal',
        'prep_history_delete',
    }
    stats = {
        'known_permanent_removal_units': 0,
        'raw_capacity_depleted_units': 0,
        'unmatched_permanent_removal_units': 0,
        'bases_with_raw_capacity_depletion': set(),
    }
    for event in history_events:
        removal_type = str(
            event.get('removal_type') or ''
        ).strip().casefold()
        if removal_type not in permanent_removal_types:
            continue
        remove_quantity = max(
            0,
            ss_normalization._coerce_int(event.get('old_quantity'), 0)
            - ss_normalization._coerce_int(event.get('new_quantity'), 0),
        )
        if remove_quantity <= 0:
            continue
        stats['known_permanent_removal_units'] += remove_quantity
        base_key = event.get('_base_key') or _inventory_age_base_key(
            event.get('barcode')
        )
        lots = evidence['catalog'].get(base_key, {})
        eligible = sorted(
            (
                (lot_info['received_at'], lot_key)
                for lot_key, lot_info in lots.items()
                if (
                    lot_info.get('received_at') is not None
                    and lot_info['received_at']
                    <= event['_occurred_at'] + datetime.timedelta(days=1)
                )
            ),
            key=lambda value: (value[0], value[1]),
        )
        remaining_to_deplete = remove_quantity
        for _received_at, lot_key in eligible:
            pair = (base_key, lot_key)
            available = max(0, remaining.get(pair, 0))
            take = min(available, remaining_to_deplete)
            if take <= 0:
                continue
            remaining[pair] = available - take
            depleted_by_pair[pair] = (
                depleted_by_pair.get(pair, 0) + take
            )
            stats['raw_capacity_depleted_units'] += take
            stats['bases_with_raw_capacity_depletion'].add(base_key)
            remaining_to_deplete -= take
            if remaining_to_deplete <= 0:
                break
        stats['unmatched_permanent_removal_units'] += remaining_to_deplete

    stats['bases_with_raw_capacity_depletion'] = len(
        stats['bases_with_raw_capacity_depletion']
    )
    return remaining, depleted_by_pair, stats


def _inventory_age_read_ledger_rows(cur):
    cur.execute('''
        SELECT id, searchrack_id, barcode, received_at, quantity, source,
               source_history_id, estimated, lot_number, evidence_source,
               confidence, confidence_score, evidence_json, rebuild_run_id,
               created_at
        FROM inventory_age_batches
        WHERE quantity > 0
        ORDER BY id
    ''')
    return [dict(row) for row in cur.fetchall()]


def _inventory_age_ledger_signature(rows):
    return [(
        int(row['id']),
        int(row['searchrack_id']),
        str(row.get('barcode') or ''),
        str(row.get('received_at') or ''),
        max(0, ss_normalization._coerce_int(row.get('quantity'), 0)),
        str(row.get('source') or ''),
        row.get('source_history_id'),
        max(0, ss_normalization._coerce_int(row.get('estimated'), 0)),
        str(row.get('lot_number') or ''),
        str(row.get('evidence_source') or ''),
        str(row.get('confidence') or ''),
        max(0, ss_normalization._coerce_int(row.get('confidence_score'), 0)),
        str(row.get('evidence_json') or ''),
        str(row.get('rebuild_run_id') or ''),
        str(row.get('created_at') or ''),
    ) for row in rows]


def _inventory_age_unit_signature(unit):
    evidence_json = unit.get('evidence_json') or '{}'
    if isinstance(evidence_json, (dict, list)):
        evidence_json = json.dumps(
            evidence_json, sort_keys=True, separators=(',', ':')
        )
    return (
        ss_inventory_age._inventory_age_received_at(unit.get('received_at')),
        str(unit.get('source') or ''),
        str(unit.get('lot_number') or ''),
        str(unit.get('evidence_source') or ''),
        str(unit.get('confidence') or 'low'),
        max(0, ss_normalization._coerce_int(unit.get('confidence_score'), 0)),
        1 if unit.get('estimated') else 0,
        str(evidence_json or '{}'),
        unit.get('source_history_id'),
    )


def _inventory_age_group_plan(units, row_id, barcode):
    grouped = {}
    for unit in units:
        key = _inventory_age_unit_signature(unit)
        grouped[key] = grouped.get(key, 0) + 1
    result = []
    for key, quantity in sorted(grouped.items(), key=lambda item: item[0]):
        (
            received_at, source, lot_number, evidence_source, confidence,
            confidence_score, estimated, evidence_json, source_history_id
        ) = key
        result.append({
            'searchrack_id': int(row_id),
            'barcode': str(barcode or '').strip(),
            'received_at': received_at,
            'quantity': quantity,
            'source': source,
            'source_history_id': source_history_id,
            'estimated': bool(estimated),
            'lot_number': lot_number,
            'evidence_source': evidence_source,
            'confidence': confidence,
            'confidence_score': confidence_score,
            'evidence_json': evidence_json,
        })
    return result
