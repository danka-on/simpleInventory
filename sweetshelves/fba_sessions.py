"""Fba sessions for Sweet Shelves."""

import json
import sqlite3
import time
from fba_inbound import FbaInboundValidationError
from flask import jsonify, request
from . import config as ss_config, errors as ss_errors, fba_inventory as ss_fba_inventory, fba_schema as ss_fba_schema, fba_shipments as ss_fba_shipments, listing_checks as ss_listing_checks, listing_settings as ss_listing_settings, normalization as ss_normalization, warehouse_locations as ss_warehouse_locations


def _fba_review_rows(conn, *, status='open', limit=250):
    from urllib.parse import quote

    cur = conn.cursor()
    ss_fba_schema._ensure_fba_prep_tables(cur)
    status = str(status or 'open').strip().lower()
    limit = max(1, min(int(limit or 250), 500))
    sql = '''
        SELECT r.*, b.batch_name, b.shipment_id, b.destination_fc,
               i.asin, i.seller_sku, i.fnsku, i.item_condition,
               i.prep_type, i.expiration_date, i.box_number
        FROM fba_listing_reviews r
        JOIN fba_prep_batches b ON b.id = r.batch_id
        JOIN fba_prep_items i ON i.id = r.fba_item_id
    '''
    params = []
    if status in ('open', 'reviewed'):
        sql += ' WHERE r.status = ?'
        params.append(status)
    sql += " ORDER BY CASE WHEN r.status = 'open' THEN 0 ELSE 1 END, r.created_at DESC, r.id DESC LIMIT ?"
    params.append(limit)
    rows = [dict(row) for row in cur.execute(sql, tuple(params)).fetchall()]

    marketplaces = ss_fba_inventory._fba_marketplace_status_for_barcodes([row.get('barcode') for row in rows])
    for row in rows:
        snapshot_locations = ss_fba_schema._fba_json_list(row.pop('remaining_locations_json', '[]'))
        snapshot_links = ss_fba_schema._fba_json_list(row.pop('listing_links_json', '[]'))
        row['original_reasons'] = ss_fba_schema._fba_json_list(row.pop('reasons_json', '[]'))
        inventory = ss_fba_inventory._fba_inventory_state_for_barcode(cur, row.get('barcode'))
        marketplace = marketplaces.get(ss_warehouse_locations._movelocation_barcode_key(row.get('barcode'))) or {}

        row['remaining_inventory_qty'] = inventory['quantity']
        row['remaining_locations'] = inventory['locations'] or snapshot_locations
        if marketplace.get('ebay_checked'):
            row['listed_ebay'] = 1 if marketplace.get('listed_ebay') else 0
        if marketplace.get('amazon_checked'):
            row['listed_amazon'] = 1 if marketplace.get('listed_amazon') else 0
        row['listing_links'] = marketplace.get('links') or snapshot_links
        row['reasons'] = ss_fba_inventory._fba_review_reasons(
            remaining_quantity=row.get('remaining_inventory_qty'),
            listed_ebay=bool(row.get('listed_ebay')),
            listed_amazon=bool(row.get('listed_amazon')),
        )
        row['listingagent_url'] = '/listingagent?upc=' + quote(str(row.get('barcode') or '').strip())
    return rows


