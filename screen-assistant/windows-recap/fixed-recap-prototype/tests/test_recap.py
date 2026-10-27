import tempfile
import unittest
from pathlib import Path

from core import Token, metrics_from_tokens, parse_count, require_account, RecapError
from flow import Store, Runner, atomic_json
from transport import check_reply
from desktop import Desktop, View, uia_tokens, room_from_tokens


def t(text, x, y, w=65, h=16, score=.99):
    return Token(text, (x, y, x+w, y+h), score)


class ParsingTests(unittest.TestCase):
    def test_raw_approximate_and_missing(self):
        self.assertEqual(parse_count('1,234'), (1234, False))
        self.assertEqual(parse_count('1.2万'), (12000, True))
        self.assertEqual(parse_count('0'), (0, False))
        for bad in ['--', '', '12,34', '1.2', '-2', '12人']:
            self.assertEqual(parse_count(bad), (None, False))

    def test_labels_not_order_associate_values(self):
        tokens = [t('新增粉丝', 200, 100), t('1', 222, 72, 9),
                  t('累计观众', 90, 100), t('107', 110, 72, 25),
                  t('获得音浪', 0, 100), t('3073', 10, 72, 35)]
        result = metrics_from_tokens(list(reversed(tokens)))
        self.assertEqual(result['metrics']['累计观众'], 107)
        self.assertEqual(result['metrics']['获得音浪'], 3073)
        self.assertIsNone(result['metrics']['送礼人数'])
        self.assertEqual(result['raw_displays']['新增粉丝'], '1')
        moved = [Token(q.text, tuple(v+350 if i%2 == 0 else v+90 for i,v in enumerate(q.box)),q.score) for q in tokens]
        self.assertEqual(metrics_from_tokens(moved), result)

    def test_ambiguous_low_score_and_missing_are_null(self):
        tokens = [t('累计观众', 100, 100), t('107', 104, 72, 25), t('108', 134, 72, 25)]
        self.assertIsNone(metrics_from_tokens(tokens)['metrics']['累计观众'])
        self.assertIsNone(metrics_from_tokens([t('累计观众',100,100),t('9',110,72,9,score=.5)])['metrics']['累计观众'])
        self.assertIsNone(metrics_from_tokens([t('9',110,72,9)])['metrics']['累计观众'])

    def test_duplicate_label_and_inline_units(self):
        result = metrics_from_tokens([t('累计观众1.2万',0,0,130)])
        self.assertEqual(result['metrics']['累计观众'], 12000)
        self.assertIn('累计观众', result['approximate_metrics'])
        result = metrics_from_tokens([t('累计观众',0,100),t('累计观众',200,100),t('1',10,70)])
        self.assertIsNone(result['metrics']['累计观众'])

    def test_identity_exact_not_substring(self):
        require_account([t('抖音号：SQLjiayou',0,0,150)], 'SQLjiayou')
        for tokens in [[t('抖音号：SQLjiayou2',0,0,160)], [t('SQLjiayou',0,0)]]:
            with self.assertRaises(RecapError):
                require_account(tokens, 'SQLjiayou')


class FakeDesktop:
    def __init__(self):
        self.calls=[]
        self.fail=None
        self.during_bind=None

    def bind(self, account):
        self.calls.append('bind')
        if self.during_bind:
            self.during_bind()
        if self.fail:
            raise RecapError(self.fail)
        return {'douyin_account': account, 'room_id': '1234567890123456789', 'window': {'handle': 12}}

    def capture(self, binding):
        self.calls.append('capture')
        if self.fail:
            raise RecapError(self.fail)
        return {'metrics': {k:1 for k in ['累计观众','新增粉丝','评论人数','送礼人数','获得音浪']},
                'missing_fields': [], 'metrics_captured_at':'test', 'capture_evidence':{}}


class FlowTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store=Store(Path(self.tmp.name))
        self.desktop=FakeDesktop()
        self.runner=Runner(self.store,self.desktop)

    def test_split_idempotent_start_stop_and_readback(self):
        first=self.runner.start('one','abc',None,'test_simulated')
        self.assertEqual(first['status'],'waiting_for_stop')
        self.assertIsNone(first['metrics'])
        self.runner.start('one','abc',None,'test_simulated')
        result=self.runner.stop('one','test_simulated')
        self.assertEqual(result['status'],'captured_locally')
        self.assertEqual(self.runner.stop('one','test_simulated'),result)
        self.assertEqual(self.desktop.calls,['bind','capture'])
        self.assertEqual(self.store.get('one'),result)
        self.assertIsNone(result['session_id'])

    def test_stop_during_binding_is_not_lost(self):
        self.desktop.during_bind=lambda:self.runner.stop('one','test_simulated')
        result=self.runner.start('one','abc',None,'test_simulated')
        self.assertTrue(result['stop_signal_received'])
        self.assertEqual(result['status'],'captured_locally')
        self.assertEqual(self.desktop.calls,['bind','capture'])

    def test_failure_stops_dependent_actions(self):
        for reason in ['USER_CONTROL','wrong_identity','locator_ambiguous']:
            with self.subTest(reason=reason):
                folder=Path(self.tmp.name)/reason
                fake=FakeDesktop(); fake.fail=reason
                result=Runner(Store(folder),fake).start('one','abc',None,'test_simulated')
                self.assertEqual(result['status'],'needs_input')
                self.assertEqual(fake.calls,['bind'])
                self.assertIsNone(result['metrics'])

    def test_account_conflict_and_busy(self):
        self.runner.start('one','abc',None,'test_simulated')
        with self.assertRaises(RecapError):
            self.runner.start('one','wrong',None,'test_simulated')
        with self.assertRaises(RecapError):
            self.runner.start('two','other',None,'test_simulated')
        self.assertEqual(self.desktop.calls,['bind'])

    def test_stop_unknown_does_not_create_fake_task(self):
        with self.assertRaises(RecapError):
            self.runner.stop('unknown','test_simulated')
        self.assertEqual(self.desktop.calls,[])

    def test_json_serializable_geometry_roundtrips(self):
        import json
        path=Path(self.tmp.name)/'geometry.json'
        atomic_json(path, {'box':(1,2,3,4)})
        self.assertEqual(json.loads(path.read_text(encoding='utf-8')), {'box':[1,2,3,4]})

    def test_partial_result_is_saved_and_not_recaptured(self):
        class Partial(FakeDesktop):
            def capture(self,binding):
                result=super().capture(binding)
                result['metrics']['新增粉丝']=None
                result['missing_fields']=['新增粉丝']
                return result
        fake=Partial(); runner=Runner(self.store,fake)
        runner.start('one','abc',None,'test_simulated')
        result=runner.stop('one','test_simulated')
        self.assertEqual(result['status'],'partial')
        self.assertIsNone(result['metrics']['新增粉丝'])
        self.assertEqual(runner.stop('one','test_simulated'),result)
        self.assertEqual(fake.calls,['bind','capture'])


class AdapterTests(unittest.TestCase):
    def test_locator_ambiguity_stops_without_second_observation(self):
        from unittest.mock import Mock
        with tempfile.TemporaryDirectory() as folder:
            driver=Desktop(None,None,None,folder,'https://union.bytedance.com/open/portal/')
            driver.observe=Mock(return_value=None)
            def ambiguous(view): raise RecapError('locator_ambiguous')
            with self.assertRaises(RecapError): driver.wait({},ambiguous)
            self.assertEqual(driver.observe.call_count,1)

    def test_cli_denies_before_loading_desktop(self):
        import subprocess
        import sys
        import json
        with tempfile.TemporaryDirectory() as folder:
            code=Path(__file__).resolve().parents[2]/'glm-recap-prototype'/'recap_glm.py'
            result=subprocess.run([sys.executable,str(code),'start','--account','demo',
                                  '--data',folder],capture_output=True,text=True,encoding='utf-8')
            self.assertEqual(result.returncode,2)
            self.assertEqual(json.loads(result.stdout)['reason'],'current_desktop_authorization_required')
            self.assertEqual(list(Path(folder).glob('*.json')),[])

    def test_control_status_matches_installed_ai_state(self):
        import json
        class MCP:
            def call(self,*args,**kwargs):
                return {'content':[{'text':json.dumps({'state':self.state})}]}
        mcp=MCP()
        with tempfile.TemporaryDirectory() as folder:
            desktop=Desktop(mcp,None,None,folder,'https://union.bytedance.com/open/portal/')
            for state in ('ready','ai'):
                mcp.state=state; desktop.guard()
            for state in ('user','takeover_pending','unavailable','unexpected'):
                mcp.state=state
                with self.assertRaises(RecapError): desktop.guard()

    def test_tool_plain_text_error_stops(self):
        for text in ('USER_CONTROL: stop','Error: failed to switch','Application not found'):
            with self.assertRaises(RecapError): check_reply({'content':[{'text':text}]})

    def test_uia_current_window_only_and_value(self):
        text='UI Tree:\nwindow "A"\n└── (100,50) edit "地址和搜索栏" [value:"https://union.bytedance.com/open/portal/live_flow/followRoomDetail?roomID=1234567890123456789&x=1"]\nwindow "B"\n└── (1,2) button "进入直播间"'
        tokens=uia_tokens(text,'A')
        self.assertEqual(room_from_tokens(tokens),'1234567890123456789')
        self.assertFalse(any(q.text=='进入直播间' for q in tokens))
        with self.assertRaises(RecapError): room_from_tokens([t('https://other.com/?roomID=1234567890123456789',0,0)])

    def test_scaled_image_origin_and_moving_window(self):
        v=View([],Path('unused'),{'rect':[-400,200,800,1000]},(600,400),'test',[])
        self.assertEqual(v.point(t('按钮',90,40,20,20)),[-200,300])

    def test_wrong_capture_identity_stops_without_metrics(self):
        class Wrong(FakeDesktop):
            def capture(self,binding):
                self.calls.append('capture_identity_check')
                raise RecapError('wrong_identity')
        with tempfile.TemporaryDirectory() as folder:
            fake=Wrong(); runner=Runner(Store(folder),fake)
            runner.start('one','abc',None,'test_simulated')
            result=runner.stop('one','test_simulated')
            self.assertEqual(result['status'],'needs_input')
            self.assertIsNone(result['metrics'])
            self.assertEqual(fake.calls,['bind','capture_identity_check'])


if __name__ == '__main__':
    unittest.main()
