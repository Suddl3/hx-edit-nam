"""Startup/recreation tab layout regressions; synthetic widgets, no UI/device."""
from pathlib import Path
import subprocess
import unittest

HERE = Path(__file__).resolve().parent


class TabStartupTests(unittest.TestCase):
    def test_observed_native_heights_reorder_without_changing_identity_or_size(self):
        self.node(r'''
const assert=require('node:assert/strict');
// Candidate51 read-only snapshot: 360x25 bar, 88px tabs, heights 23/25/23/23.
for(const selected of [0,1,2,3]) {
 for(const width of [300,311,312,360,413,600]) {
  const radio={},buttons=[0,25,50,75].map((x,i)=>({x,id:i,h:i===selected?25:23}));
  const before=buttons.map(b=>({id:b.id,h:b.h}));let calls=0;
  const api={ready:()=>true,radio:()=>radio,buttons:()=>buttons,
   box:b=>b===radio?{x:0,y:0,w:width,h:25}:
    {x:(Math.floor((width-6)/4)+2)*b.x/25,y:0,w:Math.floor((width-6)/4),h:b.h},
   visualIndex:b=>b.x/25,redraw:()=>{},
   unanchoredHorizontally:()=>true,setVisualIndex:(b,index)=>{b.x=index*25;calls++;}};
  const order=createLibraryTabOrder(api);
  assert.equal(order.refresh(),true);
  assert.deepEqual(buttons.map(b=>b.x),[0,25,75,50]);
  assert.deepEqual(buttons.map(b=>({id:b.id,h:b.h})),before);
  assert.equal(order.refresh(),true);assert.equal(calls,2);
  if(width===360)assert.deepEqual(buttons.map(b=>api.box(b).x),[0,90,270,180]);
 }
}
''')

    def test_invalid_height_still_blocks_native_mutation(self):
        self.node(r'''
const assert=require('node:assert/strict');
for(const badHeight of [0,10,22,26,NaN]) {
 const radio={},buttons=[0,90,180,270].map((x,i)=>({x,y:0,w:88,h:i===2?badHeight:23}));
 let calls=0;
 const order=createLibraryTabOrder({ready:()=>true,radio:()=>radio,buttons:()=>buttons,
  box:b=>b===radio?{x:0,y:0,w:360,h:25}:b,
  visualIndex:b=>b.x/90,redraw:()=>{},
  unanchoredHorizontally:()=>true,setVisualIndex:()=>calls++});
 assert.equal(order.refresh(),false);assert.equal(calls,0);
}
''')

    def node(self, code):
        result = subprocess.run(['node', '-e', (HERE/'library_tab_order.js').read_text()+code],
                                capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_missing_initial_geometry_recovers_and_recreation_resets_fault(self):
        self.node(r'''
const assert=require('node:assert/strict');
let layout=false,fail=false,width=360,calls=0;
const radio={},buttons=[0,25,50,75].map(x=>({x})),reports=[];
const api={ready:()=>true,radio:()=>radio,buttons:()=>buttons,
 box:b=>!layout?null:b===radio?{x:0,y:0,w:width,h:25}:
   {x:(Math.floor((width-6)/4)+2)*b.x/25,y:0,w:Math.floor((width-6)/4),h:25},
 visualIndex:b=>b.x/25,redraw:()=>{},
 unanchoredHorizontally:()=>true,setVisualIndex:(b,index)=>{if(fail)throw Error('fault');b.x=index*25;calls++;},
 report:r=>reports.push(r)};
const order=createLibraryTabOrder(api);
assert.equal(order.refresh(),false);assert.equal(order.refresh(),false);assert.equal(calls,0);
assert.deepEqual(reports,['geometry-not-ready']);
layout=true;assert.equal(order.refresh(),true);assert.equal(calls,2);
assert.deepEqual(buttons.map(b=>b.x),[0,25,75,50]);
for(width of [300,311,360,413,600])assert.equal(order.refresh(),true);
assert.equal(calls,2);
buttons[2].x=50;buttons[3].x=75;fail=true;assert.equal(order.refresh(),false);
fail=false;assert.equal(order.refresh(),false); // Native faults do not blindly retry.
order.reset();assert.equal(order.refresh(),true);assert.equal(calls,4);
''')

    def test_native_geometry_resolves_proxy_and_rejects_cycles(self):
        self.node(r'''
const assert=require('node:assert/strict'),memory=new Map();
class P{constructor(n){this.n=n;}add(n){return new P(this.n+n);}isNull(){return this.n===0;}
 toString(){return String(this.n);}readPointer(){return new P(memory.get(this.n)||0);}
 readS32(){return memory.get(this.n)||0;}}
const api=createLibraryTabNativeApi(()=>()=>{},x=>new P(x),()=>new P(1),()=>true);
const widget=new P(100),proxy=new P(200),g=1000,g2=2000;
assert.equal(api.box(widget),null);
memory.set(100+0x54,g);memory.set(g+0xb4,200);memory.set(200+0x54,g2);
for(const [offset,value] of [[4,90],[8,0],[12,180],[16,25]])memory.set(g2+offset,value);
assert.deepEqual(api.box(widget),{x:90,y:0,w:90,h:25});
memory.set(g2+0xb4,100);assert.throws(()=>api.box(widget),/proxy chain/);
''')

    def test_native_adapter_uses_custom_layout_and_preserves_radio_vector(self):
        self.node(r'''
const assert=require('node:assert/strict'),m=new Map(),calls=[];
class P{constructor(n){this.n=n;}add(n){return new P(this.n+n);}isNull(){return !this.n;}
 equals(p){return this.n===p.n;}toString(){return String(this.n);}
 readPointer(){return new P(m.get(this.n)||0);}readS32(){return m.get(this.n)||0;}
 readU32(){return (m.get(this.n)||0)>>>0;}readU8(){return (m.get(this.n)||0)&255;}}
const root=new P(100),radio=new P(200),list=300;
const buttons=[0,1,2,3].map(i=>new P(1000+i*0x400));
m.set(200,0x915358);m.set(200+0x7c,list);m.set(list+8,4);
for(const [i,b] of buttons.entries()){
 const g=10000+i*0x100;m.set(b.n,0x8c35a0);m.set(b.n+0x54,g);
 m.set(g,0xffff0000+i);m.set(b.n+0x212,1);m.set(b.n+0x218,i);
}
function method(rva,type,args){
 if(rva===0x3b7ae0)return()=>radio;
 if(rva===0x389f30)return(_,i)=>buttons[i];
 if(rva===0x3b8790)return()=>radio;
 if(rva===0x3b7f30)return()=>-1;
 if(rva===0x1f77e0)return(b,enabled,index)=>{calls.push(['configure',b.n,enabled,index]);m.set(b.n+0x218,index);};
 if(rva===0x1f7460)return b=>calls.push(['layout',b.n]);
 if(rva===0x3baa40)return b=>calls.push(['redraw',b.n]);
 throw Error('Unexpected native method '+rva.toString(16));
}
const api=createLibraryTabNativeApi(method,n=>new P(n),()=>root,()=>true);
assert.equal(api.radio(),radio);assert.deepEqual(api.buttons(radio),buttons);
api.setVisualIndex(buttons[2],3);api.setVisualIndex(buttons[3],2);api.redraw(radio);
assert.deepEqual(buttons.map(b=>api.visualIndex(b)),[0,1,3,2]);
assert.deepEqual(buttons.map(b=>b.add(0x54).readPointer().readU32()),[0xffff0000,0xffff0001,0xffff0002,0xffff0003]);
assert.deepEqual(calls,[['configure',buttons[2].n,1,3],['layout',buttons[2].n],
 ['configure',buttons[3].n,1,2],['layout',buttons[3].n],['redraw',radio.n]]);
for(const [location,bad] of [[buttons[2].n,0x1234],[buttons[2].n+0x212,0]]){
 const previous=m.get(location);m.set(location,bad);assert.equal(api.buttons(radio),null);m.set(location,previous);
}
const g=buttons[2].add(0x54).readPointer().n;
m.set(g,123);assert.equal(api.buttons(radio),null);m.set(g,0xffff0002);
m.set(g+0xb4,800);assert.equal(api.buttons(radio),null);
''')