def api_fba_prep_session_live(session_id):
    """Return local shared-session progress and maintain scanner presence."""
    conn = None
    try:
        data = request.get_json(silent=True) or {} if request.method == 'POST' else request.args
        client_id, operator_name = ss_fba_schema._fba_client_identity(data)
        active_box_id = ss_fba_schema._fba_trim(data.get('active_box_id'), 40).upper()
        after_scan_id = max(0, ss_listing_settings._listingagent_parse_int(data.get('after_scan_id'), 0) or 0)

        conn = sqlite3.connect(str(ss_config.BASE_DIR / 'searchRack.db'), timeout=30.0)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        ss_fba_schema._ensure_fba_prep_tables(cur)
        if request.method == 'POST' and client_id:
            ss_fba_schema._fba_touch_session_worker(cur, session_id, client_id, operator_name, active_box_id)
            cur.execute('''
                DELETE FROM fba_session_workers
                WHERE last_seen_epoch < ?
            ''', (time.time() - 86400,))
        conn.commit()

        row = cur.execute('SELECT * FROM fba_prep_sessions WHERE id = ?', (session_id,)).fetchone()
        if not row or str(row['status'] or '') == 'deleted':
            return jsonify({'success': False, 'error': 'FBA session not found'}), 404

        workers = [dict(worker) for worker in cur.execute('''
            SELECT client_id, operator_name, active_box_id, last_seen_at
            FROM fba_session_workers
            WHERE session_id = ? AND last_seen_epoch >= ?
            ORDER BY operator_name COLLATE NOCASE, client_id
        ''', (session_id, time.time() - 45)).fetchall()]
        recent_scans = [dict(scan) for scan in cur.execute('''
            SELECT id, scan_token, barcode, msku, title, packing_group_id, box_id,
                   source_location, inventory_removed, label_printed, scanned_at,
                   client_id, operator_name
            FROM fba_pack_scans
            WHERE session_id = ? AND id > ?
            ORDER BY id ASC
            LIMIT 50
        ''', (session_id, after_scan_id)).fetchall()]
        scan_summary = cur.execute('''
            SELECT COUNT(*) AS scan_count, COALESCE(MAX(id), 0) AS latest_scan_id
            FROM fba_pack_scans WHERE session_id = ?
        ''', (session_id,)).fetchone()
        return jsonify({
            'success': True,
            'session_id': session_id,
            'session_status': str(row['status'] or ''),
            'session_updated_at': row['updated_at'],
            'amazon_workflow': ss_fba_shipments._fba_amazon_state(row),
            'workers': workers,
            'worker_count': len(workers),
            'recent_scans': recent_scans,
            'scan_count': int(scan_summary['scan_count'] or 0),
            'latest_scan_id': int(scan_summary['latest_scan_id'] or 0),
            'count_revision': int(row['count_revision'] or 0),
            'rejected_items': ss_fba_schema._fba_json_list(row['rejected_items_json']),
            'items': ss_fba_schema._fba_json_list(row['items_json']),
        })
    except FbaInboundValidationError as exc:
        if conn is not None:
            conn.rollback()
        return jsonify({'success': False, 'error': str(exc)}), 409
    except Exception as exc:
        if conn is not None:
            conn.rollback()
        return jsonify({'success': False, 'error': ss_errors._safe_error(exc, 'fba shared session')}), 500
    finally:
        if conn is not None:
            conn.close()


