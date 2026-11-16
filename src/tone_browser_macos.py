"""Adapt the catalog commands to the embedded WebKit pane."""

from queue import Queue
from threading import Event

from tone_browser import ToneBrowser


class MacToneBrowser(ToneBrowser):
    startup_error = 'The TONE3000 panel did not start. Reopen it to try again.'

    def __init__(self, script, session, window, bounds, detached):
        self.script = script
        self.session = session
        self.window = window
        self.bounds = bounds
        self.detached = detached
        self.closed = Event()
        self.ready = Event()
        self.actions = Queue()
        self.error = None

    def send(self, kind, **data):
        if self.is_set():
            return False
        try:
            self.script.post({'type': 'toneBrowser', 'payload': dict(type=kind, session=self.session, **data)})
            return True
        except Exception:
            self.closed.set()
            return False

    def wait_ready(self):
        self.send('open', window=self.window, bounds=self.bounds)
        return super().wait_ready()

    def receive(self, event):
        if event['session'] != self.session:
            return
        if event['type'] == 'ready':
            self.ready.set()
        elif event['type'] == 'action':
            self.actions.put(event['value'])
        elif event['type'] == 'error':
            self.error = event['value']
            self.ready.set()
            self.actions.put((None, self.error))
            self.actions.put('finish')

    def close(self):
        self.send('close')
        self.closed.set()
