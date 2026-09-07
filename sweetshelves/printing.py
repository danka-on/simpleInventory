"""Printing for Sweet Shelves."""

import base64
import io
import sqlite3
from flask import jsonify, render_template, request
from printer_manager import printer_manager
from . import (
    config as ss_config, database as ss_database, errors as ss_errors, prep_schema as ss_prep_schema,
    runtime as ss_runtime,
)


def printer_settings_page():
    """Printer configuration page"""
    return render_template('printer_settings.html')


def printer_label_designer_page():
    """Label sizing visualizer and test-print page."""
    return render_template('printer_label_designer.html')


def get_printer_config():
    """Get current printer configuration"""
    conn = None
    platform_type = printer_manager.detect_platform_type()
    platform_name = printer_manager.platform
    try:
        conn = sqlite3.connect('bol.db')
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute('SELECT * FROM printer_config WHERE id = 1')
        config = cur.fetchone()
        
        if config:
            return jsonify({
                'success': True,
                'config': dict(config),
                'server_platform': platform_name,
                'server_platform_type': platform_type
            })
        else:
            return jsonify({
                'success': True,
                'config': None,
                'server_platform': platform_name,
                'server_platform_type': platform_type
            })
    except Exception as e:
        return jsonify({'success': False, 'error': ss_errors._safe_error(e)}), 500
    finally:
        if conn:
            conn.close()


def get_printer_status():
    """Return live printer connection status for setup UI and item-prep."""
    try:
        status = printer_manager.get_connection_status()
        config = printer_manager.get_config_snapshot()
        return jsonify({'success': True, 'status': status, 'config': config})
    except Exception as e:
        return jsonify({'success': False, 'error': ss_errors._safe_error(e)}), 500


def save_printer_config():
    """Save printer configuration"""
    try:
        data = request.get_json()
        existing_config = printer_manager.get_config_snapshot()
        printer_type = data.get('printer_type', existing_config.get('printer_type', 'bluetooth'))
        printer_mode = data.get('printer_mode', existing_config.get('printer_mode', 'thermal'))
        print_method = data.get('print_method', existing_config.get('print_method', 'escpos'))
        bluetooth_address = data.get('bluetooth_address', existing_config.get('bluetooth_address', ''))
        printer_name = data.get('printer_name', existing_config.get('printer_name', ''))
        printer_model = data.get('printer_model', existing_config.get('printer_model', ''))
        network_ip = data.get('network_ip', existing_config.get('network_ip', ''))
        network_port = int(data.get('network_port', existing_config.get('network_port', 9100)))
        label_width = int(data.get('label_width', existing_config.get('label_width', 50)))
        label_height = int(data.get('label_height', existing_config.get('label_height', 30)))
        label_show_name = data.get('label_show_name', existing_config.get('label_show_name', True))
        label_show_barcode = data.get('label_show_barcode', existing_config.get('label_show_barcode', True))
        label_title_font_size = int(data.get('label_title_font_size', existing_config.get('label_title_font_size', 10)))
        label_barcode_font_size = int(data.get('label_barcode_font_size', existing_config.get('label_barcode_font_size', 10)))
        label_barcode_height = int(data.get('label_barcode_height', existing_config.get('label_barcode_height', 34)))
        label_barcode_scale = float(data.get('label_barcode_scale', existing_config.get('label_barcode_scale', 1.8)))
        label_title_lines = int(data.get('label_title_lines', existing_config.get('label_title_lines', 2)))
        brother_ql_label = str(data.get('brother_ql_label', existing_config.get('brother_ql_label', ''))).strip()
        label_show_barcode_text = data.get('label_show_barcode_text', existing_config.get('label_show_barcode_text', True))
        printer_mac = str(data.get('printer_mac', existing_config.get('printer_mac', ''))).strip()

        success = printer_manager.save_printer_config(
            printer_type,
            bluetooth_address,
            printer_name,
            network_ip,
            network_port,
            printer_mode,
            print_method,
            printer_model,
            label_width,
            label_height,
            label_show_name,
            label_show_barcode,
            label_title_font_size,
            label_barcode_font_size,
            label_barcode_height,
            label_barcode_scale,
            label_title_lines,
            brother_ql_label,
            label_show_barcode_text,
            printer_mac
        )
        
        if success:
            ss_runtime.cache.delete_memoized(get_printer_config)
            return jsonify({'success': True, 'message': 'Printer configuration saved'})
        else:
            return jsonify({'success': False, 'error': 'Failed to save configuration'}), 500
    except Exception as e:
        return jsonify({'success': False, 'error': ss_errors._safe_error(e)}), 500


