"""Native library selection, load action and readable labels. Offline overlays."""
from pathlib import Path
from xml.etree import ElementTree as ET

from audition_diagnostics import replace_once

HELPERS = r'''
  const localRowIds = new Map();
  let libraryLoader = null;
  let libraryBusy = false;
  function selectedLibraryItem() { return localRowIds.get(selectedSlot()) || null; }
  function datedLabel(item, status) {
    const parsed = item.addedAt ? new Date(item.addedAt) : null;
    const date = parsed && Number.isFinite(parsed.getTime())
      ? parsed.toLocaleDateString(undefined, {year:'numeric', month:'short', day:'numeric'}) : 'unknown';
    return `${item.title} | ${item.categoryLabel} | ${status} | Added ${date}`;
  }
  function friendlyMenuLabel(slot, fallback) {
    const item = verifiedMetadata[slot];
    return item ? Memory.allocUtf8String(`${slot}: ${item.title}`) : fallback;
  }
'''


def patch_library_view(source):
    source = replace_once(source, '  const verifiedMetadata = {};', '  const verifiedMetadata = {};\n' + HELPERS)
    source = replace_once(source, 'const buttonIds = { copy:', 'const buttonIds = { load: 0x6e6c6f64, copy:')
    source = replace_once(source, '    const enabled = { copy: selected,',
        '''    const localItem = selectedLibraryItem();
    const enabled = { load: !!libraryLoader && !libraryBusy && !!localItem?.canLoad && hasActiveModel(), copy: selected,''')
    source = replace_once(source, '  function runAction(action) {', '''  function runAction(action) {
    if (action === 'load') {
      const item = selectedLibraryItem();
      if (libraryLoader && !libraryBusy && item?.canLoad) libraryLoader(item.contentId);
      return;
    }''')
    source = replace_once(source, "    return 'Installed | ' + (item ? item.title + ' - ' + item.categoryLabel : name);",
                          "    return item ? datedLabel(item, 'Installed') : 'Installed | ' + name;")
    source = replace_once(source, '''    for (const item of local) {
      const preview = audition && verifiedMetadata[audition.slot]?.contentId === item.contentId;
      const label = (preview ? 'Auditioning ' + audition.slot : 'Local') + ' | ' + item.title + ' - ' + item.categoryLabel;
      appendRow(namList, -1, Memory.allocUtf8String(label), NULL, 0, -1, 1, 0, 0);
    }''', '''    localRowIds.clear();
    for (const [index, item] of local.entries()) {
      const preview = audition && verifiedMetadata[audition.slot]?.contentId === item.contentId;
      const label = datedLabel(item, preview ? 'Preview ' + audition.slot : 'Local');
      const id = -index - 2;
      localRowIds.set(id + 1, item);
      appendRow(namList, -1, Memory.allocUtf8String(label), NULL, 0, id, 1, 0, 0);
    }''')
    source = replace_once(source, '    refreshProfiles,\n    flushLibrary,', '''    setLibraryLoader(callback) { libraryLoader = callback; refreshButtons(); },
    setLibraryBusy(value, refresh = true) { libraryBusy = value; if (refresh) refreshButtons(); },
    installedSlotFor(contentId) {
      const list = irList();
      return Object.keys(profiles).map(Number).filter(slot => slot < 128 &&
        verifiedMetadata[slot]?.contentId === contentId && hasProfile(list, slot)).sort((a,b)=>a-b)[0] ?? null;
    },
    selectInstalled(contentId, slot) {
      return this.installedSlotFor(contentId) === slot && setModelSlot(slot);
    },
    refreshProfiles,
    flushLibrary,''')
    source = replace_once(source,
        '''      return append(menu, index, label, id, options, disabled);
    }
    if (parts && audition''', '''      return append(menu, index, friendlyMenuLabel(Number(parts[1]), label), id, options, disabled);
    }
    if (parts && audition''')
    source = replace_once(source,
        "        && parts[2] === audition.name) return append(menu, index, label, id, options, disabled);",
        "        && parts[2] === audition.name) return append(menu, index, friendlyMenuLabel(Number(parts[1]), label), id, options, disabled);")
    return source


