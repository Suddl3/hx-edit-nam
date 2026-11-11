'use strict';

// Only our fixed codes/structural metadata may reach a receipt. Never serialize
// a native exception message (it may include addresses, paths or user content).
class NativeExportDecodeError extends Error {
  constructor(code, actualTag) {
    super(code);this.code=code;this.steps=[];
    if(Number.isInteger(actualTag) && actualTag>=0 && actualTag<=0xffffffff)this.actualTag=actualTag;
  }
}
function nativeExportRead(step, fn) {
  try{return fn();}catch(error){
    const safe=error instanceof NativeExportDecodeError ? error : new NativeExportDecodeError('memory-read-failed');
    safe.steps.unshift(step);safe.steps=safe.steps.slice(0,8);throw safe;
  }
}

// Diagnostic observer only. Never calls an export/import, selects a row, changes
// a result, or schedules USB work. Install only in an explicitly opted-in launch.
function installNativeExportObserver(config, env) {
  const api = env || createNativeExportObserverApi();
  config={...config};
  if (!config.request || (!config.repeat && (!Number.isInteger(config.slot) || config.slot < 1 || config.slot > 127 ||
      typeof config.path !== 'string' || !config.path))) throw Error('Invalid export observation');
  const sessionRequest=config.request;
  let active = null, revision = 0, stopped = false, sequence = 0, timer = null;
  let closed=false,proof=null,monitorFailed=false;
  let lastDevice=null, automaticDispatch=false, notificationReports=0;
  function invalidate(){
    revision++;proof=null;
    if(config.trackSlots)api.invalidate();
  }
  const totalLimit=15000, unlockGrace=1500;
  const hooks = [];
  function clearTimer(){if(timer!==null){api.clearTimer(timer);timer=null;}}
  function evidence(){
    if(!active)return {};
    return {initialRevision:active.revision,currentRevision:revision,initialDevice:active.device,
      workerStarted:active.worker,workerReturned:active.returned,
      notifications:active.notifications,settings:active.settings,
      ...(active.returned ? {currentDevice:active.finishedDevice,nativeResult:active.result.result,
        nativeDetails:active.result.details,timing:timing()} : {})};
  }
  function timing(){return {workerMs:active.finishedAt-active.started,
    unlockAfterWorkerMs:active.unlockedAt===null?null:active.unlockedAt-active.finishedAt};}
  function emit(stage, fields) { api.emit({type:'irExportObservation', request:config.request, stage, ...fields}); }
  function stop(reason, fields = {}) {
    if (stopped) return;
    const diagnostic=evidence();clearTimer();stopped = true;active = null;
    emit('discarded', {reason,...diagnostic,...fields});
  }
  function armDeadline(){
    clearTimer();const observed=active.sequence;
    timer=api.setTimer(()=>{
      if(stopped || !active || active.sequence!==observed)return;
      timer=null;
      attempt('deadline',()=>{
        if(api.now()<active.deadline){armDeadline();return;}
        stop(active.returned?'export-ui-lock-timeout':'timeout');
      });
    },Math.max(1,active.deadline-api.now()));
  }
  function finishIfReady(){
    if(!active || !active.returned)return;
    if(api.now()>=active.deadline){stop('export-ui-lock-timeout');return;}
    if(revision!==active.revision){stop('inventory-or-device-changed');return;}
    if(active.lockPhase!==2)return;
    // Results and device identity were copied at worker return. Never retain or
    // dereference its result dictionary/manager pointer in a delayed callback.
    clearTimer();stopped=true;
    if(config.trackSlots)proof={request:config.request,sequence:active.sequence,
      slot:config.slot,device:active.device,revision,epoch:active.slotEpoch};
    emit('completed',{sequence:active.sequence,slot:config.slot,path:config.path,
      device:active.device,revision,result:active.result.result,details:active.result.details,
      notifications:active.notifications,settings:active.settings,timing:timing()});
    active=null;
  }
  function attempt(phase,fn) {
    try { fn(); } catch (error) {
      if(config.trackSlots){monitorFailed=true;invalidate();}
      const fault={phase,code:'unexpected-observer-error',steps:[]};
      if(error instanceof NativeExportDecodeError){
        fault.code=error.code;fault.steps=error.steps;
        if(error.actualTag!==undefined)fault.actualTag=error.actualTag;
      }
      stop('native-observation-fault', {fault,workerStarted:!!(active && active.worker),
        notifications:active?active.notifications:[],settings:active?active.settings:[]});
    }
  }
  function same(a,b) { return a === b; }
  function matches(batch) {
    return batch.indices.length === 1 && batch.paths.length === 1 &&
      batch.indices[0] === config.slot - 1 && batch.paths[0] === config.path;
  }
  function beginSubmission(batch,manager) {
        if(config.repeat){
          if(active){stop('overlapping-export');return;}
          if(batch.indices.length!==1 || batch.paths.length!==1 ||
              !Number.isInteger(batch.indices[0]) || batch.indices[0]<0 || batch.indices[0]>126 ||
              typeof batch.paths[0]!=='string' || !/\.wav$/i.test(batch.paths[0]))return;
          config.slot=batch.indices[0]+1;config.path=batch.paths[0];
          config.request=sessionRequest+':'+(sequence+1);
          stopped=false;proof=null;
        }
        if (!matches(batch)) { if (active) stop('overlapping-export'); return; }
        if (active) { stop('overlapping-export'); return; }
        if(config.trackSlots && api.forget)api.forget(config.slot);
        const device=api.device(manager);
        if(lastDevice!==null && lastDevice!==device)invalidate();
        lastDevice=device;
        active = {sequence:++sequence, manager:api.identity(manager),
          device, revision, started:api.now(), worker:false,
          notifications:[], settings:[], lockPhase:0, returned:false,unlockedAt:null};
        if(config.trackSlots)active.slotEpoch=api.slotEpoch();
        active.deadline=active.started+totalLimit;
        emit('submitted', {sequence:active.sequence, slot:config.slot, path:config.path,
          device:active.device, revision});
        armDeadline();
        return true;
  }
  hooks.push(api.attach('submit', {
    onEnter(args) {
      if (closed || monitorFailed || (stopped && !config.repeat)) return;
      attempt('submit',() => {
        const batch=api.submission(args);
        // If nested interception is delivered on a different Frida/platform
        // version, the explicitly armed call must not become a second export.
        if(automaticDispatch && active && !active.worker && matches(batch) &&
            active.manager===api.identity(this.context.ecx))return;
        beginSubmission(batch,this.context.ecx);
      });
    }
  }));
  hooks.push(api.attach('worker', {
    onEnter(args) {
      this.observedSequence = null;
      if (stopped || !active) return;
      attempt('worker-enter',() => {
        const batch = api.job(args[1]);
        if (!matches(batch) || !same(active.manager,api.identity(this.context.ecx))) {
          stop('worker-mismatch'); return;
        }
        if (active.worker) { stop('overlapping-worker'); return; }
        active.worker = true;
        this.observedSequence = active.sequence;
        this.observedManager = this.context.ecx;
        this.observedResult = args[2];
      });
    },
    onLeave() {
      if (stopped || !active || this.observedSequence !== active.sequence) return;
      attempt('worker-leave',() => {
        if (api.now()>=active.deadline) { stop('timeout'); return; }
        if(active.returned){stop('duplicate-worker-return');return;}
        const result = api.result(this.observedResult);
        const currentDevice=api.device(this.observedManager);
        active.result={result:result.result,details:result.details.slice()};
        active.finishedDevice=currentDevice;active.finishedAt=api.now();active.returned=true;
        if (revision !== active.revision || currentDevice !== active.device) {
          stop('inventory-or-device-changed', {initialRevision:active.revision,currentRevision:revision,
            initialDevice:active.device,currentDevice,nativeResult:result.result,
            nativeDetails:result.details,notifications:active.notifications,settings:active.settings}); return;
        }
        if (api.cancelled(this.observedManager) || result.result !== 0 ||
            result.details.length !== 1 || result.details[0] !== 0) {
          stop('native-export-failed'); return;
        }
        active.deadline=Math.min(active.deadline,active.finishedAt+unlockGrace);
        if(active.lockPhase!==2){
          emit('awaiting-unlock',{sequence:active.sequence,slot:config.slot,path:config.path,
            device:active.device,revision,result:result.result,details:active.result.details});
          armDeadline();
        }
        finishIfReady();
      });
    }
  }));
  // This entry dispatches IR changes AND task progress. The pinned handler's
  // EdLib_TaskProgress branch only updates its progress UI; do not mistake the
  // export's own progress for a slot mutation. The native export worker also
  // sets local UILockout true then false. UI delivery may follow worker return.
  // Require the pair within the bounded transaction, retaining mutation guards.
  hooks.push(api.attach('inventory', {onEnter(args) {
    if(closed || (stopped && !config.trackSlots) || monitorFailed)return;
    attempt('notification',() => {
      const name=api.notification(args);
      if(active && api.now()>=active.deadline){stop(active.returned?'export-ui-lock-timeout':'timeout');return;}
      // Retain the triggering name even when decoding its payload fails.
      if(active && active.notifications.length<16)active.notifications.push(
        typeof name==='string' && /^[A-Za-z0-9_.:-]{1,80}$/.test(name) ? name : '(unclassified)');
      // Candidate30b observed this ordinary Model-control notification. The
      // pinned IR handler has no branch for it and returns without IR mutation.
      // Do not exempt other EditBuffer events or arbitrary unknown messages.
      let benign=name==='EdLib_TaskProgress' || name==='EditBuffer_ParamChanged';
      if(name==='Device_SettingChanged' && (active || api.busy)) {
        const setting=api.setting(args);
        if(api.busy && setting.name==='UILockout')api.busy(setting.value);
        if(active && active.settings.length<16)active.settings.push(setting);
        if(active && active.worker && setting.name==='UILockout' && setting.onDevice===false &&
            setting.isUndo===false && setting.resetAll===false &&
            ((active.lockPhase===0 && setting.value===true) ||
             (active.lockPhase===1 && setting.value===false))) {
          active.lockPhase++;benign=true;
          if(active.lockPhase===2)active.unlockedAt=api.now();
        }
      }
      if(!benign){
        if(notificationReports++<32)emit('invalidated',{reason:typeof name==='string' && /^[A-Za-z0-9_.:-]{1,80}$/.test(name)?name:'unclassified-notification'});
        invalidate();
      }
      finishIfReady();
    });
  }}));
  emit('ready', {slot:config.slot,path:config.path});
  return {
    revision:()=>revision,
    available:()=>!closed && !monitorFailed,
    automaticSubmit(request,manager,args,submit){
      if(!config.automatic || !config.repeat || closed || monitorFailed || active ||
          automaticDispatch || typeof submit!=='function')return false;
      let accepted=false;
      attempt('automatic-submit',()=>{
        const batch=api.submission(args);
        if(!request || request.revision!==revision || request.device!==0x210001 ||
            api.device(manager)!==request.device || batch.indices.length!==1 ||
            batch.paths.length!==1 || batch.indices[0]!==request.slot-1 ||
            batch.paths[0]!==request.path || !Number.isInteger(request.slot) ||
            request.slot<1 || request.slot>127)return;
        if(beginSubmission(batch,manager)!==true)return;
        if(!active || active.revision!==request.revision){stop('inventory-or-device-changed');return;}
        automaticDispatch=true;
        try{submit();accepted=!monitorFailed;}
        finally{automaticDispatch=false;}
      });
      return accepted;
    },
    connectionChanged(){
      if(closed)return;
      if(active)stop('connection-changed');
      proof=null;
      if(config.trackSlots)api.invalidate(false); // A disconnect must also discard presentation history.
    },
    publish(value){
      if(!config.trackSlots || closed || monitorFailed || !proof || !value ||
          revision!==proof.revision || api.slotEpoch()!==proof.epoch ||
          ['request','sequence','slot','device','revision'].some(key=>value[key]!==proof[key]) ||
          typeof value.contentId!=='string' || !/^[0-9a-f]{64}$/.test(value.contentId))return false;
      const accepted=api.publish({...proof,contentId:value.contentId})===true;
      proof=null; // Never accept a duplicate/stale receipt.
      return accepted;
    },
    close() { clearTimer();stopped=true;closed=true;active=null;invalidate();for(const hook of hooks)hook.detach(); }
  };
}