def api_fba_prep_sessions():
    conn = None
    try:
        conn = sqlite3.connect(str(ss_config.BASE_DIR / 'searchRack.db'), timeout=30.0)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        ss_fba_schema._ensure_fba_prep_tables(cur)

        if request.method == 'GET':
            status = str(request.args.get('status') or 'open').strip().lower()
            limit = max(1, min(ss_listing_settings._listingagent_parse_int(request.args.get('limit'), 100) or 100, 250))
            sql = 'SELECT * FROM fba_prep_sessions'
            params = []
            if status in ('open', 'completed', 'deleted'):
                sql += ' WHERE status = ?'
                params.append(status)
            elif status != 'all':
                return jsonify({'success': False, 'error': 'Session status must be open, completed, deleted, or all'}), 400
            sql += " ORDER BY CASE WHEN status = 'open' THEN 0 ELSE 1 END, updated_at DESC, id DESC LIMIT ?"
            params.append(limit)
            rows = [ss_fba_inventory._fba_session_row_payload(row) for row in cur.execute(sql, tuple(params)).fetchall()]
            conn.commit()
            return jsonify({'success': True, 'sessions': rows})

        data = request.get_json() or {}
        raw_items = data.get('items') if isinstance(data.get('items'), list) else []
        if len(raw_items) > 500:
            return jsonify({'success': False, 'error': 'A saved FBA session cannot exceed 500 item rows'}), 400
        items = []
        for raw_item in raw_items:
            item = ss_fba_inventory._fba_session_item_payload(raw_item)
            if item.get('barcode') or item.get('seller_sku'):
                items.append(item)
        items_json = json.dumps(items, ensure_ascii=False)
        if len(items_json.encode('utf-8')) > 2 * 1024 * 1024:
            return jsonify({'success': False, 'error': 'This FBA session is too large to save'}), 400
        working_locations = ss_fba_inventory._fba_working_locations_payload(data.get('working_locations'))
        working_locations_json = json.dumps(working_locations, ensure_ascii=False)

        session_id = ss_listing_settings._listingagent_parse_int(data.get('id'), None)
        if session_id is not None and session_id <= 0:
            session_id = None
        if session_id:
            conn.commit()
            cur.execute('BEGIN IMMEDIATE')
            existing_session = cur.execute('''
                SELECT items_json, amazon_inbound_plan_id, count_revision, rejected_items_json
                FROM fba_prep_sessions
                WHERE id = ? AND status = 'open'
            ''', (session_id,)).fetchone()
            incoming_count_revision = max(0, ss_listing_settings._listingagent_parse_int(data.get('count_revision'), 0) or 0)
            if existing_session and incoming_count_revision < int(existing_session['count_revision'] or 0):
                items = ss_fba_schema._fba_json_list(existing_session['items_json'])
                items_json = json.dumps(items, ensure_ascii=False)
            if (
                existing_session
                and ss_fba_schema._fba_trim(existing_session['amazon_inbound_plan_id'], 38)
                and ss_fba_inventory._fba_plan_item_signature(ss_fba_schema._fba_json_list(existing_session['items_json']))
                    != ss_fba_inventory._fba_plan_item_signature(items)
            ):
                return jsonify({
                    'success': False,
                    'error': 'Amazon plan quantities are locked. Start a new session to change SKUs or quantities.',
                }), 409
        # Keep removed rejects recognizable when the physical item is scanned
        # during packing. A current repaired row supersedes its saved rejection.
        rejected_items = []
        if session_id and existing_session:
            def reject_key(item):
                return ss_warehouse_locations._movelocation_barcode_key(item.get('barcode')) or str(item.get('seller_sku') or '').casefold()
            history = {reject_key(item): item for item in ss_fba_schema._fba_json_list(existing_session['rejected_items_json']) if isinstance(item, dict)}
            incoming_keys = {reject_key(item) for item in items}
            for item in ss_fba_schema._fba_json_list(existing_session['items_json']) + items:
                if not isinstance(item, dict):
                    continue
                key = reject_key(item)
                if key not in incoming_keys and (item.get('fba_enablement_status') != 'ready' or data.get('preserve_excluded_items') is True):
                    history[key] = {**item, 'fba_set_aside': True}
                elif item.get('fba_enablement_status') == 'failed':
                    history[key] = item
                else:
                    history.pop(key, None)
            rejected_items = list(history.values())
        batch_name = ss_fba_schema._fba_trim(data.get('batch_name'), 120)
        session_name = ss_fba_schema._fba_trim(data.get('session_name') or batch_name, 120)
        if not session_name:
            session_name = 'FBA Session ' + time.strftime('%Y-%m-%d %H:%M')
        prep_owner = ss_fba_schema._fba_trim(data.get('prep_owner') or 'SELLER', 20).upper()
        label_owner = ss_fba_schema._fba_trim(data.get('label_owner') or 'SELLER', 20).upper()
        if prep_owner not in ('SELLER', 'AMAZON') or label_owner not in ('SELLER', 'AMAZON'):
            return jsonify({'success': False, 'error': 'Prep and label owners must be Seller or Amazon'}), 400
        boxes_raw = data.get('boxes_planned')
        boxes_planned = None if boxes_raw in (None, '') else ss_normalization._strict_inventory_quantity(boxes_raw)
        if boxes_planned is not None and boxes_planned <= 0:
            return jsonify({'success': False, 'error': 'Planned boxes must be a positive whole number'}), 400
        now = ss_listing_checks._listagent_now_iso()
        total_units = sum(int(item.get('quantity') or 0) for item in items)

        if session_id:
            cur.execute('''
                UPDATE fba_prep_sessions
                SET session_name = ?, batch_name = ?, shipment_id = ?, destination_fc = ?,
                    prep_owner = ?, label_owner = ?, boxes_planned = ?, notes = ?,
                    working_locations_json = ?, items_json = ?, item_count = ?,
                    total_units = ?, updated_at = ?
                WHERE id = ? AND status = 'open'
            ''', (
                session_name,
                batch_name or None,
                ss_fba_schema._fba_trim(data.get('shipment_id'), 120) or None,
                ss_fba_schema._fba_trim(data.get('destination_fc'), 80).upper() or None,
                prep_owner,
                label_owner,
                boxes_planned,
                ss_fba_schema._fba_trim(data.get('notes'), 2000) or None,
                working_locations_json,
                items_json,
                len(items),
                total_units,
                now,
                session_id,
            ))
            if cur.rowcount <= 0:
                conn.rollback()
                return jsonify({'success': False, 'error': 'Open FBA session not found'}), 404
        else:
            cur.execute('''
                INSERT INTO fba_prep_sessions (
                    session_name, batch_name, shipment_id, destination_fc,
                    prep_owner, label_owner, boxes_planned, notes,
                    working_locations_json, items_json, item_count, total_units,
                    status, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'open', ?, ?)
            ''', (
                session_name,
                batch_name or None,
                ss_fba_schema._fba_trim(data.get('shipment_id'), 120) or None,
                ss_fba_schema._fba_trim(data.get('destination_fc'), 80).upper() or None,
                prep_owner,
                label_owner,
                boxes_planned,
                ss_fba_schema._fba_trim(data.get('notes'), 2000) or None,
                working_locations_json,
                items_json,
                len(items),
                total_units,
                now,
                now,
            ))
            session_id = cur.lastrowid

        cur.execute('UPDATE fba_prep_sessions SET rejected_items_json = ? WHERE id = ?',
                    (json.dumps(rejected_items, ensure_ascii=False), session_id))
        conn.commit()
        row = cur.execute('SELECT * FROM fba_prep_sessions WHERE id = ?', (session_id,)).fetchone()
        return jsonify({'success': True, 'session': ss_fba_inventory._fba_session_row_payload(row, include_items=True)})
    except Exception as exc:
        if conn is not None:
            conn.rollback()
        return jsonify({'success': False, 'error': ss_errors._safe_error(exc, 'fba prep sessions')}), 500
    finally:
        if conn is not None:
            conn.close()


