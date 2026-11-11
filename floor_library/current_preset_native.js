// HX Edit 3.82 x86 only. Pure local-memory reads: no native calls, locks, USB,
// serialization callbacks, or device requests. The caller must run on the editor
// UI thread at the guarded Clear boundary and establish readiness/lifecycle.
// Layout evidence: native_current_preset_static.py and clear-reference-evidence/.
class CurrentPresetReadError extends Error {
  constructor(code) { super(code); this.name='CurrentPresetReadError'; this.code=code; }
}

function createCurrentPresetMemoryReader() {
  const limits={nodes:512,properties:512,array:2048,variants:30000,depth:24,string:8192};
  function fail(code) { throw new CurrentPresetReadError(code); }
  function present(p) { if(!p || p.isNull())fail('missing-memory'); return p; }
  function same(a,b) { return a.toString()===b.toString(); }
  function decode(root) {
    let budget=limits.variants;
    const active=new Set();
    function spend() { if(--budget<0)fail('value-budget'); }
    function enter(p,fn) {
      const id=p.toString();if(active.has(id))fail('value-cycle');
      active.add(id);try{return fn();}finally{active.delete(id);}
    }
    function put(obj,key,value) {
      if(typeof key!=='string' || key.length===0 || Object.hasOwn(obj,key))fail('invalid-or-duplicate-key');
      Object.defineProperty(obj,key,{value,enumerable:true,writable:true,configurable:true});
    }
    function bounds(rep,bo,eo,max) {
      const begin=rep.add(bo).readU32(),end=rep.add(eo).readU32();
      if(end<begin || begin>8192 || end-begin>max)fail('invalid-bounds');
      return {begin,end};
    }
    function tree(first,left,right,max,visit) {
      const pending=[first],seen=new Set();
      while(pending.length) {
        const node=pending.pop();if(node.isNull())continue;
        const id=node.toString();if(seen.has(id))fail('tree-cycle');
        if(seen.size>=max)fail('tree-budget');seen.add(id);spend();
        visit(node);pending.push(node.add(left).readPointer(),node.add(right).readPointer());
      }
    }
    function value(p,depth=0) {
      present(p);spend();if(depth>limits.depth)fail('value-depth');
      const tag=p.readU32(),v=p.add(8);
      if(tag===4)return v.readS32();
      if(tag===5)return v.readU32();
      if(tag===8){const n=v.readFloat();if(!Number.isFinite(n))fail('nonfinite-number');return n;}
      if(tag===9){if(v.readU32()!==0)fail('opaque-pointer-value');return null;}
      if(tag===11){const b=v.readU8();if(b>1)fail('invalid-boolean');return b===1;}
      if(tag===10){
        const object=present(v.readPointer()),rep=object.add(4).readPointer();
        if(rep.isNull())return '';
        const length=rep.add(4).readU32();if(length>limits.string)fail('string-budget');
        if(length===0)return '';
        return present(rep.add(16).readPointer()).readUtf8String(length);
      }
      if(tag===12){
        const rep=present(v.readPointer());
        return enter(rep,()=>{
          const {begin,end}=bounds(rep,16,20,limits.array),data=rep.add(8).readPointer(),out=[];
          if(end>begin)present(data);
          for(let i=begin;i<end;i++)out.push(value(data.add(i*16),depth+1));
          return out;
        });
      }
      if(tag===13){
        const rep=present(v.readPointer());
        return enter(rep,()=>{
          const out={};tree(rep.add(4).readPointer(),40,44,limits.nodes,node=>
            put(out,value(node.add(8),depth+1),value(node.add(24),depth+1)));
          return out;
        });
      }
      // Unsupported tags are incomplete, never silently discarded.
      fail('unsupported-variant-'+tag);
    }
    function properties(array) {
      const {begin,end}=bounds(array,20,24,limits.properties),data=array.add(12).readPointer(),out={};
      if(end>begin)present(data);
      for(let i=begin;i<end;i++){
        const pair=present(data.add(i*4).readPointer());
        put(out,value(pair),value(pair.add(16)));
      }
      return out;
    }
    function nodes(offset) {
      const out={};tree(root.add(offset+4).readPointer(),68,72,limits.nodes,node=>
        put(out,value(node.add(8)),properties(node.add(24))));return out;
    }
    return {global:properties(root.add(24)),globalNodes:nodes(68),dsp0:nodes(76),dsp1:nodes(84)};
  }
  function read(editor,base,revision) {
    try {
      present(editor);present(base);
      if(!same(editor.readPointer(),base.add(0x963dfc)))fail('editor-vtable');
      const engine=present(editor.add(0x124).readPointer()),root=engine.add(0x30);
      if(root.add(4).readU32()!==6)fail('unsupported-model-kind');
      const undo=editor.add(0x294).readS32(),startRevision=revision();
      if(!Number.isSafeInteger(startRevision))fail('revision-unavailable');
      // Two independent copies catch concurrent changes without invoking engine
      // mutexes. Readiness and event-generation checks remain caller obligations.
      const first=decode(root),second=decode(root);
      if(JSON.stringify(first)!==JSON.stringify(second) ||
          !same(editor.add(0x124).readPointer(),engine) ||
          !same(editor.readPointer(),base.add(0x963dfc)) ||
          root.add(4).readU32()!==6 || editor.add(0x294).readS32()!==undo ||
          revision()!==startRevision)fail('edit-buffer-changed');
      return {complete:true,revision:startRevision,undo,tree:first};
    } catch(error) {
      return {complete:false,reason:error instanceof CurrentPresetReadError?error.code:'memory-unavailable'};
    }
  }
  return {read};
}
if(typeof module!=='undefined' && module.exports)module.exports={createCurrentPresetMemoryReader,CurrentPresetReadError};

