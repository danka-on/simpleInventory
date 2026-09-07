"""Listing queue for Sweet Shelves."""

import hashlib
import json
import os
import requests
import sqlite3
import time
from flask import jsonify, request, url_for
from . import (
    bulk_manifest as ss_bulk_manifest, database as ss_database, errors as ss_errors, listing_checks as
    ss_listing_checks, listing_log as ss_listing_log, listing_settings as ss_listing_settings,
    normalization as ss_normalization, prep_schema as ss_prep_schema, runtime as ss_runtime,
    shipping_identity as ss_shipping_identity, warehouse_allocations as ss_warehouse_allocations,
)


def _listagent_format_upc12(value):
    s = (value or '').strip()
    if not s:
        return ''
    if '-' in s:
        base, suffix = s.split('-', 1)
    else:
        base, suffix = s, ''
    base = (base or '').strip()
    suffix = (suffix or '').strip()
    if base.isdigit() and len(base) <= 12:
        base = base.zfill(12)
    return f'{base}-{suffix}' if suffix else base


def _listagent_upc_variants(value):
    """Return tolerant UPC variants: padded/raw/stripped (preserving -suffix)."""
    raw = (value or '').strip()
    if not raw:
        return []
    formatted = _listagent_format_upc12(raw)
    out = []

    def add(v):
        vv = (v or '').strip()
        if vv and vv not in out:
            out.append(vv)

    add(formatted)
    add(raw)

    core = formatted or raw
    if '-' in core:
        base, suffix = core.split('-', 1)
    else:
        base, suffix = core, ''

    if base.isdigit():
        stripped = base.lstrip('0') or '0'
        add(f'{stripped}-{suffix}' if suffix else stripped)

    return out


def _items_to_list_upc_search_variants(value):
    """
    Return tolerant UPC search variants for /api/bol_items.

    This keeps suffixes intact while also matching the common normalized forms
    used by listing-agent, so a user can search for a suffixed item regardless
    of whether the stored row is padded, stripped, or typed with leading zeros.
    """
    raw = (value or '').strip()
    if not raw:
        return []

    variants = []

    def add(candidate):
        candidate = (candidate or '').strip()
        if candidate and candidate not in variants:
            variants.append(candidate)

    add(raw)
    add(ss_normalization._normalize_upc_preserve_suffix_for_match(raw))
    add(_listagent_format_upc12(raw))
    for candidate in _listagent_upc_variants(raw):
        add(candidate)

    return variants


def _listagent_add_to_queue(upc, *, title=None, source=None, item_status=None, added_mode=None):
    upc = _listagent_format_upc12(upc)
    if not upc:
        raise ValueError('upc is required')
    upc_variants = _listagent_upc_variants(upc)
    placeholders = ','.join('?' for _ in upc_variants)

    now = ss_listing_checks._listagent_now_iso()
    title = (title or '').strip() or None
    source = (source or '').strip() or None
    item_status = (item_status or '').strip().lower() or None
    added_mode = ss_listing_settings._listagent_normalize_added_mode(added_mode, default='my')

    with ss_database.db_connection('listagent.db') as conn:
        cur = conn.cursor()
        ss_listing_checks._listagent_init_tables(cur)

        # If it already exists in the active list (queued or done), keep it and just fill metadata.
        cur.execute('''
            SELECT *
            FROM listing_queue
            WHERE upc IN (''' + placeholders + ''') AND status IN ('queued', 'done')
            ORDER BY added_at DESC, id DESC
            LIMIT 1
        ''', tuple(upc_variants))
        row = cur.fetchone()
        if row:
            qid = row['id']
            should_update_metadata = (
                title is not None or
                source is not None or
                item_status is not None or
                added_mode in ('my', 'auto')
            )
            if should_update_metadata:
                cur.execute('''
                    UPDATE listing_queue
                    SET
                        title = COALESCE(NULLIF(title, ''), ?),
                        source = COALESCE(NULLIF(source, ''), ?),
                        added_mode = COALESCE(?, COALESCE(NULLIF(TRIM(added_mode), ''), 'my')),
                        item_status = COALESCE(?, item_status)
                    WHERE id = ?
                ''', (title, source, added_mode, item_status, qid))
            cur.execute('SELECT * FROM listing_queue WHERE id = ? LIMIT 1', (qid,))
            return ss_listing_checks._listagent_row_to_dict(cur.fetchone()), False

        # If it was removed before, revive it (and clear any previous "listed" flags).
        cur.execute('''
            SELECT id
            FROM listing_queue
            WHERE upc IN (''' + placeholders + ''') AND status = 'removed'
            ORDER BY removed_at DESC, id DESC
            LIMIT 1
        ''', tuple(upc_variants))
        removed = cur.fetchone()
        if removed:
            qid = removed['id']
            cur.execute('''
                UPDATE listing_queue
                SET
                    status = 'queued',
                    added_at = ?,
                    removed_at = NULL,
                    title = COALESCE(?, title),
                    source = COALESCE(?, source),
                    added_mode = COALESCE(?, COALESCE(NULLIF(TRIM(added_mode), ''), 'my')),
                    item_status = COALESCE(?, item_status),
                    listed_at = NULL,
                    listed_platform = NULL,
                    listed_listing_id = NULL,
                    listed_offer_id = NULL,
                    listed_sku = NULL,
                    listed_asin = NULL,
                    listed_url = NULL,
                    listed_ebay_at = NULL,
                    listed_amazon_at = NULL
                WHERE id = ?
            ''', (now, title, source, added_mode, item_status, qid))
            cur.execute('SELECT * FROM listing_queue WHERE id = ? LIMIT 1', (qid,))
            return ss_listing_checks._listagent_row_to_dict(cur.fetchone()), True

        # Fresh insert
        cur.execute('''
            INSERT INTO listing_queue (upc, title, source, added_mode, item_status, status, added_at)
            VALUES (?, ?, ?, ?, ?, 'queued', ?)
        ''', (upc, title, source, added_mode, item_status, now))
        qid = cur.lastrowid
        cur.execute('SELECT * FROM listing_queue WHERE id = ? LIMIT 1', (qid,))
        return ss_listing_checks._listagent_row_to_dict(cur.fetchone()), True


