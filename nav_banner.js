'use strict';

// Native HX Edit 3.82 widgets; the launcher also verifies the full executable hash.
// RVAs are reused from the owner-tested NAM library hooks, not a guessed toolbar
// dispatcher. Only our exact resource button pointers can open the browser.
const navigationBannerGuards = Object.freeze({
  '3bd120': '558bec53568b7508578bf981fe5f636c',
  '3b7ae0': '558bec518bc1538945fc568b5054578b',
  '3bcff0': '558bec83ec10568bf18b46548b88b400',
  '3ba100': '558bec8b41548b80b400000085c0740f',
  '3b8790': '8b41548b80b400000085c074120f1f00',
  '3bbef0': '558bec8bd18b42548b80b400000085c0',
  '3b7e00': '558bec83ec08568b7508578bf9c70600',
  '2156a0': '558bec538b5d085657538bf1e81fe814',
  '215130': '558bec83ec08568bf18b561085d20f84',
  // These prologues stop before absolute addresses rewritten by PE relocation.
  '214ee0': '558bec568bf1',
});

function navigationBannerLayout(redo, limits) {
  if (!redo || !limits || ![redo.x, redo.y, redo.w, redo.h, limits.right].every(Number.isFinite)
      || redo.w <= 0 || redo.h <= 0 || redo.x < 0 || redo.y < 0) return null;
  const left = Math.ceil(redo.x + redo.w + 7);
  const space = Math.floor(limits.right - 7 - left);
  for (const [id, width] of [['t3bf', 196], ['t3bc', 150], ['t3bi', 32]]) {
    if (space >= width) return { id, left, top: Math.round(redo.y), width };
  }
  return null; // Never overlap existing controls in a window too narrow to fit.
}

// The DSP meter (dsp_meter.js, drawn by NamLibrary.exe --dsp-meter) sits right of the TONE3000
// button, on its row, and only where it fits whole before the tempo/tuner controls.
function dspMeterLayout(banner, redo, limits) {
  if (!banner || !redo || !limits) return null;
  const left = banner.left + banner.width + 14;
  const width = Math.min(300, Math.floor(limits.right - 10 - left));
  if (width < 220) return null;
  return { left, top: banner.top, width, height: Math.max(16, Math.round(redo.h)) };
}

