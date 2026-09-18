"""Offline voice-note tests: isolated recordings and DB, no paid API calls."""
from pathlib import Path
from contextlib import closing
import io
import os
import sqlite3
import tempfile
import unittest
from unittest.mock import Mock, patch

from flask import Flask
import requests
from voice_note_routes import register, VoiceError, VoiceNotes, LITHUANIAN_AUDIO_HINT


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

    def test_vocabulary_and_quantity_guidance_reach_providers(self):
        self.client.post(self.url)
        speech, translation = self.post.call_args_list
        self.assertEqual(speech.kwargs['data']['prompt'], LITHUANIAN_AUDIO_HINT)
        for word in ('Trūksta', 'šaukšto', 'lėkštės', 'puodelio', 'Didelis', 'mažas', 'Yra tik trys'):
            self.assertIn(word, speech.kwargs['data']['prompt'])
        guidance = translation.kwargs['json']['system']
        for phrase in ('truksta', 'ira tik', 'there are only three', 'N missing', 'Do not blindly replace rust'):
            self.assertIn(phrase, guidance)

    def test_manual_reanalysis_uses_audio_again(self):
        self.client.post(self.url)
        self.post.side_effect = [self.response({'text':'Yra tik trys.'}),
            self.response({'stop_reason':'end_turn', 'content':[{'type':'text','text':'There are only three.'}]})]
        result = self.client.post(self.url, json={'reanalyze':True})
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.json['analysis']['lithuanian'], 'Yra tik trys.')
        self.assertEqual(self.post.call_count, 4)

    def test_failed_reanalysis_preserves_previous_result(self):
        original = self.client.post(self.url).json['analysis']
        self.post.side_effect = requests.Timeout('secret')
        self.assertEqual(self.client.post(self.url, json={'reanalyze':True}).status_code, 502)
        saved = self.client.get(self.url).json['analysis']
        self.assertEqual(saved['english'], original['english'])
        self.assertEqual(saved['lithuanian'], original['lithuanian'])
        self.assertNotIn('secret', saved['error'])

    def test_invalid_reanalysis_flag_rejected(self):
        self.assertEqual(self.client.post(self.url, json={'reanalyze':'false'}).status_code, 400)
        self.post.assert_not_called()


class WrittenNoteTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        app = Flask(__name__, static_folder=str(self.root/'static'))
        self.voice = register(app, self.root)
        self.client = app.test_client()
        with closing(sqlite3.connect(self.root/'bol.db')) as conn:
            conn.executescript('''CREATE TABLE items_prep_notes (id INTEGER PRIMARY KEY,upc TEXT,note TEXT);
                INSERT INTO items_prep_notes VALUES (1,'123-1','ira tik trys mazi saukstai');
                INSERT INTO items_prep_notes VALUES (2,'123-2','truksta dideles lekstes');
                CREATE TABLE items_prep_status (id INTEGER PRIMARY KEY,upc TEXT,note TEXT,reason TEXT);
                INSERT INTO items_prep_status VALUES (1,'123-1','ira tik 7 puodeliai','truksta lekstes');''')
        with closing(sqlite3.connect(self.root/'searchRack.db')) as conn:
            conn.executescript('''CREATE TABLE SEARCHRACK (ID INTEGER PRIMARY KEY,BARCODE TEXT,WAREHOUSE_NOTE TEXT);
                INSERT INTO SEARCHRACK VALUES (1,'123-1','There is rust on the spoon.');''')
        self.env = patch.dict(os.environ, {'ANTHROPIC_API_KEY':'test-claude','OPENAI_API_KEY':''})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.network = patch('voice_note_routes.requests.post', return_value=VoiceNoteTests.response({
            'stop_reason':'end_turn','content':[{'type':'text','text':'There are only three small spoons.'}]}))
        self.post = self.network.start()
        self.addCleanup(self.network.stop)
        self.url = '/api/warehouse/written-notes/prep-note/1/translation'

    def test_manual_translation_preserves_source_and_cached_reload(self):
        self.assertEqual(self.client.get(self.url).json['translation']['status'], 'pending')
        self.post.assert_not_called()
        body = {'original':'ira tik trys mazi saukstai'}
        response = self.client.post(self.url, json=body)
        self.assertEqual(response.status_code, 200, response.json)
        self.assertEqual(response.json['translation']['original'], body['original'])
        self.assertEqual(response.json['translation']['english'], 'There are only three small spoons.')
        self.assertEqual(self.client.get(self.url).json, response.json)
        self.assertEqual(self.client.post(self.url, json=body).json, response.json)
        self.assertEqual(self.post.call_count, 1)
        self.assertIn('anthropic.com', self.post.call_args.args[0])
        guidance = self.post.call_args.kwargs['json']['system']
        self.assertIn('assume Lithuanian', guidance)
        self.assertIn('already English, return it unchanged', guidance)
        self.assertNotIn('mishears', guidance)
        with closing(sqlite3.connect(self.root/'bol.db')) as conn:
            self.assertEqual(conn.execute('SELECT note FROM items_prep_notes WHERE id=1').fetchone()[0], body['original'])

    def test_all_written_sources_and_ids_stay_separate(self):
        for kind, text in [('prep-reason','truksta lekstes'),('prep-status-note','ira tik 7 puodeliai'),
                           ('warehouse','There is rust on the spoon.')]:
            url = f'/api/warehouse/written-notes/{kind}/1/translation'
            self.assertEqual(self.client.post(url,json={'original':text}).status_code,200)
        self.assertEqual(self.client.get(self.url).json['translation']['english'], '')
        self.assertEqual(self.client.get(self.url.replace('/1/','/2/')).json['translation']['english'], '')

    def test_stale_browser_text_and_changed_database_are_rejected(self):
        self.assertEqual(self.client.post(self.url,json={'original':'old note'}).status_code,409)
        self.post.assert_not_called()
        self.client.post(self.url,json={'original':'ira tik trys mazi saukstai'})
        with closing(sqlite3.connect(self.root/'bol.db')) as conn:
            conn.execute("UPDATE items_prep_notes SET note='ira tik 5' WHERE id=1")
            conn.commit()
        self.assertEqual(self.client.get(self.url).json['translation']['english'], '')

    def test_edit_during_translation_cannot_save_old_result(self):
        def edit(*args, **kwargs):
            with closing(sqlite3.connect(self.root/'bol.db')) as conn:
                conn.execute("UPDATE items_prep_notes SET note='truksta puodelio' WHERE id=1")
                conn.commit()
            return 'Wrong old result'
        with patch.object(self.voice,'translate',side_effect=edit):
            response = self.client.post(self.url,json={'original':'ira tik trys mazi saukstai'})
        self.assertEqual(response.status_code,409)
        self.assertEqual(self.client.get(self.url).json['translation']['english'],'')

    def test_concurrent_translation_and_provider_failure_retry(self):
        def nested(*args, **kwargs):
            self.assertEqual(self.client.post(self.url,json={'original':args[0]}).status_code,409)
            raise requests.Timeout('secret')
        with patch.object(self.voice,'translate',side_effect=nested):
            response = self.client.post(self.url,json={'original':'ira tik trys mazi saukstai'})
        self.assertEqual(response.status_code,502)
        self.assertNotIn('secret',response.get_data(as_text=True))
        self.assertEqual(self.client.post(self.url,json={'original':'ira tik trys mazi saukstai'}).status_code,200)

    def test_unknown_missing_empty_and_invalid_request_rejected(self):
        self.assertEqual(self.client.get(self.url.replace('prep-note','secret')).status_code,400)
        self.assertEqual(self.client.get(self.url.replace('/1/','/99/')).status_code,404)
        self.assertEqual(self.client.post(self.url,json=[]).status_code,400)
        self.assertEqual(self.client.post(self.url,json={'original':'x'},headers={'Sec-Fetch-Site':'cross-site'}).status_code,403)
        with closing(sqlite3.connect(self.root/'bol.db')) as conn:
            conn.execute("UPDATE items_prep_notes SET note='' WHERE id=1")
            conn.commit()
        self.assertEqual(self.client.get(self.url).status_code,404)
        self.post.assert_not_called()


