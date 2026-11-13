'use strict';
const assert = require('node:assert/strict');
const {installTargetBinding: install, targetBindingGuards: guards,
  targetBindingRestoreAllowed: allowed} = require('../target_binding.js');
const memory=new Map(), text=new Map(), native=new Map();
let address=0x20000000, thread=42, constructions=0, destructions=0, writes=[];
class Pointer {
  constructor(value){this.value=Number(value);}
  add(n){return new Pointer(this.value+n);}
  isNull(){return this.value===0;}
  equals(other){return this.value===other.value;}
  readU32(){return Number(memory.get(this.value)||0)>>>0;}
  readS32(){return this.readU32()|0;}
  readU8(){return this.readU32()&255;}
  writeU32(value){memory.set(this.value,value);return this;}
  readPointer(){return new Pointer(this.readU32());}
  readFloat(){return Number(memory.get(this.value)||0);}
  writeFloat(value){memory.set(this.value,value);}
  writeByteArray(bytes){for(let i=0;i<bytes.length;i++)memory.set(this.value+i,bytes[i]);}
  readUtf8String(length){return text.get(this.value).slice(0,length);}
  readByteArray(size){return Uint8Array.from(Buffer.from(guards[(this.value-0x400000).toString(16)]||'00'.repeat(size),'hex')).buffer;}
}
const p=value=>new Pointer(value);
function alloc(size){const result=p(address);address+=size+32;return result;}
function utf8(value){const result=alloc(value.length+1);text.set(result.value,value);return result;}
function putString(object,value){
  const rep=alloc(20), data=utf8(value);
  object.add(4).writeU32(rep.value);rep.add(4).writeU32(value.length);rep.add(16).writeU32(data.value);
}
function getString(object){const rep=object.add(4).readPointer();return rep.add(16).readPointer().readUtf8String(rep.add(4).readU32());}
function putValue(pointer,value,type=typeof value==='string'?10:4){
  pointer.writeU32(type);
  if(type===10){const object=alloc(8);putString(object,value);pointer.add(8).writeU32(object.value);constructions++;}
  else if(type===8)pointer.add(8).writeFloat(value);
  else pointer.add(8).writeU32(value);
}
function getValue(pointer){return pointer.readU32()===10?getString(pointer.add(8).readPointer()):pointer.add(8).readFloat();}
global.Process={arch:'ia32',pointerSize:4,mainModule:{base:p(0x400000)},getCurrentThreadId:()=>thread};
global.Memory={alloc,allocUtf8String:utf8};
global.NativeFunction=function(at){assert.ok(native.has(at.value-0x400000));return native.get(at.value-0x400000);};
const editor=alloc(0x300), controller=alloc(0x110), slider=alloc(0x200);
editor.writeU32(0xd63dfc);p(0xd63dfc+0x7c).writeU32(0x9178c0);
p(0xd63dfc+0x88).writeU32(0x904f50);p(0xd63dfc+0xdc).writeU32(0x91aaf0);
controller.writeU32(0xcd948c);controller.add(0x34).writeU32(editor.value);controller.add(0x80).writeU32(0);
putString(controller.add(0x84),'block0');
slider.writeU32(0xce942c);putString(slider.add(0xf8),'Index');
let uiSlot=9, slotType=4;
const blocks=new Map([['0:block0',{'@model':'HD2_AmpNAMLite',Index:9,Drive:0.6,Bass:0.45,Treble:0.55}],
  ['0:block1',{'@model':'HD2_AmpNAMLite',Index:10,Drive:0.5,Bass:0.5,Treble:0.5}]]);
