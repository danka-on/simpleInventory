"""Manifest google for Sweet Shelves."""

import base64
import datetime
import json
import os
import requests
import sqlite3
import threading
import time
from decimal import Decimal, ROUND_HALF_UP
from . import (
    bulk_manifest as ss_bulk_manifest, config as ss_config, database as ss_database, normalization as
    ss_normalization, runtime as ss_runtime,
)


_bulk_manifest_google_jobs = {}


_bulk_manifest_google_jobs_lock = threading.Lock()


def _bulk_manifest_google_share_email():
    candidate = str(
        os.getenv('GOOGLE_SHEETS_SHARE_EMAIL')
        or os.getenv('SMTP_USER')
        or ''
    ).strip()
    return candidate if '@' in candidate else ''


def _bulk_manifest_google_shared_spreadsheet_id():
    raw_value = str(
        os.getenv('GOOGLE_SHEETS_SHARED_SPREADSHEET_ID')
        or os.getenv('GOOGLE_SHEETS_SPREADSHEET_ID')
        or ''
    ).strip()
    if not raw_value:
        return ''
    if '/d/' in raw_value:
        tail = raw_value.split('/d/', 1)[1]
        return tail.split('/', 1)[0].split('?', 1)[0].split('#', 1)[0].strip()
    return raw_value


def _bulk_manifest_google_tab_title(manifest_id, shared_mode=False):
    manifest_key = max(0, ss_normalization._coerce_int(manifest_id, 0))
    base = f'Manifest {manifest_key}' if shared_mode else 'Manifest'
    cleaned = ''.join(ch for ch in base if ch not in ('[', ']', ':', '*', '?', '/', '\\'))
    return cleaned[:100].strip() or f'Manifest {manifest_key}'


def _bulk_manifest_google_timeout(default_seconds=8):
    raw_value = str(os.getenv('GOOGLE_API_TIMEOUT_SECONDS') or '').strip()
    try:
        timeout_seconds = int(raw_value or default_seconds)
    except Exception:
        timeout_seconds = default_seconds
    return max(3, min(20, timeout_seconds))


def _bulk_manifest_google_request_error(prefix, exc):
    detail = str(exc or '').strip() or exc.__class__.__name__
    lower = detail.lower()
    if isinstance(exc, requests.exceptions.Timeout):
        return f'{prefix}: timed out contacting Google'
    if isinstance(exc, requests.exceptions.ConnectionError):
        dns_signals = (
            'name or service not known',
            'name resolution',
            'temporary failure in name resolution',
            'nodename nor servname',
            'getaddrinfo failed'
        )
        if any(signal in lower for signal in dns_signals):
            return f'{prefix}: DNS lookup for Google failed'
        return f'{prefix}: could not connect to Google'
    return f'{prefix}: {detail}'


def _bulk_manifest_google_access_token(scopes=None):
    creds_path = ss_config.BASE_DIR / 'credentials.json'
    if not creds_path.exists():
        raise RuntimeError('Google credentials.json not found')

    with open(creds_path, 'r', encoding='utf-8') as fh:
        creds = json.load(fh)

    if str(creds.get('type') or '').strip() != 'service_account':
        raise RuntimeError('credentials.json is not a Google service account key')

    import json as _json
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding

    token_uri = str(creds.get('token_uri') or 'https://oauth2.googleapis.com/token').strip()
    client_email = str(creds.get('client_email') or '').strip()
    private_key_pem = str(creds.get('private_key') or '').strip()
    if not client_email or not private_key_pem:
        raise RuntimeError('Google service account key is missing required fields')

    scope_list = list(scopes or (
        'https://www.googleapis.com/auth/spreadsheets',
        'https://www.googleapis.com/auth/drive.file'
    ))

    private_key = serialization.load_pem_private_key(
        private_key_pem.encode('utf-8'),
        password=None
    )

    now = int(time.time())
    header = {'alg': 'RS256', 'typ': 'JWT'}
    claim = {
        'iss': client_email,
        'scope': ' '.join(scope_list),
        'aud': token_uri,
        'iat': now,
        'exp': now + 3600
    }

    def _b64url(data):
        return base64.urlsafe_b64encode(data).rstrip(b'=')

    unsigned = b'.'.join((
        _b64url(_json.dumps(header, separators=(',', ':')).encode('utf-8')),
        _b64url(_json.dumps(claim, separators=(',', ':')).encode('utf-8'))
    ))
    signature = private_key.sign(unsigned, padding.PKCS1v15(), hashes.SHA256())
    assertion = unsigned + b'.' + _b64url(signature)

    try:
        resp = requests.post(
            token_uri,
            data={
                'grant_type': 'urn:ietf:params:oauth:grant-type:jwt-bearer',
                'assertion': assertion.decode('utf-8')
            },
            timeout=_bulk_manifest_google_timeout(8)
        )
    except requests.RequestException as exc:
        raise RuntimeError(_bulk_manifest_google_request_error('Google token request failed', exc)) from exc
    try:
        payload = resp.json()
    except Exception:
        payload = {}
    if not resp.ok:
        raise RuntimeError(
            'Google token request failed: ' +
            str(payload.get('error_description') or payload.get('error') or resp.text[:240] or 'unknown error')
        )
    token = str(payload.get('access_token') or '').strip()
    if not token:
        raise RuntimeError('Google token response did not include access_token')
    return token


