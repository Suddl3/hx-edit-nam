"""Native typed target binding verified offline; no live application or device."""
from pathlib import Path
import re
import shutil
import struct
import subprocess

import pytest

SOURCE = Path(__file__).resolve().parents[1]
# A prepared copy of stock HX Edit 3.82 kept outside the repository; tests that need it skip without it.
FIXTURE = SOURCE.parents[1] / 'research/hx-edit-nam-review/floor-port/integrated-candidate-53/app/HX Edit.exe'


def test_bound_target_native_api_contract():
    node = shutil.which('node')
    if not node:
        pytest.skip('node not installed')
    result = subprocess.run([node, str(SOURCE / 'tests' / 'target_binding_probe.cjs')],
                            capture_output=True, text=True, timeout=30, cwd=SOURCE)
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'target binding probe passed' in result.stdout


@pytest.mark.skipif(not FIXTURE.is_file(), reason='pinned editor fixture unavailable')
def test_bound_native_bytes_and_vtable_contract():
    data = FIXTURE.read_bytes()
    pe = struct.unpack_from('<I', data, 0x3c)[0]
    count = struct.unpack_from('<H', data, pe + 6)[0]
    section = pe + 24 + struct.unpack_from('<H', data, pe + 20)[0]
    sections = [struct.unpack_from('<IIII', data, section + i * 40 + 8) for i in range(count)]
    def read(rva, size):
        offset = next(raw + rva - va for _, va, length, raw in sections if va <= rva < va + length)
        return data[offset:offset + size]
    guards = re.findall(r"'([0-9a-f]+)': '([0-9a-f]+)'", (SOURCE / 'target_binding.js').read_text())
    assert len(guards) == 7
    for encoded, expected in guards:
        assert read(int(encoded, 16), len(expected) // 2).hex() == expected
    for offset, target in ((0x7c, 0x9178c0), (0x88, 0x904f50), (0xdc, 0x91aaf0)):
        assert struct.unpack('<I', read(0x963dfc + offset, 4))[0] == target
    # Both dispatch instructions and argument cleanup substantiate the captured ABI.
    assert read(0x229c47, 3) == bytes.fromhex('ff507c')
    assert read(0x229d8a, 6) == bytes.fromhex('ff9088000000')
    assert read(0x504f50 + 0x406, 3) == bytes.fromhex('c21000')
    assert read(0x5183d8, 3) == bytes.fromhex('c21400')
    # HXSliderParamView valueType=0 rounds a float to signed integer tag4;
    # valueType=1 stores float32 tag8. Neither is a double-precision payload.
    assert read(0x23cce1, 11) == bytes.fromhex('f30f2cc0c745bc04000000')
    assert read(0x23cc6a, 12) == bytes.fromhex('c745cc08000000f30f1155d4')
