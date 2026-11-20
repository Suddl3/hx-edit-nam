"""HX Unbound menu, Options: "3D pictures in the signal chain" (nam_catalog.patch_chain_art, tone_view chainPicture)."""
import struct
import tempfile
import unittest
from pathlib import Path

import nam_catalog
from dsp_meter import DspMeter
from nam_catalog import CHAIN_CAPTURES, CHAIN_MODELS, MODEL_ART, ROOT, chain_art_files, patch_chain_art


def png_size(path):
    data = Path(path).read_bytes()[:24]
    assert data[:8] == b'\x89PNG\r\n\x1a\n'
    return struct.unpack('>II', data[16:24])


class ChainPictureTests(unittest.TestCase):
    def test_every_render_and_capture_picture_has_a_chain_picture_at_the_icon_size(self):
        renders = sorted(path.name for path in MODEL_ART.glob('*.png'))
        self.assertEqual(sorted(path.name for path in CHAIN_MODELS.glob('*.png')), renders)
        captures = sorted(path.name for path in nam_catalog.CAPTURE_ICONS.glob('*.png'))
        self.assertEqual(sorted(path.name for path in CHAIN_CAPTURES.glob('*.png')), captures)
        for on, off, _ in nam_catalog.CHAIN_SETS:            # every picture also with the bypassed ring
            self.assertEqual(sorted(path.name for path in off.glob('*.png')), sorted(path.name for path in on.glob('*.png')))
            for path in [*on.glob('*.png'), *off.glob('*.png')]:
                self.assertEqual(png_size(path), (38, 38), path.name)

    def test_the_build_adds_them_beside_the_pictures_they_stand_for(self):
        with tempfile.TemporaryDirectory() as folder:
            models = Path(folder, 'icons_models')
            models.mkdir()
            stock = sorted(CHAIN_MODELS.glob('*.png'))[0].name
            (models / stock).write_bytes(b'stock')
            added = patch_chain_art(Path(folder))
            captures = len(list(CHAIN_CAPTURES.glob('*.png')))
            self.assertEqual(added, 2 * (1 + captures))            # only stock pictures this copy has; each twice
            self.assertTrue((models / f'CHAIN_{stock}').is_file())
            self.assertTrue((models / f'CHAIN_OFF_{stock}').is_file())
            files = chain_art_files(folder)
            self.assertIn(f'CHAIN_{stock}', files)
            self.assertEqual(sum(name.startswith('CHAIN_NAMCAP_') for name in files), captures)
            self.assertEqual(sum(name.startswith('CHAIN_OFF_NAMCAP_') for name in files), captures)
            self.assertEqual(chain_art_files(Path(folder, 'missing')), [])


class ChainOptionTests(unittest.TestCase):
    def test_the_menu_option_and_the_hook_are_wired(self):
        self.assertIn('chainArt', DspMeter.CHOICES)
        tone = (ROOT / 'tone_view.js').read_text(encoding='utf-8')
        for marker in ("onLeave() { if (chainArt) chainPicture(this.item); }", 'let chainArt = profile.chainArt === true;',
                       "`CHAIN_NAMCAP_${capture.icon}.png`", "'CHAIN_' + picture", 'if (chainArt) chainPicture(entry.item);',
                       'setChainArt: on =>', "owner.add(0x1e).readU8() !== 0", "file.replace(/^CHAIN_/, 'CHAIN_OFF_')"):
            self.assertIn(marker, tone)
        self.assertIn("recv('chainArt'", (ROOT / 'nam_sidebar.js').read_text(encoding='utf-8'))
        self.assertIn('3D pictures in the signal chain', (ROOT / 'NamLibrary.cs').read_text(encoding='utf-8'))
        launcher = (ROOT / 'start-hx-edit-nam.pyw').read_text(encoding='utf-8')
        self.assertIn("load_settings().get('chainModelArt') is True", launcher)     # off unless turned on
        self.assertIn("save_setting('chainModelArt', on)", launcher)


if __name__ == '__main__':
    unittest.main()
