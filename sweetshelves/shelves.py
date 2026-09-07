"""Shelves for Sweet Shelves."""

import datetime
import os
import sqlite3
import time
from flask import has_app_context, jsonify, request, url_for
from . import caching as ss_caching, config as ss_config, database as ss_database, errors as ss_errors, normalization as ss_normalization, shelf_assets as ss_shelf_assets


def api_check_shelf_code():
    """Check if a shelf code already exists"""
    data = request.get_json() or {}
    code, code_error = ss_shelf_assets._validate_shelf_code_input(data.get('code'))
    
    if code_error:
        return jsonify({'exists': True, 'valid': False, 'reason': code_error}), 200
    
    conn = None
    try:
        conn = sqlite3.connect('searchRack.db')
        cur = conn.cursor()
        result = ss_shelf_assets._shelf_code_exists(cur, code)
        
        if result:
            return jsonify({
                'exists': True,
                'valid': False,
                'reason': f'Shelf "{code}" already exists'
            }), 200
        else:
            return jsonify({
                'exists': False,
                'valid': True,
                'reason': 'Code available'
            }), 200
            
    except Exception as e:
        print(f"Error checking shelf code: {e}")
        return jsonify({'exists': False, 'reason': 'Error checking code'}), 500
    finally:
        if conn is not None:
            conn.close()


def api_create_shelf():
    """Create a new shelf entry. Expects JSON: { shelf_name, location, notes, group_id (optional, defaults to 1) }"""
    data = request.get_json() or {}
    shelf_name = (data.get('shelf_name') or '').strip()
    location = (data.get('location') or '').strip()
    notes = (data.get('notes') or '').strip()
    group_id = data.get('group_id', 1)  # Default to group 1
    
    shelf_name, code_error = ss_shelf_assets._validate_shelf_code_input(shelf_name)
    if code_error:
        return jsonify({'success': False, 'error': code_error}), 400
    
    conn = None
    try:
        ensure_shelf_groups_table()
        # Store shelves in a simple table in searchRack.db
        conn = sqlite3.connect('searchRack.db')
        cur = conn.cursor()
        cur.execute('BEGIN IMMEDIATE')
        if ss_shelf_assets._shelf_code_exists(cur, shelf_name):
            return jsonify({'success': False, 'error': f'Shelf "{shelf_name}" already exists'}), 409
        cur.execute('SELECT id FROM shelf_groups WHERE id = ?', (group_id,))
        if not cur.fetchone():
            return jsonify({'success': False, 'error': 'Selected group does not exist'}), 404
        
        # Insert the new shelf with group_id
        cur.execute('''
            INSERT INTO shelves (shelf_name, location, notes, group_id)
            VALUES (?, ?, ?, ?)
        ''', (shelf_name, location, notes, group_id))
        
        conn.commit()
        shelf_id = cur.lastrowid
        
        return jsonify({
            'success': True,
            'message': f'Shelf "{shelf_name}" created successfully',
            'shelf_id': shelf_id
        })
    
    except Exception as e:
        return jsonify({'success': False, 'error': ss_errors._safe_error(e)}), 500
    finally:
        if conn is not None:
            conn.close()


