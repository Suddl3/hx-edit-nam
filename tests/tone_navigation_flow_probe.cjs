'use strict';

// Drive the real installer, native selection hooks, command receiver and UI
// dispatch callbacks. Native memory and edit-buffer calls are in-memory only.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync(process.argv[2], 'utf8');

function fixture(options = {}) {
  const memory = new Map(), strings = new Map(), hooks = new Map(), replaced = new Map(), receivers = new Map();
  const messages = [], posts = [], timers = [], writes = [], imports = [], captures = [], foreground = [];
  const sessions = [], library = [], clears = [], slotClears = [];
  let delayedRestore = false, restorePending = null, visibleSlot = null;
  let next = 0x10000000, now = 0, selected = null, preview = null, importReady = true, clearAllowed = true, inventoryBusy = false;
  let presetUse = null; // null: derived from the blocks; otherwise the forced previewUse() answer
  class Pointer {
    constructor(value) { this.value = Number(value); }
    add(n) { return new Pointer(this.value + n); }
    isNull() { return this.value === 0; }
    equals(other) { return this.value === other.value; }
    toString() { return String(this.value); }
    toUInt32() { return this.value >>> 0; }
    readU32() { return Number(memory.get(this.value) || 0) >>> 0; }
    readS32() { return this.readU32() | 0; }
    writeU32(value) { memory.set(this.value, value); return this; }
    readPointer() { return new Pointer(this.readU32()); }
    writePointer(value) { return this.writeU32(value.value); }
    readUtf8String() { return strings.get(this.value) || ''; }
  }
  const ptr = n => new Pointer(n), nil = ptr(0), base = ptr(0x400000), window = ptr(0x1000), wake = 0xc101;
  const alloc = size => { const p = ptr(next); next += size + 32; return p; };
  const text = value => { const p = alloc(value.length * 2 + 2); strings.set(p.value, value); return p; };
  const key = p => p instanceof Pointer ? p.value : p;
  const editor = alloc(0x300), vtable = alloc(0x100), panel = alloc(0x100), geometry = alloc(0x30);
  editor.writePointer(vtable); vtable.add(0xdc).writePointer(base.add(0x51aaf0));
  panel.add(0x54).writePointer(geometry);
  geometry.add(0xc).writeU32(500); geometry.add(0x10).writeU32(600);
  let panelVisible = true, panelX = 80, panelY = 100;
  const states = new Map();
  function makeBlock(name, slot, dsp = 0) {
    const controller = alloc(0x120), model = alloc(16), rep = alloc(0x20);
    controller.add(0x34).writePointer(editor); controller.add(0x80).writeU32(dsp);
    controller.add(0x84 + 4).writePointer(rep); rep.add(0x10).writePointer(text(name));
    controller.add(0x98).writeU32(states.size);
    const state = {controller, model, name, slot, nam: true, dsp}; states.set(controller.value, state); return state;
  }
  const a = makeBlock('block0', 9), b = makeBlock('block1', 10), c = makeBlock('block2', 11, 1);
  const context = vm.createContext({
    base, NULL: nil, user32: {getExportByName: name => name},
    Memory: {alloc, allocUtf8String: text, allocUtf16String: text},
    Date: {now: () => now},
    NativeCallback: function(callback) { return callback; },
    NativeFunction: function(address) {
      const at = key(address);
      if (at === 'RegisterWindowMessageW') return () => wake;
      if (at === 'PostMessageW') return (hwnd, event) => { posts.push({hwnd, event}); return 1; };
      if (at === 'AllowSetForegroundWindow') return pid => { foreground.push(pid); return 1; };
      if (at === 'IsChild') return (_parent, child) => Number(child.value === 0x1001);
      if (at === base.value + 0x217120) return () => {};
      if (at === base.value + 0x3ba100) return () => Number(panelVisible);
      if (at === base.value + 0x3b7e00) return (_panel, out) => { out.writeU32(panelX); out.add(4).writeU32(panelY); return out; };
      return () => assert.fail('unexpected native call: ' + at);
    },
    Interceptor: {
      attach(address, listener) { const list = hooks.get(key(address)) || []; list.push(listener); hooks.set(key(address), list); },
      replace(address, callback) { replaced.set(key(address), callback); },
    },
    send: payload => messages.push(payload),
    recv: (type, callback) => receivers.set(type, callback),
    setTimeout: callback => { timers.push(callback); return timers.length; },
    getIrRow: () => nil, getActiveWindow: () => window,
    messageBox: () => assert.fail('unexpected native modal dialog'),
    toneImport: null, namImportController: nil, AUDITION_SLOT: 128,
  });
  const binding = {
    capture(controller, model, options) {
      const state = states.get(controller.value);
      assert.equal(state, selected, 'capture must use the latest visible block');
      assert.equal(model.value, state.model.value);
      if ((state.visible ?? state.slot) !== state.slot) throw new Error('selected slider and addressed block disagree');
      captures.push(state.name);
      return {valid: true, controller, state, dsp: state.dsp, blockName: state.name,
        originalSlot: state.slot, context: options.context};
    },
    release(value) { value.valid = false; },
    read(value, options) { assert.ok(value.valid); assert.equal(value.context, options.context); return {slot: value.state.slot}; },
    restore(value, slot, options) {
      assert.equal(value.state.slot, options.expectedSlot);
      writes.push({block: value.state.name, slot, kind: 'restore'});
      // As observed on the Floor (Candidate58 owner trial): a typed edit-buffer write does not
      // refresh the Model control of a parameter view that is on screen.
      if (value.state === selected) value.state.visible = value.state.slot;
      if (delayedRestore) restorePending = () => { value.state.slot = slot; };
      else value.state.slot = slot;
      // Native notifications about the OLD block are not a user selection.
      replaced.get(base.value + 0x217120)(value.controller, 0, nil, nil, nil);
      return true;
    },
  };
  context.namView = {
    modelSlot: () => selected?.nam ? visibleSlot ?? selected.visible ?? selected.slot : null,
    nativeModel: () => selected?.model || nil,
    slotName: slot => slot === 128 ? (preview ? preview.name || 'foreign-ir' : '') : 'raw-profile-' + slot,
    libraryReady: () => true,
    clearSlot(slot) { slotClears.push(slot); if (slot === 128) preview = null; return true; },
    releaseLeftoverPreview(name) {
      if (!preview || preview.name !== name) return 'gone';
      if ([...states.values()].some(block => block.slot === 128)) return 'used';
      slotClears.push(128); preview = null; return 'cleared';
    },
    verifiedCaptureSlot: () => 12,
    profileLabel: slot => 'Friendly capture ' + slot,
    slotEmpty: () => preview === null,
    inventoryBusy: () => inventoryBusy,
    setWorkflowBusy() {},
    setBrowserSessionActive: value => sessions.push(value),
    setLibraryBrowsing: value => library.push(value),
    importPreview(sourceFile, slot) {
      const transfer = {source: sourceFile, slot, controller: ptr(0x777), imported: importReady, name: 'owned-preview'};
      imports.push(transfer); context.toneImport = transfer;
      context.namImportController = transfer.controller;
      if (importReady) preview = transfer;
      return transfer;
    },
    previewName: transfer => preview === transfer && transfer.imported ? 'owned-preview' : '',
    previewSlotEmpty: () => preview === null,
    canClearPreview: () => clearAllowed,
    previewUse(transfer) {
      if (preview !== transfer || !transfer.imported) return 'unknown';
      if (presetUse) return presetUse;
      return [...states.values()].some(block => block.slot === transfer.slot) ? 'used' : 'unused';
    },
    setSlot(slot) {
      // The Model control itself: the control and the edit buffer change together.
      const state = selected;
      writes.push({block: state.name, slot, kind: 'set'});
      const apply = () => { state.slot = slot; state.visible = undefined; };
      editor.add(0x294).writeU32(editor.add(0x294).readU32() + 1);   // an edit the Undo list records
      if (delayedRestore) restorePending = apply; else apply();
      return true;
    },
    clearPreview(transfer) {
      if (!clearAllowed || preview !== transfer) return false;
      assert.ok([...states.values()].every(block => block.slot !== 128), 'must restore native Model before clearing');
      clears.push(transfer); preview = null; return true;
    },
    stopPreview() {}, abandonPreview() {},
    freeSlots: () => [12],
    playPreview(transfer) {
      if (preview !== transfer) return false;
      writes.push({block: selected.name, slot: transfer.slot, kind: 'apply'});
      selected.slot = transfer.slot; selected.visible = undefined;
      editor.add(0x294).writeU32(editor.add(0x294).readU32() + 1); return true;
    },
  };
  vm.runInContext(source, context, {timeout: 2000, filename: 'tone_view.js'});
  context.binding = binding;
  context.toneProfile = {navigation: true, strictPreview: true, leftoverPreview: options.leftover || null};
  const view = vm.runInContext("installToneView([{label:'TONE3000',block:'NAM A2 Lite',modes:['original']}], toneProfile, binding)", context);
  function select(state) {
    selected = state; state.visible = undefined;   // a newly shown parameter view reads the edit buffer
    replaced.get(base.value + 0x217120)(state.controller, 0, nil, nil, nil);
  }
  function dispatch(event, hwnd = window, wparam = 0) {
    const message = alloc(16); message.writePointer(hwnd); message.add(4).writeU32(event); message.add(8).writeU32(wparam);
    const listeners = hooks.get('DispatchMessageW') || [], frames = listeners.map(() => ({}));
    listeners.forEach((listener, i) => listener.onEnter?.call(frames[i], [message]));
    listeners.forEach((listener, i) => listener.onLeave?.call(frames[i]));
  }
  function pump() {
    for (let i = 0; posts.length; i++) {
      assert.ok(i < 100, 'UI message replay must remain bounded');
      dispatch(posts.shift().event);
    }
  }
  function tick(ms = 100) { now += ms; const ready = timers.splice(0); ready.forEach(callback => callback()); pump(); }
  function deliver(type, payload) { assert.ok(receivers.has(type)); receivers.get(type)({payload}); }
  const target = () => messages.filter(value => value.type === 'toneTarget').at(-1);
  function editBufferEvent(name, state = selected) {
    // The native string argument of the controller's edit-buffer notification handler.
    const value = alloc(16), rep = alloc(0x20);
    value.add(4).writePointer(rep); rep.add(0x10).writePointer(text(name));
    const listeners = hooks.get(base.value + 0x218a00) || [];
    const frames = listeners.map(() => ({context: {ecx: state.controller}}));
    listeners.forEach((listener, i) => listener.onEnter?.call(frames[i], [value]));
    listeners.forEach((listener, i) => listener.onLeave?.call(frames[i]));
  }
  let requestId = 0;
  function command(action, extras = {}, drain = true) {
    const id = ++requestId;
    deliver('toneCommand', {session: 1, id, action, targetVersion: target()?.version, ...extras});
    if (drain) pump();
    return id;
  }
  return {a,b,c,select,view,window,panel,messages,sessions,library,foreground,writes,imports,captures,clears,
    command,target,pump,tick,dispatch,deliver,
    open: (options = {}) => view.openBrowser(window, {dockPanel: panel, ...options}),
    setDelayedRestore: value => { delayedRestore = value; },
    finishRestore: () => { assert.ok(restorePending); restorePending(); restorePending = null; tick(); },
    setVisibleSlot: value => { visibleSlot = value; },
    destroyController: state => {
      for (const hook of hooks.get(base.value + 0x216e00) || []) hook.onEnter.call({context:{ecx:state.controller}}, []);
    },
    escape: (hwnd = window) => dispatch(0x100, hwnd, 0x1b),
    ptr,
    setImportReady: value => { importReady = value; },
    finishUpload: () => { const transfer = imports.at(-1); transfer.imported = true; preview = transfer; tick(); },
    setClearAllowed: value => { clearAllowed = value; },
    retainForeignPreview: (name) => { preview = {name}; },
    slotClears,
    previewStates: () => messages.filter(value => value.type === 'tonePreviewState').map(value => value.state),
    setInventoryBusy: value => { inventoryBusy = value; },
    resize: (x,y,w,h,visible=true) => { panelX=x;panelY=y;geometry.add(0xc).writeU32(w);geometry.add(0x10).writeU32(h);panelVisible=visible; },
    reply: id => messages.filter(value => value.type === 'toneReply' && value.id === id).at(-1),
    editBufferEvent,
    setPresetUse: value => { presetUse = value; },
    traces: reason => messages.filter(value => value.type === 'toneTrace' && value.reason === reason),
  };
}

