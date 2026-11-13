'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const source = fs.readFileSync(path.join(__dirname, '../nam_view.js'), 'utf8');
const start = source.indexOf('  Interceptor.attach(address(0x2156a0),');
const end = source.indexOf('  const constructing =', start);
assert.ok(start >= 0 && end > start);
const memory = new Map();
class Pointer {
  constructor(value) { this.value = value; }
  add(offset) { return new Pointer(this.value + offset); }
  readPointer() { return new Pointer(memory.get(this.value) || 0); }
  equals(other) { return this.value === other.value; }
  isNull() { return !this.value; }
  toUInt32() { return this.value; }
}
const ptr = value => new Pointer(value);
const controller = ptr(0x1000), library = ptr(0x2000);
memory.set(0x1010, library.value); memory.set(0x105c, 0x3000);
let hook, requests = 0;
const context = vm.createContext({
  Process: {getCurrentThreadId: () => 7}, ptr, address: ptr,
  Interceptor: {attach(_address, value) { hook = value; }},
  libraryUiThread: 7, libraryInputDepth: 0, irController: ptr(0), tab: 0,
  libraryViews: [1,2,3,4], libraryRoot: ptr(0), allCapturesButton: ptr(0),
  namList: ptr(0x4000),
  findWidget: () => ptr(0), orderTabs() {}, setVisible() {},
  requestLibraryVerification() { requests++; },
  refreshProfiles() {}, hasActiveModel: () => false,
  pumpSidebarPresentation() {},
});
vm.runInContext(source.slice(start, end), context, {timeout:1000});
function select(tab, inputDepth=0, caller=0xabcdef) {
  context.libraryInputDepth = inputDepth;
  const invocation = {context: {ecx: controller}, returnAddress: ptr(caller)};
  const args = [ptr(tab)];
  hook.onEnter.call(invocation, args); hook.onLeave.call(invocation);
}
select(3); // persisted constructor state
assert.equal(requests, 0);
select(1, 1, 0x2153b4); // progress redirects IR back into NAM
assert.equal(context.tab, 3); assert.equal(requests, 0);
select(3, 1); // unrelated click refreshes already-selected tab
assert.equal(requests, 0);
select(0, 1); select(3, 1); // actual owner changes from Presets to NAM
assert.equal(requests, 1);
select(1, 1, 0x2153b4); select(3, 1);
assert.equal(requests, 1, 'progress/refresh cannot re-arm verification');
select(0); select(3); // non-input programmatic switch
assert.equal(requests, 1);
select(0, 1); select(3, 2); // nested native input remains an owner gesture
assert.equal(requests, 2);
console.log('library entry probe passed: restore, progress, reselect, input and nested dispatch');