def _listagent_get_queue(*, limit=50, added_mode=''):
    limit = int(limit or 50)
    limit = max(1, min(limit, 200))
    mode = ss_listing_settings._listagent_normalize_added_mode(added_mode, default='')
    if mode not in ('my', 'auto'):
        mode = ''
    with ss_database.db_connection('listagent.db') as conn:
        cur = conn.cursor()
        ss_listing_checks._listagent_init_tables(cur)
        sql = '''
            SELECT *
            FROM listing_queue
            WHERE status IN ('queued', 'done')
        '''
        params = []
        if mode:
            sql += " AND COALESCE(NULLIF(TRIM(added_mode), ''), 'my') = ?"
            params.append(mode)
        sql += '''
            ORDER BY CASE WHEN status = 'done' THEN 1 ELSE 0 END, added_at DESC, id DESC
            LIMIT ?
        '''
        params.append(limit)
        cur.execute(sql, tuple(params))
        rows = [ss_listing_checks._listagent_row_to_dict(r) for r in cur.fetchall()]
    draft_flags = _listagent_get_working_draft_statuses([r.get('upc') for r in rows if r])
    check_flags = ss_listing_checks._listagent_get_check_request_statuses([r.get('upc') for r in rows if r])
    for row in rows:
        if not row:
            continue
        upc = _listagent_format_upc12(row.get('upc'))
        row['has_working_draft'] = bool(draft_flags.get(upc))
        check = check_flags.get((row.get('upc') or '').strip()) or check_flags.get(upc) or {}
        row['has_check_request'] = bool(check)
        row['check_request_status'] = (check.get('status') or '').strip().lower() if check else ''
        row['check_request_quantity'] = check.get('check_quantity') if check else None
        row['check_request_take_pictures'] = bool(ss_listing_settings._listingagent_parse_int(check.get('take_pictures'), 0)) if check else False
        row['check_request_check_color'] = bool(ss_listing_settings._listingagent_parse_int(check.get('check_color'), 0)) if check else False
        row['check_request_check_condition'] = bool(ss_listing_settings._listingagent_parse_int(check.get('check_condition'), 0)) if check else False
        row['check_request_note'] = (check.get('custom_note') or '').strip() if check else ''
    return rows


def _listagent_get_queue_statuses(upcs, *, added_mode=''):
    mode = ss_listing_settings._listagent_normalize_added_mode(added_mode, default='')
    if mode not in ('my', 'auto'):
        mode = ''

    requested = []
    requested_seen = set()
    variant_to_requested = {}
    all_variants = []

    for raw in upcs or []:
        key = (raw or '').strip()
        if not key or key in requested_seen:
            continue
        requested_seen.add(key)
        requested.append(key)
        for variant in _listagent_upc_variants(key):
            if variant not in variant_to_requested:
                variant_to_requested[variant] = []
            if key not in variant_to_requested[variant]:
                variant_to_requested[variant].append(key)
            if variant not in all_variants:
                all_variants.append(variant)

    if not requested or not all_variants:
        return {}

    placeholders = ','.join('?' for _ in all_variants)
    with ss_database.db_connection('listagent.db') as conn:
        cur = conn.cursor()
        ss_listing_checks._listagent_init_tables(cur)
        sql = '''
            SELECT *
            FROM listing_queue
            WHERE upc IN (''' + placeholders + ''')
              AND status IN ('queued', 'done')
        '''
        params = list(all_variants)
        if mode:
            sql += " AND COALESCE(NULLIF(TRIM(added_mode), ''), 'my') = ?"
            params.append(mode)
        sql += '''
            ORDER BY CASE WHEN status = 'queued' THEN 0 ELSE 1 END, added_at DESC, id DESC
        '''
        cur.execute(sql, tuple(params))
        rows = cur.fetchall()

    out = {}
    draft_flags = _listagent_get_working_draft_statuses([_listagent_format_upc12(ss_listing_checks._listagent_row_to_dict(row).get('upc')) for row in rows])
    check_flags = ss_listing_checks._listagent_get_check_request_statuses([ss_listing_checks._listagent_row_to_dict(row).get('upc') for row in rows])
    for row in rows:
        item = ss_listing_checks._listagent_row_to_dict(row)
        if not item:
            continue
        item['has_working_draft'] = bool(draft_flags.get(_listagent_format_upc12(item.get('upc'))))
        check = check_flags.get((item.get('upc') or '').strip()) or check_flags.get(_listagent_format_upc12(item.get('upc'))) or {}
        item['has_check_request'] = bool(check)
        item['check_request_status'] = (check.get('status') or '').strip().lower() if check else ''
        item['check_request_quantity'] = check.get('check_quantity') if check else None
        item['check_request_take_pictures'] = bool(ss_listing_settings._listingagent_parse_int(check.get('take_pictures'), 0)) if check else False
        item['check_request_check_color'] = bool(ss_listing_settings._listingagent_parse_int(check.get('check_color'), 0)) if check else False
        item['check_request_check_condition'] = bool(ss_listing_settings._listingagent_parse_int(check.get('check_condition'), 0)) if check else False
        item['check_request_note'] = (check.get('custom_note') or '').strip() if check else ''
        matched_requested = []
        for variant in _listagent_upc_variants(item.get('upc') or ''):
            matched_requested.extend(variant_to_requested.get(variant, []))
        for key in matched_requested:
            if key not in out:
                out[key] = item
    return out


def _listagent_mark_listed(upc, *, platform=None, listing_id=None, offer_id=None, sku=None, asin=None, url=None,
                           marketplace_id=None, title=None, price=None, quantity=None,
                           source='listingagent', action='listed', success=True, error=None, meta=None):
    upc = _listagent_format_upc12(upc)
    if not upc:
        raise ValueError('upc is required')
    sku = (sku or '').strip() or upc
    upc_variants = _listagent_upc_variants(upc)
    placeholders = ','.join('?' for _ in upc_variants)

    now = ss_listing_checks._listagent_now_iso()
    platform = (platform or '').strip().lower() or None
    updated_item = None

    with ss_database.db_connection('listagent.db') as conn:
        cur = conn.cursor()
        ss_listing_checks._listagent_init_tables(cur)

        cur.execute('''
            SELECT *
            FROM listing_queue
            WHERE upc IN (''' + placeholders + ''') AND status IN ('queued', 'done')
            ORDER BY added_at DESC, id DESC
            LIMIT 1
        ''', tuple(upc_variants))
        row = cur.fetchone()

        if row and bool(success):
            qid = row['id']

            # Persist per-platform completion (queue stays visible until both platforms are done).
            cur.execute('''
                UPDATE listing_queue
                SET
                    listed_at = ?,
                    listed_platform = ?,
                    listed_listing_id = ?,
                    listed_offer_id = ?,
                    listed_sku = ?,
                    listed_asin = ?,
                    listed_url = ?,
                    listed_ebay_at = CASE WHEN ? = 'ebay' THEN ? ELSE listed_ebay_at END,
                    listed_amazon_at = CASE WHEN ? = 'amazon' THEN ? ELSE listed_amazon_at END
                WHERE id = ?
            ''', (
                now, platform, listing_id, offer_id, sku, asin, url,
                platform, now,
                platform, now,
                qid
            ))

            # If both platforms are listed, mark the queue item as done.
            cur.execute('SELECT listed_ebay_at, listed_amazon_at FROM listing_queue WHERE id = ? LIMIT 1', (qid,))
            st = cur.fetchone()
            ebay_at = (st['listed_ebay_at'] if st else None)
            amazon_at = (st['listed_amazon_at'] if st else None)
            new_status = 'done' if (ebay_at and amazon_at) else 'queued'
            cur.execute('UPDATE listing_queue SET status = ? WHERE id = ?', (new_status, qid))

            cur.execute('SELECT * FROM listing_queue WHERE id = ? LIMIT 1', (qid,))
            updated_item = ss_listing_checks._listagent_row_to_dict(cur.fetchone())

    # Best-effort persistent listing log (separate DB; never blocks listing flow)
    try:
        ss_listing_log._listinglog_add_entry(
            upc=upc,
            platform=platform,
            action=action or 'listed',
            source=source or 'listingagent',
            created_at=now,
            marketplace_id=marketplace_id,
            listing_id=listing_id,
            offer_id=offer_id,
            sku=sku,
            asin=asin,
            url=url,
            title=title,
            price=price,
            quantity=quantity,
            success=bool(success),
            error=error,
            meta=meta
        )
    except Exception:
        pass

    return updated_item


