"""Rich local NAM view. Reuses Library and the editor's existing action/session owner.

This bridge has no network, capture conversion or USB API. The native editor
publishes layout only; the same Library used by the expanded page supplies rows.
"""
from hashlib import sha256
import json
import os
from pathlib import Path
from queue import Empty, Full, Queue
import re
import subprocess
from threading import Event, Lock, Thread
from time import monotonic

from capture_icons import icon_for, icon_path
from nam_library import GEARS


GROUPS = {'amp': 'amp-only', 'amp-cab': 'amp+cab', 'pedal': 'pedals',
          'pedal-amp': 'pedal+amp', 'amp-pedal-cab': 'pedal+amp+cab', '': 'unidentified'}


def prepare_sidebar(destination=None):
    """Explicit, bounded helper build. Never launches HX Edit or the child pane."""
    source = Path(__file__).with_name('NamLibrary.cs')
    output = Path(destination or source.with_suffix('.exe'))
    digest = sha256(source.read_bytes()).hexdigest()
    stamp = output.with_suffix(output.suffix + '.sha256')
    if output.is_file() and stamp.is_file() and stamp.read_text(encoding='ascii').strip() == digest:
        return output
    compiler = Path(os.environ.get('WINDIR', r'C:\Windows')) / 'Microsoft.NET' / 'Framework' / 'v4.0.30319' / 'csc.exe'
    if not compiler.is_file():
        raise OSError('The .NET Framework compiler needed for the NAM sidebar is unavailable')
    output.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run([str(compiler), '/nologo', '/target:winexe', '/platform:x86', '/codepage:65001',
                    '/r:System.Windows.Forms.dll', '/r:System.Drawing.dll', '/r:System.Web.Extensions.dll',
                    '/out:' + str(output), str(source)], check=True, timeout=45,
                   stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                   creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    stamp.write_text(digest + '\n', encoding='ascii')
    return output


def classification_revision(entry):
    fields = {key: entry.get(key) for key in ('gear', 'sourceGear', 'gearOverride')}
    return sha256(json.dumps(fields, sort_keys=True).encode()).hexdigest()


def sidebar_rows(library, *, can_load=False):
    """Translate, never persist, the collection's shared metadata and current proof."""
    rows = []
    for identity, entry in library.entries().items():
        try:
            source, _ = library._source(identity, entry)
            exists = source.is_file()
        except (ValueError, OSError):
            exists = False
        state, slots = entry.get('slotState', 'unverified'), entry.get('irSlots', [])
        verified = slots and state in ('verified', 'partial')
        gear = entry.get('gear', '')
        device_state = 'checking' if state == 'refreshing' else 'verified' if verified else 'local-only' if state == 'verified' else 'unverified'
        pending = entry.get('pendingSlots', [])
        detail = ('Capture content verified on this connection' if verified else
                  'Previous slot numbers; verification is refreshing' if state == 'refreshing' else
                  'Not found in the verified permanent slots' if state == 'verified' else
                  'Installation has not been verified on this connection')
        rows.append(dict(contentId=identity, title=entry.get('name', ''), aliases=[entry.get('irName', '')],
                         category=GROUPS.get(gear, gear), categoryLabel=GEARS.get(gear, 'Unsorted'),
                         categoryOverride=entry.get('gearOverride', ''), categoryRevision=classification_revision(entry),
                         categoryEvidence=[dict(source='Retained source metadata', gear=entry.get('sourceGear', gear))],
                         addedAt=entry.get('added', ''), dateEvidence=entry.get('dateEvidence', {}),
                         deviceStatus=device_state, displaySlots=slots if state == 'refreshing' else [],
                         verifiedSlots=slots if verified else [], pendingSlots=pending,
                         fileStatus='available' if exists else 'missing',
                         fileDetail='Kept on this PC' if exists else 'The kept capture file is unavailable',
                         icon=icon_path('', ident=icon_for(entry)),
                         locationDetail=detail, canLoad=bool(can_load and (exists or verified) and state != 'refreshing' and not pending)))
    return rows


class NamSidebar:
    OPERATION_TIMEOUT = 60  # seconds without any status before a Load/Use request is given up

    def __init__(self, script, library, detached, *, exe=None, action_handler=None, process_factory=subprocess.Popen,
                 trace=None):
        self.script, self.library, self.detached = script, library, detached
        self.trace = trace
        self.operation_started = None
        self.exe = Path(exe or Path(__file__).with_name('NamLibrary.exe'))
        self.action_handler = action_handler
        self.process_factory = process_factory
        self.process = None
        self.queue = Queue()
        self.outbound = Queue(maxsize=64)
        self.closed = Event()
        self.write_lock = Lock()
        self.layout = {'visible': False}
        self.operation_busy = False
        self.last_state = None
        self.write_started = None
        self.helper_ready = False
        self.helper_started = None
        self.unsubscribe = library.subscribe(self.refresh)
        self.worker = Thread(target=self._run, daemon=True)
        self.worker.start()

    def receive(self, value):
        kind = value.get('type')
        if kind not in ('namSidebarLayout', 'namSidebarResizeResult', 'namSidebarResult'):
            return False
        self.queue.put(value)
        return True

    def refresh(self):
        if not self.closed.is_set():
            self.queue.put({'type': 'refresh'})

    def status(self, value):
        if isinstance(value, dict) and not self.closed.is_set():
            self.queue.put({'type': 'status', 'value': dict(value)})

    def send(self, message):
        if self.process is None or self.process.poll() is not None:
            return False
        try:
            self.outbound.put_nowait(json.dumps(message, ensure_ascii=False) + '\n')
            return True
        except (Full, ValueError):
            self.queue.put({'type': 'helperStopped'})
            return False

    def _write_messages(self):
        while self.process is not None and self.process.poll() is None:
            try:
                line = self.outbound.get(timeout=.25)
            except Empty:
                continue
            try:
                self.write_started = monotonic()
                self.process.stdin.write(line)
                self.process.stdin.flush()
            except (OSError, ValueError):
                self.queue.put({'type': 'helperStopped'})
                return
            finally:
                self.write_started = None

    def _state(self):
        state = dict(self.layout)
        # The native side once sent 1 for "a NAM block is selected"; `is True` then disabled every
        # Load/Use on Floor. Normalise the flag here as well as at its source.
        state['targetReady'] = state.get('targetReady') in (True, 1)
        state.update(rows=sidebar_rows(self.library, can_load=state['targetReady']),
                     view=self.library.view_state(), busy=self.operation_busy or bool(state.get('busy')))
        return state

    def _publish(self):
        if not self.process:
            return
        state = self._state()
        if state != self.last_state:
            self.send({'type': 'state', 'state': state})
            self.last_state = state

    def _handle_action(self, message):
        if message.get('type') != 'action' or self.detached.is_set() or self.closed.is_set():
            return
        action = message.get('action')
        if action == 'view':
            self.library.update_view(message.get('view'))
            return
        if action == 'resize':
            width = message.get('width')
            if type(width) is int and 300 <= width <= 600:
                self.script.post({'type': 'namSidebarAction', 'payload': {'action': 'resize', 'width': width}})
            return
        if action == 'expand':
            self.script.post({'type': 'namSidebarAction', 'payload': {'action': 'expand'}})
            return
        identity = message.get('contentId')
        if not isinstance(identity, str) or not re.fullmatch('[a-f0-9]{64}', identity):
            return
        if action == 'classify':
            entry = self.library.entries().get(identity)
            try:
                if entry is None or message.get('revision') != classification_revision(entry):
                    raise ValueError('This capture changed. Select its current row and try again.')
                gear = message.get('gear')
                if gear is None:
                    self.library.reset_gear(identity)
                elif gear in GEARS:
                    self.library.update(identity, gear=gear)
                else:
                    raise ValueError('Unknown capture type')
                self.send({'type': 'classificationResult', 'error': ''})
            except (OSError, ValueError, KeyError) as error:
                self.send({'type': 'classificationResult', 'error': str(error)})
            return
        if action not in ('load', 'select'):
            return
        # The helper shows "Checking selected NAM block…" until it gets a result, so every
        # request is answered. A silent return left the button waiting with no explanation.
        if self.operation_busy:
            self._trace(action, 'rejected-busy')
            self.send({'type': 'result', 'error': 'The previous library action is still running. Try again in a moment.'})
            return
        if identity not in self.library.entries() or not self.layout.get('targetReady') or self.action_handler is None:
            self._trace(action, 'rejected-no-target' if identity in self.library.entries() else 'rejected-unknown-capture')
            self.send({'type': 'result', 'error': 'Select a compatible NAM block in HX Edit first.'})
            return
        self.library.update_view({'selected': identity})
        self.operation_busy = True
        self.operation_started = monotonic()
        try:
            self.action_handler('use' if action == 'select' else 'load', identity)
            self._trace(action, 'sent')
        except Exception as error:
            self.operation_busy = False
            self._trace(action, 'failed')
            self.send({'type': 'result', 'error': str(error)})

    def _trace(self, action, outcome):
        """Structural diagnostics only: the action and what became of it, never a capture name."""
        if self.trace:
            try:
                self.trace({'type': 'namSidebarAction', 'action': action, 'outcome': outcome,
                            'targetReady': self.layout.get('targetReady') in (True, 1),
                            'busy': bool(self.layout.get('busy'))})
            except Exception:
                pass

    def _read_actions(self):
        try:
            for line in self.process.stdout:
                if self.closed.is_set() or self.detached.is_set():
                    break
                try:
                    value = json.loads(line)
                    if isinstance(value, dict):
                        self.queue.put({'type': 'helperReady'} if value.get('type') == 'ready' else {'type': 'action', 'value': value})
                except (ValueError, TypeError):
                    continue
        except (OSError, ValueError):
            pass
        finally:
            self.queue.put({'type': 'helperStopped'})

    def _run(self):
        try:
            while not self.closed.is_set() and not self.detached.is_set():
                if ((self.write_started is not None and monotonic() - self.write_started > 3) or
                        (self.helper_started is not None and not self.helper_ready and monotonic() - self.helper_started > 10)):
                    self.script.post({'type': 'namSidebarReady', 'payload': {'ready': False}})
                    break
                started = self.operation_started
                if self.operation_busy and started is not None and monotonic() - started > self.OPERATION_TIMEOUT:
                    # HX Edit never answered this request. Do not leave Load/Use disabled for the
                    # rest of the session; nothing is retried.
                    self.operation_busy = False
                    self._trace('load-or-use', 'timed-out')
                    self.send({'type': 'result', 'error': 'HX Edit did not answer. Nothing was retried; check the selected NAM block and try again.'})
                try:
                    event = self.queue.get(timeout=.25)
                except Empty:
                    continue
                kind = event['type']
                if kind == 'namSidebarLayout':
                    self.layout = dict(event.get('state', {}))
                    if self.process is None and self.layout.get('visible'):
                        parent = event.get('window')
                        if type(parent) is not int or parent <= 0:
                            continue
                        self.process = self.process_factory([str(self.exe), str(parent)], stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE, encoding='utf-8', errors='replace',
                            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
                        self.helper_started = monotonic()
                        Thread(target=self._write_messages, daemon=True).start()
                        Thread(target=self._read_actions, daemon=True).start()
                elif kind == 'helperReady':
                    self.helper_ready = True
                    self.last_state = None
                    self.script.post({'type': 'namSidebarReady', 'payload': {'ready': True}})
                elif kind == 'namSidebarResizeResult':
                    self.send({'type': 'resizeResult', 'error': event.get('error', ''), 'width': event.get('width')})
                elif kind == 'action':
                    self._handle_action(event['value'])
                elif kind in ('status', 'namSidebarResult'):
                    value = event.get('value', event)
                    self.operation_busy = bool(value.get('busy'))
                    self.operation_started = monotonic() if self.operation_busy else None
                    self.send({'type': 'workflowStatus', 'text': value.get('text', value.get('error', '')),
                               'busy': self.operation_busy})
                elif kind == 'helperStopped':
                    self.script.post({'type': 'namSidebarReady', 'payload': {'ready': False}})
                    break  # Do not repeatedly relaunch a broken helper.
                self._publish()
        except (OSError, ValueError):
            try:
                self.script.post({'type': 'namSidebarReady', 'payload': {'ready': False}})
            except Exception:
                pass
        finally:
            self.unsubscribe()
            self.send({'type': 'close'})
            if self.process:
                try:
                    self.process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    self.process.terminate()  # Presentation child has no device access.
                    self.process.wait(timeout=3)
            self.closed.set()

    def close(self):
        self.closed.set()
        self.queue.put({'type': 'close'})
        if self.worker.is_alive():
            self.worker.join(timeout=7)
