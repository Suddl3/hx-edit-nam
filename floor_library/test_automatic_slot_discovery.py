"""Complete offline discovery scheduling and fresh-byte verification; no USB/UI."""
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import ctypes as C

from automatic_slot_service import AutomaticSlotService
from library_catalog import empty_catalog,add_wav
from test_library_catalog import wav
from native_export_static import automatic_contract
from device_connection_watch import floor_presence,ConnectionWatch
from test_device_connection_watch import Api

HERE=Path(__file__).resolve().parent


class AutomaticTests(unittest.TestCase):
    def test_incomplete_inventory_never_claims_local_only(self):
        code=(HERE/'automatic_slot_discovery.js').read_text()+r'''
const assert=require('node:assert/strict');
for(const [result,excluded,expected] of [['matched',0,'complete'],['unidentified',0,'complete-partial'],['ir',1,'complete-partial']]){
 let now=0,plan,states=[];
 const s=createAutomaticSlotDiscovery({now:()=>now,prepare:p=>plan=p,export:()=>true,status:v=>states.push(v)});
 const snap={device:0x210001,identity:'x',signature:'x',slots:[9],revision:0,excluded};
 s.host({generation:0,ready:true,present:true});s.tick(snap);now=1501;s.tick(snap);
 s.prepared({token:plan.token,path:'capture.wav'});s.tick(snap);s.result({token:plan.token,status:result});s.tick(snap);
 assert.equal(states.at(-1).state,expected);
}
'''
        result=subprocess.run(['node','-e',code],capture_output=True,text=True,timeout=15)
        self.assertEqual(result.returncode,0,result.stderr)

    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.catalog=empty_catalog()
        self.first=add_wav(self.catalog,wav(),'One');self.second=add_wav(self.catalog,wav(gain=.2),'Two')
        self.service=AutomaticSlotService(lambda:self.catalog,self.root/'receipts')

    def plan(self,token='1:1',slot=127,revision=3):
        return self.service.prepare(dict(token=token,slot=slot,device=0x210001,revision=revision))

    def finish(self,plan,slot=127,raw=None,revision=3,sequence=1):
        # A very fast native worker may write BEFORE Python receives submission.
        Path(plan['path']).write_bytes(wav() if raw is None else raw)
        event=dict(request='observer:'+str(sequence),sequence=sequence,slot=slot,path=plan['path'],device=0x210001,revision=revision)
        self.service.receive(dict(event,stage='submitted'))
        return self.service.receive(dict(event,stage='completed',result=0,details=[0]))

    def test_automatic_multislot_and_reconnect_without_manual_export_paths(self):
        a=self.finish(self.plan());b=self.finish(self.plan('1:2',9),9,wav(gain=.2),sequence=2)
        self.assertEqual([a['evidence']['contentId'],b['evidence']['contentId']],[self.first,self.second])
        self.service.connection_changed()
        c=self.finish(self.plan('2:3'),sequence=3)
        self.assertEqual(c['evidence']['contentId'],self.first)
        self.assertEqual(len(list((self.root/'receipts').glob('*/RESULT.json'))),3)

    def test_unmatched_nam_does_not_claim_a_kept_capture(self):
        result=self.finish(self.plan(),raw=wav(gain=.7))
        self.assertEqual(result['status'],'unmatched-nam');self.assertIsNone(result['evidence'])

    def test_changed_identity_malformed_bytes_and_disconnect_fail_closed(self):
        result=self.finish(self.plan(),revision=4)
        # Submission itself fails; no later completion can revive it.
        self.assertIsNone(result)
        self.assertEqual(self.service.active.result['status'],'discarded')
        result=self.finish(self.plan('2:1'),raw=b'bad file data')
        self.assertEqual(result['status'],'unidentified');self.assertIsNone(result['evidence'])
        plan=self.plan('3:1');self.service.connection_changed()
        self.assertIsNone(self.finish(plan))

    def test_reserved_preview_invalid_slot_and_duplicate_device_rejected(self):
        for slot in (0,128,True,'127'):
            with self.assertRaises(ValueError):self.plan(slot=slot)
        with self.assertRaises(ValueError):self.service.prepare(dict(token='a',slot=1,device=0x210004,revision=0))
        self.service.close()
        with self.assertRaises(ValueError):self.plan()

    def test_foreign_manual_export_cannot_replace_private_plan(self):
        plan=self.plan();other=self.root/'other.wav';other.write_bytes(wav(gain=.2))
        self.service.receive(dict(stage='submitted',request='other',path=str(other),slot=127,sequence=1,device=0x210001,revision=3))
        self.assertIsNone(self.service.active.submission)
        self.assertEqual(self.finish(plan)['evidence']['contentId'],self.first)


    def test_scheduler_serial_reads_reconnect_busy_timeout_and_stale_callbacks(self):
        code=(HERE/'automatic_slot_discovery.js').read_text()+r'''
const assert=require('assert');let now=0,plans=[],exports=[],states=[];
const s=createAutomaticSlotDiscovery({now:()=>now,prepare:p=>plans.push(p),export:(p,v)=>{exports.push({...p});return true;},status:v=>states.push(v)});
const snap={device:0x210001,identity:'controller',signature:'a|b',slots:[9,127],busy:false,revision:3};
function tick(dt=0,v=snap){now+=dt;s.tick(v);}
function connect(g=0){s.host({generation:g,ready:true,present:true});tick();tick(1501);}
function start(){const p=plans.at(-1);s.prepared({token:p.token,path:'E:\\private\\'+p.token.replace(':','-')+'.wav'});tick();return p;}
tick(9999);assert.equal(plans.length,0);connect();assert.equal(plans.length,1);
const first=start();for(let i=0;i<5;i++)tick(100);assert.equal(exports.length,1);
s.result({token:'old',status:'matched'});assert.equal(s.inspect().done.length,0);
s.result({token:first.token,status:'matched'});tick();assert.equal(plans.at(-1).slot,127);
const second=start();s.result({token:second.token,status:'unmatched-nam'});tick();assert.equal(states.at(-1).state,'complete');
assert.deepEqual(s.inspect().done,[9,127]);s.host({generation:1,ready:true,present:false});tick(2000);assert.equal(exports.length,2);
s.host({generation:0,ready:true,present:true});assert.equal(s.inspect().present,false);
connect(2);const fresh=plans.at(-1);assert.equal(fresh.slot,9);assert.notEqual(fresh.token,first.token);
s.prepared({token:first.token,path:'E:\\stale.wav'});tick();assert.equal(exports.length,2);
tick(100,{...snap,busy:true});assert.equal(exports.length,2);start();
s.invalidate();s.result({token:fresh.token,status:'matched'});assert.deepEqual(s.inspect().done,[]);
assert.equal(s.inspect().blocked,true);
for(let i=0;i<40;i++){s.invalidate();tick(2000);}assert.equal(exports.length,3);
connect(3);assert.equal(s.inspect().blocked,true);assert.equal(exports.length,3);
s.host({generation:4,ready:true,present:false});connect(5);start();tick(18001);assert.equal(s.inspect().blocked,true);
tick(30000);assert.equal(exports.length,4);
s.host({generation:6,ready:true,present:false});connect(7);assert.equal(s.inspect().blocked,false);
s.close();const count=plans.length;tick(3000);assert.equal(plans.length,count);
// Malformed inventories never become native requests; preview128 excluded.
const t=createAutomaticSlotDiscovery({now:()=>now,prepare:()=>{throw Error('unexpected export');},status:()=>{}});
t.host({generation:0,ready:true,present:true});t.tick({...snap,slots:[128]});assert.equal(t.inspect().blocked,true);
console.log('automatic coordinator passed');
'''
        result=subprocess.run(['node','-e',code],capture_output=True,text=True,timeout=15)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)

    def test_presence_query_uses_only_windows_interface_list(self):
        class Fn:
            def __init__(self,fn):self.fn=fn
            def __call__(self,*a):return self.fn(*a)
        for paths,expected in [([],False),(['usb#vid_0e41&pid_4248&mi_00#one'],True),
                               (['usb#vid_0e41&pid_424a&mi_00#lt'],False),
                               (['usb#vid_0e41&pid_4248&mi_00#a','usb#vid_0e41&pid_4248&mi_00#b'],False)]:
            data='\0'.join(paths)+'\0\0'
            def size(count,guid,device,flags):
                C.cast(count,C.POINTER(C.c_uint32))[0]=len(data);return 0
            def read(guid,device,buffer,count,flags):
                for i,ch in enumerate(data):buffer[i]=ch
                return 0
            class Lib:pass
            lib=Lib();lib.CM_Get_Device_Interface_List_SizeW=Fn(size);lib.CM_Get_Device_Interface_ListW=Fn(read)
            with patch('device_connection_watch.C.WinDLL',return_value=lib):self.assertEqual(floor_presence(),expected)

    def test_native_adapter_builds_exact_request_without_selection_or_writes(self):
        source=(HERE/'native_export_observer.js').read_text()+'\n'+r'''
const assert=require('assert');let next=0x1000;const mem=new Map();
class P{
 constructor(n){this.n=n;}add(n){return new P(this.n+n);}isNull(){return this.n===0;}
 toString(){return String(this.n);}readU32(){return mem.get(this.n)||0;}readS32(){return this.readU32();}
 readU8(){return this.readU32();}writeU32(v){mem.set(this.n,v);return this;}writePointer(p){return this.writeU32(p.n);}
 readPointer(){return new P(this.readU32());}readCString(){return mem.get(this.n)||'';}
 readUtf8String(){return this.readCString();}
}
const Memory={alloc:n=>{const p=new P(next);next+=n+32;return p;},allocUtf8String:s=>{const p=new P(next);next+=s.length+32;mem.set(p.n,s);return p;}};
const Process={arch:'ia32',mainModule:{base:new P(0x400000)},getCurrentThreadId:()=>7};
const address=rva=>Process.mainModule.base.add(rva);let onUI=true,libraryBusy=false,audition=null,toneImport=null;
const namImportController=new P(0),manager=Memory.alloc(256),irController=Memory.alloc(0x280),list=Memory.alloc(16);
manager.add(0x44).writeU32(0x210001);irController.add(0x270).writePointer(manager);
let listPresent=true;const irList=()=>listPresent?list:new P(0),libraryReady=()=>onUI;
const rows=Array.from({length:128},(_,i)=>{const r=Memory.alloc(0x120);mem.set(r.n,'Slot '+(i+1));return r;});
rows[8].add(0x10c).writeU32(1);rows[126].add(0x10c).writeU32(1);rows[127].add(0x10c).writeU32(1);
rows[10].add(0x10c).writeU32(1);rows[10].add(0x114).writeU32(0x10);
const getIrRow=(l,i)=>rows[i];let calls=[],count=128;
const method=(rva)=>{
 if(rva===0x379fb0)return ()=>count;
 if(rva===0x1a1640)return (p,text,copy)=>{assert.equal(copy,1);const rep=Memory.alloc(20);p.add(4).writePointer(rep);rep.add(4).writeU32(text.readCString().length);rep.add(16).writePointer(text);calls.push('construct');return p;};
 if(rva===0x1a0120)return ()=>calls.push('destroy');
 if(rva===0x3f5f40)return (m,indices,paths,mode)=>{
  assert.equal(m.n,manager.n);assert.equal(mode,0);
  const api=createNativeExportObserverApi();calls.push(api.submission([indices,paths]));
 };
 throw Error('Unexpected native call '+rva);
};
'''+(HERE/'automatic_slot_native.js').read_text()+r'''
let snapshot=discoverySnapshot(3);assert.deepEqual(snapshot.slots,[9,127]);
const request={slot:127,device:0x210001,revision:3,path:'E:\\private\\fresh.wav'};
const observe=(r,m,args,submit)=>{assert.equal(r,request);assert.equal(m.n,manager.n);calls.push('armed');submit();return true;};
assert(exportDiscoverySlot(request,snapshot,observe));assert.deepEqual(calls,['construct','armed',{indices:[126],paths:[request.path]},'destroy']);
calls=[];assert.equal(exportDiscoverySlot({...request,slot:128},snapshot),false);
discoveryLocked=true;assert.equal(exportDiscoverySlot(request,snapshot,observe),false);discoveryLocked=false;
libraryBusy=true;assert(discoverySnapshot(3).busy);libraryBusy=false;
audition={};assert(discoverySnapshot(3).busy);audition=null;
mem.set(rows[126].n,'Changed');assert.equal(exportDiscoverySlot(request,snapshot,observe),false);
onUI=false;assert.equal(discoverySnapshot(3),null);onUI=true;
count=127;assert.equal(discoverySnapshot(3),null);count=128;
manager.add(0x44).writeU32(0x210004);assert.equal(discoverySnapshot(3),null);
assert.deepEqual(calls,[]);console.log('native adapter ABI replay passed');
'''
        result=subprocess.run(['node','-e',source],capture_output=True,text=True,timeout=15)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)

    def test_presence_failure_stops_automatic_readiness(self):
        values=[]
        def fail():raise OSError('fixture')
        w=ConnectionWatch(values.append,api=Api(),presence=fail);self.addCleanup(w.close)
        w.start();self.assertEqual(values[0],dict(generation=0,ready=False,present=False))


if __name__=='__main__':unittest.main()
