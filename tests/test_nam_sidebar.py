import io
import json
import os
from pathlib import Path
import subprocess
import shutil
import tempfile
import time
from threading import Event
import unittest

from nam_library import Library, render_library
from nam_sidebar import NamSidebar, classification_revision, sidebar_rows, prepare_sidebar


class SidebarModelTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.folder = Path(self.temp.name)
        self.library = Library(self.folder / 'library')
        source = self.folder / 'Deluxe.nam'
        source.write_text('{"metadata":{"gear_type":"amp_cab"},"weights":[1,2]}')
        self.identity = self.library.add(source, name='Deluxe Reverb')

    def tearDown(self):
        self.temp.cleanup()

    def test_kept_rows_exist_before_any_usb_verification(self):
        rows = sidebar_rows(self.library, can_load=True)
        self.assertEqual([r['title'] for r in rows], ['Deluxe Reverb'])
        self.assertEqual(rows[0]['deviceStatus'], 'unverified')
        self.assertEqual(rows[0]['verifiedSlots'], [])
        self.assertTrue(rows[0]['canLoad'])
        self.assertEqual(rows[0]['categoryLabel'], 'Amp + cab')

    def test_only_pending_capture_gets_amber_receipt(self):
        source = self.folder / 'Pedal.nam'
        source.write_text('{"metadata":{"gear_type":"pedal"}}')
        other = self.library.add(source)
        self.library.mark_pending(self.identity, 21)
        rows = {r['contentId']: r for r in sidebar_rows(self.library, can_load=True)}
        self.assertEqual(rows[self.identity]['pendingSlots'], [21])
        self.assertEqual(rows[other]['pendingSlots'], [])
        self.assertFalse(rows[self.identity]['canLoad'])

    def test_atomic_proof_and_reconnect_keep_local_rows(self):
        content = 'a' * 64
        self.library._payload_index[self.identity] = {content}
        self.library.set_slot_inventory({'epoch': 1, 'state': 'verified', 'entries': [[127, content]]})
        self.assertEqual(sidebar_rows(self.library)[0]['verifiedSlots'], [127])
        self.library.set_slot_inventory({'epoch': 2, 'state': 'refreshing', 'entries': [[127, content]]})
        row = sidebar_rows(self.library)[0]
        self.assertEqual(row['displaySlots'], [127])
        self.assertEqual(row['verifiedSlots'], [])
        self.library.set_slot_inventory({'epoch': 3, 'state': 'unverified', 'entries': []})
        row = sidebar_rows(self.library)[0]
        self.assertEqual(row['title'], 'Deluxe Reverb')
        self.assertEqual(row['verifiedSlots'], [])

    def test_shared_state_survives_view_switch_and_safe_updates(self):
        changes = []
        unsubscribe = self.library.subscribe(lambda: changes.append(self.library.view_state()))
        view = dict(selected=self.identity, query='Deluxe', filter='amp-cab', sortKey='added', sortDirection=-1)
        self.assertTrue(self.library.update_view(view))
        state = self.library.view_state()
        self.assertEqual({k: state[k] for k in view}, view)
        html = render_library(self.library.entries(), state)
        self.assertIn('const initialView = ', html)
        self.assertIn('"selected": "' + self.identity, html)
        self.assertIn('"query": "Deluxe"', html)
        self.assertTrue(self.library.update_view(view))
        self.assertEqual(len(changes), 1)  # No feedback loop from an identical echo.
        self.assertFalse(self.library.update_view({'selected': {}}))
        self.assertFalse(self.library.update_view({'filter': 'arbitrary'}))
        self.assertFalse(self.library.update_view({'sortDirection': True}))
        self.assertFalse(self.library.update_view({'query': 'a' * 301}))
        unsubscribe()

    def test_type_correction_preserves_source_and_reset(self):
        before = self.library.entries()[self.identity]
        self.library.update(self.identity, gear='pedal')
        after = self.library.entries()[self.identity]
        self.assertEqual(after['sourceGear'], 'amp-cab')
        self.assertNotEqual(classification_revision(before), classification_revision(after))
        self.library.reset_gear(self.identity)
        self.assertEqual(self.library.entries()[self.identity]['gear'], 'amp-cab')

    def test_missing_file_and_html_names_are_honest(self):
        path, _ = self.library._source(self.identity, self.library.entries()[self.identity])
        path.unlink()
        row = sidebar_rows(self.library, can_load=True)[0]
        self.assertEqual(row['fileStatus'], 'missing')
        self.assertFalse(row['canLoad'])
        self.library.update(self.identity, name='</script><script>oops</script>')
        self.assertNotIn('</script><script>oops', render_library(self.library.entries()))

    def test_local_metadata_change_notifies_without_device(self):
        calls = []
        self.library.subscribe(lambda: calls.append(len(self.library.entries())))
        self.library.update(self.identity, name='Friendly')
        self.library.mark_pending(self.identity, 17)
        self.assertEqual(calls, [1, 1])


