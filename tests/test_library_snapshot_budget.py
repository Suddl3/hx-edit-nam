"""Busy library verification must avoid native row scans and presentation work."""
from pathlib import Path
import shutil
import subprocess

import pytest


def test_actual_snapshot_skips_all_rows_while_busy_then_reads_only_permanent_slots():
    node = shutil.which('node')
    if not node:
        pytest.skip('Node.js unavailable')
    root = Path(__file__).resolve().parents[1]
    source = (root / 'nam_view.js').read_text(encoding='utf-8')
    begin = source.index('  function discoverySnapshot(')
    end = source.index('  function exportDiscoverySlot(', begin)
    actual = source[begin:end]
    setup = r'''
const assert = require('node:assert/strict');
const memory = new Map();
let calls;
function resetCalls() { calls = {flush:0, count:0, row:0, pointerReads:0, valueReads:0, names:0}; }
class Pointer {
  constructor(value) { this.value = value; }
  add(offset) { return new Pointer(this.value + offset); }
  isNull() { return this.value === 0; }
  toString() { return String(this.value); }
  readPointer() { calls.pointerReads++; return new Pointer(memory.get(this.value) || 0); }
  readU32() { calls.valueReads++; return memory.get(this.value) || 0; }
  readU8() { return this.readU32(); }
  readCString(limit) { assert.equal(limit,256); calls.names++; return memory.get(this.value) || ''; }
}
const nil = new Pointer(0), controller = new Pointer(0x1000), manager = new Pointer(0x2000), list = new Pointer(0x3000);
memory.set(controller.value + 0x270, manager.value);
memory.set(manager.value + 0x44, 0x210001);
const rows = Array.from({length:128}, (_,i) => new Pointer(0x10000 + i * 0x200));
for (const slot of [9,11,127,128]) {
  memory.set(rows[slot-1].value + 0x10c,1);
  memory.set(rows[slot-1].value,'Slot ' + slot);
}
memory.set(rows[10].value + 0x114,0x10);
let onUI = true, irController = controller, namImportController = nil;
let discoveryLocked = false, exportPending = false, workflowBusy = false, audition = null, toneImport = null;
const libraryReady = () => onUI;
const flushInventoryPresentation = () => calls.flush++;
const irList = () => list;
const slotNames = new Map();
const discoveryCount = value => { assert.equal(value.value,list.value); calls.count++; return 128; };
const getIrRow = (value,index) => {
  assert.equal(value.value,list.value); assert(index >= 0 && index < 127, 'preview128 must never be read');
  calls.row++; return rows[index];
};
'''
    checks = r'''
function expectIdle(label) {
  resetCalls(); assert.equal(discoverySnapshot(12),null,label);
  assert.deepEqual(calls,{flush:0,count:0,row:0,pointerReads:0,valueReads:0,names:0},label);
}
discoveryLocked=true; expectIdle('native discovery lock'); discoveryLocked=false;
exportPending=true; expectIdle('pending native export'); exportPending=false;
workflowBusy=true; expectIdle('browser operation'); workflowBusy=false;
audition={}; expectIdle('active audition'); audition=null;
namImportController=controller; expectIdle('native import'); namImportController=nil;
toneImport={}; expectIdle('tone transfer'); toneImport=null;
onUI=false; expectIdle('foreign thread'); onUI=true;
irController=nil; expectIdle('destroyed library controller'); irController=controller;
memory.set(manager.value + 0x40,1);
resetCalls(); assert.equal(discoverySnapshot(12),null);
assert.equal(calls.row,0,'native manager lock must stop before enumerating rows');
assert.equal(calls.names,0);
memory.set(manager.value + 0x40,0);
resetCalls(); const snapshot=discoverySnapshot(12);
assert.equal(snapshot.device,0x210001); assert.equal(snapshot.revision,12); assert.equal(snapshot.busy,false);
assert.deepEqual(snapshot.slots,[9,127]); assert.equal(snapshot.excluded,1);
assert.deepEqual(snapshot.slotSignatures,[[9,'Slot 9'],[127,'Slot 127']]);
assert.equal(snapshot.signature,JSON.stringify(snapshot.slotSignatures));
assert.equal(calls.flush,1); assert.equal(calls.count,1); assert.equal(calls.row,127); assert.equal(calls.names,2);
console.log('snapshot budget passed: busy paths do no native work; explicit idle snapshot reads127 permanent rows');
'''
    result = subprocess.run([node, '-'], input=setup + actual + checks, capture_output=True,
                            text=True, timeout=15, cwd=root)
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'snapshot budget passed' in result.stdout
