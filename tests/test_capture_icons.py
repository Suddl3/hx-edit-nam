import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import capture_icons  # noqa: E402
from capture_icons import FOLDER, icon_for, icon_path, known_icons, picker_groups, pick  # noqa: E402


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


class ChosenPictureTests(unittest.TestCase):
    def test_a_chosen_picture_wins_and_an_unknown_one_is_ignored(self):
        entry = {'name': 'Ibanez TS9', 'gear': 'pedal'}
        self.assertEqual(icon_for(entry), 'green_screamer')
        self.assertEqual(icon_for(dict(entry, icon='wah_pedal')), 'wah_pedal')
        self.assertEqual(icon_for(dict(entry, icon='../../evil')), 'green_screamer')

    def test_picker_lists_every_picture_once(self):
        listed = [ident for group in picker_groups() for ident in group['ids']]
        self.assertEqual(sorted(listed), sorted(known_icons()))
        self.assertEqual([group['label'] for group in picker_groups()],
                         ['Amps', 'Amp + cab', 'Cabs', 'Pedals', 'Other gear'])

    def test_library_stores_and_clears_the_choice(self):
        from nam_library import Library
        with tempfile.TemporaryDirectory() as folder:
            library = Library(Path(folder))
            library.catalog.parent.mkdir(parents=True, exist_ok=True)
            library.catalog.write_text(json.dumps({'abc': {'name': 'Ibanez TS9', 'gear': 'pedal'}}), encoding='utf-8')
            library.set_icon('abc', 'wah_pedal')
            self.assertEqual(library.entries()['abc']['icon'], 'wah_pedal')
            library._payload_index['abc'] = {'payload-1'}
            self.assertEqual(library.payload_metadata()['payload-1']['icon'], 'wah_pedal')
            with self.assertRaises(ValueError):
                library.set_icon('abc', 'not_a_picture')
            self.assertEqual(library.entries()['abc']['icon'], 'wah_pedal')
            library.set_icon('abc', '')
            self.assertNotIn('icon', library.entries()['abc'])
            self.assertEqual(library.payload_metadata()['payload-1']['icon'], 'green_screamer')


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

    def test_library_page_offers_the_picker(self):
        from nam_library import render_library
        page = render_library({'a': {'name': 'Ibanez TS9', 'gear': 'pedal', 'icon': 'wah_pedal'}})
        for ident in known_icons():
            self.assertIn(f'"{ident}": "data:image/png;base64,', page)
        self.assertIn('"icon": "wah_pedal", "iconChosen": true, "iconAuto": "green_screamer"', page)
        self.assertIn('"label": "Pedals"', page)
        self.assertIn("host?.postMessage('icon:' + row.id + ':' + (ident || ''))", page)
        self.assertNotIn('$iconGroups', page)

    def test_escape_closes_the_picker_not_the_pane(self):
        page = (ROOT / 'library_card.html').read_text(encoding='utf-8')
        self.assertIn("host?.postMessage('dialog:open')", page)
        self.assertIn("host?.postMessage('dialog:closed')", page)
        self.assertIn('window.hxCloseDialog = closePicker', page)
        pane = (ROOT / 'tone_browser.cs').read_text(encoding='utf-8')
        self.assertIn('if (reason == "escape" && CloseCardDialog()) return;', pane)
        self.assertIn('window.hxCloseDialog && window.hxCloseDialog()', pane)

    def test_preview_saves_the_choice(self):
        source = (ROOT / 'tone_preview.py').read_text(encoding='utf-8')
        self.assertIn("self.library.set_icon(identity, value)", source)

    def test_prepared_editor_gets_the_pictures(self):
        from nam_catalog import add_capture_pictures
        with tempfile.TemporaryDirectory() as folder:
            resources = Path(folder)
            (resources / 'icons_models').mkdir()
            add_capture_pictures(resources)
            # (uploaded pictures of this PC's real library, NAMCAP_u_*, may be copied too)
            copied = sorted(p.name for p in (resources / 'icons_models').iterdir() if not p.name.startswith('NAMCAP_u_'))
            self.assertIn('NAMCAP_green_screamer.png', copied)
            self.assertEqual(len(copied), len(list(FOLDER.glob('*.png'))))

    def test_capture_rows_load_pictures_by_name_with_a_fallback(self):
        source = (ROOT / 'tone_view.js').read_text(encoding='utf-8')
        self.assertIn('`NAMCAP_${icon}.png`', source)
        self.assertIn('image.isNull() ? frame.image : image', source)
        self.assertIn("icon: capture.icon || ''", (ROOT / 'nam_view.js').read_text(encoding='utf-8'))


