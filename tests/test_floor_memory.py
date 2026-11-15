"""What the editor remembers about the Floor between launches; no device or editor required."""
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from floor_memory import FloorMemory

ROOT = Path(__file__).resolve().parents[1]
CONTENT = 'a' * 64


class FloorMemoryTests(unittest.TestCase):
    def test_preview_record_and_slots_survive_a_restart(self):
        with tempfile.TemporaryDirectory() as folder:
            memory = FloorMemory(folder)
            self.assertIsNone(memory.preview())
            self.assertEqual(memory.slots(), [])
            record = {'dsp': 0, 'block': 'block3', 'originalSlot': 22, 'name': 'BRIT50 1a2b3c'}
            self.assertTrue(memory.receive({'type': 'tonePreviewState', 'state': record}))
            self.assertTrue(memory.receive({'type': 'slotMemory', 'slot': 9, 'name': 'Deluxe', 'contentId': CONTENT}))
            self.assertTrue(memory.receive({'type': 'slotMemory', 'slot': 40, 'name': 'Room IR', 'contentId': None}))
            self.assertFalse(memory.receive({'type': 'toneTrace'}))

            again = FloorMemory(folder)                                # the next launch
            self.assertEqual(again.preview(), record)
            self.assertEqual(again.slots(), [[9, 'Deluxe', CONTENT], [40, 'Room IR', None]])
            again.receive({'type': 'tonePreviewState', 'state': None})  # the block is back on its capture
            again.receive({'type': 'slotMemory', 'slot': 9, 'forget': True})
            last = FloorMemory(folder)
            self.assertIsNone(last.preview())
            self.assertEqual(last.slots(), [[40, 'Room IR', None]])
            self.assertFalse(list(Path(folder).glob('*.tmp')))

    def test_anything_that_is_not_a_valid_record_is_ignored(self):
        with tempfile.TemporaryDirectory() as folder:
            memory = FloorMemory(folder)
            for state in ({'dsp': 2, 'block': 'block0', 'originalSlot': 9, 'name': 'x'},
                          {'dsp': 0, 'block': '../x', 'originalSlot': 9, 'name': 'x'},
                          {'dsp': 0, 'block': 'block0', 'originalSlot': 128, 'name': 'x'},
                          {'dsp': 0, 'block': 'block0', 'originalSlot': 9, 'name': ''},
                          {'dsp': True, 'block': 'block0', 'originalSlot': 9, 'name': 'x'}, 'text'):
                memory.receive({'type': 'tonePreviewState', 'state': state})
                self.assertIsNone(memory.preview())
            for message in ({'slot': 128, 'name': 'x', 'contentId': CONTENT}, {'slot': 0, 'name': 'x', 'contentId': None},
                            {'slot': 9, 'name': '', 'contentId': CONTENT}, {'slot': 9, 'name': 'x', 'contentId': 'short'},
                            {'slot': '9', 'name': 'x', 'contentId': CONTENT}):
                memory.receive({'type': 'slotMemory', **message})
            self.assertEqual(memory.slots(), [])

    def test_a_damaged_file_is_an_empty_memory_not_a_failed_launch(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder, 'floor-memory.json')
            for damaged in (b'', b'{"slots": ', b'[]', b'\xff\xfe'):
                path.write_bytes(damaged)
                memory = FloorMemory(folder)
                self.assertEqual((memory.preview(), memory.slots()), (None, []))
            path.write_text(json.dumps({'preview': {'dsp': 9}, 'slots': {'9': {'name': 'Deluxe', 'contentId': CONTENT},
                                                                      '200': {'name': 'x', 'contentId': None},
                                                                      'nine': {'name': 'x', 'contentId': None}}}), encoding='utf-8')
            memory = FloorMemory(folder)
            self.assertEqual((memory.preview(), memory.slots()), (None, [[9, 'Deluxe', CONTENT]]))