def api_fba_prep_session_detail(session_id):
    conn = None
    try:
        conn = sqlite3.connect(str(ss_config.BASE_DIR / 'searchRack.db'), timeout=30.0)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        ss_fba_schema._ensure_fba_prep_tables(cur)
        row = cur.execute('SELECT * FROM fba_prep_sessions WHERE id = ?', (session_id,)).fetchone()
        if not row or str(row['status'] or '') == 'deleted':
            return jsonify({'success': False, 'error': 'FBA session not found'}), 404
        if request.method == 'DELETE':
            if str(row['status'] or '') != 'open':
                return jsonify({'success': False, 'error': 'Only open FBA sessions can be deleted'}), 409
            now = ss_listing_checks._listagent_now_iso()
            cur.execute('''
                UPDATE fba_prep_sessions
                SET status = 'deleted', deleted_at = ?, updated_at = ?
                WHERE id = ? AND status = 'open'
            ''', (now, now, session_id))
            conn.commit()
            return jsonify({'success': True, 'deleted': True, 'recoverable': True})
        conn.commit()
        return jsonify({'success': True, 'session': ss_fba_inventory._fba_session_row_payload(row, include_items=True)})
    except Exception as exc:
        if conn is not None:
            conn.rollback()
        return jsonify({'success': False, 'error': ss_errors._safe_error(exc, 'fba prep session detail')}), 500
    finally:
        if conn is not None:
            conn.close()


def api_fba_prep_batches():
    conn = None
    try:
        limit = max(1, min(ss_listing_settings._listingagent_parse_int(request.args.get('limit'), 12) or 12, 25))
        conn = sqlite3.connect(str(ss_config.BASE_DIR / 'searchRack.db'), timeout=30.0)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        ss_fba_schema._ensure_fba_prep_tables(cur)
        conn.commit()
        batch_ids = [row['id'] for row in cur.execute('''
            SELECT id
            FROM fba_prep_batches
            WHERE status = 'completed'
            ORDER BY COALESCE(completed_at, created_at) DESC, id DESC
            LIMIT ?
        ''', (limit,)).fetchall()]
        batches = [ss_fba_inventory._fba_batch_payload(conn, batch_id) for batch_id in batch_ids]
        return jsonify({'success': True, 'batches': [batch for batch in batches if batch is not None]})
    except Exception as exc:
        return jsonify({'success': False, 'error': ss_errors._safe_error(exc, 'fba prep batches')}), 500
    finally:
        if conn is not None:
            conn.close()


def api_fba_prep_batch_detail(batch_id):
    conn = None
    try:
        conn = sqlite3.connect(str(ss_config.BASE_DIR / 'searchRack.db'), timeout=30.0)
        conn.row_factory = sqlite3.Row
        batch = ss_fba_inventory._fba_batch_payload(conn, batch_id)
        if not batch:
            return jsonify({'success': False, 'error': 'FBA batch not found'}), 404
        return jsonify({'success': True, 'batch': batch})
    except Exception as exc:
        return jsonify({'success': False, 'error': ss_errors._safe_error(exc, 'fba prep batch detail')}), 500
    finally:
        if conn is not None:
            conn.close()


