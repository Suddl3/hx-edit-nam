"""Replay the native observer and scheduler with synthetic memory, without HX Edit."""
from pathlib import Path
import hashlib
import shutil
import struct
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(shutil.which('node'), 'Node is required for native JavaScript replay')
class NativeInventoryTests(unittest.TestCase):
    def node(self, files, code):
        source = '\n'.join((ROOT / file).read_text(encoding='utf-8') for file in files)
        run = subprocess.run(['node'], input=source + '\n' + code,
                             capture_output=True, text=True, encoding='utf-8', timeout=15)
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)

    def test_block_selection_preserves_completed_scan_and_only_changed_slots_reexport(self):
        self.node(['library_slot_state.js', 'automatic_slot_discovery.js', 'native_export_observer.js'], r'''
const assert=require('node:assert/strict'),handlers={},ledger=createLibrarySlotLedger();
const plans=[],exports=[];let now=0,name='EditBuffer_HelixCursorChange',slot=9;
let automatic;
const observer=installNativeExportObserver({request:'selection-replay',repeat:true,trackSlots:true},{
 attach:(n,h)=>(handlers[n]=h,{detach(){}}),emit:()=>{},now:()=>now,
 notification:()=>name,changedSlot:()=>slot,
 invalidate:(_display,slots)=>{if(slots)ledger.invalidateSlots(slots);else ledger.invalidate();automatic.invalidate(slots);}
});
automatic=createAutomaticSlotDiscovery({now:()=>now,status:()=>{},prepare:p=>plans.push(p),
 export:p=>{exports.push(p.slot);return true;}});
const snap={device:0x210001,identity:'floor-fixture',signature:'9,127',
 slots:[9,127],slotSignatures:[[9,'A'],[127,'B']],revision:0};
function tick(dt=0){now+=dt;automatic.tick({...snap,revision:observer.revision()});}
function finish(){const p=plans.at(-1);automatic.prepared({token:p.token,path:'E:\\fresh.wav'});tick();
 ledger.accept(ledger.epoch(),p.slot,(p.slot===9?'a':'b').repeat(64));
 automatic.result({token:p.token,status:'matched'});tick();}
automatic.host({generation:0,ready:true,present:true});automatic.requestVerification();tick();tick(1501);
finish();finish();ledger.commitDisplay();assert.deepEqual(exports,[9,127]);
const original=ledger.entries();
for(let i=0;i<5;i++){handlers.inventory.onEnter([]);tick(2000);}
assert.deepEqual(ledger.entries(),original);assert.deepEqual(automatic.inspect().done,[9,127]);
assert.equal(observer.revision(),0);assert.deepEqual(exports,[9,127]);
// Enter NAM again: completed slots do not export again.
automatic.requestVerification();tick(1501);assert.deepEqual(exports,[9,127]);
// A real edit to 9 invalidates only 9; cursor events cannot hide it.
name='Librarian_HelixIRChanged';handlers.inventory.onEnter([]);
assert.deepEqual(ledger.entries(),[[127,'b'.repeat(64)]]);
name='EditBuffer_HelixCursorChange';handlers.inventory.onEnter([]);tick(1501);finish();
assert.deepEqual(exports,[9,127,9]);
// Preview storage does not recheck permanent captures, even during docking.
automatic.setSessionActive(true);name='Librarian_HelixIRChanged';slot=128;
handlers.inventory.onEnter([]);automatic.setSessionActive(false);tick(2000);
assert.deepEqual(exports,[9,127,9]);
// Editing or changing the preset does not change the device's IR library.
for(const preset of ['EditBuffer_HelixBlockAdded','EditBuffer_HelixBlockRemoved',
    'EditBuffer_PresetContentsWillChange','EditBuffer_PresetContentsDidChange']){
  const before=ledger.entries();name=preset;handlers.inventory.onEnter([]);tick(2000);
  assert.deepEqual(ledger.entries(),before);
  assert.deepEqual([...automatic.inspect().done].sort((a,b)=>a-b),[9,127]);
}
assert.deepEqual(exports,[9,127,9]);
// No prefix-based exemption: unknown edit-buffer mutations still lose proof.
name='EditBuffer_HelixCursorChangeUnknown';handlers.inventory.onEnter([]);
assert.deepEqual(ledger.entries(),[]);assert.deepEqual(automatic.inspect().done,[]);
tick(5000);assert.deepEqual(exports,[9,127,9]);
''')

    def test_atomic_display_never_authorizes_stale_capture(self):
        self.node(['library_slot_state.js'], r'''
const assert=require('node:assert/strict'), s=createLibrarySlotLedger(), a='a'.repeat(64),b='b'.repeat(64);
s.accept(0,9,a);s.accept(0,127,b);s.invalidate();
const before=s.displayEntries();assert.deepEqual(s.entries(),[]);
s.forget(9,true);s.accept(s.epoch(),10,a);assert.deepEqual(s.displayEntries(),before);
s.forget(127,true);s.accept(s.epoch(),11,b);assert.deepEqual(s.displayEntries(),before);
assert.equal(s.accept(0,9,a),false);s.commitDisplay();assert.deepEqual(s.displayEntries(),[[10,a],[11,b]]);
s.invalidate(false);assert.deepEqual(s.displayEntries(),[]);
for(const slot of [0,128,129,1.5,true,'7'])assert.equal(s.accept(s.epoch(),slot,a),false);
s.accept(s.epoch(),7,a);s.commitDisplay();s.imported(7);assert.equal(s.contentFor(7),null);
assert.deepEqual(createLibrarySlotLedger().entries(),[]);
''')

    def test_friendly_caption_preserves_slot_identity_and_capacity(self):
        source = (ROOT / 'nam_view.js').read_text(encoding='utf-8')
        helper = source[source.index('function friendlyNamCaption('):source.index('function installNamView(')]
        self.node([], helper + r'''
const assert=require('node:assert/strict');
const raw='NAM AC Deluxe Reverb 4999accc';
assert.equal(friendlyNamCaption(127,'127: '+raw,raw,'Deluxe Reverb'),'127: Deluxe Reverb');
assert.equal(friendlyNamCaption(127,raw,raw,'Deluxe Reverb'),'127: Deluxe Reverb');
assert.equal(friendlyNamCaption(127,'',raw,'Deluxe Reverb'),'127: Deluxe Reverb');
assert.equal(friendlyNamCaption(9,'127: '+raw,raw,'Other'),'127: '+raw);
assert.equal(friendlyNamCaption(127,'127: Stale',raw,'Other'),'127: Stale');
assert.equal(friendlyNamCaption(127,raw,raw,'\0'),raw);
const wide=friendlyNamCaption(127,raw,raw,'界'.repeat(100));
assert.match(wide,/^127: 界+…$/);assert.ok(wide.length<30);
// Candidate60 owner screenshot: a long library name pushed the slot number out of the control.
const long='Friedman Brit 50 Teaser Tone from Capture Pack (32 DI Captures + IR\'s) – BRIT50 HG BRI CAB';
assert.equal(friendlyNamCaption(22,raw,raw,long),'22: BRIT50 HG BRI CAB');
assert.match(friendlyNamCaption(22,raw,raw,'A very long single capture name with no profile part'),/^22: A very long sing.*…$/);
assert.ok(friendlyNamCaption(124,raw,raw,long+' and a much longer profile name').length<=26);
// Candidate67 owner screenshot: capitals are wide. This caption fitted by character count and
// still lost its first characters in the control; "16: EVH 5150III® 100S ste…" had fitted.
assert.equal(friendlyNamCaption(9,raw,raw,'FNDR BFDRI VB Clean BASS'),'9: FNDR BFDRI VB Cle…');
assert.equal(friendlyNamCaption(16,raw,raw,'EVH 5150III® 100S stealth'),'16: EVH 5150III® 100S s…');
assert.equal(friendlyNamCaption(127,raw,raw,'Deluxe Reverb'),'127: Deluxe Reverb');
assert.equal(friendlyNamCaption(128,raw,raw,undefined),raw);
''')

    def test_use_on_floor_requires_current_content_identity(self):
        source = (ROOT / 'nam_view.js').read_text(encoding='utf-8')
        begin = source.index('    verifiedCaptureSlot(identity, requestedSlot) {')
        getter = source[begin:source.index('\n    },', begin) + 7]
        self.node(['library_slot_state.js'], r'''
const assert=require('node:assert/strict'), slotLedger=createLibrarySlotLedger();
const identity='a'.repeat(64),content='b'.repeat(64),payloadMetadata={[content]:{id:identity}},AUDITION_SLOT=128;
let present=true,occupied=true;const irList=()=>({isNull:()=>!present});
const getIrRow=()=>({isNull:()=>false,add:()=>({readU32:()=>Number(occupied)})});
const view={''' + getter + r'''};
slotLedger.accept(0,127,content);slotLedger.accept(0,9,content);
assert.equal(view.verifiedCaptureSlot(identity),9);assert.equal(view.verifiedCaptureSlot(identity,127),127);
assert.equal(view.verifiedCaptureSlot(identity,128),null);assert.equal(view.verifiedCaptureSlot(content),null);
occupied=false;assert.equal(view.verifiedCaptureSlot(identity),null);occupied=true;
slotLedger.invalidate();assert.deepEqual(slotLedger.displaySlotsFor(content),[9,127]);
assert.equal(view.verifiedCaptureSlot(identity),null); // previous display cannot authorize Use
slotLedger.accept(slotLedger.epoch(),10,content);assert.equal(view.verifiedCaptureSlot(identity),10);
present=false;assert.equal(view.verifiedCaptureSlot(identity),null);
''')

    def test_preview_clear_checks_other_blocks_and_every_snapshot(self):
        source = (ROOT / 'nam_view.js').read_text(encoding='utf-8')
        helpers = source[:source.index('function installNamView(')]
        self.node([], helpers + r'''
const assert=require('node:assert/strict'), models={NAM:['Index'],IR:['Index'],Stock:[]};
const tree={global:{'@topology0':'A'},dsp0:{old:{'@model':'NAM',Index:128}},dsp1:{},
 globalNodes:{snapshot0:{'@name':'A',blocks:{dsp0:{old:true}}},global:{}}};
assert.equal(previewSlotUnused(presetIrSlots(tree,models),128),false);
tree.dsp0.old.Index=9;assert.equal(previewSlotUnused(presetIrSlots(tree,models),128),true);
tree.dsp0.other={'@model':'NAM',Index:128};assert.equal(previewSlotUnused(presetIrSlots(tree,models),128),false);
tree.dsp0.other.Index=11;tree.globalNodes.snapshot1={'@name':'B',controllers:{dsp0:{other:{Index:{'@value':128}}}}};
assert.equal(previewSlotUnused(presetIrSlots(tree,models),128),false);
tree.globalNodes.snapshot1.controllers.dsp0.other.Index['@value']=11;
assert.equal(previewSlotUnused(presetIrSlots(tree,models),128),true);
tree.dsp0.other['@model']='Unknown';assert.equal(previewSlotUnused(presetIrSlots(tree,models),128),false);
assert.equal(previewSlotUnused({reason:'preset-changing'},128),false);
''')

    def test_serial_scheduler_reconnect_busy_and_timeout(self):
        self.node(['automatic_slot_discovery.js'], r'''
const assert=require('node:assert/strict');let now=0,plans=[],calls=[],states=[];
const s=createAutomaticSlotDiscovery({now:()=>now,prepare:p=>plans.push(p),export:p=>{calls.push(p);return true;},status:v=>states.push(v.state)});
const snap={device:0x210001,identity:'fixture',signature:'9,127',slots:[9,127],revision:3};
function tick(dt=0,v=snap){now+=dt;s.tick(v);}
function start(){const p=plans.at(-1);s.prepared({token:p.token,path:'E:\\private\\fresh.wav'});tick();return p;}
tick(9999);assert.equal(plans.length,0);s.host({generation:0,ready:true,present:true});s.requestVerification();tick();tick(1501);
let p=start();tick(100);assert.equal(calls.length,1);s.result({token:'stale',status:'matched'});
assert.deepEqual(s.inspect().done,[]);s.result({token:p.token,status:'matched'});tick();
assert.equal(plans.at(-1).slot,127);p=start();s.result({token:p.token,status:'ir'});tick();assert.equal(states.at(-1),'complete');
s.host({generation:1,ready:true,present:false});tick(2000);assert.equal(calls.length,2);
s.host({generation:2,ready:true,present:true});s.requestVerification();tick();tick(1501);tick(10,{...snap,busy:true});assert.equal(calls.length,2);
p=start();s.invalidate();s.result({token:p.token,status:'matched'});assert.equal(s.inspect().blocked,true);
for(let i=0;i<20;i++)tick(2000);assert.equal(calls.length,3);
s.host({generation:3,ready:true,present:false});s.host({generation:4,ready:true,present:true});s.requestVerification();tick();tick(1501);start();tick(18001);
assert.equal(states.at(-1),'discovery-timeout');s.close();tick(3000);assert.equal(calls.length,4);
''')

    def test_workflow_cancels_only_unsubmitted_preparation(self):
        self.node(['automatic_slot_discovery.js'], r'''
const assert=require('node:assert/strict');let now=0,plans=[],states=[],exports=[];
const s=createAutomaticSlotDiscovery({now:()=>now,prepare:p=>plans.push(p),export:p=>{exports.push(p);return true;},status:v=>states.push(v.state)});
const snap={device:0x210001,identity:'x',signature:'9',slots:[9],revision:1};
function tick(dt=0,v=snap){now+=dt;s.tick(v);}
s.host({generation:0,ready:true,present:true});s.requestVerification();tick();tick(1501);
const old=plans.at(-1);assert.equal(s.pauseForWorkflow(),true);assert.equal(states.at(-1),'preparation-cancelled');
s.prepared({token:old.token,path:'E:\\stale.wav'});tick(6000,{...snap,busy:true});
assert.equal(s.inspect().blocked,false);assert.equal(exports.length,0);
tick();tick(1501);const current=plans.at(-1);assert.notEqual(current.token,old.token);
s.prepared({token:current.token,path:'E:\\fresh.wav'});tick();assert.equal(exports.length,1);
assert.equal(s.pauseForWorkflow(),false);assert.equal(s.inspect().pending.token,current.token);
s.result({token:current.token,status:'matched'});tick();assert.equal(states.at(-1),'complete');
// A known permanent-slot mutation only rechecks that slot. An unknown mutation
// cancels authorization and requires another request instead of auto-rescanning.
s.invalidate([9]);tick(1501);const second=plans.at(-1);s.invalidate();
assert.equal(s.inspect().blocked,false);assert.equal(states.at(-1),'verification-needed');
s.prepared({token:second.token,path:'E:\\late.wav'});tick();tick(1501);
assert.equal(exports.length,1);assert.equal(plans.at(-1).token,second.token);
s.requestVerification();tick();tick(1501);assert.notEqual(plans.at(-1).token,second.token);
''')

    def test_lazy_verification_preserves_completed_slots_across_workflows(self):
        self.node(['automatic_slot_discovery.js'], r'''
const assert=require('node:assert/strict');let now=0;const plans=[],exports=[];
const s=createAutomaticSlotDiscovery({now:()=>now,prepare:p=>plans.push(p),export:p=>{exports.push(p.slot);return true;},status:()=>{}});
let snap={device:0x210001,identity:'x',signature:'initial',slotSignatures:[[9,'A'],[127,'B']],slots:[9,127],revision:1};
function tick(dt=0){now+=dt;s.tick(snap);}
function finish(){const p=plans.at(-1);s.prepared({token:p.token,path:'E:\\fresh.wav'});tick();s.result({token:p.token,status:'matched'});tick();}
s.host({generation:0,ready:true,present:true});for(let i=0;i<1000;i++)tick(250);
assert.equal(plans.length,0,'startup never exports without a verification request');
s.requestVerification();tick();tick(1501);finish();finish();assert.deepEqual(exports,[9,127]);
for(let i=0;i<7;i++){s.pauseForWorkflow();tick(2000);}
assert.deepEqual(exports,[9,127]);assert.deepEqual(s.inspect().done,[9,127]);
s.setSessionActive(true);s.invalidate([128]);tick(5000);
assert.deepEqual(s.inspect().done,[9,127],'preview128 preserves permanent-slot proof');
s.invalidate([9]);for(let i=0;i<100;i++)tick(250);
assert.equal(plans.length,2,'changed-slot verification waits while the browser is active');
s.setSessionActive(false);tick(1501);finish();assert.deepEqual(exports,[9,127,9]);
// A renamed/newly changed permanent row invalidates only its own cached result.
snap={...snap,signature:'changed',slotSignatures:[[9,'A'],[127,'C']]};
s.requestVerification();tick();tick(1501);finish();assert.deepEqual(exports,[9,127,9,127]);
s.invalidate();for(let i=0;i<100;i++)tick(250);
assert.equal(plans.length,4,'unknown mutation cannot silently launch a full scan');
assert.deepEqual(s.inspect().done,[]);s.requestVerification();tick();tick(1501);finish();finish();
assert.deepEqual(exports,[9,127,9,127,9,127]);
''')

    def test_browser_pause_preserves_inflight_receipt_and_explicit_request_is_bounded(self):
        self.node(['automatic_slot_discovery.js'], r'''
const assert=require('node:assert/strict');let now=0;const plans=[],exports=[];
const s=createAutomaticSlotDiscovery({now:()=>now,prepare:p=>plans.push(p),export:p=>{exports.push(p.slot);return true;},status:()=>{}});
const snap={device:0x210001,identity:'x',signature:'9,10',slots:[9,10],revision:1};
function tick(dt=0){now+=dt;s.tick(snap);}
s.host({generation:0,ready:true,present:true});s.setSessionActive(true);s.requestVerification();tick();tick(2000);
assert.equal(plans.length,0);
s.requestVerification({allowWhileBrowsing:true});tick();tick(1501);
const first=plans.at(-1);s.prepared({token:first.token,path:'E:\\fresh.wav'});tick();assert.deepEqual(exports,[9]);
s.pauseForWorkflow();assert.equal(s.inspect().pending.token,first.token);
s.result({token:first.token,status:'matched'});tick(2000);
assert.deepEqual(s.inspect().done,[9]);assert.equal(plans.length,1);
s.setSessionActive(false);tick();const second=plans.at(-1);assert.equal(second.slot,10);
s.prepared({token:second.token,path:'E:\\next.wav'});tick();s.result({token:second.token,status:'ir'});tick();
s.setSessionActive(true);for(let i=0;i<1000;i++)tick(250);
assert.deepEqual(exports,[9,10]);assert.equal(s.inspect().requested,false);
''')

    def test_resume_only_restores_an_existing_unblocked_verification_request(self):
        self.node(['automatic_slot_discovery.js'], r'''
const assert=require('node:assert/strict');let now=0;const plans=[];
const s=createAutomaticSlotDiscovery({now:()=>now,prepare:p=>plans.push(p),export:()=>true,status:()=>{}});
const snap={device:0x210001,identity:'x',signature:'9',slots:[9],revision:1};
const resume=()=>s.resumeRequestedVerification({allowWhileBrowsing:true});
function tick(dt=0){now+=dt;s.tick(snap);}
s.host({generation:0,ready:true,present:true});s.setSessionActive(true);
assert.equal(resume(),false);assert.equal(s.needsSnapshot(),false);
s.requestVerification({allowWhileBrowsing:true});tick();s.pauseForWorkflow();
assert.equal(s.needsSnapshot(),false);assert(resume());tick(1501);
let p=plans.at(-1);s.prepared({token:p.token,path:'E:\\fresh.wav'});tick();s.result({token:p.token,status:'matched'});tick();
assert.equal(s.inspect().requested,false);assert.equal(resume(),false);
s.invalidate([9]);assert.equal(s.needsSnapshot(),false);assert(resume());tick(1501);
s.invalidate();assert.equal(resume(),false);assert.equal(s.inspect().requested,false);
s.requestVerification({allowWhileBrowsing:true});tick();tick(1501);tick(5001);
assert.equal(s.inspect().blocked,true);assert.equal(resume(),false);assert.equal(s.needsSnapshot(),false);
s.requestVerification({allowWhileBrowsing:true});s.fault('native-observer-unavailable');
assert.equal(resume(),false);assert.equal(s.inspect().blocked,true);
s.host({generation:1,ready:true,present:false});assert.equal(resume(),false);
s.host({generation:2,ready:true,present:true});assert.equal(resume(),false);
s.close();assert.equal(resume(),false);
''')

    def test_paused_or_invalidated_preparation_releases_the_host_export_arm(self):
        self.node(['automatic_slot_discovery.js'], r'''
const assert=require('node:assert/strict');let now=0,armed=false;const plans=[];
const s=createAutomaticSlotDiscovery({now:()=>now,
 prepare:p=>{assert.equal(armed,false,'old host preparation must be cancelled first');armed=true;plans.push(p);},
 export:()=>true,status:v=>{if(v.state==='preparation-cancelled')armed=false;}});
const snap={device:0x210001,identity:'x',signature:'9',slots:[9],revision:1};
function tick(dt=0){now+=dt;s.tick(snap);}
function prepare(){s.requestVerification({allowWhileBrowsing:true});tick();tick(1501);assert(armed);}
s.host({generation:0,ready:true,present:true});prepare();s.setSessionActive(true);assert.equal(armed,false);
prepare();s.invalidate([9]);assert.equal(armed,false);
prepare();s.invalidate();assert.equal(armed,false);
prepare();const pending=plans.at(-1);s.prepared({token:pending.token,path:'E:\\fresh.wav'});tick();
s.setSessionActive(true);assert(armed,'an actual submitted export must finish, not lose its receipt');
''')

    def test_partial_invalidation_retains_unaffected_proof_but_rejects_old_receipts(self):
        self.node(['library_slot_state.js'], r'''
const assert=require('node:assert/strict'),s=createLibrarySlotLedger(),a='a'.repeat(64),b='b'.repeat(64);
s.accept(0,9,a);s.accept(0,127,b);s.imported(10);
s.invalidateSlots([128]);assert.equal(s.epoch(),0);assert.deepEqual(s.entries(),[[9,a],[127,b]]);
s.invalidateSlots([9]);assert.equal(s.contentFor(9),null);assert.equal(s.contentFor(127),b);
assert.deepEqual(s.displayEntries(),[[127,b]]);assert.equal(s.importCurrent(10),true);
assert.equal(s.accept(0,9,a),false);assert(s.accept(s.epoch(),9,a));
s.invalidate();assert.deepEqual(s.entries(),[]);assert.deepEqual(s.displayEntries(),[[9,a],[127,b]]);
''')

    def test_idle_and_browsing_adapter_does_not_poll_native_rows_or_queue_wakes(self):
        self.node(['automatic_slot_discovery.js'], r'''
const assert=require('node:assert/strict');let now=0,timer,hook,posts=0,snapshots=0;
Date.now=()=>now;
const window={value:7,isNull:()=>false,equals:other=>other.value===7};
global.ptr=value=>({value,isNull:()=>value===0,equals:other=>value===other.value});
global.Process={getModuleByName:()=>({getExportByName:name=>name})};
global.Memory={allocUtf16String:value=>value};global.recv=()=>{};global.send=()=>{};
global.NativeFunction=function(name){
 if(name==='PostMessageW')return()=>{posts++;return 1;};
 if(name==='RegisterWindowMessageW')return()=>123;
 if(name==='GetActiveWindow')return()=>window;
 assert.fail(name);
};
global.Interceptor={attach:(_address,value)=>(hook=value,{detach(){}})};
global.setInterval=callback=>(timer=callback,1);global.clearInterval=()=>{};
const view={libraryReady:()=>true,setDiscoveryStatus:()=>{},
 discoverySnapshot:()=>{snapshots++;return {device:0x210001,identity:'x',signature:'[]',slots:[],revision:0};},
 exportDiscoverySlot:()=>assert.fail('empty/idle library cannot export')};
const api=installAutomaticSlotDiscovery(view,{available:()=>true,revision:()=>0});
function dispatch(event){const state={};hook.onEnter.call(state,[{readPointer:()=>window,add:()=>({readU32:()=>event})}]);hook.onLeave.call(state);}
api.host({generation:0,ready:true,present:true});dispatch(15);
for(let i=0;i<1000;i++){now+=250;timer();dispatch(123);}
assert.equal(posts,0);assert.equal(snapshots,0);
api.requestVerification();timer();dispatch(123);assert.equal(posts,1);assert.equal(snapshots,1);
api.setSessionActive(true);for(let i=0;i<1000;i++){now+=250;timer();dispatch(123);}
assert.equal(posts,1);assert.equal(snapshots,1);
api.setSessionActive(false);now+=1501;timer();dispatch(123);
assert.equal(snapshots,2);assert.equal(api.inspect().requested,false);
for(let i=0;i<1000;i++){now+=250;timer();}assert.equal(posts,2);api.close();
''')

    def test_local_ui_lock_is_not_content_mutation_and_unknown_events_stay_untrusted(self):
        self.node(['native_export_observer.js'], r'''
const assert=require('node:assert/strict'),handlers={},invalidations=[],locks=[];
let name='Device_SettingChanged',setting={name:'UILockout',value:true,onDevice:false,isUndo:false,resetAll:false};
const api={attach:(n,h)=>(handlers[n]=h,{detach(){}}),emit:()=>{},now:()=>0,
 invalidate:(display,slots)=>invalidations.push({display,slots}),busy:value=>locks.push(value),
 notification:()=>name,setting:()=>setting};
const observer=installNativeExportObserver({request:'x',repeat:true,trackSlots:true},api);
handlers.inventory.onEnter([]);setting={...setting,value:false};handlers.inventory.onEnter([]);
assert.deepEqual(locks,[true,false]);assert.equal(observer.revision(),0);assert.deepEqual(invalidations,[]);
assert.equal(observer.slotsChanged([128]),false);assert.equal(observer.revision(),0);
assert(observer.slotsChanged([9]));assert.deepEqual(invalidations,[{display:true,slots:[9]}]);
name='Unknown_IR_Mutation';handlers.inventory.onEnter([]);assert.equal(invalidations.at(-1).slots,null);
name='Device_SettingChanged';setting={...setting,onDevice:true};handlers.inventory.onEnter([]);
assert.equal(invalidations.length,3,'device-side/unknown changes still invalidate globally');
''')

    def test_exact_slot_notifications_preserve_preview_and_unaffected_permanent_proof(self):
        self.node(['native_export_observer.js', 'library_slot_state.js'], r'''
const assert=require('node:assert/strict');
for(const event of ['Librarian_HelixIRChanged','Librarian_HelixIRDeleted','Librarian_HelixIRRenamed']){
 const handlers={},ledger=createLibrarySlotLedger(),invalidations=[];
 const a='a'.repeat(64),b='b'.repeat(64);ledger.accept(0,9,a);ledger.accept(0,127,b);
 let name=event,slot=128;
 const api={attach:(n,h)=>(handlers[n]=h,{detach(){}}),emit:()=>{},now:()=>0,
  notification:()=>name,changedSlot:()=>slot,invalidate:(display,slots)=>{
   invalidations.push(slots);if(slots)ledger.invalidateSlots(slots);else ledger.invalidate(display);
  }};
 const observer=installNativeExportObserver({request:'x',repeat:true,trackSlots:true},api);
 for(let i=0;i<1000;i++)handlers.inventory.onEnter([]);
 assert.equal(observer.revision(),0);assert.equal(ledger.epoch(),0);
 assert.deepEqual(ledger.entries(),[[9,a],[127,b]]);
 slot=9;handlers.inventory.onEnter([]);assert.deepEqual(invalidations,[[9]]);
 assert.equal(ledger.contentFor(9),null);assert.equal(ledger.contentFor(127),b);
 name=event+'Unknown';handlers.inventory.onEnter([]);
 assert.equal(invalidations.at(-1),null);assert.deepEqual(ledger.entries(),[]);
}
for(const slot of [0,129,-1,1.5,'128',null]){
 const handlers={},invalidations=[];
 const api={attach:(n,h)=>(handlers[n]=h,{detach(){}}),emit:()=>{},now:()=>0,
  clearTimer:()=>{},notification:()=> 'Librarian_HelixIRChanged',changedSlot:()=>slot,
  invalidate:(display,slots)=>invalidations.push(slots)};
 const observer=installNativeExportObserver({request:'x',repeat:true,trackSlots:true},api);
 handlers.inventory.onEnter([]);assert.equal(observer.available(),false);
 assert.deepEqual(invalidations,[null],'malformed exact notifications fail closed');
}
''')

    def test_native_notification_slot_decoder_is_passive_strict_and_bounded(self):
        self.node(['native_export_observer.js'], r'''
const assert=require('node:assert/strict');let next=0x1000;const mem=new Map();
class P{
 constructor(n){this.n=n;}add(n){return new P(this.n+n);}isNull(){return this.n===0;}toString(){return String(this.n);}
 readU32(){return mem.get(this.n)||0;}readS32(){return this.readU32()|0;}readU8(){return this.readU32()&255;}
 readPointer(){return new P(this.readU32());}readUtf8String(length){return (mem.get(this.n)||'').slice(0,length);}
 writeU32(n){mem.set(this.n,n);return this;}writePointer(p){return this.writeU32(p.n);}
}
function alloc(n=64){const p=new P(next);next+=n+32;return p;}
function text(p,value){const object=alloc(),rep=alloc(),data=alloc();mem.set(data.n,value);
 p.writeU32(10);p.add(8).writePointer(object);object.add(4).writePointer(rep);
 rep.add(4).writeU32(value.length);rep.add(16).writePointer(data);}
function dictionary(index,tag=4,key='index'){
 const value=alloc(),rep=alloc(),node=alloc();value.writeU32(13);value.add(8).writePointer(rep);
 rep.add(4).writePointer(node);text(node.add(8),key);node.add(24).writeU32(tag);node.add(32).writeU32(index);
 return {value,node};
}
global.Process={arch:'ia32',mainModule:{base:new P(0x400000)}};
global.NativeFunction=function(){assert.fail('passive notification decode must never call native code');};
const api=createNativeExportObserverApi();
for(const tag of [4,5])for(const index of [0,8,126,127]){
 const {value}=dictionary(index,tag),before=[...mem];
 assert.equal(api.changedSlot([null,value]),index+1);assert.deepEqual([...mem],before);
}
for(const [index,tag,key] of [[-1,4,'index'],[128,5,'index'],[0,11,'index'],[0,10,'index'],[0,4,'indices']]){
 const {value}=dictionary(index,tag,key),before=[...mem];
 assert.throws(()=>api.changedSlot([null,value]),NativeExportDecodeError);assert.deepEqual([...mem],before);
}
const cycle=dictionary(127);cycle.node.add(40).writePointer(cycle.node);
assert.throws(()=>api.changedSlot([null,cycle.value]),e=>e.code==='dictionary-cycle');
const first=dictionary(127),second=dictionary(8);first.node.add(40).writePointer(second.node);
assert.throws(()=>api.changedSlot([null,first.value]),e=>e.code==='duplicate-dictionary-key');
const many=dictionary(127);let tail=many.node;
for(let i=0;i<128;i++){const entry=dictionary(1,4,'other'+i);tail.add(40).writePointer(entry.node);tail=entry.node;}
assert.throws(()=>api.changedSlot([null,many.value]),e=>e.code==='dictionary-too-large');
''')

    def test_export_lock_pair_rejects_premature_reversed_and_duplicate_events(self):
        self.node(['native_export_observer.js'], r'''
const assert=require('node:assert/strict');
for(const scenario of ['premature','reversed','duplicate']){
 const handlers={},events=[];let epoch=0,setting;
 const batch={indices:[8],paths:['E:\\private\\9.wav']};
 const api={attach:(n,h)=>(handlers[n]=h,{detach(){}}),emit:e=>events.push(e),now:()=>0,
  identity:p=>p,device:()=>0x210001,submission:a=>a[0],job:v=>v,result:v=>v,cancelled:()=>false,
  slotEpoch:()=>epoch,invalidate:()=>epoch++,forget:()=>{},publish:()=>assert.fail('invalid lock cannot publish'),
  setTimer:()=>1,clearTimer:()=>{},notification:()=> 'Device_SettingChanged',setting:()=>setting};
 const observer=installNativeExportObserver({request:'x',automatic:true,repeat:true,trackSlots:true},api);
 observer.automaticSubmit({slot:9,path:batch.paths[0],device:0x210001,revision:0},'m',[batch],()=>{});
 function lock(value){setting={name:'UILockout',value,onDevice:false,isUndo:false,resetAll:false};handlers.inventory.onEnter([]);}
 if(scenario==='premature')lock(true);
 const frame={context:{ecx:'m'}};handlers.worker.onEnter.call(frame,[null,batch,{result:0,details:[0]}]);
 if(scenario==='reversed')lock(false);
 lock(true);if(scenario==='duplicate')lock(true);lock(false);handlers.worker.onLeave.call(frame);
 assert(epoch>0);assert.equal(events.some(e=>e.stage==='completed'),false);
 assert.equal(observer.publish({contentId:'a'.repeat(64)}),false);
}
''')

    def test_observer_only_publishes_after_unlock_and_current_epoch(self):
        self.node(['native_export_observer.js'], r'''
const assert=require('node:assert/strict');
for(const late of [false,true]){
 const handlers={},events=[],stored=[];let epoch=0,now=0,name='EdLib_TaskProgress',setting;
 const batch={indices:[126],paths:['E:\\private\\127.wav']};
 const api={attach:(n,h)=>(handlers[n]=h,{detach(){}}),emit:e=>events.push(e),now:()=>now,
 identity:p=>p,device:()=>0x210001,submission:a=>a[0],job:v=>v,result:v=>v,cancelled:()=>false,
 slotEpoch:()=>epoch,invalidate:()=>epoch++,forget:()=>{},publish:e=>{stored.push(e);return true;},
 setTimer:()=>1,clearTimer:()=>{},notification:()=>name,setting:()=>setting};
 const observer=installNativeExportObserver({request:'fixture',automatic:true,repeat:true,trackSlots:true},api);
 const request={slot:127,path:batch.paths[0],device:0x210001,revision:0};
 assert(observer.automaticSubmit(request,'m',[batch],()=>{}));
 assert.equal(observer.automaticSubmit(request,'m',[batch],()=>{}),false);
 const frame={context:{ecx:'m'}};handlers.worker.onEnter.call(frame,[null,batch,{result:0,details:[0]}]);
 function lock(value){name='Device_SettingChanged';setting={name:'UILockout',value,onDevice:false,isUndo:false,resetAll:false};handlers.inventory.onEnter([]);}
 lock(true);if(late){now=100;handlers.worker.onLeave.call(frame);assert.equal(events.at(-1).stage,'awaiting-unlock');}
 lock(false);if(!late)handlers.worker.onLeave.call(frame);
 const completed=events.at(-1);assert.equal(completed.stage,'completed');
 const evidence={...completed,contentId:'a'.repeat(64)};assert(observer.publish(evidence));
 assert.equal(observer.publish(evidence),false);assert.equal(stored.length,1);
 observer.connectionChanged();assert.equal(observer.publish(evidence),false);assert(epoch>0);observer.close();
}
''')

    def test_browser_operation_guard_is_armed_before_native_export(self):
        self.node(['native_export_observer.js', 'library_slot_state.js'], r'''
const assert=require('node:assert/strict'),handlers={},messages=[];let busy=false,epoch=0;
global.recv=()=>{};global.send=e=>messages.push(e);
const batch={indices:[8],paths:['E:\\private\\9.wav']};
const api={attach:(n,h)=>(handlers[n]=h,{detach(){}}),emit:send,now:()=>0,
 identity:p=>p,device:()=>0x210001,submission:a=>a[0],job:v=>v,result:v=>v,cancelled:()=>false,
 setTimer:()=>1,clearTimer:()=>{},notification:()=> 'EdLib_TaskProgress'};
const namView={slotEpoch:()=>epoch,invalidateVerifiedSlots:()=>epoch++,forgetVerifiedSlot:()=>{},
 recordVerifiedSlot:()=>true,setExportPending:value=>busy=value,setPayloadMetadata:()=>{}};
const observer=connectNativeExportLibrary({request:'fixture',repeat:true},namView,api);
handlers.submit.onEnter.call({context:{ecx:'m'}},[batch]);assert.equal(busy,true);
observer.connectionChanged();assert.equal(busy,false);
assert.equal(messages.at(-1).stage,'discarded');observer.close();
''')

    def test_actual_view_adapter_exports_exact_slot_without_selecting_or_importing(self):
        source = (ROOT / 'nam_view.js').read_text(encoding='utf-8')
        adapter = source[source.index('  const discoveryCount ='):source.index('  // HXCustomTab')]
        self.node(['native_export_observer.js'], r'''
const assert=require('node:assert/strict');let next=0x1000;const mem=new Map();
class P{
 constructor(n){this.n=n;}add(n){return new P(this.n+n);}isNull(){return this.n===0;}toString(){return String(this.n);}
 readU32(){return mem.get(this.n)||0;}readS32(){return this.readU32();}readU8(){return this.readU32();}
 writeU32(v){mem.set(this.n,v);return this;}writePointer(p){return this.writeU32(p.n);}readPointer(){return new P(this.readU32());}
 readCString(){return mem.get(this.n)||'';}readUtf8String(){return this.readCString();}
}
const Memory={alloc:n=>{const p=new P(next);next+=n+32;return p;},allocUtf8String:s=>{const p=new P(next);next+=s.length+32;mem.set(p.n,s);return p;}};
const Process={arch:'ia32',mainModule:{base:new P(0x400000)},getCurrentThreadId:()=>7};
const address=rva=>Process.mainModule.base.add(rva);let onUI=true,audition=null,toneImport=null,discoveryLocked=false,exportPending=false,workflowBusy=false;const slotNames=new Map();
const namImportController=new P(0),manager=Memory.alloc(256),irController=Memory.alloc(0x280),list=Memory.alloc(16);
manager.add(0x44).writeU32(0x210001);irController.add(0x270).writePointer(manager);
const irList=()=>list,libraryReady=()=>onUI,flushInventoryPresentation=()=>{};
const rows=Array.from({length:128},(_,i)=>{const r=Memory.alloc(0x120);mem.set(r.n,'Slot '+(i+1));return r;});
rows[8].add(0x10c).writeU32(1);rows[126].add(0x10c).writeU32(1);rows[127].add(0x10c).writeU32(1);
rows[10].add(0x10c).writeU32(1);rows[10].add(0x114).writeU32(0x10);
const getIrRow=(l,i)=>rows[i];let calls=[],count=128;
const method=rva=>{
 if(rva===0x379fb0)return ()=>count;
 if(rva===0x1a1640)return (p,text,copy)=>{assert.equal(copy,1);const rep=Memory.alloc(20);p.add(4).writePointer(rep);rep.add(4).writeU32(text.readCString().length);rep.add(16).writePointer(text);calls.push('construct');return p;};
 if(rva===0x1a0120)return ()=>calls.push('destroy');
 if(rva===0x3f5f40)return (m,indices,paths,mode)=>{assert.equal(m.n,manager.n);assert.equal(mode,0);calls.push(createNativeExportObserverApi().submission([indices,paths]));};
 throw Error('Unexpected native call '+rva);
};
''' + adapter + r'''
let snapshot=discoverySnapshot(3);assert.deepEqual(snapshot.slots,[9,127]);assert.equal(snapshot.excluded,1);
const request={slot:127,device:0x210001,revision:3,path:'E:\\private\\fresh.wav'};
const observe=(r,m,args,submit)=>{calls.push('armed');submit();return true;};
assert(exportDiscoverySlot(request,snapshot,observe));assert.deepEqual(calls,['construct','armed',{indices:[126],paths:[request.path]},'destroy']);
calls=[];assert.equal(exportDiscoverySlot({...request,slot:128},snapshot,observe),false);
discoveryLocked=true;assert.equal(exportDiscoverySlot(request,snapshot,observe),false);discoveryLocked=false;
exportPending=true;assert.equal(discoverySnapshot(3),null);exportPending=false;
workflowBusy=true;assert.equal(discoverySnapshot(3),null);workflowBusy=false;
audition={};assert.equal(discoverySnapshot(3),null);audition=null;
mem.set(rows[126].n,'Changed');assert.equal(exportDiscoverySlot(request,snapshot,observe),false);
onUI=false;assert.equal(discoverySnapshot(3),null);onUI=true;count=127;assert.equal(discoverySnapshot(3),null);
assert.deepEqual(calls,[]);
''')

    def test_actual_commit_presents_without_another_scan_and_idle_changes_are_coalesced(self):
        view_source = (ROOT / 'nam_view.js').read_text(encoding='utf-8')
        automatic = (ROOT / 'automatic_slot_discovery.js').read_text(encoding='utf-8')
        presentation = view_source[view_source.index('  function flushInventoryPresentation('):
                                   view_source.index('  const discoveryCount =')]
        snapshot = view_source[view_source.index('  function discoverySnapshot('):
                               view_source.index('  function exportDiscoverySlot(')]
        render = view_source[view_source.index('  function renderProfiles('):
                             view_source.index('  function refreshProfiles(')]

        def method(name):
            start = view_source.index('    ' + name + '(')
            return view_source[start:view_source.index('\n    },', start) + 7]

        setup = r'''
const assert=require('node:assert/strict');let now=0,timer,hook,posts=0,snapshotReads=0,rowReads=0,flushes=0;
const events=[],exports=[],callbacks=new Map(),memory=new Map(),profiles={};
Date.now=()=>now;
class P{
 constructor(n){this.n=n;}add(n){return new P(this.n+n);}isNull(){return this.n===0;}
 equals(p){return this.n===p.n;}toString(){return String(this.n);}
 readU32(){return memory.get(this.n)||0;}readU8(){return this.readU32();}
 readPointer(){return new P(this.readU32());}readCString(){return memory.get(this.n)||'';}
 readUtf8String(){return this.readCString();}
}
const nil=new P(0),window=new P(7),list=new P(8),irController=new P(0x1000),manager=new P(0x2000);
const occupied=[...Array.from({length:21},(_,i)=>i+1),123,124,125,126,127];
const matches=occupied.filter(slot=>slot>=9),rows=Array.from({length:128},(_,i)=>new P(0x10000+i*0x200));
for(const slot of occupied){memory.set(rows[slot-1].n,'Slot '+slot);memory.set(rows[slot-1].n+0x10c,1);}
memory.set(irController.n+0x270,manager.n);memory.set(manager.n+0x44,0x210001);
global.ptr=n=>new P(n);global.Process={getModuleByName:()=>({getExportByName:name=>name})};
global.Memory={allocUtf16String:value=>value,allocUtf8String:value=>value};
global.recv=(name,callback)=>callbacks.set(name,callback);global.send=e=>events.push(e);
global.NativeFunction=function(name){
 if(name==='PostMessageW')return()=>{posts++;return 1;};
 if(name==='RegisterWindowMessageW')return()=>123;
 if(name==='GetActiveWindow')return()=>window;
 assert.fail(name);
};
global.Interceptor={attach:(_address,value)=>(hook=value,{detach(){}})};
global.setInterval=callback=>(timer=callback,1);global.clearInterval=()=>{};
let onUI=true,inventoryDirty=false,inventoryFlushing=false,inventoryRetryAt=0,inventoryState='unverified',discoveryStatus='inactive',discoveryChecked=0,discoveryTotal=0,discoveryCountedAt=-1;const rememberedSlots=new Map(),slotNames=new Map(),forgetRememberedSlot=slot=>{if(rememberedSlots.delete(slot))send({type:'slotMemory',slot,forget:true});};
let discoveryLocked=false,exportPending=false,workflowBusy=false,audition=null,toneImport=null,namImportController=nil;
let slots=[],drawn=[],labelPrefix='Capture ',clearCount=0,tab=3;
const profile={nativeDiscovery:true},slotLedger=createLibrarySlotLedger(),namList=new P(11),namPanel=new P(12),NULL=nil;
let listAvailable=true;
const libraryReady=()=>onUI,irList=()=>listAvailable?list:nil;
const discoveryCount=()=>{snapshotReads++;return 128;};
const getIrRow=(_list,index)=>{rowReads++;return rows[index];};
const emitInventory=()=>events.push({type:'inventory',entries:slotLedger.displayEntries()});
const hasActiveModel=()=>false,rebuildModelMenu=()=>assert.fail('no active model'),refreshDisplayedModelName=()=>{};
const selectedSlot=()=>0,profileLabel=slot=>labelPrefix+slot;
const clearRows=()=>{clearCount++;drawn=[];flushInventoryPresentation();}; // Native re-entry must be harmless.
const appendRow=(_list,_index,text)=>drawn.push(text),findWidget=()=>nil,setVisible=()=>{},selectRow=()=>{};
const isVisible=()=>true;
const redraw=()=>{flushes++;};
'''
        methods = ',\n'.join(method(name).rstrip(',') for name in
                              ('invalidateVerifiedSlots', 'recordVerifiedSlot', 'setDiscoveryStatus'))
        checks = r'''
const view={libraryReady,discoverySnapshot,flushInventoryPresentation,needsInventoryPresentation,
 exportDiscoverySlot:p=>{exports.push({...p});return true;},
METHODS};
const api=installAutomaticSlotDiscovery(view,{available:()=>true,revision:()=>0});
function dispatch(event=123){const frame={};hook.onEnter.call(frame,[{readPointer:()=>window,add:()=>({readU32:()=>event})}]);hook.onLeave.call(frame);}
function wake(dt=0){now+=dt;timer();dispatch();}
api.host({generation:0,ready:true,present:true});
view.invalidateVerifiedSlots(false);for(let i=0;i<3;i++)view.invalidateVerifiedSlots(true);
dispatch(15);wake();assert.equal(snapshotReads,0,'startup display must not enumerate slots');
api.requestVerification();wake();wake(1501);
for(const slot of occupied){
 const plan=events.filter(e=>e.type==='automaticSlotPrepare').at(-1);assert.equal(plan.slot,slot);
 callbacks.get('automaticSlotPrepared')({payload:{token:plan.token,path:'E:\\fresh.wav'}});wake();
 if(matches.includes(slot))assert(view.recordVerifiedSlot(slotLedger.epoch(),slot,'a'.repeat(64)));
 api.result({token:plan.token,status:matches.includes(slot)?'matched':'ir'});
 wake();
}
assert.equal(api.inspect().requested,false);assert.equal(api.inspect().done.length,26);
assert.equal(slotLedger.entries().length,18);assert.equal(slotLedger.displayEntries().length,18);
assert.equal(exports.length,26);
if(NEGATIVE){
 assert.deepEqual(drawn,[],'negative control reproduces accepted18 / rendered0 at first completion');
 api.requestVerification();wake();wake(1501);
 assert.equal(drawn.length,18,'a second explicit request flushes the old commit, matching source ordering');
 assert.equal(exports.length,26);
}else{
 assert.equal(drawn.length,18,'final commit must render on its same UI wake without another click');
 const presented=events.filter(e=>e.type==='floorInventoryPresentation').at(-1);
 assert.equal(presented.profileRows,18);assert.equal(presented.verifiedSlots,18);
 assert.equal(presented.displayedSlots,18);assert(presented.nativeListReady);assert.equal(presented.selectedTab,3);
 assert(presented.listVisible);assert(presented.uiThreadReady);assert.equal(presented.nativeListHandle,'11');
 assert.equal(Object.hasOwn(presented,'names'),false);assert.equal(Object.hasOwn(presented,'contentIds'),false);
 const after={posts,snapshotReads,rowReads,flushes};
 for(let i=0;i<1000;i++){now+=250;timer();}
 assert.deepEqual({posts,snapshotReads,rowReads,flushes},after,'completed display is idle');
 view.invalidateVerifiedSlots(true,[9]);api.invalidate([9]);api.setSessionActive(true);
 const beforeClear={posts,snapshotReads};
 for(let i=0;i<1000;i++)timer();
 assert.equal(posts,beforeClear.posts+1,'idle invalidation coalesces to one pending UI wake');
 assert.equal(snapshotReads,beforeClear.snapshotReads);dispatch();
 assert.equal(drawn.length,17);assert(!drawn.includes('Capture 9'));assert.equal(exports.length,26);
 labelPrefix='Renamed ';inventoryDirty=true;
 const beforeBusy={posts,rowReads,flushes};
 for(const blocked of ['thread','locked','export','workflow','audition','import','tone']){
  onUI=blocked!=='thread';discoveryLocked=blocked==='locked';exportPending=blocked==='export';workflowBusy=blocked==='workflow';
  audition=blocked==='audition'?{}:null;namImportController=blocked==='import'?irController:nil;toneImport=blocked==='tone'?{}:null;
  for(let i=0;i<100;i++)flushInventoryPresentation();
  assert.deepEqual({posts,rowReads,flushes},beforeBusy,'busy/foreign thread skips native presentation');
 }
 onUI=true;discoveryLocked=false;exportPending=false;workflowBusy=false;audition=null;namImportController=nil;toneImport=null;
 for(const blocked of ['native-lock','missing-manager','missing-list']){
  if(blocked==='native-lock')memory.set(manager.n+0x40,1);
  if(blocked==='missing-manager')memory.set(irController.n+0x270,0);
  if(blocked==='missing-list')listAvailable=false;
  const before={posts,rowReads,flushes,snapshotReads};
  flushInventoryPresentation();assert.equal(inventoryDirty,true,blocked+' retains pending presentation');
  assert.equal(needsInventoryPresentation(),false,blocked+' backs off scheduling');
  for(let i=0;i<1000;i++){timer();flushInventoryPresentation();}
  assert.deepEqual({posts,rowReads,flushes,snapshotReads},before,blocked+' cannot read/render native rows or spin');
  memory.set(manager.n+0x40,0);memory.set(irController.n+0x270,manager.n);listAvailable=true;
  now+=1000;assert.equal(needsInventoryPresentation(),true,blocked+' becomes eligible for bounded retry');
 }
 wake();assert(drawn.every(name=>name.startsWith('Renamed ')));assert.equal(snapshotReads,beforeClear.snapshotReads);
 assert.equal(exports.length,26);assert.equal(inventoryDirty,false);assert.equal(inventoryFlushing,false);
 assert.equal(clearCount,flushes,'reentrant native redraw never duplicates a render');
}
api.close();
'''.replace('METHODS', methods)
        for negative in (False, True):
            with self.subTest(post_commit_flush=not negative):
                scheduler = automatic
                if negative:
                    self.assertIn('        namView.flushInventoryPresentation?.();', scheduler)
                    scheduler = scheduler.replace('        namView.flushInventoryPresentation?.();', '')
                self.node(['library_slot_state.js'], scheduler + setup + presentation + snapshot + render +
                          checks.replace('NEGATIVE', 'true' if negative else 'false'))


