"""只使用虚构输出验证诊断，不请求模型或操作桌面。"""
import asyncio
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
import agent_worker
from glm_runtime import GLMRuntime,safe_worker_diagnostic
from glm_vision import GLMVision
from core import RecapError
import test_memory_visual as fixture_module


class WorkerDiagnosticsTests(unittest.TestCase):
    def sdk(self,answer=None,error=None,status=None):
        observed={}
        class Client:
            def __init__(self,**kwargs): observed['http']=kwargs['http_client']
            async def close(self): pass
        class Http:
            def __init__(self,event_hooks): self.event_hooks=event_hooks
        class Runner:
            @staticmethod
            async def run(*args,**kwargs):
                hooks=observed['http'].event_hooks
                await hooks['request'][0](None)
                if status is not None: await hooks['response'][0](types.SimpleNamespace(status_code=status))
                if error: raise error
                return types.SimpleNamespace(final_output=answer,raw_responses=[])
        agents=types.ModuleType('agents')
        agents.Agent=lambda **kwargs:kwargs; agents.AsyncOpenAI=Client
        agents.OpenAIChatCompletionsModel=lambda **kwargs:kwargs; agents.Runner=Runner
        agents.set_tracing_disabled=lambda flag:None
        settings=types.ModuleType('agents.model_settings'); settings.ModelSettings=lambda **kwargs:kwargs
        shared=types.ModuleType('openai.types.shared'); shared.Reasoning=lambda **kwargs:kwargs
        http=types.ModuleType('httpx2'); http.AsyncClient=Http
        return patch.dict(sys.modules,{'agents':agents,'agents.model_settings':settings,
            'openai.types.shared':shared,'httpx2':http})

    def test_worker_separates_timeout_http_and_json_without_raw_content(self):
        cases=[(None,TimeoutError('private detail'),None,'request_timeout'),
               (None,type('APIStatusError',(Exception,),{})('private detail'),503,'http_error'),
               ('private model text',None,200,'json_parse_failed')]
        job={'image_base64':'synthetic','prompt':'synthetic','purpose':'navigation'}
        for answer,error,status,category in cases:
            with self.subTest(category=category),self.sdk(answer,error,status), \
                 patch.dict('os.environ',{'TIANYI_API_KEY':'test-only'}):
                reply=asyncio.run(agent_worker.run(job))
                self.assertEqual(reply['status'],'FAIL')
                self.assertEqual(reply['diagnostic']['category'],category)
                self.assertEqual(reply['diagnostic']['request_attempts'],1)
                self.assertEqual(reply['diagnostic']['http_status'],status)
                self.assertNotIn('private',json.dumps(reply))

    def test_worker_known_sdk_budget_error_has_fixed_category(self):
        message="Chat Completions response terminated with finish_reason='length' but produced no assistant text, tool call, or refusal."
        error=type('ModelBehaviorError',(Exception,),{})(message)
        with self.sdk(None,error,200),patch.dict('os.environ',{'TIANYI_API_KEY':'test-only'}):
            reply=asyncio.run(agent_worker.run({'image_base64':'synthetic','prompt':'synthetic','purpose':'navigation'}))
        self.assertEqual(reply['diagnostic']['category'],'output_budget_exhausted')

    def test_runtime_failure_diagnostic_reaches_vision_and_count_stays_unknown(self):
        view=fixture_module.MemoryVisualTests().view([])
        diagnostic={'phase':'sdk_request','category':'request_timeout','request_attempts':1,
                    'http_status':None,'output_chars':None,'output_tokens':None}
        reply={'status':'FAIL','diagnostic':diagnostic}
        with tempfile.TemporaryDirectory() as folder, \
             patch.dict('os.environ',{'TIANYI_API_KEY':'test-only','BACKSTAGE_SDK_PYTHON':sys.executable}), \
             patch('glm_runtime.require_gate_a'), \
             patch('subprocess.run',return_value=subprocess.CompletedProcess([],2,json.dumps(reply).encode(),b'')):
            runtime=GLMRuntime(folder,gate='synthetic')
            with self.assertRaisesRegex(RecapError,'visual_worker_request_timeout'):
                GLMVision(runtime).locate(view,'地址栏',guard=lambda window:window)
            self.assertIsNone(runtime.http_requests)
            self.assertEqual(runtime.diagnostics[-1]['category'],'request_timeout')
            self.assertEqual(list(Path(folder).iterdir()),[])

    def test_unknown_worker_diagnostic_is_not_logged(self):
        view=fixture_module.MemoryVisualTests().view([])
        reply={'status':'FAIL','diagnostic':{'phase':'private','category':'secret','raw':'private'}}
        with tempfile.TemporaryDirectory() as folder, \
             patch.dict('os.environ',{'TIANYI_API_KEY':'test-only','BACKSTAGE_SDK_PYTHON':sys.executable}), \
             patch('glm_runtime.require_gate_a'), \
             patch('subprocess.run',return_value=subprocess.CompletedProcess([],2,json.dumps(reply).encode(),b'')):
            runtime=GLMRuntime(folder,gate='synthetic')
            with self.assertRaises(RecapError): GLMVision(runtime).locate(view,'地址栏',guard=lambda window:window)
            self.assertNotIn('private',json.dumps(runtime.diagnostics))
            self.assertNotIn('secret',json.dumps(runtime.diagnostics))

    def test_diagnostic_rejects_untyped_or_unbounded_fields(self):
        good={'phase':'sdk_request','category':'request_timeout','request_attempts':1,
              'http_status':None,'output_chars':None,'output_tokens':None}
        self.assertEqual(safe_worker_diagnostic(good),good)
        for key,value in (('phase',[]),('category',{}),('request_attempts',True),
                          ('http_status',999),('output_chars','private'),('output_tokens',-1)):
            self.assertIsNone(safe_worker_diagnostic({**good,key:value}))

    def test_cli_persists_only_safe_failure_diagnostic(self):
        import recap_glm
        view=fixture_module.MemoryVisualTests().view([])
        diagnostic={'phase':'sdk_request','category':'request_timeout','request_attempts':1,
                    'http_status':None,'output_chars':None,'output_tokens':None}
        reply={'status':'FAIL','diagnostic':diagnostic}
        with tempfile.TemporaryDirectory() as folder:
            runtime=GLMRuntime(folder,gate='synthetic')
            desktop=Mock(); desktop.bind.side_effect=lambda account:GLMVision(runtime).locate(view,'地址栏',guard=lambda window:window)
            wire=Mock(); wire.close.return_value=True
            modules={'glm_desktop':types.SimpleNamespace(GLMDesktop=Mock(return_value=desktop)),
                     'window_probe':types.SimpleNamespace(Probe=Mock()),
                     'ocr_local':types.SimpleNamespace(LocalOCR=Mock()),
                     'transport':types.SimpleNamespace(MCP=Mock(return_value=wire))}
            args=['recap_glm.py','start','--task','synthetic','--account','synthetic',
                  '--session-id','synthetic','--source','android_app','--data',folder,
                  '--scratch',folder,'--desktop-authorized']
            with patch.object(sys,'argv',args),patch.dict(sys.modules,modules), \
                 patch.object(recap_glm,'GLMRuntime',return_value=runtime), \
                 patch.object(recap_glm,'require_gate_a'),patch.object(recap_glm,'require_sdk_runtime'), \
                 patch('glm_runtime.require_gate_a'), \
                 patch.dict('os.environ',{'TIANYI_API_KEY':'test-only','BACKSTAGE_SDK_PYTHON':sys.executable}), \
                 patch('subprocess.run',return_value=subprocess.CompletedProcess([],2,json.dumps(reply).encode(),b'')), \
                 redirect_stdout(io.StringIO()) as output:
                self.assertEqual(recap_glm.main(),2)
            record=json.loads(output.getvalue())['record']
            self.assertEqual(record['reason'],'visual_worker_request_timeout')
            self.assertIsNone(record['normal_path_llm_calls'])
            self.assertEqual(record['visual_diagnostics'][0]['request_attempts'],1)
            self.assertNotIn('test-only',output.getvalue())