@unittest.skipUnless(shutil.which('node'), 'Node is required for the slot-check replay')
class RememberedSlotCheckTests(unittest.TestCase):
    def test_remembered_slots_are_not_read_back_and_new_reads_are_learned(self):
        code = r'''
const assert=require('node:assert/strict');
const exportsMade=[],learned=[],statuses=[];let now=0;
// Slot 9 was read on an earlier launch and still has the same name; 40 is a remembered plain IR;
// 127 is new to this PC; 20 was renamed since it was remembered.
const memory=new Map([[9,{name:'Deluxe',contentId:'a'.repeat(64)}],[40,{name:'Room IR',contentId:null}],
  [20,{name:'Old name',contentId:'c'.repeat(64)}]]);
const accepted=[];
const automatic=createAutomaticSlotDiscovery({now:()=>now,status:s=>statuses.push(s),prepare:p=>plans.push(p),
  export:p=>{exportsMade.push(p.slot);return true;},
  remembered:(slot,name)=>{const m=memory.get(slot);if(!m||m.name!==name){memory.delete(slot);return false;}
    if(m.contentId)accepted.push(slot);return true;},
  learned:(slot,status,name,contentId)=>learned.push([slot,status,name,contentId||null])});
const plans=[];
const names=[[9,'Deluxe'],[20,'New name'],[40,'Room IR'],[127,'Plexi']];
const snap={device:0x210001,identity:'floor',signature:JSON.stringify(names),slots:names.map(n=>n[0]),slotSignatures:names,revision:0};
const tick=(dt=0)=>{now+=dt;automatic.tick(snap);};
automatic.host({generation:0,ready:true,present:true});automatic.requestVerification();tick();tick(1501);
// Only the renamed slot and the new one are read back.
function finish(status,contentId){const p=plans.at(-1);automatic.prepared({token:p.token,path:'E:\\x.wav'});tick();
  automatic.result({token:p.token,status,contentId});tick();}
finish('matched','d'.repeat(64));finish('ir');
assert.deepEqual(exportsMade,[20,127]);assert.deepEqual(accepted,[9]);
assert.deepEqual(learned,[[20,'matched','New name','d'.repeat(64)],[127,'ir','Plexi',null]]);
assert.equal(statuses.at(-1).state,'complete');assert.equal(statuses.at(-1).checked,4);
// Everything remembered: the next check reads nothing at all.
memory.set(20,{name:'New name',contentId:'d'.repeat(64)});memory.set(127,{name:'Plexi',contentId:null});
automatic.invalidate();automatic.requestVerification();tick();tick(1501);tick();
assert.deepEqual(exportsMade,[20,127]);assert.equal(statuses.at(-1).state,'complete');
'''
        source = (ROOT / 'automatic_slot_discovery.js').read_text(encoding='utf-8')
        run = subprocess.run(['node'], input=source + '\n' + code, capture_output=True, text=True,
                             encoding='utf-8', timeout=15)
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)

    def test_connecting_recalls_remembered_slots_without_reading_any_back(self):
        # Started with the Floor off, the slot check used to wait for the NAM tab. Now each connect
        # (and each full refresh from the Floor) accepts what this PC remembers, by itself; a slot
        # that would need reading back still waits for a request, as before.
        code = r'''
const assert=require('node:assert/strict');
const plans=[],states=[];let now=0;
const memory=new Map([[9,{name:'Deluxe',contentId:'a'.repeat(64)}],[40,{name:'Room IR',contentId:null}]]);
const automatic=createAutomaticSlotDiscovery({now:()=>now,recallOnConnect:true,status:s=>states.push(s.state),
  prepare:p=>plans.push(p),export:()=>true,
  remembered:(slot,name)=>{const m=memory.get(slot);return !!m&&m.name===name;}});
let names=[[9,'Deluxe'],[40,'Room IR']];
const snap=()=>({device:0x210001,identity:'floor',signature:JSON.stringify(names),slots:names.map(n=>n[0]),slotSignatures:names,revision:0});
const tick=(dt=0)=>{now+=dt;automatic.tick(snap());};
tick(5000);assert.equal(automatic.needsSnapshot(),false,'nothing before the Floor is there');
automatic.host({generation:0,ready:true,present:true});
assert.equal(automatic.needsSnapshot(),true);tick();tick(1501);
assert.equal(states.at(-1),'complete');assert.equal(plans.length,0);assert.equal(automatic.needsSnapshot(),false);
// The Floor's full refresh right after connecting discards proof: recalled again.
automatic.invalidate();assert.equal(automatic.needsSnapshot(),true);tick();tick(1501);
assert.equal(states.at(-1),'complete');assert.equal(plans.length,0);
// A recall completion does not make a later changed slot read back by itself.
automatic.invalidate([9]);tick(5000);tick(5000);assert.equal(plans.length,0);
// A slot this PC does not know: the recall stops without reading, keeping what it accepted.
automatic.invalidate();names=[[9,'Deluxe'],[40,'Room IR'],[127,'Plexi']];tick();tick(1501);
assert.equal(plans.length,0);assert.equal(states.at(-1),'verification-needed');assert.equal(automatic.needsSnapshot(),false);
assert.deepEqual(automatic.inspect().done.sort((a,b)=>a-b),[9,40]);
// The NAM tab's request then reads only the unknown slot.
automatic.requestVerification();tick();tick(1501);assert.deepEqual(plans.map(p=>p.slot),[127]);
// Off: nothing more. Without the option (the old contract) connecting asks nothing.
automatic.host({generation:1,ready:true,present:false});assert.equal(automatic.needsSnapshot(),false);
const plain=createAutomaticSlotDiscovery({now:()=>now,status:()=>{},prepare:()=>{},export:()=>true,remembered:()=>true});
plain.host({generation:0,ready:true,present:true});assert.equal(plain.needsSnapshot(),false);
'''
        source = (ROOT / 'automatic_slot_discovery.js').read_text(encoding='utf-8')
        run = subprocess.run(['node'], input=source + '\n' + code, capture_output=True, text=True,
                             encoding='utf-8', timeout=15)
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)

    def test_the_native_view_remembers_by_the_slots_own_name_not_the_change_signature(self):
        # First Candidate62 session on the Floor: the slot check's signature is the name plus the
        # bytes after it in the row (about 250 characters, different on every launch). Using it as
        # the name stored 2 of 27 slots and would have matched none of them next time.
        view = (ROOT / 'nam_view.js').read_text(encoding='utf-8')
        methods = view[view.index('    acceptRememberedSlot(slot) {'):view.index('    invalidateVerifiedSlots(')]
        snapshot = view[view.index('  function discoverySnapshot('):view.index('  function exportDiscoverySlot(')]
        code = r'''
const assert=require('node:assert/strict'),sent=[];
const send=message=>sent.push(message);
const rememberedSlots=new Map(),slotNames=new Map();let inventoryDirty=false;
function forgetRememberedSlot(slot){if(rememberedSlots.delete(slot))send({type:'slotMemory',slot,forget:true});}
const accepted=[];
const slotLedger={epoch:()=>7,accept:(epoch,slot,contentId)=>{accepted.push([epoch,slot,contentId]);return true;}};
// Rows as the native list presents them: the name, then unrelated bytes.
let junk='\x01��';
const names={9:'Deluxe Reverb 1a2b3c',40:'Room IR'};
const row=slot=>({isNull:()=>false,readCString:()=>names[slot]+junk.repeat(60),readUtf8String:()=>names[slot],
  add:offset=>({readU32:()=>offset===0x10c?Number(slot in names):0})});
const nil={isNull:()=>true},AUDITION_SLOT=128;
const libraryReady=()=>true,irController={isNull:()=>false,add:()=>({readPointer:()=>manager}),toString:()=>'ir'};
const manager={isNull:()=>false,add:offset=>({readU8:()=>0,readU32:()=>0x210001}),toString:()=>'manager'};
let discoveryLocked=false,exportPending=false,workflowBusy=false,audition=null,toneImport=null;
const namImportController=nil,flushInventoryPresentation=()=>{},irList=()=>({isNull:()=>false});
const discoveryCount=()=>128,getIrRow=(_list,index)=>row(index+1);
''' + snapshot + '\nconst api={\n' + methods + '\n};\n' + r'''
const first=discoverySnapshot(0);
assert.deepEqual(first.slots,[9,40]);assert.ok(first.slotSignatures[0][1].length>100,'the signature carries the trailing bytes');
assert.deepEqual([...slotNames],[[9,'Deluxe Reverb 1a2b3c'],[40,'Room IR']]);
// Learn what two read-backs found. The signature argument is ignored.
api.rememberSlot(9,'matched',first.slotSignatures[0][1],'a'.repeat(64));
api.rememberSlot(40,'ir',first.slotSignatures[1][1]);
api.rememberSlot(41,'matched','no such slot','b'.repeat(64));
assert.deepEqual(sent,[{type:'slotMemory',slot:9,name:'Deluxe Reverb 1a2b3c',contentId:'a'.repeat(64)},
  {type:'slotMemory',slot:40,name:'Room IR',contentId:null}]);
// The next launch: the bytes after the name differ, the name does not.
junk='\x7f�Q';discoverySnapshot(0);
assert.equal(api.acceptRememberedSlot(9),true);assert.deepEqual(accepted,[[7,9,'a'.repeat(64)]]);
assert.equal(api.acceptRememberedSlot(40),true);assert.equal(accepted.length,1,'a plain IR is known, not a capture');
assert.equal(api.acceptRememberedSlot(41),false);
// A slot whose name changed is forgotten and read back again.
names[9]='Something else';discoverySnapshot(0);
assert.equal(api.acceptRememberedSlot(9),false);assert.deepEqual(sent.at(-1),{type:'slotMemory',slot:9,forget:true});
// An emptied slot has no name to compare.
delete names[40];discoverySnapshot(0);assert.equal(slotNames.has(40),false);assert.equal(api.acceptRememberedSlot(40),false);
'''
        run = subprocess.run(['node'], input=code, capture_output=True, text=True, encoding='utf-8', timeout=15)
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)



