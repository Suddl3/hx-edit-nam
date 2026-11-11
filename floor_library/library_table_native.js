  // Read local/native-editor state only. Never opens a separate USB session.
  const libraryOrigin = method(0x3b7e00, 'pointer', ['pointer']);
  const libraryPoint = Memory.alloc(8);
  let libraryUiThread = 0;
  let libraryRoot = NULL;
  const sidebar = createNamSidebarResize(createNamSidebarNativeApi(method, () => libraryRoot, libraryReady));
  function resizeLibrary(width) { return sidebar.requestWidth(width); }
  let librarySnapshotBusy = false;
  const slotLedger=createLibrarySlotLedger();
  let slotRefresh=()=>{};
  let modelPresentationKey='';
  function refreshReadbackPresentation(){
    // Only called by the existing native UI-thread snapshot callback. Receiver
    // threads publish evidence and wake the UI; they never touch native widgets.
    if(discoveryLocked)return;
    if(!hasActiveModel()){modelPresentationKey='';return;}
    const key=JSON.stringify([String(activeModel),slotLedger.epoch(),slotLedger.entries(),
      library.map(item=>[item.contentId,item.title]),Object.entries(profiles),
      audition ? [audition.slot,audition.name] : null]);
    if(key!==modelPresentationKey){
      modelPresentationKey=key;
      rebuildVerifiedModelMenu(activeModel);
    }
    refreshDisplayedModelName();
  }
  function forgetVerifiedSlot(slot){if(slotLedger.forget(slot,true))slotRefresh();}
  function invalidateVerifiedSlots(preserveDisplay=true){
    slotLedger.invalidate(preserveDisplay);slotRefresh();
    send({type:'librarySlotInvalidated',epoch:slotLedger.epoch()});
  }
  function recordVerifiedSlot(epoch,slot,contentId){
    const accepted=slotLedger.accept(epoch,slot,contentId);
    if(accepted && discoveryStatus==='inactive')slotLedger.commitDisplay();
    if(accepted)slotRefresh();
    return accepted;
  }
  function installedLibrarySlot(contentId){
    const list=irList();
    if(list.isNull() || discoveryLocked)return null;
    const slots=new Set(slotLedger.slotsFor(contentId));
    for(const slot of Object.keys(profiles).map(Number)){
      if(slotLedger.importCurrent(slot) && verifiedMetadata[slot]?.contentId===contentId && hasProfile(list,slot))slots.add(slot);
    }
    const valid=[...slots].filter(slot=>{
      if(!Number.isInteger(slot)||slot<1||slot>=128)return false;
      const row=getIrRow(list,slot-1);
      return !row.isNull() && !!row.add(0x10c).readU32() && !(row.add(0x114).readU32()&0x10);
    }).sort((a,b)=>a-b);
    const current=hasActiveModel()?getValue(activeModel)+1:null;
    return valid.includes(current)?current:valid[0]??null;
  }
  function hideLegacyLibrary() {
    if (namPanel.isNull()) return;
    // Hide only old NAM presentation. IRs, Presets and their actions are intact.
    const legacy = [namList, ...Object.values(buttons),
      findWidget(namPanel, 0x6e6c6567, 1), findWidget(namPanel, 0x6e6d7367, 1)];
    for (const widget of legacy) {
      if (!widget.isNull() && isVisible(widget, 0)) setVisible(widget, 0, 1);
    }
  }
  function libraryReady() {
    return libraryUiThread !== 0 && Process.getCurrentThreadId() === libraryUiThread;
  }
  function libraryFloorConnected() {
    if (!libraryReady() || irController.isNull()) return false;
    const manager = irController.add(0x270).readPointer();
    return !manager.isNull() && manager.add(0x44).readU32() === 0x210001 &&
      manager.add(0x40).readU8() === 0;
  }
  function readEmptyLibrarySlots(){
    // Native librarian state, not another USB read. Never infer emptiness while
    // a transfer owns the list, or from missing/unavailable list data.
    if(!libraryReady() || discoveryLocked || libraryBusy ||
        !namImportController.isNull() || toneImport || irController.isNull())return null;
    const list=irList(),manager=irController.add(0x270).readPointer();
    if(list.isNull() || manager.isNull() || discoveryCount(list)!==128 ||
        manager.add(0x44).readU32()!==0x210001 || manager.add(0x40).readU8()!==0)return null;
    const tracked=new Set([...slotLedger.trackedSlots(),...Object.keys(profiles).map(Number)]);
    const empty=[];
    for(const slot of tracked){
      if(!Number.isInteger(slot)||slot<1||slot>=128)continue;
      const row=getIrRow(list,slot-1);
      if(!row.isNull() && row.add(0x10c).readU32()===0)empty.push(slot);
    }
    empty.sort((a,b)=>a-b);
    for(const slot of empty)slotLedger.forget(slot);
    return empty;
  }
  function librarySnapshot() {
    if (!libraryReady() || librarySnapshotBusy) return null;
    librarySnapshotBusy = true;
    try {
    const emptySlots=readEmptyLibrarySlots();
    refreshReadbackPresentation();
    sidebar.refresh();
    const sidebarState = sidebar.inspect();
    const panelVisible = !namPanel.isNull() && isVisible(namPanel, 1);
    if (!panelVisible) return {visible:false,discoveryStatus,emptySlots,slotEpoch:slotLedger.epoch()};
    hideLegacyLibrary();
    libraryOrigin(namPanel, libraryPoint);
    const geometry = namPanel.add(0x54).readPointer();
    const bounds = {x:libraryPoint.readS32(), y:libraryPoint.add(4).readS32(),
      w:geometry.add(0xc).readS32()-geometry.add(4).readS32(),
      h:geometry.add(0x10).readS32()-geometry.add(8).readS32()};
    const rows = library.map(item => {
      const imported = Object.keys(profiles).map(Number).filter(slot =>
        slot !== 128 && (!audition || slot !== audition.slot) &&
        slotLedger.importCurrent(slot) &&
        verifiedMetadata[slot]?.contentId === item.contentId && hasProfile(irList(), slot));
      const installed = [...new Set([...imported,...slotLedger.slotsFor(item.contentId)])].sort((a,b)=>a-b);
      const preview = audition && verifiedMetadata[audition.slot]?.contentId === item.contentId && hasProfile(irList(), audition.slot);
      const refreshing=['waiting-for-editor','verifying'].includes(discoveryStatus);
      const previousSlots=refreshing ? slotLedger.displaySlotsFor(item.contentId) : [];
      const shown=refreshing ? previousSlots : installed;
      return {...item, location:shown.length ? 'Floor ' + shown.join(', ') : preview ? 'Preview 128' : 'Local',
        displaySlots:previousSlots,
        deviceStatus:refreshing ? 'checking' : installed.length ? 'verified' : preview ? 'preview' : discoveryStatus==='verifying' ? 'checking'
          : discoveryStatus==='complete' ? 'local-only' : discoveryStatus==='complete-partial' ? 'incomplete'
          : ['inactive','waiting-for-editor','disconnected'].includes(discoveryStatus) ? 'unverified' : 'interrupted',
        verifiedSlots:refreshing ? [] : installed,
        locationDetail:refreshing ? 'Refreshing installed slots in the background. Previous numbers are for reference only; selection waits for verification.'
          : installed.length ? 'Floor slot ' + installed.join(', ') + ' · Verified this session'
          : preview ? 'Temporary preview in slot 128 · Not a permanent installation'
          : discoveryStatus==='complete' ? 'Not installed · No copy found in permanent slots 1–127'
          : discoveryStatus==='verifying' ? 'Checking installed slots…'
          : discoveryStatus==='complete-partial' ? 'Some slots could not be identified · Installation not confirmed'
          : ['inactive','waiting-for-editor','disconnected'].includes(discoveryStatus) ? 'Waiting for a current device check'
          : 'Device check interrupted · Installation not confirmed',
        canLoad: (!!item.canLoad || installed.length>0) && !libraryBusy && !!libraryLoader && !audition
          && !refreshing};
    });
    return {visible:true,bounds,rows,busy:libraryBusy,discoveryStatus,emptySlots,slotEpoch:slotLedger.epoch(),
      resizeAvailable:sidebarState.available,sidebarWidth:sidebarState.width || 360,
      resizeStatus:sidebarState,
      target:hasActiveModel() ? 'NAM selected' : 'Select a NAM block'};
    } finally { librarySnapshotBusy = false; }
  }
  function loadFromTable(contentId,selectOnly=false) {
    if(discoveryLocked || ['waiting-for-editor','verifying'].includes(discoveryStatus))return false;
    const item = library.find(item => item.contentId === contentId && item.kept);
    if (!item || !libraryLoader || libraryBusy) return false;
    if(!item.canLoad && installedLibrarySlot(contentId)===null)return false;
    return libraryLoader(contentId,selectOnly) === true;
  }
