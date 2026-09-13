"""Ready to Ship: how a base-UPC sale lands on a suffixed rack unit."""
from test_feature_support import isolated_functions
from contextlib import contextmanager
from pathlib import Path
import sqlite3
import tempfile
from types import SimpleNamespace
import unittest

from sweetshelves import warehouse_matching as wm


def _match(row_id, barcode, note='', title='', location='or1s1', quantity=1, custom_title=False):
    return {
        'id': row_id, 'barcode': barcode, 'title': title, 'image': '', 'custom_title': custom_title,
        'quantity': quantity, 'item_position': location, 'pictureposition': '',
        'warehouse_note': note, 'location_code': location, 'location_key': location,
        'location_preview': location,
    }


SALAD_PLATES = {
    'barcode': '766360759483',
    'title': 'The Cellar Basics White Round Rim Salad Plates Set of 4 ( MISSING 1 PLATE)',
    'item_condition': 'New other (see details)',
    'item_condition_description': 'NEW. MISSING 1 SALAD PLATE. THIS IS SET OF 3 FOR SALE',
}


class ConditionClassTests(unittest.TestCase):
    def test_ebay_and_amazon_labels_classify(self):
        cases = {
            'New': 'new', 'New with tags': 'new', 'New without tags': 'new', 'New with box': 'new',
            'Brand New': 'new', '11': 'new', '1000': 'new', 'new_new': 'new',
            'New other (see details)': 'imperfect', 'Open box': 'imperfect', 'New – Open box': 'imperfect',
            'New with defects': 'imperfect', 'New with imperfections': 'imperfect',
            'Used': 'imperfect', 'Used - Mint': 'imperfect', 'Used - Good': 'imperfect',
            'Pre-owned - Excellent': 'imperfect', 'For parts or not working': 'imperfect',
            'Seller refurbished': 'imperfect', 'used_like_new': 'imperfect', '1': 'imperfect',
            '1500': 'imperfect', '3000': 'imperfect',
            '': 'unknown', 'Something else': 'unknown',
        }
        for label, expected in cases.items():
            with self.subTest(label=label):
                self.assertEqual(wm._ready_to_ship_condition_class({'item_condition': label}), expected)

    def test_is_new_accepts_every_new_spelling(self):
        self.assertTrue(wm._ready_to_ship_condition_is_new({'item_condition': 'New with tags'}))
        self.assertFalse(wm._ready_to_ship_condition_is_new({'item_condition': 'New other (see details)'}))
        self.assertFalse(wm._ready_to_ship_condition_is_new({'item_condition': ''}))


class ConditionNoteTests(unittest.TestCase):
    def test_tokens_read_shortfall_notes(self):
        self.assertEqual(wm._ready_to_ship_condition_tokens('-1 Salad Plate'), {'missing', '1', 'salad', 'plate'})
        self.assertEqual(wm._ready_to_ship_condition_tokens('MISSING 1 SALAD PLATE.'), {'missing', '1', 'salad', 'plate'})
        self.assertEqual(wm._ready_to_ship_condition_tokens('5x Cups'), {'5', 'cup'})
        self.assertEqual(wm._ready_to_ship_condition_tokens('Box Torn'), {'box', 'torn'})
        self.assertEqual(wm._ready_to_ship_condition_tokens('-2 Big Plates'), {'missing', '2', 'big', 'plate'})

    def test_defect_notes_are_recognized(self):
        for note in ('-1 Plate', 'Box Torn', '1 Broken (3 good)', 'Missing 1', '1 Plate Damage'):
            with self.subTest(note=note):
                self.assertTrue(wm._ready_to_ship_note_marks_defect(note))
        for note in ('', 'set', '5x Cups', '35 Piece', 'Amafe On Top Edge'):
            with self.subTest(note=note):
                self.assertFalse(wm._ready_to_ship_note_marks_defect(note))

    def test_score_prefers_the_note_naming_the_same_shortfall(self):
        plate = _match(1, '766360759483-1', note='-1 Salad Plate')
        cup = _match(2, '766360759483-2', note='-1 Cup')
        blank = _match(3, '766360759483-3')
        self.assertGreater(wm._ready_to_ship_condition_match_score(SALAD_PLATES, plate),
                           wm._ready_to_ship_condition_match_score(SALAD_PLATES, cup))
        self.assertGreater(wm._ready_to_ship_condition_match_score(SALAD_PLATES, cup), 0)
        self.assertEqual(wm._ready_to_ship_condition_match_score(SALAD_PLATES, blank), 0)


