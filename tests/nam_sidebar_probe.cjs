'use strict';
const fs=require('node:fs'),vm=require('node:vm'),path=require('node:path'),assert=require('node:assert/strict');
const root=path.join(__dirname,'..','src'), receivers=new Map(),queued=[],sent=[],opened=[];
let sink,accept=true,blockResult='nam',blockReady=false,nativeOk=true;const made=[],started=[],taken=new Set();
const window={toUInt32:()=>0xffffffff};
const namView={setSidebarSink:fn=>sink=fn,queueSidebarAction:fn=>{if(!accept)return false;queued.push(fn);return true;},
  resizeSidebarFromChild:width=>width===400,openExpandedLibrary:hwnd=>opened.push(['expand',hwnd]),setSidebarChildReady:()=>{}};
const context=vm.createContext({send:value=>sent.push(value),recv:(kind,fn)=>receivers.set(kind,fn),
  toneView:{openBrowser:(hwnd,options)=>{opened.push([hwnd,options]);return true;},
    makeNamBlock:gear=>{made.push(gear);return blockResult;},selectedIsNam:()=>blockReady,
    startChainDrag:id=>{started.push(id);return nativeOk;},chainDragTook:id=>taken.delete(id)},navigationBanner:{dockPanel:()=>42}});
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

{
  accept=true;sent.length=0;opened.length=0;queued.length=0;
  let drop={state:'none'};const timers=[];
  context.setTimeout=fn=>timers.push(fn);
  namView.clickChain=(x,y)=>{if(x<0)return false;drop={state:'pending',posted:Date.now()};return true;};
  namView.dropState=()=>drop;namView.endDrop=()=>{drop={state:'none'};};
  const run=()=>{while(timers.length||queued.length){if(timers.length)timers.shift()();if(queued.length)queued.shift()(window);}};
  const action=payload=>{receivers.get('namSidebarAction')({payload});queued.shift()(window);};
  action({action:'drop',id:identity,x:-1,y:5,mode:'use'});
  assert.match(sent.at(-1).error,/signal chain/);assert.equal(opened.length,0);
  action({action:'drop',id:identity,x:10,y:5,mode:'use'});
  timers.shift()();queued.shift()(window);                      // HX Edit has not handled the click yet
  assert.equal(opened.length,0);
  drop={state:'clicked',posted:Date.now()-100,clicked:Date.now()-300,namBlock:true};
  run();
  assert.equal(opened.at(-1)[1].libraryAction,'use:'+identity,'a NAM block under the drop gets the capture');
  assert.equal(drop.state,'none');
  // An empty cell or another kind of block becomes a NAM block first, then the capture goes on it.
  blockResult='adding';
  action({action:'drop',id:identity,x:10,y:5,mode:'load',gear:'pedal'});
  drop={state:'clicked',posted:Date.now()-100,clicked:Date.now()-300};
  let before=opened.length;
  timers.shift()();queued.shift()(window);                      // the block row is chosen
  assert.deepEqual(made.at(-1),'pedal');
  timers.shift()();queued.shift()(window);                      // HX Edit has not built it yet
  assert.equal(opened.length,before);
  blockReady=true;run();
  assert.equal(opened.at(-1)[1].libraryAction,'load:'+identity,'the new NAM block gets the capture');
  assert.equal(made.filter(gear=>gear==='pedal').length,1,'the block is added once');
  // Somewhere a NAM block cannot go: nothing is changed and the reason is shown.
  blockResult='A NAM block cannot go there.';blockReady=false;
  action({action:'drop',id:identity,x:10,y:5,mode:'use',gear:''});
  drop={state:'clicked',posted:Date.now()-100,clicked:Date.now()-300};
  before=opened.length;run();
  assert.equal(opened.length,before);assert.match(sent.at(-1).error,/cannot go there/);
  blockResult='nam';
  action({action:'drop',id:identity,x:10,y:5,mode:'load'});
  drop={state:'pending',posted:Date.now()-5000};run();
  assert.match(sent.at(-1).error,/did not take the drop/);assert.equal(sent.at(-1).busy,false);
  action({action:'drop',id:'nope',x:10,y:5,mode:'use'});assert.equal(drop.state,'none','a malformed drop does nothing');
  // A capture on the Floor: HX Edit's own drop handling runs the drag, and the panel's drop on release is skipped.
  action({action:'nativeDrag',id:identity});
  assert.deepEqual(started,[identity]);
  taken.add(identity);sent.length=0;let clicks=0;const click=namView.clickChain;namView.clickChain=(x,y)=>{clicks++;return click(x,y);};
  action({action:'drop',id:identity,x:10,y:5,mode:'use'});
  assert.equal(clicks,0,'no click: HX Edit already dropped it');assert.equal(sent.at(-1).busy,false);
  action({action:'drop',id:identity,x:10,y:5,mode:'use'});
  assert.equal(clicks,1,'without a native drag the panel drops as before');
  namView.clickChain=click;run();
}

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
  chainBoxes:()=>[{x:300,y:100,w:900,h:120}],workflowBusy:false,discoveryLocked:false,exportPending:false,hasActiveModel:()=>true,discoveryStatus:'inactive',discoveryChecked:0,discoveryTotal:0,discoveryCountedAt:-1,slotLedger:null});
frame.sidebarHost={isNull:()=>false};
vm.runInContext(source.slice(begin,end),frame,{timeout:1000});
frame.sidebarPresentation();assert.deepEqual(changes,[['list',0],['toolbar',0],['opening',0],['error',1]]);
changes.length=0;frame.sidebarPresentation();assert.equal(changes.length,0,'Unchanged fallback does not relayout or redraw');
frame.sidebarChildReady=true;frame.sidebarChildFailed=false;frame.sidebarPresentation();assert.deepEqual(changes,[['error',0]]);
// Candidate58-60 on the Floor: the native visibility call returns 1, the bridge compared the
// flag with true, and Load/Use on Floor stayed unavailable with a NAM block selected.
frame.hasActiveModel=()=>1;assert.strictEqual(frame.sidebarPresentation().targetReady,true);
frame.hasActiveModel=()=>0;assert.strictEqual(frame.sidebarPresentation().targetReady,false);
assert.deepEqual(frame.sidebarPresentation().chain,[{x:300,y:100,w:900,h:120}],'the chain area reaches the NAM tab');
console.log('sidebar bridge passed: deferred native actions, deduplication, queue rejection and visible failure fallback');
