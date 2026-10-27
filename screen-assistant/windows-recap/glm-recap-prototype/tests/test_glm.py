import json
from pathlib import Path
import sys
import tempfile
import unittest
import subprocess
from unittest.mock import Mock,patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from glm_runtime import MODEL, BASE_URL, parse_final, require_gate_a
from glm_vision import GLMVision,validate_box


class GLMTests(unittest.TestCase):
    def test_app_sources_early_stop_preserve_session_and_reject_mixed_source(self):
        from flow import Store
        for source in ('ios_app','android_app'):
            with tempfile.TemporaryDirectory() as folder:
                store=Store(folder); store.create('source-test','demo','existing-session',source)
                command=[sys.executable,str(ROOT/'recap_glm.py'),'stop','--task','source-test',
                    '--data',folder,'--desktop-authorized','--source',source]
                result=subprocess.run(command,capture_output=True,text=True,encoding='utf-8')
                self.assertEqual(result.returncode,0,result.stderr)
                state=json.loads(result.stdout)['record']
                self.assertEqual(state['session_id'],'existing-session')
                self.assertEqual(state['stop_signal_source'],source)
                before=store.path('source-test').read_bytes()
                command[-1]='android_app' if source=='ios_app' else 'ios_app'
                conflict=subprocess.run(command,capture_output=True,text=True,encoding='utf-8')
                self.assertEqual(json.loads(conflict.stdout)['reason'],'signal_source_conflict')
                self.assertEqual(store.path('source-test').read_bytes(),before)

    def test_only_exact_existing_model_and_route(self):
        self.assertEqual(MODEL,'glm-5.3-flash')
        self.assertEqual(BASE_URL,'https://ai.ctaigw.cn/v1')

    def test_plain_or_single_fenced_json_and_tool_receipt(self):
        expected={'status':'found','box':[1,2,3,4]}
        content=json.dumps({'observation_receipt':'abc','result':expected})
        self.assertEqual(parse_final(content,'abc'),expected)
        self.assertEqual(parse_final('```json\n'+content+'\n```','abc'),expected)
        for wrong in ('{}','prefix '+content,content.replace('abc','other')):
            with self.assertRaises(ValueError): parse_final(wrong,'abc')
        self.assertEqual(parse_final(json.dumps(expected)),expected)
        self.assertEqual(parse_final(json.dumps({'result':expected})),expected)
        with self.assertRaises(ValueError): parse_final('')

    def test_old_model_gate_cannot_enable_glm_desktop(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder,'gate.json')
            path.write_text(json.dumps({'gate_a_visual_mcp_roundtrip':'OK','model_requested':'gpt-6.1-sol'}),encoding='utf-8')
            with self.assertRaises(ValueError): require_gate_a(path)

    def test_missing_credential_does_not_start_process(self):
        from glm_runtime import GLMRuntime
        with tempfile.TemporaryDirectory() as folder,patch.dict('os.environ',{},clear=True),patch('glm_runtime.subprocess.Popen') as process:
            with self.assertRaisesRegex(RuntimeError,'missing_current_process_tianyi_key'):
                GLMRuntime(folder).run(Path(folder)/'missing.png','goal')
            process.assert_not_called()

    def test_invalid_box_and_ambiguous_do_not_become_actions(self):
        for box in ([-1,2,3,4],[1,2,101,4],[1,2,float('nan'),4],[1,2,3]):
            with self.assertRaises(ValueError): validate_box({'status':'found','box':box},(100,100))
        from core import RecapError
        from PIL import Image
        with tempfile.TemporaryDirectory() as folder:
            image=Path(folder)/'image.png'; Image.new('RGB',(100,100)).save(image)
            runtime=Mock(); runtime.run.return_value=({'status':'ambiguous','box':None},{})
            with self.assertRaises(RecapError): GLMVision(runtime).locate(Mock(image=image),'target')
            self.assertEqual(runtime.run.call_count,1)

    def test_read_only_status_without_model_or_desktop(self):
        with tempfile.TemporaryDirectory() as folder:
            value={'local_test_id':'test-one','status':'waiting_for_stop','session_id':None}
            Path(folder,'test-one.json').write_text(json.dumps(value),encoding='utf-8')
            process=subprocess.run([sys.executable,str(ROOT/'recap_glm.py'),'status','--task','test-one','--data',folder],
                capture_output=True,text=True,encoding='utf-8')
            self.assertEqual(process.returncode,0,process.stderr)
            self.assertEqual(json.loads(process.stdout)['record'],value)

    def test_no_desktop_authorization_stops_before_runtime(self):
        with tempfile.TemporaryDirectory() as folder:
            process=subprocess.run([sys.executable,str(ROOT/'recap_glm.py'),'start','--task','no-auth',
                '--account','demo','--data',folder],capture_output=True,text=True,encoding='utf-8')
            self.assertEqual(process.returncode,2)
            self.assertEqual(json.loads(process.stdout)['reason'],'current_desktop_authorization_required')
            self.assertFalse(Path(folder,'no-auth.json').exists())

    def test_full_steps_reuse_with_explicit_fakes_and_glm_metadata(self):
        sys.path.insert(0,str(ROOT.parent/'fixed-recap-prototype/tests'))
        from test_fixed_path import Scenario
        from desktop import View
        from core import Token
        from flow import Store,Runner
        from glm_desktop import GLMDesktop
        with tempfile.TemporaryDirectory() as folder:
            scenario=Scenario(folder); original_call=scenario.call
            for window in (scenario.window,scenario.room):
                window['raw_rect']=list(window['rect']); window['maximized']=False
            def call(tool,**args):
                if tool=='ControlStatus': return {'content':[{'text':'{"state":"ai","generation":7}'}]}
                return original_call(tool,**args)
            scenario.call=call; scenario.note=lambda *args,**kwargs:None
            class Vision:
                def locate(self,view,goal,toolbar=False):
                    label={'home':'主播管理','management':'主播列表','list':'请输入主播昵称或抖音号',
                        'candidate':'抖音号:demo','filtered':'主播详情','detail':'进入直播间'}[scenario.stage]
                    return next(q for q in view.tokens if q.text==label)
            class Driver(GLMDesktop):
                def observe(self,window,use_uia=False):
                    tokens=scenario.tokens()
                    if scenario.stage=='filtered': tokens.append(Token('抖音号:demo',(100,60,200,72)))
                    return View(tokens,scenario.image,scenario.get(window),(600,400),'fake',[])
            driver=Driver(scenario,scenario,None,Path(folder)/'data/evidence',
                'https://union.bytedance.com/open/portal/',vision=Vision())
            runner=Runner(Store(Path(folder)/'data'),driver)
            record=runner.start('glm-fake','demo',None,'test_simulated')
            self.assertEqual(record['status'],'waiting_for_stop',record)
            self.assertIn(MODEL,record['binding_evidence']['locator'])
            actions=list(scenario.inputs)
            self.assertEqual(runner.start('glm-fake','demo',None,'test_simulated'),record)
            self.assertEqual(scenario.inputs,actions)
            record=runner.stop('glm-fake','test_simulated')
            self.assertEqual(record['status'],'captured_locally',record)
            self.assertEqual(record['metrics'],{'累计观众':100,'新增粉丝':1,'评论人数':9,'送礼人数':2,'获得音浪':300})
            actions=list(scenario.inputs)
            self.assertEqual(runner.stop('glm-fake','test_simulated'),record)
            self.assertEqual(scenario.inputs,actions)

if __name__=='__main__': unittest.main()
