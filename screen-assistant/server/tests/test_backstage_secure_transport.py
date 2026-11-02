"""仅验证虚构令牌和隔离回环，不访问生产服务。"""
from unittest.mock import Mock
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import os
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
    monkeypatch.setattr(bridge,'_windows_listeners',lambda _: [('0.0.0.0',42)])
    with pytest.raises(BackstageFatalError):
        bridge.verify_ssh_tunnel('http://127.0.0.1:57321',42,100)


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


def test_secure_launcher_injects_only_single_field_into_child(tmp_path, monkeypatch):
    from screen_assistant_server import backstage_launcher as launcher
    path=tmp_path/'worker-token.env'
    path.write_bytes(b'BACKSTAGE_WORKER_TOKEN=fixture-token\n')
    popen=Mock()
    popen.return_value.pid=1234
    monkeypatch.setattr(launcher.subprocess,'Popen',popen)
    monkeypatch.setattr(launcher,'verify_ssh_tunnel',Mock())
    env={'SYSTEMROOT':'C:\\Windows','PATH':'fixture-path','API_KEY':'unrelated-secret',
         'BACKSTAGE_CLI_PYTHON':'fixture-python','BACKSTAGE_CLI_ENTRY':'fixture-entry'}
    pid=launcher.launch_bridge('http://127.0.0.1:57321',42,100,path,env)
    assert pid==1234
    args,kwargs=popen.call_args
    assert 'fixture-token' not in args[0]
    assert kwargs['env']['BACKSTAGE_WORKER_TOKEN']=='fixture-token'
    assert 'API_KEY' not in kwargs['env']
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
