  // Called only from the existing main UI thread. Native submission copies the
  // two vectors into a queued job before returning; never call the worker directly.
  const discoverySubmit=method(0x3f5f40,'void',['pointer','pointer','uint8']);
  const discoveryString=method(0x1a1640,'pointer',['pointer','int']);
  const discoveryStringDestroy=method(0x1a0120,'void',[]);
  const discoveryCount=method(0x379fb0,'uint',[]);
  let discoveryLocked=false;
  let discoveryStatus='inactive';
  function discoverySnapshot(revision){
    if(!libraryReady() || irController.isNull())return null;
    const list=irList(),manager=irController.add(0x270).readPointer();
    if(list.isNull() || manager.isNull() || discoveryCount(list)!==128)return null;
    const device=manager.add(0x44).readU32();
    if(device!==0x210001)return null;
    const occupied=[],names=[];let excluded=0;
    for(let i=0;i<127;i++){
      const row=getIrRow(list,i);if(row.isNull())return null;
      if(row.add(0x10c).readU32() && (row.add(0x114).readU32()&0x10))excluded++;
      // Stock exports select HIR for protected rows (flag 0x10). They cannot
      // provide the comparable WAV payload and are excluded, never guessed.
      if(row.add(0x10c).readU32() && !(row.add(0x114).readU32()&0x10)){
        occupied.push(i+1);names.push(row.readCString(256).split('\0')[0]);
      }
    }
    return {device,revision,identity:irController.toString()+':'+manager.toString(),slots:occupied,excluded,
      signature:JSON.stringify(names),busy:discoveryLocked || libraryBusy || !!audition ||
        !namImportController.isNull() || !!toneImport || manager.add(0x40).readU8()!==0};
  }
  function exportDiscoverySlot(request,expected,observeSubmission){
    if(typeof observeSubmission!=='function')return false;
    const current=discoverySnapshot(expected.revision);
    if(!current || current.busy || current.identity!==expected.identity ||
        current.signature!==expected.signature || !current.slots.includes(request.slot) ||
        request.device!==current.device || request.revision!==current.revision)return false;
    // Private path supplied by the host's pre-armed readback adapter, never an IR name.
    if(typeof request.path!=='string' || !/^[A-Za-z]:\\/.test(request.path) ||
        !request.path.endsWith('.wav') || request.path.includes('\0'))return false;
    const index=Memory.alloc(4);index.writeU32(request.slot-1);
    const path=Memory.alloc(8),text=Memory.allocUtf8String(request.path);
    function vector(vtable,data){
      const v=Memory.alloc(24);v.writePointer(address(vtable));v.add(4).writeU32(1);
      v.add(8).writePointer(data);v.add(12).writeU32(1);v.add(16).writeU32(0);v.add(20).writeU32(1);return v;
    }
    discoveryString(path,text,1);
    try{
      const indices=vector(0x8bb0dc,index),paths=vector(0x8b42ac,path);
      const manager=irController.add(0x270).readPointer();
      // Interceptor callbacks can suppress nested NativeFunction interception.
      // Arm the exact request synchronously before the queued worker can start.
      return observeSubmission(request,manager,[indices,paths],
        ()=>discoverySubmit(manager,indices,paths,0))===true;
    }finally{discoveryStringDestroy(path);}
  }
