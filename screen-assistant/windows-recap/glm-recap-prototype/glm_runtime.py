"""保留旧配置，并为经校验的局部图片提供内存模型传输。"""
import json
import os
import base64
import hashlib
import hmac
import secrets
import subprocess
from pathlib import Path
import time

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
    def __init__(self,scratch,context=None,gate=None):
        self.scratch=Path(scratch).resolve(); self.scratch.mkdir(parents=True,exist_ok=True)
        self.calls=0; self.http_requests=0; self.context=context or {}; self.gate=gate; self.deadline=time.monotonic()+240

    def run(self,image,prompt,schema=None,challenge=None):
        raise RuntimeError('image_visual_disabled')

    def run_crop(self, crop, prompt):
        """仅将本轮核验的小裁区经子进程标准输入传给既有模型。"""
        from glm_vision import LocalCrop, _CROP_ISSUER
        if not isinstance(crop, LocalCrop) or crop._issuer is not _CROP_ISSUER:
            raise RuntimeError('visual_crop_source_invalid')
        crop.ensure_current()
        if not isinstance(prompt, str) or not prompt or len(prompt) > 2048:
            raise RuntimeError('visual_prompt_invalid')
        if not os.environ.get('TIANYI_API_KEY'):
            raise RuntimeError('missing_current_process_tianyi_key')
        if self.gate is None:
            raise RuntimeError('gate_a_not_verified')
        require_gate_a(self.gate)
        sdk_python = require_sdk_runtime()
        if self.calls >= 24 or time.monotonic() >= self.deadline:
            raise RuntimeError('model_phase_budget_exhausted')
        job = {'image_base64': base64.b64encode(crop.image).decode('ascii'),
               'prompt': prompt, 'purpose': crop.purpose}
        canonical = json.dumps(job, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf-8')
        capability = secrets.token_hex(32)
        job['attestation'] = hmac.new(capability.encode('ascii'), canonical, hashlib.sha256).hexdigest()
        data = json.dumps(job, ensure_ascii=False).encode('utf-8')
        if len(data) > 4 * 1024 * 1024:
            raise RuntimeError('visual_crop_too_large')
        allowed = {'SYSTEMROOT', 'WINDIR', 'PATH', 'PATHEXT', 'TEMP', 'TMP', 'LOCALAPPDATA',
                   'APPDATA', 'USERPROFILE', 'COMSPEC', 'PROGRAMFILES', 'PROGRAMFILES(X86)',
                   'OS', 'HOME', 'LANG', 'LC_ALL', 'TIANYI_API_KEY'}
        env = {key: value for key, value in os.environ.items() if key.upper() in allowed}
        env.update(PYTHONUTF8='1', PYTHONDONTWRITEBYTECODE='1', RECAP_VISUAL_CAPABILITY=capability)
        self.calls += 1
        try:
            result = subprocess.run([sdk_python, '-B', str(Path(__file__).with_name('agent_worker.py'))],
                input=data, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env=env,
                timeout=min(110, max(.1, self.deadline-time.monotonic())),
                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        except (OSError, subprocess.TimeoutExpired):
            self.http_requests = None
            raise RuntimeError('visual_worker_failed') from None
        if result.returncode != 0 or len(result.stdout) > 16384:
            raise RuntimeError('visual_worker_failed')
        try:
            reply = json.loads(result.stdout)
        except (ValueError, UnicodeError):
            raise RuntimeError('visual_worker_invalid_reply') from None
        if not isinstance(reply, dict) or reply.get('status') != 'OK' or not isinstance(reply.get('result'), dict):
            raise RuntimeError('visual_worker_invalid_reply')
        requests = reply.get('http_requests')
        if type(requests) is not int or not 1 <= requests <= 3:
            raise RuntimeError('visual_worker_invalid_reply')
        self.http_requests = None if self.http_requests is None else self.http_requests + requests
        return reply['result']
