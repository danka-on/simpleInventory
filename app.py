# Place below app = Flask(__name__)

from contextlib import nullcontext

from flask import Flask, request, send_file, url_for, render_template, jsonify, redirect, session, make_response
from flask_caching import Cache
from flask_compress import Compress
from werkzeug.exceptions import RequestEntityTooLarge
from PIL import Image, ImageDraw
import io, time, subprocess, os, requests, json, threading, sqlite3, sys, datetime, base64, gzip
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
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
    databases = ['sold.db', 'bol.db', 'searchRack.db', 'ebayStore.db', 'amazonStore.db', 'rawbol.db', 'rackhistory.db', 'deleted.db', 'listing_alerts.db', 'fbstore.db', 'listagent.db', 'listinglog.db', 'preplog.db', 'pricemaster.db']
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
        'bol.db': [
            ('bol_items', 'upc'),
            ('bol_items', 'item_description'),
            ('bol_items', 'lot_number')
        ],
        'rawbol.db': [
            ('raw_bol_items', 'upc'),
            ('raw_bol_items', 'item_description'),
            ('raw_bol_items', 'lot_number')
        ],
        'searchRack.db': [
            ('SEARCHRACK', 'BARCODE'),
            ('SEARCHRACK', 'TITLE'),
            ('SEARCHRACK', 'item_position'),
            ('SEARCHRACK', 'CREATED_AT')
        ],
        'sold.db': [
            ('orders', 'barcode'),
            ('orders', 'order_id'),
            ('orders', 'store'),
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

def _ensure_listing_alerts_tables():
    """Create listing_alerts.db tables if they don't exist."""
    try:
        conn = sqlite3.connect('listing_alerts.db')
        cur = conn.cursor()
        cur.execute('''
            CREATE TABLE IF NOT EXISTS dismissed_alerts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                alert_type TEXT NOT NULL,
                upc TEXT NOT NULL,
                store TEXT,
                listing_ids TEXT,
                dismissed_at TEXT DEFAULT CURRENT_TIMESTAMP,
                snapshot_hash TEXT NOT NULL,
                UNIQUE(alert_type, snapshot_hash)
            )
        ''')
        cur.execute('CREATE INDEX IF NOT EXISTS idx_dismissed_hash ON dismissed_alerts(alert_type, snapshot_hash)')
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"Error initializing listing_alerts.db: {e}")

# Initialize listing alerts db on startup
_ensure_listing_alerts_tables()

def _ensure_fbstore_tables():
    """Create fbstore.db tables for Facebook Marketplace tracking."""
    try:
        conn = sqlite3.connect('fbstore.db')
        cur = conn.cursor()
        cur.execute('''
            CREATE TABLE IF NOT EXISTS fb_listings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                upc TEXT NOT NULL,
                title TEXT,
                image TEXT,
                quantity INTEGER DEFAULT 1,
                listed_at TEXT DEFAULT CURRENT_TIMESTAMP,
                unlisted_at TEXT,
                is_active INTEGER DEFAULT 1,
                notes TEXT
            )
        ''')
        cur.execute('CREATE INDEX IF NOT EXISTS idx_fb_upc ON fb_listings(upc)')
        cur.execute('CREATE INDEX IF NOT EXISTS idx_fb_active ON fb_listings(is_active)')
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"Error initializing fbstore.db: {e}")

# Initialize fbstore db on startup
_ensure_fbstore_tables()

def _ensure_fbstore_log_tables():
    """Create fbstore.db tables for FB listing sync logs."""
    try:
        conn = sqlite3.connect('fbstore.db')
        cur = conn.cursor()
        cur.execute('''
            CREATE TABLE IF NOT EXISTS fb_listing_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                action TEXT NOT NULL,
                upc TEXT,
                delta_qty INTEGER,
                prev_qty INTEGER,
                new_qty INTEGER,
                prev_listed INTEGER,
                prev_listed_date TEXT,
                order_id TEXT,
                item_id TEXT,
                store TEXT,
                title TEXT,
                note TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                undone INTEGER DEFAULT 0,
                undone_at TEXT
            )
        ''')
        cur.execute('CREATE INDEX IF NOT EXISTS idx_fb_log_created ON fb_listing_log(created_at)')
        cur.execute('''
            CREATE TABLE IF NOT EXISTS fb_listing_sold_sync (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                order_id TEXT,
                item_id TEXT,
                upc TEXT,
                qty INTEGER,
                paid_time TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(order_id, item_id, upc, qty, paid_time)
            )
        ''')
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"Error initializing fbstore log tables: {e}")

def _ensure_fbstore_notes_tables():
    """Create fbstore.db tables for FB listing notes."""
    try:
        conn = sqlite3.connect('fbstore.db')
        cur = conn.cursor()
        cur.execute('''
            CREATE TABLE IF NOT EXISTS fb_listing_notes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                upc TEXT NOT NULL,
                note TEXT NOT NULL,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT
            )
        ''')
        cur.execute('CREATE INDEX IF NOT EXISTS idx_fb_notes_upc ON fb_listing_notes(upc)')
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"Error initializing fbstore notes tables: {e}")

def _log_fb_listing_action(action, upc=None, delta_qty=None, prev_qty=None, new_qty=None,
                           prev_listed=None, prev_listed_date=None, order_id=None,
                           item_id=None, store=None, title=None, note=None):
    """Write a FB listing log entry."""
    try:
        _ensure_fbstore_log_tables()
        conn = sqlite3.connect('fbstore.db')
        cur = conn.cursor()
        cur.execute('''
            INSERT INTO fb_listing_log (
                action, upc, delta_qty, prev_qty, new_qty,
                prev_listed, prev_listed_date, order_id, item_id,
                store, title, note
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            action, upc, delta_qty, prev_qty, new_qty,
            prev_listed, prev_listed_date, order_id, item_id,
            store, title, note
        ))
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"Error logging FB listing action: {e}")

def _track_fb_listing(upc, quantity=1, listed=True, title=None, image=None):
    """Track Facebook Marketplace listing in fbstore.db"""
    try:
        _ensure_fbstore_tables()
        conn = sqlite3.connect('fbstore.db')
        cur = conn.cursor()

        if listed:
            # Get title/image from bol.db if not provided
            if not title or not image:
                bol_conn = sqlite3.connect('bol.db')
                bol_conn.row_factory = sqlite3.Row
                bol_cur = bol_conn.cursor()
                bol_cur.execute('SELECT title, image FROM bol_items WHERE upc = ? COLLATE NOCASE LIMIT 1', (upc,))
                row = bol_cur.fetchone()
                if row:
                    title = title or row['title']
                    image = image or row['image']
                bol_conn.close()

            # Insert new listing record
            cur.execute('''
                INSERT INTO fb_listings (upc, title, image, quantity, listed_at, is_active)
                VALUES (?, ?, ?, ?, datetime("now"), 1)
            ''', (upc, title, image, quantity))
            print(f"[fbstore] Added FB listing: UPC={upc}, Qty={quantity}")
        else:
            # Mark most recent active listing as unlisted
            cur.execute('''
                UPDATE fb_listings
                SET is_active = 0, unlisted_at = datetime("now")
                WHERE upc = ? COLLATE NOCASE AND is_active = 1
            ''', (upc,))
            print(f"[fbstore] Marked FB listing unlisted: UPC={upc}")

        conn.commit()
        conn.close()
    except Exception as e:
        print(f"Error tracking FB listing: {e}")

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

import re as _re
_I18N_SRC_PAT = _re.compile(r'src=(["\'])/static/i18n\.js(?:\?[^"\']*)?\1')
_I18N_CACHE_V = int(getattr(app, 'start_time', 0) or 0)

@app.after_request
def add_no_cache_headers(response):
    try:
        content_type = response.content_type or ''

        if 'text/html' in content_type:
            try:
                if (not getattr(response, 'direct_passthrough', False)) and (not getattr(response, 'is_streamed', False)):
                    html = response.get_data(as_text=True)
                    if html:
                        needs_write = False
                        v = _I18N_CACHE_V

                        # Only run regex if i18n.js is already in the HTML
                        if 'i18n.js' in html:
                            if v:
                                new_html = _I18N_SRC_PAT.sub(lambda m: f'src={m.group(1)}/static/i18n.js?v={v}{m.group(1)}', html)
                            else:
                                new_html = _I18N_SRC_PAT.sub(r'src=\1/static/i18n.js\1', html)
                            if new_html is not html:
                                html = new_html
                                needs_write = True
                        else:
                            # Inject i18n.js for legacy templates that don't include it
                            inject = f'\n<script src="/static/i18n.js?v={v}"></script>\n' if v else '\n<script src="/static/i18n.js"></script>\n'
                            if '</body>' in html:
                                html = html.replace('</body>', inject + '</body>', 1)
                            elif '</html>' in html:
                                html = html.replace('</html>', inject + '</html>', 1)
                            else:
                                html = html + inject
                            needs_write = True

                        if needs_write:
                            response.set_data(html)
            except Exception:
                pass

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

@app.route('/listingagent')
def listingagent():
    """Experimental: assisted listing page (start with eBay)."""
    return render_template('listingagent.html')

@app.route('/listingagent/mobile')
def listingagent_mobile():
    """Mobile helper: camera upload page for Listing Agent photos."""
    upc = (request.args.get('upc') or '').strip()
    if not upc:
        return "Missing upc", 400
    return render_template('listingagent_mobile.html', upc=upc)

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
        with db_connection('sync_settings.db') as conn:
            cur = conn.cursor()
            _listingagent_init_settings_table(cur)
            cur.execute('SELECT key, value FROM listing_agent_settings')
            rows = cur.fetchall()
            return {r['key']: r['value'] for r in rows}
    except Exception:
        return {}

def _listingagent_upsert_settings(settings: dict):
    now = datetime.datetime.now().isoformat()
    with db_connection('sync_settings.db') as conn:
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

# -----------------------------
# Listing Agent - Listing Queue (listagent.db)
# -----------------------------

def _listagent_init_tables(cur):
    # listagent.db might be created after startup; ensure WAL gets enabled.
    try:
        cur.execute('PRAGMA journal_mode=WAL')
    except Exception:
        pass

    cur.execute('''
        CREATE TABLE IF NOT EXISTS listing_queue (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            upc TEXT NOT NULL,
            title TEXT,
            source TEXT,
            item_status TEXT,
            status TEXT NOT NULL DEFAULT 'queued',
            added_at TEXT NOT NULL,
            listed_at TEXT,
            listed_platform TEXT,
            listed_listing_id TEXT,
            listed_offer_id TEXT,
            listed_sku TEXT,
            listed_asin TEXT,
            listed_url TEXT,
            listed_ebay_at TEXT,
            listed_amazon_at TEXT,
            removed_at TEXT
        )
    ''')
    cur.execute('CREATE INDEX IF NOT EXISTS idx_listing_queue_status_added_at ON listing_queue(status, added_at)')
    try:
        # Enforce only one active (queued) entry per UPC.
        cur.execute("CREATE UNIQUE INDEX IF NOT EXISTS uq_listing_queue_active_upc ON listing_queue(upc) WHERE status='queued'")
    except Exception:
        # Partial indexes require newer SQLite; degrade gracefully.
        pass
    try:
        # Keep done entries unique too (so queue stays clean).
        cur.execute("CREATE UNIQUE INDEX IF NOT EXISTS uq_listing_queue_done_upc ON listing_queue(upc) WHERE status='done'")
    except Exception:
        pass

    # Schema migration: add item_status for queue provenance (e.g., good/bad from list manager)
    try:
        cur.execute('PRAGMA table_info(listing_queue)')
        cols = []
        for r in cur.fetchall() or []:
            try:
                cols.append((r['name'] or '').lower())
            except Exception:
                try:
                    cols.append((r[1] or '').lower())
                except Exception:
                    pass
        if 'item_status' not in cols:
            cur.execute('ALTER TABLE listing_queue ADD COLUMN item_status TEXT')
        if 'listed_ebay_at' not in cols:
            cur.execute('ALTER TABLE listing_queue ADD COLUMN listed_ebay_at TEXT')
        if 'listed_amazon_at' not in cols:
            cur.execute('ALTER TABLE listing_queue ADD COLUMN listed_amazon_at TEXT')
        if 'removed_at' not in cols:
            cur.execute('ALTER TABLE listing_queue ADD COLUMN removed_at TEXT')
    except Exception:
        pass

    # Uploaded listing photos (mobile -> desktop)
    cur.execute('''
        CREATE TABLE IF NOT EXISTS listing_photos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            upc TEXT NOT NULL,
            image_path TEXT NOT NULL,
            original_filename TEXT,
            size_bytes INTEGER,
            created_at TEXT NOT NULL
        )
    ''')
    cur.execute('CREATE INDEX IF NOT EXISTS idx_listing_photos_upc_created_at ON listing_photos(upc, created_at, id)')

def _listagent_row_to_dict(row):
    if not row:
        return None
    try:
        return {k: row[k] for k in row.keys()}
    except Exception:
        try:
            return dict(row)
        except Exception:
            return None

def _listagent_now_iso():
    return datetime.datetime.now().isoformat()

# -----------------------------
# Listing Log (listinglog.db)
# -----------------------------

def _listinglog_init_tables(cur):
    # listinglog.db might be created after startup; ensure WAL gets enabled.
    try:
        cur.execute('PRAGMA journal_mode=WAL')
    except Exception:
        pass

    cur.execute('''
        CREATE TABLE IF NOT EXISTS listing_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL,
            upc TEXT NOT NULL,
            platform TEXT NOT NULL,
            action TEXT NOT NULL,
            source TEXT,
            marketplace_id TEXT,
            listing_id TEXT,
            offer_id TEXT,
            sku TEXT,
            asin TEXT,
            url TEXT,
            title TEXT,
            price REAL,
            quantity INTEGER,
            success INTEGER NOT NULL DEFAULT 1,
            error TEXT,
            meta_json TEXT
        )
    ''')
    cur.execute('CREATE INDEX IF NOT EXISTS idx_listing_log_upc_created_at ON listing_log(upc, created_at, id)')
    cur.execute('CREATE INDEX IF NOT EXISTS idx_listing_log_platform_created_at ON listing_log(platform, created_at, id)')

def _listinglog_add_entry(*,
                          upc,
                          platform,
                          action='listed',
                          source='listingagent',
                          created_at=None,
                          marketplace_id=None,
                          listing_id=None,
                          offer_id=None,
                          sku=None,
                          asin=None,
                          url=None,
                          title=None,
                          price=None,
                          quantity=None,
                          success=True,
                          error=None,
                          meta=None):
    upc = (upc or '').strip()
    if not upc:
        raise ValueError('upc is required')

    platform = (platform or 'unknown').strip().lower() or 'unknown'
    action = (action or 'listed').strip().lower() or 'listed'
    source = (source or '').strip() or None
    created_at = (created_at or _listagent_now_iso()).strip()

    meta_json = None
    try:
        if meta is not None:
            meta_json = json.dumps(meta, ensure_ascii=False)
    except Exception:
        meta_json = None

    with db_connection('listinglog.db') as conn:
        cur = conn.cursor()
        _listinglog_init_tables(cur)
        cur.execute('''
            INSERT INTO listing_log (
                created_at, upc, platform, action, source, marketplace_id,
                listing_id, offer_id, sku, asin, url, title, price, quantity,
                success, error, meta_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            created_at, upc, platform, action, source, (marketplace_id or None),
            (listing_id or None), (offer_id or None), (sku or None), (asin or None),
            (url or None), (title or None), price, quantity,
            1 if success else 0, (error or None), meta_json
        ))
        rid = cur.lastrowid
        cur.execute('SELECT * FROM listing_log WHERE id = ? LIMIT 1', (rid,))
        return _listagent_row_to_dict(cur.fetchone())

# -----------------------------
# Prep Log (preplog.db)
# -----------------------------

def _preplog_init_tables(cur):
    # preplog.db might be created after startup; ensure WAL gets enabled.
    try:
        cur.execute('PRAGMA journal_mode=WAL')
    except Exception:
        pass

    cur.execute('''
        CREATE TABLE IF NOT EXISTS prep_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL,
            upc TEXT NOT NULL,
            base_upc TEXT,
            status TEXT NOT NULL,
            quantity INTEGER,
            note TEXT,
            reason TEXT,
            source TEXT,
            meta_json TEXT,
            undone INTEGER NOT NULL DEFAULT 0,
            undone_at TEXT,
            undo_error TEXT
        )
    ''')
    cur.execute('CREATE INDEX IF NOT EXISTS idx_prep_log_created_at ON prep_log(created_at, id)')
    cur.execute('CREATE INDEX IF NOT EXISTS idx_prep_log_upc_created_at ON prep_log(upc, created_at, id)')
    cur.execute('CREATE INDEX IF NOT EXISTS idx_prep_log_undone_created_at ON prep_log(undone, created_at, id)')

def _preplog_add_entry(*,
                       upc,
                       status,
                       quantity=1,
                       created_at=None,
                       base_upc=None,
                       note=None,
                       reason=None,
                       source='item-prep',
                       meta=None,
                       dedupe=False):
    upc = (upc or '').strip()
    if not upc:
        raise ValueError('upc is required')

    status = (status or '').strip().lower()
    if status not in ('good', 'bad', 'unchecked', 'return'):
        raise ValueError('status must be good/bad/unchecked/return')

    created_at = (created_at or _listagent_now_iso()).strip()
    base_upc = (base_upc or '').strip() or None
    note = (note or '').strip() or None
    reason = (reason or '').strip() or None
    source = (source or '').strip() or None

    meta_json = None
    try:
        if meta is not None:
            meta_json = json.dumps(meta, ensure_ascii=False)
    except Exception:
        meta_json = None

    try:
        quantity = int(quantity) if quantity is not None else None
    except Exception:
        quantity = None

    with db_connection('preplog.db') as conn:
        cur = conn.cursor()
        _preplog_init_tables(cur)

        if dedupe:
            cur.execute('''
                SELECT id
                FROM prep_log
                WHERE upc = ? COLLATE NOCASE
                  AND status = ?
                  AND undone = 0
                ORDER BY id DESC
                LIMIT 1
            ''', (upc, status))
            existing = cur.fetchone()
            if existing and (existing[0] is not None):
                rid = int(existing[0])
                cur.execute('''
                    UPDATE prep_log
                    SET created_at=?,
                        base_upc=COALESCE(?, base_upc),
                        quantity=COALESCE(?, quantity),
                        note=COALESCE(?, note),
                        reason=COALESCE(?, reason),
                        source=COALESCE(?, source),
                        meta_json=COALESCE(?, meta_json),
                        undo_error=NULL
                    WHERE id = ?
                ''', (created_at, base_upc, quantity, note, reason, source, meta_json, rid))
                cur.execute('SELECT * FROM prep_log WHERE id = ? LIMIT 1', (rid,))
                return _listagent_row_to_dict(cur.fetchone())

        cur.execute('''
            INSERT INTO prep_log (
                created_at, upc, base_upc, status, quantity, note, reason, source, meta_json, undone
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0)
        ''', (created_at, upc, base_upc, status, quantity, note, reason, source, meta_json))
        rid = cur.lastrowid
        cur.execute('SELECT * FROM prep_log WHERE id = ? LIMIT 1', (rid,))
        return _listagent_row_to_dict(cur.fetchone())

def _preplog_recent(*, limit=200, include_undone=True):
    limit = int(limit or 200)
    limit = max(1, min(limit, 2000))
    with db_connection('preplog.db') as conn:
        cur = conn.cursor()
        _preplog_init_tables(cur)

        sql = 'SELECT * FROM prep_log WHERE 1=1'
        params = []
        if not include_undone:
            sql += ' AND undone = 0'
        sql += ' ORDER BY created_at DESC, id DESC LIMIT ?'
        params.append(limit)

        cur.execute(sql, params)
        return [_listagent_row_to_dict(r) for r in cur.fetchall()]

def _preplog_mark_undone(log_id: int, *, undone_at=None, undo_error=None):
    log_id = int(log_id)
    undone_at = (undone_at or _listagent_now_iso()).strip()
    undo_error = (undo_error or '').strip() or None

    with db_connection('preplog.db') as conn:
        cur = conn.cursor()
        _preplog_init_tables(cur)
        cur.execute('''
            UPDATE prep_log
            SET undone = 1,
                undone_at = ?,
                undo_error = ?
            WHERE id = ?
        ''', (undone_at, undo_error, log_id))
        cur.execute('SELECT * FROM prep_log WHERE id = ? LIMIT 1', (log_id,))
        return _listagent_row_to_dict(cur.fetchone())

def _preplog_set_undo_error(log_id: int, error: str):
    log_id = int(log_id)
    error = (error or '').strip() or None
    with db_connection('preplog.db') as conn:
        cur = conn.cursor()
        _preplog_init_tables(cur)
        cur.execute('UPDATE prep_log SET undo_error = ? WHERE id = ?', (error, log_id))
        cur.execute('SELECT * FROM prep_log WHERE id = ? LIMIT 1', (log_id,))
        return _listagent_row_to_dict(cur.fetchone())

