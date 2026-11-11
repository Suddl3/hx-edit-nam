"""Pre-arm private export files BEFORE the native queue starts. No device access."""
from pathlib import Path
from threading import RLock
from native_export_trial import NativeExportTrial


class AutomaticSlotService:
    def __init__(self,catalog,root):
        self.catalog=catalog;self.root=Path(root);self.lock=RLock()
        self.active=None;self.token=None;self.closed=False

    def prepare(self,value):
        with self.lock:
            if self.closed:raise ValueError('Discovery closed')
            if (not isinstance(value.get('token'),str) or len(value['token'])>80 or
                    type(value.get('slot')) is not int or not 1<=value['slot']<=127 or
                    value.get('device')!=0x210001 or type(value.get('revision')) is not int):
                raise ValueError('Invalid discovery request')
            if self.active and not self.active.stopped:self.active.close('superseded-discovery')
            catalog=self.catalog() if callable(self.catalog) else self.catalog
            self.active=NativeExportTrial(catalog,self.root,value['slot'],timeout=30)
            self.active.prime(value['device'],value['revision'])
            self.token=value['token']
            return {'token':self.token,'path':self.active.config['path']}

    def receive(self,event):
        with self.lock:
            trial=self.active
            if self.closed or trial is None or trial.stopped:return None
            if event.get('stage')=='submitted':
                if event.get('path')!=trial.config['path']:return None
                trial.config['request']=event.get('request')
            if event.get('request')!=trial.config['request']:return None
            evidence=trial.receive(event)
            if trial.stopped:
                return {'token':self.token,'status':trial.result['status'],'evidence':evidence}

    def connection_changed(self):
        with self.lock:
            if self.active:self.active.close('connection-changed')
            self.active=None;self.token=None

    def close(self):
        with self.lock:self.connection_changed();self.closed=True
