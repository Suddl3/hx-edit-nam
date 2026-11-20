"""Optimize: a lighter version of one library capture, kept beside the original (nam_library.VERSIONS).

Only the NAM Pedal version exists so far: helix-lt-nam's tools/nam_pedal.py fits a small fixed model
(four FIRs and three curves) to a pedal capture in about half a minute. It plays in a NAM Pedal block,
which the Floor firmware does not have yet, so a version can be made and kept here but not used on the
Floor until it does (PRODUCT-VISION "Optimize: versions of one capture"). Amps get a layer-skipping
"Light" version once that firmware exists.
"""

from hashlib import sha256
from pathlib import Path
import re
import subprocess

import nam_light

# Gear types a pedal fit suits (upstream: boosts and overdrives 0.4-1.7% error, a fuzz 6%; amps 10-18%).
PEDAL_GEARS = {'pedal'}
FITTER = 'nam_pedal.py'                # beside nam2ir.py / nam_refine.py in helix-lt-nam's tools
STEP = re.compile(r'step (\d+)/(\d+): weighted error ([\d.]+)')
CHECK = re.compile(r'held-out ESR ([\d.]+)%')


def version_for(gear):
    """The version Optimize makes for a capture of this gear type, or None (no fit suits it yet)."""
    return 'pedal' if (gear or '').lower() in PEDAL_GEARS else None


def fitter_path(converter):
    """The fitter: a pinned helix-lt-nam-fit tool set beside helix-lt-nam when the build has one (its
    helpers are newer than the converter's), else next to the converter."""
    converter = Path(converter)
    pinned = converter.parents[2] / 'helix-lt-nam-fit' / 'tools' / FITTER if len(converter.parents) > 2 else None
    return pinned if pinned is not None and pinned.is_file() else converter.with_name(FITTER)


def converter_id(fitter):
    """A short id of the fitter that made a version: a new fitter can be told from the one before."""
    name = Path(fitter).stem
    try:
        return name + ' ' + sha256(Path(fitter).read_bytes()).hexdigest()[:12]
    except OSError:
        return name


LIGHT_KEY, LIGHT_LAYERS, LIGHT_STEPS = 'light16', 16, 3000   # a Light version: 16 of 23 layers, Standard training


def light_trainer(converter):
    """nam_refine.py, beside the fitter (the pinned helix-lt-nam-fit tools when the build has them)."""
    return fitter_path(converter).with_name('nam_refine.py')


class OptimizeJob(nam_light.LightJob):
    """nam_pedal.py --check on one capture in a child process. Events as LightJob's (step, steps, best;
    then done or error); the done event also has 'esr', the fit's held-out ESR (0..1) when reported."""

    def run(self):
        state, last = self.state, ''
        try:
            if not self.refine.is_file():
                raise RuntimeError('This build has no helix-lt-nam tools/nam_pedal.py.')
            python = self.find_python(self.refine)
            if python is None:
                raise RuntimeError(nam_light.MISSING)
            if self.cancelled:
                raise RuntimeError('Cancelled.')
            self.process = subprocess.Popen(
                [python, '-u', '-B', '-X', 'utf8', str(self.refine), str(self.capture), '-o', str(self.output),
                 '-v', '--check'],
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, encoding='utf-8', errors='replace',
                creationflags=nam_light.NO_WINDOW)
            with self.process.stdout as output:
                for line in output:
                    if line.strip():
                        last = line.strip()
                    if m := STEP.search(line):
                        state.update(step=int(m[1]), steps=int(m[2]), best=float(m[3]))
                        self.on_event(dict(state))
                    elif m := CHECK.search(line):
                        state['esr'] = float(m[1]) / 100
            if self.process.wait() or not self.output.is_file():
                raise RuntimeError('Cancelled.' if self.cancelled else
                                   last.replace(str(self.capture), self.capture.name) or 'The fit failed.')
            state['done'] = True
        except (OSError, RuntimeError) as error:
            state['error'] = str(error)
            state['cancelled'] = self.cancelled
        self.on_event(dict(state))