function createNativeExportObserverApi() {
  if (Process.arch !== 'ia32') throw Error('Pinned x86 editor required');
  const base=Process.mainModule.base;
  const offsets={submit:0x3f5f40,worker:0x3e2b60,inventory:0x20c3f0};
  const keys={indices:'indices',paths:'filePaths',result:'result',details:'detailedResults',
    setting:'setting',value:'value',onDevice:'onDevice',isUndo:'isUndo',resetAll:'resetAll'};
  function requireTag(value,tags) {
    if(value.isNull())throw new NativeExportDecodeError('null-variant');
    const tag=value.readU32();
    if(!tags.includes(tag))throw new NativeExportDecodeError('unexpected-variant-tag',tag);
  }
  function field(value,key,optional=false) {
    return nativeExportRead('field.'+key,()=>{
    requireTag(value,[13]);
    // Do not call 5a6ea0: its missing-key path INSERTS an empty variant.
    // Pinned map layout: variant+8 -> rep, rep+4 -> root; node key+8,
    // value+24, children+40/+44. Walk bounded nodes without calling native code.
    const rep=value.add(8).readPointer();
    if(rep.isNull())throw new NativeExportDecodeError('dictionary-storage-missing');
    const pending=[rep.add(4).readPointer()],seen=new Set();let found=null;
    while(pending.length) {
      const node=pending.pop();if(node.isNull())continue;
      const identity=node.toString();
      if(seen.has(identity))throw new NativeExportDecodeError('dictionary-cycle');
      if(seen.size>=128)throw new NativeExportDecodeError('dictionary-too-large');
      seen.add(identity);
      if(nativeExportRead('node-key',()=>string(node.add(8)))===keys[key]) {
        if(found!==null)throw new NativeExportDecodeError('duplicate-dictionary-key');
        found=node.add(24);
      }
      pending.push(node.add(40).readPointer(),node.add(44).readPointer());
    }
    if(found!==null)return found;
    if(optional)return null;
    throw new NativeExportDecodeError('missing-dictionary-field');
    });
  }
  function array(value) {
    requireTag(value,[12]);
    const rep=value.add(8).readPointer();
    if(rep.isNull())throw new NativeExportDecodeError('array-storage-missing');
    const begin=rep.add(16).readU32(),end=rep.add(20).readU32();
    if(end<begin || end-begin>128 || begin>4096)throw new NativeExportDecodeError('invalid-array-bounds');
    const data=rep.add(8).readPointer();
    if(data.isNull() && end>begin)throw new NativeExportDecodeError('array-data-missing');
    return Array.from({length:end-begin},(_,i)=>data.add((begin+i)*16));
  }
  function integer(value) {
    requireTag(value,[4,5]);
    return value.add(8).readS32();
  }
  function boolean(value, optional=false) {
    // Only resetAll may be absent or an explicit empty (tag 9) variant.
    // Candidate20 observed tag 9 here; pinned native bool conversion returns
    // false for it. Do not extend this exemption to required fields.
    if(optional && value===null)return false;
    if(optional && !value.isNull() && value.readU32()===9)return false;
    requireTag(value,[11]);
    const n=value.add(8).readU8();
    if(n!==0 && n!==1)throw new NativeExportDecodeError('invalid-boolean');
    return n===1;
  }
  function stringObject(value) {
    const rep=value.add(4).readPointer();
    if(rep.isNull())return '';
    const length=rep.add(4).readU32();
    if(length>32767)throw new NativeExportDecodeError('string-too-long');
    return rep.add(16).readPointer().readUtf8String(length);
  }
  function string(value) {
    requireTag(value,[10]);
    const text=value.add(8).readPointer();
    if(text.isNull())throw new NativeExportDecodeError('string-missing');
    return stringObject(text);
  }
  function vector(value,stride,read) {
    const begin=value.add(16).readU32(),end=value.add(20).readU32();
    if(end<begin || end-begin>128 || begin>4096)throw new NativeExportDecodeError('invalid-vector-bounds');
    const data=value.add(8).readPointer();
    if(data.isNull() && end>begin)throw new NativeExportDecodeError('vector-data-missing');
    return Array.from({length:end-begin},(_,i)=>read(data.add((begin+i)*stride)));
  }
  return {
    attach:(name,handlers)=>Interceptor.attach(base.add(offsets[name]),handlers),
    emit:event=>send(event), now:()=>Date.now(),identity:p=>p.toString(),
    setTimer:(fn,ms)=>setTimeout(fn,ms),clearTimer:id=>clearTimeout(id),
    device:p=>p.add(0x44).readU32(),cancelled:p=>p.add(0x40).readU8()!==0,
    submission:args=>({indices:nativeExportRead('submission.indices',()=>vector(args[0],4,p=>p.readU32())),
      paths:nativeExportRead('submission.paths',()=>vector(args[1],8,stringObject))}),
    notification:args=>nativeExportRead('notification.name',()=>stringObject(args[0])),
    setting:args=>{
      const name=nativeExportRead('setting.name',()=>string(field(args[1],'setting')));
      // Do not log arbitrary setting values (they can contain private data).
      const safeName=/^[A-Za-z0-9_.:-]{1,80}$/.test(name)?name:'(unclassified)';
      if(name!=='UILockout')return {name:safeName};
      return {name,value:nativeExportRead('setting.value',()=>boolean(field(args[1],'value'))),
        onDevice:nativeExportRead('setting.onDevice',()=>boolean(field(args[1],'onDevice'))),
        isUndo:nativeExportRead('setting.isUndo',()=>boolean(field(args[1],'isUndo'))),
        resetAll:nativeExportRead('setting.resetAll',()=>boolean(field(args[1],'resetAll',true),true))};
    },
    job:value=>({indices:nativeExportRead('job.indices',()=>array(field(value,'indices')).map(integer)),
      paths:nativeExportRead('job.paths',()=>array(field(value,'paths')).map(string))}),
    result:value=>({result:nativeExportRead('result.overall',()=>integer(field(value,'result'))),
      details:nativeExportRead('result.details',()=>array(field(value,'details')).map(item=>integer(field(item,'result'))))})
  };
}
