"""Host routing for the navigation candidate; all device I/O remains in HX Edit.

The native observer, rather than a completed file alone, decides whether export
evidence is still current. Only its accepted inventory reaches the library.
"""
from pathlib import Path

from device_connection_watch import ConnectionWatch, floor_presence
from floor_inventory import FloorInventoryService


class NavigationHost:
    def __init__(self, script, workspace, library, detached, *, service=None, watch_factory=ConnectionWatch):
        self.script, self.library, self.detached = script, library, detached
        self.service = service or FloorInventoryService(Path(workspace) / 'slot-exports')
        self.closed = False
        self.watch = watch_factory(self.connection_changed, presence=floor_presence)

    def post(self, kind, payload):
        if not self.closed and not self.detached.is_set():
            self.script.post({'type': kind, 'payload': payload})

    def connection_changed(self, value):
        self.service.connection_changed()
        self.post('toneDeviceChange', value)
        self.post('hostDeviceChange', value)

    def metadata_changed(self):
        self.post('floorPayloadMetadata', self.library.payload_metadata())

    def start(self):
        return self.watch.start()

    def receive(self, payload):
        if self.closed or self.detached.is_set():
            return False
        kind = payload.get('type')
        if kind == 'automaticSlotPrepare':
            try:
                result = self.service.prepare(payload)
            except (ValueError, OSError) as error:
                result = {'token': payload.get('token'), 'error': str(error)}
            self.post('automaticSlotPrepared', result)
        elif kind == 'irExportObservation':
            result = self.service.receive(payload)
            if result:
                if result.get('evidence'):
                    self.post('librarySlotEvidence', result['evidence'])
                else:
                    self.post('automaticSlotResult', result)
        elif kind == 'floorInventory':
            self.library.set_slot_inventory(payload)
        elif kind == 'automaticSlotStatus':
            # A failed/invalidated native transaction cannot be reused later.
            if payload.get('state') in ('disconnected', 'closed', 'discovery-timeout',
                    'preparation-failed', 'preparation-cancelled', 'export-unavailable', 'verification-stopped',
                    'inventory-changed-during-discovery', 'invalid-inventory',
                    'native-discovery-fault', 'native-observer-unavailable', 'connection-monitor-failed'):
                self.service.connection_changed()
        else:
            return False
        return True

    def close(self):
        if self.closed:
            return
        self.closed = True
        try:
            self.watch.close()
        finally:
            self.service.close()
