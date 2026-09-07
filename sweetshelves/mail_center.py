"""Mail center for Sweet Shelves."""

import datetime
import email
import hashlib
import html
import imaplib
import json
import os
import re as _re
import requests
import threading
import time
from contextlib import nullcontext
from email import policy
from email.header import decode_header
from email.utils import parseaddr, parsedate_to_datetime
from flask import has_app_context, jsonify, request
from . import (
    amazon_catalog as ss_amazon_catalog, config as ss_config, database as ss_database, errors as
    ss_errors, integrations as ss_integrations, mail_schema as ss_mail_schema, runtime as ss_runtime,
    sync as ss_sync,
)


def _mail_center_env_int(name, default, min_value, max_value):
    try:
        raw = os.getenv(name, str(default))
        value = int(str(raw).strip())
    except Exception:
        value = int(default)
    if value < min_value:
        return min_value
    if value > max_value:
        return max_value
    return value


def _mail_center_hourly_refresh_once():
    """
    Lightweight periodic refresh for mail-center data.
    Runs small-window pulls so /alerts can show fresher unread counts
    even if /mail-center has not been opened recently.
    """
    ctx = nullcontext() if has_app_context() else ss_runtime.app.app_context()
    with ctx:
        ss_mail_schema._ensure_storemail_tables()

        ebay_hours = _mail_center_env_int('MAIL_CENTER_REFRESH_EBAY_HOURS', 24, 1, 168)
        ebay_pages = _mail_center_env_int('MAIL_CENTER_REFRESH_EBAY_PAGES', 1, 1, 5)
        amazon_days = _mail_center_env_int('MAIL_CENTER_REFRESH_AMAZON_DAYS', 2, 1, 14)
        amazon_orders = _mail_center_env_int('MAIL_CENTER_REFRESH_AMAZON_ORDERS', 25, 1, 120)

        summary = {'success': True, 'ebay': None, 'amazon': None, 'warnings': []}

        ebay_token = (os.getenv('EBAY_OLDAUTH_TOKEN') or '').strip()
        if ebay_token:
            try:
                summary['ebay'] = _mail_sync_ebay(hours_back=ebay_hours, max_pages=ebay_pages)
            except Exception as e:
                summary['ebay'] = {'error': ss_errors._safe_error(e, 'mail_center:hourly_ebay')}
                summary['warnings'].append('eBay refresh failed')
        else:
            summary['ebay'] = {'skipped': 'missing_ebay_token'}

        has_amazon_creds = (ss_config.BASE_DIR / 'amazon_credentials.json').exists()
        if ss_integrations.AMAZON_AVAILABLE and has_amazon_creds:
            try:
                summary['amazon'] = _mail_sync_amazon(days_back=amazon_days, max_orders=amazon_orders)
                if isinstance(summary.get('amazon'), dict):
                    if summary['amazon'].get('error'):
                        summary['warnings'].append('Amazon SP-API refresh had issues')
                    forwarded = summary['amazon'].get('forwarded_email') or {}
                    if isinstance(forwarded, dict) and forwarded.get('error'):
                        summary['warnings'].append('Amazon forwarded-email refresh had issues')
            except Exception as e:
                summary['amazon'] = {'error': ss_errors._safe_error(e, 'mail_center:hourly_amazon')}
                summary['warnings'].append('Amazon refresh failed')
        else:
            summary['amazon'] = {'skipped': 'amazon_unavailable_or_missing_credentials'}

        try:
            ss_sync.update_sync_timestamp('mail_center_refresh')
        except Exception:
            pass

        return summary


def _mail_parse_payload(value):
    try:
        if not value:
            return {}
        if isinstance(value, dict):
            return value
        return json.loads(value)
    except Exception:
        return {}


def _mail_message_to_dict(row):
    payload = _mail_parse_payload(row['external_payload']) if 'external_payload' in row.keys() else {}
    return {
        'id': row['id'],
        'store': row['store'],
        'direction': row['direction'],
        'status': row['status'],
        'sender_name': row['sender_name'],
        'recipient_name': row['recipient_name'],
        'subject': row['subject'] or '',
        'body': row['body'] or '',
        'reply_to_id': row['reply_to_id'],
        'created_at': row['created_at'],
        'sent_at': row['sent_at'],
        'read_at': row['read_at'],
        'external_source': row['external_source'] if 'external_source' in row.keys() else None,
        'external_id': row['external_id'] if 'external_id' in row.keys() else None,
        'external_payload': payload,
    }


def _mail_store_upsert_external(*, store, direction, status, sender_name, recipient_name, subject, body,
                                external_source, external_id, external_payload=None, created_at=None):
    """Insert-or-update a message row keyed by external_source+external_id."""
    with ss_database.db_connection('storemail.db') as conn:
        cur = conn.cursor()
        existing = cur.execute('''
            SELECT id, direction, status FROM store_messages
            WHERE external_source = ? AND external_id = ?
            LIMIT 1
        ''', (external_source, external_id)).fetchone()
        payload_json = json.dumps(external_payload or {}, ensure_ascii=True)
        if existing:
            effective_status = status
            if (
                str(existing['direction'] or '').lower() == 'inbound'
                and str(existing['status'] or '').lower() == 'read'
                and str(status or '').lower() == 'unread'
            ):
                # Keep local read state when upstream cannot reliably report read/unread.
                effective_status = 'read'

            cur.execute('''
                UPDATE store_messages
                SET store = ?,
                    direction = ?,
                    status = ?,
                    sender_name = ?,
                    recipient_name = ?,
                    subject = ?,
                    body = ?,
                    external_payload = ?,
                    read_at = CASE
                        WHEN ? = 'read' AND direction = 'inbound' THEN COALESCE(read_at, CURRENT_TIMESTAMP)
                        ELSE read_at
                    END,
                    last_synced_at = CURRENT_TIMESTAMP
                WHERE id = ?
            ''', (
                store, direction, effective_status, sender_name, recipient_name, subject, body,
                payload_json, str(effective_status or '').lower(), existing['id']
            ))
            return existing['id'], False

        if created_at:
            cur.execute('''
                INSERT INTO store_messages (
                    store, direction, status, sender_name, recipient_name, subject, body,
                    external_source, external_id, external_payload, created_at, last_synced_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ''', (
                store, direction, status, sender_name, recipient_name, subject, body,
                external_source, external_id, payload_json, created_at
            ))
        else:
            cur.execute('''
                INSERT INTO store_messages (
                    store, direction, status, sender_name, recipient_name, subject, body,
                    external_source, external_id, external_payload, last_synced_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ''', (
                store, direction, status, sender_name, recipient_name, subject, body,
                external_source, external_id, payload_json
            ))
        return cur.lastrowid, True


