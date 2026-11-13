'use strict';

// Run the actual helper and DispatchMessage hook, with no native process or UI.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const source = fs.readFileSync(path.join(__dirname, '..', 'nam_view.js'), 'utf8');
const begin = source.indexOf('  function endSidebarDrag(');
const end = source.indexOf('  function selectedSlot()', begin);
assert.ok(begin >= 0 && end > begin, 'sidebar capture implementation is present');
const hookSource = source.slice(begin, end);
const pointer = value => ({ value, equals(other) { return value === other.value; }, isNull() { return value === 0; } });
const main = pointer(1), other = pointer(2), nil = pointer(0);

function fixture() {
  const calls = [], messages = [];
  let hook, capture = nil, uiThread = true, captureSucceeds = true;
  const view = { side: { w: 320 } };
  const context = vm.createContext({
    drag: null, sidebarWidth: 320, resizeCursor: pointer(3), libraryInputDepth: 0,
    user32: { getExportByName: name => name },
    Interceptor: { attach(name, callbacks) { assert.equal(name, 'DispatchMessageW'); hook = callbacks; } },
    libraryReady: () => uiThread,
    getCapture() { calls.push('getCapture'); return capture; },
    setCapture(hwnd) { calls.push('setCapture'); if (captureSucceeds) capture = hwnd; },
    releaseCapture() {
      assert.equal(context.drag, null, 'clear state before reentrant native release');
      assert.ok(capture.equals(main), 'never release another window capture');
      calls.push('releaseCapture'); capture = nil;
    },
    sidebar() { calls.push('sidebar'); return view; },
    sidebarEdge(hwnd) { calls.push('sidebarEdge'); return hwnd.equals(main) ? { view, left: 0 } : null; },
    resizeSidebar(_view, width) { calls.push(['resize', width]); },
    syncSidebar() { calls.push('syncSidebar'); },
    pumpSidebarPresentation() {},
    NULL: nil,
    setCursor() { calls.push('setCursor'); },
    send(message) { messages.push(message); },
  });
  vm.runInContext(hookSource, context, { timeout: 1000 });
  function dispatch(event, wParam = 0, hwnd = main) {
    const fields = { 4: event, 8: wParam, 12: 340 | (40 << 16) };
    const message = { readPointer: () => hwnd, add: offset => ({
      readU32: () => fields[offset], writeU32(value) { fields[offset] = value; },
    }) };
    const invocation = {};
    hook.onEnter.call(invocation, [message]);
    assert.equal(context.libraryInputDepth, uiThread && [0x100,0x201,0x202].includes(event) ? 1 : 0);
    hook.onLeave.call(invocation);
    assert.equal(context.libraryInputDepth, 0, 'input scope ends after dispatch, including cancelled drag');
    return fields[4];
  }
  function start() {
    assert.equal(dispatch(0x201, 1), 0);
    assert.ok(context.drag && capture.equals(main));
    calls.length = 0;
  }
  return { context, calls, messages, dispatch, start,
    setCaptureOwner(value) { capture = value; },
    captureOwner: () => capture,
    setUiThread(value) { uiThread = value; },
    failCapture() { captureSucceeds = false; } };
}

// Normal dragging still consumes the gesture, saves width once, and releases.
{
  const f = fixture(); f.start();
  assert.equal(f.dispatch(0x200, 1), 0);
  assert.ok(f.calls.some(call => Array.isArray(call) && call[0] === 'resize'));
  assert.equal(f.dispatch(0x202), 0);
  assert.equal(f.context.drag, null);
  assert.equal(f.calls.filter(call => call === 'releaseCapture').length, 1);
  assert.deepEqual(f.messages.map(message => [message.type, message.width]), [['sidebarWidth', 320]]);
  assert.equal(f.dispatch(0x200), 0x200, 'normal hover is not consumed after release');
}

// Cancel, focus loss, deactivation, destruction and capture loss leave the
// original notification untouched; they are not completed drag gestures.
for (const event of [0x1f, 8, 6, 0x1c, 2, 0x82, 0x215]) {
  const f = fixture(); f.start();
  if (event === 0x215) f.setCaptureOwner(other);
  assert.equal(f.dispatch(event), event);
  assert.equal(f.context.drag, null, 'cancellation event ' + event.toString(16));
  assert.equal(f.calls.filter(call => call === 'releaseCapture').length, event === 0x215 ? 0 : 1);
  assert.equal(f.messages.length, 0, 'cancelled gesture does not persist width');
  assert.equal(f.dispatch(0x200), 0x200);
}

// Direct WindowProc notifications bypass DispatchMessage. Ownership checking
// still detects the loss on the very next queued UI message, without releasing
// or stealing the replacement owner's capture.
for (const event of [15, 0x200, 0x201, 0x202]) {
  const f = fixture(); f.start(); f.setCaptureOwner(other);
  assert.equal(f.dispatch(event, 1), event);
  assert.equal(f.context.drag, null);
  assert.ok(f.captureOwner().equals(other));
  assert.ok(!f.calls.includes('releaseCapture') && !f.calls.includes('setCapture'));
}

// Missing button-up (for example after switching windows) is caught from the
// next mouse move's button state, even if capture still reports this window.
{
  const f = fixture(); f.start();
  assert.equal(f.dispatch(0x200, 0), 0x200);
  assert.equal(f.context.drag, null);
  assert.equal(f.calls.filter(call => call === 'releaseCapture').length, 1);
  assert.ok(!f.calls.some(call => Array.isArray(call) && call[0] === 'resize'));
}

// Activation is not deactivation; messages on other HWNDs must not cancel a
// live gesture solely because that other window changed focus.
{
  const f = fixture(); f.start();
  for (const [event, wParam, hwnd] of [[6, 1, main], [0x1c, 1, main], [8, 0, other]]) {
    assert.equal(f.dispatch(event, wParam, hwnd), event);
    assert.ok(f.context.drag);
  }
}

// Frida intercepts DispatchMessage in all target threads. Do no capture or
// widget calls on a different thread, and do not erase the UI thread's state.
{
  const f = fixture(); f.start(); f.setUiThread(false);
  assert.equal(f.dispatch(0x200), 0x200);
  assert.ok(f.context.drag);
  assert.deepEqual(f.calls, []);
  f.setUiThread(true);
  f.dispatch(0x200);
  assert.equal(f.context.drag, null);
}

// Refused capture and another control's existing capture never swallow a down.
for (const blocked of ['failed', 'another-owner']) {
  const f = fixture();
  if (blocked === 'failed') f.failCapture(); else f.setCaptureOwner(other);
  assert.equal(f.dispatch(0x201, 1), 0x201);
  assert.equal(f.context.drag, null);
  assert.ok(!f.calls.includes('releaseCapture'));
  if (blocked === 'another-owner') assert.ok(!f.calls.includes('setCapture'));
}
console.log('sidebar capture probe passed: loss, cancellation, button state, ownership and UI thread');
