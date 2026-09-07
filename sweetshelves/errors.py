"""Errors for Sweet Shelves."""

from flask import jsonify
from . import config as ss_config


def _safe_error(e, context=''):
    """Log the real error server-side, return a generic message for the client."""
    ss_config.logger.error(f"{context}: {e}" if context else str(e), exc_info=True)
    return 'An internal error occurred'


def require_debug_mode(f):
    """Decorator to restrict endpoints to debug mode only."""
    from functools import wraps
    @wraps(f)
    def decorated(*args, **kwargs):
        if not ss_config.DEBUG_MODE:
            return jsonify({'error': 'Not found'}), 404
        return f(*args, **kwargs)
    return decorated


class _ListingAgentUserError(Exception):
    def __init__(self, message, status_code=400, extra=None):
        super().__init__(message)
        self.status_code = status_code
        self.extra = extra or {}


def handle_file_too_large(e):
    return jsonify({'success': False, 'error': 'Upload too large. Try fewer photos or enable Low res.'}), 413
