"""Local offline overlay: trace native boundaries; contain orphan browser failure.

This does not claim to fix the primary HX Edit crash. It does not change USB
commands, undo policy, slot allocation, authentication or saved presets.
"""
import json
import re
from pathlib import Path
from threading import Lock
import time


def structural_nodes(value):
    """Validate bounded schema descriptions; never accept parameter values."""
    if not isinstance(value, list) or len(value) > 16:
        return None
    identifier = r'(?:@?[A-Za-z0-9_][A-Za-z0-9_.-]{0,63}|<nonstandard>)'
    kinds = ('object', 'array', 'null', 'number', 'boolean', 'string', 'undefined')
    for node in value:
        if not isinstance(node, dict) or set(node) != {'name','type','propertyCount','fields','truncated'}:
            return None
        if not isinstance(node['name'], str) or not re.fullmatch(identifier, node['name']) or node['type'] not in kinds:
            return None
        if type(node['propertyCount']) is not int or not 0 <= node['propertyCount'] <= 65536 or type(node['truncated']) is not bool:
            return None
        fields = node['fields']
        if not isinstance(fields, list) or len(fields) > 64 or any(
                not isinstance(field, str) or len(field) > 280 or not re.fullmatch(
                    identifier + r'(?:\.' + identifier + r'){0,3}:(?:' + '|'.join(kinds) + ')', field)
                for field in fields):
            return None
    return value


class Journal:
    """Only fixed diagnostic fields; never command files, URLs or auth payloads."""
    def __init__(self, path):
        self.path = Path(path)
        self.lock = Lock()
        self.started = time.monotonic()

    def write(self, event, **values):
        allowed = {'phase', 'session', 'id', 'slot', 'step', 'steps', 'action',
                   'reason', 'pid', 'lineNumber', 'columnNumber', 'accepted', 'epoch', 'ready', 'generation',
                   'fileCount', 'dialogSupplied', 'pathMatches',
                   'localCount', 'installedCount', 'metadataCount',
                   'expectedUndo', 'currentUndo'}
        # Explicit numeric UI-only fields; retain privacy filtering for strings,
        # arbitrary objects, filenames and account payloads.
        numeric = {'width', 'requested', 'changed', 'available', 'parentWidth',
                   'libraryX', 'libraryWidth', 'editorX', 'editorWidth', 'rightAnchor', 'leftAnchor', 'checked'}
        row = {'event': event, 'elapsedMs': round((time.monotonic() - self.started) * 1000)}
        row.update({k: v for k, v in values.items() if k in allowed
                    and (v is None or type(v) in (int, float, bool, str))})
        row.update({k:v for k,v in values.items() if k in numeric and type(v) in (int,float,bool)})
        if event == 'slot-clear':
            for key in ('complete', 'baseline', 'pendingReady'):
                if type(values.get(key)) is bool:
                    row[key] = values[key]
            for key in ('depth', 'notifications', 'ownerChanges', 'notificationErrors', 'otherEditorNotifications', 'unknownGlobalCount'):
                value = values.get(key)
                if type(value) is int and 0 <= value <= 2**53:
                    row[key] = value
            for key in ('slots', 'references'):
                value = values.get(key)
                if isinstance(value, list) and len(value) <= 256 and all(type(n) is int and 1 <= n <= 128 for n in value):
                    row[key] = value
            kinds = values.get('pendingKinds')
            if isinstance(kinds, list) and len(kinds) <= 4 and all(
                    n in ('PresetContents', 'HelixSignalFlow', 'HelixSnapshot', 'HelixAssignments') for n in kinds):
                row['pendingKinds'] = kinds
            name = values.get('lastEvent')
            if isinstance(name, str) and (name in ('', 'other-notification') or
                    re.fullmatch(r'(?:EditBuffer|Device|UndoBuffer)_[A-Za-z0-9_]{1,120}', name)):
                row['lastEvent'] = name
            nodes = structural_nodes(values.get('unknownGlobals'))
            if nodes is not None:
                row['unknownGlobals'] = nodes
        if event == 'automatic-slot-discovery' and values.get('state') in {
            'inactive', 'waiting-for-editor', 'disconnected', 'verifying', 'complete', 'complete-partial',
            'discovery-timeout', 'invalid-inventory', 'export-unavailable',
            'preparation-failed', 'verification-stopped', 'native-discovery-fault',
            'connection-monitor-failed', 'inventory-changed-during-discovery',
            'native-observer-unavailable', 'closed'}:
            row['state'] = values['state']
        with self.lock:
            with self.path.open('a', encoding='utf-8') as stream:
                stream.write(json.dumps(row) + '\n')


