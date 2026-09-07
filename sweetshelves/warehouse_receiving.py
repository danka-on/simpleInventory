"""Warehouse receiving for Sweet Shelves."""

import json
import os
import sqlite3
from contextlib import closing
from DBmanager import addToSearchRack
from flask import jsonify, make_response, redirect, render_template, request, session, url_for
from . import (
    caching as ss_caching, config as ss_config, database as ss_database, errors as ss_errors,
    inventory_age as ss_inventory_age, inventory_history as ss_inventory_history, listing_lifecycle as
    ss_listing_lifecycle, normalization as ss_normalization, prep_context as ss_prep_context, prep_schema
    as ss_prep_schema, runtime as ss_runtime, warehouse_maps as ss_warehouse_maps,
)


def item_prep_create_item_page():
    """Page for creating custom items with auto-generated 777 barcodes"""
    return render_template('item_prep_create_item.html')


def _ensure_custom_item_registry(cur):
    cur.execute('''
        CREATE TABLE IF NOT EXISTS custom_item_registry (
            upc TEXT PRIMARY KEY COLLATE NOCASE,
            item_description TEXT NOT NULL DEFAULT '',
            image_url TEXT NOT NULL DEFAULT '',
            reserved_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    columns = {row[1] for row in cur.execute('PRAGMA table_info(custom_item_registry)')}
    for column in ('title_override', 'image_override'):
        if column not in columns:
            cur.execute(f'ALTER TABLE custom_item_registry ADD COLUMN {column} INTEGER NOT NULL DEFAULT 0')
    cur.execute('CREATE INDEX IF NOT EXISTS idx_custom_item_registry_updated ON custom_item_registry(updated_at)')
    cur.execute('''
        CREATE TABLE IF NOT EXISTS custom_item_registry_meta (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
    ''')
    # Recover pre-registry warehouse names once, including ordinary UPCs.
    if not cur.execute("SELECT 1 FROM custom_item_registry_meta WHERE key = 'warehouse_names_v2'").fetchone():
        try:
            with ss_database.db_connection('searchRack.db') as rack_conn:
                rows = rack_conn.execute("SELECT BARCODE, TITLE FROM SEARCHRACK WHERE CUSTOM_TITLE = 1 AND TRIM(COALESCE(TITLE, '')) != '' ORDER BY ID DESC").fetchall()
            for barcode, title in rows:
                key = ss_normalization._normalize_upc(barcode)
                if key:
                    cur.execute("INSERT OR IGNORE INTO custom_item_registry (upc, item_description) VALUES (?, ?)", (key, title))
            cur.execute("INSERT OR REPLACE INTO custom_item_registry_meta (key, value) VALUES ('warehouse_names_v2', CURRENT_TIMESTAMP)")
        except sqlite3.OperationalError:
            # An older installation may not yet have CUSTOM_TITLE; retry later.
            pass
    migrated = cur.execute(
        "SELECT value FROM custom_item_registry_meta WHERE key = 'legacy_backfill_v1'"
    ).fetchone()
    if migrated:
        return

    recovered = {}
    try:
        with ss_database.db_connection('searchRack.db') as rack_conn:
            rack_conn.row_factory = sqlite3.Row
            rack_cur = rack_conn.cursor()
            rack_cur.execute('''
                SELECT BARCODE, TITLE, COALESCE(IMAGE, '') AS IMAGE
                FROM SEARCHRACK
                WHERE BARCODE LIKE '777%'
            ''')
            for row in rack_cur.fetchall():
                upc = str(row['BARCODE'] or '').strip()
                if upc:
                    recovered[upc] = (
                        str(row['TITLE'] or '').strip(),
                        str(row['IMAGE'] or '').strip()
                    )
    except Exception:
        pass

    try:
        with ss_database.db_connection('rackhistory.db') as history_conn:
            history_conn.row_factory = sqlite3.Row
            history_cur = history_conn.cursor()
            history_cur.execute('''
                SELECT barcode, title, source_row_json
                FROM removed_items
                WHERE barcode LIKE '777%'
                ORDER BY id ASC
            ''')
            for row in history_cur.fetchall():
                upc = str(row['barcode'] or '').strip()
                if not upc:
                    continue
                title = str(row['title'] or '').strip()
                image = ''
                try:
                    snapshot = json.loads(row['source_row_json'] or '{}')
                    if isinstance(snapshot, dict):
                        title = str(snapshot.get('TITLE') or snapshot.get('title') or title).strip()
                        image = str(snapshot.get('IMAGE') or snapshot.get('image') or '').strip()
                except Exception:
                    pass
                previous = recovered.get(upc, ('', ''))
                recovered[upc] = (title or previous[0], image or previous[1])
    except Exception:
        pass

    for upc, (title, image) in recovered.items():
        cur.execute('''
            INSERT OR IGNORE INTO custom_item_registry (
                upc, item_description, image_url, reserved_at, updated_at
            ) VALUES (?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
        ''', (upc, title, image))
    cur.execute('''
        INSERT OR REPLACE INTO custom_item_registry_meta (key, value)
        VALUES ('legacy_backfill_v1', CURRENT_TIMESTAMP)
    ''')


def generate_custom_barcode():
    """Generate auto-incremented 777 prefix barcode with duplicate protection"""
    conn = None
    try:
        data = request.get_json(silent=True) or {}
        excluded_barcodes = {
            str(code or '').strip()
            for code in (data.get('exclude_barcodes') or data.get('exclude') or [])
            if str(code or '').strip()
        }

        conn = sqlite3.connect('bol.db')
        cur = conn.cursor()
        cur.execute('BEGIN IMMEDIATE')
        
        # Create temp_items table if not exists
        cur.execute('''
            CREATE TABLE IF NOT EXISTS temp_items (
                upc TEXT PRIMARY KEY,
                item_description TEXT,
                image_url TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        _ensure_custom_item_registry(cur)
        
        # Get the highest 777 barcode from both tables
        cur.execute('''
            SELECT MAX(CAST(upc AS INTEGER)) as max_barcode
            FROM bol_items 
            WHERE upc LIKE '777%' AND LENGTH(upc) = 12
        ''')
        result = cur.fetchone()
        
        # Also check temp_items table
        cur.execute('''
            SELECT MAX(CAST(upc AS INTEGER)) as max_barcode
            FROM temp_items 
            WHERE upc LIKE '777%' AND LENGTH(upc) = 12
        ''')
        temp_result = cur.fetchone()

        cur.execute('''
            SELECT MAX(CAST(upc AS INTEGER)) AS max_barcode
            FROM custom_item_registry
            WHERE upc LIKE '777%' AND LENGTH(upc) = 12
        ''')
        registry_result = cur.fetchone()
        
        # Get the highest barcode from both tables
        max_barcode = result[0] if result[0] else None
        temp_max = temp_result[0] if temp_result[0] else None
        registry_max = registry_result[0] if registry_result and registry_result[0] else None
        
        if temp_max and (not max_barcode or temp_max > max_barcode):
            max_barcode = temp_max
        if registry_max and (not max_barcode or registry_max > max_barcode):
            max_barcode = registry_max
        
        # Generate new barcode with duplicate check
        attempts = 0
        max_attempts = 100
        
        while attempts < max_attempts:
            if max_barcode:
                # Increment by 1
                new_barcode = str(int(max_barcode) + 1)
                # Ensure it still starts with 777
                if not new_barcode.startswith('777'):
                    # Extract the numeric part after 777 and increment
                    numeric_part = int(str(max_barcode)[3:]) + 1
                    new_barcode = f"777{str(numeric_part).zfill(9)}"
            else:
                # Start at 777000000001 (12 digits with 777 prefix)
                new_barcode = '777000000001'
            
            # Ensure it's exactly 12 digits and starts with 777
            if len(new_barcode) < 12:
                # Pad the numeric part after 777
                if new_barcode.startswith('777'):
                    numeric_part = new_barcode[3:]
                    new_barcode = f"777{numeric_part.zfill(9)}"
                else:
                    new_barcode = new_barcode.zfill(12)
            
            # Check if this barcode already exists in either table
            cur.execute('SELECT upc FROM bol_items WHERE upc = ? COLLATE NOCASE', (new_barcode,))
            exists_bol = cur.fetchone()
            
            cur.execute('SELECT upc FROM temp_items WHERE upc = ? COLLATE NOCASE', (new_barcode,))
            exists_temp = cur.fetchone()

            cur.execute('SELECT upc FROM custom_item_registry WHERE upc = ? COLLATE NOCASE', (new_barcode,))
            exists_registry = cur.fetchone()
            
            if not exists_bol and not exists_temp and not exists_registry and new_barcode not in excluded_barcodes:
                # Reserve immediately so a generated code is never issued again,
                # even if the user leaves before adding it to active inventory.
                cur.execute('''
                    INSERT INTO custom_item_registry (upc, reserved_at, updated_at)
                    VALUES (?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                ''', (new_barcode,))
                conn.commit()
                return jsonify({'success': True, 'barcode': new_barcode.strip()})
            
            # Barcode exists, increment and try again
            max_barcode = int(new_barcode)
            attempts += 1
        
        # If we exhausted attempts, return error
        return jsonify({'success': False, 'error': 'Could not generate unique barcode after 100 attempts'}), 500
        
    except Exception as e:
        print(f'Error generating barcode: {e}')
        return jsonify({'success': False, 'error': ss_errors._safe_error(e)}), 500
    finally:
        if conn is not None:
            conn.close()


def save_temp_item():
    """Save custom item directly to bol_items so it appears in Item Manager"""
    conn = None
    try:
        data = request.get_json()
        upc = data.get('upc', '').strip()
        item_description = data.get('item_description', '').strip()
        image_data = data.get('image_data', '')
        
        if not upc or not item_description or not image_data:
            return jsonify({'success': False, 'error': 'Missing required fields'}), 400
        
        # Normalize UPC for consistent storage and caching
        upc_norm = ss_normalization._normalize_upc(upc)
        
        current = _add_item_screening_lookup(upc_norm)
        title_override = int(bool(current.get('title')) and item_description != current['title'])
        # A user-supplied photo is intentional, including when adding a missing image.
        image_override = 1

        # Save image to file
        import base64
        import uuid
        
        # Remove data URL prefix if present
        if image_data.startswith('data:image'):
            image_data = image_data.split(',')[1]
        
        # Decode base64 image
        image_bytes = base64.b64decode(image_data)
        
        # Create directory for custom items if it doesn't exist
        custom_dir = os.path.join('static', 'custom_items')
        os.makedirs(custom_dir, exist_ok=True)
        
        # Save with UPC as filename
        image_filename = f"{upc_norm}.jpg"
        image_path = os.path.join(custom_dir, image_filename)
        
        with open(image_path, 'wb') as f:
            f.write(image_bytes)
        
        # Insert directly into bol_items table
        conn = sqlite3.connect('bol.db')
        cur = conn.cursor()
        _ensure_custom_item_registry(cur)
        
        # Check if item already exists
        cur.execute('SELECT upc FROM bol_items WHERE upc = ? COLLATE NOCASE', (upc_norm,))
        exists = cur.fetchone()
        
        web_image_path = f"/static/custom_items/{image_filename}"

        cur.execute('''
            INSERT INTO custom_item_registry (
                upc, item_description, image_url, title_override, image_override, reserved_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
            ON CONFLICT(upc) DO UPDATE SET
                item_description = excluded.item_description,
                image_url = excluded.image_url,
                title_override = MAX(custom_item_registry.title_override, excluded.title_override),
                image_override = excluded.image_override,
                updated_at = CURRENT_TIMESTAMP
        ''', (upc_norm, item_description, web_image_path, title_override, image_override))
        
        if exists:
            # Update existing item
            cur.execute('''
                UPDATE bol_items 
                SET item_description = ?, image_url = ?
                WHERE upc = ? COLLATE NOCASE
            ''', (item_description, web_image_path, upc_norm))
        else:
            # Insert new item into bol_items
            cur.execute('''
                INSERT INTO bol_items (
                    upc, item_description, image_url, 
                    lot_number, bol_number, import_date
                ) VALUES (?, ?, ?, NULL, 'CUSTOM', datetime('now'))
            ''', (upc_norm, item_description, web_image_path))
        
        conn.commit()
        
        # Clear cache for this UPC using normalized UPC
        cache_key = f"view//api/bol_lookup?upc={upc_norm}"
        ss_runtime.cache.delete(cache_key)
        ss_runtime.cache.delete_memoized(ss_prep_context.api_bol_lookup)
        
        return jsonify({'success': True, 'message': 'Custom item saved to inventory', 'image_url': web_image_path})
    except Exception as e:
        print(f'Error saving custom item: {e}')
        return jsonify({'success': False, 'error': ss_errors._safe_error(e)}), 500
    finally:
        if conn is not None:
            conn.close()


def save_custom_item_identity():
    """Reserve a custom barcode and retain its title independently of active stock."""
    try:
        data = request.get_json(silent=True) or {}
        upc = ss_normalization._normalize_upc(data.get('upc'))
        title = ' '.join(str(data.get('item_description') or data.get('title') or '').split())
        if len(title) > 200 or title.lower() in ('unknown', 'warehouse item', 'item', 'n/a') or title.replace('-', '').isdigit():
            return jsonify({'success': False, 'error': 'Use a short descriptive name, not a barcode or placeholder (maximum 200 characters).'}), 400
        if not upc or not title:
            return jsonify({'success': False, 'error': 'Barcode and title are required'}), 400

        current = _add_item_screening_lookup(upc)
        # A deliberate edit of an existing name stays authoritative on later scans.
        title_override = int(bool(current.get('title')) and title != current['title'])
        with ss_database.db_connection('bol.db') as conn:
            cur = conn.cursor()
            _ensure_custom_item_registry(cur)
            cur.execute('''
                INSERT INTO custom_item_registry (
                    upc, item_description, title_override, reserved_at, updated_at
                ) VALUES (?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                ON CONFLICT(upc) DO UPDATE SET
                    item_description = excluded.item_description,
                    title_override = MAX(custom_item_registry.title_override, excluded.title_override),
                    updated_at = CURRENT_TIMESTAMP
            ''', (upc, title, title_override))
            conn.commit()
        ss_runtime.cache.delete_memoized(ss_prep_context.api_bol_lookup)
        return jsonify({'success': True, 'upc': upc, 'item_description': title})
    except Exception as e:
        return jsonify({'success': False, 'error': ss_errors._safe_error(e, 'custom_item:identity')}), 500


def _clean_warehouse_note(value):
    return str(value or '').replace('\r\n', '\n').replace('\r', '\n').strip()[:2000]


def _load_add_item_warehouse_notes(raw_value):
    notes = {}
    if not raw_value:
        return notes
    try:
        parsed = json.loads(raw_value)
    except Exception:
        return notes
    if not isinstance(parsed, dict):
        return notes
    for raw_key, raw_note in parsed.items():
        key = ss_normalization._normalize_scanned_upc(raw_key)
        note = _clean_warehouse_note(raw_note)
        if key and note:
            notes[key] = note
    return notes


def _warehouse_note_existing_suffixes(cur, base_barcode):
    base = ss_normalization._normalize_scanned_upc(base_barcode)
    if not base:
        return set()
    variants = {base}
    stripped = ss_normalization._strip_leading_zeros_numeric(base)
    if stripped:
        variants.add(stripped)
    if base.isdigit() and len(base) <= 13:
        variants.add(base.zfill(12))
        variants.add(base.zfill(13))
    if stripped and stripped.isdigit() and len(stripped) <= 13:
        variants.add(stripped.zfill(12))
        variants.add(stripped.zfill(13))

    used = set()
    for variant in variants:
        like = f"{variant}-%"
        try:
            cur.execute("SELECT BARCODE FROM SEARCHRACK WHERE BARCODE = ? COLLATE NOCASE OR BARCODE LIKE ? COLLATE NOCASE", (variant, like))
        except Exception:
            continue
        for row in cur.fetchall():
            raw = str(row[0] or '').strip()
            if '-' not in raw:
                continue
            row_base, row_suffix = raw.rsplit('-', 1)
            if not row_suffix.isdigit():
                continue
            if ss_normalization._strip_leading_zeros_numeric(row_base) == ss_normalization._strip_leading_zeros_numeric(base):
                used.add(int(row_suffix))

    # Never recycle a suffix that belonged to a deleted warehouse row; its history remains addressable.
    history_conn = None
    try:
        history_conn = sqlite3.connect(str(ss_config.BASE_DIR / 'rackhistory.db'))
        history_cur = history_conn.cursor()
        ss_inventory_history._ensure_removed_items_table(history_cur)
        for variant in variants:
            history_cur.execute('''
                SELECT DISTINCT barcode
                FROM removed_items
                WHERE barcode = ? COLLATE NOCASE OR barcode LIKE ? COLLATE NOCASE
            ''', (variant, f'{variant}-%'))
            for row in history_cur.fetchall():
                raw = str(row[0] or '').strip()
                if '-' not in raw:
                    continue
                row_base, row_suffix = raw.rsplit('-', 1)
                if row_suffix.isdigit() and (
                    ss_normalization._strip_leading_zeros_numeric(row_base) == ss_normalization._strip_leading_zeros_numeric(base)
                ):
                    used.add(int(row_suffix))
    except Exception:
        pass
    finally:
        if history_conn is not None:
            history_conn.close()
    return used


def _next_warehouse_note_suffix(cur, base_barcode, reserved_suffixes):
    used = _warehouse_note_existing_suffixes(cur, base_barcode) | set(reserved_suffixes or set())
    suffix = 1
    while suffix in used:
        suffix += 1
    return suffix


def additemtrue():
    # Get values from form data (preferred) or fall back to session variables
    form_barcode = request.form.get('barcode', '').strip()
    form_position = request.form.get('item_position', '').strip()
    form_pictureposition = request.form.get('pictureposition', '').strip()
    raw_title_overrides = request.form.get('title_overrides', '').strip()
    warehouse_notes = _load_add_item_warehouse_notes(request.form.get('warehouse_notes', '').strip())

    # Use form data if available, otherwise use session variables
    final_barcode = form_barcode or session.get('inv_barcode')
    final_position = form_position or session.get('inv_position_code')
    final_pictureposition = form_pictureposition or session.get('inv_pictureposition_path')
    same_position = session.get('inv_same_position', False)

    title_overrides = {}
    if raw_title_overrides:
        try:
            parsed = json.loads(raw_title_overrides)
            if isinstance(parsed, dict):
                for raw_key, raw_title in parsed.items():
                    key = ss_normalization._normalize_scanned_upc(raw_key)
                    value = str(raw_title or '').strip()
                    if not key or not value:
                        continue
                    title_overrides[str(key)] = value[:200]
        except Exception:
            title_overrides = {}
    
    # Validate that we have required data
    if not final_barcode:
        print("ERROR: No barcode provided!")
        return "Error: Barcode is required", 400
    
    if not final_position and not final_pictureposition:
        print("ERROR: No position provided!")
        return "Error: Position is required", 400
    
    # Handle multiple barcodes (comma-separated)
    barcodes = [b.strip() for b in final_barcode.split(',') if b.strip()]
    
    try:
        # If a picture position was used, compress and convert to B&W
        if final_pictureposition:
            abs_path = os.path.join(os.getcwd(), final_pictureposition)
            try:
                # Use optimized image loading (memory-efficient for Pi)
                img = ss_warehouse_maps.load_image_efficiently(abs_path, max_size=(400, 400), convert_rgb=False)
                img = img.convert('L')  # Convert to grayscale
                img.save(abs_path, optimize=True, quality=40)
            except Exception as e:
                print(f"Image processing failed: {e}")
        print("Flow Complete, adding to SearchRack....")
        # If picture position is set, store 'picture' in ITEM_POSITION
        item_position_to_store = 'picture' if final_pictureposition else final_position
        
        labels_to_print = []
        reserved_note_suffixes = {}
        # Preserve FIFO receiving dates when a removal is immediately followed
        # by an add, but never wait through the normal 30-second SQLite timeout.
        # If another worker owns the database, the post-save pass (or the next
        # inventory maintenance run) can catch the ledger up.
        try:
            with ss_database.db_connection('searchRack.db') as age_conn:
                age_conn.execute('PRAGMA busy_timeout = 2000')
                ss_inventory_age._reconcile_inventory_age_batches(age_conn)
        except Exception as age_err:
            print(f"Warning: deferred pre-add inventory age reconciliation: {age_err}")
        finally:
            try:
                age_conn.execute('PRAGMA busy_timeout = 30000')
            except Exception:
                pass

        with closing(sqlite3.connect(str(ss_config.BASE_DIR / 'searchRack.db'))) as rack_conn_for_suffix:
            rack_cur_for_suffix = rack_conn_for_suffix.cursor()
            for barcode_item in barcodes:
                normalized_barcode = ss_normalization._normalize_scanned_upc(barcode_item)
                base_barcode = normalized_barcode.split('-', 1)[0] if normalized_barcode else ''
                warehouse_note = (
                    warehouse_notes.get(normalized_barcode)
                    or warehouse_notes.get(base_barcode)
                    or ''
                )
                barcode_to_add = barcode_item
                if warehouse_note and normalized_barcode and '-' not in normalized_barcode:
                    suffix_base_key = ss_normalization._strip_leading_zeros_numeric(base_barcode or normalized_barcode)
                    reserved = reserved_note_suffixes.setdefault(suffix_base_key, set())
                    next_suffix = _next_warehouse_note_suffix(rack_cur_for_suffix, base_barcode or normalized_barcode, reserved)
                    reserved.add(next_suffix)
                    barcode_to_add = f"{normalized_barcode}-{next_suffix}"
                    normalized_barcode = ss_normalization._normalize_scanned_upc(barcode_to_add)
                    labels_to_print.append({
                        'barcode': barcode_to_add,
                        'description': title_overrides.get(base_barcode) or title_overrides.get(normalized_barcode) or 'Warehouse item',
                        'warehouse_note': warehouse_note
                    })
                title_override = (
                    title_overrides.get(normalized_barcode)
                    or title_overrides.get(base_barcode)
                    or None
                )
                metadata = _add_item_screening_lookup(barcode_to_add)
                added = addToSearchRack(
                    item_position_to_store, barcode_to_add, None,
                    final_pictureposition, title_override, warehouse_note, metadata,
                )
                if added is False:
                    raise RuntimeError(f'Inventory database was busy while adding {barcode_to_add}')
                print(f"Added to searchRack: position={item_position_to_store}, barcode={barcode_to_add}, pictureposition={final_pictureposition}")


        # Inventory is already durable at this point. Age-ledger maintenance is
        # secondary and must not turn a successful batch into a 500 response;
        # that made the browser retry the entire list and could duplicate stock.
        try:
            with ss_database.db_connection('searchRack.db') as age_conn:
                age_conn.execute('PRAGMA busy_timeout = 2000')
                ss_inventory_age._reconcile_inventory_age_batches(age_conn)
        except Exception as age_err:
            print(f"Warning: deferred inventory age reconciliation after add: {age_err}")
        finally:
            try:
                age_conn.execute('PRAGMA busy_timeout = 30000')
            except Exception:
                pass

        # SearchRack writes should be visible immediately in /searchrack.
        ss_caching._invalidate_searchrack_cache()
        
        # Clear session variables (but keep position if locked)
        if not same_position:
            session.pop('inv_position_code', None)
            session.pop('inv_pictureposition_path', None)
        session.pop('inv_barcode', None)
    except Exception as e:
        print("something went wrong with adding to RACK", e)
        import traceback
        traceback.print_exc()
        ss_errors._safe_error(e)
        return "An internal error occurred", 500
    # Check if this is a fetch request (multi-scan mode) or form submission
    if request.headers.get('Accept') == '*/*' or request.is_json or 'fetch' in request.headers.get('Sec-Fetch-Mode', ''):
        # Fetch request - return JSON success
        return jsonify({'success': True, 'message': 'Item added successfully', 'labels_to_print': labels_to_print})
    
    # Traditional form submission - return HTML
    # Add script to clear sessionStorage after successful add
    clear_script = '''<script>
        sessionStorage.removeItem('barcode');
        sessionStorage.removeItem('barcode_entries');
        sessionStorage.removeItem('additem_manual_titles');
        sessionStorage.removeItem('additem_manual_title_skips');'''
    # Only clear position if not locked
    if not same_position:
        clear_script += '''
        sessionStorage.removeItem('item_position');
        sessionStorage.removeItem('pictureposition_path');'''
    clear_script += '''
    </script>'''
    if same_position:
        # After successful add with locked shelf, unlock and restart from position page
        session['inv_same_position'] = False  # Unlock on server side
        redirect_script = '''<script>
        // Clear all session storage including lock state
        sessionStorage.removeItem('barcode');
        sessionStorage.removeItem('barcode_entries');
        sessionStorage.removeItem('additem_manual_titles');
        sessionStorage.removeItem('additem_manual_title_skips');
        sessionStorage.removeItem('item_position');
        sessionStorage.removeItem('pictureposition_path');
        sessionStorage.removeItem('positionLocked');
        // Notify server to clear lock state
        fetch('/toggle', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ checked: false })
        }).then(() => {
            window.location.href = '/position';
        }).catch(err => {
            console.error('Error clearing lock:', err);
            window.location.href = '/position';
        });
        </script>'''
        return redirect_script
    else:
        return render_template("position.html") + clear_script


def barcode_page():
    # If redirected from pictureposition, pictureposition_path is already in session
    if request.args.get("pictureposition") == "1":
        # Will be handled by JS below
        pass
    response = make_response(render_template("barcode.html"))
    response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    response.headers['Pragma'] = 'no-cache'
    response.headers['Expires'] = '0'
    return response


def pictures_page():
    return render_template("pictures.html")


def position_page():
    # Add cache-busting for template updates
    response = make_response(render_template("position.html"))
    response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    response.headers['Pragma'] = 'no-cache'
    response.headers['Expires'] = '0'
    return response


def movelocation_page():
    response = make_response(render_template("movelocation.html"))
    response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    response.headers['Pragma'] = 'no-cache'
    response.headers['Expires'] = '0'
    return response


def fba_prep_page():
    response = make_response(render_template("fba_prep.html"))
    response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    response.headers['Pragma'] = 'no-cache'
    response.headers['Expires'] = '0'
    return response


def fba_labels_page():
    response = make_response(render_template("fba_labels.html"))
    response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    response.headers['Pragma'] = 'no-cache'
    response.headers['Expires'] = '0'
    return response


def position_diagnostic():
    """Handle position submission from diagnostic pages."""
    conn = None
    try:
        upc = request.form.get('upc', '').strip()
        location = request.form.get('scanned_result', '').strip()
        pictureposition = request.form.get('pictureposition', '').strip()
        return_url = request.form.get('return_url', '').strip()
        
        if not upc:
            return jsonify({'success': False, 'error': 'Missing UPC'}), 400
        
        upc = ss_normalization._normalize_upc(upc)
        ss_prep_schema._ensure_items_prep_tables()
        
        import datetime
        ts = datetime.datetime.now().isoformat()
        conn = sqlite3.connect('bol.db')
        cur = conn.cursor()
        
        # Check if status exists
        cur.execute('SELECT upc FROM items_prep_status WHERE upc = ? COLLATE NOCASE', (upc,))
        exists = cur.fetchone()
        
        if exists:
            cur.execute('UPDATE items_prep_status SET location=?, pictureposition=?, updated_at=? WHERE upc=?', 
                       (location, pictureposition, ts, upc))
        else:
            cur.execute('INSERT INTO items_prep_status (upc, location, pictureposition, updated_at) VALUES (?,?,?,?)', 
                       (upc, location, pictureposition, ts))
        
        # Get item description for searchRack
        cur.execute('SELECT item_description FROM bol_items WHERE upc = ? COLLATE NOCASE LIMIT 1', (upc,))
        bol_row = cur.fetchone()
        title = bol_row[0] if bol_row else None
        
        conn.commit()
        
        # Update searchRack.db
        if location or pictureposition:
            search_conn = None
            try:
                search_conn = sqlite3.connect('searchRack.db')
                ss_inventory_age._reconcile_inventory_age_batches(search_conn)
                search_cur = search_conn.cursor()
                # Ensure table exists
                search_cur.execute('''
                    CREATE TABLE IF NOT EXISTS SEARCHRACK (
                        ID INTEGER PRIMARY KEY AUTOINCREMENT,
                        TITLE TEXT,
                        BARCODE TEXT,
                        ITEM_POSITION TEXT,
                        IMAGES TEXT,
                        PICTUREPOSITION TEXT,
                        ITEMID TEXT,
                        QUANTITY INTEGER DEFAULT 1,
                        CREATED_AT TEXT
                    )
                ''')
                
                # Picture position items should ALWAYS be separate entries (never update existing)
                # Shelf code items with same barcode should append QTY
                has_picture = pictureposition and pictureposition.strip()
                
                if has_picture:
                    # Picture position: ALWAYS insert new entry (never update existing)
                    search_cur.execute('''
                        INSERT INTO SEARCHRACK (TITLE, BARCODE, ITEM_POSITION, PICTUREPOSITION, CREATED_AT, QUANTITY)
                        VALUES (?, ?, ?, ?, ?, 1)
                    ''', (title, upc, location or 'picture', pictureposition, ts))
                    new_id = search_cur.lastrowid
                    
                    print(f"📦 Add to shelf (picture): {title} - ID: {new_id}, Location: {pictureposition}")
                    
                    # Log to removed_items for history tracking
                    removed_conn = None
                    try:
                        removed_conn = sqlite3.connect('rackhistory.db')
                        removed_cur = removed_conn.cursor()
                        ss_inventory_history._ensure_removed_items_table(removed_cur)
                        removed_cur.execute('''
                            INSERT INTO removed_items 
                            (order_id, barcode, title, quantity_removed, removed_at, searchrack_id,
                             old_quantity, new_quantity, removal_type, item_position, event_status)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending')
                        ''', (None, upc, title, 1, ts, new_id, 0, 1, 'add_to_shelf', pictureposition))
                        removed_conn.commit()
                        print(f"✅ Logged add-to-shelf (picture) to history")
                    except Exception as log_err:
                        print(f"❌ Error logging add-to-shelf to removed_items: {log_err}")
                        import traceback
                        traceback.print_exc()
                    finally:
                        if removed_conn is not None:
                            removed_conn.close()
                else:
                    # Shelf code: Check if entry exists with same barcode and location
                    search_cur.execute('''
                        SELECT ID, QUANTITY FROM SEARCHRACK 
                        WHERE BARCODE = ? COLLATE NOCASE 
                        AND ITEM_POSITION = ? COLLATE NOCASE
                        AND (PICTUREPOSITION IS NULL OR TRIM(PICTUREPOSITION) = '')
                    ''', (upc, location))
                    existing_sr = search_cur.fetchone()
                    
                    if existing_sr:
                        # Update existing shelf entry: increment quantity
                        existing_id, existing_qty = existing_sr
                        new_qty = (existing_qty or 0) + 1
                        search_cur.execute('''
                            UPDATE SEARCHRACK 
                            SET QUANTITY = ?, TITLE = ?, CREATED_AT = ?
                            WHERE ID = ?
                        ''', (new_qty, title, ts, existing_id))
                        
                        print(f"📦 Add to shelf (update): {title} - ID: {existing_id}, Qty: {existing_qty} → {new_qty}, Location: {location}")
                        
                        # Log to removed_items for history tracking
                        try:
                            removed_conn = sqlite3.connect('rackhistory.db')
                            removed_cur = removed_conn.cursor()
                            ss_inventory_history._ensure_removed_items_table(removed_cur)
                            removed_cur.execute('''
                                INSERT INTO removed_items 
                                (order_id, barcode, title, quantity_removed, removed_at, searchrack_id,
                                 old_quantity, new_quantity, removal_type, item_position, event_status)
                                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending')
                            ''', (None, upc, title, 1, ts, existing_id, existing_qty, new_qty, 'add_to_shelf', location))
                            removed_conn.commit()
                            print(f"✅ Logged add-to-shelf (update) to history")
                        except Exception as log_err:
                            print(f"❌ Error logging add-to-shelf update to removed_items: {log_err}")
                            import traceback
                            traceback.print_exc()
                        finally:
                            removed_conn.close()
                    else:
                        # Insert new shelf entry
                        search_cur.execute('''
                            INSERT INTO SEARCHRACK (TITLE, BARCODE, ITEM_POSITION, PICTUREPOSITION, CREATED_AT, QUANTITY)
                            VALUES (?, ?, ?, ?, ?, 1)
                        ''', (title, upc, location, '', ts))
                        new_id = search_cur.lastrowid
                        
                        print(f"📦 Add to shelf (insert): {title} - ID: {new_id}, Location: {location}")
                        
                        # Log to removed_items for history tracking
                        try:
                            removed_conn = sqlite3.connect('rackhistory.db')
                            removed_cur = removed_conn.cursor()
                            ss_inventory_history._ensure_removed_items_table(removed_cur)
                            removed_cur.execute('''
                                INSERT INTO removed_items 
                                (order_id, barcode, title, quantity_removed, removed_at, searchrack_id,
                                 old_quantity, new_quantity, removal_type, item_position, event_status)
                                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending')
                            ''', (None, upc, title, 1, ts, new_id, 0, 1, 'add_to_shelf', location))
                            removed_conn.commit()
                            print(f"✅ Logged add-to-shelf (insert) to history")
                        except Exception as log_err:
                            print(f"❌ Error logging add-to-shelf insert to removed_items: {log_err}")
                            import traceback
                            traceback.print_exc()
                        finally:
                            removed_conn.close()
                
                search_conn.commit()
                ss_inventory_age._reconcile_inventory_age_batches(search_conn)
                ss_caching._invalidate_searchrack_cache()
            except Exception as e:
                print(f'Warning: Failed to update searchRack: {e}')
            finally:
                if search_conn is not None:
                    search_conn.close()
        
        # Redirect back to the diagnostic page
        if return_url:
            return redirect(return_url)
        else:
            return redirect(f'/item-prep/diagnostic?upc={upc}')
    except Exception as e:
        return jsonify({'success': False, 'error': ss_errors._safe_error(e)}), 500
    finally:
        if conn is not None:
            conn.close()


def process_position():
    session['inv_position_code'] = request.form.get('scanned_result')
    session['inv_pictureposition_path'] = request.form.get('pictureposition')
    position_locked_raw = request.form.get('position_locked')
    position_locked = (position_locked_raw or '').strip().lower() == 'true'

    print("Received scanned code:", session.get('inv_position_code'))
    print("Received picture position path:", session.get('inv_pictureposition_path'))
    print(f"DEBUG: position_locked from form = {position_locked}")
    print(f"DEBUG: inv_same_position (session) = {session.get('inv_same_position')}")

    # Trust the client-submitted form field when present.
    # (Previously we OR'd with session state, which could stale/lag and incorrectly force multi-barcode flow.)
    if position_locked_raw is None:
        is_locked = session.get('inv_same_position', False)
    else:
        is_locked = position_locked

    # Keep server session in sync with the latest scan.
    session['inv_same_position'] = bool(is_locked)
    
    # Check if shelf is locked
    if is_locked:
        # Shelf is locked - go to multibarcode page
        print("DEBUG: Redirecting to multibarcode.html (locked)")
        return redirect(url_for('multibarcode_page'))
    else:
        # Normal flow - go to barcode page
        print("DEBUG: Redirecting to barcode.html")
        return redirect(url_for('barcode_page'))


def process_barcode():
    scanned = (request.form.get('scanned_result') or '').strip()
    if ',' in scanned:
        normalized_codes = [
            ss_normalization._normalize_scanned_upc(code)
            for code in scanned.split(',')
            if (code or '').strip()
        ]
        session['inv_barcode'] = ','.join(normalized_codes)
    else:
        session['inv_barcode'] = ss_normalization._normalize_scanned_upc(scanned)
    print("Received scanned code:", session.get('inv_barcode'))

    response = make_response(render_template("additem.html"))
    response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    response.headers['Pragma'] = 'no-cache'
    response.headers['Expires'] = '0'
    return response


def _add_item_screening_lookup(barcode):
    actual = ss_normalization._normalize_scanned_upc(barcode)
    if not actual:
        return {
            'barcode': '',
            'title': '',
            'image_url': '',
            'title_source': '',
            'image_source': '',
            'macy_found': False,
            'macy_title': '',
            'macy_image_url': '',
            'needs_manual_title': True,
            'missing_image': True
        }

    variants = ss_listing_lifecycle._marketplace_upc_lookup_variants(actual)
    if '-' in str(actual or ''):
        base_part, suffix_part = str(actual).split('-', 1)
        base_part = str(base_part or '').strip()
        suffix_part = str(suffix_part or '').strip()
        macy_variants = []

        def _add_macy_variant(value):
            v = str(value or '').strip().lower()
            if v and v not in macy_variants:
                macy_variants.append(v)

        if base_part and suffix_part:
            _add_macy_variant(f'{base_part}-{suffix_part}')
            stripped_base = ss_normalization._strip_leading_zeros_numeric(base_part) or base_part
            _add_macy_variant(f'{stripped_base}-{suffix_part}')
            if base_part.isdigit() and len(base_part) <= 12:
                _add_macy_variant(f'{base_part.zfill(12)}-{suffix_part}')
                _add_macy_variant(f'{base_part.zfill(13)}-{suffix_part}')
            if stripped_base.isdigit() and len(stripped_base) <= 12:
                _add_macy_variant(f'{stripped_base.zfill(12)}-{suffix_part}')
                _add_macy_variant(f'{stripped_base.zfill(13)}-{suffix_part}')
        # Prefer an exact suffixed raw-BOL row, then fall back to the base UPC.
        # A suffix identifies an individual warehouse unit; product metadata
        # normally lives on the unsuffixed catalog/BOL barcode.
        for variant in variants:
            _add_macy_variant(variant)
    else:
        macy_variants = list(variants)
    title = ''
    image_url = ''
    title_source = ''
    image_source = ''
    macy_found = False
    macy_title = ''
    macy_image_url = ''

    def _pick_row(cur, query, candidates=None):
        combined = {}
        for candidate in (candidates or variants):
            for row in cur.execute(query.replace('LIMIT 1', ''), (candidate,)):
                for key in row.keys():
                    if not str(combined.get(key) or '').strip() and str(row[key] or '').strip():
                        combined[key] = row[key]
                if (combined.get('title') or combined.get('item_description')) and combined.get('image_url'):
                    return combined
        if combined:
            for key in ('title', 'item_description', 'image_url'):
                combined.setdefault(key, '')
            return combined
        return None

    try:
        with ss_database.db_connection('rawbol.db') as conn:
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()
            row = _pick_row(cur, '''
                SELECT upc, item_description, image_url
                FROM raw_bol_items
                WHERE upc IS NOT NULL
                  AND TRIM(upc) != ''
                  AND LOWER(TRIM(upc)) = ?
                ORDER BY rowid DESC
                LIMIT 1
            ''', macy_variants)
            if row:
                macy_found = True
                macy_title = str(row['item_description'] or '').strip()
                macy_image_url = str(row['image_url'] or '').strip()
                if macy_title:
                    title = macy_title
                    title_source = 'rawbol'
                if macy_image_url:
                    image_url = macy_image_url
                    image_source = 'rawbol'
    except Exception:
        pass

    # Fallback: ebayStore.db INVENTORY (Title / Image)
    if not title or not image_url:
        try:
            with ss_database.db_connection('ebayStore.db') as conn:
                conn.row_factory = sqlite3.Row
                cur = conn.cursor()
                row = _pick_row(cur, '''
                    SELECT COALESCE(Title, '') AS title,
                           COALESCE(Image, '') AS image_url
                    FROM INVENTORY
                    WHERE UPC IS NOT NULL
                      AND TRIM(UPC) != ''
                      AND LOWER(TRIM(UPC)) = ?
                    ORDER BY ID DESC
                    LIMIT 1
                ''')
                if row:
                    if not title:
                        title = str(row['title'] or '').strip()
                        if title:
                            title_source = 'ebay'
                    if not image_url:
                        image_url = str(row['image_url'] or '').strip()
                        if image_url:
                            image_source = 'ebay'
        except Exception:
            pass

    # Fallback: amazonStore.db — try ITEMS (SP-API) then INVENTORY (legacy)
    if not title or not image_url:
        try:
            with ss_database.db_connection('amazonStore.db') as conn:
                conn.row_factory = sqlite3.Row
                cur = conn.cursor()
                # Try SP-API table first
                try:
                    row = _pick_row(cur, '''
                        SELECT COALESCE(TITLE, '') AS title,
                               COALESCE(IMAGE, '') AS image_url
                        FROM ITEMS
                        WHERE UPC IS NOT NULL
                          AND TRIM(UPC) != ''
                          AND LOWER(TRIM(UPC)) = ?
                        ORDER BY LAST_UPDATED DESC
                        LIMIT 1
                    ''')
                except sqlite3.OperationalError:
                    row = None
                # Fall back to legacy INVENTORY table
                if not row or not row.get('title') or not row.get('image_url'):
                    try:
                        legacy_row = _pick_row(cur, '''
                            SELECT COALESCE(Title, '') AS title,
                                   COALESCE(Image, '') AS image_url
                            FROM INVENTORY
                            WHERE UPC IS NOT NULL
                              AND TRIM(UPC) != ''
                              AND LOWER(TRIM(UPC)) = ?
                            ORDER BY ID DESC
                            LIMIT 1
                        ''')
                    except sqlite3.OperationalError:
                        legacy_row = None
                    if legacy_row:
                        row = row or {}
                        for field in ('title', 'image_url'):
                            row[field] = row.get(field) or legacy_row.get(field, '')
                if row:
                    if not title:
                        title = str(row['title'] or '').strip()
                        if title:
                            title_source = 'amazon'
                    if not image_url:
                        image_url = str(row['image_url'] or '').strip()
                        if image_url:
                            image_source = 'amazon'
        except Exception:
            pass

    try:
        with ss_database.db_connection('bol.db') as conn:
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()
            _ensure_custom_item_registry(cur)
            conn.commit()
            row = _pick_row(cur, '''
                SELECT item_description AS title, image_url
                FROM custom_item_registry
                WHERE LOWER(TRIM(upc)) = ?
                LIMIT 1
            ''')
            if row:
                if not title:
                    title = str(row['title'] or '').strip()
                    if title:
                        title_source = 'custom_registry'
                if not image_url:
                    image_url = str(row['image_url'] or '').strip()
                    if image_url:
                        image_source = 'custom_registry'

            if not title or not image_url:
                try:
                    bol_row = _pick_row(cur, '''
                        SELECT item_description AS title, image_url
                        FROM bol_items
                        WHERE upc IS NOT NULL
                          AND TRIM(upc) != ''
                          AND LOWER(TRIM(upc)) = ?
                        ORDER BY rowid DESC
                        LIMIT 1
                    ''')
                except Exception:
                    bol_row = None
                if bol_row:
                    if not title:
                        title = str(bol_row['title'] or '').strip()
                        if title:
                            title_source = 'bol'
                    if not image_url:
                        image_url = str(bol_row['image_url'] or '').strip()
                        if image_url:
                            image_source = 'bol'
    except Exception:
        pass

    if not title or not image_url:
        try:
            with ss_database.db_connection('searchRack.db') as conn:
                conn.row_factory = sqlite3.Row
                cur = conn.cursor()
                cur.execute("PRAGMA table_info('SEARCHRACK')")
                cols = [r[1] for r in cur.fetchall()]
                cols_lower = {str(c).lower(): c for c in cols}
                barcode_col = cols_lower.get('barcode') or cols_lower.get('upc')
                title_col = cols_lower.get('title')
                image_col = cols_lower.get('image') or cols_lower.get('images')
                id_col = cols_lower.get('id') or 'rowid'

                if barcode_col and (title_col or image_col):
                    title_expr = title_col or "''"
                    image_expr = image_col or "''"
                    row = _pick_row(cur, f'''
                        SELECT COALESCE({title_expr}, '') AS title,
                               COALESCE({image_expr}, '') AS image_url
                        FROM SEARCHRACK
                        WHERE {barcode_col} IS NOT NULL
                          AND TRIM({barcode_col}) != ''
                          AND LOWER(TRIM({barcode_col})) = ?
                        ORDER BY {id_col} DESC
                        LIMIT 1
                    ''')
                    if row:
                        if not title:
                            title = str(row['title'] or '').strip()
                            if title:
                                title_source = 'searchrack'
                        if not image_url:
                            image_url = str(row['image_url'] or '').strip()
                            if image_url:
                                image_source = 'searchrack'
        except Exception:
            pass

    # Explicit field overrides win over catalog enrichment. Resolve each field
    # independently and prefer the exact warehouse suffix before its base UPC.
    try:
        with ss_database.db_connection('bol.db') as conn:
            cur = conn.cursor()
            locked_title = locked_image = False
            for candidate in variants:
                row = cur.execute('''
                    SELECT item_description, image_url, title_override, image_override
                    FROM custom_item_registry WHERE LOWER(TRIM(upc)) = ?
                ''', (candidate,)).fetchone()
                if not row:
                    continue
                if not locked_title and row[2] and str(row[0] or '').strip():
                    title, title_source, locked_title = str(row[0]).strip(), 'custom_registry', True
                if not locked_image and row[3] and str(row[1] or '').strip():
                    image_url, image_source, locked_image = str(row[1]).strip(), 'custom_registry', True
    except sqlite3.OperationalError:
        pass

    return {
        'barcode': actual,
        'title': title,
        'image_url': image_url,
        'title_source': title_source,
        'image_source': image_source,
        'macy_found': macy_found,
        'macy_title': macy_title,
        'macy_image_url': macy_image_url,
        'needs_manual_title': not bool(str(title or '').strip()),
        'missing_image': not bool(str(image_url or '').strip())
    }


def api_add_item_screen():
    try:
        data = request.get_json(silent=True) or {}
        raw_barcodes = data.get('barcodes') or []
        if not isinstance(raw_barcodes, list):
            return jsonify({'success': False, 'error': 'barcodes must be an array'}), 400

        payload = {}
        seen = set()
        for raw in raw_barcodes:
            raw_key = str(raw or '').strip()
            barcode = ss_normalization._normalize_scanned_upc(raw)
            if not barcode:
                continue
            key = str(barcode).strip()
            if key in seen:
                if raw_key and raw_key not in payload and key in payload:
                    payload[raw_key] = payload[key]
                continue
            seen.add(key)
            result = _add_item_screening_lookup(key)
            payload[key] = result
            if raw_key and raw_key != key:
                payload[raw_key] = result

        return jsonify({'success': True, 'items': payload})
    except Exception as e:
        return jsonify({'success': False, 'error': ss_errors._safe_error(e, 'add_item:screen')}), 500


def additem_page():
    response = make_response(render_template("additem.html"))
    response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    response.headers['Pragma'] = 'no-cache'
    response.headers['Expires'] = '0'
    return response


def additem_multi_page():
    """Page for multi-barcode adds when shelf is locked"""
    response = make_response(render_template("additem_multi.html"))
    response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    response.headers['Pragma'] = 'no-cache'
    response.headers['Expires'] = '0'
    return response


def multibarcode_page():
    """Multi-barcode scanning page with list building"""
    response = make_response(render_template("multibarcode.html"))
    response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    response.headers['Pragma'] = 'no-cache'
    response.headers['Expires'] = '0'
    return response


def toggle():
    data = request.get_json()
    is_checked = data.get('checked', False)
    session['inv_same_position'] = is_checked
    print("Checkbox state:", is_checked)  # True or False

    # Respond with JSON so the page doesn't change
    return jsonify({'success': True, 'message': f'Checkbox is {"ON" if is_checked else "OFF"}'})


def get_high_res_image_url(url):
    if url and "s-l" in url:
        return url.replace("s-l140.jpg", "s-l1600.jpg").replace("s-l500.jpg", "s-l1600.jpg")
    return url


def finalize_barcodes():
    print("🔎 Finalizing barcodes in ebayStore.db...")
    conn = None
    try:
        conn = sqlite3.connect('ebayStore.db')
        cur = conn.cursor()
        # Ensure barcodes_finalized column exists
        cur.execute("PRAGMA table_info(INVENTORY)")
        columns = [row[1] for row in cur.fetchall()]
        if "barcodes_finalized" not in columns:
            cur.execute("ALTER TABLE INVENTORY ADD COLUMN barcodes_finalized INTEGER DEFAULT 0")
            conn.commit()
        # Select items not finalized
        cur.execute("SELECT ItemID, SKU, UPC, barcodes_finalized FROM INVENTORY WHERE barcodes_finalized IS NULL OR barcodes_finalized = 0")
        rows = cur.fetchall()
        for item_id, sku, upc, finalized in rows:
            if not sku:
                continue
            # If SKU and UPC are the same, delete SKU
            if sku == upc:
                cur.execute("UPDATE INVENTORY SET SKU = NULL, barcodes_finalized = 1 WHERE ItemID = ?", (item_id,))
                print(f"ItemID {item_id}: SKU and UPC are the same, SKU deleted.")
                continue
            # If SKU is all digits, >9 chars, and UPC is null/empty
            if sku.isdigit() and len(sku) > 9 and (upc is None or upc == '' or upc == 'null'):
                cur.execute("UPDATE INVENTORY SET UPC = ?, SKU = NULL, barcodes_finalized = 1 WHERE ItemID = ?", (sku, item_id))
                print(f"ItemID {item_id}: Numeric SKU transferred to UPC and SKU deleted.")
                continue
            # Otherwise, just mark as finalized
            cur.execute("UPDATE INVENTORY SET barcodes_finalized = 1 WHERE ItemID = ?", (item_id,))
        conn.commit()
        print("✅ Barcode finalization complete.")
    except Exception as e:
        print(f"❌ Error finalizing barcodes: {e}")
    finally:
        if conn is not None:
            conn.close()



