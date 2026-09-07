"""Prep diagnostics for Sweet Shelves."""

import datetime
import os
import sqlite3
import time
from flask import jsonify, request
from . import (
    caching as ss_caching, database as ss_database, errors as ss_errors, listing_checks as
    ss_listing_checks, listing_lifecycle as ss_listing_lifecycle, listing_log as ss_listing_log,
    listing_settings as ss_listing_settings, normalization as ss_normalization, prep_log as ss_prep_log,
    prep_media as ss_prep_media, prep_schema as ss_prep_schema, runtime as ss_runtime,
    warehouse_allocations as ss_warehouse_allocations,
)


def api_items_prep_diagnostic():
    """Save diagnostic info and photos for a UPC. form-data: upc, reason, note, files: photos[]"""
    conn_temp = None
    try:
        ss_prep_schema._ensure_items_prep_tables()
        upc_raw = ss_normalization._normalize_upc(request.form.get('upc'))
        # Strip leading zeros ONLY if it's all digits (preserve suffix like -24)
        upc = ss_normalization._normalize_upc_preserve_suffix_for_match(upc_raw)
        reason = (request.form.get('reason') or '').strip()
        note = (request.form.get('note') or '').strip()
        if not upc:
            return jsonify({'success': False, 'error': 'Missing upc'}), 400

        # DEBUG: Log incoming request details
        print(f'[DEBUG] Diagnostic upload for UPC: {upc_raw} -> {upc}')
        print(f'[DEBUG] Form keys: {list(request.form.keys())}')
        print(f'[DEBUG] Files keys: {list(request.files.keys())}')
        
        # Mark the target diagnostic row as permanent (no qty changes needed here).
        conn_temp = sqlite3.connect('bol.db', isolation_level='IMMEDIATE')
        cur_temp = conn_temp.cursor()
        selected_lot = ss_warehouse_allocations._preferred_lot_from_request()
        if selected_lot:
            cur_temp.execute('''
                SELECT id, temporary, COALESCE(lot_number, '')
                FROM bol_items
                WHERE upc = ? COLLATE NOCASE
                  AND lot_number = ? COLLATE NOCASE
                ORDER BY import_date DESC, id DESC
                LIMIT 1
            ''', (upc, selected_lot))
            temp_row = cur_temp.fetchone()
        else:
            cur_temp.execute('''
                SELECT id, temporary, COALESCE(lot_number, '')
                FROM bol_items
                WHERE upc = ? COLLATE NOCASE
                ORDER BY import_date DESC, id DESC
                LIMIT 1
            ''', (upc,))
            temp_row = cur_temp.fetchone()

        if temp_row:
            target_id = int(temp_row[0])
            is_temporary = int(temp_row[1] or 0) == 1
            target_lot = ss_normalization._normalize_lot_number(temp_row[2])
            cur_temp.execute('UPDATE bol_items SET temporary = 0 WHERE id = ?', (target_id,))
            conn_temp.commit()
            if is_temporary:
                print(f'[DIAGNOSTIC COMPLETE] Set {upc} (id={target_id}, lot={target_lot or "(none)"}) to permanent')
                print(f'[DIAGNOSTIC COMPLETE] Base qty already decremented on bad entry creation - no further changes needed')

        # save files under static/items_prep
        from werkzeug.utils import secure_filename
        save_dir = os.path.join(ss_runtime.app.root_path, 'static', 'items_prep')
        os.makedirs(save_dir, exist_ok=True)
        saved = []
        files = request.files.getlist('photos[]') or request.files.getlist('photos') or ([] if 'photo' not in request.files else [request.files['photo']])
        # optional per-file rotations can be provided as rotations[] in the same form-data (one per file, same order)
        rotations = request.form.getlist('rotations[]') or request.form.getlist('rotations') or []
        print(f'[DEBUG] Files received: {len(files)}')
        print(f'[DEBUG] Rotations received: {len(rotations)}')
        import datetime
        ts = datetime.datetime.now(datetime.UTC).isoformat()
        if files:
            conn_i = sqlite3.connect('bol.db')
            try:
                cur_i = conn_i.cursor()
                for idx, f in enumerate(files):
                    if not f or not getattr(f, 'filename', ''):
                        print(f'[DEBUG] Skipping invalid file at index {idx}')
                        continue
                    fn = secure_filename(f.filename)
                    name, ext = os.path.splitext(fn)
                    unique = f"{ss_normalization._normalize_upc(upc)}_{int(time.time()*1000)}{ext or '.jpg'}"
                    path = os.path.join(save_dir, unique)
                    try:
                        f.save(path)
                        rel = f"items_prep/{unique}"
                        # parse rotation for this file (if provided), default to 0
                        rot = 0
                        try:
                            if idx < len(rotations):
                                rot = int(rotations[idx] or 0)
                        except Exception:
                            rot = 0
                        cur_i.execute('INSERT INTO items_prep_images (upc, image_path, created_at, rotation) VALUES (?,?,?,?)', (upc, rel, ts, rot))
                        saved.append(rel)
                        print(f'[DEBUG] Successfully saved photo {idx+1}: {rel}')
                    except Exception as se:
                        print(f'[DEBUG] Failed to save diagnostic image {idx}:', se)
                conn_i.commit()
            finally:
                conn_i.close()
            print(f'[DEBUG] Total photos saved to database: {len(saved)}')
        # Note: Status is now handled by /api/items_prep/status endpoint which properly handles suffixed UPCs
        # Do not update status here as it would overwrite base barcode status incorrectly

        # Update data version for cache invalidation
        ss_caching.update_data_version()

        return jsonify({'success': True, 'saved': saved})
    except Exception as e:
        try:
            import traceback
            print('Diagnostic upload error:', e)
            traceback.print_exc()
        except Exception:
            pass
        return jsonify({'success': False, 'error': ss_errors._safe_error(e)}), 500
    finally:
        if conn_temp is not None:
            conn_temp.close()