def api_update_shelf():
    """Update an existing shelf image and/or rename the shelf code.
    Expects form-data: old_code (required), new_code (optional), image (optional file)
    """
    conn = None
    file_snapshots = {}
    affected_paths = set()
    try:
        old_code = ss_shelf_assets._safe_shelf_lookup_code(request.form.get('old_code'))
        requested_new_code = str(request.form.get('new_code') or '').strip()
        if not old_code:
            return jsonify({'success': False, 'error': 'A valid old_code is required'}), 400
        if requested_new_code:
            target_code, code_error = ss_shelf_assets._validate_shelf_code_input(requested_new_code)
            if code_error:
                return jsonify({'success': False, 'error': code_error}), 400
        else:
            target_code = old_code

        file = request.files.get('image')
        orig_file = request.files.get('original_image')
        for upload in (file, orig_file):
            if upload and upload.filename and not str(upload.mimetype or '').casefold().startswith('image/'):
                return jsonify({'success': False, 'error': 'Only image uploads are allowed'}), 400

        ensure_shelf_groups_table()
        conn = sqlite3.connect('searchRack.db', timeout=30.0)
        cur = conn.cursor()
        cur.execute('BEGIN IMMEDIATE')
        existing_row = ss_shelf_assets._shelf_code_exists(cur, old_code)
        if requested_new_code and ss_shelf_assets._shelf_code_exists(cur, target_code, exclude_code=old_code):
            conn.rollback()
            return jsonify({'success': False, 'error': f'Shelf "{target_code}" already exists'}), 409

        old_path = ss_shelf_assets._resolve_exact_shelf_display_path(old_code, existing_only=True)
        old_orig_path = ss_shelf_assets._resolve_exact_shelf_original_path(old_code, existing_only=True)
        target_path = ss_shelf_assets._resolve_exact_shelf_display_path(target_code, existing_only=False)
        target_orig_path = ss_shelf_assets._resolve_exact_shelf_original_path(target_code, existing_only=False)
        if target_path is None or target_orig_path is None:
            conn.rollback()
            return jsonify({'success': False, 'error': 'Could not resolve safe shelf image paths'}), 400
        if requested_new_code and target_path.exists() and target_path != old_path:
            conn.rollback()
            return jsonify({'success': False, 'error': f'An image already exists for shelf "{target_code}"'}), 409
        if requested_new_code and target_orig_path.exists() and target_orig_path != old_orig_path:
            conn.rollback()
            return jsonify({'success': False, 'error': f'An original image already exists for shelf "{target_code}"'}), 409

        for path in (old_path, old_orig_path, target_path, target_orig_path):
            if path is None:
                continue
            affected_paths.add(path)
            if path.exists():
                file_snapshots[path] = path.read_bytes()

        if existing_row:
            cur.execute(
                'UPDATE shelves SET shelf_name = ? WHERE id = ?',
                (target_code, existing_row[0])
            )
        else:
            cur.execute(
                'INSERT INTO shelves (shelf_name, group_id, created_at) VALUES (?, 1, CURRENT_TIMESTAMP)',
                (target_code,)
            )

        if target_code.casefold() != old_code.casefold():
            cur.execute('''
                UPDATE SEARCHRACK
                SET ITEM_POSITION = CASE
                        WHEN LOWER(TRIM(COALESCE(ITEM_POSITION, ''))) = LOWER(TRIM(?)) THEN ?
                        ELSE ITEM_POSITION
                    END,
                    PICTUREPOSITION = CASE
                        WHEN LOWER(TRIM(COALESCE(PICTUREPOSITION, ''))) = LOWER(TRIM(?)) THEN ?
                        ELSE PICTUREPOSITION
                    END
                WHERE LOWER(TRIM(COALESCE(ITEM_POSITION, ''))) = LOWER(TRIM(?))
                   OR LOWER(TRIM(COALESCE(PICTUREPOSITION, ''))) = LOWER(TRIM(?))
            ''', (old_code, target_code, old_code, target_code, old_code, old_code))

        original_only = ss_shelf_assets._truthy_form_value(request.form.get('original_only'))
        saved_display = False
        if file and file.filename and (not original_only or old_path is None):
            target_path.parent.mkdir(parents=True, exist_ok=True)
            ss_shelf_assets._save_uploaded_shelf_png(file, target_path)
            saved_display = True
        elif target_code.casefold() != old_code.casefold() and old_path and old_path.exists():
            target_path.parent.mkdir(parents=True, exist_ok=True)
            os.replace(str(old_path), str(target_path))

        if orig_file and orig_file.filename:
            target_orig_path.parent.mkdir(parents=True, exist_ok=True)
            ss_shelf_assets._save_uploaded_shelf_png(orig_file, target_orig_path)
        elif target_code.casefold() != old_code.casefold() and old_orig_path and old_orig_path.exists():
            target_orig_path.parent.mkdir(parents=True, exist_ok=True)
            os.replace(str(old_orig_path), str(target_orig_path))

        if target_code.casefold() != old_code.casefold():
            if saved_display and old_path and old_path.exists() and old_path != target_path:
                old_path.unlink()
            if orig_file and orig_file.filename and old_orig_path and old_orig_path.exists() and old_orig_path != target_orig_path:
                old_orig_path.unlink()

        conn.commit()
        try:
            if saved_display and target_path.exists():
                ss_shelf_assets._sync_preview_base_image(target_code, target_path)
            elif target_orig_path.exists():
                ss_shelf_assets._sync_original_to_preview_base(target_code)
            if target_code.casefold() != old_code.casefold():
                ss_shelf_assets._delete_preview_base_image(old_code)
        except Exception as preview_error:
            print(f'Warning: shelf preview sync failed for {target_code}: {preview_error}')
        ss_caching._invalidate_searchrack_cache()
        return jsonify({'success': True, 'code': target_code})
    except Exception as e:
        if conn is not None:
            try:
                conn.rollback()
            except Exception:
                pass
        for path in affected_paths:
            try:
                if path in file_snapshots:
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(file_snapshots[path])
                elif path.exists():
                    path.unlink()
            except Exception as restore_error:
                print(f'Failed to restore shelf file {path}: {restore_error}')
        status = 400 if isinstance(e, ValueError) else 500
        return jsonify({'success': False, 'error': ss_errors._safe_error(e)}), status
    finally:
        if conn is not None:
            conn.close()