def patch_library_tone(source):
    code = Path(__file__).with_name('library_load_native.js').read_text(encoding='utf-8')
    source = replace_once(source, '  function run(request) {', code + '\n  function run(request) {\n    if (active.workflow === \'library\') return runLibraryLoad(request);')
    source = replace_once(source,
        '''    const row = selectedRow(sender);''', '''    libraryController = controller;
    const row = selectedRow(sender);''')
    # Also capture the controller when ordinary NAM parameter events pass through.
    source = replace_once(source,
        '''      modelEvent(controller, event, sender, data, flags);
      if (event === selectionEvent''', '''      libraryController = controller;
      modelEvent(controller, event, sender, data, flags);
      if (event === selectionEvent''')
    source = replace_once(source,
        '''      if (active && active.controller.equals(this.context.ecx)) {''', '''      if (libraryController.equals(this.context.ecx)) libraryController = NULL;
      if (active && active.controller.equals(this.context.ecx)) {''')
    source = replace_once(source, '''    send({ type: 'toneReply', id: request.id, ...result });''', '''    if (request.workflow === 'library') {
      const contentId = request.contentId;
      namView.setLibraryBusy(false, !result.windowUnavailable);
      active = null;
      send({type: 'libraryResult', contentId, slotEpoch:namView.slotEpoch(), ...result});
      if (result.error && !result.windowUnavailable) libraryNotice((result.savedSlot ? `Capture is in slot ${result.savedSlot}. ` : '') + result.error);
      return;
    }
    send({ type: 'toneReply', id: request.id, ...result });''')
    source = replace_once(source,
        "reply(request, { error: 'HX Edit closed during the TONE3000 operation.' });",
        "reply(request, { error: 'HX Edit closed during the TONE3000 operation.', windowUnavailable: true });")
    source = replace_once(source,
        '      if (!active || active.id !== this.layoutSession) return;',
        "      if (!active || active.workflow === 'library' || active.id !== this.layoutSession) return;")
    return source


def patch_library_layout(resources):
    resources = Path(resources)
    main = ET.parse(resources / 'main.xml')
    library = ET.parse(resources / 'librarian.xml')
    root = library.find("panel_widget[@id='livw']")
    placeholder = main.find(".//panel_widget[@id='livw']")
    editor = main.find(".//panel_widget[@id='edvw']/anchor_pos")
    if root.find(".//pushbutton_widget[@id='nlod']") is not None:
        raise ValueError('Library layout already applied')
    if root.get('width') != '360' or placeholder.get('width') != '360' or editor.get('left') != '368':
        raise ValueError('Review library layout before widening')
    # Compact default; the table owns its columns rather than widening the app.
    root.set('width', '360'); placeholder.set('width', '360'); editor.set('left', '368')
    panel = root.find("panel_widget[@id='namv']//listbox_widget[@id='nlst']/..")
    if panel is None: raise ValueError('NAM list panel missing')
    panel.find("listbox_widget[@id='nlst']/anchor_pos").set('bottom', '92')
    legend = panel.find("text_widget[@id='nleg']")
    legend.find('text/label').text = ('Local: kept on PC; installation unverified\n'
        'Select a local capture, then Load on Floor\nIRs: all physical slots; preview uses 128')
    button = ET.fromstring('''<pushbutton_widget id="nlod" left="8" top="0" height="24" width="145" name="Load on Floor">
      <anchor_pos left="8" bottom="62"/><bg_color>0xFF392039</bg_color>
      <text><label>LOAD ON FLOOR</label><font name="Roboto" size="11"/><text_color>0xFFFFA6E6</text_color></text>
    </pushbutton_widget>''')
    panel.append(button)
    main.write(resources / 'main.xml', encoding='utf-8')
    library.write(resources / 'librarian.xml', encoding='utf-8')
