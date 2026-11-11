"""Local catalog presentation for the native NAM pane. No device reads/writes."""
import re

from audition_diagnostics import replace_once
from library_catalog import category, classification_revision
from library_catalog import inspect_wav
from library_files import file_state
from pathlib import Path
from xml.etree import ElementTree as ET

LABELS = {'amp-only': 'Amp only', 'amp+cab': 'Amp + cab', 'pedals': 'Pedal',
          'unidentified': 'Needs classification', 'cab': 'Cab capture'}


def capture_device_name(title, group, identity):
    """Stable short transport label; matching it is presentation evidence only."""
    if not re.fullmatch(r'[0-9a-f]{64}', identity):
        raise ValueError('Invalid capture identity')
    tag = {'amp-only': 'NAM A', 'amp+cab': 'NAM AC', 'pedals': 'NAM P'}.get(group, 'NAM')
    title = re.sub(r'[^A-Za-z0-9 _-]', '', title).strip() or 'Capture'
    title = title[:31-len(tag)-10].rstrip()
    return f'{tag} {title} {identity[:8]}'


def library_rows(catalog, *, include_previews=False, catalog_path=None):
    rows = []
    for identity, record in catalog['captures'].items():
        if record['kind'] != 'nam':
            continue
        kept = record.get('retention') != 'preview'
        if not kept and not include_previews:
            continue
        # Names are labels only; never parse them to infer gear or device slots.
        aliases = [' '.join(n.split()) for n in record['names'] if isinstance(n, str) and n.strip()]
        friendly = [n for n in aliases if not re.match(r'^T3K\s+\d+\s+[0-9a-f]+\s', n, re.I)]
        preferred = record.get('displayName')
        title = ' '.join(preferred.split()) if isinstance(preferred, str) and preferred.strip() else (friendly or aliases or ['Unnamed capture'])[0]
        group = category(record)
        # Reconstruct only our exact generated label. Do not strip arbitrary hash
        # suffixes or use this display match to mark a physical slot installed.
        if re.fullmatch(r'[0-9a-f]{64}', identity):
            device_alias = capture_device_name(title, group, identity)
            if device_alias not in aliases:
                aliases.append(device_alias)
        local = file_state(record, identity, catalog_path)
        rows.append({'contentId': identity, 'title': title, 'category': group, **local,
                     'categoryOverride': record.get('override'),
                     'categoryRevision': classification_revision(record),
                     'categoryEvidence': record.get('gearEvidence', []),
                     'categoryLabel': LABELS.get(group, group.replace('-', ' ').title()),
                     'aliases': aliases, 'kept': kept, 'addedAt': record.get('addedAt'),
                     'dateEvidence': record.get('dateEvidence'),
                     'canLoad': record.get('retention') == 'kept' and local['fileStatus'] == 'ready'})
    return sorted(rows, key=lambda r: (r['categoryLabel'].casefold(), r['title'].casefold(), r['contentId']))


def imported_rows(catalog, files, first_slot, profile_slots=None, *, catalog_path=None):
    """Metadata for the exact files validated for this transaction, never names."""
    indexed = {row['contentId']: row for row in library_rows(catalog, include_previews=True, catalog_path=catalog_path)}
    result = {}
    allowed = None if profile_slots is None else {int(slot) for slot in profile_slots}
    for offset, file in enumerate(files):
        if allowed is not None and first_slot + offset not in allowed:
            continue  # Stock IR/HIR imports do not need NAM catalog parsing.
        identity = inspect_wav(Path(file).read_bytes())['contentId']
        if identity in indexed:
            result[first_slot + offset] = indexed[identity]
    return result


HELPERS = r'''
  // Local catalog rows have no device identity. Row data -1 means no slot.
  // Native operations still require a positive, session-verified physical slot.
  const verifiedMetadata = {};
  let libraryDirty = true;
  const blankSlotLabel = Memory.allocUtf8String('');
  function mergeLibrary(items) {
    for (const item of items) {
      const index = library.findIndex(row => row.contentId === item.contentId);
      if (index < 0) library.push(item);
      else library[index] = item;
    }
    library.sort((a, b) => a.categoryLabel.localeCompare(b.categoryLabel) || a.title.localeCompare(b.title));
    libraryDirty = true;
  }
  function profileLabel(slot, name) {
    const item = verifiedMetadata[slot];
    return 'Installed | ' + (item ? item.title + ' - ' + item.categoryLabel : name);
  }
  function localLibraryRows() {
    const installed = new Set(Object.keys(profiles).map(slot => verifiedMetadata[slot])
      .filter(Boolean).map(item => item.contentId));
    return library.filter(item => !installed.has(item.contentId));
  }
  function flushLibrary() {
    if (libraryDirty && !namList.isNull()) renderProfiles();
  }
  function invalidateLibrarySlots() {
    for (const slot of Object.keys(profiles)) delete profiles[slot];
    for (const slot of Object.keys(verifiedMetadata)) delete verifiedMetadata[slot];
    pending.clear();
    audition = null;
    libraryDirty = true;
  }
'''


