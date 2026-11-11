"""Mocked Windows notification tests; never register with the live OS/device."""
import ctypes as C
from threading import Event
import unittest
from unittest.mock import patch

from device_connection_watch import ConnectionWatch,WindowsNotifications,NotifyFilter


class Api:
    def __init__(self,fail=False):self.fail=fail;self.closed=[]
    def register(self,callback):
        if self.fail:raise OSError('fixture failure')
        self.callback=callback;return 'token'
    def unregister(self,token):self.closed.append(token)


class DeviceWatchTests(unittest.TestCase):
    def test_registration_failure_disables_publication_without_a_worker(self):
        events=[];watch=ConnectionWatch(events.append,api=Api(fail=True))
        self.assertFalse(watch.start());self.assertIsNone(watch.worker)
        self.assertEqual(events,[{'generation':0,'ready':False}]);watch.close()

    def test_arrival_removal_and_bursts_reach_current_generation(self):
        api=Api();events=[];seen=Event()
        def deliver(value):
            events.append(value)
            if value['generation']==100:seen.set()
        watch=ConnectionWatch(deliver,api=api);self.addCleanup(watch.close)
        self.assertTrue(watch.start())
        for i in range(100):api.callback(i%2)
        self.assertTrue(seen.wait(2))
        self.assertEqual(events[0],{'generation':0,'ready':True})
        self.assertEqual(events[-1],{'generation':100,'ready':True})
        self.assertEqual(sorted({e['generation'] for e in events}),[e['generation'] for e in events])
        before=watch.generation;api.callback(9);self.assertEqual(watch.generation,before)
        watch.close();api.callback(1);self.assertEqual(watch.generation,before)
        self.assertEqual(api.closed,['token']);self.assertFalse(watch.worker.is_alive())

    def test_queued_early_notifications_are_not_lost(self):
        class Early(Api):
            def register(self,callback):callback(1);return super().register(callback)
        seen=Event();events=[]
        def deliver(value):
            events.append(value)
            if value['generation']==1:seen.set()
        watch=ConnectionWatch(deliver,api=Early());self.addCleanup(watch.close)
        watch.start();self.assertTrue(seen.wait(2))
        self.assertEqual([e['generation'] for e in events],[0,1])

    def test_windows_abi_and_callback_do_not_read_event_data(self):
        calls=[]
        class Fn:
            def __init__(self,fn):self.fn=fn
            def __call__(self,*args):return self.fn(*args)
        def register(spec,context,callback,handle):
            value=C.cast(spec,C.POINTER(NotifyFilter)).contents
            self.assertEqual((value.size,value.flags,value.kind,value.reserved),(416,1,0,0))
            self.assertIsNone(context)
            self.assertEqual(bytes(value.data),b'\0'*400)
            # Invalid event pointer is never dereferenced or logged.
            self.assertEqual(callback(None,None,1,C.c_void_p(1),0),0)
            callback(None,None,0,C.c_void_p(1),0)
            callback(None,None,9,C.c_void_p(1),0)
            C.cast(handle,C.POINTER(C.c_void_p))[0]=C.c_void_p(123)
            return 0
        class Lib:pass
        lib=Lib();lib.CM_Register_Notification=Fn(register)
        lib.CM_Unregister_Notification=Fn(lambda h:calls.append(h.value) or 0)
        with patch('device_connection_watch.C.WinDLL',return_value=lib):
            api=WindowsNotifications();events=[];token=api.register(events.append)
            self.assertEqual(events,[1,0]);api.unregister(token);self.assertEqual(calls,[123])


if __name__=='__main__':unittest.main()
