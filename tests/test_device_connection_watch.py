"""Exercise Windows filter layout with a fake cfgmgr32, never registering with Windows."""
import ctypes as C
from unittest.mock import Mock
from uuid import UUID

from device_connection_watch import LINE6_INTERFACE_GUID, NotifyFilter, WindowsNotifications, ConnectionWatch


def test_notification_filter_is_line6_class_only_not_all_pc_devices():
    api = WindowsNotifications.__new__(WindowsNotifications)
    api.lib = Mock()
    api.callback_type = lambda fn: fn
    captured = {}

    def register(pointer, context, callback, output):
        spec = C.cast(pointer, C.POINTER(NotifyFilter)).contents
        captured.update(flags=spec.flags, kind=spec.kind, size=spec.size,
                        guid=C.string_at(C.addressof(spec.data), 16), callback=callback)
        C.cast(output, C.POINTER(C.c_void_p)).contents.value = 123
        return 0

    api.lib.CM_Register_Notification.side_effect = register
    changed = Mock()
    handle, callback = api.register(changed)
    assert captured['flags'] == 0
    assert captured['kind'] == 0
    assert captured['size'] == C.sizeof(NotifyFilter)
    assert captured['guid'] == UUID(LINE6_INTERFACE_GUID).bytes_le
    assert handle.value == 123
    for action in (0, 1, 2, 3):
        callback(None, None, action, None, 0)
    assert [call.args for call in changed.call_args_list] == [(0,), (1,)]


def test_failed_registration_does_not_claim_monitor_ready():
    api = Mock()
    api.register.side_effect = OSError('not supported')
    delivered = []
    watcher = ConnectionWatch(delivered.append, api=api, presence=lambda: True)
    assert not watcher.start()
    assert delivered == [{'generation': 0, 'ready': False, 'present': False}]
    watcher.close()
    api.unregister.assert_not_called()
