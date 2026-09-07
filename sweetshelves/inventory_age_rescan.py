"""Inventory age rescan for Sweet Shelves."""

import datetime
import hashlib
import json
import sqlite3
import threading
import uuid
from . import (
    inventory_age as ss_inventory_age, inventory_age_evidence as ss_inventory_age_evidence,
    inventory_history as ss_inventory_history, normalization as ss_normalization,
)


_inventory_age_rescan_lock = threading.Lock()


def _rescan_inventory_age_from_history(
    conn, history_path=None, match_window_days=2, evidence_paths=None
):
    """Comprehensively rebuild current ages without mutating warehouse stock."""
    from collections import Counter, defaultdict

    started_at = datetime.datetime.now().isoformat()
    run_id = uuid.uuid4().hex
    ss_inventory_age._ensure_inventory_age_schema(conn)
    reconcile = {
        'backfilled': False,
        'added_units': 0,
        'consumed_units': 0,
        'canonical_rebuild': True,
    }
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute('''
        SELECT ID AS searchrack_id, BARCODE AS barcode,
               COALESCE(CAST(QUANTITY AS INTEGER), 0) AS quantity,
               CREATED_AT AS created_at
        FROM SEARCHRACK
        WHERE COALESCE(CAST(QUANTITY AS INTEGER), 0) > 0
        ORDER BY ID
    ''')
    active_rows = [dict(row) for row in cur.fetchall()]
    active_keys = {
        ss_inventory_age_evidence._inventory_age_exact_key(row.get('barcode'))
        for row in active_rows
        if ss_inventory_age_evidence._inventory_age_exact_key(row.get('barcode'))
    }
    active_base_keys = {
        ss_inventory_age_evidence._inventory_age_base_key(row.get('barcode'))
        for row in active_rows
        if ss_inventory_age_evidence._inventory_age_base_key(row.get('barcode'))
    }
    expected_inventory = [(
        int(row['searchrack_id']),
        str(row.get('barcode') or '').strip(),
        max(0, ss_normalization._coerce_int(row.get('quantity'), 0)),
    ) for row in active_rows]
    active_row_ids = {
        row_id for row_id, _barcode, _quantity in expected_inventory
    }

    existing_ledger_rows = ss_inventory_age_evidence._inventory_age_read_ledger_rows(cur)
    existing_ledger_signature = ss_inventory_age_evidence._inventory_age_ledger_signature(
        existing_ledger_rows
    )
    stale_ledger_row_ids = sorted({
        int(row['searchrack_id'])
        for row in existing_ledger_rows
        if int(row['searchrack_id']) not in active_row_ids
    })

    evidence = ss_inventory_age_evidence._inventory_age_load_receipt_evidence(evidence_paths)
    if not evidence.get('complete'):
        raise RuntimeError(
            'Comprehensive age evidence is unavailable: '
            + '; '.join(evidence.get('fatal_errors') or ['unknown source error'])
        )
    history = ss_inventory_age_evidence._inventory_age_load_history(
        active_keys, history_path, active_base_keys
    )
    if not history.get('available'):
        raise RuntimeError(
            'Comprehensive age evidence is unavailable: '
            + str(history.get('error') or 'rack history is unavailable')
        )
    row_lineages, replay_stats = ss_inventory_age_evidence._inventory_age_replay_history(
        history['events'], evidence, match_window_days
    )

    all_units = []
    fallback_units = 0
    now = datetime.datetime.now()
    for active in active_rows:
        row_id = int(active['searchrack_id'])
        barcode = str(active.get('barcode') or '').strip()
        exact_key = ss_inventory_age_evidence._inventory_age_exact_key(barcode)
        base_key = ss_inventory_age_evidence._inventory_age_base_key(barcode)
        target_quantity = max(0, ss_normalization._coerce_int(active.get('quantity'), 0))
        created_at = ss_inventory_history._history_timestamp(active.get('created_at')) or now

        history_units = list(
            row_lineages.get((row_id, exact_key), [])
        )
        history_units.sort(key=lambda unit: unit['received_at'])
        if len(history_units) > target_quantity:
            history_units = history_units[:target_quantity]

        # The plan is canonical and independent of the prior derived ledger.
        # Existing batches are used only for change detection/audit, never as
        # receipt evidence. That makes a second rebuild exactly idempotent.
        while len(history_units) < target_quantity:
            history_units.append({
                'received_at': created_at,
                'first_seen': created_at,
                'lineage_source': 'warehouse_created_at',
                'lineage_confidence': 35,
                'history_ids': [],
            })
            fallback_units += 1
        history_units.sort(key=lambda unit: unit['received_at'])

        for index in range(target_quantity):
            lineage = history_units[index]
            all_units.append({
                'searchrack_id': row_id,
                'barcode': barcode,
                'exact_key': exact_key,
                'base_key': base_key,
                'unit_index': index,
                'received_at': lineage['received_at'],
                'first_seen': (
                    lineage.get('first_seen')
                    or created_at
                ),
                'lineage_source': str(
                    lineage.get('lineage_source')
                    or 'warehouse_created_at'
                ),
                'lineage_confidence': max(
                    0,
                    ss_normalization._coerce_int(
                        lineage.get('lineage_confidence'), 35
                    ),
                ),
                'history_ids': list(lineage.get('history_ids') or []),
                'reorg_match_type': str(
                    lineage.get('reorg_match_type') or ''
                ),
                'reorg_matches': list(
                    lineage.get('reorg_matches') or []
                ),
                'assigned': False,
            })

    (
        remaining_capacity,
        depleted_capacity_by_pair,
        depletion_stats,
    ) = ss_inventory_age_evidence._inventory_age_depleted_raw_capacity(
        history['events'], evidence
    )

    assignment_counts = Counter()
    assigned_by_exact_lot = Counter()

    def lot_is_temporally_eligible(unit, lot_info):
        first_seen = unit.get('first_seen')
        received_at = lot_info.get('received_at')
        if first_seen is None or received_at is None:
            return False
        return received_at <= first_seen + datetime.timedelta(days=1)

    def assign_receipt(unit, lot_key, evidence_source, score, detail):
        base_key = unit['base_key']
        lot_info = evidence['catalog'].get(base_key, {}).get(lot_key)
        capacity_key = (base_key, lot_key)
        if (
            unit.get('assigned')
            or not lot_info
            or remaining_capacity.get(capacity_key, 0) <= 0
            or not lot_is_temporally_eligible(unit, lot_info)
        ):
            return False
        adjusted_score = max(0, min(100, ss_normalization._coerce_int(score, 0)))
        if not lot_info.get('upload_confirmed'):
            adjusted_score -= 3
        if not lot_info.get('bol_confirmed'):
            adjusted_score -= 3
        adjusted_score = max(0, adjusted_score)
        confidence = ss_inventory_age_evidence._inventory_age_confidence_label(adjusted_score)
        evidence_detail = {
            'reason': evidence_source,
            'raw_bol_ids': lot_info.get('raw_ids') or [],
            'raw_bol_quantity': max(
                0, ss_normalization._coerce_int(lot_info.get('quantity'), 0)
            ),
            'known_permanent_depletion': max(
                0,
                depleted_capacity_by_pair.get(
                    (base_key, lot_key), 0
                ),
            ),
            'date_source': lot_info.get('date_source'),
            'upload_log_confirmed': bool(
                lot_info.get('upload_confirmed')
            ),
            'bol_item_confirmed': bool(
                lot_info.get('bol_confirmed')
            ),
            'warehouse_lineage': {
                'lineage_source': unit.get('lineage_source'),
                'history_ids': unit.get('history_ids') or [],
                'reorg_match_type': unit.get('reorg_match_type') or '',
                'reorg_matches': unit.get('reorg_matches') or [],
            },
            **dict(detail or {}),
        }
        unit.update({
            'received_at': lot_info['received_at'],
            'source': 'comprehensive_age_rebuild',
            'source_history_id': None,
            'estimated': adjusted_score < 90,
            'lot_number': lot_info.get('lot_number') or lot_key,
            'evidence_source': evidence_source,
            'confidence': confidence,
            'confidence_score': adjusted_score,
            'evidence_json': json.dumps(
                evidence_detail,
                sort_keys=True,
                separators=(',', ':'),
            ),
            'assigned': True,
        })
        remaining_capacity[capacity_key] -= 1
        assignment_counts[evidence_source] += 1
        assigned_by_exact_lot[(unit['exact_key'], lot_key)] += 1
        return True

    units_by_exact = defaultdict(list)
    for unit in all_units:
        units_by_exact[unit['exact_key']].append(unit)
    for units in units_by_exact.values():
        units.sort(key=lambda unit: (
            unit['first_seen'],
            unit['searchrack_id'],
            unit['unit_index'],
        ))

    # First priority: non-undone GOOD prep events with an exact UPC and an
    # explicit lot. Each logged quantity is consumed once.
    for exact_key, units in sorted(units_by_exact.items()):
        expanded_tokens = []
        for token in evidence['prep_by_exact'].get(exact_key, []):
            for token_index in range(
                max(0, ss_normalization._coerce_int(token.get('quantity'), 0))
            ):
                expanded_tokens.append({
                    **token,
                    'token_index': token_index,
                })
        for unit in units:
            candidates = []
            for token in expanded_tokens:
                lot_info = evidence['catalog'].get(
                    unit['base_key'], {}
                ).get(token['lot_key'])
                if not lot_info:
                    continue
                if not lot_is_temporally_eligible(unit, lot_info):
                    continue
                if (
                    token['created_at']
                    > unit['first_seen'] + datetime.timedelta(days=2)
                ):
                    continue
                if (
                    lot_info['received_at']
                    > token['created_at'] + datetime.timedelta(days=1)
                ):
                    continue
                if remaining_capacity.get(
                    (unit['base_key'], token['lot_key']), 0
                ) <= 0:
                    continue
                distance = abs(
                    (
                        unit['first_seen'] - token['created_at']
                    ).total_seconds()
                )
                candidates.append((distance, token['created_at'], token))
            if not candidates:
                continue
            candidates.sort(key=lambda value: (
                value[0], value[1], value[2]['id'],
                value[2]['token_index'],
            ))
            token = candidates[0][2]
            score = 98
            if (
                token.get('exception_overage')
                or token.get('needs_review')
                or token.get('continued_without_lot')
            ):
                score = 88
            elif (
                token.get('auto_assigned')
                or token.get('forced_lot_override')
            ):
                score = 96
            if assign_receipt(
                unit,
                token['lot_key'],
                'prep_log_exact_lot',
                score,
                {
                    'prep_log_id': token['id'],
                    'prep_event_at': token['created_at'].isoformat(),
                    'auto_assigned': bool(token.get('auto_assigned')),
                    'forced_lot_override': bool(
                        token.get('forced_lot_override')
                    ),
                    'exception_overage': bool(
                        token.get('exception_overage')
                    ),
                    'needs_review': bool(token.get('needs_review')),
                },
            ):
                expanded_tokens.remove(token)

    # Item Manager's current GOOD status can fill only its remaining exact
    # UPC+lot quantity. Its updated_at is deliberately not treated as receipt.
    status_remaining = {}
    for key, status in evidence['status_by_exact_lot'].items():
        status_remaining[key] = max(
            0,
            ss_normalization._coerce_int(status.get('quantity'), 0)
            - assigned_by_exact_lot.get(key, 0),
        )
    for exact_key, units in sorted(units_by_exact.items()):
        for unit in units:
            if unit.get('assigned'):
                continue
            eligible_lots = []
            for (status_exact, lot_key), remaining in status_remaining.items():
                if status_exact != exact_key or remaining <= 0:
                    continue
                lot_info = evidence['catalog'].get(
                    unit['base_key'], {}
                ).get(lot_key)
                if (
                    lot_info
                    and lot_is_temporally_eligible(unit, lot_info)
                    and remaining_capacity.get(
                        (unit['base_key'], lot_key), 0
                    ) > 0
                ):
                    eligible_lots.append(lot_key)
            eligible_lots = sorted(set(eligible_lots))
            if len(eligible_lots) != 1:
                continue
            lot_key = eligible_lots[0]
            status = evidence['status_by_exact_lot'][
                (exact_key, lot_key)
            ]
            if assign_receipt(
                unit,
                lot_key,
                'item_manager_exact_lot',
                95,
                {'item_prep_status_ids': status.get('ids') or []},
            ):
                status_remaining[(exact_key, lot_key)] -= 1

    # A suffixed Item Manager/BOL row is a distinct unit identity. A base BOL
    # row is intentionally not used as a hard pin because it can blend lots.
    for exact_key, units in sorted(units_by_exact.items()):
        if '-' not in exact_key:
            continue
        for unit in units:
            if unit.get('assigned'):
                continue
            lots = [
                lot_key
                for lot_key in evidence['bol_suffix_lots'].get(
                    exact_key, set()
                )
                if (
                    lot_key in evidence['catalog'].get(
                        unit['base_key'], {}
                    )
                    and lot_is_temporally_eligible(
                        unit,
                        evidence['catalog'][unit['base_key']][lot_key],
                    )
                    and remaining_capacity.get(
                        (unit['base_key'], lot_key), 0
                    ) > 0
                )
            ]
            if len(set(lots)) == 1:
                assign_receipt(
                    unit,
                    lots[0],
                    'item_manager_suffix_lot',
                    92,
                    {'exact_suffix_identity': True},
                )

    # When Raw BOL contains only one receipt lot for the base UPC, there is no
    # cross-lot choice to guess. Capacity still applies globally.
    units_by_base = defaultdict(list)
    for unit in all_units:
        units_by_base[unit['base_key']].append(unit)
    for base_key, units in sorted(units_by_base.items()):
        catalog_lots = evidence['catalog'].get(base_key, {})
        if len(catalog_lots) != 1:
            continue
        lot_key = next(iter(catalog_lots))
        for unit in sorted(units, key=lambda item: (
            item['first_seen'], item['searchrack_id'], item['unit_index']
        )):
            if unit.get('assigned'):
                continue
            assign_receipt(
                unit,
                lot_key,
                'raw_bol_unique_lot',
                96,
                {'candidate_lot_count': 1},
            )

    # For repeated UPCs, a trustworthy first warehouse sighting may eliminate
    # all later lots. More than one eligible lot stays ambiguous.
    for base_key, units in sorted(units_by_base.items()):
        catalog_lots = evidence['catalog'].get(base_key, {})
        if len(catalog_lots) <= 1:
            continue
        for unit in sorted(units, key=lambda item: (
            item['first_seen'], item['searchrack_id'], item['unit_index']
        )):
            if unit.get('assigned'):
                continue
            eligible_lots = [
                lot_key
                for lot_key, lot_info in catalog_lots.items()
                if (
                    lot_is_temporally_eligible(unit, lot_info)
                    and remaining_capacity.get((base_key, lot_key), 0) > 0
                )
            ]
            if len(eligible_lots) == 1:
                assign_receipt(
                    unit,
                    eligible_lots[0],
                    'raw_bol_temporal_single_lot',
                    86,
                    {
                        'candidate_lot_count': len(catalog_lots),
                        'eligible_lot_count': 1,
                        'first_seen': unit['first_seen'].isoformat(),
                    },
                )

    # Any unresolved unit retains its best reconstructed lineage date and is
    # explicitly estimated; no arbitrary oldest/newest lot is selected.
    for unit in all_units:
        if unit.get('assigned'):
            continue
        lineage_score = max(
            25, min(65, ss_normalization._coerce_int(unit.get('lineage_confidence'), 35))
        )
        evidence_source = (
            'rack_history_lineage'
            if unit.get('history_ids')
            else 'warehouse_age_fallback'
        )
        unit.update({
            'source': 'comprehensive_age_rebuild',
            'source_history_id': None,
            'estimated': True,
            'lot_number': '',
            'evidence_source': evidence_source,
            'confidence': ss_inventory_age_evidence._inventory_age_confidence_label(lineage_score),
            'confidence_score': lineage_score,
            'evidence_json': json.dumps({
                'reason': evidence_source,
                'lineage_source': unit.get('lineage_source'),
                'history_ids': unit.get('history_ids') or [],
                'reorg_match_type': unit.get('reorg_match_type') or '',
                'reorg_matches': unit.get('reorg_matches') or [],
                'raw_candidate_lots': len(
                    evidence['catalog'].get(unit['base_key'], {})
                ),
            }, sort_keys=True, separators=(',', ':')),
        })

    proposed_units_by_row = defaultdict(list)
    for unit in all_units:
        proposed_units_by_row[int(unit['searchrack_id'])].append(unit)
    for units in proposed_units_by_row.values():
        units.sort(key=ss_inventory_age_evidence._inventory_age_unit_signature)

    active_barcode_by_id = {
        int(row['searchrack_id']): str(row.get('barcode') or '').strip()
        for row in active_rows
    }
    proposed_batches_by_row = {
        row_id: ss_inventory_age_evidence._inventory_age_group_plan(
            units,
            row_id,
            active_barcode_by_id.get(row_id, ''),
        )
        for row_id, units in proposed_units_by_row.items()
    }

    current_units_for_compare = defaultdict(list)
    for batch in existing_ledger_rows:
        for _ in range(max(0, ss_normalization._coerce_int(batch.get('quantity'), 0))):
            current_units_for_compare[int(batch['searchrack_id'])].append({
                'received_at': batch.get('received_at'),
                'source': batch.get('source'),
                'source_history_id': batch.get('source_history_id'),
                'estimated': bool(batch.get('estimated')),
                'lot_number': batch.get('lot_number'),
                'evidence_source': batch.get('evidence_source'),
                'confidence': batch.get('confidence'),
                'confidence_score': batch.get('confidence_score'),
                'evidence_json': batch.get('evidence_json'),
            })
    for units in current_units_for_compare.values():
        units.sort(key=ss_inventory_age_evidence._inventory_age_unit_signature)

    rows_changed = 0
    rows_rebuilt = 0
    units_changed = 0
    units_moved_older = 0
    units_moved_newer = 0
    rows_to_rebuild = []
    for active in active_rows:
        row_id = int(active['searchrack_id'])
        old_units = current_units_for_compare.get(row_id, [])
        new_units = proposed_units_by_row.get(row_id, [])
        old_signatures = [
            ss_inventory_age_evidence._inventory_age_unit_signature(unit) for unit in old_units
        ]
        new_signatures = [
            ss_inventory_age_evidence._inventory_age_unit_signature(unit) for unit in new_units
        ]
        if old_signatures != new_signatures:
            rows_rebuilt += 1
            rows_to_rebuild.append(row_id)
        old_dates = sorted(
            ss_inventory_history._history_timestamp(unit.get('received_at')) or now
            for unit in old_units
        )
        new_dates = sorted(
            ss_inventory_history._history_timestamp(unit.get('received_at')) or now
            for unit in new_units
        )
        changed_here = 0
        for old_at, new_at in zip(old_dates, new_dates):
            difference = (new_at - old_at).total_seconds()
            if abs(difference) <= 60:
                continue
            changed_here += 1
            if difference < 0:
                units_moved_older += 1
            else:
                units_moved_newer += 1
        if changed_here:
            rows_changed += 1
            units_changed += changed_here

    ledger_state_changed = bool(
        rows_to_rebuild or stale_ledger_row_ids
    )
    inventory_units = len(all_units)
    current_reorg_units = sum(
        1 for unit in all_units if unit.get('reorg_matches')
    )
    current_short_gap_reorg_units = sum(
        1
        for unit in all_units
        if any(
            match.get('match_type') == 'short_gap'
            for match in unit.get('reorg_matches') or []
        )
    )
    current_historical_reorg_units = sum(
        1
        for unit in all_units
        if any(
            match.get('match_type')
            == 'known_historical_office_reinventory'
            for match in unit.get('reorg_matches') or []
        )
    )
    raw_coverage_units = sum(
        1 for unit in all_units
        if unit['base_key'] in evidence['catalog']
    )
    verified_units = sum(
        1 for unit in all_units
        if ss_normalization._coerce_int(unit.get('confidence_score'), 0) >= 90
    )
    medium_confidence_units = sum(
        1 for unit in all_units
        if 70 <= ss_normalization._coerce_int(unit.get('confidence_score'), 0) < 90
    )
    low_confidence_units = (
        inventory_units - verified_units - medium_confidence_units
    )
    history_only_units = sum(
        1 for unit in all_units
        if unit.get('evidence_source') in {
            'rack_history_lineage', 'warehouse_age_fallback'
        }
    )
    ambiguous_units = sum(
        1 for unit in all_units
        if (
            not unit.get('assigned')
            and len(evidence['catalog'].get(unit['base_key'], {})) > 1
        )
    )
    no_bol_units = sum(
        1 for unit in all_units
        if not evidence['catalog'].get(unit['base_key'])
    )
    estimated_units = sum(
        1 for unit in all_units if bool(unit.get('estimated'))
    )
    raw_capacity_shortfall_units = 0
    for base_key, units in units_by_base.items():
        available_capacity = sum(
            max(
                0,
                ss_normalization._coerce_int(info.get('quantity'), 0)
                - depleted_capacity_by_pair.get(
                    (base_key, lot_key), 0
                ),
            )
            for lot_key, info in evidence['catalog'].get(
                base_key, {}
            ).items()
        )
        if (
            evidence['catalog'].get(base_key)
            and len(units) > available_capacity
        ):
            raw_capacity_shortfall_units += (
                len(units) - available_capacity
            )

    before_ledger_json = (
        json.dumps(
            existing_ledger_rows,
            ensure_ascii=False,
            default=str,
            separators=(',', ':'),
        )
        if ledger_state_changed
        else '[]'
    )
    planned_batches = [
        batch
        for row_id in sorted(proposed_batches_by_row)
        for batch in proposed_batches_by_row[row_id]
    ]
    after_plan_json = json.dumps(
        planned_batches,
        ensure_ascii=False,
        default=str,
        sort_keys=True,
        separators=(',', ':'),
    )
    plan_digest = hashlib.sha256(
        after_plan_json.encode('utf-8')
    ).hexdigest()
    input_digest = hashlib.sha256(json.dumps({
        'inventory': expected_inventory,
        'ledger': existing_ledger_signature,
        'history': history['fingerprint'],
        'evidence': evidence['fingerprint'],
    }, default=str, sort_keys=True, separators=(',', ':')).encode(
        'utf-8'
    )).hexdigest()

    # Re-read every external source immediately before the write. The final
    # SEARCHRACK transaction independently rechecks inventory and ledger state.
    evidence_recheck = ss_inventory_age_evidence._inventory_age_load_receipt_evidence(
        evidence['paths']
    )
    if (
        not evidence_recheck.get('complete')
        or evidence_recheck['fingerprint'] != evidence['fingerprint']
    ):
        raise RuntimeError(
            'BOL or Item Manager data changed during the age rebuild; please retry'
        )
    history_recheck = ss_inventory_age_evidence._inventory_age_load_history(
        active_keys, history['path'], active_base_keys
    )
    if (
        not history_recheck.get('available')
        or history_recheck['fingerprint'] != history['fingerprint']
    ):
        raise RuntimeError(
            'Rack history changed during the age rebuild; please retry'
        )

    completed_at = datetime.datetime.now().isoformat()
    summary = {
        'run_id': run_id,
        'started_at': started_at,
        'completed_at': completed_at,
        'mode': 'comprehensive',
        'match_window_days': max(1, int(match_window_days or 2)),
        'inventory_rows': len(active_rows),
        'inventory_units': inventory_units,
        'raw_bol_coverage_units': raw_coverage_units,
        'high_confidence_units': verified_units,
        'strong_evidence_percent': (
            round((verified_units / inventory_units) * 100, 1)
            if inventory_units else 100.0
        ),
        # Kept for older clients; this is evidence coverage, not a measured
        # accuracy rate.
        'verified_units': verified_units,
        'verified_percent': (
            round((verified_units / inventory_units) * 100, 1)
            if inventory_units else 100.0
        ),
        'medium_confidence_units': medium_confidence_units,
        'low_confidence_units': low_confidence_units,
        'estimated_units': estimated_units,
        'history_only_units': history_only_units,
        'ambiguous_units': ambiguous_units,
        'no_bol_units': no_bol_units,
        'raw_capacity_shortfall_units': raw_capacity_shortfall_units,
        'known_permanent_removal_units': depletion_stats[
            'known_permanent_removal_units'
        ],
        'raw_capacity_depleted_units': depletion_stats[
            'raw_capacity_depleted_units'
        ],
        'unmatched_permanent_removal_units': depletion_stats[
            'unmatched_permanent_removal_units'
        ],
        'bases_with_raw_capacity_depletion': depletion_stats[
            'bases_with_raw_capacity_depletion'
        ],
        'history_events_scanned': len(history['events']),
        'history_events_used': replay_stats['history_events_used'],
        'movement_events_suppressed': history[
            'movement_events_suppressed'
        ],
        'matched_reorg_units': current_reorg_units,
        'matched_short_gap_reorg_units': current_short_gap_reorg_units,
        'matched_historical_exception_units': (
            current_historical_reorg_units
        ),
        'history_reorg_operations': replay_stats[
            'matched_reorg_units'
        ],
        'reorg_units_blocked_by_new_receipt': replay_stats[
            'reorg_units_blocked_by_new_receipt'
        ],
        'reorg_units_blocked_by_confirmed_receipt': replay_stats[
            'reorg_units_blocked_by_confirmed_receipt'
        ],
        'reorg_units_blocked_by_possible_receipt': replay_stats[
            'reorg_units_blocked_by_possible_receipt'
        ],
        'pending_unmatched_clear_units': replay_stats[
            'pending_unmatched_clear_units'
        ],
        'inferred_history_units': replay_stats[
            'inferred_history_units'
        ],
        'fallback_units': fallback_units,
        'rows_rebuilt': rows_rebuilt,
        'stale_age_rows_removed': len(stale_ledger_row_ids),
        'rows_changed': rows_changed,
        'units_changed': units_changed,
        'units_moved_older': units_moved_older,
        'units_moved_newer': units_moved_newer,
        'assignment_counts': dict(sorted(assignment_counts.items())),
        'input_digest': input_digest,
        'plan_digest': plan_digest,
        'warnings': evidence['warnings'],
    }

    cur.execute('BEGIN IMMEDIATE')
    try:
        cur.execute('''
            SELECT ID, BARCODE, COALESCE(CAST(QUANTITY AS INTEGER), 0)
            FROM SEARCHRACK
            WHERE COALESCE(CAST(QUANTITY AS INTEGER), 0) > 0
            ORDER BY ID
        ''')
        current_inventory = [(
            int(row[0]),
            str(row[1] or '').strip(),
            max(0, ss_normalization._coerce_int(row[2], 0)),
        ) for row in cur.fetchall()]
        if current_inventory != expected_inventory:
            raise RuntimeError(
                'Warehouse inventory changed during the age rebuild; please retry'
            )
        current_ledger_rows = ss_inventory_age_evidence._inventory_age_read_ledger_rows(cur)
        if (
            ss_inventory_age_evidence._inventory_age_ledger_signature(current_ledger_rows)
            != existing_ledger_signature
        ):
            raise RuntimeError(
                'Inventory ages changed during the age rebuild; please retry'
            )

        if stale_ledger_row_ids:
            placeholders = ','.join('?' for _ in stale_ledger_row_ids)
            cur.execute(
                f'DELETE FROM inventory_age_batches '
                f'WHERE searchrack_id IN ({placeholders})',
                tuple(stale_ledger_row_ids),
            )
        for row_id in rows_to_rebuild:
            cur.execute(
                'DELETE FROM inventory_age_batches WHERE searchrack_id = ?',
                (row_id,),
            )
            for batch in proposed_batches_by_row.get(row_id, []):
                ss_inventory_age._inventory_age_insert_batch(
                    cur,
                    row_id,
                    batch['barcode'],
                    batch['received_at'],
                    batch['quantity'],
                    source=batch['source'],
                    source_history_id=batch['source_history_id'],
                    estimated=batch['estimated'],
                    lot_number=batch['lot_number'],
                    evidence_source=batch['evidence_source'],
                    confidence=batch['confidence'],
                    confidence_score=batch['confidence_score'],
                    evidence_json=batch['evidence_json'],
                    rebuild_run_id=run_id,
                )

        cur.execute('''
            SELECT searchrack_id, COALESCE(SUM(quantity), 0)
            FROM inventory_age_batches
            WHERE quantity > 0
            GROUP BY searchrack_id
        ''')
        ledger_quantities = {
            int(row[0]): max(0, ss_normalization._coerce_int(row[1], 0))
            for row in cur.fetchall()
        }
        expected_quantities = {
            row_id: quantity
            for row_id, _barcode, quantity in expected_inventory
        }
        if ledger_quantities != expected_quantities:
            raise RuntimeError(
                'Age rebuild quantity invariant failed; no changes were saved'
            )

        final_ledger_rows = ss_inventory_age_evidence._inventory_age_read_ledger_rows(cur)
        final_ledger_json = (
            json.dumps(
                final_ledger_rows,
                ensure_ascii=False,
                default=str,
                separators=(',', ':'),
            )
            if ledger_state_changed
            else '[]'
        )
        cur.execute('''
            INSERT INTO inventory_age_rebuild_runs (
                run_id, started_at, completed_at, input_digest,
                plan_digest, mode, status, summary_json,
                before_ledger_json, after_ledger_json
            ) VALUES (?, ?, ?, ?, ?, 'comprehensive', 'completed', ?, ?, ?)
        ''', (
            run_id,
            started_at,
            completed_at,
            input_digest,
            plan_digest,
            json.dumps(summary, sort_keys=True, separators=(',', ':')),
            before_ledger_json,
            final_ledger_json,
        ))
        # Retain complete rollback evidence for only the five newest
        # state-changing rebuilds. Older/no-op runs keep their digest/summary.
        cur.execute('''
            UPDATE inventory_age_rebuild_runs
            SET before_ledger_json = '[]',
                after_ledger_json = '[]'
            WHERE run_id NOT IN (
                SELECT run_id
                FROM inventory_age_rebuild_runs
                WHERE before_ledger_json != '[]'
                   OR after_ledger_json != '[]'
                ORDER BY completed_at DESC, started_at DESC
                LIMIT 5
            )
        ''')
        summary_json = json.dumps(
            summary, sort_keys=True, separators=(',', ':')
        )
        cur.execute('''
            INSERT OR REPLACE INTO inventory_age_meta(key, value)
            VALUES ('last_comprehensive_rebuild', ?)
        ''', (summary_json,))
        cur.execute('''
            INSERT OR IGNORE INTO inventory_age_meta(key, value)
            VALUES ('backfill_v1', ?)
        ''', (completed_at,))
        cur.execute('''
            INSERT OR REPLACE INTO inventory_age_meta(key, value)
            VALUES ('last_history_rescan', ?)
        ''', (summary_json,))
        conn.commit()
    except Exception:
        conn.rollback()
        raise

    return {
        **summary,
        'reconcile': reconcile,
        'quantity_invariant': True,
    }
