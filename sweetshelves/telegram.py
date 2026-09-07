"""Telegram for Sweet Shelves."""

import datetime
import json
import os
import requests
import sqlite3
import subprocess
import threading
import time
from DBmanager import connect_db
from flask import jsonify, render_template, request
from pathlib import Path
from token_manager import get_access_token, is_expired, load_tokens
from . import (
    config as ss_config, errors as ss_errors, health as ss_health, integrations as ss_integrations,
    inventory_alerts as ss_inventory_alerts, server_metrics as ss_server_metrics, sync as ss_sync,
)


def _ensure_telegram_tables():
    conn = None
    try:
        conn = sqlite3.connect('searchRack.db')
        cur = conn.cursor()
        cur.execute('''
            CREATE TABLE IF NOT EXISTS telegram_config (
                key TEXT PRIMARY KEY,
                value TEXT,
                updated_at TEXT
            )
        ''')
        cur.execute('''
            CREATE TABLE IF NOT EXISTS telegram_recipients (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id TEXT NOT NULL,
                display_name TEXT,
                alert_type TEXT DEFAULT 'inventory',
                interval TEXT DEFAULT '1d',
                enabled INTEGER DEFAULT 1,
                disable_notification INTEGER DEFAULT 1,
                last_sent_at TEXT,
                last_test_sent TEXT,
                created_at TEXT,
                updated_at TEXT,
                UNIQUE(chat_id, alert_type)
            )
        ''')
        cur.execute('''
            CREATE TABLE IF NOT EXISTS telegram_alert_state (
                alert_key TEXT PRIMARY KEY,
                signature TEXT,
                consecutive_count INTEGER DEFAULT 0,
                first_seen_at TEXT,
                last_seen_at TEXT,
                notified_signature TEXT,
                notified_at TEXT,
                metadata_json TEXT
            )
        ''')
        cur.execute('''
            CREATE TABLE IF NOT EXISTS telegram_recent_chats (
                chat_id TEXT PRIMARY KEY,
                title TEXT,
                username TEXT,
                first_name TEXT,
                last_name TEXT,
                chat_type TEXT,
                last_text TEXT,
                last_message_at TEXT,
                last_update_id INTEGER
            )
        ''')
        conn.commit()
    finally:
        if conn is not None:
            conn.close()


def _telegram_get_config_value(key, default=''):
    _ensure_telegram_tables()
    conn = None
    try:
        conn = sqlite3.connect('searchRack.db')
        cur = conn.cursor()
        cur.execute('SELECT value FROM telegram_config WHERE key = ?', (key,))
        row = cur.fetchone()
        return str(row[0] or '').strip() if row and row[0] is not None else default
    except Exception:
        return default
    finally:
        if conn is not None:
            conn.close()


def _telegram_set_config_value(key, value):
    _ensure_telegram_tables()
    conn = None
    try:
        conn = sqlite3.connect('searchRack.db')
        cur = conn.cursor()
        cur.execute('''
            INSERT OR REPLACE INTO telegram_config (key, value, updated_at)
            VALUES (?, ?, ?)
        ''', (key, str(value or '').strip(), datetime.datetime.now().isoformat()))
        conn.commit()
    finally:
        if conn is not None:
            conn.close()


def _telegram_get_bot_token():
    token = _telegram_get_config_value('bot_token', '')
    if token:
        return token
    return str(os.getenv('TELEGRAM_BOT_TOKEN') or '').strip()


def _telegram_api_base():
    token = _telegram_get_bot_token()
    if not token:
        return ''
    return f'https://api.telegram.org/bot{token}'


def _telegram_send_message(chat_id, text, disable_notification=True):
    base = _telegram_api_base()
    if not base:
        return False, 'Telegram bot token is not configured'
    if not chat_id:
        return False, 'Missing Telegram chat id'

    try:
        resp = requests.post(
            f'{base}/sendMessage',
            json={
                'chat_id': str(chat_id),
                'text': str(text or ''),
                'disable_notification': bool(disable_notification)
            },
            timeout=20
        )
        data = resp.json() if resp.content else {}
        if not resp.ok or not data.get('ok'):
            return False, data.get('description') or f'HTTP {resp.status_code}'
        return True, data.get('result', {})
    except Exception as e:
        return False, str(e)


def _telegram_parse_interval_minutes(interval):
    raw = str(interval or '').strip().lower()
    if raw == '10m':
        return 10
    if raw == '30m':
        return 30
    if raw == '1h':
        return 60
    if raw == '12h':
        return 720
    if raw == '1d':
        return 1440
    if raw == '1w':
        return 10080
    return 1440


def _telegram_disable_notification_value(value, default=True):
    raw = str(value if value is not None else '').strip().lower()
    if raw in ('0', 'false', 'no', 'off'):
        return False
    if raw in ('1', 'true', 'yes', 'on'):
        return True
    return bool(default)


def _api_credential_age_issue(label, path, *, warn_days=165, reset_days=180):
    try:
        p = Path(path)
        if not p.exists():
            return {
                'label': label,
                'status': 'missing',
                'message': f'{label} credential file is missing: {p.name}'
            }
        updated_at = datetime.datetime.fromtimestamp(p.stat().st_mtime)
        age_days = (datetime.datetime.now() - updated_at).days
        days_left = reset_days - age_days
        if age_days >= reset_days:
            return {
                'label': label,
                'status': 'overdue',
                'age_days': age_days,
                'updated_at': updated_at.isoformat(timespec='seconds'),
                'message': f'{label} token reset is overdue. Last refreshed about {age_days} days ago.'
            }
        if age_days >= warn_days:
            return {
                'label': label,
                'status': 'reminder',
                'age_days': age_days,
                'days_left': days_left,
                'updated_at': updated_at.isoformat(timespec='seconds'),
                'message': f'{label} token reset reminder: about {days_left} day(s) left before the 180-day refresh window.'
            }
    except Exception as e:
        return {
            'label': label,
            'status': 'issue',
            'message': f'Could not check {label} token refresh age: {e}'
        }
    return None


