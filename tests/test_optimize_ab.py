"""Optimize A/B on this PC: render, level match, cache and the Listen buttons.
No torch, device or sound: the engines are stand-ins, a stand-in script plays nam_ab_render.py and the
player is a mock."""
import json
import math
from pathlib import Path
from queue import Queue
import struct
import sys
import tempfile
from threading import Event
import unittest
from unittest.mock import Mock, patch

import numpy as np

import nam_ab
import nam_ab_render as render
from nam_library import Library, render_library
from tone_preview import TonePreview

RATE = 48000


def write_float_wav(path, x, rate=RATE, channels=1, extensible=False):
    data = np.asarray(x, '<f4').tobytes()
    if extensible:
        fmt = struct.pack('<HHIIHHHHI16s', 0xFFFE, channels, rate, rate * 4 * channels, 4 * channels, 32, 22, 32, 0,
                          struct.pack('<H', 3) + b'\x00\x00\x00\x00\x10\x00\x80\x00\x00\xaa\x00\x38\x9b\x71')
    else:
        fmt = struct.pack('<HHIIHH', 3, channels, rate, rate * 4 * channels, 4 * channels, 32)
    body = b'WAVE' + b'fmt ' + struct.pack('<I', len(fmt)) + fmt + b'data' + struct.pack('<I', len(data)) + data
    Path(path).write_bytes(b'RIFF' + struct.pack('<I', len(body)) + body)