def api_clear_shelf_inventory(code):
    """Clear an exact shelf code from current and legacy inventory locations."""
    conn = None
    sconn = None
    try:
        code = ss_shelf_assets._safe_shelf_lookup_code(code)
        if not code:
            return jsonify({'success': False, 'error': 'A valid shelf code is required'}), 400

        sconn = sqlite3.connect('searchRack.db', timeout=30.0)
        scur = sconn.cursor()
        scur.execute('BEGIN IMMEDIATE')
        scur.execute('''
            UPDATE SEARCHRACK
            SET ITEM_POSITION = CASE
                    WHEN LOWER(TRIM(COALESCE(ITEM_POSITION, ''))) = LOWER(TRIM(?)) THEN ''
                    ELSE ITEM_POSITION
                END,
                PICTUREPOSITION = CASE
                    WHEN LOWER(TRIM(COALESCE(PICTUREPOSITION, ''))) = LOWER(TRIM(?)) THEN ''
                    ELSE PICTUREPOSITION
                END
            WHERE LOWER(TRIM(COALESCE(ITEM_POSITION, ''))) = LOWER(TRIM(?))
               OR LOWER(TRIM(COALESCE(PICTUREPOSITION, ''))) = LOWER(TRIM(?))
        ''', (code, code, code, code))
        search_updated = scur.rowcount
        sconn.commit()

        # Legacy rack.db is best-effort compatibility; searchRack.db is the
        # authoritative warehouse database.
        legacy_updated = 0
        try:
            conn = sqlite3.connect('rack.db', timeout=10.0)
            cur = conn.cursor()
            cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
            tables = [str(row[0]) for row in cur.fetchall()]
            table_name = next(
                (name for name in tables if name.casefold() in ('items', 'inventory')),
                None
            )
            if table_name:
                cur.execute(f'PRAGMA table_info({ss_database._sqlite_ident(table_name)})')
                columns = [str(row[1]) for row in cur.fetchall()]
                location_columns = [
                    column for column in columns
                    if column.casefold() in ('item_position', 'itemposition', 'position', 'location', 'pictureposition')
                ]
                for column in location_columns:
                    cur.execute(
                        f'''UPDATE {ss_database._sqlite_ident(table_name)}
                            SET {ss_database._sqlite_ident(column)} = ''
                            WHERE LOWER(TRIM(COALESCE({ss_database._sqlite_ident(column)}, ''))) = LOWER(TRIM(?))''',
                        (code,)
                    )
                    legacy_updated += cur.rowcount
                conn.commit()
        except Exception as legacy_error:
            print(f'Warning: legacy rack.db shelf clear failed for {code}: {legacy_error}')

        ss_caching._invalidate_searchrack_cache()
        return jsonify({
            'success': True,
            'updated': search_updated,
            'search_updated': search_updated,
            'legacy_updated': legacy_updated
        })
    except Exception as e:
        if sconn is not None:
            try:
                sconn.rollback()
            except Exception:
                pass
        return jsonify({'success': False, 'error': ss_errors._safe_error(e)}), 500
    finally:
        if conn is not None:
            conn.close()
        if sconn is not None:
            sconn.close()


def api_list_shelves():
    """Return the union of shelf metadata and exact shelf image files."""
    try:
        sort = (request.args.get('sort') or 'created').lower()
        results = []
        files = ss_shelf_assets._iter_shelf_display_files()
        file_map = {path.stem.casefold().strip(): path for path in files if path.stem.strip()}
        metadata_map = {}
        counts_map = {}

        ensure_shelf_groups_table()
        with ss_database.db_connection('searchRack.db') as conn:
            cur = conn.cursor()
            cur.execute('SELECT shelf_name, created_at, group_id FROM shelves ORDER BY id')
            for row in cur.fetchall():
                code = str(row['shelf_name'] or '').strip()
                if code and code.casefold() not in metadata_map:
                    metadata_map[code.casefold()] = {
                        'code': code,
                        'created_at': row['created_at'],
                        'group_id': row['group_id']
                    }

            # Count physical units once per distinct location key on each row.
            cur.execute('''
                SELECT ITEM_POSITION, PICTUREPOSITION,
                       COALESCE(CAST(QUANTITY AS INTEGER), 0) AS quantity
                FROM SEARCHRACK
                WHERE COALESCE(CAST(QUANTITY AS INTEGER), 0) > 0
            ''')
            for row in cur.fetchall():
                qty = max(0, ss_normalization._coerce_int(row['quantity'], 0))
                keys = {
                    str(row['ITEM_POSITION'] or '').strip().casefold(),
                    str(row['PICTUREPOSITION'] or '').strip().casefold()
                }
                for key in keys:
                    if key:
                        counts_map[key] = counts_map.get(key, 0) + qty

        for normalized_code in sorted(set(file_map) | set(metadata_map)):
            path = file_map.get(normalized_code)
            metadata = metadata_map.get(normalized_code, {})
            code = str(metadata.get('code') or (path.stem if path else '')).strip()
            if not code:
                continue
            mtime = 0
            cache_version = ''
            if path is not None:
                try:
                    stat_info = path.stat()
                    mtime = stat_info.st_mtime
                    cache_version = str(getattr(stat_info, 'st_mtime_ns', int(mtime * 1000000000)))
                except Exception:
                    cache_version = str(int(time.time() * 1000000000))
            results.append({
                'code': code,
                'filename': path.name if path else '',
                'url': url_for('shelf_image', code=code) if path else '',
                'has_image': bool(path),
                'lastModified': int(mtime),
                'cacheVersion': cache_version,
                'created_at': metadata.get('created_at'),
                'count': int(counts_map.get(normalized_code, 0)),
                'group_id': metadata.get('group_id') or 1
            })

        if sort == 'items':
            results.sort(key=lambda x: x.get('count', 0), reverse=True)
        elif sort == 'created':
            def created_key(item):
                created_at = item.get('created_at')
                if created_at:
                    return str(created_at).replace('T', ' ')
                try:
                    return datetime.datetime.fromtimestamp(
                        item.get('lastModified', 0)
                    ).strftime('%Y-%m-%d %H:%M:%S')
                except Exception:
                    return '1970-01-01 00:00:00'
            results.sort(key=created_key, reverse=True)
        else:
            results.sort(key=lambda x: (x.get('code') or '').casefold())
        return jsonify({'success': True, 'shelves': results})
    except Exception as e:
        return jsonify({'success': False, 'error': ss_errors._safe_error(e)}), 500


