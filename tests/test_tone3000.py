import base64
from hashlib import sha256
import json
from pathlib import Path
from queue import Queue
import tempfile
from threading import Event, Thread, Timer
import unittest
from unittest.mock import Mock, call, patch
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlencode, urlsplit
from urllib.request import urlopen

from nam_convert import is_nam_wav, stage_tone_import, validate_import
from test_nam import CONVERTER, capture
from tone3000 import API, ToneError, callback_result, get_tone, prepare_model, read_url, select_tone
from tone_preview import TonePreview, render_card


class ToneTests(unittest.TestCase):
    def test_card_escapes_catalog_text_and_rejects_non_https_cover(self):
        tone = dict(title='<amp>', user={'username': 'a&b'}, gear='amp', license='t3k',
                    images=['javascript:alert(1)'])
        card = render_card(tone, [{'id': 42, 'name': '</option><script>bad()</script>'}])
        self.assertIn('&lt;amp&gt;', card)
        self.assertIn('@a&amp;b', card)
        self.assertIn('value="42">&lt;/option&gt;&lt;script&gt;bad()', card)
        self.assertNotIn('javascript:alert(1)', card)

    def test_callback_rejects_mismatched_state_and_duplicate_parameters(self):
        self.assertEqual(callback_result('/callback?state=no&code=xyz&tone_id=42', 'yes'), (False, None))
        self.assertEqual(callback_result('/callback?state=yes&code=x&code=y&tone_id=42', 'yes'), (False, None))
        self.assertEqual(callback_result('/callback?state=yes&code=x&tone_id=42', 'yes'), (True, ('x', 42)))

    def select(self, reply, read):
        workers = []

        def open_browser(url):
            self.params = parse_qs(urlsplit(url).query)
            redirect = self.params['redirect_uri'][0]
            state = self.params['state'][0]

            def respond():
                try:
                    urlopen(f'{redirect}?state=bad&code=wrong&tone_id=1', timeout=3).close()
                except HTTPError as error:
                    self.status = error.code
                valid_url = f'{redirect}?{urlencode(dict(state=state, **reply))}'
                with urlopen(valid_url, timeout=3) as response:
                    self.result = response.status
                try:
                    urlopen(valid_url, timeout=1).close()
                    self.replayed = True
                except OSError:
                    self.replayed = False

            worker = Thread(target=respond)
            workers.append(worker)
            worker.start()
            return True

        stopped = Event()
        watchdog = Timer(5, stopped.set)
        watchdog.start()
        try:
            with patch('tone3000.read_url', read):
                result = select_tone('t3k_pub_test', stopped, open_browser=open_browser)
        finally:
            watchdog.cancel()
            for worker in workers:
                worker.join(timeout=3)
        self.assertEqual((self.status, self.result), (400, 200))
        self.assertFalse(self.replayed)
        return result

    def test_select_exchanges_only_the_valid_callback_with_pkce(self):
        with patch('tone3000.read_url', return_value=b'{"access_token":"test-token"}') as read:
            self.assertEqual(self.select(dict(code='right', tone_id=42), read), (42, 'test-token'))
            self.assertEqual(read.call_count, 1)
            form = read.call_args.kwargs['form']
        self.assertEqual(self.params['prompt'], ['select_tone'])
        self.assertEqual(self.params['architecture'], ['2'])
        self.assertEqual(form['code'], 'right')
        self.assertEqual(form['redirect_uri'], self.params['redirect_uri'][0])
        challenge = base64.urlsafe_b64encode(sha256(form['code_verifier'].encode()).digest()).rstrip(b'=')
        self.assertEqual(challenge.decode(), self.params['code_challenge'][0])

    def test_cancel_does_not_exchange_code(self):
        with patch('tone3000.read_url') as read:
            self.assertIsNone(self.select(dict(canceled='true', code='unused'), read))
            read.assert_not_called()

    def test_selection_survives_the_old_five_minute_limit(self):
        # Simulate a delay beyond the old deadline without waiting five minutes.
        with patch('tone3000.monotonic', side_effect=[0, 301], create=True), \
                patch('tone3000.read_url', return_value=b'{"access_token":"test-token"}') as read:
            self.assertEqual(self.select(dict(code='right', tone_id=42), read), (42, 'test-token'))

    def test_api_errors_and_untrusted_download_host(self):
        with self.assertRaisesRegex(ToneError, 'unexpected model URL'):
            read_url('https://example.com/file.nam', token='test-token')
        with patch('tone3000.build_opener') as opener:
            opener.return_value.open.side_effect = URLError('offline')
            with self.assertRaisesRegex(ToneError, 'Check your connection'):
                read_url(f'{API}/tones/1', token='test-token')

    def test_lists_a2_models_in_catalog_order(self):
        payloads = [dict(format='nam', gear='amp'),
                    dict(data=[dict(id=1, architecture_version=1), dict(id=3, architecture_version=2)], total_pages=2),
                    dict(data=[dict(id=2, architecture_version=2)], total_pages=2)]
        with patch('tone3000.read_url', side_effect=map(json.dumps, payloads)) as read:
            _, models = get_tone(42, 'test-token')
        self.assertEqual([model['id'] for model in models], [3, 2])
        self.assertIn('architecture=2', read.call_args.args[0])

    def test_browse_saves_only_the_current_trial_to_an_offered_slot(self):
        browser = Mock()
        browser.actions = Queue()
        browser.next_action.side_effect = lambda: browser.actions.get(timeout=3)
        browser.is_set.return_value = False
        second = Event()
        preview = TonePreview(None, None, None, Event(), None)
        preview.command = Mock(side_effect=[
            {'slot': 2, 'freeSlots': [3, 4]}, {'slot': 2, 'freeSlots': [3, 4]},
            {'savedSlot': 4, 'freeSlots': [3]},
        ])

        selections = iter([(42, 'first'), (43, 'second'), None])

        def select(*_args, **_kwargs):
            selection = next(selections)
            if selection is None:
                raise ToneError('Catalog expired')
            if selection[0] == 43:
                second.wait(timeout=3)
            return selection

        def send(kind, **data):
            if kind == 'status' and not data['busy']:
                if data['trial'] == 1:
                    browser.actions.put('source')
                    second.set()
                elif data['trial'] == 2:
                    browser.actions.put('save:1:4')
                    browser.actions.put('save:2:2')
                    browser.actions.put('save:2:4')
                elif data['text'] == 'Saved in User IR 4.':
                    browser.actions.put('finish')

        browser.send.side_effect = send
        tone = dict(id=42, title='Amp', user={'username': 'creator'}, gear='amp', license='t3k')
        with patch('tone_preview.select_tone', side_effect=select), \
                patch('tone_preview.get_tone', return_value=(tone, [{'id': 1, 'name': 'Amp'}])), \
                patch('tone_preview.prepare_model', side_effect=['first.wav', 'second.wav']):
            self.assertIsNone(preview.browse(browser, 1, 'key'))
        self.assertIn(call('source', url='https://www.tone3000.com/tones/42'), browser.send.call_args_list)
        self.assertEqual(preview.command.call_args_list, [
            call(1, 'audition', file='first.wav'), call(1, 'audition', file='second.wav'), call(1, 'save', slot=4)])

    def test_no_free_slot_lists_the_taken_ones_and_auditions_into_the_chosen_one(self):
        browser = Mock()
        browser.actions = Queue()
        browser.next_action.side_effect = lambda: browser.actions.get(timeout=3)
        browser.is_set.return_value = False
        preview = TonePreview(None, None, None, Event(), None)
        taken = [[7, 'Old amp'], [128, 'Old audition']]
        preview.command = Mock(side_effect=[
            {'error': 'No free User IR slot. Choose one to replace.', 'replace': taken, 'freeSlots': []},
            {'slot': 128, 'freeSlots': []},
        ])
        shown = []

        def send(kind, **data):
            if kind != 'status' or data['busy']:
                return
            shown.append(data['replace'])
            if data['replace']:
                browser.actions.put('replace:9')      # not offered: ignored
                browser.actions.put('replace:128')
            elif data['trial'] == 1:
                browser.actions.put('finish')
                done.set()

        browser.send.side_effect = send
        tone = dict(id=42, title='Amp', user={'username': 'creator'}, gear='amp', license='t3k')
        selections = iter([(42, 'token')])
        done = Event()

        def select(*_args, **_kwargs):                 # the catalog stays open until the test is done
            return next(selections, None) or done.wait(3) and None

        with patch('tone_preview.select_tone', side_effect=select), \
                patch('tone_preview.get_tone', return_value=(tone, [{'id': 1, 'name': 'Amp'}])), \
                patch('tone_preview.prepare_model', return_value='p.wav'):
            preview.browse(browser, 1, 'key')
        self.assertEqual(preview.command.call_args_list, [
            call(1, 'audition', file='p.wav'), call(1, 'audition', file='p.wav', replace=128)])
        self.assertEqual(shown[0], taken)
        self.assertEqual(shown[-1], [])

    def test_a_converting_tile_waits_for_the_profile_pick(self):
        browser = Mock()
        browser.actions = Queue()
        browser.next_action.side_effect = lambda: browser.actions.get(timeout=3)
        browser.is_set.return_value = False
        preview = TonePreview(None, None, None, Event(), None)
        preview.tile = {'block': 'NAM Pedal', 'modes': ['pedal']}
        preview.command = Mock(return_value={'slot': 127, 'freeSlots': []})
        done = Event()
        selections = iter([(42, 'token')])

        def select(*_args, **_kwargs):
            return next(selections, None) or done.wait(3) and None

        def send(kind, **data):
            if kind != 'status' or data['busy']:
                return
            if data['text'].startswith('2 profiles'):
                self.assertEqual(data['model'], -1)
                browser.actions.put('model:2')
            elif data['trial'] == 1:
                browser.actions.put('finish')
                done.set()

        browser.send.side_effect = send
        tone = dict(id=42, title='Drive', user={'username': 'creator'}, gear='pedal', license='t3k')
        models = [{'id': 1, 'name': 'G3'}, {'id': 2, 'name': 'G9'}]
        with patch('tone_preview.select_tone', side_effect=select), \
                patch('tone_preview.get_tone', return_value=(tone, models)), \
                patch('tone_preview.prepare_model', return_value='p.wav') as prepare:
            preview.browse(browser, 1, 'key')
        self.assertEqual([c.args[0]['id'] for c in prepare.call_args_list], [2])     # only the picked one
        self.assertEqual(preview.command.call_args_list, [call(1, 'audition', file='p.wav')])

    def test_a_profile_from_tone3000_finds_its_tone_page_again(self):
        import tone3000
        tone = {'id': 7, 'url': 'https://www.tone3000.com/tones/jhs-morning-glory-7'}
        tone3000.remember_link(80474, tone3000.tone_url(tone))
        self.assertEqual(tone3000.tone_link('JHS MG Red G9 PED 80474'), tone['url'])
        self.assertIsNone(tone3000.tone_link('JHS MG Red G9 PED 80475'))
        self.assertIsNone(tone3000.tone_link('My own capture'))
        self.assertIsNone(tone3000.tone_link(''))
        self.assertEqual(tone3000.tone_url({'id': 7, 'url': 'https://evil.example/x'}),
                         'https://www.tone3000.com/tones/7')
        tone3000.LINKS.write_text('{"5": "https://evil.example/x"}', encoding='utf-8')
        self.assertIsNone(tone3000.tone_link('Amp 5'))

    def test_saved_names_fit_the_slot_and_are_safe_for_windows(self):
        with tempfile.TemporaryDirectory() as folder, patch('tone3000.read_url', return_value=b'capture'), \
                patch('tone3000.convert_batch', return_value=['profile.wav']) as convert:
            for label, model_id, expected in (
                ('A' * 40, 123456789, 'A' * 21 + ' 123456789'),
                ('Чистый тон', 42, 'T3K 42'),
                ('CON.clean', 42, '_CON.clean 42'),
            ):
                with self.subTest(label=label):
                    model = dict(id=model_id, name=label, model_url=f'{API}/models/{model_id}/download')
                    prepare_model(model, 'test-token', folder, CONVERTER)
                    self.assertEqual(convert.call_args.args[0][0].stem, expected)

    def test_a_converted_mode_keeps_the_id_last_and_its_tag_before_it(self):
        model = dict(id=123456789, name='A' * 40, model_url=f'{API}/models/123456789/download')
        with tempfile.TemporaryDirectory() as folder, patch('tone3000.read_url', return_value=b'capture'), \
                patch('tone3000.convert_batch', side_effect=lambda files, *a: [str(files[0])]) as convert:
            def fake_train(capture, mode, workspace, converter):
                tag = ' PED ' if mode == 'pedal' else ' L16 '
                self.assertEqual(capture.stem, 'A' * 17 + tag + '123456789')
                out = Path(workspace, 'trained.wav' if mode == 'pedal' else 'trained.nam')
                out.write_bytes(b'x')
                return out
            for mode, stem, suffix in (('pedal', 'A' * 17 + ' PED 123456789', '.wav'),
                                       ('16-quick', 'A' * 17 + ' L16 123456789', '.nam')):
                with self.subTest(mode=mode):
                    out = Path(prepare_model(model, 'test-token', folder, CONVERTER, mode, train=fake_train))
                    self.assertEqual((out.stem, out.suffix), (stem, suffix))
                    self.assertLessEqual(len(out.stem), 31)
            self.assertTrue(Path(folder, 'captures', 'A' * 17 + ' PED 123456789.nam').is_file())  # for the library
            with self.assertRaisesRegex(ToneError, 'cancelled'):
                prepare_model(model, 'test-token', folder, CONVERTER, 'pedal', train=lambda *a: None)
            self.assertEqual(convert.call_count, 2)

    @unittest.skipUnless(CONVERTER.is_file(), 'requires the sibling helix-lt-nam checkout')
    def test_prepares_only_selected_profile_and_rejects_incompatible_data(self):
        model = dict(id=42, name='../5150III®: amp', model_url=f'{API}/models/42/download')
        with tempfile.TemporaryDirectory() as folder, patch('tone3000.read_url') as read:
            read.return_value = json.dumps(capture()).encode()
            wav = Path(prepare_model(model, 'test-token', folder, CONVERTER))
            self.assertTrue(is_nam_wav(wav))
            self.assertEqual(wav.stem, '_5150III_ amp 42')
            self.assertTrue(wav.name.isascii())
            read.assert_called_once_with(model['model_url'], token='test-token')
            with patch('nam_convert.secrets.token_hex', side_effect=['1a2b3c', '4d5e6f']):
                audition = stage_tone_import(wav, folder, audition=True)
                repeated = stage_tone_import(wav, folder, audition=True)
            self.assertEqual(audition.stem, f'{wav.stem} 1a2b3c')
            self.assertNotEqual(audition.stem, repeated.stem)
            audition.unlink()  # HX Edit consumes staged files during import.
            saved = stage_tone_import(wav, folder)
            self.assertNotEqual(audition, saved)
            self.assertEqual(saved.name, wav.name)
            self.assertEqual(saved.read_bytes(), wav.read_bytes())
            with self.assertRaisesRegex(ValueError, 'already occupied'):
                validate_import([wav], 1, set(range(1, 129)), nam_only=True)
            read.return_value = b'{}'
            with self.assertRaisesRegex(ValueError, 'no 3-channel WaveNet'):
                prepare_model(model, 'test-token', folder, CONVERTER)
