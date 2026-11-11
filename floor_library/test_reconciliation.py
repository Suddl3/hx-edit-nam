"""Regression checks for the current-upstream integration, without editor/USB."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

from test_library_catalog import wav
from library_catalog import load
from runtime import configure_modules

HERE = Path(__file__).resolve().parent


class ReconciliationTests(unittest.TestCase):
    def test_direct_import_retained_but_preview_and_cab_ir_not_kept(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            work = root / 'session'
            work.mkdir()
            capture = work / 'Capture.wav'
            capture.write_bytes(wav())
            cab = work / 'Cab.wav'
            cab.write_bytes(wav(False))
            convert = types.ModuleType('nam_convert')
            convert.convert_batch = lambda files, *args: files
            convert.is_nam_wav = lambda path: path == capture
            tone = types.ModuleType('tone3000')
            tone.get_tone = lambda *args: ({}, [])
            def prepare(*args):
                convert.convert_batch([str(capture)], work, None)
                return str(capture)
            tone.prepare_model = prepare
            catalog = root / 'library/catalog.json'
            previous_path = sys.path[:]
            try:
                with patch.dict(sys.modules, {'nam_convert': convert, 'tone3000': tone}):
                    configure_modules(root, work, catalog)
                    tone.prepare_model({}, None, work, None)
                    self.assertFalse(catalog.exists(), 'Auditions must not populate the kept library')
                    convert.convert_batch([str(cab)], work, None)
                    self.assertFalse(catalog.exists(), 'Cab IRs must not become NAM captures')
                    convert.convert_batch([str(capture)], work, None)
                    record, = load(catalog)['captures'].values()
                    self.assertEqual(record['retention'], 'kept')
                    self.assertTrue(record['addedAt'])
                    capture.unlink()
                    self.assertEqual((catalog.parent / record['keptFile']).read_bytes(), wav())
            finally:
                sys.path[:] = previous_path

    def test_restore_waits_for_model_and_clear_completion_and_preserves_controls(self):
        source = (HERE / 'tone_view.js').read_text(encoding='utf8')
        restore = source[source.index('  function restore(request, saved) {'):source.index('\n  const commandAddress')]
        harness = r'''
const assert = require('node:assert/strict');
let model, index, empty, cleared, stopped, active, pending, results, reason, positionOK, targetOK;
const namView = {
  canRestoreModel:()=>true, modelSlot:()=>model,
  restoreModel:()=>true, // Setter succeeds before the model/Undo update arrives.
  clearPreview:()=>{cleared++;return true;},stopPreview:()=>stopped++,
  previewSlotEmpty:()=>empty
};
const undoIndex=()=>index,atPosition=()=>positionOK,atTarget=()=>targetOK;
const block=()=>42,restoreBlockReason=()=>reason,trace=()=>{};
function poll(request,fn){pending.push(fn);}
function afterRestore(request,result){results.push(result);return {};}
function reset(){model=128;index=8;empty=false;cleared=stopped=0;pending=[];results=[];
  reason='undo-history-changed';positionOK=targetOK=true;
  active={transfer:{slot:128},undoBefore:3,undoAfter:4,originalSlot:9,originalBlock:42,originalReference:{slot:9}};
}
function step(expired=false){return pending[0](expired);}
reset();restore({action:'close'});assert.equal(cleared,0);
step();assert.equal(cleared,0);model=9;step();assert.equal(cleared,0);
index=9;step();assert.equal(cleared,1);assert.equal(results.length,0);
pending.shift();step();assert.equal(results.length,0);
empty=true;step();assert.deepEqual(results,[{}]);assert.equal(active.transfer,null);
reset();restore({action:'close'});step(true);assert.equal(cleared,0);assert.ok(results[0].notice);
reset();restore({action:'close'});positionOK=false;step();assert.equal(cleared,0);assert.ok(results[0].notice);
reset();reason='preset-saved';restore({action:'close'});assert.equal(pending.length,0);assert.equal(cleared,0);
reset();reason=null;index=4;
const editorPointer={readPointer(){return this;},add(){return this;}};active.editor=editorPointer;
function NativeFunction(){return ()=>{index--;return 0;};}
restore({action:'close'});assert.equal(index,3);assert.equal(cleared,0);
model=9;step();assert.equal(cleared,1);
'''
        result = subprocess.run(['node', '-e', restore + harness], capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_preserved_upstream_staging_and_opt_in_boundaries(self):
        source = (HERE / 'tone_view.js').read_text(encoding='utf8')
        self.assertIn('active.originalBlock =', source)
        self.assertIn('namView.importPreview(request.file, active.previewSlot, true)', source)
        self.assertIn('namView.libraryFloorConnected()', source)
        prepare = (HERE / 'prepare_library.py').read_text(encoding='utf8')
        self.assertIn("spec_from_file_location('_floor_browser_build'", prepare)
        self.assertNotIn('from tone_browser import', prepare)
        launcher = (HERE.parent / 'start-hx-edit-nam.pyw').read_text(encoding='utf8')
        self.assertIn("if args.floor_library:", launcher)
        self.assertIn('profiles, scan_error = {}, True', launcher)
        self.assertIn('run_editor(exe, workspace, device, profiles)', launcher)
