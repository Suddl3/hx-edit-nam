from pathlib import Path
import subprocess
import unittest

HERE = Path(__file__).resolve().parent

class ClearDispatchTests(unittest.TestCase):
    def test_atomic_submission_fresh_state_and_fail_closed(self):
        code = '\n'.join((HERE / n).read_text() for n in
                         ('slot_clear_policy.js', 'slot_clear_dispatch.js'))
        code += r'''
const assert=require('node:assert/strict');
let revision=1, refs=[{slot:17,blockId:'dsp1:block4'}], submitted=[], blocked=[], fail='', change=false;
const api={selection:value=>{if(fail==='selection')throw Error();return value;},
 context:()=>fail==='context'?null:{presetId:'edit',revision},
 inventory:ctx=>{if(fail==='inventory')throw Error();if(change)revision++;
  return {...ctx,complete:true,references:refs};},
 submit:(m,s)=>submitted.push([...s]),blocked:d=>blocked.push(d.reason)};
const clear=createSlotClearDispatch(api);
assert.equal(clear('manager',[17,18]).allowed,false);assert.deepEqual(submitted,[]);
assert.deepEqual(blocked,['used-by-current-preset']);
assert.equal(clear('manager',[18]).allowed,true);assert.deepEqual(submitted,[[18]]);
refs=[];assert.equal(clear('manager',[17]).allowed,true); // fresh switched/edited state
// Owner reproduction: changing or selecting a different IR-list row is not
// evidence that the current preset uses that slot. Only its decoded references
// determine protection, and those are freshly checked on every submission.
refs=[1,2,3].map(slot=>({slot,blockId:'dsp0:block'+slot}));
assert.equal(clear('manager',[20]).allowed,true);
assert.equal(clear('manager',[2,20]).allowed,false);
assert.equal(submitted.at(-1)[0],20);
refs=[{slot:18,blockId:'dsp0:block1',bypassed:true,snapshot:7}];
assert.equal(clear('manager',[18]).allowed,false); // no cached allow decision
change=true;assert.equal(clear('manager',[19]).reason,'preset-changed');change=false;
for(fail of ['selection','inventory','context'])assert.equal(clear('manager',[19]).allowed,false);
fail='';assert.deepEqual(submitted,[[18],[17],[20]]);
api.blocked=()=>{assert.equal(clear('manager',[19]).allowed,false);};
assert.equal(clear('manager',[18]).allowed,false); // modal reentrancy cannot delete
api.blocked=()=>{throw Error('must not notify automated cleanup');};
assert.equal(clear('manager',[18],false).allowed,false);
assert.deepEqual(submitted,[[18],[17],[20]]);
api.blocked=()=>{throw Error('notification unavailable');};
assert.equal(clear('manager',[18]).allowed,false); // callbacks never escape into native host
api.submit=()=>{throw Error('submission fault');};
assert.equal(clear('manager',[19]).reason,'submission-unconfirmed');
'''
        result=subprocess.run(['node','-e',code],capture_output=True,text=True,timeout=15)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
