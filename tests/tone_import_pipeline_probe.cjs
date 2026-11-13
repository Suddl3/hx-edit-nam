'use strict';

// Run the real dialog and native batch hooks with poisoned memory. No native
// library is loaded: only the existing Python file validator touches fixtures.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const { spawnSync } = require('node:child_process');
const input = JSON.parse(fs.readFileSync(0, 'utf8'));
const source = fs.readFileSync(input.source, 'utf8');

function replay(scenario, program = source) {
  const memory = new Map(), hooks = new Map(), traces = [], imports = [], validations = [];
  let next = 0x10000000, pending;
  class Pointer {
    constructor(value) { this.value = Number(value); }
    add(offset) { return new Pointer(this.value + offset); }
    isNull() { return this.value === 0; }
    equals(other) { return this.value === other.value; }
    readU8() { return memory.get(this.value) || 0; }
    writeByteArray(value) { Array.from(value).forEach((n, i) => memory.set(this.value + i, n)); return this; }
    readU16() { return this.readU8() + this.add(1).readU8() * 256; }
    writeU16(n) { return this.writeByteArray([n & 255, (n >>> 8) & 255]); }
    readU32() { return (this.readU16() + this.add(2).readU16() * 65536) >>> 0; }
    writeU32(n) { return this.writeByteArray([n & 255, (n >>> 8) & 255, (n >>> 16) & 255, (n >>> 24) & 255]); }
    readPointer() { return new Pointer(this.readU32()); }
    writePointer(pointer) { return this.writeU32(pointer.value); }
    writeUtf16String(text) {
      // Matches the recorded actual Frida17.5.2 poisoned-memory fixture.
      return this.writeByteArray(Buffer.from(text.split('\0')[0] + '\0', 'utf16le'));
    }
    readUtf16String(length) {
      const chars = [];
      for (let i = 0; i < (length ?? 4096); i++) {
        const unit = this.add(i * 2).readU16();
        if (length === undefined && unit === 0) return String.fromCharCode(...chars);
        chars.push(unit);
      }
      if (length !== undefined) return String.fromCharCode(...chars);
      throw Error('unterminated fixture UTF16 string');
    }
    readUtf8String() {
      const bytes = [];
      for (let i = 0; i < 8192; i++) {
        const byte = this.add(i).readU8();
        if (!byte) return Buffer.from(bytes).toString('utf8');
        bytes.push(byte);
      }
      throw Error('unterminated fixture UTF8 string');
    }
  }
  const ptr = value => new Pointer(value), nil = ptr(0), base = ptr(0x400000);
  const alloc = size => { const p = ptr(next); next += size + 32; return p; };
  const utf8 = text => alloc(Buffer.byteLength(text) + 1).writeByteArray(Buffer.from(text + '\0'));
  const utf16 = text => alloc((text.length + 1) * 2).writeUtf16String(text);
  const key = address => address instanceof Pointer ? address.value : address;
  const moduleFor = name => ({ getExportByName: symbol => name + '!' + symbol });
  const controller = alloc(256), list = alloc(16), row = alloc(512);
  controller.add(0x3c).writePointer(list);
  row.add(0x10c).writeU32(scenario.occupied ? 1 : 0);
  const transfer = { file: scenario.file, slot: scenario.slot ?? 128 };
  const context = vm.createContext({
    NULL: nil, Process: {arch: 'ia32', pointerSize: 4, mainModule: {base}, getModuleByName: moduleFor},
    Module: {load: moduleFor}, Memory: {alloc, allocUtf16String: utf16},
    NativeCallback: function(callback) { return callback; },
    NativeFunction: function(address) {
      if (key(address) === base.value + 0x380610) return () => row;
      if (key(address) === base.value + 0x2123c0) return (_controller, _batch, first) => imports.push(first);
      return () => assert.fail('unexpected native call: ' + key(address));
    },
    Interceptor: {attach() {}, replace(address, callback) { hooks.set(key(address), callback); }},
    namView: {refreshProfiles() {}, trackImport() { assert.fail('tone import cannot become an ordinary import'); }},
    send(event) {
      if (event.type !== 'validateImport') { traces.push(event); return; }
      validations.push(event);
      const script = 'import json,sys\nfrom nam_convert import validate_import\np=json.load(sys.stdin)\n' +
        'try: print(json.dumps({"profiles":validate_import(p["files"],p["firstSlot"],p["occupied"],p["namOnly"])}))\n' +
        'except (OSError,ValueError) as e: print(json.dumps({"error":str(e)}))\n';
      const result = spawnSync(input.python, ['-B', '-X', 'utf8', '-c', script], {input: JSON.stringify(event),
        cwd: input.root, encoding: 'utf8', timeout: 5000, windowsHide: true});
      assert.equal(result.status, 0, result.stderr);
      pending = JSON.parse(result.stdout);
    },
    recv(type, callback) {
      assert.equal(type, 'converted');
      return {wait() { assert.ok(pending); callback({payload: pending}); pending = undefined; }};
    },
    testController: controller, testTransfer: transfer,
  });
  vm.runInContext(program, context, {timeout: 2000, filename: 'nam_import.js'});
  vm.runInContext('namImportController=testController; toneImport=testTransfer;', context);

  const file = scenario.file, capacity = scenario.capacity ?? 1024;
  const buffer = alloc(capacity * 2).writeByteArray(Buffer.alloc(capacity * 2, 0x41));
  // One stale filename behind the first NUL reconstructs one wrong native path,
  // matching Candidate55's fileCount=1 followed by a path mismatch.
  if (capacity > file.length + 16) buffer.add((file.length + 1) * 2)
    .writeByteArray(Buffer.from('stale.wav\0\0', 'utf16le'));
  const filters = alloc(64).writeByteArray(Buffer.from('WAV files\0*.wav\0\0', 'utf16le'));
  const title = alloc(256), dialog = alloc(76);
  dialog.writeU32(76);
  dialog.add(12).writePointer(filters);
  dialog.add(28).writePointer(scenario.nullBuffer ? nil : buffer);
  dialog.add(32).writeU32(capacity);
  dialog.add(36).writePointer(title);
  dialog.add(40).writeU32(128);
  let shown = 1, decoded;
  if (scenario.unsupplied) decoded = [file];
  else {
    shown = hooks.get('comdlg32.dll!GetOpenFileNameW')(dialog);
    if (shown) {
      const parts = [];
      for (let i = 0; i < capacity;) {
        const part = buffer.add(i * 2).readUtf16String();
        if (!part) break;
        parts.push(part); i += part.length + 1;
      }
      decoded = parts.length === 1 ? parts : parts.slice(1).map(name => parts[0] + '\\' + name);
      assert.equal(dialog.add(56).readU16(), file.lastIndexOf('\\') + 1);
      assert.equal(dialog.add(58).readU16(), file.length - 3);
      assert.equal(title.readUtf16String(), file.slice(file.lastIndexOf('\\') + 1));
    }
  }
  if (shown) {
    const files = scenario.tamper || decoded, batch = alloc(32), array = alloc(files.length * 8);
    files.forEach((path, i) => {
      const string = alloc(32); string.add(0x10).writePointer(utf8(path));
      array.add(i * 8 + 4).writePointer(string);
    });
    batch.add(8).writePointer(array); batch.add(0x10).writeU32(0); batch.add(0x14).writeU32(files.length);
    hooks.get(base.value + 0x2123c0)(controller, batch, 0);
  }
  const result = {shown, decoded, transfer, imports, validations, traces};
  // Diagnostics must remain structurally useful without copying the path.
  for (const event of traces) {
    assert.equal(Object.values(event).some(value => typeof value === 'string' && value.includes(file)), false);
    assert.ok(Object.keys(event).every(k => ['type', 'reason', 'namOnly', 'tone', 'files', 'captures',
      'phase', 'fileCount', 'dialogSupplied', 'pathMatches'].includes(k)));
  }
  return result;
}

