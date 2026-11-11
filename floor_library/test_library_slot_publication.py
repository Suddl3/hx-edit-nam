"""Offline export-content -> observer gate -> NAM row tests. No device/UI calls."""
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

from library_catalog import add_wav, empty_catalog
from native_export_trial import NativeExportTrial
from test_library_catalog import wav

HERE=Path(__file__).resolve().parent


class SlotPublicationTests(unittest.TestCase):
    def test_scan_rows_stay_identical_until_batch_finishes(self):
        table=(HERE/'library_table_native.js').read_text()
        mapping=table[table.index('    const rows = library.map'):table.index('    return {visible:true,bounds,rows')]
        forget=table[table.index('  function forgetVerifiedSlot'):table.index('  function invalidateVerifiedSlots')]
        self.node((HERE/'library_slot_state.js').read_text()+r'''
const assert=require('node:assert/strict');
const slotLedger=createLibrarySlotLedger(),a='a'.repeat(64),b='b'.repeat(64);
const library=[{contentId:a,title:'A',canLoad:true},{contentId:b,title:'B',canLoad:true}];
const profiles={},verifiedMetadata={},audition=null,libraryBusy=false,libraryLoader=()=>{};
const irList=()=>0,hasProfile=()=>false;let discoveryStatus='verifying';
const slotRefresh=()=>{};
'''+forget+r'''
function rows(){'''+mapping+r'''return rows;}
slotLedger.accept(0,9,a);slotLedger.accept(0,127,b);slotLedger.invalidate();
const before=rows();assert.equal(before[0].canLoad,false);
// The real observer forgets each slot BEFORE exporting it. This must remove
// authorization without erasing the retained checking label mid-scan.
forgetVerifiedSlot(9);assert.deepEqual(rows(),before);
slotLedger.accept(slotLedger.epoch(),10,a);
assert.deepEqual(rows(),before);
forgetVerifiedSlot(127);assert.deepEqual(rows(),before);
slotLedger.accept(slotLedger.epoch(),11,b);
assert.deepEqual(rows(),before);
slotLedger.commitDisplay();discoveryStatus='complete';
assert.deepEqual(rows().map(r=>r.verifiedSlots),[[10],[11]]);
assert.equal(rows()[0].canLoad,true);
slotLedger.invalidate(false);discoveryStatus='disconnected';
assert.deepEqual(rows()[0].displaySlots,[]);
assert.deepEqual(rows()[0].verifiedSlots,[]);
''')

    def test_background_display_is_atomic_and_never_authorizes_stale_slots(self):
        self.node((HERE/'library_slot_state.js').read_text()+r'''
const assert=require('node:assert/strict');
const s=createLibrarySlotLedger(),a='a'.repeat(64),b='b'.repeat(64);
s.accept(0,9,a);s.accept(0,127,b);
s.invalidate();
assert.deepEqual(s.displaySlotsFor(a),[9]);
assert.deepEqual(s.slotsFor(a),[]); // Retained display is NOT selection evidence.
assert.equal(s.accept(0,9,a),false);
s.accept(s.epoch(),10,a);
assert.deepEqual(s.displaySlotsFor(a),[9]);
assert.deepEqual(s.displaySlotsFor(b),[127]); // No row-by-row replacement.
s.commitDisplay();
assert.deepEqual(s.displaySlotsFor(a),[10]);
assert.deepEqual(s.displaySlotsFor(b),[]); // Deleted capture clears at commit.
s.invalidate(false); // Disconnect clears history immediately.
assert.deepEqual(s.displaySlotsFor(a),[]);
s.accept(s.epoch(),11,a);s.commitDisplay();s.forget(11);
assert.deepEqual(s.displaySlotsFor(a),[]);
s.accept(s.epoch(),12,a);s.imported(12);
assert.deepEqual(s.displaySlotsFor(a),[]);
s.accept(s.epoch(),13,a);s.invalidate();s.forget(13);
assert.deepEqual(s.displaySlotsFor(a),[]); // Real deletion even during a scan.
s.accept(s.epoch(),14,a);s.commitDisplay();s.forget(14,true);
assert.deepEqual(s.slotsFor(a),[]);
assert.deepEqual(s.displaySlotsFor(a),[]); // Manual re-export outside a batch.
''')

    def node(self,code):
        result=subprocess.run(['node','-e',code],capture_output=True,text=True,timeout=15)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)

    def test_import_replacement_removes_old_slot_owner_but_preserves_duplicates_elsewhere(self):
        from library_table_patch import patch_table_view
        source=(HERE/'track_library_import_fixture.js').read_text()
        start=source.index('    trackLibraryImport(first, count, metadata) {')
        method=source[start:source.index('\n    },',start)+7]
        self.node((HERE/'library_slot_state.js').read_text()+r'''
const assert=require('node:assert/strict');
const a='a'.repeat(64),b='b'.repeat(64),slotLedger=createLibrarySlotLedger();
const verifiedMetadata={},mergeLibrary=()=>{},send=()=>{};
slotLedger.accept(0,11,a);slotLedger.accept(0,127,a);
const view={'''+method+r'''};
view.trackLibraryImport(127,1,{'127':{contentId:b}});
assert.deepEqual(slotLedger.slotsFor(a),[11]);
assert.equal(slotLedger.contentFor(127),null);
assert.equal(slotLedger.importCurrent(127),true);
assert.equal(verifiedMetadata[127].contentId,b);
// An ordinary IR replacement has no NAM metadata: remove the old owner too.
view.trackLibraryImport(11,1,{});
assert.deepEqual(slotLedger.slotsFor(a),[]);
assert.equal(slotLedger.importCurrent(11),false);
''')

    def test_ledger_is_session_only_and_clears_same_name_replacements(self):
        self.node((HERE/'library_slot_state.js').read_text()+r'''
const assert=require('node:assert/strict');
const s=createLibrarySlotLedger(),a='a'.repeat(64),b='b'.repeat(64);
assert.deepEqual(s.slotsFor(a),[]);
s.imported(11);assert.equal(s.importCurrent(11),true);
assert.equal(s.accept(s.epoch(),127,a),true);s.accept(s.epoch(),11,a);
assert.deepEqual(s.slotsFor(a),[11,127]);
s.slotsFor(a).push(3);assert.deepEqual(s.slotsFor(a),[11,127]);
s.accept(s.epoch(),127,b);assert.deepEqual(s.slotsFor(a),[11]);
for(const slot of [0,128,129,-1,1.5,'127',true])assert.equal(s.accept(s.epoch(),slot,a),false);
for(const id of [null,'a','A'.repeat(64),'../path'])assert.equal(s.accept(s.epoch(),1,id),false);
const epoch=s.epoch();s.invalidate();
assert.deepEqual(s.slotsFor(a),[]);assert.deepEqual(s.slotsFor(b),[]);
assert.equal(s.importCurrent(11),false);assert.equal(s.accept(epoch,127,a),false);
assert.deepEqual(createLibrarySlotLedger().slotsFor(a),[]);
''')

    def test_content_match_publishes_to_actual_row_mapping_and_invalidates(self):
        with tempfile.TemporaryDirectory() as root:
            catalog=empty_catalog();identity=add_wav(catalog,wav(),'Deluxe')
            trial=NativeExportTrial(catalog,root,127)
            event={**trial.config,'device':42,'revision':0,'sequence':1}
            self.assertIsNone(trial.receive({**event,'stage':'submitted'}))
            Path(event['path']).write_bytes(wav())
            evidence=trial.receive({**event,'stage':'completed','result':0,'details':[0]})
            self.assertEqual(evidence['contentId'],identity)
            self.assertIsNone(trial.receive({**event,'stage':'completed','result':0,'details':[0]}))
            source=(HERE/'native_export_observer.js').read_text()+'\n'+(HERE/'library_slot_state.js').read_text()
            table=(HERE/'library_table_native.js').read_text()
            mapping=table[table.index('    const rows = library.map'):table.index('    return {visible:true,bounds,rows')]
            setup='const discoveryStatus="inactive"; const cfg='+json.dumps(trial.config)+';\nconst evidence='+json.dumps(evidence)+';\n'
            self.node(source+'\n'+setup+r'''
const assert=require('node:assert/strict');
function fixture(requireHostMonitor=false){
 const handlers={},messages=[],receivers={};let setting,name='EdLib_TaskProgress',time=0;
 const slotLedger=createLibrarySlotLedger();let refreshes=0;
 const library=[{contentId:evidence.contentId,title:'Deluxe',canLoad:true}],profiles={},verifiedMetadata={};
 const audition=null,libraryBusy=false,libraryLoader=()=>{},irList=()=>0,hasProfile=()=>false;
 const namView={slotEpoch:()=>slotLedger.epoch(),invalidateVerifiedSlots(){slotLedger.invalidate();refreshes++;},
   forgetVerifiedSlot:slot=>slotLedger.forget(slot),
   recordVerifiedSlot(epoch,slot,id){const ok=slotLedger.accept(epoch,slot,id);if(ok)refreshes++;return ok;}};
 global.recv=(type,handler)=>receivers[type]=handler;
 global.send=message=>messages.push(message);
 const api={attach:(n,h)=>(handlers[n]=h,{detach(){}}),emit:send,
   now:()=>time,setTimer:()=>1,clearTimer:()=>{},identity:x=>x,device:()=>42,cancelled:()=>false,
   submission:a=>a[0],job:v=>v,result:v=>v,notification:()=>name,
   setting:()=>{if(setting==='throw')throw Error('bad fixture');return setting;}};
 const observer=connectNativeExportLibrary({...cfg,requireHostMonitor},namView,api);
 const batch={indices:[126],paths:[cfg.path]};
 const lock=value=>({name:'UILockout',value,onDevice:false,isUndo:false,resetAll:false});
 function notify(n,s){name=n;setting=s;handlers.inventory.onEnter([]);}
 function complete(late=false){
   handlers.submit.onEnter.call({context:{ecx:1}},[batch]);
   const frame={context:{ecx:1}};
   handlers.worker.onEnter.call(frame,[null,batch,{result:0,details:[0]}]);
   notify('Device_SettingChanged',lock(true));
   if(late){time=100;handlers.worker.onLeave.call(frame);time=120;}
   notify('Device_SettingChanged',lock(false));
   if(!late)handlers.worker.onLeave.call(frame);
   assert.equal(messages.at(-1).stage,'completed');
 }
 function rows(){'''+mapping+r'''return rows;}
 return {complete,notify,rows,namView,observer,messages,slotLedger,refreshes:()=>refreshes,
   reply:(payload=evidence)=>receivers.librarySlotEvidence({payload}),
   host:payload=>receivers.hostDeviceChange({payload})};
}
for(const late of [false,true]){
 const f=fixture();f.complete(late);assert.equal(f.rows()[0].deviceStatus,'unverified');
 f.reply();assert.equal(f.messages.at(-1).accepted,true);
 assert.equal(f.rows()[0].deviceStatus,'verified');assert.deepEqual(f.rows()[0].verifiedSlots,[127]);
 assert.equal(f.rows()[0].location,'Floor 127');assert.equal(f.refreshes(),1);
 f.reply();assert.equal(f.messages.at(-1).accepted,false); // duplicate proof
 f.notify('EdLib_TaskProgress');assert.deepEqual(f.rows()[0].verifiedSlots,[127]);
 f.notify('Librarian_HelixIRChanged');assert.equal(f.rows()[0].deviceStatus,'unverified');
 assert.deepEqual(f.rows()[0].verifiedSlots,[]);assert.equal(f.refreshes(),2);
}
for(const change of [f=>f.notify('Librarian_HelixIRChanged'),f=>f.notify('Librarian_HelixIRRenamed'),
 f=>f.notify('Librarian_HelixIRDeleted'),f=>f.notify('Device_Disconnected'),
 f=>f.notify('unknown'),f=>f.namView.invalidateVerifiedSlots(),
 f=>f.notify('Device_SettingChanged','throw'),f=>f.observer.close()]){
 let f=fixture();f.complete();change(f);f.reply();
 assert.equal(f.messages.at(-1).accepted,false);assert.deepEqual(f.rows()[0].verifiedSlots,[]);
 f=fixture();f.complete();f.reply();assert.deepEqual(f.rows()[0].verifiedSlots,[127]);
 change(f);assert.deepEqual(f.rows()[0].verifiedSlots,[]);
}
for(const changes of [{request:'old'},{device:99},{revision:1},{sequence:2},{slot:128},{contentId:'bad'}]){
 const f=fixture();f.complete();f.reply({...evidence,...changes});
 assert.equal(f.messages.at(-1).accepted,false);assert.deepEqual(f.rows()[0].verifiedSlots,[]);
}
const restarted=fixture();restarted.reply();assert.equal(restarted.messages.at(-1).accepted,false);
assert.equal(restarted.rows()[0].deviceStatus,'unverified');
// Reproduce Candidate23: no native inventory or teardown callback on reconnect.
// Host PnP alone must clear the row and block stale delayed publications.
let f=fixture(true);f.complete();f.reply();assert.equal(f.messages.at(-1).accepted,false);
f=fixture(true);f.host({generation:0,ready:true});f.complete();f.reply();
assert.deepEqual(f.rows()[0].verifiedSlots,[127]);
f.host({generation:1,ready:true});assert.deepEqual(f.rows()[0].verifiedSlots,[]);
f.host({generation:2,ready:true});assert.deepEqual(f.rows()[0].verifiedSlots,[]);
f.reply();assert.equal(f.messages.at(-1).accepted,false);
for(const message of [{generation:1,ready:true},{generation:0,ready:false},null]){
 f=fixture(true);f.host({generation:0,ready:true});f.complete();f.host(message);f.reply();
 assert.equal(f.messages.at(-1).accepted,false);assert.deepEqual(f.rows()[0].verifiedSlots,[]);
}
f=fixture(true);f.host({generation:3,ready:true});f.complete();
const epoch=f.slotLedger.epoch();f.host({generation:2,ready:true});
assert.equal(f.slotLedger.epoch(),epoch);f.reply();assert.equal(f.messages.at(-1).accepted,true);
''')

    def test_nonmatches_and_disconnect_never_return_publication(self):
        for case in ('unknown','ir','malformed','failed','disconnect'):
            with self.subTest(case=case), tempfile.TemporaryDirectory() as root:
                catalog=empty_catalog();add_wav(catalog,wav(),'Known')
                trial=NativeExportTrial(catalog,root,127)
                e={**trial.config,'device':42,'revision':0,'sequence':1}
                trial.receive({**e,'stage':'submitted'})
                raw=wav(gain=.2) if case=='unknown' else b'invalid' if case=='malformed' else wav()
                if case=='ir':
                    raw=bytearray(raw)
                    # Change the first sample signature, keeping the WAV valid.
                    offset=raw.index(b'data')+8;raw[offset:offset+4]=b'\0'*4;raw=bytes(raw)
                Path(e['path']).write_bytes(raw)
                if case=='disconnect':trial.close()
                self.assertIsNone(trial.receive({**e,'stage':'completed',
                    'result':-1 if case=='failed' else 0,'details':[0]}))



if __name__=='__main__':unittest.main()
