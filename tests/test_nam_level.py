"""Level presets: the standard, the loudness meter (BS.1770), the measurer and the scan/apply/undo flow. No Floor."""
import json
import math
from pathlib import Path
from queue import Queue
import sys
import tempfile
import unittest

import numpy as np

import nam_level
from nam_level_measure import loudness


class LoudnessTests(unittest.TestCase):
    def test_the_standard_reference_tones(self):
        t = np.arange(48000 * 5) / 48000
        # BS.1770: a 1 kHz sine at 0 dBFS in one channel reads -3.01 LUFS; at -20 dBFS, -23.01.
        self.assertAlmostEqual(loudness(np.sin(2 * np.pi * 1000 * t)), -3.01, delta=0.05)
        self.assertAlmostEqual(loudness(0.1 * np.sin(2 * np.pi * 1000 * t)), -23.01, delta=0.05)
        # The same sine in both channels is 3 dB louder.
        stereo = np.stack([0.1 * np.sin(2 * np.pi * 1000 * t)] * 2, axis=1)
        self.assertAlmostEqual(loudness(stereo), -20.0, delta=0.05)

    def test_silence_and_gating(self):
        self.assertEqual(loudness(np.zeros(48000 * 2)), float('-inf'))
        self.assertEqual(loudness(np.zeros(1000)), float('-inf'))              # shorter than one block
        t = np.arange(48000 * 4) / 48000
        tone = 0.1 * np.sin(2 * np.pi * 1000 * t)
        # Silence after the playing does not pull the reading down (the -70 / -10 LU gates); only the
        # few blocks that straddle the end read a little lower, as BS.1770 defines.
        padded = np.concatenate([tone, np.zeros(48000 * 4)])
        self.assertAlmostEqual(loudness(padded), loudness(tone), delta=0.3)
        self.assertLess(loudness(np.concatenate([tone, np.full(48000 * 4, 1e-6)])) - loudness(tone), 0.01)




class StandardTests(unittest.TestCase):
    def test_default_standard_is_the_middle(self):
        self.assertEqual(nam_level.default_standard([-14.2, -9.0, -12.1]), -12.0)
        self.assertEqual(nam_level.default_standard([-14.0, -10.0]), -12.0)
        self.assertIsNone(nam_level.default_standard([None, float('-inf')]))

    def test_changes_are_held_back_not_clipped(self):
        self.assertEqual(nam_level.check_change(3.0, -9.0), '')
        self.assertIn('12 dB', nam_level.check_change(12.5, -30.0))
        self.assertIn('Would peak at -0.5', nam_level.check_change(2.5, -3.0))
        self.assertEqual(nam_level.check_change(-6.0, -0.2), '')        # turning down is always safe
        self.assertEqual(nam_level.check_change(float('nan'), -9.0), 'Not a number')

    def test_restore_and_undo_files(self):
        with tempfile.TemporaryDirectory() as folder:
            restore, records = Path(folder, 'restore.json'), Path(folder, 'undo.json')
            nam_level.remember_inputs({'preset': {'setlist': 3, 'preset': 30},
                                       'saved': {'inputs': [[0, 'inputA', 1]], 'outputs': [[0, 1]], 'snapshot': 2}},
                                      restore)
            self.assertEqual(nam_level.interrupted(restore)['outputs'], [[0, 1]])
            nam_level.forget_inputs(restore)
            self.assertIsNone(nam_level.interrupted(restore))
            nam_level.forget_inputs(restore)                                 # twice is fine
            nam_level.save_records({'saved': True, 'presets': [{'preset': {'preset': 31}}]}, records)
            self.assertEqual(nam_level.load_records(records)['presets'][0]['preset']['preset'], 31)
            self.assertIn('when', nam_level.load_records(records))
            nam_level.forget_records(records)
            self.assertIsNone(nam_level.load_records(records))


