"""Lighter captures: nam_refine's output parsed, the training child process, and the library page wiring.
No torch, device or browser: a stand-in script plays nam_refine.py."""
import json
from pathlib import Path
from queue import Empty, Queue
import sys
import tempfile
from threading import Event
import unittest
from unittest.mock import Mock, patch

import nam_light
from nam_library import Library, render_library
from tone_preview import TonePreview


FAKE_REFINE = '''
import sys
args = sys.argv[1:]
out = args[args.index('-o') + 1]
if 'fail' in args[0]:
    sys.exit('this capture is an A2 Lite only: no bigger model to learn from')
print('the full Lite: held-out ESR 0.01040; its first 16 layers alone: 1.14398')
print('step 400/800: held-out ESR 0.02000 (best 0.01900), 6 s')
open(out, 'w').write('{}')
print('[+] ' + out + ': error against the teacher 1.04% (the full Lite) -> 1.77% (held-out ESR), loudness -15.4 dB')
'''


class ParseTests(unittest.TestCase):
    def test_reads_progress_and_result_lines(self):
        state = {}
        self.assertFalse(nam_light.parse('teachers run on 91 s + 15 s held out in 6 s', state))
        self.assertTrue(nam_light.parse('the full Lite: held-out ESR 0.02438; its first 16 layers alone: 1.14398', state))
        self.assertTrue(nam_light.parse('step 300/3000: held-out ESR 0.05384 (best 0.05300), 34 s', state))
        self.assertTrue(nam_light.parse('[+] x.nam: error against the teacher 2.44% (the full Lite) -> 5.38% '
                                        '(held-out ESR), loudness -15.4 dB', state))
        self.assertEqual(state, {'before': 0.02438, 'step': 300, 'steps': 3000, 'best': 0.053, 'after': 0.0538})

    def test_reads_a_refine_and_a_capture_trained_from_scratch(self):
        state = {}
        nam_light.parse('[+] x.nam: error against the teacher 1.20% -> 0.90% (held-out ESR), loudness -18.0 dB', state)
        self.assertEqual(state, {'before': 0.012, 'after': 0.009})
        state = {}
        nam_light.parse('[+] x.nam: error against the teacher 3.10% (held-out ESR), loudness -18.0 dB', state)
        self.assertEqual(state, {'after': 0.031})


class JobTests(unittest.TestCase):
    def run_job(self, folder, capture, find_python=lambda _: sys.executable, refine=None):
        events = Queue()
        if refine is None:
            refine = Path(folder, 'nam_refine.py')
            refine.write_text(FAKE_REFINE, encoding='utf-8')
        output = Path(folder, 'out.nam')
        job = nam_light.LightJob(refine, Path(folder, capture), output, 16, 800, events.put, find_python=find_python)
        job.thread.join(timeout=30)
        result = []
        while True:
            try:
                result.append(events.get_nowait())
            except Empty:
                return result

    def test_reports_progress_then_done(self):
        with tempfile.TemporaryDirectory() as folder:
            events = self.run_job(folder, 'AC30.nam')
            self.assertEqual(events[1]['step'], 400)
            self.assertEqual(events[-1], {'layers': 16, 'step': 400, 'steps': 800, 'before': 0.0104, 'best': 0.019,
                                          'after': 0.0177, 'done': True})

    def test_reports_nam_refine_refusal_and_missing_torch(self):
        with tempfile.TemporaryDirectory() as folder:
            self.assertIn('no bigger model', self.run_job(folder, 'fail.nam')[-1]['error'])
            self.assertEqual(self.run_job(folder, 'AC30.nam', find_python=lambda _: None)[-1]['error'], nam_light.MISSING)
            missing = self.run_job(folder, 'AC30.nam', refine=Path(folder, 'none.py'))[-1]['error']
            self.assertIn('nam_refine.py', missing)


class LibraryWiringTests(unittest.TestCase):
    def test_page_renders_the_light_panel(self):
        page = render_library({})
        self.assertIn('id="lighter"', page)
        self.assertIn('~29% DSP', page)
        self.assertIn('id="lightPedal"', page)
        self.assertIn("':14:quick'", page)        # the pedal button's own job: 14 layers, Quick

    def test_trained_capture_joins_the_library(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder, 'VOX AC30.nam')
            source.write_text(json.dumps({'metadata': {}}), encoding='utf-8')
            library = Library(Path(folder, 'library'))
            identity = library.add(source, name='VOX AC30', gear='amp')
            started = []

            class FakeJob:
                def __init__(self, refine, capture, output, layers, steps, on_event):
                    started.append((Path(refine).name, Path(capture).name, Path(output).name, layers, steps))
                    Path(output).write_text(json.dumps({'metadata': {'nam_light': {'layers': layers}}}), encoding='utf-8')
                    on_event({'layers': layers, 'step': steps, 'steps': steps, 'before': 0.01, 'after': 0.02, 'done': True})

                def cancel(self):
                    pass

            # the page asks for a 16-layer version; the job's events come back through the action queue
            browser = Mock()
            browser.actions = Queue()
            browser.actions.put(f'light:{identity}:16:quick')
            browser.next_action = lambda: 'finish' if browser.actions.empty() else browser.actions.get_nowait()
            preview = TonePreview(Mock(), folder, Path(folder, 'tools', 'nam2ir.py'), Event(), Mock(),
                                  browser_factory=Mock(), library=library)
            with patch('nam_light.LightJob', FakeJob):
                preview.browse(browser, 1, '')
            self.assertEqual(started, [('nam_refine.py', 'VOX AC30.nam', 'VOX AC30 L16.nam', 16, 800)])
            names = sorted(entry['name'] for entry in library.entries().values())
            self.assertEqual(names, ['VOX AC30', 'VOX AC30 [16 layers]'])
            last = browser.send.call_args_list[-1].kwargs
            self.assertTrue(last['light']['done'] and last['light']['result'] in library.entries())


if __name__ == '__main__':
    unittest.main()