def _mail_ebay_headers(call_name):
    return {
        "X-EBAY-API-SITEID": "0",
        "X-EBAY-API-COMPATIBILITY-LEVEL": "967",
        "X-EBAY-API-CALL-NAME": call_name,
        "X-EBAY-API-DEV-NAME": os.getenv("EBAY_PROD_DEV_ID"),
        "X-EBAY-API-APP-NAME": os.getenv("EBAY_PROD_APP_ID"),
        "X-EBAY-API-CERT-NAME": os.getenv("EBAY_PROD_CERT_ID"),
        "Content-Type": "text/xml",
    }


def _mail_ebay_get_member_messages(hours_back=72, page=1, entries_per_page=50, message_type='All'):
    token = (os.getenv("EBAY_OLDAUTH_TOKEN") or '').strip()
    if not token:
        raise Exception("Missing EBAY_OLDAUTH_TOKEN")

    now_utc = datetime.datetime.now(datetime.UTC)
    start_utc = now_utc - datetime.timedelta(hours=max(1, int(hours_back or 72)))
    start_iso = start_utc.strftime('%Y-%m-%dT%H:%M:%S.000Z')
    end_iso = now_utc.strftime('%Y-%m-%dT%H:%M:%S.000Z')
    xml_payload = f'''<?xml version="1.0" encoding="utf-8"?>
      <GetMemberMessagesRequest xmlns="urn:ebay:apis:eBLBaseComponents">
        <RequesterCredentials>
          <eBayAuthToken>{token}</eBayAuthToken>
        </RequesterCredentials>
        <DetailLevel>ReturnAll</DetailLevel>
        <MailMessageType>{message_type}</MailMessageType>
        <StartCreationTime>{start_iso}</StartCreationTime>
        <EndCreationTime>{end_iso}</EndCreationTime>
        <Pagination>
          <EntriesPerPage>{int(entries_per_page or 50)}</EntriesPerPage>
          <PageNumber>{int(page or 1)}</PageNumber>
        </Pagination>
      </GetMemberMessagesRequest>
    '''
    headers = _mail_ebay_headers('GetMemberMessages')
    resp = requests.post("https://api.ebay.com/ws/api.dll", headers=headers, data=xml_payload, timeout=30)
    if resp.status_code >= 400:
        raise Exception(f"eBay Trading API HTTP {resp.status_code}: {resp.text[:250]}")

    import xml.etree.ElementTree as ET
    ns = {'eb': 'urn:ebay:apis:eBLBaseComponents'}
    root = ET.fromstring(resp.text or '')
    ack = (root.findtext('.//eb:Ack', default='', namespaces=ns) or '').strip()
    if ack.lower() not in ('success', 'warning'):
        err = root.findtext('.//eb:Errors/eb:LongMessage', default='', namespaces=ns) or \
              root.findtext('.//eb:Errors/eb:ShortMessage', default='', namespaces=ns) or \
              'Unknown eBay API error'
        raise Exception(f"GetMemberMessages failed: {err}")

    total_pages = root.findtext('.//eb:PaginationResult/eb:TotalNumberOfPages', default='1', namespaces=ns)
    try:
        total_pages = max(1, int(total_pages or '1'))
    except Exception:
        total_pages = 1

    items = []
    exchanges = root.findall('.//eb:MemberMessageExchange', ns)
    for ex in exchanges:
        msg_id = (
            ex.findtext('eb:MessageID', default='', namespaces=ns) or
            ex.findtext('eb:Question/eb:MessageID', default='', namespaces=ns) or
            ex.findtext('eb:Response/eb:MessageID', default='', namespaces=ns)
        ).strip()
        subject = (
            ex.findtext('eb:Question/eb:Subject', default='', namespaces=ns) or
            ex.findtext('eb:Response/eb:Subject', default='', namespaces=ns) or
            ex.findtext('eb:Subject', default='', namespaces=ns)
        ).strip()
        body = (
            ex.findtext('eb:Question/eb:Body', default='', namespaces=ns) or
            ex.findtext('eb:Body', default='', namespaces=ns)
        ).strip()
        sender_id = (ex.findtext('eb:Question/eb:SenderID', default='', namespaces=ns) or '').strip()
        recipient_id = (ex.findtext('eb:Question/eb:RecipientID', default='', namespaces=ns) or '').strip()
        item_id = (ex.findtext('eb:Item/eb:ItemID', default='', namespaces=ns) or '').strip()
        created_at = (
            ex.findtext('eb:Question/eb:MessageCreationDate', default='', namespaces=ns) or
            ex.findtext('eb:CreationDate', default='', namespaces=ns)
        ).strip()
        message_status = (ex.findtext('eb:MessageStatus', default='', namespaces=ns) or '').strip().lower()

        if not msg_id:
            digest = hashlib.sha1(f"{item_id}|{sender_id}|{created_at}|{subject}|{body}".encode('utf-8', errors='ignore')).hexdigest()[:24]
            msg_id = f"synthetic-{digest}"

        row_status = 'read' if message_status in ('answered', 'read') else 'unread'
        if not body:
            body = '(No body)'

        items.append({
            'external_id': msg_id,
            'subject': subject or 'eBay Buyer Message',
            'body': body,
            'sender_name': sender_id or 'eBay Buyer',
            'recipient_name': recipient_id or 'Sweet Shelves',
            'created_at': created_at or None,
            'status': row_status,
            'payload': {
                'item_id': item_id,
                'sender_id': sender_id,
                'recipient_id': recipient_id,
                'message_status': message_status,
            }
        })
    return items, total_pages