def _listagent_add_to_queue(upc, *, title=None, source=None, item_status=None):
    upc = (upc or '').strip()
    if not upc:
        raise ValueError('upc is required')

    now = _listagent_now_iso()
    title = (title or '').strip() or None
    source = (source or '').strip() or None
    item_status = (item_status or '').strip().lower() or None

    with db_connection('listagent.db') as conn:
        cur = conn.cursor()
        _listagent_init_tables(cur)

        # If it already exists in the active list (queued or done), keep it and just fill metadata.
        cur.execute('''
            SELECT *
            FROM listing_queue
            WHERE upc = ? AND status IN ('queued', 'done')
            ORDER BY added_at DESC, id DESC
            LIMIT 1
        ''', (upc,))
        row = cur.fetchone()
        if row:
            qid = row['id']
            if title or source or item_status:
                cur.execute('''
                    UPDATE listing_queue
                    SET
                        title = COALESCE(NULLIF(title, ''), ?),
                        source = COALESCE(NULLIF(source, ''), ?),
                        item_status = COALESCE(?, item_status)
                    WHERE id = ?
                ''', (title, source, item_status, qid))
            cur.execute('SELECT * FROM listing_queue WHERE id = ? LIMIT 1', (qid,))
            return _listagent_row_to_dict(cur.fetchone()), False

        # If it was removed before, revive it (and clear any previous "listed" flags).
        cur.execute('''
            SELECT id
            FROM listing_queue
            WHERE upc = ? AND status = 'removed'
            ORDER BY removed_at DESC, id DESC
            LIMIT 1
        ''', (upc,))
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
            ''', (now, title, source, item_status, qid))
            cur.execute('SELECT * FROM listing_queue WHERE id = ? LIMIT 1', (qid,))
            return _listagent_row_to_dict(cur.fetchone()), True

        # Fresh insert
        cur.execute('''
            INSERT INTO listing_queue (upc, title, source, item_status, status, added_at)
            VALUES (?, ?, ?, ?, 'queued', ?)
        ''', (upc, title, source, item_status, now))
        qid = cur.lastrowid
        cur.execute('SELECT * FROM listing_queue WHERE id = ? LIMIT 1', (qid,))
        return _listagent_row_to_dict(cur.fetchone()), True

def _listagent_get_queue(*, limit=50):
    limit = int(limit or 50)
    limit = max(1, min(limit, 200))
    with db_connection('listagent.db') as conn:
        cur = conn.cursor()
        _listagent_init_tables(cur)
        cur.execute('''
            SELECT *
            FROM listing_queue
            WHERE status IN ('queued', 'done')
            ORDER BY CASE WHEN status = 'done' THEN 1 ELSE 0 END, added_at DESC, id DESC
            LIMIT ?
        ''', (limit,))
        return [_listagent_row_to_dict(r) for r in cur.fetchall()]

def _listagent_mark_listed(upc, *, platform=None, listing_id=None, offer_id=None, sku=None, asin=None, url=None,
                           marketplace_id=None, title=None, price=None, quantity=None,
                           source='listingagent', action='listed', success=True, error=None, meta=None):
    upc = (upc or '').strip()
    if not upc:
        raise ValueError('upc is required')

    now = _listagent_now_iso()
    platform = (platform or '').strip().lower() or None
    updated_item = None

    with db_connection('listagent.db') as conn:
        cur = conn.cursor()
        _listagent_init_tables(cur)

        cur.execute('''
            SELECT *
            FROM listing_queue
            WHERE upc = ? AND status IN ('queued', 'done')
            ORDER BY added_at DESC, id DESC
            LIMIT 1
        ''', (upc,))
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
            updated_item = _listagent_row_to_dict(cur.fetchone())

    # Best-effort persistent listing log (separate DB; never blocks listing flow)
    try:
        _listinglog_add_entry(
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
    upc = (upc or '').strip()
    if not upc:
        raise ValueError('upc is required')
    now = _listagent_now_iso()
    with db_connection('listagent.db') as conn:
        cur = conn.cursor()
        _listagent_init_tables(cur)
        cur.execute('''
            UPDATE listing_queue
            SET status = 'removed', removed_at = ?
            WHERE upc = ? AND status IN ('queued', 'done')
        ''', (now, upc))
        changed = bool(cur.rowcount and cur.rowcount > 0)
        cur.execute('''
            SELECT *
            FROM listing_queue
            WHERE upc = ?
            ORDER BY removed_at DESC, id DESC
            LIMIT 1
        ''', (upc,))
        return _listagent_row_to_dict(cur.fetchone()), changed

def _listagent_add_photo(upc, *, image_path, original_filename=None, size_bytes=None):
    upc = (upc or '').strip()
    if not upc:
        raise ValueError('upc is required')
    image_path = (image_path or '').strip()
    if not image_path:
        raise ValueError('image_path is required')

    now = _listagent_now_iso()
    with db_connection('listagent.db') as conn:
        cur = conn.cursor()
        _listagent_init_tables(cur)
        cur.execute('''
            INSERT INTO listing_photos (upc, image_path, original_filename, size_bytes, created_at)
            VALUES (?, ?, ?, ?, ?)
        ''', (upc, image_path, (original_filename or None), size_bytes, now))
        pid = cur.lastrowid
        cur.execute('SELECT * FROM listing_photos WHERE id = ? LIMIT 1', (pid,))
        return _listagent_row_to_dict(cur.fetchone())

def _listagent_get_photos(upc, *, limit=30):
    upc = (upc or '').strip()
    if not upc:
        raise ValueError('upc is required')
    limit = int(limit or 30)
    limit = max(1, min(limit, 200))
    with db_connection('listagent.db') as conn:
        cur = conn.cursor()
        _listagent_init_tables(cur)
        cur.execute('''
            SELECT *
            FROM listing_photos
            WHERE upc = ? COLLATE NOCASE
            ORDER BY created_at DESC, id DESC
            LIMIT ?
        ''', (upc, limit))
        return [_listagent_row_to_dict(r) for r in cur.fetchall()]

def _listagent_search_live_listings(q, *, limit=60):
    q = (q or '').strip()
    limit = int(limit or 60)
    limit = max(1, min(limit, 200))
    like = f"%{q}%"

    results = []

    # eBay live listings (from ebayStore.db)
    try:
        with db_connection('ebayStore.db') as conn:
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
        with db_connection('amazonStore.db') as conn:
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

@app.route('/api/listingagent/settings', methods=['GET'])
def api_listingagent_get_settings():
    try:
        return jsonify({'success': True, 'settings': _listingagent_get_settings()})
    except Exception as e:
        return jsonify({'success': False, 'error': _safe_error(e, 'listingagent:get_settings')}), 500

@app.route('/api/listingagent/settings', methods=['POST'])
def api_listingagent_save_settings():
    try:
        payload = request.json or {}
        settings = payload.get('settings', payload)
        if not isinstance(settings, dict):
            return jsonify({'success': False, 'error': 'settings must be an object'}), 400
        _listingagent_upsert_settings(settings)
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': _safe_error(e, 'listingagent:save_settings')}), 500

@app.route('/api/listingagent/queue', methods=['GET'])
def api_listingagent_queue():
    """Get the Listing Agent queue (queued + done; excludes removed)."""
    try:
        limit = _listingagent_parse_int(request.args.get('limit'), 60) or 60
        items = _listagent_get_queue(limit=limit)
        return jsonify({'success': True, 'items': items})
    except Exception as e:
        return jsonify({'success': False, 'error': _safe_error(e, 'listingagent:queue_get')}), 500

@app.route('/api/listingagent/queue/add', methods=['POST'])
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

        item, added = _listagent_add_to_queue(upc, title=title, source=source, item_status=item_status)
        return jsonify({'success': True, 'added': added, 'item': item})
    except Exception as e:
        return jsonify({'success': False, 'error': _safe_error(e, 'listingagent:queue_add')}), 500

@app.route('/api/listingagent/queue/mark_listed', methods=['POST'])
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

        item = _listagent_mark_listed(
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
        return jsonify({'success': False, 'error': _safe_error(e, 'listingagent:queue_mark_listed')}), 500

@app.route('/api/listingagent/queue/remove', methods=['POST'])
def api_listingagent_queue_remove():
    """Remove a UPC from the Listing Agent queue (soft remove; keeps history)."""
    try:
        data = request.json or {}
        upc = (data.get('upc') or '').strip()
        if not upc:
            return jsonify({'success': False, 'error': 'upc is required'}), 400
        item, removed = _listagent_remove_from_queue(upc)
        return jsonify({'success': True, 'removed': removed, 'item': item})
    except Exception as e:
        return jsonify({'success': False, 'error': _safe_error(e, 'listingagent:queue_remove')}), 500

@app.route('/api/listinglog/recent', methods=['GET'])
def api_listinglog_recent():
    """Recent listing events (listinglog.db)."""
    try:
        upc = (request.args.get('upc') or '').strip()
        platform = (request.args.get('platform') or '').strip().lower()
        limit = _listingagent_parse_int(request.args.get('limit'), 200) or 200
        limit = max(1, min(limit, 1000))

        with db_connection('listinglog.db') as conn:
            cur = conn.cursor()
            _listinglog_init_tables(cur)

            sql = 'SELECT * FROM listing_log WHERE 1=1'
            params = []
            if upc:
                sql += ' AND upc = ? COLLATE NOCASE'
                params.append(upc)
            if platform:
                sql += ' AND platform = ?'
                params.append(platform)
            sql += ' ORDER BY created_at DESC, id DESC LIMIT ?'
            params.append(limit)

            cur.execute(sql, params)
            rows = [_listagent_row_to_dict(r) for r in cur.fetchall()]

        return jsonify({'success': True, 'items': rows})
    except Exception as e:
        return jsonify({'success': False, 'error': _safe_error(e, 'listinglog:recent')}), 500

@app.route('/api/preplog/recent', methods=['GET'])
def api_preplog_recent():
    """Recent item-prep events (preplog.db)."""
    try:
        limit = _listingagent_parse_int(request.args.get('limit'), 200) or 200
        limit = max(1, min(limit, 2000))
        include_undone = (request.args.get('include_undone') or '1').strip().lower() in ('1', 'true', 'yes', 'y')
        items = _preplog_recent(limit=limit, include_undone=include_undone)
        return jsonify({'success': True, 'items': items})
    except Exception as e:
        return jsonify({'success': False, 'error': _safe_error(e, 'preplog:recent')}), 500

@app.route('/api/preplog/undo', methods=['POST'])
def api_preplog_undo():
    """Undo a prep log entry (and mark it undone in preplog.db). JSON: { id }"""
    try:
        data = request.get_json() or {}
        log_id = data.get('id')
        if log_id is None:
            return jsonify({'success': False, 'error': 'Missing id'}), 400
        try:
            log_id = int(log_id)
        except Exception:
            return jsonify({'success': False, 'error': 'Invalid id'}), 400

        # Fetch entry
        with db_connection('preplog.db') as conn:
            cur = conn.cursor()
            _preplog_init_tables(cur)
            cur.execute('SELECT * FROM prep_log WHERE id = ? LIMIT 1', (log_id,))
            row = cur.fetchone()
            entry = _listagent_row_to_dict(row)

        if not entry:
            return jsonify({'success': False, 'error': 'Log entry not found'}), 404
        if entry.get('undone'):
            return jsonify({'success': False, 'error': 'Already undone'}), 400

        upc = (entry.get('upc') or '').strip()
        status = (entry.get('status') or '').strip().lower()
        qty = entry.get('quantity') if entry.get('quantity') is not None else 1
        base_upc = (entry.get('base_upc') or '').strip() or None

        ok, err, code = _items_prep_undo_core(upc=upc, status=status, qty=qty, base_upc=base_upc)
        if not ok:
            try:
                _preplog_set_undo_error(log_id, err)
            except Exception:
                pass
            return jsonify({'success': False, 'error': err or 'Undo failed'}), (code or 400)

        updated = _preplog_mark_undone(log_id, undone_at=_listagent_now_iso(), undo_error=None)
        return jsonify({'success': True, 'item': updated})
    except Exception as e:
        return jsonify({'success': False, 'error': _safe_error(e, 'preplog:undo')}), 500

@app.route('/api/listingagent/photos', methods=['GET'])
def api_listingagent_photos_get():
    """Get uploaded listing photos for a UPC (from listagent.db)."""
    try:
        upc = (request.args.get('upc') or '').strip()
        if not upc:
            return jsonify({'success': False, 'error': 'upc is required'}), 400
        limit = _listingagent_parse_int(request.args.get('limit'), 30) or 30

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
        return jsonify({'success': False, 'error': _safe_error(e, 'listingagent:photos_get')}), 500

@app.route('/api/listingagent/photos/upload', methods=['POST'])
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

        save_dir = os.path.join(app.root_path, 'static', 'listingagent_uploads')
        os.makedirs(save_dir, exist_ok=True)

        base = (request.url_root or '').rstrip('/')
        saved_urls = []
        saved_count = 0
        safe_upc = secure_filename(_normalize_upc(upc)) or 'upc'

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
        return jsonify({'success': False, 'error': _safe_error(e, 'listingagent:photos_upload')}), 500

@app.route('/api/listingagent/live_listings/search', methods=['GET'])
def api_listingagent_live_listings_search():
    """Search live eBay/Amazon listings from local store DBs."""
    try:
        q = (request.args.get('q') or '').strip()
        limit = _listingagent_parse_int(request.args.get('limit'), 60) or 60
        results = _listagent_search_live_listings(q, limit=limit)
        return jsonify({'success': True, 'results': results})
    except Exception as e:
        return jsonify({'success': False, 'error': _safe_error(e, 'listingagent:live_listings_search')}), 500

@app.route('/api/listingagent/upc/search', methods=['GET'])
def api_listingagent_upc_search():
    """Typeahead for UPCs from searchRack.db and bol.db."""
    try:
        q = (request.args.get('q') or '').strip()
        limit = _listingagent_parse_int(request.args.get('limit'), 20) or 20
        limit = max(1, min(limit, 50))

        results_by_upc = {}

        like = f"%{q}%"
        with db_connection('searchRack.db') as conn:
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

        with db_connection('bol.db') as conn:
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
        return jsonify({'success': False, 'error': _safe_error(e, 'listingagent:upc_search')}), 500

@app.route('/api/listingagent/upc/<upc>', methods=['GET'])
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

        # searchRack inventory
        with db_connection('searchRack.db') as conn:
            cur = conn.cursor()
            cur.execute('''
                SELECT ID, TITLE, ITEM_POSITION, PICTUREPOSITION, QUANTITY, IMAGE, IMAGES, ITEMID, CREATED_AT
                FROM SEARCHRACK
                WHERE TRIM(BARCODE) = ? COLLATE NOCASE
                ORDER BY CREATED_AT DESC
            ''', (upc,))
            rows = cur.fetchall()

            total_qty = 0
            positions = []
            for r in rows:
                qty = int(r['QUANTITY'] or 0) if r['QUANTITY'] is not None else 0
                if qty <= 0:
                    qty = 1
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

        # BOL info
        with db_connection('bol.db') as conn:
            cur = conn.cursor()
            cur.execute('''
                SELECT item_description, image_url, lot_number, import_date,
                       good_qty, bad_qty, unchecked_qty, quantity,
                       listed_ebay, listed_ebay_date
                FROM bol_items
                WHERE TRIM(upc) = ? COLLATE NOCASE
                ORDER BY import_date DESC
                LIMIT 1
            ''', (upc,))
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
        with db_connection('ebayStore.db') as conn:
            cur = conn.cursor()
            cur.execute('''
                SELECT Title, ItemID, SKU, Price, Quantity, Image, URL, List_State, Sold_Date, List_Date
                FROM INVENTORY
                WHERE TRIM(UPC) = ? COLLATE NOCASE
                ORDER BY List_Date DESC
                LIMIT 10
            ''', (upc,))
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
            with db_connection('amazonStore.db') as conn:
                cur = conn.cursor()
                cur.execute('''
                    SELECT ASIN, SKU, TITLE, PRICE, QUANTITY, STATUS, IMAGE, UPC, CONDITION, FULFILLMENT_CHANNEL, LAST_UPDATED
                    FROM ITEMS
                    WHERE TRIM(UPC) = ? COLLATE NOCASE
                    ORDER BY LAST_UPDATED DESC
                    LIMIT 10
                ''', (upc,))
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
        item['prep'] = {'notes': [], 'images': []}
        try:
            _ensure_items_prep_tables()

            base_upc = (upc.split('-', 1)[0] if '-' in upc else upc).strip()
            base_upc = base_upc.lstrip('0') if base_upc and base_upc.isdigit() else base_upc
            like_upc = f"{base_upc}-%" if base_upc else ''

            prep_notes = []
            prep_images = []

            if base_upc:
                with db_connection('bol.db') as conn:
                    cur = conn.cursor()

                    # Notes (free-form) from Listing Manager / Item Prep
                    cur.execute('''
                        SELECT upc, id, note, created_at
                        FROM items_prep_notes
                        WHERE upc = ? COLLATE NOCASE OR upc LIKE ? COLLATE NOCASE
                        ORDER BY created_at DESC, id DESC
                        LIMIT 25
                    ''', (base_upc, like_upc))
                    prep_notes = [dict(r) for r in cur.fetchall()]

                    # Fallback: if no note bubbles exist, use the status.note (single field) if present.
                    if not prep_notes:
                        cur.execute('''
                            SELECT upc, note, updated_at
                            FROM items_prep_status
                            WHERE (upc = ? COLLATE NOCASE OR upc LIKE ? COLLATE NOCASE)
                              AND note IS NOT NULL
                              AND TRIM(note) != ''
                            ORDER BY COALESCE(updated_at, '') DESC
                            LIMIT 1
                        ''', (base_upc, like_upc))
                        sn = cur.fetchone()
                        if sn and (sn['note'] or '').strip():
                            prep_notes = [{
                                'upc': sn['upc'],
                                'id': None,
                                'note': sn['note'],
                                'created_at': sn['updated_at'] or ''
                            }]

                    # Photos from Listing Manager / Item Prep
                    cur.execute('''
                        SELECT upc, id, image_path, created_at, rotation
                        FROM items_prep_images
                        WHERE (upc = ? COLLATE NOCASE OR upc LIKE ? COLLATE NOCASE)
                          AND (deleted_at IS NULL OR TRIM(COALESCE(deleted_at,'')) = '')
                        ORDER BY created_at DESC, id DESC
                        LIMIT 30
                    ''', (base_upc, like_upc))
                    prep_images = [dict(r) for r in cur.fetchall()]

                base_url = (request.url_root or '').rstrip('/')
                prep_urls = []
                for pr in prep_images:
                    rel = (pr.get('image_path') or '').strip()
                    if not rel:
                        continue
                    url = f"{base_url}{url_for('static', filename=rel)}"
                    if url and url not in prep_urls:
                        prep_urls.append(url)

                if prep_urls:
                    # Prefer prep photos ahead of marketplace images, but keep existing order otherwise.
                    merged = []
                    for u in prep_urls + (item.get('images') or []):
                        if not u:
                            continue
                        if u not in merged:
                            merged.append(u)
                    item['images'] = merged

                item['prep'] = {'notes': prep_notes, 'images': prep_urls}
        except Exception:
            # Item prep tables may not exist in some envs; ignore.
            item['prep'] = {'notes': [], 'images': []}

        # Listing Agent uploaded photos (from listagent.db) — prefer these first.
        try:
            photo_rows = _listagent_get_photos(upc, limit=30)
            base = (request.url_root or '').rstrip('/')
            uploaded_urls = []
            for pr in photo_rows:
                rel = (pr.get('image_path') or '').strip()
                if not rel:
                    continue
                url = f"{base}{url_for('static', filename=rel)}"
                if url and url not in item['images']:
                    uploaded_urls.append(url)
            if uploaded_urls:
                item['images'] = uploaded_urls + item['images']
        except Exception:
            pass

        # sold stats (optional)
        with db_connection('sold.db') as conn:
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

        return jsonify({'success': True, 'item': item})
    except Exception as e:
        return jsonify({'success': False, 'error': _safe_error(e, 'listingagent:upc_detail')}), 500

def _ebay_api_request(method, path, *, params=None, payload=None, timeout=30):
    token = get_access_token()  # refreshes if expired
    headers_local = {
        'Authorization': f'Bearer {token}',
        'Content-Type': 'application/json',
        'Accept': 'application/json',
        'Content-Language': 'en-US'
    }
    url = f"https://api.ebay.com{path}"
    resp = requests.request(method, url, headers=headers_local, params=params, json=payload, timeout=timeout)
    return resp

def _ebay_extract_error(resp):
    try:
        data = resp.json()
        # Common eBay error schema: { errors: [ {message, errorId, ...} ] }
        errors = data.get('errors') if isinstance(data, dict) else None
        if errors and isinstance(errors, list):
            first = errors[0] or {}
            msg = first.get('message') or first.get('longMessage') or json.dumps(first)
            return f"eBay API error ({resp.status_code}): {msg}"
        if isinstance(data, dict) and data.get('message'):
            return f"eBay API error ({resp.status_code}): {data.get('message')}"
        return f"eBay API error ({resp.status_code}): {resp.text[:300]}"
    except Exception:
        return f"eBay API error ({resp.status_code}): {resp.text[:300]}"

@app.route('/api/listingagent/ebay/locations', methods=['GET'])
def api_listingagent_ebay_locations():
    """Fetch inventory locations (locationKey) from eBay (requires sell.inventory scope)."""
    try:
        resp = _ebay_api_request('GET', '/sell/inventory/v1/location', params={'limit': 50})
        if resp.status_code >= 400:
            return jsonify({'success': False, 'error': _ebay_extract_error(resp)}), 400
        return jsonify({'success': True, 'data': resp.json() if resp.text else {}})
    except Exception as e:
        return jsonify({'success': False, 'error': _safe_error(e, 'listingagent:ebay_locations')}), 500

_LISTINGAGENT_EBAY_POLICIES_CACHE = {}
_LISTINGAGENT_EBAY_POLICIES_LOCK = threading.Lock()

def _listingagent_get_ebay_business_policies(marketplace_id: str):
    """Fetch eBay business policies (fulfillment/payment/return). Cached in-memory."""
    marketplace_id = (marketplace_id or 'EBAY_US').strip() or 'EBAY_US'
    cache_key = marketplace_id
    now = time.time()

    cached = _LISTINGAGENT_EBAY_POLICIES_CACHE.get(cache_key)
    if cached and (now - float(cached.get('ts') or 0)) < 6 * 3600:
        return cached.get('data') or {}

    def _fetch(path):
        # Sell Account API endpoints only take marketplace_id; passing unknown params can 400.
        resp = _ebay_api_request('GET', path, params={'marketplace_id': marketplace_id})
        if resp.status_code == 403:
            # Most common cause: missing OAuth scope for Sell Account API.
            raise _ListingAgentUserError(
                "eBay API error (403): Forbidden. Your eBay OAuth token likely does not include the "
                "`sell.account` scope required to load business policies. Re-authorize your eBay app "
                "with `https://api.ebay.com/oauth/api_scope/sell.account` (or "
                "`https://api.ebay.com/oauth/api_scope/sell.account.readonly`) and regenerate "
                "`tokens.json`.",
                status_code=400,
                extra={'needs_scope': 'sell.account'}
            )
        if resp.status_code == 401:
            raise _ListingAgentUserError(
                "eBay API error (401): Unauthorized. Your token may be expired or invalid. "
                "Try re-authorizing and regenerating `tokens.json`.",
                status_code=400
            )
        if resp.status_code >= 400:
            raise _ListingAgentUserError(_ebay_extract_error(resp), status_code=400)
        return resp.json() if resp.text else {}

    with _LISTINGAGENT_EBAY_POLICIES_LOCK:
        cached = _LISTINGAGENT_EBAY_POLICIES_CACHE.get(cache_key)
        now = time.time()
        if cached and (now - float(cached.get('ts') or 0)) < 6 * 3600:
            return cached.get('data') or {}

        ful = _fetch('/sell/account/v1/fulfillment_policy')
        pay = _fetch('/sell/account/v1/payment_policy')
        ret = _fetch('/sell/account/v1/return_policy')

        def _norm_list(payload, list_key, id_key):
            items = payload.get(list_key) or payload.get(list_key[0].upper() + list_key[1:]) or []
            out = []
            if isinstance(items, list):
                for it in items:
                    if not isinstance(it, dict):
                        continue
                    pid = (it.get(id_key) or it.get(id_key[0].lower() + id_key[1:]) or '').strip()
                    if not pid:
                        continue
                    name = (it.get('name') or it.get('policyName') or it.get('policy_name') or '').strip()
                    out.append({
                        'id': pid,
                        'name': name or pid,
                        'categoryTypes': it.get('categoryTypes') or it.get('category_types') or [],
                    })
            out.sort(key=lambda x: (x.get('name') or '').lower())
            return out

        data = {
            'fulfillment': _norm_list(ful, 'fulfillmentPolicies', 'fulfillmentPolicyId'),
            'payment': _norm_list(pay, 'paymentPolicies', 'paymentPolicyId'),
            'returns': _norm_list(ret, 'returnPolicies', 'returnPolicyId'),
        }

        _LISTINGAGENT_EBAY_POLICIES_CACHE[cache_key] = {'ts': now, 'data': data}
        return data

@app.route('/api/listingagent/ebay/business_policies', methods=['GET'])
def api_listingagent_ebay_business_policies():
    """Get eBay business policies (fulfillment/payment/return) for dropdowns."""
    try:
        settings = _listingagent_get_settings()
        marketplace_id = (request.args.get('marketplaceId') or settings.get('ebay_marketplace_id') or 'EBAY_US').strip() or 'EBAY_US'
        data = _listingagent_get_ebay_business_policies(marketplace_id)
        out = {'success': True, 'marketplaceId': marketplace_id}
        out.update(data or {})
        return jsonify(out)
    except _ListingAgentUserError as e:
        payload = {'success': False, 'error': str(e)}
        payload.update(e.extra or {})
        return jsonify(payload), e.status_code
    except Exception as e:
        return jsonify({'success': False, 'error': _safe_error(e, 'listingagent:ebay_business_policies')}), 500

@app.route('/api/listingagent/ebay/offers_for_sku', methods=['GET'])
def api_listingagent_ebay_offers_for_sku():
    """Lookup Inventory offers for a SKU (helps edit existing listings)."""
    try:
        sku = (request.args.get('sku') or '').strip()
        if not sku:
            return jsonify({'success': False, 'error': 'sku is required'}), 400

        include_raw = (request.args.get('raw') or '').lower() == 'true'
        resp = _ebay_api_request('GET', '/sell/inventory/v1/offer', params={'sku': sku, 'limit': 50})
        if resp.status_code >= 400:
            return jsonify({'success': False, 'error': _ebay_extract_error(resp)}), 400

        payload = resp.json() if resp.text else {}
        offers = payload.get('offers') or payload.get('Offers') or []
        results = []
        if isinstance(offers, list):
            for o in offers:
                if not isinstance(o, dict):
                    continue
                results.append({
                    'offerId': o.get('offerId') or o.get('offer_id') or '',
                    'listingId': o.get('listingId') or o.get('listing_id') or '',
                    'marketplaceId': o.get('marketplaceId') or o.get('marketplace_id') or '',
                    'status': o.get('status') or '',
                    'format': o.get('format') or '',
                    'availableQuantity': o.get('availableQuantity') if o.get('availableQuantity') is not None else o.get('available_quantity'),
                    'categoryId': o.get('categoryId') or o.get('category_id') or '',
                    'pricingSummary': o.get('pricingSummary') or o.get('pricing_summary') or {},
                })

        out = {'success': True, 'data': {'sku': sku, 'offers': results}}
        if include_raw:
            out['raw'] = payload
        return jsonify(out)
    except Exception as e:
        return jsonify({'success': False, 'error': _safe_error(e, 'listingagent:ebay_offers_for_sku')}), 500

def _build_ebay_inventory_item_payload(upc, title, description, images, quantity, condition, *, aspects=None):
    images = [i.strip() for i in (images or []) if isinstance(i, str) and i.strip()]
    # eBay limits imageUrls to 12
    images = images[:12]
    payload = {
        'availability': {
            'shipToLocationAvailability': {
                'quantity': int(quantity or 1)
            }
        },
        'condition': condition or 'USED_GOOD',
        'product': {
            'title': title or upc,
            'description': description or (title or upc),
            'upc': [upc],
        }
    }
    if images:
        payload['product']['imageUrls'] = images
    if aspects and isinstance(aspects, dict):
        clean = {}
        for k, v in aspects.items():
            name = str(k or '').strip()
            if not name:
                continue
            vals = []
            if isinstance(v, str):
                vals = [v.strip()]
            elif isinstance(v, (list, tuple)):
                vals = [str(x).strip() for x in v if str(x).strip()]
            elif v is not None:
                vals = [str(v).strip()]
            vals = [x for x in vals if x]
            if vals:
                clean[name] = vals[:20]
        if clean:
            payload['product']['aspects'] = clean
    return payload

def _build_ebay_offer_payload(sku, marketplace_id, currency, price, quantity, category_id, listing_description,
                             merchant_location_key, fulfillment_policy_id, payment_policy_id, return_policy_id,
                             listing_duration='GTC', format_='FIXED_PRICE'):
    payload = {
        'sku': sku,
        'marketplaceId': marketplace_id,
        'format': format_,
    }
    if quantity is not None:
        payload['availableQuantity'] = int(quantity)
    if listing_duration:
        payload['listingDuration'] = listing_duration
    if category_id:
        payload['categoryId'] = str(category_id)
    if listing_description:
        payload['listingDescription'] = listing_description
    if merchant_location_key:
        payload['merchantLocationKey'] = str(merchant_location_key)
    if price is not None:
        payload['pricingSummary'] = {
            'price': {'currency': currency or 'USD', 'value': f"{float(price):.2f}"}
        }
    policies = {}
    if fulfillment_policy_id:
        policies['fulfillmentPolicyId'] = str(fulfillment_policy_id)
    if payment_policy_id:
        policies['paymentPolicyId'] = str(payment_policy_id)
    if return_policy_id:
        policies['returnPolicyId'] = str(return_policy_id)
    if policies:
        payload['listingPolicies'] = policies
    # Let eBay enrich if it can match a catalog product
    payload['includeCatalogProductDetails'] = True
    return payload

class _ListingAgentUserError(Exception):
    def __init__(self, message, status_code=400, extra=None):
        super().__init__(message)
        self.status_code = status_code
        self.extra = extra or {}

# eBay Buy/Browse requires an application token (client_credentials). We keep a simple in-memory cache
# to avoid re-auth on every request. This does not touch tokens.json (which is used for Sell APIs).
_LISTINGAGENT_EBAY_APP_TOKEN = {'access_token': None, 'expires_at': 0.0, 'scope': ''}
_LISTINGAGENT_EBAY_APP_TOKEN_LOCK = threading.Lock()

def _listingagent_get_ebay_app_token(scope=None):
    scope = (scope or os.getenv('EBAY_BUY_SCOPE') or 'https://api.ebay.com/oauth/api_scope').strip()
    now = time.time()
    cached = _LISTINGAGENT_EBAY_APP_TOKEN
    if cached.get('access_token') and cached.get('scope') == scope and now < float(cached.get('expires_at') or 0) - 60:
        return cached['access_token']

    if not CLIENT_ID or not CLIENT_SECRET:
        raise _ListingAgentUserError('Missing EBAY_CLIENT_ID / EBAY_CLIENT_SECRET in environment (.env)', status_code=503)

    with _LISTINGAGENT_EBAY_APP_TOKEN_LOCK:
        cached = _LISTINGAGENT_EBAY_APP_TOKEN
        now = time.time()
        if cached.get('access_token') and cached.get('scope') == scope and now < float(cached.get('expires_at') or 0) - 60:
            return cached['access_token']

        encoded = base64.b64encode(f"{CLIENT_ID}:{CLIENT_SECRET}".encode('utf-8')).decode('utf-8')
        headers = {
            'Content-Type': 'application/x-www-form-urlencoded',
            'Authorization': f"Basic {encoded}",
        }
        data = {
            'grant_type': 'client_credentials',
            'scope': scope
        }
        resp = requests.post('https://api.ebay.com/identity/v1/oauth2/token', headers=headers, data=data, timeout=30)
        if resp.status_code >= 400:
            try:
                j = resp.json()
                msg = j.get('error_description') or j.get('error') or resp.text[:200]
            except Exception:
                msg = resp.text[:200]
            raise _ListingAgentUserError(f"eBay app token failed: {msg}", status_code=503)

        j = resp.json() if resp.text else {}
        access_token = (j.get('access_token') or '').strip()
        expires_in = int(j.get('expires_in') or 0)
        if not access_token:
            raise _ListingAgentUserError('eBay app token response missing access_token', status_code=503)

        _LISTINGAGENT_EBAY_APP_TOKEN.update({
            'access_token': access_token,
            'expires_at': time.time() + max(60, expires_in),
            'scope': scope
        })
        return access_token

def _ebay_buy_api_request(method, path, *, params=None, timeout=30, marketplace_id='EBAY_US', scope=None):
    token = _listingagent_get_ebay_app_token(scope=scope)
    headers_local = {
        'Authorization': f'Bearer {token}',
        'Content-Type': 'application/json',
        'Accept': 'application/json',
        'Accept-Language': 'en-US',
        # Strongly recommended for Browse APIs.
        'X-EBAY-C-MARKETPLACE-ID': marketplace_id or 'EBAY_US',
    }
    url = f"https://api.ebay.com{path}"
    resp = requests.request(method, url, headers=headers_local, params=params, timeout=timeout)
    return resp

@app.route('/api/listingagent/ebay/comps', methods=['GET'])
def api_listingagent_ebay_comps():
    """Fetch live eBay marketplace comps by UPC (Buy Browse API)."""
    try:
        upc = (request.args.get('upc') or request.args.get('gtin') or '').strip()
        if not upc:
            raise _ListingAgentUserError('upc is required', status_code=400)

        limit = _listingagent_parse_int(request.args.get('limit'), 12) or 12
        limit = max(1, min(limit, 50))
        include_raw = (request.args.get('raw') or '').lower() == 'true'

        settings = _listingagent_get_settings()
        marketplace_id = (request.args.get('marketplaceId') or settings.get('ebay_marketplace_id') or 'EBAY_US').strip()
        sort = (request.args.get('sort') or '').strip()

        params = {'gtin': upc, 'limit': limit}
        if sort:
            params['sort'] = sort

        resp = _ebay_buy_api_request('GET', '/buy/browse/v1/item_summary/search', params=params, marketplace_id=marketplace_id)
        if resp.status_code >= 400:
            raise _ListingAgentUserError(_ebay_extract_error(resp), status_code=400)

        payload = resp.json() if resp.text else {}
        items = payload.get('itemSummaries') or payload.get('item_summaries') or []

        results = []
        for it in items[:limit]:
            price = it.get('price') or {}

            # shipping cost (best-effort; often empty)
            shipping = None
            ship_opts = it.get('shippingOptions') or it.get('shipping_options') or []
            if ship_opts and isinstance(ship_opts, list):
                sc = (ship_opts[0] or {}).get('shippingCost') or (ship_opts[0] or {}).get('shipping_cost') or {}
                if isinstance(sc, dict) and sc.get('value') is not None:
                    shipping = {'value': sc.get('value'), 'currency': sc.get('currency')}

            image_url = ''
            img = it.get('image') or {}
            if isinstance(img, dict):
                image_url = (img.get('imageUrl') or img.get('image_url') or '').strip()
            if not image_url:
                thumbs = it.get('thumbnailImages') or it.get('thumbnail_images') or []
                if thumbs and isinstance(thumbs, list):
                    t0 = thumbs[0] or {}
                    if isinstance(t0, dict):
                        image_url = (t0.get('imageUrl') or t0.get('image_url') or '').strip()

            seller = ''
            seller_obj = it.get('seller') or {}
            if isinstance(seller_obj, dict):
                seller = (seller_obj.get('username') or '').strip()

            results.append({
                'itemId': it.get('itemId') or it.get('item_id') or '',
                'title': it.get('title') or '',
                'price': {'value': price.get('value'), 'currency': price.get('currency')},
                'shipping': shipping,
                'condition': it.get('condition') or '',
                'conditionId': it.get('conditionId') or it.get('condition_id') or '',
                'itemWebUrl': it.get('itemWebUrl') or it.get('item_web_url') or '',
                'image': image_url,
                'seller': seller,
                'buyingOptions': it.get('buyingOptions') or it.get('buying_options') or [],
            })

        out = {'success': True, 'total': payload.get('total') or len(results), 'results': results}
        if include_raw:
            out['raw'] = payload
        return jsonify(out)

    except _ListingAgentUserError as e:
        payload = {'success': False, 'error': str(e)}
        payload.update(e.extra or {})
        return jsonify(payload), e.status_code
    except Exception as e:
        return jsonify({'success': False, 'error': _safe_error(e, 'listingagent:ebay_comps')}), 500

_LISTINGAGENT_EBAY_CATEGORY_TREE_ID = {}
_LISTINGAGENT_EBAY_CATEGORY_TREE_LOCK = threading.Lock()

def _listingagent_get_ebay_category_tree_id(marketplace_id: str):
    mp = (marketplace_id or 'EBAY_US').strip() or 'EBAY_US'
    cached = _LISTINGAGENT_EBAY_CATEGORY_TREE_ID.get(mp)
    if cached:
        return cached
    with _LISTINGAGENT_EBAY_CATEGORY_TREE_LOCK:
        cached = _LISTINGAGENT_EBAY_CATEGORY_TREE_ID.get(mp)
        if cached:
            return cached
        resp = _ebay_buy_api_request(
            'GET',
            '/commerce/taxonomy/v1/get_default_category_tree_id',
            params={'marketplace_id': mp},
            marketplace_id=mp,
            scope='https://api.ebay.com/oauth/api_scope'
        )
        if resp.status_code >= 400:
            raise _ListingAgentUserError(_ebay_extract_error(resp), status_code=400)
        data = resp.json() if resp.text else {}
        tree_id = str(data.get('categoryTreeId') or data.get('category_tree_id') or '').strip()
        if not tree_id:
            raise _ListingAgentUserError('eBay taxonomy did not return categoryTreeId', status_code=502, extra={'raw': data})
        _LISTINGAGENT_EBAY_CATEGORY_TREE_ID[mp] = tree_id
        return tree_id

@app.route('/api/listingagent/ebay/category_suggestions', methods=['GET'])
def api_listingagent_ebay_category_suggestions():
    """Suggest eBay categories for a given query using Commerce Taxonomy API."""
    try:
        q = (request.args.get('q') or request.args.get('query') or '').strip()
        if not q:
            raise _ListingAgentUserError('q is required', status_code=400)

        limit = _listingagent_parse_int(request.args.get('limit'), 12) or 12
        limit = max(1, min(limit, 50))
        include_raw = (request.args.get('raw') or '').lower() == 'true'

        settings = _listingagent_get_settings()
        marketplace_id = (request.args.get('marketplaceId') or settings.get('ebay_marketplace_id') or 'EBAY_US').strip()

        category_tree_id = _listingagent_get_ebay_category_tree_id(marketplace_id)

        resp = _ebay_buy_api_request(
            'GET',
            f'/commerce/taxonomy/v1/category_tree/{category_tree_id}/get_category_suggestions',
            params={'q': q},
            marketplace_id=marketplace_id,
            scope='https://api.ebay.com/oauth/api_scope'
        )
        if resp.status_code >= 400:
            raise _ListingAgentUserError(_ebay_extract_error(resp), status_code=400)

        payload = resp.json() if resp.text else {}
        suggestions = payload.get('categorySuggestions') or payload.get('category_suggestions') or []

        results = []
        for s in (suggestions or [])[:limit]:
            cat = s.get('category') or {}
            cat_id = str(cat.get('categoryId') or cat.get('category_id') or '').strip()
            cat_name = (cat.get('categoryName') or cat.get('category_name') or '').strip()

            ancestors = s.get('categoryTreeNodeAncestors') or s.get('category_tree_node_ancestors') or []
            path_parts = []
            if isinstance(ancestors, list):
                for a in ancestors:
                    if not isinstance(a, dict):
                        continue
                    nm = (a.get('categoryName') or a.get('category_name') or '').strip()
                    if nm:
                        path_parts.append(nm)
            if cat_name:
                path_parts.append(cat_name)
            path = ' > '.join(path_parts) if path_parts else cat_name

            relevancy = s.get('relevancy')
            try:
                relevancy = float(relevancy) if relevancy is not None else None
            except Exception:
                relevancy = None

            if not cat_id:
                continue
            results.append({
                'categoryId': cat_id,
                'categoryName': cat_name,
                'path': path or cat_name or cat_id,
                'relevancy': relevancy,
            })

        out = {
            'success': True,
            'marketplaceId': marketplace_id,
            'categoryTreeId': category_tree_id,
            'recommended': results[0] if results else None,
            'results': results,
        }
        if include_raw:
            out['raw'] = payload
        return jsonify(out)

    except _ListingAgentUserError as e:
        payload = {'success': False, 'error': str(e)}
        payload.update(e.extra or {})
        return jsonify(payload), e.status_code
    except Exception as e:
        return jsonify({'success': False, 'error': _safe_error(e, 'listingagent:ebay_category_suggestions')}), 500

_LISTINGAGENT_EBAY_ASPECTS_CACHE = {}
_LISTINGAGENT_EBAY_ASPECTS_LOCK = threading.Lock()

def _listingagent_get_ebay_category_aspects(category_id: str, marketplace_id: str, *, values_limit: int = 140):
    """Fetch item aspects metadata for an eBay category (Commerce Taxonomy). Cached in-memory."""
    category_id = (category_id or '').strip()
    marketplace_id = (marketplace_id or 'EBAY_US').strip() or 'EBAY_US'
    values_limit = int(values_limit or 140)
    values_limit = max(0, min(values_limit, 250))

    cache_key = (marketplace_id, category_id, values_limit)
    now = time.time()
    cached = _LISTINGAGENT_EBAY_ASPECTS_CACHE.get(cache_key)
    if cached and (now - float(cached.get('ts') or 0)) < 6 * 3600:
        return cached.get('data') or []

    with _LISTINGAGENT_EBAY_ASPECTS_LOCK:
        cached = _LISTINGAGENT_EBAY_ASPECTS_CACHE.get(cache_key)
        if cached and (now - float(cached.get('ts') or 0)) < 6 * 3600:
            return cached.get('data') or []

        category_tree_id = _listingagent_get_ebay_category_tree_id(marketplace_id)
        resp = _ebay_buy_api_request(
            'GET',
            f'/commerce/taxonomy/v1/category_tree/{category_tree_id}/get_item_aspects_for_category',
            params={'category_id': category_id},
            marketplace_id=marketplace_id,
            scope='https://api.ebay.com/oauth/api_scope'
        )
        if resp.status_code >= 400:
            raise _ListingAgentUserError(_ebay_extract_error(resp), status_code=400)

        payload = resp.json() if resp.text else {}
        aspects = payload.get('aspects') or payload.get('Aspects') or []

        results = []
        if isinstance(aspects, list):
            for a in aspects:
                if not isinstance(a, dict):
                    continue
                name = (a.get('aspectName') or a.get('aspect_name') or '').strip()
                if not name:
                    continue
                constraint = a.get('aspectConstraint') or a.get('aspect_constraint') or {}
                if not isinstance(constraint, dict):
                    constraint = {}

                required = bool(constraint.get('aspectRequired') or constraint.get('aspect_required') or False)
                mode = (constraint.get('aspectMode') or constraint.get('aspect_mode') or '').strip()
                max_values = constraint.get('aspectMaxValues')
                if max_values is None:
                    max_values = constraint.get('aspect_max_values')
                try:
                    max_values = int(max_values) if max_values is not None else 1
                except Exception:
                    max_values = 1
                if max_values < 1:
                    max_values = 1

                values_raw = a.get('aspectValues') or a.get('aspect_values') or []
                values_count = len(values_raw) if isinstance(values_raw, list) else 0
                values = []
                if values_limit and isinstance(values_raw, list):
                    for v in values_raw[:values_limit]:
                        if not isinstance(v, dict):
                            continue
                        val = (v.get('localizedAspectValue') or v.get('localized_aspect_value') or v.get('value') or '').strip()
                        if val:
                            values.append(val)

                results.append({
                    'name': name,
                    'required': required,
                    'mode': mode,
                    'maxValues': max_values,
                    'values': values,
                    'valuesCount': values_count,
                    'valuesTruncated': bool(values_limit and values_count > len(values))
                })

        # Required first, then A-Z
        results.sort(key=lambda x: (0 if x.get('required') else 1, (x.get('name') or '').lower()))

        _LISTINGAGENT_EBAY_ASPECTS_CACHE[cache_key] = {'ts': now, 'data': results}
        return results

@app.route('/api/listingagent/ebay/category_aspects', methods=['GET'])
def api_listingagent_ebay_category_aspects():
    """Get eBay item specifics (aspects) for a categoryId (Commerce Taxonomy)."""
    try:
        category_id = (request.args.get('categoryId') or request.args.get('category_id') or '').strip()
        if not category_id:
            raise _ListingAgentUserError('categoryId is required', status_code=400)

        values_limit = _listingagent_parse_int(request.args.get('valuesLimit'), 140) or 140
        include_raw = (request.args.get('raw') or '').lower() == 'true'

        settings = _listingagent_get_settings()
        marketplace_id = (request.args.get('marketplaceId') or settings.get('ebay_marketplace_id') or 'EBAY_US').strip()

        aspects = _listingagent_get_ebay_category_aspects(category_id, marketplace_id, values_limit=values_limit)

        out = {
            'success': True,
            'marketplaceId': marketplace_id,
            'categoryId': category_id,
            'aspects': aspects
        }
        if include_raw:
            # Raw is not cached; fetch again with full payload when requested.
            category_tree_id = _listingagent_get_ebay_category_tree_id(marketplace_id)
            resp = _ebay_buy_api_request(
                'GET',
                f'/commerce/taxonomy/v1/category_tree/{category_tree_id}/get_item_aspects_for_category',
                params={'category_id': category_id},
                marketplace_id=marketplace_id,
                scope='https://api.ebay.com/oauth/api_scope'
            )
            out['raw'] = resp.json() if resp.text else {}
        return jsonify(out)
    except _ListingAgentUserError as e:
        payload = {'success': False, 'error': str(e)}
        payload.update(e.extra or {})
        return jsonify(payload), e.status_code
    except Exception as e:
        return jsonify({'success': False, 'error': _safe_error(e, 'listingagent:ebay_category_aspects')}), 500

def _listingagent_ebay_create_draft_offer(data, *, dry_run=False):
    upc = (data.get('upc') or '').strip()
    sku = (data.get('sku') or upc).strip()
    if not upc:
        raise _ListingAgentUserError('UPC is required')
    if not sku:
        raise _ListingAgentUserError('SKU is required')

    title = (data.get('title') or '').strip()
    description = (data.get('description') or '').strip()
    condition = (data.get('condition') or '').strip() or 'USED_GOOD'

    quantity = _listingagent_parse_int(data.get('quantity'), 1) or 1
    price = _listingagent_parse_float(data.get('price'), None)

    settings = _listingagent_get_settings()
    marketplace_id = (data.get('marketplaceId') or settings.get('ebay_marketplace_id') or 'EBAY_US').strip()
    currency = (data.get('currency') or settings.get('ebay_currency') or 'USD').strip()
    category_id = (data.get('categoryId') or settings.get('ebay_category_id') or '').strip()
    listing_duration = (data.get('listingDuration') or settings.get('ebay_listing_duration') or 'GTC').strip()

    merchant_location_key = (data.get('merchantLocationKey') or settings.get('ebay_location_key') or '').strip()
    fulfillment_policy_id = (data.get('fulfillmentPolicyId') or settings.get('ebay_fulfillment_policy_id') or '').strip()
    payment_policy_id = (data.get('paymentPolicyId') or settings.get('ebay_payment_policy_id') or '').strip()
    return_policy_id = (data.get('returnPolicyId') or settings.get('ebay_return_policy_id') or '').strip()

    listing_description = (data.get('listingDescription') or '').strip()

    images = data.get('images') or []
    if isinstance(images, str):
        images = [images]

    aspects = data.get('aspects') or {}
    if not isinstance(aspects, dict):
        aspects = {}

    inventory_item_payload = _build_ebay_inventory_item_payload(upc, title, description, images, quantity, condition, aspects=aspects)
    offer_payload = _build_ebay_offer_payload(
        sku, marketplace_id, currency, price, quantity, category_id, listing_description,
        merchant_location_key, fulfillment_policy_id, payment_policy_id, return_policy_id,
        listing_duration=listing_duration
    )

    if dry_run:
        return {'success': True, 'dry_run': True, 'inventoryItem': inventory_item_payload, 'offer': offer_payload}

    from urllib.parse import quote
    sku_encoded = quote(sku, safe='')

    resp_item = _ebay_api_request('PUT', f'/sell/inventory/v1/inventory_item/{sku_encoded}', payload=inventory_item_payload)
    if resp_item.status_code >= 400:
        raise _ListingAgentUserError(
            _ebay_extract_error(resp_item),
            status_code=400,
            extra={'inventoryItem': inventory_item_payload}
        )

    resp_offer = _ebay_api_request('POST', '/sell/inventory/v1/offer', payload=offer_payload)
    if resp_offer.status_code >= 400:
        raise _ListingAgentUserError(
            _ebay_extract_error(resp_offer),
            status_code=400,
            extra={'offer': offer_payload}
        )

    offer_data = resp_offer.json() if resp_offer.text else {}
    return {
        'success': True,
        'offerId': offer_data.get('offerId'),
        'inventoryItem': inventory_item_payload,
        'offer': offer_payload,
        'raw': offer_data
    }

@app.route('/api/listingagent/ebay/draft', methods=['POST'])
def api_listingagent_ebay_draft():
    """Create/replace an inventory item and create a draft offer (not published)."""
    try:
        data = request.json or {}
        dry_run = bool(data.get('dry_run', False))
        result = _listingagent_ebay_create_draft_offer(data, dry_run=dry_run)
        return jsonify(result)
    except _ListingAgentUserError as e:
        payload = {'success': False, 'error': str(e)}
        payload.update(e.extra or {})
        return jsonify(payload), e.status_code
    except Exception as e:
        return jsonify({'success': False, 'error': _safe_error(e, 'listingagent:ebay_draft')}), 500

@app.route('/api/listingagent/ebay/publish', methods=['POST'])
def api_listingagent_ebay_publish():
    """Publish an offer (creates inventory item + offer if needed)."""
    try:
        data = request.json or {}
        confirm = bool(data.get('confirm', False))
        dry_run = bool(data.get('dry_run', False))

        offer_id = (data.get('offerId') or '').strip()
        if not confirm and not dry_run:
            return jsonify({'success': False, 'error': 'Confirmation required to publish'}), 400
 
        inventory_payload = None
        offer_payload = None
        # If an offerId was provided, treat this as an edit/revise flow:
        # update inventory item + offer (if we have full fields) and then publish to apply changes.
        if offer_id:
            wants_update = bool(data.get('updateExisting', True))
            upc_for_update = (data.get('upc') or '').strip()
            if wants_update and upc_for_update:
                required = ['upc', 'sku', 'title', 'listingDescription', 'price', 'quantity', 'categoryId',
                            'merchantLocationKey', 'fulfillmentPolicyId', 'paymentPolicyId', 'returnPolicyId']
                missing = [k for k in required if not str(data.get(k) or '').strip()]
                if missing and not dry_run:
                    return jsonify({'success': False, 'error': f"Missing required fields to update: {', '.join(missing)}"}), 400
  
                # Reuse the draft builder to generate payloads without creating a new offer.
                preview = _listingagent_ebay_create_draft_offer(data, dry_run=True)
                inventory_payload = preview.get('inventoryItem')
                offer_payload = preview.get('offer')
  
                if dry_run:
                    return jsonify({'success': True, 'dry_run': True, 'offerId': offer_id, 'inventoryItem': inventory_payload, 'offer': offer_payload, 'wouldPublish': True, 'wouldUpdate': True})
  
                from urllib.parse import quote
                sku_local = (data.get('sku') or upc_for_update).strip()
                sku_encoded = quote(sku_local, safe='')
  
                resp_item = _ebay_api_request('PUT', f'/sell/inventory/v1/inventory_item/{sku_encoded}', payload=inventory_payload)
                if resp_item.status_code >= 400:
                    return jsonify({'success': False, 'error': _ebay_extract_error(resp_item), 'inventoryItem': inventory_payload}), 400
  
                resp_offer = _ebay_api_request('PUT', f'/sell/inventory/v1/offer/{offer_id}', payload=offer_payload)
                if resp_offer.status_code >= 400:
                    return jsonify({'success': False, 'error': _ebay_extract_error(resp_offer), 'offer': offer_payload}), 400
  
        if not offer_id:
            # Enforce publish-required fields (unless dry_run)
            required = ['upc', 'sku', 'title', 'listingDescription', 'price', 'quantity', 'categoryId',
                        'merchantLocationKey', 'fulfillmentPolicyId', 'paymentPolicyId', 'returnPolicyId']
            missing = [k for k in required if not str(data.get(k) or '').strip()]
            if missing and not dry_run:
                return jsonify({'success': False, 'error': f"Missing required fields: {', '.join(missing)}"}), 400

            draft_result = _listingagent_ebay_create_draft_offer(data, dry_run=dry_run)
            if dry_run:
                # Tell the UI what would happen next
                return jsonify({**draft_result, 'wouldPublish': True})

            offer_id = (draft_result.get('offerId') or '').strip()
            inventory_payload = draft_result.get('inventoryItem')
            offer_payload = draft_result.get('offer')
            if not offer_id:
                return jsonify({'success': False, 'error': 'Draft offer created but no offerId returned'}), 500

        if dry_run:
            return jsonify({'success': True, 'dry_run': True, 'offerId': offer_id, 'inventoryItem': inventory_payload, 'offer': offer_payload, 'wouldPublish': True})

        resp_pub = _ebay_api_request('POST', f'/sell/inventory/v1/offer/{offer_id}/publish')
        if resp_pub.status_code >= 400:
            return jsonify({'success': False, 'error': _ebay_extract_error(resp_pub)}), 400

        pub_data = resp_pub.json() if resp_pub.text else {}
        listing_id = pub_data.get('listingId')

        # Mark BOL as listed on eBay so /items-to-list marketplace checkboxes stay in sync.
        upc = (data.get('upc') or '').strip()
        if upc:
            try:
                now_iso = datetime.datetime.now().isoformat()
                like_upc = f"{upc}-%"
                with db_connection('bol.db') as conn:
                    cur = conn.cursor()
                    cur.execute('''
                        UPDATE bol_items
                        SET listed_ebay = 1, listed_ebay_date = ?
                        WHERE TRIM(upc) = ? COLLATE NOCASE
                           OR TRIM(upc) LIKE ? COLLATE NOCASE
                    ''', (now_iso, upc, like_upc))
            except Exception:
                pass
 
        # Record listing completion in listagent.db (if UPC is available)
        try:
            if upc:
                sku_local = (data.get('sku') or upc).strip() or None
                try:
                    settings_local = _listingagent_get_settings()
                    marketplace_id_local = (data.get('marketplaceId') or settings_local.get('ebay_marketplace_id') or 'EBAY_US').strip() or 'EBAY_US'
                except Exception:
                    marketplace_id_local = (data.get('marketplaceId') or 'EBAY_US').strip() or 'EBAY_US'
                title_local = (data.get('title') or '').strip() or None
                qty_local = _listingagent_parse_int(data.get('quantity'), None)
                price_local = _listingagent_parse_float(data.get('price'), None)
                _listagent_mark_listed(
                    upc,
                    platform='ebay',
                    listing_id=listing_id,
                    offer_id=offer_id,
                    sku=sku_local,
                    marketplace_id=marketplace_id_local,
                    title=title_local,
                    price=price_local,
                    quantity=qty_local,
                    source='listingagent'
                )
        except Exception:
            pass
  
        return jsonify({'success': True, 'listingId': listing_id, 'raw': pub_data})
    except _ListingAgentUserError as e:
        payload = {'success': False, 'error': str(e)}
        payload.update(e.extra or {})
        return jsonify(payload), e.status_code
    except Exception as e:
        return jsonify({'success': False, 'error': _safe_error(e, 'listingagent:ebay_publish')}), 500

# -----------------------------
# Listing Agent - Amazon (SP-API)
# -----------------------------

def _amazon_marketplace_from_id(marketplace_id: str):
    try:
        from sp_api.base import Marketplaces
        if not marketplace_id:
            return Marketplaces.US
        for name in dir(Marketplaces):
            if not name.isupper():
                continue
            mp = getattr(Marketplaces, name)
            if getattr(mp, 'marketplace_id', None) == marketplace_id:
                return mp
        return Marketplaces.US
    except Exception:
        # If SP-API isn't installed or something is misconfigured, default to US.
        return None

def _amazon_spapi_context():
    """Load SP-API credentials + seller/marketplace from amazon_credentials.json."""
    creds_path = BASE_DIR / 'amazon_credentials.json'
    if not creds_path.exists():
        raise _ListingAgentUserError('amazon_credentials.json not found', status_code=503)

    with open(creds_path, 'r', encoding='utf-8') as f:
        c = json.load(f)

    seller_id = (c.get('seller_id') or '').strip()
    marketplace_id = (c.get('marketplace_id') or '').strip() or 'ATVPDKIKX0DER'
    if not seller_id:
        raise _ListingAgentUserError('Amazon seller_id missing in amazon_credentials.json', status_code=503)

    credentials = {
        'refresh_token': c.get('refresh_token'),
        'lwa_app_id': c.get('lwa_app_id'),
        'lwa_client_secret': c.get('lwa_client_secret', ''),
    }
    if c.get('aws_access_key') and c.get('aws_secret_key'):
        credentials['aws_access_key'] = c.get('aws_access_key')
        credentials['aws_secret_key'] = c.get('aws_secret_key')
    if c.get('role_arn'):
        credentials['role_arn'] = c.get('role_arn')

    marketplace = _amazon_marketplace_from_id(marketplace_id)
    return credentials, seller_id, marketplace_id, marketplace

def _amazon_get_listing_product_type(li, seller_id, sku, marketplace_id, cache=None):
    cache = cache if cache is not None else {}
    if sku in cache:
        return cache.get(sku)
    try:
        resp = li.get_listings_item(
            seller_id,
            sku,
            marketplaceIds=[marketplace_id],
            includedData=['summaries']
        )
        if getattr(resp, 'errors', None):
            raise Exception(str(resp.errors))
        payload = resp.payload or {}
        summaries = payload.get('summaries') or []
        pt = None
        for s in summaries:
            pt = s.get('productType')
            if pt:
                break
        if not pt:
            pt = payload.get('productType')
        if pt:
            cache[sku] = pt
        return pt
    except Exception:
        return None

def _amazon_get_catalog_product_type(credentials, marketplace, marketplace_id, asin):
    if not asin:
        return None
    try:
        from sp_api.api import CatalogItems
        ci = CatalogItems(credentials=credentials, marketplace=marketplace, version='2022-04-01')
        resp = ci.get_catalog_item(
            asin,
            marketplaceIds=[marketplace_id],
            includedData=['productTypes']
        )
        if getattr(resp, 'errors', None):
            return None
        payload = resp.payload or {}
        pts = payload.get('productTypes') or []
        for pt in pts:
            if isinstance(pt, dict) and pt.get('productType'):
                return pt.get('productType')
        # Some responses may nest product types elsewhere
        pt = payload.get('productType')
        return pt
    except Exception:
        return None

def _amazon_build_price_feed_xml(*, seller_id, jobs, currency='USD'):
    # Legacy XML feed for price updates (does not require product type).
    from xml.sax.saxutils import escape as _xesc
    lines = [
        '<?xml version="1.0" encoding="utf-8"?>',
        '<AmazonEnvelope xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xsi:noNamespaceSchemaLocation="amzn-envelope.xsd">',
        '  <Header>',
        '    <DocumentVersion>1.01</DocumentVersion>',
        f'    <MerchantIdentifier>{_xesc(str(seller_id))}</MerchantIdentifier>',
        '  </Header>',
        '  <MessageType>Price</MessageType>',
    ]
    msg_id = 1
    for j in jobs:
        sku = j.get('sku')
        price = j.get('new_price')
        if not sku or price is None:
            continue
        lines.extend([
            '  <Message>',
            f'    <MessageID>{msg_id}</MessageID>',
            '    <Price>',
            f'      <SKU>{_xesc(str(sku))}</SKU>',
            f'      <StandardPrice currency="{_xesc(str(currency).upper())}">{float(price):.2f}</StandardPrice>',
            '    </Price>',
            '  </Message>',
        ])
        msg_id += 1
    lines.append('</AmazonEnvelope>')
    return '\n'.join(lines).encode('utf-8')

def _amazon_submit_price_feed_json(*, credentials, marketplace, marketplace_id, seller_id, jobs, currency='USD', offer_audience='ALL'):
    from sp_api.api import Feeds
    feed = {
        'header': {
            'sellerId': seller_id,
            'version': '2.0',
            'issueLocale': 'en_US'
        },
        'messages': []
    }
    msg_id = 1
    for j in jobs:
        sku = j.get('sku')
        product_type = j.get('product_type')
        price = j.get('new_price')
        if not sku or not product_type:
            continue
        attrs = _amazon_offer_price_attrs(currency, price, marketplace_id, offer_audience=offer_audience)
        feed['messages'].append({
            'messageId': msg_id,
            'sku': sku,
            'operationType': 'PATCH',
            'productType': product_type,
            'patches': [{
                'op': 'replace',
                'path': '/attributes/purchasable_offer',
                'value': attrs.get('purchasable_offer')
            }]
        })
        msg_id += 1

    if not feed['messages']:
        raise Exception('No feed messages to submit')

    feeds = Feeds(credentials=credentials, marketplace=marketplace)
    content_type = 'application/json; charset=UTF-8'
    body = json.dumps(feed, ensure_ascii=True).encode('utf-8')
    try:
        doc_id, url, uploaded = _amazon_create_feed_document(feeds, content_type, body)
    except Exception as e:
        raise Exception(_amazon_feeds_error(e, 'create_feed_document'))
    if not doc_id:
        raise Exception('Failed to create feed document')
    if not uploaded:
        if not url:
            raise Exception('Feed document upload URL missing')
        up = requests.put(url, data=body, headers={'Content-Type': content_type})
        if up.status_code >= 400:
            raise Exception(f'Feed upload failed ({up.status_code})')

    try:
        feed_resp = _amazon_create_feed(
            feeds,
            feed_type='JSON_LISTINGS_FEED',
            marketplace_ids=[marketplace_id],
            input_feed_document_id=doc_id
        )
        feed_payload = getattr(feed_resp, 'payload', None) or {}
    except Exception as e:
        raise Exception(_amazon_feeds_error(e, 'create_feed'))
    feed_id = feed_payload.get('feedId') or feed_payload.get('feed_id')
    if not feed_id:
        raise Exception('Feed submission failed')
    return feed_id

def _amazon_submit_price_feed_xml(*, credentials, marketplace, marketplace_id, seller_id, jobs, currency='USD'):
    from sp_api.api import Feeds
    feeds = Feeds(credentials=credentials, marketplace=marketplace)
    content_type = 'text/xml; charset=UTF-8'
    body = _amazon_build_price_feed_xml(seller_id=seller_id, jobs=jobs, currency=currency)
    try:
        doc_id, url, uploaded = _amazon_create_feed_document(feeds, content_type, body)
    except Exception as e:
        raise Exception(_amazon_feeds_error(e, 'create_feed_document'))
    if not doc_id:
        raise Exception('Failed to create feed document')
    if not uploaded:
        if not url:
            raise Exception('Feed document upload URL missing')
        up = requests.put(url, data=body, headers={'Content-Type': content_type})
        if up.status_code >= 400:
            raise Exception(f'Feed upload failed ({up.status_code})')

    try:
        feed_resp = _amazon_create_feed(
            feeds,
            feed_type='POST_PRODUCT_PRICING_DATA',
            marketplace_ids=[marketplace_id],
            input_feed_document_id=doc_id
        )
        feed_payload = getattr(feed_resp, 'payload', None) or {}
    except Exception as e:
        raise Exception(_amazon_feeds_error(e, 'create_feed'))
    feed_id = feed_payload.get('feedId') or feed_payload.get('feed_id')
    if not feed_id:
        raise Exception('Feed submission failed')
    return feed_id

def _amazon_submit_price_feed(*, credentials, marketplace, marketplace_id, seller_id, jobs, currency='USD', offer_audience='ALL'):
    try:
        return _amazon_submit_price_feed_json(
            credentials=credentials,
            marketplace=marketplace,
            marketplace_id=marketplace_id,
            seller_id=seller_id,
            jobs=jobs,
            currency=currency,
            offer_audience=offer_audience
        )
    except Exception as e:
        json_err = _amazon_format_spapi_error(e)
        try:
            return _amazon_submit_price_feed_xml(
                credentials=credentials,
                marketplace=marketplace,
                marketplace_id=marketplace_id,
                seller_id=seller_id,
                jobs=jobs,
                currency=currency
            )
        except Exception as e2:
            xml_err = _amazon_format_spapi_error(e2)
            raise Exception(f"JSON feed failed: {json_err} | XML feed failed: {xml_err}")

def _amazon_parse_feed_report(body_bytes):
    """Parse Amazon feed processing report (JSON or XML). Returns dict with errors/warnings."""
    out = {
        'error_count': 0,
        'warning_count': 0,
        'errors': [],
        'warnings': [],
        'raw_excerpt': ''
    }
    if not body_bytes:
        return out
    try:
        raw = body_bytes.decode('utf-8', errors='replace')
    except Exception:
        try:
            raw = body_bytes.decode('latin-1', errors='replace')
        except Exception:
            raw = ''
    out['raw_excerpt'] = (raw[:1200] + '...') if len(raw) > 1200 else raw

    # Try JSON first
    try:
        import json as _json
        js = _json.loads(raw)
        issues = []
        if isinstance(js, dict):
            issues = js.get('issues') or js.get('issuesWithAttributes') or js.get('messagesWithIssues') or []
        if isinstance(issues, dict):
            issues = [issues]
        for it in issues:
            sev = str(it.get('severity') or it.get('Severity') or '').upper()
            msg = it.get('message') or it.get('messageText') or it.get('description') or it.get('Message') or ''
            code = it.get('code') or it.get('Code') or ''
            if sev == 'ERROR':
                out['error_count'] += 1
                out['errors'].append({'code': code, 'message': msg})
            elif sev == 'WARNING':
                out['warning_count'] += 1
                out['warnings'].append({'code': code, 'message': msg})
        return out
    except Exception:
        pass

    # Fallback: XML
    try:
        import xml.etree.ElementTree as _ET
        root = _ET.fromstring(raw)
        for result in root.findall('.//Result'):
            code = (result.findtext('ResultCode') or '').strip().lower()
            msg = (result.findtext('ResultMessage') or '').strip()
            if code == 'error':
                out['error_count'] += 1
                out['errors'].append({'code': 'Error', 'message': msg})
            elif code == 'warning':
                out['warning_count'] += 1
                out['warnings'].append({'code': 'Warning', 'message': msg})
        return out
    except Exception:
        return out

def _amazon_get_feed_document_info(feeds, feed_document_id):
    import inspect as _inspect
    try:
        params = _inspect.signature(feeds.get_feed_document).parameters
    except Exception:
        params = {}
    if not params or 'feedDocumentId' in params or any(p.kind == _inspect.Parameter.VAR_KEYWORD for p in params.values()):
        try:
            return feeds.get_feed_document(feedDocumentId=feed_document_id)
        except TypeError:
            pass
    return feeds.get_feed_document(feed_document_id)

def _amazon_get_feed_status(credentials, marketplace, feed_id):
    from sp_api.api import Feeds
    feeds = Feeds(credentials=credentials, marketplace=marketplace)
    try:
        resp = feeds.get_feed(feedId=feed_id)
    except TypeError:
        resp = feeds.get_feed(feed_id)
    payload = getattr(resp, 'payload', None) or {}
    status = payload.get('processingStatus') or payload.get('processing_status') or payload.get('status')
    result_doc = payload.get('resultFeedDocumentId') or payload.get('result_feed_document_id')
    return {
        'feed_id': feed_id,
        'status': status,
        'result_feed_document_id': result_doc,
        'raw': payload
    }

def _amazon_download_feed_document(url):
    import gzip as _gzip
    if not url:
        return None
    resp = requests.get(url, timeout=30)
    if resp.status_code != 200:
        return None
    data = resp.content or b''
    # Decompress if gzipped
    try:
        if data[:2] == b'\x1f\x8b':
            data = _gzip.decompress(data)
    except Exception:
        pass
    return data

@app.route('/api/listingagent/amazon/catalog_search', methods=['GET'])
def api_listingagent_amazon_catalog_search():
    """Search Amazon catalog by keyword/UPC (best-effort) to suggest ASINs."""
    try:
        q = (request.args.get('q') or request.args.get('upc') or '').strip()
        if not q:
            return jsonify({'success': False, 'error': 'q is required'}), 400

        limit = _listingagent_parse_int(request.args.get('limit'), 8) or 8
        limit = max(1, min(limit, 20))
        include_raw = (request.args.get('raw') or '').lower() == 'true'
        mode = (request.args.get('mode') or '').strip().lower()
        identifiers_type = (request.args.get('identifiersType') or '').strip().upper()
        q_no_space = q.replace(' ', '')

        credentials, _seller_id, marketplace_id, marketplace = _amazon_spapi_context()
        if marketplace is None:
            return jsonify({'success': False, 'error': 'Amazon SP-API not available'}), 503

        from sp_api.api import CatalogItems
        from sp_api.base.exceptions import SellingApiException

        ci = CatalogItems(credentials=credentials, marketplace=marketplace, version='2022-04-01')

        # Prefer identifier search when q looks like a UPC/GTIN (or explicitly requested).
        use_identifiers = False
        if mode in ('upc', 'gtin', 'identifier'):
            use_identifiers = True
        elif identifiers_type:
            use_identifiers = True
        elif mode in ('asin',):
            use_identifiers = True
            if not identifiers_type:
                identifiers_type = 'ASIN'
        elif q_no_space.isalnum() and len(q_no_space) == 10 and any(ch.isdigit() for ch in q_no_space) and any(ch.isalpha() for ch in q_no_space):
            # Likely ASIN (avoid treating plain words as ASIN).
            use_identifiers = True
            if not identifiers_type:
                identifiers_type = 'ASIN'
        elif q.isdigit() and 8 <= len(q) <= 14:
            use_identifiers = True

        if use_identifiers:
            # Heuristic: UPC=8/10/11/12, EAN=13, GTIN=14. Allow override via identifiersType=...
            if not identifiers_type:
                if len(q) == 13:
                    identifiers_type = 'EAN'
                elif len(q) == 14:
                    identifiers_type = 'GTIN'
                elif len(q) == 10:
                    # Commonly ISBN-10 when numeric; if this is actually UPC/other, keyword fallback will still work.
                    identifiers_type = 'ISBN'
                else:
                    identifiers_type = 'UPC'

        def _do_keywords():
            return ci.search_catalog_items(
                keywords=[q],
                marketplaceIds=[marketplace_id],
                includedData=['summaries', 'images'],
                pageSize=limit
            )

        def _do_identifiers():
            return ci.search_catalog_items(
                identifiers=[q],
                identifiersType=identifiers_type,
                marketplaceIds=[marketplace_id],
                includedData=['summaries', 'images'],
                pageSize=limit
            )

        try:
            resp = _do_identifiers() if use_identifiers else _do_keywords()
        except SellingApiException:
            # Some accounts/marketplaces/product-types can be finicky; fall back to keyword search.
            if use_identifiers:
                resp = _do_keywords()
            else:
                raise
        if resp.errors:
            return jsonify({'success': False, 'error': str(resp.errors)}), 400

        payload = resp.payload or {}
        items = payload.get('items') or []
        # If identifier search returned nothing and the caller didn't force identifier mode, try keywords.
        if use_identifiers and not items and mode not in ('upc','gtin','identifier','asin') and not (request.args.get('identifiersType') or '').strip():
            try:
                resp_kw = _do_keywords()
                if not resp_kw.errors:
                    payload = resp_kw.payload or {}
                    items = payload.get('items') or []
            except Exception:
                pass

        results = []
        for it in items[:limit]:
            asin = (it.get('asin') or '').strip()
            title = ''
            brand = ''
            summaries = it.get('summaries') or []
            if summaries:
                s0 = summaries[0] or {}
                title = s0.get('itemName') or s0.get('item_name') or ''
                brand = s0.get('brandName') or s0.get('brand_name') or ''

            image_url = ''
            for grp in (it.get('images') or []):
                imgs = grp.get('images') or []
                if imgs:
                    image_url = (imgs[0].get('link') or '').strip()
                    if image_url:
                        break

            results.append({
                'asin': asin,
                'title': title or '',
                'brand': brand or '',
                'image': image_url or ''
            })

        out = {'success': True, 'results': results}
        if include_raw:
            out['raw'] = payload
        return jsonify(out)

    except Exception as e:
        # SellingApiException string tends to be safe+useful (issues, codes, etc.)
        from sp_api.base.exceptions import SellingApiException
        if isinstance(e, SellingApiException):
            return jsonify({'success': False, 'error': str(e)}), 400
        return jsonify({'success': False, 'error': _safe_error(e, 'listingagent:amazon_catalog_search')}), 500

@app.route('/api/listingagent/amazon/catalog_item', methods=['GET'])
def api_listingagent_amazon_catalog_item():
    """Fetch catalog details for an ASIN (public product page template data)."""
    try:
        asin = (request.args.get('asin') or '').strip()
        if not asin:
            return jsonify({'success': False, 'error': 'asin is required'}), 400

        included = (request.args.get('includedData') or 'summaries,images,attributes').strip()
        included_data = [x.strip() for x in included.split(',') if x.strip()]
        include_raw = (request.args.get('raw') or '').lower() == 'true'

        credentials, _seller_id, marketplace_id, marketplace = _amazon_spapi_context()
        if marketplace is None:
            return jsonify({'success': False, 'error': 'Amazon SP-API not available'}), 503

        from sp_api.api import CatalogItems
        from sp_api.base.exceptions import SellingApiException

        ci = CatalogItems(credentials=credentials, marketplace=marketplace, version='2022-04-01')
        resp = ci.get_catalog_item(
            asin,
            marketplaceIds=[marketplace_id],
            includedData=included_data
        )
        if resp.errors:
            return jsonify({'success': False, 'error': str(resp.errors)}), 400

        payload = resp.payload or {}
        out = {'success': True, 'data': payload}
        if include_raw:
            out['raw'] = payload
        return jsonify(out)

    except Exception as e:
        from sp_api.base.exceptions import SellingApiException
        if isinstance(e, SellingApiException):
            return jsonify({'success': False, 'error': str(e)}), 400
        return jsonify({'success': False, 'error': _safe_error(e, 'listingagent:amazon_catalog_item')}), 500

@app.route('/api/listingagent/amazon/listing', methods=['GET'])
def api_listingagent_amazon_get_listing():
    """Fetch live SKU details from Amazon Listings Items API."""
    try:
        sku = (request.args.get('sku') or '').strip()
        if not sku:
            return jsonify({'success': False, 'error': 'sku is required'}), 400

        included = (request.args.get('includedData') or 'summaries,attributes,issues').strip()
        included_data = [x.strip() for x in included.split(',') if x.strip()]

        credentials, seller_id, marketplace_id, marketplace = _amazon_spapi_context()
        if marketplace is None:
            return jsonify({'success': False, 'error': 'Amazon SP-API not available'}), 503

        from sp_api.api import ListingsItems
        from sp_api.base.exceptions import SellingApiException

        li = ListingsItems(credentials=credentials, marketplace=marketplace)
        resp = li.get_listings_item(
            seller_id,
            sku,
            marketplaceIds=[marketplace_id],
            includedData=included_data
        )
        if resp.errors:
            return jsonify({'success': False, 'error': str(resp.errors)}), 400
        return jsonify({'success': True, 'data': resp.payload or {}})

    except Exception as e:
        from sp_api.base.exceptions import SellingApiException
        if isinstance(e, SellingApiException):
            return jsonify({'success': False, 'error': str(e)}), 400
        return jsonify({'success': False, 'error': _safe_error(e, 'listingagent:amazon_get_listing')}), 500

def _amazon_normalize_condition_type(raw, default_condition='used_good'):
    allowed = {
        'new_new',
        'used_like_new',
        'used_very_good',
        'used_good',
        'used_acceptable',
        'collectible_like_new',
        'collectible_very_good',
        'collectible_good',
        'collectible_acceptable',
        'refurbished_refurbished',
        'club_club',
    }
    if raw is None:
        return default_condition
    s = str(raw).strip()
    if not s:
        return default_condition
    if s.isdigit():
        code_map = {
            11: 'new_new',
            1: 'used_good',
            2: 'collectible_good',
            3: 'refurbished_refurbished',
            4: 'club_club',
        }
        return code_map.get(int(s), default_condition)

    lowered = s.lower().strip()
    label_map = {
        'new': 'new_new',
        'brand_new': 'new_new',
        'used_like_new': 'used_like_new',
        'used_very_good': 'used_very_good',
        'used_good': 'used_good',
        'used_acceptable': 'used_acceptable',
        'collectible_like_new': 'collectible_like_new',
        'collectible_very_good': 'collectible_very_good',
        'collectible_good': 'collectible_good',
        'collectible_acceptable': 'collectible_acceptable',
        'refurbished': 'refurbished_refurbished',
        'refurbished_refurbished': 'refurbished_refurbished',
        'club': 'club_club',
        'club_club': 'club_club',
    }
    cleaned = _re.sub(r'[^a-z0-9]+', '_', lowered).strip('_')
    if cleaned in label_map:
        return label_map[cleaned]
    if cleaned in allowed:
        return cleaned
    return default_condition

def _amazon_normalize_fulfillment_channel(raw, default_fc='DEFAULT'):
    if raw is None:
        return default_fc
    s = str(raw).strip().upper()
    if not s:
        return default_fc
    if s in ('DEFAULT', 'AMAZON_NA'):
        return s
    if s == 'AFN':
        return 'AMAZON_NA'
    if s == 'MFN':
        return 'DEFAULT'
    return default_fc

def _amazon_format_spapi_error(err):
    try:
        from sp_api.base.exceptions import SellingApiException
        if isinstance(err, SellingApiException):
            payload = getattr(err, 'payload', None) or getattr(err, 'errors', None)
            if payload:
                return f"{err} | payload={payload}"
    except Exception:
        pass
    return str(err)

def _amazon_feeds_error(err, step=''):
    msg = _amazon_format_spapi_error(err)
    if step:
        return f"{step}: {msg}"
    return msg

def _amazon_create_feed_document(feeds, content_type, body_bytes):
    import inspect as _inspect
    f = io.BytesIO(body_bytes)
    params = {}
    try:
        params = _inspect.signature(feeds.create_feed_document).parameters
    except Exception:
        params = {}

    # Try file-based signature (newer sp-api versions)
    if 'file' in params or any(p.kind == _inspect.Parameter.VAR_KEYWORD for p in params.values()):
        for kwargs in (
            {'file': f, 'content_type': content_type},
            {'file': f, 'contentType': content_type},
        ):
            try:
                f.seek(0)
                resp = feeds.create_feed_document(**kwargs)
                payload = getattr(resp, 'payload', None) or {}
                doc_id = payload.get('feedDocumentId') or payload.get('feed_document_id')
                url = payload.get('url')
                return doc_id, url, True
            except TypeError:
                continue
    # Fallback: legacy signature (no file upload)
    resp = feeds.create_feed_document(contentType=content_type)
    payload = getattr(resp, 'payload', None) or {}
    doc_id = payload.get('feedDocumentId') or payload.get('feed_document_id')
    url = payload.get('url')
    return doc_id, url, False

def _amazon_create_feed(feeds, *, feed_type, marketplace_ids, input_feed_document_id):
    import inspect as _inspect
    try:
        params = _inspect.signature(feeds.create_feed).parameters
    except Exception:
        params = {}

    # Newer sp-api supports keyword args
    if not params or 'feedType' in params or 'feed_type' in params or any(p.kind == _inspect.Parameter.VAR_KEYWORD for p in params.values()):
        try:
            return feeds.create_feed(feedType=feed_type, marketplaceIds=marketplace_ids, inputFeedDocumentId=input_feed_document_id)
        except TypeError:
            try:
                return feeds.create_feed(feed_type=feed_type, marketplace_ids=marketplace_ids, input_feed_document_id=input_feed_document_id)
            except Exception:
                pass

    # Legacy signature: (feed_type, input_feed_document_id, marketplace_ids)
    return feeds.create_feed(feed_type, input_feed_document_id, marketplace_ids)

def _amazon_update_price_spapi(li, seller_id, sku, mp_id, *, product_type, requirements, attrs):
    debug = {'attempts': []}

    def _record_attempt(method, body, resp=None, exc=None):
        entry = {'method': method, 'body': body}
        if resp is not None:
            entry['errors'] = getattr(resp, 'errors', None)
            try:
                payload = getattr(resp, 'payload', None) or {}
                if payload:
                    entry['payload_status'] = payload.get('status')
                    issues = payload.get('issues')
                    if issues:
                        entry['payload_issues'] = issues
            except Exception:
                pass
        if exc is not None:
            entry['exception'] = _amazon_format_spapi_error(exc)
        debug['attempts'].append(entry)

    def _payload_has_error(resp):
        try:
            payload = getattr(resp, 'payload', None) or {}
        except Exception:
            payload = {}
        if not payload:
            return False, None
        try:
            status = str(payload.get('status') or '').strip().upper()
        except Exception:
            status = ''
        issues = []
        try:
            issues = payload.get('issues') or []
        except Exception:
            issues = []
        err_issues = []
        try:
            for it in issues:
                sev = str(it.get('severity') or '').strip().upper()
                if sev == 'ERROR':
                    err_issues.append(it)
        except Exception:
            pass
        if err_issues:
            return True, f"issues={err_issues}"
        if status in ('INVALID', 'ERROR', 'REJECTED', 'FAILURE'):
            return True, f"status={status}"
        return False, None

    patch_failed = False
    if hasattr(li, 'patch_listings_item'):
        patch_body = {
            'productType': product_type,
            'patches': []
        }
        if attrs.get('purchasable_offer'):
            patch_body['patches'].append({
                'op': 'replace',
                'path': '/attributes/purchasable_offer',
                'value': attrs['purchasable_offer']
            })
        if attrs.get('condition_type'):
            patch_body['patches'].append({
                'op': 'replace',
                'path': '/attributes/condition_type',
                'value': attrs['condition_type']
            })
        if patch_body['patches']:
            try:
                resp = li.patch_listings_item(
                    seller_id,
                    sku,
                    marketplaceIds=[mp_id],
                    issueLocale='en_US',
                    body=patch_body
                )
                _record_attempt('PATCH', patch_body, resp=resp)
                if not getattr(resp, 'errors', None):
                    payload_err, payload_detail = _payload_has_error(resp)
                    if payload_err:
                        return False, debug, payload_detail
                    return True, debug, None
            except Exception as e:
                _record_attempt('PATCH', patch_body, exc=e)
                patch_failed = True

    put_body = {
        'productType': product_type,
        'requirements': requirements,
        'attributes': attrs
    }
    try:
        resp = li.put_listings_item(
            seller_id,
            sku,
            marketplaceIds=[mp_id],
            issueLocale='en_US',
            body=put_body
        )
        _record_attempt('PUT', put_body, resp=resp)
        if getattr(resp, 'errors', None):
            return False, debug, resp.errors
        payload_err, payload_detail = _payload_has_error(resp)
        if payload_err:
            return False, debug, payload_detail
        return True, debug, None
    except Exception as e:
        _record_attempt('PUT', put_body, exc=e)
        return False, debug, e

def _amazon_price_value_key(marketplace_id):
    # US marketplace expects "value". VAT marketplaces often use "value_with_tax".
    if not marketplace_id:
        return 'value'
    mp = str(marketplace_id).strip().upper()
    if mp == 'ATVPDKIKX0DER':
        return 'value'
    return 'value_with_tax'

def _amazon_offer_audience(settings=None):
    val = None
    try:
        if settings and settings.get('amazon_offer_audience'):
            val = str(settings.get('amazon_offer_audience')).strip()
    except Exception:
        val = None
    return val or 'ALL'

def _build_amazon_offer_attributes(*, upc, asin, condition_type, fulfillment_channel_code, quantity, currency, price, include_identifiers=True, marketplace_id=None, offer_audience='ALL'):
    attrs = {}

    if include_identifiers:
        if asin:
            attrs['merchant_suggested_asin'] = [{'value': asin}]

        # Optional: attach UPC to help Amazon match the catalog item (best-effort).
        if upc:
            attrs['externally_assigned_product_identifier'] = [{'value': upc}]
            attrs['externally_assigned_product_identifier_type'] = [{'value': 'UPC'}]

    if condition_type:
        entry = {'value': condition_type}
        if marketplace_id:
            entry['marketplace_id'] = marketplace_id
        attrs['condition_type'] = [entry]

    if fulfillment_channel_code:
        entry = {
            'fulfillment_channel_code': fulfillment_channel_code,
            'quantity': int(quantity or 1)
        }
        if marketplace_id:
            entry['marketplace_id'] = marketplace_id
        attrs['fulfillment_availability'] = [entry]

    if price is not None:
        value_key = _amazon_price_value_key(marketplace_id)
        offer = {
            'currency': (currency or 'USD').upper(),
            'audience': offer_audience or 'ALL',
            'our_price': [{
                'schedule': [{
                    value_key: float(price)
                }]
            }]
        }
        if marketplace_id:
            offer['marketplace_id'] = marketplace_id
        attrs['purchasable_offer'] = [{
            **offer
        }]

    return attrs

def _amazon_offer_price_attrs(currency, price, marketplace_id=None, offer_audience='ALL'):
    value_key = _amazon_price_value_key(marketplace_id)
    offer = {
        'currency': (currency or 'USD').upper(),
        'audience': offer_audience or 'ALL',
        'our_price': [{
            'schedule': [{
                value_key: float(price)
            }]
        }]
    }
    if marketplace_id:
        offer['marketplace_id'] = marketplace_id
    return {'purchasable_offer': [offer]}

@app.route('/api/listingagent/amazon/put_offer', methods=['POST'])
def api_listingagent_amazon_put_offer():
    """Create/update an Amazon offer using Listings Items API (PUT)."""
    try:
        data = request.json or {}
        dry_run = bool(data.get('dry_run', False))
        confirm = bool(data.get('confirm', False))

        if not confirm and not dry_run:
            return jsonify({'success': False, 'error': 'Confirmation required'}), 400

        settings = _listingagent_get_settings()

        upc = (data.get('upc') or '').strip()
        sku = (data.get('sku') or '').strip()
        asin = (data.get('asin') or '').strip()

        if not sku:
            return jsonify({'success': False, 'error': 'SKU is required'}), 400
        if not asin and not upc:
            return jsonify({'success': False, 'error': 'ASIN or UPC is required'}), 400

        quantity = _listingagent_parse_int(data.get('quantity'), 1) or 1
        price = _listingagent_parse_float(data.get('price'), None)
        if price is None:
            return jsonify({'success': False, 'error': 'Price is required'}), 400

        condition_type = (data.get('conditionType') or settings.get('amazon_condition_type') or 'used_good').strip()
        fulfillment_channel_code = (data.get('fulfillmentChannelCode') or settings.get('amazon_fulfillment_channel_code') or 'DEFAULT').strip()
        currency = (data.get('currency') or settings.get('amazon_currency') or 'USD').strip()
        product_type = (data.get('productType') or settings.get('amazon_product_type') or 'PRODUCT').strip()
        requirements = (data.get('requirements') or settings.get('amazon_requirements') or 'LISTING_OFFER_ONLY').strip()

        marketplace_id_override = (data.get('marketplaceId') or '').strip()

        condition_type = _amazon_normalize_condition_type(condition_type, settings.get('amazon_condition_type') or 'used_good')
        fulfillment_channel_code = _amazon_normalize_fulfillment_channel(
            fulfillment_channel_code,
            settings.get('amazon_fulfillment_channel_code') or 'DEFAULT'
        )

        attributes = _build_amazon_offer_attributes(
            upc=upc,
            asin=asin,
            condition_type=condition_type,
            fulfillment_channel_code=fulfillment_channel_code,
            quantity=quantity,
            currency=currency,
            price=price,
            marketplace_id=mp_id
        )

        body = {
            'productType': product_type,
            'requirements': requirements,
            'attributes': attributes
        }

        if dry_run:
            return jsonify({'success': True, 'dry_run': True, 'body': body})

        credentials, seller_id, marketplace_id, marketplace = _amazon_spapi_context()
        if marketplace is None:
            return jsonify({'success': False, 'error': 'Amazon SP-API not available'}), 503

        mp_id = marketplace_id_override or marketplace_id

        from sp_api.api import ListingsItems
        from sp_api.base.exceptions import SellingApiException

        li = ListingsItems(credentials=credentials, marketplace=marketplace)
        resp = li.put_listings_item(
            seller_id,
            sku,
            marketplaceIds=[mp_id],
            issueLocale='en_US',
            body=body
        )
        if resp.errors:
            return jsonify({'success': False, 'error': str(resp.errors)}), 400

        # Mark BOL as listed on Amazon so /items-to-list marketplace checkboxes stay in sync.
        if upc:
            try:
                now_iso = datetime.datetime.now().isoformat()
                like_upc = f"{upc}-%"
                with db_connection('bol.db') as conn:
                    cur = conn.cursor()
                    cur.execute('''
                        UPDATE bol_items
                        SET listed_amazon = 1, listed_amazon_date = ?
                        WHERE TRIM(upc) = ? COLLATE NOCASE
                           OR TRIM(upc) LIKE ? COLLATE NOCASE
                    ''', (now_iso, upc, like_upc))
            except Exception:
                pass
 
        # Record listing completion in listagent.db (if UPC is available)
        try:
            if upc:
                _listagent_mark_listed(
                    upc,
                    platform='amazon',
                    sku=sku,
                    asin=asin,
                    marketplace_id=mp_id,
                    price=price,
                    quantity=quantity,
                    source='listingagent'
                )
        except Exception:
            pass
 
        return jsonify({'success': True, 'data': resp.payload or {}, 'body': body})
 
    except Exception as e:
        from sp_api.base.exceptions import SellingApiException
        if isinstance(e, SellingApiException):
            return jsonify({'success': False, 'error': str(e)}), 400
        return jsonify({'success': False, 'error': _safe_error(e, 'listingagent:amazon_put_offer')}), 500

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
@cache.cached(timeout=300, query_string=True)
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
                
                # Get barcode and try to find in searchRack with flexible matching
                barcode = order['barcode']
                item_row = None

                # Helper to clean barcode for comparisons
                barcode_stripped = barcode.lstrip('0') if barcode and barcode.isdigit() else barcode

                # 1. Try exact match first
                searchrack_cur.execute('SELECT ID, QUANTITY FROM SEARCHRACK WHERE BARCODE = ?', (barcode,))
                item_row = searchrack_cur.fetchone()

                # 2. Try with zero-padding to 12 digits (UPC-A)
                if not item_row and barcode and barcode.isdigit():
                    barcode_padded = barcode.zfill(12)
                    if barcode_padded != barcode:
                        searchrack_cur.execute('SELECT ID, QUANTITY FROM SEARCHRACK WHERE BARCODE = ?', (barcode_padded,))
                        item_row = searchrack_cur.fetchone()

                # 3. Try with zero-padding to 13 digits (EAN)
                if not item_row and barcode and barcode.isdigit():
                    barcode_padded13 = barcode.zfill(13)
                    if barcode_padded13 != barcode:
                        searchrack_cur.execute('SELECT ID, QUANTITY FROM SEARCHRACK WHERE BARCODE = ?', (barcode_padded13,))
                        item_row = searchrack_cur.fetchone()

                # 4. Try stripped leading zeros
                if not item_row and barcode_stripped and barcode_stripped != barcode:
                    searchrack_cur.execute('SELECT ID, QUANTITY FROM SEARCHRACK WHERE BARCODE = ?', (barcode_stripped,))
                    item_row = searchrack_cur.fetchone()

                # 5. Try matching by stripping zeros from searchRack barcode (handles both directions)
                if not item_row and barcode_stripped and len(barcode_stripped) >= 8:
                    searchrack_cur.execute('''
                        SELECT ID, QUANTITY FROM SEARCHRACK
                        WHERE CAST(CAST(BARCODE AS INTEGER) AS TEXT) = ?
                        LIMIT 1
                    ''', (barcode_stripped,))
                    item_row = searchrack_cur.fetchone()

                if not item_row:
                    # Item not found in searchRack - DO NOT mark as processed
                    # This allows retry when barcode is enriched or item is added later
                    print(f"  ⏭️  Order {order['order_id']}: Item not in inventory yet (barcode: {barcode}) - will retry later")
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
        
        if 'listed_facebook_qty' not in cols:
            print("📘 Adding Facebook listing quantity column...")
            cur.execute("ALTER TABLE bol_items ADD COLUMN listed_facebook_qty INTEGER")
            # Backfill quantity for existing FB listings
            try:
                cur.execute('''
                    UPDATE bol_items
                    SET listed_facebook_qty = COALESCE(quantity, 1)
                    WHERE COALESCE(listed_facebook, 0) = 1
                    AND listed_facebook_qty IS NULL
                ''')
            except Exception:
                pass
        
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

@app.route('/item-prep/log')
def item_prep_log_page():
    return render_template('item_prep_log.html')

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

            # Prep log (preplog.db)
            try:
                _preplog_add_entry(
                    upc=base_upc,
                    base_upc=base_upc,
                    status='good',
                    quantity=qty,
                    note=note or None,
                    reason=None,
                    source='item-prep',
                    meta={
                        'action': 'converted_to_good' if suffixed_upc_found else 'saved_good',
                        'lot_number': selected_lot
                    },
                    dedupe=False
                )
            except Exception:
                pass

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

            # Prep log (preplog.db)
            try:
                log_note = note or ''
                try:
                    cur.execute('''
                        SELECT note
                        FROM items_prep_notes
                        WHERE upc = ? COLLATE NOCASE
                        ORDER BY created_at DESC, id DESC
                        LIMIT 1
                    ''', (suffixed_upc,))
                    nrow = cur.fetchone()
                    if nrow and nrow[0] and str(nrow[0]).strip():
                        log_note = str(nrow[0]).strip()
                except Exception:
                    pass
                _preplog_add_entry(
                    upc=suffixed_upc,
                    base_upc=base_upc,
                    status='bad',
                    quantity=1,
                    note=log_note or None,
                    reason=reason or None,
                    source='item-prep',
                    meta={'action': 'converted_to_bad', 'lot_number': selected_lot},
                    dedupe=True
                )
            except Exception:
                pass

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

        # Prep log (preplog.db) — only for BAD (we don't log unchecked).
        if status == 'bad':
            try:
                log_note = note or ''
                try:
                    cur.execute('''
                        SELECT note
                        FROM items_prep_notes
                        WHERE upc = ? COLLATE NOCASE
                        ORDER BY created_at DESC, id DESC
                        LIMIT 1
                    ''', (upc,))
                    nrow = cur.fetchone()
                    if nrow and nrow[0] and str(nrow[0]).strip():
                        log_note = str(nrow[0]).strip()
                except Exception:
                    pass
                _preplog_add_entry(
                    upc=upc,
                    base_upc=base_upc,
                    status='bad',
                    quantity=qty,
                    note=log_note or None,
                    reason=reason or None,
                    source='item-prep',
                    meta={'action': 'updated'},
                    dedupe=True
                )
            except Exception:
                pass

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

        # Update data version for cache invalidation
        try:
            update_data_version()
        except Exception:
            pass

        # Prep log (preplog.db)
        try:
            _preplog_add_entry(
                upc=base_upc,
                base_upc=base_upc,
                status='good',
                quantity=total_qty_allocated,
                note=note or None,
                reason=reason or None,
                source='item-prep',
                meta={'action': 'allocate_lots', 'lots_updated': results},
                dedupe=False
            )
        except Exception:
            pass
        
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

        # Prep log (preplog.db)
        try:
            _preplog_add_entry(
                upc=suffixed_upc,
                base_upc=base_upc,
                status='return',
                quantity=qty,
                note=None,
                reason='return',
                source='item-prep',
                meta={'action': 'create_return_entry'},
                dedupe=True
            )
        except Exception:
            pass

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

def _items_prep_undo_core(*, upc, status, qty=1, base_upc=None):
    """Shared undo logic for Item Prep actions.

    Returns: (ok: bool, error: str|None, http_status: int)
    """
    try:
        upc = (upc or '').strip()
        status = (status or '').strip().lower()
        base_upc = (base_upc or '').strip() or None

        if not upc or status not in ('good', 'bad', 'return'):
            return False, 'Missing upc or invalid status', 400

        try:
            qty = int(qty or 1)
        except Exception:
            qty = 1
        if qty < 1:
            qty = 1

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
        try:
            if status == 'good':
                # Good item undo: Move qty from good back to unchecked
                cur.execute('SELECT good_qty, unchecked_qty FROM bol_items WHERE upc = ? COLLATE NOCASE', (upc,))
                row = cur.fetchone()
                if not row:
                    return False, f'UPC {upc} not found', 404

                current_good = row[0] or 0
                current_unchecked = row[1] or 0

                if qty > current_good:
                    return False, f'Cannot undo {qty} - only {current_good} marked as good', 400

                new_good = current_good - qty
                new_unchecked = current_unchecked + qty

                cur.execute('''
                    UPDATE bol_items
                    SET good_qty = ?, unchecked_qty = ?, quantity = ?
                    WHERE upc = ? COLLATE NOCASE
                ''', (new_good, new_unchecked, new_good, upc))

                if new_good > 0:
                    cur.execute('UPDATE items_prep_status SET quantity = ? WHERE upc = ? COLLATE NOCASE', (new_good, upc))
                else:
                    cur.execute('DELETE FROM items_prep_status WHERE upc = ? COLLATE NOCASE', (upc,))

                print(f'[UNDO GOOD] {upc}: moved {qty} from good to unchecked (good: {current_good}→{new_good}, unchecked: {current_unchecked}→{new_unchecked})')

            elif status == 'bad':
                # Bad item undo: Delete suffixed entry and move qty from bad back to unchecked
                cur.execute('SELECT quantity, temporary FROM bol_items WHERE upc = ? COLLATE NOCASE', (upc,))
                row = cur.fetchone()
                if not row:
                    return False, f'UPC {upc} not found', 404

                bad_qty_for_this_entry = row[0] if row[0] else 1
                temporary = row[1]

                cur.execute('DELETE FROM bol_items WHERE upc = ? COLLATE NOCASE', (upc,))
                cur.execute('DELETE FROM items_prep_status WHERE upc = ? COLLATE NOCASE', (upc,))

                if temporary == 0:
                    cur.execute('DELETE FROM items_prep_images WHERE upc = ? COLLATE NOCASE', (upc,))
                    try:
                        cur.execute('DELETE FROM items_prep_notes WHERE upc = ? COLLATE NOCASE', (upc,))
                    except Exception:
                        pass

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

            elif status == 'return':
                # Return undo: remove the suffixed return entry (does not touch base UPC quantities)
                cur.execute('SELECT temporary FROM bol_items WHERE upc = ? COLLATE NOCASE', (upc,))
                row = cur.fetchone()
                if not row:
                    return False, f'UPC {upc} not found', 404

                temporary = row[0]
                cur.execute('DELETE FROM bol_items WHERE upc = ? COLLATE NOCASE', (upc,))
                cur.execute('DELETE FROM items_prep_status WHERE upc = ? COLLATE NOCASE', (upc,))
                if temporary == 0:
                    cur.execute('DELETE FROM items_prep_images WHERE upc = ? COLLATE NOCASE', (upc,))
                    try:
                        cur.execute('DELETE FROM items_prep_notes WHERE upc = ? COLLATE NOCASE', (upc,))
                    except Exception:
                        pass

                print(f'[UNDO RETURN] Deleted return entry {upc}')

            conn.commit()
            try:
                update_data_version()
            except Exception:
                pass
            return True, None, 200
        except Exception as e:
            try:
                conn.rollback()
            except Exception:
                pass
            return False, _safe_error(e, 'items_prep:undo_core'), 500
        finally:
            conn.close()
    except Exception as e:
        return False, _safe_error(e, 'items_prep:undo_core_outer'), 500

@app.route('/api/items_prep/undo', methods=['POST'])
def api_items_prep_undo():
    """Undo the last item prep action.
    For Good items: Decrement status qty (or delete if qty becomes 0), increment base UPC qty
    For Bad items: Delete suffixed entry from bol_items if temporary=1 (not completed yet)
                   or if temporary=0 (completed), restore base qty and delete suffixed entry
    """
    try:
        data = request.get_json() or {}
        upc = data.get('upc')
        status = data.get('status')
        base_upc = data.get('base_upc')
        qty = data.get('qty', 1)

        ok, err, code = _items_prep_undo_core(upc=upc, status=status, qty=qty, base_upc=base_upc)
        if not ok:
            return jsonify({'success': False, 'error': err or 'Undo failed'}), (code or 400)
        return jsonify({'success': True})
        
    except Exception as e:
        print(f'Undo error: {e}')
        return jsonify({'success': False, 'error': _safe_error(e)}), 500


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

@app.route('/price-master')
def price_master_page():
    """Bulk re-pricing dashboard for eBay + Amazon store listings."""
    return render_template('price_master.html')

@app.route('/label-master')
def label_master_page():
    """Label Master portal for buying/printing labels for sold orders."""
    return render_template('label.html')

@app.route('/fb-listings')
def fb_listings_page():
    return render_template('fb_listings.html')

@app.route('/store-listing-helper')
def store_listing_helper_page():
    return render_template('store_listing_helper.html')

# -----------------------------
# Label Master (Shipping Labels)
# -----------------------------

_LABELMASTER_REQUIRED_SETTINGS = [
    'label_from_name',
    'label_from_address1',
    'label_from_city',
    'label_from_state',
    'label_from_postal',
    'label_from_country',
    'label_pkg_weight_oz',
    'label_pkg_length_in',
    'label_pkg_width_in',
    'label_pkg_height_in',
]

def _labelmaster_get_settings():
    settings = _listingagent_get_settings()
    out = {k: v for k, v in (settings or {}).items() if str(k).startswith('label_')}
    if not out.get('label_from_country'):
        out['label_from_country'] = 'US'
    if not out.get('label_amazon_delivery_experience'):
        out['label_amazon_delivery_experience'] = 'NoTracking'
    if not out.get('label_amazon_carrier_pickup_option'):
        out['label_amazon_carrier_pickup_option'] = 'ShipperWillDropOff'
    if not out.get('label_ebay_label_size'):
        out['label_ebay_label_size'] = '4"x6"'
    return out

def _labelmaster_missing_settings(settings: dict):
    missing = []
    for k in _LABELMASTER_REQUIRED_SETTINGS:
        v = (settings or {}).get(k)
        if v is None or str(v).strip() == '':
            missing.append(k)
    return missing

def _labelmaster_init_label_tables(cur):
    cur.execute('''
        CREATE TABLE IF NOT EXISTS shipping_labels (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            store TEXT NOT NULL,
            order_id TEXT NOT NULL,
            label_id TEXT,
            label_url TEXT,
            label_format TEXT,
            label_path TEXT,
            cost REAL,
            currency TEXT,
            created_at TEXT,
            status TEXT,
            raw_response TEXT
        )
    ''')
    cur.execute('CREATE INDEX IF NOT EXISTS idx_shipping_labels_order ON shipping_labels(store, order_id, id)')

def _labelmaster_safe_name(s):
    try:
        return _re.sub(r'[^A-Za-z0-9._-]+', '_', str(s or '').strip()) or 'label'
    except Exception:
        return 'label'

def _labelmaster_store_label(store, order_id, *, label_id=None, label_url=None, label_format=None, label_bytes=None, cost=None, currency=None, raw=None):
    label_path = None
    if label_bytes:
        label_dir = BASE_DIR / 'debug_uploads' / 'labels' / store
        label_dir.mkdir(parents=True, exist_ok=True)
        safe_order = _labelmaster_safe_name(order_id)
        ts = datetime.datetime.utcnow().strftime('%Y%m%d_%H%M%S')
        ext = (label_format or 'pdf').lower().replace('.', '')
        if ext not in ('pdf', 'png', 'zpl'):
            ext = 'pdf'
        label_path = str(label_dir / f"{safe_order}_{ts}.{ext}")
        with open(label_path, 'wb') as f:
            f.write(label_bytes)

    with sqlite3.connect('sold.db') as conn:
        cur = conn.cursor()
        _labelmaster_init_label_tables(cur)
        cur.execute('''
            INSERT INTO shipping_labels
                (store, order_id, label_id, label_url, label_format, label_path, cost, currency, created_at, status, raw_response)
            VALUES
                (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            store,
            order_id,
            label_id,
            label_url,
            label_format,
            label_path,
            cost,
            currency,
            datetime.datetime.utcnow().isoformat() + 'Z',
            'created',
            json.dumps(raw, ensure_ascii=True, default=str) if raw is not None else None
        ))
        conn.commit()
    return label_path

