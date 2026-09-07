"""Shipping orders for Sweet Shelves."""

import hashlib
import sqlite3
from DBmanager import ensure_sold_orders_schema
from flask import jsonify, request, send_file
from . import (
    config as ss_config, errors as ss_errors, normalization as ss_normalization, runtime as ss_runtime,
    shipping_identity as ss_shipping_identity, warehouse_allocations as ss_warehouse_allocations,
    warehouse_matching as ss_warehouse_matching,
)


_READY_TO_SHIP_CACHE_DAYS = (1, 2, 3, 5, 7, 14, 30, 60, 90, 120)


def _invalidate_ready_to_ship_cache(day_values=None):
    values = tuple(day_values or _READY_TO_SHIP_CACHE_DAYS)
    for days in values:
        try:
            ss_runtime.cache.delete(f'view//sold-orders?days={days}')
        except Exception:
            pass
        try:
            ss_runtime.cache.delete(f'view//api/ready-to-ship/count?days={days}')
        except Exception:
            pass


def ready_to_ship_clear_location(order_id):
    """Clear a Finder-locked Ready to Ship location without changing the barcode."""
    conn = None
    try:
        conn = sqlite3.connect('sold.db')
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        ss_shipping_identity._ensure_order_removal_allocations_table(cur)
        ss_shipping_identity._ensure_order_finder_matches_table(cur)
        cur.execute(
            'SELECT id, order_id, location, rackupdated, isHandled FROM orders WHERE id = ?',
            (order_id,)
        )
        order = cur.fetchone()
        if not order:
            return jsonify({'success': False, 'error': 'Order not found'}), 404

        is_handled = str(order['isHandled'] or '').strip() == '1'
        rackupdated = ss_normalization._coerce_int(order['rackupdated'], 0)
        if is_handled or rackupdated == 1:
            return jsonify({
                'success': False,
                'error': 'This order is already completed; its location cannot be cleared here.'
            }), 409

        old_location = str(order['location'] or '').strip()
        cur.execute('UPDATE orders SET location = ? WHERE id = ?', ('', order_id))
        cur.execute('DELETE FROM order_removal_allocations WHERE order_row_id = ?', (order_id,))
        cur.execute('DELETE FROM order_finder_matches WHERE order_row_id = ?', (order_id,))
        conn.commit()
        _invalidate_ready_to_ship_cache()

        return jsonify({
            'success': True,
            'order_id': order_id,
            'old_location': old_location,
            'new_location': ''
        })
    except Exception as e:
        try:
            if conn is not None:
                conn.rollback()
        except Exception:
            pass
        return jsonify({'success': False, 'error': ss_errors._safe_error(e)}), 500
    finally:
        try:
            if conn is not None:
                conn.close()
        except Exception:
            pass


def _ready_to_ship_poll_state(days):
    conn = sqlite3.connect(str(ss_config.BASE_DIR / 'sold.db'), timeout=30.0)
    try:
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        ensure_sold_orders_schema(cur, conn, default_store='ebay')
        ss_shipping_identity._ensure_ready_to_ship_notes_table(cur)
        cur.execute('''
            SELECT id,
                   COALESCE(TRIM(isHandled), '') AS isHandled,
                   COALESCE(rackupdated, 0) AS rackupdated,
                   COALESCE(barcode, '') AS barcode,
                   COALESCE(location, '') AS location,
                   COALESCE(isHandledDate, '') AS isHandledDate,
                   COALESCE(paid_time, '') AS paid_time,
                   COALESCE(shipped_time, '') AS shipped_time
            FROM orders
            WHERE paid_time >= date('now', '-' || ? || ' days')
            ORDER BY id DESC
        ''', (days,))
        rows = cur.fetchall()
        note_lookup = ss_shipping_identity._load_ready_to_ship_note_lookup(cur, [row['id'] for row in rows])
        label_lookup = ss_shipping_identity._load_ready_to_ship_label_lookup(cur, [row['id'] for row in rows])

        pending_count = 0
        digest = hashlib.sha1()
        for row in rows:
            is_handled = str(row['isHandled'] or '').strip() == '1'
            if not is_handled:
                pending_count += 1
            note_row = note_lookup.get(int(row['id']))
            label_rows = label_lookup.get(int(row['id']), [])
            note_payload = ss_shipping_identity._ready_to_ship_note_payload(note_row, label_rows)
            label_digest = '|'.join(
                f"{str(label.get('label_id') or label.get('id') or '').strip()}:{str(label.get('label_filename') or '').strip()}:{str(label.get('label_uploaded_at') or '').strip()}"
                for label in (note_payload.get('ready_to_ship_labels') or [])
            )
            digest.update(
                (
                    f"{int(row['id'] or 0)}|"
                    f"{1 if is_handled else 0}|"
                    f"{int(row['rackupdated'] or 0)}|"
                    f"{str(row['barcode'] or '').strip()}|"
                    f"{str(row['location'] or '').strip()}|"
                    f"{str(row['isHandledDate'] or '').strip()}|"
                    f"{str(row['paid_time'] or '').strip()}|"
                    f"{str(row['shipped_time'] or '').strip()}|"
                    f"{str(note_payload['ready_to_ship_note'] or '').strip()}|"
                    f"{str(note_payload['ready_to_ship_note_updated_at'] or '').strip()}|"
                    f"{str(note_payload.get('ready_to_ship_label_filename') or '').strip()}|"
                    f"{str(note_payload.get('ready_to_ship_label_uploaded_at') or '').strip()}|"
                    f"{int(note_payload.get('ready_to_ship_label_count') or 0)}|"
                    f"{label_digest}\n"
                ).encode('utf-8', errors='ignore')
            )

        return {
            'count': pending_count,
            'total': len(rows),
            'version': digest.hexdigest(),
        }
    finally:
        conn.close()


