"""Facebook Marketplace messages: the server side of the Lister extension's inbox reader.

Meta has no API for Marketplace chats, so the Sweet Shelves Lister extension reads the
Marketplace inbox page (facebook.com/marketplace/inbox) in the user's own signed-in Chrome and
posts what it sees here: one entry per chat row, as raw text lines plus a few hints (link,
bold text, which Selling/Buying tab was open). Parsing happens here rather than in the extension
so that when Facebook changes its page we fix one Python file on the Pi instead of shipping a
new extension release.

Only the Marketplace inbox is read, so personal Messenger chats never reach this module; every
thread stored here is a Marketplace conversation, tagged Selling or Buying.

A new buyer message (a snippet that changed and was not written by us) sends a Telegram alert
that starts with the Marketplace tag, to the linked Telegram of whoever runs the reader, or to
every enabled chat when that person has not linked one. /fb-messages lists the chats.

Shared verbatim by the modular app (sweetshelves/routing.py) and the Desktop debby app.py;
dependencies come in through register(app, deps).
"""

import datetime
import hashlib
import json
import re
import sqlite3
import threading
from pathlib import Path

from flask import jsonify, render_template, request

MUTATION_HEADER = 'X-Sweet-Shelves-Lister'
DB_NAME = 'fbmessages.db'
MAX_ROWS = 80
STALE_MINUTES = 20  # the extension reloads the inbox tab every 10 minutes

# Relative stamps the inbox prints next to a snippet: "2m", "1 h", "3d", "1w", "Just now", "Mon", "Sep 12".
TIME_RE = re.compile(
    r'^(?:just now|now|yesterday|\d{1,2}\s?(?:s|m|min|mins|h|hr|hrs|d|w|wk|wks|mo|y|yr)|'
    r'(?:mon|tue|wed|thu|fri|sat|sun)[a-z]*|'
    r'(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.? \d{1,2}(?:,? \d{4})?|'
    r'\d{1,2}:\d{2}\s?(?:am|pm)?|\d{1,2}/\d{1,2}(?:/\d{2,4})?)$', re.I)
# Row chrome that is not part of the conversation.
NOISE = {'·', '•', 'unread', 'mark as read', 'mark as unread', 'active now', 'marketplace', 'new',
         'seen', 'delivered', 'sent', 'more', 'options', 'archive', 'message', 'listing', 'sold',
         'pending', 'available'}
ME_PREFIXES = ('you:', 'you sent', 'you replied', 'you reacted', 'you marked', 'you changed',
               'you unsent', 'you named', 'you created')
INBOX_URL = 'https://www.facebook.com/marketplace/inbox/'
# Rows the reader found inside the Marketplace list itself. Readers before 0.2.60 also picked up
# Messenger's dropdown / chat pop-ups (every personal chat), so rows without this mark are dropped.
MARKETPLACE_AREA = 'marketplace-inbox'
# Bumped when stored chats must be thrown away (2: 0.2.59 had mixed personal chats in).
SCHEMA = '2'
THREAD_RE = re.compile(r'/(?:messages|marketplace)/t/(\d{5,})')


def _now():
    return datetime.datetime.now().isoformat(timespec='seconds')


def _text(value, limit=None):
    text = re.sub(r'\s+', ' ', str(value if value is not None else '')).strip()
    return text[:limit] if limit else text


def _minutes_since(stamp):
    try:
        return (datetime.datetime.now() - datetime.datetime.fromisoformat(stamp)).total_seconds() / 60
    except Exception:
        return None


def _same_snippet(a, b):
    """True when two captures show the same message. The inbox cuts snippets to the column width,
    so a resized window can show a longer or shorter piece of the same text."""
    a = _text(a).rstrip('.… ').lower()
    b = _text(b).rstrip('.… ').lower()
    if not a or not b:
        return a == b
    return a == b or a.startswith(b) or b.startswith(a)


