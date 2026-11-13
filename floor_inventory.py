"""Verify fresh exports made by HX Edit itself. This module never opens USB.

The native observer owns transaction/connection validity. This side owns a
private, pre-armed export path and checks the completed file's sample payload.
Filename, IR name, and previously saved slot numbers are never identity proof.
"""
from hashlib import sha256
import math
from pathlib import Path
import struct
from threading import RLock
import time
from uuid import uuid4

from nam_inventory import is_nam_profile

FLOOR = 0x210001
MAX_EXPORT_BYTES = 1024 * 1024


def prime_library(library, converter, workspace, *, timeout=120):
    """Build a session-only identity index from existing local .nam originals.

    No catalog migration or disk slot cache: conversion is offline, each child
    already has a 30s bound, and the whole batch stops between captures at its
    deadline. Unsupported captures remain local with blank, unverified slots.
    """
    from nam_convert import convert_batch
    deadline = time.monotonic() + timeout
    result = {'indexed': 0, 'unsupported': [], 'timedOut': False}
    Path(workspace).mkdir(parents=True, exist_ok=True)
    for identity in library.entries():
        if time.monotonic() >= deadline:
            result['timedOut'] = True
            break
        try:
            entry = library.entries()[identity]
            if entry.get('format') == '.wav':
                library.register_payload(identity, library.folder / (identity + '.wav'))
                result['indexed'] += 1
                continue
            capture = library.folder / f'{identity}.nam'
            [wav] = convert_batch([capture], Path(workspace), Path(converter))
            library.register_payload(identity, wav)
            result['indexed'] += 1
        except (OSError, ValueError) as error:
            result['unsupported'].append({'id': identity, 'reason': str(error)})
    return result


def import_legacy_catalog(library, catalog_path, *, apply=False):
    """Plan/copy Candidate53's kept WAV library into a separate empty library.

    Original files, catalog and date-evidence sidecars are read-only. Slots are
    intentionally absent. Converted captures keep working, but cannot train a
    new model without their original .nam. apply=False performs no writes.
    """
    import json
    import re
    catalog_path = Path(catalog_path).resolve(strict=True)
    root = catalog_path.parent
    if library.catalog.exists() or library.folder.resolve() == root:
        raise ValueError('Legacy import requires a separate empty destination library')
    catalog = json.loads(catalog_path.read_text(encoding='utf-8'))
    dates = {}
    for filename in ('recovered-dates.json', 'recovered-file-dates.json'):
        sidecar = root / filename
        if sidecar.is_file():
            evidence = json.loads(sidecar.read_text(encoding='utf-8'))
            if evidence.get('schema') == 'hx-library-date-evidence-v1':
                dates.update(evidence.get('captures', {}))
    entries, files, skipped = {}, [], []
    for identity, record in catalog.get('captures', {}).items():
        if record.get('retention') != 'kept' or record.get('kind') != 'nam':
            continue
        if not re.fullmatch('[0-9a-f]{64}', identity):
            raise ValueError('Invalid legacy capture identity')
        try:
            source = (root / record['keptFile']).resolve(strict=True)
            if not source.is_relative_to(root) or source.suffix.lower() != '.wav':
                raise ValueError('Legacy file is outside its library')
            raw = source.read_bytes()
            if inspect_payload(raw) != {'contentId': identity, 'kind': 'nam'}:
                raise ValueError('Legacy capture content differs')
        except (KeyError, OSError, ValueError) as error:
            skipped.append({'id': identity, 'reason': str(error)})
            continue
        names = [name for name in record.get('names', []) if isinstance(name, str) and name.strip()]
        friendly = [name for name in names if not re.match(r'^T3K\s+\d+\s+[0-9a-f]+\s', name, re.I)]
        title = record.get('displayName') or (friendly or names or ['Unnamed capture'])[0]
        title = ' '.join(title.split())
        normalize = {'full-rig': 'amp-cab', 'studio': 'outboard'}
        kinds = {normalize.get(item.get('gear'), item.get('gear')) for item in record.get('gearEvidence', [])}
        gear = record.get('override') or (next(iter(kinds)) if len(kinds) == 1 else '')
        gear = normalize.get(gear, gear)
        from nam_library import GEARS, IR_NAME_LIMIT
        if gear not in GEARS:
            gear = ''
        evidence = dates.get(identity, {})
        added = str(record.get('addedAt') or evidence.get('day') or '')[:10]
        if added and not re.fullmatch(r'\d{4}-\d{2}-\d{2}', added):
            added = ''
        # A display name is not an identity. Strip only filename-illegal chars.
        ir_name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '', title)[:IR_NAME_LIMIT].rstrip(' .') or 'NAM'
        entries[identity] = dict(name=title, irName=ir_name, gear=gear, added=added,
                                 format='.wav', legacy=True, dateEvidence=evidence,
                                 gearEvidence=record.get('gearEvidence', []))
        files.append((identity, raw))
    if apply:
        if skipped:
            raise ValueError('Legacy import is incomplete; inspect the plan before copying')
        library.folder.mkdir(parents=True, exist_ok=True)
        for identity, raw in files:
            target = library.folder / (identity + '.wav')
            if target.exists() and target.read_bytes() != raw:
                raise ValueError('Existing destination capture conflicts')
            target.write_bytes(raw)
        library._write(entries)
        for identity, _ in files:
            library.register_payload(identity, library.folder / (identity + '.wav'))
    return {'eligible': len(entries) + len(skipped), 'copied': len(entries) if apply else 0,
            'ready': len(entries), 'skipped': skipped, 'entries': entries, 'applied': apply}


