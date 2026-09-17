"""Sweet Shelves Lister "+ NEW": an item that is on no BOL yet.

The side panel opens a draft and the warehouse phone is sent a Telegram link; the two then fill the
same draft from opposite ends. The phone dictates the name, dictates the details and takes the
photos. The panel scans the barcode (or generates and prints one), marks damage on the photos and
submits. Submitting writes what an Item Prep pass would have written - the custom-item name and
thumbnail, a bol_items row, the prep photos and the prep notes - and queues the unit on Items to
List, so it arrives in the panel's own eBay and Amazon lists like anything else.

Everything the submit writes is keyed on the barcode, so scanning that same code later on /barcode
or /multibarcode brings the name, the picture, the description and the damage notes back with it,
whether the code is one of ours (777...) or the item's own.

Shared verbatim by the modular app (sweetshelves/routing.py) and the Desktop debby app.py through
lister_routes.register.
"""

import base64
import datetime
import os
import re
import secrets
import shutil
import sqlite3

from pathlib import Path
from urllib.parse import quote

from flask import jsonify, render_template, request

from lister_routes import ListerError, MUTATION_HEADER

# Where the phone is in the intake flow. The panel shows it, and the phone page opens on it.
STAGES = ('title', 'details', 'photos', 'done')
MAX_PHOTO_BYTES = 25_000_000
PHOTO_TYPES = {'image/jpeg': '.jpg', 'image/png': '.png', 'image/webp': '.webp', 'image/heic': '.jpg'}

_UPC_RE = re.compile(r'[^0-9A-Za-z\-]')


