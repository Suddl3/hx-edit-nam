"""Native-to-library routing, stale disconnects, and launcher lifecycle without hardware."""
from importlib.machinery import SourceFileLoader
import importlib.util
from pathlib import Path
import shutil
import subprocess
from threading import Event
from unittest.mock import Mock, patch

import pytest

from navigation_host import NavigationHost

ROOT = Path(__file__).resolve().parents[1]


def host_fixture(tmp_path):
    script, library, service, watch = Mock(), Mock(), Mock(), Mock()
    detached = Event()
    host = NavigationHost(script, tmp_path, library, detached,
                          service=service, watch_factory=watch)
    return host, script, library, service, watch, detached


def test_file_evidence_goes_back_to_native_acceptance_before_library(tmp_path):
    host, script, library, service, _, _ = host_fixture(tmp_path)
    evidence = {'slot': 9, 'contentId': 'a' * 64, 'automaticToken': '1:1'}
    service.receive.return_value = {'token': '1:1', 'status': 'matched', 'evidence': evidence}
    assert host.receive({'type': 'irExportObservation', 'stage': 'completed'})
    script.post.assert_called_once_with({'type': 'librarySlotEvidence', 'payload': evidence})
    library.set_slot_inventory.assert_not_called()
    accepted = {'type': 'floorInventory', 'epoch': 2, 'state': 'verified', 'entries': [[9, 'a' * 64]]}
    host.receive(accepted)
    library.set_slot_inventory.assert_called_once_with(accepted)


def test_a_type_change_in_the_library_reaches_the_editor_once(tmp_path):
    # The NAM tab or library card changes a capture's type; the block selector's NAM categories
    # are built from the metadata the editor holds, so it must be posted without a restart.
    from nam_library import Library
    library = Library(tmp_path / "library")
    capture = tmp_path / 'demo.nam'
    capture.write_bytes(b'{"metadata": {"gear_type": "amp"}}')
    identity = library.add(capture)
    library._payload_index[identity] = {'c' * 64}          # as prime_library leaves it
    script, service, watch = Mock(), Mock(), Mock()
    host = NavigationHost(script, tmp_path, library, Event(), service=service, watch_factory=watch)
    posted = lambda: [call.args[0] for call in script.post.call_args_list
                      if call.args[0]['type'] == 'floorPayloadMetadata']
    library.mark_pending(identity, 5)                      # an unrelated notice: nothing posted
    assert posted() == []
    library.update(identity, gear='pedal')
    assert [p['payload']['c' * 64]['gear'] for p in posted()] == ['pedal']
    library.set_slot_inventory({'epoch': 1, 'state': 'verified', 'entries': [], 'generation': 1})
    assert len(posted()) == 1                              # the editor's own slot report: no repost
    library.reset_gear(identity)
    assert [p['payload']['c' * 64]['gear'] for p in posted()] == ['pedal', 'amp']
    host.close()
    library.update(identity, gear='amp-cab')               # after close: not posted
    assert len(posted()) == 2


def test_failure_reports_same_token_and_does_not_publish_mapping(tmp_path):
    host, script, library, service, _, _ = host_fixture(tmp_path)
    service.prepare.side_effect = ValueError('Export already pending')
    host.receive({'type': 'automaticSlotPrepare', 'token': '1:2'})
    assert script.post.call_args.args[0] == {'type': 'automaticSlotPrepared',
        'payload': {'token': '1:2', 'error': 'Export already pending'}}
    service.receive.return_value = {'token': '1:2', 'status': 'unidentified'}
    host.receive({'type': 'irExportObservation'})
    assert script.post.call_args.args[0]['type'] == 'automaticSlotResult'
    library.set_slot_inventory.assert_not_called()


def test_connection_change_and_shutdown_reject_delayed_messages(tmp_path):
    host, script, library, service, watch, detached = host_fixture(tmp_path)
    host.connection_changed({'generation': 2, 'ready': True, 'present': False})
    service.connection_changed.assert_called_once()
    assert script.post.call_args_list[0].args[0]['type'] == 'toneDeviceChange'
    assert script.post.call_args.args[0]['type'] == 'hostDeviceChange'
    detached.set()
    assert not host.receive({'type': 'floorInventory', 'entries': [[9, 'a' * 64]]})
    library.set_slot_inventory.assert_not_called()
    host.close(); host.close()
    watch.return_value.close.assert_called_once()
    service.close.assert_called_once()


@pytest.mark.parametrize('state', ['preparation-cancelled', 'native-discovery-fault',
                                  'native-observer-unavailable', 'connection-monitor-failed'])
def test_terminal_native_status_releases_private_export_arm(tmp_path, state):
    host, script, library, service, _, _ = host_fixture(tmp_path)
    host.receive({'type': 'automaticSlotStatus', 'state': state})
    service.connection_changed.assert_called_once()
    script.post.assert_not_called()
    library.set_slot_inventory.assert_not_called()


