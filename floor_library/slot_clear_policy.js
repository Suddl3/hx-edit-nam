'use strict';

// Pure decision policy, connected through native_clear_guard in Candidate46+.
// The native adapter must provide a complete CURRENT preset reference
// inventory (all blocks/paths/snapshot values, including bypassed blocks), not
// just the selected NAM widget or the saved preset on disk.
function evaluateSlotClear(request, inventory) {
  const validSlot=s=>Number.isInteger(s)&&s>=1&&s<=128;
  const deny=(reason,slots=[])=>({allowed:false,reason,slots});
  if(!request||!Array.isArray(request.slots)||!request.slots.length||
      request.slots.some(s=>!validSlot(s))||new Set(request.slots).size!==request.slots.length)
    return deny('invalid-selection');
  if(!inventory||inventory.complete!==true||!Array.isArray(inventory.references)||
      typeof inventory.presetId!=='string'||!inventory.presetId||
      !Number.isInteger(inventory.revision)||inventory.revision<0)
    return deny('references-unavailable');
  if(request.presetId!==inventory.presetId||request.revision!==inventory.revision)
    return deny('preset-changed');
  if(inventory.references.some(r=>!r||!validSlot(r.slot)||typeof r.blockId!=='string'||!r.blockId))
    return deny('references-unavailable');
  const wanted=new Set(request.slots);
  const used=[...new Set(inventory.references.filter(r=>wanted.has(r.slot)).map(r=>r.slot))].sort((a,b)=>a-b);
  return used.length?deny('used-by-current-preset',used):{allowed:true,reason:'unused-by-current-preset',slots:[...wanted].sort((a,b)=>a-b)};
}
