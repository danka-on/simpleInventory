"""Sweet Shelves Lister "+ NEW": the draft the phone and the side panel fill together.

Runs against temporary databases through the real sweetshelves helpers; no message leaves the
process and no store is touched.
"""
import base64
import io
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from contextlib import closing
from unittest.mock import patch

os.environ.setdefault('DISABLE_BACKGROUND_SERVICES', '1')

from flask import Flask

from sweetshelves import config, database, errors, listing_checks, listing_queue, warehouse_receiving
import lister_routes


PANEL = {'X-Sweet-Shelves-Lister': '1'}
JPEG = base64.b64decode(
    '/9j/4AAQSkZJRgABAQEAYABgAAD/2wBDAAgGBgcGBQgHBwcJCQgKDBQNDAsLDBkSEw8UHRofHh0a'
    'HBwgJC4nICIsIxwcKDcpLDAxNDQ0Hyc5PTgyPC4zNDL/wAALCAABAAEBAREA/8QAFAABAAAAAAAA'
    'AAAAAAAAAAAACf/EABQQAQAAAAAAAAAAAAAAAAAAAAD/2gAIAQEAAD8AKp//2Q==')


class NewItemTestCase(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.folder.cleanup)
        self.root = Path(self.folder.name)
        (self.root / 'static').mkdir()
        self._patch = patch.object(config, 'BASE_DIR', self.root)
        self._patch.start()
        self.addCleanup(self._patch.stop)

        self.app = Flask('lister-new-item-test', static_folder=str(self.root / 'static'),
                         template_folder=str(Path(__file__).resolve().parent / 'templates'))
        self.app.teardown_appcontext(database.close_db_connections)

        self.telegram_sent = []
        self.telegram_deleted = []
        self.telegram_result = (True, {'message_id': 7})
        self.known_names = {}

        def fake_send(chat_id, text, disable_notification=True):
            self.telegram_sent.append((chat_id, text, disable_notification))
            return self.telegram_result

        def fake_lookup(barcode):
            found = self.known_names.get(str(barcode or '').strip(), {})
            return {'title': found.get('title', ''), 'image_url': found.get('image_url', '')}

        self.lister = lister_routes.register(self.app, {
            'db_connection': database.db_connection,
            '_safe_error': errors._safe_error,
            '_listagent_mark_listed': listing_queue._listagent_mark_listed,
            '_listagent_upc_variants': listing_queue._listagent_upc_variants,
            '_listagent_format_upc12': listing_queue._listagent_format_upc12,
            '_listagent_init_tables': listing_checks._listagent_init_tables,
            '_listagent_add_to_queue': listing_queue._listagent_add_to_queue,
            '_add_item_screening_lookup': fake_lookup,
            '_ensure_custom_item_registry': warehouse_receiving._ensure_custom_item_registry,
            '_telegram_send_message': fake_send,
            '_telegram_collect_recipient_rows': lambda: [{'chat_id': '111', 'display_name': 'Danka', 'enabled': 1}],
            '_telegram_get_bot_token': lambda: 'test-bot-token',
            'BASE_DIR': self.root,
        })
        self.client = self.app.test_client()

        # deleteMessage is the only thing the module reaches the network for.
        class FakeResponse:
            ok = True

            @staticmethod
            def json():
                return {'ok': True}

        def fake_post(target, json=None, timeout=None):
            self.telegram_deleted.append(json)
            return FakeResponse()

        self._requests = patch('requests.post', fake_post)
        self._requests.start()
        self.addCleanup(self._requests.stop)

    # -- helpers ---------------------------------------------------------------------------

    def sql(self, db, query, args=()):
        with closing(sqlite3.connect(self.root / db)) as conn:
            conn.row_factory = sqlite3.Row
            return [dict(r) for r in conn.execute(query, args).fetchall()]

    def create(self, **body):
        res = self.client.post('/api/lister/new', json=body, headers=PANEL, base_url='https://pi.example')
        self.assertEqual(res.status_code, 201, res.get_json())
        return res.get_json()

    def panel(self, path, body=None, method='POST', expect=200):
        call = getattr(self.client, method.lower())
        res = call(path, json=(body if body is not None else {}), headers=PANEL, base_url='https://pi.example') \
            if method != 'GET' else call(path, headers=PANEL, base_url='https://pi.example')
        self.assertEqual(res.status_code, expect, res.get_json())
        return res.get_json()

    def phone(self, token, path, body):
        res = self.client.post(f'/api/lister/new/t/{token}{path}', json=body, base_url='https://pi.example')
        self.assertIn(res.status_code, (200, 201), res.get_json())
        return res.get_json()

    # -- tests -----------------------------------------------------------------------------

    def test_opening_a_draft_buzzes_the_phone_with_a_link_to_this_draft(self):
        created = self.create()
        draft = created['draft']
        self.assertEqual(draft['stage'], 'title')
        self.assertEqual(draft['status'], 'draft')
        self.assertEqual(draft['missing'], ['barcode', 'name'])
        self.assertFalse(draft['ready'])
        self.assertEqual(created['link']['kind'], 'intake')

        self.assertEqual([chat for chat, _t, _q in self.telegram_sent], ['111'])
        chat, text, quiet = self.telegram_sent[0]
        self.assertIn(f"https://pi.example/items-to-list/new-item/{draft['token']}", text)
        self.assertFalse(quiet, 'the phone should buzz: that is the point of the button')

    def test_the_phone_dictates_the_name_the_details_and_the_photos(self):
        draft = self.create()['draft']
        token = draft['token']

        page = self.client.get(f'/items-to-list/new-item/{token}')
        self.assertEqual(page.status_code, 200)
        self.assertIn(token.encode(), page.data)
        # Three steps, each with its own microphone or camera, and the dictation route the
        # receiving screens use. A step quietly dropped from the page is a step nobody fills.
        for marker in (b'id="stepTitle"', b'id="stepDetails"', b'id="stepPhotos"', b'id="titleMic"',
                       b'id="detailsMic"', b'id="liveShutter"', b'/api/warehouse/name-dictation'):
            self.assertIn(marker, page.data, marker)

        state = self.client.get(f'/api/lister/new/t/{token}').get_json()
        self.assertTrue(state['success'])
        self.assertEqual(state['draft']['stage'], 'title')

        after = self.phone(token, '/step', {'title': 'Ninja blender, 1000 watt, black', 'stage': 'details'})
        self.assertEqual(after['draft']['title'], 'Ninja blender, 1000 watt, black')
        self.assertEqual(after['draft']['titleSource'], 'voice')
        self.assertEqual(after['draft']['stage'], 'details')

        after = self.phone(token, '/step', {'description': 'Lid is scratched, jug is clean.', 'stage': 'photos'})
        self.assertEqual(after['draft']['description'], 'Lid is scratched, jug is clean.')
        self.assertEqual(after['draft']['stage'], 'photos')

        res = self.client.post(f'/api/lister/new/t/{token}/photos',
                               data={'photos[]': (io.BytesIO(JPEG), 'phone-1.jpg')},
                               content_type='multipart/form-data', base_url='https://pi.example')
        self.assertEqual(res.status_code, 201, res.get_json())
        photos = res.get_json()['draft']['photos']
        self.assertEqual(len(photos), 1)
        self.assertTrue(photos[0]['url'].startswith('/static/items_prep/new_'))
        self.assertTrue((self.root / 'static' / photos[0]['url'].split('/static/')[1]).is_file())

        # Still not submittable: nobody has given it a barcode.
        self.assertEqual(self.panel(f"/api/lister/new/{draft['id']}/submit", expect=400)['error'],
                         'Scan a barcode, or generate one, before submitting.')

    def test_opening_the_link_takes_the_bot_message_back_down(self):
        draft = self.create()['draft']
        res = self.client.post(f"/api/lister/new/t/{draft['token']}/opened", json={})
        self.assertEqual(res.get_json(), {'success': True, 'deleted': 1}, res.get_json())
        self.assertEqual(self.telegram_deleted, [{'chat_id': '111', 'message_id': 7}])
        # Twice must not delete twice.
        self.client.post(f"/api/lister/new/t/{draft['token']}/opened", json={})
        self.assertEqual(len(self.telegram_deleted), 1)

    def test_a_barcode_we_already_know_replaces_the_link_with_a_photos_only_one(self):
        self.known_names['012345678905'] = {'title': 'Lenox Butterfly Meadow Plate'}
        draft = self.create()['draft']
        self.telegram_sent.clear()

        result = self.panel(f"/api/lister/new/{draft['id']}/barcode", {'barcode': '012345678905'})
        self.assertEqual(result['systemTitle'], 'Lenox Butterfly Meadow Plate')
        self.assertEqual(result['draft']['title'], 'Lenox Butterfly Meadow Plate')
        self.assertEqual(result['draft']['titleSource'], 'system')
        self.assertEqual(result['draft']['upc'], '012345678905')

        # The first message is gone and a second one, straight to the camera, took its place.
        self.assertEqual(self.telegram_deleted, [{'chat_id': '111', 'message_id': 7}])
        self.assertEqual(result['link']['kind'], 'photos')
        self.assertEqual(result['link']['stage'], 'photos')
        self.assertEqual(len(self.telegram_sent), 1)
        self.assertIn('Lenox Butterfly Meadow Plate', self.telegram_sent[0][1])
        self.assertIn('step=photos', self.telegram_sent[0][1])

    def test_a_dictated_name_is_not_overwritten_by_the_barcode(self):
        self.known_names['012345678905'] = {'title': 'Whatever the catalog says'}
        draft = self.create()['draft']
        self.phone(draft['token'], '/step', {'title': 'The name the warehouse gave it', 'stage': 'details'})
        self.telegram_sent.clear()

        result = self.panel(f"/api/lister/new/{draft['id']}/barcode", {'barcode': '012345678905'})
        self.assertEqual(result['draft']['title'], 'The name the warehouse gave it')
        self.assertEqual(result['draft']['titleSource'], 'voice')
        self.assertEqual(self.telegram_sent, [], 'the phone is already past the name: leave its link alone')

    def test_a_barcode_two_open_drafts_both_want_is_refused(self):
        first = self.create()['draft']
        second = self.create()['draft']
        self.panel(f"/api/lister/new/{first['id']}/barcode", {'barcode': '777000000001', 'kind': 'generated'})
        error = self.panel(f"/api/lister/new/{second['id']}/barcode", {'barcode': '777000000001'}, expect=409)
        self.assertIn('777000000001', error['error'])

    def test_marking_damage_keeps_the_photo_the_phone_took(self):
        draft = self.create()['draft']
        self.client.post(f"/api/lister/new/t/{draft['token']}/photos",
                         data={'photos[]': (io.BytesIO(JPEG), 'phone-1.jpg')},
                         content_type='multipart/form-data', base_url='https://pi.example')
        photo = self.panel(f"/api/lister/new/{draft['id']}", method='GET')['draft']['photos'][0]

        marked = self.panel(f"/api/lister/new/{draft['id']}/photos/{photo['id']}", {
            'image': 'data:image/jpeg;base64,' + base64.b64encode(JPEG).decode(),
            'note': 'Cracked corner, circled',
        })['draft']['photos'][0]
        self.assertEqual(marked['url'], photo['url'], 'the original is what the item really looked like')
        self.assertTrue(marked['markedUrl'].endswith('_marked.jpg'), marked['markedUrl'])
        self.assertEqual(marked['note'], 'Cracked corner, circled')
        self.assertTrue((self.root / 'static' / marked['markedUrl'].split('/static/')[1]).is_file())

        # Rubbing the circles out again drops the copy but keeps the note.
        cleared = self.panel(f"/api/lister/new/{draft['id']}/photos/{photo['id']}", {'image': ''})['draft']['photos'][0]
        self.assertEqual(cleared['markedUrl'], '')
        self.assertEqual(cleared['note'], 'Cracked corner, circled')

    def test_submitting_writes_the_item_where_receiving_and_listing_both_look(self):
        draft = self.create()['draft']
        token = draft['token']
        self.phone(token, '/step', {'title': 'Ninja blender, 1000 watt, black', 'stage': 'details'})
        self.phone(token, '/step', {'description': 'Jug is clean, lid is scratched.', 'stage': 'photos'})
        self.client.post(f'/api/lister/new/t/{token}/photos',
                         data={'photos[]': (io.BytesIO(JPEG), 'phone-1.jpg')},
                         content_type='multipart/form-data', base_url='https://pi.example')
        photo = self.panel(f"/api/lister/new/{draft['id']}", method='GET')['draft']['photos'][0]
        self.panel(f"/api/lister/new/{draft['id']}/photos/{photo['id']}", {
            'image': 'data:image/jpeg;base64,' + base64.b64encode(JPEG).decode(), 'note': 'Cracked corner'})
        self.panel(f"/api/lister/new/{draft['id']}/barcode", {'barcode': '777000000042', 'kind': 'generated'})

        res = self.client.post(f"/api/lister/new/{draft['id']}/submit", json={'actor': 'Dan'},
                               headers=PANEL, base_url='https://pi.example')
        self.assertEqual(res.status_code, 201, res.get_json())
        body = res.get_json()
        self.assertEqual(body['upc'], '777000000042')
        self.assertEqual(body['draft']['status'], 'submitted')

        # The name and the picture, where /barcode and /multibarcode read them from.
        registry = self.sql('bol.db', 'SELECT * FROM custom_item_registry WHERE upc = ?', ('777000000042',))
        self.assertEqual(len(registry), 1)
        self.assertEqual(registry[0]['item_description'], 'Ninja blender, 1000 watt, black')
        self.assertEqual(registry[0]['image_url'], '/static/custom_items/777000000042.jpg')
        self.assertTrue((self.root / 'static' / 'custom_items' / '777000000042.jpg').is_file())
        items = self.sql('bol.db', 'SELECT * FROM bol_items WHERE upc = ?', ('777000000042',))
        self.assertEqual([r['item_description'] for r in items], ['Ninja blender, 1000 watt, black'])
        self.assertEqual(items[0]['bol_number'], 'CUSTOM')

        # Both copies of the photo, and the dictated details plus the damage note, as prep evidence.
        images = self.sql('bol.db', 'SELECT image_path FROM items_prep_images WHERE upc = ? ORDER BY id',
                          ('777000000042',))
        self.assertEqual(len(images), 2, images)
        self.assertTrue(images[1]['image_path'].endswith('_marked.jpg'))
        notes = [r['note'] for r in self.sql('bol.db', 'SELECT note FROM items_prep_notes WHERE upc = ? ORDER BY id',
                                             ('777000000042',))]
        self.assertEqual(notes, ['Jug is clean, lid is scratched.', 'Photo 1: Cracked corner'])

        # And it is queued on Items to List, which is what the side panel reads.
        queued = self.sql('listagent.db', 'SELECT * FROM listing_queue WHERE upc = ?', ('777000000042',))
        self.assertEqual(len(queued), 1)
        self.assertEqual(queued[0]['status'], 'queued')
        self.assertEqual(queued[0]['source'], 'lister-new')
        self.assertEqual(queued[0]['title'], 'Ninja blender, 1000 watt, black')

        # The link is gone from the chat and the draft cannot be submitted twice.
        self.assertEqual(self.telegram_deleted, [{'chat_id': '111', 'message_id': 7}])
        self.panel(f"/api/lister/new/{draft['id']}/submit", expect=409)

        # And from the side panel's own point of view it is now an ordinary queued item, on both
        # store lists, which is the whole point of submitting.
        for platform in ('ebay', 'amazon'):
            listed = self.panel('/api/lister/queue?platform=' + platform, method='GET')['items']
            row = next((it for it in listed if it['upc'] == '777000000042'), None)
            self.assertIsNotNone(row, platform)
            self.assertEqual((row['status'], row['title'], row['source']),
                             ('queued', 'Ninja blender, 1000 watt, black', 'lister-new'))

    def test_a_damage_note_is_what_the_listing_reads_the_condition_out_of(self):
        """The whole reason the note is written as a prep note and not as a photo caption."""
        verdict = lister_routes.condition_from_notes(['Photo 1: Cracked corner'])
        self.assertEqual(verdict['condition'], 'USED_GOOD')
        self.assertIn('Cracked corner', verdict['conditionDescription'])

    def submit_with(self, upc, step):
        draft = self.create()['draft']
        self.phone(draft['token'], '/step', {'title': 'Ninja blender', 'stage': 'details'})
        shown = self.phone(draft['token'], '/step', {**step, 'stage': 'photos'})['draft']
        self.panel(f"/api/lister/new/{draft['id']}/barcode", {'barcode': upc, 'kind': 'generated'})
        body = self.panel(f"/api/lister/new/{draft['id']}/submit", {'actor': 'Dan'}, expect=201)
        upc = body['upc']
        status = self.sql('bol.db', 'SELECT status, reason, note FROM items_prep_status WHERE upc = ?', (upc,))
        notes = [r['note'] for r in self.sql('bol.db', 'SELECT note FROM items_prep_notes WHERE upc = ? ORDER BY id', (upc,))]
        return shown, body, status, notes

    def test_saying_nothing_about_it_passes_it_good_and_new(self):
        shown, body, status, notes = self.submit_with('777000000051', {'description': ''})
        self.assertEqual((shown['prepStatus'], shown['defects'], shown['prepStatusChosen']), ('good', [], ''))
        self.assertEqual(body['verdict'], {'status': 'good', 'reason': ''})
        self.assertEqual(body['upc'], '777000000051', 'a good unit keeps its own barcode')
        self.assertEqual([(r['status'], r['reason']) for r in status], [('good', '')])
        self.assertEqual(notes, [])
        # Item Prep passing a unit good with nothing said about it is what makes the listing New.
        self.assertEqual(lister_routes.condition_from_notes(notes, prep_status='good')['condition'], 'NEW')

    def test_defect_bubbles_make_it_bad_and_used(self):
        shown, body, status, notes = self.submit_with('777000000052', {
            'description': 'Lid is cracked and the manual is not there.', 'prepStatus': 'bad',
            'defects': ['Broken', 'missing pieces', 'Not a defect']})
        self.assertEqual((shown['prepStatus'], shown['defects']), ('bad', ['Missing pieces', 'Broken']))
        # Items to List shows BAD only on a suffixed barcode, so it is filed the way Prep + files it.
        self.assertEqual((body['upc'], body['baseUpc']), ('777000000052-1', '777000000052'))
        suffix = self.sql('bol.db', 'SELECT lot_number, bol_number, item_description FROM bol_items WHERE upc = ?',
                          ('777000000052-1',))
        self.assertEqual(suffix, [{'lot_number': None, 'bol_number': 'CUSTOM', 'item_description': 'Ninja blender'}])
        queued = self.sql('listagent.db', 'SELECT upc, source FROM listing_queue WHERE upc LIKE ?', ('777000000052%',))
        self.assertEqual(queued, [{'upc': '777000000052-1', 'source': 'lister-new'}])
        self.assertEqual([(r['status'], r['reason'], r['note']) for r in status],
                         [('bad', 'Missing pieces | Broken', 'Lid is cracked and the manual is not there.')])
        self.assertEqual(notes, ['Defect: Missing pieces, Broken', 'Lid is cracked and the manual is not there.'])
        self.assertEqual(lister_routes.condition_from_notes(notes, prep_status='bad')['condition'], 'USED_GOOD')

    def test_a_defect_alone_means_bad_and_return_keeps_its_word(self):
        shown = self.submit_with('777000000053', {'defects': ['Replacement']})[0]
        self.assertEqual((shown['prepStatus'], shown['defects']), ('bad', ['Replacement']))
        shown, _body, status, _notes = self.submit_with('777000000054', {'prepStatus': 'return'})
        self.assertEqual(shown['prepStatus'], 'return')
        self.assertEqual([(r['status'], r['reason']) for r in status], [('return', '')])
        # Tapping Good after a defect drops the defect instead of arguing with it.
        shown = self.submit_with('777000000055', {'prepStatus': 'good', 'defects': ['Broken']})[0]
        self.assertEqual((shown['prepStatus'], shown['defects']), ('good', []))

    def test_the_finder_trail_says_it_came_from_lister_new(self):
        from finder_trail import collect_trail

        body = self.submit_with('777000000056', {'prepStatus': 'bad', 'defects': ['Broken']})[1]
        self.assertEqual(body['draft']['filedUpc'], '777000000056-1')

        def connect(name):
            return closing(sqlite3.connect(self.root / name))

        for searched in ('777000000056-1', '777000000056'):
            trail = collect_trail(self.root, connect, searched)
            self.assertEqual(trail['identity']['origin'], 'lister-new', searched)
            created = [e for e in trail['events'] if e['kind'] == 'created']
            self.assertEqual([e['title'] for e in created], ['Added via Lister + NEW'], searched)
            self.assertIn('filed as 777000000056-1', created[0]['detail'])
            self.assertEqual(created[0]['category'], 'prep')
        self.assertNotEqual(collect_trail(self.root, connect, '777000000099')['identity']['origin'], 'lister-new')

    def test_an_unknown_status_is_refused(self):
        draft = self.create()['draft']
        res = self.client.post(f"/api/lister/new/t/{draft['token']}/step", json={'prepStatus': 'shiny'},
                               base_url='https://pi.example')
        self.assertEqual(res.status_code, 400)
        self.assertIn('status must be one of', res.get_json()['error'])

    def test_an_item_with_no_name_is_not_submittable(self):
        draft = self.create()['draft']
        self.panel(f"/api/lister/new/{draft['id']}/barcode", {'barcode': '777000000043', 'kind': 'generated'})
        self.assertEqual(self.panel(f"/api/lister/new/{draft['id']}/submit", expect=400)['error'],
                         'This item still has no name.')

    def test_the_panel_can_pick_a_draft_back_up_and_throw_one_away(self):
        first = self.create()['draft']
        self.create()
        self.assertEqual(len(self.panel('/api/lister/new', method='GET')['drafts']), 2)

        self.panel(f"/api/lister/new/{first['id']}/cancel")
        open_now = self.panel('/api/lister/new', method='GET')['drafts']
        self.assertEqual([d['id'] for d in open_now].count(first['id']), 0)
        self.assertEqual(self.panel(f"/api/lister/new/{first['id']}", method='GET')['draft']['status'], 'cancelled')

    def test_another_site_cannot_drive_the_panel_routes(self):
        draft = self.create()['draft']
        res = self.client.post(f"/api/lister/new/{draft['id']}/submit", json={},
                               headers={'Sec-Fetch-Site': 'cross-site'})
        self.assertEqual(res.status_code, 403)
        res = self.client.post(f"/api/lister/new/t/{draft['token']}/step", json={'title': 'x'},
                               headers={'Sec-Fetch-Site': 'cross-site'})
        self.assertEqual(res.status_code, 403)

    def test_a_draft_that_telegram_cannot_reach_still_opens(self):
        self.telegram_result = (False, 'chat not found')
        created = self.create()
        self.assertEqual(created['draft']['stage'], 'title')
        self.assertIn('chat not found', created['linkError'])
        self.assertFalse(created['draft']['linkSent'])


if __name__ == '__main__':
    unittest.main()