def _bulk_manifest_google_request(method, url, *, token, params=None, json_body=None, timeout=45):
    headers = {'Authorization': f'Bearer {token}'}
    if json_body is not None:
        headers['Content-Type'] = 'application/json'
    effective_timeout = timeout if timeout is not None else _bulk_manifest_google_timeout(8)
    try:
        resp = requests.request(
            method,
            url,
            headers=headers,
            params=params,
            json=json_body,
            timeout=effective_timeout
        )
    except requests.RequestException as exc:
        raise RuntimeError(_bulk_manifest_google_request_error('Google API request failed', exc)) from exc
    try:
        data = resp.json()
    except Exception:
        data = None
    if not resp.ok:
        message = ''
        if isinstance(data, dict):
            error_obj = data.get('error')
            if isinstance(error_obj, dict):
                message = str(error_obj.get('message') or '').strip()
            elif error_obj:
                message = str(error_obj).strip()
        if not message:
            message = str(resp.text[:300] or f'HTTP {resp.status_code}').strip()
        raise RuntimeError(message)
    return data if data is not None else {}


def _bulk_manifest_google_health(include_token_probe=False):
    creds_path = ss_config.BASE_DIR / 'credentials.json'
    health = {
        'credentials_present': creds_path.exists(),
        'service_account': False,
        'client_email_present': False,
        'cryptography_ok': False,
        'share_email_configured': bool(_bulk_manifest_google_share_email()),
        'shared_spreadsheet_configured': bool(_bulk_manifest_google_shared_spreadsheet_id()),
        'token_ok': None
    }

    creds = {}
    if creds_path.exists():
        try:
            with open(creds_path, 'r', encoding='utf-8') as fh:
                creds = json.load(fh)
            health['service_account'] = str(creds.get('type') or '').strip() == 'service_account'
            health['client_email_present'] = bool(str(creds.get('client_email') or '').strip())
        except Exception as exc:
            health['credentials_error'] = str(exc).strip() or 'Failed to read credentials.json'
    else:
        health['credentials_error'] = 'Google credentials.json not found'

    try:
        from cryptography.hazmat.primitives import hashes, serialization  # noqa: F401
        from cryptography.hazmat.primitives.asymmetric import padding  # noqa: F401
        health['cryptography_ok'] = True
    except Exception as exc:
        health['cryptography_error'] = str(exc).strip() or 'cryptography import failed'

    if include_token_probe:
        try:
            _bulk_manifest_google_access_token()
            health['token_ok'] = True
        except Exception as exc:
            health['token_ok'] = False
            health['token_error'] = str(exc).strip() or 'Google token probe failed'

    return health


def _bulk_manifest_google_sheet_range(sheet_title, cell_range='A1'):
    title = str(sheet_title or 'Manifest').replace("'", "''")
    return f"'{title}'!{cell_range}"


def _bulk_manifest_google_get_spreadsheet(spreadsheet_id, token):
    data = _bulk_manifest_google_request(
        'GET',
        f'https://sheets.googleapis.com/v4/spreadsheets/{spreadsheet_id}',
        token=token,
        params={'fields': 'spreadsheetId,spreadsheetUrl,sheets(properties(sheetId,title))'}
    )
    return data if isinstance(data, dict) else {}


def _bulk_manifest_google_ensure_manifest_sheet(spreadsheet_id, token, desired_title='Manifest', allow_rename_singleton=True):
    desired_title = str(desired_title or 'Manifest').strip() or 'Manifest'
    meta = _bulk_manifest_google_get_spreadsheet(spreadsheet_id, token)
    spreadsheet_url = str(meta.get('spreadsheetUrl') or '').strip() or f'https://docs.google.com/spreadsheets/d/{spreadsheet_id}/edit'

    sheets = meta.get('sheets')
    if not isinstance(sheets, list):
        sheets = []

    for sheet in sheets:
        props = sheet.get('properties') if isinstance(sheet, dict) else {}
        title = str((props or {}).get('title') or '').strip()
        sheet_id = ss_normalization._coerce_int((props or {}).get('sheetId'), -1)
        if title == desired_title and sheet_id >= 0:
            return {
                'spreadsheet_id': str(spreadsheet_id).strip(),
                'spreadsheet_url': spreadsheet_url,
                'sheet_id': sheet_id,
                'sheet_title': desired_title
            }

    if allow_rename_singleton and len(sheets) == 1:
        props = sheets[0].get('properties') if isinstance(sheets[0], dict) else {}
        existing_id = ss_normalization._coerce_int((props or {}).get('sheetId'), -1)
        existing_title = str((props or {}).get('title') or '').strip()
        if existing_id >= 0 and existing_title and existing_title != desired_title:
            _bulk_manifest_google_request(
                'POST',
                f'https://sheets.googleapis.com/v4/spreadsheets/{spreadsheet_id}:batchUpdate',
                token=token,
                json_body={
                    'requests': [{
                        'updateSheetProperties': {
                            'properties': {'sheetId': existing_id, 'title': desired_title},
                            'fields': 'title'
                        }
                    }]
                }
            )
            return {
                'spreadsheet_id': str(spreadsheet_id).strip(),
                'spreadsheet_url': spreadsheet_url,
                'sheet_id': existing_id,
                'sheet_title': desired_title
            }

    created = _bulk_manifest_google_request(
        'POST',
        f'https://sheets.googleapis.com/v4/spreadsheets/{spreadsheet_id}:batchUpdate',
        token=token,
        json_body={
            'requests': [{
                'addSheet': {
                    'properties': {'title': desired_title}
                }
            }]
        }
    )
    replies = created.get('replies') if isinstance(created, dict) else []
    new_sheet_id = -1
    if isinstance(replies, list):
        for reply in replies:
            add_sheet = reply.get('addSheet') if isinstance(reply, dict) else {}
            props = add_sheet.get('properties') if isinstance(add_sheet, dict) else {}
            if str(props.get('title') or '').strip() == desired_title:
                new_sheet_id = ss_normalization._coerce_int(props.get('sheetId'), -1)
                break

    if new_sheet_id < 0:
        refreshed = _bulk_manifest_google_get_spreadsheet(spreadsheet_id, token)
        refreshed_sheets = refreshed.get('sheets')
        if isinstance(refreshed_sheets, list):
            for sheet in refreshed_sheets:
                props = sheet.get('properties') if isinstance(sheet, dict) else {}
                if str((props or {}).get('title') or '').strip() == desired_title:
                    new_sheet_id = ss_normalization._coerce_int((props or {}).get('sheetId'), -1)
                    break
        spreadsheet_url = str(refreshed.get('spreadsheetUrl') or spreadsheet_url).strip() or spreadsheet_url

    if new_sheet_id < 0:
        raise RuntimeError('Google Sheets export could not find or create the Manifest tab')

    return {
        'spreadsheet_id': str(spreadsheet_id).strip(),
        'spreadsheet_url': spreadsheet_url,
        'sheet_id': new_sheet_id,
        'sheet_title': desired_title
    }


