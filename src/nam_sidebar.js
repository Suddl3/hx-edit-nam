'use strict';

// Host/model bridge only. Native widget work is queued onto HX Edit's UI thread.
function installNamSidebar(namView) {
  let last = '';
  namView.setSidebarSink((state, window) => {
    if (!state) return;
    const payload = {type:'namSidebarLayout', window:window.toUInt32(), state};
    const signature = JSON.stringify(payload);
    if (signature !== last) { last = signature; send(payload); }
  });
  function ready() {
    recv('namSidebarReady', message => {
      namView.queueSidebarAction(() => { namView.setSidebarChildReady(message.payload?.ready === true); last=''; });
      ready();
    });
  }
  function actions() {
    recv('namSidebarAction', message => {
      const value = message.payload || {};
      const queued=namView.queueSidebarAction(window => {
        if (value.action === 'resize') {
          const ok=namView.resizeSidebarFromChild(value.width);
          send({type:'namSidebarResizeResult',width:value.width,error:ok?'':'Sidebar layout is not available.'});
        } else if (value.action === 'expand') {
          namView.openExpandedLibrary(window);
        } else if (value.action === 'level') {
          // The Level button by the DSP meter: the Level page, docked where the library opens.
          const opened=toneView.openBrowser(window,{level:true,dockPanel:navigationBanner.dockPanel()});
          if (opened === false) send({type:'namSidebarResult',error:'Select a block in HX Edit, then press Level again.',busy:false});
        } else if (value.action === 'look') {
          // HX Unbound menu, Floor look: what Focus View shows on the Floor, in its own window.
          const opened=toneView.openBrowser(window,{look:true,dockPanel:navigationBanner.dockPanel()});
          if (opened === false) send({type:'namSidebarResult',error:'Select a block in HX Edit, then choose Floor look again.',busy:false});
        } else if (value.action === 'nativeDrag' && /^[a-f0-9]{64}$/.test(value.id || '')) {
          // A capture on the Floor: HX Edit's own drop handling runs the drag (tone_view startChainDrag).
          if (!toneView.startChainDrag?.(value.id)) send({type:'toneTrace',reason:'chain-drag',stage:'not-started'});
        } else if (value.action === 'drop' && /^[a-f0-9]{64}$/.test(value.id || '') && ['load','use'].includes(value.mode)
            && Number.isInteger(value.x) && Number.isInteger(value.y)) {
          if (toneView.chainDragTook?.(value.id)) { send({type:'namSidebarResult',text:'',busy:false}); return; }
          // A capture dropped from the NAM tab: click the block under it, then Use / Load there.
          if (!namView.clickChain(value.x, value.y))
            send({type:'namSidebarResult',error:'Drop the capture on a block in the signal chain.',busy:false});
          else finishDrop(value);
        } else if (['load','use'].includes(value.action) && /^[a-f0-9]{64}$/.test(value.id || '')) {
          const opened=toneView.openBrowser(window,{headless:true,libraryAction:value.action+':'+value.id,
            dockPanel:navigationBanner.dockPanel()});
          if (opened === false) send({type:'namSidebarResult',error:'Select a NAM block and try again.',busy:false});
        }
      });
      if (!queued) send({type:value.action==='resize'?'namSidebarResizeResult':'namSidebarResult',
        error:'The editor is busy. Try this action again after the current change finishes.',busy:false});
      actions();
    });
  }
  // After the drop's click: wait until HX Edit has handled it and the block's controls have
  // settled. An empty cell or another kind of block becomes a NAM block first; then the capture
  // goes on it as a double-click on the row does (Use, or Load when it is not on the Floor yet).
  const DROP_WAIT = 1500, DROP_SETTLE = 250, BLOCK_WAIT = 3000;
  function finishDrop(value) {
    let adding = 0;
    const retry = () => setTimeout(() => {
      if (!namView.queueSidebarAction(window => step(window))) {
        namView.endDrop();
        send({type:'namSidebarResult',error:'The editor is busy. Drop the capture again.',busy:false});
      }
    }, 30);
    function step(window) {
      const drop = namView.dropState();
      if (drop.state === 'none') return;
      const now = Date.now();
      if (drop.state === 'pending') {
        if (now - drop.posted > DROP_WAIT) {
          namView.endDrop();
          send({type:'namSidebarResult',error:'HX Edit did not take the drop. Select the block, then double-click the capture.',busy:false});
        } else retry();
        return;
      }
      if (now - drop.clicked < DROP_SETTLE) { retry(); return; }
      if (adding) {
        // The NAM block row was chosen: wait for HX Edit to build the block and its Model control.
        if (!toneView.selectedIsNam()) {
          if (now - adding > BLOCK_WAIT) {
            namView.endDrop();
            send({type:'namSidebarResult',error:'HX Edit did not add the NAM block. Check the spot, then drop again.',busy:false});
          } else retry();
          return;
        }
      } else {
        const made = toneView.makeNamBlock(value.gear || '');
        if (made === 'adding') { adding = now; retry(); return; }
        if (made !== 'nam') { namView.endDrop(); send({type:'namSidebarResult',error:made,busy:false}); return; }
      }
      namView.endDrop();
      const opened=toneView.openBrowser(window,{headless:true,libraryAction:value.mode+':'+value.id,
        dockPanel:navigationBanner.dockPanel()});
      if (opened === false) send({type:'namSidebarResult',error:'Select a NAM block and try again.',busy:false});
    }
    retry();
  }
  function irDots() {
    // HX Unbound menu: "Mark NAM slots in the IR list" turned on or off.
    recv('irDots', message => {
      const on = message.payload?.on === true;
      namView.queueSidebarAction(() => namView.setIrDots(on));
      irDots();
    });
  }
  function chainArt() {
    // HX Unbound menu: "3D pictures in the signal chain" turned on or off (redrawn on HX Edit's UI thread).
    recv('chainArt', message => {
      const on = message.payload?.on === true;
      namView.queueSidebarAction(() => toneView.setChainArt?.(on));
      chainArt();
    });
  }
  ready();actions();irDots();chainArt();
}
