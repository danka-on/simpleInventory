"""Listing settings for Sweet Shelves."""

import datetime
import os
import sqlite3
import time
from flask import jsonify, render_template, request, url_for
from . import (
    database as ss_database, errors as ss_errors, listing_checks as ss_listing_checks, listing_queue as
    ss_listing_queue, normalization as ss_normalization, runtime as ss_runtime,
)


def listingagent():
    """Experimental: assisted listing page (start with eBay)."""
    return render_template('listingagent.html')


def listingagent_mobile():
    """Mobile helper: camera upload page for Listing Agent photos."""
    upc = (request.args.get('upc') or '').strip()
    if not upc:
        return "Missing upc", 400
    return render_template('listingagent_mobile.html', upc=upc)


def check_items():
    """Checker station for open UPC-specific inspection requests."""
    upc = (request.args.get('upc') or '').strip()
    return render_template('check_items.html', initial_upc=upc)


def items_to_list_mobile_photos():
    """Mobile helper: camera upload page for Items-to-List row-scoped photos."""
    upc = ss_normalization._normalize_upc_preserve_suffix_for_match(request.args.get('upc') or '')
    if not upc:
        return "Missing upc", 400
    row_status = ss_normalization._normalize_prep_row_status(request.args.get('row_status') or request.args.get('status'))
    title = str(request.args.get('title') or '').strip()
    return_path = str(request.args.get('return') or '').strip()
    if not title:
        conn = None
        try:
            conn = sqlite3.connect('bol.db')
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()
            cur.execute('''
                SELECT item_description
                FROM bol_items
                WHERE upc = ? COLLATE NOCASE
                ORDER BY import_date DESC, id DESC
                LIMIT 1
            ''', (upc,))
            row = cur.fetchone()
            title = str((row['item_description'] if row else '') or '').strip()
        except Exception:
            title = ''
        finally:
            try:
                if conn is not None:
                    conn.close()
            except Exception:
                pass
    return render_template(
        'items_to_list_mobile_photos.html',
        upc=upc,
        row_status=row_status,
        title=title,
        return_path=(return_path if return_path.startswith('/') else '')
    )


def _listingagent_init_settings_table(cur):
    cur.execute('''
        CREATE TABLE IF NOT EXISTS listing_agent_settings (
            key TEXT PRIMARY KEY,
            value TEXT,
            updated_at TEXT
        )
    ''')


def _listingagent_get_settings():
    try:
        with ss_database.db_connection('sync_settings.db') as conn:
            cur = conn.cursor()
            _listingagent_init_settings_table(cur)
            cur.execute('SELECT key, value FROM listing_agent_settings')
            rows = cur.fetchall()
            return {r['key']: r['value'] for r in rows}
    except Exception:
        return {}


def _listingagent_upsert_settings(settings: dict):
    now = datetime.datetime.now().isoformat()
    with ss_database.db_connection('sync_settings.db') as conn:
        cur = conn.cursor()
        _listingagent_init_settings_table(cur)
        for k, v in (settings or {}).items():
            if not k:
                continue
            cur.execute('''
                INSERT INTO listing_agent_settings (key, value, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET
                    value = excluded.value,
                    updated_at = excluded.updated_at
            ''', (str(k), None if v is None else str(v), now))


def _listingagent_parse_float(val, default=None):
    try:
        if val is None:
            return default
        if isinstance(val, (int, float)):
            return float(val)
        s = str(val).strip()
        if s == '':
            return default
        return float(s)
    except Exception:
        return default


def _listingagent_parse_int(val, default=None):
    try:
        if val is None:
            return default
        if isinstance(val, bool):
            return default
        if isinstance(val, int):
            return int(val)
        s = str(val).strip()
        if s == '':
            return default
        return int(float(s))
    except Exception:
        return default


