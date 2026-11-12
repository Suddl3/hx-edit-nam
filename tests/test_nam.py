import json
from pathlib import Path
import struct
import tempfile
import unittest
from xml.etree import ElementTree as ET
from unittest.mock import patch

import msgpack

from nam_catalog import ROOT, prepare_copy
from nam_convert import convert_batch, validate_import
from nam_inventory import is_nam_profile, require_hx_device


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
    def setUp(self):
        # the user's own settings.json (namLayers, sidebarWidth) must not change what the tests see
        settings = patch('nam_catalog.load_settings', return_value={})
        settings.start()
        self.addCleanup(settings.stop)

    @unittest.skipUnless(BASELINE.is_dir(), 'requires the prepared HX Edit 3.82 baseline')
    def test_catalog_adds_one_nam_model_for_lt_and_floor(self):
        with tempfile.TemporaryDirectory() as temp, patch('nam_catalog.read_custom_names', return_value={}):
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
            self.assertEqual(nam['devices\0'], [
                {'id\0': 0x210004, 'version\0': '0x03800000\0'},
                {'id\0': 0x210001, 'version\0': '0x03800000\0'},
            ])
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

    @unittest.skipUnless(BASELINE.is_dir(), 'requires the prepared HX Edit 3.82 baseline')
    def test_layers_firmware_adds_three_lighter_blocks_with_their_loads(self):
        with tempfile.TemporaryDirectory() as temp, patch('nam_catalog.read_custom_names', return_value={}), \
                patch.dict('os.environ', {'HX_EDIT_NAM_LAYERS': '1'}):
            res = prepare_copy(temp).parent / 'res'
            symbols = json.loads((res / 'Helix.sym').read_bytes())
            original = json.loads((BASELINE / 'Helix.sym').read_bytes())
            self.assertEqual([i for i, s in enumerate(symbols) if s != original[i]], [380, 382, 516, 572])
            definitions = msgpack.unpackb((res / 'HelixModelDefs.bin').read_bytes())
            self.assertEqual([(m['symbolicID\0'], round(m['load\0'], 3)) for m in definitions[-4:]], [
                ('HD2_AmpNAMLite\0', 36.5), ('HD2_AmpNAMLite20\0', 33.0),
                ('HD2_AmpNAMLite16\0', 29.5), ('HD2_AmpNAMLite14\0', 27.7)])
            self.assertEqual([symbols[i]['symbol'] for i in (380, 382, 516, 572)],
                             [m['symbolicID\0'].rstrip('\0') for m in definitions[-4:]])
            self.assertTrue(all(symbols[i]['parameters'] == symbols[380]['parameters'] for i in (382, 516, 572)))
            catalog = msgpack.unpackb((res / 'HX_ModelCatalog.bin').read_bytes())
            amp = next(c for c in catalog['categories\0'] if c['name\0'] == 'Amp\0')
            folder = next(c for c in amp['subcategories\0'] if c['name\0'] == 'NAM\0')
            self.assertEqual([t['name\0'] for t in folder['models\0']],
                             ['NAM A2 Lite\0', 'NAM Lite 20\0', 'NAM Lite 16\0', 'NAM Lite 14\0'])

    @unittest.skipUnless(BASELINE.is_dir(), 'requires the prepared HX Edit 3.82 baseline')
    def test_hx_nam_keeps_preset_types_and_the_stereo_fuzz(self):
        stock_symbols = json.loads((BASELINE / 'Helix.sym').read_bytes())
        stock_defs = msgpack.unpackb((BASELINE / 'HelixModelDefs.bin').read_bytes())
        for device, device_id, category, folder in (
            ('stomp', 0x210006, 'Amp\0', 'NAM\0'),
            ('effects', 0x210005, 'Distortion\0', 'Mono\0'),
        ):
            with self.subTest(device=device), tempfile.TemporaryDirectory() as temp, \
                    patch('nam_catalog.read_custom_names', return_value={}):
                res = prepare_copy(temp, hx_nam=device).parent / 'res'
                symbols = json.loads((res / 'Helix.sym').read_bytes())
                self.assertEqual([i for i, s in enumerate(symbols) if s != stock_symbols[i]], [380, 555])
                self.assertEqual(symbols[555], {**stock_symbols[555], 'symbol': 'HD2_DistNAMLite'})
                definitions = msgpack.unpackb((res / 'HelixModelDefs.bin').read_bytes())
                self.assertEqual(definitions[:-2], stock_defs)
                nam = definitions[-1]
                self.assertEqual(nam['devices\0'], [{'id\0': device_id, 'version\0': '0x03800000\0'}])
                params = nam['params\0'][:10]
                self.assertEqual([p['symbolicID\0'].rstrip('\0') for p in params], symbols[555]['parameters'])
                self.assertEqual([p['valueType\0'] for p in params], [1, 1, 1, 0, 1, 1, 1, 1, 0, 1])
                self.assertEqual(params[6]['name\0'], 'Model\0')
                self.assertEqual(params[6]['displayType\0'], 'ir_select\0')
                self.assertIs(type(params[6]['default\0']), float)
                self.assertEqual((params[6]['min\0'], params[6]['max\0']), (1.0, 128.0))

                catalog = msgpack.unpackb((res / 'HX_ModelCatalog.bin').read_bytes())
                categories = {c['name\0']: c for c in catalog['categories\0']}
                models = next(f['models\0'] for f in categories[category]['subcategories\0'] if f['name\0'] == folder)
                self.assertEqual([m['name\0'] for m in models if m['id\0'] == 'HD2_DistNAMLite\0'], ['NAM A2 Lite\0'])
                stereo = next(f for f in categories['Distortion\0']['subcategories\0'] if f['name\0'] == 'Stereo\0')
                xeno = next(m for m in stereo['models\0'] if m['id\0'] == 'HD2_DistXenomorphFuzz\0')
                self.assertEqual(xeno['name\0'], 'Xenomorph Fuzz\0')

    def test_hx_nam_requires_only_the_requested_device(self):
        require_hx_device([0x4246], 'stomp')
        require_hx_device([0x4245], 'effects')
        for products in ([], [0x424A], [0x4245], [0x4246, 0x424A], [0x4246, 0x4246]):
            with self.subTest(products=products), self.assertRaisesRegex(OSError, 'Connect only the HX Stomp'):
                require_hx_device(products, 'stomp')

    @unittest.skipUnless(BASELINE.is_dir(), 'requires the prepared HX Edit 3.82 baseline')
    def test_custom_names_apply_to_the_stock_definition_and_catalog(self):
        with tempfile.TemporaryDirectory() as temp:
            csv_path = Path(temp, 'names.csv')
            csv_path.write_text('internal,real_name\nHD2_DistScream808Stereo,Мой овердрайв\n'
                                'Drive,Other Gain\nHD2_AmpNAMLite,Other NAM\n', encoding='utf-8-sig')
            with patch.dict('os.environ', {'HX_EDIT_NAM_NAMES': str(csv_path)}):
                resources = prepare_copy(temp).parent / 'res'

            symbol = 'HD2_DistScream808\0'
            original = msgpack.unpackb((BASELINE / 'HelixModelDefs.bin').read_bytes())
            next(model for model in original if model['symbolicID\0'] == symbol)['name\0'] = 'Мой овердрайв\0'
            definitions = msgpack.unpackb((resources / 'HelixModelDefs.bin').read_bytes())
            self.assertEqual(definitions[:-1], original)
            self.assertEqual(definitions[-1]['name\0'], 'NAM A2 Lite\0')
            catalog = msgpack.unpackb((resources / 'HX_ModelCatalog.bin').read_bytes())
            distortion = next(c for c in catalog['categories\0'] if c['name\0'] == 'Distortion\0')
            mono, stereo, _ = distortion['subcategories\0']
            model = next(m for m in mono['models\0'] if m['id\0'] == symbol)
            self.assertEqual(model['name\0'], 'Мой овердрайв\0')
            alias = next(m for m in stereo['models\0'] if m['id\0'] == symbol)
            self.assertEqual(alias['use_subcategory\0'], mono['id\0'])

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