class PresetUsageTests(unittest.TestCase):
    """Which presets use each IR slot, learned as presets are opened (the delete warning)."""

    def test_usage_is_kept_between_launches_and_bad_records_are_ignored(self):
        with tempfile.TemporaryDirectory() as folder:
            memory = FloorMemory(folder)
            self.assertEqual(memory.presets(), [])
            self.assertTrue(memory.receive({'type': 'presetUsage', 'setlist': 3, 'preset': 12, 'name': "Bass Pre",
                                            'slots': [24, 9, 24]}))
            memory.receive({'type': 'presetUsage', 'setlist': 3, 'preset': 30, 'name': 'New Preset', 'slots': []})
            memory.receive({'type': 'presetUsage', 'setlist': 3, 'preset': 7, 'name': 'Odd', 'slots': None})
            for bad in ({'setlist': 99, 'preset': 1, 'name': 'x', 'slots': []}, {'setlist': 1, 'preset': 1, 'name': 'x', 'slots': [128]},
                        {'setlist': 1, 'preset': 1, 'name': 7, 'slots': []}):
                memory.receive({'type': 'presetUsage', **bad})
            memory.receive({'type': 'presetUsage', 'setlist': 3, 'preset': 12, 'name': "Bass Pre", 'slots': [9]})  # saved again
            again = FloorMemory(folder)
            self.assertEqual(again.presets(), [[3, 7, 'Odd', None], [3, 12, "Bass Pre", [9]], [3, 30, 'New Preset', []]])
            stored = json.loads(Path(folder, 'floor-memory.json').read_text(encoding='utf-8'))
            self.assertRegex(stored['presets']['3:12']['seen'], r'^\d{4}-\d{2}-\d{2}$')

    def test_the_preset_header_and_the_warning(self):
        view = (ROOT / 'nam_view.js').read_text(encoding='utf-8')
        unpack = view[view.index('  function unpackPresetHeader(bytes) {'):view.index('  function currentPresetHeader() {')]
        using = view[view.index('  function presetLabel(entry, setlist) {'):view.index('  function confirmClearUsed(')]
        code = r'''
const assert=require('node:assert/strict');
''' + unpack + r'''
// The header as HX Edit held it for preset 030 on the test Floor (November 14).
const bytes=Uint8Array.from('83 66 cd 0a 78 67 00 68 87 6b cd 00 03 6c cd 00 1e 6d ab 4e 65 77 20 50 72 65 73 65 74 00 75 c2'
  .split(' ').map(h=>parseInt(h,16)));
const header=unpackPresetHeader(bytes);
assert.deepEqual(header,{setlist:3,preset:30,nameStart:0x13,nameLength:11});
assert.equal(String.fromCharCode(...bytes.slice(0x13,0x13+10)),'New Preset');
assert.deepEqual(unpackPresetHeader(Uint8Array.from([0x82,0x66,0x01,0x68,0x83,0x6c,0x05,0x6b,0x00,0x6d,0xa2,0x41,0x42])),
  {setlist:0,preset:5,nameStart:11,nameLength:2});
assert.equal(unpackPresetHeader(Uint8Array.from([0x92,1,2])),null);              // not a map
assert.equal(unpackPresetHeader(Uint8Array.from([0x81,0x66,0xdc,0,1])),null);    // unknown value type
assert.equal(unpackPresetHeader(bytes.slice(0,12)),null);                        // cut short
let current={setlist:3,preset:30,name:'New Preset'};
const currentPresetHeader=()=>current;
const presetUses=new Map([['3:12',{setlist:3,preset:12,name:"Bass Pre",slots:[9,24]}],
  ['3:30',{setlist:3,preset:30,name:'New Preset',slots:[24]}],['1:4',{setlist:1,preset:4,name:'Live',slots:[24]}],
  ['3:7',{setlist:3,preset:7,name:'Odd',slots:null}]]);
''' + using + r'''
assert.deepEqual(presetsUsing([24]),['004 Live (another setlist)',"012 Bass Pre"]);   // not the loaded one
assert.deepEqual(presetsUsing([24],false),['004 Live (another setlist)',"012 Bass Pre",'030 New Preset']);
assert.deepEqual(presetsUsing([5]),[]);
'''
        run = subprocess.run(['node'], input=code, capture_output=True, text=True, encoding='utf-8', timeout=15)
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        self.assertIn("if (users.length && !confirmClearUsed(selected, users)) return;", view)
        self.assertIn('0x134) === 6', view)                 # Yes/No, No by default