def _api_status_probe():
    """Uncached eBay/Amazon API status probe for text alerts and status UI."""
    status = {'ebay': 'ok', 'amazon': 'ok', 'details': {}}

    try:
        tokens = load_tokens()
        if is_expired(tokens):
            get_access_token()
            tokens = load_tokens()
        test_headers = {
            'Authorization': f"Bearer {tokens.get('access_token', '')}",
            'Content-Type': 'application/json'
        }
        r = requests.get('https://api.ebay.com/sell/fulfillment/v1/order?limit=1', headers=test_headers, timeout=8)
        if r.status_code == 401:
            status['ebay'] = 'expired'
            status['details']['ebay'] = f'eBay auth check failed with HTTP {r.status_code}.'
        elif r.status_code == 403:
            status['ebay'] = 'permission'
            status['details']['ebay'] = (
                'eBay API returned HTTP 403. The token was accepted, but the request '
                'does not have the required permission or scope.'
            )
        elif r.status_code == 429 or r.status_code >= 500:
            status['ebay'] = 'transient'
            status['details']['ebay'] = f'eBay API is temporarily unavailable (HTTP {r.status_code}).'
        elif r.status_code >= 400:
            status['ebay'] = 'issue'
            status['details']['ebay'] = f'eBay API check returned HTTP {r.status_code}.'
    except requests.exceptions.RequestException as e:
        status['ebay'] = 'transient'
        status['details']['ebay'] = f'eBay API request temporarily failed: {e}'
    except Exception as e:
        err_str = str(e).lower()
        if 'unauthorized' in err_str or 'invalid' in err_str or 'expired' in err_str:
            status['ebay'] = 'expired'
        else:
            status['ebay'] = 'issue'
        status['details']['ebay'] = f'eBay token/API check failed: {e}'

    try:
        amazon = ss_integrations.AmazonManager()
        amazon_result = amazon.connection_status()
        amazon_state = str(amazon_result.get('status') or 'issue').strip().lower()
        if amazon_state != 'ok':
            amazon_detail = str(
                amazon_result.get('message') or 'Amazon SP-API connection test failed.'
            ).strip()
            transient_markers = (
                'timeout', 'timed out', 'connection', 'temporar', 'throttl',
                'rate limit', 'http 429', 'http 500', 'http 502', 'http 503',
                'http 504', 'service unavailable'
            )
            if amazon_state == 'expired':
                status['amazon'] = 'expired'
            elif any(marker in amazon_detail.casefold() for marker in transient_markers):
                status['amazon'] = 'transient'
            else:
                status['amazon'] = 'issue'
            status['details']['amazon'] = amazon_detail
    except Exception as e:
        err_str = str(e).lower()
        if (
            'unauthorized' in err_str
            or 'invalid_grant' in err_str
            or 'access denied' in err_str
            or 'invalid access token' in err_str
            or 'invalid refresh token' in err_str
            or 'http 401' in err_str
        ):
            status['amazon'] = 'expired'
        elif any(marker in err_str for marker in (
            'timeout', 'timed out', 'connection', 'temporar', 'throttl',
            'rate limit', 'http 429', 'http 500', 'http 502', 'http 503', 'http 504'
        )):
            status['amazon'] = 'transient'
        else:
            status['amazon'] = 'issue'
        status['details']['amazon'] = f'Amazon SP-API check failed: {e}'

    # Token-file modification time is not credential issuance time. eBay access
    # token refreshes rewrite tokens.json, and Amazon credentials live elsewhere,
    # so file age produced misleading expiry reminders.
    status['reminders'] = []
    return status


def _telegram_alert_state_update(
    alert_key,
    signature,
    *,
    required_count=1,
    minimum_age_minutes=0,
    metadata=None
):
    """Persist confirmation state so restarts and brief failures cannot trigger alerts."""
    _ensure_telegram_tables()
    now = datetime.datetime.now().astimezone()
    now_iso = now.isoformat()
    key = str(alert_key or '').strip()
    signature = str(signature or '').strip()
    conn = sqlite3.connect('searchRack.db')
    conn.row_factory = sqlite3.Row
    try:
        cur = conn.cursor()
        cur.execute('SELECT * FROM telegram_alert_state WHERE alert_key = ?', (key,))
        row = cur.fetchone()
        if not signature:
            cur.execute('''
                INSERT INTO telegram_alert_state (
                    alert_key, signature, consecutive_count, first_seen_at,
                    last_seen_at, notified_signature, notified_at, metadata_json
                ) VALUES (?, '', 0, NULL, ?, '', NULL, ?)
                ON CONFLICT(alert_key) DO UPDATE SET
                    signature = '',
                    consecutive_count = 0,
                    first_seen_at = NULL,
                    last_seen_at = excluded.last_seen_at,
                    notified_signature = '',
                    notified_at = NULL,
                    metadata_json = excluded.metadata_json
            ''', (key, now_iso, json.dumps(metadata or {}, default=str)))
            conn.commit()
            return False

        if row and str(row['signature'] or '') == signature:
            count = int(row['consecutive_count'] or 0) + 1
            first_seen_raw = str(row['first_seen_at'] or now_iso)
        else:
            count = 1
            first_seen_raw = now_iso

        try:
            first_seen = datetime.datetime.fromisoformat(first_seen_raw)
            if first_seen.tzinfo is None:
                first_seen = first_seen.replace(tzinfo=now.tzinfo)
            age_minutes = max(0, (now - first_seen).total_seconds() / 60.0)
        except Exception:
            age_minutes = 0

        notified_signature = (
            str(row['notified_signature'] or '')
            if row and str(row['signature'] or '') == signature
            else ''
        )
        cur.execute('''
            INSERT INTO telegram_alert_state (
                alert_key, signature, consecutive_count, first_seen_at,
                last_seen_at, notified_signature, notified_at, metadata_json
            ) VALUES (?, ?, ?, ?, ?, ?, NULL, ?)
            ON CONFLICT(alert_key) DO UPDATE SET
                signature = excluded.signature,
                consecutive_count = excluded.consecutive_count,
                first_seen_at = excluded.first_seen_at,
                last_seen_at = excluded.last_seen_at,
                notified_signature = excluded.notified_signature,
                metadata_json = excluded.metadata_json
        ''', (
            key,
            signature,
            count,
            first_seen_raw,
            now_iso,
            notified_signature,
            json.dumps(metadata or {}, default=str),
        ))
        conn.commit()
        return (
            count >= max(1, int(required_count or 1))
            and age_minutes >= max(0, float(minimum_age_minutes or 0))
            and notified_signature != signature
        )
    finally:
        conn.close()