// Opening the selected NAM block is enough; no bind command is sent.
{
  const f = fixture(); f.select(f.a); assert.equal(f.open(), true);
  assert.equal(f.target().bound, true); assert.match(f.target().label, /Friendly capture 9/);
  assert.deepEqual(f.sessions, [true]); assert.equal(f.writes.length, 0);
  const start = f.messages.find(value => value.type === 'toneStart');
  assert.deepEqual({...start.bounds}, {visible:true,x:80,y:100,w:500,h:600});
  f.resize(120,140,700,800); f.dispatch(5);
  assert.deepEqual({...f.messages.filter(value => value.type === 'toneBounds').at(-1).bounds},
    {visible:true,x:120,y:140,w:700,h:800});
  f.deliver('toneBrowserProcess', {session:0,pid:999}); f.open(); assert.deepEqual(f.foreground, []);
  f.deliver('toneBrowserProcess', {session:1,pid:444}); f.open(); assert.deepEqual(f.foreground, [444]);
  assert.equal(f.messages.filter(value => value.type === 'toneFocus').at(-1).session, 1);
  f.deliver('toneLibraryVisibility', {session:0,visible:true});
  f.deliver('toneLibraryVisibility', {session:1,visible:true});
  f.deliver('toneLibraryVisibility', {session:1,visible:false});
  assert.deepEqual(f.library, [true,false]);
  f.command('close'); assert.deepEqual(f.sessions, [true,false]);
  f.deliver('toneLibraryVisibility', {session:1,visible:true});
  assert.deepEqual(f.library, [true,false]);
}