const unchangedParameters = JSON.parse(JSON.stringify([...blocks]));
native.set(0x1a6ac0,(out,source)=>{putValue(out,text.get(source.value),10);return out;});
native.set(0x1a1b30,value=>{if(value.readU32()===10)destructions++;value.writeU32(9);});
native.set(0x504f50,(instance,dsp,block,key,out)=>{
  assert.equal(instance.value,editor.value);
  const object=blocks.get(`${dsp}:${getValue(block)}`), parameter=getValue(key);
  if(!object||!Object.hasOwn(object,parameter))return -1;
  putValue(out,object[parameter],parameter==='Index'?slotType:undefined);return 0;
});
native.set(0x5178c0,(instance,dsp,block,key,value,flags)=>{
  assert.equal(instance.value,editor.value);assert.equal(flags,0x80000000);
  const identity=`${dsp}:${getValue(block)}`,parameter=getValue(key);
  writes.push({identity,parameter,value:getValue(value)});
  blocks.get(identity)[parameter]=getValue(value);return 0;
});
native.set(0x23cb50,(_model,out)=>{putValue(out,uiSlot,slotType);return out;});
const api=install();
const binding=api.capture(controller,slider,{context:7,slot:9});
assert.equal(binding.blockName,'block0');assert.equal(binding.dsp,0);assert.equal(binding.parameter,'Index');
assert.deepEqual(api.read(binding,{context:7}),{slot:9,valueType:4,engine:'HD2_AmpNAMLite'});
// Native cursor/controller and current slider now point to another NAM block.
putString(controller.add(0x84),'block1');uiSlot=10;
blocks.get('0:block0').Index=128;
assert.equal(api.restore(binding,9,{context:7,expectedSlot:128}),true);
assert.equal(blocks.get('0:block0').Index,9);
assert.equal(blocks.get('0:block1').Index,10,'restoring bound block must not edit selected block');
assert.deepEqual([...blocks],unchangedParameters,'Input and EQ on both blocks are retained');
assert.deepEqual(writes,[{identity:'0:block0',parameter:'Index',value:9}]);
assert.equal(constructions,destructions,'all temporary native strings are destroyed');
const count=writes.length;
assert.throws(()=>api.restore(binding,9,{context:7,expectedSlot:128}),/ownership/);
blocks.get('0:block0').Index=128;
assert.throws(()=>api.restore(binding,9,{context:8,expectedSlot:128}),/expired/);
assert.throws(()=>api.restore(binding,10,{context:7,expectedSlot:128}),/ownership/);
assert.throws(()=>api.restore(binding,9,{context:7,expectedSlot:127}),/ownership/);
blocks.get('0:block0')['@model']='HD2_AmpStock';
assert.throws(()=>api.restore(binding,9,{context:7,expectedSlot:128}),/supported NAM/);
blocks.get('0:block0')['@model']='HD2_AmpNAMLite';
thread=3;assert.throws(()=>api.read(binding,{context:7}),/UI thread/);thread=42;
editor.writeU32(0x1234);assert.throws(()=>api.read(binding,{context:7}),/editor instance/);editor.writeU32(0xd63dfc);
assert.equal(writes.length,count,'guard failures never write');
assert.equal(constructions,destructions);
api.release(binding);assert.equal(binding.valid,false);
assert.throws(()=>api.read(binding,{context:7}),/expired/);
assert.throws(()=>api.capture(controller,slider,{context:7,slot:128}),/permanent original/);
putString(slider.add(0xf8),'Drive');
assert.throws(()=>api.capture(controller,slider,{context:7,slot:10}),/parameter identity/);
putString(slider.add(0xf8),'Index');
uiSlot=12;assert.throws(()=>api.capture(controller,slider,{context:7,slot:10}),/disagree/);
assert.equal(constructions,destructions);
for(const type of [5,8]){
  slotType=type;uiSlot=10;
  const typed=api.capture(controller,slider,{context:9,slot:10});
  blocks.get('0:block1').Index=128;
  assert.equal(api.restore(typed,10,{context:9,expectedSlot:128}),true);
  assert.equal(api.read(typed,{context:9}).valueType,type);
  api.release(typed);
}
assert.equal(constructions,destructions);
assert.deepEqual(blocks.get('0:block1'),unchangedParameters[1][1]);
assert.equal(allowed({valid:true,context:1,originalSlot:9,engine:'n',valueType:4},
  {slot:128,engine:'n',valueType:4},{context:1,originalSlot:9,expectedSlot:128}),true);
for(const change of [{valid:false},{context:2},{originalSlot:128},{engine:'x'},{valueType:8}]){
  assert.equal(allowed({valid:true,context:1,originalSlot:9,engine:'n',valueType:4,...change},
    {slot:128,engine:'n',valueType:4},{context:1,originalSlot:9,expectedSlot:128}),false);
}
const original=Pointer.prototype.readByteArray;
Pointer.prototype.readByteArray=function(size){return new Uint8Array(size).buffer;};
assert.throws(()=>install(),/byte guard/);Pointer.prototype.readByteArray=original;
console.log('target binding probe passed: explicit old block, context, thread, engine, preview, value, lifetime and allocations');
