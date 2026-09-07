"""Config for Sweet Shelves."""

import logging
import os
from dotenv import load_dotenv
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parents[1]


# Environment is loaded once, before any feature reads configuration.
load_dotenv(BASE_DIR / '.env')


CLIENT_ID = os.getenv("EBAY_CLIENT_ID")


CLIENT_SECRET = os.getenv("EBAY_CLIENT_SECRET")


RUNAME = os.getenv("EBAY_RUNAME")


DEBUG_MODE = os.getenv('DEBUG_MODE', 'false').lower() == 'true'


logger = logging.getLogger('app')
