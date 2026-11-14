import json
from pathlib import Path
import shutil
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[1]
NODE = shutil.which('node')

# A fake Frida world: base 0x400000, one window that is visible or not, and the helper hook.
HARNESS = r"""
const sent = [];
let hook = null, visible = 1;
const mem = new Map([[0x5a6034, '6a016a0153e8826c4600'], [0xa0ccc0, '558bec568b750885f6745b56']]);
class P {
  constructor(v) { this.v = v; }
  add(n) { return new P(this.v + n); }
  equals(o) { return o.v === this.v; }
  isNull() { return this.v === 0; }
  readByteArray(n) { const hex = mem.get(this.v); return Uint8Array.from(hex.match(/../g).slice(0, n).map(h => parseInt(h, 16))).buffer; }
}
globalThis.NULL = new P(0);
globalThis.Process = { arch: 'ia32', pointerSize: 4, mainModule: { base: new P(0x400000) },
  getModuleByName: () => ({ getExportByName: () => new P(1) }) };
globalThis.NativeFunction = function () { return () => visible; };
globalThis.Interceptor = { attach(at, callbacks) { hook = { at: at.v, ...callbacks }; return { detach() {} }; } };
globalThis.send = m => sent.push(m);
""" + (ROOT / 'window_keep.js').read_text(encoding='utf-8') + r"""
installWindowKeeper();
function call(returnAddress, hwnd, isVisible) {
  visible = isVisible; const args = [new P(hwnd)];
  hook.onEnter.call({ returnAddress: new P(returnAddress) }, args);
  return args[0].v;
}
const out = {
  hookAt: hook.at,
  reconnectVisible: call(0x5a603e, 0x1234, 1),   // reconnect, window on screen: helper gets null
  startupHidden: call(0x5a603e, 0x1234, 0),      // first show at startup: untouched
  otherCaller: call(0x5a7000, 0x1234, 1),        // any other caller: untouched
  sent,
};
process.stdout.write(JSON.stringify(out));
"""


@unittest.skipUnless(NODE, 'node is not installed')
class WindowKeeperTests(unittest.TestCase):
    def test_only_the_reconnect_show_of_a_visible_window_is_skipped(self):
        result = subprocess.run([NODE, '-e', HARNESS], capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        out = json.loads(result.stdout)
        self.assertEqual(out['hookAt'], 0xa0ccc0)
        self.assertEqual(out['reconnectVisible'], 0)
        self.assertEqual(out['startupHidden'], 0x1234)
        self.assertEqual(out['otherCaller'], 0x1234)
        self.assertEqual(out['sent'], [{'type': 'windowKeeper', 'event': 'kept'}])

    def test_guard_mismatch_installs_nothing(self):
        broken = HARNESS.replace("'558bec568b750885f6745b56']]", "'90909090909090909090909090']]", 1)
        result = subprocess.run([NODE, '-e', broken], capture_output=True, text=True, timeout=30)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Window keeper native byte guard failed at 60ccc0', result.stderr)

    def test_launcher_and_build_include_it(self):
        launcher = (ROOT / 'start-hx-edit-nam.pyw').read_text(encoding='utf-8')
        self.assertIn("'window_keep.js'", launcher)
        self.assertIn('try { installWindowKeeper(); } catch (error)', launcher)
        self.assertIn("'window_keep.js'", (ROOT / 'build-windows.py').read_text(encoding='utf-8'))


if __name__ == '__main__':
    unittest.main()
