from pathlib import Path
import subprocess
import unittest

HERE=Path(__file__).resolve().parent


class SchemaSummaryTests(unittest.TestCase):
    def test_bounded_shapes_without_values(self):
        source='\n'.join((HERE/name).read_text() for name in ('current_preset_slots.js','current_preset_schema_summary.js'))
        source+=r'''
const assert=require('node:assert/strict');
const tree={globalNodes:{snapshot0:{'@name':'PRIVATE PRESET'},variax:{'@model':'PRIVATE'},
 controller:{dsp0:{block0:{Index:{'@min':11,'@max':21}}}},unused:{},
 odd:{'@name':'SECRET CAPTURE','C:\\private\\path':'SECRET'},bad:null}};
const result=describeCurrentPresetGlobalNodes(tree),text=JSON.stringify(result);
assert.equal(result.unknownGlobalCount,4);assert.equal(result.unknownGlobals.length,4);
assert.ok(!text.includes('PRIVATE')&&!text.includes('SECRET')&&!text.includes('11')&&!text.includes('21'));
assert.deepEqual(result.unknownGlobals.find(x=>x.name==='unused').fields,[]);
assert.equal(result.unknownGlobals.find(x=>x.name==='unused').propertyCount,0);
assert.ok(result.unknownGlobals.find(x=>x.name==='controller').fields.includes('dsp0.block0.Index.@min:number'));
assert.equal(result.unknownGlobals.find(x=>x.name==='bad').type,'null');
tree.globalNodes.huge=Object.fromEntries(Array.from({length:70},(_,i)=>['p'+i,{q:{r:{s:{t:'PRIVATE'}}}}]));
const huge=describeCurrentPresetGlobalNodes(tree).unknownGlobals.find(x=>x.name==='huge');
assert.ok(huge.truncated);assert.ok(huge.fields.length<=64);
for(let i=0;i<30;i++)tree.globalNodes['extra'+i]={};
assert.equal(describeCurrentPresetGlobalNodes(tree).unknownGlobals.length,16);
assert.deepEqual(describeCurrentPresetGlobalNodes(null),{unknownGlobals:[],unknownGlobalCount:0});
'''
        result=subprocess.run(['node'],input=source,capture_output=True,text=True,timeout=15)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