def _bulk_manifest_google_style_sheet(
    spreadsheet_id,
    worksheet_id,
    worksheet_title,
    sheet_title,
    token,
    update_spreadsheet_title=True,
    row_count=0,
    image_row_indexes=None,
    emphasis_row_indexes=None,
    header_row_index=0,
    frozen_row_count=1
):
    row_count = max(0, ss_normalization._coerce_int(row_count, 0))
    totals_start_row = max(0, row_count - 1)
    header_row_index = max(0, min(totals_start_row, ss_normalization._coerce_int(header_row_index, 0)))
    frozen_row_count = max(1, min(row_count or 1, ss_normalization._coerce_int(frozen_row_count, 1)))
    normal_row_height = 21
    summary_row_height = 30
    image_row_height = 124
    thumb_column_index = 6
    thumb_column_width = 100
    image_rows = sorted({
        idx for idx in [
            ss_normalization._coerce_int(value, -1) for value in (image_row_indexes or [])
        ]
        if 0 < idx < totals_start_row
    })
    emphasis_rows = sorted({
        idx for idx in [
            ss_normalization._coerce_int(value, -1) for value in (emphasis_row_indexes or [])
        ]
        if header_row_index < idx < totals_start_row
    })
    requests_payload = []
    if update_spreadsheet_title:
        requests_payload.append({'updateSpreadsheetProperties': {
            'properties': {'title': sheet_title},
            'fields': 'title'
        }})
    requests_payload.extend([
        {'repeatCell': {
            'range': {'sheetId': worksheet_id, 'startRowIndex': header_row_index, 'endRowIndex': header_row_index + 1},
            'cell': {'userEnteredFormat': {'textFormat': {'bold': True}}},
            'fields': 'userEnteredFormat.textFormat.bold'
        }},
        {'updateSheetProperties': {
            'properties': {
                'sheetId': worksheet_id,
                'title': worksheet_title,
                'gridProperties': {'frozenRowCount': frozen_row_count}
            },
            'fields': 'title,gridProperties.frozenRowCount'
        }}
    ])
    if row_count > 0:
        requests_payload.append({'updateDimensionProperties': {
            'range': {
                'sheetId': worksheet_id,
                'dimension': 'ROWS',
                'startIndex': 0,
                'endIndex': row_count
            },
            'properties': {'pixelSize': normal_row_height},
            'fields': 'pixelSize'
        }})
        requests_payload.append({'updateDimensionProperties': {
            'range': {
                'sheetId': worksheet_id,
                'dimension': 'ROWS',
                'startIndex': 0,
                'endIndex': 1
            },
            'properties': {'pixelSize': summary_row_height},
            'fields': 'pixelSize'
        }})
    requests_payload.append({'updateDimensionProperties': {
        'range': {
            'sheetId': worksheet_id,
            'dimension': 'COLUMNS',
            'startIndex': thumb_column_index,
            'endIndex': thumb_column_index + 1
        },
        'properties': {'pixelSize': thumb_column_width},
        'fields': 'pixelSize'
    }})
    if image_rows:
        start_idx = image_rows[0]
        end_idx = start_idx + 1
        contiguous_ranges = []
        for idx in image_rows[1:]:
            if idx == end_idx:
                end_idx += 1
                continue
            contiguous_ranges.append((start_idx, end_idx))
            start_idx = idx
            end_idx = idx + 1
        contiguous_ranges.append((start_idx, end_idx))
        for start_idx, end_idx in contiguous_ranges:
            requests_payload.append({'updateDimensionProperties': {
                'range': {
                    'sheetId': worksheet_id,
                    'dimension': 'ROWS',
                    'startIndex': start_idx,
                    'endIndex': end_idx
                },
                'properties': {'pixelSize': image_row_height},
                'fields': 'pixelSize'
            }})
    for idx in emphasis_rows:
        requests_payload.append({'repeatCell': {
            'range': {
                'sheetId': worksheet_id,
                'startRowIndex': idx,
                'endRowIndex': idx + 1,
                'startColumnIndex': 4,
                'endColumnIndex': 6
            },
            'cell': {'userEnteredFormat': {'textFormat': {'bold': True}}},
            'fields': 'userEnteredFormat.textFormat.bold'
        }})
    if row_count > 1:
        totals_range = {
            'sheetId': worksheet_id,
            'startRowIndex': totals_start_row,
            'endRowIndex': row_count,
            'startColumnIndex': 2,
            'endColumnIndex': 6
        }
        top_summary_range = {
            'sheetId': worksheet_id,
            'startRowIndex': 0,
            'endRowIndex': 1,
            'startColumnIndex': 0,
            'endColumnIndex': 6
        }
        requests_payload.extend([
            {'repeatCell': {
                'range': top_summary_range,
                'cell': {'userEnteredFormat': {
                    'backgroundColor': {'red': 1.0, 'green': 0.949, 'blue': 0.8},
                    'textFormat': {'bold': True, 'fontSize': 14}
                }},
                'fields': 'userEnteredFormat.backgroundColor,userEnteredFormat.textFormat.bold,userEnteredFormat.textFormat.fontSize'
            }},
            {'updateBorders': {
                'range': top_summary_range,
                'top': {'style': 'SOLID', 'color': {'red': 0.6, 'green': 0.52, 'blue': 0.18}},
                'bottom': {'style': 'SOLID', 'color': {'red': 0.6, 'green': 0.52, 'blue': 0.18}},
                'left': {'style': 'SOLID', 'color': {'red': 0.6, 'green': 0.52, 'blue': 0.18}},
                'right': {'style': 'SOLID', 'color': {'red': 0.6, 'green': 0.52, 'blue': 0.18}},
                'innerVertical': {'style': 'SOLID', 'color': {'red': 0.82, 'green': 0.72, 'blue': 0.25}}
            }},
            {'repeatCell': {
                'range': totals_range,
                'cell': {'userEnteredFormat': {
                    'backgroundColor': {'red': 1.0, 'green': 0.949, 'blue': 0.8},
                    'textFormat': {'bold': True}
                }},
                'fields': 'userEnteredFormat.backgroundColor,userEnteredFormat.textFormat.bold'
            }},
            {'updateBorders': {
                'range': totals_range,
                'top': {'style': 'SOLID', 'color': {'red': 0.6, 'green': 0.52, 'blue': 0.18}},
                'bottom': {'style': 'SOLID', 'color': {'red': 0.6, 'green': 0.52, 'blue': 0.18}},
                'left': {'style': 'SOLID', 'color': {'red': 0.6, 'green': 0.52, 'blue': 0.18}},
                'right': {'style': 'SOLID', 'color': {'red': 0.6, 'green': 0.52, 'blue': 0.18}},
                'innerVertical': {'style': 'SOLID', 'color': {'red': 0.82, 'green': 0.72, 'blue': 0.25}}
            }}
        ])
    _bulk_manifest_google_request(
        'POST',
        f'https://sheets.googleapis.com/v4/spreadsheets/{spreadsheet_id}:batchUpdate',
        token=token,
        json_body={'requests': requests_payload}
    )


