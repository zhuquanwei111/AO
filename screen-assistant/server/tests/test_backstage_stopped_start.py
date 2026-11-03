"""已结束任务只能使用已有本场绑定，不再执行新的桌面start。"""
import json

import pytest

from screen_assistant_server.backstage_bridge import BackstageBridge, BridgeConfig


@pytest.mark.parametrize('record', [None, {'status': 'binding'}])
def test_stopped_unbound_task_never_launches_desktop_start(monkeypatch, record):
    task = {'task_id': 'A', 'session_id': 'session-A', 'douyin_account': 'account-A',
            'stop_signal_received': True, 'start_signal_source': 'android_app',
            'stop_signal_source': 'android_app'}
    commands = []
    class Process:
        pid = 1
        returncode = 0
        def communicate(self, timeout=None):
            return json.dumps({'record': record}), ''
    def popen(args, **kwargs):
        commands.append(args[2])
        return Process()
    monkeypatch.setattr('screen_assistant_server.backstage_bridge.subprocess.Popen', popen)
    bridge = BackstageBridge(BridgeConfig('http://127.0.0.1:8000', 'fixture', 'python', 'recap.py'))
    outcome = bridge._cli('start', task)
    assert commands == ['result']
    assert outcome.failure_reason == 'stopped_before_binding'


def test_stopped_bound_task_resumes_existing_identity_without_a_new_start(monkeypatch):
    task = {'task_id': 'A', 'session_id': 'session-A', 'douyin_account': 'account-A',
            'stop_signal_received': True, 'start_signal_source': 'android_app',
            'stop_signal_source': 'android_app'}
    record = {'session_id': 'session-A', 'douyin_account': 'account-A',
              'status': 'waiting_for_stop', 'start_signal_source': 'android_app',
              'binding_evidence': {'douyin_account': 'account-A', 'room_id': 'room-A',
                                   'observed_at': '2026-11-03T10:00:00+08:00', 'locator': 'fixture'}}
    commands = []
    class Process:
        pid = 1
        returncode = 0
        def communicate(self, timeout=None):
            return json.dumps({'record': record}), ''
    def popen(args, **kwargs):
        commands.append(args[2])
        return Process()
    monkeypatch.setattr('screen_assistant_server.backstage_bridge.subprocess.Popen', popen)
    bridge = BackstageBridge(BridgeConfig('http://127.0.0.1:8000', 'fixture', 'python', 'recap.py'))
    outcome = bridge._cli('start', task)
    assert commands == ['result']
    assert outcome.output['record'] == record
    assert outcome.returncode == 0