def ready_to_ship_count():
    """Return pending count plus a lightweight version fingerprint for cross-session refresh."""
    days = int(request.args.get('days', 2))
    if days < 1:
        days = 1
    elif days > 120:
        days = 120
    try:
        state = _ready_to_ship_poll_state(days)
        return jsonify({'success': True, 'count': state['count'], 'total': state['total'], 'version': state['version'], 'days': days})
    except Exception as e:
        return jsonify({'success': False, 'error': ss_errors._safe_error(e, 'ready_to_ship:count')}), 500


def get_order():
    order_id = request.args.get('id')
    if not order_id:
        return jsonify({'error': 'Missing order id'}), 400
    conn = None
    try:
        conn = sqlite3.connect(str(ss_config.BASE_DIR / 'sold.db'), timeout=30.0)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        ensure_sold_orders_schema(cur, conn, default_store='ebay')
        ss_shipping_identity._ensure_ready_to_ship_notes_table(cur)
        cur.execute("SELECT * FROM orders WHERE id = ?", (order_id,))
        order = cur.fetchone()
        
        if not order:
            return jsonify({'error': 'Order not found'}), 404
            
        # Convert sqlite3.Row to dict
        order_dict = dict(order)
        note_lookup = ss_shipping_identity._load_ready_to_ship_note_lookup(cur, [order_id])
        label_lookup = ss_shipping_identity._load_ready_to_ship_label_lookup(cur, [order_id])
        ss_shipping_identity._apply_ready_to_ship_note_payload(order_dict, note_lookup.get(int(order['id'])), label_lookup.get(int(order['id']), []))
        effective_barcode = ss_warehouse_matching._effective_sold_order_barcode(order_dict, prefer_manual_override=True)
        if effective_barcode:
            order_dict['stored_barcode'] = str(order_dict.get('barcode') or '').strip()
            order_dict['barcode'] = effective_barcode
        return jsonify(order_dict)
    except Exception as e:
        return jsonify({'error': ss_errors._safe_error(e)}), 500
    finally:
        try:
            if conn is not None:
                conn.close()
        except Exception:
            pass


def ready_to_ship_save_note(order_id):
    conn = None
    try:
        data = request.get_json(silent=True) or {}
        raw_note = str(data.get('note') or '')
        note = raw_note.replace('\r\n', '\n').replace('\r', '\n').strip()
        if len(note) > 2000:
            return jsonify({'success': False, 'error': 'Note is too long (max 2000 characters).'}), 400

        conn = sqlite3.connect('sold.db')
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        ss_shipping_identity._ensure_ready_to_ship_notes_table(cur)
        ss_shipping_identity._ensure_ready_to_ship_order_labels_table(cur)

        order = cur.execute('SELECT id FROM orders WHERE id = ?', (order_id,)).fetchone()
        if not order:
            return jsonify({'success': False, 'error': 'Order not found'}), 404

        existing = cur.execute('SELECT * FROM ready_to_ship_notes WHERE order_row_id = ?', (order_id,)).fetchone()
        if note:
            if existing:
                cur.execute('''
                    UPDATE ready_to_ship_notes
                    SET note = ?, updated_at = CURRENT_TIMESTAMP
                    WHERE order_row_id = ?
                ''', (note, order_id))
            else:
                cur.execute('''
                    INSERT INTO ready_to_ship_notes (order_row_id, note)
                    VALUES (?, ?)
                ''', (order_id, note))
        else:
            label_count_row = cur.execute(
                "SELECT COUNT(*) AS c FROM ready_to_ship_order_labels WHERE order_row_id = ? AND COALESCE(TRIM(label_filename), '') <> ''",
                (order_id,)
            ).fetchone()
            label_count = int(ss_shipping_identity._ready_to_ship_row_value(label_count_row, 'c', 0) or 0)
            if existing and (str(ss_shipping_identity._ready_to_ship_row_value(existing, 'label_filename', '') or '').strip() or label_count > 0):
                cur.execute('''
                    UPDATE ready_to_ship_notes
                    SET note = '', updated_at = CURRENT_TIMESTAMP
                    WHERE order_row_id = ?
                ''', (order_id,))
            else:
                cur.execute('DELETE FROM ready_to_ship_notes WHERE order_row_id = ?', (order_id,))

        conn.commit()

        _invalidate_ready_to_ship_cache()

        payload = ss_shipping_identity._ready_to_ship_payload_for_order(cur, order_id)
        payload['success'] = True
        return jsonify(payload)
    except Exception as e:
        return jsonify({'success': False, 'error': ss_errors._safe_error(e, 'ready_to_ship:note')}), 500
    finally:
        try:
            if conn is not None:
                conn.close()
        except Exception:
            pass