def _mail_sync_ebay(hours_back=72, max_pages=3):
    inserted = 0
    updated = 0
    fetched = 0
    page = 1
    total_pages = 1
    while page <= max_pages and page <= total_pages:
        try:
            batch, total_pages = _mail_ebay_get_member_messages(
                hours_back=hours_back, page=page, entries_per_page=50, message_type='All'
            )
        except Exception as e:
            msg = str(e)
            if 'MessageType is Invalid' in msg:
                batch, total_pages = _mail_ebay_get_member_messages(
                    hours_back=hours_back, page=page, entries_per_page=50, message_type='All'
                )
            else:
                raise
        fetched += len(batch)
        for msg in batch:
            _id, is_new = _mail_store_upsert_external(
                store='eBay',
                direction='inbound',
                status=msg['status'],
                sender_name=msg['sender_name'],
                recipient_name=msg['recipient_name'],
                subject=msg['subject'],
                body=msg['body'],
                external_source='ebay_member_message',
                external_id=msg['external_id'],
                external_payload=msg['payload'],
                created_at=msg.get('created_at'),
            )
            if is_new:
                inserted += 1
            else:
                updated += 1
        page += 1
    return {'fetched': fetched, 'inserted': inserted, 'updated': updated, 'pages': page - 1}


_MAIL_AMAZON_ORDER_ID_RE = _re.compile(r'\b(?:\d{3}-\d{7}-\d{7}|[A-Z0-9]{3}-[A-Z0-9]{7}-[A-Z0-9]{7})\b')


_MAIL_AMAZON_HINTS = (
    'amazon',
    'amazon.com',
    'sellercentral.amazon',
    '@amazon.',
    'seller central',
    'amazon marketplace',
)


_MAIL_AMAZON_BUYER_HINTS = (
    'buyer-seller messaging',
    'buyer seller messaging',
    'new message from a buyer',
    'you have received a new message from a buyer',
    'you received a new message from a buyer',
    'customer sent you a message',
    'message from buyer',
    'respond to buyer',
    'reply to buyer',
    'sent you a message',
)


def _mail_decode_header_value(value):
    if value is None:
        return ''
    raw = str(value)
    try:
        decoded = decode_header(raw)
    except Exception:
        return raw
    out = []
    for part, enc in decoded:
        if isinstance(part, bytes):
            charset = enc or 'utf-8'
            try:
                out.append(part.decode(charset, errors='replace'))
            except Exception:
                out.append(part.decode('utf-8', errors='replace'))
        else:
            out.append(str(part))
    return ''.join(out).strip()


def _mail_date_to_iso(value):
    raw = (value or '').strip()
    if not raw:
        return None
    try:
        dt = parsedate_to_datetime(raw)
        if dt is None:
            return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=datetime.UTC)
        else:
            dt = dt.astimezone(datetime.UTC)
        return dt.isoformat().replace('+00:00', 'Z')
    except Exception:
        return None


def _mail_clean_message_id(value):
    v = (value or '').strip()
    if not v:
        return ''
    return v.strip('<>').strip()


def _mail_strip_subject_prefixes(value):
    s = (value or '').strip()
    for _ in range(6):
        next_s = _re.sub(r'^(?:re|fw|fwd)\s*:\s*', '', s, flags=_re.IGNORECASE).strip()
        if next_s == s:
            break
        s = next_s
    return s


def _mail_html_to_text(html_body):
    raw = html_body or ''
    raw = _re.sub(r'(?is)<(script|style).*?>.*?</\1>', ' ', raw)
    raw = _re.sub(r'(?i)<br\s*/?>', '\n', raw)
    raw = _re.sub(r'(?i)</p\s*>', '\n', raw)
    raw = _re.sub(r'<[^>]+>', ' ', raw)
    raw = html.unescape(raw)
    raw = raw.replace('\r', '')
    raw = _re.sub(r'[ \t]{2,}', ' ', raw)
    raw = _re.sub(r'\n{3,}', '\n\n', raw)
    return raw.strip()


def _mail_extract_text_body(msg_obj):
    plain_parts = []
    html_parts = []
    parts = msg_obj.walk() if msg_obj.is_multipart() else [msg_obj]
    for part in parts:
        ctype = (part.get_content_type() or '').lower()
        disp = (part.get_content_disposition() or '').lower()
        if disp == 'attachment' or ctype not in ('text/plain', 'text/html'):
            continue
        text = ''
        try:
            text = part.get_content()
        except Exception:
            payload = part.get_payload(decode=True) or b''
            charset = part.get_content_charset() or 'utf-8'
            try:
                text = payload.decode(charset, errors='replace')
            except Exception:
                text = payload.decode('utf-8', errors='replace')
        if not text:
            continue
        if ctype == 'text/plain':
            plain_parts.append(str(text))
        else:
            html_parts.append(str(text))
    body = '\n'.join(plain_parts).strip() if plain_parts else _mail_html_to_text('\n'.join(html_parts))
    body = body.replace('\r', '')
    body = _re.sub(r'\n{3,}', '\n\n', body)
    return body.strip()