def _listagent_normalize_added_mode(val, default='my'):
    raw = (val or '').strip().lower().replace(' ', '_')
    if raw in ('auto', 'auto_added', 'autofill', 'automatic'):
        return 'auto'
    if raw in ('my', 'mine', 'my_added', 'manual', 'user'):
        return 'my'
    fallback = '' if default is None else str(default).strip().lower()
    if fallback not in ('my', 'auto', ''):
        fallback = 'my'
    return fallback


def api_listingagent_get_settings():
    try:
        return jsonify({'success': True, 'settings': _listingagent_get_settings()})
    except Exception as e:
        return jsonify({'success': False, 'error': ss_errors._safe_error(e, 'listingagent:get_settings')}), 500


def api_listingagent_save_settings():
    try:
        payload = request.json or {}
        settings = payload.get('settings', payload)
        if not isinstance(settings, dict):
            return jsonify({'success': False, 'error': 'settings must be an object'}), 400
        _listingagent_upsert_settings(settings)
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': ss_errors._safe_error(e, 'listingagent:save_settings')}), 500


def api_listingagent_get_working_draft():
    """Get the saved working draft for a UPC."""
    try:
        upc = (request.args.get('upc') or '').strip()
        if not upc:
            return jsonify({'success': False, 'error': 'upc is required'}), 400
        draft = ss_listing_queue._listagent_get_working_draft(upc)
        return jsonify({'success': True, 'draft': draft})
    except Exception as e:
        return jsonify({'success': False, 'error': ss_errors._safe_error(e, 'listingagent:get_working_draft')}), 500


def api_listingagent_save_working_draft():
    """Persist a working draft snapshot for a UPC."""
    try:
        data = request.json or {}
        upc = (data.get('upc') or '').strip()
        if not upc:
            return jsonify({'success': False, 'error': 'upc is required'}), 400
        draft = data.get('draft')
        if draft is None:
            draft = {k: v for k, v in data.items() if k != 'upc'}
        if not isinstance(draft, dict):
            return jsonify({'success': False, 'error': 'draft must be an object'}), 400
        ss_listing_queue._listagent_upsert_working_draft(upc, draft)
        return jsonify({'success': True, 'draft': ss_listing_queue._listagent_get_working_draft(upc)})
    except Exception as e:
        return jsonify({'success': False, 'error': ss_errors._safe_error(e, 'listingagent:save_working_draft')}), 500


def api_listingagent_delete_working_draft():
    """Delete a saved working draft for a UPC."""
    try:
        data = request.json or {}
        upc = (data.get('upc') or request.args.get('upc') or '').strip()
        if not upc:
            return jsonify({'success': False, 'error': 'upc is required'}), 400
        removed = ss_listing_queue._listagent_delete_working_draft(upc)
        return jsonify({'success': True, 'removed': removed})
    except Exception as e:
        return jsonify({'success': False, 'error': ss_errors._safe_error(e, 'listingagent:delete_working_draft')}), 500


def api_listingagent_check_request_get():
    try:
        upc = (request.args.get('upc') or '').strip()
        if not upc:
            return jsonify({'success': False, 'error': 'upc is required'}), 400
        req = ss_listing_checks._listagent_get_check_request(upc)
        notes = ss_listing_checks._listagent_get_check_notes(upc)
        media_rows = ss_listing_checks._listagent_get_check_media(upc)
        base = (request.url_root or '').rstrip('/')
        media = []
        for mr in media_rows:
            rel = (mr.get('file_path') or '').strip()
            if not rel:
                continue
            media.append({
                'id': mr.get('id'),
                'upc': mr.get('upc') or '',
                'url': f"{base}{url_for('static', filename=rel)}",
                'file_path': rel,
                'media_type': (mr.get('media_type') or 'image').strip().lower() or 'image',
                'original_filename': mr.get('original_filename') or '',
                'size_bytes': mr.get('size_bytes'),
                'note_type': (mr.get('note_type') or 'checker').strip().lower() or 'checker',
                'created_at': mr.get('created_at') or '',
            })
        return jsonify({'success': True, 'request': req, 'notes': notes, 'media': media})
    except Exception as e:
        return jsonify({'success': False, 'error': ss_errors._safe_error(e, 'listingagent:check_request_get')}), 500