def _listagent_remove_from_queue(upc):
    upc = _listagent_format_upc12(upc)
    if not upc:
        raise ValueError('upc is required')
    upc_variants = _listagent_upc_variants(upc)
    placeholders = ','.join('?' for _ in upc_variants)
    now = ss_listing_checks._listagent_now_iso()
    with ss_database.db_connection('listagent.db') as conn:
        cur = conn.cursor()
        ss_listing_checks._listagent_init_tables(cur)
        cur.execute('''
            UPDATE listing_queue
            SET status = 'removed', removed_at = ?
            WHERE upc IN (''' + placeholders + ''') AND status IN ('queued', 'done')
        ''', (now, *tuple(upc_variants)))
        changed = bool(cur.rowcount and cur.rowcount > 0)
        cur.execute('''
            SELECT *
            FROM listing_queue
            WHERE upc IN (''' + placeholders + ''')
            ORDER BY removed_at DESC, id DESC
            LIMIT 1
        ''', tuple(upc_variants))
        return ss_listing_checks._listagent_row_to_dict(cur.fetchone()), changed


def _listagent_add_photo(upc, *, image_path, original_filename=None, size_bytes=None):
    upc = _listagent_format_upc12(upc)
    if not upc:
        raise ValueError('upc is required')
    image_path = (image_path or '').strip()
    if not image_path:
        raise ValueError('image_path is required')

    now = ss_listing_checks._listagent_now_iso()
    with ss_database.db_connection('listagent.db') as conn:
        cur = conn.cursor()
        ss_listing_checks._listagent_init_tables(cur)
        cur.execute('''
            INSERT INTO listing_photos (upc, image_path, original_filename, size_bytes, created_at)
            VALUES (?, ?, ?, ?, ?)
        ''', (upc, image_path, (original_filename or None), size_bytes, now))
        pid = cur.lastrowid
        cur.execute('SELECT * FROM listing_photos WHERE id = ? LIMIT 1', (pid,))
        return ss_listing_checks._listagent_row_to_dict(cur.fetchone())


def _listagent_get_photos(upc, *, limit=30):
    upc = _listagent_format_upc12(upc)
    if not upc:
        raise ValueError('upc is required')
    limit = int(limit or 30)
    limit = max(1, min(limit, 200))
    upc_variants = _listagent_upc_variants(upc)
    placeholders = ','.join('?' for _ in upc_variants)
    with ss_database.db_connection('listagent.db') as conn:
        cur = conn.cursor()
        ss_listing_checks._listagent_init_tables(cur)
        cur.execute('''
            SELECT *
            FROM listing_photos
            WHERE upc COLLATE NOCASE IN (''' + placeholders + ''')
            ORDER BY created_at DESC, id DESC
            LIMIT ?
        ''', (*tuple(upc_variants), limit))
        return [ss_listing_checks._listagent_row_to_dict(r) for r in cur.fetchall()]


def _listagent_photo_path_exists(image_path):
    image_path = (image_path or '').strip()
    if not image_path:
        return False
    with ss_database.db_connection('listagent.db') as conn:
        cur = conn.cursor()
        ss_listing_checks._listagent_init_tables(cur)
        cur.execute('SELECT 1 FROM listing_photos WHERE image_path = ? LIMIT 1', (image_path,))
        return cur.fetchone() is not None


def _listagent_get_working_draft(upc):
    upc = _listagent_format_upc12(upc)
    if not upc:
        return None
    with ss_database.db_connection('listagent.db') as conn:
        cur = conn.cursor()
        ss_listing_checks._listagent_init_tables(cur)
        cur.execute('''
            SELECT upc, draft_json, updated_at
            FROM listing_agent_working_drafts
            WHERE upc = ? COLLATE NOCASE
            LIMIT 1
        ''', (upc,))
        row = cur.fetchone()
        if not row:
            return None
        draft = None
        try:
            draft = json.loads(row['draft_json'] if isinstance(row, sqlite3.Row) else row[1])
        except Exception:
            draft = None
        if not isinstance(draft, dict):
            return None
        draft['upc'] = row['upc'] if isinstance(row, sqlite3.Row) else row[0]
        draft['updated_at'] = row['updated_at'] if isinstance(row, sqlite3.Row) else row[2]
        return draft


def _listagent_upsert_working_draft(upc, draft):
    upc = _listagent_format_upc12(upc)
    if not upc:
        raise ValueError('upc is required')
    payload = draft if isinstance(draft, dict) else {}
    now = ss_listing_checks._listagent_now_iso()
    with ss_database.db_connection('listagent.db') as conn:
        cur = conn.cursor()
        ss_listing_checks._listagent_init_tables(cur)
        cur.execute('''
            INSERT INTO listing_agent_working_drafts (upc, draft_json, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(upc) DO UPDATE SET
                draft_json = excluded.draft_json,
                updated_at = excluded.updated_at
        ''', (upc, json.dumps(payload, ensure_ascii=False), now))


def _listagent_delete_working_draft(upc):
    upc = _listagent_format_upc12(upc)
    if not upc:
        return False
    with ss_database.db_connection('listagent.db') as conn:
        cur = conn.cursor()
        ss_listing_checks._listagent_init_tables(cur)
        cur.execute('DELETE FROM listing_agent_working_drafts WHERE upc = ? COLLATE NOCASE', (upc,))
        return cur.rowcount > 0


def _listagent_get_working_draft_statuses(upcs):
    requested = []
    seen = set()
    all_variants = []
    variant_to_requested = {}
    for raw in upcs or []:
        key = _listagent_format_upc12(raw)
        if not key or key in seen:
            continue
        seen.add(key)
        requested.append(key)
        for variant in _listagent_upc_variants(key):
            if variant not in variant_to_requested:
                variant_to_requested[variant] = []
            if key not in variant_to_requested[variant]:
                variant_to_requested[variant].append(key)
            if variant not in all_variants:
                all_variants.append(variant)
    if not requested or not all_variants:
        return {}
    placeholders = ','.join('?' for _ in all_variants)
    with ss_database.db_connection('listagent.db') as conn:
        cur = conn.cursor()
        ss_listing_checks._listagent_init_tables(cur)
        cur.execute(f'''
            SELECT upc
            FROM listing_agent_working_drafts
            WHERE upc IN ({placeholders})
        ''', tuple(all_variants))
        rows = cur.fetchall()
    found = set()
    for row in rows:
        try:
            found.add(_listagent_format_upc12(row['upc'] if isinstance(row, sqlite3.Row) else row[0]))
        except Exception:
            continue
    out = {}
    for row_upc in requested:
        if row_upc in found:
            out[row_upc] = True
            continue
        for variant in _listagent_upc_variants(row_upc):
            if variant in found:
                out[row_upc] = True
                break
    return out