def api_shelf_counts():
    """Return physical-unit counts for shelf codes across both location columns."""
    sconn = None
    try:
        codes = request.get_json() or {}
        codes = codes.get('codes', []) if isinstance(codes, dict) else codes
        if not isinstance(codes, list):
            return jsonify({'success': False, 'error': 'codes must be a list'}), 400

        result = {}
        sconn = sqlite3.connect('searchRack.db')
        scur = sconn.cursor()
        for code in codes:
            scur.execute('''
                SELECT COALESCE(SUM(COALESCE(CAST(QUANTITY AS INTEGER), 0)), 0)
                FROM SEARCHRACK
                WHERE COALESCE(CAST(QUANTITY AS INTEGER), 0) > 0
                  AND (
                    LOWER(TRIM(COALESCE(ITEM_POSITION, ''))) = LOWER(TRIM(?))
                    OR LOWER(TRIM(COALESCE(PICTUREPOSITION, ''))) = LOWER(TRIM(?))
                  )
            ''', (code, code))
            row = scur.fetchone()
            result[code] = {'searchrack': int(row[0] or 0) if row else 0}

        return jsonify({'success': True, 'counts': result})
    except Exception as e:
        return jsonify({'success': False, 'error': ss_errors._safe_error(e)}), 500
    finally:
        if sconn is not None:
            sconn.close()


