"""从受限单字段文件向桥子进程传递凭据；不输出凭据。"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import queue
import re
import subprocess
import sys
import threading

from .backstage_bridge import BackstageFatalError, validate_worker_url, verify_ssh_tunnel
from . import backstage_bridge


def read_worker_token(path: Path) -> str:
    try:
        data=path.read_bytes()
        if data.startswith(b'\xef\xbb\xbf') or len(data)>4096:
            raise ValueError()
        text=data.decode('utf-8')
        lines=text.splitlines()
        if len(lines)!=1 or not text.endswith('\n') or not lines[0].startswith('BACKSTAGE_WORKER_TOKEN='):
            raise ValueError()
        token=lines[0].split('=',1)[1]
        if not token or token!=token.strip() or any(ord(ch)<33 or ord(ch)>126 for ch in token):
            raise ValueError()
        return token
    except (OSError,UnicodeError,ValueError):
        raise BackstageFatalError('worker credential file invalid') from None


def launch_bridge(url: str, ssh_pid: int, ssh_created_filetime: int, token_path: Path, source_env):
    validate_worker_url(url)
    verify_ssh_tunnel(url,ssh_pid,ssh_created_filetime)
    token=read_worker_token(token_path)
    allowed={'SYSTEMROOT','WINDIR','PATH','PATHEXT','TEMP','TMP','LOCALAPPDATA','APPDATA','USERPROFILE',
             'COMSPEC','PROGRAMFILES','PROGRAMFILES(X86)','OS','HOME','LANG','LC_ALL',
             'BACKSTAGE_CLI_PYTHON','BACKSTAGE_CLI_ENTRY','BACKSTAGE_CLI_DATA','BACKSTAGE_CLI_MODELS',
             'BACKSTAGE_CLI_SCRATCH','BACKSTAGE_MCP_CLIENT_PYTHON','BACKSTAGE_WINDOWS_MCP_PYTHON',
             'BACKSTAGE_SDK_PYTHON','BACKSTAGE_CLI_GATE_A','TIANYI_API_KEY',
             'BACKSTAGE_POLL_SECONDS','BACKSTAGE_CLI_TIMEOUT_SECONDS'}
    env={key:value for key,value in source_env.items() if key.upper() in allowed}
    env.update(BACKSTAGE_SERVER_URL=url,BACKSTAGE_WORKER_TOKEN=token,
               BACKSTAGE_SSH_PID=str(ssh_pid),BACKSTAGE_SSH_CREATED_FILETIME=str(ssh_created_filetime),
               PYTHONUTF8='1',PYTHONDONTWRITEBYTECODE='1')
    try:
        process=subprocess.Popen([sys.executable,'-m','screen_assistant_server.backstage_bridge','--startup-handshake'],
                                 cwd=str(Path(__file__).resolve().parent.parent),
                                 env=env,stdin=subprocess.PIPE,stdout=subprocess.PIPE,
                                 stderr=subprocess.PIPE,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    except OSError:
        raise BackstageFatalError('bridge process could not start') from None
    process.safe_stderr=bytearray()
    def drain_stderr():
        while True:
            block=process.stderr.read(1024)
            if not block: break
            if len(process.safe_stderr)<4096:
                process.safe_stderr.extend(block[:4096-len(process.safe_stderr)])
    process.stderr_thread=threading.Thread(target=drain_stderr,daemon=True)
    process.stderr_thread.start()
    replies=queue.Queue(maxsize=1)
    def read_startup():
        try: replies.put(process.stdout.readline(1025))
        except (OSError,ValueError): replies.put(b'')
    threading.Thread(target=read_startup,daemon=True).start()
    try:
        line=replies.get(timeout=15)
    except queue.Empty:
        line=b''
    try:
        reply=json.loads(line) if len(line)<=1024 else None
    except (ValueError,UnicodeError,TypeError):
        reply=None
    if reply=={'stage':'preclaim','status':'ready'} and process.poll() is None:
        try:
            process.stdin.write(b'go\n'); process.stdin.flush(); process.stdin.close()
        except (OSError,ValueError):
            reply={'stage':'handshake','error':'OSError'}
        else:
            return process
    try:
        code=process.wait(timeout=2)
    except subprocess.TimeoutExpired:
        process.terminate()
        try: code=process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill(); code=process.wait(timeout=5)
    process.stdout.close()
    if process.stdin and not process.stdin.closed:
        process.stdin.close()
    process.stderr_thread.join(timeout=5)
    process.stderr.close()
    stage=reply.get('stage') if isinstance(reply,dict) else None
    error=reply.get('error') if isinstance(reply,dict) else None
    setting=reply.get('setting') if isinstance(reply,dict) else None
    if stage not in ('config','lock','handshake'): stage='unknown'
    if error not in ('BackstageFatalError','ValueError','ImportError','OSError'): error='unknown'
    if error=='unknown': error=stderr_category(process.safe_stderr)
    known_settings=('BACKSTAGE_SERVER_URL','BACKSTAGE_WORKER_TOKEN','BACKSTAGE_CLI_PYTHON',
                    'BACKSTAGE_CLI_ENTRY','BACKSTAGE_SSH_PID','BACKSTAGE_SSH_CREATED_FILETIME')
    suffix=f', setting={setting}' if setting in known_settings else ''
    raise BackstageFatalError(f'bridge startup failed: stage={stage}, category={error}, exit={code}{suffix}')


def stderr_category(data):
    """只投影固定异常类别，不输出进程诊断正文。"""
    allowed=('ModuleNotFoundError','ImportError','ValueError','TypeError','OSError','RuntimeError',
             'AttributeError','SyntaxError','UnicodeDecodeError')
    for name in allowed:
        if re.search(rb'(?m)^'+name.encode('ascii')+rb'(?::|\s|$)',bytes(data)):
            return name
    return 'unknown'


def supervise_bridge(process):
    """等待子进程退出，仅保留有界且白名单允许的状态。"""
    captured=bytearray()
    def drain():
        while True:
            block=process.stdout.read(1024)
            if not block: break
            if len(captured)<1025:
                captured.extend(block[:1025-len(captured)])
    thread=threading.Thread(target=drain,daemon=True)
    thread.start()
    code=process.wait()
    thread.join(timeout=5)
    process.stdout.close()
    process.stderr_thread.join(timeout=5)
    process.stderr.close()
    try:
        result=json.loads(captured) if len(captured)<=1024 else None
    except (ValueError,UnicodeError,TypeError):
        result=None
    category=result.get('error') if isinstance(result,dict) and result.get('stage')=='runtime' else None
    if not isinstance(category,str) or not (category in ('bridge_fatal','runtime_exception') or re.fullmatch(r'http_rejected_[0-9]{3}',category)):
        category=stderr_category(process.safe_stderr)
    if category=='runtime_exception' or isinstance(result,dict) and 'phase' in result and category!='unknown':
        detail=backstage_bridge.safe_runtime_diagnostic(result)
        child_code=detail['cli_exit_code'] if detail['cli_exit_code'] is not None else 'unknown'
        print(f"bridge exited: stage=runtime, category={category}, phase={detail['phase']}, "
              f"exception={detail['exception_class']}, cli_exit={child_code}, exit={code}",flush=True)
    else:
        print(f'bridge exited: stage=runtime, category={category}, exit={code}',flush=True)
    return code


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--server-url',required=True)
    parser.add_argument('--ssh-pid',required=True,type=int)
    parser.add_argument('--ssh-created-filetime',required=True,type=int)
    args=parser.parse_args()
    if os.name!='nt' or not os.environ.get('LOCALAPPDATA'):
        raise BackstageFatalError('interactive Windows user required')
    token_path=Path(os.environ['LOCALAPPDATA'])/'screen-assistant'/'recap-bridge'/'private-config-v1'/'worker-token.env'
    process=launch_bridge(args.server_url,args.ssh_pid,args.ssh_created_filetime,token_path,os.environ)
    print(f'bridge process started: {process.pid}; preclaim initialization ready; claim not confirmed',flush=True)
    return supervise_bridge(process)


if __name__=='__main__':
    try: raise SystemExit(main())
    except BackstageFatalError as error:
        print(str(error),file=sys.stderr)
        raise SystemExit(2) from None
