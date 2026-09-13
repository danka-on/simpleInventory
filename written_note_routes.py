"""Manual, source-bound translations of warehouse notes and item-prep text."""
from contextlib import closing, contextmanager
import hashlib
import os
from pathlib import Path
import sqlite3
import time
import uuid

from flask import jsonify, request
from voice_note_routes import VoiceError, LEASE_SECONDS

SOURCES = {
    'warehouse': ('searchRack.db', 'SEARCHRACK', 'WAREHOUSE_NOTE', 'BARCODE'),
    'prep-note': ('bol.db', 'items_prep_notes', 'note', 'upc'),
    'prep-reason': ('bol.db', 'items_prep_status', 'reason', 'upc'),
    'prep-status-note': ('bol.db', 'items_prep_status', 'note', 'upc'),
}


class WrittenNotes:
    def __init__(self, base_dir, voice_service):
        self.root = Path(base_dir)
        self.voice = voice_service

    @contextmanager
    def connection(self):
        conn = sqlite3.connect(self.root / 'bol.db', timeout=10)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute('''CREATE TABLE IF NOT EXISTS written_note_translations (
                source_kind TEXT NOT NULL, source_id INTEGER NOT NULL, fingerprint TEXT NOT NULL,
                english TEXT NOT NULL DEFAULT '', updated_at REAL NOT NULL DEFAULT 0,
                lease_until REAL NOT NULL DEFAULT 0, token TEXT NOT NULL DEFAULT '',
                error TEXT NOT NULL DEFAULT '', PRIMARY KEY (source_kind, source_id))''')
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def source(self, kind, note_id):
        if kind not in SOURCES:
            raise VoiceError('Unknown note type.', 400)
        db, table, field, identity = SOURCES[kind]
        with closing(sqlite3.connect((self.root / db).resolve().as_uri() + '?mode=ro', uri=True)) as conn:
            # Identifiers come only from the fixed allowlist above.
            row = conn.execute(f'SELECT {field}, {identity} FROM {table} WHERE id=?', (note_id,)).fetchone()
        if not row or not str(row[0] or '').strip():
            raise VoiceError('Written note not found.', 404)
        original = str(row[0]).replace('\r\n', '\n').strip()
        if len(original) > 20000:
            raise VoiceError('This note is too long to translate (maximum 20,000 characters).', 413)
        fingerprint = hashlib.sha256((str(row[1]) + '\0' + original).encode('utf-8')).hexdigest()
        return original, fingerprint

    @staticmethod
    def payload(original, row=None):
        row = dict(row) if row else {}
        return dict(original=original, english=row.get('english', ''), error=row.get('error', ''),
            status='processing' if row.get('lease_until', 0) > time.time() else
                   ('complete' if row.get('english') else 'pending'))

    def get(self, kind, note_id):
        original, fingerprint = self.source(kind, note_id)
        with self.connection() as conn:
            row = conn.execute('''SELECT * FROM written_note_translations
                WHERE source_kind=? AND source_id=? AND fingerprint=?''', (kind, note_id, fingerprint)).fetchone()
        return self.payload(original, row)

    def translate(self, kind, note_id, expected_text):
        original, fingerprint = self.source(kind, note_id)
        if not isinstance(expected_text, str) or expected_text.replace('\r\n', '\n').strip() != original:
            raise VoiceError('The note has changed. Reopen the item before translating.', 409)
        token = uuid.uuid4().hex
        with self.connection() as conn:
            conn.execute('BEGIN IMMEDIATE')
            row = conn.execute('SELECT * FROM written_note_translations WHERE source_kind=? AND source_id=?',
                               (kind, note_id)).fetchone()
            if row and row['lease_until'] > time.time():
                raise VoiceError('This note is already being translated. Please wait.', 409)
            if row and row['fingerprint'] == fingerprint and row['english']:
                return self.payload(original, row)
            if not os.getenv('ANTHROPIC_API_KEY', '').strip():
                raise VoiceError('Written translation needs ANTHROPIC_API_KEY configured on the server.', 503)
            if conn.execute('SELECT COUNT(*) FROM written_note_translations WHERE lease_until>?',
                            (time.time(),)).fetchone()[0] >= 2:
                raise VoiceError('Other notes are being translated. Please try again shortly.', 429)
            conn.execute('''INSERT INTO written_note_translations
                (source_kind, source_id, fingerprint, token, lease_until) VALUES (?,?,?,?,?)
                ON CONFLICT(source_kind,source_id) DO UPDATE SET fingerprint=excluded.fingerprint,
                english='', error='', token=excluded.token, lease_until=excluded.lease_until''',
                (kind, note_id, fingerprint, token, time.time() + LEASE_SECONDS))
        try:
            english = self.voice.translate(original, written=True)
            _, current = self.source(kind, note_id)
            if current != fingerprint:
                raise VoiceError('The note changed during translation. Reopen the item and retry.', 409)
            with self.connection() as conn:
                updated = conn.execute('''UPDATE written_note_translations SET english=?, updated_at=?, lease_until=0,
                    error='' WHERE source_kind=? AND source_id=? AND token=?''',
                    (english, time.time(), kind, note_id, token))
                if updated.rowcount != 1:
                    raise VoiceError('A newer translation replaced this request. Reopen the item.', 409)
            return self.get(kind, note_id)
        except Exception as exc:
            message = str(exc) if isinstance(exc, VoiceError) else 'Translation could not finish. Please retry.'
            with self.connection() as conn:
                conn.execute('''UPDATE written_note_translations SET lease_until=0, error=?
                    WHERE source_kind=? AND source_id=? AND token=?''', (message, kind, note_id, token))
            if isinstance(exc, VoiceError):
                raise
            raise VoiceError(message) from None


def register(app, base_dir, voice_service):
    service = WrittenNotes(base_dir, voice_service)

    @app.route('/api/warehouse/written-notes/<kind>/<int:note_id>/translation', methods=['GET', 'POST'])
    def warehouse_written_note_translation(kind, note_id):
        try:
            if request.method == 'POST':
                if request.headers.get('Sec-Fetch-Site') == 'cross-site':
                    raise VoiceError('Please translate notes from the warehouse page.', 403)
                if request.content_length and request.content_length > 150000:
                    raise VoiceError('Translation request is too large.', 413)
                body = request.get_json(silent=True)
                if not isinstance(body, dict):
                    raise VoiceError('Invalid translation request.', 400)
                result = service.translate(kind, note_id, body.get('original'))
            else:
                result = service.get(kind, note_id)
            response = jsonify(success=True, translation=result)
            response.headers['Cache-Control'] = 'no-store'
            return response
        except VoiceError as exc:
            return jsonify(success=False, error=str(exc)), exc.status
        except Exception:
            app.logger.exception('Written translation failed for %s/%s', kind, note_id)
            return jsonify(success=False, error='Unable to load this note. Please retry.'), 500

    return service
