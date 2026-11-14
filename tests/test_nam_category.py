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

    def test_model_lock_follows_how_the_capture_was_chosen(self):
        view = (ROOT / 'tone_view.js').read_text(encoding='utf-8')
        lock = view[view.index('  // --- Model lock ----'):view.index('  // --- NAM block icon ----')]
        code = r'''
const assert=require('node:assert/strict');
const NULL={isNull:()=>true,equals:()=>false};
const profile={namCategory:true};
let query=null;
const namView={setModelLockQuery:q=>{query=q;},applyModelLock:()=>{},modelSlot:()=>slots[current.name]};
const sent=[],send=value=>sent.push(value);
const slots={block1:22,block2:9,block3:null};
const controller=name=>({name,isNull:()=>false,equals:o=>o===current,
  add:offset=>({readPointer:()=>'editor',readS32:()=>0})});
let current=controller('block1'),selectedController=NULL;
const controllerReady=c=>!!c&&!c.isNull(),block=c=>c.name;
const targetBinding={peekSlot:(editor,dsp,name)=>slots[name]};
''' + lock + r'''
noteCursor(current);
// From a preset: locked; the padlock unlocks and locks it again.
assert.equal(query.state(),'locked');
query.toggle();
assert.equal(query.state(),'unlocked');
query.toggle();
assert.equal(query.state(),'locked');
query.toggle();
setLockState(current,'locked','capture-row');      // a capture picked from the list locks it
assert.equal(query.state(),'locked');
// A plain NAM A2 Lite block: unlocked, no padlock, and a click cannot lock it.
current=controller('block2');noteCursor(current);
plainPicked(current);
assert.equal(query.state(),'plain');
query.toggle();
assert.equal(query.state(),'plain');
setLockState(current,'locked','capture-row');
assert.equal(query.state(),'locked');
// Not a NAM block: no padlock. The control on screen belongs to another block: no answer yet.
current=controller('block3');noteCursor(current);
assert.equal(query.state(),'none');
const other=controller('block1');noteCursor(other);
assert.equal(query.state(),null);                   // modelSlot() is still block3's
'''
        run = subprocess.run(['node'], input=code, capture_output=True, text=True, encoding='utf-8', timeout=15)
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)


    def test_the_open_capture_list_follows_the_floor_and_capture_types(self):
        view = (ROOT / 'tone_view.js').read_text(encoding='utf-8').replace('\r\n', '\n')
        rows = view[view.index('  function captureGroup(gear) {'):view.index('  function chooseCapture(')]
        refresh = view[view.index('  // --- Keeping the open capture list current'):view.index('  // --- NAM block icon ----')]
        code = r"""
const assert=require('node:assert/strict');
const NULL={isNull:()=>true,equals:o=>o===NULL,toString:()=>'0'};
const Memory={allocUtf8String:text=>({text})};
const method=()=>()=>{};
const profile={namCategory:true}, tiles=[{block:'NAM A2 Lite'}], selectionEvent=1;
const NAM_CATEGORY_NAMES=['NAM Amp','NAM Amp + cab','NAM Pedal','NAM Other'];
let active=null,command=null,workflowRequest=null,toneImport=null,pendingCapture=null;
const sent=[],send=v=>sent.push(v);
const captureRows=new Map(),captureLabels=new Map();
let installed=[];
let blockSlot=22;
const namView={installedCaptures:()=>installed,requestSlotCheck(){},libraryReady:()=>true,modelSlot:()=>blockSlot,
  setModelLockQuery(){},applyModelLock(){}};
const ptr=(fields={})=>({fields,isNull:()=>false,equals(o){return o===this;},toString:()=>'p',
  add(off){return {readPointer:()=>fields[off]??NULL,readS32:()=>fields[off]??0};}});
const list=texts=>{const l=ptr();l.rows=texts.map(t=>({text:t}));l.selected=-1;return l;};
const row=(l,i)=>{const r=l.rows[i];return {isNull:()=>!r,readUtf8String:()=>r.text,
  add:off=>({readPointer:()=>r['p'+off]??NULL,readS32:()=>r['p'+off]??0})};};
const rowCount=l=>l.rows.length,getIrRow=(l,i)=>row(l,i);
const selectedRow=l=>l.selected<0?NULL:row(l,l.selected);
const clearSelection=l=>{l.selected=-1;}, selectRow=(l,on,i)=>{l.selected=i;};
const appendRow=(l,at,label)=>{l.rows.push({text:label.text});};
const categories=list(['Looper',...NAM_CATEGORY_NAMES]);
const models=list([]);
let rebuilds=0;
const modelEvent=(c,ev,sender)=>{  // HX Edit: a category choice rebuilds the model column
  rebuilds++; const name=categories.rows[categories.selected].text;
  models.rows=[{text:'NAM A2 Lite'+' '.repeat(NAM_CATEGORY_NAMES.indexOf(name)+1)}]; models.selected=0;};
const controller=ptr({0x50:categories,0x58:models,0x80:0});
let selectedController=controller;
const controllerReady=c=>!!c&&!c.isNull(), block=()=>'block0';
const targetBinding={peekSlot:()=>blockSlot};
""" + rows + refresh + r"""
// HX Edit built NAM Other before the startup slot check: no captures yet.
categories.selected=4; modelEvent(controller,1,categories);
appendCaptureRows({header:models.rows[0].text,list:models,image:NULL,index:0,model:NULL});
assert.deepEqual(models.rows.map(r=>r.text),['NAM A2 Lite    ']);
categoryList=categories; noteCursor(controller);
refreshCaptureList(); assert.equal(rebuilds,1);                 // nothing changed yet
// The check finishes: the list moves to the block's own category and lists its captures.
installed=[{slot:10,name:'BRIT50 Plexi',gear:''},{slot:22,name:'BRIT50 HG',gear:'amp-cab'}];
listCheckAt=0; refreshCaptureList();
assert.equal(rebuilds,2);
assert.equal(categories.rows[categories.selected].text,'NAM Amp + cab');
assert.deepEqual(models.rows.map(r=>r.text),['NAM A2 Lite  ','22: BRIT50 HG']);
assert.equal(models.rows[models.selected].text,'22: BRIT50 HG');   // the loaded capture is highlighted
listCheckAt=0; refreshCaptureList(); assert.equal(rebuilds,2);  // no change: no rebuild
// A type changed in the NAM tab: the open list is rebuilt.
installed=[{slot:10,name:'BRIT50 Plexi',gear:'amp-cab'},{slot:22,name:'BRIT50 HG',gear:'amp-cab'}];
listCheckAt=0; refreshCaptureList();
assert.deepEqual(models.rows.map(r=>r.text),['NAM A2 Lite  ','22: BRIT50 HG','10: BRIT50 Plexi']);
// The block's capture changes to one of another type: its category comes up.
installed=[{slot:10,name:'BRIT50 Plexi',gear:''},{slot:22,name:'BRIT50 HG',gear:'amp-cab'}];
listCheckAt=0; refreshCaptureList(); assert.equal(rebuilds,4);     // the type change first
blockSlot=10; listCheckAt=0; refreshCaptureList(); assert.equal(rebuilds,5);
assert.equal(categories.rows[categories.selected].text,'NAM Other');
assert.equal(models.rows[models.selected].text,'10: BRIT50 Plexi');
// Opening another NAM category by hand is left alone while nothing changes.
categories.selected=3; modelEvent(controller,1,categories);
appendCaptureRows({header:models.rows[0].text,list:models,image:NULL,index:0,model:NULL});
listCheckAt=0; refreshCaptureList(); assert.equal(rebuilds,6); assert.equal(categories.rows[categories.selected].text,'NAM Pedal');
// Not while a preview is running, and not when a stock category is on show.
installed=[]; active={transfer:{}}; listCheckAt=0; refreshCaptureList(); assert.equal(rebuilds,6);
active=null; categories.selected=0; listCheckAt=0; refreshCaptureList(); assert.equal(rebuilds,6);
// Nothing of the library on the Floor (a new install): the check is asked for again only until
// it has completed, not every two seconds for as long as the empty list is shown.
let asked=0, done=false; namView.requestSlotCheck=()=>{asked++;}; namView.slotCheckDone=()=>done;
categories.selected=4; builtSignature=''; installed=[];
listCheckAt=0; checkAskedAt=0; refreshCaptureList(); assert.equal(asked,1);
listCheckAt=0; refreshCaptureList(); assert.equal(asked,1);          // at most every two seconds
done=true; listCheckAt=0; checkAskedAt=0; refreshCaptureList(); assert.equal(asked,1);
"""
        run = subprocess.run(['node'], input=code, capture_output=True, text=True, encoding='utf-8', timeout=15)
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)

