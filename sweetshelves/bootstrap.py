"""Application startup: route registration, schema initialization, and workers."""

import os
import sys
import threading
from token_manager import get_access_token
from . import (
    background as ss_background, database as ss_database, ebay_auth as ss_ebay_auth, facebook as
    ss_facebook, inventory_history as ss_inventory_history, listing_alerts as ss_listing_alerts,
    listing_lifecycle as ss_listing_lifecycle, mail_schema as ss_mail_schema, shelves as ss_shelves,
)
from .routing import register_routes
from .runtime import app


def initialize():
    """Initialize the singleton Flask application once per process."""
    if getattr(app, "_sweetshelves_initialized", False):
        return app
    register_routes()
    ss_database.enable_wal_mode()
    ss_database.create_database_indexes()
    print(f"🔧 Python executable: {sys.executable}")
    ss_listing_alerts._ensure_listing_alerts_tables()
    ss_facebook._ensure_fbstore_tables()
    ss_mail_schema._ensure_storemail_tables()
    try:
        ss_ebay_auth.access_token = get_access_token()
    except Exception as e:
        print(f"⚠️ eBay token refresh failed at startup: {e}")
        print("   The app will start, but eBay API calls will fail until token is refreshed.")
        ss_ebay_auth.access_token = None
    ss_ebay_auth.headers = {
        "Authorization": f"Bearer {ss_ebay_auth.access_token}" if ss_ebay_auth.access_token else "",
        "Content-Type": "application/json"
    }
    try:
        ss_listing_lifecycle.ensure_lifecycle_tables()
        ss_shelves.ensure_shelf_groups_table()
        inventory_guard_status = ss_inventory_history._initialize_searchrack_history_guard()
        if not inventory_guard_status.get('installed'):
            print(f"⚠️ Active inventory guard not installed: {inventory_guard_status.get('reason', 'unknown reason')}")
        print("✅ Database initialization completed")
    except Exception as e:
        print(f"❌ Database initialization failed: {e}")
    if os.getenv('DISABLE_BACKGROUND_SERVICES', '').strip().lower() not in ('1', 'true', 'yes'):
        threading.Thread(target=ss_background.delayed_start, daemon=True).start()
    app._sweetshelves_initialized = True
    return app


initialize()
