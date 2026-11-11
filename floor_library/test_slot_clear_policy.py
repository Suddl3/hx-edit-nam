"""Pure policy checks; native wiring is covered by separate boundary tests."""
from pathlib import Path
import subprocess
import unittest

class ClearPolicyTests(unittest.TestCase):
    def test_current_preset_protection_is_atomic_and_requires_complete_fresh_inventory(self):
        source=Path(__file__).with_name('slot_clear_policy.js').read_text()
        script=source+r'''
const assert=require('node:assert/strict');
const inventory={complete:true,presetId:'current-edit-buffer',revision:8,references:[
 {slot:17,blockId:'path1:block2',bypassed:true},
 {slot:9,blockId:'path2:block3',snapshot:7},
 {slot:17,blockId:'path2:block4'}]};
const request={presetId:inventory.presetId,revision:8,slots:[17,18]};
assert.deepEqual(evaluateSlotClear(request,inventory),{allowed:false,reason:'used-by-current-preset',slots:[17]});
assert.equal(evaluateSlotClear({...request,slots:[9]},inventory).allowed,false);
assert.equal(evaluateSlotClear({...request,slots:[18]},inventory).allowed,true);
assert.equal(evaluateSlotClear(request,{...inventory,revision:9}).reason,'preset-changed');
assert.equal(evaluateSlotClear(request,{...inventory,presetId:'different-preset'}).allowed,false);
for(const invalid of [null,{}, {...inventory,complete:false},{...inventory,references:null},
 {...inventory,references:[{slot:0,blockId:'bad'}]}, {...inventory,references:[{slot:17}]}])
 assert.equal(evaluateSlotClear(request,invalid).allowed,false);
for(const slots of [[],[0],[129],[17,17],['17'],[true],null])
 assert.equal(evaluateSlotClear({...request,slots},inventory).allowed,false);
const switched={...inventory,presetId:'stock',revision:9,references:[]};
assert.equal(evaluateSlotClear({...request,presetId:'stock',revision:9},switched).allowed,true);
assert.deepEqual(request.slots,[17,18]); // Does not trim a mixed selection and partially delete it.
'''
        result=subprocess.run(['node','-e',script],capture_output=True,text=True,timeout=15)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