def api_items_prep_diagnostic_get(upc):
    conn = None
    try:
        upc_raw = ss_normalization._normalize_upc(upc)
        # Strip leading zeros from numeric base while preserving suffix (e.g. 071..-4 -> 71..-4)
        upc_n = ss_normalization._normalize_upc_preserve_suffix_for_match(upc_raw)
        lot_number = ss_warehouse_allocations._preferred_lot_from_request()
        row_status = ss_normalization._normalize_prep_row_status(request.args.get('row_status') or request.args.get('status'))
        include_related_arg = request.args.get('include_related')
        include_related = True if include_related_arg is None else ss_normalization._coerce_bool(include_related_arg)
        exact_scope = ss_normalization._coerce_bool(request.args.get('exact_scope'))
        is_suffixed_upc = ('-' in upc_n and upc_n.rsplit('-', 1)[-1].isdigit())
        if exact_scope or is_suffixed_upc:
            include_related = False
        include_bol_image = ss_normalization._coerce_bool(request.args.get('include_bol_image'))
        try:
            max_images = int(request.args.get('max_images') or 0)
        except Exception:
            max_images = 0
        max_images = max(0, min(max_images, 50))
        print(f'[DEBUG] Getting diagnostic for UPC: {upc} -> {upc_raw} -> {upc_n}')
        ss_prep_schema._ensure_items_prep_tables()
        conn = sqlite3.connect('bol.db')
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        srow = ss_warehouse_allocations._select_prep_status_row(cur, upc_n, lot_number, columns='status, reason, note, updated_at, lot_number, quantity')
        image_lookup = 'exact'
        prefer_bol_image = ss_normalization._coerce_bool(request.args.get('prefer_bol_image'))

        # Try exact UPC first, then compatible normalizations used by older codepaths.
        candidate_upcs = []
        for cand in (upc_n, upc_raw, ss_normalization._strip_leading_zeros_numeric(upc_raw), str(upc or '').strip()):
            cand_n = ss_normalization._normalize_upc(cand)
            if cand_n and cand_n not in candidate_upcs:
                candidate_upcs.append(cand_n)

        related_bases = []
        for source in (upc_n, upc_raw, str(upc or '').strip()):
            source = ss_normalization._normalize_upc(source)
            if not source:
                continue
            base = source.split('-', 1)[0] if '-' in source else source
            base = ss_normalization._strip_leading_zeros_numeric(base)
            if base and base not in related_bases:
                related_bases.append(base)

        def _collect_manifest_rows(table_name):
            rows = []
            seen_ids = set()
            try:
                cur.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name = ? LIMIT 1", (table_name,))
                if not cur.fetchone():
                    return rows
            except Exception:
                return rows

            def _append(sql, params):
                try:
                    cur.execute(sql, params)
                    for rr in cur.fetchall():
                        rid = rr['id']
                        if rid in seen_ids:
                            continue
                        seen_ids.add(rid)
                        rows.append(dict(rr))
                except Exception:
                    return

            for cand in candidate_upcs:
                if lot_number:
                    _append(f'''
                        SELECT id, upc, image_url, import_date
                        FROM {table_name}
                        WHERE upc = ? COLLATE NOCASE
                          AND lot_number = ? COLLATE NOCASE
                          AND TRIM(COALESCE(image_url, '')) <> ''
                        ORDER BY import_date DESC, id DESC
                        LIMIT 5
                    ''', (cand, lot_number))
                _append(f'''
                    SELECT id, upc, image_url, import_date
                    FROM {table_name}
                    WHERE upc = ? COLLATE NOCASE
                      AND TRIM(COALESCE(image_url, '')) <> ''
                    ORDER BY import_date DESC, id DESC
                    LIMIT 5
                ''', (cand,))

            if include_related:
                for base in related_bases:
                    safe_base = base.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')
                    like_base = f"{safe_base}-%"
                    if lot_number:
                        _append(f'''
                            SELECT id, upc, image_url, import_date
                            FROM {table_name}
                            WHERE (upc = ? COLLATE NOCASE OR upc LIKE ? ESCAPE '\\')
                              AND lot_number = ? COLLATE NOCASE
                              AND TRIM(COALESCE(image_url, '')) <> ''
                            ORDER BY import_date DESC, id DESC
                            LIMIT 10
                        ''', (base, like_base, lot_number))
                    _append(f'''
                        SELECT id, upc, image_url, import_date
                        FROM {table_name}
                        WHERE (upc = ? COLLATE NOCASE OR upc LIKE ? ESCAPE '\\')
                          AND TRIM(COALESCE(image_url, '')) <> ''
                        ORDER BY import_date DESC, id DESC
                        LIMIT 10
                    ''', (base, like_base))

            return rows

        def _rows_to_images(rows, source_label):
            seen_urls = set()
            out = []
            for rr in rows:
                raw_url = str(rr.get('image_url') or '').strip()
                if not raw_url:
                    continue
                key = raw_url.lower()
                if key in seen_urls:
                    continue
                seen_urls.add(key)
                out.append({
                    'id': f"{source_label}-{rr.get('id')}",
                    'image_path': raw_url,
                    'created_at': rr.get('import_date') or '',
                    'rotation': 0,
                    'source': source_label
                })
            return out

        def _collect_manifest_images():
            raw_rows = _collect_manifest_rows('raw_bol_items')
            raw_images = _rows_to_images(raw_rows, 'raw_bol_items')
            if raw_images:
                return raw_images, 'rawbol_image'

            bol_rows = _collect_manifest_rows('bol_items')
            bol_images = _rows_to_images(bol_rows, 'bol_items')
            if bol_images:
                return bol_images, 'bol_image'
            return [], ''

        images = []
        if include_bol_image and prefer_bol_image:
            manifest_images, manifest_lookup = _collect_manifest_images()
            if manifest_images:
                images = manifest_images
                image_lookup = manifest_lookup

        if not images:
            asset_scope_upc, asset_scope_status = ss_normalization._items_to_list_asset_scope(upc_n, row_status)
            for cand in candidate_upcs:
                if exact_scope or row_status or ('-' in asset_scope_upc and cand.lower() == asset_scope_upc.lower()):
                    scope_upc, scope_status = ss_normalization._items_to_list_asset_scope(cand, row_status)
                    cur.execute(
                        "SELECT id, image_path, created_at, rotation, COALESCE(row_status, '') AS row_status FROM items_prep_images "
                        "WHERE upc = ? COLLATE NOCASE "
                        "AND COALESCE(row_status, '') = ? COLLATE NOCASE "
                        "AND (deleted_at IS NULL OR TRIM(COALESCE(deleted_at,'')) = '') "
                        "ORDER BY created_at DESC, id DESC",
                        (scope_upc, scope_status)
                    )
                else:
                    cur.execute(
                        "SELECT id, image_path, created_at, rotation, COALESCE(row_status, '') AS row_status FROM items_prep_images "
                        "WHERE upc = ? COLLATE NOCASE "
                        "AND (deleted_at IS NULL OR TRIM(COALESCE(deleted_at,'')) = '') "
                        "ORDER BY created_at DESC, id DESC",
                        (cand,)
                    )
                rows = [dict(r) for r in cur.fetchall()]
                if rows:
                    images = rows
                    if cand != upc_n:
                        image_lookup = 'variant'
                    break

        # Optional log-mode fallback: if exact/variant not found, include base and suffixed UPC rows.
        if include_related and not images:
            related_rows = []
            for base in related_bases:
                safe_base = base.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')
                like_base = f"{safe_base}-%"
                cur.execute(
                    "SELECT id, image_path, created_at, rotation FROM items_prep_images "
                    "WHERE (upc = ? COLLATE NOCASE OR upc LIKE ? ESCAPE '\\') "
                    "AND (deleted_at IS NULL OR TRIM(COALESCE(deleted_at,'')) = '') "
                    "ORDER BY created_at DESC, id DESC",
                    (base, like_base)
                )
                related_rows.extend([dict(r) for r in cur.fetchall()])

            if related_rows:
                # Preserve newest-first ordering and avoid duplicates across multiple base candidates.
                seen = set()
                deduped = []
                for row in related_rows:
                    rid = row.get('id')
                    if rid in seen:
                        continue
                    seen.add(rid)
                    deduped.append(row)
                images = deduped
                image_lookup = 'related'

        # Optional fallback for log/modal usage: if no diagnostic photos exist,
        # surface manifest image_url (raw_bol_items first, then bol_items).
        if include_bol_image and not images:
            manifest_images, manifest_lookup = _collect_manifest_images()
            if manifest_images:
                images = manifest_images
                image_lookup = manifest_lookup

        if max_images > 0 and len(images) > max_images:
            images = images[:max_images]

        print(f'[DEBUG] Found {len(images)} images for UPC {upc_n} (lookup={image_lookup}, include_related={include_related})')
        return jsonify({
            'upc': upc_n,
            'status': dict(srow) if srow else None,
            'images': images,
            'image_lookup': image_lookup
        })
    except Exception as e:
        return jsonify({'error': ss_errors._safe_error(e)}), 500
    finally:
        if conn is not None:
            conn.close()