def api_listingagent_check_request_save():
    try:
        data = request.json or {}
        upc = (data.get('upc') or '').strip()
        if not upc:
            return jsonify({'success': False, 'error': 'upc is required'}), 400
        req = ss_listing_checks._listagent_set_check_request(
            upc=upc,
            check_quantity=data.get('check_quantity'),
            take_pictures=data.get('take_pictures'),
            custom_note=data.get('custom_note'),
            status=data.get('status'),
            source=data.get('source')
        )
        return jsonify({'success': True, 'request': req})
    except Exception as e:
        return jsonify({'success': False, 'error': ss_errors._safe_error(e, 'listingagent:check_request_save')}), 500


def api_listingagent_check_request_complete():
    try:
        data = request.json or {}
        upc = (data.get('upc') or '').strip()
        if not upc:
            return jsonify({'success': False, 'error': 'upc is required'}), 400
        complete = data.get('complete', True)
        completed_by = (data.get('completed_by') or '').strip() or None
        req = ss_listing_checks._listagent_mark_check_request_complete(upc=upc, complete=bool(complete), completed_by=completed_by)
        return jsonify({'success': True, 'request': req})
    except Exception as e:
        return jsonify({'success': False, 'error': ss_errors._safe_error(e, 'listingagent:check_request_complete')}), 500


def api_listingagent_check_requests():
    try:
        limit = _listingagent_parse_int(request.args.get('limit'), 100) or 100
        status = (request.args.get('status') or 'open').strip().lower()
        if status not in ('', 'open', 'complete', 'all'):
            status = 'open'
        rows = ss_listing_checks._listagent_get_check_requests(limit=limit, status='' if status == 'all' else status)
        rows = ss_listing_checks._listagent_enrich_check_request_rows(rows, base_url=(request.url_root or '').rstrip('/'))
        return jsonify({'success': True, 'items': rows})
    except Exception as e:
        return jsonify({'success': False, 'error': ss_errors._safe_error(e, 'listingagent:check_requests')}), 500


def api_listingagent_check_notes_get():
    try:
        upc = (request.args.get('upc') or '').strip()
        if not upc:
            return jsonify({'success': False, 'error': 'upc is required'}), 400
        notes = ss_listing_checks._listagent_get_check_notes(upc)
        return jsonify({'success': True, 'notes': notes})
    except Exception as e:
        return jsonify({'success': False, 'error': ss_errors._safe_error(e, 'listingagent:check_notes_get')}), 500


def api_listingagent_check_notes_add():
    try:
        data = request.json or {}
        upc = (data.get('upc') or '').strip()
        note = (data.get('note') or '').strip()
        note_type = (data.get('note_type') or 'checker').strip().lower() or 'checker'
        if not upc or not note:
            return jsonify({'success': False, 'error': 'upc and note are required'}), 400
        row = ss_listing_checks._listagent_add_check_note(upc=upc, note=note, note_type=note_type)
        return jsonify({'success': True, 'item': row})
    except Exception as e:
        return jsonify({'success': False, 'error': ss_errors._safe_error(e, 'listingagent:check_notes_add')}), 500


def api_listingagent_check_media_get():
    try:
        upc = (request.args.get('upc') or '').strip()
        if not upc:
            return jsonify({'success': False, 'error': 'upc is required'}), 400
        media_rows = ss_listing_checks._listagent_get_check_media(upc)
        base = (request.url_root or '').rstrip('/')
        media = []
        for mr in media_rows:
            rel = (mr.get('file_path') or '').strip()
            if not rel:
                continue
            media.append({
                'id': mr.get('id'),
                'upc': mr.get('upc') or '',
                'url': f"{base}{url_for('static', filename=rel)}",
                'file_path': rel,
                'media_type': (mr.get('media_type') or 'image').strip().lower() or 'image',
                'original_filename': mr.get('original_filename') or '',
                'size_bytes': mr.get('size_bytes'),
                'note_type': (mr.get('note_type') or 'checker').strip().lower() or 'checker',
                'created_at': mr.get('created_at') or '',
            })
        return jsonify({'success': True, 'items': media})
    except Exception as e:
        return jsonify({'success': False, 'error': ss_errors._safe_error(e, 'listingagent:check_media_get')}), 500


