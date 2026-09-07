"""Provider-aware expiry checks for long-lived marketplace credentials.

Only provider-reported or explicitly configured expiration dates are eligible
for alerts. File modification dates are deliberately ignored because copying or
refreshing a credential file does not establish when its token was issued.
"""

from __future__ import annotations

import base64
import datetime as dt
import json
import os
from pathlib import Path
import xml.etree.ElementTree as ET

import requests


UTC = dt.timezone.utc


def _utc_now(now=None):
    value = now or dt.datetime.now(UTC)
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _parse_datetime(value):
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return dt.datetime.fromtimestamp(float(value), tz=UTC)
    raw = str(value).strip()
    if not raw:
        return None
    if raw.isdigit():
        return dt.datetime.fromtimestamp(float(raw), tz=UTC)
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    parsed = dt.datetime.fromisoformat(raw)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _configured_expiry(env, direct_key, issued_key, lifetime_days):
    direct = _parse_datetime(env.get(direct_key))
    if direct:
        return direct
    issued = _parse_datetime(env.get(issued_key))
    if issued:
        return issued + dt.timedelta(days=lifetime_days)
    return None


def _status(
    key, label, expires_at, now, *, source="provider", active=None, note="",
    alertable=None,
):
    result = {
        "key": key,
        "label": label,
        "expires_at": expires_at.isoformat() if expires_at else None,
        "days_left": None,
        "source": source,
        "active": active,
        "note": note,
        "alertable": bool(expires_at) if alertable is None else bool(alertable),
    }
    if expires_at:
        seconds = (expires_at - now).total_seconds()
        # Fractional days are retained for precise threshold checks and display.
        result["days_left"] = seconds / 86400.0
    return result


def _read_json(path):
    try:
        with Path(path).open("r", encoding="utf-8") as handle:
            value = json.load(handle)
        return value if isinstance(value, dict) else {}
    except Exception:
        return {}


def _ebay_oauth_statuses(base_dir, env, now, http):
    tokens = _read_json(Path(base_dir) / "tokens.json")
    if not tokens:
        return [_status("ebay_oauth_refresh", "eBay OAuth refresh token", None, now,
                        active=False, note="not configured")]

    statuses = []
    access_expiry = _parse_datetime(tokens.get("expires_at"))
    statuses.append(_status(
        "ebay_oauth_access",
        "eBay OAuth access token",
        access_expiry,
        now,
        source="stored expiry",
        active=(access_expiry is None or access_expiry > now),
        note="automatically refreshed; no manual renewal needed",
        alertable=False,
    ))

    refresh_expiry = _parse_datetime(
        tokens.get("refresh_token_expires_at")
        or env.get("EBAY_OAUTH_REFRESH_TOKEN_EXPIRES_AT")
    )
    active = None
    source = "stored expiry" if refresh_expiry else "unknown"
    note = ""
    client_id = str(env.get("EBAY_CLIENT_ID") or "").strip()
    client_secret = str(env.get("EBAY_CLIENT_SECRET") or "").strip()
    refresh_token = str(tokens.get("refresh_token") or "").strip()
    if client_id and client_secret and refresh_token:
        try:
            encoded = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
            response = http.post(
                "https://api.ebay.com/identity/v1/oauth2/token/introspect",
                headers={
                    "Authorization": f"Basic {encoded}",
                    "Content-Type": "application/x-www-form-urlencoded",
                    "Accept": "application/json",
                },
                data={"token": refresh_token, "token_type_hint": "refresh_token"},
                timeout=12,
            )
            if response.ok:
                payload = response.json() if response.content else {}
                active = bool(payload.get("active"))
                provider_expiry = _parse_datetime(payload.get("exp"))
                if provider_expiry:
                    refresh_expiry = provider_expiry
                    source = "eBay"
                elif active is False:
                    note = "provider expiry lookup did not recognize this historical token"
            else:
                note = f"expiry lookup unavailable (HTTP {response.status_code})"
        except Exception as exc:
            note = f"expiry lookup unavailable ({type(exc).__name__})"
    if not refresh_expiry and not note:
        note = "expiration date unavailable; reauthorize once to record it"
    statuses.append(_status(
        "ebay_oauth_refresh",
        "eBay OAuth refresh token",
        refresh_expiry,
        now,
        source=source,
        active=active,
        note=note,
    ))
    return statuses


