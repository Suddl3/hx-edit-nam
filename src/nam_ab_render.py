"""Render one Optimize A/B pair: a few seconds of guitar through the original capture and through its pedal version.

Runs as a child process in the training Python (numpy, torch, neural-amp-modeler), as nam_pedal.py does.
The original is the capture's biggest model (the one the version was fitted to and its quality badge was
measured against), run by neural-amp-modeler; the pedal version is the NAM Pedal block's arithmetic, the
fitter's own play() (helix-lt-nam tools/nam_pedal.py, "the pedal model as the DSP kernel runs it").

Both renders are then loudness-matched: each brought to TARGET_LUFS (ITU-R BS.1770 integrated loudness,
nam_level_measure.loudness), then both lowered by the same amount if either would peak above CEILING.
So the pair differs in tone only, and the level difference it had before matching is reported.

usage: nam_ab_render.py SPEC.json
SPEC: {"fitter": tools/nam_pedal.py, "capture": .nam, "version": .pedal.wav, "source": DI .wav,
       "start": s, "seconds": s, "peak": dBFS or null, "out": folder}
Writes out/original.wav and out/pedal.wav (48 kHz, 16-bit, mono) and out/match.json; prints one JSON line
{"ok": true, ...the match} or {"ok": false, "error": "..."}.
"""
import json
import math
from pathlib import Path
import struct
import sys

RATE = 48000
TARGET_LUFS = -18.0      # where both renders are played: a comfortable listening level, with headroom
CEILING = -1.0           # dBFS: neither render peaks above this after matching
MIN_SECONDS = 4.0        # a shorter take (Level keeps two seconds) is played twice, with a short gap
GAP = 0.25               # s of silence between the repeats
FADE = 0.01              # s fade in and out of each repeat (no click at a cut)
TAIL = 0.3               # s after the playing, for the decay
PEDAL_WORDS = 627        # nam_pedal.N_WORDS: a pedal model's coefficients in a User IR


def read_wav(path):
    """(samples as float64 mono, rate) of a PCM 16/24/32-bit or float 32/64-bit WAV (the stdlib wave
    module refuses float WAVs, which Level's takes are)."""
    import numpy as np
    data = Path(path).read_bytes()
    if data[:4] != b'RIFF' or data[8:12] != b'WAVE':
        raise ValueError(f'{Path(path).name} is not a WAV file')
    position, fmt, body = 12, None, None
    while position + 8 <= len(data):
        tag, size = data[position:position + 4], struct.unpack('<I', data[position + 4:position + 8])[0]
        chunk = data[position + 8:position + 8 + size]
        if tag == b'fmt ':
            fmt = struct.unpack('<HHIIHH', chunk[:16])
            if fmt[0] == 0xFFFE and len(chunk) >= 26:              # WAVE_FORMAT_EXTENSIBLE: the sub-format
                fmt = (struct.unpack('<H', chunk[24:26])[0],) + fmt[1:]
        elif tag == b'data':
            body = chunk
        position += 8 + size + (size & 1)
    if fmt is None or body is None:
        raise ValueError(f'{Path(path).name} has no audio')
    kind, channels, rate, _, _, bits = fmt
    width = bits // 8
    usable = len(body) - len(body) % (width * channels)
    body = body[:usable]
    if kind == 3 and bits in (32, 64):
        x = np.frombuffer(body, '<f4' if bits == 32 else '<f8').astype(np.float64)
    elif kind == 1 and bits == 16:
        x = np.frombuffer(body, '<i2') / 32768.0
    elif kind == 1 and bits == 32:
        x = np.frombuffer(body, '<i4') / 2147483648.0
    elif kind == 1 and bits == 24:
        raw = np.frombuffer(body, np.uint8).reshape(-1, 3).astype(np.int32)
        value = raw[:, 0] | (raw[:, 1] << 8) | (raw[:, 2] << 16)
        x = np.where(value & 0x800000, value - (1 << 24), value) / 8388608.0
    else:
        raise ValueError(f'{Path(path).name}: an unsupported WAV format ({kind}, {bits}-bit)')
    return x.reshape(-1, channels).mean(axis=1), rate


def write_wav(path, x, rate=RATE):
    """16-bit PCM mono: what every player (winsound included) plays."""
    import numpy as np
    pcm = np.clip(np.round(np.asarray(x, np.float64) * 32767), -32768, 32767).astype('<i2').tobytes()
    header = struct.pack('<4sI4s4sIHHIIHH4sI', b'RIFF', 36 + len(pcm), b'WAVE', b'fmt ', 16, 1, 1, rate, rate * 2,
                         2, 16, b'data', len(pcm))
    Path(path).write_bytes(header + pcm)


def pedal_model(path):
    """The pedal model kept in a version's User IR (nam_pedal.ir_words): {a, k1, b, k2, c, k3, d}."""
    import numpy as np
    data = Path(path).read_bytes()
    start = data.find(b'data')
    if data[:4] != b'RIFF' or start < 0:
        raise ValueError('The pedal version is not a User IR')
    size = struct.unpack('<I', data[start + 4:start + 8])[0]
    words = np.frombuffer(data[start + 8:start + 8 + size - size % 4], '<f4').astype(np.float64)
    if len(words) < 8 + PEDAL_WORDS or words[0] != 0.5 or words[1] != -0.5 or words[2] != 3.0:
        raise ValueError('The pedal version is not a pedal model')
    if round(words[4] * 1024) != PEDAL_WORDS:
        raise ValueError('The pedal version holds another model size')
    coefs = words[8:8 + PEDAL_WORDS] * words[3]
    sizes = (('a', 64), ('k1', 65), ('b', 64), ('k2', 65), ('c', 48), ('k3', 65), ('d', 256))
    model, at = {}, 0
    for name, count in sizes:
        model[name] = coefs[at:at + count].tolist()
        at += count
    return model