def _bulk_manifest_google_write_sheet_rows(spreadsheet_id, worksheet_title, rows, token):
    a1_full_range = _bulk_manifest_google_sheet_range(worksheet_title, 'A:O')
    a1_start = _bulk_manifest_google_sheet_range(worksheet_title, 'A1')

    _bulk_manifest_google_request(
        'POST',
        f'https://sheets.googleapis.com/v4/spreadsheets/{spreadsheet_id}/values:batchClear',
        token=token,
        json_body={'ranges': [a1_full_range]}
    )
    _bulk_manifest_google_request(
        'POST',
        f'https://sheets.googleapis.com/v4/spreadsheets/{spreadsheet_id}/values:batchUpdate',
        token=token,
        json_body={
            'valueInputOption': 'USER_ENTERED',
            'data': [{
                'range': a1_start,
                'majorDimension': 'ROWS',
                'values': rows
            }]
        }
    )


def _bulk_manifest_google_should_recreate_sheet(message):
    reason = str(message or '').strip().lower()
    if not reason:
        return False
    recreate_signals = (
        'requested entity was not found',
        'not found',
        'permission denied',
        'the caller does not have permission',
        'insufficient permissions',
        'forbidden'
    )
    return any(signal in reason for signal in recreate_signals)


def _bulk_manifest_google_share_sheet(spreadsheet_id, token):
    try:
        _bulk_manifest_google_request(
            'POST',
            f'https://www.googleapis.com/drive/v3/files/{spreadsheet_id}/permissions',
            token=token,
            params={'sendNotificationEmail': 'false'},
            json_body={'type': 'anyone', 'role': 'reader'}
        )
    except Exception:
        pass

    share_email = _bulk_manifest_google_share_email()
    if share_email:
        try:
            _bulk_manifest_google_request(
                'POST',
                f'https://www.googleapis.com/drive/v3/files/{spreadsheet_id}/permissions',
                token=token,
                params={'sendNotificationEmail': 'false'},
                json_body={'type': 'user', 'role': 'writer', 'emailAddress': share_email}
            )
        except Exception:
            pass
    return share_email


