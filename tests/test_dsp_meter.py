import json
from pathlib import Path
import shutil
import subprocess
import sys
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dsp_meter import DspMeter  # noqa: E402

NODE = shutil.which('node')


def run_js(files, expression):
    source = '\n'.join((ROOT / name).read_text(encoding='utf-8') for name in files)
    script = source + f'\nprocess.stdout.write(JSON.stringify({expression}));'
    result = subprocess.run([NODE, '-e', script], capture_output=True, text=True, timeout=30)
    if result.returncode:
        raise AssertionError(result.stderr)
    return json.loads(result.stdout)


@unittest.skipUnless(NODE, 'node is not installed')
class DspMeterScriptTests(unittest.TestCase):
    def test_reported_load_becomes_the_floors_used_of_75(self):
        views = run_js(['dsp_meter.js'], '[42, 51, 15, 60, 69.5, 90, 12, -1, NaN, null].map(dspMeterView)')
        self.assertEqual(views[0], {'load': 42, 'used': 27, 'budget': 75, 'level': 'green'})
        self.assertEqual(views[1], {'load': 51, 'used': 36, 'budget': 75, 'level': 'green'})
        self.assertEqual(views[2]['used'], 0)
        self.assertEqual(views[3]['level'], 'yellow')          # 45/75 = 60 %
        self.assertEqual(views[4]['level'], 'yellow')          # 54.5/75 = 72.7 %
        self.assertEqual(views[5], {'load': 90, 'used': 75, 'budget': 75, 'level': 'red'})
        self.assertEqual(views[6]['used'], 0)                  # below the base: never negative
        self.assertEqual(views[7:], [None, None, None])        # -1: HX Edit could not ask the device

    def test_meter_sits_right_of_the_banner_only_where_it_fits(self):
        layout = run_js(['nav_banner.js'], '''[
            dspMeterLayout({left: 100, top: 9, width: 196}, {h: 24}, {right: 900}),
            dspMeterLayout({left: 100, top: 9, width: 196}, {h: 24}, {right: 1400}),
            dspMeterLayout({left: 100, top: 9, width: 196}, {h: 24}, {right: 500}),
            dspMeterLayout(null, {h: 24}, {right: 1400})]''')
        self.assertEqual(layout[0], {'left': 588, 'top': 9, 'width': 300, 'height': 24})   # ends 12 px before the tempo
        self.assertEqual(layout[1], {'left': 1088, 'top': 9, 'width': 300, 'height': 24})
        self.assertIsNone(layout[2])           # 488 - 310 = 178 between button and tempo: too narrow, hidden
        squeezed = run_js(['nav_banner.js'], 'dspMeterLayout({left: 100, top: 9, width: 196}, {h: 24}, {right: 560})')
        self.assertEqual(squeezed, {'left': 310, 'top': 9, 'width': 238, 'height': 24})   # never over the button
        self.assertIsNone(layout[3])           # no banner, no meter
        banner = (ROOT / 'nav_banner.js').read_text(encoding='utf-8')
        self.assertIn('minLeft: Math.max(view.x + layout.left + layout.width, buttonRight) + 14', banner)   # never over the button
        helper = (ROOT / 'NamLibrary.cs').read_text(encoding='utf-8')
        self.assertIn('if(spanWidth>=0&&ParentWidth()!=placedWidth)Place();', helper)   # follows window resizes
        self.assertIn('if(shown)Invalidate();', helper)   # recovers from HX Edit's repaints over it

    def test_reads_only_through_guarded_native_code(self):
        source = (ROOT / 'dsp_meter.js').read_text(encoding='utf-8')
        self.assertIn("'495f90': '558bec515356578bf9c745fc00000000'", source)
        self.assertIn("'4972b0': '558bec6aff68'", source)
        self.assertIn("onEnter() { device = this.context.ecx; }", source)   # notes, never changes
        self.assertNotIn('onLeave', source)
        self.assertNotIn('retval.replace', source)


class FakeProcess:
    def __init__(self, command, **_):
        self.command = command
        self.lines = []
        self.closed = False
        self.stdin = self

    def write(self, text):
        self.lines.append(json.loads(text))

    def flush(self):
        pass

    def close(self):
        self.closed = True

    def poll(self):
        return 0 if self.closed else None

    def wait(self, timeout=None):
        return 0

    def terminate(self):
        self.closed = True


class DspMeterHostTests(unittest.TestCase):
    def wait_for(self, condition):
        for _ in range(100):
            if condition():
                return
            time.sleep(.02)
        self.fail('timed out')

    def test_starts_on_first_visible_layout_and_forwards_changes(self):
        processes = []
        meter = DspMeter(exe='NamLibrary.exe', process_factory=lambda command, **kw: processes.append(FakeProcess(command)) or processes[-1])
        try:
            meter.receive({'type': 'dspLoad', 'dsp': [{'used': 27}, None]})
            meter.receive({'type': 'dspMeterLayout', 'window': 0, 'visible': False, 'span': None})
            time.sleep(.1)
            self.assertEqual(processes, [])      # nowhere to draw yet
            span = {'minLeft': 310, 'right': 1388, 'top': 9, 'height': 24}
            meter.receive({'type': 'dspMeterLayout', 'window': 1234, 'visible': True, 'x': 300, 'y': 9, 'w': 300, 'h': 24, 'span': span})
            self.wait_for(lambda: processes and processes[0].lines)
            self.assertEqual(processes[0].command, ['NamLibrary.exe', '--dsp-meter', '1234'])
            self.assertEqual(processes[0].lines[-1], {'visible': True, 'x': 300, 'y': 9, 'w': 300, 'h': 24, 'span': span,
                                                      'dsp': [{'used': 27}, None]})
            meter.receive({'type': 'dspLoad', 'dsp': [{'used': 30}, {'used': 36}]})
            self.wait_for(lambda: processes[0].lines[-1]['dsp'][1] == {'used': 36})
            self.assertFalse(meter.receive({'type': 'somethingElse'}))
        finally:
            meter.close()
        self.assertTrue(processes[0].closed)     # closing its input ends the helper
        self.assertEqual(len(processes), 1)

    def test_launcher_and_build_include_the_meter(self):
        launcher = (ROOT / 'start-hx-edit-nam.pyw').read_text(encoding='utf-8')
        self.assertIn("'dsp_meter.js'", launcher)
        self.assertIn('try { installDspMeter(); } catch (error)', launcher)
        self.assertIn('meter.receive(payload)', launcher)
        self.assertIn('meter.close()', launcher)
        build = (ROOT / 'build-windows.py').read_text(encoding='utf-8')
        self.assertIn("'dsp_meter.py', 'dsp_meter.js'", build)
        self.assertIn('args[0]=="--dsp-meter"', (ROOT / 'NamLibrary.cs').read_text(encoding='utf-8'))


if __name__ == '__main__':
    unittest.main()