def ready_to_ship_upload_label(order_id):
    conn = None
    saved_filename = ''
    try:
        file_storage = request.files.get('label') or request.files.get('file')
        if not file_storage:
            return jsonify({'success': False, 'error': 'PDF label file is required.'}), 400

        saved_filename, original_name, size_bytes, pdf_path = ss_shipping_identity._ready_to_ship_save_uploaded_label_file(file_storage)

        conn = sqlite3.connect('sold.db')
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        ss_shipping_identity._ensure_ready_to_ship_notes_table(cur)

        order = cur.execute('SELECT id FROM orders WHERE id = ?', (order_id,)).fetchone()
        if not order:
            ss_shipping_identity._ready_to_ship_delete_label_file(saved_filename)
            saved_filename = ''
            return jsonify({'success': False, 'error': 'Order not found'}), 404

        ss_shipping_identity._ready_to_ship_attach_label(cur, order_id, saved_filename, original_name, size_bytes)
        conn.commit()
        _invalidate_ready_to_ship_cache()

        payload = ss_shipping_identity._ready_to_ship_payload_for_order(cur, order_id)
        payload['success'] = True
        payload['order_id'] = order_id
        return jsonify(payload)
    except ValueError as e:
        if saved_filename:
            ss_shipping_identity._ready_to_ship_delete_label_file(saved_filename)
        return jsonify({'success': False, 'error': str(e)}), 400
    except Exception as e:
        if saved_filename:
            ss_shipping_identity._ready_to_ship_delete_label_file(saved_filename)
        try:
            if conn is not None:
                conn.rollback()
        except Exception:
            pass
        return jsonify({'success': False, 'error': ss_errors._safe_error(e, 'ready_to_ship:label_upload')}), 500
    finally:
        try:
            if conn is not None:
                conn.close()
        except Exception:
            pass


def ready_to_ship_upload_labels_bulk():
    conn = None
    try:
        files = request.files.getlist('labels') or request.files.getlist('label') or request.files.getlist('file')
        if not files:
            return jsonify({'success': False, 'error': 'Drop at least one PDF label file.'}), 400

        try:
            days = max(1, min(int(request.form.get('days') or 2), 120))
        except Exception:
            days = 2

        conn = sqlite3.connect('sold.db')
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        ss_shipping_identity._ensure_ready_to_ship_notes_table(cur)
        ss_shipping_identity._ensure_ready_to_ship_unmatched_labels_table(cur)

        results = []
        for file_storage in files:
            saved_filename = ''
            original_name = file_storage.filename or 'label.pdf'
            try:
                saved_filename, safe_original, size_bytes, pdf_path = ss_shipping_identity._ready_to_ship_save_uploaded_label_file(file_storage)
                extracted_text = ss_shipping_identity._ready_to_ship_extract_pdf_text(pdf_path)
                match = ss_shipping_identity._ready_to_ship_find_label_match(cur, f"{extracted_text}\n{safe_original}", days=days)
                if not match:
                    unmatched_row = ss_shipping_identity._ready_to_ship_insert_unmatched_label(
                        cur,
                        saved_filename,
                        safe_original,
                        size_bytes,
                        extracted_text,
                        'No address match found'
                    )
                    unmatched_payload = ss_shipping_identity._ready_to_ship_unmatched_label_payload(unmatched_row)
                    saved_filename = ''
                    results.append({
                        'success': True,
                        'matched': False,
                        'saved': True,
                        'filename': original_name,
                        'error': 'No address match found; saved for manual printing.',
                        **unmatched_payload
                    })
                    continue

                order_row = match['order']
                order_id = int(order_row['id'])
                ss_shipping_identity._ready_to_ship_attach_label(cur, order_id, saved_filename, safe_original, size_bytes)
                payload = ss_shipping_identity._ready_to_ship_payload_for_order(cur, order_id)
                payload.update({
                    'success': True,
                    'matched': True,
                    'order_id': order_id,
                    'order_ref': str(ss_shipping_identity._ready_to_ship_row_value(order_row, 'order_id', '') or ''),
                    'title': str(ss_shipping_identity._ready_to_ship_row_value(order_row, 'title', '') or ''),
                    'shipping_name': str(ss_shipping_identity._ready_to_ship_row_value(order_row, 'shipping_name', '') or ''),
                    'score': match['score'],
                    'reasons': match['reasons'],
                    'filename': safe_original
                })
                results.append(payload)
            except ValueError as e:
                if saved_filename:
                    ss_shipping_identity._ready_to_ship_delete_label_file(saved_filename)
                results.append({
                    'success': False,
                    'matched': False,
                    'filename': original_name,
                    'error': str(e)
                })
            except Exception as e:
                if saved_filename:
                    ss_shipping_identity._ready_to_ship_delete_label_file(saved_filename)
                results.append({
                    'success': False,
                    'matched': False,
                    'filename': original_name,
                    'error': ss_errors._safe_error(e, 'ready_to_ship:label_bulk_file')
                })

        conn.commit()
        _invalidate_ready_to_ship_cache()
        return jsonify({
            'success': True,
            'results': results,
            'matched_count': sum(1 for r in results if r.get('matched')),
            'unmatched_count': sum(1 for r in results if not r.get('matched'))
        })
    except Exception as e:
        try:
            if conn is not None:
                conn.rollback()
        except Exception:
            pass
        return jsonify({'success': False, 'error': ss_errors._safe_error(e, 'ready_to_ship:labels_upload')}), 500
    finally:
        try:
            if conn is not None:
                conn.close()
        except Exception:
            pass


