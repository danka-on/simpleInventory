from contextlib import nullcontext

from flask import Flask, request, send_file, url_for, render_template, jsonify, redirect
from werkzeug.exceptions import RequestEntityTooLarge
from PIL import Image, ImageDraw
import io, time, subprocess, os, requests, json, threading, sqlite3, sys
import xml.etree.ElementTree as ET
from dotenv import load_dotenv
import xml.dom.minidom as minidom



from inventory import find_item  # adjust this to match your actual import
from DBmanager import ebayStoreDB, amazonStoreDB, store_ebay_order, createSearchRackDB, addToSearchRack
from DBmanager import enrich_searchrack_db
try:
    from amazon_manager import AmazonManager
    AMAZON_AVAILABLE = True
    print("✅ Amazon integration loaded successfully")
except ImportError as e:
    AMAZON_AVAILABLE = False
    print(f"⚠️ Warning: Amazon integration not available (import error): {e}")
except Exception as e:
    AMAZON_AVAILABLE = False
    print(f"⚠️ Warning: Amazon integration not available (other error): {e}")

# Print which Python is running the app (helps debug venv vs system Python issues)
print(f"🔧 Python executable: {sys.executable}")
try:
    from BOLextractor import process_bol_excel
    BOL_AVAILABLE = True
except ImportError:
    BOL_AVAILABLE = False
    print("Warning: BOLextractor not available (pandas missing)")
# manualMatcher removed: functionality deprecated and files deleted

oldAuth_token = 'v^1.1#i^1#I^3#f^0#p^3#r^1#t^Ul4xMF82OkYwRjY2Q0VFOUY1QUM0MkEyMjkyMDY5Q0E5NjY0NjIxXzFfMSNFXjI2MA=='


CLIENT_ID = os.getenv("EBAY_CLIENT_ID")
CLIENT_SECRET = os.getenv("EBAY_CLIENT_SECRET")
RUNAME = os.getenv("EBAY_RUNAME")
app = Flask(__name__)
# Ensure template changes hot-reload and disable caching for dev to avoid stale pages
app.config['TEMPLATES_AUTO_RELOAD'] = True
app.config['SEND_FILE_MAX_AGE_DEFAULT'] = 0
try:
    app.jinja_env.auto_reload = True
except Exception:
    pass

@app.after_request
def add_no_cache_headers(response):
    try:
        # Strong no-cache to ensure browser and any proxy fetch fresh HTML/JS
        response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
        response.headers['Pragma'] = 'no-cache'
        response.headers['Expires'] = '0'
    except Exception:
        pass
    return response
# Increase upload limit to better accommodate multiple high-res photos
app.config['MAX_CONTENT_LENGTH'] = 64 * 1024 * 1024  # 64 MB limit for uploads
#for ebay api calls

from token_manager import get_access_token, load_tokens, is_expired
from printer_manager import printer_manager

access_token = get_access_token()
headers = {
    "Authorization": f"Bearer {access_token}",
    "Content-Type": "application/json"
}

# Simple lock and status for background enrichment
_enrich_lock = threading.Lock()
_enrich_status = {'running': False, 'last_run': None, 'message': ''}

def _run_enrich_in_background():
    global _enrich_status
    if _enrich_lock.locked():
        return False
    def _worker():
        global _enrich_status
        with _enrich_lock:
            _enrich_status['running'] = True
            _enrich_status['message'] = 'running'
            try:
                enrich_searchrack_db()
                _enrich_status['message'] = 'completed'
            except Exception as e:
                _enrich_status['message'] = f'error: {e}'
            finally:
                _enrich_status['running'] = False
                _enrich_status['last_run'] = int(time.time())
    t = threading.Thread(target=_worker, daemon=True)
    t.start()
    return True





@app.route('/tools')
def tools():
    return render_template('tools.html')

# BOL Statistics page
@app.route('/bol-statistics')
def bol_statistics():
    return render_template('bol_statistics.html')

# Barcode Print Que page
@app.route('/barcode-print-que')
def barcode_print_que():
     return render_template('barcode_print_que.html')

@app.route('/sync')
def sync():
    return render_template('sync.html')

# Route for misc settings page
@app.route('/misc')
def misc():
    return render_template('misc.html')

# Financial Analytics page
@app.route('/financial-analytics')
def financial_analytics():
    print("DEBUG: Financial analytics page accessed")
    return render_template('financial_analytics.html')

