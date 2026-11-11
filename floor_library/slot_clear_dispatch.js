'use strict';

// The native adapter supplies a fresh synchronous inventory on the UI thread.
// Never retain an allow decision across the native confirmation dialog.
function createSlotClearDispatch(api) {
  let checking = false;
  function report(decision, notify) {
    if (notify) { try { api.blocked(decision); } catch (_) {} }
  }
  return function clear(manager, selection, notify = true) {
    let decision = {allowed:false, reason:'references-unavailable', slots:[]};
    let submitting = false;
    if (checking) return decision; // A notification/dialog must not re-enter deletion.
    checking = true;
    try {
      const slots = api.selection(selection);
      const before = api.context(manager);
      const inventory = before ? api.inventory(before) : null;
      const after = before ? api.context(manager) : null;
      if (before && after && before.presetId === after.presetId &&
          before.revision === after.revision) {
        decision = evaluateSlotClear({...after, slots}, inventory);
      } else if (before) {
        decision = {allowed:false, reason:'preset-changed', slots:[]};
      }
      if (decision.allowed) {
        // Synchronous native submission; no await, message pump or confirmation
        // may run between the final context check and this call.
        submitting = true;
        api.submit(manager, selection);
        try { if(api.submitted)api.submitted(decision); } catch (_) {}
      } else report(decision, notify);
      return decision;
    } catch (error) {
      // Reading an unavailable model cannot authorize a destructive operation.
      decision = {allowed:false, reason:submitting?'submission-unconfirmed':'references-unavailable', slots:[]};
      report(decision, notify);
      return decision;
    } finally { checking = false; }
  };
}