class LenientPresetSlotsTests(unittest.TestCase):
    def test_the_record_reads_slots_where_the_strict_check_gives_up(self):
        view = (ROOT / 'nam_view.js').read_text(encoding='utf-8')
        strict = view[view.index('function presetIrSlots(tree, irParameters) {'):view.index('// The IR slots a preset uses, for the record')]
        lenient = view[view.index('function presetIrSlotsLenient('):view.index('function previewSlotUnused(')]
        code = strict + lenient + r'''
const assert=require('node:assert/strict');
const ir={NAM:['Index'],IRMono:['Index'],Amp:[]};
// What 012 looked like to the strict reader: an unread value (variant 7) and a global node it
// does not know. The strict check refuses it; the record still finds the slots.
const tree={global:{'@tempo':120,odd:{unread:7}},globalNodes:{footswitch:{x:{unread:7}},
  snapshot1:{'@name':'B',controllers:{dsp0:{block1:{Index:{'@value':40}}},dsp1:{x:{}}}}},
  dsp0:{block0:{'@model':'Amp',Drive:5},block1:{'@model':'NAM',Index:12,Level:{unread:7}},block2:{'@model':'HelixPlugin_x'}},
  dsp1:{block0:{'@model':'IRMonoStereo',Index:128},block1:{'@model':'IRMono',Index:9}}};
assert.ok(presetIrSlots(tree,ir).reason);
assert.deepEqual([...presetIrSlotsLenient(tree,ir).slots].sort((a,b)=>a-b),[9,12,40]);   // not 128: the preview slot
assert.deepEqual([...presetIrSlotsLenient({global:{},globalNodes:{},dsp0:{},dsp1:{}},ir).slots],[]);
'''
        run = subprocess.run(['node'], input=code, capture_output=True, text=True, encoding='utf-8', timeout=15)
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)