class MeasurerTests(unittest.TestCase):
    def test_serve_answers_one_line_per_request(self):
        import io
        from unittest.mock import patch
        import nam_level_measure
        good = {'ok': True, 'lufs': -12.0, 'peak': -6.0, 'rms': -15.0, 'returned': -6.0, 'seconds': 2.0}
        silent = dict(good, lufs=float('-inf'), peak=-90.0)
        nothing = dict(silent, returned=-90.0)
        answers = [good, silent, nothing]
        out = io.StringIO()
        with patch('nam_level_measure.measure', lambda *args: answers.pop(0)):
            nam_level_measure.serve(io.StringIO('{"di": "x.wav"}\n\n{"di": "x.wav"}\n{"di": "x.wav"}\nnot json\n'), out)
        lines = [json.loads(line) for line in out.getvalue().splitlines()]
        self.assertEqual(lines[0]['lufs'], -12.0)
        self.assertTrue(lines[1]['silent'])                    # a muted snapshot: noted, the scan goes on
        self.assertTrue(lines[2]['setup'])                     # the DI never arrived: the scan stops
        self.assertFalse(lines[3]['ok'])
        self.assertEqual(len(lines), 4)

    def test_meter_keeps_one_process(self):
        import io

        class FakeProcess:
            started = 0

            def __init__(self, args, **_):
                FakeProcess.started += 1
                self.args = args
                self.stdin = io.StringIO()
                self.stdout = io.StringIO(json.dumps({'ok': True, 'lufs': -11.0}) + '\n' +
                                          json.dumps({'ok': True, 'lufs': -13.0}) + '\n')

            def poll(self):
                return None

            def wait(self, timeout=None):
                return 0

        with tempfile.TemporaryDirectory() as folder:
            di = Path(folder, 'di.wav')
            di.write_bytes(b'RIFF')
            meter = nam_level.Meter(di, find_python=lambda _: sys.executable, popen=FakeProcess)
            self.assertEqual(meter.measure()['lufs'], -11.0)
            self.assertEqual(meter.measure()['lufs'], -13.0)
            self.assertEqual(FakeProcess.started, 1)
            self.assertIn('--serve', meter.process.args)
            self.assertIn('"seconds": 2.0', meter.process.stdin.getvalue())
            meter.close()
            missing = nam_level.Meter(Path(folder, 'none.wav'), find_python=lambda _: sys.executable, popen=FakeProcess)
            self.assertIn('missing', missing.measure()['error'])


class FakeHelix:
    """HX Edit's level actions over a pretend Floor: presets 030-032 of setlist 3, two snapshots each."""

    def __init__(self, preset=30, snapshot=1):
        self.preset, self.snapshot = preset, snapshot
        self.loaded, self.saved_at, self.sent = [], 0, []
        self.routing = 'normal'
        self.gain = {n: 0.0 for n in (30, 31, 32)}
        self.undo = None
        self.groups = None       # snapshots that sound alike ([[1, 2]]); None: each its own
        self.names = ['Rhythm', 'Lead']
        self.ties = None         # {snapshot: output Level} when the output Level is on Snapshots
        self.controller = 19
        self.flat = None         # the one Level every snapshot has once the scan re-routes the output
        self.put = []
        self.prepared = False    # the editor's measuring record (kept across preset loads)

    def header(self):
        return {'setlist': 3, 'preset': self.preset, 'name': f'Sound {self.preset}'}

    def load(self, number):
        self.loaded.append(number)
        self.preset, self.snapshot, self.routing, self.undo = number, 1, 'normal', None

    def command(self, action, **data):
        self.sent.append((action, data))
        state = {'preset': self.header(), 'snapshot': self.snapshot, 'savedAt': self.saved_at, 'prepared': False,
                 'snapshots': [{'number': n, 'name': name} for n, name in enumerate(self.names, 1)]}
        if action == 'levelState' and data.get('gain'):
            state['gain'] = self.gain[self.preset]
        if action == 'levelTies':
            if self.ties and self.routing == 'normal' and self.flat is None:
                return {'snapshot': self.snapshot, 'ties': [{'dsp': 0, 'controllers': {'gain': self.controller},
                                                            'values': {'gain': {'type': 8, 'value': self.ties[self.snapshot]}}}]}
            value = self.flat if self.flat is not None else self.gain[self.preset]
            return {'snapshot': self.snapshot, 'ties': [{'dsp': 0, 'controllers': {},
                                                        'values': {'gain': {'type': 8, 'value': value}}}]}
        if action == 'levelTie':
            self.flat = None
        if action == 'levelPut':
            self.put.append((data['snapshot'], data['value']['value']))
        if action == 'levelApplyPlan' and self.prepared:
            raise RuntimeError('Measuring is still in progress.')
        if action in ('levelRestore', 'levelDrop'):
            self.prepared = False
        if action == 'levelPrepare' and data.get('outputs') is False:
            self.prepared = True
            return dict(state, saved={'inputs': [[0, 'inputA', 1]], 'outputs': [[0, 1]], 'snapshot': self.snapshot})
        if action == 'levelPrepare':
            self.prepared = True
            self.routing = 'measuring'
            if self.ties:
                self.flat = self.ties[self.snapshot]       # re-routing drops the controller
            return dict(state, saved={'inputs': [[0, 'inputA', 1]], 'outputs': [[0, 1]], 'snapshot': self.snapshot})
        if action == 'levelRestore':
            self.routing = 'normal'
        if action == 'levelRecall':
            self.snapshot = data['snapshot']
        if action == 'levelApplyPlan':
            self.undo = {'preset': self.header(), 'snapshot': data['home'], 'outputs': [
                {'dsp': 0, 'assigned': False, 'whole': True, 'original': self.gain[self.preset], 'before': {}}]}
            self.gain[self.preset] += next(iter(data['trims'].values()))
            return {'visit': [], 'outputs': [0]}
        if action == 'levelApplyEnd':
            record, self.undo = (None, None) if data.get('forget') else (self.undo, self.undo)
            return dict(state, record=record)
        if action == 'levelUndoLoad':
            assert data['record']['preset']['preset'] == self.preset
            self.undo = data['record']
        if action == 'levelUndoPlan':
            self.gain[self.preset] = self.undo['outputs'][0]['original']
            return {'visit': [], 'snapshot': self.undo['snapshot']}
        if action == 'levelSave':
            assert data['preset']['preset'] == self.preset
            self.saved_at += 1
        if action == 'levelRecover':
            return {'other': True} if data.get('preset', {}).get('preset') != self.preset else dict(state, recovered=True)
        return state