// Restore only the old bound block, then bind whichever NAM block is selected
// at cleanup completion. A newer selection supersedes the first new choice.
{
  const f = fixture(); f.select(f.a); f.open();
  const id = f.command('audition', {file:'fixture.wav'}); assert.equal(f.reply(id).slot, 128);
  f.select(f.b); f.select(f.c); assert.equal(f.target().bound, false);
  f.pump(); assert.equal(f.target().bound, true); assert.match(f.target().label, /DSP 2 · block2/);
  assert.equal(f.a.slot, 9); assert.equal(f.b.slot, 10); assert.equal(f.c.slot, 11);
  assert.deepEqual(f.writes, [{block:'block0',slot:128,kind:'apply'},{block:'block0',slot:9,kind:'restore'}]);
  assert.equal(f.captures.at(-1), 'block2');
}

// A command queued for the old target is rejected before it can upload. Only
// after its reply can the new visible block be bound for a fresh user action.
{
  const f = fixture(); f.select(f.a); f.open();
  const id = f.command('audition', {file:'stale.wav'}, false);
  f.select(f.b); assert.equal(f.target().bound, false); f.pump();
  assert.match(f.reply(id).error, /target changed/i); assert.equal(f.imports.length, 0);
  assert.equal(f.writes.length, 0); assert.equal(f.target().bound, true);
  assert.match(f.target().label, /block1/);
}