def api_fba_prep_reviews():
    conn = None
    try:
        status = str(request.args.get('status') or 'open').strip().lower()
        if status not in ('open', 'reviewed', 'all'):
            return jsonify({'success': False, 'error': 'Review status must be open, reviewed, or all'}), 400
        limit = ss_listing_settings._listingagent_parse_int(request.args.get('limit'), 250) or 250
        conn = sqlite3.connect(str(ss_config.BASE_DIR / 'searchRack.db'), timeout=30.0)
        conn.row_factory = sqlite3.Row
        rows = _fba_review_rows(conn, status=status, limit=limit)
        counts = {
            row['status']: int(row['count'])
            for row in conn.execute('SELECT status, COUNT(*) AS count FROM fba_listing_reviews GROUP BY status').fetchall()
        }
        conn.commit()
        return jsonify({
            'success': True,
            'items': rows,
            'counts': {
                'open': counts.get('open', 0),
                'reviewed': counts.get('reviewed', 0),
            }
        })
    except Exception as exc:
        return jsonify({'success': False, 'error': ss_errors._safe_error(exc, 'fba prep reviews')}), 500
    finally:
        if conn is not None:
            conn.close()


def api_fba_prep_review_update(review_id):
    conn = None
    try:
        data = request.get_json() or {}
        status = str(data.get('status') or '').strip().lower()
        if status not in ('open', 'reviewed'):
            return jsonify({'success': False, 'error': 'Review status must be open or reviewed'}), 400
        reviewer_note = ss_fba_schema._fba_trim(data.get('reviewer_note'), 1500)
        now = ss_listing_checks._listagent_now_iso()

        conn = sqlite3.connect(str(ss_config.BASE_DIR / 'searchRack.db'), timeout=30.0)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        ss_fba_schema._ensure_fba_prep_tables(cur)
        cur.execute('''
            UPDATE fba_listing_reviews
            SET status = ?, reviewer_note = ?, reviewed_at = ?
            WHERE id = ?
        ''', (
            status,
            reviewer_note or None,
            now if status == 'reviewed' else None,
            review_id,
        ))
        if cur.rowcount <= 0:
            conn.rollback()
            return jsonify({'success': False, 'error': 'FBA review item not found'}), 404
        conn.commit()
        row = next((item for item in _fba_review_rows(conn, status='all', limit=500) if int(item.get('id') or 0) == review_id), None)
        return jsonify({'success': True, 'item': row})
    except Exception as exc:
        if conn is not None:
            conn.rollback()
        return jsonify({'success': False, 'error': ss_errors._safe_error(exc, 'fba prep review update')}), 500
    finally:
        if conn is not None:
            conn.close()


def api_fba_prep_items():
    """Search the permanent UPC, Seller SKU, and FNSKU ledger for completed FBA items."""
    conn = None
    try:
        query = ss_fba_schema._fba_trim(request.args.get('q'), 160)
        limit = max(1, min(ss_listing_settings._listingagent_parse_int(request.args.get('limit'), 250) or 250, 500))
        conn = sqlite3.connect(str(ss_config.BASE_DIR / 'searchRack.db'), timeout=30.0)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        ss_fba_schema._ensure_fba_prep_tables(cur)
        conn.commit()
        where_sql = "WHERE b.status = 'completed'"
        params = []
        if query:
            where_sql += ''' AND (
                i.barcode LIKE ? OR i.title LIKE ? OR i.asin LIKE ?
                OR i.seller_sku LIKE ? OR i.fnsku LIKE ?
                OR b.batch_name LIKE ? OR b.shipment_id LIKE ?
                OR b.amazon_inbound_plan_id LIKE ?
            )'''
            like_query = f'%{query}%'
            params.extend([like_query] * 8)
        total = cur.execute(f'''
            SELECT COUNT(*)
            FROM fba_prep_items i
            JOIN fba_prep_batches b ON b.id = i.batch_id
            {where_sql}
        ''', params).fetchone()[0]
        rows = cur.execute(f'''
            SELECT i.id, i.batch_id, i.barcode, i.title,
                   i.requested_quantity, i.quantity_removed,
                   i.asin, i.seller_sku, i.fnsku, i.item_condition,
                   i.created_at, b.batch_name, b.shipment_id,
                   b.amazon_inbound_plan_id, b.destination_fc,
                   b.completed_at
            FROM fba_prep_items i
            JOIN fba_prep_batches b ON b.id = i.batch_id
            {where_sql}
            ORDER BY COALESCE(b.completed_at, i.created_at) DESC, i.id DESC
            LIMIT ?
        ''', [*params, limit]).fetchall()
        return jsonify({
            'success': True,
            'items': [dict(row) for row in rows],
            'count': int(total or 0),
            'limit': limit,
            'query': query,
        })
    except Exception as exc:
        return jsonify({'success': False, 'error': ss_errors._safe_error(exc, 'fba prep item ledger')}), 500
    finally:
        if conn is not None:
            conn.close()
