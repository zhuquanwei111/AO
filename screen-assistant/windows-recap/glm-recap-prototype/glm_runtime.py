"""天翼GLM单一路由薄适配：官方Agents Runner在隔离子进程中运行。"""
import json
import os
from pathlib import Path
import secrets
import subprocess
import time
import uuid

MODEL='glm-5.3-flash'
BASE_URL='https://ai.ctaigw.cn/v1'


def parse_final(text,receipt=None):
    text=text.strip()
    if text.startswith('```json\n') and text.endswith('```'): text=text[8:-3].strip()
    elif text.startswith('```\n') and text.endswith('```'): text=text[4:-3].strip()
    value=json.loads(text)
    if receipt is None:
        if not isinstance(value,dict): raise ValueError('invalid_result_json')
        if set(value)=={'result'} and isinstance(value['result'],dict): return value['result']
        return value
    if not isinstance(value,dict) or value.get('observation_receipt')!=receipt or not isinstance(value.get('result'),dict):
        raise ValueError('invalid_tool_receipt_or_json')
    return value['result']


def require_gate_a(path):
    try: value=json.loads(Path(path).read_text(encoding='utf-8'))
    except (OSError,ValueError): raise ValueError('gate_a_not_verified') from None
    if value.get('gate_a_visual_mcp_roundtrip')!='OK' or value.get('model')!=MODEL or value.get('base_url')!=BASE_URL:
        raise ValueError('gate_a_not_verified')


def require_sdk_runtime():
    sdk_python=os.environ.get('BACKSTAGE_SDK_PYTHON')
    if not sdk_python or not Path(sdk_python).is_file():
        raise ValueError('agent_sdk_runtime_missing')
    return sdk_python


class GLMRuntime:
    def __init__(self,scratch,context=None):
        self.scratch=Path(scratch).resolve(); self.scratch.mkdir(parents=True,exist_ok=True)
        self.calls=0; self.http_requests=0; self.context=context or {}; self.deadline=time.monotonic()+240

    def run(self,image,prompt,schema=None,challenge=None):
        if not os.environ.get('TIANYI_API_KEY'): raise RuntimeError('missing_current_process_tianyi_key')
        sdk_python=require_sdk_runtime()
        if self.calls>=24: raise RuntimeError('model_call_budget_exhausted')
        remaining=self.deadline-time.monotonic()
        if remaining<=0: raise RuntimeError('model_phase_budget_exhausted')
        image=Path(image).resolve()
        if not image.is_file(): raise ValueError('image_missing')
        folder=self.scratch/uuid.uuid4().hex; folder.mkdir()
        job=folder/'job.json'; result=folder/'result.json'
        job.write_text(json.dumps({'image':str(image),'prompt':prompt,'schema':schema,
            'context':self.context,'receipt':secrets.token_hex(12),'challenge':challenge,
            'result_path':str(result),'audit':str(folder/'mcp-audit.jsonl')},ensure_ascii=False),encoding='utf-8')
        env={k:v for k,v in os.environ.items() if not k.startswith('AO_')}
        env.update(PYTHONUTF8='1',PYTHONDONTWRITEBYTECODE='1')
        self.calls+=1
        process=subprocess.Popen([sdk_python,str(Path(__file__).with_name('agent_worker.py')),str(job)],
            stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,encoding='utf-8',env=env,
            creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        try: stdout,stderr=process.communicate(timeout=min(110,remaining))
        except subprocess.TimeoutExpired:
            subprocess.run(['taskkill','/PID',str(process.pid),'/T','/F'],capture_output=True,timeout=10,
                creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            self.http_requests=None
            raise RuntimeError('agent_timeout_outcome_unknown') from None
        # worker只输出脱敏摘要；不输出SDK异常原文、HTTP正文或密钥。
        (folder/'worker-summary.json').write_text(stdout,encoding='utf-8')
        (folder/'worker-stderr.log').write_text(stderr,encoding='utf-8')
        if not result.is_file():
            self.http_requests=None
            raise RuntimeError('agent_worker_no_receipt')
        output=json.loads(result.read_text(encoding='utf-8'))
        self.http_requests+=output.get('http_requests',0)
        if output.get('status')!='OK': raise RuntimeError('glm_'+output.get('reason','agent_failed'))
        return output['result'],output['receipt']
