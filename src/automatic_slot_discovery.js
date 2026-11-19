'use strict';

// Deterministic coordinator. Native calls belong to the UI-thread adapter.
function createAutomaticSlotDiscovery(api) {
  let generation=-1, present=false, epoch=0, next=0, pending=null;
  let signature=null, stableAt=0, blocked=false, closed=false;
  let identity=null, slotSignatures=new Map(), requested=false, verifiedOnce=false;
  let sessionActive=false, allowWhileBrowsing=false;
  // Recall: with api.recallOnConnect, a pass that runs by itself once the Floor connects (and
  // again after each full refresh from it) and only accepts slots this PC remembers. It never
  // reads a slot back: if any slot needs reading it stops and waits for a request as before.
  let recall=false;
  const recallArmed=()=>api.recallOnConnect===true && present;
  const done=new Set(),unidentified=new Set();
  function status(state){api.status({state,checked:done.size,pending:pending?.slot||null});}
  function reset(){epoch++;pending=null;signature=null;identity=null;slotSignatures.clear();done.clear();unidentified.clear();stableAt=api.now();}
  function cancelPreparation(){
    if(pending?.phase==='exporting')return false;
    if(pending){epoch++;pending=null;status('preparation-cancelled');}
    stableAt=api.now();return true;
  }
  function fail(reason){pending=null;blocked=true;status(reason);}
  return {
    host(value){
      if(closed || !value || !Number.isInteger(value.generation) || value.generation<generation)return;
      if(value.generation===generation)return;
      const remainedPresent=present && value.ready===true && value.present===true;
      const keepStopped=remainedPresent && blocked;
      generation=value.generation;present=value.ready===true && value.present===true;
      reset();blocked=keepStopped;requested=false;verifiedOnce=false;allowWhileBrowsing=false;recall=recallArmed();
      status(keepStopped?'inventory-changed-during-discovery':present?'verification-needed':'disconnected');
    },
    invalidate(slots=null){
      if(closed)return;
      if(Array.isArray(slots)){
        const changed=slots.filter(slot=>Number.isInteger(slot)&&slot>=1&&slot<128);
        if(!changed.length)return; // Preview128 never changes permanent-slot content.
        cancelPreparation();
        epoch++;pending=null;stableAt=api.now();allowWhileBrowsing=false;
        for(const slot of changed){done.delete(slot);unidentified.delete(slot);}
        requested=requested||verifiedOnce;
        status(sessionActive?'verification-paused':requested?'waiting-for-editor':'verification-needed');
        return;
      }
      const interrupted=pending?.phase==='exporting';
      cancelPreparation();
      reset();
      requested=false;verifiedOnce=false;allowWhileBrowsing=false;
      if(interrupted || blocked){recall=false;fail('inventory-changed-during-discovery');return;}
      recall=recallArmed();
      status('verification-needed');
    },
    requestVerification(options={}){
      if(closed || !present)return false;
      blocked=false;requested=true;allowWhileBrowsing=options.allowWhileBrowsing===true;
      stableAt=api.now();status(sessionActive&&!allowWhileBrowsing?'verification-paused':'verification-requested');
      return true;
    },
    resumeRequestedVerification(options={}){
      if(closed || !present || !requested || blocked)return false;
      allowWhileBrowsing=options.allowWhileBrowsing===true;
      stableAt=api.now();
      status(sessionActive&&!allowWhileBrowsing?'verification-paused':'verification-requested');
      return true;
    },
    setSessionActive(value){
      sessionActive=value===true;
      if(sessionActive){
        allowWhileBrowsing=false;cancelPreparation();
        if(requested)status('verification-paused');
      }
    },
    pauseForWorkflow(){
      if(closed || blocked)return false;
      // A queued native export is allowed to finish under its existing proof.
      // Cancelling preparation must not erase already verified slots.
      allowWhileBrowsing=false;
      const prepared=!!pending;
      if(!cancelPreparation())return false;
      status(prepared?'preparation-cancelled':'workflow-paused');
      return true;
    },
    tick(snapshot){
      if(closed || !present || blocked)return;
      if(pending && api.now()>=pending.deadline){fail('discovery-timeout');return;}
      if(!(requested||recall) || (sessionActive&&!allowWhileBrowsing))return;
      if(!snapshot || snapshot.device!==0x210001 || snapshot.busy)return;
      if(!Array.isArray(snapshot.slots) || snapshot.slots.some((s,i,a)=>!Number.isInteger(s)||s<1||s>127||a.indexOf(s)!==i)){
        fail('invalid-inventory');return;
      }
      if(identity!==null && identity!==snapshot.identity){
        cancelPreparation();
        reset();requested=false;verifiedOnce=false;recall=recallArmed();api.changed?.(null);status('verification-needed');return;
      }
      identity=snapshot.identity;
      const rows=snapshot.slotSignatures || snapshot.slots.map(slot=>[slot,snapshot.signature]);
      if(!Array.isArray(rows) || rows.length!==snapshot.slots.length ||
          rows.some(row=>!Array.isArray(row)||row.length!==2||!snapshot.slots.includes(row[0])||typeof row[1]!=='string') ||
          new Set(rows.map(row=>row[0])).size!==rows.length){fail('invalid-inventory');return;}
      const currentSlots=new Map(rows);
      const current=JSON.stringify([snapshot.identity,snapshot.slots,snapshot.signature]);
      if(signature!==current){
        if(pending?.phase==='exporting'){reset();requested=false;fail('inventory-changed-during-discovery');return;}
        cancelPreparation();
        const changed=signature===null?[]:[...new Set([...slotSignatures.keys(),...currentSlots.keys()])]
          .filter(slot=>slotSignatures.get(slot)!==currentSlots.get(slot));
        for(const slot of changed){done.delete(slot);unidentified.delete(slot);}
        slotSignatures=currentSlots;signature=current;
        if(changed.length)api.changed?.(changed);
        return;
      }
      if(api.now()-stableAt<1500)return;
      if(pending){
        if(pending.phase==='prepared'){
          if(snapshot.revision!==pending.revision){cancelPreparation();return;}
          pending.phase='exporting';pending.deadline=api.now()+18000;
          try{if(api.export(pending,snapshot)!==true)fail('export-unavailable');}
          catch(_){fail('export-unavailable');}
        }
        return;
      }
      // A slot this PC has already read back, whose name has not changed since, is not read
      // again: each read locks HX Edit for about half a second. api.remembered accepts the
      // remembered content itself (or knows the slot holds something that is not a library
      // capture) and returns true; the slot then counts as checked.
      if(api.remembered)for(const s of snapshot.slots){
        if(!done.has(s) && api.remembered(s,currentSlots.get(s))===true)done.add(s);
      }
      const slot=snapshot.slots.find(s=>!done.has(s));
      // Only a requested pass makes later changed slots rechecked by themselves (verifiedOnce).
      if(slot===undefined){verifiedOnce=verifiedOnce||requested;requested=false;recall=false;allowWhileBrowsing=false;
        status(unidentified.size || snapshot.excluded>0 ? 'complete-partial' : 'complete');return;}
      // A recall pass never reads back: what it accepted stays checked for the next request.
      if(!requested){recall=false;return;}
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
      // What this read found, with the slot's name as it was when the read was planned.
      api.learned?.(pending.slot,value.status,slotSignatures.get(pending.slot),value.contentId);
      done.add(pending.slot);pending=null;status('verifying');
    },
    fault(reason){if(!closed)fail(reason);},
    close(){closed=true;reset();status('closed');},
    needsSnapshot(){return !closed && present && !blocked && (requested||recall) &&
      (!sessionActive||allowWhileBrowsing) && pending?.phase!=='exporting';},
    needsWake(){return !closed && (!!pending || this.needsSnapshot());},
    inspect(){return {generation,present,epoch,blocked,requested,recall,sessionActive,verifiedOnce,
      done:[...done],pending:pending&&{...pending}};}
  };
}