def _bulk_manifest_google_export_sheet(
    existing_sheet_id,
    sheet_title,
    rows,
    token,
    *,
    worksheet_title='Manifest',
    update_spreadsheet_title=True,
    allow_rename_singleton=True,
    image_row_indexes=None,
    emphasis_row_indexes=None,
    header_row_index=0,
    frozen_row_count=1
):
    desired_tab_title = str(worksheet_title or 'Manifest').strip() or 'Manifest'
    existing_sheet_id = str(existing_sheet_id or '').strip()
    sheet_title = str(sheet_title or 'Bulk Manifest').strip() or 'Bulk Manifest'

    sheet_meta = None
    if existing_sheet_id:
        try:
            sheet_meta = _bulk_manifest_google_ensure_manifest_sheet(
                existing_sheet_id,
                token,
                desired_title=desired_tab_title,
                allow_rename_singleton=allow_rename_singleton
            )
        except Exception as exc:
            if _bulk_manifest_google_should_recreate_sheet(exc):
                ss_config.logger.warning('bulk_manifest_google_sheet: recreating spreadsheet %s after reuse failure: %s', existing_sheet_id, exc)
                sheet_meta = None
            else:
                raise

    if sheet_meta is None:
        created = _bulk_manifest_google_request(
            'POST',
            'https://sheets.googleapis.com/v4/spreadsheets',
            token=token,
            json_body={
                'properties': {'title': sheet_title},
                'sheets': [{'properties': {'title': desired_tab_title}}]
            }
        )
        spreadsheet_id = str(created.get('spreadsheetId') or '').strip()
        if not spreadsheet_id:
            raise RuntimeError('Google Sheets create response did not include spreadsheet ID')
        sheet_meta = _bulk_manifest_google_ensure_manifest_sheet(
            spreadsheet_id,
            token,
            desired_title=desired_tab_title,
            allow_rename_singleton=allow_rename_singleton
        )

    _bulk_manifest_google_write_sheet_rows(
        sheet_meta['spreadsheet_id'],
        sheet_meta['sheet_title'],
        rows,
        token
    )
    _bulk_manifest_google_style_sheet(
        sheet_meta['spreadsheet_id'],
        sheet_meta['sheet_id'],
        sheet_meta['sheet_title'],
        sheet_title,
        token,
        update_spreadsheet_title=update_spreadsheet_title,
        row_count=len(rows or []),
        image_row_indexes=image_row_indexes,
        emphasis_row_indexes=emphasis_row_indexes,
        header_row_index=header_row_index,
        frozen_row_count=frozen_row_count
    )
    return sheet_meta


def _bulk_manifest_google_job_get(manifest_id):
    manifest_key = max(0, ss_normalization._coerce_int(manifest_id, 0))
    with _bulk_manifest_google_jobs_lock:
        job = _bulk_manifest_google_jobs.get(manifest_key)
        return dict(job) if isinstance(job, dict) else None


def _bulk_manifest_google_job_set(manifest_id, **updates):
    manifest_key = max(0, ss_normalization._coerce_int(manifest_id, 0))
    with _bulk_manifest_google_jobs_lock:
        current = dict(_bulk_manifest_google_jobs.get(manifest_key) or {})
        current.update(updates)
        current['manifest_id'] = manifest_key
        _bulk_manifest_google_jobs[manifest_key] = current
        return dict(current)


def _bulk_manifest_google_job_clear(manifest_id):
    manifest_key = max(0, ss_normalization._coerce_int(manifest_id, 0))
    with _bulk_manifest_google_jobs_lock:
        _bulk_manifest_google_jobs.pop(manifest_key, None)


def _bulk_manifest_google_delete_sheet_resource(manifest_id, spreadsheet_id, token):
    spreadsheet_id = str(spreadsheet_id or '').strip()
    if not spreadsheet_id:
        return
    shared_workbook_id = _bulk_manifest_google_shared_spreadsheet_id()
    using_shared_workbook = bool(shared_workbook_id and spreadsheet_id == shared_workbook_id)
    if using_shared_workbook:
        worksheet_title = _bulk_manifest_google_tab_title(manifest_id, shared_mode=True)
        meta = _bulk_manifest_google_get_spreadsheet(spreadsheet_id, token)
        sheets = meta.get('sheets') if isinstance(meta, dict) else []
        target_sheet_id = -1
        sheet_count = 0
        if isinstance(sheets, list):
            sheet_count = len(sheets)
            for sheet in sheets:
                props = sheet.get('properties') if isinstance(sheet, dict) else {}
                if str((props or {}).get('title') or '').strip() == worksheet_title:
                    target_sheet_id = ss_normalization._coerce_int((props or {}).get('sheetId'), -1)
                    break
        if target_sheet_id < 0:
            return
        if sheet_count <= 1:
            _bulk_manifest_google_request(
                'POST',
                f'https://sheets.googleapis.com/v4/spreadsheets/{spreadsheet_id}/values:batchClear',
                token=token,
                json_body={
                    'ranges': [_bulk_manifest_google_sheet_range(worksheet_title, 'A:Z')]
                }
            )
            return
        _bulk_manifest_google_request(
            'POST',
            f'https://sheets.googleapis.com/v4/spreadsheets/{spreadsheet_id}:batchUpdate',
            token=token,
            json_body={
                'requests': [{
                    'deleteSheet': {'sheetId': target_sheet_id}
                }]
            }
        )
        return
    _bulk_manifest_google_request(
        'DELETE',
        f'https://www.googleapis.com/drive/v3/files/{spreadsheet_id}',
        token=token
    )


def _bulk_manifest_google_delete_manifest(manifest_id):
    manifest_key = max(0, ss_normalization._coerce_int(manifest_id, 0))
    with ss_database.db_connection('marketplace.db') as conn:
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        ss_bulk_manifest._ensure_bulk_manifest_tables(cur)
        cur.execute('''
            SELECT id, manifest_title, google_sheet_id, google_sheet_url
            FROM bulk_manifests
            WHERE id = ?
        ''', (manifest_key,))
        manifest_row = cur.fetchone()
        if not manifest_row:
            raise LookupError('Manifest not found')
        manifest = dict(manifest_row)

    spreadsheet_id = str(manifest.get('google_sheet_id') or '').strip()
    sheet_url = str(manifest.get('google_sheet_url') or '').strip()
    if not spreadsheet_id and not sheet_url:
        _bulk_manifest_google_job_clear(manifest_key)
        return {
            'success': True,
            'manifest_id': manifest_key,
            'deleted': False,
            'status': 'idle'
        }

    token = _bulk_manifest_google_access_token()
    try:
        _bulk_manifest_google_delete_sheet_resource(manifest_key, spreadsheet_id, token)
    except Exception as exc:
        detail = str(exc).strip().lower()
        ignorable = (
            'requested entity was not found',
            'not found',
            'does not exist',
            'insufficient permissions'
        )
        if not any(signal in detail for signal in ignorable):
            raise

    now_iso = datetime.datetime.now().isoformat()
    with ss_database.db_connection('marketplace.db') as conn:
        cur = conn.cursor()
        ss_bulk_manifest._ensure_bulk_manifest_tables(cur)
        cur.execute('''
            UPDATE bulk_manifests
            SET google_sheet_id = '',
                google_sheet_url = '',
                google_sheet_exported_at = '',
                google_sheet_shared_with = '',
                updated_at = ?
            WHERE id = ?
        ''', (now_iso, manifest_key))

    _bulk_manifest_google_job_clear(manifest_key)
    return {
        'success': True,
        'manifest_id': manifest_key,
        'deleted': True,
        'status': 'idle'
    }


