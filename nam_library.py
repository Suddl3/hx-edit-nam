"""NAM captures kept on this PC: original .nam files plus a small JSON catalog."""

from datetime import date
from hashlib import sha256
from html import escape
import json
import os
from pathlib import Path
import shutil
from string import Template
from threading import Lock

from nam_convert import IR_NAME_LIMIT


FOLDER = Path(os.environ.get('LOCALAPPDATA', Path.home())) / 'HX Edit NAM' / 'NAM Library'
PAGE = Path(__file__).with_name('library_card.html')
GEARS = {'amp': 'Amp', 'amp-cab': 'Amp + cab', 'pedal': 'Pedal', 'preamp': 'Preamp',
         'cab': 'Cab capture', 'studio': 'Studio', 'outboard': 'Outboard', 'experimental': 'Experimental',
         'space': 'Space', 'pedal-amp': 'Pedal + amp', 'amp-pedal-cab': 'Pedal + amp + cab', '': 'Unsorted'}


def capture_gear(path):
    try:
        gear = json.loads(Path(path).read_text(encoding='utf-8')).get('metadata', {}).get('gear_type')
    except (OSError, ValueError, AttributeError):
        return ''
    # NAM trainer metadata names -> TONE3000 gear names used by the library.
    return {'amp': 'amp', 'preamp': 'amp', 'amp_cab': 'amp-cab', 'amp_pedal_cab': 'amp-cab',
            'pedal': 'pedal', 'pedal_amp': 'pedal', 'studio': 'outboard'}.get(gear, '')


class CatalogError(ValueError):
    """The catalog exists but could not be read, so it must not be written. A ValueError, which every
    caller that reports library failures to the user already handles."""


