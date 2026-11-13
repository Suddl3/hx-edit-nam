"""Synthetic binary fixtures cover actual file-format gates, no user captures."""
from pathlib import Path
import struct
import tempfile
import unittest

from floor_capture import FORMAT, validate_floor_capture_files


def chunk(kind, data):
    return kind + struct.pack('<I', len(data)) + data + (b'\0' if len(data) & 1 else b'')


def wav(*, layers=0.0, count=1871 / 1024, scale=2.0, fmt=FORMAT, data=None, junk=False, data_first=False):
    if data is None:
        data = struct.pack('<8f', .5, -.5, 3, scale, count, 0, 0, layers) + bytes(8192 - 32)
    parts = [chunk(b'fmt ', struct.pack('<HHIIHH', *fmt)), chunk(b'data', data)]
    if data_first:
        parts.reverse()
    body = b'WAVE' + (chunk(b'JUNK', b'odd') if junk else b'') + b''.join(parts)
    return b'RIFF' + struct.pack('<I', len(body)) + body


class FloorCaptureTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name, 'Synthetic.wav')

    def check(self, content):
        self.path.write_bytes(content)
        validate_floor_capture_files([self.path])

    def test_original_and_explicit23_layers_allowed(self):
        for layers in (0, 23 / 32):
            self.check(wav(layers=layers, junk=True))

    def test_new_shorter_engines_blocked(self):
        for layers in (14 / 32, 16 / 32, 20 / 32, float('nan'), float('inf')):
            with self.assertRaisesRegex(ValueError, 'shortened NAM'):
                self.check(wav(layers=layers))

    def test_pedal_and_unknown_formats_blocked(self):
        for count in (627 / 1024, 1000 / 1024):
            with self.assertRaisesRegex(ValueError, 'needs firmware'):
                self.check(wav(count=count))

    def test_scale_must_be_supported_power_of_two(self):
        for scale in (0, 1.5, 128):
            with self.assertRaisesRegex(ValueError, 'scale'):
                self.check(wav(scale=scale))

    def test_nam_at_wrong_sample_rate_blocked(self):
        with self.assertRaisesRegex(ValueError, 'container'):
            self.check(wav(fmt=(3, 1, 44100, 176400, 4, 32)))

    def test_short_payload_with_signature_blocked(self):
        with self.assertRaisesRegex(ValueError, 'container'):
            self.check(wav(data=struct.pack('<3f', .5, -.5, 3)))

    def test_truncated_and_oversized_chunk_blocked(self):
        for broken in (wav()[:-1], b'RIFF', b'RIFF\x0c\0\0\0WAVEdata\xff\xff\xff\xff'):
            with self.assertRaises(ValueError):
                self.check(broken)

    def test_ordinary_cab_ir_remains_available(self):
        self.check(wav(fmt=(1, 1, 48000, 96000, 2, 16), data=bytes(4096)))

    def test_data_before_fmt_and_odd_metadata(self):
        self.check(wav(junk=True, data_first=True))

    def test_format_gate_does_not_process_hir_or_unconverted_nam(self):
        validate_floor_capture_files([Path(self.directory.name, 'cab.hir'), Path(self.directory.name, 'original.nam')])


if __name__ == '__main__':
    unittest.main()
