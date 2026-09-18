"""Facebook Marketplace inbox reader: row parsing, the first-capture baseline, buyer-message alerts,
the Marketplace tag on every alert, and the routes' cross-site guard."""

import tempfile
import threading
import time
import unittest

from flask import Flask

import fb_marketplace as fbm

LISTER = {'X-Sweet-Shelves-Lister': '1', 'Cf-Access-Authenticated-User-Email': 'Dan@Example.com'}


def row(thread_id, *lines, bold=()):
    return {'threadId': thread_id, 'href': f'https://www.facebook.com/marketplace/t/{thread_id}/',
            'lines': list(lines), 'bold': list(bold)}


class ParseRowTests(unittest.TestCase):
    def test_name_item_snippet_and_age(self):
        parsed = fbm.parse_row(row('1234567', 'Jane Doe · Nike Air Max 90', 'Is this still available? · 2h'))
        self.assertEqual((parsed['name'], parsed['item'], parsed['snippet'], parsed['time']),
                         ('Jane Doe', 'Nike Air Max 90', 'Is this still available?', '2h'))
        self.assertFalse(parsed['from_me'])
        self.assertEqual(parsed['href'], 'https://www.facebook.com/marketplace/t/1234567/')

    def test_age_on_its_own_line_and_name_prefix(self):
        parsed = fbm.parse_row(row('1234567', 'Jane Doe · Lamp', 'Jane: Can you do $20?', '·', '15m'))
        self.assertEqual((parsed['snippet'], parsed['time']), ('Can you do $20?', '15m'))

    def test_our_own_last_message(self):
        parsed = fbm.parse_row(row('1234567', 'Jane Doe · Lamp', 'You: Yes, still available · Mon'))
        self.assertTrue(parsed['from_me'])
        self.assertEqual((parsed['snippet'], parsed['time']), ('Yes, still available', 'Mon'))

    def test_thread_id_from_link_and_bold_snippet_is_unread(self):
        parsed = fbm.parse_row({'href': 'https://www.facebook.com/messages/t/99887766/', 'lines': ['Sam · Chair', 'Hello? · 1m'],
                                'bold': ['Sam · Chair', 'Hello?']})
        self.assertEqual(parsed['thread_id'], '99887766')
        self.assertTrue(parsed['unread'])

    def test_foreign_links_are_not_kept(self):
        parsed = fbm.parse_row({'threadId': '1234567', 'href': 'javascript:alert(1)', 'lines': ['A · B', 'hi']})
        self.assertEqual(parsed['href'], 'https://www.facebook.com/messages/t/1234567')

    def test_alert_text_is_tagged_marketplace(self):
        text = fbm.alert_text({'name': 'Jane', 'item': 'Lamp', 'snippet': 'Still there?',
                               'href': 'https://www.facebook.com/marketplace/t/1/'}, 'selling')
        self.assertTrue(text.startswith('🛒 Facebook Marketplace · Selling'))
        self.assertIn('Jane about "Lamp":', text)
        self.assertIn('Reply: https://www.facebook.com/marketplace/t/1/', text)


class CaptureTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(folder.cleanup)
        self.sent = []
        self.done = threading.Event()

        def send(chat_id, text, disable_notification=True):
            self.sent.append((chat_id, text, disable_notification))
            self.done.set()
            return True, {'message_id': 1}

        self.app = Flask(__name__, template_folder=str(fbm.Path(__file__).resolve().parent / 'templates'))
        self.market = fbm.register(self.app, {
            'BASE_DIR': folder.name,
            '_telegram_send_message': send,
            '_telegram_collect_recipient_rows': lambda: [{'chat_id': '10', 'enabled': 1}, {'chat_id': '10', 'enabled': 1},
                                                         {'chat_id': '20', 'enabled': 0}],
            '_telegram_chat_for_email': lambda email: {'chat_id': '77'} if email == 'linked@example.com' else None,
        })
        self.client = self.app.test_client()

    def capture(self, rows, role='selling', headers=LISTER):
        response = self.client.post('/api/fb-marketplace/capture', json={'rows': rows, 'role': role}, headers=headers)
        return response.status_code, response.get_json()

    def wait_sent(self, count):
        deadline = time.time() + 3
        while len(self.sent) < count and time.time() < deadline:
            time.sleep(0.02)

    def test_first_capture_is_a_quiet_baseline_then_buyer_messages_alert_once(self):
        status, data = self.capture([row('1111111', 'Jane · Lamp', 'Is this available? · 3h')])
        self.assertEqual(status, 200)
        self.assertTrue(data['baseline'])
        self.assertEqual(data['alerts'], 0)

        # Same message again (window narrower, snippet cut shorter): no alert.
        self.assertEqual(self.capture([row('1111111', 'Jane · Lamp', 'Is this avail… · 3h')])[1]['alerts'], 0)

        # A new buyer message and a brand-new chat both alert, once each, to every enabled chat (deduped).
        data = self.capture([row('1111111', 'Jane · Lamp', 'Can you do $15? · 1m'),
                             row('2222222', 'Bob · Chair', 'Hi! · now')])[1]
        self.assertEqual(data['alerts'], 2)
        self.wait_sent(2)
        self.assertEqual([chat for chat, _, _ in self.sent], ['10', '10'])
        self.assertTrue(all(text.startswith('🛒 Facebook Marketplace · Selling') for _, text, _ in self.sent))
        self.assertTrue(all(quiet is False for _, _, quiet in self.sent))
        self.assertEqual(self.capture([row('1111111', 'Jane · Lamp', 'Can you do $15? · 2m')])[1]['alerts'], 0)

    def test_our_replies_never_alert_and_clear_needs_reply(self):
        self.capture([row('1111111', 'Jane · Lamp', 'Is this available? · 3h')])
        self.assertEqual(self.capture([row('1111111', 'Jane · Lamp', 'You: Yes it is · now')])[1]['alerts'], 0)
        threads = self.client.get('/api/fb-marketplace/threads').get_json()['threads']
        self.assertFalse(threads[0]['needsReply'])
        self.assertEqual([h['fromMe'] for h in threads[0]['history']], [True, False])

    def test_alert_goes_to_the_readers_own_telegram_when_linked(self):
        self.capture([row('1111111', 'Jane · Lamp', 'Hi · 3h')])
        headers = dict(LISTER, **{'Cf-Access-Authenticated-User-Email': 'linked@example.com'})
        self.capture([row('1111111', 'Jane · Lamp', 'Still there? · now')], headers=headers)
        self.wait_sent(1)
        self.assertEqual([chat for chat, _, _ in self.sent], ['77'])

    def test_handled_and_threads_listing(self):
        self.capture([row('1111111', 'Jane · Lamp', 'Hi · 3h')], role='buying')
        threads = self.client.get('/api/fb-marketplace/threads').get_json()
        self.assertEqual(threads['threads'][0]['role'], 'buying')
        self.assertTrue(threads['threads'][0]['needsReply'])
        self.assertFalse(threads['reader']['stale'])
        response = self.client.post('/api/fb-marketplace/threads/1111111/handled', json={'handled': True})
        self.assertEqual(response.status_code, 200)
        self.assertFalse(self.client.get('/api/fb-marketplace/threads').get_json()['threads'][0]['needsReply'])
        self.assertEqual(self.client.post('/api/fb-marketplace/threads/999/handled', json={}).status_code, 404)

    def test_cross_site_posts_without_the_lister_header_are_refused(self):
        status, _ = self.capture([row('1111111', 'A · B', 'hi')], headers={'Sec-Fetch-Site': 'cross-site'})
        self.assertEqual(status, 403)
        self.assertEqual(self.client.post('/api/fb-marketplace/threads/1/handled', json={},
                                          headers={'Sec-Fetch-Site': 'cross-site'}).status_code, 403)

    def test_empty_inbox_keeps_the_probe_and_page_renders(self):
        status, data = self.capture([], role='')
        self.client.post('/api/fb-marketplace/capture', json={'rows': [], 'probe': {'threadLinks': 0}}, headers=LISTER)
        reader = self.client.get('/api/fb-marketplace/threads').get_json()['reader']
        self.assertTrue(reader['noRowsOnPage'])
        page = self.client.get('/fb-messages')
        self.assertEqual(page.status_code, 200)
        self.assertIn(b'Marketplace Messages', page.data)


if __name__ == '__main__':
    unittest.main()