class SidebarBridgeTests(unittest.TestCase):
    def test_native_bridge_is_deferred_deduplicated_and_has_failure_fallback(self):
        node = shutil.which('node')
        if not node:
            self.skipTest('Node is unavailable')
        result = subprocess.run([node, str(Path(__file__).with_name('nam_sidebar_probe.cjs'))],
                                capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_every_load_or_use_request_is_answered_and_traced(self):
        # Candidate59 owner session: a click on Use on Floor produced nothing, with no record of why.
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder, 'Deluxe.nam')
            source.write_text('{}', encoding='utf-8')
            library = Library(Path(folder, 'library'))
            identity = library.add(source)
            sent, traced, handled = [], [], []
            class Script:
                def post(self, value): pass
            bridge = NamSidebar(Script(), library, Event(), trace=traced.append,
                                action_handler=lambda action, capture: handled.append((action, capture)),
                                process_factory=lambda *a, **k: self.fail('no helper in this test'))
            try:
                bridge.send = lambda message: sent.append(message) or True
                request = {'type': 'action', 'action': 'select', 'contentId': identity}
                bridge._handle_action(request)                     # no NAM block selected yet
                self.assertIn('NAM block', sent[-1]['error'])
                self.assertEqual(traced[-1]['outcome'], 'rejected-no-target')
                bridge.layout = {'visible': True, 'targetReady': 1}   # as the native side once sent it
                self.assertTrue(bridge._state()['rows'][0]['canLoad'])
                self.assertIs(bridge._state()['targetReady'], True)
                bridge._handle_action(request)
                self.assertEqual(handled, [('use', identity)])
                self.assertEqual(traced[-1]['outcome'], 'sent')
                bridge._handle_action(request)                     # the first one has not finished
                self.assertIn('still running', sent[-1]['error'])
                self.assertEqual((handled, traced[-1]['outcome']), ([('use', identity)], 'rejected-busy'))
                # No answer from HX Edit: the request is given up instead of blocking Load/Use for good.
                bridge.operation_started -= NamSidebar.OPERATION_TIMEOUT + 1
                deadline = time.monotonic() + 3
                while bridge.operation_busy and time.monotonic() < deadline:
                    time.sleep(.05)
                self.assertFalse(bridge.operation_busy)
                self.assertIn('did not answer', sent[-1]['error'])
                self.assertNotIn('Deluxe', json.dumps(traced))     # diagnostics carry no capture names
            finally:
                bridge.close()

    def test_unrecognized_messages_do_not_start_helper(self):
        with tempfile.TemporaryDirectory() as folder:
            detached = Event()
            class Script:
                def post(self, value):
                    raise AssertionError('No device/native action expected')
            def no_spawn(*args, **kwargs):
                raise AssertionError('No helper expected before visible layout')
            bridge = NamSidebar(Script(), Library(folder), detached, process_factory=no_spawn)
            try:
                self.assertFalse(bridge.receive({'type': 'floorInventory'}))
                bridge.refresh()
            finally:
                bridge.close()
            self.assertTrue(bridge.closed.is_set())


@unittest.skipUnless(os.name == 'nt', 'Native WinForms helper is Windows-only')
class NativeSidebarTests(unittest.TestCase):
    def test_native_table_and_hidden_render(self):
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder)
            exe = prepare_sidebar(output / 'NamLibrary.exe')
            tested = subprocess.run([str(exe), '--self-test'], capture_output=True, text=True,
                                    timeout=45, creationflags=subprocess.CREATE_NO_WINDOW)
            self.assertEqual(tested.returncode, 0, tested.stdout + tested.stderr)
            self.assertIn('Library table self-tests passed', tested.stdout)
            image = output / 'sidebar.png'
            rendered = subprocess.run([str(exe), '--render-fixture', str(image)], capture_output=True, text=True,
                                      timeout=45, creationflags=subprocess.CREATE_NO_WINDOW)
            self.assertEqual(rendered.returncode, 0, rendered.stdout + rendered.stderr)
            self.assertIn('Native sidebar fixture passed', rendered.stdout)
            self.assertGreater(image.stat().st_size, 5000)


if __name__ == '__main__':
    unittest.main()
