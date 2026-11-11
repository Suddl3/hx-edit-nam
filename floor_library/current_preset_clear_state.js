'use strict';

// Notification state is necessary but not sufficient: the adapter also checks
// UI ownership, device/busy state, locks, complete graph and stable revisions.
function createCurrentPresetClearState() {
  let controller=null,editor=null,thread=0,revision=0,depth=0,ready=false,pendingReady=false;
  let baseline=false,lastEvent='',resetReason='not-observed',notifications=0,ownerChanges=0,contentsFailed=false,unsupportedChange=false;
  const barriers=new Map(),paired=new Set(['PresetContents','HelixSignalFlow','HelixSnapshot','HelixAssignments']);
  function reset(reason='reset') {
    controller=null;editor=null;thread=0;depth=0;ready=false;pendingReady=false;
    baseline=false;contentsFailed=false;unsupportedChange=false;barriers.clear();resetReason=reason;revision++;
  }
  return {
    begin(c,e,t,event,completion={}) {
      if (!c || !e || !Number.isInteger(t) || t<=0 || typeof event!=='string') {reset('invalid-notification');return null;}
      if(controller!==c || editor!==e || thread!==t) {
        if(controller!==null)ownerChanges++;
        reset('owner-changed');controller=c;editor=e;thread=t;
      }
      revision++;depth++;notifications++;
      lastEvent=/^(?:EditBuffer|Device|UndoBuffer)_[A-Za-z0-9_]{1,120}$/.test(event)?event:'other-notification';
      const will=/^EditBuffer_(.+)WillChange$/.exec(event);
      if(/^EditBuffer_(?:TargetDeviceChanged|DevicePresetChanged|PresetChanged)$/.test(event)) {
        ready=false;pendingReady=false;baseline=false;unsupportedChange=false;barriers.clear();resetReason='preset-replacement';
      } else if(will) {
        ready=false;pendingReady=false;
        if(will[1]==='PresetContents') {
          baseline=false;
          if(!barriers.has('PresetContents'))contentsFailed=false;
        }
        if(paired.has(will[1]))barriers.set(will[1],(barriers.get(will[1])||0)+1);
        else {baseline=false;unsupportedChange=true;resetReason='unsupported-will-change';}
      }
      return {controller:c,editor:e,thread:t,event,result:completion.result};
    },
    end(token) {
      if(!token || token.controller!==controller || token.editor!==editor || token.thread!==thread){reset('notification-owner-mismatch');return;}
      depth=Math.max(0,depth-1);revision++;
      const did=/^EditBuffer_(.+)DidChange$/.exec(token.event);
      if(did && paired.has(did[1])) {
        const count=barriers.get(did[1])||0;
        if(did[1]==='PresetContents') {
          ready=false;pendingReady=false;
          if(token.result!==0)contentsFailed=true;
          // This is the actual native load-completion handshake: a matched
          // contents Will/Did with a typed result=0, never an arbitrary event.
          // A failed/missing completion or Did without Will cannot bootstrap.
          if(count===0)baseline=false;
          if(count===1)baseline=!contentsFailed&&!unsupportedChange;
        }
        if(count>1)barriers.set(did[1],count-1);else barriers.delete(did[1]);
      }
      // Do not permit Clear from a reentrant notification stack. Wait for the
      // editor's existing UI snapshot pass after this notification returns.
      // Other paired DidChange notifications only restore an existing baseline.
      if(baseline && barriers.size===0 && depth===0 && !ready)pendingReady=true;
    },
    settle(t) {if(pendingReady && depth===0 && t===thread){pendingReady=false;ready=true;revision++;}},
    destroyed(c) {if(c===controller)reset('controller-destroyed');},
    reset,
    current(t) {return ready && depth===0 && t===thread ? {controller,editor,thread,revision}:null;},
    revision:()=>revision,
    describe(t) {
      const reason=controller===null?resetReason:t!==thread?'different-thread':depth>0?'notification-in-progress':
        barriers.size?'awaiting-paired-completion':!baseline?'awaiting-completed-preset':
        pendingReady?'awaiting-ui-settle':ready?'ready':'not-ready';
      return {reason,ready,baseline,depth,pendingReady,pendingKinds:Array.from(barriers.keys()).sort(),
        lastEvent,notifications,ownerChanges};
    }
  };
}
