import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import capture_icons  # noqa: E402
from capture_icons import FOLDER, icon_path, pick  # noqa: E402


class CaptureIconRulesTests(unittest.TestCase):
    def test_every_rule_has_its_picture(self):
        rules = json.loads((FOLDER / 'icons.json').read_text(encoding='utf-8'))
        ids = {icon['id'] for icon in rules['icons']}
        self.assertGreaterEqual(len(ids), 100)
        for ident in ids | {g for pool in rules['generic'].values() for g in pool}:
            self.assertTrue((FOLDER / f'{ident}.png').is_file(), ident)

    def test_popular_gear_names(self):
        cases = [
            ('Mesa Boogie Dual Rectifier Rev F', 'amp-cab', 'diamond_plate_head'),
            ('NAM AC MES DRECF RD Ga a21b358c', 'amp-cab', 'diamond_plate_head'),
            ('Fender Super Reverb 1965', 'amp-cab', 'blackface_combo'),
            ('T3K 748130 d6c9bb FNDR BFSR VB', 'amp-cab', 'blackface_combo'),
            ('D-Lux-556392-Lite', 'amp', 'small_blackface_combo'),
            ('EVH 5150III 100S', 'amp', 'striped_metal_head'),
            ('NAM AC SLDN SL100 NR D c7cbcbcc', 'amp-cab', 'chrome_panel_head'),
            ('Allure_70s_WhoWatt_100', '', 'vintage_grey_head'),             # camelCase
            ('TF 71 MARSH FULLSTACK G12 BLEND 57 3', 'amp-cab', 'brit_half_stack'),
            ('Tube Screamer Gain4-d597bcaa-Lite', 'pedal', 'green_screamer'),
            ('APP-SD1-Boost-III-687bde2e-Lite', 'pedal', 'yellow_compact'),   # specific beats "boost"
            ('NAM P Klon Gain0 75ac5908', 'pedal', 'gold_overdrive'),
            ('Dunlop Cry Baby', 'pedal', 'wah_pedal'),
        ]
        for name, gear, expected in cases:
            with self.subTest(name=name):
                self.assertEqual(pick(name, gear), expected)

    def test_cab_names_prefer_cab_pictures(self):
        self.assertEqual(pick("'76 Marshall 4x12 G12M Blackbacks IR"), 'closed_4x12')
        self.assertEqual(pick('Orange V30 4x12'), 'orange_4x12')
        self.assertEqual(pick('Mesa Oversized 4x12 V30'), 'oversized_4x12')
        self.assertEqual(pick('Marshall JCM800', 'amp'), 'brit_gold_head')
        # Amp + cab captures that name their cab are still the amp.
        self.assertEqual(pick('FNDR BFSR VB Edge WRM2 CAB FREE', 'amp-cab'), 'blackface_combo')
        self.assertEqual(pick('SLDN SL100 NR Drive WRM5 CAB', 'amp-cab'), 'chrome_panel_head')
        self.assertEqual(pick('MES DRECF RD Gain2 BAL O808 CAB', 'amp-cab'), 'diamond_plate_head')
        self.assertEqual(pick('BRIT50 Plexi Crunch Bal2 CAB', 'amp-cab'), 'brit_gold_head')
        self.assertEqual(pick('Mesa Boogie Dual Rectifier Rev F - Red Channel', 'amp-cab'), 'diamond_plate_head')

    def test_category_rules_out_other_kinds(self):
        self.assertEqual(pick('Walrus Slo', 'pedal'), 'reverb_box')        # not the SLO-100 head
        self.assertEqual(pick('Soldano SLO-100', 'amp'), 'chrome_panel_head')

    def test_unmatched_names_get_a_stable_generic_of_their_kind(self):
        first = pick('KD-EV53-RH-D-75', 'amp-cab')
        self.assertTrue(first.startswith('gen_combo_'))
        self.assertEqual(first, pick('KD-EV53-RH-D-75', 'amp-cab'))
        self.assertTrue(pick('Zzyzx', 'pedal').startswith('gen_pedal_'))
        self.assertTrue(pick('Zzyzx', 'amp').startswith('gen_head_'))
        self.assertTrue(icon_path('Zzyzx', 'pedal').endswith('.png'))
        self.assertEqual(pick('', None), pick('', None))


class CaptureIconPlumbingTests(unittest.TestCase):
    def test_payload_metadata_and_sidebar_rows_carry_the_icon(self):
        from nam_library import Library
        from nam_sidebar import sidebar_rows
        with tempfile.TemporaryDirectory() as folder:
            library = Library(Path(folder))
            capture = Path(folder) / 'source.nam'
            capture.write_text(json.dumps({'version': '0.5.0', 'architecture': 'WaveNet', 'config': {},
                                           'weights': [], 'metadata': {'gear_type': 'pedal'}}), encoding='utf-8')
            try:
                identity = library.add(capture, name='Ibanez TS9', gear='pedal')
            except Exception as error:  # the library may refuse a weightless file; the rows test then skips
                self.skipTest(f'library refused the fixture: {error}')
            rows = sidebar_rows(library)
            self.assertEqual(Path(rows[0]['icon']).name, 'green_screamer.png')
            library._payload_index[identity] = {'payload-1'}   # as register_payload records a conversion
            self.assertEqual(library.payload_metadata()['payload-1']['icon'], 'green_screamer')

    def test_library_page_shows_each_picture_once(self):
        from nam_library import render_library
        page = render_library({'a': {'name': 'Ibanez TS9', 'gear': 'pedal'},
                               'b': {'name': 'TS808 copy', 'gear': 'pedal'},
                               'c': {'name': 'Deluxe Reverb', 'gear': 'amp-cab'}})
        self.assertEqual(page.count('"green_screamer": "data:image/png;base64,'), 1)
        self.assertIn('"small_blackface_combo": "data:image/png;base64,', page)
        self.assertIn('"icon": "green_screamer"', page)
        self.assertIn("picture.src = icons[row.icon]", page)

    def test_prepared_editor_gets_the_pictures(self):
        from nam_catalog import add_capture_pictures
        with tempfile.TemporaryDirectory() as folder:
            resources = Path(folder)
            (resources / 'icons_models').mkdir()
            add_capture_pictures(resources)
            copied = sorted(p.name for p in (resources / 'icons_models').iterdir())
            self.assertIn('NAMCAP_green_screamer.png', copied)
            self.assertEqual(len(copied), len(list(FOLDER.glob('*.png'))))

    def test_capture_rows_load_pictures_by_name_with_a_fallback(self):
        source = (ROOT / 'tone_view.js').read_text(encoding='utf-8')
        self.assertIn('`NAMCAP_${icon}.png`', source)
        self.assertIn('image.isNull() ? frame.image : image', source)
        self.assertIn("icon: capture.icon || ''", (ROOT / 'nam_view.js').read_text(encoding='utf-8'))


if __name__ == '__main__':
    unittest.main()
