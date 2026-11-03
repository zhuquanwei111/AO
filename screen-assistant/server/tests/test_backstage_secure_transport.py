"""仅验证虚构令牌和隔离回环，不访问生产服务。"""
from unittest.mock import Mock
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import os
import subprocess
import threading

import pytest

from screen_assistant_server.backstage_bridge import BridgeConfig, BackstageFatalError, validate_worker_url


@pytest.mark.parametrize('url', [
    'http://118.195.214.101:8000', 'http://localhost:8000', 'http://127.0.0.1',
    'http://127.0.0.1:8000/path', 'http://127.0.0.1:8000/?x=1',
    'http://user@127.0.0.1:8000', 'https://127.0.0.1:8000',
])
def test_worker_transport_rejects_noncanonical_endpoint(url):
    with pytest.raises(BackstageFatalError): validate_worker_url(url)


def test_worker_transport_accepts_only_explicit_loopback_port():
    assert validate_worker_url('http://127.0.0.1:43210') == 'http://127.0.0.1:43210'


@pytest.mark.skipif(os.name!='nt',reason='Windows SSH进程与监听验证')
def test_tunnel_identity_and_listener_must_match(monkeypatch):
    from screen_assistant_server import backstage_bridge as bridge
    monkeypatch.setenv('SYSTEMROOT',r'C:\Windows')
    monkeypatch.setattr(bridge,'_windows_process_identity',lambda _: (r'C:\Windows\System32\OpenSSH\ssh.exe',100))
    monkeypatch.setattr(bridge,'_windows_listeners',lambda _: [('127.0.0.1',42)])
    bridge.verify_ssh_tunnel('http://127.0.0.1:57321',42,100)
    with pytest.raises(BackstageFatalError):
        bridge.verify_ssh_tunnel('http://127.0.0.1:57321',42,101)
    for rows in ([('0.0.0.0',42)],[('127.0.0.1',43)],
                 [('127.0.0.1',42),('127.0.0.1',43)],[]):
        monkeypatch.setattr(bridge,'_windows_listeners',lambda _,rows=rows: rows)
        with pytest.raises(BackstageFatalError):
            bridge.verify_ssh_tunnel('http://127.0.0.1:57321',42,100)


@pytest.mark.skipif(os.name!='nt',reason='Windows netstat listener parsing')
def test_windows_listener_parses_ascii_rows_with_non_utf8_localized_header(monkeypatch):
    from screen_assistant_server import backstage_bridge as bridge
    monkeypatch.setenv('SYSTEMROOT',r'C:\Windows')
    run=Mock(return_value=Mock(stdout=b'\xff\xfe localized header\r\n  TCP  127.0.0.1:57321  0.0.0.0:0  LISTENING  42\r\n'))
    monkeypatch.setattr(bridge.subprocess,'run',run)
    assert bridge._windows_listeners(57321)==[('127.0.0.1',42)]
    assert not run.call_args.kwargs.get('text',False)


