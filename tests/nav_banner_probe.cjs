'use strict';
// Models the existing native APIs and memory structure. This executes our hook
// lifecycle and exact-pointer click routing, not the real editor or its USB code.
const assert = require('node:assert/strict');
const { navigationBannerLayout: layout, navigationBannerGuards: guards,
  installNavigationBanner: install } = require('../nav_banner.js');
for (let available = 0; available < 500; available++) {
  const redo = { x: 400, y: 8, w: 35, h: 29 };
  const result = layout(redo, { right: 449 + available });
  const expected = available >= 196 ? 't3bf' : available >= 150 ? 't3bc' : available >= 32 ? 't3bi' : null;
  assert.equal(result?.id ?? null, expected);
  if (result) assert.ok(result.left + result.width <= 442 + available);
}
assert.equal(layout({ x: NaN, y: 8, w: 35, h: 29 }, {right: 1000}), null);
assert.equal(layout({ x: 400, y: 8, w: 0, h: 29 }, {right: 1000}), null);

const memory = new Map(), widgets = new Map(), handlers = new Map(), native = new Map(), logs = [];
let allocation = 0x10000000, active = 0x900000, windows = new Map([[active, {pid: 42, width: 1600, height: 900}]]);
let clock = 1000, currentThread = 7, nativeCalls = 0, onNative = null, nextTimer = 0;
const timers = new Map(), posted = [], layoutWake = 0xc100;
const originalNow = Date.now;
Date.now = () => clock;
global.setTimeout = (callback, delay) => { const id = ++nextTimer; timers.set(id, {callback, due:clock + delay}); return id; };
global.clearTimeout = id => timers.delete(id);
class Pointer {
  constructor(value) { this.value = Number(value); }
  add(n) { return new Pointer(this.value + n); }
  equals(p) { return this.value === p.value; }
  isNull() { return this.value === 0; }
  readU32() { return Number(memory.get(this.value) || 0) >>> 0; }
  readS32() { return this.readU32() | 0; }
  readPointer() { return new Pointer(this.readU32()); }
  writeU32(value) { memory.set(this.value, Number(value)); }
  readByteArray(size) {
    const hex = guards[(this.value - 0x400000).toString(16)];
    return Uint8Array.from(Buffer.from(hex || '00'.repeat(size), 'hex')).buffer;
  }
  toUInt32() { return this.value >>> 0; }
  toString() { return this.value.toString(16); }
}
const p = n => new Pointer(n);
const alloc = n => { const value = allocation; allocation += n + 16; return p(value); };
global.NULL = p(0); global.Process = { arch: 'ia32', pointerSize: 4, id: 42, mainModule: {base: p(0x400000)},
  getCurrentThreadId: () => currentThread,
  getModuleByName: () => ({getExportByName: name => name}) };