def _listagent_localize_image_urls(upc, images, *, request_base_url=''):
    """
    Normalize image URLs for marketplace submission.

    - Keeps already-local/static URLs as-is.
    - Downloads remote URLs into static/listingagent_uploads and returns the
      public local URL so marketplace APIs can consume them.
    """
    from urllib.parse import urlparse
    from werkzeug.utils import secure_filename
    import mimetypes

    out = []
    seen = set()
    raw_list = images if isinstance(images, (list, tuple)) else [images]
    base = (request_base_url or (request.url_root or '')).rstrip('/')
    safe_upc = secure_filename(_listagent_format_upc12(upc) or ss_normalization._normalize_upc(upc) or 'upc') or 'upc'
    save_dir = os.path.join(ss_runtime.app.root_path, 'static', 'listingagent_uploads')
    os.makedirs(save_dir, exist_ok=True)

    for raw in raw_list:
        url = (raw or '').strip() if isinstance(raw, str) else ''
        if not url:
          continue

        normalized = url
        parsed = None
        try:
            parsed = urlparse(url)
        except Exception:
            parsed = None

        is_http = bool(parsed and parsed.scheme in ('http', 'https'))
        if not is_http:
            if url.startswith('/') and base:
                normalized = f"{base}{url}"
            elif url.startswith('//') and base:
                normalized = f"{parsed.scheme if parsed and parsed.scheme else 'https'}:{url}"
            if normalized not in seen:
                seen.add(normalized)
                out.append(normalized)
            continue

        host = (parsed.hostname or '').lower() if parsed else ''
        path = (parsed.path or '').lower() if parsed else ''
        local_host = ''
        try:
            local_host = (urlparse(base).hostname or '').lower()
        except Exception:
            local_host = ''

        if local_host and host == local_host and '/static/' in path:
            if normalized not in seen:
                seen.add(normalized)
                out.append(normalized)
            continue

        source_key = f"{safe_upc}|{url}"
        digest = hashlib.sha256(source_key.encode('utf-8')).hexdigest()[:18]
        ext = ''
        try:
            ext = os.path.splitext(parsed.path or '')[1].lower()
        except Exception:
            ext = ''
        if ext not in ('.jpg', '.jpeg', '.png', '.webp', '.gif', '.heic', '.heif'):
            ext = ''

        abs_filename = f"{safe_upc}_{digest}{ext or '.jpg'}"
        rel = f"listingagent_uploads/{abs_filename}"
        abs_path = os.path.join(save_dir, abs_filename)
        if not os.path.exists(abs_path):
            headers = {
                'User-Agent': 'Mozilla/5.0 (ListingAgent/1.0)',
                'Accept': 'image/*,*/*;q=0.8',
            }
            resp = requests.get(url, timeout=18, headers=headers)
            resp.raise_for_status()
            content_type = (resp.headers.get('content-type') or '').split(';', 1)[0].strip().lower()
            if not content_type.startswith('image/'):
                raise ValueError(f'URL did not return an image: {url}')
            with open(abs_path, 'wb') as fh:
                fh.write(resp.content)
            try:
                if os.path.getsize(abs_path) <= 0:
                    raise ValueError('downloaded file is empty')
            except Exception:
                raise
        if not _listagent_photo_path_exists(rel):
            size_bytes = None
            try:
                size_bytes = os.path.getsize(abs_path)
            except Exception:
                size_bytes = None
            _listagent_add_photo(
                upc,
                image_path=rel,
                original_filename=os.path.basename(parsed.path or url) or abs_filename,
                size_bytes=size_bytes
            )

        local_url = f"{base}{url_for('static', filename=rel)}" if base else url_for('static', filename=rel)
        if local_url not in seen:
            seen.add(local_url)
            out.append(local_url)

    return out


def _listagent_search_live_listings(q, *, limit=60):
    q = (q or '').strip()
    limit = int(limit or 60)
    limit = max(1, min(limit, 200))
    like = f"%{q}%"

    results = []

    # eBay live listings (from ebayStore.db)
    try:
        with ss_database.db_connection('ebayStore.db') as conn:
            cur = conn.cursor()
            cur.execute('''
                SELECT Title, ItemID, SKU, Price, Quantity, Image, URL, List_State, UPC, List_Date
                FROM INVENTORY
                WHERE List_State = 'Active'
                  AND (? = '' OR UPC LIKE ? OR Title LIKE ? OR SKU LIKE ? OR ItemID LIKE ?)
                ORDER BY List_Date DESC
                LIMIT ?
            ''', (q, like, like, like, like, limit))
            for r in cur.fetchall():
                results.append({
                    'platform': 'ebay',
                    'upc': (r['UPC'] or '').strip(),
                    'title': r['Title'] or '',
                    'sku': r['SKU'] or '',
                    'item_id': r['ItemID'] or '',
                    'price': r['Price'] or '',
                    'quantity': r['Quantity'] or '',
                    'image': r['Image'] or '',
                    'url': r['URL'] or '',
                    'state': r['List_State'] or '',
                    'last_updated': r['List_Date'] or '',
                })
    except Exception:
        pass

    # Amazon live listings (from amazonStore.db)
    try:
        with ss_database.db_connection('amazonStore.db') as conn:
            cur = conn.cursor()
            cur.execute('''
                SELECT ASIN, SKU, TITLE, PRICE, QUANTITY, STATUS, IMAGE, UPC, LAST_UPDATED
                FROM ITEMS
                WHERE STATUS = 'Active'
                  AND (? = '' OR UPC LIKE ? OR TITLE LIKE ? OR SKU LIKE ? OR ASIN LIKE ?)
                ORDER BY LAST_UPDATED DESC
                LIMIT ?
            ''', (q, like, like, like, like, limit))
            for r in cur.fetchall():
                asin = (r['ASIN'] or '').strip()
                results.append({
                    'platform': 'amazon',
                    'upc': (r['UPC'] or '').strip(),
                    'title': r['TITLE'] or '',
                    'sku': r['SKU'] or '',
                    'asin': asin,
                    'price': r['PRICE'],
                    'quantity': r['QUANTITY'],
                    'image': r['IMAGE'] or '',
                    'url': f"https://www.amazon.com/dp/{asin}" if asin else '',
                    'state': r['STATUS'] or '',
                    'last_updated': r['LAST_UPDATED'] or '',
                })
    except Exception:
        pass

    # Prefer matches that have UPC and tighter match on UPC, then title.
    ql = q.lower()
    def score(it):
        upc = (it.get('upc') or '').lower()
        title = (it.get('title') or '').lower()
        if not q:
            return (0, it.get('platform') or '', upc, title)
        if upc and upc.startswith(ql):
            return (0, it.get('platform') or '', upc, title)
        if upc and ql in upc:
            return (1, it.get('platform') or '', upc, title)
        if title and ql in title:
            return (2, it.get('platform') or '', upc, title)
        return (3, it.get('platform') or '', upc, title)

    results = sorted(results, key=score)[:limit]
    return results


