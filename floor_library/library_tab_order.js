// Move the existing buttons visually; preserve native indexes and controllers.
function createLibraryTabOrder(api) {
  let busy=false,faulted=false,lastReason='';
  function status(reason) {
    if(reason!==lastReason){lastReason=reason;if(api.report)api.report(reason);}
    return reason==='verified';
  }
  function refresh() {
    if(!api.ready() || busy || faulted)return false;
    busy=true;
    try {
      const radio=api.radio();
      if(!radio)return status('radio-not-ready');
      const buttons=api.buttons(radio);
      if(!buttons || buttons.length!==4)return status('buttons-not-ready');
      const boxes=buttons.map(button=>api.box(button));
      const radioBox=api.box(radio);
      if(!radioBox || boxes.some(b=>!b))return status('geometry-not-ready');
      const width=radioBox.w;
      if(!Number.isInteger(width)||width<280||width>2000)return status('radio-width-rejected');
      // Native tabs can differ by the two-pixel bottom inset (observed 23/25).
      // Validate against the containing bar, not the first button's height.
      const height=radioBox.h;
      if(!Number.isInteger(height)||height<3)return status('radio-height-rejected');
      const buttonWidth=Math.floor((width-6)/4),step=buttonWidth+2;
      if(boxes.some(b=>![b.x,b.y,b.w,b.h].every(Number.isInteger)||b.w<=0||b.h<=0||
          b.w!==buttonWidth||b.y!==0||b.h<height-2||b.h>height))return status('button-geometry-rejected');
      const xs=boxes.map(b=>b.x);
      const near=(v,p)=>v===step*p/25;
      if(!near(xs[0],0)||!near(xs[1],25))return status('leading-position-rejected');
      const original=near(xs[2],50)&&near(xs[3],75);
      const reordered=near(xs[2],75)&&near(xs[3],50);
      if(!original&&!reordered)return status('trailing-position-rejected');
      const indices=buttons.map(button=>api.visualIndex(button));
      const expected=original?[0,1,2,3]:[0,1,3,2];
      if(indices.some((n,i)=>n!==expected[i]))return status('visual-index-rejected');
      if(buttons.some(button=>!api.unanchoredHorizontally(button)))return status('button-anchor-rejected');
      if(reordered)return status('verified');
      // HXCustomTab ignores ordinary X coordinates. Its own layout uses a
      // separate visual index, independent of radio-vector/selection indexes.
      api.setVisualIndex(buttons[2],3);
      api.setVisualIndex(buttons[3],2);
      api.redraw(radio);
      const after=buttons.map(button=>api.box(button));
      return status(after.every(Boolean)&&near(after[2].x,75)&&near(after[3].x,50)?'verified':'layout-pending');
    } catch(_) {faulted=true;return status('native-layout-fault');}
    finally {busy=false;}
  }
  return {refresh,reset(){faulted=false;lastReason='';}};
}

function createLibraryTabNativeApi(method,address,getLibrary,ready) {
  const find=method(0x3b7ae0,'pointer',['uint','int']);
  const button=method(0x389f30,'pointer',['uint']);
  const parent=method(0x3b8790,'pointer',[]);
  const anchor=method(0x3b7f30,'int',['int']);
  const configureTab=method(0x1f77e0,'void',['int','int']);
  const layoutTab=method(0x1f7460,'void',[]);
  const redraw=method(0x3baa40,'void',[]);
  function geometry(widget) {
    // Use the same bounded proxy resolution as the tested sidebar adapter.
    // Reads are bounded; custom-tab identity is separately checked before writes.
    const seen=new Set();
    for(let depth=0;depth<8;depth++){
      if(widget.isNull()||seen.has(String(widget)))throw Error('Invalid tab proxy chain');
      seen.add(String(widget));
      const g=widget.add(0x54).readPointer();
      if(g.isNull())return null; // Construction/layout may not be complete yet.
      const proxy=g.add(0xb4).readPointer();
      if(proxy.isNull())return g;
      widget=proxy;
    }
    throw Error('Tab proxy depth exceeded');
  }
  return {ready,radio() {
    const root=getLibrary();if(root.isNull())return null;
    const radio=find(root,0x6c627462,1);
    if(radio.isNull()||!radio.readPointer().equals(address(0x915358)))return null;
    const list=radio.add(0x7c).readPointer();
    if(list.isNull()||list.add(8).readU32()!==4)return null;
    return radio;
  },buttons(radio) {
    const result=Array.from({length:4},(_,i)=>button(radio,i));
    if(result.some((b,i)=>b.isNull()||!parent(b).equals(radio)||
        !b.readPointer().equals(address(0x8c35a0))||b.add(0x212).readU8()!==1||
        b.add(0x54).readPointer().isNull()||
        !b.add(0x54).readPointer().add(0xb4).readPointer().isNull()||
        b.add(0x54).readPointer().readU32()!==(0xffff0000+i)) ||
        new Set(result.map(String)).size!==4)return null;
    return result;
  },box(widget) {
    const g=geometry(widget);
    if(!g)return null;
    const x=g.add(4).readS32(),y=g.add(8).readS32();
    return {x,y,w:g.add(12).readS32()-x,h:g.add(16).readS32()-y};
  },unanchoredHorizontally:b=>anchor(b,1)<0&&anchor(b,3)<0,
    visualIndex:b=>b.add(0x218).readS32(),
    setVisualIndex:(b,index)=>{configureTab(b,1,index);layoutTab(b);},
    redraw,
    report:reason=>send({type:'toneTrace',phase:'library-tab-order',reason})};
}
