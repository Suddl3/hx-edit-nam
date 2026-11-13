'use strict';

// HX Edit 3.82 x86. Use the native model selector, IR librarian and undo manager.
// tiles: one TONE3000 entry per NAM block a profile can go to ({label, block, modes}, nam_modes.tiles):
// each is a copy of its block's row, so HX Edit greys it exactly when that block does not fit.
function floorPreviewPlan(state) {
  if (!state.atTarget || state.deviceSaved) throw new Error('Select the original NAM block before auditioning.');
  if (state.hasTransfer) {
    if (!state.playing || !state.ownsPreview || state.previewSlot !== 128 ||
        !state.origin || state.origin.structural || !Number.isInteger(state.origin.slot) ||
        state.origin.slot < 1 || state.origin.slot > 127) {
      throw new Error('The preview target or slot ownership changed. End this preview before trying again.');
    }
    return { kind: 'reuse', slot: 128, originalSlot: state.origin.slot };
  }
  if (state.modelSlot === 128) {
    throw new Error('This block is still on the preview slot (User IR 128). Pick a capture for it in the NAM tab first, then preview.');
  }
  if (!state.sameModel || !Number.isInteger(state.modelSlot) || state.modelSlot < 1 || state.modelSlot > 127) {
    throw new Error('Select an existing compatible NAM block using a permanent slot (1–127).');
  }
  // User IR 128 is the preview slot and nothing else (owner decision, November 13): a new
  // preview always replaces whatever an earlier preview or session left in it.
  return { kind: 'first', slot: 128, replace: !state.empty128 };
}