def scan_bluetooth_devices():
    """Scan for available Bluetooth devices"""
    try:
        devices = printer_manager.get_available_bluetooth_devices()
        return jsonify({'success': True, 'devices': devices})
    except Exception as e:
        return jsonify({'success': False, 'error': ss_errors._safe_error(e)}), 500


def wake_printer():
    """Send a TCP knock to wake the printer from sleep/standby."""
    try:
        result = printer_manager.wake_printer()
        return jsonify({'success': result.get('ok', False), **result})
    except Exception as e:
        ss_config.logger.error('printer:wake failed: %s', e, exc_info=True)
        return jsonify({'success': False, 'error': ss_errors._safe_error(e)}), 500


def test_printer():
    """Test printer connection"""
    try:
        printer_manager.test_print()
        return jsonify({'success': True, 'message': 'Test print sent successfully'})
    except Exception as e:
        ss_config.logger.error('printer:test failed: %s', e, exc_info=True)
        return jsonify({'success': False, 'error': str(e).strip() or ss_errors._safe_error(e)}), 500


def test_printer_sample():
    """Print a sample barcode using optional layout overrides."""
    try:
        data = request.get_json() or {}
        upc = str(data.get('upc', '') or '').strip()
        item_description = str(data.get('item_description', '') or '').strip()
        layout_override = {
            'label_width': data.get('label_width', 65),
            'label_height': data.get('label_height', 29),
            'label_show_name': data.get('label_show_name', True),
            'label_show_barcode': data.get('label_show_barcode', True),
            'label_show_barcode_text': data.get('label_show_barcode_text', True),
            'label_title_font_size': data.get('label_title_font_size', 10),
            'label_barcode_font_size': data.get('label_barcode_font_size', 10),
            'label_barcode_height': data.get('label_barcode_height', 34),
            'label_barcode_scale': data.get('label_barcode_scale', 1.8),
            'label_title_lines': data.get('label_title_lines', 2),
        }

        if not upc:
            return jsonify({'success': False, 'error': 'Barcode is required'}), 400

        printer_manager.print_barcode(upc, item_description, quantity=1, layout_override=layout_override)
        return jsonify({'success': True, 'message': 'Sample label sent to printer'})
    except Exception as e:
        ss_config.logger.error('printer:test-sample failed: %s', e, exc_info=True)
        return jsonify({'success': False, 'error': str(e).strip() or ss_errors._safe_error(e)}), 500