def write_pcm_wav(path, x, bits, rate=RATE):
    x = np.asarray(x, np.float64)
    if bits == 16:
        data = np.round(x * 32767).astype('<i2').tobytes()
    else:                                                       # 24-bit
        v = np.round(x * 8388607).astype(np.int32)
        data = b''.join(int(s & 0xFFFFFF).to_bytes(3, 'little') for s in v)
    fmt = struct.pack('<HHIIHH', 1, 1, rate, rate * bits // 8, bits // 8, bits)
    body = b'WAVE' + b'fmt ' + struct.pack('<I', 16) + fmt + b'data' + struct.pack('<I', len(data)) + data
    Path(path).write_bytes(b'RIFF' + struct.pack('<I', len(body)) + body)


def pedal_coefficients(seed=1):
    rng = np.random.default_rng(seed)
    knots = np.linspace(-4, 4, 65)
    model = {'a': rng.normal(0, 0.1, 64), 'k1': np.tanh(knots), 'b': rng.normal(0, 0.1, 64), 'k2': knots * 0.9,
             'c': rng.normal(0, 0.1, 48), 'k3': np.tanh(knots * 1.5), 'd': rng.normal(0, 1.0, 256) * 3}
    model['a'][0] = model['b'][0] = model['c'][0] = 1.0
    model['d'][0] = 20.0                                        # forces a scale 2^e > 1
    return model


def write_pedal_version(path, model, words=627):
    """A User IR as nam_pedal.ir_words writes it: header, then the coefficients over 2^e."""
    coefs = np.concatenate([model[n] for n in ORDER]) if isinstance(model, dict) else model
    e = 0
    while np.max(np.abs(coefs)) / 2.0 ** e > 15.5:
        e += 1
    values = np.concatenate([[0.5, -0.5, 3.0, 2.0 ** e, words / 1024.0, 0.25, 0.0, 0.0], coefs / 2.0 ** e])
    write_float_wav(path, np.concatenate([values, np.zeros(2048 - len(values))]))


ORDER = ('a', 'k1', 'b', 'k2', 'c', 'k3', 'd')                    # nam_pedal.ORDER


def take(seconds=2.0, level=0.2, seed=4):
    """Something like playing: decaying plucks of a few notes."""
    t = np.arange(int(seconds * RATE)) / RATE
    x = np.zeros_like(t)
    for start, freq in zip(np.arange(0, seconds, 0.5), (82.4, 110, 146.8, 196)):
        on = t >= start
        x[on] += np.sin(2 * np.pi * freq * (t[on] - start)) * np.exp(-3 * (t[on] - start))
    return x / np.max(np.abs(x)) * level


def lufs(path):
    from nam_level_measure import loudness
    x, rate = render.read_wav(path)
    return loudness(x, rate)


class RenderTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.folder = Path(self.temporary.name)

    def tearDown(self):
        self.temporary.cleanup()

    def test_reads_the_wav_formats_takes_and_versions_come_in(self):
        x = take(0.5)
        for name, write, tolerance in (('float.wav', lambda p: write_float_wav(p, x), 1e-6),
                                       ('ext.wav', lambda p: write_float_wav(p, x, extensible=True), 1e-6),
                                       ('pcm16.wav', lambda p: write_pcm_wav(p, x, 16), 1e-4),
                                       ('pcm24.wav', lambda p: write_pcm_wav(p, x, 24), 1e-6)):
            write(self.folder / name)
            got, rate = render.read_wav(self.folder / name)
            self.assertEqual(rate, RATE)
            self.assertLess(float(np.max(np.abs(got - x))), tolerance, name)
        stereo = np.stack([x, -x], axis=1).reshape(-1)
        write_float_wav(self.folder / 'stereo.wav', stereo, channels=2)
        self.assertLess(float(np.max(np.abs(render.read_wav(self.folder / 'stereo.wav')[0]))), 1e-6)  # mixed to mono
        (self.folder / 'not.wav').write_bytes(b'nope')
        with self.assertRaises(ValueError):
            render.read_wav(self.folder / 'not.wav')

    def test_the_pedal_model_comes_back_from_its_user_ir(self):
        model = pedal_coefficients()
        write_pedal_version(self.folder / 'v.wav', model)
        got = render.pedal_model(self.folder / 'v.wav')
        self.assertEqual({k: len(v) for k, v in got.items()}, {k: len(v) for k, v in model.items()})
        for name in model:
            np.testing.assert_allclose(got[name], model[name], rtol=1e-6, atol=1e-6)
        write_pedal_version(self.folder / 'lite.wav', np.zeros(627), words=600)      # another model size
        with self.assertRaises(ValueError):
            render.pedal_model(self.folder / 'lite.wav')
        write_float_wav(self.folder / 'ir.wav', np.full(2048, 0.1))                  # an ordinary IR
        with self.assertRaises(ValueError):
            render.pedal_model(self.folder / 'ir.wav')

    def test_a_short_take_is_played_twice_and_nam_file_set_to_a_guitar_peak(self):
        write_float_wav(self.folder / 'take.wav', take(2.0, level=0.3))
        x = render.guitar(self.folder / 'take.wav')
        self.assertGreaterEqual(len(x), render.MIN_SECONDS * RATE)
        self.assertAlmostEqual(float(np.max(np.abs(x))), 0.3, places=2)              # as recorded
        self.assertEqual(x[0], 0.0)                                                  # faded in: no click
        long = np.concatenate([np.zeros(RATE), take(9.0, level=0.9)])
        write_float_wav(self.folder / 'nam.wav', long)
        x = render.guitar(self.folder / 'nam.wav', start=1.0, seconds=6.0, peak=-14.0)
        self.assertAlmostEqual(len(x) / RATE, 6.0 + render.TAIL, places=3)
        self.assertAlmostEqual(20 * math.log10(float(np.max(np.abs(x)))), -14.0, places=1)
        write_float_wav(self.folder / 'silent.wav', np.zeros(RATE))
        with self.assertRaises(ValueError):
            render.guitar(self.folder / 'silent.wav')
        write_float_wav(self.folder / 'slow.wav', take(1.0), rate=44100)
        with self.assertRaises(ValueError):
            render.guitar(self.folder / 'slow.wav')

    def test_render_writes_a_level_matched_pair(self):
        write_float_wav(self.folder / 'take.wav', take(2.0))
        model = pedal_coefficients()
        write_pedal_version(self.folder / 'v.wav', model)
        seen = {}

        def play_pedal(m, x):
            seen['model'] = m
            return np.asarray(x) * 0.25                         # 6 dB quieter than the original below

        spec = {'source': str(self.folder / 'take.wav'), 'version': str(self.folder / 'v.wav'),
                'out': str(self.folder / 'pair'), 'start': 0.0, 'seconds': None, 'peak': None}
        match = render.render(spec, lambda x: np.asarray(x) * 0.5, play_pedal)
        np.testing.assert_allclose(seen['model']['d'], model['d'], rtol=1e-6)
        self.assertAlmostEqual(match['difference'], -6.02, places=1)
        self.assertAlmostEqual(match['pedalGain'] - match['originalGain'], 6.02, places=1)
        self.assertEqual(match['lufs'], render.TARGET_LUFS)
        self.assertGreaterEqual(match['seconds'], render.MIN_SECONDS)
        original, pedal = self.folder / 'pair' / 'original.wav', self.folder / 'pair' / 'pedal.wav'
        self.assertEqual(original.read_bytes()[20:24], struct.pack('<HH', 1, 1))      # 16-bit PCM mono: any player
        self.assertAlmostEqual(lufs(original), render.TARGET_LUFS, delta=0.1)
        self.assertAlmostEqual(lufs(pedal), lufs(original), delta=0.05)
        self.assertEqual(json.loads((self.folder / 'pair' / 'match.json').read_text())['difference'], match['difference'])

    def test_a_hot_pair_is_lowered_together_under_the_ceiling(self):
        x = take(3.0, level=0.9)
        spiky = x.copy()
        spiky[RATE] = 1.0                                       # one peak far above the playing
        a, b, match = render.level_match(x * 0.05, spiky * 0.05, target=-6.0)
        self.assertLessEqual(match['peak'], render.CEILING + 0.01)
        self.assertLess(match['lufs'], -6.0)                    # both played lower than asked, by the same amount
        self.assertAlmostEqual(match['originalGain'] - match['pedalGain'],
                               -(match['originalLufs'] - match['pedalLufs']), places=1)
        with self.assertRaises(ValueError):
            render.level_match(np.zeros(RATE), x)

    def test_main_reports_a_failure_as_one_json_line(self):
        out = []
        with patch('builtins.print', lambda line, **_: out.append(line)):
            self.assertEqual(render.main([str(self.folder / 'missing.json')]), 1)
        answer = json.loads(out[-1])
        self.assertFalse(answer['ok'])
        self.assertIn('missing.json', answer['error'])


class CacheTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.folder = Path(self.temporary.name)
        for name, data in (('take.wav', b'RIFF take'), ('capture.nam', b'{}'), ('v.wav', b'RIFF v1')):
            (self.folder / name).write_bytes(data)
        self.source = {'kind': 'guitar', 'path': str(self.folder / 'take.wav'), 'start': 0.0, 'seconds': None,
                       'peak': None}

    def tearDown(self):
        self.temporary.cleanup()

    def key(self):
        return nam_ab.cache_key(self.source, self.folder / 'capture.nam', self.folder / 'v.wav')

    def test_the_key_follows_the_guitar_and_the_version(self):
        first = self.key()
        self.assertEqual(self.key(), first)
        (self.folder / 'v.wav').write_bytes(b'RIFF v2')                 # optimized again
        second = self.key()
        self.assertNotEqual(second, first)
        (self.folder / 'take.wav').write_bytes(b'RIFF new take')        # the guitar recorded again
        self.assertNotEqual(self.key(), second)
        third = self.key()
        self.source['start'] = 64.0
        self.assertNotEqual(self.key(), third)
        with patch.object(nam_ab, 'REVISION', nam_ab.REVISION + 1):
            self.source['start'] = 0.0
            self.assertNotEqual(self.key(), third)

    def test_lookup_needs_the_whole_pair_and_prune_keeps_the_newest(self):
        renders = nam_ab.Renders(self.folder / 'renders', keep=2)
        self.assertIsNone(renders.lookup('k1'))
        for index, key in enumerate(('k1', 'k2', 'k3')):
            (renders.folder / key).mkdir(parents=True)
            (renders.folder / key / 'match.json').write_text(json.dumps({'difference': -1.0 * index}))
            renders.path(key, 'original').write_bytes(b'RIFF')
            renders.path(key, 'pedal').write_bytes(b'RIFF')
            import os
            os.utime(renders.folder / key, (1000 + index, 1000 + index))
        self.assertEqual(renders.lookup('k2')['key'], 'k2')               # heard now: the newest
        renders.prune()
        self.assertEqual(sorted(p.name for p in renders.folder.iterdir()), ['k2', 'k3'])
        renders.path('k3', 'pedal').unlink()
        self.assertIsNone(renders.lookup('k3'))
        with self.assertRaises(ValueError):
            renders.path('k2', 'other')

    def test_which_guitar_is_played(self):
        take_file = self.folder / 'take.wav'
        guitars = {'guitars': [{'id': 'g1', 'name': 'Strat', 'peak': -12.0, 'file': str(take_file)},
                               {'id': 'g2', 'name': 'Les Paul', 'peak': -9.0, 'file': str(take_file)}], 'last': 'g2'}
        chosen = nam_ab.pick_source(guitars, nam_file=self.folder / 'nam.wav')
        self.assertEqual((chosen['kind'], chosen['name'], chosen['peak']), ('guitar', 'Les Paul', None))
        chosen = nam_ab.pick_source({'guitars': [], 'last': None}, nam_file=self.folder / 'nam.wav')
        self.assertEqual((chosen['kind'], chosen['start'], chosen['seconds'], chosen['peak']),
                         ('nam', 64.0, nam_ab.NAM_SECONDS, -14.0))
        with patch('nam_level.nam_input', return_value=None):
            self.assertIsNone(nam_ab.pick_source({'guitars': [], 'last': None}))

    def test_match_text(self):
        self.assertIn('1.5 dB quieter', nam_ab.match_text({'difference': -1.5, 'lufs': -18.0}))
        self.assertIn('2.0 dB louder', nam_ab.match_text({'difference': 2.0, 'lufs': -18.0}))
        self.assertIn('the same level', nam_ab.match_text({'difference': 0.2, 'lufs': -18.0}))
        self.assertEqual(nam_ab.match_text(None), '')


class PlayerTests(unittest.TestCase):
    def test_plays_on_the_pc_never_on_the_floor(self):
        sound = Mock(SND_FILENAME=0x20000, SND_ASYNC=1, SND_NODEFAULT=2)
        with patch.dict(sys.modules, winsound=sound), patch.object(sys, 'platform', 'win32'):
            with self.assertRaises(OSError) as refused:
                nam_ab.Player(lambda: 'Line 6 Helix').play('pair/pedal.wav')
            self.assertIn('the Floor', str(refused.exception))
            self.assertFalse(any(c.args and c.args[0] for c in sound.PlaySound.call_args_list))   # only stops
            nam_ab.Player(lambda: 'Speakers (Realtek(R) Audio)').play('pair/pedal.wav')
            sound.PlaySound.assert_called_with('pair/pedal.wav', 0x20000 | 1 | 2)
            nam_ab.Player(lambda: None).stop()
            sound.PlaySound.assert_called_with(None, 0)


FAKE_RENDERER = '''
import json, sys
from pathlib import Path
spec = json.loads(Path(sys.argv[1]).read_text(encoding='utf-8'))
print('loading torch...')
if 'Broken' in spec['capture']:
    print(json.dumps({'ok': False, 'error': 'this capture is an A2 Lite only'}))
    sys.exit(1)
out = Path(spec['out'])
out.mkdir(parents=True)
(out / 'original.wav').write_bytes(b'RIFF original')
if 'Half' in spec['capture']:
    sys.exit('crashed half way')
(out / 'pedal.wav').write_bytes(b'RIFF pedal')
(out / 'match.json').write_text(json.dumps({'difference': -0.8, 'lufs': -18.0}))
print(json.dumps({'ok': True, 'difference': -0.8}))
'''


class RenderJobTests(unittest.TestCase):
    def run_job(self, folder, capture, find_python=lambda _: sys.executable):
        renderer = Path(folder, 'nam_ab_render.py')
        renderer.write_text(FAKE_RENDERER, encoding='utf-8')
        fitter = Path(folder, 'nam_pedal.py')
        fitter.write_text('', encoding='utf-8')
        events = Queue()
        renders = nam_ab.Renders(Path(folder, 'renders'))
        job = nam_ab.RenderJob(renders, 'pair1', {'fitter': str(fitter), 'capture': capture}, events.put,
                               find_python=find_python, renderer=renderer)
        job.thread.join(timeout=30)
        return events.get_nowait(), renders

    def test_a_render_is_kept_for_the_next_listen(self):
        with tempfile.TemporaryDirectory() as folder:
            event, renders = self.run_job(folder, 'Klon.nam')
            self.assertTrue(event['done'])
            self.assertEqual((event['match']['key'], event['match']['difference']), ('pair1', -0.8))
            self.assertEqual(renders.lookup('pair1')['difference'], -0.8)
            self.assertEqual([p.name for p in renders.folder.iterdir()], ['pair1'])   # no work folder left

    def test_failures_leave_no_half_pair(self):
        with tempfile.TemporaryDirectory() as folder:
            event, renders = self.run_job(folder, 'Broken.nam')
            self.assertEqual(event['error'], 'this capture is an A2 Lite only')
            event, renders = self.run_job(folder, 'Half.nam')
            self.assertIn('crashed half way', event['error'])
            self.assertEqual(list(renders.folder.iterdir()), [])
            event, _ = self.run_job(folder, 'Klon.nam', find_python=lambda _: None)
            self.assertIn('Listening to a version needs Python', event['error'])


def pedal_library(folder, gear='pedal', version=True):
    source = Path(folder, 'Klon Gain 6.nam')
    source.write_text('{"version": "0.5.4"}', encoding='utf-8')
    library = Library(Path(folder, 'library'))
    identity = library.add(source, name='Klon Gain 6', gear=gear)
    if version:
        made = Path(folder, 'made.wav')
        made.write_bytes(b'RIFF pedal model')
        library.add_version(identity, 'pedal', made, error=0.012, converter='nam_pedal 1')
    return library, identity


class ListenFlowTests(unittest.TestCase):
    def browse(self, folder, library, actions, *, optimize=True, job=None, player=None, no_source=False):
        browser = Mock()
        browser.actions = Queue()
        for action in actions:
            browser.actions.put(action)
        browser.next_action = lambda: 'finish' if browser.actions.empty() else browser.actions.get_nowait()
        preview = TonePreview(Mock(), folder, Path(folder, 'tools', 'nam2ir.py'), Event(), Mock(),
                              browser_factory=Mock(), library=library, optimize=optimize,
                              listen_folder=Path(folder, 'renders'))
        self.player = player or Mock()
        take_file = Path(folder, 'take.wav')
        take_file.write_bytes(b'RIFF take')
        source = None if no_source else {'kind': 'guitar', 'path': str(take_file), 'name': 'Strat',
                                         'start': 0.0, 'seconds': None, 'peak': None}
        with patch('nam_ab.RenderJob', job or Mock()), patch('nam_ab.Player', return_value=self.player), \
                patch('nam_ab.pick_source', return_value=source):
            preview.browse(browser, 1, '')
        return browser

    def test_first_listen_renders_then_plays_and_the_next_plays_at_once(self):
        with tempfile.TemporaryDirectory() as folder:
            library, identity = pedal_library(folder)
            started = []

            class FakeJob:
                def __init__(self, renders, key, spec, on_event):
                    started.append(spec)
                    (renders.folder / key).mkdir(parents=True)
                    for which in nam_ab.WHICH:
                        renders.path(key, which).write_bytes(b'RIFF')
                    (renders.folder / key / 'match.json').write_text(json.dumps({'difference': -1.2, 'lufs': -18.0}))
                    on_event({'done': True, 'match': renders.lookup(key)})

                def cancel(self):
                    pass

            browser = self.browse(folder, library, [f'listen:{identity}:pedal'], job=FakeJob)
            spec, = started
            self.assertEqual(Path(spec['capture']).name, f'{identity}.nam')            # the kept original
            self.assertEqual(Path(spec['version']).name, f'{identity}.pedal.wav')
            self.assertEqual(Path(spec['fitter']).name, 'nam_pedal.py')
            played, = self.player.play.call_args_list
            self.assertEqual(Path(played.args[0]).name, 'pedal.wav')
            last = browser.send.call_args_list[-1].kwargs
            self.assertIn('Playing the Pedal version on this PC (Strat)', last['text'])
            self.assertIn('1.2 dB quieter', last['text'])
            self.assertEqual((last['listen']['playing'], last['listen']['match']['difference']), ('pedal', -1.2))
            self.player.stop.assert_called()                                            # stopped when the page closed
            # the same guitar and version again: no render, straight to the player
            self.browse(folder, library, [f'listen:{identity}:original'], job=FakeJob)
            self.assertEqual(len(started), 1)
            self.assertEqual(Path(self.player.play.call_args.args[0]).name, 'original.wav')

    def test_refusals_change_nothing(self):
        with tempfile.TemporaryDirectory() as folder:
            library, identity = pedal_library(folder)
            job = Mock()
            browser = self.browse(folder, library, [f'listen:{identity}:pedal'], optimize=False, job=job)
            self.assertIn('not available', browser.send.call_args_list[-1].kwargs['text'])
            self.assertIsNone(browser.send.call_args_list[-1].kwargs['listen'])
            browser = self.browse(folder, library, [f'listen:{identity}:pedal'], job=job, no_source=True)
            self.assertIn('record your guitar', browser.send.call_args_list[-1].kwargs['text'])
            self.browse(folder, library, [f'listen:{identity}:louder'], job=job)
            job.assert_not_called()
            self.player.play.assert_not_called()
        with tempfile.TemporaryDirectory() as folder:
            library, identity = pedal_library(folder, version=False)
            browser = self.browse(folder, library, [f'listen:{identity}:pedal'], job=job)
            self.assertIn('no Pedal version', browser.send.call_args_list[-1].kwargs['text'])
            job.assert_not_called()

    def test_a_failed_render_and_stop(self):
        with tempfile.TemporaryDirectory() as folder:
            library, identity = pedal_library(folder)

            class FailingJob:
                def __init__(self, renders, key, spec, on_event):
                    on_event({'error': 'Listening to a version needs Python with torch'})

                def cancel(self):
                    pass

            browser = self.browse(folder, library, [f'listen:{identity}:original', 'listenStop'], job=FailingJob)
            texts = [c.kwargs['text'] for c in browser.send.call_args_list if 'text' in c.kwargs]
            self.assertTrue(any(t.startswith('Listen failed: Listening to a version needs Python') for t in texts))
            self.assertIn('Stopped.', texts)
            self.player.play.assert_not_called()

    def test_the_page_has_hear_on_this_pc_beside_the_version_line(self):
        with tempfile.TemporaryDirectory() as folder:
            library, _ = pedal_library(folder)
            page = render_library(library.entries())
            for marker in ('id="listenOriginal"', 'id="listenPedal"', 'id="listenStop"', 'Hear on this PC', 'id="listenRecord"', "host?.postMessage('levelOpen')",
                           "listenSource === '' && !mine", "(capture.versionSlots?.pedal || []).includes(blockSlot)",
                           "'listen:' + selected + ':original'", "'listen:' + selected + ':pedal'",
                           "host?.postMessage('listenStop')", 'listening = data.listen || null',
                           'group.hidden = !canOptimize || !capture || !pedal', 'renderListen(capture, running ? null : pedal)'):
                self.assertIn(marker, page)


@unittest.skipUnless(Path(nam_ab.RENDERER).is_file(), 'renderer missing')
class PinnedKernelTests(unittest.TestCase):
    """With the pinned fitter and torch present (the training Python), nam_pedal.ir_words -> pedal_model
    round-trips, so the A/B plays the very coefficients the NAM Pedal block will get."""

    def test_round_trip_through_the_fitter(self):
        fitter = Path(__file__).resolve().parents[3] / 'research/pedal-port-20261115/helix-lt-nam-dev/tools'
        try:
            import torch  # noqa: F401
        except ImportError:
            self.skipTest('needs torch (the training Python)')
        if not fitter.is_dir():
            self.skipTest('the pinned fitter is not on this PC')
        sys.path.insert(0, str(fitter))
        import nam_pedal
        from nam2ir import write_wav
        model = {k: list(v) for k, v in pedal_coefficients().items()}
        with tempfile.TemporaryDirectory() as folder:
            write_wav(str(Path(folder, 'v.wav')), nam_pedal.ir_words(model))
            got = render.pedal_model(Path(folder, 'v.wav'))
        for name in model:
            np.testing.assert_allclose(got[name], model[name], rtol=1e-6, atol=1e-6)


if __name__ == '__main__':
    unittest.main()
