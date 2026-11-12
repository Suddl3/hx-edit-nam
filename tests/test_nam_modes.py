import struct
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

import nam_modes
from nam_inventory import is_nam_profile, is_pedal_profile


class FakeJob:
    """Stands in for a training child process: a few steps, then the output file."""

    def __init__(self, refine, capture, output, layers, steps, on_event):
        self.args = (Path(refine).name, Path(capture).name, layers, steps)
        self.output = Path(output)
        FakeJob.made.append(self)
        self.thread = threading.Thread(target=self.run, args=(on_event, steps), daemon=True)
        self.thread.start()

    def run(self, on_event, steps):
        on_event({'step': steps // 2, 'steps': steps})
        self.output.write_text('trained', encoding='utf-8')
        on_event({'done': True, 'step': steps, 'steps': steps})

    def cancel(self):
        pass


class ModeTests(unittest.TestCase):
    def setUp(self):
        FakeJob.made = []

    def test_the_pedal_mode_is_offered_only_with_the_pedal_firmware(self):
        self.assertNotIn('pedal', [m[0] for m in nam_modes.modes(False)])
        self.assertEqual([m[0] for m in nam_modes.modes(True)],
                         ['original', 'pedal', '16-quick', '16-standard', '14-standard'])
        self.assertNotIn('14-quick', [m[0] for m in nam_modes.MODES])     # it lost the highs

    def test_trained_names_fit_an_ir_name(self):
        long = Path('A very long capture name that goes on and on.nam')
        self.assertTrue(nam_modes.light_name(long, 16).endswith(' L16.nam'))
        self.assertTrue(nam_modes.light_name(long, 0).endswith(' PED.wav'))
        self.assertLessEqual(len(Path(nam_modes.light_name(long, 0)).stem), nam_modes.IR_NAME_LIMIT)

    def test_train_runs_each_capture_with_the_modes_tool_and_steps(self):
        with tempfile.TemporaryDirectory() as temp:
            caps = [Path(temp, 'one.nam'), Path(temp, 'two.nam')]
            for cap in caps:
                cap.write_text(cap.name, encoding='utf-8')
            out = nam_modes.train(caps, '16-quick', temp, Path(temp, 'nam_refine.py'), job=FakeJob, show=False)
            self.assertEqual([Path(o).name for o in out], ['one L16.nam', 'two L16.nam'])
            self.assertEqual([j.args for j in FakeJob.made], [('nam_refine.py', 'one.nam', 16, 800),
                                                              ('nam_refine.py', 'two.nam', 16, 800)])
            FakeJob.made = []
            out = nam_modes.train(caps[:1], 'pedal', temp, Path(temp, 'nam_refine.py'), job=FakeJob, show=False)
            self.assertEqual([Path(o).name for o in out], ['one PED.wav'])
            self.assertEqual(FakeJob.made[0].args[0], 'nam_pedal.py')

    def test_a_capture_converted_before_comes_from_the_cache(self):
        with tempfile.TemporaryDirectory() as temp:
            one, two, renamed = Path(temp, 'one.nam'), Path(temp, 'two.nam'), Path(temp, 'again.nam')
            for cap, text in ((one, 'A'), (two, 'B'), (renamed, 'A')):
                cap.write_text(text, encoding='utf-8')
            nam_modes.train([one], 'pedal', temp, Path(temp, 'nam_refine.py'), job=FakeJob, show=False)
            FakeJob.made = []
            # the same bytes under another name: cached; another capture: trained; another mode: trained
            out = nam_modes.train([renamed, two], 'pedal', temp, Path(temp, 'nam_refine.py'), job=FakeJob, show=False)
            self.assertEqual([Path(o).name for o in out], ['again PED.wav', 'two PED.wav'])
            self.assertEqual([j.args[1] for j in FakeJob.made], ['two.nam'])
            self.assertEqual(Path(out[0]).read_text(encoding='utf-8'), 'trained')
            FakeJob.made = []
            nam_modes.train([one], '16-quick', temp, Path(temp, 'nam_refine.py'), job=FakeJob, show=False)
            self.assertEqual(len(FakeJob.made), 1)
            FakeJob.made = []
            self.assertIsNotNone(nam_modes.train([one, two], 'pedal', temp, Path(temp, 'r.py'), job=FakeJob, show=False))
            self.assertEqual(FakeJob.made, [])                         # nothing to train: no dialog either

    def test_a_pedal_model_is_a_nam_profile_and_known_as_a_pedal(self):
        words = [0.5, -0.5, 3.0, 4.0, 627 / 1024, 0.0, 0.0, 0.0] + [0.1] * 627
        payload = struct.pack('<%df' % len(words), *words) + bytes(8192 - 4 * len(words))
        self.assertTrue(is_nam_profile(payload) and is_pedal_profile(payload))
        lite = [0.5, -0.5, 3.0, 2.0, 1871 / 1024] + [0.0] * 3
        payload = struct.pack('<8f', *lite) + bytes(8192 - 32)
        self.assertTrue(is_nam_profile(payload) and not is_pedal_profile(payload))


class FirmwareTests(unittest.TestCase):
    def test_pedals_default_to_the_pedal_model_only_with_the_pedal_firmware(self):
        self.assertEqual(nam_modes.default_mode('pedal', True), 'pedal')
        self.assertEqual(nam_modes.default_mode('Pedal', False), 'original')
        self.assertEqual(nam_modes.default_mode('amp', True), 'original')
        self.assertEqual([k for k, _, _ in nam_modes.FIRMWARES], ['pedal', 'layers', 'plain'])

    def test_one_tone3000_tile_per_block_of_the_firmware(self):
        modes = {k for k, *_ in nam_modes.MODES}
        for firmware, _, _ in nam_modes.FIRMWARES:
            tiles = nam_modes.tiles(firmware)
            self.assertEqual(len({t['label'] for t in tiles}), len(tiles))
            self.assertTrue(all(set(t['modes']) <= modes and t['label'].startswith('TONE3000') for t in tiles))
        self.assertEqual([t['block'] for t in nam_modes.tiles('plain')], ['NAM A2 Lite'])
        self.assertEqual(nam_modes.tiles('pedal')[-1], {'label': 'TONE3000 Pedal', 'block': 'NAM Pedal',
                                                        'modes': ['pedal']})

    def test_a_tile_with_one_mode_does_not_ask(self):
        with patch.object(nam_modes, 'ask') as ask:
            self.assertEqual(nam_modes.choose_tile_mode('x', ['pedal']), 'pedal')
            ask.assert_not_called()
            ask.return_value = '16-standard'
            self.assertEqual(nam_modes.choose_tile_mode('x', ['16-quick', '16-standard']), '16-standard')
            self.assertEqual([k for k, *_ in ask.call_args[0][3]], ['16-quick', '16-standard'])


class ChooserWiringTests(unittest.TestCase):
    def test_the_startup_question_sets_the_blocks_and_cancel_keeps_the_last_answer(self):
        launcher = self.load()
        saved = {}
        with patch('nam_catalog.save_setting', side_effect=saved.__setitem__), \
                patch('nam_catalog.nam_pedal', return_value=False), patch('nam_catalog.nam_layers', return_value=True):
            with patch('nam_modes.choose_firmware', return_value='pedal') as ask:
                self.assertEqual(launcher.ask_firmware(), 'pedal')
                self.assertEqual(ask.call_args.args, ('layers',))      # Enter: the last answer
            self.assertEqual(saved, {'namPedal': True, 'namLayers': True})
            with patch('nam_modes.choose_firmware', return_value=None):
                self.assertEqual(launcher.ask_firmware(), 'layers')
            self.assertEqual(saved, {'namPedal': False, 'namLayers': True})
            with patch('nam_modes.choose_firmware', return_value='plain'):
                launcher.ask_firmware()
            self.assertEqual(saved, {'namPedal': False, 'namLayers': False})

    def load(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location('launcher', Path(__file__).parents[1] / 'start-hx-edit-nam.pyw')
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod

    def test_an_import_asks_only_on_the_layers_firmware_and_swaps_in_the_trained_files(self):
        launcher = self.load()
        files = ['C:/a/cab.wav', 'C:/a/amp.nam']
        with patch('nam_catalog.nam_layers', return_value=False):
            self.assertEqual(launcher.choose_and_train({'files': files, 'ask': True}, 'ws'), files)
        with patch('nam_catalog.nam_layers', return_value=True), patch('nam_catalog.nam_pedal', return_value=True), \
                patch('nam_modes.choose_mode', return_value='pedal') as choose, \
                patch('nam_modes.train', return_value=['ws/amp PED.wav']):
            self.assertEqual(launcher.choose_and_train({'files': files, 'ask': True, 'owner': 5}, 'ws'),
                             ['C:/a/cab.wav', 'ws/amp PED.wav'])
            self.assertEqual(choose.call_args.args, (['amp'], 5, True))
        with patch('nam_catalog.nam_layers', return_value=True), patch('nam_catalog.nam_pedal', return_value=False), \
                patch('nam_modes.choose_mode', return_value=None):
            self.assertIsNone(launcher.choose_and_train({'files': files, 'ask': True}, 'ws'))
        self.assertEqual(launcher.choose_and_train({'files': files}, 'ws'), files)       # drag and drop: no ask


if __name__ == '__main__':
    unittest.main()
