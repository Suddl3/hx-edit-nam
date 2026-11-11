"""Offline, content-addressed library. Never opens USB or infers type from a name.

Slot observations are supplied by a separate reader. A cached slot is not proof
that a capture is still installed there. Metadata survives slot moves because
it belongs to the sample payload, not the WAV filename or physical slot.
"""
import argparse
import hashlib
import json
import math
import os
import re
import tempfile
from pathlib import Path
import sqlite3
import struct
from threading import Lock

GEAR = {'amp': 'amp-only', 'amp-cab': 'amp+cab', 'full-rig': 'amp+cab',
        'pedal': 'pedals', 'preamp': 'preamp', 'cab': 'cab',
        'pedal-amp': 'pedal+amp', 'amp-pedal-cab': 'pedal+amp+cab',
        'studio': 'studio', 'space': 'space', 'outboard': 'outboard',
        'experimental': 'experimental'}


def inspect_wav(raw, *, native_export=False):
    """Strict RIFF parsing, canonical sample identity, current A2 Lite signature.

    Container metadata is excluded from identity. Truncated, nonfinite,
    unsupported and NAM-like malformed payloads stay unidentified, not IRs.
    """
    if len(raw) < 12 or raw[:4] != b'RIFF' or raw[8:12] != b'WAVE':
        raise ValueError('Not a RIFF WAV')
    end = struct.unpack_from('<I', raw, 4)[0] + 8
    if end != len(raw):
        raise ValueError('Truncated WAV or trailing bytes')
    pos, fmt, data = 12, None, None
    while pos < end:
        if pos + 8 > end:
            raise ValueError('Truncated chunk header')
        name, size = struct.unpack_from('<4sI', raw, pos)
        pos += 8
        stop = pos + size
        # Pinned HX Edit exports may omit the final padding byte on LIST
        # metadata. Accept only this observed container quirk, only in readback:
        # the LIST payload and RIFF length must still be complete and exact.
        unpadded_list = (native_export and name == b'LIST' and size & 1
                         and stop == end)
        if stop + (size & 1) > end and not unpadded_list:
            raise ValueError('Truncated chunk')
        if name == b'fmt ':
            if fmt is not None or size < 16:
                raise ValueError('Ambiguous WAV format')
            fmt = struct.unpack_from('<HHIIHH', raw, pos)
        if name == b'data':
            if data is not None:
                raise ValueError('Multiple data chunks')
            data = raw[pos:stop]
        pos = stop if unpadded_list else stop + (size & 1)
    if fmt is None or not data:
        raise ValueError('Missing WAV format or samples')
    encoding, channels, rate, byte_rate, align, bits = fmt
    if (encoding not in (1, 3) or channels < 1 or rate < 1 or bits not in (8, 16, 24, 32)
            or (encoding == 3 and bits != 32) or align != channels * bits // 8
            or byte_rate != rate * align or len(data) % align):
        raise ValueError('Unsupported WAV format')
    nam = False
    if encoding == 3:
        samples = struct.unpack('<' + 'f' * (len(data) // 4), data)
        if not all(math.isfinite(v) for v in samples):
            raise ValueError('Nonfinite samples')
        signature = len(samples) >= 2 and samples[:2] == (0.5, -0.5)
        if signature:
            nam = (fmt == (3, 1, 48000, 192000, 4, 32) and len(samples) == 2048
                   and samples[2] == 3 and samples[3] in (1, 2, 4, 8)
                   and struct.unpack_from('<I', data, 16)[0] == 0x3FE9E000)
            if not nam:
                raise ValueError('Unrecognized NAM-like payload; keep unidentified')
    identity = hashlib.sha256(struct.pack('<HHIIHH', *fmt) + data).hexdigest()
    return {'contentId': identity, 'kind': 'nam' if nam else 'ir'}


def empty_catalog():
    return {'schema': 'hx-library-catalog-v1', 'captures': {}}


def add_wav(catalog, raw, name, *, gear=None, provenance='local', override=False):
    found = inspect_wav(raw)
    record = catalog['captures'].setdefault(found['contentId'], {
        **found, 'names': [], 'gearEvidence': [], 'override': None})
    if name and name not in record['names']:
        record['names'].append(name)
    if isinstance(gear, str) and gear.strip():
        gear = gear.strip().lower().replace('_', '-')
        if override:
            if gear not in GEAR:
                raise ValueError('Unknown manual category')
            record['override'] = gear
        else:
            evidence = {'gear': gear, 'source': provenance}
            if evidence not in record['gearEvidence']:
                record['gearEvidence'].append(evidence)
    return found['contentId']


def category(record):
    if record['kind'] == 'ir':
        return 'irs'
    if record['override'] is not None:
        return GEAR[record['override']]
    kinds = {GEAR.get(e['gear'], 'unidentified') for e in record['gearEvidence']}
    return next(iter(kinds)) if len(kinds) == 1 else 'unidentified'


def classification_revision(record):
    """Only classification evidence participates; slot/file refreshes do not."""
    data = {key: record.get(key) for key in ('override', 'gearEvidence')}
    return hashlib.sha256(json.dumps(data, sort_keys=True, ensure_ascii=False).encode('utf-8')).hexdigest()


def import_sidecar(catalog, path):
    path = Path(path)
    source = json.loads(path.read_text(encoding='utf-8-sig'))
    if source.get('schema') != 'floor-tone-source-v1':
        raise ValueError('Unsupported source record')
    wav = path.with_suffix('').with_suffix('.wav')
    raw = wav.read_bytes()
    if hashlib.sha256(raw).hexdigest() != source.get('wavSha256'):
        raise ValueError('Source record does not match WAV')
    return add_wav(catalog, raw, source.get('model', {}).get('name') or wav.stem,
                   gear=source.get('tone', {}).get('gear'), provenance='tone3000')


def import_legacy_library(catalog, path):
    """Read HX Unbound's existing library, ignoring its historical slot guesses."""
    db = sqlite3.connect(Path(path).resolve().as_uri() + '?mode=ro', uri=True)
    count = 0
    try:
        for key, name, wav, gear, override in db.execute(
                'SELECT id,name,wav,file_gear,override FROM captures WHERE wav IS NOT NULL'):
            add_wav(catalog, wav, name, gear=gear, provenance='nam-metadata')
            for (tone_gear,) in db.execute('SELECT tone_gear FROM sources WHERE id=?', (key,)):
                add_wav(catalog, wav, name, gear=tone_gear, provenance='tone3000')
            # Legacy overrides are display labels, not gear codes.
            legacy = {'Amp head': 'amp', 'Amp + cab': 'amp-cab', 'Pedal': 'pedal'}
            if override in legacy:
                add_wav(catalog, wav, name, gear=legacy[override], override=True)
            count += 1
    finally:
        db.close()
    return count


def reconcile(catalog, observations):
    """Build a 128-slot view from *current-session* evidence supplied by a reader.

    Each observed row has slot (1-based), occupied (bool), name, and optionally
    contentId from freshly read samples. Name matches NEVER enable a selection.
    Unobserved slots are unknown, not free. This function performs no transfers.
    """
    rows = {i: {'slot': i, 'state': 'unread', 'category': 'unidentified',
                'selectable': False} for i in range(1, 129)}
    seen = set()
    for item in observations:
        slot = item['slot']
        if type(slot) is not int or not 1 <= slot <= 128 or slot in seen:
            raise ValueError('Invalid or duplicate slot')
        if type(item.get('occupied')) is not bool:
            raise ValueError('Occupancy must be explicit')
        seen.add(slot)
        row = rows[slot]
        if not item['occupied']:
            row.update(state='empty', category='empty')
            continue
        row.update(state='unidentified', name=item.get('name', ''))
        content_id = item.get('contentId')
        record = catalog['captures'].get(content_id)
        if record:
            row.update(state=record['kind'], contentId=content_id,
                       category=category(record), selectable=record['kind'] == 'nam')
    result = list(rows.values())
    counts = {state: sum(r['state'] == state for r in result)
              for state in ('nam', 'ir', 'unidentified', 'empty', 'unread')}
    return {'slots': result, 'counts': counts, 'complete': not counts['unread']}


def atomic_write(path, raw):
    """Commit one complete file; failed writes never replace the previous file."""
    path = Path(path)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix='.' + path.name + '.',
                                         suffix='.tmp', delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass  # Unreferenced staging residue is safer than hiding the write error.


def save(catalog, path):
    raw = (json.dumps(catalog, indent=2, ensure_ascii=False) + '\n').encode('utf-8')
    atomic_write(path, raw)


def load(path):
    data = json.loads(Path(path).read_text(encoding='utf-8'))
    if data.get('schema') != 'hx-library-catalog-v1':
        raise ValueError('Unsupported catalog')
    return data


class CatalogStore:
    """One editor session's persistent local index; no device slot assignments."""
    def __init__(self, path):
        self.path = Path(path)
        self.lock = Lock()

    def classify(self, identity, gear, expected_revision):
        """Reversible local override, serialized with import/keep operations."""
        if (not isinstance(identity, str) or not re.fullmatch('[0-9a-f]{64}', identity)
                or not isinstance(expected_revision, str)
                or not re.fullmatch('[0-9a-f]{64}', expected_revision)):
            raise ValueError('Invalid classification request')
        if gear is not None and (not isinstance(gear, str) or gear not in GEAR):
            raise ValueError('Unknown manual category')
        with self.lock:
            catalog = load(self.path)
            record = catalog['captures'].get(identity)
            if not record or record.get('kind') != 'nam' or record.get('retention') == 'preview':
                raise ValueError('Select a kept NAM capture')
            if classification_revision(record) != expected_revision:
                raise ValueError('Classification changed. Review the refreshed type and try again.')
            record['override'] = gear
            save(catalog, self.path)
            return classification_revision(record)

    def record(self, wav_path, *, gear=None, provenance='local', name=None):
        path = Path(wav_path)
        raw = path.read_bytes()
        with self.lock:
            catalog = load(self.path) if self.path.exists() else empty_catalog()
            identity = add_wav(catalog, raw, name or path.stem, gear=gear, provenance=provenance)
            if isinstance(name, str) and name.strip():
                catalog['captures'][identity]['displayName'] = ' '.join(name.split())
            self.path.parent.mkdir(parents=True, exist_ok=True)
            save(catalog, self.path)
        return identity


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--legacy-library', type=Path)
    p.add_argument('--sidecars', type=Path)
    p.add_argument('--output', type=Path, required=True)
    a = p.parse_args()
    catalog = load(a.output) if a.output.exists() else empty_catalog()
    if a.legacy_library:
        import_legacy_library(catalog, a.legacy_library)
    if a.sidecars:
        for sidecar in sorted(a.sidecars.rglob('*.source.json')):
            import_sidecar(catalog, sidecar)
    save(catalog, a.output)
    print(json.dumps({'captures': len(catalog['captures']), 'categories': {
        name: sum(category(r) == name for r in catalog['captures'].values())
        for name in sorted({category(r) for r in catalog['captures'].values()})},
        'deviceAccess': False}, indent=2))