class NameDictationTests(unittest.TestCase):
    """Receiving-screen dictation: one short clip in, one cleaned item name out."""

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        (root / 'static').mkdir()
        app = Flask(__name__, static_folder=str(root / 'static'))
        register(app, root)
        self.client = app.test_client()
        env = patch.dict(os.environ, {'OPENAI_API_KEY': 'test-openai'})
        env.start()
        self.addCleanup(env.stop)
        network = patch('voice_note_routes.requests.post')
        self.post = network.start()
        self.addCleanup(network.stop)

    def dictate(self, filename='name.webm', audio=b'spoken name', headers=None, kind=None):
        data = {'audio': (io.BytesIO(audio), filename)}
        if kind is not None:
            data['kind'] = kind
        return self.client.post('/api/warehouse/name-dictation', headers=headers or {},
                                data=data, content_type='multipart/form-data')

    def test_label_read_aloud_becomes_a_clean_name(self):
        self.post.return_value = VoiceNoteTests.response(
            {'text': ' "Lenox Tuscany Classics wine glasses, set of 4,  clear." '})
        response = self.dictate(filename='name.mp4')
        self.assertEqual(response.status_code, 200, response.json)
        self.assertEqual(response.json['text'], 'Lenox Tuscany Classics wine glasses, set of 4, clear')
        self.assertEqual(response.headers['Cache-Control'], 'no-store')
        call = self.post.call_args
        self.assertEqual(call.args[0], 'https://api.openai.com/v1/audio/transcriptions')
        self.assertEqual(call.kwargs['data']['model'], 'gpt-4o-transcribe')
        self.assertEqual(call.kwargs['data']['language'], 'en')
        self.assertEqual(call.kwargs['files']['file'][:2], ('name.mp4', b'spoken name'))

    def test_refused_newer_model_falls_back_to_whisper(self):
        self.post.side_effect = [VoiceNoteTests.response({}, 404),
                                 VoiceNoteTests.response({'text': 'Zwilling fry pan'})]
        self.assertEqual(self.dictate().json['text'], 'Zwilling fry pan')
        self.assertEqual([c.kwargs['data']['model'] for c in self.post.call_args_list],
                         ['gpt-4o-transcribe', 'whisper-1'])

    def test_quota_errors_are_not_retried_on_another_model(self):
        self.post.return_value = VoiceNoteTests.response({}, 429)
        self.assertEqual(self.dictate().status_code, 502)
        self.assertEqual(self.post.call_count, 1)

    def test_silence_is_not_returned_as_a_name(self):
        for heard in ('', '  ', 'Thank you.', 'you'):
            self.post.return_value = VoiceNoteTests.response({'text': heard})
            self.assertEqual(self.dictate().status_code, 422, heard)

    def test_bad_requests_fail_before_network(self):
        self.assertEqual(self.dictate(filename='name.txt').status_code, 415)
        self.assertEqual(self.dictate(audio=b'').status_code, 413)
        self.assertEqual(self.dictate(audio=b'x' * 5_000_001).status_code, 413)
        self.assertEqual(self.client.post('/api/warehouse/name-dictation').status_code, 400)
        self.assertEqual(self.dictate(headers={'Sec-Fetch-Site': 'cross-site'}).status_code, 403)
        with patch.dict(os.environ, {'OPENAI_API_KEY': ''}):
            response = self.dictate()
        self.assertEqual(response.status_code, 503)
        self.assertIn('OPENAI_API_KEY', response.json['error'])
        self.post.assert_not_called()

    def test_unreachable_service_reports_without_leaking_details(self):
        self.post.side_effect = requests.Timeout('secret token')
        response = self.dictate()
        self.assertEqual(response.status_code, 504)
        self.assertNotIn('secret', response.get_data(as_text=True))

    def test_note_keeps_the_spoken_language_and_punctuation(self):
        self.post.return_value = VoiceNoteTests.response({'text': ' Trūksta dviejų šaukštų.  Dėžė pažeista. '})
        response = self.dictate(filename='note.webm', kind='note')
        self.assertEqual(response.status_code, 200, response.json)
        self.assertEqual(response.json['text'], 'Trūksta dviejų šaukštų. Dėžė pažeista.')
        call = self.post.call_args
        self.assertNotIn('language', call.kwargs['data'])
        self.assertNotIn('prompt', call.kwargs['data'])
        self.assertEqual(call.kwargs['files']['file'][0], 'note.webm')

    def test_notes_may_be_long_but_names_are_capped(self):
        self.post.return_value = VoiceNoteTests.response({'text': 'x' * 2500})
        self.assertEqual(len(self.dictate(kind='note').json['text']), 2000)
        self.assertEqual(len(self.dictate(kind='name').json['text']), 200)

    def test_english_flag_translates_a_lithuanian_note(self):
        self.post.side_effect = [
            VoiceNoteTests.response({'text': 'Trūksta dviejų šaukštų. Dėžė pažeista.'}),
            VoiceNoteTests.response({'stop_reason': 'end_turn', 'content': [
                {'type': 'text', 'text': 'Two spoons are missing. The box is damaged.'}]})]
        with patch.dict(os.environ, {'ANTHROPIC_API_KEY': 'test-claude'}):
            response = self.client.post('/api/warehouse/name-dictation', content_type='multipart/form-data', data={
                'audio': (io.BytesIO(b'note'), 'note.webm'), 'kind': 'note', 'english': '1'})
        self.assertEqual(response.status_code, 200, response.json)
        self.assertEqual(response.json['text'], 'Two spoons are missing. The box is damaged.')
        self.assertEqual(response.json['warning'], '')
        claude = self.post.call_args_list[1]
        self.assertEqual(claude.args[0], 'https://api.anthropic.com/v1/messages')
        self.assertEqual(claude.kwargs['json']['messages'][0]['content'], 'Trūksta dviejų šaukštų. Dėžė pažeista.')

    def test_english_flag_keeps_the_note_when_translation_fails(self):
        self.post.side_effect = [VoiceNoteTests.response({'text': 'Dėžė pažeista.'}),
                                 VoiceNoteTests.response({}, 500)]
        with patch.dict(os.environ, {'ANTHROPIC_API_KEY': 'test-claude'}):
            response = self.client.post('/api/warehouse/name-dictation', content_type='multipart/form-data', data={
                'audio': (io.BytesIO(b'note'), 'note.webm'), 'kind': 'note', 'english': '1'})
        self.assertEqual(response.status_code, 200, response.json)
        self.assertEqual(response.json['text'], 'Dėžė pažeista.')
        self.assertIn('Not translated', response.json['warning'])

    def test_english_flag_is_ignored_for_names(self):
        self.post.return_value = VoiceNoteTests.response({'text': 'Zwilling fry pan'})
        with patch.dict(os.environ, {'ANTHROPIC_API_KEY': 'test-claude'}):
            response = self.client.post('/api/warehouse/name-dictation', content_type='multipart/form-data', data={
                'audio': (io.BytesIO(b'name'), 'name.webm'), 'kind': 'name', 'english': '1'})
        self.assertEqual(response.json['text'], 'Zwilling fry pan')
        self.assertEqual(self.post.call_count, 1)

    def test_silent_note_and_unknown_kind_are_rejected(self):
        self.post.return_value = VoiceNoteTests.response({'text': 'Ačiū.'})
        response = self.dictate(kind='note')
        self.assertEqual(response.status_code, 422)
        self.assertIn('the note', response.json['error'])
        self.post.reset_mock()
        self.assertEqual(self.dictate(kind='essay').status_code, 400)
        self.post.assert_not_called()