def printer_label_preview_api():
    """Render the exact printer raster used by the label designer preview."""
    try:
        data = request.get_json(silent=True) or {}
        upc = str(data.get('upc', '') or '').strip()
        item_description = str(data.get('item_description', '') or '').strip()
        layout_override = {
            'label_width': data.get('label_width', 65),
            'label_height': data.get('label_height', 29),
            'label_show_name': data.get('label_show_name', True),
            'label_show_barcode': data.get('label_show_barcode', True),
            'label_show_barcode_text': data.get('label_show_barcode_text', True),
            'label_title_font_size': data.get('label_title_font_size', 10),
            'label_barcode_font_size': data.get('label_barcode_font_size', 10),
            'label_barcode_height': data.get('label_barcode_height', 34),
            'label_barcode_scale': data.get('label_barcode_scale', 1.8),
            'label_title_lines': data.get('label_title_lines', 2),
        }

        if not upc:
            return jsonify({'success': False, 'error': 'Barcode is required'}), 400

        img = printer_manager.render_label_preview_image(
            upc,
            item_description,
            layout_override=layout_override
        )
        buffer = io.BytesIO()
        img.save(buffer, format='PNG')
        encoded = base64.b64encode(buffer.getvalue()).decode('ascii')
        return jsonify({
            'success': True,
            'image_data': f'data:image/png;base64,{encoded}',
            'width_px': img.width,
            'height_px': img.height
        })
    except Exception as e:
        ss_config.logger.error('printer:label-preview failed: %s', e, exc_info=True)
        return jsonify({'success': False, 'error': str(e).strip() or ss_errors._safe_error(e)}), 500


def print_barcode_api():
    """Print barcode label"""
    try:
        data = request.get_json()
        upc = data.get('upc', '').strip()
        item_description = data.get('item_description', '')
        quantity = int(data.get('quantity', 1))
        
        if not upc:
            return jsonify({'success': False, 'error': 'UPC is required'}), 400
        
        # Print the barcode
        printer_manager.print_barcode(upc, item_description, quantity)
        
        return jsonify({'success': True, 'message': f'Printed {quantity} label(s)'})
    except Exception as e:
        ss_config.logger.error('printer:print-barcode failed: %s', e, exc_info=True)
        return jsonify({'success': False, 'error': str(e).strip() or ss_errors._safe_error(e)}), 500


def browser_print_barcode():
    """Generate a printable page for browser-based barcode printing"""
    try:
        data = request.get_json()
        barcodes = data.get('barcodes', [])  # Array of {upc, description}
        
        if not barcodes:
            return jsonify({'success': False, 'error': 'No barcodes provided'}), 400
        
        # Return the data for the frontend to generate the print window
        return jsonify({'success': True, 'barcodes': barcodes})
    except Exception as e:
        return jsonify({'success': False, 'error': ss_errors._safe_error(e)}), 500


def generate_barcode_api():
    """Generate barcode image without printing (for preview/testing)"""
    try:
        data = request.get_json()
        upc = data.get('upc', '').strip()
        item_description = data.get('item_description', '')
        
        if not upc:
            return jsonify({'success': False, 'error': 'UPC is required'}), 400
        
        # Generate barcode file
        filepath = printer_manager.generate_barcode_file(upc, item_description)
        
        # Return relative path for web access
        web_path = filepath.replace('\\', '/').replace('static/', '/')
        
        return jsonify({'success': True, 'image_url': web_path, 'filepath': filepath})
    except Exception as e:
        return jsonify({'success': False, 'error': ss_errors._safe_error(e)}), 500


