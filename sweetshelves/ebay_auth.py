"""Ebay auth for Sweet Shelves."""

import time
from flask import jsonify, render_template, request
from token_manager import get_access_token, is_expired, load_tokens
from . import errors as ss_errors, runtime as ss_runtime, telegram as ss_telegram


access_token = None


headers = {'Authorization': '', 'Content-Type': 'application/json'}


@ss_errors.require_debug_mode
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
        return jsonify({"error": ss_errors._safe_error(e, 'token refresh')}), 500


@ss_runtime.cache.cached(timeout=60)  # Short cache so expired tokens show up quickly without hammering APIs
def api_token_status():
    """Check if eBay and Amazon tokens are valid/working."""
    return jsonify(ss_telegram._api_status_probe())


def home():
    shelf = request.args.get("shelf")
    return render_template("index.html", shelf=shelf)


def callback():
    code = request.args.get("code")
    error = (request.args.get("error") or "").strip()
    error_desc = (request.args.get("error_description") or "").strip()
    if not code:
        if error:
            detail = f"error={error}"
            if error_desc:
                detail += f", description={error_desc}"
            return f"Authorization failed: {detail}"
        return "Authorization failed or cancelled. No authorization code was returned."

    # Step 5: exchange this code for an access token
    from markupsafe import escape
    return f"Authorization code: {escape(code)}"