def _telegram_alert_state_mark_notified(alert_keys):
    keys = [str(key or '').strip() for key in (alert_keys or []) if str(key or '').strip()]
    if not keys:
        return
    _ensure_telegram_tables()
    now_iso = datetime.datetime.now().astimezone().isoformat()
    conn = sqlite3.connect('searchRack.db')
    try:
        cur = conn.cursor()
        for key in keys:
            cur.execute('''
                UPDATE telegram_alert_state
                SET notified_signature = signature, notified_at = ?
                WHERE alert_key = ?
            ''', (now_iso, key))
        conn.commit()
    finally:
        conn.close()


def _api_alert_failure_is_confirmed(platform, state, message):
    """Require a sustained, persisted marketplace failure before automated texts."""
    platform_key = str(platform or '').strip().lower()
    state_key = str(state or '').strip().lower()
    if state_key == 'ok':
        _telegram_alert_state_update(f'api:{platform_key}', '')
        return False
    # Transient network/rate-limit/service failures need a longer confirmation
    # window than explicit authentication or permission failures.
    required_count = 4 if state_key == 'transient' else 3
    minimum_age = 25 if state_key == 'transient' else 15
    return _telegram_alert_state_update(
        f'api:{platform_key}',
        state_key,
        required_count=required_count,
        minimum_age_minutes=minimum_age,
        metadata={'message': str(message or '')[:1000]},
    )


def _collect_api_issue_alert(require_confirmation=False):
    probe = _api_status_probe()
    issues = []
    for platform in ('ebay', 'amazon'):
        state = str(probe.get(platform) or 'ok').strip().lower()
        detail = str((probe.get('details') or {}).get(platform) or '').strip()
        if require_confirmation:
            confirmed = _api_alert_failure_is_confirmed(platform, state, detail)
            if state != 'ok' and not confirmed:
                continue
        if state == 'ok':
            continue
        label = 'eBay' if platform == 'ebay' else 'Amazon'
        issues.append({
            'alert_key': f'api:{platform}',
            'platform': label,
            'status': state,
            'message': detail or f'{label} API status is {state}.'
        })
    for reminder in probe.get('reminders') or []:
        issues.append({
            'platform': reminder.get('label') or 'Token',
            'status': reminder.get('status') or 'reminder',
            'message': reminder.get('message') or ''
        })
    return {
        'has_issues': bool(issues),
        'issues': issues,
        'probe': probe
    }


def _generate_api_issue_text(alert):
    issues = alert.get('issues') or []
    lines = ['API ISSUE TEXT ALERT']
    if not issues:
        lines.append('No eBay or Amazon API issues are currently active.')
    for issue in issues:
        platform = str(issue.get('platform') or 'API').strip()
        state = str(issue.get('status') or 'issue').strip().upper()
        message = str(issue.get('message') or '').strip()
        lines.append(f'- {platform}: {state}')
        if message:
            lines.append(f'  {message}')
    lines.append('')
    lines.append('Check tokens/secrets on the Pi if this repeats.')
    return '\n'.join(lines)


def _telegram_collect_recipient_rows():
    _ensure_telegram_tables()
    conn = sqlite3.connect('searchRack.db')
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute('''
        SELECT id, chat_id, display_name, alert_type, interval, enabled, disable_notification, last_sent_at, last_test_sent
        FROM telegram_recipients
        ORDER BY COALESCE(display_name, chat_id) COLLATE NOCASE ASC, id ASC
    ''')
    rows = [dict(row) for row in cur.fetchall()]
    conn.close()
    return rows


def _telegram_recipient_label(recipient):
    chat_id = str(_telegram_recipient_value(recipient, 'chat_id', '') or '').strip()
    name = str(_telegram_recipient_value(recipient, 'display_name', '') or '').strip()
    alert_type = str(_telegram_recipient_value(recipient, 'alert_type', '') or '').strip().lower()
    if name:
        return name
    if chat_id:
        return chat_id
    return alert_type or 'Recipient'


def _telegram_recipient_value(recipient, key, default=''):
    if recipient is None:
        return default
    try:
        if isinstance(recipient, dict):
            return recipient.get(key, default)
    except Exception:
        pass
    try:
        return recipient[key]
    except Exception:
        return default


def _telegram_subscription_targets(alert_type):
    rows = []
    for row in _telegram_collect_recipient_rows():
        if int(row.get('enabled') or 0) != 1:
            continue
        if str(row.get('alert_type') or '').strip().lower() != str(alert_type or '').strip().lower():
            continue
        rows.append(row)
    return rows


def _telegram_send_alert_to_recipients(alert_type, text_builder, *, recipients=None):
    sent = 0
    errors = []
    target_rows = recipients if recipients is not None else _telegram_subscription_targets(alert_type)
    now_iso = datetime.datetime.now().isoformat()
    if not target_rows:
        return {'sent': 0, 'errors': [], 'skipped': 'No Telegram recipients configured for this alert type'}

    conn = sqlite3.connect('searchRack.db')
    cur = conn.cursor()
    try:
        for row in target_rows:
            chat_id = str(row.get('chat_id') or '').strip()
            if not chat_id:
                continue
            try:
                body = str(text_builder(row) or '').strip()
                label = _telegram_recipient_label(row)
                text = f"Hi {label},\n\n{body}" if label else body
                disable_notification = _telegram_disable_notification_value(row.get('disable_notification'), default=True)
                ok, result = _telegram_send_message(chat_id, text, disable_notification=disable_notification)
                if ok:
                    sent += 1
                    cur.execute('''
                        UPDATE telegram_recipients
                        SET last_sent_at = ?, updated_at = ?
                        WHERE id = ?
                    ''', (now_iso, now_iso, row['id']))
                else:
                    errors.append({'chat_id': chat_id, 'error': result})
            except Exception as inner_e:
                errors.append({'chat_id': chat_id, 'error': str(inner_e)})
        conn.commit()
    finally:
        conn.close()
    return {'sent': sent, 'errors': errors}


def _telegram_format_age_hours(value):
    if not isinstance(value, (int, float)):
        return 'never'
    if value < 1:
        return f'{max(0, int(round(value * 60)))}m ago'
    if value < 48:
        return f'{value:.1f}h ago'
    return f'{value / 24.0:.1f}d ago'