// Selection can also move while a native upload is unfinished. Its late
// completion is cleaned up without applying it to any newly selected block.
{
  const f = fixture(); f.select(f.a); f.open(); f.setImportReady(false);
  const id = f.command('audition', {file:'late.wav'}); assert.equal(f.reply(id), undefined);
  f.select(f.b); f.select(f.c); assert.equal(f.target().bound, false);
  f.finishUpload(); assert.match(f.reply(id).error, /target changed/i);
  assert.equal(f.target().bound, true); assert.match(f.target().label, /block2/);
  assert.deepEqual(f.writes, []); assert.equal(f.a.slot, 9); assert.equal(f.c.slot, 11);
}

// An owned but uncleared128 remains quarantined; selecting yet another block
// cannot clear the failure or silently authorize a new audition.
{
  const f = fixture(); f.select(f.a); f.open(); f.command('audition', {file:'fixture.wav'});
  f.setClearAllowed(false); f.select(f.b); f.pump();
  assert.equal(f.target().quarantined, true); assert.equal(f.target().bound, false);
  f.select(f.c); f.pump(); assert.equal(f.target().bound, false);
  const count = f.imports.length; const id = f.command('audition', {file:'forbidden.wav'});
  assert.ok(f.reply(id).error); assert.equal(f.imports.length, count);
  assert.equal(f.b.slot, 10); assert.equal(f.c.slot, 11);
}

// Error abandonment closes the browser session lock as well as normal close.
{
  const f = fixture(); f.select(f.a); f.open(); f.setInventoryBusy(true);
  const id = f.command('close'); f.tick(13000);
  assert.match(f.reply(id).notice, /unconfirmed preview was retained/i);
  assert.deepEqual(f.sessions, [true,false]);
}

// User IR 128 is the preview slot and nothing else (owner decision after the
// Candidate61 session): whatever an earlier preview or session left in it is
// replaced by the next preview. The block it previews on is restored as usual.
{
  const f = fixture(); f.select(f.a); f.retainForeignPreview(); f.open();
  assert.equal(f.target().bound, true); assert.equal(f.target().quarantined, false);
  const id = f.command('audition', {file:'replaces.wav'}); f.tick();
  assert.equal(f.reply(id).slot, 128); assert.deepEqual(f.slotClears, [128]);
  assert.equal(f.imports.length, 1); assert.equal(f.a.slot, 128);
  const end = f.command('end'); assert.equal(f.reply(end).ended, true); assert.equal(f.a.slot, 9);
}

// While a preview is on a block the supervisor is told which block, what it
// played before and the preview's slot name; the record is withdrawn when the
// block is back on its capture.
{
  const f = fixture(); f.select(f.a); f.open(); f.command('audition', {file:'first.wav'});
  assert.deepEqual({...f.previewStates().at(-1)}, {dsp:0, block:'block0', originalSlot:9, name:'owned-preview'});
  f.command('end'); assert.equal(f.previewStates().at(-1), null);
}

