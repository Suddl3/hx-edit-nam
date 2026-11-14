'use strict';

// Leave HX Edit's window as the user has it when a device reconnects.
//
// HX Edit 3.82 x86 shows its editor window when a device connects: the routine around RVA 0x1a5b20
// calls the framework helper 0x60ccc0(hwnd, show = 1, activate = 1) at 0x1a6039, which runs
// ShowWindow(hwnd, SW_SHOWNORMAL) and then brings the window forward and focuses it. At startup that
// is what first shows the window. On a reconnect (the Floor switched off and on, a cable replugged)
// the window is already on screen, and SW_SHOWNORMAL takes it out of maximized; seen with a Floor
// power cycle, the only top-level window call HX Edit made. So, for that one call site only and only
// while the window is already visible, the helper gets a null window and returns at its own first
// check: no un-maximize, no move, no focus change. Startup and every other caller are untouched.
const windowKeepGuards = Object.freeze({
  '1a6034': '6a016a0153e8826c4600',      // push 1; push 1; push ebx; call 0x60ccc0 (relative)
  '60ccc0': '558bec568b750885f6745b56',  // the helper: hwnd = [ebp+8]; null -> return
});

function installWindowKeeper() {
  if (Process.arch !== 'ia32' || Process.pointerSize !== 4) throw new Error('Window keeper requires HX Edit x86');
  const base = Process.mainModule.base;
  for (const [rva, hex] of Object.entries(windowKeepGuards)) {
    const bytes = new Uint8Array(base.add(parseInt(rva, 16)).readByteArray(hex.length / 2));
    const actual = Array.from(bytes, b => b.toString(16).padStart(2, '0')).join('');
    if (actual !== hex) throw new Error(`Window keeper native byte guard failed at ${rva}`);
  }
  const user = Process.getModuleByName('user32.dll');
  const isWindowVisible = new NativeFunction(user.getExportByName('IsWindowVisible'), 'int', ['pointer']);
  const reconnectShow = base.add(0x1a603e);   // return address of the call at 0x1a6039
  const hook = Interceptor.attach(base.add(0x60ccc0), {
    onEnter(args) {
      if (!this.returnAddress.equals(reconnectShow) || args[0].isNull() || !isWindowVisible(args[0])) return;
      args[0] = NULL;
      send({ type: 'windowKeeper', event: 'kept' });
    },
  });
  return { dispose() { hook.detach(); } };
}
