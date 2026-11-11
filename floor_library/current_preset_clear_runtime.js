  const presetClearState=createCurrentPresetClearState();
  let clearNotificationErrors=0,clearOtherEditorNotifications=0,clearLifecycleReports=0;
  const clearWait=new NativeFunction(Process.getModuleByName('kernel32.dll').getExportByName('WaitForSingleObject'),
    'uint32',['pointer','uint32'],'stdcall');
  const clearRelease=new NativeFunction(Process.getModuleByName('kernel32.dll').getExportByName('ReleaseMutex'),
    'int',['pointer'],'stdcall');
  function presetClearEventName(value) {
    return readClearNotificationString(value);
  }
  // Existing stock model-controller notifications execute on its UI owner.
  // Hook entries only; never attach in the middle of an intercepted function.
  Interceptor.attach(address(0x218a00),{
    onEnter(args) {
      try {
        const controller=this.context.ecx,editor=controller.add(0x34).readPointer();
        if(editor.isNull() || !editor.readPointer().equals(address(0x963dfc))) {
          clearOtherEditorNotifications++;presetClearState.reset('non-helix-editor');return;
        }
        const name=presetClearEventName(args[0]);
        const completion=name==='EditBuffer_PresetContentsDidChange'?
          {result:readPresetContentsResult(args[1])}:{};
        this.clearToken=presetClearState.begin(controller.toString(),editor.toString(),
          Process.getCurrentThreadId(),name,completion);
      } catch(_) {clearNotificationErrors++;presetClearState.reset('notification-decode-failed');}
    },
    onLeave() {if(this.clearToken)presetClearState.end(this.clearToken);}
  });
  Interceptor.attach(address(0x216e00),{
    onEnter(){presetClearState.destroyed(this.context.ecx.toString());}
  });
  function currentClearContext(manager) {
    const reject=reason=>{send({type:'slotClearContext',reason});return null;};
    if(!libraryReady() || irController.isNull() || discoveryLocked || libraryBusy ||
        !namImportController.isNull() || toneImport)return reject('editor-busy-or-unavailable');
    const state=presetClearState.current(Process.getCurrentThreadId());
    if(!state) {
      // Bounded diagnostics at the user's Clear request, not an event stream.
      // No object addresses, preset names, parameters or capture names are sent.
      if(clearLifecycleReports++<64)send({type:'slotClearLifecycle',
        ...presetClearState.describe(Process.getCurrentThreadId()),
        notificationErrors:clearNotificationErrors,otherEditorNotifications:clearOtherEditorNotifications});
      return reject('preset-not-ready');
    }
    if(manager.isNull() || !irController.add(0x270).readPointer().equals(manager) ||
        manager.add(0x44).readU32()!==0x210001 || manager.add(0x40).readU8()!==0)
      return reject('device-owner-or-busy');
    const controller=ptr(state.controller),editor=ptr(state.editor);
    if(!controller.add(0x34).readPointer().equals(editor) ||
        !editor.readPointer().equals(address(0x963dfc)))return reject('editor-owner-changed');
    const engine=editor.add(0x124).readPointer();
    if(engine.isNull())return reject('engine-unavailable');
    return {presetId:[state.editor,engine,editor.add(0x294).readS32(),slotLedger.epoch()].join(':'),
      revision:state.revision,editor};
  }
  function currentClearInventory(context) {
      const read=readCurrentPresetSynchronized(context.editor,base,presetClearState.revision,
        {wait:clearWait,release:clearRelease});
      const result=read.complete && read.revision===context.revision ?
        collectCurrentPresetSlots(read.tree,context,clearModelCatalog):
        {...context,complete:false,references:[],reason:read.reason||'edit-buffer-changed'};
      // Structural diagnostics only: do not log preset names or capture titles.
      send({type:'slotClearInventory',complete:result.complete,reason:result.reason||'complete',
        references:result.complete?result.references.map(r=>r.slot):[],
        ...describeCurrentPresetGlobalNodes(read.complete?read.tree:null)});
      return result;
  }
  let clearStartupInspected=false;
  function inspectCurrentClearReferences() {
    // One read of HX Edit's local buffer after startup settles; no device
    // commands, exports, Clear submissions, or repeated polling of the buffer.
    if(clearStartupInspected || !['complete','complete-partial'].includes(discoveryStatus) ||
        !libraryReady() || irController.isNull() || discoveryLocked || libraryBusy ||
        !namImportController.isNull() || toneImport ||
        !presetClearState.current(Process.getCurrentThreadId()))return;
    clearStartupInspected=true;
    try {
      const context=currentClearContext(irController.add(0x270).readPointer());
      if(context)currentClearInventory(context);
    } catch(_) {send({type:'slotClearContext',reason:'startup-inspection-failed'});}
  }
  const slotClearGuard=installNativeSlotClearGuard(address(0x3da2b0),address(0x8bb0dc),{
    context:currentClearContext,
    inventory:currentClearInventory,
    blocked(decision) {
      send({type:'slotClearBlocked',reason:decision.reason,slots:decision.slots});
      if(libraryReady())messageBox(getActiveWindow(),Memory.allocUtf16String(slotClearBlockedMessage(decision)),
        Memory.allocUtf16String('IR slot protection'),0x30);
    },
    submitted(decision) {
      send({type:'slotClearQueued',slots:decision.slots});
    }
  });