// Does not execute until an explicitly opted-in candidate calls this installer.
function installAutomaticSlotDiscovery(namView, observer, options={}) {
  const win=Process.getModuleByName('user32.dll');
  const fn=(name,result,args)=>new NativeFunction(win.getExportByName(name),result,args,'stdcall');
  const post=fn('PostMessageW','int',['pointer','uint','uint','uint']);
  const wake=fn('RegisterWindowMessageW','uint',['pointer'])(Memory.allocUtf16String('HX NAM slot discovery'));
  const activeWindow=fn('GetActiveWindow','pointer',[]);
  // wakeAt: when the last wake was posted (0: none outstanding). HX Edit's own IR-list drag runs a
  // message loop that drops other posted messages: a lost wake left the check waiting for good
  // (Floor, November 18), so an unanswered wake is posted again after WAKE_RETRY.
  const WAKE_RETRY=1000;
  let window=ptr(0),lastStatus='',running=false,wakeAt=0;
  const coordinator=createAutomaticSlotDiscovery({now:()=>Date.now(),recallOnConnect:options.recallOnConnect!==false,
    status:value=>{const text=JSON.stringify(value);if(text!==lastStatus){lastStatus=text;namView.setDiscoveryStatus(value.state,value.checked);send({type:'automaticSlotStatus',...value});}},
    prepare:value=>send({type:'automaticSlotPrepare',...value}),
    export:(request,snapshot)=>namView.exportDiscoverySlot(request,snapshot,observer.automaticSubmit),
    remembered:(slot,name)=>namView.acceptRememberedSlot?.(slot,name)===true,
    learned:(slot,status,name,contentId)=>namView.rememberSlot?.(slot,status,name,contentId),
    changed:slots=>slots===null?observer.inventoryChanged():observer.slotsChanged(slots)
  });
  const hook=Interceptor.attach(win.getExportByName('DispatchMessageW'),{
    onEnter(args){
      this.run=false;
      if(!namView.libraryReady())return;
      const hwnd=args[0].readPointer();
      if(window.isNull() && !hwnd.isNull() && hwnd.equals(activeWindow()))window=hwnd;
      this.run=!window.isNull() && hwnd.equals(window) && args[0].add(4).readU32()===wake;
      if(this.run)wakeAt=0;
    },
    onLeave(){
      if(!this.run || running)return;
      running=true;
      try{
        coordinator.tick(null); // Deadline bookkeeping does not need native rows.
        if(coordinator.needsSnapshot()){
          if(!observer.available())coordinator.fault('native-observer-unavailable');
          else coordinator.tick(namView.discoverySnapshot(observer.revision()));
        }
        // The last tick commits display evidence after its snapshot. Also
        // present idle invalidations/metadata without requesting a new scan.
        // The view guards UI ownership/busy state and reads displayed rows only.
        namView.flushInventoryPresentation?.();
      }
      catch(_){coordinator.fault('native-discovery-fault');}
      finally{running=false;}
    }
  });
  const timer=setInterval(()=>{
    if(!window.isNull() && (wakeAt===0 || Date.now()-wakeAt>=WAKE_RETRY) &&
        (coordinator.needsWake() || namView.needsInventoryPresentation?.()))
      wakeAt=post(window,wake,0,0)!==0?Date.now():0;
  },250);
  function prepared(){recv('automaticSlotPrepared',message=>{coordinator.prepared(message.payload);prepared();});}
  function result(){recv('automaticSlotResult',message=>{coordinator.result(message.payload);result();});}
  prepared();result();
  return {...coordinator,close(){clearInterval(timer);hook.detach();coordinator.close();}};
}