def _mail_parse_forwarded_amazon_email(raw_bytes, *, uid='', flags=''):
    try:
        msg_obj = email.message_from_bytes(raw_bytes, policy=policy.default)
    except Exception:
        return None

    subject_raw = _mail_decode_header_value(msg_obj.get('Subject'))
    subject = _mail_strip_subject_prefixes(subject_raw) or subject_raw
    from_raw = _mail_decode_header_value(msg_obj.get('From'))
    from_name, from_email = parseaddr(from_raw)
    body = _mail_extract_text_body(msg_obj)

    search_blob = '\n'.join([subject_raw or '', from_raw or '', body or ''])
    low = search_blob.lower()
    has_amazon_hint = any(h in low for h in _MAIL_AMAZON_HINTS)
    has_buyer_hint = any(h in low for h in _MAIL_AMAZON_BUYER_HINTS)
    order_match = _MAIL_AMAZON_ORDER_ID_RE.search(search_blob or '')
    order_id = order_match.group(0) if order_match else ''

    if not has_amazon_hint:
        return None
    if not has_buyer_hint and not (order_id and ('message' in low or 'buyer' in low)):
        return None

    status = 'read' if '\\Seen' in (flags or '') else 'unread'
    msg_id = _mail_clean_message_id(_mail_decode_header_value(msg_obj.get('Message-ID')))
    created_at = _mail_date_to_iso(_mail_decode_header_value(msg_obj.get('Date')))
    if not subject:
        subject = f"Amazon buyer message for order {order_id}" if order_id else 'Amazon buyer message'

    sender_name = from_name or from_email or 'Amazon Buyer Message'
    if 'amazon.' not in (from_email or '').lower():
        sender_name = 'Amazon Buyer Message (forwarded)'

    body_text = body or '(No body)'
    if len(body_text) > 9000:
        body_text = body_text[:9000].rstrip() + '\n...[truncated]'

    ext_id = msg_id or hashlib.sha1(
        f"{uid}|{from_raw}|{subject}|{created_at}|{body_text[:400]}".encode('utf-8', errors='ignore')
    ).hexdigest()[:32]
    payload = {
        'imap_uid': str(uid or ''),
        'order_id': order_id or '',
        'from': from_raw or '',
        'from_email': from_email or '',
        'message_id_header': msg_id or '',
        'flags': flags or '',
        'source': 'forwarded_email',
    }
    return {
        'external_id': ext_id,
        'subject': subject,
        'body': body_text,
        'sender_name': sender_name,
        'recipient_name': 'Sweet Shelves',
        'created_at': created_at,
        'status': status,
        'payload': payload,
    }


def _mail_sync_amazon_forwarded_emails(days_back=7, max_messages=120):
    imap_server = (os.getenv('IMAP_SERVER') or 'imap.gmail.com').strip() or 'imap.gmail.com'
    imap_port_raw = (os.getenv('IMAP_PORT') or '993').strip() or '993'
    imap_user = (os.getenv('IMAP_USER') or os.getenv('SMTP_USER') or '').strip()
    imap_password = (os.getenv('IMAP_PASSWORD') or os.getenv('SMTP_PASSWORD') or '').strip()
    imap_mailbox = (os.getenv('IMAP_MAILBOX') or 'INBOX').strip() or 'INBOX'

    if not imap_user or not imap_password:
        raise Exception("IMAP credentials missing (set IMAP_USER/IMAP_PASSWORD or SMTP_USER/SMTP_PASSWORD)")

    try:
        imap_port = int(imap_port_raw)
    except Exception:
        imap_port = 993
    days_back = max(1, min(30, int(days_back or 7)))
    max_messages = max(1, min(500, int(max_messages or 120)))

    since_dt = datetime.datetime.now(datetime.UTC) - datetime.timedelta(days=days_back)
    since_arg = since_dt.strftime('%d-%b-%Y')

    scanned = 0
    matched = 0
    inserted = 0
    updated = 0
    first_error = None
    client = None
    try:
        client = imaplib.IMAP4_SSL(imap_server, imap_port)
        client.login(imap_user, imap_password)
        status, _ = client.select(imap_mailbox, readonly=True)
        if status != 'OK':
            raise Exception(f"Failed to select mailbox '{imap_mailbox}'")

        status, data = client.uid('search', None, 'SINCE', since_arg)
        if status != 'OK':
            raise Exception('IMAP search failed')

        uid_list = [u for u in ((data[0] or b'').split()) if u]
        if len(uid_list) > max_messages:
            uid_list = uid_list[-max_messages:]

        for uid_bytes in reversed(uid_list):
            scanned += 1
            uid = uid_bytes.decode('utf-8', errors='ignore')
            try:
                f_status, fetched = client.uid('fetch', uid, '(BODY.PEEK[] FLAGS)')
                if f_status != 'OK' or not fetched:
                    continue

                raw_bytes = b''
                flags_raw = ''
                for part in fetched:
                    if not isinstance(part, tuple):
                        continue
                    if len(part) > 0 and isinstance(part[0], (bytes, bytearray)):
                        flags_raw += bytes(part[0]).decode('utf-8', errors='ignore')
                    if len(part) > 1 and isinstance(part[1], (bytes, bytearray)):
                        raw_bytes = bytes(part[1])
                if not raw_bytes:
                    continue

                parsed = _mail_parse_forwarded_amazon_email(raw_bytes, uid=uid, flags=flags_raw)
                if not parsed:
                    continue

                matched += 1
                _id, is_new = _mail_store_upsert_external(
                    store='Amazon',
                    direction='inbound',
                    status=parsed['status'],
                    sender_name=parsed['sender_name'],
                    recipient_name=parsed['recipient_name'],
                    subject=parsed['subject'],
                    body=parsed['body'],
                    external_source='amazon_forwarded_email',
                    external_id=parsed['external_id'],
                    external_payload=parsed['payload'],
                    created_at=parsed.get('created_at'),
                )
                if is_new:
                    inserted += 1
                else:
                    updated += 1
            except Exception as e:
                if first_error is None:
                    first_error = str(e)
                continue
    finally:
        if client is not None:
            try:
                client.close()
            except Exception:
                pass
            try:
                client.logout()
            except Exception:
                pass

    result = {'scanned': scanned, 'matched': matched, 'inserted': inserted, 'updated': updated}
    if first_error:
        result['error'] = first_error
    return result


