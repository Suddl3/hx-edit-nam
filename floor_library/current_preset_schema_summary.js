// Bounded structural diagnostics only. Never emit preset/parameter values.
function describeCurrentPresetGlobalNodes(tree) {
  const record=v=>v!==null && typeof v==='object' && !Array.isArray(v);
  const kind=v=>v===null?'null':Array.isArray(v)?'array':typeof v;
  const identifier=s=>/^@?[A-Za-z0-9_][A-Za-z0-9_.-]{0,63}$/.test(s)?s:'<nonstandard>';
  if(!record(tree) || !record(tree.globalNodes))return {unknownGlobals:[],unknownGlobalCount:0};
  const known=new Set(CURRENT_PRESET_GLOBAL_METADATA);
  const names=Object.keys(tree.globalNodes).filter(n=>!known.has(n)&&!/^snapshot[0-7]$/.test(n)).sort();
  const unknownGlobals=names.slice(0,16).map(name=>{
    const node=tree.globalNodes[name],fields=[];let truncated=false;
    function visit(value,path,depth) {
      if(fields.length>=64){truncated=true;return;}
      const type=kind(value);
      if(!record(value)||depth>=4||Object.keys(value).length===0){fields.push(path+':'+type);return;}
      const keys=Object.keys(value).sort();if(keys.length>32)truncated=true;
      for(const key of keys.slice(0,32))visit(value[key],path?path+'.'+identifier(key):identifier(key),depth+1);
    }
    if(record(node))for(const key of Object.keys(node).sort().slice(0,32))visit(node[key],identifier(key),1);
    if(record(node)&&Object.keys(node).length>32)truncated=true;
    return {name:identifier(name),type:kind(node),propertyCount:record(node)?Object.keys(node).length:0,fields,truncated};
  });
  return {unknownGlobals,unknownGlobalCount:names.length};
}