class FakeMeter:
    """readings: one per sound, given twice (a steady sound: both readings agree), unless raw: each
    measure() takes the next one as it is."""

    def __init__(self, readings, raw=False):
        self.readings = readings
        self.raw = raw
        self.again = None
        self.closed = False

    def __call__(self, di):
        return self

    def ready(self):
        return {'ok': True}

    def guitar_level(self, seconds, keep=None, started=None):
        if started:
            started()                              # the countdown is over: recording
        if keep:
            Path(keep).write_bytes(b'RIFF')        # the kept take
        return {'ok': True, 'peak': self.__dict__.get('peak', -21.0), 'playing': 9.5, 'kept': bool(keep)}

    def measure(self, start=0.0, peak=None):
        self.peaks = getattr(self, 'peaks', []) + [peak]
        self.starts = getattr(self, 'starts', []) + [start]
        if self.raw:
            return self.readings.pop(0)
        if self.again is not None:
            reading, self.again = self.again, None
            return reading
        reading = self.readings.pop(0)
        if reading.get('ok'):
            self.again = reading
        return reading

    def close(self):
        self.closed = True


class LevelFlowTests(unittest.TestCase):
    def flow(self, helix, readings=(), recorded=True, nam=None):
        from nam_level_flow import LevelFlow
        folder = tempfile.mkdtemp()
        self.restore, self.records = Path(folder, 'restore.json'), Path(folder, 'undo.json')
        self.meter = FakeMeter(list(readings))
        self.guitar = Path(folder, 'guitar.json')
        if recorded:                               # "Record my guitar" was done: a saved guitar and its take
            take = Path(folder, 'level-guitars', 'g1.wav')
            take.parent.mkdir()
            take.write_bytes(b'RIFF')
            nam_level.save_guitars({'guitars': [{'id': 'g1', 'name': 'Strat', 'peak': -21.0, 'when': None,
                                                 'file': str(take)}], 'last': 'g1'}, Path(folder, 'level-guitars.json'))
        now = [0.0]                                # a pretend clock: sleeping moves it on

        def sleep(seconds):
            now[0] += seconds
        return LevelFlow(helix.command, lambda: None, di=Path(folder, 'di.wav'), meter_factory=self.meter,
                         load_preset=helix.load, restore_file=self.restore, records_file=self.records,
                         guitar_file=self.guitar, nam_input=lambda: nam, sleep=sleep, clock=lambda: now[0])

    def run_job(self, flow, name, *args):
        getattr(flow, name)(*args)
        flow.worker.join(timeout=10)
        return flow.view()

    def reading(self, lufs, peak=-8.0):
        return {'ok': True, 'lufs': lufs, 'peak': peak}

    def test_scan_this_preset_measures_each_snapshot_silently_and_restores(self):
        helix = FakeHelix(snapshot=2)
        flow = self.flow(helix, [self.reading(-12.0), self.reading(-15.0)])
        view = self.run_job(flow, 'scan', [], 8)
        actions = [a for a, _ in helix.sent]
        # Silenced first (before any snapshot is recalled), each snapshot measured, then put back.
        self.assertLess(actions.index('levelPrepare'), actions.index('levelRecall'))
        between = actions[actions.index('levelPrepare'):actions.index('levelRestore') + 1]
        self.assertEqual(between.count('levelRecall'), 2)
        self.assertEqual(between.count('levelRestore'), 1)
        self.assertEqual(helix.loaded, [])                          # the current preset is not reloaded
        self.assertEqual(helix.snapshot, 2)                         # back on the player's snapshot
        self.assertEqual([(r['snapshot'], r['lufs']) for r in view['rows']], [(1, -12.0), (2, -15.0)])
        self.assertIsNone(view['running'])
        self.assertIn('nothing has been changed', view['message'])
        self.assertTrue(self.meter.closed)
        self.assertEqual(self.meter.peaks, [None] * 4)              # the player's take as recorded; 2 readings each
        self.assertEqual(self.meter.starts, [0.0] * 4)
        self.assertFalse(self.restore.exists())

    def test_a_sound_that_moves_on_its_own_is_read_more_often_and_the_middle_counts(self):
        helix = FakeHelix()
        flow = self.flow(helix, [])
        flow.meter_factory = self.meter = FakeMeter([
            self.reading(-12.0, peak=-6.0), self.reading(-14.0, peak=-7.0),     # snapshot 1 disagrees...
            self.reading(-13.0, peak=-5.0), self.reading(-12.5, peak=-6.5),     # ...so 4 readings
            self.reading(-15.0), self.reading(-15.2)], raw=True)                # snapshot 2: steady, 2
        view = self.run_job(flow, 'scan', [], 8)
        rows = {r['snapshot']: r for r in view['rows']}
        self.assertEqual((rows[1]['lufs'], rows[1]['peak'], rows[1]['varies']), (-12.75, -5.0, 1.0))
        self.assertEqual((rows[2]['lufs'], rows[2]['varies']), (-15.1, None))   # the two readings' middle
        self.assertEqual(self.meter.readings, [])

    def test_snapshots_that_measure_the_same_show_as_one_row_and_change_together(self):
        helix = FakeHelix()
        flow = self.flow(helix, [self.reading(-12.0), self.reading(-12.05)])
        view = self.run_job(flow, 'scan', [], 8)
        self.assertEqual([(r['snapshot'], r['snapshots']) for r in view['rows']], [(1, [1, 2])])
        self.assertEqual(view['rows'][0]['level'], 0.0)               # the output Level HX Edit shows
        self.run_job(flow, 'apply', {30: {1: -2.0}})
        plan = next(d for a, d in helix.sent if a == 'levelApplyPlan')
        self.assertEqual(plan['trims'], {1: -2.0, 2: -2.0})          # every snapshot alike: one plain change

    def test_recording_a_guitar_counts_down_and_saves_it_by_name(self):
        from nam_level_flow import LevelError
        flow = self.flow(FakeHelix(), recorded=False)
        progress = []
        flow.changed = lambda: progress.append(flow._view.get('progress'))
        self.assertEqual(flow.view()['guitars'], [])
        with self.assertRaises(LevelError):            # nothing to play yet: Scan waits for a recording
            flow.scan([], 8)
        view = self.run_job(flow, 'measure_guitar', 10.0, 'Strat · neck')
        self.assertEqual([(g['name'], g['peak']) for g in view['guitars']], [('Strat · neck', -21.0)])
        self.assertEqual(view['lastGuitar'], view['guitars'][0]['id'])
        self.assertIn('-21.0 dBFS', view['message'])
        counted = [p for p in progress if p and p.startswith('Get ready')]
        self.assertEqual([p[-1] for p in counted], ['3', '2', '1'])          # 3-2-1, then recording
        self.assertTrue(any(p and p.startswith('Recording') for p in progress))
        # A second guitar; the same name again replaces that one.
        self.run_job(flow, 'measure_guitar', 10.0, '')
        self.meter.peak = -18.0
        view = self.run_job(flow, 'measure_guitar', 10.0, 'strat · NECK')
        self.assertEqual(sorted((g['name'], g['peak']) for g in view['guitars']),
                         [('My guitar', -21.0), ('strat · NECK', -18.0)])
        self.assertEqual(len(list(flow.takes.glob('*.wav'))), 2)          # the replaced take is gone

    def test_a_saved_guitar_can_be_deleted(self):
        from nam_level_flow import LevelError
        flow = self.flow(FakeHelix(), recorded=False)
        self.run_job(flow, 'measure_guitar', 10.0, 'Strat')
        self.run_job(flow, 'measure_guitar', 10.0, 'Tele')
        strat, tele = flow.view()['guitars']
        flow.remove_guitar(tele['id'])
        view = flow.view()
        self.assertEqual([g['name'] for g in view['guitars']], ['Strat'])
        self.assertEqual(view['lastGuitar'], strat['id'])           # the one left is used next
        self.assertEqual(len(list(flow.takes.glob('*.wav'))), 1)    # its recording is gone too
        self.assertIn('Tele', view['message'])
        with self.assertRaises(LevelError):
            flow.remove_guitar(tele['id'])

    def test_a_recording_from_before_names_becomes_my_guitar(self):
        flow = self.flow(FakeHelix(), recorded=False)
        nam_level.save_guitar(-21.0, self.guitar)       # level-guitar.json and level-take.wav (the last version)
        self.guitar.with_name('level-take.wav').write_bytes(b'RIFF')
        self.assertEqual([g['name'] for g in flow.view()['guitars']], ['My guitar'])
        only_number = self.flow(FakeHelix(), recorded=False)
        nam_level.save_guitar(-21.0, self.guitar)       # older still: the number only, nothing to play
        self.assertEqual(only_number.view()['guitars'], [])

    def test_a_scan_uses_the_chosen_guitar_and_apply_remembers_it(self):
        helix = FakeHelix()
        flow = self.flow(helix, [self.reading(-12.0), self.reading(-12.0)])
        self.run_job(flow, 'scan', [], 8, 'g1')
        view = self.run_job(flow, 'apply', {30: {1: -2.0}}, {'value': -14.0, 'metric': 'lufs'})
        self.assertEqual(list(view['leveled']), ['3:30:Sound 30'])
        last = view['leveled']['3:30:Sound 30']
        self.assertEqual((last['with'], last['target'], last['metric']), ('Strat', -14.0, 'lufs'))
        view = self.run_job(flow, 'undo')                   # Undo: the note before it (none) is back
        self.assertEqual(view['leveled'], {})

    def test_nams_input_file_plays_its_guitar_part_at_the_players_level(self):
        from nam_level_flow import LevelError
        helix = FakeHelix()
        flow = self.flow(helix, [self.reading(-12.0), self.reading(-15.0)], nam=Path('v3_0_0.wav'))
        self.assertTrue(flow.view()['nam'])
        self.run_job(flow, 'scan', [], 8, 'nam')
        self.assertEqual(self.meter.peaks, [-21.0] * 4)
        self.assertEqual(self.meter.starts, [nam_level.NAM_START] * 4)
        missing = self.flow(FakeHelix())
        self.assertFalse(missing.view()['nam'])
        with self.assertRaises(LevelError):
            missing.scan([], 8, 'nam')
        # No guitar recorded: NAM's file still works, at a typical humbucker's level.
        bare = self.flow(FakeHelix(), [self.reading(-12.0), self.reading(-15.0)], recorded=False, nam=Path('v3_0_0.wav'))
        self.run_job(bare, 'scan', [], 8, 'nam')
        self.assertEqual(self.meter.peaks, [nam_level.DEFAULT_PEAK] * 4)

    def test_nams_input_file_is_found_by_its_length_not_its_name(self):
        import wave
        from unittest.mock import patch
        folder = Path(tempfile.mkdtemp())
        for name, seconds in (('input.wav', 0.5), ('v3_0_0.wav', 1.0)):
            with wave.open(str(folder / name), 'wb') as audio:
                audio.setnchannels(1); audio.setsampwidth(2); audio.setframerate(48000)
                audio.writeframes(bytes(2 * int(48000 * seconds)))
        with patch.object(nam_level, 'NAM_SECONDS', (0.9, 1.1)):
            self.assertEqual(nam_level.nam_input([folder / 'input.wav', folder / 'v3_0_0.wav']), folder / 'v3_0_0.wav')
            self.assertIsNone(nam_level.nam_input([folder / 'input.wav', folder / 'none.wav']))

    def test_the_kept_take_is_steady_playing_at_its_middle_loudness(self):
        import numpy as np
        import nam_level_measure
        rate = 1000
        quiet, loud, silence = np.full(3000, 0.05), np.full(3000, 0.5), np.zeros(3000)
        middle = np.full(3000, 0.2)
        take = nam_level_measure.typical_take(np.concatenate([silence, quiet, middle, loud, silence]), 2.0, rate)
        self.assertEqual(len(take), 2000)
        self.assertTrue(np.all(take > 0))                       # no silence in it
        self.assertAlmostEqual(float(np.median(take)), 0.2)     # not the loudest, not the quietest

    def test_serve_measures_the_guitar_level_on_request(self):
        import io
        from unittest.mock import patch
        import nam_level_measure
        out = io.StringIO()
        def recording(seconds, keep=None, started=None):
            started()
            return {'ok': True, 'peak': -21.0, 'seconds': seconds}
        with patch('nam_level_measure.guitar_level', recording):
            nam_level_measure.serve(io.StringIO('{"mode": "guitar", "seconds": 10}\n'), out)
        lines = [json.loads(line) for line in out.getvalue().splitlines()]
        self.assertEqual(lines[0], {'recording': True})              # the moment recording begins
        self.assertEqual(lines[1]['peak'], -21.0)

    def test_the_page_follows_the_preset_hx_edit_is_on(self):
        import time
        helix = FakeHelix()
        flow = self.flow(helix)
        flow.WATCH = 0.02
        flow.open()
        self.assertEqual(flow.view()['current']['preset']['preset'], 30)
        helix.load(31)
        for _ in range(100):
            if flow.view()['current']['preset']['preset'] == 31:
                break
            time.sleep(0.02)
        self.assertEqual(flow.view()['current']['preset']['preset'], 31)
        flow.close()

    def test_an_output_level_on_snapshots_is_read_first_corrected_for_and_put_back(self):
        helix = FakeHelix()
        helix.ties = {1: 0.0, 2: -6.0}
        # Read last on snapshot 2, the output is re-routed there: every snapshot then plays at -6.0 and
        # measures the same; snapshot 1 (really 0.0) is put right by +6.
        flow = self.flow(helix, [self.reading(-12.0, peak=-8.0), self.reading(-12.0, peak=-8.0)])
        view = self.run_job(flow, 'scan', [], 8)
        self.assertEqual([(r['snapshot'], r['lufs'], r['peak'], r['level']) for r in view['rows']],
                         [(1, -6.0, -2.0, 0.0), (2, -12.0, -8.0, -6.0)])
        self.assertEqual(sorted(helix.put), [(1, 0.0), (2, -6.0)])          # every snapshot's Level back
        self.assertIn('levelTie', [a for a, _ in helix.sent])
        actions = [a for a, _ in helix.sent]
        self.assertLess(actions.index('levelRestore'), actions.index('levelTie'))
        self.assertIsNone(view['error'])

    def test_an_output_on_a_pedal_is_left_alone(self):
        helix = FakeHelix()
        helix.ties, helix.controller = {1: 0.0, 2: 0.0}, 3
        flow = self.flow(helix, [self.reading(-12.0)])
        view = self.run_job(flow, 'scan', [], 8)
        self.assertIn('pedal or another controller', view['error'])
        prepares = [d for a, d in helix.sent if a == 'levelPrepare']
        self.assertEqual(prepares, [{'outputs': False}])            # the guitar only; the output never moved
        self.assertIn('levelRestore', [a for a, _ in helix.sent])

    def test_apply_after_scanning_another_preset_is_not_blocked(self):
        helix = FakeHelix(preset=31)
        flow = self.flow(helix, [self.reading(-12.0), self.reading(-15.0)])
        self.run_job(flow, 'scan', [30], 2)
        self.assertFalse(helix.prepared)
        view = self.run_job(flow, 'apply', {30: {1: -1.0}})
        self.assertIsNone(view['error'])
        self.assertEqual(helix.saved_at, 1)

    def test_the_player_chooses_all_snapshots_the_first_four_or_just_one(self):
        from nam_level_flow import scanned_snapshots
        snaps = [{'number': n, 'name': f'SNAPSHOT {n}'} for n in range(1, 9)]
        self.assertEqual(len(scanned_snapshots(snaps, 'all')), 8)
        self.assertEqual([s['number'] for s in scanned_snapshots(snaps, 4)], [1, 2, 3, 4])
        self.assertEqual([s['number'] for s in scanned_snapshots(snaps, 1)], [1])
        helix = FakeHelix()
        flow = self.flow(helix, [self.reading(-12.0)])
        view = self.run_job(flow, 'scan', [], 1)
        self.assertEqual([r['snapshot'] for r in view['rows']], [1])
        self.assertEqual(view['skipped'], {30: [2]})

    def test_scan_several_presets_loads_each_and_comes_back(self):
        helix = FakeHelix(preset=31, snapshot=2)
        flow = self.flow(helix, [self.reading(-10.0 - i) for i in range(6)])
        view = self.run_job(flow, 'scan', [30, 31, 32], 2)
        self.assertEqual(helix.loaded, [30, 32, 31])                # 031 (current) last: no extra load
        self.assertEqual([a for a, _ in helix.sent].count('levelRestore'), 1)   # only 031 is put back
        self.assertEqual((helix.preset, helix.snapshot), (31, 2))
        self.assertEqual([r['preset'] for r in view['rows']], [30, 30, 31, 31, 32, 32])
        self.assertEqual(helix.routing, 'normal')

    def test_a_silent_snapshot_is_noted_and_a_missing_signal_stops(self):
        helix = FakeHelix()
        flow = self.flow(helix, [{'ok': False, 'silent': True, 'error': 'No sound came out of this snapshot.'},
                                 self.reading(-12.0)])
        view = self.run_job(flow, 'scan', [], 8)
        self.assertEqual(view['rows'][0]['note'], 'No sound came out of this snapshot.')
        self.assertEqual(view['rows'][1]['lufs'], -12.0)
        helix = FakeHelix()
        flow = self.flow(helix, [{'ok': False, 'setup': True, 'error': 'The test signal did not reach the Floor.'}])
        view = self.run_job(flow, 'scan', [], 8)
        self.assertIn('did not reach', view['error'])
        self.assertIn('levelRestore', [a for a, _ in helix.sent])
        self.assertEqual(helix.routing, 'normal')

    def test_cancel_stops_between_snapshots_and_restores(self):
        helix = FakeHelix()
        flow = self.flow(helix, [self.reading(-12.0), self.reading(-13.0)])
        flow.cancelled.set()
        flow._start = lambda name, target, *args: target(*args)       # run here, cancelled from the start
        message = flow._scan([], 8, flow._signal('mine'))
        self.assertIn('Cancelled', message)
        self.assertEqual(helix.routing, 'normal')

    def test_apply_to_this_preset_changes_without_saving_and_undo_puts_it_back(self):
        helix = FakeHelix()
        flow = self.flow(helix, [self.reading(-12.0), self.reading(-12.0)])
        self.run_job(flow, 'scan', [], 8)
        view = self.run_job(flow, 'apply', {30: {1: -2.0}})
        self.assertEqual(helix.gain[30], -2.0)
        self.assertEqual([(r['lufs'], r['applied'], r['level']) for r in view['rows']], [(-14.0, -2.0, -2.0)])
        self.assertEqual(helix.saved_at, 0)
        self.assertIn('Not saved', view['message'])
        self.assertEqual(view['undo']['presets'], [30])
        view = self.run_job(flow, 'undo')
        self.assertEqual(helix.gain[30], 0.0)
        self.assertEqual([(r['lufs'], r.get('applied')) for r in view['rows']], [(-12.0, None)])
        self.assertIsNone(view['undo'])
        self.assertIn('Undone: 1', view['message'])

    def test_apply_to_several_presets_saves_each_and_undo_restores_them(self):
        helix = FakeHelix(preset=30, snapshot=2)
        flow = self.flow(helix, [self.reading(-12.0) for _ in range(4)])
        self.run_job(flow, 'scan', [30, 31], 2)
        view = self.run_job(flow, 'apply', {30: {1: 1.5}, 31: {1: -3.0}})
        self.assertEqual((helix.gain[30], helix.gain[31]), (1.5, -3.0))
        self.assertEqual(helix.saved_at, 2)                          # each preset saved on the Floor
        self.assertEqual((helix.preset, helix.snapshot), (30, 2))   # back where the player was
        self.assertTrue(view['undo']['saved'])
        records = nam_level.load_records(self.records)
        self.assertEqual([e['preset']['preset'] for e in records['presets']], [30, 31])
        self.assertTrue(all(e['saved'] for e in records['presets']))
        view = self.run_job(flow, 'undo')
        self.assertEqual((helix.gain[30], helix.gain[31]), (0.0, 0.0))
        self.assertEqual(helix.saved_at, 4)
        self.assertEqual(helix.preset, 30)
        self.assertIsNone(view['undo'])

    def test_apply_refuses_what_was_not_measured_or_would_clip(self):
        from nam_level_flow import LevelError
        helix = FakeHelix()
        flow = self.flow(helix, [self.reading(-12.0, peak=-2.0), self.reading(-12.0)])
        self.run_job(flow, 'scan', [], 8)
        with self.assertRaises(LevelError):
            flow.apply({30: {1: 3.0}})                               # -2 + 3 passes the -1 dBFS ceiling
        with self.assertRaises(LevelError):
            flow.apply({31: {1: 1.0}})                               # never measured
        with self.assertRaises(LevelError):
            flow.apply({30: {2: 0.0}})                               # nothing to change
        self.assertEqual(helix.gain[30], 0.0)

    def test_undo_of_an_unsaved_apply_after_a_preset_change_does_nothing(self):
        helix = FakeHelix()
        flow = self.flow(helix, [self.reading(-12.0), self.reading(-12.0)])
        self.run_job(flow, 'scan', [], 8)
        self.run_job(flow, 'apply', {30: {1: -2.0}})
        helix.load(31)                                               # the unsaved change went with 030
        view = self.run_job(flow, 'undo')
        self.assertEqual(helix.loaded, [31])
        self.assertIn('never saved', view['message'])

    def test_opening_recovers_a_cut_short_measurement(self):
        helix = FakeHelix()
        flow = self.flow(helix)
        record = {'preset': {'setlist': 3, 'preset': 30}, 'inputs': [[0, 'inputA', 1]], 'outputs': [[0, 1]],
                  'snapshot': 1}
        self.restore.write_text(json.dumps(record), encoding='utf-8')
        flow.open()
        self.assertEqual(helix.sent[1], ('levelRecover', record))
        self.assertFalse(self.restore.exists())
        self.assertIn('cut short', flow.view()['message'])
        record['preset']['preset'] = 31                              # another preset: kept for later
        self.restore.write_text(json.dumps(record), encoding='utf-8')
        flow.open()
        self.assertTrue(self.restore.exists())