class Library:
    def __init__(self, folder=FOLDER):
        self.folder = Path(folder)
        self.catalog = self.folder / 'catalog.json'
        self.backup = self.folder / 'catalog.json.bak'
        self.lock = Lock()
        self._slot_inventory = {'entries': [], 'state': 'unverified', 'generation': -1}
        self._payload_index = {}
        self._pending_slots = {}
        self._listeners = set()
        self._view = dict(selected=None, query='', filter='*', sortKey='name', sortDirection=1, revision=0)

    def subscribe(self, callback):
        """Observe this collection, not a second catalog owned by a presentation."""
        with self.lock:
            self._listeners.add(callback)
        def unsubscribe():
            with self.lock:
                self._listeners.discard(callback)
        return unsubscribe

    def _changed(self):
        with self.lock:
            listeners = tuple(self._listeners)
        for callback in listeners:
            try:
                callback()
            except Exception:
                # A closed presentation must not undo a successfully kept capture.
                pass

    def view_state(self):
        with self.lock:
            return dict(self._view)

    def update_view(self, value):
        if not isinstance(value, dict):
            return False
        allowed = {'selected', 'query', 'filter', 'sortKey', 'sortDirection', 'revision'}
        if set(value) - allowed:
            return False
        candidate = self.view_state()
        candidate.update({key: item for key, item in value.items() if key != 'revision'})
        if (candidate['selected'] is not None and (not isinstance(candidate['selected'], str) or candidate['selected'] not in self._read())
                or not isinstance(candidate['query'], str) or len(candidate['query']) > 300
                or candidate['filter'] not in (*GEARS, '*')
                or candidate['sortKey'] not in ('name', 'gear', 'irSlots', 'added')
                or type(candidate['sortDirection']) is not int or candidate['sortDirection'] not in (-1, 1)):
            return False
        with self.lock:
            if all(candidate[key] == self._view[key] for key in allowed - {'revision'}):
                return True
            candidate['revision'] = self._view['revision'] + 1
            self._view = candidate
        self._changed()
        return True

    def _read(self, for_write=False):
        """The catalog as a dict. A missing catalog is an empty library; an unreadable one is not.

        Readers get {} for a catalog that cannot be read right now, so a view can stay open. A writer
        (for_write) gets CatalogError instead: saving over a catalog we could not read would replace
        every kept name, type, date and source link with the one entry being written."""
        try:
            # utf-8-sig: a catalog saved by another tool with a byte-order mark is still this catalog.
            text = self.catalog.read_text(encoding='utf-8-sig')
        except FileNotFoundError:
            return {}
        except (OSError, ValueError) as error:
            if for_write:
                raise CatalogError(f'The NAM library catalog could not be read ({error}). Nothing was changed.')
            return {}
        try:
            entries = json.loads(text)
        except ValueError:
            entries = None
        if not isinstance(entries, dict):
            if for_write:
                raise CatalogError('The NAM library catalog is damaged. Nothing was changed; '
                                   f'restore {self.catalog.name} from {self.backup.name} in {self.folder}.')
            return {}
        return entries

    def entries(self):
        """Local captures, decorated with this running editor's evidence only."""
        entries = self._read()
        with self.lock:
            payload_index = {identity: set(payloads) for identity, payloads in self._payload_index.items()}
            slot_inventory = dict(self._slot_inventory)
            pending_slots = dict(self._pending_slots)
        for identity, entry in entries.items():
            entry['canTrain'] = entry.get('format', '.nam') == '.nam'
            payloads = payload_index.get(identity, set())
            entry['irSlots'] = sorted({slot for slot, content in slot_inventory['entries']
                                       if content in payloads})
            entry['slotState'] = slot_inventory['state']
            entry['pendingSlots'] = sorted(slot for slot, capture in pending_slots.items() if capture == identity)
        return entries

    def mark_pending(self, identity, slot):
        """Acknowledge a successful permanent import; amber is not readback proof."""
        if type(slot) is not int or not 1 <= slot <= 127 or identity not in self._read():
            return False
        with self.lock:
            self._pending_slots[slot] = identity
        self._changed()
        return True

    def _source(self, identity, entry):
        if not isinstance(identity, str) or len(identity) != 64 or any(c not in '0123456789abcdef' for c in identity):
            raise ValueError('Invalid local capture identity')
        extension = entry.get('format', '.nam')
        if extension not in ('.nam', '.wav'):
            raise ValueError('Unsupported local capture format')
        source = self.folder / (identity + extension)
        if source.is_symlink() or source.resolve().parent != self.folder.resolve():
            raise ValueError('Local capture path changed')
        return source, extension

    def register_payload(self, identity, wav):
        """Index a known conversion without changing the user's capture/catalog."""
        from floor_inventory import inspect_payload
        entry = self._read().get(identity)
        if entry is None:
            raise ValueError('Library capture identity changed')
        source, extension = self._source(identity, entry)
        if not source.is_file():
            raise ValueError('Library capture identity changed')
        original = source.read_bytes()
        actual = inspect_payload(original)['contentId'] if extension == '.wav' else sha256(original).hexdigest()
        if actual != identity:
            raise ValueError('Library capture identity changed')
        found = inspect_payload(Path(wav).read_bytes())
        if found['kind'] != 'nam':
            raise ValueError('Converted capture is not a NAM payload')
        with self.lock:
            self._payload_index.setdefault(identity, set()).add(found['contentId'])
        self._changed()
        return found['contentId']

    def payload_metadata(self):
        entries = self._read()
        with self.lock:
            payload_index = {identity: set(payloads) for identity, payloads in self._payload_index.items()}
        return {payload: dict(id=identity, name=entries[identity].get('name', ''),
                              gear=entries[identity].get('gear', ''))
                for identity, payloads in payload_index.items() if identity in entries
                for payload in payloads}

    def set_slot_inventory(self, value):
        """Consume the native observer's accepted, atomic view; never load disk mappings."""
        if (not isinstance(value, dict) or type(value.get('epoch')) is not int
                or value['epoch'] < 0
                or value.get('state') not in ('verified', 'refreshing', 'unverified', 'partial')):
            return False
        rows = value.get('entries')
        if not isinstance(rows, list):
            return False
        clean = []
        for row in rows:
            if (not isinstance(row, (list, tuple)) or len(row) != 2 or type(row[0]) is not int
                    or not 1 <= row[0] <= 127 or not isinstance(row[1], str)
                    or len(row[1]) != 64 or any(c not in '0123456789abcdef' for c in row[1])
                    or any(slot == row[0] for slot, _ in clean)):
                return False
            clean.append(list(row))
        with self.lock:
            if value['epoch'] < self._slot_inventory['generation']:
                return False
            self._slot_inventory = {'generation': value['epoch'], 'state': value['state'], 'entries': clean}
            if value['state'] in ('verified', 'partial', 'unverified'):
                self._pending_slots.clear()
        self._changed()
        return True

    def _write(self, entries):
        """Replace the catalog in one step, keeping the version it replaces as catalog.json.bak.

        Callers hold self.lock and read with for_write=True first, so the file being backed up is one
        that parsed a moment ago."""
        self.folder.mkdir(parents=True, exist_ok=True)
        temporary = self.catalog.with_name(f'catalog.{os.getpid()}.tmp')
        try:
            with open(temporary, 'w', encoding='utf-8') as handle:
                handle.write(json.dumps(entries, indent=1, ensure_ascii=False) + '\n')
                handle.flush()
                os.fsync(handle.fileno())
            if self.catalog.is_file():
                shutil.copy2(self.catalog, self.backup)
            temporary.replace(self.catalog)
        finally:
            temporary.unlink(missing_ok=True)

    def add(self, capture, *, name=None, gear=None, source=''):
        """Keep a .nam file; the same capture added twice stays one entry with its first date."""
        data = Path(capture).read_bytes()
        identity = sha256(data).hexdigest()
        ir_name = Path(capture).stem[:IR_NAME_LIMIT].rstrip(' .') or 'NAM'
        with self.lock:
            entries = self._read(for_write=True)
            entry = entries.setdefault(identity, {'added': date.today().isoformat()})
            entry.setdefault('name', name or Path(capture).stem)
            entry.setdefault('irName', ir_name)
            entry.setdefault('gear', gear if gear is not None else capture_gear(capture))
            if source:
                entry['source'] = source
            self.folder.mkdir(parents=True, exist_ok=True)
            if not (self.folder / f'{identity}.nam').is_file():
                (self.folder / f'{identity}.nam').write_bytes(data)
            self._write(entries)
        self._changed()
        return identity

    def update(self, identity, *, gear=None, name=None):
        with self.lock:
            entries = self._read(for_write=True)
            if identity not in entries:
                raise KeyError(identity)
            if gear is not None:
                if gear not in GEARS:
                    raise ValueError(f'Unknown type: {gear}')
                entries[identity].setdefault('sourceGear', entries[identity].get('gear', ''))
                entries[identity]['gear'] = gear
                entries[identity]['gearOverride'] = gear
            if name is not None and name.strip():
                entries[identity]['name'] = name.strip()[:200]
            self._write(entries)
        self._changed()

    def reset_gear(self, identity):
        with self.lock:
            entries = self._read(for_write=True)
            entry = entries[identity]
            if 'sourceGear' in entry:
                entry['gear'] = entry['sourceGear']
            entry.pop('gearOverride', None)
            self._write(entries)
        self._changed()

    def remove(self, identity):
        with self.lock:
            entries = self._read(for_write=True)
            entry = entries.pop(identity, None)
            if entry is not None:
                source, _ = self._source(identity, entry)
                # Catalog first: a failed write must not leave a record whose file is already gone.
                self._write(entries)
                source.unlink(missing_ok=True)
            if self._view['selected'] == identity:
                self._view.update(selected=None, revision=self._view['revision'] + 1)
        self._changed()

    def export(self, identity, directory):
        """Copy a kept capture under its IR name so HX Edit shows a readable slot name."""
        entry = self.entries()[identity]
        source, extension = self._source(identity, entry)
        name = entry.get('irName')
        if (not isinstance(name, str) or not name or name in ('.', '..') or
                any(ord(c) < 32 or c in '<>:"/\\|?*' for c in name) or name != name.rstrip(' .')):
            raise ValueError('Local capture export name is not a filename')
        folder = Path(directory).resolve()
        destination = folder / (name + extension)
        if destination.is_symlink() or destination.resolve().parent != folder:
            raise ValueError('Local capture export path changed')
        raw = source.read_bytes()
        if extension == '.wav':
            from floor_inventory import inspect_payload
            if inspect_payload(raw) != {'contentId': identity, 'kind': 'nam'}:
                raise ValueError('Kept WAV no longer matches its library identity')
        elif sha256(raw).hexdigest() != identity:
            raise ValueError('Kept NAM no longer matches its library identity')
        folder.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(raw)
        return destination


def render_library(entries, view_state=None):
    rows = [dict(id=key, name=value.get('name', ''), gear=value.get('gear', ''),
                 gearLabel=GEARS.get(value.get('gear', ''), 'Unsorted'), aliases=[value.get('irName', '')],
                 added=value.get('added', ''), source=value.get('source', ''),
                 irSlots=value.get('irSlots', []), slotState=value.get('slotState', 'unverified'),
                 canTrain=value.get('canTrain', True), pendingSlots=value.get('pendingSlots', []))
            for key, value in entries.items()]
    options = ''.join(f'<option value="{escape(key)}">{escape(label)}</option>' for key, label in GEARS.items())
    # JSON inside <script>: escape "<" so a capture name cannot close the element.
    data = json.dumps(rows, ensure_ascii=False).replace('<', '\\u003c')
    view = json.dumps(view_state or {}, ensure_ascii=False).replace('<', '\\u003c')
    return Template(PAGE.read_text(encoding='utf-8')).substitute(rows=data, gears=options, view=view)