class PickerSideTests(unittest.TestCase):
    def test_the_last_side_per_category_is_kept(self):
        with tempfile.TemporaryDirectory() as folder:
            memory = FloorMemory(folder)
            self.assertEqual(memory.sides(), {})
            memory.receive({'type': 'pickerSide', 'category': 'NAM Amp', 'value': 2})
            memory.receive({'type': 'pickerSide', 'category': 'Amp', 'value': 1})
            memory.receive({'type': 'pickerSide', 'category': 'NAM Amp', 'value': 99})
            for bad in ({'category': '', 'value': 1}, {'category': 'Amp', 'value': '1'}, {'category': 'x' * 41, 'value': 1}):
                memory.receive({'type': 'pickerSide', **bad})
            self.assertEqual(FloorMemory(folder).sides(), {'Amp': 1, 'NAM Amp': 99})

    def test_capture_sides_and_the_tagged_rows(self):
        view = (ROOT / 'tone_view.js').read_text(encoding='utf-8')
        sides = view[view.index('  const SIDE_ALL = 99'):view.index('  function shownCategory() {')]
        code = r'''
const assert=require('node:assert/strict');
const profile={pickerSides:{'NAM Amp':2,bad:'x'}};
''' + sides + r'''
assert.equal(rememberedSide('NAM Amp'),SIDE_BASS);
assert.equal(rememberedSide('NAM Pedal'),SIDE_ALL);          // never picked: All
assert.equal(rememberedSides.has('bad'),false);
const bass={instrument:'bass'},guitar={instrument:'guitar'},any={instrument:'any'},old={};
assert.deepEqual([bass,guitar,any,old].map(c=>onSide(c,SIDE_ALL)),[true,true,true,true]);
assert.deepEqual([bass,guitar,any,old].map(c=>onSide(c,SIDE_BASS)),[true,false,true,false]);
assert.deepEqual([bass,guitar,any,old].map(c=>onSide(c,SIDE_GUITAR)),[false,true,true,true]);
'''
        run = subprocess.run(['node'], input=code, capture_output=True, text=True, encoding='utf-8', timeout=15)
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)