def _mail_sync_amazon(days_back=7, max_orders=60):
    days_back = max(1, int(days_back or 7))
    max_orders = max(1, int(max_orders or 60))
    checked = 0
    inserted = 0
    updated = 0
    first_error = None

    try:
        from sp_api.api import Messaging
        credentials, _seller_id, marketplace_id, marketplace = ss_amazon_catalog._amazon_spapi_context()
        msg_api = Messaging(credentials=credentials, marketplace=marketplace)
        with ss_database.db_connection('sold.db') as conn:
            rows = conn.execute('''
                SELECT order_id, MAX(COALESCE(paid_time, shipped_time)) AS ts
                FROM orders
                WHERE store = 'amazon'
                  AND order_id IS NOT NULL
                  AND TRIM(order_id) != ''
                  AND date(COALESCE(paid_time, shipped_time, date('now'))) >= date('now', '-' || ? || ' days')
                GROUP BY order_id
                ORDER BY datetime(ts) DESC
                LIMIT ?
            ''', (days_back, max_orders)).fetchall()

        for r in rows:
            order_id = (r['order_id'] or '').strip()
            if not order_id:
                continue
            checked += 1
            try:
                resp = msg_api.get_messaging_actions_for_order(order_id, marketplaceIds=[marketplace_id])
                payload = getattr(resp, 'payload', None) or {}
            except Exception as e:
                if first_error is None:
                    first_error = str(e)
                continue

            actions = []
            links = (payload.get('_links') or {}) if isinstance(payload, dict) else {}
            raw_actions = links.get('actions') or []
            for a in raw_actions:
                if not isinstance(a, dict):
                    continue
                href = (a.get('href') or '').strip()
                name = (a.get('name') or '').strip() or href.split('/')[-1]
                if href:
                    actions.append({'name': name, 'href': href})

            if not actions:
                continue

            digest = hashlib.sha1(json.dumps(actions, sort_keys=True).encode('utf-8', errors='ignore')).hexdigest()[:16]
            ext_id = f"{order_id}:{digest}"
            subject = f"Amazon Order {order_id}: messaging actions updated"
            body_lines = ["Available Amazon messaging actions:"]
            for a in actions:
                body_lines.append(f"- {a.get('name')}: {a.get('href')}")
            body = '\n'.join(body_lines)
            _id, is_new = _mail_store_upsert_external(
                store='Amazon',
                direction='inbound',
                status='unread',
                sender_name='Amazon Messaging API',
                recipient_name='Sweet Shelves',
                subject=subject,
                body=body,
                external_source='amazon_order_actions',
                external_id=ext_id,
                external_payload={'order_id': order_id, 'actions': actions},
                created_at=None,
            )
            if is_new:
                inserted += 1
            else:
                updated += 1
    except Exception as e:
        if first_error is None:
            first_error = str(e)

    forwarded_result = {}
    try:
        forwarded_result = _mail_sync_amazon_forwarded_emails(
            days_back=days_back,
            max_messages=max(max_orders, min(max_orders * 4, 500)),
        )
    except Exception as e:
        forwarded_result = {'error': str(e)}
        if first_error is None:
            first_error = str(e)

    result = {
        'checked_orders': checked,
        'inserted': inserted,
        'updated': updated,
        'forwarded_email': forwarded_result,
        'inserted_total': inserted + int(forwarded_result.get('inserted') or 0),
        'updated_total': updated + int(forwarded_result.get('updated') or 0),
    }
    if first_error:
        result['error'] = first_error
        result['note'] = 'Amazon SP-API messaging may be limited. Forwarded-email sync can still ingest buyer messages.'
    return result


def _verify_sns_signature(message):
    """Verify AWS SNS message signature to ensure it came from Amazon."""
    try:
        # Validate the certificate URL is from Amazon
        cert_url = message.get('SigningCertUrl', '')
        if not cert_url.startswith('https://sns.'):
            return False
        if '.amazonaws.com/' not in cert_url:
            return False
        
        # Get the certificate from Amazon
        try:
            cert_response = requests.get(cert_url, timeout=5)
            if cert_response.status_code != 200:
                return False
            cert_pem = cert_response.text
        except Exception:
            return False
        
        # Verify the signature
        try:
            from cryptography.hazmat.primitives import serialization, hashes
            from cryptography.hazmat.primitives.asymmetric import padding
            from cryptography.hazmat.backends import default_backend
            from cryptography import x509
            import base64
            
            # Load certificate
            cert_obj = x509.load_pem_x509_certificate(
                cert_pem.encode('utf-8'),
                default_backend()
            )
            public_key = cert_obj.public_key()
            
            # Build the string to sign (order matters!)
            fields_to_sign = []
            for field in ['Message', 'MessageId', 'Subject', 'Timestamp', 'TopicArn', 'Type']:
                if field in message:
                    fields_to_sign.append(f"{field}\n{message[field]}")
            
            if not fields_to_sign:
                return False
            
            signing_string = '\n'.join(fields_to_sign)
            signature_b64 = message.get('Signature', '')
            signature = base64.b64decode(signature_b64)
            
            # Verify
            public_key.verify(
                signature,
                signing_string.encode('utf-8'),
                padding.PKCS1v15(),
                hashes.SHA256()
            )
            return True
        except Exception as e:
            print(f"SNS signature verification failed: {e}")
            return False
    
    except Exception as e:
        print(f"Error in SNS signature verification: {e}")
        return False