def api_items_prep_diagnostic_live_listings(upc):
    """Return live marketplace listings (active/listed) for one diagnostic UPC."""
    try:
        upc_raw = ss_normalization._normalize_upc(upc)
        upc_n = ss_normalization._normalize_upc_preserve_suffix_for_match(upc_raw)
        if not upc_n:
            return jsonify({'success': False, 'error': 'Missing UPC'}), 400

        listing_map = ss_listing_lifecycle._fetch_auto_marketplace_listing_links(
            [upc_n],
            max_lookup_keys=4000,
            max_links_per_platform=80
        ) or {}

        seen = set()
        listings = []
        ebay_count = 0
        amazon_count = 0

        for key in ss_listing_lifecycle._marketplace_upc_lookup_variants(upc_n):
            bucket = listing_map.get(key) or {}
            for platform in ('ebay', 'amazon'):
                for entry in (bucket.get(platform) or []):
                    listing_id = str(entry.get('listing_id') or '').strip()
                    listing_url = str(entry.get('url') or '').strip()
                    token = f"{platform}|{listing_id.lower()}|{listing_url.lower()}"
                    if token in seen:
                        continue
                    seen.add(token)
                    row = {
                        'platform': platform,
                        'listing_id': listing_id,
                        'title': str(entry.get('title') or '').strip(),
                        'url': listing_url
                    }
                    listings.append(row)
                    if platform == 'ebay':
                        ebay_count += 1
                    elif platform == 'amazon':
                        amazon_count += 1

        listings.sort(
            key=lambda x: (
                0 if (x.get('platform') or '') == 'ebay' else 1,
                (x.get('listing_id') or '').lower(),
                (x.get('url') or '').lower()
            )
        )

        return jsonify({
            'success': True,
            'upc': upc_n,
            'total_count': ebay_count + amazon_count,
            'ebay_count': ebay_count,
            'amazon_count': amazon_count,
            'listings': listings
        })
    except Exception as e:
        return jsonify({'success': False, 'error': ss_errors._safe_error(e, 'items_prep:live_listings')}), 500


