"""The DSP meter beside the TONE3000 button: NamLibrary.exe --dsp-meter, a child window of HX Edit.

dsp_meter.js reports each DSP's load ({'type': 'dspLoad'}), nav_banner.js where the meter goes
({'type': 'dspMeterLayout'}, window client pixels). This passes both to the helper as JSON lines.
The helper only draws: it has no device access, and closing its input ends it.
"""
import json
from queue import Empty, Queue
import subprocess
from threading import Event, Thread

from nam_sidebar import prepare_sidebar


class DspMeter:
    def __init__(self, *, exe=None, process_factory=subprocess.Popen, trace=None):
        self.exe = exe
        self.process_factory = process_factory
        self.trace = trace or (lambda value: None)
        self.process = None
        self.layout = None
        self.dsp = [None, None]
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
        elif kind == 'dspMeter':
            self.trace({'type': 'dspMeter', 'event': value.get('event'), 'error': value.get('error', '')})
        else:
            return False
        return True

    def _start(self, parent):
        exe = self.exe or prepare_sidebar()
        self.process = self.process_factory([str(exe), '--dsp-meter', str(parent)], stdin=subprocess.PIPE,
                                            encoding='utf-8', errors='replace',
                                            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))

    def _send(self):
        if self.process is None or self.process.poll() is not None or self.layout is None:
            return
        state = {key: self.layout.get(key) for key in ('visible', 'x', 'y', 'w', 'h')}
        state['dsp'] = self.dsp
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
                if not started and value.get('visible') and type(parent) is int and parent > 0:
                    started = True
                    try:
                        self._start(parent)
                    except (OSError, subprocess.SubprocessError) as error:
                        self.trace({'type': 'dspMeter', 'event': 'helper-failed', 'error': str(error)})
            elif kind == 'dsp':
                self.dsp = value
            self._send()
        if self.process is not None:
            try:
                self.process.stdin.close()
                self.process.wait(timeout=3)
            except (OSError, ValueError, subprocess.TimeoutExpired):
                self.process.terminate()   # presentation only

    def close(self):
        self.closed.set()
        self.queue.put(('close', None))
        if self.worker.is_alive():
            self.worker.join(timeout=5)
