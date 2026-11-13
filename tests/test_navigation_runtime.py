"""Exercise the actual worker/native runtime functions without launching HX Edit."""
import json
from pathlib import Path
from queue import Queue
import re
import shutil
import subprocess
from threading import Event
import unittest
from unittest.mock import Mock, patch

from tone_preview import TonePreview

ROOT = Path(__file__).resolve().parents[1]
TONE = (ROOT / 'tone_view.js').read_text(encoding='utf-8')


class Browser:
    def __init__(self, actions=()):
        self.actions = Queue()
        for action in actions:
            self.actions.put(action)
        self.sent = []

    def next_action(self):
        if self.actions.empty():
            return None
        return self.actions.get_nowait()

    def send(self, kind, **value):
        self.sent.append(dict(type=kind, **value))

    def is_set(self):
        return False


class NavigationWorkerTests(unittest.TestCase):
    def make_preview(self, browser, **kwargs):
        p = TonePreview(Mock(), str(ROOT), 'unused-converter', Event(), Mock(), **kwargs)
        p.navigation = True
        p.session = 5
        p.browser = browser
        p.tile = {'block': 'NAM A2 Lite', 'modes': ['original']}
        p.navigation_state = dict(session=5, version=1, bound=False)
        return p

    def selection(self):
        return ({'id': 10, 'title': 'Demo', 'gear': 'amp'}, [{'id': 20, 'name': 'Clean'}])

    def test_browsing_unbound_downloads_no_capture_and_sends_no_native_write(self):
        b = Browser([((10, 'token'), None), 'cancel'])
        p = self.make_preview(b)
        p.command = Mock()
        with patch('tone_preview.Thread'), patch('tone_preview.get_tone', return_value=self.selection()), \
             patch('tone_preview.prepare_model') as prepare:
            p.browse(b, 5, 't3k_pub_test')
        prepare.assert_not_called()
        p.command.assert_not_called()
        self.assertTrue(any('Tone selected for browsing' in x.get('text', '') for x in b.sent))

    def test_explicit_bind_then_audition_carries_the_native_target_version(self):
        b = Browser([((10, 'token'), None), 'bind', 'auditionSelected', 'cancel'])
        p = self.make_preview(b)

        def command(session, action, **values):
            if action == 'bind':
                p.receive(dict(type='toneTarget', session=5, version=2, bound=True, label='NAM A', canBind=True))
                return {'target': True, 'version': 2}
            return {'slot': 128, 'freeSlots': [2, 3]}

        p.command = Mock(side_effect=command)
        with patch('tone_preview.Thread'), patch('tone_preview.get_tone', return_value=self.selection()), \
             patch('tone_preview.prepare_model', return_value='converted.wav'), patch('tone_preview.remember_link'):
            p.browse(b, 5, 't3k_pub_test')
        p.command.assert_any_call(5, 'bind')
        p.command.assert_any_call(5, 'audition', file='converted.wav', targetVersion=2)

    def test_selected_compatible_nam_select_tone_auditions_without_bind(self):
        b = Browser([((10, 'token'), None), 'cancel'])
        p = self.make_preview(b)
        p.receive(dict(type='toneTarget', session=5, version=7, bound=True,
                       label='DSP 1 · block0 · Friendly capture · User IR 9'))
        p.command = Mock(return_value={'slot': 128, 'freeSlots': [12]})
        with patch('tone_preview.Thread'), patch('tone_preview.get_tone', return_value=self.selection()), \
             patch('tone_preview.prepare_model', return_value='converted.wav') as prepare, \
             patch('tone_preview.remember_link'):
            p.browse(b, 5, 't3k_pub_test')
        prepare.assert_called_once()
        p.command.assert_called_once_with(5, 'audition', file='converted.wav', targetVersion=7)

    def test_download_completion_after_selection_change_cannot_retarget(self):
        b = Browser([((10, 'token'), None), 'cancel'])
        p = self.make_preview(b)
        p.navigation_state.update(bound=True)
        p.command = Mock()

        def prepare(*_args):
            p.receive(dict(type='toneTarget', session=5, version=2, bound=False, preview=False))
            return 'converted.wav'

        with patch('tone_preview.Thread'), patch('tone_preview.get_tone', return_value=self.selection()), \
             patch('tone_preview.prepare_model', side_effect=prepare), patch('tone_preview.remember_link'):
            p.browse(b, 5, 't3k_pub_test')
        p.command.assert_not_called()
        self.assertTrue(any('block changed during download' in x.get('text', '') for x in b.sent))

    def test_target_updates_keep_browser_open_and_old_session_cannot_replace_state(self):
        b = Browser()
        p = self.make_preview(b)
        p.receive(dict(type='toneTarget', session=4, version=99, bound=True))
        self.assertEqual(p.navigation_state['version'], 1)
        p.receive(dict(type='toneTarget', session=5, version=2, bound=False, preview=True, quarantined=True))
        self.assertIs(p.browser, b)
        self.assertEqual(b.next_action()['navigation']['version'], 2)
        self.assertNotIn('cancel', list(b.actions.queue))

    def test_focus_opens_all_local_library_without_a_second_session(self):
        b = Browser()
        p = self.make_preview(b)
        p.receive(dict(type='toneFocus', session=5, page='library'))
        self.assertEqual(b.sent, [{'type': 'focus'}])
        self.assertEqual(b.next_action(), 'library')
        self.assertEqual(p.session, 5)

    def test_retained_cleanup_never_claims_preview_ended(self):
        b = Browser(['end', 'cancel'])
        p = self.make_preview(b)
        p.command = Mock(return_value={'retained': True, 'ended': False, 'notice': 'Preview remains in User IR 128.'})
        with patch('tone_preview.Thread'):
            p.browse(b, 5, 't3k_pub_test')
        texts = [value.get('text', '') for value in b.sent]
        self.assertIn('Preview remains in User IR 128.', texts)
        self.assertFalse(any('Preview ended' in value for value in texts))

    def test_inventory_update_is_atomic_status_without_replacing_library_page(self):
        b = Browser(['cancel'])
        library = Mock()
        library.entries.return_value = {'abc': {'name': 'Demo', 'irSlots': [7, 14], 'slotState': 'verified'}}
        p = self.make_preview(b, library=library)
        p.inventory_changed()
        b.actions.get_nowait()  # remove cancel ahead of the inventory update
        with patch('tone_preview.Thread'):
            p.browse(b, 5, 't3k_pub_test')
        self.assertFalse(any(x['type'] == 'card' for x in b.sent))
        self.assertEqual(b.sent[-1]['inventory']['rows'], [dict(id='abc', irSlots=[7, 14], pendingSlots=[], slotState='verified')])

    def test_known_converted_payload_registration_notifies_native_metadata(self):
        callback, library = Mock(), Mock()
        library.payload_metadata.return_value = {'content': {'name': 'Demo'}}
        p = self.make_preview(Browser(), library=library, payload_callback=callback)
        p.register_payload('capture-id', 'converted.wav')
        library.register_payload.assert_called_once_with('capture-id', 'converted.wav')
        callback.assert_called_once_with({'content': {'name': 'Demo'}})

    def test_use_installed_capture_does_not_export_convert_or_upload(self):
        identity = 'a' * 64
        b, library = Browser(['use:' + identity, 'cancel']), Mock()
        library.entries.return_value = {identity: {'name': 'Demo', 'irSlots': [9], 'slotState': 'verified'}}
        p = self.make_preview(b, library=library)
        p.navigation_state.update(bound=True, preview=False)
        p.command = Mock(return_value={'usedSlot': 9})
        with patch('tone_preview.Thread'), patch('tone_preview.convert_batch') as convert:
            p.browse(b, 5, 't3k_pub_test')
        library.export.assert_not_called()
        convert.assert_not_called()
        p.command.assert_called_once_with(5, 'use', identity=identity, targetVersion=1)
        self.assertTrue(any('Using the verified capture in User IR 9' in x.get('text', '') for x in b.sent))

    def test_direct_load_marks_only_loaded_row_pending_and_never_auditions(self):
        import tempfile
        identity = 'b' * 64
        b, library = Browser(['load:' + identity, 'cancel']), Mock()
        library.entries.return_value = {identity: {'name': 'Demo', 'irSlots': [], 'slotState': 'verified'}}
        library.export.return_value = 'source.wav'
        with tempfile.TemporaryDirectory() as folder:
            p = self.make_preview(b, library=library)
            p.workspace = folder
            p.navigation_state.update(bound=True, preview=False)
            p.command = Mock(return_value={'loadedSlot': 2})
            with patch('tone_preview.Thread'), patch('tone_preview.convert_batch', return_value=['converted.wav']):
                p.browse(b, 5, 't3k_pub_test')
        p.command.assert_called_once_with(5, 'load', identity=identity, targetVersion=1, file='converted.wav')
        library.mark_pending.assert_called_once_with(identity, 2)
        self.assertTrue(any('Loaded into User IR 2' in x.get('text', '') for x in b.sent))

    def test_direct_load_is_blocked_during_preview_without_side_effects(self):
        identity = 'b' * 64
        b, library = Browser(['load:' + identity, 'cancel']), Mock()
        library.entries.return_value = {identity: {'name': 'Demo'}}
        p = self.make_preview(b, library=library)
        p.navigation_state.update(bound=True, preview=True)
        p.command = Mock()
        with patch('tone_preview.Thread'):
            p.browse(b, 5, 't3k_pub_test')
        library.export.assert_not_called()
        p.command.assert_not_called()

    def test_converted_only_capture_cannot_start_lighter_training(self):
        identity = 'b' * 64
        b, library = Browser(['light:' + identity + ':16:quick', 'cancel']), Mock()
        library.entries.return_value = {identity: {'name': 'Demo', 'canTrain': False}}
        p = self.make_preview(b, library=library)
        with patch('tone_preview.Thread'), patch('tone_preview.nam_light.LightJob') as job:
            p.browse(b, 5, 't3k_pub_test')
        library.export.assert_not_called()
        job.assert_not_called()