def api_amazon_sns_webhook():
    """Handle SNS notifications from Amazon when customer messages arrive on orders."""
    try:
        data = request.get_json(silent=True) or {}
        message_type = data.get('Type', '').strip()
        
        # Handle subscription confirmation (one-time only during setup)
        if message_type == 'SubscriptionConfirmation':
            subscribe_url = data.get('SubscribeURL', '').strip()
            if subscribe_url:
                try:
                    resp = requests.get(subscribe_url, timeout=10)
                    if resp.status_code == 200:
                        return jsonify({'success': True, 'message': 'Subscription confirmed'}), 200
                except Exception as e:
                    return jsonify({'error': f'Failed to confirm subscription: {str(e)}'}), 400
            return jsonify({'error': 'No SubscribeURL in message'}), 400
        
        # Handle actual notifications
        if message_type != 'Notification':
            return jsonify({'error': f'Unknown SNS message type: {message_type}'}), 400
        
        # Verify the signature is from Amazon
        if not _verify_sns_signature(data):
            return jsonify({'error': 'Invalid SNS signature - not from Amazon'}), 403
        
        # Parse the message content
        message_body = data.get('Message', '{}')
        try:
            if isinstance(message_body, str):
                message_data = json.loads(message_body)
            else:
                message_data = message_body
        except Exception:
            message_data = {}
        
        # Extract order ID (Amazon's structure varies)
        order_id = (
            message_data.get('orderId') or 
            message_data.get('order_id') or 
            message_data.get('AmazonOrderId')
        )
        
        if not order_id:
            # Still return 200 - don't want SNS retrying on data we can't parse
            return jsonify({'warning': 'No order ID found in SNS message', 'data': message_data}), 200
        
        # Store the notification in mail-center
        ss_mail_schema._ensure_storemail_tables()
        subject = f"📨 Message on Amazon order {order_id}"
        body = f"""A customer sent you a message on Amazon.

Order ID: {order_id}

Please log into Amazon Seller Central to view and reply to the message."""
        
        _id, is_new = _mail_store_upsert_external(
            store='Amazon',
            direction='inbound',
            status='unread',
            sender_name='Amazon Customer',
            recipient_name='Sweet Shelves',
            subject=subject,
            body=body,
            external_source='amazon_sns_message',
            external_id=f"{order_id}:{data.get('Timestamp', '')}",
            external_payload={'order_id': order_id, 'sns_data': message_data},
            created_at=data.get('Timestamp'),
        )
        
        return jsonify({
            'success': True,
            'order_id': order_id,
            'message_id': _id,
            'is_new': is_new
        }), 200
    
    except Exception as e:
        print(f"Amazon SNS webhook error: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'error': ss_errors._safe_error(e, 'amazon_sns_webhook')}), 500


def _mail_ebay_send_reply(*, item_id, recipient_id, parent_message_id, subject, body):
    token = (os.getenv("EBAY_OLDAUTH_TOKEN") or '').strip()
    if not token:
        raise Exception("Missing EBAY_OLDAUTH_TOKEN")
    if not item_id:
        raise Exception("Missing eBay ItemID for reply")
    if not recipient_id:
        raise Exception("Missing eBay RecipientID for reply")

    import html as _html
    subj = _html.escape(subject or 'Re: eBay Message')
    msg_body = _html.escape(body or '')
    recip = _html.escape(recipient_id)
    item = _html.escape(item_id)
    parent = _html.escape(parent_message_id or '')
    parent_xml = f"<ParentMessageID>{parent}</ParentMessageID>" if parent else ""
    xml_payload = f'''<?xml version="1.0" encoding="utf-8"?>
      <AddMemberMessageRTQRequest xmlns="urn:ebay:apis:eBLBaseComponents">
        <RequesterCredentials>
          <eBayAuthToken>{token}</eBayAuthToken>
        </RequesterCredentials>
        <ItemID>{item}</ItemID>
        <MemberMessage>
          <RecipientID>{recip}</RecipientID>
          {parent_xml}
          <Subject>{subj}</Subject>
          <Body>{msg_body}</Body>
          <DisplayToPublic>false</DisplayToPublic>
          <EmailCopyToSender>false</EmailCopyToSender>
        </MemberMessage>
      </AddMemberMessageRTQRequest>
    '''
    headers = _mail_ebay_headers('AddMemberMessageRTQ')
    resp = requests.post("https://api.ebay.com/ws/api.dll", headers=headers, data=xml_payload, timeout=30)
    if resp.status_code >= 400:
        raise Exception(f"eBay reply failed HTTP {resp.status_code}: {resp.text[:250]}")

    import xml.etree.ElementTree as ET
    ns = {'eb': 'urn:ebay:apis:eBLBaseComponents'}
    root = ET.fromstring(resp.text or '')
    ack = (root.findtext('.//eb:Ack', default='', namespaces=ns) or '').strip().lower()
    if ack not in ('success', 'warning'):
        err = root.findtext('.//eb:Errors/eb:LongMessage', default='', namespaces=ns) or \
              root.findtext('.//eb:Errors/eb:ShortMessage', default='', namespaces=ns) or \
              'Unknown eBay API error'
        raise Exception(f"eBay reply failed: {err}")
    external_id = hashlib.sha1(f"{item_id}|{recipient_id}|{subject}|{body}|{time.time()}".encode('utf-8', errors='ignore')).hexdigest()[:24]
    return {'external_id': external_id}


def api_mail_center_sync():
    """Pull live updates from eBay and Amazon into the centralized message store."""
    try:
        ss_mail_schema._ensure_storemail_tables()
        data = request.get_json(silent=True) if request.method == 'POST' else request.args
        data = data or {}
        store_req = (data.get('store') or 'all').strip().lower()
        if store_req not in ('all', 'ebay', 'amazon'):
            store_req = 'all'
        ebay_hours = int(data.get('ebay_hours', 72) or 72)
        ebay_pages = int(data.get('ebay_pages', 3) or 3)
        amazon_days = int(data.get('amazon_days', 7) or 7)
        amazon_orders = int(data.get('amazon_orders', 60) or 60)

        out = {'success': True, 'ebay': None, 'amazon': None, 'warnings': []}
        if store_req in ('all', 'ebay'):
            try:
                out['ebay'] = _mail_sync_ebay(hours_back=ebay_hours, max_pages=ebay_pages)
            except Exception as e:
                out['ebay'] = {'error': ss_errors._safe_error(e, 'mail_center:sync_ebay')}
                out['warnings'].append('eBay sync failed')
        if store_req in ('all', 'amazon'):
            try:
                out['amazon'] = _mail_sync_amazon(days_back=amazon_days, max_orders=amazon_orders)
                if isinstance(out.get('amazon'), dict):
                    if out['amazon'].get('error'):
                        out['warnings'].append('Amazon SP-API sync had issues')
                    forwarded = out['amazon'].get('forwarded_email') or {}
                    if isinstance(forwarded, dict) and forwarded.get('error'):
                        out['warnings'].append('Amazon forwarded-email sync had issues')
            except Exception as e:
                out['amazon'] = {'error': ss_errors._safe_error(e, 'mail_center:sync_amazon')}
                out['warnings'].append('Amazon sync failed')
        return jsonify(out)
    except Exception as e:
        return jsonify({'success': False, 'error': ss_errors._safe_error(e, 'mail_center:sync')}), 500


def api_mail_center_messages():
    try:
        ss_mail_schema._ensure_storemail_tables()
        store_raw = request.args.get('store', 'all')
        store = ss_mail_schema._normalize_mail_store(store_raw) if store_raw and store_raw.lower() != 'all' else None
        box = (request.args.get('box', 'inbox') or 'inbox').strip().lower()
        if box not in ('inbox', 'sent', 'all'):
            box = 'inbox'

        q = (request.args.get('q', '') or '').strip()
        limit = int(request.args.get('limit', 200) or 200)
        if limit < 1:
            limit = 1
        if limit > 500:
            limit = 500

        where = []
        params = []
        if store:
            where.append('store = ?')
            params.append(store)
        if box == 'inbox':
            where.append("direction = 'inbound'")
        elif box == 'sent':
            where.append("direction = 'outbound'")
        if q:
            where.append('(subject LIKE ? OR body LIKE ? OR sender_name LIKE ? OR recipient_name LIKE ?)')
            like = f'%{q}%'
            params.extend([like, like, like, like])

        where_sql = f"WHERE {' AND '.join(where)}" if where else ''
        sql = f'''
            SELECT id, store, direction, status, sender_name, recipient_name, subject, body, reply_to_id,
                   external_source, external_id, external_payload,
                   created_at, sent_at, read_at
            FROM store_messages
            {where_sql}
            ORDER BY
                CASE WHEN direction = 'inbound' AND status = 'unread' THEN 0 ELSE 1 END,
                datetime(created_at) DESC,
                id DESC
            LIMIT ?
        '''
        params.append(limit)

        with ss_database.db_connection('storemail.db') as conn:
            rows = conn.execute(sql, params).fetchall()
        return jsonify({'success': True, 'messages': [_mail_message_to_dict(r) for r in rows]})
    except Exception as e:
        return jsonify({'success': False, 'error': ss_errors._safe_error(e, 'mail_center:list')}), 500


def api_mail_center_create_message():
    """Create a new inbound message (useful for ingestion/testing)."""
    try:
        ss_mail_schema._ensure_storemail_tables()
        data = request.get_json(silent=True) or {}
        store = ss_mail_schema._normalize_mail_store(data.get('store'))
        body = (data.get('body') or '').strip()
        sender_name = (data.get('sender_name') or '').strip()
        subject = (data.get('subject') or '').strip()
        if not store:
            return jsonify({'success': False, 'error': 'Valid store is required (Amazon or eBay)'}), 400
        if not body:
            return jsonify({'success': False, 'error': 'Message body is required'}), 400

        with ss_database.db_connection('storemail.db') as conn:
            cur = conn.cursor()
            cur.execute('''
                INSERT INTO store_messages (store, direction, status, sender_name, subject, body)
                VALUES (?, 'inbound', 'unread', ?, ?, ?)
            ''', (store, sender_name, subject, body))
            message_id = cur.lastrowid
            row = conn.execute('''
                SELECT id, store, direction, status, sender_name, recipient_name, subject, body, reply_to_id,
                       external_source, external_id, external_payload,
                       created_at, sent_at, read_at
                FROM store_messages WHERE id = ?
            ''', (message_id,)).fetchone()
        return jsonify({'success': True, 'message': _mail_message_to_dict(row)})
    except Exception as e:
        return jsonify({'success': False, 'error': ss_errors._safe_error(e, 'mail_center:create_inbound')}), 500


def api_mail_center_send():
    try:
        ss_mail_schema._ensure_storemail_tables()
        data = request.get_json(silent=True) or {}
        store = ss_mail_schema._normalize_mail_store(data.get('store'))
        recipient_name = (data.get('recipient_name') or '').strip()
        sender_name = (data.get('sender_name') or 'Sweet Shelves').strip()
        subject = (data.get('subject') or '').strip()
        body = (data.get('body') or '').strip()
        reply_to_id = data.get('reply_to_id')

        if not store:
            return jsonify({'success': False, 'error': 'Valid store is required (Amazon or eBay)'}), 400
        if not body:
            return jsonify({'success': False, 'error': 'Message body is required'}), 400

        reply_to_value = None
        external_source = None
        external_id = None
        external_payload = None
        if str(reply_to_id or '').strip():
            try:
                reply_to_value = int(reply_to_id)
            except Exception:
                return jsonify({'success': False, 'error': 'reply_to_id must be an integer'}), 400
            with ss_database.db_connection('storemail.db') as conn:
                parent = conn.execute('''
                    SELECT id, store, direction, external_source, external_id, external_payload
                    FROM store_messages
                    WHERE id = ?
                    LIMIT 1
                ''', (reply_to_value,)).fetchone()
            if parent and parent['store'] == 'eBay' and parent['direction'] == 'inbound':
                parent_payload = _mail_parse_payload(parent['external_payload'])
                item_id = (parent_payload.get('item_id') or '').strip()
                sender_id = (parent_payload.get('sender_id') or '').strip()
                parent_mid = (parent['external_id'] or '').strip()
                if item_id and sender_id:
                    sent_live = _mail_ebay_send_reply(
                        item_id=item_id,
                        recipient_id=sender_id,
                        parent_message_id=parent_mid,
                        subject=subject or 'Re: eBay Message',
                        body=body,
                    )
                    external_source = 'ebay_member_message_reply'
                    external_id = sent_live.get('external_id')
                    external_payload = {
                        'item_id': item_id,
                        'recipient_id': sender_id,
                        'parent_message_id': parent_mid,
                        'live_sent': True,
                    }

        with ss_database.db_connection('storemail.db') as conn:
            cur = conn.cursor()
            cur.execute('''
                INSERT INTO store_messages (
                    store, direction, status, sender_name, recipient_name, subject, body, reply_to_id,
                    external_source, external_id, external_payload, sent_at
                ) VALUES (?, 'outbound', 'sent', ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ''', (
                store, sender_name, recipient_name, subject, body, reply_to_value,
                external_source, external_id, json.dumps(external_payload or {}, ensure_ascii=True)
            ))
            message_id = cur.lastrowid
            row = conn.execute('''
                SELECT id, store, direction, status, sender_name, recipient_name, subject, body, reply_to_id,
                       external_source, external_id, external_payload,
                       created_at, sent_at, read_at
                FROM store_messages WHERE id = ?
            ''', (message_id,)).fetchone()
        return jsonify({'success': True, 'message': _mail_message_to_dict(row)})
    except Exception as e:
        return jsonify({'success': False, 'error': ss_errors._safe_error(e, 'mail_center:send')}), 500


def api_mail_center_reply():
    try:
        ss_mail_schema._ensure_storemail_tables()
        data = request.get_json(silent=True) or {}
        message_id = data.get('message_id')
        body = (data.get('body') or '').strip()
        if not str(message_id or '').strip():
            return jsonify({'success': False, 'error': 'message_id is required'}), 400
        if not body:
            return jsonify({'success': False, 'error': 'Reply body is required'}), 400

        try:
            message_id = int(message_id)
        except Exception:
            return jsonify({'success': False, 'error': 'message_id must be an integer'}), 400

        live_info = None
        with ss_database.db_connection('storemail.db') as conn:
            cur = conn.cursor()
            original = cur.execute('''
                SELECT id, store, direction, sender_name, recipient_name, subject, external_source, external_id, external_payload
                FROM store_messages
                WHERE id = ?
            ''', (message_id,)).fetchone()
            if not original:
                return jsonify({'success': False, 'error': 'Original message not found'}), 404

            original_subject = (original['subject'] or '').strip()
            if original_subject and original_subject.lower().startswith('re:'):
                subject = original_subject
            elif original_subject:
                subject = f"Re: {original_subject}"
            else:
                subject = 'Re: Message'

            recipient_name = (original['sender_name'] or original['recipient_name'] or '').strip()
            external_source = None
            external_id = None
            external_payload = None
            if original['store'] == 'eBay' and original['direction'] == 'inbound':
                parent_payload = _mail_parse_payload(original['external_payload'])
                item_id = (parent_payload.get('item_id') or '').strip()
                sender_id = (parent_payload.get('sender_id') or '').strip()
                parent_mid = (original['external_id'] or '').strip()
                if item_id and sender_id:
                    sent_live = _mail_ebay_send_reply(
                        item_id=item_id,
                        recipient_id=sender_id,
                        parent_message_id=parent_mid,
                        subject=subject,
                        body=body,
                    )
                    live_info = {'live_sent': True, 'provider': 'eBay'}
                    external_source = 'ebay_member_message_reply'
                    external_id = sent_live.get('external_id')
                    external_payload = {
                        'item_id': item_id,
                        'recipient_id': sender_id,
                        'parent_message_id': parent_mid,
                        'live_sent': True,
                    }

            cur.execute('''
                INSERT INTO store_messages (
                    store, direction, status, sender_name, recipient_name, subject, body, reply_to_id,
                    external_source, external_id, external_payload, sent_at
                ) VALUES (?, 'outbound', 'sent', ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ''', (
                original['store'], 'Sweet Shelves', recipient_name, subject, body, original['id'],
                external_source, external_id, json.dumps(external_payload or {}, ensure_ascii=True)
            ))
            sent_id = cur.lastrowid

            cur.execute('''
                UPDATE store_messages
                SET status = CASE WHEN direction = 'inbound' THEN 'read' ELSE status END,
                    read_at = CASE WHEN direction = 'inbound' THEN COALESCE(read_at, CURRENT_TIMESTAMP) ELSE read_at END
                WHERE id = ?
            ''', (original['id'],))

            row = cur.execute('''
                SELECT id, store, direction, status, sender_name, recipient_name, subject, body, reply_to_id,
                       external_source, external_id, external_payload,
                       created_at, sent_at, read_at
                FROM store_messages WHERE id = ?
            ''', (sent_id,)).fetchone()

        return jsonify({'success': True, 'message': _mail_message_to_dict(row), 'live': live_info})
    except Exception as e:
        return jsonify({'success': False, 'error': ss_errors._safe_error(e, 'mail_center:reply')}), 500


def api_mail_center_mark_read(message_id):
    try:
        ss_mail_schema._ensure_storemail_tables()
        with ss_database.db_connection('storemail.db') as conn:
            cur = conn.cursor()
            cur.execute('''
                UPDATE store_messages
                SET status = CASE WHEN direction = 'inbound' THEN 'read' ELSE status END,
                    read_at = CASE WHEN direction = 'inbound' THEN COALESCE(read_at, CURRENT_TIMESTAMP) ELSE read_at END
                WHERE id = ?
            ''', (message_id,))
            if cur.rowcount < 1:
                return jsonify({'success': False, 'error': 'Message not found'}), 404
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': ss_errors._safe_error(e, 'mail_center:mark_read')}), 500


def mail_center_refresh_worker():
    """Background worker to refresh mail-center message data on a fixed interval."""
    import time as _time
    interval_seconds = _mail_center_env_int('MAIL_CENTER_REFRESH_INTERVAL_SECONDS', 3600, 300, 86400)
    startup_delay = _mail_center_env_int('MAIL_CENTER_REFRESH_STARTUP_DELAY_SECONDS', 45, 0, 900)
    print(f"📬 Mail-center refresh worker started (every {interval_seconds // 60} minutes)")

    if startup_delay > 0:
        _time.sleep(startup_delay)

    while True:
        loop_started = _time.time()
        try:
            summary = _mail_center_hourly_refresh_once()
            warnings = summary.get('warnings') if isinstance(summary, dict) else []
            if warnings:
                print(f"📬 Mail-center refresh completed with warnings: {', '.join(warnings)}")
            else:
                print("📬 Mail-center refresh completed")
        except Exception as e:
            print(f"⚠️ Mail-center refresh worker error: {e}")

        elapsed = _time.time() - loop_started
        sleep_for = max(30, interval_seconds - int(elapsed))
        _time.sleep(sleep_for)


def _start_mail_center_refresh_thread():
    """Start hourly lightweight refresh for centralized message data."""
    refresh_thread = threading.Thread(target=mail_center_refresh_worker, daemon=True)
    refresh_thread.start()
    print("🚀 Mail-center refresh thread started")