class NewItemError(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def _now():
    return datetime.datetime.now().isoformat(timespec='seconds')


def _text(value, limit=None):
    text = str(value if value is not None else '').strip()
    return text[:limit] if limit else text


def _row(row):
    if row is None:
        return None
    try:
        return {key: row[key] for key in row.keys()}
    except Exception:
        return dict(row)


def intake_url(token, base_url, *, stage=''):
    """The phone page for one draft. The stage it opens on is a hint, not a gate: the page asks the
    server what is still missing, so a link opened late does not send anyone back to step one."""
    url = (base_url or '').rstrip('/') + '/items-to-list/new-item/' + quote(_text(token), safe='')
    if stage:
        url += '?step=' + quote(stage, safe='')
    return url


class NewItems:
    def __init__(self, lister, deps):
        self.lister = lister
        self.db = lister.db
        self.static_folder = Path(lister.static_folder)
        self.add_to_queue = deps.get('_listagent_add_to_queue')
        self.screening_lookup = deps.get('_add_item_screening_lookup')
        self.ensure_registry = deps.get('_ensure_custom_item_registry')
        self.normalize_upc = deps.get('_normalize_upc')
        self.bump_data_version = deps.get('update_data_version')

    # -- storage -----------------------------------------------------------------------------

    @staticmethod
    def init_tables(cur):
        cur.execute('''
            CREATE TABLE IF NOT EXISTS lister_new_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                token TEXT NOT NULL UNIQUE,
                link_token TEXT NOT NULL DEFAULT '',
                link_kind TEXT NOT NULL DEFAULT '',
                upc TEXT NOT NULL DEFAULT '',
                upc_kind TEXT NOT NULL DEFAULT '',
                title TEXT NOT NULL DEFAULT '',
                title_source TEXT NOT NULL DEFAULT '',
                description TEXT NOT NULL DEFAULT '',
                stage TEXT NOT NULL DEFAULT 'title',
                status TEXT NOT NULL DEFAULT 'draft',
                actor TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                submitted_at TEXT
            )
        ''')
        cur.execute('CREATE INDEX IF NOT EXISTS idx_lister_new_items_status ON lister_new_items(status, updated_at)')
        cur.execute('''
            CREATE TABLE IF NOT EXISTS lister_new_item_photos (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                draft_id INTEGER NOT NULL,
                image_path TEXT NOT NULL,
                marked_path TEXT NOT NULL DEFAULT '',
                note TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL
            )
        ''')
        cur.execute('CREATE INDEX IF NOT EXISTS idx_lister_new_item_photos_draft ON lister_new_item_photos(draft_id)')

    def _open(self):
        conn = self.db('listagent.db')
        return conn

    def _fetch(self, cur, *, draft_id=None, token=''):
        if draft_id is not None:
            cur.execute('SELECT * FROM lister_new_items WHERE id = ?', (int(draft_id),))
        else:
            cur.execute('SELECT * FROM lister_new_items WHERE token = ?', (_text(token),))
        draft = _row(cur.fetchone())
        if not draft:
            raise NewItemError('That new-item draft is gone.', 404)
        return draft

    def _photos(self, cur, draft_id):
        cur.execute('SELECT * FROM lister_new_item_photos WHERE draft_id = ? ORDER BY id', (int(draft_id),))
        return [_row(r) for r in cur.fetchall()]

    def _touch(self, cur, draft_id, **fields):
        fields['updated_at'] = _now()
        sets = ', '.join(f'{key} = ?' for key in fields)
        cur.execute(f'UPDATE lister_new_items SET {sets} WHERE id = ?', (*fields.values(), int(draft_id)))

    # -- shape -------------------------------------------------------------------------------

    def _public(self, draft, photos, *, base_url):
        """What both ends see. The phone gets the same shape as the panel: one description of the
        draft means the two can never disagree about which step is still open."""
        return {
            'id': int(draft['id']),
            'token': draft['token'],
            'upc': _text(draft.get('upc')),
            'upcKind': _text(draft.get('upc_kind')),
            'title': _text(draft.get('title')),
            'titleSource': _text(draft.get('title_source')),
            'description': _text(draft.get('description')),
            'stage': _text(draft.get('stage')) or 'title',
            'status': _text(draft.get('status')) or 'draft',
            'actor': _text(draft.get('actor')),
            'createdAt': _text(draft.get('created_at')),
            'updatedAt': _text(draft.get('updated_at')),
            'submittedAt': _text(draft.get('submitted_at')),
            'linkKind': _text(draft.get('link_kind')),
            'linkSent': bool(_text(draft.get('link_token'))),
            'phoneUrl': intake_url(draft['token'], base_url, stage=self._next_stage(draft, photos)),
            'photos': [{
                'id': int(p['id']),
                'url': '/static/' + p['image_path'],
                'markedUrl': ('/static/' + p['marked_path']) if _text(p.get('marked_path')) else '',
                'note': _text(p.get('note')),
                'createdAt': _text(p.get('created_at')),
            } for p in photos],
            'ready': bool(_text(draft.get('upc')) and _text(draft.get('title'))),
            'missing': self._missing(draft),
        }

    @staticmethod
    def _missing(draft):
        gaps = []
        if not _text(draft.get('upc')):
            gaps.append('barcode')
        if not _text(draft.get('title')):
            gaps.append('name')
        return gaps

    @staticmethod
    def _next_stage(draft, photos):
        """Where the phone should land. A name already found by the barcode makes step one pointless,
        so the link that follows it opens the camera instead."""
        if _text(draft.get('status')) != 'draft':
            return 'done'
        if not _text(draft.get('title')):
            return 'title'
        # Somebody who has already reached the camera is not sent back for details they chose to skip.
        if _text(draft.get('stage')) in ('photos', 'done'):
            return 'photos'
        if not _text(draft.get('description')) and _text(draft.get('title_source')) == 'voice':
            return 'details'
        return 'photos'

    # -- drafts ------------------------------------------------------------------------------

    def create(self, *, actor='', base_url='http://localhost/', send=True):
        token = secrets.token_urlsafe(9)
        now = _now()
        with self._open() as conn:
            cur = conn.cursor()
            self.init_tables(cur)
            cur.execute('INSERT INTO lister_new_items (token, actor, created_at, updated_at) VALUES (?, ?, ?, ?)',
                        (token, _text(actor, 80), now, now))
            draft = self._fetch(cur, draft_id=cur.lastrowid)
            conn.commit()
        result = {'draft': self._public(draft, [], base_url=base_url)}
        if send:
            # The phone is meant to buzz the moment the draft opens; a chat the server cannot reach
            # is not a reason to throw the draft away, so the failure is reported beside it.
            try:
                result['link'] = self.send_link(draft['id'], base_url=base_url)
            except (NewItemError, ListerError) as e:
                result['linkError'] = str(e)
            except Exception as e:
                result['linkError'] = self.lister.safe_error(e, 'lister:new-item-link')
        return self.get(draft['id'], base_url=base_url) | {k: v for k, v in result.items() if k != 'draft'}

    def get(self, draft_id, *, base_url='http://localhost/', token=''):
        with self._open() as conn:
            cur = conn.cursor()
            self.init_tables(cur)
            draft = self._fetch(cur, draft_id=draft_id, token=token)
            photos = self._photos(cur, draft['id'])
        return {'draft': self._public(draft, photos, base_url=base_url)}

    def open_drafts(self, *, base_url='http://localhost/', limit=20):
        with self._open() as conn:
            cur = conn.cursor()
            self.init_tables(cur)
            cur.execute("SELECT * FROM lister_new_items WHERE status = 'draft' ORDER BY id DESC LIMIT ?",
                        (max(1, min(int(limit or 20), 100)),))
            drafts = [_row(r) for r in cur.fetchall()]
            out = [self._public(d, self._photos(cur, d['id']), base_url=base_url) for d in drafts]
        return {'drafts': out}

    def cancel(self, draft_id, *, base_url='http://localhost/'):
        with self._open() as conn:
            cur = conn.cursor()
            self.init_tables(cur)
            draft = self._fetch(cur, draft_id=draft_id)
            self._touch(cur, draft['id'], status='cancelled', stage='done')
            conn.commit()
        self._drop_link(draft)
        return self.get(draft_id, base_url=base_url)

    # -- the phone link ----------------------------------------------------------------------

    def _drop_link(self, draft):
        """Take the outstanding Telegram message back down. Best effort: a message left in a chat is
        a nuisance, never a reason to fail the thing the user actually asked for."""
        token = _text(draft.get('link_token'))
        if not token:
            return 0
        try:
            return int((self.lister.photo_link_opened(token) or {}).get('deleted') or 0)
        except Exception:
            return 0

    def send_link(self, draft_id, *, base_url='http://localhost/', replace=True):
        with self._open() as conn:
            cur = conn.cursor()
            self.init_tables(cur)
            draft = self._fetch(cur, draft_id=draft_id)
            photos = self._photos(cur, draft['id'])
        if _text(draft.get('status')) != 'draft':
            raise NewItemError('That draft is already finished.', 409)
        if replace:
            self._drop_link(draft)

        stage = self._next_stage(draft, photos)
        kind = 'photos' if stage == 'photos' else 'intake'
        token = secrets.token_urlsafe(9)
        url = intake_url(draft['token'], base_url, stage=stage)
        # Icon, name, link, nothing else: this is read off a lock screen.
        if kind == 'photos':
            text = '\U0001F4F7 ' + (_text(draft.get('title'), 120) or 'New item') + '\n' + url
        else:
            text = '\U0001F195 New item — name it, then photos\n' + url
        result = self.lister.send_link(key='new:' + draft['token'], token=token, url=url, text=text)

        with self._open() as conn:
            cur = conn.cursor()
            self._touch(cur, draft['id'], link_token=token, link_kind=kind)
            conn.commit()
        return {'url': url, 'kind': kind, 'stage': stage, 'sent': result['sent'], 'errors': result['errors']}

    def link_opened(self, token):
        """The phone opened the page, so the bot takes its own message back down."""
        with self._open() as conn:
            cur = conn.cursor()
            self.init_tables(cur)
            draft = self._fetch(cur, token=token)
        return {'deleted': self._drop_link(draft)}

    # -- barcode -----------------------------------------------------------------------------

    def _normalize(self, value):
        code = _UPC_RE.sub('', _text(value, 40))
        if callable(self.normalize_upc):
            try:
                return _text(self.normalize_upc(code), 40) or code
            except Exception:
                return code
        return code

    def _system_name(self, upc):
        """What our own records already call this code: raw BOL, the catalog, the custom registry."""
        if not callable(self.screening_lookup):
            return {'title': '', 'image_url': ''}
        try:
            found = self.screening_lookup(upc) or {}
        except Exception:
            return {'title': '', 'image_url': ''}
        return {'title': _text(found.get('title'), 200), 'image_url': _text(found.get('image_url'), 500)}

    def set_barcode(self, draft_id, *, barcode, kind='scanned', base_url='http://localhost/'):
        upc = self._normalize(barcode)
        if not upc:
            raise NewItemError('A barcode is required.')
        with self._open() as conn:
            cur = conn.cursor()
            self.init_tables(cur)
            draft = self._fetch(cur, draft_id=draft_id)
            photos = self._photos(cur, draft['id'])
            if _text(draft.get('status')) != 'draft':
                raise NewItemError('That draft is already finished.', 409)
            cur.execute("SELECT id FROM lister_new_items WHERE upc = ? AND status = 'draft' AND id != ?",
                        (upc, int(draft['id'])))
            if cur.fetchone():
                raise NewItemError('Another open draft is already using ' + upc + '.', 409)
            fields = {'upc': upc, 'upc_kind': _text(kind, 20) or 'scanned'}
            found = self._system_name(upc)
            # Our own name for the code beats a dictated one only when nobody has dictated yet.
            took_name = bool(found['title']) and _text(draft.get('title_source')) in ('', 'system')
            if took_name:
                fields['title'] = found['title']
                fields['title_source'] = 'system'
            self._touch(cur, draft['id'], **fields)
            draft = self._fetch(cur, draft_id=draft['id'])
            conn.commit()

        result = self.get(draft['id'], base_url=base_url)
        result['systemTitle'] = found['title']
        # The name is settled, so step one of the link we already sent is dead weight: pull that
        # message and send one that opens straight on the camera.
        if took_name and not photos and _text(draft.get('link_token')):
            try:
                result['link'] = self.send_link(draft['id'], base_url=base_url)
                result['draft'] = self.get(draft['id'], base_url=base_url)['draft']
            except (NewItemError, ListerError) as e:
                result['linkError'] = str(e)
            except Exception as e:
                result['linkError'] = self.lister.safe_error(e, 'lister:new-item-link')
        return result

    def set_fields(self, draft_id, *, title=None, description=None, source='typed', base_url='http://localhost/'):
        fields = {}
        if title is not None:
            fields['title'] = _text(title, 200)
            fields['title_source'] = _text(source, 20) or 'typed'
        if description is not None:
            fields['description'] = _text(description, 4000)
        if not fields:
            raise NewItemError('Nothing to change.')
        with self._open() as conn:
            cur = conn.cursor()
            self.init_tables(cur)
            draft = self._fetch(cur, draft_id=draft_id)
            if _text(draft.get('status')) != 'draft':
                raise NewItemError('That draft is already finished.', 409)
            self._touch(cur, draft['id'], **fields)
            conn.commit()
        return self.get(draft_id, base_url=base_url)

    def draft_id_for_token(self, token):
        with self._open() as conn:
            cur = conn.cursor()
            self.init_tables(cur)
            return int(self._fetch(cur, token=token)['id'])

    def set_stage(self, token, stage, *, base_url='http://localhost/'):
        stage = _text(stage)
        if stage not in STAGES:
            raise NewItemError('stage must be one of ' + ', '.join(STAGES))
        with self._open() as conn:
            cur = conn.cursor()
            self.init_tables(cur)
            draft = self._fetch(cur, token=token)
            self._touch(cur, draft['id'], stage=stage)
            conn.commit()
        return self.get(draft['id'], base_url=base_url)

    # -- photos ------------------------------------------------------------------------------

    def _prep_dir(self):
        folder = self.static_folder / 'items_prep'
        folder.mkdir(parents=True, exist_ok=True)
        return folder

    def add_photos(self, token, files, *, base_url='http://localhost/'):
        with self._open() as conn:
            cur = conn.cursor()
            self.init_tables(cur)
            draft = self._fetch(cur, token=token)
            if _text(draft.get('status')) != 'draft':
                raise NewItemError('That draft is already finished.', 409)
        folder = self._prep_dir()
        saved, now = [], _now()
        for index, upload in enumerate(files or []):
            if not upload or not getattr(upload, 'filename', ''):
                continue
            data = upload.read(MAX_PHOTO_BYTES + 1)
            if not data:
                continue
            if len(data) > MAX_PHOTO_BYTES:
                raise NewItemError('That photo is too large.', 413)
            ext = PHOTO_TYPES.get(_text(getattr(upload, 'mimetype', '')).lower()) \
                or (os.path.splitext(_text(upload.filename))[1].lower() if '.' in _text(upload.filename) else '')
            name = f"new_{draft['token']}_{int(datetime.datetime.now().timestamp() * 1000)}_{index}{ext or '.jpg'}"
            (folder / name).write_bytes(data)
            saved.append('items_prep/' + name)
        if not saved:
            raise NewItemError('No photo was received.')
        with self._open() as conn:
            cur = conn.cursor()
            cur.executemany('INSERT INTO lister_new_item_photos (draft_id, image_path, created_at) VALUES (?, ?, ?)',
                            [(int(draft['id']), path, now) for path in saved])
            self._touch(cur, draft['id'], stage='photos')
            conn.commit()
        return self.get(draft['id'], base_url=base_url) | {'added': len(saved)}

    def mark_photo(self, draft_id, photo_id, *, image=None, note=None, base_url='http://localhost/'):
        """Keep the photo the phone took and save the circled copy beside it. The original is what the
        item really looked like; the marked one is what we want a buyer to look at first."""
        with self._open() as conn:
            cur = conn.cursor()
            self.init_tables(cur)
            draft = self._fetch(cur, draft_id=draft_id)
            cur.execute('SELECT * FROM lister_new_item_photos WHERE id = ? AND draft_id = ?',
                        (int(photo_id), int(draft['id'])))
            photo = _row(cur.fetchone())
        if not photo:
            raise NewItemError('That photo is gone.', 404)

        marked = _text(photo.get('marked_path'))
        if image is not None:
            data = self._decode(image)
            if data:
                folder = self._prep_dir()
                stem = Path(photo['image_path']).stem
                name = f'{stem}_marked.jpg'
                (folder / name).write_bytes(data)
                marked = 'items_prep/' + name
            else:
                # An empty payload means the circles were rubbed out again.
                self._unlink(marked)
                marked = ''
        with self._open() as conn:
            cur = conn.cursor()
            fields = {'marked_path': marked}
            if note is not None:
                fields['note'] = _text(note, 500)
            sets = ', '.join(f'{key} = ?' for key in fields)
            cur.execute(f'UPDATE lister_new_item_photos SET {sets} WHERE id = ?', (*fields.values(), int(photo_id)))
            self._touch(cur, draft['id'])
            conn.commit()
        return self.get(draft['id'], base_url=base_url)

    def delete_photo(self, draft_id, photo_id, *, base_url='http://localhost/'):
        with self._open() as conn:
            cur = conn.cursor()
            self.init_tables(cur)
            draft = self._fetch(cur, draft_id=draft_id)
            cur.execute('SELECT * FROM lister_new_item_photos WHERE id = ? AND draft_id = ?',
                        (int(photo_id), int(draft['id'])))
            photo = _row(cur.fetchone())
            if not photo:
                raise NewItemError('That photo is gone.', 404)
            cur.execute('DELETE FROM lister_new_item_photos WHERE id = ?', (int(photo_id),))
            self._touch(cur, draft['id'])
            conn.commit()
        self._unlink(photo.get('image_path'))
        self._unlink(photo.get('marked_path'))
        return self.get(draft_id, base_url=base_url)

    def _unlink(self, relative):
        path = _text(relative)
        if not path:
            return
        try:
            (self.static_folder / path).unlink(missing_ok=True)
        except Exception:
            pass

    @staticmethod
    def _decode(image):
        """A data URL from the panel's photo editor, or a file upload."""
        if hasattr(image, 'read'):
            return image.read(MAX_PHOTO_BYTES + 1)
        text = _text(image)
        if not text:
            return b''
        if text.startswith('data:'):
            text = text.split(',', 1)[-1]
        try:
            return base64.b64decode(text, validate=False)
        except Exception:
            raise NewItemError('That edited photo could not be read.')

    # -- submit ------------------------------------------------------------------------------

    def submit(self, draft_id, *, actor='', base_url='http://localhost/'):
        with self._open() as conn:
            cur = conn.cursor()
            self.init_tables(cur)
            draft = self._fetch(cur, draft_id=draft_id)
            photos = self._photos(cur, draft['id'])
        if _text(draft.get('status')) != 'draft':
            raise NewItemError('That draft was already submitted.', 409)
        upc = _text(draft.get('upc'))
        title = _text(draft.get('title'), 200)
        if not upc:
            raise NewItemError('Scan a barcode, or generate one, before submitting.')
        if not title:
            raise NewItemError('This item still has no name.')

        thumbnail = self._save_thumbnail(upc, photos)
        self._write_identity(upc, title, thumbnail)
        images = self._write_prep_photos(upc, photos)
        notes = self._write_prep_notes(upc, draft, photos)

        queued = {}
        if callable(self.add_to_queue):
            row, _created = self.add_to_queue(upc, title=title, source='lister-new', added_mode='my')
            queued = {'id': (row or {}).get('id'), 'status': (row or {}).get('status')}

        with self._open() as conn:
            cur = conn.cursor()
            self._touch(cur, draft['id'], status='submitted', stage='done', submitted_at=_now(),
                        actor=_text(actor, 80) or _text(draft.get('actor'), 80))
            conn.commit()
        self._drop_link(draft)
        if callable(self.bump_data_version):
            try:
                self.bump_data_version()
            except Exception:
                pass
        return {'upc': upc, 'title': title, 'photos': images, 'notes': notes, 'queued': queued,
                'thumbnail': thumbnail, **self.get(draft['id'], base_url=base_url)}

    def _save_thumbnail(self, upc, photos):
        """The warehouse row's picture. The marked copy wins: if somebody circled damage, that is
        exactly what the next person to pick this unit up should see first."""
        first = next((p for p in photos if _text(p.get('marked_path')) or _text(p.get('image_path'))), None)
        if not first:
            return ''
        source = self.static_folder / (_text(first.get('marked_path')) or first['image_path'])
        if not source.is_file():
            return ''
        folder = self.static_folder / 'custom_items'
        folder.mkdir(parents=True, exist_ok=True)
        target = folder / f'{upc}.jpg'
        try:
            shutil.copyfile(source, target)
        except Exception:
            return ''
        return f'/static/custom_items/{upc}.jpg'

    def _write_identity(self, upc, title, image_url):
        """Name and picture keyed on the barcode, in the same two places receiving reads them from,
        so scanning this code on /barcode or /multibarcode finds the item we just described."""
        with self.db('bol.db') as conn:
            cur = conn.cursor()
            if callable(self.ensure_registry):
                self.ensure_registry(cur)
            self._ensure_bol_items(cur)
            current = self._system_name(upc)
            # A deliberate name stays authoritative over a later catalog import, exactly as the
            # "No barcode" modal on /barcode does it.
            title_override = int(bool(current['title']) and title != current['title'])
            cur.execute('''
                INSERT INTO custom_item_registry (
                    upc, item_description, image_url, title_override, image_override, reserved_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                ON CONFLICT(upc) DO UPDATE SET
                    item_description = excluded.item_description,
                    image_url = CASE WHEN excluded.image_url != '' THEN excluded.image_url ELSE custom_item_registry.image_url END,
                    title_override = MAX(custom_item_registry.title_override, excluded.title_override),
                    image_override = MAX(custom_item_registry.image_override, excluded.image_override),
                    updated_at = CURRENT_TIMESTAMP
            ''', (upc, title, image_url, title_override, int(bool(image_url))))
            cur.execute('SELECT upc FROM bol_items WHERE upc = ? COLLATE NOCASE', (upc,))
            if cur.fetchone():
                cur.execute('''UPDATE bol_items SET item_description = ?,
                               image_url = CASE WHEN ? != '' THEN ? ELSE image_url END
                               WHERE upc = ? COLLATE NOCASE''', (title, image_url, image_url, upc))
            else:
                cur.execute('''INSERT INTO bol_items (upc, item_description, image_url, lot_number, bol_number, import_date)
                               VALUES (?, ?, ?, NULL, 'CUSTOM', datetime('now'))''', (upc, title, image_url))
            conn.commit()

    def _write_prep_photos(self, upc, photos):
        """Both copies join the unit's prep photos, original first, so the listing panel can offer
        either one and Item Prep shows what the phone actually saw."""
        rows, now = [], datetime.datetime.now(datetime.UTC).isoformat()
        for photo in photos:
            for path in (photo.get('image_path'), photo.get('marked_path')):
                if _text(path):
                    rows.append((upc, '', _text(path), now, 0))
        if not rows:
            return 0
        with self.db('bol.db') as conn:
            cur = conn.cursor()
            self._ensure_prep_tables(cur)
            cur.executemany('INSERT INTO items_prep_images (upc, row_status, image_path, created_at, rotation) '
                            'VALUES (?, ?, ?, ?, ?)', rows)
            conn.commit()
        return len(rows)

    def _write_prep_notes(self, upc, draft, photos):
        """The dictated details and every damage note, as prep notes. That is the same well the
        listing panel reads its condition and condition description out of, so a circled scratch
        ends up in the eBay listing without anybody retyping it."""
        notes = []
        description = _text(draft.get('description'), 4000)
        if description:
            notes.append(description)
        for index, photo in enumerate(photos, start=1):
            note = _text(photo.get('note'), 500)
            if note:
                notes.append(f'Photo {index}: {note}')
        if not notes:
            return 0
        now = datetime.datetime.now(datetime.UTC).isoformat()
        with self.db('bol.db') as conn:
            cur = conn.cursor()
            self._ensure_prep_tables(cur)
            cur.executemany('INSERT INTO items_prep_notes (upc, row_status, note, created_at) VALUES (?, ?, ?, ?)',
                            [(upc, '', note, now) for note in notes])
            conn.commit()
        return len(notes)

    @staticmethod
    def _ensure_bol_items(cur):
        """BOL imports own this table; the same shape DBmanager creates, so a warehouse that has not
        imported a BOL yet can still take its first hand-made item."""
        cur.execute('''CREATE TABLE IF NOT EXISTS bol_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT, upc TEXT UNIQUE, item_description TEXT, client_cost REAL,
            total_client_cost REAL, image_url TEXT, lot_number TEXT, bol_number TEXT, import_date TEXT)''')

    @staticmethod
    def _ensure_prep_tables(cur):
        """Item Prep owns these tables; create them only so a fresh database does not fall over here."""
        cur.execute('''CREATE TABLE IF NOT EXISTS items_prep_images (
            id INTEGER PRIMARY KEY AUTOINCREMENT, upc TEXT NOT NULL, row_status TEXT, image_path TEXT NOT NULL,
            created_at TEXT, rotation INTEGER DEFAULT 0, deleted_at TEXT)''')
        cur.execute('''CREATE TABLE IF NOT EXISTS items_prep_notes (
            id INTEGER PRIMARY KEY AUTOINCREMENT, upc TEXT NOT NULL, row_status TEXT, note TEXT NOT NULL,
            created_at TEXT)''')


def register(app, lister, deps):
    """Wire the "+ NEW" routes beside the rest of the Lister. deps is the same mapping
    lister_routes.register was handed; everything this module uses out of it is optional, so a host
    without the Items-to-List queue still starts, it just cannot submit."""
    new_items = NewItems(lister, deps)

    def base_url():
        try:
            root = (request.url_root or '').strip() or 'http://localhost/'
            proto = _text(request.headers.get('X-Forwarded-Proto')).split(',')[0].strip().lower()
            if proto == 'https' and root.startswith('http://'):
                root = 'https://' + root[len('http://'):]
            return root
        except Exception:
            return 'http://localhost/'

    def actor():
        data = request.get_json(silent=True) if request.method in ('POST', 'DELETE') else None
        value = _text((data or {}).get('actor'), 80)
        return value or _text(request.headers.get('Cf-Access-Authenticated-User-Email'), 120) or 'lister'

    def guard():
        """The panel's calls carry the extension header. The phone's do not, and do not need to:
        they are same-origin and prove which draft they are for by holding its token."""
        if request.headers.get('Sec-Fetch-Site') == 'cross-site' and not request.headers.get(MUTATION_HEADER):
            raise NewItemError('Use the Sweet Shelves Lister extension or a Sweet Shelves page for this action.', 403)

    def phone_guard():
        if request.headers.get('Sec-Fetch-Site') == 'cross-site':
            raise NewItemError('Open the link from the message we sent you.', 403)

    def failure(e, context):
        if isinstance(e, (NewItemError, ListerError)):
            return jsonify({'success': False, 'error': str(e)}), e.status
        return jsonify({'success': False, 'error': lister.safe_error(e, context)}), 500

    # -- the panel ---------------------------------------------------------------------------

    def api_new_create():
        try:
            guard()
            data = request.get_json(silent=True) or {}
            result = new_items.create(actor=actor(), base_url=base_url(), send=data.get('send', True) is not False)
            return jsonify({'success': True, **result}), 201
        except Exception as e:
            return failure(e, 'lister:new-item-create')

    def api_new_list():
        try:
            return jsonify({'success': True, **new_items.open_drafts(base_url=base_url())})
        except Exception as e:
            return failure(e, 'lister:new-item-list')

    def api_new_get(draft_id):
        try:
            return jsonify({'success': True, **new_items.get(draft_id, base_url=base_url())})
        except Exception as e:
            return failure(e, 'lister:new-item')

    def api_new_link(draft_id):
        try:
            guard()
            return jsonify({'success': True, **new_items.send_link(draft_id, base_url=base_url()),
                            **new_items.get(draft_id, base_url=base_url())})
        except Exception as e:
            return failure(e, 'lister:new-item-link')

    def api_new_barcode(draft_id):
        try:
            guard()
            data = request.get_json(silent=True) or {}
            return jsonify({'success': True, **new_items.set_barcode(
                draft_id, barcode=data.get('barcode'), kind=_text(data.get('kind'), 20) or 'scanned',
                base_url=base_url())})
        except Exception as e:
            return failure(e, 'lister:new-item-barcode')

    def api_new_fields(draft_id):
        try:
            guard()
            data = request.get_json(silent=True) or {}
            return jsonify({'success': True, **new_items.set_fields(
                draft_id, title=data.get('title'), description=data.get('description'),
                source=_text(data.get('source'), 20) or 'typed', base_url=base_url())})
        except Exception as e:
            return failure(e, 'lister:new-item-fields')

    def api_new_photo(draft_id, photo_id):
        try:
            guard()
            if request.method == 'DELETE':
                return jsonify({'success': True, **new_items.delete_photo(draft_id, photo_id, base_url=base_url())})
            data = request.get_json(silent=True) or {}
            return jsonify({'success': True, **new_items.mark_photo(
                draft_id, photo_id, image=data.get('image'), note=data.get('note'), base_url=base_url())})
        except Exception as e:
            return failure(e, 'lister:new-item-photo')

    def api_new_submit(draft_id):
        try:
            guard()
            return jsonify({'success': True, **new_items.submit(draft_id, actor=actor(), base_url=base_url())}), 201
        except Exception as e:
            return failure(e, 'lister:new-item-submit')

    def api_new_cancel(draft_id):
        try:
            guard()
            return jsonify({'success': True, **new_items.cancel(draft_id, base_url=base_url())})
        except Exception as e:
            return failure(e, 'lister:new-item-cancel')

    # -- the phone ---------------------------------------------------------------------------

    def new_item_page(token):
        return render_template('lister_new_item_mobile.html', token=_text(token, 64),
                               step=_text(request.args.get('step'), 20))

    def api_new_phone_get(token):
        try:
            return jsonify({'success': True, **new_items.get(None, token=token, base_url=base_url())})
        except Exception as e:
            return failure(e, 'lister:new-item-phone')

    def api_new_phone_step(token):
        try:
            phone_guard()
            data = request.get_json(silent=True) or {}
            if 'stage' in data and 'title' not in data and 'description' not in data:
                return jsonify({'success': True, **new_items.set_stage(token, data.get('stage'), base_url=base_url())})
            result = new_items.set_fields(
                new_items.draft_id_for_token(token), title=data.get('title'),
                description=data.get('description'), source='voice', base_url=base_url())
            if _text(data.get('stage')) in STAGES:
                result = new_items.set_stage(token, data.get('stage'), base_url=base_url())
            return jsonify({'success': True, **result})
        except Exception as e:
            return failure(e, 'lister:new-item-step')

    def api_new_phone_photos(token):
        try:
            phone_guard()
            files = request.files.getlist('photos[]') or request.files.getlist('photos')
            return jsonify({'success': True, **new_items.add_photos(token, files, base_url=base_url())}), 201
        except Exception as e:
            return failure(e, 'lister:new-item-phone-photos')

    def api_new_phone_opened(token):
        try:
            return jsonify({'success': True, **new_items.link_opened(token)})
        except Exception as e:
            return failure(e, 'lister:new-item-opened')

    app.add_url_rule('/api/lister/new', 'api_lister_new_create', api_new_create, methods=['POST'])
    app.add_url_rule('/api/lister/new', 'api_lister_new_list', api_new_list)
    app.add_url_rule('/api/lister/new/<int:draft_id>', 'api_lister_new_get', api_new_get)
    app.add_url_rule('/api/lister/new/<int:draft_id>/link', 'api_lister_new_link', api_new_link, methods=['POST'])
    app.add_url_rule('/api/lister/new/<int:draft_id>/barcode', 'api_lister_new_barcode', api_new_barcode, methods=['POST'])
    app.add_url_rule('/api/lister/new/<int:draft_id>/fields', 'api_lister_new_fields', api_new_fields, methods=['POST'])
    app.add_url_rule('/api/lister/new/<int:draft_id>/photos/<int:photo_id>', 'api_lister_new_photo', api_new_photo,
                     methods=['POST', 'DELETE'])
    app.add_url_rule('/api/lister/new/<int:draft_id>/submit', 'api_lister_new_submit', api_new_submit, methods=['POST'])
    app.add_url_rule('/api/lister/new/<int:draft_id>/cancel', 'api_lister_new_cancel', api_new_cancel, methods=['POST'])
    app.add_url_rule('/items-to-list/new-item/<token>', 'lister_new_item_page', new_item_page)
    app.add_url_rule('/api/lister/new/t/<token>', 'api_lister_new_phone_get', api_new_phone_get)
    app.add_url_rule('/api/lister/new/t/<token>/step', 'api_lister_new_phone_step', api_new_phone_step, methods=['POST'])
    app.add_url_rule('/api/lister/new/t/<token>/photos', 'api_lister_new_phone_photos', api_new_phone_photos, methods=['POST'])
    app.add_url_rule('/api/lister/new/t/<token>/opened', 'api_lister_new_phone_opened', api_new_phone_opened, methods=['POST'])
    return new_items