def ready_to_ship_unmatched_labels():
    conn = None
    try:
        conn = sqlite3.connect('sold.db')
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        ss_shipping_identity._ensure_ready_to_ship_unmatched_labels_table(cur)
        rows = cur.execute('''
            SELECT id, label_filename, label_original_filename, label_size_bytes, label_uploaded_at, match_error, created_at, updated_at
            FROM ready_to_ship_unmatched_labels
            ORDER BY datetime(label_uploaded_at) DESC, id DESC
            LIMIT 100
        ''').fetchall()
        return jsonify({
            'success': True,
            'labels': [ss_shipping_identity._ready_to_ship_unmatched_label_payload(row) for row in rows]
        })
    except Exception as e:
        return jsonify({'success': False, 'error': ss_errors._safe_error(e, 'ready_to_ship:unmatched_labels')}), 500
    finally:
        try:
            if conn is not None:
                conn.close()
        except Exception:
            pass


def ready_to_ship_delete_all_labels():
    """Manually purge every Ready to Ship label record and stored PDF."""
    conn = None
    try:
        label_dir = ss_shipping_identity._ready_to_ship_label_dir().resolve()
        filenames = {
            path.name
            for path in label_dir.iterdir()
            if path.is_file() and path.suffix.lower() == '.pdf'
        }

        conn = sqlite3.connect('sold.db')
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        ss_shipping_identity._ensure_ready_to_ship_order_labels_table(cur)
        ss_shipping_identity._ensure_ready_to_ship_unmatched_labels_table(cur)

        attached_rows = cur.execute('''
            SELECT label_filename
            FROM ready_to_ship_order_labels
            WHERE COALESCE(TRIM(label_filename), '') <> ''
        ''').fetchall()
        unmatched_rows = cur.execute('''
            SELECT label_filename
            FROM ready_to_ship_unmatched_labels
            WHERE COALESCE(TRIM(label_filename), '') <> ''
        ''').fetchall()
        legacy_rows = cur.execute('''
            SELECT label_filename
            FROM ready_to_ship_notes
            WHERE COALESCE(TRIM(label_filename), '') <> ''
        ''').fetchall()

        for row in [*attached_rows, *unmatched_rows, *legacy_rows]:
            filename = str(ss_shipping_identity._ready_to_ship_row_value(row, 'label_filename', '') or '').strip()
            if filename:
                filenames.add(filename)

        cur.execute('DELETE FROM ready_to_ship_order_labels')
        attached_deleted = max(0, int(cur.rowcount or 0))
        cur.execute('DELETE FROM ready_to_ship_unmatched_labels')
        unmatched_deleted = max(0, int(cur.rowcount or 0))
        cur.execute('''
            UPDATE ready_to_ship_notes
            SET label_filename = '',
                label_original_filename = '',
                label_size_bytes = 0,
                label_uploaded_at = '',
                updated_at = CURRENT_TIMESTAMP
            WHERE COALESCE(TRIM(label_filename), '') <> ''
               OR COALESCE(TRIM(label_original_filename), '') <> ''
               OR COALESCE(label_size_bytes, 0) <> 0
               OR COALESCE(TRIM(label_uploaded_at), '') <> ''
        ''')
        cur.execute("DELETE FROM ready_to_ship_notes WHERE COALESCE(TRIM(note), '') = ''")
        conn.commit()

        files_deleted = 0
        files_failed = 0
        for filename in sorted(filenames):
            path = ss_shipping_identity._ready_to_ship_label_path(filename)
            existed = bool(path and path.exists())
            ss_shipping_identity._ready_to_ship_delete_label_file(filename)
            if existed:
                if path and not path.exists():
                    files_deleted += 1
                else:
                    files_failed += 1

        _invalidate_ready_to_ship_cache()
        return jsonify({
            'success': True,
            'attached_deleted': attached_deleted,
            'unmatched_deleted': unmatched_deleted,
            'records_deleted': attached_deleted + unmatched_deleted,
            'files_deleted': files_deleted,
            'files_failed': files_failed,
        })
    except Exception as e:
        try:
            if conn is not None:
                conn.rollback()
        except Exception:
            pass
        return jsonify({'success': False, 'error': ss_errors._safe_error(e, 'ready_to_ship:labels_delete_all')}), 500
    finally:
        try:
            if conn is not None:
                conn.close()
        except Exception:
            pass


