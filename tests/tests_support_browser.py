"""Shared stand-ins for driving TonePreview's worker without HX Edit (as test_navigation_runtime does)."""
from pathlib import Path
from queue import Queue
from threading import Event
from unittest.mock import Mock

from tone_preview import TonePreview

ROOT = Path(__file__).resolve().parents[1] / 'src'


class Browser:
    def __init__(self, actions=()):
        self.actions = Queue()
        for action in actions:
            self.actions.put(action)
        self.sent = []

    def next_action(self):
        if self.actions.empty():
            return None
        return self.actions.get_nowait()

    def send(self, kind, **value):
        self.sent.append(dict(type=kind, **value))

    def is_set(self):
        return False


def make_preview(browser, **kwargs):
    p = TonePreview(Mock(), str(ROOT), 'unused-converter', Event(), Mock(), **kwargs)
    p.navigation = True
    p.session = 5
    p.browser = browser
    p.tile = {'block': 'NAM A2 Lite', 'modes': ['original']}
    p.navigation_state = dict(session=5, version=1, bound=False)
    return p
