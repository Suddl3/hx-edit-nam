"""Resolve an explicit local-library selection. No device, account or network IO."""
import hashlib
import json
from pathlib import Path
import re

from library_catalog import inspect_wav, load, save
from nam_library_view import library_rows, capture_device_name


def resolve_capture(catalog_path, content_id, workspace):
    if not isinstance(content_id, str) or not re.fullmatch(r'[0-9a-f]{64}', content_id):
        raise ValueError('Select a valid kept capture')
    catalog_path = Path(catalog_path).resolve()
    catalog = load(catalog_path)
    record = catalog['captures'].get(content_id)
    if not record or record.get('retention') != 'kept' or not record.get('keptFile'):
        raise ValueError('This entry has no kept capture file. Add it to Library first.')
    path = (catalog_path.parent / record['keptFile']).resolve(strict=True)
    if not path.is_relative_to(catalog_path.parent):
        raise ValueError('Kept capture path is outside the library')
    raw = path.read_bytes()
    inspected = inspect_wav(raw)
    if inspected['contentId'] != content_id or inspected['kind'] != 'nam':
        raise ValueError('Kept capture content no longer matches the library')
    item = next(row for row in library_rows(catalog) if row['contentId'] == content_id)
    stem = capture_device_name(item['title'], item['category'], content_id)
    folder = Path(workspace).resolve() / 'library-loads'
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / (stem + '.wav')
    if target.exists() and target.read_bytes() != raw:
        raise ValueError('Library staging file conflicts with the kept capture')
    if not target.exists():
        target.write_bytes(raw)
    if record.get('sourceFile'):
        sidecar_path = (catalog_path.parent / record['sourceFile']).resolve(strict=True)
        if not sidecar_path.is_relative_to(catalog_path.parent):
            raise ValueError('Source metadata path is outside the library')
        source = json.loads(sidecar_path.read_text(encoding='utf-8-sig'))
        if source.get('schema') != 'floor-tone-source-v1' or source.get('wavSha256') != hashlib.sha256(raw).hexdigest():
            raise ValueError('Source metadata does not match the kept capture')
        target.with_suffix('.source.json').write_text(json.dumps(source) + '\n', encoding='utf-8')
    return {'file': str(target), 'contentId': content_id, 'title': item['title']}


def hydrate_kept_files(catalog_path, candidates, *, apply=False):
    """Relink legacy retained entries by exact payload; never add audition entries."""
    catalog_path = Path(catalog_path).resolve()
    catalog = load(catalog_path)
    wanted = {key for key, record in catalog['captures'].items()
              if record['kind'] == 'nam' and record.get('retention') != 'preview'
              and not record.get('keptFile')}
    found = {}
    for candidate in candidates:
        candidate = Path(candidate)
        try:
            if candidate.suffix.lower() != '.wav' or candidate.stat().st_size > 1024 * 1024:
                continue
            raw = candidate.read_bytes()
            key = inspect_wav(raw)['contentId']
        except (ValueError, OSError):
            continue
        if key in wanted and key not in found:
            found[key] = (candidate, raw)
    if apply and found:
        folder = catalog_path.parent / 'kept-captures'
        folder.mkdir(parents=True, exist_ok=True)
        for key, (candidate, raw) in found.items():
            destination = folder / (key + '.wav')
            if destination.exists() and inspect_wav(destination.read_bytes())['contentId'] != key:
                raise ValueError('Existing kept file has conflicting content')
            destination.write_bytes(raw)
            record = catalog['captures'][key]
            record['keptFile'] = str(destination.relative_to(catalog_path.parent))
            record['retention'] = 'kept'
            sidecar = candidate.with_suffix('.source.json')
            if sidecar.is_file():
                try:
                    source = json.loads(sidecar.read_text(encoding='utf-8-sig'))
                    if source.get('schema') == 'floor-tone-source-v1' and source.get('wavSha256') == hashlib.sha256(raw).hexdigest():
                        target = destination.with_suffix('.source.json')
                        target.write_text(json.dumps({k: source.get(k) for k in
                            ('schema','tone','model','classification','wavSha256')}) + '\n', encoding='utf-8')
                        record['sourceFile'] = str(target.relative_to(catalog_path.parent))
                except (ValueError, OSError):
                    pass  # The capture identity remains verified; do not invent source metadata.
        save(catalog, catalog_path)
    return {'eligible': len(wanted), 'matched': len(found), 'unresolved': len(wanted-set(found)), 'applied': apply}
