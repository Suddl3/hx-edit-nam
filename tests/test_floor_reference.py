"""Floor integration regressions: no device, browser, or proprietary fixtures."""
import json
import importlib.util
from importlib.machinery import SourceFileLoader
from pathlib import Path
import re
import shutil
import subprocess
import unittest
from unittest.mock import Mock, patch

from floor_reference import require_plain_catalog, startup_profiles

ROOT = Path(__file__).resolve().parents[1]
TONE = (ROOT / 'tone_view.js').read_text(encoding='utf-8')
NAM = (ROOT / 'nam_view.js').read_text(encoding='utf-8')


def js(code):
    return json.loads(subprocess.run(['node', '-e', code], check=True, text=True,
                                    capture_output=True, timeout=10).stdout)


class FloorStartupTests(unittest.TestCase):
    def test_launcher_composes_native_profile_and_floating_browser_without_running_either(self):
        loader = SourceFileLoader('floor_reference_launcher_test', str(ROOT / 'start-hx-edit-nam.pyw'))
        spec = importlib.util.spec_from_loader(loader.name, loader)
        launcher = importlib.util.module_from_spec(spec)
        loader.exec_module(launcher)
        device, session, script = Mock(), Mock(), Mock()
        callbacks, message_callbacks = {}, {}
        device.spawn.return_value = 123
        device.attach.return_value = session
        session.on.side_effect = lambda name, action: callbacks.__setitem__(name, action)
        session.create_script.return_value = script
        script.on.side_effect = lambda name, action: message_callbacks.__setitem__(name, action)
        script.load.side_effect = lambda: message_callbacks['message']({'type': 'send', 'payload': {'type': 'ready'}}, None)
        device.resume.side_effect = lambda _: callbacks['detached']()
        with patch('nam_catalog.ir_parameters', return_value={}), \
             patch('nam_catalog.sidebar_width', return_value=320), \
             patch('nam_library.Library'), patch('tone_preview.TonePreview') as preview, \
             patch('tone_browser.ToneBrowser') as browser:
            preview.return_value.thread = None
            launcher.run_editor(Path('not-a-real-editor.exe'), 'unused', device, {},
                                floor_reference=True, tone_window='floating')
            factory = preview.call_args.kwargs['browser_factory']
            factory('parent', 'bounds', 'detach')
            browser.assert_called_once_with('parent', 'bounds', 'detach', mode='floating')
            injected = session.create_script.call_args.args[0]
            self.assertIn('"strictPreview": true', injected)
            self.assertIn('"lastPermanentSlot": 127', injected)
            self.assertIn('installToneView', injected)
        device.kill.assert_not_called()

    def test_native_only_never_calls_scanner(self):
        scan = Mock(side_effect=AssertionError('USB scanner must not run'))
        self.assertEqual(startup_profiles(floor_reference=True, scan=scan,
                                         converter_directory=None), ({}, False))
        scan.assert_not_called()

    def test_other_device_scan_behavior_preserved(self):
        scan = Mock(return_value={9: 'Example'})
        self.assertEqual(startup_profiles(floor_reference=False, scan=scan,
                                         converter_directory='converter'), ({9: 'Example'}, False))
        scan.assert_called_once_with('converter', None)

    def test_incompatible_catalog_or_device_rejected(self):
        require_plain_catalog(layers=False, pedal=False)
        for layers, pedal in [(True, False), (False, True), (True, True)]:
            with self.assertRaises(ValueError):
                require_plain_catalog(layers=layers, pedal=pedal)
        with self.assertRaises(ValueError):
            startup_profiles(floor_reference=True, scan=Mock(), converter_directory=None, hx_nam='stomp')


