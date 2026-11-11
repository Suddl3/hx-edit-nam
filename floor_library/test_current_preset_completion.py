from pathlib import Path
import subprocess
import unittest


class CurrentPresetCompletionTests(unittest.TestCase):

    def test_bounded_notification_result_decoder(self):
        code=Path(__file__).with_name('current_preset_completion.js').read_text()+r'''
const assert=require('node:assert/strict');
let memory=new Map();
class P {
 constructor(n){this.n=n;}add(n){return new P(this.n+n);}isNull(){return this.n===0;}
 toString(){return String(this.n);}
 get(){if(!memory.has(this.n))throw Error('unreadable');return memory.get(this.n);}
 readPointer(){return new P(this.get());}readU32(){return this.get()>>>0;}readS32(){return this.get()|0;}
 readUtf8String(length){const s=this.get();if(s.includes('\0'))throw Error('invalid UTF8 NUL');return s.slice(0,length);}
}
const ptr=n=>new P(n);
function fixture(){
 memory=new Map([[100,13],[108,200],[204,300],
 [308,10],[316,400],[404,500],[504,6],[516,600],[600,'result'],
 [324,4],[332,0],[340,0],[344,0]]);
}
fixture();assert.equal(readPresetContentsResult(ptr(100)),0);
memory.set(332,-1);assert.equal(readPresetContentsResult(ptr(100)),-1);
memory.set(324,5);memory.set(332,0xffffffff);assert.equal(readPresetContentsResult(ptr(100)),0xffffffff);
for(const [address,value] of [[100,9],[108,0],[204,0],[308,4],[316,0],[404,0],[504,0],[504,161],[516,0],[324,8],[340,300]]){
 fixture();memory.set(address,value);assert.throws(()=>readPresetContentsResult(ptr(100)),String(address));
}
fixture();memory.set(600,'unused');assert.throws(()=>readPresetContentsResult(ptr(100)));
fixture();memory.set(344,700);memory.set(708,10);memory.set(716,400);memory.set(724,4);memory.set(732,0);
memory.set(740,0);memory.set(744,0);assert.throws(()=>readPresetContentsResult(ptr(100)),/Duplicate/);
fixture();memory.delete(340);assert.throws(()=>readPresetContentsResult(ptr(100)),/unreadable/);
'''
        result=subprocess.run(['node'],input=code,capture_output=True,text=True,timeout=15)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