def ready_to_ship_delete_unmatched_label(label_id):
    conn = None
    try:
        conn = sqlite3.connect('sold.db')
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        ss_shipping_identity._ensure_ready_to_ship_unmatched_labels_table(cur)
        row = cur.execute('''
            SELECT id, label_filename
            FROM ready_to_ship_unmatched_labels
            WHERE id = ?
        ''', (label_id,)).fetchone()
        if not row:
            return jsonify({'success': False, 'error': 'Unmatched label not found'}), 404

        filename = str(ss_shipping_identity._ready_to_ship_row_value(row, 'label_filename', '') or '').strip()
        cur.execute('DELETE FROM ready_to_ship_unmatched_labels WHERE id = ?', (label_id,))
        conn.commit()
        if filename:
            ss_shipping_identity._ready_to_ship_delete_label_file(filename)
        return jsonify({'success': True, 'label_id': label_id, 'unmatched_label_id': label_id})
    except Exception as e:
        try:
            if conn is not None:
                conn.rollback()
        except Exception:
            pass
        return jsonify({'success': False, 'error': ss_errors._safe_error(e, 'ready_to_ship:unmatched_label_delete')}), 500
    finally:
        try:
            if conn is not None:
                conn.close()
        except Exception:
            pass


def ready_to_ship_unmatched_label_file(label_id):
    conn = None
    try:
        conn = sqlite3.connect('sold.db')
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        ss_shipping_identity._ensure_ready_to_ship_unmatched_labels_table(cur)
        row = cur.execute('''
            SELECT label_filename, label_original_filename
            FROM ready_to_ship_unmatched_labels
            WHERE id = ?
        ''', (label_id,)).fetchone()
        filename = str(ss_shipping_identity._ready_to_ship_row_value(row, 'label_filename', '') or '').strip()
        path = ss_shipping_identity._ready_to_ship_label_path(filename)
        if not path or not path.exists():
            return jsonify({'success': False, 'error': 'Label file not found'}), 404
        download_name = str(ss_shipping_identity._ready_to_ship_row_value(row, 'label_original_filename', '') or '').strip() or f'ready_to_ship_unmatched_{label_id}.pdf'
        return send_file(str(path), mimetype='application/pdf', as_attachment=False, download_name=download_name)
    except Exception as e:
        return jsonify({'success': False, 'error': ss_errors._safe_error(e, 'ready_to_ship:unmatched_label_file')}), 500
    finally:
        try:
            if conn is not None:
                conn.close()
        except Exception:
            pass


def ready_to_ship_unmatched_label_print(label_id):
    conn = None
    try:
        conn = sqlite3.connect('sold.db')
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        ss_shipping_identity._ensure_ready_to_ship_unmatched_labels_table(cur)
        row = cur.execute('SELECT label_filename FROM ready_to_ship_unmatched_labels WHERE id = ?', (label_id,)).fetchone()
        filename = str(ss_shipping_identity._ready_to_ship_row_value(row, 'label_filename', '') or '').strip()
        path = ss_shipping_identity._ready_to_ship_label_path(filename)
        if not path or not path.exists():
            return jsonify({'success': False, 'error': 'Label file not found'}), 404
        return jsonify({
            'success': True,
            'label_url': f'/api/ready-to-ship/labels/unmatched/{label_id}/file'
        })
    except Exception as e:
        return jsonify({'success': False, 'error': ss_errors._safe_error(e, 'ready_to_ship:unmatched_label_print')}), 500
    finally:
        try:
            if conn is not None:
                conn.close()
        except Exception:
            pass


