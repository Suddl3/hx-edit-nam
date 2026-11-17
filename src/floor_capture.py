"""Import boundary for the original Floor A2 Lite format, without device access.

Format source: andreidevs/helix-lt-nam, helix-lt-2026-11-13,
tools/nam2ir.py. Header word7 is layers/32 (zero means all23). The Floor
reference supports the ordinary23-layer payload, not the newer shorter engines;
the627-word pedal model only with a NAM Pedal firmware (pedal=True, floor_firmware.py). This verifies format compatibility, not audio quality
or that a particular image is installed. Normal cabinet IRs remain native imports.
"""

from pathlib import Path
import struct


SIGNATURE = (0x3F000000, 0xBF000000, 0x40400000)
A2_WORDS = 0x3FE9E000  # 1871/1024 as float32
PEDAL_WORDS = 0x3F1CC000  # 627/1024: a NAM Pedal model (helix-lt-nam tools/nam_pedal.py)
FORMAT = (3, 1, 48000, 192000, 4, 32)


def validate_floor_capture_files(files, pedal=False):
    """pedal: the Floor's firmware has the NAM Pedal block, so pedal models (627 words) are accepted too."""
    for filename in files:
        path = Path(filename)
        if path.suffix.lower() != '.wav':
            continue
        _validate_wav(path, pedal)


def _validate_wav(path, pedal=False):
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
            if words[4] == PEDAL_WORDS and pedal:
                if words[6] or words[7]:            # word 5 is the input level, as in an A2 Lite capture
                    raise ValueError(f'{path.name}: unsupported NAM Pedal header.')
            elif words[4] != A2_WORDS:
                raise ValueError(f'{path.name}: this NAM format needs firmware outside the Floor A2 Lite profile.')
            if not (0x3F800000 <= words[3] <= 0x42800000 and words[3] & 0x007FFFFF == 0):
                raise ValueError(f'{path.name}: invalid NAM capture scale.')
            layers = struct.unpack_from('<f', head, 28)[0]
            if words[4] == A2_WORDS and layers not in (0.0, 23 / 32):
                raise ValueError(f'{path.name}: shortened NAM layers need matching firmware; use the original A2 Lite capture.')
