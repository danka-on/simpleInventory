"""Caching for Sweet Shelves."""

import time
from DBmanager import connect_db
from flask import jsonify, request
from . import (
    listing_alerts as ss_listing_alerts, prep_context as ss_prep_context, runtime as ss_runtime,
    warehouse_search as ss_warehouse_search,
)


def update_data_version():
    try:
        with connect_db('bol.db') as conn:
            conn.execute('CREATE TABLE IF NOT EXISTS app_metadata (key TEXT PRIMARY KEY, value TEXT)')
            conn.execute("""
                INSERT INTO app_metadata (key, value) VALUES ('data_version', ?)
                ON CONFLICT(key) DO UPDATE SET value =
                    MAX(CAST(app_metadata.value AS INTEGER) + 1, CAST(excluded.value AS INTEGER))
            """, (int(time.time() * 1000),))
    except Exception as e:
        print(f"Error updating data version: {e}")


def _listing_helper_scan_cache_get(max_age_seconds=15):
    now_ts = time.time()
    try:
        max_age = float(max_age_seconds or 0)
    except Exception:
        max_age = 0
    if max_age <= 0:
        return None
    with ss_listing_alerts._listing_helper_scan_cache_lock:
        payload = ss_listing_alerts._listing_helper_scan_cache_state.get('payload')
        ts = float(ss_listing_alerts._listing_helper_scan_cache_state.get('ts') or 0.0)
        if payload is None:
            return None
        if (now_ts - ts) > max_age:
            return None
        return payload


def _listing_helper_scan_cache_set(payload):
    with ss_listing_alerts._listing_helper_scan_cache_lock:
        ss_listing_alerts._listing_helper_scan_cache_state['payload'] = payload
        ss_listing_alerts._listing_helper_scan_cache_state['ts'] = time.time()


def _listing_helper_scan_cache_clear():
    with ss_listing_alerts._listing_helper_scan_cache_lock:
        ss_listing_alerts._listing_helper_scan_cache_state['payload'] = None
        ss_listing_alerts._listing_helper_scan_cache_state['ts'] = 0.0


def _invalidate_searchrack_cache():
    """Clear cached search views after searchRack writes."""
    try:
        ss_runtime.cache.clear()
    except Exception as e:
        print(f"Warning: cache clear failed after searchRack update: {e}")
    try:
        _listing_helper_scan_cache_clear()
    except Exception:
        pass
    try:
        search_all_cache = ss_warehouse_search._search_all_cache
        if isinstance(search_all_cache, dict):
            search_all_cache.clear()
    except Exception:
        pass
    try:
        update_data_version()
    except Exception as e:
        print(f"Warning: data version update failed after searchRack update: {e}")


def get_data_version():
    try:
        with connect_db('bol.db') as conn:
            conn.execute('CREATE TABLE IF NOT EXISTS app_metadata (key TEXT PRIMARY KEY, value TEXT)')
            row = conn.execute("SELECT value FROM app_metadata WHERE key = 'data_version'").fetchone()
            if row is None:
                conn.execute("INSERT OR IGNORE INTO app_metadata (key, value) VALUES ('data_version', ?)",
                             (int(time.time() * 1000),))
                row = conn.execute("SELECT value FROM app_metadata WHERE key = 'data_version'").fetchone()
            return int(row[0])
    except Exception as e:
        print(f"Error getting data version: {e}")
        return int(time.time() * 1000)




def api_data_version():
    return jsonify({'version': get_data_version()})


def api_clear_cache():
    """Clear the cache for a specific UPC lookup to force refresh"""
    try:
        upc = request.args.get('upc')
        if upc:
            # Clear specific cache key for this UPC
            cache_key = f"view//api/bol_lookup?upc={upc}"
            ss_runtime.cache.delete(cache_key)
            # Also clear memoized lookup entries (including lot-scoped query variants)
            ss_runtime.cache.delete_memoized(ss_prep_context.api_bol_lookup)
        else:
            # Clear all bol_lookup cache
            ss_runtime.cache.delete_memoized(ss_prep_context.api_bol_lookup)
        
        return jsonify({'success': True})
    except Exception as e:
        print(f"Cache clear error: {e}")
        return jsonify({'success': True})  # Always return success, cache clear is not critical
