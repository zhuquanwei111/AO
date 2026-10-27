"""离线替身验证；不启动MCP、模型或桌面。"""
import io
from contextlib import redirect_stdout
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
        for cleanup in (True, False):
            with self.subTest(cleanup=cleanup), tempfile.TemporaryDirectory() as folder:
                store = Store(folder)
                runtime = SimpleNamespace(calls=0, http_requests=0)
                desktop = Mock()
                desktop.bind.side_effect = RecapError('edge_launch_unconfirmed')
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
                self.assertEqual(record['status'], 'failed' if cleanup else 'needs_input')
                self.assertEqual(record['stop_signal_source'], 'android_app')
                self.assertIsNone(record['metrics'])
                if cleanup:
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

    def test_app_error_actual_reply_is_recorded_before_error_check(self):
        with tempfile.TemporaryDirectory() as folder:
            client = self.client(folder)
            client.open = Mock()
            client._receive = Mock(return_value={'is_error': True, 'content': [{'text': 'Failed to launch fake Edge'}]})
            with self.assertRaisesRegex(RecapError, 'mcp_tool_error'):
                client.call('App', mode='launch', name='Microsoft Edge')
            event = json.loads(client.trace.read_text(encoding='utf-8').splitlines()[-1])
            self.assertEqual(event['app_result_text'], 'Failed to launch fake Edge')
            self.assertFalse(client._call_inflight)


if __name__ == '__main__':
    unittest.main()