def ensure_shelf_groups_table():
    """Ensure shelf_groups table exists and shelves table has group_id column"""
    conn = None
    try:
        if has_app_context():
            with ss_database.db_connection('searchRack.db', row_factory=False) as conn_ctx:
                cur = conn_ctx.cursor()

                # Create shelves table if it doesn't exist
                cur.execute('''
                    CREATE TABLE IF NOT EXISTS shelves (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        shelf_name TEXT NOT NULL,
                        location TEXT,
                        notes TEXT,
                        created_at TEXT DEFAULT CURRENT_TIMESTAMP
                    )
                ''')

                # Create shelf_groups table if it doesn't exist
                cur.execute('''
                    CREATE TABLE IF NOT EXISTS shelf_groups (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        name TEXT NOT NULL,
                        created_at TEXT DEFAULT CURRENT_TIMESTAMP
                    )
                ''')

                # Check if shelves table has group_id column
                cur.execute('PRAGMA table_info(shelves)')
                columns = [row[1] for row in cur.fetchall()]

                if 'group_id' not in columns:
                    # Add group_id column (defaults to NULL, meaning ungrouped)
                    cur.execute('ALTER TABLE shelves ADD COLUMN group_id INTEGER')
                    print('[SHELF_GROUPS] Added group_id column to shelves table')

                # Ensure default group exists (id=1, name="Default Group")
                cur.execute('SELECT id FROM shelf_groups WHERE id = 1')
                if not cur.fetchone():
                    cur.execute('INSERT INTO shelf_groups (id, name) VALUES (1, ?)', ('Default Group',))
                    print('[SHELF_GROUPS] Created default group')
        else:
            conn = sqlite3.connect(str(ss_config.BASE_DIR / 'searchRack.db'), timeout=30.0)
            cur = conn.cursor()

            cur.execute('''
                CREATE TABLE IF NOT EXISTS shelves (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    shelf_name TEXT NOT NULL,
                    location TEXT,
                    notes TEXT,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            cur.execute('''
                CREATE TABLE IF NOT EXISTS shelf_groups (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            cur.execute('PRAGMA table_info(shelves)')
            columns = [row[1] for row in cur.fetchall()]
            if 'group_id' not in columns:
                cur.execute('ALTER TABLE shelves ADD COLUMN group_id INTEGER')
                print('[SHELF_GROUPS] Added group_id column to shelves table')
            cur.execute('SELECT id FROM shelf_groups WHERE id = 1')
            if not cur.fetchone():
                cur.execute('INSERT INTO shelf_groups (id, name) VALUES (1, ?)', ('Default Group',))
                print('[SHELF_GROUPS] Created default group')
            conn.commit()
        return True
    except Exception as e:
        print(f'[SHELF_GROUPS] Error ensuring tables: {e}')
        import traceback
        traceback.print_exc()
        return False
    finally:
        if conn is not None:
            conn.close()


def api_get_groups():
    """Get all shelf groups with shelf counts"""
    conn = None
    try:
        ensure_shelf_groups_table()
        conn = sqlite3.connect('searchRack.db')
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        
        # Get all groups with shelf counts
        cur.execute('''
            SELECT 
                g.id,
                g.name,
                g.created_at,
                COUNT(CASE WHEN s.id = (
                    SELECT MIN(s2.id)
                    FROM shelves s2
                    WHERE LOWER(TRIM(s2.shelf_name)) = LOWER(TRIM(s.shelf_name))
                ) THEN 1 END) as shelf_count
            FROM shelf_groups g
            LEFT JOIN shelves s ON s.group_id = g.id
            GROUP BY g.id
            ORDER BY g.id
        ''')
        
        groups = [dict(row) for row in cur.fetchall()]
        
        return jsonify({'success': True, 'groups': groups})
    except Exception as e:
        print(f'[api_get_groups] Error: {e}')
        return jsonify({'success': False, 'error': ss_errors._safe_error(e)}), 500
    finally:
        if conn is not None:
            conn.close()


def api_create_group():
    """Create a new shelf group"""
    conn = None
    try:
        data = request.get_json() or {}
        name = (data.get('name') or '').strip()
        
        if not name:
            return jsonify({'success': False, 'error': 'Group name is required'}), 400
        if len(name) > 80:
            return jsonify({'success': False, 'error': 'Group name must be 80 characters or fewer'}), 400
        
        ensure_shelf_groups_table()
        conn = sqlite3.connect('searchRack.db')
        cur = conn.cursor()
        cur.execute('BEGIN IMMEDIATE')
        cur.execute(
            'SELECT id FROM shelf_groups WHERE LOWER(TRIM(name)) = LOWER(TRIM(?)) LIMIT 1',
            (name,)
        )
        if cur.fetchone():
            return jsonify({'success': False, 'error': 'A group with that name already exists'}), 409
        
        cur.execute('INSERT INTO shelf_groups (name) VALUES (?)', (name,))
        group_id = cur.lastrowid
        
        conn.commit()
        
        return jsonify({
            'success': True,
            'group_id': group_id,
            'message': f'Group "{name}" created successfully'
        })
    except Exception as e:
        return jsonify({'success': False, 'error': ss_errors._safe_error(e)}), 500
    finally:
        if conn is not None:
            conn.close()


def api_delete_group():
    """Delete a shelf group and move its shelves to Default Group (id=1)"""
    conn = None
    try:
        data = request.get_json() or {}
        try:
            group_id = int(data.get('group_id'))
        except (TypeError, ValueError):
            group_id = 0
        
        if not group_id:
            return jsonify({'success': False, 'error': 'group_id is required'}), 400
        
        if group_id == 1:
            return jsonify({'success': False, 'error': 'Cannot delete Default Group'}), 400
        
        ensure_shelf_groups_table()
        conn = sqlite3.connect('searchRack.db')
        cur = conn.cursor()
        
        # Move all shelves from this group to Default Group (id=1)
        cur.execute('UPDATE shelves SET group_id = 1 WHERE group_id = ?', (group_id,))
        moved_count = cur.rowcount
        
        # Delete the group
        cur.execute('DELETE FROM shelf_groups WHERE id = ?', (group_id,))
        if cur.rowcount == 0:
            conn.rollback()
            return jsonify({'success': False, 'error': 'Group not found'}), 404
        
        conn.commit()
        
        return jsonify({
            'success': True,
            'moved_shelves': moved_count,
            'message': f'Group deleted. {moved_count} shelf(es) moved to Default Group.'
        })
    except Exception as e:
        return jsonify({'success': False, 'error': ss_errors._safe_error(e)}), 500
    finally:
        if conn is not None:
            conn.close()


def api_move_shelves():
    """Move shelves to a different group. Accepts shelf_codes (list of shelf names) and group_id (or target_group_id)."""
    conn = None
    try:
        data = request.get_json() or {}
        shelf_codes = data.get('shelf_codes', [])
        target_group_id = data.get('group_id') or data.get('target_group_id')
        
        if not isinstance(shelf_codes, list) or not shelf_codes or target_group_id is None:
            return jsonify({'success': False, 'error': 'shelf_codes and group_id are required'}), 400
        try:
            target_group_id = int(target_group_id)
        except (TypeError, ValueError):
            return jsonify({'success': False, 'error': 'group_id must be an integer'}), 400
        normalized_codes = []
        seen_codes = set()
        for value in shelf_codes:
            code = ss_shelf_assets._safe_shelf_lookup_code(value)
            if not code:
                return jsonify({'success': False, 'error': 'Every shelf code must be valid'}), 400
            key = code.casefold()
            if key not in seen_codes:
                normalized_codes.append(code)
                seen_codes.add(key)
        
        ensure_shelf_groups_table()
        conn = sqlite3.connect('searchRack.db')
        cur = conn.cursor()
        
        # Verify target group exists
        cur.execute('SELECT id FROM shelf_groups WHERE id = ?', (target_group_id,))
        if not cur.fetchone():
            return jsonify({'success': False, 'error': 'Target group does not exist'}), 404
        
        # For each shelf code, ensure it exists in the shelves table (create if missing)
        moved_count = 0
        for code in normalized_codes:
            cur.execute(
                'SELECT id FROM shelves WHERE LOWER(TRIM(shelf_name)) = LOWER(TRIM(?)) LIMIT 1',
                (code,)
            )
            row = cur.fetchone()
            if not row:
                # Shelf doesn't exist in table, create it
                cur.execute('INSERT INTO shelves (shelf_name, group_id) VALUES (?, ?)', (code, target_group_id))
                print(f'[MOVE_SHELVES] Created missing shelf entry for {code}')
                moved_count += 1
            else:
                cur.execute(
                    'UPDATE shelves SET group_id = ? '
                    'WHERE LOWER(TRIM(shelf_name)) = LOWER(TRIM(?))',
                    (target_group_id, code)
                )
                moved_count += 1
        
        conn.commit()
        
        return jsonify({
            'success': True,
            'moved_count': moved_count,
            'message': f'{moved_count} shelf(es) moved successfully'
        })
    except Exception as e:
        if conn is not None:
            try:
                conn.rollback()
            except Exception:
                pass
        return jsonify({'success': False, 'error': ss_errors._safe_error(e)}), 500
    finally:
        if conn is not None:
            conn.close()


def api_upload_shelf():
    """Upload a shelf image and associate it with a group"""
    conn = None
    created_paths = []
    try:
        group_id = request.form.get('group_id', 1)  # Default to group 1
        try:
            group_id = int(group_id)
        except Exception:
            group_id = 1
        
        if 'image' not in request.files:
            return jsonify({'success': False, 'error': 'No image file provided'}), 400
        
        file = request.files['image']
        if not file.filename:
            return jsonify({'success': False, 'error': 'Empty filename'}), 400
        if not str(file.mimetype or '').casefold().startswith('image/'):
            return jsonify({'success': False, 'error': 'Only image uploads are allowed'}), 400
        
        # Use provided code or generate a unique shelf code
        requested_code = request.form.get('code')
        if not requested_code:
            import uuid
            requested_code = str(uuid.uuid4())[:8].upper()
        shelf_code, code_error = ss_shelf_assets._validate_shelf_code_input(requested_code)
        if code_error:
            return jsonify({'success': False, 'error': code_error}), 400

        original_only = ss_shelf_assets._truthy_form_value(request.form.get('original_only'))
        ensure_shelf_groups_table()
        conn = sqlite3.connect('searchRack.db', timeout=30.0)
        cur = conn.cursor()
        cur.execute('BEGIN IMMEDIATE')
        if ss_shelf_assets._shelf_code_exists(cur, shelf_code):
            conn.rollback()
            return jsonify({'success': False, 'error': f'Shelf "{shelf_code}" already exists'}), 409
        cur.execute('SELECT id FROM shelf_groups WHERE id = ?', (group_id,))
        if not cur.fetchone():
            conn.rollback()
            return jsonify({'success': False, 'error': 'Selected group does not exist'}), 404

        target_display_path = ss_shelf_assets._resolve_exact_shelf_display_path(shelf_code, existing_only=False)
        orig_path = ss_shelf_assets._resolve_exact_shelf_original_path(shelf_code, existing_only=False)
        if target_display_path is None or orig_path is None:
            conn.rollback()
            return jsonify({'success': False, 'error': 'Could not resolve safe shelf image paths'}), 400
        if any(path.exists() for path in ss_shelf_assets._shelf_exact_display_path_candidates(shelf_code)):
            conn.rollback()
            return jsonify({'success': False, 'error': f'An image already exists for shelf "{shelf_code}"'}), 409

        cur.execute('''
            INSERT INTO shelves (shelf_name, group_id, created_at)
            VALUES (?, ?, CURRENT_TIMESTAMP)
        ''', (shelf_code, group_id))

        target_display_path.parent.mkdir(parents=True, exist_ok=True)
        ss_shelf_assets._save_uploaded_shelf_png(file, target_display_path)
        created_paths.append(target_display_path)
        orig_file = request.files.get('original_image')
        if orig_file and orig_file.filename:
            if not str(orig_file.mimetype or '').casefold().startswith('image/'):
                raise ValueError('Only image uploads are allowed')
            orig_path.parent.mkdir(parents=True, exist_ok=True)
            ss_shelf_assets._save_uploaded_shelf_png(orig_file, orig_path)
            created_paths.append(orig_path)

        conn.commit()
        try:
            if not original_only or target_display_path.exists():
                ss_shelf_assets._sync_preview_base_image(shelf_code, target_display_path)
            elif orig_path.exists():
                ss_shelf_assets._sync_original_to_preview_base(shelf_code)
        except Exception as preview_error:
            print(f'Warning: shelf preview sync failed for {shelf_code}: {preview_error}')
        
        return jsonify({
            'success': True,
            'code': shelf_code,
            'message': f'Shelf {shelf_code} uploaded successfully'
        })
    except Exception as e:
        if conn is not None:
            try:
                conn.rollback()
            except Exception:
                pass
        for path in created_paths:
            try:
                path.unlink(missing_ok=True)
            except Exception:
                pass
        status = 400 if isinstance(e, ValueError) else 500
        return jsonify({'success': False, 'error': ss_errors._safe_error(e)}), status
    finally:
        if conn is not None:
            conn.close()


def api_delete_shelf():
    """Delete an empty shelf, staging exact files until the DB commit succeeds."""
    conn = None
    staged_paths = []
    try:
        data = request.get_json() or {}
        shelf_id = data.get('shelf_id')
        shelf_code = ss_shelf_assets._safe_shelf_lookup_code(data.get('code'))

        if not shelf_id and not shelf_code:
            return jsonify({'success': False, 'error': 'shelf_id or code is required'}), 400

        conn = sqlite3.connect('searchRack.db', timeout=30.0)
        cur = conn.cursor()
        cur.execute('BEGIN IMMEDIATE')
        code_to_delete = None

        if shelf_id:
            cur.execute('SELECT shelf_name FROM shelves WHERE id = ?', (shelf_id,))
            row = cur.fetchone()
            if row:
                code_to_delete = row[0]
            else:
                conn.rollback()
                return jsonify({'success': False, 'error': 'Shelf not found'}), 404
        else:
            cur.execute(
                'SELECT id, shelf_name FROM shelves WHERE LOWER(TRIM(shelf_name)) = LOWER(TRIM(?)) LIMIT 1',
                (shelf_code,)
            )
            row = cur.fetchone()
            if row:
                shelf_id = row[0]
                code_to_delete = row[1]
            else:
                code_to_delete = shelf_code

        cur.execute('''
            SELECT COUNT(*), COALESCE(SUM(COALESCE(CAST(QUANTITY AS INTEGER), 0)), 0)
            FROM SEARCHRACK
            WHERE COALESCE(CAST(QUANTITY AS INTEGER), 0) > 0
              AND (
                  LOWER(TRIM(COALESCE(ITEM_POSITION, ''))) = LOWER(TRIM(?))
                  OR LOWER(TRIM(COALESCE(PICTUREPOSITION, ''))) = LOWER(TRIM(?))
              )
        ''', (code_to_delete, code_to_delete))
        active_count, active_units = cur.fetchone()
        if active_count:
            conn.rollback()
            return jsonify({
                'success': False,
                'error': (
                    f'Shelf {code_to_delete} still contains {int(active_units or 0)} active unit(s). '
                    'Move or remove those items before deleting the shelf.'
                ),
                'active_items': int(active_count or 0),
                'active_units': int(active_units or 0)
            }), 409

        # Rename exact shelf files to hidden recovery names before changing the
        # database. This avoids both ghost records and irreversible partial deletes.
        import uuid
        exact_paths = (
            ss_shelf_assets._shelf_exact_display_path_candidates(code_to_delete)
            + ss_shelf_assets._shelf_exact_original_path_candidates(code_to_delete)
        )
        preview_path = ss_shelf_assets._preview_base_path_for_code(code_to_delete)
        if preview_path is not None:
            exact_paths.append(preview_path)
        seen_paths = set()
        for path in exact_paths:
            if path in seen_paths or not path.exists():
                continue
            seen_paths.add(path)
            staged = path.with_name(f'.{path.name}.delete-{uuid.uuid4().hex}')
            os.replace(str(path), str(staged))
            staged_paths.append((path, staged))

        if shelf_id:
            cur.execute('DELETE FROM shelves WHERE id = ?', (shelf_id,))
        conn.commit()
        cleanup_warnings = []
        for _, staged in staged_paths:
            try:
                staged.unlink(missing_ok=True)
            except Exception as cleanup_error:
                cleanup_warnings.append(str(cleanup_error))
        ss_caching._invalidate_searchrack_cache()
        return jsonify({
            'success': True,
            'message': f'Shelf {code_to_delete} deleted successfully',
            'cleanup_pending': bool(cleanup_warnings)
        })
    except Exception as e:
        if conn is not None:
            try:
                conn.rollback()
            except Exception:
                pass
        for original, staged in reversed(staged_paths):
            try:
                if staged.exists():
                    original.parent.mkdir(parents=True, exist_ok=True)
                    os.replace(str(staged), str(original))
            except Exception as restore_error:
                print(f'Failed to restore staged shelf file {staged}: {restore_error}')
        return jsonify({'success': False, 'error': ss_errors._safe_error(e)}), 500
    finally:
        if conn is not None:
            conn.close()


def api_valid_shelves():
    """Get list of valid shelf codes from the database"""
    conn = None
    try:
        ensure_shelf_groups_table()
        conn = sqlite3.connect('searchRack.db')
        cur = conn.cursor()
        cur.execute('SELECT shelf_name FROM shelves ORDER BY shelf_name')
        rows = cur.fetchall()
        
        codes = [row[0] for row in rows if row[0]]
        
        return jsonify({
            'success': True,
            'codes': codes
        })
    except Exception as e:
        return jsonify({'success': False, 'error': ss_errors._safe_error(e), 'codes': []}), 500
    finally:
        if conn is not None:
            conn.close()


def api_location_hierarchy():
    """Get location hierarchy with item counts for searchRack inventory"""
    conn = None
    try:
        ensure_shelf_groups_table()
        conn = sqlite3.connect('searchRack.db')
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        
        # Get all groups with their shelves and item counts (exclude Default Group)
        cur.execute('''
            SELECT 
                g.id as group_id,
                g.name as group_name,
                s.id as shelf_id,
                s.shelf_name as shelf_code,
                COUNT(DISTINCT sr.ID) as item_count,
                COALESCE(SUM(CAST(sr.QUANTITY as INTEGER)), 0) as total_quantity
            FROM shelf_groups g
            LEFT JOIN shelves s ON s.group_id = g.id
            LEFT JOIN SEARCHRACK sr
              ON (
                    LOWER(TRIM(COALESCE(sr.ITEM_POSITION, ''))) = LOWER(TRIM(s.shelf_name))
                    OR LOWER(TRIM(COALESCE(sr.PICTUREPOSITION, ''))) = LOWER(TRIM(s.shelf_name))
                 )
             AND COALESCE(CAST(sr.QUANTITY AS INTEGER), 0) > 0
            WHERE g.name != 'Default Group' OR g.name IS NULL
            GROUP BY g.id, s.id
            ORDER BY s.shelf_name
        ''')
        
        rows = cur.fetchall()
        
        # Organize into hierarchy
        hierarchy = {}
        for row in rows:
            group_id = row['group_id']
            group_name = row['group_name']
            
            if group_id not in hierarchy:
                hierarchy[group_id] = {
                    'id': group_id,
                    'name': group_name,
                    'total_items': 0,
                    'total_quantity': 0,
                    'shelves': []
                }
            
            if row['shelf_id']:
                shelf_info = {
                    'id': row['shelf_id'],
                    'code': row['shelf_code'],
                    'item_count': row['item_count'] or 0,
                    'quantity': row['total_quantity'] or 0
                }
                hierarchy[group_id]['shelves'].append(shelf_info)
                hierarchy[group_id]['total_items'] += shelf_info['item_count']
                hierarchy[group_id]['total_quantity'] += shelf_info['quantity']
        
        # Convert to list and sort by total_quantity descending
        locations = sorted(hierarchy.values(), key=lambda x: x['total_quantity'], reverse=True)
        
        return jsonify({
            'success': True,
            'locations': locations
        })
    except Exception as e:
        print(f'[api_location_hierarchy] Error: {e}')
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': ss_errors._safe_error(e)}), 500
    finally:
        if conn is not None:
            conn.close()


def api_duplicate_shelf():
    """Duplicate shelf metadata plus its display and clean original images."""
    conn = None
    created_paths = []
    try:
        data = request.get_json() or {}
        code = ss_shelf_assets._safe_shelf_lookup_code(data.get('code'))
        
        if not code:
            return jsonify({'success': False, 'error': 'A valid code is required'}), 400

        conn = sqlite3.connect('searchRack.db', timeout=30.0)
        cur = conn.cursor()
        cur.execute('BEGIN IMMEDIATE')
        
        cur.execute(
            'SELECT id, shelf_name, group_id FROM shelves '
            'WHERE LOWER(TRIM(shelf_name)) = LOWER(TRIM(?)) LIMIT 1',
            (code,)
        )
        row = cur.fetchone()
        if not row:
            conn.rollback()
            return jsonify({'success': False, 'error': 'Shelf not found'}), 404
            
        _, source_name, group_id = row
        import re
        import shutil

        match = re.search(r'dupe(\d+)$', source_name, re.IGNORECASE)
        if match:
            base_name_prefix = source_name[:match.start()]
            counter = int(match.group(1)) + 1
        else:
            base_name_prefix = source_name
            counter = 1

        while True:
            suffix = f'dupe{counter}'
            new_name = f'{base_name_prefix[:max(1, 80 - len(suffix))]}{suffix}'
            _, code_error = ss_shelf_assets._validate_shelf_code_input(new_name)
            if code_error:
                conn.rollback()
                return jsonify({'success': False, 'error': code_error}), 400
            target_images_exist = any(
                path.exists()
                for path in (
                    ss_shelf_assets._shelf_exact_display_path_candidates(new_name)
                    + ss_shelf_assets._shelf_exact_original_path_candidates(new_name)
                )
            )
            if not ss_shelf_assets._shelf_code_exists(cur, new_name) and not target_images_exist:
                break
            counter += 1

        src_path = ss_shelf_assets._resolve_exact_shelf_display_path(source_name, existing_only=True)
        dst_path = ss_shelf_assets._resolve_exact_shelf_display_path(new_name, existing_only=False)
        if src_path is None or not src_path.exists():
            conn.rollback()
            return jsonify({'success': False, 'error': 'Source image not found'}), 404
        if dst_path is None:
            conn.rollback()
            return jsonify({'success': False, 'error': 'Could not resolve a safe destination'}), 400

        cur.execute(
            'INSERT INTO shelves (shelf_name, group_id, created_at) VALUES (?, ?, CURRENT_TIMESTAMP)',
            (new_name, group_id or 1)
        )
        dst_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(str(src_path), str(dst_path))
        os.utime(str(dst_path), None)
        created_paths.append(dst_path)

        src_orig = ss_shelf_assets._resolve_exact_shelf_original_path(source_name, existing_only=True)
        dst_orig = ss_shelf_assets._resolve_exact_shelf_original_path(new_name, existing_only=False)
        if src_orig is not None and src_orig.exists() and dst_orig is not None:
            dst_orig.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(str(src_orig), str(dst_orig))
            os.utime(str(dst_orig), None)
            created_paths.append(dst_orig)

        conn.commit()
        try:
            if dst_orig is not None and dst_orig.exists():
                ss_shelf_assets._sync_preview_base_image(new_name, dst_orig)
            else:
                ss_shelf_assets._sync_preview_base_image(new_name, dst_path)
        except Exception as preview_error:
            print(f'Warning: duplicated shelf preview sync failed for {new_name}: {preview_error}')
        return jsonify({
            'success': True, 
            'new_code': new_name,
            'message': f'Shelf duplicated as {new_name}'
        })
        
    except Exception as e:
        if conn is not None:
            try:
                conn.rollback()
            except Exception:
                pass
        for path in created_paths:
            try:
                path.unlink(missing_ok=True)
            except Exception:
                pass
        return jsonify({'success': False, 'error': ss_errors._safe_error(e)}), 500
    finally:
        if conn is not None:
            conn.close()