def _ensure_label_presets_table(cur):
    cur.execute('''
        CREATE TABLE IF NOT EXISTS label_presets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            label_height REAL DEFAULT 29,
            label_show_name INTEGER DEFAULT 1,
            label_show_barcode INTEGER DEFAULT 1,
            label_show_barcode_text INTEGER DEFAULT 1,
            label_title_font_size INTEGER DEFAULT 10,
            label_barcode_font_size INTEGER DEFAULT 10,
            label_barcode_height INTEGER DEFAULT 34,
            label_barcode_scale REAL DEFAULT 1.8,
            label_title_lines INTEGER DEFAULT 2,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    try:
        cur.execute("PRAGMA table_info(label_presets)")
        columns = {str(row[1]).lower() for row in cur.fetchall()}
        migrations = [
            ('label_height', "ALTER TABLE label_presets ADD COLUMN label_height REAL DEFAULT 29"),
            ('label_show_name', "ALTER TABLE label_presets ADD COLUMN label_show_name INTEGER DEFAULT 1"),
            ('label_show_barcode', "ALTER TABLE label_presets ADD COLUMN label_show_barcode INTEGER DEFAULT 1"),
            ('label_show_barcode_text', "ALTER TABLE label_presets ADD COLUMN label_show_barcode_text INTEGER DEFAULT 1"),
            ('label_title_font_size', "ALTER TABLE label_presets ADD COLUMN label_title_font_size INTEGER DEFAULT 10"),
            ('label_barcode_font_size', "ALTER TABLE label_presets ADD COLUMN label_barcode_font_size INTEGER DEFAULT 10"),
            ('label_barcode_height', "ALTER TABLE label_presets ADD COLUMN label_barcode_height INTEGER DEFAULT 34"),
            ('label_barcode_scale', "ALTER TABLE label_presets ADD COLUMN label_barcode_scale REAL DEFAULT 1.8"),
            ('label_title_lines', "ALTER TABLE label_presets ADD COLUMN label_title_lines INTEGER DEFAULT 2"),
        ]
        for column_name, statement in migrations:
            if column_name in columns:
                continue
            try:
                cur.execute(statement)
            except Exception:
                pass
    except Exception:
        pass


def get_label_presets():
    try:
        with ss_database.db_connection('bol.db') as conn:
            cur = conn.cursor()
            _ensure_label_presets_table(cur)
            cur.execute('SELECT id, name, label_height, label_show_name, label_show_barcode, label_show_barcode_text, label_title_font_size, label_barcode_font_size, label_barcode_height, label_barcode_scale, label_title_lines FROM label_presets ORDER BY name COLLATE NOCASE')
            rows = cur.fetchall()
        presets = [
            {'id': r[0], 'name': r[1], 'label_height': r[2], 'label_show_name': bool(r[3]),
             'label_show_barcode': bool(r[4]), 'label_show_barcode_text': bool(r[5]),
             'label_title_font_size': r[6], 'label_barcode_font_size': r[7],
             'label_barcode_height': r[8], 'label_barcode_scale': r[9], 'label_title_lines': r[10]}
            for r in rows
        ]
        return jsonify({'success': True, 'presets': presets})
    except Exception as e:
        return jsonify({'success': False, 'error': ss_errors._safe_error(e)}), 500


def save_label_preset():
    try:
        data = request.get_json()
        name = str(data.get('name') or '').strip()
        if not name:
            return jsonify({'success': False, 'error': 'Preset name is required'}), 400
        with ss_database.db_connection('bol.db') as conn:
            cur = conn.cursor()
            _ensure_label_presets_table(cur)
            cur.execute('''
                INSERT INTO label_presets
                    (name, label_height, label_show_name, label_show_barcode, label_show_barcode_text,
                     label_title_font_size, label_barcode_font_size,
                     label_barcode_height, label_barcode_scale, label_title_lines)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                name,
                float(data.get('label_height') or 29),
                1 if data.get('label_show_name', True) else 0,
                1 if data.get('label_show_barcode', True) else 0,
                1 if data.get('label_show_barcode_text', True) else 0,
                int(data.get('label_title_font_size') or 10),
                int(data.get('label_barcode_font_size') or 10),
                int(data.get('label_barcode_height') or 34),
                float(data.get('label_barcode_scale') or 1.8),
                int(data.get('label_title_lines') or 2),
            ))
            preset_id = cur.lastrowid
        return jsonify({'success': True, 'id': preset_id, 'name': name})
    except Exception as e:
        return jsonify({'success': False, 'error': ss_errors._safe_error(e)}), 500


