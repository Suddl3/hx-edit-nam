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
  ready();actions();
}