def api_listingagent_photos_get():
    """Get uploaded listing photos for a UPC (from listagent.db)."""
    try:
        upc = (request.args.get('upc') or '').strip()
        if not upc:
            return jsonify({'success': False, 'error': 'upc is required'}), 400
        limit = ss_listing_settings._listingagent_parse_int(request.args.get('limit'), 30) or 30

        rows = _listagent_get_photos(upc, limit=limit)
        base = (request.url_root or '').rstrip('/')
        images = []
        items = []
        for r in rows:
            rel = (r.get('image_path') or '').strip()
            if not rel:
                continue
            url = f"{base}{url_for('static', filename=rel)}"
            images.append(url)
            items.append({
                'url': url,
                'created_at': r.get('created_at') or '',
                'original_filename': r.get('original_filename') or '',
                'size_bytes': r.get('size_bytes'),
            })

        return jsonify({'success': True, 'upc': upc, 'images': images, 'items': items})
    except Exception as e:
        return jsonify({'success': False, 'error': ss_errors._safe_error(e, 'listingagent:photos_get')}), 500


def api_listingagent_photos_upload():
    """Upload listing photos for a UPC. form-data: upc, files: photos[]"""
    try:
        upc = (request.form.get('upc') or request.args.get('upc') or '').strip()
        if not upc:
            return jsonify({'success': False, 'error': 'upc is required'}), 400

        from werkzeug.utils import secure_filename
        import uuid

        files = request.files.getlist('photos[]') or request.files.getlist('photos') or ([] if 'photo' not in request.files else [request.files['photo']])
        if not files:
            return jsonify({'success': False, 'error': 'No files uploaded'}), 400

        save_dir = os.path.join(ss_runtime.app.root_path, 'static', 'listingagent_uploads')
        os.makedirs(save_dir, exist_ok=True)

        base = (request.url_root or '').rstrip('/')
        saved_urls = []
        saved_count = 0
        safe_upc = secure_filename(ss_normalization._normalize_upc(upc)) or 'upc'

        for f in files:
            if not f or not getattr(f, 'filename', None):
                continue
            fn = secure_filename(f.filename)
            name, ext = os.path.splitext(fn)
            ext = (ext or '').lower()
            if ext not in ('.jpg', '.jpeg', '.png', '.webp', '.gif', '.heic', '.heif'):
                ext = '.jpg'
            # Keep filenames stable-ish but unique (UPC + timestamp + random).
            unique = f"{safe_upc}_{int(time.time()*1000)}_{uuid.uuid4().hex[:10]}{ext}"
            abs_path = os.path.join(save_dir, unique)
            f.save(abs_path)

            rel = f"listingagent_uploads/{unique}"
            size_bytes = None
            try:
                size_bytes = os.path.getsize(abs_path)
            except Exception:
                size_bytes = None

            _listagent_add_photo(upc, image_path=rel, original_filename=fn, size_bytes=size_bytes)

            url = f"{base}{url_for('static', filename=rel)}"
            saved_urls.append(url)
            saved_count += 1

        return jsonify({'success': True, 'upc': upc, 'saved': saved_urls, 'saved_count': saved_count})
    except Exception as e:
        return jsonify({'success': False, 'error': ss_errors._safe_error(e, 'listingagent:photos_upload')}), 500


def api_listingagent_live_listings_search():
    """Search live eBay/Amazon listings from local store DBs."""
    try:
        q = (request.args.get('q') or '').strip()
        limit = ss_listing_settings._listingagent_parse_int(request.args.get('limit'), 60) or 60
        results = _listagent_search_live_listings(q, limit=limit)
        return jsonify({'success': True, 'results': results})
    except Exception as e:
        return jsonify({'success': False, 'error': ss_errors._safe_error(e, 'listingagent:live_listings_search')}), 500


def api_listingagent_upc_search():
    """Typeahead for UPCs from searchRack.db and bol.db."""
    try:
        q = (request.args.get('q') or '').strip()
        limit = ss_listing_settings._listingagent_parse_int(request.args.get('limit'), 20) or 20
        limit = max(1, min(limit, 50))

        results_by_upc = {}

        like = f"%{q}%"
        with ss_database.db_connection('searchRack.db') as conn:
            cur = conn.cursor()
            cur.execute('''
                SELECT
                    TRIM(BARCODE) AS upc,
                    MAX(TITLE) AS title,
                    SUM(COALESCE(QUANTITY, 1)) AS quantity,
                    MAX(CREATED_AT) AS last_seen,
                    MAX(IMAGE) AS image
                FROM SEARCHRACK
                WHERE BARCODE IS NOT NULL AND TRIM(BARCODE) != ''
                  AND COALESCE(CAST(QUANTITY AS INTEGER), 0) > 0
                  AND (? = '' OR BARCODE LIKE ? OR TITLE LIKE ?)
                GROUP BY TRIM(BARCODE)
                ORDER BY MAX(CREATED_AT) DESC
                LIMIT ?
            ''', (q, like, like, limit))

            for row in cur.fetchall():
                upc = (row['upc'] or '').strip()
                if not upc:
                    continue
                results_by_upc[upc] = {
                    'upc': upc,
                    'title': row['title'] or '',
                    'quantity': int(row['quantity'] or 0),
                    'image': row['image'] or '',
                    'source': 'searchRack'
                }

        with ss_database.db_connection('bol.db') as conn:
            cur = conn.cursor()
            cur.execute('''
                SELECT
                    TRIM(upc) AS upc,
                    MAX(item_description) AS title,
                    MAX(image_url) AS image,
                    MAX(COALESCE(good_qty, quantity, original_qty, 1)) AS quantity,
                    MAX(import_date) AS last_seen
                FROM bol_items
                WHERE upc IS NOT NULL AND TRIM(upc) != ''
                  AND (? = '' OR upc LIKE ? OR item_description LIKE ?)
                GROUP BY TRIM(upc)
                ORDER BY MAX(import_date) DESC
                LIMIT ?
            ''', (q, like, like, limit))
            for row in cur.fetchall():
                upc = (row['upc'] or '').strip()
                if not upc:
                    continue
                if upc in results_by_upc:
                    # Fill missing title/image from BOL
                    if not results_by_upc[upc].get('title') and row['title']:
                        results_by_upc[upc]['title'] = row['title'] or ''
                    if not results_by_upc[upc].get('image') and row['image']:
                        results_by_upc[upc]['image'] = row['image'] or ''
                    continue
                results_by_upc[upc] = {
                    'upc': upc,
                    'title': row['title'] or '',
                    'quantity': int(row['quantity'] or 0),
                    'image': row['image'] or '',
                    'source': 'bol'
                }

        # Basic ordering: prioritize UPC prefix matches, then title matches
        def score(item):
            if not q:
                return (0, item.get('upc', ''))
            upc = item.get('upc', '')
            title = (item.get('title') or '').lower()
            ql = q.lower()
            if upc.startswith(q):
                return (0, upc)
            if q in upc:
                return (1, upc)
            if ql in title:
                return (2, upc)
            return (3, upc)

        results = sorted(results_by_upc.values(), key=score)[:limit]
        return jsonify({'success': True, 'results': results})
    except Exception as e:
        return jsonify({'success': False, 'error': ss_errors._safe_error(e, 'listingagent:upc_search')}), 500


