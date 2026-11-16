"""Import boundary for the original Floor A2 Lite format, without device access.

Format source: andreidevs/helix-lt-nam, helix-lt-2026-11-13,
tools/nam2ir.py. Header word7 is layers/32 (zero means all23). The Floor
reference supports the ordinary23-layer payload, not the newer shorter engines
or the627-word pedal model. This verifies format compatibility, not audio quality
or that a particular image is installed. Normal cabinet IRs remain native imports.
"""

from pathlib import Path
import struct


SIGNATURE = (0x3F000000, 0xBF000000, 0x40400000)
A2_WORDS = 0x3FE9E000  # 1871/1024 as float32
FORMAT = (3, 1, 48000, 192000, 4, 32)


def validate_floor_capture_files(files):
    for filename in files:
        path = Path(filename)
        if path.suffix.lower() != '.wav':
            continue
        _validate_wav(path)


def _validate_wav(path):
    with path.open('rb') as stream:
        header = stream.read(12)
        if len(header) != 12 or header[:4] != b'RIFF' or header[8:] != b'WAVE':
            raise ValueError(f'{path.name}: invalid WAV container.')
        end = struct.unpack_from('<I', header, 4)[0] + 8
        if end < 12 or end > path.stat().st_size:
            raise ValueError(f'{path.name}: incomplete WAV container.')
        fmt = None
        captures = []
        while stream.tell() < end:
            chunk = stream.read(8)
            if len(chunk) != 8:
                raise ValueError(f'{path.name}: incomplete WAV chunk.')
            kind, size = struct.unpack('<4sI', chunk)
            next_chunk = stream.tell() + size + (size & 1)
            if next_chunk > end:
                raise ValueError(f'{path.name}: incomplete WAV chunk.')
            if kind == b'fmt ' and size >= 16:
                fmt = struct.unpack('<HHIIHH', stream.read(16))
            elif kind == b'data' and size >= 12:
                head = stream.read(min(size, 32))
                if struct.unpack_from('<3I', head) == SIGNATURE:
                    captures.append((size, head))
            stream.seek(next_chunk)
        for size, head in captures:
            if fmt != FORMAT or size != 8192 or len(head) != 32:
                raise ValueError(f'{path.name}: unsupported NAM container for the Floor A2 Lite profile.')
            words = struct.unpack('<8I', head)
            if words[4] != A2_WORDS:
                raise ValueError(f'{path.name}: this NAM format needs firmware outside the Floor A2 Lite profile.')
            if not (0x3F800000 <= words[3] <= 0x42800000 and words[3] & 0x007FFFFF == 0):
                raise ValueError(f'{path.name}: invalid NAM capture scale.')
            layers = struct.unpack_from('<f', head, 28)[0]
            if layers not in (0.0, 23 / 32):
                raise ValueError(f'{path.name}: shortened NAM layers need matching firmware; use the original A2 Lite capture.')
