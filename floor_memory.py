"""What this PC remembers about the Floor between launches of the editor.

Two things, both small and both checked again against the device before they are used:

* the preview that is on a block right now (which block, what it played before, the preview's
  slot name). If the editor closes while a preview is on a block, the Floor keeps that block on
  User IR 128; the next launch uses this record to put the earlier capture back.
* which capture each permanent User IR slot held when it was last read back, with the slot's
  name at that time. A slot whose name is unchanged is not read back again, so opening the NAM
  tab does not lock HX Edit while every slot is exported.
* which IR slots each preset used when it was last opened or saved in this editor, so clearing
  a slot can name the presets that use it. Only presets opened here are known.
* the side (All / Guitar / Bass) last picked in each block picker category's dropdown.

Nothing here is trusted on its own: the native side applies a record only while the device still
shows the same slot name, and a slot that reports a change is forgotten and read again.
"""

import json
import os
from datetime import date
from pathlib import Path
import re
from threading import Lock


BLOCK = re.compile(r'block\d{1,2}')
CONTENT = re.compile(r'[0-9a-f]{64}')
NAME_LIMIT = 255
ICON = re.compile(r'[a-z0-9_]{0,40}')   # capture_icons id ('' when none)


def _name(value):
    return isinstance(value, str) and 0 < len(value) <= NAME_LIMIT and '\0' not in value


def _slot(value):
    return type(value) is int and 1 <= value <= 127


def _side(category, value):
    return (isinstance(category, str) and 0 < len(category) <= 40 and '\0' not in category
            and type(value) is int and 0 <= value <= 999)


def _favorite(name):
    return isinstance(name, str) and 0 < len(name) <= 64 and '\0' not in name


def _favorite_slot(value):
    return type(value) is int and 1 <= value <= 128     # 128: a favorite saved during a preview


def _look(value):
    return (isinstance(value, dict) and _favorite_slot(value.get('slot')) and type(value.get('group')) is int
            and 1 <= value['group'] <= 5 and isinstance(value.get('icon'), str) and ICON.fullmatch(value['icon']) is not None)


def preset_usage(value):
    """{setlist, preset, name, slots (list or None)} from a 'presetUsage' message, or None."""
    if not isinstance(value, dict):
        return None
    setlist, preset, name, slots = (value.get(key) for key in ('setlist', 'preset', 'name', 'slots'))
    if not (type(setlist) is int and 0 <= setlist <= 31 and type(preset) is int and 0 <= preset <= 255
            and isinstance(name, str) and len(name) <= 48 and '\0' not in name):
        return None
    if slots is not None and not (isinstance(slots, list) and all(_slot(slot) for slot in slots)):
        return None
    return {'setlist': setlist, 'preset': preset, 'name': name,
            'slots': sorted(set(slots)) if slots is not None else None}


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
        self._presets = {}   # "setlist:preset" -> usage record + 'seen' (ISO date)
        self._sides = {}     # block picker category -> subcategory id last picked there
        self._favorites = {}  # HX Edit favorite name -> the capture slot it plays (saved from a NAM block here)
        self._looks = {}      # favorite name -> {slot, group, icon}: its picture before the slot check
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
        sides = data.get('pickerSides')
        if isinstance(sides, dict):
            self._sides = {key: value for key, value in sides.items() if _side(key, value)}
        favorites = data.get('favoriteSlots')
        if isinstance(favorites, dict):
            self._favorites = {name: slot for name, slot in favorites.items() if _favorite(name) and _favorite_slot(slot)}
        looks = data.get('favoriteLooks')
        if isinstance(looks, dict):
            self._looks = {name: look for name, look in looks.items() if name in self._favorites and _look(look)}
        presets = data.get('presets')
        if isinstance(presets, dict):
            for entry in presets.values():
                record = preset_usage(entry)
                if record:
                    record['seen'] = entry.get('seen') if isinstance(entry.get('seen'), str) else ''
                    self._presets[f"{record['setlist']}:{record['preset']}"] = record

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

    def sides(self):
        """{category: subcategory id} for the native side's starting memory."""
        with self.lock:
            return dict(self._sides)

    def favorites(self):
        """{favorite name: slot} for the native side's starting memory."""
        with self.lock:
            return dict(self._favorites)

    def looks(self):
        """{favorite name: {slot, group, icon}} for the native side's starting memory."""
        with self.lock:
            return {name: dict(look) for name, look in self._looks.items()}

    def presets(self):
        """[[setlist, preset, name, slots or None], …] for the native side's starting memory."""
        with self.lock:
            return [[entry['setlist'], entry['preset'], entry['name'], entry['slots']]
                    for _, entry in sorted(self._presets.items(), key=lambda item: (item[1]['setlist'], item[1]['preset']))]

    def receive(self, payload):
        """Apply a 'tonePreviewState', 'slotMemory' or 'presetUsage' message. False: not one of ours."""
        kind = payload.get('type') if isinstance(payload, dict) else None
        if kind == 'pickerSide':
            category, value = payload.get('category'), payload.get('value')
            if _side(category, value):
                with self.lock:
                    if self._sides.get(category) != value:
                        self._sides[category] = value
                        self._save()
            return True
        if kind == 'favoriteSlot':
            name, slot = payload.get('name'), payload.get('slot')
            if _favorite(name) and (slot is None or _favorite_slot(slot)):
                with self.lock:
                    if self._favorites.get(name) != slot:
                        if slot is None:
                            self._favorites.pop(name, None)
                            self._looks.pop(name, None)
                        else:
                            self._favorites[name] = slot
                        self._save()
            return True
        if kind == 'favoriteLook':
            name, look = payload.get('name'), payload.get('look')
            if _favorite(name) and _look(look):
                look = {'slot': look['slot'], 'group': look['group'], 'icon': look['icon']}
                with self.lock:
                    if name in self._favorites and self._looks.get(name) != look:
                        self._looks[name] = look
                        self._save()
            return True
        if kind == 'presetUsage':
            record = preset_usage(payload)
            if record:
                record['seen'] = date.today().isoformat()
                with self.lock:
                    self._presets[f"{record['setlist']}:{record['preset']}"] = record
                    self._save()
            return True
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
                'slots': {str(slot): entry for slot, entry in sorted(self._slots.items())},
                'presets': dict(sorted(self._presets.items())),
                'pickerSides': dict(sorted(self._sides.items())),
                'favoriteSlots': dict(sorted(self._favorites.items())),
                'favoriteLooks': dict(sorted(self._looks.items()))}
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
