"""Dock a local sortable table in NAM, using UI-thread native state/actions."""
from pathlib import Path
from audition_diagnostics import replace_once


def patch_table_view(source):
    if '  function librarySnapshot()' in source:
        raise ValueError('Library table overlay already applied')
    code = Path(__file__).with_name('library_table_native.js').read_text(encoding='utf-8')
    resize = Path(__file__).with_name('sidebar_resize_native.js').read_text(encoding='utf-8')
    slots = Path(__file__).with_name('library_slot_state.js').read_text(encoding='utf-8')
    code = slots + '\n' + resize + '\n' + code
    code += '\n' + Path(__file__).with_name('automatic_slot_native.js').read_text(encoding='utf-8')
    source = replace_once(source, '  function selectedSlot() {', code + '\n  function selectedSlot() {')
    # The child table is the sole NAM presentation. Retain native list objects
    # for existing internal bookkeeping, but never paint their combined rows.
    source = replace_once(source, '    redraw(namList);', '    hideLegacyLibrary();\n    redraw(namList);')
    source = replace_once(source, '  function invalidateLibrarySlots() {',
        '  function invalidateLibrarySlots() {\n    invalidateVerifiedSlots();')
    source = replace_once(source, '        verifiedMetadata[slot] = item;',
        '        verifiedMetadata[slot] = item;\n        slotLedger.imported(Number(slot));')
    source = replace_once(source,
        '      for (let slot = first; slot < first + count; slot++) delete verifiedMetadata[slot];',
        '''      for (let slot = first; slot < first + count; slot++) {
        delete verifiedMetadata[slot];
        slotLedger.forget(slot); // A confirmed replacement supersedes old readback, including IR-only imports.
      }''')
    source = replace_once(source, '''      const list = irList();
      return Object.keys(profiles).map(Number).filter(slot => slot < 128 &&
        verifiedMetadata[slot]?.contentId === contentId && hasProfile(list, slot)).sort((a,b)=>a-b)[0] ?? null;''',
        '      return installedLibrarySlot(contentId);')
    source = replace_once(source,
        '      this.library = this.context.ecx.add(0x10).readPointer();',
        '''      // This native librarian lifecycle callback establishes UI-thread ownership.
      libraryUiThread = Process.getCurrentThreadId();
      this.library = this.context.ecx.add(0x10).readPointer();
      libraryRoot = this.library;''')
    source = replace_once(source, '        namPanel = NULL;',
        '        namPanel = NULL;\n        libraryRoot = NULL;\n        sidebar.reset();')
    return replace_once(source, '    setLibraryLoader(callback)',
        '    libraryReady,\n    libraryFloorConnected,\n    librarySnapshot,\n    loadFromTable,\n    resizeLibrary,\n'
        '    slotEpoch:()=>slotLedger.epoch(),\n    invalidateVerifiedSlots,\n    recordVerifiedSlot,\n    forgetVerifiedSlot,\n'
        '    discoverySnapshot,\n    exportDiscoverySlot,\n    setDiscoveryLocked(value){discoveryLocked=value;},\n'
        '    setDiscoveryStatus(value){discoveryStatus=value;if(["complete","complete-partial"].includes(value))slotLedger.commitDisplay();slotRefresh();},\n'
        '    setSlotRefresh(callback){slotRefresh=callback;},\n    setLibraryLoader(callback)')


def patch_table_tone(source):
    # Fixed structural fields only: no paths, capture titles or exception text.
    source = replace_once(source,
        "      throw new Error('The target changed. Select TONE3000 again on the desired block.');",
        """      trace('audition-target-guard', {targetMatches:!!atTarget(),
        hasTransfer:!!active.transfer, undoMatches:active.selectionUndo===undoIndex(),
        targetValid:!!active.valid});
      throw new Error('The target changed. Select TONE3000 again on the desired block.');""")
    source = replace_once(source, '  let command = null;', '''  let command = null;
  let tableAction = null;
  let tableWindow = NULL;
  namView.setSlotRefresh(()=>{if (!tableWindow.isNull()) post(tableWindow, wake, 0, 0);});
  let lastTable = '';
  let tableSampling = false;
  function publishTable(window) {
    if (tableSampling || !namView.libraryReady()) return;
    tableSampling = true;
    try {
      const state = namView.librarySnapshot();
      if (!state) return;
      if (tableWindow.isNull()) {
        if (!state.visible) return;
        tableWindow = window;
      }
      const text = JSON.stringify(state);
      if (text !== lastTable) {
        lastTable = text;
        send({type:'libraryTable', window:window.toUInt32(), state});
      }
    } finally { tableSampling = false; }
  }
  function listenTableAction() {
    recv('libraryTableAction', message => {
      const value = message.payload;
      const load = ['load','select'].includes(value?.action) && typeof value.contentId === 'string' && /^[0-9a-f]{64}$/.test(value.contentId);
      const resize = value?.action === 'resize' && Number.isInteger(value.width) && value.width >= 300 && value.width <= 600;
      if ((load || resize) && !tableAction && !tableWindow.isNull()) {
        tableAction = value;
        if (!post(tableWindow, wake, 0, 0)) {
          tableAction = null;
          send({type:resize ? 'libraryResizeResult' : 'libraryResult', error:'The editor window is unavailable. Reopen the NAM tab before trying again.'});
        }
      } else if (load || resize) {
        send({type:resize ? 'libraryResizeResult' : 'libraryResult',
          error:'The editor is busy or the NAM tab is unavailable. Try again after the current action finishes.',
          reason:'action-unavailable', requested:resize ? value.width : undefined});
      }
      listenTableAction();
    });
  }
  listenTableAction();''')
    source = replace_once(source, '''      if (!active || !args[0].readPointer().equals(active.window)) return;
      const event''', '''      const window = args[0].readPointer();
      const eventId = args[0].add(4).readU32();
      // Repaint, layout, completed input and explicit catalog/action wakeups only.
      // Idle/mouse-move traffic must not sample native widgets.
      const tableEvent = eventId === wake || [5,0xf,0x47,0x101,0x111,0x202].includes(eventId);
      if (tableEvent && !tableSampling && namView.libraryReady() && !window.isNull()) {
        if (window.equals(tableWindow) || (tableWindow.isNull() && window.equals(getActiveWindow()))) {
          this.tableWindow = window;
          if (eventId === wake && tableAction) {
            const pending = tableAction;
            tableAction = null;
            if (pending.action === 'resize') {
              const result = namView.resizeLibrary(pending.width);
              send({type:'libraryResizeResult', error:result.ok ? '' : 'Sidebar resize unavailable: ' + (result.detail || result.reason),
                reason:result.reason, detail:result.detail, width:result.width, requested:pending.width, changed:result.changed});
            } else if (active || command || !namView.loadFromTable(pending.contentId,pending.action==='select')) {
              send({type:'libraryResult', contentId:pending.contentId,
                error:'Select an existing NAM block and finish any audition or transfer before loading.'});
            }
          }
        }
      }
      if (!active || !window.equals(active.window)) return;
      const event''')
    source = replace_once(source, '''      if (!active || active.workflow === 'library' || active.id !== this.layoutSession) return;''', '''      if (this.tableWindow) publishTable(this.tableWindow);
      if (!active || active.workflow === 'library' || active.id !== this.layoutSession) return;''')
    return replace_once(source, '''      if (active) post(active.window, wake, 0, 0);
      listenLibrary();''', '''      if (active) post(active.window, wake, 0, 0);
      else if (!tableWindow.isNull()) post(tableWindow, wake, 0, 0);
      listenLibrary();''')