function installNavigationBanner(options) {
  if (!options || typeof options.openBrowser !== 'function') throw new Error('Navigation requires openBrowser(window)');
  if (Process.arch !== 'ia32' || Process.pointerSize !== 4) throw new Error('Navigation requires HX Edit x86');
  const base = Process.mainModule.base;
  for (const [rva, hex] of Object.entries(navigationBannerGuards)) {
    const bytes = new Uint8Array(base.add(parseInt(rva, 16)).readByteArray(hex.length / 2));
    const actual = Array.from(bytes, b => b.toString(16).padStart(2, '0')).join('');
    if (actual !== hex) throw new Error(`Navigation native byte guard failed at ${rva}`);
  }
  const method = (rva, type, args) => new NativeFunction(base.add(rva), type, ['pointer', ...args], 'thiscall');
  const findWidget = method(0x3b7ae0, 'pointer', ['uint', 'int']);
  const setVisible = method(0x3bcff0, 'void', ['int', 'int']);
  const isVisible = method(0x3ba100, 'uint8', ['int']);
  const getParent = method(0x3b8790, 'pointer', []);
  const setAnchor = method(0x3bbef0, 'void', ['int', 'int', 'int']);
  const widgetOrigin = method(0x3b7e00, 'pointer', ['pointer']);
  const user = Process.getModuleByName('user32.dll');
  const win = (name, type, args) => new NativeFunction(user.getExportByName(name), type, args, 'stdcall');
  const getClientRect = win('GetClientRect', 'int', ['pointer', 'pointer']);
  const getAncestor = win('GetAncestor', 'pointer', ['pointer', 'uint']);
  const getOwner = win('GetWindowThreadProcessId', 'uint', ['pointer', 'pointer']);
  const isWindow = win('IsWindow', 'int', ['pointer']);
  const activeWindow = win('GetActiveWindow', 'pointer', []);
  const post = win('PostMessageW', 'int', ['pointer', 'uint', 'uint', 'uint']);
  const layoutWake = win('RegisterWindowMessageW', 'uint', ['pointer'])(Memory.allocUtf16String('HX NAM banner layout'));
  const origin = Memory.alloc(8), rectangle = Memory.alloc(16), owner = Memory.alloc(4);
  const ids = { t3bf: 0x74336266, t3bc: 0x74336263, t3bi: 0x74336269 };
  let buttons = {}, library = NULL, editor = NULL, mainWindow = NULL;
  let libraryUiThread = 0;
  let disposed = false, inLayout = false, opening = false, layoutKey = '', lastError = '';
  let layoutDirty = true, nextLayoutAt = 0, layoutTimer = null, dspMeterKey = '';
  const layoutInterval = 50;
  const hooks = [];
  function log(event, details = {}) { send({ type: 'navigationBanner', event, ...details }); }
  function report(error) {
    const message = String(error);
    if (message !== lastError) { lastError = message; log('error', { message }); }
  }
  function canonical(widget) {
    for (let i = 0; i < 8 && !widget.isNull(); i++) {
      const geometry = widget.add(0x54).readPointer();
      if (geometry.isNull()) return NULL;
      const proxy = geometry.add(0xb4).readPointer();
      if (proxy.isNull()) return widget;
      widget = proxy;
    }
    return NULL;
  }
  function bounds(widget) {
    const geometry = widget.add(0x54).readPointer();
    const x = geometry.add(4).readS32(), y = geometry.add(8).readS32();
    widgetOrigin(widget, origin);
    return { x: origin.readS32(), y: origin.add(4).readS32(),
      w: geometry.add(12).readS32() - x, h: geometry.add(16).readS32() - y };
  }
  function discover() {
    for (let widget = canonical(library), depth = 0; depth < 12 && !widget.isNull(); depth++) {
      let found = canonical(findWidget(widget, 0x65647677, 1)); // edvw
      if (widget.add(0x54).readPointer().readU32() === 0x65647677) found = widget;
      if (!found.isNull()) {
        const next = {};
        for (const [name, id] of Object.entries(ids)) {
          next[name] = canonical(findWidget(found, id, 1));
          if (next[name].isNull()) return false;
        }
        editor = found;
        buttons = next;
        return true;
      }
      widget = canonical(getParent(widget));
    }
    return false;
  }
  function validWindow(hwnd) {
    if (editor.isNull() || hwnd.isNull() || !isWindow(hwnd) || !getAncestor(hwnd, 2).equals(hwnd)) return false;
    owner.writeU32(0);
    getOwner(hwnd, owner);
    if (owner.readU32() !== Process.id || !getClientRect(hwnd, rectangle)) return false;
    const view = bounds(editor);
    return view.w > 0 && view.h > 0 && view.x >= 0 && view.y >= 0
      && rectangle.add(8).readS32() >= view.x + view.w
      && rectangle.add(12).readS32() >= view.y + view.h;
  }
  function cancelLayoutTimer() {
    if (layoutTimer !== null) clearTimeout(layoutTimer);
    layoutTimer = null;
  }
  function retryLayout() {
    // A single trailing wake preserves the final resize without running widget
    // methods on Frida's timer thread or accumulating one message per event.
    if (layoutTimer !== null || mainWindow.isNull()) return;
    const window = mainWindow;
    layoutTimer = setTimeout(() => {
      layoutTimer = null;
      if (!disposed && layoutDirty && window.equals(mainWindow)) post(window, layoutWake, 0, 0);
    }, Math.max(1, nextLayoutAt - Date.now()));
  }
  function sync(hwnd = NULL) {
    if (disposed || inLayout || !libraryUiThread || Process.getCurrentThreadId() !== libraryUiThread ||
        (!layoutDirty && !mainWindow.isNull()) || library.isNull()) return;
    if (!hwnd.isNull() && !mainWindow.isNull() && !hwnd.equals(mainWindow)) return;
    if (Date.now() < nextLayoutAt) { retryLayout(); return; }
    nextLayoutAt = Date.now() + layoutInterval;
    cancelLayoutTimer();
    inLayout = true;
    try {
      if (editor.isNull() && !discover()) return;
      if (!hwnd.isNull()) {
        if (!validWindow(hwnd)) return;
        mainWindow = hwnd;
      }
      const redo = canonical(findWidget(editor, 0x72656462, 1));
      const tempo = canonical(findWidget(editor, 0x746d7677, 1));
      const tuner = canonical(findWidget(editor, 0x74756e62, 1));
      if (redo.isNull() || tempo.isNull() || !isVisible(redo, 1)) return;
      const view = bounds(editor), rb = bounds(redo);
      let right = view.x + view.w - 10;
      for (const reserved of [tempo, tuner]) {
        if (!reserved.isNull() && isVisible(reserved, 1)) right = Math.min(right, bounds(reserved).x);
      }
      const layout = navigationBannerLayout({ ...rb, x: rb.x - view.x, y: rb.y - view.y }, { right: right - view.x });
      const meter = dspMeterLayout(layout, rb, { right: right - view.x });
      const meterKey = meter && !mainWindow.isNull()
        ? `${mainWindow.toUInt32()}:${view.x + meter.left}:${view.y + meter.top}:${meter.width}:${meter.height}` : 'hidden';
      if (meterKey !== dspMeterKey) {
        dspMeterKey = meterKey;
        // Window client pixels, like the docked library pane.
        send({ type: 'dspMeterLayout', window: mainWindow.isNull() ? 0 : mainWindow.toUInt32(), visible: meterKey !== 'hidden',
               x: meter ? view.x + meter.left : 0, y: meter ? view.y + meter.top : 0,
               w: meter ? meter.width : 0, h: meter ? meter.height : 0 });
      }
      const key = layout ? `${layout.id}:${layout.left}:${layout.top}` : 'hidden';
      layoutDirty = false;
      if (key === layoutKey) return;
      layoutKey = key;
      for (const [name, button] of Object.entries(buttons)) {
        const show = layout !== null && name === layout.id;
        if (show) {
          setAnchor(button, 3, layout.left, 1); // left, existing NAM sidebar convention
          setAnchor(button, 0, layout.top, 1); // top
        }
        setVisible(button, Number(show), 1);
      }
      log('layout', { variant: layout?.id || 'hidden', left: layout?.left ?? null });
    } catch (error) { report(error); } finally { inLayout = false; }
  }
  function open(sender) {
    if (disposed || inLayout || opening || Process.getCurrentThreadId() !== libraryUiThread ||
        !Object.values(buttons).some(button => button.equals(sender))) return;
    opening = true;
    try {
      sync();
      if (!isVisible(sender, 1)) return;
      if (!validWindow(mainWindow)) {
        const hwnd = activeWindow(); // Only inside this editor's exact native button event.
        if (!validWindow(hwnd)) { log('blocked', { reason: 'main-window-unavailable' }); return; }
        mainWindow = hwnd;
      }
      options.openBrowser(mainWindow, {dockPanel: dockPanel()});
      log('open');
    } finally { opening = false; }
  }
  // 0x215130 is the library refresh called by its constructor at 0x214e0c;
  // 0x2156a0 is the existing tab-switch hook. The former covers first paint
  // without requiring a tab click. Missing editor parents are retried on paint.
  for (const rva of [0x215130, 0x2156a0]) {
    hooks.push(Interceptor.attach(base.add(rva), {
      onEnter() {
        const thread = Process.getCurrentThreadId();
        if (inLayout || (libraryUiThread && thread !== libraryUiThread)) return;
        this.bannerLibrary = this.context.ecx.add(0x10).readPointer();
        if (!this.bannerLibrary.isNull()) libraryUiThread = thread;
      },
      onLeave() {
        if (!this.bannerLibrary || this.bannerLibrary.isNull()) return;
        library = this.bannerLibrary;
        layoutDirty = true;
        sync();
      },
    }));
  }
  hooks.push(Interceptor.attach(base.add(0x214ee0), {
    onEnter() {
      if (Process.getCurrentThreadId() !== libraryUiThread) return;
      if (this.context.ecx.add(0x10).readPointer().equals(library)) {
        library = NULL; editor = NULL; mainWindow = NULL; buttons = {}; layoutKey = '';
        libraryUiThread = 0;
        layoutDirty = true; nextLayoutAt = 0; cancelLayoutTimer();
      }
    },
  }));
  hooks.push(Interceptor.attach(user.getExportByName('DispatchMessageW'), {
    onEnter(args) {
      if (disposed || inLayout || opening || !libraryUiThread ||
          Process.getCurrentThreadId() !== libraryUiThread || library.isNull()) return;
      const event = args[0].add(4).readU32();
      // Never enter native widget layout from mouse movement. Paint is only a
      // recovery opportunity for initial/dirty layout, not a periodic scan.
      const geometry = event === 5 || event === 0x47 || event === 0x232 || event === 0x202;
      if (!geometry && event !== layoutWake && (event !== 15 || (!layoutDirty && !mainWindow.isNull()))) return;
      const hwnd = args[0].readPointer();
      if (hwnd.isNull() || (!mainWindow.isNull() && !hwnd.equals(mainWindow))) return;
      this.bannerWindow = hwnd;
      this.bannerGeometry = geometry;
    },
    onLeave() {
      if (!this.bannerWindow) return;
      if (this.bannerGeometry) layoutDirty = true;
      sync(this.bannerWindow);
    },
  }));
  hooks.push(Interceptor.attach(base.add(0x3bd120), {
    onEnter(args) {
      if (!disposed && !inLayout && !opening && Process.getCurrentThreadId() === libraryUiThread &&
          args[0].toUInt32() === 0x2162705f && Object.values(buttons).some(b => b.equals(this.context.ecx))) {
        this.bannerButton = this.context.ecx;
      }
    },
    onLeave() { if (this.bannerButton) { try { open(this.bannerButton); } catch (error) { report(error); } } },
  }));
  log('installed');
  // Detach is for process teardown. Native widget mutation belongs on the UI thread.
  function dockPanel() {
    if (disposed || Process.getCurrentThreadId() !== libraryUiThread || editor.isNull()) return NULL;
    return canonical(findWidget(editor, 0x50525657, 1)); // PRVW, native parameter pane
  }
  return { dockPanel, dispose() { disposed = true; cancelLayoutTimer(); for (const hook of hooks) hook.detach(); } };
}

if (typeof module !== 'undefined' && module.exports) {
  module.exports = { navigationBannerLayout, navigationBannerGuards, installNavigationBanner };
}
