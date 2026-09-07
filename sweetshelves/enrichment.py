"""Enrichment for Sweet Shelves."""

import os
import threading
import time
from DBmanager import enrich_searchrack_db
from flask import jsonify


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


def maybe_run_startup_enrich():
    # only run once in main process
    try:
        if os.environ.get('WERKZEUG_RUN_MAIN') == 'true' or os.environ.get('FLASK_ENV') == 'production' or not os.getenv('FLASK_DEBUG'):
            print('Starting searchRack enrichment on startup...')
            # run in background so startup isn't blocked heavily
            _run_enrich_in_background()
    except Exception as e:
        print('Failed to start startup enrich:', e)


def api_refresh_searchrack():
    # no auth as requested
    if _enrich_lock.locked():
        return jsonify({'success': False, 'message': 'Enrichment already running'}), 409
    started = _run_enrich_in_background()
    if not started:
        return jsonify({'success': False, 'message': 'Failed to start enrichment'}), 500
    return jsonify({'success': True, 'message': 'Enrichment started'})


def api_enrich_status():
    return jsonify(_enrich_status)
