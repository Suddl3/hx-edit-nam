"""Compose the current-preset protection into a new sealed candidate only."""
import json
from pathlib import Path
import msgpack
from audition_diagnostics import replace_once
from native_clear_static import clear_contract
from native_current_preset_static import audit
from native_preset_completion_static import audit as audit_completion
from native_empty_node_static import audit_empty_node_omission

HERE=Path(__file__).resolve().parent
PARTS=('slot_clear_policy.js','slot_clear_dispatch.js','slot_clear_selection.js',
       'slot_clear_message.js','native_clear_guard.js','current_preset_clear_state.js','current_preset_completion.js',
       'current_preset_native.js','current_preset_slots.js','current_preset_schema_summary.js')

def patch_clear_protection(source,definitions):
    clear_contract()
    audit()
    audit_completion()
    audit_empty_node_omission()
    models=msgpack.unpackb(Path(definitions).read_bytes())
    catalog={}
    for model in models:
        symbol=model['symbolicID\0'].rstrip('\0')
        params=[p['symbolicID\0'].rstrip('\0') for p in model.get('params\0',[])
                if p.get('displayType\0')=='ir_select\0']
        if symbol in catalog and catalog[symbol]!=params:
            raise ValueError('Conflicting model definitions for Clear protection')
        catalog[symbol]=params
    if catalog.get('HD2_AmpNAMLite')!=['Index'] or catalog.get('HD2_ImpulseResponse1024Dual')!=['Index','Index_1']:
        raise ValueError('The Clear reference catalog differs from the reviewed models')
    helpers='\n'.join((HERE/name).read_text(encoding='utf-8') for name in PARTS)
    source=replace_once(source,'function installNamView(profiles, library = [])',
                        helpers+'\nfunction installNamView(profiles, library = [])')
    runtime='  const clearModelCatalog='+json.dumps(catalog,separators=(',',':'))+';\n'
    runtime+=(HERE/'current_preset_clear_runtime.js').read_text(encoding='utf-8')
    source=replace_once(source,'  function selectedSlot() {',runtime+'\n  function selectedSlot() {')
    source=replace_once(source,"  const clearIrSlots = method(0x3da2b0, 'void', ['pointer']);\n",'')
    source=replace_once(source,'      clearIrSlots(librarian, slots);\n      return true;',
                        '      return slotClearGuard.clear(librarian, slots, false).allowed;')
    source=replace_once(source,'  function invalidateVerifiedSlots(preserveDisplay=true){',
                        '  function invalidateVerifiedSlots(preserveDisplay=true){\n    if(!preserveDisplay)presetClearState.reset();')
    source=replace_once(source,'    if (!libraryReady() || librarySnapshotBusy) return null;',
                        '    if (!libraryReady() || librarySnapshotBusy) return null;\n    presetClearState.settle(Process.getCurrentThreadId());\n    inspectCurrentClearReferences();')
    return source