def _bulk_manifest_google_state(manifest_id, row=None):
    manifest_key = max(0, ss_normalization._coerce_int(manifest_id, 0))
    source = row if isinstance(row, dict) else {}
    job = _bulk_manifest_google_job_get(manifest_key) or {}

    db_sheet_id = str(source.get('google_sheet_id') or '').strip()
    db_sheet_url = str(source.get('google_sheet_url') or '').strip()
    db_exported_at = str(source.get('google_sheet_exported_at') or '').strip()
    db_shared_with = str(source.get('google_sheet_shared_with') or '').strip()

    job_status = str(job.get('status') or '').strip().lower()
    sheet_id = str(job.get('sheet_id') or db_sheet_id).strip()
    sheet_url = str(job.get('sheet_url') or db_sheet_url).strip()
    exported_at = str(job.get('exported_at') or db_exported_at).strip()
    shared_with = str(job.get('shared_with') or db_shared_with).strip()
    available = bool(sheet_url)

    if job_status in ('queued', 'processing'):
        status = job_status
    elif job_status == 'error':
        status = 'error'
    elif available:
        status = 'ready'
    else:
        status = 'idle'

    if status in ('queued', 'processing'):
        button_label = 'Updating Sheet...' if available else 'Creating Sheet...'
    elif available:
        button_label = 'Google Sheet'
    else:
        button_label = 'Create Sheet'

    return {
        'google_sheet_status': status,
        'google_sheet_available': available,
        'google_sheet_button_label': button_label,
        'google_sheet_button_tone': ('primary' if available else 'default'),
        'google_sheet_id': sheet_id,
        'google_sheet_url': sheet_url,
        'google_sheet_exported_at': exported_at,
        'google_sheet_shared_with': shared_with,
        'google_sheet_error': str(job.get('error') or '').strip() if status == 'error' else ''
    }


def _bulk_manifest_queue_google_export(manifest_id, origin_base='', manifest_title='', force_refresh=False):
    manifest_key = max(0, ss_normalization._coerce_int(manifest_id, 0))
    origin_base = str(origin_base or '').strip().rstrip('/')
    manifest_title = str(manifest_title or '').strip()

    active_job = _bulk_manifest_google_job_get(manifest_key) or {}
    active_status = str(active_job.get('status') or '').strip().lower()
    if active_status in ('queued', 'processing'):
        if force_refresh:
            _bulk_manifest_google_job_set(
                manifest_key,
                refresh_pending=True,
                origin_base=origin_base or str(active_job.get('origin_base') or '').strip(),
                manifest_title=manifest_title or str(active_job.get('manifest_title') or '').strip()
            )
        return _bulk_manifest_google_status_payload(manifest_key)

    _bulk_manifest_google_job_set(
        manifest_key,
        status='queued',
        started_at=datetime.datetime.now().isoformat(),
        finished_at='',
        error='',
        sheet_id=str(active_job.get('sheet_id') or '').strip(),
        sheet_url=str(active_job.get('sheet_url') or '').strip(),
        shared_with=str(active_job.get('shared_with') or '').strip(),
        exported_at=str(active_job.get('exported_at') or '').strip(),
        origin_base=origin_base,
        manifest_title=manifest_title,
        refresh_pending=False
    )
    worker = threading.Thread(
        target=_bulk_manifest_google_export_worker,
        args=(manifest_key, origin_base),
        daemon=True
    )
    worker.start()
    return _bulk_manifest_google_status_payload(manifest_key)


def _bulk_manifest_google_manifest_meta(manifest_id):
    with ss_database.db_connection('marketplace.db') as conn:
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        ss_bulk_manifest._ensure_bulk_manifest_tables(cur)
        cur.execute('''
            SELECT
                id,
                manifest_title,
                google_sheet_id,
                google_sheet_url,
                google_sheet_exported_at,
                google_sheet_shared_with
            FROM bulk_manifests
            WHERE id = ?
        ''', (manifest_id,))
        row = cur.fetchone()
        if not row:
            raise LookupError('Manifest not found')
        row = dict(row)
        return {
            'manifest_id': max(0, ss_normalization._coerce_int(row.get('id'), 0)),
            'manifest_title': str(row.get('manifest_title') or '').strip(),
            'sheet_id': str(row.get('google_sheet_id') or '').strip(),
            'sheet_url': str(row.get('google_sheet_url') or '').strip(),
            'shared_with': str(row.get('google_sheet_shared_with') or '').strip(),
            'exported_at': str(row.get('google_sheet_exported_at') or '').strip()
        }


