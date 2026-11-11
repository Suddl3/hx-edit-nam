"""Passive Windows PnP notifications; no device handles, enumeration or USB I/O."""
import ctypes as C
from queue import Queue, Empty
from threading import Event, Lock, Thread


class NotifyUnion(C.Union):
    # cfgmgr32.h CM_NOTIFY_FILTER.u; MAX_DEVICE_ID_LEN = 200 WCHARs.
    _fields_=[('guid',C.c_uint32*4),('handle',C.c_void_p),('instance',C.c_uint16*200)]


class NotifyFilter(C.Structure):
    _fields_=[('size',C.c_uint32),('flags',C.c_uint32),('kind',C.c_uint32),
              ('reserved',C.c_uint32),('data',NotifyUnion)]


class WindowsNotifications:
    def __init__(self):
        self.lib=C.WinDLL('cfgmgr32.dll')
        self.callback_type=C.WINFUNCTYPE(C.c_uint32,C.c_void_p,C.c_void_p,C.c_int,C.c_void_p,C.c_uint32)
        self.lib.CM_Register_Notification.argtypes=[C.POINTER(NotifyFilter),C.c_void_p,self.callback_type,C.POINTER(C.c_void_p)]
        self.lib.CM_Register_Notification.restype=C.c_uint32
        self.lib.CM_Unregister_Notification.argtypes=[C.c_void_p]
        self.lib.CM_Unregister_Notification.restype=C.c_uint32

    def register(self,changed):
        spec=NotifyFilter();spec.size=C.sizeof(spec)
        spec.flags=1  # ALL_INTERFACE_CLASSES; conservative invalidation.
        spec.kind=0   # DEVICEINTERFACE. Do not register a device handle.
        def callback(handle,context,action,data,size):
            # Deliberately never inspect device paths, serials or event data.
            if action in (0,1):changed(action)
            return 0
        callback=self.callback_type(callback)
        handle=C.c_void_p()
        result=self.lib.CM_Register_Notification(C.byref(spec),None,callback,C.byref(handle))
        if result or not handle.value:raise OSError('PnP notification registration failed')
        return handle,callback  # retain callback until unregister completes

    def unregister(self,token):
        return self.lib.CM_Unregister_Notification(token[0])


def floor_presence():
    """Read Windows' interface list only. Never opens an interface or USB handle."""
    from uuid import UUID
    cfg=C.WinDLL('cfgmgr32.dll')
    guid=(C.c_ubyte*16).from_buffer_copy(UUID('fd7bdaab-7d76-b4c8-b9be-209c747294d0').bytes_le)
    size=cfg.CM_Get_Device_Interface_List_SizeW
    size.argtypes=[C.POINTER(C.c_uint32),C.c_void_p,C.c_wchar_p,C.c_uint32];size.restype=C.c_uint32
    read=cfg.CM_Get_Device_Interface_ListW
    read.argtypes=[C.c_void_p,C.c_wchar_p,C.c_wchar_p,C.c_uint32,C.c_uint32];read.restype=C.c_uint32
    count=C.c_uint32()
    if size(C.byref(count),guid,None,0) or not 1<=count.value<=65536:raise OSError('Interface list unavailable')
    buffer=C.create_unicode_buffer(count.value)
    if read(guid,None,buffer,count.value,0):raise OSError('Interface list changed')
    paths=[p for p in buffer[:].split('\0') if 'vid_0e41&pid_4248&mi_00' in p.lower()]
    return len(paths)==1


class ConnectionWatch:
    """Short native callbacks enqueue invalidation; a worker delivers to Frida."""
    def __init__(self,deliver,*,api=None,presence=None):
        self.deliver=deliver;self.api=api
        self.presence=presence
        self.generation=0;self.lock=Lock();self.queue=Queue(maxsize=1)
        self.closed=Event();self.token=None;self.worker=None

    def changed(self,action):
        if action not in (0,1):return
        with self.lock:
            if self.closed.is_set():return
            self.generation+=1
            # Coalesce bursts without losing the newest generation.
            try:self.queue.get_nowait()
            except Empty:pass
            self.queue.put_nowait(self.generation)

    def send(self,generation,ready):
        if not self.closed.is_set():
            value={'generation':generation,'ready':ready}
            if self.presence is not None:
                try:value['present']=ready and self.presence()
                except OSError:value.update(present=False,ready=False)
            self.deliver(value)

    def start(self):
        if self.worker is not None or self.closed.is_set():raise ValueError('Watcher cannot restart')
        try:
            if self.api is None:self.api=WindowsNotifications()
            self.token=self.api.register(self.changed)
        except (OSError,AttributeError):
            self.send(0,False)
            return False
        self.send(0,True)
        self.worker=Thread(target=self.run,daemon=True)
        self.worker.start()
        return True

    def run(self):
        while not self.closed.is_set():
            try:generation=self.queue.get(timeout=.1)
            except Empty:continue
            try:self.send(generation,True)
            except Exception:
                # A broken editor channel cannot be retried into another session.
                self.closed.set()

    def close(self):
        self.closed.set()
        if self.token is not None:
            self.api.unregister(self.token);self.token=None
        if self.worker is not None:self.worker.join(timeout=2)
