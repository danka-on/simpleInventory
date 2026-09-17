"""Prep + : fast bulk intake for damaged, barcode-less and store-found items.

Item Prep works down a manifest: a lot exists, a barcode resolves, and the screen
asks what is wrong with a known item. Prep + is the opposite direction. The item is
in your hand, nothing upstream describes it, and the only cheap inputs are a camera,
a voice and a barcode gun. So this page captures those, one item at a time, and each
finished item is written straight through as a prepped unit that Items-to-List picks
up like any other.

One endpoint does the writing: POST /api/prep-plus/item (multipart). It creates the
bol_items carrier row, moves a unit into the good or bad bucket, seeds
items_prep_status, and files the photos, the condition recording and its transcript
against the same barcode the rest of Item Prep uses.

Damaged units always take a suffixed barcode (base-1, base-2, ...) because
Items-to-List only honours BAD/RETURN on a suffixed row; a good unit keeps the base
barcode when the barcode is new, and takes the next free suffix when the barcode
already carries stock, so a second unit never overwrites the first.
"""
import datetime as dt
import io
import json
import os
from pathlib import Path
import re
import time

from flask import jsonify, render_template, request
from PIL import Image

# Damaged items are filed with the same defect words Items-to-List filters on.
DEFECTS = ('Missing pieces', 'Wrong item', 'Broken', 'Box damage', 'Return', 'Replacement', 'Other')

# Where a scanned barcode's name may come from, best first, each tried across every
# spelling of the barcode. Our own listings outrank the Macy manifest on purpose: if we
# have already listed this item, that title is the one a buyer saw and the one the rest
# of the app shows for it, while the manifest line is the vendor's wording. Only when
# nothing here knows the barcode does the page ask the outside catalogs, and those are
# never adopted on their own — the person picks.
IDENTITY_SOURCES = (
    ('ebay_store', 'eBay store', 'ebayStore.db', (
        "SELECT COALESCE(Title, '') AS title, COALESCE(Image, '') AS image FROM INVENTORY"
        " WHERE UPC IS NOT NULL AND LOWER(TRIM(UPC)) = ? ORDER BY ID DESC LIMIT 1",
    )),
    ('amazon_store', 'Amazon store', 'amazonStore.db', (
        # ITEMS is the SP-API table; INVENTORY is the older one some installs still carry.
        "SELECT COALESCE(TITLE, '') AS title, COALESCE(IMAGE, '') AS image FROM ITEMS"
        " WHERE UPC IS NOT NULL AND LOWER(TRIM(UPC)) = ? ORDER BY LAST_UPDATED DESC LIMIT 1",
        "SELECT COALESCE(Title, '') AS title, COALESCE(Image, '') AS image FROM INVENTORY"
        " WHERE UPC IS NOT NULL AND LOWER(TRIM(UPC)) = ? ORDER BY ID DESC LIMIT 1",
    )),
    ('macy_bol', 'Macy BOL', 'rawbol.db', (
        "SELECT COALESCE(item_description, '') AS title, COALESCE(image_url, '') AS image"
        " FROM raw_bol_items WHERE upc IS NOT NULL AND LOWER(TRIM(upc)) = ? ORDER BY rowid DESC LIMIT 1",
    )),
)
MAX_PHOTO_BYTES = 10 * 1024 * 1024
MAX_PHOTOS = 12
MAX_AUDIO_BYTES = 25 * 1024 * 1024
MAX_TITLE = 200
MAX_NOTE = 2000
PHOTO_TYPES = {'JPEG': 'jpg', 'PNG': 'png', 'WEBP': 'webp'}
AUDIO_TYPES = {'audio/webm': '.webm', 'audio/ogg': '.ogg', 'audio/mp4': '.mp4',
               'audio/mpeg': '.mp3', 'audio/wav': '.wav', 'audio/x-m4a': '.m4a', 'audio/aac': '.m4a'}


class PrepPlusError(ValueError):
    """A problem the person at the bench can fix and retry."""


