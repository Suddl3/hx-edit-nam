"""Optimize on a NAM Pedal firmware: a capture's Pedal version goes on the selected block as a NAM Pedal block, the
original as a NAM A2 Lite block. No HX Edit, device or browser: the worker's commands and the library's slot evidence."""
from pathlib import Path
import re
import struct
import tempfile
import unittest
from unittest.mock import Mock, patch

from floor_capture import FORMAT
from nam_library import Library
from tests_support_browser import Browser, make_preview

ROOT = Path(__file__).resolve().parents[1] / 'src'


def ir_wav(words):
    """A User IR .wav whose first eight samples are the NAM header (2,048 float32 at 48 kHz, as the Floor keeps them)."""
    data = struct.pack('<8f', .5, -.5, 3, 2.0, words / 1024, 1.0, 0, 0) + bytes(8192 - 32)
    body = b'WAVE' + b'fmt ' + struct.pack('<I', 16) + struct.pack('<HHIIHH', *FORMAT) + b'data' + struct.pack('<I', len(data)) + data
    return b'RIFF' + struct.pack('<I', len(body)) + body


class VersionSlotTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        folder = Path(self.temporary.name)
        capture = folder / 'Klon Gain 6.nam'
        capture.write_text('{"version": "0.5.4"}', encoding='utf-8')
        self.library = Library(folder / 'library')
        self.identity = self.library.add(capture, name='Klon Gain 6', gear='pedal')
        made = folder / 'made.wav'
        made.write_bytes(ir_wav(627))
        self.library.add_version(self.identity, 'pedal', made, error=0.01)

    def content(self):
        from floor_inventory import inspect_payload
        return inspect_payload(self.library.version_path(self.identity, 'pedal').read_bytes())['contentId']

    def test_an_installed_pedal_version_is_its_captures_version_never_the_original(self):
        content = self.content()
        metadata = self.library.payload_metadata()
        self.assertEqual(metadata[content]['id'], self.identity)
        self.assertEqual(metadata[content]['version'], 'pedal')
        self.assertEqual(metadata[content]['name'], 'Klon Gain 6 (Pedal)')
        self.assertTrue(self.library.set_slot_inventory({'epoch': 1, 'state': 'verified', 'entries': [[14, content]]}))
        entry = self.library.entries()[self.identity]
        self.assertEqual(entry['versionSlots'], {'pedal': [14]})
        self.assertEqual(entry['irSlots'], [])                  # the original is not in slot 14

    def test_no_version_no_slots(self):
        self.library.remove_version(self.identity, 'pedal')
        self.assertEqual(self.library.entries()[self.identity]['versionSlots'], {})
        self.assertFalse(any(m.get('version') for m in self.library.payload_metadata().values()))


class UseVersionTests(unittest.TestCase):
    identity = 'c' * 64

    def preview(self, actions, pedal_block, version_file=None):
        b, library = Browser(actions + ['cancel']), Mock()
        library.entries.return_value = {self.identity: {'name': 'Klon', 'irSlots': [], 'slotState': 'verified',
                                                        'versions': {'pedal': {}}}}
        library.version_path.return_value = Path(version_file or 'missing.pedal.wav')
        library.payload_metadata.return_value = {}
        p = make_preview(b, library=library, pedal_block=pedal_block)
        p.navigation_state.update(bound=True, preview=False)
        p.payload_callback = Mock()
        return b, library, p

    def test_pedal_goes_on_as_a_nam_pedal_block(self):
        with tempfile.TemporaryDirectory() as folder:
            made = Path(folder, 'x.pedal.wav')
            made.write_bytes(ir_wav(627))
            b, library, p = self.preview([f'useVersion:{self.identity}:pedal'], True, made)
            p.command = Mock(return_value={'loadedSlot': 14})
            with patch('tone_preview.Thread'):
                p.browse(b, 5, 't3k_pub_test')
        p.command.assert_called_once_with(5, 'load', identity=self.identity, targetVersion=1,
                                          model='HD2_AmpNAMPedal', version='pedal', file=str(made))
        p.payload_callback.assert_called_once()              # the slot is then recognized as the Pedal version
        library.mark_pending.assert_not_called()              # not the original's pending slot
        self.assertTrue(any('Pedal version plays in User IR 14' in x.get('text', '') for x in b.sent))

    def test_original_goes_back_as_a_nam_a2_lite_block(self):
        b, library, p = self.preview([f'useVersion:{self.identity}:original'], True)
        library.export.return_value = 'source.nam'
        with tempfile.TemporaryDirectory() as folder:
            p.workspace = folder
            p.command = Mock(return_value={'loadedSlot': 3})
            with patch('tone_preview.Thread'), patch('tone_preview.convert_batch', return_value=['converted.wav']):
                p.browse(b, 5, 't3k_pub_test')
        p.command.assert_called_once_with(5, 'load', identity=self.identity, targetVersion=1,
                                          model='HD2_AmpNAMLite', file='converted.wav')

    def test_refused_without_the_pedal_firmware_or_the_version(self):
        b, library, p = self.preview([f'useVersion:{self.identity}:pedal'], False)
        p.command = Mock()
        with patch('tone_preview.Thread'):
            p.browse(b, 5, 't3k_pub_test')
        p.command.assert_not_called()
        self.assertTrue(any('needs NAM Pedal firmware' in x.get('text', '') for x in b.sent))
        b, library, p = self.preview([f'useVersion:{self.identity}:pedal'], True)        # no version file
        p.command = Mock()
        with patch('tone_preview.Thread'):
            p.browse(b, 5, 't3k_pub_test')
        p.command.assert_not_called()
        self.assertTrue(any('no Pedal version yet' in x.get('text', '') for x in b.sent))

    def test_plain_load_on_a_pedal_firmware_also_names_the_a2_lite_block(self):
        b, library, p = self.preview([f'use:{self.identity}'], True)
        p.command = Mock(return_value={'usedSlot': 9})
        with patch('tone_preview.Thread'):
            p.browse(b, 5, 't3k_pub_test')
        p.command.assert_called_once_with(5, 'use', identity=self.identity, targetVersion=1, model='HD2_AmpNAMLite')


class NativeSideTests(unittest.TestCase):
    def test_versions_are_kept_apart_in_the_native_code(self):
        view = (ROOT / 'nam_view.js').read_text(encoding='utf-8')
        tone = (ROOT / 'tone_view.js').read_text(encoding='utf-8')
        self.assertIn("(payloadMetadata[contentId].version || undefined) !== (version || undefined)", view)
        self.assertRegex(view, r"capture\.version\) continue;")                      # not under NAM A2 Lite rows
        self.assertIn("HD2_AmpNAMPedal: 'NAM Pedal'", tone)
        self.assertRegex(tone, r"namView\.verifiedCaptureSlot\(request\.identity, undefined, request\.version\)")
        self.assertTrue(re.search(r"if \(request\.model && !request\.switched\)", tone))


if __name__ == '__main__':
    unittest.main()
