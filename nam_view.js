'use strict';

// HX Edit 3.82 x86, verified by the launcher before these offsets are used.
// Read HX Edit's in-memory edit buffer (SF variant tree) without native calls.
// Layout from the Helix Floor library contribution (PR #4), pinned to HX Edit 3.82 x86.
function readPresetTree(root) {
  const limits = { nodes: 512, properties: 512, array: 2048, depth: 24, string: 8192 };
  let budget = 30000;
  const fail = reason => { throw new Error(reason); };
  const present = p => (p.isNull() ? fail('missing-memory') : p);
  const spend = () => { if (--budget < 0) fail('value-budget'); };
  function put(object, key, value) {
    if (typeof key !== 'string' || !key || Object.hasOwn(object, key)) fail('invalid-key');
    Object.defineProperty(object, key, { value, enumerable: true, writable: true, configurable: true });
  }
  function bounds(rep, begin, end, maximum) {
    const first = rep.add(begin).readU32(), last = rep.add(end).readU32();
    if (last < first || first > 8192 || last - first > maximum) fail('invalid-bounds');
    return [first, last];
  }
  function tree(first, left, right, visit) {
    const pending = [first], seen = new Set();
    while (pending.length) {
      const node = pending.pop();
      if (node.isNull()) continue;
      if (seen.has(node.toString()) || seen.size >= limits.nodes) fail('invalid-tree');
      seen.add(node.toString());
      spend();
      visit(node);
      pending.push(node.add(left).readPointer(), node.add(right).readPointer());
    }
  }
  function value(p, depth = 0) {
    present(p);
    spend();
    if (depth > limits.depth) fail('value-depth');
    const tag = p.readU32(), v = p.add(8);
    if (tag === 4) return v.readS32();
    if (tag === 5) return v.readU32();
    if (tag === 8) return v.readFloat();
    if (tag === 9) return v.readU32() === 0 ? null : fail('opaque-pointer');
    if (tag === 11) return v.readU8() === 1;
    if (tag === 10) {
      const rep = present(v.readPointer()).add(4).readPointer();
      if (rep.isNull()) return '';
      const length = rep.add(4).readU32();
      if (length > limits.string) fail('string-budget');
      return length ? present(rep.add(16).readPointer()).readUtf8String(length) : '';
    }
    if (tag === 12) {
      const rep = present(v.readPointer());
      const [first, last] = bounds(rep, 16, 20, limits.array);
      const data = rep.add(8).readPointer(), out = [];
      for (let i = first; i < last; i++) out.push(value(present(data).add(i * 16), depth + 1));
      return out;
    }
    if (tag === 13) {
      const out = {};
      tree(present(v.readPointer()).add(4).readPointer(), 40, 44,
        node => put(out, value(node.add(8), depth + 1), value(node.add(24), depth + 1)));
      return out;
    }
    fail('unsupported-variant-' + tag); // unknown values are never silently dropped
  }
  function properties(array) {
    const [first, last] = bounds(array, 20, 24, limits.properties);
    const data = array.add(12).readPointer(), out = {};
    for (let i = first; i < last; i++) {
      const pair = present(present(data).add(i * 4).readPointer());
      put(out, value(pair), value(pair.add(16)));
    }
    return out;
  }
  function nodes(offset) {
    const out = {};
    tree(root.add(offset + 4).readPointer(), 68, 72, node => put(out, value(node.add(8)), properties(node.add(24))));
    return out;
  }
  return { global: properties(root.add(24)), globalNodes: nodes(68), dsp0: nodes(76), dsp1: nodes(84) };
}

// Every IR slot the current preset references, or a reason it cannot be known.
// irParameters maps each model symbol to its ir_select parameters ([] = no IR).
function presetIrSlots(tree, irParameters) {
  const record = value => value !== null && typeof value === 'object' && !Array.isArray(value);
  const scalar = value => value === null || ['boolean', 'number', 'string'].includes(typeof value);
  const slots = new Set();
  if (Object.entries(tree.global).some(([key, value]) => !key.startsWith('@') || !scalar(value))) {
    return { reason: 'unsupported-global-properties' };
  }
  if (!Object.keys(tree.dsp0).length) return { reason: 'empty-signal-flow' };
  for (const dsp of ['dsp0', 'dsp1']) {
    for (const node of Object.values(tree[dsp])) {
      const model = record(node) ? node['@model'] : undefined;
      if (typeof model !== 'string' || model.startsWith('HelixPlugin_')) return { reason: 'unknown-block' };
      const parameters = irParameters[model] ?? irParameters[model.replace(/(Mono|Stereo)$/, '')];
      if (!parameters) return { reason: 'unknown-model' };
      for (const parameter of parameters) {
        const slot = node[parameter];
        if (!Number.isInteger(slot) || slot < 1 || slot > 128) return { reason: 'invalid-slot-value' };
        slots.add(slot);
      }
      // Controller or snapshot assignments would appear as nested values here.
      if (Object.values(node).some(value => !scalar(value))) return { reason: 'unsupported-block-value' };
    }
  }
  const snapshotKeys = new Set(['@name', '@tempo', '@valid', '@custom_name', '@ledcolor', '@pedalstate']);
  const metadata = new Set(['global', 'variax', 'dt0', 'dt1', 'dtdual', 'powercab0', 'powercab1', 'powercabdual', 'footswitch']);
  // Snapshot-controlled parameters (controllers.dsp0.block0.Bass = {@value, …}): an IR parameter's
  // values are slots too; anything shaped otherwise fails closed.
  function snapshotSlots(controllers) {
    for (const [dsp, blocks] of Object.entries(controllers)) {
      if (!['dsp0', 'dsp1'].includes(dsp) || !record(blocks)) return 'unsupported-snapshot-values';
      for (const [name, controlled] of Object.entries(blocks)) {
        const node = tree[dsp][name];
        if (!record(controlled) || !record(node)) return 'unsupported-snapshot-values';
        const parameters = irParameters[node['@model']] ?? irParameters[node['@model'].replace(/(Mono|Stereo)$/, '')];
        for (const [parameter, control] of Object.entries(controlled)) {
          if (!parameters.includes(parameter)) continue;
          const slot = record(control) ? control['@value'] : control;
          if (!Number.isInteger(slot) || slot < 1 || slot > 128) return 'invalid-slot-value';
          slots.add(slot);
        }
      }
    }
    return null;
  }
  for (const [name, node] of Object.entries(tree.globalNodes)) {
    if (!record(node)) return { reason: 'unsupported-global-node' };
    if (/^snapshot\d$/.test(name)) {
      for (const [key, value] of Object.entries(node)) {
        if (key === 'controllers' && record(value)) {
          const failed = snapshotSlots(value);
          if (failed) return { reason: failed };
        } else if (snapshotKeys.has(key) ? !scalar(value) : key !== 'blocks' || !record(value)) {
          return { reason: `unsupported-snapshot-values (${key})` };
        }
      }
    } else if (Object.keys(node).length && (!metadata.has(name) || Object.values(node).some(value => !scalar(value)))) {
      return { reason: 'unsupported-global-node' };
    }
  }
  return { slots };
}

function previewSlotUnused(preset, slot) {
  // A preview is owned by its session, but another block or snapshot can still
  // reference it. Ownership alone never authorizes destroying its samples.
  return Number.isInteger(slot) && slot >= 1 && slot <= 128 &&
    preset?.slots instanceof Set && !preset.slots.has(slot);
}

