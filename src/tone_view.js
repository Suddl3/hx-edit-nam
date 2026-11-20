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
  const imageCache = new NativeFunction(base.add(0x37d2f0), 'pointer', []);   // HX Edit's image cache
  const imageNamed = method(0x37d3e0, 'pointer', ['pointer']);                 // cache, bare file name -> image
  const retainImage = method(0x37c8a0, 'void', []);                           // image reference +1
  const redrawWidget = method(0x3baa40, 'void', []);                         // as nam_view redraws its list
  const categoryPicture = method(0x3c2f70, 'pointer', []);                    // category -> its picture
  let sidebarFavorites = NULL;      // the sidebar's favorites list: it keeps its rows, drawn from each favorite's model
  let pickerFavorites = NULL;       // the block picker's model column while it lists favorites
  let savingFrom = null, favoriteNamesSeen = null;   // Save As Favorite opened on a capture slot; names at the last reload
  const findWidget = method(0x3b7ae0, 'pointer', ['uint', 'int']);
  const widgetOrigin = method(0x3b7e00, 'pointer', ['pointer']);
  const isVisible = method(0x3ba100, 'uint8', ['int']);
  const point = Memory.alloc(8);
  const selectionEvent = 0x216c6273; // sbl!
  const gridSelectEvent = 0x21677273; // srg!: a tile clicked in the block picker's grid view
  const gridSelected = method(0x37a010, 'pointer', []);   // grid list -> its clicked tile (row layout)
  // The Guitar / Bass menu above the model column (controller +0x50, class vtable 0x9176e8). Its value
  // is the subcategory id (Guitar 1, Bass 2: stock Amp's and ours); HX Edit reads it on "cva!"
  // (0x217120 at 0x2172eb). The menu's own select (0x3ac940: value, event, redraw) moves the check
  // mark, sets the label and then the value, which sends that event (the plain widget setter at
  // 0x3bcb50 left the label on "Guitar").
  const menuSelect = method(0x3ac940, 'void', ['int', 'uint', 'int']);
  let menuSelectReady = null;
  const subcategoryChanged = 0x21617663;   // cva!
  function subcategoryMenu(controller) {
    const menu = controller.add(0x50).readPointer();
    return !menu.isNull() && menu.readPointer().equals(base.add(0x9176e8)) ? menu : NULL;
  }
  function subcategoryValue(controller) {
    const menu = subcategoryMenu(controller);
    return menu.isNull() ? null : menu.add(0x54).readPointer().add(0x1c).readS32();
  }
  // Sides of the dropdown (nam_catalog.PICKER_SIDES): All 99, Guitar 1, Bass 2. Each category
  // remembers the side last picked there (floor-memory.json); never picked: All.
  const SIDE_ALL = 99, SIDE_GUITAR = 1, SIDE_BASS = 2;
  const SIDED_CATEGORIES = new Set(['Amp', 'Amp+Cab', 'Preamp', 'NAM Amp', 'NAM Amp + cab', 'NAM Cab', 'NAM Drive',
    'NAM Other']);
  const rememberedSides = new Map(Object.entries(profile.pickerSides && typeof profile.pickerSides === 'object' ?
    profile.pickerSides : {}).filter(([, value]) => Number.isInteger(value)));
  // name -> {slot, group, icon} last seen (floor memory): HX Edit loads its favorites at connect,
  // before the slot check says which capture is where.
  const favoriteLooks = new Map(Object.entries(profile.favoriteLooks && typeof profile.favoriteLooks === 'object' ?
    profile.favoriteLooks : {}).filter(([, look]) => look && Number.isInteger(look.slot) && Number.isInteger(look.group)));
  const slotLooks = new Map(Array.from(favoriteLooks.values(), look => [look.slot, look]));   // slot -> its last known look
  let choosingSide = false;
  const rememberedSide = category => rememberedSides.get(category) ?? SIDE_ALL;
  // A capture shows on All, on its own instrument's side, and on both when it is marked Any.
  const onSide = (capture, side) => side === SIDE_ALL ||
    [side === SIDE_BASS ? 'bass' : 'guitar', 'any'].includes(capture.instrument || 'guitar');
  function shownCategory() {
    try {
      const row = categoryList.isNull() ? NULL : selectedRow(categoryList);
      return row.isNull() ? '' : row.readUtf8String();
    } catch (_) { return ''; }
  }
  function noteSideChoice(controller) {
    // The user picked a side in the dropdown: remember it for this category.
    if (choosingSide) return;
    const category = shownCategory(), value = subcategoryValue(controller);
    if (!SIDED_CATEGORIES.has(category) || !Number.isInteger(value) || rememberedSides.get(category) === value) return;
    rememberedSides.set(category, value);
    send({type: 'pickerSide', category, value});
  }
  const sideBlocks = new Map();   // controller -> the block its sides were last chosen for
  function blockSide(controller) {
    // After HX Edit shows the selected block's category (0x21e500): its remembered side when the
    // block's model is on it, otherwise All. Once per block, so browsing another side sticks.
    // Every block counts as "last", whatever its category: coming back to a block applies its side again.
    const key = position(controller);
    if (sideBlocks.get(controller.toString()) === key) return;
    const category = shownCategory();
    if (!category) return;                 // no category showing yet (startup): try again next time
    sideBlocks.set(controller.toString(), key);
    if (!SIDED_CATEGORIES.has(category) || category.startsWith('NAM ')) return;   // NAM: wantedRecord
    const list = controller.add(0x58).readPointer();
    if (list.isNull()) return;
    const shown = selectedRow(list), model = shown.isNull() ? '' : shown.readUtf8String();
    const wanted = rememberedSide(category);
    if (wanted === subcategoryValue(controller)) return;
    chooseSubcategory(controller, wanted);
    if (!model) return;
    let index = rowIndex(list, text => text === model);
    if (index < 0 && wanted !== SIDE_ALL) { chooseSubcategory(controller, SIDE_ALL); index = rowIndex(list, text => text === model); }
    if (index >= 0) selectRow(list, 1, index, 0, 0);   // the block's model stays highlighted
  }
  function chooseSubcategory(controller, value) {
    const menu = subcategoryMenu(controller);
    if (menu.isNull() || subcategoryValue(controller) === value) return;
    if (menuSelectReady === null) {
      const bytes = new Uint8Array(base.add(0x3ac940).readByteArray(16));
      menuSelectReady = Array.from(bytes, b => b.toString(16).padStart(2, '0')).join('') === '558bec56578bf1e8d4c500008b7d083b';
    }
    if (!menuSelectReady) return;     // another HX Edit build: leave the menu as it is
    choosingSide = true;
    try { menuSelect(menu, value, subcategoryChanged, 1); } finally { choosingSide = false; }
    if (listReports-- > 0) send({type: 'toneTrace', reason: 'nam-category', stage: 'subcategory', value});
  }
  // grid list: add a tile (index, label, picture, 0, row index, model, flags, 1), as 0x21dbfd does
  const gridAppend = method(0x37a1d0, 'pointer', ['int', 'pointer', 'pointer', 'int', 'int', 'pointer', 'int', 'int']);
  function listRows(list) {
    // A list's rows ([[list+0x7c]+4], count at +8): the model column and grid view store them alike.
    const rows = list.add(0x7c).readPointer();
    if (rows.isNull()) return [];
    const count = Math.min(rows.add(8).readS32(), 512), items = rows.add(4).readPointer(), out = [];
    for (let index = 0; index < count; index++) out.push(items.add(index * 4).readPointer());
    return out;
  }
  function appendCaptureTiles(controller, list) {
    // HX Edit builds grid view's tiles from the model column's rows when it fills the column
    // (0x21db23). Capture rows added after that (a NAM block's own category, refreshed when the
    // block is selected or added) had no tiles: only NAM A2 Lite showed. One tile per capture row
    // not yet shown, with the row's own picture.
    try {
      const grid = controller.add(0x60).readPointer();
      if (grid.isNull()) return;
      const shown = new Set(listRows(grid).filter(tile => !tile.isNull()).map(tile => tile.readUtf8String()));
      let added = 0;
      for (const row of listRows(list)) {
        if (row.isNull()) continue;
        const label = row.readUtf8String();
        if (!captureRows.has(label) || shown.has(label)) continue;
        if (gridAppend(grid, -1, row, row.add(0x104).readPointer(), 0, row.add(0x100).readS32(),
                       row.add(0x10c).readPointer(), 0, 1).isNull()) break;   // the grid is locked
        added++;
      }
      if (added) redrawList(grid);
    } catch (error) {
      send({type: 'toneTrace', reason: 'nam-category', stage: 'grid-tiles-skipped', error: String(error.message)});
    }
  }
  const tileLabels = tiles.map(tile => Memory.allocUtf8String(tile.label));
  const tileByBlock = new Map(tiles.map((tile, index) => [tile.block, index]));
  const tileByLabel = new Map(tiles.map((tile, index) => [tile.label, index]));
  // controller -> the model row selected before a tile was clicked: the block's own model
  const lastModel = new Map();
  const title = Memory.allocUtf16String('TONE3000 · HX Edit Unbound');
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
      if (selectedController.add(0x80).readS32() !== leftover.dsp || block(selectedController) !== leftover.block) return;
      const playing = namView.modelSlot();
      if (Number.isInteger(playing) && playing !== AUDITION_SLOT && playing >= 1 && playing <= 127) {
        // The recorded block plays another capture now (it was moved off the preview before the
        // editor closed): it keeps it; only the preview slot is released once nothing uses it.
        send({type: 'toneTrace', reason: 'leftover-preview', stage: 'block-moved-on', dsp: leftover.dsp, block: leftover.block, slot: playing});
        leftover.clearUntil = now + 8000;
        return;
      }
      if (playing !== AUDITION_SLOT) return;                 // not on screen yet
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
    const payloadCanBind = target => !!target && !active.transfer && !active.pendingTransfer && !active.quarantined;
    const bound = active.bound && !active.quarantined && atTarget();
    const payload = { type: 'toneTarget', session: active.id, version: active.targetVersion,
      bound: !!bound, label: bound ? active.targetLabel : '',
      // What the bound block plays (the library's Original | Pedal switch shows it): its model and capture slot.
      blockModel: bound ? boundModel() : null, blockSlot: bound ? boundSlot() : null,
      selected: selected ? selected.label : '', canBind: payloadCanBind(selected),
      preview: !!active.transfer || !!active.orphanTransfer || !!active.pendingTransfer, quarantined: !!active.quarantined,
      text: text || (bound ? active.transfer ? 'Auditioning in User IR 128. Input and EQ stay with this block.' :
        active.onPreviewSlot ? 'This block is still on the preview slot (User IR 128). Pick a capture for it in the NAM tab, then preview.' :
        'Ready to audition on this NAM block.' :
        active.cleanupRequested ? 'Restoring the previous block and clearing its preview…' :
        active.quarantined ? 'Preview cleanup needs attention. No new block will be changed.' :
        // A NAM block is selected and Preview binds it: after a snapshot change or reselecting the
        // block the card said "Select a NAM block" while Preview worked (Floor, November 13).
        payloadCanBind(selected) ? 'Ready to audition on the selected NAM block.' :
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
      send({ type: 'toneFocus', session: active.id, page: options.level ? 'level' : options.look ? 'look' : options.library ? 'library' : 'catalog' });
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
      tile: tiles[0], profile: '', navigation: true, library: !!options.library, level: !!options.level, look: !!options.look,
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
  // A capture just put on a block that HX Edit was still building: HX Edit can then reset the new
  // block's Model to slot 1 (an IR dragged from the IR list became a NAM block on slot 24, then
  // played slot 1, a plain IR, and the Floor went silent: November 18). For a few seconds the
  // block's slot is read back from the edit buffer and set again if it moved.
  let captureCheck = null;
  const CHECK_MS = 3000, CHECK_STABLE = 700, CHECK_TRIES = 3;
  function checkCaptureLater(controller, slot) {
    try {
      if (!controllerReady(controller) || !Number.isInteger(slot)) return;
      captureCheck = {controller, slot, dsp: controller.add(0x80).readS32(), block: block(controller),
        until: Date.now() + CHECK_MS, stableSince: 0, tries: 0, at: 0};
    } catch (_) { captureCheck = null; }
  }
  function runCaptureCheck() {
    // UI thread, from the message hook.
    const check = captureCheck, now = Date.now();
    if (!check || now < check.at) return;
    check.at = now + 100;
    try {
      const controller = check.controller;
      if (!controllerReady(controller) || controller.add(0x80).readS32() !== check.dsp || block(controller) !== check.block) {
        captureCheck = null; return;                         // another block now: nothing to check
      }
      const slot = namBlockSlot(controller);
      if (slot === check.slot) {
        if (!check.stableSince) check.stableSince = now;
        if (now - check.stableSince >= CHECK_STABLE) {
          captureCheck = null;
          if (check.tries) send({type: 'toneTrace', reason: 'capture-check', stage: 'kept', slot, tries: check.tries});
        }
        return;
      }
      check.stableSince = 0;
      if (now > check.until || check.tries >= CHECK_TRIES) {
        captureCheck = null;
        send({type: 'toneTrace', reason: 'capture-check', stage: 'lost', slot: check.slot, now: slot});
        send({type: 'dspNotice', text: `The NAM block did not keep capture ${check.slot}. Check its Model.`});
        return;
      }
      if (Number.isInteger(slot) && Number.isInteger(namView.modelSlot()) && cursorController().equals(controller)) {
        check.tries++;
        const accepted = namView.setSlot(check.slot);
        send({type: 'toneTrace', reason: 'capture-check', stage: accepted ? 'set-again' : 'refused', slot: check.slot, was: slot});
      }
    } catch (error) {
      captureCheck = null;
      send({type: 'toneTrace', reason: 'capture-check', stage: 'skipped', error: String(error.message)});
    }
  }

  function captureGroup(gear) {
    // nam_catalog.NAM_CATEGORY_GROUPS: 1 Amp, 2 Amp + cab, 3 Cab, 4 Pedal, 5 Other.
    return gear === 'amp' || gear === 'preamp' ? 1 : gear === 'amp-cab' || gear === 'amp-pedal-cab' ? 2 :
      gear === 'cab' ? 3 : gear === 'pedal' || gear === 'pedal-amp' ? 4 : 5;
  }

  function captureRowLabel(capture) {
    const name = String(capture.name).split(' – ').pop().trim() || 'Capture';
    return `${capture.slot}: ${name.length > 28 ? name.slice(0, 27).trimEnd() + '…' : name}`;
  }

  function captureView(header) {
    // header: the block row's text. Its trailing spaces name the group (1-4) plus 4 per side:
    // All 1-4, Guitar 5-8, Bass 9-12 (nam_catalog.patch_nam_category). None (Amp > NAM, or an HX
    // Edit that trims names): every capture.
    const spaces = header.length - header.trimEnd().length;
    if (spaces === 0) return {group: 0, instrument: null};
    const groups = NAM_CATEGORY_NAMES.length, group = (spaces - 1) % groups + 1, side = Math.floor((spaces - 1) / groups);
    return {group, instrument: side === 1 ? 'guitar' : side === 2 ? 'bass' : null};
  }

  function capturesUnder(header) {
    const {group, instrument} = captureView(header);
    return namView.installedCaptures()
      .filter(capture => group === 0 || captureGroup(capture.gear) === group)
      // Guitar or Bass: that instrument's captures and the ones marked Any.
      .filter(capture => !instrument || [instrument, 'any'].includes(capture.instrument || 'guitar'))
      .sort((a, b) => String(a.name).localeCompare(String(b.name)) || a.slot - b.slot);
  }

  // Gear pictures for capture rows (capture_icons.py picks the id from the capture's name; the
  // build copies the pictures into res/icons_models as NAMCAP_<id>.png). Loaded the way HX Edit
  // loads a model's own picture: its image cache by bare file name. Each is retained once and
  // kept for the session. A missing picture leaves the NAM block's own picture on the row.
  const captureImages = new Map();   // icon id -> image object (or NULL)

  function captureImage(icon) {
    if (!/^[a-z0-9_]{1,40}$/.test(icon || '')) return NULL;
    if (!captureImages.has(icon)) {
      let image = NULL;
      try {
        const cache = imageCache();
        image = cache.isNull() ? NULL : imageNamed(cache, Memory.allocUtf8String(`NAMCAP_${icon}.png`));
        if (!image.isNull()) retainImage(image);
      } catch (error) {
        image = NULL;
        send({type: 'toneTrace', reason: 'nam-category', stage: 'icon-skipped', icon, error: String(error.message)});
      }
      captureImages.set(icon, image);
    }
    return captureImages.get(icon);
  }

  // HX Edit's favorites of a NAM block. A favorite's small icon (Save As Favorite, the sidebar's
  // device list) is the category picture of its model, drawn later from FavoriteObj +0x10:
  // [[model+0x30]+0x14] is the category (0x3c2710), so every NAM favorite showed NAM Other, the
  // last category holding the NAM model. A favorite whose capture slot is known (nam_view's
  // favoriteSlot) is pointed at the NAM model of its capture's category instead: same model id,
  // the record HX Edit itself lists there. In the block picker (rows of 0x91fcd0, picture given
  // here) it shows the capture's picture, as the capture rows do. The Save As Favorite dialog's
  // new entry (0x25df6a) is named after the capture on the block, not "NAM A2 Lite" plus the
  // spaces that tag the NAM rows, which ended up in the saved name.
  const FAVORITE_VTABLE = 0x919304, PICKER_LIST = 0x91fcd0, SMALL_LIST = 0x91ebb4, NEW_FAVORITE_ROW = 0x25df6a, SIDEBAR_ROW = 0x204924;
  const NAM_FAVORITE_MODELS = new Set(['HD2_AmpNAMLite', 'HD2_DistNAMLite']);   // NAM_MODEL_ID is declared further down
  const favoriteLabels = new Map();  // default favorite name -> its native string, kept alive
  // The slot check empties its list for a moment whenever the favorites change on the Floor (a
  // save, a clear): the picker then fell back to NAM Other until it was done. The last full list
  // stands in while it is empty.
  let lastCaptures = [];
  function knownCaptures() {
    const captures = namView.installedCaptures();
    if (captures.length) lastCaptures = captures;
    return captures.length ? captures : lastCaptures;
  }
  function favoriteCapture(slot) {
    return Number.isInteger(slot) ? knownCaptures().find(capture => capture.slot === slot) || null : null;
  }
  function favoriteName(capture) {
    // The capture's short name, as its row shows it; Helix names hold 16 characters.
    const name = capture ? String(capture.name).split(' – ').pop().trim() : '';
    return (name || tiles[0].block).slice(0, 16).trimEnd();
  }
  function pointFavorite(object) {
    // A NAM favorite whose capture is known: its model becomes the NAM model of the capture's
    // category. The capture, or null when the favorite is not one of those.
    if (object.compare(ptr(0x10000)) < 0 || !object.readPointer().equals(base.add(FAVORITE_VTABLE))) return null;
    const model = object.add(0x10).readPointer();
    const id = model.isNull() ? '' : l6text(model.add(0x24));
    if (!NAM_FAVORITE_MODELS.has(id)) return null;
    const name = l6text(object.add(8)), slot = namView.favoriteSlot?.(name);
    if (!Number.isInteger(slot)) return null;
    // The capture on that slot, or (before this launch's slot check) what it was last time.
    // The slot check is busy for a moment after a save: then the slot's last known look.
    const capture = favoriteCapture(slot);
    let look = favoriteLooks.get(name) || null;
    const fresh = capture ? {slot, group: captureGroup(capture.gear), icon: capture.icon || ''} :
      look && look.slot === slot ? look : slotLooks.get(slot) || null;
    if (!fresh) return null;
    slotLooks.set(slot, fresh);
    if (!look || look.slot !== fresh.slot || look.group !== fresh.group || look.icon !== fresh.icon) {
      look = fresh;
      favoriteLooks.set(name, look);
      send({type: 'favoriteLook', name, look});
    }
    const record = id === 'HD2_AmpNAMLite' ? groupRecord(look.group) : null;
    if (record && !record.equals(model) && l6text(record.add(0x24)) === id) object.add(0x10).writePointer(record);
    return look;
  }
  function groupRecord(group) {
    return Number.isInteger(group) && group >= 1 && group <= NAM_CATEGORY_NAMES.length ?
      namRecords.get(NAM_CATEGORY_NAMES[group - 1] + ':0') || null : null;
  }
  function captureRecord(capture) {
    // The NAM model as listed in the capture's category (its picture is that category's).
    return capture ? groupRecord(captureGroup(capture.gear)) : null;
  }
  function favoritePicture(object) {
    // A picker list row's picture: its (now right) category's, as the other favorites and the
    // sidebar show. Grid view shows the capture's own picture (the 0x3c2fb0 hook).
    return categoryImage(object.add(0x10).readPointer());
  }
  let favoriteRevision = '';
  const categoryImages = new Map();   // category -> its picture, retained once for the rows given it
  function categoryImage(model) {
    // The picture HX Edit gives the model's category (0x3c2710 category, 0x3c2f70 its picture).
    const category = model.add(0x30).readPointer().add(0x14).readPointer();
    const key = category.toString();
    if (!categoryImages.has(key)) {
      const image = categoryPicture(category);
      if (!image.isNull()) retainImage(image);
      categoryImages.set(key, image);
    }
    return categoryImages.get(key);
  }
  function pointFavorites() {
    // A favorite just saved, renamed or replaced, or the slot check done: rows built before were
    // drawn with NAM Other. Pointed again, and the sidebar's list redrawn (it keeps its rows).
    const revision = `${namView.favoriteRevision?.() ?? 0}|${namView.installedCaptures().length}|${namRecords.size}`;
    if (revision === favoriteRevision) return;
    favoriteRevision = revision;
    let known = 0;
    for (const object of namView.favoriteObjects?.() || []) {
      try { if (pointFavorite(object)) known++; } catch (_) { /* the next row build does it */ }
    }
    if (known) refreshFavoriteLists();
  }
  function refreshFavoriteLists() {
    // The sidebar and the Save dialog draw each favorite's picture from its model when they paint
    // (0x401580); the block picker baked its picture into the row (+0x104, drawn by 0x37fc20).
    let changed = 0;
    if (!pickerFavorites.isNull() && pickerFavorites.readPointer().equals(base.add(PICKER_LIST))) {
      for (const row of listRows(pickerFavorites)) {
        if (row.isNull()) continue;
        const object = row.add(0x10c).readPointer(), capture = pointFavorite(object);
        if (!capture) continue;
        const image = favoritePicture(object);
        if (image.isNull() || image.equals(row.add(0x104).readPointer())) continue;
        retainImage(image);                  // the row's own reference: the list may release it with the row
        row.add(0x104).writePointer(image);
        changed++;
      }
      if (changed) redrawWidget(pickerFavorites);
    }
    if (!sidebarFavorites.isNull() && sidebarFavorites.readPointer().equals(base.add(SMALL_LIST))) redrawWidget(sidebarFavorites);
    send({type: 'toneTrace', reason: 'nam-category', stage: 'favorites-pointed', changed});
  }
  function favoriteRow(list, args, from) {
    try {
      const kind = list.readPointer(), picker = kind.equals(base.add(PICKER_LIST));
      if (!picker && !kind.equals(base.add(SMALL_LIST))) return false;
      const object = args[5];
      if (object.compare(ptr(0x10000)) < 0) return false;          // some lists pass a number here
      if (object.readPointer().equals(base.add(FAVORITE_VTABLE))) {
        if (!picker && from.equals(base.add(SIDEBAR_ROW))) sidebarFavorites = list;
        if (picker) pickerFavorites = list;
        const capture = pointFavorite(object);
        if (!capture) return false;
        // The picker computed its picture from the model before this (0x21c56f): replace it.
        if (picker) { const image = favoritePicture(object); if (!image.isNull()) args[2] = image; }
        return true;
      }
      if (picker || !from.equals(base.add(NEW_FAVORITE_ROW)) || args[1].isNull() ||
          args[1].readUtf8String().trimEnd() !== tiles[0].block) return false;
      const name = favoriteName(favoriteCapture(namView.modelSlot()));
      if (!favoriteLabels.has(name)) favoriteLabels.set(name, Memory.allocUtf8String(name));
      args[1] = favoriteLabels.get(name);
      return true;
    } catch (error) {
      send({type: 'toneTrace', reason: 'nam-category', stage: 'favorite-row-skipped', error: String(error.message)});
      return false;
    }
  }

  function appendCaptureRows(frame) {
    try {
      namView.requestSlotCheck?.();          // remembered slots make this immediate after the first run
      builtSignature = captureSignature();
      // Rows already in the list (HX Edit's own rebuild went through the row hook) are not added twice.
      let present = new Set();
      try { present = new Set(listRows(frame.list).filter(row => !row.isNull()).map(row => row.readUtf8String())); }
      catch (_) { present = new Set(); }
      for (const capture of capturesUnder(frame.header)) {
        const label = captureRowLabel(capture);
        if (present.has(label)) continue;
        if (!captureLabels.has(label)) captureLabels.set(label, Memory.allocUtf8String(label));
        captureRows.set(label, {slot: capture.slot, header: frame.header});
        const image = captureImage(capture.icon);
        appendRow(frame.list, -1, captureLabels.get(label), image.isNull() ? frame.image : image, 0, frame.index, frame.model, 0, 0);
      }
    } catch (error) {
      send({type: 'toneTrace', reason: 'nam-category', stage: 'rows-skipped', error: String(error.message)});
    }
  }

  function chooseCapture(controller, list, entry) {
    // A capture row was chosen. Never forwarded to HX Edit as a model choice.
    if (active?.transfer || active?.pendingTransfer || command || workflowRequest || toneImport) {
      clearSelection(list, 0, 0);     // not chosen: refreshCaptureList puts the playing capture's row back
      send({type: 'toneTrace', reason: 'nam-category', stage: 'ignored-busy', slot: entry.slot});
      return;
    }
    const session = active;
    if (session) session.applying = true;
    try {
      // The Model control on screen belongs to the block whose model list was clicked. If it
      // exists, this is already a NAM block: change its capture only, never re-pick its model
      // (that would reset Input and EQ).
      const before = namView.modelSlot();
      if (Number.isInteger(before)) {
        const accepted = namView.setSlot(entry.slot);
        send({type: 'toneTrace', reason: 'nam-category', stage: accepted ? 'capture-set' : 'capture-refused', slot: entry.slot});
        // Clicking the capture the block already plays changes nothing, the lock included.
        if (accepted && entry.slot !== before) setLockState(controller, 'locked', 'capture-row');
        // The chosen row stays selected (a tile: its row in the column); a refused one is deselected.
        if (accepted) highlightCapture(list, entry.slot); else clearSelection(list, 0, 0);
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
        send({type: 'dspNotice', text: 'HX Edit did not add the NAM block. Nothing else was changed.'});
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
      if (accepted) { setLockState(selectedController, 'locked', 'capture-row'); checkCaptureLater(selectedController, pending.slot); }
    } catch (error) {
      pendingCapture = null;
      send({type: 'toneTrace', reason: 'nam-category', stage: 'skipped', error: String(error.message)});
    }
  }

  // --- A capture dropped on an empty cell or another kind of block --------------------------
  // The cell becomes a NAM A2 Lite block, as choosing the capture's NAM category and then its
  // block row by hand would (one Undo step); the caller then puts the capture on it as Use does.
  // Returns 'nam' when the selected block already is one, 'adding' when the block row was chosen,
  // else what stopped it. UI thread (the NAM tab's queued actions run in the message hook).
  function makeNamBlock(gear, options = {}) {
    if (!profile.namCategory) return 'Drop the capture on a NAM block (NAM categories are off in this build).';
    if (active?.transfer || active?.pendingTransfer || command || workflowRequest || toneImport || pendingCapture)
      return 'HX Edit is finishing another change. Drop the capture again in a moment.';
    try {
      const controller = listOwner();
      if (controller.isNull() || categoryList.isNull()) return 'HX Edit did not open the model list for that spot. Select it, then drop again.';
      if (Number.isInteger(namBlockSlot(controller))) return 'nam';
      // HX Edit refuses a block its DSP cannot fit ("Failed to load model. (Out of memory
      // condition.)", Floor, November 18): say so, by the DSP meter, and change nothing.
      const full = options.roomChecked ? null : namRoom(controller);   // a replace drop counted it already
      if (full) {
        send({type: 'dspNotice', text: full});
        send({type: 'toneTrace', reason: 'nam-drop', stage: 'no-room'});
        return full;
      }
      const target = NAM_CATEGORY_NAMES[captureGroup(gear) - 1];
      const index = rowIndex(categoryList, text => text === target);
      if (index < 0) return 'A NAM block cannot go there.';
      clearSelection(categoryList, 0, 0);
      selectRow(categoryList, 1, index, 0, 1);
      modelEvent(controller, selectionEvent, categoryList, NULL, NULL);
      markCurrentRow(categoryList, index);
      const list = controller.add(0x58).readPointer();
      const header = list.isNull() ? -1 : rowIndex(list, text => text.trimEnd() === tiles[0].block);
      if (header < 0) return `HX Edit's model list has no ${tiles[0].block} row for that spot.`;
      clearSelection(list, 0, 0);
      selectRow(list, 1, header, 0, 0);
      send({type: 'toneTrace', reason: 'nam-drop', stage: 'adding-block', group: captureGroup(gear)});
      modelEvent(controller, selectionEvent, list, NULL, NULL);
      return 'adding';
    } catch (error) {
      send({type: 'toneTrace', reason: 'nam-drop', stage: 'skipped', error: String(error.message)});
      return 'Could not put a NAM block there. Select the spot, then drop again.';
    }
  }
  // A NAM slot taken from HX Edit's own IR list (dragged onto the chain or double-clicked) gives
  // the block an IR model, which would play the capture's data as an impulse. When the selected
  // block plays the slot just pressed in the IR list as an IR, it becomes a NAM block on that slot
  // (NAM category, block row, then the slot and the lock, as a capture row does).
  const IR_MODELS = new Set(['HD2_ImpulseResponse1024', 'HD2_ImpulseResponse2048']);
  let irCheckAt = 0;
  function convertIrPick() {
    const pick = namView.irPick?.();
    if (!pick || !targetBinding?.peekModel) return;
    const now = Date.now();
    if (now < irCheckAt) return;
    irCheckAt = now + 100;
    if (active?.transfer || active?.pendingTransfer || command || workflowRequest || toneImport || pendingCapture) return;
    try {
      const controller = cursorController();
      if (controller.isNull()) return;
      const dsp = controller.add(0x80).readS32();
      const seen = targetBinding.peekModel(controller.add(0x34).readPointer(), dsp, block(controller));
      if (!seen || !IR_MODELS.has(seen.model) || seen.index !== pick.slot) return;
      namView.clearIrPick();
      const capture = namView.installedCaptures().find(entry => entry.slot === pick.slot);
      const checked = roomCheckedFor && roomCheckedFor.slot === pick.slot && now < roomCheckedFor.until;
      roomCheckedFor = null;
      const made = makeNamBlock(capture ? capture.gear : '', {roomChecked: !!checked});
      send({type: 'toneTrace', reason: 'ir-list-nam', stage: made === 'adding' ? 'adding-block' : 'not-added', slot: pick.slot});
      if (made === 'adding') pendingCapture = {slot: pick.slot, dsp, until: now + 3000};
    } catch (error) {
      namView.clearIrPick();
      send({type: 'toneTrace', reason: 'ir-list-nam', stage: 'skipped', error: String(error.message)});
    }
  }
  function namRoom(controller) {
    // null when the block's DSP can take a NAM block in place of the selected one (or when there is
    // no reading: HX Edit decides), else why not. Loads as the DSP meter has them (dsp_meter.js),
    // what the replaced model frees from this copy's catalog (options.modelLoads).
    const dsp = controller.add(0x80).readS32();
    let replaced = null;
    try {
      const seen = targetBinding?.peekModel?.(controller.add(0x34).readPointer(), dsp, block(controller));
      replaced = seen ? seen.model : null;
    } catch (_) { replaced = null; }
    return namRoomOn(dsp, replaced);
  }
  function namRoomOn(dsp, replaced) {
    // As namRoom, for a NAM block on DSP dsp in place of model replaced (null: an empty cell).
    const loads = typeof dspLoadsNow === 'undefined' ? null : dspLoadsNow;
    const view = loads && loads[dsp];
    if (!view || typeof view.used !== 'number' || typeof view.budget !== 'number') return null;
    const freed = replaced && profile.modelLoads ? Number(profile.modelLoads[replaced]) || 0 : 0;
    const after = Math.round((view.used - freed + (Number(profile.namLoad) || 36)) * 10) / 10;
    return after <= view.budget ? null : `DSP ${dsp + 1} has no room for a NAM block (${after} of ${view.budget}).`;
  }
  // --- Dropping an IR-list slot on the chain: over a block replaces it ------------------------
  // HX Edit's own drag from the IR list ('lbir', the slot's row) only ever inserts: the chain gives
  // an occupied cell a "before" and an "after" zone and slides the blocks aside. installDropReplace
  // adds a centre "replace" zone inside HX Edit's own zone machinery; the per-DSP drop delegate D
  // (controller + 0x34) holds editor [D+8], view [D+0xc], dsp [D+0x10], path model [D+0x14], preview
  // clone [D+0x18], zones at D+0x1c and the chosen zone at D+0x34 (mode at D+0x48, row D+0x4c, col
  // D+0x50).
  const dropGuards = {
    '413d70': '558bec6aff68????????64a100000000', '412560': '558bec6aff68????????64a100000000',
    '413a90': '558bec6aff68????????64a100000000', '513620': '558bec6aff68????????64a100000000',
    '500320': '558bec6aff68????????64a100000000', '264e70': '558bec568b750883fe0273428b013d05',
    '265e50': '83390075128b410c85c074088b401080', '3777f0': '558bec8b0185c074128b4d080f1f4000',
    '40e730': '558bec83ec6053578b7d148bd9895de8', '265df0': '558bec53578bf98b073d05002100741e',
    '40c6b0': '558bec568bf1578b560885d20f849600', '40eff0': '558bec8b417c3d050021007507b80900',
    '40adc0': '558bec8a4508568bf16a008886970000', '3b5cc0': '558bec6aff68????????64a100000000',
  };
  const LBIR = 0x6c626972, SFOL = 0x73666f4c;           // drag types: an IR-list slot, a block being moved
  let roomCheckedFor = null;         // {slot, until}: a drop that already counted the DSP room
  function guardsMatch(guards) {
    // '??' skips a byte: the SEH prologues push an absolute handler address, which relocation changes.
    return Object.entries(guards).every(([rva, hex]) => {
      const bytes = new Uint8Array(base.add(parseInt(rva, 16)).readByteArray(hex.length / 2));
      return Array.from(bytes, b => b.toString(16).padStart(2, '0')).every((byte, i) => {
        const want = hex.substr(i * 2, 2);
        return want === '??' || want === byte;
      });
    });
  }
  // After a replace drop of a NAM slot on another kind of block: that block is clicked (as a click
  // on it would select it), then made a NAM block and given the slot, as a NAM category pick does.
  // One model change and one slot change in HX Edit's history, so Undo brings the old block back.
  let runNamFollow = null;          // installDropReplace's follow-up, run from the picker's message hook
  let irReplace = null;              // {slot, gear, until}
  function runIrReplace() {
    // UI thread, from the message hook.
    const job = irReplace, now = Date.now();
    try {
      const click = namView.dropState();
      if (now > job.until || click.state === 'none') {
        irReplace = null; namView.endDrop();
        send({type: 'dspNotice', text: 'HX Edit did not select that block. Drop the slot again.'});
        return;
      }
      if (click.state !== 'clicked' || now - click.clicked < 250) return;
      irReplace = null; namView.endDrop();
      const made = makeNamBlock(job.gear);
      send({type: 'toneTrace', reason: 'drop-replace', stage: 'nam-' + (made === 'adding' || made === 'nam' ? made : 'refused'), slot: job.slot});
      const controller = cursorController();
      if (made === 'adding' && !controller.isNull()) pendingCapture = {slot: job.slot, dsp: controller.add(0x80).readS32(), until: now + 3000};
      else if (made === 'nam' && namView.setSlot(job.slot)) {
        setLockState(controller, 'locked', 'drop');
        checkCaptureLater(controller, job.slot);
      } else if (made !== 'adding' && made !== 'nam') send({type: 'dspNotice', text: made});
    } catch (error) {
      irReplace = null;
      send({type: 'toneTrace', reason: 'drop-replace', stage: 'nam-skipped', error: String(error.message)});
    }
  }

  function installDropReplace() {
    // HX Edit's own hover stays in charge. For a drag
    // from the IR list ('lbir'):
    //  - the zone builder 0x40e730 gets a centre zone on each occupied cell (mode 0 = REPLACE), between
    //    narrower before/after edges, where HX Edit's dry run allows a replace;
    //  - choosing that zone opens no gap (0x265df0 does nothing for it), and the block is dimmed
    //    (0x40adc0) to show it will be replaced;
    //  - a change of zone waits a moment (the pointer HX Edit sees is held on the current zone), so
    //    blocks do not slide on every brush past a block;
    //  - on drop, HX Edit's own paste is told to replace (flag nibble 0): its Undo restores the block.
    //    A NAM capture's slot on a NAM block only sets its slot; on another kind of block, that block
    //    is selected and made a NAM block (runIrReplace).
    if (!profile.navigation || !targetBinding?.setIndex) return;
    if (!guardsMatch(dropGuards)) { send({type: 'toneTrace', reason: 'drop-replace', stage: 'guard-failed'}); return; }
    const find = new NativeFunction(base.add(0x3777f0), 'pointer', ['pointer', 'uint'], 'thiscall');
    const cellAt = new NativeFunction(base.add(0x264e70), 'pointer', ['pointer', 'int', 'int'], 'thiscall');
    const cellEmpty = new NativeFunction(base.add(0x265e50), 'uint8', ['pointer'], 'thiscall');
    const canPaste = new NativeFunction(base.add(0x500320), 'int', ['pointer', 'int', 'int', 'pointer', 'uint'], 'thiscall');
    const pushZone = new NativeFunction(base.add(0x40c6b0), 'void', ['pointer', 'pointer'], 'thiscall');
    const itemAt = new NativeFunction(base.add(0x40eff0), 'pointer', ['pointer', 'int', 'int'], 'thiscall');
    const dimItem = new NativeFunction(base.add(0x40adc0), 'void', ['pointer', 'int', 'int'], 'thiscall');
    const openGap = new NativeFunction(base.add(0x265df0), 'uint8', ['pointer', 'int', 'int', 'int'], 'thiscall');
    // Blocks glide GLIDE_MS (HX Edit's own 175 ms, shortened while an IR-list drag hovers the chain);
    // a zone change waits just past it, so one glide finishes before the next starts.
    const REPLACE = 0, EDGE = 0.22, GLIDE_MS = 110, DWELL_REPLACE = 140, DWELL_INSERT = 120;
    const record = Memory.alloc(0x20), target = Memory.alloc(16);
    const held = new Map();            // D -> {point, has, pending: {zone, since}}
    let overD = NULL, dimmed = null, dropReplace = false;

    function blockName(object) {
      // A path object's id (+8): HX Edit's String {vtable, rep}; rep: length +4, characters* +0x10.
      const rep = object.add(0xc).readPointer();
      if (rep.isNull()) return '';
      const length = rep.add(4).readU32(), chars = rep.add(0x10).readPointer();
      return length > 0 && length < 64 && !chars.isNull() ? chars.readUtf8String(length) : '';
    }
    function irDrag(dd) {
      // An IR-list slot, or a model dragged from the picker (a favorite this editor wrote); not a
      // block being moved ('sfoL').
      if (dd.isNull() || !find(dd, SFOL).isNull()) return false;
      return !find(dd, LBIR).isNull() || (!!chainDrag?.file && dd.equals(chainDrag.dd) && !find(dd, XGFI).isNull());
    }
    function zoneList(vector) {
      const data = vector.add(8).readPointer(), begin = vector.add(0x10).readS32(), end = vector.add(0x14).readS32();
      const list = [];
      for (let i = begin; i < end && i - begin < 64; i++) {
        const at = data.add(i * 0x20);
        list.push({at, l: at.readS32(), t: at.add(4).readS32(), r: at.add(8).readS32(), b: at.add(12).readS32(),
                   obj: at.add(0x10).readPointer(), mode: at.add(0x14).readS32(), row: at.add(0x18).readS32(), col: at.add(0x1c).readS32()});
      }
      return list;
    }
    function hit(D, x, y) {
      // As DragOver's own test: the first zone in order that is not off and holds the point.
      return zoneList(D.add(0x1c)).find(z => z.mode !== -1 && x >= z.l - 1 && x < z.r + 1 && y >= z.t - 1 && y < z.b + 1) || null;
    }
    function chosen(D) {
      return {obj: D.add(0x44).readPointer(), mode: D.add(0x48).readS32(), row: D.add(0x4c).readS32(), col: D.add(0x50).readS32()};
    }
    const sameZone = (a, b) => !!a && !!b && a.obj.equals(b.obj) && a.mode === b.mode && a.row === b.row && a.col === b.col;
    function undim() {
      if (!dimmed) return;
      try { dimItem(dimmed, 0, 1); } catch (_) { /* drawing only */ }
      dimmed = null;
    }
    let roomSeen = {key: '', at: 0, full: null};
    function namDropFull(D, entry) {
      // Why a NAM capture dragged here (an 'lbir' NAM slot) cannot become a NAM block on this DSP,
      // else null: over a NAM block it only changes the capture; over another block that block's
      // load is freed (namRoomOn). Kept for 300 ms, as DragOver runs on every move.
      if (!entry || entry.isNull() || entry.readU32() !== LBIR) return null;
      const slot = entry.add(4).readPointer().readU32() + 1;
      const dsp = D.add(0x10).readS32(), mode = D.add(0x48).readS32(), row = D.add(0x4c).readS32(), col = D.add(0x50).readS32();
      const key = `${slot}:${dsp}:${mode}:${row}:${col}`, now = Date.now();
      if (key === roomSeen.key && now - roomSeen.at < 300) return roomSeen.full;
      let full = null;
      if (namView.isNamSlot(slot)) {
        const object = cellAt(D.add(0x14).readPointer(), row, col);
        const empty = object.isNull() || object.readU32() !== 0 || cellEmpty(object);
        let replaced = null, keeps = false;
        if (!empty && mode === REPLACE) {
          const name = blockName(object);
          const seen = name ? targetBinding?.peekModel?.(D.add(8).readPointer(), dsp, name) : null;
          replaced = seen ? seen.model : null;
          keeps = replaced === 'HD2_AmpNAMLite';
        }
        full = keeps ? null : namRoomOn(dsp, replaced);
      }
      roomSeen = {key, at: now, full};
      return full;
    }

    Interceptor.attach(base.add(0x413d70), {
      onEnter(args) {
        this.ir = false;
        try {
          if (!irDrag(args[4])) return;
          const D = this.context.ecx;
          this.ir = true; this.D = D; this.info = args[6]; this.dd = args[4]; overD = D;
          let state = held.get(D.toString());
          if (!state) { state = {point: Memory.alloc(8), has: false, pending: null}; held.set(D.toString(), state); }
          if ((args[1].toInt32() & 0xff) !== 0 || D.add(0x18).readPointer().isNull()) { state.has = false; state.pending = null; return; }
          const local = args[2], x = local.readS32(), y = local.add(4).readS32(), now = Date.now();
          const zone = hit(D, x, y), current = chosen(D);
          if (zone && sameZone(zone, current)) {
            state.point.writeS32(x); state.point.add(4).writeS32(y); state.has = true; state.pending = null;
            return;
          }
          if (!zone || !state.has) { state.pending = null; return; }
          if (!state.pending || !sameZone(state.pending.zone, zone)) state.pending = {zone, since: now};
          const wait = zone.mode === REPLACE ? DWELL_REPLACE : DWELL_INSERT;
          if (now - state.pending.since < wait && sameZone(hit(D, state.point.readS32(), state.point.add(4).readS32()), current)) {
            args[2] = state.point;           // HX Edit still sees the pointer on the current zone
          } else state.pending = null;
        } catch (error) {
          this.ir = false;
          send({type: 'toneTrace', reason: 'drop-replace', stage: 'over-skipped', error: String(error.message)});
        }
      },
      onLeave() {
        if (!this.ir) return;
        overD = NULL;
        try {
          const D = this.D, effect = this.info.readU8();
          if (effect !== 1 && effect !== 2) dropHint = 'none';
          else if (D.add(0x48).readS32() === REPLACE) dropHint = 'replace';
          else {
            const object = cellAt(D.add(0x14).readPointer(), D.add(0x4c).readS32(), D.add(0x50).readS32());
            dropHint = object.isNull() || object.readU32() !== 0 || cellEmpty(object) ? 'place' : 'insert';
          }
          // No room on this DSP for the NAM block a drop would make: the drag box greys out.
          if (dropHint !== 'none') { try { if (namDropFull(D, find(this.dd, LBIR))) dropHint = 'full'; } catch (_) { /* HX Edit decides */ } }
          if (dropHint !== 'full' && D.add(0x48).readS32() === REPLACE && (effect === 1 || effect === 2) && !D.add(0x18).readPointer().isNull()) {
            const item = itemAt(D.add(0xc).readPointer(), D.add(0x4c).readS32(), D.add(0x50).readS32());
            if (!item.isNull() && !item.equals(dimmed)) { undim(); dimItem(item, 1, 1); dimmed = item; }
          } else undim();
        } catch (_) { dimmed = null; }
      },
    });

    Interceptor.attach(base.add(0x40e730), {
      onEnter(args) { this.on = !overD.isNull(); this.vector = args[3]; },
      onLeave() {
        if (!this.on) return;
        try {
          const D = overD, editor = D.add(8).readPointer(), dsp = D.add(0x10).readS32(), clone = D.add(0x18).readPointer();
          const byCell = new Map();
          for (const zone of zoneList(this.vector)) {
            const key = `${zone.row}:${zone.col}`;
            byCell.set(key, [...(byCell.get(key) || []), zone]);
          }
          const centres = [];
          for (const zones of byCell.values()) {
            if (zones.length !== 2) continue;
            const [left, right] = zones[0].l <= zones[1].l ? zones : [zones[1], zones[0]];
            if (![2, -1].includes(left.mode) || ![3, -1].includes(right.mode)) continue;
            const object = clone.isNull() ? NULL : cellAt(clone, left.row, left.col);
            if (object.isNull() || object.readU32() !== 0 || cellEmpty(object)) continue;
            target.writeU32(5); target.add(4).writeU32(0); target.add(8).writeU32((left.row << 16) | left.col); target.add(12).writeU32(0);
            if (canPaste(editor, 2, dsp, target, 0) < 0) continue;
            const half = Math.min(left.r - left.l, right.r - right.l), middle = left.r, edge = Math.round(half * 2 * EDGE);
            let from = middle - half + edge, to = middle + half - edge;
            if (left.mode === -1 && right.mode === -1) { from = left.l; to = right.r; }   // no insert here: all replace
            else { left.at.add(8).writeS32(from); right.at.writeS32(to); }
            centres.push({from, to, t: left.t, b: left.b, obj: left.obj, row: left.row, col: left.col});
          }
          for (const zone of centres) {           // after the edits: a push may move the records
            record.writeS32(zone.from); record.add(4).writeS32(zone.t); record.add(8).writeS32(zone.to); record.add(12).writeS32(zone.b);
            record.add(0x10).writePointer(zone.obj); record.add(0x14).writeS32(REPLACE);
            record.add(0x18).writeS32(zone.row); record.add(0x1c).writeS32(zone.col);
            pushZone(this.vector, record);
          }
        } catch (error) {
          send({type: 'toneTrace', reason: 'drop-replace', stage: 'zones-skipped', error: String(error.message)});
        }
      },
    });

    Interceptor.replace(base.add(0x265df0), new NativeCallback(function (model, row, col, before) {
      // The replace zone: no gap (HX Edit then lays the blocks out where they are).
      try {
        if (!overD.isNull() && model.equals(overD.add(0x18).readPointer()) && overD.add(0x48).readS32() === REPLACE) return 1;
      } catch (_) { /* fall through to HX Edit */ }
      return openGap(model, row, col, before);
    }, 'uint8', ['pointer', 'int', 'int', 'int'], 'thiscall'));

    Interceptor.attach(base.add(0x413a90), { onEnter() { undim(); } });
    // The block glide 0x3b5cc0(item; RECT*, ms, easing, flag): shorter during these drags only.
    Interceptor.attach(base.add(0x3b5cc0), {
      onEnter(args) { if (!overD.isNull() && args[1].toInt32() === 0xaf) args[1] = ptr(GLIDE_MS); },
    });

    function pickerCaptureDrop(D, entry, info) {
      // A capture row dragged from the block picker ('lbir', its slot): onto an empty cell or over a
      // block, the NAM block is made or changed in place, as the picker changes models (setModel /
      // addModel, then its capture with setIndex) - not HX Edit's IR paste and a conversion after it,
      // which took seconds. Between blocks it stays HX Edit's insert. true: handled here.
      const slot = entry.add(4).readPointer().readU32() + 1;
      if (!namView.isNamSlot(slot)) return false;
      const editor = D.add(8).readPointer(), dsp = D.add(0x10).readS32();
      const model = D.add(0x14).readPointer(), row = D.add(0x4c).readS32(), col = D.add(0x50).readS32();
      const object = cellAt(model, row, col);
      const empty = object.isNull() || object.readU32() !== 0 || cellEmpty(object);
      if (!empty && D.add(0x48).readS32() !== REPLACE) return false;
      const name = empty ? '' : blockName(object);
      const seen = name ? targetBinding.peekModel(editor, dsp, name) : null;
      info.writeU8(0);                         // HX Edit's own drop only tidies up
      namView.clearIrPick?.();
      if (seen && seen.model === 'HD2_AmpNAMLite') {
        const shown = cursorController();
        if (!shown.isNull() && controllerReady(shown) && shown.add(0x80).readS32() === dsp && block(shown) === name &&
            Number.isInteger(namView.modelSlot())) {
          // The block on screen: through its Model control, so the control shows it (one undo step).
          namView.queueSidebarAction(() => { try { namView.setSlot(slot); } catch (_) { /* refused */ } });
        } else targetBinding.setIndex(editor, dsp, name, slot);
        pickerNamFollow = {slot, dsp, editor, block: name, indexSet: true, until: Date.now() + 3000};
        send({type: 'toneTrace', reason: 'drop-replace', stage: 'capture-set', slot, block: name, ms: Date.now()});
        return true;
      }
      const room = namRoomOn(dsp, seen ? seen.model : null);
      if (room) {
        send({type: 'dspNotice', text: room});
        send({type: 'toneTrace', reason: 'drop-replace', stage: 'no-room', slot});
        return true;
      }
      let target = name;
      if (empty) {
        targetBinding.addModel(editor, dsp, row, col, 'HD2_AmpNAMLite');
        // HX Edit puts the new block in the chain a moment later: until then the cell reads empty
        // (and any other name would be another block). runNamFollow finds it by its cell.
        target = '';
      } else targetBinding.setModel(editor, dsp, name, 'HD2_AmpNAMLite');
      // Its capture at once where the block is known: a NAM block left on slot 1 (a plain IR) silences the Floor.
      if (target) targetBinding.setIndex(editor, dsp, target, slot);
      pickerNamFollow = {slot, dsp, editor, model, row, col, block: target, indexSet: !!target, until: Date.now() + 3000};
      send({type: 'toneTrace', reason: 'drop-replace', stage: empty ? 'nam-added' : 'nam-set', slot, block: target, ms: Date.now()});
      return true;
    }
    runNamFollow = function () {
      // UI thread, from the message hook, after a picker drop made or set a NAM block: its capture
      // (on the block found at the drop's cell, never "the selected one"), then, once HX Edit has
      // selected it, the lock, the Model control and the capture check.
      const follow = pickerNamFollow, now = Date.now();
      if (!follow || now < (follow.next || 0)) return;
      follow.next = now + 50;
      try {
        if (now > follow.until) {
          pickerNamFollow = null;
          if (!follow.indexSet) {
            send({type: 'toneTrace', reason: 'drop-replace', stage: 'nam-follow-lost', slot: follow.slot});
            send({type: 'dspNotice', text: `The new NAM block may not be on capture ${follow.slot}. Check its Model.`});
          }
          return;
        }
        if (!follow.block) {
          const made = cellAt(follow.model, follow.row, follow.col);
          if (made.isNull() || made.readU32() !== 0 || cellEmpty(made)) return;
          const name = blockName(made);
          const seen = name ? targetBinding.peekModel(follow.editor, follow.dsp, name) : null;
          if (!seen || seen.model !== 'HD2_AmpNAMLite') return;
          follow.block = name;
        }
        if (!follow.indexSet) {
          targetBinding.setIndex(follow.editor, follow.dsp, follow.block, follow.slot);
          follow.indexSet = true;
          send({type: 'toneTrace', reason: 'drop-replace', stage: 'nam-index-set', slot: follow.slot, block: follow.block, ms: now});
        }
        const shown = cursorController();
        if (shown.isNull() || !controllerReady(shown) || shown.add(0x80).readS32() !== follow.dsp ||
            block(shown) !== follow.block || !Number.isInteger(namView.modelSlot())) return;   // not selected yet
        pickerNamFollow = null;
        if (namView.modelSlot() !== follow.slot) namView.setSlot(follow.slot);
        setLockState(shown, 'locked', 'capture-row');
        checkCaptureLater(shown, follow.slot);
        send({type: 'toneTrace', reason: 'drop-replace', stage: 'nam-follow', slot: follow.slot, block: follow.block, ms: now});
      } catch (error) {
        pickerNamFollow = null;
        send({type: 'toneTrace', reason: 'drop-replace', stage: 'nam-follow-failed', error: String(error.message)});
      }
    };
    Interceptor.attach(base.add(0x412560), {
      onEnter(args) {
        undim();
        this.flagged = false;
        try {
          const D = this.context.ecx, entry = args[2], info = args[3];
          if (entry.isNull()) return;
          const effect = info.readU8();
          if (effect !== 1 && effect !== 2) return;
          if (entry.readU32() === XGFI && droppingDrag?.symbol) {
            // A model from the picker, onto a block or an empty cell: as the picker itself changes a
            // block's model or adds one (quick: HX Edit fills the defaults), not by pasting the
            // favorite, which writes every setting. Between blocks it is HX Edit's own insert.
            const editor = D.add(8).readPointer(), dsp = D.add(0x10).readS32();
            const row = D.add(0x4c).readS32(), col = D.add(0x50).readS32();
            const object = cellAt(D.add(0x14).readPointer(), row, col);
            const empty = object.isNull() || object.readU32() !== 0 || cellEmpty(object);
            if (D.add(0x48).readS32() === REPLACE && !empty) {
              info.writeU8(0);
              targetBinding.setModel(editor, dsp, blockName(object), droppingDrag.symbol);
              send({type: 'toneTrace', reason: 'drop-replace', stage: 'model-set', ms: Date.now()});
            } else if (empty) {
              info.writeU8(0);
              targetBinding.addModel(editor, dsp, row, col, droppingDrag.symbol);
              send({type: 'toneTrace', reason: 'drop-replace', stage: 'model-added', ms: Date.now()});
            }
            return;
          }
          const full = namDropFull(D, entry);
          if (full) {
            // The drag box said so already: nothing changes (as makeNamBlock and pickerCaptureDrop refuse).
            info.writeU8(0);
            namView.clearIrPick?.();
            send({type: 'dspNotice', text: full});
            send({type: 'toneTrace', reason: 'drop-replace', stage: 'no-room'});
            return;
          }
          if (entry.readU32() === LBIR && droppingDrag?.fromPicker && pickerCaptureDrop(D, entry, info)) return;
          if (D.add(0x48).readS32() !== REPLACE || entry.readU32() !== LBIR) return;
          const slot = entry.add(4).readPointer().readU32() + 1;
          const editor = D.add(8).readPointer(), dsp = D.add(0x10).readS32();
          const object = cellAt(D.add(0x14).readPointer(), D.add(0x4c).readS32(), D.add(0x50).readS32());
          const name = object.isNull() ? '' : blockName(object);
          const seen = name ? targetBinding.peekModel(editor, dsp, name) : null;
          if (namView.isNamSlot(slot)) {
            info.writeU8(0);                     // HX Edit's own drop only tidies up
            namView.clearIrPick?.();
            if (seen && seen.model === 'HD2_AmpNAMLite') {
              targetBinding.setIndex(editor, dsp, name, slot);   // one "set parameter" step
              send({type: 'toneTrace', reason: 'drop-replace', stage: 'capture-set', slot, block: name});
              return;
            }
            const capture = namView.installedCaptures().find(item => item.slot === slot);
            setTimeout(() => namView.queueSidebarAction(() => {
              if (namView.clickChainAtCursor()) irReplace = {slot, gear: capture ? capture.gear : '', until: Date.now() + 2500};
              else send({type: 'dspNotice', text: 'Drop the slot on a block in the chain.'});
            }), 80);
            send({type: 'toneTrace', reason: 'drop-replace', stage: 'select-block', slot, block: name});
            return;
          }
          dropReplace = true; this.flagged = true;  // a plain IR: HX Edit's own paste, as a replace
          send({type: 'toneTrace', reason: 'drop-replace', stage: 'replace', slot, block: name});
        } catch (error) {
          send({type: 'toneTrace', reason: 'drop-replace', stage: 'drop-skipped', error: String(error.message)});
        }
      },
      onLeave() { if (this.flagged) dropReplace = false; },
    });

    Interceptor.attach(base.add(0x513620), {
      onEnter(args) {
        // HX Edit's paste (pasteboard, dsp, target, flags): the low nibble 0 replaces the block.
        if (dropReplace) args[3] = ptr((args[3].toUInt32() & ~0xf) >>> 0);
      },
    });
    send({type: 'toneTrace', reason: 'drop-replace', stage: 'installed'});
  }
  try { installDropReplace(); } catch (error) { send({type: 'toneTrace', reason: 'drop-replace', stage: 'skipped', error: String(error.message)}); }

  // --- A capture dragged from the NAM tab or the library, through HX Edit's own drop handling ---
  // For a capture on the Floor, the drag behaves as one from HX Edit's IR list: the same zones,
  // sliding preview, replace zone and Undo. HX Edit's window-level drag functions are driven
  // directly (no OLE) with an 'lbir' payload (the slot's
  // row): over 0x38af00(W; sameSource, POINT* client, mods, dd, allowed, DropInfo*) while the button is
  // down, then drop 0x38ae50(W; sameSource, POINT*, Item*, DropInfo*) or leave 0x38aec0(W; sameSource,
  // dd). They run from a Win32 timer callback, not from inside a hook, so the replace-zone hooks above
  // see them. The pointer comes from GetCursorPos: the drag's own window keeps the mouse meanwhile.
  const chainDragGuards = {
    '3b8bf0': '53568bd9578b7b548bb7b400000085f6', '38af00': '558bec8b451c83ec105356578bf9c700',
    '38ae50': '558bec83ec1032c056578bf98bb78400', '38aec0': '558bec568bf18b968400000085d27428',
    '3776a0': '558bec538bd956578b7d088b3385f674', '377740': '558bec83ec088bc1538945f88b1885db',
  };
  let chainDrag = null;              // {timer, window, dd, point, info, slot}
  let chainDragTaken = null;         // {identity, at}: this drag's release is HX Edit's, not the panel's drop
  let pickerNamFollow = null;       // {slot, dsp, block, until}: a capture a picker drop made or set, for its follow-up
  let droppingDrag = null;           // the chain drag being dropped (chainDrag is already cleared then)
  let dropHint = 'none';             // what a drop here would do: place | replace | insert | none (DragOver)
  const chainDragApi = (() => {
    try {
      if (!profile.navigation || !guardsMatch(chainDragGuards)) return null;
      const user32 = Process.getModuleByName('user32.dll');
      const api = (name, ret, args) => new NativeFunction(user32.getExportByName(name), ret, args, 'stdcall');
      return {
        windowOf: new NativeFunction(base.add(0x3b8bf0), 'pointer', ['pointer'], 'thiscall'),
        over: new NativeFunction(base.add(0x38af00), 'int', ['pointer', 'int', 'pointer', 'int', 'pointer', 'int', 'pointer'], 'thiscall'),
        drop: new NativeFunction(base.add(0x38ae50), 'int', ['pointer', 'int', 'pointer', 'pointer', 'pointer'], 'thiscall'),
        leave: new NativeFunction(base.add(0x38aec0), 'int', ['pointer', 'int', 'pointer'], 'thiscall'),
        add: new NativeFunction(base.add(0x3776a0), 'pointer', ['pointer', 'uint', 'pointer', 'uint', 'int', 'uint'], 'thiscall'),
        clear: new NativeFunction(base.add(0x377740), 'void', ['pointer'], 'thiscall'),
        find: new NativeFunction(base.add(0x3777f0), 'pointer', ['pointer', 'uint'], 'thiscall'),
        setTimer: api('SetTimer', 'uint', ['pointer', 'uint', 'uint', 'pointer']),
        killTimer: api('KillTimer', 'int', ['pointer', 'uint']),
        keyState: api('GetAsyncKeyState', 'int16', ['int']),
      };
    } catch (_) { return null; }
  })();
  const chainDragTick = chainDragApi && new NativeCallback(() => {
    // UI thread, from the message loop (WM_TIMER), outside any hook.
    const drag = chainDrag;
    if (!drag) return;
    const native = chainDragApi;
    try {
      const at = namView.cursorInWindow();
      if (at) { drag.point.writeS32(at.x); drag.point.add(4).writeS32(at.y); }
      if (drag.failed) {
        send({type: 'dspNotice', text: 'HX Edit could not load that model to drag it.'});
        endChainDrag(false);
        return;
      }
      const escape = (native.keyState(0x1b) & 0x8000) !== 0, down = (native.keyState(0x01) & 0x8000) !== 0;
      if (down && !escape && Date.now() < drag.until) {
        if (at) { drag.info.writeU64(0); native.over(drag.window, 0, drag.point, 0, drag.dd, 3, drag.info); drag.hovered = true; }
        const hint = at && [1, 2].includes(drag.info.readU8()) ? dropHint : 'none';
        if (hint !== drag.hint) { drag.hint = hint; send({type: 'dropHint', hint}); }
        return;
      }
      endChainDrag(!escape && drag.hovered);
    } catch (error) {
      send({type: 'toneTrace', reason: 'chain-drag', stage: 'tick-failed', error: String(error.message)});
      endChainDrag(false);
    }
  }, 'void', ['pointer', 'uint', 'uint', 'uint'], 'stdcall');
  function endChainDrag(dropIt) {
    const drag = chainDrag, native = chainDragApi;
    if (!drag) return;
    chainDrag = null;
    droppingDrag = drag;               // the drop hook still needs to know what is dropped
    if (drag.ghost) send({type: 'pickerGhost', hide: true});
    const selectDropped = drag.fromPicker;
    if (drag.hint !== 'none') send({type: 'dropHint', hint: 'none'});
    try { native.killTimer(NULL, drag.timer); } catch (_) { /* gone with the window */ }
    try {
      const effect = drag.info.readU8();
      if (dropIt && (effect === 1 || effect === 2)) {
        const entry = native.find(drag.dd, drag.info.add(4).readU32());
        if (!entry.isNull()) native.drop(drag.window, 0, drag.point, entry, drag.info);
        else native.leave(drag.window, 0, drag.dd);
        send({type: 'toneTrace', reason: 'chain-drag', stage: 'dropped', slot: drag.slot, effect, ms: Date.now()});
        // From the picker: the dropped block is selected, as a click on it would (after HX Edit has it).
        if (selectDropped && !irReplace) {
          setTimeout(() => namView.queueSidebarAction(() => { namView.clickChainAtCursor(); }), 120);
          // That click focuses the chain; the picker list gets the focus back (its outline, the arrow keys).
          setTimeout(() => namView.queueSidebarAction(() => {
            keepFocus = {list: null, grid: !!drag.fromGrid, until: Date.now() + 600};
            holdFocus();
          }), 400);
        }
      } else {
        native.leave(drag.window, 0, drag.dd);
        if (!dropIt) namView.clearIrPick?.();
        send({type: 'toneTrace', reason: 'chain-drag', stage: 'left', slot: drag.slot});
      }
      drag.window.add(0x84).writePointer(NULL);   // no drop target left over
    } catch (error) {
      send({type: 'toneTrace', reason: 'chain-drag', stage: 'end-failed', error: String(error.message)});
    } finally {
      droppingDrag = null;
      try { native.clear(drag.dd); } catch (_) { /* freed with the drag */ }
    }
  }
  function startChainDrag(what) {
    // UI thread, the button down. what: a library capture's identity (a string), {slot} (a NAM
    // capture slot) or {file} (a one-block favorite). false: not possible here (the caller then
    // drops the old way, if it has one).
    if (!chainDragApi || chainDrag || !profile.namCategory) return false;
    const identity = typeof what === 'string' ? what : null;
    const slot = identity ? namView.verifiedCaptureSlot(identity) : what && what.slot;
    const file = !identity && what && typeof what.file === 'string' ? what.file : null;
    if (!file && (!Number.isInteger(slot) || slot < 1 || slot >= AUDITION_SLOT || !namView.isNamSlot(slot))) return false;
    const panel = namView.chainPanel?.();
    if (!panel || panel.isNull()) return false;
    const window = chainDragApi.windowOf(panel);
    if (window.isNull() || (chainDragApi.keyState(0x01) & 0x8000) === 0) return false;
    const dd = Memory.alloc(4);
    dd.writePointer(NULL);
    let data;
    if (file) {
      // HX Edit's favorite item: the path as UTF-8 with its NUL, flag 1, file type 7 (L6ModelFavorite).
      data = Memory.allocUtf8String(file);
      chainDragApi.add(dd, XGFI, data, data.readUtf8String().length + 1, 1, 7);
    } else {
      data = Memory.alloc(4); data.writeU32(slot - 1);
      chainDragApi.add(dd, LBIR, data, 4, 0, 0);
      namView.setIrPick(slot);
    }
    const timer = chainDragApi.setTimer(NULL, 0, 15, chainDragTick);
    if (!timer) { chainDragApi.clear(dd); return false; }
    chainDrag = {timer, window, dd, data, slot: file ? null : slot, file, symbol: file ? what.symbol : null,
                 point: Memory.alloc(8), info: Memory.alloc(8), hovered: false, failed: false, hint: 'none',
                 until: Date.now() + 60000};
    if (identity) chainDragTaken = {identity, at: Date.now()};
    send({type: 'toneTrace', reason: 'chain-drag', stage: 'started', slot: file ? null : slot, model: file ? what.symbol : null, ms: Date.now()});
    return true;
  }
  function chainDragTook(identity) {
    // The panel's drop for this capture is already done by HX Edit's own drop handling.
    const taken = chainDragTaken;
    chainDragTaken = null;
    return !!taken && taken.identity === identity && Date.now() - taken.at < 120000;
  }

  // --- Press and hold in the block picker: drag that model onto the chain ----------------------
  // The picker changes the selected block's model the moment a row (or grid tile) is pressed: it
  // sends 'sbl!' / 'srg!' from its mouse-down (0x402e70 list, 0x37aba0 grid). That event is held
  // here while the button is down. Released without moving: it is replayed, so a click is a click.
  // Moved past the drag distance: the picker's old selection is put back and the row's model is
  // dragged onto the chain through HX Edit's own drop handling (startChainDrag): a NAM capture row
  // as its slot, any other model as a one-block favorite file. The main window holds
  // the mouse from the press, so moves and the release reach it.
  const pickerGuards = {
    '402e70': '558bec83ec0c5357ff75108b7d088bd9', '37aba0': '558bec5156576a006a006a108bf9e82d',
    '380b50': '558bec83ec14538bd9568b7508578b03', '37a460': '558bec83ec105356578bf933f683cbff',
    '503b60': '558bec6aff68????????64a100000000', '37fc20': '558bec81eccc010000a1????????33c5',
    '3bc160': '558bec568bf18b4654f6405801744157', '38ad60': '8b4178c3cccccccccccccccccccccccc',
  };
  // Keyboard focus: a window's focused widget is [window+0x78] (0x38ad60); 0x3bc160(widget; focus, why)
  // gives it focus, as a press does (0x38b3d7). A capture applied in a NAM category moves the focus to
  // the block's Model control, which left the arrow keys dead in the list: it is handed back.
  const focusedIn = new NativeFunction(base.add(0x38ad60), 'pointer', ['pointer'], 'thiscall');
  const giveFocus = new NativeFunction(base.add(0x3bc160), 'void', ['pointer', 'int', 'int'], 'thiscall');
  let keepFocus = null;               // {list, until}: after an apply from a list that had the focus
  function listHasFocus(list) {
    try { const window = chainDragApi.windowOf(list); return !window.isNull() && focusedIn(window).equals(list); }
    catch (_) { return false; }
  }
  function holdFocus() {
    const keep = keepFocus;
    if (!keep) return;
    if (Date.now() > keep.until) { keepFocus = null; return; }
    try {
      let list = keep.list;
      if (!list) {                     // after a drop: the picker of the block now selected
        const controller = cursorController();
        list = controller.isNull() ? NULL : controller.add(keep.grid ? 0x60 : 0x58).readPointer();
      }
      if (!list.isNull() && !listHasFocus(list)) giveFocus(list, 1, 1);
    } catch (_) { keepFocus = null; }
  }
  const XGFI = 0x58474649, DRAG_DISTANCE = 4;
  let pickerPress = null;            // {list, grid, index, label, model, previous, start, inside, held, dragging}
  let pickerReplaying = false, lastDown = null;
  // A single click only selects a row; a double-click applies it (the owner's choice, November 19).
  // pendingSelect: the click's held event, until a double-click applies it. While it waits, the row of
  // what the block really plays is drawn in yellow (installPickerDrag's painter hook).
  let pendingSelect = null;
  // Keyboard: arrows only select (keyNav: a WM_KEYDOWN being dispatched); Enter applies the waiting row.
  let keyNav = false, enterQueued = null, enterTimer = 0, pickerPresses = 0;
  let pressSeen = null, releaseSeen = false;   // a press / release, handled when the next message comes
  const pickerEnterTick = chainDragApi && new NativeCallback(() => {
    // UI thread, from the message loop (WM_TIMER), outside any hook.
    const target = enterQueued;
    enterQueued = null;
    try { chainDragApi.killTimer(NULL, enterTimer); } catch (_) { /* already gone */ }
    if (!target || target !== pendingSelect) return;
    send({type: 'toneTrace', reason: 'picker-drag', stage: 'enter'});
    applyPickerRow(target);
  }, 'void', ['pointer', 'uint', 'uint', 'uint'], 'stdcall');
  const ACTIVE_RGB = [255, 205, 60];
  let pickerPlaying = {at: 0, key: '', symbol: null, slot: null};
  function playingNow(controller) {
    // What the picker's block plays (its model, and a NAM block's capture slot), read from the edit
    // buffer at most every 250 ms while the picker paints.
    const now = Date.now(), key = lockKey(controller);
    if (key === pickerPlaying.key && now - pickerPlaying.at < 250) return pickerPlaying;
    let symbol = null, slot = null;
    try {
      const seen = targetBinding?.peekModel?.(controller.add(0x34).readPointer(), controller.add(0x80).readS32(), block(controller));
      symbol = seen ? seen.model : null;
      slot = symbol === 'HD2_AmpNAMLite' ? namBlockSlot(controller) : null;
    } catch (_) { symbol = null; }
    pickerPlaying = {at: now, key, symbol, slot};
    return pickerPlaying;
  }
  const SECOND_CLICK_MS = 900;        // a slower second click on the waiting row still applies it
  const doubleEvent = 0x216c6264;     // 'dbl!': the list's double-click (0x3814c0, vtbl+0x148); data = the row under the pointer
  const gridDoubleEvent = 0x21677264; // 'drg!': grid view's double-click (0x37ab70, its vtbl+0x148); data = the tile
  function rowIsPlaying(controller, row, label, marking) {
    // marking: for the yellow row (a NAM block's capture row, not its NAM A2 Lite row). Otherwise: the
    // row already is the block's model, so choosing it again must change nothing.
    const now = playingNow(controller);
    const capture = captureRows.get(label);
    if (capture) return now.slot !== null && capture.slot === now.slot;
    if (marking && now.slot !== null && label.trimEnd() !== label) return false;
    const model = row.add(0x10c).readPointer();
    if (model.compare(ptr(0x10000)) < 0) return false;
    if (model.equals(controller.add(0xa4).readPointer())) return true;   // the record HX Edit itself selects by (0x3804f0)
    return !!now.symbol && modelSymbol(model) === now.symbol;
  }
  function heldPickerEvent(controller, event, sender, data) {
    // From onModelEvent: true when this press-time event is held for now.
    if (pickerReplaying) return false;
    if (event === doubleEvent || event === gridDoubleEvent) {
      // HX Edit's own double-click carries the row (tile) under the pointer and does not depend on the
      // selection: that row is applied, whatever moved the selection since the first click.
      const grid = event === gridDoubleEvent;
      if (!sender.equals(controller.add(grid ? 0x60 : 0x58).readPointer())) return false;
      const label = !data || data.isNull() ? null : data.readUtf8String();
      let pending = pendingSelect && pendingSelect.list.equals(sender) ? pendingSelect : null;
      if (pending && pending.label !== label) pending = null;            // another row is under the pointer now
      send({type: 'toneTrace', reason: 'picker-drag', stage: 'double-click', grid, waiting: !!pending});
      if (label === null) return false;
      // No click waiting: the row was already selected. Only a row that is not the block's model is applied.
      if (!pending && rowIsPlaying(controller, data, label, false)) return false;
      applyPickerRow({controller, list: sender, grid, label, key: lockKey(controller)});
      return false;                    // HX Edit has no handling of its own for these two events (0x217120)
    }
    const press = pickerPress;
    if (press && press.inside && sender.equals(press.list) && (event === selectionEvent || event === gridSelectEvent)) {
      press.held = {controller, event, sender};
      send({type: 'toneTrace', reason: 'picker-drag', stage: 'held'});
      // The click waits from its press: HX Edit's own mouse handling does not always let its release
      // reach DispatchMessageW, so waiting from the release missed most clicks (and their yellow row).
      startWaiting(controller, press.list, press.grid, press.label, press);
      return true;
    }
    const picked = event === selectionEvent || event === gridSelectEvent;
    if (keyNav && picked && sender.equals(controller.add(event === gridSelectEvent ? 0x60 : 0x58).readPointer())) {
      // An arrow key moved the selection: like a single click it only selects; Enter (or a double-click) applies.
      const grid = event === gridSelectEvent, row = grid ? gridSelected(sender) : selectedRow(sender);
      if (!row.isNull()) {
        startWaiting(controller, sender, grid, row.readUtf8String(), null);
        send({type: 'toneTrace', reason: 'picker-drag', stage: 'key-held', grid});
        return true;
      }
    }
    // Any other choice in this picker (a category, a side, another row) ends a waiting click.
    if (pendingSelect && !choosingSide && controller.equals(pendingSelect.controller) && (picked || event === subcategoryChanged)) {
      let same = false;
      try {
        const row = !picked ? NULL : event === gridSelectEvent ? gridSelected(sender) : selectedRow(sender);
        same = sender.equals(pendingSelect.list) && !row.isNull() && row.readUtf8String() === pendingSelect.label;
      } catch (_) { same = false; }
      if (!same) pendingSelect = null;
    }
    return false;
  }
  function startWaiting(controller, list, grid, label, press) {
    // A row chosen but not applied: it stays selected, the block's own row turns yellow (installPickerDrag).
    reportYellow(pendingSelect);
    pendingSelect = {controller, list, grid, label, key: lockKey(controller), at: Date.now(), press};
    if (grid) return;
    try { aimYellow(pendingSelect); } catch (_) { /* presentation only */ }
    try { redrawList(list); } catch (_) { /* presentation only */ }
  }
  function applyPickerRow(target) {
    // UI thread. The row becomes the block's model as a stock click on it would: it is made the
    // selected row first, because HX Edit (0x217120 -> 0x380780) and onModelEvent both apply the
    // list's selected row, not the row of the event.
    reportYellow(pendingSelect);
    pendingSelect = null;
    if (pickerPress) pickerPress.held = null;   // its release (if seen) must not wait on the applied row
    const skipped =why => send({type: 'toneTrace', reason: 'picker-drag', stage: 'apply-skipped', why});
    try {
      const {controller, list, grid, label} = target;
      if (!controllerReady(controller) || !controller.add(grid ? 0x60 : 0x58).readPointer().equals(list) ||
          lockKey(controller) !== target.key) return skipped('context');
      if (grid) {                      // HX Edit reads the grid's clicked tile (0x37a010)
        const tile = gridSelected(list);
        if (tile.isNull() || tile.readUtf8String() !== label) return skipped('tile');
      } else {
        const index = rowIndex(list, text => text === label);
        if (index < 0) return skipped('row-gone');
        const selected = selectedRow(list);
        if (selected.isNull() || selected.readUtf8String() !== label) { clearSelection(list, 0, 0); selectRow(list, 1, index, 0, 0); }
      }
      const focused = listHasFocus(list);
      pickerReplaying = true;
      onModelEvent(controller, grid ? gridSelectEvent : selectionEvent, list, NULL, NULL);
      send({type: 'toneTrace', reason: 'picker-drag', stage: 'applied', row: label.trim(), focused});
      // The arrow keys keep working in the list after an apply (a capture moves the focus, at once or a
      // moment later): for 1.5 s, unless a press elsewhere takes it.
      if (focused) { keepFocus = {list, until: Date.now() + 1500}; holdFocus(); }
    } catch (error) {
      send({type: 'toneTrace', reason: 'picker-drag', stage: 'apply-failed', error: String(error.message)});
    } finally {
      pickerReplaying = false;
      // The block's previous row was drawn yellow while the click waited: repaint it plain.
      try { if (!target.grid) redrawList(target.list); } catch (_) { /* presentation only */ }
    }
  }
  function aimYellow(pending) {
    // UI thread, outside painting: which row of the waiting click's list the block plays, found once
    // every 250 ms. The painter then only compares labels. One 'yellow-target' line per waiting click.
    if (!pending || pending.grid || Date.now() - (pending.aimedAt || 0) < 250) return;
    pending.aimedAt = Date.now();
    let found = null, rows = 0, error = null;
    try {
      for (const row of listRows(pending.list)) {
        rows++;
        if (row.isNull()) continue;
        const label = row.readUtf8String();
        if (rowIsPlaying(pending.controller, row, label, true)) { found = label; break; }
      }
    } catch (e) { error = String(e.message); }
    pending.playingLabel = found;
    if (pending.aimReported && pending.aimReported === (found || '-')) return;
    pending.aimReported = found || '-';
    let now = null, record = null;
    try { now = playingNow(pending.controller); } catch (_) { now = null; }
    try { const a4 = pending.controller.add(0xa4).readPointer(); record = a4.compare(ptr(0x10000)) > 0 ? modelSymbol(a4) : null; } catch (_) { record = null; }
    let sample = null;
    if (!found) try { sample = listRows(pending.list).slice(0, 3).map(row => [row.readUtf8String(), modelSymbol(row.add(0x10c).readPointer())]); } catch (_) { sample = null; }
    send({type: 'toneTrace', reason: 'picker-drag', stage: 'yellow-target', row: found && found.trim(), rows, error,
          symbol: now && now.symbol, slot: now && now.slot, record, sample});
  }
  // The picker's standing yellow row: what the block plays, in the model column on show, whether or
  // not it is the selected row. Found every 250 ms from the message hook; a change repaints the list.
  let shownYellow = {list: NULL, label: null, at: 0};
  function aimShown() {
    const now = Date.now();
    if (now - shownYellow.at < 250) return;
    shownYellow.at = now;
    let list = NULL, label = null;
    try {
      const controller = cursorController();
      if (!controller.isNull()) {
        list = controller.add(0x58).readPointer();
        if (!list.isNull()) for (const row of listRows(list)) {
          if (row.isNull()) continue;
          const text = row.readUtf8String();
          if (rowIsPlaying(controller, row, text, true)) { label = text; break; }
        }
      }
    } catch (_) { label = null; }
    try { followPlaying(list, label); } catch (error) { if (followReports-- > 0) send({type: 'toneTrace', reason: 'picker-drag', stage: 'follow-failed', error: String(error.message)}); }
    if (label === shownYellow.label && list.equals(shownYellow.list)) return;
    const before = shownYellow.list;
    shownYellow.list = list; shownYellow.label = label;
    try { if (!list.isNull()) redrawList(list); if (!before.isNull() && !before.equals(list)) redrawList(before); } catch (_) { /* presentation only */ }
  }
  let followReports = 40, followBlocked = null;
  function followPlaying(list, label) {
    // With no choice waiting (a single click, an arrow key), no press or drag under way, the list's
    // selection is the row the block plays: after a drop, a block change, Undo, or a waiting row
    // let go by a press elsewhere. Selection only (no event): nothing is applied.
    if (list.isNull() || !label) return;
    const why = pendingSelect ? 'waiting' : chainDrag ? 'drag' : choosingSide ? 'side' : pickerReplaying ? 'replaying' : null;
    if (why) { if (why !== followBlocked) { followBlocked = why; if (followReports-- > 0) send({type: 'toneTrace', reason: 'picker-drag', stage: 'follow-blocked', why}); } return; }
    followBlocked = null;
    // A press still held (its release is not always seen, so pickerPress alone can be stale).
    if (pickerPress && chainDragApi && (chainDragApi.keyState(0x01) & 0x8000) !== 0) return;
    const selected = selectedRow(list);
    if (!selected.isNull() && selected.readUtf8String() === label) return;
    const index = rowIndex(list, text => text === label);
    if (index < 0) return;
    clearSelection(list, 0, 0);
    selectRow(list, 1, index, 0, 0);
    redrawList(list);
    if (followReports-- > 0) send({type: 'toneTrace', reason: 'picker-drag', stage: 'selection-followed', row: label.trim(),
                                   was: selected.isNull() ? null : selected.readUtf8String().trim()});
  }
  function reportYellow(pending) {
    // One line per waiting click that never showed its yellow row: what the painter saw.
    if (!pending || pending.grid || pending.yellow || pending.reported) return;
    pending.reported = true;
    let now = null;
    try { now = playingNow(pending.controller); } catch (_) { now = null; }
    send({type: 'toneTrace', reason: 'picker-drag', stage: 'yellow-missed', painted: pending.painted || 0,
          waited: Date.now() - pending.at, symbol: now && now.symbol, slot: now && now.slot, error: pending.paintError || null});
  }
  function modelSymbol(model) {
    // A catalog model's symbol: String at +0x24 {vtable, rep}; rep: length +4, characters* +0x10.
    const rep = model.add(0x28).readPointer();
    if (rep.isNull()) return '';
    const length = rep.add(4).readU32(), chars = rep.add(0x10).readPointer();
    return length > 0 && length < 96 && !chars.isNull() ? chars.readUtf8String(length) : '';
  }
  function dragFavorite(symbol) {
    // A one-block favorite for HX Edit's own drop: @model and @type, every other setting at its default.
    const type = profile.modelTypes ? profile.modelTypes[symbol] : undefined;
    if (!profile.dragFavorite || !Number.isInteger(type) || type < 0) return null;
    const favorite = {schema: 'L6ModelFavorite', version: 1, data: {device: 0x210001, device_version: 0x03800000,
      meta: {name: symbol}, favorite: {slot0: {'@model': symbol, '@type': type}}}};
    const file = new File(profile.dragFavorite, 'wb');
    file.write(JSON.stringify(favorite));
    file.close();
    return profile.dragFavorite;
  }
  function startPickerDrag(press) {
    // UI thread, the button still down: the picker keeps the block's model as it was.
    press.dragging = true;
    const held = press.held;
    press.held = null;
    if (pendingSelect && pendingSelect.press === press) pendingSelect = null;   // a drag, not a click
    try {
      if (!press.grid && held) {
        clearSelection(press.list, 0, 0);
        if (press.previous >= 0) selectRow(press.list, 1, press.previous, 0, 0);
        redrawList(press.list);
      }
    } catch (_) { /* presentation only */ }
    const capture = captureRows.get(press.label);
    let started = false;
    if (capture) started = startChainDrag({slot: capture.slot});
    else {
      const symbol = press.model.isNull() ? '' : modelSymbol(press.model);
      const path = symbol ? dragFavorite(symbol) : null;
      started = !!path && startChainDrag({file: path, symbol});
      if (!path) send({type: 'toneTrace', reason: 'picker-drag', stage: 'no-favorite', symbol});
    }
    send({type: 'toneTrace', reason: 'picker-drag', stage: started ? 'started' : 'not-started', capture: !!capture});
    if (!started || !chainDrag) return;
    // The model's picture and name beside the pointer, as the NAM tab shows its captures: drawn by
    // the DSP meter's helper (a window of its own), from HX Edit's res/icons_models.
    let image = null;
    try {
      const rep = press.model.isNull() ? NULL : press.model.add(0x1c).readPointer();   // its picture's file name
      image = rep.isNull() ? null : rep.add(0x10).readPointer().readCString();
    } catch (_) { image = null; }
    const icon = capture ? namView.installedCaptures().find(item => item.slot === capture.slot)?.icon : null;
    chainDrag.ghost = true; chainDrag.fromPicker = true; chainDrag.fromGrid = !!press.grid;
    send({type: 'pickerGhost', name: press.label.trim(), image: icon ? `NAMCAP_${icon}.png` : image});
  }
  (function installPickerDrag() {
    try {
      if (!profile.navigation || !chainDragApi || !guardsMatch(pickerGuards)) return;
      const listHit = new NativeFunction(base.add(0x380b50), 'int', ['pointer', 'pointer', 'pointer', 'pointer'], 'thiscall');
      const gridHit = new NativeFunction(base.add(0x37a460), 'int', ['pointer', 'pointer', 'pointer'], 'thiscall');
      const index = Memory.alloc(4), part = Memory.alloc(4);
      const seenDown = [false, false];
      function pressed(list, grid, point) {
        index.writeS32(-1); part.writeS32(0);
        if (grid) gridHit(list, point, index); else listHit(list, point, index, part);
        const at = index.readS32();
        if (at < 0 || at >= rowCount(list)) return null;
        const row = getIrRow(list, at);
        if (row.isNull()) return null;
        let previous = -1;
        if (!grid) {
          const selected = selectedRow(list);
          if (!selected.isNull()) previous = rowIndex(list, (_text, item) => item.equals(selected));
        }
        return {list, grid, index: at, label: row.readUtf8String(), model: row.add(0x10c).readPointer(), previous,
                start: lastDown, inside: true, held: null, dragging: false};
      }
      const onPress = grid => ({
        onEnter(args) {
          try {
            const list = this.context.ecx;
            if (!seenDown[grid ? 1 : 0]) {
              seenDown[grid ? 1 : 0] = true;
              send({type: 'toneTrace', reason: 'picker-drag', stage: 'mouse-down-seen', grid});
            }
            // The model column (+0x58) or grid (+0x60) of the block picker on show, not the category
            // list, Favorites or the librarian's lists (the same list classes).
            const owner = [cursorController(), selectedController].find(controller => controllerReady(controller) &&
              controller.add(grid ? 0x60 : 0x58).readPointer().equals(list));
            // button is an int; mods is one byte pushed as four (the upper bytes are left over).
            const button = args[1].toInt32(), mods = args[2].toInt32() & 0xff;
            if (owner) pickerPresses++;
            if (!owner || button !== 0 || mods !== 0 || !lastDown) {
              if (owner) send({type: 'toneTrace', reason: 'picker-drag', stage: 'press-ignored', button, mods, down: !!lastDown});
              return;
            }
            pickerPress = pressed(list, grid, args[0]);
            send({type: 'toneTrace', reason: 'picker-drag', stage: 'press', grid, row: pickerPress ? pickerPress.index : -1,
                  mode: grid ? null : list.add(0x1d8).readS32()});
          } catch (error) {
            pickerPress = null;
            send({type: 'toneTrace', reason: 'picker-drag', stage: 'press-failed', error: String(error.message)});
          }
        },
        onLeave() { if (pickerPress) pickerPress.inside = false; },
      });
      Interceptor.attach(base.add(0x402e70), onPress(false));
      Interceptor.attach(base.add(0x37aba0), onPress(true));
      // A favorite HX Edit could not load leaves the previous drag's content: that drag stops.
      Interceptor.attach(base.add(0x503b60), {
        onLeave(result) { if (chainDrag && chainDrag.file && result.toInt32() < 0) chainDrag.failed = true; },
      });
      Interceptor.attach(user32.getExportByName('DispatchMessageW'), {
        onEnter(args) {
          const event = args[0].add(4).readU32();
          // Work after the previous message (this hook's onLeave state does not reliably reach onLeave).
          if (keyNav && event !== 0x100) keyNav = false;
          if (pressSeen && event !== 0x201 && event !== 0x203) {
            const seen = pressSeen;
            pressSeen = null;
            if (seen.presses === pickerPresses && pendingSelect) {
              // A press anywhere but the picker's rows: the waiting row is let go (so Enter elsewhere never applies it).
              const list = pendingSelect.grid ? null : pendingSelect.list;
              pendingSelect = null;
              try { if (list) redrawList(list); } catch (_) { /* presentation only */ }
              send({type: 'toneTrace', reason: 'picker-drag', stage: 'let-go'});
            }
          }
          if (releaseSeen) { releaseSeen = false; pickerReleased(); }
          if (pickerNamFollow && runNamFollow) runNamFollow();
          if (keepFocus && event !== 0x201 && event !== 0x203) holdFocus();
          if (event !== 0x201 && event !== 0x203) { try { aimShown(); } catch (_) { /* presentation only */ } }
          if (event === 0x100) {                        // WM_KEYDOWN
            const key = args[0].add(8).readU32();
            if (key >= 0x21 && key <= 0x28) keyNav = true;   // PgUp/PgDn/End/Home/arrows: until the next message
            else if (key === 0x0d && pendingSelect && !enterQueued) {
              // Enter applies the waiting row, as a double-click does; from a timer, outside this hook.
              enterQueued = pendingSelect;
              try { enterTimer = chainDragApi.setTimer(NULL, 0, 0, pickerEnterTick); } catch (_) { enterQueued = null; }
              if (!enterTimer) enterQueued = null;
            }
            return;
          }
          if (event === 0x201 || event === 0x203) {   // a press, or the second press of a double-click
            const position = args[0].add(12).readU32();
            lastDown = {x: (position << 16) >> 16, y: position >> 16, at: Date.now()};
            if (event === 0x201) { pressSeen = {presses: pickerPresses}; keepFocus = null; }
            return;
          }
          const waiting = pendingSelect;
          if (waiting && !waiting.grid) {
            const before = waiting.playingLabel;
            try { aimYellow(waiting); } catch (_) { /* presentation only */ }
            if (waiting.playingLabel !== before) { waiting.yellow = false; waiting.redraws = 0; }
          }
          if (waiting && !waiting.grid && !waiting.yellow && (waiting.redraws || 0) < 3 &&
              Date.now() - (waiting.redrawAt || waiting.at) > 150) {
            waiting.redraws = (waiting.redraws || 0) + 1; waiting.redrawAt = Date.now();
            try { redrawList(waiting.list); } catch (_) { /* presentation only */ }
          }
          const press = pickerPress;
          if (!press || press.dragging) return;
          if (event === 0x200 && (args[0].add(8).readU32() & 1) && press.start) {
            const position = args[0].add(12).readU32(), x = (position << 16) >> 16, y = position >> 16;
            if (Math.abs(x - press.start.x) > DRAG_DISTANCE || Math.abs(y - press.start.y) > DRAG_DISTANCE) {
              try { startPickerDrag(press); } catch (error) {
                send({type: 'toneTrace', reason: 'picker-drag', stage: 'failed', error: String(error.message)});
              }
            }
          } else if (event === 0x202) releaseSeen = true;
        },
      });
      function pickerReleased() {
        {
          const press = pickerPress;
          pickerPress = null;
          if (!press || press.dragging) return;
          try {
            if (!press.held) {
              // The row was already selected: no event. HX Edit's own double-click comes as 'dbl!' /
              // 'drg!' (no press at all). A second, slower click on the row that still waits applies it too.
              const pending = pendingSelect;
              if (pending && pending.list.equals(press.list) && pending.label === press.label &&
                  Date.now() - pending.at <= SECOND_CLICK_MS) {
                send({type: 'toneTrace', reason: 'picker-drag', stage: 'second-click', after: Date.now() - pending.at});
                applyPickerRow(pending);
              }
              return;
            }
            // A single click: the row stays selected, the block unchanged until a double-click. It already
            // waits from its press (heldPickerEvent); a release seen here only repaints.
            if (pendingSelect && pendingSelect.press === press) { if (!press.grid) redrawList(press.list); return; }
            startWaiting(press.held.controller, press.list, press.grid, press.label, press);
          } catch (_) { pendingSelect = null; }
        }
      }
      // The block picker's rows are painted by the generic list painter 0x37fc20(list; gfx, RECT*, row,
      // index), which reads the list's text colour straight from [list+0x90]. While it paints the row the
      // block plays (another row being selected but not applied), that colour is yellow, then put back.
      Interceptor.attach(base.add(0x37fc20), {
        onEnter(args) {
          this.restore = null;
          const pending = pendingSelect, list = this.context.ecx;
          // The row the block plays: a waiting click's own aim (found at once), else the picker's
          // standing one (shownYellow), selected or not.
          const waiting = pending && !pending.grid && list.equals(pending.list) ? pending : null;
          const target = (waiting && waiting.playingLabel) || (list.equals(shownYellow.list) ? shownYellow.label : null);
          if (!waiting && !target) return;
          try {
            const row = args[2];
            if (row.isNull()) return;
            if (waiting) waiting.painted = (waiting.painted || 0) + 1;
            const label = row.readUtf8String();
            if (!target || label !== target) return;
            if (waiting && !waiting.yellow) { waiting.yellow = true; send({type: 'toneTrace', reason: 'picker-drag', stage: 'yellow', nam: captureRows.has(label), painted: waiting.painted}); }
            // Text colour: [list+0x98] for a selected row, [list+0x90] otherwise (0x37fe09); R, G, B, A.
            this.at = list.add(row.add(0x110).readU8() ? 0x98 : 0x90);
            this.restore = this.at.readU32();
            this.at.writeU8(ACTIVE_RGB[0]); this.at.add(1).writeU8(ACTIVE_RGB[1]); this.at.add(2).writeU8(ACTIVE_RGB[2]);
          } catch (error) { this.restore = null; if (waiting) waiting.paintError = String(error.message); }
        },
        onLeave() { if (this.restore !== null) this.at.writeU32(this.restore); },
      });
      send({type: 'toneTrace', reason: 'picker-drag', stage: 'installed'});
    } catch (error) {
      send({type: 'toneTrace', reason: 'picker-drag', stage: 'skipped', error: String(error.message)});
    }
  })();



  function selectedIsNam() {
    // The block now selected is a NAM block and HX Edit shows its Model control.
    try {
      const controller = listOwner();
      const owner = controller.isNull() ? cursorController() : controller;
      return !owner.isNull() && Number.isInteger(namBlockSlot(owner)) && Number.isInteger(namView.modelSlot());
    } catch (_) { return false; }
  }

  // --- Keeping the open capture list current ------------------------------------------------
  // HX Edit builds the model column once, when a category is chosen. The capture rows come from
  // what is known to be on the Floor and from the capture types, and both can change while the
  // list is open: the startup slot check finishes after the first list is built (NAM Other was
  // empty until another category was opened), and a type changed in the NAM tab moves a capture.
  // Then the NAM category on show is chosen again, as a click would, and its rows rebuilt. When the
  // selected block is a NAM block whose capture is known, its own category is the one shown.
  // Native calls made here run inside the message hook, where Frida runs no other hooks, so the
  // capture rows are added here rather than by the list hook.
  let categoryList = NULL, builtSignature = null, shownBlock = null, listCheckAt = 0, listReports = 12, checkAskedAt = 0;
  const redrawList = method(0x3baa40, 'void', []);

  function captureSignature() {
    return namView.installedCaptures().map(capture =>
      `${capture.slot}:${captureGroup(capture.gear)}:${capture.instrument}:${capture.name}`).join('|');
  }
  function listOwner() {
    // The selector that owns the category list; its model column is at +0x58.
    for (const controller of [cursorController(), selectedController]) {
      if (!controllerReady(controller)) continue;
      for (let offset = 0x40; offset <= 0xa0; offset += 4) {
        if (controller.add(offset).readPointer().equals(categoryList)) return controller;
      }
    }
    return NULL;
  }
  function rowIndex(list, test) {
    for (let index = 0, count = rowCount(list); index < count && index < 512; index++) {
      const row = getIrRow(list, index);
      if (!row.isNull() && test(row.readUtf8String(), row)) return index;
    }
    return -1;
  }
  function highlightCapture(list, slot) {
    // The capture the block plays, rather than the NAM A2 Lite row HX Edit highlights. Selection
    // only: no event, so nothing is chosen.
    if (!Number.isInteger(slot)) return;
    const index = rowIndex(list, text => captureRows.get(text)?.slot === slot);
    if (index < 0) return;
    const selected = selectedRow(list);
    if (!selected.isNull() && selected.readUtf8String() === getIrRow(list, index).readUtf8String()) return;
    clearSelection(list, 0, 0);
    selectRow(list, 1, index, 0, 1);
  }
  function markCurrentRow(list, index) {
    // Selecting a row from code moves the selection but not the list's current row (+0x180 and
    // +0x184 kept the row HX Edit chose at startup), which keeps its arrow and lighter background:
    // NAM Other stayed marked next to the category actually shown (Floor, November 13).
    try {
      const count = rowCount(list);
      if (!Number.isInteger(index) || index < 0 || index >= count) return;
      for (const offset of [0x180, 0x184]) {
        const value = list.add(offset).readS32();
        if (value >= 0 && value < count) list.add(offset).writeS32(index);
      }
      redrawList(list);
    } catch (_) { /* presentation only */ }
  }
  function refreshCaptureList() {
    // UI thread, from the message hook.
    const now = Date.now();
    if (now < listCheckAt) return;
    listCheckAt = now + 300;
    try {
      if (categoryList.isNull() || (namView.libraryReady && !namView.libraryReady())) return;
      if (active?.transfer || active?.pendingTransfer || command || workflowRequest || toneImport || pendingCapture) return;
      // Only a category list a live selector still holds is read (the list is rebuilt with it).
      const controller = listOwner();
      if (controller.isNull()) return;
      // The selected block and its capture. A change of either while a NAM category is on show
      // (a block selected or added: HX Edit picks the category before the capture is known, and
      // showed NAM Other) also brings up the block's own category. Kept current while a stock
      // category is shown, so opening a NAM category by hand is never overridden.
      const slot = namBlockSlot(controller);
      const lock = lockKey(controller);
      const blockKey = `${lock}|${slot}`;
      const blockChanged = shownBlock !== null && blockKey !== shownBlock;
      const sameBlock = shownBlock !== null && shownBlock.startsWith(`${lock}|`);   // only its capture changed
      shownBlock = blockKey;
      const shownRow = selectedRow(categoryList);
      const shown = shownRow.isNull() ? '' : shownRow.readUtf8String();
      if (!NAM_CATEGORY_NAMES.includes(shown)) return;
      const list = controller.add(0x58).readPointer();
      if (list.isNull()) return;
      const signature = captureSignature();
      // A NAM category on show with nothing known to be on the Floor: the slot check was asked for
      // when the list was built, but proof discarded after that (one notice after another) is
      // asked for again by nobody. Ask again, every two seconds at most, until the check has
      // completed: with no library captures on the Floor (a new install) the list stays empty and
      // asking on that alone repeated the check every two seconds for as long as it was shown.
      if (signature === '' && !(namView.slotCheckDone?.()) && now >= checkAskedAt) {
        checkAskedAt = now + 2000; namView.requestSlotCheck?.();
      }
      // The same block on another capture whose row is already in this list (a row applied from it,
      // Use in the NAM tab, an undo): nothing to rebuild. A rebuild makes HX Edit select the block's
      // model row, NAM A2 Lite, and asks the Floor for a slot check each time.
      const inList = Number.isInteger(slot) && rowIndex(list, text => captureRows.get(text)?.slot === slot) >= 0;
      if (signature !== builtSignature || (blockChanged && !(sameBlock && inList))) {
        const capture = Number.isInteger(slot) ? namView.installedCaptures().find(entry => entry.slot === slot) : null;
        const target = capture ? NAM_CATEGORY_NAMES[captureGroup(capture.gear) - 1] : shown;
        // Guitar or Bass in NAM Amp / NAM Amp + cab: the block's capture decides; with none, the side
        // being browsed is kept (choosing the category row alone goes back to HX Edit's first, Guitar).
        const keepSide = target === shown ? subcategoryValue(controller) : null;
        const index = rowIndex(categoryList, text => text === target);
        if (index < 0) return;
        clearSelection(categoryList, 0, 0);
        selectRow(categoryList, 1, index, 0, 1);
        modelEvent(controller, selectionEvent, categoryList, NULL, NULL);
        markCurrentRow(categoryList, index);
        // The block's capture: its category's remembered side if the capture is on it, else All.
        const remembered = rememberedSide(target);
        const side = capture ? (onSide(capture, remembered) ? remembered : SIDE_ALL) : keepSide;
        if (side !== null) chooseSubcategory(controller, side);
        const header = rowIndex(list, text => text.trimEnd() === tiles[0].block);
        if (header >= 0) {
          const row = getIrRow(list, header);
          appendCaptureRows({header: row.readUtf8String(), list, image: row.add(0x104).readPointer(),
            index: row.add(0x100).readS32(), model: row.add(0x10c).readPointer()});
          redrawList(list);
          appendCaptureTiles(controller, list);
        }
        builtSignature = signature;
        if (listReports-- > 0) send({type: 'toneTrace', reason: 'nam-category', stage: 'list-refreshed', from: shown, to: target});
      }
      // A click waiting for its double-click keeps its row selected, also across a rebuild (HX Edit
      // selects the block's model row then); its row gone: the click is dropped. Otherwise the
      // playing capture's row is the selected one.
      if (pendingSelect && lockKey(pendingSelect.controller) !== pendingSelect.key) pendingSelect = null;
      const pending = pendingSelect && !pendingSelect.grid && pendingSelect.list.equals(list) ? pendingSelect : null;
      if (pickerPress) return;                                   // button down: the press owns the selection
      if (pending) {
        const index = rowIndex(list, text => text === pending.label);
        const selected = selectedRow(list);
        if (index < 0) pendingSelect = null;
        else if (selected.isNull() || selected.readUtf8String() !== pending.label) {
          clearSelection(list, 0, 0); selectRow(list, 1, index, 0, 0); redrawList(list);
        }
      } else if (Number.isInteger(slot) && slot === namView.modelSlot()) highlightCapture(list, slot);
    } catch (error) {
      if (listReports-- > 0) send({type: 'toneTrace', reason: 'nam-category', stage: 'list-refresh-skipped', error: String(error.message)});
    }
  }

  // --- Model lock --------------------------------------------------------------------------
  // Owner rule (November 13): a NAM block's Model control is locked by default when its capture
  // was picked from a NAM category list or the block came with a preset. A padlock left of the
  // Model value (nam_view) shows the state and unlocks / locks it on click. A plain NAM A2 Lite
  // block, picked from its own row, starts unlocked and shows no padlock. The state is the
  // editor's, per block of the loaded preset, and is never written to the preset or the device.
  // A block with no entry is locked; a preset or device change forgets every entry.
  const modelLocks = new Map();      // block key -> 'unlocked' | 'plain' (absent: locked)
  // block key -> {undo, previous}: where HX Edit's undo history stood just after a plain NAM A2
  // Lite pick, and the state before it. Undoing that pick took the block back to its capture while
  // it stayed "plain" (no padlock) until the preset was loaded again; below that point the earlier
  // state returns.
  const plainMarks = new Map();
  let lockCursor = NULL, pendingPlain = null, lockEventReports = 40;

  function noteCursor(controller) {
    try { if (controllerReady(controller)) lockCursor = controller; } catch (_) { /* not a view yet */ }
  }
  function cursorController() {
    if (controllerReady(lockCursor)) return lockCursor;
    return controllerReady(selectedController) ? selectedController : NULL;
  }
  function lockKey(controller) {
    return `${controller.add(0x34).readPointer()}|${controller.add(0x80).readS32()}|${block(controller)}`;
  }
  function namBlockSlot(controller) {
    return targetBinding.peekSlot(controller.add(0x34).readPointer(), controller.add(0x80).readS32(), block(controller));
  }
  function undoPosition(controller) {
    try {
      const editor = controller.add(0x34).readPointer();
      return editor.isNull() ? null : editor.add(0x294).readS32();
    } catch (_) { return null; }
  }
  function lockState(controller) {
    const key = lockKey(controller), mark = plainMarks.get(key);
    if (mark) {
      const position = undoPosition(controller);
      // HX Edit can record the step a moment after the change (as previews also see): settle on
      // the first rise within 1.5 s, then watch for the history going below it.
      if (!mark.armed && Number.isInteger(position) && Number.isInteger(mark.undo)) {
        if (position > mark.undo && Date.now() - mark.at < 1500) { mark.undo = position; mark.armed = true; }
        else if (Date.now() - mark.at >= 1500) mark.armed = true;
      }
      if (mark.armed && Number.isInteger(position) && Number.isInteger(mark.undo) && position < mark.undo) {
        plainMarks.delete(key);
        if (mark.previous === 'locked') modelLocks.delete(key); else modelLocks.set(key, mark.previous);
        send({type: 'toneTrace', reason: 'model-lock', stage: mark.previous, why: 'plain-undone', block: block(controller)});
      }
    }
    return modelLocks.get(key) || 'locked';
  }
  function setLockState(controller, state, why) {
    try {
      if (!controllerReady(controller)) return;
      const key = lockKey(controller);
      const before = modelLocks.get(key) || 'locked';
      if (state === 'locked') modelLocks.delete(key); else modelLocks.set(key, state);
      if (state === 'plain') plainMarks.set(key, {undo: undoPosition(controller), at: Date.now(), armed: false,
        previous: before === 'plain' ? 'locked' : before});
      else plainMarks.delete(key);
      send({type: 'toneTrace', reason: 'model-lock', stage: state, why, block: block(controller)});
      namView.applyModelLock?.(true);
    } catch (error) { send({type: 'toneTrace', reason: 'model-lock', stage: 'state-skipped', error: String(error.message)}); }
  }
  function modelLockState() {
    // The cursor block's state for the Model control on screen: 'locked' | 'unlocked' | 'plain',
    // 'none' when the cursor block is not a NAM block, null when the two cannot be tied yet.
    const controller = cursorController();
    if (controller.isNull()) return null;
    const slot = namBlockSlot(controller);
    if (!Number.isInteger(slot)) return 'none';
    if (slot !== namView.modelSlot()) return null;
    return lockState(controller);
  }
  function toggleModelLock() {
    // The padlock was clicked: it is shown only on a locked or unlocked block's Model row.
    const controller = cursorController();
    if (controller.isNull()) return;
    const state = modelLockState();
    if (state === 'locked' || state === 'unlocked') setLockState(controller, state === 'locked' ? 'unlocked' : 'locked', 'padlock');
  }
  function finishPendingPlain() {
    // UI thread: a plain NAM A2 Lite row was picked for a cell that had no block name yet.
    const pending = pendingPlain;
    if (Date.now() > pending.until) { pendingPlain = null; return; }
    const controller = cursorController();
    if (controller.isNull() || controller.add(0x80).readS32() !== pending.dsp || !Number.isInteger(namBlockSlot(controller))) return;
    pendingPlain = null;
    setLockState(controller, 'plain', 'plain-row');
  }
  function plainPicked(controller) {
    try {
      if (Number.isInteger(namBlockSlot(controller))) return setLockState(controller, 'plain', 'plain-row');
      pendingPlain = {dsp: controller.add(0x80).readS32(), until: Date.now() + 3000};
    } catch (_) { pendingPlain = null; }
  }
  if (profile.namCategory) namView.setModelLockQuery?.({state: modelLockState, toggle: toggleModelLock});

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
  const NAM_CATEGORY_NAMES = ['NAM Amp', 'NAM Amp + cab', 'NAM Cab', 'NAM Drive', 'NAM Other'];  // nam_catalog.NAM_CATEGORY_GROUPS
  const namRecords = new Map();      // "category name:subcategory index" -> that record of the NAM model
  const flowDsp = new Map();         // path object -> its DSP index
  const flowItems = new Map();       // path item -> {item, object, vtable}, to redraw after a change
  const selectorStack = [];          // model selectors currently syncing to the cursor block
  let iconReports = 40, iconState = '', iconCheckAt = 0, iconRefreshAt = 0, iconNudge = null;
  const l6text = p => { const rep = p.add(4).readPointer(); return rep.isNull() ? '' : rep.add(0x10).readPointer().readCString(); };
  const redrawItem = new NativeFunction(base.add(0x40ae70), 'void', ['pointer', 'int'], 'thiscall');

  // Amp+Cab (category 10) is built by HX Edit when it loads the catalog (0x3c0fb0) from Amp's sides,
  // twice: with current cabs (Guitar 1 -> 11, Bass 2 -> 12) and with legacy cabs ("Guitar+Legacy",
  // "Bass+Legacy", ids 1 and 2). Amp's All side (99, nam_catalog.PICKER_SIDES) came out as "All"
  // in both halves, with one id. The legacy one becomes "All+Legacy" with id 98. Its name gets a
  // string of its own (kept for the session, never released): the copy shares Amp's.
  const keptNames = [];
  function nameLegacyAll(catalog) {
    const count = (o, first, end) => o.add(end).readU32() - o.add(first).readU32();
    const at = (o, data, first, i) => o.add(data).readPointer().add(4 * (o.add(first).readU32() + i)).readPointer();
    for (let i = 0; i < count(catalog, 0x14, 0x18) && i < 64; i++) {
      const category = at(catalog, 0x0c, 0x14, i);
      if (category.add(0x24).readS32() !== 10) continue;
      let alls = 0;
      for (let j = 0; j < count(category, 0x3c, 0x40) && j < 16; j++) {
        const side = at(category, 0x34, 0x3c, j);
        if (side.add(0xc).readS32() !== 99 || ++alls < 2) continue;
        const text = Memory.allocUtf8String('All+Legacy'), rep = Memory.alloc(0x14);
        rep.writeU32(0x40000000);                 // reference count: never reaches zero
        rep.add(4).writeU32(10); rep.add(8).writeU32(0); rep.add(0xc).writeU32(11); rep.add(0x10).writePointer(text);
        keptNames.push(text, rep);
        side.add(4).add(4).writePointer(rep);
        side.add(0xc).writeS32(98);
        send({type: 'toneTrace', reason: 'nam-category', stage: 'legacy-all-named'});
        return;
      }
    }
  }
  if (profile.namCategory) {
    const ampCabGuards = { '3c0fb0': '558bec6aff68', '3c101c': '837f240a' };
    const ampCabReady = Object.entries(ampCabGuards).every(([rva, hex]) =>
      Array.from(new Uint8Array(base.add(parseInt(rva, 16)).readByteArray(hex.length / 2)), b => b.toString(16).padStart(2, '0')).join('') === hex);
    if (ampCabReady) Interceptor.attach(base.add(0x3c0fb0), {
      onEnter() { this.catalog = this.context.ecx; },
      onLeave() {
        try { nameLegacyAll(this.catalog); }
        catch (error) { send({type: 'toneTrace', reason: 'nam-category', stage: 'legacy-all-skipped', error: String(error.message)}); }
      },
    });
  }

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
          if (l6text(record.add(0x24)) === NAM_MODEL_ID) namRecords.set(name + ':' + j, record);   // j: 0 Guitar / only, 1 Bass
        }
      }
    }
  }

  function wantedRecord(dsp, blockName) {
    // The record of the category that matches this block's capture, or null to leave HX Edit's own.
    // The edit buffer is the one the model selector holds; the selector exists from startup,
    // before the user has selected anything (selectedController is only set by a selection).
    iconWhy = `records=${namRecords.size}`;
    if (!NAM_CATEGORY_NAMES.every(name => namRecords.has(name + ':0'))) return null;
    iconWhy = 'no-editor';
    if (iconEditor.isNull()) return null;
    const slot = targetBinding.peekSlot(iconEditor, dsp, blockName);
    iconWhy = `slot=${slot} dsp=${dsp}`;
    if (!Number.isInteger(slot)) return null;
    const capture = knownCaptures().find(entry => entry.slot === slot);
    iconWhy = capture ? `gear=${capture.gear}` : `slot ${slot} not a known capture (${namView.installedCaptures().length} known)`;
    if (!capture) return null;
    // The record on the category's remembered side when the capture shows there, else on All, so
    // HX Edit opens that side itself (record index = side position: All 0, Guitar 1, Bass 2).
    const name = NAM_CATEGORY_NAMES[captureGroup(capture.gear) - 1], side = rememberedSide(name);
    const position = !onSide(capture, side) ? 0 : side === SIDE_GUITAR ? 1 : side === SIDE_BASS ? 2 : 0;
    return namRecords.get(name + ':' + position) || namRecords.get(name + ':0') || null;
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
    if (profile.namCategory) { try { pointFavorites(); } catch (_) { /* presentation only */ } }
    try {
      if (namView.libraryReady && !namView.libraryReady()) return;
      const state = `${iconEditor}|${controllerReady(selectedController) ? selectedController.toString() : ''}|${namView.modelSlot()}|${namView.installedCaptures().length}|${namView.payloadRevision?.() || 0}`;
      if (state !== iconState) { iconState = state; iconRefreshAt = now + 150; iconActiveUntil = now + 4000; }
    } catch (_) { /* presentation only */ }
  }

  // HX Edit's favorites of a NAM block, kept on their capture's category (see pointFavorite).
  // 0x3c90c0 (catalog, from device command 0x24) rebuilds the favorites from the Floor and sets
  // each one's model with findModel (0x3c9519): NAM Other for every NAM favorite. It runs on a
  // save, a rename, at connect and whenever the Save As Favorite dialog opens, before any list is
  // built from it. Afterwards the favorite notes waiting for it are taken (nam_view) and every
  // NAM favorite pointed again. 0x25f3d0 sets up that dialog with a category for its new entry
  // (findModel of the block's model at 0x3d3bf4, so NAM Other too): the block's capture's instead.
  if (profile.namCategory) {
    const favoriteGuards = { '3c90c0': '558bec6aff68e252', '3c9519': 'e8c28fffff8945f0', '25f3d0': '558bec56ff75088bf18d' };
    const favoriteReady = Object.entries(favoriteGuards).every(([rva, hex]) =>
      Array.from(new Uint8Array(base.add(parseInt(rva, 16)).readByteArray(hex.length / 2)), b => b.toString(16).padStart(2, '0')).join('') === hex);
    if (!favoriteReady) send({ type: 'toneTrace', reason: 'nam-category', stage: 'favorite-hooks-skipped' });
    else {
      Interceptor.attach(base.add(0x3c90c0), {
        onEnter() { this.catalog = this.context.ecx; },
        onLeave() {
          try {
            namView.flushFavoriteNotes?.();
            if (namRecords.size === 0) indexNamRecords(this.catalog);
            const data = this.catalog.add(0x38).readPointer(), first = this.catalog.add(0x40).readU32(), end = this.catalog.add(0x44).readU32();
            const names = new Set();
            let pointed = 0, adopted = 0;
            for (let i = first; i < end && i < first + 512; i++) {
              const object = data.add(4 * i).readPointer(), name = l6text(object.add(8));
              names.add(name);
              // Saved a moment ago from the dialog: a new name, reloaded before HX Edit says which.
              if (savingFrom && Date.now() < savingFrom.until && favoriteNamesSeen && !favoriteNamesSeen.has(name) &&
                  NAM_FAVORITE_MODELS.has(l6text(object.add(0x10).readPointer().add(0x24))) &&
                  !Number.isInteger(namView.favoriteSlot?.(name))) { namView.rememberFavoriteSlot?.(name, savingFrom.slot); adopted++; }
              if (pointFavorite(object)) pointed++;
            }
            send({ type: 'toneTrace', reason: 'nam-category', stage: 'favorites-reloaded', count: end - first, pointed, adopted,
                   saving: !!savingFrom, seen: favoriteNamesSeen ? favoriteNamesSeen.size : -1 });
            favoriteNamesSeen = names;
            favoriteRevision = '';            // the rows built from here on show it; earlier ones are refreshed
          } catch (error) {
            send({ type: 'toneTrace', reason: 'nam-category', stage: 'favorites-reload-skipped', error: String(error.message) });
          }
        },
      });
      Interceptor.attach(base.add(0x25f3d0), {
        onEnter(args) {
          try {
            const category = args[1];
            if (category.compare(ptr(0x10000)) < 0 || !l6text(category.add(4)).startsWith('NAM ')) return;
            const slot = namView.modelSlot(), capture = favoriteCapture(slot);
            savingFrom = Number.isInteger(slot) ? {slot, until: Date.now() + 120000} : null;
            if (capture) slotLooks.set(slot, {slot, group: captureGroup(capture.gear), icon: capture.icon || ''});
            const record = captureRecord(capture);
            const wanted = record ? record.add(0x30).readPointer().add(0x14).readPointer() : NULL;
            if (!wanted.isNull()) args[1] = wanted;
          } catch (error) {
            send({ type: 'toneTrace', reason: 'nam-category', stage: 'favorite-dialog-skipped', error: String(error.message) });
          }
        },
      });
    }
  }

  if (profile.namCategory) {
    let lastSelector = NULL;
    const selectorShowsBlock = Array.from(new Uint8Array(base.add(0x21f54f).readByteArray(12)),
      b => b.toString(16).padStart(2, '0')).join('') === '8b4dc88d45ac50e8852f1a00';
    Interceptor.attach(base.add(0x3c24e0), {            // HXModelCatalog::findModel(id, variant)
      onEnter(args) {
        try {
          if (namRecords.size === 0) indexNamRecords(this.context.ecx);
          // In a selector sync, or the selector showing its block's category again on its own
          // (0x21f556, e.g. after a favorite is cleared): that opened NAM Other until the next sync.
          this.selector = selectorStack.length > 0 ? selectorStack[selectorStack.length - 1] :
            selectorShowsBlock && this.returnAddress.equals(base.add(0x21f55b)) ? lastSelector : NULL;
          this.nam = !this.selector.isNull() && l6text(args[0]) === NAM_MODEL_ID;
        } catch (_) { this.nam = false; }
      },
      onLeave(result) {
        if (!this.nam || result.isNull()) return;
        try {
          const selector = this.selector;
          const blockName = block(selector), wanted = wantedRecord(selector.add(0x80).readS32(), blockName);
          if (wanted && !wanted.equals(result)) { result.replace(wanted); iconTrace('selector', blockName, wanted); }
          else if (!wanted) iconSkipped('selector', blockName);
        } catch (error) { iconWhy = String(error.message); iconSkipped('selector', '?'); }
      },
    });
    Interceptor.attach(base.add(0x21e500), {            // model selector: sync to the cursor block
      onEnter() {
        this.controller = this.context.ecx;
        lastSelector = this.context.ecx;
        selectorStack.push(this.context.ecx);
        noteCursor(this.context.ecx);
        try { if (controllerReady(this.context.ecx)) iconEditor = this.context.ecx.add(0x34).readPointer(); } catch (_) { /* keep the last one */ }
      },
      onLeave() {
        selectorStack.pop();
        // HX Edit has shown the selected block's category: Amp / Preamp open on the remembered side.
        try { if (profile.namCategory) blockSide(this.controller); } catch (_) { /* leave HX Edit's own side */ }
      },
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

  // Grid view of the block picker. HX Edit builds its tiles from the list's rows (loop at 0x21db23, the
  // row in [ebp-0x14]), each with its row model's picture (0x3c2fb0, called from 0x21dbee), never the
  // row's own picture: every capture tile showed the NAM block's rack. For a capture row the capture's
  // picture is returned instead. Stock rows are untouched; other bytes leave grid view as it was.
  if (profile.namCategory) {
    const gridGuards = { '21dbda': '8b4dec426a010fb6c05056ffb1000100008bcf52e8bd531a00',
                         '3c2fb0': '568bf1837e2000752c8b461c85c07405' };
    const gridReady = Object.entries(gridGuards).every(([rva, hex]) => {
      const bytes = new Uint8Array(base.add(parseInt(rva, 16)).readByteArray(hex.length / 2));
      return Array.from(bytes, b => b.toString(16).padStart(2, '0')).join('') === hex;
    });
    if (!gridReady) send({ type: 'toneTrace', reason: 'nam-category', stage: 'grid-pictures-skipped' });
    else {
      const tileBuilder = base.add(0x21dbf3);
      Interceptor.attach(base.add(0x3c2fb0), {
        onEnter() {
          this.picture = NULL;
          if (!this.returnAddress.equals(tileBuilder)) return;
          try {
            const row = this.context.ebp.sub(0x14).readPointer();
            if (row.isNull()) return;
            // A NAM favorite's tile: its capture's picture (its list row shows the category's).
            const object = row.add(0x10c).readPointer();
            const look = object.compare(ptr(0x10000)) < 0 ? null : pointFavorite(object);
            if (look && look.icon) { const image = captureImage(look.icon); if (!image.isNull()) { this.picture = image; return; } }
            const picture = row.add(0x104).readPointer();   // capture rows carry their capture's picture
            for (const image of captureImages.values()) if (!image.isNull() && image.equals(picture)) { this.picture = picture; break; }
          } catch (error) {
            this.picture = NULL;
          }
        },
        onLeave(result) { if (!this.picture.isNull()) result.replace(this.picture); },
      });
    }
  }

  Interceptor.attach(base.add(0x380940), {
    onEnter(args) {
      if (profile.navigation) {
        if (!profile.namCategory) return;
        if (favoriteRow(this.context.ecx, args, this.returnAddress)) return;
        if (!this.context.ecx.readPointer().equals(base.add(0x91fcd0)) || args[1].isNull() || args[5].isNull()) return;
        const header = args[1].readUtf8String();
        // The last stock category row in the category column (list kind 0): mark it afterwards.
        if (header === 'Looper' && this.context.ecx.add(0x1d8).readU32() === 0) {
          this.separator = true; categoryList = this.context.ecx; return;
        }
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
    // A press in the picker may become a drag: its selection waits for the release (installPickerDrag).
    try { if (heldPickerEvent(controller, event, sender, data)) return; } catch (_) { /* as before */ }
    if (profile.navigation) {
      let plain = false;
      if (profile.namCategory && (event === selectionEvent || event === gridSelectEvent) && !sender.isNull()) {
        let entry = null, text = null, column = NULL;
        try {
          column = controller.add(0x58).readPointer();                    // the model column
          let row = NULL;
          if (event === selectionEvent && sender.equals(column)) row = selectedRow(sender);
          // Grid view: its tiles (+0x60, built from the column's rows by 0x21db23, same labels)
          // send "srg!" and HX Edit reads the clicked tile with 0x37a010 (handler at 0x217d3c).
          else if (event === gridSelectEvent && sender.equals(controller.add(0x60).readPointer())) row = gridSelected(sender);
          text = row.isNull() ? null : row.readUtf8String();
          entry = text === null ? null : captureRows.get(text) || null;
        } catch (_) { entry = null; text = null; }
        // A capture tile is applied through the model column, exactly as a click on its row.
        if (entry) return chooseCapture(controller, column, entry);
        plain = text !== null && text.trimEnd() === tiles[0].block;   // a NAM category's own NAM A2 Lite row
      }
      modelEvent(controller, event, sender, data, flags);
      if (profile.namCategory) {
        try {
          // A side picked in the dropdown is remembered; a category opened by a click shows its
          // remembered side (never picked: All, the first).
          if (event === subcategoryChanged && sender.equals(controller.add(0x50).readPointer())) noteSideChoice(controller);
          else if (event === selectionEvent && !categoryList.isNull() && sender.equals(categoryList)) {
            const category = shownCategory();
            if (SIDED_CATEGORIES.has(category)) chooseSubcategory(controller, rememberedSide(category));
          }
        } catch (error) { if (listReports-- > 0) send({type: 'toneTrace', reason: 'nam-category', stage: 'side-skipped', error: String(error.message)}); }
      }
      if (plain) plainPicked(controller);
      observeController(controller);
      if (profile.namCategory) noteCursor(controller);
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
      if (lockCursor.equals(this.context.ecx)) lockCursor = NULL;
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
      if (event === 'EditBuffer_PresetSavedToDevice') levelSavedAt = Date.now();   // Level's Save, or any save
      if (profile.namCategory && contextEvent.test(event)) {
        if (lockEventReports-- > 0) send({type: 'toneTrace', reason: 'model-lock', stage: 'context', event, entries: modelLocks.size});
        // Blocks of another preset (or device) are new to this editor: locked by default again.
        // PresetContentsWillChange is the one seen when a preset loads (Floor traces, November
        // 13); none of these came with Model, capture or lock changes, or a plain block pick.
        if (/^EditBuffer_(?:TargetDeviceChanged|DevicePresetChanged|PresetChanged|PresetContentsWillChange)$/.test(event)) {
          modelLocks.clear(); plainMarks.clear(); pendingPlain = null;
        }
      }
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
      // Every open view receives these notices; only a cursor change says which block is the cursor's.
      if (profile.namCategory && this.event === 'EditBuffer_HelixCursorChange') noteCursor(this.controller);
      if (profile.navigation && this.event === 'EditBuffer_HelixCursorChange') refreshBounds(true);
      // (Not while inserting or restoring a block: the cell's position changes when it fills or empties.)
      if (this.ours && active && (active.valid || active.bound) && !active.applying && !active.inserting && !active.restoring && !atTarget()) {
        invalidate(`moved after ${this.event}`);
      }
    },
  });

  // --- Level matching: the edit buffer's own property calls (research/editor-control-20261115) ---
  // HX Edit's editor (vtable 0x963dfc, the selector's +0x34) sets and reads cursor, input type and
  // snapshot as properties: set(dsp, group, key, value, flags) at vtable +0x7c (0x5178c0), get(dsp,
  // group, key, out) at +0x88 (0x504f50); Variants of 16 bytes (type at +0, value at +8: 4 s32,
  // 9 empty, 0xa L6String*, 0xb bool). The UI itself calls them; each set is one undo step, nothing
  // is saved. UI thread only (this runs from the wake message).
  const LEVEL_EDITOR = 0x963dfc, LEVEL_SET = 0x5178c0, LEVEL_GET = 0x504f50, USB_78 = 16, NO_INPUT = 0;
  // An output block's @output: 1 Multi ... 10 USB 1/2 (traced on both paths). While measuring, the final
  // outputs go to USB 1/2 only, so the test signal is read back without reaching the amp.
  // Saving: the editor's vtable +0x118 (0x5159b0) stores the edit buffer at a preset address,
  // "l6device://0x00210001:/preset/<setlist>/<preset>" (traced from Save Preset As > OK, which first
  // sets the name, then calls this). 0x210001 is the Helix Floor, the only device this build serves.
  const USB_12 = 10, LEVEL_SAVE = 0x5159b0, FLOOR_DEVICE = 0x210001;
  let levelSavedAt = 0;
  const levelString = new NativeFunction(base.add(0x1a1640), 'pointer', ['pointer', 'pointer', 'int'], 'thiscall');
  const levelStringFree = new NativeFunction(base.add(0x1a0120), 'void', ['pointer'], 'thiscall');
  const levelInputs = [[0, 'inputA'], [0, 'inputB'], [1, 'inputA'], [1, 'inputB']];
  let levelSaved = null;      // {inputs: [[dsp, group, index]], outputs: [[dsp, output]], snapshot}: what Restore puts back
  // Apply: the preset's final output level ('gain' of each DSP's outputA, dB, a float), per snapshot
  // when needed through HX Edit's Snapshots controller (19; 0 removes it). Path 1's @output 2-4 sends
  // it into path 2 (Path 2A / 2B / 2A+B), so path 2's output is then the final one.
  const SNAPSHOTS_CONTROLLER = 19, INTO_PATH_2 = [2, 3, 4], GAIN_MIN = -120, GAIN_MAX = 20;
  const OUTPUT_PARAMETERS = ['gain', 'pan'];   // an output block's Level and Pan
  let levelUndo = null;       // {snapshot, outputs: [{dsp, assigned, whole, original, before: {n: dB}}]}
  function levelGainController(editor, dsp) {
    return levelGet(editor, -1, 'controller', `dsp${dsp}.outputA.gain.@controller`);
  }
  function levelSetGain(editor, dsp, from, to) {
    // As HX Edit's own Level control does: the old value, then the new one twice.
    to = Math.max(GAIN_MIN, Math.min(GAIN_MAX, Math.round(to * 10) / 10));
    if (levelSet(editor, dsp, 'outputA', 'gain', { float: from }, 0x80000001) < 0 ||
        levelSet(editor, dsp, 'outputA', 'gain', { float: to }, 0x80000003) < 0 ||
        levelSet(editor, dsp, 'outputA', 'gain', { float: to }, 0x80000002) < 0) {
      throw new Error(`HX Edit refused the output level of path ${dsp + 1}.`);
    }
    return to;
  }
  function levelOutputs(editor) {
    // The outputs that reach the amp: path 1's unless it feeds path 2; path 2's when fed or given an input.
    const first = levelGet(editor, 0, 'outputA', '@output');
    const second = levelGet(editor, 1, 'outputA', '@output');
    const intoSecond = INTO_PATH_2.includes(first);
    const secondPlays = intoSecond || levelInputs.some(([dsp, group]) =>
      dsp === 1 && ![null, NO_INPUT].includes(levelGet(editor, dsp, group, '@input')));
    const outputs = [];
    if (Number.isInteger(first) && !intoSecond) outputs.push(0);
    if (Number.isInteger(second) && secondPlays) outputs.push(1);
    return outputs;
  }
  function levelSamePreset(preset) {
    let now = null;
    try { now = namView.presetHeader?.() || null; } catch (_) { now = null; }
    return !!(preset && now && preset.setlist === now.setlist && preset.preset === now.preset);
  }
  function levelHere(editor, snapshot) {
    if (levelGet(editor, -1, 'global', '@current_snapshot') !== snapshot - 1) {
      throw new Error(`HX Edit is not on snapshot ${snapshot} yet.`);
    }
  }
  function levelEditor() {
    const controller = cursorController().isNull() ? NULL : cursorController();
    const editor = !controller.isNull() ? controller.add(0x34).readPointer() : iconEditor;
    if (editor.isNull()) throw new Error('Select a block in HX Edit first.');
    const vt = editor.readPointer();
    if (!vt.equals(base.add(LEVEL_EDITOR)) || !vt.add(0x7c).readPointer().equals(base.add(LEVEL_SET)) ||
        !vt.add(0x88).readPointer().equals(base.add(LEVEL_GET))) {
      throw new Error('This HX Edit does not match the reviewed build; level matching is unavailable.');
    }
    return editor;
  }
  function levelVariants(values, use) {
    const memory = Memory.alloc(24 * values.length), made = [];
    try {
      return use(values.map((value, i) => {
        const variant = memory.add(16 * i);
        if (typeof value === 'string') {
          const text = memory.add(16 * values.length + 8 * i);
          levelString(text, Memory.allocUtf8String(value), 1);
          made.push(text);
          variant.writeU32(0xa); variant.add(8).writePointer(text);
        } else if (value === null) variant.writeU32(9);
        else if (typeof value === 'object') {           // {float: x} (type 8) or {type, int}
          if ('float' in value) { variant.writeU32(8); variant.add(8).writeFloat(value.float); }
          else { variant.writeU32(value.type); variant.add(8).writeS32(value.int); }
        } else { variant.writeU32(4); variant.add(8).writeS32(value); }
        return variant;
      }));
    } finally { made.forEach(text => levelStringFree(text)); }
  }
  function levelSet(editor, dsp, group, key, value, flags) {
    const set = new NativeFunction(base.add(LEVEL_SET), 'int', ['pointer', 'int', 'pointer', 'pointer', 'pointer', 'uint'], 'thiscall');
    return levelVariants([group, key, value], v => set(editor, dsp, v[0], v[1], v[2], flags >>> 0));
  }
  function levelGet(editor, dsp, group, key) {
    const typed = levelGetTyped(editor, dsp, group, key);
    return typed === null ? null : typed.value;
  }
  // {type, value} (the variant type, to write a value back as it was), or null.
  function levelGetTyped(editor, dsp, group, key) {
    const get = new NativeFunction(base.add(LEVEL_GET), 'int', ['pointer', 'int', 'pointer', 'pointer', 'pointer'], 'thiscall');
    return levelVariants([group, key, null], v => {
      if (get(editor, dsp, v[0], v[1], v[2]) < 0) return null;
      const type = v[2].readU32(), at = v[2].add(8);
      return { type, value: levelVariantValue(type, at) };
    });
  }
  function levelVariantValue(type, at) {
    {
      if (type === 0xa) {
        const text = at.readPointer();
        if (text.isNull()) return '';
        const value = stringValue(text);
        new NativeFunction(text.readPointer().readPointer(), 'pointer', ['pointer', 'int'], 'thiscall')(text, 1);
        return value;
      }
      return type === 0xb || type === 1 ? at.readU8() : type === 0 ? at.readS8() : type === 8 ? at.readFloat() : at.readS32();
    }
  }
  function levelState(editor, withGain = false) {
    const snapshot = levelGet(editor, -1, 'global', '@current_snapshot');
    const snapshots = [];
    for (let i = 0; i < 8; i++) {
      const name = levelGet(editor, -1, `snapshot${i}`, '@name');
      // custom: renamed by the player; valid: the preset's own "this snapshot is set up" flag (when the
      // editor has one). Level scans the snapshots that are set up (nam_level_flow.used_snapshots).
      if (name !== null) snapshots.push({ number: i + 1, name, custom: levelGet(editor, -1, `snapshot${i}`, '@custom_name'),
                                          valid: levelGet(editor, -1, `snapshot${i}`, '@valid') });
    }
    const inputs = levelInputs.map(([dsp, group]) => ({ dsp, group, input: levelGet(editor, dsp, group, '@input') }))
      .filter(entry => Number.isInteger(entry.input));
    let preset = null;
    try { preset = namView.presetHeader?.() || null; } catch (_) { preset = null; }
    // The final output's Level (dB) on this snapshot: what HX Edit shows, and what Apply changes. (No
    // preset-tree read here: it takes HX Edit's edit-buffer locks, and with it in every scan step the
    // scan stopped reaching the snapshots it recalled, Floor, November 16.)
    const finals = withGain ? (levelSaved ? levelSaved.outputs.map(([dsp]) => dsp) : levelOutputs(editor)) : [];
    const gain = finals.length ? levelGet(editor, finals[0], 'outputA', 'gain') : undefined;
    return { snapshot: Number.isInteger(snapshot) ? snapshot + 1 : null, snapshots, inputs, preset, gain,
             prepared: levelSaved !== null, saved: levelSaved, savedAt: levelSavedAt,
             canUndo: levelUndo !== null && levelSamePreset(levelUndo.preset) };
  }
  function levelRestoreSaved(editor) {
    const saved = levelSaved;
    if (!saved) return levelState(editor);
    const failed = [];
    for (const [dsp, output] of saved.rerouted || saved.outputs || []) {
      if (levelSet(editor, dsp, 'outputA', '@output', output, 0x80000000) < 0) failed.push(`path ${dsp + 1}'s output`);
    }
    for (const [dsp, group, input] of saved.inputs) {
      if (levelSet(editor, dsp, group, '@input', input, 0x80000000) < 0) failed.push(group);
    }
    if (Number.isInteger(saved.snapshot)) levelSet(editor, -1, 'global', '@current_snapshot', saved.snapshot - 1, 0x80000000);
    if (failed.length) throw new Error(`Could not put back ${failed.join(', ')}. Set it in HX Edit.`);
    levelSaved = null;
    return Object.assign(levelState(editor), { restored: true });
  }
  function levelAction(request) {
    const editor = levelEditor();
    if (request.action === 'levelState') return levelState(editor, request.gain === true);
    if (request.action === 'levelPrepare') {
      // Every real input (not None) of the preset plays the DI from USB 7/8, and every final output goes
      // to USB 1/2 only (silent on the amp); Restore puts them back. Changing an output block's @output
      // makes it a different block: its Level and Pan lose any controller (Floor, November 16: Level on
      // Snapshots came back as one plain value). nam_level_flow reads them first (levelTies) and puts
      // them back afterwards (levelTie, levelPut).
      const state = levelState(editor);
      if (!levelSaved) {
        const outputs = levelOutputs(editor).map(dsp => [dsp, levelGet(editor, dsp, 'outputA', '@output')])
          .filter(([, output]) => Number.isInteger(output));
        levelSaved = { inputs: state.inputs.filter(entry => entry.input !== NO_INPUT)
          .map(entry => [entry.dsp, entry.group, entry.input]), outputs, rerouted: outputs, snapshot: state.snapshot };
      }
      if (!levelSaved.inputs.length) throw new Error('This preset has no input to play the DI into.');
      if (!levelSaved.outputs.length) throw new Error('This preset has no output to measure.');
      // outputs false: only the inputs for now (the guitar is silenced at once, while the scan still
      // reads the outputs' controllers); a second levelPrepare re-routes the outputs.
      for (const [dsp] of request.outputs === false ? [] : levelSaved.rerouted) {
        if (levelSet(editor, dsp, 'outputA', '@output', USB_12, 0x80000000) < 0) throw new Error(`Could not set path ${dsp + 1}'s output to USB 1/2.`);
      }
      for (const [dsp, group] of levelSaved.inputs) {
        if (levelSet(editor, dsp, group, '@input', USB_78, 0x80000000) < 0) throw new Error(`Could not set ${group} to USB 7/8.`);
      }
      return levelState(editor);
    }
    if (request.action === 'levelTies') {
      // The final outputs' Level and Pan on this snapshot ({type, value}), and their controllers.
      const finals = levelSaved ? levelSaved.outputs.map(([dsp]) => dsp) : levelOutputs(editor);
      const snapshot = levelGet(editor, -1, 'global', '@current_snapshot');
      return { snapshot: Number.isInteger(snapshot) ? snapshot + 1 : null, ties: finals.map(dsp => ({
        dsp,
        controllers: Object.fromEntries(OUTPUT_PARAMETERS.map(key =>
          [key, levelGet(editor, -1, 'controller', `dsp${dsp}.outputA.${key}.@controller`)])
          .filter(([, controller]) => controller !== null && controller !== 0)),
        values: Object.fromEntries(OUTPUT_PARAMETERS.map(key => [key, levelGetTyped(editor, dsp, 'outputA', key)])),
      })) };
    }
    if (request.action === 'levelTie') {
      // Put an output's Level or Pan back on its controller (19: Snapshots; every snapshot then holds
      // the current value until levelPut writes each one).
      const { dsp, key, controller } = request;
      if (![0, 1].includes(dsp) || !OUTPUT_PARAMETERS.includes(key) || !Number.isInteger(controller) || controller < 1 || controller > 255) {
        throw new Error('Unknown output setting.');
      }
      if (levelSet(editor, -1, 'controller', `dsp${dsp}.outputA.${key}.@controller`, { type: 1, int: controller }, 0) < 0) {
        throw new Error(`Could not put path ${dsp + 1}'s output ${key} back on its controller.`);
      }
      return { tied: true };
    }
    if (request.action === 'levelPut') {
      // On snapshot n (after levelRecall and a full second): an output's Level or Pan back to what it was,
      // written as HX Edit's own controls do (old value, then the new one twice).
      const { dsp, key, snapshot, value } = request;
      if (![0, 1].includes(dsp) || !OUTPUT_PARAMETERS.includes(key) || !value || !Number.isFinite(value.value)) {
        throw new Error('Unknown output setting.');
      }
      levelHere(editor, snapshot);
      const now = levelGetTyped(editor, dsp, 'outputA', key);
      const variant = v => value.type === 8 ? { float: v } : { type: value.type, int: Math.round(v) };
      const from = now && Number.isFinite(now.value) ? now.value : value.value;
      if (levelSet(editor, dsp, 'outputA', key, variant(from), 0x80000001) < 0 ||
          levelSet(editor, dsp, 'outputA', key, variant(value.value), 0x80000003) < 0 ||
          levelSet(editor, dsp, 'outputA', key, variant(value.value), 0x80000002) < 0) {
        throw new Error(`HX Edit refused path ${dsp + 1}'s output ${key}.`);
      }
      return { put: true };
    }
    if (request.action === 'levelRecall') {
      const number = request.snapshot;
      if (!Number.isInteger(number) || number < 1 || number > 8) throw new Error('Unknown snapshot.');
      if (levelSet(editor, -1, 'global', '@current_snapshot', number - 1, 0x80000000) < 0) throw new Error('HX Edit could not recall that snapshot.');
      return { recalled: number };     // the edit buffer reports it about 150 ms later: levelState to check
    }
    if (request.action === 'levelRestore') return levelRestoreSaved(editor);
    if (request.action === 'levelDrop') {
      // A scanned preset that is left by loading another one: the load drops its measuring routing
      // (never saved), so there is nothing to put back; only this record goes.
      levelSaved = null;
      return { dropped: true };
    }
    if (request.action === 'levelRecover') {
      // Measuring was cut short (the app closed or crashed) with the inputs on USB 7/8. This editor's
      // own record wins; otherwise the record the app kept on disk ({preset, inputs, snapshot}) puts
      // back every input of that preset that is still on USB 7/8. Another preset: nothing to do here.
      if (levelSaved) return Object.assign(levelRestoreSaved(editor), { recovered: true });
      if (!levelSamePreset(request.preset)) return { other: true };
      const failed = [];
      let fixed = 0;
      for (const entry of request.outputs || []) {
        const [dsp, output] = Array.isArray(entry) ? entry : [];
        if (![0, 1].includes(dsp) || !Number.isInteger(output) || output === USB_12) continue;
        if (levelGet(editor, dsp, 'outputA', '@output') !== USB_12) continue;
        if (levelSet(editor, dsp, 'outputA', '@output', output, 0x80000000) < 0) failed.push(`path ${dsp + 1}'s output`); else fixed++;
      }
      for (const entry of request.inputs || []) {
        const [dsp, group, input] = Array.isArray(entry) ? entry : [];
        if (!levelInputs.some(([d, g]) => d === dsp && g === group) || !Number.isInteger(input) || input === USB_78) continue;
        if (levelGet(editor, dsp, group, '@input') !== USB_78) continue;
        if (levelSet(editor, dsp, group, '@input', input, 0x80000000) < 0) failed.push(group); else fixed++;
      }
      if (failed.length) throw new Error(`Could not put back ${failed.join(', ')}. Set it in HX Edit.`);
      return Object.assign(levelState(editor), { recovered: fixed > 0 });
    }
    if (request.action === 'levelApplyPlan') {
      // trims: {snapshot: dB}. One change for the whole preset when every snapshot moves alike and the
      // level is not per snapshot already; otherwise the output level goes on the Snapshots controller
      // (every snapshot keeps today's value) and the editor visits each snapshot (levelApplyHere).
      if (levelSaved) throw new Error('Measuring is still in progress.');
      const trims = Object.entries(request.trims || {}).map(([n, dB]) => [Number(n), Number(dB)])
        .filter(([n, dB]) => Number.isInteger(n) && n >= 1 && n <= 8 && Number.isFinite(dB) && Math.abs(dB) <= 12);
      if (!trims.length) throw new Error('Nothing to apply.');
      const state = levelState(editor), dsps = levelOutputs(editor);
      if (!dsps.length) throw new Error('This preset has no output to level.');
      const everySnapshot = state.snapshots.every(s => trims.some(([n]) => n === s.number));
      const alike = trims.every(([, dB]) => dB === trims[0][1]);
      const home = Number.isInteger(request.home) && request.home >= 1 && request.home <= 8 ? request.home : state.snapshot;
      const undo = { snapshot: home, outputs: [], preset: state.preset };
      let visit = [];
      levelUndo = undo;     // kept even if a later step fails, so Undo can put back what did change
      for (const dsp of dsps) {
        const perSnapshot = levelGainController(editor, dsp) === SNAPSHOTS_CONTROLLER;
        const original = levelGet(editor, dsp, 'outputA', 'gain');
        if (typeof original !== 'number') throw new Error(`Could not read the output level of path ${dsp + 1}.`);
        const entry = { dsp, assigned: false, whole: false, original, before: {} };
        undo.outputs.push(entry);
        if (!perSnapshot && everySnapshot && alike) {
          entry.whole = true;
          levelSetGain(editor, dsp, original, original + trims[0][1]);
        } else {
          if (!perSnapshot) {
            if (levelSet(editor, -1, 'controller', `dsp${dsp}.outputA.gain.@controller`,
                         { type: 1, int: SNAPSHOTS_CONTROLLER }, 0) < 0) {
              throw new Error(`Could not put path ${dsp + 1}'s output level on snapshots.`);
            }
            entry.assigned = true;
          }
          visit = trims.map(([n]) => n);
        }
      }
      return { visit, trims: Object.fromEntries(trims), outputs: dsps };
    }
    if (request.action === 'levelApplyHere') {
      // On snapshot n (after levelRecall): each per-snapshot output moves by the trim.
      const n = request.snapshot, dB = Number(request.trim);
      if (!levelUndo) throw new Error('Nothing is being applied.');
      if (!Number.isFinite(dB) || Math.abs(dB) > 12) throw new Error('That change is too large.');
      levelHere(editor, n);
      const levels = {};
      for (const entry of levelUndo.outputs.filter(entry => !entry.whole)) {
        // Put on snapshots by this Apply: every snapshot still holds the one old level, so nothing is
        // read back (a read right after a recall can still be the previous snapshot's value: Floor,
        // November 16, preset 005 ran up to +20 dB that way). Per snapshot already: read, after the
        // editor has settled on this snapshot (nam_level_flow waits for that).
        const before = entry.assigned && !(n in entry.before) ? entry.original
          : !(n in entry.before) && entry === levelUndo.outputs[0] && Number.isFinite(request.start) ? request.start
          : levelGet(editor, entry.dsp, 'outputA', 'gain');
        if (typeof before !== 'number') throw new Error(`Could not read the output level of path ${entry.dsp + 1}.`);
        if (!(n in entry.before)) entry.before[n] = before;
        levels[entry.dsp] = levelSetGain(editor, entry.dsp, before, entry.before[n] + dB);
      }
      return { snapshot: n, levels };
    }
    if (request.action === 'levelUndoPlan') {
      // Outputs this put on snapshots lose the controller and get their one old level back; outputs
      // that were per snapshot already need each changed snapshot visited (levelUndoHere).
      if (!levelUndo) throw new Error('Nothing to undo.');
      if (!levelSamePreset(levelUndo.preset)) {
        levelUndo = null;
        throw new Error('A different preset is loaded now; use HX Edit\'s own Undo.');
      }
      for (const entry of levelUndo.outputs.filter(entry => entry.whole || entry.assigned)) {
        if (entry.assigned && levelSet(editor, -1, 'controller', `dsp${entry.dsp}.outputA.gain.@controller`,
                                       { type: 1, int: 0 }, 0) < 0) {
          throw new Error(`Could not take path ${entry.dsp + 1}'s output level off snapshots.`);
        }
        const now = levelGet(editor, entry.dsp, 'outputA', 'gain');
        levelSetGain(editor, entry.dsp, typeof now === 'number' ? now : entry.original, entry.original);
        entry.before = {};
        entry.done = true;
      }
      const visit = [...new Set(levelUndo.outputs.filter(entry => !entry.done)
        .flatMap(entry => Object.keys(entry.before).map(Number)))];
      return { visit, snapshot: levelUndo.snapshot };
    }
    if (request.action === 'levelUndoHere') {
      const n = request.snapshot;
      if (!levelUndo) throw new Error('Nothing to undo.');
      levelHere(editor, n);
      for (const entry of levelUndo.outputs.filter(entry => !entry.done && n in entry.before)) {
        const now = levelGet(editor, entry.dsp, 'outputA', 'gain');
        levelSetGain(editor, entry.dsp, typeof now === 'number' ? now : entry.before[n], entry.before[n]);
        delete entry.before[n];
      }
      return { snapshot: n };
    }
    if (request.action === 'levelApplyEnd') {
      // forget: after an Undo (or a new measurement) there is nothing left to undo. record: what Undo
      // needs, kept by the app too (Undo of several presets, or after HX Edit restarted).
      if (request.forget) levelUndo = null;
      return Object.assign(levelState(editor), { canUndo: levelUndo !== null,
        record: levelUndo ? JSON.parse(JSON.stringify(levelUndo)) : null });
    }
    if (request.action === 'levelUndoLoad') {
      // An Apply's record from the app (levelApplyEnd's), for Undo of a preset loaded again.
      const record = request.record;
      if (!record || !Array.isArray(record.outputs) || !record.outputs.every(entry =>
          [0, 1].includes(entry?.dsp) && typeof entry.original === 'number' && entry.before && typeof entry.before === 'object')) {
        throw new Error('That undo record is not usable.');
      }
      if (!levelSamePreset(record.preset)) throw new Error('A different preset is loaded now.');
      levelUndo = JSON.parse(JSON.stringify(record));
      return Object.assign(levelState(editor), { canUndo: true });
    }
    if (request.action === 'levelSave') {
      // HX Edit's own File > Save Preset, into the preset's own place; levelState's savedAt moves when
      // the Floor confirms it (EditBuffer_PresetSavedToDevice).
      if (levelSaved) throw new Error('Measuring is still in progress.');
      if (active?.transfer) throw new Error('A preview is playing from User IR 128; end it before saving.');
      if (!editor.readPointer().add(0x118).readPointer().equals(base.add(LEVEL_SAVE))) {
        throw new Error('This HX Edit does not match the reviewed build; saving is unavailable.');
      }
      if (!levelSamePreset(request.preset)) {
        let now = null;
        try { now = namView.presetHeader?.() || null; } catch (_) { now = null; }
        throw new Error(`A different preset is loaded now (${now ? String(now.preset).padStart(3, '0') : 'not read'}); nothing was saved.`);
      }
      const where = request.preset;
      if (!Number.isInteger(where.setlist) || !Number.isInteger(where.preset) || where.setlist < 0 || where.setlist > 31 ||
          where.preset < 0 || where.preset > 127) throw new Error('Unknown preset.');
      const uri = `l6device://0x${FLOOR_DEVICE.toString(16).padStart(8, '0')}:/preset/${where.setlist}/${where.preset}`;
      const text = Memory.alloc(16);
      levelString(text, Memory.allocUtf8String(uri), 1);
      let result;
      try {
        result = new NativeFunction(base.add(LEVEL_SAVE), 'int', ['pointer', 'pointer'], 'thiscall')(editor, text);
      } finally { levelStringFree(text); }
      if (result < 0) throw new Error(`HX Edit refused to save preset ${String(where.preset).padStart(3, '0')}.`);
      return { sent: true, savedAt: levelSavedAt };
    }
    throw new Error('Unknown level action.');
  }

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
      command = { request, next, deadline, check, finishImport, since: Date.now() };
      if (!post(active.window, wake, 0, 0)) {
        command = null;
        reply(request, { error: 'HX Edit closed during the TONE3000 operation.' });
      } else watchWake(command);
    }
    check();
  }

  // A command runs when its wake message reaches HX Edit's window through DispatchMessageW. A modal loop
  // of HX Edit's own (seen: saving a preset while Level presets polled, Floor PC, November 16) can take
  // that message without dispatching it, and the command then waited forever: every later command was
  // refused as "still finishing" until the window closed. So the wake is posted again each second while
  // the command waits, and one still waiting after WAKE_GIVE_UP (the Python side stops waiting at 35 s)
  // is dropped rather than run late.
  const WAKE_GIVE_UP = 34000;
  function watchWake(pending) {
    setTimeout(() => {
      if (command !== pending) return;                     // ran (or was replaced)
      const session = active;
      if (!session || pending.request.session !== session.id) { command = null; return; }
      if (Date.now() - pending.since >= WAKE_GIVE_UP) {
        command = null;
        reply(pending.request, { error: 'HX Edit did not get to this step in time; it was not run.' });
        if (session.queuedClose || session.cleanupRequested) post(session.window, wake, 0, 0);
        return;
      }
      post(session.window, wake, 0, 0);
      watchWake(pending);
    }, 1000);
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

  // A preview whose block no longer plays the preview slot has ended in sound: an Undo of the
  // preview's Model change (seen on the Floor, November 13: the card kept saying "Previewing"
  // while the block was back on its own capture), or Model changed on an unlocked block. End it in
  // the session too, the way a snapshot change does: the block keeps what it plays now, and an
  // owned, unreferenced User IR 128 is released. Only once the change has held for 300 ms.
  let leftPreviewSince = 0;
  function watchPreviewLeft() {
    const transfer = active.transfer;
    if (!active.navigation || !transfer || active.pendingTransfer || active.applying || active.restoring ||
        active.inserting || active.cleanupRequested || active.quarantined || command || workflowRequest ||
        active.undoAfter === undefined || !targetOnScreen()) { leftPreviewSince = 0; return; }
    const slot = namView.modelSlot();
    if (!Number.isInteger(slot) || slot === transfer.slot) { leftPreviewSince = 0; return; }
    const now = Date.now();
    if (!leftPreviewSince) { leftPreviewSince = now; return; }
    if (now - leftPreviewSince < 300) return;
    leftPreviewSince = 0;
    send({type: 'toneTrace', reason: 'preview-left', slot, previewSlot: transfer.slot});
    invalidate('model-left-preview');
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
    // The capture the block ends on: its original one, or, when the preview ended because the
    // block itself was moved off the preview slot (watchPreviewLeft: an Undo, or Model changed on
    // an unlocked block), the one it plays now. Keeping 128 then only blocked the next preview.
    let keep = origin.slot;
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
        if (request.action !== 'autoEnd' || active.invalidatedBy !== 'model-left-preview' ||
            !Number.isInteger(current.slot) || current.slot < 1 || current.slot > 127) {
          return retained('block-edited', 'The original block was edited independently. Preview remains in User IR 128 for review.', current.slot);
        }
        keep = current.slot;
        cleanupTrace(request, 'kept-new-capture', current.slot);
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
        if (current.slot === keep && !clearing && !refreshed && targetOnScreen() &&
            namView.modelSlot() === transfer.slot) {
          // The block is restored but its control on screen still shows the preview (it came
          // back on screen after a typed restore). Bring the control to the same original slot.
          refreshed = true;
          active.applying = true;
          try { namView.setSlot(keep); }
          finally { active.applying = false; }
          return null;
        }
        if (current.slot !== keep || (targetOnScreen() && namView.modelSlot() !== keep)) {
          if (!clearing && (current.slot === transfer.slot || current.slot === keep) && !expired) return null;
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
      // A capture chosen from the library (Use, double-click, a drop) is locked like one picked
      // from the NAM category, even on a block that was added as a plain NAM A2 Lite.
      if (profile.namCategory) setLockState(active.controller, 'locked', 'library');
      checkCaptureLater(active.controller, slot);
      return transfer ? {loadedSlot:slot} : {usedSlot:slot};
    }, 3500);
  }

  // Optimize versions (NAM Pedal firmware builds): the NAM block a capture plays in, by model symbol.
  const NAM_BLOCKS = {HD2_AmpNAMLite: 'NAM A2 Lite', HD2_AmpNAMPedal: 'NAM Pedal'};

  function boundSlot() {
    try { const slot = namView.modelSlot(); return Number.isInteger(slot) ? slot : null; } catch (_) { return null; }
  }

  function boundModel() {
    // The bound block's model symbol, from the preset as the editor holds it; null when it cannot be read.
    try {
      const tree = namView.presetTree();
      const dsp = active.controller.add(0x80).readS32();
      const node = tree && tree[`dsp${dsp}`] && tree[`dsp${dsp}`][active.block];
      return node && typeof node['@model'] === 'string' ? node['@model'] : null;
    } catch (_) { return null; }
  }

  function pickModel(list, name) {
    // the model list's row for this block (NAM category rows carry trailing spaces): exact text first
    if (pick(list, name)) return true;
    for (let index = 0, count = rowCount(list); index < count; index++) {
      const row = getIrRow(list, index);
      if (row.isNull() || row.readUtf8String().trimEnd() !== name) continue;
      clearSelection(list, 0, 0);
      selectRow(list, 1, index, 0, 0);
      return true;
    }
    return false;
  }

  function switchModel(request, then) {
    // A version and its original play in different blocks: the bound NAM block's model changes with the slot
    // (as picking it in HX Edit's model list would; one Undo step), then `then` puts the capture on it.
    const symbol = request.model;
    if (!(symbol in NAM_BLOCKS)) throw new Error('Unknown NAM block for this version.');
    const current = boundModel();
    if (current === symbol) return then();
    if (current === null) throw new Error('Could not read the model of the selected block. Select the block again and retry.');
    if (!(current in NAM_BLOCKS)) throw new Error('The selected block is not a NAM block.');
    const list = active.controller.add(0x58).readPointer();
    if (list.isNull() || !pickModel(list, NAM_BLOCKS[symbol])) {
      throw new Error(`Open the selected block's model list in HX Edit, then choose this version again (${NAM_BLOCKS[symbol]}).`);
    }
    send({type: 'toneTrace', reason: 'version-model', from: current, to: symbol});
    modelEvent(active.controller, selectionEvent, list, NULL, NULL);
    poll(request, expired => {
      if (!active.controller || !controllerReady(active.controller)) throw new Error('The block went away while changing its model.');
      if (boundModel() !== symbol) {
        if (!expired) return null;
        throw new Error(`HX Edit did not change the block to ${NAM_BLOCKS[symbol]}. Nothing else was changed.`);
      }
      active.block = block(active.controller);
      active.position = position(active.controller);
      active.model = NAM_BLOCKS[symbol];
      captureTarget();
      then();
      return {};
    }, 3500);
  }

  function installPermanent(request) {
    if (active.transfer || active.pendingTransfer || active.orphanTransfer) throw new Error('End the current preview before loading a library capture.');
    if (typeof request.identity !== 'string' || !/^[a-f0-9]{64}$/.test(request.identity)) throw new Error('Invalid library capture identity.');
    if (request.model && !request.switched) {
      if (request.version !== undefined && request.version !== 'pedal') throw new Error('Unknown capture version.');
      request.switched = true;
      return switchModel(request, () => installPermanent(request));
    }
    const existing = namView.verifiedCaptureSlot(request.identity, undefined, request.version);
    if (existing) return applyPermanent(request, existing, null);
    if (request.action === 'use') throw new Error('The installed slot is no longer verified. Wait for verification, then try again.');
    const slot = namView.freeSlots(AUDITION_SLOT).find(value => value >= 1 && value <= 127);
    if (!slot) throw new Error('No empty permanent User IR slot is available. Preview slot 128 is reserved.');
    const transfer = namView.importPreview(request.file, slot, false);
    awaitImport(request, transfer, () => applyPermanent(request, slot, transfer));
  }

  function run(request) {
    if (typeof request.action === 'string' && request.action.startsWith('level')) {
      let result;
      try { result = levelAction(request); } catch (error) { result = { error: String(error.message) }; }
      return reply(request, result);
    }
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
      if (captureCheck) runCaptureCheck();
      if (profile.namCategory) {
        if (pendingPlain) { try { finishPendingPlain(); } catch (_) { pendingPlain = null; } }
        if (!pendingCapture) convertIrPick();
        if (irReplace && !pendingCapture) runIrReplace();
        namView.applyModelLock?.();
        refreshCaptureList();
        watchNamIcons(args[0].readPointer());
      }
      if (!active) return;
      try { watchPreviewLeft(); } catch (_) { leftPreviewSince = 0; }
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
      if (current && command && Date.now() - (command.since || 0) >= WAKE_GIVE_UP + 2000) {
        command = null;                                    // stale (see watchWake): never block what follows
      }
      if (current && !command && (!active.navigation || !workflowRequest)) {
        command = { request, since: Date.now() };
        if (!post(active.window, wake, 0, 0)) {
          command = null;
          reply(request, { error: 'The HX Edit window is unavailable.' });
        } else watchWake(command);
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
  return { openBrowser, makeNamBlock, selectedIsNam, startChainDrag, chainDragTook };
}
