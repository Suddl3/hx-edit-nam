'use strict';
const fs=require('node:fs'),vm=require('node:vm'),path=require('node:path'),assert=require('node:assert/strict');
const root=path.join(__dirname,'..'), receivers=new Map(),queued=[],sent=[],opened=[];
let sink,accept=true;
const window={toUInt32:()=>0xffffffff};
const namView={setSidebarSink:fn=>sink=fn,queueSidebarAction:fn=>{if(!accept)return false;queued.push(fn);return true;},
  resizeSidebarFromChild:width=>width===400,openExpandedLibrary:hwnd=>opened.push(['expand',hwnd]),setSidebarChildReady:()=>{}};
const context=vm.createContext({send:value=>sent.push(value),recv:(kind,fn)=>receivers.set(kind,fn),
  toneView:{openBrowser:(hwnd,options)=>{opened.push([hwnd,options]);return true;}},navigationBanner:{dockPanel:()=>42}});
vm.runInContext(fs.readFileSync(path.join(root,'nam_sidebar.js'),'utf8'),context,{timeout:1000});
context.installNamSidebar(namView);
sink({visible:true},window);sink({visible:true},window);
assert.equal(sent.length,1,'Unchanged mouse/paint geometry does not republish');
const identity='a'.repeat(64);
receivers.get('namSidebarAction')({payload:{action:'load',id:identity}});
assert.equal(opened.length,0,'Receive thread must not invoke native controls');
queued.shift()(window);
assert.equal(opened[0][1].libraryAction,'load:'+identity);assert.equal(opened[0][1].headless,true);
receivers.get('namSidebarAction')({payload:{action:'expand'}});queued.shift()(window);
assert.equal(opened[1][0],'expand');
accept=false;receivers.get('namSidebarAction')({payload:{action:'load',id:identity}});
assert.equal(sent.at(-1).type,'namSidebarResult');assert.equal(sent.at(-1).busy,false);

const source=fs.readFileSync(path.join(root,'nam_view.js'),'utf8');
const begin=source.indexOf('  function sidebarPresentation()'),end=source.indexOf('  function pumpSidebarPresentation(',begin);
const seen=new Map(),changes=[];
const widget=id=>({id,isNull:()=>false});
const widgets={0x6e626172:widget('toolbar'),0x6e6d7367:widget('opening'),0x6e657272:widget('error')};
seen.set('error',false);['list','toolbar','opening'].forEach(id=>seen.set(id,true));
const frame=vm.createContext({libraryReady:()=>true,sidebar:()=>({side:{w:360}}),namPanel:widget('panel'),
  sidebarHost:window,tab:3,sidebarChildReady:false,sidebarChildFailed:true,namList:widget('list'),allCapturesButton:{isNull:()=>true},
  findWidget:(_p,id)=>widgets[id],isVisible:(w,ancestor)=>w.id==='panel'?true:!!seen.get(w.id),
  setVisible:(w,value)=>{changes.push([w.id,value]);seen.set(w.id,!!value);},
  widgetOrigin(){},sidebarPoint:{readS32:()=>0,add:()=>({readS32:()=>75})},box:()=>({w:360,h:700}),
  workflowBusy:false,discoveryLocked:false,exportPending:false,hasActiveModel:()=>true,discoveryStatus:'inactive',discoveryChecked:0,discoveryTotal:0,discoveryCountedAt:-1,slotLedger:null});
frame.sidebarHost={isNull:()=>false};
vm.runInContext(source.slice(begin,end),frame,{timeout:1000});
frame.sidebarPresentation();assert.deepEqual(changes,[['list',0],['toolbar',0],['opening',0],['error',1]]);
changes.length=0;frame.sidebarPresentation();assert.equal(changes.length,0,'Unchanged fallback does not relayout or redraw');
frame.sidebarChildReady=true;frame.sidebarChildFailed=false;frame.sidebarPresentation();assert.deepEqual(changes,[['error',0]]);
// Candidate58-60 on the Floor: the native visibility call returns 1, the bridge compared the
// flag with true, and Load/Use on Floor stayed unavailable with a NAM block selected.
frame.hasActiveModel=()=>1;assert.strictEqual(frame.sidebarPresentation().targetReady,true);
frame.hasActiveModel=()=>0;assert.strictEqual(frame.sidebarPresentation().targetReady,false);
console.log('sidebar bridge passed: deferred native actions, deduplication, queue rejection and visible failure fallback');
