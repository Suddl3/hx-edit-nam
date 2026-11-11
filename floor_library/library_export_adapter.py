"""Offline completion adapter. No native hook, USB operation or auto-scan.

The future native adapter must attest its own completion and supply fresh inventory.
File existence alone is never a successful export. This module is not wired live.
"""
from dataclasses import dataclass
from pathlib import Path

from library_readback import ExportRequest


@dataclass(frozen=True)
class ExportPlan:
    request: ExportRequest
    destination: Path


class ExportReadbackAdapter:
    def __init__(self,session,root):
        self.session=session
        self.root=Path(root).resolve()/session.session
        self.root.mkdir(parents=True,exist_ok=False)
        self.pending=None

    def prepare(self,slot,*,filename=None,destination=None):
        if self.pending is not None:raise ValueError('An export is already pending')
        if destination is not None:
            if filename is not None:raise ValueError('Choose one destination mode')
            destination=Path(destination)
            # Only a path supplied by a correlated owner-triggered export.
            # Never create/delete/overwrite this file ourselves.
            if (not destination.is_absolute() or destination.suffix.lower()!='.wav'
                    or destination.resolve()!=destination or destination.is_symlink()
                    or not destination.parent.is_dir()):
                raise ValueError('Export needs a new absolute WAV path without redirection')
        if filename is not None and (not isinstance(filename,str) or not filename.endswith('.wav')
                or Path(filename).name!=filename or any(c in filename for c in '/\\:')):
            raise ValueError('Export filename must be a local WAV basename')
        request=self.session.begin(slot)
        destination=destination if destination is not None else self.root/(filename or request.request+'.wav')
        if destination.exists():
            self.session.fail(request)
            raise ValueError('Export destination must be new')
        self.pending=ExportPlan(request,destination)
        return self.pending

    def _fail(self,request,reason):
        self.session.fail(request)
        self.pending=None
        return {'status':'discarded','reason':reason}

    def complete(self,plan,*,reported_path,native_success,after,revision):
        if plan is None or plan!=self.pending:
            return {'status':'discarded','reason':'stale-completion'}
        if self.session.pending!=plan.request:
            self.pending=None
            return {'status':'discarded','reason':'invalidated-request'}
        if self.session.expire():
            self.pending=None
            return {'status':'discarded','reason':'timeout'}
        if native_success is not True:
            return self._fail(plan.request,'native-export-failed')
        try:
            path=Path(reported_path)
            if not path.is_absolute() or path!=plan.destination or path.resolve()!=plan.destination:
                return self._fail(plan.request,'wrong-export-path')
            if path.is_symlink() or not path.is_file():
                return self._fail(plan.request,'invalid-export-file')
            with path.open('rb') as stream:
                import os
                before=os.fstat(stream.fileno())
                if not 12<=before.st_size<=1024*1024:
                    return self._fail(plan.request,'invalid-export-size')
                raw=stream.read(1024*1024+1)
                last=os.fstat(stream.fileno())
            current=path.stat()
            signature=lambda s:(s.st_dev,s.st_ino,s.st_size,s.st_mtime_ns)
            if signature(before)!=signature(last) or signature(last)!=signature(current) or len(raw)!=last.st_size:
                return self._fail(plan.request,'export-file-changed')
        except (OSError,ValueError,TypeError):
            return self._fail(plan.request,'export-file-unavailable')
        self.pending=None
        return self.session.complete(plan.request,raw,after=after,revision=revision)

    def cancel(self):
        self.session.cancel()
        self.pending=None

    def close(self):
        self.pending=None
        self.session.close()