// Synchronization wrapper: injected APIs must be the pinned imported
// WaitForSingleObject / ReleaseMutex (stdcall). Timeout is always ZERO. Stock
// order is editor+0x134, then engine+0x18; release reverses that order. No getter,
// serializer, worker, or USB function is called here.
function readCurrentPresetSynchronized(editor,base,revision,api) {
  const held=[];let result=null,releaseFailed=false;
  const incomplete=reason=>({complete:false,reason});
  function acquire(object) {
    if(object.readPointer().toString()!==base.add(0x92dc4c).toString())
      throw new CurrentPresetReadError('unexpected-lock-type');
    const rep=object.add(4).readPointer();if(rep.isNull())throw new CurrentPresetReadError('lock-storage-missing');
    const handle=rep.readPointer();if(handle.isNull())throw new CurrentPresetReadError('lock-handle-missing');
    const status=api.wait(handle,0)>>>0;
    if(status===0 || status===0x80)held.push(handle);
    if(status===0x80)throw new CurrentPresetReadError('abandoned-edit-buffer-lock');
    if(status!==0)throw new CurrentPresetReadError(status===0x102?'edit-buffer-busy':'edit-buffer-lock-failed');
  }
  try {
    if(!editor || editor.isNull() || editor.readPointer().toString()!==base.add(0x963dfc).toString())
      throw new CurrentPresetReadError('editor-vtable');
    acquire(editor.add(0x134));
    const engine=editor.add(0x124).readPointer();
    if(engine.isNull())throw new CurrentPresetReadError('missing-engine');
    acquire(engine.add(0x18));
    result=createCurrentPresetMemoryReader().read(editor,base,revision);
  }catch(error){result=incomplete(error instanceof CurrentPresetReadError?error.code:'memory-or-lock-unavailable');}
  finally {
    for(let i=held.length-1;i>=0;i--){
      try {if(!api.release(held[i]))releaseFailed=true;}catch(_){releaseFailed=true;}
    }
  }
  return releaseFailed?incomplete('edit-buffer-unlock-failed'):result;
}
if(typeof module!=='undefined' && module.exports)module.exports.readCurrentPresetSynchronized=readCurrentPresetSynchronized;
