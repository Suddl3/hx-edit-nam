"""Single owner-triggered native export observation. No USB/GUI operations."""
import json
from pathlib import Path
from uuid import uuid4
from threading import RLock
from library_readback import ReadbackSession
from library_export_adapter import ExportReadbackAdapter


class NativeExportTrial:
    def __init__(self,catalog,root,slot,*,clock=None,destination=None,request=None,timeout=15):
        if type(slot) is not int or not 1<=slot<=127:raise ValueError('Permanent slot 1–127 required')
        self.session=ReadbackSession('native-editor-observation',catalog,timeout=timeout,
            **({'clock':clock} if clock else {}))
        self.adapter=ExportReadbackAdapter(self.session,root)
        self.destination=destination
        self.config={'slot':slot,'path':destination if destination is not None else str(self.adapter.root/'native-export.wav'),
            'request':request or uuid4().hex}
        self.plan=None;self.submission=None;self.stopped=False;self.waiting=False
        self.primed=None;self.result=None
        self.lock=RLock()
        self.report=self.adapter.root/'RESULT.json'

    def finish(self,result):
        self.stopped=True
        self.result=result
        # Historical observation only. Do not promote this to a lasting live-slot
        # mapping until editor disconnect/mutation lifecycle wiring is verified.
        self.report.write_text(json.dumps({'scope':'single native export observation',
            'updatesLibrary':False,**result},indent=2)+'\n',encoding='utf8')
        self.adapter.close()

    def prime(self,device,revision):
        """Automatic exports allocate/check their fresh destination before enqueueing."""
        if self.plan is not None or self.submission is not None:raise ValueError('Already prepared')
        self.primed={'device':device,'revision':revision}
        self.row={'slot':self.config['slot'],'occupied':True,'name':''}
        self.session.set_inventory([self.row],revision)
        self.plan=self.adapter.prepare(self.config['slot'],filename='native-export.wav')

    def receive(self,event):
        with self.lock:return self._receive(event)

    def _receive(self,event):
        if self.stopped or event.get('request')!=self.config['request']:return
        if event.get('stage')=='ready':return
        if event.get('stage')=='discarded':
            self.finish({'status':'discarded','reason':event.get('reason'),
                'diagnostic':{k:event[k] for k in ('initialRevision','currentRevision','initialDevice',
                    'currentDevice','nativeResult','nativeDetails','notifications','settings',
                    'fault','workerStarted','workerReturned','timing') if k in event}});return
        try:
            if event.get('slot')!=self.config['slot'] or event.get('path')!=self.config['path']:
                raise ValueError('Export target differs')
            if event.get('stage')=='submitted':
                if self.submission is not None:raise ValueError('Duplicate submission')
                if type(event.get('device')) is not int or type(event.get('revision')) is not int:
                    raise ValueError('Missing native identity/revision')
                if type(event.get('sequence')) is not int or event['sequence']<1:
                    raise ValueError('Missing native sequence')
                self.submission=dict(event)
                self.row={'slot':self.config['slot'],'occupied':True,'name':''}
                if self.primed is not None:
                    if any(event[k]!=v for k,v in self.primed.items()):raise ValueError('Prepared identity changed')
                else:
                    self.session.set_inventory([self.row],event['revision'])
                    self.plan=(self.adapter.prepare(self.config['slot'],destination=self.destination) if self.destination is not None
                        else self.adapter.prepare(self.config['slot'],filename='native-export.wav'))
            elif event.get('stage') in ('awaiting-unlock','completed'):
                if self.submission is None:raise ValueError('Completion without submission')
                if any(event.get(k)!=self.submission[k] for k in ('sequence','device','revision')):
                    raise ValueError('Completion identity changed')
                details=event.get('details')
                if (type(event.get('result')) is not int or event['result']!=0 or details!=[0]
                        or type(details[0]) is not int):
                    raise ValueError('Native export did not succeed')
                if event['stage']=='awaiting-unlock':
                    if self.waiting:raise ValueError('Duplicate unlock wait')
                    self.waiting=True
                    # Worker return is not completion. Keep the original plan,
                    # timeout and file untouched until the observer sees unlock.
                    return
                result=self.adapter.complete(self.plan,reported_path=event['path'],native_success=True,
                    after=self.row,revision=event['revision'])
                self.finish({**result,'device':event['device'],'path':event['path'],
                    'diagnostic':{k:event[k] for k in ('notifications','settings','timing') if k in event}})
                if result.get('status')=='matched':
                    # Return fresh evidence directly, never reload a historical
                    # receipt. Native-side revision/epoch recheck gates display.
                    return {key:event[key] for key in ('request','sequence','slot','device','revision')} | {
                        'contentId':result['contentId']}
            else:raise ValueError('Unexpected export event')
        except (ValueError,OSError,KeyError,TypeError) as error:
            self.finish({'status':'discarded','reason':str(error)})

    def close(self,reason='editor-disconnected-before-completion'):
        with self.lock:
            if not self.stopped:self.finish({'status':'discarded','reason':reason})


class NativeExportSeries:
    """Serial owner exports, each with a fresh receipt and file-freshness check.

    Only the actual native export callback supplies a target. This never reads
    arbitrary UI paths, starts exports, or restores a saved slot mapping.
    """
    def __init__(self,catalog,root,*,clock=None):
        self.catalog=catalog;self.root=Path(root);self.clock=clock
        self.config={'request':uuid4().hex,'repeat':True}
        self.active=None;self.last_sequence=0;self.closed=False;self.lock=RLock()

    def receive(self,event):
        with self.lock:
            if self.closed or event.get('stage')=='ready':return
            sequence=event.get('sequence')
            if event.get('stage')=='submitted':
                if (type(sequence) is not int or sequence<=self.last_sequence or
                        event.get('request')!=self.config['request']+':'+str(sequence) or
                        type(event.get('slot')) is not int or not 1<=event['slot']<=127 or
                        not isinstance(event.get('path'),str)):return
                if self.active and not self.active.stopped:
                    self.active.close('superseded-export')
                self.last_sequence=sequence
                catalog=self.catalog() if callable(self.catalog) else self.catalog
                self.active=NativeExportTrial(catalog,self.root,event['slot'],clock=self.clock,
                    destination=event['path'],request=event['request'])
            if self.active:return self.active.receive(event)

    def connection_changed(self):
        with self.lock:
            if self.active:self.active.close('connection-changed')

    def close(self):
        with self.lock:
            self.closed=True
            if self.active:self.active.close()