def ready_to_ship_delete_label(order_id):
    conn = None
    try:
        conn = sqlite3.connect('sold.db')
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        ss_shipping_identity._ensure_ready_to_ship_order_labels_table(cur)

        existing = cur.execute('SELECT * FROM ready_to_ship_notes WHERE order_row_id = ?', (order_id,)).fetchone()
        label_rows = cur.execute('''
            SELECT id, label_filename
            FROM ready_to_ship_order_labels
            WHERE order_row_id = ?
              AND COALESCE(TRIM(label_filename), '') <> ''
        ''', (order_id,)).fetchall()
        if not existing and not label_rows:
            return jsonify({'success': False, 'error': 'No label is attached to this order.'}), 404

        filenames = [
            str(ss_shipping_identity._ready_to_ship_row_value(row, 'label_filename', '') or '').strip()
            for row in label_rows
        ]
        legacy_filename = str(ss_shipping_identity._ready_to_ship_row_value(existing, 'label_filename', '') or '').strip() if existing else ''
        if legacy_filename and legacy_filename not in filenames:
            filenames.append(legacy_filename)
        note_text = str(ss_shipping_identity._ready_to_ship_row_value(existing, 'note', '') or '').strip()
        cur.execute('DELETE FROM ready_to_ship_order_labels WHERE order_row_id = ?', (order_id,))
        if note_text:
            cur.execute('''
                UPDATE ready_to_ship_notes
                SET label_filename = '',
                    label_original_filename = '',
                    label_size_bytes = 0,
                    label_uploaded_at = '',
                    updated_at = CURRENT_TIMESTAMP
                WHERE order_row_id = ?
            ''', (order_id,))
        else:
            cur.execute('DELETE FROM ready_to_ship_notes WHERE order_row_id = ?', (order_id,))

        conn.commit()
        for filename in filenames:
            if filename:
                ss_shipping_identity._ready_to_ship_delete_label_file(filename)
        _invalidate_ready_to_ship_cache()

        payload = ss_shipping_identity._ready_to_ship_payload_for_order(cur, order_id)
        payload['success'] = True
        payload['order_id'] = order_id
        return jsonify(payload)
    except Exception as e:
        try:
            if conn is not None:
                conn.rollback()
        except Exception:
            pass
        return jsonify({'success': False, 'error': ss_errors._safe_error(e, 'ready_to_ship:label_delete')}), 500
    finally:
        try:
            if conn is not None:
                conn.close()
        except Exception:
            pass


def ready_to_ship_delete_order_label(order_id, label_id):
    conn = None
    try:
        conn = sqlite3.connect('sold.db')
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        ss_shipping_identity._ensure_ready_to_ship_order_labels_table(cur)

        row = cur.execute('''
            SELECT id, label_filename
            FROM ready_to_ship_order_labels
            WHERE id = ? AND order_row_id = ?
        ''', (label_id, order_id)).fetchone()
        if not row:
            return jsonify({'success': False, 'error': 'Label not found'}), 404

        filename = str(ss_shipping_identity._ready_to_ship_row_value(row, 'label_filename', '') or '').strip()
        cur.execute('DELETE FROM ready_to_ship_order_labels WHERE id = ? AND order_row_id = ?', (label_id, order_id))
        ss_shipping_identity._ready_to_ship_sync_legacy_label_columns(cur, order_id)
        conn.commit()
        if filename:
            ss_shipping_identity._ready_to_ship_delete_label_file(filename)
        _invalidate_ready_to_ship_cache()

        payload = ss_shipping_identity._ready_to_ship_payload_for_order(cur, order_id)
        payload['success'] = True
        payload['order_id'] = order_id
        payload['label_id'] = label_id
        return jsonify(payload)
    except Exception as e:
        try:
            if conn is not None:
                conn.rollback()
        except Exception:
            pass
        return jsonify({'success': False, 'error': ss_errors._safe_error(e, 'ready_to_ship:order_label_delete')}), 500
    finally:
        try:
            if conn is not None:
                conn.close()
        except Exception:
            pass


def ready_to_ship_label_file(order_id):
    conn = None
    try:
        conn = sqlite3.connect('sold.db')
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        ss_shipping_identity._ensure_ready_to_ship_order_labels_table(cur)
        row = cur.execute('''
            SELECT id, label_filename, label_original_filename
            FROM ready_to_ship_order_labels
            WHERE order_row_id = ?
              AND COALESCE(TRIM(label_filename), '') <> ''
            ORDER BY datetime(label_uploaded_at) DESC, id DESC
            LIMIT 1
        ''', (order_id,)).fetchone()
        if not row:
            row = cur.execute('''
                SELECT 0 AS id, label_filename, label_original_filename
                FROM ready_to_ship_notes
                WHERE order_row_id = ?
            ''', (order_id,)).fetchone()
        filename = str(ss_shipping_identity._ready_to_ship_row_value(row, 'label_filename', '') or '').strip()
        path = ss_shipping_identity._ready_to_ship_label_path(filename)
        if not path or not path.exists():
            return jsonify({'success': False, 'error': 'Label file not found'}), 404
        download_name = str(ss_shipping_identity._ready_to_ship_row_value(row, 'label_original_filename', '') or '').strip() or f'ready_to_ship_{order_id}.pdf'
        return send_file(str(path), mimetype='application/pdf', as_attachment=False, download_name=download_name)
    except Exception as e:
        return jsonify({'success': False, 'error': ss_errors._safe_error(e, 'ready_to_ship:label_file')}), 500
    finally:
        try:
            if conn is not None:
                conn.close()
        except Exception:
            pass


