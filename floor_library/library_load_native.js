  // Called on HX Edit's UI thread by the explicit NAM Load on Floor button.
  let libraryController = NULL;
  function libraryNotice(text) {
    messageBox(getActiveWindow(), Memory.allocUtf16String(text), title, 0x40);
  }
  function beginLibraryLoad(contentId,selectionOnly=false) {
    if (active || command || !namImportController.isNull()) {
      libraryNotice('Finish the current audition or transfer before loading a library capture.');
      return false;
    }
    if (libraryController.isNull() || namView.modelSlot() === null || namView.modelSlot() === 128) {
      libraryNotice('Select an existing NAM A2 Lite block on a saved preset first. Finish any preview in slot 128.');
      return false;
    }
    const controller = libraryController;
    const editor = controller.add(0x34).readPointer();
    const window = getActiveWindow();
    if (window.isNull() || editor.isNull() ||
        !editor.readPointer().add(0xdc).readPointer().equals(base.add(0x51aaf0))) return false;
    active = {id: ++nextSession, workflow: 'library', contentId, controller, editor,
      position: position(controller), block: block(controller), window,
      valid: true, panel: NULL, layout: ''};
    active.selectionUndo = undoIndex();
    namView.setLibraryBusy(true);
    if(selectionOnly || namView.installedSlotFor(contentId)!==null){
      const request={workflow:'library',action:'load',session:active.id,id:0,contentId,selectionOnly:true};
      try{runLibraryLoad(request);}catch(error){reply(request,{error:String(error.message||error)});}
      return true;
    }
    send({type: 'libraryResolve', session: active.id, contentId});
    return true;
  }
  function runLibraryLoad(request) {
    if (request.error) throw new Error(request.error);
    if (!atTarget() || undoIndex() !== active.selectionUndo) {
      throw new Error('The selected NAM block changed. Select it again before loading.');
    }
    const known = namView.installedSlotFor(active.contentId);
    if (known !== null) {
      if (!namView.selectInstalled(active.contentId, known) || namView.modelSlot() !== known) {
        throw new Error('The installed capture selection was not confirmed.');
      }
      reply(request, {savedSlot: known, reused: true});
      return;
    }
    if(request.selectionOnly)throw new Error('The installed slot is no longer verified. Wait for discovery before selecting it.');
    const slot = namView.freeSlots(128).sort((a,b)=>a-b)[0];
    if (!slot) throw new Error('No permanent slot is free in 1–127. Nothing was overwritten.');
    trace('library-import-before', {slot});
    const transfer = namView.importPreview(request.file, slot);
    awaitImport(request, transfer, () => {
      request.savedSlot = slot;
      if (!namView.registerProfile(transfer)) throw new Error('The installed capture could not be confirmed.');
      if (!atTarget() || undoIndex() !== active.selectionUndo) {
        throw new Error('The capture was installed, but the selected block changed; it was not applied.');
      }
      if (!namView.selectProfile(transfer) || !atTarget() || namView.modelSlot() !== slot) {
        throw new Error('The capture was installed, but Model selection was not confirmed.');
      }
      reply(request, {savedSlot: slot, reused: false});
    });
  }
  function listenLibraryResolved() {
    recv('libraryResolved', message => {
      const result = message.payload;
      if (active && active.workflow === 'library' && result.session === active.id && !command) {
        const request = {...result, action: 'load', session: active.id, id: 0,
          workflow: 'library', contentId: active.contentId};
        command = {request};
        if (!post(active.window, wake, 0, 0)) {
          command = null;
          namView.setLibraryBusy(false, false); // Receiver thread: no native UI calls.
          active = null;
          trace('library-load-window-unavailable');
        }
      }
      listenLibraryResolved();
    });
  }
  namView.setLibraryLoader(beginLibraryLoad);
  listenLibraryResolved();