class FavoriteSlotsTests(unittest.TestCase):
    """HX Edit favorites that play a NAM capture go when that capture's slot is cleared or replaced."""

    def test_the_slot_of_each_favorite_is_kept_between_launches(self):
        with tempfile.TemporaryDirectory() as folder:
            memory = FloorMemory(folder)
            self.assertEqual(memory.favorites(), {})
            self.assertTrue(memory.receive({'type': 'favoriteSlot', 'name': 'Plexi Crunch', 'slot': 18}))
            memory.receive({'type': 'favoriteSlot', 'name': 'Bass DI', 'slot': 40})
            memory.receive({'type': 'favoriteSlot', 'name': 'Preview', 'slot': 128})
            for bad in ({'name': '', 'slot': 3}, {'name': 'x', 'slot': 129}, {'name': 7, 'slot': 3},
                        {'name': 'x' * 65, 'slot': 3}, {'stage': 'note-failed', 'error': 'boom'}):
                self.assertTrue(memory.receive({'type': 'favoriteSlot', **bad}))
            memory.receive({'type': 'favoriteSlot', 'name': 'Bass DI', 'slot': None})       # deleted or renamed
            look = {'slot': 18, 'group': 2, 'icon': 'brit_stack'}
            memory.receive({'type': 'favoriteLook', 'name': 'Plexi Crunch', 'look': look})
            memory.receive({'type': 'favoriteLook', 'name': 'Preview', 'look': {'slot': 128, 'group': 9, 'icon': ''}})  # no group 9
            memory.receive({'type': 'favoriteLook', 'name': 'Gone', 'look': look})                                   # not a known favorite
            memory.receive({'type': 'favoriteLook', 'name': 'Preview', 'look': {'slot': 128, 'group': 5, 'icon': 'Bad/Name'}})
            again = FloorMemory(folder)
            self.assertEqual(again.favorites(), {'Plexi Crunch': 18, 'Preview': 128})
            self.assertEqual(again.looks(), {'Plexi Crunch': look})
            again.receive({'type': 'favoriteSlot', 'name': 'Plexi Crunch', 'slot': None})   # deleted: its look goes too
            self.assertEqual(FloorMemory(folder).looks(), {})
            self.assertEqual(again.presets(), [])

    def test_which_favorites_are_noted_and_removed(self):
        view = (ROOT / 'nam_view.js').read_text(encoding='utf-8')
        constants = view[view.index('  const NAM_MODEL_IDS'):view.index('  const favoriteSlots')]
        logic = view[view.index('  function favoriteChanges('):view.index('  function noteFavoriteEvent(')]
        code = constants + logic + r'''
const assert=require('node:assert/strict');
const NAM='HD2_AmpNAMLite', DIST='HD2_DistNAMLite';
const fav=(id,name,model=NAM)=>({id,name,model});
let list=[fav(1,'Alpaca','HD2_AmpUSDeluxeVib'),fav(2,'Plexi'),fav(3,'Fuzz',DIST)];
let known=new Map();
// Saved from a NAM block on slot 18: noted. From a stock block, or with no capture selected: not.
assert.deepEqual(favoriteChanges('Librarian_HelixFavoriteAdded',list,null,1,18,known),[['Plexi',18]]);
assert.deepEqual(favoriteChanges('Librarian_HelixFavoriteAdded',list,null,0,18,known),[['Alpaca',null]]);
assert.deepEqual(favoriteChanges('Librarian_HelixFavoriteAdded',list,null,2,null,known),[['Fuzz',null]]);
assert.deepEqual(favoriteChanges('Librarian_HelixFavoriteChanged',list,null,2,129,known),[['Fuzz',null]]);
known=new Map([['Plexi',18],['Fuzz',40]]);
// Replaced from a block on another slot: the new slot.
assert.deepEqual(favoriteChanges('Librarian_HelixFavoriteChanged',list,null,2,41,known),[['Fuzz',41]]);
// Renamed: the slot moves to the new name.
const renamed=[fav(1,'Alpaca','HD2_AmpUSDeluxeVib'),fav(2,'Plexi 2'),fav(3,'Fuzz',DIST)];
assert.deepEqual(favoriteChanges('Librarian_HelixFavoriteRenamed',renamed,['Alpaca','Plexi','Fuzz'],1,7,known),
  [['Plexi 2',18],['Plexi',null]]);
// Deleted (or all deleted): names no longer there are forgotten.
assert.deepEqual(favoriteChanges('Librarian_HelixFavoriteDeleted',[list[0],list[2]],null,1,7,known),[['Plexi',null]]);
assert.deepEqual(favoriteChanges('Librarian_HelixAllFavoritesDeleted',[],null,null,7,known),[['Plexi',null],['Fuzz',null]]);
assert.deepEqual(favoriteChanges('Librarian_HelixFavoriteMoved',list,null,0,7,known),[]);
// Removal: only NAM favorites on a cleared slot, and never a name used twice.
assert.deepEqual(favoritesPlaying(list,[18],known).map(f=>f.id),[2]);
assert.deepEqual(favoritesPlaying(list,[18,40,5],known).map(f=>f.id),[2,3]);
assert.deepEqual(favoritesPlaying(list,[5],known),[]);
assert.deepEqual(favoritesPlaying([...list,fav(9,'Plexi')],[18],known),[]);                  // two "Plexi"
assert.deepEqual(favoritesPlaying([fav(4,'Plexi','HD2_AmpUSDeluxeVib')],[18],known),[]);     // now a stock amp
assert.deepEqual(favoritesPlaying(list,[18],new Map()),[]);                                    // never noted
'''
        run = subprocess.run(['node'], input=code, capture_output=True, text=True, encoding='utf-8', timeout=15)
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        # Wired in: noted from HX Edit's notifications, removed after the Clear goes out, guarded by bytes.
        self.assertIn('if (favoriteEvent.test(event)) noteFavoriteEvent(event, notificationIndex(args[1]));', view)
        self.assertIn('function flushFavoriteNotes() {', view)          # taken from HX Edit's favorites reload
        tone = (ROOT / 'tone_view.js').read_text(encoding='utf-8')
        self.assertIn("namView.flushFavoriteNotes?.();", tone)
        self.assertIn("'3c90c0': '558bec6aff68e252'", tone)
        self.assertIn('this.context.ecx.readPointer().equals(address(0x8d948c))', view)
        self.assertIn('owner.add(0x17c).readPointer()', view)
        self.assertIn('removeFavoritesOnSlots(selected || []);', view)
        self.assertIn("read(0x3d9f30, 6) === '558bec6aff68'", view)
        self.assertIn('favoritesNote(selected)', view)



