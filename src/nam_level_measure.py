"""Measure one sound on the Floor: replay a guitar DI to USB 7/8, record the main output (USB in 1/2).

Runs as a child process in a Python with numpy and sounddevice (the training Python), because the
editor's own runtime has neither. Prints one JSON line:
  {"ok": true, "lufs": -9.0, "peak": -2.0, "rms": -11.3, "returned": -6.0, "seconds": 6.0}
or {"ok": false, "error": "..."}.

The preset's input must already be USB 7/8 (the editor switches it and back). Loudness is ITU-R
BS.1770-4 integrated loudness (K-weighting, 400 ms blocks, -70 LUFS absolute and -10 LU relative
gates), the measure broadcast and streaming level matching use. 'returned' is the peak of the DI
as the Floor sends it back on USB in 7 (its dry input), so a run without signal is caught.

With --serve it stays running for a whole scan: each line on stdin is one measurement
({"di": .., "start": .., "seconds": .., "peak": ..}) and each answer is one JSON line, so the start-up
(numpy, the audio device) is paid once instead of once per snapshot.

usage: nam_level_measure.py --di DI.wav [--start 20] [--seconds 2] [--peak -6]
       nam_level_measure.py --serve
"""
import argparse
import json
import math
import sys

RATE = 48000
DEVICE_IN = 'Digital Audio Interface (Line 6 Helix)'
DEVICE_OUT = 'Line 6 Helix'


def biquad(x, b, a):
    """One second-order filter, in float64: scipy's when this Python has it, else a plain loop."""
    import numpy as np
    try:
        from scipy.signal import lfilter
        return lfilter(b, a, x)
    except ImportError:
        pass
    y = np.zeros_like(x)
    x1 = x2 = y1 = y2 = 0.0
    b0, b1, b2 = b
    _, a1, a2 = a
    for n, v in enumerate(x):
        out = b0 * v + b1 * x1 + b2 * x2 - a1 * y1 - a2 * y2
        x2, x1, y2, y1 = x1, v, y1, out
        y[n] = out
    return y


def k_weighting(rate):
    """BS.1770 K-weighting: the high shelf and the high pass, for any sample rate."""
    f0, g, q = 1681.974450955533, 3.999843853973347, 0.7071752369554196
    k = math.tan(math.pi * f0 / rate)
    vh = 10 ** (g / 20)
    vb = vh ** 0.4996667741545416
    a0 = 1 + k / q + k * k
    shelf = ((vh + vb * k / q + k * k) / a0, 2 * (k * k - vh) / a0, (vh - vb * k / q + k * k) / a0), \
            (1.0, 2 * (k * k - 1) / a0, (1 - k / q + k * k) / a0)
    f0, q = 38.13547087602444, 0.5003270373238773
    k = math.tan(math.pi * f0 / rate)
    a0 = 1 + k / q + k * k
    high = (1.0, -2.0, 1.0), (1.0, 2 * (k * k - 1) / a0, (1 - k / q + k * k) / a0)
    return shelf, high


def loudness(samples, rate=RATE):
    """Integrated loudness in LUFS of samples shaped (n,) or (n, channels); -inf for silence."""
    import numpy as np
    x = np.asarray(samples, dtype='float64')
    if x.ndim == 1:
        x = x[:, None]
    shelf, high = k_weighting(rate)
    weighted = np.stack([biquad(biquad(x[:, c], *shelf), *high) for c in range(x.shape[1])], axis=1)
    block, step = int(0.4 * rate), int(0.1 * rate)
    if len(weighted) < block:
        return float('-inf')
    starts = range(0, len(weighted) - block + 1, step)
    power = np.array([np.sum(np.mean(weighted[s:s + block] ** 2, axis=0)) for s in starts])
    with np.errstate(divide='ignore'):
        levels = -0.691 + 10 * np.log10(power)
    kept = power[levels > -70]
    if not len(kept):
        return float('-inf')
    relative = -0.691 + 10 * np.log10(np.mean(kept)) - 10
    kept = power[(levels > -70) & (levels > relative)]
    return float(-0.691 + 10 * np.log10(np.mean(kept))) if len(kept) else float('-inf')


def db(value):
    return 20 * math.log10(value) if value > 0 else float('-inf')


def find_device(name, kind):
    import sounddevice as sd
    for index, device in enumerate(sd.query_devices()):
        if (sd.query_hostapis(device['hostapi'])['name'] == 'Windows WDM-KS' and name in device['name']
                and device[f'max_{kind}_channels'] >= 8):
            return index
    raise RuntimeError('The Floor is not available as a USB audio device (WDM-KS, 8 channels).')


def measure(di_path, start=20.0, seconds=2.0, peak=-6.0):
    """peak: the level the DI is set to (dBFS), or None to play it as recorded."""
    import numpy as np
    import sounddevice as sd
    import soundfile as sf
    di, rate = sf.read(di_path, dtype='float32', always_2d=True)
    if rate != RATE:
        raise RuntimeError(f'The DI must be {RATE} Hz (it is {rate} Hz).')
    take = di[int(start * RATE):int((start + seconds) * RATE), 0]
    if len(take) < RATE:
        raise RuntimeError('The DI is too short for this start and length.')
    if peak is not None:
        take = take / max(1e-9, float(np.max(np.abs(take)))) * 10 ** (peak / 20)
    # A short lead for the stream to start; a short tail for the Floor's latency (delay and reverb tails
    # are left out on purpose: the level is the playing, not the decay).
    lead, tail = np.zeros(int(0.2 * RATE), 'float32'), np.zeros(int(0.3 * RATE), 'float32')
    signal = np.concatenate([lead, take, tail])
    out = np.zeros((len(signal), 8), dtype='float32')
    out[:, 6] = out[:, 7] = signal                      # USB out 7/8: the reamp input
    devices = find_device(DEVICE_IN, 'input'), find_device(DEVICE_OUT, 'output')
    recorded = sd.playrec(out, samplerate=RATE, channels=8, device=devices, dtype='float32')
    sd.wait()
    body = recorded[len(lead):len(lead) + len(take)]
    main = recorded[:, :2]
    return {'ok': True, 'lufs': round(loudness(main), 2), 'peak': round(db(float(np.max(np.abs(body[:, :2])))), 2),
            'rms': round(db(float(np.sqrt(np.mean(body[:, :2] ** 2)))), 2),
            'returned': round(db(float(np.max(np.abs(body[:, 6])))), 2), 'seconds': round(len(take) / RATE, 1)}


