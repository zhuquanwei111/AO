"""复用既有官方MCP SDK JSONL客户端；本文件不实现任何桌面动作。"""
import json
import os
from pathlib import Path
import queue
import shutil
import subprocess
import threading
import time
from core import RecapError

TOOLS={'App','Click','ControlStatus','Screenshot','Scroll','Shortcut','Snapshot','Type','Wait'}


def check_reply(reply):
    text='\n'.join(item.get('text','') for item in reply.get('content',[]))
    if 'USER_CONTROL' in text.upper(): raise RecapError('USER_CONTROL')
    if reply.get('is_error') or reply.get('error'):
        raise RecapError('mcp_tool_error')
    if any(marker in text.lower() for marker in ('error capturing','error:','failed to','not found','could not','unable to')):
        raise RecapError('mcp_text_error')
    return text


class MCP:
    def __init__(self,python,client,trace,observations=None):
        self.python,self.client,self.trace=python,Path(client),Path(trace)
        self.observations=Path(observations or self.trace.parent/'observations').resolve()
        self.process=None; self.lines=queue.Queue(); self._call_inflight=False
        self._close_confirmed=False

    def _read(self):
        try:
            for line in self.process.stdout:
                self.lines.put(line)
        finally: self.lines.put(None)

    def _receive(self):
        try: line=self.lines.get(timeout=75)
        except queue.Empty: raise RecapError('mcp_timeout_outcome_unknown') from None
        if line is None: raise RecapError('mcp_client_exited')
        try: return json.loads(line)
        except ValueError: raise RecapError('invalid_mcp_reply') from None

    def open(self):
        if self.process: return
        began=time.monotonic()
        if not self.client.is_file() or not Path(self.python).is_file():
            raise RecapError('mcp_runtime_missing')
        self.trace.parent.mkdir(parents=True,exist_ok=True)
        self.observations.mkdir(parents=True,exist_ok=True)
        self.stderr=(self.trace.parent/'mcp-stderr.log').open('a',encoding='utf-8')
        child_env={key:value for key,value in os.environ.items()
            if key not in {'TIANYI_API_KEY','BACKSTAGE_WORKER_TOKEN'} and not key.startswith('AO_')}
        child_env['BACKSTAGE_MCP_OBSERVATIONS_DIR']=str(self.observations)
        self.process=subprocess.Popen([self.python,str(self.client)],stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,stderr=self.stderr,text=True,encoding='utf-8',bufsize=1,
            env=child_env,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        threading.Thread(target=self._read,daemon=True).start()
        ready=self._receive()
        if not ready.get('ready') or set(ready.get('tools',[]))!=TOOLS:
            raise RecapError('mcp_whitelist_mismatch')
        with self.trace.open('a',encoding='utf-8') as stream:
            stream.write(json.dumps({'event':'mcp_ready','elapsed_ms':round((time.monotonic()-began)*1000)})+'\n')

    def call(self,tool,**arguments):
        if tool not in TOOLS: raise RecapError('tool_not_allowed')
        if arguments.get('use_dom'): raise RecapError('dom_forbidden')
        self.open(); began=time.monotonic()
        self._call_inflight=True
        self.process.stdin.write(json.dumps({'tool':tool,'arguments':arguments},ensure_ascii=False)+'\n')
        self.process.stdin.flush()
        reply=self._receive()
        self._call_inflight=False
        for item in reply.get('content',[]):
            if 'image_path' in item:
                source=Path(item['image_path']).resolve()
                if source.parent!=self.observations or not source.is_file():
                    raise RecapError('unexpected_screenshot_path')
                folder=self.trace.parent/'screens'; folder.mkdir(exist_ok=True)
                target=folder/source.name
                if target.exists(): raise RecapError('screenshot_path_collision')
                shutil.move(str(source),str(target))
                item['image_path']=str(target)
        # 不记录输入正文、页面树或截图base64到常规日志。
        with self.trace.open('a',encoding='utf-8') as stream:
            event={'tool':tool,'elapsed_ms':round((time.monotonic()-began)*1000),
                'is_error':bool(reply.get('is_error'))}
            if tool=='Shortcut' and arguments.get('shortcut') in ('ctrl+t','ctrl+l','end','esc'):
                event['shortcut']=arguments['shortcut']
            if tool=='App' and arguments.get('mode')=='launch':
                event['app_result_text']='\n'.join(item.get('text','') for item in reply.get('content',[]))[:1024]
            stream.write(json.dumps(event)+'\n')
        check_reply(reply)
        return reply

    def close(self):
        if not self.process: return self._close_confirmed
        confirmed=False
        try:
            self.process.stdin.close()
            try:
                confirmed=self.process.wait(timeout=10)==0 and not self._call_inflight
            except subprocess.TimeoutExpired:
                # 仅终止本次启动的客户端/其MCP子树，绝不扫描／终止其他会话。
                subprocess.run(['taskkill','/PID',str(self.process.pid),'/T','/F'],capture_output=True,
                    creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0),timeout=10)
        finally:
            self._close_confirmed=confirmed
            self.stderr.close()
            self.process=None
        return confirmed

    def note(self,event,**fields):
        self.trace.parent.mkdir(parents=True,exist_ok=True)
        with self.trace.open('a',encoding='utf-8') as stream:
            stream.write(json.dumps({'event':event,**fields})+'\n')
