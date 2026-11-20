"""The DSP meter beside the TONE3000 button: NamLibrary.exe --dsp-meter, a child window of HX Edit.

dsp_meter.js reports each DSP's load ({'type': 'dspLoad'}), nav_banner.js where the meter goes
({'type': 'dspMeterLayout'}, window client pixels). This passes both to the helper as JSON lines.
The helper only draws: it has no device access, and closing its input ends it.
"""
import ctypes
import json
from pathlib import Path
from queue import Empty, Queue
import subprocess
from threading import Event, Thread

# After the first layout HX Edit is still placing its editor view (about 60 px off when the Floor
# connects: Floor PC, November 16) and sends no geometry message once it has, so nav_banner.js is
# asked for a fresh layout a few times (seconds after the helper starts). Its own registered message.
RELAYOUT_AFTER = (1.5, 4.0, 8.0, 15.0)
LAYOUT_WAKE = 'HX NAM banner layout'


def post_layout_wake(window):
    try:
        user = ctypes.windll.user32
        message = user.RegisterWindowMessageW(LAYOUT_WAKE)
        if message:
            user.PostMessageW(ctypes.c_void_p(window), message, 0, 0)
    except (AttributeError, OSError):
        pass

from nam_sidebar import prepare_sidebar


class DspMeter:
    """on_choice(name): a choice from the HX Unbound menu button ('level', 'look', 'library'); the button is
    shown only when given."""

    def __init__(self, *, exe=None, process_factory=subprocess.Popen, trace=None, on_choice=None,
                 relayout=post_layout_wake, relayout_after=RELAYOUT_AFTER, icons=None):
        self.exe = exe
        self.relayout, self.relayout_after = relayout, relayout_after
        self.process_factory = process_factory
        self.trace = trace or (lambda value: None)
        self.on_choice = on_choice
        self.process = None
        self.layout = None
        self.dsp = [None, None]
        self.notice = None
        self.ir_dots = None       # the HX Unbound menu's "Mark NAM slots in the IR list" tick (set_ir_dots)
        self.ghost = None         # {name, icon, over} while a model from the block picker is dragged
        self.ghost_shown = False
        self.icons = Path(icons) if icons else None   # HX Edit's res/icons_models
        self.queue = Queue()
        self.closed = Event()
        self.worker = Thread(target=self._run, daemon=True)
        self.worker.start()

    def receive(self, value):
        kind = value.get('type')
        if kind == 'dspMeterLayout':
            self.queue.put(('layout', dict(value)))
        elif kind == 'dspLoad':
            self.queue.put(('dsp', list(value.get('dsp') or [None, None])[:2]))
        elif kind == 'pickerGhost':
            # A model dragged from HX Edit's block picker: its picture and name beside the pointer.
            self.queue.put(('ghost', dict(value)))
        elif kind == 'dropHint':
            # What a drop where the pointer is would do (place / replace / insert / none).
            self.queue.put(('ghost', {'hint': value.get('hint')}))
        elif kind == 'dspNotice':
            # A short notice shown in place of the meter for a few seconds (the helper times it).
            self.queue.put(('notice', str(value.get('text') or '')[:120]))
        elif kind == 'dspMeter':
            self.trace({'type': 'dspMeter', 'event': value.get('event'), 'error': value.get('error', '')})
        else:
            return False
        return True

    def _start(self, parent):
        exe = self.exe or prepare_sidebar()
        self.process = self.process_factory([str(exe), '--dsp-meter', str(parent)], stdin=subprocess.PIPE,
                                            stdout=subprocess.PIPE if self.on_choice else None,
                                            encoding='utf-8', errors='replace',
                                            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        if self.on_choice and getattr(self.process, 'stdout', None) is not None:
            Thread(target=self._clicks, args=(self.process.stdout,), daemon=True).start()

    CHOICES = ('level', 'look', 'library', 'irDots')

    def _clicks(self, output):
        # The helper writes one line per choice from the HX Unbound menu.
        try:
            for line in output:
                if line.strip() in self.CHOICES:
                    try:
                        self.on_choice(line.strip())
                    except Exception as error:     # the meter keeps drawing whatever the page does
                        self.trace({'type': 'dspMeter', 'event': 'choice-failed', 'error': str(error)})
        except (OSError, ValueError):
            pass

    def _send(self):
        if self.process is None or self.process.poll() is not None or self.layout is None:
            return
        state = {key: self.layout.get(key) for key in ('visible', 'x', 'y', 'w', 'h', 'span')}
        state['dsp'] = self.dsp
        if self.on_choice is not None:
            state['unbound'] = True
        if self.notice:
            state['notice'], self.notice = self.notice, None
        if self.ir_dots is not None:
            state['irDots'] = self.ir_dots
        if self.ghost is not None or self.ghost_shown:
            state['ghost'] = self.ghost             # shown, or one line to hide it
            self.ghost_shown = self.ghost is not None
        try:
            self.process.stdin.write(json.dumps(state) + '\n')
            self.process.stdin.flush()
        except (OSError, ValueError):
            self.process = None   # a closed helper is not restarted; the editor carries on without it

    def _run(self):
        started = False
        while not self.closed.is_set():
            try:
                kind, value = self.queue.get(timeout=.25)
            except Empty:
                continue
            if kind == 'close':
                break
            if kind == 'layout':
                self.layout = value
                parent = value.get('window')
                # A span is enough: a window too narrow now may be widened later (the helper follows).
                if not started and (value.get('visible') or value.get('span')) and type(parent) is int and parent > 0:
                    started = True
                    Thread(target=self._relayouts, args=(parent,), daemon=True).start()
                    try:
                        self._start(parent)
                    except (OSError, subprocess.SubprocessError) as error:
                        self.trace({'type': 'dspMeter', 'event': 'helper-failed', 'error': str(error)})
            elif kind == 'dsp':
                self.dsp = value
            elif kind == 'notice':
                self.notice = value
            elif kind == 'irDots':
                self.ir_dots = value
            elif kind == 'ghost':
                self.ghost = self._ghost(value)
            self._send()
        if self.process is not None:
            try:
                self.process.stdin.close()
                self.process.wait(timeout=3)
            except (OSError, ValueError, subprocess.TimeoutExpired):
                self.process.terminate()   # presentation only

    def _relayouts(self, window):
        waited = 0.0
        for when in self.relayout_after:
            if self.closed.wait(max(0.0, when - waited)):
                return
            waited = when
            self.relayout(window)

    def _ghost(self, value):
        if value.get('hide'):
            return None
        ghost = dict(self.ghost or {})
        if 'name' in value:
            ghost['name'] = str(value.get('name') or '')[:80]
            image = str(value.get('image') or '')
            # Only a picture file of HX Edit's own folder, by its bare name.
            path = self.icons / image if self.icons and image and Path(image).name == image and image.endswith('.png') else None
            ghost['icon'] = str(path) if path and path.is_file() else ''
        if 'hint' in value:
            if not self.ghost and 'name' not in value:
                return None                  # a hint for a drag this helper does not show
            ghost['hint'] = value.get('hint') if value.get('hint') in ('place', 'replace', 'insert') else 'none'
        ghost.setdefault('hint', 'none')
        return ghost

    def set_ir_dots(self, on):
        self.queue.put(('irDots', bool(on)))

    def close(self):
        self.closed.set()
        self.queue.put(('close', None))
        if self.worker.is_alive():
            self.worker.join(timeout=5)