// HX Edit was closed with a preview on a block (Candidate61 session). On the
// next launch that block still reads 128. With the record, its earlier capture
// is put back as soon as its parameter view is on screen, with no browser
// session, and the slot is released. Other blocks are never written.
{
  const f = fixture({leftover: {dsp:0, block:'block0', originalSlot:9, name:'left-behind 1a2b3c'}});
  f.retainForeignPreview('left-behind 1a2b3c'); f.a.slot = 128;
  f.select(f.b); f.tick(300); f.dispatch(0xf);
  assert.equal(f.a.slot, 128); assert.equal(f.b.slot, 10); assert.equal(f.writes.length, 0);
  f.select(f.a); f.tick(300); f.dispatch(0xf);
  assert.deepEqual(f.writes, [{block:'block0', slot:9, kind:'set'}]); assert.equal(f.a.slot, 9);
  f.tick(300); f.dispatch(0xf);
  assert.deepEqual(f.slotClears, [128]); assert.equal(f.previewStates().at(-1), null);
  assert.ok(f.traces('leftover-preview').some(m => m.stage === 'recovered'));
  // Afterwards the block previews normally.
  f.open(); assert.equal(f.target().bound, true);
  const id = f.command('audition', {file:'after.wav'}); assert.equal(f.reply(id).slot, 128);
}

// The recorded block was moved to another capture before HX Edit closed (Floor, November 13):
// it keeps that capture, nothing is written to it, and the preview slot is released.
{
  const f = fixture({leftover: {dsp:0, block:'block0', originalSlot:9, name:'left-behind 4d5e6f'}});
  f.retainForeignPreview('left-behind 4d5e6f'); f.a.slot = 11;
  f.select(f.a); f.tick(300); f.dispatch(0xf); f.tick(300); f.dispatch(0xf);
  assert.equal(f.writes.length, 0); assert.equal(f.a.slot, 11);
  assert.deepEqual(f.slotClears, [128]); assert.equal(f.previewStates().at(-1), null);
  assert.ok(f.traces('leftover-preview').some(m => m.stage === 'block-moved-on'));
}

// During a preview the block itself leaves the preview slot (an Undo, or Model moved on an
// unlocked block). The preview ends: the block keeps what it plays now and 128 is released,
// and the next preview is not blocked.
{
  const f = fixture(); f.select(f.a); f.open();
  const id = f.command('audition', {file:'first.wav'}); assert.equal(f.reply(id).slot, 128);
  f.a.slot = 11;
  for (let i = 0; i < 4; i++) { f.tick(200); f.dispatch(0xf); }
  f.tick(); f.dispatch(0xf); f.tick(300); f.dispatch(0xf);
  assert.ok(f.traces('preview-left').length >= 1);
  assert.equal(f.a.slot, 11); assert.equal(f.clears.length, 1);
  assert.equal(f.target().quarantined, false);
  const next = f.command('audition', {file:'second.wav'}); assert.equal(f.reply(next).slot, 128);
}

// A record whose preview is no longer in 128 changes nothing and is dropped.
{
  const f = fixture({leftover: {dsp:0, block:'block0', originalSlot:9, name:'left-behind 1a2b3c'}});
  f.retainForeignPreview('something-else'); f.a.slot = 128;
  f.select(f.a); f.tick(300); f.dispatch(0xf);
  assert.equal(f.a.slot, 128); assert.equal(f.writes.length, 0); assert.deepEqual(f.slotClears, []);
  assert.equal(f.previewStates().at(-1), null);
}

// No record (another PC, or an older build): the block left on 128 cannot be
// previewed on, because there is nothing to return to, but Use on Floor gives
// it a permanent slot and it previews normally afterwards.
{
  const f = fixture(); f.retainForeignPreview('unknown-preview'); f.a.slot = 128;
  f.select(f.a); f.open();
  assert.equal(f.target().bound, true); assert.match(f.target().text, /still on the preview slot/);
  const refused = f.command('audition', {file:'no.wav'});
  assert.match(f.reply(refused).error, /still on the preview slot/); assert.equal(f.imports.length, 0);
  const use = f.command('use', {identity:'a'.repeat(64)}); f.tick();
  assert.equal(f.reply(use).usedSlot, 12); assert.equal(f.a.slot, 12);
  const id = f.command('audition', {file:'now.wav'}); f.tick();
  assert.equal(f.reply(id).slot, 128); assert.deepEqual(f.slotClears, [128]);
}