class ModelLockResourceTests(unittest.TestCase):
    def test_padlocks_are_appended_to_the_image_bundle_and_the_parameter_row(self):
        import struct
        from nam_catalog import (MODEL_LOCK_BUTTONS, MODEL_LOCK_IMAGES, NAM_CATEGORY_ICONS,
                                 add_bundle_images, model_lock_slider_xml)
        png = lambda w, h: b'\x89PNG\r\n\x1a\n' + bytes(8) + struct.pack('>II', w, h) + b'IEND\xaeB`\x82'
        images = [png(14, 28), png(10, 24)]
        body = b''.join(images)
        bundle = (b'XGIM' + struct.pack('>III', 16 + len(body), 2, 0) + body +
                  struct.pack('>5I', 1, 16, len(images[0]), 2, 1) + struct.pack('>5I', 2, 16 + len(images[0]), len(images[1]), 4, 1))
        lock = (NAM_CATEGORY_ICONS / MODEL_LOCK_IMAGES[0][1]).read_bytes()
        patched = add_bundle_images(bundle, [(7001, lock, 4)])
        table, count, _ = struct.unpack('>III', patched[4:16])
        entries = [struct.unpack('>5I', patched[table + 20 * k:table + 20 * k + 20]) for k in range(count)]
        self.assertEqual([e[0] for e in entries], [1, 2, 7001])
        self.assertEqual(patched[entries[2][1]:entries[2][1] + entries[2][2]], lock)
        self.assertEqual(patched[16:16 + len(body)], body)                    # stock images untouched
        self.assertEqual(add_bundle_images(patched, [(7001, lock, 4)]), patched)
        for _, name in MODEL_LOCK_IMAGES:                                        # 16x16, four frames
            data = (NAM_CATEGORY_ICONS / name).read_bytes()
            self.assertEqual(struct.unpack('>II', data[16:24]), (16, 64), name)
        slider = b'<xg><panel_widget id="slvw"><text_menu_widget id="menu" width="150"><anchor_pos right="23"/></text_menu_widget></panel_widget></xg>'
        once = model_lock_slider_xml(slider)
        self.assertEqual(model_lock_slider_xml(once), once)
        ids = [identity for identity, _, _ in MODEL_LOCK_BUTTONS]
        for identity in ids:
            self.assertIn(f'id="{identity}"'.encode(), once)
        self.assertEqual(once.count(b'<visible>no</visible>'), 2)
        self.assertEqual(once.count(b'right="178"'), 2)
        view = (ROOT / 'nam_view.js').read_text(encoding='utf-8')
        packed = {identity: int.from_bytes(identity.encode(), 'big') for identity in ids}
        self.assertIn(f"locked: {hex(packed['nlkd'])}, unlocked: {hex(packed['nulk'])}", view)

if __name__ == '__main__':
    unittest.main()