def guitar(source, start=0.0, seconds=None, peak=None):
    """The DI to play: `seconds` from `start` of the source (all of it when None), at `peak` dBFS when given
    (NAM's file) or as recorded (the player's take); a short take repeated to MIN_SECONDS. 48 kHz."""
    import numpy as np
    x, rate = read_wav(source)
    if rate != RATE:
        raise ValueError(f'The guitar recording must be {RATE} Hz (it is {rate} Hz).')
    x = x[int(start * RATE):int((start + seconds) * RATE) if seconds else None]
    if len(x) < RATE // 2:
        raise ValueError('The guitar recording is too short.')
    top = float(np.max(np.abs(x)))
    if top <= 1e-6:
        raise ValueError('The guitar recording is silent.')
    if peak is not None:
        x = x / top * 10 ** (peak / 20)
    fade = int(FADE * RATE)
    ramp = np.linspace(0.0, 1.0, fade)
    x = x.copy()
    x[:fade] *= ramp
    x[-fade:] *= ramp[::-1]
    pieces = [x]
    while sum(len(p) for p in pieces) < MIN_SECONDS * RATE:
        pieces += [np.zeros(int(GAP * RATE)), x]
    return np.concatenate(pieces + [np.zeros(int(TAIL * RATE))])


def level_match(original, pedal, rate=RATE, target=TARGET_LUFS, ceiling=CEILING, loudness=None):
    """(original, pedal, match): both at the same loudness. match: their loudness before (LUFS), the
    difference (pedal - original, dB), the gain each got and where they were played (LUFS, peak dBFS)."""
    import numpy as np
    if loudness is None:
        from nam_level_measure import loudness
    before = loudness(original, rate), loudness(pedal, rate)
    if not all(math.isfinite(v) for v in before):
        raise ValueError('A render came out silent, so the two cannot be level-matched.')
    gains = [target - v for v in before]
    peak = max(float(np.max(np.abs(s))) * 10 ** (g / 20) for s, g in zip((original, pedal), gains))
    over = 20 * math.log10(peak) - ceiling if peak > 0 else 0.0
    if over > 0:
        gains = [g - over for g in gains]
    original = np.asarray(original, np.float64) * 10 ** (gains[0] / 20)
    pedal = np.asarray(pedal, np.float64) * 10 ** (gains[1] / 20)
    match = {'originalLufs': round(before[0], 2), 'pedalLufs': round(before[1], 2),
             'difference': round(before[1] - before[0], 2), 'originalGain': round(gains[0], 2),
             'pedalGain': round(gains[1], 2), 'lufs': round(target - max(over, 0.0), 2),
             'peak': round(20 * math.log10(max(float(np.max(np.abs(original))), float(np.max(np.abs(pedal))), 1e-12)), 2)}
    return original, pedal, match


def render(spec, run_original, play_pedal, loudness=None):
    """Write the matched pair into spec['out']; return the match. run_original(x) and play_pedal(model, x)
    are the two engines (injected, so this runs without torch in tests)."""
    import numpy as np
    out = Path(spec['out'])
    x = guitar(spec['source'], spec.get('start') or 0.0, spec.get('seconds'), spec.get('peak'))
    model = pedal_model(spec['version'])
    original = np.asarray(run_original(x), np.float64)
    pedal = np.asarray(play_pedal(model, x), np.float64)
    original, pedal, match = level_match(original, pedal, loudness=loudness)
    out.mkdir(parents=True, exist_ok=True)
    write_wav(out / 'original.wav', original)
    write_wav(out / 'pedal.wav', pedal)
    match['seconds'] = round(len(x) / RATE, 2)
    (out / 'match.json').write_text(json.dumps(match), encoding='utf-8')
    return match


def engines(fitter):
    """(run_original, play_pedal) from the pinned fitter's tools: neural-amp-modeler on the capture's
    biggest model, and nam_pedal.play (the kernel's arithmetic)."""
    import numpy as np
    import torch
    sys.path.insert(0, str(Path(fitter).resolve().parent))
    import nam_pedal
    import nam_refine

    def run_original(capture):
        d = json.loads(Path(capture).read_text(encoding='utf-8'))
        teacher = nam_pedal.load_teacher(d)
        # At the level a NAM block plays it: its loudness brought to LOUD_TARGET, as the fitter did for the
        # pedal version's FIR d. So the level difference reported is the one the Floor would have.
        loud = nam_refine.nam_pack_number((d.get('metadata') or {}).get('loudness'))
        gain = 10.0 ** ((nam_pedal.nam_lite.LOUD_TARGET - loud) / 20.0) if loud is not None else 1.0
        return lambda x: nam_refine.run(teacher, np.asarray(x, np.float32), torch).numpy() * gain

    return run_original, nam_pedal.play


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    try:
        spec = json.loads(Path(argv[0]).read_text(encoding='utf-8'))
        load_original, play_pedal = engines(spec['fitter'])
        match = render(spec, load_original(spec['capture']), play_pedal)
        print(json.dumps(dict(match, ok=True)), flush=True)
        return 0
    except SystemExit as error:            # the fitter's helpers stop with SystemExit("...") on a bad capture
        print(json.dumps({'ok': False, 'error': str(error.code)}), flush=True)
    except Exception as error:             # noqa: BLE001 - one JSON line for the editor, whatever failed
        print(json.dumps({'ok': False, 'error': f'{type(error).__name__}: {error}'}), flush=True)
    return 1


if __name__ == '__main__':
    sys.exit(main())