// Missing/hidden docking geometry cannot start a helper with synthetic bounds.
{
  const f = fixture(); f.select(f.a); f.resize(0,0,500,600,false);
  assert.equal(f.open(), false); assert.deepEqual(f.sessions, []);
  assert.equal(f.messages.some(value => value.type === 'toneStart'), false);
}
// A native setter returning successfully is not restoration completion. Keep
// the owned128 and refuse replacement until the addressed Model confirms.
{
  const f = fixture(); f.select(f.a); f.open();
  f.a.input = 0.6; f.a.bass = 0.45; f.a.treble = 0.55;
  f.command('audition', {file:'first.wav'}); f.setDelayedRestore(true);
  const id = f.command('audition', {file:'second.wav'});
  assert.equal(f.reply(id), undefined); assert.equal(f.clears.length, 0);
  assert.equal(f.imports.length, 1); assert.equal(f.a.slot, 128);
  f.finishRestore(); assert.equal(f.reply(id).slot, 128); assert.equal(f.imports.length, 2);
  assert.equal(f.clears.length, 1);
  assert.deepEqual([f.a.input,f.a.bass,f.a.treble], [0.6,0.45,0.55]);
  f.setDelayedRestore(false); f.command('close', {reason:'escape'});
  assert.equal(f.a.slot, 9); assert.equal(f.clears.length, 2);
  assert.deepEqual([f.a.input,f.a.bass,f.a.treble], [0.6,0.45,0.55]);
  const phases = f.messages.filter(m => m.reason === 'preview-cleanup');
  assert.ok(phases.some(m => m.source === 'escape' && m.stage === 'completed' && m.originalSlot === 9 && m.observedSlot === 9));
}

// The addressed block and visible Model must agree while that block remains
// selected. Do not accept a local API value while its parameter UI is stale.
{
  const f = fixture(); f.select(f.a); f.open(); f.command('audition',{file:'first.wav'});
  f.setVisibleSlot(128); const id = f.command('close', {reason:'close-button'});
  assert.equal(f.a.slot,9); assert.equal(f.reply(id),undefined); assert.equal(f.clears.length,0);
  f.setVisibleSlot(null); f.tick(); assert.equal(f.clears.length,1);
  assert.equal(f.reply(id).retained,undefined);
}

// A timed-out restore never clears the preview or claims success; no second
// native write is sent during the wait.
{
  const f = fixture(); f.select(f.a); f.open(); f.command('audition',{file:'first.wav'});
  f.setDelayedRestore(true); const id=f.command('close',{reason:'window-close'}); f.tick(3600);
  assert.equal(f.reply(id).retained,true); assert.equal(f.a.slot,128); assert.equal(f.clears.length,0);
  assert.deepEqual(f.writes.filter(w => w.kind !== 'apply'), [{block:'block0',slot:9,kind:'set'}]);
  assert.ok(f.messages.some(m => m.reason === 'preview-cleanup' && m.rejection === 'restore-not-confirmed'));
}

// Candidate58 owner trial, 00:55: a second preview on the block that is still
// selected. A typed write restored the edit buffer but left the Model control
// on 128, and cleanup gave up. The control itself must do the restore.
{
  const f = fixture(); f.select(f.a); f.open();
  f.command('audition',{file:'first.wav'});
  const second = f.command('audition',{file:'second.wav'});
  assert.equal(f.reply(second).slot,128); assert.equal(f.reply(second).error,undefined);
  assert.deepEqual(f.writes, [{block:'block0',slot:128,kind:'apply'},{block:'block0',slot:9,kind:'set'},
    {block:'block0',slot:128,kind:'apply'}]);
  const third = f.command('audition',{file:'third.wav'}); assert.equal(f.reply(third).slot,128);
  const end = f.command('end'); assert.equal(f.reply(end).ended,true);
  assert.equal(f.a.slot,9); assert.equal(f.clears.length,3);
  assert.equal(f.writes.some(w => w.kind === 'restore'),false,'no typed write to a block whose control is on screen');
  assert.equal(f.target().bound,true); assert.equal(f.target().quarantined,false);
}

// A block restored by its typed binding while another block was selected is
// consistent again when its parameter view returns, and can be previewed again.
{
  const f = fixture(); f.select(f.a); f.open(); f.command('audition',{file:'first.wav'});
  f.select(f.b); f.pump();
  assert.deepEqual(f.writes.at(-1), {block:'block0',slot:9,kind:'restore'});
  f.select(f.a); f.pump(); assert.equal(f.target().bound,true); assert.match(f.target().label,/block0/);
  const id = f.command('audition',{file:'again.wav'}); assert.equal(f.reply(id).slot,128);
}

// Rebuilding the parameter-view controller during a selection change does not
// destroy the original edit-buffer binding or retarget its cleanup.
{
  const f=fixture(); f.select(f.a); f.open(); f.command('audition',{file:'first.wav'});
  f.destroyController(f.a); f.select(f.b); f.pump();
  assert.equal(f.a.slot,9); assert.equal(f.b.slot,10); assert.equal(f.clears.length,1);
  assert.equal(f.target().bound,true); assert.match(f.target().label,/block1/);
}

