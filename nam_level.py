"""Level presets: bring every preset and snapshot to one output standard (PRODUCT-VISION "Level presets").

The player picks a standard, a loudness (LUFS) or a peak (dBFS), and the Level page measures each
chosen preset's snapshots on the Floor: a short guitar DI is played into the preset (input USB 7/8)
and its final output is read back from USB 1/2, with the output switched to USB only meanwhile so
nothing comes out of the amp. Each snapshot then gets a suggested output-level change:

  change = standard - measured      (dB; only the output level moves, never the tone)

A change is held back, never clipped silently, when it is larger than MAX_TRIM or when the sound's
measured peak plus the change would pass CEILING. Measuring runs nam_level_measure.py in the
training Python (numpy, sounddevice); this module runs in the editor's own Python.
"""

import ctypes
from datetime import datetime
import json
import math
import os
from pathlib import Path
import subprocess
from threading import Lock

import nam_light

MAX_TRIM = 12.0          # dB either way: more than that is a different sound, not a level
CEILING = -1.0           # dBFS: room left above a sound's measured peak after its change
SECONDS = 2.0            # of DI per snapshot
# The test signal must be real playing at a real guitar's level: an amp block squashes a hot signal and a
# clean preset does not, so a signal played too hot makes clean presets read far louder than they sound
# with a guitar (the first tests, at -6 dBFS). Hence the player's own recording, played as recorded.
# The player's own guitar level (its peak), measured with their recording ("Record my guitar").
GUITAR_FILE = Path(os.environ.get('LOCALAPPDATA', Path.home())) / 'HX Edit NAM' / 'level-guitar.json'
# The test signal: two seconds of the player's own playing, kept from "Record my guitar" (on this PC
# only; nothing is bundled). Played back as recorded, so it is at their guitar's own level.
TAKE_FILE = GUITAR_FILE.with_name('level-take.wav')
# Or NAM's standard reamp file (the "v3" input.wav of Neural Amp Modeler, github.com/sdatkinson/
# neural-amp-modeler), when the player already has it: never bundled, only looked for. Its guitar part
# from NAM_START (the file opens with test tones and noise), played at the player's measured peak.
NAM_NAMES = ('nam_v3_input.wav', 'v3_0_0.wav', 'input.wav')
NAM_SECONDS = (189.0, 191.0)   # the v3 file is 190 s at 48 kHz: any other input.wav is not it
NAM_START = 64.0


# Saved guitars: each recording ("Strat · neck") with its level, so a guitar is recorded once and picked
# again later. {'guitars': [{id, name, peak, when, file}], 'last': id}. Takes live in GUITARS_DIR.
GUITARS_FILE = GUITAR_FILE.with_name('level-guitars.json')
DEFAULT_PEAK = -14.0     # dBFS: a humbucker's level, for NAM's file when no guitar has been recorded
# Each preset's last Apply: "setlist:preset:name" -> {when, with (the guitar), target, metric}.
HISTORY_FILE = GUITAR_FILE.with_name('level-history.json')


def _read_json(path):
    try:
        value = json.loads(Path(path).read_text(encoding='utf-8'))
        return value if isinstance(value, dict) else None
    except (OSError, ValueError):
        return None


def _write_json(value, path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding='utf-8')


def load_guitars(path=GUITARS_FILE, legacy=GUITAR_FILE):
    """{'guitars': [...], 'last': id or None}: only guitars whose take is still there. A recording from
    before guitars had names (legacy level-guitar.json with its take) becomes "My guitar"."""
    data = _read_json(path)
    if data is None:
        data = {'guitars': [], 'last': None}
        old = load_guitar(legacy)
        if old and old.get('take'):
            take = Path(legacy).with_name(TAKE_FILE.name)
            data = {'guitars': [{'id': 'g1', 'name': 'My guitar', 'peak': old['peak'], 'when': old.get('when'),
                                 'file': str(take)}], 'last': 'g1'}
    guitars = [g for g in data.get('guitars') or [] if isinstance(g, dict) and isinstance(g.get('peak'), (int, float))
               and g.get('id') and Path(str(g.get('file', ''))).is_file()]
    last = data.get('last') if any(g['id'] == data.get('last') for g in guitars) else (guitars[-1]['id'] if guitars else None)
    return {'guitars': guitars, 'last': last}


def save_guitars(data, path=GUITARS_FILE):
    _write_json({'guitars': data.get('guitars') or [], 'last': data.get('last')}, path)


def guitar_name(name, guitars):
    """The name a new recording gets: the one typed, else "My guitar" (or "Guitar 2", "Guitar 3"...)."""
    name = ' '.join(str(name or '').split())[:40]
    if name:
        return name
    names = {g['name'].lower() for g in guitars}
    if 'my guitar' not in names:
        return 'My guitar'
    n = 2
    while f'guitar {n}' in names:
        n += 1
    return f'Guitar {n}'


def load_history(path=HISTORY_FILE):
    return _read_json(path) or {}