class FavoriteRowsTests(unittest.TestCase):
    """HX Edit's favorite rows of a NAM block: the capture's name, category and picture."""

    def test_the_default_name_is_the_capture(self):
        view = (ROOT / 'tone_view.js').read_text(encoding='utf-8')
        name = view[view.index('  function favoriteName(capture) {'):view.index('  function favoriteRow(')]
        code = "const assert=require('node:assert/strict');const tiles=[{block:'NAM A2 Lite'}];\n" + name + r'''
assert.equal(favoriteName({name:'Jcm800'}),'Jcm800');
assert.equal(favoriteName({name:'Friedman – BE-100 Deluxe Lead Channel'}),'BE-100 Deluxe Le');   // 16 characters
assert.equal(favoriteName({name:'Electro-Harmonix Big Muff Pi '}),'Electro-Harmonix');
assert.equal(favoriteName(null),'NAM A2 Lite');                      // no capture known: no tag spaces either
assert.equal(favoriteName({name:'  '}),'NAM A2 Lite');
'''
        run = subprocess.run(['node'], input=code, capture_output=True, text=True, encoding='utf-8', timeout=15)
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        self.assertIn('if (favoriteRow(this.context.ecx, args, this.returnAddress)) return;', view)
        self.assertIn("NEW_FAVORITE_ROW = 0x25df6a", view)
        nam_view = (ROOT / 'nam_view.js').read_text(encoding='utf-8')
        self.assertIn('favoriteSlot: name => favoriteSlots.has(name) ? favoriteSlots.get(name) : null,', nam_view)


if __name__ == '__main__':
    unittest.main()
