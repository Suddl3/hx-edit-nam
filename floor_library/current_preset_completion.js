// Read-only, bounded SF notification payload decoding. No native functions or
// device requests. Layout agrees with the existing local SF variant reader.
function readClearNotificationString(value) {
  if(!value || value.isNull())throw Error('Missing notification string');
  const rep=value.add(4).readPointer();
  if(rep.isNull())throw Error('Missing notification string storage');
  const length=rep.add(4).readU32();
  if(length<1 || length>160)throw Error('Invalid notification string length');
  const data=rep.add(16).readPointer();
  if(data.isNull())throw Error('Missing notification string data');
  return data.readUtf8String(length);
}

function readPresetContentsResult(value) {
  if(!value || value.isNull() || value.readU32()!==13)throw Error('Missing completion dictionary');
  const rep=value.add(8).readPointer();
  if(rep.isNull())throw Error('Missing completion dictionary storage');
  const pending=[rep.add(4).readPointer()],seen=new Set();
  let found=false,result;
  while(pending.length) {
    const node=pending.pop();if(node.isNull())continue;
    const id=node.toString();
    if(seen.has(id) || seen.size>=64)throw Error('Invalid completion dictionary tree');
    seen.add(id);
    const key=node.add(8);
    if(key.readU32()!==10)throw Error('Invalid completion dictionary key');
    const name=readClearNotificationString(key.add(8).readPointer());
    if(name==='result') {
      if(found)throw Error('Duplicate completion result');
      const item=node.add(24),type=item.readU32();
      if(type!==4 && type!==5)throw Error('Invalid completion result type');
      result=type===4?item.add(8).readS32():item.add(8).readU32();found=true;
    }
    pending.push(node.add(40).readPointer(),node.add(44).readPointer());
  }
  if(!found)throw Error('Missing completion result');
  return result;
}