def checked_photo(upload):
    """Bytes and extension of one uploaded photo, or an explainable refusal."""
    data = upload.read(MAX_PHOTO_BYTES + 1)
    if not data:
        raise PrepPlusError('One of the photos was empty. Retake it.')
    if len(data) > MAX_PHOTO_BYTES:
        raise PrepPlusError('Each photo must be smaller than 10 MB.')
    try:
        with Image.open(io.BytesIO(data)) as im:
            if im.format not in PHOTO_TYPES or im.width * im.height > 40_000_000:
                raise PrepPlusError('Use a JPEG, PNG, or WebP photo under 40 megapixels.')
            extension = PHOTO_TYPES[im.format]
            im.verify()
    except PrepPlusError:
        raise
    except Exception as exc:
        raise PrepPlusError('A photo could not be read. Retake it as a JPEG, PNG, or WebP.') from exc
    return data, extension


def checked_audio(upload):
    """Bytes and extension of the condition recording."""
    data = upload.read(MAX_AUDIO_BYTES + 1)
    if not data:
        return None, ''
    if len(data) > MAX_AUDIO_BYTES:
        raise PrepPlusError('The condition recording must be smaller than 25 MB.')
    mime = str(getattr(upload, 'mimetype', '') or '').split(';')[0].strip().lower()
    extension = AUDIO_TYPES.get(mime) or os.path.splitext(str(upload.filename or ''))[1].lower()
    if extension not in set(AUDIO_TYPES.values()):
        extension = '.webm'
    return data, extension


def _table_columns(cur, table):
    return {row[1] for row in cur.execute(f'PRAGMA table_info({table})')}


def _insert_bol_item(cur, values, columns):
    """Insert a bol_items row using only the columns this database actually has.

    bol_items grew its quantity buckets over time and the shape differs between the
    Pi, the desktop copy and a test fixture, so the writer adapts instead of assuming.
    """
    usable = {key: value for key, value in values.items() if key in columns}
    names = ', '.join(usable)
    marks = ', '.join('?' for _ in usable)
    cur.execute(f'INSERT INTO bol_items ({names}) VALUES ({marks})', tuple(usable.values()))
    return cur.lastrowid


