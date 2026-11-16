"""Train a lighter A2 Lite of a library capture with helix-lt-nam's tools/nam_refine.py --layers.

A light capture keeps only the first N of the A2 Lite's 23 WaveNet layers (the rest are zero), so a
firmware that skips zero layers runs it for less DSP. It stays an ordinary A2 Lite .nam: the converter
(nam2ir.py) writes N into the User IR. Training distils the capture's own bigger model, so the .nam
must hold one (a TONE3000 A2 file with Full + Lite, or a standard WaveNet).

Training needs torch and neural-amp-modeler, which the launcher's Python does not carry: the first
Python here that imports both is used (HX_EDIT_NAM_TRAIN_PYTHON, then helix-lt-nam's .venv, then
the launcher's own).
"""

import os
from pathlib import Path
import re
import subprocess
import sys
from threading import Thread


# Layers -> a Helix LT DSP's share for one block (measured on the unit, NAM4_LAYERS firmware, 2026-11-12).
LAYERS = {23: '~36%', 20: '~33%', 16: '~29%', 14: '~28%'}
STEPS = {'quick': 800, 'standard': 3000, 'deep': 10000}
MISSING = ('Training a lighter capture needs Python with torch and neural-amp-modeler. Create '
           'helix-lt-nam\\.venv as its README describes (NAM Studio), or set HX_EDIT_NAM_TRAIN_PYTHON '
           'to a python.exe that has them, then try again.')
NO_WINDOW = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0

STEP = re.compile(r'step (\d+)/(\d+): held-out ESR ([\d.]+) \(best ([\d.]+)\)')
FULL = re.compile(r'the full Lite: held-out ESR ([\d.]+)')
DONE = re.compile(r'error against the teacher (?:([\d.]+)%[^>]*-> )?([\d.]+)%')


def python_candidates(refine):
    checkout = Path(refine).resolve().parent.parent
    names = [os.environ.get('HX_EDIT_NAM_TRAIN_PYTHON', ''),
             checkout / '.venv' / 'Scripts' / 'python.exe', checkout / '.venv' / 'bin' / 'python', sys.executable]
    return [str(name) for name in names if name and Path(name).is_file()]


def training_python(refine):
    """The first candidate Python that imports torch and has nam, or None. (Not `import nam`: the package's
    bundled Python leaves out nam's trainer and librosa, which helix-lt-nam's tools stand in for.)"""
    probe = 'import torch, scipy, importlib.util as u; assert u.find_spec("nam")'
    for python in python_candidates(refine):
        try:
            found = subprocess.run([python, '-c', probe], capture_output=True, timeout=180,
                                   creationflags=NO_WINDOW)
        except (OSError, subprocess.TimeoutExpired):
            continue
        if found.returncode == 0:
            return python
    return None


def parse(line, state):
    """Update state from one line of nam_refine's output; True when something changed."""
    if match := STEP.search(line):
        state.update(step=int(match[1]), steps=int(match[2]), best=float(match[4]))
    elif match := FULL.search(line):
        state['before'] = float(match[1])
    elif match := DONE.search(line):
        if match[1] and 'before' not in state:
            state['before'] = round(float(match[1]) / 100, 6)
        state['after'] = round(float(match[2]) / 100, 6)
    else:
        return False
    return True


class LightJob:
    """nam_refine.py on one capture in a child process; on_event(state) after every change and once
    at the end with state['done'] True, or state['error'] (cancelled: state['cancelled'])."""

    def __init__(self, refine, capture, output, layers, steps, on_event, *, find_python=training_python):
        self.refine, self.capture, self.output = Path(refine), Path(capture), Path(output)
        self.layers, self.steps, self.on_event = int(layers), int(steps), on_event
        self.find_python = find_python
        self.process = None
        self.cancelled = False
        self.state = {'layers': self.layers, 'step': 0, 'steps': self.steps}
        self.thread = Thread(target=self.run, daemon=True)
        self.thread.start()

    def cancel(self):
        self.cancelled = True
        if self.process and self.process.poll() is None:
            self.process.terminate()

    def run(self):
        state, last = self.state, ''
        try:
            if not self.refine.is_file():
                raise RuntimeError('This build has no helix-lt-nam tools/nam_refine.py to train with.')
            python = self.find_python(self.refine)
            if python is None:
                raise RuntimeError(MISSING)
            if self.cancelled:
                raise RuntimeError('Cancelled.')
            self.process = subprocess.Popen(
                [python, '-u', '-B', '-X', 'utf8', str(self.refine), str(self.capture), '-o', str(self.output),
                 '--layers', str(self.layers), '--steps', str(self.steps)],
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, encoding='utf-8', errors='replace',
                creationflags=NO_WINDOW)
            with self.process.stdout as output:
                for line in output:
                    if line.strip():
                        last = line.strip()
                    if parse(line, state):
                        self.on_event(dict(state))
            if self.process.wait() or not self.output.is_file():
                raise RuntimeError('Cancelled.' if self.cancelled else
                                   last.replace(str(self.capture), self.capture.name) or 'Training failed.')
            state['done'] = True
        except (OSError, RuntimeError) as error:
            state['error'] = str(error)
            state['cancelled'] = self.cancelled
        self.on_event(dict(state))
