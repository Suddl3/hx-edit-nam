"""TonePreview lifecycle tests; no device, browser process or network required."""
from threading import Event
import unittest
from unittest.mock import Mock

from tone_preview import TonePreview


class TonePreviewLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.entered = {n: Event() for n in (1, 2)}
        self.release = {n: Event() for n in (1, 2)}
        self.notice_entered = Event()
        self.notice_release = Event()
        self.close_entered = Event()
        self.close_release = Event()
        self.close_release.set()
        self.with_notice = True
        self.workers = []
        self.timeouts = []
        self.script = Mock()

        def browser_factory(*_args):
            browser = Mock()
            browser.wait_ready.return_value = True
            return browser

        self.preview = TonePreview(self.script, None, None, Event(), self.notice,
                                   browser_factory=browser_factory, client_id='t3k_pub_test')
        self.preview.browse = self.browse
        self.preview.command = Mock(side_effect=self.command)
        self.addCleanup(self.release_workers)

    def wait(self, event, label):
        if not event.wait(5):
            self.timeouts.append(label)

    def browse(self, browser, session, client_id):
        self.entered[session].set()
        self.wait(self.release[session], 'browse')

    def command(self, session, action):
        if session == 1:
            self.close_entered.set()
            self.wait(self.close_release, 'close')
            if self.with_notice:
                return {'notice': 'The audition profile was left on Helix.'}
        return {}

    def notice(self, text, flags):
        self.notice_entered.set()
        self.wait(self.notice_release, 'notice')

    def start(self, session):
        self.preview.receive({'type': 'toneStart', 'session': session,
                              'window': 1, 'bounds': {}})
        worker = self.preview.thread
        if worker is not None and worker not in self.workers:
            self.workers.append(worker)
        return worker

    def release_workers(self):
        for event in (*self.release.values(), self.notice_release, self.close_release):
            event.set()
        for worker in self.workers:
            worker.join(timeout=2)
            self.assertFalse(worker.is_alive(), 'worker did not terminate')
        self.assertEqual(self.timeouts, [])

    def assert_start_rejected(self):
        self.start(2)
        self.script.post.assert_called_once_with({'type': 'toneCommand', 'payload': {
            'session': 2, 'action': 'close', 'id': 0}})
        self.assertEqual(self.preview.session, 1)
        self.assertFalse(self.entered[2].is_set())
        self.script.post.reset_mock()

    def test_reopen_while_completed_session_notice_is_still_open(self):
        first = self.start(1)
        self.assertTrue(self.entered[1].wait(2))
        self.release[1].set()
        self.assertTrue(self.notice_entered.wait(2))
        self.preview.command.assert_called_once_with(1, 'close')

        # The notice is deliberately still blocked when a new toneStart arrives.
        second = self.start(2)
        self.script.post.assert_not_called()
        self.assertTrue(self.entered[2].wait(2))
        self.assertIsNot(first, second)
        self.assertEqual(self.preview.session, 2)
        browser = self.preview.browser

        # Dismissing the old notice must not reset the newer worker/browser.
        self.notice_release.set()
        first.join(timeout=2)
        self.assertFalse(first.is_alive())
        self.assertIs(self.preview.thread, second)
        self.assertIs(self.preview.browser, browser)
        self.assertEqual(self.preview.session, 2)

    def test_active_audition_and_unfinished_cleanup_still_reject_start(self):
        self.with_notice = False
        self.close_release.clear()
        first = self.start(1)
        self.assertTrue(self.entered[1].wait(2))
        self.assert_start_rejected()
        self.release[1].set()
        self.assertTrue(self.close_entered.wait(2))
        self.assert_start_rejected()
        self.close_release.set()
        first.join(timeout=2)
        self.assertFalse(first.is_alive())
        self.start(2)
        self.assertTrue(self.entered[2].wait(2))
        self.script.post.assert_not_called()

    def test_normal_close_without_notice_can_reopen(self):
        self.with_notice = False
        first = self.start(1)
        self.assertTrue(self.entered[1].wait(2))
        self.release[1].set()
        first.join(timeout=2)
        self.assertFalse(first.is_alive())
        self.assertFalse(self.notice_entered.is_set())
        second = self.start(2)
        self.assertTrue(self.entered[2].wait(2))
        self.assertIsNot(first, second)
        self.script.post.assert_not_called()
