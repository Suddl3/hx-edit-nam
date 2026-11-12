"""Ask how to import NAM captures (as they are, or a lighter trained copy) and run the training.

On the Helix LT NAM4_LAYERS firmware (namLayers / HX_EDIT_NAM_LAYERS) the IR import shows a Windows
task dialog: the capture as it is, or a copy trained down to 16 or 14 layers (nam_light.LightJob)
for the NAM Lite 16 / 14 blocks; on NAM5_PEDAL (namPedal) also a pedal model for the NAM Pedal block
(helix-lt-nam's tools/nam_pedal.py, about 25 s). Training runs with a progress dialog and Cancel; the
results are what HX Edit then imports. 14 layers Quick is not offered: on Benson Preamp G8 it lost
8-11 dB above 6 kHz.

The dialogs have no owner window: HX Edit's UI thread waits for the import reply, and a window
owned by a waiting thread of another process would share its input queue and freeze with it.
They are centred on HX Edit's window and kept on top instead.
"""

import ctypes
from ctypes import wintypes
from hashlib import sha256
import os
from pathlib import Path
import shutil
import tempfile
import threading

import re
import subprocess

import nam_light

CACHE = Path(os.environ.get('LOCALAPPDATA', Path.home())) / 'HX Edit NAM' / 'Converted'

# (key, layers, quality, title, detail); quality None: the capture as it is
MODES = (
    ('original', 23, None, 'As it is · NAM A2 Lite',
     'Instant. Any capture, full quality. NAM A2 Lite block, about 36% of a DSP.'),
    ('pedal', 0, 'pedal', 'Pedal · ~25 s · NAM Pedal',
     'About 25 seconds. For pedals: overdrive, distortion, fuzz (on 13 TONE3000 pedals 0.4-6% error, a fuzz 6%). '
     'NAM Pedal block, about 8.5% of a DSP. Not for captures with an amp and a cabinet.'),
    ('16-quick', 16, 'quick', '16 layers · fast · NAM Lite 16',
     'About 2 minutes. Close to the original (Benson Preamp: 0.54% error). NAM Lite 16 block, about 29%.'),
    ('16-standard', 16, 'standard', '16 layers · best · NAM Lite 16',
     'About 6 minutes. As good as the original (0.36% error). NAM Lite 16 block, about 29%.'),
    ('14-standard', 14, 'standard', '14 layers · best · NAM Lite 14',
     'About 5 minutes. Slightly below the original (0.60% error). NAM Lite 14 block, about 28%.'),
)
BUTTON_BASE = 1000
IR_NAME_LIMIT = 31

