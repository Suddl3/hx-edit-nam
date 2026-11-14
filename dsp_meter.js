'use strict';

// DSP meter: each DSP's load as the Floor reports it, read through HX Edit 3.82 x86's own
// "dspLoad0"/"dspLoad1" property code. That code (0x495f90, thiscall(device, dsp) -> float)
// asks the connected device (editor request 6/0x17) and returns its figure / 100, or -1
// when it cannot. HX Edit itself never asks: the meter polls every POLL_MS. The device
// object is the one HX Edit's property reader (0x4972b0) runs on; the hook only notes it.
const dspMeterGuards = Object.freeze({
  '495f90': '558bec515356578bf9c745fc00000000',
  '4972b0': '558bec6aff68',
});

// The Floor admits a preset while 1500 + its blocks' costs <= 9000 per DSP (hx-unbound-floor
// FINDINGS, "Per-DSP budget"), so blocks may use 75 of a DSP's 100: the Floor's own DSP meter
// shows "used/75". The reported load includes the 15 base.
const DSP_BASE = 15, DSP_BUDGET = 75;

function dspMeterView(load) {
  if (typeof load !== 'number' || !Number.isFinite(load) || load < 0) return null;
  const used = Math.max(0, Math.round((load - DSP_BASE) * 10) / 10);
  const share = used / DSP_BUDGET;
  return { load: Math.round(load * 10) / 10, used, budget: DSP_BUDGET,
           level: share > 0.9 ? 'red' : share >= 0.6 ? 'yellow' : 'green' };
}

function installDspMeter(options = {}) {
  if (Process.arch !== 'ia32' || Process.pointerSize !== 4) throw new Error('DSP meter requires HX Edit x86');
  const base = Process.mainModule.base;
  for (const [rva, hex] of Object.entries(dspMeterGuards)) {
    const bytes = new Uint8Array(base.add(parseInt(rva, 16)).readByteArray(hex.length / 2));
    const actual = Array.from(bytes, b => b.toString(16).padStart(2, '0')).join('');
    if (actual !== hex) throw new Error(`DSP meter native byte guard failed at ${rva}`);
  }
  const POLL_MS = options.pollMs || 2000;
  const load = new NativeFunction(base.add(0x495f90), 'float', ['pointer', 'int'], 'thiscall');
  let device = NULL, last = '', failures = 0;
  // Only notes the object; never changes arguments or results.
  const hook = Interceptor.attach(base.add(0x4972b0), { onEnter() { device = this.context.ecx; } });
  const timer = setInterval(() => {
    if (device.isNull()) return;
    let loads;
    try {
      // 0x495f90 asks the device only while the connected device ([+0x38]) is the one its figures
      // are for ([+0x500], not 0x210100) and its query object ([+0x4f8]) exists; otherwise it
      // returns HX Edit's own offline estimate (seen: 36/36 with the Floor switched off). The meter
      // shows the Floor's own figures or nothing.
      const expected = device.add(0x500).readU32();
      const live = device.add(0x38).readU32() === expected && expected !== 0x210100 && !device.add(0x4f8).readPointer().isNull();
      loads = live ? [0, 1].map(dsp => load(device, dsp)) : [-1, -1];
      failures = 0;
    } catch (error) {
      if (++failures === 3) send({ type: 'dspMeter', event: 'read-failed', error: String(error.message) });
      loads = [-1, -1];
    }
    const views = loads.map(dspMeterView);
    const key = JSON.stringify(views);
    if (key === last) return;
    last = key;
    send({ type: 'dspLoad', dsp: views });
  }, POLL_MS);
  return { dispose() { clearInterval(timer); hook.detach(); } };
}
