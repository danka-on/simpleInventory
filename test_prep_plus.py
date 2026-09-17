import io
import sqlite3
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path

from flask import Flask
from PIL import Image

from prep_plus import DEFECTS, register

BARCODE = '777000000123'


def photo():
    stream = io.BytesIO()
    Image.new('RGB', (24, 24), 'red').save(stream, 'JPEG')
    return stream.getvalue()


def asset_scope(upc, row_status=''):
    """The app's Items-to-List scoping rule, copied so the test pins the contract."""
    status = row_status if row_status in ('good', 'bad', 'return', 'unchecked') else ''
    return (upc, '') if '-' in upc else (upc, status)


class PrepPlusTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.app = Flask(__name__, static_folder=str(self.root / 'static'),
                         template_folder=str(Path(__file__).parent / 'templates'))
        self.logged = []

        @contextmanager
        def db(name):
            conn = sqlite3.connect(self.root / name)
            conn.row_factory = sqlite3.Row
            try:
                yield conn
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            finally:
                conn.close()
        self.db = db

        with db('bol.db') as conn:
            conn.executescript('''
                CREATE TABLE bol_items (id INTEGER PRIMARY KEY AUTOINCREMENT, upc TEXT, item_description TEXT,
                    image_url TEXT, lot_number TEXT, bol_number TEXT, import_date TEXT, temporary INTEGER DEFAULT 0,
                    quantity INTEGER, original_qty INTEGER, unchecked_qty INTEGER, good_qty INTEGER, bad_qty INTEGER);
                CREATE TABLE items_prep_status (id INTEGER PRIMARY KEY AUTOINCREMENT, upc TEXT NOT NULL,
                    lot_number TEXT NOT NULL DEFAULT '', status TEXT, reason TEXT, note TEXT, updated_at TEXT,
                    quantity INTEGER DEFAULT 1, UNIQUE(upc, lot_number));
                CREATE TABLE items_prep_images (id INTEGER PRIMARY KEY AUTOINCREMENT, upc TEXT, row_status TEXT,
                    image_path TEXT, created_at TEXT, rotation INTEGER DEFAULT 0);
                CREATE TABLE items_prep_notes (id INTEGER PRIMARY KEY AUTOINCREMENT, upc TEXT, row_status TEXT,
                    note TEXT, created_at TEXT);
                CREATE TABLE items_prep_media (id INTEGER PRIMARY KEY AUTOINCREMENT, upc TEXT, row_status TEXT,
                    media_type TEXT, file_path TEXT, mime_type TEXT, created_at TEXT);
            ''')

        with db('ebayStore.db') as conn:
            conn.execute('CREATE TABLE INVENTORY (ID INTEGER PRIMARY KEY, UPC TEXT, Title TEXT, Image TEXT)')
        with db('amazonStore.db') as conn:
            conn.execute('CREATE TABLE ITEMS (UPC TEXT, TITLE TEXT, IMAGE TEXT, LAST_UPDATED TEXT)')
        with db('rawbol.db') as conn:
            conn.execute('CREATE TABLE raw_bol_items (upc TEXT, item_description TEXT, image_url TEXT)')

        def registry(cur):
            cur.execute('''CREATE TABLE IF NOT EXISTS custom_item_registry (upc TEXT PRIMARY KEY,
                item_description TEXT, image_url TEXT, reserved_at TEXT, updated_at TEXT)''')

        self.service = register(
            self.app, db_connection=db,
            normalize_upc=lambda value: str(value or '').strip().lstrip('0') or str(value or '').strip(),
            normalize_lot=lambda value: str(value or '').strip(),
            reject_title=lambda title: 'Use a short descriptive name, not a barcode or placeholder.'
                                       if title.lower() in ('unknown', 'item') or title.isdigit() else '',
            asset_scope=asset_scope, ensure_registry=registry, ensure_prep=lambda: None,
            clear_cache=lambda: None, upc_variants=lambda value: [str(value).lower()],
            preplog=lambda **kwargs: self.logged.append(kwargs),
            update_data_version=lambda: None)
        self.client = self.app.test_client()
        self.tokens = 0

    # ---- helpers ---------------------------------------------------------

    def post(self, **overrides):
        self.tokens += 1
        data = dict(request_id=f'{self.tokens:032x}', barcode=BARCODE, title='Red KitchenAid mixer',
                    condition='good')
        data.update(overrides)
        photos = data.pop('photos', 0)
        for index in range(photos):
            data.setdefault('photos[]', []).append((io.BytesIO(photo()), f'photo_{index}.jpg'))
        if data.pop('audio', False):
            data['audio'] = (io.BytesIO(b'fake-opus-bytes'), 'condition.webm', 'audio/webm')
        return self.client.post('/api/prep-plus/item', data=data, content_type='multipart/form-data')

    def rows(self, sql, *params):
        with self.db('bol.db') as conn:
            return [tuple(row) for row in conn.execute(sql, params)]

    def stock(self, *, ebay=None, amazon=None, macy=None):
        if ebay:
            with self.db('ebayStore.db') as conn:
                conn.execute('INSERT INTO INVENTORY (UPC, Title, Image) VALUES (?,?,?)', (BARCODE, ebay, 'e.jpg'))
        if amazon:
            with self.db('amazonStore.db') as conn:
                conn.execute('INSERT INTO ITEMS VALUES (?,?,?,?)', (BARCODE, amazon, 'a.jpg', '2026-01-01'))
        if macy:
            with self.db('rawbol.db') as conn:
                conn.execute('INSERT INTO raw_bol_items VALUES (?,?,?)', (BARCODE, macy, 'm.jpg'))

    def identify(self, barcode=BARCODE):
        return self.client.get('/api/prep-plus/identify?upc=' + barcode).get_json()

    # ---- the happy paths -------------------------------------------------

    def test_new_barcode_becomes_a_good_row_items_to_list_can_show(self):
        result = self.post(photos=2, note='Box opened, contents fine').get_json()
        self.assertTrue(result['success'])
        self.assertEqual(result['upc'], BARCODE)
        self.assertEqual(
            self.rows('SELECT good_qty, bad_qty, unchecked_qty, bol_number FROM bol_items WHERE upc = ?', BARCODE),
            [(1, 0, 0, 'CUSTOM')])
        self.assertEqual(self.rows('SELECT status, reason, note FROM items_prep_status WHERE upc = ?', BARCODE),
                         [('good', '', 'Box opened, contents fine')])
        # Photos and the note are scoped the way Items-to-List reads them for a base barcode.
        self.assertEqual(self.rows('SELECT COUNT(*), row_status FROM items_prep_images WHERE upc = ?', BARCODE),
                         [(2, 'good')])
        self.assertEqual(self.rows('SELECT row_status FROM items_prep_notes WHERE upc = ?', BARCODE), [('good',)])
        self.assertEqual(len(list((self.root / 'static' / 'items_prep').iterdir())), 2)

    def test_damaged_item_takes_a_suffix_because_a_base_row_cannot_be_bad(self):
        result = self.post(condition='damaged', **{'defect[]': ['Broken', 'Missing pieces']}).get_json()
        self.assertEqual(result['upc'], BARCODE + '-1')
        self.assertEqual(result['status'], 'bad')
        self.assertEqual(self.rows('SELECT status, reason FROM items_prep_status WHERE upc = ?', BARCODE + '-1'),
                         [('bad', 'Broken | Missing pieces')])
        self.assertEqual(self.rows('SELECT good_qty, bad_qty, unchecked_qty FROM bol_items WHERE upc = ?', BARCODE),
                         [(0, 1, 0)])
        self.assertEqual(self.rows('SELECT temporary, bad_qty, quantity FROM bol_items WHERE upc = ?', BARCODE + '-1'),
                         [(1, 1, 1)])

    def test_damaged_without_a_chosen_defect_is_still_filed_as_a_defect(self):
        self.post(condition='damaged')
        self.assertEqual(self.rows('SELECT reason FROM items_prep_status WHERE upc = ?', BARCODE + '-1'),
                         [('Other',)])
        self.assertIn('Other', DEFECTS)

    def test_a_second_unit_of_one_barcode_never_overwrites_the_first(self):
        first = self.post(note='first unit').get_json()
        second = self.post(note='second unit').get_json()
        self.assertEqual(first['upc'], BARCODE)
        self.assertEqual(second['upc'], BARCODE + '-1')
        self.assertEqual(sorted(self.rows('SELECT upc, note FROM items_prep_status')),
                         [(BARCODE, 'first unit'), (BARCODE + '-1', 'second unit')])

    def test_a_suffix_is_never_reused_while_its_photos_still_exist(self):
        self.post()
        self.post(photos=1)  # takes -1
        with self.db('bol.db') as conn:
            conn.execute('DELETE FROM bol_items WHERE upc = ?', (BARCODE + '-1',))
            conn.execute('DELETE FROM items_prep_status WHERE upc = ?', (BARCODE + '-1',))
        self.assertEqual(self.post().get_json()['upc'], BARCODE + '-2')

    def test_the_recording_is_kept_even_when_the_transcript_is_empty(self):
        self.post(audio=True, note='')
        self.assertEqual(self.rows('SELECT media_type, row_status FROM items_prep_media WHERE upc = ?', BARCODE),
                         [('audio', 'good')])
        self.assertEqual(self.rows('SELECT COUNT(*) FROM items_prep_notes'), [(0,)])

    def test_an_existing_manifest_item_keeps_its_row_and_its_counts(self):
        with self.db('bol.db') as conn:
            conn.execute('''INSERT INTO bol_items (upc, item_description, lot_number, bol_number, import_date,
                            original_qty, unchecked_qty, good_qty, bad_qty, quantity)
                            VALUES (?, 'Manifest mixer', 'LOT9', 'BOL-4', '2026-01-01', 4, 4, 0, 0, 4)''', (BARCODE,))
        result = self.post(condition='damaged', **{'defect[]': ['Box damage']}).get_json()
        self.assertEqual(result['upc'], BARCODE + '-1')
        self.assertEqual(result['lot_number'], 'LOT9')
        self.assertEqual(self.rows('''SELECT item_description, good_qty, bad_qty, unchecked_qty, bol_number
                                      FROM bol_items WHERE upc = ?''', BARCODE),
                         [('Manifest mixer', 0, 1, 3, 'BOL-4')])

    def test_the_same_upload_twice_adds_one_item(self):
        self.tokens += 1
        token = f'{self.tokens:032x}'
        first = self.post(request_id=token).get_json()
        repeat = self.post(request_id=token).get_json()
        self.assertEqual(first['upc'], repeat['upc'])
        self.assertTrue(repeat['repeated'])
        self.assertEqual(self.rows('SELECT COUNT(*) FROM items_prep_status'), [(1,)])

    def test_the_prep_log_records_what_the_bench_did(self):
        self.post(condition='damaged', **{'defect[]': ['Broken']})
        self.assertEqual(self.logged[0]['source'], 'prep-plus')
        self.assertEqual(self.logged[0]['status'], 'bad')
        self.assertEqual(self.logged[0]['base_upc'], BARCODE)

    # ---- identifying a scan ----------------------------------------------

    def test_our_own_listing_beats_the_macy_manifest(self):
        self.stock(macy='MENS SHIRT BLU LG', ebay='Polo Ralph Lauren blue shirt, large')
        found = self.identify()
        self.assertEqual((found['found'], found['source'], found['title']),
                         (True, 'ebay_store', 'Polo Ralph Lauren blue shirt, large'))

    def test_the_macy_manifest_answers_when_we_have_not_listed_it(self):
        self.stock(macy='MENS SHIRT BLU LG')
        found = self.identify()
        self.assertEqual((found['found'], found['source'], found['label'], found['image_url']),
                         (True, 'macy_bol', 'Macy BOL', 'm.jpg'))

    def test_an_amazon_listing_answers_when_ebay_has_nothing(self):
        self.stock(macy='MENS SHIRT BLU LG', amazon='Ralph Lauren Mens Classic Fit Shirt')
        found = self.identify()
        self.assertEqual((found['source'], found['title']),
                         ('amazon_store', 'Ralph Lauren Mens Classic Fit Shirt'))

    def test_a_barcode_nobody_holds_is_left_for_the_catalogs(self):
        found = self.identify()
        self.assertFalse(found['found'])
        # Every source is named in the report, so the page can say what it asked.
        self.assertEqual([row['source'] for row in found['tried']],
                         ['ebay_store', 'amazon_store', 'macy_bol'])

    def test_a_placeholder_title_is_not_an_answer(self):
        self.stock(ebay='unknown', macy='MENS SHIRT BLU LG')
        self.assertEqual(self.identify()['source'], 'macy_bol')

    def test_a_missing_store_database_is_not_an_answer_either(self):
        (self.root / 'ebayStore.db').unlink()
        self.stock(macy='MENS SHIRT BLU LG')
        found = self.identify()
        self.assertEqual(found['source'], 'macy_bol')
        self.assertEqual([row for row in found['tried'] if row['source'] == 'ebay_store'][0]['result'],
                         'unavailable')

    def test_identify_needs_a_barcode(self):
        response = self.client.get('/api/prep-plus/identify?upc=')
        self.assertEqual(response.status_code, 400)
        self.assertIn('Scan a barcode first', response.get_json()['error'])

    # ---- the refusals ----------------------------------------------------

    def test_missing_pieces_of_an_item_are_named_not_swallowed(self):
        for payload, expected in [
            (dict(barcode=''), 'Scan, type or generate a barcode'),
            (dict(title=''), 'Say or type a name'),
            (dict(title='unknown'), 'short descriptive name'),
            (dict(condition='sideways'), 'Good or Damaged'),
            (dict(request_id='not-a-token'), 'Reload Prep +'),
        ]:
            response = self.post(**payload)
            self.assertEqual(response.status_code, 400, payload)
            self.assertIn(expected, response.get_json()['error'], payload)
        self.assertEqual(self.rows('SELECT COUNT(*) FROM bol_items'), [(0,)])

    def test_a_photo_that_is_not_an_image_leaves_nothing_behind(self):
        self.tokens += 1
        response = self.client.post('/api/prep-plus/item', content_type='multipart/form-data', data=dict(
            request_id=f'{self.tokens:032x}', barcode=BARCODE, title='Red mixer', condition='good',
            **{'photos[]': (io.BytesIO(b'this is not a photo'), 'notes.txt')}))
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.rows('SELECT COUNT(*) FROM bol_items'), [(0,)])
        self.assertFalse((self.root / 'static' / 'items_prep').exists())

    def test_the_page_renders_with_every_defect_the_list_filters_on(self):
        body = self.client.get('/prep-plus').get_data(as_text=True)
        self.assertIn('id="nextItem"', body)
        self.assertIn('id="micCondition"', body)
        self.assertIn('/static/prep-plus.js', body)
        for defect in DEFECTS:
            self.assertIn(defect, body)


if __name__ == '__main__':
    unittest.main()
