"""Optimize A/B on this PC: hear a capture's pedal version against the original before the Floor can play it.

"Listen: Original / Pedal" in the version panel renders a few seconds of the player's own guitar (the take
Level presets kept with "Record my guitar"; else the guitar part of NAM's v3 input file at a typical
guitar's level) through the original capture and through the pedal version, level-matched, and plays either
on the PC's default output (not the Floor). The render runs nam_ab_render.py in the training Python and is
kept, so the next listen of the same guitar and the same version plays at once (RENDERS, KEEP pairs).

This module runs in the editor's own Python: no numpy here, only the child process has it.
"""

from hashlib import sha256
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from threading import Thread

import nam_light
import nam_level

RENDERER = Path(__file__).with_name('nam_ab_render.py')
RENDERS = nam_level.GUITAR_FILE.parent / 'ab-renders'
KEEP = 24                    # pairs kept; the oldest go first
REVISION = 1                 # bump when a render would come out different: older ones are not reused
NAM_SECONDS = 6.0            # of NAM's file (from nam_level.NAM_START, its guitar part)
WHICH = ('original', 'pedal')
HASHED = 8 << 20             # files up to this size are known by their bytes; larger (NAM's file) by size and time


def pick_source(guitars=None, nam_file=None, extra=()):
    """What to play through both: the player's last saved guitar, else NAM's input file at a typical
    guitar's peak, else None. guitars: nam_level.load_guitars(); nam_file: nam_level.nam_input(extra)."""
    guitars = guitars if guitars is not None else nam_level.load_guitars()
    chosen = next((g for g in guitars.get('guitars') or [] if g['id'] == guitars.get('last')), None)
    if chosen:
        return {'kind': 'guitar', 'path': str(chosen['file']), 'name': chosen.get('name') or 'My guitar',
                'start': 0.0, 'seconds': None, 'peak': None}
    if nam_file is None:
        nam_file = nam_level.nam_input(extra)
    if nam_file:
        return {'kind': 'nam', 'path': str(nam_file), 'name': 'NAM test guitar', 'start': nam_level.NAM_START,
                'seconds': NAM_SECONDS, 'peak': nam_level.DEFAULT_PEAK}
    return None


def fingerprint(path):
    """A file's content id: its bytes' hash, or for a big file its name, size and modification time."""
    path = Path(path)
    status = path.stat()
    if status.st_size <= HASHED:
        return sha256(path.read_bytes()).hexdigest()
    return f'{path.resolve()}|{status.st_size}|{status.st_mtime_ns}'


def cache_key(source, capture, version):
    """The pair's id: the guitar (file and part), the original capture and the version, and REVISION.
    Re-recording the guitar or optimizing again makes a new pair; the old one ages out."""
    parts = [REVISION, fingerprint(source['path']), source.get('start'), source.get('seconds'), source.get('peak'),
             fingerprint(capture), fingerprint(version)]
    return sha256(json.dumps(parts).encode('utf-8')).hexdigest()[:32]


class Renders:
    """Kept pairs: <folder>/<key>/original.wav, pedal.wav, match.json."""

    def __init__(self, folder=RENDERS, keep=KEEP):
        self.folder, self.keep = Path(folder), keep

    def path(self, key, which):
        if which not in WHICH:
            raise ValueError(f'Unknown render: {which}')
        return self.folder / key / f'{which}.wav'

    def lookup(self, key):
        """The pair's match (with 'key') when both renders are kept, else None."""
        try:
            match = json.loads((self.folder / key / 'match.json').read_text(encoding='utf-8'))
        except (OSError, ValueError):
            return None
        if not isinstance(match, dict) or not all(self.path(key, w).is_file() for w in WHICH):
            return None
        try:
            os.utime(self.folder / key)                 # recently heard: kept longest
        except OSError:
            pass
        return dict(match, key=key)

    def prune(self):
        try:
            pairs = sorted((p for p in self.folder.iterdir() if p.is_dir()), key=lambda p: p.stat().st_mtime,
                           reverse=True)
        except OSError:
            return
        for old in pairs[self.keep:]:
            shutil.rmtree(old, ignore_errors=True)


def match_text(match):
    """What the panel says about the level match."""
    if not match:
        return ''
    difference = match.get('difference')
    if not isinstance(difference, (int, float)):
        return 'Level-matched.'
    louder = ('the same level as' if abs(difference) < 0.5 else
              f'{abs(difference):.1f} dB {"louder" if difference > 0 else "quieter"} than')
    return f'Level-matched at {match.get("lufs", -18):.0f} LUFS (the Pedal version came out {louder} the original).'