def api_listingagent_upc_detail(upc):
    """Aggregate item info by UPC across local DBs to help auto-fill listings."""
    try:
        upc = (upc or '').strip()
        if not upc:
            return jsonify({'success': False, 'error': 'UPC is required'}), 400

        item = {
            'upc': upc,
            'title': '',
            'images': [],
            'defect': '',
            'inventory': {
                'total_quantity': 0,
                'rows': [],
                'positions': []
            },
            'bol': None,
            'ebay_store': {
                'listings': []
            },
            'amazon_store': {
                'listings': []
            },
            'sold_stats': None
        }

        # Build UPC lookup variants to handle:
        # - suffixed UPCs (e.g. "123456789012-1" → base "123456789012")
        # - leading-zero padding differences ("035886267162" vs "35886267162")
        _upc_base = upc.split('-', 1)[0].strip() if '-' in upc else upc
        _upc_base_stripped = _upc_base.lstrip('0') or _upc_base  # "35886267162"
        _upc_base_padded = _upc_base.zfill(12) if _upc_base.isdigit() else _upc_base  # "035886267162"
        item['base_upc'] = _upc_base
        item['items_to_list_url'] = ss_warehouse_allocations._items_prep_items_to_list_url(_upc_base or upc)
        # Unique ordered variants: exact, padded, stripped (all base, no suffix for store lookups)
        def _upc_lookup_variants(*extras):
            seen = []
            for v in [upc, _upc_base, _upc_base_padded, _upc_base_stripped] + list(extras):
                v = (v or '').strip()
                if v and v not in seen:
                    seen.append(v)
            return seen
        with ss_database.db_connection('searchRack.db') as conn:
            cur = conn.cursor()
            _inv_variants = _upc_lookup_variants()
            _inv_placeholders = ','.join('?' for _ in _inv_variants)
            if '-' in upc:
                # Suffixed UPC: match exact variants only
                cur.execute(f'''
                    SELECT ID, TITLE, ITEM_POSITION, PICTUREPOSITION, QUANTITY, IMAGE, IMAGES, ITEMID, CREATED_AT
                    FROM SEARCHRACK
                    WHERE TRIM(BARCODE) COLLATE NOCASE IN ({_inv_placeholders})
                      AND COALESCE(CAST(QUANTITY AS INTEGER), 0) > 0
                    ORDER BY CREATED_AT DESC
                ''', _inv_variants)
            else:
                # Base UPC: match padded/stripped variants, but exclude any suffixed rows
                cur.execute(f'''
                    SELECT ID, TITLE, ITEM_POSITION, PICTUREPOSITION, QUANTITY, IMAGE, IMAGES, ITEMID, CREATED_AT
                    FROM SEARCHRACK
                    WHERE TRIM(BARCODE) COLLATE NOCASE IN ({_inv_placeholders})
                      AND TRIM(BARCODE) NOT LIKE '%-_%' COLLATE NOCASE
                      AND COALESCE(CAST(QUANTITY AS INTEGER), 0) > 0
                    ORDER BY CREATED_AT DESC
                ''', _inv_variants)
            rows = cur.fetchall()

            total_qty = 0
            positions = []
            for r in rows:
                qty = int(r['QUANTITY'] or 0) if r['QUANTITY'] is not None else 0
                total_qty += qty
                pos = (r['ITEM_POSITION'] or '').strip()
                if pos:
                    positions.append(pos)

                for img in [r['IMAGE'], r['IMAGES']]:
                    if not img:
                        continue
                    s = str(img).strip()
                    if not s:
                        continue
                    if s not in item['images']:
                        item['images'].append(s)

            item['inventory']['total_quantity'] = total_qty
            item['inventory']['positions'] = sorted(set([p for p in positions if p]))
            item['inventory']['rows'] = [{
                'id': r['ID'],
                'title': r['TITLE'] or '',
                'position': r['ITEM_POSITION'] or '',
                'picturePosition': r['PICTUREPOSITION'] or '',
                'quantity': r['QUANTITY'] if r['QUANTITY'] is not None else 1,
                'image': r['IMAGE'] or '',
                'createdAt': r['CREATED_AT'] or ''
            } for r in rows[:50]]

            # Prefer the most recent title
            for r in rows:
                if r['TITLE']:
                    item['title'] = r['TITLE']
                    break

        # rawbol.db — primary source for title + thumbnail; always use base barcode.
        _rawbol_variants = [_upc_base_padded, _upc_base_stripped, _upc_base]
        _rawbol_variants = list(dict.fromkeys(v for v in _rawbol_variants if v))  # dedupe, preserve order
        try:
            with ss_database.db_connection('rawbol.db') as conn:
                cur = conn.cursor()
                cur.execute('''
                    SELECT item_description, image_url, prep_reason
                    FROM raw_bol_items
                    WHERE TRIM(upc) IN ({})
                    ORDER BY import_date DESC, id DESC
                    LIMIT 1
                '''.format(','.join('?' for _ in _rawbol_variants)),
                tuple(_rawbol_variants))
                raw_row = cur.fetchone()
                if raw_row:
                    if not item['title'] and (raw_row['item_description'] or '').strip():
                        item['title'] = raw_row['item_description'].strip()
                    img = (raw_row['image_url'] or '').strip()
                    if img and img not in item['images']:
                        item['images'].insert(0, img)  # rawbol image first
                    item['defect'] = (raw_row['prep_reason'] or '').strip()
        except Exception:
            pass

        # BOL info (bol.db — processed BOL items with prep quantities)
        with ss_database.db_connection('bol.db') as conn:
            cur = conn.cursor()
            cur.execute('''
                SELECT item_description, image_url, lot_number, import_date,
                       good_qty, bad_qty, unchecked_qty, quantity,
                       listed_ebay, listed_ebay_date
                FROM bol_items
                WHERE TRIM(upc) IN ({})
                ORDER BY import_date DESC
                LIMIT 1
            '''.format(','.join('?' for _ in _upc_lookup_variants())),
            tuple(_upc_lookup_variants()))
            bol_row = cur.fetchone()
            if bol_row:
                item['bol'] = {
                    'description': bol_row['item_description'] or '',
                    'image_url': bol_row['image_url'] or '',
                    'lot_number': bol_row['lot_number'] or '',
                    'import_date': bol_row['import_date'] or '',
                    'good_qty': bol_row['good_qty'],
                    'bad_qty': bol_row['bad_qty'],
                    'unchecked_qty': bol_row['unchecked_qty'],
                    'quantity': bol_row['quantity'],
                    'listed_ebay': bol_row['listed_ebay'],
                    'listed_ebay_date': bol_row['listed_ebay_date'] or ''
                }
                if not item['title'] and item['bol']['description']:
                    item['title'] = item['bol']['description']
                if item['bol']['image_url'] and item['bol']['image_url'] not in item['images']:
                    item['images'].append(item['bol']['image_url'])

        # ebayStore existing listings
        _ev = _upc_lookup_variants()
        with ss_database.db_connection('ebayStore.db') as conn:
            cur = conn.cursor()
            cur.execute('''
                SELECT Title, ItemID, SKU, Price, Quantity, Image, URL, List_State, Sold_Date, List_Date
                FROM INVENTORY
                WHERE TRIM(UPC) IN ({})
                ORDER BY List_Date DESC
                LIMIT 10
            '''.format(','.join('?' for _ in _ev)),
            tuple(_ev))
            for r in cur.fetchall():
                listing = {
                    'title': r['Title'] or '',
                    'item_id': r['ItemID'] or '',
                    'sku': r['SKU'] or '',
                    'price': r['Price'] or '',
                    'quantity': r['Quantity'] or '',
                    'image': r['Image'] or '',
                    'url': r['URL'] or '',
                    'state': r['List_State'] or '',
                    'sold_date': r['Sold_Date'] or '',
                    'list_date': r['List_Date'] or ''
                }
                item['ebay_store']['listings'].append(listing)
                if listing.get('image') and listing['image'] not in item['images']:
                    item['images'].append(listing['image'])

        # amazonStore existing listings
        try:
            _av = _upc_lookup_variants()
            with ss_database.db_connection('amazonStore.db') as conn:
                cur = conn.cursor()
                cur.execute('''
                    SELECT ASIN, SKU, TITLE, PRICE, QUANTITY, STATUS, IMAGE, UPC, CONDITION, FULFILLMENT_CHANNEL, LAST_UPDATED
                    FROM ITEMS
                    WHERE TRIM(UPC) IN ({})
                    ORDER BY LAST_UPDATED DESC
                    LIMIT 10
                '''.format(','.join('?' for _ in _av)),
                tuple(_av))
                for r in cur.fetchall():
                    asin = (r['ASIN'] or '').strip()
                    listing = {
                        'asin': asin,
                        'sku': r['SKU'] or '',
                        'title': r['TITLE'] or '',
                        'price': r['PRICE'],
                        'quantity': r['QUANTITY'],
                        'status': r['STATUS'] or '',
                        'image': r['IMAGE'] or '',
                        'condition': r['CONDITION'] or '',
                        'fulfillment_channel': r['FULFILLMENT_CHANNEL'] or '',
                        'last_updated': r['LAST_UPDATED'] or '',
                        'product_url': f"https://www.amazon.com/dp/{asin}" if asin else ''
                    }
                    item['amazon_store']['listings'].append(listing)
                    if listing.get('image') and listing['image'] not in item['images']:
                        item['images'].append(listing['image'])
                    if not item['title'] and listing.get('title'):
                        item['title'] = listing['title']
        except Exception:
            # amazonStore.db may not exist in some environments; ignore.
            pass

        # Listing Manager (Item Prep) photos + notes (bol.db items_prep_*) — useful for auto-filling listing images and showing notes.
        item['prep'] = {'notes': [], 'images': [], 'voice_notes': []}
        try:
            ss_prep_schema._ensure_items_prep_tables()

            _upc_is_suffixed = '-' in upc
            base_upc = (upc.split('-', 1)[0] if _upc_is_suffixed else upc).strip()
            base_upc = ss_normalization._strip_leading_zeros_numeric(base_upc)

            # When the requested UPC has a suffix (e.g. "123456789012-1"), only pull
            # notes/images/audio for that exact suffixed UPC — not all siblings.
            # When there's no suffix, pull base UPC + all suffixed variants (broad view).
            if _upc_is_suffixed:
                # Build padding variants of the suffixed UPC itself
                _suffix_part = upc.split('-', 1)[1]
                _suffixed_padded = f"{_upc_base_padded}-{_suffix_part}"
                _suffixed_stripped = f"{_upc_base_stripped}-{_suffix_part}"
                _prep_exact_variants = list(dict.fromkeys(
                    v for v in [upc, _suffixed_padded, _suffixed_stripped] if v
                ))
                _prep_placeholders = ','.join('?' for _ in _prep_exact_variants)
                def _prep_where_clause():
                    return f'upc COLLATE NOCASE IN ({_prep_placeholders})'
                def _prep_params():
                    return _prep_exact_variants
            else:
                # Base barcode: exact match only — do NOT pull suffixed siblings' notes/media.
                # Each suffix is its own distinct product.
                def _prep_where_clause():
                    return 'upc = ? COLLATE NOCASE'
                def _prep_params():
                    return [base_upc]

            prep_notes = []
            prep_images = []
            prep_audio_rows = []
            prep_video_rows = []

            if base_upc:
                with ss_database.db_connection('bol.db') as conn:
                    cur = conn.cursor()

                    # Notes (free-form) from Listing Manager / Item Prep
                    cur.execute(f'''
                        SELECT upc, id, note, created_at
                        FROM items_prep_notes
                        WHERE {_prep_where_clause()}
                        ORDER BY created_at DESC, id DESC
                        LIMIT 25
                    ''', _prep_params())
                    prep_notes = [dict(r) for r in cur.fetchall()]

                    # Fallback: if no note bubbles exist, use the status.note (single field) if present.
                    if not prep_notes:
                        cur.execute(f'''
                            SELECT upc, note, updated_at
                            FROM items_prep_status
                            WHERE ({_prep_where_clause()})
                              AND note IS NOT NULL
                              AND TRIM(note) != ''
                            ORDER BY COALESCE(updated_at, '') DESC
                            LIMIT 1
                        ''', _prep_params())
                        sn = cur.fetchone()
                        if sn and (sn['note'] or '').strip():
                            prep_notes = [{
                                'upc': sn['upc'],
                                'id': None,
                                'note': sn['note'],
                                'created_at': sn['updated_at'] or ''
                            }]

                    # Photos from Listing Manager / Item Prep
                    cur.execute(f'''
                        SELECT upc, id, image_path, created_at, rotation
                        FROM items_prep_images
                        WHERE ({_prep_where_clause()})
                          AND (deleted_at IS NULL OR TRIM(COALESCE(deleted_at,'')) = '')
                        ORDER BY created_at DESC, id DESC
                        LIMIT 30
                    ''', _prep_params())
                    prep_images = [dict(r) for r in cur.fetchall()]

                    # Voice notes (audio) from items_prep_media
                    try:
                        cur.execute(f'''
                            SELECT id, upc, COALESCE(row_status,'') AS row_status,
                                   media_type, file_path, COALESCE(mime_type,'') AS mime_type, created_at
                            FROM items_prep_media
                            WHERE ({_prep_where_clause()})
                              AND COALESCE(media_type,'') = 'audio'
                            ORDER BY created_at DESC, id DESC
                            LIMIT 10
                        ''', _prep_params())
                        prep_audio_rows = [dict(r) for r in cur.fetchall()]
                    except Exception:
                        prep_audio_rows = []

                    try:
                        cur.execute(f'''
                            SELECT id, upc, COALESCE(row_status,'') AS row_status,
                                   media_type, file_path, COALESCE(mime_type,'') AS mime_type, created_at
                            FROM items_prep_media
                            WHERE ({_prep_where_clause()})
                              AND COALESCE(media_type,'') = 'video'
                            ORDER BY created_at DESC, id DESC
                            LIMIT 10
                        ''', _prep_params())
                        prep_video_rows = [dict(r) for r in cur.fetchall()]
                    except Exception:
                        prep_video_rows = []

                base_url = (request.url_root or '').rstrip('/')
                prep_urls = []
                for pr in prep_images:
                    rel = (pr.get('image_path') or '').strip()
                    if not rel:
                        continue
                    url = f"{base_url}{url_for('static', filename=rel)}"
                    if url and url not in prep_urls:
                        prep_urls.append(url)

                prep_voice_notes = []
                for ar in prep_audio_rows:
                    rel = (ar.get('file_path') or '').strip()
                    if not rel:
                        continue
                    url = f"{base_url}{url_for('static', filename=rel)}"
                    prep_voice_notes.append({
                        'id': ar.get('id'),
                        'upc': ar.get('upc', ''),
                        'url': url,
                        'mime_type': ar.get('mime_type', ''),
                        'created_at': ar.get('created_at', '')
                    })

                prep_videos = []
                for vr in prep_video_rows:
                    rel = (vr.get('file_path') or '').strip()
                    if not rel:
                        continue
                    url = f"{base_url}{url_for('static', filename=rel)}"
                    prep_videos.append({
                        'id': vr.get('id'),
                        'upc': vr.get('upc', ''),
                        'url': url,
                        'mime_type': vr.get('mime_type', ''),
                        'created_at': vr.get('created_at', '')
                    })

                if prep_urls:
                    # Prefer prep photos ahead of marketplace images, but keep existing order otherwise.
                    merged = []
                    for u in prep_urls + (item.get('images') or []):
                        if not u:
                            continue
                        if u not in merged:
                            merged.append(u)
                    item['images'] = merged

                item['prep'] = {'notes': prep_notes, 'images': prep_urls, 'voice_notes': prep_voice_notes, 'videos': prep_videos}
        except Exception:
            # Item prep tables may not exist in some envs; ignore.
            item['prep'] = {'notes': [], 'images': [], 'voice_notes': [], 'videos': []}

        # Checker requests, notes, and media (listagent.db)
        try:
            checker_req = ss_listing_checks._listagent_get_check_request(upc)
            checker_notes = ss_listing_checks._listagent_get_check_notes(upc)
            checker_media_rows = ss_listing_checks._listagent_get_check_media(upc)
            base = (request.url_root or '').rstrip('/')
            checker_media = []
            for mr in checker_media_rows:
                rel = (mr.get('file_path') or '').strip()
                if not rel:
                    continue
                checker_media.append({
                    'id': mr.get('id'),
                    'upc': mr.get('upc') or '',
                    'url': f"{base}{url_for('static', filename=rel)}",
                    'file_path': rel,
                    'media_type': (mr.get('media_type') or 'image').strip().lower() or 'image',
                    'original_filename': mr.get('original_filename') or '',
                    'size_bytes': mr.get('size_bytes'),
                    'note_type': (mr.get('note_type') or 'checker').strip().lower() or 'checker',
                    'created_at': mr.get('created_at') or ''
                })
            item['checker'] = {
                'request': checker_req,
                'notes': checker_notes,
                'media': checker_media
            }
        except Exception:
            item['checker'] = {'request': None, 'notes': [], 'media': []}

        # Listing Agent uploaded photos (from listagent.db) — prefer these first.
        try:
            photo_rows = _listagent_get_photos(upc, limit=30)
            base = (request.url_root or '').rstrip('/')
            has_macy_image = any(ss_bulk_manifest._listagent_is_macy_image_url(img, origin_base=base) for img in (item.get('images') or []))
            uploaded_urls = []
            for pr in photo_rows:
                rel = (pr.get('image_path') or '').strip()
                if not rel:
                    continue
                url = f"{base}{url_for('static', filename=rel)}"
                if url and url not in item['images']:
                    uploaded_urls.append(url)
            if uploaded_urls:
                if has_macy_image:
                    item['images'] = item['images'] + uploaded_urls
                else:
                    item['images'] = uploaded_urls + item['images']
        except Exception:
            pass

        try:
            base = (request.url_root or '').rstrip('/')
            item['images'] = ss_bulk_manifest._listagent_prioritize_macy_thumbnail(item.get('images') or [], origin_base=base)
        except Exception:
            pass

        # sold stats (optional)
        with ss_database.db_connection('sold.db') as conn:
            cur = conn.cursor()
            cur.execute('''
                SELECT COUNT(*) AS cnt,
                       AVG(price) AS avg_price,
                       MIN(price) AS min_price,
                       MAX(price) AS max_price,
                       MAX(paid_time) AS last_sold
                FROM orders
                WHERE TRIM(barcode) = ? COLLATE NOCASE
                  AND price IS NOT NULL
                  AND price > 0
            ''', (upc,))
            r = cur.fetchone()
            if r and (r['cnt'] or 0) > 0:
                item['sold_stats'] = {
                    'count': int(r['cnt'] or 0),
                    'avg_price': float(r['avg_price'] or 0),
                    'min_price': float(r['min_price'] or 0),
                    'max_price': float(r['max_price'] or 0),
                    'last_sold': r['last_sold'] or ''
                }

        # Prep qty from items-to-list (matches what /items-to-list page shows)
        try:
            prep_stats = ss_shipping_identity._ready_to_ship_items_to_list_stats_for_barcode(upc)
            item['prep_qty'] = prep_stats.get('prepped_qty', 0)
        except Exception:
            item['prep_qty'] = None

        return jsonify({'success': True, 'item': item})
    except Exception as e:
        return jsonify({'success': False, 'error': ss_errors._safe_error(e, 'listingagent:upc_detail')}), 500
