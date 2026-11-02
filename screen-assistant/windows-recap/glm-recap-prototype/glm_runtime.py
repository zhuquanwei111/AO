"""保留旧配置兼容；当前复盘路径不发送图片或调用视觉模型。"""
import json
import os
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
    def __init__(self,scratch,context=None):
        self.scratch=Path(scratch).resolve(); self.scratch.mkdir(parents=True,exist_ok=True)
        self.calls=0; self.http_requests=0; self.context=context or {}; self.deadline=time.monotonic()+240

    def run(self,image,prompt,schema=None,challenge=None):
        raise RuntimeError('image_visual_disabled')
