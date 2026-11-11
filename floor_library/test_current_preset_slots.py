from pathlib import Path
import json
import subprocess
import unittest
import msgpack

HERE=Path(__file__).resolve().parent

class CurrentPresetSlotsTests(unittest.TestCase):
    def default_tree(self):
        # Minimal synthetic shape, not a copied preset or device export.
        nodes = {key: {'@model': 'SyntheticEndpoint'} for key in
                 ('inputA', 'inputB', 'outputA', 'outputB', 'split', 'join')}
        return {'global': {'@current_snapshot': 0},
                'dsp0': {key: dict(value) for key, value in nodes.items()},
                'dsp1': {key: dict(value) for key, value in nodes.items()},
                'globalNodes': {'snapshot' + str(i): {'@valid': True, 'blocks': {}}
                                for i in range(8)}}

    def run_js(self,script):
        result=subprocess.run(['node','-e',(HERE/'current_preset_slots.js').read_text()+'\n'+script],
                              capture_output=True,text=True,timeout=15)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)

    def catalog(self):
        # Only the symbols exercised by this synthetic fixture.
        return {'SyntheticEndpoint': [], 'HD2_AmpNAMLite': ['Index'],
                'HD2_ImpulseResponse1024': ['Index'],
                'HD2_ImpulseResponse2048': ['Index'],
                'HD2_ImpulseResponse1024Dual': ['Index', 'Index_1']}


    def test_all_paths_bypass_dual_ir_and_unsupported_assignments(self):
        catalog=json.dumps(self.catalog())
        code=f'const catalog={catalog};const endpoints={json.dumps(self.default_tree())};\n'+r'''
const assert=require('node:assert/strict');
const ctx={presetId:'edit',revision:1};
const tree={global:{'@current_snapshot':0},globalNodes:{},dsp0:{
 block0:{'@model':'HD2_AmpNAMLite','@enabled':false,Index:9},
 block1:{'@model':'HD2_ImpulseResponse1024Dual',Index:17,Index_1:127}},dsp1:{
 block0:{'@model':'HD2_ImpulseResponse2048',Index:128}}};
for(let i=0;i<8;i++)tree.globalNodes['snapshot'+i]={'@name':'SNAPSHOT '+(i+1),
 '@valid':true,blocks:{dsp0:{block0:false,block1:true},dsp1:{block0:true}}};
tree.dsp0={...endpoints.dsp0,...tree.dsp0};tree.dsp1={...endpoints.dsp1,...tree.dsp1};
const collect=()=>collectCurrentPresetSlots(tree,ctx,catalog);
assert.equal(collect().complete,true);
assert.deepEqual(collect().references.map(x=>x.slot),[9,17,127,128]);
const original=structuredClone(tree);
tree.dsp0.block0.Index=0;assert.equal(collect().complete,false);tree.dsp0.block0.Index=9;
tree.dsp0.block0['@model']='UnknownNewModel';assert.equal(collect().reason,'unknown-model');
tree.dsp0.block0['@model']='HD2_AmpNAMLite';
tree.globalNodes.snapshot7.controllers={dsp0:{block0:{Index:38}}};
assert.equal(collect().reason,'unsupported-snapshot-values');delete tree.globalNodes.snapshot7.controllers;
tree.globalNodes.controller={dsp0:{block0:{Index:{'@min':1,'@max':128}}}};
assert.equal(collect().reason,'unknown-global-node');delete tree.globalNodes.controller;
tree.globalNodes.surprise={Index:28};assert.equal(collect().reason,'unknown-global-node');
delete tree.globalNodes.surprise;
delete tree.globalNodes.snapshot7;assert.equal(collect().reason,'missing-snapshot');
tree.globalNodes.snapshot7=original.globalNodes.snapshot7;
delete tree.dsp1.block0.Index;assert.equal(collect().reason,'invalid-slot-value');
tree.dsp1.block0.Index=128;
const plain=structuredClone(tree);plain.dsp0=endpoints.dsp0;plain.dsp1=endpoints.dsp1;
for(const node of Object.values(plain.globalNodes))if(node.blocks)node.blocks={};
assert.deepEqual(collectCurrentPresetSlots(plain,ctx,catalog).references,[]);
assert.equal(collectCurrentPresetSlots(plain,ctx,catalog).complete,true);
delete plain.dsp1.inputB;assert.equal(collectCurrentPresetSlots(plain,ctx,catalog).reason,'missing-path-nodes');
'''
        self.run_js(code)



    def test_only_exact_empty_global_records_are_ignored(self):
        self.run_js('const assert=require("node:assert/strict");const baseline='
            +json.dumps(self.default_tree())+';const catalog='+json.dumps(self.catalog())+r''';
const ctx={presetId:'empty-native-node-fixture',revision:0};
const collect=tree=>collectCurrentPresetSlots(tree,ctx,catalog);
for(const name of ['controller','command','not-a-guessed-whitelist-name']){
 const tree=structuredClone(baseline);tree.globalNodes[name]={};
 assert.equal(collect(tree).complete,true,name);
 for(const populated of [{Index:17},{'@min':1,'@max':128},{blocks:{}},{value:null},
                         {dsp0:{block0:{Index:17}}},[],null,0,'']){
  tree.globalNodes[name]=populated;
  assert.equal(collect(tree).complete,false,`${name}: ${JSON.stringify(populated)}`);
 }
}
let tree=structuredClone(baseline);tree.dsp0.unknown={};
assert.equal(collect(tree).reason,'unknown-node'); // no relaxation for DSP nodes
tree=structuredClone(baseline);tree.globalNodes.unused={};delete tree.globalNodes.snapshot7;
assert.equal(collect(tree).reason,'missing-snapshot');
tree=structuredClone(baseline);tree.globalNodes.unused={};delete tree.dsp1.inputB;
assert.equal(collect(tree).reason,'missing-path-nodes');
''')