def api_listingagent_check_media_upload():
    try:
        upc = (request.form.get('upc') or request.args.get('upc') or '').strip()
        if not upc:
            return jsonify({'success': False, 'error': 'upc is required'}), 400
        note_type = (request.form.get('note_type') or request.args.get('note_type') or 'checker').strip().lower() or 'checker'
        media_type_hint = (request.form.get('media_type') or request.args.get('media_type') or '').strip().lower()
        from werkzeug.utils import secure_filename
        import uuid

        files = request.files.getlist('files[]') or request.files.getlist('files') or request.files.getlist('media[]') or ([] if not request.files else list(request.files.values()))
        if not files:
            return jsonify({'success': False, 'error': 'No files uploaded'}), 400

        save_dir = os.path.join(ss_runtime.app.root_path, 'static', 'listingagent_uploads', 'check_items')
        os.makedirs(save_dir, exist_ok=True)
        base = (request.url_root or '').rstrip('/')
        saved = []
        for f in files:
            if not f or not getattr(f, 'filename', None):
                continue
            fn = secure_filename(f.filename)
            name, ext = os.path.splitext(fn)
            ext = (ext or '').lower()
            if ext not in ('.jpg', '.jpeg', '.png', '.webp', '.gif', '.heic', '.heif', '.mp4', '.mov', '.m4v', '.webm', '.mp3', '.m4a', '.wav', '.aac'):
                ext = '.jpg'
            media_type = media_type_hint
            if not media_type:
                if ext in ('.mp4', '.mov', '.m4v', '.webm'):
                    media_type = 'video'
                elif ext in ('.mp3', '.m4a', '.wav', '.aac'):
                    media_type = 'audio'
                else:
                    media_type = 'image'
            unique = f"{secure_filename(ss_normalization._normalize_upc(upc)) or 'upc'}_{int(time.time()*1000)}_{uuid.uuid4().hex[:10]}{ext}"
            abs_path = os.path.join(save_dir, unique)
            f.save(abs_path)
            rel = f"listingagent_uploads/check_items/{unique}"
            size_bytes = None
            try:
                size_bytes = os.path.getsize(abs_path)
            except Exception:
                size_bytes = None
            if media_type == 'image':
                try:
                    ss_listing_queue._listagent_add_photo(upc, image_path=rel, original_filename=fn, size_bytes=size_bytes)
                except Exception:
                    pass
            row = ss_listing_checks._listagent_add_check_media(
                upc=upc,
                media_type=media_type,
                file_path=rel,
                original_filename=fn,
                size_bytes=size_bytes,
                note_type=note_type
            )
            saved.append({
                'id': row.get('id'),
                'url': f"{base}{url_for('static', filename=rel)}",
                'file_path': rel,
                'media_type': media_type,
                'original_filename': fn,
                'size_bytes': size_bytes,
                'created_at': row.get('created_at') or ''
            })
        return jsonify({'success': True, 'upc': upc, 'saved': saved, 'saved_count': len(saved)})
    except Exception as e:
        return jsonify({'success': False, 'error': ss_errors._safe_error(e, 'listingagent:check_media_upload')}), 500


def api_listingagent_queue():
    """Get the Listing Agent queue (queued + done; excludes removed)."""
    try:
        limit = _listingagent_parse_int(request.args.get('limit'), 60) or 60
        added_mode = _listagent_normalize_added_mode(
            request.args.get('added_mode') or request.args.get('queue_mode') or request.args.get('mode'),
            default=''
        )
        if added_mode not in ('my', 'auto'):
            added_mode = ''
        items = ss_listing_queue._listagent_get_queue(limit=limit, added_mode=added_mode)
        return jsonify({'success': True, 'items': items})
    except Exception as e:
        return jsonify({'success': False, 'error': ss_errors._safe_error(e, 'listingagent:queue_get')}), 500


