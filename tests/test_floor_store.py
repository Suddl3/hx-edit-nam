"""The Floor look file (floor_store.py): its shape and which picture each capture slot gets; no device required."""
import json
from pathlib import Path
import tempfile
import unittest

import capture_icons
import floor_store


class FloorStoreTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.root = Path(self.folder.name)
        self.addCleanup(self.folder.cleanup)
        self.ids = capture_icons.known_icons()

    def test_a_slot_gets_the_librarys_picture_else_one_picked_from_its_name_and_real_irs_get_none(self):
        chosen = self.ids[0]
        slots = {17: {'name': 'NAM P Klon Gain0 75ac5908', 'contentId': 'a' * 64},
                 21: {'name': 'Nobels LG 64 Deluxe', 'contentId': 'b' * 64},            # on the Floor, not in the library
                 2: {'name': 'Allure_59_Tweed_P10N', 'contentId': None},                # a real cab IR
                 200: {'name': 'Out of range', 'contentId': 'c' * 64}}
        got = floor_store.slot_pictures(slots, {'a' * 64: {'icon': chosen}})
        self.assertEqual(sorted(got), [17, 21])
        self.assertEqual(Path(got[17]['picture']).name, f'{chosen}.png')
        self.assertEqual(Path(got[21]['picture']).name, capture_icons.pick('Nobels LG 64 Deluxe') + '.png')
        self.assertEqual(got[17]['icon'], got[17]['picture'])

    def test_a_bigger_art_folder_wins_and_an_uploaded_picture_comes_from_the_library(self):
        art = self.root / 'art256'
        art.mkdir()
        (art / f'{self.ids[0]}.png').write_bytes(b'png')
        pictures = self.root / 'pictures'
        pictures.mkdir()
        (pictures / 'u_0123456789abcdef.png').write_bytes(b'png')
        slots = {1: {'name': 'x', 'contentId': 'a' * 64}, 2: {'name': 'y', 'contentId': 'b' * 64}}
        got = floor_store.slot_pictures(slots, {'a' * 64: {'icon': self.ids[0]}, 'b' * 64: {'icon': 'u_0123456789abcdef'}},
                                        pictures=pictures, art_dirs=[art])
        self.assertEqual(Path(got[1]['picture']), (art / f'{self.ids[0]}.png').resolve())
        self.assertEqual(Path(got[2]['picture']), (pictures / 'u_0123456789abcdef.png').resolve())

    def test_the_file_has_the_agreed_shape_and_leaves_out_what_is_not_chosen(self):
        scenes = self.root / 'scenes'
        scenes.mkdir()
        look = {'scenesDir': str(scenes), 'modelsDir': str(self.root / 'missing'), 'assign': {'scenes': ['rain']}}
        store = floor_store.build({17: {'name': 'Klon', 'contentId': 'a' * 64}}, {'a' * 64: {'icon': self.ids[0]}},
                                  look=look)
        self.assertEqual(store['format'], 1)
        self.assertEqual(store['pictures']['size'], 256)
        self.assertEqual(store['backgrounds'], {'scenes_dir': str(scenes.resolve()), 'assign': {'scenes': ['rain']}})
        self.assertEqual(store['nam']['preset_ids'], [380])
        self.assertEqual(list(store['nam']['slots']), ['17'])
        self.assertEqual(store['icons'], {'size': 36})
        self.assertTrue(all(Path(p).is_absolute() for p in store['nam']['slots']['17'].values()))
        if not floor_store.MODELS_DIR.is_dir():
            self.assertNotIn('models_dir', store['pictures'])
        path = floor_store.write(self.root / 'out' / 'floor-store.json', store)
        self.assertEqual(json.loads(path.read_text(encoding='utf-8')), store)
        self.assertEqual([p.name for p in path.parent.iterdir()], ['floor-store.json'])

    def test_with_no_backgrounds_chosen_the_file_carries_the_tools_default_assignment(self):
        # "Use the defaults" removes the page's choice; the Floor tool refuses a scenes folder without an assignment
        # (hx-unbound-floor#16), so the file carries the tool's default one, limited to the scenes the folder has.
        scenes = self.root / 'scenes'
        scenes.mkdir()
        for name in ('studio', 'stage', 'rack'):
            (scenes / f'{name}.png').write_bytes(b'png')
        store = floor_store.build({}, {}, look={'scenesDir': str(scenes)})
        assign = store['backgrounds']['assign']
        self.assertEqual(assign['default'], 'studio')
        self.assertEqual(assign['category']['amp'], 'stage')
        self.assertEqual(assign['category']['output'], 'rack')
        self.assertNotIn('delay', assign['category'])            # deep_sea is not in this folder
        self.assertEqual(set(assign['scenes']), {'studio', 'stage', 'rack'})
        empty = self.root / 'empty'
        empty.mkdir()
        self.assertNotIn('backgrounds', floor_store.build({}, {}, look={'scenesDir': str(empty)}))

    def test_sizes_the_firmware_cannot_take_are_refused(self):
        for look in ({'iconSize': 48}, {'iconSize': 0}, {'pictureSize': 512}, {'pictureSize': '256'}):
            with self.subTest(look=look), self.assertRaises(ValueError):
                floor_store.build({}, {}, look=look)

    def test_the_backgrounds_chosen_on_the_page_become_the_tools_assignment(self):
        known = ['studio', 'stage', 'synthwave', 'neon_rain']
        got = floor_store.assignment({'default': 'synthwave', 'category': {'amp': 'neon_rain', 'delay': 'stage',
                                                                           'made up': 'stage', 'reverb': 'missing'}}, known)
        self.assertEqual(got, {'scenes': ['synthwave', 'neon_rain', 'stage'], 'default': 'synthwave',
                               'category': {'amp': 'neon_rain', 'delay': 'stage'}, 'preset': {}})
        self.assertEqual(floor_store.assignment({'default': 'gone'}, known)['default'], 'studio')

    def test_the_page_view_lists_capture_slots_scenes_and_starts_from_the_tools_defaults(self):
        slots = {17: {'name': 'NAM P Klon Gain0', 'contentId': 'a' * 64}, 3: {'name': 'Allure', 'contentId': None},
                 21: {'name': 'Nobels LG 64 Deluxe', 'contentId': 'b' * 64}}
        got = floor_store.view(slots, {'a' * 64: {'id': 'f' * 64, 'name': 'Klon Gain=0', 'icon': self.ids[0]}})
        self.assertEqual([(row['slot'], row['id'], row['name']) for row in got['slots']],
                         [(17, 'f' * 64, 'Klon Gain=0'), (21, None, 'Nobels LG 64 Deluxe')])
        self.assertFalse(got['chosen'])
        self.assertEqual(got['defaults'], {'default': floor_store.DEFAULT_ASSIGN['default'],
                                           'category': floor_store.DEFAULT_ASSIGN['category']})
        self.assertEqual(got['assign']['default'], floor_store.DEFAULT_ASSIGN['default'])
        self.assertEqual(len(got['categories']), len(floor_store.CATEGORY_LABELS))
        if floor_store.MANIFEST.is_file():
            ids = [scene['id'] for scene in got['scenes']]
            self.assertIn('synthwave', ids)
            self.assertTrue(all(scene['thumb'].startswith('data:image/jpeg;base64,') for scene in got['scenes']))
            self.assertTrue(set(floor_store.DEFAULT_ASSIGN['category'].values()) <= set(ids))
            self.assertEqual(set(got['gear']), {'default', *floor_store.CATEGORY_LABELS})
        chosen = floor_store.view({}, {}, look={'assign': {'default': 'space', 'category': {'amp': 'stage'}}})
        self.assertTrue(chosen['chosen'])
        self.assertEqual(chosen['assign'], {'default': 'space', 'category': {'amp': 'stage'}})


if __name__ == '__main__':
    unittest.main()
