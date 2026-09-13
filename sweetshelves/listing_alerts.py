"""Listing alerts for Sweet Shelves."""

import datetime
import json
import os
import sqlite3
import threading
from DBmanager import connect_db
from flask import jsonify, request
from . import (
    caching as ss_caching, config as ss_config, errors as ss_errors, listing_lifecycle as
    ss_listing_lifecycle, marketplace_removal as ss_marketplace_removal, normalization as
    ss_normalization, sync as ss_sync, warehouse_matching as ss_warehouse_matching,
)


def _ensure_listing_alerts_tables():
    """Create listing_alerts.db tables if they don't exist."""
    try:
        conn = sqlite3.connect('listing_alerts.db')
        cur = conn.cursor()
        cur.execute('''
            CREATE TABLE IF NOT EXISTS dismissed_alerts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                alert_type TEXT NOT NULL,
                upc TEXT NOT NULL,
                store TEXT,
                listing_ids TEXT,
                dismissed_at TEXT DEFAULT CURRENT_TIMESTAMP,
                snapshot_hash TEXT NOT NULL,
                UNIQUE(alert_type, snapshot_hash)
            )
        ''')
        cur.execute('CREATE INDEX IF NOT EXISTS idx_dismissed_hash ON dismissed_alerts(alert_type, snapshot_hash)')
        cur.execute('''
            CREATE TABLE IF NOT EXISTS inventory_zero_tracker (
                upc TEXT PRIMARY KEY,
                ever_in_inventory INTEGER DEFAULT 0,
                last_qty INTEGER DEFAULT 0,
                last_nonzero_at TEXT,
                zero_triggered_at TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        cur.execute('CREATE INDEX IF NOT EXISTS idx_zero_tracker_zero ON inventory_zero_tracker(zero_triggered_at)')
        cur.execute('''
            CREATE TABLE IF NOT EXISTS ebay_listing_health_cache (
                item_id TEXT PRIMARY KEY,
                is_active INTEGER NOT NULL DEFAULT 1,
                checked_at TEXT DEFAULT CURRENT_TIMESTAMP,
                source TEXT,
                note TEXT
            )
        ''')
        cur.execute('CREATE INDEX IF NOT EXISTS idx_ebay_listing_health_checked_at ON ebay_listing_health_cache(checked_at)')
        cur.execute('''
            CREATE TABLE IF NOT EXISTS listing_inventory_matches (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                store TEXT NOT NULL,
                listing_key TEXT NOT NULL COLLATE NOCASE,
                listing_id TEXT,
                marketplace_barcode TEXT,
                listing_title TEXT,
                searchrack_id INTEGER NOT NULL,
                inventory_barcode TEXT NOT NULL,
                inventory_location TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(store, listing_key)
            )
        ''')
        cur.execute('CREATE INDEX IF NOT EXISTS idx_listing_inventory_match_rack ON listing_inventory_matches(searchrack_id)')
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"Error initializing listing_alerts.db: {e}")


def _listing_alert_upc_key(raw_upc):
    """Normalize UPC/Barcode for cross-db matching in listing alert logic."""
    upc = (str(raw_upc or '').strip())
    if not upc:
        return ''
    normalized = ss_normalization._strip_leading_zeros_numeric(upc)
    return (normalized or upc).lower()


def _normalize_store_listing_identity(store, listing_key):
    normalized_store = str(store or '').strip().lower()
    normalized_key = str(listing_key or '').strip()
    if normalized_store not in {'ebay', 'amazon', 'facebook'} or not normalized_key:
        return '', ''
    prefix = f'{normalized_store}:'
    if normalized_key.lower().startswith(prefix):
        normalized_key = normalized_key[len(prefix):].strip()
    return normalized_store, normalized_key


def _store_listing_order_identity_candidates(order):
    store = str(ss_warehouse_matching._sold_order_value(order, 'store', '') or '').strip().lower()
    values = []
    if store == 'ebay':
        values = [
            ss_warehouse_matching._sold_order_value(order, 'listing_listing_id', ''),
            ss_warehouse_matching._sold_order_value(order, 'item_id', ''),
            ss_warehouse_matching._sold_order_value(order, 'listing_sku', ''),
            ss_warehouse_matching._sold_order_value(order, 'sku', '')
        ]
    elif store == 'amazon':
        values = [
            ss_warehouse_matching._sold_order_value(order, 'listing_sku', ''),
            ss_warehouse_matching._sold_order_value(order, 'sku', ''),
            ss_warehouse_matching._sold_order_value(order, 'listing_asin', ''),
            ss_warehouse_matching._sold_order_value(order, 'item_id', '')
        ]

    out = []
    for value in values:
        _, key = _normalize_store_listing_identity(store, value)
        key_lower = key.lower()
        if key and key_lower not in out:
            out.append(key_lower)
    return store, out


def _load_listing_inventory_match_lookup():
    _ensure_listing_alerts_tables()
    lookup = {}
    try:
        with sqlite3.connect(str(ss_config.BASE_DIR / 'listing_alerts.db')) as conn:
            conn.row_factory = sqlite3.Row
            for row in conn.execute('SELECT * FROM listing_inventory_matches'):
                store, key = _normalize_store_listing_identity(row['store'], row['listing_key'])
                if store and key:
                    lookup[(store, key.lower())] = dict(row)
    except Exception as e:
        print(f'Warning: Could not load store-listing inventory matches: {e}')
    return lookup


def _listing_inventory_match_for_order(order, lookup=None):
    mapping_lookup = lookup if lookup is not None else _load_listing_inventory_match_lookup()
    store, candidates = _store_listing_order_identity_candidates(order)
    for candidate in candidates:
        match = mapping_lookup.get((store, candidate))
        if match:
            return match
    return None


def _listing_alert_base_upc_key(raw_upc):
    """Normalize base UPC (strip -suffix + leading zeros) for broader matching."""
    upc = (str(raw_upc or '').strip())
    if not upc:
        return ''
    base = upc.split('-', 1)[0].strip()
    if not base:
        return ''
    normalized = ss_normalization._strip_leading_zeros_numeric(base)
    return (normalized or base).lower()


_listing_helper_scan_cache_lock = threading.Lock()


_listing_helper_scan_cache_state = {
    'ts': 0.0,
    'payload': None
}



def api_listing_helper_scan():
    """Serve global alerts from the same Listings & Stock calculation."""
    from . import listing_reconciliation
    try:
        return jsonify(listing_reconciliation.listing_alert_summary(ss_sync._sync_manager_overdue_alert()))
    except Exception as e:
        return jsonify({'success':False,'error':ss_errors._safe_error(e,'listing-stock-alerts')}),500


def api_listing_helper_inventory_match():
    """Persist or clear a store-listing fallback to one exact warehouse row."""
    data = request.get_json() or {}
    store, listing_key = _normalize_store_listing_identity(
        data.get('store'), data.get('listing_key') or data.get('listing_id')
    )
    if not store or not listing_key:
        return jsonify({'success': False, 'error': 'Valid store and listing key are required'}), 400

    _ensure_listing_alerts_tables()
    clear_match = bool(data.get('clear'))
    from finder_aliases import update_alias
    alias_source = f'{store}:{listing_key.lower()}'
    try:
        with connect_db('listing_alerts.db') as match_conn:
            match_conn.row_factory = sqlite3.Row
            match_cur = match_conn.cursor()
            previous = match_cur.execute(
                'SELECT * FROM listing_inventory_matches WHERE store = ? AND LOWER(listing_key) = LOWER(?)',
                (store, listing_key)
            ).fetchone()

            if clear_match:
                match_cur.execute(
                    'DELETE FROM listing_inventory_matches WHERE store = ? AND LOWER(listing_key) = LOWER(?)',
                    (store, listing_key)
                )
                alias_undo = update_alias(match_conn, alias_source, undo_token=data.get('finder_alias_undo'))
                match_conn.commit()
                ss_caching._listing_helper_scan_cache_clear()
                return jsonify({
                    'success': True,
                    'cleared': True,
                    'finder_alias_undo': alias_undo,
                    'old_match': dict(previous) if previous else None
                })

            searchrack_id = ss_normalization._coerce_int(data.get('searchrack_id'), 0)
            if searchrack_id <= 0:
                return jsonify({'success': False, 'error': 'A warehouse item is required'}), 400

            with connect_db('searchRack.db') as rack_conn:
                rack_conn.row_factory = sqlite3.Row
                rack_match = ss_warehouse_matching._ready_to_ship_searchrack_match_by_id(rack_conn.cursor(), searchrack_id)
            if not rack_match or ss_normalization._coerce_int(rack_match.get('quantity'), 0) <= 0:
                return jsonify({'success': False, 'error': 'The selected warehouse item is unavailable'}), 409

            inventory_barcode = str(rack_match.get('barcode') or '').strip()
            inventory_location = ss_warehouse_matching._ready_to_ship_match_display_location(rack_match)
            match_cur.execute('''
                INSERT INTO listing_inventory_matches (
                    store, listing_key, listing_id, marketplace_barcode,
                    listing_title, searchrack_id, inventory_barcode,
                    inventory_location, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(store, listing_key) DO UPDATE SET
                    listing_id = excluded.listing_id,
                    marketplace_barcode = excluded.marketplace_barcode,
                    listing_title = excluded.listing_title,
                    searchrack_id = excluded.searchrack_id,
                    inventory_barcode = excluded.inventory_barcode,
                    inventory_location = excluded.inventory_location,
                    updated_at = CURRENT_TIMESTAMP
            ''', (
                store,
                listing_key,
                str(data.get('listing_id') or '').strip(),
                str(data.get('marketplace_barcode') or '').strip(),
                str(data.get('listing_title') or '').strip(),
                searchrack_id,
                inventory_barcode,
                inventory_location
            ))
            alias_undo = update_alias(
                match_conn, alias_source, inventory_barcode,
                data.get('listing_title') if data.get('finder_learn') else '',
                undo_token=data.get('finder_alias_undo')
            )
            match_conn.commit()
            saved = match_cur.execute(
                'SELECT * FROM listing_inventory_matches WHERE store = ? AND listing_key = ?',
                (store, listing_key)
            ).fetchone()

        ss_caching._listing_helper_scan_cache_clear()
        return jsonify({
            'success': True,
            'finder_alias_undo': alias_undo,
            'match': dict(saved) if saved else None,
            'old_match': dict(previous) if previous else None
        })
    except Exception as e:
        return jsonify({'success': False, 'error': ss_errors._safe_error(e, 'listing-helper:inventory-match')}), 500


def api_listing_helper_dismiss():
    """Dismiss an alert by storing its hash"""
    try:
        data = request.get_json() or {}
        alert_type = data.get('alert_type')
        snap_hash = data.get('hash')
        upc = data.get('upc', '')
        store = data.get('store', '')
        listing_ids = data.get('listing_ids', [])

        if not alert_type or not snap_hash:
            return jsonify({'success': False, 'error': 'alert_type and hash required'}), 400

        _ensure_listing_alerts_tables()
        conn = sqlite3.connect('listing_alerts.db')
        cur = conn.cursor()

        import json as json_module
        cur.execute('''
            INSERT OR REPLACE INTO dismissed_alerts (alert_type, upc, store, listing_ids, snapshot_hash)
            VALUES (?, ?, ?, ?, ?)
        ''', (alert_type, upc, store, json_module.dumps(listing_ids), snap_hash))
        conn.commit()
        conn.close()
        ss_caching._listing_helper_scan_cache_clear()

        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': ss_errors._safe_error(e, 'listing-helper-dismiss')}), 500


def api_listing_helper_undismiss():
    """Remove a dismissal to re-show an alert"""
    try:
        data = request.get_json() or {}
        snap_hash = data.get('hash')

        if not snap_hash:
            return jsonify({'success': False, 'error': 'hash required'}), 400

        conn = sqlite3.connect('listing_alerts.db')
        cur = conn.cursor()
        cur.execute('DELETE FROM dismissed_alerts WHERE snapshot_hash = ?', (snap_hash,))
        conn.commit()
        conn.close()
        ss_caching._listing_helper_scan_cache_clear()

        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': ss_errors._safe_error(e, 'listing-helper-undismiss')}), 500


def api_listing_helper_dismissed():
    """Get list of dismissed alerts"""
    try:
        _ensure_listing_alerts_tables()
        conn = sqlite3.connect('listing_alerts.db')
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute('SELECT * FROM dismissed_alerts ORDER BY dismissed_at DESC')
        rows = [dict(r) for r in cur.fetchall()]
        conn.close()
        return jsonify({'success': True, 'dismissed': rows})
    except Exception as e:
        return jsonify({'success': False, 'error': ss_errors._safe_error(e, 'listing-helper-dismissed')}), 500
