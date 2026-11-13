"""What this PC remembers about the Floor between launches of the editor.

Two things, both small and both checked again against the device before they are used:

* the preview that is on a block right now (which block, what it played before, the preview's
  slot name). If the editor closes while a preview is on a block, the Floor keeps that block on
  User IR 128; the next launch uses this record to put the earlier capture back.
* which capture each permanent User IR slot held when it was last read back, with the slot's
  name at that time. A slot whose name is unchanged is not read back again, so opening the NAM
  tab does not lock HX Edit while every slot is exported.

Nothing here is trusted on its own: the native side applies a record only while the device still
shows the same slot name, and a slot that reports a change is forgotten and read again.
"""

import json
import os
from pathlib import Path
import re
from threading import Lock


BLOCK = re.compile(r'block\d{1,2}')
CONTENT = re.compile(r'[0-9a-f]{64}')
NAME_LIMIT = 255


def _name(value):
    return isinstance(value, str) and 0 < len(value) <= NAME_LIMIT and '\0' not in value


def _slot(value):
    return type(value) is int and 1 <= value <= 127


def preview_record(value):
    """The record as the native side sends it, or None when it is not one."""
    if (isinstance(value, dict) and value.get('dsp') in (0, 1) and type(value.get('dsp')) is int
            and isinstance(value.get('block'), str) and BLOCK.fullmatch(value['block'])
            and _slot(value.get('originalSlot')) and _name(value.get('name')) and len(value['name']) <= 64):
        return {key: value[key] for key in ('dsp', 'block', 'originalSlot', 'name')}
    return None


class FloorMemory:
    def __init__(self, folder):
        self.path = Path(folder) / 'floor-memory.json'
        self.lock = Lock()
        self._preview = None
        self._slots = {}
        self._load()

    def _load(self):
        try:
            data = json.loads(self.path.read_text(encoding='utf-8-sig'))
        except (OSError, ValueError):
            return
        if not isinstance(data, dict):
            return
        self._preview = preview_record(data.get('preview'))
        slots = data.get('slots')
        if isinstance(slots, dict):
            for key, entry in slots.items():
                try:
                    slot = int(key)
                except (TypeError, ValueError):
                    continue
                if self._valid(slot, entry):
                    self._slots[slot] = {'name': entry['name'], 'contentId': entry.get('contentId')}

    @staticmethod
    def _valid(slot, entry):
        return (_slot(slot) and isinstance(entry, dict) and _name(entry.get('name')) and
                (entry.get('contentId') is None or
                 (isinstance(entry.get('contentId'), str) and CONTENT.fullmatch(entry['contentId']))))

    def preview(self):
        with self.lock:
            return dict(self._preview) if self._preview else None

    def slots(self):
        """[[slot, name, contentId or None], …] for the native side's starting memory."""
        with self.lock:
            return [[slot, entry['name'], entry['contentId']] for slot, entry in sorted(self._slots.items())]

    def receive(self, payload):
        """Apply a 'tonePreviewState' or 'slotMemory' message. False: not one of ours."""
        kind = payload.get('type') if isinstance(payload, dict) else None
        if kind == 'tonePreviewState':
            record = preview_record(payload.get('state'))
            with self.lock:
                if record == self._preview:
                    return True
                self._preview = record
                self._save()
            return True
        if kind == 'slotMemory':
            slot = payload.get('slot')
            if not _slot(slot):
                return True
            with self.lock:
                if payload.get('forget') is True:
                    if self._slots.pop(slot, None) is None:
                        return True
                else:
                    entry = {'name': payload.get('name'), 'contentId': payload.get('contentId')}
                    if not self._valid(slot, entry) or self._slots.get(slot) == entry:
                        return True
                    self._slots[slot] = entry
                self._save()
            return True
        return False

    def _save(self):
        # Callers hold self.lock. A failed write only costs the next launch a slower start.
        data = {'preview': self._preview,
                'slots': {str(slot): entry for slot, entry in sorted(self._slots.items())}}
        temporary = self.path.with_name(f'floor-memory.{os.getpid()}.tmp')
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary.write_text(json.dumps(data, indent=1, ensure_ascii=False) + '\n', encoding='utf-8')
            temporary.replace(self.path)
        except OSError:
            pass
        finally:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