def api_items_prep_diagnostic_history(upc):
    """Unified per-item timeline for diagnostic view (item-prep + item-manager changes)."""
    try:
        upc_n = ss_normalization._normalize_upc_preserve_suffix_for_match(ss_normalization._normalize_upc(upc))
        if not upc_n:
            return jsonify({'success': False, 'error': 'Missing upc'}), 400

        base_upc = upc_n.split('-', 1)[0] if '-' in upc_n else upc_n
        requested_lot = ss_normalization._normalize_lot_number(ss_warehouse_allocations._preferred_lot_from_request())
        requested_row_status = ss_normalization._normalize_prep_row_status(request.args.get('row_status') or request.args.get('status'))
        exact_row_status = '' if '-' in upc_n else requested_row_status
        limit = ss_listing_settings._listingagent_parse_int(request.args.get('limit'), 200) or 200
        limit = max(1, min(limit, 1000))
        fetch_limit = max(100, min(limit * 4, 4000))

        safe_base = base_upc.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')
        like_base = f"{safe_base}-%"
        events = []

        def _parse_dt(raw_ts):
            txt = str(raw_ts or '').strip()
            if not txt:
                return datetime.datetime.min
            try:
                txt = txt.replace(' ', 'T')
                if txt.endswith('Z'):
                    txt = txt[:-1] + '+00:00'
                parsed = datetime.datetime.fromisoformat(txt)
                if parsed.tzinfo is not None:
                    parsed = parsed.astimezone(datetime.timezone.utc).replace(tzinfo=None)
                return parsed
            except Exception:
                return datetime.datetime.min

        def _to_int(raw_value):
            try:
                return int(raw_value) if raw_value is not None else None
            except Exception:
                return None

        target_upc_key = upc_n.lower()

        def _matches_upc(candidate):
            key = ss_normalization._normalize_upc_preserve_suffix_for_match(ss_normalization._normalize_upc(candidate))
            if not key:
                return False
            return key.lower() == target_upc_key

        def _matches_lot(event_lot):
            if not requested_lot:
                return True
            lot_key = ss_normalization._normalize_lot_number(event_lot)
            if not lot_key:
                # Keep older/unscoped records visible even when lot context is active.
                return True
            return lot_key.lower() == requested_lot.lower()

        def _matches_row_status(event_status='', event_row_status=''):
            if not exact_row_status:
                return True
            candidate = ss_normalization._normalize_prep_row_status(event_row_status or event_status)
            return bool(candidate and candidate == exact_row_status)

        def _append_event(payload):
            if not isinstance(payload, dict):
                return
            if not str(payload.get('timestamp') or '').strip():
                return
            events.append(payload)

        prep_rows = []
        try:
            with ss_database.db_connection('preplog.db') as prep_conn:
                prep_cur = prep_conn.cursor()
                ss_prep_log._preplog_init_tables(prep_cur)
                prep_cur.execute('''
                    SELECT *
                    FROM prep_log
                    WHERE (
                        upc = ? COLLATE NOCASE
                        OR upc = ? COLLATE NOCASE
                        OR base_upc = ? COLLATE NOCASE
                        OR upc LIKE ? ESCAPE '\\'
                    )
                    ORDER BY created_at DESC, id DESC
                    LIMIT ?
                ''', (upc_n, base_upc, base_upc, like_base, fetch_limit))
                prep_rows = [ss_prep_log._preplog_enrich_row(ss_listing_checks._listagent_row_to_dict(r)) for r in prep_cur.fetchall()]
        except Exception:
            prep_rows = []

        for row in prep_rows:
            row_upc = row.get('upc')
            if not _matches_upc(row_upc):
                continue
            if not _matches_row_status(row.get('status'), row.get('status')):
                continue

            meta = row.get('meta') if isinstance(row.get('meta'), dict) else ss_prep_log._preplog_meta_dict(row)
            event_lot = ss_normalization._normalize_lot_number(
                row.get('lot_number')
                or meta.get('lot_number')
                or meta.get('assigned_lot')
                or meta.get('requested_lot')
            )
            if not _matches_lot(event_lot):
                continue

            status = str(row.get('status') or '').strip().lower()
            action = str(meta.get('action') or '').strip().lower()
            qty = _to_int(row.get('quantity'))
            note = str(row.get('note') or '').strip()
            reason = str(row.get('reason') or '').strip()

            if status == 'good' and action in (
                'saved_good',
                'saved_good_suffixed',
                'converted_to_good',
                'updated_suffixed_good',
                'converted_bad_to_good_kept_suffix',
                'allocate_lots'
            ):
                title = 'Added from item-prep'
            elif status == 'good':
                title = 'Item-prep marked good'
            elif status == 'bad':
                title = 'Item-prep marked bad'
            elif status == 'return':
                title = 'Item-prep marked return'
            elif status == 'unchecked':
                title = 'Item-prep marked unchecked'
            else:
                title = 'Item-prep update'

            desc_bits = []
            if qty is not None:
                desc_bits.append(f'qty {qty}')
            if reason:
                desc_bits.append(f'reason: {reason}')
            if action:
                desc_bits.append(action.replace('_', ' '))

            changes = []
            if status:
                changes.append({'field': 'status', 'to': status})
            if qty is not None:
                changes.append({'field': 'quantity', 'to': qty})
            if reason:
                changes.append({'field': 'reason', 'to': reason})
            if note:
                changes.append({'field': 'note', 'to': note})

            _append_event({
                'id': f"prep-{row.get('id')}",
                '_sort_id': _to_int(row.get('id')) or 0,
                'timestamp': row.get('created_at') or '',
                'category': 'item_prep',
                'title': title,
                'description': '; '.join(desc_bits),
                'upc': row_upc,
                'base_upc': row.get('base_upc') or base_upc,
                'lot_number': event_lot,
                'source': row.get('source') or 'item-prep',
                'changes': changes
            })

        listing_rows = []
        try:
            with ss_database.db_connection('listinglog.db') as listing_conn:
                listing_cur = listing_conn.cursor()
                ss_listing_log._listinglog_init_tables(listing_cur)
                listing_cur.execute('''
                    SELECT *
                    FROM listing_log
                    WHERE (
                        upc = ? COLLATE NOCASE
                        OR upc = ? COLLATE NOCASE
                        OR upc LIKE ? ESCAPE '\\'
                    )
                    ORDER BY created_at DESC, id DESC
                    LIMIT ?
                ''', (upc_n, base_upc, like_base, fetch_limit))
                listing_rows = [ss_listing_checks._listagent_row_to_dict(r) for r in listing_cur.fetchall()]
        except Exception:
            listing_rows = []

        for row in listing_rows:
            row_upc = row.get('upc')
            if not _matches_upc(row_upc):
                continue

            meta = ss_prep_log._listinglog_meta_dict(row)
            meta_row_status = ss_normalization._normalize_prep_row_status(meta.get('row_status') or meta.get('status'))
            if not _matches_row_status('', meta_row_status):
                continue
            event_lot = ss_normalization._normalize_lot_number(
                meta.get('lot_number')
                or meta.get('target_lot')
                or meta.get('lot')
                or meta.get('assigned_lot')
            )
            if not _matches_lot(event_lot):
                continue

            platform = str(row.get('platform') or '').strip().lower()
            action = str(row.get('action') or '').strip().lower()
            source = str(row.get('source') or '').strip()
            source_label = ss_listing_lifecycle._listing_source_label(source) if source else ''
            qty = _to_int(row.get('quantity'))

            changes = []
            meta_changes = meta.get('changes')
            if isinstance(meta_changes, dict):
                for field, change in meta_changes.items():
                    if isinstance(change, dict):
                        changes.append({
                            'field': str(field),
                            'from': change.get('from'),
                            'to': change.get('to')
                        })
                    else:
                        changes.append({'field': str(field), 'to': change})

            category = 'item_manager'
            if platform in ('amazon', 'ebay', 'facebook'):
                category = 'marketplace'
                platform_label = 'eBay' if platform == 'ebay' else platform.capitalize()
                if action == 'listed':
                    title = f'{platform_label} marked listed'
                elif action == 'unlisted':
                    title = f'{platform_label} marked unlisted'
                else:
                    title = f'{platform_label} listing update'
                if qty is not None and not any(ch.get('field') == 'quantity' for ch in changes):
                    changes.append({'field': 'quantity', 'to': qty})
            elif platform == 'item_manager':
                title_map = {
                    'quantity_update': 'Quantity changed in item-manager',
                    'location_update': 'Location updated in item-manager',
                    'note_add': 'Note added in item-manager',
                    'note_delete': 'Note removed in item-manager',
                    'prep_reset': 'Prep quantities reset in item-manager',
                    'diagnostic_reset': 'Diagnostic reset in item-manager'
                }
                title = title_map.get(action) or 'Item-manager update'
                if action == 'note_add':
                    note_text = str(meta.get('note') or '').strip()
                    if note_text and not any(ch.get('field') == 'note' for ch in changes):
                        changes.append({'field': 'note', 'to': note_text})
                elif action == 'note_delete':
                    note_text = str(meta.get('note') or '').strip()
                    if note_text and not any(ch.get('field') == 'note' for ch in changes):
                        changes.append({'field': 'note', 'from': note_text})
            else:
                category = platform or 'other'
                title = 'Inventory update'

            desc_bits = []
            if action:
                desc_bits.append(action.replace('_', ' '))
            if source_label:
                desc_bits.append(f'by {source_label}')
            if event_lot:
                desc_bits.append(f'LOT {event_lot}')

            _append_event({
                'id': f"log-{row.get('id')}",
                '_sort_id': _to_int(row.get('id')) or 0,
                'timestamp': row.get('created_at') or '',
                'category': category,
                'title': title,
                'description': '; '.join(desc_bits),
                'upc': row_upc,
                'base_upc': base_upc,
                'lot_number': event_lot,
                'source': source or None,
                'changes': changes
            })

        events.sort(
            key=lambda ev: (_parse_dt(ev.get('timestamp')), int(ev.get('_sort_id') or 0)),
            reverse=True
        )

        items = []
        for ev in events[:limit]:
            out = dict(ev)
            out.pop('_sort_id', None)
            items.append(out)

        return jsonify({
            'success': True,
            'upc': upc_n,
            'base_upc': base_upc,
            'lot_number': requested_lot,
            'row_status': exact_row_status,
            'items': items,
            'count': len(items)
        })
    except Exception as e:
        return jsonify({'success': False, 'error': ss_errors._safe_error(e, 'items_prep:diagnostic_history')}), 500