class RankingTests(unittest.TestCase):
    def test_not_new_sale_ranks_matching_note_first_regardless_of_id(self):
        rows = [
            _match(30, '766360759483-3'),
            _match(20, '766360759483-2', note='-1 Cup'),
            _match(10, '766360759483-1', note='-1 Salad Plate'),
        ]
        ranked = wm._ready_to_ship_rank_inventory_matches(rows, SALAD_PLATES)
        self.assertEqual([row['id'] for row in ranked], [10, 20, 30])

    def test_new_sale_avoids_units_noted_as_defective(self):
        order = {'barcode': '35886415860', 'title': 'Mikasa Glasses', 'item_condition': 'New'}
        rows = [
            _match(9, '35886415860-1', note='-1 glass'),
            _match(5, '35886415860-2'),
        ]
        ranked = wm._ready_to_ship_rank_inventory_matches(rows, order)
        self.assertEqual([row['id'] for row in ranked], [5, 9])

    def test_exact_identity_still_beats_condition(self):
        order = dict(SALAD_PLATES, image='https://i.ebayimg.com/images/g/abc/s-l1600.jpg')
        rows = [
            _match(1, '766360759483-1', note='-1 Salad Plate'),
            dict(_match(2, '766360759483-2', title=SALAD_PLATES['title'], custom_title=True),
                 image='https://i.ebayimg.com/images/g/abc/s-l1600.jpg'),
        ]
        ranked = wm._ready_to_ship_rank_inventory_matches(rows, order)
        self.assertEqual(ranked[0]['id'], 2)


class SelectionTests(unittest.TestCase):
    def setUp(self):
        self.exact = [_match(1, '766360759483', location='omr1s2')]
        self.suffixed = [_match(2, '766360759483-1', note='-1 Plate', location='omr2s2')]

    def test_not_new_sale_takes_suffixed_units_even_when_base_is_in_stock(self):
        rows, suggested, reason = wm._ready_to_ship_select_inventory_rows(SALAD_PLATES, self.exact, self.suffixed)
        self.assertEqual([row['id'] for row in rows], [2])
        self.assertTrue(suggested)
        self.assertEqual(reason, 'condition')

    def test_new_and_unknown_sales_keep_the_plain_barcode(self):
        for condition in ('New', 'New with tags', ''):
            with self.subTest(condition=condition):
                order = {'barcode': '766360759483', 'item_condition': condition}
                rows, suggested, reason = wm._ready_to_ship_select_inventory_rows(order, self.exact, self.suffixed)
                self.assertEqual([row['id'] for row in rows], [1])
                self.assertFalse(suggested)
                self.assertEqual(reason, '')

    def test_new_sale_borrows_a_suffixed_unit_only_when_nothing_else_is_left(self):
        order = {'barcode': '766360759483', 'item_condition': 'New'}
        rows, suggested, reason = wm._ready_to_ship_select_inventory_rows(order, [], self.suffixed)
        self.assertEqual([row['id'] for row in rows], [2])
        self.assertTrue(suggested)
        self.assertEqual(reason, 'only_stock')

    def test_order_naming_a_suffixed_unit_is_never_rerouted(self):
        order = dict(SALAD_PLATES, barcode='766360759483-1')
        rows, suggested, _ = wm._ready_to_ship_select_inventory_rows(order, self.suffixed, self.suffixed)
        self.assertEqual([row['id'] for row in rows], [2])
        self.assertFalse(suggested)

    def test_reason_names_condition_and_note(self):
        self.assertEqual(
            wm._ready_to_ship_match_reason(SALAD_PLATES, self.suffixed[0], 'condition'),
            'Sold as New other (see details) · unit note: -1 Plate',
        )
        self.assertEqual(
            wm._ready_to_ship_match_reason({'item_condition': 'New'}, _match(3, 'x-1'), 'only_stock'),
            'Only suffixed units in stock',
        )
        self.assertEqual(
            wm._ready_to_ship_match_reason({'item_condition': 'used_good'}, _match(4, 'x-1'), 'condition'),
            'Sold as Used Good',
        )
        self.assertEqual(wm._ready_to_ship_match_reason(SALAD_PLATES, self.suffixed[0], ''), '')

    def test_alternatives_list_the_plain_row_and_carry_notes(self):
        alternatives = wm._ready_to_ship_suffix_alternatives(self.suffixed + self.exact, '766360759483-1')
        self.assertEqual(len(alternatives), 1)
        self.assertEqual(alternatives[0]['barcode'], '766360759483')
        self.assertEqual(alternatives[0]['notes'], [])
        alternatives = wm._ready_to_ship_suffix_alternatives(self.suffixed + self.exact, '766360759483')
        self.assertEqual(alternatives[0]['barcode'], '766360759483-1')
        self.assertEqual(alternatives[0]['notes'], ['-1 Plate'])
        self.assertEqual(alternatives[0]['locations'], ['omr2s2'])