def parse_row(row):
    """One captured inbox row -> {thread_id, name, item, snippet, from_me, time, unread}.

    Expected shape (Facebook changes it now and then, which is why this lives on the server):
        line 1   "Jane Doe · Nike Air Max 90"          the buyer and the listing
        line 2   "Is this still available? · 2h"       the last message and its age
    Variants seen on other layouts put the age on its own line, prefix the snippet with
    "Jane:" or "You:", or leave the listing title off the first line.
    """
    href = _text(row.get('href'), 400)
    match = THREAD_RE.search(href)
    thread_id = _text(row.get('threadId'), 40) or (match.group(1) if match else '')
    lines = []
    for raw in row.get('lines') or []:
        line = _text(raw, 400)
        if line and line.lower() not in NOISE:
            lines.append(line)
    stamp = ''
    # The age is either its own line or the last " · " piece of a line (usually the snippet line).
    for index in range(len(lines) - 1, 0, -1):
        line = lines[index]
        if TIME_RE.match(line.lstrip('· ').strip()):
            stamp = line.lstrip('· ').strip()
            lines.pop(index)
            break
        head, sep, tail = line.rpartition(' · ')
        if sep and TIME_RE.match(tail.strip()):
            stamp = tail.strip()
            lines[index] = head.strip()
            break
    header = lines[0] if lines else _text(row.get('label'), 300)
    name, sep, item = header.partition(' · ')
    if not sep:
        item = _text(row.get('item'), 300)
    snippet = ' '.join(lines[1:]).strip()
    from_me = snippet.lower().startswith(ME_PREFIXES)
    if from_me:
        snippet = re.sub(r'^you:\s*', '', snippet, flags=re.I)
    elif name and snippet.lower().startswith(name.split(' ')[0].lower() + ':'):
        snippet = snippet.split(':', 1)[1].strip()
    # Unread: the inbox prints an unread snippet in bold. The extension reports each text piece with
    # its font weight; the name line is always bold, so only the snippet pieces count.
    unread = bool(row.get('unread'))
    for piece in row.get('bold') or []:
        piece = _text(piece, 400)
        if piece and snippet and piece != header and (piece in snippet or snippet.startswith(piece[:40])):
            unread = True
            break
    if not thread_id and (name or item):
        # The Marketplace list has no per-chat link; the buyer + listing pair names the chat.
        thread_id = 'mp-' + hashlib.sha1(f'{name.strip().lower()}|{item.strip().lower()}'.encode('utf-8')).hexdigest()[:16]
    return {
        'thread_id': thread_id,
        'href': href if href.startswith('https://www.facebook.com/') else
                ('https://www.facebook.com/messages/t/' + thread_id if thread_id.isdigit() else INBOX_URL),
        'name': _text(name, 120),
        'item': _text(item, 200),
        'snippet': _text(snippet, 500),
        'from_me': from_me,
        'time': _text(stamp, 40),
        'unread': unread,
        'thumb': _text(row.get('img'), 600) if _text(row.get('img')).startswith('https://') else '',
    }


def alert_text(thread, role):
    tag = '🛒 Facebook Marketplace' + (' · ' + role.capitalize() if role in ('selling', 'buying') else '')
    who = thread.get('name') or 'Someone'
    about = f' about "{thread["item"]}"' if thread.get('item') else ''
    lines = [tag, f'{who}{about}:', f'"{thread.get("snippet") or "(photo or attachment)"}"']
    if thread.get('href'):
        lines.append('Reply: ' + thread['href'])
    return '\n'.join(lines)


