"""Replay the production worker close/actions without HX Edit, USB or network."""
from pathlib import Path
from queue import Queue
from threading import Event
import unittest
from unittest.mock import Mock, patch

from tone_preview import TonePreview


class Browser:
    error = None
    close_reason = None
    process = None

    def __init__(self, actions, events):
        self.actions = Queue()
        for action in actions:
            self.actions.put(action)
        self.events = events
        self.sent = []

    def wait_ready(self):
        return True

    def next_action(self):
        return self.actions.get_nowait() if not self.actions.empty() else None

    def is_set(self):
        return False

    def send(self, kind, **values):
        self.sent.append((kind, values))

    def close(self):
        self.events.append('helper-closed')


class PreviewCleanupLifecycleTests(unittest.TestCase):
    def make_preview(self, actions):
        events = []
        browser = Browser(actions, events)
        library = Mock()
        library.entries.return_value = {}
        library.view_state.return_value = {}
        preview = TonePreview(Mock(), str(Path(__file__).resolve().parents[1]), 'unused', Event(), Mock(),
                              library=library, browser_factory=Mock(return_value=browser))
        preview.navigation = True
        preview.session = 7
        preview.navigation_state = dict(session=7, version=3, bound=True, preview=False)
        preview.target_ready.set()
        preview.tile = {'block':'NAM A2 Lite', 'modes':['original']}
        preview.bounds = {}

        def post(message):
            if message['type'] != 'toneCommand':
                return
            request = message['payload']
            events.append(dict(request))
            result = {'usedSlot':9} if request['action'] == 'use' else {'ended':True}
            preview.receive(dict(type='toneReply', id=request['id'], session=request['session'], **result))
        preview.script.post.side_effect = post
        return preview, browser, events

    def run_offline(self, preview):
        with patch.dict('os.environ', {'T3K_PUBLISHABLE_KEY':''}), patch('tone_preview.render_library', return_value='<library/>'):
            preview.run(7, 1)

    def test_escape_close_and_x_restore_before_helper_disposal_with_reason(self):
        for reason in ('escape', 'window-close', 'escape-native', 'close-button'):
            with self.subTest(reason=reason):
                preview, _, events = self.make_preview([{'close':reason}])
                self.run_offline(preview)
                self.assertEqual([item['action'] for item in events if isinstance(item, dict)], ['close'])
                self.assertEqual(events[0]['reason'], reason)
                self.assertEqual(events[-1], 'helper-closed')
                preview.message.assert_not_called()

    def test_native_escape_uses_same_worker_close_and_stale_session_is_ignored(self):
        preview, browser, events = self.make_preview([])
        preview.browser = browser
        preview.receive(dict(type='toneDismiss', session=6, reason='escape-native'))
        self.assertTrue(browser.actions.empty())
        preview.receive(dict(type='toneDismiss', session=7, reason='escape-native'))
        self.run_offline(preview)
        self.assertEqual(events[0]['reason'], 'escape-native')

    def test_catalog_library_and_presentation_changes_do_not_cleanup(self):
        preview, _, events = self.make_preview(['library', {'presentation':'floating'},
                                               'libraryView:{"query":"Deluxe"}', 'catalog',
                                               {'presentation':'docked'}, 'finish'])
        self.run_offline(preview)
        commands = [item for item in events if isinstance(item, dict)]
        self.assertEqual([item['action'] for item in commands], ['close'])
        self.assertEqual(commands[0]['reason'], 'close-button')
        preview.library.update_view.assert_called_once_with({'query':'Deluxe'})

    def test_headless_sidebar_uses_existing_native_workflow_without_browser_or_network(self):
        preview, _, events = self.make_preview([])
        identity = 'a' * 64
        preview.library.entries.return_value = {identity:{'name':'Demo', 'irSlots':[9]}}
        preview.headless = True
        preview.initial_action = 'use:' + identity
        preview.sidebar_status = Mock()
        with patch('tone_preview.select_tone') as select, patch('tone_preview.convert_batch') as convert:
            self.run_offline(preview)
        preview.browser_factory.assert_not_called()
        select.assert_not_called()
        convert.assert_not_called()
        commands = [item for item in events if isinstance(item, dict)]
        self.assertEqual([item['action'] for item in commands], ['use', 'close'])
        self.assertEqual(commands[0]['targetVersion'],3)
        self.assertEqual(commands[0]['identity'],identity)
        self.assertEqual(commands[1]['reason'],'library-action-completed')
        statuses = [call.args[0] for call in preview.sidebar_status.call_args_list]
        self.assertTrue(any(status['busy'] for status in statuses))
        self.assertTrue(any('Preset remains unsaved' in status['text'] and not status['busy'] for status in statuses))

    def test_close_intent_during_download_cannot_apply_prepared_capture(self):
        for reason in ('escape', 'close-button', 'window-close', 'escape-native'):
            with self.subTest(reason=reason):
                preview, browser, events = self.make_preview([((42,'private-token'),None), 'finish'])
                preview.browser = browser
                tone = {'id':42, 'title':'Demo', 'gear':'amp'}
                models = [{'id':43, 'name':'Clean'}]
                def prepare(*_args):
                    if reason == 'escape-native':
                        preview.receive(dict(type='toneDismiss',session=7,reason='escape-native'))
                    else:
                        browser.close_reason = reason
                    return 'unused.wav'
                with patch('tone_preview.Thread'), patch('tone_preview.get_tone',return_value=(tone,models)), \
                     patch('tone_preview.prepare_model',side_effect=prepare), patch('tone_preview.remember_link'):
                    preview.browse(browser,7,'t3k_pub_test')
                self.assertFalse(any(isinstance(event,dict) for event in events), 'cancelled preparation sent a native command')

    def test_shared_library_changes_coalesce_and_only_row_changes_rerender(self):
        for metadata in (False, True):
            with self.subTest(metadata=metadata):
                preview, browser, _ = self.make_preview([])
                preview.browser = browser
                identity = 'a' * 64
                preview.library.entries.return_value = {identity:{'name':'Demo','gear':'amp','irSlots':[9]}}
                preview.library.view_state.return_value = {'query':'','revision':0}
                calls = 0
                def next_action():
                    nonlocal calls
                    calls += 1
                    if calls > 1:
                        return 'finish'
                    preview.library.view_state.return_value = {'query':'Deluxe','revision':1}
                    if metadata:
                        preview.library.entries.return_value = {identity:{'name':'Deluxe','gear':'amp','irSlots':[9]}}
                    preview.library_changed()
                    preview.library_changed()
                    self.assertEqual(browser.actions.qsize(),1)
                    return browser.actions.get_nowait()
                browser.next_action = next_action
                with patch('tone_preview.render_library',return_value='<library/>') as render:
                    preview.browse(browser,7,'')
                self.assertEqual(render.call_count,2 if metadata else 1)
                statuses=[data for kind,data in browser.sent if kind=='status']
                self.assertEqual(statuses[-1]['libraryView'],{'query':'Deluxe','revision':1})


if __name__ == '__main__':
    unittest.main()
