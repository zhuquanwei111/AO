"""正式main的preclaim后异常只输出固定诊断，不运行真实桌面或服务。"""
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import Mock
import uuid

import pytest
from screen_assistant_server import backstage_bridge as module
from screen_assistant_server import backstage_launcher as launcher


@pytest.mark.parametrize('phase,exception_class,cli_exit',[
    ('claim_request','ConnectionResetError',None),
    ('cli_result_wait','ValueError',None),
    ('cli_stop_wait','ValueError',None),
    ('payload_prepare','TypeError',2),
    ('result_post','ConnectionResetError',2),
])
def test_formal_main_reports_phase_without_exception_body(monkeypatch,capsys,phase,exception_class,cli_exit):
    task={'task_id':'synthetic-task','session_id':'synthetic-session','douyin_account':'synthetic-account',
          'status':'capturing','start_signal_source':'android_app','stop_signal_source':'android_app',
          'stop_signal_received':True}
    record={'local_test_id':task['task_id'],'session_id':task['session_id'],'douyin_account':task['douyin_account'],
            'start_signal_source':'android_app','stop_signal_source':None,'status':'needs_input',
            'reason':'visual_worker_connection_error','binding_evidence':None,'metrics':None}
    secret='fixture-private-exception-text'
    commands=[]; posts=[]
    def http(method,path,payload=None):
        if path.endswith('/tasks/claim'):
            if phase=='claim_request': raise ConnectionResetError(secret)
            return {'status':'claimed','task':task}
        if path.endswith('/backstage/result'):
            if phase=='result_post': raise ConnectionResetError(secret)
            posts.append(payload)
            return {'status':'accepted','task_id':task['task_id'],'result_status':payload['status']}
        raise AssertionError('unexpected-test-route')
    class Process:
        def __init__(self,command):
            self.command=command; self.returncode=0 if command=='result' else 2
        def communicate(self,timeout=None):
            if phase=='cli_'+self.command+'_wait': raise ValueError(secret)
            current=record if self.command=='result' else {**record,'stop_signal_source':'android_app','status':'failed'}
            return json.dumps({'record':current}),''
    def popen(args,**kwargs):
        assert args[2] in ('result','stop')
        commands.append(args[2]); return Process(args[2])
    config=module.BridgeConfig('http://127.0.0.1:57321','fixture-worker','fixture-python','recap.py')
    bridge=module.BackstageBridge(config,http=http,sleep=lambda _:None)
    if phase=='payload_prepare':
        def invalid_payload(*args,**kwargs): raise TypeError(secret)
        monkeypatch.setattr(bridge,'result_payload',invalid_payload)
    lock=module.bridge_instance_lock
    monkeypatch.setattr(module,'bridge_instance_lock',lambda:lock('ScreenAssistantDiagnosticFixture-'+uuid.uuid4().hex))
    monkeypatch.setattr(module.BridgeConfig,'from_env',classmethod(lambda cls:config))
    monkeypatch.setattr(module,'BackstageBridge',lambda _:bridge)
    monkeypatch.setattr(module.subprocess,'Popen',popen)
    monkeypatch.setattr(module.sys,'stdin',SimpleNamespace(buffer=io.BytesIO(b'go\n')))
    assert module.main(['--startup-handshake'])==2
    captured=capsys.readouterr()
    events=[json.loads(line) for line in captured.out.splitlines()]
    assert events[0]=={'stage':'preclaim','status':'ready'}
    assert events[-1]['error']=='runtime_exception'
    assert events[-1]['phase']==phase
    assert events[-1]['exception_class']==exception_class
    assert events[-1]['cli_exit_code']==cli_exit
    assert secret not in captured.out+captured.err
    assert 'Traceback' not in captured.out+captured.err
    assert not posts
    assert 'start' not in commands