def inspect_payload(raw, *, native_export=False):
    """Canonical WAV identity; metadata/container padding do not affect it."""
    if len(raw) < 12 or len(raw) > MAX_EXPORT_BYTES or raw[:4] != b'RIFF' or raw[8:12] != b'WAVE':
        raise ValueError('Not a bounded RIFF WAV')
    end = struct.unpack_from('<I', raw, 4)[0] + 8
    if end != len(raw):
        raise ValueError('Truncated WAV or trailing bytes')
    pos, fmt, samples = 12, None, None
    while pos < end:
        if pos + 8 > end:
            raise ValueError('Truncated chunk header')
        name, size = struct.unpack_from('<4sI', raw, pos)
        pos += 8
        stop = pos + size
        # HX Edit 3.82 omits final padding on some LIST chunks. Only observed
        # native exports may use this quirk; sample and RIFF lengths stay exact.
        unpadded = native_export and name == b'LIST' and size & 1 and stop == end
        if stop + (size & 1) > end and not unpadded:
            raise ValueError('Truncated chunk')
        if name == b'fmt ':
            if fmt is not None or size < 16:
                raise ValueError('Ambiguous WAV format')
            fmt = struct.unpack_from('<HHIIHH', raw, pos)
        elif name == b'data':
            if samples is not None:
                raise ValueError('Multiple data chunks')
            samples = raw[pos:stop]
        pos = stop if unpadded else stop + (size & 1)
    if fmt is None or not samples:
        raise ValueError('Missing format or samples')
    encoding, channels, rate, byte_rate, align, bits = fmt
    if (encoding not in (1, 3) or not 1 <= channels <= 32 or not 1 <= rate <= 768000
            or bits not in (8, 16, 24, 32) or (encoding == 3 and bits != 32)
            or align != channels * bits // 8 or byte_rate != rate * align or len(samples) % align):
        raise ValueError('Unsupported WAV format')
    nam = False
    if encoding == 3:
        if not all(math.isfinite(value[0]) for value in struct.iter_unpack('<f', samples)):
            raise ValueError('Nonfinite samples')
        looks_nam = len(samples) >= 8 and struct.unpack_from('<ff', samples) == (.5, -.5)
        nam = fmt == (3, 1, 48000, 192000, 4, 32) and is_nam_profile(samples)
        if looks_nam and not nam:
            raise ValueError('Unrecognized NAM payload')
    return {'contentId': sha256(struct.pack('<HHIIHH', *fmt) + samples).hexdigest(),
            'kind': 'nam' if nam else 'ir'}


class FloorInventoryService:
    """One export at a time, including delayed UI completion, bounded to 30s.

    prepare(request) -> automaticSlotPrepared payload.
    receive(irExportObservation) -> {token,status,evidence?} when finished.
    Only send evidence back to Frida; its final epoch/revision check authorizes
    display. Do not publish Python's receipt directly as a verified mapping.
    """
    def __init__(self, root, *, clock=time.monotonic):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.clock, self.lock = clock, RLock()
        self.active = None
        self.closed = False

    def prepare(self, value):
        with self.lock:
            if self.closed:
                raise ValueError('Discovery closed')
            if (not isinstance(value.get('token'), str) or not 1 <= len(value['token']) <= 80
                    or type(value.get('slot')) is not int or not 1 <= value['slot'] <= 127
                    or type(value.get('device')) is not int or value['device'] != FLOOR
                    or type(value.get('revision')) is not int or value['revision'] < 0):
                raise ValueError('Invalid discovery request')
            if self.active is not None:
                raise ValueError('Export already pending')
            folder = self.root / uuid4().hex
            folder.mkdir()
            path = folder / 'capture.wav'
            self.active = {key: value[key] for key in ('token', 'slot', 'device', 'revision')}
            self.active.update(path=path, deadline=self.clock() + 30, submission=None)
            return {'token': value['token'], 'path': str(path)}

    def receive(self, event):
        with self.lock:
            active = self.active
            if self.closed or active is None or event.get('stage') == 'ready':
                return None
            # Other native exports are not our private transaction.
            if event.get('path') != str(active['path']):
                if event.get('request') != (active['submission'] or {}).get('request'):
                    return None
            try:
                if self.clock() >= active['deadline']:
                    raise ValueError('Export timed out')
                stage = event.get('stage')
                if stage == 'submitted':
                    if active['submission'] is not None:
                        raise ValueError('Duplicate submission')
                    # The worker can finish writing before Frida's submitted
                    # message reaches Python. Freshness was established when
                    # prepare created this unique private directory, before
                    # native dispatch; do not reject that legitimate fast path.
                    if (type(event.get('sequence')) is not int
                            or event['sequence'] < 1 or not isinstance(event.get('request'), str)
                            or not event['request']):
                        raise ValueError('Stale export target')
                    if any(event.get(key) != active[key] for key in ('slot', 'device', 'revision')):
                        raise ValueError('Export target changed')
                    active['submission'] = {key: event[key] for key in
                                            ('request', 'sequence', 'slot', 'device', 'revision', 'path')}
                    return None
                if stage not in ('awaiting-unlock', 'completed'):
                    raise ValueError('Native observation rejected')
                submitted = active['submission']
                if submitted is None or any(event.get(key) != value for key, value in submitted.items()):
                    raise ValueError('Completion identity changed')
                if type(event.get('result')) is not int or event['result'] != 0 or event.get('details') != [0]:
                    raise ValueError('Native export failed')
                if type(event['details'][0]) is not int:
                    raise ValueError('Invalid native result')
                if stage == 'awaiting-unlock':
                    return None  # worker return is not UI completion
                path = active['path']
                if path.is_symlink() or not path.is_file() or path.resolve().parent != path.parent:
                    raise ValueError('Export target replaced')
                before = path.stat()
                if not 0 < before.st_size <= MAX_EXPORT_BYTES:
                    raise ValueError('Export size invalid')
                with path.open('rb') as stream:
                    raw = stream.read(MAX_EXPORT_BYTES + 1)
                after = path.stat()
                if (before.st_size, before.st_mtime_ns, before.st_ino) != (after.st_size, after.st_mtime_ns, after.st_ino):
                    raise ValueError('Export changed during read')
                try:
                    found = inspect_payload(raw, native_export=True)
                except ValueError as error:
                    self.active = None
                    return {'token': active['token'], 'status': 'unidentified', 'reason': str(error)}
                result = {'token': active['token'], 'status': 'matched' if found['kind'] == 'nam' else 'ir'}
                if found['kind'] == 'nam':
                    result['evidence'] = {key: submitted[key] for key in ('request', 'sequence', 'slot', 'device', 'revision')}
                    result['evidence'].update(contentId=found['contentId'], automaticToken=active['token'])
            except (ValueError, OSError, KeyError, TypeError) as error:
                result = {'token': active['token'], 'status': 'discarded', 'reason': str(error)}
            self.active = None
            return result

    def connection_changed(self):
        with self.lock:
            self.active = None

    def cancel(self, _reason='discovery-stopped'):
        """Release a pre-arm after coordinator timeout/fault; late exports cannot publish."""
        self.connection_changed()

    def close(self):
        with self.lock:
            self.active = None
            self.closed = True
