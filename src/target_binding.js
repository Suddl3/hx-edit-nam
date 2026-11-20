'use strict';

// HX Edit 3.82 x86 typed EditBuffer API. It addresses a DSP/block/parameter,
// independently of the currently selected parameter widget. See BOUND-TARGET.md.
const targetBindingGuards = Object.freeze({
  '504f50': '558bec6aff',
  '504f78': '8bf9897df08b751483cbffc745a40900',
  '5178c0': '558bec6aff',
  '5178e8': '8bf9897de88b4d188bc12500000040c7',
  '1a6ac0': '558bec6aff',
  '1a1b30': '568bf18b0683c0f683f8047714ff2485',
  '23cb50': '558bec6aff',
});

function targetBindingRestoreAllowed(binding, observed, options) {
  return !!binding && binding.valid === true && !!observed && !!options
    && options.context === binding.context && options.originalSlot === binding.originalSlot
    && Number.isInteger(binding.originalSlot) && binding.originalSlot >= 1 && binding.originalSlot <= 127
    && options.expectedSlot === 128 && observed.slot === 128
    && observed.engine === binding.engine && observed.valueType === binding.valueType;
}

function installTargetBinding(options = {}) {
  if (Process.arch !== 'ia32' || Process.pointerSize !== 4) throw new Error('Target binding requires HX Edit x86');
  const base = Process.mainModule.base;
  for (const [rva, expected] of Object.entries(targetBindingGuards)) {
    const actual = Array.from(new Uint8Array(base.add(parseInt(rva, 16)).readByteArray(expected.length / 2)),
      b => b.toString(16).padStart(2, '0')).join('');
    if (actual !== expected) throw new Error(`Target binding native byte guard failed at ${rva}`);
  }
  const method = (rva, type, args) => new NativeFunction(base.add(rva), type, ['pointer', ...args], 'thiscall');
  const constructString = method(0x1a6ac0, 'pointer', ['pointer']);
  const destroyVariant = method(0x1a1b30, 'void', []);
  const readValue = method(0x504f50, 'int', ['int', 'pointer', 'pointer', 'pointer']);
  const writeValue = method(0x5178c0, 'int', ['int', 'pointer', 'pointer', 'pointer', 'uint']);
  const sliderValue = method(0x23cb50, 'pointer', ['pointer']);
  const engines = new Set(options.supportedEngines || ['HD2_AmpNAMLite']);
  const bindings = new WeakMap();
  function fail(reason) { throw new Error(`NAM target binding: ${reason}`); }
  function emptyVariant() {
    const result = Memory.alloc(16);
    result.writeByteArray(new Uint8Array(16));
    result.writeU32(9); // native null/opaque pointer value
    return result;
  }
  function string(text) {
    const result = emptyVariant();
    constructString(result, Memory.allocUtf8String(text));
    return result;
  }
  function nativeString(object) {
    if (object.isNull()) fail('missing native string');
    const representation = object.add(4).readPointer();
    if (representation.isNull()) return '';
    const length = representation.add(4).readU32();
    if (length > 256) fail('native string exceeds identity bound');
    const data = representation.add(16).readPointer();
    if (length && data.isNull()) fail('invalid native string');
    return length ? data.readUtf8String(length) : '';
  }
  function decode(value) {
    const type = value.readU32();
    if (type === 4) return { type, value: value.add(8).readS32() };
    if (type === 5) return { type, value: value.add(8).readU32() };
    if (type === 8) return { type, value: value.add(8).readFloat() };
    if (type === 10) return { type, value: nativeString(value.add(8).readPointer()) };
    fail('unsupported native parameter type');
  }
  function editorReady(editor) {
    if (editor.isNull() || !editor.readPointer().equals(base.add(0x963dfc))) return false;
    const table = editor.readPointer();
    return table.add(0x7c).readPointer().equals(base.add(0x5178c0))
      && table.add(0x88).readPointer().equals(base.add(0x504f50))
      && table.add(0xdc).readPointer().equals(base.add(0x51aaf0));
  }
  function internal(binding, context) {
    const state = bindings.get(binding);
    if (!state || binding.valid !== true || context !== state.context) fail('expired context');
    if (Process.getCurrentThreadId() !== state.thread) fail('not on the captured UI thread');
    if (!editorReady(state.editor)) fail('editor instance changed');
    return state;
  }
  function parameter(state, name) {
    let block = null, key = null;
    const value = emptyVariant();
    try {
      block = string(state.blockName); key = string(name);
      const result = readValue(state.editor, state.dsp, block, key, value);
      if (result < 0) fail(`read was rejected (${result})`);
      return decode(value);
    } finally {
      destroyVariant(value);
      if (key) destroyVariant(key);
      if (block) destroyVariant(block);
    }
  }
  function observed(state) {
    const model = parameter(state, '@model');
    const value = parameter(state, state.parameter);
    if (model.type !== 10 || !engines.has(model.value)) fail('block is no longer a supported NAM engine');
    if (![4, 5, 8].includes(value.type) || !Number.isInteger(value.value) || value.value < 1 || value.value > 128) {
      fail('native slot value is not an integer in 1–128');
    }
    return { slot: value.value, valueType: value.type, engine: model.value };
  }
  function capture(controller, model, config) {
    if (!config || config.context === undefined || !Number.isInteger(config.slot) || config.slot < 1 || config.slot > 127) {
      fail('capture requires a permanent original slot and context');
    }
    if (controller.isNull() || !controller.readPointer().equals(base.add(0x8d948c))) fail('unknown model selector');
    if (model.isNull() || !model.readPointer().equals(base.add(0x8e942c))) fail('unknown Model slider');
    if (model.add(0x104).readU8() !== 0) fail('global parameter cannot be bound as a block');
    const editor = controller.add(0x34).readPointer();
    if (!editorReady(editor)) fail('unsupported edit buffer');
    const dsp = controller.add(0x80).readS32(), blockName = nativeString(controller.add(0x84));
    const name = nativeString(model.add(0xf8));
    // The patched NAM engine inherits the stock IR model's Index parameter.
    // Never bind a different numeric slider merely because its value matches.
    if (![0, 1].includes(dsp) || !/^block\d{1,2}$/.test(blockName) || name !== 'Index') {
      fail('unsupported block or parameter identity');
    }
    const state = { editor, dsp, blockName, parameter: name, context: config.context,
      thread: Process.getCurrentThreadId(), originalSlot: config.slot };
    const current = observed(state);
    const value = emptyVariant();
    let slider;
    try { sliderValue(model, value); slider = decode(value); }
    finally { destroyVariant(value); }
    // The slider's valueType=0 emits signed integers, while the stored variant
    // may retain an unsigned numeric tag. Identity is the same integral slot;
    // preserve the edit buffer's own tag when restoring it later.
    if (current.slot !== config.slot || ![4, 5, 8].includes(slider.type) || slider.value !== config.slot) {
      fail('selected slider and addressed block disagree');
    }
    state.engine = current.engine; state.valueType = current.valueType;
    const binding = { valid: true, dsp, blockName, parameter: name, engine: current.engine,
      originalSlot: config.slot, context: config.context };
    bindings.set(binding, state);
    return binding;
  }
  function read(binding, config) {
    const state = internal(binding, config?.context);
    const current = observed(state);
    if (current.engine !== state.engine || current.valueType !== state.valueType) fail('bound block changed');
    return current;
  }
  function restore(binding, originalSlot, config) {
    const state = internal(binding, config?.context);
    const current = observed(state);
    if (!targetBindingRestoreAllowed({ ...state, valid: binding.valid }, current,
      { ...config, originalSlot })) fail('preview ownership or original value changed');
    let block = null, key = null;
    const value = emptyVariant();
    try {
      block = string(state.blockName); key = string(state.parameter);
      value.writeU32(state.valueType);
      if (state.valueType === 8) value.add(8).writeFloat(state.originalSlot);
      else value.add(8).writeU32(state.originalSlot);
      // Same typed call/flag used by HXParameterViewController for a single cva!
      // edit outside a slider gesture. No cursor or parameter-widget mutation.
      const result = writeValue(state.editor, state.dsp, block, key, value, 0x80000000);
      if (result < 0) fail(`restore was rejected (${result})`);
    } finally {
      destroyVariant(value);
      if (key) destroyVariant(key);
      if (block) destroyVariant(block);
    }
    // This acknowledges acceptance of the native edit, not completion. HX Edit
    // can dispatch its parameter notification after this call unwinds. The
    // preview lifecycle must poll read() (and the visible Model when selected)
    // before releasing128. Never make a successful API return stand in for it.
    return true;
  }
  function release(binding) {
    if (binding) { binding.valid = false; bindings.delete(binding); }
    // No native pointers to temporary strings are retained between calls.
  }
  function peekSlot(editor, dsp, blockName) {
    // The capture slot of any NAM block, read from the edit buffer. Read-only and unbound: for
    // presentation (which icon a block shows), never for an edit. null when it is not a NAM block
    // or cannot be read on this thread.
    try {
      if (!editorReady(editor) || ![0, 1].includes(dsp) || !/^block\d{1,2}$/.test(blockName)) return null;
      const state = {editor, dsp, blockName, parameter: 'Index'};
      const model = parameter(state, '@model');
      if (model.type !== 10 || !engines.has(model.value)) return null;
      const value = parameter(state, 'Index');
      return [4, 5, 8].includes(value.type) && Number.isInteger(value.value) && value.value >= 1 && value.value <= 128 ?
        value.value : null;
    } catch (_) { return null; }
  }
  function peekModel(editor, dsp, blockName) {
    // Any block's model symbol and Index (an IR block's slot), read from the edit buffer.
    // Read-only, for noticing an IR block that plays a NAM capture's slot. null when unreadable.
    try {
      if (!editorReady(editor) || ![0, 1].includes(dsp) || !/^block\d{1,2}$/.test(blockName)) return null;
      const state = {editor, dsp, blockName, parameter: 'Index'};
      const model = parameter(state, '@model');
      if (model.type !== 10 || typeof model.value !== 'string') return null;
      let index = null;
      try {                                    // a block with no Index (most models) still has a model
        const value = parameter(state, 'Index');
        if ([4, 5, 8].includes(value.type) && Number.isInteger(value.value)) index = value.value;
      } catch (_) { index = null; }
      return {model: model.value, index};
    } catch (_) { return null; }
  }
  function setIndex(editor, dsp, blockName, slot) {
    // A NAM block's capture slot, written as restore() writes it (one undo step), keeping the
    // stored value's own type. Only for a supported NAM engine and a permanent slot.
    if (!editorReady(editor) || ![0, 1].includes(dsp) || !/^block\d{1,2}$/.test(blockName) ||
        !Number.isInteger(slot) || slot < 1 || slot > 127) fail('unsupported block or slot');
    const state = {editor, dsp, blockName, parameter: 'Index'};
    const model = parameter(state, '@model');
    if (model.type !== 10 || !engines.has(model.value)) fail('not a NAM block');
    const current = parameter(state, 'Index');
    if (![4, 5, 8].includes(current.type)) fail('unexpected slot value');
    let block = null, key = null;
    const value = emptyVariant();
    try {
      block = string(blockName); key = string('Index');
      value.writeU32(current.type);
      if (current.type === 8) value.add(8).writeFloat(slot); else value.add(8).writeU32(slot);
      const result = writeValue(editor, dsp, block, key, value, 0x80000000);
      if (result < 0) fail(`write was rejected (${result})`);
    } finally {
      destroyVariant(value);
      if (key) destroyVariant(key);
      if (block) destroyVariant(block);
    }
    return true;
  }
  // What the block picker itself does when a model row is chosen (0x21a470): a block's model
  // changes through the editor's parameter write (vtbl+0x7c = 0x5178c0, "@model"); an empty cell
  // gets a block through vtbl+0xa8 = 0x4ff300(dsp, &Variant(symbol), &Variant{5, (row<<16)|col},
  // flags). One undo step each, and HX Edit fills the defaults itself: far quicker than pasting a
  // favorite. Symbols only of HX Edit's models.
  const symbolPattern = /^[A-Za-z0-9_]{3,64}$/;
  function setModel(editor, dsp, blockName, symbol) {
    if (!editorReady(editor) || ![0, 1].includes(dsp) || !/^block\d{1,2}$/.test(blockName) || !symbolPattern.test(symbol)) {
      fail('unsupported block or model');
    }
    let block = null, key = null, value = null;
    try {
      block = string(blockName); key = string('@model'); value = string(symbol);
      const result = writeValue(editor, dsp, block, key, value, 0x80000000);
      if (result < 0) fail(`model change was rejected (${result})`);
    } finally {
      if (value) destroyVariant(value);
      if (key) destroyVariant(key);
      if (block) destroyVariant(block);
    }
    return true;
  }
  function addModel(editor, dsp, row, col, symbol) {
    if (!editorReady(editor) || ![0, 1].includes(dsp) || ![0, 1].includes(row) || !Number.isInteger(col) ||
        col < 0 || col > 15 || !symbolPattern.test(symbol)) fail('unsupported cell or model');
    const entry = editor.readPointer().add(0xa8).readPointer();
    if (!entry.equals(base.add(0x4ff300))) fail('unexpected editor add-block entry');
    const add = new NativeFunction(entry, 'int', ['pointer', 'int', 'pointer', 'pointer', 'uint'], 'thiscall');
    let value = null;
    const cell = emptyVariant();
    try {
      value = string(symbol);
      cell.writeU32(5); cell.add(8).writeU32(((row << 16) | col) >>> 0);
      const result = add(editor, dsp, value, cell, 0x80000000);
      if (result < 0) fail(`adding the block was rejected (${result})`);
    } finally {
      if (value) destroyVariant(value);
    }
    return true;
  }
  return { capture, read, restore, release, peekSlot, peekModel, setIndex, setModel, addModel };
}

if (typeof module !== 'undefined' && module.exports) {
  module.exports = { targetBindingGuards, targetBindingRestoreAllowed, installTargetBinding };
}
