"""只用内存合成图和工具替身验证有界恢复，不请求模型或操作桌面。"""
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import types
import unittest
from unittest.mock import Mock,patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'fixed-recap-prototype'))
from core import RecapError,Token
from glm_desktop import GLMDesktop
from glm_runtime import GLMRuntime
from glm_vision import GLMVision
import test_memory_visual as fixtures


def action(kind):
    return Token(kind,(100,80,230,110),1.,'vision')


class NavigationRecoveryTests(unittest.TestCase):
    def driver(self):
        view=fixtures.MemoryVisualTests().view([])
        driver=object.__new__(GLMDesktop)
        driver.guard=Mock(side_effect=lambda window=None:window)
        driver.observe=Mock(side_effect=lambda *a,**k:fixtures.MemoryVisualTests().view([]))
        driver.vision=Mock(); driver.mcp=Mock(); driver.mcp.wire=driver.mcp; driver.mcp._call_inflight=False
        driver.click=Mock()
        return driver,view

    def test_transient_model_failure_reobserves_and_continues(self):
        driver,view=self.driver()
        driver.vision.next_search_action.side_effect=[RecapError('visual_worker_connection_error'),action('search_field')]
        with patch('glm_desktop.time.sleep'):
            fresh,field=driver.search_entry(view.window)
        self.assertEqual(field.text,'search_field')
        self.assertEqual(driver.observe.call_count,2)
        first,second=[call.args[0] for call in driver.vision.next_search_action.call_args_list]
        self.assertIsNot(first,second)
        driver.click.assert_not_called()
        self.assertEqual(driver.navigation_diagnostics[-1]['model_recoveries'],1)

    def test_continuous_failure_stops_after_two_recoveries(self):
        driver,view=self.driver()
        driver.vision.next_search_action.side_effect=RecapError('visual_worker_connection_error')
        with patch('glm_desktop.time.sleep'),self.assertRaisesRegex(RecapError,'visual_worker_connection_error'):
            driver.search_entry(view.window)
        self.assertEqual(driver.vision.next_search_action.call_count,3)
        self.assertEqual(driver.navigation_diagnostics[-1]['model_recoveries'],2)
        self.assertEqual(driver.navigation_diagnostics[-1]['reason'],'visual_worker_connection_error')
        driver.click.assert_not_called()

    def test_menu_missing_or_ambiguous_reobserves_before_stopping(self):
        for reason in ('visual_missing','visual_ambiguous'):
            driver,view=self.driver()
            driver.vision.next_search_action.side_effect=[RecapError(reason),action('search_field')]
            with patch('glm_desktop.time.sleep'):
                driver.search_entry(view.window)
            self.assertEqual(driver.observe.call_count,2)
            self.assertEqual(driver.navigation_diagnostics[-1]['visual_reobservations'],1)
            driver.click.assert_not_called()

    def test_unapproved_model_action_is_never_executed(self):
        driver,view=self.driver(); runtime=Mock(); driver.vision=GLMVision(runtime)
        runtime.run_crop.return_value={'status':'found','kind':'unapproved_action','box':[100,80,230,110]}
        with self.assertRaisesRegex(RecapError,'visual_navigation_unconfirmed'):
            driver.search_entry(view.window)
        driver.click.assert_not_called()

    def test_takeover_and_nonrecoverable_failures_are_not_retried(self):
        for reason in ('USER_CONTROL','needs_login_or_verification','wrong_identity','room_changed',
                       'visual_worker_http_error','visual_worker_worker_timeout'):
            driver,view=self.driver(); driver.vision.next_search_action.side_effect=RecapError(reason)
            with self.subTest(reason=reason),self.assertRaisesRegex(RecapError,reason):
                driver.search_entry(view.window)
            self.assertEqual(driver.vision.next_search_action.call_count,1)
            driver.click.assert_not_called()

    def test_only_confirmed_transient_http_status_is_retried(self):
        for status in (408,409,429,503,401,403,400,None):
            driver,view=self.driver()
            driver.vision.runtime=types.SimpleNamespace(diagnostics=[{'category':'http_error','http_status':status}])
            driver.vision.next_search_action.side_effect=[RecapError('visual_worker_http_error'),action('search_field')]
            with self.subTest(status=status),patch('glm_desktop.time.sleep'):
                if status in (408,409,429,503):
                    driver.search_entry(view.window)
                    self.assertEqual(driver.vision.next_search_action.call_count,2)
                else:
                    with self.assertRaisesRegex(RecapError,'visual_worker_http_error'):
                        driver.search_entry(view.window)
                    self.assertEqual(driver.vision.next_search_action.call_count,1)

    def test_action_applied_with_error_receipt_is_observed_not_replayed(self):
        driver,view=self.driver()
        state={'search_visible':False}
        def observe(*args,**kwargs):
            current=fixtures.MemoryVisualTests().view([])
            current.search_visible=state['search_visible']
            return current
        def applied(*args):
            state['search_visible']=True
            raise RecapError('mcp_tool_error')
        driver.observe.side_effect=observe; driver.click.side_effect=applied
        driver.vision.next_search_action.side_effect=lambda current,guard:action('search_field' if current.search_visible else 'navigation_entry')
        _,field=driver.search_entry(view.window)
        self.assertEqual(field.text,'search_field')
        self.assertEqual(driver.click.call_count,1)
        self.assertEqual(driver.observe.call_count,2)
        self.assertEqual(driver.navigation_diagnostics[-1]['action_reobservations'],1)

    def test_recoveries_are_shared_across_menu_steps_not_reset(self):
        driver,view=self.driver()
        failure=RecapError('visual_worker_connection_error')
        driver.vision.next_search_action.side_effect=[failure,failure,action('navigation_entry'),failure]
        with patch('glm_desktop.time.sleep'),self.assertRaisesRegex(RecapError,'visual_worker_connection_error'):
            driver.search_entry(view.window)
        self.assertEqual(driver.vision.next_search_action.call_count,4)
        self.assertEqual(driver.navigation_diagnostics[-1]['model_recoveries'],2)

    def test_takeover_before_retry_stops_and_is_recorded(self):
        driver,view=self.driver(); driver.guard.side_effect=RecapError('USER_CONTROL')
        driver.vision.next_search_action.side_effect=RecapError('visual_worker_connection_error')
        with self.assertRaisesRegex(RecapError,'USER_CONTROL'):
            driver.search_entry(view.window)
        self.assertEqual(driver.vision.next_search_action.call_count,1)
        self.assertEqual(driver.navigation_diagnostics[-1]['reason'],'USER_CONTROL')

    def test_address_and_portal_use_fresh_bounded_recovery(self):
        driver,view=self.driver(); driver.key=Mock(); driver.type_replacement=Mock()
        driver.portal_url='https://union.bytedance.com/open/portal/index'
        driver.vision.locate.side_effect=[RecapError('visual_worker_connection_error'),action('confirmed_address_bar')]
        driver.vision.verify_portal.side_effect=[RecapError('visual_worker_request_timeout'),None]
        with patch('glm_desktop.time.sleep'):
            driver.navigate_entry(view.window)
        self.assertEqual(driver.vision.locate.call_count,2)
        self.assertEqual(driver.vision.verify_portal.call_count,2)
        self.assertEqual(driver.type_replacement.call_count,1)
        self.assertEqual(driver._model_recoveries,2)

    def test_unsettled_tool_timeout_never_replays_or_uses_misaligned_channel(self):
        driver,view=self.driver(); driver.vision.next_search_action.return_value=action('navigation_entry')
        driver.click.side_effect=RecapError('mcp_timeout_outcome_unknown')
        driver.mcp._call_inflight=True
        with self.assertRaisesRegex(RecapError,'mcp_timeout_outcome_unknown'):
            driver.search_entry(view.window)
        self.assertEqual(driver.click.call_count,1); self.assertEqual(driver.observe.call_count,1)

    def test_broken_action_pipe_preserves_unknown_without_replay(self):
        driver,view=self.driver(); driver.vision.next_search_action.return_value=action('navigation_entry')
        driver.click.side_effect=BrokenPipeError('test-only')
        with self.assertRaisesRegex(RecapError,'mcp_pipe_outcome_unknown'):
            driver.search_entry(view.window)
        self.assertEqual(driver.click.call_count,1); self.assertEqual(driver.observe.call_count,1)

    def test_unchanged_uncertain_action_is_not_repeated(self):
        driver,view=self.driver(); driver.vision.next_search_action.return_value=action('navigation_entry')
        driver.click.side_effect=RecapError('mcp_tool_error')
        with self.assertRaisesRegex(RecapError,'menu_action_outcome_unknown'):
            driver.search_entry(view.window)
        self.assertEqual(driver.click.call_count,1); self.assertEqual(driver.observe.call_count,2)

    def test_different_boxes_at_same_uncertain_click_point_are_not_replayed(self):
        driver,view=self.driver()
        driver.vision.next_search_action.side_effect=[action('navigation_entry'),
            Token('navigation_entry',(90,70,240,120),1.,'vision')]
        driver.click.side_effect=RecapError('mcp_tool_error')
        with self.assertRaisesRegex(RecapError,'menu_action_outcome_unknown'):
            driver.search_entry(view.window)
        self.assertEqual(driver.click.call_count,1)

    def test_menu_without_progress_is_bounded(self):
        driver,view=self.driver(); driver.vision.next_search_action.return_value=action('navigation_entry')
        with self.assertRaisesRegex(RecapError,'search_navigation_budget_exhausted'):
            driver.search_entry(view.window)
        self.assertEqual(driver.click.call_count,6)
        self.assertEqual(driver.observe.call_count,6)

    def test_visual_can_choose_sidebar_or_floating_entry_without_ocr_anchor(self):
        for box in ([5,20,150,50],[240,70,430,110]):
            driver,view=self.driver(); runtime=Mock(); driver.vision=GLMVision(runtime)
            runtime.run_crop.side_effect=[{'status':'found','kind':'navigation_entry','box':box},
                {'status':'found','kind':'search_field','box':[300,140,600,170]}]
            _,field=driver.search_entry(view.window)
            self.assertEqual(field.text,'search_field'); driver.click.assert_called_once()
            self.assertEqual(runtime.run_crop.call_count,2)

    def test_real_runtime_receipts_keep_unknown_count_and_original_deadline(self):
        driver,view=self.driver()
        failed={'status':'FAIL','diagnostic':{'phase':'sdk_request','category':'connection_error',
            'request_attempts':1,'http_status':None,'output_chars':None,'output_tokens':None}}
        ok={'status':'OK','result':{'status':'found','kind':'search_field','box':[100,80,230,110]},'http_requests':1,
            'diagnostic':{'phase':'response_validate','category':'response_ok','request_attempts':1,
                'http_status':200,'output_chars':80,'output_tokens':30}}
        replies=[subprocess.CompletedProcess([],2,json.dumps(failed).encode(),b''),
                 subprocess.CompletedProcess([],0,json.dumps(ok).encode(),b'')]
        with tempfile.TemporaryDirectory() as folder,patch.dict('os.environ',{'TIANYI_API_KEY':'test-only',
                'BACKSTAGE_SDK_PYTHON':sys.executable}),patch('glm_runtime.require_gate_a'), \
                patch('glm_runtime.subprocess.run',side_effect=replies),patch('glm_desktop.time.sleep'):
            runtime=GLMRuntime(folder,gate='synthetic'); deadline=runtime.deadline
            driver.vision=GLMVision(runtime)
            driver.search_entry(view.window)
            self.assertEqual(runtime.calls,2); self.assertIsNone(runtime.http_requests)
            self.assertEqual(runtime.deadline,deadline)
            self.assertEqual([row['category'] for row in runtime.diagnostics],['connection_error','response_ok'])
            self.assertEqual(list(Path(folder).iterdir()),[])

    def test_total_budget_failure_is_not_hidden_as_missing_address(self):
        driver,view=self.driver()
        with tempfile.TemporaryDirectory() as folder,patch.dict('os.environ',{'TIANYI_API_KEY':'test-only',
                'BACKSTAGE_SDK_PYTHON':sys.executable}),patch('glm_runtime.require_gate_a'), \
                patch('glm_runtime.subprocess.run') as worker:
            runtime=GLMRuntime(folder,gate='synthetic'); runtime.calls=24
            driver.vision=GLMVision(runtime)
            with self.assertRaisesRegex(RecapError,'model_phase_budget_exhausted'):
                driver.search_entry(view.window)
            worker.assert_not_called()
            self.assertEqual(driver.navigation_diagnostics[-1]['reason'],'model_phase_budget_exhausted')

    def test_cli_persists_stage_recoveries_and_final_failure(self):
        import recap_glm
        from glm_runtime import VisualRuntimeError
        driver,view=self.driver()
        runtime=Mock(calls=3,http_requests=None,all_workers_finished=True,diagnostics=[])
        runtime.run_crop.side_effect=VisualRuntimeError('visual_worker_connection_error')
        driver.vision=GLMVision(runtime)
        driver.bind=lambda account:driver.search_entry(view.window)
        driver.mcp.close.return_value=True
        with tempfile.TemporaryDirectory() as folder:
            modules={'window_probe':types.SimpleNamespace(Probe=Mock()),
                'ocr_local':types.SimpleNamespace(LocalOCR=Mock()),
                'transport':types.SimpleNamespace(MCP=Mock(return_value=driver.mcp))}
            args=['recap_glm.py','start','--task','synthetic-recovery','--account','123456',
                  '--session-id','synthetic-session','--source','android_app','--data',folder,
                  '--scratch',folder,'--desktop-authorized']
            with patch.object(sys,'argv',args),patch.dict(sys.modules,modules), \
                 patch.object(recap_glm,'GLMRuntime',return_value=runtime), \
                 patch('glm_desktop.GLMDesktop',return_value=driver), \
                 patch.object(recap_glm,'require_gate_a'),patch.object(recap_glm,'require_sdk_runtime'), \
                 patch.dict('os.environ',{'TIANYI_API_KEY':'test-only'},clear=True), \
                 patch('glm_desktop.time.sleep'),redirect_stdout(io.StringIO()) as output:
                self.assertEqual(recap_glm.main(),2)
            record=json.loads(output.getvalue())['record']
            final=record['navigation_diagnostics'][-1]
            self.assertEqual((final['stage'],final['model_recoveries'],final['reason']),
                ('search_menu',2,'visual_worker_connection_error'))
            self.assertIsNone(record['normal_path_llm_calls'])
            self.assertEqual(record['reason'],'visual_worker_connection_error')


if __name__=='__main__': unittest.main()