def function(name):
    return re.search(r'^  function ' + name + r'\(.*?^  }$', TONE, re.S | re.M).group(0)


def js(source):
    return json.loads(subprocess.run(['node', '-e', source], capture_output=True, text=True,
                                    check=True, timeout=10).stdout)


@unittest.skipUnless(shutil.which('node'), 'Node.js required')
class NavigationNativeTests(unittest.TestCase):
    def test_repeated_control_notifications_publish_only_changed_target_state(self):
        result = js('''
          let active={id:5,navigation:true,bound:true,valid:true,targetVersion:3,targetLabel:'NAM A'};
          let selected={label:'Selected A'},selectedController=null;
          const sent=[],counts=[],selectedTarget=()=>selected,atTarget=()=>true,controllerReady=()=>true;
          const send=value=>sent.push(value),invalidate=()=>{throw Error('unexpected invalidation');};
          const autoBindSelected=()=>{}; // Full binding/cleanup is exercised by tone_navigation_flow_probe.cjs.
        ''' + function('publishTarget') + function('observeController') + '''
          for(let i=0;i<5000;i++)observeController('A');
          counts.push(sent.length);
          function changed(edit,text) {
            const before=sent.length;edit();publishTarget(text);
            for(let i=0;i<1000;i++)publishTarget(text);
            counts.push(sent.length-before);
          }
          changed(()=>{active.targetVersion++;});
          changed(()=>{active.targetLabel='Renamed A';});
          changed(()=>{selected={label:'Selected B'};});
          changed(()=>{active.bound=false;});
          changed(()=>{active.pendingTransfer={slot:128};});
          // Different bookkeeping with an identical visible payload stays deduplicated.
          changed(()=>{active.pendingTransfer=null;active.transfer={slot:128};});
          changed(()=>{active.cleanupRequested=true;});
          changed(()=>{active.quarantined=true;});
          changed(()=>{active.transfer=null;});
          changed(()=>{},'Cleanup needs attention.');
          changed(()=>{active.cleanupRequested=false;active.quarantined=false;});
          changed(()=>{selected=null;});
          console.log(JSON.stringify({counts,sent}));
        ''')
        self.assertEqual(result['counts'], [1, 1, 1, 1, 1, 1, 0, 1, 1, 1, 1, 1, 1])
        self.assertEqual(result['sent'][1]['version'], 4)
        self.assertEqual(result['sent'][2]['label'], 'Renamed A')
        self.assertEqual(result['sent'][3]['selected'], 'Selected B')
        self.assertFalse(result['sent'][4]['bound'])
        self.assertTrue(result['sent'][5]['preview'])
        self.assertFalse(result['sent'][5]['canBind'])
        self.assertIn('Restoring the previous block', result['sent'][6]['text'])
        self.assertTrue(result['sent'][7]['quarantined'])
        self.assertFalse(result['sent'][8]['preview'])
        self.assertEqual(result['sent'][9]['text'], 'Cleanup needs attention.')
        self.assertTrue(result['sent'][10]['canBind'])
        self.assertFalse(result['sent'][11]['canBind'])

    def test_selection_cleanup_and_explicit_reopen_still_publish_fresh_target(self):
        result = js('''
          let contextVersion=4,targetVersion=3,nextSession=5,toneImport=null,selectedController=null;
          let matches=true,selected={label:'Selected A'};
          const window={isNull:()=>false,toUInt32:()=>77},NULL={isNull:()=>true},wake=42;
          let active={id:5,navigation:true,bound:true,valid:true,context:4,window,
            targetVersion:3,targetLabel:'NAM A',nativeTarget:{valid:true},transfer:{slot:128}};
          const sent=[],posts=[],send=value=>sent.push(value),post=(...args)=>posts.push(args);
          const profile={navigation:true},tiles=[{block:'NAM A2 Lite'}],controllerReady=()=>true;
          const selectedTarget=()=>selected,atTarget=()=>matches;
          const namView={stopPreview(){}},releaseTarget=()=>{},reply=()=>{},resume=()=>{};
          const autoBindSelected=()=>{},refreshBounds=()=>{},allowForeground=()=>{};
          const dockPanel={isNull:()=>false},panelBounds=()=>({visible:true,x:80,y:100,w:500,h:600});
          const targets=()=>sent.filter(value=>value.type==='toneTarget');
        ''' + function('publishTarget') + function('observeController') + function('invalidate') + \
            function('afterRestore') + function('openBrowser') + '''
          observeController('A');
          matches=false;selected={label:'Selected B'};observeController('B');
          for(let i=0;i<5000;i++)observeController('B');
          const afterSelection=targets().length;
          afterRestore({action:'autoEnd'},{});
          for(let i=0;i<5000;i++)observeController('B');
          const afterCleanup=targets().length;
          openBrowser(window);openBrowser(window);
          const afterReopen=targets().length;
          active=null;openBrowser(window,{dockPanel});
          for(let i=0;i<5000;i++)observeController('B');
          console.log(JSON.stringify({afterSelection,afterCleanup,afterReopen,
            targets:targets(),posts:posts.length,focus:sent.filter(value=>value.type==='toneFocus').length}));
        ''')
        self.assertEqual(result['afterSelection'], 2)
        self.assertEqual(result['posts'], 1)
        self.assertFalse(result['targets'][1]['bound'])
        self.assertTrue(result['targets'][1]['preview'])
        self.assertTrue(result['targets'][1]['quarantined'])
        self.assertEqual(result['afterCleanup'], 3)
        self.assertFalse(result['targets'][2]['preview'])
        self.assertTrue(result['targets'][2]['canBind'])
        self.assertEqual(result['afterReopen'], 5)
        self.assertEqual(result['focus'], 2)
        self.assertEqual(result['targets'][2], result['targets'][3])
        self.assertEqual(result['targets'][3], result['targets'][4])
        self.assertEqual(len(result['targets']), 6)
        self.assertEqual(result['targets'][-1]['session'], 6)

    def test_save_then_new_preview_restores_the_new_saved_capture(self):
        result = js('''
          let contextVersion=4,targetVersion=1,slot=128,undo=3,empty=false;
          const callbacks=[],calls=[];
          let active={navigation:true,bound:true,valid:true,context:4,targetVersion:1,controller:'A',block:'block0',position:'A',
            tile:{block:'NAM A2 Lite'},transfer:{slot:128},origin:{slot:9},nativeTarget:{valid:true,originalSlot:9}};
          const namView={modelSlot:()=>slot,selectProfile:s=>{slot=s.slot;undo++;return true;},
            clearPreview:()=>{empty=true;return true;},canClearPreview:()=>true,previewSlotEmpty:()=>empty,
            stopPreview:()=>{},freeSlots:()=>[2],previewName:()=>true};
          const atTarget=()=>true,undoIndex=()=>undo,poll=(_r,next)=>callbacks.push(next),publishTarget=()=>{},releaseTarget=()=>{};
          const captureTarget=()=>{active.nativeTarget={valid:true,originalSlot:slot};calls.push(['capture',slot]);};
          const cleanupTrace=()=>{},targetOnScreen=()=>false; // the old block is not the one on screen
          const forgetPreview=()=>{};
          let response;const reply=(_r,value)=>response=value;
          const targetBinding={read:()=>({slot}),restore:(binding,original)=>{
            if(binding.originalSlot!==original)throw Error('original mismatch');
            calls.push(['restore',original]);slot=original;return true;}};
        ''' + function('keepSaved') + function('afterRestore') + function('restoreOwnedPreview') + function('restoreBoundPreview') + '''
          keepSaved({action:'save',targetVersion:1},{slot:128},{slot:17});callbacks.shift()(false);
          slot=128;empty=false;active.transfer={slot:128};active.origin={slot:17};active.bound=false;
          restoreBoundPreview({action:'autoEnd'});while(callbacks.length)callbacks.shift()(false);
          console.log(JSON.stringify({calls,slot,response}));
        ''')
        self.assertEqual(result['calls'], [['capture', 17], ['restore', 17]])
        self.assertEqual(result['slot'], 17)
        self.assertTrue(result['response']['ended'])

    def test_cross_block_cleanup_restores_explicit_old_block_without_touching_new_selection(self):
        result = js('''
          function scenario(mode) {
            let contextVersion=4,targetVersion=9,empty=false;
            const blocks={A:{Model:128,Input:6,Bass:4.5},B:{Model:11,Input:8,Bass:5}};
            const binding={valid:true,blockName:'A',context:4},calls=[],pending=[];
            let active={navigation:true,valid:false,bound:false,context:4,controller:'new-B',nativeTarget:binding,
              cleanupRequested:true,transfer:{slot:128},origin:{slot:16},targetVersion:9};
            const targetBinding={read:(_b,c)=>{
              if(c.context!==4)throw Error('expired context');return{slot:blocks.A.Model};},
              restore:(_b,slot,c)=>{calls.push(['restore-old',slot]);blocks.A.Model=slot;return true;}};
            const namView={previewName:()=>mode!=='owner-changed',canClearPreview:()=>mode!=='referenced-elsewhere',
              clearPreview:()=>{calls.push(['clear',128]);empty=true;return true;},previewSlotEmpty:()=>empty,stopPreview:()=>{}};
            const poll=(_r,next)=>pending.push(next),publishTarget=()=>{},releaseTarget=()=>{},resume=()=>{};
            const cleanupTrace=()=>{},atTarget=()=>false,targetOnScreen=()=>false,forgetPreview=()=>{};
            let response;const reply=(_r,value)=>response=value;
        ''' + function('afterRestore') + function('restoreOwnedPreview') + function('restoreBoundPreview') + '''
            restoreBoundPreview({action:'autoEnd'});
            if(mode==='context-changed')contextVersion=5;
            while(pending.length)pending.shift()(false);
            return {blocks,calls,response,empty,open:!!active,bound:active.bound,cleanup:active.cleanupRequested};
          }
          console.log(JSON.stringify(['ok','referenced-elsewhere','owner-changed','context-changed'].map(scenario)));
        ''')
        self.assertEqual(result[0]['calls'], [['restore-old', 16], ['clear', 128]])
        self.assertTrue(result[0]['response']['ended'])
        self.assertEqual(result[1]['calls'], [['restore-old', 16]])
        self.assertEqual(result[2]['calls'], [])
        self.assertEqual(result[3]['calls'], [['restore-old', 16]])
        for i, state in enumerate(result):
            self.assertEqual(state['blocks']['B'], {'Model': 11, 'Input': 8, 'Bass': 5})
            self.assertEqual(state['blocks']['A']['Input'], 6)
            self.assertEqual(state['blocks']['A']['Bass'], 4.5)
            self.assertTrue(state['open'])
            self.assertFalse(state['bound'])
            self.assertFalse(state['cleanup'])
            if i:
                self.assertFalse(state['response']['ended'])
                self.assertTrue(state['response']['retained'])

    def test_selection_change_queues_automatic_cleanup_after_native_event_unwinds(self):
        result = js('''
          let contextVersion=4,targetVersion=1,toneImport=null;
          const posts=[],wake=42,publishTarget=()=>{},send=()=>{},post=(...args)=>posts.push(args);
          const active={navigation:true,bound:true,valid:true,context:4,window:77,
            nativeTarget:{valid:true},transfer:{slot:128}};
        ''' + function('invalidate') + '''
          invalidate('selection-changed');console.log(JSON.stringify({posts,active}));
        ''')
        self.assertEqual(result['posts'], [[77, 42, 0, 0]])
        self.assertTrue(result['active']['cleanupRequested'])
        self.assertFalse(result['active']['valid'])
        self.assertFalse(result['active']['bound'])

    def test_duplicate_captures_have_distinct_bound_target_labels(self):
        result = js('const namView={slotName:()=>"Deluxe"};' + function('boundTargetLabel') + '''
          console.log(JSON.stringify([boundTargetLabel({dsp:0,blockName:'block0'},9),boundTargetLabel({dsp:0,blockName:'block2'},9)]));
        ''')
        self.assertNotEqual(result[0], result[1])
        self.assertIn('DSP 1', result[0])
        self.assertIn('block2', result[1])

    def test_unrelated_reply_cannot_release_inflight_workflow(self):
        result = js('''
          let workflowRequest={session:1,id:8},active={navigation:true};const calls=[];
          const namView={setWorkflowBusy:value=>calls.push(value)},send=()=>{},publishTarget=()=>{};
        ''' + function('reply') + '''
          reply({session:1,id:9,action:'bind'},{});const held=workflowRequest!==null;
          reply({session:1,id:8,action:'audition'},{error:'stopped'});
          console.log(JSON.stringify({held,calls,workflowRequest}));
        ''')
        self.assertTrue(result['held'])
        self.assertEqual(result['calls'], [False])
        self.assertIsNone(result['workflowRequest'])

    def test_permanent_library_reuses_verified_slot_or_lowest_empty_never128(self):
        result = js('''
          function scenario(existing,free,action='load') {
            const active={},AUDITION_SLOT=128,calls=[];
            const namView={verifiedCaptureSlot:()=>existing,freeSlots:()=>free,
              importPreview:(file,slot,preview)=>{calls.push(['import',slot,preview]);return{slot};}};
            const applyPermanent=(_r,slot,transfer)=>calls.push(['apply',slot,!!transfer]);
            const awaitImport=(_r,_t,next)=>next();let error=null;
        ''' + function('installPermanent') + '''
            try{installPermanent({identity:'a'.repeat(64),file:'capture.wav',action});}catch(e){error=e.message;}
            return{calls,error};
          }
          console.log(JSON.stringify([scenario(9,[2,4]),scenario(0,[2,4,128]),scenario(0,[128]),scenario(0,[2],'use')]));
        ''')
        self.assertEqual(result[0]['calls'], [['apply', 9, False]])
        self.assertEqual(result[1]['calls'], [['import', 2, False], ['apply', 2, True]])
        self.assertEqual(result[2]['calls'], [])
        self.assertEqual(result[3]['calls'], [])
        self.assertIsNotNone(result[2]['error'])
        self.assertIsNotNone(result[3]['error'])

    def test_permanent_apply_preserves_knobs_and_checks_target_again(self):
        result = js('''
          function scenario(stale) {
            let selected=9,undo=2,next,calls=[];const knobs={Input:6,Bass:4.5};
            const active={targetVersion:3,tile:{block:'NAM A2 Lite'}};
            const atTarget=()=>!stale,undoIndex=()=>undo,poll=(_r,cb)=>next=cb,captureTarget=()=>{};
            const namView={modelSlot:()=>selected,setSlot:n=>{calls.push(n);selected=n;undo++;return true;},slotName:()=> 'Demo'};
            let result=null,error=null;
        ''' + function('applyPermanent') + '''
            try{applyPermanent({targetVersion:3},7,null);result=next(false);}catch(e){error=e.message;}
            return{calls,result,error,knobs};
          }
          console.log(JSON.stringify([scenario(false),scenario(true)]));
        ''')
        self.assertEqual(result[0]['result'], {'usedSlot': 7})
        self.assertEqual(result[0]['calls'], [7])
        self.assertEqual(result[1]['calls'], [])
        for state in result:
            self.assertEqual(state['knobs'], {'Input': 6, 'Bass': 4.5})
    def test_write_waits_for_active_export_and_pauses_new_discovery(self):
        result = js('''
          let active={navigation:true,bound:true,targetVersion:2,quarantined:false},workflowRequest=null;
          let exporting=true,pending,calls=[];const profile={},AUDITION_SLOT=128;
          const namView={setWorkflowBusy:()=>calls.push('pause'),inventoryBusy:()=>exporting};
          const poll=(_r,next)=>pending=next,atTarget=()=>true,audition=()=>calls.push('audition');
        ''' + function('run') + '''
          run({action:'audition',session:1,id:1,targetVersion:2});
          const first=[...calls];pending(false);exporting=false;pending(false);
          console.log(JSON.stringify({first,calls}));
        ''')
        self.assertEqual(result['first'], ['pause'])
        self.assertEqual(result['calls'], ['pause', 'pause', 'audition'])

    def test_passive_connection_change_revokes_target_without_native_calls(self):
        result = js('''
          const profile={navigation:true},NULL=null,events=[];let connectionGeneration=-1,contextVersion=1,targetVersion=1;
          let selectedController={old:true},active={id:7,navigation:true,bound:true,valid:true,targetVersion:1,transfer:{slot:128}};
          const send=value=>events.push(value),releaseTarget=()=>{};
        ''' + function('connectionChanged') + '''
          connectionChanged({generation:3,present:false,ready:true});
          connectionChanged({generation:2,present:true,ready:true});
          connectionChanged({generation:3,present:true,ready:true});
          console.log(JSON.stringify({events,active,selectedController,contextVersion}));
        ''')
        self.assertEqual(len(result['events']), 1)
        self.assertFalse(result['active']['bound'])
        self.assertFalse(result['active']['valid'])
        self.assertEqual(result['active']['targetVersion'], 2)
        self.assertIsNone(result['selectedController'])
        self.assertTrue(result['active']['quarantined'])
        self.assertFalse(result['events'][0]['canBind'])

    def test_browser_open_without_selected_target_reuses_session_and_opens_requested_page(self):
        result = js('''
          const profile={navigation:true},NULL={isNull:()=>true},tiles=[{block:'NAM A2 Lite'}],events=[];
          const send=e=>events.push(e), publishTarget=()=>{}, window={isNull:()=>false,toUInt32:()=>77};
          const namView={},autoBindSelected=()=>{},refreshBounds=()=>{},allowForeground=()=>{};
          const dockPanel={isNull:()=>false},panelBounds=()=>({visible:true,x:80,y:100,w:500,h:600});
          let active=null,nextSession=0,targetVersion=0,contextVersion=3;
        ''' + function('openBrowser') + '''
          openBrowser(window,{dockPanel}); const first={...active}; openBrowser(window,{library:true,dockPanel});
          console.log(JSON.stringify({first:{bound:first.bound,valid:first.valid,context:first.context},events}));
        ''')
        self.assertEqual(result['first'], {'bound': False, 'valid': False, 'context': 3})
        self.assertEqual([e['type'] for e in result['events']], ['toneStart', 'toneFocus'])
        self.assertEqual(result['events'][1]['page'], 'library')

    def test_binding_requires_a_selected_block_and_no_unresolved_transfer(self):
        # A block left on the preview slot is bound without a typed restore binding, so that
        # Load/Use on Floor can give it a permanent slot; floorPreviewPlan refuses a preview on it.
        result = js('''
          let active={},targetVersion=0,selection=null;const captured=[];
          const AUDITION_SLOT=128,releaseTarget=()=>captured.push('released');
          const selectedTarget=()=>selection,publishTarget=()=>{},undoIndex=()=>3,reply=()=>{},captureTarget=()=>captured.push('typed');
        ''' + function('bindSelectedTarget') + function('bindSelected') + '''
          const results=[];
          for(const state of [{}, {slot:128}, {slot:9,transfer:true}, {slot:9,quarantine:true}, {slot:9}]) {
            active={transfer:state.transfer,quarantined:state.quarantine};
            selection=state.slot?{slot:state.slot,tile:{block:'NAM A2 Lite'},label:'NAM A'}:null;
            try {bindSelected({});results.push({bound:active.bound,version:active.targetVersion,onPreviewSlot:active.onPreviewSlot});}
            catch(e){results.push('blocked');}
          }
          console.log(JSON.stringify([results,captured]));
        ''')
        results, captured = result
        self.assertEqual(results[0], 'blocked')
        self.assertEqual(results[1], {'bound': True, 'version': 1, 'onPreviewSlot': True})
        self.assertEqual(results[2:4], ['blocked'] * 2)
        self.assertEqual(results[4], {'bound': True, 'version': 2, 'onPreviewSlot': False})
        self.assertEqual(captured, ['released', 'typed'])

    def test_different_controller_or_context_is_not_the_bound_target(self):
        result = js('''
          let selectedController='A',contextVersion=1;
          const controller={equals:value=>value==='A'};
          const active={navigation:true,valid:true,controller,context:1,position:'pos',block:'NAM'};
          const controllerReady=()=>true,position=()=> 'pos',block=()=> 'NAM';
        ''' + function('atPosition') + function('atTarget') + '''
          const results=[atTarget()]; selectedController='B';results.push(atTarget());
          selectedController='A';contextVersion=2;results.push(atTarget());
          console.log(JSON.stringify(results));
        ''')
        self.assertEqual(result, [True, False, False])

    def test_delayed_native_import_finishes_but_never_applies_to_changed_target(self):
        result = js('''
          let active={navigation:true,targetVersion:2},pending,completed=false,applied=false,finished=false,error=null;
          const transfer={slot:128},request={action:'audition',targetVersion:1};
          const poll=(_r,next,_t,finishImport)=>{pending=next;if(!finishImport)throw Error('must complete transfer');};
          const finishTransfer=()=>finished=true,namView={previewName:()=>completed};
          const atTarget=()=>false;
        ''' + function('awaitImport') + '''
          awaitImport(request,transfer,()=>applied=true);
          const waiting=pending(false);completed=true;
          try{pending(false);}catch(e){error=e.message;}
          console.log(JSON.stringify({waiting,applied,finished,error,orphan:active.orphanTransfer.slot,pending:active.pendingTransfer}));
        ''')
        self.assertIsNone(result['waiting'])
        self.assertFalse(result['applied'])
        self.assertTrue(result['finished'])
        self.assertEqual(result['orphan'], 128)
        self.assertIsNone(result['pending'])
        self.assertIn('no block was changed', result['error'])

    def test_selection_during_upload_schedules_owned128_cleanup_without_applying_it(self):
        result = js('''
          let active={navigation:true,targetVersion:2,context:4,nativeTarget:{valid:true,originalSlot:16}},pending,contextVersion=4;
          let applied=false,error=null;const transfer={slot:128},request={action:'audition',targetVersion:1};
          const poll=(_r,next)=>pending=next,finishTransfer=()=>{},namView={previewName:()=>true};
        ''' + function('awaitImport') + '''
          awaitImport(request,transfer,()=>applied=true);
          try{pending(false);}catch(e){error=e.message;}
          console.log(JSON.stringify({applied,error,cleanup:active.cleanupRequested,slot:active.transfer.slot,origin:active.origin.slot}));
        ''')
        self.assertFalse(result['applied'])
        self.assertTrue(result['cleanup'])
        self.assertEqual(result['slot'], 128)
        self.assertEqual(result['origin'], 16)

    def test_restore_changes_only_model_and_clears_only_after_confirmation(self):
        result = js('''
          function scenario(moved) {
            let active={navigation:true,valid:!moved,bound:true,targetVersion:1,controller:{},editor:{},
              transfer:{slot:128},origin:{slot:16,block:'NAM',structural:false}};
            let selected=128,targetVersion=1; const calls=[],pending=[],knobs={Input:6,Bass:4.5};
            const namView={modelSlot:()=>selected,previewName:()=>true,
              setSlot:n=>{calls.push('model:'+n);selected=n;return true;},
              clearPreview:()=>{calls.push('clear:128');return true;},previewSlotEmpty:()=>true,stopPreview:()=>{}};
            const send=()=>{},publishTarget=()=>{},releaseTarget=()=>{},undoIndex=()=>4,position=()=> 'p',block=()=> 'NAM';
            const playing=()=>!moved,poll=(_r,next)=>pending.push(next),resume=()=>{};
            let response; const reply=(_r,value)=>response=value;
        ''' + function('afterRestore') + function('restore') + '''
            restore({action:'end'});while(pending.length)pending.shift()(false);
            return {calls,response,knobs,browserOpen:!!active,bound:active.bound};
          }
          console.log(JSON.stringify([scenario(false),scenario(true)]));
        ''')
        self.assertEqual(result[0]['calls'], ['model:16', 'clear:128'])
        self.assertEqual(result[0]['response'], {'ended': True})
        self.assertEqual(result[1]['calls'], [])
        self.assertFalse(result[1]['response']['ended'])
        self.assertTrue(result[1]['response']['retained'])
        for state in result:
            self.assertEqual(state['knobs'], {'Input': 6, 'Bass': 4.5})
            self.assertTrue(state['browserOpen'])
            self.assertFalse(state['bound'])