class PinnedNotificationContractTests(unittest.TestCase):
    def test_notification_index_schema_matches_pinned_editor_bytes(self):
        binary = ROOT.parents[1] / 'research/hx-edit-nam-review/floor-port/integrated-candidate-53/app/HX Edit.exe'
        if not binary.is_file():
            self.skipTest('Pinned editor fixture is not installed')
        raw = binary.read_bytes()
        self.assertEqual(hashlib.sha256(raw).hexdigest(),
                         'f7faac4996164f9039c9ae3ca8d85cd135c5bceae8a511c8921a1244046cab66')
        pe = struct.unpack_from('<I', raw, 0x3c)[0]
        count, optional = struct.unpack_from('<H', raw, pe + 6)[0], struct.unpack_from('<H', raw, pe + 20)[0]
        base = struct.unpack_from('<I', raw, pe + 52)[0]

        def read(address, size):
            rva = address - base
            for i in range(count):
                _, va, length, offset = struct.unpack_from('<IIII', raw, pe + 24 + optional + 40 * i + 8)
                if va <= rva and rva + size <= va + length:
                    return raw[offset + rva - va:offset + rva - va + size]
            self.fail('Static read outside mapped file bytes')

        self.assertEqual(read(0xccab04, 6), b'index\0')
        # All three IR mutation branches fetch the same index dictionary key,
        # convert it, and feed the unchanged integer to a native row method.
        for address, expected in {
            0x60c471: '6804abcc008bcee823aaf9ff8bc8e8cc5ff9ff8b',
            0x60c676: '6804abcc008bcee81ea8f9ff8bc8e8c75df9ff8b',
            0x60c700: '6804abcc008bcee894a7f9ff8bc8e83d5df9ff8b',
            0x60c4f2: '53e8b8611700',  # Changed: push index; update row state.
            0x60c692: '56e818601700',  # Deleted: push index; update row state.
            0x60c770: '53e87a601700',  # Renamed: push index; update row name.
            0x60c79e: '53e86c3e1700',  # Renamed: same index; get native row.
            0x5a249f: '8b400833c985c00f49c88bc1c3',  # Signed integer tag4 at +8.
            0x5a24ac: '8b48088bc1c3',  # Unsigned integer tag5 at +8.
        }.items():
            self.assertEqual(read(address, len(bytes.fromhex(expected))), bytes.fromhex(expected))
        self.assertEqual(struct.unpack('<II', read(0x5a2500 + 4 * 4, 8)), (0x5a249f, 0x5a24ac))
        for name, ctor, global_address, branch in (
            ('Librarian_HelixIRChanged', 0x4362a0, 0xe6fa5c, 0x60c42c),
            ('Librarian_HelixIRDeleted', 0x4362c0, 0xe6fe5c, 0x60c643),
            ('Librarian_HelixIRRenamed', 0x4362e0, 0xe6f99c, 0x60c6bc),
        ):
            code = read(ctor, 17)
            self.assertEqual(code[:3], b'\x6a\x01\x68')
            self.assertEqual(struct.unpack_from('<I', code, 8)[0], global_address)
            string_address = struct.unpack_from('<I', code, 3)[0]
            self.assertEqual(read(string_address, len(name) + 1), name.encode() + b'\0')
            self.assertEqual(read(branch, 5), b'\x68' + struct.pack('<I', global_address))


if __name__ == '__main__':
    unittest.main()
