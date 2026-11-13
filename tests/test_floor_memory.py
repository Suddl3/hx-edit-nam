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


if __name__ == '__main__':
    unittest.main()