def _bulk_manifest_google_export_manifest(manifest_id, origin_base=''):
    with ss_database.db_connection('marketplace.db') as conn:
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        try:
            manifest, items = ss_bulk_manifest._bulk_manifest_load_detail(cur, manifest_id)
        except LookupError:
            raise LookupError('Manifest not found')

    if not items:
        raise RuntimeError('Manifest has no line items to export')

    token = _bulk_manifest_google_access_token()
    sheet_data = _bulk_manifest_build_google_sheet_rows(manifest, items, origin_base=origin_base)
    rows = list((sheet_data or {}).get('rows') or [])
    image_row_indexes = list((sheet_data or {}).get('image_row_indexes') or [])
    emphasis_row_indexes = list((sheet_data or {}).get('emphasis_row_indexes') or [])
    header_row_index = ss_normalization._coerce_int((sheet_data or {}).get('header_row_index'), 0)
    frozen_row_count = ss_normalization._coerce_int((sheet_data or {}).get('frozen_row_count'), 1)
    sheet_title = str((manifest or {}).get('manifest_title') or f'Bulk Manifest {manifest_id}').strip() or f'Bulk Manifest {manifest_id}'
    sheet_title = f'{sheet_title} Digital Export'
    shared_workbook_id = _bulk_manifest_google_shared_spreadsheet_id()
    configured_sheet_id = str((manifest or {}).get('google_sheet_id') or '').strip()
    target_spreadsheet_id = configured_sheet_id or shared_workbook_id
    using_shared_workbook = bool(shared_workbook_id and target_spreadsheet_id == shared_workbook_id)
    worksheet_title = _bulk_manifest_google_tab_title(manifest_id, shared_mode=using_shared_workbook)

    exported = _bulk_manifest_google_export_sheet(
        target_spreadsheet_id,
        sheet_title,
        rows,
        token,
        worksheet_title=worksheet_title,
        update_spreadsheet_title=(not using_shared_workbook),
        allow_rename_singleton=(not using_shared_workbook),
        image_row_indexes=image_row_indexes,
        emphasis_row_indexes=emphasis_row_indexes,
        header_row_index=header_row_index,
        frozen_row_count=frozen_row_count
    )
    spreadsheet_id = str(exported.get('spreadsheet_id') or '').strip()
    worksheet_id = max(0, ss_normalization._coerce_int(exported.get('sheet_id'), 0))
    sheet_url = str(exported.get('spreadsheet_url') or '').strip() or f'https://docs.google.com/spreadsheets/d/{spreadsheet_id}/edit'
    if using_shared_workbook and spreadsheet_id and worksheet_id > 0:
        sheet_url = f'https://docs.google.com/spreadsheets/d/{spreadsheet_id}/edit#gid={worksheet_id}'
    if not spreadsheet_id or not sheet_url:
        raise RuntimeError('Google Sheets export did not return a spreadsheet link')
    shared_with = ''
    if not using_shared_workbook:
        shared_with = _bulk_manifest_google_share_sheet(spreadsheet_id, token)

    now_iso = datetime.datetime.now().isoformat()
    with ss_database.db_connection('marketplace.db') as conn:
        cur = conn.cursor()
        ss_bulk_manifest._ensure_bulk_manifest_tables(cur)
        cur.execute('''
            UPDATE bulk_manifests
            SET google_sheet_id = ?,
                google_sheet_url = ?,
                google_sheet_exported_at = ?,
                google_sheet_shared_with = ?,
                updated_at = ?
            WHERE id = ?
        ''', (
            spreadsheet_id,
            sheet_url,
            now_iso,
            shared_with,
            now_iso,
            manifest_id
        ))

    return {
        'success': True,
        'status': 'ready',
        'manifest_id': manifest_id,
        'sheet_id': spreadsheet_id,
        'sheet_url': sheet_url,
        'shared_with': shared_with,
        'exported_at': now_iso
    }


def _bulk_manifest_google_export_worker(manifest_id, origin_base=''):
    started_at = datetime.datetime.now().isoformat()
    ss_config.logger.info('bulk_manifest_google_export_worker started for manifest %s', manifest_id)
    _bulk_manifest_google_job_set(
        manifest_id,
        status='processing',
        started_at=started_at,
        finished_at='',
        error=''
    )
    with ss_runtime.app.app_context():
        try:
            result = _bulk_manifest_google_export_manifest(manifest_id, origin_base=origin_base)
            result = dict(result or {})
            result.pop('manifest_id', None)
            job_state = _bulk_manifest_google_job_set(
                manifest_id,
                finished_at=datetime.datetime.now().isoformat(),
                error='',
                **result
            )
            if job_state.get('refresh_pending'):
                next_origin_base = str(job_state.get('origin_base') or origin_base).strip().rstrip('/')
                next_title = str(job_state.get('manifest_title') or '').strip()
                _bulk_manifest_google_job_set(
                    manifest_id,
                    status='queued',
                    finished_at='',
                    error='',
                    refresh_pending=False,
                    origin_base=next_origin_base,
                    manifest_title=next_title
                )
                threading.Thread(
                    target=_bulk_manifest_google_export_worker,
                    args=(manifest_id, next_origin_base),
                    daemon=True
                ).start()
                ss_config.logger.info('bulk_manifest_google_export_worker requeued refresh for manifest %s', manifest_id)
                return
            ss_config.logger.info('bulk_manifest_google_export_worker ready for manifest %s', manifest_id)
        except LookupError:
            _bulk_manifest_google_job_set(
                manifest_id,
                status='error',
                error='Manifest not found',
                finished_at=datetime.datetime.now().isoformat()
            )
            ss_config.logger.warning('bulk_manifest_google_export_worker manifest not found: %s', manifest_id)
        except Exception as exc:
            ss_config.logger.warning('bulk_manifest_google_export_worker failed for manifest %s: %s', manifest_id, exc, exc_info=True)
            _bulk_manifest_google_job_set(
                manifest_id,
                status='error',
                error=str(exc).strip() or 'Google Sheet export failed',
                finished_at=datetime.datetime.now().isoformat()
            )


