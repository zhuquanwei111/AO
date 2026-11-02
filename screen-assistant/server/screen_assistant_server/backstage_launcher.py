"""从受限单字段文件向桥子进程传递凭据；不输出凭据。"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import subprocess
import sys

from .backstage_bridge import BackstageFatalError, validate_worker_url, verify_ssh_tunnel


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
             'BACKSTAGE_CLI_SCRATCH','BACKSTAGE_MCP_CLIENT_PYTHON','BACKSTAGE_WINDOWS_MCP_PYTHON'}
    env={key:value for key,value in source_env.items() if key.upper() in allowed}
    env.update(BACKSTAGE_SERVER_URL=url,BACKSTAGE_WORKER_TOKEN=token,
               BACKSTAGE_SSH_PID=str(ssh_pid),BACKSTAGE_SSH_CREATED_FILETIME=str(ssh_created_filetime),
               PYTHONUTF8='1',PYTHONDONTWRITEBYTECODE='1')
    try:
        process=subprocess.Popen([sys.executable,'-m','screen_assistant_server.backstage_bridge'],
                                 cwd=str(Path(__file__).resolve().parent.parent),
                                 env=env,stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,
                                 stderr=subprocess.DEVNULL,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    except OSError:
        raise BackstageFatalError('bridge process could not start') from None
    return process.pid


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--server-url',required=True)
    parser.add_argument('--ssh-pid',required=True,type=int)
    parser.add_argument('--ssh-created-filetime',required=True,type=int)
    args=parser.parse_args()
    if os.name!='nt' or not os.environ.get('LOCALAPPDATA'):
        raise BackstageFatalError('interactive Windows user required')
    token_path=Path(os.environ['LOCALAPPDATA'])/'screen-assistant'/'recap-bridge'/'private-config-v1'/'worker-token.env'
    pid=launch_bridge(args.server_url,args.ssh_pid,args.ssh_created_filetime,token_path,os.environ)
    print(f'bridge process started: {pid}; readiness not confirmed')


if __name__=='__main__':
    try: main()
    except BackstageFatalError as error:
        print(str(error),file=sys.stderr)
        raise SystemExit(2) from None
