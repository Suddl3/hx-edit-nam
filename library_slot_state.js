'use strict';

// In-memory evidence only. Never loads a saved mapping or accesses a device.
function createLibrarySlotLedger() {
  let epoch=0;
  const verified=new Map(), imports=new Map();
  let displayed=new Map(),refreshing=false;
  const validSlot=slot=>Number.isInteger(slot) && slot>=1 && slot<128;
  return {
    epoch:()=>epoch,
    invalidate(preserveDisplay=true){epoch++;verified.clear();imports.clear();refreshing=true;if(!preserveDisplay)displayed.clear();},
    invalidateSlots(slots){
      const changed=new Set(slots.filter(validSlot));
      if(!changed.size)return;
      epoch++;
      for(const slot of changed){verified.delete(slot);displayed.delete(slot);imports.delete(slot);}
      for(const slot of imports.keys())imports.set(slot,epoch);
    },
    commitDisplay(){displayed=new Map(verified);refreshing=false;},
    displaySlotsFor:contentId=>[...displayed].filter(([slot,id])=>id===contentId).map(([slot])=>slot).sort((a,b)=>a-b),
    displayEntries:()=>[...displayed].sort((a,b)=>a[0]-b[0]),
    imported(slot){if(validSlot(slot)){verified.delete(slot);displayed.delete(slot);imports.set(slot,epoch);}},
    importCurrent:slot=>imports.get(slot)===epoch,
    contentFor:slot=>verified.get(slot) || null,
    entries:()=>[...verified].sort((a,b)=>a[0]-b[0]),
    trackedSlots:()=>[...new Set([...verified.keys(),...displayed.keys(),...imports.keys()])],
    forget(slot,rechecking=false){
      const had=verified.delete(slot);
      // Readback invalidates authorization immediately, but a background scan
      // retains its previous display until commit. Real replacement/deletion
      // calls omit rechecking and must still clear the old display immediately.
      const shown=!(rechecking && refreshing) && displayed.delete(slot);
      const imported=imports.delete(slot);return had||shown||imported;
    },
    accept(ticket,slot,contentId){
      if(ticket!==epoch || !validSlot(slot) || typeof contentId!=='string' ||
          !/^[0-9a-f]{64}$/.test(contentId))return false;
      imports.delete(slot);verified.set(slot,contentId);if(!refreshing)displayed.set(slot,contentId);return true;
    },
    slotsFor:contentId=>[...verified].filter(([slot,id])=>id===contentId)
      .map(([slot])=>slot).sort((a,b)=>a-b)
  };
}

// Attach the verifier only for the explicitly requested single-export trial.
// Python checks fresh file contents; this side checks that its reply is still
// current before changing any presentation state. No native calls in replies.
function connectNativeExportLibrary(config,namView,env) {
  const api=env || createNativeExportObserverApi();
  let automatic=null;
  let hostReady=!config.requireHostMonitor,hostGeneration=-1;
  const observer=installNativeExportObserver({...config,trackSlots:true},{...api,
    emit:event=>{
      if(event.stage==='submitted')namView.setExportPending?.(true);
      if(event.stage==='completed' || event.stage==='discarded')namView.setExportPending?.(false);
      api.emit(event);
    },
    slotEpoch:()=>namView.slotEpoch(),
    invalidate:(preserveDisplay=true,slots=null)=>{
      namView.invalidateVerifiedSlots(preserveDisplay,slots);
      if(automatic)automatic.invalidate(slots);
    },
    ...(config.automatic ? {busy:value=>namView.setDiscoveryLocked(value)} : {}),
    forget:slot=>namView.forgetVerifiedSlot(slot),
    publish:e=>hostReady && namView.recordVerifiedSlot(e.epoch,e.slot,e.contentId)});
  if(config.automatic){
    automatic=installAutomaticSlotDiscovery(namView,observer);
    namView.setDiscoveryPause?.(()=>automatic.pauseForWorkflow());
    namView.setDiscoveryControls?.({
      requestVerification:options=>automatic.requestVerification(options),
      resumeRequestedVerification:options=>automatic.resumeRequestedVerification(options),
      setSessionActive:value=>automatic.setSessionActive(value),
      slotsChanged:slots=>observer.slotsChanged(slots)
    });
  }
  function listenHost(){
    recv('hostDeviceChange',message=>{
      const value=message.payload;
      if(value && Number.isInteger(value.generation) && value.generation>=0 &&
          typeof value.ready==='boolean'){
        if(value.generation>hostGeneration || !value.ready){
          hostGeneration=Math.max(hostGeneration,value.generation);
          hostReady=value.ready;
          observer.connectionChanged();
          if(automatic)automatic.host(value);
          send({type:'libraryConnectionGuard',ready:hostReady,generation:hostGeneration});
        }
      }else{
        hostReady=false;observer.connectionChanged();if(automatic)automatic.fault('connection-monitor-failed');
        send({type:'libraryConnectionGuard',ready:false});
      }
      listenHost();
    });
  }
  if(config.requireHostMonitor)listenHost();
  function listen(){
    recv('librarySlotEvidence',message=>{
      const accepted=observer.publish(message.payload);
      if(automatic && message.payload?.automaticToken)automatic.result({token:message.payload.automaticToken,
        status:accepted?'matched':'discarded',contentId:accepted?message.payload.contentId:undefined});
      send({type:'librarySlotEvidenceResult',accepted,slot:message.payload?.slot,request:message.payload?.request});
      listen();
    });
  }
  function metadata(){recv('floorPayloadMetadata',message=>{
    namView.setPayloadMetadata(message.payload);metadata();
  });}
  metadata();listen();
  return {...observer,close(){if(automatic)automatic.close();observer.close();}};
}
