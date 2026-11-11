"""Private pipe bridge for the local library table. No device or network APIs."""
import json
from pathlib import Path
from queue import Queue, Empty
import re
import subprocess
from threading import Thread, Event, Lock
from library_catalog import GEAR


class LibraryTableBridge:
    def __init__(self, exe, script, detached, on_classify=None):
        self.exe, self.script, self.detached = Path(exe), script, detached
        self.on_classify = on_classify
        self.queue = Queue()
        self.process = None
        self.lock = Lock()
        self.closed = Event()
        self.worker = Thread(target=self.run, daemon=True)
        self.worker.start()

    def receive(self, payload):
        self.queue.put(payload)

    def send(self, payload):
        if not self.process or self.process.poll() is not None:
            return False
        try:
            with self.lock:
                self.process.stdin.write(json.dumps(payload) + '\n')
                self.process.stdin.flush()
            return True
        except (OSError, ValueError):
            return False

    def handle_action(self, message):
        if message.get('type') == 'action' and message.get('action') == 'classify':
            callback = getattr(self, 'on_classify', None)
            identity, revision, gear = (message.get(k) for k in ('contentId', 'revision', 'gear'))
            if (callback is None or self.detached.is_set()
                    or not isinstance(identity, str) or not re.fullmatch('[0-9a-f]{64}', identity)
                    or not isinstance(revision, str) or not re.fullmatch('[0-9a-f]{64}', revision)
                    or 'gear' not in message
                    or (gear is not None and (not isinstance(gear, str) or gear not in GEAR))):
                self.send({'type': 'classificationResult', 'error': 'Invalid or unavailable classification request.'})
                return
            try:
                callback(identity, gear, revision)
                self.send({'type': 'classificationResult', 'error': ''})
            except Exception as error:
                self.send({'type': 'classificationResult', 'error': str(error)})
            return  # Catalog only: never forwards a device action.
        if (message.get('type') == 'action' and message.get('action') == 'resize'
                and type(message.get('width')) is int and 300 <= message['width'] <= 600
                and not self.detached.is_set()):
            try:
                self.script.post({'type':'libraryTableAction', 'payload':{
                    'action':'resize', 'width':message['width']}})
            except Exception:
                self.send({'type':'resizeResult', 'error':'Editor connection ended.'})
            return
        if (message.get('type') == 'action' and message.get('action') in ('load', 'select')
                and isinstance(message.get('contentId'), str)
                and re.fullmatch('[0-9a-f]{64}', message['contentId'])
                and not self.detached.is_set()):
            try:
                self.script.post({'type':'libraryTableAction', 'payload':{
                    'action':message['action'], 'contentId':message['contentId']}})
            except Exception:
                # The native editor may detach between the check and post.
                self.send({'type':'result', 'error':'Editor connection ended. Nothing was requested.'})

    def read_actions(self):
        try:
            for line in self.process.stdout:
                try:
                    self.handle_action(json.loads(line))
                except (ValueError, TypeError, AttributeError):
                    continue
                if self.detached.is_set():
                    break
        finally:
            self.closed.set()

    def run(self):
        try:
            while not self.detached.is_set():
                try:
                    event = self.queue.get(timeout=0.25)
                except Empty:
                    continue
                if event.get('type') == 'libraryTable':
                    state = event.get('state', {})
                    if self.process is None and state.get('visible'):
                        parent = event.get('window')
                        if not isinstance(parent, int) or parent <= 0:
                            continue
                        self.process = subprocess.Popen([str(self.exe), str(parent)],
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            encoding='utf-8', errors='replace',
                            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
                        Thread(target=self.read_actions, daemon=True).start()
                    self.send({'type':'state', 'state':state})
                elif event.get('type') == 'libraryResult':
                    self.send({'type':'result', 'savedSlot':event.get('savedSlot'),
                               'contentId':event.get('contentId'),
                               'slotEpoch':event.get('slotEpoch'),
                               'error':event.get('error', ''), 'reused':event.get('reused', False)})
                elif event.get('type') == 'libraryResizeResult':
                    self.send({'type':'resizeResult', 'error':event.get('error', ''),
                               'width':event.get('width')})
        finally:
            self.send({'type':'close'})
            if self.process:
                try:
                    self.process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    self.process.terminate()  # UI-only child; never owns device transfers.
                    self.process.wait(timeout=3)
            self.closed.set()
