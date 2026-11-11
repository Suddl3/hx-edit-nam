"""Preserve controls when replacing an audition on an existing NAM block.

Uses the existing native Model parameter setter, never rewinds unrelated edits.
Insertion into an empty position still uses the original guarded undo path.
"""
from audition_diagnostics import replace_once


def patch_nam_settings(source):
    if '    modelReference() {' in source:
        raise ValueError('Preview settings overlay is already applied')
    return replace_once(source,
        '    modelSlot: () => hasActiveModel() ? getValue(activeModel) + 1 : null,', '''    modelSlot: () => hasActiveModel() ? getValue(activeModel) + 1 : null,
    modelReference() {
      const slot = this.modelSlot();
      const list = irList();
      if (!Number.isInteger(slot) || slot < 1 || slot > 127 || list.isNull()) return null;
      const row = getIrRow(list, slot - 1);
      if (row.isNull() || !row.add(0x10c).readU32()) return null;
      return {slot, name: row.readUtf8String(), controller: irController};
    },
    canRestoreModel(transfer, original) {
      if (!original || !Number.isInteger(original.slot) || original.slot < 1 || original.slot > 127 ||
          !original.controller.equals(irController) || !this.previewName(transfer) ||
          this.modelSlot() !== transfer.slot) return false;
      const list = irList();
      if (list.isNull()) return false;
      const row = getIrRow(list, original.slot - 1);
      return !row.isNull() && !!row.add(0x10c).readU32() && row.readUtf8String() === original.name;
    },
    restoreModel(transfer, original) {
      if (!this.canRestoreModel(transfer, original)) return false;
      return setModelSlot(original.slot); // The caller waits for native completion.
    },''')


def patch_tone_settings(source):
    if '    active.originalReference = namView.modelReference();' in source:
        raise ValueError('Preview settings overlay is already applied')
    source = replace_once(source,
        '    active.originalSlot = namView.modelSlot();',
        '    active.originalSlot = namView.modelSlot();\n    active.originalReference = namView.modelReference();')
    source = replace_once(source, '''  function canRestore(transfer) {
    return restoreBlockReason(transfer) === null;
  }''', '''  function canRestore(transfer) {
    const blocked = restoreBlockReason(transfer);
    return blocked === null || (blocked === 'undo-history-changed' &&
      namView.canRestoreModel(transfer, active.originalReference));
  }''')
    source = replace_once(source,
        '    if (blocked) {\n      trace(\'restore-blocked\'', '''    if (blocked === 'undo-history-changed' && namView.canRestoreModel(transfer, active.originalReference)) {
      // No undo: retain Input/EQ/volume/bypass and unrelated owner edits.
      // Verify the native Model setter before clearing any preview payload.
      trace('restore-model-before', {slot: transfer.slot});
      active.applying = true;
      let restored;
      try {
        restored = namView.restoreModel(transfer, active.originalReference);
      } finally {
        active.applying = false;
      }
      if (!restored || !atTarget() || namView.modelSlot() !== active.originalSlot) {
        trace('restore-blocked', {reason: 'model-restore-unconfirmed', slot: transfer.slot});
        return {notice: `Could not confirm the original Model selection. User IR ${transfer.slot} was left untouched; stop and inspect the NAM block.`};
      }
      trace('restore-model-after', {slot: transfer.slot});
      trace('clear-before', {slot: transfer.slot});
      const cleared = namView.clearPreview(transfer);
      trace('clear-after', {slot: transfer.slot});
      return cleared ? {clearing: transfer} : retained;
    }
    if (blocked) {
      trace('restore-blocked\'''')
    return source