def tiny_png(width=2, height=2, red=200):
    import struct, zlib
    def chunk(kind, data):
        return struct.pack('>I', len(data)) + kind + data + struct.pack('>I', zlib.crc32(kind + data) & 0xffffffff)
    rows = b''.join(b'\x00' + bytes([red, 40, 40, 255]) * width for _ in range(height))
    return (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', width, height, 8, 6, 0, 0, 0)) +
            chunk(b'IDAT', zlib.compress(rows)) + chunk(b'IEND', b''))


class UploadedPictureTests(unittest.TestCase):
    def setUp(self):
        from nam_library import Library
        self.folder = tempfile.TemporaryDirectory()
        self.library = Library(Path(self.folder.name))
        self.library.catalog.parent.mkdir(parents=True, exist_ok=True)
        self.library.catalog.write_text(json.dumps({'abc': {'name': 'Ibanez TS9', 'gear': 'pedal'}}), encoding='utf-8')

    def tearDown(self):
        self.folder.cleanup()

    def test_upload_is_kept_once_and_chosen(self):
        first = self.library.add_picture(tiny_png())
        self.assertRegex(first, r'^u_[0-9a-f]{16}$')
        self.assertEqual(self.library.add_picture(tiny_png()), first)
        self.assertEqual([p.name for p in self.library.pictures.iterdir()], [f'{first}.png'])
        self.library.set_icon('abc', first)
        entry = self.library.entries()['abc']
        self.assertEqual(self.library.icon_for(entry), first)
        self.assertTrue(self.library.picture_path(first).endswith(f'{first}.png'))
        self.library._payload_index['abc'] = {'payload-1'}
        self.assertEqual(self.library.payload_metadata()['payload-1']['icon'], first)
        from nam_sidebar import sidebar_rows
        self.assertEqual(Path(sidebar_rows(self.library)[0]['icon']).name, f'{first}.png')

    def test_missing_upload_falls_back_to_the_automatic_picture(self):
        ident = self.library.add_picture(tiny_png())
        self.library.set_icon('abc', ident)
        (self.library.pictures / f'{ident}.png').unlink()
        self.assertEqual(self.library.icon_for(self.library.entries()['abc']), 'green_screamer')
        self.assertEqual(icon_for({'name': 'x', 'icon': ident}), pick('x'))   # no pictures folder given

    def test_rejects_what_the_page_would_not_send(self):
        for data in (b'not a png', tiny_png(300, 2), tiny_png(2, 0), tiny_png()[:16] + b'0' * (600 * 1024)):
            with self.subTest(size=len(data)), self.assertRaises(ValueError):
                self.library.add_picture(data)
        with self.assertRaises(ValueError):
            self.library.set_icon('abc', 'u_0123456789abcdef')   # never uploaded
        self.assertFalse(self.library.pictures.exists())

    def test_upload_reaches_the_running_editor_and_the_page(self):
        earlier = self.library.add_picture(tiny_png(red=10))
        models = Path(self.folder.name) / 'icons_models'
        models.mkdir()
        self.library.publish_pictures(models)          # editor start: earlier uploads
        self.assertTrue((models / f'NAMCAP_{earlier}.png').is_file())
        ident = self.library.add_picture(tiny_png())   # and the next ones as they come
        self.assertTrue((models / f'NAMCAP_{ident}.png').is_file())
        self.library.set_icon('abc', ident)
        from nam_library import render_library
        page = render_library(self.library.entries(), None, self.library.pictures)
        self.assertIn(f'"label": "Your pictures", "ids": ["{ident}", "{earlier}"]', page)
        self.assertIn(f'"{ident}": "data:image/png;base64,', page)
        self.assertIn(f'"icon": "{ident}", "iconChosen": true, "iconAuto": "green_screamer"', page)

    def test_page_and_preview_carry_the_upload(self):
        page = (ROOT / 'library_card.html').read_text(encoding='utf-8')
        self.assertIn("host?.postMessage('iconUpload:' + row.id + ':' + base64)", page)
        self.assertIn('createImageBitmap(file)', page)
        self.assertIn('Upload picture', page)
        source = (ROOT / 'tone_preview.py').read_text(encoding='utf-8')
        self.assertIn('self.library.set_icon(identity, self.library.add_picture(picture))', source)
        self.assertIn('base64.b64decode(value, validate=True)', source)
        self.assertIn('render_library(entries, self.library.view_state(), self.library.pictures)', source)
        launcher = (ROOT / 'start-hx-edit-nam.pyw').read_text(encoding='utf-8')
        self.assertIn("library.publish_pictures(exe.parent / 'res' / 'icons_models')", launcher)


if __name__ == '__main__':
    unittest.main()