def _labelmaster_latest_label(store, order_id):
    with sqlite3.connect('sold.db') as conn:
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        _labelmaster_init_label_tables(cur)
        cur.execute('''
            SELECT *
            FROM shipping_labels
            WHERE store = ? AND order_id = ?
            ORDER BY id DESC
            LIMIT 1
        ''', (store, order_id))
        row = cur.fetchone()
    return dict(row) if row else None

def _labelmaster_find_order(order_id, store):
    with sqlite3.connect('sold.db') as conn:
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        params = [store, order_id]
        q = 'SELECT * FROM orders WHERE store = ? AND order_id = ? ORDER BY id DESC LIMIT 1'
        cur.execute(q, params)
        row = cur.fetchone()
        if row:
            return dict(row)
        # Fallback to numeric id
        try:
            oid = int(order_id)
            cur.execute('SELECT * FROM orders WHERE store = ? AND id = ? ORDER BY id DESC LIMIT 1', (store, oid))
            row = cur.fetchone()
            if row:
                return dict(row)
        except Exception:
            pass
    return None

def _labelmaster_amazon_ship_from(settings):
    return {
        'Name': (settings.get('label_from_name') or '').strip(),
        'AddressLine1': (settings.get('label_from_address1') or '').strip(),
        'AddressLine2': (settings.get('label_from_address2') or '').strip(),
        'City': (settings.get('label_from_city') or '').strip(),
        'StateOrProvinceCode': (settings.get('label_from_state') or '').strip(),
        'PostalCode': (settings.get('label_from_postal') or '').strip(),
        'CountryCode': (settings.get('label_from_country') or 'US').strip(),
        'Phone': (settings.get('label_from_phone') or '').strip(),
        'Email': (settings.get('label_from_email') or '').strip(),
    }