def history_key(preset):
    """A preset's key in the history: its setlist, number and name (a preset moved or replaced in that
    slot does not inherit it)."""
    return f'{preset.get("setlist") if preset.get("setlist") is not None else ""}:{preset.get("preset")}:{preset.get("name") or ""}'


def nam_input(extra=()):
    """The path of NAM's v3 input file on this PC, or None: HX Edit NAM's folder, Downloads, Documents,
    and `extra` (paths given by a build). Checked by its length and rate, not only its name."""
    import wave
    home = Path.home()
    folders = [GUITAR_FILE.parent, home / 'Downloads', home / 'Documents']
    candidates = [Path(p) for p in extra if p] + [folder / name for folder in folders for name in NAM_NAMES]
    for path in candidates:
        try:
            with wave.open(str(path), 'rb') as audio:
                rate, frames = audio.getframerate(), audio.getnframes()
        except (OSError, EOFError, wave.Error):
            continue
        if rate == 48000 and NAM_SECONDS[0] <= frames / rate <= NAM_SECONDS[1]:
            return path
    return None


def save_guitar(peak, path=GUITAR_FILE):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({'peak': float(peak), 'when': datetime.now().isoformat(timespec='seconds')}),
                    encoding='utf-8')


def load_guitar(path=GUITAR_FILE, take=None):
    """{'peak', 'when', 'take'} of the player's measured guitar ('take': their recording is there to
    play), or None."""
    try:
        guitar = json.loads(Path(path).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None
    if not isinstance(guitar, dict) or not isinstance(guitar.get('peak'), (int, float)):
        return None
    take = Path(take) if take else Path(path).with_name(TAKE_FILE.name)
    return dict(guitar, take=take.is_file())
MEASURER = Path(__file__).with_name('nam_level_measure.py')
FOLDER = Path(os.environ.get('LOCALAPPDATA', Path.home())) / 'HX Edit NAM'
# While measuring, the preset's inputs are on USB 7/8 and its outputs on USB 1/2 (unsaved). What they
# were is kept here too, so a crash mid-measure is put right the next time the Level page opens.
INTERRUPTED = FOLDER / 'level-restore.json'
# The last Apply: each preset's old output levels, for Undo (also after the app was closed).
RECORDS = FOLDER / 'level-undo.json'
_measuring_python = {}   # measurer -> the Python found for it, once per session


def measuring_python(measurer):
    """The first candidate Python with numpy, sounddevice and soundfile (not torch: the training probe's
    torch import made every measurement about 20 s slower). Remembered for the session."""
    key = str(measurer)
    if key not in _measuring_python:
        _measuring_python[key] = None
        for python in nam_light.python_candidates(measurer):
            try:
                found = subprocess.run([python, '-c', 'import numpy, sounddevice, soundfile'], capture_output=True,
                                       timeout=60, creationflags=nam_light.NO_WINDOW)
            except (OSError, subprocess.TimeoutExpired):
                continue
            if found.returncode == 0:
                _measuring_python[key] = python
                break
    return _measuring_python[key]


def remember_inputs(state, path=INTERRUPTED, ties=()):
    """Keep {preset, inputs, outputs, snapshot, ties} from a levelPrepare reply ('saved': the editor's
    record); ties: each snapshot's output Level / Pan that the re-routing dropped (nam_level_flow)."""
    saved = state.get('saved') or {}
    if not saved.get('inputs') or not state.get('preset'):
        return
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({'preset': state['preset'], 'inputs': saved['inputs'],
                                'outputs': saved.get('outputs') or [], 'snapshot': saved.get('snapshot'),
                                'ties': list(ties)}),
                    encoding='utf-8')


def interrupted(path=INTERRUPTED):
    """The routing a cut-short measurement left behind, or None."""
    try:
        saved = json.loads(Path(path).read_text(encoding='utf-8'))
        return saved if isinstance(saved, dict) and isinstance(saved.get('inputs'), list) else None
    except (OSError, ValueError):
        return None


def forget_inputs(path=INTERRUPTED):
    try:
        Path(path).unlink()
    except FileNotFoundError:
        pass


def default_standard(values):
    """The middle of what was measured (median, to 0.5 dB): the standard that moves the fewest sounds."""
    values = sorted(v for v in values if isinstance(v, (int, float)) and math.isfinite(v))
    if not values:
        return None
    middle = len(values) // 2
    median = values[middle] if len(values) % 2 else (values[middle - 1] + values[middle]) / 2
    return round(median * 2) / 2 + 0.0


def check_change(change, peak):
    """'' when a change may be applied, else why it is held back."""
    if not isinstance(change, (int, float)) or not math.isfinite(change):
        return 'Not a number'
    if abs(change) > MAX_TRIM:
        return f'More than {MAX_TRIM:g} dB: check the sound itself'
    if isinstance(peak, (int, float)) and math.isfinite(peak) and peak + change > CEILING:
        return f'Would peak at {peak + change:+.1f} dBFS (limit {CEILING:g})'
    return ''


def save_records(records, path=RECORDS):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({'when': datetime.now().isoformat(timespec='seconds'), **records}), encoding='utf-8')


