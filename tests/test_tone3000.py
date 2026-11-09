import base64
from hashlib import sha256
import json
from pathlib import Path
from queue import Queue
import tempfile
from threading import Event, Thread
import unittest
from unittest.mock import Mock, call, patch
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlencode, urlsplit
from urllib.request import urlopen

from nam_convert import is_nam_wav, validate_import
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

        with patch('tone3000.read_url', read):
            result = select_tone('t3k_pub_test', Event(), open_browser=open_browser, timeout=5)
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

    @unittest.skipUnless(CONVERTER.is_file(), 'requires the sibling helix-lt-nam checkout')
    def test_prepares_only_selected_profile_and_rejects_incompatible_data(self):
        model = dict(id=42, name='../5150III®: amp', model_url=f'{API}/models/42/download')
        with tempfile.TemporaryDirectory() as folder, patch('tone3000.read_url') as read:
            read.return_value = json.dumps(capture()).encode()
            wav = Path(prepare_model(model, 'test-token', folder, CONVERTER))
            self.assertTrue(is_nam_wav(wav))
            self.assertTrue(wav.name.startswith('T3K 42 '))
            self.assertTrue(wav.name.isascii())
            self.assertTrue(wav.stem.endswith('_5150III_ amp'))
            read.assert_called_once_with(model['model_url'], token='test-token')
            with self.assertRaisesRegex(ValueError, 'already occupied'):
                validate_import([wav], 1, set(range(1, 129)), nam_only=True)
            read.return_value = b'{}'
            with self.assertRaisesRegex(ValueError, 'no 3-channel WaveNet'):
                prepare_model(model, 'test-token', folder, CONVERTER)