def api_items_prep_diagnostic_delete_photos(upc):
    """Soft-delete all diagnostic photos for a UPC by moving them to trash and setting retention expiry.
    Query param hard=1 to permanently delete files and rows.
    """
    conn = None
    try:
        upc_norm = ss_normalization._normalize_upc(upc)
        upc_n = ss_normalization._normalize_upc_preserve_suffix_for_match(upc_norm)
        row_status = ss_normalization._normalize_prep_row_status(request.args.get('row_status') or request.args.get('status'))
        scope_upc, scope_status = ss_normalization._items_to_list_asset_scope(upc_n, row_status)
        hard = (request.args.get('hard') or '0') in ('1','true','yes')
        ss_prep_schema._ensure_items_prep_tables()
        conn = sqlite3.connect('bol.db')
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        if hard:
            if row_status or ('-' in scope_upc):
                cur.execute('''
                    SELECT id, image_path, trash_path, deleted_at
                    FROM items_prep_images
                    WHERE upc = ? COLLATE NOCASE
                      AND COALESCE(row_status, '') = ? COLLATE NOCASE
                ''', (scope_upc, scope_status))
            else:
                cur.execute('SELECT id, image_path, trash_path, deleted_at FROM items_prep_images WHERE upc = ? COLLATE NOCASE', (scope_upc,))
            rows = cur.fetchall()
            for r in rows:
                # remove from whichever exists
                for rel in [r['trash_path'], r['image_path']]:
                    if rel:
                        abs_path = os.path.join(ss_runtime.app.root_path, 'static', rel) if not os.path.isabs(rel) else rel
                        try:
                            if os.path.isfile(abs_path):
                                os.remove(abs_path)
                        except Exception as fe:
                            print('Failed hard remove photo', abs_path, fe)
            if row_status or ('-' in scope_upc):
                cur.execute('''
                    DELETE FROM items_prep_images
                    WHERE upc = ? COLLATE NOCASE
                      AND COALESCE(row_status, '') = ? COLLATE NOCASE
                ''', (scope_upc, scope_status))
            else:
                cur.execute('DELETE FROM items_prep_images WHERE upc = ? COLLATE NOCASE', (scope_upc,))
            deleted = cur.rowcount
            conn.commit()
            return jsonify({'success': True, 'deleted': deleted, 'hard': True})
        else:
            # Soft delete only active (not already deleted) rows
            if row_status or ('-' in scope_upc):
                cur.execute('''
                    SELECT id, image_path
                    FROM items_prep_images
                    WHERE upc = ? COLLATE NOCASE
                      AND COALESCE(row_status, '') = ? COLLATE NOCASE
                      AND (deleted_at IS NULL OR TRIM(COALESCE(deleted_at,'')) = '')
                ''', (scope_upc, scope_status))
            else:
                cur.execute(
                    "SELECT id, image_path FROM items_prep_images WHERE upc = ? COLLATE NOCASE AND (deleted_at IS NULL OR TRIM(COALESCE(deleted_at,'')) = '')",
                    (scope_upc,)
                )
            rows = cur.fetchall()
            deleted = 0
            for r in rows:
                rel = r['image_path']
                if not rel:
                    continue
                abs_path = os.path.join(ss_runtime.app.root_path, 'static', rel) if not os.path.isabs(rel) else rel
                new_rel = None
                if os.path.isfile(abs_path):
                    new_rel = ss_prep_media._move_to_trash(abs_path, scope_upc)
                # mark as deleted with expiry
                del_at = ss_normalization._now_iso()
                import datetime as _dt
                exp = ( _dt.datetime.now(_dt.UTC) + _dt.timedelta(days=ss_prep_media._trash_retention_days()) ).isoformat()
                cur.execute('UPDATE items_prep_images SET deleted_at=?, expires_at=?, trash_path=? WHERE id=?', (del_at, exp, new_rel or rel, r['id']))
                deleted += 1
            conn.commit()
            return jsonify({'success': True, 'deleted': deleted, 'hard': False, 'retention_days': ss_prep_media._trash_retention_days()})
    except Exception as e:
        return jsonify({'success': False, 'error': ss_errors._safe_error(e)}), 500
    finally:
        if conn is not None:
            conn.close()


