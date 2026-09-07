"""Cache admin for Sweet Shelves."""

import datetime
import json
import pytz
import time
from flask import jsonify, request
from . import config as ss_config, errors as ss_errors, runtime as ss_runtime


CACHE_CONFIG_FILE = ss_config.BASE_DIR / 'cache_config.json'


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


def api_admin_clear_cache():
    try:
        ss_runtime.cache.clear()
        print("🧹 Cache cleared manually via API")
        return jsonify({'success': True, 'message': 'Cache cleared successfully'})
    except Exception as e:
        return jsonify({'success': False, 'error': ss_errors._safe_error(e)}), 500


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
        ss_runtime.cache.clear()

        print("✅ Sold.db enrichment completed via API")
        return jsonify({'success': True, 'message': 'Enrichment completed. Cache cleared.'})
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': ss_errors._safe_error(e)}), 500


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
        return jsonify({'success': False, 'error': ss_errors._safe_error(e)}), 500


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
                    with ss_runtime.app.app_context():
                        ss_runtime.cache.clear()
                    last_run_date = current_date_str
                    
            time.sleep(30) # Check every 30 seconds
        except Exception as e:
            print(f"❌ Cache scheduler error: {e}")
            time.sleep(60)
