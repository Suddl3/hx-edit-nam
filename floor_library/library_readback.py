"""Offline core for future editor-owned export readback. No USB/native calls.

The live adapter is deliberately not wired. An adapter must supply a fresh device
session, occupied-slot inventory/revision, correlated export completion and a
post-export inventory observation. Saved reports are never startup verification.
"""
from copy import deepcopy
from dataclasses import dataclass
import time
from uuid import uuid4

from library_catalog import inspect_wav


def match_export(raw, catalog):
    if not isinstance(raw, bytes) or not 12 <= len(raw) <= 1024 * 1024:
        raise ValueError('Export must be a bounded WAV byte buffer')
    found = inspect_wav(raw, native_export=True)
    record = catalog['captures'].get(found['contentId'])
    matched = (found['kind'] == 'nam' and record is not None
               and record.get('kind') == 'nam' and record.get('retention') != 'preview')
    return {**found, 'status': 'matched' if matched else 'unmatched-nam' if found['kind']=='nam' else 'ir'}


def inventory_rows(rows):
    result = {}
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError('Inventory row must be an object')
        slot = row.get('slot')
        if type(slot) is not int or not 1 <= slot <= 128 or slot in result:
            raise ValueError('Invalid or duplicate inventory slot')
        if type(row.get('occupied')) is not bool or not isinstance(row.get('name'), str):
            raise ValueError('Inventory needs explicit occupancy and native name')
        result[slot] = {'slot':slot, 'occupied':row['occupied'], 'name':row['name']}
    return result


@dataclass(frozen=True)
class ExportRequest:
    session: str
    request: str
    revision: int
    slot: int


class ReadbackSession:
    """Serial request/match/invalidate state machine; never schedules device work."""
    def __init__(self, device_key, catalog, *, clock=time.monotonic, timeout=5):
        if not isinstance(device_key,str) or not device_key:
            raise ValueError('Fresh device identity is required')
        if type(timeout) not in (int,float) or not 0 < timeout <= 30:
            raise ValueError('Bounded export timeout required')
        self.device_key=device_key
        self.session=uuid4().hex
        self.catalog=deepcopy(catalog)
        self.clock,self.timeout=clock,timeout
        self.revision=-1
        self.inventory={}
        self.results={}
        self.pending=None
        self.deadline=None
        self.closed=False
        self.blocked=False

    def set_inventory(self, rows, revision):
        if self.closed:raise ValueError('Session closed')
        if type(revision) is not int or revision <= self.revision:
            raise ValueError('Inventory revision must advance')
        checked=inventory_rows(rows)
        self.inventory=checked
        self.revision=revision
        self.results={}
        self.pending=None
        self.deadline=None
        # A timeout/error is not cleared by polling inventory. Start a new
        # explicitly recovered connection/session before sending more requests.

    def begin(self, slot):
        if self.closed or self.blocked:raise ValueError('Readback session is stopped')
        if self.pending is not None:raise ValueError('Export already in flight')
        if type(slot) is not int or slot not in self.inventory or not self.inventory[slot]['occupied']:
            raise ValueError('Readback needs a freshly observed occupied slot')
        request=ExportRequest(self.session,uuid4().hex,self.revision,slot)
        self.pending=request
        self.deadline=self.clock()+self.timeout
        return request

    def expire(self):
        if self.pending is not None and self.clock()>=self.deadline:
            self.pending=None
            self.deadline=None
            self.blocked=True
            return True
        return False

    def complete(self, request, raw, *, after, revision):
        if self.closed or request!=self.pending or request is None:
            return {'status':'discarded','reason':'stale-request'}
        if self.expire():return {'status':'discarded','reason':'timeout'}
        self.pending=None
        self.deadline=None
        if type(revision) is not int or revision!=request.revision:
            self.results={}
            self.inventory={}
            return {'status':'discarded','reason':'slot-changed'}
        try:
            after=inventory_rows([after])[request.slot]
        except (KeyError,TypeError,ValueError):
            self.results.pop(request.slot,None)
            self.inventory.pop(request.slot,None)
            return {'status':'discarded','reason':'invalid-post-export-inventory'}
        if after!=self.inventory[request.slot]:
            self.results.pop(request.slot,None)
            self.inventory.pop(request.slot,None)
            return {'status':'discarded','reason':'slot-changed'}
        try:
            result=match_export(raw,self.catalog)
        except ValueError:
            result={'status':'unidentified','kind':'unidentified'}
        result={**result,'slot':request.slot,'revision':revision,'session':self.session,
                'previewSlot':request.slot==128}
        self.results[request.slot]=result
        return deepcopy(result)

    def fail(self, request):
        if request==self.pending and request is not None:
            self.pending=None
            self.deadline=None
            self.blocked=True

    def cancel(self):
        self.pending=None
        self.deadline=None

    def close(self):
        self.cancel()
        self.closed=True
        self.results={}
        self.inventory={}

    def slot_map(self):
        mappings={}
        for slot,row in sorted(self.results.items()):
            if row['status']=='matched' and slot!=128:
                mappings.setdefault(row['contentId'],[]).append(slot)
        return mappings