// Native-focused Escape is scoped to HX Edit/its children. It queues a close
// through the same worker as browser Escape, once, without any edit on keydown.
{
  const f=fixture(); f.select(f.a); f.open(); f.command('audition',{file:'first.wav'});
  const before=f.writes.length; f.escape(f.ptr(0x9999));
  assert.equal(f.messages.some(m=>m.type==='toneDismiss'),false);
  f.escape(f.ptr(0x1001)); f.escape();
  const dismiss=f.messages.filter(m=>m.type==='toneDismiss'); assert.equal(dismiss.length,1);
  assert.equal(dismiss[0].reason,'escape-native'); assert.equal(f.writes.length,before);
  f.command('close',{reason:dismiss[0].reason}); assert.equal(f.a.slot,9); assert.equal(f.clears.length,1);
}

// Catalog/library and native layout events retain the same preview. Equal
// geometry is resent on native interaction so the dock can restore z-order;
// ordinary paint messages do not create a foreground/layout loop.
{
  const f=fixture(); f.select(f.a); f.open(); f.command('audition',{file:'first.wav'});
  const before=f.writes.length;
  f.deliver('toneLibraryVisibility',{session:1,visible:true});
  f.deliver('toneLibraryVisibility',{session:1,visible:false});
  const boundsBefore=f.messages.filter(m=>m.type==='toneBounds').length;
  f.dispatch(0x202); f.dispatch(0x47);
  assert.equal(f.messages.filter(m=>m.type==='toneBounds').length,boundsBefore+2);
  f.dispatch(0xf); f.dispatch(0xf);
  assert.equal(f.messages.filter(m=>m.type==='toneBounds').length,boundsBefore+2);
  assert.equal(f.writes.length,before); assert.equal(f.a.slot,128); assert.equal(f.clears.length,0);
}

// Sidebar actions reuse a live browser without changing page/focus. A fresh
// headless action needs no docking geometry or helper window.
{
  const f=fixture(); f.select(f.a); f.open();
  const focus=f.messages.filter(m=>m.type==='toneFocus').length;
  assert.equal(f.open({headless:true,libraryAction:'use:identity'}),true);
  assert.equal(f.messages.filter(m=>m.type==='toneFocus').length,focus);
  assert.equal(f.messages.filter(m=>m.type==='toneAction').at(-1).action,'use:identity');
  const h=fixture(); h.select(h.a); h.resize(0,0,0,0,false);
  assert.equal(h.open({headless:true,libraryAction:'use:identity'}),true);
  assert.equal(h.messages.find(m=>m.type==='toneStart').headless,true);
  assert.equal(h.target().bound,true); assert.equal(h.open({headless:true,libraryAction:'use:other'}),false);
}

// A snapshot recall keeps the preset's blocks. The preview is ended on its
// original block after the recall settles, not stranded on User IR 128.
{
  const f=fixture(); f.select(f.a); f.open(); f.command('audition',{file:'first.wav'});
  f.editBufferEvent('EditBuffer_HelixSnapshotWillChange'); f.pump();
  assert.equal(f.a.slot,128,'no edit while the snapshot recall settles'); assert.equal(f.clears.length,0);
  f.tick(200); assert.equal(f.a.slot,128);
  f.tick(300); f.tick();
  assert.equal(f.a.slot,9); assert.equal(f.clears.length,1);
  // Its parameter view stayed on screen through the recall, so its own control restores it.
  assert.deepEqual(f.writes.at(-1), {block:'block0',slot:9,kind:'set'});
  assert.equal(f.target().quarantined,false); assert.equal(f.target().bound,true);
  const id=f.command('audition',{file:'second.wav'}); assert.equal(f.reply(id).slot,128);
}

// Leaving the preset discards the unsaved preview edit with it. Once the new
// preset no longer references 128, the owned slot is released automatically
// and the newly selected block is ready; no block of the new preset is edited.
{
  const f=fixture(); f.select(f.a); f.open(); f.command('audition',{file:'first.wav'});
  f.editBufferEvent('EditBuffer_PresetContentsWillChange'); f.pump();
  assert.equal(f.target().quarantined,true); assert.equal(f.clears.length,0);
  f.tick(1000); assert.equal(f.clears.length,0,'the old preset still references the preview');
  f.a.slot=9;                       // the device has loaded the other preset
  f.tick(600); f.tick(); f.tick();
  assert.equal(f.clears.length,1);
  assert.equal(f.writes.filter(w=>w.kind!=='apply').length,0,'nothing in the new preset is written');
  assert.equal(f.target().quarantined,false); assert.equal(f.target().bound,true);
  assert.ok(f.traces('preview-cleanup').some(m=>m.stage==='completed-detached'));
}