if os.name == 'nt':
    WM_USER = 0x400
    TDM_CLICK_BUTTON, TDM_SET_PROGRESS_BAR_RANGE, TDM_SET_PROGRESS_BAR_POS = WM_USER + 102, WM_USER + 105, WM_USER + 106
    TDM_SET_ELEMENT_TEXT = WM_USER + 108
    TDE_CONTENT, TDE_MAIN_INSTRUCTION = 0, 3
    TDN_CREATED, TDN_BUTTON_CLICKED, TDN_TIMER = 0, 2, 4
    TDF_ALLOW_DIALOG_CANCELLATION, TDF_USE_COMMAND_LINKS = 0x8, 0x10
    TDF_SHOW_PROGRESS_BAR, TDF_CALLBACK_TIMER = 0x200, 0x800
    TDCBF_CANCEL_BUTTON, IDCANCEL = 0x8, 2

    CALLBACK = ctypes.WINFUNCTYPE(ctypes.c_long, wintypes.HWND, wintypes.UINT, wintypes.WPARAM,
                                  wintypes.LPARAM, ctypes.c_ssize_t)

    class BUTTON(ctypes.Structure):
        _pack_ = 1
        _fields_ = [('id', ctypes.c_int), ('text', wintypes.LPCWSTR)]

    class CONFIG(ctypes.Structure):
        _pack_ = 1
        _fields_ = [('cbSize', wintypes.UINT), ('hwndParent', wintypes.HWND), ('hInstance', wintypes.HINSTANCE),
                    ('dwFlags', ctypes.c_int), ('dwCommonButtons', ctypes.c_int),
                    ('pszWindowTitle', wintypes.LPCWSTR), ('pszMainIcon', ctypes.c_void_p),
                    ('pszMainInstruction', wintypes.LPCWSTR), ('pszContent', wintypes.LPCWSTR),
                    ('cButtons', wintypes.UINT), ('pButtons', ctypes.POINTER(BUTTON)), ('nDefaultButton', ctypes.c_int),
                    ('cRadioButtons', wintypes.UINT), ('pRadioButtons', ctypes.POINTER(BUTTON)),
                    ('nDefaultRadioButton', ctypes.c_int), ('pszVerificationText', wintypes.LPCWSTR),
                    ('pszExpandedInformation', wintypes.LPCWSTR), ('pszExpandedControlText', wintypes.LPCWSTR),
                    ('pszCollapsedControlText', wintypes.LPCWSTR), ('pszFooterIcon', ctypes.c_void_p),
                    ('pszFooter', wintypes.LPCWSTR), ('pfCallback', CALLBACK), ('lpCallbackData', ctypes.c_ssize_t),
                    ('cxWidth', wintypes.UINT)]

    class ACTCTX(ctypes.Structure):
        _fields_ = [('cbSize', wintypes.ULONG), ('dwFlags', wintypes.DWORD), ('lpSource', wintypes.LPCWSTR),
                    ('wProcessorArchitecture', wintypes.USHORT), ('wLangId', wintypes.USHORT),
                    ('lpAssemblyDirectory', wintypes.LPCWSTR), ('lpResourceName', wintypes.LPCWSTR),
                    ('lpApplicationName', wintypes.LPCWSTR), ('hModule', wintypes.HMODULE)]

MANIFEST = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<assembly xmlns="urn:schemas-microsoft-com:asm.v1" manifestVersion="1.0"><dependency><dependentAssembly>
<assemblyIdentity type="win32" name="Microsoft.Windows.Common-Controls" version="6.0.0.0"
 processorArchitecture="*" publicKeyToken="6595b64144ccf1df" language="*"/></dependentAssembly></dependency></assembly>
