"""Runtime for Sweet Shelves."""

import os
import secrets
import time
from flask import Flask
from flask_caching import Cache
from flask_compress import Compress
from . import config as ss_config


app = Flask('app', root_path=str(ss_config.BASE_DIR))


app.secret_key = os.getenv('FLASK_SECRET_KEY') or secrets.token_hex(32)


app.start_time = time.time()  # Track app startup time for uptime calculation


app.config['TEMPLATES_AUTO_RELOAD'] = False  # Disable template reloading for better performance


app.config['SEND_FILE_MAX_AGE_DEFAULT'] = None  # Revalidate unversioned assets after deployments.


try:
    app.jinja_env.auto_reload = False  # Disable Jinja auto-reload
except Exception:
    pass


cache = Cache(app, config={
    'CACHE_TYPE': 'simple',  # In-memory cache
    'CACHE_DEFAULT_TIMEOUT': 300,  # 5 minutes default
    'CACHE_THRESHOLD': 500  # Max 500 cached items
})


Compress(app)


app.config['MAX_CONTENT_LENGTH'] = 64 * 1024 * 1024  # 64 MB limit for uploads
