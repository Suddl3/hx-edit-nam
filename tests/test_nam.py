import json
from pathlib import Path
import struct
import tempfile
import unittest
from xml.etree import ElementTree as ET

import msgpack

from nam_catalog import ROOT, prepare_copy
from nam_convert import convert_batch, validate_import
from nam_inventory import is_nam_profile


CONVERTER = ROOT.parent / 'helix-lt-nam' / 'tools' / 'nam2ir.py'
BASELINE = ROOT / 'build' / 'HX Edit' / 'res'


def capture():
    return {
        'architecture': 'WaveNet',
        'sample_rate': 48000,
        'config': {'head_scale': 1.0, 'layers': [{
            'input_size': 1, 'condition_size': 1, 'channels': 3, 'bottleneck': 3,
            'kernel_sizes': [6] * 14 + [15, 15] + [6] * 7,
            'dilations': [1, 3, 7, 17, 41, 101, 239] * 2 + [1, 13] + [1, 3, 7, 17, 41, 101, 239],
            'activation': [{'type': 'LeakyReLU', 'negative_slope': 0.01}] * 23,
            'layer1x1': {'active': True},
            'head': {'out_channels': 1, 'kernel_size': 16, 'bias': True},
        }]},
        'weights': [0.0] * 1871,
    }


class NamTests(unittest.TestCase):
    @unittest.skipUnless(BASELINE.is_dir(), 'requires the prepared HX Edit 3.82 baseline')
    def test_catalog_adds_one_lt_model(self):
        with tempfile.TemporaryDirectory() as temp:
            res = prepare_copy(temp).parent / 'res'
            symbols = json.loads((res / 'Helix.sym').read_bytes())
            original = json.loads((BASELINE / 'Helix.sym').read_bytes())
            self.assertEqual([i for i, s in enumerate(symbols) if s != original[i]], [380])
            definitions = msgpack.unpackb((res / 'HelixModelDefs.bin').read_bytes())
            stock = msgpack.unpackb((BASELINE / 'HelixModelDefs.bin').read_bytes())
            self.assertEqual(definitions[:-1], stock)
            nam = definitions[-1]
            params = nam['params\0'][:8]
            self.assertEqual(symbols[380]['parameters'], [p['symbolicID\0'].rstrip('\0') for p in params])
            self.assertEqual([p['valueType\0'] for p in params], [0, 1, 1, 1, 1, 1, 1, 2])
            self.assertEqual([p['default\0'] for p in params], [1] + [0.5] * 6 + [False])
            self.assertEqual(nam['devices\0'][0]['id\0'], 0x210004)
            catalog = msgpack.unpackb((res / 'HX_ModelCatalog.bin').read_bytes())
            amp = next(c for c in catalog['categories\0'] if c['name\0'] == 'Amp\0')
            folder = next(c for c in amp['subcategories\0'] if c['name\0'] == 'NAM\0')
            self.assertEqual(folder['models\0'][0]['id\0'], nam['symbolicID\0'])
            library = ET.parse(res / 'librarian.xml').getroot().find("panel_widget[@id='livw']")
            buttons = library.findall("radio_widget[@id='lbtb']/radio_button_widget")
            self.assertEqual([button.get('id') for button in buttons], ['1', '2', '3', '4'])
            self.assertEqual(buttons[-1].findtext('text/label'), 'NAM')
            ir_list = library.find("panel_widget[@id='irvw']//listbox_widget[@id='ilst']")
            nam_list = library.find("panel_widget[@id='namv']//listbox_widget[@id='nlst']")
            self.assertIsNotNone(nam_list)
            self.assertEqual([ET.tostring(child) for child in nam_list],
                             [ET.tostring(child) for child in ir_list])
            actions = library.findall("panel_widget[@id='namv']//panel_widget[@id='nbar']/pushbutton_widget")
            self.assertEqual([button.get('id') for button in actions],
                             ['ncop', 'npas', 'nimp', 'nexp', 'nclr'])
            self.assertEqual([button.findtext('text/label') for button in actions],
                             ['COPY', 'PASTE', 'IMPORT', 'EXPORT', 'CLEAR'])

    @unittest.skipUnless(CONVERTER.is_file(), 'requires the sibling helix-lt-nam checkout')
    def test_conversion_preserves_file_names_and_wav(self):
        with tempfile.TemporaryDirectory() as temp:
            profile = Path(temp) / 'Чистый тон.NAM'
            profile.write_text(json.dumps(capture()), encoding='utf-8')
            ir = Path(temp) / 'IR.wav'
            ir.write_bytes(b'unchanged wav bytes')
            nam, wav = [Path(p) for p in convert_batch([profile, ir], temp, CONVERTER)]
            self.assertEqual(nam.name, 'Чистый тон.wav')
            self.assertEqual(nam.parent, wav.parent)
            data = nam.read_bytes()
            self.assertEqual(len(data), 8236)
            self.assertEqual(struct.unpack_from('<HHI', data, 20), (3, 1, 48000))
            self.assertEqual(struct.unpack_from('<3f', data, 44), (0.5, -0.5, 3.0))
            self.assertTrue(is_nam_profile(data[44:]))
            self.assertFalse(is_nam_profile(bytes(8192)))
            self.assertEqual(wav.read_bytes(), ir.read_bytes())
            self.assertEqual(validate_import([wav], 128, set()), {})
            self.assertEqual(validate_import([nam], 1, set(), nam_only=True), {1: profile.stem})
            with self.assertRaisesRegex(ValueError, 'cabinet IR'):
                validate_import([wav], 1, set(), nam_only=True)
            with self.assertRaisesRegex(ValueError, 'Slot 51 is already occupied'):
                validate_import([nam], 51, {51})
            with self.assertRaisesRegex(ValueError, 'enough consecutive slots'):
                validate_import([nam, nam], 128, set())
            with self.assertRaisesRegex(ValueError, 'same IR name'):
                convert_batch([profile, nam], temp, CONVERTER)

            profile.write_text('{}', encoding='utf-8')
            with self.assertRaisesRegex(ValueError, 'no 3-channel WaveNet'):
                convert_batch([profile], temp, CONVERTER)