def api_items_prep_diagnostic_add_photos(upc):
    """Upload additional diagnostic photos for a UPC without changing status.
    Expects multipart/form-data with files in photos[] (or photos/photo) and optional rotations[]
    Returns: { success, images: [{id, image_path, created_at, rotation}] }
    """
    conn = None
    try:
        upc_norm = ss_normalization._normalize_upc(upc)
        upc_n = ss_normalization._normalize_upc_preserve_suffix_for_match(upc_norm)
        row_status = ss_normalization._normalize_prep_row_status(request.form.get('row_status') or request.form.get('status') or request.args.get('row_status') or request.args.get('status'))
        scope_upc, scope_status = ss_normalization._items_to_list_asset_scope(upc_n, row_status)
        if not scope_upc:
            return jsonify({'success': False, 'error': 'Missing upc'}), 400
        ss_prep_schema._ensure_items_prep_tables()
        from werkzeug.utils import secure_filename
        save_dir = os.path.join(ss_runtime.app.root_path, 'static', 'items_prep')
        os.makedirs(save_dir, exist_ok=True)
        files = request.files.getlist('photos[]') or request.files.getlist('photos') or ([] if 'photo' not in request.files else [request.files['photo']])
        rotations = request.form.getlist('rotations[]') or request.form.getlist('rotations') or []
        if not files:
            return jsonify({'success': False, 'error': 'No files uploaded'}), 400
        import datetime
        ts = datetime.datetime.now(datetime.UTC).isoformat()
        conn = sqlite3.connect('bol.db')
        cur = conn.cursor()
        out = []
        for idx, f in enumerate(files):
            if not f or not getattr(f, 'filename', ''):
                continue
            fn = secure_filename(f.filename)
            name, ext = os.path.splitext(fn)
            unique = f"{upc_n}_{int(time.time()*1000)}{ext or '.jpg'}"
            abs_path = os.path.join(save_dir, unique)
            try:
                f.save(abs_path)
                rel = f"items_prep/{unique}"
                rot = 0
                try:
                    if idx < len(rotations):
                        rot = int(rotations[idx] or 0)
                except Exception:
                    rot = 0
                cur.execute(
                    'INSERT INTO items_prep_images (upc, row_status, image_path, created_at, rotation) VALUES (?,?,?,?,?)',
                    (scope_upc, scope_status, rel, ts, rot)
                )
                img_id = cur.lastrowid
                out.append({'id': img_id, 'image_path': rel, 'created_at': ts, 'rotation': rot, 'row_status': scope_status})
            except Exception as se:
                print('Failed to save diagnostic image (add):', se)
        conn.commit()
        
        # Clear cache for this UPC to ensure fresh data on next lookup
        cache_key = f"view//api/bol_lookup?upc={scope_upc}"
        ss_runtime.cache.delete(cache_key)
        
        return jsonify({'success': True, 'images': out})
    except Exception as e:
        return jsonify({'success': False, 'error': ss_errors._safe_error(e)}), 500
    finally:
        if conn is not None:
            conn.close()


def api_items_prep_delete_photo(photo_id):
    """Soft-delete or hard-delete a single diagnostic photo by its id.
    Query param hard=1 to permanently remove file and DB row.
    """
    conn = None
    try:
        hard = (request.args.get('hard') or '0') in ('1','true','yes')
        ss_prep_schema._ensure_items_prep_tables()
        conn = sqlite3.connect('bol.db')
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute('SELECT id, upc, image_path, trash_path, deleted_at FROM items_prep_images WHERE id = ?', (photo_id,))
        r = cur.fetchone()
        if not r:
            return jsonify({'success': False, 'error': 'Photo not found'}), 404
        if hard:
            # remove file(s) if present then delete row
            for rel in (r['trash_path'], r['image_path']):
                if rel:
                    abs_path = os.path.join(ss_runtime.app.root_path, 'static', rel) if not os.path.isabs(rel) else rel
                    try:
                        if os.path.isfile(abs_path):
                            os.remove(abs_path)
                    except Exception as fe:
                        print('Failed hard remove photo', abs_path, fe)
            cur.execute('DELETE FROM items_prep_images WHERE id = ?', (photo_id,))
            deleted = cur.rowcount
            conn.commit()
            return jsonify({'success': True, 'deleted': deleted, 'hard': True})
        else:
            # soft delete (mark deleted_at, set expires_at, move file to trash)
            if r['deleted_at'] and str(r['deleted_at']).strip():
                return jsonify({'success': False, 'error': 'Photo already deleted'}), 400
            rel = r['image_path']
            abs_path = os.path.join(ss_runtime.app.root_path, 'static', rel) if not os.path.isabs(rel) else rel
            new_rel = None
            if os.path.isfile(abs_path):
                new_rel = ss_prep_media._move_to_trash(abs_path, r['upc'])
            del_at = ss_normalization._now_iso()
            import datetime as _dt
            exp = (_dt.datetime.now(_dt.UTC) + _dt.timedelta(days=ss_prep_media._trash_retention_days())).isoformat()
            cur.execute('UPDATE items_prep_images SET deleted_at=?, expires_at=?, trash_path=? WHERE id=?', (del_at, exp, new_rel or rel, photo_id))
            conn.commit()
            try:
                cache_key = f"view//api/bol_lookup?upc={r['upc']}"
                ss_runtime.cache.delete(cache_key)
            except Exception:
                pass
            return jsonify({'success': True, 'deleted': 1, 'hard': False, 'retention_days': ss_prep_media._trash_retention_days()})
    except Exception as e:
        return jsonify({'success': False, 'error': ss_errors._safe_error(e)}), 500
    finally:
        if conn is not None:
            conn.close()