def _labelmaster_ebay_ship_from(settings):
    addr = {
        'addressLine1': (settings.get('label_from_address1') or '').strip(),
        'addressLine2': (settings.get('label_from_address2') or '').strip(),
        'city': (settings.get('label_from_city') or '').strip(),
        'stateOrProvince': (settings.get('label_from_state') or '').strip(),
        'postalCode': (settings.get('label_from_postal') or '').strip(),
        'countryCode': (settings.get('label_from_country') or 'US').strip(),
    }
    contact = {
        'fullName': (settings.get('label_from_name') or '').strip(),
        'companyName': (settings.get('label_from_company') or '').strip(),
        'contactAddress': addr,
    }
    phone = (settings.get('label_from_phone') or '').strip()
    if phone:
        contact['primaryPhone'] = {'phoneNumber': phone}
    email = (settings.get('label_from_email') or '').strip()
    if email:
        contact['email'] = email
    return contact

def _labelmaster_package_dims(settings):
    return {
        'length': _listingagent_parse_float(settings.get('label_pkg_length_in'), None),
        'width': _listingagent_parse_float(settings.get('label_pkg_width_in'), None),
        'height': _listingagent_parse_float(settings.get('label_pkg_height_in'), None),
        'unit': 'INCH'
    }

def _labelmaster_package_weight(settings):
    return {
        'value': _listingagent_parse_float(settings.get('label_pkg_weight_oz'), None),
        'unit': 'OUNCE'
    }

def _labelmaster_validate_package(settings):
    dims = _labelmaster_package_dims(settings)
    weight = _labelmaster_package_weight(settings)
    if not dims['length'] or not dims['width'] or not dims['height']:
        return None, None, 'Package dimensions are required'
    if not weight['value']:
        return None, None, 'Package weight is required'
    return dims, weight, None

def _labelmaster_amazon_decode_label(label):
    if not label:
        return None
    data = label.get('FileContents') or label.get('LabelStream')
    if not data:
        return None
    raw = base64.b64decode(data)
    try:
        return gzip.decompress(raw)
    except Exception:
        return raw

def _labelmaster_pick_cheapest_rate(rates):
    best = None
    best_amount = None
    for r in rates or []:
        rate = r.get('Rate') or {}
        amt = rate.get('Amount')
        try:
            val = float(amt)
        except Exception:
            continue
        if best is None or val < best_amount:
            best = r
            best_amount = val
    return best

def _labelmaster_ebay_request(method, path, token, *, json_payload=None, marketplace_id='EBAY_US', timeout=30):
    url = f"https://api.ebay.com/sell/logistics/v1_beta{path}"
    headers = {
        'Authorization': f'Bearer {token}',
        'Content-Type': 'application/json',
        'X-EBAY-C-MARKETPLACE-ID': marketplace_id
    }
    resp = requests.request(method, url, headers=headers, json=json_payload, timeout=timeout)
    return resp

def _labelmaster_buy_ebay(order, settings):
    from token_manager import get_access_token
    token = get_access_token()
    if not token:
        raise Exception('Missing eBay OAuth token (run ebay_oauth_setup.py)')

    dims, weight, err = _labelmaster_validate_package(settings)
    if err:
        raise Exception(err)

    ship_to_addr = {
        'addressLine1': (order.get('shipping_street1') or '').strip(),
        'addressLine2': (order.get('shipping_street2') or '').strip(),
        'city': (order.get('shipping_city') or '').strip(),
        'stateOrProvince': (order.get('shipping_state') or '').strip(),
        'postalCode': (order.get('shipping_postal_code') or '').strip(),
        'countryCode': (order.get('shipping_country') or 'US').strip(),
    }
    if not ship_to_addr['addressLine1'] or not ship_to_addr['postalCode']:
        raise Exception('Missing ship-to address on order. Re-sync orders and try again.')

    ship_to = {
        'fullName': (order.get('shipping_name') or 'eBay Buyer').strip(),
        'contactAddress': ship_to_addr
    }

    ship_from = _labelmaster_ebay_ship_from(settings)
    if not ship_from.get('fullName') or not ship_from.get('contactAddress', {}).get('addressLine1'):
        raise Exception('Ship-from defaults are missing. Fill in Label Master defaults.')

    payload = {
        'shipFrom': ship_from,
        'shipTo': ship_to,
        'packageSpecification': {
            'dimensions': dims,
            'weight': weight
        },
        'orders': [{
            'channel': 'EBAY',
            'orderId': (order.get('order_id') or '').strip()
        }]
    }

    settings_all = _listingagent_get_settings()
    marketplace_id = (settings_all.get('ebay_marketplace_id') or 'EBAY_US').strip()

    quote_resp = _labelmaster_ebay_request('POST', '/shipping_quote', token, json_payload=payload, marketplace_id=marketplace_id)
    try:
        quote_json = quote_resp.json()
    except Exception:
        quote_json = {}

    if quote_resp.status_code >= 400:
        raise Exception(str(quote_json.get('errors') or quote_json or f"eBay error {quote_resp.status_code}"))

    quote_id = quote_json.get('shippingQuoteId')
    rates = quote_json.get('rates') or []
    if not quote_id or not rates:
        raise Exception('No shipping rates returned from eBay')

    best = None
    best_cost = None
    for r in rates:
        cost = (r.get('totalShippingCost') or r.get('baseShippingCost') or {}).get('value')
        try:
            val = float(cost)
        except Exception:
            continue
        if best is None or val < best_cost:
            best = r
            best_cost = val

    if not best:
        raise Exception('Could not select a shipping rate')

    shipment_payload = {
        'shippingQuoteId': quote_id,
        'rateId': best.get('rateId'),
        'labelSize': settings.get('label_ebay_label_size') or '4\"x6\"'
    }
    ship_resp = _labelmaster_ebay_request('POST', '/shipment', token, json_payload=shipment_payload, marketplace_id=marketplace_id)
    try:
        ship_json = ship_resp.json()
    except Exception:
        ship_json = {}

    if ship_resp.status_code >= 400:
        raise Exception(str(ship_json.get('errors') or ship_json or f"eBay error {ship_resp.status_code}"))

    label_url = ship_json.get('labelDownloadUrl') or ship_json.get('labelUrl')
    label_bytes = None
    if label_url:
        try:
            dl = requests.get(label_url, headers={'Authorization': f'Bearer {token}'}, timeout=30)
            if dl.status_code == 200:
                label_bytes = dl.content
        except Exception:
            label_bytes = None

    label_path = _labelmaster_store_label(
        'ebay',
        order.get('order_id') or '',
        label_id=ship_json.get('shipmentId'),
        label_url=label_url,
        label_format='pdf',
        label_bytes=label_bytes,
        cost=best_cost,
        currency=(best.get('totalShippingCost') or best.get('baseShippingCost') or {}).get('currency') or 'USD',
        raw={'quote': quote_json, 'shipment': ship_json}
    )

    return {
        'label_id': ship_json.get('shipmentId'),
        'label_url': label_url,
        'label_path': label_path,
        'cost': best_cost,
        'currency': (best.get('totalShippingCost') or best.get('baseShippingCost') or {}).get('currency') or 'USD',
        'format': 'pdf'
    }

