'use strict';

// Execute the launcher's entire, unmodified Frida program without loading a
// process or native library. Interceptor installation changes code bytes, as
// real trampolines do; a later guard must therefore see the changed bytes.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const { navigationBannerGuards } = require('../nav_banner.js');
const { targetBindingGuards } = require('../target_binding.js');
const source = fs.readFileSync(0, 'utf8');
const base = 0x400000;
const shared = [0x3bd120, 0x214ee0, 0x2156a0];

function startup(corruptRva, program = source) {
  const bytes = new Map(), handlers = new Map(), messages = [], nativeCalls = [];
  let nextAllocation = 0x10000000;
  class Pointer {
    constructor(value) { this.value = Number(value); }
    add(offset) { return new Pointer(this.value + offset); }
    isNull() { return this.value === 0; }
    equals(other) { return this.value === other.value; }
    toString() { return '0x' + this.value.toString(16); }
    readU8() { return bytes.get(this.value) || 0; }
    readByteArray(size) {
      return Uint8Array.from({ length: size }, (_, i) => this.add(i).readU8()).buffer;
    }
    writeByteArray(value) {
      Array.from(value).forEach((byte, i) => bytes.set(this.value + i, byte));
    }
  }
  const ptr = value => new Pointer(value);
  const alloc = size => {
    const result = ptr(nextAllocation);
    nextAllocation += size + 16;
    return result;
  };
  for (const [rva, hex] of Object.entries({ ...navigationBannerGuards, ...targetBindingGuards })) {
    ptr(base + parseInt(rva, 16)).writeByteArray(Buffer.from(hex, 'hex'));
  }
  // The NAM formatter has its own independent, non-entrypoint byte guard.
  ptr(base + 0x26bfed).writeByteArray(Buffer.from('8b8b440100008d95f0feffff', 'hex'));
  if (corruptRva !== undefined) bytes.set(base + corruptRva, 0xcc);

  const key = address => address instanceof Pointer ? address.value : address;
  const patchCode = address => {
    if (address instanceof Pointer) bytes.set(address.value, 0xe9);
  };
  const moduleFor = name => ({ getExportByName: symbol => name + '!' + symbol });
  const context = vm.createContext({
    NULL: ptr(0), ptr,
    Process: { arch: 'ia32', pointerSize: 4, id: 42,
      mainModule: { base: ptr(base) }, getModuleByName: moduleFor,
      getCurrentThreadId: () => 1 },
    Module: { load: moduleFor },
    Memory: { alloc,
      allocUtf8String: text => alloc(Buffer.byteLength(text) + 1),
      allocUtf16String: text => alloc((text.length + 1) * 2) },
    NativeFunction: function(address) {
      return function() {
        nativeCalls.push(key(address));
        // These are the only synchronous API calls during initialization.
        // Never execute a real API or silently invent editor/device behavior.
        if (address === 'user32.dll!LoadCursorW') return ptr(0x70000000);
        if (address === 'user32.dll!RegisterWindowMessageW') return 0xc001;
        assert.fail('unexpected native call during startup: ' + key(address));
      };
    },
    NativeCallback: function(callback) { return callback; },
    Interceptor: {
      attach(address, hook) {
        const addressKey = key(address), listeners = handlers.get(addressKey) || [];
        // Frida permits multiple listeners on the same function. Do not mask
        // collisions by overwriting a previous listener, or skip patching code.
        listeners.push(hook); handlers.set(addressKey, listeners); patchCode(address);
        return { detach() { listeners.splice(listeners.indexOf(hook), 1); } };
      },
      replace(address) { patchCode(address); },
    },
    send: message => messages.push(message),
    recv: () => ({ wait() { assert.fail('startup must not wait for a host reply'); } }),
    // Register timers/listeners only. Native UI has not resumed yet, so none of
    // their event callbacks or any device workflow is executed by this probe.
    setInterval: () => 1, setTimeout: () => 2,
    clearInterval() {}, clearTimeout() {},
  });
  let failure;
  try { vm.runInContext(program, context, { timeout: 5000, filename: 'launcher-composition.js' }); }
  catch (error) { failure = error; }
  return { bytes, handlers, messages, nativeCalls, failure };
}

const valid = startup();
assert.ifError(valid.failure);
assert.equal(valid.messages.filter(message => message.type === 'ready').length, 1);
assert.equal(valid.messages.filter(message => message.type === 'navigationBanner' && message.event === 'installed').length, 1);
for (const rva of shared) {
  assert.ok(valid.handlers.get(base + rva)?.length >= 2,
    'both NAM and banner listeners must survive at ' + rva.toString(16));
  assert.equal(valid.bytes.get(base + rva), 0xe9, 'hook must change the guarded entrypoint');
}

// Negative control: relocate only the actual banner initializer to its former
// position at the end of startup. Execute all the real installers again; this
// must reproduce Candidate54's guard failure, proving the mock sees hooks.
const bannerCall = source.match(/^(?:const navigationBanner = )?installNavigationBanner\([^\r\n]+\);$/m);
assert.ok(bannerCall, 'launcher must include the navigation banner initializer');
const withoutBanner = source.replace(bannerCall[0], '');
const readyOffset = withoutBanner.lastIndexOf('\nsend(');
assert.ok(readyOffset >= 0, 'launcher must include the final ready message');
const lateBanner = withoutBanner.slice(0, readyOffset) + '\n' + bannerCall[0] + withoutBanner.slice(readyOffset);
const oldOrder = startup(undefined, lateBanner);
assert.match(String(oldOrder.failure), /Navigation native byte guard failed at 3bd120/);
assert.ok(!oldOrder.messages.some(message => message.type === 'ready'));

for (const rva of shared) {
  const invalid = startup(rva);
  assert.match(String(invalid.failure), new RegExp('Navigation native byte guard failed at ' + rva.toString(16)));
  assert.ok(!invalid.messages.some(message => message.type === 'ready'), 'bad native bytes cannot reach ready');
  assert.ok(shared.every(sharedRva => !invalid.handlers.has(base + sharedRva)),
    'guard must reject native mismatch before either feature hooks a shared entrypoint');
}
console.log('navigation startup probe passed: actual composition, old-order failure, mutable code guards and shared listeners');