def _bulk_manifest_google_status_payload(manifest_id):
    job = _bulk_manifest_google_job_get(manifest_id) or {}
    status = str(job.get('status') or '').strip().lower()
    if status:
        state = _bulk_manifest_google_state(manifest_id)
        payload = {
            'success': state['google_sheet_status'] != 'error',
            'status': state['google_sheet_status'],
            'manifest_id': manifest_id,
            'sheet_id': state['google_sheet_id'],
            'sheet_url': state['google_sheet_url'],
            'shared_with': state['google_sheet_shared_with'],
            'exported_at': state['google_sheet_exported_at'],
            'manifest_title': str(job.get('manifest_title') or '').strip()
        }
        payload['button_label'] = state['google_sheet_button_label']
        payload['button_tone'] = state['google_sheet_button_tone']
        payload['sheet_available'] = state['google_sheet_available']
        if state['google_sheet_status'] == 'error':
            payload['error'] = state['google_sheet_error'] or 'Google Sheet export failed'
        return payload

    meta = _bulk_manifest_google_manifest_meta(manifest_id)
    state = _bulk_manifest_google_state(manifest_id, row={
        'google_sheet_id': meta.get('sheet_id'),
        'google_sheet_url': meta.get('sheet_url'),
        'google_sheet_exported_at': meta.get('exported_at'),
        'google_sheet_shared_with': meta.get('shared_with')
    })
    payload = {
        'success': state['google_sheet_status'] != 'error',
        'status': state['google_sheet_status'],
        'manifest_id': manifest_id,
        'sheet_id': state['google_sheet_id'],
        'sheet_url': state['google_sheet_url'],
        'shared_with': state['google_sheet_shared_with'],
        'exported_at': state['google_sheet_exported_at'],
        'manifest_title': str(meta.get('manifest_title') or '').strip()
    }
    payload['button_label'] = state['google_sheet_button_label']
    payload['button_tone'] = state['google_sheet_button_tone']
    payload['sheet_available'] = state['google_sheet_available']
    if state['google_sheet_status'] == 'error':
        payload['error'] = state['google_sheet_error'] or 'Google Sheet export failed'
    return payload


def _bulk_manifest_build_google_sheet_rows(manifest, items, origin_base=''):
    manifest_id = max(0, ss_normalization._coerce_int((manifest or {}).get('id'), 0))
    title = str((manifest or {}).get('manifest_title') or f'Bulk Manifest {manifest_id}').strip() or f'Bulk Manifest {manifest_id}'

    rows = []
    image_row_indexes = []
    emphasis_row_indexes = []

    total_units = 0
    total_retail = Decimal('0.00')
    item_rows = list(items or [])
    prepared_rows = []
    for idx, row in enumerate(item_rows):
        qty = max(0, ss_normalization._coerce_int(row.get('quantity'), 0))
        unit_price = Decimal(str(ss_bulk_manifest._bulk_manifest_money(row.get('unit_price'), default=0.0)))
        line_total = (Decimal(qty) * unit_price).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
        total_units += qty
        total_retail += line_total

        raw_image = str(row.get('image_url') or '').strip()
        if not raw_image:
            try:
                lookup = ss_bulk_manifest._bulk_manifest_lookup_rawbol(row.get('barcode'))
            except Exception:
                lookup = None
            raw_image = str((lookup or {}).get('image_url') or '').strip()
        raw_image = ss_bulk_manifest._bulk_manifest_normalize_image_url(raw_image, origin_base=origin_base)
        thumb_image = ss_bulk_manifest._bulk_manifest_macy_thumb_url(raw_image, origin_base=origin_base)
        full_image = ss_bulk_manifest._bulk_manifest_macy_full_image_url(raw_image, origin_base=origin_base)
        thumb_formula = ss_bulk_manifest._bulk_manifest_google_image_formula(
            thumb_image,
            origin_base=origin_base,
            width=100,
            height=122
        )
        full_link_formula = ss_bulk_manifest._bulk_manifest_google_link_formula(full_image, 'Open Full Size', origin_base=origin_base)
        prepared_rows.append({
            'row_number': idx + 1,
            'barcode': str(row.get('barcode') or '').strip(),
            'title': str(row.get('title') or '').strip() or 'item',
            'qty': qty,
            'display_price': f'{(line_total if qty > 1 else unit_price):.2f}',
            'emphasis': qty > 1,
            'thumb_formula': thumb_formula,
            'full_link_formula': full_link_formula
        })

    rows.append([
        'Total Items',
        total_units,
        '',
        '',
        'Total Retail Price',
        f'{total_retail.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP):.2f}',
        '',
        ''
    ])
    rows.append(['', '', '', '', '', '', '', ''])
    header_row_index = len(rows)
    rows.append([
        'Manifest Title',
        'Line #',
        'Barcode',
        'Item Name',
        'Qty',
        'Original Retail',
        'Macy Thumb (Sheets)',
        'Full Size Link (Sheets)'
    ])

    for prepared in prepared_rows:
        row_index = len(rows)
        if prepared['thumb_formula']:
            image_row_indexes.append(row_index)
        if prepared['emphasis']:
            emphasis_row_indexes.append(row_index)
        rows.append([
            title,
            prepared['row_number'],
            prepared['barcode'],
            prepared['title'],
            prepared['qty'],
            prepared['display_price'],
            prepared['thumb_formula'],
            prepared['full_link_formula']
        ])

    rows.append([
        '',
        '',
        'Total Items',
        total_units,
        'Total Retail Price',
        f'{total_retail.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP):.2f}',
        '',
        ''
    ])
    return {
        'rows': rows,
        'image_row_indexes': image_row_indexes,
        'emphasis_row_indexes': emphasis_row_indexes,
        'header_row_index': header_row_index,
        'frozen_row_count': header_row_index + 1
    }
