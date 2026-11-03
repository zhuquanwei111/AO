import io
import asyncio
from contextlib import redirect_stdout
import json
import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from PIL import Image
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'fixed-recap-prototype'))
from core import RecapError, Token
from desktop import View
from glm_vision import make_local_crop, make_address_crop, address_seed_candidates, GLMVision
from glm_runtime import GLMRuntime


class MemoryVisualTests(unittest.TestCase):
    def view(self, tokens):
        image = io.BytesIO()
        Image.new('RGB', (800, 600), 'white').save(image, 'PNG')
        return View(tokens, image.getvalue(), {'handle': 1, 'pid': 2, 'process_created': 3, 'title': 'Target', 'rect': [0, 0, 800, 600],
                    'window_class':'Chrome_WidgetWin_1','owner':0,'root_owner':1,'visible':True,'enabled':True,'minimized':False},
                    (800, 600), 'fixture-time', [])

    def test_address_seed_requires_unique_named_edit_and_untruncated_target_window(self):
        tree = 'UI Tree:\ndesktop\n└── window "Target"\n    └── (350,50) edit "地址和搜索栏" [focused]'
        seed = address_seed_candidates(tree, 'Target', [0, 0, 800, 600])
        self.assertEqual(len(seed), 1)
        for bad in (tree.replace(' edit ', ' button '), tree.replace('Target', 'Other'),
                    tree + '\n... [truncated: reached the 500-element capture limit — incomplete]',
                    tree + '\n    └── (400,50) edit "地址和搜索栏" [focused]',
                    tree.replace('(350,50)', '(900,50)')):
            with self.assertRaises(RecapError): address_seed_candidates(bad, 'Target', [0, 0, 800, 600])

    def test_seed_is_only_a_crop_not_an_action_without_visual_confirmation(self):
        tree = 'UI Tree:\ndesktop\n└── window "Target"\n    └── (350,50) edit "地址和搜索栏" [focused]'
        seed = address_seed_candidates(tree, 'Target', [0, 0, 800, 600])[0]
        view = self.view([])
        crop = make_address_crop(view, seed, lambda window: window)
        vision = GLMVision(Mock())
        vision.runtime.run_crop.return_value = {'status': 'found', 'kind': 'address_bar', 'box': [10, 10, 20, 20]}
        target = vision.locate(view, 'address', seed=seed, guard=lambda window: window)
        self.assertNotEqual(view.point(target), [350, 50])
        self.assertGreater(len(crop.image), 0)
        vision.runtime.run_crop.return_value = {'status': 'found', 'kind': 'button', 'box': [10, 10, 20, 20]}
        with self.assertRaises(RecapError): vision.locate(view, 'address', seed=seed, guard=lambda window: window)

    def test_product_navigation_requires_visual_confirmation_before_type(self):
        from glm_desktop import GLMDesktop
        tree = 'UI Tree:\ndesktop\n└── window "Target"\n    └── (350,50) edit "地址和搜索栏" [focused]'
        view = self.view([])
        view.address_seeds = tuple(address_seed_candidates(tree, 'Target', view.window['rect']))
        driver = object.__new__(GLMDesktop)
        driver.guard = lambda window: window
        driver.mcp = Mock()
        driver.probe = Mock(); driver.probe.windows.return_value = [view.window]
        driver.key = Mock(); driver.observe = Mock(return_value=view)
        driver.type_replacement = Mock(); driver.wait_entry = Mock(return_value='confirmed')
        driver.portal_url = 'https://union.bytedance.com/open/portal/index'
        driver.vision = GLMVision(Mock())
        driver.vision.runtime.run_crop.return_value = {'status': 'found', 'kind': 'address_bar', 'box': [10, 10, 20, 20]}
        self.assertEqual(driver.navigate_entry(view.window), 'confirmed')
        used = driver.type_replacement.call_args.args[1]
        self.assertEqual(used.text, 'confirmed_address_bar')
        self.assertNotEqual(view.point(used), [350, 50])
        driver.type_replacement.reset_mock()
        driver.vision.runtime.run_crop.return_value = {'status': 'found', 'kind': 'button', 'box': [10, 10, 20, 20]}
        with self.assertRaises(RecapError): driver.navigate_entry(view.window)
        driver.type_replacement.assert_not_called()
        driver.vision.runtime.run_crop.return_value = {'status': 'found', 'kind': 'address_bar', 'box': [0, 0, 1000, 1000]}
        with self.assertRaises(RecapError): driver.navigate_entry(view.window)
        driver.type_replacement.assert_not_called()
        driver.vision.runtime.run_crop.reset_mock()
        view.address_seeds = ()
        view.address_seed_error = 'address_seed_unavailable'
        driver.vision.runtime.run_crop.return_value = {'status': 'found', 'kind': 'address_bar', 'box': [10, 10, 20, 20]}
        self.assertEqual(driver.navigate_entry(view.window), 'confirmed')
        driver.vision.runtime.run_crop.assert_called_once()
        driver.type_replacement.assert_called_once()

    def test_direct_window_vision_blocks_login_and_changed_window(self):
        vision = GLMVision(Mock())
        view = self.view([Token('验证码', (1, 1, 30, 20))])
        with self.assertRaisesRegex(RecapError, 'needs_login_or_verification'):
            vision.locate(view, '地址栏', guard=lambda window: window)
        vision.runtime.run_crop.assert_not_called()
        view = self.view([])
        with self.assertRaisesRegex(RecapError, 'visual_crop_window_changed'):
            vision.locate(view, '地址栏', guard=lambda window: {**window, 'rect': [1, 0, 800, 600]})
        vision.runtime.run_crop.assert_not_called()
        vision.runtime.run_crop.return_value = {'status': 'blocked', 'kind': None, 'box': None}
        with self.assertRaisesRegex(RecapError, 'visual_blocked'):
            vision.locate(view, '地址栏', guard=lambda window: window)

    def test_metric_without_authorized_crop_is_rejected(self):
        view = self.view([])
        runtime = Mock()
        vision = GLMVision(runtime)
        with self.assertRaisesRegex(RecapError, 'visual_crop_anchor_invalid'):
            vision.read(view, ('累计观众',), '读取', guard=lambda w: w, account='123456')
        runtime.run_crop.assert_not_called()

    def test_direct_navigation_target_requires_matching_kind(self):
        view = self.view([])
        runtime = Mock()
        vision = GLMVision(runtime)
        runtime.run_crop.return_value = {'status': 'found', 'kind': 'target', 'box': [30, 40, 70, 60]}
        token = vision.locate(view, '主播管理', guard=lambda w: w)
        self.assertEqual(token.box, (30, 40, 70, 60))
        runtime.run_crop.return_value = {'status': 'found', 'kind': 'address_bar', 'box': [30, 40, 70, 60]}
        with self.assertRaises(RecapError):
            vision.locate(view, '主播管理', guard=lambda w: w)

    def test_visual_identity_requires_exact_account_and_confirmed_live(self):
        view = self.view([Token('抖音号:123456',(100,80,230,100)),Token('已开播00:01',(300,80,400,100))]); runtime = Mock(); vision = GLMVision(runtime)
        runtime.run_crop.return_value = {'account': '123456', 'live_status': '已开播00:01'}
        vision.verify_identity(view, '123456', lambda w: w, live=True)
        for value in ({'account': '123457', 'live_status': '已开播00:01'},
                      {'account': None, 'live_status': None}):
            runtime.run_crop.return_value = value
            with self.assertRaises(RecapError):
                vision.verify_identity(view, '123456', lambda w: w, live=True)
        runtime.run_crop.return_value = {'account': '123456', 'live_status': None}
        view = self.view([Token('抖音号:123456',(100,80,230,100))])
        with self.assertRaisesRegex(RecapError, 'live_status_unconfirmed'):
            vision.verify_identity(view, '123456', lambda w: w, live=True)

    def test_pre_live_identity_never_sends_full_window_or_other_account(self):
        runtime = Mock(); vision = GLMVision(runtime)
        view = self.view([])
        with self.assertRaises(RecapError):
            vision.verify_identity(view, '123456', lambda w: w)
        runtime.run_crop.assert_not_called()
        view = self.view([Token('抖音号:123456',(100,80,230,100)),
                          Token('抖音号:654321',(400,80,530,100))])
        runtime.run_crop.return_value = {'account': '123456', 'live_status': None}
        vision.verify_identity(view, '123456', lambda w: w)
        crop = runtime.run_crop.call_args.args[0]
        self.assertLess((crop.box[2]-crop.box[0])*(crop.box[3]-crop.box[1]), 800*600)
        self.assertLess(crop.box[2], 400)

    def test_product_bind_reaches_guarded_navigation_and_stops_at_fixture_boundary(self):
        from glm_desktop import GLMDesktop
        tree = 'UI Tree:\ndesktop\n└── window "Target"\n    └── (350,50) edit "地址和搜索栏" [focused]'
        view = self.view([])
        view.address_seeds = tuple(address_seed_candidates(tree, 'Target', view.window['rect']))
        driver = object.__new__(GLMDesktop)
        driver.guard = lambda window=None: window
        driver.mcp = Mock()
        driver.probe = Mock(); driver.probe.windows.return_value = [view.window]
        driver.focus = Mock(return_value=view.window)
        driver.key = Mock(); driver.observe = Mock(return_value=view)
        driver.type_replacement = Mock(); driver.wait_entry = Mock(side_effect=RecapError('fixture_stop'))
        driver.portal_url = 'https://union.bytedance.com/open/portal/index'
        driver.vision = GLMVision(Mock())
        driver.vision.runtime.run_crop.return_value = {'status': 'found', 'kind': 'address_bar', 'box': [10, 10, 20, 20]}
        with self.assertRaisesRegex(RecapError, 'fixture_stop'):
            driver.bind('fixture-account')
        driver.type_replacement.assert_called_once()
        driver.vision.runtime.run_crop.assert_called_once()

    def test_bind_targets_portal_with_unknown_background(self):
        from glm_desktop import GLMDesktop
        driver=object.__new__(GLMDesktop)
        driver.guard=Mock(); driver.mcp=Mock(); driver.probe=Mock()
        portal={**self.view([]).window,'title':'直播服务平台'}
        driver.probe.windows.return_value=[portal,{'handle':2,'title':''}]
        driver.focus=Mock(return_value=portal); driver.wait_entry=Mock(side_effect=RecapError('fixture_stop'))
        with self.assertRaisesRegex(RecapError,'fixture_stop'):
            driver.bind('demo')
        driver.focus.assert_called_once_with(portal)

    def test_bind_rejects_two_main_windows_and_accepts_one_portal(self):
        from glm_desktop import GLMDesktop
        driver=object.__new__(GLMDesktop)
        driver.guard=Mock(); driver.mcp=Mock(); driver.probe=Mock()
        portal={**self.view([]).window,'title':'直播服务平台'}
        second={**portal,'handle':2,'root_owner':2,'pid':3,'title':'直播服务平台'}
        driver.probe.windows.return_value=[portal,second]
        driver.focus=Mock(return_value=portal)
        with self.assertRaisesRegex(RecapError,'edge_window_ambiguous'):
            driver.bind('demo')
        driver.focus.assert_not_called()
        driver.probe.windows.return_value=[portal]
        driver.wait_entry=Mock(side_effect=RecapError('fixture_stop'))
        with self.assertRaisesRegex(RecapError,'fixture_stop'):
            driver.bind('demo')
        driver.focus.assert_called_once_with(portal)

    def test_only_current_unique_authorized_anchor_can_form_crop(self):
        anchor = Token('主播列表', (100, 80, 160, 100))
        view = self.view([anchor])
        crop = make_local_crop(view, anchor, 'navigation', lambda window: window)
        self.assertLess(len(crop.image), len(view.image))
        for invalid in (self.view([]), self.view([anchor, anchor]),
                        self.view([anchor, Token('主播列表', (200, 80, 260, 100))]),
                        self.view([anchor, Token('验证码', (5, 5, 40, 20))]),
                        self.view([anchor, Token('其他主播', (80, 80, 95, 95))])):
            with self.assertRaises(RecapError):
                make_local_crop(invalid, anchor, 'navigation', lambda window: window)
        with self.assertRaises(RecapError):
            make_local_crop(view, anchor, 'metrics', lambda window: window)

    def test_runtime_serializes_crop_in_memory_without_image_files_or_logs(self):
        anchor = Token('主播列表', (100, 80, 160, 100))
        crop = make_local_crop(self.view([anchor]), anchor, 'navigation', lambda window: window)
        result = {'status': 'OK', 'result': {'status': 'missing', 'box': None}, 'http_requests': 1}
        with tempfile.TemporaryDirectory() as folder, \
             patch.dict('os.environ', {'TIANYI_API_KEY': 'fake', 'BACKSTAGE_SDK_PYTHON': __import__('sys').executable}), \
             patch('glm_runtime.require_gate_a'), \
             patch('subprocess.run', return_value=subprocess.CompletedProcess([], 0, json.dumps(result).encode(), b'')) as run:
            runtime = GLMRuntime(folder, gate='fixture-gate')
            self.assertEqual(runtime.run_crop(crop, 'fixture'), result['result'])
            data = json.loads(run.call_args.kwargs['input'])
            self.assertTrue(data['image_base64'])
            self.assertNotIn('fake', str(run.call_args.args))
            self.assertEqual(list(Path(folder).rglob('*')), [])

    def test_unvalidated_and_timeout_never_write_image(self):
        anchor = Token('主播列表', (100, 80, 160, 100))
        crop = make_local_crop(self.view([anchor]), anchor, 'navigation', lambda window: window)
        with tempfile.TemporaryDirectory() as folder, \
             patch.dict('os.environ', {'TIANYI_API_KEY': 'fake', 'BACKSTAGE_SDK_PYTHON': __import__('sys').executable}), \
             patch('glm_runtime.require_gate_a'), \
             patch('subprocess.run', side_effect=subprocess.TimeoutExpired('worker', 1)) as run:
            runtime = GLMRuntime(folder, gate='fixture-gate')
            with self.assertRaises(RuntimeError): runtime.run_crop(b'not a crop', 'fixture')
            run.assert_not_called()
            with self.assertRaises(RuntimeError): runtime.run_crop(crop, 'fixture')
            self.assertEqual(list(Path(folder).rglob('*')), [])

    def test_missing_gate_rejects_before_starting_worker(self):
        anchor = Token('主播列表', (100, 80, 160, 100))
        crop = make_local_crop(self.view([anchor]), anchor, 'navigation', lambda window: window)
        with tempfile.TemporaryDirectory() as folder, \
             patch.dict('os.environ', {'TIANYI_API_KEY': 'fake', 'BACKSTAGE_SDK_PYTHON': sys.executable}), \
             patch('subprocess.run') as run:
            with self.assertRaisesRegex(RuntimeError, 'gate_a_not_verified'):
                GLMRuntime(folder).run_crop(crop, 'fixture')
            run.assert_not_called()

    def test_worker_failure_makes_request_count_unknown(self):
        anchor = Token('主播列表', (100, 80, 160, 100))
        crop = make_local_crop(self.view([anchor]), anchor, 'navigation', lambda window: window)
        replies = [subprocess.CompletedProcess([], 2, b'{"status":"FAIL"}', b''),
                   subprocess.CompletedProcess([], 0, b'x' * 16385, b''),
                   subprocess.CompletedProcess([], 0, b'not-json', b''),
                   subprocess.CompletedProcess([], 0, b'{"status":"FAIL"}', b''),
                   subprocess.CompletedProcess([], 0, b'{"status":"OK","result":{},"http_requests":0}', b'')]
        with tempfile.TemporaryDirectory() as folder, \
             patch.dict('os.environ', {'TIANYI_API_KEY': 'fake', 'BACKSTAGE_SDK_PYTHON': sys.executable}), \
             patch('glm_runtime.require_gate_a'):
            for reply in replies:
                runtime = GLMRuntime(folder, gate='fixture-gate')
                with patch('subprocess.run', return_value=reply), self.assertRaises(RuntimeError):
                    runtime.run_crop(crop, 'fixture')
                self.assertIsNone(runtime.http_requests)

    def test_cli_finalization_persists_unknown_request_count(self):
        import recap_glm
        anchor = Token('主播列表', (100, 80, 160, 100))
        crop = make_local_crop(self.view([anchor]), anchor, 'navigation', lambda window: window)
        with tempfile.TemporaryDirectory() as folder:
            runtime = GLMRuntime(folder, gate='fixture-gate')
            desktop = Mock()
            desktop.bind.side_effect = lambda account: runtime.run_crop(crop, 'fixture')
            wire = Mock(); wire.close.return_value = True
            modules = {'glm_desktop': types.SimpleNamespace(GLMDesktop=Mock(return_value=desktop)),
                       'window_probe': types.SimpleNamespace(Probe=Mock()),
                       'ocr_local': types.SimpleNamespace(LocalOCR=Mock()),
                       'transport': types.SimpleNamespace(MCP=Mock(return_value=wire))}
            args = ['recap_glm.py', 'start', '--task', 'task', '--account', 'demo',
                    '--session-id', 'session', '--source', 'android_app', '--data', folder,
                    '--scratch', folder, '--desktop-authorized']
            failure = subprocess.CompletedProcess([], 2, b'{"status":"FAIL"}', b'')
            with patch.object(sys, 'argv', args), patch.dict(sys.modules, modules), \
                 patch.object(recap_glm, 'GLMRuntime', return_value=runtime), \
                 patch.object(recap_glm, 'require_gate_a'), patch.object(recap_glm, 'require_sdk_runtime'), \
                 patch.dict('os.environ', {'TIANYI_API_KEY': 'fake', 'BACKSTAGE_SDK_PYTHON': sys.executable}), \
                 patch('glm_runtime.require_gate_a'), patch('subprocess.run', return_value=failure), \
                 redirect_stdout(io.StringIO()) as output:
                self.assertEqual(recap_glm.main(), 2)
            self.assertIsNone(json.loads(output.getvalue())['record']['normal_path_llm_calls'])

    def test_cli_checks_visual_configuration_before_desktop_start(self):
        import recap_glm
        with tempfile.TemporaryDirectory() as folder:
            wire = Mock(); wire.close.return_value = True
            desktop_constructor = Mock()
            modules = {'glm_desktop': types.SimpleNamespace(GLMDesktop=desktop_constructor),
                       'window_probe': types.SimpleNamespace(Probe=Mock()),
                       'ocr_local': types.SimpleNamespace(LocalOCR=Mock()),
                       'transport': types.SimpleNamespace(MCP=Mock(return_value=wire))}
            args = ['recap_glm.py', 'start', '--task', 'task', '--account', 'demo',
                    '--session-id', 'session', '--source', 'android_app', '--data', folder,
                    '--scratch', folder, '--desktop-authorized']
            with patch.object(sys, 'argv', args), patch.dict(sys.modules, modules), \
                 patch.object(recap_glm, 'require_gate_a', side_effect=ValueError('private fixture')), \
                 patch.dict('os.environ', {'TIANYI_API_KEY': 'fake'}, clear=True), \
                 redirect_stdout(io.StringIO()) as output:
                self.assertEqual(recap_glm.main(), 2)
            record = json.loads(output.getvalue())['record']
            self.assertEqual(record['reason'], 'visual_runtime_unavailable')
            self.assertNotIn('private fixture', output.getvalue())
            desktop_constructor.assert_not_called()

    def test_cli_rejects_missing_key_or_sdk_before_desktop_start(self):
        import recap_glm
        for env, sdk_error in (({}, None), ({'TIANYI_API_KEY': 'fake'}, ValueError('sdk missing'))):
            with self.subTest(sdk_error=sdk_error), tempfile.TemporaryDirectory() as folder:
                wire = Mock(); wire.close.return_value = True
                desktop_constructor = Mock()
                modules = {'glm_desktop': types.SimpleNamespace(GLMDesktop=desktop_constructor),
                           'window_probe': types.SimpleNamespace(Probe=Mock()),
                           'ocr_local': types.SimpleNamespace(LocalOCR=Mock()),
                           'transport': types.SimpleNamespace(MCP=Mock(return_value=wire))}
                args = ['recap_glm.py', 'start', '--task', 'task', '--account', 'demo',
                        '--session-id', 'session', '--source', 'android_app', '--data', folder,
                        '--scratch', folder, '--desktop-authorized']
                with patch.object(sys, 'argv', args), patch.dict(sys.modules, modules), \
                     patch.object(recap_glm, 'require_gate_a'), \
                     patch.object(recap_glm, 'require_sdk_runtime', side_effect=sdk_error), \
                     patch.dict('os.environ', env, clear=True), redirect_stdout(io.StringIO()) as output:
                    self.assertEqual(recap_glm.main(), 2)
                self.assertEqual(json.loads(output.getvalue())['record']['reason'], 'visual_runtime_unavailable')
                desktop_constructor.assert_not_called()

    def test_moved_window_rejects_crop_before_worker(self):
        anchor = Token('主播列表', (100, 80, 160, 100))
        view = self.view([anchor])
        guard = Mock(side_effect=[view.window, view.window,
                                 {**view.window, 'rect': [2, 0, 802, 600]}])
        crop = make_local_crop(view, anchor, 'navigation', guard)
        with tempfile.TemporaryDirectory() as folder, patch('subprocess.run') as run:
            with self.assertRaises(RecapError): GLMRuntime(folder).run_crop(crop, 'fixture')
            run.assert_not_called()

    def test_metrics_are_separate_and_overlapping_content_is_rejected(self):
        first = Token('累计观众', (100, 100, 160, 120))
        second = Token('新增粉丝', (400, 100, 460, 120))
        identity = Token('抖音号:demo', (100, 400, 200, 420))
        view = self.view([first, second, identity])
        a = make_local_crop(view, first, 'metric', lambda window: window, 'demo')
        b = make_local_crop(view, second, 'metric', lambda window: window, 'demo')
        self.assertEqual((a.purpose, b.purpose), ('metric', 'metric'))
        overlap = Token('新增粉丝', (165, 100, 225, 120))
        with self.assertRaises(RecapError):
            make_local_crop(self.view([first, overlap, identity]), first, 'metric', lambda window: window, 'demo')

    def test_product_metric_review_only_fills_verified_missing_value(self):
        from glm_desktop import GLMDesktop
        metric = Token('累计观众', (100, 100, 160, 120))
        identity = Token('抖音号:demo', (100, 400, 200, 420))
        view = self.view([metric, identity])
        runtime = Mock()
        runtime.run_crop.return_value = {'value': '12'}
        driver = object.__new__(GLMDesktop)
        driver.guard = lambda window: window
        driver.expected_account = 'demo'
        driver.vision = GLMVision(runtime)
        result = {'metrics': {'累计观众': None, '新增粉丝': 0}, 'raw_displays': {},
                  'missing_fields': ['累计观众'], 'missing_reasons': {'累计观众': '未读取'},
                  'approximate_metrics': []}
        refined = driver.refine_metrics(view, result)
        self.assertEqual(refined['metrics'], {'累计观众': 12, '新增粉丝': 0})
        self.assertEqual(runtime.run_crop.call_count, 1)
        runtime.reset_mock()
        self.assertEqual(driver.refine_metrics(view, refined)['metrics']['新增粉丝'], 0)
        runtime.run_crop.assert_not_called()

    def test_actual_worker_rejects_invalid_input_without_echo_or_files(self):
        with tempfile.TemporaryDirectory() as folder:
            worker = Path(__file__).resolve().parents[1] / 'agent_worker.py'
            result = subprocess.run([sys.executable, '-B', str(worker)], input=b'{"secret":"marker"}',
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=folder, timeout=10)
            self.assertEqual(result.returncode, 2)
            self.assertNotIn(b'marker', result.stdout + result.stderr)
            self.assertEqual(list(Path(folder).iterdir()), [])

    def test_worker_parses_actual_in_memory_serialization(self):
        import agent_worker
        import base64
        anchor = Token('主播列表', (100, 80, 160, 100))
        crop = make_local_crop(self.view([anchor]), anchor, 'navigation', lambda window: window)
        reply = {'status': 'OK', 'result': {}, 'http_requests': 1}
        with tempfile.TemporaryDirectory() as folder, \
             patch.dict('os.environ', {'TIANYI_API_KEY': 'fake', 'BACKSTAGE_SDK_PYTHON': sys.executable}), \
             patch('glm_runtime.require_gate_a'), \
             patch('subprocess.run', return_value=subprocess.CompletedProcess([], 0, json.dumps(reply).encode(), b'')) as run:
            GLMRuntime(folder, gate='fixture-gate').run_crop(crop, 'fixture')
            encoded = run.call_args.kwargs['input']
            capability = run.call_args.kwargs['env']['RECAP_VISUAL_CAPABILITY']
        with patch.dict('os.environ', {'RECAP_VISUAL_CAPABILITY': capability}):
            result = agent_worker.read_job(io.BytesIO(encoded))
        self.assertEqual(base64.b64decode(result['image_base64']), crop.image)
        with patch.dict('os.environ', {}, clear=True), self.assertRaises(ValueError):
            agent_worker.read_job(io.BytesIO(encoded))

    def test_sdk_adapter_receives_only_in_memory_crop(self):
        import agent_worker
        import base64
        anchor = Token('主播列表', (100, 80, 160, 100))
        crop = make_local_crop(self.view([anchor]), anchor, 'navigation', lambda window: window)
        job = {'image_base64': base64.b64encode(crop.image).decode('ascii'),
               'prompt': 'fixture', 'purpose': 'navigation'}
        observed = {}
        class Client:
            def __init__(self, **kwargs):
                observed['base_url'] = kwargs['base_url']
                observed['client'] = kwargs['http_client']
            async def close(self): observed['closed'] = True
        class Http:
            def __init__(self, event_hooks): self.event_hooks = event_hooks
        class Runner:
            @staticmethod
            async def run(agent, input, max_turns):
                observed['content'] = input[0]['content']
                await observed['client'].event_hooks['request'][0](None)
                return types.SimpleNamespace(final_output='{"status":"missing","box":null}')
        agents = types.ModuleType('agents')
        agents.Agent = lambda **kwargs: kwargs
        agents.AsyncOpenAI = Client
        def model(**kwargs):
            observed['model'] = kwargs['model']
            return kwargs
        agents.OpenAIChatCompletionsModel = model
        agents.Runner = Runner
        agents.set_tracing_disabled = lambda flag: observed.update(tracing_disabled=flag)
        settings = types.ModuleType('agents.model_settings')
        settings.ModelSettings = lambda **kwargs: kwargs
        shared = types.ModuleType('openai.types.shared')
        shared.Reasoning = lambda **kwargs: kwargs
        httpx2 = types.ModuleType('httpx2')
        httpx2.AsyncClient = Http
        with tempfile.TemporaryDirectory() as folder, patch.dict('os.environ', {'TIANYI_API_KEY': 'fake'}), \
             patch.dict(sys.modules, {'agents': agents, 'agents.model_settings': settings,
                                      'openai.types.shared': shared, 'httpx2': httpx2}):
            result = asyncio.run(agent_worker.run(job))
            self.assertEqual(result['result'], {'status': 'missing', 'box': None})
            self.assertEqual(observed['base_url'], 'https://ai.ctaigw.cn/v1')
            self.assertEqual(observed['model'], 'glm-5.3-flash')
            self.assertEqual(observed['content'][1]['image_url'], 'data:image/png;base64,' + job['image_base64'])
            self.assertTrue(observed['tracing_disabled'] and observed['closed'])
            self.assertEqual(list(Path(folder).iterdir()), [])


if __name__ == '__main__': unittest.main()