def api_items_prep_set_display_image():
    """Set a diagnostic photo as the display image for a UPC in bol_items.
    JSON: { id?, upc, lot_number?, photo_id } OR { id?, upc, lot_number?, image_path }
    Returns: { success, image_url, id, lot_number }
    """
    conn = None
    try:
        data = request.get_json() or {}
        upc = ss_normalization._normalize_upc(data.get('upc', ''))
        upc_n = ss_normalization._normalize_upc_preserve_suffix_for_match(upc)
        item_id = data.get('id')
        if item_id is not None and str(item_id).strip() != '':
            try:
                item_id = int(item_id)
            except Exception:
                return jsonify({'success': False, 'error': 'Invalid id'}), 400
            if item_id <= 0:
                return jsonify({'success': False, 'error': 'Invalid id'}), 400
        else:
            item_id = None
        lot_number = ss_normalization._normalize_lot_number(ss_warehouse_allocations._preferred_lot_from_request(data))
        photo_id = data.get('photo_id')
        image_path = data.get('image_path')

        if not upc_n:
            return jsonify({'success': False, 'error': 'Missing upc'}), 400

        # Get the image path from photo_id if provided
        if photo_id and not image_path:
            ss_prep_schema._ensure_items_prep_tables()
            with sqlite3.connect('bol.db') as lookup_conn:
                cur = lookup_conn.cursor()
                cur.execute('SELECT image_path FROM items_prep_images WHERE id = ?', (photo_id,))
                row = cur.fetchone()
                if row:
                    image_path = row[0]

        if not image_path:
            return jsonify({'success': False, 'error': 'Missing photo_id or image_path'}), 400

        # Convert relative path to full URL (items_prep/xxx.jpg -> /static/items_prep/xxx.jpg)
        image_url = f"/static/{image_path}" if not image_path.startswith('/') else image_path

        conn = sqlite3.connect('bol.db', isolation_level='IMMEDIATE')
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        target_id = None
        target_lot = lot_number
        if item_id is not None:
            cur.execute('''
                SELECT id, upc, COALESCE(lot_number, '') AS lot_number
                FROM bol_items
                WHERE id = ?
                LIMIT 1
            ''', (item_id,))
            row = cur.fetchone()
            if not row:
                return jsonify({'success': False, 'error': f'Item id {item_id} not found'}), 404
            row_upc = ss_normalization._normalize_upc_preserve_suffix_for_match(row['upc'])
            row_lot = ss_normalization._normalize_lot_number(row['lot_number'])
            if row_upc.lower() != upc_n.lower():
                return jsonify({'success': False, 'error': 'Provided upc does not match item id'}), 400
            if lot_number and row_lot and lot_number.lower() != row_lot.lower():
                return jsonify({'success': False, 'error': 'Provided lot_number does not match item id'}), 400
            target_id = int(row['id'])
            target_lot = row_lot
        else:
            if not lot_number and ss_warehouse_allocations._has_multiple_lot_rows(cur, upc_n):
                return jsonify({
                    'success': False,
                    'error': 'Multiple LOT rows found for this UPC. Please provide lot_number or row id.'
                }), 400
            row = ss_warehouse_allocations._select_canonical_bol_row(
                cur,
                upc_n,
                lot_number,
                columns='id, upc, lot_number',
                strict_lot=bool(lot_number)
            )
            if not row:
                if lot_number:
                    return jsonify({'success': False, 'error': f'Item {upc_n} not found in lot {lot_number}'}), 404
                return jsonify({'success': False, 'error': f'Item {upc_n} not found'}), 404
            target_id = int(row[0])
            target_lot = ss_normalization._normalize_lot_number(row[2])

        cur.execute('UPDATE bol_items SET image_url = ? WHERE id = ?', (image_url, target_id))
        updated = cur.rowcount
        conn.commit()

        print(
            f'[SET DISPLAY IMAGE] Updated bol_items id={target_id} '
            f'for UPC {upc_n} lot={target_lot or "(none)"} with image_url: {image_url}'
        )
        return jsonify({
            'success': True,
            'image_url': image_url,
            'updated': updated,
            'id': target_id,
            'lot_number': target_lot
        })
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': ss_errors._safe_error(e)}), 500
    finally:
        if conn is not None:
            conn.close()


def api_items_prep_trash_list(upc):
    conn = None
    try:
        upc_norm = ss_normalization._normalize_upc(upc)
        # Strip leading zeros to match item manager behavior
        upc_n = ss_normalization._strip_leading_zeros_numeric(upc_norm)
        ss_prep_schema._ensure_items_prep_tables()
        conn = sqlite3.connect('bol.db')
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute('SELECT id, trash_path, deleted_at, expires_at FROM items_prep_images WHERE upc = ? COLLATE NOCASE AND deleted_at IS NOT NULL ORDER BY deleted_at DESC', (upc_n,))
        rows = [dict(r) for r in cur.fetchall()]
        return jsonify({'success': True, 'results': rows})
    except Exception as e:
        return jsonify({'success': False, 'error': ss_errors._safe_error(e)}), 500
    finally:
        if conn is not None:
            conn.close()