def api_listingagent_queue_statuses():
    """Get queue membership for a specific set of UPCs."""
    try:
        data = request.json or {}
        raw_upcs = data.get('upcs') or []
        if isinstance(raw_upcs, str):
            raw_upcs = [raw_upcs]
        if not isinstance(raw_upcs, list):
            return jsonify({'success': False, 'error': 'upcs must be a list'}), 400
        upcs = []
        seen = set()
        for raw in raw_upcs[:250]:
            key = (raw or '').strip()
            if not key or key in seen:
                continue
            seen.add(key)
            upcs.append(key)
        added_mode = _listagent_normalize_added_mode(
            data.get('added_mode') or data.get('queue_mode') or data.get('mode'),
            default=''
        )
        if added_mode not in ('my', 'auto'):
            added_mode = ''
        items = ss_listing_queue._listagent_get_queue_statuses(upcs, added_mode=added_mode)
        return jsonify({'success': True, 'items': items})
    except Exception as e:
        return jsonify({'success': False, 'error': ss_errors._safe_error(e, 'listingagent:queue_statuses')}), 500


def api_listingagent_queue_add():
    """Add a UPC to the Listing Agent queue."""
    try:
        data = request.json or {}
        upc = (data.get('upc') or '').strip()
        if not upc:
            return jsonify({'success': False, 'error': 'upc is required'}), 400
        title = (data.get('title') or '').strip()
        source = (data.get('source') or '').strip()
        item_status = (data.get('item_status') or data.get('itemStatus') or '').strip()
        added_mode = _listagent_normalize_added_mode(
            data.get('added_mode') or data.get('queue_mode') or data.get('mode'),
            default='my'
        )

        item, added = ss_listing_queue._listagent_add_to_queue(
            upc,
            title=title,
            source=source,
            item_status=item_status,
            added_mode=added_mode
        )
        return jsonify({'success': True, 'added': added, 'item': item})
    except Exception as e:
        return jsonify({'success': False, 'error': ss_errors._safe_error(e, 'listingagent:queue_add')}), 500


def api_listingagent_queue_mark_listed():
    """Mark a queued UPC as listed on a platform (and record where/when)."""
    try:
        data = request.json or {}
        upc = (data.get('upc') or '').strip()
        if not upc:
            return jsonify({'success': False, 'error': 'upc is required'}), 400

        platform = (data.get('platform') or '').strip()
        listing_id = (data.get('listingId') or data.get('listing_id') or '').strip() or None
        offer_id = (data.get('offerId') or data.get('offer_id') or '').strip() or None
        sku = (data.get('sku') or '').strip() or None
        asin = (data.get('asin') or '').strip() or None
        url = (data.get('url') or '').strip() or None

        item = ss_listing_queue._listagent_mark_listed(
            upc,
            platform=platform,
            listing_id=listing_id,
            offer_id=offer_id,
            sku=sku,
            asin=asin,
            url=url
        )
        return jsonify({'success': True, 'item': item})
    except Exception as e:
        return jsonify({'success': False, 'error': ss_errors._safe_error(e, 'listingagent:queue_mark_listed')}), 500


def api_listingagent_queue_remove():
    """Remove a UPC from the Listing Agent queue (soft remove; keeps history)."""
    try:
        data = request.json or {}
        upc = (data.get('upc') or '').strip()
        if not upc:
            return jsonify({'success': False, 'error': 'upc is required'}), 400
        item, removed = ss_listing_queue._listagent_remove_from_queue(upc)
        return jsonify({'success': True, 'removed': removed, 'item': item})
    except Exception as e:
        return jsonify({'success': False, 'error': ss_errors._safe_error(e, 'listingagent:queue_remove')}), 500
