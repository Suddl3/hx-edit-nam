'use strict';

// HX Edit 3.82 x86. Use the native model selector, IR librarian and undo manager.
function installToneView() {
  const method = (rva, type, args) => new NativeFunction(base.add(rva), type, ['pointer', ...args], 'thiscall');
  const win = (name, type, args) => new NativeFunction(user32.getExportByName(name), type, args, 'stdcall');
  const post = win('PostMessageW', 'int', ['pointer', 'uint', 'uint', 'uint']);
  const wake = win('RegisterWindowMessageW', 'uint', ['pointer'])(Memory.allocUtf16String('HX Edit NAM TONE3000'));
  const rowCount = method(0x379fb0, 'uint', []);
  const selectedRow = method(0x380780, 'pointer', []);
  const selectRow = method(0x3821d0, 'void', ['int', 'int', 'int', 'int']);
  const clearSelection = method(0x37f690, 'void', ['int', 'int']);
  const appendRow = method(0x380940, 'void', ['int', 'pointer', 'pointer', 'int', 'int', 'pointer', 'int', 'int']);
  const findWidget = method(0x3b7ae0, 'pointer', ['uint', 'int']);
  const widgetOrigin = method(0x3b7e00, 'pointer', ['pointer']);
  const isVisible = method(0x3ba100, 'uint8', ['int']);
  const point = Memory.alloc(8);
  const selectionEvent = 0x216c6273; // sbl!
  const nam = 'NAM A2 Lite';
  const label = Memory.allocUtf8String('TONE3000');
  const title = Memory.allocUtf16String('TONE3000 Â· HX Edit NAM');
  const eventAddress = base.add(0x217120);
  const eventTypes = ['pointer', 'uint', 'pointer', 'pointer', 'pointer'];
  const modelEvent = new NativeFunction(eventAddress, 'void', eventTypes, 'thiscall');
  let active = null;
  let nextSession = 0;
  let command = null;
  let tableAction = null;
  let tableWindow = NULL;
  namView.setSlotRefresh(()=>{if (!tableWindow.isNull()) post(tableWindow, wake, 0, 0);});
  let lastTable = '';
  let tableSampling = false;
  function publishTable(window) {
    if (tableSampling || !namView.libraryReady()) return;
    tableSampling = true;
    try {
      const state = namView.librarySnapshot();
      if (!state) return;
      if (tableWindow.isNull()) {
        if (!state.visible) return;
        tableWindow = window;
      }
      const text = JSON.stringify(state);
      if (text !== lastTable) {
        lastTable = text;
        send({type:'libraryTable', window:window.toUInt32(), state});
      }
    } finally { tableSampling = false; }
  }
  function listenTableAction() {
    recv('libraryTableAction', message => {
      const value = message.payload;
      const load = ['load','select'].includes(value?.action) && typeof value.contentId === 'string' && /^[0-9a-f]{64}$/.test(value.contentId);
      const resize = value?.action === 'resize' && Number.isInteger(value.width) && value.width >= 300 && value.width <= 600;
      if ((load || resize) && !tableAction && !tableWindow.isNull()) {
        tableAction = value;
        if (!post(tableWindow, wake, 0, 0)) {
          tableAction = null;
          send({type:resize ? 'libraryResizeResult' : 'libraryResult', error:'The editor window is unavailable. Reopen the NAM tab before trying again.'});
        }
      } else if (load || resize) {
        send({type:resize ? 'libraryResizeResult' : 'libraryResult',
          error:'The editor is busy or the NAM tab is unavailable. Try again after the current action finishes.',
          reason:'action-unavailable', requested:resize ? value.width : undefined});
      }
      listenTableAction();
    });
  }
  listenTableAction();
  function trace(phase, fields = {}) {
    send({type: 'toneTrace', phase, session: active ? active.id : null, ...fields});
  }

  function parameterPanel(list) {
    // editView.xml puts PRVW beside the model selector in the same edit view.
    let widget = list;
    for (let depth = 0; depth < 5 && !widget.isNull(); depth++) {
      const panel = findWidget(widget, 0x50525657, 1); // PRVW
      if (!panel.isNull()) return panel;
      widget = widget.add(0x54).readPointer().add(0x14).readPointer();
    }
    return NULL;
  }

  function panelBounds(panel) {
    if (!isVisible(panel, 1)) return { visible: false };
    widgetOrigin(panel, point);
    const geometry = panel.add(0x54).readPointer();
    return { visible: true, x: point.readS32(), y: point.add(4).readS32(),
      w: geometry.add(0xc).readS32() - geometry.add(4).readS32(),
      h: geometry.add(0x10).readS32() - geometry.add(8).readS32() };
  }

  function stringValue(value) {
    const data = value.add(4).readPointer();
    return data.isNull() ? '' : data.add(0x10).readPointer().readUtf8String();
  }

  function position(controller) {
    return [controller.add(0x34).readPointer(), controller.add(0x80).readS32(),
      controller.add(0x98).readS32(), controller.add(0x9c).readS32()].join(':');
  }

  function block(controller) {
    return stringValue(controller.add(0x84));
  }

  function atPosition() {
    return active && active.valid && position(active.controller) === active.position;
  }

  function atTarget() {
    return atPosition() && block(active.controller) === active.block;
  }

  function undoIndex() {
    return active.editor.add(0x294).readS32();
  }

  function insertNam() {
    const controller = active.controller;
    const list = controller.add(0x58).readPointer();
    if (list.isNull()) throw new Error('Reopen the NAM model list to select the destination.');
    for (let index = 0, count = rowCount(list); index < count; index++) {
      if (getIrRow(list, index).readUtf8String() !== nam) continue;
      clearSelection(list, 0, 0);
      selectRow(list, 1, index, 0, 0);
      modelEvent(controller, selectionEvent, list, NULL, NULL);
      return;
    }
    throw new Error('Reopen the NAM model list to select the destination.');
  }

  function ensureEntry(controller) {
    const list = controller.add(0x58).readPointer();
    if (list.isNull()) return;
    let namRow = NULL;
    for (let index = 0, count = rowCount(list); index < count; index++) {
      const row = getIrRow(list, index);
      if (row.isNull()) continue;
      const name = row.readUtf8String();
      if (name === 'TONE3000') return;
      if (name === nam) namRow = row;
    }
    if (!namRow.isNull()) appendRow(list, -1, label, namRow.add(0x104).readPointer(), 0,
      namRow.add(0x100).readS32(), namRow.add(0x10c).readPointer(), 0, 0);
  }

  Interceptor.attach(base.add(0x380940), {
    onEnter(args) {
      if (!this.context.ecx.readPointer().equals(base.add(0x91fcd0)) || args[1].isNull() ||
          args[1].readUtf8String() !== nam || args[5].isNull()) return;
      this.list = this.context.ecx;
      this.image = args[2];
      this.index = args[4].toInt32();
      this.model = args[5];
    },
    onLeave() {
      if (this.list) appendRow(this.list, -1, label, this.image, 0, this.index, this.model, 0, 0);
    },
  });

  Interceptor.replace(eventAddress, new NativeCallback(function (controller, event, sender, data, flags) {
    if (event !== selectionEvent || sender.isNull() || !sender.equals(controller.add(0x58).readPointer())) {
      libraryController = controller;
      modelEvent(controller, event, sender, data, flags);
      if (event === selectionEvent || event === 0x21617663) ensureEntry(controller);
      return;
    }
    libraryController = controller;
    const row = selectedRow(sender);
    if (row.isNull() || row.readUtf8String() !== 'TONE3000') {
      return modelEvent(controller, event, sender, data, flags);
    }
    clearSelection(sender, 0, 0);
    if (!namView.libraryFloorConnected()) {
      libraryNotice('The experimental library requires a connected Helix Floor. Use the default launcher for other devices.');
      return;
    }
    if (active) {
      messageBox(getActiveWindow(), Memory.allocUtf16String('Finish the current TONE3000 selection first.'), title, 0x40);
      return;
    }
    const window = getActiveWindow();
    const editor = controller.add(0x34).readPointer();
    if (window.isNull() || editor.isNull() || !editor.readPointer().add(0xdc).readPointer().equals(base.add(0x51aaf0))) return;
    const panel = parameterPanel(sender);
    const bounds = panel.isNull() ? null : panelBounds(panel);
    if (!bounds || !bounds.visible || bounds.w < 60 || bounds.h < 60) {
      messageBox(window, Memory.allocUtf16String('The TONE3000 pane cannot fit in the current editor view.'), title, 0x40);
      return;
    }
    active = { id: ++nextSession, controller, editor, position: position(controller),
      block: block(controller), window, valid: true, panel, layout: JSON.stringify(bounds) };
    active.selectionUndo = undoIndex();
    send({ type: 'toneStart', session: active.id, window: window.toUInt32(), bounds });
  }, 'void', eventTypes, 'thiscall'));

  Interceptor.attach(base.add(0x216e00), {
    onEnter() {
      if (libraryController.equals(this.context.ecx)) libraryController = NULL;
      if (active && active.controller.equals(this.context.ecx)) {
        trace('target-invalidated', {reason: 'controller-teardown'});
        active.valid = false;
        active.panel = NULL;
      }
    },
  });

  Interceptor.attach(base.add(0x218a00), {
    onEnter(args) {
      this.ours = active && active.controller.equals(this.context.ecx);
      if (!this.ours) return;
      const event = stringValue(args[0]);
      if (event === 'EditBuffer_PresetSavedToDevice') active.deviceSaved = true;
      if (/^EditBuffer_(?:TargetDeviceChanged|DevicePresetChanged|PresetChanged|HelixSnapshotWillChange)$/.test(event)) {
        trace('target-invalidated', {reason: event});
        active.valid = false;
      }
    },
    onLeave() {
      if (this.ours && active && !active.applying &&
          !(active.inserting || active.restoring ? atPosition() : atTarget())) {
        active.valid = false;
      }
    },
  });

  function reply(request, result) {
    trace(result.error ? 'command-error' : 'command-reply', {id: request.id, action: request.action});
    if (request.workflow === 'library') {
      const contentId = request.contentId;
      namView.setLibraryBusy(false, !result.windowUnavailable);
      active = null;
      send({type: 'libraryResult', contentId, slotEpoch:namView.slotEpoch(), ...result});
      if (result.error && !result.windowUnavailable) libraryNotice((result.savedSlot ? `Capture is in slot ${result.savedSlot}. ` : '') + result.error);
      return;
    }
    send({ type: 'toneReply', id: request.id, ...result });
  }

  function finishTransfer(transfer) {
    if (toneImport !== transfer) return;
    toneImport = null;
    if (namImportController.equals(transfer.controller)) namImportController = NULL;
  }

  function poll(request, next, timeout = 12000) {
    const deadline = Date.now() + timeout;
    function check() {
      if (!active || request.session !== active.id) return;
      command = { request, next, deadline, check };
      if (!post(active.window, wake, 0, 0)) {
        command = null;
        reply(request, { error: 'HX Edit closed during the TONE3000 operation.', windowUnavailable: true });
      }
    }
    check();
  }

  function awaitImport(request, transfer, imported) {
    poll(request, expired => {
      if (transfer.error) {
        finishTransfer(transfer);
        throw new Error(transfer.error);
      }
      if (!namView.previewName(transfer)) {
        if (!expired && (request.action === 'save' || atTarget())) return null;
        finishTransfer(transfer);
        throw new Error(`HX Edit did not confirm the import. Check User IR ${transfer.slot} before retrying.`);
      }
      finishTransfer(transfer);
      trace('import-confirmed', {slot: transfer.slot});
      imported();
      return {};
    });
  }

  function applyProfile(request, transfer) {
    if (!atTarget() || undoIndex() !== active.selectionUndo) {
      throw new Error(`The target changed. The downloaded profile is in User IR ${transfer.slot}.`);
    }
    const saving = request.action === 'save';
    if (!saving) active.transfer = transfer;
    active.undoBefore = undoIndex();
    active.originalBlock = active.block;
    active.originalSlot = namView.modelSlot();
    active.originalReference = namView.modelReference();
    active.inserting = active.originalSlot === null;
    if (active.inserting) insertNam();
    let selecting = false;
    let expectedUndo;
    poll(request, expired => {
      if (!(active.inserting ? atPosition() : atTarget())) {
        throw new Error(`The target changed. The downloaded profile is in User IR ${transfer.slot}.`);
      }
      if (namView.modelSlot() === null && !expired) return null;
      if (active.inserting) {
        // Inserting into an empty cell gives it a block ID after the native edit.
        if (undoIndex() === active.undoBefore && !expired) return null;
        if (undoIndex() !== active.undoBefore + 1) {
          throw new Error(`HX Edit did not confirm the new block. Check User IR ${transfer.slot}.`);
        }
        active.block = block(active.controller);
        active.inserting = false;
      }
      if (!selecting) {
        expectedUndo = active.undoBefore + Number(active.originalSlot === null) +
          Number(namView.modelSlot() !== transfer.slot);
        selecting = saving ? namView.selectProfile(transfer) : namView.playPreview(transfer);
      }
      // HX Edit can update the Model widget before recording the edit for Undo.
      if (!selecting || namView.modelSlot() !== transfer.slot || undoIndex() !== expectedUndo) {
        if (!expired) return null;
        throw new Error(`Could not apply the profile. Check User IR ${transfer.slot} on Helix.`);
      }
      const freeSlots = namView.freeSlots(128).sort((a,b)=>a-b);
      if (saving) {
        active.selectionUndo = undoIndex();
        return { savedSlot: transfer.slot, freeSlots };
      }
      active.undoAfter = undoIndex();
      return { slot: transfer.slot, freeSlots };
    }, 3500);
  }

  function playing(transfer) {
    return transfer && atTarget() && active.undoAfter !== undefined &&
      namView.modelSlot() === transfer.slot && !!namView.previewName(transfer);
  }

  function restoreBlockReason(transfer) {
    if (!transfer) return 'no-preview-transfer';
    if (!active || !active.valid) return 'preview-target-invalidated';
    if (!atPosition()) return 'preview-position-changed';
    if (!atTarget()) return 'preview-block-changed';
    if (active.undoAfter === undefined) return 'preview-not-confirmed';
    if (namView.modelSlot() !== transfer.slot) return 'preview-model-unavailable-or-changed';
    if (!namView.previewName(transfer)) return 'preview-slot-identity-changed';
    if (active.deviceSaved) return 'preset-saved';
    if (active.undoAfter !== undoIndex()) return 'undo-history-changed';
    return null;
  }

  function canRestore(transfer) {
    const blocked = restoreBlockReason(transfer);
    return blocked === null || (blocked === 'undo-history-changed' &&
      namView.canRestoreModel(transfer, active.originalReference));
  }

  function restore(request, saved) {
    const { transfer, editor, undoBefore, undoAfter, originalSlot, originalBlock } = active;
    if (!transfer) return afterRestore(request, {}, saved);
    const retained = { notice: `Check User IR ${transfer.slot}. The audition profile was left on Helix.` };
    const blocked = restoreBlockReason(transfer);
    const preserveControls = blocked === 'undo-history-changed' &&
      namView.canRestoreModel(transfer, active.originalReference);
    if (blocked && !preserveControls) {
      trace('restore-blocked', {reason: blocked, slot: transfer.slot});
      return afterRestore(request, retained, saved);
    }

    let expectedUndo = undoBefore;
    active.applying = true;
    try {
      if (preserveControls) {
        // Only change Model. Do not undo Input, EQ, level or bypass changes.
        expectedUndo = undoIndex() + Number(namView.modelSlot() !== originalSlot);
        if (!namView.restoreModel(transfer, active.originalReference)) {
          return afterRestore(request, retained, saved);
        }
      } else {
        const steps = undoAfter - undoBefore;
        if (steps < 0 || steps > 2 || (!steps && originalSlot !== transfer.slot)) {
          return afterRestore(request, retained, saved);
        }
        const undo = new NativeFunction(editor.readPointer().add(0xdc).readPointer(), 'int', ['pointer'], 'thiscall');
        for (let step = 0; step < steps; step++) {
          if (undo(editor) < 0 || undoIndex() !== undoAfter - step - 1) {
            return afterRestore(request, retained, saved);
          }
        }
      }
    } finally {
      // afterRestore(close) can release the session on a conservative failure.
      if (active) active.applying = false;
    }

    // Preserve upstream's completion fence. A successful setter or Undo call
    // does not prove the native Model/selection and Undo record have settled.
    active.restoring = true;
    poll(request, expired => {
      if (!atPosition()) return afterRestore(request, retained, saved);
      if (undoIndex() !== expectedUndo || namView.modelSlot() !== originalSlot ||
          block(active.controller) !== originalBlock) {
        return expired ? afterRestore(request, retained, saved) : null;
      }
      active.block = originalBlock;
      active.restoring = false;
      if (!namView.clearPreview(transfer)) return afterRestore(request, retained, saved);
      namView.stopPreview();
      poll(request, expired => {
        if (namView.previewSlotEmpty(transfer)) {
          active.transfer = null;
          if (request.action !== 'close' && (!atTarget() || undoIndex() !== expectedUndo)) {
            throw new Error('The target changed. Select TONE3000 again on the desired block.');
          }
          return afterRestore(request, {}, saved);
        }
        return expired ? afterRestore(request, {
          notice: `Could not verify that User IR ${transfer.slot} was cleared.`,
        }, saved) : null;
      }, 3500);
      return {};
    }, 3500);
  }

  const commandAddress = base.add(0x35e800);
  const dispatch = new NativeFunction(commandAddress, 'uint8', ['pointer', 'pointer'], 'thiscall');
  Interceptor.replace(commandAddress, new NativeCallback(function (app, event) {
    if (active && active.transfer && !event.isNull() && event.readU32() === 0x1b) {
      messageBox(active.window, Memory.allocUtf16String(
        'Use Save to Helix in TONE3000 before saving a preset with an audition profile.'), title, 0x40);
      return 0;
    }
    return dispatch(app, event);
  }, 'uint8', ['pointer', 'pointer'], 'thiscall'));

  function afterRestore(request, result, saved) {
    active.restoring = false;
    if (request.action !== 'close') {
      if (result.notice) throw new Error(result.notice);
      active.selectionUndo = undoIndex();
      active.undoAfter = undefined;
      active.deviceSaved = false;
      if (saved) {
        applyProfile(request, saved);
      } else {
        active.previewSlot ??= 128;
        if (!namView.freeSlots().includes(128)) {
          throw new Error('Preview slot 128 is occupied or unavailable. Nothing was overwritten; review it before auditioning.');
        }
        if (!active.previewSlot) throw new Error('No free User IR slots on the Helix.');
        trace('import-before', {slot: active.previewSlot});
        const transfer = namView.importPreview(request.file, active.previewSlot, true);
        trace('import-after', {slot: active.previewSlot});
        awaitImport(request, transfer, () => applyProfile(request, transfer));
      }
    } else {
      namView.stopPreview();
      active = null;
      reply(request, result);
    }
    return {};
  }

  // Called on HX Edit's UI thread by the explicit NAM Load on Floor button.
  let libraryController = NULL;
  function libraryNotice(text) {
    messageBox(getActiveWindow(), Memory.allocUtf16String(text), title, 0x40);
  }
  function beginLibraryLoad(contentId,selectionOnly=false) {
    if (!namView.libraryFloorConnected()) {
      libraryNotice('Connect a Helix Floor before loading a library capture.');
      return false;
    }
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
    if (!namView.libraryFloorConnected()) throw new Error('The Helix Floor connection changed.');
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
    if (!slot) throw new Error('No permanent slot is free in 1â€“127. Nothing was overwritten.');
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

  function run(request) {
    if (active.workflow === 'library') return runLibraryLoad(request);
    trace('command-start', {id: request.id, action: request.action});
    if (request.action === 'save') {
      const trial = active.transfer;
      if (!canRestore(trial)) throw new Error('The audition changed. Reopen TONE3000 on the desired block.');
      const slot = namView.freeSlots(128).sort((a,b)=>a-b)[0];
      if (!slot) throw new Error('No permanent slot is free in 1â€“127. The preview remains in 128.');
      if (request.slot !== slot) throw new Error('Available storage changed. Use the lowest free destination shown before saving.');
      if (!Number.isInteger(slot) || slot < 1 || slot > 128 || slot === active.previewSlot) {
        throw new Error('Select a different free User IR slot.');
      }
      const saved = namView.importPreview(trial.source, slot);
      awaitImport(request, saved, () => {
        if (!namView.registerProfile(saved)) throw new Error(`Check User IR ${slot} on Helix.`);
        request.savedSlot = slot;
        restore(request, saved);
      });
      return;
    }
    if (request.action === 'audition' && (!atTarget() ||
        (!active.transfer && active.selectionUndo !== undoIndex()))) {
      trace('audition-target-guard', {targetMatches:!!atTarget(),
        hasTransfer:!!active.transfer, undoMatches:active.selectionUndo===undoIndex(),
        targetValid:!!active.valid});
      throw new Error('The target changed. Select TONE3000 again on the desired block.');
    }
    restore(request);
  }

  Interceptor.attach(user32.getExportByName('DispatchMessageW'), {
    onEnter(args) {
      const window = args[0].readPointer();
      const eventId = args[0].add(4).readU32();
      // Repaint, layout, completed input and explicit catalog/action wakeups only.
      // Idle/mouse-move traffic must not sample native widgets.
      const tableEvent = eventId === wake || [5,0xf,0x47,0x101,0x111,0x202].includes(eventId);
      if (tableEvent && !tableSampling && namView.libraryReady() && !window.isNull()) {
        if (window.equals(tableWindow) || (tableWindow.isNull() && window.equals(getActiveWindow()))) {
          this.tableWindow = window;
          if (eventId === wake && tableAction) {
            const pending = tableAction;
            tableAction = null;
            if (pending.action === 'resize') {
              const result = namView.resizeLibrary(pending.width);
              send({type:'libraryResizeResult', error:result.ok ? '' : 'Sidebar resize unavailable: ' + (result.detail || result.reason),
                reason:result.reason, detail:result.detail, width:result.width, requested:pending.width, changed:result.changed});
            } else if (active || command || !namView.loadFromTable(pending.contentId,pending.action==='select')) {
              send({type:'libraryResult', contentId:pending.contentId,
                error:'Select an existing NAM block and finish any audition or transfer before loading.'});
            }
          }
        }
      }
      if (!active || !window.equals(active.window)) return;
      const event = args[0].add(4).readU32();
      if (active.workflow !== 'library' && event === 0x100 && args[0].add(8).readU32() === 27) {
        // A main-window Escape must not tear down the selected native view first.
        args[0].add(4).writeU32(0); // WM_NULL; consume only this preview's Escape.
        if (!active.cancelRequested) {
          active.cancelRequested = true;
          trace('close-requested', {reason: 'host-escape'});
          send({type: 'toneCancel', session: active.id});
        }
        return;
      }
      if (event === 5 || event === 0xf || event === 0x47 || event === 0x202 || event === wake) {
        this.layoutSession = active.id;
      }
      if (!command || event !== wake) return;
      const { request, next, deadline, check } = command;
      command = null;
      try {
        if (!next) return run(request);
        const result = next(Date.now() >= deadline);
        if (result === null) setTimeout(check, 75);
        else if ('slot' in result || 'savedSlot' in result) reply(request, result);
      } catch (error) {
        const result = { error: String(error.message) };
        if (request.action !== 'close') {
          const transfer = active.transfer;
          if (playing(transfer)) result.slot = transfer.slot;
          result.freeSlots = namView.freeSlots(128).sort((a,b)=>a-b);
          if (request.savedSlot) result.savedSlot = request.savedSlot;
        }
        reply(request, result);
      }
    },
    onLeave() {
      if (this.tableWindow) publishTable(this.tableWindow);
      if (!active || active.workflow === 'library' || active.id !== this.layoutSession) return;
      namView.flushLibrary();
      const bounds = active.panel.isNull() ? { visible: false } : panelBounds(active.panel);
      const layout = JSON.stringify(bounds);
      if (layout === active.layout) return;
      active.layout = layout;
      send({ type: 'toneBounds', session: active.id, bounds });
    },
  });

  function listen() {
    recv('toneCommand', message => {
      const request = message.payload;
      if (active && active.id === request.session && !command) {
        command = { request };
        if (!post(active.window, wake, 0, 0)) {
          command = null;
          reply(request, { error: 'The HX Edit window is unavailable.' });
        }
      } else {
        reply(request, { error: 'This TONE3000 selection is no longer active.' });
      }
      listen();
    });
  }
  function listenLibrary() {
    recv('libraryUpdate', message => {
      namView.mergeLibrary(message.payload);
      if (active) post(active.window, wake, 0, 0);
      else if (!tableWindow.isNull()) post(tableWindow, wake, 0, 0);
      listenLibrary();
    });
  }
  listenLibrary();
  listen();
}