for (const scenario of input.scenarios) {
  const result = replay(scenario);
  if (scenario.accept) {
    assert.equal(result.shown, 1); assert.equal(result.transfer.error, undefined);
    assert.deepEqual(result.decoded, [scenario.file]);
    assert.deepEqual(result.imports, [(scenario.slot ?? 128) - 1]);
    assert.equal(result.validations.length, 1);
    assert.equal(result.validations[0].files[0], scenario.file);
    assert.equal(typeof result.transfer.name, 'string');
  } else {
    assert.equal(result.imports.length, 0);
    assert.match(result.transfer.error, new RegExp(scenario.error));
    assert.equal(result.validations.length, scenario.validation ? 1 : 0);
  }
}

// Negative control restores the exact pre-fix callsite, while retaining every
// real guard. It must reproduce the same fast rejection before validation.
const legacy = source.replace('writeToneImportFile(buffer, capacity, file);', "buffer.writeUtf16String(file + '\\0');");
assert.notEqual(legacy, source);
const rejected = replay(input.scenarios[0], legacy);
assert.equal(rejected.imports.length, 0);
assert.equal(rejected.validations.length, 0);
assert.match(rejected.transfer.error, /different file/);
assert.equal(rejected.traces.find(e => e.phase === 'import-batch-check').reason, 'file-path-mismatch');
console.log(JSON.stringify({scenarios: input.scenarios.length, legacyRejected: true}));