def _labelmaster_buy_amazon(order, settings):
    if not AMAZON_AVAILABLE:
        raise Exception('Amazon SP-API not available')

    dims, weight, err = _labelmaster_validate_package(settings)
    if err:
        raise Exception(err)

    credentials, seller_id, marketplace_id, marketplace = _amazon_spapi_context()
    if marketplace is None:
        raise Exception('Amazon SP-API not available')

    from amazon_manager import AmazonManager
    am = AmazonManager()
    items = am.get_order_items(order.get('order_id') or '')
    if not items:
        raise Exception('Could not load Amazon order items')

    item_list = []
    for it in items:
        oid = it.get('OrderItemId')
        qty = it.get('QuantityOrdered') or it.get('Quantity') or 1
        if oid:
            item_list.append({'OrderItemId': oid, 'Quantity': int(qty)})
    if not item_list:
        raise Exception('Amazon order items missing OrderItemId')

    ship_from = _labelmaster_amazon_ship_from(settings)
    if not ship_from.get('Name') or not ship_from.get('AddressLine1'):
        raise Exception('Ship-from defaults are missing. Fill in Label Master defaults.')

    ship_date = datetime.datetime.utcnow().replace(microsecond=0).isoformat() + 'Z'
    delivery_experience = (settings.get('label_amazon_delivery_experience') or 'NoTracking').strip()
    carrier_pickup = str(settings.get('label_amazon_carrier_pickup') or '').lower() in ('1', 'true', 'yes')
    carrier_pickup_option = (settings.get('label_amazon_carrier_pickup_option') or 'ShipperWillDropOff').strip()

    service_options = {
        'DeliveryExperience': delivery_experience,
        'CarrierWillPickUp': carrier_pickup
    }
    if carrier_pickup:
        service_options['CarrierWillPickUpOption'] = carrier_pickup_option

    shipment_request = {
        'AmazonOrderId': (order.get('order_id') or '').strip(),
        'ItemList': item_list,
        'ShipFromAddress': ship_from,
        'PackageDimensions': {
            'Length': dims['length'],
            'Width': dims['width'],
            'Height': dims['height'],
            'Unit': 'inches'
        },
        'Weight': {
            'Value': weight['value'],
            'Unit': 'oz'
        },
        'ShipDate': ship_date,
        'ShippingServiceOptions': service_options
    }

    try:
        from sp_api.api import MerchantFulfillment, Tokens
    except Exception:
        from sp_api.api import MerchantFulfillment
        Tokens = None

    rdt = None
    if Tokens:
        try:
            tokens_api = Tokens(credentials=credentials, marketplace=marketplace)
            rdt_resp = tokens_api.create_restricted_data_token(restricted_resources=[{
                'method': 'POST',
                'path': '/mfn/v0/shipments',
                'dataElements': ['shippingAddress', 'buyerInfo']
            }])
            rdt = (rdt_resp.payload or {}).get('restrictedDataToken')
        except Exception:
            rdt = None

    if rdt:
        mf = MerchantFulfillment(credentials=credentials, marketplace=marketplace, restricted_data_token=rdt)
    else:
        mf = MerchantFulfillment(credentials=credentials, marketplace=marketplace)

    services_resp = mf.get_eligible_shipment_services(shipment_request_details=shipment_request)
    if getattr(services_resp, 'errors', None):
        raise Exception(str(services_resp.errors))

    services = (services_resp.payload or {}).get('ShippingServiceList') or []
    if not services:
        raise Exception('No Amazon shipping services returned')

    preferred_id = (settings.get('label_amazon_service_id') or '').strip()
    chosen = None
    if preferred_id:
        for s in services:
            if str(s.get('ShippingServiceId')) == preferred_id:
                chosen = s
                break
    if not chosen:
        chosen = _labelmaster_pick_cheapest_rate(services)

    if not chosen:
        raise Exception('Could not select an Amazon shipping service')

    service_id = chosen.get('ShippingServiceId')
    offer_id = chosen.get('ShippingServiceOfferId')

    kwargs = {}
    if offer_id:
        try:
            import inspect as _inspect
            sig = _inspect.signature(mf.create_shipment)
            if 'shipping_service_offer_id' in sig.parameters:
                kwargs['shipping_service_offer_id'] = offer_id
            elif 'ShippingServiceOfferId' in sig.parameters:
                kwargs['ShippingServiceOfferId'] = offer_id
        except Exception:
            kwargs['shipping_service_offer_id'] = offer_id

    create_resp = mf.create_shipment(
        shipment_request_details=shipment_request,
        shipping_service_id=service_id,
        **kwargs
    )
    if getattr(create_resp, 'errors', None):
        raise Exception(str(create_resp.errors))

    shipment = (create_resp.payload or {}).get('Shipment') or create_resp.payload or {}
    label = shipment.get('Label') or {}
    label_bytes = _labelmaster_amazon_decode_label(label)
    label_format = (label.get('LabelFormat') or 'PDF').lower()
    label_id = shipment.get('ShipmentId')
    label_cost = None
    label_currency = None
    rate = chosen.get('Rate') or {}
    try:
        label_cost = float(rate.get('Amount'))
        label_currency = rate.get('CurrencyCode') or 'USD'
    except Exception:
        label_cost = None

    label_path = _labelmaster_store_label(
        'amazon',
        order.get('order_id') or '',
        label_id=label_id,
        label_url=None,
        label_format=label_format,
        label_bytes=label_bytes,
        cost=label_cost,
        currency=label_currency,
        raw={'services': services_resp.payload, 'shipment': shipment}
    )

    return {
        'label_id': label_id,
        'label_url': None,
        'label_path': label_path,
        'cost': label_cost,
        'currency': label_currency or 'USD',
        'format': label_format
    }

@app.route('/api/labelmaster/settings', methods=['GET'])
def api_labelmaster_get_settings():
    try:
        return jsonify({
            'success': True,
            'settings': _labelmaster_get_settings(),
            'required': _LABELMASTER_REQUIRED_SETTINGS
        })
    except Exception as e:
        return jsonify({'success': False, 'error': _safe_error(e, 'labelmaster:get_settings')}), 500

@app.route('/api/labelmaster/settings', methods=['POST'])
def api_labelmaster_save_settings():
    try:
        payload = request.json or {}
        settings = payload.get('settings', payload)
        if not isinstance(settings, dict):
            return jsonify({'success': False, 'error': 'settings must be an object'}), 400
        filtered = {k: v for k, v in settings.items() if str(k).startswith('label_')}
        _listingagent_upsert_settings(filtered)
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': _safe_error(e, 'labelmaster:save_settings')}), 500

@app.route('/api/labelmaster/status', methods=['GET'])
def api_labelmaster_status():
    """Return a quick capability check for label buying/printing APIs."""
    settings = _labelmaster_get_settings()
    missing = _labelmaster_missing_settings(settings)

    ebay_connected = False
    ebay_reason = ''
    ebay_scope = (os.getenv('EBAY_SCOPE') or '')
    has_logistics_scope = 'sell.logistics' in ebay_scope

    tokens_path = BASE_DIR / 'tokens.json'
    if tokens_path.exists():
        try:
            with open(tokens_path, 'r', encoding='utf-8') as f:
                tokens = json.load(f)
            ebay_connected = bool(tokens.get('refresh_token'))
        except Exception:
            ebay_connected = False

    if not ebay_connected:
        ebay_reason = 'eBay OAuth token missing (run ebay_oauth_setup.py)'
    elif not has_logistics_scope:
        ebay_reason = 'Missing sell.logistics scope (reauthorize eBay app)'
    elif missing:
        ebay_reason = f"Missing defaults: {', '.join(missing)}"

    amazon_connected = False
    amazon_reason = ''
    if AMAZON_AVAILABLE:
        try:
            credentials, seller_id, marketplace_id, marketplace = _amazon_spapi_context()
            amazon_connected = marketplace is not None
            if not amazon_connected:
                amazon_reason = 'Amazon SP-API not available'
            elif missing:
                amazon_reason = f"Missing defaults: {', '.join(missing)}"
        except Exception as e:
            amazon_connected = False
            amazon_reason = _safe_error(e, 'labelmaster:amazon_status')
    else:
        amazon_reason = 'Amazon SP-API library not available'

    return jsonify({
        'success': True,
        'ebay': {
            'connected': ebay_connected,
            'supported': ebay_connected and has_logistics_scope and not missing,
            'reason': ebay_reason
        },
        'amazon': {
            'connected': amazon_connected,
            'supported': amazon_connected and not missing,
            'reason': amazon_reason
        },
        'missing_defaults': missing
    })

@app.route('/api/labelmaster/orders', methods=['GET'])
def api_labelmaster_orders():
    """Return sold orders for label purchasing/printing."""
    try:
        days = int(request.args.get('days', 7))
        store = (request.args.get('store') or '').strip().lower()
        q = (request.args.get('q') or '').strip().lower()

        params = [days]
        where = "paid_time >= date('now', '-' || ? || ' days')"
        if store in ('ebay', 'amazon'):
            where += " AND store = ?"
            params.append(store)

        with sqlite3.connect('sold.db') as conn:
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()
            _labelmaster_init_label_tables(cur)
            cur.execute(f'''
                SELECT o.*,
                       l.label_id,
                       l.label_url,
                       l.label_format,
                       l.label_path,
                       l.created_at AS label_created_at
                FROM orders o
                LEFT JOIN (
                    SELECT store, order_id, MAX(id) AS max_id
                    FROM shipping_labels
                    GROUP BY store, order_id
                ) last
                    ON last.store = o.store AND last.order_id = o.order_id
                LEFT JOIN shipping_labels l
                    ON l.id = last.max_id
                WHERE {where}
                ORDER BY paid_time DESC
                LIMIT 4000
            ''', params)
            rows = cur.fetchall()

        orders = [dict(r) for r in rows]
        for o in orders:
            o['label_ready'] = bool(o.get('label_path') or o.get('label_url'))

        if q:
            def _matches(o):
                hay = ' '.join([
                    str(o.get('order_id') or ''),
                    str(o.get('item_id') or ''),
                    str(o.get('title') or ''),
                    str(o.get('barcode') or ''),
                    str(o.get('shipping_name') or ''),
                    str(o.get('shipping_city') or ''),
                    str(o.get('shipping_state') or ''),
                    str(o.get('shipping_postal_code') or ''),
                    str(o.get('location') or ''),
                ]).lower()
                return q in hay
            orders = [o for o in orders if _matches(o)]

        return jsonify({'success': True, 'orders': orders})
    except Exception as e:
        return jsonify({'success': False, 'error': _safe_error(e, 'labelmaster:orders')}), 500

@app.route('/api/labelmaster/buy', methods=['POST'])
def api_labelmaster_buy():
    """Buy a shipping label for an order."""
    try:
        data = request.json or {}
        order_id = (data.get('order_id') or '').strip()
        store = (data.get('store') or '').strip().lower()
        if not order_id or store not in ('ebay', 'amazon'):
            return jsonify({'success': False, 'error': 'order_id and store are required'}), 400

        settings = _labelmaster_get_settings()
        missing = _labelmaster_missing_settings(settings)
        if missing:
            return jsonify({'success': False, 'error': f"Missing defaults: {', '.join(missing)}"}), 400

        order = _labelmaster_find_order(order_id, store)
        if not order:
            return jsonify({'success': False, 'error': 'Order not found'}), 404

        if store == 'ebay':
            result = _labelmaster_buy_ebay(order, settings)
        else:
            result = _labelmaster_buy_amazon(order, settings)

        label_url = result.get('label_url')
        if not label_url and result.get('label_path'):
            label_url = url_for('api_labelmaster_label_file', store=store, order_id=order.get('order_id') or order_id)

        return jsonify({
            'success': True,
            'label_url': label_url,
            'label_id': result.get('label_id'),
            'cost': result.get('cost'),
            'currency': result.get('currency'),
            'format': result.get('format')
        })
    except Exception as e:
        return jsonify({'success': False, 'error': _safe_error(e, 'labelmaster:buy')}), 500

@app.route('/api/labelmaster/print', methods=['POST'])
def api_labelmaster_print():
    """Return the label URL for printing if purchased."""
    try:
        data = request.json or {}
        order_id = (data.get('order_id') or '').strip()
        store = (data.get('store') or '').strip().lower()
        if not order_id or store not in ('ebay', 'amazon'):
            return jsonify({'success': False, 'error': 'order_id and store are required'}), 400
        order = _labelmaster_find_order(order_id, store)
        if not order:
            return jsonify({'success': False, 'error': 'Order not found'}), 404

        label = _labelmaster_latest_label(store, order.get('order_id') or order_id)
        if not label:
            return jsonify({'success': False, 'error': 'Label not purchased yet'}), 400

        if label.get('label_path'):
            label_url = url_for('api_labelmaster_label_file', store=store, order_id=order.get('order_id') or order_id)
            return jsonify({'success': True, 'label_url': label_url})

        if label.get('label_url'):
            return jsonify({'success': True, 'label_url': label.get('label_url')})

        return jsonify({'success': False, 'error': 'Label file missing'}), 404
    except Exception as e:
        return jsonify({'success': False, 'error': _safe_error(e, 'labelmaster:print')}), 500

@app.route('/api/labelmaster/label', methods=['GET'])
def api_labelmaster_label_file():
    try:
        store = (request.args.get('store') or '').strip().lower()
        order_id = (request.args.get('order_id') or '').strip()
        if not order_id or store not in ('ebay', 'amazon'):
            return jsonify({'success': False, 'error': 'order_id and store are required'}), 400

        label = _labelmaster_latest_label(store, order_id)
        if not label or not label.get('label_path'):
            return jsonify({'success': False, 'error': 'Label file not found'}), 404

        label_path = label.get('label_path')
        if not label_path or not os.path.exists(label_path):
            return jsonify({'success': False, 'error': 'Label file not found on disk'}), 404
        mime = 'application/pdf'
        ext = os.path.splitext(label_path)[1].lower()
        if ext == '.png':
            mime = 'image/png'
        elif ext == '.zpl':
            mime = 'application/octet-stream'
        return send_file(label_path, mimetype=mime)
    except Exception as e:
        return jsonify({'success': False, 'error': _safe_error(e, 'labelmaster:label_file')}), 500

# -----------------------------
# Price Master (Bulk Repricing)
# -----------------------------

def _pricemaster_now_iso():
    # UTC, second precision keeps it readable and stable for sorting.
    return datetime.datetime.utcnow().replace(microsecond=0).isoformat() + 'Z'

def _pricemaster_parse_decimal(val):
    try:
        if val is None:
            return None
        if isinstance(val, Decimal):
            return val
        if isinstance(val, (int, float)) and not isinstance(val, bool):
            return Decimal(str(val))
        s = str(val).strip()
        if s == '':
            return None
        s = s.replace('$', '').replace(',', '')
        return Decimal(s)
    except (InvalidOperation, Exception):
        return None

def _pricemaster_money_2dp(val: Decimal):
    try:
        if val is None:
            return None
        return val.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    except Exception:
        return None

def _pricemaster_compute_new_price(old_price: Decimal, mode: str, value: Decimal):
    if old_price is None or value is None:
        return None
    mode = (mode or '').strip().lower()
    if mode not in ('delta', 'percent'):
        mode = 'delta'

    try:
        if mode == 'delta':
            nxt = old_price + value
        else:
            nxt = old_price * (Decimal('1') + (value / Decimal('100')))
        if nxt < 0:
            nxt = Decimal('0')
        return _pricemaster_money_2dp(nxt)
    except Exception:
        return None

def _pricemaster_init_tables(cur):
    try:
        cur.execute('PRAGMA journal_mode=WAL')
    except Exception:
        pass

    cur.execute('''
        CREATE TABLE IF NOT EXISTS listing_meta (
            platform TEXT NOT NULL,
            listing_key TEXT NOT NULL,
            first_seen_at TEXT,
            last_price_change_at TEXT,
            last_price_change_price REAL,
            updated_at TEXT,
            PRIMARY KEY (platform, listing_key)
        )
    ''')

    cur.execute('''
        CREATE TABLE IF NOT EXISTS price_changes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            platform TEXT NOT NULL,
            listing_key TEXT NOT NULL,
            old_price REAL,
            new_price REAL,
            changed_at TEXT NOT NULL,
            adjustment_mode TEXT,
            adjustment_value REAL,
            success INTEGER NOT NULL DEFAULT 1,
            error TEXT
        )
    ''')

    cur.execute('CREATE INDEX IF NOT EXISTS idx_price_changes_key_time ON price_changes(platform, listing_key, changed_at)')

    cur.execute('''
        CREATE TABLE IF NOT EXISTS price_feed_jobs (
            feed_id TEXT PRIMARY KEY,
            marketplace_id TEXT,
            submitted_at TEXT,
            status TEXT,
            last_checked_at TEXT,
            result_document_id TEXT,
            items_json TEXT,
            error TEXT
        )
    ''')

def _pricemaster_touch_meta(rows):
    """Insert first_seen_at for listings if missing (platform, key, first_seen_at)."""
    if not rows:
        return
    now = _pricemaster_now_iso()
    with db_connection('pricemaster.db') as conn:
        cur = conn.cursor()
        _pricemaster_init_tables(cur)
        cur.executemany('''
            INSERT OR IGNORE INTO listing_meta (platform, listing_key, first_seen_at, updated_at)
            VALUES (?, ?, ?, ?)
        ''', [(p, k, fs or now, now) for (p, k, fs) in rows if p and k])

def _pricemaster_get_meta_map(platform: str, keys: list):
    """Return dict: listing_key -> meta row."""
    out = {}
    platform = (platform or '').strip().lower()
    keys = [k for k in (keys or []) if k]
    if not platform or not keys:
        return out

    # SQLite default max vars is commonly 999; batch to be safe.
    CHUNK = 900
    with db_connection('pricemaster.db') as conn:
        cur = conn.cursor()
        _pricemaster_init_tables(cur)
        for i in range(0, len(keys), CHUNK):
            batch = keys[i:i+CHUNK]
            ph = ','.join(['?'] * len(batch))
            cur.execute(f'''
                SELECT listing_key, first_seen_at, last_price_change_at, last_price_change_price
                FROM listing_meta
                WHERE platform = ? AND listing_key IN ({ph})
            ''', [platform] + batch)
            for r in cur.fetchall():
                out[r['listing_key']] = {
                    'first_seen_at': r['first_seen_at'],
                    'last_price_change_at': r['last_price_change_at'],
                    'last_price_change_price': r['last_price_change_price'],
                }
    return out

def _pricemaster_save_feed_job(feed_id, marketplace_id, items, error=None, status='SUBMITTED'):
    if not feed_id:
        return
    now = _pricemaster_now_iso()
    try:
        import json as _json
        items_json = _json.dumps(items or [], ensure_ascii=True, default=str)
    except Exception:
        items_json = '[]'
    with db_connection('pricemaster.db') as conn:
        cur = conn.cursor()
        _pricemaster_init_tables(cur)
        cur.execute('''
            INSERT INTO price_feed_jobs (feed_id, marketplace_id, submitted_at, status, last_checked_at, result_document_id, items_json, error)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(feed_id) DO UPDATE SET
                marketplace_id = excluded.marketplace_id,
                status = excluded.status,
                last_checked_at = excluded.last_checked_at,
                result_document_id = excluded.result_document_id,
                items_json = excluded.items_json,
                error = excluded.error
        ''', (feed_id, marketplace_id, now, status or 'SUBMITTED', now, None, items_json, error))

def _pricemaster_get_feed_job(feed_id):
    if not feed_id:
        return None
    with db_connection('pricemaster.db') as conn:
        cur = conn.cursor()
        _pricemaster_init_tables(cur)
        cur.execute('''
            SELECT feed_id, marketplace_id, submitted_at, status, last_checked_at, result_document_id, items_json, error
            FROM price_feed_jobs
            WHERE feed_id = ?
        ''', (feed_id,))
        row = cur.fetchone()
        return dict(row) if row else None

def _pricemaster_update_feed_job(feed_id, *, status=None, error=None, result_document_id=None):
    if not feed_id:
        return
    now = _pricemaster_now_iso()
    with db_connection('pricemaster.db') as conn:
        cur = conn.cursor()
        _pricemaster_init_tables(cur)
        cur.execute('''
            UPDATE price_feed_jobs
            SET status = COALESCE(?, status),
                last_checked_at = ?,
                result_document_id = COALESCE(?, result_document_id),
                error = COALESCE(?, error)
            WHERE feed_id = ?
        ''', (status, now, result_document_id, error, feed_id))