class LocationOptionsTests(unittest.TestCase):
    """The removal endpoint honors the unit the page suggested before the plain barcode."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.args = {}
        self.connections = []

        def connect(path, *args, **kwargs):
            conn = sqlite3.connect(self.folder / Path(str(path)).name)
            self.connections.append(conn)
            return conn
        self.connect = connect

        @contextmanager
        def connect_db(path):
            conn = connect(path)
            try:
                yield conn
                conn.commit()
            finally:
                conn.close()
        self.scope = dict(
            sqlite3=SimpleNamespace(connect=connect, Row=sqlite3.Row, Error=sqlite3.Error),
            BASE_DIR=self.folder, connect_db=connect_db,
            request=SimpleNamespace(args=self.args),
            jsonify=lambda data: data,
            _safe_error=lambda error, *args: str(error),
        )
        isolated_functions({'ready_to_ship_location_options'}, self.scope)
        with connect_db('sold.db') as conn:
            conn.execute('''CREATE TABLE orders (
                id INTEGER PRIMARY KEY, order_id TEXT, item_id TEXT, sku TEXT, store TEXT, barcode TEXT,
                source_upc TEXT, quantity INTEGER, title TEXT, location TEXT, listing_listing_id TEXT,
                listing_sku TEXT, listing_asin TEXT)''')
            conn.execute("INSERT INTO orders VALUES (7, 'ORD-7', '188029253143', '', 'ebay', '766360759483', "
                         "'', 1, 'Salad Plates', '', '', '', '')")
        with connect_db('searchRack.db') as conn:
            conn.execute('''CREATE TABLE SEARCHRACK (ID INTEGER PRIMARY KEY, TITLE TEXT, BARCODE TEXT,
                ITEM_POSITION TEXT, IMAGES TEXT, PICTUREPOSITION TEXT, ITEMID TEXT, QUANTITY INTEGER,
                CREATED_AT TEXT, IMAGE TEXT, CUSTOM_TITLE INTEGER, WAREHOUSE_NOTE TEXT)''')
            conn.execute("INSERT INTO SEARCHRACK VALUES (1920, 'Salad Plates', '766360759483', 'omr1s2', '', '', '', 1, '', '', 0, '')")
            conn.execute("INSERT INTO SEARCHRACK VALUES (3568, '', '766360759483-1', 'omr2s2', '', '', '', 1, '', '', 0, '-1 Plate')")
        self.addCleanup(self.close_connections)

    def close_connections(self):
        for conn in self.connections:
            try:
                conn.close()
            except Exception:
                pass

    def test_suggested_suffixed_unit_wins_over_base_row_in_stock(self):
        self.args['matched_barcode'] = '766360759483-1'
        payload = self.scope['ready_to_ship_location_options'](7)
        self.assertTrue(payload['success'])
        self.assertEqual(payload['barcode'], '766360759483-1')
        self.assertEqual([loc['location_code'] for loc in payload['locations']], ['omr2s2'])
        self.assertTrue(payload['can_fulfill'])

    def test_plain_barcode_without_suggestion_keeps_base_row(self):
        payload = self.scope['ready_to_ship_location_options'](7)
        self.assertEqual(payload['barcode'], '766360759483')
        self.assertEqual([loc['location_code'] for loc in payload['locations']], ['omr1s2'])

    def test_unrelated_suggestion_is_ignored(self):
        self.args['matched_barcode'] = '999999999999-1'
        payload = self.scope['ready_to_ship_location_options'](7)
        self.assertEqual(payload['barcode'], '766360759483')


if __name__ == '__main__':
    unittest.main()
