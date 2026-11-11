'use strict';

// One replacement at the entry of the stock batch queue routine. No interior
// interceptor, stack rewriting, worker hook or separate USB connection.
function installNativeSlotClearGuard(entry, vectorVtable, api) {
  const types = ['pointer', 'pointer'];
  const original = new NativeFunction(entry, 'void', types, 'thiscall');
  const guarded = createSlotClearDispatch({
    selection:vector=>readClearSlotSelection(vector,vectorVtable),
    context:manager=>api.context(manager),
    inventory:context=>api.inventory(context),
    submit:(manager,vector)=>original(manager,vector),
    submitted:decision=>{if(api.submitted)api.submitted(decision);},
    blocked:decision=>api.blocked(decision)
  });
  const callback = new NativeCallback(function(manager,vector) {
    guarded(manager,vector);
  }, 'void', types, 'thiscall');
  Interceptor.replace(entry,callback);
  // Our own preview cleanup calls this explicitly: Frida may suppress nested
  // replacements entered from an existing JS callback. No permission bypass.
  return {clear:guarded, callback};
}