@unittest.skipUnless(shutil.which('node'), 'Node.js is required')
class FloorNativePolicyTests(unittest.TestCase):
    def test_repeat_preview_restores_then_clears_only_owned128_and_retains_controls(self):
        plan = re.search(r'^function floorPreviewPlan.*?^}$', TONE, re.S | re.M).group(0)
        audition = re.search(r'^  function audition\(request\).*?^  }$', TONE, re.S | re.M).group(0)
        script = plan + '\n' + '''
          function scenario(mode) {
            const calls=[], pending=[], profile={strictPreview:true}, AUDITION_SLOT=128;
            const previous={slot:128,name:'owned'}, knobs={Input:6,Bass:4.5,Treble:5.5};
            let selected=128, owns=true, empty=false, target=true;
            const active={transfer:previous,origin:{slot:16,structural:false},model:'NAM A2 Lite',tile:{block:'NAM A2 Lite'}};
            const atTarget=()=>target, playing=()=>true;
            const targetChanged=(_,text)=>new Error(text);
            const poll=(request,next)=>pending.push(next);
            const awaitImport=(request,transfer,next)=>next();
            const applyProfile=()=>calls.push('apply');
            const namView={
              previewName:()=>owns?'owned':'', slotEmpty:()=>empty, modelSlot:()=>selected,
              setSlot:slot=>{calls.push('restore:'+slot);if(mode!=='restore-timeout')selected=slot;return true;},
              clearPreview:()=>{if(!owns)return false;calls.push('clear:128');empty=mode!=='clear-timeout';return true;},
              previewSlotEmpty:()=>empty, stopPreview:()=>calls.push('stop'),
              importPreview:(file,slot)=>{if(!empty)throw Error('unsafe overwrite');calls.push('import:'+slot);return {slot};}
            };
        ''' + audition + '''
            let error=null;
            try {
              audition({file:'new',action:'audition'});
              if(mode==='target-changed')target=false;
              if(mode==='ownership-changed')owns=false;
              while(pending.length)pending.shift()(true);
            }catch(e){error=e.message;}
            return {calls,error,knobs,origin:active.origin.slot};
          }
          console.log(JSON.stringify(['ok','restore-timeout','clear-timeout','target-changed','ownership-changed'].map(scenario)));
        '''
        result = js(script)
        self.assertEqual(result[0]['calls'], ['restore:16', 'clear:128', 'stop', 'import:128', 'apply'])
        self.assertIsNone(result[0]['error'])
        self.assertEqual(result[1]['calls'], ['restore:16'])
        self.assertEqual(result[2]['calls'], ['restore:16', 'clear:128'])
        self.assertEqual(result[3]['calls'], ['restore:16'])
        self.assertEqual(result[4]['calls'], ['restore:16'])
        for i, outcome in enumerate(result):
            self.assertEqual(outcome['knobs'], {'Input': 6, 'Bass': 4.5, 'Treble': 5.5})
            self.assertEqual(outcome['origin'], 16)
            if i:
                self.assertIsNotNone(outcome['error'])

    def test_a_preview_only_ever_uses_slot_128_and_replaces_what_is_there(self):
        # Owner decision, November 13: User IR 128 is the preview slot and nothing else, so an
        # occupied 128 is replaced rather than refused. A preview still never uses another slot,
        # and a block that is itself on 128 has nothing to return to and is refused.
        function = re.search(r'^function floorPreviewPlan.*?^}$', TONE, re.S | re.M).group(0)
        state = {'atTarget': True, 'deviceSaved': False, 'hasTransfer': False,
                 'sameModel': True, 'modelSlot': 9, 'empty128': True}
        script = function + f'\nconst state = {json.dumps(state)};\n' + '''
          const results = [floorPreviewPlan(state)];
          results.push(floorPreviewPlan({...state, empty128:false}));
          for (const change of [{modelSlot:128}, {atTarget:false},
                                {deviceSaved:true}, {sameModel:false}, {modelSlot:null}]) {
            try { floorPreviewPlan({...state, ...change}); results.push('UNSAFE'); }
            catch (e) { results.push('blocked'); }
          }
          console.log(JSON.stringify(results));
        '''
        self.assertEqual(js(script), [{'kind': 'first', 'slot': 128, 'replace': False},
                                      {'kind': 'first', 'slot': 128, 'replace': True}] + ['blocked'] * 5)

    def test_reuse_requires_same_target_and_owned_preview_and_permanent_origin(self):
        function = re.search(r'^function floorPreviewPlan.*?^}$', TONE, re.S | re.M).group(0)
        state = {'atTarget': True, 'deviceSaved': False, 'hasTransfer': True, 'playing': True,
                 'ownsPreview': True, 'previewSlot': 128, 'origin': {'slot': 16, 'structural': False}}
        script = function + f'\nconst state = {json.dumps(state)};\n' + '''
          const results = [floorPreviewPlan(state)];
          for (const change of [{atTarget:false}, {deviceSaved:true}, {playing:false},
             {ownsPreview:false}, {previewSlot:17}, {origin:null},
             {origin:{slot:128}}, {origin:{slot:16,structural:true}}]) {
            try { floorPreviewPlan({...state, ...change}); results.push('UNSAFE'); }
            catch (e) { results.push('blocked'); }
          }
          console.log(JSON.stringify(results));
        '''
        self.assertEqual(js(script), [{'kind': 'reuse', 'slot': 128, 'originalSlot': 16}] + ['blocked'] * 8)

    def test_native_tab_order_repairs_both_visual_indexes_without_changing_page_ids(self):
        function = re.search(r'^  function orderTabs\(\).*?^  }$', NAM, re.S | re.M).group(0)
        script = '''
          const address = x => x, libraryRoot = {};
          function tab(index, page) { return { index, page, isNull:()=>false,
            readPointer:()=>({equals:x=>x===0x8c35a0}), add:()=>({readS32:()=>index}) }; }
          const radio = {isNull:()=>false, readPointer:()=>({equals:x=>x===0x915358})};
          const findWidget=()=>radio, getRadioButton=(_,i)=>tabs[i];
          const configureTab=(t,unused,index)=>{t.index=index;}, layoutTab=()=>{}, redraw=()=>{};
          let tabs;
        ''' + function + '''
          const result=[];
          for (let f=0;f<4;f++) for(let n=0;n<4;n++) {
            tabs=[null,null,tab(f,2),tab(n,3)]; orderTabs();
            result.push(tabs.slice(2).map(t=>[t.index,t.page]));
          }
          console.log(JSON.stringify(result));
        '''
        self.assertEqual(js(script), [[[3, 2], [2, 3]]] * 16)

    def test_permanent_slots_are_ascending_and_exclude_reserved_preview(self):
        function = re.search(r'^    freeSlots\(excluded\) \{.*?^    },$', NAM, re.S | re.M).group(0)
        function = function.strip().removesuffix(',')
        script = '''
          const AUDITION_SLOT=128, profile={lastPermanentSlot:127}, occupied=new Set([1,3]);
          const irList=()=>({isNull:()=>false});
          const getIrRow=(_,index)=>({isNull:()=>false,add:()=>({readU32:()=>occupied.has(index+1)?1:0})});
          const view={''' + function + '''};
          const slots=view.freeSlots(9);
          console.log(JSON.stringify({first:slots[0],last:slots.at(-1),has128:slots.includes(128),has9:slots.includes(9)}));
        '''
        self.assertEqual(js(script), {'first': 2, 'last': 127, 'has128': False, 'has9': False})


if __name__ == '__main__':
    unittest.main()
