// Move the existing buttons visually; preserve native indexes and controllers.
function createLibraryTabOrder(api) {
  let busy=false,faulted=false;
  function refresh() {
    if(!api.ready() || busy || faulted)return false;
    busy=true;
    try {
      const radio=api.radio();
      if(!radio)return false;
      const buttons=api.buttons(radio);
      if(!buttons || buttons.length!==4)return false;
      const boxes=buttons.map(button=>api.box(button));
      const width=api.box(radio).w;
      if(!Number.isInteger(width)||width<280||width>2000)return false;
      if(boxes.some(b=>![b.x,b.y,b.w,b.h].every(Number.isInteger)||b.w<=0||b.h<=0||
          Math.abs(b.w-width/4)>2||b.y!==boxes[0].y||b.h!==boxes[0].h))return false;
      const xs=boxes.map(b=>b.x);
      const near=(v,p)=>Math.abs(v-width*p/100)<=2;
      if(!near(xs[0],0)||!near(xs[1],25))return false;
      const original=near(xs[2],50)&&near(xs[3],75);
      const reordered=near(xs[2],75)&&near(xs[3],50);
      if(!original&&!reordered)return false;
      if(buttons.some(button=>!api.unanchoredHorizontally(button)))return false;
      if(reordered)return true;
      // The existing native percentage setter includes normal relayout. Width,
      // label, generated button ID and selection index are never changed.
      api.setX(buttons[2],75);
      api.setX(buttons[3],50);
      return true;
    } catch(_) {faulted=true;return false;}
    finally {busy=false;}
  }
  return {refresh};
}

function createLibraryTabNativeApi(method,address,getLibrary,ready) {
  const find=method(0x3b7ae0,'pointer',['uint','int']);
  const button=method(0x389f30,'pointer',['uint']);
  const parent=method(0x3b8790,'pointer',[]);
  const anchor=method(0x3b7f30,'int',['int']);
  const setX=method(0x3bc920,'void',['int','int','int']);
  return {ready,radio() {
    const root=getLibrary();if(root.isNull())return null;
    const radio=find(root,0x6c627462,1);
    if(radio.isNull()||!radio.readPointer().equals(address(0x915358)))return null;
    const list=radio.add(0x7c).readPointer();
    if(list.isNull()||list.add(8).readU32()!==4)return null;
    return radio;
  },buttons(radio) {
    const result=Array.from({length:4},(_,i)=>button(radio,i));
    if(result.some(b=>b.isNull()||!parent(b).equals(radio)) ||
        new Set(result.map(String)).size!==4)return null;
    return result;
  },box(widget) {
    const g=widget.add(0x54).readPointer();
    if(g.isNull()||!g.add(0xb4).readPointer().isNull())throw Error('Unexpected tab proxy');
    const x=g.add(4).readS32(),y=g.add(8).readS32();
    return {x,y,w:g.add(12).readS32()-x,h:g.add(16).readS32()-y};
  },unanchoredHorizontally:b=>anchor(b,1)<0&&anchor(b,3)<0,
    setX:(b,x)=>setX(b,x,0,1)};
}