def test_worker_token_is_not_forwarded_on_redirect(monkeypatch):
    from screen_assistant_server import backstage_bridge as bridge
    received=[]
    class Sink(BaseHTTPRequestHandler):
        def do_GET(self):
            received.append(True); self.send_response(200); self.end_headers()
        def log_message(self,*args): pass
    sink=ThreadingHTTPServer(('127.0.0.1',0),Sink)
    class Redirect(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(302)
            self.send_header('Location',f'http://127.0.0.1:{sink.server_port}/other')
            self.end_headers()
        def log_message(self,*args): pass
    source=ThreadingHTTPServer(('127.0.0.1',0),Redirect)
    threads=[threading.Thread(target=s.serve_forever,daemon=True) for s in (sink,source)]
    for thread in threads: thread.start()
    try:
        monkeypatch.setattr(bridge,'verify_ssh_tunnel',lambda *_: None)
        config=BridgeConfig(f'http://127.0.0.1:{source.server_port}','fixture-token','python','cli')
        with pytest.raises(BackstageFatalError): bridge.BackstageBridge(config).http('GET','fixture')
        assert received==[]
    finally:
        for server in (source,sink): server.shutdown(); server.server_close()
        for thread in threads: thread.join(timeout=5)


def test_changed_tunnel_after_connect_sends_no_request(monkeypatch):
    from screen_assistant_server import backstage_bridge as bridge
    received=[]
    class Sink(BaseHTTPRequestHandler):
        def do_GET(self):
            received.append(True); self.send_response(200); self.end_headers()
        def log_message(self,*args): pass
    server=ThreadingHTTPServer(('127.0.0.1',0),Sink)
    thread=threading.Thread(target=server.serve_forever,daemon=True); thread.start()
    checks=Mock(side_effect=[None,BackstageFatalError('隧道身份已改变')])
    monkeypatch.setattr(bridge,'verify_ssh_tunnel',checks)
    try:
        config=BridgeConfig(f'http://127.0.0.1:{server.server_port}','fixture-token','python','cli')
        with pytest.raises(BackstageFatalError): bridge.BackstageBridge(config).http('GET','fixture')
        assert checks.call_count==2
        assert received==[]
    finally:
        server.shutdown(); server.server_close(); thread.join(timeout=5)


def test_bridge_config_rejects_insecure_url_before_start(monkeypatch):
    monkeypatch.setenv('BACKSTAGE_SERVER_URL','http://example.invalid:8000')
    monkeypatch.setenv('BACKSTAGE_WORKER_TOKEN','fixture')
    monkeypatch.setenv('BACKSTAGE_CLI_PYTHON','fixture-python')
    monkeypatch.setenv('BACKSTAGE_CLI_ENTRY','fixture-cli')
    with pytest.raises(BackstageFatalError): BridgeConfig.from_env()


def test_preclaim_check_does_not_claim_or_open_desktop(monkeypatch,capsys):
    from contextlib import nullcontext
    from screen_assistant_server import backstage_bridge as bridge
    config=BridgeConfig('http://127.0.0.1:57321','fixture-token','python','cli')
    monkeypatch.setattr(bridge.BridgeConfig,'from_env',lambda:config)
    monkeypatch.setattr(bridge,'bridge_instance_lock',lambda: nullcontext())
    run=Mock(side_effect=AssertionError('preclaim不得领取任务'))
    monkeypatch.setattr(bridge.BackstageBridge,'_run_once',run)
    assert bridge.main(['--check'])==0
    run.assert_not_called()
    assert 'fixture-token' not in capsys.readouterr().out


def test_handshake_without_parent_ack_cannot_claim(monkeypatch,capsys):
    from contextlib import nullcontext
    from screen_assistant_server import backstage_bridge as bridge
    config=BridgeConfig('http://127.0.0.1:57321','fixture-token','python','cli')
    monkeypatch.setattr(bridge.BridgeConfig,'from_env',lambda:config)
    monkeypatch.setattr(bridge,'bridge_instance_lock',lambda: nullcontext())
    monkeypatch.setattr(bridge.sys,'stdin',io.TextIOWrapper(io.BytesIO(b''),encoding='utf-8'))
    run=Mock(side_effect=AssertionError('未确认不得领取任务'))
    monkeypatch.setattr(bridge.BackstageBridge,'_run_once',run)
    assert bridge.main(['--startup-handshake'])==2
    run.assert_not_called()
    output=capsys.readouterr().out
    assert 'fixture-token' not in output
    assert '"stage": "handshake"' in output


@pytest.mark.parametrize('stage',['config','lock'])
def test_preclaim_check_reports_safe_stage_without_secret(monkeypatch,capsys,stage):
    from screen_assistant_server import backstage_bridge as bridge
    config=BridgeConfig('http://127.0.0.1:57321','fixture-token','python','cli')
    def failed(*_args,**_kwargs): raise BackstageFatalError('fixture-token should never appear')
    monkeypatch.setattr(bridge.BridgeConfig,'from_env',failed if stage=='config' else lambda:config)
    if stage=='lock': monkeypatch.setattr(bridge,'bridge_instance_lock',failed)
    assert bridge.main(['--check'])==2
    output=capsys.readouterr().out
    assert 'fixture-token' not in output
    assert '"stage": "'+stage+'"' in output
    assert 'BackstageFatalError' in output


def test_launcher_rejects_child_exit_before_ready_without_secret(tmp_path,monkeypatch):
    from screen_assistant_server import backstage_launcher as launcher
    path=tmp_path/'worker-token.env'; path.write_bytes(b'BACKSTAGE_WORKER_TOKEN=fixture-token\n')
    child=Mock(pid=1234,stdout=io.BytesIO(b''),stderr=io.BytesIO()); child.poll.return_value=13; child.wait.return_value=13
    monkeypatch.setattr(launcher.subprocess,'Popen',Mock(return_value=child))
    monkeypatch.setattr(launcher,'verify_ssh_tunnel',Mock())
    with pytest.raises(BackstageFatalError) as error:
        launcher.launch_bridge('http://127.0.0.1:57321',42,100,path,{})
    assert 'fixture-token' not in str(error.value)
    assert '13' in str(error.value)


def test_launcher_reports_handshake_write_failure(tmp_path,monkeypatch):
    from screen_assistant_server import backstage_launcher as launcher
    path=tmp_path/'worker-token.env'; path.write_bytes(b'BACKSTAGE_WORKER_TOKEN=fixture-token\n')
    closed=io.BytesIO(); closed.close()
    child=Mock(pid=1234,stdout=io.BytesIO(b'{"stage":"preclaim","status":"ready"}\n'),
               stderr=io.BytesIO(),stdin=closed)
    child.poll.return_value=None; child.wait.return_value=2
    monkeypatch.setattr(launcher.subprocess,'Popen',Mock(return_value=child))
    monkeypatch.setattr(launcher,'verify_ssh_tunnel',Mock())
    with pytest.raises(BackstageFatalError) as error:
        launcher.launch_bridge('http://127.0.0.1:57321',42,100,path,{})
    assert 'stage=handshake' in str(error.value) and 'fixture-token' not in str(error.value)


def test_launcher_times_out_and_stops_only_its_child(tmp_path,monkeypatch):
    from screen_assistant_server import backstage_launcher as launcher
    path=tmp_path/'worker-token.env'; path.write_bytes(b'BACKSTAGE_WORKER_TOKEN=fixture-token\n')
    child=Mock(pid=1234,stdout=io.BytesIO(b''),stderr=io.BytesIO(),stdin=io.BytesIO())
    child.wait.side_effect=[subprocess.TimeoutExpired('fixture',2),-1]
    monkeypatch.setattr(launcher.subprocess,'Popen',Mock(return_value=child))
    monkeypatch.setattr(launcher,'verify_ssh_tunnel',Mock())
    mailbox=Mock(); mailbox.get.side_effect=launcher.queue.Empty
    monkeypatch.setattr(launcher.queue,'Queue',lambda **_kwargs:mailbox)
    with pytest.raises(BackstageFatalError) as error:
        launcher.launch_bridge('http://127.0.0.1:57321',42,100,path,{})
    child.terminate.assert_called_once()
    assert 'exit=-1' in str(error.value) and 'fixture-token' not in str(error.value)


def test_launcher_reports_only_whitelisted_child_error(tmp_path,monkeypatch):
    from screen_assistant_server import backstage_launcher as launcher
    path=tmp_path/'worker-token.env'; path.write_bytes(b'BACKSTAGE_WORKER_TOKEN=fixture-token\n')
    child=Mock(pid=1234,stdout=io.BytesIO(b'{"stage":"config","status":"failed","error":"fixture-token"}\n'),stderr=io.BytesIO())
    child.poll.return_value=2; child.wait.return_value=2
    monkeypatch.setattr(launcher.subprocess,'Popen',Mock(return_value=child))
    monkeypatch.setattr(launcher,'verify_ssh_tunnel',Mock())
    with pytest.raises(BackstageFatalError) as error:
        launcher.launch_bridge('http://127.0.0.1:57321',42,100,path,{})
    assert 'fixture-token' not in str(error.value)
    assert 'stage=config' in str(error.value) and 'category=unknown' in str(error.value)


def test_launcher_reports_runtime_exit_after_ready(tmp_path,monkeypatch,capsys):
    from screen_assistant_server import backstage_launcher as launcher
    path=tmp_path/'worker-token.env'; path.write_bytes(b'BACKSTAGE_WORKER_TOKEN=fixture-token\n')
    child=Mock(pid=1234,stdout=io.BytesIO(b'{"stage":"preclaim","status":"ready"}\n'
        b'{"stage":"runtime","status":"failed","error":"http_rejected_403"}\n'),stdin=io.BytesIO(),stderr=io.BytesIO())
    child.poll.return_value=None; child.wait.return_value=2
    monkeypatch.setattr(launcher.subprocess,'Popen',Mock(return_value=child))
    monkeypatch.setattr(launcher,'verify_ssh_tunnel',Mock())
    process=launcher.launch_bridge('http://127.0.0.1:57321',42,100,path,{})
    assert launcher.supervise_bridge(process)==2
    output=capsys.readouterr().out
    assert 'stage=runtime' in output and 'http_rejected_403' in output
    assert 'exit=2' in output and 'fixture-token' not in output


def test_supervisor_discards_large_untrusted_output(capsys):
    from screen_assistant_server import backstage_launcher as launcher
    child=Mock(stdout=io.BytesIO(b'fixture-token'+b'x'*5000),stderr=io.BytesIO(),safe_stderr=bytearray(),stderr_thread=Mock())
    child.wait.return_value=7
    assert launcher.supervise_bridge(child)==7
    output=capsys.readouterr().out
    assert 'fixture-token' not in output and 'category=unknown' in output and 'exit=7' in output


def test_stderr_projection_exposes_only_exception_category():
    from screen_assistant_server.backstage_launcher import stderr_category
    assert stderr_category(b'Traceback\nTypeError: fixture-token\n')=='TypeError'
    assert stderr_category(b'Traceback\nUnicodeDecodeError: fixture-token\n')=='UnicodeDecodeError'
    assert stderr_category(b'Traceback\nAttributeError: fixture-token\n')=='AttributeError'
    assert stderr_category(b'fixture-token\n')=='unknown'


def test_launcher_supervises_real_child_with_bounded_safe_status(tmp_path):
    import shutil
    import sys
    from screen_assistant_server import backstage_launcher as launcher
    package=tmp_path/'screen_assistant_server'; package.mkdir()
    (package/'__init__.py').write_text('',encoding='utf-8')
    shutil.copyfile(launcher.__file__,package/'backstage_launcher.py')
    stub='''import json,sys,time\nclass BackstageFatalError(Exception): pass\ndef validate_worker_url(url): return url\ndef verify_ssh_tunnel(*args): pass\nif __name__=='__main__':\n print(json.dumps({'stage':'preclaim','status':'ready'}),flush=True)\n if sys.stdin.buffer.readline(16)!=b'go\\n': sys.exit(3)\n time.sleep(0.3)\n print(json.dumps({'stage':'runtime','status':'failed','error':'bridge_fatal'}),flush=True)\n sys.exit(2)\n'''
    (package/'backstage_bridge.py').write_text(stub,encoding='utf-8')
    secret=tmp_path/'screen-assistant'/'recap-bridge'/'private-config-v1'/'worker-token.env'
    secret.parent.mkdir(parents=True); secret.write_bytes(b'BACKSTAGE_WORKER_TOKEN=fixture-token\n')
    allowed={'SYSTEMROOT','WINDIR','PATH','PATHEXT','TEMP','TMP','HOME','LANG','LC_ALL','OS'}
    env={key:value for key,value in os.environ.items() if key.upper() in allowed}
    env['LOCALAPPDATA']=str(tmp_path)
    process=subprocess.Popen([sys.executable,'-B','-m','screen_assistant_server.backstage_launcher',
        '--server-url','http://127.0.0.1:57321','--ssh-pid','42','--ssh-created-filetime','100'],
        cwd=tmp_path,env=env,stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,text=True)
    first=process.stdout.readline()
    assert 'preclaim initialization ready' in first
    assert process.poll() is None
    rest=process.stdout.read()
    assert process.wait(timeout=5)==2
    assert 'stage=runtime' in rest and 'category=bridge_fatal' in rest and 'exit=2' in rest
    assert 'fixture-token' not in first+rest
    process.stdout.close()


def test_secure_launcher_injects_only_single_field_into_child(tmp_path, monkeypatch):
    from screen_assistant_server import backstage_launcher as launcher
    path=tmp_path/'worker-token.env'
    path.write_bytes(b'BACKSTAGE_WORKER_TOKEN=fixture-token\n')
    popen=Mock()
    popen.return_value.pid=1234
    popen.return_value.stdout=io.BytesIO(b'{"stage":"preclaim","status":"ready"}\n')
    popen.return_value.stderr=io.BytesIO()
    popen.return_value.stdin=io.BytesIO()
    popen.return_value.poll.return_value=None
    popen.return_value.wait.side_effect=subprocess.TimeoutExpired('fixture',2)
    monkeypatch.setattr(launcher.subprocess,'Popen',popen)
    monkeypatch.setattr(launcher,'verify_ssh_tunnel',Mock())
    env={'SYSTEMROOT':'C:\\Windows','PATH':'fixture-path','API_KEY':'unrelated-secret',
         'BACKSTAGE_CLI_PYTHON':'fixture-python','BACKSTAGE_CLI_ENTRY':'fixture-entry',
         'TIANYI_API_KEY':'fixture-model-key','BACKSTAGE_SDK_PYTHON':'fixture-sdk',
         'BACKSTAGE_CLI_GATE_A':'fixture-gate','BACKSTAGE_POLL_SECONDS':'3',
         'BACKSTAGE_CLI_TIMEOUT_SECONDS':'300'}
    child=launcher.launch_bridge('http://127.0.0.1:57321',42,100,path,env)
    assert child.pid==1234
    args,kwargs=popen.call_args
    assert 'fixture-token' not in args[0]
    assert kwargs['env']['BACKSTAGE_WORKER_TOKEN']=='fixture-token'
    assert 'API_KEY' not in kwargs['env']
    assert kwargs['env']['TIANYI_API_KEY']=='fixture-model-key'
    assert kwargs['env']['BACKSTAGE_SDK_PYTHON']=='fixture-sdk'
    assert kwargs['env']['BACKSTAGE_CLI_GATE_A']=='fixture-gate'
    assert kwargs['env']['BACKSTAGE_POLL_SECONDS']=='3'
    assert kwargs['env']['BACKSTAGE_CLI_TIMEOUT_SECONDS']=='300'
    assert 'fixture-model-key' not in args[0]
    assert kwargs['env']['BACKSTAGE_SSH_PID']=='42'
    assert kwargs['cwd']==str(launcher.Path(launcher.__file__).resolve().parent.parent)


@pytest.mark.parametrize('content', [b'', b'BACKSTAGE_WORKER_TOKEN=\n',
    b'BACKSTAGE_WORKER_TOKEN=a\nOTHER=b\n', b'\xef\xbb\xbfBACKSTAGE_WORKER_TOKEN=a\n'])
def test_secure_launcher_rejects_unexpected_file_without_start(tmp_path,monkeypatch,content):
    from screen_assistant_server import backstage_launcher as launcher
    path=tmp_path/'worker-token.env'; path.write_bytes(content)
    popen=Mock(); monkeypatch.setattr(launcher.subprocess,'Popen',popen)
    monkeypatch.setattr(launcher,'verify_ssh_tunnel',Mock())
    with pytest.raises(BackstageFatalError):
        launcher.launch_bridge('http://127.0.0.1:57321',42,100,path,{})
    popen.assert_not_called()