def launcher_module():
    loader = SourceFileLoader('navigation_launcher_test', str(ROOT / 'start-hx-edit-nam.pyw'))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


@pytest.mark.parametrize('window', ['embedded', 'floating'])
def test_navigation_launcher_composes_and_routes_without_usb_or_real_process(tmp_path, window):
    module = launcher_module()
    device, session, script = Mock(), Mock(), Mock()
    callbacks, events = {}, {}
    device.spawn.return_value = 123
    device.attach.return_value = session
    session.on.side_effect = lambda name, callback: callbacks.__setitem__(name, callback)
    session.create_script.return_value = script
    script.on.side_effect = lambda name, callback: events.__setitem__(name, callback)
    script.load.side_effect = lambda: events['message']({'type': 'send', 'payload': {'type': 'ready'}}, None)

    def resume(_):
        for kind in ('toneStart', 'toneTarget', 'toneFocus', 'toneReply', 'toneBounds', 'toneDismiss', 'toneAction', 'namSidebarLayout'):
            events['message']({'type': 'send', 'payload': {'type': kind, 'session': 1}}, None)
        callbacks['detached']()

    device.resume.side_effect = resume
    with patch('nam_catalog.ir_parameters', return_value={}), \
         patch('nam_catalog.sidebar_width', return_value=320), \
         patch('nam_library.Library') as library, \
         patch('floor_inventory.prime_library') as prime, \
         patch('navigation_host.NavigationHost') as host, \
         patch('nam_sidebar.prepare_sidebar') as prepare_sidebar, \
         patch('nam_sidebar.NamSidebar') as sidebar, \
         patch('tone_preview.TonePreview') as preview:
        library.return_value.payload_metadata.return_value = {}
        preview.return_value.thread = None
        module.run_editor(tmp_path / 'HX Edit.exe', tmp_path, device, {}, floor_reference=True,
                          tone_window=window, navigation=True, client_id='t3k_pub_test')
        prime.assert_called_once()
        prepare_sidebar.assert_called_once()
        injected = session.create_script.call_args.args[0]
        assert 'installNavigationBanner' in injected
        assert 'connectNativeExportLibrary' in injected
        assert 'installNamSidebar(namView)' in injected
        assert '"requireHostMonitor": true' in injected
        assert '"navigation": true' in injected
        assert '"nativeDiscovery": true' in injected
        assert 'dockPanel:navigationBanner.dockPanel()' in injected
        assert 't3k_pub_test' not in injected
        if shutil.which('node'):
            # Parse the actual concatenated Frida program, including module-level
            # names. Individual file checks miss duplicate lexical declarations.
            subprocess.run(['node', '--check'], input=injected, text=True,
                           capture_output=True, check=True, timeout=15)
            # Execute that same composition with code bytes changed by each
            # mocked interceptor, so guard-vs-hook ordering is observable.
            result = subprocess.run(['node', str(ROOT / 'tests' / 'navigation_startup_probe.cjs')],
                                    input=injected, text=True, capture_output=True, timeout=15)
            assert result.returncode == 0, result.stdout + result.stderr
            assert 'navigation startup probe passed' in result.stdout
        assert preview.call_args.kwargs['client_id'] == 't3k_pub_test'
        assert [call.args[0]['type'] for call in preview.return_value.receive.call_args_list] == [
            'toneStart', 'toneTarget', 'toneFocus', 'toneReply', 'toneBounds', 'toneDismiss', 'toneAction']
        assert preview.return_value.sidebar_status == sidebar.return_value.status
        assert any(call.args[0]['type'] == 'namSidebarLayout' for call in sidebar.return_value.receive.call_args_list)
        sidebar.return_value.close.assert_called_once()
        host.return_value.start.assert_called_once()
        host.return_value.close.assert_called_once()
    device.kill.assert_not_called()


@pytest.mark.parametrize('floor,window', [(False, 'floating'), (False, 'embedded')])
def test_navigation_profile_is_validated_before_spawn(tmp_path, floor, window):
    device = Mock()
    with pytest.raises(ValueError):
        launcher_module().run_editor(tmp_path / 'fake.exe', tmp_path, device, {},
                                    floor_reference=floor, tone_window=window, navigation=True)
    device.spawn.assert_not_called()


def test_unavailable_sidebar_stops_before_any_editor_or_device_session(tmp_path):
    device = Mock()
    with patch('nam_sidebar.prepare_sidebar', side_effect=OSError('compiler unavailable')), \
         patch('floor_inventory.prime_library') as prime:
        with pytest.raises(OSError, match='compiler unavailable'):
            launcher_module().run_editor(tmp_path / 'fake.exe', tmp_path, device, {},
                                        floor_reference=True, navigation=True)
        prime.assert_not_called()
    device.spawn.assert_not_called()
    device.attach.assert_not_called()