function installToneView(tiles = [{ label: 'TONE3000', block: 'NAM A2 Lite', modes: ['original'] }], profile = {}, targetBinding = null) {
  if (profile.navigation && !targetBinding) throw new Error('Navigation requires the verified native target adapter.');
  const method = (rva, type, args) => new NativeFunction(base.add(rva), type, ['pointer', ...args], 'thiscall');
  const win = (name, type, args) => new NativeFunction(user32.getExportByName(name), type, args, 'stdcall');
  const post = win('PostMessageW', 'int', ['pointer', 'uint', 'uint', 'uint']);
  const allowForeground = win('AllowSetForegroundWindow', 'int', ['uint']);
  const isChild = win('IsChild', 'int', ['pointer', 'pointer']);
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
  const tileLabels = tiles.map(tile => Memory.allocUtf8String(tile.label));
  const tileByBlock = new Map(tiles.map((tile, index) => [tile.block, index]));
  const tileByLabel = new Map(tiles.map((tile, index) => [tile.label, index]));
  // controller -> the model row selected before a tile was clicked: the block's own model
  const lastModel = new Map();
  const title = Memory.allocUtf16String('TONE3000 · HX Edit NAM');
  const eventAddress = base.add(0x217120);
  const eventTypes = ['pointer', 'uint', 'pointer', 'pointer', 'pointer'];
  const modelEvent = new NativeFunction(eventAddress, 'void', eventTypes, 'thiscall');
  let active = null;
  let nextSession = 0;
  let command = null;
  let selectedController = NULL;
  let contextVersion = 0;
  let targetVersion = 0;
  let connectionGeneration = -1;
  let workflowRequest = null;
  let nextAutomaticRequest = 0;
  const contextEvent = /^EditBuffer_(?:TargetDeviceChanged|DevicePresetChanged|PresetChanged|PresetContentsWillChange|HelixSnapshotWillChange|HelixSignalFlowWillChange|HelixBlockAdded|HelixBlockRemoved)$/;
  // How long automatic preview cleanup waits after a snapshot recall, or after a preset, device or
  // signal-flow change, before it reads the edit buffer. Not measured on hardware: chosen to
  // outlast the native change, and cleanup still polls for confirmation afterwards.
  const SNAPSHOT_SETTLE_MS = 400, CONTEXT_SETTLE_MS = 1500;

  // The preview currently on a block, kept by the supervisor on disk: which block, what it
  // played before, and the preview's slot name. If HX Edit closes (or the session is lost)
  // while a preview is on the block, the Floor keeps the block on User IR 128. This record is
  // what lets the next launch put the earlier capture back (recoverLeftover).
  function leftoverRecord(value) {
    return value && [0, 1].includes(value.dsp) && typeof value.block === 'string' && /^block\d{1,2}$/.test(value.block) &&
      Number.isInteger(value.originalSlot) && value.originalSlot >= 1 && value.originalSlot <= 127 &&
      typeof value.name === 'string' && value.name.length > 0 && value.name.length <= 64 ?
      {dsp: value.dsp, block: value.block, originalSlot: value.originalSlot, name: value.name} : null;
  }
  let leftover = profile.navigation ? leftoverRecord(profile.leftoverPreview) : null;
  let leftoverCheckAt = 0;

  function rememberPreview() {
    if (!active?.navigation || !active.transfer || !active.origin || active.origin.structural) return;
    const record = leftoverRecord({dsp: active.nativeTarget?.dsp, block: active.nativeTarget?.blockName,
      originalSlot: active.origin.slot, name: namView.previewName(active.transfer)});
    if (!record) return;
    leftover = record;
    send({type: 'tonePreviewState', state: record});
  }

  function forgetPreview(reason) {
    if (!leftover) return;
    leftover = null;
    send({type: 'tonePreviewState', state: null});
    if (reason) send({type: 'toneTrace', reason: 'leftover-preview', stage: reason});
  }

  function recoverLeftover() {
    // UI thread, from the message hook, a few times a second while a record exists and no
    // live session owns a preview. Restores only the recorded block, only while it is on screen
    // and reads 128, and only while 128 still holds that very preview (by name).
    const now = Date.now();
    if (now < leftoverCheckAt) return;
    leftoverCheckAt = now + 250;
    if (active?.transfer || active?.pendingTransfer || active?.orphanTransfer || command || workflowRequest || toneImport) return;
    if (namView.libraryReady && !namView.libraryReady()) return;
    try {
      if (leftover.clearUntil) {
        // The block is back on its capture. Release the slot once the preset stops using it.
        const outcome = namView.releaseLeftoverPreview(leftover.name);
        if (outcome === 'cleared' || outcome === 'gone') return forgetPreview('recovered');
        if (now >= leftover.clearUntil) return forgetPreview('restored-slot-kept'); // the next preview reuses 128
        return;
      }
      // The earlier capture's slot is the sign that the IR list is loaded. Until it shows,
      // nothing is decided: an empty list must not read as "that preview is gone".
      if (!namView.slotName(leftover.originalSlot)) return;
      if (namView.slotName(AUDITION_SLOT) !== leftover.name) return forgetPreview('preview-gone');
      if (!controllerReady(selectedController)) return;
      if (selectedController.add(0x80).readS32() !== leftover.dsp || block(selectedController) !== leftover.block ||
          namView.modelSlot() !== AUDITION_SLOT) return;      // not this block, or not on screen yet
      const session = active;
      if (session) session.applying = true;
      let accepted;
      try { accepted = namView.setSlot(leftover.originalSlot); }
      finally { if (session) session.applying = false; }
      if (!accepted) return;
      send({type: 'toneTrace', reason: 'leftover-preview', stage: 'restoring', dsp: leftover.dsp, block: leftover.block,
        originalSlot: leftover.originalSlot});
      leftover.clearUntil = now + 8000;
    } catch (error) {
      send({type: 'toneTrace', reason: 'leftover-preview', stage: 'skipped', error: String(error.message)});
    }
  }

  function releaseTarget() {
    if (active?.nativeTarget) targetBinding.release(active.nativeTarget);
    if (active) active.nativeTarget = null;
  }

  function boundTargetLabel(binding, slot) {
    // DSP and block key come from the typed edit-buffer identity. Do not infer a
    // Path A/B position from a model list index or the current widget geometry.
    return `DSP ${binding.dsp + 1} · ${binding.blockName} · ${namView.profileLabel?.(slot) || namView.slotName(slot) || 'NAM'} · User IR ${slot}`;
  }

  function captureTarget() {
    const binding = targetBinding.capture(active.controller, namView.nativeModel(),
      {context:contextVersion, slot:namView.modelSlot()});
    releaseTarget();
    active.nativeTarget = binding;
    active.targetLabel = boundTargetLabel(binding, binding.originalSlot);
  }

  function connectionChanged(change) {
    if (!profile.navigation || !Number.isInteger(change?.generation) || change.generation <= connectionGeneration) return;
    connectionGeneration = change.generation;
    // This callback runs on Frida's message thread. Invalidate JS state only;
    // do not invoke native UI methods or read a widget while it is disconnecting.
    contextVersion++;
    selectedController = NULL;
    if (!active || !active.navigation) return;
    active.valid = active.bound = false;
    active.targetVersion = ++targetVersion;
    releaseTarget();
    active.invalidatedBy = 'connection-changed';
    active.quarantined = !!active.transfer || !!active.pendingTransfer || !!active.orphanTransfer;
    send({type:'toneTarget', session:active.id, version:active.targetVersion, bound:false,
      label:'', selected:'', canBind:false, preview:active.quarantined, quarantined:active.quarantined,
      text:active.quarantined ? 'The device connection changed. The upload was retained; verify it before another audition.' :
        change.present && change.ready ? 'Connection refreshed. Select a NAM block to audition.' :
        'Device unavailable. Browsing is still available.'});
  }

  function listenConnection() {
    recv('toneDeviceChange', message => {
      connectionChanged(message.payload);
      listenConnection();
    });
  }
  if (profile.navigation) listenConnection();

  function listenBrowserProcess() {
    recv('toneBrowserProcess', message => {
      const value = message.payload;
      if (active?.navigation && value?.session === active.id && Number.isInteger(value.pid) &&
          value.pid > 0 && value.pid <= 0xffffffff) active.browserPid = value.pid;
      listenBrowserProcess();
    });
  }
  function listenLibraryVisibility() {
    recv('toneLibraryVisibility', message => {
      const value = message.payload;
      if (active?.navigation && value?.session === active.id && typeof value.visible === 'boolean') {
        namView.setLibraryBrowsing?.(value.visible);
      }
      listenLibraryVisibility();
    });
  }
  function abandonSession() {
    // The supervisor has no worker or helper for this session any more (it timed out waiting for
    // a reply, or the helper ended without a confirmed close). Drop the session so Browse TONE3000
    // can start a new one. Nothing is edited: an unconfirmed preview stays on the device, where the
    // next session reports User IR 128 as occupied. UI thread only.
    const reopen = active.reopen;
    send({type:'toneTrace', reason:'session-abandoned', session:active.id, preview:!!active.transfer,
      pending:!!command || !!workflowRequest});
    command = null;
    if (workflowRequest) { workflowRequest = null; namView.setWorkflowBusy?.(false); }
    namView.abandonPreview();
    releaseTarget();
    namView.setBrowserSessionActive?.(false);
    active = null;
    // The Browse click that revealed the dead session should still open a browser.
    if (reopen && Date.now() - reopen.at < 3000) openBrowser(reopen.window, reopen.options);
  }

  function listenAbandon() {
    recv('toneAbandon', message => {
      // Frida's message thread: record the request only. The UI thread performs it on the wake
      // message, or on the next Browse click if that message is never dispatched.
      if (active?.navigation && active.id === message.payload?.session) {
        active.abandoned = true;
        post(active.window, wake, 0, 0);
      }
      listenAbandon();
    });
  }
  if (profile.navigation) { listenBrowserProcess(); listenLibraryVisibility(); listenAbandon(); }

  function controllerReady(controller) {
    if (!controller || controller.isNull()) return false;
    const editor = controller.add(0x34).readPointer();
    return !editor.isNull() && editor.readPointer().add(0xdc).readPointer().equals(base.add(0x51aaf0));
  }

  function selectedTarget() {
    try {
      if (!controllerReady(selectedController)) return null;
      const slot = namView.modelSlot();
      if (!Number.isInteger(slot) || slot < 1 || slot > 128) return null;
      const dsp = selectedController.add(0x80).readS32(), blockName = block(selectedController);
      if (![0, 1].includes(dsp) || !/^block\d{1,2}$/.test(blockName)) return null;
      // Navigation is enabled only for the original Floor catalog. Model/ir_select
      // is the native NAM parameter, rather than a guessed capture-name match.
      return { controller: selectedController, editor: selectedController.add(0x34).readPointer(),
        position: position(selectedController), block: blockName,
        label: boundTargetLabel({dsp, blockName}, slot),
        slot, tile: tiles[0], context: contextVersion };
    } catch (_) { return null; }
  }

  function publishTarget(text, force = false) {
    if (!active || !active.navigation) return;
    const selected = selectedTarget();
    const bound = active.bound && !active.quarantined && atTarget();
    const payload = { type: 'toneTarget', session: active.id, version: active.targetVersion,
      bound: !!bound, label: bound ? active.targetLabel : '',
      selected: selected ? selected.label : '', canBind: !!selected && !active.transfer && !active.pendingTransfer && !active.quarantined,
      preview: !!active.transfer || !!active.orphanTransfer || !!active.pendingTransfer, quarantined: !!active.quarantined,
      text: text || (bound ? active.transfer ? 'Auditioning in User IR 128. Input and EQ stay with this block.' :
        active.onPreviewSlot ? 'This block is still on the preview slot (User IR 128). Pick a capture for it in the NAM tab, then preview.' :
        'Ready to audition on this NAM block.' :
        active.cleanupRequested ? 'Restoring the previous block and clearing its preview…' :
        active.quarantined ? 'Preview cleanup needs attention. No new block will be changed.' :
        'Select a NAM block to audition.') };
    // Native control notifications often repeat without changing the target.
    // Compare the entire visible state; a new session has no cached snapshot.
    const snapshot = JSON.stringify(payload);
    if (!force && snapshot === active.publishedTarget) return;
    send(payload);
    active.publishedTarget = snapshot;
  }

  function observeController(controller) {
    try {
      if (!controllerReady(controller)) return;
      // Our synchronous restore/apply notifications are not a new user
      // selection. In particular, restoring the old target must not replace
      // the latest selected controller before automatic rebinding.
      if (active?.navigation && (active.applying || active.inserting)) return;
      selectedController = controller;
      if (!active || !active.navigation) return;
      if (active.bound && !active.inserting && !active.restoring && !active.applying && !atTarget()) {
        invalidate('selection-changed');
      }
      autoBindSelected();
      publishTarget();
    } catch (_) { /* a destroyed or incomplete native view is not a target */ }
  }

  function openBrowser(window, options = {}) {
    if (!profile.navigation || !window || window.isNull()) return false;
    if (active?.abandoned) { active.reopen = null; abandonSession(); }
    if (active) {
      if (options.headless) {
        if (active.headless || active.cleanupRequested || active.quarantined) return false;
        send({type:'toneAction',session:active.id,action:options.libraryAction});
        return true;
      }
      if (active.headless) return false; // finish the sidebar operation before opening a helper
      // If the supervisor answers that this session is gone, this click opens a new one instead.
      active.reopen = {window, options, at: Date.now()};
      if (options.dockPanel && !options.dockPanel.isNull()) active.panel = options.dockPanel;
      refreshBounds();
      autoBindSelected();
      publishTarget(undefined, true); // Explicit reopen gets a fresh target snapshot.
      if (active.browserPid) allowForeground(active.browserPid);
      send({ type: 'toneFocus', session: active.id, page: options.library ? 'library' : 'catalog' });
      return true;
    }
    const panel = options.dockPanel || NULL;
    let bounds;
    try { bounds = panel.isNull() ? null : panelBounds(panel); } catch (_) { bounds = null; }
    if (!options.headless && (!bounds?.visible || bounds.w < 60 || bounds.h < 60)) {
      send({type:'toneTrace',reason:'navigation-panel-unavailable'});
      return false;
    }
    active = { id: ++nextSession, navigation: true, bound: false, controller: NULL, editor: NULL,
      window, valid: false, panel, layout: JSON.stringify(bounds), tile: tiles[0],
      targetVersion: ++targetVersion, context: contextVersion, headless:!!options.headless };
    namView.setBrowserSessionActive?.(true);
    send({ type: 'toneStart', session: active.id, window: window.toUInt32(), bounds,
      tile: tiles[0], profile: '', navigation: true, library: !!options.library,
      headless:!!options.headless, libraryAction:options.libraryAction });
    autoBindSelected();
    publishTarget();
    return true;
  }

  function bindSelectedTarget() {
    if (active.transfer || active.pendingTransfer || active.orphanTransfer || active.quarantined) {
      throw new Error('End the previous preview before choosing another block.');
    }
    const selected = selectedTarget();
    if (!selected) throw new Error('Select a compatible NAM block to audition.');
    Object.assign(active, selected, { valid: true, bound: true, tile: selected.tile,
      model: selected.tile.block, targetLabel: selected.label, targetVersion: ++targetVersion,
      deviceSaved: false, invalidatedBy: null, quarantined: false, onPreviewSlot: selected.slot === AUDITION_SLOT });
    // A block still on User IR 128 with no record of its earlier capture: it can take Load on
    // Floor or Use on Floor (which give it a permanent slot), but there is nothing to restore a
    // preview to, so it gets no typed restore binding and floorPreviewPlan refuses a preview.
    if (active.onPreviewSlot) { releaseTarget(); }
    else {
      try { captureTarget(); }
      catch (error) { active.bound = active.valid = false; throw error; }
    }
    active.selectionUndo = undoIndex();
    publishTarget();
  }

  function autoBindSelected() {
    if (!active?.navigation || command || workflowRequest || active.transfer || active.pendingTransfer ||
        active.orphanTransfer || toneImport || active.cleanupRequested || active.quarantined ||
        active.restoring || active.applying || active.inserting) return false;
    try {
      if (active.bound && atTarget()) return true;
      const selected = selectedTarget();
      if (!selected) return false;
      // A block that an earlier session left on the preview slot. With a record of its earlier
      // capture it is put back first (recoverLeftover); wait for that rather than binding 128.
      if (selected.slot === AUDITION_SLOT && leftover) return false;
      bindSelectedTarget();
      return true;
    }
    catch (_) {
      // A partial/rebuilding native selection is not a target. The next native
      // selection event can try again; no edit or upload has been requested.
      publishTarget();
      return false;
    }
  }

  function bindSelected(request) {
    bindSelectedTarget();
    reply(request, { target: true, version: active.targetVersion });
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

  function refreshBounds(force = false) {
    if (!active || active.headless) return;
    let bounds;
    try { bounds = active.panel.isNull() ? {visible:false} : panelBounds(active.panel); }
    catch (_) { bounds = {visible:false}; }
    const layout = JSON.stringify(bounds);
    if (!force && layout === active.layout) return;
    active.layout = layout;
    send({type:'toneBounds', session:active.id, bounds});
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
    return active && active.valid && controllerReady(active.controller) &&
      (!active.navigation || active.context === contextVersion && active.controller.equals(selectedController)) &&
      position(active.controller) === active.position;
  }

  function atTarget() {
    return atPosition() && block(active.controller) === active.block;
  }

  function targetOnScreen() {
    // The bound block's own parameter view is the one on screen, so namView's Model control is
    // this block's. Unlike atTarget() this does not need the binding to be valid for new work:
    // a snapshot recall invalidates it while the same view stays up.
    try {
      return !!active && controllerReady(active.controller) && active.controller.equals(selectedController) &&
        active.context === contextVersion && position(active.controller) === active.position &&
        block(active.controller) === active.block;
    } catch (_) { return false; }
  }

  function undoIndex() {
    return active.editor.add(0x294).readS32();
  }

  function pick(list, name) {
    // select a model list row by its text, as a click would (the caller sends the selection event)
    for (let index = 0, count = rowCount(list); index < count; index++) {
      const row = getIrRow(list, index);
      if (row.isNull() || row.readUtf8String() !== name) continue;
      clearSelection(list, 0, 0);
      selectRow(list, 1, index, 0, 0);
      return true;
    }
    return false;
  }

  function insertNam() {
    // the tile's block: into an empty cell, or in place of another NAM block (HX Edit changes the model)
    const controller = active.controller;
    const list = controller.add(0x58).readPointer();
    if (list.isNull() || !pick(list, active.tile.block)) {
      throw new Error('Reopen the NAM model list to select the destination.');
    }
    modelEvent(controller, selectionEvent, list, NULL, NULL);
  }

  function dismiss(reason = 'selection-changed') {
    // close the TONE3000 pane (TonePreview cancels; its close undoes the audition)
    if (!active || active.dismissed) return;
    active.dismissed = true;
    send({ type: 'toneDismiss', session: active.id, reason });
  }

  function targetChanged(where, text) {
    // which check failed (trace.log; no names): the block moved or changed, or an edit happened since
    const state = { type: 'toneTrace', reason: 'target-changed', where, valid: active.valid,
      invalidatedBy: active.invalidatedBy || null, undo: undoIndex(), selectionUndo: active.selectionUndo };
    try {
      Object.assign(state, { atPosition: atPosition(), sameBlock: block(active.controller) === active.block,
        blockSet: !!active.block, blockNow: !!block(active.controller) });
    } catch (error) {
      state.readError = String(error.message);
    }
    send(state);
    return new Error(text);
  }

  function invalidate(by) {
    active.invalidatedBy = active.invalidatedBy || by;
    active.valid = false;
    if (active.navigation) {
      send({type:'toneTrace',reason:'navigation-target-invalidated',by,
        preview:!!active.transfer,binding:!!active.nativeTarget?.valid,sameContext:active.context === contextVersion});
      active.bound = false;
      active.targetVersion = ++targetVersion;
      active.quarantined = !!active.transfer || !!toneImport;
      if (active.transfer) {
        // With its block still addressable the preview is restored on that block. After a preset
        // or device change it is not: cleanup then only releases an owned, unreferenced User IR 128
        // (releaseDetachedPreview), so leaving a preset does not leave the slot behind.
        active.cleanupRequested = true;
        post(active.window, wake, 0, 0); // restore after this native event has unwound
      }
      publishTarget();
      return;
    }
    dismiss();
  }

  function settleCleanup(delay) {
    // Automatic cleanup waits until the native change that requested it has been applied.
    active.settleUntil = Math.max(active.settleUntil || 0, Date.now() + delay);
  }

  function ensureEntry(controller) {
    if (profile.navigation) return;
    const list = controller.add(0x58).readPointer();
    if (list.isNull()) return;
    const present = new Set();
    const blockRows = new Map();
    for (let index = 0, count = rowCount(list); index < count; index++) {
      const row = getIrRow(list, index);
      if (row.isNull()) continue;
      const name = row.readUtf8String();
      if (tileByLabel.has(name)) present.add(name);
      if (tileByBlock.has(name)) blockRows.set(name, row);
    }
    const selected = selectedRow(list);
    if (!selected.isNull() && !active) {    // (an audition's model switch is not the block's own model)
      const name = selected.readUtf8String();
      if (!tileByLabel.has(name)) lastModel.set(controller.toString(), name);
    }
    tiles.forEach((tile, index) => {
      const row = blockRows.get(tile.block);
      if (present.has(tile.label) || !row) return;
      appendRow(list, -1, tileLabels[index], row.add(0x104).readPointer(), 0,
        row.add(0x100).readS32(), row.add(0x10c).readPointer(), 0, 0);
    });
  }

  // --- NAM category -----------------------------------------------------------------------
  // With the catalog's NAM category (profile.namCategory), the model column lists the captures
  // that are on the Floor under the NAM A2 Lite block row of each group. Picking one puts that
  // capture on the selected block: directly when it is already a NAM block, otherwise by
  // choosing the NAM block row first and setting the capture when its Model control appears.
  const captureRows = new Map();   // row label -> {slot, header}
  const captureLabels = new Map(); // row label -> its native string, kept alive for HX Edit
  let pendingCapture = null;

  function captureGroup(gear) {
    // nam_catalog.NAM_CATEGORY_GROUPS: 1 Amp, 2 Amp + cab, 3 Pedal, 4 Other.
    return gear === 'amp' || gear === 'preamp' ? 1 : gear === 'amp-cab' || gear === 'amp-pedal-cab' ? 2 :
      gear === 'pedal' || gear === 'pedal-amp' ? 3 : 4;
  }

  function captureRowLabel(capture) {
    const name = String(capture.name).split(' – ').pop().trim() || 'Capture';
    return `${capture.slot}: ${name.length > 28 ? name.slice(0, 27).trimEnd() + '…' : name}`;
  }

  function capturesUnder(header) {
    // header: the block row's text. Its trailing spaces name the group; none (Amp > NAM, or an
    // HX Edit that trims names) lists every capture.
    const group = header.length - header.trimEnd().length;
    return namView.installedCaptures()
      .filter(capture => group === 0 || captureGroup(capture.gear) === group)
      .sort((a, b) => String(a.name).localeCompare(String(b.name)) || a.slot - b.slot);
  }

  function appendCaptureRows(frame) {
    try {
      namView.requestSlotCheck?.();          // remembered slots make this immediate after the first run
      for (const capture of capturesUnder(frame.header)) {
        const label = captureRowLabel(capture);
        if (!captureLabels.has(label)) captureLabels.set(label, Memory.allocUtf8String(label));
        captureRows.set(label, {slot: capture.slot, header: frame.header});
        appendRow(frame.list, -1, captureLabels.get(label), frame.image, 0, frame.index, frame.model, 0, 0);
      }
    } catch (error) {
      send({type: 'toneTrace', reason: 'nam-category', stage: 'rows-skipped', error: String(error.message)});
    }
  }

  function chooseCapture(controller, list, entry) {
    // A capture row was clicked. Never forwarded to HX Edit as a model choice.
    clearSelection(list, 0, 0);
    if (active?.transfer || active?.pendingTransfer || command || workflowRequest || toneImport) {
      send({type: 'toneTrace', reason: 'nam-category', stage: 'ignored-busy', slot: entry.slot});
      return;
    }
    const session = active;
    if (session) session.applying = true;
    try {
      // The Model control on screen belongs to the block whose model list was clicked. If it
      // exists, this is already a NAM block: change its capture only, never re-pick its model
      // (that would reset Input and EQ).
      if (Number.isInteger(namView.modelSlot())) {
        const accepted = namView.setSlot(entry.slot);
        send({type: 'toneTrace', reason: 'nam-category', stage: accepted ? 'capture-set' : 'capture-refused', slot: entry.slot});
        return;
      }
      // Not a NAM block yet: choose the block row as a click would, then finish in the hook.
      if (!pick(list, entry.header)) {
        send({type: 'toneTrace', reason: 'nam-category', stage: 'block-row-missing', slot: entry.slot});
        return;
      }
      pendingCapture = {slot: entry.slot, dsp: controller.add(0x80).readS32(), until: Date.now() + 3000};
      send({type: 'toneTrace', reason: 'nam-category', stage: 'adding-block', slot: entry.slot});
      modelEvent(controller, selectionEvent, list, NULL, NULL);
    } finally { if (session) session.applying = false; }
  }

  function finishPendingCapture() {
    // UI thread, from the message hook: the new NAM block's Model control exists now.
    const pending = pendingCapture;
    try {
      if (Date.now() > pending.until) {
        pendingCapture = null;
        send({type: 'toneTrace', reason: 'nam-category', stage: 'block-not-confirmed', slot: pending.slot});
        return;
      }
      if (namView.libraryReady && !namView.libraryReady()) return;
      if (!controllerReady(selectedController) || selectedController.add(0x80).readS32() !== pending.dsp) return;
      if (!Number.isInteger(namView.modelSlot())) return;
      pendingCapture = null;
      const session = active;
      if (session) session.applying = true;
      let accepted;
      try { accepted = namView.setSlot(pending.slot); }
      finally { if (session) session.applying = false; }
      send({type: 'toneTrace', reason: 'nam-category', stage: accepted ? 'capture-set' : 'capture-refused', slot: pending.slot});
    } catch (error) {
      pendingCapture = null;
      send({type: 'toneTrace', reason: 'nam-category', stage: 'skipped', error: String(error.message)});
    }
  }

  // --- NAM block icon ---------------------------------------------------------------------
  // HX Edit draws a path block from the catalog record it resolves for the block's model id
  // (icon, short name and colour come from that record's category), and for an id held by several
  // categories its loader keeps the last one, so every NAM block showed the NAM Other waveform.
  // The four NAM categories each hold their own record of the one NAM model. Here the record a
  // block points at is swapped for the one whose category matches the block's capture type, at
  // the two places HX Edit reads it: the path item, just before it redraws itself, and the
  // selector's lookup, which decides the highlighted category and the header. Records are only
  // exchanged between these four siblings of the same model; nothing is sent to the device.
  // (Addresses and layouts: BLOCK-ICON-RESEARCH.md; confirmed by probes on November 13.)
  const NAM_MODEL_ID = 'HD2_AmpNAMLite';
  const NAM_CATEGORY_NAMES = ['NAM Amp', 'NAM Amp + cab', 'NAM Pedal', 'NAM Other'];  // nam_catalog.NAM_CATEGORY_GROUPS
  const namRecords = new Map();      // category name -> that category's record of the NAM model
  const flowDsp = new Map();         // path object -> its DSP index
  const flowItems = new Map();       // path item -> {item, object, vtable}, to redraw after a change
  const selectorStack = [];          // model selectors currently syncing to the cursor block
  let iconReports = 40, iconState = '', iconCheckAt = 0, iconRefreshAt = 0, iconNudge = null;
  const l6text = p => { const rep = p.add(4).readPointer(); return rep.isNull() ? '' : rep.add(0x10).readPointer().readCString(); };
  const redrawItem = new NativeFunction(base.add(0x40ae70), 'void', ['pointer', 'int'], 'thiscall');

  function indexNamRecords(catalog) {
    const count = (o, first, end) => o.add(end).readU32() - o.add(first).readU32();
    const at = (o, data, first, i) => o.add(data).readPointer().add(4 * (o.add(first).readU32() + i)).readPointer();
    for (let i = 0; i < count(catalog, 0x14, 0x18) && i < 64; i++) {
      const category = at(catalog, 0x0c, 0x14, i), name = l6text(category.add(4));
      if (!NAM_CATEGORY_NAMES.includes(name)) continue;
      for (let j = 0; j < count(category, 0x3c, 0x40) && j < 8; j++) {
        const sub = at(category, 0x34, 0x3c, j);
        for (let k = 0; k < count(sub, 0x28, 0x2c) && k < 16; k++) {
          const record = at(sub, 0x20, 0x28, k);
          if (l6text(record.add(0x24)) === NAM_MODEL_ID) namRecords.set(name, record);
        }
      }
    }
  }

  function wantedRecord(dsp, blockName) {
    // The record of the category that matches this block's capture, or null to leave HX Edit's own.
    // The edit buffer is the one the model selector holds; the selector exists from startup,
    // before the user has selected anything (selectedController is only set by a selection).
    iconWhy = `records=${namRecords.size}`;
    if (namRecords.size !== NAM_CATEGORY_NAMES.length) return null;
    iconWhy = 'no-editor';
    if (iconEditor.isNull()) return null;
    const slot = targetBinding.peekSlot(iconEditor, dsp, blockName);
    iconWhy = `slot=${slot} dsp=${dsp}`;
    if (!Number.isInteger(slot)) return null;
    const capture = namView.installedCaptures().find(entry => entry.slot === slot);
    iconWhy = capture ? `gear=${capture.gear}` : `slot ${slot} not a known capture (${namView.installedCaptures().length} known)`;
    return capture ? namRecords.get(NAM_CATEGORY_NAMES[captureGroup(capture.gear) - 1]) || null : null;
  }
  let iconWhy = '', iconWhyLast = '', iconEditor = NULL;
  function iconSkipped(stage, blockName) {
    // Why a NAM block kept HX Edit's own record: once per distinct reason, a few times at most.
    const line = `${stage}|${blockName}|${iconWhy}`;
    if (line === iconWhyLast || iconReports <= 0) return;
    iconWhyLast = line; iconReports--;
    send({type: 'toneTrace', reason: 'nam-icon', stage: stage + '-kept', block: blockName, why: iconWhy});
  }

  function iconTrace(stage, blockName, record) {
    if (iconReports-- > 0) send({type: 'toneTrace', reason: 'nam-icon', stage, block: blockName,
      category: l6text(record.add(0x30).readPointer().add(0x14).readPointer().add(4))});
  }

  function retargetItem(item, stage) {
    // Point one path item's block at the record of its capture's category. Called as HX Edit
    // refreshes the item, and directly before a redraw this code asks for: Frida does not run a
    // hook for a call made from inside another hook's callback, so the redraw alone would not.
    try {
      let object = item.add(0x8c).readPointer();
      if (object.isNull()) object = item.add(0x88).readPointer();
      if (object.isNull() || object.readS32() !== 0) return;                   // blocks only
      const record = object.add(0x24).readPointer();
      if (record.isNull() || l6text(record.add(0x24)) !== NAM_MODEL_ID) return;
      if (flowItems.size > 128) flowItems.clear();
      flowItems.set(item.toString(), {item, object, vtable: item.readPointer()});
      const dsp = flowDsp.get(object.toString()), blockName = l6text(object.add(8));
      if (dsp === undefined) { iconWhy = 'path object never resolved'; iconSkipped(stage, blockName); return; }
      const wanted = wantedRecord(dsp, blockName);
      if (wanted && !wanted.equals(record)) { object.add(0x24).writePointer(wanted); iconTrace(stage, blockName, wanted); }
      else if (!wanted) iconSkipped(stage, blockName);
    } catch (error) { iconWhy = String(error.message); iconSkipped(stage, '?'); }
  }

  function redrawNamBlocks() {
    // Ask the path items seen so far to redraw. An item that is no longer the object it was is dropped.
    for (const [key, entry] of [...flowItems]) {
      try {
        const current = entry.item.add(0x8c).readPointer().isNull() ? entry.item.add(0x88).readPointer() : entry.item.add(0x8c).readPointer();
        if (!entry.item.readPointer().equals(entry.vtable) || !current.equals(entry.object)) { flowItems.delete(key); continue; }
        retargetItem(entry.item, 'redraw');
        redrawItem(entry.item, 1);
      } catch (_) { flowItems.delete(key); }
    }
  }

  let iconWindow = NULL, iconActiveUntil = 0;
  function watchNamIcons(hwnd) {
    // UI thread, from the message hook. A change of the selected block's capture, or of what is
    // known to be installed, means some block may now belong under a different category.
    const now = Date.now();
    if (hwnd && !hwnd.isNull()) iconWindow = hwnd;
    if (!iconNudge) {
      // An idle editor dispatches no messages, so a change that arrives with the last one (the
      // slot check finishing) would never be acted on. While anything is pending, or for a few
      // seconds after activity, ask for one more pass. Frida's timer thread: post only.
      iconNudge = setInterval(() => {
        if (!iconWindow.isNull() && (iconRefreshAt || Date.now() < iconActiveUntil)) post(iconWindow, wake, 0, 0);
      }, 400);
    }
    if (iconRefreshAt && now >= iconRefreshAt) { iconRefreshAt = 0; redrawNamBlocks(); }
    if (!iconActiveUntil) iconActiveUntil = now + 20000;     // startup: the first slot check is still to come
    if (now < iconCheckAt) return;
    iconCheckAt = now + 250;
    try {
      if (namView.libraryReady && !namView.libraryReady()) return;
      const state = `${iconEditor}|${controllerReady(selectedController) ? selectedController.toString() : ''}|${namView.modelSlot()}|${namView.installedCaptures().length}`;
      if (state !== iconState) { iconState = state; iconRefreshAt = now + 150; iconActiveUntil = now + 4000; }
    } catch (_) { /* presentation only */ }
  }

  if (profile.namCategory) {
    Interceptor.attach(base.add(0x3c24e0), {            // HXModelCatalog::findModel(id, variant)
      onEnter(args) {
        try {
          if (namRecords.size === 0) indexNamRecords(this.context.ecx);
          this.nam = selectorStack.length > 0 && l6text(args[0]) === NAM_MODEL_ID;
        } catch (_) { this.nam = false; }
      },
      onLeave(result) {
        if (!this.nam || result.isNull()) return;
        try {
          const selector = selectorStack[selectorStack.length - 1];
          const blockName = block(selector), wanted = wantedRecord(selector.add(0x80).readS32(), blockName);
          if (wanted && !wanted.equals(result)) { result.replace(wanted); iconTrace('selector', blockName, wanted); }
          else if (!wanted) iconSkipped('selector', blockName);
        } catch (error) { iconWhy = String(error.message); iconSkipped('selector', '?'); }
      },
    });
    Interceptor.attach(base.add(0x21e500), {            // model selector: sync to the cursor block
      onEnter() {
        selectorStack.push(this.context.ecx);
        try { if (controllerReady(this.context.ecx)) iconEditor = this.context.ecx.add(0x34).readPointer(); } catch (_) { /* keep the last one */ }
      },
      onLeave() { selectorStack.pop(); },
    });
    Interceptor.attach(base.add(0x269540), {            // signal-flow data: resolve one path object
      onEnter(args) {
        try {
          if (flowDsp.size > 512) flowDsp.clear();
          flowDsp.set(args[0].toString(), this.context.ecx.add(0x0c).readS32());
        } catch (_) { /* unknown object: its item keeps HX Edit's record */ }
      },
    });
    Interceptor.attach(base.add(0x40ae70), {            // path item: refresh icon, label and colour
      onEnter() { retargetItem(this.context.ecx, 'path'); },
    });
  }

  // --- block icon research probes (profile.namProbe; trace only, change nothing) ---------------
  // Static analysis (BLOCK-ICON-RESEARCH.md) says a path block's icon, short name and colour come
  // from the catalog record findModel returns for its model id, and that the last category holding
  // the id wins. These report what the running editor does, so the substitution can be written
  // against facts: which records exist, what findModel returns, and whether the signal-flow
  // resolver runs again when only the capture changes.
  if (profile.namProbe) {
    let budget = 260, dumped = false;
    const probe = payload => { if (budget-- > 0) send({type: 'toneTrace', reason: 'probe', ...payload}); };
    const text = p => { const rep = p.add(4).readPointer(); return rep.isNull() ? '' : rep.add(0x10).readPointer().readCString(); };
    const category = m => {
      if (m.isNull()) return null;
      const sub = m.add(0x30).readPointer(); if (sub.isNull()) return null;
      const cat = sub.add(0x14).readPointer();
      return cat.isNull() ? null : {id: cat.add(0x24).readS32(), name: text(cat.add(4))};
    };
    const model = m => m.isNull() ? null : {ptr: m.toString(), id: text(m.add(0x24)), category: category(m),
      stereo: m.add(0x70).readU8(), sub: m.add(0x30).readPointer().isNull() ? null : m.add(0x30).readPointer().add(0x0c).readS32()};
    const guard = (name, work) => { try { work(); } catch (error) { probe({stage: name + '-failed', error: String(error.message)}); } };
    function dumpCatalog(catalog) {
      const count = (o, first, end) => o.add(end).readU32() - o.add(first).readU32();
      const at = (o, data, first, i) => o.add(data).readPointer().add(4 * (o.add(first).readU32() + i)).readPointer();
      const found = [];
      for (let i = 0; i < count(catalog, 0x14, 0x18) && i < 64; i++) {
        const cat = at(catalog, 0x0c, 0x14, i);
        for (let j = 0; j < count(cat, 0x3c, 0x40) && j < 16; j++) {
          const sub = at(cat, 0x34, 0x3c, j);
          for (let k = 0; k < count(sub, 0x28, 0x2c) && k < 400; k++) {
            const record = at(sub, 0x20, 0x28, k);
            if (text(record.add(0x24)) === 'HD2_AmpNAMLite') found.push(model(record));
          }
        }
      }
      probe({stage: 'catalog', categories: count(catalog, 0x14, 0x18), nam: found});
    }
    Interceptor.attach(base.add(0x3c24e0), {
      onEnter(args) { guard('findModel', () => { this.catalog = this.context.ecx; this.id = text(args[0]); this.variant = args[1].toInt32(); }); },
      onLeave(result) {
        guard('findModel', () => {
          if (!dumped) { dumped = true; dumpCatalog(this.catalog); }
          if (this.id === 'HD2_AmpNAMLite') probe({stage: 'findModel', variant: this.variant, result: model(result),
            from: this.returnAddress.sub(base).toString()});
        });
      },
    });
    Interceptor.attach(base.add(0x269540), {
      onEnter(args) { guard('sfResolve', () => { this.object = args[0]; this.dsp = this.context.ecx.add(0x0c).readS32(); this.resolve = args[1].toInt32() & 0xff; }); },
      onLeave() {
        guard('sfResolve', () => {
          if (!this.object || this.object.isNull() || this.object.readS32() !== 0) return;
          const found = this.object.add(0x24).readPointer();
          if (!found.isNull() && text(found.add(0x24)) === 'HD2_AmpNAMLite') probe({stage: 'sfResolve', dsp: this.dsp,
            resolve: this.resolve, block: text(this.object.add(8)), object: this.object.toString(), model: model(found)});
        });
      },
    });
    Interceptor.attach(base.add(0x40ae70), {
      onEnter() {
        guard('itemRefresh', () => {
          const item = this.context.ecx;
          let object = item.add(0x8c).readPointer(); if (object.isNull()) object = item.add(0x88).readPointer();
          if (object.isNull() || object.readS32() !== 0) return;
          const found = object.add(0x24).readPointer();
          if (!found.isNull() && text(found.add(0x24)) === 'HD2_AmpNAMLite') probe({stage: 'itemRefresh', item: item.toString(),
            object: object.toString(), block: text(object.add(8)), model: model(found)});
        });
      },
    });
    Interceptor.attach(base.add(0x21e500), {
      onEnter() { this.selector = this.context.ecx; },
      onLeave() {
        guard('selectorSync', () => {
          const found = this.selector.add(0xa4).readPointer();
          if (!found.isNull() && text(found.add(0x24)) === 'HD2_AmpNAMLite') probe({stage: 'selectorSync',
            dsp: this.selector.add(0x80).readS32(), block: text(this.selector.add(0x84)), model: model(found)});
        });
      },
    });
  }

  Interceptor.attach(base.add(0x380940), {
    onEnter(args) {
      if (profile.navigation) {
        if (!profile.namCategory) return;
        if (!this.context.ecx.readPointer().equals(base.add(0x91fcd0)) || args[1].isNull() || args[5].isNull()) return;
        const header = args[1].readUtf8String();
        // The last stock category row in the category column (list kind 0): mark it afterwards.
        if (header === 'Looper' && this.context.ecx.add(0x1d8).readU32() === 0) { this.separator = true; return; }
        if (header.trimEnd() !== tiles[0].block) return;      // the NAM A2 Lite block row of any group
        this.captures = {header, list: this.context.ecx, image: args[2], index: args[4].toInt32(), model: args[5]};
        return;
      }
      if (!this.context.ecx.readPointer().equals(base.add(0x91fcd0)) || args[1].isNull() || args[5].isNull()) return;
      const tile = tileByBlock.get(args[1].readUtf8String());
      if (tile === undefined) return;
      this.tile = tile;
      this.list = this.context.ecx;
      this.image = args[2];
      this.index = args[4].toInt32();
      this.model = args[5];
    },
    onLeave(item) {
      if (this.separator) {
        // HX Edit's own list rows can draw a one-pixel line along their bottom edge (item flags
        // bit 1 at +0x114, drawn in the row's text colour across the whole row, icon column
        // included). That is the divider before the NAM categories: no row of its own, nothing
        // to hover or choose. The list is rebuilt often; this runs each time the row is added.
        try { if (!item.isNull()) item.add(0x114).writeU32(item.add(0x114).readU32() | 2); }
        catch (error) { send({type: 'toneTrace', reason: 'nam-category', stage: 'separator-skipped', error: String(error.message)}); }
        return;
      }
      if (this.captures) return appendCaptureRows(this.captures);
      if (this.list) appendRow(this.list, -1, tileLabels[this.tile], this.image, 0, this.index, this.model, 0, 0);
    },
  });

  function onModelEvent(controller, event, sender, data, flags) {
    if (profile.navigation) {
      if (profile.namCategory && event === selectionEvent && !sender.isNull()) {
        let entry = null;
        try {
          if (sender.equals(controller.add(0x58).readPointer())) {       // the model column
            const row = selectedRow(sender);
            entry = row.isNull() ? null : captureRows.get(row.readUtf8String()) || null;
          }
        } catch (_) { entry = null; }
        if (entry) return chooseCapture(controller, sender, entry);
      }
      modelEvent(controller, event, sender, data, flags);
      observeController(controller);
      if (event === selectionEvent) refreshBounds(true);
      return;
    }
    if (event !== selectionEvent || sender.isNull() || !sender.equals(controller.add(0x58).readPointer())) {
      modelEvent(controller, event, sender, data, flags);
      if (event === selectionEvent || event === 0x21617663) ensureEntry(controller);
      return;
    }
    const row = selectedRow(sender);
    const name = row.isNull() ? null : row.readUtf8String();
    const tileIndex = name === null ? undefined : tileByLabel.get(name);
    if (active && name !== null && (tileIndex !== undefined || active.controller.equals(controller))) {
      // Another model or tile while TONE3000 is open: close it first (undoing the audition), then pick that row.
      clearSelection(sender, 0, 0);
      active.pending = { controller, list: sender, name };
      dismiss();
      return;
    }
    if (tileIndex === undefined) {
      if (name !== null) lastModel.set(controller.toString(), name);   // the model the user picked
      return modelEvent(controller, event, sender, data, flags);
    }
    const tile = tiles[tileIndex];
    clearSelection(sender, 0, 0);
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
      block: block(controller), window, valid: true, panel, layout: JSON.stringify(bounds),
      tile, model: lastModel.has(controller.toString()) ? lastModel.get(controller.toString()) : null };
    namView.setBrowserSessionActive?.(true);
    active.selectionUndo = undoIndex();
    send({ type: 'toneTrace', reason: 'tile', tile: tile.block, model: active.model, block: active.block });
    // the block's profile name: a TONE3000 one ends with its model ID, which TonePreview opens the tone of
    send({ type: 'toneStart', session: active.id, window: window.toUInt32(), bounds, tile,
      profile: namView.slotName(namView.modelSlot()) });
  }
  Interceptor.replace(eventAddress, new NativeCallback(onModelEvent, 'void', eventTypes, 'thiscall'));

  function resume(pending) {
    // the row clicked while TONE3000 was open, now that it has closed
    try {
      if (!pending.list.equals(pending.controller.add(0x58).readPointer()) || !pick(pending.list, pending.name)) return;
      onModelEvent(pending.controller, selectionEvent, pending.list, NULL, NULL);
    } catch (error) {
      send({ type: 'toneTrace', reason: 'resume', error: String(error.message) });
    }
  }

  Interceptor.attach(base.add(0x216e00), {
    onEnter() {
      if (selectedController.equals(this.context.ecx)) selectedController = NULL;
      if (active && active.controller.equals(this.context.ecx)) {
        if (!active.navigation) active.panel = NULL;
        if (active.navigation) send({type:'toneTrace',reason:'navigation-controller-destroyed',
          preview:!!active.transfer,binding:!!active.nativeTarget?.valid});
        // A parameter view is disposable; its edit-buffer block is not. Keep
        // the typed DSP/block binding alive so cleanup can restore that block
        // even when selection destroys/replaces its old view controller.
        // Preset, snapshot and device events separately invalidate the context.
        invalidate('view-closed');
      }
    },
  });

  Interceptor.attach(base.add(0x218a00), {
    onEnter(args) {
      this.controller = this.context.ecx;
      const event = this.event = stringValue(args[0]);
      this.ours = active && active.controller.equals(this.context.ecx);
      // A save with no preview playing stores nothing of ours: it must not block the next preview.
      if (this.ours && event === 'EditBuffer_PresetSavedToDevice') active.deviceSaved = !!active.transfer;
      if (!contextEvent.test(event)) return;
      if (!profile.navigation) {
        if (this.ours) invalidate(event);
        return;
      }
      if (event === 'EditBuffer_HelixSnapshotWillChange' && active?.navigation && active.transfer &&
          active.nativeTarget?.valid && active.context === contextVersion && !active.deviceSaved) {
        // A snapshot recall keeps the preset's blocks, so the typed DSP/block binding still
        // addresses the previewed block. End the preview on it once the recall has settled
        // (restore writes only while that block still reads 128) instead of stranding it.
        settleCleanup(SNAPSHOT_SETTLE_MS);
        invalidate('snapshot-changed');
        return;
      }
      contextVersion++;
      releaseTarget();
      // Every open parameter view receives these. Invalidate once per change, whichever arrives.
      if (active?.navigation && (active.bound || active.valid || (active.transfer && !active.cleanupRequested))) {
        if (active.transfer) settleCleanup(CONTEXT_SETTLE_MS);
        invalidate(event);
      }
    },
    onLeave() {
      if (profile.navigation) observeController(this.controller);
      if (profile.navigation && this.event === 'EditBuffer_HelixCursorChange') refreshBounds(true);
      // (Not while inserting or restoring a block: the cell's position changes when it fills or empties.)
      if (this.ours && active && (active.valid || active.bound) && !active.applying && !active.inserting && !active.restoring && !atTarget()) {
        invalidate(`moved after ${this.event}`);
      }
    },
  });

  function reply(request, result) {
    if (workflowRequest && workflowRequest.id === request.id && workflowRequest.session === request.session) {
      workflowRequest = null;
      namView.setWorkflowBusy?.(false);
    }
    if (active?.navigation && !result.error && ['audition', 'save', 'load', 'use'].includes(request.action)) publishTarget();
    if (request.action === 'autoEnd') send({type:'toneTrace', reason:'automatic-preview-cleanup', ended:!!result.ended, retained:!!result.retained, error:result.error || null});
    else send({ type: 'toneReply', id: request.id, session: request.session, ...result });
    if ((active?.cleanupRequested || active?.queuedClose) && !workflowRequest) post(active.window, wake, 0, 0);
  }

  function finishTransfer(transfer) {
    if (toneImport !== transfer) return;
    toneImport = null;
    if (namImportController.equals(transfer.controller)) namImportController = NULL;
  }

  function poll(request, next, timeout = 12000, finishImport = false) {
    const deadline = Date.now() + timeout;
    function check() {
      if (!active || request.session !== active.id) return;
      command = { request, next, deadline, check, finishImport };
      if (!post(active.window, wake, 0, 0)) {
        command = null;
        reply(request, { error: 'HX Edit closed during the TONE3000 operation.' });
      }
    }
    check();
  }

  function awaitImport(request, transfer, imported) {
    if (active.navigation) active.pendingTransfer = transfer;
    poll(request, expired => {
      const stale = active.navigation && request.targetVersion !== active.targetVersion;
      if (transfer.error) {
        finishTransfer(transfer);
        if (active.navigation) active.pendingTransfer = null;
        throw new Error(transfer.error);
      }
      if (!namView.previewName(transfer)) {
        if (!expired && (active.navigation || request.action === 'save' || atTarget())) return null;
        finishTransfer(transfer);
        if (active.navigation) { active.orphanTransfer = transfer; active.pendingTransfer = null; }
        throw new Error(`HX Edit did not confirm the import. Check User IR ${transfer.slot} before retrying.`);
      }
      finishTransfer(transfer);
      if (active.navigation) active.pendingTransfer = null;
      if (stale) {
        if (request.action === 'audition' && transfer.slot === 128 && active.nativeTarget?.valid && active.context === contextVersion) {
          // Selection changed before applying a completed upload. The old block
          // still has its original capture; clean our owned128 automatically.
          active.transfer = transfer;
          active.origin = active.origin || {slot:active.nativeTarget.originalSlot,structural:false};
          active.cleanupRequested = true;
        } else {
          active.orphanTransfer = transfer;
          if (active.transfer && active.nativeTarget?.valid && active.context === contextVersion) active.cleanupRequested = true;
        }
        throw new Error(`The target changed during upload. User IR ${transfer.slot} was retained; no block was changed.`);
      }
      imported();
      return {};
    }, 12000, true);
  }

  function applyProfile(request, transfer) {
    // Put an imported audition profile on the block. The first one records the block's own state
    // (active.origin) for Close and, on an empty cell or another NAM model, inserts the tile's block.
    // Later ones only move the block to their slot: nothing is undone between tones, so an inserted
    // block stays. The previous audition's slot is then cleared. (No Undo-count checks: HX Edit adds
    // Undo steps of its own a moment after a Model change.)
    if (!atTarget()) {
      throw targetChanged('apply', `The target changed. The downloaded profile is in User IR ${transfer.slot}.`);
    }
    const previous = active.transfer;
    active.transfer = transfer;
    if (!active.origin) {
      const slot = namView.modelSlot();
      // An empty cell, or a NAM block known to be another model than the tile's. Unknown: left as it is
      // (re-selecting the same model makes HX Edit add an Undo step later).
      active.origin = { undo: undoIndex(), slot, block: active.block, position: active.position,
        structural: slot === null || (active.model !== null && active.model !== active.tile.block) };
      active.inserting = active.origin.structural;
    }
    const undoBefore = undoIndex();
    const deadline = Date.now() + 1500;
    if (active.inserting) insertNam();
    let selecting = false;
    let expectedUndo;
    poll(request, expired => {
      if (!(active.inserting ? active.valid : atTarget())) {
        throw targetChanged('applying', `The target changed. The downloaded profile is in User IR ${transfer.slot}.`);
      }
      if (namView.modelSlot() === null && !expired) return null;
      if (active.inserting) {
        // Inserting into an empty cell gives it a block ID after the native edit. A NAM block that already
        // was the tile's model records nothing: accepted once the deadline passes.
        const same = active.origin.slot !== null && Date.now() >= deadline;
        if (undoIndex() === undoBefore && !expired && !same) return null;
        if (undoIndex() === undoBefore && !same) {
          throw new Error(`HX Edit did not confirm the new block. Check User IR ${transfer.slot}.`);
        }
        active.block = block(active.controller);
        active.position = position(active.controller);   // a filled cell has a new position
        active.inserting = false;
      }
      if (!selecting) {
        expectedUndo = undoIndex() + Number(namView.modelSlot() !== transfer.slot);
        selecting = namView.playPreview(transfer);
      }
      // HX Edit can update the Model widget before recording the edit for Undo.
      if (!selecting || namView.modelSlot() !== transfer.slot || undoIndex() < expectedUndo) {
        if (!expired) return null;
        throw new Error(`Could not apply the profile. Check User IR ${transfer.slot} on Helix.`);
      }
      if (previous && previous !== transfer) namView.clearPreview(previous);
      active.model = active.tile.block;      // the block is the tile's model now
      active.undoAfter = undoIndex();
      rememberPreview();
      return { slot: transfer.slot, freeSlots: namView.freeSlots(transfer.slot) };
    }, 3500);
  }

  function playing(transfer) {
    return transfer && atTarget() && active.undoAfter !== undefined &&
      namView.modelSlot() === transfer.slot && !!namView.previewName(transfer);
  }

  function restore(request) {
    // Close: the block back to active.origin, then the audition slot cleared. A slot change alone is set
    // back as a new edit; an inserted block or a model switch is undone back to origin.undo.
    const { transfer, editor, origin } = active;
    if (!transfer || !origin) return afterRestore(request, {});
    // The block could not be brought back: no message (trace.log has why). The audition slot is cleared
    // when the block does not play it; otherwise it stays, as the block's profile.
    const keep = reason => {
      const state = { type: 'toneTrace', reason, action: request.action,
        origin: { undo: origin.undo, slot: origin.slot, structural: origin.structural }, slot: transfer.slot,
        valid: active.valid, invalidatedBy: active.invalidatedBy || null, deviceSaved: !!active.deviceSaved };
      try {
        Object.assign(state, { undo: undoIndex(), modelSlot: namView.modelSlot(),
          samePosition: position(active.controller) === active.position,
          originPosition: position(active.controller) === origin.position,
          sameBlock: block(active.controller) === active.block, originBlock: block(active.controller) === origin.block,
          preview: !!namView.previewName(transfer) });
        if (!active.navigation && namView.modelSlot() !== transfer.slot) namView.clearPreview(transfer);
      } catch (error) {
        state.readError = String(error.message);
      }
      send(state);
      return afterRestore(request, active.navigation ? {
        notice: 'Preview remains in User IR 128. Return to the original NAM block and choose End preview.',
        retained: true } : {});
    };
    const clearAudition = () => {
      if (!namView.clearPreview(transfer)) return keep('clear-refused');
      namView.stopPreview();
      poll(request, expired => {
        if (namView.previewSlotEmpty(transfer)) {
          active.transfer = null;
          return afterRestore(request, {});
        }
        return expired ? afterRestore(request, { retained: true,
          notice: 'Preview cleanup was not confirmed. Check User IR128 before another audition.' }) : null;
      }, 3500);
      return {};
    };
    if (!playing(transfer) || active.deviceSaved) return keep('not-restorable');
    active.restoring = true;
    if (!origin.structural) {
      if (!namView.setSlot(origin.slot)) return keep('slot-refused');
    } else {
      const undo = new NativeFunction(editor.readPointer().add(0xdc).readPointer(), 'int', ['pointer'], 'thiscall');
      active.applying = true;
      try {
        for (let step = 0; step < 8 && undoIndex() > origin.undo; step++) {
          if (undo(editor) < 0) break;
        }
      } finally {
        active.applying = false;
      }
      if (undoIndex() !== origin.undo) return keep('undo-failed');
    }
    poll(request, expired => {
      if (!active.valid) return keep('moved-after-undo');
      if (namView.modelSlot() !== origin.slot || block(active.controller) !== origin.block) {
        return expired ? keep('original-not-back') : null;
      }
      active.block = origin.block;
      active.position = position(active.controller);    // an emptied cell is back at its old position
      active.restoring = false;
      return clearAudition();
    }, 3500);
  }

  const commandAddress = base.add(0x35e800);
  const dispatch = new NativeFunction(commandAddress, 'uint8', ['pointer', 'pointer'], 'thiscall');
  Interceptor.replace(commandAddress, new NativeCallback(function (app, event) {
    if (active && active.transfer && !event.isNull() && event.readU32() === 0x1b) {
      messageBox(active.window, Memory.allocUtf16String(active.navigation ?
        'A preview is playing from User IR 128. Choose Load on Floor to keep this capture, or End preview, before saving the preset.' :
        'Use Save to Helix in TONE3000 before saving a preset with an audition profile.'), title, 0x40);
      return 0;
    }
    return dispatch(app, event);
  }, 'uint8', ['pointer', 'pointer'], 'thiscall'));

  function afterRestore(request, result) {
    // Close finished (or could not bring the block back, see keep): end the session.
    active.restoring = false;
    namView.stopPreview();
    if (active.navigation && request.action !== 'close') {
      active.cleanupRequested = false;
      active.bound = false;
      active.valid = false;
      active.targetVersion = ++targetVersion;
      active.quarantined = !!result.retained;
      if (!result.retained) {
        active.transfer = null;
        active.origin = null;
        active.undoAfter = undefined;
        releaseTarget();
      }
      publishTarget(result.notice);
      reply(request, { ...result, ended: !result.retained });
      return {};
    }
    const pending = active.pending;
    if (active.navigation) releaseTarget();
    namView.setBrowserSessionActive?.(false);
    active = null;
    reply(request, result);
    if (pending) resume(pending);
    return {};
  }

  function cleanupTrace(request, stage, observedSlot = null, reason = null) {
    send({type:'toneTrace', reason:'preview-cleanup', stage, session:active.id,
      action:request.action, source:request.reason || (request.action === 'autoEnd' ? active.invalidatedBy : request.action),
      originalSlot:active.origin?.slot ?? null, previewSlot:active.transfer?.slot ?? null,
      observedSlot, selectedSlot:atTarget() ? namView.modelSlot() : null,
      dsp:active.nativeTarget?.dsp ?? null, block:active.nativeTarget?.blockName ?? null,
      sameContext:active.context === contextVersion, rejection:reason});
  }

  function restoreOwnedPreview(request, completed) {
    const {transfer, origin, nativeTarget} = active;
    const retained = (reason, message, observed = null) => {
      cleanupTrace(request, 'retained', observed, reason);
      return afterRestore(request, {retained:true, notice:message,
        ...(!['end','close','autoEnd'].includes(request.action) ? {error:message} : {})});
    };
    if (!transfer || !origin) return completed();
    cleanupTrace(request, 'requested');
    if (active.deviceSaved) {
      return retained('preset-saved', 'The preset was saved while this preview was playing, so the saved preset uses User IR 128. Nothing was restored or cleared.');
    }
    if (!nativeTarget?.valid || active.context !== contextVersion) return releaseDetachedPreview(request, completed, retained);
    if (!namView.previewName(transfer)) return retained('ownership-changed', 'Preview slot ownership changed. Nothing was overwritten or cleared.');
    try {
      const current = targetBinding.read(nativeTarget, {context:contextVersion});
      cleanupTrace(request, 'observed', current.slot);
      if (current.slot === transfer.slot) {
        let accepted;
        active.applying = true;
        try {
          // While the previewed block's own parameter view is on screen, restore through its
          // Model control, the way the preview was applied. A typed edit-buffer write does not
          // refresh that control: in the Candidate58 owner trial the block read its original
          // slot again while the control stayed on 128, and cleanup gave up. Any other block is
          // addressed by its typed binding and never through the control on screen.
          accepted = (targetOnScreen() && namView.modelSlot() === transfer.slot && namView.setSlot(origin.slot)) ||
            targetBinding.restore(nativeTarget, origin.slot, {context:contextVersion, expectedSlot:128});
        } finally { active.applying = false; }
        if (!accepted) {
          return retained('restore-rejected', 'The previous block rejected restoration. Preview remains in User IR 128.');
        }
        cleanupTrace(request, 'restore-accepted', current.slot);
      } else if (current.slot !== origin.slot) {
        return retained('block-edited', 'The original block was edited independently. Preview remains in User IR 128 for review.', current.slot);
      }
    } catch (error) { return retained('native-read-or-write-failed', String(error.message)); }
    active.restoring = true;
    let clearing = false, refreshed = false;
    poll(request, expired => {
      try {
        if (!nativeTarget.valid || active.context !== contextVersion || active.deviceSaved) {
          return retained('context-changed', 'The target context changed during cleanup. Preview was retained.');
        }
        const current = targetBinding.read(nativeTarget, {context:contextVersion});
        if (current.slot === origin.slot && !clearing && !refreshed && targetOnScreen() &&
            namView.modelSlot() === transfer.slot) {
          // The block is restored but its control on screen still shows the preview (it came
          // back on screen after a typed restore). Bring the control to the same original slot.
          refreshed = true;
          active.applying = true;
          try { namView.setSlot(origin.slot); }
          finally { active.applying = false; }
          return null;
        }
        if (current.slot !== origin.slot || (targetOnScreen() && namView.modelSlot() !== origin.slot)) {
          if (!clearing && (current.slot === transfer.slot || current.slot === origin.slot) && !expired) return null;
          return retained('restore-not-confirmed', 'The previous block did not confirm its original capture. Preview was retained.', current.slot);
        }
        if (!clearing) {
          cleanupTrace(request, 'native-model-confirmed', current.slot);
          if (!namView.previewName(transfer) || !namView.canClearPreview(transfer) || !namView.clearPreview(transfer)) {
            return retained('clear-refused', 'User IR 128 is still referenced or could not be checked. The original block is restored; the slot was retained.', current.slot);
          }
          clearing = true;
        }
        if (!namView.previewSlotEmpty(transfer)) {
          return expired ? retained('clear-not-confirmed', 'The original block is restored; clearing User IR 128 was not confirmed.', current.slot) : null;
        }
        cleanupTrace(request, 'completed', current.slot);
        namView.stopPreview();
        active.transfer = null;
        active.restoring = false;
        forgetPreview();
        return completed();
      } catch (error) { return retained('native-read-or-clear-failed', String(error.message)); }
    }, 3500);
  }

  function releaseDetachedPreview(request, completed, retained) {
    // The preset, device or signal flow changed under the preview, so the typed binding can no
    // longer address its block. Two things remain safe:
    //  - clear User IR 128 once it is still ours by name and the current preset does not use it
    //    (the usual case after switching to another preset: the unsaved preview edit went with
    //    the old one);
    //  - on an explicit End or Close with a NAM block selected that plays 128, set that block
    //    back to the original capture first. Only blocks that read 128 can be playing our preview.
    const {transfer, origin} = active;
    const explicit = request.action === 'end' || request.action === 'close';
    const use = () => namView.previewUse ? namView.previewUse(transfer) :
      namView.canClearPreview(transfer) ? 'unused' : 'unknown';
    const adoptable = () => {
      const selected = selectedTarget();
      return explicit && !!selected && selected.slot === transfer.slot && !origin.structural &&
        Number.isInteger(origin.slot) && origin.slot >= 1 && origin.slot <= 127;
    };
    let adopting = false, clearing = false;
    active.restoring = true;
    poll(request, expired => {
      let finished = false;
      try {
        if (!namView.previewName(transfer)) {
          if (!namView.previewSlotEmpty(transfer)) {
            return retained('ownership-changed', 'Preview slot ownership changed. Nothing was overwritten or cleared.');
          }
          finished = true; // already cleared elsewhere
        } else if (!clearing) {
          const state = use();
          if (state !== 'unused') {
            if (state === 'used' && !adopting && adoptable()) {
              cleanupTrace(request, 'restore-selected', transfer.slot);
              active.applying = true;
              try { adopting = namView.setSlot(origin.slot); }
              finally { active.applying = false; }
              if (!adopting) return retained('restore-rejected', 'The selected block rejected restoration. Preview remains in User IR 128.');
              return null;
            }
            if (!expired) return null;
            return retained(state === 'used' ? 'still-referenced' : 'clear-refused', state === 'used' ?
              'The preset changed while this preview was playing and still uses User IR 128. Select the NAM block that plays it, then choose End preview.' :
              'The preset changed while this preview was playing. User IR 128 could not be checked, so it was kept.');
          }
          if (!namView.clearPreview(transfer)) {
            return retained('clear-refused', 'User IR 128 could not be cleared after the preset changed. It was kept.');
          }
          clearing = true;
          return null;
        } else if (!namView.previewSlotEmpty(transfer)) {
          return expired ? retained('clear-not-confirmed', 'Clearing User IR 128 was not confirmed after the preset changed.') : null;
        } else finished = true;
      } catch (error) { return retained('native-read-or-clear-failed', String(error.message)); }
      if (!finished) return null;
      cleanupTrace(request, 'completed-detached');
      namView.stopPreview();
      active.transfer = null;
      active.restoring = false;
      forgetPreview();
      return completed();
    }, explicit ? 6000 : 4000);
  }

  function restoreBoundPreview(request) {
    return restoreOwnedPreview(request, () => {
        const permanent = active.orphanTransfer?.slot;
        if (permanent && permanent !== 128) {
          active.orphanTransfer = null;
          return afterRestore(request, {notice:`Preview ended. The completed library upload remains in User IR ${permanent}.`});
        }
        return afterRestore(request, {});
    });
  }

  function audition(request) {
    // A free User IR slot (128 first); none free: the slot the user chose to replace (request.replace),
    // cleared first, or an error listing the taken slots so TonePreview can ask.
    const load = slot => {
      active.previewSlot = slot;
      const transfer = namView.importPreview(request.file, slot, true);
      awaitImport(request, transfer, () => applyProfile(request, transfer));
    };
    if (profile.strictPreview) {
      const previous = active.transfer;
      const plan = floorPreviewPlan({ atTarget: atTarget(), deviceSaved: !!active.deviceSaved,
        hasTransfer: !!previous, playing: playing(previous),
        ownsPreview: !!previous && !!namView.previewName(previous), previewSlot: previous?.slot,
        origin: active.origin, sameModel: active.model === active.tile.block,
        modelSlot: namView.modelSlot(), empty128: namView.slotEmpty(AUDITION_SLOT) });
      if (plan.kind === 'first') {
        if (active.navigation) captureTarget();
        if (!plan.replace) return load(plan.slot);
        if (!namView.clearSlot(AUDITION_SLOT)) throw new Error('User IR 128 could not be cleared for the preview.');
        poll(request, expired => {
          if (!namView.slotEmpty(AUDITION_SLOT)) {
            if (!expired) return null;
            throw new Error('User IR 128 could not be cleared for the preview. Nothing was uploaded.');
          }
          if (!atTarget()) throw new Error('The selected NAM target changed; no preview was uploaded.');
          load(AUDITION_SLOT);
          return {};
        }, 3500);
        return;
      }

      // Release only this session's owned preview, after the selected block has
      // returned to its permanent capture. Direct Model edits retain Input/EQ;
      // no Undo and no unknown-slot replacement is used between auditions.
      if (active.navigation) {
        return restoreOwnedPreview(request, () => {
          if (!atTarget() || request.targetVersion !== active.targetVersion) {
            throw new Error('The selected NAM target changed; no replacement preview was uploaded.');
          }
          load(AUDITION_SLOT);
          return {};
        });
      }
      if (!namView.setSlot(plan.originalSlot)) throw new Error('Could not restore the original capture; preview was retained.');
      let clearing = false;
      poll(request, expired => {
        if (!atTarget() || active.deviceSaved) throw targetChanged('preview-reuse', 'The target changed; preview replacement stopped.');
        if (namView.modelSlot() !== plan.originalSlot) {
          if (!expired) return null;
          throw new Error('Original capture was not confirmed; preview slot was retained.');
        }
        if (!clearing) {
          if (!namView.clearPreview(previous)) throw new Error('Preview ownership changed; nothing was cleared.');
          clearing = true;
        }
        if (!namView.previewSlotEmpty(previous)) {
          if (!expired) return null;
          throw new Error('Preview cleanup was not confirmed; no new capture was uploaded.');
        }
        namView.stopPreview();
        active.transfer = null;
        load(AUDITION_SLOT);
        return {};
      }, 3500);
      return;
    }
    const free = namView.auditionSlot();
    if (free) return load(free);
    const slot = request.replace;
    if (!Number.isInteger(slot) || slot < 1 || slot > AUDITION_SLOT) {
      const error = new Error('No free User IR slot. Choose one to replace.');
      error.replace = namView.usedSlots();
      throw error;
    }
    if (!namView.clearSlot(slot)) throw new Error(`Could not clear User IR ${slot}.`);
    poll(request, expired => {
      if (!namView.slotEmpty(slot)) {
        if (!expired) return null;
        throw new Error(`Could not clear User IR ${slot}.`);
      }
      load(slot);
      return {};
    }, 3500);
  }

  function keepSaved(request, trial, saved) {
    // Save keeps what the audition did to the block (inserted it, switched its model) and only moves it
    // from the audition slot to the saved one. No Undo: undoing the audition first could take the block
    // back out, and the re-insert after it could fail. Then the audition slot is cleared.
    if (active.navigation && (!atTarget() || request.targetVersion !== active.targetVersion)) {
      throw new Error(`The target changed. The saved capture remains in User IR ${saved.slot}; no block was changed.`);
    }
    const expected = undoIndex() + 1;
    if (!namView.selectProfile(saved)) throw new Error(`Check User IR ${saved.slot} on Helix.`);
    if (active.navigation) {
      // Save explicitly keeps this capture. If selection changes while cleanup
      // completes, do not restore the capture that preceded the saved preview.
      active.origin = {slot:saved.slot,block:active.block,position:active.position,structural:false};
    }
    let clearing = false;
    poll(request, expired => {
      // HX Edit can update the Model widget before recording the edit for Undo.
      if (namView.modelSlot() !== saved.slot || undoIndex() < expected) {
        if (!expired) return null;
        throw new Error(`Could not switch the block to User IR ${saved.slot}. Check it on Helix.`);
      }
      if (!clearing && !namView.clearPreview(trial)) {
        if (active.navigation) {
          active.quarantined = true;
          active.origin = {slot:saved.slot,block:active.block,position:active.position,structural:false};
          captureTarget();
          publishTarget('Capture saved; preview slot 128 is still referenced and was retained.');
        }
        return {savedSlot:saved.slot, retained:true,
          notice:'Capture saved, but preview slot 128 is still referenced or could not be checked. It was not cleared.', freeSlots:[]};
      }
      clearing = true;
      if (!namView.previewSlotEmpty(trial)) {
        if (!expired) return null;
        throw new Error(`Capture saved in User IR ${saved.slot}, but preview cleanup was not confirmed.`);
      }
      namView.stopPreview();
      active.transfer = null;
      forgetPreview();                         // the block is on its saved slot: nothing is left to recover
      active.origin = null;                    // the saved profile is what Close keeps from now on
      active.undoAfter = undefined;
      active.model = active.tile.block;        // the block keeps the tile's model now
      active.selectionUndo = undoIndex();
      if (active.navigation) captureTarget();
      return { savedSlot: saved.slot, freeSlots: namView.freeSlots(trial.slot) };
    }, 3500);
  }

  function applyPermanent(request, slot, transfer) {
    if (!atTarget() || request.targetVersion !== active.targetVersion) throw new Error('The NAM target changed before applying the capture.');
    const expectedUndo = undoIndex() + Number(namView.modelSlot() !== slot);
    if (transfer && !namView.registerProfile(transfer)) throw new Error(`The capture remains in User IR ${slot}; registration was not confirmed.`);
    if (!(transfer ? namView.selectProfile(transfer) : namView.setSlot(slot))) throw new Error('Could not select the capture on this NAM block.');
    poll(request, expired => {
      if (!atTarget()) throw new Error(`The target changed. Check User IR ${slot}; no other block was selected by the browser.`);
      if (namView.modelSlot() !== slot || undoIndex() < expectedUndo) {
        if (!expired) return null;
        throw new Error(`Could not confirm Model ${slot}. Check the selected NAM block.`);
      }
      active.model = active.tile.block;
      active.selectionUndo = undoIndex();
      captureTarget();
      active.onPreviewSlot = false; // it has a permanent slot now, so it can be previewed on
      return transfer ? {loadedSlot:slot} : {usedSlot:slot};
    }, 3500);
  }

  function installPermanent(request) {
    if (active.transfer || active.pendingTransfer || active.orphanTransfer) throw new Error('End the current preview before loading a library capture.');
    if (typeof request.identity !== 'string' || !/^[a-f0-9]{64}$/.test(request.identity)) throw new Error('Invalid library capture identity.');
    const existing = namView.verifiedCaptureSlot(request.identity);
    if (existing) return applyPermanent(request, existing, null);
    if (request.action === 'use') throw new Error('The installed slot is no longer verified. Wait for verification, then try again.');
    const slot = namView.freeSlots(AUDITION_SLOT).find(value => value >= 1 && value <= 127);
    if (!slot) throw new Error('No empty permanent User IR slot is available. Preview slot 128 is reserved.');
    const transfer = namView.importPreview(request.file, slot, false);
    awaitImport(request, transfer, () => applyPermanent(request, slot, transfer));
  }

  function run(request) {
    if (active.navigation && request.action !== 'bind') {
      workflowRequest = {id:request.id, session:request.session};
      namView.setWorkflowBusy(true); // no new automatic export may race this operation
      if (namView.inventoryBusy()) {
        poll(request, expired => {
          if (namView.inventoryBusy()) {
            if (!expired) return null;
            throw new Error('The current device export has not finished. No new edit was sent.');
          }
          run(request);
          return {};
        }, 12000);
        return;
      }
    }
    if (active.navigation) {
      if (request.action === 'bind') return bindSelected(request);
      if (request.action === 'end' || request.action === 'close' || request.action === 'autoEnd') {
        if (active.orphanTransfer && !active.transfer && active.orphanTransfer.slot !== 128) {
          const slot = active.orphanTransfer.slot;
          active.orphanTransfer = null;
          return afterRestore(request, {notice:`No preview is active. The completed library upload remains in User IR ${slot}.`});
        }
        if (active.orphanTransfer && !active.transfer) return afterRestore(request, { retained: true,
          notice: `Upload retained in User IR ${active.orphanTransfer.slot} after the target changed. No new target was modified.` });
        if (!active.transfer) return afterRestore(request, {});
        return restoreBoundPreview(request);
      }
      if (!active.bound || active.quarantined || !atTarget() || request.targetVersion !== active.targetVersion) {
        throw new Error('The selected NAM target changed. Select the tone again for the current block.');
      }
    }
    if (request.action === 'save') {
      const trial = active.transfer;
      if (!playing(trial) || active.deviceSaved) {
        throw new Error('The audition changed. Reopen TONE3000 on the desired block.');
      }
      const { slot } = request;
      if (!Number.isInteger(slot) || slot < 1 || slot > (profile.lastPermanentSlot || AUDITION_SLOT) || slot === active.previewSlot) {
        throw new Error('Select a different free User IR slot.');
      }
      const saved = namView.importPreview(trial.source, slot);
      awaitImport(request, saved, () => {
        if (!namView.registerProfile(saved)) throw new Error(`Check User IR ${slot} on Helix.`);
        request.savedSlot = slot;
        keepSaved(request, trial, saved);
      });
      return;
    }
    if (active.navigation && (request.action === 'load' || request.action === 'use')) return installPermanent(request);
    if (request.action === 'audition') {
      if (!atTarget()) throw targetChanged('run', 'The target changed. Select TONE3000 again on the desired block.');
      return audition(request);         // the next tone replaces the current audition: nothing is undone
    }
    restore(request);                   // close
  }

  Interceptor.attach(user32.getExportByName('DispatchMessageW'), {
    onEnter(args) {
      if (leftover) recoverLeftover();
      if (pendingCapture) finishPendingCapture();
      if (profile.namCategory) watchNamIcons(args[0].readPointer());
      if (!active) return;
      const hwnd = args[0].readPointer();
      const event = args[0].add(4).readU32();
      // Keyboard focus can remain in HX Edit while a docked browser is visible.
      // Observe only Escape dispatched to this editor or one of its children;
      // never hook global input or intercept another application's keys.
      if (active.navigation && event === 0x100 && args[0].add(8).readU32() === 0x1b &&
          (hwnd.equals(active.window) || isChild(active.window, hwnd))) dismiss('escape-native');
      if (!hwnd.equals(active.window)) return;
      if (event === wake && active.abandoned) return abandonSession();
      if (event === 5 || event === 0xf || event === 0x47 || event === 0x202 || event === wake) {
        this.layoutSession = active.id;
        this.forceLayout = event === 5 || event === 0x47 || event === 0x202;
      }
      if (!command && !workflowRequest && event === wake && active.queuedClose) {
        // A Close that arrived during another workflow. It performs the same restoration as the
        // automatic cleanup and then ends the session, so it replaces a pending automatic one.
        command = {request: active.queuedClose};
        active.queuedClose = null;
      }
      if (!command && !workflowRequest && event === wake && active.cleanupRequested) {
        if (Date.now() >= (active.settleUntil || 0)) {
          command = {request:{session:active.id,id:--nextAutomaticRequest,action:'autoEnd'}};
        } else if (!active.settling) {
          const session = active;
          session.settling = true;
          setTimeout(() => {
            session.settling = false;
            if (active === session) post(session.window, wake, 0, 0);
          }, 100);
        }
      }
      if (!command || event !== wake) return;
      const { request, next, deadline, check, finishImport } = command;
      command = null;
      try {
        if (!next) return run(request);
        if (active.navigation && !finishImport && !['close', 'end', 'bind', 'autoEnd'].includes(request.action) &&
            request.targetVersion !== active.targetVersion) throw new Error('The NAM target changed; pending work was stopped. Check User IR128.');
        const result = next(Date.now() >= deadline);
        if (result === null) setTimeout(check, 75);
        else if ('slot' in result || 'savedSlot' in result || 'loadedSlot' in result || 'usedSlot' in result) reply(request, result);
      } catch (error) {
        const result = { error: String(error.message) };
        if (error.replace) result.replace = error.replace;
        if (request.action !== 'close') {
          const transfer = active.transfer;
          if (playing(transfer)) result.slot = transfer.slot;
          result.freeSlots = namView.freeSlots(active.previewSlot);
          if (request.savedSlot) result.savedSlot = request.savedSlot;
          if (active.navigation) {
            if (request.action === 'autoEnd') active.cleanupRequested = false;
            active.quarantined = !!active.transfer || !!active.orphanTransfer || !!toneImport;
            publishTarget(result.error);
          }
        } else if (active.navigation) {
          // The helper has closed. Do not leave an active ID pointing at a dead
          // browser, or execute cleanup through a busy native export queue.
          namView.abandonPreview();
          releaseTarget();
          namView.setBrowserSessionActive?.(false);
          active = null;
          result.notice = result.error + ' Any unconfirmed preview was retained; check User IR 128 before auditioning again.';
          delete result.error;
        }
        reply(request, result);
      }
    },
    onLeave() {
      if (!active || active.id !== this.layoutSession) return;
      autoBindSelected();
      refreshBounds(this.forceLayout === true);
    },
  });

  function listen() {
    recv('toneCommand', message => {
      const request = message.payload;
      const current = active && active.id === request.session;
      if (current && !command && (!active.navigation || !workflowRequest)) {
        command = { request };
        if (!post(active.window, wake, 0, 0)) {
          command = null;
          reply(request, { error: 'The HX Edit window is unavailable.' });
        }
      } else if (current && active.navigation && request.action === 'close' && !active.queuedClose) {
        // Escape/Close during another workflow (for example the automatic cleanup after a block
        // change). Rejecting it would end the supervisor's session while this one stays open, and
        // Browse TONE3000 would then do nothing. Run it as soon as that workflow has replied.
        active.queuedClose = request;
      } else if (current && active.navigation) {
        reply(request, { error: 'HX Edit is still finishing the previous preview step. Try again in a moment.' });
      } else {
        reply(request, { error: 'This TONE3000 selection is no longer active.' });
      }
      listen();
    });
  }
  listen();
  return { openBrowser };
}