def test_supervisor_keeps_only_fixed_runtime_diagnostics(capsys):
    event={'stage':'runtime','status':'failed','error':'runtime_exception',
           'phase':'cli_result_wait','exception_class':'ValueError','cli_exit_code':None,
           'untrusted':'fixture-private-exception-text'}
    process=Mock(stdout=io.BytesIO((json.dumps(event)+'\n').encode()),stderr=io.BytesIO(),
                 safe_stderr=bytearray(),stderr_thread=Mock())
    process.wait.return_value=2
    assert launcher.supervise_bridge(process)==2
    text=capsys.readouterr().out
    assert 'category=runtime_exception' in text
    assert 'phase=cli_result_wait' in text and 'exception=ValueError' in text
    assert 'fixture-private' not in text


@pytest.mark.parametrize('value',[
    {'phase':'fixture-private','exception_class':'fixture-private','cli_exit_code':True},
    {'phase':[],'exception_class':{},'cli_exit_code':2**80},
    None,
])
def test_untrusted_diagnostic_fields_do_not_leak_or_fake_exit_code(value):
    assert module.safe_runtime_diagnostic(value)=={
        'phase':'unknown','exception_class':'other_exception','cli_exit_code':None}


def test_formal_launcher_and_actual_bridge_main_report_runtime_failure(tmp_path):
    package=tmp_path/'screen_assistant_server'; package.mkdir()
    (package/'__init__.py').write_text('',encoding='utf-8')
    shutil.copyfile(module.__file__,package/'bridge_impl.py')
    shutil.copyfile(launcher.__file__,package/'backstage_launcher.py')
    # 只替身SSH验证和HTTP；其余正式main、握手、环境构造与监督使用本次实际源码。
    shim=('from . import bridge_impl as b\n'
          'BackstageFatalError=b.BackstageFatalError\n'
          'validate_worker_url=b.validate_worker_url\n'
          'safe_runtime_diagnostic=b.safe_runtime_diagnostic\n'
          'def verify_ssh_tunnel(*args): pass\n'
          'if __name__=="__main__":\n'
          ' b.verify_ssh_tunnel=verify_ssh_tunnel\n'
          ' import uuid\n'
          ' original_lock=b.bridge_instance_lock\n'
          ' b.bridge_instance_lock=lambda:original_lock("ScreenAssistantFormalDiagnosticFixture-"+uuid.uuid4().hex)\n'
          ' original=b.BackstageBridge\n'
          ' def fail(*args): raise ConnectionResetError("fixture-private-exception-text")\n'
          ' b.BackstageBridge=lambda config:original(config,http=fail)\n'
          ' raise SystemExit(b.main())\n')
    (package/'backstage_bridge.py').write_text(shim,encoding='utf-8')
    private=tmp_path/'screen-assistant/recap-bridge/private-config-v1/worker-token.env'
    private.parent.mkdir(parents=True); private.write_text('BACKSTAGE_WORKER_TOKEN=fixture-worker\n',encoding='utf-8')
    env={key:value for key,value in os.environ.items() if key.upper() in
         ('SYSTEMROOT','WINDIR','PATH','PATHEXT','TEMP','TMP','HOME','LANG','LC_ALL','OS')}
    env.update(LOCALAPPDATA=str(tmp_path),BACKSTAGE_CLI_PYTHON='fixture-python',BACKSTAGE_CLI_ENTRY='recap.py')
    process=subprocess.run([sys.executable,'-B','-m','screen_assistant_server.backstage_launcher',
        '--server-url','http://127.0.0.1:57321','--ssh-pid','42','--ssh-created-filetime','100'],
        cwd=tmp_path,env=env,stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.PIPE,
        text=True,encoding='utf-8',timeout=15)
    assert process.returncode==2
    assert 'preclaim initialization ready' in process.stdout
    assert 'category=runtime_exception' in process.stdout
    assert 'phase=claim_request' in process.stdout and 'exception=ConnectionResetError' in process.stdout
    assert 'fixture-private' not in process.stdout+process.stderr
    assert 'fixture-worker' not in process.stdout+process.stderr
    assert 'Traceback' not in process.stdout+process.stderr