def ready_to_ship_order_label_file(order_id, label_id):
    conn = None
    try:
        conn = sqlite3.connect('sold.db')
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        ss_shipping_identity._ensure_ready_to_ship_order_labels_table(cur)
        row = cur.execute('''
            SELECT id, label_filename, label_original_filename
            FROM ready_to_ship_order_labels
            WHERE id = ? AND order_row_id = ?
        ''', (label_id, order_id)).fetchone()
        filename = str(ss_shipping_identity._ready_to_ship_row_value(row, 'label_filename', '') or '').strip()
        path = ss_shipping_identity._ready_to_ship_label_path(filename)
        if not path or not path.exists():
            return jsonify({'success': False, 'error': 'Label file not found'}), 404
        download_name = str(ss_shipping_identity._ready_to_ship_row_value(row, 'label_original_filename', '') or '').strip() or f'ready_to_ship_{order_id}_{label_id}.pdf'
        return send_file(str(path), mimetype='application/pdf', as_attachment=False, download_name=download_name)
    except Exception as e:
        return jsonify({'success': False, 'error': ss_errors._safe_error(e, 'ready_to_ship:order_label_file')}), 500
    finally:
        try:
            if conn is not None:
                conn.close()
        except Exception:
            pass


def ready_to_ship_label_print(order_id):
    conn = None
    try:
        conn = sqlite3.connect('sold.db')
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        ss_shipping_identity._ensure_ready_to_ship_order_labels_table(cur)
        row = cur.execute('''
            SELECT id, label_filename
            FROM ready_to_ship_order_labels
            WHERE order_row_id = ?
              AND COALESCE(TRIM(label_filename), '') <> ''
            ORDER BY datetime(label_uploaded_at) DESC, id DESC
            LIMIT 1
        ''', (order_id,)).fetchone()
        if not row:
            row = cur.execute('SELECT 0 AS id, label_filename FROM ready_to_ship_notes WHERE order_row_id = ?', (order_id,)).fetchone()
        filename = str(ss_shipping_identity._ready_to_ship_row_value(row, 'label_filename', '') or '').strip()
        path = ss_shipping_identity._ready_to_ship_label_path(filename)
        if not path or not path.exists():
            return jsonify({'success': False, 'error': 'Label file not found'}), 404
        label_id = ss_normalization._coerce_int(ss_shipping_identity._ready_to_ship_row_value(row, 'id', 0), 0)
        return jsonify({
            'success': True,
            'label_url': f'/api/ready-to-ship/label/{order_id}/file/{label_id}' if label_id else f'/api/ready-to-ship/label/{order_id}/file'
        })
    except Exception as e:
        return jsonify({'success': False, 'error': ss_errors._safe_error(e, 'ready_to_ship:label_print')}), 500
    finally:
        try:
            if conn is not None:
                conn.close()
        except Exception:
            pass


def ready_to_ship_order_label_print(order_id, label_id):
    conn = None
    try:
        conn = sqlite3.connect('sold.db')
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        ss_shipping_identity._ensure_ready_to_ship_order_labels_table(cur)
        row = cur.execute('''
            SELECT label_filename
            FROM ready_to_ship_order_labels
            WHERE id = ? AND order_row_id = ?
        ''', (label_id, order_id)).fetchone()
        filename = str(ss_shipping_identity._ready_to_ship_row_value(row, 'label_filename', '') or '').strip()
        path = ss_shipping_identity._ready_to_ship_label_path(filename)
        if not path or not path.exists():
            return jsonify({'success': False, 'error': 'Label file not found'}), 404
        return jsonify({
            'success': True,
            'label_url': f'/api/ready-to-ship/label/{order_id}/file/{label_id}'
        })
    except Exception as e:
        return jsonify({'success': False, 'error': ss_errors._safe_error(e, 'ready_to_ship:order_label_print')}), 500
    finally:
        try:
            if conn is not None:
                conn.close()
        except Exception:
            pass