class PrepPlus:
    def __init__(self, app, db_connection, normalize_upc, normalize_lot, reject_title,
                 asset_scope, ensure_registry, ensure_prep, clear_cache, upc_variants,
                 preplog=None, update_data_version=None):
        self.app, self.db = app, db_connection
        self.normalize_upc, self.normalize_lot = normalize_upc, normalize_lot
        self.reject_title, self.asset_scope = reject_title, asset_scope
        self.ensure_registry, self.ensure_prep = ensure_registry, ensure_prep
        self.clear_cache, self.upc_variants = clear_cache, upc_variants
        self.preplog, self.update_data_version = preplog, update_data_version

    # ---- who is this? ----------------------------------------------------

    def identify(self):
        """The name we already hold for a scanned barcode, from the closest source out."""
        upc = self.normalize_upc(request.args.get('upc'))
        if not upc:
            raise PrepPlusError('Scan a barcode first.')
        variants = [value for value in (self.upc_variants(upc) or []) if value] or [upc.lower()]
        tried = []
        for source, label, database, queries in IDENTITY_SOURCES:
            answered = False
            for query in queries:
                try:
                    with self.db(database) as conn:
                        cur = conn.cursor()
                        for variant in variants:
                            row = cur.execute(query, (variant,)).fetchone()
                            if not row:
                                continue
                            title = ' '.join(str(row['title'] or '').split())[:MAX_TITLE]
                            if not title or self.reject_title(title):
                                continue
                            return jsonify(success=True, found=True, source=source, label=label,
                                           title=title, image_url=str(row['image'] or '').strip(),
                                           tried=tried)
                    answered = True
                except Exception:
                    # A table or database this install does not have is not an answer. Only
                    # when none of a source's tables could be read has the source failed:
                    # an install without the legacy table still got a real answer from the
                    # current one, and saying "unavailable" there would be a lie.
                    continue
            tried.append({'source': source, 'label': label,
                          'result': 'no match' if answered else 'unavailable'})
        return jsonify(success=True, found=False, tried=tried)

    # ---- storage ---------------------------------------------------------

    def schema(self, cur):
        cur.execute('''CREATE TABLE IF NOT EXISTS prep_plus_intake (
            request_id TEXT PRIMARY KEY,
            upc TEXT NOT NULL,
            base_upc TEXT NOT NULL,
            status TEXT NOT NULL,
            created_at TEXT NOT NULL,
            evidence_json TEXT
        )''')

    def media_dir(self):
        target = Path(self.app.static_folder) / 'items_prep'
        target.mkdir(parents=True, exist_ok=True)
        return target

    def next_suffix(self, cur, base_upc):
        """First `base-N` that no bol_items row and no prep artifact has ever used.

        Reusing a freed suffix would silently adopt the old unit's photos and notes.
        """
        probes = [('bol_items', 'upc'), ('items_prep_status', 'upc'), ('items_prep_images', 'upc'),
                  ('items_prep_notes', 'upc'), ('items_prep_media', 'upc')]
        for number in range(1, 501):
            candidate = f'{base_upc}-{number}'
            taken = False
            for table, column in probes:
                try:
                    if cur.execute(f'SELECT 1 FROM {table} WHERE {column} = ? COLLATE NOCASE LIMIT 1',
                                   (candidate,)).fetchone():
                        taken = True
                        break
                except Exception:
                    continue  # a table this install has not created yet cannot hold the suffix
            if not taken:
                return candidate
        raise PrepPlusError(f'No free entry number left for {base_upc}. Use a different barcode.')

    def base_row(self, cur, base_upc, lot):
        return cur.execute('''SELECT id, item_description, image_url, lot_number,
                                     COALESCE(good_qty, 0), COALESCE(bad_qty, 0),
                                     unchecked_qty, COALESCE(original_qty, 0)
                              FROM bol_items
                              WHERE upc = ? COLLATE NOCASE
                              ORDER BY import_date DESC, id DESC LIMIT 1''', (base_upc,)).fetchone()

    # ---- the one write ---------------------------------------------------

    def save(self):
        form = request.form
        token = str(form.get('request_id') or '')
        if not re.fullmatch(r'[a-f0-9-]{32,36}', token):
            raise PrepPlusError('Reload Prep + and add this item again.')

        raw_barcode = str(form.get('barcode') or '').strip()
        base_upc = self.normalize_upc(raw_barcode)
        if not base_upc or not re.fullmatch(r'[0-9A-Za-z]{4,32}', base_upc):
            raise PrepPlusError('Scan, type or generate a barcode for this item.')

        title = ' '.join(str(form.get('title') or '').split())[:MAX_TITLE + 1]
        if not title:
            raise PrepPlusError('Say or type a name for this item.')
        rejection = self.reject_title(title)
        if rejection:
            raise PrepPlusError(rejection)

        condition = str(form.get('condition') or 'good').strip().lower()
        if condition not in ('good', 'damaged'):
            raise PrepPlusError('Mark the item Good or Damaged.')
        status = 'bad' if condition == 'damaged' else 'good'

        defects = [value for value in (form.getlist('defect[]') or form.getlist('defect')) if value in DEFECTS]
        if status == 'bad' and not defects:
            defects = ['Other']
        reason = ' | '.join(dict.fromkeys(defects)) if status == 'bad' else ''

        note = str(form.get('note') or '').replace('\r\n', '\n').strip()[:MAX_NOTE]
        lot = self.normalize_lot(form.get('lot_number'))

        photos = [checked_photo(upload) for upload
                  in (request.files.getlist('photos[]') or request.files.getlist('photos'))[:MAX_PHOTOS]
                  if upload and upload.filename]
        audio, audio_extension = checked_audio(request.files['audio']) if 'audio' in request.files else (None, '')

        self.ensure_prep()
        now = dt.datetime.now(dt.timezone.utc).isoformat()
        written = []
        try:
            with self.db('bol.db') as conn:
                cur = conn.cursor()
                self.schema(cur)
                self.ensure_registry(cur)
                conn.commit()
                cur.execute('BEGIN IMMEDIATE')

                # A retried upload (flaky warehouse wifi) must not create a second unit.
                done = cur.execute('SELECT upc, base_upc, status FROM prep_plus_intake WHERE request_id = ?',
                                   (token,)).fetchone()
                if done:
                    return jsonify(success=True, upc=done[0], base_upc=done[1], status=done[2], repeated=True)

                columns = _table_columns(cur, 'bol_items')
                existing = self.base_row(cur, base_upc, lot)
                created_base = existing is None
                if created_base:
                    _insert_bol_item(cur, {
                        'upc': base_upc, 'item_description': title, 'image_url': '',
                        'lot_number': lot or None, 'bol_number': 'CUSTOM', 'import_date': now,
                        'original_qty': 1, 'unchecked_qty': 1, 'good_qty': 0, 'bad_qty': 0, 'quantity': 1,
                    }, columns)
                    existing = self.base_row(cur, base_upc, lot)

                base_id, base_title, base_image, base_lot = existing[0], existing[1] or title, existing[2] or '', existing[3]
                base_good, base_bad, base_unchecked, base_original = existing[4], existing[5], existing[6], existing[7]
                if base_unchecked is None:
                    base_unchecked = max(0, base_original - base_good - base_bad)
                if not lot:
                    lot = self.normalize_lot(base_lot)

                held = cur.execute('''SELECT 1 FROM items_prep_status
                                      WHERE upc = ? COLLATE NOCASE
                                        AND COALESCE(lot_number, '') = ? COLLATE NOCASE LIMIT 1''',
                                   (base_upc, lot)).fetchone()
                # The base barcode is the canonical row: it can hold one good unit and
                # only while nothing has claimed it. Everything else becomes its own entry.
                on_base = status == 'good' and (created_base or (not held and base_unchecked >= 1))
                target = base_upc if on_base else self.next_suffix(cur, base_upc)

                # The unit leaves the unchecked bucket whichever row it lands on, so the
                # base count still reads as one prepped item rather than two.
                moved = 1 if base_unchecked >= 1 else 0
                cur.execute('''UPDATE bol_items SET good_qty = ?, bad_qty = ?, unchecked_qty = ?
                               WHERE id = ?''',
                            (base_good + (1 if status == 'good' else 0),
                             base_bad + (0 if status == 'good' else 1),
                             max(0, base_unchecked - moved), base_id))

                if not on_base:
                    _insert_bol_item(cur, {
                        'upc': target, 'item_description': base_title, 'image_url': base_image,
                        'lot_number': lot or None, 'bol_number': 'CUSTOM', 'import_date': now,
                        'temporary': 1, 'original_qty': 1, 'unchecked_qty': 0,
                        'good_qty': 1 if status == 'good' else 0,
                        'bad_qty': 0 if status == 'good' else 1, 'quantity': 1,
                    }, columns)

                cur.execute('''INSERT INTO items_prep_status (upc, lot_number, status, reason, note, quantity, updated_at)
                               VALUES (?,?,?,?,?,1,?)
                               ON CONFLICT(upc, lot_number) DO UPDATE SET
                                   status = excluded.status, reason = excluded.reason,
                                   note = excluded.note, updated_at = excluded.updated_at''',
                            (target, lot, status, reason, note, now))

                scope_upc, scope_status = self.asset_scope(target, status)
                image_url = base_image
                for index, (data, extension) in enumerate(photos):
                    name = f'{target}_{int(time.time() * 1000)}_{index}.{extension}'
                    path = self.media_dir() / name
                    with path.open('xb') as out:
                        out.write(data)
                    written.append(path)
                    cur.execute('''INSERT INTO items_prep_images (upc, row_status, image_path, created_at, rotation)
                                   VALUES (?,?,?,?,0)''', (scope_upc, scope_status, f'items_prep/{name}', now))
                    if not image_url:
                        image_url = f'/static/items_prep/{name}'

                if audio:
                    name = f'{target}_audio_{int(time.time() * 1000)}{audio_extension}'
                    path = self.media_dir() / name
                    with path.open('xb') as out:
                        out.write(audio)
                    written.append(path)
                    cur.execute('''INSERT INTO items_prep_media (upc, row_status, media_type, file_path, mime_type, created_at)
                                   VALUES (?,?,'audio',?,?,?)''',
                                (scope_upc, scope_status, f'items_prep/{name}',
                                 AUDIO_TYPES.get(audio_extension, 'audio/webm'), now))

                if note:
                    cur.execute('''INSERT INTO items_prep_notes (upc, row_status, note, created_at)
                                   VALUES (?,?,?,?)''', (scope_upc, scope_status, note, now))

                # The name and picture are what the rest of the app shows for a barcode
                # that no manifest describes, so they are kept outside bol_items too.
                cur.execute('''UPDATE bol_items SET item_description = ?, image_url = ?
                               WHERE upc = ? COLLATE NOCASE''', (title, image_url, target))
                if created_base and target != base_upc:
                    cur.execute('UPDATE bol_items SET item_description = ? WHERE id = ?', (title, base_id))
                cur.execute('''INSERT INTO custom_item_registry (upc, item_description, image_url, reserved_at, updated_at)
                               VALUES (?,?,?,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)
                               ON CONFLICT(upc) DO UPDATE SET
                                   item_description = excluded.item_description,
                                   image_url = CASE WHEN TRIM(COALESCE(excluded.image_url, '')) = ''
                                                    THEN custom_item_registry.image_url ELSE excluded.image_url END,
                                   updated_at = CURRENT_TIMESTAMP''', (base_upc, title, image_url))

                cur.execute('INSERT INTO prep_plus_intake VALUES (?,?,?,?,?,?)',
                            (token, target, base_upc, status, now,
                             json.dumps({'title': title, 'reason': reason, 'photos': len(photos),
                                         'audio': bool(audio), 'lot_number': lot, 'source': 'prep-plus'})))
        except Exception:
            for path in written:
                path.unlink(missing_ok=True)
            raise

        if self.preplog:
            try:
                self.preplog(upc=target, base_upc=base_upc, status=status, quantity=1,
                             note=note or None, reason=reason or None, source='prep-plus',
                             meta={'action': 'prep_plus_intake', 'lot_number': lot,
                                   'photos': len(photos), 'audio': bool(audio),
                                   'created_barcode': created_base})
            except Exception:
                self.app.logger.warning('Prep + log entry failed for %s', target)
        for hook in (self.clear_cache, self.update_data_version):
            try:
                if hook:
                    hook()
            except Exception:
                pass
        return jsonify(success=True, upc=target, base_upc=base_upc, status=status,
                       lot_number=lot, photos=len(photos),
                       items_to_list_url='/items-to-list?q=' + base_upc)


def register(app, **dependencies):
    service = PrepPlus(app, **dependencies)
    app.extensions['prep_plus'] = service

    def page():
        return render_template('prep_plus.html', defects=DEFECTS)

    def guarded(method, failure):
        def view():
            try:
                return method()
            except PrepPlusError as exc:
                return jsonify(success=False, error=str(exc)), 400
            except Exception:
                app.logger.exception('Prep + request failed')
                return jsonify(success=False, error=failure), 500
        return view

    app.add_url_rule('/prep-plus', 'prep_plus_page', page)
    app.add_url_rule('/api/prep-plus/item', 'prep_plus_item',
                     guarded(service.save, 'Could not save this item. It is still in the list — retry it.'),
                     methods=['POST'])
    app.add_url_rule('/api/prep-plus/identify', 'prep_plus_identify',
                     guarded(service.identify, 'Could not check this barcode. Type the name instead.'),
                     methods=['GET'])
    return service
