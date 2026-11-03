"""离线替身验证；不启动MCP、模型或桌面。"""
import io
from contextlib import redirect_stdout
from contextlib import nullcontext
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'fixed-recap-prototype'))
sys.path.insert(0, str(ROOT / 'glm-recap-prototype'))
from core import RecapError
from flow import Runner, Store, lock
from transport import MCP


class FailureFinalizationTests(unittest.TestCase):
    def admin_fixture(self,folder):
        task='1749b7977a99471db445c195bb8efe70'; session='d2407d04df6e45799d1f36795aac31af'
        start='2026-11-03T11:48:41.202482+08:00'
        store=Store(folder); store.create(task,'synthetic',session,'android_app')
        store.update(task,status='needs_input',phase='binding',reason='visual_address_unconfirmed',start_signal_received_at=start)
        store.stop_signal(task,'android_app')
        proof={'task_id':task,'session_id':session,'status':'needs_input','start_signal_source':'android_app',
               'stop_signal_source':'android_app','created_at_ms':1793677720507,'stopped_at_ms':1793677931394,
               'received_at_ms':1793677932615,'result_exists':True,'account_matches':True,'active_claimed_count':0}
        return store,task,start,proof

    def test_admin_release_is_single_task_audited_idempotent_and_preserves_result(self):
        with tempfile.TemporaryDirectory() as folder:
            store,task,start,proof=self.admin_fixture(folder); before=store.get(task)
            self.assertEqual(store.admin_abandon_verified(start,lambda:proof,lambda:True,nullcontext),'recovered')
            after=store.get(task)
            for key in ('status','reason','metrics','binding_evidence','stop_signal_received'):
                self.assertEqual(before[key],after[key])
            self.assertEqual(after['events'][-1]['historical_cleanup'],'unknown')
            self.assertNotIn('mcp_closed_normally',after['events'][-1])
            self.assertEqual(store.admin_abandon_verified(start,lambda:proof,lambda:True,nullcontext),'already_recovered')
            self.assertEqual(len(after['events']),1)
            self.assertTrue(store.create('second','synthetic','second-session','android_app')[1])
            self.assertEqual(store.get(task),after)

    def test_admin_release_rejects_remote_owner_source_and_index_conflicts(self):
        for variant in ('remote','owner','source','active','created','binding','stop','other_claimed','account'):
            with self.subTest(variant=variant),tempfile.TemporaryDirectory() as folder:
                store,task,start,proof=self.admin_fixture(folder)
                if variant=='remote': proof['received_at_ms']=0
                if variant=='other_claimed': proof['active_claimed_count']=1
                if variant=='account': proof['account_matches']=False
                if variant=='source': store.update(task,stop_signal_source='ios_app')
                if variant=='binding': store.update(task,binding_evidence={'room_id':'synthetic'})
                if variant=='stop': store.update(task,stop_signal_received=False)
                if variant=='active':
                    from flow import atomic_json
                    atomic_json(store.root/'active.json',{'task':'other'})
                if variant=='created': start='different'
                before=store.path(task).read_bytes()
                with self.assertRaises(RecapError):
                    store.admin_abandon_verified(start,lambda:proof,lambda:variant!='owner',nullcontext)
                self.assertEqual(store.path(task).read_bytes(),before)

    def test_admin_release_recovers_after_atomic_index_write_failure(self):
        import flow
        with tempfile.TemporaryDirectory() as folder:
            store,task,start,proof=self.admin_fixture(folder)
            actual=flow.atomic_json
            def interrupted(path,value):
                if path.name=='active.json': raise OSError('synthetic')
                return actual(path,value)
            with patch('flow.atomic_json',side_effect=interrupted):
                with self.assertRaises(OSError):
                    store.admin_abandon_verified(start,lambda:proof,lambda:True,nullcontext)
            self.assertEqual(json.loads((store.root/'active.json').read_text(encoding='utf-8'))['task'],task)
            self.assertEqual(len(store.get(task)['events']),1)
            self.assertEqual(store.admin_abandon_verified(start,lambda:proof,lambda:True,nullcontext),'recovered')
            self.assertEqual(len(store.get(task)['events']),1)

    def test_admin_remote_and_owner_checks_run_inside_all_locks(self):
        with tempfile.TemporaryDirectory() as folder:
            store,task,start,proof=self.admin_fixture(folder)
            global_path=store.root/'synthetic-global.lock'
            def verify_locks():
                self.assertTrue(global_path.exists())
                self.assertTrue((store.root/'desktop.lock').exists())
                self.assertTrue((store.root/'state.lock').exists())
            def owners(): verify_locks(); return True
            def remote(): verify_locks(); return proof
            self.assertEqual(store.admin_abandon_verified(start,remote,owners,lambda:lock(global_path)),'recovered')
            self.assertEqual(len(store.get(task)['events']),1)

    def test_admin_release_rejects_held_global_or_desktop_lock(self):
        with tempfile.TemporaryDirectory() as folder:
            store,task,start,proof=self.admin_fixture(folder)
            global_path=store.root/'synthetic-global.lock'
            with lock(global_path):
                with self.assertRaisesRegex(RecapError,'busy'):
                    store.admin_abandon_verified(start,lambda:proof,lambda:True,lambda:lock(global_path))
            with lock(store.root/'desktop.lock'):
                with self.assertRaisesRegex(RecapError,'busy'):
                    store.admin_abandon_verified(start,lambda:proof,lambda:True,nullcontext)
            self.assertEqual(store.get(task)['events'],[])
    def setup_task(self, folder, reason='edge_launch_unconfirmed'):
        store = Store(folder)
        store.create('first', 'demo_account', 'first-session', 'android_app')
        runner = Runner(store, None)
        runner._fail('first', reason)
        return store, runner

    def assert_blocked(self, store):
        with self.assertRaisesRegex(RecapError, 'busy'):
            store.create('second', 'demo_account', 'second-session', 'android_app')

    def test_stopped_known_failure_only_releases_after_confirmed_cleanup(self):
        with tempfile.TemporaryDirectory() as folder:
            store, runner = self.setup_task(folder)
            original = store.get('first')
            store.stop_signal('first', 'android_app')
            self.assert_blocked(store)
            closed = runner.finish_unbound_execution('first', cleanup_confirmed=True)
            self.assertEqual(closed['status'], 'failed')
            for key in ('reason', 'local_test_id', 'session_id', 'douyin_account', 'metrics', 'last_failure_at'):
                self.assertEqual(closed[key], original[key])
            self.assertEqual(closed['stop_signal_source'], 'android_app')
            old_bytes = store.path('first').read_bytes()
            desktop = Mock()
            desktop.bind.return_value = {'douyin_account': 'demo_account', 'room_id': 'synthetic-room'}
            result = Runner(store, desktop).start('second', 'demo_account', 'second-session', 'android_app')
            self.assertEqual(result['status'], 'waiting_for_stop')
            self.assertEqual(store.path('first').read_bytes(), old_bytes)

    def test_late_stop_finalizes_and_duplicate_stop_is_byte_identical(self):
        with tempfile.TemporaryDirectory() as folder:
            store, runner = self.setup_task(folder)
            runner.finish_unbound_execution('first', cleanup_confirmed=True)
            self.assertEqual(store.get('first')['status'], 'needs_input')
            self.assert_blocked(store)
            self.assertEqual(runner.stop('first', 'android_app')['status'], 'failed')
            before = store.path('first').read_bytes()
            self.assertEqual(runner.stop('first', 'android_app')['status'], 'failed')
            self.assertEqual(store.path('first').read_bytes(), before)

    def test_closed_visual_failure_releases_only_after_same_source_stop(self):
        with tempfile.TemporaryDirectory() as folder:
            store=Store(folder); desktop=Mock()
            desktop.bind.side_effect=RecapError('visual_address_unconfirmed')
            runner=Runner(store,desktop)
            runner.start('first','demo_account','first-session','android_app')
            runner.finish_unbound_execution('first',cleanup_confirmed=True)
            self.assert_blocked(store)
            stopped=runner.stop('first','android_app')
            self.assertEqual(stopped['status'],'needs_input')
            self.assertEqual(stopped['reason'],'visual_address_unconfirmed')
            self.assertIsNone(stopped['metrics'])
            before=store.path('first').read_bytes()
            self.assertEqual(runner.stop('first','android_app')['status'],'needs_input')
            self.assertEqual(store.path('first').read_bytes(),before)
            self.assertTrue(store.create('second','demo_account','second-session','android_app')[1])

    def test_failed_binding_needs_same_source_stop_and_confirmed_close(self):
        with tempfile.TemporaryDirectory() as folder:
            store=Store(folder); desktop=Mock()
            desktop.bind.side_effect=RecapError('not_live')
            runner=Runner(store,desktop)
            self.assertEqual(runner.start('first','demo_account','first-session','android_app')['status'],'failed')
            self.assert_blocked(store)
            runner.finish_unbound_execution('first',cleanup_confirmed=True)
            self.assert_blocked(store)
            runner.stop('first','android_app')
            self.assertTrue(store.create('second','demo_account','second-session','android_app')[1])

    def test_unconfirmed_cleanup_and_still_running_are_not_released(self):
        for close in (None, False):
            with self.subTest(close=close), tempfile.TemporaryDirectory() as folder:
                store, runner = self.setup_task(folder)
                store.stop_signal('first', 'android_app')
                if close is not None:
                    runner.finish_unbound_execution('first', cleanup_confirmed=close)
                self.assertEqual(runner.stop('first', 'android_app')['status'], 'needs_input')
                self.assert_blocked(store)

    def test_owner_lock_blocks_even_with_a_prior_cleanup_receipt(self):
        with tempfile.TemporaryDirectory() as folder:
            store, runner = self.setup_task(folder)
            runner.finish_unbound_execution('first', cleanup_confirmed=True)
            with lock(store.root / 'desktop.lock'):
                self.assertEqual(runner.stop('first', 'android_app')['status'], 'needs_input')
                self.assert_blocked(store)
            self.assertEqual(runner.stop('first', 'android_app')['status'], 'failed')

    def test_receipt_creation_identity_mismatch_cannot_finalize(self):
        with tempfile.TemporaryDirectory() as folder:
            store,runner=self.setup_task(folder)
            runner.finish_unbound_execution('first',cleanup_confirmed=True)
            record=store.get('first'); events=record['events']
            events[-1]['start_signal_received_at']='different'
            store.update('first',events=events)
            store.stop_signal('first','android_app')
            self.assertEqual(runner.finalize_stopped_unbound('first')['status'],'needs_input')
            self.assert_blocked(store)

    def test_stop_before_close_and_lock_conflict_preserve_old_record(self):
        with tempfile.TemporaryDirectory() as folder:
            store=Store(folder); desktop=Mock()
            desktop.bind.side_effect=RecapError('visual_address_unconfirmed')
            runner=Runner(store,desktop)
            runner.start('first','demo_account','first-session','android_app')
            runner.stop('first','android_app')
            self.assert_blocked(store)
            with lock(store.root/'desktop.lock'):
                self.assertEqual(runner.finish_unbound_execution('first',cleanup_confirmed=True)['events'],[])
            self.assert_blocked(store)
            runner.finish_unbound_execution('first',cleanup_confirmed=True)
            before=store.path('first').read_bytes()
            self.assertTrue(store.create('second','demo_account','second-session','android_app')[1])
            self.assertEqual(store.path('first').read_bytes(),before)

    def test_wrong_active_task_cannot_finalize_or_replace_index(self):
        with tempfile.TemporaryDirectory() as folder:
            store,runner=self.setup_task(folder)
            runner.finish_unbound_execution('first',cleanup_confirmed=True)
            store.stop_signal('first','android_app')
            from flow import atomic_json
            atomic_json(store.root/'active.json',{'task':'different'})
            self.assertEqual(runner.finalize_stopped_unbound('first')['status'],'needs_input')
            self.assertEqual(json.loads((store.root/'active.json').read_text(encoding='utf-8'))['task'],'different')

    def test_bound_unknown_unlisted_reason_wrong_source_and_foreign_receipt_stay_blocked(self):
        for variant in ('bound', 'unknown', 'unlisted', 'source', 'foreign_receipt'):
            with self.subTest(variant=variant), tempfile.TemporaryDirectory() as folder:
                reason = {'unknown': 'mcp_timeout_outcome_unknown', 'unlisted': 'agent_worker_no_receipt'}.get(variant, 'edge_launch_unconfirmed')
                store, runner = self.setup_task(folder, reason)
                if variant == 'bound':
                    store.update('first', binding_evidence={'room_id': 'kept'})
                runner.finish_unbound_execution('first', cleanup_confirmed=True)
                if variant == 'foreign_receipt':
                    events = store.get('first')['events']
                    events[-1]['session_id'] = 'other-session'
                    store.update('first', events=events)
                store.stop_signal('first', 'ios_app' if variant == 'source' else 'android_app')
                # 只核对无绑定收口，禁止本测试触发任何capture动作。
                self.assertEqual(runner.finalize_stopped_unbound('first')['status'], 'needs_input')
                self.assert_blocked(store)

    def test_real_cli_stop_status_result_use_persisted_fake_cleanup_evidence(self):
        with tempfile.TemporaryDirectory() as folder:
            store, runner = self.setup_task(folder)
            runner.finish_unbound_execution('first', cleanup_confirmed=True)
            for command, expected in (('stop', 2), ('stop', 2), ('status', 0), ('result', 0), ('start', 2)):
                args = [sys.executable, str(ROOT / 'glm-recap-prototype/recap_glm.py'), command,
                        '--task', 'first', '--source', 'android_app', '--data', folder,
                        '--desktop-authorized']
                if command == 'start': args += ['--account', 'demo_account', '--session-id', 'first-session']
                result = subprocess.run(args, capture_output=True, text=True, encoding='utf-8', timeout=10)
                self.assertEqual(result.returncode, expected, result.stderr)
                record = json.loads(result.stdout)['record']
                self.assertEqual(record['status'], 'failed')
                self.assertEqual(record['reason'], 'edge_launch_unconfirmed')
                self.assertIsNone(record['metrics'])
            before = store.path('first').read_bytes()
            args[args.index('android_app')] = 'ios_app'
            result = subprocess.run(args, capture_output=True, text=True, encoding='utf-8', timeout=10)
            self.assertEqual(result.returncode, 2)
            self.assertNotIn('record', json.loads(result.stdout))
            self.assertEqual(store.path('first').read_bytes(), before)

    def test_real_cli_busy_has_no_record_and_preserves_previous_task(self):
        with tempfile.TemporaryDirectory() as folder:
            store, runner = self.setup_task(folder)
            store.stop_signal('first', 'android_app')
            before = store.path('first').read_bytes()
            result = subprocess.run([sys.executable, str(ROOT / 'glm-recap-prototype/recap_glm.py'),
                'start', '--task', 'second', '--account', 'demo_account', '--session-id', 'second-session',
                '--source', 'android_app', '--data', folder, '--desktop-authorized'],
                capture_output=True, text=True, encoding='utf-8', timeout=10)
            self.assertEqual(result.returncode, 2)
            self.assertEqual(json.loads(result.stdout), {'status': 'needs_input', 'reason': 'busy'})
            self.assertFalse(store.path('second').exists())
            self.assertEqual(store.path('first').read_bytes(), before)

    def test_cli_finally_waits_for_close_and_consumes_early_stop_only_afterwards(self):
        import recap_glm
        for cleanup, calls, settled in ((True,0,True),(False,0,True),(True,1,True),(True,1,False)):
            with self.subTest(cleanup=cleanup,calls=calls,settled=settled), tempfile.TemporaryDirectory() as folder:
                store = Store(folder)
                runtime = SimpleNamespace(calls=calls, http_requests=0,all_workers_finished=settled)
                desktop = Mock()
                reason='visual_address_unconfirmed' if calls else 'edge_launch_unconfirmed'
                desktop.bind.side_effect = RecapError(reason)
                wire = Mock()
                def close():
                    before = Runner(store, None).stop('wired', 'android_app')
                    self.assertEqual(before['status'], 'needs_input')
                    self.assertEqual(before['events'], [])
                    with self.assertRaisesRegex(RecapError, 'busy'):
                        store.create('next', 'demo_account', 'next-session', 'android_app')
                    return cleanup
                wire.close.side_effect = close
                modules = {
                    'glm_vision': SimpleNamespace(GLMVision=Mock()),
                    'glm_desktop': SimpleNamespace(GLMDesktop=Mock(return_value=desktop)),
                    'window_probe': SimpleNamespace(Probe=Mock()),
                    'ocr_local': SimpleNamespace(LocalOCR=Mock()),
                    'transport': SimpleNamespace(MCP=Mock(return_value=wire)),
                }
                args = ['recap_glm.py', 'start', '--task', 'wired', '--account', 'demo_account',
                        '--session-id', 'wired-session', '--source', 'android_app', '--data', folder,
                        '--scratch', folder, '--desktop-authorized']
                with patch.object(sys, 'argv', args), patch.dict(sys.modules, modules), \
                     patch.object(recap_glm, 'require_gate_a'), \
                     patch.object(recap_glm, 'require_sdk_runtime', return_value=sys.executable), \
                     patch.object(recap_glm, 'GLMRuntime', return_value=runtime), \
                     patch.dict('os.environ', {'TIANYI_API_KEY': 'explicit-test-double'}, clear=True), \
                     redirect_stdout(io.StringIO()) as output:
                    self.assertEqual(recap_glm.main(), 2)
                record = json.loads(output.getvalue())['record']
                self.assertEqual(record['status'], 'failed' if cleanup and settled and not calls else 'needs_input')
                self.assertEqual(record['stop_signal_source'], 'android_app')
                self.assertIsNone(record['metrics'])
                if cleanup and settled:
                    self.assertTrue(store.create('next', 'demo_account', 'next-session', 'android_app')[1])
                else:
                    with self.assertRaisesRegex(RecapError, 'busy'):
                        store.create('next', 'demo_account', 'next-session', 'android_app')