def load_records(path=RECORDS):
    try:
        records = json.loads(Path(path).read_text(encoding='utf-8'))
        return records if isinstance(records, dict) and isinstance(records.get('presets'), list) else None
    except (OSError, ValueError):
        return None


def forget_records(path=RECORDS):
    try:
        Path(path).unlink()
    except FileNotFoundError:
        pass


class Meter:
    """nam_level_measure.py --serve in a child process, kept for a whole scan: measure() returns the
    measurer's JSON (ok, lufs, peak, rms, returned; or ok False and error). Not thread-safe by itself:
    one scan uses it at a time."""

    def __init__(self, di=None, *, measurer=MEASURER, find_python=None, popen=subprocess.Popen):
        self.di = Path(di) if di else None
        self.measurer = Path(measurer)
        self.find_python = find_python or measuring_python
        self.popen = popen
        self.process = None
        self.lock = Lock()

    def _start(self):
        python = self.find_python(self.measurer)
        if python is None:
            raise RuntimeError('Level presets needs numpy, sounddevice and soundfile in HX Edit NAM’s Python '
                               '(FLOOR-SETUP.md, Updating an existing clone).')
        self.process = self.popen([python, '-u', '-B', '-X', 'utf8', str(self.measurer), '--serve'],
                                  stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                  encoding='utf-8', errors='replace', creationflags=nam_light.NO_WINDOW)

    def measure(self, *, start=0.0, seconds=SECONDS, peak=None):
        """peak: the level the signal is played at (dBFS), or None for as recorded."""
        if self.di is None or not self.di.is_file():
            return {'ok': False, 'error': 'The test signal is missing: record your guitar first.'}
        return self._ask({'di': str(self.di), 'start': start, 'seconds': seconds, 'peak': peak})

    def guitar_level(self, seconds=10.0, keep=None, started=None):
        """The player's guitar: {'ok', 'peak' (dBFS), 'playing' (s), 'kept'} from the Floor's dry input;
        keep: where two seconds of it are written, the test signal for scans. started(): called the
        moment recording begins (for the countdown on the page)."""
        return self._ask({'mode': 'guitar', 'seconds': seconds, 'keep': str(keep) if keep else None}, started)

    def ready(self):
        """Start the measurer with its audio libraries and the Floor's device found, so a recording begins
        on time after the countdown."""
        return self._ask({'mode': 'ready'})

    def _ask(self, request, started=None):
        with self.lock:
            try:
                if self.process is None or self.process.poll() is not None:
                    self._start()
                self.process.stdin.write(json.dumps(request) + '\n')
                self.process.stdin.flush()
                while True:
                    line = self.process.stdout.readline()
                    if not line:
                        self.process = None
                        return {'ok': False, 'error': 'The measurer stopped.'}
                    answer = json.loads(line)
                    if answer.get('recording'):        # an interim line: recording has begun
                        if started:
                            started()
                        continue
                    return answer
            except (OSError, RuntimeError, ValueError) as error:
                return {'ok': False, 'error': str(error)}

    def close(self):
        process, self.process = self.process, None
        if process is None:
            return
        try:
            process.stdin.close()
            process.wait(timeout=3)
        except (OSError, ValueError, subprocess.TimeoutExpired):
            process.kill()


def load_preset(number, *, device='Helix', channel=1):
    """Load preset `number` (000-127) of the Floor's current setlist with a MIDI Program Change over USB,
    as a footswitch or MIDI controller would. HX Edit follows on its own. Windows (winmm) only."""
    if not 0 <= int(number) <= 127:
        raise ValueError('Preset numbers are 000 to 127.')
    winmm = ctypes.WinDLL('winmm')

    class Caps(ctypes.Structure):
        _fields_ = [('wMid', ctypes.c_ushort), ('wPid', ctypes.c_ushort), ('vDriverVersion', ctypes.c_uint),
                    ('szPname', ctypes.c_wchar * 32), ('wTechnology', ctypes.c_ushort),
                    ('wVoices', ctypes.c_ushort), ('wNotes', ctypes.c_ushort),
                    ('wChannelMask', ctypes.c_ushort), ('dwSupport', ctypes.c_ulong)]
    port = None
    for index in range(winmm.midiOutGetNumDevs()):
        caps = Caps()
        if winmm.midiOutGetDevCapsW(index, ctypes.byref(caps), ctypes.sizeof(caps)) == 0 and device in caps.szPname:
            port = index
            break
    if port is None:
        raise RuntimeError('The Floor\'s MIDI port was not found (is it connected over USB?).')
    handle = ctypes.c_void_p()
    if winmm.midiOutOpen(ctypes.byref(handle), port, None, None, 0) != 0:
        raise RuntimeError('The Floor\'s MIDI port is busy (another program has it open).')
    try:
        if winmm.midiOutShortMsg(handle, (0xC0 | (channel - 1)) | (int(number) << 8)) != 0:
            raise RuntimeError('The preset change could not be sent.')
    finally:
        winmm.midiOutClose(handle)