class RenderJob:
    """nam_ab_render.py for one pair in a child process. on_event(state) once at the end: {'done': True,
    'match': {...}} or {'error': ...}. The pair is written to a temporary folder and moved into place, so
    a cancelled or failed render never leaves half a pair behind."""

    def __init__(self, renders, key, spec, on_event, *, find_python=nam_light.training_python, renderer=RENDERER):
        self.renders, self.key, self.spec, self.on_event = renders, key, dict(spec), on_event
        self.find_python, self.renderer = find_python, Path(renderer)
        self.process = None
        self.cancelled = False
        self.thread = Thread(target=self.run, daemon=True)
        self.thread.start()

    def cancel(self):
        self.cancelled = True
        if self.process is not None and self.process.poll() is None:
            self.process.kill()

    def run(self):
        state = {}
        work = None
        try:
            fitter = Path(self.spec['fitter'])
            if not fitter.is_file():
                raise RuntimeError('This build has no helix-lt-nam tools/nam_pedal.py.')
            python = self.find_python(fitter)
            if python is None:
                raise RuntimeError(nam_light.MISSING.replace('Training a lighter capture', 'Listening to a version'))
            if self.cancelled:
                raise RuntimeError('Cancelled.')
            self.renders.folder.mkdir(parents=True, exist_ok=True)
            work = Path(tempfile.mkdtemp(prefix='.render-', dir=self.renders.folder))
            spec = dict(self.spec, out=str(work / 'pair'))
            (work / 'spec.json').write_text(json.dumps(spec), encoding='utf-8')
            self.process = subprocess.Popen([python, '-u', '-B', '-X', 'utf8', str(self.renderer), str(work / 'spec.json')],
                                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, encoding='utf-8',
                                            errors='replace', creationflags=nam_light.NO_WINDOW)
            output, _ = self.process.communicate()
            if self.cancelled:
                raise RuntimeError('Cancelled.')
            answer = None
            for line in reversed(output.splitlines()):
                try:
                    answer = json.loads(line)
                    break
                except ValueError:
                    continue
            if not isinstance(answer, dict):
                last = next((line.strip() for line in reversed(output.splitlines()) if line.strip()), '')
                raise RuntimeError(last or 'The render failed.')
            if not answer.get('ok'):
                raise RuntimeError(str(answer.get('error') or 'The render failed.'))
            target = self.renders.folder / self.key
            shutil.rmtree(target, ignore_errors=True)
            (work / 'pair').replace(target)
            self.renders.prune()
            match = self.renders.lookup(self.key)
            if match is None:
                raise RuntimeError('The render was not kept.')
            state.update(done=True, match=match)
        except (OSError, RuntimeError, ValueError, KeyError) as error:
            state.update(error=str(error), cancelled=self.cancelled)
        finally:
            if work is not None:
                shutil.rmtree(work, ignore_errors=True)
        self.on_event(state)


FLOOR_OUTPUT = ('helix', 'line 6')   # a default output with one of these names is the Floor's USB audio


def default_output():
    """The name of Windows' preferred playback device (what winsound plays on; at most 31 characters,
    e.g. "Speakers (Realtek(R) Audio)"), or None when it cannot be read."""
    if sys.platform != 'win32':
        return None
    import ctypes
    from ctypes import wintypes

    class Caps(ctypes.Structure):
        _fields_ = [('wMid', wintypes.WORD), ('wPid', wintypes.WORD), ('vDriverVersion', wintypes.UINT),
                    ('szPname', wintypes.WCHAR * 32), ('dwFormats', wintypes.DWORD), ('wChannels', wintypes.WORD),
                    ('wReserved1', wintypes.WORD), ('dwSupport', wintypes.DWORD)]
    try:
        winmm = ctypes.WinDLL('winmm')
        device, flags = wintypes.DWORD(), wintypes.DWORD()
        # DRVM_MAPPER_PREFERRED_GET on WAVE_MAPPER: the device the mapper plays on
        if winmm.waveOutMessage(wintypes.HANDLE(0xFFFFFFFF), 0x2015, ctypes.byref(device), ctypes.byref(flags)):
            return None
        caps = Caps()
        if winmm.waveOutGetDevCapsW(ctypes.c_size_t(device.value), ctypes.byref(caps), ctypes.sizeof(caps)):
            return None
        return caps.szPname or None
    except (OSError, AttributeError):
        return None


class Player:
    """Plays one WAV on the PC's default output, the previous one stopped. Windows: winsound (in the
    embeddable Python); macOS: afplay. Refuses when Windows' default output is the Floor itself: A/B is
    for the PC's speakers or headphones (output_name: default_output, replaced in tests)."""

    def __init__(self, output_name=default_output):
        self.process = None
        self.output_name = output_name

    def play(self, path):
        self.stop()
        name = self.output_name()
        if name and any(word in name.lower() for word in FLOOR_OUTPUT):
            raise OSError(f'Windows plays sound on "{name}", the Floor. Choose your PC\'s speakers or '
                          'headphones as the default output in Windows, then listen again.')
        if sys.platform == 'win32':
            import winsound
            winsound.PlaySound(str(path), winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_NODEFAULT)
        elif sys.platform == 'darwin':
            self.process = subprocess.Popen(['afplay', str(path)])
        else:
            raise OSError('Playback on this PC is not available.')

    def stop(self):
        if sys.platform == 'win32':
            try:
                import winsound
                winsound.PlaySound(None, 0)
            except (ImportError, RuntimeError):
                pass
        if self.process is not None and self.process.poll() is None:
            self.process.kill()
        self.process = None
