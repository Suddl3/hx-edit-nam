"""Offline retention and source overlays for dedicated preview slot 128."""
import copy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from threading import Lock

from audition_diagnostics import replace_once
from library_catalog import CatalogStore, empty_catalog, inspect_wav, load, save, atomic_write


class CaptureRetention:
    def __init__(self, workspace, catalog_path):
        self.workspace = Path(workspace).resolve()
        self.kept = CatalogStore(catalog_path)
        self.preview = CatalogStore(self.workspace / 'preview-catalog.json')
        self.lock = Lock()

    def keep(self, file):
        file = Path(file).resolve(strict=True)
        if not file.is_relative_to(self.workspace):
            raise ValueError('Capture is outside this audition workspace')
        raw = file.read_bytes()
        identity = inspect_wav(raw)['contentId']
        preview = load(self.preview.path) if self.preview.path.exists() else empty_catalog()
        if identity not in preview['captures']:
            raise ValueError('Capture has no preview metadata in this session')
        sidecar_path = file.with_suffix('.source.json')
        sidecar = None
        if sidecar_path.exists():
            sidecar = json.loads(sidecar_path.read_text(encoding='utf-8-sig'))
            if (sidecar.get('schema') != 'floor-tone-source-v1'
                    or sidecar.get('wavSha256') != hashlib.sha256(raw).hexdigest()):
                raise ValueError('Capture source metadata does not match its file')
        with self.lock, self.kept.lock:
            catalog = load(self.kept.path) if self.kept.path.exists() else empty_catalog()
            incoming = copy.deepcopy(preview['captures'][identity])
            first_keep = identity not in catalog['captures']
            record = catalog['captures'].setdefault(identity, incoming)
            if first_keep:
                record['addedAt'] = datetime.now(timezone.utc).isoformat(timespec='seconds')
            for key in ('names', 'gearEvidence'):
                for value in incoming[key]:
                    if value not in record[key]: record[key].append(value)
            if incoming.get('displayName') and not record.get('displayName'):
                record['displayName'] = incoming['displayName']
            folder = self.kept.path.parent / 'kept-captures'
            folder.mkdir(parents=True, exist_ok=True)
            destination = folder / (identity + '.wav')
            kept_raw = raw
            if destination.exists():
                kept_raw = destination.read_bytes()
                if inspect_wav(kept_raw)['contentId'] != identity:
                    raise ValueError('Kept capture file conflicts with its identity')
            else:
                atomic_write(destination, raw)
            record['keptFile'] = str(destination.relative_to(self.kept.path.parent))
            if sidecar is not None:
                # Store only the runtime's documented source fields.
                source = {key: sidecar.get(key) for key in
                          ('schema', 'tone', 'model', 'classification', 'wavSha256')}
                kept_hash = hashlib.sha256(kept_raw).hexdigest()
                if kept_hash != source['wavSha256']:
                    # Identity was checked above: samples match, container metadata
                    # differs. Bind this record to the bytes actually retained and
                    # preserve the original download's hash as separate evidence.
                    source['downloadWavSha256'] = source['wavSha256']
                    source['wavSha256'] = kept_hash
                source_raw = (json.dumps(source, sort_keys=True, indent=2) + '\n').encode('utf-8')
                sources = folder / 'source-records'
                sources.mkdir(exist_ok=True)
                source_file = sources / (hashlib.sha256(source_raw).hexdigest() + '.json')
                if source_file.exists():
                    if source_file.read_bytes() != source_raw:
                        raise ValueError('Kept source record conflicts with its identity')
                else:
                    atomic_write(source_file, source_raw)
                # Immutable sources are written first; only the final atomic catalog
                # commit changes the pointer. Failure leaves the previous pair intact.
                record['sourceFile'] = str(source_file.relative_to(self.kept.path.parent))
            record['retention'] = 'kept'
            save(catalog, self.kept.path)
        return identity

    def metadata_catalog(self):
        catalog = load(self.kept.path) if self.kept.path.exists() else empty_catalog()
        if self.preview.path.exists():
            for key, value in load(self.preview.path)['captures'].items():
                if key not in catalog['captures']:
                    value['retention'] = 'preview'
                    catalog['captures'][key] = value
        return catalog