def _telegram_system_uptime():
    try:
        seconds = int(float(Path('/proc/uptime').read_text().split()[0]))
    except Exception:
        try:
            import psutil
            seconds = max(0, int(time.time() - psutil.boot_time()))
        except Exception:
            return 'unknown'
    days, remainder = divmod(seconds, 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes = remainder // 60
    return f'{days}d {hours}h {minutes}m' if days else f'{hours}h {minutes}m'


def _telegram_latest_backup_file(directory, pattern='*.sqlite3.gz'):
    try:
        files = [path for path in Path(directory).glob(pattern) if path.is_file()]
        if not files:
            return None
        latest = max(files, key=lambda path: path.stat().st_mtime)
        stat = latest.stat()
        return {
            'path': str(latest),
            'name': latest.name,
            'mtime': float(stat.st_mtime),
            'age_hours': round(max(0, time.time() - stat.st_mtime) / 3600.0, 1),
            'size_mb': round(stat.st_size / (1024 ** 2), 1),
        }
    except Exception:
        return None


def _telegram_backup_log_result(path):
    try:
        log_path = Path(path)
        if not log_path.is_file():
            return {'exists': False, 'result': 'unknown', 'line': '', 'mtime': None}
        lines = log_path.read_text(encoding='utf-8', errors='replace').splitlines()
        last_line = next((line.strip() for line in reversed(lines) if line.strip()), '')
        lowered = last_line.casefold()
        if 'error:' in lowered or ' failed' in lowered:
            result = 'failed'
        elif 'backup ok:' in lowered or 'completed:' in lowered:
            result = 'ok'
        else:
            result = 'unknown'
        return {
            'exists': True,
            'result': result,
            'line': last_line[-800:],
            'mtime': float(log_path.stat().st_mtime),
        }
    except Exception as exc:
        return {'exists': False, 'result': 'unknown', 'line': str(exc), 'mtime': None}


def _telegram_backup_status():
    mount_path = Path(os.getenv('SWEETSHELVES_USB_MOUNT', '/media/dk/USB'))
    mounted = os.path.ismount(str(mount_path))
    writable = bool(mounted and os.access(str(mount_path), os.W_OK))
    backup_root = mount_path / 'sweetshelves-db-backups'
    daily = _telegram_latest_backup_file(backup_root / 'daily', 'searchRack-*.sqlite3.gz')
    weekly = _telegram_latest_backup_file(backup_root / 'weekly', '*.sqlite3.gz')

    schedules = ''
    if os.name != 'nt':
        try:
            proc = subprocess.run(
                ['crontab', '-l'],
                capture_output=True,
                text=True,
                timeout=3,
            )
            schedules = proc.stdout or ''
        except Exception:
            schedules = ''
    daily_scheduled = 'backup_daily_searchrack.sh' in schedules
    weekly_scheduled = 'backup_weekly_otherdbs.sh' in schedules
    daily_log = _telegram_backup_log_result(ss_config.BASE_DIR / 'logs' / 'backup_daily_searchrack.log')
    weekly_log = _telegram_backup_log_result(ss_config.BASE_DIR / 'logs' / 'backup_weekly_otherdbs.log')

    if not mounted:
        health = 'failed'
        status = 'USB backup drive is not mounted'
    elif not writable:
        health = 'failed'
        status = 'USB backup drive is read-only'
    elif not daily_scheduled:
        health = 'failed'
        status = 'Daily backup schedule is missing'
    elif daily_log.get('result') == 'failed':
        health = 'failed'
        status = 'Latest daily backup attempt failed'
    elif not daily:
        health = 'failed'
        status = 'No daily backup was found'
    elif daily['age_hours'] > 36:
        health = 'overdue'
        status = 'Daily backup is overdue'
    elif not weekly_scheduled:
        health = 'warning'
        status = 'Weekly backup schedule is missing'
    elif weekly_log.get('result') == 'failed':
        health = 'failed'
        status = 'Latest weekly backup attempt failed'
    elif not weekly or weekly['age_hours'] > (8 * 24):
        health = 'overdue'
        status = 'Weekly backup is overdue'
    else:
        health = 'ok'
        status = 'Automatic backups are healthy'

    return {
        'health': health,
        'status': status,
        'mount_path': str(mount_path),
        'mounted': mounted,
        'writable': writable,
        'daily_scheduled': daily_scheduled,
        'weekly_scheduled': weekly_scheduled,
        'daily': daily,
        'weekly': weekly,
        'daily_log': daily_log,
        'weekly_log': weekly_log,
    }


def _telegram_recent_metric_summary(key, hours=1):
    result = {'average': None, 'peak': None, 'minimum': None, 'count': 0}
    try:
        cutoff = int(time.time()) - max(1, int(hours)) * 3600
        with connect_db(ss_server_metrics._SERVER_METRICS_DB_NAME) as conn:
            cur = conn.cursor()
            ss_server_metrics._ensure_server_metrics_schema(cur)
            cur.execute(
                f'''
                SELECT AVG({key}), MAX({key}), MIN({key}), COUNT({key})
                FROM system_metric_snapshots
                WHERE collected_ts >= ? AND {key} IS NOT NULL
                ''',
                (cutoff,),
            )
            row = cur.fetchone()
        if row:
            result = {
                'average': round(float(row[0]), 1) if row[0] is not None else None,
                'peak': round(float(row[1]), 1) if row[1] is not None else None,
                'minimum': round(float(row[2]), 1) if row[2] is not None else None,
                'count': int(row[3] or 0),
            }
    except Exception:
        pass
    return result


def _telegram_metric_line(label, current, recent, unit='%'):
    current_text = f'{current:.1f}{unit}' if isinstance(current, (int, float)) else 'unavailable'
    if recent.get('count'):
        return (
            f'{label}: {current_text} '
            f'(1h avg {recent["average"]:.1f}{unit}, peak {recent["peak"]:.1f}{unit})'
        )
    return f'{label}: {current_text}'


def _telegram_api_status_text(probe=None):
    probe = probe or _api_status_probe()
    lines = ['Marketplace APIs']
    for key, label in (('ebay', 'eBay'), ('amazon', 'Amazon')):
        state = str(probe.get(key) or 'issue').strip().lower()
        marker = 'OK' if state == 'ok' else state.upper()
        lines.append(f'- {label}: {marker}')
        detail = str((probe.get('details') or {}).get(key) or '').strip()
        if detail and state != 'ok':
            lines.append(f'  {detail[:500]}')
    return '\n'.join(lines)


def _telegram_backup_status_text(status=None):
    status = status or _telegram_backup_status()
    lines = [
        'Backups',
        f'- Status: {status["status"]}',
        f'- USB: {"mounted and writable" if status["mounted"] and status["writable"] else "not ready"}',
    ]
    for key, label in (('daily', 'Daily searchRack'), ('weekly', 'Weekly databases')):
        item = status.get(key)
        if item:
            lines.append(
                f'- {label}: {_telegram_format_age_hours(item.get("age_hours"))}, '
                f'{item.get("size_mb", 0):.1f} MB'
            )
        else:
            lines.append(f'- {label}: not found')
    return '\n'.join(lines)


def _telegram_command_response(command):
    normalized = str(command or '').strip().casefold()
    if normalized in ('help', 'start'):
        return (
            'Sweet Shelves server commands\n'
            '- status: complete server update\n'
            '- cpu: CPU usage and one-hour trend\n'
            '- memory: memory usage and one-hour trend\n'
            '- temp or temperature: temperature and one-hour trend\n'
            '- backups: USB backup status\n'
            '- api: live Amazon and eBay status\n\n'
            'Commands work with or without a leading slash.'
        )

    if normalized == 'backups':
        return _telegram_backup_status_text()
    if normalized == 'api':
        return _telegram_api_status_text()

    snapshot = ss_server_metrics._collect_server_metrics_snapshot()
    cpu_recent = _telegram_recent_metric_summary('cpu_percent')
    memory_recent = _telegram_recent_metric_summary('memory_percent')
    temp_recent = _telegram_recent_metric_summary('temp_c')
    if normalized == 'cpu':
        return _telegram_metric_line('CPU', snapshot.get('cpu_percent'), cpu_recent)
    if normalized == 'memory':
        return _telegram_metric_line('Memory', snapshot.get('memory_percent'), memory_recent)
    if normalized in ('temp', 'temperature'):
        return _telegram_metric_line('Temperature', snapshot.get('temp_c'), temp_recent, '°C')
    if normalized != 'status':
        return ''

    disk = snapshot.get('disk_percent')
    disk_text = f'{disk:.1f}%' if isinstance(disk, (int, float)) else 'unavailable'
    backup_text = _telegram_backup_status_text()
    api_text = _telegram_api_status_text()
    return '\n'.join([
        'Sweet Shelves server status',
        f'Uptime: {_telegram_system_uptime()}',
        _telegram_metric_line('CPU', snapshot.get('cpu_percent'), cpu_recent),
        _telegram_metric_line('Memory', snapshot.get('memory_percent'), memory_recent),
        _telegram_metric_line('Temperature', snapshot.get('temp_c'), temp_recent, '°C'),
        f'Disk: {disk_text} used',
        '',
        backup_text,
        '',
        api_text,
    ])


def _telegram_authorized_chat_ids():
    return {
        str(row.get('chat_id') or '').strip()
        for row in _telegram_collect_recipient_rows()
        if int(row.get('enabled') or 0) == 1 and str(row.get('chat_id') or '').strip()
    }


def _telegram_store_recent_chat(update_id, message):
    chat = (message or {}).get('chat') or {}
    chat_id = str(chat.get('id') or '').strip()
    if not chat_id:
        return
    raw_date = message.get('date')
    try:
        message_at = datetime.datetime.fromtimestamp(
            int(raw_date),
            tz=datetime.timezone.utc,
        ).isoformat()
    except Exception:
        message_at = datetime.datetime.now(datetime.timezone.utc).isoformat()
    _ensure_telegram_tables()
    conn = sqlite3.connect('searchRack.db')
    try:
        conn.execute('''
            INSERT INTO telegram_recent_chats (
                chat_id, title, username, first_name, last_name, chat_type,
                last_text, last_message_at, last_update_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(chat_id) DO UPDATE SET
                title = excluded.title,
                username = excluded.username,
                first_name = excluded.first_name,
                last_name = excluded.last_name,
                chat_type = excluded.chat_type,
                last_text = excluded.last_text,
                last_message_at = excluded.last_message_at,
                last_update_id = excluded.last_update_id
        ''', (
            chat_id,
            str(chat.get('title') or ''),
            str(chat.get('username') or ''),
            str(chat.get('first_name') or ''),
            str(chat.get('last_name') or ''),
            str(chat.get('type') or ''),
            str(message.get('text') or '')[:1000],
            message_at,
            int(update_id or 0),
        ))
        conn.commit()
    finally:
        conn.close()


def _telegram_normalize_command(text):
    value = str(text or '').strip()
    if not value:
        return ''
    first = value.split()[0].casefold()
    if first.startswith('/'):
        first = first[1:]
    if '@' in first:
        first = first.split('@', 1)[0]
    aliases = {
        'temperature': 'temperature',
        'temp': 'temp',
        'cpu': 'cpu',
        'memory': 'memory',
        'mem': 'memory',
        'backup': 'backups',
        'backups': 'backups',
        'status': 'status',
        'api': 'api',
        'apis': 'api',
        'help': 'help',
        'start': 'start',
    }
    return aliases.get(first, '')


def _telegram_register_commands():
    base = _telegram_api_base()
    if not base:
        return
    commands = [
        {'command': 'status', 'description': 'Complete server status'},
        {'command': 'cpu', 'description': 'CPU usage and trend'},
        {'command': 'memory', 'description': 'Memory usage and trend'},
        {'command': 'temp', 'description': 'Temperature and trend'},
        {'command': 'backups', 'description': 'USB backup status'},
        {'command': 'api', 'description': 'Amazon and eBay API status'},
        {'command': 'help', 'description': 'Show available commands'},
    ]
    try:
        requests.post(
            f'{base}/setMyCommands',
            json={'commands': commands},
            timeout=15,
        )
    except Exception:
        pass


_telegram_command_thread_started = False


def telegram_command_worker():
    """Long-poll Telegram and answer server commands from configured chats."""
    import time as _time
    _telegram_register_commands()
    while True:
        base = _telegram_api_base()
        if not base:
            _time.sleep(60)
            continue
        try:
            raw_offset = _telegram_get_config_value('updates_offset', '')
            offset = int(raw_offset) if str(raw_offset).strip() else None
            initial_sync = offset is None
            params = {
                'timeout': 20,
                'limit': 100,
                'allowed_updates': json.dumps(['message', 'edited_message']),
            }
            if offset is not None:
                params['offset'] = offset
            response = requests.get(
                f'{base}/getUpdates',
                params=params,
                timeout=30,
            )
            data = response.json() if response.content else {}
            if not response.ok or not data.get('ok'):
                _time.sleep(30)
                continue

            updates = data.get('result') or []
            authorized = _telegram_authorized_chat_ids()
            newest_offset = offset
            for update in updates:
                update_id = int(update.get('update_id') or 0)
                newest_offset = max(newest_offset or 0, update_id + 1)
                message = update.get('message') or update.get('edited_message') or {}
                _telegram_store_recent_chat(update_id, message)
                chat_id = str((message.get('chat') or {}).get('id') or '').strip()
                command = _telegram_normalize_command(message.get('text'))
                # The first poll acknowledges the historical queue without
                # replaying old commands. New commands work from the next poll.
                if initial_sync or not command or chat_id not in authorized:
                    continue
                reply = _telegram_command_response(command)
                if reply:
                    _telegram_send_message(chat_id, reply, disable_notification=True)
            if newest_offset is not None and newest_offset != offset:
                _telegram_set_config_value('updates_offset', str(newest_offset))
        except Exception as exc:
            print(f'⚠️ Telegram command worker error: {exc}')
            _time.sleep(30)


def _start_telegram_command_thread():
    global _telegram_command_thread_started
    if _telegram_command_thread_started:
        return
    _telegram_command_thread_started = True
    thread = threading.Thread(target=telegram_command_worker, daemon=True)
    thread.start()
    print('🚀 Telegram command thread started')


def telegram_page():
    return render_template('telegram.html')


def api_telegram_settings():
    try:
        _ensure_telegram_tables()
        if request.method == 'GET':
            config_token = _telegram_get_bot_token()
            token_suffix = config_token[-4:] if len(config_token) >= 4 else config_token
            recipients = _telegram_collect_recipient_rows()
            return jsonify({
                'success': True,
                'has_token': bool(config_token),
                'token_suffix': token_suffix,
                'recipients': recipients
            })

        data = request.get_json() or {}
        bot_token = str(data.get('bot_token') or '').strip()
        if bot_token:
            _telegram_set_config_value('bot_token', bot_token)
        recipients = data.get('recipients', [])
        conn = sqlite3.connect('searchRack.db')
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute('''
            SELECT chat_id, alert_type, last_sent_at, last_test_sent, created_at
            FROM telegram_recipients
        ''')
        existing_rows = {
            (
                str(row['chat_id'] or '').strip(),
                str(row['alert_type'] or '').strip().lower(),
            ): dict(row)
            for row in cur.fetchall()
        }
        cur.execute('DELETE FROM telegram_recipients')
        now_iso = datetime.datetime.now().isoformat()
        for recipient in recipients:
            if not isinstance(recipient, dict):
                continue
            chat_id = str(recipient.get('chat_id') or '').strip()
            alert_type = str(recipient.get('alert_type') or 'inventory').strip().lower()
            if not chat_id:
                continue
            display_name = str(recipient.get('display_name') or '').strip()
            interval = str(recipient.get('interval') or '1d').strip()
            enabled = 1 if str(recipient.get('enabled', 1)).strip().lower() not in ('0', 'false', 'no', 'off') else 0
            disable_notification = 1 if _telegram_disable_notification_value(recipient.get('disable_notification'), default=True) else 0
            previous = existing_rows.get((chat_id, alert_type), {})
            last_sent_at = str(
                recipient.get('last_sent_at')
                or previous.get('last_sent_at')
                or ''
            ).strip() or None
            last_test_sent = str(
                recipient.get('last_test_sent')
                or previous.get('last_test_sent')
                or ''
            ).strip() or None
            created_at = str(previous.get('created_at') or now_iso)
            cur.execute('''
                INSERT INTO telegram_recipients
                (
                    chat_id, display_name, alert_type, interval, enabled,
                    disable_notification, last_sent_at, last_test_sent,
                    created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                chat_id, display_name, alert_type, interval, enabled,
                disable_notification, last_sent_at, last_test_sent,
                created_at, now_iso
            ))
        conn.commit()
        conn.close()
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': ss_errors._safe_error(e)}), 500


def api_telegram_recent_chats():
    try:
        limit = max(1, min(50, int(request.args.get('limit', 20))))
        _ensure_telegram_tables()
        conn = sqlite3.connect('searchRack.db')
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute('''
            SELECT
                chat_id, title, username, first_name, last_name,
                chat_type AS type, last_text AS text,
                last_message_at AS date
            FROM telegram_recent_chats
            ORDER BY COALESCE(last_update_id, 0) DESC
            LIMIT ?
        ''', (limit,))
        chats = [dict(row) for row in cur.fetchall()]
        conn.close()
        return jsonify({'success': True, 'chats': chats})
    except Exception as e:
        return jsonify({'success': False, 'error': ss_errors._safe_error(e)}), 500


def api_telegram_send_test():
    try:
        data = request.get_json() or {}
        chat_ids = data.get('chat_ids', [])
        message = str(data.get('message') or 'Telegram test message from Sweet Shelves').strip()
        if not chat_ids:
            return jsonify({'success': False, 'error': 'No chat_ids provided'}), 400
        results = []
        for chat_id in chat_ids:
            # Test messages should always notify normally, regardless of the recipient's silent alert setting.
            ok, result = _telegram_send_message(chat_id, message, disable_notification=False)
            results.append({'chat_id': chat_id, 'success': ok, 'result': result})
        return jsonify({'success': True, 'results': results, 'disable_notification': False})
    except Exception as e:
        return jsonify({'success': False, 'error': ss_errors._safe_error(e)}), 500


def api_telegram_send_inventory():
    try:
        issues = ss_inventory_alerts.collect_inventory_mismatches()
        if not issues.get('has_issues'):
            return jsonify({'success': True, 'message': 'No inventory issues found'}), 200

        def _build_text(_recipient):
            return ss_inventory_alerts.generate_inventory_email_plain(issues)

        recipients = _telegram_subscription_targets('inventory')
        result = _telegram_send_alert_to_recipients('inventory', _build_text, recipients=recipients)
        return jsonify({'success': True, 'sent': result['sent'], 'errors': result.get('errors', [])})
    except Exception as e:
        return jsonify({'success': False, 'error': ss_errors._safe_error(e)}), 500


def api_telegram_send_health():
    try:
        stats = ss_health.collect_health_stats()

        def _build_text(_recipient):
            return ss_health.generate_health_email_plain(stats)

        recipients = _telegram_subscription_targets('health')
        result = _telegram_send_alert_to_recipients('health', _build_text, recipients=recipients)
        return jsonify({'success': True, 'sent': result['sent'], 'errors': result.get('errors', [])})
    except Exception as e:
        return jsonify({'success': False, 'error': ss_errors._safe_error(e)}), 500


def api_telegram_send_sync_overdue():
    try:
        alert = ss_sync._sync_manager_overdue_alert()
        if not alert:
            return jsonify({'success': True, 'message': 'No sync overdue alert is currently active'}), 200

        def _build_text(_recipient):
            lines = [
                'SYNC OVERDUE ALERT',
                f"Overdue count: {alert.get('overdue_count', 0)}",
                f"Last sync: {alert.get('last_sync_at') or 'unknown'}",
                f"Threshold: {alert.get('threshold_minutes') or 'unknown'} minutes",
                '',
                str(alert.get('message') or '').strip()
            ]
            return '\n'.join(line for line in lines if line is not None)

        recipients = _telegram_subscription_targets('sync_overdue')
        result = _telegram_send_alert_to_recipients('sync_overdue', _build_text, recipients=recipients)
        return jsonify({'success': True, 'sent': result['sent'], 'errors': result.get('errors', [])})
    except Exception as e:
        return jsonify({'success': False, 'error': ss_errors._safe_error(e)}), 500


def api_telegram_send_api_issue():
    try:
        alert = _collect_api_issue_alert()
        if not alert.get('has_issues'):
            return jsonify({'success': True, 'message': 'No eBay or Amazon API issues are currently active'}), 200

        def _build_text(_recipient):
            return _generate_api_issue_text(alert)

        recipients = _telegram_subscription_targets('api_issue')
        result = _telegram_send_alert_to_recipients('api_issue', _build_text, recipients=recipients)
        return jsonify({'success': True, 'sent': result['sent'], 'errors': result.get('errors', [])})
    except Exception as e:
        return jsonify({'success': False, 'error': ss_errors._safe_error(e)}), 500


def _telegram_signature_event_ready(alert_key, signature, metadata=None):
    """Return true once for each new event signature; initialize old events silently."""
    signature = str(signature or '').strip()
    if not signature:
        return False
    _ensure_telegram_tables()
    now_iso = datetime.datetime.now().astimezone().isoformat()
    conn = sqlite3.connect('searchRack.db')
    conn.row_factory = sqlite3.Row
    try:
        cur = conn.cursor()
        cur.execute(
            'SELECT signature, notified_signature FROM telegram_alert_state WHERE alert_key = ?',
            (alert_key,),
        )
        row = cur.fetchone()
        if row is None:
            cur.execute('''
                INSERT INTO telegram_alert_state (
                    alert_key, signature, consecutive_count, first_seen_at,
                    last_seen_at, notified_signature, notified_at, metadata_json
                ) VALUES (?, ?, 1, ?, ?, ?, ?, ?)
            ''', (
                alert_key, signature, now_iso, now_iso, signature, now_iso,
                json.dumps(metadata or {}, default=str)
            ))
            conn.commit()
            return False

        cur.execute('''
            UPDATE telegram_alert_state
            SET signature = ?, consecutive_count = 1, first_seen_at = ?,
                last_seen_at = ?, metadata_json = ?
            WHERE alert_key = ?
        ''', (
            signature, now_iso, now_iso,
            json.dumps(metadata or {}, default=str),
            alert_key
        ))
        conn.commit()
        return str(row['notified_signature'] or '') != signature
    finally:
        conn.close()


def _telegram_server_monitor_events():
    """Create deduplicated events for sustained resource spikes and USB backups."""
    events = []
    snapshot = ss_server_metrics._collect_server_metrics_snapshot()
    thresholds = {
        'cpu': float(os.getenv('TELEGRAM_CPU_ALERT_PERCENT', '90')),
        'memory': float(os.getenv('TELEGRAM_MEMORY_ALERT_PERCENT', '90')),
        'temperature': float(os.getenv('TELEGRAM_TEMP_ALERT_C', '78')),
    }
    resource_checks = (
        ('cpu', 'CPU', snapshot.get('cpu_percent'), thresholds['cpu'], '%'),
        ('memory', 'Memory', snapshot.get('memory_percent'), thresholds['memory'], '%'),
        ('temperature', 'Temperature', snapshot.get('temp_c'), thresholds['temperature'], '°C'),
    )
    for key, label, value, threshold, unit in resource_checks:
        is_high = isinstance(value, (int, float)) and value >= threshold
        ready = _telegram_alert_state_update(
            f'server:{key}:high',
            'high' if is_high else '',
            required_count=3,
            minimum_age_minutes=15,
            metadata={'value': value, 'threshold': threshold},
        )
        if ready:
            events.append({
                'alert_key': f'server:{key}:high',
                'text': (
                    f'SERVER HEALTH ALERT\n'
                    f'{label} has remained unusually high for at least 15 minutes.\n'
                    f'Current: {value:.1f}{unit}\n'
                    f'Alert threshold: {threshold:.1f}{unit}'
                ),
            })

    backup = _telegram_backup_status()
    unhealthy = backup.get('health') != 'ok'
    backup_ready = _telegram_alert_state_update(
        'backup:unhealthy',
        str(backup.get('health') or 'failed') if unhealthy else '',
        required_count=2,
        minimum_age_minutes=8,
        metadata=backup,
    )
    if backup_ready:
        events.append({
            'alert_key': 'backup:unhealthy',
            'text': f'BACKUP ALERT\n{_telegram_backup_status_text(backup)}',
        })

    for key, label in (('daily', 'Daily searchRack'), ('weekly', 'Weekly databases')):
        log_result = backup.get(f'{key}_log') or {}
        if log_result.get('result') != 'ok' or not log_result.get('mtime'):
            continue
        signature = f'{int(log_result["mtime"])}:{log_result.get("line", "")}'
        alert_key = f'backup:{key}:completed'
        if _telegram_signature_event_ready(alert_key, signature, log_result):
            item = backup.get(key) or {}
            events.append({
                'alert_key': alert_key,
                'text': (
                    f'BACKUP COMPLETED\n'
                    f'{label} backup completed successfully.\n'
                    f'File: {item.get("name") or "available on USB"}\n'
                    f'Size: {item.get("size_mb", 0):.1f} MB\n'
                    f'Time: {_telegram_format_age_hours(item.get("age_hours"))}'
                ),
            })
    return events


def _telegram_send_server_events(events):
    if not events:
        return {'sent': 0, 'errors': []}
    recipients = _telegram_subscription_targets('health')
    if not recipients:
        return {'sent': 0, 'errors': [], 'skipped': 'No health recipients configured'}
    body = '\n\n'.join(str(event.get('text') or '').strip() for event in events)
    result = _telegram_send_alert_to_recipients(
        'health',
        lambda _recipient: body,
        recipients=recipients,
    )
    if result.get('sent'):
        _telegram_alert_state_mark_notified(
            [event.get('alert_key') for event in events]
        )
    return result


_telegram_alert_thread_started = False


def telegram_alert_worker():
    """Background worker that sends Telegram alerts to per-user subscriptions."""
    import time
    while True:
        try:
            recipients = _telegram_collect_recipient_rows()
            if not recipients or not _telegram_get_bot_token():
                time.sleep(300)
                continue

            now = datetime.datetime.now()
            sent_any = False

            # Server events are independent of periodic report intervals. Alerts
            # are sustained and persisted, while backup completions are emitted
            # once for each new successful run.
            server_events = _telegram_server_monitor_events()
            server_result = _telegram_send_server_events(server_events)
            sent_any = sent_any or bool(server_result.get('sent'))

            for alert_type in ('inventory', 'health', 'sync_overdue', 'api_issue'):
                relevant = []
                for row in recipients:
                    if int(row.get('enabled') or 0) != 1:
                        continue
                    if str(row.get('alert_type') or '').strip().lower() != alert_type:
                        continue
                    interval_minutes = _telegram_parse_interval_minutes(row.get('interval'))
                    last_sent_raw = str(row.get('last_sent_at') or '').strip()
                    if last_sent_raw:
                        try:
                            last_sent_dt = datetime.datetime.fromisoformat(last_sent_raw)
                            if (now - last_sent_dt).total_seconds() < interval_minutes * 60:
                                continue
                        except Exception:
                            pass
                    relevant.append(row)

                # Keep API confirmation progressing every worker pass instead
                # of tying it to a recipient's delivery interval.
                api_alert = None
                if alert_type == 'api_issue' and any(
                    int(row.get('enabled') or 0) == 1
                    and str(row.get('alert_type') or '').strip().lower() == 'api_issue'
                    for row in recipients
                ):
                    api_alert = _collect_api_issue_alert(require_confirmation=True)

                if alert_type == 'health' and server_result.get('sent'):
                    # The event message already updated health recipients and
                    # contains the actionable status; avoid a second report in
                    # the same worker pass.
                    continue
                if not relevant:
                    continue

                if alert_type == 'inventory':
                    issues = ss_inventory_alerts.collect_inventory_mismatches()
                    if not issues.get('has_issues'):
                        continue

                    def _build_text(_recipient):
                        return ss_inventory_alerts.generate_inventory_email_plain(issues)

                    result = _telegram_send_alert_to_recipients(alert_type, _build_text, recipients=relevant)
                    sent_any = sent_any or bool(result.get('sent'))
                elif alert_type == 'health':
                    stats = ss_health.collect_health_stats()

                    def _build_text(_recipient):
                        return ss_health.generate_health_email_plain(stats)

                    result = _telegram_send_alert_to_recipients(alert_type, _build_text, recipients=relevant)
                    sent_any = sent_any or bool(result.get('sent'))
                elif alert_type == 'sync_overdue':
                    alert = ss_sync._sync_manager_overdue_alert()
                    if not alert:
                        continue

                    def _build_text(_recipient):
                        lines = [
                            'SYNC OVERDUE ALERT',
                            f"Overdue count: {alert.get('overdue_count', 0)}",
                            f"Last sync: {alert.get('last_sync_at') or 'unknown'}",
                            f"Threshold: {alert.get('threshold_minutes') or 'unknown'} minutes",
                            '',
                            str(alert.get('message') or '').strip()
                        ]
                        return '\n'.join(line for line in lines if line is not None)

                    result = _telegram_send_alert_to_recipients(alert_type, _build_text, recipients=relevant)
                    sent_any = sent_any or bool(result.get('sent'))
                elif alert_type == 'api_issue':
                    alert = api_alert or _collect_api_issue_alert(require_confirmation=True)
                    if not alert.get('has_issues'):
                        continue

                    def _build_text(_recipient):
                        return _generate_api_issue_text(alert)

                    result = _telegram_send_alert_to_recipients(alert_type, _build_text, recipients=relevant)
                    sent_any = sent_any or bool(result.get('sent'))
                    if result.get('sent'):
                        _telegram_alert_state_mark_notified([
                            issue.get('alert_key')
                            for issue in alert.get('issues') or []
                        ])

            time.sleep(300 if sent_any else 600)
        except Exception as e:
            print(f"❌ Telegram alert worker error: {e}")
            import traceback
            traceback.print_exc()
            time.sleep(300)


def _start_telegram_alert_thread():
    """Start the Telegram alert background thread."""
    global _telegram_alert_thread_started
    if _telegram_alert_thread_started:
        return
    _telegram_alert_thread_started = True
    telegram_thread = threading.Thread(target=telegram_alert_worker, daemon=True)
    telegram_thread.start()
    print("🚀 Telegram alert thread started")