class VoiceClipTests(unittest.TestCase):
    """Item Prep voice notes: Lithuanian transcript and English translation before saving."""

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        (root / 'static').mkdir()
        app = Flask(__name__, static_folder=str(root / 'static'))
        register(app, root)
        self.client = app.test_client()
        env = patch.dict(os.environ, {'OPENAI_API_KEY': 'test-openai', 'ANTHROPIC_API_KEY': 'test-claude'})
        env.start()
        self.addCleanup(env.stop)
        network = patch('voice_note_routes.requests.post')
        self.post = network.start()
        self.addCleanup(network.stop)
        self.transcript = VoiceNoteTests.response({'text': 'Trūksta dviejų šaukštų.'})
        self.translation = VoiceNoteTests.response({'stop_reason': 'end_turn', 'content': [
            {'type': 'text', 'text': 'Two spoons are missing.'}]})

    def send(self, filename='prep_voice_1.m4a', audio=b'lithuanian audio', headers=None):
        return self.client.post('/api/warehouse/voice-notes/transcribe', headers=headers or {},
                                data={'audio': (io.BytesIO(audio), filename)},
                                content_type='multipart/form-data')

    def test_returns_lithuanian_and_english(self):
        self.post.side_effect = [self.transcript, self.translation]
        response = self.send()
        self.assertEqual(response.status_code, 200, response.json)
        self.assertEqual((response.json['lithuanian'], response.json['english'], response.json['warning']),
                         ('Trūksta dviejų šaukštų.', 'Two spoons are missing.', ''))
        self.assertEqual(response.headers['Cache-Control'], 'no-store')
        first, second = self.post.call_args_list
        self.assertEqual(first.kwargs['data']['model'], 'whisper-1')
        self.assertEqual(first.kwargs['data']['language'], 'lt')
        self.assertEqual(first.kwargs['files']['file'][:2], ('voice-note.m4a', b'lithuanian audio'))
        self.assertEqual(second.kwargs['json']['messages'][0]['content'], 'Trūksta dviejų šaukštų.')

    def test_failed_translation_still_returns_the_lithuanian(self):
        self.post.side_effect = [self.transcript, requests.Timeout('secret token')]
        response = self.send()
        self.assertEqual(response.status_code, 200, response.json)
        self.assertEqual(response.json['lithuanian'], 'Trūksta dviejų šaukštų.')
        self.assertEqual(response.json['english'], '')
        self.assertTrue(response.json['warning'])
        self.assertNotIn('secret', response.get_data(as_text=True))

    def test_missing_translation_key_warns_without_calling_claude(self):
        self.post.return_value = self.transcript
        with patch.dict(os.environ, {'ANTHROPIC_API_KEY': ''}):
            response = self.send()
        self.assertEqual(response.status_code, 200, response.json)
        self.assertIn('ANTHROPIC_API_KEY', response.json['warning'])
        self.assertEqual(self.post.call_count, 1)

    def test_bad_requests_fail_before_network(self):
        self.assertEqual(self.send(filename='note.txt').status_code, 415)
        self.assertEqual(self.send(audio=b'').status_code, 413)
        self.assertEqual(self.client.post('/api/warehouse/voice-notes/transcribe').status_code, 400)
        self.assertEqual(self.send(headers={'Sec-Fetch-Site': 'cross-site'}).status_code, 403)
        with patch.dict(os.environ, {'OPENAI_API_KEY': ''}):
            response = self.send()
        self.assertEqual(response.status_code, 503)
        self.assertIn('OPENAI_API_KEY', response.json['error'])
        self.post.assert_not_called()

    def test_english_speech_is_returned_unchanged_not_explained(self):
        spoken = 'Lenox wine glasses, set of four, clear.'
        self.post.side_effect = [VoiceNoteTests.response({'text': spoken}),
                                 VoiceNoteTests.response({'stop_reason': 'end_turn', 'content': [
                                     {'type': 'text', 'text': spoken}]})]
        response = self.send()
        self.assertEqual((response.json['lithuanian'], response.json['english']), (spoken, spoken))
        self.assertIn('already English, return it unchanged', self.post.call_args_list[1].kwargs['json']['system'])

    def test_a_closing_note_about_the_language_is_dropped(self):
        spoken = 'Lenox wine glasses, set of four, clear.'
        noted = spoken + chr(10) * 2 + '(This is already in English, so it is returned unchanged.)'
        self.post.side_effect = [VoiceNoteTests.response({'text': spoken}),
                                 VoiceNoteTests.response({'stop_reason': 'end_turn', 'content': [
                                     {'type': 'text', 'text': noted}]})]
        self.assertEqual(self.send().json['english'], spoken)
        kept = 'Two spoons are missing.' + chr(10) + '(Box corner crushed.)'
        self.assertEqual(VoiceNotes.drop_language_note(kept), kept)

    def test_unreachable_speech_service_reports_without_details(self):
        self.post.side_effect = requests.ConnectionError('secret token')
        response = self.send()
        self.assertEqual(response.status_code, 504)
        self.assertNotIn('secret', response.get_data(as_text=True))


if __name__ == '__main__':
    unittest.main()
