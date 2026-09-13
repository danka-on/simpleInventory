"""Offline voice-note tests: isolated recordings and DB, no paid API calls."""
from pathlib import Path
from contextlib import closing
import os
import sqlite3
import tempfile
import unittest
from unittest.mock import Mock, patch

from flask import Flask
import requests
from voice_note_routes import register, VoiceError


class VoiceNoteTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / 'static/items_prep').mkdir(parents=True)
        self.audio = self.root / 'static/items_prep/note.webm'
        self.audio.write_bytes(b'test recording')
        with closing(sqlite3.connect(self.root / 'bol.db')) as conn:
            conn.execute('''CREATE TABLE items_prep_media (id INTEGER PRIMARY KEY, upc TEXT,
                row_status TEXT, file_path TEXT, media_type TEXT, created_at TEXT)''')
            conn.execute("INSERT INTO items_prep_media VALUES (1,'123-1','GOOD','items_prep/note.webm','audio','2026-09-13')")
            conn.execute("INSERT INTO items_prep_media VALUES (2,'123-2','GOOD','items_prep/note.webm','audio','2026-09-13')")
            conn.commit()
        app = Flask(__name__, static_folder=str(self.root / 'static'))
        self.service = register(app, self.root)
        self.client = app.test_client()
        self.url = '/api/warehouse/voice-notes/1/analysis'
        self.env = patch.dict(os.environ, {'OPENAI_API_KEY': 'test-openai', 'ANTHROPIC_API_KEY': 'test-claude'})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.network = patch('voice_note_routes.requests.post')
        self.post = self.network.start()
        self.addCleanup(self.network.stop)
        self.post.side_effect = [self.response({'text': 'Dėžė pažeista. Trūksta dviejų dalių.'}),
            self.response({'stop_reason': 'end_turn', 'content': [{'type': 'text',
                'text': 'The box is damaged. Two parts are missing.'}]})]

    @staticmethod
    def response(data, status=200):
        return Mock(status_code=status, json=Mock(return_value=data))

    def sql(self, query, args=()):
        with closing(sqlite3.connect(self.root / 'bol.db')) as conn:
            rows = conn.execute(query, args).fetchall()
            conn.commit()
            return rows

    def test_get_is_manual_and_does_not_call_providers(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json['analysis']['status'], 'pending')
        self.post.assert_not_called()

    def test_transcribe_translate_persist_and_reuse(self):
        response = self.client.post(self.url)
        self.assertEqual(response.status_code, 200, response.json)
        result = response.json['analysis']
        self.assertEqual(result['lithuanian'], 'Dėžė pažeista. Trūksta dviejų dalių.')
        self.assertEqual(result['english'], 'The box is damaged. Two parts are missing.')
        self.assertEqual(result['status'], 'complete')
        first, second = self.post.call_args_list
        self.assertEqual(first.args[0], 'https://api.openai.com/v1/audio/transcriptions')
        self.assertEqual(first.kwargs['data']['language'], 'lt')
        self.assertEqual(first.kwargs['files']['file'][1], b'test recording')
        self.assertEqual(second.kwargs['json']['messages'][0]['content'], result['lithuanian'])
        self.assertEqual(self.client.get(self.url).json['analysis'], result)
        self.assertEqual(self.client.post(self.url).json['analysis'], result)
        self.assertEqual(self.post.call_count, 2)
        self.assertEqual(self.sql('SELECT COUNT(*) FROM items_prep_media')[0][0], 2)

    def test_sibling_recordings_do_not_share_analysis(self):
        self.client.post(self.url)
        sibling = self.client.get('/api/warehouse/voice-notes/2/analysis').json['analysis']
        self.assertEqual(sibling['lithuanian'], '')
        self.assertEqual(sibling['english'], '')

    def test_missing_key_fails_before_network(self):
        with patch.dict(os.environ, {'OPENAI_API_KEY': ''}):
            response = self.client.post(self.url)
        self.assertEqual(response.status_code, 503)
        self.assertIn('OPENAI_API_KEY', response.json['error'])
        self.post.assert_not_called()

    def test_translation_retry_reuses_original_transcript(self):
        self.post.side_effect = [self.response({'text': 'Neveikia.'}), requests.Timeout('secret token')]
        self.assertEqual(self.client.post(self.url).status_code, 502)
        saved = self.client.get(self.url).json['analysis']
        self.assertEqual(saved['lithuanian'], 'Neveikia.')
        self.assertEqual(saved['english'], '')
        self.assertNotIn('secret', saved['error'])
        self.post.side_effect = [self.response({'stop_reason': 'end_turn',
            'content': [{'type': 'text', 'text': 'It does not work.'}]})]
        with patch.dict(os.environ, {'OPENAI_API_KEY': ''}):
            self.assertEqual(self.client.post(self.url).status_code, 200)
        self.assertEqual(self.post.call_count, 3)
        self.assertIn('anthropic.com', self.post.call_args.args[0])

    def test_concurrent_click_is_rejected_without_duplicate_provider_call(self):
        def during_transcription(path, audio):
            self.assertEqual(self.client.get(self.url).json['analysis']['status'], 'processing')
            self.assertEqual(self.client.post(self.url).status_code, 409)
            return 'Pastaba.'
        with patch.object(self.service, 'transcribe', side_effect=during_transcription), \
             patch.object(self.service, 'translate', return_value='A note.'):
            self.assertEqual(self.client.post(self.url).status_code, 200)
        self.post.assert_not_called()

    def test_expired_claim_can_be_retried(self):
        with self.service.connection() as conn:
            _, _, fingerprint = self.service.source(conn, 1)
            conn.execute('INSERT INTO voice_note_analysis (media_id, fingerprint, lease_until, token) VALUES (?,?,1,?)',
                         (1, fingerprint, 'abandoned'))
        self.assertEqual(self.client.post(self.url).status_code, 200)

    def test_changed_file_invalidates_saved_text(self):
        self.client.post(self.url)
        self.audio.write_bytes(b'different recording')
        self.assertEqual(self.client.get(self.url).json['analysis']['status'], 'pending')
        self.assertEqual(self.client.get(self.url).json['analysis']['lithuanian'], '')

    def test_replacement_during_analysis_does_not_get_stale_results(self):
        def replace_file(transcript):
            self.audio.write_bytes(b'replaced while analyzing')
            return 'Old translation'
        with patch.object(self.service, 'translate', side_effect=replace_file):
            self.assertEqual(self.client.post(self.url).status_code, 409)
        self.assertEqual(self.client.get(self.url).json['analysis']['english'], '')

    def test_deleted_note_does_not_return_saved_analysis(self):
        self.client.post(self.url)
        self.sql('DELETE FROM items_prep_media WHERE id=1')
        self.assertEqual(self.client.get(self.url).status_code, 404)

    def test_non_audio_and_missing_recordings_are_rejected(self):
        self.sql("UPDATE items_prep_media SET media_type='video' WHERE id=1")
        self.assertEqual(self.client.post(self.url).status_code, 400)
        self.sql("UPDATE items_prep_media SET media_type='audio' WHERE id=1")
        self.audio.unlink()
        self.assertEqual(self.client.post(self.url).status_code, 404)
        self.post.assert_not_called()

    def test_path_escape_and_remote_urls_are_rejected(self):
        for path in ('../secret.webm', 'https://example.com/recording.webm'):
            self.sql('UPDATE items_prep_media SET file_path=? WHERE id=1', (path,))
            self.assertEqual(self.client.post(self.url).status_code, 400)
        self.post.assert_not_called()

    def test_empty_oversized_and_unsupported_audio_rejected(self):
        with patch('voice_note_routes.MAX_AUDIO_BYTES', 2):
            self.assertEqual(self.client.post(self.url).status_code, 413)
        self.audio.write_bytes(b'')
        self.assertEqual(self.client.post(self.url).status_code, 413)
        other = self.audio.with_suffix('.txt')
        other.write_text('not audio')
        self.sql("UPDATE items_prep_media SET file_path='items_prep/note.txt' WHERE id=1")
        self.assertEqual(self.client.post(self.url).status_code, 415)
        self.post.assert_not_called()

    def test_empty_transcription_and_truncated_translation_rejected(self):
        self.post.side_effect = [self.response({'text': ''})]
        self.assertEqual(self.client.post(self.url).status_code, 422)
        self.post.side_effect = [self.response({'text': 'Pastaba.'}),
            self.response({'stop_reason': 'max_tokens', 'content': [{'type': 'text', 'text': 'Partial'}]})]
        self.assertEqual(self.client.post(self.url).status_code, 502)
        self.assertEqual(self.client.get(self.url).json['analysis']['english'], '')

    def test_provider_errors_do_not_leak_response_content(self):
        self.post.side_effect = [self.response({'error': 'SECRET'}, status=401)]
        response = self.client.post(self.url)
        self.assertEqual(response.status_code, 502)
        self.assertIn('401', response.json['error'])
        self.assertNotIn('SECRET', response.get_data(as_text=True))

    def test_cross_site_post_rejected(self):
        self.assertEqual(self.client.post(self.url, headers={'Sec-Fetch-Site': 'cross-site'}).status_code, 403)
        self.post.assert_not_called()


if __name__ == '__main__':
    unittest.main()