'''
_actctx = None


def _common_controls_v6():
    """Task dialogs live in ComCtl32 6, which python.exe's manifest does not ask for: activate it."""
    global _actctx
    kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
    if _actctx is None:
        path = Path(tempfile.gettempdir()) / 'hx-edit-nam-comctl6.manifest'
        path.write_text(MANIFEST, encoding='utf-8')
        ctx = ACTCTX(cbSize=ctypes.sizeof(ACTCTX), lpSource=str(path))
        kernel32.CreateActCtxW.restype = wintypes.HANDLE
        handle = kernel32.CreateActCtxW(ctypes.byref(ctx))
        if handle in (None, wintypes.HANDLE(-1).value):
            raise OSError(f'Could not load the Windows task dialog (error {ctypes.get_last_error()}).')
        _actctx = handle
    cookie = ctypes.c_size_t()
    kernel32.ActivateActCtx(wintypes.HANDLE(_actctx), ctypes.byref(cookie))
    return cookie


def _centre_on_top(hwnd, owner):
    user32 = ctypes.windll.user32
    box, area = wintypes.RECT(), wintypes.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(box))
    x = y = None
    if owner and user32.GetWindowRect(wintypes.HWND(owner), ctypes.byref(area)):
        x = area.left + (area.right - area.left - (box.right - box.left)) // 2
        y = area.top + (area.bottom - area.top - (box.bottom - box.top)) // 2
    flags = 0x0001 | 0x0010 if x is None else 0x0010          # SWP_NOSIZE (| SWP_NOMOVE), SWP_NOACTIVATE off
    user32.SetWindowPos(hwnd, wintypes.HWND(-1), x or 0, y or 0, 0, 0, flags | 0x0001)
    user32.SetForegroundWindow(hwnd)


def _show(config):
    comctl32 = ctypes.WinDLL('comctl32')
    pressed = ctypes.c_int()
    cookie = _common_controls_v6()
    try:
        result = comctl32.TaskDialogIndirect(ctypes.byref(config), ctypes.byref(pressed), None, None)
    finally:
        ctypes.windll.kernel32.DeactivateActCtx(0, cookie)
    if result:
        raise OSError(f'The Windows task dialog failed (0x{result & 0xffffffff:08x}).')
    return pressed.value


def modes(pedal):
    return [m for m in MODES if pedal or m[0] != 'pedal']


def default_mode(gear, pedal):
    """The chooser's default for a capture of this TONE3000 gear type: the pedal model for a pedal (when the
    firmware has NAM Pedal), the capture as it is for anything else."""
    return 'pedal' if pedal and (gear or '').lower() == 'pedal' else 'original'


def ask(window, instruction, content, options, default=None, owner=0):
    """A task dialog of command links: options [(key, title, detail)]; the chosen key, or None (Cancel).
    default: the key whose button Enter presses."""
    first = next((i for i, o in enumerate(options) if o[0] == default), 0)
    buttons = (BUTTON * len(options))(*[BUTTON(BUTTON_BASE + i, f'{t}\n{d}') for i, (_, t, d) in enumerate(options)])

    def callback(hwnd, note, wparam, lparam, data):
        if note == TDN_CREATED:
            _centre_on_top(hwnd, owner)
        return 0

    keep = CALLBACK(callback)
    config = CONFIG(cbSize=ctypes.sizeof(CONFIG), dwFlags=TDF_USE_COMMAND_LINKS | TDF_ALLOW_DIALOG_CANCELLATION,
                    dwCommonButtons=TDCBF_CANCEL_BUTTON, pszWindowTitle=window, pszMainInstruction=instruction,
                    pszContent=content, cButtons=len(options), pButtons=buttons, nDefaultButton=BUTTON_BASE + first,
                    pfCallback=keep, cxWidth=300)
    pressed = _show(config)
    return options[pressed - BUTTON_BASE][0] if BUTTON_BASE <= pressed < BUTTON_BASE + len(options) else None


def choose_mode(names, owner=0, pedal=False, default='original', window='HX Edit NAM Import'):
    """The chosen MODES key for these captures, or None (cancelled). pedal: the firmware has NAM Pedal;
    default: the MODES key whose button Enter presses."""
    shown = ', '.join(names[:3]) + (f' and {len(names) - 3} more' if len(names) > 3 else '')
    return ask(window, 'How should these captures go to the Helix?',
               f'{shown}\n\nA lighter copy is trained against the capture\'s own bigger model, '
               'so it needs a TONE3000 A2 file or another full-size capture.',
               [(k, t, d) for k, _, _, t, d in modes(pedal)], default, owner)


# The firmware on the unit, asked when HX Edit NAM starts: which NAM blocks HX Edit's catalog has and which
# import modes make sense. (key, title, detail); the key is settings.json's "namFirmware".
FIRMWARES = (
    ('pedal', 'NAM5_PEDAL · five NAM blocks',
     'helix-lt-nam lt_port.py --pedal: NAM A2 Lite, NAM Lite 20 / 16 / 14 and NAM Pedal.'),
    ('layers', 'NAM4_LAYERS · four NAM blocks',
     'helix-lt-nam lt_port.py --layers: NAM A2 Lite and NAM Lite 20 / 16 / 14.'),
    ('plain', 'One NAM block',
     'NAM2_PERSONAL, the Helix Floor build or HX Stomp / HX Effects: NAM A2 Lite only.'),
)


# The TONE3000 tiles in Amp > NAM, one per NAM block a TONE3000 profile can go to: each a copy of its block's
# row, so HX Edit greys it exactly when that block does not fit (its DSP); (label, block, MODES keys).
TILES = {
    'plain': (('TONE3000', 'NAM A2 Lite', ('original',)),),
    'layers': (('TONE3000 A2', 'NAM A2 Lite', ('original',)),
               ('TONE3000 Lite 16', 'NAM Lite 16', ('16-quick', '16-standard')),
               ('TONE3000 Lite 14', 'NAM Lite 14', ('14-standard',))),
}
TILES['pedal'] = TILES['layers'] + (('TONE3000 Pedal', 'NAM Pedal', ('pedal',)),)


def tiles(firmware):
    """The TONE3000 tiles for a firmware key (FIRMWARES), as tone_view.js takes them."""
    return [dict(label=label, block=block, modes=list(keys)) for label, block, keys in TILES[firmware]]


def choose_tile_mode(name, keys, owner=0, default=None, window='HX Edit NAM · TONE3000'):
    """The MODES key for a profile going to a tile's block: its only mode, or the user's pick among keys."""
    if len(keys) == 1:
        return keys[0]
    return ask(window, 'How should this profile go to the Helix?', name,
               [(k, t, d) for k, _, _, t, d in MODES if k in keys], default or keys[0], owner)


def choose_firmware(current='plain'):
    """The firmware key the user picks (Enter: current), or None (Cancel: keep the current one)."""
    return ask('HX Edit NAM', 'Which NAM firmware is on your Helix?',
               'HX Edit NAM shows the NAM blocks and the import modes of that firmware. A block the unit '
               'does not have would not load, so pick what is installed.', list(FIRMWARES), current)


def light_name(source, layers):
    """A trained copy's file name: '<name> L16.nam', or for a pedal model '<name> PED.wav' (IR names fit 31)."""
    suffix = f' L{layers}' if layers else ' PED'
    return source.stem[:IR_NAME_LIMIT - len(suffix)].rstrip(' .') + suffix + ('.nam' if layers else '.wav')


class PedalJob(nam_light.LightJob):
    """helix-lt-nam's tools/nam_pedal.py on one capture in a child process: a pedal model .wav (a User IR)
    for the NAM Pedal block, with LightJob's events (step, steps; then done, or error)."""
    STEP = re.compile(r'step (\d+)/(\d+): weighted error ([\d.]+)')

    def run(self):
        state, last = self.state, ''
        try:
            if not self.refine.is_file():
                raise RuntimeError('This build has no helix-lt-nam tools/nam_pedal.py.')
            python = self.find_python(self.refine)
            if python is None:
                raise RuntimeError(nam_light.MISSING)
            if self.cancelled:
                raise RuntimeError('Cancelled.')
            self.process = subprocess.Popen(
                [python, '-u', '-B', '-X', 'utf8', str(self.refine), str(self.capture), '-o', str(self.output), '-v'],
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, encoding='utf-8', errors='replace',
                creationflags=nam_light.NO_WINDOW)
            with self.process.stdout as output:
                for line in output:
                    if line.strip():
                        last = line.strip()
                    if m := self.STEP.search(line):
                        state.update(step=int(m[1]), steps=int(m[2]), best=float(m[3]))
                        self.on_event(dict(state))
            if self.process.wait() or not self.output.is_file():
                raise RuntimeError('Cancelled.' if self.cancelled else
                                   last.replace(str(self.capture), self.capture.name) or 'Conversion failed.')
            state['done'] = True
        except (OSError, RuntimeError) as error:
            state['error'] = str(error)
            state['cancelled'] = self.cancelled
        self.on_event(dict(state))


def train(captures, key, workspace, refine, owner=0, **options):
    """_train with a cache in CACHE/<mode>: a capture converted for this mode before (the same bytes, from
    the import, TONE3000 or the library) is copied from there instead of trained again; new results are
    kept there. The copies carry light_name, so HX Edit imports them under the usual IR name."""
    # ponytail: no eviction (a pedal .wav ~8 KB, a lighter .nam ~0.3 MB); a new converter does not
    # invalidate it either: delete the Converted folder after upgrading nam_pedal / nam_refine.
    layers = next(m[1] for m in MODES if m[0] == key)
    captures = [Path(c) for c in captures]
    stored = [CACHE / key / (sha256(c.read_bytes()).hexdigest()[:32] + ('.nam' if layers else '.wav'))
              for c in captures]
    missing = [c for c, s in zip(captures, stored) if not s.is_file()]
    made = iter(())
    if missing:
        made = _train(missing, key, workspace, refine, owner, **options)
        if made is None:
            return None
        made = iter(made)
    folder = Path(tempfile.mkdtemp(prefix='import-cached-', dir=workspace))
    outputs = []
    for capture, cached in zip(captures, stored):
        if cached.is_file():
            out = folder / light_name(capture, layers)
            shutil.copyfile(cached, out)
        else:
            out = Path(next(made))
            try:
                cached.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(out, cached.with_suffix('.part'))
                cached.with_suffix('.part').replace(cached)
            except OSError as error:            # no cache: the next time trains again
                print(f'Could not cache {out.name}: {error}')
        outputs.append(str(out))
    return outputs


def _train(captures, key, workspace, refine, owner=0, *, job=nam_light.LightJob, show=True):
    """The captures trained for MODES[key], as new paths in the workspace (the same order): .nam copies,
    or for 'pedal' pedal model .wav files. None when cancelled; RuntimeError when training fails."""
    _, layers, quality, title, _ = next(m for m in MODES if m[0] == key)
    if key == 'pedal':                      # the pedal converter sits next to nam_refine.py
        refine, steps = Path(refine).with_name('nam_pedal.py'), 400
        job = PedalJob if job is nam_light.LightJob else job
    else:
        steps = nam_light.STEPS[quality]
    folder = Path(tempfile.mkdtemp(prefix='import-light-', dir=workspace))
    captures = [Path(c) for c in captures]
    outputs = [folder / light_name(c, layers) for c in captures]
    state = {'index': 0, 'job': None, 'event': {}, 'failed': None, 'cancelled': False, 'finished': False}
    lock = threading.Lock()

    def start(index):
        state['index'], state['event'] = index, {}
        state['job'] = job(refine, captures[index], outputs[index], layers, steps, on_event)

    def on_event(event):
        with lock:
            state['event'] = event
            if event.get('error'):
                state['failed'] = event['error']
                state['finished'] = True
            elif event.get('done'):
                if state['index'] + 1 < len(captures):
                    start(state['index'] + 1)
                else:
                    state['finished'] = True

    start(0)
    if not show:                                   # tests: wait without a dialog
        while True:
            with lock:
                if state['finished']:
                    break
            threading.Event().wait(0.05)
    else:
        def callback(hwnd, note, wparam, lparam, data):
            if note == TDN_CREATED:
                _centre_on_top(hwnd, owner)
                ctypes.windll.user32.SendMessageW(hwnd, TDM_SET_PROGRESS_BAR_RANGE, 0, 1000 << 16)
            elif note == TDN_TIMER:
                with lock:
                    finished, event, index = state['finished'], dict(state['event']), state['index']
                done = (index + (event.get('step', 0) / max(1, event.get('steps', steps)))) / len(captures)
                ctypes.windll.user32.SendMessageW(hwnd, TDM_SET_PROGRESS_BAR_POS, int(1000 * done), 0)
                text = f'{captures[index].stem} ({index + 1} of {len(captures)})\n'
                text += (f'Step {event["step"]} of {event["steps"]}' + (
                    f' · error {100 * event["best"]:.2f}%' if event.get('best') is not None else '')
                    if event.get('step') else 'Preparing the capture\'s bigger model…')
                ctypes.windll.user32.SendMessageW(hwnd, TDM_SET_ELEMENT_TEXT, TDE_CONTENT, ctypes.c_wchar_p(text))
                if finished:
                    ctypes.windll.user32.PostMessageW(hwnd, TDM_CLICK_BUTTON, IDCANCEL, 0)
            elif note == TDN_BUTTON_CLICKED and wparam == IDCANCEL:
                with lock:
                    if not state['finished']:
                        state['cancelled'] = True
                        state['job'].cancel()
            return 0

        keep = CALLBACK(callback)
        config = CONFIG(cbSize=ctypes.sizeof(CONFIG), dwFlags=TDF_SHOW_PROGRESS_BAR | TDF_CALLBACK_TIMER,
                        dwCommonButtons=TDCBF_CANCEL_BUTTON, pszWindowTitle='HX Edit NAM Import',
                        pszMainInstruction=f'Training: {title}', pszContent='Preparing…',
                        pfCallback=keep, cxWidth=300)
        _show(config)
        if state['cancelled']:
            state['job'].thread.join(timeout=10)
            return None
    if state['failed']:
        raise RuntimeError(state['failed'])
    return [str(o) for o in outputs]
