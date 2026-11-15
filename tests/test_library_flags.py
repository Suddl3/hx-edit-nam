"""Favourites and guitar/bass: stored per capture, shared by the NAM tab and the library window."""
from pathlib import Path
import json
import re
import tempfile
from threading import Event
import unittest

from nam_library import Library, instrument_auto, instrument_of, render_library
from nam_sidebar import NamSidebar, sidebar_rows


class InstrumentRuleTests(unittest.TestCase):
    def test_bass_only_when_the_name_says_so(self):
        for name in ('Ampeg SVT-CL', 'Darkglass B7K Ultra', 'SansAmp Bass Driver', 'Aguilar Tone Hammer',
                     'Gallien-Krueger 800RB', 'Markbass Little Mark', 'BASS DI clean', 'b7k pedal'):
            self.assertEqual(instrument_auto({'name': name}), 'bass', name)
        for name in ('Fender Bassman 59', 'FNDR Bassbreaker 15', 'Jcm800', 'Klon Gain=0', 'Bassy fuzz', ''):
            self.assertEqual(instrument_auto({'name': name}), 'guitar', name)
        # The Floor's own IR name counts too (often the original file name).
        self.assertEqual(instrument_auto({'name': 'My rig', 'irName': 'svt clean'}), 'bass')

    def test_pedals_and_studio_gear_default_to_any(self):
        self.assertEqual(instrument_auto({'name': 'Klon Gain=0', 'gear': 'pedal'}), 'any')
        self.assertEqual(instrument_auto({'name': 'LA-2A', 'gear': 'studio'}), 'any')
        self.assertEqual(instrument_auto({'name': 'Plexi', 'gear': 'amp'}), 'guitar')
        self.assertEqual(instrument_auto({'name': 'Mystery', 'gear': ''}), 'guitar')     # unsorted: mostly amps
        self.assertEqual(instrument_auto({'name': 'Darkglass B7K', 'gear': 'pedal'}), 'bass')
        self.assertEqual(instrument_of({'name': 'Klon', 'gear': 'pedal', 'instrument': 'guitar'}), 'guitar')

    def test_a_choice_overrides_the_name(self):
        self.assertEqual(instrument_of({'name': 'Ampeg SVT', 'instrument': 'guitar'}), 'guitar')
        self.assertEqual(instrument_of({'name': 'Plexi', 'instrument': 'bass'}), 'bass')
        self.assertEqual(instrument_of({'name': 'Plexi', 'instrument': 'banjo'}), 'guitar')


class LibraryFlagTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        folder = Path(self.temp.name)
        self.library = Library(folder / 'library')
        source = folder / 'SVT.nam'
        source.write_text('{"weights":[1]}')
        self.svt = self.library.add(source, name='Ampeg SVT')
        source = folder / 'Plexi.nam'
        source.write_text('{"weights":[2]}')
        self.plexi = self.library.add(source, name='Plexi')

    def tearDown(self):
        self.temp.cleanup()

    def rows(self):
        page = render_library(self.library.entries())
        return {row['id']: row for row in json.loads(re.search(r'const rows = (\[.*?\]);\n', page).group(1))}

    def test_favourite_and_instrument_are_kept_and_shown(self):
        rows = self.rows()
        self.assertEqual((rows[self.svt]['instrument'], rows[self.svt]['instrumentChosen']), ('bass', False))
        self.assertEqual((rows[self.plexi]['instrument'], rows[self.plexi]['favorite']), ('guitar', False))
        self.library.set_favorite(self.plexi, True)
        self.library.set_instrument(self.svt, 'guitar')
        again = Library(self.library.folder)                     # the next launch
        rows = {row['contentId']: row for row in sidebar_rows(again)}
        self.assertIs(rows[self.plexi]['favorite'], True)
        self.assertEqual((rows[self.svt]['instrument'], rows[self.svt]['instrumentChosen']), ('guitar', True))
        again.set_favorite(self.plexi, False)
        again.set_instrument(self.svt, None)                     # back to automatic
        entries = again.entries()
        self.assertNotIn('favorite', entries[self.plexi])
        self.assertNotIn('instrument', entries[self.svt])
        self.assertEqual(self.rows()[self.svt]['instrument'], 'bass')

    def test_any_is_kept_and_reaches_the_editor(self):
        self.library.set_instrument(self.plexi, 'any')
        self.assertEqual(self.rows()[self.plexi]['instrument'], 'any')
        self.assertIn("instrument=instrument_of(entries[identity])", (Path(__file__).parents[1] / 'nam_library.py').read_text(encoding='utf-8'))

    def test_bad_values_are_refused(self):
        with self.assertRaises(ValueError):
            self.library.set_instrument(self.svt, 'banjo')
        with self.assertRaises(ValueError):
            self.library.set_favorite(self.svt, 'yes')
        with self.assertRaises(KeyError):
            self.library.set_favorite('f' * 64, True)

    def test_the_shared_view_carries_both_filters(self):
        self.assertEqual(self.library.view_state()['instrument'], '*')
        self.assertTrue(self.library.update_view({'instrument': 'bass', 'favorites': True}))
        self.assertEqual({k: self.library.view_state()[k] for k in ('instrument', 'favorites')},
                         {'instrument': 'bass', 'favorites': True})
        self.assertTrue(self.library.update_view({'filter': 'amp'}))   # the docked pane's partial view
        self.assertEqual(self.library.view_state()['instrument'], 'bass')
        self.assertFalse(self.library.update_view({'instrument': 'banjo'}))
        self.assertFalse(self.library.update_view({'favorites': 1}))
        self.assertTrue(self.library.update_view({'sortKey': 'instrument'}))
        self.assertEqual(self.library.view_state()['layout'], 'list')
        self.assertTrue(self.library.update_view({'layout': 'grid'}))
        self.assertFalse(self.library.update_view({'layout': 'tiles'}))

    def test_filters_layout_and_sort_are_kept_but_not_search_or_selection(self):
        self.assertTrue(self.library.update_view({'instrument': 'bass', 'favorites': True, 'layout': 'grid',
                                                  'filter': 'amp', 'sortKey': 'added', 'sortDirection': -1,
                                                  'query': 'plexi', 'selected': self.plexi}))
        again = Library(self.library.folder).view_state()                 # the next launch
        self.assertEqual({key: again[key] for key in ('instrument', 'favorites', 'layout', 'filter', 'sortKey', 'sortDirection')},
                         {'instrument': 'bass', 'favorites': True, 'layout': 'grid', 'filter': 'amp',
                          'sortKey': 'added', 'sortDirection': -1})
        self.assertEqual((again['query'], again['selected']), ('', None))
        # A damaged or edited file: each bad value keeps its default, the good ones stay.
        (self.library.folder / 'library-view.json').write_text('{"instrument": "banjo", "layout": "grid"}', encoding='utf-8')
        view = Library(self.library.folder).view_state()
        self.assertEqual((view['instrument'], view['layout']), ('*', 'grid'))
        (self.library.folder / 'library-view.json').write_text('not json', encoding='utf-8')
        self.assertEqual(Library(self.library.folder).view_state()['layout'], 'list')

    def test_the_nam_tab_menu_sets_both(self):
        sent = []

        class Script:
            def post(self, value):
                pass
        bridge = NamSidebar(Script(), self.library, Event(), process_factory=lambda *a, **k: self.fail('no helper'))
        bridge.send = lambda message: sent.append(message) or True
        bridge._handle_action({'type': 'action', 'action': 'favorite', 'contentId': self.svt, 'favorite': True})
        bridge._handle_action({'type': 'action', 'action': 'instrument', 'contentId': self.plexi, 'instrument': 'bass'})
        bridge._handle_action({'type': 'action', 'action': 'instrument', 'contentId': self.plexi, 'instrument': 'banjo'})
        self.assertEqual([m['error'] for m in sent][:2], ['', ''])
        self.assertIn('Unknown instrument', sent[2]['error'])
        entries = self.library.entries()
        self.assertIs(entries[self.svt].get('favorite'), True)
        self.assertEqual(entries[self.plexi].get('instrument'), 'bass')


class LibraryPageTests(unittest.TestCase):
    def test_the_page_has_the_controls_and_shares_the_view(self):
        page = render_library({}, {'instrument': 'bass', 'favorites': True})
        self.assertIn('<option value="*">All</option>', page)
        self.assertIn("new Option('Both', 'any')", page)
        for marker in ('id="instrument"', 'id="favorites"', "'favorite:'", "'instrument:'",
                       'instrument: instrument.value', "favorites: favorites.getAttribute('aria-pressed') === 'true'",
                       'id="layout"', 'id="tiles"', "layout: gridShown() ? 'grid' : 'list'", 'function renderTiles('):
            self.assertIn(marker, page)


if __name__ == '__main__':
    unittest.main()