def typical_take(guitar, seconds=2.0, rate=RATE):
    """From a take (mono), the `seconds` of steady playing at its middle loudness: among the windows
    with sound all through, the one whose level is the median (not the loudest strum, not a pause)."""
    import numpy as np
    window, size = int(0.1 * rate), int(seconds * rate)
    count = len(guitar) // window
    level = np.sqrt(np.mean(guitar[:count * window].reshape(count, window) ** 2, axis=1))
    playing = level > 10 ** (-50 / 20)
    span = int(round(seconds / 0.1))
    starts = [i for i in range(0, count - span + 1)]
    full = [i for i in starts if playing[i:i + span].all()]
    pool = full or sorted(starts, key=lambda i: -playing[i:i + span].sum())[:max(1, len(starts) // 4)]
    pool = sorted(pool, key=lambda i: float(np.mean(level[i:i + span] ** 2)))
    first = pool[len(pool) // 2] * window
    return guitar[first:first + size]


def guitar_level(seconds=10.0, keep=None, started=None):
    """How hot the player's guitar is: the highest peak (dBFS) at the Floor's dry input (USB in 7)
    while they play for `seconds`. keep: a path where two typical seconds of it are written (the test
    signal for scans, on this PC only); otherwise only the number is kept."""
    import numpy as np
    import sounddevice as sd
    device = find_device(DEVICE_IN, 'input')
    if started:
        started()
    recorded = sd.rec(int(seconds * RATE), samplerate=RATE, channels=8, device=device, dtype='float32')
    sd.wait()
    guitar = recorded[:, 6].astype('float64')
    window = int(0.1 * RATE)
    count = len(guitar) // window
    blocks = guitar[:count * window].reshape(count, window)
    playing = np.sqrt(np.mean(blocks ** 2, axis=1)) > 10 ** (-50 / 20)
    if playing.sum() * 0.1 < 3:
        return {'ok': False, 'error': 'Not enough playing came in (at least 3 seconds). Is the guitar plugged '
                                      'into the Floor\'s guitar input?'}
    result = {'ok': True, 'peak': round(db(float(np.max(np.abs(blocks[playing])))), 1),
              'playing': round(float(playing.sum()) * 0.1, 1), 'kept': False}
    if keep:
        import soundfile as sf
        sf.write(keep, typical_take(guitar).astype('float32'), RATE, subtype='FLOAT')
        result['kept'] = True
    return result


def checked(result):
    if 'lufs' in result and (result['lufs'] == float('-inf') or result['peak'] < -60):
        if result.get('returned', 0) < -60:
            return {'ok': False, 'setup': True,
                    'error': 'The test signal did not reach the Floor. Is the preset\'s input set to USB 7/8?'}
        return {'ok': False, 'silent': True, 'error': 'No sound came out of this snapshot.'}
    return result


def serve(lines=sys.stdin, out=sys.stdout):
    """One measurement per input line, one JSON answer per line, until stdin closes."""
    for line in lines:
        if not line.strip():
            continue
        try:
            request = json.loads(line)
            if request.get('mode') == 'ready':
                import numpy, sounddevice, soundfile   # noqa: F401 (loaded now, not after the countdown)
                find_device(DEVICE_IN, 'input')
                out.write(json.dumps({'ok': True}) + '\n')
                out.flush()
                continue
            if request.get('mode') == 'guitar':
                def started():
                    out.write(json.dumps({'recording': True}) + '\n')
                    out.flush()
                out.write(json.dumps(guitar_level(float(request.get('seconds', 10.0)), request.get('keep'), started)) + '\n')
                out.flush()
                continue
            peak = request.get('peak', -6.0)
            result = checked(measure(request['di'], float(request.get('start', 20.0)),
                                     float(request.get('seconds', 2.0)), None if peak is None else float(peak)))
        except Exception as error:      # one line per request, never a traceback
            result = {'ok': False, 'error': str(error)}
        out.write(json.dumps(result) + '\n')
        out.flush()


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('--di')
    parser.add_argument('--serve', action='store_true')
    parser.add_argument('--start', type=float, default=20.0)
    parser.add_argument('--seconds', type=float, default=2.0)
    parser.add_argument('--peak', type=float, default=-6.0)
    args = parser.parse_args(argv)
    if args.serve:
        serve()
        return 0
    try:
        if not args.di:
            raise RuntimeError('Give --di or --serve.')
        result = checked(measure(args.di, args.start, args.seconds, args.peak))
    except Exception as error:      # reported to the editor as one line, never a traceback
        result = {'ok': False, 'error': str(error)}
    print(json.dumps(result), flush=True)
    return 0 if result['ok'] else 1


if __name__ == '__main__':
    sys.exit(main())