def replace_once(source, old, new):
    if source.count(old) != 1:
        raise ValueError('Upstream source changed; review audition diagnostics')
    return source.replace(old, new)


def patch_browser(source):
    # Destroying the native parent destroys child HWNDs before our layout timer
    # notices. Calling Form.Close then can dispose an already-dead WebView2 and
    # raise the observed exception. The orphan helper has no device connection:
    # exit that process directly, without another native-control teardown pass.
    return replace_once(source, '        if (!IsWindow(parent)) { Close(); return; }', '''        if (!IsWindow(parent))
        {
            timer.Stop();
            try { output.WriteLine("{\\"type\\":\\"error\\",\\"text\\":\\"HX Edit closed unexpectedly; the browser is stopping.\\"}"); }
            finally { Environment.Exit(1); }
            return;
        }''')


def patch_tone_view(source):
    source = replace_once(source, '  let command = null;', '''  let command = null;
  function trace(phase, fields = {}) {
    send({type: 'toneTrace', phase, session: active ? active.id : null, ...fields});
  }''')
    replacements = (
        ('''  function canRestore(transfer) {
    return playing(transfer) && !active.deviceSaved && active.undoAfter === undoIndex();
  }''', '''  function restoreBlockReason(transfer) {
    if (!playing(transfer)) return 'preview-target-or-identity-changed';
    if (active.deviceSaved) return 'preset-saved';
    if (active.undoAfter !== undoIndex()) return 'undo-history-changed';
    return null;
  }

  function canRestore(transfer) {
    return restoreBlockReason(transfer) === null;
  }'''),
        ('    if (!canRestore(transfer)) return retained;', '''    const blocked = restoreBlockReason(transfer);
    if (blocked) {
      trace('restore-blocked', {reason: blocked, slot: transfer.slot,
        expectedUndo: active.undoAfter, currentUndo: undoIndex()});
      const explanation = blocked === 'undo-history-changed'
        ? 'Amp settings or undo history changed after auditioning. Returning a knob to its earlier value does not reset undo history.'
        : blocked === 'preset-saved'
          ? 'The preset was saved after auditioning.'
          : 'The preview block or capture no longer matches this audition.';
      return {notice: `Preview paused: ${explanation} No replacement was attempted. The audition profile remains in User IR ${transfer.slot}.`};
    }'''),
        ('  function run(request) {', '''  function run(request) {
    trace('command-start', {id: request.id, action: request.action});'''),
        ("  function reply(request, result) {", """  function reply(request, result) {
    trace(result.error ? 'command-error' : 'command-reply', {id: request.id, action: request.action});"""),
        ("        if (undo(editor) < 0 || undoIndex() !== undoAfter - step - 1) return retained;", """        trace('undo-before', {step, steps});
        const undoResult = undo(editor);
        trace('undo-after', {step, steps});
        if (undoResult < 0 || undoIndex() !== undoAfter - step - 1) return retained;"""),
        ("    return namView.clearPreview(transfer) ? { clearing: transfer } : retained;", """    trace('clear-before', {slot: transfer.slot});
    const cleared = namView.clearPreview(transfer);
    trace('clear-after', {slot: transfer.slot});
    return cleared ? { clearing: transfer } : retained;"""),
        ("        const transfer = namView.importPreview(request.file, active.previewSlot);", """        trace('import-before', {slot: active.previewSlot});
        const transfer = namView.importPreview(request.file, active.previewSlot);
        trace('import-after', {slot: active.previewSlot});"""),
        ("      const saved = namView.importPreview(trial.file, slot);", """      trace('save-import-before', {slot});
      const saved = namView.importPreview(trial.file, slot);
      trace('save-import-after', {slot});"""),
        ("      imported();", """      trace('import-confirmed', {slot: transfer.slot});
      imported();"""),
        ("      if (!selecting) selecting = saving ? namView.selectProfile(transfer) : namView.playPreview(transfer);", """      if (!selecting) {
        trace('select-before', {slot: transfer.slot});
        selecting = saving ? namView.selectProfile(transfer) : namView.playPreview(transfer);
        trace('select-after', {slot: transfer.slot});
      }"""),
    )
    for old, new in replacements:
        source = replace_once(source, old, new)
    return source
