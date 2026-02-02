# Place below app = Flask(__name__)

from contextlib import nullcontext

from flask import Flask, request, send_file, url_for, render_template, jsonify, redirect, session, make_response
from flask_caching import Cache
from flask_compress import Compress
from werkzeug.exceptions import RequestEntityTooLarge
from PIL import Image, ImageDraw
import io, time, subprocess, os, requests, json, threading, sqlite3, sys, datetime
import pytz
import xml.etree.ElementTree as ET
from dotenv import load_dotenv
load_dotenv()
import xml.dom.minidom as minidom
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent

from inventory import find_item  # adjust this to match your actual import
from DBmanager import ebayStoreDB, amazonStoreDB, store_ebay_order, createSearchRackDB, addToSearchRack
from DBmanager import enrich_searchrack_db

# Disable print statements globally for performance boost
# TEMPORARILY DISABLED FOR DEBUGGING
import builtins
# builtins.print = lambda *args, **kwargs: None

# Database connection pooling for better performance on Raspberry Pi
from contextlib import contextmanager

# Request-scoped connection pool using Flask's g object
# Connections are reused within a single request, then closed automatically

def get_db_connection(db_name):
    """Get a request-scoped database connection (reused within same request, closed at end)"""
    from flask import g
    if not hasattr(g, '_db_connections'):
        g._db_connections = {}

    if db_name not in g._db_connections:
        if os.path.isabs(db_name):
            db_path = db_name
        else:
            db_path = str(BASE_DIR / db_name)

        conn = sqlite3.connect(db_path, timeout=30.0)
        conn.row_factory = sqlite3.Row
        g._db_connections[db_name] = conn

    return g._db_connections[db_name]

@contextmanager
def db_connection(db_name, row_factory=True):
    """Context manager for database operations with automatic commit/rollback"""
    conn = get_db_connection(db_name)
    if not row_factory:
        original_factory = conn.row_factory
        conn.row_factory = None
    
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        if not row_factory:
            conn.row_factory = original_factory

# Enable WAL mode for SQLite databases for better concurrent performance
def enable_wal_mode():
    """Enable Write-Ahead Logging for all SQLite databases"""
    databases = ['sold.db', 'bol.db', 'searchRack.db', 'ebayStore.db', 'amazonStore.db', 'rawbol.db', 'rackhistory.db', 'deleted.db']
    for db_name in databases:
        try:
            db_path = BASE_DIR / db_name
            if db_path.exists():
                conn = sqlite3.connect(str(db_path))
                try:
                    conn.execute('PRAGMA journal_mode=WAL')
                finally:
                    conn.close()
        except Exception:
            pass  # Skip if database doesn't exist or error occurs

def create_database_indexes():
    """Create indexes on frequently searched columns for all databases"""
    print("🔍 Creating database indexes for faster searches...")
    
    # Index definitions: {database: [(table, column), ...]}
    index_configs = {
        'rawbol.db': [
            ('raw_bol_items', 'upc'),
            ('raw_bol_items', 'item_description'),
            ('raw_bol_items', 'lot_number')
        ],
        'searchRack.db': [
            ('SEARCHRACK', 'UPC'),
            ('SEARCHRACK', 'item_position'),
            ('SEARCHRACK', 'CREATED_AT')
        ],
        'sold.db': [
            ('sold_items', 'UPC'),
            ('sold_items', 'barcode'),
            ('sold_items', 'OrderID'),
            ('returns', 'upc'),
            ('returns', 'order_id')
        ],
        'ebayStore.db': [
            ('INVENTORY', 'UPC'),
            ('INVENTORY', 'SKU'),
            ('orders', 'OrderID')
        ],
        'amazonStore.db': [
            ('INVENTORY', 'UPC'),
            ('INVENTORY', 'SKU'),
            ('INVENTORY', 'asin'),
            ('orders', 'AmazonOrderId')
        ]
    }
    
    for db_name, indexes in index_configs.items():
        try:
            db_path = BASE_DIR / db_name
            if not db_path.exists():
                continue
                
            conn = sqlite3.connect(str(db_path))
            cur = conn.cursor()
            
            for table, column in indexes:
                try:
                    # Check if table exists
                    cur.execute(f"SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,))
                    if not cur.fetchone():
                        continue
                    
                    # Check if column exists
                    cur.execute(f"PRAGMA table_info({table})")
                    cols = [row[1].lower() for row in cur.fetchall()]
                    if column.lower() not in cols:
                        continue
                    
                    # Create index if it doesn't exist
                    index_name = f"idx_{table}_{column}".replace('-', '_')
                    cur.execute(f"CREATE INDEX IF NOT EXISTS {index_name} ON {table}({column} COLLATE NOCASE)")
                    print(f"  ✅ Index created: {db_name}.{table}.{column}")
                except Exception as e:
                    print(f"  ⚠️ Could not create index on {db_name}.{table}.{column}: {e}")
            
            conn.commit()
        except Exception as e:
            print(f"  ⚠️ Error processing {db_name}: {e}")
        finally:
            conn.close()
    
    print("✅ Database indexes created")

# Initialize database optimizations on startup
enable_wal_mode()
create_database_indexes()

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

CLIENT_ID = os.getenv("EBAY_CLIENT_ID")
CLIENT_SECRET = os.getenv("EBAY_CLIENT_SECRET")
RUNAME = os.getenv("EBAY_RUNAME")
app = Flask(__name__)
app.secret_key = os.getenv('FLASK_SECRET_KEY', 'dev-fallback-change-in-production')
app.start_time = time.time()  # Track app startup time for uptime calculation
# Production settings - optimized for Raspberry Pi deployment
app.config['TEMPLATES_AUTO_RELOAD'] = False  # Disable template reloading for better performance
app.config['SEND_FILE_MAX_AGE_DEFAULT'] = 31536000  # Cache static files for 1 year (31536000 seconds)
try:
    app.jinja_env.auto_reload = False  # Disable Jinja auto-reload
except Exception:
    pass

# Initialize Flask-Caching for API response caching (huge performance boost)
cache = Cache(app, config={
    'CACHE_TYPE': 'simple',  # In-memory cache
    'CACHE_DEFAULT_TIMEOUT': 300,  # 5 minutes default
    'CACHE_THRESHOLD': 500  # Max 500 cached items
})

# Initialize Flask-Compress for automatic gzip compression (70% smaller responses)
Compress(app)

# Debug mode flag - set DEBUG_MODE=true in .env to enable debug endpoints
DEBUG_MODE = os.getenv('DEBUG_MODE', 'false').lower() == 'true'

import logging
logger = logging.getLogger(__name__)

def _safe_error(e, context=''):
    """Log the real error server-side, return a generic message for the client."""
    logger.error(f"{context}: {e}" if context else str(e), exc_info=True)
    return 'An internal error occurred'

def require_debug_mode(f):
    """Decorator to restrict endpoints to debug mode only."""
    from functools import wraps
    @wraps(f)
    def decorated(*args, **kwargs):
        if not DEBUG_MODE:
            return jsonify({'error': 'Not found'}), 404
        return f(*args, **kwargs)
    return decorated

def _ensure_removed_items_table(cur):
    """Create removed_items table if it doesn't exist. Call with an active cursor."""
    cur.execute('''
        CREATE TABLE IF NOT EXISTS removed_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            order_id TEXT,
            barcode TEXT,
            title TEXT,
            quantity_removed INTEGER,
            removed_at TEXT,
            searchrack_id INTEGER,
            old_quantity INTEGER,
            new_quantity INTEGER,
            removal_type TEXT,
            item_position TEXT
        )
    ''')

# Global Data Version for cache invalidation
# Use database to ensure consistency across workers
def update_data_version():
    try:
        conn = sqlite3.connect('bol.db')
        conn.execute('CREATE TABLE IF NOT EXISTS app_metadata (key TEXT PRIMARY KEY, value TEXT)')
        conn.execute('INSERT OR REPLACE INTO app_metadata (key, value) VALUES (?, ?)', ('data_version', str(int(time.time()))))
        conn.commit()
    except Exception as e:
        print(f"Error updating data version: {e}")
    finally:
        conn.close()

def get_data_version():
    try:
        conn = sqlite3.connect('bol.db')
        conn.execute('CREATE TABLE IF NOT EXISTS app_metadata (key TEXT PRIMARY KEY, value TEXT)')
        cur = conn.execute('SELECT value FROM app_metadata WHERE key = ?', ('data_version',))
        row = cur.fetchone()
        if not row:
            # Initialize if missing
            initial_version = str(int(time.time()))
            conn.execute('INSERT INTO app_metadata (key, value) VALUES (?, ?)', ('data_version', initial_version))
            conn.commit()
            return int(initial_version)
        return int(row[0])
    except Exception as e:
        print(f"Error getting data version: {e}")
        return int(time.time()) # Fallback to current time to force refresh
    finally:
        conn.close()

@app.teardown_appcontext
def close_db_connections(exception):
    """Close all request-scoped database connections at end of request."""
    from flask import g
    connections = getattr(g, '_db_connections', {})
    for conn in connections.values():
        try:
            conn.close()
        except Exception:
            pass
    connections.clear()

@app.route('/api/data_version')
def api_data_version():
    return jsonify({'version': get_data_version()})

@app.context_processor
def inject_server_info():
    """Inject server information into all templates"""
    hostname = request.headers.get('Host', '')
    is_testing = 'nexuscentralhq.org' in hostname and not hostname.startswith('pi.')
    return dict(is_testing_server=is_testing, server_hostname=hostname)

@app.after_request
def add_no_cache_headers(response):
    try:
        # Only apply no-cache to HTML and JSON responses, not static files
        content_type = response.content_type or ''
        if 'text/html' in content_type or 'application/json' in content_type:
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

try:
    access_token = get_access_token()
except Exception as e:
    print(f"⚠️ eBay token refresh failed at startup: {e}")
    print("   The app will start, but eBay API calls will fail until token is refreshed.")
    access_token = None

headers = {
    "Authorization": f"Bearer {access_token}" if access_token else "",
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

def send_email_smtp(to_emails, subject, body, html_body=None):
    """
    Send email using SMTP (Gmail, Outlook, etc.)
    
    Args:
        to_emails: List of recipient email addresses
        subject: Email subject line
        body: Plain text email body
        html_body: Optional HTML email body
    
    Returns:
        (success: bool, error_message: str or None)
    """
    try:
        # Get SMTP settings from environment or database
        smtp_server = os.getenv('SMTP_SERVER', 'smtp.gmail.com')
        smtp_port = int(os.getenv('SMTP_PORT', '587'))
        smtp_user = os.getenv('SMTP_USER', '')
        smtp_password = os.getenv('SMTP_PASSWORD', '')
        from_email = os.getenv('SMTP_FROM_EMAIL', smtp_user)
        
        if not smtp_user or not smtp_password:
            return False, 'SMTP credentials not configured. Set SMTP_USER and SMTP_PASSWORD in environment variables or .env file'
        
        # Create message
        msg = MIMEMultipart('alternative')
        msg['Subject'] = subject
        msg['From'] = from_email
        msg['To'] = ', '.join(to_emails)
        
        # Attach text and HTML parts
        text_part = MIMEText(body, 'plain')
        msg.attach(text_part)
        
        if html_body:
            html_part = MIMEText(html_body, 'html')
            msg.attach(html_part)
        
        # Send email
        with smtplib.SMTP(smtp_server, smtp_port) as server:
            server.starttls()
            server.login(smtp_user, smtp_password)
            server.send_message(msg)
        
        return True, None
        
    except Exception as e:
        return False, str(e)





@app.route('/tools')
def tools():
    return render_template('tools.html')

# Emailer page
@app.route('/emailer')
def emailer():
    return render_template('emailer.html')

# API endpoints for emailer
@app.route('/api/emailer/settings', methods=['GET'])
def get_emailer_settings():
    """Get saved emailer settings with per-email configuration"""
    try:
        conn = sqlite3.connect('searchRack.db')
        cur = conn.cursor()
        
        # Create table if it doesn't exist (don't drop it!)
        cur.execute('''
            CREATE TABLE IF NOT EXISTS emailer_settings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT,
                alert_type TEXT DEFAULT 'test',
                interval TEXT DEFAULT '1d',
                last_test_sent TEXT,
                UNIQUE(email, alert_type)
            )
        ''')
        
        # Get all email settings
        cur.execute('SELECT email, alert_type, interval FROM emailer_settings')
        rows = cur.fetchall()
        
        email_settings = []
        for row in rows:
            email_settings.append({
                'email': row[0],
                'alert_type': row[1] or 'test',
                'interval': row[2] or '1d'
            })
        
        
        return jsonify({
            'success': True,
            'email_settings': email_settings
        })
    except Exception as e:
        return jsonify({'success': False, 'error': _safe_error(e)}), 500
    finally:
        conn.close()

@app.route('/api/emailer/settings', methods=['POST'])
def save_emailer_settings():
    """Save emailer settings with per-email configuration"""
    try:
        data = request.json
        email_settings = data.get('email_settings', [])
        
        conn = sqlite3.connect('searchRack.db')
        cur = conn.cursor()
        
        # Create table if it doesn't exist
        cur.execute('''
            CREATE TABLE IF NOT EXISTS emailer_settings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT,
                alert_type TEXT DEFAULT 'test',
                interval TEXT DEFAULT '1d',
                last_test_sent TEXT,
                UNIQUE(email, alert_type)
            )
        ''')
        
        # Delete all existing settings
        cur.execute('DELETE FROM emailer_settings')
        
        # Insert new settings
        for setting in email_settings:
            email = setting.get('email')
            alert_type = setting.get('alert_type', 'test')
            interval = setting.get('interval', '1d')
            
            cur.execute('''
                INSERT INTO emailer_settings (email, alert_type, interval)
                VALUES (?, ?, ?)
            ''', (email, alert_type, interval))
        
        conn.commit()
        
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': _safe_error(e)}), 500
    finally:
        conn.close()

@app.route('/api/emailer/send-test', methods=['POST'])
def send_test_email():
    """Send a test email"""
    try:
        data = request.json
        emails = data.get('emails', [])
        
        if not emails:
            return jsonify({'success': False, 'error': 'No email addresses provided'}), 400
        
        # Send actual email
        subject = "This is an automated test email from store app"
        body = "Hello, this is the store app speaking."
        
        success, error = send_email_smtp(emails, subject, body)
        
        if not success:
            return jsonify({'success': False, 'error': error}), 500
        
        # Update last test sent time
        conn = sqlite3.connect('searchRack.db')
        cur = conn.cursor()
        cur.execute('''
            UPDATE emailer_settings 
            SET last_test_sent = ? 
            WHERE id = 1
        ''', (datetime.datetime.now().isoformat(),))
        conn.commit()
        
        print(f"✅ Test email sent successfully to: {', '.join(emails)}")
        
        return jsonify({
            'success': True,
            'message': f'Test email sent to {len(emails)} recipient(s)'
        })
    except Exception as e:
        return jsonify({'success': False, 'error': _safe_error(e)}), 500
    finally:
        conn.close()

@app.route('/api/emailer/send-test-inventory', methods=['POST'])
def send_test_inventory_email():
    """Send a test inventory alert email with sample data"""
    try:
        data = request.json
        emails = data.get('emails', [])
        
        if not emails:
            return jsonify({'success': False, 'error': 'No email addresses provided'}), 400
        
        # Generate fake inventory issues for testing
        test_issues = {
            'has_issues': True,
            'timestamp': datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'ebay_items': [
                {
                    'barcode': '0123456789012',
                    'title': 'Sample Product - Wireless Bluetooth Headphones with Noise Cancellation',
                    'store_qty': 3,
                    'item_id': '123456789012'
                },
                {
                    'barcode': '9876543210987',
                    'title': 'Test Item - Premium Leather Wallet with RFID Protection Technology',
                    'store_qty': 1,
                    'item_id': '987654321098'
                }
            ],
            'amazon_items': [
                {
                    'barcode': '5551234567890',
                    'title': 'Example Product - Stainless Steel Water Bottle 32oz Insulated',
                    'store_qty': 5,
                    'asin': 'B08ABCD1234'
                },
                {
                    'barcode': '4449876543210',
                    'title': 'Demo Item - USB-C Hub Multi-Port Adapter with HDMI and Ethernet',
                    'store_qty': 2,
                    'asin': 'B09WXYZ5678'
                }
            ]
        }
        
        # Generate HTML email
        html_body = generate_inventory_email_html(test_issues)
        plain_body = generate_inventory_email_plain(test_issues)
        
        # Send email
        subject = f"⚠️ [TEST] Inventory Alert - 4 Items Need Attention - {datetime.datetime.now().strftime('%Y-%m-%d %H:%M')}"
        success, error = send_email_smtp(emails, subject, plain_body, html_body)
        
        if not success:
            return jsonify({'success': False, 'error': error}), 500
        
        print(f"✅ Test inventory alert sent to: {', '.join(emails)}")
        
        return jsonify({
            'success': True,
            'message': f'Test inventory alert sent to {len(emails)} recipient(s)'
        })
    except Exception as e:
        return jsonify({'success': False, 'error': _safe_error(e)}), 500

@app.route('/api/emailer/send-health', methods=['POST'])
def send_health_email():
    """Send health stats email to configured recipients"""
    try:
        data = request.json
        emails = data.get('emails', [])
        
        if not emails:
            return jsonify({'success': False, 'error': 'No email addresses provided'}), 400
        
        # Collect health stats
        health_data = collect_health_stats()
        
        # Generate HTML email
        html_body = generate_health_email_html(health_data)
        plain_body = generate_health_email_plain(health_data)
        
        # Send email
        subject = f"📊 Store App Health Report - {datetime.datetime.now().strftime('%Y-%m-%d %H:%M')}"
        success, error = send_email_smtp(emails, subject, plain_body, html_body)
        
        if not success:
            return jsonify({'success': False, 'error': error}), 500
        
        # Clear power event counters after successful email send
        try:
            conn = sqlite3.connect('sync_settings.db')
            cur = conn.cursor()
            cur.execute('DELETE FROM power_events WHERE key IN (?, ?)', 
                       ('undervoltage_count', 'throttle_count'))
            conn.commit()
            print(f"✅ Health email sent to: {', '.join(emails)} - Power event counters reset")
        except Exception:
            print(f"✅ Health email sent to: {', '.join(emails)}")
        finally:
            conn.close()
        
        return jsonify({
            'success': True,
            'message': f'Health report sent to {len(emails)} recipient(s)'
        })
    except Exception as e:
        return jsonify({'success': False, 'error': _safe_error(e)}), 500

@app.route('/api/emailer/send-inventory', methods=['POST'])
def send_inventory_alert_email():
    """Send inventory alert email for items with store quantity but no searchRack quantity"""
    try:
        data = request.json
        emails = data.get('emails', [])
        
        print(f"📧 Inventory alert request received for emails: {emails}")
        
        if not emails:
            print("❌ No emails provided")
            return jsonify({'success': False, 'error': 'No email addresses provided'}), 400
        
        # Collect inventory mismatches
        print("🔍 Collecting inventory mismatches...")
        inventory_issues = collect_inventory_mismatches()
        
        print(f"📊 Issues found - eBay: {len(inventory_issues['ebay_items'])}, Amazon: {len(inventory_issues['amazon_items'])}, Duplicates: {len(inventory_issues['duplicate_locations'])}, Has issues: {inventory_issues['has_issues']}")
        
        # Only send if there are eBay or Amazon issues (exclude duplicate locations from email)
        has_email_issues = len(inventory_issues['ebay_items']) > 0 or len(inventory_issues['amazon_items']) > 0
        if not has_email_issues:
            print("ℹ️ No inventory issues found - email not sent")
            return jsonify({
                'success': True,
                'message': 'No inventory issues found - email not sent'
            })
        
        # Generate HTML email
        print("📝 Generating email content...")
        html_body = generate_inventory_email_html(inventory_issues)
        plain_body = generate_inventory_email_plain(inventory_issues)
        
        # Send email
        subject = f"⚠️ Inventory Alert - {len(inventory_issues['ebay_items']) + len(inventory_issues['amazon_items'])} Items Need Attention - {datetime.datetime.now().strftime('%Y-%m-%d %H:%M')}"
        print(f"📤 Sending email with subject: {subject}")
        success, error = send_email_smtp(emails, subject, plain_body, html_body)
        
        if not success:
            print(f"❌ Email send failed: {error}")
            return jsonify({'success': False, 'error': error}), 500
        
        print(f"✅ Inventory alert sent to: {', '.join(emails)}")
        
        return jsonify({
            'success': True,
            'message': f'Inventory alert sent to {len(emails)} recipient(s)'
        })
    except Exception as e:
        print(f"❌ Exception in send_inventory_alert_email: {str(e)}")
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': _safe_error(e)}), 500

def collect_health_stats():
    """Collect all health statistics for the app"""
    import platform
    import psutil
    
    stats = {}
    
    # Server uptime (Flask process uptime)
    try:
        if not hasattr(app, 'start_time'):
            app.start_time = time.time()
        uptime_seconds = time.time() - app.start_time
        days = int(uptime_seconds // 86400)
        hours = int((uptime_seconds % 86400) // 3600)
        minutes = int((uptime_seconds % 3600) // 60)
        stats['uptime'] = f"{days}d {hours}h {minutes}m"
    except Exception:
        stats['uptime'] = 'Unknown'
    
    # System info
    stats['python_version'] = platform.python_version()
    stats['timestamp'] = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    
    # Memory usage
    try:
        memory = psutil.virtual_memory()
        stats['memory_used_gb'] = round(memory.used / (1024**3), 2)
        stats['memory_total_gb'] = round(memory.total / (1024**3), 2)
        stats['memory_free_gb'] = round(memory.available / (1024**3), 2)
        stats['memory_percent'] = round(memory.percent, 1)
    except Exception:
        stats['memory_used_gb'] = 0
        stats['memory_total_gb'] = 0
        stats['memory_free_gb'] = 0
        stats['memory_percent'] = 0
    
    # CPU usage
    try:
        # Current CPU usage (1 second sample)
        stats['cpu_percent_current'] = round(psutil.cpu_percent(interval=1), 1)
        # Average CPU usage (since boot or process start)
        stats['cpu_percent_avg'] = round(psutil.cpu_percent(interval=0), 1)
    except Exception:
        stats['cpu_percent_current'] = 0
        stats['cpu_percent_avg'] = 0
    
    # CPU temperature (if available)
    try:
        temps = psutil.sensors_temperatures()
        if temps:
            # Try to get CPU temp from common sensor names
            cpu_temp = None
            for name in ['coretemp', 'cpu_thermal', 'cpu-thermal', 'k10temp']:
                if name in temps and temps[name]:
                    cpu_temp = temps[name][0].current
                    break
            
            if cpu_temp:
                stats['cpu_temp_current'] = round(cpu_temp, 1)
                # Calculate average from all cores if available
                all_temps = [sensor.current for sensors in temps.values() for sensor in sensors]
                stats['cpu_temp_avg'] = round(sum(all_temps) / len(all_temps), 1) if all_temps else cpu_temp
            else:
                stats['cpu_temp_current'] = None
                stats['cpu_temp_avg'] = None
        else:
            stats['cpu_temp_current'] = None
            stats['cpu_temp_avg'] = None
    except Exception:
        stats['cpu_temp_current'] = None
        stats['cpu_temp_avg'] = None
    
    # Power status (battery/AC)
    try:
        battery = psutil.sensors_battery()
        if battery:
            stats['power_plugged'] = battery.power_plugged
            stats['battery_percent'] = round(battery.percent, 1)
            stats['battery_time_left'] = None
            if not battery.power_plugged and battery.secsleft != psutil.POWER_TIME_UNLIMITED:
                # Convert seconds to hours:minutes
                hours = int(battery.secsleft // 3600)
                minutes = int((battery.secsleft % 3600) // 60)
                stats['battery_time_left'] = f"{hours}h {minutes}m"
        else:
            stats['power_plugged'] = None
            stats['battery_percent'] = None
            stats['battery_time_left'] = None
    except Exception:
        stats['power_plugged'] = None
        stats['battery_percent'] = None
        stats['battery_time_left'] = None
    
    # Raspberry Pi undervoltage detection with persistent tracking
    stats['undervoltage_detected'] = False
    stats['undervoltage_now'] = False
    stats['undervoltage_count'] = 0
    stats['throttle_count'] = 0
    stats['last_undervoltage_time'] = None
    
    try:
        # Check for Raspberry Pi throttling status (includes undervoltage)
        import subprocess
        result = subprocess.run(['vcgencmd', 'get_throttled'], capture_output=True, text=True, timeout=2)
        if result.returncode == 0:
            # Parse throttled status (hex value)
            throttled_hex = result.stdout.strip().split('=')[1]
            throttled = int(throttled_hex, 16)
            
            # Bit 0: Undervoltage currently detected
            # Bit 16: Undervoltage has occurred since boot
            stats['undervoltage_now'] = bool(throttled & 0x1)
            stats['undervoltage_detected'] = bool(throttled & 0x10000)
            
            # Store throttle status for detailed reporting
            stats['throttle_status'] = {
                'undervoltage_now': bool(throttled & 0x1),
                'arm_frequency_capped_now': bool(throttled & 0x2),
                'currently_throttled': bool(throttled & 0x4),
                'soft_temp_limit_active': bool(throttled & 0x8),
                'undervoltage_occurred': bool(throttled & 0x10000),
                'arm_frequency_capped_occurred': bool(throttled & 0x20000),
                'throttling_occurred': bool(throttled & 0x40000),
                'soft_temp_limit_occurred': bool(throttled & 0x80000)
            }
            
            # Track undervoltage events in database
            try:
                conn = sqlite3.connect('sync_settings.db')
                cur = conn.cursor()
                cur.execute('CREATE TABLE IF NOT EXISTS power_events (key TEXT PRIMARY KEY, value TEXT)')
                
                # Get last known state
                cur.execute('SELECT value FROM power_events WHERE key = ?', ('last_throttle_state',))
                row = cur.fetchone()
                last_state = int(row[0]) if row else 0
                
                # Check if undervoltage state changed from off to on (new event)
                if stats['undervoltage_now'] and not (last_state & 0x1):
                    # Increment undervoltage counter
                    cur.execute('SELECT value FROM power_events WHERE key = ?', ('undervoltage_count',))
                    row = cur.fetchone()
                    count = int(row[0]) if row else 0
                    count += 1
                    cur.execute('INSERT OR REPLACE INTO power_events (key, value) VALUES (?, ?)', 
                               ('undervoltage_count', str(count)))
                    cur.execute('INSERT OR REPLACE INTO power_events (key, value) VALUES (?, ?)', 
                               ('last_undervoltage_time', datetime.datetime.now().isoformat()))
                    stats['undervoltage_count'] = count
                    stats['last_undervoltage_time'] = datetime.datetime.now().isoformat()
                else:
                    # Get existing count
                    cur.execute('SELECT value FROM power_events WHERE key = ?', ('undervoltage_count',))
                    row = cur.fetchone()
                    stats['undervoltage_count'] = int(row[0]) if row else 0
                    
                    cur.execute('SELECT value FROM power_events WHERE key = ?', ('last_undervoltage_time',))
                    row = cur.fetchone()
                    stats['last_undervoltage_time'] = row[0] if row else None
                
                # Check if throttling state changed
                if stats['throttle_status']['currently_throttled'] and not (last_state & 0x4):
                    cur.execute('SELECT value FROM power_events WHERE key = ?', ('throttle_count',))
                    row = cur.fetchone()
                    count = int(row[0]) if row else 0
                    count += 1
                    cur.execute('INSERT OR REPLACE INTO power_events (key, value) VALUES (?, ?)', 
                               ('throttle_count', str(count)))
                    stats['throttle_count'] = count
                else:
                    cur.execute('SELECT value FROM power_events WHERE key = ?', ('throttle_count',))
                    row = cur.fetchone()
                    stats['throttle_count'] = int(row[0]) if row else 0
                
                # Update last known state
                cur.execute('INSERT OR REPLACE INTO power_events (key, value) VALUES (?, ?)', 
                           ('last_throttle_state', str(throttled)))
                
                conn.commit()
            except Exception as e:
                print(f"Error tracking power events: {e}")
                pass
            finally:
                conn.close()
    except Exception:
        # Not a Pi or vcgencmd not available
        stats['throttle_status'] = None
    
    # Disk space
    try:
        import shutil
        total, used, free = shutil.disk_usage('.')
        stats['disk_free_gb'] = round(free / (1024**3), 2)
        stats['disk_total_gb'] = round(total / (1024**3), 2)
        stats['disk_percent'] = round((used / total) * 100, 1)
    except Exception:
        stats['disk_free_gb'] = 0
        stats['disk_total_gb'] = 0
        stats['disk_percent'] = 0
    
    # Database sizes
    db_sizes = {}
    for db_name in ['sold.db', 'bol.db', 'amazonStore.db', 'ebayStore.db', 'searchRack.db', 'rawbol.db']:
        try:
            size_bytes = os.path.getsize(db_name)
            db_sizes[db_name] = round(size_bytes / (1024**2), 2)  # MB
        except Exception:
            db_sizes[db_name] = 0
    stats['db_sizes'] = db_sizes
    
    # Static folder size
    try:
        static_size = 0
        for dirpath, dirnames, filenames in os.walk('static'):
            for filename in filenames:
                filepath = os.path.join(dirpath, filename)
                static_size += os.path.getsize(filepath)
        stats['static_folder_size_mb'] = round(static_size / (1024**2), 2)
    except Exception:
        stats['static_folder_size_mb'] = 0
    
    # Picture position folder size and count
    try:
        picture_position_path = os.path.join('static', 'picture_position')
        if os.path.exists(picture_position_path):
            picture_size = 0
            picture_count = 0
            for dirpath, dirnames, filenames in os.walk(picture_position_path):
                for filename in filenames:
                    filepath = os.path.join(dirpath, filename)
                    picture_size += os.path.getsize(filepath)
                    picture_count += 1
            stats['picture_position_size_mb'] = round(picture_size / (1024**2), 2)
            stats['picture_position_count'] = picture_count
        else:
            stats['picture_position_size_mb'] = 0
            stats['picture_position_count'] = 0
    except Exception:
        stats['picture_position_size_mb'] = 0
        stats['picture_position_count'] = 0
    
    # Sync status (last sync times from sync_settings.db)
    try:
        conn = sqlite3.connect('sync_settings.db')
        cur = conn.cursor()
        cur.execute('CREATE TABLE IF NOT EXISTS sync_status (key TEXT PRIMARY KEY, value TEXT)')
        cur.execute('SELECT key, value FROM sync_status')
        sync_data = dict(cur.fetchall())
        
        stats['ebay_orders_last'] = sync_data.get('ebay_orders_last', 'Never')
        stats['ebay_listings_last'] = sync_data.get('ebay_listings_last', 'Never')
        stats['amazon_orders_last'] = sync_data.get('amazon_orders_last', 'Never')
        stats['amazon_listings_last'] = sync_data.get('amazon_listings_last', 'Never')
        stats['amazon_upcs_last'] = sync_data.get('amazon_upcs_last', 'Never')
        stats['auto_sync_enabled'] = sync_data.get('auto_sync_enabled', 'false') == 'true'
        
    except Exception:
        stats['ebay_orders_last'] = 'Unknown'
        stats['ebay_listings_last'] = 'Unknown'
        stats['amazon_orders_last'] = 'Unknown'
        stats['amazon_listings_last'] = 'Unknown'
        stats['amazon_upcs_last'] = 'Unknown'
        stats['auto_sync_enabled'] = False
    finally:
        conn.close()
    
    # System warnings
    warnings = []
    
    # Check for low disk space
    if stats['disk_percent'] > 90:
        warnings.append(f"⚠️ Low disk space: {stats['disk_percent']}% used")
    
    # Check for high memory usage
    if stats['memory_percent'] > 90:
        warnings.append(f"⚠️ High memory usage: {stats['memory_percent']}% used")
    
    # Check for high CPU temperature
    if stats['cpu_temp_current'] and stats['cpu_temp_current'] > 80:
        warnings.append(f"⚠️ High CPU temperature: {stats['cpu_temp_current']}°C")
    
    # Check for power issues
    if stats['power_plugged'] is False:
        if stats['battery_percent'] and stats['battery_percent'] < 20:
            warnings.append(f"🔋 CRITICAL: Battery low at {stats['battery_percent']}%! {stats['battery_time_left'] or 'Unknown time'} remaining")
        elif stats['battery_percent'] and stats['battery_percent'] < 50:
            warnings.append(f"🔋 WARNING: Running on battery - {stats['battery_percent']}% remaining")
        else:
            warnings.append(f"🔋 Running on battery power ({stats['battery_percent']}%)")
    
    # Check for Raspberry Pi undervoltage
    if stats['undervoltage_now']:
        count_text = f" (Event #{stats['undervoltage_count']})" if stats['undervoltage_count'] > 0 else ""
        warnings.append(f"⚡ CRITICAL: Undervoltage detected NOW!{count_text} Power supply insufficient!")
    elif stats['undervoltage_detected']:
        if stats['undervoltage_count'] > 0:
            warnings.append(f"⚡ WARNING: {stats['undervoltage_count']} undervoltage events detected - check power supply")
        else:
            warnings.append("⚡ WARNING: Undervoltage detected since boot - check power supply")
    
    # Check for other Pi throttling issues
    if stats.get('throttle_status'):
        ts = stats['throttle_status']
        if ts['currently_throttled']:
            throttle_text = f" ({stats['throttle_count']} events)" if stats['throttle_count'] > 0 else ""
            warnings.append(f"🐌 Performance throttled{throttle_text} due to power/temperature issues")
        if ts['soft_temp_limit_active']:
            warnings.append("🌡️ Soft temperature limit active - system thermal throttling")
    
    # Check for stale syncs (> 24 hours)
    for sync_key, sync_label in [
        ('ebay_orders_last', 'eBay Orders'),
        ('amazon_orders_last', 'Amazon Orders')
    ]:
        last_sync = stats.get(sync_key, 'Never')
        if last_sync not in ['Never', 'Unknown']:
            try:
                last_sync_dt = datetime.datetime.fromisoformat(last_sync)
                hours_ago = (datetime.datetime.now() - last_sync_dt).total_seconds() / 3600
                if hours_ago > 24:
                    warnings.append(f"⚠️ {sync_label} not synced in {int(hours_ago)} hours")
            except Exception:
                pass
    
    stats['warnings'] = warnings
    
    return stats

def generate_health_email_html(stats):
    """Generate HTML email body for health report"""
    warnings_html = ""
    if stats['warnings']:
        warnings_html = "<h3 style='color: #e74c3c;'>⚠️ WARNINGS</h3><ul style='margin: 10px 0;'>"
        for warning in stats['warnings']:
            warnings_html += f"<li style='color: #e74c3c;'>{warning}</li>"
        warnings_html += "</ul>"
    else:
        warnings_html = "<h3 style='color: #27ae60;'>✅ No Warnings</h3><p style='color: #7f8c8d;'>All systems operating normally</p>"
    
    disk_color = '#27ae60' if stats['disk_percent'] < 80 else ('#f39c12' if stats['disk_percent'] < 90 else '#e74c3c')
    memory_color = '#27ae60' if stats['memory_percent'] < 80 else ('#f39c12' if stats['memory_percent'] < 90 else '#e74c3c')
    cpu_color = '#27ae60' if stats['cpu_percent_current'] < 70 else ('#f39c12' if stats['cpu_percent_current'] < 90 else '#e74c3c')
    sync_status_color = '#27ae60' if stats['auto_sync_enabled'] else '#95a5a6'
    
    # CPU temperature display
    cpu_temp_html = ""
    if stats['cpu_temp_current'] is not None:
        temp_color = '#27ae60' if stats['cpu_temp_current'] < 70 else ('#f39c12' if stats['cpu_temp_current'] < 80 else '#e74c3c')
        cpu_temp_html = f"<div class='metric'><span class='label'>CPU Temperature:</span> <span class='value' style='color: {temp_color}; font-weight: bold;'>{stats['cpu_temp_current']}°C (avg: {stats['cpu_temp_avg']}°C)</span></div>"
    
    # Power status display
    power_html = ""
    if stats['power_plugged'] is not None:
        if stats['power_plugged']:
            power_html = "<div class='metric'><span class='label'>Power:</span> <span class='value' style='color: #27ae60; font-weight: bold;'>🔌 AC Power</span></div>"
        else:
            battery_color = '#e74c3c' if stats['battery_percent'] < 20 else ('#f39c12' if stats['battery_percent'] < 50 else '#27ae60')
            time_left_text = f" ({stats['battery_time_left']} left)" if stats['battery_time_left'] else ""
            power_html = f"<div class='metric'><span class='label'>Power:</span> <span class='value' style='color: {battery_color}; font-weight: bold;'>🔋 Battery {stats['battery_percent']}%{time_left_text}</span></div>"
    
    # Undervoltage status display (for Raspberry Pi)
    undervoltage_html = ""
    if stats.get('throttle_status'):
        ts = stats['throttle_status']
        if stats['undervoltage_now']:
            count_badge = f" <span style='background: #c0392b; color: white; padding: 2px 6px; border-radius: 3px; font-size: 11px;'>×{stats['undervoltage_count']}</span>" if stats['undervoltage_count'] > 0 else ""
            undervoltage_html = f"<div class='metric'><span class='label'>Voltage:</span> <span class='value' style='color: #e74c3c; font-weight: bold;'>⚡ UNDERVOLTAGE NOW!{count_badge}</span></div>"
        elif stats['undervoltage_detected']:
            if stats['undervoltage_count'] > 0:
                undervoltage_html = f"<div class='metric'><span class='label'>Voltage:</span> <span class='value' style='color: #f39c12; font-weight: bold;'>⚡ {stats['undervoltage_count']} events detected</span></div>"
            else:
                undervoltage_html = "<div class='metric'><span class='label'>Voltage:</span> <span class='value' style='color: #f39c12; font-weight: bold;'>⚡ Undervoltage occurred</span></div>"
        else:
            undervoltage_html = "<div class='metric'><span class='label'>Voltage:</span> <span class='value' style='color: #27ae60; font-weight: bold;'>✓ Normal</span></div>"
    
    html = f"""
    <html>
    <head>
        <style>
            body {{ font-family: 'Segoe UI', Arial, sans-serif; line-height: 1.6; color: #333; max-width: 800px; margin: 0 auto; padding: 20px; }}
            .header {{ background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); color: white; padding: 30px; border-radius: 10px; text-align: center; margin-bottom: 30px; }}
            .section {{ margin: 20px 0; padding: 20px; background: #f8f9fa; border-left: 4px solid #3498db; border-radius: 8px; }}
            .metric {{ margin: 12px 0; padding: 10px 0; border-bottom: 1px solid #e0e0e0; display: flex; justify-content: space-between; }}
            .metric:last-child {{ border-bottom: none; }}
            .label {{ font-weight: 600; color: #555; }}
            .value {{ color: #2c3e50; font-weight: 500; }}
            h2 {{ color: #2c3e50; margin-top: 0; padding-bottom: 10px; border-bottom: 2px solid #3498db; }}
            .footer {{ margin-top: 30px; padding: 20px; background: #ecf0f1; border-radius: 8px; font-size: 13px; color: #7f8c8d; }}
        </style>
    </head>
    <body>
        <div class='header'>
            <h1 style='margin: 0; font-size: 28px;'>📊 Store App Health Report</h1>
            <p style='margin: 10px 0 0 0; opacity: 0.9;'>{stats['timestamp']}</p>
        </div>
        
        <div class='section'>
            <h2>🏥 SERVER HEALTH</h2>
            <div class='metric'><span class='label'>Uptime:</span> <span class='value'>{stats['uptime']}</span></div>
            <div class='metric'><span class='label'>Python Version:</span> <span class='value'>{stats['python_version']}</span></div>
            <div class='metric'><span class='label'>Memory:</span> <span class='value' style='color: {memory_color}; font-weight: bold;'>{stats['memory_free_gb']} GB free ({stats['memory_used_gb']}/{stats['memory_total_gb']} GB used, {stats['memory_percent']}%)</span></div>
            <div class='metric'><span class='label'>CPU Usage:</span> <span class='value' style='color: {cpu_color}; font-weight: bold;'>{stats['cpu_percent_current']}% current (avg: {stats['cpu_percent_avg']}%)</span></div>
            {cpu_temp_html}
            {power_html}
            {undervoltage_html}
            <div class='metric'><span class='label'>Disk Space:</span> <span class='value' style='color: {disk_color}; font-weight: bold;'>{stats['disk_free_gb']} GB free ({100-stats['disk_percent']:.1f}% available)</span></div>
        </div>
        
        <div class='section'>
            <h2>🔄 SYNC STATUS</h2>
            <div class='metric'><span class='label'>Auto-sync:</span> <span class='value' style='color: {sync_status_color}; font-weight: bold;'>{'✅ Enabled' if stats['auto_sync_enabled'] else '❌ Disabled'}</span></div>
            <div class='metric'><span class='label'>eBay Orders:</span> <span class='value'>{format_time_ago(stats['ebay_orders_last'])}</span></div>
            <div class='metric'><span class='label'>Amazon Orders:</span> <span class='value'>{format_time_ago(stats['amazon_orders_last'])}</span></div>
            <div class='metric'><span class='label'>Amazon UPCs:</span> <span class='value'>{format_time_ago(stats['amazon_upcs_last'])}</span></div>
        </div>
        
        <div class='section' style='border-left-color: {"#e74c3c" if stats["warnings"] else "#27ae60"};'>
            {warnings_html}
        </div>
        
        <div class='footer'>
            <p style='margin: 0 0 10px 0; font-weight: bold;'>📁 Database Sizes:</p>
            <ul style='margin: 5px 0; padding-left: 20px;'>
                {''.join([f"<li>{db}: <strong>{size} MB</strong></li>" for db, size in stats['db_sizes'].items()])}
            </ul>
            <p style='margin: 15px 0 10px 0; font-weight: bold;'>📂 Folder Sizes:</p>
            <ul style='margin: 5px 0; padding-left: 20px;'>
                <li>Static Folder: <strong>{stats['static_folder_size_mb']} MB</strong></li>
                <li>Picture Position: <strong>{stats['picture_position_size_mb']} MB</strong> ({stats['picture_position_count']} pictures)</li>
            </ul>
        </div>
    </body>
    </html>
    """
    return html

def generate_health_email_plain(stats):
    """Generate plain text email body for health report"""
    warnings_text = "\n".join(stats['warnings']) if stats['warnings'] else "✅ None"
    
    cpu_temp_text = ""
    if stats['cpu_temp_current'] is not None:
        cpu_temp_text = f"\n├─ CPU Temp: {stats['cpu_temp_current']}°C (avg: {stats['cpu_temp_avg']}°C)"
    
    power_text = ""
    if stats['power_plugged'] is not None:
        if stats['power_plugged']:
            power_text = "\n├─ Power: 🔌 AC Power"
        else:
            time_left = f" ({stats['battery_time_left']} left)" if stats['battery_time_left'] else ""
            power_text = f"\n├─ Power: 🔋 Battery {stats['battery_percent']}%{time_left}"
    
    # Undervoltage status (for Raspberry Pi)
    voltage_text = ""
    if stats.get('throttle_status'):
        if stats['undervoltage_now']:
            count_text = f" (×{stats['undervoltage_count']})" if stats['undervoltage_count'] > 0 else ""
            voltage_text = f"\n├─ Voltage: ⚡ UNDERVOLTAGE NOW!{count_text}"
        elif stats['undervoltage_detected']:
            if stats['undervoltage_count'] > 0:
                voltage_text = f"\n├─ Voltage: ⚡ {stats['undervoltage_count']} events detected"
            else:
                voltage_text = "\n├─ Voltage: ⚡ Undervoltage occurred"
        else:
            voltage_text = "\n├─ Voltage: ✓ Normal"
    
    text = f"""
📊 STORE APP HEALTH REPORT
Generated: {stats['timestamp']}

🏥 SERVER HEALTH
├─ Uptime: {stats['uptime']}
├─ Python: {stats['python_version']}
├─ Memory: {stats['memory_free_gb']} GB free ({stats['memory_used_gb']}/{stats['memory_total_gb']} GB used, {stats['memory_percent']}%)
├─ CPU Usage: {stats['cpu_percent_current']}% current (avg: {stats['cpu_percent_avg']}%){cpu_temp_text}{power_text}{voltage_text}
└─ Disk: {stats['disk_free_gb']} GB free ({100-stats['disk_percent']:.1f}%)

🔄 SYNC STATUS
├─ Auto-sync: {'✅ Enabled' if stats['auto_sync_enabled'] else '❌ Disabled'}
├─ eBay Orders: {format_time_ago(stats['ebay_orders_last'])}
├─ Amazon Orders: {format_time_ago(stats['amazon_orders_last'])}
└─ Amazon UPCs: {format_time_ago(stats['amazon_upcs_last'])}

⚠️ WARNINGS
{warnings_text}

📁 DATABASE SIZES
{', '.join([f"{db}: {size}MB" for db, size in stats['db_sizes'].items()])}

📂 FOLDER SIZES
Static: {stats['static_folder_size_mb']} MB
Picture Position: {stats['picture_position_size_mb']} MB ({stats['picture_position_count']} pictures)
"""
    return text

def format_time_ago(timestamp_str):
    """Format timestamp as human-readable time ago"""
    if timestamp_str in ['Never', 'Unknown']:
        return timestamp_str
    
    try:
        dt = datetime.datetime.fromisoformat(timestamp_str)
        now = datetime.datetime.now()
        diff = now - dt
        
        if diff.total_seconds() < 60:
            return "✅ Just now"
        elif diff.total_seconds() < 3600:
            mins = int(diff.total_seconds() / 60)
            return f"✅ {mins}m ago"
        elif diff.total_seconds() < 86400:
            hours = int(diff.total_seconds() / 3600)
            return f"⏰ {hours}h ago"
        else:
            days = int(diff.total_seconds() / 86400)
            return f"⚠️ {days}d ago"
    except Exception:
        return timestamp_str

def collect_inventory_mismatches():
    """
    Find items where store listings have quantity > 0 but searchRack has 0 quantity.
    Only reports on items that exist in searchRack with 0 quantity (not missing items).
    Also checks for duplicate barcodes in different locations.
    """
    issues = {
        'has_issues': False,
        'ebay_items': [],
        'amazon_items': [],
        'duplicate_locations': [],
        'timestamp': datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    }
    
    try:
        # Get searchRack items with 0 quantity
        rack_conn = sqlite3.connect('searchRack.db')
        rack_conn.row_factory = sqlite3.Row
        rack_cur = rack_conn.cursor()
        
        # Get quantity column name
        rack_cur.execute('PRAGMA table_info(SEARCHRACK)')
        cols = [r[1] for r in rack_cur.fetchall()]
        qty_col = 'QUANTITY' if 'QUANTITY' in cols else ('QTY' if 'QTY' in cols else None)
        
        if not qty_col:
            return issues
        
        # Get all items with 0 quantity and their barcodes
        rack_cur.execute(f'''
            SELECT ITEMID as barcode, TITLE, {qty_col} as quantity 
            FROM SEARCHRACK 
            WHERE ITEMID IS NOT NULL 
            AND TRIM(ITEMID) != ''
            AND ({qty_col} = 0 OR {qty_col} IS NULL)
        ''')
        zero_qty_items = {row['barcode'].strip().upper(): row['TITLE'] for row in rack_cur.fetchall() if row['barcode']}
        
        # Check eBay store for matching items with quantity > 0 (only if we have zero-qty items)
        if zero_qty_items:
            try:
                ebay_conn = sqlite3.connect('ebayStore.db')
                ebay_conn.row_factory = sqlite3.Row
                ebay_cur = ebay_conn.cursor()
                
                ebay_cur.execute('''
                    SELECT SKU, Title, Quantity, ItemID 
                    FROM INVENTORY 
                    WHERE SKU IS NOT NULL 
                    AND TRIM(SKU) != ''
                    AND CAST(Quantity AS INTEGER) > 0
                ''')
                
                for row in ebay_cur.fetchall():
                    sku = row['SKU'].strip().upper() if row['SKU'] else ''
                    if sku and sku in zero_qty_items:
                        issues['ebay_items'].append({
                            'barcode': sku,
                            'title': row['Title'] or zero_qty_items[sku],
                            'store_qty': row['Quantity'],
                            'item_id': row['ItemID']
                        })
                        issues['has_issues'] = True
                
            except Exception as e:
                print(f"Error checking eBay inventory: {e}")
            finally:
                ebay_conn.close()
            
            # Check Amazon store for matching items with quantity > 0
            try:
                amazon_conn = sqlite3.connect('amazonStore.db')
                amazon_conn.row_factory = sqlite3.Row
                amazon_cur = amazon_conn.cursor()
                
                amazon_cur.execute('''
                    SELECT UPC, TITLE, QTY, ASIN 
                    FROM ITEMS 
                    WHERE UPC IS NOT NULL 
                    AND TRIM(UPC) != ''
                    AND CAST(QTY AS INTEGER) > 0
                ''')
                
                for row in amazon_cur.fetchall():
                    upc = row['UPC'].strip().upper() if row['UPC'] else ''
                    if upc and upc in zero_qty_items:
                        issues['amazon_items'].append({
                            'barcode': upc,
                            'title': row['TITLE'] or zero_qty_items[upc],
                            'store_qty': row['QTY'],
                            'asin': row['ASIN']
                        })
                        issues['has_issues'] = True
                
            except Exception as e:
                print(f"Error checking Amazon inventory: {e}")
            finally:
                amazon_conn.close()
        
        # Check for duplicate barcodes (non-suffixed) in different locations
        try:
            rack_conn = sqlite3.connect('searchRack.db')
            rack_conn.row_factory = sqlite3.Row
            rack_cur = rack_conn.cursor()
            
            # Get quantity column name
            rack_cur.execute('PRAGMA table_info(SEARCHRACK)')
            cols = [r[1] for r in rack_cur.fetchall()]
            qty_col = 'QUANTITY' if 'QUANTITY' in cols else ('QTY' if 'QTY' in cols else None)
            
            if qty_col:
                # Find barcodes that appear in multiple different locations (excluding suffixed barcodes)
                rack_cur.execute(f'''
                    SELECT 
                        BARCODE,
                        TITLE,
                        GROUP_CONCAT(ITEM_POSITION || ' (Qty: ' || {qty_col} || ')', ', ') as locations,
                        COUNT(DISTINCT ITEM_POSITION) as location_count,
                        SUM({qty_col}) as total_qty
                    FROM SEARCHRACK
                    WHERE BARCODE IS NOT NULL 
                    AND TRIM(BARCODE) != ''
                    AND BARCODE NOT LIKE '%-%'
                    AND ITEM_POSITION IS NOT NULL
                    AND TRIM(ITEM_POSITION) != ''
                    AND ({qty_col} > 0 OR {qty_col} IS NULL)
                    GROUP BY BARCODE
                    HAVING COUNT(DISTINCT ITEM_POSITION) > 1
                    ORDER BY location_count DESC, BARCODE
                ''')
                
                for row in rack_cur.fetchall():
                    issues['duplicate_locations'].append({
                        'barcode': row['BARCODE'],
                        'title': row['TITLE'] or 'Unknown',
                        'locations': row['locations'],
                        'location_count': row['location_count'],
                        'total_qty': row['total_qty'] or 0
                    })
                    issues['has_issues'] = True
            
        except Exception as e:
            print(f"Error checking for duplicate locations: {e}")
        finally:
            rack_conn.close()
        
    except Exception as e:
        print(f"Error collecting inventory mismatches: {e}")
    finally:
        rack_conn.close()
    
    return issues

def generate_inventory_email_html(issues):
    """Generate HTML email body for inventory alert"""
    ebay_rows = ""
    for item in issues['ebay_items']:
        ebay_rows += f"""
        <tr>
            <td style="padding: 12px; border-bottom: 1px solid #e0e0e0;">{item['barcode']}</td>
            <td style="padding: 12px; border-bottom: 1px solid #e0e0e0;">{item['title'][:60]}...</td>
            <td style="padding: 12px; border-bottom: 1px solid #e0e0e0; text-align: center; font-weight: bold; color: #e74c3c;">{item['store_qty']}</td>
        </tr>
        """
    
    amazon_rows = ""
    for item in issues['amazon_items']:
        amazon_rows += f"""
        <tr>
            <td style="padding: 12px; border-bottom: 1px solid #e0e0e0;">{item['barcode']}</td>
            <td style="padding: 12px; border-bottom: 1px solid #e0e0e0;">{item['title'][:60]}...</td>
            <td style="padding: 12px; border-bottom: 1px solid #e0e0e0; text-align: center; font-weight: bold; color: #e74c3c;">{item['store_qty']}</td>
        </tr>
        """
    
    total_issues = len(issues['ebay_items']) + len(issues['amazon_items'])
    
    html = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <style>
            body {{ font-family: 'Segoe UI', Arial, sans-serif; background: #f9f9f9; margin: 0; padding: 20px; }}
            .container {{ max-width: 900px; margin: 0 auto; background: white; border-radius: 12px; box-shadow: 0 2px 8px rgba(0,0,0,0.1); }}
            .header {{ background: linear-gradient(135deg, #e74c3c, #c0392b); color: white; padding: 30px; border-radius: 12px 12px 0 0; }}
            .header h1 {{ margin: 0; font-size: 28px; }}
            .header p {{ margin: 10px 0 0 0; opacity: 0.9; }}
            .content {{ padding: 30px; }}
            .section {{ margin-bottom: 30px; }}
            .section h2 {{ color: #2c3e50; font-size: 20px; margin: 0 0 15px 0; padding-bottom: 10px; border-bottom: 2px solid #e0e0e0; }}
            table {{ width: 100%; border-collapse: collapse; background: white; }}
            th {{ background: #34495e; color: white; padding: 12px; text-align: left; font-weight: 600; }}
            .alert {{ background: #fff3cd; border-left: 4px solid #ffc107; padding: 15px; margin: 20px 0; border-radius: 4px; }}
            .footer {{ padding: 20px; text-align: center; color: #7f8c8d; font-size: 14px; background: #ecf0f1; border-radius: 0 0 12px 12px; }}
        </style>
    </head>
    <body>
        <div class="container">
            <div class="header">
                <h1>⚠️ Inventory Alert</h1>
                <p>{total_issues} issue(s) detected in inventory</p>
                <p style="font-size: 14px; margin-top: 10px;">{issues['timestamp']}</p>
            </div>
            
            <div class="content">
                <div class="alert">
                    <strong>⚠️ Action Required:</strong> Issues detected with your inventory. Please review and address the following items.
                </div>
    """
    
    if issues['ebay_items']:
        html += f"""
                <div class="section">
                    <h2>🛒 eBay ({len(issues['ebay_items'])} items)</h2>
                    <p style="color: #7f8c8d; margin-bottom: 15px;">Listed online but showing 0 in physical inventory:</p>
                    <table>
                        <thead>
                            <tr>
                                <th>Barcode (SKU)</th>
                                <th>Title</th>
                                <th style="text-align: center;">Store Qty</th>
                            </tr>
                        </thead>
                        <tbody>
                            {ebay_rows}
                        </tbody>
                    </table>
                </div>
        """
    
    if issues['amazon_items']:
        html += f"""
                <div class="section">
                    <h2>📦 Amazon ({len(issues['amazon_items'])} items)</h2>
                    <p style="color: #7f8c8d; margin-bottom: 15px;">Listed online but showing 0 in physical inventory:</p>
                    <table>
                        <thead>
                            <tr>
                                <th>Barcode (UPC)</th>
                                <th>Title</th>
                                <th style="text-align: center;">Store Qty</th>
                            </tr>
                        </thead>
                        <tbody>
                            {amazon_rows}
                        </tbody>
                    </table>
                </div>
        """
    
    html += """
            </div>
            
            <div class="footer">
                <p>This is an automated inventory alert from your Store App</p>
            </div>
        </div>
    </body>
    </html>
    """
    
    return html

def generate_inventory_email_plain(issues):
    """Generate plain text email body for inventory alert"""
    total_issues = len(issues['ebay_items']) + len(issues['amazon_items'])
    
    text = f"""
⚠️ INVENTORY ALERT
Generated: {issues['timestamp']}

{total_issues} issue(s) detected in inventory.
Action required: Please review and address the following items.

"""
    
    if issues['ebay_items']:
        text += f"\n🛒 eBay ({len(issues['ebay_items'])} items):\n"
        text += "Listed online but showing 0 in physical inventory\n"
        text += "-" * 70 + "\n"
        for item in issues['ebay_items']:
            text += f"Barcode: {item['barcode']}\n"
            text += f"Title: {item['title'][:60]}\n"
            text += f"Store Qty: {item['store_qty']}\n"
            text += "-" * 70 + "\n"
    
    if issues['amazon_items']:
        text += f"\n📦 Amazon ({len(issues['amazon_items'])} items):\n"
        text += "Listed online but showing 0 in physical inventory\n"
        text += "-" * 70 + "\n"
        for item in issues['amazon_items']:
            text += f"Barcode: {item['barcode']}\n"
            text += f"Title: {item['title'][:60]}\n"
            text += f"Store Qty: {item['store_qty']}\n"
            text += "-" * 70 + "\n"
    
    text += """
--
This is an automated inventory alert from your Store App.
"""
    
    return text

# BOL Statistics page
@app.route('/bol-stats')
def bol_stats_page():
    return render_template('bol_stats.html')

# Barcode Print Que page
@app.route('/barcode-print-que')
def barcode_print_que():
     return render_template('barcode_print_que.html')

@app.route('/barcode-print-view')
def barcode_print_view():
    """Mobile-friendly print view for barcode queue"""
    return render_template('barcode_print_view.html')

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
# Temporarily disable cache to debug
# @cache.cached(timeout=300, query_string=True)  # Cache for 5 minutes
def api_financial_analytics():
    """
    Fetch all sold orders with cost data from BOL and calculate profits.
    Returns financial metrics for the dashboard.
    """
    print("DEBUG: Financial analytics API called")
    try:
        # Use connection pool for better performance
        sold_conn = get_db_connection('sold.db')
        sold_cur = sold_conn.cursor()
        
        # Get all sold orders with lot_number
        sold_cur.execute('''
            SELECT 
                id,
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
        
        # Use connection pool for other databases
        rawbol_conn = get_db_connection('rawbol.db')
        rawbol_cur = rawbol_conn.cursor()
        
        ebay_conn = get_db_connection('ebayStore.db')
        ebay_cur = ebay_conn.cursor()
        
        amazon_conn = get_db_connection('amazonStore.db')
        amazon_cur = amazon_conn.cursor()
        
        # Get returns data from sold.db
        returns_data = {}
        unmatched_returns = []  # Store returns without original_order_id
        try:
            sold_cur.execute('''
                SELECT 
                    original_order_id,
                    order_id,
                    title,
                    refund_amount,
                    original_shipping_cost,
                    return_shipping_cost,
                    return_date,
                    store,
                    return_reason
                FROM returns
            ''')
            
            returns_rows = sold_cur.fetchall()
            print(f"DEBUG: Found {len(returns_rows)} returns in database")
            
            for row in returns_rows:
                original_order_id = row['original_order_id']
                total_return_cost = (
                    (row['refund_amount'] or 0) +
                    (row['original_shipping_cost'] or 0) +
                    (row['return_shipping_cost'] or 0)
                )
                
                if original_order_id:
                    # Matched return - add to returns_data for order matching
                    returns_data[original_order_id] = total_return_cost
                else:
                    # Unmatched return - add as standalone transaction
                    unmatched_returns.append({
                        'order_id': row['order_id'],
                        'title': row['title'],
                        'refund_amount': row['refund_amount'],
                        'return_cost': total_return_cost,
                        'return_date': row['return_date'],
                        'store': row['store'],
                        'return_reason': row['return_reason'],
                        'is_unmatched_return': True
                    })
                
            print(f"DEBUG: Processed {len(returns_data)} matched returns, {len(unmatched_returns)} unmatched returns, total return costs: ${(sum(returns_data.values()) + sum(r['return_cost'] for r in unmatched_returns)):.2f}")
        except Exception as e:
            print(f"Warning: Could not load returns data: {e}")
            import traceback
            traceback.print_exc()
        
        transactions = []
        
        for order in orders:
            item_data = dict(order)
            
            # Add return cost if this order has a return
            # Use .get() to safely access the id key
            order_pk_id = item_data.get('id', 0)
            item_data['return_cost'] = returns_data.get(order_pk_id, 0)
            
            # Skip cost lookup for marketplace sales (no cost associated)
            if order['store'] == 'marketplace':
                item_data['cost'] = None
                item_data['cost_source'] = 'marketplace'
                item_data['upc'] = order['barcode'] or order['item_id']
                item_data['bol_number'] = None
                transactions.append(item_data)
                continue
            
            # Try to get cost from rawbol.db
            upc = order['barcode'] or order['item_id']
            cost = None
            cost_source = None
            # LOT number comes directly from sold.db (already enriched from rawbol.db)
            bol_number = order['lot_number'] if order['lot_number'] and str(order['lot_number']).lower() not in ['', 'nan', 'none', 'null'] else None
            
            if upc:
                # Check rawbol for avg_cost - try direct UPC match first
                rawbol_cur.execute('SELECT avg_cost, lot_number FROM raw_bol_items WHERE upc = ? COLLATE NOCASE ORDER BY created_at DESC LIMIT 1', (upc,))
                rawbol_row = rawbol_cur.fetchone()
                if rawbol_row and rawbol_row['avg_cost']:
                    cost = float(rawbol_row['avg_cost'])
                    cost_source = 'rawbol'
                    # Update bol_number if we found it in rawbol and it wasn't set
                    if not bol_number and rawbol_row['lot_number']:
                        bol_number = rawbol_row['lot_number']
                
                # For Amazon items, if barcode looks like ASIN, try to get UPC from amazonStore
                # and re-lookup in rawbol (handles old items that had ASIN as barcode)
                if not cost and order['store'] == 'amazon' and upc and (upc.startswith('B0') or len(upc) == 10):
                    amazon_cur.execute('SELECT UPC FROM ITEMS WHERE ASIN = ? COLLATE NOCASE', (upc,))
                    amazon_row = amazon_cur.fetchone()
                    if amazon_row and amazon_row['UPC']:
                        actual_upc = amazon_row['UPC']
                        # Try rawbol again with the actual UPC
                        rawbol_cur.execute('SELECT avg_cost, lot_number FROM raw_bol_items WHERE upc = ? COLLATE NOCASE ORDER BY created_at DESC LIMIT 1', (actual_upc,))
                        rawbol_row = rawbol_cur.fetchone()
                        if rawbol_row and rawbol_row['avg_cost']:
                            cost = float(rawbol_row['avg_cost'])
                            cost_source = 'rawbol_via_asin'
                            if not bol_number and rawbol_row['lot_number']:
                                bol_number = rawbol_row['lot_number']
                
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
        
        # No need to close connections - they're pooled and reused
        
        # Get LOT # data from rawbol.db upload_logs
        lot_options = []
        try:
            # Reuse existing connection
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
            
        except Exception as e:
            print(f"Warning: Could not fetch LOT # data from rawbol.db: {e}")
            import traceback
            traceback.print_exc()
        
        # Add unmatched returns as standalone transactions (for financial impact)
        for unmatched in unmatched_returns:
            # Create a transaction entry for unmatched return
            # Use negative values to represent a loss
            transactions.append({
                'id': None,
                'order_id': unmatched['order_id'],
                'title': unmatched['title'],
                'quantity': 1,
                'price': 0,  # No revenue from return
                'seller_fee': 0,
                'taxes': 0,
                'paid_time': unmatched['return_date'],
                'shipped_time': None,
                'barcode': None,
                'store': unmatched['store'],
                'location': None,
                'shipping_cost': 0,
                'lot_number': None,
                'return_cost': unmatched['return_cost'],
                'cost': 0,
                'cost_source': 'unmatched_return',
                'upc': None,
                'bol_number': None,
                'is_unmatched_return': True  # Flag for UI
            })
        
        print(f"DEBUG: Total transactions (including {len(unmatched_returns)} unmatched returns): {len(transactions)}")
        
        # Get BOL extract totals from rawbol.db (total_client_cost + shipping_cost per LOT)
        bol_extract_totals = {}
        try:
            rawbol_cur.execute('''
                SELECT 
                    lot_number,
                    total_client_cost,
                    shipping_cost
                FROM upload_logs
                WHERE lot_number IS NOT NULL
            ''')
            
            for row in rawbol_cur.fetchall():
                lot_num = row['lot_number']
                total_cost = float(row['total_client_cost']) if row['total_client_cost'] else 0
                shipping_cost = float(row['shipping_cost']) if row['shipping_cost'] else 0
                
                bol_extract_totals[lot_num] = {
                    'total_cost': total_cost,
                    'shipping_cost': shipping_cost
                }
            
            print(f"DEBUG: Loaded BOL extract totals for {len(bol_extract_totals)} LOTs")
        except Exception as e:
            print(f"Warning: Could not fetch BOL extract totals: {e}")
        
        return jsonify({
            'success': True,
            'transactions': transactions,
            'count': len(transactions),
            'lot_options': lot_options,
            'bol_extract_totals': bol_extract_totals
        })
        
    except Exception as e:
        print(f"Error in financial analytics API: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({
            'success': False,
            'error': _safe_error(e)
        }), 500

@app.route('/api/amazon/financial-summary', methods=['GET'])
@cache.cached(timeout=300, query_string=True)  # Cache for 5 minutes
def api_amazon_financial_summary():
    """
    Get aggregate financial totals from Amazon Financial Events API.
    Returns total seller fees, shipping costs, taxes, and returns data across all orders.
    Query params: days (default 30), include_returns (default true)
    """
    if not AMAZON_AVAILABLE:
        return jsonify({'success': False, 'error': 'Amazon integration not available'}), 503
    
    try:
        days = int(request.args.get('days', 30))
        include_returns = request.args.get('include_returns', 'true').lower() == 'true'
        
        amazon = AmazonManager()
        financial_data = amazon.get_financial_events(days_back=days)
        
        # Calculate totals
        total_seller_fees = 0
        total_shipping_costs = 0
        total_taxes = 0
        orders_with_fees = 0
        orders_with_shipping = 0
        
        for order_id, data in financial_data.items():
            if data['seller_fee'] > 0:
                total_seller_fees += data['seller_fee']
                orders_with_fees += 1
            
            if data['shipping_cost'] > 0:
                total_shipping_costs += data['shipping_cost']
                orders_with_shipping += 1
            
            total_taxes += data['taxes']
        
        summary = {
            'total_orders': len(financial_data),
            'orders_with_fees': orders_with_fees,
            'orders_with_shipping': orders_with_shipping,
            'total_seller_fees': round(total_seller_fees, 2),
            'total_shipping_costs': round(total_shipping_costs, 2),
            'total_taxes': round(total_taxes, 2),
            'total_amazon_costs': round(total_seller_fees + total_shipping_costs + total_taxes, 2)
        }
        
        # Add returns data if requested
        if include_returns:
            try:
                conn = sqlite3.connect('sold.db')
                conn.row_factory = sqlite3.Row
                cur = conn.cursor()
                
                # Get returns within the same time period
                cur.execute('''
                    SELECT 
                        COUNT(*) as total_returns,
                        SUM(refund_amount) as total_refunded,
                        SUM(refund_amount + original_shipping_cost + return_shipping_cost) as total_return_cost
                    FROM returns
                    WHERE store = 'amazon'
                    AND return_date >= datetime('now', '-' || ? || ' days')
                ''', (days,))
                
                returns_row = cur.fetchone()
                
                summary['returns'] = {
                    'total_returns': returns_row['total_returns'] or 0,
                    'total_refunded': round(returns_row['total_refunded'] or 0, 2),
                    'total_return_cost': round(returns_row['total_return_cost'] or 0, 2),
                    'return_rate': round((returns_row['total_returns'] or 0) / len(financial_data) * 100, 2) if len(financial_data) > 0 else 0
                }
                
                # Calculate net profit impact
                summary['net_costs_with_returns'] = round(
                    summary['total_amazon_costs'] + summary['returns']['total_return_cost'], 2
                )
                
            except Exception as e:
                print(f"Warning: Could not include returns data: {e}")
                summary['returns'] = None
            finally:
                conn.close()
        
        return jsonify({
            'success': True,
            'days': days,
            'summary': summary
        })
        
    except Exception as e:
        print(f"Error fetching Amazon financial summary: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({
            'success': False,
            'error': _safe_error(e)
        }), 500

@app.route('/api/refresh-sold-data', methods=['POST'])
def api_refresh_sold_data():
    """
    Re-enrich sold.db orders with latest barcode and LOT number data.
    This updates old Amazon items that may have ASIN as barcode to use proper UPC.
    """
    try:
        print("DEBUG: Refreshing sold data...")
        
        # Use connection pool
        sold_conn = get_db_connection('sold.db')
        sold_cur = sold_conn.cursor()
        
        amazon_conn = get_db_connection('amazonStore.db')
        amazon_cur = amazon_conn.cursor()
        
        rawbol_conn = get_db_connection('rawbol.db')
        rawbol_cur = rawbol_conn.cursor()
        
        # Get Amazon orders that might need updating
        sold_cur.execute('''
            SELECT id, order_id, item_id, barcode, lot_number
            FROM orders
            WHERE store = 'amazon' AND barcode IS NOT NULL
        ''')
        amazon_orders = sold_cur.fetchall()
        
        updated_barcodes = 0
        updated_lot_numbers = 0
        
        for order in amazon_orders:
            order_id = order['order_id']
            current_barcode = order['barcode']
            current_lot = order['lot_number']
            needs_update = False
            new_barcode = current_barcode
            new_lot = current_lot
            
            # Check if barcode looks like ASIN (old format)
            if current_barcode and (current_barcode.startswith('B0') or len(current_barcode) == 10):
                # Try to get UPC from amazonStore
                amazon_cur.execute('SELECT UPC FROM ITEMS WHERE ASIN = ? COLLATE NOCASE', (current_barcode,))
                amazon_row = amazon_cur.fetchone()
                if amazon_row and amazon_row['UPC'] and amazon_row['UPC'] != current_barcode:
                    new_barcode = amazon_row['UPC']
                    needs_update = True
                    updated_barcodes += 1
            
            # Check if we can enrich lot_number from rawbol using the (possibly new) barcode
            if not current_lot or str(current_lot).lower() in ['', 'nan', 'none', 'null']:
                rawbol_cur.execute('SELECT lot_number FROM raw_bol_items WHERE upc = ? COLLATE NOCASE ORDER BY created_at DESC LIMIT 1', (new_barcode,))
                rawbol_row = rawbol_cur.fetchone()
                if rawbol_row and rawbol_row['lot_number']:
                    new_lot = rawbol_row['lot_number']
                    needs_update = True
                    updated_lot_numbers += 1
            
            # Update if changes were made
            if needs_update:
                sold_cur.execute('UPDATE orders SET barcode = ?, lot_number = ? WHERE id = ?', 
                               (new_barcode, new_lot, order['id']))
                print(f"✅ Updated {order_id}: barcode={new_barcode}, lot={new_lot}")
        
        sold_conn.commit()
        
        # No need to close - connections are pooled
        
        return jsonify({
            'success': True,
            'updated_barcodes': updated_barcodes,
            'updated_lot_numbers': updated_lot_numbers,
            'message': f'Updated {updated_barcodes} barcodes and {updated_lot_numbers} LOT numbers'
        })
        
    except Exception as e:
        print(f"Error refreshing sold data: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({
            'success': False,
            'error': _safe_error(e)
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
        if 'quantity' not in status_cols:
            cur.execute('ALTER TABLE items_prep_status ADD COLUMN quantity INTEGER DEFAULT 1')
        
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
    except Exception as e:
        print('Failed ensuring items_prep tables:', e)
    finally:
        conn.close()

def _now_iso():
    import datetime as _dt
    return _dt.datetime.now(_dt.UTC).isoformat()

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
                return int(row[0])
    except Exception:
        pass
    finally:
        conn.close()
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
    except Exception as e:
        print('Failed ensuring app_settings:', e)
    finally:
        conn.close()

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
                base = _dt.datetime.now(_dt.UTC)
            new_exp = (base + _dt.timedelta(days=days)).isoformat()
            cur.execute('UPDATE items_prep_images SET expires_at=? WHERE id=?', (new_exp, rid))
        conn.commit()
        return True
    except Exception as e:
        print('Failed setting retention days:', e)
        return False
    finally:
        conn.close()

def _move_to_trash(abs_path, upc):
    try:
        # Build trash path under static/items_prep_trash/YYYY/MM/UPC
        import datetime as _dt
        base = os.path.join(app.root_path, 'static', 'items_prep_trash')
        now = _dt.datetime.now(_dt.UTC)
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
        return count
    except Exception as e:
        print('Purge error:', e)
        return 0
    finally:
        conn.close()

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

# ZERO-QUANTITY DELETION WORKER
# ============================================================================
_zero_qty_deleter_started = False
_automatic_removal_started = False

def _purge_zero_qty_items():
    """Delete searchRack items that have been at 0 quantity for the configured interval"""
    try:
        conn = sqlite3.connect('searchRack.db')
        cur = conn.cursor()
        
        # Ensure tables exist
        cur.execute('''
            CREATE TABLE IF NOT EXISTS zero_qty_pending_deletion (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                searchrack_id INTEGER,
                marked_at TEXT,
                delete_at TEXT,
                deletion_cancelled INTEGER DEFAULT 0
            )
        ''')
        
        cur.execute('''
            CREATE TABLE IF NOT EXISTS zero_qty_settings (
                key TEXT PRIMARY KEY,
                value TEXT
            )
        ''')
        
        # Get interval from settings
        cur.execute("SELECT value FROM zero_qty_settings WHERE key = 'interval_minutes'")
        settings = cur.fetchone()
        interval_minutes = int(settings[0]) if settings else 60
        
        from datetime import datetime, timedelta
        now = datetime.now()
        
        # STEP 1: Scan for unmarked zero-quantity items and mark them
        print(f"🔍 Scanning for unmarked zero-quantity items...")
        cur.execute('''
            SELECT ID, BARCODE, TITLE 
            FROM SEARCHRACK 
            WHERE QUANTITY = 0
        ''')
        zero_qty_items = cur.fetchall()
        
        if zero_qty_items:
            # Get already marked items
            cur.execute('SELECT searchrack_id FROM zero_qty_pending_deletion')
            already_marked = {row[0] for row in cur.fetchall()}
            
            marked_count = 0
            for item_id, barcode, title in zero_qty_items:
                if item_id not in already_marked:
                    delete_at = now + timedelta(minutes=interval_minutes)
                    cur.execute('''
                        INSERT INTO zero_qty_pending_deletion (searchrack_id, marked_at, delete_at, deletion_cancelled)
                        VALUES (?, ?, ?, 0)
                    ''', (item_id, now.isoformat(), delete_at.isoformat()))
                    marked_count += 1
                    print(f"  ✓ Marked item {item_id} ({barcode}): {title[:50]} for deletion")
            
            if marked_count > 0:
                print(f"✅ Marked {marked_count} new zero-quantity items for deletion")
                conn.commit()
        
        # STEP 2: Find items ready for deletion (past grace period and not cancelled)
        cur.execute('''
            SELECT id, searchrack_id, marked_at, delete_at 
            FROM zero_qty_pending_deletion 
            WHERE delete_at <= ? AND COALESCE(deletion_cancelled, 0) = 0
        ''', (now.isoformat(),))
        
        items_to_delete = cur.fetchall()
        
        if not items_to_delete:
            return
        
        print(f"🗑️  Processing {len(items_to_delete)} zero-quantity items for deletion...")
        
        # Ensure archived table exists
        cur.execute('''
            CREATE TABLE IF NOT EXISTS archived_searchrack (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                original_id INTEGER,
                title TEXT,
                barcode TEXT,
                item_position TEXT,
                images TEXT,
                pictureposition TEXT,
                itemid TEXT,
                quantity INTEGER,
                created_at TEXT,
                image TEXT,
                archived_at TEXT,
                reason TEXT
            )
        ''')
        
        deleted_count = 0
        for pending_id, searchrack_id, marked_at, delete_at in items_to_delete:
            # Verify the item still has 0 quantity
            cur.execute('SELECT QUANTITY FROM SEARCHRACK WHERE ID = ?', (searchrack_id,))
            row = cur.fetchone()
            
            if not row:
                # Item already deleted somehow, just remove from pending
                cur.execute('DELETE FROM zero_qty_pending_deletion WHERE id = ?', (pending_id,))
                continue
            
            qty = row[0] or 0
            if qty > 0:
                # Quantity was increased, remove from pending deletion
                cur.execute('DELETE FROM zero_qty_pending_deletion WHERE id = ?', (pending_id,))
                print(f"  ℹ️  Item {searchrack_id} quantity is now {qty}, skipping deletion")
                continue
            
            # Archive the item before deletion
            cur.execute('''
                INSERT INTO archived_searchrack 
                (original_id, title, barcode, item_position, images, pictureposition, itemid, quantity, created_at, image, archived_at, reason)
                SELECT ID, TITLE, BARCODE, ITEM_POSITION, IMAGES, PICTUREPOSITION, ITEMID, QUANTITY, CREATED_AT, IMAGE, ?, 'zero_quantity_auto_delete'
                FROM SEARCHRACK WHERE ID = ?
            ''', (now.isoformat(), searchrack_id))
            
            # Delete the item from SEARCHRACK
            cur.execute('DELETE FROM SEARCHRACK WHERE ID = ?', (searchrack_id,))
            
            # Remove from pending deletion
            cur.execute('DELETE FROM zero_qty_pending_deletion WHERE id = ?', (pending_id,))
            
            deleted_count += 1
            print(f"  ✓ Deleted item {searchrack_id} (marked at {marked_at})")
        
        conn.commit()
        
        if deleted_count > 0:
            print(f"✅ Deleted {deleted_count} zero-quantity items from searchRack")
    
    except Exception as e:
        print(f"❌ Error purging zero-quantity items: {e}")
        import traceback
        traceback.print_exc()
    finally:
        conn.close()

def _start_zero_qty_deleter_thread():
    """Start background thread to delete zero-quantity items after 24 hours"""
    global _zero_qty_deleter_started
    if _zero_qty_deleter_started:
        return
    _zero_qty_deleter_started = True
    
    def _runner():
        import time as _time
        # Run immediately on startup
        try:
            _purge_zero_qty_items()
        except Exception as e:
            print('Zero-qty deletion initial run error:', e)
        
        while True:
            # Check every hour
            _time.sleep(60*60)
            try:
                _purge_zero_qty_items()
            except Exception as e:
                print('Zero-qty deletion tick error:', e)
    
    t = threading.Thread(target=_runner, daemon=True)
    t.start()
    print("🚀 Zero-quantity deletion thread started")

def _process_automatic_inventory_removals():
    """Process sold orders that are past grace period and reduce inventory automatically"""
    from datetime import datetime, timedelta
    
    try:
        print("🔄 Checking for automatic inventory removals...")
        sold_conn = sqlite3.connect('sold.db')
        sold_conn.row_factory = sqlite3.Row
        sold_cur = sold_conn.cursor()
        
        # Get grace period setting
        sold_cur.execute("CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT)")
        sold_conn.commit()
        sold_cur.execute("SELECT value FROM settings WHERE key = 'removal_grace_hours'")
        row = sold_cur.fetchone()
        grace_period_hours = float(row[0]) if row and str(row[0]).strip() else 48
        
        print(f"   Grace period: {grace_period_hours} hours ({grace_period_hours * 60:.1f} minutes)")
        
        # Get orders eligible for automatic removal - MUST include shipped_time
        sold_cur.execute('''
            SELECT id, order_id, barcode, quantity, title, shipped_time
            FROM orders 
            WHERE rackupdated = 0 
            AND shipped_time IS NOT NULL 
            AND shipped_time != ''
            AND barcode IS NOT NULL
            AND barcode != ''
            AND COALESCE(removal_cancelled, 0) = 0
        ''')
        orders = sold_cur.fetchall()
        
        print(f"   Found {len(orders)} orders with shipped_time")
        
        now = datetime.now()
        
        searchrack_conn = sqlite3.connect('searchRack.db')
        searchrack_cur = searchrack_conn.cursor()
        
        processed_count = 0
        
        for order in orders:
            try:
                shipped_str = order['shipped_time']
                
                # Parse shipped time
                if 'T' in shipped_str:
                    if shipped_str.endswith('Z'):
                        shipped_dt = datetime.fromisoformat(shipped_str.replace('Z', '+00:00'))
                    else:
                        shipped_dt = datetime.fromisoformat(shipped_str)
                else:
                    shipped_dt = datetime.fromisoformat(shipped_str)
                
                if shipped_dt.tzinfo:
                    shipped_dt = shipped_dt.replace(tzinfo=None)
                
                hours_since_shipped = (now - shipped_dt).total_seconds() / 3600
                
                # Check if eligible for removal (past grace period)
                if hours_since_shipped < grace_period_hours:
                    continue
                
                # Get barcode and try to find in searchRack
                barcode = order['barcode']
                
                # Try original barcode first (searchRack may store without leading zeros)
                searchrack_cur.execute('SELECT ID, QUANTITY FROM SEARCHRACK WHERE BARCODE = ?', (barcode,))
                item_row = searchrack_cur.fetchone()
                
                # If not found, try with zero-padding (for items that have leading zeros)
                if not item_row:
                    barcode_padded = barcode.zfill(12) if barcode and barcode.isdigit() else barcode
                    searchrack_cur.execute('SELECT ID, QUANTITY FROM SEARCHRACK WHERE BARCODE = ?', (barcode_padded,))
                    item_row = searchrack_cur.fetchone()
                
                if not item_row:
                    # Item not found in searchRack, mark as handled anyway
                    sold_cur.execute('UPDATE orders SET rackupdated = 1 WHERE id = ?', (order['id'],))
                    print(f"  ℹ️  Order {order['order_id']}: Item not found in searchRack (barcode: {barcode})")
                    continue
                
                item_id, current_qty = item_row
                current_qty = current_qty or 0
                sold_qty = order['quantity'] or 1
                
                # Calculate new quantity (don't go below 0)
                new_qty = max(0, current_qty - sold_qty)
                
                # Update searchRack quantity
                searchrack_cur.execute('UPDATE SEARCHRACK SET QUANTITY = ? WHERE ID = ?', (new_qty, item_id))
                
                # Mark order as processed
                sold_cur.execute('UPDATE orders SET rackupdated = 1 WHERE id = ?', (order['id'],))
                
                # Log to rackhistory.db
                removed_conn = sqlite3.connect('rackhistory.db')
                try:
                    removed_cur = removed_conn.cursor()
                    _ensure_removed_items_table(removed_cur)
                
                    # Get location before removal
                    searchrack_cur.execute('SELECT ITEM_POSITION FROM SEARCHRACK WHERE ID = ?', (item_id,))
                    loc_row = searchrack_cur.fetchone()
                    item_location = loc_row[0] if loc_row else ''
                
                    removed_cur.execute('''
                    INSERT INTO removed_items 
                    (order_id, barcode, title, quantity_removed, removed_at, searchrack_id, old_quantity, new_quantity, removal_type, item_position)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''', (order['order_id'], barcode, order['title'], sold_qty, now.isoformat(), item_id, current_qty, new_qty, 'automatic', item_location))
                    removed_conn.commit()
                finally:
                    removed_conn.close()
                
                processed_count += 1
                
                # If item is now at 0 quantity, mark for deletion
                if new_qty == 0:
                    try:
                        # Get interval from settings
                        searchrack_cur.execute("SELECT value FROM zero_qty_settings WHERE key = 'interval_minutes'")
                        settings = searchrack_cur.fetchone()
                        interval = int(settings[0]) if settings else 60
                        
                        # Calculate deletion time
                        delete_at = datetime.now() + timedelta(minutes=interval)
                        
                        # Check if already marked (and not cancelled)
                        searchrack_cur.execute('SELECT id, deletion_cancelled FROM zero_qty_pending_deletion WHERE searchrack_id = ?', (item_id,))
                        existing = searchrack_cur.fetchone()
                        
                        if not existing:
                            # Mark for deletion
                            searchrack_cur.execute('''
                                INSERT INTO zero_qty_pending_deletion (searchrack_id, marked_at, delete_at, deletion_cancelled)
                                VALUES (?, ?, ?, 0)
                            ''', (item_id, datetime.now().isoformat(), delete_at.isoformat()))
                            print(f"  ✓ Order {order['order_id']}: Reduced {barcode} from {current_qty} to 0 → Marked for deletion in {interval} min")
                        elif existing[1] == 1:
                            # Entry exists but was cancelled - update it to re-enable
                            searchrack_cur.execute('''
                                UPDATE zero_qty_pending_deletion 
                                SET deletion_cancelled = 0, marked_at = ?, delete_at = ?
                                WHERE searchrack_id = ?
                            ''', (datetime.now().isoformat(), delete_at.isoformat(), item_id))
                            print(f"  ✓ Order {order['order_id']}: Reduced {barcode} from {current_qty} to 0 → Re-enabled deletion (was cancelled)")
                        else:
                            print(f"  ✓ Order {order['order_id']}: Reduced {barcode} from {current_qty} to 0 (already marked for deletion)")
                    except Exception as mark_error:
                        print(f"  ❌ Error marking item {item_id} for zero-qty deletion: {mark_error}")
                        import traceback
                        traceback.print_exc()
                else:
                    print(f"  ✓ Order {order['order_id']}: Reduced {barcode} from {current_qty} to {new_qty}")
                
            except Exception as e:
                print(f"  ❌ Error processing order {order['order_id']}: {e}")
                continue
        
        searchrack_conn.commit()
        
        sold_conn.commit()
        
        if processed_count > 0:
            print(f"✅ Automatically processed {processed_count} inventory removals")
    
    except Exception as e:
        print(f"❌ Error in automatic inventory removal: {e}")
        import traceback
        traceback.print_exc()
    finally:
        sold_conn.close()
        searchrack_conn.close()

def _start_automatic_removal_thread():
    """Start background thread to automatically process inventory removals after grace period"""
    global _automatic_removal_started
    if _automatic_removal_started:
        return
    _automatic_removal_started = True
    
    def _runner():
        import time as _time
        # Run immediately on startup
        try:
            _process_automatic_inventory_removals()
        except Exception as e:
            print('Automatic removal initial run error:', e)
        
        # Then run every 5 minutes
        while True:
            _time.sleep(5*60)
            try:
                _process_automatic_inventory_removals()
            except Exception as e:
                print('Automatic removal tick error:', e)
    
    t = threading.Thread(target=_runner, daemon=True)
    t.start()
    print("🚀 Automatic inventory removal thread started")

def _ensure_bol_list_status_column():
    """Ensure bol_items has list_status, temporary, and quantity tracking columns."""
    print("[_ensure_bol_list_status_column] Starting migration check...")
    try:
        conn = sqlite3.connect('bol.db')
        cur = conn.cursor()
        cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='bol_items'")
        if not cur.fetchone():
            print("[_ensure_bol_list_status_column] bol_items table not found")
            return
        cur.execute("PRAGMA table_info(bol_items)")
        # Use lowercase for case-insensitive comparison
        cols = [r[1].lower() for r in cur.fetchall()]
        print(f"[_ensure_bol_list_status_column] Found {len(cols)} columns in bol_items")
        
        # Add list_status and temporary columns
        if 'list_status' not in cols:
            print("[_ensure_bol_list_status_column] Adding list_status column")
            cur.execute("ALTER TABLE bol_items ADD COLUMN list_status TEXT")
        if 'temporary' not in cols:
            print("[_ensure_bol_list_status_column] Adding temporary column")
            cur.execute("ALTER TABLE bol_items ADD COLUMN temporary INTEGER DEFAULT 0")
        
        # NEW: Add quantity tracking columns for robust prep workflow
        if 'original_qty' not in cols:
            print("📊 Adding original_qty column and backfilling from quantity...")
            cur.execute("ALTER TABLE bol_items ADD COLUMN original_qty INTEGER")
            # Backfill from quantity column
            cur.execute("UPDATE bol_items SET original_qty = quantity WHERE original_qty IS NULL")
            
        if 'good_qty' not in cols:
            print("✅ Adding good_qty column...")
            cur.execute("ALTER TABLE bol_items ADD COLUMN good_qty INTEGER DEFAULT 0")
            # Backfill from items_prep_status for items already marked good
            cur.execute('''
                UPDATE bol_items 
                SET good_qty = (
                    SELECT COALESCE(quantity, 0) 
                    FROM items_prep_status 
                    WHERE items_prep_status.upc = bol_items.upc 
                    AND items_prep_status.status = 'good'
                )
                WHERE EXISTS (
                    SELECT 1 FROM items_prep_status 
                    WHERE items_prep_status.upc = bol_items.upc 
                    AND items_prep_status.status = 'good'
                )
            ''')
            
        if 'bad_qty' not in cols:
            print("❌ Adding bad_qty column...")
            cur.execute("ALTER TABLE bol_items ADD COLUMN bad_qty INTEGER DEFAULT 0")
            # Calculate bad_qty from suffixed entries
            cur.execute('''
                UPDATE bol_items 
                SET bad_qty = (
                    SELECT COALESCE(SUM(quantity), 0)
                    FROM bol_items AS suffixed
                    WHERE suffixed.upc LIKE bol_items.upc || '-%'
                    AND suffixed.temporary = 1
                )
                WHERE upc NOT LIKE '%-%'
            ''')
            
        if 'unchecked_qty' not in cols:
            print("📦 Adding unchecked_qty column and calculating from original - good - bad...")
            cur.execute("ALTER TABLE bol_items ADD COLUMN unchecked_qty INTEGER")
            # Backfill: original - good - bad = unchecked
            cur.execute('''
                UPDATE bol_items 
                SET unchecked_qty = COALESCE(original_qty, 0) - COALESCE(good_qty, 0) - COALESCE(bad_qty, 0)
                WHERE unchecked_qty IS NULL
            ''')
        
        # NEW: Add marketplace-specific listing columns with timestamps
        if 'listed_amazon' not in cols:
            print("🛒 Adding Amazon listing tracking columns...")
            cur.execute("ALTER TABLE bol_items ADD COLUMN listed_amazon INTEGER DEFAULT 0")
        else:
            print("[_ensure_bol_list_status_column] listed_amazon column already exists")
            
        if 'listed_amazon_date' not in cols:
            print("🛒 Adding Amazon listing date column...")
            cur.execute("ALTER TABLE bol_items ADD COLUMN listed_amazon_date TEXT")
        else:
            print("[_ensure_bol_list_status_column] listed_amazon_date column already exists")
            
        if 'listed_ebay' not in cols:
            print("🏷️ Adding eBay listing tracking columns...")
            cur.execute("ALTER TABLE bol_items ADD COLUMN listed_ebay INTEGER DEFAULT 0")
        else:
            print("[_ensure_bol_list_status_column] listed_ebay column already exists")
            
        if 'listed_ebay_date' not in cols:
            print("🏷️ Adding eBay listing date column...")
            cur.execute("ALTER TABLE bol_items ADD COLUMN listed_ebay_date TEXT")
        else:
            print("[_ensure_bol_list_status_column] listed_ebay_date column already exists")
            
        if 'listed_facebook' not in cols:
            print("📘 Adding Facebook listing tracking columns...")
            cur.execute("ALTER TABLE bol_items ADD COLUMN listed_facebook INTEGER DEFAULT 0")
        else:
            print("[_ensure_bol_list_status_column] listed_facebook column already exists")
            
        if 'listed_facebook_date' not in cols:
            print("📘 Adding Facebook listing date column...")
            cur.execute("ALTER TABLE bol_items ADD COLUMN listed_facebook_date TEXT")
        else:
            print("[_ensure_bol_list_status_column] listed_facebook_date column already exists")
        
        # One-time migration: convert existing list_status='listed' to listed_amazon=1
        # This runs even if columns already exist, but only for items that haven't been migrated
        print("[_ensure_bol_list_status_column] Running migration for list_status='listed' items...")
        try:
            # Use case-insensitive check for 'listed'
            cur.execute("""
                UPDATE bol_items 
                SET listed_amazon = 1, 
                    listed_amazon_date = datetime('now')
                WHERE (list_status = 'listed' OR list_status = 'Listed')
                AND (listed_amazon IS NULL OR listed_amazon = 0)
            """)
            migrated = cur.rowcount
            if migrated > 0:
                print(f"📦 Migrated {migrated} items from list_status='listed' to listed_amazon=1")
                # Force update data version if we migrated items
                try:
                    cur.execute('CREATE TABLE IF NOT EXISTS app_metadata (key TEXT PRIMARY KEY, value TEXT)')
                    cur.execute('INSERT OR REPLACE INTO app_metadata (key, value) VALUES (?, ?)', ('data_version', str(int(time.time()))))
                except Exception: pass
        except Exception as e:
            print(f"Warning: Could not run marketplace migration: {e}")
        
        conn.commit()
        # print("✅ Quantity tracking columns migrated successfully") # Reduce noise
    except Exception as e:
        print('Failed ensuring quantity columns in bol_items:', e)
        import traceback
        traceback.print_exc()
    finally:
        conn.close()

@app.route('/item-prep')
def item_prep_page():
    return render_template('item_prep.html')

@app.route('/item-prep/diagnostic')
def item_prep_diagnostic_page():
    upc_raw = (request.args.get('upc') or '').strip()
    # Strip leading zeros ONLY if it's all digits (preserve suffix like -24)
    if upc_raw and '-' in upc_raw:
        # Has suffix: strip zeros from base part only (e.g., '0719978859014-24' -> '719978859014-24')
        parts = upc_raw.split('-', 1)
        base = parts[0].lstrip('0') if parts[0].isdigit() else parts[0]
        upc = f"{base}-{parts[1]}"
    else:
        # No suffix: strip zeros normally
        upc = upc_raw.lstrip('0') if upc_raw.isdigit() else upc_raw
    
    # If coming from Item Manager with lot parameter, set it in session
    lot = request.args.get('lot', '').strip()
    if lot:
        session['selected_lot'] = lot
        print(f'[DEBUG] Set selected_lot in session: {lot}')
    
    print(f'[DEBUG] Diagnostic page - URL param: {upc_raw} -> Rendered UPC: {upc}')
    return render_template('item_prep_diagnostic.html', upc=upc)

@app.route('/item-prep/diagnostic/view')
def item_prep_diagnostic_view_page():
    upc_raw = request.args.get('upc', '').strip()
    # Strip leading zeros from barcode
    upc_stripped = upc_raw.lstrip('0') if upc_raw.isdigit() else upc_raw
    upc = _normalize_upc(upc_stripped)
    
    # If coming from Item Manager with lot parameter, set it in session
    lot = request.args.get('lot', '').strip()
    if lot:
        session['selected_lot'] = lot
        print(f'[DEBUG] Set selected_lot in session: {lot}')
    
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
        cur.execute('SELECT status, reason, note, updated_at, quantity FROM items_prep_status WHERE upc = ? COLLATE NOCASE', (upc,))
        row = cur.fetchone()
        status = dict(row) if row else None
        cur.execute("SELECT id, image_path, created_at FROM items_prep_images WHERE upc = ? COLLATE NOCASE AND (deleted_at IS NULL OR TRIM(COALESCE(deleted_at,'')) = '') ORDER BY created_at DESC, id DESC", (upc,))
        images = [dict(r) for r in cur.fetchall()]
        cur.execute('''
            SELECT id, upc, item_description, image_url, lot_number, bol_number, import_date, list_status, quantity,
                   listed_amazon, listed_amazon_date, listed_ebay, listed_ebay_date, listed_facebook, listed_facebook_date
            FROM bol_items 
            WHERE upc = ? COLLATE NOCASE 
            LIMIT 1
        ''', (upc,))
        b = cur.fetchone()
        bol = dict(b) if b else None
        
        # If item has prep status with quantity, use that instead of bol quantity
        if bol and status and status.get('quantity') is not None:
            bol['quantity'] = status['quantity']
        
    except Exception as e:
        print('Diagnostic view load error:', e)
    finally:
        conn.close()
    return render_template('item_prep_diagnostic_view.html', upc=upc, status=status, images=images, bol=bol)

@app.route('/item-prep-no-barcode')
def item_prep_no_barcode_page():
    """Page for searching items by name when there's no barcode"""
    return render_template('item_prep_no_barcode.html')

@app.route('/api/search-rawbol', methods=['POST'])
def search_rawbol_api():
    """Search rawbol.db by item description OR UPC with pagination"""
    try:
        data = request.get_json()
        query = data.get('query', '').strip()
        page = int(data.get('page', 1))
        per_page = 3  # Show only 3 items per page
        offset = (page - 1) * per_page
        
        if not query:
            return jsonify({'success': False, 'error': 'No search query provided'}), 400
        
        # Search rawbol.db
        conn = sqlite3.connect('rawbol.db')
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        
        # Check if query looks like a UPC (numeric)
        is_upc_query = query.isdigit()
        
        if is_upc_query:
            # Search by UPC (exact or partial match)
            search_pattern = f"%{query}%"
            
            # Get total count for pagination
            cur.execute('''
                SELECT COUNT(*) as total
                FROM raw_bol_items 
                WHERE upc LIKE ? COLLATE NOCASE
            ''', (search_pattern,))
            total = cur.fetchone()['total']
            
            # Get paginated results
            cur.execute('''
                SELECT upc, item_description, image_url 
                FROM raw_bol_items 
                WHERE upc LIKE ? COLLATE NOCASE
                LIMIT ? OFFSET ?
            ''', (search_pattern, per_page, offset))
        else:
            # Search by item description
            search_pattern = f"%{query}%"
            
            # Get total count for pagination
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
        return jsonify({'success': False, 'error': _safe_error(e)}), 500
    finally:
        conn.close()

# ============================================================================
# PRINTER ROUTES
# ============================================================================

@app.route('/printer-settings')
def printer_settings_page():
    """Printer configuration page"""
    return render_template('printer_settings.html')

@app.route('/api/printer/config', methods=['GET'])
@cache.cached(timeout=1800)  # Cache for 30 minutes (printer config rarely changes)
def get_printer_config():
    """Get current printer configuration"""
    try:
        conn = sqlite3.connect('bol.db')
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute('SELECT * FROM printer_config WHERE id = 1')
        config = cur.fetchone()
        
        if config:
            return jsonify({'success': True, 'config': dict(config)})
        else:
            return jsonify({'success': True, 'config': None})
    except Exception as e:
        return jsonify({'success': False, 'error': _safe_error(e)}), 500
    finally:
        conn.close()

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
            return jsonify({'success': False, 'error': 'Failed to save configuration'}), 500
    except Exception as e:
        return jsonify({'success': False, 'error': _safe_error(e)}), 500

@app.route('/api/printer/scan-bluetooth', methods=['POST'])
def scan_bluetooth_devices():
    """Scan for available Bluetooth devices"""
    try:
        devices = printer_manager.get_available_bluetooth_devices()
        return jsonify({'success': True, 'devices': devices})
    except Exception as e:
        return jsonify({'success': False, 'error': _safe_error(e)}), 500

@app.route('/api/printer/test', methods=['POST'])
def test_printer():
    """Test printer connection"""
    try:
        printer_manager.test_print()
        return jsonify({'success': True, 'message': 'Test print sent successfully'})
    except Exception as e:
        return jsonify({'success': False, 'error': _safe_error(e)}), 500

@app.route('/api/printer/print-barcode', methods=['POST'])
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
        return jsonify({'success': False, 'error': _safe_error(e)}), 500

@app.route('/api/printer/browser-print', methods=['POST'])
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
        return jsonify({'success': False, 'error': _safe_error(e)}), 500

@app.route('/api/printer/generate-barcode', methods=['POST'])
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
        return jsonify({'success': False, 'error': _safe_error(e)}), 500

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
        cur.execute('SELECT title, barcode, added_at FROM print_queue ORDER BY added_at ASC')
        rows = cur.fetchall()
        
        queue = [{'title': row[0], 'barcode': row[1], 'added_at': row[2]} for row in rows]
        return jsonify({'success': True, 'queue': queue})
    except Exception as e:
        return jsonify({'success': False, 'error': _safe_error(e)}), 500
    finally:
        conn.close()

@app.route('/api/print-queue', methods=['POST'])
def add_to_print_queue():
    """Add an item to the print queue"""
    try:
        data = request.get_json()
        title = data.get('title', '').strip()
        barcode = data.get('barcode', '').strip()
        
        if not title or not barcode:
            return jsonify({'success': False, 'error': 'Title and barcode are required'}), 400
        
        _ensure_items_prep_tables()  # Ensure table exists
        conn = sqlite3.connect('bol.db')
        cur = conn.cursor()
        
        # Insert or ignore (prevent duplicates)
        cur.execute('INSERT OR IGNORE INTO print_queue (title, barcode) VALUES (?, ?)', (title, barcode))
        conn.commit()
        
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': _safe_error(e)}), 500
    finally:
        conn.close()

@app.route('/api/print-queue', methods=['DELETE'])
def clear_print_queue():
    """Clear all items from the print queue"""
    try:
        _ensure_items_prep_tables()  # Ensure table exists
        conn = sqlite3.connect('bol.db')
        cur = conn.cursor()
        cur.execute('DELETE FROM print_queue')
        conn.commit()
        
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': _safe_error(e)}), 500
    finally:
        conn.close()

@app.route('/api/print-queue/item', methods=['DELETE'])
def remove_from_print_queue():
    """Remove a specific item from the print queue"""
    try:
        data = request.get_json()
        barcode = data.get('barcode', '').strip()
        
        if not barcode:
            return jsonify({'success': False, 'error': 'Barcode is required'}), 400
        
        _ensure_items_prep_tables()  # Ensure table exists
        conn = sqlite3.connect('bol.db')
        cur = conn.cursor()
        cur.execute('DELETE FROM print_queue WHERE barcode = ?', (barcode,))
        conn.commit()
        
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': _safe_error(e)}), 500
    finally:
        conn.close()

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
                return jsonify({'success': True, 'barcode': new_barcode.strip()})
            
            # Barcode exists, increment and try again
            max_barcode = int(new_barcode)
            attempts += 1
        
        # If we exhausted attempts, return error
        return jsonify({'success': False, 'error': 'Could not generate unique barcode after 100 attempts'}), 500
        
    except Exception as e:
        print(f'Error generating barcode: {e}')
        return jsonify({'success': False, 'error': _safe_error(e)}), 500
    finally:
        conn.close()

@app.route('/api/items-prep/temp-item', methods=['POST'])
def save_temp_item():
    """Save custom item directly to bol_items so it appears in Item Manager"""
    try:
        data = request.get_json()
        upc = data.get('upc', '').strip()
        item_description = data.get('item_description', '').strip()
        image_data = data.get('image_data', '')
        
        if not upc or not item_description or not image_data:
            return jsonify({'success': False, 'error': 'Missing required fields'}), 400
        
        # Normalize UPC for consistent storage and caching
        upc_norm = _normalize_upc(upc)
        
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
        
        # Check if item already exists
        cur.execute('SELECT upc FROM bol_items WHERE upc = ? COLLATE NOCASE', (upc_norm,))
        exists = cur.fetchone()
        
        web_image_path = f"/static/custom_items/{image_filename}"
        
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
        cache.delete(cache_key)
        
        return jsonify({'success': True, 'message': 'Custom item saved to inventory', 'image_url': web_image_path})
    except Exception as e:
        print(f'Error saving custom item: {e}')
        return jsonify({'success': False, 'error': _safe_error(e)}), 500
    finally:
        conn.close()

 

@app.route('/api/clear_cache', methods=['GET'])
def api_clear_cache():
    """Clear the cache for a specific UPC lookup to force refresh"""
    try:
        upc = request.args.get('upc')
        if upc:
            # Clear specific cache key for this UPC
            cache_key = f"view//api/bol_lookup?upc={upc}"
            cache.delete(cache_key)
        else:
            # Clear all bol_lookup cache
            cache.delete_memoized(api_bol_lookup)
        
        return jsonify({'success': True})
    except Exception as e:
        print(f"Cache clear error: {e}")
        return jsonify({'success': True})  # Always return success, cache clear is not critical

@app.route('/api/bol_lookup', methods=['GET'])
@cache.cached(timeout=600, query_string=True)  # Cache for 10 minutes
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
            return jsonify({'found': True, 'item': item})
        
        # Ensure temporary column exists in bol_items
        cur.execute('PRAGMA table_info(bol_items)')
        cols = [r[1] for r in cur.fetchall()]
        if 'temporary' not in cols:
            cur.execute('ALTER TABLE bol_items ADD COLUMN temporary INTEGER DEFAULT 0')
            conn.commit()
        
        # Check if this UPC already exists - ORDER BY import_date DESC to get newest LOT first
        cur.execute("""
            SELECT id, upc, item_description, image_url, lot_number, bol_number, import_date, temporary 
            FROM bol_items 
            WHERE upc = ? COLLATE NOCASE 
            ORDER BY import_date DESC, id DESC
        """, (upc,))
        rows = cur.fetchall()
        
        if not rows:
            return jsonify({'found': False})
        
        # Find the newest permanent entry (temporary = 0 or NULL)
        # Since rows are sorted by import_date DESC, the first permanent row is the newest LOT
        permanent_row = None
        for r in rows:
            if not r['temporary']:
                permanent_row = r
                break
        
        if not permanent_row:
            # All rows are temporary (shouldn't happen, but handle gracefully)
            permanent_row = rows[0]
        
        # Check if this item has already been prepped (exists in items_prep_status table)
        # Check for both exact match AND any suffixed versions (e.g., 719978859014, 719978859014-1, etc.)
        has_prep_record = False
        if permanent_row:
            _ensure_items_prep_tables()
            # Check for exact match OR any entry starting with "upc-"
            cur.execute('SELECT status FROM items_prep_status WHERE upc = ? OR upc LIKE ? COLLATE NOCASE LIMIT 1', (upc, f'{upc}-%'))
            status_row = cur.fetchone()
            has_prep_record = status_row is not None
            print(f'[DEBUG] BOL lookup for {upc}: has_prep_record={has_prep_record}, status={status_row["status"] if status_row else None}')
            print(f'[DEBUG] Using NEWEST LOT: lot_number={permanent_row["lot_number"]}, import_date={permanent_row["import_date"]}')
        
        # Only create a suffixed entry if the item already has a prep record
        # BUT: Skip if this UPC itself is already a suffixed entry in items_prep_status (to prevent double suffixes like 35886326470-3-1)
        is_suffixed_prep_entry = False
        if '-' in str(upc):
            # Check if this specific suffixed UPC exists in items_prep_status
            cur.execute('SELECT upc FROM items_prep_status WHERE upc = ? COLLATE NOCASE', (upc,))
            is_suffixed_prep_entry = cur.fetchone() is not None
            if is_suffixed_prep_entry:
                print(f'[DEBUG] Skipping suffix creation - {upc} is already a suffixed prep entry')
        
        if permanent_row and has_prep_record and not is_suffixed_prep_entry:
            # Find the next available suffix - check both bol_items and prep tables to avoid reusing deleted suffixes
            suffix = 1
            while True:
                suffixed_upc = f"{upc}-{suffix}"
                
                # Check bol_items
                cur.execute("SELECT upc FROM bol_items WHERE upc = ? COLLATE NOCASE", (suffixed_upc,))
                if cur.fetchone():
                    suffix += 1
                    continue
                
                # Check prep data to avoid reusing deleted suffixes with orphaned data
                cur.execute('SELECT upc FROM items_prep_images WHERE upc = ? COLLATE NOCASE LIMIT 1', (suffixed_upc,))
                if cur.fetchone():
                    suffix += 1
                    continue
                
                cur.execute('SELECT upc FROM items_prep_status WHERE upc = ? COLLATE NOCASE LIMIT 1', (suffixed_upc,))
                if cur.fetchone():
                    suffix += 1
                    continue
                
                try:
                    cur.execute('SELECT upc FROM items_prep_notes WHERE upc = ? COLLATE NOCASE LIMIT 1', (suffixed_upc,))
                    if cur.fetchone():
                        suffix += 1
                        continue
                except Exception:
                    pass
                
                # Suffix is clean
                break
            
            # Create temporary duplicate entry using the NEWEST LOT data
            print(f'[DEBUG] Creating suffixed entry: {suffixed_upc} from NEWEST LOT')
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
            # Return the existing NEWEST LOT row (no suffix needed)
            item = dict(permanent_row)
            item['is_duplicate'] = False
        
        
        # Also include current prep status if exists
        try:
            _ensure_items_prep_tables()
            conn2 = sqlite3.connect('bol.db')
            conn2.row_factory = sqlite3.Row
            cur2 = conn2.cursor()
            cur2.execute('SELECT status, reason, note, updated_at FROM items_prep_status WHERE upc = ? COLLATE NOCASE', (item['upc'],))
            srow = cur2.fetchone()
            if srow:
                item['prep_status'] = dict(srow)
        except Exception:
            item['prep_status'] = None
        finally:
            conn2.close()

        return jsonify({'found': True, 'item': item})
    except Exception as e:
        return jsonify({'found': False, 'error': _safe_error(e)}), 500
    finally:
        conn.close()

@app.route('/api/items_prep/status/<upc>', methods=['GET'])
def api_items_prep_status_get(upc):
    """Get preparation status for a UPC."""
    try:
        upc_norm = _normalize_upc(upc)
        # Strip leading zeros to match item manager behavior
        upc_n = upc_norm.lstrip('0') if upc_norm and upc_norm.isdigit() else upc_norm
        _ensure_items_prep_tables()
        conn = sqlite3.connect('bol.db')
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute('SELECT status, reason, note, updated_at FROM items_prep_status WHERE upc = ? COLLATE NOCASE', (upc_n,))
        row = cur.fetchone()
        if row:
            return jsonify({'success': True, 'status': row['status'], 'reason': row['reason'], 'note': row['note'], 'updated_at': row['updated_at']})
        else:
            return jsonify({'success': True, 'status': 'unchecked', 'reason': None, 'note': None, 'updated_at': None})
    except Exception as e:
        return jsonify({'success': False, 'error': _safe_error(e)}), 500
    finally:
        conn.close()

@app.route('/api/items_prep/status', methods=['POST'])
def api_items_prep_status():
    """Upsert preparation status for a UPC.
    
    NEW LOGIC:
    - GOOD flow: Always uses base UPC, increments qty in items_prep_status, decrements base qty in bol_items
    - BAD flow: UPC should already be suffixed (created by Bad button), just update status
    - Base qty is only decremented when status is finalized (Good immediately, Bad on Complete)
    """
    try:
        data = request.get_json() or {}
        upc = _normalize_upc(data.get('upc'))
        status = (data.get('status') or '').strip().lower()
        reason = (data.get('reason') or '').strip()
        note = (data.get('note') or '').strip()
        qty = int(data.get('qty', 1))
        
        if not upc or status not in ('good', 'bad', 'unchecked'):
            return jsonify({'success': False, 'error': 'Missing upc or invalid status'}), 400
        
        # Strip leading zeros but preserve suffix
        if upc and '-' in upc:
            parts = upc.split('-', 1)
            base = parts[0].lstrip('0') if parts[0].isdigit() else parts[0]
            upc = f"{base}-{parts[1]}"
        else:
            upc = upc.lstrip('0') if upc and upc.isdigit() else upc
        
        base_upc = upc.split('-')[0] if '-' in upc else upc
        
        print(f'[STATUS API] Received UPC: {data.get("upc")}, Normalized: {upc}, Base: {base_upc}, Status: {status}')
        print(f'[STATUS API] UPC has suffix: {upc != base_upc}')
        print(f'[STATUS API] Reason: "{reason}", Note: "{note}", Qty: {qty}')
        
        conn = sqlite3.connect('bol.db', isolation_level='IMMEDIATE')
        cur = conn.cursor()
        _ensure_items_prep_tables()
        
        import datetime
        ts = datetime.datetime.now(datetime.UTC).isoformat()
        
        # GOOD flow - always uses base UPC, tracks quantities explicitly
        if status == 'good':
            # GOOD items cannot have defect reasons
            if reason:
                return jsonify({'success': False, 'error': 'GOOD items cannot have defect reasons. Please clear the defect field or select BAD status.'}), 400
            
            # Get selected LOT from session (used to set lot_number in bol_items for item-manager)
            selected_lot = session.get('selected_lot')
            print(f'[STATUS API] Selected LOT from session: {selected_lot}')
            
            # If no LOT selected, try to find the item's existing LOT or use a default
            if not selected_lot:
                cur.execute('SELECT lot_number FROM bol_items WHERE upc = ? COLLATE NOCASE AND (itemprepped IS NULL OR itemprepped = 0) LIMIT 1', (base_upc,))
                lot_row = cur.fetchone()
                if lot_row and lot_row[0]:
                    selected_lot = lot_row[0]
                    print(f'[STATUS API] No LOT in session, using existing LOT from bol_items: {selected_lot}')
                else:
                    print('[STATUS API] Warning: No LOT selected and item has no LOT in bol_items, proceeding without LOT')
                    selected_lot = None
            
            # Check if the incoming UPC itself is a suffixed BAD entry
            suffixed_upc_found = None
            suffixed_reason = None
            
            if upc != base_upc:
                # UPC is already suffixed, check if it's a BAD entry
                cur.execute('SELECT status, reason FROM items_prep_status WHERE upc = ? COLLATE NOCASE', (upc,))
                row = cur.fetchone()
                print(f'[GOOD] Checking suffixed UPC {upc} in items_prep_status: {row}')
                if row and row[0] == 'bad':
                    suffixed_upc_found = upc
                    suffixed_reason = (row[1] or '').strip()
                    print(f'[GOOD] Found suffixed BAD entry directly: {suffixed_upc_found}')
                elif row:
                    # Entry exists but is not BAD - this could be from the initial item-prep flow
                    # where we clicked Bad button and are now changing to Good in diagnostic
                    print(f'[GOOD] Found suffixed entry {upc} with status={row[0]} (not bad, treating as BAD→GOOD conversion)')
                    suffixed_upc_found = upc
                    suffixed_reason = (row[1] or '').strip()
                else:
                    # No items_prep_status entry yet - check if this is a bad entry in bol_items
                    # (created by Bad button but diagnostic not completed yet)
                    cur.execute('SELECT bad_qty, original_qty FROM bol_items WHERE upc = ? COLLATE NOCASE', (upc,))
                    bol_row = cur.fetchone()
                    print(f'[GOOD] No prep_status entry, checking bol_items for {upc}: {bol_row}')
                    if bol_row and bol_row[0] and bol_row[0] > 0:
                        # This suffixed entry has bad_qty > 0, meaning it was created from Bad flow
                        suffixed_upc_found = upc
                        suffixed_reason = ''
                        print(f'[GOOD] Found suffixed BAD entry in bol_items (no prep_status yet): {suffixed_upc_found}')
            
            # If not found yet, search for suffixed entries starting from base_upc
            if not suffixed_upc_found:
                suffix_num = 1
                while True:
                    test_suffixed = f"{base_upc}-{suffix_num}"
                    cur.execute('SELECT status, reason FROM items_prep_status WHERE upc = ? COLLATE NOCASE', (test_suffixed,))
                    row = cur.fetchone()
                    if row and row[0] == 'bad':
                        suffixed_upc_found = test_suffixed
                        suffixed_reason = (row[1] or '').strip()
                        print(f'[GOOD] Found suffixed BAD entry by search: {suffixed_upc_found}')
                        break
                    elif not row:
                        # No more suffixes exist
                        break
                    suffix_num += 1
            
            # If we found a BAD suffixed entry, this is a BAD→GOOD conversion
            if suffixed_upc_found:
                print(f'[GOOD] Converting BAD item {suffixed_upc_found} to GOOD')
                
                # Keep the suffixed entry but change status to GOOD (don't merge back to base)
                # This preserves any notes or distinguishing information added during BAD flow
                
                # UPSERT items_prep_status (might not exist yet if diagnostic not completed)
                cur.execute('SELECT upc FROM items_prep_status WHERE upc = ? COLLATE NOCASE', (suffixed_upc_found,))
                if cur.fetchone():
                    # Update existing entry
                    cur.execute('UPDATE items_prep_status SET status = ?, reason = ?, note = ?, updated_at = ?, quantity = ? WHERE upc = ? COLLATE NOCASE',
                              ('good', reason or '', note, ts, qty, suffixed_upc_found))
                    print(f'[GOOD] Updated items_prep_status {suffixed_upc_found}: status=good, reason="{reason or ""}"')
                else:
                    # Insert new entry
                    cur.execute('INSERT INTO items_prep_status (upc, status, reason, note, updated_at, quantity) VALUES (?, ?, ?, ?, ?, ?)',
                              (suffixed_upc_found, 'good', reason or '', note, ts, qty))
                    print(f'[GOOD] Inserted items_prep_status {suffixed_upc_found}: status=good, reason="{reason or ""}"')
                
                # Update bol_items quantities: move from bad_qty to good_qty
                cur.execute('SELECT bad_qty, good_qty FROM bol_items WHERE upc = ? COLLATE NOCASE', (suffixed_upc_found,))
                bol_row = cur.fetchone()
                if bol_row:
                    current_bad = bol_row[0] or 0
                    current_good = bol_row[1] or 0
                    # Move all bad_qty to good_qty
                    new_bad = 0
                    new_good = current_good + current_bad
                    cur.execute('UPDATE bol_items SET bad_qty = ?, good_qty = ?, temporary = 0 WHERE upc = ? COLLATE NOCASE',
                              (new_bad, new_good, suffixed_upc_found))
                    print(f'[GOOD] Updated bol_items {suffixed_upc_found}: bad_qty {current_bad}→{new_bad}, good_qty {current_good}→{new_good}')
                    
                    # Also update base UPC: decrement bad_qty
                    cur.execute('SELECT bad_qty FROM bol_items WHERE upc = ? COLLATE NOCASE', (base_upc,))
                    base_row = cur.fetchone()
                    if base_row:
                        base_bad = base_row[0] or 0
                        new_base_bad = max(0, base_bad - current_bad)
                        cur.execute('UPDATE bol_items SET bad_qty = ? WHERE upc = ? COLLATE NOCASE',
                                  (new_base_bad, base_upc))
                        print(f'[GOOD] Updated base {base_upc}: bad_qty {base_bad}→{new_base_bad}')
                
                conn.commit()
                
                # Update data version for cache invalidation
                update_data_version()

                return jsonify({
                    'success': True, 
                    'action': 'converted_bad_to_good_kept_suffix',
                    'upc': suffixed_upc_found,
                    'lot_number': selected_lot
                })
            
            # Not a BAD→GOOD conversion - proceed with normal GOOD flow for base UPC
            # Update items_prep_status for the base UPC
            # Check if ANY entry exists for this UPC (regardless of status)
            cur.execute('SELECT status, quantity FROM items_prep_status WHERE upc = ? COLLATE NOCASE', (base_upc,))
            existing_prep_row = cur.fetchone()
            if existing_prep_row:
                existing_status = existing_prep_row[0]
                existing_qty = existing_prep_row[1] if existing_prep_row[1] is not None else 0
                
                if existing_status == 'good':
                    # Add to existing GOOD quantity
                    total_prep_good = existing_qty + qty
                    cur.execute('UPDATE items_prep_status SET quantity = ?, reason = ?, note = ?, updated_at = ? WHERE upc = ? COLLATE NOCASE',
                               (total_prep_good, reason, note, ts, base_upc))
                    print(f'[GOOD] Updated items_prep_status {base_upc}: qty {existing_qty}→{total_prep_good}')
                else:
                    # Replace existing status (was unchecked/bad) with GOOD
                    cur.execute('UPDATE items_prep_status SET status = ?, quantity = ?, reason = ?, note = ?, updated_at = ? WHERE upc = ? COLLATE NOCASE',
                               ('good', qty, reason, note, ts, base_upc))
                    print(f'[GOOD] Changed items_prep_status {base_upc} from {existing_status} to good, qty={qty}')
            else:
                # No existing entry, insert new GOOD status
                cur.execute('INSERT INTO items_prep_status (upc, status, reason, note, quantity, updated_at) VALUES (?,?,?,?,?,?)',
                           (base_upc, 'good', reason, note, qty, ts))
                print(f'[GOOD] Created items_prep_status {base_upc} with status=good, qty={qty}')
            
            # Update bol_items good_qty
            cur.execute('SELECT good_qty FROM bol_items WHERE upc = ? COLLATE NOCASE', (base_upc,))
            bol_row = cur.fetchone()
            if bol_row:
                current_good = bol_row[0] or 0
                new_good = current_good + qty
                # Update good_qty and set lot_number if we have a selected_lot
                if selected_lot:
                    cur.execute('UPDATE bol_items SET good_qty = ?, lot_number = ? WHERE upc = ? COLLATE NOCASE', (new_good, selected_lot, base_upc))
                    print(f'[GOOD] Updated bol_items {base_upc}: good_qty {current_good}→{new_good}, lot_number={selected_lot}')
                else:
                    cur.execute('UPDATE bol_items SET good_qty = ? WHERE upc = ? COLLATE NOCASE', (new_good, base_upc))
                    print(f'[GOOD] Updated bol_items {base_upc}: good_qty {current_good}→{new_good} (no LOT update)')
            else:
                print(f'[GOOD] WARNING: Could not find bol_items entry for {base_upc} to update good_qty')

            
            conn.commit()
            print(f'[GOOD] Transaction committed for {base_upc}')
            
            # Update data version for cache invalidation
            update_data_version()

            return jsonify({
                'success': True, 
                'action': 'converted_to_good' if suffixed_upc_found else 'saved_good',
                'upc': base_upc,
                'lot_number': selected_lot
            })
        
        # BAD/UNCHECKED flow - upc should already be suffixed if coming from Bad button
        # Just upsert the status (no qty changes here, that happens in diagnostic Complete)
        
        # Special case: If changing GOOD (base UPC) to BAD, need to create a suffixed entry
        # Check if we're working with a base UPC (no suffix) - this is a GOOD->BAD conversion
        if status == 'bad' and '-' not in str(upc):
            # This is a GOOD item (base UPC) being changed to BAD
            print(f'[BAD] Converting GOOD item {base_upc} to BAD')
            
            # Get the LOT from session (set by Item Manager)
            selected_lot = session.get('selected_lot')
            if not selected_lot:
                return jsonify({'success': False, 'error': 'No LOT selected for BAD conversion'}), 400
            
            # Get the bol_items entry to decrement its quantity
            cur.execute('''
                SELECT id, quantity
                FROM bol_items 
                WHERE upc = ? COLLATE NOCASE 
                AND lot_number = ? COLLATE NOCASE
                AND (itemprepped IS NULL OR itemprepped = 0)
                LIMIT 1
            ''', (base_upc, selected_lot))
            
            bol_row = cur.fetchone()
            if bol_row:
                bol_id, current_bol_qty = bol_row
                current_bol_qty = current_bol_qty or 1
                
                # Decrement bol_items quantity by 1
                if current_bol_qty > 1:
                    new_bol_qty = current_bol_qty - 1
                    cur.execute('UPDATE bol_items SET quantity = ? WHERE id = ?', (new_bol_qty, bol_id))
                    print(f'[BAD] Decremented bol_items quantity for {base_upc} from {current_bol_qty} to {new_bol_qty}')
                # If qty=1, leave it at 1 so the GOOD item stays visible with qty=1
            
            # Check if the GOOD item exists in items_prep_status
            # If it doesn't exist, we need to create it with the remaining quantity
            cur.execute('SELECT quantity FROM items_prep_status WHERE upc = ? COLLATE NOCASE', (base_upc,))
            good_row = cur.fetchone()
            
            if good_row:
                # Entry exists in items_prep_status
                good_qty = good_row[0] if good_row[0] is not None else 1
                
                # Check if we actually decremented bol_items (meaning original qty > 1)
                if bol_row and current_bol_qty > 1:
                    # Original had multiple units - keep remaining as GOOD
                    # Use bol_items quantity as source of truth for remaining GOOD units
                    remaining_qty = new_bol_qty  # This is current_bol_qty - 1
                    cur.execute('UPDATE items_prep_status SET quantity = ? WHERE upc = ? COLLATE NOCASE', 
                              (remaining_qty, base_upc))
                    print(f'[BAD] Set GOOD status qty for {base_upc} to {remaining_qty} (bol_items was decremented)')
                else:
                    # Original qty was 1 - all units now BAD, set to unchecked
                    cur.execute('UPDATE items_prep_status SET status = ?, quantity = ? WHERE upc = ? COLLATE NOCASE', 
                              ('unchecked', 1, base_upc))
                    print(f'[BAD] Set {base_upc} to unchecked (original bol_items qty was 1)')
            else:
                # No entry in items_prep_status - create one with remaining bol_items quantity
                # After decrement, bol_items now has the remaining GOOD quantity
                if bol_row and current_bol_qty > 1:
                    # We decremented from 2+ to at least 1
                    remaining_qty = new_bol_qty  # This is current_bol_qty - 1
                    cur.execute('INSERT INTO items_prep_status (upc, status, reason, note, quantity, updated_at) VALUES (?,?,?,?,?,?)', 
                              (base_upc, 'good', '', '', remaining_qty, ts))
                    print(f'[BAD] Created GOOD status entry {base_upc} with qty={remaining_qty}')
                else:
                    # We had qty=1, didn't decrement, so set to unchecked (all units now BAD)
                    cur.execute('INSERT INTO items_prep_status (upc, status, reason, note, quantity, updated_at) VALUES (?,?,?,?,?,?)', 
                              (base_upc, 'unchecked', '', '', 1, ts))
                    print(f'[BAD] Created unchecked status entry {base_upc} (original qty was 1)')
            
            # Find next available suffix (check items_prep_status only)
            # We do this BEFORE commit to ensure atomicity
            suffix_num = 1
            max_suffix_attempts = 50
            suffixed_upc = None
            
            for attempt in range(max_suffix_attempts):
                test_suffixed = f"{base_upc}-{suffix_num}"
                cur.execute('SELECT upc FROM items_prep_status WHERE upc = ? COLLATE NOCASE', (test_suffixed,))
                if not cur.fetchone():
                    suffixed_upc = test_suffixed
                    print(f'[BAD] Found available suffix: {suffixed_upc}')
                    break
                suffix_num += 1
            
            if not suffixed_upc:
                conn.rollback()
                return jsonify({'success': False, 'error': f'Could not find available suffix after {max_suffix_attempts} attempts'}), 500
            
            # Create new suffixed entry (BAD) with quantity=1
            try:
                cur.execute('INSERT INTO items_prep_status (upc, status, reason, note, quantity, updated_at) VALUES (?,?,?,?,?,?)', 
                          (suffixed_upc, status, reason, note, 1, ts))
                print(f'[BAD] Created BAD status entry {suffixed_upc} with qty=1')
            except Exception as e:
                # If insert fails, rollback everything and return error
                print(f'[BAD] Failed to create {suffixed_upc}. Error: {e}')
                conn.rollback()
                return jsonify({'success': False, 'error': _safe_error(e, 'Failed to create BAD entry')}), 500
            
            # Commit all changes atomically
            conn.commit()

            # Update data version for cache invalidation
            update_data_version()

            return jsonify({'success': True, 'upc': suffixed_upc, 'action': 'converted_to_bad', 'quantity': 1})
        
        # Normal BAD/UNCHECKED flow - UPC already has suffix or is being updated
        # Just update the existing entry directly
        cur.execute('SELECT upc FROM items_prep_status WHERE upc = ? COLLATE NOCASE', (upc,))
        if cur.fetchone():
            cur.execute('UPDATE items_prep_status SET status=?, reason=?, note=?, updated_at=? WHERE upc=? COLLATE NOCASE', 
                      (status, reason, note, ts, upc))
            print(f'[{status.upper()}] Updated existing entry {upc}')
        else:
            cur.execute('INSERT INTO items_prep_status (upc, status, reason, note, updated_at) VALUES (?,?,?,?,?)', 
                      (upc, status, reason, note, ts))
            print(f'[{status.upper()}] Created new entry {upc}')
        conn.commit()
        

        # Update data version for cache invalidation
        update_data_version()

        return jsonify({'success': True, 'upc': upc, 'action': 'updated', 'quantity': qty})
        
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': _safe_error(e)}), 500
    finally:
        conn.close()

@app.route('/api/items_prep/allocate_lots', methods=['POST'])
def api_items_prep_allocate_lots():
    """Handle multi-LOT good marking with qty distribution across LOTs.
    JSON: { upc, allocations: [{lot_id, qty}], reason, note }
    """
    try:
        data = request.get_json() or {}
        upc = _normalize_upc(data.get('upc'))
        allocations = data.get('allocations', [])  # [{lot_id, qty}, ...]
        reason = (data.get('reason') or '').strip()
        note = (data.get('note') or '').strip()
        
        if not upc or not allocations:
            return jsonify({'success': False, 'error': 'Missing upc or allocations'}), 400
        
        # Strip leading zeros
        base_upc = upc.lstrip('0') if upc and upc.isdigit() else upc
        
        conn = sqlite3.connect('bol.db')
        cur = conn.cursor()
        _ensure_items_prep_tables()
        
        import datetime
        ts = datetime.datetime.now(datetime.UTC).isoformat()
        
        total_qty_allocated = 0
        results = []
        
        # Process each LOT allocation
        for alloc in allocations:
            lot_id = alloc.get('id')
            qty = int(alloc.get('qty', 0))
            
            if qty <= 0:
                continue
            
            # Get LOT details and validate
            cur.execute('''
                SELECT lot_number, good_qty, unchecked_qty, original_qty 
                FROM bol_items 
                WHERE id = ? AND upc = ? COLLATE NOCASE
            ''', (lot_id, base_upc))
            
            lot_row = cur.fetchone()
            if not lot_row:
                return jsonify({'success': False, 'error': f'LOT with id {lot_id} not found'}), 404
            
            lot_number, current_good, current_unchecked, original_qty = lot_row
            current_good = current_good or 0
            current_unchecked = current_unchecked or 0
            original_qty = original_qty or 0
            
            # Validate sufficient unchecked qty
            if qty > current_unchecked:
                return jsonify({
                    'success': False,
                    'error': f'LOT {lot_number}: Cannot mark {qty} as good - only {current_unchecked} unchecked'
                }), 400
            
            # Update quantities for this LOT
            new_good = current_good + qty
            new_unchecked = current_unchecked - qty
            
            cur.execute('''
                UPDATE bol_items 
                SET good_qty = ?, unchecked_qty = ?, quantity = ?
                WHERE id = ?
            ''', (new_good, new_unchecked, new_good, lot_id))
            
            total_qty_allocated += qty
            results.append({
                'lot_number': lot_number,
                'qty': qty,
                'good_qty': new_good,
                'unchecked_qty': new_unchecked
            })
            
            print(f'[GOOD-LOT] {base_upc} LOT {lot_number}: moved {qty} from unchecked to good (good: {current_good}→{new_good}, unchecked: {current_unchecked}→{new_unchecked})')
        
        # Update prep status with ONLY the quantity allocated in this request (not the LOT's total good_qty)
        # This prevents adding the entire LOT quantity when user only specifies a smaller qty
        cur.execute('SELECT quantity FROM items_prep_status WHERE upc = ? AND status = ? COLLATE NOCASE', (base_upc, 'good'))
        existing_row = cur.fetchone()
        existing_qty = existing_row[0] if existing_row else 0
        
        # Add only the quantity allocated in this request
        new_prep_qty = existing_qty + total_qty_allocated
        
        if existing_row:
            cur.execute('UPDATE items_prep_status SET quantity = ?, updated_at = ? WHERE upc = ? AND status = ? COLLATE NOCASE',
                       (new_prep_qty, ts, base_upc, 'good'))
        else:
            cur.execute('INSERT INTO items_prep_status (upc, status, reason, note, quantity, updated_at) VALUES (?,?,?,?,?,?)',
                       (base_upc, 'good', reason, note, new_prep_qty, ts))
        
        conn.commit()
        
        return jsonify({
            'success': True,
            'upc': base_upc,
            'total_qty': total_qty_allocated,
            'total_good': new_prep_qty,
            'lots_updated': results
        })
        
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': _safe_error(e)}), 500
    finally:
        conn.close()

@app.route('/api/items_prep/create_bad_entry', methods=['POST'])
def api_items_prep_create_bad_entry():
    """Create a temporary suffixed entry for Bad flow.
    This creates the suffix and temporary bol_items entry, but does NOT decrement base qty yet.
    Base qty will be decremented when diagnostic is completed.
    JSON: { upc, qty }
    Returns: { success, suffixed_upc }
    """
    try:
        data = request.get_json() or {}
        upc = _normalize_upc(data.get('upc'))
        qty = int(data.get('qty', 1))
        
        if not upc:
            return jsonify({'success': False, 'error': 'Missing upc'}), 400
        
        # Strip leading zeros
        if upc and '-' in upc:
            parts = upc.split('-', 1)
            base = parts[0].lstrip('0') if parts[0].isdigit() else parts[0]
            upc = f"{base}-{parts[1]}"
        else:
            upc = upc.lstrip('0') if upc and upc.isdigit() else upc
        
        base_upc = upc.split('-')[0] if '-' in upc else upc
        
        conn = sqlite3.connect('bol.db')
        cur = conn.cursor()
        
        # Find next available suffix for bad items
        # Check BOTH bol_items AND prep tables to avoid reusing deleted suffixes with orphaned data
        suffix_num = 1
        while True:
            suffixed_upc = f"{base_upc}-{suffix_num}"
            
            # Check if exists in bol_items
            cur.execute('SELECT upc FROM bol_items WHERE upc = ? COLLATE NOCASE', (suffixed_upc,))
            if cur.fetchone():
                suffix_num += 1
                continue
            
            # Also check if prep data exists (images, status, notes) to avoid reusing old suffixes
            cur.execute('SELECT upc FROM items_prep_images WHERE upc = ? COLLATE NOCASE LIMIT 1', (suffixed_upc,))
            if cur.fetchone():
                suffix_num += 1
                continue
            
            cur.execute('SELECT upc FROM items_prep_status WHERE upc = ? COLLATE NOCASE LIMIT 1', (suffixed_upc,))
            if cur.fetchone():
                suffix_num += 1
                continue
            
            try:
                cur.execute('SELECT upc FROM items_prep_notes WHERE upc = ? COLLATE NOCASE LIMIT 1', (suffixed_upc,))
                if cur.fetchone():
                    suffix_num += 1
                    continue
            except Exception:
                pass
            
            # Suffix is clean - no bol_items entry and no orphaned prep data
            break
        
        # Get base item details to copy and check unchecked quantity
        cur.execute('''
            SELECT item_description, image_url, lot_number, bol_number, unchecked_qty, bad_qty, original_qty 
            FROM bol_items WHERE upc = ? COLLATE NOCASE
        ''', (base_upc,))
        base_item = cur.fetchone()
        
        if not base_item:
            return jsonify({'success': False, 'error': f'Base UPC {base_upc} not found in bol_items'}), 404
        
        unchecked = base_item[4] or 0
        current_bad = base_item[5] or 0
        original_qty = base_item[6] or 0
        
        # Create temporary suffixed entry with new quantity columns
        import datetime
        import_date = datetime.datetime.now(datetime.UTC).isoformat()
        
        cur.execute('''
            INSERT INTO bol_items (
                upc, item_description, image_url, lot_number, bol_number, import_date, 
                temporary, original_qty, unchecked_qty, bad_qty, good_qty, quantity
            )
            VALUES (?, ?, ?, ?, ?, ?, 1, ?, 0, ?, 0, ?)
        ''', (suffixed_upc, base_item[0], base_item[1], base_item[2], base_item[3], import_date, 
              qty, qty, qty))  # original_qty=qty, bad_qty=qty, quantity=qty for this suffixed entry
        
        # Update base item: move qty from unchecked to bad
        new_unchecked = unchecked - qty
        new_bad = current_bad + qty
        
        cur.execute('''
            UPDATE bol_items 
            SET unchecked_qty = ?, bad_qty = ?
            WHERE upc = ? COLLATE NOCASE
        ''', (new_unchecked, new_bad, base_upc))
        
        conn.commit()
        
        print(f'[BAD] Created temporary suffixed entry: {suffixed_upc} (temporary=1, qty={qty})')
        print(f'[BAD] {base_upc}: moved {qty} from unchecked to bad (bad: {current_bad}→{new_bad}, unchecked: {unchecked}→{new_unchecked})')
        

        # Update data version for cache invalidation
        update_data_version()

        return jsonify({
            'success': True, 
            'suffixed_upc': suffixed_upc, 
            'base_upc': base_upc,
            'unchecked_qty': new_unchecked,
            'bad_qty': new_bad
        })
        
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': _safe_error(e)}), 500
    finally:
        conn.close()

@app.route('/api/items_prep/create_return_entry', methods=['POST'])
def api_items_prep_create_return_entry():
    """Create a suffixed entry with status 'good' and reason 'return' for items being returned to vendor.
    Similar to bad flow but marks as good with return defect.
    JSON: { upc, qty }
    Returns: { success, suffixed_upc }
    """
    try:
        data = request.get_json() or {}
        upc = _normalize_upc(data.get('upc'))
        qty = int(data.get('qty', 1))
        
        if not upc:
            return jsonify({'success': False, 'error': 'Missing upc'}), 400
        
        # Strip leading zeros
        if upc and '-' in upc:
            parts = upc.split('-', 1)
            base = parts[0].lstrip('0') if parts[0].isdigit() else parts[0]
            upc = f"{base}-{parts[1]}"
        else:
            upc = upc.lstrip('0') if upc and upc.isdigit() else upc
        
        base_upc = upc.split('-')[0] if '-' in upc else upc
        
        conn = sqlite3.connect('bol.db')
        cur = conn.cursor()
        _ensure_items_prep_tables()
        
        # Find next available suffix for return items
        suffix_num = 1
        while True:
            suffixed_upc = f"{base_upc}-{suffix_num}"
            
            # Check if exists in bol_items
            cur.execute('SELECT upc FROM bol_items WHERE upc = ? COLLATE NOCASE', (suffixed_upc,))
            if cur.fetchone():
                suffix_num += 1
                continue
            
            # Also check prep tables
            cur.execute('SELECT upc FROM items_prep_status WHERE upc = ? COLLATE NOCASE LIMIT 1', (suffixed_upc,))
            if cur.fetchone():
                suffix_num += 1
                continue
            
            cur.execute('SELECT upc FROM items_prep_images WHERE upc = ? COLLATE NOCASE LIMIT 1', (suffixed_upc,))
            if cur.fetchone():
                suffix_num += 1
                continue
            
            # Suffix is available
            break
        
        # Get base item details
        cur.execute('''
            SELECT item_description, image_url, lot_number, bol_number
            FROM bol_items WHERE upc = ? COLLATE NOCASE
        ''', (base_upc,))
        base_item = cur.fetchone()
        
        if not base_item:
            return jsonify({'success': False, 'error': f'Base UPC {base_upc} not found in bol_items'}), 404
        
        # Create suffixed entry in bol_items marked as temporary with good_qty
        import datetime
        import_date = datetime.datetime.now(datetime.UTC).isoformat()
        
        cur.execute('''
            INSERT INTO bol_items (
                upc, item_description, image_url, lot_number, bol_number, import_date, 
                temporary, original_qty, unchecked_qty, good_qty, bad_qty, quantity
            )
            VALUES (?, ?, ?, ?, ?, ?, 1, ?, 0, ?, 0, ?)
        ''', (suffixed_upc, base_item[0], base_item[1], base_item[2], base_item[3], import_date, 
              qty, qty, qty))  # original_qty=qty, good_qty=qty, quantity=qty for this suffixed entry
        
        # Create items_prep_status entry with status 'good' and reason 'return'
        ts = datetime.datetime.now(datetime.UTC).isoformat()
        cur.execute('''
            INSERT INTO items_prep_status (upc, status, reason, note, updated_at, quantity)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (suffixed_upc, 'good', 'return', '', ts, qty))
        
        conn.commit()
        
        print(f'[RETURN] Created suffixed return entry: {suffixed_upc} (status=good, reason=return, qty={qty})')
        

        # Update data version for cache invalidation
        update_data_version()

        return jsonify({
            'success': True, 
            'suffixed_upc': suffixed_upc, 
            'base_upc': base_upc
        })
        
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': _safe_error(e)}), 500
    finally:
        conn.close()

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
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': _safe_error(e)}), 500
    finally:
        conn.close()

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
        
        # Check if new quantity columns exist
        cur.execute('PRAGMA table_info(bol_items)')
        cols = [col[1].lower() for col in cur.fetchall()]
        has_new_cols = 'original_qty' in cols and 'unchecked_qty' in cols
        
        if has_new_cols:
            # Update both old quantity and unchecked_qty (assuming manual edits change unchecked items)
            cur.execute('''UPDATE bol_items 
                          SET quantity = ?, 
                              original_qty = ?,
                              unchecked_qty = ? - COALESCE(good_qty, 0) - COALESCE(bad_qty, 0)
                          WHERE upc = ? COLLATE NOCASE''', 
                       (quantity, quantity, quantity, upc))
        else:
            # Old behavior
            cur.execute('UPDATE bol_items SET quantity = ? WHERE upc = ? COLLATE NOCASE', (quantity, upc))
        
        # Also update items_prep_status.quantity if the item has been prepped (status exists)
        _ensure_items_prep_tables()
        cur.execute('SELECT status FROM items_prep_status WHERE upc = ? COLLATE NOCASE', (upc,))
        prep_row = cur.fetchone()
        if prep_row:
            # Item has prep status, update its prep quantity too
            cur.execute('UPDATE items_prep_status SET quantity = ? WHERE upc = ? COLLATE NOCASE', (quantity, upc))
        
        conn.commit()
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': _safe_error(e)}), 500
    finally:
        conn.close()

@app.route('/api/items_prep/status/<upc>', methods=['DELETE'])
def api_items_prep_status_delete(upc):
    """Delete preparation status for a UPC (for undo functionality).
    If UPC has a suffix (e.g., 110101-1), also delete the suffixed entry from bol_items.
    """
    try:
        upc_norm = _normalize_upc(upc)
        # Strip leading zeros to match item manager behavior (but preserve suffixes like -1)
        if upc_norm and '-' not in upc_norm and upc_norm.isdigit():
            upc = upc_norm.lstrip('0')
        else:
            upc = upc_norm
        if not upc:
            return jsonify({'success': False, 'error': 'Missing upc'}), 400
        
        _ensure_items_prep_tables()
        conn = sqlite3.connect('bol.db')
        cur = conn.cursor()
        
        # Delete from items_prep_status
        cur.execute('DELETE FROM items_prep_status WHERE upc = ? COLLATE NOCASE', (upc,))
        
        # If this is a suffixed UPC (e.g., 110101-1), delete it from bol_items too
        if '-' in upc and upc.split('-')[-1].isdigit():
            print(f'Deleting suffixed bad entry {upc} from bol_items')
            cur.execute('DELETE FROM bol_items WHERE upc = ? COLLATE NOCASE', (upc,))
        
        conn.commit()
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': _safe_error(e)}), 500
    finally:
        conn.close()

@app.route('/api/items_prep/undo', methods=['POST'])
def api_items_prep_undo():
    """Undo the last item prep action.
    For Good items: Decrement status qty (or delete if qty becomes 0), increment base UPC qty
    For Bad items: Delete suffixed entry from bol_items if temporary=1 (not completed yet)
                   or if temporary=0 (completed), restore base qty and delete suffixed entry
    """
    try:
        data = request.get_json()
        if not data:
            return jsonify({'success': False, 'error': 'Missing request body'}), 400
        
        upc = data.get('upc')  # The status UPC (could be suffixed for Bad items)
        status = data.get('status')  # 'good' or 'bad'
        action = data.get('action')  # 'incremented' or 'created' for good items
        previous_qty = data.get('previousQty', 0)  # Previous qty before increment (for good items)
        base_upc = data.get('base_upc')  # The base UPC (for bad items, to restore qty)
        qty = data.get('qty', 1)  # The qty that was decremented
        
        if not upc or not status:
            return jsonify({'success': False, 'error': 'Missing upc or status'}), 400
        
        upc_norm = _normalize_upc(upc)
        if upc_norm and '-' not in upc_norm and upc_norm.isdigit():
            upc = upc_norm.lstrip('0')
        else:
            upc = upc_norm
        
        if base_upc:
            base_upc_norm = _normalize_upc(base_upc)
            if base_upc_norm and base_upc_norm.isdigit():
                base_upc = base_upc_norm.lstrip('0')
            else:
                base_upc = base_upc_norm
        
        _ensure_items_prep_tables()
        conn = sqlite3.connect('bol.db')
        cur = conn.cursor()
        
        if status == 'good':
            # Good item undo: Move qty from good back to unchecked
            cur.execute('SELECT good_qty, unchecked_qty FROM bol_items WHERE upc = ? COLLATE NOCASE', (upc,))
            row = cur.fetchone()
            
            if not row:
                return jsonify({'success': False, 'error': f'UPC {upc} not found'}), 404
            
            current_good = row[0] or 0
            current_unchecked = row[1] or 0
            
            if qty > current_good:
                return jsonify({'success': False, 'error': f'Cannot undo {qty} - only {current_good} marked as good'}), 400
            
            # Move qty from good back to unchecked
            new_good = current_good - qty
            new_unchecked = current_unchecked + qty
            
            cur.execute('''
                UPDATE bol_items 
                SET good_qty = ?, unchecked_qty = ?, quantity = ?
                WHERE upc = ? COLLATE NOCASE
            ''', (new_good, new_unchecked, new_good, upc))
            
            # Update prep status
            if new_good > 0:
                cur.execute('UPDATE items_prep_status SET quantity = ? WHERE upc = ? COLLATE NOCASE', (new_good, upc))
            else:
                # Delete status if no good items left
                cur.execute('DELETE FROM items_prep_status WHERE upc = ? COLLATE NOCASE', (upc,))
            
            print(f'[UNDO GOOD] {upc}: moved {qty} from good to unchecked (good: {current_good}→{new_good}, unchecked: {current_unchecked}→{new_unchecked})')
            
        elif status == 'bad':
            # Bad item undo: Delete suffixed entry and move qty from bad back to unchecked
            cur.execute('SELECT quantity, temporary FROM bol_items WHERE upc = ? COLLATE NOCASE', (upc,))
            row = cur.fetchone()
            
            if not row:
                return jsonify({'success': False, 'error': f'UPC {upc} not found'}), 404
            
            bad_qty_for_this_entry = row[0] if row[0] else 1
            temporary = row[1]
            
            # Delete the suffixed bad entry
            cur.execute('DELETE FROM bol_items WHERE upc = ? COLLATE NOCASE', (upc,))
            cur.execute('DELETE FROM items_prep_status WHERE upc = ? COLLATE NOCASE', (upc,))
            
            # Delete associated photos and notes if completed
            if temporary == 0:
                cur.execute('DELETE FROM items_prep_images WHERE upc = ? COLLATE NOCASE', (upc,))
                try:
                    cur.execute('DELETE FROM items_prep_notes WHERE upc = ? COLLATE NOCASE', (upc,))
                except Exception:
                    pass
            
            # Move qty from bad back to unchecked in base UPC
            if base_upc:
                cur.execute('SELECT bad_qty, unchecked_qty FROM bol_items WHERE upc = ? COLLATE NOCASE', (base_upc,))
                base_row = cur.fetchone()
                
                if base_row:
                    current_bad = base_row[0] or 0
                    current_unchecked = base_row[1] or 0
                    
                    new_bad = max(0, current_bad - bad_qty_for_this_entry)
                    new_unchecked = current_unchecked + bad_qty_for_this_entry
                    
                    cur.execute('''
                        UPDATE bol_items 
                        SET bad_qty = ?, unchecked_qty = ?
                        WHERE upc = ? COLLATE NOCASE
                    ''', (new_bad, new_unchecked, base_upc))
                    
                    print(f'[UNDO BAD] Deleted {upc}, {base_upc}: moved {bad_qty_for_this_entry} from bad to unchecked (bad: {current_bad}→{new_bad}, unchecked: {current_unchecked}→{new_unchecked})')
            else:
                print(f'[UNDO BAD] Warning: No base_upc provided for {upc}, could not restore qty')
        
        conn.commit()
        return jsonify({'success': True})
        
    except Exception as e:
        print(f'Undo error: {e}')
        return jsonify({'success': False, 'error': _safe_error(e)}), 500
    finally:
        conn.close()

@app.route('/api/items_prep/diagnostic', methods=['POST'])
def api_items_prep_diagnostic():
    """Save diagnostic info and photos for a UPC. form-data: upc, reason, note, files: photos[]"""
    try:
        _ensure_items_prep_tables()
        upc_raw = _normalize_upc(request.form.get('upc'))
        # Strip leading zeros ONLY if it's all digits (preserve suffix like -24)
        if upc_raw and '-' in upc_raw:
            # Has suffix: strip zeros from base part only (e.g., '0719978859014-24' -> '719978859014-24')
            parts = upc_raw.split('-', 1)
            base = parts[0].lstrip('0') if parts[0].isdigit() else parts[0]
            upc = f"{base}-{parts[1]}"
        else:
            # No suffix: strip zeros normally
            upc = upc_raw.lstrip('0') if upc_raw and upc_raw.isdigit() else upc_raw
        reason = (request.form.get('reason') or '').strip()
        note = (request.form.get('note') or '').strip()
        if not upc:
            return jsonify({'success': False, 'error': 'Missing upc'}), 400

        # DEBUG: Log incoming request details
        print(f'[DEBUG] Diagnostic upload for UPC: {upc_raw} -> {upc}')
        print(f'[DEBUG] Form keys: {list(request.form.keys())}')
        print(f'[DEBUG] Files keys: {list(request.files.keys())}')
        
        # Mark temporary entry as permanent (no qty changes needed - already done on bad entry creation)
        conn_temp = sqlite3.connect('bol.db')
        cur_temp = conn_temp.cursor()
        
        # Check if this entry is temporary (Bad flow)
        cur_temp.execute('SELECT temporary FROM bol_items WHERE upc = ? COLLATE NOCASE', (upc,))
        temp_row = cur_temp.fetchone()
        is_temporary = temp_row and temp_row[0] == 1
        
        if is_temporary:
            # This is a Bad flow completion - just set temporary=0 (base qty already decremented on bad entry creation)
            cur_temp.execute('UPDATE bol_items SET temporary = 0 WHERE upc = ? COLLATE NOCASE', (upc,))
            conn_temp.commit()
            print(f'[DIAGNOSTIC COMPLETE] Set {upc} to permanent (temporary=0)')
            print(f'[DIAGNOSTIC COMPLETE] Base qty already decremented on bad entry creation - no further changes needed')
        else:
            # Not temporary, just ensure it's marked as permanent
            cur_temp.execute('UPDATE bol_items SET temporary = 0 WHERE upc = ? COLLATE NOCASE AND temporary = 1', (upc,))
            conn_temp.commit()
        
        cur_temp.close()

        # save files under static/items_prep
        from werkzeug.utils import secure_filename
        save_dir = os.path.join(app.root_path, 'static', 'items_prep')
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
        update_data_version()

        return jsonify({'success': True, 'saved': saved})
    except Exception as e:
        try:
            import traceback
            print('Diagnostic upload error:', e)
            traceback.print_exc()
        except Exception:
            pass
        return jsonify({'success': False, 'error': _safe_error(e)}), 500
    finally:
        conn_temp.close()

@app.errorhandler(RequestEntityTooLarge)
def handle_file_too_large(e):
    return jsonify({'success': False, 'error': 'Upload too large. Try fewer photos or enable Low res.'}), 413

@app.route('/api/items_prep/diagnostic/<upc>', methods=['GET'])
def api_items_prep_diagnostic_get(upc):
    try:
        upc_norm = _normalize_upc(upc)
        # Strip leading zeros ONLY if it's all digits (preserve suffix like -24)
        if upc_norm and '-' in upc_norm:
            # Has suffix: strip zeros from base part only (e.g., '0719978859014-24' -> '719978859014-24')
            parts = upc_norm.split('-', 1)
            base = parts[0].lstrip('0') if parts[0].isdigit() else parts[0]
            upc_n = f"{base}-{parts[1]}"
        else:
            # No suffix: strip zeros normally
            upc_n = upc_norm.lstrip('0') if upc_norm and upc_norm.isdigit() else upc_norm
        print(f'[DEBUG] Getting diagnostic for UPC: {upc} -> {upc_norm} -> {upc_n}')
        _ensure_items_prep_tables()
        conn = sqlite3.connect('bol.db')
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute('SELECT status, reason, note, updated_at FROM items_prep_status WHERE upc = ? COLLATE NOCASE', (upc_n,))
        srow = cur.fetchone()
        cur.execute("SELECT id, image_path, created_at, rotation FROM items_prep_images WHERE upc = ? COLLATE NOCASE AND (deleted_at IS NULL OR TRIM(COALESCE(deleted_at,'')) = '') ORDER BY created_at DESC, id DESC", (upc_n,))
        images = [dict(r) for r in cur.fetchall()]
        print(f'[DEBUG] Found {len(images)} images for UPC {upc_n}')
        return jsonify({'upc': upc_n, 'status': dict(srow) if srow else None, 'images': images})
    except Exception as e:
        return jsonify({'error': _safe_error(e)}), 500
    finally:
        conn.close()

@app.route('/api/items_prep/diagnostic/<upc>/photos', methods=['DELETE'])
def api_items_prep_diagnostic_delete_photos(upc):
    """Soft-delete all diagnostic photos for a UPC by moving them to trash and setting retention expiry.
    Query param hard=1 to permanently delete files and rows.
    """
    try:
        upc_norm = _normalize_upc(upc)
        # Strip leading zeros to match item manager behavior
        upc_n = upc_norm.lstrip('0') if upc_norm and upc_norm.isdigit() else upc_norm
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
                exp = ( _dt.datetime.now(_dt.UTC) + _dt.timedelta(days=_trash_retention_days()) ).isoformat()
                cur.execute('UPDATE items_prep_images SET deleted_at=?, expires_at=?, trash_path=? WHERE id=?', (del_at, exp, new_rel or rel, r['id']))
                deleted += 1
            conn.commit()
            return jsonify({'success': True, 'deleted': deleted, 'hard': False, 'retention_days': _trash_retention_days()})
    except Exception as e:
        return jsonify({'success': False, 'error': _safe_error(e)}), 500
    finally:
        conn.close()


@app.route('/api/items_prep/diagnostic/<upc>/photos', methods=['POST'])
def api_items_prep_diagnostic_add_photos(upc):
    """Upload additional diagnostic photos for a UPC without changing status.
    Expects multipart/form-data with files in photos[] (or photos/photo) and optional rotations[]
    Returns: { success, images: [{id, image_path, created_at, rotation}] }
    """
    try:
        upc_norm = _normalize_upc(upc)
        # Strip leading zeros to match item manager behavior
        upc_n = upc_norm.lstrip('0') if upc_norm and upc_norm.isdigit() else upc_norm
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
                cur.execute('INSERT INTO items_prep_images (upc, image_path, created_at, rotation) VALUES (?,?,?,?)', (upc_n, rel, ts, rot))
                img_id = cur.lastrowid
                out.append({'id': img_id, 'image_path': rel, 'created_at': ts, 'rotation': rot})
            except Exception as se:
                print('Failed to save diagnostic image (add):', se)
        conn.commit()
        
        # Clear cache for this UPC to ensure fresh data on next lookup
        cache_key = f"view//api/bol_lookup?upc={upc_n}"
        cache.delete(cache_key)
        
        return jsonify({'success': True, 'images': out})
    except Exception as e:
        return jsonify({'success': False, 'error': _safe_error(e)}), 500
    finally:
        conn.close()


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
            return jsonify({'success': True, 'deleted': deleted, 'hard': True})
        else:
            # soft delete (mark deleted_at, set expires_at, move file to trash)
            if r['deleted_at'] and str(r['deleted_at']).strip():
                return jsonify({'success': False, 'error': 'Photo already deleted'}), 400
            rel = r['image_path']
            abs_path = os.path.join(app.root_path, 'static', rel) if not os.path.isabs(rel) else rel
            new_rel = None
            if os.path.isfile(abs_path):
                new_rel = _move_to_trash(abs_path, r['upc'])
            del_at = _now_iso()
            import datetime as _dt
            exp = (_dt.datetime.now(_dt.UTC) + _dt.timedelta(days=_trash_retention_days())).isoformat()
            cur.execute('UPDATE items_prep_images SET deleted_at=?, expires_at=?, trash_path=? WHERE id=?', (del_at, exp, new_rel or rel, photo_id))
            conn.commit()
            return jsonify({'success': True, 'deleted': 1, 'hard': False, 'retention_days': _trash_retention_days()})
    except Exception as e:
        return jsonify({'success': False, 'error': _safe_error(e)}), 500
    finally:
        conn.close()

@app.route('/api/items_prep/set_display_image', methods=['POST'])
def api_items_prep_set_display_image():
    """Set a diagnostic photo as the display image for a UPC in bol_items.
    JSON: { upc, photo_id } OR { upc, image_path }
    Returns: { success, image_url }
    """
    try:
        data = request.get_json() or {}
        upc = _normalize_upc(data.get('upc', ''))
        upc_n = upc.lstrip('0') if upc and upc.isdigit() else upc
        photo_id = data.get('photo_id')
        image_path = data.get('image_path')
        
        if not upc_n:
            return jsonify({'success': False, 'error': 'Missing upc'}), 400
        
        # Get the image path from photo_id if provided
        if photo_id and not image_path:
            _ensure_items_prep_tables()
            conn = sqlite3.connect('bol.db')
            try:
                cur = conn.cursor()
                cur.execute('SELECT image_path FROM items_prep_images WHERE id = ?', (photo_id,))
                row = cur.fetchone()
                if row:
                    image_path = row[0]
            finally:
                conn.close()
        
        if not image_path:
            return jsonify({'success': False, 'error': 'Missing photo_id or image_path'}), 400
        
        # Convert relative path to full URL (items_prep/xxx.jpg -> http://host/static/items_prep/xxx.jpg)
        # For simplicity, just store the relative path and let the frontend construct the URL
        image_url = f"/static/{image_path}" if not image_path.startswith('/') else image_path
        
        # Update bol_items with the new image_url
        conn = sqlite3.connect('bol.db')
        cur = conn.cursor()
        
        # Update all entries with this UPC (base and suffixed) to use this image
        cur.execute('UPDATE bol_items SET image_url = ? WHERE upc = ? COLLATE NOCASE', (image_url, upc_n))
        updated = cur.rowcount
        
        conn.commit()
        
        print(f'[SET DISPLAY IMAGE] Updated {updated} bol_items entries for UPC {upc_n} with image_url: {image_url}')
        
        return jsonify({'success': True, 'image_url': image_url, 'updated': updated})
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': _safe_error(e)}), 500
    finally:
        conn.close()

@app.route('/api/items_prep/diagnostic/<upc>/trash', methods=['GET'])
def api_items_prep_trash_list(upc):
    try:
        upc_norm = _normalize_upc(upc)
        # Strip leading zeros to match item manager behavior
        upc_n = upc_norm.lstrip('0') if upc_norm and upc_norm.isdigit() else upc_norm
        _ensure_items_prep_tables()
        conn = sqlite3.connect('bol.db')
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute('SELECT id, trash_path, deleted_at, expires_at FROM items_prep_images WHERE upc = ? COLLATE NOCASE AND deleted_at IS NOT NULL ORDER BY deleted_at DESC', (upc_n,))
        rows = [dict(r) for r in cur.fetchall()]
        return jsonify({'success': True, 'results': rows})
    except Exception as e:
        return jsonify({'success': False, 'error': _safe_error(e)}), 500
    finally:
        conn.close()

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
        return jsonify({'success': True, 'restored': restored})
    except Exception as e:
        return jsonify({'success': False, 'error': _safe_error(e)}), 500
    finally:
        conn.close()

@app.route('/api/items_prep/diagnostic/<upc>/reset', methods=['POST'])
def api_items_prep_diagnostic_reset(upc):
    """Reset item to original unchecked state: restore original_qty to unchecked, clear good/bad quantities"""
    try:
        upc_n = _normalize_upc(upc)
        conn = sqlite3.connect('bol.db')
        cur = conn.cursor()
        
        # Get the current original_qty
        cur.execute('SELECT original_qty FROM bol_items WHERE upc = ? COLLATE NOCASE', (upc_n,))
        row = cur.fetchone()
        
        if not row:
            return jsonify({'success': False, 'error': 'Item not found'}), 404, 404
        
        original_qty = row[0] or 0
        
        # Reset: unchecked_qty = original_qty, good_qty = 0, bad_qty = 0, quantity = 0
        cur.execute('''
            UPDATE bol_items 
            SET unchecked_qty = ?, 
                good_qty = 0, 
                bad_qty = 0, 
                quantity = 0
            WHERE upc = ? COLLATE NOCASE
        ''', (original_qty, upc_n))
        
        # Also clear the status and reason from items_prep_status
        _ensure_items_prep_tables()
        cur.execute('DELETE FROM items_prep_status WHERE upc = ? COLLATE NOCASE', (upc_n,))
        
        conn.commit()
        
        return jsonify({
            'success': True, 
            'original_qty': original_qty,
            'message': f'Reset to {original_qty} unchecked'
        })
    except Exception as e:
        return jsonify({'success': False, 'error': _safe_error(e)}), 500
    finally:
        conn.close()

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
        upc_norm = _normalize_upc(upc)
        # Strip leading zeros to match item manager behavior
        upc_n = upc_norm.lstrip('0') if upc_norm and upc_norm.isdigit() else upc_norm
        _ensure_items_prep_tables()
        conn = sqlite3.connect('bol.db')
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute('SELECT id, note, created_at FROM items_prep_notes WHERE upc = ? COLLATE NOCASE ORDER BY created_at DESC', (upc_n,))
        notes = [dict(r) for r in cur.fetchall()]
        return jsonify({'success': True, 'notes': notes})
    except Exception as e:
        return jsonify({'success': False, 'error': _safe_error(e)}), 500
    finally:
        conn.close()

@app.route('/api/items_prep/notes', methods=['POST'])
def api_items_prep_notes_add():
    """Add a new note for a UPC. JSON: { upc, note }"""
    try:
        data = request.get_json() or {}
        upc_norm = _normalize_upc(data.get('upc'))
        # Strip leading zeros to match item manager behavior
        upc = upc_norm.lstrip('0') if upc_norm and upc_norm.isdigit() else upc_norm
        note = (data.get('note') or '').strip()
        if not upc or not note:
            return jsonify({'success': False, 'error': 'Missing upc or note'}), 400
        _ensure_items_prep_tables()
        import datetime
        ts = datetime.datetime.now(datetime.UTC).isoformat()
        conn = sqlite3.connect('bol.db')
        cur = conn.cursor()
        cur.execute('INSERT INTO items_prep_notes (upc, note, created_at) VALUES (?,?,?)', (upc, note, ts))
        note_id = cur.lastrowid
        conn.commit()
        return jsonify({'success': True, 'id': note_id, 'created_at': ts})
    except Exception as e:
        return jsonify({'success': False, 'error': _safe_error(e)}), 500
    finally:
        conn.close()

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
        if deleted == 0:
            return jsonify({'success': False, 'error': 'Note not found'}), 404
        return jsonify({'success': True, 'deleted': deleted})
    except Exception as e:
        return jsonify({'success': False, 'error': _safe_error(e)}), 500
    finally:
        conn.close()

@app.route('/api/items_prep/location/<upc>', methods=['GET'])
def api_items_prep_location_get(upc):
    """Get location for a UPC."""
    try:
        upc_norm = _normalize_upc(upc)
        # Strip leading zeros to match item manager behavior
        upc = upc_norm.lstrip('0') if upc_norm and upc_norm.isdigit() else upc_norm
        _ensure_items_prep_tables()
        conn = sqlite3.connect('bol.db')
        cur = conn.cursor()
        cur.execute('SELECT location, pictureposition FROM items_prep_status WHERE upc = ? COLLATE NOCASE', (upc,))
        row = cur.fetchone()
        if row:
            return jsonify({'success': True, 'location': row[0], 'pictureposition': row[1]})
        else:
            return jsonify({'success': True, 'location': None, 'pictureposition': None})
    except Exception as e:
        return jsonify({'success': False, 'error': _safe_error(e)}), 500
    finally:
        conn.close()

@app.route('/api/items_prep/location', methods=['POST'])
def api_items_prep_location_set():
    """Set location for a UPC. JSON: { upc, location, pictureposition }"""
    try:
        data = request.get_json() or {}
        upc_norm = _normalize_upc(data.get('upc'))
        # Strip leading zeros to match item manager behavior
        upc = upc_norm.lstrip('0') if upc_norm and upc_norm.isdigit() else upc_norm
        location = (data.get('location') or '').strip()
        pictureposition = (data.get('pictureposition') or '').strip()
        if not upc:
            return jsonify({'success': False, 'error': 'Missing upc'}), 400
        _ensure_items_prep_tables()
        import datetime
        ts = datetime.datetime.now(datetime.UTC).isoformat()
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
            except Exception as e:
                print(f'Warning: Failed to update searchRack: {e}')
            finally:
                search_conn.close()
        
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': _safe_error(e)}), 500
    finally:
        conn.close()

@app.route('/api/items_prep/reset', methods=['POST'])
def api_items_prep_reset():
    """Reset item back to rawbol.db default state. JSON: { upc }"""
    try:
        data = request.get_json() or {}
        upc = _normalize_upc(data.get('upc'))
        if not upc:
            return jsonify({'success': False, 'error': 'Missing upc'}), 400
        
        _ensure_items_prep_tables()
        
        # Get original quantity from rawbol.db
        rawbol_qty = None
        try:
            rawbol_conn = sqlite3.connect('rawbol.db')
            rawbol_cur = rawbol_conn.cursor()
            rawbol_cur.execute('SELECT QTY FROM rawbol WHERE UPC = ? COLLATE NOCASE LIMIT 1', (upc,))
            rawbol_row = rawbol_cur.fetchone()
            rawbol_qty = rawbol_row[0] if rawbol_row else 1
        except Exception as e:
            print(f'Warning: Could not fetch from rawbol.db: {e}')
            rawbol_qty = 1
        finally:
            rawbol_conn.close()
        
        conn = sqlite3.connect('bol.db')
        cur = conn.cursor()
        
        # 1. Delete from items_prep_status
        cur.execute('DELETE FROM items_prep_status WHERE upc = ? COLLATE NOCASE', (upc,))
        
        # 2. Delete from items_prep_images
        cur.execute('DELETE FROM items_prep_images WHERE upc = ? COLLATE NOCASE', (upc,))
        
        # 3. Delete from items_prep_notes if it exists
        try:
            cur.execute('DELETE FROM items_prep_notes WHERE upc = ? COLLATE NOCASE', (upc,))
        except Exception:
            pass  # Table may not exist
        
        # 4. Restore quantity in bol_items
        # Check if new quantity columns exist
        cur.execute('PRAGMA table_info(bol_items)')
        cols = [col[1].lower() for col in cur.fetchall()]
        has_new_cols = 'original_qty' in cols and 'unchecked_qty' in cols
        
        if has_new_cols:
            # Reset all quantity columns to rawbol value
            cur.execute('''UPDATE bol_items 
                          SET quantity = ?, 
                              original_qty = ?,
                              good_qty = 0,
                              bad_qty = 0,
                              unchecked_qty = ?
                          WHERE upc = ? COLLATE NOCASE''', 
                       (rawbol_qty, rawbol_qty, rawbol_qty, upc))
        else:
            # Old behavior
            cur.execute('UPDATE bol_items SET quantity = ? WHERE upc = ? COLLATE NOCASE', (rawbol_qty, upc))
        
        # 5. Clear list_status if it exists
        try:
            cur.execute('UPDATE bol_items SET list_status = NULL WHERE upc = ? COLLATE NOCASE', (upc,))
        except Exception:
            pass  # Column may not exist
        
        conn.commit()
        
        return jsonify({'success': True, 'quantity': rawbol_qty, 'message': 'Item reset to default state'})
    except Exception as e:
        return jsonify({'success': False, 'error': _safe_error(e)}), 500
    finally:
        conn.close()

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
        return jsonify({'success': False, 'error': _safe_error(e)}), 500

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
        # add absolute-ish URLs for preview (served from static)
        for r in rows:
            r['url'] = url_for('static', filename=(r.get('trash_path') or r.get('image_path') or ''))
        return jsonify({'success': True, 'results': rows, 'total': total, 'page': page, 'size': size})
    except Exception as e:
        return jsonify({'success': False, 'error': _safe_error(e)}), 500
    finally:
        conn.close()

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
            return jsonify({'success': False, 'error': 'No ids provided'}), 400
        # Group ids by UPC, then restore only the specific ids per UPC
        from DBmanager import connect_db
        with connect_db('bol.db') as conn:
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()
            placeholders = ','.join('?' for _ in ids)
            cur.execute(f'SELECT id, upc FROM items_prep_images WHERE id IN ({placeholders})', tuple(ids))
            rows = cur.fetchall()
        # Group by UPC
        upc_ids = {}
        for r in rows:
            upc_ids.setdefault(r['upc'], []).append(r['id'])
        restored_total = 0
        for group_upc, group_ids in upc_ids.items():
            # Build a fake request context with the specific ids
            with app.test_request_context(json={'ids': group_ids}):
                resp = api_items_prep_trash_restore(group_upc)
                restored_total += len(group_ids)
        return jsonify({'success': True, 'restored': restored_total})
    except Exception as e:
        return jsonify({'success': False, 'error': _safe_error(e)}), 500

@app.route('/api/trash/hard_delete', methods=['POST'])
def api_trash_hard_delete():
    try:
        data = request.get_json() or {}
        ids = data.get('ids') or []
        upc = data.get('upc')
        if upc and not ids:
            # hard delete all for UPC
            with app.test_request_context(query_string={'hard': '1'}):
                return api_items_prep_diagnostic_delete_photos(upc)
        if not ids:
            return jsonify({'success': False, 'error': 'No ids provided'}), 400, 400
        # Delete only the specific IDs requested
        from DBmanager import connect_db
        with connect_db('bol.db') as conn:
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()
            placeholders = ','.join('?' for _ in ids)
            cur.execute(f'SELECT id, image_path, trash_path FROM items_prep_images WHERE id IN ({placeholders})', tuple(ids))
            rows = cur.fetchall()
            deleted = 0
            for r in rows:
                for rel in [r['trash_path'], r['image_path']]:
                    if rel:
                        abs_path = os.path.join(app.root_path, 'static', rel) if not os.path.isabs(rel) else rel
                        try:
                            if os.path.isfile(abs_path):
                                os.remove(abs_path)
                        except Exception as fe:
                            print('Failed hard remove photo', abs_path, fe)
                cur.execute('DELETE FROM items_prep_images WHERE id = ?', (r['id'],))
                deleted += 1
        return jsonify({'success': True, 'deleted': deleted, 'hard': True})
    except Exception as e:
        return jsonify({'success': False, 'error': _safe_error(e)}), 500

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
        total = 0
        for u in upcs:
            # hard=1 ensures permanent deletion
            with app.test_request_context(query_string={'hard':'1'}):
                resp = api_items_prep_diagnostic_delete_photos(u)
                total += 1
        return jsonify({'success': True, 'deleted_groups': total})
    except Exception as e:
        return jsonify({'success': False, 'error': _safe_error(e)}), 500
    finally:
        conn.close()

@app.route('/api/trash/purge_expired', methods=['POST'])
def api_trash_purge_expired():
    try:
        count = _purge_expired_trash()
        return jsonify({'success': True, 'purged': count})
    except Exception as e:
        return jsonify({'success': False, 'error': _safe_error(e)}), 500

@app.route('/api/debug/db_check', methods=['GET'])
@require_debug_mode
def api_debug_db_check():
    """Debug endpoint to check DB columns and sample data."""
    try:
        conn = sqlite3.connect('bol.db')
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        
        # Check columns
        cur.execute("PRAGMA table_info(bol_items)")
        cols = [dict(r) for r in cur.fetchall()]
        
        # Check sample listed item
        cur.execute("SELECT upc, list_status, listed_amazon, listed_ebay, listed_facebook FROM bol_items WHERE list_status IS NOT NULL AND list_status != '' LIMIT 5")
        rows = [dict(r) for r in cur.fetchall()]
        
        return jsonify({'columns': cols, 'sample_listed': rows})
    except Exception as e:
        return jsonify({'error': _safe_error(e)}), 500
    finally:
        conn.close()

@app.route('/api/bol_items', methods=['GET'])
# NO CACHE - Items-to-list needs fresh data for marketplace checkboxes
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
        # Allow large limits for stats calculation (up to 50000), otherwise cap at 100
        if limit < 1: limit = 25
        elif limit > 50000: limit = 50000
        elif limit <= 100: pass  # Normal range
        # else: allow limits between 100 and 50000 for stats
        offset = (page - 1) * limit
        
        # Parse comma-separated status filters (e.g., "good,bad")
        status_filters = [s.strip() for s in status_filter.split(',') if s.strip()] if status_filter else []
        
        # Get listed/not_listed filters
        listed_flag = request.args.get('listed', '').strip().lower() == 'true'
        not_listed_flag = request.args.get('not_listed', '').strip().lower() == 'true'
        
        # Get defect filter
        defect_filter = (request.args.get('defect') or '').strip()
        
        # Log request for debugging
        print(f"[api_bol_items] Request: page={page}, limit={limit}, q={q_stripped}, status={status_filters}, defect={defect_filter}, _v={request.args.get('_v')}, _t={request.args.get('_t')}")
        
        # DEBUG: Check specific UPC if present in query
        if q_stripped == '86279051523':
             print(f"[DEBUG] Searching for 86279051523. Status filters: {status_filters}")

        print(f"[api_bol_items] Filters - lot: '{lot}', import_date: '{import_date}', q: '{q_stripped}', status_filters: {status_filters}, listed: {listed_flag}, not_listed: {not_listed_flag}")
        conn = sqlite3.connect('bol.db')
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='bol_items'")
        if not cur.fetchone():
            return jsonify({'results': []})
        # Ensure prep tables exist for left join and statuses
        _ensure_items_prep_tables()
        cur.execute("PRAGMA table_info(bol_items)")
        cols = [r[1] for r in cur.fetchall()]
        has_temporary = any(c.lower() == 'temporary' for c in cols)
        has_itemprepped = any(c.lower() == 'itemprepped' for c in cols)
        
        # DEBUG: Log columns to diagnose marketplace column issue
        marketplace_cols = [c for c in cols if 'listed' in c.lower()]
        if not marketplace_cols:
            print(f"[api_bol_items] WARNING: No 'listed' columns found in bol_items table!")
            print(f"[api_bol_items] Available columns: {cols}")
        
        where = []
        params = []
        # Exclude itemprepped entries (internal prep tracking) from item manager
        if has_itemprepped:
            where.append('(b.itemprepped IS NULL OR b.itemprepped = 0)')
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
        
        # Apply status filter in SQL WHERE clause (supports multiple statuses)
        if status_filters:
            status_conditions = []
            for sf in status_filters:
                sf = sf.replace(' ', '_')
                if sf == 'unchecked':
                    # Unchecked: no prep_status OR explicit 'unchecked', AND exclude suffixed items (BAD flow temporaries)
                    status_conditions.append("((s.status IS NULL OR s.status = 'unchecked') AND b.upc NOT LIKE '%-%')")
                elif sf in ('good', 'bad'):
                    # Good or Bad: explicit status match
                    status_conditions.append(f"s.status = '{sf}'")
            
            if status_conditions:
                where.append(f"({' OR '.join(status_conditions)})")
        
        # Apply listed/not_listed filter
        if listed_flag:
            where.append("(b.list_status = 'listed')")
        elif not_listed_flag:
            where.append("(b.list_status IS NULL OR b.list_status = '' OR b.list_status != 'listed')")
        
        # Apply defect filter (filter by prep_reason column)
        if defect_filter:
            where.append('s.reason = ?')
            params.append(defect_filter)
        
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
        # Get total count, unique items count, and total quantity
        count_sql = 'SELECT COUNT(*), COUNT(DISTINCT b.upc), SUM(COALESCE(b.quantity, 1)) FROM bol_items b LEFT JOIN items_prep_status s ON s.upc = b.upc ' + where_sql
        cur.execute(count_sql, params)
        count_row = cur.fetchone()
        total = count_row[0]
        unique_items = count_row[1] or 0
        total_quantity = count_row[2] or 0
        
        # Check if new quantity columns exist
        has_original_qty = any(c.lower() == 'original_qty' for c in cols)
        has_good_qty = any(c.lower() == 'good_qty' for c in cols)
        has_bad_qty = any(c.lower() == 'bad_qty' for c in cols)
        has_unchecked_qty = any(c.lower() == 'unchecked_qty' for c in cols)
        has_listed_amazon = any(c.lower() == 'listed_amazon' for c in cols)
        has_listed_ebay = any(c.lower() == 'listed_ebay' for c in cols)
        has_listed_facebook = any(c.lower() == 'listed_facebook' for c in cols)
        
        # DEBUG: Log marketplace column detection
        print(f"[api_bol_items] Marketplace column detection:")
        print(f"  has_listed_amazon: {has_listed_amazon}")
        print(f"  has_listed_ebay: {has_listed_ebay}")
        print(f"  has_listed_facebook: {has_listed_facebook}")
        print(f"  Actual columns: {[c for c in cols if 'listed' in c.lower()]}")
        
        sql = (
            'SELECT b.id, b.upc, b.item_description, b.image_url, b.lot_number, b.bol_number, b.import_date, b.list_status, b.quantity, ' +
            ('b.temporary, ' if has_temporary else '') +
            ('b.original_qty, ' if has_original_qty else '') +
            ('b.good_qty, ' if has_good_qty else '') +
            ('b.bad_qty, ' if has_bad_qty else '') +
            ('b.unchecked_qty, ' if has_unchecked_qty else '') +
            ('b.listed_amazon, b.listed_amazon_date, ' if has_listed_amazon else '') +
            ('b.listed_ebay, b.listed_ebay_date, ' if has_listed_ebay else '') +
            ('b.listed_facebook, b.listed_facebook_date, ' if has_listed_facebook else '') +
            's.status as prep_status, s.reason as prep_reason, s.note as prep_note, s.updated_at as prep_updated_at, s.quantity as prep_quantity '
            'FROM bol_items b '
            'LEFT JOIN items_prep_status s ON s.upc = b.upc '
            + where_sql + order_sql
        )
        sql += ' LIMIT ? OFFSET ?'
        params.extend([limit, offset])
        cur.execute(sql, params)
        rows = [dict(r) for r in cur.fetchall()]
        
        # DEBUG: Check if marketplace columns are in the first row
        if rows and len(rows) > 0:
            first_row = rows[0]
            print(f"[api_bol_items] First row keys: {list(first_row.keys())}")
            print(f"[api_bol_items] First row marketplace data: listed_amazon={first_row.get('listed_amazon')}, listed_ebay={first_row.get('listed_ebay')}, listed_facebook={first_row.get('listed_facebook')}")
        
        # Build a status map only for UPCs in current page results (avoids full table scan)
        status_map = {}
        page_upcs = set()
        for r in rows:
            u = r.get('upc')
            if u:
                page_upcs.add(str(u))
                page_upcs.add(_normalize_upc(u))
        try:
            if page_upcs:
                _ensure_items_prep_tables()
                from DBmanager import connect_db
                with connect_db('bol.db') as c2:
                    c2.row_factory = sqlite3.Row
                    k2 = c2.cursor()
                    placeholders = ','.join('?' for _ in page_upcs)
                    k2.execute(f'SELECT upc, status, reason, note, updated_at FROM items_prep_status WHERE upc IN ({placeholders})', tuple(page_upcs))
                    for rr in k2.fetchall():
                        raw_upc = rr['upc']
                        st = {'prep_status': rr['status'], 'prep_reason': rr['reason'], 'prep_note': rr['note'], 'prep_updated_at': rr['updated_at']}
                        if raw_upc:
                            status_map[str(raw_upc)] = st
                            nu = _normalize_upc(raw_upc)
                            status_map[nu] = st

                    # Check items_prep_notes only for page UPCs
                    k2.execute(f'SELECT upc, COUNT(*) as note_count FROM items_prep_notes WHERE upc IN ({placeholders}) GROUP BY upc', tuple(page_upcs))
                    for rr in k2.fetchall():
                        raw_upc = rr['upc']
                        note_count = rr['note_count']
                        existing = status_map.get(str(raw_upc), {})
                        existing['prep_note_count'] = note_count
                        status_map[str(raw_upc)] = existing
                        nu = _normalize_upc(raw_upc)
                        existing_n = status_map.get(nu, {})
                        existing_n['prep_note_count'] = note_count
                        status_map[nu] = existing_n
        except Exception:
            status_map = {}
        # Enrich rows with status map for any missing joins
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
                    row['prep_note_count'] = sm.get('prep_note_count', 0)
                    st = (row.get('prep_status') or '').strip().lower()
            # Also enrich with note count if available in status_map
            if 'prep_note_count' not in row:
                up = _normalize_upc(row.get('upc'))
                sm = status_map.get(up)
                if sm and 'prep_note_count' in sm:
                    row['prep_note_count'] = sm.get('prep_note_count', 0)
            return st if st in ('good','bad','unchecked') else 'unchecked'
        
        # Apply status enrichment to rows
        for r in rows:
            status_of(r)
        
        # Normalize for UI
        results = []
        for r in rows:
            # Determine last_edited: prefer items_prep_status.updated_at, fall back to import_date
            last_edited = r.get('prep_updated_at') or r.get('import_date') or ''
            status = (r.get('prep_status') or 'unchecked').strip().lower()
            
            # For GOOD status, use prep_quantity from items_prep_status (if available)
            # For other statuses, use the bol_items quantity
            if status == 'good' and r.get('prep_quantity') is not None:
                display_qty = r.get('prep_quantity')
            else:
                display_qty = r.get('quantity') or 1
            
            # Check if item has notes in items_prep_notes table
            has_notes = r.get('prep_note_count', 0) > 0
            
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
                'status': status,
                'list_status': (r.get('list_status') or ''),
                'temporary': r.get('temporary'),
                'quantity': display_qty,
                'note': 'yes' if has_notes else '',
                # Marketplace listing columns
                'listed_amazon': r.get('listed_amazon'),
                'listed_amazon_date': r.get('listed_amazon_date'),
                'listed_ebay': r.get('listed_ebay'),
                'listed_ebay_date': r.get('listed_ebay_date'),
                'listed_facebook': r.get('listed_facebook'),
                'listed_facebook_date': r.get('listed_facebook_date')
            })
        return jsonify({'results': results, 'total': total, 'unique_items': unique_items, 'total_quantity': total_quantity, 'page': page, 'limit': limit})
    except Exception as e:
        return jsonify({'error': _safe_error(e)}), 500
    finally:
        conn.close()

@app.route('/api/bulk_delete_bol_items', methods=['POST'])
def api_bulk_delete_bol_items():
    """Delete or reset selected BOL items.
    - Duplicates (UPC with -N suffix) or custom 777 barcodes: DELETE
    - Base barcodes: RESET to rawbol.db default state
    """
    try:
        data = request.get_json() or {}
        # Support both old format (ids array) and new format (items array with id+upc)
        items = data.get('items', [])
        if not items:
            # Fallback to old format
            ids = data.get('ids', [])
            if not ids:
                return jsonify({'success': False, 'error': 'No items provided'}), 400
            # Fetch UPCs for the old format
            conn = sqlite3.connect('bol.db')
            try:
                conn.row_factory = sqlite3.Row
                cur = conn.cursor()
                placeholders = ','.join('?' for _ in ids)
                cur.execute(f'SELECT id, upc FROM bol_items WHERE id IN ({placeholders})', tuple(ids))
                rows = cur.fetchall()
                items = [{'id': r['id'], 'upc': r['upc']} for r in rows]
            finally:
                conn.close()
        
        if not items:
            return jsonify({'success': False, 'error': 'No items provided'}), 400
        
        import re
        _ensure_items_prep_tables()
        
        deletable_ids = []
        reset_upcs = []
        
        # Categorize items: duplicates/custom for deletion, base barcodes for reset
        for item in items:
            upc_val = item.get('upc', '')
            s = '' if upc_val is None else str(upc_val).strip()
            # Delete if UPC ends with -[digits] OR starts with 777
            if re.search(r'-\d+$', s) or s.startswith('777'):
                deletable_ids.append(item['id'])
            else:
                # Base barcode - reset instead of delete
                reset_upcs.append(s)
        
        deleted = 0
        reset_count = 0
        
        # Handle deletions
        if deletable_ids:
            conn = sqlite3.connect('bol.db')
            try:
                conn.row_factory = sqlite3.Row
                cur = conn.cursor()
            
                # First, get the UPCs for these IDs so we can delete their prep data
                ph = ','.join('?' for _ in deletable_ids)
                cur.execute(f'SELECT id, upc FROM bol_items WHERE id IN ({ph})', tuple(deletable_ids))
                items_to_delete = cur.fetchall()
                upcs_to_clean = [row['upc'] for row in items_to_delete]
            
                print(f'[BULK DELETE] Deleting {len(items_to_delete)} suffixed entries:')
                for item in items_to_delete:
                    print(f'  - ID: {item["id"]}, UPC: {item["upc"]}')
            
                # Delete the bol_items entries
                cur.execute(f'DELETE FROM bol_items WHERE id IN ({ph})', tuple(deletable_ids))
                deleted = cur.rowcount
            
                # Also delete associated prep data (status, images, notes) for these suffixed UPCs
                for upc in upcs_to_clean:
                    print(f'[BULK DELETE] Cleaning prep data for UPC: {upc}')
                    cur.execute('DELETE FROM items_prep_status WHERE upc = ? COLLATE NOCASE', (upc,))
                    status_deleted = cur.rowcount
                    cur.execute('DELETE FROM items_prep_images WHERE upc = ? COLLATE NOCASE', (upc,))
                    images_deleted = cur.rowcount
                    try:
                        cur.execute('DELETE FROM items_prep_notes WHERE upc = ? COLLATE NOCASE', (upc,))
                        notes_deleted = cur.rowcount
                    except Exception:
                        notes_deleted = 0
                    print(f'[BULK DELETE]   - Deleted: {status_deleted} status, {images_deleted} images, {notes_deleted} notes')
            
                conn.commit()
            finally:
                conn.close()
        
        # Handle resets for base barcodes
        if reset_upcs:
            for upc in reset_upcs:
                try:
                    # Get original quantity from rawbol.db
                    rawbol_qty = 1
                    try:
                        rawbol_conn = sqlite3.connect('rawbol.db')
                        rawbol_cur = rawbol_conn.cursor()
                        rawbol_cur.execute('SELECT QTY FROM rawbol WHERE UPC = ? COLLATE NOCASE LIMIT 1', (upc,))
                        rawbol_row = rawbol_cur.fetchone()
                        rawbol_qty = rawbol_row[0] if rawbol_row else 1
                    except Exception:
                        pass
                    finally:
                        rawbol_conn.close()
                    
                    conn = sqlite3.connect('bol.db')
                    cur = conn.cursor()
                    
                    # Check if item was marked as "good" - if so, reset to unchecked instead of deleting
                    cur.execute('SELECT status FROM items_prep_status WHERE upc = ? COLLATE NOCASE', (upc,))
                    status_row = cur.fetchone()
                    is_good = status_row and status_row[0] == 'good'
                    
                    if is_good:
                        # Reset to unchecked instead of deleting
                        print(f'[BULK DELETE] Resetting GOOD item {upc} to unchecked')
                        cur.execute('UPDATE items_prep_status SET status = ?, reason = "", note = "" WHERE upc = ? COLLATE NOCASE', ('unchecked', upc))
                    else:
                        # Not good - delete prep status entirely
                        cur.execute('DELETE FROM items_prep_status WHERE upc = ? COLLATE NOCASE', (upc,))
                    
                    # Always delete images and notes on reset
                    cur.execute('DELETE FROM items_prep_images WHERE upc = ? COLLATE NOCASE', (upc,))
                    try:
                        cur.execute('DELETE FROM items_prep_notes WHERE upc = ? COLLATE NOCASE', (upc,))
                    except Exception:
                        pass
                    
                    # Restore quantity and clear list_status
                    # Check if new quantity columns exist
                    cur.execute('PRAGMA table_info(bol_items)')
                    cols = [col[1].lower() for col in cur.fetchall()]
                    has_new_cols = 'original_qty' in cols and 'unchecked_qty' in cols
                    
                    if has_new_cols:
                        # Reset all quantity columns to rawbol value
                        cur.execute('''UPDATE bol_items 
                                      SET quantity = ?, 
                                          original_qty = ?,
                                          good_qty = 0,
                                          bad_qty = 0,
                                          unchecked_qty = ?
                                      WHERE upc = ? COLLATE NOCASE''', 
                                   (rawbol_qty, rawbol_qty, rawbol_qty, upc))
                    else:
                        # Old behavior
                        cur.execute('UPDATE bol_items SET quantity = ? WHERE upc = ? COLLATE NOCASE', (rawbol_qty, upc))
                    
                    try:
                        cur.execute('UPDATE bol_items SET list_status = NULL WHERE upc = ? COLLATE NOCASE', (upc,))
                    except Exception:
                        pass
                    
                    conn.commit()
                    reset_count += 1
                except Exception as e:
                    print(f'Warning: Failed to reset UPC {upc}: {e}')
                finally:
                    conn.close()
        
        return jsonify({'success': True, 'deleted': deleted, 'reset': reset_count})
    except Exception as e:
        return jsonify({'success': False, 'error': _safe_error(e)}), 500

@app.route('/api/lots/select', methods=['POST'])
def api_set_selected_lot():
    """Set the selected LOT in session storage."""
    try:
        data = request.get_json() or {}
        lot_number = data.get('lot_number', '').strip()
        
        if not lot_number:
            return jsonify({'success': False, 'error': 'Missing lot_number'}), 400
        
        session['selected_lot'] = lot_number
        return jsonify({'success': True, 'selected_lot': lot_number})
    
    except Exception as e:
        return jsonify({'success': False, 'error': _safe_error(e)}), 500

@app.route('/api/lots', methods=['GET'])
@app.route('/api/bol_lots', methods=['GET'])
def api_bol_lots():
    """Return list of available lots with most recent import_date from rawbol.db. Sorted newest first."""
    try:
        conn = sqlite3.connect('rawbol.db')
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        
        # Check if raw_bol_items table exists
        cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='raw_bol_items'")
        if not cur.fetchone():
            return jsonify({'lots': [], 'selected_lot': session.get('selected_lot')})
        
        # Get distinct LOT numbers with their import dates, filtered and sorted
        cur.execute('''
            SELECT 
                lot_number,
                MAX(import_date) as import_date
            FROM raw_bol_items
            WHERE lot_number IS NOT NULL 
                AND TRIM(COALESCE(lot_number, '')) != ''
                AND LOWER(lot_number) NOT IN ('nan', 'none', 'null')
            GROUP BY lot_number
            ORDER BY MAX(import_date) DESC
        ''')
        
        lots = []
        for r in cur.fetchall():
            # Format date as YYYY-MM-DD
            date_str = r['import_date'] or ''
            if date_str:
                try:
                    from datetime import datetime
                    dt = datetime.fromisoformat(date_str.replace('Z', '+00:00'))
                    date_str = dt.strftime('%Y-%m-%d')
                except Exception:
                    pass
            
            lots.append({
                'lot_number': r['lot_number'],
                'import_date': date_str
            })
        
        
        # Return lots with current selected lot from session
        selected_lot = session.get('selected_lot')
        # If no lot selected, default to the newest (first in list)
        if not selected_lot and lots:
            selected_lot = lots[0]['lot_number']
            session['selected_lot'] = selected_lot
        
        return jsonify({'lots': lots, 'selected_lot': selected_lot})
    except Exception as e:
        return jsonify({'error': _safe_error(e)}), 500
    finally:
        conn.close()

@app.route('/api/bol_items/list_status', methods=['POST'])
def api_bol_items_set_list_status():
    """Set marketplace listing status for a BOL item by UPC. 
    JSON: { upc, marketplace, listed } where marketplace in ['amazon', 'ebay', 'facebook'] and listed is boolean"""
    try:
        data = request.get_json() or {}
        upc = _normalize_upc(data.get('upc'))
        marketplace = (data.get('marketplace') or '').strip().lower()
        listed = bool(data.get('listed', True))
        
        print(f"[list_status] UPC: {upc}, Marketplace: {marketplace}, Listed: {listed}")
        
        if not upc:
            return jsonify({'success': False, 'error': 'Missing upc'}), 400
        if marketplace not in ('amazon', 'ebay', 'facebook'):
            return jsonify({'success': False, 'error': 'Invalid marketplace. Must be amazon, ebay, or facebook'}), 400
        
        # Ensure columns exist before trying to update
        _ensure_bol_list_status_column()
        
        conn = sqlite3.connect('bol.db')
        cur = conn.cursor()
        
        # Update marketplace-specific column and timestamp
        if marketplace == 'amazon':
            if listed:
                cur.execute('UPDATE bol_items SET listed_amazon=1, listed_amazon_date=datetime("now") WHERE upc = ? COLLATE NOCASE', (upc,))
                print(f"[list_status] Set listed_amazon=1 for {upc}")
            else:
                cur.execute('UPDATE bol_items SET listed_amazon=0, listed_amazon_date=NULL WHERE upc = ? COLLATE NOCASE', (upc,))
                print(f"[list_status] Set listed_amazon=0 for {upc}")
        elif marketplace == 'ebay':
            if listed:
                cur.execute('UPDATE bol_items SET listed_ebay=1, listed_ebay_date=datetime("now") WHERE upc = ? COLLATE NOCASE', (upc,))
            else:
                cur.execute('UPDATE bol_items SET listed_ebay=0, listed_ebay_date=NULL WHERE upc = ? COLLATE NOCASE', (upc,))
        elif marketplace == 'facebook':
            if listed:
                cur.execute('UPDATE bol_items SET listed_facebook=1, listed_facebook_date=datetime("now") WHERE upc = ? COLLATE NOCASE', (upc,))
            else:
                cur.execute('UPDATE bol_items SET listed_facebook=0, listed_facebook_date=NULL WHERE upc = ? COLLATE NOCASE', (upc,))
        
        # Update legacy list_status column for backward compatibility
        # Item is "listed" if listed on ANY marketplace
        cur.execute('''
            UPDATE bol_items 
            SET list_status = CASE 
                WHEN COALESCE(listed_amazon, 0) = 1 OR COALESCE(listed_ebay, 0) = 1 OR COALESCE(listed_facebook, 0) = 1 
                THEN 'listed' 
                ELSE NULL 
            END
            WHERE upc = ? COLLATE NOCASE
        ''', (upc,))
        
        conn.commit()
        updated = cur.rowcount
        
        # Fetch the updated state to return it
        cur.execute('SELECT listed_amazon, listed_ebay, listed_facebook FROM bol_items WHERE upc = ? COLLATE NOCASE', (upc,))
        row = cur.fetchone()
        current_state = {
            'listed_amazon': row[0] if row else 0,
            'listed_ebay': row[1] if row else 0,
            'listed_facebook': row[2] if row else 0
        }
        
        
        print(f"[list_status] Updated {updated} rows, new state: {current_state}")
        
        # CRITICAL: Clear the cache for /api/bol_items to show new data immediately
        # The 1-year cache was preventing checkbox states from persisting
        cache.clear()
        
        # Invalidate cache so changes are immediately visible
        update_data_version()
        return jsonify({'success': True, 'updated': updated, 'current_state': current_state})
    except Exception as e:
        print(f"[list_status] Error: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': _safe_error(e)}), 500
    finally:
        conn.close()

@app.route('/api/debug/migrate_listings', methods=['GET'])
@require_debug_mode
def api_debug_migrate_listings():
    """Force run the listing migration and return results."""
    try:
        _ensure_bol_list_status_column()
        update_data_version()
        
        # Check results
        conn = sqlite3.connect('bol.db')
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM bol_items WHERE listed_amazon = 1")
        amazon_count = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM bol_items WHERE list_status = 'listed'")
        legacy_count = cur.fetchone()[0]
        
        return jsonify({
            'success': True, 
            'message': 'Migration ran. Check server logs for details.',
            'amazon_listed_count': amazon_count,
            'legacy_listed_count': legacy_count
        })
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': _safe_error(e)}), 500
    finally:
        conn.close()

@app.route('/api/debug/check_item/<upc>', methods=['GET'])
@require_debug_mode
def api_debug_check_item(upc):
    """Check the current marketplace state of a specific item."""
    try:
        upc = _normalize_upc(upc)
        conn = sqlite3.connect('bol.db')
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute('''
            SELECT upc, list_status, listed_amazon, listed_amazon_date, 
                   listed_ebay, listed_ebay_date, listed_facebook, listed_facebook_date
            FROM bol_items WHERE upc = ? COLLATE NOCASE
        ''', (upc,))
        row = cur.fetchone()
        
        if not row:
            return jsonify({'success': False, 'error': 'Item not found'}), 404, 404
        
        return jsonify({
            'success': True,
            'upc': row['upc'],
            'list_status': row['list_status'],
            'listed_amazon': row['listed_amazon'],
            'listed_amazon_date': row['listed_amazon_date'],
            'listed_ebay': row['listed_ebay'],
            'listed_ebay_date': row['listed_ebay_date'],
            'listed_facebook': row['listed_facebook'],
            'listed_facebook_date': row['listed_facebook_date']
        })
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': _safe_error(e)}), 500
    finally:
        conn.close()

@app.route('/api/cleanup_temporary_entry', methods=['POST'])
def api_cleanup_temporary_entry():
    """Delete a temporary bol_items entry that was created but not completed."""
    try:
        # Handle both JSON and form data (sendBeacon can send either)
        if request.is_json:
            data = request.get_json() or {}
        else:
            # Try to parse as JSON from raw data
            try:
                data = json.loads(request.data.decode('utf-8'))
            except Exception:
                data = {}
        
        upc = _normalize_upc(data.get('upc'))
        if not upc:
            print('[CLEANUP] No UPC provided')
            return jsonify({'success': False, 'error': 'Missing upc'}), 400
        
        print(f'[CLEANUP] Attempting to delete temporary entry: {upc}')
        
        conn = sqlite3.connect('bol.db')
        cur = conn.cursor()
        # Only delete if it's marked as temporary
        cur.execute('DELETE FROM bol_items WHERE upc = ? COLLATE NOCASE AND temporary = 1', (upc,))
        deleted = cur.rowcount
        conn.commit()
        
        print(f'[CLEANUP] Deleted {deleted} temporary entries for UPC: {upc}')
        return jsonify({'success': True, 'deleted': deleted})
    except Exception as e:
        print(f'[CLEANUP] Error: {e}')
        return jsonify({'success': False, 'error': _safe_error(e)}), 500
    finally:
        conn.close()
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
        return jsonify({'error': _safe_error(e)}), 500
    finally:
        conn.close()

@app.route("/token-status")
@require_debug_mode
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
        return jsonify({"error": _safe_error(e, 'token refresh')}), 500

@app.route("/api/token-status")
@cache.cached(timeout=600)  # Cache for 10 minutes - avoids expensive external API calls
def api_token_status():
    """Check if eBay and Amazon tokens are valid/working."""
    status = {"ebay": "ok", "amazon": "ok"}

    # Check eBay token
    try:
        tokens = load_tokens()
        if is_expired(tokens):
            # Try to refresh
            get_access_token()
        # Quick validation: call a lightweight eBay endpoint matching our scopes
        test_headers = {
            "Authorization": f"Bearer {tokens.get('access_token', '')}",
            "Content-Type": "application/json"
        }
        r = requests.get("https://api.ebay.com/sell/fulfillment/v1/order?limit=1", headers=test_headers, timeout=5)
        if r.status_code == 401:
            status["ebay"] = "expired"
    except Exception as e:
        status["ebay"] = "expired"

    # Check Amazon token
    try:
        amazon = AmazonManager()
        # Try a lightweight SP-API call
        orders_api = Orders(credentials=amazon.credentials, marketplace=amazon.marketplace)
        orders_api.get_orders(CreatedAfter=(datetime.datetime.utcnow() - datetime.timedelta(minutes=5)).isoformat(), MaxResultsPerPage=1)
    except Exception as e:
        err_str = str(e).lower()
        if "unauthorized" in err_str or "invalid_grant" in err_str or "access denied" in err_str or "token" in err_str:
            status["amazon"] = "expired"
        else:
            # Could be a rate limit or other transient error, don't flag as expired
            pass

    return jsonify(status)

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
    from markupsafe import escape
    return f"Authorization code: {escape(code)}"
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
        return jsonify({'success': False, 'message': 'Enrichment already running'}), 409
    started = _run_enrich_in_background()
    if not started:
        return jsonify({'success': False, 'message': 'Failed to start enrichment'}), 500
    return jsonify({'success': True, 'message': 'Enrichment started'})


@app.route('/api/enrich_status', methods=['GET'])
def api_enrich_status():
    return jsonify(_enrich_status)

# Optimized image loading for Raspberry Pi (prevents memory exhaustion)
def load_image_efficiently(image_path, max_size=(1920, 1920), convert_rgb=True):
    """
    Load and resize image efficiently to prevent memory exhaustion on Pi.
    Uses thumbnail() to resize in-place without loading full image into memory.
    """
    try:
        img = Image.open(image_path)
        
        # Resize large images before converting (saves memory)
        if img.size[0] > max_size[0] or img.size[1] > max_size[1]:
            img.thumbnail(max_size, Image.Resampling.LANCZOS)
        
        # Convert to RGB if needed
        if convert_rgb and img.mode != 'RGB':
            img = img.convert('RGB')
        
        return img
    except Exception as e:
        raise Exception(f"Failed to load image {image_path}: {e}")

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
        # Use optimized image loading (memory-efficient for Pi)
        img = load_image_efficiently(image_path, max_size=(1920, 1920), convert_rgb=True)
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
    try:
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
    finally:
        conn.close()
    return render_template("inventory.html", items=items)


# inventory flow variables stored in Flask session to avoid race conditions
# Keys: 'inv_same_position', 'inv_position_code', 'inv_barcode', 'inv_pictureposition_path'




@app.route('/additemtrue', methods=['POST'])
def additemtrue():
    # Get values from form data (preferred) or fall back to session variables
    form_barcode = request.form.get('barcode', '').strip()
    form_position = request.form.get('item_position', '').strip()
    form_pictureposition = request.form.get('pictureposition', '').strip()

    # Use form data if available, otherwise use session variables
    final_barcode = form_barcode or session.get('inv_barcode')
    final_position = form_position or session.get('inv_position_code')
    final_pictureposition = form_pictureposition or session.get('inv_pictureposition_path')
    same_position = session.get('inv_same_position', False)
    
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
                img = load_image_efficiently(abs_path, max_size=(400, 400), convert_rgb=False)
                img = img.convert('L')  # Convert to grayscale
                img.save(abs_path, optimize=True, quality=40)
            except Exception as e:
                print(f"Image processing failed: {e}")
        print("Flow Complete, adding to SearchRack....")
        # If picture position is set, store 'picture' in ITEM_POSITION
        item_position_to_store = 'picture' if final_pictureposition else final_position
        
        # Add each barcode to SearchRack
        for barcode_item in barcodes:
            addToSearchRack(item_position_to_store, barcode_item, None, final_pictureposition)
            print(f"Added to searchRack: position={item_position_to_store}, barcode={barcode_item}, pictureposition={final_pictureposition}")
        
        # Clear session variables (but keep position if locked)
        if not same_position:
            session.pop('inv_position_code', None)
            session.pop('inv_pictureposition_path', None)
        session.pop('inv_barcode', None)
    except Exception as e:
        print("something went wrong with adding to RACK", e)
        import traceback
        traceback.print_exc()
        _safe_error(e)
        return "An internal error occurred", 500
    # Check if this is a fetch request (multi-scan mode) or form submission
    if request.headers.get('Accept') == '*/*' or request.is_json or 'fetch' in request.headers.get('Sec-Fetch-Mode', ''):
        # Fetch request - return JSON success
        return jsonify({'success': True, 'message': 'Item added successfully'})
    
    # Traditional form submission - return HTML
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
        # After successful add with locked shelf, unlock and restart from position page
        session['inv_same_position'] = False  # Unlock on server side
        redirect_script = '''<script>
        // Clear all session storage including lock state
        sessionStorage.removeItem('barcode');
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




@app.route("/barcode")
def barcode_page():
    # If redirected from pictureposition, pictureposition_path is already in session
    if request.args.get("pictureposition") == "1":
        # Will be handled by JS below
        pass
    return render_template("barcode.html")

@app.route("/pictures")
def pictures_page():
    return render_template("pictures.html")

#adding inventory flow #1/3
@app.route("/position")
def position_page():
    # Add cache-busting for template updates
    response = make_response(render_template("position.html"))
    response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    response.headers['Pragma'] = 'no-cache'
    response.headers['Expires'] = '0'
    return response


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
                    try:
                        removed_conn = sqlite3.connect('rackhistory.db')
                        removed_cur = removed_conn.cursor()
                        _ensure_removed_items_table(removed_cur)
                        removed_cur.execute('''
                            INSERT INTO removed_items 
                            (order_id, barcode, title, quantity_removed, removed_at, searchrack_id, old_quantity, new_quantity, removal_type, item_position)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        ''', (None, upc, title, 1, ts, new_id, 0, 1, 'add_to_shelf', pictureposition))
                        removed_conn.commit()
                        print(f"✅ Logged add-to-shelf (picture) to history")
                    except Exception as log_err:
                        print(f"❌ Error logging add-to-shelf to removed_items: {log_err}")
                        import traceback
                        traceback.print_exc()
                    finally:
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
                            _ensure_removed_items_table(removed_cur)
                            removed_cur.execute('''
                                INSERT INTO removed_items 
                                (order_id, barcode, title, quantity_removed, removed_at, searchrack_id, old_quantity, new_quantity, removal_type, item_position)
                                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                            _ensure_removed_items_table(removed_cur)
                            removed_cur.execute('''
                                INSERT INTO removed_items 
                                (order_id, barcode, title, quantity_removed, removed_at, searchrack_id, old_quantity, new_quantity, removal_type, item_position)
                                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
            except Exception as e:
                print(f'Warning: Failed to update searchRack: {e}')
            finally:
                search_conn.close()
        
        # Redirect back to the diagnostic page
        if return_url:
            return redirect(return_url)
        else:
            return redirect(f'/item-prep/diagnostic?upc={upc}')
    except Exception as e:
        return jsonify({'success': False, 'error': _safe_error(e)}), 500
    finally:
        conn.close()


#inventory flow #2
@app.route('/submitposition', methods=['POST'])
def process_position():
    session['inv_position_code'] = request.form.get('scanned_result')
    session['inv_pictureposition_path'] = request.form.get('pictureposition')
    position_locked = request.form.get('position_locked', 'false') == 'true'

    print("Received scanned code:", session.get('inv_position_code'))
    print("Received picture position path:", session.get('inv_pictureposition_path'))
    print(f"DEBUG: position_locked from form = {position_locked}")
    print(f"DEBUG: inv_same_position (session) = {session.get('inv_same_position')}")

    # Use form value as primary source, fall back to session variable
    is_locked = position_locked or session.get('inv_same_position', False)
    
    # Check if shelf is locked
    if is_locked:
        # Shelf is locked - go to multibarcode page
        print("DEBUG: Redirecting to multibarcode.html (locked)")
        return render_template("multibarcode.html")
    else:
        # Normal flow - go to barcode page
        print("DEBUG: Redirecting to barcode.html")
        return render_template("barcode.html")


#inventory flow #3
@app.route('/submitbarcode', methods=['POST'])
def process_barcode():
    session['inv_barcode'] = request.form.get('scanned_result')
    print("Received scanned code:", session.get('inv_barcode'))

    return render_template("additem.html")



@app.route('/additem', methods=['POST'])
def additem_page():
    #return pictures

    return render_template("additem.html")


@app.route('/additem-multi', methods=['GET'])
def additem_multi_page():
    """Page for multi-barcode adds when shelf is locked"""
    return render_template("additem_multi.html")


@app.route('/multibarcode', methods=['GET'])
def multibarcode_page():
    """Multi-barcode scanning page with list building"""
    return render_template("multibarcode.html")


@app.route('/toggle', methods=['POST'])
def toggle():
    data = request.get_json()
    is_checked = data.get('checked', False)
    session['inv_same_position'] = is_checked
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
    except Exception as alter_e:
        print(f"Failed to ensure UPC/UPC_Processed columns exist: {alter_e}")
    finally:
        conn.close()
    # Query for missing UPCs and not processed
    try:
        conn = sqlite3.connect('ebayStore.db')
        cur = conn.cursor()
        cur.execute("SELECT ItemID FROM INVENTORY WHERE (UPC IS NULL OR UPC = '' OR UPC = 'null') AND (UPC_Processed IS NULL OR UPC_Processed = 0)")
        item_ids_missing_upc = set(row[0] for row in cur.fetchall() if row[0])
    except Exception as e:
        print(f"Failed to get ItemIDs missing UPC: {e}")
        item_ids_missing_upc = set()
    finally:
        conn.close()
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






        def _process_ebay_items(items, list_state):
            """Parse eBay XML items, store in DB, and print debug info."""
            nonlocal count
            for item in items:
                title = item.find('ebay:Title', ns)
                item_id = item.find('ebay:ItemID', ns)
                sku = item.find('ebay:SKU', ns)
                price = item.find('.//ebay:CurrentPrice', ns)
                quantity = item.find('ebay:Quantity', ns)
                list_date = item.find('ebay:ListingDetails/ebay:StartTime', ns)
                sold_date = item.find('ebay:ListingDetails/ebay:EndTime', ns)
                URL = f"https://www.ebay.com/itm/{item_id.text}"
                picture_url = item.find('.//ebay:PictureDetails/ebay:GalleryURL', ns)
                high_res_url = get_high_res_image_url(picture_url.text) if picture_url is not None else "No image"

                ebayStoreDB(title=title.text if title is not None else "N/A",
                           item_id=item_id.text if item_id is not None else "N/A",
                           sku=sku.text if sku is not None else "None",
                           price=price.text if price is not None else "N/A",
                           quantity=quantity.text if quantity is not None else "N/A",
                           image=high_res_url,
                           List_State=list_state,
                           Sold_Date=sold_date.text if sold_date is not None else "None",
                           List_Date=list_date.text if list_date is not None else "None",
                           URL=URL if URL is not None else "None")

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

        #ACTIVE LIST ITEMS
        ActiveItems = root.findall('.//ebay:ActiveList/ebay:ItemArray/ebay:Item', ns)
        _process_ebay_items(ActiveItems, "Active")

        # finding unsold item list
        UnsoldItems = root.findall('.//ebay:UnsoldList/ebay:ItemArray/ebay:Item', ns)
        _process_ebay_items(UnsoldItems, "Unsold")

        # finding sold list items
        SoldItems = root.findall('.//ebay:SoldList/ebay:OrderTransactionArray/ebay:OrderTransaction/ebay:Transaction/ebay:Item', ns)
        _process_ebay_items(SoldItems, "Sold")

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
        except Exception as alter_e:
            print(f"Failed to ensure UPC column exists: {alter_e}")
        finally:
            conn.close()

        # Only fetch UPCs for items that do not already have a UPC and not processed
        # Filter only those in both all_item_ids and item_ids_missing_upc, and not already processed
        to_lookup = [eid for eid in all_item_ids if eid in item_ids_missing_upc and eid not in processed_upc_ids]
        if to_lookup:
            from DBmanager import connect_db
            with connect_db('ebayStore.db') as upc_conn:
                upc_cur = upc_conn.cursor()
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
                        upc_cur.execute("UPDATE INVENTORY SET UPC = ?, UPC_Processed = 1 WHERE ItemID = ?", (upc, eid))
                        print(f"Updated UPC for ItemID {eid} in ebayStore.db and marked as processed")
                        processed_upc_ids.add(eid)
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
        print("✅ Barcode finalization complete.")
    except Exception as e:
        print(f"❌ Error finalizing barcodes: {e}")
    finally:
        conn.close()

def ensure_lifecycle_tables():
    """Ensure returns table has lifecycle columns and create lifecycle events table"""
    try:
        conn = sqlite3.connect('sold.db')
        cur = conn.cursor()
        
        # Add lifecycle columns to returns table if they don't exist
        try:
            cur.execute("ALTER TABLE returns ADD COLUMN relisted INTEGER DEFAULT 0")
        except sqlite3.OperationalError:
            pass  # Column already exists
        
        try:
            cur.execute("ALTER TABLE returns ADD COLUMN relisted_date TEXT")
        except sqlite3.OperationalError:
            pass
        
        try:
            cur.execute("ALTER TABLE returns ADD COLUMN relisted_store TEXT")
        except sqlite3.OperationalError:
            pass
        
        try:
            cur.execute("ALTER TABLE returns ADD COLUMN relisted_item_id TEXT")
        except sqlite3.OperationalError:
            pass
        
        try:
            cur.execute("ALTER TABLE returns ADD COLUMN resold INTEGER DEFAULT 0")
        except sqlite3.OperationalError:
            pass
        
        try:
            cur.execute("ALTER TABLE returns ADD COLUMN resold_date TEXT")
        except sqlite3.OperationalError:
            pass
        
        try:
            cur.execute("ALTER TABLE returns ADD COLUMN resold_order_id TEXT")
        except sqlite3.OperationalError:
            pass
        
        try:
            cur.execute("ALTER TABLE returns ADD COLUMN lifecycle_count INTEGER DEFAULT 1")
        except sqlite3.OperationalError:
            pass
        
        # Create lifecycle events table
        cur.execute('''
            CREATE TABLE IF NOT EXISTS return_lifecycle_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                return_id INTEGER NOT NULL,
                event_type TEXT NOT NULL,
                event_date TEXT NOT NULL,
                auto_detected INTEGER DEFAULT 0,
                store TEXT,
                item_id TEXT,
                order_id TEXT,
                notes TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (return_id) REFERENCES returns (id) ON DELETE CASCADE
            )
        ''')
        
        # Create payouts table
        cur.execute('''
            CREATE TABLE IF NOT EXISTS payouts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                store TEXT NOT NULL,
                settlement_id TEXT UNIQUE NOT NULL,
                start_date TEXT,
                end_date TEXT,
                payout_date TEXT,
                amount REAL DEFAULT 0,
                currency TEXT DEFAULT 'USD',
                status TEXT,
                transaction_count INTEGER DEFAULT 0,
                synced_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        conn.commit()
        print("✅ Lifecycle tables initialized")
        print("✅ Payouts table initialized")
    except Exception as e:
        print(f"⚠️ Error initializing lifecycle tables: {e}")
    finally:
        conn.close()

def start_flask():
    app.run(host="0.0.0.0", port=8080)

def start_tunnel():
    config_path = os.getenv('CLOUDFLARED_CONFIG', os.path.normpath(os.path.expanduser('~/.cloudflared/config.yml')))
    subprocess.Popen([
        "cloudflared",
        "tunnel",
        "--config",
        config_path,
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
        return jsonify({'success': False, 'error': 'BOL extractor not available (pandas not installed)'}), 500
    if 'excel_file' not in request.files or 'import_date' not in request.form:
        return jsonify({'success': False, 'error': 'Missing file or import date.'}), 400
    file = request.files['excel_file']
    import_date = request.form['import_date']
    if file.filename == '':
        return jsonify({'success': False, 'error': 'No file selected.'}), 400
    if file:
        print('DEBUG: Received file:', file.filename, 'Content-Type:', file.content_type, 'Size:', file.content_length)
        result = process_bol_excel(file, import_date)
        return jsonify(result)
    return jsonify({'success': False, 'error': 'Unknown error during file upload.'}), 500

@app.route('/api/rawbol/upload', methods=['POST'])
def api_rawbol_upload():
    """Upload .xls file to raw BOL database and auto-sync"""
    if not BOL_AVAILABLE:
        return jsonify({'success': False, 'error': 'BOL extractor not available (pandas not installed)'}), 500
    
    if 'excel_file' not in request.files or 'import_date' not in request.form:
        return jsonify({'success': False, 'error': 'Missing file or import date.'}), 400
    
    file = request.files['excel_file']
    import_date = request.form['import_date'].strip()
    shipping_cost_str = request.form.get('shipping_cost', '').strip()
    
    # Parse shipping cost if provided
    shipping_cost = None
    if shipping_cost_str:
        try:
            shipping_cost = float(shipping_cost_str)
        except ValueError:
            return jsonify({'success': False, 'error': 'Invalid shipping cost value.'}), 400
    
    if file.filename == '':
        return jsonify({'success': False, 'error': 'No file selected.'}), 400
    
    print(f'DEBUG: Received file: {file.filename}, Date: {import_date}, Shipping: {shipping_cost}')
    
    # Upload to rawbol.db - lot_number will be extracted from file
    # Use filename without extension as temporary lot_number for database storage
    import os
    temp_lot_number = os.path.splitext(file.filename)[0]
    
    result = process_bol_excel(file, temp_lot_number, import_date, shipping_cost)
    
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

@app.route('/api/rawbol/update/<lot_number>', methods=['PUT'])
def api_rawbol_update_lot(lot_number):
    """Update LOT information (name, date, shipping cost)"""
    try:
        data = request.get_json() or {}
        new_lot_number = data.get('lot_number', '').strip()
        import_date = data.get('import_date', '').strip()
        shipping_cost_str = data.get('shipping_cost', '')
        
        # Parse shipping cost
        shipping_cost = None
        if shipping_cost_str != '' and shipping_cost_str is not None:
            try:
                shipping_cost = float(shipping_cost_str)
            except ValueError:
                return jsonify({'success': False, 'error': 'Invalid shipping cost value.'}), 400, 400
        
        conn = sqlite3.connect('rawbol.db')
        cur = conn.cursor()
        
        # Update upload_logs
        update_fields = []
        update_values = []
        
        if new_lot_number and new_lot_number != lot_number:
            # Check if new lot number already exists
            cur.execute('SELECT COUNT(*) as count FROM upload_logs WHERE lot_number = ?', (new_lot_number,))
            if cur.fetchone()[0] > 0:
                return jsonify({'success': False, 'error': f'LOT # "{new_lot_number}" already exists.'}), 400
            update_fields.append('lot_number = ?')
            update_values.append(new_lot_number)
        
        if import_date:
            update_fields.append('import_date = ?')
            update_values.append(import_date)
        
        if shipping_cost is not None:
            update_fields.append('shipping_cost = ?')
            update_values.append(shipping_cost)
        
        if not update_fields:
            return jsonify({'success': False, 'error': 'No fields to update.'}), 400
        
        # Update upload_logs
        update_values.append(lot_number)  # WHERE clause
        cur.execute(f"UPDATE upload_logs SET {', '.join(update_fields)} WHERE lot_number = ?", update_values)
        
        # If lot_number changed, also update raw_bol_items
        if new_lot_number and new_lot_number != lot_number:
            cur.execute('UPDATE raw_bol_items SET lot_number = ? WHERE lot_number = ?', (new_lot_number, lot_number))
        
        if import_date and (new_lot_number and new_lot_number != lot_number or not new_lot_number):
            # Update import_date in raw_bol_items
            cur.execute('UPDATE raw_bol_items SET import_date = ? WHERE lot_number = ?', 
                       (import_date, new_lot_number if new_lot_number else lot_number))
        
        conn.commit()
        
        return jsonify({
            'success': True,
            'message': 'LOT information updated successfully.',
            'new_lot_number': new_lot_number if new_lot_number else lot_number
        })
        
    except Exception as e:
        return jsonify({'success': False, 'error': _safe_error(e)}), 500
    finally:
        conn.close()

@app.route('/api/rawbol/desync', methods=['POST'])
def api_rawbol_desync_all():
    """Desync (undo) ALL raw BOL from main BOL database"""
    from rawbol_manager import desync_all_rawbol
    result = desync_all_rawbol()
    return jsonify(result)

@app.route('/api/sold/enrich-lot-numbers', methods=['POST'])
def api_sold_enrich_lot_numbers():
    """Backfill LOT numbers for all sold orders using smart matching"""
    try:
        from lot_matcher import backfill_all_sold_orders
        result = backfill_all_sold_orders()
        return jsonify(result)
    except Exception as e:
        return jsonify({'success': False, 'error': _safe_error(e)}), 500

@app.route('/api/sold/enrich-recent', methods=['POST'])
def api_sold_enrich_recent():
    """Enrich recent orders (last 7 days) with LOT numbers"""
    try:
        from lot_matcher import enrich_new_orders
        days_back = int(request.args.get('days', 7))
        result = enrich_new_orders(days_back=days_back)
        return jsonify(result)
    except Exception as e:
        return jsonify({'success': False, 'error': _safe_error(e)}), 500

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
                avg_cost
            FROM raw_bol_items 
            WHERE bol_number = ?
            ORDER BY id
        ''', (bol_number,))
        items = [dict(row) for row in cur.fetchall()]
        
        # Calculate total quantity for avg cost calculation
        total_qty = sum(item['original_qty'] or 0 for item in items)
        avg_cost = (total_bol_cost / total_qty) if total_qty > 0 and total_bol_cost else 0
        
        
        return jsonify({
            'success': True,
            'items': items,
            'total_bol_cost': total_bol_cost,
            'total_qty': total_qty,
            'avg_cost': avg_cost
        })
    except Exception as e:
        return jsonify({'success': False, 'error': _safe_error(e)}), 500
    finally:
        conn.close()



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
        return jsonify({'success': False, 'error': _safe_error(e)}), 500


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
        return jsonify({'success': False, 'error': _safe_error(e)}), 500


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
        return jsonify({'success': False, 'error': _safe_error(e)}), 500


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
        return jsonify({'success': False, 'error': _safe_error(e)}), 500


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
        
        return jsonify({
            'success': True,
            'orders': [dict(order) for order in orders]
        })
    except Exception as e:
        return jsonify({'success': False, 'error': _safe_error(e)}), 500
    finally:
        conn.close()


@app.route('/sold-orders', methods=['GET'])
@cache.cached(timeout=300, query_string=True)  # Cache for 5 minutes based on query params (days parameter)
def sold_orders():
    days = int(request.args.get('days', 1))
    
    # Get orders from sold.db
    sold_conn = sqlite3.connect('sold.db')
    try:
        sold_conn.row_factory = sqlite3.Row
        sold_cur = sold_conn.cursor()
        # Only show orders from the last N days
        sold_cur.execute('''SELECT * FROM orders WHERE paid_time >= date('now', '-' || ? || ' days') ORDER BY paid_time DESC''', (days,))
        orders = sold_cur.fetchall()
    finally:
        sold_conn.close()
    
    # Enrich with location from searchRack.db
    result = []
    try:
        rack_conn = sqlite3.connect('searchRack.db')
        rack_conn.row_factory = sqlite3.Row
        rack_cur = rack_conn.cursor()
        
        for order in orders:
            order_dict = dict(order)
            
            # If location is empty and barcode exists, look it up in searchRack
            if (not order_dict.get('location') or order_dict.get('location', '').strip() == '') and order_dict.get('barcode'):
                try:
                    # Try original barcode first (searchRack stores without leading zeros)
                    rack_cur.execute('SELECT ITEM_POSITION, PICTUREPOSITION, QUANTITY FROM SEARCHRACK WHERE BARCODE = ? COLLATE NOCASE', (order_dict['barcode'],))
                    rack_rows = rack_cur.fetchall()
                    
                    # If not found, try padded version
                    if not rack_rows and order_dict['barcode'] and order_dict['barcode'].isdigit():
                        barcode_padded = order_dict['barcode'].zfill(12)
                        rack_cur.execute('SELECT ITEM_POSITION, PICTUREPOSITION, QUANTITY FROM SEARCHRACK WHERE BARCODE = ? COLLATE NOCASE', (barcode_padded,))
                        rack_rows = rack_cur.fetchall()
                    
                    if rack_rows:
                        # Collect all locations for this barcode
                        locations = []
                        for rack_row in rack_rows:
                            loc = rack_row['ITEM_POSITION'] or rack_row['PICTUREPOSITION']
                            if loc:
                                locations.append({
                                    'code': loc,
                                    'image': rack_row['PICTUREPOSITION'] if rack_row['PICTUREPOSITION'] and rack_row['PICTUREPOSITION'].strip() else loc,
                                    'quantity': rack_row['QUANTITY'] if rack_row['QUANTITY'] else 1
                                })
                        
                        # Store as JSON array if multiple locations, or single string for backward compatibility
                        if len(locations) > 1:
                            order_dict['locations'] = locations  # Array of location objects
                            order_dict['location'] = locations[0]['code']  # First location for backward compatibility
                        elif len(locations) == 1:
                            order_dict['location'] = locations[0]['code']
                            order_dict['location_image'] = locations[0]['image']
                except sqlite3.Error as e:
                    # If searchRack query fails, just skip location lookup for this order
                    print(f"Warning: Failed to lookup location for barcode {order_dict['barcode']}: {e}")

            # If still no location, flag for manual search via Finder page
            if not order_dict.get('location') or order_dict.get('location', '').strip() == '':
                if order_dict.get('title'):
                    order_dict['location_search'] = order_dict['title']

            result.append(order_dict)
        
    except Exception as e:
        # If searchRack.db is unavailable, return orders without location enrichment
        print(f"Warning: searchRack.db unavailable, skipping location lookup: {e}")
        result = [dict(order) for order in orders]
    finally:
        rack_conn.close()

    return jsonify(result)


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
        
        if not order:
            return jsonify({'error': 'Order not found'}), 404
            
        # Convert sqlite3.Row to dict
        order_dict = dict(order)
        return jsonify(order_dict)
    except Exception as e:
        return jsonify({'error': _safe_error(e)}), 500
    finally:
        conn.close()

@app.route('/mark-order-handled', methods=['POST'])
def mark_order_handled():
    data = request.get_json()
    order_id = data.get('id')
    
    if not order_id:
        return jsonify({'success': False, 'error': 'Missing order id'}), 400
        
    try:
        conn = sqlite3.connect('sold.db')
        cur = conn.cursor()
        # Set shipped_time if not already set (for test orders to trigger automatic removal)
        cur.execute("""
            UPDATE orders 
            SET isHandled = '1', 
                isHandledDate = datetime('now'),
                shipped_time = COALESCE(shipped_time, datetime('now'))
            WHERE id = ?
        """, (order_id,))
        conn.commit()
        
        # Clear the sold-orders cache since data changed
        cache.delete_memoized(sold_orders)
        
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': _safe_error(e)}), 500
    finally:
        conn.close()

@app.route('/mark-order-unhandled', methods=['POST'])
def mark_order_unhandled():
    data = request.get_json()
    order_id = data.get('id')
    
    if not order_id:
        return jsonify({'success': False, 'error': 'Missing order id'}), 400
        
    try:
        conn = sqlite3.connect('sold.db')
        cur = conn.cursor()
        cur.execute("UPDATE orders SET isHandled = '', isHandledDate = NULL WHERE id = ?", (order_id,))
        conn.commit()
        
        # Clear the sold-orders cache since data changed
        cache.delete_memoized(sold_orders)
        
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': _safe_error(e)}), 500
    finally:
        conn.close()

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
            grace_period_hours = float(row[0]) if row and str(row[0]).strip() else 48
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
        now = datetime.datetime.now()
        
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
                
                # Always convert to naive datetime for comparison
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
        
        return jsonify({'success': True, 'orders': result, 'grace_hours': grace_period_hours})
    except Exception as e:
        return jsonify({'success': False, 'error': _safe_error(e)}), 500
    finally:
        conn.close()

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
            return jsonify({'success': False, 'error': 'Order not found'}), 404
        
        return jsonify({'success': True, 'message': 'Automatic removal cancelled'})
    except Exception as e:
        return jsonify({'success': False, 'error': _safe_error(e)}), 500
    finally:
        conn.close()

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
            return jsonify({'success': False, 'error': 'Order not found'}), 404
        
        return jsonify({'success': True, 'message': 'Automatic removal re-enabled'})
    except Exception as e:
        return jsonify({'success': False, 'error': _safe_error(e)}), 500
    finally:
        conn.close()

@app.route('/api/sold/trigger-automatic-removal', methods=['POST'])
def trigger_automatic_removal():
    """Manually trigger automatic inventory removal process (for testing)"""
    try:
        _process_automatic_inventory_removals()
        return jsonify({'success': True, 'message': 'Automatic removal process triggered'})
    except Exception as e:
        return jsonify({'success': False, 'error': _safe_error(e)}), 500

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
        hours = float(row[0]) if row and str(row[0]).strip() else 48
        return jsonify({'success': True, 'hours': hours})
    except Exception as e:
        return jsonify({'success': False, 'error': _safe_error(e)}), 500
    finally:
        conn.close()

@app.route('/api/grace_period', methods=['POST'])
def api_set_grace_period():
    try:
        data = request.get_json(force=True) if request.is_json else {}
        hours = float(data.get('hours', 48))  # Allow fractional hours for testing (e.g., 0.0167 = 1 minute)
        if hours < 0 or hours > 240:
            return jsonify({'success': False, 'error': 'hours out of range (0-240)'}), 400
        conn = sqlite3.connect('sold.db')
        cur = conn.cursor()
        cur.execute("CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT)")
        conn.commit()
        cur.execute("INSERT INTO settings (key, value) VALUES ('removal_grace_hours', ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value", (str(hours),))
        conn.commit()
        return jsonify({'success': True, 'hours': hours})
    except Exception as e:
        return jsonify({'success': False, 'error': _safe_error(e)}), 500
    finally:
        conn.close()

# Zero-Quantity Deletion Settings API
@app.route('/api/zero_qty_settings', methods=['GET'])
def api_get_zero_qty_settings():
    try:
        conn = sqlite3.connect('searchRack.db')
        cur = conn.cursor()
        cur.execute('''
            CREATE TABLE IF NOT EXISTS zero_qty_settings (
                key TEXT PRIMARY KEY,
                value TEXT
            )
        ''')
        
        # Get show_display setting
        cur.execute("SELECT value FROM zero_qty_settings WHERE key = 'show_display'")
        row = cur.fetchone()
        show_display = row and row[0] == 'true'
        
        # Get interval_minutes setting
        cur.execute("SELECT value FROM zero_qty_settings WHERE key = 'interval_minutes'")
        row = cur.fetchone()
        interval_minutes = int(row[0]) if row else 1440
        
        
        return jsonify({
            'success': True,
            'show_display': show_display,
            'interval_minutes': interval_minutes
        })
    except Exception as e:
        return jsonify({'success': False, 'error': _safe_error(e)}), 500
    finally:
        conn.close()

@app.route('/api/zero_qty_settings', methods=['POST'])
def api_set_zero_qty_settings():
    try:
        data = request.get_json() or {}
        show_display = data.get('show_display', False)
        interval_minutes = int(data.get('interval_minutes', 1440))
        
        conn = sqlite3.connect('searchRack.db')
        cur = conn.cursor()
        cur.execute('''
            CREATE TABLE IF NOT EXISTS zero_qty_settings (
                key TEXT PRIMARY KEY,
                value TEXT
            )
        ''')
        
        cur.execute('''
            INSERT INTO zero_qty_settings (key, value) 
            VALUES ('show_display', ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
        ''', ('true' if show_display else 'false',))
        
        cur.execute('''
            INSERT INTO zero_qty_settings (key, value) 
            VALUES ('interval_minutes', ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
        ''', (str(interval_minutes),))
        
        conn.commit()
        
        # Update the deletion intervals for items already in the queue
        if interval_minutes != 1440:  # If not default 24 hours
            from datetime import datetime, timedelta
            cur.execute('SELECT id, marked_at FROM zero_qty_pending_deletion')
            pending_items = cur.fetchall()
            
            for item_id, marked_at in pending_items:
                marked_dt = datetime.fromisoformat(marked_at)
                new_delete_at = marked_dt + timedelta(minutes=interval_minutes)
                cur.execute('UPDATE zero_qty_pending_deletion SET delete_at = ? WHERE id = ?',
                           (new_delete_at.isoformat(), item_id))
            
            conn.commit()
        
        
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': _safe_error(e)}), 500
    finally:
        conn.close()

@app.route('/api/zero_qty_pending', methods=['GET'])
def api_get_zero_qty_pending():
    try:
        conn = sqlite3.connect('searchRack.db')
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        
        # Add deletion_cancelled column if not exists
        try:
            cur.execute('ALTER TABLE zero_qty_pending_deletion ADD COLUMN deletion_cancelled INTEGER DEFAULT 0')
            conn.commit()
        except sqlite3.OperationalError:
            pass  # Column already exists
        
        # Get grace period setting
        cur.execute("SELECT value FROM zero_qty_settings WHERE key = 'interval_minutes'")
        interval_row = cur.fetchone()
        interval_minutes = int(interval_row['value']) if interval_row else 2880  # Default 48 hours
        grace_hours = interval_minutes / 60
        
        cur.execute('''
            SELECT 
                p.id,
                p.searchrack_id,
                p.marked_at,
                p.delete_at,
                COALESCE(p.deletion_cancelled, 0) as deletion_cancelled,
                s.TITLE as title,
                s.BARCODE as barcode,
                s.QUANTITY as quantity,
                s.ITEM_POSITION as position
            FROM zero_qty_pending_deletion p
            LEFT JOIN SEARCHRACK s ON p.searchrack_id = s.ID
            ORDER BY p.delete_at ASC
        ''')
        
        from datetime import datetime
        now = datetime.now()
        
        items = []
        orphaned_ids = []
        for row in cur.fetchall():
            # Skip orphaned entries (where the searchRack item no longer exists)
            if row['title'] is None:
                orphaned_ids.append(row['id'])
                continue
                
            delete_at = datetime.fromisoformat(row['delete_at'])
            time_remaining = (delete_at - now).total_seconds() / 3600
            is_eligible = time_remaining <= 0 and row['deletion_cancelled'] == 0
            
            items.append({
                'id': row['id'],
                'searchrack_id': row['searchrack_id'],
                'marked_at': row['marked_at'],
                'delete_at': row['delete_at'],
                'deletion_cancelled': row['deletion_cancelled'],
                'title': row['title'],
                'barcode': row['barcode'],
                'quantity': row['quantity'],
                'position': row['position'],
                'is_eligible': is_eligible,
                'hours_remaining': max(0, round(time_remaining, 1))
            })
        
        # Clean up any orphaned entries found
        if orphaned_ids:
            for orphan_id in orphaned_ids:
                cur.execute('DELETE FROM zero_qty_pending_deletion WHERE id = ?', (orphan_id,))
            conn.commit()
            print(f"🧹 Cleaned up {len(orphaned_ids)} orphaned pending deletion entries")
        
        
        return jsonify({
            'success': True,
            'items': items,
            'grace_hours': grace_hours
        })
    except Exception as e:
        return jsonify({'success': False, 'error': _safe_error(e)}), 500
    finally:
        conn.close()

@app.route('/api/zero_qty_delete_now/<barcode>', methods=['POST'])
def api_zero_qty_delete_now(barcode):
    """Immediately delete a searchRack item (bypass grace period)"""
    try:
        conn = sqlite3.connect('searchRack.db')
        cur = conn.cursor()
        
        # Find the item by barcode (pad to 12 digits)
        barcode_padded = barcode.zfill(12) if barcode.isdigit() else barcode
        cur.execute('SELECT ID FROM SEARCHRACK WHERE BARCODE = ?', (barcode_padded,))
        row = cur.fetchone()
        
        if not row:
            return jsonify({'success': False, 'error': 'Item not found'}), 404
        
        searchrack_id = row[0]
        
        # Archive the item
        from datetime import datetime
        now = datetime.now()
        
        cur.execute('''
            CREATE TABLE IF NOT EXISTS archived_searchrack (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                original_id INTEGER,
                title TEXT,
                barcode TEXT,
                item_position TEXT,
                images TEXT,
                pictureposition TEXT,
                itemid TEXT,
                quantity INTEGER,
                created_at TEXT,
                image TEXT,
                archived_at TEXT,
                reason TEXT
            )
        ''')
        
        cur.execute('''
            INSERT INTO archived_searchrack 
            (original_id, title, barcode, item_position, images, pictureposition, itemid, quantity, created_at, image, archived_at, reason)
            SELECT ID, TITLE, BARCODE, ITEM_POSITION, IMAGES, PICTUREPOSITION, ITEMID, QUANTITY, CREATED_AT, IMAGE, ?, 'manual_zero_quantity'
            FROM SEARCHRACK WHERE ID = ?
        ''', (now.isoformat(), searchrack_id))
        
        # Get item details for logging
        cur.execute('SELECT TITLE, BARCODE, QUANTITY, ITEM_POSITION FROM SEARCHRACK WHERE ID = ?', (searchrack_id,))
        item_row = cur.fetchone()
        if item_row:
            item_title, item_barcode, old_qty, item_location = item_row
            
            # Log to removed_items for history tracking
            try:
                removed_conn = sqlite3.connect('rackhistory.db')
                removed_cur = removed_conn.cursor()
                _ensure_removed_items_table(removed_cur)
                removed_cur.execute('''
                    INSERT INTO removed_items 
                    (order_id, barcode, title, quantity_removed, removed_at, searchrack_id, old_quantity, new_quantity, removal_type, item_position)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''', (None, item_barcode, item_title, old_qty, now.isoformat(), searchrack_id, old_qty, 0, 'manual', item_location or ''))
                removed_conn.commit()
            except Exception as log_err:
                print(f"Warning: Could not log manual deletion to removed_items: {log_err}")
            finally:
                removed_conn.close()
        
        # Delete from SEARCHRACK
        cur.execute('DELETE FROM SEARCHRACK WHERE ID = ?', (searchrack_id,))
        
        # Remove from pending deletion queue
        cur.execute('DELETE FROM zero_qty_pending_deletion WHERE searchrack_id = ?', (searchrack_id,))
        
        conn.commit()
        
        print(f"✓ Manually deleted searchRack item {searchrack_id} (barcode: {barcode})")
        
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': _safe_error(e)}), 500
    finally:
        conn.close()

@app.route('/api/zero_qty_cancel/<barcode>', methods=['POST'])
def api_zero_qty_cancel(barcode):
    """Cancel automatic deletion for a zero-quantity item"""
    try:
        conn = sqlite3.connect('searchRack.db')
        cur = conn.cursor()
        
        # Find the item by barcode
        barcode_padded = barcode.zfill(12) if barcode.isdigit() else barcode
        cur.execute('SELECT ID FROM SEARCHRACK WHERE BARCODE = ?', (barcode_padded,))
        row = cur.fetchone()
        
        if not row:
            return jsonify({'success': False, 'error': 'Item not found'}), 404
        
        searchrack_id = row[0]
        
        # Update deletion_cancelled flag
        cur.execute('''
            UPDATE zero_qty_pending_deletion 
            SET deletion_cancelled = 1 
            WHERE searchrack_id = ?
        ''', (searchrack_id,))
        
        conn.commit()
        
        print(f"✓ Cancelled automatic deletion for item {searchrack_id} (barcode: {barcode})")
        
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': _safe_error(e)}), 500
    finally:
        conn.close()

@app.route('/api/zero_qty_allow/<barcode>', methods=['POST'])
def api_zero_qty_allow(barcode):
    """Re-enable automatic deletion for a zero-quantity item"""
    try:
        conn = sqlite3.connect('searchRack.db')
        cur = conn.cursor()
        
        # Find the item by barcode
        barcode_padded = barcode.zfill(12) if barcode.isdigit() else barcode
        cur.execute('SELECT ID FROM SEARCHRACK WHERE BARCODE = ?', (barcode_padded,))
        row = cur.fetchone()
        
        if not row:
            return jsonify({'success': False, 'error': 'Item not found'}), 404
        
        searchrack_id = row[0]
        
        # Update deletion_cancelled flag
        cur.execute('''
            UPDATE zero_qty_pending_deletion 
            SET deletion_cancelled = 0 
            WHERE searchrack_id = ?
        ''', (searchrack_id,))
        
        conn.commit()
        
        print(f"✓ Re-enabled automatic deletion for item {searchrack_id} (barcode: {barcode})")
        
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': _safe_error(e)}), 500
    finally:
        conn.close()

@app.route('/api/zero_qty/trigger-purge', methods=['POST'])
def api_trigger_zero_qty_purge():
    """Manually trigger zero-quantity deletion process"""
    try:
        print("🔄 Manual trigger: Running zero-quantity purge...")
        _purge_zero_qty_items()
        return jsonify({'success': True, 'message': 'Zero-quantity purge triggered'})
    except Exception as e:
        return jsonify({'success': False, 'error': _safe_error(e)}), 500

# Inventory History endpoint
@app.route('/api/inventory/history', methods=['GET'])
def api_inventory_history():
    """Get comprehensive history of inventory changes (additions, removals, edits)"""
    try:
        search = (request.args.get('search') or '').strip().lower()
        sort_order = request.args.get('sort', 'desc')  # 'asc' or 'desc'
        
        history_items = []
        
        # Get removal history from rackhistory.db (removed_items table has more detail)
        try:
            rem_conn = sqlite3.connect('rackhistory.db')
            rem_conn.row_factory = sqlite3.Row
            rem_cur = rem_conn.cursor()
            
            # Check if removed_items table exists (more detailed than removed table)
            rem_cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='removed_items'")
            if rem_cur.fetchone():
                # Check if item_position column exists, add it if not
                rem_cur.execute("PRAGMA table_info(removed_items)")
                columns = [col[1] for col in rem_cur.fetchall()]
                if 'item_position' not in columns:
                    rem_cur.execute('ALTER TABLE removed_items ADD COLUMN item_position TEXT')
                    rem_conn.commit()
                
                rem_cur.execute('''
                    SELECT 
                        id, order_id, barcode, title, quantity_removed,
                        removed_at, searchrack_id, old_quantity, new_quantity, removal_type, item_position
                    FROM removed_items
                    ORDER BY removed_at DESC
                    LIMIT 1000
                ''')
                
                for row in rem_cur.fetchall():
                    title = row['title'] or 'Unknown'
                    barcode = row['barcode'] or ''
                    location = row['item_position'] or ''
                    
                    if search and search not in title.lower() and search not in barcode.lower() and search not in location.lower():
                        continue
                    
                    # Determine if this is addition or removal based on old/new quantity
                    old_qty = row['old_quantity'] or 0
                    new_qty = row['new_quantity'] or 0
                    qty_change = new_qty - old_qty
                    
                    # Normalize timestamp for display (convert UTC to local time)
                    display_timestamp = row['removed_at']
                    try:
                        from datetime import datetime, timedelta
                        dt = datetime.fromisoformat(str(display_timestamp))
                        if dt.tzinfo is not None:
                            # Convert UTC to US/Eastern (handles EST/EDT automatically)
                            eastern = pytz.timezone('US/Eastern')
                            dt = dt.astimezone(eastern).replace(tzinfo=None)
                            display_timestamp = dt.isoformat()
                    except Exception:
                        pass  # Keep original if parsing fails
                    
                    # For manual_edit type, show as addition or removal based on qty_change
                    removal_type = row['removal_type'] or 'unknown'
                    
                    # Check if barcode has a suffix (diagnostic flow)
                    is_diagnostic = '-' in barcode and barcode.split('-')[-1].isdigit()
                    
                    if removal_type == 'add_to_shelf':
                        action = 'Added'
                        source = 'Diagnostic' if is_diagnostic else 'Add to Shelf'
                    elif removal_type == 'manual_edit':
                        if qty_change > 0:
                            action = 'Added'
                            source = 'Diagnostic' if is_diagnostic else 'Manual Edit (Addition)'
                        else:
                            action = 'Removed'
                            source = 'Diagnostic' if is_diagnostic else 'Manual Edit (Removal)'
                    elif removal_type == 'manual':
                        action = 'Removed'
                        source = 'Diagnostic' if is_diagnostic else 'Manual Deletion'
                    elif removal_type == 'manual_sold_removal':
                        action = 'Removed'
                        source = 'Manual Sold Removal (Remove Now)'
                    elif removal_type == 'manual_multidb_delete':
                        action = 'Removed'
                        source = 'Multi-DB Search (Gear Icon Delete)'
                    elif removal_type == 'location_edit':
                        if qty_change > 0:
                            action = 'Added'
                            source = 'Location Change (Moved Here)'
                        else:
                            action = 'Removed'
                            source = 'Location Change (Moved Away)'
                    elif removal_type == 'automatic':
                        action = 'Removed'
                        source = 'Automatic Removal'
                    else:
                        action = 'Removed'
                        source = 'Unknown'
                    
                    history_items.append({
                        'id': f"removed_{row['id']}",
                        'type': 'addition' if qty_change > 0 else 'removal',
                        'action': action,
                        'title': title,
                        'barcode': barcode,
                        'location': location,
                        'quantity_change': qty_change,
                        'old_quantity': old_qty,
                        'new_quantity': new_qty,
                        'timestamp': display_timestamp,
                        'method': removal_type,
                        'source': source,
                        'order_id': row['order_id'],
                        'can_undo': False  # Can't undo from this detailed log
                    })
            
        except Exception as e:
            print(f"Error reading removed_items: {e}")
        finally:
            rem_conn.close()
        
        # Sort by timestamp (handle both UTC and local timestamps)
        def parse_timestamp_for_sort(ts):
            """Parse timestamp to datetime, converting UTC to local time for consistent sorting"""
            from datetime import datetime, timedelta
            try:
                if not ts:
                    return datetime.min
                
                # Parse ISO format timestamp
                ts_str = str(ts)
                dt = datetime.fromisoformat(ts_str)
                
                # If timezone-aware (UTC like +00:00), convert to US/Eastern (handles EST/EDT)
                if dt.tzinfo is not None:
                    eastern = pytz.timezone('US/Eastern')
                    dt = dt.astimezone(eastern).replace(tzinfo=None)
                
                return dt
            except Exception:
                return datetime.min
        
        history_items.sort(key=lambda x: parse_timestamp_for_sort(x['timestamp']), reverse=(sort_order == 'desc'))
        
        return jsonify({'success': True, 'items': history_items[:500]})  # Limit to 500 for performance
        
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': _safe_error(e)}), 500

# Removed items: list and page
@app.route('/api/removed', methods=['GET'])
def api_removed_list():
    try:
        conn = sqlite3.connect('rackhistory.db')
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
        return jsonify({'success': True, 'items': rows})
    except Exception as e:
        return jsonify({'success': False, 'error': _safe_error(e)}), 500
    finally:
        conn.close()

@app.route('/removed')
def removed_page():
    try:
        return render_template('removed.html')
    except Exception as e:
        _safe_error(e, 'Error loading page')
        return 'Error loading page', 500

@app.route('/api/removed/undo/<int:rem_id>', methods=['POST'])
def api_removed_undo(rem_id: int):
    """Undo a removal: increment searchRack quantity by qty for the barcode, and mark removed row undone."""
    try:
        # Open rackhistory.db and fetch entry
        rem_conn = sqlite3.connect('rackhistory.db')
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
            return jsonify({'success': False, 'error': 'Removed entry not found'}), 404
        if row['undone_at']:
            return jsonify({'success': False, 'error': 'Already undone'}), 400

        barcode = (row['barcode'] or '').strip()
        qty = int(row['qty'] or 0)
        if not barcode or qty <= 0:
            return jsonify({'success': False, 'error': 'Invalid removed entry data'}), 400

        # Update searchRack quantity by BARCODE (pad with leading zeros)
        rack_conn = sqlite3.connect('searchRack.db')
        rack_cur = rack_conn.cursor()
        rack_cur.execute('PRAGMA table_info(SEARCHRACK)')
        cols = [r[1] for r in rack_cur.fetchall()]
        qty_col = 'QUANTITY' if 'QUANTITY' in cols else ('QTY' if 'QTY' in cols else None)
        if not qty_col:
            return jsonify({'success': False, 'error': 'No quantity column in SEARCHRACK'}), 500

        barcode_padded = barcode.zfill(12) if barcode and barcode.isdigit() else barcode
        rack_cur.execute(f'SELECT ID, {qty_col} FROM SEARCHRACK WHERE BARCODE = ? COLLATE NOCASE', (barcode_padded,))
        found = rack_cur.fetchone()
        if not found:
            return jsonify({'success': False, 'error': 'No matching inventory found to restore'}), 404

        rack_id = found[0]
        current_qty = int(found[1] or 0)
        new_qty = current_qty + qty
        rack_cur.execute(f'UPDATE SEARCHRACK SET {qty_col} = ? WHERE ID = ?', (new_qty, rack_id))
        rack_conn.commit()

        # Mark removed row undone
        import datetime as _dt
        rem_cur.execute('UPDATE removed SET undone_at = ? WHERE id = ?', (_dt.datetime.now(_dt.UTC).isoformat() + 'Z', rem_id))
        rem_conn.commit()

        return jsonify({'success': True, 'new_qty': new_qty})
    except Exception as e:
        return jsonify({'success': False, 'error': _safe_error(e)}), 500
    finally:
        rem_conn.close()
        rack_conn.close()

@app.route('/api/sold/remove-now/<int:order_id>', methods=['POST'])
def api_sold_remove_now(order_id: int):
    """Immediately reduce inventory for a specific sold order and log to rackhistory.db, bypassing grace period."""
    try:
        # Fetch order details
        s_conn = sqlite3.connect('sold.db')
        s_conn.row_factory = sqlite3.Row
        s_cur = s_conn.cursor()
        s_cur.execute('SELECT id, order_id, title, barcode, quantity FROM orders WHERE id = ?', (order_id,))
        order = s_cur.fetchone()
        if not order:
            return jsonify({'success': False, 'error': 'Order not found'}), 404, 404
        barcode = (order['barcode'] or '').strip()
        sold_qty = int(order['quantity'] or 1)
        if not barcode:
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
        
        # Try original barcode first (searchRack may store without leading zeros)
        r_cur.execute(f'SELECT ID, {qty_col} FROM SEARCHRACK WHERE BARCODE = ? COLLATE NOCASE', (barcode,))
        row = r_cur.fetchone()
        
        # If not found, try with zero-padding
        if not row:
            barcode_padded = barcode.zfill(12) if barcode and barcode.isdigit() else barcode
            r_cur.execute(f'SELECT ID, {qty_col} FROM SEARCHRACK WHERE BARCODE = ? COLLATE NOCASE', (barcode_padded,))
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

        # Log to rackhistory.db
        rem_conn = sqlite3.connect('rackhistory.db')
        rem_cur = rem_conn.cursor()
        
        # Log to old removed table (legacy)
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
        import datetime as _dt
        rem_cur.execute(
            'INSERT INTO removed (name, barcode, qty, time_removed) VALUES (?,?,?,?)',
            (order['title'] or '', barcode, sold_qty, _dt.datetime.now().isoformat())
        )
        
        # Log to removed_items table (for history view)
        _ensure_removed_items_table(rem_cur)
        
        # Get location if inventory was found
        item_location = None
        if inventory_found:
            r_cur.execute('SELECT ITEM_POSITION FROM SEARCHRACK WHERE ID = ?', (rack_id,))
            loc_row = r_cur.fetchone()
            if loc_row:
                item_location = loc_row[0]
        
        rem_cur.execute('''
            INSERT INTO removed_items 
            (order_id, barcode, title, quantity_removed, removed_at, searchrack_id, old_quantity, new_quantity, removal_type, item_position)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (order['order_id'], barcode, order['title'] or '', sold_qty, _dt.datetime.now().isoformat(), 
              rack_id if inventory_found else None, current_qty if inventory_found else 0, 
              new_qty if inventory_found else 0, 'manual_sold_removal', item_location))
        
        rem_conn.commit()

        # Mark order as processed
        s_cur.execute('UPDATE orders SET rackupdated = 1 WHERE id = ?', (order_id,))
        s_conn.commit()

        return jsonify({'success': True, 'new_qty': new_qty, 'inventory_found': inventory_found})
    except Exception as e:
        return jsonify({'success': False, 'error': _safe_error(e)}), 500
    finally:
        s_conn.close()
        r_conn.close()
        rem_conn.close()


@app.route('/undo_match', methods=['POST'])
def undo_match():
    data = request.json
    bol_id = data.get('bol_id')
    ebay_id = data.get('ebay_id')
    if bol_id is None or ebay_id is None:
        return jsonify({'success': False, 'error': 'Missing IDs'}), 400
    try:
        # Look up the upc for this bol_id
        bol_conn = sqlite3.connect('bol.db')
        bol_cur = bol_conn.cursor()
        bol_cur.execute('SELECT upc FROM bol_items WHERE rowid=?', (bol_id,))
        bol_row = bol_cur.fetchone()
        if not bol_row or not bol_row[0]:
            return jsonify({'success': False, 'error': 'No UPC found for BOL item'}), 404
        upc = bol_row[0]
        # Remove from found.db using upc and ebay_id
        found_conn = sqlite3.connect('found.db')
        found_cur = found_conn.cursor()
        found_cur.execute('DELETE FROM matches WHERE ebay_id=? AND upc=?', (ebay_id, upc))
        found_conn.commit()
        # Unmark bol item
        bol_conn = sqlite3.connect('bol.db')
        bol_cur = bol_conn.cursor()
        bol_cur.execute("UPDATE bol_items SET isFound='' WHERE rowid=?", (bol_id,))
        bol_conn.commit()
        # Unmark ebay item
        ebay_conn = sqlite3.connect('ebayStore.db')
        ebay_cur = ebay_conn.cursor()
        ebay_cur.execute("UPDATE INVENTORY SET isFound='' WHERE rowid=?", (ebay_id,))
        ebay_conn.commit()
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': _safe_error(e)}), 500
    finally:
        bol_conn.close()
        found_conn.close()
        ebay_conn.close()


@app.route('/get_bol_upcs', methods=['GET'])
def get_bol_upcs():
    """Return a JSON list of all UPCs from bol.db bol_items table (UPC column)."""
    try:
        conn = sqlite3.connect('bol.db')
        cur = conn.cursor()
        cur.execute('SELECT upc FROM bol_items WHERE upc IS NOT NULL AND upc != ""')
        upcs = [row[0] for row in cur.fetchall()]
        return jsonify({'upcs': upcs})
    except Exception as e:
        return jsonify({'error': _safe_error(e)}), 500
    finally:
        conn.close()


@app.route('/upload_position_picture', methods=['POST'])
def upload_position_picture():
    import os
    from werkzeug.utils import secure_filename
    # Ensure static/pictureposition folder exists
    save_dir = os.path.join(os.getcwd(), 'static', 'pictureposition')
    os.makedirs(save_dir, exist_ok=True)
    
    file = request.files.get('picture')
    if not file:
        return jsonify({'success': False, 'error': 'No file uploaded'}), 400
    
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
    data = request.get_json()
    session['inv_pictureposition_path'] = data.get('path')
    print(f"Set pictureposition_path from barcode.html: {session.get('inv_pictureposition_path')}")
    return jsonify({'success': True})

@app.route('/searchrack')
def searchrack_page():
    response = make_response(render_template('searchrack.html'))
    response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    response.headers['Pragma'] = 'no-cache'
    response.headers['Expires'] = '0'
    return response

@app.route('/finder')
def finder_page():
    response = make_response(render_template('finder.html'))
    response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    response.headers['Pragma'] = 'no-cache'
    response.headers['Expires'] = '0'
    return response

@app.route('/api/finder', methods=['POST'])
def api_finder():
    """Search searchRack and rawbol databases for item location."""
    data = request.get_json() or {}
    q = data.get('q', '').strip()
    if not q:
        return jsonify({'searchrack': [], 'rawbol': []})

    q_stripped = q.lstrip('0') if q.isdigit() else q
    # Split into words for multi-word fuzzy matching
    words = [w for w in q_stripped.split() if len(w) >= 2]
    if not words:
        words = [q_stripped]

    searchrack_results = []
    rawbol_results = []

    # Build WHERE clause: all words must match (AND) against TITLE, or any word matches BARCODE
    def build_where(title_col, barcode_col, words):
        clauses = []
        params = []
        # All words must appear in title
        title_parts = [f"{title_col} LIKE ? COLLATE NOCASE" for _ in words]
        clauses.append('(' + ' AND '.join(title_parts) + ')')
        params.extend(f'%{w}%' for w in words)
        # OR barcode matches any word
        for w in words:
            clauses.append(f"{barcode_col} LIKE ? COLLATE NOCASE")
            params.append(f'%{w}%')
        return ' OR '.join(clauses), params

    # Search searchRack.db
    try:
        conn = sqlite3.connect(str(BASE_DIR / 'searchRack.db'))
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        where, params = build_where('TITLE', 'BARCODE', words)
        cur.execute(f'''SELECT ID, TITLE, BARCODE, ITEM_POSITION, IMAGES, PICTUREPOSITION, QUANTITY, IMAGE, ITEMID
                       FROM SEARCHRACK
                       WHERE {where}
                       LIMIT 20''', params)
        for row in cur.fetchall():
            searchrack_results.append({
                'id': row['ID'],
                'title': row['TITLE'],
                'barcode': row['BARCODE'],
                'item_position': row['ITEM_POSITION'],
                'pictureposition': row['PICTUREPOSITION'],
                'quantity': row['QUANTITY'],
                'image': row['IMAGE'],
                'itemid': row['ITEMID'],
            })
        conn.close()
    except Exception as e:
        print(f"Finder searchRack error: {e}")

    # Search rawbol.db
    try:
        conn = sqlite3.connect(str(BASE_DIR / 'rawbol.db'))
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        where, params = build_where('item_description', 'upc', words)
        cur.execute(f'''SELECT upc, item_description, image_url, lot_number
                       FROM raw_bol_items
                       WHERE {where}
                       LIMIT 20''', params)
        for row in cur.fetchall():
            rawbol_results.append({
                'upc': row['upc'],
                'item_description': row['item_description'],
                'image_url': row['image_url'],
                'lot_number': row['lot_number'],
            })
        conn.close()
    except Exception as e:
        print(f"Finder rawbol error: {e}")

    return jsonify({'searchrack': searchrack_results, 'rawbol': rawbol_results})

@app.route('/api/finder/assign', methods=['POST'])
def api_finder_assign():
    """Assign a location to a sold order."""
    data = request.get_json() or {}
    order_id = data.get('order_id')
    location = data.get('location', '').strip()
    if not order_id or not location:
        return jsonify({'error': 'Missing order_id or location'}), 400
    try:
        conn = sqlite3.connect(str(BASE_DIR / 'sold.db'))
        cur = conn.cursor()
        cur.execute('UPDATE orders SET location = ? WHERE id = ?', (location, order_id))
        conn.commit()
        conn.close()
        # Clear the sold-orders cache so the change is visible immediately
        try:
            cache.delete_memoized(sold_orders)
        except Exception:
            pass
        return jsonify({'ok': True})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/finder/remove', methods=['POST'])
def api_finder_remove():
    """Remove 1 (or sold qty) from inventory for a specific searchRack row."""
    import datetime as _dt
    data = request.get_json() or {}
    searchrack_id = data.get('searchrack_id')
    order_id = data.get('order_id')  # optional — sold order context
    qty_to_remove = int(data.get('qty', 1))
    if not searchrack_id:
        return jsonify({'ok': False, 'error': 'Missing searchrack_id'}), 400
    try:
        r_conn = sqlite3.connect(str(BASE_DIR / 'searchRack.db'))
        r_conn.row_factory = sqlite3.Row
        r_cur = r_conn.cursor()
        r_cur.execute('SELECT ID, TITLE, BARCODE, QUANTITY, ITEM_POSITION FROM SEARCHRACK WHERE ID = ?', (searchrack_id,))
        row = r_cur.fetchone()
        if not row:
            r_conn.close()
            return jsonify({'ok': False, 'error': 'Item not found'}), 404
        current_qty = int(row['QUANTITY'] or 0)
        new_qty = max(0, current_qty - qty_to_remove)
        r_cur.execute('UPDATE SEARCHRACK SET QUANTITY = ? WHERE ID = ?', (new_qty, searchrack_id))
        r_conn.commit()

        # Log to rackhistory.db
        rem_conn = sqlite3.connect(str(BASE_DIR / 'rackhistory.db'))
        rem_cur = rem_conn.cursor()
        rem_cur.execute('''
            CREATE TABLE IF NOT EXISTS removed (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT, barcode TEXT, qty INTEGER,
                time_removed TEXT, undone_at TEXT
            )
        ''')
        rem_cur.execute(
            'INSERT INTO removed (name, barcode, qty, time_removed) VALUES (?,?,?,?)',
            (row['TITLE'] or '', row['BARCODE'] or '', qty_to_remove, _dt.datetime.now().isoformat())
        )
        _ensure_removed_items_table(rem_cur)
        rem_cur.execute('''
            INSERT INTO removed_items
            (order_id, barcode, title, quantity_removed, removed_at, searchrack_id, old_quantity, new_quantity, removal_type, item_position)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (order_id or '', row['BARCODE'] or '', row['TITLE'] or '', qty_to_remove,
              _dt.datetime.now().isoformat(), searchrack_id, current_qty, new_qty, 'finder_removal', row['ITEM_POSITION']))
        rem_conn.commit()
        rem_conn.close()

        # If order context, mark order as rackupdated
        if order_id:
            s_conn = sqlite3.connect(str(BASE_DIR / 'sold.db'))
            s_cur = s_conn.cursor()
            s_cur.execute('UPDATE orders SET rackupdated = 1 WHERE id = ?', (order_id,))
            s_conn.commit()
            s_conn.close()

        r_conn.close()
        return jsonify({'ok': True, 'new_qty': new_qty, 'old_qty': current_qty, 'searchrack_id': searchrack_id, 'order_id': order_id or None})
    except Exception as e:
        return jsonify({'ok': False, 'error': _safe_error(e)}), 500

@app.route('/api/finder/undo-remove', methods=['POST'])
def api_finder_undo_remove():
    """Undo a finder removal — restore quantity and optionally unmark the sold order."""
    data = request.get_json() or {}
    searchrack_id = data.get('searchrack_id')
    old_qty = data.get('old_qty')
    order_id = data.get('order_id')
    if not searchrack_id or old_qty is None:
        return jsonify({'ok': False, 'error': 'Missing searchrack_id or old_qty'}), 400
    try:
        r_conn = sqlite3.connect(str(BASE_DIR / 'searchRack.db'))
        r_cur = r_conn.cursor()
        r_cur.execute('UPDATE SEARCHRACK SET QUANTITY = ? WHERE ID = ?', (int(old_qty), int(searchrack_id)))
        r_conn.commit()
        r_conn.close()

        if order_id:
            s_conn = sqlite3.connect(str(BASE_DIR / 'sold.db'))
            s_cur = s_conn.cursor()
            s_cur.execute('UPDATE orders SET rackupdated = 0 WHERE id = ?', (order_id,))
            s_conn.commit()
            s_conn.close()

        return jsonify({'ok': True, 'restored_qty': int(old_qty)})
    except Exception as e:
        return jsonify({'ok': False, 'error': _safe_error(e)}), 500

@app.route('/cleanup')
def cleanup_page():
    response = make_response(render_template('cleanup.html'))
    response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    response.headers['Pragma'] = 'no-cache'
    response.headers['Expires'] = '0'
    return response

@app.route('/unified-search')
def unified_search_page():
    """Unified search page that searches across all databases"""
    return render_template('unified_search.html')

@app.route('/searchrack_api')
def searchrack_api():
    q = request.args.get('q', '').strip()
    # Strip leading zeros from barcode searches
    q_stripped = q.lstrip('0') if q.isdigit() else q
    results = []
    if q_stripped:
        conn = sqlite3.connect('searchRack.db')
        try:
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
        finally:
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
        try:
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()
            cur.execute('''
            SELECT item_description as description, upc, avg_cost, 
                   lot_number, bol_location, quantity
            FROM raw_bol_items 
            WHERE item_description LIKE ? OR upc LIKE ? OR lot_number LIKE ? OR bol_location LIKE ?
        ''', (f'%{q_stripped}%', f'%{q_stripped}%', f'%{q_stripped}%', f'%{q_stripped}%'))
            results = [dict(row) for row in cur.fetchall()]
        finally:
            conn.close()
    return jsonify({'results': results})

@app.route('/view_all/<db_type>')
def view_all(db_type):
    if db_type == 'rack':
        conn = sqlite3.connect('searchRack.db')
        try:
            cur = conn.cursor()
            cur.execute('SELECT * FROM SEARCHRACK')
            columns = [desc[0] for desc in cur.description]
            items = [dict(zip(columns, row)) for row in cur.fetchall()]
            title = 'All Inventory Items'
        finally:
            conn.close()
    elif db_type == 'bol':
        conn = sqlite3.connect('rawbol.db')
        try:
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()
            cur.execute('SELECT * FROM raw_bol_items')
            items = [dict(row) for row in cur.fetchall()]
            title = 'All BOL Items'
        finally:
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
        
        # Normalize data keys to uppercase for searchRack (case-insensitive matching)
        if db_type == 'rack':
            normalized_data = {k.upper(): v for k, v in data.items()}
        else:
            normalized_data = data
        
        # If updating quantity in searchRack, log to removed_items for history
        if db_type == 'rack' and 'QUANTITY' in normalized_data:
            try:
                # Get old quantity and item details
                cur.execute('SELECT QUANTITY, BARCODE, TITLE, ITEM_POSITION FROM SEARCHRACK WHERE ID = ?', (item_id,))
                old_row = cur.fetchone()
                if old_row:
                    old_qty, barcode, title, item_location = old_row
                    new_qty = normalized_data['QUANTITY']
                    qty_change = new_qty - old_qty
                    
                    print(f"📝 Manual edit detected: {title} (ID: {item_id}) - Qty change: {old_qty} → {new_qty} (change: {qty_change})")
                    
                    if qty_change != 0:  # Only log if quantity actually changed
                        from datetime import datetime
                        now = datetime.now()
                        
                        removed_conn = sqlite3.connect('rackhistory.db')
                        try:
                            removed_cur = removed_conn.cursor()
                            _ensure_removed_items_table(removed_cur)
                            removed_cur.execute('''
                            INSERT INTO removed_items 
                            (order_id, barcode, title, quantity_removed, removed_at, searchrack_id, old_quantity, new_quantity, removal_type, item_position)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        ''', (None, barcode, title, abs(qty_change), now.isoformat(), item_id, old_qty, new_qty, 'manual_edit', item_location or ''))
                            removed_conn.commit()
                        finally:
                            removed_conn.close()
                        print(f"✅ Logged manual edit to history: {title}")
                    else:
                        print(f"⚠️ Quantity unchanged, not logging to history")
            except Exception as log_err:
                print(f"❌ Error logging quantity change to removed_items: {log_err}")
                import traceback
                traceback.print_exc()
        
        # Build update query using normalized data
        set_clause = ', '.join([f'"{k}"=?' for k in normalized_data.keys() if k in columns])
        values = [v for k, v in normalized_data.items() if k in columns]
        values.append(item_id)
        
        if not set_clause:
            return jsonify({'success': False, 'error': 'No valid fields to update'}), 400
        
        query = f'UPDATE {"SEARCHRACK" if db_type == "rack" else "raw_bol_items"} SET {set_clause} WHERE id=?'
        cur.execute(query, values)
        conn.commit()
        
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': _safe_error(e)}), 500
    finally:
        conn.close()

@app.route('/api/search/<db_key>', methods=['POST'])
@cache.cached(timeout=300, make_cache_key=lambda *args, **kwargs: f"search_{kwargs.get('db_key')}_{hash(str(request.get_json()))}")
def api_search_db(db_key):
    data = request.get_json() or {}
    q = (data.get('q') or '').strip()
    print(f"DEBUG: api_search_db hit. db={db_key}, q='{q}'")
    # Strip leading zeros from barcode searches
    q_stripped = q.lstrip('0') if q and q.isdigit() else q
    # Accept limit from client. If limit is 0 or None, we will NOT apply a SQL LIMIT (i.e., return all rows).
    limit_raw = data.get('limit')
    try:
        limit = int(limit_raw) if limit_raw is not None else 50
    except Exception:
        limit = 50
    try:
        print(f"[SEARCH DEBUG] db_key={db_key}, query={q}, query_stripped={q_stripped}")
        mapping = {
            'ebayStore': 'ebayStore.db',
            'amazonStore': 'amazonStore.db',
            'sold': 'sold.db',
            'returns': 'sold.db',  # Returns table in sold.db
            'searchRack': 'searchRack.db',
            'found': 'found.db',
            'bol': 'rawbol.db'  # Search rawbol.db to see original imported amounts across all LOTs
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
                now_iso = _dt.datetime.now(_dt.UTC).isoformat()
                cur_m.execute("UPDATE SEARCHRACK SET CREATED_AT = ? WHERE CREATED_AT IS NULL OR TRIM(COALESCE(CREATED_AT,'')) = ''", (now_iso,))
                conn_m.commit()
            except Exception:
                # Don't block search if migration fails
                pass
            finally:
                conn_m.close()
        
        # Use cached connection for better performance
        conn = get_db_connection(db_path)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")
        tables = [r[0] for r in cur.fetchall()]
        if not tables:
            conn.close()
            return jsonify({'results': []})
        prefer = None
        # Special case for returns: use returns table
        if db_key == 'returns':
            prefer = 'returns' if 'returns' in tables else None
        else:
            for t in ['orders','INVENTORY','SEARCHRACK','searchrack','rack','items','bol_items']:
                if t in tables:
                    prefer = t
                    break
        table = prefer or tables[0]
        print(f"[SEARCH DEBUG] db_key={db_key}, db_path={db_path}, table={table}, tables={tables}")
        cur.execute(f"PRAGMA table_info('{table}')")
        cols = [r[1] for r in cur.fetchall()]
        print(f"[SEARCH DEBUG] columns={cols}")
        where_clause = ''
        params = []
        # Accept optional location filter (from client UI) to search Item_Position specifically
        location = (data.get('location') or '').strip()
        # Accept optional LOT filter for BOL database
        lot_filter = (data.get('lot_filter') or '').strip() if db_key == 'bol' else ''
        # Accept optional location_group_id for filtering by shelf group (from location browser)
        location_group_id = data.get('location_group_id')
        
        if q_stripped:
            likes = []
            for c in cols:
                likes.append(f"LOWER(COALESCE({c},'')) LIKE ?")
                params.append(f"%{q_stripped.lower()}%")
            where_clause = ' WHERE ' + ' OR '.join(likes)
        
        # If a specific LOT was provided (BOL database only), add filter
        if db_key == 'bol' and lot_filter:
            lot_condition = "lot_number = ?"
            params.append(lot_filter)
            if where_clause:
                where_clause += f' AND {lot_condition}'
            else:
                where_clause = f' WHERE {lot_condition}'
        
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
        
        # If location_group_id is provided (searchRack only), filter by all shelves in that group
        if db_key == 'searchRack' and location_group_id:
            try:
                # Get all shelf codes in this group
                group_conn = sqlite3.connect('searchRack.db')
                group_cur = group_conn.cursor()
                group_cur.execute('SELECT shelf_name FROM shelves WHERE group_id = ?', (location_group_id,))
                shelf_codes = [row[0] for row in group_cur.fetchall()]
                
                if shelf_codes:
                    # Find item_position column
                    loc_cols = [c for c in cols if c.lower() in ('item_position','itemposition','position')]
                    if not loc_cols:
                        loc_cols = [c for c in cols if 'position' in c.lower()]
                    
                    if loc_cols:
                        loc_col = loc_cols[0]
                        # Build OR condition for all shelves in group
                        shelf_conditions = []
                        for shelf_code in shelf_codes:
                            shelf_conditions.append(f"LOWER(TRIM({loc_col})) = LOWER(TRIM(?))")
                            params.append(shelf_code)
                        
                        if where_clause:
                            where_clause += ' AND (' + ' OR '.join(shelf_conditions) + ')'
                        else:
                            where_clause = ' WHERE (' + ' OR '.join(shelf_conditions) + ')'
            except Exception as e:
                print(f"[SEARCH DEBUG] Error filtering by location_group_id: {e}")
            finally:
                group_conn.close()
        
        # compute total matching count for pagination
        count_sql = f"SELECT COUNT(*) FROM {table} {where_clause}"
        print(f"[SEARCH DEBUG] count_sql={count_sql}, params={params}")
        cur.execute(count_sql, params)
        total_count = cur.fetchone()[0]
        print(f"[SEARCH DEBUG] total_count={total_count}")

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

        # Pre-load enrichment data in batch for searchRack results (avoids N+1 queries)
        _enrichment_cache = {}
        if db_key == 'searchRack' and rows:
            # Collect all barcodes that need enrichment
            _barcodes_to_enrich = set()
            for r in rows:
                bc = r.get('BARCODE') or r.get('barcode') or r.get('Barcode') or ''
                if bc:
                    base = str(bc).split('-')[0]
                    base = base.lstrip('0') if base and base.isdigit() else base
                    if base:
                        _barcodes_to_enrich.add(base)
            if _barcodes_to_enrich:
                from DBmanager import connect_db
                placeholders = ','.join('?' for _ in _barcodes_to_enrich)
                bc_tuple = tuple(_barcodes_to_enrich)
                # Batch lookup: ebayStore
                try:
                    with connect_db('ebayStore.db') as _ec:
                        _ec.row_factory = sqlite3.Row
                        for r in _ec.cursor().execute(f"SELECT Title, Image, ItemID, UPC FROM INVENTORY WHERE UPC IN ({placeholders}) COLLATE NOCASE", bc_tuple):
                            upc_key = (r['UPC'] or '').lstrip('0') if (r['UPC'] or '').isdigit() else r['UPC']
                            _enrichment_cache.setdefault(upc_key, {}).update({'title': r['Title'], 'image': r['Image'], 'item_id': r['ItemID']})
                except Exception:
                    pass
                # Batch lookup: amazonStore
                try:
                    with connect_db('amazonStore.db') as _ac:
                        _ac.row_factory = sqlite3.Row
                        for r in _ac.cursor().execute(f"SELECT TITLE, IMAGE, ASIN, UPC FROM ITEMS WHERE UPC IN ({placeholders}) COLLATE NOCASE", bc_tuple):
                            upc_key = (r['UPC'] or '').lstrip('0') if (r['UPC'] or '').isdigit() else r['UPC']
                            cached = _enrichment_cache.setdefault(upc_key, {})
                            cached.setdefault('title', r['TITLE'])
                            cached.setdefault('image', r['IMAGE'])
                            cached.setdefault('item_id', r['ASIN'])
                except Exception:
                    pass
                # Batch lookup: rawbol
                try:
                    with connect_db('rawbol.db') as _rc:
                        _rc.row_factory = sqlite3.Row
                        for r in _rc.cursor().execute(f"SELECT item_description, image_url, upc FROM raw_bol_items WHERE upc IN ({placeholders}) COLLATE NOCASE", bc_tuple):
                            upc_key = (r['upc'] or '').lstrip('0') if (r['upc'] or '').isdigit() else r['upc']
                            cached = _enrichment_cache.setdefault(upc_key, {})
                            cached.setdefault('title', r['item_description'])
                            cached.setdefault('image', r['image_url'])
                            cached.setdefault('item_id', r['upc'])
                except Exception:
                    pass
                # Batch lookup: bol
                try:
                    with connect_db('bol.db') as _bc:
                        _bc.row_factory = sqlite3.Row
                        for r in _bc.cursor().execute(f"SELECT item_description, image_url, upc FROM bol_items WHERE upc IN ({placeholders}) COLLATE NOCASE", bc_tuple):
                            upc_key = (r['upc'] or '').lstrip('0') if (r['upc'] or '').isdigit() else r['upc']
                            cached = _enrichment_cache.setdefault(upc_key, {})
                            cached.setdefault('title', r['item_description'])
                            cached.setdefault('image', r['image_url'])
                            cached.setdefault('item_id', r['upc'])
                except Exception:
                    pass

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

            # Quantity extraction that properly handles 0 values
            qty_raw = None
            for qkey in ['QUANTITY', 'Quantity', 'quantity', 'qty']:
                if qkey in item and item[qkey] is not None:
                    qty_raw = item[qkey]
                    if db_key == 'searchRack':
                        print(f"[QTY EXTRACT] Found {qkey}={qty_raw}, type={type(qty_raw)}, barcode={item.get('BARCODE') or item.get('barcode')}")
                    break
            if qty_raw is None:
                qty_raw = ''
                if db_key == 'searchRack':
                    print(f"[QTY EXTRACT] No quantity found, defaulting to empty, barcode={item.get('BARCODE') or item.get('barcode')}")
            
            # Debug logging for zero quantity items
            if qty_raw == 0 and db_key == 'searchRack':
                print(f"[DEBUG] Zero quantity item found: barcode={barcode_val}, qty_raw={qty_raw}, type={type(qty_raw)}")
            
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
                'quantity': qty_raw,
                # created_at available on SEARCHRACK rows populated by DBmanager
                'created_at': item.get('CREATED_AT') or item.get('created_at') or '',
                # store field for returns (amazon/ebay)
                'store': item.get('store') or item.get('Store') or item.get('STORE') or '',
                'raw': item
            }
            # If this row comes from searchRack, enrich from pre-loaded batch cache
            try:
                if db_key == 'searchRack' and item_out.get('barcode'):
                    lookup_barcode = item_out.get('barcode')
                    base_barcode = str(lookup_barcode).split('-')[0] if lookup_barcode else lookup_barcode
                    base_barcode = base_barcode.lstrip('0') if base_barcode and base_barcode.isdigit() else base_barcode
                    cached = _enrichment_cache.get(base_barcode, {})
                    if cached:
                        item_out['title'] = item_out.get('title') or cached.get('title')
                        item_out['image'] = item_out.get('image') or cached.get('image')
                        item_out['item_id'] = item_out.get('item_id') or cached.get('item_id')
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
                            except Exception as e:
                                print(f"Debug: sold barcode lookup error: {e}")
                                pass
                            finally:
                                es_conn.close()
                    
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
                        except Exception as e:
                            print(f"Debug: rawbol enrichment error: {e}")
                            pass
                        finally:
                            bol_conn.close()
            except Exception:
                pass
            results.append(item_out)
        
        # Note: Not closing connection - using connection pooling for performance
        
        # If searching bol (rawbol.db), aggregate by UPC and handle multiple LOTs
        if db_key == 'bol' and results:
            from collections import defaultdict
            import datetime as _dt
            
            # Group by UPC
            upc_groups = defaultdict(list)
            for r in results:
                upc = (r.get('barcode') or '').strip()
                if upc:
                    upc_groups[upc].append(r)
            
            aggregated = []
            for upc, items in upc_groups.items():
                # Extract LOT information from all items with this UPC
                lot_data = []
                total_qty = 0
                
                # Use first item as base for title, image, etc.
                base_item = items[0].copy()
                
                for item in items:
                    qty = item.get('quantity') or item.get('raw', {}).get('quantity') or 1
                    try:
                        qty = int(qty)
                    except (ValueError, TypeError):
                        qty = 1
                    
                    total_qty += qty
                    
                    lot_num = item.get('raw', {}).get('lot_number') or ''
                    lot_num = str(lot_num).strip() if lot_num else ''
                    if not lot_num or lot_num.lower() in ['nan', 'none', 'null', '']:
                        lot_num = '(No LOT)'
                    
                    import_date = item.get('raw', {}).get('import_date') or ''
                    if not import_date or str(import_date).strip().lower() in ['nan', 'none', 'null', '']:
                        import_date = '(No Date)'
                    
                    lot_data.append({
                        'lot_number': lot_num,
                        'qty': qty,
                        'import_date': import_date
                    })
                
                # Sort lots by import_date (newest first), handling "(No Date)" specially
                def parse_date(date_str):
                    if date_str == '(No Date)':
                        return _dt.datetime.min
                    try:
                        # Try parsing YYYY-MM-DD format
                        return _dt.datetime.strptime(str(date_str).split()[0], '%Y-%m-%d')
                    except Exception:
                        try:
                            # Try ISO format
                            return _dt.datetime.fromisoformat(str(date_str).replace('Z', '+00:00'))
                        except Exception:
                            return _dt.datetime.min
                
                lot_data.sort(key=lambda x: parse_date(x['import_date']), reverse=True)
                
                # Determine LOT display
                unique_lots = list(set([ld['lot_number'] for ld in lot_data]))
                unique_lots = [lot for lot in unique_lots if lot != '(No LOT)']
                
                if len(unique_lots) == 0:
                    lot_display = '(No LOT)'
                    has_multiple = False
                elif len(unique_lots) == 1:
                    lot_display = unique_lots[0]
                    has_multiple = False
                else:
                    lot_display = 'multiple LOTS'
                    has_multiple = True
                
                # Build aggregated item
                aggregated_item = {
                    'source_db': 'bol',
                    'source_table': base_item.get('source_table'),
                    'id': base_item.get('id'),
                    'title': base_item.get('title'),
                    'image': base_item.get('image'),
                    'barcode': upc,
                    'item_id': base_item.get('item_id'),
                    'quantity': total_qty,
                    'lot_number': lot_display,
                    'has_multiple_lots': has_multiple,
                    'lot_data': lot_data,  # Array of {lot_number, qty, import_date}
                    'raw': base_item.get('raw')
                }
                
                aggregated.append(aggregated_item)
            
            results = aggregated
            total_count = len(results)
        
        # If searching the searchRack snapshot, merge rows with same barcode+item_position and sum quantities
        # BUT: picture position items should NEVER be merged (each picture is unique)
        if db_key == 'searchRack' and results:
            merged = {}
            for r in results:
                bc = (r.get('barcode') or '').strip()
                pic = (r.get('pictureposition') or '').strip()
                
                # Picture position items are ALWAYS unique - use pictureposition + row ID for key
                if pic:
                    # Each picture position gets unique key (never merge)
                    key = f"{bc.lower()}||pic||{pic.lower()}||{r.get('id')}"
                else:
                    # Shelf code items: merge by barcode + item_position
                    pos = (r.get('item_position') or '').strip()
                    if not bc:
                        key = f"__{id(r)}_{len(merged)}"
                    else:
                        key = f"{bc.lower()}||shelf||{pos.lower()}"
                
                if key not in merged:
                    merged[key] = r.copy()
                    # normalize quantity - handle 0 values properly
                    qty_val = r.get('quantity')
                    if qty_val is None or qty_val == '':
                        merged[key]['quantity'] = 1
                    elif isinstance(qty_val, (int, float)):
                        merged[key]['quantity'] = int(qty_val)
                    elif str(qty_val).isdigit():
                        merged[key]['quantity'] = int(qty_val)
                    else:
                        merged[key]['quantity'] = 1
                else:
                    qty_val = r.get('quantity')
                    if qty_val is None or qty_val == '':
                        add_q = 1
                    elif isinstance(qty_val, (int, float)):
                        add_q = int(qty_val)
                    elif str(qty_val).isdigit():
                        add_q = int(qty_val)
                    else:
                        add_q = 1
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

        # Calculate total quantity for all results
        total_quantity = 0
        for r in results:
            try:
                qty = r.get('quantity', 0)
                if isinstance(qty, (int, float)):
                    total_quantity += int(qty)
                elif isinstance(qty, str) and qty.isdigit():
                    total_quantity += int(qty)
                else:
                    total_quantity += 1
            except Exception:
                total_quantity += 1

        # Debug: Check what quantities are in results before sending
        if db_key == 'searchRack':
            for r in results:
                if r.get('barcode') == '882864825810':
                    print(f"[BEFORE JSONIFY] barcode={r.get('barcode')}, quantity={r.get('quantity')}, type={type(r.get('quantity'))}")

        return jsonify({'results': results, 'total': total_count, 'total_quantity': total_quantity})
    except Exception as e:
        return jsonify({'error': _safe_error(e)}), 500


# Quick search cache for UPC/barcode lookups (Option 3: Hybrid search)
_search_all_cache = {}
_search_cache_timeout = 300  # 5 minutes

@app.route('/api/search-all', methods=['POST'])
def api_search_all():
    """Search across all databases simultaneously with cached quick lookups for UPCs"""
    try:
        data = request.get_json() or {}
        query = (data.get('q') or '').strip()
        
        if not query:
            return jsonify({'error': 'No search query provided'}), 400
        
        # Strip leading zeros for barcode searches
        query_stripped = query.lstrip('0') if query and query.isdigit() else query
        
        # Determine search type (UPC searches are cached)
        is_upc_search = query.isdigit()
        
        # Check cache for UPC searches only
        cache_key = f"search_all:{query_stripped}"
        if is_upc_search and cache_key in _search_all_cache:
            cached_data, cached_time = _search_all_cache[cache_key]
            if time.time() - cached_time < _search_cache_timeout:
                cached_data['from_cache'] = True
                return jsonify(cached_data)
        
        # Define databases to search with display info (ordered: warehouse, item-manager, Macy BOL, sold, amazon, ebay)
        databases = [
            {'key': 'shelves', 'path': 'searchRack.db', 'table': 'SEARCHRACK', 'name': 'Warehouse', 'color': '#9b59b6', 'icon': '📦'},
            {'key': 'processed', 'path': 'bol.db', 'table': 'bol_items', 'name': 'Item Manager (processed items)', 'color': '#2ecc71', 'icon': '✅', 'filter': 'checked_only'},
            {'key': 'bol', 'path': 'rawbol.db', 'table': 'raw_bol_items', 'name': 'Macy BOL', 'color': '#3498db', 'icon': '📦'},
            {'key': 'sold', 'path': 'sold.db', 'table': 'sold_items', 'name': 'Sold Items', 'color': '#e74c3c', 'icon': '💰'},
            {'key': 'amazon', 'path': 'amazonStore.db', 'table': 'INVENTORY', 'name': 'Amazon Store', 'color': '#1abc9c', 'icon': '📦'},
            {'key': 'ebay', 'path': 'ebayStore.db', 'table': 'INVENTORY', 'name': 'eBay Store', 'color': '#f39c12', 'icon': '🛒'},
        ]
        
        results = {
            'query': query,
            'search_type': 'upc' if is_upc_search else 'text',
            'databases': {},
            'from_cache': False
        }
        
        # Search each database
        for db_info in databases:
            db_key = db_info['key']
            db_path = BASE_DIR / db_info['path']
            
            if not db_path.exists():
                results['databases'][db_key] = {
                    'name': db_info['name'],
                    'color': db_info['color'],
                    'icon': db_info['icon'],
                    'found': False,
                    'count': 0,
                    'error': 'Database not found'
                }
                continue
            
            try:
                conn = get_db_connection(str(db_path))
                conn.row_factory = sqlite3.Row
                cur = conn.cursor()
                
                # Check if table exists
                cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (db_info['table'],))
                if not cur.fetchone():
                    results['databases'][db_key] = {
                        'name': db_info['name'],
                        'color': db_info['color'],
                        'icon': db_info['icon'],
                        'found': False,
                        'count': 0,
                        'error': 'Table not found'
                    }
                    continue
                
                # Get table columns
                cur.execute(f"PRAGMA table_info({db_info['table']})")
                cols = [r[1] for r in cur.fetchall()]
                
                # Build search query based on type
                if is_upc_search:
                    # Fast UPC search on indexed columns
                    upc_cols = [c for c in cols if c.lower() in ('upc', 'barcode', 'sku')]
                    if not upc_cols:
                        upc_cols = [c for c in cols if 'upc' in c.lower() or 'barcode' in c.lower()]
                    
                    if upc_cols:
                        where_parts = [f"{col} LIKE ?" for col in upc_cols]
                        where_clause = " OR ".join(where_parts)
                        params = [f"%{query_stripped}%"] * len(upc_cols)
                    else:
                        where_clause = "1=0"  # No UPC columns found
                        params = []
                else:
                    # Text search across all columns
                    where_parts = [f"LOWER(COALESCE({col},'')) LIKE ?" for col in cols]
                    where_clause = " OR ".join(where_parts)
                    params = [f"%{query_stripped.lower()}%"] * len(cols)
                
                # Apply filters for specific databases
                if db_info.get('filter') == 'checked_only':
                    # For Item Manager (processed), only show checked items (good or bad)
                    # Check for items_prep_status table (bol.db)
                    cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='items_prep_status'")
                    if cur.fetchone():
                        where_clause = f"({where_clause}) AND upc IN (SELECT upc FROM items_prep_status WHERE status IN ('good', 'bad'))"
                
                # Get count and sample results
                count_sql = f"SELECT COUNT(*) as count FROM {db_info['table']} WHERE {where_clause}"
                cur.execute(count_sql, params)
                count = cur.fetchone()['count']
                
                # Get sample results (up to 3)
                sample_results = []
                if count > 0:
                    sample_sql = f"SELECT * FROM {db_info['table']} WHERE {where_clause} LIMIT 3"
                    cur.execute(sample_sql, params)
                    sample_results = [dict(row) for row in cur.fetchall()]
                
                results['databases'][db_key] = {
                    'name': db_info['name'],
                    'color': db_info['color'],
                    'icon': db_info['icon'],
                    'found': count > 0,
                    'count': count,
                    'samples': sample_results[:3] if count > 0 else []
                }
                
            except Exception as e:
                results['databases'][db_key] = {
                    'name': db_info['name'],
                    'color': db_info['color'],
                    'icon': db_info['icon'],
                    'found': False,
                    'count': 0,
                    'error': _safe_error(e)
                }
        
        # Cache UPC searches
        if is_upc_search:
            _search_all_cache[cache_key] = (results, time.time())
            # Clean old cache entries (keep cache size under control)
            current_time = time.time()
            expired_keys = [k for k, (_, t) in _search_all_cache.items() if current_time - t > _search_cache_timeout]
            for k in expired_keys:
                del _search_all_cache[k]
        
        return jsonify(results)
        
    except Exception as e:
        return jsonify({'error': _safe_error(e)}), 500


@app.route('/api/get_lot_numbers', methods=['GET'])
def api_get_lot_numbers():
    """Get all distinct LOT numbers from rawbol.db with item counts, sorted by most recent import_date."""
    try:
        conn = sqlite3.connect('rawbol.db')
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        
        # Get unique LOT numbers with counts and latest import date
        cur.execute('''
            SELECT 
                lot_number,
                COUNT(*) as item_count,
                MAX(import_date) as latest_date
            FROM raw_bol_items
            WHERE lot_number IS NOT NULL 
                AND TRIM(COALESCE(lot_number, '')) != ''
                AND LOWER(lot_number) NOT IN ('nan', 'none', 'null')
            GROUP BY lot_number
            ORDER BY latest_date DESC
        ''')
        
        rows = cur.fetchall()
        lots = [{'lot_number': r['lot_number'], 'item_count': r['item_count'], 'latest_date': r['latest_date']} for r in rows]
        
        return jsonify({'success': True, 'lots': lots})
    except Exception as e:
        return jsonify({'success': False, 'error': _safe_error(e)}), 500
    finally:
        conn.close()


@app.route('/api/bol_stats', methods=['GET'])
def api_bol_stats():
    """Calculate BOL statistics using the new quantity tracking system.
    Now properly tracks original_qty, good_qty, bad_qty, and unchecked_qty per LOT.
    """
    try:
        conn = sqlite3.connect('bol.db')
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        
        # Check if new quantity columns exist
        cur.execute('PRAGMA table_info(bol_items)')
        cols = [col[1].lower() for col in cur.fetchall()]
        has_new_columns = all(c in cols for c in ['original_qty', 'good_qty', 'bad_qty', 'unchecked_qty'])
        
        if not has_new_columns:
            return jsonify({
                'success': False, 
                'error': 'Quantity columns not yet migrated. Please restart Flask to run migration.'
            }), 500
        
        # Get all LOT numbers with their stats, ordered by import_date DESC (newest first)
        cur.execute('''
            SELECT 
                lot_number,
                MAX(import_date) as latest_date,
                COUNT(DISTINCT upc) as unique_items,
                SUM(COALESCE(original_qty, 0)) as total_original,
                SUM(COALESCE(good_qty, 0)) as total_good,
                SUM(COALESCE(bad_qty, 0)) as total_bad,
                SUM(COALESCE(unchecked_qty, 0)) as total_unchecked
            FROM bol_items
            WHERE (temporary IS NULL OR temporary = 0)
                AND upc NOT LIKE '%-%'
                AND lot_number IS NOT NULL 
                AND TRIM(COALESCE(lot_number, '')) != ''
                AND LOWER(lot_number) NOT IN ('nan', 'none', 'null')
            GROUP BY lot_number
            ORDER BY latest_date DESC
        ''')
        
        lots = cur.fetchall()
        stats = []
        
        for lot_row in lots:
            lot_number = lot_row['lot_number']
            import_date = lot_row['latest_date']
            unique_items = lot_row['unique_items'] or 0
            total_original = lot_row['total_original'] or 0
            total_good = lot_row['total_good'] or 0
            total_bad = lot_row['total_bad'] or 0
            total_unchecked = lot_row['total_unchecked'] or 0
            
            # Calculate prepped quantity (good + bad)
            total_prepped = total_good + total_bad
            
            # Calculate percentage done based on quantity (not item count)
            if total_original > 0:
                percent_done = round((total_prepped / total_original) * 100, 1)
            else:
                percent_done = 0.0
            
            # Calculate loss rate (bad / original)
            if total_original > 0:
                loss_rate = round((total_bad / total_original) * 100, 1)
            else:
                loss_rate = 0.0
            
            stats.append({
                'lot_number': lot_number,
                'import_date': import_date,
                'unique_items': unique_items,
                'total_original': total_original,
                'total_good': total_good,
                'total_bad': total_bad,
                'total_unchecked': total_unchecked,
                'total_prepped': total_prepped,
                'percent_done': percent_done,
                'loss_rate': loss_rate
            })
        
        
        return jsonify({'success': True, 'stats': stats})
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': _safe_error(e)}), 500
    finally:
        conn.close()


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
            return jsonify({'found': False})
        r = dict(row)
        return jsonify({'found': True, 'item_position': r.get('ITEM_POSITION') or r.get('item_position'), 'pictureposition': r.get('PICTUREPOSITION') or r.get('pictureposition') or r.get('image')})
    except Exception as e:
        return jsonify({'error': _safe_error(e)}), 500
    finally:
        conn.close()


@app.route('/api/set_rack_location', methods=['POST'])
def api_set_rack_location():
    """Set ITEM_POSITION for a SEARCHRACK row. Expects JSON: { id: <id>, item_position: <pos> }"""
    data = request.get_json() or {}
    item_id_raw = data.get('id')
    pos = (data.get('item_position') or '').strip()
    pictureposition = (data.get('pictureposition') or '').strip()
    
    # WORKAROUND: Parse move_qty from item_id if encoded as "id:qty" (for cache issues)
    move_qty_from_id = None
    if item_id_raw and ':' in str(item_id_raw):
        parts = str(item_id_raw).split(':')
        if len(parts) == 2:
            item_id_raw = parts[0]
            try:
                move_qty_from_id = int(parts[1])
            except Exception:
                pass
    
    item_id = item_id_raw
    
    # Optional move quantity: how many items to move from this row's Quantity
    try:
        move_qty = int(data.get('move_qty')) if data.get('move_qty') is not None else None
    except Exception:
        move_qty = None
    
    # Use move_qty from encoded ID if not provided in data
    if move_qty is None and move_qty_from_id is not None:
        move_qty = move_qty_from_id
    
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
            return jsonify({'success': False, 'error': 'Row not found'}), 404

        # Extract current quantity (try common column names)
        existing_dict = {d[0]: existing[idx] for idx, d in enumerate(cur.description)} if cur.description else dict(zip([c[0] for c in cur.description], existing))
        
        # Get barcode and title for history logging
        barcode = existing_dict.get('BARCODE') or existing_dict.get('barcode') or existing_dict.get('Barcode') or ''
        title = existing_dict.get('TITLE') or existing_dict.get('title') or existing_dict.get('Title') or ''
        old_location = existing_dict.get('ITEM_POSITION') or existing_dict.get('item_position') or existing_dict.get('ItemPosition') or ''
        
        qty_cols = ['Quantity','quantity','Qty','QTY','QUANTITY']
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
                # Exclude primary key column from INSERT so it auto-increments
                col_names = [c for c in cols if c not in (pk, 'id', 'ID', 'Id', 'rowid')]
                placeholders = ','.join('?' for _ in col_names)
                # build values copying existing row values, replacing quantity and picture/item position
                cur_vals = []
                for c in col_names:
                    # Find value from existing row (need to get column index from full cols list)
                    col_idx = cols.index(c)
                    val = existing[col_idx]
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
                    
                    # Log the quantity reduction to history
                    try:
                        from datetime import datetime
                        now = datetime.now().isoformat()
                        removed_conn = sqlite3.connect('rackhistory.db')
                        removed_cur = removed_conn.cursor()
                        removed_cur.execute('''
                            INSERT INTO removed_items 
                            (barcode, title, quantity_removed, removed_at, searchrack_id, old_quantity, new_quantity, removal_type, item_position)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                        ''', (barcode, title, target_move, now, item_id, current_qty, current_qty - target_move, 'location_edit', old_location))
                        removed_conn.commit()
                    except Exception:
                        pass
                    finally:
                        removed_conn.close()

                # insert new row for moved qty
                # Exclude primary key column from INSERT so it auto-increments
                col_names = [c for c in cols if c not in (pk, 'id', 'ID', 'Id', 'rowid')]
                placeholders = ','.join('?' for _ in col_names)
                cur_vals = []
                for c in col_names:
                    # Find value from existing row (need to get column index from full cols list)
                    col_idx = cols.index(c)
                    val = existing[col_idx]
                    if c == qty_col_name:
                        val = target_move
                    if c.lower() == 'pictureposition':
                        val = ''
                    if c.lower() == 'item_position' or c == 'ITEM_POSITION' or c.lower() == 'itemposition':
                        val = pos
                    cur_vals.append(val)
                cur.execute(f"INSERT INTO SEARCHRACK ({', '.join(col_names)}) VALUES ({placeholders})", tuple(cur_vals))
                
                # Log the new row creation to history
                try:
                    from datetime import datetime
                    now = datetime.now().isoformat()
                    removed_conn = sqlite3.connect('rackhistory.db')
                    removed_cur = removed_conn.cursor()
                    removed_cur.execute('''
                        INSERT INTO removed_items 
                        (barcode, title, quantity_removed, removed_at, old_quantity, new_quantity, removal_type, item_position)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ''', (barcode, title, target_move, now, 0, target_move, 'location_edit', pos))
                    removed_conn.commit()
                except Exception:
                    pass
                finally:
                    removed_conn.close()
            else:
                # update in-place - log location change
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
        return jsonify({'success': True, 'updated': updated})
    except Exception as e:
        return jsonify({'success': False, 'error': _safe_error(e)}), 500
    finally:
        conn.close()


def _get_table_and_pk(db_path, table_hint=None):
    conn = sqlite3.connect(db_path)
    try:
        cur = conn.cursor()
        cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")
        tables = [r[0] for r in cur.fetchall()]
        if not tables:
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
    finally:
        conn.close()
    return table, pk


@app.route('/api/update/<db_key>/<int:item_id>', methods=['POST'])
def api_update_row(db_key, item_id):
    data = request.get_json() or {}
    mapping = {
        'ebayStore': 'ebayStore.db',
        'amazonStore': 'amazonStore.db',
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
        
        # Debug logging
        print(f"DEBUG UPDATE: db_key={db_key}, table={table}, columns={cols}")
        print(f"DEBUG UPDATE: incoming data={data}")
        
        # If updating searchRack quantity, log old value for history tracking
        old_qty = None
        old_barcode = None
        old_title = None
        old_location = None
        if db_key == 'searchRack' and any(k.lower() == 'quantity' for k in data.keys()):
            try:
                cur.execute(f'SELECT QUANTITY, BARCODE, TITLE, ITEM_POSITION FROM {table} WHERE {pk} = ?', (item_id,))
                old_row = cur.fetchone()
                if old_row:
                    old_qty, old_barcode, old_title, old_location = old_row
            except Exception as e:
                print(f"Warning: Could not fetch old values for history: {e}")
        
        set_parts = []
        params = []
        
        # Create case-insensitive column mapping
        cols_lower = {c.lower(): c for c in cols}
        
        for k, v in data.items():
            k_lower = k.lower()
            # Find matching column name (case-insensitive)
            if k_lower in cols_lower:
                actual_col = cols_lower[k_lower]
                set_parts.append(f"{actual_col} = ?")
                params.append(v)
            else:
                print(f"DEBUG UPDATE: Field '{k}' not found in columns (tried lowercase '{k_lower}')")
        
        if not set_parts:
            print(f"ERROR UPDATE: No updatable fields. Data keys: {list(data.keys())}, Table cols: {cols}")
            return jsonify({
                'error': 'No updatable fields provided',
                'data_keys': list(data.keys()),
                'table_columns': cols
            }), 400
        
        params.append(item_id)
        sql = f"UPDATE {table} SET {', '.join(set_parts)} WHERE {pk} = ?"
        print(f"DEBUG UPDATE: SQL={sql}, params={params}")
        
        cur.execute(sql, params)
        conn.commit()
        updated = cur.rowcount
        
        # Log quantity changes to history for searchRack
        if db_key == 'searchRack' and old_qty is not None:
            new_qty = next((v for k, v in data.items() if k.lower() == 'quantity'), None)
            if new_qty is not None and new_qty != old_qty:
                try:
                    from datetime import datetime
                    now = datetime.now()
                    
                    removed_conn = sqlite3.connect('rackhistory.db')
                    removed_cur = removed_conn.cursor()
                    _ensure_removed_items_table(removed_cur)
                    removed_cur.execute('''
                        INSERT INTO removed_items 
                        (order_id, barcode, title, quantity_removed, removed_at, searchrack_id, old_quantity, new_quantity, removal_type, item_position)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ''', (None, old_barcode, old_title, abs(new_qty - old_qty), now.isoformat(), item_id, old_qty, new_qty, 'manual_edit', old_location or ''))
                    removed_conn.commit()
                    print(f"✅ Logged manual edit to history: {old_title} ({old_qty} → {new_qty})")
                except Exception as log_err:
                    print(f"❌ Error logging to history: {log_err}")
                    import traceback
                    traceback.print_exc()
                finally:
                    removed_conn.close()
        
        # If updating searchRack and quantity is being set to 0, mark for deletion
        if db_key == 'searchRack' and 'quantity' in [k.lower() for k in data.keys()]:
            qty_value = next((v for k, v in data.items() if k.lower() == 'quantity'), None)
            if qty_value == 0:
                # Mark this item for deletion after configured interval
                cur.execute('''
                    CREATE TABLE IF NOT EXISTS zero_qty_pending_deletion (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        searchrack_id INTEGER,
                        marked_at TEXT,
                        delete_at TEXT
                    )
                ''')
                
                # Get configured interval (default 24 hours = 1440 minutes)
                cur.execute('''
                    CREATE TABLE IF NOT EXISTS zero_qty_settings (
                        key TEXT PRIMARY KEY,
                        value TEXT
                    )
                ''')
                cur.execute("SELECT value FROM zero_qty_settings WHERE key = 'interval_minutes'")
                interval_row = cur.fetchone()
                interval_minutes = int(interval_row[0]) if interval_row else 1440
                
                from datetime import datetime, timedelta
                now = datetime.now()
                delete_at = now + timedelta(minutes=interval_minutes)
                
                # Check if already marked
                cur.execute('SELECT id FROM zero_qty_pending_deletion WHERE searchrack_id = ?', (item_id,))
                existing = cur.fetchone()
                
                if not existing:
                    cur.execute('''
                        INSERT INTO zero_qty_pending_deletion (searchrack_id, marked_at, delete_at)
                        VALUES (?, ?, ?)
                    ''', (item_id, now.isoformat(), delete_at.isoformat()))
                    interval_display = f"{interval_minutes} minute{'s' if interval_minutes != 1 else ''}" if interval_minutes < 60 else f"{interval_minutes/60:.1f} hours"
                    print(f"✓ Marked searchRack item {item_id} for deletion in {interval_display}")
                
                conn.commit()
            elif qty_value > 0:
                # If quantity is increased back above 0, remove from deletion queue
                cur.execute('DELETE FROM zero_qty_pending_deletion WHERE searchrack_id = ?', (item_id,))
                if cur.rowcount > 0:
                    print(f"✓ Removed searchRack item {item_id} from deletion queue (quantity > 0)")
                conn.commit()
        
        
        print(f"DEBUG UPDATE: Updated {updated} rows")
        return jsonify({'success': True, 'updated': updated})
    except Exception as e:
        import traceback
        print(f"ERROR UPDATE: {e}")
        print(traceback.format_exc())
        return jsonify({'error': _safe_error(e)}), 500
    finally:
        conn.close()

@app.route('/api/check_shelf_code', methods=['POST'])
def api_check_shelf_code():
    """Check if a shelf code already exists"""
    data = request.get_json() or {}
    code = (data.get('code') or '').strip()
    
    if not code:
        return jsonify({'exists': False, 'reason': 'No code provided'}), 200
    
    try:
        conn = sqlite3.connect('searchRack.db')
        cur = conn.cursor()
        
        # Check if shelf_name already exists
        cur.execute('SELECT shelf_name FROM shelves WHERE shelf_name = ?', (code,))
        result = cur.fetchone()
        
        if result:
            return jsonify({
                'exists': True,
                'reason': f'Shelf "{code}" already exists'
            }), 200
        else:
            return jsonify({
                'exists': False,
                'reason': 'Code available'
            }), 200
            
    except Exception as e:
        print(f"Error checking shelf code: {e}")
        return jsonify({'exists': False, 'reason': 'Error checking code'}), 500
    finally:
        conn.close()


@app.route('/api/create_shelf', methods=['POST'])
def api_create_shelf():
    """Create a new shelf entry. Expects JSON: { shelf_name, location, notes, group_id (optional, defaults to 1) }"""
    data = request.get_json() or {}
    shelf_name = (data.get('shelf_name') or '').strip()
    location = (data.get('location') or '').strip()
    notes = (data.get('notes') or '').strip()
    group_id = data.get('group_id', 1)  # Default to group 1
    
    if not shelf_name:
        return jsonify({'success': False, 'error': 'Shelf name is required'}), 400
    
    try:
        ensure_shelf_groups_table()
        # Store shelves in a simple table in searchRack.db
        conn = sqlite3.connect('searchRack.db')
        cur = conn.cursor()
        
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
        return jsonify({'success': False, 'error': _safe_error(e)}), 500
    finally:
        conn.close()


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
        
        orig_dir = os.path.join(shelves_dir, 'originals')
        os.makedirs(orig_dir, exist_ok=True)
        old_orig_path = os.path.join(orig_dir, f"{old_code}.png")

        # If image provided, overwrite or write to new path
        file = request.files.get('image')
        if file:
            target_code = new_code if new_code else old_code
            target_path = os.path.join(shelves_dir, f"{target_code}.png")
            file.save(target_path)
            
        # If original image provided, save it
        orig_file = request.files.get('original_image')
        if orig_file:
            target_code = new_code if new_code else old_code
            target_orig_path = os.path.join(orig_dir, f"{target_code}.png")
            orig_file.save(target_orig_path)

        # If renaming requested and file exists, rename on disk
        if new_code and new_code != old_code:
            new_path = os.path.join(shelves_dir, f"{new_code}.png")
            # If image was uploaded we already saved to new_path; otherwise rename existing file
            if not os.path.exists(new_path) and os.path.exists(old_path):
                os.rename(old_path, new_path)
            elif os.path.exists(new_path) and os.path.exists(old_path):
                # New file created by upload, remove old file to prevent duplication
                try:
                    os.remove(old_path)
                except Exception as e:
                    print(f"Error removing old shelf file: {e}")
            
            # Rename original if it exists and wasn't just uploaded
            new_orig_path = os.path.join(orig_dir, f"{new_code}.png")
            if not os.path.exists(new_orig_path) and os.path.exists(old_orig_path):
                os.rename(old_orig_path, new_orig_path)
            elif os.path.exists(new_orig_path) and os.path.exists(old_orig_path):
                # New original created by upload, remove old original
                try:
                    os.remove(old_orig_path)
                except Exception as e:
                    print(f"Error removing old original file: {e}")

            # Cascade rename into searchRack.db (SEARCHRACK.ITEM_POSITION)
            try:
                # Update searchRack.db
                sconn = sqlite3.connect('searchRack.db')
                scur = sconn.cursor()
                scur.execute("UPDATE SEARCHRACK SET ITEM_POSITION = ? WHERE LOWER(TRIM(ITEM_POSITION)) = LOWER(TRIM(?))", (new_code, old_code))
                s_updated = scur.rowcount
                
                # Also update the shelves table to maintain metadata link
                scur.execute("UPDATE shelves SET shelf_name = ? WHERE shelf_name = ?", (new_code, old_code))
                
                sconn.commit()
            except Exception:
                s_updated = None
            finally:
                sconn.close()

            # Log rename cascade results
            try:
                with open('clear_shelf.log', 'a', encoding='utf-8') as lf:
                    lf.write(f"SHELF_RENAME: {time.strftime('%Y-%m-%d %H:%M:%S')} {old_code} -> {new_code} searchRack_updated={s_updated}\n")
            except Exception:
                pass

        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': _safe_error(e)}), 500


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
            return jsonify({'success': False, 'error': 'No location column found in items table'}), 400

        conn.commit()

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
        except Exception as e:
            try:
                with open('clear_shelf.log', 'a', encoding='utf-8') as lf:
                    lf.write(f"CLEAR_SEARCH_ERROR: {time.strftime('%Y-%m-%d %H:%M:%S')} error={e}\n")
            except Exception:
                pass
        finally:
            sconn.close()

        # Log final counts
        try:
            with open('clear_shelf.log', 'a', encoding='utf-8') as lf:
                lf.write(f"CLEAR_FINAL: code={repr(code)} inventory_updated={updated} searchrack_updated={search_updated}\n")
        except Exception:
            pass

        return jsonify({'success': True, 'updated': updated, 'search_updated': search_updated})
    except Exception as e:
        return jsonify({'success': False, 'error': _safe_error(e)}), 500
    finally:
        conn.close()


@app.route('/api/list_shelves', methods=['GET'])
def api_list_shelves():
    """Return list of shelf images from static/shelves as JSON, with optional sorting and counts."""
    try:
        sort = (request.args.get('sort') or 'created').lower()
        shelves_dir = os.path.join(app.root_path, 'static', 'shelves')
        results = []
        codes = []
        if os.path.isdir(shelves_dir):
            files = [f for f in os.listdir(shelves_dir) if f.lower().endswith('.png')]
            codes = [os.path.splitext(f)[0] for f in files]

            # Query created_at and group_id from searchRack.db.shelves
            created_map = {}
            group_map = {}
            # counts map from searchRack.db.SEARCHRACK (ITEM_POSITION)
            counts_map = {}
            sdb_path = os.path.join(app.root_path, 'searchRack.db')
            # Query created_at and group_id; ignore if table missing
            try:
                conn = sqlite3.connect(sdb_path)
                cur = conn.cursor()
                if codes:
                    placeholders = ','.join('?' for _ in codes)
                    cur.execute(f"SELECT shelf_name, created_at, group_id FROM shelves WHERE shelf_name IN ({placeholders})", tuple(codes))
                    for r in cur.fetchall():
                        created_map[r[0]] = r[1]
                        group_map[r[0]] = r[2]
            except Exception:
                created_map = {}
                group_map = {}
            finally:
                conn.close()
            # Query counts; handle missing table separately
            try:
                conn = sqlite3.connect(sdb_path)
                cur = conn.cursor()
                cur.execute("SELECT LOWER(TRIM(ITEM_POSITION)) as pos, COUNT(*) FROM SEARCHRACK GROUP BY LOWER(TRIM(ITEM_POSITION))")
                for pos, cnt in cur.fetchall():
                    counts_map[pos] = cnt
            except Exception:
                counts_map = {}
            finally:
                conn.close()

            for fname in files:
                code = os.path.splitext(fname)[0]
                path = os.path.join(shelves_dir, fname)
                try:
                    mtime = os.path.getmtime(path)
                except Exception:
                    mtime = 0
                url = url_for('static', filename=f'shelves/{fname}')
                count = counts_map.get(code.lower().strip(), 0)
                # Default to group 1 (Ungrouped) if not set
                group_id = group_map.get(code, 1)
                results.append({
                    'code': code, 
                    'filename': fname, 
                    'url': url, 
                    'lastModified': int(mtime), 
                    'created_at': created_map.get(code), 
                    'count': int(count),
                    'group_id': group_id
                })

            # Apply server-side sorting
            if sort == 'items':
                results.sort(key=lambda x: x.get('count', 0), reverse=True)
            elif sort == 'created':
                # Sort by created_at desc; fallback to lastModified desc
                def created_key(x):
                    ca = x.get('created_at')
                    # If created_at exists, use it. Ensure consistent format (replace T with space)
                    if ca:
                        return str(ca).replace('T', ' ')
                    
                    # Fallback to lastModified
                    try:
                        ts = x.get('lastModified', 0)
                        # Use space separator to match SQLite default
                        return datetime.datetime.fromtimestamp(ts).strftime('%Y-%m-%d %H:%M:%S')
                    except Exception:
                        return '1970-01-01 00:00:00'
                
                results.sort(key=created_key, reverse=True)
            else:
                # name
                results.sort(key=lambda x: (x.get('code') or '').lower())
        return jsonify({'success': True, 'shelves': results})
    except Exception as e:
        return jsonify({'success': False, 'error': _safe_error(e)}), 500


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
        except Exception:
            for code in codes:
                result[code] = {'searchrack': 0}
        finally:
            sconn.close()

        return jsonify({'success': True, 'counts': result})
    except Exception as e:
        return jsonify({'success': False, 'error': _safe_error(e)}), 500


# ============================================================================
# SHELF GROUPS API
# ============================================================================

def ensure_shelf_groups_table():
    """Ensure shelf_groups table exists and shelves table has group_id column"""
    try:
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
        
        conn.commit()
        return True
    except Exception as e:
        print(f'[SHELF_GROUPS] Error ensuring tables: {e}')
        import traceback
        traceback.print_exc()
        return False
    finally:
        conn.close()

@app.route('/api/groups', methods=['GET'])
def api_get_groups():
    """Get all shelf groups with shelf counts"""
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
                COUNT(s.id) as shelf_count
            FROM shelf_groups g
            LEFT JOIN shelves s ON s.group_id = g.id
            GROUP BY g.id
            ORDER BY g.id
        ''')
        
        groups = [dict(row) for row in cur.fetchall()]
        
        return jsonify({'success': True, 'groups': groups})
    except Exception as e:
        print(f'[api_get_groups] Error: {e}')
        return jsonify({'success': False, 'error': _safe_error(e)}), 500
    finally:
        conn.close()

@app.route('/api/create_group', methods=['POST'])
def api_create_group():
    """Create a new shelf group"""
    try:
        data = request.get_json() or {}
        name = (data.get('name') or '').strip()
        
        if not name:
            return jsonify({'success': False, 'error': 'Group name is required'}), 400
        
        ensure_shelf_groups_table()
        conn = sqlite3.connect('searchRack.db')
        cur = conn.cursor()
        
        cur.execute('INSERT INTO shelf_groups (name) VALUES (?)', (name,))
        group_id = cur.lastrowid
        
        conn.commit()
        
        return jsonify({
            'success': True,
            'group_id': group_id,
            'message': f'Group "{name}" created successfully'
        })
    except Exception as e:
        return jsonify({'success': False, 'error': _safe_error(e)}), 500
    finally:
        conn.close()

@app.route('/api/delete_group', methods=['POST'])
def api_delete_group():
    """Delete a shelf group and move its shelves to Default Group (id=1)"""
    try:
        data = request.get_json() or {}
        group_id = data.get('group_id')
        
        if not group_id:
            return jsonify({'success': False, 'error': 'group_id is required'}), 400
        
        if int(group_id) == 1:
            return jsonify({'success': False, 'error': 'Cannot delete Default Group'}), 400
        
        ensure_shelf_groups_table()
        conn = sqlite3.connect('searchRack.db')
        cur = conn.cursor()
        
        # Move all shelves from this group to Default Group (id=1)
        cur.execute('UPDATE shelves SET group_id = 1 WHERE group_id = ?', (group_id,))
        moved_count = cur.rowcount
        
        # Delete the group
        cur.execute('DELETE FROM shelf_groups WHERE id = ?', (group_id,))
        
        conn.commit()
        
        return jsonify({
            'success': True,
            'moved_shelves': moved_count,
            'message': f'Group deleted. {moved_count} shelf(es) moved to Default Group.'
        })
    except Exception as e:
        return jsonify({'success': False, 'error': _safe_error(e)}), 500
    finally:
        conn.close()

@app.route('/api/move_shelves', methods=['POST'])
def api_move_shelves():
    """Move shelves to a different group. Accepts shelf_codes (list of shelf names) and group_id (or target_group_id)."""
    try:
        data = request.get_json() or {}
        shelf_codes = data.get('shelf_codes', [])
        target_group_id = data.get('group_id') or data.get('target_group_id')
        
        if not shelf_codes or target_group_id is None:
            return jsonify({'success': False, 'error': 'shelf_codes and group_id are required'}), 400
        
        ensure_shelf_groups_table()
        conn = sqlite3.connect('searchRack.db')
        cur = conn.cursor()
        
        # Verify target group exists
        cur.execute('SELECT id FROM shelf_groups WHERE id = ?', (target_group_id,))
        if not cur.fetchone():
            return jsonify({'success': False, 'error': 'Target group does not exist'}), 404
        
        # For each shelf code, ensure it exists in the shelves table (create if missing)
        for code in shelf_codes:
            cur.execute('SELECT id FROM shelves WHERE shelf_name = ? COLLATE NOCASE', (code,))
            if not cur.fetchone():
                # Shelf doesn't exist in table, create it
                cur.execute('INSERT INTO shelves (shelf_name, group_id) VALUES (?, ?)', (code, target_group_id))
                print(f'[MOVE_SHELVES] Created missing shelf entry for {code}')
        
        # Now update all shelves to the target group
        placeholders = ','.join('?' for _ in shelf_codes)
        cur.execute(f'UPDATE shelves SET group_id = ? WHERE shelf_name IN ({placeholders}) COLLATE NOCASE', 
                   [target_group_id] + shelf_codes)
        moved_count = cur.rowcount
        
        conn.commit()
        
        return jsonify({
            'success': True,
            'moved_count': moved_count,
            'message': f'{moved_count} shelf(es) moved successfully'
        })
    except Exception as e:
        return jsonify({'success': False, 'error': _safe_error(e)}), 500
    finally:
        conn.close()

@app.route('/api/upload_shelf', methods=['POST'])
def api_upload_shelf():
    """Upload a shelf image and associate it with a group"""
    try:
        group_id = request.form.get('group_id', 1)  # Default to group 1
        
        if 'image' not in request.files:
            return jsonify({'success': False, 'error': 'No image file provided'}), 400
        
        file = request.files['image']
        if not file.filename:
            return jsonify({'success': False, 'error': 'Empty filename'}), 400
        
        # Use provided code or generate a unique shelf code
        shelf_code = request.form.get('code')
        if not shelf_code:
            import uuid
            shelf_code = str(uuid.uuid4())[:8].upper()
        
        # Sanitize shelf_code to be safe for filenames
        import re
        shelf_code = re.sub(r'[<>:"/\\|?*]', '_', shelf_code)
        
        # Save the image
        shelves_dir = os.path.join('static', 'shelves')
        os.makedirs(shelves_dir, exist_ok=True)
        file_path = os.path.join(shelves_dir, f"{shelf_code}.png")
        file.save(file_path)
        
        # Save original image if provided
        if 'original_image' in request.files:
            orig_file = request.files['original_image']
            if orig_file.filename:
                orig_dir = os.path.join(shelves_dir, 'originals')
                os.makedirs(orig_dir, exist_ok=True)
                orig_path = os.path.join(orig_dir, f"{shelf_code}.png")
                orig_file.save(orig_path)
        
        # Create or update shelf entry in database
        ensure_shelf_groups_table()
        conn = sqlite3.connect('searchRack.db')
        cur = conn.cursor()
        
        # Check if shelf already exists
        cur.execute('SELECT id FROM shelves WHERE shelf_name = ?', (shelf_code,))
        existing_row = cur.fetchone()
        
        if existing_row:
            # Update existing shelf's group
            cur.execute('UPDATE shelves SET group_id = ? WHERE id = ?', (group_id, existing_row[0]))
        else:
            # Insert new shelf
            cur.execute('''
                INSERT INTO shelves (shelf_name, group_id, created_at)
                VALUES (?, ?, CURRENT_TIMESTAMP)
            ''', (shelf_code, group_id))
        
        conn.commit()
        
        return jsonify({
            'success': True,
            'code': shelf_code,
            'message': f'Shelf {shelf_code} uploaded successfully'
        })
    except Exception as e:
        return jsonify({'success': False, 'error': _safe_error(e)}), 500
    finally:
        conn.close()

@app.route('/api/delete_shelf', methods=['POST'])
def api_delete_shelf():
    """Delete a shelf and its associated files"""
    try:
        data = request.get_json() or {}
        shelf_id = data.get('shelf_id')
        shelf_code = data.get('code')
        
        print(f"[DELETE_SHELF] Request received for id={shelf_id}, code={shelf_code}")

        if not shelf_id and not shelf_code:
            return jsonify({'success': False, 'error': 'shelf_id or code is required'}), 400
        
        # Use a timeout for the connection
        conn = sqlite3.connect('searchRack.db', timeout=10.0)
        cur = conn.cursor()
        
        code_to_delete = None
        
        # Get shelf info
        if shelf_id:
            cur.execute('SELECT shelf_name FROM shelves WHERE id = ?', (shelf_id,))
            row = cur.fetchone()
            if row:
                code_to_delete = row[0]
                # Delete shelf from database
                cur.execute('DELETE FROM shelves WHERE id = ?', (shelf_id,))
                print(f"[DELETE_SHELF] Deleted shelf ID {shelf_id} from DB")
            else:
                print(f"[DELETE_SHELF] Shelf ID {shelf_id} not found in DB")
                return jsonify({'success': False, 'error': 'Shelf not found'}), 404
        else:
            # Try case-insensitive match first
            cur.execute('SELECT id, shelf_name FROM shelves WHERE shelf_name = ? COLLATE NOCASE', (shelf_code,))
            row = cur.fetchone()
            if row:
                shelf_id = row[0]
                code_to_delete = row[1] # Use the actual name from DB
                # Delete shelf from database
                cur.execute('DELETE FROM shelves WHERE id = ?', (shelf_id,))
                print(f"[DELETE_SHELF] Deleted shelf '{code_to_delete}' (ID {shelf_id}) from DB")
            else:
                # Shelf not in DB, but we have the code, so we can try to delete the file
                code_to_delete = shelf_code
                print(f"[DELETE_SHELF] Shelf '{shelf_code}' not found in DB, proceeding to file deletion")
        
        conn.commit()
        
        # Delete shelf image file
        if code_to_delete:
            # Try exact match first
            shelf_file = os.path.join('static', 'shelves', f"{code_to_delete}.png")
            orig_file = os.path.join('static', 'shelves', 'originals', f"{code_to_delete}.png")
            
            if os.path.exists(shelf_file):
                try:
                    os.remove(shelf_file)
                    print(f"[DELETE_SHELF] Deleted file: {shelf_file}")
                except Exception as e:
                    print(f"[DELETE_SHELF] Error deleting file {shelf_file}: {e}")
            
            if os.path.exists(orig_file):
                try:
                    os.remove(orig_file)
                    print(f"[DELETE_SHELF] Deleted original file: {orig_file}")
                except Exception as e:
                    print(f"[DELETE_SHELF] Error deleting original file {orig_file}: {e}")
            
            if not os.path.exists(shelf_file):
                # Try case-insensitive search for file
                print(f"[DELETE_SHELF] File {shelf_file} not found, trying case-insensitive search")
                shelves_dir = os.path.join('static', 'shelves')
                if os.path.exists(shelves_dir):
                    for f in os.listdir(shelves_dir):
                        if f.lower() == f"{code_to_delete}.png".lower():
                            full_path = os.path.join(shelves_dir, f)
                            try:
                                os.remove(full_path)
                                print(f"[DELETE_SHELF] Deleted file (case-insensitive match): {full_path}")
                            except Exception as e:
                                print(f"[DELETE_SHELF] Error deleting file {full_path}: {e}")
                            break
        
        return jsonify({
            'success': True,
            'message': f'Shelf {code_to_delete} deleted successfully'
        })
    except Exception as e:
        print(f"[DELETE_SHELF] Error: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': _safe_error(e)}), 500
    finally:
        conn.close()

@app.route('/api/valid_shelves', methods=['GET'])
def api_valid_shelves():
    """Get list of valid shelf codes from the database"""
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
        return jsonify({'success': False, 'error': _safe_error(e), 'codes': []}), 500
    finally:
        conn.close()

@app.route('/api/location_hierarchy', methods=['GET'])
def api_location_hierarchy():
    """Get location hierarchy with item counts for searchRack inventory"""
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
            LEFT JOIN SEARCHRACK sr ON LOWER(TRIM(sr.ITEM_POSITION)) = LOWER(TRIM(s.shelf_name))
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
        return jsonify({'success': False, 'error': _safe_error(e)}), 500
    finally:
        conn.close()


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
            return jsonify({'error': 'Row not found'}), 404
        rowdict = dict(row)
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
        deleted_at = datetime.datetime.now(datetime.UTC).isoformat()
        dcur.execute('INSERT INTO deleted_items (source_db, source_table, source_pk, source_id, deleted_at, data_json) VALUES (?,?,?,?,?,?)',
                     (db_key, table, pk, str(item_id), deleted_at, json.dumps(rowdict)))
        dconn.commit()
        archive_id = dcur.lastrowid
        # Log to rackhistory if deleting from searchRack
        if db_key == 'searchRack':
            try:
                removed_conn = sqlite3.connect('rackhistory.db')
                removed_cur = removed_conn.cursor()
                _ensure_removed_items_table(removed_cur)
                
                # Extract details from the archived row data
                item_title = rowdict.get('TITLE') or rowdict.get('title', '')
                item_barcode = rowdict.get('BARCODE') or rowdict.get('barcode', '')
                old_qty = rowdict.get('QUANTITY') or rowdict.get('quantity', 0)
                item_location = rowdict.get('ITEM_POSITION') or rowdict.get('item_position', '')
                
                removed_cur.execute('''
                    INSERT INTO removed_items 
                    (order_id, barcode, title, quantity_removed, removed_at, searchrack_id, old_quantity, new_quantity, removal_type, item_position)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''', (None, item_barcode, item_title, old_qty, deleted_at, item_id, old_qty, 0, 'manual_multidb_delete', item_location))
                removed_conn.commit()
            except Exception as log_err:
                print(f"Warning: Could not log multi-db deletion to removed_items: {log_err}")
            finally:
                removed_conn.close()
        
        conn2 = sqlite3.connect(src_db)
        try:
            cur2 = conn2.cursor()
            cur2.execute(f"DELETE FROM {table} WHERE {pk} = ?", (item_id,))
            conn2.commit()
        finally:
            conn2.close()
        return jsonify({'success': True, 'archived_id': archive_id})
    except Exception as e:
        return jsonify({'error': _safe_error(e)}), 500
    finally:
        conn.close()
        dconn.close()


@app.route('/api/undelete/<int:archive_id>', methods=['POST'])
def api_undelete(archive_id):
    try:
        dconn = sqlite3.connect('deleted.db')
        dconn.row_factory = sqlite3.Row
        dcur = dconn.cursor()
        dcur.execute('SELECT * FROM deleted_items WHERE id = ?', (archive_id,))
        row = dcur.fetchone()
        if not row:
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
            dcur.execute('DELETE FROM deleted_items WHERE id = ?', (archive_id,))
            dconn.commit()
            return jsonify({'success': True})
        else:
            return jsonify({'error': 'No insertable columns found'}), 400
    except Exception as e:
        return jsonify({'error': _safe_error(e)}), 500
    finally:
        dconn.close()
        conn.close()


@app.route('/api/location_duplicates', methods=['GET'])
def api_location_duplicates():
    """Return items that have the same barcode in multiple locations - ONE ROW per barcode with locations array"""
    try:
        conn = sqlite3.connect('searchRack.db')
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        
        # Get quantity column name
        cur.execute('PRAGMA table_info(SEARCHRACK)')
        cols = [r[1] for r in cur.fetchall()]
        qty_col = 'QUANTITY' if 'QUANTITY' in cols else ('QTY' if 'QTY' in cols else 'quantity')
        
        # Find barcodes that appear in multiple locations (non-suffixed barcodes only)
        cur.execute(f'''
            SELECT 
                BARCODE,
                MAX(TITLE) as TITLE,
                MAX(IMAGE) as IMAGE,
                MAX(PICTUREPOSITION) as PICTUREPOSITION,
                MAX(CREATED_AT) as CREATED_AT,
                GROUP_CONCAT(ID || ':' || ITEM_POSITION || ':' || {qty_col} || ':' || COALESCE(PICTUREPOSITION, ''), '|') as location_data,
                SUM(CAST({qty_col} AS INTEGER)) as total_quantity
            FROM SEARCHRACK
            WHERE BARCODE IS NOT NULL 
            AND TRIM(BARCODE) != ''
            AND BARCODE NOT LIKE '%-%'
            AND ITEM_POSITION IS NOT NULL
            AND TRIM(ITEM_POSITION) != ''
            GROUP BY BARCODE
            HAVING COUNT(DISTINCT ITEM_POSITION) > 1
            ORDER BY BARCODE
        ''')
        
        grouped_rows = cur.fetchall()
        results = []
        total_qty = 0
        
        for group_row in grouped_rows:
            barcode = group_row['BARCODE']
            title = group_row['TITLE'] or 'Unknown'
            image = group_row['IMAGE'] or ''
            created_at = group_row['CREATED_AT']
            location_data = group_row['location_data']
            group_total_qty = group_row['total_quantity'] or 0
            
            # Parse location data: "id:location:qty:picturepos|..."
            locations_list = []
            
            for loc_entry in location_data.split('|'):
                parts = loc_entry.split(':')
                if len(parts) >= 3:
                    item_id = parts[0]
                    location = parts[1]
                    qty_str = parts[2]
                    picturepos = parts[3] if len(parts) > 3 else ''
                    
                    try:
                        qty = int(float(qty_str)) if qty_str else 0
                    except Exception:
                        qty = 0
                    
                    locations_list.append({
                        'id': item_id,
                        'code': location,
                        'quantity': qty,
                        'image': picturepos if picturepos else location
                    })
            
            total_qty += group_total_qty
            
            # Create ONE result entry per barcode with locations array
            result = {
                'id': locations_list[0]['id'] if locations_list else None,  # Use first ID as primary
                'barcode': barcode,
                'title': title,
                'quantity': group_total_qty,
                'locations': locations_list,  # Array of all locations
                'created_at': created_at,
                'image': image,
                'source_db': 'searchRack',
                'is_duplicate_group': True
            }
            results.append(result)
        
        
        return jsonify({
            'results': results,
            'total_quantity': total_qty,
            'total_groups': len(grouped_rows)
        })
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'error': _safe_error(e), 'results': []}), 500
    finally:
        conn.close()

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
                except Exception:
                    pass
                finally:
                    es_conn.close()
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
                    except Exception:
                        pass
                    finally:
                        bol_conn.close()

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
        # IMPORTANT: Store ALL archive IDs in merged_archive_ids so we can delete all entries at once
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
                    # Store array of all archive IDs that were merged into this row
                    r_copy['merged_archive_ids'] = [r.get('id')]
                    merged[key] = r_copy
                else:
                    merged[key]['quantity'] = merged[key].get('quantity', 0) + n
                    # Append this archive ID to the list
                    if 'merged_archive_ids' not in merged[key]:
                        merged[key]['merged_archive_ids'] = []
                    merged[key]['merged_archive_ids'].append(r.get('id'))
            results = list(merged.values())
        return jsonify({'results': results})
    except Exception as e:
        return jsonify({'error': _safe_error(e)}), 500
    finally:
        conn.close()


@app.route('/api/delete_archive/<int:archive_id>', methods=['POST'])
def api_delete_archive(archive_id):
    try:
        conn = sqlite3.connect('deleted.db')
        cur = conn.cursor()
        cur.execute('DELETE FROM deleted_items WHERE id = ?', (archive_id,))
        conn.commit()
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'error': _safe_error(e)}), 500
    finally:
        conn.close()

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
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': _safe_error(e)}), 500
    finally:
        conn.close()

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
        return jsonify({'error': _safe_error(e)}), 500
    finally:
        conn.close()

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
        
        return jsonify({'success': True})
    except Exception as e:
        print(f"❌ Error saving settings: {e}")
        return jsonify({'success': False, 'error': _safe_error(e)}), 500
    finally:
        conn.close()

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
    except Exception as e:
        print(f"⚠️ Error updating sync timestamp: {e}")
    finally:
        conn.close()

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
                
                # Enrich with images from amazonStore.db
                from DBmanager import enrich_amazon_sold_images
                enrich_amazon_sold_images()
                
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
        return jsonify({'success': False, 'message': _safe_error(e, 'sync all')}), 500

@app.route('/api/sync/ebay-orders', methods=['POST'])
def sync_ebay_orders_api():
    """Sync eBay orders"""
    try:
        # Use the local eBay orders sync function defined in this file
        orders()
        
        # Sync eBay seller fees
        try:
            from ebay_manager import EbayManager
            em = EbayManager()
            fees_count = em.sync_fees_to_db(days_back=90)
            print(f"✅ Synced fees for {fees_count} eBay orders")
        except Exception as e:
            print(f"⚠️ Error syncing eBay fees: {e}")
        
        # Sync eBay returns
        try:
            from ebay_manager import EbayManager
            em = EbayManager()
            returns_count = em.sync_returns_to_db(days_back=90)
            print(f"✅ Synced {returns_count} eBay returns")
        except Exception as e:
            print(f"⚠️ Error syncing eBay returns: {e}")
        
        # Auto-enrich recent orders with LOT numbers
        try:
            from lot_matcher import enrich_new_orders
            enrich_result = enrich_new_orders(days_back=7)
            if enrich_result.get('success'):
                print(f"✅ Enriched {enrich_result.get('enriched', 0)} eBay orders with LOT numbers")
        except Exception as e:
            print(f"⚠️ Error enriching eBay orders with LOTs: {e}")
        
        update_sync_timestamp('ebay_orders')
        return jsonify({'success': True, 'message': 'eBay orders synced successfully'})
    except Exception as e:
        return jsonify({'success': False, 'message': _safe_error(e, 'ebay orders sync')}), 500

@app.route('/api/sync/ebay-listings', methods=['POST'])
def sync_ebay_listings_api():
    """Sync eBay listings and enrich with Amazon data"""
    try:
        from DBmanager import enrich_searchrack_db
        enrich_searchrack_db(batch_size=500, do_backup=False)  # Enrich with eBay, BOL, and Amazon data
        update_sync_timestamp('ebay_listings')
        return jsonify({'success': True, 'message': 'eBay listings synced and enriched successfully'})
    except Exception as e:
        return jsonify({'success': False, 'message': _safe_error(e, 'ebay listings sync')}), 500

@app.route('/api/sync/amazon-orders', methods=['POST'])
def sync_amazon_orders_api():
    """Sync Amazon orders"""
    if not AMAZON_AVAILABLE:
        return jsonify({'success': False, 'message': 'Amazon integration not available'}), 500
    try:
        amazon = AmazonManager()
        amazon.sync_orders_to_db(days_back=30)
        
        # Sync returns
        try:
            returns_count = amazon.sync_returns_to_db(days_back=90)
            print(f"✅ Synced {returns_count} Amazon returns")
        except Exception as e:
            print(f"⚠️ Error syncing Amazon returns: {e}")
        
        # Enrich with images from amazonStore.db
        from DBmanager import enrich_amazon_sold_images
        enrich_amazon_sold_images()
        
        # ADDITIONAL: Enrich barcodes for orders that don't have them yet
        try:
            sold_conn = sqlite3.connect('sold.db')
            sold_cur = sold_conn.cursor()
            
            amazon_conn = sqlite3.connect('amazonStore.db')
            amazon_conn.row_factory = sqlite3.Row
            amazon_cur = amazon_conn.cursor()
            
            # Find Amazon orders without barcodes
            sold_cur.execute('''
                SELECT id, item_id FROM orders 
                WHERE store = 'amazon' 
                AND (barcode IS NULL OR barcode = '' OR barcode = item_id)
                AND item_id IS NOT NULL
            ''')
            
            orders_to_enrich = sold_cur.fetchall()
            enriched = 0
            
            for order_id, asin in orders_to_enrich:
                # Look up UPC in amazonStore.db
                amazon_cur.execute('SELECT UPC FROM ITEMS WHERE ASIN = ? COLLATE NOCASE', (asin,))
                row = amazon_cur.fetchone()
                
                if row and row['UPC'] and row['UPC'] != asin:
                    # Update sold order with proper UPC
                    sold_cur.execute('UPDATE orders SET barcode = ? WHERE id = ?', (row['UPC'], order_id))
                    enriched += 1
            
            sold_conn.commit()
            
            print(f"✅ Enriched {enriched} Amazon orders with barcodes")
            
        except Exception as e:
            print(f"⚠️ Error enriching Amazon order barcodes: {e}")
        finally:
            sold_conn.close()
            amazon_conn.close()
        
        # Auto-enrich recent orders with LOT numbers
        try:
            from lot_matcher import enrich_new_orders
            enrich_result = enrich_new_orders(days_back=7)
            if enrich_result.get('success'):
                print(f"✅ Enriched {enrich_result.get('enriched', 0)} Amazon orders with LOT numbers")
        except Exception as e:
            print(f"⚠️ Error enriching Amazon orders with LOTs: {e}")
        
        from DBmanager import process_sold_orders_inventory_reduction
        process_sold_orders_inventory_reduction()
        update_sync_timestamp('amazon_orders')
        return jsonify({'success': True, 'message': 'Amazon orders synced successfully'})
    except Exception as e:
        return jsonify({'success': False, 'message': _safe_error(e, 'amazon orders sync')}), 500

@app.route('/api/sync/amazon-listings', methods=['POST'])
def sync_amazon_listings_api():
    """Sync Amazon listings with quota protection"""
    if not AMAZON_AVAILABLE:
        return jsonify({'success': False, 'message': 'Amazon integration not available'}), 500
    try:
        amazon = AmazonManager()
        result = amazon.sync_listings_to_db()
        
        # Handle quota exceeded
        if result == -1:
            return jsonify({
                'success': False, 
                'message': 'Amazon listings sync quota exceeded - please wait 24 hours',
                'quota_exceeded': True
            }), 429  # 429 Too Many Requests
        
        # Handle no changes
        if result == 0:
            return jsonify({
                'success': True, 
                'message': 'No listings to sync (last sync was recent)'
            })
        
        update_sync_timestamp('amazon_listings')
        return jsonify({
            'success': True, 
            'message': f'Amazon listings synced successfully ({result} items)'
        })
    except Exception as e:
        return jsonify({'success': False, 'message': _safe_error(e, 'amazon listings sync')}), 500

def sync_missing_upcs(max_items=25):
    """
    Sync UPCs for Amazon items that don't have them (where UPC = ASIN)
    
    Args:
        max_items: Maximum number of items to process per run (default 25 to avoid quota issues)
    
    Returns:
        Number of UPCs found
    """
    import time
    from datetime import datetime, timedelta
    
    try:
        conn = sqlite3.connect('amazonStore.db')
        cur = conn.cursor()
        
        # Ensure tracking columns exist
        try:
            cur.execute('ALTER TABLE ITEMS ADD COLUMN upc_fetch_attempted INTEGER DEFAULT 0')
            conn.commit()
        except Exception:
            pass
        try:
            cur.execute('ALTER TABLE ITEMS ADD COLUMN upc_last_fetch_date TEXT')
            conn.commit()
        except Exception:
            pass
        
        # Get retry interval from sync settings (use sync_interval in minutes)
        # Default to 60 minutes if not set
        retry_minutes = 60
        try:
            settings_conn = sqlite3.connect('sync_settings.db')
            settings_cur = settings_conn.cursor()
            settings_cur.execute('SELECT value FROM sync_status WHERE key = ?', ('sync_interval',))
            row = settings_cur.fetchone()
            if row:
                retry_minutes = int(row[0])
        except Exception:
            pass  # Use default if settings not available
        finally:
            settings_conn.close()
        
        # Find items that need UPC fetching using smart tracking:
        # - upc_fetch_attempted = 0 (never tried) OR
        # - upc_fetch_attempted = 2 AND last fetch > retry_interval ago (retry after error)
        # - EXCLUDE upc_fetch_attempted = 1 (no UPC exists, skip forever)
        # - EXCLUDE upc_fetch_attempted = 3 (successfully fetched)
        retry_threshold = (datetime.now() - timedelta(minutes=retry_minutes)).isoformat()
        
        cur.execute('''
            SELECT ASIN FROM ITEMS 
            WHERE (UPC = ASIN OR UPC IS NULL OR UPC = '')
            AND (
                upc_fetch_attempted = 0 
                OR upc_fetch_attempted IS NULL
                OR (upc_fetch_attempted = 2 AND (upc_last_fetch_date IS NULL OR upc_last_fetch_date < ?))
            )
            ORDER BY upc_last_fetch_date ASC NULLS FIRST
            LIMIT ?
        ''', (retry_threshold, max_items))
        items_without_upcs = [row[0] for row in cur.fetchall()]
    except Exception as e:
        print(f"❌ Database error in sync_missing_upcs: {e}")
        import traceback
        traceback.print_exc()
        raise
    finally:
        conn.close()
    
    if not items_without_upcs:
        print("✅ No items need UPC fetching (using smart tracking)")
        return 0
    
    print(f"🔍 Found {len(items_without_upcs)} items without UPCs (limited to {max_items}). Fetching...")
    
    try:
        amazon = AmazonManager()
        print("✅ AmazonManager initialized")
    except Exception as e:
        print(f"❌ Failed to initialize AmazonManager: {e}")
        raise
    
    upcs_found = 0
    items_processed = 0
    
    quota_exceeded_count = 0
    
    for i, asin in enumerate(items_without_upcs, 1):
        try:
            # Get catalog item
            catalog_data = amazon.get_catalog_item(asin)
            items_processed += 1
            
            if not catalog_data:
                # API error - mark for retry based on sync interval (status 2)
                cur.execute('''
                    UPDATE ITEMS 
                    SET upc_fetch_attempted = 2, 
                        upc_last_fetch_date = ? 
                    WHERE ASIN = ?
                ''', (datetime.now().isoformat(), asin))
                retry_text = f"{retry_minutes} minutes" if retry_minutes < 60 else f"{retry_minutes // 60} hours"
                print(f"⚠️ [{i}/{len(items_without_upcs)}] {asin}: No catalog data (will retry in {retry_text})")
                conn.commit()
                time.sleep(1.0)  # Increased delay
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
                # UPC found - mark as successfully fetched (status 3)
                cur.execute('''
                    UPDATE ITEMS 
                    SET UPC = ?, 
                        upc_fetch_attempted = 3, 
                        upc_last_fetch_date = ? 
                    WHERE ASIN = ?
                ''', (upc, datetime.now().isoformat(), asin))
                upcs_found += 1
                print(f"✅ [{i}/{len(items_without_upcs)}] {asin}: {upc}")
            else:
                # No UPC exists in catalog - mark to skip forever (status 1)
                cur.execute('''
                    UPDATE ITEMS 
                    SET upc_fetch_attempted = 1, 
                        upc_last_fetch_date = ? 
                    WHERE ASIN = ?
                ''', (datetime.now().isoformat(), asin))
                print(f"⚠️ [{i}/{len(items_without_upcs)}] {asin}: No UPC found (marked to skip)")
            
            # Commit after each item to preserve progress
            conn.commit()
            
            # Rate limiting: Catalog Items API allows 2 requests per second
            # Use 1 second delay to avoid quota exhaustion (1 per second is safer)
            time.sleep(1.0)
            
        except Exception as e:
            # Check if quota exceeded
            error_str = str(e)
            if 'QuotaExceeded' in error_str or 'quota' in error_str.lower():
                quota_exceeded_count += 1
                print(f"⚠️ [{i}/{len(items_without_upcs)}] {asin}: Quota exceeded, stopping UPC sync")
                print(f"💡 {upcs_found} UPCs fetched before hitting quota. Will retry remaining items in {retry_minutes} minutes.")
                break  # Stop processing to avoid further quota violations
            
            # API error - mark for retry based on sync interval (status 2)
            cur.execute('''
                UPDATE ITEMS 
                SET upc_fetch_attempted = 2, 
                    upc_last_fetch_date = ? 
                WHERE ASIN = ?
            ''', (datetime.now().isoformat(), asin))
            conn.commit()
            retry_text = f"{retry_minutes} minutes" if retry_minutes < 60 else f"{retry_minutes // 60} hours"
            print(f"❌ [{i}/{len(items_without_upcs)}] {asin}: Error - {e} (will retry in {retry_text})")
            time.sleep(1.0)  # Increased delay
    
    
    if quota_exceeded_count > 0:
        print(f"\n⚠️ UPC sync stopped: Amazon API quota exceeded")
        print(f"✅ Progress saved: {upcs_found} UPCs found from {items_processed} items checked")
        print(f"💡 Remaining items will be retried in {retry_minutes} minutes")
    else:
        print(f"\n✅ UPC sync complete: {upcs_found} UPCs found from {items_processed} items checked")
    
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
        return jsonify({'success': False, 'message': _safe_error(e, 'amazon UPC sync')}), 500

@app.route('/api/sync/recover-upcs', methods=['POST'])
def recover_corrupted_upcs_api():
    """One-time bulk recovery for corrupted UPCs (UPC = ASIN but status = 3)"""
    if not AMAZON_AVAILABLE:
        return jsonify({'success': False, 'message': 'Amazon integration not available'}), 500
    try:
        print("🔍 Scanning for corrupted UPCs...")
        
        conn = sqlite3.connect('amazonStore.db')
        cur = conn.cursor()
        
        # Find all items where UPC = ASIN but upc_fetch_attempted = 3 (corrupted)
        cur.execute('''
            SELECT ASIN, UPC, upc_fetch_attempted 
            FROM ITEMS 
            WHERE UPC = ASIN AND upc_fetch_attempted = 3
        ''')
        corrupted_items = cur.fetchall()
        
        if not corrupted_items:
            return jsonify({
                'success': True, 
                'message': 'No corrupted UPCs found - all items are healthy',
                'count': 0
            })
        
        print(f"📋 Found {len(corrupted_items)} corrupted UPCs:")
        for item in corrupted_items[:5]:  # Show first 5
            print(f"   - ASIN: {item[0]}, UPC: {item[1]}")
        if len(corrupted_items) > 5:
            print(f"   ... and {len(corrupted_items) - 5} more")
        
        # Reset tracking for all corrupted items
        cur.execute('''
            UPDATE ITEMS 
            SET upc_fetch_attempted = 0, upc_last_fetch_date = NULL
            WHERE UPC = ASIN AND upc_fetch_attempted = 3
        ''')
        conn.commit()
        reset_count = cur.rowcount
        
        print(f"✅ Reset tracking for {reset_count} corrupted UPCs")
        
        return jsonify({
            'success': True,
            'message': f'Reset {reset_count} corrupted UPCs - run "Fetch Missing Amazon UPCs" to re-fetch them',
            'count': reset_count
        })
        
    except Exception as e:
        import traceback
        error_details = traceback.format_exc()
        print(f"❌ UPC recovery error: {e}")
        print(error_details)
        return jsonify({'success': False, 'message': _safe_error(e, 'UPC recovery')}), 500
    finally:
        conn.close()

@app.route('/api/sync/fix-amazon-barcodes', methods=['POST'])
def fix_amazon_barcodes():
    """One-time fix: Enrich Amazon orders in sold.db with barcodes and images from amazonStore.db"""
    try:
        sold_conn = sqlite3.connect('sold.db')
        sold_cur = sold_conn.cursor()
        
        amazon_conn = sqlite3.connect('amazonStore.db')
        amazon_conn.row_factory = sqlite3.Row
        amazon_cur = amazon_conn.cursor()
        
        # Find ALL Amazon orders without proper barcodes (where barcode = ASIN or null)
        sold_cur.execute('''
            SELECT id, item_id, barcode, image FROM orders 
            WHERE store = 'amazon' 
            AND item_id IS NOT NULL
        ''')
        
        all_amazon_orders = sold_cur.fetchall()
        fixed_barcodes = 0
        fixed_images = 0
        
        print(f"🔍 Scanning {len(all_amazon_orders)} Amazon orders...")
        
        for order_id, asin, current_barcode, current_image in all_amazon_orders:
            # Look up in amazonStore.db
            amazon_cur.execute('SELECT UPC, IMAGE FROM ITEMS WHERE ASIN = ? COLLATE NOCASE', (asin,))
            row = amazon_cur.fetchone()
            
            if row:
                needs_update = False
                updates = []
                params = []
                
                # Fix barcode if missing or equals ASIN
                if row['UPC'] and row['UPC'] != asin and (not current_barcode or current_barcode == asin):
                    updates.append('barcode = ?')
                    params.append(row['UPC'])
                    fixed_barcodes += 1
                    needs_update = True
                
                # Fix image if missing
                if row['IMAGE'] and not current_image:
                    updates.append('image = ?')
                    params.append(row['IMAGE'])
                    fixed_images += 1
                    needs_update = True
                
                if needs_update:
                    params.append(order_id)
                    sold_cur.execute(f"UPDATE orders SET {', '.join(updates)} WHERE id = ?", params)
        
        sold_conn.commit()
        
        print(f"✅ Fixed {fixed_barcodes} barcodes and {fixed_images} images")
        
        return jsonify({
            'success': True,
            'message': f'Fixed {fixed_barcodes} barcodes and {fixed_images} images for Amazon orders',
            'fixed_barcodes': fixed_barcodes,
            'fixed_images': fixed_images
        })
        
    except Exception as e:
        import traceback
        error_details = traceback.format_exc()
        print(f"❌ Fix Amazon barcodes error: {e}")
        print(error_details)
        return jsonify({'success': False, 'message': _safe_error(e, 'fix amazon barcodes')}), 500
    finally:
        sold_conn.close()
        amazon_conn.close()

@app.route('/api/sync/debug', methods=['GET'])
@require_debug_mode
def sync_debug():
      return jsonify({
        'amazon_available': AMAZON_AVAILABLE,
        'bol_available': BOL_AVAILABLE if 'BOL_AVAILABLE' in globals() else False,
        'python_version': sys.version,
        'working_directory': os.getcwd()
    })

# ============================================================================
# MARKETPLACE SALES ENDPOINTS
# ============================================================================

@app.route('/marketplace-sale')
def marketplace_sale():
    """Marketplace sale entry page."""
    return render_template('marketplace_sale.html')

@app.route('/marketplace-session')
def marketplace_session():
    """Marketplace bulk sale session page."""
    return render_template('marketplace_session.html')

@app.route('/marketplace-stats')
def marketplace_stats():
    """Marketplace sales statistics page."""
    return render_template('marketplace_stats.html')

@app.route('/api/marketplace/lookup', methods=['GET'])
def api_marketplace_lookup():
    """Lookup item details from rawbol.db by barcode."""
    try:
        barcode = request.args.get('barcode', '').strip()
        if not barcode:
            return jsonify({'success': False, 'error': 'Missing barcode'}), 400
        
        # Try to find in rawbol.db
        rawbol_conn = sqlite3.connect('rawbol.db')
        rawbol_conn.row_factory = sqlite3.Row
        rawbol_cur = rawbol_conn.cursor()
        
        rawbol_cur.execute('SELECT item_description FROM raw_bol_items WHERE upc = ? COLLATE NOCASE LIMIT 1', (barcode,))
        row = rawbol_cur.fetchone()
        
        if row:
            return jsonify({'success': True, 'found': True, 'title': row['item_description']})
        else:
            return jsonify({'success': True, 'found': False, 'title': ''})
    except Exception as e:
        return jsonify({'success': False, 'error': _safe_error(e)}), 500
    finally:
        rawbol_conn.close()

@app.route('/api/marketplace/sale', methods=['POST'])
def api_marketplace_sale():
    """Create a new marketplace sale and add to sold.db."""
    try:
        from marketplace_manager import add_marketplace_sale
        
        data = request.get_json() or {}
        barcode = data.get('barcode', '').strip()
        title = data.get('title', '').strip()
        quantity = int(data.get('quantity', 1))
        price = float(data.get('price', 0))
        
        if not barcode:
            return jsonify({'success': False, 'error': 'Missing barcode'}), 400
        
        # Add to marketplace.db
        result = add_marketplace_sale(barcode, title, quantity, price)
        if not result['success']:
            return jsonify(result), 500
        
        # Add to sold.db (orders table) with store="marketplace"
        try:
            sold_conn = sqlite3.connect('sold.db')
            sold_cur = sold_conn.cursor()
            
            sale_date = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            
            # Insert ONE row with the quantity value
            # Marketplace orders are immediately handled (isHandled='1') and don't need inventory removal
            sold_cur.execute('''INSERT INTO orders 
                (barcode, title, price, paid_time, store, quantity, isHandled, isHandledDate, rackupdated)
                VALUES (?, ?, ?, ?, ?, ?, '1', ?, 1)''',
                (barcode, title, price, sale_date, 'marketplace', quantity, sale_date))
            
            # Auto-detect if this is a resold return
            if barcode:
                sold_cur.execute('''
                    SELECT id, relisted_store, relisted_item_id 
                    FROM returns 
                    WHERE barcode = ? 
                    AND relisted = 1 
                    AND resold = 0
                    ORDER BY relisted_date DESC
                    LIMIT 1
                ''', (barcode,))
                return_row = sold_cur.fetchone()
                
                if return_row:
                    return_id = return_row[0]
                    
                    # Mark as resold
                    sold_cur.execute('''
                        UPDATE returns 
                        SET resold = 1, resold_date = ?, resold_order_id = ?,
                            lifecycle_count = lifecycle_count + 1
                        WHERE id = ?
                    ''', (sale_date, f'marketplace-{result["id"]}', return_id))
                    
                    # Add auto-detected lifecycle event
                    sold_cur.execute('''
                        INSERT INTO return_lifecycle_events 
                        (return_id, event_type, event_date, auto_detected, order_id, store, notes)
                        VALUES (?, 'resold', ?, 1, ?, ?, ?)
                    ''', (return_id, sale_date, f'marketplace-{result["id"]}', 'marketplace', 'Auto-detected from marketplace sale'))
                    
                    print(f'[AUTO-DETECT] Return #{return_id} marked as resold (Marketplace sale #{result["id"]})')
            
            sold_conn.commit()
        except Exception as e:
            print(f'Warning: Failed to add to sold.db: {e}')
        finally:
            sold_conn.close()
        
        # Check if item exists in searchRack.db and needs decrementing
        try:
            rack_conn = sqlite3.connect('searchRack.db')
            rack_conn.row_factory = sqlite3.Row
            rack_cur = rack_conn.cursor()
            
            rack_cur.execute('SELECT area_code, quantity FROM rack WHERE barcode = ? COLLATE NOCASE', (barcode,))
            locations = rack_cur.fetchall()
            
            if len(locations) > 1:
                # Multiple locations - return them for user to choose
                return jsonify({
                    'success': True,
                    'sale_id': result['id'],
                    'needs_location_choice': True,
                    'locations': [{'area_code': loc['area_code'], 'quantity': loc['quantity']} for loc in locations]
                })
            elif len(locations) == 1:
                # Single location - auto-decrement
                area_code = locations[0]['area_code']
                current_qty = locations[0]['quantity']
                new_qty = max(0, current_qty - quantity)
                
                rack_cur.execute('UPDATE rack SET quantity = ? WHERE barcode = ? COLLATE NOCASE AND area_code = ?',
                               (new_qty, barcode, area_code))
                rack_conn.commit()
                
                return jsonify({
                    'success': True,
                    'sale_id': result['id'],
                    'decremented': True,
                    'area_code': area_code,
                    'new_quantity': new_qty
                })
            else:
                # Not in rack
                return jsonify({'success': True, 'sale_id': result['id'], 'decremented': False})
        except Exception as e:
            print(f'Warning: Failed to check/decrement searchRack.db: {e}')
            return jsonify({'success': True, 'sale_id': result['id'], 'decremented': False})
        finally:
            rack_conn.close()
        
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': _safe_error(e)}), 500

@app.route('/api/marketplace/decrement', methods=['POST'])
def api_marketplace_decrement():
    """Decrement inventory in searchRack.db for a specific location."""
    try:
        data = request.get_json() or {}
        barcode = data.get('barcode', '').strip()
        area_code = data.get('area_code', '').strip()
        quantity = int(data.get('quantity', 1))
        
        if not barcode or not area_code:
            return jsonify({'success': False, 'error': 'Missing barcode or area_code'}), 400
        
        rack_conn = sqlite3.connect('searchRack.db')
        rack_cur = rack_conn.cursor()
        
        rack_cur.execute('SELECT quantity FROM rack WHERE barcode = ? COLLATE NOCASE AND area_code = ?',
                        (barcode, area_code))
        row = rack_cur.fetchone()
        
        if not row:
            return jsonify({'success': False, 'error': 'Item not found in this location'}), 404
        
        current_qty = row[0]
        new_qty = max(0, current_qty - quantity)
        
        rack_cur.execute('UPDATE rack SET quantity = ? WHERE barcode = ? COLLATE NOCASE AND area_code = ?',
                        (new_qty, barcode, area_code))
        rack_conn.commit()
        
        return jsonify({'success': True, 'new_quantity': new_qty})
    except Exception as e:
        return jsonify({'success': False, 'error': _safe_error(e)}), 500
    finally:
        rack_conn.close()

@app.route('/api/marketplace/sales', methods=['GET'])
def api_marketplace_sales():
    """Get all marketplace sales."""
    try:
        from marketplace_manager import get_marketplace_sales
        
        limit = request.args.get('limit', type=int)
        offset = request.args.get('offset', type=int)
        
        result = get_marketplace_sales(limit=limit, offset=offset)
        return jsonify(result)
    except Exception as e:
        return jsonify({'success': False, 'error': _safe_error(e)}), 500

@app.route('/api/marketplace/sale/<int:sale_id>', methods=['DELETE'])
def api_marketplace_sale_delete(sale_id):
    """Delete a marketplace sale."""
    try:
        from marketplace_manager import delete_marketplace_sale
        
        result = delete_marketplace_sale(sale_id)
        return jsonify(result)
    except Exception as e:
        return jsonify({'success': False, 'error': _safe_error(e)}), 500

@app.route('/api/marketplace/sale/<int:sale_id>', methods=['PUT'])
def api_marketplace_sale_update(sale_id):
    """Update a marketplace sale."""
    try:
        from marketplace_manager import update_marketplace_sale
        
        data = request.get_json() or {}
        result = update_marketplace_sale(
            sale_id,
            barcode=data.get('barcode'),
            title=data.get('title'),
            quantity=data.get('quantity'),
            price=data.get('price')
        )
        return jsonify(result)
    except Exception as e:
        return jsonify({'success': False, 'error': _safe_error(e)}), 500

# ============================================================================
# RETURNS MANAGEMENT
# ============================================================================
# PAYOUTS ROUTES
# ============================================================================
@app.route('/payouts')
def payouts_page():
    """Payouts/settlements management page."""
    return render_template('payouts.html')

@app.route('/api/payouts', methods=['GET'])
def api_get_payouts():
    """Get all payouts with optional filters."""
    try:
        store = request.args.get('store', '').strip()
        status = request.args.get('status', '').strip()
        start_date = request.args.get('start_date', '').strip()
        end_date = request.args.get('end_date', '').strip()
        
        conn = sqlite3.connect('sold.db')
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        
        # Build query with filters - exclude $0.00 payouts
        query = 'SELECT * FROM payouts WHERE amount > 0'
        params = []
        
        if store:
            query += ' AND store = ?'
            params.append(store)
        
        if status:
            query += ' AND LOWER(status) = LOWER(?)'
            params.append(status)
        
        if start_date:
            query += ' AND start_date >= ?'
            params.append(start_date)
        
        if end_date:
            query += ' AND end_date <= ?'
            params.append(end_date)
        
        query += ' ORDER BY start_date DESC'
        
        cur.execute(query, params)
        rows = cur.fetchall()
        
        payouts = []
        total_amount = 0
        
        for row in rows:
            payout = {
                'id': row['id'],
                'store': row['store'],
                'settlement_id': row['settlement_id'],
                'start_date': row['start_date'],
                'end_date': row['end_date'],
                'payout_date': row['payout_date'],
                'amount': row['amount'],
                'currency': row['currency'],
                'status': row['status'],
                'transaction_count': row['transaction_count'],
                'synced_at': row['synced_at']
            }
            payouts.append(payout)
            
            # Sum amounts (convert to USD if needed)
            if row['currency'] == 'USD':
                total_amount += row['amount']
        
        # Calculate stats
        stats = {
            'total_payouts': len(payouts),
            'total_amount': total_amount,
            'open_count': len([p for p in payouts if p['status'] == 'Open']),
            'closed_count': len([p for p in payouts if p['status'] == 'Closed'])
        }
        
        
        return jsonify({
            'success': True,
            'payouts': payouts,
            'stats': stats
        })
        
    except Exception as e:
        return jsonify({'success': False, 'error': _safe_error(e)}), 500
    finally:
        conn.close()

@app.route('/api/payouts/sync', methods=['POST'])
def api_sync_payouts():
    """Manually trigger payout sync for Amazon and/or eBay."""
    try:
        # Accept both JSON and form POSTs
        if request.is_json:
            data = request.get_json() or {}
        else:
            data = request.form.to_dict() if request.form else {}
        store = data.get('store', 'all').lower()  # 'amazon', 'ebay', or 'all'

        results = {'amazon': 0, 'ebay': 0}
        errors = []

        # Sync Amazon payouts
        if store in ['amazon', 'all']:
            if AMAZON_AVAILABLE:
                try:
                    amazon = AmazonManager()
                    results['amazon'] = amazon.sync_settlements_to_db(days_back=90)
                except Exception as e:
                    errors.append(f"Amazon: {str(e)}")
            else:
                errors.append("Amazon integration not available")

        # Sync eBay payouts
        if store in ['ebay', 'all']:
            try:
                from ebay_manager import EbayManager
                ebay = EbayManager()
                results['ebay'] = ebay.sync_payouts_to_db(days_back=90)
            except Exception as e:
                errors.append(f"eBay: {str(e)}")

        total_synced = results['amazon'] + results['ebay']

        message = f"Synced {results['amazon']} Amazon + {results['ebay']} eBay payouts"
        if errors:
            message += f" (Errors: {'; '.join(errors)})"

        return jsonify({
            'success': True,
            'message': message,
            'synced_count': total_synced,
            'details': results,
            'errors': errors
        })

    except Exception as e:
        return jsonify({'success': False, 'error': _safe_error(e)}), 500

# ============================================================================
# RETURNS ROUTES
# ============================================================================
@app.route('/returns')
def returns_page():
    """Returns management page."""
    return render_template('returns.html')

@app.route('/api/returns', methods=['GET'])
def api_get_returns():
    """Get all returns with optional filters and lifecycle info."""
    try:
        store = request.args.get('store', '').strip()
        status = request.args.get('status', '').strip()
        
        conn = sqlite3.connect('sold.db')
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        
        # Build query with filters - include lifecycle columns
        query = '''
            SELECT r.*, 
                   COUNT(e.id) as event_count
            FROM returns r
            LEFT JOIN return_lifecycle_events e ON e.return_id = r.id
            WHERE 1=1
        '''
        params = []
        
        if store:
            query += ' AND r.store = ?'
            params.append(store)
        
        if status == 'pending':
            query += ' AND r.received_date IS NULL'
        elif status == 'received':
            query += ' AND r.received_date IS NOT NULL AND r.restocked = 0'
        elif status == 'restocked':
            query += ' AND r.restocked = 1'
        elif status == 'relisted':
            query += ' AND r.relisted = 1 AND r.resold = 0'
        elif status == 'resold':
            query += ' AND r.resold = 1'
        
        query += ' GROUP BY r.id ORDER BY r.return_date DESC'
        
        cur.execute(query, params)
        rows = cur.fetchall()
        
        returns = []
        for row in rows:
            returns.append({
                'id': row['id'],
                'original_order_id': row['original_order_id'],
                'order_id': row['order_id'],
                'item_id': row['item_id'],
                'barcode': row['barcode'],
                'title': row['title'],
                'quantity': row['quantity'],
                'original_price': row['original_price'] or 0,
                'refund_amount': row['refund_amount'] or 0,
                'original_shipping_cost': row['original_shipping_cost'] or 0,
                'return_shipping_cost': row['return_shipping_cost'] or 0,
                'original_seller_fee': row['original_seller_fee'] or 0,
                'seller_fee_refund': row['seller_fee_refund'] or 0,
                'return_date': row['return_date'],
                'received_date': row['received_date'],
                'store': row['store'],
                'return_reason': row['return_reason'],
                'condition_received': row['condition_received'],
                'restocked': row['restocked'] == 1,
                'lot_number': row['lot_number'],
                'location': row['location']
            })
        
        # Calculate stats
        total_returns = len(returns)
        total_refunded = sum(r['refund_amount'] for r in returns)
        total_cost = sum(
            r['refund_amount'] + r['original_shipping_cost'] + r['return_shipping_cost']
            for r in returns
        )
        
        # Get total orders for return rate
        cur.execute('SELECT COUNT(*) FROM orders')
        total_orders = cur.fetchone()[0]
        return_rate = (total_returns / total_orders * 100) if total_orders > 0 else 0
        
        
        return jsonify({
            'success': True,
            'returns': returns,
            'stats': {
                'total_returns': total_returns,
                'total_refunded': total_refunded,
                'total_cost': total_cost,
                'return_rate': return_rate
            }
        })
        
    except Exception as e:
        return jsonify({'success': False, 'error': _safe_error(e)}), 500
    finally:
        conn.close()

@app.route('/api/amazon/sync-returns', methods=['POST'])
def api_sync_amazon_returns():
    """Sync returns from Amazon API."""
    try:
        from amazon_manager import AmazonManager
        
        am = AmazonManager()
        synced_count = am.sync_returns_to_db(days_back=90)
        
        return jsonify({
            'success': True,
            'synced_count': synced_count,
            'message': f'Successfully synced {synced_count} returns from Amazon'
        })
        
    except Exception as e:
        return jsonify({'success': False, 'error': _safe_error(e)}), 500

@app.route('/api/ebay/sync-returns', methods=['POST'])
def api_sync_ebay_returns():
    """Sync returns from eBay API."""
    try:
        from ebay_manager import EbayManager
        
        em = EbayManager()
        synced_count = em.sync_returns_to_db(days_back=90)
        
        return jsonify({
            'success': True,
            'synced_count': synced_count,
            'message': f'Successfully synced {synced_count} returns from eBay'
        })
        
    except Exception as e:
        return jsonify({'success': False, 'error': _safe_error(e)}), 500

@app.route('/api/returns/<int:return_id>/mark-received', methods=['POST'])
def api_mark_return_received(return_id):
    """Mark a return as received."""
    try:
        conn = sqlite3.connect('sold.db')
        cur = conn.cursor()
        
        cur.execute('''
            UPDATE returns
            SET received_date = CURRENT_TIMESTAMP
            WHERE id = ?
        ''', (return_id,))
        
        # Create lifecycle event
        cur.execute('''
            INSERT INTO return_lifecycle_events 
            (return_id, event_type, event_date, auto_detected)
            VALUES (?, 'received', CURRENT_TIMESTAMP, 0)
        ''', (return_id,))
        
        conn.commit()
        
        return jsonify({
            'success': True,
            'message': 'Return marked as received'
        })
        
    except Exception as e:
        return jsonify({'success': False, 'error': _safe_error(e)}), 500
    finally:
        conn.close()

@app.route('/api/returns/<int:return_id>/restock', methods=['POST'])
def api_restock_return(return_id):
    """Mark return as restocked and update inventory."""
    try:
        data = request.get_json() or {}
        shelf_location = data.get('location', '')
        
        conn = sqlite3.connect('sold.db')
        cur = conn.cursor()
        
        # Get return details
        cur.execute('''
            SELECT barcode, quantity, lot_number, location
            FROM returns
            WHERE id = ?
        ''', (return_id,))
        
        result = cur.fetchone()
        if not result:
            return jsonify({'success': False, 'error': 'Return not found'}), 404
        
        barcode, quantity, lot_number, existing_location = result
        
        # Use provided location or fall back to existing
        final_location = shelf_location or existing_location
        
        # Update return with restocked status and location
        cur.execute('''
            UPDATE returns
            SET restocked = 1, location = ?
            WHERE id = ?
        ''', (final_location, return_id))
        
        # Create lifecycle event with shelf location
        notes = f'Restocked to shelf: {final_location}' if final_location else 'Restocked to inventory'
        cur.execute('''
            INSERT INTO return_lifecycle_events 
            (return_id, event_type, event_date, auto_detected, notes)
            VALUES (?, 'restocked', CURRENT_TIMESTAMP, 0, ?)
        ''', (return_id, notes))
        
        conn.commit()
        
        return jsonify({
            'success': True,
            'message': 'Return marked as restocked'
        })
        
    except Exception as e:
        return jsonify({'success': False, 'error': _safe_error(e)}), 500
    finally:
        conn.close()

@app.route('/api/returns/<int:return_id>', methods=['PUT'])
def api_update_return(return_id):
    """Update return details (for editing)."""
    try:
        data = request.json
        conn = sqlite3.connect('sold.db')
        cur = conn.cursor()
        
        # Build update query based on provided fields
        update_fields = []
        values = []
        
        if 'received' in data:
            if data['received']:
                # Set received_date to current timestamp if marking as received
                update_fields.append('received_date = datetime("now")')
            else:
                # Clear received_date if unmarking as received
                update_fields.append('received_date = NULL')
        
        if 'restocked' in data:
            update_fields.append('restocked = ?')
            values.append(1 if data['restocked'] else 0)
        
        if 'location' in data:
            update_fields.append('location = ?')
            values.append(data['location'])
        
        if 'return_reason' in data:
            update_fields.append('return_reason = ?')
            values.append(data['return_reason'])
        
        if not update_fields:
            return jsonify({'success': False, 'error': 'No fields to update'}), 400
        
        values.append(return_id)
        query = f"UPDATE returns SET {', '.join(update_fields)} WHERE id = ?"
        
        cur.execute(query, values)
        conn.commit()
        
        return jsonify({'success': True})
        
    except Exception as e:
        return jsonify({'success': False, 'error': _safe_error(e)}), 500
    finally:
        conn.close()

@app.route('/api/returns/<int:return_id>', methods=['DELETE'])
def api_delete_return(return_id):
    """Delete a return record."""
    try:
        conn = sqlite3.connect('sold.db')
        cur = conn.cursor()
        
        cur.execute('DELETE FROM returns WHERE id = ?', (return_id,))
        cur.execute('DELETE FROM return_lifecycle_events WHERE return_id = ?', (return_id,))
        conn.commit()
        
        return jsonify({'success': True})
        
    except Exception as e:
        return jsonify({'success': False, 'error': _safe_error(e)}), 500
    finally:
        conn.close()

@app.route('/api/returns/<int:return_id>/relist', methods=['POST'])
def api_relist_return(return_id):
    """Mark return as relisted."""
    try:
        data = request.json
        store = data.get('store', '').strip()
        item_id = data.get('item_id', '').strip()
        notes = data.get('notes', '').strip()
        
        conn = sqlite3.connect('sold.db')
        cur = conn.cursor()
        
        import datetime
        now = datetime.datetime.now().isoformat()
        
        # Update returns table
        cur.execute('''
            UPDATE returns 
            SET relisted = 1, relisted_date = ?, relisted_store = ?, relisted_item_id = ?,
                lifecycle_count = lifecycle_count + 1
            WHERE id = ?
        ''', (now, store, item_id, return_id))
        
        # Add lifecycle event for relisted
        cur.execute('''
            INSERT INTO return_lifecycle_events 
            (return_id, event_type, event_date, auto_detected, store, item_id, notes)
            VALUES (?, 'relisted', ?, 0, ?, ?, ?)
        ''', (return_id, now, store, item_id, notes))

        # Add lifecycle event for sold action (for new lifecycle)
        cur.execute('''
            INSERT INTO return_lifecycle_events 
            (return_id, event_type, event_date, auto_detected, store, item_id, notes)
            VALUES (?, 'sold', ?, 0, ?, ?, ?)
        ''', (return_id, now, store, item_id, 'Auto-set for new lifecycle'))
        
        conn.commit()
        
        return jsonify({'success': True})
        
    except Exception as e:
        return jsonify({'success': False, 'error': _safe_error(e)}), 500
    finally:
        conn.close()

@app.route('/api/returns/<int:return_id>/resold', methods=['POST'])
def api_resold_return(return_id):
    """Mark return as resold."""
    try:
        data = request.json
        order_id = data.get('order_id', '').strip()
        notes = data.get('notes', '').strip()
        
        conn = sqlite3.connect('sold.db')
        cur = conn.cursor()
        
        import datetime
        now = datetime.datetime.now().isoformat()
        
        # Update returns table
        cur.execute('''
            UPDATE returns 
            SET resold = 1, resold_date = ?, resold_order_id = ?,
                lifecycle_count = lifecycle_count + 1
            WHERE id = ?
        ''', (now, order_id, return_id))
        
        # Add lifecycle event
        cur.execute('''
            INSERT INTO return_lifecycle_events 
            (return_id, event_type, event_date, auto_detected, order_id, notes)
            VALUES (?, 'resold', ?, 0, ?, ?)
        ''', (return_id, now, order_id, notes))
        
        conn.commit()
        
        return jsonify({'success': True})
        
    except Exception as e:
        return jsonify({'success': False, 'error': _safe_error(e)}), 500
    finally:
        conn.close()

@app.route('/api/returns/<int:return_id>/restart-lifecycle', methods=['POST'])
def api_restart_return_lifecycle(return_id):
    """Restart lifecycle - mark as received again after being resold."""
    try:
        data = request.json
        notes = data.get('notes', '').strip()
        
        conn = sqlite3.connect('sold.db')
        cur = conn.cursor()
        
        import datetime
        now = datetime.datetime.now().isoformat()
        
        # Reset lifecycle flags but keep history
        cur.execute('''
            UPDATE returns 
            SET restocked = 0, relisted = 0, resold = 0,
                relisted_date = NULL, relisted_store = NULL, relisted_item_id = NULL,
                resold_date = NULL, resold_order_id = NULL,
                lifecycle_count = lifecycle_count + 1
            WHERE id = ?
        ''', (return_id,))
        
        # Add lifecycle event
        cur.execute('''
            INSERT INTO return_lifecycle_events 
            (return_id, event_type, event_date, auto_detected, notes)
            VALUES (?, 'returned_again', ?, 0, ?)
        ''', (return_id, now, notes))
        
        conn.commit()
        
        return jsonify({'success': True})
        
    except Exception as e:
        return jsonify({'success': False, 'error': _safe_error(e)}), 500
    finally:
        conn.close()

@app.route('/api/returns/<int:return_id>/history', methods=['GET'])
def api_get_return_history(return_id):
    """Get lifecycle history for a return."""
    try:
        conn = sqlite3.connect('sold.db')
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        
        # Get return info
        cur.execute('SELECT * FROM returns WHERE id = ?', (return_id,))
        return_data = cur.fetchone()
        
        if not return_data:
            return jsonify({'success': False, 'error': 'Return not found'}), 404
        
        # Get lifecycle events
        cur.execute('''
            SELECT * FROM return_lifecycle_events 
            WHERE return_id = ? 
            ORDER BY event_date ASC
        ''', (return_id,))
        events = [dict(row) for row in cur.fetchall()]
        
        
        return jsonify({
            'success': True,
            'return': dict(return_data),
            'events': events
        })
        
    except Exception as e:
        return jsonify({'success': False, 'error': _safe_error(e)}), 500
    finally:
        conn.close()

# ============================================================================
# TEST SOLD ORDERS
# ============================================================================
@app.route('/test-sold-orders')
def test_sold_orders_page():
    """Test sold orders page for creating test orders."""
    return render_template('test_sold_orders.html')

@app.route('/api/test-sold-order', methods=['POST'])
def api_create_test_sold_order():
    """Create a test order in sold.db for testing purposes."""
    try:
        data = request.get_json() or {}
        
        # Validate required fields
        required_fields = ['barcode', 'title', 'shipping_name']
        for field in required_fields:
            if not data.get(field):
                return jsonify({'success': False, 'error': f'Missing required field: {field}'}), 400
        
        # Build order data
        barcode = _normalize_upc(data.get('barcode', '').strip())
        title = data.get('title', '').strip()
        
        # Validate numeric fields
        try:
            quantity = int(data.get('quantity', 1))
            if quantity < 1:
                quantity = 1
        except (ValueError, TypeError):
            quantity = 1
        
        try:
            price = float(data.get('price', 10.0))
            if price < 0:
                price = 0.0
        except (ValueError, TypeError):
            price = 10.0
        
        store = data.get('store', 'test').strip()
        shipping_name = data.get('shipping_name', '').strip()
        shipping_street1 = data.get('shipping_street1', '').strip()
        shipping_city = data.get('shipping_city', '').strip()
        shipping_state = data.get('shipping_state', '').strip()
        shipping_postal_code = data.get('shipping_postal_code', '').strip()
        
        # Check if shipped time should be set
        set_shipped = data.get('set_shipped', False)
        
        # Generate test order_id
        import random
        import time
        order_id = f"TEST-{int(time.time())}-{random.randint(1000, 9999)}"
        
        # Insert into sold.db
        import datetime
        paid_time = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        shipped_time = paid_time if set_shipped else None
        
        conn = sqlite3.connect('sold.db')
        cur = conn.cursor()
        
        cur.execute('''
            INSERT INTO orders (
                order_id, item_id, title, quantity, price, 
                shipping_name, shipping_street1, shipping_city, 
                shipping_state, shipping_postal_code, shipping_country,
                paid_time, shipped_time, barcode, store, isHandled, rackupdated
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '', 0)
        ''', (
            order_id, order_id, title, quantity, price,
            shipping_name, shipping_street1, shipping_city,
            shipping_state, shipping_postal_code, 'US',
            paid_time, shipped_time, barcode, store
        ))
        
        conn.commit()
        inserted_id = cur.lastrowid
        
        return jsonify({
            'success': True, 
            'order_id': order_id,
            'id': inserted_id,
            'message': 'Test order created successfully'
        })
        
    except Exception as e:
        return jsonify({'success': False, 'error': _safe_error(e)}), 500
    finally:
        conn.close()

# ============================================================================
# AUTO-SYNC BACKGROUND THREAD
# ============================================================================
def auto_sync_worker():
    """Background thread that runs auto-sync based on settings"""
    print("🔄 Auto-sync worker started")
    
    while True:
        try:
            # Check if auto-sync is enabled
            conn = sqlite3.connect('sync_settings.db')
            cur = conn.cursor()
            
            # Create table if not exists
            cur.execute('''CREATE TABLE IF NOT EXISTS sync_status (
                key TEXT PRIMARY KEY,
                value TEXT
            )''')
            
            cur.execute('SELECT value FROM sync_status WHERE key = ?', ('auto_sync_enabled',))
            row = cur.fetchone()
            auto_sync_enabled = row and row[0] == 'true'
            
            if not auto_sync_enabled:
                # Check every 60 seconds if auto-sync is enabled
                time.sleep(60)
                continue
            
            # Get sync interval (in minutes)
            cur.execute('SELECT value FROM sync_status WHERE key = ?', ('sync_interval',))
            row = cur.fetchone()
            sync_interval = int(row[0]) if row else 60
            
            # Get last sync time
            cur.execute('SELECT value FROM sync_status WHERE key = ?', ('last_auto_sync',))
            row = cur.fetchone()
            last_sync = row[0] if row else None
            
            
            # Check if it's time to sync
            from datetime import datetime, timedelta
            now = datetime.now()
            should_sync = False
            
            if not last_sync:
                should_sync = True
                print(f"🔄 Auto-sync: First sync (never synced before)")
            else:
                last_sync_dt = datetime.fromisoformat(last_sync)
                time_since_sync = (now - last_sync_dt).total_seconds() / 60  # minutes
                
                if time_since_sync >= sync_interval:
                    should_sync = True
                    print(f"🔄 Auto-sync: {time_since_sync:.1f} minutes since last sync (interval: {sync_interval})")
            
            if should_sync:
                print(f"🔄 Starting auto-sync at {now.strftime('%Y-%m-%d %H:%M:%S')}")
                
                # Sync eBay orders
                try:
                    print("  📦 Syncing eBay orders...")
                    orders()
                    update_sync_timestamp('ebay_orders')
                    print("  ✅ eBay orders synced")
                except Exception as e:
                    print(f"  ⚠️ eBay orders sync failed: {e}")
                
                # Sync eBay listings (searchRack)
                try:
                    print("  📋 Syncing eBay listings...")
                    from DBmanager import enrich_searchrack_db
                    enrich_searchrack_db(batch_size=500, do_backup=False)
                    update_sync_timestamp('ebay_listings')
                    print("  ✅ eBay listings synced")
                except Exception as e:
                    print(f"  ⚠️ eBay listings sync failed: {e}")
                
                # Sync Amazon if available
                if AMAZON_AVAILABLE:
                    # Get orders lookback setting
                    conn = sqlite3.connect('sync_settings.db')
                    try:
                        cur = conn.cursor()
                        cur.execute('SELECT value FROM sync_status WHERE key = ?', ('orders_lookback',))
                        row = cur.fetchone()
                        orders_lookback = int(row[0]) if row else 30
                    finally:
                        conn.close()
                    
                    # CHANGED ORDER: Sync listings FIRST so amazonStore.db has latest data
                    try:
                        print("  � Syncing Amazon listings...")
                        amazon = AmazonManager()
                        amazon.sync_listings_to_db()
                        update_sync_timestamp('amazon_listings')
                        print("  ✅ Amazon listings synced")
                    except Exception as e:
                        print(f"  ⚠️ Amazon listings sync failed: {e}")
                    
                    # Check if auto UPC is enabled
                    conn = sqlite3.connect('sync_settings.db')
                    try:
                        cur = conn.cursor()
                        cur.execute('SELECT value FROM sync_status WHERE key = ?', ('auto_upc_enabled',))
                        row = cur.fetchone()
                        auto_upc = row and row[0] == 'true'
                    finally:
                        conn.close()
                    
                    # MOVED UP: Fetch UPCs BEFORE orders so they're available for enrichment
                    if auto_upc:
                        try:
                            print("  � Fetching Amazon UPCs...")
                            # Call the correct function with max_items limit (reduced to 25 to avoid quota issues)
                            upcs_found = sync_missing_upcs(max_items=25)
                            update_sync_timestamp('amazon_upcs')
                            print(f"  ✅ Amazon UPCs fetched: {upcs_found} found")
                        except Exception as e:
                            print(f"  ⚠️ Amazon UPCs fetch failed: {e}")
                    
                    # NOW sync orders LAST (UPCs and listings are already in amazonStore.db)
                    try:
                        print("  📦 Syncing Amazon orders...")
                        amazon = AmazonManager()
                        amazon.sync_orders_to_db(days_back=orders_lookback)
                        
                        from DBmanager import enrich_amazon_sold_images
                        enrich_amazon_sold_images()
                        
                        # ADDED: Enrich barcodes for orders that don't have them yet
                        try:
                            sold_conn = sqlite3.connect('sold.db')
                            sold_cur = sold_conn.cursor()
                            
                            amazon_conn = sqlite3.connect('amazonStore.db')
                            amazon_conn.row_factory = sqlite3.Row
                            amazon_cur = amazon_conn.cursor()
                            
                            # Find Amazon orders without barcodes
                            sold_cur.execute('''
                                SELECT id, item_id FROM orders 
                                WHERE store = 'amazon' 
                                AND (barcode IS NULL OR barcode = '' OR barcode = item_id)
                                AND item_id IS NOT NULL
                            ''')
                            
                            orders_to_enrich = sold_cur.fetchall()
                            enriched = 0
                            
                            for order_id, asin in orders_to_enrich:
                                # Look up UPC in amazonStore.db
                                amazon_cur.execute('SELECT UPC FROM ITEMS WHERE ASIN = ? COLLATE NOCASE', (asin,))
                                row = amazon_cur.fetchone()
                                
                                if row and row['UPC'] and row['UPC'] != asin:
                                    # Update sold order with proper UPC
                                    sold_cur.execute('UPDATE orders SET barcode = ? WHERE id = ?', (row['UPC'], order_id))
                                    enriched += 1
                            
                            sold_conn.commit()
                            
                            if enriched > 0:
                                print(f"  ✅ Enriched {enriched} Amazon orders with barcodes")
                            
                        except Exception as e:
                            print(f"  ⚠️ Error enriching Amazon order barcodes: {e}")
                        finally:
                            sold_conn.close()
                            amazon_conn.close()
                        
                        from DBmanager import process_sold_orders_inventory_reduction
                        process_sold_orders_inventory_reduction()
                        update_sync_timestamp('amazon_orders')
                        print("  ✅ Amazon orders synced")
                    except Exception as e:
                        print(f"  ⚠️ Amazon orders sync failed: {e}")
                    
                    # Sync Amazon payouts/settlements
                    try:
                        print("  💵 Syncing Amazon payouts...")
                        amazon = AmazonManager()
                        payout_count = amazon.sync_settlements_to_db(days_back=90)
                        update_sync_timestamp('amazon_payouts')
                        print(f"  ✅ Amazon payouts synced: {payout_count} settlements")
                    except Exception as e:
                        print(f"  ⚠️ Amazon payouts sync failed: {e}")
                
                # Sync eBay payouts
                try:
                    print("  💵 Syncing eBay payouts...")
                    from ebay_manager import EbayManager
                    ebay = EbayManager()
                    payout_count = ebay.sync_payouts_to_db(days_back=90)
                    update_sync_timestamp('ebay_payouts')
                    print(f"  ✅ eBay payouts synced: {payout_count} payouts")
                except Exception as e:
                    print(f"  ⚠️ eBay payouts sync failed: {e}")
                
                # Update last auto-sync timestamp
                conn = sqlite3.connect('sync_settings.db')
                try:
                    cur = conn.cursor()
                    cur.execute('INSERT OR REPLACE INTO sync_status (key, value) VALUES (?, ?)', 
                               ('last_auto_sync', now.isoformat()))
                    conn.commit()
                finally:
                    conn.close()
                
                print(f"✅ Auto-sync completed at {now.strftime('%Y-%m-%d %H:%M:%S')}")
            
            # Sleep for 1 minute before checking again
            time.sleep(60)
            
        except Exception as e:
            print(f"❌ Auto-sync worker error: {e}")
            import traceback
            traceback.print_exc()
            time.sleep(60)  # Wait before retrying
        finally:
            conn.close()

def _start_auto_sync_thread():
    """Start the auto-sync background thread"""
    auto_sync_thread = threading.Thread(target=auto_sync_worker, daemon=True)
    auto_sync_thread.start()
    print("🚀 Auto-sync thread started")

def emailer_alert_worker():
    """Background worker to send email alerts"""
    import time
    while True:
        try:
            # Check every 5 minutes
            time.sleep(300)  # 5 minutes
            
            # Get emailer settings
            conn = sqlite3.connect('searchRack.db')
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()
            
            # Ensure table exists
            cur.execute('''
                CREATE TABLE IF NOT EXISTS emailer_settings (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    email TEXT,
                    alert_type TEXT DEFAULT 'test',
                    interval TEXT DEFAULT '1d',
                    last_test_sent TEXT,
                    UNIQUE(email, alert_type)
                )
            ''')
            
            # Get all active alert settings
            cur.execute('SELECT email, alert_type, interval, last_test_sent FROM emailer_settings')
            settings = cur.fetchall()
            
            if not settings:
                continue
            
            from datetime import datetime, timedelta
            now = datetime.now()
            
            for setting in settings:
                email = setting['email']
                alert_type = setting['alert_type']
                interval = setting['interval']
                last_sent = setting['last_test_sent']
                
                # Only process inventory and health alerts in this worker (skip test)
                if alert_type not in ['inventory', 'health']:
                    continue
                
                # Parse interval to minutes
                interval_minutes = 0
                if interval == '30m':
                    interval_minutes = 30
                elif interval == '10m':
                    interval_minutes = 10
                elif interval == '1h':
                    interval_minutes = 60
                elif interval == '12h':
                    interval_minutes = 720
                elif interval == '1d':
                    interval_minutes = 1440
                elif interval == '1w':
                    interval_minutes = 10080
                elif interval == '1mo':
                    interval_minutes = 43200
                else:
                    continue
                
                # Check if it's time to send
                should_send = False
                if not last_sent:
                    should_send = True
                else:
                    try:
                        last_sent_dt = datetime.fromisoformat(last_sent)
                        time_since_sent = (now - last_sent_dt).total_seconds() / 60  # minutes
                        if time_since_sent >= interval_minutes:
                            should_send = True
                    except Exception:
                        should_send = True
                
                if should_send:
                    try:
                        if alert_type == 'inventory':
                            # Collect inventory issues
                            inventory_issues = collect_inventory_mismatches()
                            
                            # Only send if there are eBay or Amazon issues (exclude duplicate locations)
                            has_email_issues = len(inventory_issues['ebay_items']) > 0 or len(inventory_issues['amazon_items']) > 0
                            if has_email_issues:
                                html_body = generate_inventory_email_html(inventory_issues)
                                plain_body = generate_inventory_email_plain(inventory_issues)
                                
                                subject = f"⚠️ Inventory Alert - {len(inventory_issues['ebay_items']) + len(inventory_issues['amazon_items'])} Items Need Attention - {now.strftime('%Y-%m-%d %H:%M')}"
                                success, error = send_email_smtp([email], subject, plain_body, html_body)
                                
                                if success:
                                    print(f"✅ Inventory alert sent to: {email}")
                                    
                                    # Update last sent time
                                    conn = sqlite3.connect('searchRack.db')
                                    try:
                                        cur = conn.cursor()
                                        cur.execute('''
                                        UPDATE emailer_settings 
                                        SET last_test_sent = ? 
                                        WHERE email = ? AND alert_type = ?
                                    ''', (now.isoformat(), email, alert_type))
                                        conn.commit()
                                    finally:
                                        conn.close()
                                else:
                                    print(f"❌ Failed to send inventory alert to {email}: {error}")
                            else:
                                print(f"ℹ️ No inventory issues - skipping alert for {email}")
                                
                                # Still update timestamp to avoid checking too frequently
                                conn = sqlite3.connect('searchRack.db')
                                try:
                                    cur = conn.cursor()
                                    cur.execute('''
                                    UPDATE emailer_settings 
                                    SET last_test_sent = ? 
                                    WHERE email = ? AND alert_type = ?
                                ''', (now.isoformat(), email, alert_type))
                                    conn.commit()
                                finally:
                                    conn.close()
                        
                        elif alert_type == 'health':
                            # Collect health stats
                            health_stats = collect_health_stats()
                            html_body = generate_health_email_html(health_stats)
                            plain_body = generate_health_email_plain(health_stats)
                            
                            warnings_count = len(health_stats.get('warnings', []))
                            subject = f"📊 App Health Report{' - ' + str(warnings_count) + ' Warnings' if warnings_count > 0 else ''} - {now.strftime('%Y-%m-%d %H:%M')}"
                            success, error = send_email_smtp([email], subject, plain_body, html_body)
                            
                            if success:
                                print(f"✅ Health alert sent to: {email}")
                                
                                # Update last sent time
                                conn = sqlite3.connect('searchRack.db')
                                try:
                                    cur = conn.cursor()
                                    cur.execute('''
                                    UPDATE emailer_settings 
                                    SET last_test_sent = ? 
                                    WHERE email = ? AND alert_type = ?
                                ''', (now.isoformat(), email, alert_type))
                                    conn.commit()
                                finally:
                                    conn.close()
                            else:
                                print(f"❌ Failed to send health alert to {email}: {error}")
                            
                    except Exception as e:
                        print(f"❌ Error sending inventory alert to {email}: {e}")
            
        except Exception as e:
            print(f"❌ Emailer alert worker error: {e}")
            import traceback
            traceback.print_exc()
        finally:
            conn.close()

def _start_emailer_alert_thread():
    """Start the emailer alert background thread"""
    emailer_thread = threading.Thread(target=emailer_alert_worker, daemon=True)
    emailer_thread.start()
    print("🚀 Emailer alert thread started")

def start_background_services():
    """Start all background services with a lock to ensure single execution"""
    # Only run on non-Windows (Unix/Pi) to avoid locking issues during dev
    if os.name != 'nt':
        try:
            import fcntl
            # Create/open lock file
            lock_file = open("background_tasks.lock", "w")
            # Try to acquire an exclusive non-blocking lock
            fcntl.lockf(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
            print("🔒 Acquired background task lock")
        except IOError:
            print("⚠️ Another worker is already running background tasks. Skipping.")
            return
        except ImportError:
            pass # fcntl not available

    print("🚀 Starting background services...")
    try:
        _start_trash_purger_thread()
    except Exception as e:
        print(f"Failed to start trash purger: {e}")
        
    try:
        _start_zero_qty_deleter_thread()
    except Exception as e:
        print(f"Failed to start zero qty deleter: {e}")
        
    try:
        _start_automatic_removal_thread()
    except Exception as e:
        print(f"Failed to start automatic removal: {e}")
        
    try:
        _start_auto_sync_thread()
    except Exception as e:
        print(f"Failed to start auto sync: {e}")
        
    try:
        _start_emailer_alert_thread()
    except Exception as e:
        print(f"Failed to start emailer: {e}")

# Start background services when imported (for Gunicorn)
# We use a small delay to allow the app to fully load
def delayed_start():
    time.sleep(5)
    start_background_services()

# Initialize database and tables
try:
    ensure_lifecycle_tables()
    ensure_shelf_groups_table()
    print("✅ Database initialization completed")
except Exception as e:
    print(f"❌ Database initialization failed: {e}")

# Start background services in a separate thread to not block import
# This ensures they run regardless of whether app is run directly or via Gunicorn
threading.Thread(target=delayed_start, daemon=True).start()

@app.route('/api/duplicate_shelf', methods=['POST'])
def api_duplicate_shelf():
    """Duplicate a shelf including its image and database entry"""
    try:
        data = request.get_json() or {}
        code = data.get('code')
        
        if not code:
            return jsonify({'success': False, 'error': 'Code is required'}), 400

        conn = sqlite3.connect('searchRack.db')
        cur = conn.cursor()
        
        # Get source shelf
        cur.execute('SELECT id, shelf_name, group_id FROM shelves WHERE shelf_name = ?', (code,))
        row = cur.fetchone()
        
        if not row:
            # Try case insensitive
            cur.execute('SELECT id, shelf_name, group_id FROM shelves WHERE shelf_name = ? COLLATE NOCASE', (code,))
            row = cur.fetchone()
            
        if not row:
            return jsonify({'success': False, 'error': 'Shelf not found'}), 404
            
        source_id, source_name, group_id = row
        
        # Determine new name
        import re
        import shutil
        
        # Check if name ends with dupeN
        match = re.search(r'dupe(\d+)$', source_name)
        if match:
            number_part = match.group(1)
            base_name_prefix = source_name[:match.start()]
            counter = int(number_part) + 1
            new_name = f"{base_name_prefix}dupe{counter}"
        else:
            base_name_prefix = source_name
            counter = 1
            new_name = f"{source_name}dupe{counter}"
            
        # Verify uniqueness loop
        while True:
            cur.execute('SELECT id FROM shelves WHERE shelf_name = ?', (new_name,))
            if not cur.fetchone():
                break
            counter += 1
            new_name = f"{base_name_prefix}dupe{counter}"
        
        # Copy file
        src_path = os.path.join('static', 'shelves', f"{source_name}.png")
        dst_path = os.path.join('static', 'shelves', f"{new_name}.png")
        
        if not os.path.exists(src_path):
             # Try to find source file case-insensitively
             shelves_dir = os.path.join('static', 'shelves')
             found = False
             if os.path.exists(shelves_dir):
                for f in os.listdir(shelves_dir):
                    if f.lower() == f"{source_name}.png".lower():
                        src_path = os.path.join(shelves_dir, f)
                        found = True
                        break
             
             if not found:
                return jsonify({'success': False, 'error': 'Source image not found'}), 404
             
        try:
            shutil.copy2(src_path, dst_path)
            # Update timestamp to now so it sorts correctly as newest
            os.utime(dst_path, None)
        except Exception as e:
            return jsonify({'success': False, 'error': _safe_error(e, 'Failed to copy image')}), 500
        
        # Insert into DB
        cur.execute('INSERT INTO shelves (shelf_name, group_id, created_at) VALUES (?, ?, CURRENT_TIMESTAMP)', 
                   (new_name, group_id))
        
        conn.commit()
        
        return jsonify({
            'success': True, 
            'new_code': new_name,
            'message': f'Shelf duplicated as {new_name}'
        })
        
    except Exception as e:
        return jsonify({'success': False, 'error': _safe_error(e)}), 500
    finally:
        conn.close()

# --- Cache Management System ---
CACHE_CONFIG_FILE = BASE_DIR / 'cache_config.json'

def load_cache_config():
    default_config = {
        "auto_reload": True,
        "reload_time": "00:00", # 24-hour format HH:MM
        "timezone": "US/Eastern" # Default timezone
    }
    if CACHE_CONFIG_FILE.exists():
        try:
            with open(CACHE_CONFIG_FILE, 'r') as f:
                return {**default_config, **json.load(f)}
        except Exception:
            pass
    return default_config

def save_cache_config(config):
    with open(CACHE_CONFIG_FILE, 'w') as f:
        json.dump(config, f, indent=2)

@app.route('/api/admin/cache/clear', methods=['POST'])
def api_admin_clear_cache():
    try:
        cache.clear()
        print("🧹 Cache cleared manually via API")
        return jsonify({'success': True, 'message': 'Cache cleared successfully'})
    except Exception as e:
        return jsonify({'success': False, 'error': _safe_error(e)}), 500

@app.route('/api/admin/cache/settings', methods=['GET', 'POST'])
def api_cache_settings():
    if request.method == 'GET':
        config = load_cache_config()
        return jsonify({
            'success': True,
            'enabled': config['auto_reload'],
            'time': config['reload_time'],
            'timezone': config.get('timezone', 'US/Eastern')
        })
    
    try:
        data = request.get_json()
        config = load_cache_config()
        
        if 'enabled' in data:
            config['auto_reload'] = bool(data['enabled'])
        if 'time' in data:
            # Validate HH:MM format
            t = data['time'].strip()
            if len(t) == 5 and t[2] == ':' and t[:2].isdigit() and t[3:].isdigit():
                config['reload_time'] = t
        if 'timezone' in data:
            config['timezone'] = data['timezone']
            
        save_cache_config(config)
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': _safe_error(e)}), 500

def cache_scheduler_loop():
    print("⏰ Cache scheduler started")
    last_run_date = None
    
    while True:
        try:
            config = load_cache_config()
            if config['auto_reload']:
                try:
                    tz = pytz.timezone(config.get('timezone', 'US/Eastern'))
                    local_now = datetime.datetime.now(tz)
                except Exception:
                    # Fallback to Eastern if timezone invalid
                    tz = pytz.timezone('US/Eastern')
                    local_now = datetime.datetime.now(tz)
                
                current_time_str = local_now.strftime("%H:%M")
                current_date_str = local_now.strftime("%Y-%m-%d")
                
                # Check if it's time to reload and we haven't run today yet
                if current_time_str == config['reload_time'] and last_run_date != current_date_str:
                    print(f"⏰ Auto-reloading cache at {current_time_str} ({config.get('timezone', 'US/Eastern')})")
                    with app.app_context():
                        cache.clear()
                    last_run_date = current_date_str
                    
            time.sleep(30) # Check every 30 seconds
        except Exception as e:
            print(f"❌ Cache scheduler error: {e}")
            time.sleep(60)

if __name__ == "__main__":
    # Start Cache Scheduler
    cache_thread = threading.Thread(target=cache_scheduler_loop, daemon=True)
    cache_thread.start()

    # Start Flask in a thread
    flask_thread = threading.Thread(target=start_flask, daemon=True)
    flask_thread.start()

    # Wait a bit, then start tunnel
    time.sleep(1)
    try:
        start_tunnel()
    except Exception as e:
        print(f"Warning: Tunnel start failed: {e}")

    # Wait until both are likely up
    time.sleep(2)
    try:
        # orders()  # Skip eBay sync for testing print queue
        pass
    except Exception as e:
        print(f"Warning: orders() failed: {e}")
    # finalize_barcodes()  # Skip for testing

    # Keep main thread alive
    while True:
        time.sleep(1)

