import io
import asyncio
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
from glm_vision import make_local_crop
from glm_runtime import GLMRuntime


class MemoryVisualTests(unittest.TestCase):
    def view(self, tokens):
        image = io.BytesIO()
        Image.new('RGB', (800, 600), 'white').save(image, 'PNG')
        return View(tokens, image.getvalue(), {'handle': 1, 'pid': 2, 'process_created': 3, 'rect': [0, 0, 800, 600]},
                    (800, 600), 'fixture-time', [])

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
        view = self.view([first, second])
        a = make_local_crop(view, first, 'metric', lambda window: window)
        b = make_local_crop(view, second, 'metric', lambda window: window)
        self.assertEqual((a.purpose, b.purpose), ('metric', 'metric'))
        overlap = Token('新增粉丝', (165, 100, 225, 120))
        with self.assertRaises(RecapError):
            make_local_crop(self.view([first, overlap]), first, 'metric', lambda window: window)

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
