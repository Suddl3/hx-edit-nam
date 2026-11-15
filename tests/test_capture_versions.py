"""Optimized versions of one capture: kept beside the original, shown on its one library row.
No torch, device or browser: a stand-in script plays nam_pedal.py."""
from pathlib import Path
from queue import Queue
import sys
import tempfile
from threading import Event
import unittest
from unittest.mock import Mock, patch

from nam_library import Library, SOUNDS_SAME_ERROR, VERSIONS, render_library, version_quality
from nam_sidebar import sidebar_rows
from tone_preview import TonePreview


class CaptureVersionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        folder = Path(self.temporary.name)
        capture = folder / 'Klon Gain 6.nam'
        capture.write_text('{"version": "0.5.4"}', encoding='utf-8')
        self.library = Library(folder / 'library')
        self.identity = self.library.add(capture, gear='pedal')
        self.made = folder / 'made.wav'
        self.made.write_bytes(b'RIFF pedal model')

    def tearDown(self):
        self.temporary.cleanup()

    def test_a_version_is_kept_beside_the_original_and_shown_on_its_row(self):
        self.library.add_version(self.identity, 'pedal', self.made, error=0.012, converter='nam_pedal 1')
        path = self.library.version_path(self.identity, 'pedal')
        self.assertEqual(path.name, f'{self.identity}.pedal.wav')
        self.assertEqual(path.read_bytes(), b'RIFF pedal model')
        self.assertTrue((self.library.folder / f'{self.identity}.nam').is_file())     # the original stays
        entry = self.library.entries()[self.identity]
        self.assertEqual(entry['versions']['pedal']['quality'], 'same')
        self.assertEqual(len(self.library.entries()), 1)                              # one row, not two
        row, = sidebar_rows(self.library)
        self.assertEqual([(v['key'], v['label'], v['block'], v['quality']) for v in row['versions']],
                         [('pedal', 'Pedal', 'NAM Pedal', 'same')])
        self.assertTrue(row['canOptimize'])

    def test_replacing_removing_and_deleting_the_capture(self):
        self.library.add_version(self.identity, 'pedal', self.made, error=0.06)
        self.assertEqual(self.library.entries()[self.identity]['versions']['pedal']['quality'], 'different')
        self.made.write_bytes(b'RIFF better fit')
        self.library.add_version(self.identity, 'pedal', self.made, error=0.01)   # optimized again: replaced
        self.assertEqual(self.library.version_path(self.identity, 'pedal').read_bytes(), b'RIFF better fit')
        self.assertTrue(self.library.remove_version(self.identity, 'pedal'))
        self.assertFalse(self.library.version_path(self.identity, 'pedal').exists())
        self.assertNotIn('pedal', self.library.entries()[self.identity]['versions'])
        self.assertFalse(self.library.remove_version(self.identity, 'pedal'))
        self.library.add_version(self.identity, 'pedal', self.made)
        self.library.remove(self.identity)                                        # its versions go with it
        self.assertEqual(list(self.library.folder.glob(f'{self.identity}*')), [])

    def test_refusals(self):
        with self.assertRaises(ValueError):
            self.library.add_version(self.identity, 'smaller', self.made)        # unknown kind
        with self.assertRaises(KeyError):
            self.library.add_version('f' * 64, 'pedal', self.made)
        with self.assertRaises(ValueError):
            self.library.version_path('../escape', 'pedal')
        empty = self.made.with_name('empty.wav')
        empty.write_bytes(b'')
        with self.assertRaises(ValueError):
            self.library.add_version(self.identity, 'pedal', empty)
        entries = self.library._read()
        entries[self.identity]['format'] = '.wav'                                 # a converted WAV: no teacher
        self.library._write(entries)
        with self.assertRaises(ValueError):
            self.library.add_version(self.identity, 'pedal', self.made)

    def test_quality_badge(self):
        self.assertEqual(version_quality(SOUNDS_SAME_ERROR), 'same')
        self.assertEqual(version_quality(SOUNDS_SAME_ERROR + 0.001), 'different')
        for unknown in (None, float('nan'), -1, 'low'):
            self.assertEqual(version_quality(unknown), 'unknown')
        self.assertEqual(set(VERSIONS), {'pedal', 'light16'})


FAKE_FITTER = '''
import sys
args = sys.argv[1:]
out = args[args.index('-o') + 1]
if 'Fuzz' in args[0]:
    sys.exit('the capture has no model to fit against')
print('step 200/400: weighted error 0.0310')
print('step 400/400: weighted error 0.0120')
open(out, 'wb').write(b'RIFF pedal model')
print('wrote ' + out + ' in 21 s: a pedal model (627 words) for the NAM Pedal block')
if '--check' in args:
    print('held-out ESR 1.25%, pre-emphasis ESR 2.10%')
'''