def api_items_prep_trash_restore(upc):
    conn = None
    try:
        data = request.get_json() or {}
        ids = data.get('ids')  # optional list of ids to restore; if missing, restore all for UPC
        upc_n = ss_normalization._normalize_upc(upc)
        ss_prep_schema._ensure_items_prep_tables()
        conn = sqlite3.connect('bol.db')
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        if ids and isinstance(ids, list):
            placeholders = ','.join('?' for _ in ids)
            cur.execute(f'SELECT id, image_path, trash_path FROM items_prep_images WHERE id IN ({placeholders}) AND deleted_at IS NOT NULL', tuple(ids))
            rows = cur.fetchall()
        else:
            cur.execute('SELECT id, image_path, trash_path FROM items_prep_images WHERE upc = ? COLLATE NOCASE AND deleted_at IS NOT NULL', (upc_n,))
            rows = cur.fetchall()
        restored = 0
        for r in rows:
            tr = r['trash_path']
            if not tr:
                continue
            abs_trash = os.path.join(ss_runtime.app.root_path, 'static', tr)
            abs_orig = os.path.join(ss_runtime.app.root_path, 'static', r['image_path'])
            # ensure destination dir exists
            os.makedirs(os.path.dirname(abs_orig), exist_ok=True)
            try:
                if os.path.isfile(abs_trash):
                    os.replace(abs_trash, abs_orig)
                cur.execute('UPDATE items_prep_images SET deleted_at=NULL, expires_at=NULL, trash_path=NULL WHERE id=?', (r['id'],))
                restored += 1
            except Exception as e:
                print('Restore failed:', e)
        conn.commit()
        return jsonify({'success': True, 'restored': restored})
    except Exception as e:
        return jsonify({'success': False, 'error': ss_errors._safe_error(e)}), 500
    finally:
        if conn is not None:
            conn.close()


def api_items_prep_diagnostic_reset(upc):
    """Reset item to original unchecked state: restore original_qty to unchecked, clear good/bad quantities"""
    conn = None
    try:
        upc_n = ss_normalization._normalize_upc_preserve_suffix_for_match(ss_normalization._normalize_upc(upc))
        if not upc_n:
            return jsonify({'success': False, 'error': 'Missing upc'}), 400

        req_data = request.get_json(silent=True) or {}
        lot_number = ss_warehouse_allocations._preferred_lot_from_request(req_data)
        source = ss_normalization._normalize_listing_source(req_data.get('source'), default='user')

        conn = sqlite3.connect('bol.db', isolation_level='IMMEDIATE')
        cur = conn.cursor()
        ss_prep_schema._ensure_items_prep_tables()

        if lot_number:
            cur.execute('''
                SELECT id, COALESCE(original_qty, 0), COALESCE(good_qty, 0), COALESCE(bad_qty, 0), unchecked_qty, COALESCE(quantity, 0), COALESCE(lot_number, '')
                FROM bol_items
                WHERE upc = ? COLLATE NOCASE
                  AND lot_number = ? COLLATE NOCASE
                ORDER BY import_date DESC, id DESC
                LIMIT 1
            ''', (upc_n, lot_number))
        else:
            cur.execute('''
                SELECT id, COALESCE(original_qty, 0), COALESCE(good_qty, 0), COALESCE(bad_qty, 0), unchecked_qty, COALESCE(quantity, 0), COALESCE(lot_number, '')
                FROM bol_items
                WHERE upc = ? COLLATE NOCASE
                ORDER BY import_date DESC, id DESC
                LIMIT 1
            ''', (upc_n,))
        row = cur.fetchone()

        if not row:
            return jsonify({'success': False, 'error': 'Item not found'}), 404

        row_id = int(row[0])
        original_qty = int(row[1] or 0)
        current_good = int(row[2] or 0)
        current_bad = int(row[3] or 0)
        current_unchecked = row[4]
        current_qty = int(row[5] or 0)
        target_lot = ss_normalization._normalize_lot_number(row[6])
        current_unchecked_n = int(current_unchecked or 0) if current_unchecked is not None else 0
        if original_qty <= 0:
            if current_unchecked is not None:
                original_qty = max(0, int(current_unchecked or 0) + current_good + current_bad)
            else:
                original_qty = max(0, current_good + current_bad)

        # Reset: unchecked_qty = original_qty, good_qty = 0, bad_qty = 0.
        cur.execute('''
            UPDATE bol_items 
            SET unchecked_qty = ?, 
                original_qty = ?,
                good_qty = 0, 
                bad_qty = 0, 
                quantity = ?
            WHERE id = ?
        ''', (original_qty, original_qty, original_qty, row_id))

        # Clear prep status row in this lot context only (with legacy lotless fallback cleanup).
        status_lot = ss_warehouse_allocations._resolve_prep_status_lot(cur, upc_n, target_lot or lot_number)
        cur.execute('''
            DELETE FROM items_prep_status
            WHERE upc = ? COLLATE NOCASE
              AND (
                COALESCE(lot_number, '') = ? COLLATE NOCASE
                OR (? <> '' AND COALESCE(lot_number, '') = '')
              )
        ''', (upc_n, status_lot, status_lot))

        conn.commit()
        try:
            ss_caching.update_data_version()
        except Exception:
            pass

        reset_changes = {}
        if current_qty != original_qty:
            reset_changes['quantity'] = {'from': current_qty, 'to': original_qty}
        if current_good != 0:
            reset_changes['good_qty'] = {'from': current_good, 'to': 0}
        if current_bad != 0:
            reset_changes['bad_qty'] = {'from': current_bad, 'to': 0}
        if current_unchecked_n != original_qty:
            reset_changes['unchecked_qty'] = {'from': current_unchecked_n, 'to': original_qty}
        if reset_changes:
            try:
                ss_listing_log._listinglog_add_entry(
                    upc=upc_n,
                    platform='item_manager',
                    action='prep_reset',
                    source=source,
                    quantity=original_qty,
                    success=True,
                    meta={
                        'lot_number': target_lot or ss_normalization._normalize_lot_number(lot_number),
                        'changes': reset_changes,
                        'via': 'api_items_prep_diagnostic_reset'
                    }
                )
            except Exception:
                pass
        
        return jsonify({
            'success': True, 
            'original_qty': original_qty,
            'lot_number': target_lot,
            'message': f'Reset to {original_qty} unchecked'
        })
    except Exception as e:
        return jsonify({'success': False, 'error': ss_errors._safe_error(e)}), 500
    finally:
        if conn is not None:
            conn.close()