def update_label_preset(preset_id):
    try:
        data = request.get_json() or {}
        name = str(data.get('name') or '').strip()
        if not name:
            return jsonify({'success': False, 'error': 'Preset name is required'}), 400
        with ss_database.db_connection('bol.db') as conn:
            cur = conn.cursor()
            _ensure_label_presets_table(cur)
            cur.execute('''
                UPDATE label_presets
                SET name = ?,
                    label_height = ?,
                    label_show_name = ?,
                    label_show_barcode = ?,
                    label_show_barcode_text = ?,
                    label_title_font_size = ?,
                    label_barcode_font_size = ?,
                    label_barcode_height = ?,
                    label_barcode_scale = ?,
                    label_title_lines = ?
                WHERE id = ?
            ''', (
                name,
                float(data.get('label_height') or 29),
                1 if data.get('label_show_name', True) else 0,
                1 if data.get('label_show_barcode', True) else 0,
                1 if data.get('label_show_barcode_text', True) else 0,
                int(data.get('label_title_font_size') or 10),
                int(data.get('label_barcode_font_size') or 10),
                int(data.get('label_barcode_height') or 34),
                float(data.get('label_barcode_scale') or 1.8),
                int(data.get('label_title_lines') or 2),
                preset_id,
            ))
            if cur.rowcount <= 0:
                return jsonify({'success': False, 'error': 'Preset not found'}), 404
        return jsonify({'success': True, 'id': preset_id, 'name': name})
    except Exception as e:
        return jsonify({'success': False, 'error': ss_errors._safe_error(e)}), 500


def delete_label_preset(preset_id):
    try:
        with ss_database.db_connection('bol.db') as conn:
            cur = conn.cursor()
            _ensure_label_presets_table(cur)
            cur.execute('DELETE FROM label_presets WHERE id = ?', (preset_id,))
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': ss_errors._safe_error(e)}), 500


def get_print_queue():
    """Get all items in the print queue"""
    conn = None
    try:
        ss_prep_schema._ensure_items_prep_tables()  # Ensure table exists
        conn = sqlite3.connect('bol.db')
        cur = conn.cursor()
        cur.execute('SELECT title, barcode, added_at FROM print_queue ORDER BY added_at ASC')
        rows = cur.fetchall()
        
        queue = [{'title': row[0], 'barcode': row[1], 'added_at': row[2]} for row in rows]
        return jsonify({'success': True, 'queue': queue})
    except Exception as e:
        return jsonify({'success': False, 'error': ss_errors._safe_error(e)}), 500
    finally:
        if conn is not None:
            conn.close()


def add_to_print_queue():
    """Add an item to the print queue"""
    conn = None
    try:
        data = request.get_json()
        title = data.get('title', '').strip()
        barcode = data.get('barcode', '').strip()
        
        if not title or not barcode:
            return jsonify({'success': False, 'error': 'Title and barcode are required'}), 400
        
        ss_prep_schema._ensure_items_prep_tables()  # Ensure table exists
        conn = sqlite3.connect('bol.db')
        cur = conn.cursor()
        
        # Insert or ignore (prevent duplicates)
        cur.execute('INSERT OR IGNORE INTO print_queue (title, barcode) VALUES (?, ?)', (title, barcode))
        conn.commit()
        
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': ss_errors._safe_error(e)}), 500
    finally:
        if conn is not None:
            conn.close()


def clear_print_queue():
    """Clear all items from the print queue"""
    conn = None
    try:
        ss_prep_schema._ensure_items_prep_tables()  # Ensure table exists
        conn = sqlite3.connect('bol.db')
        cur = conn.cursor()
        cur.execute('DELETE FROM print_queue')
        conn.commit()
        
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': ss_errors._safe_error(e)}), 500
    finally:
        if conn is not None:
            conn.close()


def remove_from_print_queue():
    """Remove a specific item from the print queue"""
    conn = None
    try:
        data = request.get_json()
        barcode = data.get('barcode', '').strip()
        
        if not barcode:
            return jsonify({'success': False, 'error': 'Barcode is required'}), 400
        
        ss_prep_schema._ensure_items_prep_tables()  # Ensure table exists
        conn = sqlite3.connect('bol.db')
        cur = conn.cursor()
        cur.execute('DELETE FROM print_queue WHERE barcode = ?', (barcode,))
        conn.commit()
        
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': ss_errors._safe_error(e)}), 500
    finally:
        if conn is not None:
            conn.close()
