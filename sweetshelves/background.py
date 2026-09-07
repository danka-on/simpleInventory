"""Background for Sweet Shelves."""

import os
import threading
import time
from . import (
    cache_admin as ss_cache_admin, inventory_alerts as ss_inventory_alerts, inventory_cleanup as
    ss_inventory_cleanup, mail_center as ss_mail_center, server_metrics as ss_server_metrics, sync as
    ss_sync, telegram as ss_telegram,
)


_background_tasks_lock_handle = None


def start_background_services():
    """Start all background services with a lock to ensure single execution"""
    global _background_tasks_lock_handle
    # Only run on non-Windows (Unix/Pi) to avoid locking issues during dev
    if os.name != 'nt':
        lock_file = None
        try:
            import fcntl
            # Create/open lock file
            lock_file = open("background_tasks.lock", "w")
            # Try to acquire an exclusive non-blocking lock
            fcntl.lockf(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
            # Retain the open descriptor for the lifetime of this worker.
            # Letting this local object be garbage-collected released the lock
            # and allowed other Gunicorn workers to start duplicate alert loops.
            _background_tasks_lock_handle = lock_file
            print("🔒 Acquired background task lock")
        except IOError:
            if lock_file is not None:
                try:
                    lock_file.close()
                except Exception:
                    pass
            print("⚠️ Another worker is already running background tasks. Skipping.")
            return
        except ImportError:
            pass # fcntl not available

    print("🚀 Starting background services...")
    try:
        ss_inventory_cleanup._start_trash_purger_thread()
    except Exception as e:
        print(f"Failed to start trash purger: {e}")
        
    try:
        ss_inventory_cleanup._start_zero_qty_deleter_thread()
    except Exception as e:
        print(f"Failed to start zero qty deleter: {e}")
        
    try:
        ss_inventory_cleanup._start_automatic_removal_thread()
    except Exception as e:
        print(f"Failed to start automatic removal: {e}")
        
    try:
        ss_sync._start_auto_sync_thread()
    except Exception as e:
        print(f"Failed to start auto sync: {e}")

    try:
        ss_server_metrics._start_server_metrics_history_thread()
    except Exception as e:
        print(f"Failed to start server metrics history: {e}")
        
    try:
        ss_inventory_alerts._start_emailer_alert_thread()
    except Exception as e:
        print(f"Failed to start emailer: {e}")

    try:
        ss_telegram._start_telegram_alert_thread()
    except Exception as e:
        print(f"Failed to start telegram alerts: {e}")

    try:
        ss_telegram._start_telegram_command_thread()
    except Exception as e:
        print(f"Failed to start telegram commands: {e}")

    try:
        ss_mail_center._start_mail_center_refresh_thread()
    except Exception as e:
        print(f"Failed to start mail-center refresh: {e}")

    threading.Thread(target=ss_cache_admin.cache_scheduler_loop, daemon=True).start()


def delayed_start():
    time.sleep(5)
    start_background_services()