def _pricemaster_record_change(*, platform, listing_key, old_price, new_price, mode, value, success, error=None):
    now = _pricemaster_now_iso()
    with db_connection('pricemaster.db') as conn:
        cur = conn.cursor()
        _pricemaster_init_tables(cur)
        cur.execute('''
            INSERT INTO price_changes (platform, listing_key, old_price, new_price, changed_at, adjustment_mode, adjustment_value, success, error)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            platform, listing_key,
            None if old_price is None else float(old_price),
            None if new_price is None else float(new_price),
            now,
            mode,
            None if value is None else float(value),
            1 if success else 0,
            (str(error) if error else None)
        ))

        if success:
            cur.execute('''
                INSERT INTO listing_meta (platform, listing_key, last_price_change_at, last_price_change_price, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(platform, listing_key) DO UPDATE SET
                    last_price_change_at = excluded.last_price_change_at,
                    last_price_change_price = excluded.last_price_change_price,
                    updated_at = excluded.updated_at
            ''', (
                platform, listing_key, now,
                None if new_price is None else float(new_price),
                now
            ))

def _pricemaster_ebay_trading_call(call_name: str, xml_payload: str, timeout=30):
    token = os.getenv("EBAY_OLDAUTH_TOKEN") or ''
    if not token.strip():
        raise Exception("Missing EBAY_OLDAUTH_TOKEN (Trading API token)")

    headers = {
        "X-EBAY-API-SITEID": "0",
        "X-EBAY-API-COMPATIBILITY-LEVEL": "967",
        "X-EBAY-API-CALL-NAME": call_name,
        "X-EBAY-API-DEV-NAME": os.getenv("EBAY_PROD_DEV_ID"),
        "X-EBAY-API-APP-NAME": os.getenv("EBAY_PROD_APP_ID"),
        "X-EBAY-API-CERT-NAME": os.getenv("EBAY_PROD_CERT_ID"),
        "Content-Type": "text/xml",
    }
    resp = requests.post("https://api.ebay.com/ws/api.dll", headers=headers, data=xml_payload, timeout=timeout)
    return resp

def _pricemaster_ebay_revise_prices_bulk(updates: list, currency='USD'):
    """
    updates: list of dicts {item_id, new_price}
    Returns: (ok_item_ids_set, failed_map[item_id]=error)
    """
    updates = [u for u in (updates or []) if u.get('item_id') and u.get('new_price') is not None]
    ok = set()
    failed = {}
    if not updates:
        return ok, failed

    token = os.getenv("EBAY_OLDAUTH_TOKEN") or ''
    token = token.strip()
    if not token:
        for u in updates:
            failed[u['item_id']] = 'Missing EBAY_OLDAUTH_TOKEN'
        return ok, failed

    # Build a single ReviseInventoryStatus call for a batch.
    import html as _html
    inv_chunks = []
    for u in updates:
        item_id = str(u['item_id']).strip()
        sku = str(u.get('sku') or '').strip()
        new_price = float(u['new_price'])
        sku_xml = f"<SKU>{_html.escape(sku)}</SKU>" if sku else ""
        inv_chunks.append(f'''
          <InventoryStatus>
            <ItemID>{item_id}</ItemID>
            {sku_xml}
            <StartPrice currencyID="{currency}">{new_price:.2f}</StartPrice>
          </InventoryStatus>
        ''')

    xml_payload = f'''<?xml version="1.0" encoding="utf-8"?>
      <ReviseInventoryStatusRequest xmlns="urn:ebay:apis:eBLBaseComponents">
        <RequesterCredentials>
          <eBayAuthToken>{token}</eBayAuthToken>
        </RequesterCredentials>
        <WarningLevel>High</WarningLevel>
        {''.join(inv_chunks)}
      </ReviseInventoryStatusRequest>
    '''

    resp = _pricemaster_ebay_trading_call('ReviseInventoryStatus', xml_payload, timeout=30)
    if resp.status_code != 200:
        msg = f"HTTP {resp.status_code}"
        try:
            msg = msg + f": {resp.text[:200]}"
        except Exception:
            pass
        for u in updates:
            failed[str(u['item_id']).strip()] = msg
        return ok, failed

    # Parse response
    ns = {'ebay': 'urn:ebay:apis:eBLBaseComponents'}
    try:
        root = ET.fromstring(resp.text)
    except Exception:
        for u in updates:
            failed[str(u['item_id']).strip()] = 'Invalid XML response from eBay'
        return ok, failed

    ack = (root.findtext('.//ebay:Ack', default='', namespaces=ns) or '').strip()
    errors = root.findall('.//ebay:Errors', ns) or []

    if ack in ('Success', 'Warning', 'PartialFailure'):
        # Best-effort map errors to ItemID via ErrorParameters.
        for err in errors:
            msg = (err.findtext('ebay:LongMessage', default='', namespaces=ns) or err.findtext('ebay:ShortMessage', default='', namespaces=ns) or 'eBay error').strip()
            mapped = False
            for ep in err.findall('ebay:ErrorParameters', ns) or []:
                v = (ep.findtext('ebay:Value', default='', namespaces=ns) or '').strip()
                if v and v.isdigit() and len(v) >= 8:  # item ids are typically long digits
                    failed[v] = msg
                    mapped = True
            if not mapped and ack != 'PartialFailure' and msg:
                # If we can't map and it's not partial, treat as a general failure.
                for u in updates:
                    failed[str(u['item_id']).strip()] = msg
                return ok, failed

        # Anything not marked failed is treated as success.
        for u in updates:
            item_id = str(u['item_id']).strip()
            if item_id and item_id not in failed:
                ok.add(item_id)
        return ok, failed

    # Failure
    msg = 'eBay API error'
    if errors:
        msg = (errors[0].findtext('ebay:LongMessage', default='', namespaces=ns) or errors[0].findtext('ebay:ShortMessage', default='', namespaces=ns) or msg).strip()
    for u in updates:
        failed[str(u['item_id']).strip()] = msg
    return ok, failed

def _pricemaster_fetch_ebay_rows(item_ids: list):
    out = {}
    item_ids = [str(x).strip() for x in (item_ids or []) if str(x).strip()]
    if not item_ids:
        return out
    CHUNK = 900
    with db_connection('ebayStore.db') as conn:
        cur = conn.cursor()
        for i in range(0, len(item_ids), CHUNK):
            batch = item_ids[i:i+CHUNK]
            ph = ','.join(['?'] * len(batch))
            cur.execute(f'''
                SELECT ItemID, Title, UPC, Price, Quantity, URL, List_State, List_Date
                FROM INVENTORY
                WHERE TRIM(COALESCE(ItemID,'')) IN ({ph})
                LIMIT {len(batch)}
            ''', batch)
            for r in cur.fetchall():
                out[(r['ItemID'] or '').strip()] = dict(r)
    return out

def _pricemaster_fetch_amazon_rows(skus: list):
    out = {}
    skus = [str(x).strip() for x in (skus or []) if str(x).strip()]
    if not skus:
        return out
    CHUNK = 900
    with db_connection('amazonStore.db') as conn:
        cur = conn.cursor()
        for i in range(0, len(skus), CHUNK):
            batch = skus[i:i+CHUNK]
            ph = ','.join(['?'] * len(batch))
            cur.execute(f'''
                SELECT SKU, ASIN, UPC, TITLE, PRICE, QUANTITY, STATUS, CONDITION, FULFILLMENT_CHANNEL, LAST_UPDATED
                FROM ITEMS
                WHERE TRIM(COALESCE(SKU,'')) IN ({ph})
                LIMIT {len(batch)}
            ''', batch)
            for r in cur.fetchall():
                out[(r['SKU'] or '').strip()] = dict(r)
    return out

@app.route('/api/pricemaster/listings', methods=['GET'])
def api_pricemaster_listings():
    """Return current store listings (eBay + Amazon) with local meta (first seen + last price change)."""
    try:
        platform_param = (request.args.get('platform') or '').strip().lower()
        include_inactive = (request.args.get('include_inactive') or '').strip() in ('1', 'true', 'yes')

        want_ebay = (platform_param in ('', 'all', 'ebay'))
        want_amazon = (platform_param in ('', 'all', 'amazon'))

        items = []
        ebay_keys = []
        amazon_keys = []
        touch_rows = []

        if want_ebay:
            with db_connection('ebayStore.db') as conn:
                cur = conn.cursor()
                if include_inactive:
                    cur.execute('''
                        SELECT Title, ItemID, SKU, Price, Quantity, URL, List_State, List_Date, UPC
                        FROM INVENTORY
                        WHERE TRIM(COALESCE(ItemID,'')) != ''
                        ORDER BY ID DESC
                        LIMIT 6000
                    ''')
                else:
                    cur.execute('''
                        SELECT Title, ItemID, SKU, Price, Quantity, URL, List_State, List_Date, UPC
                        FROM INVENTORY
                        WHERE TRIM(COALESCE(ItemID,'')) != ''
                          AND (TRIM(COALESCE(List_State,'')) = 'Active')
                        ORDER BY ID DESC
                        LIMIT 6000
                    ''')
                for r in cur.fetchall():
                    item_id = (r['ItemID'] or '').strip()
                    if not item_id:
                        continue
                    price_d = _pricemaster_parse_decimal(r['Price'])
                    price = float(_pricemaster_money_2dp(price_d)) if price_d is not None else None
                    qty = _listingagent_parse_int(r['Quantity'], None)
                    sku = (r['SKU'] or '').strip()
                    if sku.lower() == 'none':
                        sku = ''
                    upc = (r['UPC'] or '').strip()
                    list_date = (r['List_Date'] or '').strip()

                    items.append({
                        'platform': 'ebay',
                        'listing_key': item_id,
                        'item_id': item_id,
                        'sku': sku,
                        'asin': '',
                        'upc': upc,
                        'title': (r['Title'] or '').strip(),
                        'price': price,
                        'currency': 'USD',
                        'quantity': qty,
                        'status': (r['List_State'] or '').strip(),
                        'url': (r['URL'] or '').strip(),
                        'list_date': list_date,
                        'last_updated': '',
                    })
                    ebay_keys.append(item_id)
                    touch_rows.append(('ebay', item_id, list_date or _pricemaster_now_iso()))

        if want_amazon:
            with db_connection('amazonStore.db') as conn:
                cur = conn.cursor()
                if include_inactive:
                    cur.execute('''
                        SELECT TITLE, SKU, ASIN, UPC, PRICE, QUANTITY, STATUS, LAST_UPDATED
                        FROM ITEMS
                        WHERE TRIM(COALESCE(SKU,'')) != ''
                        ORDER BY ID DESC
                        LIMIT 6000
                    ''')
                else:
                    cur.execute('''
                        SELECT TITLE, SKU, ASIN, UPC, PRICE, QUANTITY, STATUS, LAST_UPDATED
                        FROM ITEMS
                        WHERE TRIM(COALESCE(SKU,'')) != ''
                          AND (TRIM(COALESCE(STATUS,'')) = 'Active')
                        ORDER BY ID DESC
                        LIMIT 6000
                    ''')
                for r in cur.fetchall():
                    sku = (r['SKU'] or '').strip()
                    if not sku:
                        continue
                    asin = (r['ASIN'] or '').strip()
                    upc = (r['UPC'] or '').strip()
                    last_updated = (r['LAST_UPDATED'] or '').strip()

                    price_d = _pricemaster_parse_decimal(r['PRICE'])
                    price = float(_pricemaster_money_2dp(price_d)) if price_d is not None else None
                    qty = _listingagent_parse_int(r['QUANTITY'], None)

                    items.append({
                        'platform': 'amazon',
                        'listing_key': sku,
                        'item_id': '',
                        'sku': sku,
                        'asin': asin,
                        'upc': upc,
                        'title': (r['TITLE'] or '').strip(),
                        'price': price,
                        'currency': 'USD',
                        'quantity': qty,
                        'status': (r['STATUS'] or '').strip(),
                        'url': '',
                        'list_date': '',
                        'last_updated': last_updated,
                    })
                    amazon_keys.append(sku)
                    touch_rows.append(('amazon', sku, last_updated or _pricemaster_now_iso()))

        # Ensure meta rows exist for sorting (first_seen_at), then attach meta data.
        _pricemaster_touch_meta(touch_rows)
        ebay_meta = _pricemaster_get_meta_map('ebay', ebay_keys)
        amazon_meta = _pricemaster_get_meta_map('amazon', amazon_keys)

        for it in items:
            key = it.get('listing_key') or ''
            meta = (ebay_meta.get(key) if it.get('platform') == 'ebay' else amazon_meta.get(key)) or {}
            it['first_seen_at'] = meta.get('first_seen_at') or ''
            it['last_price_change_at'] = meta.get('last_price_change_at') or ''
            it['last_price_change_price'] = meta.get('last_price_change_price')

        return jsonify({'success': True, 'items': items})
    except Exception as e:
        return jsonify({'success': False, 'error': _safe_error(e, 'pricemaster:listings')}), 500

@app.route('/api/pricemaster/bulk_update', methods=['POST'])
def api_pricemaster_bulk_update():
    """Bulk update prices on the marketplaces and record local price-change history."""
    try:
        data = request.json or {}
        adj = data.get('adjustment') or {}
        mode = (adj.get('mode') or 'delta').strip().lower()
        if mode not in ('delta', 'percent'):
            return jsonify({'success': False, 'error': 'Invalid adjustment mode'}), 400

        value_d = _pricemaster_parse_decimal(adj.get('value'))
        if value_d is None:
            return jsonify({'success': False, 'error': 'Adjustment value is required'}), 400

        targets = data.get('targets') or []
        if not isinstance(targets, list) or len(targets) == 0:
            return jsonify({'success': False, 'error': 'No targets provided'}), 400

        dry_run = bool(data.get('dry_run', False))

        ebay_ids = []
        amazon_skus = []
        for t in targets:
            p = (t.get('platform') or '').strip().lower()
            k = (t.get('listing_key') or '').strip()
            if not p or not k:
                continue
            if p == 'ebay':
                ebay_ids.append(k)
            elif p == 'amazon':
                amazon_skus.append(k)

        ebay_rows = _pricemaster_fetch_ebay_rows(ebay_ids) if ebay_ids else {}
        amazon_rows = _pricemaster_fetch_amazon_rows(amazon_skus) if amazon_skus else {}

        results = []

        # ---- eBay (Trading API) ----
        ebay_updates = []
        for item_id in ebay_ids:
            row = ebay_rows.get(item_id)
            if not row:
                results.append({'platform': 'ebay', 'listing_key': item_id, 'success': False, 'error': 'Listing not found in ebayStore.db'})
                continue

            old_d = _pricemaster_parse_decimal(row.get('Price'))
            old_d = _pricemaster_money_2dp(old_d) if old_d is not None else None
            if old_d is None:
                results.append({'platform': 'ebay', 'listing_key': item_id, 'success': False, 'error': 'Missing current price'})
                continue

            new_d = _pricemaster_compute_new_price(old_d, mode, value_d)
            if new_d is None:
                results.append({'platform': 'ebay', 'listing_key': item_id, 'success': False, 'error': 'Could not compute new price'})
                continue

            results.append({'platform': 'ebay', 'listing_key': item_id, 'success': True, 'old_price': float(old_d), 'new_price': float(new_d), '_dry': True})
            if not dry_run:
                sku = (row.get('SKU') or '').strip()
                ebay_updates.append({'item_id': item_id, 'sku': sku, 'new_price': float(new_d), 'old_price': float(old_d)})

        if not dry_run and ebay_updates:
            # Batch into modest chunks to avoid huge Trading API payloads.
            ok_ids = set()
            fail_map = {}
            BATCH = 25
            for i in range(0, len(ebay_updates), BATCH):
                batch = ebay_updates[i:i+BATCH]
                ok, failed = _pricemaster_ebay_revise_prices_bulk(batch, currency='USD')
                ok_ids |= set(ok)
                fail_map.update(failed or {})

            # Update local DB + record history
            with db_connection('ebayStore.db') as conn:
                cur = conn.cursor()
                for u in ebay_updates:
                    item_id = str(u['item_id']).strip()
                    old_price = u.get('old_price')
                    new_price = u.get('new_price')
                    if item_id in ok_ids:
                        cur.execute('UPDATE INVENTORY SET Price = ? WHERE TRIM(COALESCE(ItemID,\'\')) = ? COLLATE NOCASE', (f"{float(new_price):.2f}", item_id))
                        _pricemaster_record_change(platform='ebay', listing_key=item_id, old_price=old_price, new_price=new_price, mode=mode, value=value_d, success=True)
                    else:
                        err = fail_map.get(item_id) or 'eBay price update failed'
                        _pricemaster_record_change(platform='ebay', listing_key=item_id, old_price=old_price, new_price=new_price, mode=mode, value=value_d, success=False, error=err)

            # Patch results list to reflect real outcomes (replace the earlier _dry placeholders)
            for r in results:
                if r.get('platform') == 'ebay' and r.get('_dry') and not dry_run:
                    item_id = r.get('listing_key')
                    if item_id in ok_ids:
                        r['success'] = True
                    else:
                        r['success'] = False
                        r['error'] = fail_map.get(item_id) or 'eBay price update failed'
                    r.pop('_dry', None)

        # ---- Amazon (SP-API) ----
        amazon_jobs = []
        for sku in amazon_skus:
            row = amazon_rows.get(sku)
            if not row:
                results.append({'platform': 'amazon', 'listing_key': sku, 'success': False, 'error': 'Listing not found in amazonStore.db'})
                continue

            old_d = _pricemaster_parse_decimal(row.get('PRICE'))
            old_d = _pricemaster_money_2dp(old_d) if old_d is not None else None
            if old_d is None:
                results.append({'platform': 'amazon', 'listing_key': sku, 'success': False, 'error': 'Missing current price'})
                continue

            new_d = _pricemaster_compute_new_price(old_d, mode, value_d)
            if new_d is None:
                results.append({'platform': 'amazon', 'listing_key': sku, 'success': False, 'error': 'Could not compute new price'})
                continue

            results.append({'platform': 'amazon', 'listing_key': sku, 'success': True, 'old_price': float(old_d), 'new_price': float(new_d), '_dry': True})
            if not dry_run:
                amazon_jobs.append({
                    'sku': sku,
                    'asin': (row.get('ASIN') or '').strip(),
                    'upc': (row.get('UPC') or '').strip(),
                    'quantity': _listingagent_parse_int(row.get('QUANTITY'), 1) or 1,
                    'condition': (row.get('CONDITION') or '').strip(),
                    'fulfillment_channel': (row.get('FULFILLMENT_CHANNEL') or '').strip(),
                    'old_price': float(old_d),
                    'new_price': float(new_d),
                })

        if not dry_run and amazon_jobs:
            settings = _listingagent_get_settings()
            credentials, seller_id, marketplace_id, marketplace = _amazon_spapi_context()
            if marketplace is None:
                # Record failures consistently
                for j in amazon_jobs:
                    _pricemaster_record_change(platform='amazon', listing_key=j['sku'], old_price=j['old_price'], new_price=j['new_price'], mode=mode, value=value_d, success=False, error='Amazon SP-API not available')
                for r in results:
                    if r.get('platform') == 'amazon' and r.get('_dry'):
                        r['success'] = False
                        r['error'] = 'Amazon SP-API not available'
                        r.pop('_dry', None)
            else:
                from sp_api.api import ListingsItems
                li = ListingsItems(credentials=credentials, marketplace=marketplace)

                mp_id = (settings.get('amazon_marketplace_id') or marketplace_id or 'ATVPDKIKX0DER').strip()
                currency = (settings.get('amazon_currency') or 'USD').strip()
                default_product_type = (settings.get('amazon_product_type') or 'PRODUCT').strip()
                requirements = (settings.get('amazon_requirements') or 'LISTING_OFFER_ONLY').strip()
                default_condition = (settings.get('amazon_condition_type') or 'used_good').strip()
                default_fc = (settings.get('amazon_fulfillment_channel_code') or 'DEFAULT').strip()
                offer_audience = _amazon_offer_audience(settings)

                ok_skus = set()
                fail_skus = {}
                product_type_cache = {}
                feed_candidates = []
                for j in amazon_jobs:
                    sku = j['sku']
                    asin = j.get('asin') or ''
                    upc = j.get('upc') or ''
                    qty = int(j.get('quantity') or 1)
                    condition_type = _amazon_normalize_condition_type(j.get('condition'), default_condition)
                    fc_code = _amazon_normalize_fulfillment_channel(j.get('fulfillment_channel'), default_fc)
                    price = float(j.get('new_price'))

                    try:
                        debug = None
                        product_type = _amazon_get_listing_product_type(li, seller_id, sku, mp_id, product_type_cache)
                        if not product_type:
                            product_type = _amazon_get_catalog_product_type(credentials, marketplace, mp_id, asin)
                        product_type = product_type or default_product_type

                        # Price-only update first (avoid touching condition/fulfillment identifiers)
                        attrs_price = _amazon_offer_price_attrs(currency, price, mp_id, offer_audience=offer_audience)
                        ok, debug, err = _amazon_update_price_spapi(
                            li,
                            seller_id,
                            sku,
                            mp_id,
                            product_type=product_type,
                            requirements=requirements,
                            attrs=attrs_price
                        )
                        if not ok:
                            # Fallback: include condition + fulfillment for strict validators
                            attrs_full = _build_amazon_offer_attributes(
                                upc=upc,
                                asin=asin,
                                condition_type=condition_type,
                                fulfillment_channel_code=fc_code,
                                quantity=qty,
                                currency=currency,
                                price=price,
                                include_identifiers=False,
                                marketplace_id=mp_id,
                                offer_audience=offer_audience
                            )
                            ok2, debug2, err2 = _amazon_update_price_spapi(
                                li,
                                seller_id,
                                sku,
                                mp_id,
                                product_type=product_type,
                                requirements=requirements,
                                attrs=attrs_full
                            )
                            if debug2:
                                try:
                                    debug['attempts'].extend(debug2.get('attempts') or [])
                                except Exception:
                                    debug = debug2
                            if not ok2:
                                raise Exception(str(err2) if err2 else str(err) if err else 'Amazon price update failed')

                        ok_skus.add(sku)
                        _pricemaster_record_change(platform='amazon', listing_key=sku, old_price=j['old_price'], new_price=j['new_price'], mode=mode, value=value_d, success=True)
                    except Exception as e:
                        msg = _amazon_format_spapi_error(e)
                        try:
                            msg = f"{msg} | product_type={product_type}"
                        except Exception:
                            pass
                        try:
                            import json as json_module
                            msg = f"{msg} | debug={json_module.dumps(debug, ensure_ascii=True, default=str)}"
                        except Exception:
                            pass
                        fail_skus[sku] = msg
                        if 'InvalidInput' in msg:
                            feed_candidates.append({
                                'sku': sku,
                                'product_type': product_type,
                                'new_price': j.get('new_price'),
                                'old_price': j.get('old_price')
                            })
                        _pricemaster_record_change(platform='amazon', listing_key=sku, old_price=j['old_price'], new_price=j['new_price'], mode=mode, value=value_d, success=False, error=msg)

                # Feed fallback for InvalidInput failures
                feed_skus = set()
                pending_skus = set()
                feed_id = None
                if feed_candidates:
                    try:
                        # Attach adjustment info for later reconciliation
                        for fc in feed_candidates:
                            if 'mode' not in fc:
                                fc['mode'] = mode
                            if 'value' not in fc:
                                fc['value'] = float(value_d) if value_d is not None else None
                        feed_id = _amazon_submit_price_feed(
                            credentials=credentials,
                            marketplace=marketplace,
                            marketplace_id=mp_id,
                            seller_id=seller_id,
                            jobs=feed_candidates,
                            currency=currency,
                            offer_audience=offer_audience
                        )
                        _pricemaster_save_feed_job(feed_id, mp_id, feed_candidates, status='SUBMITTED')
                        for j in feed_candidates:
                            feed_skus.add(j['sku'])
                            pending_skus.add(j['sku'])
                            # overwrite failure if feed submitted
                            fail_skus.pop(j['sku'], None)
                    except Exception as e:
                        feed_err = _amazon_format_spapi_error(e)
                        for j in feed_candidates:
                            fail_skus[j['sku']] = f"{fail_skus.get(j['sku'], '')} | feed_error={feed_err}"

                # Update local amazonStore.db for successes
                now_iso = _pricemaster_now_iso()
                with db_connection('amazonStore.db') as conn:
                    cur = conn.cursor()
                    for j in amazon_jobs:
                        sku = j['sku']
                        if sku in ok_skus and sku not in pending_skus:
                            cur.execute('UPDATE ITEMS SET PRICE = ?, LAST_UPDATED = ? WHERE TRIM(COALESCE(SKU,\'\')) = ? COLLATE NOCASE', (float(j['new_price']), now_iso, sku))

                for r in results:
                    if r.get('platform') == 'amazon' and r.get('_dry'):
                        sku = r.get('listing_key')
                        if sku in pending_skus:
                            r['success'] = True
                            r['pending'] = True
                            if feed_id:
                                r['note'] = f"Submitted via Amazon feed {feed_id} (processing)"
                        elif sku in ok_skus:
                            r['success'] = True
                        else:
                            r['success'] = False
                            r['error'] = fail_skus.get(sku) or 'Amazon price update failed'
                        r.pop('_dry', None)

        # Clean up any remaining placeholders
        for r in results:
            r.pop('_dry', None)

        return jsonify({'success': True, 'dry_run': dry_run, 'results': results})

    except Exception as e:
        return jsonify({'success': False, 'error': _safe_error(e, 'pricemaster:bulk_update')}), 500

@app.route('/api/pricemaster/feed_status', methods=['POST'])
def api_pricemaster_feed_status():
    """Check Amazon feed processing status and apply successful feed updates to local DB."""
    try:
        data = request.json or {}
        feed_id = (data.get('feed_id') or '').strip()
        if not feed_id:
            return jsonify({'success': False, 'error': 'feed_id is required'}), 400

        credentials, _seller_id, _marketplace_id, marketplace = _amazon_spapi_context()
        if marketplace is None:
            return jsonify({'success': False, 'error': 'Amazon SP-API not available'}), 503

        info = _amazon_get_feed_status(credentials, marketplace, feed_id)
        status = (info.get('status') or '').strip()
        result_doc_id = info.get('result_feed_document_id')

        report = None
        report_errors = []
        report_warnings = []
        if result_doc_id:
            try:
                from sp_api.api import Feeds
                feeds = Feeds(credentials=credentials, marketplace=marketplace)
                doc_resp = _amazon_get_feed_document_info(feeds, result_doc_id)
                doc_payload = getattr(doc_resp, 'payload', None) or {}
                url = doc_payload.get('url')
                raw = _amazon_download_feed_document(url)
                report = _amazon_parse_feed_report(raw)
                report_errors = report.get('errors') or []
                report_warnings = report.get('warnings') or []
            except Exception as e:
                report = {'error_count': 1, 'errors': [{'code': 'Report', 'message': _amazon_format_spapi_error(e)}]}

        # Update feed job + apply if successful
        job = _pricemaster_get_feed_job(feed_id)
        if status:
            _pricemaster_update_feed_job(feed_id, status=status, result_document_id=result_doc_id)

        applied = False
        if status and status.upper() in ('DONE', 'DONE_NO_DATA', 'DONE_SUCCESS', 'DONE_WARNING', 'SUCCESS'):
            if report and report.get('error_count', 0) == 0:
                # Apply updates to local DB + record history
                items = []
                try:
                    import json as _json
                    items = _json.loads((job or {}).get('items_json') or '[]')
                except Exception:
                    items = []

                now_iso = _pricemaster_now_iso()
                with db_connection('amazonStore.db') as conn:
                    cur = conn.cursor()
                    for it in items:
                        sku = (it.get('sku') or '').strip()
                        if not sku:
                            continue
                        new_price = it.get('new_price')
                        old_price = it.get('old_price')
                        mode = it.get('mode') or 'delta'
                        val = it.get('value')
                        try:
                            cur.execute('UPDATE ITEMS SET PRICE = ?, LAST_UPDATED = ? WHERE TRIM(COALESCE(SKU,\'\')) = ? COLLATE NOCASE', (float(new_price), now_iso, sku))
                        except Exception:
                            pass
                        try:
                            _pricemaster_record_change(platform='amazon', listing_key=sku, old_price=old_price, new_price=new_price, mode=mode, value=_pricemaster_parse_decimal(val), success=True)
                        except Exception:
                            pass
                applied = True
                _pricemaster_update_feed_job(feed_id, status=status, error=None, result_document_id=result_doc_id)
            elif report and report.get('error_count', 0) > 0:
                # Record failures
                items = []
                try:
                    import json as _json
                    items = _json.loads((job or {}).get('items_json') or '[]')
                except Exception:
                    items = []
                err_msg = report_errors[0].get('message') if report_errors else 'Feed processing error'
                for it in items:
                    sku = (it.get('sku') or '').strip()
                    if not sku:
                        continue
                    try:
                        _pricemaster_record_change(platform='amazon', listing_key=sku, old_price=it.get('old_price'), new_price=it.get('new_price'), mode=it.get('mode') or 'delta', value=_pricemaster_parse_decimal(it.get('value')), success=False, error=err_msg)
                    except Exception:
                        pass
                _pricemaster_update_feed_job(feed_id, status=status, error=err_msg, result_document_id=result_doc_id)

        return jsonify({
            'success': True,
            'feed_id': feed_id,
            'status': status,
            'result_feed_document_id': result_doc_id,
            'report': report,
            'report_errors': report_errors,
            'report_warnings': report_warnings,
            'applied': applied
        })
    except Exception as e:
        return jsonify({'success': False, 'error': _safe_error(e, 'pricemaster:feed_status')}), 500

@app.route('/api/listing-helper/scan', methods=['GET'])
def api_listing_helper_scan():
    """Scan for listing issues: no warehouse match, duplicates, qty mismatch"""
    import hashlib
    import json as json_module

    alerts = {
        'no_warehouse': [],      # Listings with UPC not in warehouse
        'cross_store': [],       # Same UPC on both stores
        'same_store_dup': [],    # Same UPC multiple times on one store
        'qty_mismatch': []       # Listing qty > warehouse qty
    }

    try:
        # Get dismissed alerts
        alerts_conn = sqlite3.connect('listing_alerts.db')
        alerts_cur = alerts_conn.cursor()
        _ensure_listing_alerts_tables()
        alerts_cur.execute('SELECT alert_type, snapshot_hash FROM dismissed_alerts')
        dismissed = {(r[0], r[1]) for r in alerts_cur.fetchall()}
        alerts_conn.close()

        # Get warehouse stock (sum qty by UPC/Barcode)
        warehouse_stock = {}
        try:
            sr_conn = sqlite3.connect('searchRack.db')
            sr_conn.row_factory = sqlite3.Row
            sr_cur = sr_conn.cursor()
            sr_cur.execute('PRAGMA table_info(SEARCHRACK)')
            sr_cols = [c[1].lower() for c in sr_cur.fetchall()]
            upc_col = 'UPC' if 'upc' in sr_cols else ('BARCODE' if 'barcode' in sr_cols else None)
            qty_col = 'QUANTITY' if 'quantity' in sr_cols else ('QTY' if 'qty' in sr_cols else None)
            if upc_col and qty_col:
                sr_cur.execute(f'''
                    SELECT {upc_col} AS UPC, SUM({qty_col}) as total_qty
                    FROM SEARCHRACK
                    WHERE {upc_col} IS NOT NULL AND {upc_col} != ""
                    GROUP BY {upc_col} COLLATE NOCASE
                ''')
                for row in sr_cur.fetchall():
                    upc = (row['UPC'] or '').strip()
                    if upc:
                        warehouse_stock[upc.lower()] = int(row['total_qty'] or 0)
            else:
                print("Error reading searchRack: missing UPC/BARCODE or QUANTITY columns")
            sr_conn.close()
        except Exception as e:
            print(f"Error reading searchRack: {e}")

        # Helper to safely get an ID from a sqlite Row
        def _row_id(row):
            if row is None:
                return None
            keys = row.keys() if hasattr(row, 'keys') else []
            if 'ID' in keys:
                return row['ID']
            if 'rowid' in keys:
                return row['rowid']
            try:
                return row[0]
            except Exception:
                return None

        def _safe_int(val, default=1):
            try:
                if val is None:
                    return default
                if isinstance(val, str):
                    s = val.strip().lower()
                    if s in ('', 'n/a', 'na', 'null'):
                        return default
                return int(float(val))
            except Exception:
                return default

        # Get eBay active listings
        ebay_listings = {}  # upc -> [{id, title, qty, image}, ...]
        try:
            eb_conn = sqlite3.connect('ebayStore.db')
            eb_conn.row_factory = sqlite3.Row
            eb_cur = eb_conn.cursor()
            eb_cur.execute('SELECT ID, ItemID, Title, UPC, Quantity, Image FROM INVENTORY WHERE UPC IS NOT NULL AND UPC != "" AND (Quantity > 0 OR Quantity IS NULL) AND List_State = "Active"')
            for row in eb_cur.fetchall():
                upc = (row['UPC'] or '').strip()
                if upc and upc.lower() not in ['null', 'n/a', 'does not apply']:
                    upc_key = upc.lower()
                    if upc_key not in ebay_listings:
                        ebay_listings[upc_key] = []
                    ebay_listings[upc_key].append({
                        'id': _row_id(row),
                        'item_id': row['ItemID'],
                        'title': row['Title'],
                        'qty': _safe_int(row['Quantity'], 1),
                        'image': row['Image'],
                        'store': 'ebay',
                        'upc': upc
                    })
            eb_conn.close()
        except Exception as e:
            print(f"Error reading ebayStore: {e}")

        # Get Amazon active listings
        amazon_listings = {}  # upc -> [{id, title, qty, image}, ...]
        try:
            am_conn = sqlite3.connect('amazonStore.db')
            am_conn.row_factory = sqlite3.Row
            am_cur = am_conn.cursor()
            am_cur.execute('SELECT ID, ASIN, TITLE, UPC, QUANTITY, IMAGE FROM ITEMS WHERE UPC IS NOT NULL AND UPC != "" AND (QUANTITY > 0 OR QUANTITY IS NULL) AND STATUS = "Active"')
            for row in am_cur.fetchall():
                upc = (row['UPC'] or '').strip()
                if upc and upc.lower() not in ['null', 'n/a', 'does not apply']:
                    upc_key = upc.lower()
                    if upc_key not in amazon_listings:
                        amazon_listings[upc_key] = []
                    amazon_listings[upc_key].append({
                        'id': _row_id(row),
                        'asin': row['ASIN'],
                        'title': row['TITLE'],
                        'qty': _safe_int(row['QUANTITY'], 1),
                        'image': row['IMAGE'],
                        'store': 'amazon',
                        'upc': upc
                    })
            am_conn.close()
        except Exception as e:
            print(f"Error reading amazonStore: {e}")

        # Helper to create hash for dismissal tracking
        def make_hash(alert_type, upc, listing_ids):
            data = f"{alert_type}:{upc.lower()}:{','.join(sorted(str(x) for x in listing_ids))}"
            return hashlib.md5(data.encode()).hexdigest()

        all_upcs = set(ebay_listings.keys()) | set(amazon_listings.keys())

        for upc_key in all_upcs:
            ebay_items = ebay_listings.get(upc_key, [])
            amazon_items = amazon_listings.get(upc_key, [])
            warehouse_qty = warehouse_stock.get(upc_key, 0)

            all_listings = ebay_items + amazon_items
            listing_ids = [f"{l['store']}:{l['id']}" for l in all_listings]

            # 1. No warehouse match
            if warehouse_qty == 0 and all_listings:
                stores_with_listing = []
                if ebay_items:
                    stores_with_listing.append('ebay')
                if amazon_items:
                    stores_with_listing.append('amazon')

                snap_hash = make_hash('no_warehouse', upc_key, listing_ids)
                if ('no_warehouse', snap_hash) not in dismissed:
                    # Red if 2 stores, yellow if 1
                    severity = 'red' if len(stores_with_listing) >= 2 else 'yellow'
                    alerts['no_warehouse'].append({
                        'upc': all_listings[0]['upc'],  # Use original case
                        'stores': stores_with_listing,
                        'listings': all_listings,
                        'severity': severity,
                        'hash': snap_hash
                    })

            # 2. Cross-store duplicate (same UPC on both stores)
            # Only show if item HAS warehouse stock (no_warehouse takes priority)
            if ebay_items and amazon_items and warehouse_qty > 0:
                snap_hash = make_hash('cross_store', upc_key, listing_ids)
                if ('cross_store', snap_hash) not in dismissed:
                    alerts['cross_store'].append({
                        'upc': all_listings[0]['upc'],
                        'ebay_listings': ebay_items,
                        'amazon_listings': amazon_items,
                        'warehouse_qty': warehouse_qty,
                        'severity': 'yellow',
                        'hash': snap_hash
                    })

            # 3. Same-store duplicate (multiple listings of same UPC on one store)
            # Only show if item HAS warehouse stock (no_warehouse takes priority)
            if warehouse_qty > 0:
                for store, items in [('ebay', ebay_items), ('amazon', amazon_items)]:
                    if len(items) > 1:
                        store_listing_ids = [f"{store}:{l['id']}" for l in items]
                        snap_hash = make_hash('same_store_dup', upc_key, store_listing_ids)
                        if ('same_store_dup', snap_hash) not in dismissed:
                            alerts['same_store_dup'].append({
                                'upc': items[0]['upc'],
                                'store': store,
                                'listings': items,
                                'count': len(items),
                                'severity': 'red',
                                'hash': snap_hash
                            })

            # 4. Quantity mismatch (total listing qty > warehouse qty)
            if warehouse_qty > 0:
                total_listing_qty = sum(l['qty'] for l in all_listings)
                if total_listing_qty > warehouse_qty:
                    snap_hash = make_hash('qty_mismatch', upc_key, listing_ids)
                    if ('qty_mismatch', snap_hash) not in dismissed:
                        alerts['qty_mismatch'].append({
                            'upc': all_listings[0]['upc'],
                            'warehouse_qty': warehouse_qty,
                            'listing_qty': total_listing_qty,
                            'listings': all_listings,
                            'overage': total_listing_qty - warehouse_qty,
                            'severity': 'red',
                            'hash': snap_hash
                        })

        # Count totals
        counts = {
            'no_warehouse': len(alerts['no_warehouse']),
            'cross_store': len(alerts['cross_store']),
            'same_store_dup': len(alerts['same_store_dup']),
            'qty_mismatch': len(alerts['qty_mismatch']),
            'total': sum(len(v) for v in alerts.values())
        }

        return jsonify({'success': True, 'alerts': alerts, 'counts': counts})

    except Exception as e:
        return jsonify({'success': False, 'error': _safe_error(e, 'listing-helper-scan')}), 500

@app.route('/api/listing-helper/dismiss', methods=['POST'])
def api_listing_helper_dismiss():
    """Dismiss an alert by storing its hash"""
    try:
        data = request.get_json() or {}
        alert_type = data.get('alert_type')
        snap_hash = data.get('hash')
        upc = data.get('upc', '')
        store = data.get('store', '')
        listing_ids = data.get('listing_ids', [])

        if not alert_type or not snap_hash:
            return jsonify({'success': False, 'error': 'alert_type and hash required'}), 400

        _ensure_listing_alerts_tables()
        conn = sqlite3.connect('listing_alerts.db')
        cur = conn.cursor()

        import json as json_module
        cur.execute('''
            INSERT OR REPLACE INTO dismissed_alerts (alert_type, upc, store, listing_ids, snapshot_hash)
            VALUES (?, ?, ?, ?, ?)
        ''', (alert_type, upc, store, json_module.dumps(listing_ids), snap_hash))
        conn.commit()
        conn.close()

        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': _safe_error(e, 'listing-helper-dismiss')}), 500

@app.route('/api/listing-helper/undismiss', methods=['POST'])
def api_listing_helper_undismiss():
    """Remove a dismissal to re-show an alert"""
    try:
        data = request.get_json() or {}
        snap_hash = data.get('hash')

        if not snap_hash:
            return jsonify({'success': False, 'error': 'hash required'}), 400

        conn = sqlite3.connect('listing_alerts.db')
        cur = conn.cursor()
        cur.execute('DELETE FROM dismissed_alerts WHERE snapshot_hash = ?', (snap_hash,))
        conn.commit()
        conn.close()

        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': _safe_error(e, 'listing-helper-undismiss')}), 500

@app.route('/api/listing-helper/dismissed', methods=['GET'])
def api_listing_helper_dismissed():
    """Get list of dismissed alerts"""
    try:
        _ensure_listing_alerts_tables()
        conn = sqlite3.connect('listing_alerts.db')
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute('SELECT * FROM dismissed_alerts ORDER BY dismissed_at DESC')
        rows = [dict(r) for r in cur.fetchall()]
        conn.close()
        return jsonify({'success': True, 'dismissed': rows})
    except Exception as e:
        return jsonify({'success': False, 'error': _safe_error(e, 'listing-helper-dismissed')}), 500

@app.route('/api/fb/listings', methods=['GET'])
def api_fb_listings():
    """Get current Facebook Marketplace listings (from bol.db list status)."""
    try:
        _ensure_bol_list_status_column()
        conn = sqlite3.connect('bol.db')
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()

        # Simple: show items currently marked as listed on Facebook
        cur.execute('''
            SELECT upc,
                   item_description AS title,
                   image_url AS image,
                   COALESCE(listed_facebook_qty, quantity, 1) AS quantity,
                   listed_facebook_date AS listed_at
            FROM bol_items
            WHERE COALESCE(listed_facebook, 0) = 1 OR COALESCE(listed_facebook_qty, 0) > 0
            ORDER BY listed_facebook_date DESC
        ''')
        listings = []
        for row in cur.fetchall():
            item = dict(row)
            item['is_active'] = 1
            listings.append(item)
        conn.close()

        # Attach warehouse availability (searchRack)
        try:
            warehouse_stock = {}
            sr_conn = sqlite3.connect('searchRack.db')
            sr_conn.row_factory = sqlite3.Row
            sr_cur = sr_conn.cursor()
            sr_cur.execute('PRAGMA table_info(SEARCHRACK)')
            sr_cols = [c[1].lower() for c in sr_cur.fetchall()]
            upc_col = 'UPC' if 'upc' in sr_cols else ('BARCODE' if 'barcode' in sr_cols else None)
            qty_col = 'QUANTITY' if 'quantity' in sr_cols else ('QTY' if 'qty' in sr_cols else None)
            if upc_col and qty_col:
                sr_cur.execute(f'''
                    SELECT {upc_col} AS UPC, SUM({qty_col}) as total_qty
                    FROM SEARCHRACK
                    WHERE {upc_col} IS NOT NULL AND {upc_col} != ""
                    GROUP BY {upc_col} COLLATE NOCASE
                ''')
                for row in sr_cur.fetchall():
                    upc = (row['UPC'] or '').strip()
                    if upc:
                        warehouse_stock[upc.lower()] = int(row['total_qty'] or 0)
            sr_conn.close()
            for item in listings:
                upc_key = (str(item.get('upc') or '').strip()).lower()
                item['warehouse_qty'] = warehouse_stock.get(upc_key, 0)
        except Exception as e:
            print(f"Error reading searchRack for fb listings: {e}")

        # Attach notes flag from items_prep_notes (bol.db)
        try:
            if listings:
                _ensure_fbstore_notes_tables()
                note_upcs = [str(i.get('upc') or '').strip() for i in listings if i.get('upc')]
                note_upcs = [u for u in note_upcs if u]
                if note_upcs:
                    with sqlite3.connect('fbstore.db') as nconn:
                        nconn.row_factory = sqlite3.Row
                        ncur = nconn.cursor()
                        placeholders = ','.join('?' for _ in note_upcs)
                        ncur.execute(f'''
                            SELECT upc, COUNT(*) as note_count
                            FROM fb_listing_notes
                            WHERE upc IN ({placeholders})
                            GROUP BY upc
                        ''', tuple(note_upcs))
                        note_map = {str(r['upc']).lower(): int(r['note_count'] or 0) for r in ncur.fetchall()}
                    for item in listings:
                        upc_key = str(item.get('upc') or '').strip().lower()
                        item['has_notes'] = note_map.get(upc_key, 0) > 0
        except Exception as e:
            print(f"Error reading notes for fb listings: {e}")

        # If bol.db didn't return anything, fall back to fbstore.db active listings
        if not listings:
            try:
                fb_conn = sqlite3.connect('fbstore.db')
                fb_conn.row_factory = sqlite3.Row
                fb_cur = fb_conn.cursor()
                fb_cur.execute('''
                    SELECT upc, title, image, quantity, listed_at
                    FROM fb_listings
                    WHERE is_active = 1
                    ORDER BY listed_at DESC
                ''')
                for row in fb_cur.fetchall():
                    item = dict(row)
                    item['is_active'] = 1
                    item['warehouse_qty'] = item.get('warehouse_qty', 0)
                    item['has_notes'] = False
                    listings.append(item)
                fb_conn.close()
            except Exception as e:
                print(f"Error reading fbstore listings: {e}")

        return jsonify({'success': True, 'listings': listings})
    except Exception as e:
        return jsonify({'success': False, 'error': _safe_error(e, 'fb-listings')}), 500

@app.route('/api/fb/listings/add', methods=['POST'])
def api_fb_listings_add():
    """Add a Facebook Marketplace listing by UPC (lookup from rawbol.db)."""
    try:
        data = request.get_json() or {}
        upc = _normalize_upc(data.get('upc'))
        if not upc:
            return jsonify({'success': False, 'error': 'Missing upc'}), 400

        _ensure_bol_list_status_column()

        # Lookup item details from rawbol.db
        raw_conn = sqlite3.connect('rawbol.db')
        raw_conn.row_factory = sqlite3.Row
        raw_cur = raw_conn.cursor()
        raw_cur.execute('''
            SELECT upc, item_description, image_url, quantity, lot_number, import_date, created_at
            FROM raw_bol_items
            WHERE upc = ? COLLATE NOCASE
            ORDER BY created_at DESC
            LIMIT 1
        ''', (upc,))
        raw_row = raw_cur.fetchone()
        raw_conn.close()

        if not raw_row:
            return jsonify({'success': False, 'error': 'UPC not found in rawbol.db'}), 404

        # Ensure item exists in bol.db
        conn = sqlite3.connect('bol.db')
        cur = conn.cursor()
        cur.execute('SELECT upc, COALESCE(listed_facebook,0), COALESCE(listed_facebook_qty,1), listed_facebook_date FROM bol_items WHERE upc = ? COLLATE NOCASE', (upc,))
        existing_row = cur.fetchone()
        exists = bool(existing_row)

        if not exists:
            cur.execute('PRAGMA table_info(bol_items)')
            cols = [c[1] for c in cur.fetchall()]
            colset = {c.lower() for c in cols}

            def _has(name):
                return name.lower() in colset

            qty_val = int(raw_row['quantity'] or 1)
            insert_cols = ['upc']
            values = [upc]

            if _has('item_description'):
                insert_cols.append('item_description')
                values.append(raw_row['item_description'])
            if _has('image_url'):
                insert_cols.append('image_url')
                values.append(raw_row['image_url'])
            if _has('lot_number'):
                insert_cols.append('lot_number')
                values.append(raw_row['lot_number'])
            if _has('bol_number'):
                insert_cols.append('bol_number')
                values.append('RAWBOL')
            if _has('import_date'):
                insert_cols.append('import_date')
                values.append(raw_row['import_date'] or datetime.datetime.now().isoformat())
            if _has('quantity'):
                insert_cols.append('quantity')
                values.append(qty_val)
            if _has('original_qty'):
                insert_cols.append('original_qty')
                values.append(qty_val)
            if _has('unchecked_qty'):
                insert_cols.append('unchecked_qty')
                values.append(qty_val)
            if _has('good_qty'):
                insert_cols.append('good_qty')
                values.append(0)
            if _has('bad_qty'):
                insert_cols.append('bad_qty')
                values.append(0)
            if _has('temporary'):
                insert_cols.append('temporary')
                values.append(0)

            placeholders = ','.join('?' for _ in insert_cols)
            cur.execute(
                f'INSERT INTO bol_items ({",".join(insert_cols)}) VALUES ({placeholders})',
                tuple(values)
            )

        # If already listed, keep existing qty and skip updating date
        prev_listed = int(existing_row[1] or 0) if existing_row else 0
        prev_qty = int(existing_row[2] or 0) if existing_row else 0
        prev_listed_date = existing_row[3] if existing_row else None

        if exists and prev_listed == 1:
            conn.commit()
            conn.close()
            return jsonify({'success': True, 'upc': upc, 'already_listed': True})

        # Mark as listed on Facebook with default qty 1 (editable later)
        cur.execute('''
            UPDATE bol_items
            SET listed_facebook = 1,
                listed_facebook_date = datetime("now"),
                listed_facebook_qty = ?
            WHERE upc = ? COLLATE NOCASE
        ''', (1, upc))

        # Update legacy list_status column
        cur.execute('''
            UPDATE bol_items
            SET list_status = CASE
                WHEN COALESCE(listed_amazon, 0) = 1
                  OR COALESCE(listed_ebay, 0) = 1
                  OR COALESCE(listed_facebook, 0) = 1
                THEN 'listed'
                ELSE NULL
            END
            WHERE upc = ? COLLATE NOCASE
        ''', (upc,))

        conn.commit()
        conn.close()
        cache.clear()
        update_data_version()
        _log_fb_listing_action(
            action='manual_add',
            upc=upc,
            delta_qty=(1 - prev_qty),
            prev_qty=prev_qty,
            new_qty=1,
            prev_listed=prev_listed,
            prev_listed_date=prev_listed_date,
            title=raw_row['item_description'] if raw_row else None
        )
        return jsonify({'success': True, 'upc': upc})
    except Exception as e:
        return jsonify({'success': False, 'error': _safe_error(e, 'fb-listings-add')}), 500

@app.route('/api/fb/listings/search_rawbol', methods=['GET'])
def api_fb_listings_search_rawbol():
    """Search rawbol.db by item_description for FB listing add flow."""
    try:
        q = (request.args.get('q') or '').strip()
        if not q:
            return jsonify({'success': False, 'error': 'Missing q'}), 400

        conn = sqlite3.connect('rawbol.db')
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute('''
            SELECT upc, item_description, image_url
            FROM raw_bol_items
            WHERE item_description LIKE ? COLLATE NOCASE
            ORDER BY created_at DESC
            LIMIT 20
        ''', (f'%{q}%',))
        results = [dict(r) for r in cur.fetchall()]
        conn.close()
        return jsonify({'success': True, 'results': results})
    except Exception as e:
        return jsonify({'success': False, 'error': _safe_error(e, 'fb-listings-search')}), 500

@app.route('/api/fb/listings/sync_sold', methods=['POST'])
def api_fb_listings_sync_sold():
    """Sync sold orders tagged as Facebook Marketplace and decrement FB listing qty."""
    try:
        _ensure_bol_list_status_column()
        _ensure_fbstore_log_tables()

        sold_conn = sqlite3.connect('sold.db')
        sold_conn.row_factory = sqlite3.Row
        sold_cur = sold_conn.cursor()
        sold_cur.execute('''
            SELECT order_id, item_id, title, quantity, paid_time, barcode, store
            FROM orders
            WHERE store IN ('marketplace', 'facebook', 'fb')
        ''')
        sold_rows = sold_cur.fetchall()
        sold_conn.close()

        if not sold_rows:
            return jsonify({'success': True, 'updated': 0, 'skipped': 0, 'processed': 0})

        fb_conn = sqlite3.connect('fbstore.db')
        fb_cur = fb_conn.cursor()

        bol_conn = sqlite3.connect('bol.db')
        bol_conn.row_factory = sqlite3.Row
        bol_cur = bol_conn.cursor()

        processed = 0
        updated = 0
        skipped = 0

        for row in sold_rows:
            upc = _normalize_upc(row['barcode'] or '')
            if not upc:
                skipped += 1
                continue

            qty = int(row['quantity'] or 1)
            order_id = row['order_id']
            item_id = row['item_id']
            paid_time = row['paid_time']

            try:
                fb_cur.execute('''
                    INSERT INTO fb_listing_sold_sync (order_id, item_id, upc, qty, paid_time)
                    VALUES (?, ?, ?, ?, ?)
                ''', (order_id, item_id, upc, qty, paid_time))
                fb_conn.commit()
            except sqlite3.IntegrityError:
                skipped += 1
                continue

            processed += 1

            bol_cur.execute('''
                SELECT COALESCE(listed_facebook,0) AS listed_facebook,
                       COALESCE(listed_facebook_qty,1) AS listed_facebook_qty,
                       listed_facebook_date,
                       item_description
                FROM bol_items
                WHERE upc = ? COLLATE NOCASE
            ''', (upc,))
            b = bol_cur.fetchone()

            if not b or int(b['listed_facebook'] or 0) != 1:
                _log_fb_listing_action(
                    action='auto_sold_missing',
                    upc=upc,
                    delta_qty=-qty,
                    prev_qty=None,
                    new_qty=None,
                    prev_listed=int(b['listed_facebook']) if b else 0,
                    prev_listed_date=b['listed_facebook_date'] if b else None,
                    order_id=order_id,
                    item_id=item_id,
                    store=row['store'],
                    title=row['title'],
                    note='Not listed in FB'
                )
                skipped += 1
                continue

            prev_qty = int(b['listed_facebook_qty'] or 1)
            new_qty = max(0, prev_qty - qty)
            note = None

            if new_qty <= 0:
                bol_cur.execute('''
                    UPDATE bol_items
                    SET listed_facebook=0, listed_facebook_date=NULL, listed_facebook_qty=0
                    WHERE upc = ? COLLATE NOCASE
                ''', (upc,))
                note = 'Auto-unlisted'
            else:
                bol_cur.execute('UPDATE bol_items SET listed_facebook_qty=? WHERE upc = ? COLLATE NOCASE', (new_qty, upc))

            bol_cur.execute('''
                UPDATE bol_items
                SET list_status = CASE
                    WHEN COALESCE(listed_amazon, 0) = 1
                      OR COALESCE(listed_ebay, 0) = 1
                      OR COALESCE(listed_facebook, 0) = 1
                    THEN 'listed'
                    ELSE NULL
                END
                WHERE upc = ? COLLATE NOCASE
            ''', (upc,))

            updated += 1

            _log_fb_listing_action(
                action='auto_sold',
                upc=upc,
                delta_qty=(0 - qty),
                prev_qty=prev_qty,
                new_qty=new_qty,
                prev_listed=1,
                prev_listed_date=b['listed_facebook_date'],
                order_id=order_id,
                item_id=item_id,
                store=row['store'],
                title=b['item_description'] or row['title'],
                note=note
            )

        bol_conn.commit()
        bol_conn.close()
        fb_conn.close()

        cache.clear()
        update_data_version()

        return jsonify({'success': True, 'updated': updated, 'skipped': skipped, 'processed': processed})
    except Exception as e:
        return jsonify({'success': False, 'error': _safe_error(e, 'fb-listings-sync')}), 500

@app.route('/api/fb/listings/log', methods=['GET'])
def api_fb_listings_log():
    """Get FB listing auto/manual change logs."""
    try:
        _ensure_fbstore_log_tables()
        limit = int(request.args.get('limit', 50))
        limit = max(1, min(200, limit))
        conn = sqlite3.connect('fbstore.db')
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute('SELECT * FROM fb_listing_log ORDER BY created_at DESC LIMIT ?', (limit,))
        rows = [dict(r) for r in cur.fetchall()]
        conn.close()
        return jsonify({'success': True, 'logs': rows})
    except Exception as e:
        return jsonify({'success': False, 'error': _safe_error(e, 'fb-listings-log')}), 500

@app.route('/api/fb/listings/log/undo', methods=['POST'])
def api_fb_listings_log_undo():
    """Undo a FB listing log entry by restoring previous state."""
    try:
        data = request.get_json() or {}
        log_id = data.get('id')
        if not log_id:
            return jsonify({'success': False, 'error': 'Missing id'}), 400

        _ensure_fbstore_log_tables()
        conn = sqlite3.connect('fbstore.db')
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute('SELECT * FROM fb_listing_log WHERE id = ?', (log_id,))
        log = cur.fetchone()
        if not log:
            conn.close()
            return jsonify({'success': False, 'error': 'Log entry not found'}), 404
        if int(log['undone'] or 0) == 1:
            conn.close()
            return jsonify({'success': True, 'already_undone': True})

        upc = log['upc']
        prev_listed = log['prev_listed']
        prev_qty = log['prev_qty']
        prev_date = log['prev_listed_date']

        if prev_listed is None:
            conn.close()
            return jsonify({'success': False, 'error': 'Cannot undo this entry'}), 400

        _ensure_bol_list_status_column()
        bol_conn = sqlite3.connect('bol.db')
        bol_cur = bol_conn.cursor()

        if int(prev_listed or 0) == 1:
            qty_val = int(prev_qty or 1)
            bol_cur.execute('''
                UPDATE bol_items
                SET listed_facebook = 1,
                    listed_facebook_date = ?,
                    listed_facebook_qty = ?
                WHERE upc = ? COLLATE NOCASE
            ''', (prev_date or datetime.datetime.now().isoformat(), qty_val, upc))
        else:
            bol_cur.execute('''
                UPDATE bol_items
                SET listed_facebook = 0,
                    listed_facebook_date = NULL,
                    listed_facebook_qty = NULL
                WHERE upc = ? COLLATE NOCASE
            ''', (upc,))

        bol_cur.execute('''
            UPDATE bol_items
            SET list_status = CASE
                WHEN COALESCE(listed_amazon, 0) = 1
                  OR COALESCE(listed_ebay, 0) = 1
                  OR COALESCE(listed_facebook, 0) = 1
                THEN 'listed'
                ELSE NULL
            END
            WHERE upc = ? COLLATE NOCASE
        ''', (upc,))

        bol_conn.commit()
        bol_conn.close()

        cur.execute('UPDATE fb_listing_log SET undone=1, undone_at=datetime("now") WHERE id = ?', (log_id,))
        conn.commit()
        conn.close()

        cache.clear()
        update_data_version()

        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': _safe_error(e, 'fb-listings-undo')}), 500

@app.route('/api/fb/listings/notes/<upc>', methods=['GET'])
def api_fb_listings_notes_get(upc):
    """Get notes for a FB listing UPC."""
    try:
        upc_n = _normalize_upc(upc)
        if not upc_n:
            return jsonify({'success': False, 'error': 'Missing upc'}), 400
        _ensure_fbstore_notes_tables()
        conn = sqlite3.connect('fbstore.db')
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute('''
            SELECT id, note, created_at, updated_at
            FROM fb_listing_notes
            WHERE upc = ? COLLATE NOCASE
            ORDER BY created_at DESC
        ''', (upc_n,))
        notes = [dict(r) for r in cur.fetchall()]
        conn.close()
        return jsonify({'success': True, 'notes': notes})
    except Exception as e:
        return jsonify({'success': False, 'error': _safe_error(e, 'fb-listings-notes-get')}), 500

@app.route('/api/fb/listings/notes', methods=['POST'])
def api_fb_listings_notes_add():
    """Add a note for a FB listing UPC."""
    try:
        data = request.get_json() or {}
        upc = _normalize_upc(data.get('upc'))
        note = (data.get('note') or '').strip()
        if not upc or not note:
            return jsonify({'success': False, 'error': 'Missing upc or note'}), 400
        _ensure_fbstore_notes_tables()
        conn = sqlite3.connect('fbstore.db')
        cur = conn.cursor()
        cur.execute('INSERT INTO fb_listing_notes (upc, note, created_at) VALUES (?,?,datetime("now"))', (upc, note))
        conn.commit()
        conn.close()
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': _safe_error(e, 'fb-listings-notes-add')}), 500

@app.route('/api/fb/listings/notes/<int:note_id>', methods=['PUT'])
def api_fb_listings_notes_update(note_id):
    """Update a FB listing note."""
    try:
        data = request.get_json() or {}
        note = (data.get('note') or '').strip()
        if not note:
            return jsonify({'success': False, 'error': 'Missing note'}), 400
        _ensure_fbstore_notes_tables()
        conn = sqlite3.connect('fbstore.db')
        cur = conn.cursor()
        cur.execute('UPDATE fb_listing_notes SET note = ?, updated_at = datetime("now") WHERE id = ?', (note, note_id))
        conn.commit()
        conn.close()
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': _safe_error(e, 'fb-listings-notes-update')}), 500

@app.route('/api/fb/listings/notes/<int:note_id>', methods=['DELETE'])
def api_fb_listings_notes_delete(note_id):
    """Delete a FB listing note."""
    try:
        _ensure_fbstore_notes_tables()
        conn = sqlite3.connect('fbstore.db')
        cur = conn.cursor()
        cur.execute('DELETE FROM fb_listing_notes WHERE id = ?', (note_id,))
        conn.commit()
        conn.close()
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': _safe_error(e, 'fb-listings-notes-delete')}), 500

@app.route('/api/fb/listings/quantity', methods=['POST'])
def api_fb_listings_quantity():
    """Update Facebook listing quantity for a UPC."""
    try:
        data = request.get_json() or {}
        upc = _normalize_upc(data.get('upc'))
        qty = int(data.get('quantity', 1))
        if not upc:
            return jsonify({'success': False, 'error': 'Missing upc'}), 400
        if qty < 1:
            return jsonify({'success': False, 'error': 'Quantity must be at least 1'}), 400

        _ensure_bol_list_status_column()
        conn = sqlite3.connect('bol.db')
        cur = conn.cursor()
        cur.execute('SELECT COALESCE(listed_facebook,0), COALESCE(listed_facebook_qty,1), listed_facebook_date FROM bol_items WHERE upc = ? COLLATE NOCASE', (upc,))
        row = cur.fetchone()
        if not row:
            conn.close()
            return jsonify({'success': False, 'error': 'UPC not found'}), 404
        if row[0] != 1:
            conn.close()
            return jsonify({'success': False, 'error': 'Item is not listed on Facebook'}), 400
        prev_qty = int(row[1] or 1)
        prev_listed_date = row[2]

        cur.execute('UPDATE bol_items SET listed_facebook_qty=? WHERE upc = ? COLLATE NOCASE', (qty, upc))
        conn.commit()
        conn.close()
        cache.clear()
        update_data_version()
        _log_fb_listing_action(
            action='manual_qty_change',
            upc=upc,
            delta_qty=(qty - prev_qty),
            prev_qty=prev_qty,
            new_qty=qty,
            prev_listed=1,
            prev_listed_date=prev_listed_date
        )
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': _safe_error(e, 'fb-listings-quantity')}), 500

@app.route('/api/fb/listings/unlist', methods=['POST'])
def api_fb_listings_unlist():
    """Unlist a Facebook Marketplace item for a UPC."""
    try:
        data = request.get_json() or {}
        upc = _normalize_upc(data.get('upc'))
        if not upc:
            return jsonify({'success': False, 'error': 'Missing upc'}), 400

        _ensure_bol_list_status_column()
        conn = sqlite3.connect('bol.db')
        cur = conn.cursor()
        cur.execute('SELECT COALESCE(listed_facebook,0), COALESCE(listed_facebook_qty,1), listed_facebook_date FROM bol_items WHERE upc = ? COLLATE NOCASE', (upc,))
        prev_row = cur.fetchone()
        prev_listed = int(prev_row[0] or 0) if prev_row else 0
        prev_qty = int(prev_row[1] or 0) if prev_row else 0
        prev_listed_date = prev_row[2] if prev_row else None
        cur.execute('UPDATE bol_items SET listed_facebook=0, listed_facebook_date=NULL, listed_facebook_qty=NULL WHERE upc = ? COLLATE NOCASE', (upc,))
        updated = cur.rowcount

        # Update legacy list_status column for backward compatibility
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
        conn.close()
        cache.clear()
        update_data_version()
        if prev_listed == 1:
            _log_fb_listing_action(
                action='manual_unlist',
                upc=upc,
                delta_qty=(0 - prev_qty),
                prev_qty=prev_qty,
                new_qty=0,
                prev_listed=prev_listed,
                prev_listed_date=prev_listed_date
            )
        return jsonify({'success': True, 'updated': updated})
    except Exception as e:
        return jsonify({'success': False, 'error': _safe_error(e, 'fb-listings-unlist')}), 500

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
        
        # Apply listed/not_listed filter (optionally by marketplace)
        listed_stores = [s.strip().lower() for s in (request.args.get('listed_stores') or '').split(',') if s.strip()]
        # Default to all stores if none specified
        if not listed_stores:
            listed_stores = ['amazon', 'ebay', 'facebook']

        if listed_flag:
            if listed_stores:
                store_clauses = [f"COALESCE(b.listed_{s}, 0) = 1" for s in listed_stores]
                where.append(f"({' OR '.join(store_clauses)})")
            else:
                where.append("(b.list_status = 'listed')")
        elif not_listed_flag:
            if listed_stores:
                store_clauses = [f"COALESCE(b.listed_{s}, 0) = 0" for s in listed_stores]
                where.append(f"({' AND '.join(store_clauses)})")
            else:
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
    JSON: { upc, marketplace, listed, quantity (for facebook) } where marketplace in ['amazon', 'ebay', 'facebook'] and listed is boolean"""
    try:
        data = request.get_json() or {}
        upc = _normalize_upc(data.get('upc'))
        marketplace = (data.get('marketplace') or '').strip().lower()
        listed = bool(data.get('listed', True))
        quantity = int(data.get('quantity', 1)) if data.get('quantity') else 1

        print(f"[list_status] UPC: {upc}, Marketplace: {marketplace}, Listed: {listed}, Qty: {quantity}")

        if not upc:
            return jsonify({'success': False, 'error': 'Missing upc'}), 400
        if marketplace not in ('amazon', 'ebay', 'facebook'):
            return jsonify({'success': False, 'error': 'Invalid marketplace. Must be amazon, ebay, or facebook'}), 400

        # Ensure columns exist before trying to update
        _ensure_bol_list_status_column()

        conn = sqlite3.connect('bol.db')
        cur = conn.cursor()
        cur.execute('SELECT COALESCE(listed_facebook,0), COALESCE(listed_facebook_qty,1), listed_facebook_date FROM bol_items WHERE upc = ? COLLATE NOCASE', (upc,))
        prev_fb = cur.fetchone()
        prev_fb_listed = int(prev_fb[0] or 0) if prev_fb else 0
        prev_fb_qty = int(prev_fb[1] or 1) if prev_fb else 1
        prev_fb_date = prev_fb[2] if prev_fb else None

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
                cur.execute('UPDATE bol_items SET listed_facebook=1, listed_facebook_date=datetime("now"), listed_facebook_qty=? WHERE upc = ? COLLATE NOCASE', (quantity, upc))
                # Track in fbstore.db
                _track_fb_listing(upc, quantity, listed=True)
                action = 'manual_add' if prev_fb_listed == 0 else ('manual_qty_change' if prev_fb_qty != quantity else 'manual_add')
                _log_fb_listing_action(
                    action=action,
                    upc=upc,
                    delta_qty=(quantity - prev_fb_qty),
                    prev_qty=prev_fb_qty,
                    new_qty=quantity,
                    prev_listed=prev_fb_listed,
                    prev_listed_date=prev_fb_date,
                    note='list_status api'
                )
            else:
                cur.execute('UPDATE bol_items SET listed_facebook=0, listed_facebook_date=NULL, listed_facebook_qty=NULL WHERE upc = ? COLLATE NOCASE', (upc,))
                # Mark as unlisted in fbstore.db
                _track_fb_listing(upc, quantity, listed=False)
                _log_fb_listing_action(
                    action='manual_unlist',
                    upc=upc,
                    delta_qty=(0 - prev_fb_qty),
                    prev_qty=prev_fb_qty,
                    new_qty=0,
                    prev_listed=prev_fb_listed,
                    prev_listed_date=prev_fb_date,
                    note='list_status api'
                )
        
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
            # Try to refresh and reload tokens
            get_access_token()
            tokens = load_tokens()  # Reload after refresh to get new access_token
        # Quick validation: call a lightweight eBay endpoint matching our scopes
        test_headers = {
            "Authorization": f"Bearer {tokens.get('access_token', '')}",
            "Content-Type": "application/json"
        }
        r = requests.get("https://api.ebay.com/sell/fulfillment/v1/order?limit=1", headers=test_headers, timeout=5)
        if r.status_code == 401:
            status["ebay"] = "expired"
    except requests.exceptions.RequestException as e:
        # Network/timeout error - don't flag as expired, could be transient
        print(f"eBay token check network error (transient): {e}")
    except Exception as e:
        # Only flag as expired for auth-related errors
        err_str = str(e).lower()
        if "unauthorized" in err_str or "invalid" in err_str or "expired" in err_str:
            status["ebay"] = "expired"
        else:
            print(f"eBay token check error (transient): {e}")

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


@app.route("/movelocation")
def movelocation_page():
    response = make_response(render_template("movelocation.html"))
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

@app.route('/api/sold/repair-missing-removals', methods=['POST'])
def repair_missing_removals():
    """
    Repair orders marked as rackupdated=1 but have no removal record.
    Checks inventory and properly removes items that are still in stock.
    """
    from datetime import datetime

    try:
        results = {
            'total_checked': 0,
            'removed_from_inventory': 0,
            'already_removed_or_no_stock': 0,
            'no_barcode': 0,
            'reset_for_retry': 0,
            'details': []
        }

        # Connect to databases
        sold_conn = sqlite3.connect('sold.db')
        sold_conn.row_factory = sqlite3.Row
        sold_cur = sold_conn.cursor()

        hist_conn = sqlite3.connect('rackhistory.db')
        hist_conn.row_factory = sqlite3.Row
        hist_cur = hist_conn.cursor()
        _ensure_removed_items_table(hist_cur)

        rack_conn = sqlite3.connect('searchRack.db')
        rack_conn.row_factory = sqlite3.Row
        rack_cur = rack_conn.cursor()

        # Find all orders with rackupdated=1 and a barcode
        sold_cur.execute('''
            SELECT id, order_id, barcode, quantity, title, shipped_time
            FROM orders
            WHERE rackupdated = 1
            AND barcode IS NOT NULL AND barcode != ''
            AND COALESCE(removal_cancelled, 0) = 0
        ''')
        orders = sold_cur.fetchall()

        for order in orders:
            results['total_checked'] += 1
            barcode = order['barcode']
            order_id = order['order_id']
            sold_qty = order['quantity'] or 1

            # Check if this order already has a removal record
            hist_cur.execute('''
                SELECT COUNT(*) FROM removed_items
                WHERE (order_id = ? OR barcode = ?)
                AND removal_type IN ('automatic', 'repair_removal', 'manual_sold_removal', 'manual_immediate', 'manual_sold_selection', 'finder_removal')
                AND old_quantity > new_quantity
            ''', (order_id, barcode))
            has_removal = hist_cur.fetchone()[0] > 0

            if has_removal:
                # Already has removal record - skip
                results['already_removed_or_no_stock'] += 1
                continue

            # Try to find item in searchRack with flexible matching
            barcode_stripped = barcode.lstrip('0') if barcode.isdigit() else barcode
            item_row = None

            # Try exact match
            rack_cur.execute('SELECT ID, QUANTITY, TITLE, ITEM_POSITION FROM SEARCHRACK WHERE BARCODE = ?', (barcode,))
            item_row = rack_cur.fetchone()

            # Try padded to 12
            if not item_row and barcode.isdigit():
                rack_cur.execute('SELECT ID, QUANTITY, TITLE, ITEM_POSITION FROM SEARCHRACK WHERE BARCODE = ?', (barcode.zfill(12),))
                item_row = rack_cur.fetchone()

            # Try padded to 13
            if not item_row and barcode.isdigit():
                rack_cur.execute('SELECT ID, QUANTITY, TITLE, ITEM_POSITION FROM SEARCHRACK WHERE BARCODE = ?', (barcode.zfill(13),))
                item_row = rack_cur.fetchone()

            # Try stripped zeros
            if not item_row and barcode_stripped != barcode:
                rack_cur.execute('SELECT ID, QUANTITY, TITLE, ITEM_POSITION FROM SEARCHRACK WHERE BARCODE = ?', (barcode_stripped,))
                item_row = rack_cur.fetchone()

            # Try integer comparison
            if not item_row and barcode_stripped and len(barcode_stripped) >= 8:
                rack_cur.execute('''
                    SELECT ID, QUANTITY, TITLE, ITEM_POSITION FROM SEARCHRACK
                    WHERE CAST(CAST(BARCODE AS INTEGER) AS TEXT) = ?
                    LIMIT 1
                ''', (barcode_stripped,))
                item_row = rack_cur.fetchone()

            if not item_row:
                # Item not in inventory - reset rackupdated so it can retry later if item is added
                sold_cur.execute('UPDATE orders SET rackupdated = 0 WHERE id = ?', (order['id'],))
                results['reset_for_retry'] += 1
                results['details'].append({
                    'order_id': order_id,
                    'barcode': barcode,
                    'action': 'reset_for_retry',
                    'reason': 'Item not in searchRack'
                })
                continue

            # Item found - check quantity and remove
            item_id = item_row['ID']
            current_qty = item_row['QUANTITY'] or 0
            item_title = item_row['TITLE'] or order['title'] or ''
            item_location = item_row['ITEM_POSITION'] or ''

            if current_qty <= 0:
                # Already at zero quantity
                results['already_removed_or_no_stock'] += 1
                results['details'].append({
                    'order_id': order_id,
                    'barcode': barcode,
                    'action': 'skipped',
                    'reason': f'Already at 0 quantity (searchRack ID: {item_id})'
                })
                continue

            # Perform the removal
            new_qty = max(0, current_qty - sold_qty)
            rack_cur.execute('UPDATE SEARCHRACK SET QUANTITY = ? WHERE ID = ?', (new_qty, item_id))

            # Log to rackhistory
            hist_cur.execute('''
                INSERT INTO removed_items
                (order_id, barcode, title, quantity_removed, removed_at, searchrack_id, old_quantity, new_quantity, removal_type, item_position)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (order_id, barcode, item_title, sold_qty, datetime.now().isoformat(), item_id, current_qty, new_qty, 'repair_removal', item_location))

            results['removed_from_inventory'] += 1
            results['details'].append({
                'order_id': order_id,
                'barcode': barcode,
                'action': 'removed',
                'old_qty': current_qty,
                'new_qty': new_qty,
                'location': item_location
            })

        # Commit all changes
        sold_conn.commit()
        rack_conn.commit()
        hist_conn.commit()

        sold_conn.close()
        rack_conn.close()
        hist_conn.close()

        return jsonify({
            'success': True,
            'results': results
        })

    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': _safe_error(e)}), 500

@app.route('/api/sold/removal-history/<int:order_id>', methods=['GET'])
def get_sold_removal_history(order_id):
    """Get removal history from rackhistory.db for a specific sold order"""
    try:
        # First get the order details from sold.db
        sold_conn = sqlite3.connect('sold.db')
        sold_conn.row_factory = sqlite3.Row
        sold_cur = sold_conn.cursor()
        sold_cur.execute('SELECT * FROM orders WHERE id = ?', (order_id,))
        order = sold_cur.fetchone()
        sold_conn.close()

        if not order:
            return jsonify({'success': False, 'error': 'Order not found'}), 404

        order_dict = dict(order)
        order_id_str = order_dict.get('order_id') or ''
        barcode = order_dict.get('barcode') or ''

        # Get removal history from rackhistory.db
        history_conn = sqlite3.connect('rackhistory.db')
        history_conn.row_factory = sqlite3.Row
        history_cur = history_conn.cursor()

        # Search by order_id or barcode
        history_cur.execute('''
            SELECT * FROM removed_items
            WHERE order_id = ? OR (barcode = ? AND barcode != '')
            ORDER BY removed_at DESC
        ''', (order_id_str, barcode))
        history_rows = [dict(r) for r in history_cur.fetchall()]
        history_conn.close()

        return jsonify({
            'success': True,
            'order': order_dict,
            'history': history_rows
        })
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': _safe_error(e)}), 500

@app.route('/api/sold/remove-now/<int:order_id>', methods=['POST'])
def remove_sold_now(order_id):
    """Immediately remove inventory for a sold order (skip grace period)"""
    try:
        from datetime import datetime

        sold_conn = sqlite3.connect('sold.db')
        sold_conn.row_factory = sqlite3.Row
        sold_cur = sold_conn.cursor()

        # Get order details
        sold_cur.execute('SELECT * FROM orders WHERE id = ?', (order_id,))
        order = sold_cur.fetchone()

        if not order:
            sold_conn.close()
            return jsonify({'success': False, 'error': 'Order not found'}), 404

        if order['rackupdated'] == 1:
            sold_conn.close()
            return jsonify({'success': False, 'error': 'Already removed from inventory'}), 400

        barcode = order['barcode']
        if not barcode:
            sold_conn.close()
            return jsonify({'success': False, 'error': 'No barcode on order'}), 400

        sold_qty = order['quantity'] or 1

        # Search searchRack for matching item
        searchrack_conn = sqlite3.connect('searchRack.db')
        searchrack_cur = searchrack_conn.cursor()

        # Try exact match first, then stripped zeros
        searchrack_cur.execute('SELECT ID, QUANTITY, ITEM_POSITION, TITLE FROM SEARCHRACK WHERE BARCODE = ? COLLATE NOCASE', (barcode,))
        row = searchrack_cur.fetchone()

        if not row and barcode.isdigit():
            barcode_stripped = barcode.lstrip('0')
            searchrack_cur.execute('SELECT ID, QUANTITY, ITEM_POSITION, TITLE FROM SEARCHRACK WHERE BARCODE = ? COLLATE NOCASE', (barcode_stripped,))
            row = searchrack_cur.fetchone()

        if not row and barcode.isdigit():
            barcode_padded = barcode.zfill(12)
            searchrack_cur.execute('SELECT ID, QUANTITY, ITEM_POSITION, TITLE FROM SEARCHRACK WHERE BARCODE = ? COLLATE NOCASE', (barcode_padded,))
            row = searchrack_cur.fetchone()

        inventory_found = row is not None
        item_id = row[0] if row else None
        current_qty = row[1] if row else 0
        item_location = row[2] if row else ''
        item_title = row[3] if row else order['title']

        if inventory_found:
            new_qty = max(0, current_qty - sold_qty)
            searchrack_cur.execute('UPDATE SEARCHRACK SET QUANTITY = ? WHERE ID = ?', (new_qty, item_id))
            searchrack_conn.commit()
        else:
            new_qty = 0

        # Log to rackhistory.db
        removed_conn = sqlite3.connect('rackhistory.db')
        removed_cur = removed_conn.cursor()
        _ensure_removed_items_table(removed_cur)
        removed_cur.execute('''
            INSERT INTO removed_items
            (order_id, barcode, title, quantity_removed, removed_at, searchrack_id, old_quantity, new_quantity, removal_type, item_position)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (order['order_id'], barcode, item_title or order['title'], sold_qty, datetime.now().isoformat(),
              item_id, current_qty, new_qty, 'manual_immediate', item_location))
        removed_conn.commit()
        removed_conn.close()

        # Mark order as processed
        sold_cur.execute('UPDATE orders SET rackupdated = 1 WHERE id = ?', (order_id,))
        sold_conn.commit()
        sold_conn.close()
        searchrack_conn.close()

        return jsonify({
            'success': True,
            'inventory_found': inventory_found,
            'old_qty': current_qty,
            'new_qty': new_qty,
            'item_id': item_id
        })
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': _safe_error(e)}), 500

@app.route('/api/sold/find-inventory/<int:order_id>', methods=['GET'])
def find_inventory_for_sold(order_id):
    """Find matching inventory items in searchRack for a sold order by UPC/barcode"""
    try:
        # Get order details
        sold_conn = sqlite3.connect('sold.db')
        sold_conn.row_factory = sqlite3.Row
        sold_cur = sold_conn.cursor()
        sold_cur.execute('SELECT * FROM orders WHERE id = ?', (order_id,))
        order = sold_cur.fetchone()
        sold_conn.close()

        if not order:
            return jsonify({'success': False, 'error': 'Order not found'}), 404

        barcode = order['barcode'] or ''

        if not barcode:
            return jsonify({
                'success': True,
                'order': dict(order),
                'matches': [],
                'message': 'No barcode on order - use finder.html for manual search'
            })

        # Search searchRack for all matching items
        searchrack_conn = sqlite3.connect('searchRack.db')
        searchrack_conn.row_factory = sqlite3.Row
        searchrack_cur = searchrack_conn.cursor()

        matches = []

        # Try exact match
        searchrack_cur.execute('''
            SELECT ID, TITLE, BARCODE, ITEM_POSITION, PICTUREPOSITION, QUANTITY, IMAGE
            FROM SEARCHRACK
            WHERE BARCODE = ? COLLATE NOCASE AND QUANTITY > 0
        ''', (barcode,))
        matches.extend([dict(r) for r in searchrack_cur.fetchall()])

        # Try stripped zeros
        if barcode.isdigit():
            barcode_stripped = barcode.lstrip('0')
            if barcode_stripped != barcode:
                searchrack_cur.execute('''
                    SELECT ID, TITLE, BARCODE, ITEM_POSITION, PICTUREPOSITION, QUANTITY, IMAGE
                    FROM SEARCHRACK
                    WHERE BARCODE = ? COLLATE NOCASE AND QUANTITY > 0
                ''', (barcode_stripped,))
                for r in searchrack_cur.fetchall():
                    if not any(m['ID'] == r['ID'] for m in matches):
                        matches.append(dict(r))

            # Try padded version
            barcode_padded = barcode.zfill(12)
            if barcode_padded != barcode:
                searchrack_cur.execute('''
                    SELECT ID, TITLE, BARCODE, ITEM_POSITION, PICTUREPOSITION, QUANTITY, IMAGE
                    FROM SEARCHRACK
                    WHERE BARCODE = ? COLLATE NOCASE AND QUANTITY > 0
                ''', (barcode_padded,))
                for r in searchrack_cur.fetchall():
                    if not any(m['ID'] == r['ID'] for m in matches):
                        matches.append(dict(r))

        searchrack_conn.close()

        return jsonify({
            'success': True,
            'order': dict(order),
            'matches': matches
        })
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': _safe_error(e)}), 500

@app.route('/api/sold/match-barcode/<int:order_id>', methods=['POST'])
def match_barcode_to_sold(order_id):
    """Match/link an inventory barcode and location to a sold order. Pass empty string to clear."""
    try:
        data = request.get_json() or {}

        # Allow empty string (for undo/clear), but key must be present
        if 'barcode' not in data:
            return jsonify({'success': False, 'error': 'barcode required'}), 400
        barcode = data.get('barcode') or ''  # Normalize None to empty string
        location = data.get('location') or ''  # Location from searchRack ITEM_POSITION

        # Get order details
        sold_conn = sqlite3.connect('sold.db')
        sold_conn.row_factory = sqlite3.Row
        sold_cur = sold_conn.cursor()
        sold_cur.execute('SELECT * FROM orders WHERE id = ?', (order_id,))
        order = sold_cur.fetchone()

        if not order:
            sold_conn.close()
            return jsonify({'success': False, 'error': 'Order not found'}), 404

        old_barcode = order['barcode'] or ''
        old_location = order['location'] or ''

        # Update the order's barcode AND location directly
        sold_cur.execute('UPDATE orders SET barcode = ?, location = ? WHERE id = ?', (barcode, location, order_id))
        sold_conn.commit()
        sold_conn.close()

        # Invalidate sold-orders cache
        for days in [1, 2, 3, 5, 7, 14, 30, 60, 90, 120]:
            cache.delete(f'view//sold-orders?days={days}')
        print(f"✅ Matched order {order_id}: barcode={barcode}, location={location}")

        return jsonify({
            'success': True,
            'order_id': order_id,
            'old_barcode': old_barcode,
            'new_barcode': barcode,
            'old_location': old_location,
            'new_location': location
        })
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': _safe_error(e)}), 500

@app.route('/api/sold/debug-order/<int:order_id>', methods=['GET'])
def debug_sold_order(order_id):
    """Debug endpoint to check order state"""
    try:
        sold_conn = sqlite3.connect('sold.db')
        sold_conn.row_factory = sqlite3.Row
        sold_cur = sold_conn.cursor()
        sold_cur.execute('SELECT id, order_id, title, barcode, location, rackupdated FROM orders WHERE id = ?', (order_id,))
        order = sold_cur.fetchone()
        sold_conn.close()

        if not order:
            return jsonify({'success': False, 'error': 'Order not found'}), 404

        return jsonify({
            'success': True,
            'order': dict(order)
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/sold/debug-by-barcode/<barcode>', methods=['GET'])
def debug_sold_order_by_barcode(barcode):
    """Debug endpoint to check orders by barcode"""
    try:
        sold_conn = sqlite3.connect('sold.db')
        sold_conn.row_factory = sqlite3.Row
        sold_cur = sold_conn.cursor()
        sold_cur.execute('SELECT id, order_id, title, barcode, location, rackupdated FROM orders WHERE barcode = ? COLLATE NOCASE ORDER BY id DESC LIMIT 10', (barcode,))
        orders = [dict(row) for row in sold_cur.fetchall()]
        sold_conn.close()

        # Also check searchRack for this barcode
        rack_conn = sqlite3.connect('searchRack.db')
        rack_conn.row_factory = sqlite3.Row
        rack_cur = rack_conn.cursor()
        rack_cur.execute('SELECT ID, TITLE, BARCODE, ITEM_POSITION, QUANTITY FROM SEARCHRACK WHERE BARCODE = ? COLLATE NOCASE', (barcode,))
        rack_items = [dict(row) for row in rack_cur.fetchall()]
        rack_conn.close()

        return jsonify({
            'success': True,
            'barcode': barcode,
            'sold_orders': orders,
            'searchrack_items': rack_items
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/sold/reset-order/<int:order_id>', methods=['POST'])
def reset_sold_order(order_id):
    """Reset order's barcode and location to force fresh lookup"""
    try:
        sold_conn = sqlite3.connect('sold.db')
        sold_cur = sold_conn.cursor()
        sold_cur.execute('UPDATE orders SET location = NULL WHERE id = ?', (order_id,))
        sold_conn.commit()
        sold_conn.close()

        # Clear cache
        for days in [1, 2, 3, 5, 7, 14, 30, 60, 90, 120]:
            cache.delete(f'view//sold-orders?days={days}')

        return jsonify({'success': True, 'message': f'Reset location for order {order_id}'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/sold/manual-remove/<int:order_id>', methods=['POST'])
def manual_remove_sold_inventory(order_id):
    """Manually remove inventory from a specific searchRack item for a sold order"""
    try:
        from datetime import datetime

        data = request.get_json() or {}
        searchrack_id = data.get('searchrack_id')
        qty_to_remove = int(data.get('qty', 1))

        if not searchrack_id:
            return jsonify({'success': False, 'error': 'searchrack_id required'}), 400

        # Get order details
        sold_conn = sqlite3.connect('sold.db')
        sold_conn.row_factory = sqlite3.Row
        sold_cur = sold_conn.cursor()
        sold_cur.execute('SELECT * FROM orders WHERE id = ?', (order_id,))
        order = sold_cur.fetchone()

        if not order:
            sold_conn.close()
            return jsonify({'success': False, 'error': 'Order not found'}), 404

        if order['rackupdated'] == 1:
            sold_conn.close()
            return jsonify({'success': False, 'error': 'Already removed from inventory'}), 400

        # Get searchRack item
        searchrack_conn = sqlite3.connect('searchRack.db')
        searchrack_conn.row_factory = sqlite3.Row
        searchrack_cur = searchrack_conn.cursor()
        searchrack_cur.execute('SELECT ID, TITLE, BARCODE, ITEM_POSITION, QUANTITY FROM SEARCHRACK WHERE ID = ?', (searchrack_id,))
        rack_item = searchrack_cur.fetchone()

        if not rack_item:
            searchrack_conn.close()
            sold_conn.close()
            return jsonify({'success': False, 'error': 'SearchRack item not found'}), 404

        current_qty = rack_item['QUANTITY'] or 0
        new_qty = max(0, current_qty - qty_to_remove)

        # Update quantity
        searchrack_cur.execute('UPDATE SEARCHRACK SET QUANTITY = ? WHERE ID = ?', (new_qty, searchrack_id))
        searchrack_conn.commit()

        # Log to rackhistory.db
        removed_conn = sqlite3.connect('rackhistory.db')
        removed_cur = removed_conn.cursor()
        _ensure_removed_items_table(removed_cur)
        removed_cur.execute('''
            INSERT INTO removed_items
            (order_id, barcode, title, quantity_removed, removed_at, searchrack_id, old_quantity, new_quantity, removal_type, item_position)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (order['order_id'], rack_item['BARCODE'], rack_item['TITLE'] or order['title'], qty_to_remove,
              datetime.now().isoformat(), searchrack_id, current_qty, new_qty, 'manual_sold_selection', rack_item['ITEM_POSITION']))
        removed_conn.commit()
        removed_conn.close()

        # Mark order as processed
        sold_cur.execute('UPDATE orders SET rackupdated = 1 WHERE id = ?', (order_id,))
        sold_conn.commit()
        sold_conn.close()
        searchrack_conn.close()

        return jsonify({
            'success': True,
            'old_qty': current_qty,
            'new_qty': new_qty,
            'searchrack_id': searchrack_id
        })
    except Exception as e:
        import traceback
        traceback.print_exc()
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
        method_filter = (request.args.get('method') or '').strip()  # Filter by removal method

        history_items = []
        total_count = 0

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
                
                # Build query with optional search filter in SQL for better performance
                params = []
                where_clauses = []

                if search:
                    where_clauses.append("(LOWER(COALESCE(title, '')) LIKE ? OR LOWER(COALESCE(barcode, '')) LIKE ? OR LOWER(COALESCE(item_position, '')) LIKE ?)")
                    params.extend([f'%{search}%', f'%{search}%', f'%{search}%'])

                if method_filter:
                    where_clauses.append("removal_type = ?")
                    params.append(method_filter)

                where_sql = ''
                if where_clauses:
                    where_sql = ' WHERE ' + ' AND '.join(where_clauses)

                # Get total count (before LIMIT)
                count_query = 'SELECT COUNT(*) FROM removed_items' + where_sql
                rem_cur.execute(count_query, params)
                total_count = rem_cur.fetchone()[0]

                # Main data query
                query = '''
                    SELECT
                        id, order_id, barcode, title, quantity_removed,
                        removed_at, searchrack_id, old_quantity, new_quantity, removal_type, item_position
                    FROM removed_items
                ''' + where_sql + ' ORDER BY removed_at DESC LIMIT 1000'

                rem_cur.execute(query, params)

                for row in rem_cur.fetchall():
                    title = row['title'] or 'Unknown'
                    barcode = row['barcode'] or ''
                    location = row['item_position'] or ''

                    # For manual_edit type, show as addition or removal based on qty_change
                    removal_type = row['removal_type'] or 'unknown'

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
                    elif removal_type == 'locationmoved':
                        if qty_change > 0:
                            action = 'Added'
                            source = 'Location Move (Moved Here)'
                        elif qty_change < 0:
                            action = 'Removed'
                            source = 'Location Move (Moved Away)'
                        else:
                            action = 'Moved'
                            source = 'Location Move'
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
        
        return jsonify({'success': True, 'items': history_items[:500], 'total_count': total_count})  # Limit to 500 for performance
        
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

@app.route('/api/lookup-image-by-barcode', methods=['GET'])
def api_lookup_image_by_barcode():
    """Lookup image URL from rawbol.db by barcode with comprehensive matching"""
    barcode = request.args.get('barcode', '').strip()
    if not barcode:
        return jsonify({'success': False, 'error': 'No barcode provided'})

    # Clean barcode - remove .0 suffix if present (SQLite numeric artifact)
    if barcode.endswith('.0'):
        barcode = barcode[:-2]

    try:
        conn = sqlite3.connect('rawbol.db')
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()

        row = None
        barcode_stripped = barcode.lstrip('0') if barcode.isdigit() else barcode

        # Try exact match first
        cur.execute('SELECT image_url FROM raw_bol_items WHERE upc = ? COLLATE NOCASE LIMIT 1', (barcode,))
        row = cur.fetchone()

        # Try stripped zeros version
        if not row and barcode_stripped != barcode:
            cur.execute('SELECT image_url FROM raw_bol_items WHERE upc = ? COLLATE NOCASE LIMIT 1', (barcode_stripped,))
            row = cur.fetchone()

        # Try padded to 12 digits
        if not row and barcode.isdigit():
            padded = barcode.zfill(12)
            if padded != barcode:
                cur.execute('SELECT image_url FROM raw_bol_items WHERE upc = ? COLLATE NOCASE LIMIT 1', (padded,))
                row = cur.fetchone()

        # Try padded to 13 digits (EAN)
        if not row and barcode.isdigit():
            padded13 = barcode.zfill(13)
            if padded13 != barcode:
                cur.execute('SELECT image_url FROM raw_bol_items WHERE upc = ? COLLATE NOCASE LIMIT 1', (padded13,))
                row = cur.fetchone()

        # Try suffix match - find rawbol UPC that ends with our barcode (rawbol has more leading zeros)
        if not row and barcode_stripped and len(barcode_stripped) >= 8:
            cur.execute("SELECT image_url FROM raw_bol_items WHERE REPLACE(upc, '.0', '') LIKE ? COLLATE NOCASE LIMIT 1", ('%' + barcode_stripped,))
            row = cur.fetchone()

        # Try finding where rawbol's stripped UPC matches our stripped barcode
        if not row and barcode_stripped and len(barcode_stripped) >= 8:
            cur.execute("""
                SELECT image_url FROM raw_bol_items
                WHERE CAST(CAST(REPLACE(upc, '.0', '') AS INTEGER) AS TEXT) = ?
                COLLATE NOCASE LIMIT 1
            """, (barcode_stripped,))
            row = cur.fetchone()

        conn.close()

        if row and row['image_url']:
            return jsonify({'success': True, 'image_url': row['image_url']})
        else:
            return jsonify({'success': False, 'error': 'No image found'})
    except Exception as e:
        return jsonify({'success': False, 'error': _safe_error(e)})

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

                            barcode_to_lookup = item_out['barcode']
                            # Normalize barcode (strip .0 artifacts, ensure string)
                            if barcode_to_lookup is None:
                                barcode_to_lookup = ''
                            elif isinstance(barcode_to_lookup, float):
                                try:
                                    barcode_to_lookup = str(int(barcode_to_lookup))
                                except Exception:
                                    barcode_to_lookup = str(barcode_to_lookup)
                            else:
                                barcode_to_lookup = str(barcode_to_lookup).strip()
                                if barcode_to_lookup.endswith('.0') and barcode_to_lookup.replace('.0', '').isdigit():
                                    barcode_to_lookup = barcode_to_lookup[:-2]
                            barcode_stripped = barcode_to_lookup.lstrip('0') if barcode_to_lookup.isdigit() else barcode_to_lookup
                            row_bol = None

                            # Try exact match first
                            bol_cur.execute('SELECT item_description, image_url FROM raw_bol_items WHERE upc = ? COLLATE NOCASE LIMIT 1', (barcode_to_lookup,))
                            row_bol = bol_cur.fetchone()

                            # Try stripped zeros if no match
                            if not row_bol and barcode_to_lookup.isdigit():
                                stripped = barcode_stripped
                                if stripped != barcode_to_lookup:
                                    bol_cur.execute('SELECT item_description, image_url FROM raw_bol_items WHERE upc = ? COLLATE NOCASE LIMIT 1', (stripped,))
                                    row_bol = bol_cur.fetchone()

                            # Try padded to 12 digits if no match
                            if not row_bol and barcode_to_lookup.isdigit():
                                padded = barcode_to_lookup.zfill(12)
                                if padded != barcode_to_lookup:
                                    bol_cur.execute('SELECT item_description, image_url FROM raw_bol_items WHERE upc = ? COLLATE NOCASE LIMIT 1', (padded,))
                                    row_bol = bol_cur.fetchone()

                            # Try padded to 13 digits (EAN) if no match
                            if not row_bol and barcode_to_lookup.isdigit():
                                padded13 = barcode_to_lookup.zfill(13)
                                if padded13 != barcode_to_lookup:
                                    bol_cur.execute('SELECT item_description, image_url FROM raw_bol_items WHERE upc = ? COLLATE NOCASE LIMIT 1', (padded13,))
                                    row_bol = bol_cur.fetchone()

                            # Try suffix match (rawbol UPC has extra leading zeros)
                            if not row_bol and barcode_stripped and barcode_stripped.isdigit() and len(barcode_stripped) >= 8:
                                bol_cur.execute("SELECT item_description, image_url FROM raw_bol_items WHERE REPLACE(upc, '.0', '') LIKE ? COLLATE NOCASE LIMIT 1", ('%' + barcode_stripped,))
                                row_bol = bol_cur.fetchone()

                            # Try integer-cast comparison to normalize leading zeros
                            if not row_bol and barcode_stripped and barcode_stripped.isdigit() and len(barcode_stripped) >= 8:
                                bol_cur.execute("""
                                    SELECT item_description, image_url FROM raw_bol_items
                                    WHERE CAST(CAST(REPLACE(upc, '.0', '') AS INTEGER) AS TEXT) = ?
                                    COLLATE NOCASE LIMIT 1
                                """, (barcode_stripped,))
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

                    # Compute inv_status for sold items
                    try:
                        shipped_time = item.get('shipped_time') or ''
                        removal_cancelled = item.get('removal_cancelled') or 0
                        order_id_val = item.get('order_id') or ''
                        barcode_val = item.get('barcode') or ''

                        # Check if there's an automatic removal record in rackhistory.db
                        # Must match: barcode + removal_type='automatic' + date within expected window
                        has_removal_record = False
                        if barcode_val and shipped_time:
                            try:
                                from datetime import datetime, timedelta
                                # Parse shipped time for date comparison
                                if 'T' in shipped_time:
                                    if shipped_time.endswith('Z'):
                                        shipped_dt = datetime.fromisoformat(shipped_time.replace('Z', '+00:00'))
                                    else:
                                        shipped_dt = datetime.fromisoformat(shipped_time)
                                    shipped_dt = shipped_dt.replace(tzinfo=None)
                                else:
                                    shipped_dt = datetime.strptime(shipped_time, '%Y-%m-%d %H:%M:%S')

                                # Get grace period setting
                                grace_conn = sqlite3.connect('sold.db')
                                grace_cur = grace_conn.cursor()
                                grace_cur.execute("SELECT value FROM settings WHERE key = 'removal_grace_hours'")
                                grace_row = grace_cur.fetchone()
                                grace_hours = float(grace_row[0]) if grace_row and str(grace_row[0]).strip() else 48
                                grace_conn.close()

                                # Expected removal window: shipped + grace_period to shipped + grace_period + 24h buffer
                                removal_window_start = shipped_dt + timedelta(hours=grace_hours - 1)  # 1h before grace
                                removal_window_end = shipped_dt + timedelta(hours=grace_hours + 24)  # 24h after grace

                                hist_conn = sqlite3.connect('rackhistory.db')
                                hist_cur = hist_conn.cursor()
                                # Count valid removal types - automatic must be in window, others count anytime
                                hist_cur.execute("""
                                    SELECT id, removed_at, removal_type FROM removed_items
                                    WHERE barcode = ?
                                    AND removal_type IN ('automatic', 'repair_removal', 'manual_sold_removal', 'manual_immediate', 'manual_sold_selection', 'finder_removal')
                                    AND COALESCE(old_quantity, 0) > COALESCE(new_quantity, 0)
                                    ORDER BY removed_at DESC
                                """, (str(barcode_val),))

                                for row in hist_cur.fetchall():
                                    removed_at = row[1]
                                    removal_type = row[2]

                                    # Non-automatic removals (manual, repair, finder) count regardless of timing
                                    if removal_type != 'automatic':
                                        has_removal_record = True
                                        break

                                    # Automatic removals must be within expected date window
                                    if removed_at:
                                        try:
                                            if 'T' in str(removed_at):
                                                removed_dt = datetime.fromisoformat(str(removed_at).replace('Z', '+00:00'))
                                                removed_dt = removed_dt.replace(tzinfo=None)
                                            else:
                                                removed_dt = datetime.fromisoformat(str(removed_at))
                                            # Check if removal is within expected window
                                            if removal_window_start <= removed_dt <= removal_window_end:
                                                has_removal_record = True
                                                break
                                        except Exception:
                                            pass
                                hist_conn.close()
                            except Exception:
                                pass

                        if has_removal_record:
                            item_out['inv_status'] = 'COMPLETE'
                        elif shipped_time and not removal_cancelled:
                            # Check if within grace period
                            from datetime import datetime, timedelta
                            try:
                                # Get grace period setting
                                grace_conn = sqlite3.connect('sold.db')
                                grace_cur = grace_conn.cursor()
                                grace_cur.execute("SELECT value FROM settings WHERE key = 'removal_grace_hours'")
                                grace_row = grace_cur.fetchone()
                                grace_hours = float(grace_row[0]) if grace_row and str(grace_row[0]).strip() else 48
                                grace_conn.close()

                                # Parse shipped time
                                if 'T' in shipped_time:
                                    if shipped_time.endswith('Z'):
                                        shipped_dt = datetime.fromisoformat(shipped_time.replace('Z', '+00:00'))
                                    else:
                                        shipped_dt = datetime.fromisoformat(shipped_time)
                                    shipped_dt = shipped_dt.replace(tzinfo=None)
                                else:
                                    shipped_dt = datetime.strptime(shipped_time, '%Y-%m-%d %H:%M:%S')

                                grace_deadline = shipped_dt + timedelta(hours=grace_hours)
                                now = datetime.now()

                                if now < grace_deadline:
                                    # Still within grace period - calculate time remaining
                                    remaining = grace_deadline - now
                                    remaining_mins = int(remaining.total_seconds() / 60)
                                    item_out['inv_status'] = 'PENDING'
                                    item_out['inv_status_remaining_mins'] = remaining_mins
                                    item_out['inv_status_grace_deadline'] = grace_deadline.isoformat()
                                else:
                                    # Past grace period but not processed - should have been auto-removed
                                    item_out['inv_status'] = 'NOT_REMOVED'
                            except Exception as e:
                                print(f"Debug: grace period calc error: {e}")
                                item_out['inv_status'] = 'NOT_REMOVED'
                        else:
                            item_out['inv_status'] = 'NOT_REMOVED'
                    except Exception as e:
                        print(f"Debug: inv_status error: {e}")
                        item_out['inv_status'] = 'NOT_REMOVED'
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


@app.route('/api/location_items', methods=['GET'])
def api_location_items():
    """Get items for a specific shelf/location from searchRack.db"""
    try:
        location = (request.args.get('location') or '').strip()
        if not location:
            return jsonify({'success': False, 'error': 'Missing location'}), 400

        conn = sqlite3.connect('searchRack.db')
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute('''
            SELECT ID, TITLE, BARCODE, QUANTITY, IMAGE, IMAGES
            FROM SEARCHRACK
            WHERE LOWER(TRIM(ITEM_POSITION)) = LOWER(TRIM(?))
            ORDER BY TITLE
        ''', (location,))
        items = []
        for row in cur.fetchall():
            qty_val = row['QUANTITY'] if 'QUANTITY' in row.keys() else None
            try:
                qty = int(qty_val) if qty_val is not None and str(qty_val).strip() != '' else 1
            except Exception:
                qty = 1
            image_val = row['IMAGE'] or ''
            if not image_val:
                images_raw = row['IMAGES'] if 'IMAGES' in row.keys() else ''
                try:
                    if images_raw and str(images_raw).strip().startswith('['):
                        parsed = json.loads(images_raw)
                        if isinstance(parsed, list) and parsed:
                            image_val = parsed[0]
                except Exception:
                    pass
            items.append({
                'id': row['ID'],
                'title': row['TITLE'] or '',
                'barcode': row['BARCODE'] or '',
                'quantity': qty,
                'image': image_val or row['IMAGES'] or ''
            })
        return jsonify({'success': True, 'items': items})
    except Exception as e:
        return jsonify({'success': False, 'error': _safe_error(e)}), 500
    finally:
        try:
            conn.close()
        except Exception:
            pass


@app.route('/api/move_location', methods=['POST'])
def api_move_location():
    """Move searchRack items from one location to another and log history"""
    data = request.get_json() or {}
    from_location = (data.get('from_location') or '').strip()
    to_location = (data.get('to_location') or '').strip()
    item_ids = data.get('item_ids') or []

    if not from_location or not to_location or not item_ids:
        return jsonify({'success': False, 'error': 'Missing from_location, to_location, or item_ids'}), 400
    if not isinstance(item_ids, list):
        return jsonify({'success': False, 'error': 'item_ids must be a list'}), 400
    if from_location.lower() == to_location.lower():
        return jsonify({'success': False, 'error': 'Start and destination locations are the same'}), 400

    moved = 0
    skipped = 0

    try:
        conn = sqlite3.connect('searchRack.db')
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()

        # Determine PK and picture column names
        cur.execute("PRAGMA table_info('SEARCHRACK')")
        cols = [r[1] for r in cur.fetchall()]
        id_col = 'ID' if 'ID' in cols else ('id' if 'id' in cols else 'rowid')
        pic_col = 'PICTUREPOSITION' if 'PICTUREPOSITION' in cols else None
        if not pic_col:
            for c in cols:
                if c.lower() == 'pictureposition':
                    pic_col = c
                    break

        # History logging
        rem_conn = sqlite3.connect('rackhistory.db')
        rem_cur = rem_conn.cursor()
        _ensure_removed_items_table(rem_cur)
        # Ensure item_position column exists for older schemas
        try:
            rem_cur.execute("PRAGMA table_info(removed_items)")
            rem_cols = [c[1] for c in rem_cur.fetchall()]
            if 'item_position' not in rem_cols:
                rem_cur.execute('ALTER TABLE removed_items ADD COLUMN item_position TEXT')
                rem_conn.commit()
        except Exception:
            pass

        def _parse_qty(row_dict):
            for qc in ['QUANTITY', 'Quantity', 'quantity', 'Qty', 'QTY']:
                if qc in row_dict and row_dict.get(qc) is not None:
                    try:
                        return int(row_dict.get(qc))
                    except Exception:
                        return 1
            return 1

        def _find_qty_col():
            for qc in ['QUANTITY', 'Quantity', 'quantity', 'Qty', 'QTY']:
                if qc in cols:
                    return qc
            return None

        from_norm = from_location.strip().lower()

        from datetime import datetime
        qty_col_name = _find_qty_col()
        for raw_entry in item_ids:
            try:
                move_qty = None
                raw_id = raw_entry
                if isinstance(raw_entry, dict):
                    raw_id = raw_entry.get('id') or raw_entry.get('item_id') or raw_entry.get('ID')
                    move_qty = raw_entry.get('qty') if raw_entry.get('qty') is not None else raw_entry.get('quantity')
                if raw_id is None or raw_id == '':
                    skipped += 1
                    continue

                cur.execute(f"SELECT * FROM SEARCHRACK WHERE {id_col} = ?", (raw_id,))
                row = cur.fetchone()
                if not row:
                    skipped += 1
                    continue

                row_dict = dict(row)
                row_loc = (row_dict.get('ITEM_POSITION') or row_dict.get('item_position') or '').strip()
                if row_loc.lower() != from_norm:
                    skipped += 1
                    continue

                barcode = row_dict.get('BARCODE') or row_dict.get('barcode') or ''
                title = row_dict.get('TITLE') or row_dict.get('title') or ''
                qty = _parse_qty(row_dict)
                try:
                    move_qty_int = int(move_qty) if move_qty is not None else qty
                except Exception:
                    move_qty_int = qty
                if move_qty_int < 1 or move_qty_int > qty:
                    skipped += 1
                    continue
                now = datetime.now().isoformat()

                if move_qty_int < qty and qty_col_name:
                    # Partial move: decrement original qty and insert new row at destination
                    new_qty = qty - move_qty_int
                    cur.execute(f"UPDATE SEARCHRACK SET {qty_col_name} = ? WHERE {id_col} = ?", (new_qty, raw_id))

                    # Build new row from existing with updated quantity and location
                    col_names = [c for c in cols if c not in (id_col, 'id', 'ID', 'Id', 'rowid')]
                    placeholders = ','.join('?' for _ in col_names)
                    vals = []
                    for c in col_names:
                        val = row_dict.get(c)
                        if c == qty_col_name:
                            val = move_qty_int
                        if c.lower() == 'pictureposition':
                            val = ''
                        if c.lower() == 'item_position' or c == 'ITEM_POSITION' or c.lower() == 'itemposition':
                            val = to_location
                        vals.append(val)
                    cur.execute(f"INSERT INTO SEARCHRACK ({', '.join(col_names)}) VALUES ({placeholders})", tuple(vals))
                    new_id = cur.lastrowid

                    # Log as a location move (removed from old + added to new)
                    rem_cur.execute('''
                        INSERT INTO removed_items
                        (order_id, barcode, title, quantity_removed, removed_at, searchrack_id, old_quantity, new_quantity, removal_type, item_position)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ''', (None, barcode, title, move_qty_int, now, raw_id, qty, new_qty, 'locationmoved', from_location))

                    rem_cur.execute('''
                        INSERT INTO removed_items
                        (order_id, barcode, title, quantity_removed, removed_at, searchrack_id, old_quantity, new_quantity, removal_type, item_position)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ''', (None, barcode, title, move_qty_int, now, new_id, 0, move_qty_int, 'locationmoved', to_location))
                else:
                    # Full move: update row location (clear pictureposition if present)
                    if pic_col:
                        cur.execute(f"UPDATE SEARCHRACK SET ITEM_POSITION = ?, {pic_col} = ? WHERE {id_col} = ?", (to_location, '', raw_id))
                    else:
                        cur.execute(f"UPDATE SEARCHRACK SET ITEM_POSITION = ? WHERE {id_col} = ?", (to_location, raw_id))

                    # Log as a location move (removed from old + added to new)
                    rem_cur.execute('''
                        INSERT INTO removed_items
                        (order_id, barcode, title, quantity_removed, removed_at, searchrack_id, old_quantity, new_quantity, removal_type, item_position)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ''', (None, barcode, title, qty, now, raw_id, qty, 0, 'locationmoved', from_location))

                    rem_cur.execute('''
                        INSERT INTO removed_items
                        (order_id, barcode, title, quantity_removed, removed_at, searchrack_id, old_quantity, new_quantity, removal_type, item_position)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ''', (None, barcode, title, qty, now, raw_id, 0, qty, 'locationmoved', to_location))

                moved += 1
            except Exception:
                skipped += 1

        conn.commit()
        rem_conn.commit()

        # Update data version for cache invalidation
        update_data_version()

        return jsonify({'success': True, 'moved': moved, 'skipped': skipped})
    except Exception as e:
        return jsonify({'success': False, 'error': _safe_error(e)}), 500
    finally:
        try:
            conn.close()
        except Exception:
            pass
        try:
            rem_conn.close()
        except Exception:
            pass


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
    """Marketplace sale entry page (deprecated)."""
    return redirect('/marketplace-session')

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
        
        rawbol_cur.execute('SELECT item_description, image_url FROM raw_bol_items WHERE upc = ? COLLATE NOCASE LIMIT 1', (barcode,))
        row = rawbol_cur.fetchone()
        
        if row:
            return jsonify({
                'success': True,
                'found': True,
                'title': row['item_description'],
                'image_url': row['image_url']
            })
        else:
            return jsonify({'success': True, 'found': False, 'title': '', 'image_url': ''})
    except Exception as e:
        return jsonify({'success': False, 'error': _safe_error(e)}), 500
    finally:
        rawbol_conn.close()

@app.route('/api/marketplace/search', methods=['GET'])
def api_marketplace_search():
    """Search items by name for marketplace session."""
    try:
        q = (request.args.get('q') or '').strip()
        if not q:
            return jsonify({'success': False, 'error': 'Missing q'}), 400
        conn = sqlite3.connect('rawbol.db')
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute('''
            SELECT upc, item_description, image_url
            FROM raw_bol_items
            WHERE item_description LIKE ? COLLATE NOCASE
            ORDER BY created_at DESC
            LIMIT 30
        ''', (f'%{q}%',))
        results = [dict(r) for r in cur.fetchall()]
        conn.close()
        return jsonify({'success': True, 'results': results})
    except Exception as e:
        return jsonify({'success': False, 'error': _safe_error(e)}), 500

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
        session_id = (data.get('session_id') or '').strip() or None
        price_auto = data.get('price_auto', False)

        if not barcode:
            return jsonify({'success': False, 'error': 'Missing barcode'}), 400

        # Add to marketplace.db
        result = add_marketplace_sale(barcode, title, quantity, price, session_id=session_id, price_auto=price_auto)
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

@app.route('/api/enrich-sold-db', methods=['POST'])
def api_enrich_sold_db():
    """Run sold.db enrichment to fill in missing images and titles from rawbol"""
    try:
        from enrich_sold_db import enrich_sold_db, enrich_sold_db_image

        print("🔄 Starting sold.db enrichment via API...")

        # Run main enrichment (barcodes, titles, images)
        enrich_sold_db()

        # Run image-only enrichment for items that have barcodes but missing images
        enrich_sold_db_image()

        # Clear cache so new images show up
        cache.clear()

        print("✅ Sold.db enrichment completed via API")
        return jsonify({'success': True, 'message': 'Enrichment completed. Cache cleared.'})
    except Exception as e:
        import traceback
        traceback.print_exc()
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

