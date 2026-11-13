"""The NAM block category: catalog groups and the capture rows listed under them. No device."""
from pathlib import Path
import shutil
import subprocess
import unittest

from nam_catalog import NAM_CATEGORY_GROUPS, NAM_CATEGORY_ICONS, NAM_SECTION_AFTER, nam_category_rows

ROOT = Path(__file__).resolve().parents[1]


class NamCategoryCatalogTests(unittest.TestCase):
    def test_each_category_has_the_block_row_marked_by_its_trailing_spaces(self):
        models = [{'id\0': 'HD2_AmpNAMLite\0', 'name\0': 'NAM A2 Lite\0', 'image\0': 'x.png\0', 'params\0': []}]
        rows = [nam_category_rows(index, models) for index in range(1, len(NAM_CATEGORY_GROUPS) + 1)]
        self.assertEqual([name for name, _ in NAM_CATEGORY_GROUPS], ['NAM Amp', 'NAM Amp + cab', 'NAM Pedal', 'NAM Other'])
        self.assertEqual([row[0]['name\0'] for row in rows],
                         ['NAM A2 Lite \0', 'NAM A2 Lite  \0', 'NAM A2 Lite   \0', 'NAM A2 Lite    \0'])
        # The same model identity everywhere; the source row is not modified.
        self.assertTrue(all(row[0]['id\0'] == 'HD2_AmpNAMLite\0' for row in rows))
        self.assertEqual(models[0]['name\0'], 'NAM A2 Lite\0')

    def test_the_divider_is_a_line_on_the_last_stock_row_and_every_icon_exists(self):
        # No divider category: tone_view.js sets HX Edit's own separator flag on this row.
        self.assertEqual(NAM_SECTION_AFTER, 'Looper')
        # The block-icon code finds each category's record by these exact names.
        names = ', '.join(f"'{name}'" for name, _ in NAM_CATEGORY_GROUPS)
        self.assertIn(f"const NAM_CATEGORY_NAMES = [{names}];",
                      (ROOT / 'tone_view.js').read_text(encoding='utf-8'))
        view = (ROOT / 'tone_view.js').read_text(encoding='utf-8')
        self.assertIn("header === 'Looper'", view)
        self.assertIn("add(0x114).writeU32(", view)
        for _, name in NAM_CATEGORY_GROUPS:
            data = (NAM_CATEGORY_ICONS / name).read_bytes()
            self.assertEqual(data[:8], b'\x89PNG\r\n\x1a\n', name)
            self.assertEqual(int.from_bytes(data[16:20], 'big'), 38, name)   # stock category icons are 38x38
            self.assertEqual(int.from_bytes(data[20:24], 'big'), 38, name)


@unittest.skipUnless(shutil.which('node'), 'Node is required for the capture-row replay')
class NamCategoryRowTests(unittest.TestCase):
    def test_captures_are_listed_under_the_group_that_matches_their_type(self):
        view = (ROOT / 'tone_view.js').read_text(encoding='utf-8')
        helpers = view[view.index('  function captureGroup(gear) {'):view.index('  function appendCaptureRows(frame) {')]
        code = r'''
const assert=require('node:assert/strict');
const installed=[{slot:127,name:'Deluxe Reverb',gear:'amp-cab'},{slot:9,name:'FNDR BFDRI VB Clean',gear:'amp-cab'},
  {slot:13,name:'Tube Screamer Gain4',gear:'pedal'},{slot:22,name:'Friedman Brit 50 Teaser Tone from Capture Pack – BRIT50 HG BRI CAB',gear:'amp-cab'},
  {slot:40,name:'Plexi Bright',gear:'amp'},{slot:41,name:'Acoustic Guitar',gear:'experimental'},{slot:42,name:'Unsorted thing',gear:''}];
const namView={installedCaptures:()=>installed};
''' + helpers + r'''
assert.deepEqual(['amp','preamp','amp-cab','amp-pedal-cab','pedal','pedal-amp','','studio','experimental'].map(captureGroup),
  [1,1,2,2,3,3,4,4,4]);
const slots=header=>capturesUnder(header).map(capture=>capture.slot);
assert.deepEqual(slots('NAM A2 Lite '),[40]);                 // Amp
assert.deepEqual(slots('NAM A2 Lite  '),[127,9,22]);          // Amp + cab, by name
assert.deepEqual(slots('NAM A2 Lite   '),[13]);               // Pedal
assert.deepEqual(slots('NAM A2 Lite    '),[41,42]);           // Other
assert.equal(slots('NAM A2 Lite').length,7);                  // Amp > NAM, or names trimmed: everything
// Row labels lead with the slot, use the profile name of a long library name, and are unique.
assert.equal(captureRowLabel(installed[0]),'127: Deluxe Reverb');
assert.equal(captureRowLabel(installed[3]),'22: BRIT50 HG BRI CAB');
assert.equal(captureRowLabel({slot:5,name:'A very long capture name that keeps going and going'}),'5: A very long capture name th…');
assert.equal(new Set(installed.map(captureRowLabel)).size,installed.length);
'''
        run = subprocess.run(['node'], input=code, capture_output=True, text=True, encoding='utf-8', timeout=15)
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)


if __name__ == '__main__':
    unittest.main()
