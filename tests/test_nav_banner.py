"""Offline navigation header contracts, with no editor process or device access."""
import hashlib
from pathlib import Path
import re
import shutil
import struct
import subprocess
import xml.etree.ElementTree as ET

import pytest

from nav_resources import BUTTONS, EXE_SHA256, navigation_xml, patch_navigation_resources, library_navigation_xml

SOURCE = Path(__file__).resolve().parents[1]
# A prepared copy of stock HX Edit 3.82 kept outside the repository; tests that need it skip without it.
FIXTURE = SOURCE.parents[1] / 'research/hx-edit-nam-review/floor-port/integrated-candidate-53/app'
MINIMAL = b'''<xg><panel_widget id="edvw"><panel_widget id="udpl"><pushbutton_widget id="redb"/></panel_widget>
<panel_widget id="tmvw"/><toggle_widget id="tunb"/><panel_widget id="PRVW"><text_widget id="prms"/></panel_widget>
</panel_widget></xg>'''


def test_adds_only_navigation_widgets_preserving_existing_controls():
    before = ET.fromstring(MINIMAL)
    result = navigation_xml(MINIMAL)
    after = ET.fromstring(result)
    parent = after.find('panel_widget')
    for identity, label, width in BUTTONS:
        node = parent.find(f"pushbutton_widget[@id='{identity}']")
        assert node.findtext('text/label') == label
        assert node.get('width') == str(width)
        assert node.findtext('visible') == 'no'
        parent.remove(node)
    assert ET.tostring(after) == ET.tostring(before)
    assert navigation_xml(result) == result


@pytest.mark.parametrize('source', [MINIMAL.replace(b'id="edvw"', b'id="other"'),
                                   MINIMAL.replace(b'id="redb"', b'id="other"'),
                                   MINIMAL.replace(b'</xg>', b'<panel_widget id="edvw"/></xg>'),
                                   MINIMAL.replace(b'<panel_widget id="udpl">', b'<pushbutton_widget id="t3bf"/><panel_widget id="udpl">')])
def test_unknown_resource_shapes_fail_closed(source):
    with pytest.raises(ValueError):
        navigation_xml(source)


def test_unknown_executable_is_not_patched(tmp_path):
    (tmp_path / 'HX Edit.exe').write_bytes(b'not-the-pinned-executable')
    (tmp_path / 'res').mkdir()
    path = tmp_path / 'res' / 'editor.xml'
    path.write_bytes(MINIMAL)
    with pytest.raises(ValueError, match='supported HX Edit'):
        patch_navigation_resources(tmp_path)
    assert path.read_bytes() == MINIMAL


@pytest.mark.skipif(not FIXTURE.exists(), reason='local pinned HX Edit fixture unavailable')
@pytest.mark.parametrize('filename', ['HX Edit.exe', 'HX Edit NAM.exe'])
def test_pinned_resource_patch_preserves_all_stock_nodes(tmp_path, filename):
    exe = FIXTURE / 'HX Edit.exe'
    assert hashlib.sha256(exe.read_bytes()).hexdigest() == EXE_SHA256
    shutil.copyfile(exe, tmp_path / filename)
    (tmp_path / 'res').mkdir()
    path = tmp_path / 'res' / 'editor.xml'
    source = (FIXTURE / 'res' / 'editor.xml').read_bytes()
    path.write_bytes(source)
    patch_navigation_resources(tmp_path)
    after = ET.fromstring(path.read_bytes())
    panel = after.find("panel_widget[@id='edvw']")
    for identity, _, _ in BUTTONS:
        panel.remove(panel.find(f"pushbutton_widget[@id='{identity}']"))
    assert ET.tostring(after) == ET.tostring(ET.fromstring(source))
    assert (FIXTURE / 'res' / 'editor.xml').read_bytes() == source
    other = 'HX Edit NAM.exe' if filename == 'HX Edit.exe' else 'HX Edit.exe'
    (tmp_path / other).write_bytes(b'conflicting executable')
    with pytest.raises(ValueError, match='supported HX Edit'):
        patch_navigation_resources(tmp_path)


@pytest.mark.skipif(not FIXTURE.exists(), reason='local pinned HX Edit fixture unavailable')
def test_every_runtime_guard_matches_pinned_native_bytes():
    data = (FIXTURE / 'HX Edit.exe').read_bytes()
    pe = struct.unpack_from('<I', data, 0x3c)[0]
    count = struct.unpack_from('<H', data, pe + 6)[0]
    section = pe + 24 + struct.unpack_from('<H', data, pe + 20)[0]
    sections = [struct.unpack_from('<IIII', data, section + i * 40 + 8) for i in range(count)]
    guards = re.findall(r"'([0-9a-f]+)': '([0-9a-f]+)'", (SOURCE / 'nav_banner.js').read_text())
    assert len(guards) == 10
    for encoded_rva, expected in guards:
        rva = int(encoded_rva, 16)
        raw = next(pointer + rva - address for _, address, size, pointer in sections if address <= rva < address + size)
        assert data[raw:raw + len(expected) // 2].hex() == expected, encoded_rva


def test_native_event_and_responsive_layout_contract():
    node = shutil.which('node')
    if not node:
        pytest.skip('node is not installed')
    result = subprocess.run([node, str(SOURCE / 'tests' / 'nav_banner_probe.cjs')],
                            capture_output=True, text=True, timeout=30, cwd=SOURCE)
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'navigation banner probe passed' in result.stdout


def test_shared_sidebar_removes_redundant_footer_without_touching_slot_toolbar():
    source = b'''<xg><panel_widget id="livw"><panel_widget id="namv"><panel_widget id="nmgp"><panel_widget>
<panel_widget id="nbar"><pushbutton_widget id="nclr"/></panel_widget>
<listbox_widget id="nlst"><anchor_pos left="0" right="0" top="30" bottom="0"/></listbox_widget>
<text_widget id="nmsg"><anchor_pos left="8" right="8" top="34" bottom="8"/><text><label>No NAM profiles on this Helix.</label></text></text_widget>
</panel_widget></panel_widget></panel_widget></panel_widget></xg>'''
    result = library_navigation_xml(source)
    root = ET.fromstring(result)
    assert root.find(".//pushbutton_widget[@id='nall']") is None
    assert root.find(".//listbox_widget[@id='nlst']/anchor_pos").get('bottom') == '0'
    assert root.find(".//text_widget[@id='nmsg']/anchor_pos").get('bottom') == '0'
    assert root.find(".//text_widget[@id='nmsg']/text/label").text == (
        'Opening the NAM library…\n'
        'If its table does not appear, close and reopen HX Edit.')
    assert ET.tostring(root.find(".//panel_widget[@id='nbar']")) == ET.tostring(ET.fromstring(source).find(".//panel_widget[@id='nbar']"))
    assert library_navigation_xml(result) == result
    previous = source.replace(b'<panel_widget id="nbar">', b'<pushbutton_widget id="nall"/><panel_widget id="nbar">')
    assert ET.fromstring(library_navigation_xml(previous)).find(".//pushbutton_widget[@id='nall']") is None
    with pytest.raises(ValueError, match='Rebuild'):
        library_navigation_xml(source.replace(b'<panel_widget id="nbar">', b'<pushbutton_widget id="nlod"/><panel_widget id="nbar">'))
