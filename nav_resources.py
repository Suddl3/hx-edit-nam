"""Add the navigation button to a hash-pinned HX Edit copy, never the installed app.

The parameter panel and stock header widgets are deliberately left intact. Native
code chooses the largest label which fits between Redo and the right-side controls.
"""
from __future__ import annotations

import hashlib
from pathlib import Path
import xml.etree.ElementTree as ET

EXE_SHA256 = 'f7faac4996164f9039c9ae3ca8d85cd135c5bceae8a511c8921a1244046cab66'
BUTTONS = (('t3bf', 'Browse TONE3000', 158), ('t3bc', 'TONE3000', 84), ('t3bi', 'T3', 32))


def _verify_executable(app: Path) -> None:
    # prepare_copy renames the copied executable; standalone packaging may not.
    # If both exist they must be identical supported images, never pick one while
    # silently accepting a conflicting executable next to it.
    images = [app / name for name in ('HX Edit.exe', 'HX Edit NAM.exe') if (app / name).is_file()]
    if not images or any(hashlib.sha256(image.read_bytes()).hexdigest() != EXE_SHA256 for image in images):
        raise ValueError('Navigation requires the supported HX Edit 3.82 executable')


def navigation_xml(source: bytes) -> bytes:
    """Validate the stock anchors before adding three mutually exclusive labels."""
    root = ET.fromstring(source)
    editors = root.findall("./panel_widget[@id='edvw']")
    if len(editors) != 1:
        raise ValueError('Navigation requires exactly one edvw root')
    editor = editors[0]
    for tag, identity in (('panel_widget', 'udpl'), ('pushbutton_widget', 'redb'),
                          ('panel_widget', 'tmvw'), ('toggle_widget', 'tunb')):
        if len(editor.findall(f".//{tag}[@id='{identity}']")) != 1:
            raise ValueError(f'Navigation anchor {identity} is missing or ambiguous')
    present = [node for node in root.iter() if node.get('id') in {b[0] for b in BUTTONS}]
    if present:
        # The patch is repeatable, but never accepts a different widget with our IDs.
        expected = {identity: (label, width) for identity, label, width in BUTTONS}
        if len(present) != len(BUTTONS):
            raise ValueError('Partial or duplicate navigation resources')
        for node in present:
            label, width = expected[node.get('id')]
            if (node not in list(editor) or node.tag != 'pushbutton_widget'
                    or node.get('width') != str(width) or node.findtext('text/label') != label
                    or node.findtext('visible') != 'no'):
                raise ValueError('Navigation widget differs from the supported resource')
        return source
    for identity, label, width in BUTTONS:
        button = ET.SubElement(editor, 'pushbutton_widget', {
            'id': identity, 'name': 'Browse TONE3000', 'height': '29',
            'left': '591', 'top': '8', 'width': str(width),
        })
        ET.SubElement(button, 'anchor_pos', left='591', top='8')
        # Initially hidden: an unexpected native layout must never cover Undo/Tempo.
        ET.SubElement(button, 'visible').text = 'no'
        ET.SubElement(button, 'corner_radius').text = '3'
        # TONE3000's own look is a warm yellow wordmark on black. A native button has one flat
        # background and one text colour; the gradient wordmark itself would need an image added
        # to HX Edit's .imb bundles, which is not mapped yet.
        ET.SubElement(button, 'bg_color').text = '0xFF000000'
        ET.SubElement(button, 'disabled_opacity').text = '91'
        ET.SubElement(button, 'button_repeat').text = 'no'
        # Hover must stay clearly apart from the banner behind it (about #1C1C1C): at that
        # colour the button's outline vanished under the pointer. Stock buttons lighten on hover.
        ET.SubElement(ET.SubElement(button, 'active_state'), 'bg_color').text = '0xFF000000'
        ET.SubElement(ET.SubElement(button, 'hilite_state'), 'bg_color').text = '0xFF4A4A4A'
        text = ET.SubElement(button, 'text')
        ET.SubElement(text, 'label').text = label
        ET.SubElement(text, 'font', name='Roboto', size='12')
        ET.SubElement(text, 'align', horz='center', vert='middle')
        ET.SubElement(text, 'text_color').text = '0xFFFFC31F'
    return ET.tostring(root, encoding='utf-8', xml_declaration=True)


def patch_navigation_resources(app_directory: str | Path) -> Path:
    """Patch only the copied, known executable's editor.xml; callers launch later."""
    app = Path(app_directory)
    _verify_executable(app)
    path = app / 'res' / 'editor.xml'
    original = path.read_bytes()
    patched = navigation_xml(original)
    if patched != original:
        path.write_bytes(patched)
    return path


def library_navigation_xml(source: bytes) -> bytes:
    """Reserve one NAM surface for the shared rich sidebar, without a second route."""
    root = ET.fromstring(source)
    frames = root.findall("./panel_widget[@id='livw']/panel_widget[@id='namv']/panel_widget[@id='nmgp']/panel_widget")
    if len(frames) != 1:
        raise ValueError('Navigation requires exactly one patched NAM frame')
    frame = frames[0]
    rows = frame.find("listbox_widget[@id='nlst']/anchor_pos")
    message = frame.find("text_widget[@id='nmsg']/anchor_pos")
    message_label = frame.find("text_widget[@id='nmsg']/text/label")
    if rows is None or message is None or message_label is None or frame.find("panel_widget[@id='nbar']") is None:
        raise ValueError('Navigation requires the patched NAM list, toolbar and empty message')
    if any(node.get('id') in ('nlod', 'nleg') for node in frame):
        raise ValueError('Rebuild the NAM resource before installing the rich shared sidebar')
    existing = [node for node in root.iter() if node.get('id') == 'nall']
    if existing:
        if len(existing) != 1 or existing[0] not in list(frame):
            raise ValueError('Local-library footer differs from the supported resource')
        frame.remove(existing[0])
    rows.set('bottom', '0')
    message.set('bottom', '0')
    # Local rows never wait for a device scan. This fallback is a helper-start
    # message, not an incorrect claim that the user's collection is empty.
    message_label.text = ('Opening the NAM library…\n'
                          'If its table does not appear, close and reopen HX Edit.')
    errors = frame.findall("text_widget[@id='nerr']")
    if len(errors) > 1:
        raise ValueError('Duplicate NAM library failure message')
    if not errors:
        error = ET.fromstring(ET.tostring(frame.find("text_widget[@id='nmsg']")))
        error.set('id', 'nerr')
        visibility = error.find('visible')
        if visibility is None:
            visibility = ET.SubElement(error, 'visible')
        visibility.text = 'no'
        error.find('text/label').text = ('The NAM library view could not open.\n'
                                       'Your kept captures are unchanged. Close and reopen HX Edit.')
        frame.append(error)
    return ET.tostring(root, encoding='utf-8', xml_declaration=True)


def patch_library_navigation(app_directory: str | Path) -> Path:
    """Run after nam_catalog.patch_library in the same verified application copy."""
    app = Path(app_directory)
    _verify_executable(app)
    path = app / 'res' / 'librarian.xml'
    source = path.read_bytes()
    patched = library_navigation_xml(source)
    if patched != source:
        path.write_bytes(patched)
    return path