class LevelPageTests(unittest.TestCase):
    def page(self):
        return (Path(__file__).resolve().parents[1] / 'src').joinpath('library_card.html').read_text(encoding='utf-8')

    def test_every_level_button_talks_to_the_editor(self):
        import re
        page = self.page()
        panel = page[page.index('<section id="levelPanel"'):]
        panel = panel[:panel.index('</section>')]
        buttons = re.findall(r'<button id="(\w+)"', panel)
        self.assertEqual(set(buttons), {'levelMeasureGuitar', 'levelRemoveGuitar', 'levelScan', 'levelCancel', 'levelApply', 'levelUndo'})
        for button in buttons:
            self.assertIn("q('#%s').onclick" % button, page, button)

    def test_the_page_script_parses(self):
        import shutil
        import subprocess
        node = shutil.which('node')
        if not node:
            self.skipTest('Node.js is not installed')
        page = self.page()
        script = page[page.index('<script>') + 8:page.rindex('</script>')]
        for name, value in (('$rows', '[]'), ('$icons', '{}'), ('$iconGroups', '[]'), ('$view', '{}')):
            script = script.replace(name, value)
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder, 'page.js')
            path.write_text(script, encoding='utf-8')
            result = subprocess.run([node, '--check', str(path)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_the_level_page_is_not_in_the_library(self):
        page = self.page()
        self.assertNotIn('id="levelOpen"', page)                     # no Level… button among the library's
        self.assertIn('body.levelMode > header', page)

    def test_the_browser_opens_the_level_page(self):
        from threading import Event
        from unittest.mock import Mock
        from nam_library import Library
        from tone_preview import TonePreview
        folder = tempfile.mkdtemp()
        di = Path(folder, 'di.wav')
        di.write_bytes(b'RIFF')
        browser = Mock()
        browser.actions = Queue()
        clicks = ['level']
        browser.next_action = lambda: (browser.actions.get_nowait() if not browser.actions.empty()
                                       else clicks.pop(0) if clicks else 'finish')
        preview = TonePreview(Mock(), folder, Path(folder, 'tools', 'nam2ir.py'), Event(), Mock(),
                              browser_factory=Mock(), library=Library(Path(folder, 'library')), level=True, level_di=di,
                              level_files={'restore_file': Path(folder, 'r.json'), 'records_file': Path(folder, 'u.json'),
                                           'guitar_file': Path(folder, 'guitar.json')})
        helix = FakeHelix()
        preview.command = lambda session, action, **data: helix.command(action, **data)
        preview.browse(browser, 1, '')
        sent = [call.kwargs for call in browser.send.call_args_list if call.args and call.args[0] == 'status']
        self.assertTrue(sent[-1]['levelPage'])
        self.assertEqual(sent[-1]['level']['current']['preset']['preset'], 30)
        # Apply with the page's target: answered on the page, the window carries on (a name clash with
        # browse()'s own target() once closed it: Floor PC, November 16).
        browser.send.reset_mock()
        clicks[:] = ['level', 'levelApply:' + json.dumps({'changes': {'30': {'1': -2.0}},
                                                          'target': {'value': -14.0, 'metric': 'lufs'}})]
        preview.browse(browser, 1, '')
        sent = [call.kwargs for call in browser.send.call_args_list if call.args and call.args[0] == 'status']
        self.assertTrue(any('has not been measured' in str(s.get('text', '')) + str(s.get('message', '')) for s in sent))
        # Opened from the Level button: the Level page straight away, without a click.
        browser.send.reset_mock()
        preview.initial_level = preview.initial_library = True
        preview.browse(browser, 1, '')
        sent = [call.kwargs for call in browser.send.call_args_list if call.args and call.args[0] == 'status']
        self.assertTrue(sent[-1]['levelPage'])


if __name__ == '__main__':
    unittest.main()