// A signal-flow change in the same preset leaves the block on 128, so the
// automatic pass keeps the slot. An explicit End with that block selected
// puts the original capture back on it, then releases the slot.
{
  const f=fixture(); f.select(f.a); f.open(); f.command('audition',{file:'first.wav'});
  f.editBufferEvent('EditBuffer_HelixBlockAdded'); f.pump();
  f.tick(1600); f.tick(4100); f.tick();
  assert.equal(f.a.slot,128); assert.equal(f.clears.length,0); assert.equal(f.target().quarantined,true);
  assert.ok(f.traces('preview-cleanup').some(m=>m.rejection==='still-referenced'));
  const id=f.command('end'); f.tick(); f.tick();
  assert.equal(f.a.slot,9); assert.equal(f.clears.length,1);
  assert.deepEqual(f.writes.at(-1), {block:'block0',slot:9,kind:'set'});
  assert.equal(f.reply(id).ended,true); assert.equal(f.target().quarantined,false);
}

// With a different block selected, End cannot know which block plays the
// preview: nothing is written and the slot is kept.
{
  const f=fixture(); f.select(f.a); f.open(); f.command('audition',{file:'first.wav'});
  f.editBufferEvent('EditBuffer_HelixBlockAdded'); f.pump(); f.tick(1600); f.tick(4100); f.tick();
  f.select(f.b); f.pump();
  const before=f.writes.length; const id=f.command('end'); f.tick(6100); f.tick();
  assert.equal(f.reply(id).retained,true); assert.equal(f.writes.length,before);
  assert.equal(f.a.slot,128); assert.equal(f.b.slot,10); assert.equal(f.clears.length,0);
}

// A preset that cannot be read is never treated as "unused".
{
  const f=fixture(); f.select(f.a); f.open(); f.command('audition',{file:'first.wav'});
  f.setPresetUse('unknown'); f.editBufferEvent('EditBuffer_PresetContentsWillChange'); f.a.slot=9; f.pump();
  f.tick(1600); f.tick(4100); f.tick();
  assert.equal(f.clears.length,0); assert.equal(f.target().quarantined,true);
}

// Close during the automatic cleanup after a block change is queued, not
// rejected: it completes, the session ends, and Browse opens a new session.
{
  const f=fixture(); f.select(f.a); f.open(); f.command('audition',{file:'first.wav'});
  f.setDelayedRestore(true); f.select(f.b); f.pump();     // automatic cleanup is now waiting for the Model
  const id=f.command('close',{reason:'escape'});
  assert.equal(f.reply(id),undefined,'the close waits for the running cleanup');
  const busy=f.command('end'); assert.match(f.reply(busy).error,/still finishing/i);
  f.finishRestore(); f.tick();
  assert.equal(f.a.slot,9); assert.equal(f.clears.length,1);
  assert.ok(f.reply(id)); assert.equal(f.reply(id).error,undefined);
  assert.deepEqual(f.sessions,[true,false]);
  assert.equal(f.open(),true); assert.deepEqual(f.sessions,[true,false,true]);
  assert.equal(f.messages.filter(m=>m.type==='toneStart').at(-1).session,2);
}

// The supervisor reports that a session has no helper any more. The session is
// released on the UI thread without any edit, and the Browse click that found
// it dead opens a new one.
{
  const f=fixture(); f.select(f.a); f.open(); f.command('audition',{file:'first.wav'});
  const before=f.writes.length;
  assert.equal(f.open(),true);                            // Browse on the dead session: only a focus request
  f.deliver('toneAbandon',{session:7}); f.pump(); assert.deepEqual(f.sessions,[true]);
  f.deliver('toneAbandon',{session:1}); f.pump();
  assert.equal(f.writes.length,before); assert.equal(f.clears.length,0); assert.equal(f.a.slot,128);
  assert.deepEqual(f.sessions,[true,false,true]);
  assert.equal(f.messages.filter(m=>m.type==='toneStart').at(-1).session,2);
  assert.equal(f.traces('session-abandoned').length,1);
}

// Saving the preset with no preview playing does not block the next preview.
{
  const f=fixture(); f.select(f.a); f.open();
  f.editBufferEvent('EditBuffer_PresetSavedToDevice');
  const id=f.command('audition',{file:'after-save.wav'}); assert.equal(f.reply(id).slot,128);
  // Saved while previewing: the saved preset now uses 128, so nothing is restored or cleared.
  f.editBufferEvent('EditBuffer_PresetSavedToDevice');
  const end=f.command('end'); assert.equal(f.reply(end).retained,true);
  assert.equal(f.a.slot,128); assert.equal(f.clears.length,0);
}
console.log('29 navigation flow scenarios passed');