function friendlyNamCaption(slot, original, physicalName, title, room = 21) {
  if (!Number.isInteger(slot) || slot < 1 || slot > 128 ||
      typeof original !== 'string' || typeof physicalName !== 'string' || !physicalName ||
      typeof title !== 'string' || !title) return original;
  // Always lead with the User IR slot number: the friendly name alone does not say which slot
  // the block uses, and the same capture can be installed in more than one.
  let prefix = `${slot}: `;
  if (original !== '' && original !== physicalName) {
    const match = original.match(/^(\d+:\s*)(.*)$/);
    if (!match || Number(match[1].split(':')[0]) !== slot || match[2] !== physicalName) return original;
    prefix = match[1];
  }
  // The Model control is right-aligned and shows only the end of a long caption, which cut the
  // slot number and the start of the name off. Library names read "tone title – profile name":
  // prefer the profile name, which tells one tone's captures apart, then shorten from the end.
  // The control's font is proportional, so the room is estimated in rough glyph widths rather
  // than characters: on the Floor "16: EVH 5150III® 100S ste…" fitted while the equally long
  // "9: FNDR BFDRI VB Clean BA…" lost its first characters.
  // room: 21 by default; less when the Model lock's padlock sits left of the caption.
  const wide = text => {
    let total = 0;
    for (const ch of text) {
      total += /[MW]/.test(ch) ? 1.5 : /[mw]/.test(ch) ? 1.4 : /[A-HK-LN-VX-Z]/.test(ch) ? 1.25 :
        /[IJijl1.,:;' |!]/.test(ch) ? 0.55 : /[a-z]/.test(ch) ? 0.95 : /[ -~]/.test(ch) ? 1.0 : 1.3;
    }
    return total;
  };
  let name = title;
  if (wide(prefix + name) > room) {
    const profile = name.split(' – ').pop().trim();
    if (profile) name = profile;
    if (wide(prefix + name) > room) {
      const kept = [...name];
      while (kept.length > 1 && wide(prefix + kept.join('').trimEnd() + '…') > room) kept.pop();
      name = kept.join('').trimEnd() + '…';
    }
  }
  const result = prefix + name;
  let bytes = 0;
  for (const ch of result) {
    const code = ch.codePointAt(0);
    bytes += code <= 0x7f ? 1 : code <= 0x7ff ? 2 : code <= 0xffff ? 3 : 4;
  }
  return bytes <= 255 && !result.includes('\0') ? result : original;
}

function installNamView(profiles, savedSidebarWidth = 0, irParameters = {}, profile = {}) {
  const base = Process.mainModule.base;
  const address = rva => base.add(rva);
  const method = (rva, type, args) => new NativeFunction(address(rva), type, ['pointer', ...args], 'thiscall');
  const findWidget = method(0x3b7ae0, 'pointer', ['uint', 'int']);
  const setVisible = method(0x3bcff0, 'void', ['int', 'int']);
  const setEnabled = method(0x3b7940, 'void', ['int', 'int']);
  const redraw = method(0x3baa40, 'void', []);
  const getRadioButton = method(0x389f30, 'pointer', ['uint']);
  const isVisible = method(0x3ba100, 'uint8', ['int']);
  const getParent = method(0x3b8790, 'pointer', []);
  const getValue = method(0x3b8f20, 'int', []);
  const setValue = method(0x3bcb20, 'void', ['int', 'int', 'int']);
  const handleModelEvent = method(0x26b690, 'void', ['uint', 'pointer', 'pointer']);
  const appendRow = method(0x380940, 'void', ['int', 'pointer', 'pointer', 'int', 'int', 'int', 'int', 'int']);
  const getSelectedRows = method(0x3808a0, 'uint', ['pointer', 'uint']);
  const selectRow = method(0x3821d0, 'void', ['int', 'int', 'int', 'int']);
  const clearSelection = method(0x37f690, 'void', ['int', 'int']);
  const clearRows = method(0x37f5b0, 'void', ['int']);
  const refreshIrSelection = method(0x20df60, 'void', []);
  const copyIr = method(0x20afb0, 'void', []);
  const pasteIr = method(0x20b7c0, 'void', ['pointer', 'int']);
  const importIr = method(0x20b4b0, 'void', []);
  const exportIr = method(0x20b080, 'void', []);
  const clearIr = method(0x20aef0, 'void', []);
  const clearIrSlots = method(0x3da2b0, 'void', ['pointer']);
  const rebuildModelMenu = method(0x26e680, 'void', []);
  const configureTab = method(0x1f77e0, 'void', ['int', 'int']);
  const layoutTab = method(0x1f7460, 'void', []);
  const getAnchor = method(0x3b7f30, 'int', ['int']);
  const setAnchor = method(0x3bbef0, 'void', ['int', 'int', 'int']);
  const widgetOrigin = method(0x3b7e00, 'pointer', ['pointer']);
  const libraryViews = [0x70727677, 0x69727677, 0x66767677, 0x6e616d76]; // prvw, irvw, fvvw, namv
  const namListId = 0x6e6c7374; // nlst
  const buttonIds = { copy: 0x6e636f70, paste: 0x6e706173, import: 0x6e696d70,
    export: 0x6e657870, clear: 0x6e636c72 };
  const selectedRow = Memory.alloc(Process.pointerSize);
  const pending = new Map();
  let slots = [];
  let namList = NULL;
  let namPanel = NULL;
  let irController = NULL;
  let activeModel = NULL;
  let audition = null;
  let buttons = {};
  let tab = 0;
  let libraryRoot = NULL;
  let sidebarSink = null, sidebarChildReady = false, sidebarChildFailed = false, sidebarHost = NULL, sidebarPumping = false;
  const sidebarActions = [];
  const sidebarPoint = Memory.alloc(8);
  let libraryOpener = null, allCapturesButton = NULL;
  let libraryUiThread = 0, discoveryLocked = false, discoveryStatus = 'inactive';
  let discoveryChecked = 0, discoveryTotal = 0, discoveryCountedAt = -1;
  // slot -> {name, contentId}: what each permanent slot held when this PC last read it back
  // (contentId null: something that is not a library capture). Seeded from the supervisor's
  // file, kept current here, and used only while the device still shows the same slot name.
  const rememberedSlots = new Map();
  for (const entry of Array.isArray(profile.rememberedSlots) ? profile.rememberedSlots : []) {
    if (Array.isArray(entry) && Number.isInteger(entry[0]) && entry[0] >= 1 && entry[0] <= 127 &&
        typeof entry[1] === 'string' && entry[1] &&
        (entry[2] === null || (typeof entry[2] === 'string' && /^[0-9a-f]{64}$/.test(entry[2])))) {
      rememberedSlots.set(entry[0], {name: entry[1], contentId: entry[2]});
    }
  }
  // slot -> the slot's own name, read on the UI thread whenever the slot check takes a snapshot.
  // The check's change signature (readCString(256)) is the name followed by whatever bytes come
  // after it in the row; it is stable within a session but not a name. In the first Candidate62
  // session that made 25 of 27 "names" too long to store, and none comparable on the next launch.
  const slotNames = new Map();
  function forgetRememberedSlot(slot) {
    if (rememberedSlots.delete(slot)) send({type: 'slotMemory', slot, forget: true});
  }
  let exportPending = false, workflowBusy = false;
  let discoveryPause = null, discoveryControls = null, libraryInputDepth = 0, libraryBrowsing = false;
  const slotLedger = typeof createLibrarySlotLedger === 'function' ? createLibrarySlotLedger() : null;
  let inventoryDirty = false, inventoryState = 'unverified', inventoryFlushing = false, inventoryRetryAt = 0;
  let payloadMetadata = profile.payloadMetadata || {};
  let payloadRevision = 0;
  let displayRefreshing = false, lastModelAlias = null;

  // Proven Candidate53 text-only ABI. Guard the call-site but do not attach
  // inside the formatter: it already has an entry/onLeave interceptor below.
  const displaySite = address(0x26bfed);
  const displayBytes = [0x8b,0x8b,0x44,0x01,0x00,0x00,0x8d,0x95,0xf0,0xfe,0xff,0xff];
  if (!displayBytes.every((value, index) => displaySite.add(index).readU8() === value)) {
    throw new Error('The NAM display formatter does not match the reviewed HX Edit build.');
  }

  function libraryReady() {
    return libraryUiThread !== 0 && Process.getCurrentThreadId() === libraryUiThread;
  }

  function emitInventory() {
    if (slotLedger) send({ type: 'floorInventory', epoch: slotLedger.epoch(), state: inventoryState,
      entries: slotLedger.displayEntries(), verified: slotLedger.entries() });
  }

  function requestLibraryVerification() {
    discoveryControls?.requestVerification({allowWhileBrowsing:true});
  }

  function slotsChanged(slots) {
    // Exact native destinations only. Slot128 is preview storage, not a
    // permanent-library change; the coordinator filters it out.
    discoveryControls?.slotsChanged(slots);
  }

  function profileLabel(slot) {
    const content = slotLedger?.contentFor(slot);
    return content && payloadMetadata[content]?.name || profiles[slot];
  }

  // --- Model caption width -------------------------------------------------------------------
  // With the NAM categories a padlock sits at a fixed place left of the Model value, and the
  // value text, right-aligned, must end before it. Its width is measured rather than estimated:
  // HX Edit draws it in Roboto at 12 px, one to one with widget units (Floor, November 13:
  // GDI widths matched the screen within a few pixels), and it ends 12 units left of the value
  // menu's right edge. The caption is shortened until it fits the space up to the padlock.
  let captionGdi = null, captionReports = 12;
  function captionWidth(text) {
    try {
      if (!captionGdi) {
        const g = Process.getModuleByName('gdi32.dll'), f = (n, r, a) => new NativeFunction(g.getExportByName(n), r, a, 'stdcall');
        const dc = f('CreateCompatibleDC', 'pointer', ['pointer'])(NULL);
        const font = f('CreateFontW', 'pointer', ['int','int','int','int','int','uint','uint','uint','uint','uint','uint','uint','uint','pointer'])(
          -12, 0, 0, 0, 400, 0, 0, 0, 1, 0, 0, 5, 0, Memory.allocUtf16String('Roboto'));
        if (dc.isNull() || font.isNull()) return null;
        f('SelectObject', 'pointer', ['pointer','pointer'])(dc, font);
        captionGdi = {dc, extent: f('GetTextExtentPoint32W', 'int', ['pointer','pointer','int','pointer']), size: Memory.alloc(8)};
      }
      const wide = Memory.allocUtf16String(text);
      if (!captionGdi.extent(captionGdi.dc, wide, text.length, captionGdi.size)) return null;
      return captionGdi.size.readS32();
    } catch (_) { return null; }
  }
  function captionSpace(slider, menu) {
    // Widget units between the padlock (plus a small gap) and the end of the caption; null when
    // no padlock is shown (a plain NAM A2 Lite block, or no NAM categories).
    // Whichever padlock is shown: asking the lock state here was a moment early right after a
    // capture pick (the block had not reported its new capture yet) and gave the full width.
    if (!profile.namCategory || !modelLockQuery) return null;
    const button = Object.keys(LOCK_BUTTONS).map(name => lockButton(slider, name))
      .find(candidate => candidate && isVisible(candidate, 0));
    if (!button) return null;
    const value = box(menu), padlock = box(button);
    const space = (value.x + value.w - 12) - (padlock.x + padlock.w + 4);
    return space > 40 && space < 600 ? space : null;
  }

  function refreshDisplayedModelName() {
    if (displayRefreshing || !hasActiveModel()) return;
    displayRefreshing = true;
    try {
      const slider = activeModel;
      const label = slider.add(0x7c).readPointer(), format = slider.add(0x98).readPointer();
      if (label.isNull() || format.isNull()) return;
      const labelText = label.add(0x7c).readPointer(), displayType = format.add(0x10).readPointer();
      if (labelText.isNull() || displayType.isNull() || labelText.readCString() !== 'Model' ||
          displayType.readCString() !== 'ir_select') return;
      const slot = getValue(slider) + 1, list = irList();
      if (!Number.isInteger(slot) || slot < 1 || slot > 128 || list.isNull()) return;
      const row = getIrRow(list, slot - 1);
      if (row.isNull() || !row.add(0x10c).readU32()) return;
      const physicalName = row.readCString(), title = profileLabel(slot);
      const menu = slider.add(0x144).readPointer();
      if (menu.isNull()) return;
      const table = menu.readPointer(), setter = table.add(0x1b8).readPointer(), getter = table.add(0x1bc).readPointer();
      const end = base.add(Process.mainModule.size);
      if ([setter, getter].some(fn => fn.compare(base) < 0 || fn.compare(end) >= 0)) return;
      const readText = new NativeFunction(getter, 'pointer', ['pointer'], 'thiscall');
      const text = readText(menu);
      if (text.isNull()) return;
      const original = text.readCString(), previous = lastModelAlias;
      const source = previous && previous.menu.equals(menu) && previous.slot === slot &&
        previous.physicalName === physicalName && previous.text === original ? previous.source : original;
      let replacement = friendlyNamCaption(slot, source, physicalName, title || physicalName);
      const space = captionSpace(slider, menu);
      if (space !== null) {
        // Shorten until the measured caption ends before the padlock ("127: NAM AC Deluxe Re…"
        // ran under it when only estimated). Without a measurement, two glyphs fewer.
        for (let room = 21; room >= 6; room--) {
          const candidate = friendlyNamCaption(slot, source, physicalName, title || physicalName, room);
          const width = captionWidth(candidate);
          if (width === null) { replacement = friendlyNamCaption(slot, source, physicalName, title || physicalName, 19); break; }
          replacement = candidate;
          if (width <= space) break;
        }
        if (captionReports-- > 0) send({type: 'toneTrace', reason: 'caption-fit', text: replacement, width: captionWidth(replacement), space, original});
      }
      if (replacement === original) return;
      const setText = new NativeFunction(setter, 'void', ['pointer', 'pointer', 'int'], 'thiscall');
      setText(menu, Memory.allocUtf8String(replacement), 0);
      lastModelAlias = {menu, slot, physicalName, source, text:replacement};
      // Lay the value out again for the new text. After a capture pick HX Edit had placed the
      // long native name and kept its position for the shorter caption, which then started about
      // ten units further left, under the padlock (Floor, November 13). Re-applying the menu's own
      // right anchor with a layout pass places it from the right edge again.
      try { setAnchor(menu, 1, getAnchor(menu, 1), 1); } catch (_) { /* drawn as before */ }
      redraw(menu);
    } catch (error) {
      send({type:'toneTrace', phase:'model-display-refresh-skipped', reason:String(error)});
    } finally { displayRefreshing = false; }
  }

  function flushInventoryPresentation() {
    if (!inventoryDirty || inventoryFlushing || Date.now() < inventoryRetryAt || !libraryReady() || irController.isNull() ||
        discoveryLocked || exportPending || workflowBusy || audition ||
        !namImportController.isNull() || toneImport) return;
    const list = irList(), manager = irController.add(0x270).readPointer();
    if (list.isNull() || manager.isNull() || manager.add(0x40).readU8() !== 0) {
      // Preserve the dirty commit while native storage is unavailable/busy.
      // Only the UI wake reads these pointers; timer-side retries are bounded.
      inventoryRetryAt = Date.now() + 1000;
      return;
    }
    inventoryFlushing = true;
    try {
      inventoryRetryAt = 0;
      inventoryDirty = false;
      if (profile.nativeDiscovery && slotLedger) {
        const display = new Set(slotLedger.displayEntries().map(([slot]) => slot));
        for (const slot of Object.keys(profiles).map(Number)) {
          if (!display.has(slot) && !slotLedger.importCurrent(slot)) delete profiles[slot];
        }
        if (!list.isNull()) for (const [slot] of slotLedger.displayEntries()) {
          const row = getIrRow(list, slot - 1);
          if (!row.isNull() && row.add(0x10c).readU32()) profiles[slot] = row.readUtf8String();
        }
      }
      renderProfiles();
      if (hasActiveModel()) rebuildModelMenu(activeModel);
      refreshDisplayedModelName();
      send({type:'floorInventoryPresentation',epoch:slotLedger?.epoch() || 0,
        profileRows:slots.length,displayedSlots:slotLedger?.displayEntries().length || 0,
        verifiedSlots:slotLedger?.entries().length || 0,nativeListReady:!namList.isNull(),
        nativeListHandle:namList.toString(),selectedTab:tab,
        listVisible:!namList.isNull() && !!isVisible(namList,1),uiThreadReady:true});
    } finally { inventoryFlushing = false; }
  }

  function needsInventoryPresentation() {
    // Timer-side scheduling only: cached state, no native widget access.
    return inventoryDirty && !inventoryFlushing && Date.now() >= inventoryRetryAt && !irController.isNull() && !discoveryLocked && !exportPending &&
      !workflowBusy && !audition && namImportController.isNull() && !toneImport;
  }

  const discoveryCount = method(0x379fb0, 'uint', []);
  const discoverySubmit = method(0x3f5f40, 'void', ['pointer', 'pointer', 'uint8']);
  const discoveryString = method(0x1a1640, 'pointer', ['pointer', 'int']);
  const discoveryStringDestroy = method(0x1a0120, 'void', []);

  function discoverySnapshot(revision) {
    if (!libraryReady() || irController.isNull()) return null;
    if (discoveryLocked || exportPending || workflowBusy || audition ||
        !namImportController.isNull() || toneImport) return null;
    flushInventoryPresentation();
    const list = irList(), manager = irController.add(0x270).readPointer();
    if (list.isNull() || manager.isNull() || discoveryCount(list) !== 128) return null;
    if (manager.add(0x40).readU8() !== 0) return null;
    const device = manager.add(0x44).readU32();
    if (device !== 0x210001) return null;
    const occupied = [], names = [];
    let excluded = 0;
    for (let i = 0; i < 127; i++) {
      const row = getIrRow(list, i);
      if (row.isNull()) return null;
      if (!row.add(0x10c).readU32()) { slotNames.delete(i + 1); continue; }
      if (row.add(0x114).readU32() & 0x10) { excluded++; continue; }
      occupied.push(i + 1); names.push([i + 1, row.readCString(256)]);
      try { slotNames.set(i + 1, row.readUtf8String()); } catch (_) { slotNames.delete(i + 1); }
    }
    return { device, revision, identity: irController + ':' + manager, slots: occupied,
      excluded, signature: JSON.stringify(names), slotSignatures: names, busy: false };
  }

  function exportDiscoverySlot(request, expected, observeSubmission) {
    if (typeof observeSubmission !== 'function') return false;
    const current = discoverySnapshot(expected.revision);
    if (!current || current.busy || current.identity !== expected.identity ||
        current.signature !== expected.signature || !current.slots.includes(request.slot) ||
        current.device !== request.device || current.revision !== request.revision ||
        typeof request.path !== 'string' || !/^[A-Za-z]:\\[^\0]+\.wav$/i.test(request.path)) return false;
    const index = Memory.alloc(4); index.writeU32(request.slot - 1);
    const path = Memory.alloc(8), text = Memory.allocUtf8String(request.path);
    function vector(vtable, data) {
      const result = Memory.alloc(24);
      result.writePointer(address(vtable)); result.add(4).writeU32(1);
      result.add(8).writePointer(data); result.add(12).writeU32(1);
      result.add(16).writeU32(0); result.add(20).writeU32(1);
      return result;
    }
    discoveryString(path, text, 1);
    try {
      const indices = vector(0x8bb0dc, index), paths = vector(0x8b42ac, path);
      const manager = irController.add(0x270).readPointer();
      return observeSubmission(request, manager, [indices, paths],
        () => discoverySubmit(manager, indices, paths, 0)) === true;
    } finally { discoveryStringDestroy(path); }
  }

  // HXCustomTab lays tabs out by its own visual index, not by X or radio index.
  // Show NAM before Favorites; native tab indexes and controllers stay unchanged.
  function orderTabs() {
    const radio = findWidget(libraryRoot, 0x6c627462, 1); // lbtb
    if (radio.isNull() || !radio.readPointer().equals(address(0x915358))) return;
    const favorites = getRadioButton(radio, 2);
    const nam = getRadioButton(radio, 3);
    if ([favorites, nam].some(tab => tab.isNull() || !tab.readPointer().equals(address(0x8c35a0)))) return;
    if (nam.add(0x218).readS32() === 2 && favorites.add(0x218).readS32() === 3) return;
    for (const [tab, index] of [[favorites, 3], [nam, 2]]) {
      configureTab(tab, 1, index);
      layoutTab(tab);
    }
    redraw(radio);
  }

  // Drag the boundary between the librarian and the editor. main.xml starts at
  // the saved width; this only moves the native anchors and normal relayout.
  const sidebarIds = { library: 0x6c697677, editor: 0x65647677 }; // livw, edvw
  const sidebarLimits = { minimum: 220, maximum: 600, editor: 640, grip: 4 };
  const origin = Memory.alloc(8);
  const clientRect = Memory.alloc(16);
  const winApi = (name, type, args) => new NativeFunction(user32.getExportByName(name), type, args, 'stdcall');
  const getClientRect = winApi('GetClientRect', 'int', ['pointer', 'pointer']);
  const getAncestor = winApi('GetAncestor', 'pointer', ['pointer', 'uint']);
  const getWindowOwner = winApi('GetWindowThreadProcessId', 'uint', ['pointer', 'pointer']);
  const windowOwner = Memory.alloc(4);
  const setCapture = winApi('SetCapture', 'pointer', ['pointer']);
  const getCapture = winApi('GetCapture', 'pointer', []);
  const releaseCapture = winApi('ReleaseCapture', 'int', []);
  const setCursor = winApi('SetCursor', 'pointer', ['pointer']);
  const resizeCursor = winApi('LoadCursorW', 'pointer', ['pointer', 'pointer'])(NULL, ptr(32644)); // IDC_SIZEWE
  let sidebarWidth = savedSidebarWidth;
  let drag = null;

  function canonical(widget) {
    // Some widgets hand their geometry to a proxy widget.
    for (let depth = 0; depth < 8 && !widget.isNull(); depth++) {
      const geometry = widget.add(0x54).readPointer();
      if (geometry.isNull()) return NULL;
      const proxy = geometry.add(0xb4).readPointer();
      if (proxy.isNull()) return widget;
      widget = proxy;
    }
    return NULL;
  }

  function box(widget) {
    const geometry = widget.add(0x54).readPointer();
    const x = geometry.add(4).readS32();
    const y = geometry.add(8).readS32();
    return { x, y, w: geometry.add(0xc).readS32() - x, h: geometry.add(0x10).readS32() - y };
  }

  function sidebar() {
    for (let widget = canonical(libraryRoot), depth = 0; !widget.isNull() && depth < 8; depth++) {
      const library = canonical(findWidget(widget, sidebarIds.library, 0));
      const editor = canonical(findWidget(widget, sidebarIds.editor, 0));
      if (!library.isNull() && !editor.isNull()) {
        if (!isVisible(library, 1)) return null;
        return { library, editor, parent: box(widget), side: box(library) };
      }
      widget = canonical(getParent(widget));
    }
    return null;
  }

  function resizeSidebar(view, width) {
    const maximum = Math.min(sidebarLimits.maximum, view.parent.w - view.side.x - sidebarLimits.editor);
    if (maximum < sidebarLimits.minimum) return;
    width = Math.max(sidebarLimits.minimum, Math.min(maximum, Math.round(width)));
    if (view.side.w === width && getAnchor(view.editor, 3) === view.side.x + width) return;
    setAnchor(view.library, 1, view.parent.w - view.side.x - width, 1);
    setAnchor(view.editor, 3, view.side.x + width, 1);
  }

  // Keep the chosen width after window resizes and the librarian show/hide button.
  function syncSidebar() {
    if (!sidebarWidth || drag) return;
    const view = sidebar();
    if (view) resizeSidebar(view, sidebarWidth);
  }

  function sidebarEdge(hwnd, x, y) {
    const view = sidebar();
    if (!view || !getAncestor(hwnd, 2).equals(hwnd) || !getClientRect(hwnd, clientRect) ||
        clientRect.add(8).readS32() < view.parent.w) return null; // the main editor window only
    widgetOrigin(view.library, origin);
    const left = origin.readS32();
    const top = origin.add(4).readS32();
    const near = Math.abs(x - (left + view.side.w)) <= sidebarLimits.grip && y >= top && y < top + view.side.h;
    return near ? { view, left } : null;
  }

  function libraryWindow() {
    const hwnd = getActiveWindow(), view = sidebar();
    if (!view || hwnd.isNull() || !getAncestor(hwnd, 2).equals(hwnd) || !getClientRect(hwnd, clientRect)) return NULL;
    windowOwner.writeU32(0); getWindowOwner(hwnd, windowOwner);
    if (windowOwner.readU32() !== Process.id || clientRect.add(8).readS32() < view.parent.w ||
        clientRect.add(12).readS32() < view.side.y + view.side.h) return NULL;
    return hwnd;
  }

  // Local child-pane geometry only: never exports a slot or touches the device.
  function sidebarPresentation() {
    if (!libraryReady()) return null;
    const view = sidebar();
    if (!view || namPanel.isNull() || sidebarHost.isNull()) return {visible:false};
    const visible = tab === 3 && !!isVisible(namPanel, 1);
    if (!visible) return {visible:false};
    if (sidebarChildReady || sidebarChildFailed) {
      for (const widget of [namList, findWidget(namPanel, 0x6e626172, 1),
          findWidget(namPanel, 0x6e6d7367, 1), allCapturesButton]) {
        if (!widget.isNull() && isVisible(widget, 0)) setVisible(widget, 0, 1);
      }
      const error = findWidget(namPanel, 0x6e657272, 1); // nerr
      if (!error.isNull() && !!isVisible(error,0) !== sidebarChildFailed) setVisible(error,Number(sidebarChildFailed),1);
    }
    if (discoveryStatus === 'verifying' && discoveryCountedAt !== discoveryChecked) {
      // Permanent slots that hold something: what this pass has to read. Counted once per step.
      discoveryCountedAt = discoveryChecked;
      const list = irList();
      let total = 0;
      if (!list.isNull()) for (let slot = 1; slot < AUDITION_SLOT; slot++) {
        const row = getIrRow(list, slot - 1);
        if (!row.isNull() && row.add(0x10c).readU32()) total++;
      }
      discoveryTotal = total;
    }
    widgetOrigin(namPanel, sidebarPoint);
    const geometry = box(namPanel);
    return {visible:true, bounds:{x:sidebarPoint.readS32(), y:sidebarPoint.add(4).readS32(), w:geometry.w, h:geometry.h},
      sidebarWidth:view.side.w, resizeAvailable:true, busy:workflowBusy || discoveryLocked || exportPending,
      targetReady:!!hasActiveModel(), discoveryStatus, discoveryChecked, discoveryTotal,
      slotEpoch:slotLedger?.epoch() || 0};
  }

  function pumpSidebarPresentation(hwnd = NULL) {
    if (!libraryReady() || sidebarPumping) return;
    sidebarPumping = true;
    try {
      if (!hwnd.isNull()) {
        const root = getAncestor(hwnd, 2), view = sidebar();
        windowOwner.writeU32(0);getWindowOwner(root, windowOwner);
        if (view && windowOwner.readU32() === Process.id && getClientRect(root, clientRect) &&
            clientRect.add(8).readS32() >= view.parent.w && clientRect.add(12).readS32() >= view.side.y + view.side.h)
          sidebarHost = root;
      }
      for (let i=0;i<8 && sidebarActions.length;i++) sidebarActions.shift()(sidebarHost);
      if (sidebarSink && !sidebarHost.isNull()) sidebarSink(sidebarPresentation(), sidebarHost);
    } finally { sidebarPumping = false; }
  }

  function endSidebarDrag(saveWidth = false) {
    const previous = drag;
    drag = null; // ReleaseCapture can synchronously notify native window code.
    if (!previous) return;
    if (getCapture().equals(previous.hwnd)) releaseCapture();
    if (saveWidth) {
      const view = sidebar();
      if (view) {
        sidebarWidth = view.side.w;
        send({ type: 'sidebarWidth', width: sidebarWidth });
      }
    }
  }

  Interceptor.attach(user32.getExportByName('DispatchMessageW'), {
    onEnter(args) {
      if (!libraryReady()) return;
      this.sidebarUi = true;
      const msg = args[0];
      const event = msg.add(4).readU32();
      this.event = event;
      // Only an actual native mouse/key dispatch can open the NAM tab for
      // verification. Constructor restoration and transfer progress also call
      // the tab function, and must never arm a scan by themselves.
      if ([0x100, 0x201, 0x202].includes(event)) {
        this.libraryInput = true;
        libraryInputDepth++;
      }
      const hwnd = msg.readPointer();
      this.sidebarHwnd = hwnd;
      if (drag) {
        // Capture/focus notifications may be sent directly to WindowProc and
        // never pass DispatchMessage. Check ownership on every UI message too.
        const cancelled = hwnd.equals(drag.hwnd) && ([0x215, 0x1f, 8, 2, 0x82].includes(event)
          || event === 6 && (msg.add(8).readU32() & 0xffff) === 0
          || event === 0x1c && msg.add(8).readU32() === 0);
        const released = event === 0x200 && (msg.add(8).readU32() & 1) === 0;
        if (cancelled || released || !getCapture().equals(drag.hwnd)) {
          endSidebarDrag();
          return;
        }
      }
      if (event !== 0x200 && event !== 0x201 && event !== 0x202 && event !== 0x203) return;
      const position = msg.add(12).readU32();
      const x = (position << 16) >> 16;
      const y = position >> 16;
      if (drag) {
        if (!hwnd.equals(drag.hwnd)) return;
        msg.add(4).writeU32(0); // WM_NULL: the editor never sees the drag
        const view = sidebar();
        if (view) resizeSidebar(view, x - drag.left);
        if (event === 0x202) endSidebarDrag(true);
        setCursor(resizeCursor);
        return;
      }
      const edge = event === 0x202 ? null : sidebarEdge(hwnd, x, y);
      if (!edge) return;
      this.cursor = true;
      if (event === 0x201) {
        const captured = getCapture();
        if (!captured.isNull() && !captured.equals(hwnd)) return;
        setCapture(hwnd);
        if (!getCapture().equals(hwnd)) return;
        msg.add(4).writeU32(0);
        drag = { hwnd, left: edge.left };
      } else if (event === 0x203) {
        msg.add(4).writeU32(0);
        resizeSidebar(edge.view, sidebarLimits.minimum);
        sidebarWidth = sidebarLimits.minimum;
        send({ type: 'sidebarWidth', width: sidebarWidth });
      }
    },
    onLeave() {
      if (this.libraryInput) libraryInputDepth--;
      if (!this.sidebarUi) return;
      if (this.cursor) setCursor(resizeCursor);
      if (this.event === 5 || this.event === 0xf || this.event === 0x202) syncSidebar(); // size, paint, button up
      pumpSidebarPresentation(this.sidebarHwnd || NULL);
    },
  });

  function selectedSlot() {
    if (namList.isNull() || !getSelectedRows(namList, selectedRow, 1)) return 0;
    return selectedRow.readPointer().add(0x100).readS32() + 1;
  }

  function irList() {
    return irController.isNull() ? NULL : irController.add(0x3c).readPointer();
  }

  function hasProfile(list, slot) {
    if (list.isNull() || !Object.hasOwn(profiles, slot)) return false;
    if (profile.nativeDiscovery && slotLedger && !slotLedger.contentFor(slot) && !slotLedger.importCurrent(slot)) return false;
    const row = getIrRow(list, slot - 1);
    return !row.isNull() && !!row.add(0x10c).readU32() && row.readUtf8String() === profiles[slot];
  }

  function copiedSlot(list) {
    const start = irController.add(0x58).readU32();
    const end = irController.add(0x5c).readU32();
    const clipboard = irController.add(0x50).readPointer();
    if (end - start !== 1 || clipboard.isNull()) return 0;
    const slot = clipboard.add(start * 4).readU32() + 1;
    return hasProfile(list, slot) ? slot : 0;
  }

  function refreshButtons() {
    if (namPanel.isNull()) return;
    const list = irList();
    const selected = hasProfile(list, selectedSlot());
    const free = firstFreeSlot(list, 1) >= 0;
    const enabled = { copy: selected, paste: free && !!copiedSlot(list),
      import: free && namImportController.isNull(), export: selected, clear: selected };
    for (const [action, button] of Object.entries(buttons)) {
      if (!button.isNull()) setEnabled(button, Number(enabled[action]), 1);
    }
  }

  function selectStockRow(list, index) {
    clearSelection(list, 0, 0);
    selectRow(list, 1, index, 0, 0);
    refreshIrSelection(irController);
  }

  function runAction(action) {
    const list = irList();
    if (list.isNull()) return;
    if (action === 'import' || action === 'paste') {
      const target = firstFreeSlot(list, 1);
      if (target < 0) return;
      if (action === 'import') {
        if (!namImportController.isNull()) return;
        selectStockRow(list, target);
        namImportController = irController;
        importIr(irController);
      } else {
        const source = copiedSlot(list);
        if (!source) return;
        pending.set(target + 1, profiles[source]);
        pasteIr(irController, irController.add(0x48), target);
      }
    } else {
      const slot = selectedSlot();
      if (!hasProfile(list, slot)) return;
      selectStockRow(list, slot - 1);
      if (action === 'copy') copyIr(irController);
      if (action === 'export') exportIr(irController);
      if (action === 'clear') clearIr(irController);
    }
    refreshButtons();
  }

  function renderProfiles() {
    slots = Object.keys(profiles).map(Number).sort((a, b) => a - b);
    if (namList.isNull()) return;
    const previous = selectedSlot();
    clearRows(namList, 0);
    for (const slot of slots) {
      appendRow(namList, -1, Memory.allocUtf8String(profileLabel(slot)), NULL, 0, slot - 1, 1, 0, 0);
    }
    const message = findWidget(namPanel, 0x6e6d7367, 1); // nmsg
    if (!message.isNull()) setVisible(message, Number(slots.length === 0), 1);
    if (hasActiveModel()) syncSelection();
    else {
      const index = slots.indexOf(previous);
      if (index >= 0) selectRow(namList, 1, index, 0, 0);
    }
    redraw(namList);
  }

  function refreshProfiles(controller) {
    if (!controller.equals(irController)) return;
    const list = irList();
    if (list.isNull()) return;
    let changed = false;
    for (const slot of new Set([...slots, ...pending.keys()])) {
      const row = getIrRow(list, slot - 1);
      if (row.isNull()) continue;
      const name = row.readUtf8String();
      if (!row.add(0x10c).readU32()) {
        if (slotLedger?.forget(slot)) emitInventory();
        if (Object.hasOwn(profiles, slot)) {
          delete profiles[slot];
          changed = true;
        }
        continue;
      }
      if (Object.hasOwn(profiles, slot) && profiles[slot] !== name) {
        if (slotLedger?.forget(slot)) emitInventory();
        delete profiles[slot]; changed = true;
      }
      const expected = pending.get(slot);
      if (expected && name && (name === expected || expected.startsWith(name) && name.length >= 31)) {
        pending.delete(slot);
        profiles[slot] = name;
        changed = true;
      }
    }
    if (changed) {
      renderProfiles();
      if (hasActiveModel()) rebuildModelMenu(activeModel);
    }
    refreshButtons();
  }

  function hasActiveModel() {
    // HX Edit caches the parameter widgets after detaching them from the editor.
    // A strict boolean: isVisible returns the native 0/1, and the sidebar bridge compares its
    // targetReady flag with true. The number made every Load/Use on Floor in the NAM tab
    // unavailable ("Select a NAM block…") while a NAM block was selected.
    return !activeModel.isNull() && !getParent(activeModel).isNull() && !!isVisible(activeModel, 1);
  }

  function setModelSlot(slot) {
    if (!hasActiveModel()) return false;
    const slider = activeModel;
    const value = slot - 1;
    if (value === getValue(slider)) return true;
    const popup = slider.add(0x144).readPointer();
    if (popup.isNull()) return false;
    // The native popup stores the slot before handing its change to Model. The Model lock
    // applies to the on-screen control only: this deliberate change always passes.
    modelLockOwn++;
    try {
      setValue(popup, value, 0, 0);
      handleModelEvent(slider, 0x21617663, popup, NULL); // cva!
    } finally { modelLockOwn--; }
    // The caption is refreshed by a hook on the Model's value setter, which Frida does not run
    // for a call made from inside another hook (previews apply from the message hook): the
    // preview's "128: …" caption stayed unshortened under the padlock. Refresh it here.
    try { syncSelection(); } catch (_) { /* presentation only */ }
    return true;
  }

  // --- Model lock ---------------------------------------------------------------------------
  // With the NAM categories, a block whose capture was picked from a category list (or that came
  // with a preset) keeps it until the user unlocks it with the padlock left of the Model value.
  // tone_view decides per block; modelLockQuery.state() answers 'locked', 'unlocked', 'plain'
  // (a plain NAM A2 Lite block: no padlock), 'none' (not a NAM block) or null when the Model
  // control on screen cannot be tied to a block yet (then nothing is changed), and
  // modelLockQuery.toggle() flips the cursor block. Locked means: the value children (slider,
  // number box, value menu) are disabled, so click, drag and stepper arrows do nothing
  // (BLOCK-ICON-RESEARCH.md Q3); the row itself stays enabled so its padlock can be clicked; a
  // double-click on the row is refused; and any value change a child still reports (mouse wheel,
  // keys) is turned into the child's own "set, not by the user" notice and the child put back.
  // Picks from the category list, the NAM tab and previews go through setModelSlot and pass.
  // The padlocks are two hidden push buttons that nam_catalog.patch_model_lock adds to every
  // parameter row (slider.xml); only a NAM block's Model row shows one.
  let modelLockQuery = null, modelLockOwn = 0, modelLockCheckAt = 0, modelLockReports = 20;
  let padlockPainted = '', padlockReshows = [];
  let captionRefit = false, rightClickOpen = false, rightClickUntil = 0;
  function openForRightClick() {
    try {
      if (!hasActiveModel()) return false;
      let opened = false;
      for (const offset of MODEL_VALUE_CHILDREN) {
        const child = activeModel.add(offset).readPointer();
        if (!child.isNull() && modelLockDisabled.has(child.toString())) { setEnabled(child, 1, 0); opened = true; }
      }
      if (opened) { rightClickOpen = true; rightClickUntil = Date.now() + 10000; }
      return opened;
    } catch (_) { return false; }
  }
  const modelLockDisabled = new Set();      // value children this code disabled (only these are re-enabled)
  const MODEL_VALUE_EVENTS = [0x21617663, 0x21766362, 0x21766365];   // cva! / bcv! / ecv!
  const MODEL_VALUE_CHILDREN = [0x13c, 0x140, 0x144];                // sldr, edit, menu
  const LOCK_BUTTONS = {locked: 0x6e6c6b64, unlocked: 0x6e756c6b};   // nlkd, nulk
  function modelLockTrace(stage, extra = {}) {
    if (modelLockReports-- > 0) send({type: 'toneTrace', reason: 'model-lock', stage, ...extra});
  }
  function modelLockState() {
    try { return modelLockQuery && hasActiveModel() ? modelLockQuery.state() : null; } catch (_) { return null; }
  }
  function lockButton(view, state) {
    const button = findWidget(view, LOCK_BUTTONS[state], 1);
    return button.isNull() ? null : button;
  }
  function applyModelLock(force = false) {
    if (!modelLockQuery) return;
    const now = Date.now();
    if (!force && now < modelLockCheckAt) return;
    modelLockCheckAt = now + 100;
    try {
      if (!hasActiveModel()) return;
      const view = activeModel, state = modelLockState();
      if (state === null) return;
      const locked = state === 'locked' && !rightClickOpen;
      for (const offset of MODEL_VALUE_CHILDREN) {
        const child = view.add(offset).readPointer();
        if (child.isNull()) continue;
        const key = child.toString(), enabled = child.add(0x54).readPointer().add(0x55).readU8() !== 0;
        if (locked && enabled) { setEnabled(child, 0, 1); modelLockDisabled.add(key); }
        else if (!locked && !enabled && modelLockDisabled.has(key)) { setEnabled(child, 1, 1); modelLockDisabled.delete(key); }
        else if (!locked) modelLockDisabled.delete(key);
      }
      for (const name of Object.keys(LOCK_BUTTONS)) {
        const button = lockButton(view, name);
        if (!button) { if (name === 'locked' && locked) modelLockTrace('padlock-missing'); continue; }
        const show = Number(state === name);
        if (Number(!!isVisible(button, 0)) !== show) {
          setVisible(button, show, 1);
          captionRefit = true;
          if (show) {
            padlockReshows = [now + 300, now + 1500, now + 4000];
            modelLockTrace(name);
          }
        }
        if (show) {
          // Shown while HX Edit is still starting, the padlock was not painted, though its widget
          // state matched a painted one byte for byte (seen on the Floor); the redraws asked for
          // then are lost. So hide and show it again when it moves, and a few times in the
          // seconds after it appears. A push button picks its image frame from its state, which
          // passing through disabled sets.
          const where = `${button}|${getParent(view)}|${JSON.stringify(box(button))}|${JSON.stringify(box(view))}`;
          const due = padlockReshows.length > 0 && now >= padlockReshows[0];
          if (where !== padlockPainted || due) {
            if (due) padlockReshows.shift();
            padlockPainted = where;
            setVisible(button, 0, 0);
            setVisible(button, 1, 0);
            setEnabled(button, 0, 0);
            setEnabled(button, 1, 1);
          }
        }
      }
      // The space for the caption changed with the padlock: fit it again.
      if (captionRefit) { captionRefit = false; refreshDisplayedModelName(); }
    } catch (error) { modelLockTrace('apply-skipped', {error: String(error.message)}); }
  }

  // A right click on a locked Model: its value controls take it as usual (HX Edit's own menu,
  // controller assignment), so they are enabled for it. Locked, they ignored right clicks, and the
  // menu's items too, since the menu belongs to the value control. They stay enabled until the
  // next left click (a choice, or a click elsewhere), Escape, or ten seconds; changes of the Model
  // value are refused throughout by the value-event guard.
  Interceptor.attach(user32.getExportByName('DispatchMessageW'), {
    onEnter(args) {
      if (!modelLockQuery) return;
      try {
        const msg = args[0], event = msg.add(4).readU32();
        if ((event === 0x204 || event === 0x205 || event === 0x206) && modelLockDisabled.size > 0) openForRightClick();
        if (rightClickOpen && (event === 0x202 || (event === 0x100 && msg.add(8).readU32() === 0x1b) ||
            Date.now() > rightClickUntil)) this.rightClickDone = true;
      } catch (_) { /* not a message this cares about */ }
    },
    onLeave() { if (this.rightClickDone) { rightClickOpen = false; applyModelLock(true); } },
  });

  Interceptor.attach(address(0x26c440), {                // the row's double-click / Enter: edit or popup
    onEnter() {
      if (!modelLockQuery || modelLockOwn || activeModel.isNull() || !this.context.ecx.equals(activeModel)) return;
      if (modelLockState() !== 'locked') return;
      // It starts by testing the row's enabled flag: report it disabled for this call only.
      const flag = activeModel.add(0x54).readPointer().add(0x55);
      this.flag = flag; this.value = flag.readU8();
      flag.writeU8(0);
    },
    onLeave() { if (this.flag) this.flag.writeU8(this.value); },
  });

  Interceptor.attach(address(0x26b690), {                // HXSliderParamView::onEvent
    onEnter(args) {
      if (!modelLockQuery || modelLockOwn || activeModel.isNull() || !this.context.ecx.equals(activeModel)) return;
      const event = args[0].toUInt32();
      if (!MODEL_VALUE_EVENTS.includes(event)) return;
      const view = this.context.ecx, sender = args[1];
      if (![0x13c, 0x140, 0x144].some(offset => view.add(offset).readPointer().equals(sender))) return;
      if (modelLockState() !== 'locked') return;
      args[0] = ptr(0x21707663);   // pva!: what the child sends when set by the program; the view ignores it
      this.revert = sender;
      if (event === 0x21617663) modelLockTrace('refused');
    },
    onLeave() {
      if (!this.revert) return;
      try {
        setValue(this.revert, getValue(activeModel), 0, 0);
        syncSelection();
      } catch (error) { modelLockTrace('revert-skipped', {error: String(error.message)}); }
    },
  });

  function syncSelection() {
    refreshDisplayedModelName();
    if (namList.isNull()) return;
    const row = hasActiveModel() ? slots.indexOf(getValue(activeModel) + 1) : -1;
    if (row < 0) clearSelection(namList, 0, 1);
    else selectRow(namList, 1, row, 0, 1);
    refreshButtons();
  }

  renderProfiles();

  Interceptor.attach(address(0x20c3f0), {
    onEnter() { libraryUiThread = Process.getCurrentThreadId(); this.controller = this.context.ecx; },
    onLeave() { refreshProfiles(this.controller); },
  });

  Interceptor.attach(address(0x214ee0), {
    onEnter() {
      if (!this.context.ecx.add(0x5c).readPointer().equals(irController)) return;
      if (irController.equals(namImportController)) namImportController = NULL;
      irController = NULL;
      libraryRoot = NULL;
    },
  });

  // Instantiate the same native list class and drawing mode as the IR pane.
  Interceptor.attach(address(0x422b10), {
    onEnter(args) {
      if (args[0].toUInt32() !== namListId) return;
      this.nam = true;
      args[0] = ptr(0x696c7374); // ilst
    },
    onLeave(result) {
      if (this.nam && !result.isNull()) result.add(0x54).readPointer().writeU32(namListId);
    },
  });

  Interceptor.attach(address(0x401500), {
    onEnter() {
      if (this.context.ecx.equals(namList)) {
        namList = NULL;
        namPanel = NULL;
        libraryRoot = NULL;
        buttons = {};
      }
    },
  });

  Interceptor.attach(address(0x1971e0), {
    onEnter(args) {
      // This call from the IR renderer formats the number from the row index.
      if (this.returnAddress.equals(address(0x401a9f)) && this.context.edi.equals(namList)) {
        args[2] = ptr(this.context.esi.add(0x100).readU32() + 1);
      }
    },
  });

  Interceptor.attach(address(0x3bd120), {
    onEnter(args) {
      const sender = this.context.ecx;
      const event = args[0].toUInt32();
      this.selection = event === 0x216c6273 && sender.equals(namList); // sbl!
      if (event === 0x2162705f) { // _pb!
        this.allCaptures = !allCapturesButton.isNull() && sender.equals(allCapturesButton);
        this.action = Object.keys(buttons).find(action => sender.equals(buttons[action]));
        if (modelLockQuery && hasActiveModel()) {
          this.lockClick = Object.keys(LOCK_BUTTONS).some(name => {
            const button = lockButton(activeModel, name);
            return !!button && sender.equals(button);
          });
        }
      }
    },
    onLeave() {
      if (this.lockClick) {
        try { modelLockQuery.toggle(); } catch (error) { modelLockTrace('toggle-skipped', {error: String(error.message)}); }
        applyModelLock(true);
        return;
      }
      if (this.allCaptures) {
        requestLibraryVerification();
        const window = libraryWindow();
        if (libraryOpener && !window.isNull()) libraryOpener(window);
        return;
      }
      if (this.action) runAction(this.action);
      if (!this.selection) return;
      refreshButtons();
      const slot = selectedSlot();
      if (slot && hasProfile(irList(), slot)) setModelSlot(slot);
    },
  });

  // The stock constructor binds exactly three controllers. Keep that binding while
  // allowing the same radio widget to select the fourth, independently managed view.
  Interceptor.attach(address(0x363f00), {
    onEnter() {
      if (!this.returnAddress.equals(address(0x214de1))) return;
      const radio = this.context.ecx.add(0x50).readPointer();
      if (radio.isNull()) return;
      if (getRadioButton(radio, 3).isNull()) return;
      const maximum = radio.add(0x54).readPointer().add(0x24);
      if (maximum.readS32() !== 3) return;
      this.maximum = maximum;
      maximum.writeS32(2);
    },
    onLeave() {
      if (this.maximum) this.maximum.writeS32(3);
    },
  });

  Interceptor.attach(address(0x2156a0), {
    onEnter(args) {
      libraryUiThread = Process.getCurrentThreadId();
      this.library = this.context.ecx.add(0x10).readPointer();
      irController = this.context.ecx.add(0x5c).readPointer();
      // The stock constructor replaces XML button IDs with generated IDs.
      // Native IR transactions request the IR tab to display their progress.
      // Keep the NAM tab when that request comes from the transfer dispatcher.
      if (tab === 3 && args[0].toUInt32() === 1 && this.returnAddress.equals(address(0x2153b4))) {
        args[0] = ptr(3);
      }
      this.tab = args[0].toUInt32();
      this.requestVerification = this.tab === 3 && tab !== 3 && libraryInputDepth > 0 &&
        !this.returnAddress.equals(address(0x2153b4));
      tab = this.tab;
    },
    onLeave() {
      if (this.library.isNull() || this.tab >= libraryViews.length) return;
      libraryRoot = this.library;
      allCapturesButton = findWidget(libraryRoot, 0x6e616c6c, 1); // nall
      orderTabs();
      for (const [index, id] of libraryViews.entries()) {
        const panel = findWidget(this.library, id, 1);
        if (!panel.isNull()) setVisible(panel, Number(this.tab === index), 1);
      }
      if (this.tab !== 3) return;
      if (this.requestVerification) requestLibraryVerification();
      if (namList.isNull()) {
        namList = findWidget(this.library, namListId, 1);
        if (namList.isNull()) return;
        namPanel = findWidget(this.library, libraryViews[3], 1);
        buttons = Object.fromEntries(Object.entries(buttonIds)
          .map(([action, id]) => [action, findWidget(namPanel, id, 1)]));
        namList.add(0x1bc).writeS32(1); // single selection
        renderProfiles();
      }
      refreshProfiles(irController);
      if (hasActiveModel()) syncSelection();
      pumpSidebarPresentation();
    },
  });

  const constructing = new Map();
  const empty = Memory.allocUtf8String('No NAM profiles. Import one from NAM.');
  Interceptor.attach(address(0x26e680), {
    onEnter() {
      const slider = this.context.ecx;
      const label = slider.add(0x7c).readPointer();
      const format = slider.add(0x98).readPointer();
      if (label.isNull() || format.isNull()) return;
      const labelText = label.add(0x7c).readPointer();
      const displayType = format.add(0x10).readPointer();
      if (labelText.isNull() || displayType.isNull() || labelText.readCString() !== 'Model'
          || displayType.readCString() !== 'ir_select') return;
      this.model = slider;
      this.previous = constructing.get(this.threadId);
      constructing.set(this.threadId, { slider, menu: slider.add(0x144).readPointer(), count: 0 });
    },
    onLeave() {
      if (!this.model) return;
      if (this.previous) constructing.set(this.threadId, this.previous);
      else constructing.delete(this.threadId);
      activeModel = this.model;
      syncSelection();
      // The row was (re)built for a block: a padlock already flagged visible from the last block
      // is not painted on the new row, so show it again as on first appearance.
      padlockPainted = ''; padlockReshows = [Date.now() + 300, Date.now() + 1500, Date.now() + 4000];
      applyModelLock(true);
    },
  });

  Interceptor.attach(address(0x26aa10), {
    onEnter() {
      if (!this.context.ecx.equals(activeModel)) return;
      activeModel = NULL;
      syncSelection();
    },
  });

  // The parameter is detached on block changes and laid out on attachment.
  for (const [rva, byParent] of [[0x3baae0, true], [0x3b5e00, false]]) {
    Interceptor.attach(address(rva), {
      onEnter(args) {
        this.model = (byParent ? args[0] : this.context.ecx).equals(activeModel);
      },
      onLeave() {
        if (this.model) syncSelection();
      },
    });
  }

  Interceptor.attach(address(0x26b9b0), {
    onEnter() {
      this.model = this.context.ecx.equals(activeModel);
    },
    onLeave() {
      if (this.model) syncSelection();
    },
  });

  // Clear protection: the stock IR Clear and the NAM tab's Clear both queue
  // through the librarian here. Block slots the current preset still uses.
  const kernel32 = Process.getModuleByName('kernel32.dll');
  const waitForObject = new NativeFunction(kernel32.getExportByName('WaitForSingleObject'), 'uint32', ['pointer', 'uint32'], 'stdcall');
  const releaseMutex = new NativeFunction(kernel32.getExportByName('ReleaseMutex'), 'int', ['pointer'], 'stdcall');
  const editorVtable = address(0x963dfc);
  let presetEditor = NULL;
  let presetRevision = 0;
  let presetNotifying = 0;
  let trustedClear = false;

  Interceptor.attach(address(0x218a00), {
    onEnter() {
      const editor = this.context.ecx.add(0x34).readPointer();
      if (!editor.isNull() && editor.readPointer().equals(editorVtable)) presetEditor = editor;
      presetRevision++;
      presetNotifying++;
    },
    onLeave() {
      presetRevision++;
      presetNotifying--;
    },
  });

  function readCurrentPreset() {
    const editor = presetEditor;
    if (editor.isNull() || !editor.readPointer().equals(editorVtable)) return { reason: 'editor-unavailable' };
    if (presetNotifying) return { reason: 'preset-changing' };
    const held = [];
    function lock(object) {
      // Try-lock only (timeout 0), in the editor's own order: edit buffer, then engine.
      if (!object.readPointer().equals(address(0x92dc4c))) throw new Error('unexpected-lock');
      const handle = object.add(4).readPointer().readPointer();
      const status = waitForObject(handle, 0);
      if (status === 0 || status === 0x80) held.push(handle);
      if (status !== 0) throw new Error('preset-busy');
    }
    try {
      lock(editor.add(0x134));
      const engine = editor.add(0x124).readPointer();
      lock(engine.add(0x18));
      const root = engine.add(0x30);
      if (root.add(4).readU32() !== 6) return { reason: 'unsupported-preset' };
      const revision = presetRevision;
      const first = readPresetTree(root), second = readPresetTree(root);
      if (JSON.stringify(first) !== JSON.stringify(second) || revision !== presetRevision) {
        return { reason: 'preset-changing' };
      }
      return presetIrSlots(first, irParameters);
    } catch (error) {
      return { reason: String(error.message) };
    } finally {
      for (const handle of held.reverse()) releaseMutex(handle);
    }
  }

  function selectedClearSlots(vector) {
    if (vector.isNull() || !vector.readPointer().equals(address(0x8bb0dc))) return null;
    const begin = vector.add(16).readU32(), end = vector.add(20).readU32();
    const data = vector.add(8).readPointer();
    if (begin >= end || end - begin > 128 || data.isNull()) return null;
    const slots = [];
    for (let index = begin; index < end; index++) slots.push(data.add(index * 4).readU32() + 1);
    return slots;
  }

  const clearIrSlotsOriginal = new NativeFunction(address(0x3da2b0), 'void', ['pointer', 'pointer'], 'thiscall');
  function submitClear(librarian, vector, selected) {
    // Invalidate at the accepted submission boundary, before native async work
    // can make a receipt stale. Blocked Clear never changes the ledger.
    slotsChanged(selected || []);
    return clearIrSlotsOriginal(librarian, vector);
  }
  Interceptor.replace(address(0x3da2b0), new NativeCallback(function (librarian, vector) {
    if (trustedClear) return submitClear(librarian, vector, selectedClearSlots(vector));
    let text;
    try {
      const selected = selectedClearSlots(vector);
      const preset = readCurrentPreset();
      const used = selected && preset.slots ? selected.filter(slot => preset.slots.has(slot)) : [];
      if (selected && preset.slots && !used.length) return submitClear(librarian, vector, selected);
      text = used.length
        ? `User IR ${used.join(', ')} ${used.length === 1 ? 'is' : 'are'} used by the current preset.\n\n` +
          'Switch to a preset that does not use these slots, then try Clear again.'
        : `Could not check which IR slots the current preset uses (${selected ? preset.reason : 'unknown selection'}).\n\n` +
          'Wait for the preset to finish loading and try again.';
    } catch (error) {
      text = `Could not check the current preset (${error.message}).`;
    }
    messageBox(getActiveWindow(), Memory.allocUtf16String(text + ' Nothing was deleted.'),
      Memory.allocUtf16String('IR slot protection'), 0x30);
  }, 'void', ['pointer', 'pointer'], 'thiscall'));

  const arguments_ = ['pointer', 'int', 'pointer', 'int', 'int', 'int'];
  const append = new NativeFunction(address(0x3ac110), 'pointer', arguments_, 'thiscall');
  Interceptor.replace(address(0x3ac110), new NativeCallback(function (menu, index, label, id, options, disabled) {
    const state = constructing.get(this.threadId);
    if (!state || !menu.equals(state.menu)) return append(menu, index, label, id, options, disabled);
    const parts = label.readCString().match(/^(\d+): (.+)$/);
    if (parts && Object.hasOwn(profiles, parts[1]) && profiles[parts[1]] === parts[2]
        && hasProfile(irList(), Number(parts[1]))) {
      const friendly = profileLabel(Number(parts[1]));
      return append(menu, index, friendly === parts[2] ? label : Memory.allocUtf8String(`${parts[1]}: ${friendly}`), id, options, disabled);
    }
    if (parts && audition && audition.slider.equals(state.slider) && Number(parts[1]) === audition.slot
        && parts[2] === audition.name) return append(menu, index, label, id, options, disabled);
    if (slots.length === 0 && state.count++ === 0) {
      return append(menu, index, empty, id, options, 1);
    }
    return NULL;
  }, 'pointer', arguments_, 'thiscall'));

  return {
    refreshProfiles,
    setSidebarSink(callback) { sidebarSink = typeof callback === 'function' ? callback : null; },
    setSidebarChildReady(value) { sidebarChildReady = value === true; sidebarChildFailed = !sidebarChildReady; },
    queueSidebarAction(callback) {
      if (typeof callback !== 'function' || sidebarActions.length >= 8) return false;
      sidebarActions.push(callback);
      if (!sidebarHost.isNull()) winApi('PostMessageW','int',['pointer','uint','pointer','pointer'])(sidebarHost,0,NULL,NULL);
      return true;
    },
    resizeSidebarFromChild(width) {
      if (!libraryReady() || !Number.isInteger(width) || width < 300 || width > 600) return false;
      const view = sidebar(); if (!view) return false;
      sidebarWidth=width;resizeSidebar(view,width);send({type:'sidebarWidth',width});return true;
    },
    openExpandedLibrary(window) { if (libraryOpener && !window.isNull()) libraryOpener(window); },
    setLibraryOpener(callback) { libraryOpener = typeof callback === 'function' ? callback : null; },
    libraryReady, discoverySnapshot, exportDiscoverySlot, profileLabel,
    flushInventoryPresentation, needsInventoryPresentation,
    slotEpoch: () => slotLedger?.epoch() || 0,
    installedCaptures() {
      // Captures of this PC's library that are on the Floor now, by verified slot: for the NAM
      // category's model list. [{slot, name, gear, icon}], lowest slot first (icon: capture_icons id).
      const result = [];
      if (!slotLedger) return result;
      for (const [slot, contentId] of slotLedger.entries()) {
        const capture = payloadMetadata[contentId];
        if (!capture || slot === AUDITION_SLOT) continue;
        result.push({slot, name: capture.name || profiles[slot] || 'Capture', gear: capture.gear || '', icon: capture.icon || ''});
      }
      return result;
    },
    requestSlotCheck() { requestLibraryVerification(); },
    // The slot check has completed (all slots, or all it could read) since proof was last discarded.
    slotCheckDone: () => inventoryState === 'verified' || inventoryState === 'partial',
    acceptRememberedSlot(slot) {
      // From the slot check's UI-thread tick, right after its snapshot. True: this slot needs
      // no read-back.
      const memory = rememberedSlots.get(slot), name = slotNames.get(slot);
      if (!memory || !slotLedger) return false;
      if (typeof name !== 'string' || !name || memory.name !== name) { forgetRememberedSlot(slot); return false; }
      if (memory.contentId === null) return true;
      if (!slotLedger.accept(slotLedger.epoch(), slot, memory.contentId)) return false;
      inventoryDirty = true;
      return true;
    },
    rememberSlot(slot, status, _signature, contentId) {
      // What a read-back found. No native access: this can run on Frida's message thread, so
      // the name is the one the last snapshot read.
      const name = slotNames.get(slot);
      if (!Number.isInteger(slot) || slot < 1 || slot > 127 || typeof name !== 'string' || !name ||
          name.length > 64) return;
      let entry = null;
      if (status === 'matched' && typeof contentId === 'string' && /^[0-9a-f]{64}$/.test(contentId)) entry = {name, contentId};
      else if (status === 'ir' || status === 'unmatched-nam') entry = {name, contentId: null};
      if (!entry) return;
      const previous = rememberedSlots.get(slot);
      if (previous && previous.name === entry.name && previous.contentId === entry.contentId) return;
      rememberedSlots.set(slot, entry);
      send({type: 'slotMemory', slot, name: entry.name, contentId: entry.contentId});
    },
    invalidateVerifiedSlots(preserveDisplay = true, changedSlots = null) {
      // A slot the device reports as changed, deleted or renamed is read back again.
      if (Array.isArray(changedSlots)) for (const slot of changedSlots) forgetRememberedSlot(slot);
      if (!slotLedger) return;
      if (Array.isArray(changedSlots)) slotLedger.invalidateSlots(changedSlots);
      else slotLedger.invalidate(preserveDisplay);
      inventoryState = preserveDisplay ? 'refreshing' : 'unverified';
      inventoryDirty = true; emitInventory();
    },
    forgetVerifiedSlot(slot) {
      if (slotLedger?.forget(slot, inventoryState === 'refreshing')) inventoryDirty = true;
    },
    recordVerifiedSlot(epoch, slot, contentId) {
      const accepted = slotLedger?.accept(epoch, slot, contentId) === true;
      if (accepted) inventoryDirty = true;
      return accepted;
    },
    setDiscoveryLocked(value) { discoveryLocked = value === true; },
    setExportPending(value) { exportPending = value === true; },
    inventoryBusy: () => exportPending || discoveryLocked,
    setWorkflowBusy(value) {
      const busy = value === true;
      const wasBusy = workflowBusy;
      if (busy && !workflowBusy && discoveryPause) discoveryPause();
      workflowBusy = busy;
      if (!busy && wasBusy && libraryBrowsing) discoveryControls?.resumeRequestedVerification({allowWhileBrowsing:true});
    },
    setDiscoveryPause(callback) { discoveryPause = typeof callback === 'function' ? callback : null; },
    setDiscoveryControls(controls) { discoveryControls = controls; },
    setBrowserSessionActive(value) {
      libraryBrowsing = false;
      discoveryControls?.setSessionActive(value === true);
    },
    setLibraryBrowsing(value) {
      if (libraryBrowsing === value) return;
      libraryBrowsing = value;
      if (value) requestLibraryVerification();
      else discoveryControls?.setSessionActive(true);
    },
    setDiscoveryStatus(value, checked) {
      discoveryStatus = value;
      // For the sidebar's "Checking Floor slots… 7 of 26": how many slots this pass has read,
      // out of the permanent slots that hold something. Presentation only.
      // (The total is counted in sidebarPresentation, on the UI thread: this setter can run on
      // Frida's message thread and must not read native lists.)
      discoveryChecked = Number.isInteger(checked) && checked >= 0 ? checked : 0;
      if (value === 'complete' || value === 'complete-partial') {
        slotLedger?.commitDisplay(); inventoryState = value === 'complete' ? 'verified' : 'partial';
        inventoryDirty = true; emitInventory();
      }
    },
    setPayloadMetadata(value) {
      payloadMetadata = value || {}; inventoryDirty = true; payloadRevision++;
    },
    // Counts metadata updates (a capture's type or name changed): the NAM block icons redraw.
    payloadRevision: () => payloadRevision,
    inventorySnapshot() {
      return { epoch: slotLedger?.epoch() || 0, state: inventoryState,
        entries: slotLedger?.displayEntries() || [], verified: slotLedger?.entries() || [] };
    },
    verifiedCaptureSlot(identity, requestedSlot) {
      if (!slotLedger || typeof identity !== 'string' || !/^[0-9a-f]{64}$/.test(identity)) return null;
      const list = irList();
      if (list.isNull()) return null;
      for (const [slot, contentId] of slotLedger.entries()) {
        if (requestedSlot !== undefined && slot !== requestedSlot) continue;
        if (payloadMetadata[contentId]?.id !== identity || slot === AUDITION_SLOT) continue;
        // Current accepted epoch + the native occupied row, never display history.
        const row = getIrRow(list, slot - 1);
        if (!row.isNull() && row.add(0x10c).readU32()) return slot;
      }
      return null;
    },
    modelSlot: () => hasActiveModel() ? getValue(activeModel) + 1 : null,
    nativeModel: () => hasActiveModel() ? activeModel : NULL,
    playPreview(transfer) {
      const name = this.previewName(transfer);
      if (!name || !hasActiveModel()) return false;
      audition = { slider: activeModel, slot: transfer.slot, name };
      rebuildModelMenu(activeModel);
      return setModelSlot(transfer.slot);
    },
    stopPreview() {
      if (!audition) return;
      audition = null;
      if (hasActiveModel()) rebuildModelMenu(activeModel);
    },
    abandonPreview() { audition = null; }, // bookkeeping only; retain device samples and settings
    freeSlots(excluded) {
      const list = irList();
      const free = [];
      if (list.isNull()) return free;
      for (let slot = 1; slot <= (profile.lastPermanentSlot || AUDITION_SLOT); slot++) {
        if (slot === excluded) continue;
        const row = getIrRow(list, slot - 1);
        if (!row.isNull() && !row.add(0x10c).readU32()) free.push(slot);
      }
      return free;
    },
    importPreview(source, slot, audition = false) {
      if (profile.strictPreview && ((audition && slot !== AUDITION_SLOT) ||
          (!audition && slot === AUDITION_SLOT))) {
        throw new Error('User IR 128 is reserved for previews. Install captures in slots 1–127.');
      }
      const list = irList();
      if (list.isNull()) throw new Error('Connect your Helix to audition a tone.');
      if (!namImportController.isNull()) throw new Error('Wait for the current NAM import to finish.');
      const transfer = { controller: irController, slot, source };
      if (!this.previewSlotEmpty(transfer)) throw new Error(`User IR ${slot} is no longer free.`);
      transfer.file = requestImport({ type: 'stageTone', file: source, audition }).file;
      toneImport = transfer;
      selectStockRow(list, slot - 1);
      namImportController = irController;
      importIr(irController);
      return transfer;
    },
    previewName(transfer) {
      if (!transfer.controller.equals(irController) || !transfer.name) return '';
      const list = irList();
      if (list.isNull()) return '';
      const row = getIrRow(list, transfer.slot - 1);
      if (row.isNull() || !row.add(0x10c).readU32()) return '';
      const name = row.readUtf8String();
      return name === transfer.name || (name.length >= 31 && transfer.name.startsWith(name)) ? name : '';
    },
    registerProfile(transfer) {
      const name = this.previewName(transfer);
      if (!name) return false;
      profiles[transfer.slot] = name;
      slotsChanged([transfer.slot]);
      slotLedger?.imported(transfer.slot);
      renderProfiles();
      if (hasActiveModel()) rebuildModelMenu(activeModel);
      return true;
    },
    setSlot: slot => setModelSlot(slot),
    setModelLockQuery(query) { modelLockQuery = query && typeof query.state === 'function' && typeof query.toggle === 'function' ? query : null; },
    applyModelLock: force => applyModelLock(force),
    selectProfile(transfer) {
      return !!this.previewName(transfer) && setModelSlot(transfer.slot);
    },
    canClearPreview(transfer) {
      return !!transfer && !!this.previewName(transfer) && previewSlotUnused(readCurrentPreset(), transfer.slot);
    },
    previewUse(transfer) {
      // Whether the current preset references this session's preview slot: 'unused' (safe to
      // clear), 'used', or 'unknown' when the slot is not ours by name or the preset cannot be
      // read right now (busy, changing, or a shape presetIrSlots does not support).
      if (!transfer || !this.previewName(transfer)) return 'unknown';
      const preset = readCurrentPreset();
      if (!(preset?.slots instanceof Set)) return 'unknown';
      return preset.slots.has(transfer.slot) ? 'used' : 'unused';
    },
    releaseLeftoverPreview(name) {
      // A preview left in User IR 128 by a session that ended without cleanup, known only by
      // its slot name. Cleared once the current preset no longer references the slot.
      // 'gone': 128 no longer holds it; 'used' / 'unknown': not yet; 'cleared': clear submitted.
      if (typeof name !== 'string' || !name || this.slotName(AUDITION_SLOT) !== name) return 'gone';
      const preset = readCurrentPreset();
      if (!(preset?.slots instanceof Set)) return 'unknown';
      if (preset.slots.has(AUDITION_SLOT)) return 'used';
      return this.clearSlot(AUDITION_SLOT) ? 'cleared' : 'unknown';
    },
    clearPreview(transfer) {
      return this.canClearPreview(transfer) && this.clearSlot(transfer.slot);
    },
    slotName(slot) {
      const list = irList();
      const row = list.isNull() || !slot ? NULL : getIrRow(list, slot - 1);
      return row.isNull() || !row.add(0x10c).readU32() ? '' : row.readUtf8String();
    },
    slotEmpty(slot) {
      return this.previewSlotEmpty({ controller: irController, slot });
    },
    auditionSlot() {
      if (profile.strictPreview) return this.slotEmpty(AUDITION_SLOT) ? AUDITION_SLOT : 0;
      // 128 when free (imports leave it), else the highest free slot; 0 when every slot is taken
      for (let slot = AUDITION_SLOT; slot >= 1; slot--) if (this.slotEmpty(slot)) return slot;
      return 0;
    },
    usedSlots() {
      const list = irList();
      const used = [];
      if (list.isNull()) return used;
      for (let slot = 1; slot <= AUDITION_SLOT; slot++) {
        const row = getIrRow(list, slot - 1);
        if (!row.isNull() && row.add(0x10c).readU32()) used.push([slot, row.readUtf8String()]);
      }
      return used;
    },
    clearSlot(slot) {
      // Our own audition (after Undo restored the block), or a slot the user chose to replace with one.
      const librarian = irController.isNull() ? NULL : irController.add(0x270).readPointer();
      if (librarian.isNull()) return false;
      // Same single-slot array passed by the stock Clear confirmation handler.
      const index = Memory.alloc(4);
      index.writeU32(slot - 1);
      const slots = Memory.alloc(24);
      slots.writePointer(address(0x8bb0dc));
      slots.add(4).writeU32(1);
      slots.add(8).writePointer(index);
      slots.add(12).writeU32(1);
      slots.add(16).writeU32(0);
      slots.add(20).writeU32(1);
      trustedClear = true; // (no "used by the current preset" check: the user chose this slot)
      try {
        clearIrSlots(librarian, slots);
      } finally {
        trustedClear = false;
      }
      return true;
    },
    previewSlotEmpty(transfer) {
      if (!transfer.controller.equals(irController)) return false;
      const list = irList();
      if (list.isNull()) return false;
      const row = getIrRow(list, transfer.slot - 1);
      return !row.isNull() && !row.add(0x10c).readU32();
    },
    trackImport(first, count, imported) {
      slotsChanged(Array.from({length: count}, (_, index) => first + index));
      for (let slot = first; slot < first + count; slot++) pending.delete(slot);
      for (const [slot, name] of Object.entries(imported)) pending.set(Number(slot), name);
    },
  };
}