class OptimizeJobTests(unittest.TestCase):
    def run_job(self, folder, name, find_python=lambda _: sys.executable):
        import nam_optimize
        fitter = Path(folder, 'nam_pedal.py')
        fitter.write_text(FAKE_FITTER, encoding='utf-8')
        Path(folder, name).write_text('{}', encoding='utf-8')
        events = Queue()
        output = Path(folder, 'out.wav')
        job = nam_optimize.OptimizeJob(fitter, Path(folder, name), output, 0, 400, events.put, find_python=find_python)
        job.thread.join(timeout=30)
        seen = []
        while not events.empty():
            seen.append(events.get_nowait())
        return seen

    def test_progress_then_the_held_out_error(self):
        with tempfile.TemporaryDirectory() as folder:
            events = self.run_job(folder, 'Klon.nam')
            self.assertEqual([e['step'] for e in events[:-1]], [200, 400])
            self.assertTrue(events[-1]['done'])
            self.assertAlmostEqual(events[-1]['esr'], 0.0125)

    def test_failure_and_missing_training_python(self):
        import nam_light
        with tempfile.TemporaryDirectory() as folder:
            self.assertIn('no model to fit against', self.run_job(folder, 'Fuzz.nam')[-1]['error'])
            self.assertEqual(self.run_job(folder, 'Klon.nam', find_python=lambda _: None)[-1]['error'], nam_light.MISSING)

    def test_which_captures_get_which_version(self):
        import nam_optimize
        self.assertEqual(nam_optimize.version_for('pedal'), 'pedal')
        for gear in ('amp', 'amp-cab', 'cab', 'preamp', '', None):
            self.assertIsNone(nam_optimize.version_for(gear))


class OptimizeFlowTests(unittest.TestCase):
    def browse(self, folder, library, actions, *, optimize=True, job=None):
        browser = Mock()
        browser.actions = Queue()
        for action in actions:
            browser.actions.put(action)
        browser.next_action = lambda: 'finish' if browser.actions.empty() else browser.actions.get_nowait()
        preview = TonePreview(Mock(), folder, Path(folder, 'tools', 'nam2ir.py'), Event(), Mock(),
                              browser_factory=Mock(), library=library, optimize=optimize)
        with patch('nam_optimize.OptimizeJob', job or Mock()):
            preview.browse(browser, 1, '')
        return browser

    def test_optimize_keeps_a_pedal_version_on_the_same_row(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder, 'Klon Gain 6.nam')
            source.write_text('{}', encoding='utf-8')
            library = Library(Path(folder, 'library'))
            identity = library.add(source, name='Klon Gain 6', gear='pedal')
            started = []

            class FakeJob:
                def __init__(self, fitter, capture, output, layers, steps, on_event):
                    started.append((Path(fitter).name, Path(capture).suffix, steps))
                    Path(output).write_bytes(b'RIFF pedal model')
                    on_event({'step': steps, 'steps': steps, 'esr': 0.0125, 'done': True})

                def cancel(self):
                    pass

            browser = self.browse(folder, library, [f'optimize:{identity}'], job=FakeJob)
            self.assertEqual(started, [('nam_pedal.py', '.nam', 400)])
            self.assertEqual(len(library.entries()), 1)
            version = library.entries()[identity]['versions']['pedal']
            self.assertEqual((version['quality'], version['error']), ('same', 0.0125))
            self.assertTrue(version['converter'].startswith('nam_pedal'))
            last = browser.send.call_args_list[-1]
            self.assertIn('sounds the same', last.kwargs['text'])
            self.assertIs(last.kwargs['can_optimize'], True)
            # and removed again; the original stays
            self.browse(folder, library, [f'versionRemove:{identity}:pedal'])
            self.assertEqual(library.entries()[identity]['versions'], {})
            self.assertEqual(len(library.entries()), 1)

    def test_refused_for_amps_and_when_the_build_has_no_optimize(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder, 'Plexi.nam')
            source.write_text('{}', encoding='utf-8')
            library = Library(Path(folder, 'library'))
            amp = library.add(source, name='Plexi', gear='amp')
            job = Mock()
            browser = self.browse(folder, library, [f'optimize:{amp}'], job=job)
            job.assert_not_called()
            self.assertIn('pedal captures', browser.send.call_args_list[-1].kwargs['text'])
            library.update(amp, gear='pedal')
            browser = self.browse(folder, library, [f'optimize:{amp}'], optimize=False, job=job)
            job.assert_not_called()
            self.assertIn('not available', browser.send.call_args_list[-1].kwargs['text'])

    def test_page_shows_chips_and_the_optimize_button(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder, 'Klon.nam')
            source.write_text('{}', encoding='utf-8')
            library = Library(Path(folder, 'library'))
            identity = library.add(source, gear='pedal')
            made = Path(folder, 'made.wav')
            made.write_bytes(b'RIFF')
            library.add_version(identity, 'pedal', made, error=0.06)
            page = render_library(library.entries())
            self.assertIn('"versions": [{"key": "pedal", "label": "Pedal", "block": "NAM Pedal", "quality": "different"', page)
            for marker in ('id="optimize"', 'appendVersionChips(name, row)', "'optimize:' + selected",
                           "'versionRemove:' + id + ':pedal'", 'canOptimize = data.can_optimize === true',
                           "ask('Remove the Pedal version? The original capture stays.'"):
                self.assertIn(marker, page)
            self.assertNotIn('confirm(', page.replace('a browser confirm()', ''))   # in-page dialogs only


if __name__ == '__main__':
    unittest.main()