def patch_tone_policy(source):
    source = replace_once(source,
        '        active.previewSlot ??= namView.freeSlots().slice(-1)[0];', '''        active.previewSlot ??= 128;
        if (!namView.freeSlots().includes(128)) {
          throw new Error('Preview slot 128 is occupied or unavailable. Nothing was overwritten; review it before auditioning.');
        }''')
    source = replace_once(source, '      const freeSlots = namView.freeSlots(active.previewSlot);',
                          '      const freeSlots = namView.freeSlots(128).sort((a,b)=>a-b);')
    source = replace_once(source, '          result.freeSlots = namView.freeSlots(active.previewSlot);',
                          '          result.freeSlots = namView.freeSlots(128).sort((a,b)=>a-b);')
    source = replace_once(source, '      const { slot } = request;', '''      const slot = namView.freeSlots(128).sort((a,b)=>a-b)[0];
      if (!slot) throw new Error('No permanent slot is free in 1–127. The preview remains in 128.');
      if (request.slot !== slot) throw new Error('Available storage changed. Use the lowest free destination shown before saving.');''')
    return source


def patch_preview_retention(source):
    if 'from nam_convert import keep_preview' in source:
        raise ValueError('Preview retention overlay is already applied')
    source = replace_once(source, 'from tone_browser import ToneBrowser',
                          'from tone_browser import ToneBrowser\nfrom nam_convert import keep_preview')
    source = replace_once(source, '        def audition(candidate, index):',
                          '        loaded_file = None\n\n        def audition(candidate, index):')
    source = replace_once(source, '            nonlocal current, slot, selected, free_slots, trial',
                          '            nonlocal current, slot, selected, free_slots, trial, loaded_file\n            loaded_file = None')
    source = replace_once(source, '                trial += 1\n', '                loaded_file = path\n                trial += 1\n')
    source = replace_once(source, "            if action.startswith('save:'):", '''            if action == f'library:{trial}' and loaded_file is not None:
                try:
                    keep_preview(loaded_file)
                    status('Added to your NAM library. The Floor preview is unchanged.')
                except (ValueError, OSError) as error:
                    status('Could not keep the local capture: ' + str(error))
                continue
            if action.startswith('save:'):''')
    source = replace_once(source, "                    slot = reply.get('slot')\n", '''                    if reply.get('savedSlot') and loaded_file is not None:
                        try:
                            keep_preview(loaded_file)
                        except (ValueError, OSError):
                            status('Saved on Helix, but the local library copy failed. Keep this session open.')
                            continue
                    slot = reply.get('slot')
''')
    return source


def patch_card(source):
    if 'id="keep"' in source:
        raise ValueError('Keep button overlay is already applied')
    source = replace_once(source, '  <button id="finish">Close</button>',
                          '  <button id="keep" hidden>Add to Library</button>\n  <button id="finish">Close</button>')
    source = replace_once(source, "  const save = document.querySelector('#save');",
                          "  const save = document.querySelector('#save');\n  const keep = document.querySelector('#keep');")
    source = replace_once(source, "  save.onclick = () => host.postMessage('save:' + trial + ':' + destination.value);",
                          "  save.onclick = () => host.postMessage('save:' + trial + ':' + destination.value);\n  keep.onclick = () => host.postMessage('library:' + trial);")
    source = replace_once(source, '    if (slots.includes(Number(previous))) destination.value = previous;',
                          "    if (slots.length) destination.value = slots[0];\n    destination.disabled = true; // Permanent saves allocate lowest free 1–127.")
    source = replace_once(source, '    destination.disabled = save.disabled = !!data.busy || !slots.length;',
                          '    save.disabled = !!data.busy || !slots.length;\n    keep.hidden = trial == null;\n    keep.disabled = !!data.busy;')
    return source