global.Memory = {alloc, allocUtf16String: value => alloc((value.length + 1) * 2)}; global.send = m => logs.push(m);
global.NativeFunction = function(address) {
  const key = address instanceof Pointer ? address.value - 0x400000 : address;
  assert.ok(native.has(key), `unknown native call ${key}`);
  return (...args) => {
    nativeCalls++;
    const result = native.get(key)(...args);
    if (onNative) onNative(key);
    return result;
  };
};
global.Interceptor = {attach(address, hook) {
  const key = address instanceof Pointer ? address.value - 0x400000 : address;
  handlers.set(key, hook); return {detach() { handlers.delete(key); }};
}};
function widget(id, parent, rect) {
  const pointer = alloc(0x100), geometry = alloc(0xc0);
  pointer.add(0x54).writeU32(geometry.value);
  geometry.writeU32(id); geometry.add(4).writeU32(rect.x); geometry.add(8).writeU32(rect.y);
  geometry.add(12).writeU32(rect.x + rect.w); geometry.add(16).writeU32(rect.y + rect.h);
  widgets.set(pointer.value, {pointer, parent, id, rect: {...rect}, visible: true});
  return pointer;
}
const main = widget(0x6d61696e, NULL, {x:0,y:0,w:1600,h:900});
const library = widget(0x6c627679, main, {x:0,y:0,w:280,h:880});
const editor = widget(0x65647677, main, {x:280,y:0,w:1320,h:880});
const redo = widget(0x72656462, editor, {x:830,y:8,w:35,h:29});
const tempo = widget(0x746d7677, editor, {x:1490,y:8,w:100,h:29});
const tuner = widget(0x74756e62, editor, {x:1455,y:8,w:29,h:29});
widgets.get(tuner.value).visible = false;
const buttonIds = {t3bf:0x74336266,t3bc:0x74336263,t3bi:0x74336269};
const buttons = Object.fromEntries(Object.entries(buttonIds).map(([name,id]) => [name,widget(id,editor,{x:871,y:8,w:name==='t3bf'?196:name==='t3bc'?150:32,h:29})]));
for (const button of Object.values(buttons)) widgets.get(button.value).visible = false;
function descendant(value, ancestor) {
  for(let current=widgets.get(value); current; current=widgets.get(current.parent.value)) {
    if(current.pointer.equals(ancestor)) return true;
  }
  return false;
}
native.set(0x3b7ae0,(root,id) => [...widgets.values()].find(w => w.id===id && descendant(w.pointer.value,root))?.pointer || NULL);
native.set(0x3bcff0,(widget,shown) => {widgets.get(widget.value).visible=!!shown;});
native.set(0x3ba100,widget => Number(widgets.get(widget.value).visible));
native.set(0x3b8790,widget => widgets.get(widget.value).parent);
native.set(0x3bbef0,(widget,edge,amount) => {
  const w=widgets.get(widget.value), parent=widgets.get(w.parent.value);
  if(edge===3) w.rect.x=parent.rect.x+amount;
  else if(edge===0) w.rect.y=parent.rect.y+amount;
  else assert.fail('unexpected anchor');
});
native.set(0x3b7e00,(widget,out) => {const rect=widgets.get(widget.value).rect; out.writeU32(rect.x);out.add(4).writeU32(rect.y);return out;});
native.set('GetClientRect',(hwnd,out)=>{const w=windows.get(hwnd.value);if(!w)return 0;out.add(8).writeU32(w.width);out.add(12).writeU32(w.height);return 1;});
native.set('GetAncestor',hwnd=>hwnd);
native.set('GetWindowThreadProcessId',(hwnd,out)=>{out.writeU32(windows.get(hwnd.value)?.pid||0);return 1;});
native.set('IsWindow',hwnd=>Number(windows.has(hwnd.value)));
native.set('GetActiveWindow',()=>p(active));
native.set('RegisterWindowMessageW',()=>layoutWake);
native.set('PostMessageW',(window,event)=>{posted.push({window:window.value,event});return 1;});
const opens=[];
const api=install({openBrowser:window=>opens.push(window.value)});
function invoke(key, context, args=[], result=NULL) {
  const state={context:{ecx:context}};
  const hook=handlers.get(key);
  hook.onEnter?.call(state,args);hook.onLeave?.call(state,result);
}
const controller=alloc(0x100);controller.add(0x10).writeU32(library.value);
const message=alloc(32);
function dispatch(event, window=0x900000) {
  message.writeU32(window);message.add(4).writeU32(event);
  invoke('DispatchMessageW',NULL,[message]);
}
function advance(milliseconds) {
  clock += milliseconds;
  for (const [id,timer] of [...timers]) if (timer.due <= clock) {
    timers.delete(id); timer.callback();
  }
  while(posted.length) {const event=posted.shift();dispatch(event.event,event.window);}
}
let count=nativeCalls;
for(let i=0;i<1000;i++) dispatch(0x200);
assert.equal(nativeCalls,count,'mouse movement before initialization never runs native layout');
invoke(0x215130,controller);
assert.equal(widgets.get(buttons.t3bf.value).visible,true);
assert.equal(widgets.get(buttons.t3bc.value).visible,false);
assert.equal(widgets.get(buttons.t3bf.value).rect.x,872);
advance(50);dispatch(15); // Initial paint establishes the main HWND.
count=nativeCalls;
for(let i=0;i<1000;i++) {dispatch(0x200);dispatch(0x100);dispatch(15);}
assert.equal(nativeCalls,count,'mouse, keyboard and clean paint bursts do no native layout work');
assert.equal(timers.size,0);
currentThread=99;
for(const event of [5,15,0x47,0x202,0x232,layoutWake]) dispatch(event);
invoke(0x215130,controller);
invoke(0x3bd120,buttons.t3bf,[p(0x2162705f)]);
assert.equal(nativeCalls,count,'foreign-thread messages and callbacks never access native widgets');
assert.equal(opens.length,0);
currentThread=7;
invoke(0x3bd120,redo,[p(0x2162705f)]);
invoke(0x3bd120,buttons.t3bf,[p(0x216c6273)]);
invoke(0x3bd120,buttons.t3bc,[p(0x2162705f)]);
assert.equal(opens.length,0,'only visible own pushbutton can open browser');
invoke(0x3bd120,buttons.t3bf,[p(0x2162705f)]);
assert.deepEqual(opens,[active]);
// Floating-browser focus does not replace the retained main editor HWND.
active=0x900001; windows.set(active,{pid:77,width:1800,height:1000});
count=nativeCalls;
for(const event of [5,15,0x47,0x202,layoutWake]) dispatch(event,active);
assert.equal(nativeCalls,count,'other windows cannot trigger banner layout');
invoke(0x3bd120,buttons.t3bf,[p(0x2162705f)]);
assert.deepEqual(opens,[0x900000,0x900000]);
// Resize based on real right-side control geometry, without moving stock controls.
widgets.get(tempo.value).rect.x=1040;  // room for the 150-wide wordmark button, not the 196-wide one
dispatch(5);
count=nativeCalls;
for(let i=0;i<1000;i++) dispatch(0x47);
assert.equal(nativeCalls,count,'resize burst is gated before native layout calls');
assert.equal(timers.size,1,'one trailing wake coalesces the resize burst');
advance(50);
assert.equal(widgets.get(buttons.t3bf.value).visible,false);
assert.equal(widgets.get(buttons.t3bc.value).visible,true);
assert.equal(timers.size,0);
widgets.get(tempo.value).rect.x=915;dispatch(5);advance(50);
assert.equal(widgets.get(buttons.t3bi.value).visible,true);
widgets.get(tempo.value).rect.x=905;dispatch(5);advance(50);
assert.equal(Object.values(buttons).some(b=>widgets.get(b.value).visible),false);
// Native widget calls can dispatch messages or trigger refresh/click hooks.
// They must not nest a second layout pass or synthesize a browser opening.
widgets.get(tempo.value).rect.x=1490;
advance(50);
let reentered=0;
onNative=key=>{
  if(key!==0x3b7ae0) return;
  reentered++;
  const before=nativeCalls;
  dispatch(5);dispatch(15);invoke(0x215130,controller);
  invoke(0x3bd120,buttons.t3bf,[p(0x2162705f)]);
  assert.equal(nativeCalls,before,'nested native callbacks are rejected before widget access');
};
dispatch(0x232);onNative=null;
assert.ok(reentered>0);
assert.equal(opens.length,2);
assert.equal(timers.size,0,'self-generated layout callbacks do not schedule another pass');
// Losing the main native window fails closed rather than opening against a helper.
windows.delete(0x900000);invoke(0x3bd120,buttons.t3bf,[p(0x2162705f)]);
assert.equal(opens.length,2);
assert.ok(logs.some(m=>m.event==='blocked'));
dispatch(5);assert.equal(timers.size,1);
invoke(0x214ee0,controller);
assert.equal(timers.size,0,'library destruction cancels the trailing wake');
count=nativeCalls;advance(50);dispatch(0x200);
assert.equal(nativeCalls,count);
api.dispose();assert.equal(handlers.size,0);
// Reject changed native bytes before registering hooks.
const originalRead=Pointer.prototype.readByteArray;
Pointer.prototype.readByteArray=function(size){return new Uint8Array(size).buffer;};
assert.throws(()=>install({openBrowser(){}}),/byte guard/);
assert.equal(handlers.size,0);
Pointer.prototype.readByteArray=originalRead;
Date.now=originalNow;
console.log('navigation banner probe passed: 500 widths, pointer routing, focus, initial/resize layout, input bursts, thread isolation, rate budget, reentrancy, teardown and byte guards');