def patch_view(source):
    source = replace_once(source, 'function installNamView(profiles) {',
                          'function installNamView(profiles, library = []) {\n' + HELPERS)
    source = replace_once(source, 'Memory.allocUtf8String(profiles[slot]), NULL',
                          'Memory.allocUtf8String(profileLabel(slot, profiles[slot])), NULL')
    source = replace_once(source, '    const message = findWidget(namPanel, 0x6e6d7367, 1);', '''    const local = localLibraryRows();
    for (const item of local) {
      const preview = audition && verifiedMetadata[audition.slot]?.contentId === item.contentId;
      const label = (preview ? 'Auditioning ' + audition.slot : 'Local') + ' | ' + item.title + ' - ' + item.categoryLabel;
      appendRow(namList, -1, Memory.allocUtf8String(label), NULL, 0, -1, 1, 0, 0);
    }
    const message = findWidget(namPanel, 0x6e6d7367, 1);''')
    source = replace_once(source, 'Number(slots.length === 0), 1);',
                          'Number(slots.length === 0 && local.length === 0), 1);')
    source = replace_once(source, '      if (slot) setModelSlot(slot);',
                          '      if (hasProfile(irList(), slot)) setModelSlot(slot);')
    source = replace_once(source, '    redraw(namList);', '''    libraryDirty = false;
    send({type: 'toneTrace', phase: 'library-render', localCount: local.length, installedCount: slots.length});
    redraw(namList);''')
    source = replace_once(source, '    refreshButtons();\n  }\n\n  function hasActiveModel()',
                          '    flushLibrary();\n    refreshButtons();\n  }\n\n  function hasActiveModel()')
    source = replace_once(source, '      const expected = pending.get(slot);', '''      if (Object.hasOwn(profiles, slot) && profiles[slot] !== name) {
        delete profiles[slot];
        delete verifiedMetadata[slot];
        changed = true;
      }
      const expected = pending.get(slot);''')
    source = replace_once(source, '      irController = NULL;',
                          '      invalidateLibrarySlots();\n      irController = NULL;')
    source = replace_once(source,
        '        args[2] = ptr(this.context.esi.add(0x100).readU32() + 1);', '''        const slot = this.context.esi.add(0x100).readS32() + 1;
        if (slot > 0) args[2] = ptr(slot);
        else {
          // The established number-format call. Only change a verified integer
          // format; keep the stock fallback if this binary's arguments differ.
          try {
            if (/^%0?\\d*[diu]$/.test(args[1].readCString())) args[1] = blankSlotLabel;
          } catch (_) { /* Do not guess at an unexpected format pointer. */ }
        }''')
    source = replace_once(source, '      audition = { slider: activeModel, slot: transfer.slot, name };',
                          '      audition = { slider: activeModel, slot: transfer.slot, name };\n      libraryDirty = true;\n      flushLibrary();')
    source = replace_once(source, '      audition = null;',
                          '      audition = null;\n      libraryDirty = true;\n      flushLibrary();')
    source = replace_once(source, '    refreshProfiles,', '''    refreshProfiles,
    flushLibrary,
    mergeLibrary,
    trackLibraryImport(first, count, metadata) {
      for (let slot = first; slot < first + count; slot++) delete verifiedMetadata[slot];
      for (const [slot, item] of Object.entries(metadata)) {
        verifiedMetadata[slot] = item;
      }
      mergeLibrary(Object.values(metadata).filter(item => item.kept !== false));
      send({type: 'toneTrace', phase: 'library-import-metadata', metadataCount: Object.keys(metadata).length});
    },''')
    return source


def patch_tone_library(source):
    """Use the existing UI message thread for queued catalog redraws."""
    source = replace_once(source, '        active.previewSlot ??= namView.freeSlots()[0];',
                          '        active.previewSlot ??= namView.freeSlots().slice(-1)[0];')
    source = replace_once(source, '      if (!active || active.id !== this.layoutSession) return;',
                          '      if (!active || active.id !== this.layoutSession) return;\n      namView.flushLibrary();')
    return replace_once(source, '  listen();\n}', '''  function listenLibrary() {
    recv('libraryUpdate', message => {
      namView.mergeLibrary(message.payload);
      if (active) post(active.window, wake, 0, 0);
      listenLibrary();
    });
  }
  listenLibrary();
  listen();
}''')


def patch_layout(resources):
    resources = Path(resources)
    main = ET.parse(resources / 'main.xml')
    library = ET.parse(resources / 'librarian.xml')
    placeholder = main.find(".//panel_widget[@id='livw']")
    editor = main.find(".//panel_widget[@id='edvw']/anchor_pos")
    root = library.find("panel_widget[@id='livw']")
    frame = root.find("panel_widget[@id='namv']/panel_widget[@id='nmgp']/panel_widget")
    rows = frame.find("listbox_widget[@id='nlst']/anchor_pos")
    if (placeholder.get('width') != '220' or root.get('width') != '220'
            or editor.get('left') != '228' or rows.get('bottom') != '4'):
        raise ValueError('Unexpected library geometry; review layout before applying')
    placeholder.set('width', '360')
    root.set('width', '360')
    editor.set('left', '368')
    rows.set('bottom', '62')
    footer = ET.fromstring('''<text_widget id="nleg" height="52" left="8" top="0" width="1">
      <anchor_pos left="8" right="8" bottom="4"/>
      <text><label>Local: device status unverified
Installed: confirmed this session
IRs: all physical slots, including NAM</label>
      <font name="Roboto" size="11"/><align horz="left" vert="top"/>
      <multiline>yes</multiline><text_color>0xFFC4C4C4</text_color></text>
    </text_widget>''')
    frame.append(footer)
    main.write(resources / 'main.xml', encoding='utf-8')
    library.write(resources / 'librarian.xml', encoding='utf-8')
