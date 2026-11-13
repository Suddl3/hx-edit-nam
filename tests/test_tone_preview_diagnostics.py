"""Bounded, private audition diagnostics; no browser, device, or network activity."""
import json
from queue import Queue
from threading import Event
from time import monotonic
import unittest
from unittest.mock import Mock, patch

from tone3000 import ToneError
from tone_preview import TonePreview


class TonePreviewDiagnosticsTests(unittest.TestCase):
    def preview(self, callback=None, **kwargs):
        events = []
        preview = TonePreview(Mock(), None, None, Event(), Mock(),
                              trace_callback=callback or events.append, **kwargs)
        self.addCleanup(preview.close_diagnostics)
        return preview, events

    def reply_immediately(self, preview, **result):
        def post(message):
            request = message['payload']
            preview.receive({'type': 'toneReply', 'id': request['id'],
                             'session': request['session'], **result})
        preview.script.post.side_effect = post

    def test_command_correlates_reply_without_logging_sensitive_payload(self):
        preview, events = self.preview()
        self.reply_immediately(preview, slot=128, error='secret-token https://private.example/ C:/private.nam')
        preview.command(7, 'audition', file='C:/private.nam', token='secret-token')
        preview.close_diagnostics()
        self.assertEqual([event['stage'] for event in events], ['queued', 'replied', 'reply-error'])
        self.assertTrue(all(event['session'] == 7 and event['request'] == 1 and
                            event['action'] == 'audition' for event in events))
        self.assertIn('elapsedMs', events[1])
        self.assertFalse(any(value in json.dumps(events) for value in
                             ('secret-token', 'private.example', 'private.nam', 'slot')))

    def test_command_timeout_and_detach_are_distinct(self):
        preview, events = self.preview()
        with patch('tone_preview.monotonic', side_effect=[0, 0, 36, 36]):
            with self.assertRaises(ToneError):
                preview.command(7, 'audition')
        preview.detached.set()
        with self.assertRaises(ToneError):
            preview.command(7, 'audition')
        preview.close_diagnostics()
        self.assertEqual([event['stage'] for event in events], ['queued', 'timeout', 'detached'])
        self.assertEqual(events[1]['elapsedMs'], 36000)

    def test_post_failure_is_recorded_without_changing_the_exception(self):
        preview, events = self.preview()
        error = OSError('secret local path')
        preview.script.post.side_effect = error
        with self.assertRaises(OSError) as caught:
            preview.command(2, 'bind')
        self.assertIs(caught.exception, error)
        preview.close_diagnostics()
        self.assertEqual([event['stage'] for event in events], ['queued', 'failed'])
        self.assertNotIn('secret local path', json.dumps(events))

    def test_helper_fault_stops_wait_and_late_reply_cannot_satisfy_cleanup(self):
        preview, events = self.preview()
        preview.browser = Mock(error=None)

        def first_post(message):
            request = message['payload']
            preview.browser.error = 'The helper stopped responding.'
            preview.receive({'type': 'toneReply', 'id': request['id'], 'session': 7, 'slot': 128})

        preview.script.post.side_effect = first_post
        with self.assertRaisesRegex(ToneError, 'unconfirmed; no command was retried'):
            preview.command(7, 'audition')
        # A repeated action cannot enqueue another native edit while the helper
        # is faulted. Closing the helper still permits the one cleanup command.
        with self.assertRaises(ToneError):
            preview.command(7, 'audition')
        self.assertEqual(preview.script.post.call_count, 1)
        preview.browser = None
        self.reply_immediately(preview, ended=True)
        self.assertEqual(preview.command(7, 'close'),
                         {'type': 'toneReply', 'id': 2, 'session': 7, 'ended': True})
        preview.close_diagnostics()
        self.assertIn('helper-failed', [e['stage'] for e in events])
        self.assertFalse(any(e['stage'] == 'replied' and e.get('request') == 1 for e in events))

    def test_helper_fault_during_reply_delivery_does_not_report_success(self):
        preview, _ = self.preview()
        preview.browser = Mock(error=None)

        def get(**_kwargs):
            preview.browser.error = 'The helper stopped responding.'
            return {'type': 'toneReply', 'id': 1, 'session': 7, 'slot': 128}

        preview.replies = Mock(get=get)
        with self.assertRaisesRegex(ToneError, 'unconfirmed'):
            preview.command(7, 'audition')
        self.assertEqual(preview.script.post.call_count, 1)

    def test_catalog_preparation_and_native_command_have_separate_phases(self):
        preview, events = self.preview()
        self.reply_immediately(preview, slot=128, freeSlots=[1])
        browser = Mock()
        browser.actions = Queue()
        browser.actions.put(((42, 'secret-token'), None))
        browser.next_action.side_effect = lambda: browser.actions.get(timeout=2)
        browser.is_set.return_value = False
        tone = {'id': 42, 'title': 'Private tone', 'gear': 'amp'}
        models = [{'id': 4, 'name': 'Private capture'}]
        with patch('tone_preview.select_tone', return_value=None), \
                patch('tone_preview.get_tone', return_value=(tone, models)), \
                patch('tone_preview.prepare_model', return_value='C:/private.wav'), \
                patch('tone_preview.remember_link'):
            preview.browse(browser, 7, '')
        preview.close_diagnostics()
        stages = [(event['phase'], event['stage']) for event in events]
        for pair in [('selection', 'received'), ('metadata', 'started'), ('metadata', 'completed'),
                     ('prepare', 'started'), ('prepare', 'completed'),
                     ('native-command', 'queued'), ('native-command', 'replied')]:
            self.assertIn(pair, stages)
        self.assertLess(stages.index(('metadata', 'completed')), stages.index(('prepare', 'started')))
        self.assertLess(stages.index(('prepare', 'completed')), stages.index(('native-command', 'queued')))
        self.assertFalse(any(value in json.dumps(events) for value in
                             ('secret-token', 'Private tone', 'Private capture', 'private.wav')))

    def test_prepare_failure_is_preserved_and_redacted(self):
        preview, events = self.preview()
        error = ToneError('secret URL and capture name')
        with self.assertRaises(ToneError) as caught:
            preview._trace_call('prepare', 7, Mock(side_effect=error))
        self.assertIs(caught.exception, error)
        preview.close_diagnostics()
        self.assertEqual([(e['phase'], e['stage']) for e in events],
                         [('prepare', 'started'), ('prepare', 'failed')])
        self.assertNotIn('secret', json.dumps(events))

    def test_browser_lifecycle_is_visible(self):
        browser = Mock()
        browser.wait_ready.return_value = True
        preview, events = self.preview(browser_factory=Mock(return_value=browser), client_id='t3k_pub_test')
        preview.browse = Mock(return_value=None)
        preview.command = Mock(return_value={})
        preview.run(7, 1)
        preview.close_diagnostics()
        self.assertEqual([e['stage'] for e in events], ['started', 'ready', 'closing', 'closed'])
        browser.close.assert_called_once_with()

    def test_helper_error_notice_survives_later_cleanup_acknowledgement(self):
        browser = Mock(error='The helper stopped responding.')
        browser.wait_ready.return_value = True
        preview, _ = self.preview(browser_factory=Mock(return_value=browser), client_id='t3k_pub_test')
        preview.browse = Mock(return_value=None)
        preview.command = Mock(return_value={'notice': 'Preview restored.'})
        preview.run(7, 1)
        notice = preview.message.call_args.args[0]
        self.assertIn('helper stopped responding', notice)
        self.assertIn('unconfirmed', notice)
        self.assertNotIn('Preview restored', notice)
        preview.command.assert_called_once_with(7, 'close', reason='helper-disconnected')

    def test_stuck_sink_has_bounded_memory_and_shutdown_and_cannot_block_commands(self):
        entered, release = Event(), Event()

        def sink(_event):
            entered.set()
            release.wait(2)

        preview, _ = self.preview(sink)
        self.addCleanup(release.set)
        preview._trace('browser', 'started', 7)
        self.assertTrue(entered.wait(1))
        self.reply_immediately(preview, slot=128)
        started = monotonic()
        self.assertEqual(preview.command(7, 'audition')['slot'], 128)
        for _ in range(500):
            preview._trace('browser', 'ready', 7)
        self.assertLessEqual(preview._trace_queue.qsize(), 128)
        preview.close_diagnostics()
        self.assertLess(monotonic() - started, 1)
        release.set()

    def test_disabled_and_failing_diagnostics_do_not_change_operation(self):
        disabled = TonePreview(Mock(), None, None, Event(), Mock())
        self.assertIsNone(disabled._trace_thread)
        self.assertEqual(disabled._trace_call('prepare', 7, lambda: 42), 42)
        preview, _ = self.preview(Mock(side_effect=RuntimeError('sink failed')))
        self.reply_immediately(preview, slot=128)
        self.assertEqual(preview.command(7, 'audition')['slot'], 128)
        preview.close_diagnostics()
        self.assertFalse(preview._trace_thread.is_alive())


if __name__ == '__main__':
    unittest.main()