# API endpoint for financial analytics data
@app.route('/api/financial-analytics')
def api_financial_analytics():
    """
    Fetch all sold orders with cost data from BOL and calculate profits.
    Returns financial metrics for the dashboard.
    """
    print("DEBUG: Financial analytics API called")
    try:
        # Connect to sold.db
        sold_conn = sqlite3.connect('sold.db')
        sold_conn.row_factory = sqlite3.Row
        sold_cur = sold_conn.cursor()
        
        # Get all sold orders with lot_number
        sold_cur.execute('''
            SELECT 
                order_id,
                item_id,
                title,
                quantity,
                price,
                seller_fee,
                taxes,
                paid_time,
                shipped_time,
                barcode,
                store,
                location,
                shipping_cost,
                lot_number
            FROM orders
            WHERE paid_time IS NOT NULL
            ORDER BY paid_time DESC
        ''')
        orders = sold_cur.fetchall()
        sold_conn.close()
        
        # Connect to bol.db to get cost data
        bol_conn = sqlite3.connect('bol.db')
        bol_conn.row_factory = sqlite3.Row
        bol_cur = bol_conn.cursor()
        
        # Also connect to ebayStore and amazonStore for fallback cost data
        ebay_conn = sqlite3.connect('ebayStore.db')
        ebay_conn.row_factory = sqlite3.Row
        ebay_cur = ebay_conn.cursor()
        
        amazon_conn = sqlite3.connect('amazonStore.db')
        amazon_conn.row_factory = sqlite3.Row
        amazon_cur = amazon_conn.cursor()
        
        transactions = []
        
        for order in orders:
            item_data = dict(order)
            
            # Try to get cost from BOL first (priority 1)
            upc = order['barcode'] or order['item_id']
            cost = None
            cost_source = None
            # LOT number comes directly from sold.db (already enriched from rawbol.db)
            bol_number = order['lot_number'] if order['lot_number'] and str(order['lot_number']).lower() not in ['', 'nan', 'none', 'null'] else None
            
            if upc:
                # Check BOL - get cost only
                bol_cur.execute('SELECT client_cost FROM bol_items WHERE upc = ? COLLATE NOCASE', (upc,))
                bol_row = bol_cur.fetchone()
                if bol_row and bol_row['client_cost']:
                    cost = float(bol_row['client_cost'])
                    cost_source = 'bol'
                
                # Fallback to eBay store data (could have cost in some cases)
                if not cost:
                    ebay_cur.execute('SELECT UPC FROM INVENTORY WHERE UPC = ? OR ItemID = ? COLLATE NOCASE LIMIT 1', (upc, upc))
                    if ebay_cur.fetchone():
                        cost_source = 'ebay'
                        # Note: ebayStore.db doesn't have cost data, just marking source
                
                # Fallback to Amazon store data
                if not cost:
                    amazon_cur.execute('SELECT UPC FROM ITEMS WHERE UPC = ? OR ASIN = ? COLLATE NOCASE LIMIT 1', (upc, upc))
                    if amazon_cur.fetchone():
                        cost_source = 'amazon'
                        # Note: amazonStore.db doesn't have cost data, just marking source
            
            item_data['cost'] = cost
            item_data['cost_source'] = cost_source
            item_data['upc'] = upc
            item_data['bol_number'] = bol_number
            
            transactions.append(item_data)
        
        # Get BOL stats for each unique BOL number
        bol_stats = {}
        bol_cur.execute('''
            SELECT 
                bol_number,
                COUNT(*) as item_count,
                SUM(client_cost * quantity) as total_cost
            FROM bol_items
            WHERE bol_number IS NOT NULL 
                AND bol_number != '' 
                AND LOWER(bol_number) NOT IN ('nan', 'none', 'null')
            GROUP BY bol_number
        ''')
        for row in bol_cur.fetchall():
            bol_stats[row['bol_number']] = {
                'item_count': row['item_count'],
                'total_cost': float(row['total_cost']) if row['total_cost'] else 0
            }
        
        bol_conn.close()
        ebay_conn.close()
        amazon_conn.close()
        
        # Get LOT # data from rawbol.db upload_logs
        lot_options = []
        try:
            rawbol_conn = sqlite3.connect('rawbol.db')
            rawbol_conn.row_factory = sqlite3.Row
            rawbol_cur = rawbol_conn.cursor()
            
            rawbol_cur.execute('''
                SELECT 
                    lot_number,
                    import_date,
                    total_client_cost
                FROM upload_logs
                WHERE lot_number IS NOT NULL 
                    AND lot_number != ''
                ORDER BY import_date DESC
            ''')
            
            rows = rawbol_cur.fetchall()
            print(f"DEBUG: Found {len(rows)} LOT entries in rawbol.db")
            
            for row in rows:
                lot_num = row['lot_number']
                import_date = row['import_date']
                total_cost = row['total_client_cost']
                
                # Format as "Date + LOT #"
                display_name = f"{import_date} + {lot_num}"
                lot_options.append({
                    'value': lot_num,
                    'label': display_name,
                    'date': import_date,
                    'cost': float(total_cost) if total_cost else 0
                })
                print(f"DEBUG: Added LOT option: {display_name} (${float(total_cost) if total_cost else 0:.2f})")
            
            rawbol_conn.close()
        except Exception as e:
            print(f"Warning: Could not fetch LOT # data from rawbol.db: {e}")
            import traceback
            traceback.print_exc()
        
        return jsonify({
            'success': True,
            'transactions': transactions,
            'count': len(transactions),
            'bol_stats': bol_stats,
            'lot_options': lot_options
        })
        
    except Exception as e:
        print(f"Error in financial analytics API: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

# =========== Item Preparation Helpers and Routes ===========
def _normalize_upc(upc):
    try:
        if upc is None:
            return ''
        s = str(upc).strip()
        if not s:
            return ''
        # handle float-like strings ending with .0
        if s.endswith('.0') and s.replace('.0', '').isdigit():
            return str(int(float(s)))
        # handle numeric floats
        if s.replace('.', '', 1).isdigit():
            try:
                f = float(s)
                if abs(f - int(f)) < 1e-9:
                    return str(int(f))
            except Exception:
                pass
        return s
    except Exception:
        return str(upc or '')

def _ensure_items_prep_tables():
    try:
        conn = sqlite3.connect('bol.db')
        cur = conn.cursor()
        cur.execute('''
            CREATE TABLE IF NOT EXISTS items_prep_status (
                upc TEXT PRIMARY KEY,
                status TEXT,
                reason TEXT,
                note TEXT,
                updated_at TEXT
            )
        ''')
        cur.execute('''
            CREATE TABLE IF NOT EXISTS items_prep_images (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                upc TEXT,
                image_path TEXT,
                created_at TEXT,
                deleted_at TEXT,
                expires_at TEXT,
                trash_path TEXT
            )
        ''')
        cur.execute('''
            CREATE TABLE IF NOT EXISTS items_prep_notes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                upc TEXT,
                note TEXT,
                created_at TEXT
            )
        ''')
        # Add missing columns if table existed earlier
        # Add location to items_prep_status if not exists
        cur.execute("PRAGMA table_info(items_prep_status)")
        status_cols = [r[1] for r in cur.fetchall()]
        if 'location' not in status_cols:
            cur.execute('ALTER TABLE items_prep_status ADD COLUMN location TEXT')
        if 'pictureposition' not in status_cols:
            cur.execute('ALTER TABLE items_prep_status ADD COLUMN pictureposition TEXT')
        
        cur.execute("PRAGMA table_info(items_prep_images)")
        cols = [r[1] for r in cur.fetchall()]
        if 'deleted_at' not in cols:
            cur.execute("ALTER TABLE items_prep_images ADD COLUMN deleted_at TEXT")
        if 'expires_at' not in cols:
            cur.execute("ALTER TABLE items_prep_images ADD COLUMN expires_at TEXT")
        if 'trash_path' not in cols:
            cur.execute("ALTER TABLE items_prep_images ADD COLUMN trash_path TEXT")
        # Add rotation column to keep track of image orientation (degrees)
        if 'rotation' not in cols:
            try:
                cur.execute("ALTER TABLE items_prep_images ADD COLUMN rotation INTEGER DEFAULT 0")
            except Exception:
                # some older sqlite versions may behave differently; ignore errors
                pass
        
        # Create print queue table for cross-device synchronization
        cur.execute('''CREATE TABLE IF NOT EXISTS print_queue (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            barcode TEXT NOT NULL,
            added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(title, barcode)
        )''')
        
        conn.commit()
        conn.close()
    except Exception as e:
        print('Failed ensuring items_prep tables:', e)

def _now_iso():
    import datetime as _dt
    return _dt.datetime.utcnow().isoformat()

def _trash_retention_days():
    # Prefer app_settings table value; fallback to env var; then default 7
    try:
        conn = sqlite3.connect('bol.db')
        cur = conn.cursor()
        cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='app_settings'")
        if cur.fetchone():
            cur.execute("SELECT value FROM app_settings WHERE key='TRASH_RETENTION_DAYS'")
            row = cur.fetchone()
            if row and row[0] is not None:
                conn.close()
                return int(row[0])
        conn.close()
    except Exception:
        pass
    try:
        return int(os.getenv('TRASH_RETENTION_DAYS', '7'))
    except Exception:
        return 7

def _ensure_app_settings():
    try:
        conn = sqlite3.connect('bol.db')
        cur = conn.cursor()
        cur.execute('''CREATE TABLE IF NOT EXISTS app_settings (
            key TEXT PRIMARY KEY,
            value TEXT
        )''')
        conn.commit()
        conn.close()
    except Exception as e:
        print('Failed ensuring app_settings:', e)

def _set_trash_retention_days(days: int):
    try:
        days = int(days)
        _ensure_app_settings()
        conn = sqlite3.connect('bol.db')
        cur = conn.cursor()
        cur.execute("INSERT INTO app_settings(key, value) VALUES('TRASH_RETENTION_DAYS', ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (str(days),))
        # Update expires_at for existing trashed items
        cur.execute("SELECT id, deleted_at FROM items_prep_images WHERE deleted_at IS NOT NULL")
        rows = cur.fetchall()
        import datetime as _dt
        for rid, del_at in rows:
            try:
                base = _dt.datetime.fromisoformat(del_at)
            except Exception:
                base = _dt.datetime.utcnow()
            new_exp = (base + _dt.timedelta(days=days)).isoformat()
            cur.execute('UPDATE items_prep_images SET expires_at=? WHERE id=?', (new_exp, rid))
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        print('Failed setting retention days:', e)
        return False

def _move_to_trash(abs_path, upc):
    try:
        # Build trash path under static/items_prep_trash/YYYY/MM/UPC
        import datetime as _dt
        base = os.path.join(app.root_path, 'static', 'items_prep_trash')
        now = _dt.datetime.utcnow()
        year = str(now.year)
        month = f"{now.month:02d}"
        target_dir = os.path.join(base, year, month, str(upc))
        os.makedirs(target_dir, exist_ok=True)
        fname = os.path.basename(abs_path)
        target = os.path.join(target_dir, fname)
        # If name exists, add counter
        if os.path.exists(target):
            name, ext = os.path.splitext(fname)
            k = 1
            while os.path.exists(target):
                target = os.path.join(target_dir, f"{name}_{k}{ext}")
                k += 1
        os.replace(abs_path, target)
        # Return relative path under static
        rel = os.path.relpath(target, os.path.join(app.root_path, 'static')).replace('\\','/')
        return rel
    except Exception as e:
        print('Failed move to trash:', e)
        return None

def _purge_expired_trash():
    try:
        _ensure_items_prep_tables()
        conn = sqlite3.connect('bol.db')
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute("SELECT id, trash_path FROM items_prep_images WHERE deleted_at IS NOT NULL AND expires_at IS NOT NULL AND expires_at <= ?", (_now_iso(),))
        rows = cur.fetchall()
        count = 0
        for r in rows:
            tr = r['trash_path']
            if tr:
                abs_path = os.path.join(app.root_path, 'static', tr)
                try:
                    if os.path.isfile(abs_path):
                        os.remove(abs_path)
                except Exception as fe:
                    print('Purge remove failed:', fe)
            try:
                cur.execute('DELETE FROM items_prep_images WHERE id = ?', (r['id'],))
                count += 1
            except Exception as de:
                print('Purge delete row failed:', de)
        conn.commit()
        conn.close()
        return count
    except Exception as e:
        print('Purge error:', e)
        return 0

_trash_purger_started = False
def _start_trash_purger_thread():
    global _trash_purger_started
    if _trash_purger_started:
        return
    _trash_purger_started = True
    def _runner():
        import time as _time
        while True:
            try:
                _purge_expired_trash()
            except Exception as e:
                print('Trash purge tick error:', e)
            _time.sleep(24*60*60)
    t = threading.Thread(target=_runner, daemon=True)
    t.start()

def _ensure_bol_list_status_column():
    """Ensure bol_items has a list_status column for tracking if item is listed."""
    try:
        conn = sqlite3.connect('bol.db')
        cur = conn.cursor()
        cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='bol_items'")
        if not cur.fetchone():
            conn.close()
            return
        cur.execute("PRAGMA table_info(bol_items)")
        cols = [r[1] for r in cur.fetchall()]
        if 'list_status' not in cols:
            cur.execute("ALTER TABLE bol_items ADD COLUMN list_status TEXT")
        if 'temporary' not in cols:
            cur.execute("ALTER TABLE bol_items ADD COLUMN temporary INTEGER DEFAULT 0")
        conn.commit()
        conn.close()
    except Exception as e:
        print('Failed ensuring list_status and temporary columns in bol_items:', e)

@app.route('/item-prep')
def item_prep_page():
    return render_template('item_prep.html')

@app.route('/item-prep/diagnostic')
def item_prep_diagnostic_page():
    upc = (request.args.get('upc') or '').strip()
    return render_template('item_prep_diagnostic.html', upc=upc)

@app.route('/item-prep/diagnostic/view')
def item_prep_diagnostic_view_page():
    upc_raw = request.args.get('upc', '').strip()
    # Strip leading zeros from barcode
    upc_stripped = upc_raw.lstrip('0') if upc_raw.isdigit() else upc_raw
    upc = _normalize_upc(upc_stripped)
    # Load status, images, and (optionally) bol item details
    status = None
    images = []
    bol = None
    try:
        _ensure_items_prep_tables()
        _ensure_bol_list_status_column()
        conn = sqlite3.connect('bol.db')
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute('SELECT status, reason, note, updated_at FROM items_prep_status WHERE upc = ? COLLATE NOCASE', (upc,))
        row = cur.fetchone()
        status = dict(row) if row else None
        cur.execute("SELECT id, image_path, created_at FROM items_prep_images WHERE upc = ? COLLATE NOCASE AND (deleted_at IS NULL OR TRIM(COALESCE(deleted_at,'')) = '') ORDER BY created_at DESC, id DESC", (upc,))
        images = [dict(r) for r in cur.fetchall()]
        cur.execute('SELECT id, upc, item_description, image_url, lot_number, bol_number, import_date, list_status, quantity FROM bol_items WHERE upc = ? COLLATE NOCASE LIMIT 1', (upc,))
        b = cur.fetchone()
        bol = dict(b) if b else None
        conn.close()
    except Exception as e:
        print('Diagnostic view load error:', e)
    return render_template('item_prep_diagnostic_view.html', upc=upc, status=status, images=images, bol=bol)

@app.route('/item-prep-no-barcode')
def item_prep_no_barcode_page():
    """Page for searching items by name when there's no barcode"""
    return render_template('item_prep_no_barcode.html')

@app.route('/api/search-rawbol', methods=['POST'])
def search_rawbol_api():
    """Search rawbol.db by item description with pagination"""
    try:
        data = request.get_json()
        query = data.get('query', '').strip()
        page = int(data.get('page', 1))
        per_page = 3  # Show only 3 items per page
        offset = (page - 1) * per_page
        
        if not query:
            return jsonify({'success': False, 'error': 'No search query provided'})
        
        # Search rawbol.db
        conn = sqlite3.connect('rawbol.db')
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        
        # Get total count for pagination
        search_pattern = f"%{query}%"
        cur.execute('''
            SELECT COUNT(*) as total
            FROM raw_bol_items 
            WHERE item_description LIKE ? COLLATE NOCASE
        ''', (search_pattern,))
        total = cur.fetchone()['total']
        
        # Get paginated results
        cur.execute('''
            SELECT upc, item_description, image_url 
            FROM raw_bol_items 
            WHERE item_description LIKE ? COLLATE NOCASE
            LIMIT ? OFFSET ?
        ''', (search_pattern, per_page, offset))
        
        results = [dict(row) for row in cur.fetchall()]
        conn.close()
        
        total_pages = (total + per_page - 1) // per_page  # Ceiling division
        
        return jsonify({
            'success': True, 
            'results': results,
            'page': page,
            'per_page': per_page,
            'total': total,
            'total_pages': total_pages
        })
    except Exception as e:
        print(f'Error searching rawbol: {e}')
        return jsonify({'success': False, 'error': str(e)})

# ============================================================================
# PRINTER ROUTES
# ============================================================================

@app.route('/printer-settings')
def printer_settings_page():
    """Printer configuration page"""
    return render_template('printer_settings.html')

@app.route('/api/printer/config', methods=['GET'])
def get_printer_config():
    """Get current printer configuration"""
    try:
        conn = sqlite3.connect('bol.db')
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute('SELECT * FROM printer_config WHERE id = 1')
        config = cur.fetchone()
        conn.close()
        
        if config:
            return jsonify({'success': True, 'config': dict(config)})
        else:
            return jsonify({'success': True, 'config': None})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/printer/config', methods=['POST'])
def save_printer_config():
    """Save printer configuration"""
    try:
        data = request.get_json()
        printer_type = data.get('printer_type', 'bluetooth')
        printer_mode = data.get('printer_mode', 'thermal')
        print_method = data.get('print_method', 'escpos')
        bluetooth_address = data.get('bluetooth_address', '')
        printer_name = data.get('printer_name', '')
        network_ip = data.get('network_ip', '')
        network_port = int(data.get('network_port', 9100))
        
        success = printer_manager.save_printer_config(
            printer_type, 
            bluetooth_address, 
            printer_name,
            network_ip,
            network_port,
            printer_mode,
            print_method
        )
        
        if success:
            return jsonify({'success': True, 'message': 'Printer configuration saved'})
        else:
            return jsonify({'success': False, 'error': 'Failed to save configuration'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/printer/scan-bluetooth', methods=['POST'])
def scan_bluetooth_devices():
    """Scan for available Bluetooth devices"""
    try:
        devices = printer_manager.get_available_bluetooth_devices()
        return jsonify({'success': True, 'devices': devices})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/printer/test', methods=['POST'])
def test_printer():
    """Test printer connection"""
    try:
        printer_manager.test_print()
        return jsonify({'success': True, 'message': 'Test print sent successfully'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/printer/print-barcode', methods=['POST'])
def print_barcode_api():
    """Print barcode label"""
    try:
        data = request.get_json()
        upc = data.get('upc', '').strip()
        item_description = data.get('item_description', '')
        quantity = int(data.get('quantity', 1))
        
        if not upc:
            return jsonify({'success': False, 'error': 'UPC is required'})
        
        # Print the barcode
        printer_manager.print_barcode(upc, item_description, quantity)
        
        return jsonify({'success': True, 'message': f'Printed {quantity} label(s)'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/printer/browser-print', methods=['POST'])
def browser_print_barcode():
    """Generate a printable page for browser-based barcode printing"""
    try:
        data = request.get_json()
        barcodes = data.get('barcodes', [])  # Array of {upc, description}
        
        if not barcodes:
            return jsonify({'success': False, 'error': 'No barcodes provided'})
        
        # Return the data for the frontend to generate the print window
        return jsonify({'success': True, 'barcodes': barcodes})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/printer/generate-barcode', methods=['POST'])
def generate_barcode_api():
    """Generate barcode image without printing (for preview/testing)"""
    try:
        data = request.get_json()
        upc = data.get('upc', '').strip()
        item_description = data.get('item_description', '')
        
        if not upc:
            return jsonify({'success': False, 'error': 'UPC is required'})
        
        # Generate barcode file
        filepath = printer_manager.generate_barcode_file(upc, item_description)
        
        # Return relative path for web access
        web_path = filepath.replace('\\', '/').replace('static/', '/')
        
        return jsonify({'success': True, 'image_url': web_path, 'filepath': filepath})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

# ============================================================================
# PRINT QUEUE API ENDPOINTS
# ============================================================================

@app.route('/api/print-queue', methods=['GET'])
def get_print_queue():
    """Get all items in the print queue"""
    try:
        _ensure_items_prep_tables()  # Ensure table exists
        conn = sqlite3.connect('bol.db')
        cur = conn.cursor()
        cur.execute('SELECT title, barcode FROM print_queue ORDER BY added_at ASC')
        rows = cur.fetchall()
        conn.close()
        
        queue = [{'title': row[0], 'barcode': row[1]} for row in rows]
        return jsonify({'success': True, 'queue': queue})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/print-queue', methods=['POST'])
def add_to_print_queue():
    """Add an item to the print queue"""
    try:
        data = request.get_json()
        title = data.get('title', '').strip()
        barcode = data.get('barcode', '').strip()
        
        if not title or not barcode:
            return jsonify({'success': False, 'error': 'Title and barcode are required'})
        
        _ensure_items_prep_tables()  # Ensure table exists
        conn = sqlite3.connect('bol.db')
        cur = conn.cursor()
        
        # Insert or ignore (prevent duplicates)
        cur.execute('INSERT OR IGNORE INTO print_queue (title, barcode) VALUES (?, ?)', (title, barcode))
        conn.commit()
        conn.close()
        
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/print-queue', methods=['DELETE'])
def clear_print_queue():
    """Clear all items from the print queue"""
    try:
        _ensure_items_prep_tables()  # Ensure table exists
        conn = sqlite3.connect('bol.db')
        cur = conn.cursor()
        cur.execute('DELETE FROM print_queue')
        conn.commit()
        conn.close()
        
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/print-queue/item', methods=['DELETE'])
def remove_from_print_queue():
    """Remove a specific item from the print queue"""
    try:
        data = request.get_json()
        barcode = data.get('barcode', '').strip()
        
        if not barcode:
            return jsonify({'success': False, 'error': 'Barcode is required'})
        
        _ensure_items_prep_tables()  # Ensure table exists
        conn = sqlite3.connect('bol.db')
        cur = conn.cursor()
        cur.execute('DELETE FROM print_queue WHERE barcode = ?', (barcode,))
        conn.commit()
        conn.close()
        
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

# ============================================================================
# ITEM PREP - CREATE CUSTOM ITEM ROUTES
# ============================================================================

@app.route('/item-prep-create-item')
def item_prep_create_item_page():
    """Page for creating custom items with auto-generated 777 barcodes"""
    return render_template('item_prep_create_item.html')

@app.route('/api/items-prep/generate-barcode', methods=['POST'])
def generate_custom_barcode():
    """Generate auto-incremented 777 prefix barcode with duplicate protection"""
    try:
        conn = sqlite3.connect('bol.db')
        cur = conn.cursor()
        
        # Create temp_items table if not exists
        cur.execute('''
            CREATE TABLE IF NOT EXISTS temp_items (
                upc TEXT PRIMARY KEY,
                item_description TEXT,
                image_url TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
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
        
        # Get the highest barcode from both tables
        max_barcode = result[0] if result[0] else None
        temp_max = temp_result[0] if temp_result[0] else None
        
        if temp_max and (not max_barcode or temp_max > max_barcode):
            max_barcode = temp_max
        
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
            
            if not exists_bol and not exists_temp:
                # Barcode is unique, we're good!
                conn.close()
                return jsonify({'success': True, 'barcode': new_barcode.strip()})
            
            # Barcode exists, increment and try again
            max_barcode = int(new_barcode)
            attempts += 1
        
        # If we exhausted attempts, return error
        conn.close()
        return jsonify({'success': False, 'error': 'Could not generate unique barcode after 100 attempts'})
        
    except Exception as e:
        print(f'Error generating barcode: {e}')
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/items-prep/temp-item', methods=['POST'])
def save_temp_item():
    """Save temporary custom item with image data"""
    try:
        data = request.get_json()
        upc = data.get('upc', '').strip()
        item_description = data.get('item_description', '').strip()
        image_data = data.get('image_data', '')
        
        if not upc or not item_description or not image_data:
            return jsonify({'success': False, 'error': 'Missing required fields'})
        
        # Save image to file
        import base64
        import uuid
        
        # Remove data URL prefix if present
        if image_data.startswith('data:image'):
            image_data = image_data.split(',')[1]
        
        # Decode base64 image
        image_bytes = base64.b64decode(image_data)
        
        # Create directory for temp items if it doesn't exist
        temp_dir = os.path.join('static', 'temp_items')
        os.makedirs(temp_dir, exist_ok=True)
        
        # Save with UPC as filename
        image_filename = f"{upc}.jpg"
        image_path = os.path.join(temp_dir, image_filename)
        
        with open(image_path, 'wb') as f:
            f.write(image_bytes)
        
        # Store in session-like temp database table
        conn = sqlite3.connect('bol.db')
        cur = conn.cursor()
        
        # Create temp_items table if it doesn't exist
        cur.execute('''
            CREATE TABLE IF NOT EXISTS temp_items (
                upc TEXT PRIMARY KEY,
                item_description TEXT,
                image_url TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        # Insert or replace temp item
        web_image_path = f"/static/temp_items/{image_filename}"
        cur.execute('''
            INSERT OR REPLACE INTO temp_items (upc, item_description, image_url)
            VALUES (?, ?, ?)
        ''', (upc, item_description, web_image_path))
        
        conn.commit()
        conn.close()
        
        return jsonify({'success': True, 'message': 'Temporary item saved', 'image_url': web_image_path})
    except Exception as e:
        print(f'Error saving temp item: {e}')
        return jsonify({'success': False, 'error': str(e)})

 

@app.route('/api/bol_lookup', methods=['GET'])
def api_bol_lookup():
    """Lookup a BOL item by UPC in bol.db and return normalized fields.
    First checks temp_items for custom items, then bol_items.
    If duplicate UPC is encountered, create a temporary suffixed entry (e.g., barcode-1).
    """
    try:
        upc = _normalize_upc(request.args.get('upc'))
        if not upc:
            return jsonify({'found': False, 'error': 'Missing upc'}), 400
        
        conn = sqlite3.connect('bol.db')
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        
        # First check temp_items table for custom created items
        cur.execute('''
            CREATE TABLE IF NOT EXISTS temp_items (
                upc TEXT PRIMARY KEY,
                item_description TEXT,
                image_url TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        cur.execute("SELECT upc, item_description, image_url FROM temp_items WHERE upc = ? COLLATE NOCASE", (upc,))
        temp_item = cur.fetchone()
        
        if temp_item:
            # Return the temp item in the expected format
            item = {
                'upc': temp_item['upc'],
                'item_description': temp_item['item_description'],
                'image_url': temp_item['image_url'],
                'lot_number': None,
                'bol_number': None,
                'import_date': None,
                'temporary': 1,
                'is_duplicate': False,
                'is_custom': True
            }
            conn.close()
            return jsonify({'found': True, 'item': item})
        
        # Ensure temporary column exists in bol_items
        cur.execute('PRAGMA table_info(bol_items)')
        cols = [r[1] for r in cur.fetchall()]
        if 'temporary' not in cols:
            cur.execute('ALTER TABLE bol_items ADD COLUMN temporary INTEGER DEFAULT 0')
            conn.commit()
        
        # Check if this UPC already exists and has been processed
        cur.execute("SELECT id, upc, item_description, image_url, lot_number, bol_number, import_date, temporary FROM bol_items WHERE upc = ? COLLATE NOCASE", (upc,))
        rows = cur.fetchall()
        
        if not rows:
            conn.close()
            return jsonify({'found': False})
        
        # Find if there's already a permanent entry (temporary = 0 or NULL)
        permanent_row = None
        for r in rows:
            if not r['temporary']:
                permanent_row = r
                break
        
        # If we found a permanent entry, this is a duplicate scan
        # Create a temporary suffixed entry
        if permanent_row:
            # Find the next available suffix
            suffix = 1
            while True:
                suffixed_upc = f"{upc}-{suffix}"
                cur.execute("SELECT upc FROM bol_items WHERE upc = ? COLLATE NOCASE", (suffixed_upc,))
                if not cur.fetchone():
                    break
                suffix += 1
            
            # Create temporary duplicate entry
            cur.execute("""
                INSERT INTO bol_items (upc, item_description, image_url, lot_number, bol_number, import_date, temporary)
                VALUES (?, ?, ?, ?, ?, ?, 1)
            """, (suffixed_upc, permanent_row['item_description'], permanent_row['image_url'], 
                  permanent_row['lot_number'], permanent_row['bol_number'], permanent_row['import_date']))
            conn.commit()
            
            # Return the new temporary entry
            item = {
                'id': cur.lastrowid,
                'upc': suffixed_upc,
                'item_description': permanent_row['item_description'],
                'image_url': permanent_row['image_url'],
                'lot_number': permanent_row['lot_number'],
                'bol_number': permanent_row['bol_number'],
                'import_date': permanent_row['import_date'],
                'temporary': 1,
                'is_duplicate': True
            }
        else:
            # Return the existing row (could be temporary from a previous session)
            item = dict(rows[0])
            item['is_duplicate'] = False
        
        conn.close()
        
        # Also include current prep status if exists
        try:
            _ensure_items_prep_tables()
            conn2 = sqlite3.connect('bol.db')
            conn2.row_factory = sqlite3.Row
            cur2 = conn2.cursor()
            cur2.execute('SELECT status, reason, note, updated_at FROM items_prep_status WHERE upc = ? COLLATE NOCASE', (item['upc'],))
            srow = cur2.fetchone()
            conn2.close()
            if srow:
                item['prep_status'] = dict(srow)
        except Exception:
            item['prep_status'] = None

        return jsonify({'found': True, 'item': item})
    except Exception as e:
        return jsonify({'found': False, 'error': str(e)}), 500

@app.route('/api/items_prep/status/<upc>', methods=['GET'])
def api_items_prep_status_get(upc):
    """Get preparation status for a UPC."""
    try:
        upc_n = _normalize_upc(upc)
        _ensure_items_prep_tables()
        conn = sqlite3.connect('bol.db')
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute('SELECT status, reason, note, updated_at FROM items_prep_status WHERE upc = ? COLLATE NOCASE', (upc_n,))
        row = cur.fetchone()
        conn.close()
        if row:
            return jsonify({'success': True, 'status': row['status'], 'reason': row['reason'], 'note': row['note'], 'updated_at': row['updated_at']})
        else:
            return jsonify({'success': True, 'status': 'unchecked', 'reason': None, 'note': None, 'updated_at': None})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/items_prep/status', methods=['POST'])
def api_items_prep_status():
    """Upsert preparation status for a UPC. JSON: { upc, status, reason?, note? }
    If status is 'good', marks temporary entries as permanent.
    For custom items (777 prefix), moves from temp_items to bol_items permanently.
    """
    try:
        data = request.get_json() or {}
        upc = _normalize_upc(data.get('upc'))
        status = (data.get('status') or '').strip().lower()
        reason = (data.get('reason') or '').strip()
        note = (data.get('note') or '').strip()
        if not upc or status not in ('good', 'bad', 'unchecked'):
            return jsonify({'success': False, 'error': 'Missing upc or invalid status'}), 400
        
        conn = sqlite3.connect('bol.db')
        cur = conn.cursor()
        
        # If status is 'good' or 'bad', handle custom items (move from temp_items to bol_items)
        if status in ('good', 'bad'):
            # Check if this is a temp/custom item
            cur.execute('SELECT upc, item_description, image_url FROM temp_items WHERE upc = ? COLLATE NOCASE', (upc,))
            temp_row = cur.fetchone()
            
            if temp_row:
                # Move to bol_items permanently
                import datetime
                import_date = datetime.datetime.utcnow().isoformat()
                
                cur.execute('''
                    INSERT OR REPLACE INTO bol_items (upc, item_description, image_url, lot_number, bol_number, import_date, temporary)
                    VALUES (?, ?, ?, ?, ?, ?, 0)
                ''', (temp_row[0], temp_row[1], temp_row[2], None, None, import_date))
                
                # Delete from temp_items
                cur.execute('DELETE FROM temp_items WHERE upc = ? COLLATE NOCASE', (upc,))
                conn.commit()
                print(f'Moved custom item {upc} from temp_items to bol_items')
            
            # Also mark any existing temporary entries as permanent
            cur.execute('UPDATE bol_items SET temporary = 0 WHERE upc = ? COLLATE NOCASE AND temporary = 1', (upc,))
            conn.commit()
        
        _ensure_items_prep_tables()
        import datetime
        ts = datetime.datetime.utcnow().isoformat()
        # upsert
        cur.execute('SELECT upc FROM items_prep_status WHERE upc = ? COLLATE NOCASE', (upc,))
        if cur.fetchone():
            cur.execute('UPDATE items_prep_status SET status=?, reason=?, note=?, updated_at=? WHERE upc=?', (status, reason, note, ts, upc))
        else:
            cur.execute('INSERT INTO items_prep_status (upc, status, reason, note, updated_at) VALUES (?,?,?,?,?)', (upc, status, reason, note, ts))
        conn.commit()
        conn.close()
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/items_prep/cleanup_temp', methods=['POST'])
def api_items_prep_cleanup_temp():
    """Delete temporary entries when user skips. JSON: { upc }"""
    try:
        data = request.get_json() or {}
        upc = _normalize_upc(data.get('upc'))
        if not upc:
            return jsonify({'success': False, 'error': 'Missing upc'}), 400
        
        conn = sqlite3.connect('bol.db')
        cur = conn.cursor()
        # Delete temporary entries with this UPC
        cur.execute('DELETE FROM bol_items WHERE upc = ? COLLATE NOCASE AND temporary = 1', (upc,))
        conn.commit()
        conn.close()
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/bol_items/quantity', methods=['POST'])
def api_bol_items_update_quantity():
    """Update quantity for a BOL item. JSON: { upc, quantity }"""
    try:
        data = request.get_json() or {}
        upc = _normalize_upc(data.get('upc'))
        quantity = data.get('quantity')
        if not upc or quantity is None:
            return jsonify({'success': False, 'error': 'Missing upc or quantity'}), 400
        
        try:
            quantity = int(quantity)
        except (ValueError, TypeError):
            return jsonify({'success': False, 'error': 'Invalid quantity'}), 400
        
        conn = sqlite3.connect('bol.db')
        cur = conn.cursor()
        cur.execute('UPDATE bol_items SET quantity = ? WHERE upc = ? COLLATE NOCASE', (quantity, upc))
        conn.commit()
        conn.close()
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/items_prep/status/<upc>', methods=['DELETE'])
def api_items_prep_status_delete(upc):
    """Delete preparation status for a UPC (for undo functionality)."""
    try:
        upc = _normalize_upc(upc)
        if not upc:
            return jsonify({'success': False, 'error': 'Missing upc'}), 400
        
        _ensure_items_prep_tables()
        conn = sqlite3.connect('bol.db')
        cur = conn.cursor()
        cur.execute('DELETE FROM items_prep_status WHERE upc = ? COLLATE NOCASE', (upc,))
        conn.commit()
        conn.close()
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/items_prep/diagnostic', methods=['POST'])
def api_items_prep_diagnostic():
    """Save diagnostic info and photos for a UPC. form-data: upc, reason, note, files: photos[]"""
    try:
        _ensure_items_prep_tables()
        upc = _normalize_upc(request.form.get('upc'))
        reason = (request.form.get('reason') or '').strip()
        note = (request.form.get('note') or '').strip()
        if not upc:
            return jsonify({'success': False, 'error': 'Missing upc'}), 400

        # Mark temporary entry as permanent if it exists
        conn_temp = sqlite3.connect('bol.db')
        cur_temp = conn_temp.cursor()
        cur_temp.execute('UPDATE bol_items SET temporary = 0 WHERE upc = ? COLLATE NOCASE AND temporary = 1', (upc,))
        conn_temp.commit()
        conn_temp.close()

        # save files under static/items_prep
        from werkzeug.utils import secure_filename
        save_dir = os.path.join(app.root_path, 'static', 'items_prep')
        os.makedirs(save_dir, exist_ok=True)
        saved = []
        files = request.files.getlist('photos[]') or request.files.getlist('photos') or ([] if 'photo' not in request.files else [request.files['photo']])
        # optional per-file rotations can be provided as rotations[] in the same form-data (one per file, same order)
        rotations = request.form.getlist('rotations[]') or request.form.getlist('rotations') or []
        import datetime
        ts = datetime.datetime.utcnow().isoformat()
        if files:
            conn_i = sqlite3.connect('bol.db')
            cur_i = conn_i.cursor()
            for idx, f in enumerate(files):
                if not f or not getattr(f, 'filename', ''):
                    continue
                fn = secure_filename(f.filename)
                name, ext = os.path.splitext(fn)
                unique = f"{_normalize_upc(upc)}_{int(time.time()*1000)}{ext or '.jpg'}"
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
                except Exception as se:
                    print('Failed to save diagnostic image:', se)
            conn_i.commit()
            conn_i.close()
        # set status to bad with reason/note
        conn = sqlite3.connect('bol.db')
        cur = conn.cursor()
        cur.execute('SELECT upc FROM items_prep_status WHERE upc = ? COLLATE NOCASE', (upc,))
        if cur.fetchone():
            cur.execute('UPDATE items_prep_status SET status=?, reason=?, note=?, updated_at=? WHERE upc=?', ('bad', reason, note, ts, upc))
        else:
            cur.execute('INSERT INTO items_prep_status (upc, status, reason, note, updated_at) VALUES (?,?,?,?,?)', (upc, 'bad', reason, note, ts))
        conn.commit()
        conn.close()
        return jsonify({'success': True, 'saved': saved})
    except Exception as e:
        try:
            import traceback
            print('Diagnostic upload error:', e)
            traceback.print_exc()
        except Exception:
            pass
        return jsonify({'success': False, 'error': str(e)}), 500

@app.errorhandler(RequestEntityTooLarge)
def handle_file_too_large(e):
    return jsonify({'success': False, 'error': 'Upload too large. Try fewer photos or enable Low res.'}), 413

@app.route('/api/items_prep/diagnostic/<upc>', methods=['GET'])
def api_items_prep_diagnostic_get(upc):
    try:
        upc_n = _normalize_upc(upc)
        _ensure_items_prep_tables()
        conn = sqlite3.connect('bol.db')
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute('SELECT status, reason, note, updated_at FROM items_prep_status WHERE upc = ? COLLATE NOCASE', (upc_n,))
        srow = cur.fetchone()
        cur.execute("SELECT id, image_path, created_at, rotation FROM items_prep_images WHERE upc = ? COLLATE NOCASE AND (deleted_at IS NULL OR TRIM(COALESCE(deleted_at,'')) = '') ORDER BY created_at DESC, id DESC", (upc_n,))
        images = [dict(r) for r in cur.fetchall()]
        conn.close()
        return jsonify({'upc': upc_n, 'status': dict(srow) if srow else None, 'images': images})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/items_prep/diagnostic/<upc>/photos', methods=['DELETE'])
def api_items_prep_diagnostic_delete_photos(upc):
    """Soft-delete all diagnostic photos for a UPC by moving them to trash and setting retention expiry.
    Query param hard=1 to permanently delete files and rows.
    """
    try:
        upc_n = _normalize_upc(upc)
        hard = (request.args.get('hard') or '0') in ('1','true','yes')
        _ensure_items_prep_tables()
        conn = sqlite3.connect('bol.db')
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        if hard:
            cur.execute('SELECT id, image_path, trash_path, deleted_at FROM items_prep_images WHERE upc = ? COLLATE NOCASE', (upc_n,))
            rows = cur.fetchall()
            for r in rows:
                # remove from whichever exists
                for rel in [r['trash_path'], r['image_path']]:
                    if rel:
                        abs_path = os.path.join(app.root_path, 'static', rel) if not os.path.isabs(rel) else rel
                        try:
                            if os.path.isfile(abs_path):
                                os.remove(abs_path)
                        except Exception as fe:
                            print('Failed hard remove photo', abs_path, fe)
            cur.execute('DELETE FROM items_prep_images WHERE upc = ? COLLATE NOCASE', (upc_n,))
            deleted = cur.rowcount
            conn.commit()
            conn.close()
            return jsonify({'success': True, 'deleted': deleted, 'hard': True})
        else:
            # Soft delete only active (not already deleted) rows
            cur.execute("SELECT id, image_path FROM items_prep_images WHERE upc = ? COLLATE NOCASE AND (deleted_at IS NULL OR TRIM(COALESCE(deleted_at,'')) = '')", (upc_n,))
            rows = cur.fetchall()
            deleted = 0
            for r in rows:
                rel = r['image_path']
                if not rel:
                    continue
                abs_path = os.path.join(app.root_path, 'static', rel) if not os.path.isabs(rel) else rel
                new_rel = None
                if os.path.isfile(abs_path):
                    new_rel = _move_to_trash(abs_path, upc_n)
                # mark as deleted with expiry
                del_at = _now_iso()
                import datetime as _dt
                exp = ( _dt.datetime.utcnow() + _dt.timedelta(days=_trash_retention_days()) ).isoformat()
                cur.execute('UPDATE items_prep_images SET deleted_at=?, expires_at=?, trash_path=? WHERE id=?', (del_at, exp, new_rel or rel, r['id']))
                deleted += 1
            conn.commit()
            conn.close()
            return jsonify({'success': True, 'deleted': deleted, 'hard': False, 'retention_days': _trash_retention_days()})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/items_prep/diagnostic/<upc>/photos', methods=['POST'])
def api_items_prep_diagnostic_add_photos(upc):
    """Upload additional diagnostic photos for a UPC without changing status.
    Expects multipart/form-data with files in photos[] (or photos/photo) and optional rotations[]
    Returns: { success, images: [{id, image_path, created_at, rotation}] }
    """
    try:
        upc_n = _normalize_upc(upc)
        if not upc_n:
            return jsonify({'success': False, 'error': 'Missing upc'}), 400
        _ensure_items_prep_tables()
        from werkzeug.utils import secure_filename
        save_dir = os.path.join(app.root_path, 'static', 'items_prep')
        os.makedirs(save_dir, exist_ok=True)
        files = request.files.getlist('photos[]') or request.files.getlist('photos') or ([] if 'photo' not in request.files else [request.files['photo']])
        rotations = request.form.getlist('rotations[]') or request.form.getlist('rotations') or []
        if not files:
            return jsonify({'success': False, 'error': 'No files uploaded'}), 400
        import datetime
        ts = datetime.datetime.utcnow().isoformat()
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
                cur.execute('INSERT INTO items_prep_images (upc, image_path, created_at, rotation) VALUES (?,?,?,?)', (upc_n, rel, ts, rot))
                img_id = cur.lastrowid
                out.append({'id': img_id, 'image_path': rel, 'created_at': ts, 'rotation': rot})
            except Exception as se:
                print('Failed to save diagnostic image (add):', se)
        conn.commit()
        conn.close()
        return jsonify({'success': True, 'images': out})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/items_prep/photo/<int:photo_id>', methods=['DELETE'])
def api_items_prep_delete_photo(photo_id):
    """Soft-delete or hard-delete a single diagnostic photo by its id.
    Query param hard=1 to permanently remove file and DB row.
    """
    try:
        hard = (request.args.get('hard') or '0') in ('1','true','yes')
        _ensure_items_prep_tables()
        conn = sqlite3.connect('bol.db')
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute('SELECT id, upc, image_path, trash_path, deleted_at FROM items_prep_images WHERE id = ?', (photo_id,))
        r = cur.fetchone()
        if not r:
            conn.close()
            return jsonify({'success': False, 'error': 'Photo not found'}), 404
        if hard:
            # remove file(s) if present then delete row
            for rel in (r['trash_path'], r['image_path']):
                if rel:
                    abs_path = os.path.join(app.root_path, 'static', rel) if not os.path.isabs(rel) else rel
                    try:
                        if os.path.isfile(abs_path):
                            os.remove(abs_path)
                    except Exception as fe:
                        print('Failed hard remove photo', abs_path, fe)
            cur.execute('DELETE FROM items_prep_images WHERE id = ?', (photo_id,))
            deleted = cur.rowcount
            conn.commit()
            conn.close()
            return jsonify({'success': True, 'deleted': deleted, 'hard': True})
        else:
            # soft delete (mark deleted_at, set expires_at, move file to trash)
            if r['deleted_at'] and str(r['deleted_at']).strip():
                conn.close()
                return jsonify({'success': False, 'error': 'Photo already deleted'}), 400
            rel = r['image_path']
            abs_path = os.path.join(app.root_path, 'static', rel) if not os.path.isabs(rel) else rel
            new_rel = None
            if os.path.isfile(abs_path):
                new_rel = _move_to_trash(abs_path, r['upc'])
            del_at = _now_iso()
            import datetime as _dt
            exp = (_dt.datetime.utcnow() + _dt.timedelta(days=_trash_retention_days())).isoformat()
            cur.execute('UPDATE items_prep_images SET deleted_at=?, expires_at=?, trash_path=? WHERE id=?', (del_at, exp, new_rel or rel, photo_id))
            conn.commit()
            conn.close()
            return jsonify({'success': True, 'deleted': 1, 'hard': False, 'retention_days': _trash_retention_days()})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/items_prep/diagnostic/<upc>/trash', methods=['GET'])
def api_items_prep_trash_list(upc):
    try:
        upc_n = _normalize_upc(upc)
        _ensure_items_prep_tables()
        conn = sqlite3.connect('bol.db')
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute('SELECT id, trash_path, deleted_at, expires_at FROM items_prep_images WHERE upc = ? COLLATE NOCASE AND deleted_at IS NOT NULL ORDER BY deleted_at DESC', (upc_n,))
        rows = [dict(r) for r in cur.fetchall()]
        conn.close()
        return jsonify({'success': True, 'results': rows})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/items_prep/diagnostic/<upc>/trash/restore', methods=['POST'])
def api_items_prep_trash_restore(upc):
    try:
        data = request.get_json() or {}
        ids = data.get('ids')  # optional list of ids to restore; if missing, restore all for UPC
        upc_n = _normalize_upc(upc)
        _ensure_items_prep_tables()
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
            abs_trash = os.path.join(app.root_path, 'static', tr)
            abs_orig = os.path.join(app.root_path, 'static', r['image_path'])
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
        conn.close()
        return jsonify({'success': True, 'restored': restored})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/shelfcreator')
def shelfcreator():
    # Legacy route; redirect to Shelf Manager
    return redirect('/shelfmanager')

@app.route('/shelfmanager')
def shelfmanager():
    return render_template('shelfmanager.html')

@app.route('/extractor')
def extractor():
    return render_template('extractor.html')

@app.route('/api/items_prep/notes/<upc>', methods=['GET'])
def api_items_prep_notes_get(upc):
    """Get all notes for a UPC, ordered by created_at DESC."""
    try:
        upc_n = _normalize_upc(upc)
        _ensure_items_prep_tables()
        conn = sqlite3.connect('bol.db')
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute('SELECT id, note, created_at FROM items_prep_notes WHERE upc = ? COLLATE NOCASE ORDER BY created_at DESC', (upc_n,))
        notes = [dict(r) for r in cur.fetchall()]
        conn.close()
        return jsonify({'success': True, 'notes': notes})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/items_prep/notes', methods=['POST'])
def api_items_prep_notes_add():
    """Add a new note for a UPC. JSON: { upc, note }"""
    try:
        data = request.get_json() or {}
        upc = _normalize_upc(data.get('upc'))
        note = (data.get('note') or '').strip()
        if not upc or not note:
            return jsonify({'success': False, 'error': 'Missing upc or note'}), 400
        _ensure_items_prep_tables()
        import datetime
        ts = datetime.datetime.utcnow().isoformat()
        conn = sqlite3.connect('bol.db')
        cur = conn.cursor()
        cur.execute('INSERT INTO items_prep_notes (upc, note, created_at) VALUES (?,?,?)', (upc, note, ts))
        note_id = cur.lastrowid
        conn.commit()
        conn.close()
        return jsonify({'success': True, 'id': note_id, 'created_at': ts})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/items_prep/notes/<int:note_id>', methods=['DELETE'])
def api_items_prep_notes_delete(note_id):
    """Delete a note by ID."""
    try:
        _ensure_items_prep_tables()
        conn = sqlite3.connect('bol.db')
        cur = conn.cursor()
        cur.execute('DELETE FROM items_prep_notes WHERE id = ?', (note_id,))
        deleted = cur.rowcount
        conn.commit()
        conn.close()
        if deleted == 0:
            return jsonify({'success': False, 'error': 'Note not found'}), 404
        return jsonify({'success': True, 'deleted': deleted})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/items_prep/location/<upc>', methods=['GET'])
def api_items_prep_location_get(upc):
    """Get location for a UPC."""
    try:
        upc = _normalize_upc(upc)
        _ensure_items_prep_tables()
        conn = sqlite3.connect('bol.db')
        cur = conn.cursor()
        cur.execute('SELECT location, pictureposition FROM items_prep_status WHERE upc = ? COLLATE NOCASE', (upc,))
        row = cur.fetchone()
        conn.close()
        if row:
            return jsonify({'success': True, 'location': row[0], 'pictureposition': row[1]})
        else:
            return jsonify({'success': True, 'location': None, 'pictureposition': None})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/items_prep/location', methods=['POST'])
def api_items_prep_location_set():
    """Set location for a UPC. JSON: { upc, location, pictureposition }"""
    try:
        data = request.get_json() or {}
        upc = _normalize_upc(data.get('upc'))
        location = (data.get('location') or '').strip()
        pictureposition = (data.get('pictureposition') or '').strip()
        if not upc:
            return jsonify({'success': False, 'error': 'Missing upc'}), 400
        _ensure_items_prep_tables()
        import datetime
        ts = datetime.datetime.utcnow().isoformat()
        conn = sqlite3.connect('bol.db')
        cur = conn.cursor()
        # Check if status exists
        cur.execute('SELECT upc FROM items_prep_status WHERE upc = ? COLLATE NOCASE', (upc,))
        exists = cur.fetchone()
        if exists:
            cur.execute('UPDATE items_prep_status SET location=?, pictureposition=?, updated_at=? WHERE upc=?', (location, pictureposition, ts, upc))
        else:
            cur.execute('INSERT INTO items_prep_status (upc, location, pictureposition, updated_at) VALUES (?,?,?,?)', (upc, location, pictureposition, ts))
        
        # Get item description for searchRack
        cur.execute('SELECT item_description FROM bol_items WHERE upc = ? COLLATE NOCASE LIMIT 1', (upc,))
        bol_row = cur.fetchone()
        title = bol_row[0] if bol_row else None
        conn.commit()
        conn.close()
        
        # Update searchRack.db
        if location or pictureposition:
            try:
                search_conn = sqlite3.connect('searchRack.db')
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
                        QUANTITY INTEGER,
                        CREATED_AT TEXT
                    )
                ''')
                # Check if entry exists
                search_cur.execute('SELECT ID FROM SEARCHRACK WHERE BARCODE = ? COLLATE NOCASE', (upc,))
                existing = search_cur.fetchone()
                
                if existing:
                    # Update existing
                    search_cur.execute('''
                        UPDATE SEARCHRACK 
                        SET ITEM_POSITION = ?, PICTUREPOSITION = ?, TITLE = ?, CREATED_AT = ?
                        WHERE BARCODE = ? COLLATE NOCASE
                    ''', (location, pictureposition, title, ts, upc))
                else:
                    # Insert new
                    search_cur.execute('''
                        INSERT INTO SEARCHRACK (TITLE, BARCODE, ITEM_POSITION, PICTUREPOSITION, CREATED_AT)
                        VALUES (?, ?, ?, ?, ?)
                    ''', (title, upc, location, pictureposition, ts))
                
                search_conn.commit()
                search_conn.close()
            except Exception as e:
                print(f'Warning: Failed to update searchRack: {e}')
        
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/items-to-list')
def items_to_list_page():
    return render_template('items_to_list.html')

@app.route('/trash-manager')
def trash_manager_page():
    return render_template('trash_manager.html')

@app.route('/api/trash/settings', methods=['GET','POST'])
def api_trash_settings():
    if request.method == 'GET':
        return jsonify({'retention_days': _trash_retention_days()})
    try:
        data = request.get_json() or {}
        days = int(data.get('retention_days'))
        ok = _set_trash_retention_days(days)
        return jsonify({'success': ok, 'retention_days': _trash_retention_days()})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/trash/list', methods=['GET'])
def api_trash_list():
    try:
        page = int(request.args.get('page', 1))
        size = max(1, min(200, int(request.args.get('size', 50))))
        offset = (page-1) * size
        _ensure_items_prep_tables()
        conn = sqlite3.connect('bol.db')
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute('SELECT COUNT(*) FROM items_prep_images WHERE deleted_at IS NOT NULL')
        total = cur.fetchone()[0]
        cur.execute('''
            SELECT id, upc, trash_path, image_path, deleted_at, expires_at
            FROM items_prep_images
            WHERE deleted_at IS NOT NULL
            ORDER BY deleted_at DESC, id DESC
            LIMIT ? OFFSET ?
        ''', (size, offset))
        rows = [dict(r) for r in cur.fetchall()]
        conn.close()
        # add absolute-ish URLs for preview (served from static)
        for r in rows:
            r['url'] = url_for('static', filename=(r.get('trash_path') or r.get('image_path') or ''))
        return jsonify({'success': True, 'results': rows, 'total': total, 'page': page, 'size': size})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/trash/restore', methods=['POST'])
def api_trash_restore():
    try:
        data = request.get_json() or {}
        ids = data.get('ids') or []
        upc = data.get('upc')
        if upc and not ids:
            # restore all for UPC
            return api_items_prep_trash_restore(upc)
        if not ids:
            return jsonify({'success': False, 'error': 'No ids provided'})
        # we need UPC per id; fetch
        conn = sqlite3.connect('bol.db')
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        placeholders = ','.join('?' for _ in ids)
        cur.execute(f'SELECT id, upc FROM items_prep_images WHERE id IN ({placeholders})', tuple(ids))
        rows = cur.fetchall()
        conn.close()
        restored_total = 0
        for r in rows:
            resp = api_items_prep_trash_restore(r['upc'])
            # ignore details; this restores all for UPC—fine for simplicity now
            restored_total += 1
        return jsonify({'success': True, 'restored_groups': restored_total})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/trash/hard_delete', methods=['POST'])
def api_trash_hard_delete():
    try:
        data = request.get_json() or {}
        ids = data.get('ids') or []
        upc = data.get('upc')
        if upc and not ids:
            # hard delete all for UPC
            return api_items_prep_diagnostic_delete_photos(upc)
        if not ids:
            return jsonify({'success': False, 'error': 'No ids provided'})
        conn = sqlite3.connect('bol.db')
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        placeholders = ','.join('?' for _ in ids)
        cur.execute(f'SELECT DISTINCT upc FROM items_prep_images WHERE id IN ({placeholders})', tuple(ids))
        upcs = [r['upc'] for r in cur.fetchall()]
        conn.close()
        deleted_total = 0
        for u in upcs:
            resp = api_items_prep_diagnostic_delete_photos(u)
            deleted_total += 1
        return jsonify({'success': True, 'deleted_groups': deleted_total})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/trash/hard_delete_all', methods=['POST'])
def api_trash_hard_delete_all():
    try:
        # Hard delete everything currently in trash (deleted_at not null)
        _ensure_items_prep_tables()
        conn = sqlite3.connect('bol.db')
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute('SELECT DISTINCT upc FROM items_prep_images WHERE deleted_at IS NOT NULL')
        upcs = [r['upc'] for r in cur.fetchall()]
        conn.close()
        total = 0
        for u in upcs:
            # hard=1 ensures permanent deletion
            with app.test_request_context(query_string={'hard':'1'}):
                resp = api_items_prep_diagnostic_delete_photos(u)
                total += 1
        return jsonify({'success': True, 'deleted_groups': total})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/trash/purge_expired', methods=['POST'])
def api_trash_purge_expired():
    try:
        count = _purge_expired_trash()
        return jsonify({'success': True, 'purged': count})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/bol_items', methods=['GET'])
def api_bol_items():
    """Return BOL items with sorting and filters: lot (exact), import_date (exact), sort by date/name/qty."""
    try:
        _ensure_bol_list_status_column()
        sort = request.args.get('sort', 'date_desc')
        lot = (request.args.get('lot') or '').strip()
        import_date = (request.args.get('import_date') or '').strip()
        q = (request.args.get('q') or '').strip()
        # Strip leading zeros from barcode searches
        q_stripped = q.lstrip('0') if q and q.isdigit() else q
        status_filter = (request.args.get('status') or '').strip().lower()
        page = int(request.args.get('page', 1))
        limit = int(request.args.get('limit', 25))
        if page < 1: page = 1
        if limit < 1 or limit > 100: limit = 25
        offset = (page - 1) * limit
        print(f"[api_bol_items] Filters - lot: '{lot}', import_date: '{import_date}', q: '{q_stripped}', status_filter: '{status_filter}'")
        conn = sqlite3.connect('bol.db')
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='bol_items'")
        if not cur.fetchone():
            conn.close()
            return jsonify({'results': []})
        # Ensure prep tables exist for left join and statuses
        _ensure_items_prep_tables()
        cur.execute("PRAGMA table_info(bol_items)")
        cols = [r[1] for r in cur.fetchall()]
        has_temporary = any(c.lower() == 'temporary' for c in cols)
        where = []
        params = []
        # Treat 'all' or 'all lots' as no lot filter
        if lot and lot.lower() not in ('all', 'all lots'):
            where.append('b.lot_number = ?')
            params.append(lot)
        if import_date:
            where.append('b.import_date = ?')
            params.append(import_date)
        if q_stripped:
            where.append('(b.upc LIKE ? COLLATE NOCASE OR b.item_description LIKE ? COLLATE NOCASE)')
            like = f"%{q_stripped}%"
            params.extend([like, like])
        # Build WHERE clause only when we actually have conditions
        where_sql = (' WHERE ' + ' AND '.join(where)) if where else ''
        # Build sort
        order_sql = ' ORDER BY '
        # Support sorting by last_edited (prep_updated_at if present, else import_date)
        if sort == 'date_asc':
            order_sql += "b.import_date ASC, b.id ASC"
        elif sort == 'name':
            order_sql += "b.item_description COLLATE NOCASE ASC"
        elif sort == 'last_edited':
            # newest last_edited first
            order_sql += "COALESCE(s.updated_at, b.import_date) DESC, b.id DESC"
        elif sort == 'last_edited_asc':
            order_sql += "COALESCE(s.updated_at, b.import_date) ASC, b.id ASC"
        else:
            # date_desc default
            order_sql += "b.import_date DESC, b.id DESC"
        # Left join items_prep_status to include status
        # Use the computed where_sql which may be empty
        # Get total count
        count_sql = 'SELECT COUNT(*) FROM bol_items b LEFT JOIN items_prep_status s ON s.upc = b.upc ' + where_sql
        cur.execute(count_sql, params)
        total = cur.fetchone()[0]
        
        sql = (
            'SELECT b.id, b.upc, b.item_description, b.image_url, b.lot_number, b.bol_number, b.import_date, b.list_status, b.quantity, ' +
            ('b.temporary, ' if has_temporary else '') +
            's.status as prep_status, s.reason as prep_reason, s.note as prep_note, s.updated_at as prep_updated_at '
            'FROM bol_items b '
            'LEFT JOIN items_prep_status s ON s.upc = b.upc '
            + where_sql + order_sql
        )
        sql += ' LIMIT ? OFFSET ?'
        params.extend([limit, offset])
        print(f"[api_bol_items] SQL: {sql}")
        print(f"[api_bol_items] Params: {params}")
        cur.execute(sql, params)
        rows_all = [dict(r) for r in cur.fetchall()]
        print(f"[api_bol_items] rows_all count before status filter: {len(rows_all)}")
        conn.close()
        # Build a status map keyed by both raw UPC and normalized UPC to handle formats like '16094950.0'
        status_map = {}
        try:
            _ensure_items_prep_tables()
            c2 = sqlite3.connect('bol.db')
            c2.row_factory = sqlite3.Row
            k2 = c2.cursor()
            k2.execute('SELECT upc, status, reason, note, updated_at FROM items_prep_status')
            for rr in k2.fetchall():
                raw_upc = rr['upc']
                st = {'prep_status': rr['status'], 'prep_reason': rr['reason'], 'prep_note': rr['note'], 'prep_updated_at': rr['updated_at']}
                if raw_upc:
                    status_map[str(raw_upc)] = st
                    nu = _normalize_upc(raw_upc)
                    status_map[nu] = st
            c2.close()
        except Exception:
            status_map = {}
        # Apply status filter in Python for flexibility (unchecked = no status or explicit 'unchecked')
        def status_of(row):
            # fallback to status_map using normalized upc if join didn't match
            st = (row.get('prep_status') or '').strip().lower()
            if not st:
                up = _normalize_upc(row.get('upc'))
                sm = status_map.get(up)
                if sm:
                    row['prep_status'] = sm.get('prep_status')
                    row['prep_reason'] = sm.get('prep_reason')
                    row['prep_note'] = sm.get('prep_note')
                    row['prep_updated_at'] = sm.get('prep_updated_at')
                    st = (row.get('prep_status') or '').strip().lower()
            return st if st in ('good','bad','unchecked') else 'unchecked'
        rows = rows_all
        sf = status_filter.replace(' ', '_') if status_filter else ''
        if sf in ('good','bad','unchecked'):
            if sf == 'unchecked':
                rows = [r for r in rows_all if status_of(r) == 'unchecked']
            else:
                rows = [r for r in rows_all if status_of(r) == sf]
        elif sf in ('listed','not_listed'):
            if sf == 'listed':
                rows = [r for r in rows_all if (str(r.get('list_status') or '').strip().lower() == 'listed')]
            else:
                rows = [r for r in rows_all if not (str(r.get('list_status') or '').strip().lower() == 'listed')]
        # Normalize for UI
        results = []
        for r in rows:
            # Determine last_edited: prefer items_prep_status.updated_at, fall back to import_date
            last_edited = r.get('prep_updated_at') or r.get('import_date') or ''
            results.append({
                'id': r.get('id'),
                'title': r.get('item_description') or '',
                'image': r.get('image_url') or '',
                'upc': r.get('upc') or '',
                'lot_number': r.get('lot_number') or '',
                'bol_number': r.get('bol_number') or '',
                'import_date': r.get('import_date') or '',
                'last_edited': last_edited,
                'defect': (r.get('prep_reason') or ''),
                'status': (r.get('prep_status') or 'unchecked'),
                'list_status': (r.get('list_status') or ''),
                'temporary': r.get('temporary'),
                'quantity': r.get('quantity') or 1
            })
        return jsonify({'results': results, 'total': total, 'page': page, 'limit': limit})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/bulk_delete_bol_items', methods=['POST'])
def api_bulk_delete_bol_items():
    """Delete selected BOL items, only if they are temporary (duplicate) entries or custom 777 barcodes."""
    try:
        data = request.get_json() or {}
        ids = data.get('ids', [])
        if not ids:
            return jsonify({'success': False, 'error': 'No ids provided'}), 400
        # Load UPCs for the requested ids and only allow deletion for UPCs that:
        # 1. End with "-<number>" (e.g., 858557007115-1) - duplicate entries
        # 2. Start with "777" - custom created items
        # Base UPCs (no dash-number suffix and not starting with 777) are protected.
        import re
        conn = sqlite3.connect('bol.db')
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        placeholders = ','.join('?' for _ in ids)
        cur.execute(f'SELECT id, upc FROM bol_items WHERE id IN ({placeholders})', tuple(ids))
        rows = cur.fetchall()
        deletable_ids = []
        protected_ids = []
        for r in rows:
            upc_val = r['upc']
            s = '' if upc_val is None else str(upc_val).strip()
            # Allow delete if UPC ends with -[digits] OR starts with 777
            if re.search(r'-\d+$', s) or s.startswith('777'):
                deletable_ids.append(r['id'])
            else:
                protected_ids.append(r['id'])
        deleted = 0
        if deletable_ids:
            ph2 = ','.join('?' for _ in deletable_ids)
            cur.execute(f'DELETE FROM bol_items WHERE id IN ({ph2})', tuple(deletable_ids))
            deleted = cur.rowcount
            conn.commit()
        conn.close()
        return jsonify({'success': True, 'deleted': deleted, 'protected': protected_ids, 'attempted': ids})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/bol_lots', methods=['GET'])
def api_bol_lots():
    """Return list of available lots with most recent import_date. Sorted newest first."""
    try:
        conn = sqlite3.connect('bol.db')
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='bol_items'")
        if not cur.fetchone():
            conn.close()
            return jsonify({'lots': []})
        cur.execute("PRAGMA table_info(bol_items)")
        cols = [r[1] for r in cur.fetchall()]
        has_temporary = any(c.lower() == 'temporary' for c in cols)
        if has_temporary:
            sql = (
                "SELECT lot_number, MAX(import_date) AS import_date "
                "FROM bol_items "
                "WHERE (temporary IS NULL OR temporary = 0) AND TRIM(COALESCE(lot_number,'')) <> '' "
                "GROUP BY lot_number "
                "ORDER BY import_date DESC"
            )
        else:
            sql = (
                "SELECT lot_number, MAX(import_date) AS import_date "
                "FROM bol_items "
                "WHERE TRIM(COALESCE(lot_number,'')) <> '' "
                "GROUP BY lot_number "
                "ORDER BY import_date DESC"
            )
        cur.execute(sql)
        lots = []
        for r in cur.fetchall():
            lots.append({
                'lot_number': r['lot_number'],
                'import_date': r['import_date'] or ''
            })
        conn.close()
        return jsonify({'lots': lots})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/bol_items/list_status', methods=['POST'])
def api_bol_items_set_list_status():
    """Set list_status for a BOL item by UPC. JSON: { upc, list_status } where list_status in ['listed', '']"""
    try:
        data = request.get_json() or {}
        upc = _normalize_upc(data.get('upc'))
        list_status = (data.get('list_status') or '').strip().lower()
        if not upc:
            return jsonify({'success': False, 'error': 'Missing upc'}), 400
        if list_status not in ('listed', ''):
            return jsonify({'success': False, 'error': 'Invalid list_status'}), 400
        _ensure_bol_list_status_column()
        conn = sqlite3.connect('bol.db')
        cur = conn.cursor()
        if list_status:
            cur.execute('UPDATE bol_items SET list_status=? WHERE upc = ? COLLATE NOCASE', (list_status, upc))
        else:
            cur.execute('UPDATE bol_items SET list_status=NULL WHERE upc = ? COLLATE NOCASE', (upc,))
        conn.commit()
        updated = cur.rowcount
        conn.close()
        return jsonify({'success': True, 'updated': updated})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/cleanup_temporary_entry', methods=['POST'])
def api_cleanup_temporary_entry():
    """Delete a temporary bol_items entry that was created but not completed."""
    try:
        data = request.get_json() or {}
        upc = _normalize_upc(data.get('upc'))
        if not upc:
            return jsonify({'success': False, 'error': 'Missing upc'}), 400
        
        conn = sqlite3.connect('bol.db')
        cur = conn.cursor()
        # Only delete if it's marked as temporary
        cur.execute('DELETE FROM bol_items WHERE upc = ? COLLATE NOCASE AND temporary = 1', (upc,))
        deleted = cur.rowcount
        conn.commit()
        conn.close()
        
        return jsonify({'success': True, 'deleted': deleted})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500
def api_items_prep_diagnostic_photos_zip(upc):
    """Bundle all diagnostic photos for a UPC into a zip and return as attachment."""
    try:
        upc_n = _normalize_upc(upc)
        _ensure_items_prep_tables()
        conn = sqlite3.connect('bol.db')
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute("SELECT image_path FROM items_prep_images WHERE upc = ? COLLATE NOCASE AND (deleted_at IS NULL OR TRIM(COALESCE(deleted_at,'')) = '') ORDER BY created_at DESC, id DESC", (upc_n,))
        rows = cur.fetchall()
        conn.close()
        if not rows:
            return jsonify({'error': 'No photos for this UPC'}), 404
        import zipfile
        mem = io.BytesIO()
        with zipfile.ZipFile(mem, mode='w', compression=zipfile.ZIP_DEFLATED) as zf:
            for r in rows:
                rel = r['image_path']
                # Build absolute path
                abs_path = os.path.join(app.root_path, 'static', rel.replace('..','').replace('\\','/').split('static/')[-1]) if not os.path.isabs(rel) else rel
                # Fix double static if rel already starts with items_prep/
                if not os.path.isabs(rel):
                    abs_path = os.path.join(app.root_path, 'static', rel)
                try:
                    # Name inside zip: use basename
                    arcname = os.path.basename(abs_path)
                    if os.path.isfile(abs_path):
                        zf.write(abs_path, arcname)
                except Exception as e:
                    print('Zip add failed:', e)
        mem.seek(0)
        filename = f"diagnostic_{upc_n}.zip"
        return send_file(mem, mimetype='application/zip', as_attachment=True, download_name=filename)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route("/token-status")
def token_status():
    try:
        tokens = load_tokens()
        expired = is_expired(tokens)
        access_token = get_access_token()  # this will refresh if expired

        return jsonify({
            "token_expired": expired,
            "access_token": access_token[:40] + "...",  # for safety
            "expires_at": tokens.get("expires_at"),
            "current_time": int(time.time()),
            "seconds_until_expiry": int(tokens.get("expires_at") - time.time())
        })

    except Exception as e:
        return jsonify({"error": str(e)})

def start_cloudflare_tunnel():
    subprocess.Popen([
        "cloudflared",
        "tunnel",
        "--config",
        "C:\\Users\\boxatron\\.cloudflared\\config.yml",
        "run",
        "mytunnel"
    ])

@app.route("/", methods=["GET", "POST"])
def home():
    shelf = request.args.get("shelf")
    return render_template("index.html", shelf=shelf)


@app.route("/callback")
def callback():
    code = request.args.get("code")
    if not code:
        return "Authorization failed or cancelled."

    # Step 5: exchange this code for an access token
    return f"Authorization code: {code}"
@app.route("/privacy")
def privacy():
    return "<h1>Privacy Policy</h1><p>No user data is stored or shared. This app is for sandbox testing only.</p>"



@app.route("/search", methods=["POST"])
def search():
    query = request.form["query"]
    result = find_item(query)
    return render_template("index.html", search_result=result)


# Populate searchRack on startup (best-effort). Guard against Flask reloader by checking if in main thread.
def maybe_run_startup_enrich():
    # only run once in main process
    try:
        if os.environ.get('WERKZEUG_RUN_MAIN') == 'true' or os.environ.get('FLASK_ENV') == 'production' or not os.getenv('FLASK_DEBUG'):
            print('Starting searchRack enrichment on startup...')
            # run in background so startup isn't blocked heavily
            _run_enrich_in_background()
    except Exception as e:
        print('Failed to start startup enrich:', e)


@app.route('/api/refresh_searchrack', methods=['POST'])
def api_refresh_searchrack():
    # no auth as requested
    if _enrich_lock.locked():
        return jsonify({'success': False, 'message': 'Enrichment already running'})
    started = _run_enrich_in_background()
    if not started:
        return jsonify({'success': False, 'message': 'Failed to start enrichment'})
    return jsonify({'success': True, 'message': 'Enrichment started'})


@app.route('/api/enrich_status', methods=['GET'])
def api_enrich_status():
    return jsonify(_enrich_status)

SHELF_COORDS = [
    {"gr1":
         {
        "s6":(220, 163, 753, 407),
        "s5": (90, 825, 930, 990),
        "s4":(90, 465, 930, 630),
        "s3":(90, 645, 930, 810),
        "s2":(90, 825, 930, 990),
        "s1":(90, 825, 930, 990)
         }
    },
    {"gr2":
    {
        "s6":(220, 163, 753, 407),
        "s5": (90, 825, 930, 990),
        "s4":(90, 465, 930, 630),
        "s3":(90, 645, 930, 810),
        "s2":(90, 825, 930, 990),
        "s1":(90, 825, 930, 990)
    }
    }
]

@app.route("/highlight")
def highlight():
    shelf_name = request.args.get("shelf", "").lower().strip()
    print(f"[DEBUG] highlight() called with shelf_name: {shelf_name}")
    if not shelf_name or len(shelf_name) < 4 or not shelf_name.startswith("gr"):
        return "Invalid shelf parameter", 400
    rack = shelf_name[:3]  # e.g., 'gr1'
    shelf = shelf_name[3:]  # e.g., 's6'
    image_path = f"static/{rack}.png"
    # SHELF_COORDS is a list of dicts, find the dict for this rack
    coords = None
    for rack_dict in SHELF_COORDS:
        if rack in rack_dict:
            coords = rack_dict[rack].get(shelf)
            break
    if coords is None:
        return f"No coordinates found for {rack} {shelf}", 404
    try:
        img = Image.open(image_path).convert("RGB")
    except Exception as e:
        return f"Image not found: {image_path}", 404
    draw = ImageDraw.Draw(img)
    draw.rectangle(coords, outline="green", width=30)
    img.thumbnail((180, 80))  # Substantially decrease image size
    img_io = io.BytesIO()
    img.save(img_io, "PNG")
    img_io.seek(0)
    return send_file(img_io, mimetype="image/png")


####################################

@app.route("/database")
def show_inventory():
    q = request.args.get('q', '').strip()
    status = request.args.get('status', '').strip()
    sort = request.args.get('sort', 'ID')
    dir = request.args.get('dir', 'asc')
    allowed_sorts = ['ID','Title','ItemID','SKU','Price','Quantity','List_State','Sold_Date','List_Date']
    if sort not in allowed_sorts:
        sort = 'ID'
    if dir not in ['asc','desc']:
        dir = 'asc'
    conn = sqlite3.connect("ebayStore.db")  # fixed typo here
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    sql = "SELECT * FROM INVENTORY WHERE 1=1"
    params = []
    if q:
        sql += " AND (Title LIKE ? OR ItemID LIKE ? OR SKU LIKE ? OR Price LIKE ? OR Quantity LIKE ? OR List_State LIKE ? OR Sold_Date LIKE ? OR List_Date LIKE ?)"
        for _ in range(8):
            params.append(f"%{q}%")
    if status:
        sql += " AND List_State = ?"
        params.append(status)
    sql += f" ORDER BY {sort} {dir.upper()}"
    cursor.execute(sql, params)
    items = cursor.fetchall()
    conn.close()
    return render_template("inventory.html", items=items)


# inventory flow variables for database injection

same_position = None
position_code = None
barcode = None
pictureposition_path = None




@app.route('/additemtrue', methods=['POST'])
def additemtrue():
    global same_position
    global position_code
    global barcode
    global pictureposition_path
    
    # Get values from form data (preferred) or fall back to global variables
    form_barcode = request.form.get('barcode', '').strip()
    form_position = request.form.get('item_position', '').strip()
    form_pictureposition = request.form.get('pictureposition', '').strip()
    
    # Use form data if available, otherwise use global variables
    final_barcode = form_barcode or barcode
    final_position = form_position or position_code
    final_pictureposition = form_pictureposition or pictureposition_path
    
    # Validate that we have required data
    if not final_barcode:
        print("ERROR: No barcode provided!")
        return "Error: Barcode is required", 400
    
    if not final_position and not final_pictureposition:
        print("ERROR: No position provided!")
        return "Error: Position is required", 400
    
    try:
        # If a picture position was used, compress and convert to B&W
        if final_pictureposition:
            abs_path = os.path.join(os.getcwd(), final_pictureposition)
            try:
                img = Image.open(abs_path)
                img = img.convert('L')  # Convert to grayscale
                img.thumbnail((400, 400))  # Resize to max 400x400
                img.save(abs_path, optimize=True, quality=40)
            except Exception as e:
                print(f"Image processing failed: {e}")
        print("Flow Complete, adding to SearchRack....")
        # If picture position is set, store 'picture' in ITEM_POSITION
        item_position_to_store = 'picture' if final_pictureposition else final_position
        addToSearchRack(item_position_to_store, final_barcode, None, final_pictureposition)
        print(f"Added to searchRack: position={item_position_to_store}, barcode={final_barcode}, pictureposition={final_pictureposition}")
        
        # Clear global variables (but keep position if locked)
        if not same_position:
            position_code = None
            pictureposition_path = None
        barcode = None
    except Exception as e:
        print("something went wrong with adding to RACK", e)
        import traceback
        traceback.print_exc()
        return f"Error: {str(e)}", 500
    # Add script to clear sessionStorage after successful add
    clear_script = '''<script>
        sessionStorage.removeItem('barcode');'''
    # Only clear position if not locked
    if not same_position:
        clear_script += '''
        sessionStorage.removeItem('item_position');
        sessionStorage.removeItem('pictureposition_path');'''
    clear_script += '''
    </script>'''
    if same_position:
        return render_template("barcode.html") + clear_script
    else:
        return render_template("position.html") + clear_script




@app.route("/barcode")
def barcode_page():
    # If redirected from pictureposition, set global pictureposition_path from sessionStorage (via query param)
    from flask import request
    global pictureposition_path
    if request.args.get("pictureposition") == "1":
        # Try to get the path from sessionStorage via a hidden form or AJAX (handled in JS below)
        pass  # Will be handled by JS below
    return render_template("barcode.html")

@app.route("/pictures")
def pictures_page():
    return render_template("pictures.html")

#adding inventory flow #1/3
@app.route("/position")
def position_page():
    return render_template("position.html")


@app.route('/position/diagnostic', methods=['POST'])
def position_diagnostic():
    """Handle position submission from diagnostic pages."""
    try:
        upc = request.form.get('upc', '').strip()
        location = request.form.get('scanned_result', '').strip()
        pictureposition = request.form.get('pictureposition', '').strip()
        return_url = request.form.get('return_url', '').strip()
        
        if not upc:
            return jsonify({'success': False, 'error': 'Missing UPC'}), 400
        
        upc = _normalize_upc(upc)
        _ensure_items_prep_tables()
        
        import datetime
        ts = datetime.datetime.utcnow().isoformat()
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
        conn.close()
        
        # Update searchRack.db
        if location or pictureposition:
            try:
                search_conn = sqlite3.connect('searchRack.db')
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
                        QUANTITY INTEGER,
                        CREATED_AT TEXT
                    )
                ''')
                # Check if entry exists
                search_cur.execute('SELECT ID FROM SEARCHRACK WHERE BARCODE = ? COLLATE NOCASE', (upc,))
                existing_sr = search_cur.fetchone()
                
                if existing_sr:
                    # Update existing
                    search_cur.execute('''
                        UPDATE SEARCHRACK 
                        SET ITEM_POSITION = ?, PICTUREPOSITION = ?, TITLE = ?, CREATED_AT = ?
                        WHERE BARCODE = ? COLLATE NOCASE
                    ''', (location, pictureposition, title, ts, upc))
                else:
                    # Insert new
                    search_cur.execute('''
                        INSERT INTO SEARCHRACK (TITLE, BARCODE, ITEM_POSITION, PICTUREPOSITION, CREATED_AT)
                        VALUES (?, ?, ?, ?, ?)
                    ''', (title, upc, location, pictureposition, ts))
                
                search_conn.commit()
                search_conn.close()
            except Exception as e:
                print(f'Warning: Failed to update searchRack: {e}')
        
        # Redirect back to the diagnostic page
        if return_url:
            return redirect(return_url)
        else:
            return redirect(f'/item-prep/diagnostic?upc={upc}')
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


#inventory flow #2
@app.route('/submitposition', methods=['POST'])
def process_position():
    global position_code
    global pictureposition_path
    position_code = request.form.get('scanned_result')
    pictureposition_path = request.form.get('pictureposition')
    print("Received scanned code:", position_code)
    print("Received picture position path:", pictureposition_path)
    return render_template("barcode.html")


#inventory flow #3
@app.route('/submitbarcode', methods=['POST'])
def process_barcode():
    global barcode
    barcode = request.form.get('scanned_result')
    print("Received scanned code:", barcode)

    return render_template("additem.html")



@app.route('/additem', methods=['POST'])
def additem_page():
    #return pictures

    return render_template("additem.html")


@app.route('/toggle', methods=['POST'])
def toggle():
    global same_position
    data = request.get_json()
    is_checked = data.get('checked', False)
    same_position = is_checked
    print("Checkbox state:", is_checked)  # True or False

    # Respond with JSON so the page doesn't change
    return jsonify({'success': True, 'message': f'Checkbox is {"ON" if is_checked else "OFF"}'})
# order placement flow variables for database injection











def get_high_res_image_url(url):
    if url and "s-l" in url:
        return url.replace("s-l140.jpg", "s-l1600.jpg").replace("s-l500.jpg", "s-l1600.jpg")
    return url


def orders():
    print("✅ Now running orders()...")
    headers = {
        "X-EBAY-API-SITEID": "0",
        "X-EBAY-API-COMPATIBILITY-LEVEL": "967",
        "X-EBAY-API-CALL-NAME": "GetMyeBaySelling",
        "X-EBAY-API-DEV-NAME": os.getenv("EBAY_PROD_DEV_ID"),
        "X-EBAY-API-APP-NAME": os.getenv("EBAY_PROD_APP_ID"),
        "X-EBAY-API-CERT-NAME": os.getenv("EBAY_PROD_CERT_ID"),
        "Content-Type": "text/xml"
    }
    entries = 100
    page_number = 1
    total_pages = 20
    count = 0
    # Ensure UPC and UPC_Processed columns exist
    try:
        conn = sqlite3.connect('ebayStore.db')
        cur = conn.cursor()
        cur.execute("PRAGMA table_info(INVENTORY)")
        columns = [row[1] for row in cur.fetchall()]
        if "UPC" not in columns:
            cur.execute("ALTER TABLE INVENTORY ADD COLUMN UPC TEXT")
        if "UPC_Processed" not in columns:
            cur.execute("ALTER TABLE INVENTORY ADD COLUMN UPC_Processed INTEGER DEFAULT 0")
        conn.commit()
        conn.close()
    except Exception as alter_e:
        print(f"Failed to ensure UPC/UPC_Processed columns exist: {alter_e}")
    # Query for missing UPCs and not processed
    try:
        conn = sqlite3.connect('ebayStore.db')
        cur = conn.cursor()
        cur.execute("SELECT ItemID FROM INVENTORY WHERE (UPC IS NULL OR UPC = '' OR UPC = 'null') AND (UPC_Processed IS NULL OR UPC_Processed = 0)")
        item_ids_missing_upc = set(row[0] for row in cur.fetchall() if row[0])
        conn.close()
    except Exception as e:
        print(f"Failed to get ItemIDs missing UPC: {e}")
        item_ids_missing_upc = set()
    processed_upc_ids = set()
    while page_number <= total_pages:

        xml_payload = f'''
        <?xml version="1.0" encoding="utf-8"?>
            <GetMyeBaySellingRequest xmlns="urn:ebay:apis:eBLBaseComponents">
              <RequesterCredentials>
                <eBayAuthToken>{os.getenv("EBAY_OLDAUTH_TOKEN")}</eBayAuthToken>
              </RequesterCredentials>
              <ErrorLanguage>en_US</ErrorLanguage>
              <WarningLevel>High</WarningLevel>
              <ActiveList>
                <Sort>TimeLeft</Sort>
                <Pagination>
                  <EntriesPerPage>{entries}</EntriesPerPage>
                  <PageNumber>{page_number}</PageNumber>
                </Pagination>
              </ActiveList>
            <SoldList>
                <Include>true</Include>
                <DurationInDays>60</DurationInDays>
                    <Pagination>
                  <EntriesPerPage>{entries}</EntriesPerPage>
                  <PageNumber>{page_number}</PageNumber>
                </Pagination>
            </SoldList>
            <UnsoldList>
                <Include>true</Include>
                <Pagination>
                      <EntriesPerPage>{entries}</EntriesPerPage>
                      <PageNumber>{page_number}</PageNumber>
                    </Pagination>
                    
                </UnsoldList>
            
            </GetMyeBaySellingRequest>'''

        ns = {'ebay': 'urn:ebay:apis:eBLBaseComponents'}
        response = requests.post("https://api.ebay.com/ws/api.dll", headers=headers, data=xml_payload, timeout=20)
        root = ET.fromstring(response.text)

        '''
        rough_string = ET.tostring(root, encoding="utf-8")
        # Parse that into a minidom object
        dom = minidom.parseString(rough_string)
        # Pretty print
        pretty_xml = dom.toprettyxml(indent="  ")
        print(pretty_xml)
        '''



        ack = root.find('ebay:Ack', ns)
        if ack is None or ack.text != "Success":
            print(f"❌ API Error on page {page_number}: {ack.text if ack is not None else 'No Ack'}")
            #break






        #ACTIVE LIST ITEMS
        ActiveItems = root.findall('.//ebay:ActiveList/ebay:ItemArray/ebay:Item', ns)
        for item in ActiveItems:

            title = item.find('ebay:Title', ns)
            item_id = item.find('ebay:ItemID', ns)
            sku = item.find('ebay:SKU', ns)
            price = item.find('.//ebay:CurrentPrice', ns)
            quantity = item.find('ebay:Quantity', ns)
            list_date = item.find('ebay:ListingDetails/ebay:StartTime', ns)
            sold_date = item.find('ebay:ListingDetails/ebay:EndTime', ns)
            list_state = "Active"
            URL = f"https://www.ebay.com/itm/{item_id.text}"

            picture_url = item.find('.//ebay:PictureDetails/ebay:GalleryURL', ns)
            high_res_url = get_high_res_image_url(picture_url.text) if picture_url is not None else "No image"



            # add to database
            ebayStoreDB(title = title.text if title is not None else "N/A",
                       item_id = item_id.text if item_id is not None else "N/A",
                       sku = sku.text if sku is not None else "None",
                       price = price.text if price is not None else "N/A",
                       quantity = quantity.text if quantity is not None else "N/A",
                       image = high_res_url,
                       List_State = list_state,
                       Sold_Date=sold_date.text if sold_date is not None else "None",
                       List_Date=list_date.text if list_date is not None else "None",
                       URL = URL if URL is not None else "None"
            )




            print("Item", count)
            print("📦 Title:", title.text if title is not None else "N/A")
            print("🆔 ItemID:", item_id.text if item_id is not None else "N/A")
            print("🔖 SKU:", sku.text if sku is not None else "None")
            print("💲 Price:", price.text if price is not None else "N/A")
            print("🔢 Quantity:", quantity.text if quantity is not None else "N/A")
            print("🖼️ Image:", high_res_url)
            print("🛣️ URL:", f"https://www.ebay.com/itm/{item_id.text}")
            print("—" * 40)
            count += 1

        # finding unsold item list
        UnsoldItems = root.findall('.//ebay:UnsoldList/ebay:ItemArray/ebay:Item', ns)
        for item in UnsoldItems:
            title = item.find('ebay:Title', ns)
            item_id = item.find('ebay:ItemID', ns)
            sku = item.find('ebay:SKU', ns)
            price = item.find('.//ebay:CurrentPrice', ns)
            quantity = item.find('ebay:Quantity', ns)
            list_date = item.find('ebay:ListingDetails/ebay:StartTime', ns)
            sold_date = item.find('ebay:ListingDetails/ebay:EndTime', ns)
            list_state = "Unsold"
            URL = f"https://www.ebay.com/itm/{item_id.text}"

            picture_url = item.find('.//ebay:PictureDetails/ebay:GalleryURL', ns)
            high_res_url = get_high_res_image_url(picture_url.text) if picture_url is not None else "No image"

            # add to database
            ebayStoreDB(title=title.text if title is not None else "N/A",
                       item_id=item_id.text if item_id is not None else "N/A",
                       sku=sku.text if sku is not None else "None",
                       price=price.text if price is not None else "N/A",
                       quantity=quantity.text if quantity is not None else "N/A", image=high_res_url,
                       List_State=list_state,
                       Sold_Date=sold_date.text if sold_date is not None else "None",
                       List_Date=list_date.text if list_date is not None else "None",
                       URL=URL if URL is not None else "None"
                       )

            print("Item", count)
            print("📦 Title:", title.text if title is not None else "N/A")
            print("🆔 ItemID:", item_id.text if item_id is not None else "N/A")
            print("🔖 SKU:", sku.text if sku is not None else "None")
            print("💲 Price:", price.text if price is not None else "N/A")
            print("🔢 Quantity:", quantity.text if quantity is not None else "N/A")
            print("🖼️ Image:", high_res_url)
            print("🛣️ URL:", f"https://www.ebay.com/itm/{item_id.text}")
            print("—" * 40)
            count += 1

        # finding sold list items

        SoldItems = root.findall('.//ebay:SoldList/ebay:OrderTransactionArray/ebay:OrderTransaction/ebay:Transaction/ebay:Item', ns)

        for item in SoldItems:
            title = item.find('ebay:Title', ns)
            item_id = item.find('ebay:ItemID', ns)
            sku = item.find('ebay:SKU', ns)
            price = item.find('.//ebay:CurrentPrice', ns)
            quantity = item.find('ebay:Quantity', ns)
            sold_date = item.find('ebay:ListingDetails/ebay:EndTime', ns)
            list_date = item.find('ebay:ListingDetails/ebay:StartTime', ns)
            list_state = "Sold"
            URL = f"https://www.ebay.com/itm/{item_id.text}"
            picture_url = item.find('.//ebay:PictureDetails/ebay:GalleryURL', ns)
            high_res_url = get_high_res_image_url(picture_url.text) if picture_url is not None else "No image"

            # add to database
            ebayStoreDB(title=title.text if title is not None else "N/A",
                       item_id=item_id.text if item_id is not None else "N/A",
                       sku=sku.text if sku is not None else "None",
                       price=price.text if price is not None else "N/A",
                       quantity=quantity.text if quantity is not None else "N/A", image=high_res_url,
                       List_State=list_state,
                       Sold_Date=sold_date.text if sold_date is not None else "None",
                       List_Date=list_date.text if list_date is not None else "None",
                       URL = URL if URL is not None else "None"
                       )

            print("Item", count)
            print("📦 Title:", title.text if title is not None else "N/A")
            print("🆔 ItemID:", item_id.text if item_id is not None else "N/A")
            print("🔖 SKU:", sku.text if sku is not None else "None")
            print("💲 Price:", price.text if price is not None else "N/A")
            print("🔢 Quantity:", quantity.text if quantity is not None else "N/A")
            print("🖼️ Image:", high_res_url)
            print("🛣️ URL:", f"https://www.ebay.com/itm/{item_id.text}")
            print("—" * 40)
            count += 1

        # Collect all item IDs from Active, Unsold, and Sold lists
        all_item_ids = set()
        for item in ActiveItems:
            item_id = item.find('ebay:ItemID', ns)
            if item_id is not None and item_id.text not in [None, "N/A", "None", ""]:
                all_item_ids.add(item_id.text)
        for item in UnsoldItems:
            item_id = item.find('ebay:ItemID', ns)
            if item_id is not None and item_id.text not in [None, "N/A", "None", ""]:
                all_item_ids.add(item_id.text)
        for item in SoldItems:
            item_id = item.find('ebay:ItemID', ns)
            if item_id is not None and item_id.text not in [None, "N/A", "None", ""]:
                all_item_ids.add(item_id.text)

        # Ensure UPC column exists in ebayStore.db
        try:
            conn = sqlite3.connect('ebayStore.db')
            cur = conn.cursor()
            cur.execute("PRAGMA table_info(INVENTORY)")
            columns = [row[1] for row in cur.fetchall()]
            if "UPC" not in columns:
                cur.execute("ALTER TABLE INVENTORY ADD COLUMN UPC TEXT")
                conn.commit()
            conn.close()
        except Exception as alter_e:
            print(f"Failed to ensure UPC column exists: {alter_e}")

        # Only fetch UPCs for items that do not already have a UPC and not processed
        # Filter only those in both all_item_ids and item_ids_missing_upc, and not already processed
        to_lookup = [eid for eid in all_item_ids if eid in item_ids_missing_upc and eid not in processed_upc_ids]
        for eid in to_lookup:
            getitem_xml = f'''<?xml version="1.0" encoding="utf-8"?>
<GetItemRequest xmlns="urn:ebay:apis:eBLBaseComponents">
  <RequesterCredentials>
    <eBayAuthToken>{os.getenv("EBAY_OLDAUTH_TOKEN")}</eBayAuthToken>
  </RequesterCredentials>
  <ItemID>{eid}</ItemID>
  <DetailLevel>ReturnAll</DetailLevel>
</GetItemRequest>'''
            getitem_headers = headers.copy()
            getitem_headers["X-EBAY-API-CALL-NAME"] = "GetItem"
            try:
                getitem_resp = requests.post("https://api.ebay.com/ws/api.dll", headers=getitem_headers, data=getitem_xml, timeout=20)
                getitem_root = ET.fromstring(getitem_resp.text)
                product_details = getitem_root.find('.//{urn:ebay:apis:eBLBaseComponents}ProductListingDetails')
                upc = None
                if product_details is not None:
                    upc_elem = product_details.find('{urn:ebay:apis:eBLBaseComponents}UPC')
                    upc = upc_elem.text if upc_elem is not None else None
                print(f"Fetched UPC for ItemID {eid}: {upc}")
                # Update ebayStore.db with UPC and mark as processed
                try:
                    conn = sqlite3.connect('ebayStore.db')
                    cur = conn.cursor()
                    cur.execute("UPDATE INVENTORY SET UPC = ?, UPC_Processed = 1 WHERE ItemID = ?", (upc, eid))
                    conn.commit()
                    conn.close()
                    print(f"Updated UPC for ItemID {eid} in ebayStore.db and marked as processed")
                    processed_upc_ids.add(eid)
                except Exception as db_e:
                    print(f"Failed to update UPC for ItemID {eid} in ebayStore.db: {db_e}")
            except Exception as e:
                print(f"Failed to fetch UPC for ItemID {eid}: {e}")

        # Get total pages if first time
        if page_number == 1:
            page_info = root.find('.//ebay:PaginationResult', ns)
            if page_info is not None:
                total_pages = int(page_info.find('ebay:TotalNumberOfPages', ns).text)
            else:
                return
                #break  # no pagination info, likely no results

        page_number += 1



def get_ebay_orders(days=90):
    print(f"Getting eBay orders for the last {days} days...")
    # Trading API endpoint
    url = "https://api.ebay.com/ws/api.dll"
    headers = {
        "X-EBAY-API-SITEID": "0",
        "X-EBAY-API-COMPATIBILITY-LEVEL": "967",
        "X-EBAY-API-CALL-NAME": "GetOrders",
        "X-EBAY-API-DEV-NAME": os.getenv("EBAY_PROD_DEV_ID"),
        "X-EBAY-API-APP-NAME": os.getenv("EBAY_PROD_APP_ID"),
        "X-EBAY-API-CERT-NAME": os.getenv("EBAY_PROD_CERT_ID"),
        "Content-Type": "text/xml"
    }
    xml_payload = f'''
    <?xml version="1.0" encoding="utf-8"?>
    <GetOrdersRequest xmlns="urn:ebay:apis:eBLBaseComponents">
      <RequesterCredentials>
        <eBayAuthToken>{os.getenv("EBAY_OLDAUTH_TOKEN")}</eBayAuthToken>
      </RequesterCredentials>
      <OrderRole>Seller</OrderRole>
      <OrderStatus>All</OrderStatus>
      <NumberOfDays>{days}</NumberOfDays>
      <Pagination>
        <EntriesPerPage>100</EntriesPerPage>
        <PageNumber>1</PageNumber>
      </Pagination>
    </GetOrdersRequest>
    '''


    response = requests.post(url, headers=headers, data=xml_payload, timeout=30)
    root = ET.fromstring(response.text)
    print(root)
    '''
    rough_string = ET.tostring(root, encoding="utf-8")
    # Parse that into a minidom object
    dom = minidom.parseString(rough_string)
    # Pretty print
    pretty_xml = dom.toprettyxml(indent="  ")
    print(pretty_xml)
    '''

    ns = {'ebay': 'urn:ebay:apis:eBLBaseComponents'}
    print("right before for loop")
    for order in root.findall('.//ebay:Order', ns):
        print("inside first for loop")
        order_id = order.find('ebay:OrderID', ns)
        paid_time = order.find('ebay:PaidTime', ns)
        shipped_time = order.find('ebay:ShippedTime', ns)
        checkout_status = order.find('.//ebay:CheckoutStatus/ebay:Status', ns)
        shipping = order.find('.//ebay:ShippingAddress', ns)
        shipping_name = shipping.find('ebay:Name', ns) if shipping is not None else None
        shipping_street1 = shipping.find('ebay:Street1', ns) if shipping is not None else None
        shipping_street2 = shipping.find('ebay:Street2', ns) if shipping is not None else None
        shipping_city = shipping.find('ebay:CityName', ns) if shipping is not None else None
        shipping_state = shipping.find('ebay:StateOrProvince', ns) if shipping is not None else None
        shipping_postal_code = shipping.find('ebay:PostalCode', ns) if shipping is not None else None
        shipping_country = shipping.find('ebay:Country', ns) if shipping is not None else None
        
        # Extract shipping costs
        shipping_service_cost = order.find('.//ebay:ShippingServiceSelected/ebay:ShippingServiceCost', ns)
        shipping_insurance_cost = order.find('.//ebay:ShippingServiceSelected/ebay:ShippingInsuranceCost', ns)
        
        print("inside first for loop end")
        try:
            for transaction in order.findall('.//ebay:Transaction', ns):
                print("Storing eBay order transaction...")
                item = transaction.find('ebay:Item', ns)
                item_id = item.find('ebay:ItemID', ns) if item is not None else None
                title = item.find('ebay:Title', ns) if item is not None else None
                quantity = transaction.find('ebay:QuantityPurchased', ns)
                price = transaction.find('.//ebay:TransactionPrice', ns)
                seller_fee = transaction.find('.//ebay:FinalValueFee', ns)
                taxes = transaction.find('.//ebay:Taxes/ebay:TotalTaxAmount', ns)
                fees = transaction.find('.//ebay:TransactionSiteID', ns)  # Placeholder, adjust as needed
                
                # Extract actual cost of shipping from transaction
                shipping_cost = transaction.find('.//ebay:ActualShippingCost', ns)
                
                # Calculate estimated eBay seller fees (12.9% average: 12% FV fee + ~0.9% payment processing)
                # eBay charges fees on total amount (item price + shipping)
                item_price = float(price.text) if price is not None and price.text.replace('.', '', 1).isdigit() else 0
                shipping_amount = float(shipping_cost.text) if shipping_cost is not None and shipping_cost.text.replace('.', '', 1).isdigit() else 0
                estimated_seller_fee = (item_price + shipping_amount) * 0.129
                
                # Extract image URL from item and convert to high-res string
                picture_url = item.find('.//ebay:PictureDetails/ebay:GalleryURL', ns) if item is not None else None
                high_res_url = get_high_res_image_url(picture_url.text) if (picture_url is not None and picture_url.text) else None
                store_ebay_order({
                    'order_id': order_id.text if order_id is not None else None,
                    'item_id': item_id.text if item_id is not None else None,
                    'title': title.text if title is not None else None,
                    'quantity': int(quantity.text) if quantity is not None and quantity.text.isdigit() else None,
                    'price': float(price.text) if price is not None and price.text.replace('.', '', 1).isdigit() else None,
                    'checkout_status': checkout_status.text if checkout_status is not None else None,
                    'shipping_name': shipping_name.text if shipping_name is not None else None,
                    'shipping_street1': shipping_street1.text if shipping_street1 is not None else None,
                    'shipping_street2': shipping_street2.text if shipping_street2 is not None else None,
                    'shipping_city': shipping_city.text if shipping_city is not None else None,
                    'shipping_state': shipping_state.text if shipping_state is not None else None,
                    'shipping_postal_code': shipping_postal_code.text if shipping_postal_code is not None else None,
                    'shipping_country': shipping_country.text if shipping_country is not None else None,
                    'paid_time': paid_time.text if paid_time is not None else None,
                    'shipped_time': shipped_time.text if shipped_time is not None else None,
                    'seller_fee': estimated_seller_fee,
                    'taxes': float(taxes.text) if taxes is not None and taxes.text.replace('.', '', 1).isdigit() else None,
                    'fees': fees.text if fees is not None else None,
                    'shipping_cost': shipping_amount if shipping_amount > 0 else None,
                    'image': high_res_url,
                    'isHandled': '',
                    'isHandledDate': ''
                })
                print("Transaction stored:", {
                    'order_id': order_id.text if order_id is not None else None,
                    'item_id': item_id.text if item_id is not None else None,
                    'title': title.text if title is not None else None,
                    'quantity': int(quantity.text) if quantity is not None and quantity.text.isdigit() else None,
                    'price': float(price.text) if price is not None and price.text.replace('.', '', 1).isdigit() else None,
                    'checkout_status': checkout_status.text if checkout_status is not None else None,
                    'shipping_name': shipping_name.text if shipping_name is not None else None,
                    'shipping_street1': shipping_street1.text if shipping_street1 is not None else None,
                    'shipping_street2': shipping_street2.text if shipping_street2 is not None else None,
                    'shipping_city': shipping_city.text if shipping_city is not None else None,
                    'shipping_state': shipping_state.text if shipping_state is not None else None,
                    'shipping_postal_code': shipping_postal_code.text if shipping_postal_code is not None else None,
                    'shipping_country': shipping_country.text if shipping_country is not None else None,
                    'paid_time': paid_time.text if paid_time is not None else None,
                    'shipped_time': shipped_time.text if shipped_time is not None else None,
                    'seller_fee': float(seller_fee.text) if seller_fee is not None and seller_fee.text.replace('.', '', 1).isdigit() else None,
                    'taxes': float(taxes.text) if taxes is not None and taxes.text.replace('.', '', 1).isdigit() else None,
                    'fees': fees.text if fees is not None else None,
                    'image': picture_url,
                    'isHandled': '',
                    'isHandledDate': ''
                })
        except Exception as e:
            print("Failed to connect to sold.db:", e)

def finalize_barcodes():
    print("🔎 Finalizing barcodes in ebayStore.db...")
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
        conn.close()
        print("✅ Barcode finalization complete.")
    except Exception as e:
        print(f"❌ Error finalizing barcodes: {e}")

def start_flask():
    app.run(host="0.0.0.0", port=8080)

def start_tunnel():
    subprocess.Popen([
        "cloudflared",
        "tunnel",
        "--config",
        "C:\\Users\\boxatron\\.cloudflared\\config.yml",
        "run",
        "mytunnel"
    ])
    print("⏳ Cloudflare tunnel starting...")
    time.sleep(3)
    try:
        res = requests.get("http://localhost:5555/metrics")
        for line in res.text.splitlines():
            if "userURL" in line:
                public_url = line.split(" ")[-1]
                print(f"✅ Public URL: {public_url}")
                break
    except Exception as e:
        print("❌ Tunnel metrics not found:", e)


@app.route('/extractor/upload', methods=['POST'])
def extractor_upload():
    """Legacy route - kept for backward compatibility but not used by new UI"""
    if not BOL_AVAILABLE:
        return jsonify({'success': False, 'error': 'BOL extractor not available (pandas not installed)'})
    if 'excel_file' not in request.files or 'import_date' not in request.form:
        return jsonify({'success': False, 'error': 'Missing file or import date.'})
    file = request.files['excel_file']
    import_date = request.form['import_date']
    if file.filename == '':
        return jsonify({'success': False, 'error': 'No file selected.'})
    if file:
        print('DEBUG: Received file:', file.filename, 'Content-Type:', file.content_type, 'Size:', file.content_length)
        result = process_bol_excel(file, import_date)
        return jsonify(result)
    return jsonify({'success': False, 'error': 'Unknown error during file upload.'})

@app.route('/api/rawbol/upload', methods=['POST'])
def api_rawbol_upload():
    """Upload .xls file to raw BOL database and auto-sync"""
    if not BOL_AVAILABLE:
        return jsonify({'success': False, 'error': 'BOL extractor not available (pandas not installed)'})
    
    if 'excel_file' not in request.files or 'import_date' not in request.form:
        return jsonify({'success': False, 'error': 'Missing file or import date.'})
    
    file = request.files['excel_file']
    import_date = request.form['import_date'].strip()
    
    if file.filename == '':
        return jsonify({'success': False, 'error': 'No file selected.'})
    
    print(f'DEBUG: Received file: {file.filename}, Date: {import_date}')
    
    # Upload to rawbol.db - lot_number will be extracted from file
    # Use filename without extension as temporary lot_number for database storage
    import os
    temp_lot_number = os.path.splitext(file.filename)[0]
    
    result = process_bol_excel(file, temp_lot_number, import_date)
    
    # If upload successful, auto-sync ONLY THIS LOT to bol.db
    if result.get('success'):
        from rawbol_manager import sync_rawbol_to_bol
        # Use the lot_number from result (which is the extracted LOT #)
        lot_to_sync = result.get('lot_number')
        sync_result = sync_rawbol_to_bol(specific_lot=lot_to_sync)
        
        if sync_result.get('success'):
            # Combine results
            result['synced'] = True
            result['sync_updated'] = sync_result.get('updated', 0)
            result['sync_inserted'] = sync_result.get('inserted', 0)
        else:
            result['synced'] = False
            result['sync_error'] = sync_result.get('error', 'Unknown sync error')
    
    return jsonify(result)

@app.route('/api/rawbol/view', methods=['GET'])
def api_rawbol_view():
    """Get all raw BOL items"""
    from rawbol_manager import get_all_raw_bol_items
    result = get_all_raw_bol_items()
    return jsonify(result)

@app.route('/api/rawbol/logs', methods=['GET'])
def api_rawbol_logs():
    """Get upload logs"""
    from rawbol_manager import get_upload_logs
    result = get_upload_logs()
    return jsonify(result)

@app.route('/api/rawbol/stats', methods=['GET'])
def api_rawbol_stats():
    """Get rawbol.db statistics"""
    from rawbol_manager import get_rawbol_stats
    result = get_rawbol_stats()
    return jsonify(result)

@app.route('/api/rawbol/delete/<lot_number>', methods=['DELETE'])
def api_rawbol_delete_lot(lot_number):
    """Delete a specific lot and desync from bol.db"""
    from rawbol_manager import delete_lot
    result = delete_lot(lot_number)
    return jsonify(result)

@app.route('/api/rawbol/desync', methods=['POST'])
def api_rawbol_desync_all():
    """Desync (undo) ALL raw BOL from main BOL database"""
    from rawbol_manager import desync_all_rawbol
    result = desync_all_rawbol()
    return jsonify(result)

@app.route('/api/rawbol/items/<bol_number>', methods=['GET'])
def api_rawbol_items(bol_number):
    """Get items for a specific BOL with calculated average cost"""
    try:
        conn = sqlite3.connect('rawbol.db')
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        
        # Get BOL total cost from upload_logs
        cur.execute('SELECT total_client_cost FROM upload_logs WHERE lot_number = ?', (bol_number,))
        log_row = cur.fetchone()
        total_bol_cost = log_row['total_client_cost'] if log_row and log_row['total_client_cost'] else 0
        
        # Get items from raw_bol_items
        cur.execute('''
            SELECT 
                upc,
                item_description,
                quantity as original_qty,
                image_url,
                client_cost
            FROM raw_bol_items 
            WHERE bol_number = ?
            ORDER BY id
        ''', (bol_number,))
        items = [dict(row) for row in cur.fetchall()]
        
        # Calculate total quantity for avg cost calculation
        total_qty = sum(item['original_qty'] or 0 for item in items)
        avg_cost = (total_bol_cost / total_qty) if total_qty > 0 and total_bol_cost else 0
        
        conn.close()
        
        return jsonify({
            'success': True,
            'items': items,
            'total_bol_cost': total_bol_cost,
            'total_qty': total_qty,
            'avg_cost': avg_cost
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})



# Manual matcher routes removed


@app.route('/get-sold-orders', methods=['POST'])
def get_sold_orders_route():
    print("we're getting sold orders")
    try:
        days = request.json.get('days', 90) if request.is_json else 90
        
        # Sync eBay orders
        get_ebay_orders(days=days)
        
        # Also sync Amazon orders if available
        if AMAZON_AVAILABLE:
            try:
                print(f"🔄 Syncing Amazon orders (last {days} days)...")
                amazon = AmazonManager()
                amazon.sync_orders_to_db(days_back=days)
            except Exception as e:
                print(f"⚠️ Error syncing Amazon orders: {e}")
                # Don't fail the whole request if Amazon sync fails
        
        # Process inventory reduction after fetching sold orders
        from DBmanager import process_sold_orders_inventory_reduction
        process_sold_orders_inventory_reduction()
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})


@app.route('/api/amazon/test-connection', methods=['GET'])
def test_amazon_connection():
    """Test Amazon SP-API connection"""
    if not AMAZON_AVAILABLE:
        return jsonify({'success': False, 'error': 'Amazon integration not available'}), 500
    
    try:
        amazon = AmazonManager()
        success = amazon.test_connection()
        return jsonify({'success': success})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/amazon/sync-orders', methods=['POST'])
def sync_amazon_orders():
    """Sync Amazon orders to sold.db"""
    if not AMAZON_AVAILABLE:
        return jsonify({'success': False, 'error': 'Amazon integration not available'}), 500
    
    try:
        days = request.json.get('days', 30) if request.is_json else 30
        print(f"🔄 Syncing Amazon orders (last {days} days)...")
        
        amazon = AmazonManager()
        count = amazon.sync_orders_to_db(days_back=days)
        
        # Process inventory reduction after fetching sold orders
        from DBmanager import process_sold_orders_inventory_reduction
        process_sold_orders_inventory_reduction()
        
        return jsonify({'success': True, 'orders_synced': count})
    except Exception as e:
        print(f"❌ Error syncing Amazon orders: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/amazon/sync-listings', methods=['POST'])
def sync_amazon_listings():
    """Sync Amazon active listings to amazonStore.db"""
    if not AMAZON_AVAILABLE:
        return jsonify({'success': False, 'error': 'Amazon integration not available'}), 500
    
    try:
        print("🔄 Syncing Amazon listings...")
        
        amazon = AmazonManager()
        count = amazon.sync_listings_to_db()
        
        return jsonify({'success': True, 'listings_synced': count})
    except Exception as e:
        print(f"❌ Error syncing Amazon listings: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/amazon/orders', methods=['GET'])
def get_amazon_orders():
    """Get Amazon orders from sold.db"""
    try:
        days = int(request.args.get('days', 30))
        conn = sqlite3.connect('sold.db')
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        
        # Get orders from Amazon store
        cur.execute('''
            SELECT * FROM orders 
            WHERE store = 'amazon' 
            AND paid_time >= date('now', '-' || ? || ' days') 
            ORDER BY paid_time DESC
        ''', (days,))
        
        orders = cur.fetchall()
        conn.close()
        
        return jsonify({
            'success': True,
            'orders': [dict(order) for order in orders]
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/sold-orders', methods=['GET'])
def sold_orders():
    days = int(request.args.get('days', 1))
    conn = sqlite3.connect('sold.db')
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    # Only show orders from the last N days
    cur.execute('''SELECT * FROM orders WHERE paid_time >= date('now', '-' || ? || ' days') ORDER BY paid_time DESC''', (days,))
    orders = cur.fetchall()
    conn.close()
    return jsonify([dict(order) for order in orders])


@app.route('/get-order', methods=['GET'])
def get_order():
    order_id = request.args.get('id')
    if not order_id:
        return jsonify({'error': 'Missing order id'}), 400
    try:
        conn = sqlite3.connect('sold.db')
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute("SELECT * FROM orders WHERE id = ?", (order_id,))
        order = cur.fetchone()
        conn.close()
        
        if not order:
            return jsonify({'error': 'Order not found'}), 404
            
        # Convert sqlite3.Row to dict
        order_dict = dict(order)
        return jsonify(order_dict)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/mark-order-handled', methods=['POST'])
def mark_order_handled():
    data = request.get_json()
    order_id = data.get('id')
    
    if not order_id:
        return jsonify({'success': False, 'error': 'Missing order id'})
        
    try:
        conn = sqlite3.connect('sold.db')
        cur = conn.cursor()
        cur.execute("UPDATE orders SET isHandled = '1', isHandledDate = datetime('now') WHERE id = ?", (order_id,))
        conn.commit()
        conn.close()
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/mark-order-unhandled', methods=['POST'])
def mark_order_unhandled():
    data = request.get_json()
    order_id = data.get('id')
    
    if not order_id:
        return jsonify({'success': False, 'error': 'Missing order id'})
        
    try:
        conn = sqlite3.connect('sold.db')
        cur = conn.cursor()
        cur.execute("UPDATE orders SET isHandled = '', isHandledDate = NULL WHERE id = ?", (order_id,))
        conn.commit()
        conn.close()
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/sold/pending-removals', methods=['GET'])
def get_pending_removals():
    """Get list of sold orders pending automatic inventory removal (shipped but within 48h grace period)"""
    try:
        conn = sqlite3.connect('sold.db')
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        # Read grace period from settings (default 48)
        try:
            cur.execute("CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT)")
            conn.commit()
            cur.execute("SELECT value FROM settings WHERE key = 'removal_grace_hours'")
            row = cur.fetchone()
            grace_period_hours = int(row[0]) if row and str(row[0]).strip().isdigit() else 48
        except Exception:
            grace_period_hours = 48
        
        # Get orders that are shipped but not yet processed
        cur.execute('''
            SELECT id, order_id, item_id, barcode, title, quantity, 
                   shipped_time, paid_time, image, removal_cancelled
            FROM orders 
            WHERE rackupdated = 0 
            AND shipped_time IS NOT NULL 
            AND shipped_time != ''
            AND barcode IS NOT NULL
            AND barcode != ''
        ''')
        orders = cur.fetchall()
        
        # Calculate time remaining for each order
        import datetime
        now = datetime.datetime.utcnow()
        
        result = []
        for order in orders:
            try:
                shipped_str = order['shipped_time']
                if 'T' in shipped_str:
                    if shipped_str.endswith('Z'):
                        shipped_dt = datetime.datetime.fromisoformat(shipped_str.replace('Z', '+00:00'))
                    elif '+' in shipped_str or shipped_str.count('-') > 2:
                        shipped_dt = datetime.datetime.fromisoformat(shipped_str)
                    else:
                        shipped_dt = datetime.datetime.fromisoformat(shipped_str)
                else:
                    shipped_dt = datetime.datetime.fromisoformat(shipped_str)
                
                if shipped_dt.tzinfo:
                    shipped_dt = shipped_dt.replace(tzinfo=None)
                
                hours_since_shipped = (now - shipped_dt).total_seconds() / 3600
                hours_remaining = max(0, grace_period_hours - hours_since_shipped)
                is_eligible = hours_since_shipped >= grace_period_hours
                
                order_dict = dict(order)
                order_dict['hours_remaining'] = round(hours_remaining, 1)
                order_dict['is_eligible_for_removal'] = is_eligible
                result.append(order_dict)
            except Exception as e:
                print(f"Error processing order {order['order_id']}: {e}")
                continue
        
        conn.close()
        return jsonify({'success': True, 'orders': result, 'grace_hours': grace_period_hours})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/sold/cancel-removal/<int:order_id>', methods=['POST'])
def cancel_automatic_removal(order_id):
    """Cancel automatic inventory removal for a specific sold order"""
    try:
        conn = sqlite3.connect('sold.db')
        cur = conn.cursor()
        
        # Ensure removal_cancelled column exists
        try:
            cur.execute('PRAGMA table_info(orders)')
            cols = [r[1] for r in cur.fetchall()]
            if 'removal_cancelled' not in cols:
                cur.execute('ALTER TABLE orders ADD COLUMN removal_cancelled INTEGER DEFAULT 0')
                conn.commit()
        except Exception:
            pass
        
        # Set removal_cancelled to 1
        cur.execute('UPDATE orders SET removal_cancelled = 1 WHERE id = ?', (order_id,))
        conn.commit()
        
        if cur.rowcount == 0:
            conn.close()
            return jsonify({'success': False, 'error': 'Order not found'})
        
        conn.close()
        return jsonify({'success': True, 'message': 'Automatic removal cancelled'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/sold/allow-removal/<int:order_id>', methods=['POST'])
def allow_automatic_removal(order_id):
    """Re-enable automatic inventory removal for a specific sold order"""
    try:
        conn = sqlite3.connect('sold.db')
        cur = conn.cursor()
        
        # Set removal_cancelled back to 0
        cur.execute('UPDATE orders SET removal_cancelled = 0 WHERE id = ?', (order_id,))
        conn.commit()
        
        if cur.rowcount == 0:
            conn.close()
            return jsonify({'success': False, 'error': 'Order not found'})
        
        conn.close()
        return jsonify({'success': True, 'message': 'Automatic removal re-enabled'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

# Grace period settings endpoints
@app.route('/api/grace_period', methods=['GET'])
def api_get_grace_period():
    try:
        conn = sqlite3.connect('sold.db')
        cur = conn.cursor()
        cur.execute("CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT)")
        conn.commit()
        cur.execute("SELECT value FROM settings WHERE key = 'removal_grace_hours'")
        row = cur.fetchone()
        conn.close()
        hours = int(row[0]) if row and str(row[0]).strip().isdigit() else 48
        return jsonify({'success': True, 'hours': hours})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/grace_period', methods=['POST'])
def api_set_grace_period():
    try:
        data = request.get_json(force=True) if request.is_json else {}
        hours = int(data.get('hours', 48))
        if hours < 0 or hours > 240:
            return jsonify({'success': False, 'error': 'hours out of range (0-240)'}), 400
        conn = sqlite3.connect('sold.db')
        cur = conn.cursor()
        cur.execute("CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT)")
        conn.commit()
        cur.execute("INSERT INTO settings (key, value) VALUES ('removal_grace_hours', ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value", (str(hours),))
        conn.commit()
        conn.close()
        return jsonify({'success': True, 'hours': hours})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

# Removed items: list and page
@app.route('/api/removed', methods=['GET'])
def api_removed_list():
    try:
        conn = sqlite3.connect('removed.db')
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute('''
            CREATE TABLE IF NOT EXISTS removed (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT,
                barcode TEXT,
                qty INTEGER,
                time_removed TEXT,
                undone_at TEXT
            )
        ''')
        conn.commit()
        # Ensure undone_at exists for older databases
        try:
            cur.execute('PRAGMA table_info(removed)')
            cols = [r[1] for r in cur.fetchall()]
            if 'undone_at' not in cols:
                cur.execute('ALTER TABLE removed ADD COLUMN undone_at TEXT')
                conn.commit()
        except Exception:
            pass

        # Optional search filter
        q = (request.args.get('q') or '').strip()
        params = []
        where = ''
        if q:
            where = 'WHERE (LOWER(COALESCE(name, "")) LIKE ? OR LOWER(COALESCE(barcode, "")) LIKE ?)' 
            params.extend([f'%{q.lower()}%', f'%{q.lower()}%'])

        cur.execute(f"SELECT id, name, barcode, qty, time_removed, undone_at FROM removed {where} ORDER BY time_removed DESC LIMIT 1000", params)
        rows = [dict(r) for r in cur.fetchall()]
        conn.close()
        return jsonify({'success': True, 'items': rows})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/removed')
def removed_page():
    try:
        return render_template('removed.html')
    except Exception as e:
        return f'Error loading page: {e}', 500

@app.route('/api/removed/undo/<int:rem_id>', methods=['POST'])
def api_removed_undo(rem_id: int):
    """Undo a removal: increment searchRack quantity by qty for the barcode, and mark removed row undone."""
    try:
        # Open removed.db and fetch entry
        rem_conn = sqlite3.connect('removed.db')
        rem_conn.row_factory = sqlite3.Row
        rem_cur = rem_conn.cursor()
        # Ensure schema
        rem_cur.execute('''
            CREATE TABLE IF NOT EXISTS removed (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT,
                barcode TEXT,
                qty INTEGER,
                time_removed TEXT,
                undone_at TEXT
            )
        ''')
        rem_conn.commit()
        rem_cur.execute('SELECT id, name, barcode, qty, time_removed, undone_at FROM removed WHERE id = ?', (rem_id,))
        row = rem_cur.fetchone()
        if not row:
            rem_conn.close()
            return jsonify({'success': False, 'error': 'Removed entry not found'}), 404
        if row['undone_at']:
            rem_conn.close()
            return jsonify({'success': False, 'error': 'Already undone'}), 400

        barcode = (row['barcode'] or '').strip()
        qty = int(row['qty'] or 0)
        if not barcode or qty <= 0:
            rem_conn.close()
            return jsonify({'success': False, 'error': 'Invalid removed entry data'}), 400

        # Update searchRack quantity by ITEMID (barcode)
        rack_conn = sqlite3.connect('searchRack.db')
        rack_cur = rack_conn.cursor()
        rack_cur.execute('PRAGMA table_info(SEARCHRACK)')
        cols = [r[1] for r in rack_cur.fetchall()]
        qty_col = 'QUANTITY' if 'QUANTITY' in cols else ('QTY' if 'QTY' in cols else None)
        if not qty_col:
            rack_conn.close()
            rem_conn.close()
            return jsonify({'success': False, 'error': 'No quantity column in SEARCHRACK'}), 500

        rack_cur.execute(f'SELECT ID, {qty_col} FROM SEARCHRACK WHERE ITEMID = ? COLLATE NOCASE', (barcode,))
        found = rack_cur.fetchone()
        if not found:
            rack_conn.close()
            rem_conn.close()
            return jsonify({'success': False, 'error': 'No matching inventory found to restore'}), 404

        rack_id = found[0]
        current_qty = int(found[1] or 0)
        new_qty = current_qty + qty
        rack_cur.execute(f'UPDATE SEARCHRACK SET {qty_col} = ? WHERE ID = ?', (new_qty, rack_id))
        rack_conn.commit()
        rack_conn.close()

        # Mark removed row undone
        import datetime as _dt
        rem_cur.execute('UPDATE removed SET undone_at = ? WHERE id = ?', (_dt.datetime.utcnow().isoformat() + 'Z', rem_id))
        rem_conn.commit()
        rem_conn.close()

        return jsonify({'success': True, 'new_qty': new_qty})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/sold/remove-now/<int:order_id>', methods=['POST'])
def api_sold_remove_now(order_id: int):
    """Immediately reduce inventory for a specific sold order and log to removed.db, bypassing grace period."""
    try:
        # Fetch order details
        s_conn = sqlite3.connect('sold.db')
        s_conn.row_factory = sqlite3.Row
        s_cur = s_conn.cursor()
        s_cur.execute('SELECT id, order_id, title, barcode, quantity FROM orders WHERE id = ?', (order_id,))
        order = s_cur.fetchone()
        if not order:
            s_conn.close()
            return jsonify({'success': False, 'error': 'Order not found'}), 404
        barcode = (order['barcode'] or '').strip()
        sold_qty = int(order['quantity'] or 1)
        if not barcode:
            s_conn.close()
            return jsonify({'success': False, 'error': 'Order missing barcode'}), 400

        # Update searchRack quantity
        r_conn = sqlite3.connect('searchRack.db')
        r_cur = r_conn.cursor()
        r_cur.execute('PRAGMA table_info(SEARCHRACK)')
        cols = [r[1] for r in r_cur.fetchall()]
        qty_col = 'QUANTITY' if 'QUANTITY' in cols else ('QTY' if 'QTY' in cols else None)
        if not qty_col:
            r_conn.close(); s_conn.close()
            return jsonify({'success': False, 'error': 'No quantity column in SEARCHRACK'}), 500
        r_cur.execute(f'SELECT ID, {qty_col} FROM SEARCHRACK WHERE ITEMID = ? COLLATE NOCASE', (barcode,))
        row = r_cur.fetchone()
        inventory_found = False
        new_qty = None
        if row:
            inventory_found = True
            rack_id = row[0]
            current_qty = int(row[1] or 0)
            new_qty = max(0, current_qty - sold_qty)
            r_cur.execute(f'UPDATE SEARCHRACK SET {qty_col} = ? WHERE ID = ?', (new_qty, rack_id))
            r_conn.commit()

        # Log to removed.db
        rem_conn = sqlite3.connect('removed.db')
        rem_cur = rem_conn.cursor()
        rem_cur.execute('''
            CREATE TABLE IF NOT EXISTS removed (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT,
                barcode TEXT,
                qty INTEGER,
                time_removed TEXT,
                undone_at TEXT
            )
        ''')
        rem_conn.commit()
        import datetime as _dt
        rem_cur.execute(
            'INSERT INTO removed (name, barcode, qty, time_removed) VALUES (?,?,?,?)',
            (order['title'] or '', barcode, sold_qty, _dt.datetime.utcnow().isoformat() + 'Z')
        )
        rem_conn.commit()
        rem_conn.close()

        # Mark order as processed
        s_cur.execute('UPDATE orders SET rackupdated = 1 WHERE id = ?', (order_id,))
        s_conn.commit()
        s_conn.close()
        r_conn.close()

        return jsonify({'success': True, 'new_qty': new_qty, 'inventory_found': inventory_found})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})


@app.route('/undo_match', methods=['POST'])
def undo_match():
    data = request.json
    bol_id = data.get('bol_id')
    ebay_id = data.get('ebay_id')
    if bol_id is None or ebay_id is None:
        return jsonify({'success': False, 'error': 'Missing IDs'})
    try:
        # Look up the upc for this bol_id
        bol_conn = sqlite3.connect('bol.db')
        bol_cur = bol_conn.cursor()
        bol_cur.execute('SELECT upc FROM bol_items WHERE rowid=?', (bol_id,))
        bol_row = bol_cur.fetchone()
        bol_conn.close()
        if not bol_row or not bol_row[0]:
            return jsonify({'success': False, 'error': 'No UPC found for BOL item'})
        upc = bol_row[0]
        # Remove from found.db using upc and ebay_id
        found_conn = sqlite3.connect('found.db')
        found_cur = found_conn.cursor()
        found_cur.execute('DELETE FROM matches WHERE ebay_id=? AND upc=?', (ebay_id, upc))
        found_conn.commit()
        found_conn.close()
        # Unmark bol item
        bol_conn = sqlite3.connect('bol.db')
        bol_cur = bol_conn.cursor()
        bol_cur.execute("UPDATE bol_items SET isFound='' WHERE rowid=?", (bol_id,))
        bol_conn.commit()
        bol_conn.close()
        # Unmark ebay item
        ebay_conn = sqlite3.connect('ebayStore.db')
        ebay_cur = ebay_conn.cursor()
        ebay_cur.execute("UPDATE INVENTORY SET isFound='' WHERE rowid=?", (ebay_id,))
        ebay_conn.commit()
        ebay_conn.close()
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})


@app.route('/get_bol_upcs', methods=['GET'])
def get_bol_upcs():
    """Return a JSON list of all UPCs from bol.db bol_items table (UPC column)."""
    try:
        conn = sqlite3.connect('bol.db')
        cur = conn.cursor()
        cur.execute('SELECT upc FROM bol_items WHERE upc IS NOT NULL AND upc != ""')
        upcs = [row[0] for row in cur.fetchall()]
        conn.close()
        return jsonify({'upcs': upcs})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/upload_position_picture', methods=['POST'])
def upload_position_picture():
    import os
    from werkzeug.utils import secure_filename
    # Ensure static/pictureposition folder exists
    save_dir = os.path.join(os.getcwd(), 'static', 'pictureposition')
    os.makedirs(save_dir, exist_ok=True)
    
    file = request.files.get('picture')
    if not file:
        return jsonify({'success': False, 'error': 'No file uploaded'})
    
    # Generate a unique filename with timestamp
    import time
    timestamp = int(time.time())
    filename = secure_filename(file.filename)
    name, ext = os.path.splitext(filename)
    unique_name = f"{timestamp}{ext}"
    
    # Save the file
    save_path = os.path.join(save_dir, unique_name)
    file.save(save_path)
    
    # Return just the filename (not full path) since it's in the static folder
    return jsonify({'success': True, 'path': unique_name})


@app.route("/pictureposition")
def pictureposition_page():
    return render_template("pictureposition.html")

@app.route('/set_pictureposition_path', methods=['POST'])
def set_pictureposition_path():
    global pictureposition_path
    data = request.get_json()
    pictureposition_path = data.get('path')
    print(f"Set pictureposition_path from barcode.html: {pictureposition_path}")
    return jsonify({'success': True})

@app.route('/searchrack')
def searchrack_page():
    return render_template('searchrack.html')

@app.route('/searchrack_api')
def searchrack_api():
    q = request.args.get('q', '').strip()
    # Strip leading zeros from barcode searches
    q_stripped = q.lstrip('0') if q.isdigit() else q
    results = []
    if q_stripped:
        conn = sqlite3.connect('searchRack.db')
        cur = conn.cursor()
        cur.execute('''SELECT TITLE, BARCODE, ITEM_POSITION, IMAGES, PICTUREPOSITION, QUANTITY, IMAGE, ITEMID FROM SEARCHRACK WHERE TITLE LIKE ? OR BARCODE LIKE ?''', (f'%{q_stripped}%', f'%{q_stripped}%'))
        for row in cur.fetchall():
            results.append({
                'title': row[0],
                'barcode': row[1],
                'item_position': row[2],
                'images': row[3],
                'pictureposition': row[4],
                'quantity': row[5],
                'image': row[6],
                'itemid': row[7],
            })
        conn.close()
        # Merge results that share same barcode + item_position by summing quantities
        merged = {}
        for r in results:
            bc = (r.get('barcode') or '').strip()
            pos = (r.get('item_position') or '').strip()
            # only merge when barcode present
            if not bc:
                # use a unique key to preserve as-is
                key = f"__{id(r)}"
            else:
                key = f"{bc.lower()}||{pos.lower()}"
            if key not in merged:
                # initialize quantity from database value
                merged[key] = r.copy()
                # Properly handle quantity: use value from DB if present, otherwise default to 1
                qty_val = r.get('quantity')
                if qty_val is not None:
                    try:
                        merged[key]['quantity'] = int(qty_val)
                    except (ValueError, TypeError):
                        merged[key]['quantity'] = 1
                else:
                    merged[key]['quantity'] = 1
            else:
                # sum quantities from duplicate rows
                qty_val = r.get('quantity')
                if qty_val is not None:
                    try:
                        add_q = int(qty_val)
                    except (ValueError, TypeError):
                        add_q = 1
                else:
                    add_q = 1
                merged[key]['quantity'] = merged[key].get('quantity', 0) + add_q
        # convert merged back to list
        results = list(merged.values())
    return jsonify({'results': results})

@app.route('/searchbol_api')
def searchbol_api():
    q = request.args.get('q', '').strip()
    # Strip leading zeros from barcode searches
    q_stripped = q.lstrip('0') if q.isdigit() else q
    results = []
    if q_stripped:
        conn = sqlite3.connect('rawbol.db')
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute('''
            SELECT item_description as description, upc, client_cost, 
                   total_client_cost as total_cost, lot_number, bol_number, quantity
            FROM raw_bol_items 
            WHERE item_description LIKE ? OR upc LIKE ? OR lot_number LIKE ? OR bol_number LIKE ?
        ''', (f'%{q_stripped}%', f'%{q_stripped}%', f'%{q_stripped}%', f'%{q_stripped}%'))
        results = [dict(row) for row in cur.fetchall()]
        conn.close()
    return jsonify({'results': results})

@app.route('/view_all/<db_type>')
def view_all(db_type):
    if db_type == 'rack':
        conn = sqlite3.connect('searchRack.db')
        cur = conn.cursor()
        cur.execute('SELECT * FROM SEARCHRACK')
        columns = [desc[0] for desc in cur.description]
        items = [dict(zip(columns, row)) for row in cur.fetchall()]
        title = 'All Inventory Items'
        conn.close()
    elif db_type == 'bol':
        conn = sqlite3.connect('rawbol.db')
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute('SELECT * FROM raw_bol_items')
        items = [dict(row) for row in cur.fetchall()]
        title = 'All BOL Items'
        conn.close()
    else:
        return 'Invalid database type', 400
    
    return render_template('view_all.html', items=items, title=title, db_type=db_type)

@app.route('/update_item/<db_type>/<int:item_id>', methods=['POST'])
def update_item(db_type, item_id):
    if db_type not in ['rack', 'bol']:
        return jsonify({'success': False, 'error': 'Invalid database type'}), 400
    
    data = request.get_json()
    if not data:
        return jsonify({'success': False, 'error': 'No data provided'}), 400
    
    try:
        if db_type == 'rack':
            conn = sqlite3.connect('searchRack.db')
        else:  # bol
            conn = sqlite3.connect('rawbol.db')
        
        cur = conn.cursor()
        
        # Get column names to validate fields
        cur.execute(f'PRAGMA table_info({"SEARCHRACK" if db_type == "rack" else "raw_bol_items"})')
        columns = [col[1] for col in cur.fetchall()]
        
        # Build update query
        set_clause = ', '.join([f'"{k}"=?' for k in data.keys() if k in columns])
        values = [v for k, v in data.items() if k in columns]
        values.append(item_id)
        
        if not set_clause:
            return jsonify({'success': False, 'error': 'No valid fields to update'}), 400
        
        query = f'UPDATE {"SEARCHRACK" if db_type == "rack" else "raw_bol_items"} SET {set_clause} WHERE id=?'
        cur.execute(query, values)
        conn.commit()
        
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500
    finally:
        conn.close()

@app.route('/api/search/<db_key>', methods=['POST'])
def api_search_db(db_key):
    data = request.get_json() or {}
    q = (data.get('q') or '').strip()
    # Strip leading zeros from barcode searches
    q_stripped = q.lstrip('0') if q and q.isdigit() else q
    # Accept limit from client. If limit is 0 or None, we will NOT apply a SQL LIMIT (i.e., return all rows).
    limit_raw = data.get('limit')
    try:
        limit = int(limit_raw) if limit_raw is not None else 50
    except Exception:
        limit = 50
    try:
        mapping = {
            'ebayStore': 'ebayStore.db',
            'amazonStore': 'amazonStore.db',
            'sold': 'sold.db',
            'searchRack': 'searchRack.db',
            'found': 'found.db',
            'bol': 'rawbol.db'  # changed from bol.db to rawbol.db
        }
        if db_key not in mapping:
            return jsonify({'error': 'Unknown db_key'}), 400
        db_path = mapping[db_key]
        # Ensure created_at exists for searchRack so the UI can show timestamps
        if db_key == 'searchRack':
            try:
                conn_m = sqlite3.connect(db_path)
                cur_m = conn_m.cursor()
                cur_m.execute("PRAGMA table_info(SEARCHRACK)")
                cols_m = [r[1] for r in cur_m.fetchall()]
                if 'CREATED_AT' not in cols_m:
                    cur_m.execute('ALTER TABLE SEARCHRACK ADD COLUMN CREATED_AT TEXT')
                # Set CREATED_AT for any missing rows to current UTC so timestamps appear
                import datetime as _dt
                now_iso = _dt.datetime.utcnow().isoformat()
                cur_m.execute("UPDATE SEARCHRACK SET CREATED_AT = ? WHERE CREATED_AT IS NULL OR TRIM(COALESCE(CREATED_AT,'')) = ''", (now_iso,))
                conn_m.commit()
                conn_m.close()
            except Exception:
                # Don't block search if migration fails
                pass
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")
        tables = [r[0] for r in cur.fetchall()]
        if not tables:
            conn.close()
            return jsonify({'results': []})
        prefer = None
        for t in ['orders','INVENTORY','SEARCHRACK','searchrack','rack','items','bol_items']:
            if t in tables:
                prefer = t
                break
        table = prefer or tables[0]
        cur.execute(f"PRAGMA table_info('{table}')")
        cols = [r[1] for r in cur.fetchall()]
        where_clause = ''
        params = []
        # Accept optional location filter (from client UI) to search Item_Position specifically
        location = (data.get('location') or '').strip()
        if q_stripped:
            likes = []
            for c in cols:
                likes.append(f"LOWER(COALESCE({c},'')) LIKE ?")
                params.append(f"%{q_stripped.lower()}%")
            where_clause = ' WHERE ' + ' OR '.join(likes)
        # If a specific location was provided, add an AND clause to filter by item position columns
        if location:
            # try common column names for location
            loc_cols = [c for c in cols if c.lower() in ('item_position','itemposition','position','item_position')]
            if not loc_cols:
                # fallback to any column that looks like position
                loc_cols = [c for c in cols if 'position' in c.lower()]
            if loc_cols:
                loc_likes = []
                for lc in loc_cols:
                    loc_likes.append(f"LOWER(COALESCE({lc},'')) LIKE ?")
                    params.append(f"%{location.lower()}%")
                if where_clause:
                    where_clause += ' AND (' + ' OR '.join(loc_likes) + ')'
                else:
                    where_clause = ' WHERE ' + ' OR '.join(loc_likes)
        # compute total matching count for pagination
        count_sql = f"SELECT COUNT(*) FROM {table} {where_clause}"
        cur.execute(count_sql, params)
        total_count = cur.fetchone()[0]

        # If limit <= 0, return all rows; otherwise apply LIMIT/OFFSET
        offset = int(data.get('offset') or 0)
        if limit and int(limit) > 0:
            sql = f"SELECT * FROM {table} {where_clause} LIMIT ? OFFSET ?"
            exec_params = params + [limit, offset]
            cur.execute(sql, exec_params)
        else:
            sql = f"SELECT * FROM {table} {where_clause}"
            cur.execute(sql, params)
        rows = [dict(r) for r in cur.fetchall()]
        results = []
        for r in rows:
            item = dict(r)
            # default mappings
            if db_key == 'ebayStore':
                barcode_val = item.get('UPC') or item.get('upc') or item.get('BARCODE') or item.get('barcode') or item.get('Barcode') or ''
            elif db_key == 'amazonStore':
                barcode_val = item.get('UPC') or item.get('upc') or item.get('BARCODE') or item.get('barcode') or item.get('Barcode') or ''
            elif db_key == 'sold':
                # Be robust to all case variants and explicit barcode field
                barcode_val = item.get('barcode') or item.get('BARCODE') or item.get('Barcode') or item.get('upc') or item.get('UPC') or ''
            else:
                barcode_val = item.get('BARCODE') or item.get('barcode') or item.get('Barcode') or ''

            # bol.db normalization: different column names (item_description, upc, image_url)
            title_val = item.get('Title') or item.get('title') or item.get('name') or item.get('Name') or ''
            if db_key == 'sold':
                image_val = item.get('image') or item.get('Image') or item.get('IMAGE') or item.get('image_url') or item.get('images') or ''
            elif db_key == 'amazonStore':
                # amazonStore uses all-caps IMAGE column
                image_val = item.get('IMAGE') or item.get('Image') or item.get('image') or item.get('image_url') or item.get('images') or ''
            else:
                image_val = item.get('Image') or item.get('image') or item.get('IMAGE') or item.get('image_url') or item.get('images') or ''
            if db_key == 'bol':
                # barcode from upc field
                b = item.get('upc') or item.get('UPC') or item.get('Upc') or barcode_val
                # normalize numeric-like UPCs (e.g., '16094950.0') to '16094950'
                if isinstance(b, float) or (isinstance(b, str) and b.endswith('.0') and b.replace('.0','').isdigit()):
                    try:
                        b = str(int(float(b)))
                    except Exception:
                        b = str(b)
                elif b is None:
                    b = ''
                else:
                    b = str(b)
                barcode_val = b

                # title from item_description or description
                t = item.get('item_description') or item.get('description') or title_val
                if not t:
                    # fallback: pick the longest non-URL text field from the row
                    longest = ''
                    for v in item.values():
                        try:
                            s = str(v or '')
                        except Exception:
                            continue
                        if s and not s.lower().startswith('http') and len(s) > len(longest):
                            longest = s
                    t = longest
                title_val = t or ''

                # image from image_url if present
                image_val = item.get('image_url') or item.get('image') or image_val or ''

            item_out = {
                'source_db': db_key,
                'source_table': table,
                'id': item.get('id') or item.get('ID') or item.get('rowid'),
                'title': title_val,
                'image': image_val,
                'barcode': barcode_val,
                'item_id': item.get('ItemID') or item.get('item_id') or item.get('ItemId') or item.get('ASIN') or item.get('asin') or (barcode_val if barcode_val else ''),
                'pictureposition': item.get('PICTUREPOSITION') or item.get('pictureposition') or item.get('picture_position') or '',
                'item_position': item.get('ITEM_POSITION') or item.get('item_position') or item.get('position') or '',
                'quantity': item.get('QUANTITY') or item.get('Quantity') or item.get('quantity') or item.get('qty') or '',
                # created_at available on SEARCHRACK rows populated by DBmanager
                'created_at': item.get('CREATED_AT') or item.get('created_at') or '',
                'raw': item
            }
            # If this row comes from searchRack (the inventory snapshot), try to enrich it
            # by looking up the barcode (UPC) in ebayStore.db first, then bol.db as fallback.
            try:
                if db_key == 'searchRack' and item_out.get('barcode'):
                    lookup_barcode = item_out.get('barcode')
                    # Strip suffix from barcode for enrichment lookup (e.g., 123456-1 -> 123456)
                    base_barcode = str(lookup_barcode).split('-')[0] if lookup_barcode else lookup_barcode
                    # lookup in ebayStore.db
                    try:
                        es_conn = sqlite3.connect('ebayStore.db')
                        es_conn.row_factory = sqlite3.Row
                        es_cur = es_conn.cursor()
                        es_cur.execute("SELECT Title, Image, ItemID, Quantity, UPC FROM INVENTORY WHERE UPC = ? COLLATE NOCASE LIMIT 1", (base_barcode,))
                        row_es = es_cur.fetchone()
                        if row_es:
                            # prefer values from ebayStore if present, BUT keep searchRack quantity (physical inventory)
                            item_out['title'] = item_out.get('title') or row_es['Title']
                            item_out['image'] = item_out.get('image') or row_es['Image']
                            item_out['item_id'] = item_out.get('item_id') or row_es['ItemID']
                            # DO NOT override quantity from searchRack - it represents physical inventory
                            # item_out['quantity'] = item_out.get('quantity') or row_es['Quantity']
                        es_conn.close()
                    except Exception:
                        # ignore lookup errors
                        pass
                    # if still missing, try amazonStore.db
                    if not item_out.get('title') or not item_out.get('image'):
                        try:
                            am_conn = sqlite3.connect('amazonStore.db')
                            am_conn.row_factory = sqlite3.Row
                            am_cur = am_conn.cursor()
                            am_cur.execute("SELECT Title, Image, ASIN, Quantity, UPC FROM INVENTORY WHERE UPC = ? COLLATE NOCASE LIMIT 1", (base_barcode,))
                            row_am = am_cur.fetchone()
                            if row_am:
                                item_out['title'] = item_out.get('title') or row_am['Title']
                                item_out['image'] = item_out.get('image') or row_am['Image']
                                item_out['item_id'] = item_out.get('item_id') or row_am['ASIN']
                            am_conn.close()
                        except Exception:
                            pass
                    # if still missing title/image, try bol.db
                    if not item_out.get('title') or not item_out.get('image'):
                        try:
                            bol_conn = sqlite3.connect('bol.db')
                            bol_conn.row_factory = sqlite3.Row
                            bol_cur = bol_conn.cursor()
                            bol_cur.execute('SELECT item_description, image_url, upc FROM bol_items WHERE upc = ? COLLATE NOCASE LIMIT 1', (base_barcode,))
                            row_bol = bol_cur.fetchone()
                            if row_bol:
                                item_out['title'] = item_out.get('title') or row_bol['item_description']
                                item_out['image'] = item_out.get('image') or row_bol['image_url']
                                # If item_id missing, use upc as fallback
                                item_out['item_id'] = item_out.get('item_id') or row_bol['upc']
                            bol_conn.close()
                        except Exception:
                            pass
                # If this row comes from sold.db, the data should already be enriched during sync
                # but we can still do a fallback enrichment if needed
                elif db_key == 'sold':
                    # If barcode is missing, try to get it from ebayStore.db
                    if not item_out.get('barcode'):
                        lookup_item_id = item_out.get('item_id')
                        if lookup_item_id:
                            try:
                                es_conn = sqlite3.connect('ebayStore.db')
                                es_conn.row_factory = sqlite3.Row
                                es_cur = es_conn.cursor()
                                es_cur.execute("SELECT UPC FROM INVENTORY WHERE ItemID = ? LIMIT 1", (lookup_item_id,))
                                row_es = es_cur.fetchone()
                                if row_es and row_es['UPC']:
                                    item_out['barcode'] = row_es['UPC']
                                es_conn.close()
                            except Exception as e:
                                print(f"Debug: sold barcode lookup error: {e}")
                                pass
                    
                    # If title or image is missing, try to get from rawbol.db using barcode
                    if item_out.get('barcode') and (not item_out.get('title') or not item_out.get('image')):
                        try:
                            bol_conn = sqlite3.connect('rawbol.db')
                            bol_conn.row_factory = sqlite3.Row
                            bol_cur = bol_conn.cursor()
                            bol_cur.execute('SELECT item_description, image_url FROM raw_bol_items WHERE upc = ? COLLATE NOCASE LIMIT 1', (item_out['barcode'],))
                            row_bol = bol_cur.fetchone()
                            if row_bol:
                                if not item_out.get('title') and row_bol['item_description']:
                                    item_out['title'] = row_bol['item_description']
                                if not item_out.get('image') and row_bol['image_url']:
                                    item_out['image'] = row_bol['image_url']
                            bol_conn.close()
                        except Exception as e:
                            print(f"Debug: rawbol enrichment error: {e}")
                            pass
            except Exception:
                pass
            results.append(item_out)
        conn.close()
        # If searching the searchRack snapshot, merge rows with same barcode+item_position and sum quantities
        if db_key == 'searchRack' and results:
            merged = {}
            for r in results:
                bc = (r.get('barcode') or '').strip()
                # Use item_position, or if empty, fallback to pictureposition
                pos = (r.get('item_position') or r.get('pictureposition') or '').strip()
                if not bc:
                    key = f"__{id(r)}_{len(merged)}"
                else:
                    key = f"{bc.lower()}||{pos.lower()}"
                if key not in merged:
                    merged[key] = r.copy()
                    # normalize quantity
                    merged[key]['quantity'] = int(r.get('quantity')) if str(r.get('quantity') or '').isdigit() else 1
                else:
                    add_q = int(r.get('quantity')) if str(r.get('quantity') or '').isdigit() else 1
                    merged[key]['quantity'] = merged[key].get('quantity', 0) + add_q
            results = list(merged.values())
            # adjust total_count to reflect merged items count
            total_count = len(results)

        # If client requested debug info, return table/schema/samples plus normalized results
        if data.get('debug'):
            debug_samples = rows[:10] if isinstance(rows, list) else []
            return jsonify({
                'debug': True,
                'db_key': db_key,
                'db_path': db_path,
                'table': table,
                'columns': cols,
                'samples_raw': debug_samples,
                'results': results,
                'total': total_count
            })

        return jsonify({'results': results, 'total': total_count})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/lookup_location', methods=['POST'])
def api_lookup_location():
    data = request.get_json() or {}
    barcode = data.get('barcode')
    item_id = data.get('item_id')
    try:
        conn = sqlite3.connect('searchRack.db')
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        row = None
        if barcode:
            cur.execute("SELECT * FROM SEARCHRACK WHERE BARCODE = ? COLLATE NOCASE LIMIT 1", (barcode,))
            row = cur.fetchone()
        if not row and item_id:
            cur.execute("SELECT * FROM SEARCHRACK WHERE BARCODE = ? COLLATE NOCASE LIMIT 1", (item_id,))
            row = cur.fetchone()
        if not row:
            conn.close()
            return jsonify({'found': False})
        r = dict(row)
        conn.close()
        return jsonify({'found': True, 'item_position': r.get('ITEM_POSITION') or r.get('item_position'), 'pictureposition': r.get('PICTUREPOSITION') or r.get('pictureposition') or r.get('image')})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/set_rack_location', methods=['POST'])
def api_set_rack_location():
    """Set ITEM_POSITION for a SEARCHRACK row. Expects JSON: { id: <id>, item_position: <pos> }"""
    data = request.get_json() or {}
    item_id = data.get('id')
    pos = (data.get('item_position') or '').strip()
    pictureposition = (data.get('pictureposition') or '').strip()
    # Optional move quantity: how many items to move from this row's Quantity
    try:
        move_qty = int(data.get('move_qty')) if data.get('move_qty') is not None else None
    except Exception:
        move_qty = None
    # Require id and at least one of pos or pictureposition
    if not item_id or (not pos and not pictureposition):
        return jsonify({'success': False, 'error': 'Missing id or item_position/pictureposition'}), 400
    try:
        conn = sqlite3.connect('searchRack.db')
        cur = conn.cursor()
        # Try updating by id column; fall back to rowid if id column not present
        cur.execute("PRAGMA table_info('SEARCHRACK')")
        cols = [r[1] for r in cur.fetchall()]
        pk = None
        if 'id' in cols:
            pk = 'id'
        elif 'ID' in cols:
            pk = 'ID'
        else:
            pk = None
        # Load current row to inspect Quantity and other columns (we may need to split)
        # Determine how to address the row (by pk or rowid)
        id_col = pk
        id_val = item_id
        if not id_col:
            id_col = 'rowid'
            id_val = item_id

        cur.execute(f"SELECT * FROM SEARCHRACK WHERE {id_col} = ?", (id_val,))
        existing = cur.fetchone()
        # If we couldn't find a matching row, return error
        if not existing:
            conn.close()
            return jsonify({'success': False, 'error': 'Row not found'}), 404

        # Extract current quantity (try common column names)
        existing_dict = {d[0]: existing[idx] for idx, d in enumerate(cur.description)} if cur.description else dict(zip([c[0] for c in cur.description], existing))
        qty_cols = ['Quantity','quantity','Qty','QTY']
        current_qty = None
        for qc in qty_cols:
            if qc in existing_dict and existing_dict.get(qc) is not None:
                try:
                    current_qty = int(existing_dict.get(qc))
                    break
                except Exception:
                    current_qty = None
        # default to 1 when quantity is not known
        if current_qty is None:
            current_qty = 1

        # If a pictureposition is provided, update PICTUREPOSITION and clear ITEM_POSITION
        if pictureposition:
            if 'PICTUREPOSITION' in cols or 'pictureposition' in [c.lower() for c in cols]:
                pic_col = 'PICTUREPOSITION' if 'PICTUREPOSITION' in cols else next((c for c in cols if c.lower() == 'pictureposition'), 'PICTUREPOSITION')
            else:
                pic_col = 'PICTUREPOSITION'
            # enforce single-location rule: set pictureposition and clear item_position
            # Handle splitting when move_qty provided and less than current_qty
            target_move = move_qty or current_qty
            if target_move < 1 or target_move > current_qty:
                conn.close()
                return jsonify({'success': False, 'error': 'move_qty out of range'}), 400

            if target_move < current_qty:
                # decrement existing row's Quantity and insert a new row for moved qty
                # find quantity column name to update (if any)
                qty_col_name = None
                for qc in qty_cols:
                    if qc in cols:
                        qty_col_name = qc
                        break
                if qty_col_name:
                    # update original row quantity
                    if pk:
                        cur.execute(f"UPDATE SEARCHRACK SET {qty_col_name} = {qty_col_name} - ? WHERE {pk} = ?", (target_move, item_id))
                    else:
                        cur.execute(f"UPDATE SEARCHRACK SET {qty_col_name} = {qty_col_name} - ? WHERE rowid = ?", (target_move, item_id))
                else:
                    # no quantity column; treat as items of qty 1, so we will just insert new row and delete original
                    pass

                # prepare new row columns/values copied from existing, but with moved qty and new pictureposition/item_position
                col_names = [c for c in cols]
                placeholders = ','.join('?' for _ in col_names)
                # build values copying existing row values, replacing quantity and picture/item position
                cur_vals = []
                for c in col_names:
                    val = existing[col_names.index(c)] if c in col_names else None
                    # replace quantity if applicable
                    if c == qty_col_name:
                        val = target_move
                    if c.lower() == 'pictureposition':
                        val = pictureposition
                    if c.lower() == 'item_position' or c == 'ITEM_POSITION' or c.lower() == 'itemposition':
                        # clear item position when pictureposition is set
                        val = ''
                    cur_vals.append(val)
                # Insert new row (without specifying rowid)
                cur.execute(f"INSERT INTO SEARCHRACK ({', '.join(col_names)}) VALUES ({placeholders})", tuple(cur_vals))
            else:
                # moving all items: update in-place
                if pk:
                    cur.execute(f"UPDATE SEARCHRACK SET {pic_col} = ?, ITEM_POSITION = ? WHERE {pk} = ?", (pictureposition, '', item_id))
                else:
                    cur.execute(f"UPDATE SEARCHRACK SET {pic_col} = ?, ITEM_POSITION = ? WHERE rowid = ?", (pictureposition, '', item_id))
        # Otherwise update only ITEM_POSITION and clear PICTUREPOSITION
        elif pos:
            # find picture column name if exists
            pic_col = None
            if 'PICTUREPOSITION' in cols:
                pic_col = 'PICTUREPOSITION'
            else:
                for c in cols:
                    if c.lower() == 'pictureposition':
                        pic_col = c
                        break
            # Handle splitting similar to pictureposition case
            target_move = move_qty or current_qty
            if target_move < 1 or target_move > current_qty:
                conn.close()
                return jsonify({'success': False, 'error': 'move_qty out of range'}), 400

            if target_move < current_qty:
                # decrement existing row's Quantity
                qty_col_name = None
                for qc in qty_cols:
                    if qc in cols:
                        qty_col_name = qc
                        break
                if qty_col_name:
                    if pk:
                        cur.execute(f"UPDATE SEARCHRACK SET {qty_col_name} = {qty_col_name} - ? WHERE {pk} = ?", (target_move, item_id))
                    else:
                        cur.execute(f"UPDATE SEARCHRACK SET {qty_col_name} = {qty_col_name} - ? WHERE rowid = ?", (target_move, item_id))

                # insert new row for moved qty
                col_names = [c for c in cols]
                placeholders = ','.join('?' for _ in col_names)
                cur_vals = []
                for c in col_names:
                    val = existing[col_names.index(c)] if c in col_names else None
                    if c == qty_col_name:
                        val = target_move
                    if c.lower() == 'pictureposition':
                        val = ''
                    if c.lower() == 'item_position' or c == 'ITEM_POSITION' or c.lower() == 'itemposition':
                        val = pos
                    cur_vals.append(val)
                cur.execute(f"INSERT INTO SEARCHRACK ({', '.join(col_names)}) VALUES ({placeholders})", tuple(cur_vals))
            else:
                # update in-place
                if pk:
                    if pic_col:
                        cur.execute(f"UPDATE SEARCHRACK SET ITEM_POSITION = ?, {pic_col} = ? WHERE {pk} = ?", (pos, '', item_id))
                    else:
                        cur.execute(f"UPDATE SEARCHRACK SET ITEM_POSITION = ? WHERE {pk} = ?", (pos, item_id))
                else:
                    if pic_col:
                        cur.execute(f"UPDATE SEARCHRACK SET ITEM_POSITION = ?, {pic_col} = ? WHERE rowid = ?", (pos, '', item_id))
                    else:
                        cur.execute("UPDATE SEARCHRACK SET ITEM_POSITION = ? WHERE rowid = ?", (pos, item_id))
        conn.commit()
        updated = cur.rowcount
        conn.close()
        return jsonify({'success': True, 'updated': updated})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


def _get_table_and_pk(db_path, table_hint=None):
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")
    tables = [r[0] for r in cur.fetchall()]
    if not tables:
        conn.close()
        return None, None
    # Prefer common application tables if present (avoid picking auxiliary tables like ENRICH_META)
    prefer_order = ['orders', 'INVENTORY', 'SEARCHRACK', 'searchrack', 'rack', 'items', 'bol_items']
    prefer = None
    for p in prefer_order:
        if p in tables:
            prefer = p
            break
    table = table_hint if table_hint in tables else (prefer or (tables[0] if tables else None))
    cur.execute(f"PRAGMA table_info('{table}')")
    cols = cur.fetchall()
    pk = None
    colnames = [c[1] for c in cols]
    for c in cols:
        if c[5] == 1:
            pk = c[1]
            break
    if not pk:
        if 'id' in colnames:
            pk = 'id'
        elif 'ID' in colnames:
            pk = 'ID'
        else:
            pk = colnames[0]
    conn.close()
    return table, pk


@app.route('/api/update/<db_key>/<int:item_id>', methods=['POST'])
def api_update_row(db_key, item_id):
    data = request.get_json() or {}
    mapping = {
        'ebayStore': 'ebayStore.db',
        'sold': 'sold.db',
        'searchRack': 'searchRack.db',
        'found': 'found.db',
        'bol': 'bol.db'
    }
    if db_key not in mapping:
        return jsonify({'error': 'Unknown db_key'}), 400
    db_path = mapping[db_key]
    try:
        table, pk = _get_table_and_pk(db_path)
        if not table:
            return jsonify({'error': 'No table found in DB'}), 400
        conn = sqlite3.connect(db_path)
        cur = conn.cursor()
        cur.execute(f"PRAGMA table_info('{table}')")
        cols = [r[1] for r in cur.fetchall()]
        set_parts = []
        params = []
        for k, v in data.items():
            if k in cols:
                set_parts.append(f"{k} = ?")
                params.append(v)
        if not set_parts:
            return jsonify({'error': 'No updatable fields provided'}), 400
        params.append(item_id)
        sql = f"UPDATE {table} SET {', '.join(set_parts)} WHERE {pk} = ?"
        cur.execute(sql, params)
        conn.commit()
        conn.close()
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/create_shelf', methods=['POST'])
def api_create_shelf():
    """Create a new shelf entry. Expects JSON: { shelf_name, location, notes }"""
    data = request.get_json() or {}
    shelf_name = (data.get('shelf_name') or '').strip()
    location = (data.get('location') or '').strip()
    notes = (data.get('notes') or '').strip()
    
    if not shelf_name:
        return jsonify({'success': False, 'error': 'Shelf name is required'}), 400
    
    try:
        # Store shelves in a simple table in searchRack.db
        conn = sqlite3.connect('searchRack.db')
        cur = conn.cursor()
        
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
        
        # Insert the new shelf
        cur.execute('''
            INSERT INTO shelves (shelf_name, location, notes)
            VALUES (?, ?, ?)
        ''', (shelf_name, location, notes))
        
        conn.commit()
        shelf_id = cur.lastrowid
        conn.close()
        
        return jsonify({
            'success': True,
            'message': f'Shelf "{shelf_name}" created successfully',
            'shelf_id': shelf_id
        })
    
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/update_shelf', methods=['POST'])
def api_update_shelf():
    """Update an existing shelf image and/or rename the shelf code.
    Expects form-data: old_code (required), new_code (optional), image (optional file)
    """
    try:
        old_code = (request.form.get('old_code') or '').strip()
        new_code = (request.form.get('new_code') or '').strip()

        if not old_code:
            return jsonify({'success': False, 'error': 'old_code is required'}), 400

        shelves_dir = os.path.join('static', 'shelves')
        old_path = os.path.join(shelves_dir, f"{old_code}.png")

        # If image provided, overwrite or write to new path
        file = request.files.get('image')
        if file:
            target_code = new_code if new_code else old_code
            target_path = os.path.join(shelves_dir, f"{target_code}.png")
            file.save(target_path)

        # If renaming requested and file exists, rename on disk
        if new_code and new_code != old_code:
            new_path = os.path.join(shelves_dir, f"{new_code}.png")
            # If image was uploaded we already saved to new_path; otherwise rename existing file
            if not os.path.exists(new_path) and os.path.exists(old_path):
                os.rename(old_path, new_path)

            # Cascade rename into searchRack.db (SEARCHRACK.ITEM_POSITION)
            try:
                # Update searchRack.db
                sconn = sqlite3.connect('searchRack.db')
                scur = sconn.cursor()
                scur.execute("UPDATE SEARCHRACK SET ITEM_POSITION = ? WHERE LOWER(TRIM(ITEM_POSITION)) = LOWER(TRIM(?))", (new_code, old_code))
                s_updated = scur.rowcount
                sconn.commit()
                sconn.close()
            except Exception:
                s_updated = None

            # Log rename cascade results
            try:
                with open('clear_shelf.log', 'a', encoding='utf-8') as lf:
                    lf.write(f"SHELF_RENAME: {time.strftime('%Y-%m-%d %H:%M:%S')} {old_code} -> {new_code} searchRack_updated={s_updated}\n")
            except Exception:
                pass

        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/clear_shelf_inventory/<code>', methods=['POST'])
def api_clear_shelf_inventory(code):
    """Clear ITEM_POSITION (or similar) in rack.db for any rows matching the shelf code.
    Returns count of rows updated.
    """
    try:
        # Log incoming request for debugging
        try:
            with open('clear_shelf.log', 'a', encoding='utf-8') as lf:
                lf.write(f"CLEAR_REQUEST: {time.strftime('%Y-%m-%d %H:%M:%S')} code={repr(code)}\n")
        except Exception:
            pass

        code = (code or '').strip()
        if not code:
            return jsonify({'success': False, 'error': 'code required'}), 400

        db_path = 'rack.db'
        conn = sqlite3.connect(db_path)
        cur = conn.cursor()

        # Determine which table holds inventory rows (try common names)
        cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = [r[0] for r in cur.fetchall()]
        table_name = None
        for candidate in ('items', 'INVENTORY', 'inventory'):
            if candidate in tables:
                table_name = candidate
                break
        # fallback: pick a table that looks like inventory
        if not table_name:
            for t in tables:
                if 'item' in t.lower() or 'invent' in t.lower():
                    table_name = t
                    break

        if not table_name:
            conn.close()
            return jsonify({'success': False, 'error': 'No inventory-like table found in rack.db'}), 400

        # Find likely location columns in the chosen table
        cur.execute(f"PRAGMA table_info({table_name})")
        cols = [r[1] for r in cur.fetchall()]
        loc_cols = [c for c in cols if c.lower() in ('item_position','itemposition','position','location')]
        if not loc_cols:
            # fallback: try common column name substrings
            loc_cols = [c for c in cols if 'position' in c.lower() or 'location' in c.lower()]

        updated = 0
        if loc_cols:
            # Use case-insensitive, trimmed comparison to increase match robustness
            for col in loc_cols:
                sql = f"UPDATE {table_name} SET {col} = '' WHERE LOWER(TRIM({col})) = LOWER(TRIM(?))"
                cur.execute(sql, (code,))
                updated += cur.rowcount
            try:
                with open('clear_shelf.log', 'a', encoding='utf-8') as lf:
                    lf.write(f"CLEAR_SQL: table={table_name} loc_cols={loc_cols} code={repr(code)} updated={updated}\n")
            except Exception:
                pass
            try:
                with open('clear_shelf.log', 'a', encoding='utf-8') as lf:
                    lf.write(f"CLEAR_RESULT: updated={updated}, loc_cols={loc_cols}\n")
            except Exception:
                pass
        else:
            # Nothing to update
            conn.close()
            return jsonify({'success': False, 'error': 'No location column found in items table'}), 400

        conn.commit()
        conn.close()

        # Also clear searchRack.db ITEM_POSITION if present
        search_updated = 0
        try:
            sconn = sqlite3.connect('searchRack.db')
            scur = sconn.cursor()
            # Check for SEARCHRACK table and ITEM_POSITION column
            scur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='SEARCHRACK'")
            if scur.fetchone():
                scur.execute("PRAGMA table_info(SEARCHRACK)")
                scols = [r[1] for r in scur.fetchall()]
                if 'ITEM_POSITION' in scols:
                    scur.execute("UPDATE SEARCHRACK SET ITEM_POSITION = '' WHERE LOWER(TRIM(ITEM_POSITION)) = LOWER(TRIM(?))", (code,))
                    search_updated = scur.rowcount
                    sconn.commit()
            sconn.close()
        except Exception as e:
            try:
                with open('clear_shelf.log', 'a', encoding='utf-8') as lf:
                    lf.write(f"CLEAR_SEARCH_ERROR: {time.strftime('%Y-%m-%d %H:%M:%S')} error={e}\n")
            except Exception:
                pass

        # Log final counts
        try:
            with open('clear_shelf.log', 'a', encoding='utf-8') as lf:
                lf.write(f"CLEAR_FINAL: code={repr(code)} inventory_updated={updated} searchrack_updated={search_updated}\n")
        except Exception:
            pass

        return jsonify({'success': True, 'updated': updated, 'search_updated': search_updated})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/list_shelves', methods=['GET'])
def api_list_shelves():
    """Return list of shelf images from static/shelves as JSON, with optional sorting and counts."""
    try:
        sort = (request.args.get('sort') or 'name').lower()
        shelves_dir = os.path.join(app.root_path, 'static', 'shelves')
        results = []
        codes = []
        if os.path.isdir(shelves_dir):
            files = [f for f in os.listdir(shelves_dir) if f.lower().endswith('.png')]
            codes = [os.path.splitext(f)[0] for f in files]

            # created_at map from searchRack.db.shelves (optional)
            created_map = {}
            # counts map from searchRack.db.SEARCHRACK (ITEM_POSITION)
            counts_map = {}
            sdb_path = os.path.join(app.root_path, 'searchRack.db')
            # Query created_at; ignore if table missing
            try:
                conn = sqlite3.connect(sdb_path)
                cur = conn.cursor()
                if codes:
                    placeholders = ','.join('?' for _ in codes)
                    cur.execute(f"SELECT shelf_name, created_at FROM shelves WHERE shelf_name IN ({placeholders})", tuple(codes))
                    for r in cur.fetchall():
                        created_map[r[0]] = r[1]
                conn.close()
            except Exception:
                created_map = {}
            # Query counts; handle missing table separately
            try:
                conn = sqlite3.connect(sdb_path)
                cur = conn.cursor()
                cur.execute("SELECT LOWER(TRIM(ITEM_POSITION)) as pos, COUNT(*) FROM SEARCHRACK GROUP BY LOWER(TRIM(ITEM_POSITION))")
                for pos, cnt in cur.fetchall():
                    counts_map[pos] = cnt
                conn.close()
            except Exception:
                counts_map = {}

            for fname in files:
                code = os.path.splitext(fname)[0]
                path = os.path.join(shelves_dir, fname)
                try:
                    mtime = os.path.getmtime(path)
                except Exception:
                    mtime = 0
                url = url_for('static', filename=f'shelves/{fname}')
                count = counts_map.get(code.lower().strip(), 0)
                results.append({'code': code, 'filename': fname, 'url': url, 'lastModified': int(mtime), 'created_at': created_map.get(code), 'count': int(count)})

            # Apply server-side sorting
            if sort == 'items':
                results.sort(key=lambda x: x.get('count', 0), reverse=True)
            elif sort == 'created':
                # Sort by created_at desc; fallback to lastModified desc
                def created_key(x):
                    ca = x.get('created_at')
                    # Expect 'YYYY-MM-DD HH:MM:SS' from SQLite; parse safely
                    try:
                        # Replace space with 'T' to help Date.parse on clients; here we can prioritize lastModified for server order
                        return (x.get('lastModified') or 0) if not ca else x.get('lastModified') or 0
                    except Exception:
                        return x.get('lastModified') or 0
                # Use lastModified as reliable proxy for Newest first
                results.sort(key=lambda x: x.get('lastModified', 0), reverse=True)
            else:
                # name
                results.sort(key=lambda x: (x.get('code') or '').lower())
        return jsonify({'success': True, 'shelves': results})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/shelf_counts', methods=['POST'])
def api_shelf_counts():
    """Return a map of shelf_code -> counts of items referencing that shelf from both SEARCHRACK and INVENTORY."""
    try:
        codes = request.get_json() or {}
        codes = codes.get('codes', []) if isinstance(codes, dict) else codes
        if not isinstance(codes, list):
            return jsonify({'success': False, 'error': 'codes must be a list'}), 400

        result = {}
        try:
            sconn = sqlite3.connect('searchRack.db')
            scur = sconn.cursor()
            for code in codes:
                scur.execute("SELECT COUNT(*) FROM SEARCHRACK WHERE LOWER(TRIM(ITEM_POSITION)) = LOWER(TRIM(?))", (code,))
                r = scur.fetchone()
                result[code] = {'searchrack': r[0] if r else 0}
            sconn.close()
        except Exception:
            for code in codes:
                result[code] = {'searchrack': 0}

        return jsonify({'success': True, 'counts': result})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/delete/<db_key>/<int:item_id>', methods=['POST'])
def api_delete_row(db_key, item_id):
    mapping = {
        'ebayStore': 'ebayStore.db',
        'sold': 'sold.db',
        'searchRack': 'searchRack.db',
        'found': 'found.db',
        'bol': 'bol.db'
    }
    if db_key not in mapping:
        return jsonify({'error': 'Unknown db_key'}), 400
    src_db = mapping[db_key]
    try:
        table, pk = _get_table_and_pk(src_db)
        if not table:
            return jsonify({'error': 'No table in source DB'}), 400
        conn = sqlite3.connect(src_db)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute(f"SELECT * FROM {table} WHERE {pk} = ?", (item_id,))
        row = cur.fetchone()
        if not row:
            conn.close()
            return jsonify({'error': 'Row not found'}), 404
        rowdict = dict(row)
        conn.close()
        dconn = sqlite3.connect('deleted.db')
        dcur = dconn.cursor()
        dcur.execute('''CREATE TABLE IF NOT EXISTS deleted_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_db TEXT,
            source_table TEXT,
            source_pk TEXT,
            source_id TEXT,
            deleted_at TEXT,
            data_json TEXT
        )''')
        import datetime, json
        # Store deletion time in ISO8601 UTC
        deleted_at = datetime.datetime.utcnow().isoformat()
        dcur.execute('INSERT INTO deleted_items (source_db, source_table, source_pk, source_id, deleted_at, data_json) VALUES (?,?,?,?,?,?)',
                     (db_key, table, pk, str(item_id), deleted_at, json.dumps(rowdict)))
        dconn.commit()
        archive_id = dcur.lastrowid
        dconn.close()
        conn2 = sqlite3.connect(src_db)
        cur2 = conn2.cursor()
        cur2.execute(f"DELETE FROM {table} WHERE {pk} = ?", (item_id,))
        conn2.commit()
        conn2.close()
        return jsonify({'success': True, 'archived_id': archive_id})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/undelete/<int:archive_id>', methods=['POST'])
def api_undelete(archive_id):
    try:
        dconn = sqlite3.connect('deleted.db')
        dconn.row_factory = sqlite3.Row
        dcur = dconn.cursor()
        dcur.execute('SELECT * FROM deleted_items WHERE id = ?', (archive_id,))
        row = dcur.fetchone()
        if not row:
            dconn.close()
            return jsonify({'error': 'Archive not found'}), 404
        rec = dict(row)
        import json
        data = json.loads(rec['data_json'])
        src_db_key = rec['source_db']
        mapping = {
            'ebayStore': 'ebayStore.db',
            'sold': 'sold.db',
            'searchRack': 'searchRack.db',
            'found': 'found.db',
            'bol': 'bol.db'
        }
        if src_db_key not in mapping:
            dconn.close()
            return jsonify({'error': 'Unknown source db'}), 400
        src_db = mapping[src_db_key]
        table = rec['source_table']
        conn = sqlite3.connect(src_db)
        cur = conn.cursor()
        cur.execute(f"PRAGMA table_info('{table}')")
        cols = [r[1] for r in cur.fetchall()]
        insert_cols = [c for c in cols if c in data and c != rec['source_pk']]
        vals = [data[c] for c in insert_cols]
        placeholders = ','.join(['?'] * len(vals))
        if insert_cols:
            sql = f"INSERT INTO {table} ({','.join(insert_cols)}) VALUES ({placeholders})"
            cur.execute(sql, vals)
            conn.commit()
            conn.close()
            dcur.execute('DELETE FROM deleted_items WHERE id = ?', (archive_id,))
            dconn.commit()
            dconn.close()
            return jsonify({'success': True})
        else:
            conn.close()
            dconn.close()
            return jsonify({'error': 'No insertable columns found'}), 400
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/archived', methods=['GET', 'POST'])
def api_archived_list():
    """Return list of archived (deleted) items from deleted.db as normalized results."""
    try:
        conn = sqlite3.connect('deleted.db')
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute('SELECT * FROM deleted_items ORDER BY deleted_at DESC')
        rows = [dict(r) for r in cur.fetchall()]
        results = []
        import json
        for r in rows:
            data = {}
            try:
                data = json.loads(r.get('data_json') or '{}')
            except Exception:
                data = {}
            # Normalize common fields across mixed sources; include UPPERCASE keys from SEARCHRACK
            title_val = (
                data.get('Title') or data.get('title') or data.get('TITLE') or
                data.get('item_description') or data.get('DESCRIPTION') or data.get('description') or ''
            )
            image_val = (
                data.get('Image') or data.get('image') or data.get('image_url') or data.get('IMAGE') or ''
            )
            barcode_val = (
                data.get('BARCODE') or data.get('barcode') or data.get('upc') or
                data.get('UPC') or data.get('ItemID') or ''
            )
            itemid_val = (
                data.get('ItemID') or data.get('item_id') or data.get('ItemId') or
                data.get('ITEMID') or data.get('id') or data.get('ID') or data.get('upc') or ''
            )
            picturepos_val = (
                data.get('PICTUREPOSITION') or data.get('pictureposition') or data.get('picture_position') or ''
            )
            itempos_val = (
                data.get('ITEM_POSITION') or data.get('item_position') or data.get('position') or ''
            )
            quantity_val = (
                data.get('Quantity') or data.get('quantity') or data.get('qty') or data.get('QUANTITY') or ''
            )

            # Parse IMAGES field to extract a first URL if needed
            imgs_field = data.get('IMAGES') or data.get('images')
            if (not image_val) and imgs_field is not None:
                try:
                    if isinstance(imgs_field, list):
                        for u in imgs_field:
                            s = str(u or '')
                            if s.lower().startswith('http'):
                                image_val = s
                                break
                    elif isinstance(imgs_field, str):
                        s = imgs_field.strip()
                        if s.startswith('[') and s.endswith(']'):
                            arr = json.loads(s)
                            if isinstance(arr, list):
                                for u in arr:
                                    us = str(u or '')
                                    if us.lower().startswith('http'):
                                        image_val = us
                                        break
                        if not image_val:
                            for sep in [',',';','|','\n','\t',' ']:
                                if sep in s:
                                    for part in s.split(sep):
                                        ps = part.strip()
                                        if ps.lower().startswith('http'):
                                            image_val = ps
                                            break
                                    if image_val:
                                        break
                            if not image_val and s.lower().startswith('http'):
                                image_val = s
                except Exception:
                    pass

            # Enrich archived rows using UPC against ebayStore/bol for title/image/quantity if missing
            def _to_int_like(q):
                try:
                    if q is None:
                        return None
                    s = str(q).strip()
                    if not s:
                        return None
                    if s.isdigit():
                        return int(s)
                    if s.endswith('.0') and s.replace('.0','').isdigit():
                        return int(float(s))
                    f = float(s)
                    if abs(f - int(f)) < 1e-9:
                        return int(f)
                    return None
                except Exception:
                    return None

            if barcode_val:
                try:
                    es_conn = sqlite3.connect('ebayStore.db')
                    es_conn.row_factory = sqlite3.Row
                    es_cur = es_conn.cursor()
                    es_cur.execute("SELECT Title, Image, ItemID, Quantity FROM INVENTORY WHERE UPC = ? COLLATE NOCASE LIMIT 1", (barcode_val,))
                    row_es = es_cur.fetchone()
                    if row_es:
                        if not title_val:
                            title_val = row_es['Title']
                        if not image_val:
                            image_val = row_es['Image']
                        if not itemid_val:
                            itemid_val = row_es['ItemID']
                        if not quantity_val:
                            quantity_val = row_es['Quantity']
                    es_conn.close()
                except Exception:
                    pass
                if (not title_val or not image_val):
                    try:
                        bol_conn = sqlite3.connect('bol.db')
                        bol_conn.row_factory = sqlite3.Row
                        bol_cur = bol_conn.cursor()
                        bol_cur.execute('SELECT item_description, image_url, upc FROM bol_items WHERE upc = ? COLLATE NOCASE LIMIT 1', (barcode_val,))
                        row_bol = bol_cur.fetchone()
                        if row_bol:
                            if not title_val:
                                title_val = row_bol['item_description']
                            if not image_val:
                                image_val = row_bol['image_url']
                            if not itemid_val:
                                itemid_val = row_bol['upc']
                        bol_conn.close()
                    except Exception:
                        pass

            qn = _to_int_like(quantity_val)
            if qn is not None:
                quantity_val = qn
            item_out = {
                'archived': True,
                'archived_id': r.get('id'),
                'deleted_at': r.get('deleted_at'),
                'source_db': 'archived',
                'orig_source_db': r.get('source_db'),
                'orig_table': r.get('source_table'),
                'orig_pk': r.get('source_pk'),
                'orig_id': r.get('source_id'),
                'id': r.get('id'),
                'title': title_val,
                'image': image_val,
                'barcode': barcode_val,
                'item_id': itemid_val,
                'pictureposition': picturepos_val,
                'item_position': itempos_val,
                'quantity': quantity_val,
                'raw': data
            }
            results.append(item_out)
        # Merge archived rows similar to searchRack: group by barcode+item_position and sum quantities
        if results:
            merged = {}
            for r in results:
                bc = (r.get('barcode') or '').strip().lower()
                pos = (r.get('item_position') or '').strip().lower()
                if not bc:
                    key = f"__{id(r)}_{len(merged)}"
                else:
                    key = f"{bc}||{pos}"
                # normalize qty: if not int-like, treat as 1
                try:
                    q = r.get('quantity')
                    if isinstance(q, str):
                        q = q.strip()
                    if q is None or (isinstance(q, str) and not q):
                        n = 1
                    elif isinstance(q, int):
                        n = q
                    elif isinstance(q, float) and abs(q - int(q)) < 1e-9:
                        n = int(q)
                    elif isinstance(q, str) and q.isdigit():
                        n = int(q)
                    elif isinstance(q, str) and q.endswith('.0') and q.replace('.0','').isdigit():
                        n = int(float(q))
                    else:
                        n = 1
                except Exception:
                    n = 1
                if key not in merged:
                    r_copy = r.copy()
                    r_copy['quantity'] = n
                    merged[key] = r_copy
                else:
                    merged[key]['quantity'] = merged[key].get('quantity', 0) + n
            results = list(merged.values())
        conn.close()
        return jsonify({'results': results})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/delete_archive/<int:archive_id>', methods=['POST'])
def api_delete_archive(archive_id):
    try:
        conn = sqlite3.connect('deleted.db')
        cur = conn.cursor()
        cur.execute('DELETE FROM deleted_items WHERE id = ?', (archive_id,))
        conn.commit()
        conn.close()
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/refresh_searchrack')
def refresh_searchrack():
    from DBmanager import enrich_searchrack_db
    createSearchRackDB()
    enrich_searchrack_db(batch_size=500, do_backup=False)  # Enrich with all sources including Amazon
    return 'SearchRack database updated and enriched! <a href="/searchrack">Back to Search</a>'

@app.route('/test_sold_item', methods=['POST'])
def test_sold_item():
    import datetime
    try:
        today = datetime.date.today().isoformat()
        conn = sqlite3.connect('sold.db')
        cur = conn.cursor()
        cur.execute("UPDATE orders SET isHandled = 0, paid_time = ?, shipped_time = ? WHERE id = 99", (today, today))
        conn.commit()
        conn.close()
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

# =========== Sync Management API ===========

@app.route('/api/sync/status', methods=['GET'])
def get_sync_status():
    """Get last sync timestamps and current settings"""
    try:
        conn = sqlite3.connect('sync_settings.db')
        cur = conn.cursor()
        
        # Create table if not exists
        cur.execute('''CREATE TABLE IF NOT EXISTS sync_status (
            key TEXT PRIMARY KEY,
            value TEXT
        )''')
        
        # Get all sync timestamps
        cur.execute('SELECT key, value FROM sync_status')
        rows = cur.fetchall()
        status = dict(rows)
        
        conn.close()
        
        return jsonify({
            'ebay_orders': status.get('ebay_orders_last'),
            'ebay_listings': status.get('ebay_listings_last'),
            'amazon_orders': status.get('amazon_orders_last'),
            'amazon_listings': status.get('amazon_listings_last'),
            'amazon_upcs': status.get('amazon_upcs_last'),
            'auto_sync_enabled': status.get('auto_sync_enabled') == 'true',
            'sync_interval': int(status.get('sync_interval', 60)),
            'orders_lookback': int(status.get('orders_lookback', 30)),
            'auto_upc_enabled': status.get('auto_upc_enabled', 'true') == 'true'
        })
    except Exception as e:
        print(f"❌ Error getting sync status: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/sync/settings', methods=['POST'])
def save_sync_settings():
    """Save sync settings"""
    try:
        data = request.json
        conn = sqlite3.connect('sync_settings.db')
        cur = conn.cursor()
        
        # Create table if not exists
        cur.execute('''CREATE TABLE IF NOT EXISTS sync_status (
            key TEXT PRIMARY KEY,
            value TEXT
        )''')
        
        # Save settings
        settings = {
            'auto_sync_enabled': 'true' if data.get('auto_sync_enabled') else 'false',
            'sync_interval': str(data.get('sync_interval', 60)),
            'orders_lookback': str(data.get('orders_lookback', 30)),
            'auto_upc_enabled': 'true' if data.get('auto_upc_enabled') else 'false'
        }
        
        for key, value in settings.items():
            cur.execute('INSERT OR REPLACE INTO sync_status (key, value) VALUES (?, ?)', (key, value))
        
        conn.commit()
        conn.close()
        
        return jsonify({'success': True})
    except Exception as e:
        print(f"❌ Error saving settings: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

def update_sync_timestamp(key):
    """Helper function to update last sync timestamp"""
    try:
        conn = sqlite3.connect('sync_settings.db')
        cur = conn.cursor()
        cur.execute('''CREATE TABLE IF NOT EXISTS sync_status (
            key TEXT PRIMARY KEY,
            value TEXT
        )''')
        
        from datetime import datetime
        timestamp = datetime.now().isoformat()
        cur.execute('INSERT OR REPLACE INTO sync_status (key, value) VALUES (?, ?)', (f'{key}_last', timestamp))
        
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"⚠️ Error updating sync timestamp: {e}")

@app.route('/api/sync/all', methods=['POST'])
def sync_all():
    """Sync everything - eBay and Amazon orders, listings, and UPCs"""
    try:
        results = {}
        
        # Sync eBay orders
        try:
            # Use the local eBay orders sync function defined in this file
            orders()
            update_sync_timestamp('ebay_orders')
            results['ebay_orders'] = 'success'
        except Exception as e:
            print(f"⚠️ eBay orders sync failed: {e}")
            results['ebay_orders'] = str(e)
        
        # Sync eBay listings (searchRack)
        try:
            from DBmanager import enrich_searchrack_db
            enrich_searchrack_db(batch_size=500, do_backup=False)  # Enrich with eBay, BOL, and Amazon
            update_sync_timestamp('ebay_listings')
            results['ebay_listings'] = 'success'
        except Exception as e:
            print(f"⚠️ eBay listings sync failed: {e}")
            results['ebay_listings'] = str(e)
        
        # Sync Amazon orders
        if AMAZON_AVAILABLE:
            try:
                amazon = AmazonManager()
                amazon.sync_orders_to_db(days_back=30)
                from DBmanager import process_sold_orders_inventory_reduction
                process_sold_orders_inventory_reduction()
                update_sync_timestamp('amazon_orders')
                results['amazon_orders'] = 'success'
            except Exception as e:
                print(f"⚠️ Amazon orders sync failed: {e}")
                results['amazon_orders'] = str(e)
            
            # Sync Amazon listings
            try:
                amazon = AmazonManager()
                amazon.sync_listings_to_db()
                update_sync_timestamp('amazon_listings')
                results['amazon_listings'] = 'success'
            except Exception as e:
                print(f"⚠️ Amazon listings sync failed: {e}")
                results['amazon_listings'] = str(e)
            
            # Sync Amazon UPCs (only new items)
            try:
                count = sync_missing_upcs()
                update_sync_timestamp('amazon_upcs')
                results['amazon_upcs'] = f'success ({count} UPCs fetched)'
            except Exception as e:
                print(f"⚠️ Amazon UPC sync failed: {e}")
                results['amazon_upcs'] = str(e)
        
        success_count = sum(1 for v in results.values() if 'success' in v)
        total_count = len(results)
        
        return jsonify({
            'success': True,
            'message': f'Sync completed: {success_count}/{total_count} successful',
            'details': results
        })
    except Exception as e:
        print(f"❌ Error in sync all: {e}")
        return jsonify({'success': False, 'message': str(e)}), 500

@app.route('/api/sync/ebay-orders', methods=['POST'])
def sync_ebay_orders_api():
    """Sync eBay orders"""
    try:
        # Use the local eBay orders sync function defined in this file
        orders()
        update_sync_timestamp('ebay_orders')
        return jsonify({'success': True, 'message': 'eBay orders synced successfully'})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500

@app.route('/api/sync/ebay-listings', methods=['POST'])
def sync_ebay_listings_api():
    """Sync eBay listings and enrich with Amazon data"""
    try:
        from DBmanager import enrich_searchrack_db
        enrich_searchrack_db(batch_size=500, do_backup=False)  # Enrich with eBay, BOL, and Amazon data
        update_sync_timestamp('ebay_listings')
        return jsonify({'success': True, 'message': 'eBay listings synced and enriched successfully'})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500

@app.route('/api/sync/amazon-orders', methods=['POST'])
def sync_amazon_orders_api():
    """Sync Amazon orders"""
    if not AMAZON_AVAILABLE:
        return jsonify({'success': False, 'message': 'Amazon integration not available'}), 500
    try:
        amazon = AmazonManager()
        amazon.sync_orders_to_db(days_back=30)
        from DBmanager import process_sold_orders_inventory_reduction
        process_sold_orders_inventory_reduction()
        update_sync_timestamp('amazon_orders')
        return jsonify({'success': True, 'message': 'Amazon orders synced successfully'})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500

@app.route('/api/sync/amazon-listings', methods=['POST'])
def sync_amazon_listings_api():
    """Sync Amazon listings"""
    if not AMAZON_AVAILABLE:
        return jsonify({'success': False, 'message': 'Amazon integration not available'}), 500
    try:
        amazon = AmazonManager()
        amazon.sync_listings_to_db()
        update_sync_timestamp('amazon_listings')
        return jsonify({'success': True, 'message': 'Amazon listings synced successfully'})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500

def sync_missing_upcs():
    """Sync UPCs for Amazon items that don't have them (where UPC = ASIN)"""
    import time
    
    try:
        conn = sqlite3.connect('amazonStore.db')
        cur = conn.cursor()
        
        # Find items without UPCs (where UPC equals ASIN, meaning no UPC was set)
        cur.execute('SELECT ASIN FROM ITEMS WHERE UPC = ASIN OR UPC IS NULL')
        items_without_upcs = [row[0] for row in cur.fetchall()]
    except Exception as e:
        print(f"❌ Database error in sync_missing_upcs: {e}")
        raise
    
    if not items_without_upcs:
        conn.close()
        print("✅ No items need UPC fetching")
        return 0
    
    print(f"🔍 Found {len(items_without_upcs)} items without UPCs. Fetching...")
    
    try:
        amazon = AmazonManager()
        print("✅ AmazonManager initialized")
    except Exception as e:
        print(f"❌ Failed to initialize AmazonManager: {e}")
        conn.close()
        raise
    
    upcs_found = 0
    
    for i, asin in enumerate(items_without_upcs, 1):
        try:
            # Get catalog item
            catalog_data = amazon.get_catalog_item(asin)
            
            if not catalog_data:
                continue
            
            upc = None
            
            # Check attributes section first (most common)
            if 'attributes' in catalog_data:
                attrs = catalog_data['attributes']
                if 'externally_assigned_product_identifier' in attrs:
                    for identifier in attrs['externally_assigned_product_identifier']:
                        if identifier.get('type') in ['upc', 'ean']:
                            upc = identifier.get('value')
                            break
            
            # Fallback to identifiers section
            if not upc and 'identifiers' in catalog_data:
                identifiers = catalog_data['identifiers']
                if isinstance(identifiers, list):
                    for id_group in identifiers:
                        if 'identifiers' in id_group:
                            for identifier in id_group['identifiers']:
                                if identifier.get('identifierType') in ['UPC', 'EAN']:
                                    upc = identifier.get('identifier')
                                    break
            
            if upc:
                cur.execute('UPDATE ITEMS SET UPC = ? WHERE ASIN = ?', (upc, asin))
                upcs_found += 1
                print(f"✅ [{i}/{len(items_without_upcs)}] {asin}: {upc}")
            else:
                print(f"⚠️ [{i}/{len(items_without_upcs)}] {asin}: No UPC found")
            
            # Rate limiting: 2 requests per second max
            time.sleep(0.5)
            
        except Exception as e:
            print(f"❌ [{i}/{len(items_without_upcs)}] {asin}: Error - {e}")
            time.sleep(0.5)
    
    conn.commit()
    conn.close()
    
    print(f"\n✅ UPC sync complete: {upcs_found} UPCs found for {len(items_without_upcs)} items")
    return upcs_found

@app.route('/api/sync/amazon-upcs', methods=['POST'])
def sync_amazon_upcs_api():
    """Sync UPCs for Amazon items without barcodes"""
    if not AMAZON_AVAILABLE:
        return jsonify({'success': False, 'message': 'Amazon integration not available'}), 500
    try:
        print("🔄 Starting Amazon UPC sync...")
        count = sync_missing_upcs()
        print(f"✅ Amazon UPC sync completed: {count} UPCs fetched")
        update_sync_timestamp('amazon_upcs')
        
        if count == 0:
            message = 'No items need UPC fetching - all items already have UPCs'
        else:
            message = f'Fetched {count} UPCs for items without barcodes'
        
        return jsonify({'success': True, 'message': message, 'count': count})
    except Exception as e:
        import traceback
        error_details = traceback.format_exc()
        print(f"❌ Amazon UPC sync error: {e}")
        print(error_details)
        return jsonify({'success': False, 'message': str(e), 'error': error_details}), 500

@app.route('/api/sync/debug', methods=['GET'])
def sync_debug():
    """Debug endpoint to check sync system status"""
    import sys
    return jsonify({
        'amazon_available': AMAZON_AVAILABLE,
        'bol_available': BOL_AVAILABLE if 'BOL_AVAILABLE' in globals() else False,
        'python_version': sys.version,
        'working_directory': os.getcwd()
    })

if __name__ == "__main__":
    # Start Flask in a thread
    flask_thread = threading.Thread(target=start_flask, daemon=True)
    flask_thread.start()

    # Wait a bit, then start tunnel
    time.sleep(1)
    start_tunnel()

    # Wait until both are likely up
    time.sleep(2)
    try:
        # orders()  # Skip eBay sync for testing print queue
        pass
    except Exception as e:
        print(f"Warning: orders() failed: {e}")
    # finalize_barcodes()  # Skip for testing
    # Start trash purger daily
    _start_trash_purger_thread()

    # Keep main thread alive
    while True:
        time.sleep(1)