def _ebay_legacy_status(env, now, http):
    token = str(env.get("EBAY_OLDAUTH_TOKEN") or "").strip()
    if not token:
        return _status("ebay_authnauth", "eBay Trading token", None, now,
                       active=False, note="not configured")

    configured = _parse_datetime(env.get("EBAY_OLDAUTH_TOKEN_EXPIRES_AT"))
    headers = {
        "X-EBAY-API-SITEID": "0",
        "X-EBAY-API-COMPATIBILITY-LEVEL": "967",
        "X-EBAY-API-CALL-NAME": "GetTokenStatus",
        "X-EBAY-API-DEV-NAME": env.get("EBAY_PROD_DEV_ID"),
        "X-EBAY-API-APP-NAME": env.get("EBAY_PROD_APP_ID"),
        "X-EBAY-API-CERT-NAME": env.get("EBAY_PROD_CERT_ID"),
        "Content-Type": "text/xml",
    }
    payload = (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<GetTokenStatusRequest xmlns="urn:ebay:apis:eBLBaseComponents">'
        f'<RequesterCredentials><eBayAuthToken>{token}</eBayAuthToken></RequesterCredentials>'
        '</GetTokenStatusRequest>'
    )
    expiry = configured
    source = "configured" if configured else "unknown"
    active = None
    note = ""
    try:
        response = http.post(
            "https://api.ebay.com/ws/api.dll",
            headers=headers,
            data=payload,
            timeout=12,
        )
        if response.ok:
            root = ET.fromstring(response.text or "")
            ns = {"eb": "urn:ebay:apis:eBLBaseComponents"}
            expiry = _parse_datetime(root.findtext(".//eb:ExpirationTime", namespaces=ns)) or expiry
            status_text = str(root.findtext(".//eb:Status", default="", namespaces=ns) or "").casefold()
            active = status_text in ("active", "") if expiry else None
            if expiry:
                source = "eBay"
            else:
                api_error = (
                    root.findtext(".//eb:Errors/eb:LongMessage", default="", namespaces=ns)
                    or root.findtext(".//eb:Errors/eb:ShortMessage", default="", namespaces=ns)
                )
                if api_error:
                    note = f"expiry lookup unavailable ({str(api_error).strip()[:160]})"
        else:
            note = f"expiry lookup unavailable (HTTP {response.status_code})"
    except Exception as exc:
        note = f"expiry lookup unavailable ({type(exc).__name__})"
    if not expiry and not note:
        note = "expiration date unavailable"
    return _status("ebay_authnauth", "eBay Trading token", expiry, now,
                   source=source, active=active, note=note)


def _amazon_status(base_dir, env, now):
    credentials = _read_json(Path(base_dir) / "amazon_credentials.json")
    if not credentials.get("refresh_token"):
        return _status("amazon_lwa_refresh", "Amazon LWA refresh token", None, now,
                       active=False, note="not configured")
    expiry = _parse_datetime(
        credentials.get("refresh_token_expires_at")
        or credentials.get("authorization_expires_at")
    ) or _configured_expiry(
        env,
        "AMAZON_REFRESH_TOKEN_EXPIRES_AT",
        "AMAZON_REFRESH_TOKEN_AUTHORIZED_AT",
        365,
    )
    note = (
        "no provider expiration date; configure one only if annual reauthorization applies"
        if not expiry else "configured reauthorization date"
    )
    return _status(
        "amazon_lwa_refresh",
        "Amazon LWA refresh token",
        expiry,
        now,
        source="configured" if expiry else "unknown",
        active=None,
        note=note,
    )


def collect_token_statuses(base_dir, *, env=None, now=None, http=requests):
    """Return display-safe token lifecycle data without exposing credentials."""
    environment = os.environ if env is None else env
    current = _utc_now(now)
    statuses = _ebay_oauth_statuses(base_dir, environment, current, http)
    statuses.append(_ebay_legacy_status(environment, current, http))
    statuses.append(_amazon_status(base_dir, environment, current))
    return statuses


def expiring_token_reminders(statuses, *, within_days=14):
    reminders = []
    for item in statuses or []:
        days_left = item.get("days_left")
        if not item.get("alertable") or not isinstance(days_left, (int, float)):
            continue
        if days_left > float(within_days):
            continue
        expiry = str(item.get("expires_at") or "")
        overdue = days_left <= 0
        rounded = 0 if overdue else max(1, int(days_left + 0.999999))
        label = str(item.get("label") or "Token")
        message = (
            f"{label} has expired; renew it now."
            if overdue
            else f"{label} expires in {rounded} day(s)."
        )
        reminders.append({
            "alert_key": f"token-expiry:{item.get('key')}",
            "signature": expiry,
            "label": label,
            "status": "expired" if overdue else "reminder",
            "days_left": days_left,
            "expires_at": expiry,
            "message": message,
        })
    return reminders


def format_token_statuses(statuses):
    lines = ["Token expiration"]
    for item in statuses or []:
        label = str(item.get("label") or "Token")
        days_left = item.get("days_left")
        if isinstance(days_left, (int, float)):
            if days_left <= 0:
                detail = "expired"
            elif days_left < 1:
                hours = max(1, int(days_left * 24 + 0.999999))
                detail = f"less than 1 day ({hours} hour(s))"
            else:
                detail = f"{max(1, int(days_left + 0.999999))} day(s)"
        else:
            detail = "unknown"
        note = str(item.get("note") or "").strip()
        lines.append(f"- {label}: {detail}" + (f" — {note}" if note else ""))
    return "\n".join(lines)