def ready_to_ship_location_options(order_id):
    sold_conn = None
    rack_conn = None
    try:
        sold_conn = sqlite3.connect('sold.db')
        sold_conn.row_factory = sqlite3.Row
        sold_cur = sold_conn.cursor()
        ss_shipping_identity._ensure_order_removal_allocations_table(sold_cur)

        sold_cur.execute('''
            SELECT id, order_id, item_id, sku, store, barcode, source_upc,
                   quantity, title, location, listing_listing_id, listing_sku,
                   listing_asin
            FROM orders WHERE id = ?
        ''', (order_id,))
        order = sold_cur.fetchone()
        if not order:
            return jsonify({'success': False, 'error': 'Order not found'}), 404

        barcode = ss_warehouse_matching._effective_sold_order_barcode(order, prefer_manual_override=True)
        suggested_barcode = str(request.args.get('matched_barcode') or '').strip()
        valid_fallback_barcode = (
            suggested_barcode
            if ss_warehouse_matching._ready_to_ship_valid_requested_match(order, suggested_barcode)
            else ''
        )
        sold_qty = max(1, ss_normalization._coerce_int(order['quantity'], 1))
        existing_allocations = ss_warehouse_allocations._load_order_removal_allocations(sold_cur, order_id)

        if not barcode:
            return jsonify({
                'success': True,
                'order_id': order_id,
                'barcode': '',
                'quantity': sold_qty,
                'needs_choice': False,
                'locations': [],
                'existing_allocations': existing_allocations,
                'total_available': 0,
                'can_fulfill': False
            })

        rack_conn = sqlite3.connect('searchRack.db')
        rack_conn.row_factory = sqlite3.Row
        rack_cur = rack_conn.cursor()

        raw_stored_barcode = str(order['barcode'] or '').strip()
        fallback_barcodes = []
        if raw_stored_barcode and raw_stored_barcode != barcode:
            fallback_barcodes.append(raw_stored_barcode)

        matches = ss_warehouse_matching._searchrack_matches_for_removal_context(
            rack_cur,
            barcode,
            fallback_barcodes=fallback_barcodes,
            preferred_location=str(order['location'] or '').strip()
        )
        if not matches and valid_fallback_barcode:
            barcode = valid_fallback_barcode
            matches = ss_warehouse_matching._searchrack_matches_for_removal_context(
                rack_cur,
                barcode,
                preferred_location=str(order['location'] or '').strip()
            )

        location_groups = ss_warehouse_matching._group_searchrack_matches_by_location(matches)
        locations = [
            {
                'location_key': g['location_key'],
                'location_code': g['location_code'],
                'available_qty': max(0, ss_normalization._coerce_int(g['available_qty'], 0)),
                'preview_key': g['preview_key']
            }
            for g in location_groups
        ]
        total_available = sum(max(0, ss_normalization._coerce_int(x['available_qty'], 0)) for x in locations)

        return jsonify({
            'success': True,
            'order_id': order_id,
            'order_ref': order['order_id'],
            'title': order['title'],
            'barcode': barcode,
            'quantity': sold_qty,
            'needs_choice': len(locations) > 1,
            'locations': locations,
            'existing_allocations': existing_allocations,
            'total_available': total_available,
            'can_fulfill': total_available >= sold_qty
        })
    except Exception as e:
        return jsonify({'success': False, 'error': ss_errors._safe_error(e)}), 500
    finally:
        try:
            if sold_conn is not None:
                sold_conn.close()
        except Exception:
            pass
        try:
            if rack_conn is not None:
                rack_conn.close()
        except Exception:
            pass


def ready_to_ship_order_stats(order_id):
    sold_conn = None
    try:
        sold_conn = sqlite3.connect('sold.db')
        sold_conn.row_factory = sqlite3.Row
        sold_cur = sold_conn.cursor()
        sold_cur.execute('SELECT id, item_id, sku, store, barcode, source_upc, quantity, title FROM orders WHERE id = ?', (order_id,))
        order = sold_cur.fetchone()
        if not order:
            return jsonify({'success': False, 'error': 'Order not found'}), 404

        barcode = ss_warehouse_matching._effective_sold_order_barcode(order, prefer_manual_override=True)
        prep_stats = ss_shipping_identity._ready_to_ship_items_to_list_stats_for_barcode(barcode)
        return jsonify({
            'success': True,
            'order_id': order_id,
            'barcode': barcode,
            'base_barcode': prep_stats.get('base_barcode') or '',
            'title': str(order['title'] or '').strip(),
            'sold_quantity': max(1, ss_normalization._coerce_int(order['quantity'], 1)),
            'warehouse_qty': ss_shipping_identity._ready_to_ship_warehouse_qty_for_barcode(barcode),
            'macy_total_qty': ss_shipping_identity._ready_to_ship_rawbol_total_qty_for_barcode(barcode),
            'sold_count': ss_shipping_identity._ready_to_ship_sold_count_for_barcode(barcode),
            'prepped_qty': max(0, ss_normalization._coerce_int(prep_stats.get('prepped_qty'), 0)),
            'items_to_list_url': str(prep_stats.get('items_to_list_url') or '')
        })
    except Exception as e:
        return jsonify({'success': False, 'error': ss_errors._safe_error(e)}), 500
    finally:
        try:
            if sold_conn is not None:
                sold_conn.close()
        except Exception:
            pass