class MCPClosureTests(unittest.TestCase):
    def client(self, folder):
        client = MCP(sys.executable, __file__, Path(folder) / 'mcp.jsonl')
        client.process = Mock(pid=123)
        client.process.stdin = io.StringIO()
        client.stderr = io.StringIO()
        return client

    def test_only_normal_exit_without_inflight_is_confirmed(self):
        for code, inflight, expected in ((0, False, True), (2, False, False), (0, True, False)):
            with self.subTest(code=code, inflight=inflight), tempfile.TemporaryDirectory() as folder:
                client = self.client(folder)
                client.process.wait.return_value = code
                client._call_inflight = inflight
                self.assertIs(client.close(), expected)
                self.assertIsNone(client.process)

    def test_forced_cleanup_never_proves_normal_completion(self):
        with tempfile.TemporaryDirectory() as folder:
            client = self.client(folder)
            client.process.wait.side_effect = subprocess.TimeoutExpired('fake', 10)
            with patch('transport.subprocess.run') as taskkill:
                self.assertIs(client.close(), False)
                self.assertIs(client.close(), False)
            taskkill.assert_called_once()

    def test_unanswered_tool_stays_unknown_even_if_client_later_exits_zero(self):
        with tempfile.TemporaryDirectory() as folder:
            client = self.client(folder)
            client.open = Mock()
            client._receive = Mock(side_effect=RecapError('mcp_timeout_outcome_unknown'))
            client.process.wait.return_value = 0
            with self.assertRaisesRegex(RecapError, 'mcp_timeout_outcome_unknown'):
                client.call('App', mode='launch', name='Microsoft Edge')
            self.assertTrue(client._call_inflight)
            self.assertIs(client.close(), False)

    def test_app_error_is_recorded_without_raw_reply(self):
        with tempfile.TemporaryDirectory() as folder:
            client = self.client(folder)
            client.open = Mock()
            client._receive = Mock(return_value={'is_error': True, 'content': [{'text': 'Failed to launch fake Edge'}]})
            with self.assertRaisesRegex(RecapError, 'mcp_tool_error'):
                client.call('App', mode='launch', name='Microsoft Edge')
            event = json.loads(client.trace.read_text(encoding='utf-8').splitlines()[-1])
            self.assertTrue(event['is_error'])
            self.assertNotIn('Failed to launch fake Edge', client.trace.read_text(encoding='utf-8'))
            self.assertFalse(client._call_inflight)


if __name__ == '__main__':
    unittest.main()
