'use strict';

// Deterministic coordinator. Native calls belong to the UI-thread adapter.
function createAutomaticSlotDiscovery(api) {
  let generation=-1, present=false, epoch=0, next=0, pending=null;
  let signature=null, stableAt=0, blocked=false, closed=false;
  const done=new Set(),unidentified=new Set();
  function status(state){api.status({state,checked:done.size,pending:pending?.slot||null});}
  function reset(){epoch++;pending=null;signature=null;done.clear();unidentified.clear();stableAt=api.now();}
  function fail(reason){pending=null;blocked=true;status(reason);}
  return {
    host(value){
      if(closed || !value || !Number.isInteger(value.generation) || value.generation<generation)return;
      if(value.generation===generation)return;
      const remainedPresent=present && value.ready===true && value.present===true;
      const keepStopped=remainedPresent && blocked;
      generation=value.generation;present=value.ready===true && value.present===true;
      reset();blocked=keepStopped;
      status(keepStopped?'inventory-changed-during-discovery':present?'waiting-for-editor':'disconnected');
    },
    invalidate(){
      if(closed)return;
      const interrupted=!!pending;
      reset();
      if(interrupted || blocked){fail('inventory-changed-during-discovery');return;}
      status('waiting-for-editor');
    },
    tick(snapshot){
      if(closed || !present || blocked)return;
      if(pending && api.now()>=pending.deadline){fail('discovery-timeout');return;}
      if(!snapshot || snapshot.device!==0x210001 || snapshot.busy)return;
      if(!Array.isArray(snapshot.slots) || snapshot.slots.some((s,i,a)=>!Number.isInteger(s)||s<1||s>127||a.indexOf(s)!==i)){
        fail('invalid-inventory');return;
      }
      const current=JSON.stringify([snapshot.identity,snapshot.slots,snapshot.signature]);
      if(signature!==current){
        if(pending){reset();fail('inventory-changed-during-discovery');return;}
        reset();signature=current;return;
      }
      if(api.now()-stableAt<1500)return;
      if(pending){
        if(pending.phase==='prepared'){
          if(snapshot.revision!==pending.revision){reset();fail('inventory-changed-during-discovery');return;}
          pending.phase='exporting';pending.deadline=api.now()+18000;
          try{if(api.export(pending,snapshot)!==true)fail('export-unavailable');}
          catch(_){fail('export-unavailable');}
        }
        return;
      }
      const slot=snapshot.slots.find(s=>!done.has(s));
      if(slot===undefined){status(unidentified.size || snapshot.excluded>0 ? 'complete-partial' : 'complete');return;}
      pending={token:String(epoch)+':'+(++next),slot,device:snapshot.device,
        revision:snapshot.revision,phase:'preparing',deadline:api.now()+5000};
      status('verifying');api.prepare({...pending});
    },
    prepared(value){
      if(!pending || pending.phase!=='preparing' || value?.token!==pending.token)return;
      if(value.error || typeof value.path!=='string' || !value.path.toLowerCase().endsWith('.wav')){
        fail('preparation-failed');return;
      }
      pending.path=value.path;pending.phase='prepared';
    },
    result(value){
      if(!pending || pending.phase!=='exporting' || value?.token!==pending.token)return;
      if(!['matched','ir','unmatched-nam','unidentified'].includes(value.status)){fail('verification-stopped');return;}
      if(value.status==='unidentified')unidentified.add(pending.slot);
      done.add(pending.slot);pending=null;status('verifying');
    },
    fault(reason){if(!closed)fail(reason);},
    close(){closed=true;reset();status('closed');},
    inspect(){return {generation,present,epoch,blocked,done:[...done],pending:pending&&{...pending}};}
  };
}

// Does not execute until an explicitly opted-in candidate calls this installer.
function installAutomaticSlotDiscovery(namView, observer) {
  const win=Process.getModuleByName('user32.dll');
  const fn=(name,result,args)=>new NativeFunction(win.getExportByName(name),result,args,'stdcall');
  const post=fn('PostMessageW','int',['pointer','uint','uint','uint']);
  const wake=fn('RegisterWindowMessageW','uint',['pointer'])(Memory.allocUtf16String('HX NAM slot discovery'));
  const activeWindow=fn('GetActiveWindow','pointer',[]);
  let window=ptr(0),lastStatus='',running=false;
  const coordinator=createAutomaticSlotDiscovery({now:()=>Date.now(),
    status:value=>{const text=JSON.stringify(value);if(text!==lastStatus){lastStatus=text;namView.setDiscoveryStatus(value.state);send({type:'automaticSlotStatus',...value});}},
    prepare:value=>send({type:'automaticSlotPrepare',...value}),
    export:(request,snapshot)=>namView.exportDiscoverySlot(request,snapshot,observer.automaticSubmit)
  });
  const hook=Interceptor.attach(win.getExportByName('DispatchMessageW'),{
    onEnter(args){
      this.run=false;
      if(!namView.libraryReady())return;
      const hwnd=args[0].readPointer();
      if(window.isNull() && !hwnd.isNull() && hwnd.equals(activeWindow()))window=hwnd;
      this.run=!window.isNull() && hwnd.equals(window) && args[0].add(4).readU32()===wake;
    },
    onLeave(){
      if(!this.run || running)return;
      running=true;
      try{
        if(!observer.available())coordinator.fault('native-observer-unavailable');
        else coordinator.tick(namView.discoverySnapshot(observer.revision()));
      }
      catch(_){coordinator.fault('native-discovery-fault');}
      finally{running=false;}
    }
  });
  const timer=setInterval(()=>{if(!window.isNull())post(window,wake,0,0);},250);
  function prepared(){recv('automaticSlotPrepared',message=>{coordinator.prepared(message.payload);prepared();});}
  function result(){recv('automaticSlotResult',message=>{coordinator.result(message.payload);result();});}
  prepared();result();
  return {...coordinator,close(){clearInterval(timer);hook.detach();coordinator.close();}};
}
