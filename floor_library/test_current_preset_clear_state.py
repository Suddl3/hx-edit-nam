from pathlib import Path
import subprocess
import unittest

class CurrentPresetClearStateTests(unittest.TestCase):
    def test_loading_lifecycle_and_thread_invalidation(self):
        code=Path(__file__).with_name('current_preset_clear_state.js').read_text()+r'''
const assert=require('node:assert/strict'),s=createCurrentPresetClearState();
const begin=(e,result)=>s.begin('controller','editor',77,e,{result});
const complete=(e,result=e==='EditBuffer_PresetContentsDidChange'?0:undefined)=>{s.end(begin(e,result));s.settle(77);};
const loaded=()=>{complete('EditBuffer_PresetContentsWillChange');complete('EditBuffer_PresetContentsDidChange',0);};
assert.equal(s.current(77),null);complete('EditBuffer_ParamChanged');assert.equal(s.current(77),null);
complete('EditBuffer_PresetChanged');assert.equal(s.current(77),null);
loaded();assert.ok(s.current(77));assert.equal(s.current(78),null);
let token=begin('EditBuffer_ParamChanged');assert.equal(s.current(77),null);s.end(token);assert.ok(s.current(77));
complete('EditBuffer_HelixSnapshotWillChange');assert.equal(s.current(77),null);
complete('EditBuffer_ParamChanged');assert.equal(s.current(77),null);
complete('EditBuffer_HelixSnapshotDidChange');assert.ok(s.current(77));
s.destroyed('other');assert.ok(s.current(77));s.destroyed('controller');assert.equal(s.current(77),null);
loaded();s.reset();assert.equal(s.current(77),null);
loaded();s.end(s.begin('new-controller','new-editor',77,'EditBuffer_ParamChanged'));
assert.equal(s.current(77),null);
complete('EditBuffer_PresetContentsWillChange');
let completeToken=begin('EditBuffer_PresetContentsDidChange',0);s.end(completeToken);
assert.equal(s.current(77),null);s.settle(78);assert.equal(s.current(77),null);
s.settle(77);assert.ok(s.current(77));
// Regression: loading a capture or editing a block emits a paired lifecycle.
// Previously every WillChange poisoned readiness until an unrelated recall.
for(const kind of ['PresetContents','HelixSignalFlow','HelixSnapshot','HelixAssignments']){
 complete('EditBuffer_'+kind+'WillChange');assert.equal(s.current(77),null);
 assert.equal(s.describe(77).reason,'awaiting-paired-completion');
 complete('EditBuffer_ParamChanged');assert.equal(s.current(77),null);
 const done=begin('EditBuffer_'+kind+'DidChange',0);s.end(done);
 assert.equal(s.current(77),null);assert.equal(s.describe(77).reason,'awaiting-ui-settle');
 s.settle(77);assert.ok(s.current(77),kind);
}
// Interleaved operations must all complete, on the same owner and UI thread.
complete('EditBuffer_PresetContentsWillChange');complete('EditBuffer_HelixSnapshotWillChange');
complete('EditBuffer_PresetContentsDidChange');assert.equal(s.current(77),null);
complete('EditBuffer_HelixSnapshotDidChange');assert.ok(s.current(77));
// Nested/repeated same-family operations require equally many completions.
for(const kind of ['PresetContents','HelixSignalFlow','HelixSnapshot','HelixAssignments']){
 complete('EditBuffer_'+kind+'WillChange');complete('EditBuffer_'+kind+'WillChange');
 complete('EditBuffer_'+kind+'DidChange');assert.equal(s.current(77),null);
 assert.deepEqual(s.describe(77).pendingKinds,[kind]);
 complete('EditBuffer_'+kind+'DidChange');assert.ok(s.current(77));
}
// A matched successful contents pair proves startup/replacement completion;
// Did alone, failed results and other families cannot establish that baseline.
for(const reset of ['reset','EditBuffer_TargetDeviceChanged','EditBuffer_DevicePresetChanged']){
 if(reset==='reset')s.reset();else complete(reset);
 complete('EditBuffer_PresetContentsDidChange');assert.equal(s.current(77),null);
 complete('EditBuffer_HelixSnapshotWillChange');complete('EditBuffer_HelixSnapshotDidChange');
 assert.equal(s.current(77),null);
 complete('EditBuffer_PresetContentsWillChange');complete('EditBuffer_PresetContentsDidChange',-1);
 assert.equal(s.current(77),null);assert.equal(s.describe(77).reason,'awaiting-completed-preset');
 complete('EditBuffer_PresetContentsWillChange');complete('EditBuffer_PresetContentsDidChange',0);
 assert.ok(s.current(77));
}
// Failure in any nested contents operation prevents a later successful sibling
// from authorizing the batch. A fresh successful load can then recover.
complete('EditBuffer_PresetContentsWillChange');complete('EditBuffer_PresetContentsWillChange');
complete('EditBuffer_PresetContentsDidChange',-1);complete('EditBuffer_PresetContentsDidChange',0);
assert.equal(s.current(77),null);
complete('EditBuffer_PresetContentsWillChange');complete('EditBuffer_PresetContentsDidChange',0);
assert.ok(s.current(77));
// A disconnect inside the pair cancels it, and missing status fails closed.
complete('EditBuffer_PresetContentsWillChange');complete('EditBuffer_TargetDeviceChanged');
complete('EditBuffer_PresetContentsDidChange',0);assert.equal(s.current(77),null);
complete('EditBuffer_PresetContentsWillChange');s.end(begin('EditBuffer_PresetContentsDidChange'));s.settle(77);
assert.equal(s.current(77),null);
complete('EditBuffer_UnknownWillChange');complete('EditBuffer_UnknownDidChange');
assert.equal(s.current(77),null);complete('EditBuffer_PresetChanged');assert.equal(s.current(77),null);
loaded();assert.ok(s.current(77));
// Controller changes stay conservative, and diagnostics expose the cause.
s.end(s.begin('another-controller','editor',77,'EditBuffer_ParamChanged'));s.settle(77);
assert.equal(s.current(77),null);assert.ok(s.describe(77).ownerChanges>0);
complete('private user title / invalid event');assert.equal(s.describe(77).lastEvent,'other-notification');
'''
        result=subprocess.run(['node','-e',code],capture_output=True,text=True,timeout=15)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
