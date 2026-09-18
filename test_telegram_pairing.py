"""Self-serve Telegram pairing: a Cloudflare login links its own chat through a one-time t.me link."""

import os
import sqlite3
import tempfile
import time
import unittest
from contextlib import closing
from unittest.mock import patch

from flask import Flask

from sweetshelves import telegram as tg

EMAIL = 'dan@example.com'
HEADER = {'Cf-Access-Authenticated-User-Email': 'Dan@Example.com'}


def private_message(text, chat_id=555, first_name='Dan', chat_type='private'):
    return {'text': text, 'chat': {'id': chat_id, 'type': chat_type}, 'from': {'first_name': first_name}}


class TelegramPairingTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(folder.cleanup)
        previous = os.getcwd()
        os.chdir(folder.name)  # searchRack.db is opened relative to the working directory
        self.addCleanup(os.chdir, previous)
        self.app = Flask(__name__)
        self.sent = []
        for name, value in (('_telegram_send_message', lambda chat_id, text, disable_notification=True:
                                self.sent.append((str(chat_id), text)) or (True, {'message_id': 1})),
                            ('_telegram_bot_username', lambda: 'SweetShelves_Bot')):
            patcher = patch.object(tg, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def call(self, view, headers=None, method='POST'):
        with self.app.test_request_context('/', method=method, headers=headers or {}):
            response = view()
        body, status = (response if isinstance(response, tuple) else (response, 200))
        return body.get_json(), status

    def start_link(self):
        body, status = self.call(tg.api_telegram_link, HEADER)
        self.assertEqual(status, 200, body)
        return body['url'].split('?start=', 1)[1]

    def test_a_link_needs_a_cloudflare_login(self):
        body, status = self.call(tg.api_telegram_link)
        self.assertEqual(status, 401)
        self.assertIn('sign in', body['error'])

    def test_the_link_is_a_one_time_t_me_deep_link_for_the_signed_in_email(self):
        body, _ = self.call(tg.api_telegram_link, HEADER)
        self.assertEqual(body['email'], EMAIL, 'the email is lower-cased')
        self.assertTrue(body['url'].startswith('https://t.me/SweetShelves_Bot?start='), body['url'])
        code = body['url'].split('?start=', 1)[1]
        self.assertRegex(code, r'^[A-Za-z0-9_-]{16,64}$', 'Telegram only passes these characters through start=')

    def test_tapping_the_link_pairs_the_chat_and_says_so(self):
        code = self.start_link()
        self.assertTrue(tg._telegram_handle_link(private_message('/start ' + code)))
        linked = tg.telegram_chat_for_email(EMAIL)
        self.assertEqual((linked['chat_id'], linked['name']), ('555', 'Dan'))
        self.assertTrue(linked['linked_at'])
        self.assertEqual(self.sent[-1][0], '555')
        self.assertIn('Linked to ' + EMAIL, self.sent[-1][1])
        me, _ = self.call(tg.api_telegram_me, HEADER, method='GET')
        self.assertEqual(me['linked']['name'], 'Dan')
        self.assertEqual(me['linked']['at'], linked['linked_at'], 'the page watches this to notice a re-link')

    def test_a_code_works_once(self):
        code = self.start_link()
        tg._telegram_handle_link(private_message('/start ' + code, chat_id=555))
        self.assertTrue(tg._telegram_handle_link(private_message('/start ' + code, chat_id=999)))
        self.assertEqual(tg.telegram_chat_for_email(EMAIL)['chat_id'], '555', 'a forwarded link cannot re-pair')
        self.assertIn('expired or was already used', self.sent[-1][1])

    def test_an_expired_code_pairs_nothing(self):
        code = self.start_link()
        with closing(sqlite3.connect('searchRack.db')) as conn:
            conn.execute('UPDATE telegram_link_codes SET expires_at = ?', (time.time() - 1,))
            conn.commit()
        tg._telegram_handle_link(private_message('/start ' + code))
        self.assertIsNone(tg.telegram_chat_for_email(EMAIL))

    def test_a_new_link_retires_the_last_unused_one(self):
        old = self.start_link()
        new = self.start_link()
        tg._telegram_handle_link(private_message('/start ' + old))
        self.assertIsNone(tg.telegram_chat_for_email(EMAIL))
        tg._telegram_handle_link(private_message('/start ' + new))
        self.assertEqual(tg.telegram_chat_for_email(EMAIL)['chat_id'], '555')

    def test_a_group_chat_cannot_be_linked(self):
        code = self.start_link()
        tg._telegram_handle_link(private_message('/start ' + code, chat_type='group'))
        self.assertIsNone(tg.telegram_chat_for_email(EMAIL))

    def test_plain_commands_are_not_link_attempts(self):
        self.assertFalse(tg._telegram_handle_link(private_message('/start')))
        self.assertFalse(tg._telegram_handle_link(private_message('status')))

    def test_quiet_mode_still_pairs_but_does_not_answer_stale_codes(self):
        code = self.start_link()
        tg._telegram_handle_link(private_message('/start nope-not-a-code-at-all'), quiet=True)
        self.assertEqual(self.sent, [])
        tg._telegram_handle_link(private_message('/start ' + code), quiet=True)
        self.assertEqual(tg.telegram_chat_for_email(EMAIL)['chat_id'], '555')

    def test_unlink_forgets_the_chat(self):
        code = self.start_link()
        tg._telegram_handle_link(private_message('/start ' + code))
        body, _ = self.call(tg.api_telegram_unlink, HEADER)
        self.assertEqual(body['removed'], 1)
        self.assertIsNone(tg.telegram_chat_for_email(EMAIL))

    def test_another_site_cannot_press_link_or_unlink(self):
        headers = {**HEADER, 'Sec-Fetch-Site': 'cross-site'}
        self.assertEqual(self.call(tg.api_telegram_link, headers)[1], 403)
        self.assertEqual(self.call(tg.api_telegram_unlink, headers)[1], 403)

    def test_the_poller_pairs_a_stranger_on_its_very_first_poll(self):
        """The person is not an enabled recipient, and with no saved offset their tap is the first
        message after a restart: both gates used to drop it without a word."""
        code = self.start_link()

        class Stop(BaseException):
            pass

        class Response:
            ok = True
            content = b'1'

            @staticmethod
            def json():
                return {'ok': True, 'result': [{'update_id': 10, 'message': private_message('/start ' + code)}]}

        calls = []

        def fake_get(url, **kwargs):
            calls.append(url)
            if len(calls) > 1:
                raise Stop()
            return Response()

        with patch.object(tg, '_telegram_api_base', lambda: 'https://api.example/botX'), \
                patch.object(tg.requests, 'get', fake_get), patch.object(tg.requests, 'post', lambda *a, **k: None):
            with self.assertRaises(Stop):
                tg.telegram_command_worker()
        self.assertEqual(tg.telegram_chat_for_email(EMAIL)['chat_id'], '555')
        self.assertIn('Linked to', self.sent[-1][1])


if __name__ == '__main__':
    unittest.main()