class FbMarketplace:
    def __init__(self, deps):
        base_dir = deps.get('BASE_DIR') or Path('.')
        self.db_path = str(Path(base_dir) / DB_NAME)
        self.telegram_send = deps.get('_telegram_send_message')
        self.telegram_recipients = deps.get('_telegram_collect_recipient_rows')
        self.telegram_chat_for = deps.get('_telegram_chat_for_email')
        self.safe_error = deps.get('_safe_error') or (lambda e, context='': str(e))
        self._lock = threading.Lock()

    def connect(self):
        conn = sqlite3.connect(self.db_path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute('PRAGMA journal_mode=WAL')
        conn.executescript('''
            CREATE TABLE IF NOT EXISTS fb_threads (
                thread_id TEXT PRIMARY KEY,
                role TEXT DEFAULT '',
                name TEXT DEFAULT '',
                item TEXT DEFAULT '',
                snippet TEXT DEFAULT '',
                from_me INTEGER DEFAULT 0,
                time_text TEXT DEFAULT '',
                unread INTEGER DEFAULT 0,
                href TEXT DEFAULT '',
                thumb TEXT DEFAULT '',
                first_seen TEXT,
                updated_at TEXT,
                last_message_at TEXT,
                last_alert_snippet TEXT DEFAULT '',
                last_alert_at TEXT,
                handled_snippet TEXT DEFAULT '',
                handled_at TEXT
            );
            CREATE TABLE IF NOT EXISTS fb_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                thread_id TEXT NOT NULL,
                snippet TEXT,
                from_me INTEGER DEFAULT 0,
                seen_at TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_fb_messages_thread ON fb_messages(thread_id, id);
            CREATE TABLE IF NOT EXISTS fb_reader_state (
                key TEXT PRIMARY KEY,
                value TEXT
            );
        ''')
        if self._state(conn, 'schema') != SCHEMA:
            # Chats stored by an older reader cannot be trusted (0.2.59 mixed personal chats in).
            conn.execute('DELETE FROM fb_threads')
            conn.execute('DELETE FROM fb_messages')
            conn.execute("DELETE FROM fb_reader_state WHERE key = 'baseline_at'")
            self._set_state(conn, 'schema', SCHEMA)
            conn.commit()
        return conn

    @staticmethod
    def _state(conn, key, default=''):
        row = conn.execute('SELECT value FROM fb_reader_state WHERE key = ?', (key,)).fetchone()
        return row['value'] if row else default

    @staticmethod
    def _set_state(conn, key, value):
        conn.execute('INSERT OR REPLACE INTO fb_reader_state (key, value) VALUES (?, ?)', (key, str(value)))

    def capture(self, data, *, email=''):
        rows = data.get('rows') or []
        if not isinstance(rows, list):
            raise ValueError('rows must be a list')
        role = _text(data.get('role'), 20).lower()
        role = role if role in ('selling', 'buying') else ''
        now = _now()
        parsed, seen, dropped = [], set(), 0
        for row in rows[:MAX_ROWS]:
            if not isinstance(row, dict):
                continue
            if row.get('area') != MARKETPLACE_AREA:
                dropped += 1  # not from the Marketplace list: a personal Messenger chat, never stored
                continue
            thread = parse_row(row)
            if thread['thread_id'] and thread['thread_id'] not in seen:
                seen.add(thread['thread_id'])
                parsed.append(thread)
        alerts, new_threads, changed = [], 0, 0
        with self._lock:
            conn = self.connect()
            try:
                # The first capture ever only records what is already in the inbox; alerting on every
                # old chat at once would bury the phone.
                baseline = bool(self._state(conn, 'baseline_at'))
                for thread in parsed:
                    old = conn.execute('SELECT * FROM fb_threads WHERE thread_id = ?', (thread['thread_id'],)).fetchone()
                    is_new_message = bool(thread['snippet']) and (old is None or not _same_snippet(old['snippet'], thread['snippet']))
                    if old is None:
                        new_threads += 1
                        conn.execute(
                            'INSERT INTO fb_threads (thread_id, role, name, item, snippet, from_me, time_text, unread, href, thumb, '
                            'first_seen, updated_at, last_message_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
                            (thread['thread_id'], role, thread['name'], thread['item'], thread['snippet'], int(thread['from_me']),
                             thread['time'], int(thread['unread']), thread['href'], thread['thumb'], now, now, now))
                    else:
                        conn.execute(
                            'UPDATE fb_threads SET role = COALESCE(NULLIF(?, \'\'), role), name = COALESCE(NULLIF(?, \'\'), name), '
                            'item = COALESCE(NULLIF(?, \'\'), item), snippet = ?, from_me = ?, time_text = ?, unread = ?, '
                            'href = COALESCE(NULLIF(?, \'\'), href), thumb = COALESCE(NULLIF(?, \'\'), thumb), updated_at = ?, '
                            'last_message_at = CASE WHEN ? THEN ? ELSE last_message_at END WHERE thread_id = ?',
                            (role, thread['name'], thread['item'], thread['snippet'] if is_new_message else old['snippet'],
                             int(thread['from_me']) if is_new_message else old['from_me'], thread['time'], int(thread['unread']),
                             thread['href'], thread['thumb'], now, int(is_new_message), now, thread['thread_id']))
                    if not is_new_message:
                        continue
                    changed += 1
                    conn.execute('INSERT INTO fb_messages (thread_id, snippet, from_me, seen_at) VALUES (?, ?, ?, ?)',
                                 (thread['thread_id'], thread['snippet'], int(thread['from_me']), now))
                    already = old is not None and _same_snippet(old['last_alert_snippet'], thread['snippet'])
                    if baseline and not thread['from_me'] and not already:
                        alerts.append(dict(thread, role=role or (old['role'] if old else '')))
                        conn.execute('UPDATE fb_threads SET last_alert_snippet = ?, last_alert_at = ? WHERE thread_id = ?',
                                     (thread['snippet'], now, thread['thread_id']))
                if parsed and not baseline:
                    self._set_state(conn, 'baseline_at', now)
                self._set_state(conn, 'last_capture_at', now)
                self._set_state(conn, 'last_capture_rows', len(parsed))
                self._set_state(conn, 'last_capture_raw', len(rows))
                self._set_state(conn, 'last_capture_role', role)
                self._set_state(conn, 'last_capture_email', email)
                self._set_state(conn, 'last_capture_version', _text(data.get('version'), 20))
                # Kept for fixing the parser when Facebook changes the page: what the first rows looked like.
                self._set_state(conn, 'last_capture_sample', json.dumps(rows[:4], ensure_ascii=False)[:8000])
                probe = data.get('probe') if not parsed else None
                self._set_state(conn, 'last_capture_dropped', dropped)
                self._set_state(conn, 'last_capture_probe', json.dumps(probe, ensure_ascii=False)[:4000] if probe else '')
                conn.commit()
            finally:
                conn.close()
        if alerts:
            # Telegram can stall for seconds (IPv6 connects on the Pi); never make the reader wait on it.
            threading.Thread(target=self._send_alerts, args=(alerts, email), daemon=True).start()
        return {'threads': len(parsed), 'dropped': dropped, 'new_threads': new_threads, 'changed': changed,
                'alerts': len(alerts), 'baseline': not baseline and bool(parsed)}

    def _targets(self, email):
        if email and callable(self.telegram_chat_for):
            try:
                own = self.telegram_chat_for(email)
            except Exception:
                own = None
            if own and _text(own.get('chat_id')):
                return [_text(own['chat_id'])]
        chats = []
        if callable(self.telegram_recipients):
            try:
                rows = self.telegram_recipients() or []
            except Exception:
                rows = []
            for row in rows:
                chat_id = _text((row or {}).get('chat_id'))
                try:
                    enabled = int((row or {}).get('enabled') or 0) == 1
                except Exception:
                    enabled = False
                if chat_id and enabled and chat_id not in chats:
                    chats.append(chat_id)
        return chats

    def _send_alerts(self, alerts, email):
        if not callable(self.telegram_send):
            return
        errors = []
        for chat_id in self._targets(email):
            for thread in alerts:
                try:
                    ok, result = self.telegram_send(chat_id, alert_text(thread, thread.get('role') or ''),
                                                    disable_notification=False)
                except Exception as e:
                    ok, result = False, str(e)
                if not ok:
                    errors.append(_text(result, 200))
        try:
            conn = self.connect()
            try:
                self._set_state(conn, 'last_alert_error', errors[0] if errors else '')
                self._set_state(conn, 'last_alert_sent_at', _now())
                conn.commit()
            finally:
                conn.close()
        except Exception:
            pass

    def threads(self, *, limit=200):
        conn = self.connect()
        try:
            rows = conn.execute('SELECT * FROM fb_threads ORDER BY COALESCE(last_message_at, updated_at) DESC LIMIT ?',
                                (int(limit),)).fetchall()
            history = {}
            for row in conn.execute('SELECT thread_id, snippet, from_me, seen_at FROM fb_messages '
                                    'WHERE id IN (SELECT id FROM fb_messages ORDER BY id DESC LIMIT 2000) ORDER BY id DESC'):
                bucket = history.setdefault(row['thread_id'], [])
                if len(bucket) < 8:
                    bucket.append({'snippet': row['snippet'], 'fromMe': bool(row['from_me']), 'seenAt': row['seen_at']})
            state = {key: self._state(conn, key) for key in (
                'last_capture_at', 'last_capture_rows', 'last_capture_raw', 'last_capture_role', 'last_capture_version',
                'last_alert_error', 'last_alert_sent_at', 'baseline_at', 'last_capture_probe', 'last_capture_dropped')}
        finally:
            conn.close()
        threads = []
        for row in rows:
            waiting = (not row['from_me']) and bool(row['snippet']) and not _same_snippet(row['handled_snippet'], row['snippet'])
            threads.append({
                'threadId': row['thread_id'], 'role': row['role'] or '', 'name': row['name'], 'item': row['item'],
                'snippet': row['snippet'], 'fromMe': bool(row['from_me']), 'time': row['time_text'],
                'unread': bool(row['unread']), 'href': row['href'], 'thumb': row['thumb'],
                'firstSeen': row['first_seen'], 'lastMessageAt': row['last_message_at'], 'needsReply': waiting,
                'history': history.get(row['thread_id'], []),
            })
        age = _minutes_since(state['last_capture_at']) if state['last_capture_at'] else None
        reader = {
            'lastCaptureAt': state['last_capture_at'], 'minutesAgo': None if age is None else round(age, 1),
            'stale': age is None or age > STALE_MINUTES, 'rows': state['last_capture_rows'],
            'rawRows': state['last_capture_raw'], 'role': state['last_capture_role'],
            'version': state['last_capture_version'], 'alertError': state['last_alert_error'],
            'lastAlertAt': state['last_alert_sent_at'], 'baselineAt': state['baseline_at'],
            'noRowsOnPage': bool(state['last_capture_probe']),
            'droppedPersonal': int(state['last_capture_dropped'] or 0),
        }
        return {'threads': threads, 'reader': reader}

    def mark_handled(self, thread_id, *, handled=True):
        conn = self.connect()
        try:
            row = conn.execute('SELECT snippet FROM fb_threads WHERE thread_id = ?', (thread_id,)).fetchone()
            if row is None:
                raise KeyError(thread_id)
            conn.execute('UPDATE fb_threads SET handled_snippet = ?, handled_at = ? WHERE thread_id = ?',
                         (row['snippet'] if handled else '', _now() if handled else None, thread_id))
            conn.commit()
        finally:
            conn.close()
        return {'threadId': thread_id, 'handled': handled}


def register(app, deps):
    market = FbMarketplace(deps)
    app.extensions['sweetshelves_fb_marketplace'] = market

    def signed_in_email():
        return _text(request.headers.get('Cf-Access-Authenticated-User-Email'), 120).lower()

    def guarded():
        # The reader posts from the extension's service worker (a cross-site request carrying the
        # Lister header); a page on another site cannot set that header without a CORS preflight.
        return request.headers.get('Sec-Fetch-Site') == 'cross-site' and not request.headers.get(MUTATION_HEADER)

    def api_fb_marketplace_capture():
        if guarded():
            return jsonify({'success': False, 'error': 'Use the Sweet Shelves Lister extension.'}), 403
        try:
            result = market.capture(request.get_json(silent=True) or {}, email=signed_in_email())
            return jsonify({'success': True, **result})
        except ValueError as e:
            return jsonify({'success': False, 'error': str(e)}), 400
        except Exception as e:
            return jsonify({'success': False, 'error': market.safe_error(e, 'fb_marketplace:capture')}), 500

    def api_fb_marketplace_threads():
        try:
            return jsonify({'success': True, **market.threads()})
        except Exception as e:
            return jsonify({'success': False, 'error': market.safe_error(e, 'fb_marketplace:threads')}), 500

    def api_fb_marketplace_handled(thread_id):
        if guarded():
            return jsonify({'success': False, 'error': 'Use a Sweet Shelves page for this action.'}), 403
        try:
            data = request.get_json(silent=True) or {}
            return jsonify({'success': True, **market.mark_handled(_text(thread_id, 40), handled=bool(data.get('handled', True)))})
        except KeyError:
            return jsonify({'success': False, 'error': 'Unknown chat'}), 404
        except Exception as e:
            return jsonify({'success': False, 'error': market.safe_error(e, 'fb_marketplace:handled')}), 500

    def fb_messages_page():
        return render_template('fb_messages.html')

    app.add_url_rule('/api/fb-marketplace/capture', 'api_fb_marketplace_capture', api_fb_marketplace_capture, methods=['POST'])
    app.add_url_rule('/api/fb-marketplace/threads', 'api_fb_marketplace_threads', api_fb_marketplace_threads)
    app.add_url_rule('/api/fb-marketplace/threads/<thread_id>/handled', 'api_fb_marketplace_handled',
                     api_fb_marketplace_handled, methods=['POST'])
    app.add_url_rule('/fb-messages', 'fb_messages_page', fb_messages_page)
    return market
