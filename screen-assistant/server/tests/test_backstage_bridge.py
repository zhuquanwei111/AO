from __future__ import annotations

import subprocess

from screen_assistant_server.backstage_bridge import BackstageBridge, BridgeConfig


def test_bridge_uses_one_cli_task_and_posts_result_to_origin_session():
    task = {
        "task_id": "task-001", "session_id": "session-001",
        "douyin_account": "test-account", "status": "waiting_for_stop",
        "start_signal_source": "ios_app", "stop_signal_received": False,
        "stopped_at_ms": None, "result_status": None,
    }
    result_record = {
        "status": "partial", "session_id": "session-001",
        "douyin_account": "test-account",
        "metrics": {"累计观众": 12000, "新增粉丝": 2, "评论人数": None,
                    "送礼人数": 1, "获得音浪": 25000},
        "raw_displays": {"累计观众": "约1.2万"},
        "approximate_metrics": ["累计观众"],
        "metrics_captured_at": "2026-10-27T12:00:00+08:00",
        "room_id": "room-test", "capture_method": "受控CLI替身",
        "value_semantics": "下播信号后读取的本场左侧汇总",
        "missing_reasons": {"评论人数": "页面未显示"},
    }
    calls = []
    gets = 0

    def http(method, path, payload=None):
        nonlocal gets
        calls.append((method, path, payload))
        if method == "POST" and path.endswith("/backstage/tasks/claim"):
            return {"status": "claimed", "task": dict(task)}
        if method == "GET" and path.endswith("/task-001"):
            gets += 1
            current = dict(task)
            current["stop_signal_received"] = gets >= 2
            current["stopped_at_ms"] = 1_790_000_001_000 if gets >= 2 else None
            current["status"] = "capturing" if gets >= 2 else "waiting_for_stop"
            return {"status": "ok", "task": current}
        if method == "POST" and path.endswith("/backstage/result"):
            return {"status": "accepted", "result_status": payload["status"]}
        raise AssertionError((method, path))

    cli_calls = []

    def cli(command, claimed_task):
        cli_calls.append((command, claimed_task["task_id"], claimed_task["session_id"]))
        if command == "result":
            return {"record": result_record}
        return {"record": {"status": "waiting_for_stop"}}

    bridge = BackstageBridge(
        BridgeConfig("http://127.0.0.1:8000/prefix", "worker-secret",
                     "python.exe", "recap_glm.py"),
        http=http, cli=cli, sleep=lambda _: None,
    )
    assert bridge.run_once() is True
    assert cli_calls == [
        ("start", "task-001", "session-001"),
        ("stop", "task-001", "session-001"),
        ("result", "task-001", "session-001"),
    ]
    posted = calls[-1][2]
    assert calls[-1][1] == "v1/live/sessions/session-001/backstage/result"
    assert posted["task_id"] == "task-001"
    assert posted["douyin_account"] == "test-account"
    assert posted["metrics"] == result_record["metrics"]
    assert posted["stop_signal_received"] is True
    assert posted["start_signal_source"] == posted["stop_signal_source"] == "ios_app"
    assert posted["missing_fields"] == {"评论人数": "页面未显示"}


def test_bridge_preserves_unknown_capture_time_for_empty_result():
    task = {"task_id": "t", "session_id": "s", "douyin_account": "a",
            "stop_signal_received": True, "start_signal_source": "ios_app"}
    record = {"status": "needs_input", "metrics": {}, "missing_fields": ["累计观众"]}
    payload = BackstageBridge.result_payload(task, record)
    assert payload["metrics_captured_at"] is None
    assert payload["metrics"]["累计观众"] is None
    assert payload["missing_fields"]["累计观众"] == "未读取"


def test_bridge_forwards_android_app_signal_source_without_ios_relabeling():
    task = {"task_id": "t", "session_id": "s", "douyin_account": "a",
            "start_signal_source": "android_app", "stop_signal_source": "android_app",
            "stop_signal_received": True}
    bridge = BackstageBridge(BridgeConfig("http://localhost", "worker", "python", "cli.py"))
    assert "--source=android_app" in bridge._cli_args("start", task)
    assert "--source=android_app" in bridge._cli_args("stop", task)
    payload = bridge.result_payload(task, {"status": "needs_input", "metrics": {}})
    assert payload["start_signal_source"] == "android_app"
    assert payload["stop_signal_source"] == "android_app"


def test_android_app_task_uses_existing_bridge_and_posts_to_same_session():
    task = {
        "task_id": "android-task", "session_id": "android-session",
        "douyin_account": "android-account", "status": "waiting_for_stop",
        "start_signal_source": "android_app", "stop_signal_source": None,
        "stop_signal_received": False, "stopped_at_ms": None,
    }
    calls = []

    def http(method, path, payload=None):
        calls.append((method, path, payload))
        if method == "POST" and path.endswith("/backstage/tasks/claim"):
            return {"status": "claimed", "task": dict(task)}
        if method == "GET" and path.endswith("/android-task"):
            return {"status": "ok", "task": {
                **task, "status": "capturing", "stop_signal_received": True,
                "stop_signal_source": "android_app", "stopped_at_ms": 100,
            }}
        if method == "POST" and path.endswith("/backstage/result"):
            return {"status": "accepted", "result_status": payload["status"]}
        raise AssertionError((method, path))

    cli_sources = []

    def cli(command, task_snapshot):
        cli_sources.append((command, task_snapshot.get("start_signal_source"),
                            task_snapshot.get("stop_signal_source")))
        if command == "result":
            return {"record": {"session_id": "android-session",
                               "douyin_account": "android-account",
                               "status": "needs_input", "metrics": {}}}
        return {"record": {"status": "waiting_for_stop"}}

    bridge = BackstageBridge(
        BridgeConfig("http://127.0.0.1:8000", "worker", "python", "recap.py"),
        http=http, cli=cli, sleep=lambda _: None,
    )
    assert bridge.run_once() is True
    assert [command for command, _, _ in cli_sources] == ["start", "stop", "result"]
    assert cli_sources[0][1] == "android_app"
    assert cli_sources[1][2] == "android_app"
    result_request = calls[-1][2]
    assert calls[-1][1] == "v1/live/sessions/android-session/backstage/result"
    assert result_request["start_signal_source"] == result_request["stop_signal_source"] == "android_app"


def test_bridge_rejects_cli_result_identity_drift_before_posting():
    claim = {"task_id": "t", "session_id": "s1", "douyin_account": "a",
             "status": "waiting_for_stop", "start_signal_source": "ios_app",
             "stop_signal_received": True, "stopped_at_ms": 3}
    calls = []

    def http(method, path, payload=None):
        calls.append((method, path, payload))
        if path.endswith("/backstage/tasks/claim"):
            return {"status": "claimed", "task": claim}
        if method == "GET":
            return {"status": "ok", "task": claim}
        return {"status": "accepted"}

    cli_calls = []

    def cli(command, task):
        cli_calls.append(command)
        if command == "result":
            return {"record": {"session_id": "s2", "douyin_account": "a",
                               "status": "needs_input", "metrics": {}}}
        return {"record": {"status": "waiting_for_stop"}}

    bridge = BackstageBridge(
        BridgeConfig("http://localhost", "secret", "python", "cli.py"),
        http=http, cli=cli, sleep=lambda _: None,
    )
    try:
        bridge.run_once()
    except ValueError as exc:
        assert "identity" in str(exc)
    else:
        raise AssertionError("CLI session identity mismatch must be rejected")
    assert cli_calls == ["start", "stop", "result"]
    assert not any(path.endswith("/backstage/result") for _, path, _ in calls)


def test_cli_process_uses_argument_list_and_never_receives_worker_token(monkeypatch):
    config = BridgeConfig("http://localhost", "worker-secret", "python.exe", "recap.py")
    task = {"task_id": "t", "session_id": "s", "douyin_account": "a;& arbitrary"}
    calls = []

    def run(args, **kwargs):
        calls.append((args, kwargs))
        return subprocess.CompletedProcess(args, 0, '{"record":{"status":"waiting_for_stop"}}', "")

    monkeypatch.setenv("BACKSTAGE_WORKER_TOKEN", "worker-secret")
    monkeypatch.setenv("AO_SESSION_ID", "ao-session-test")
    monkeypatch.setattr("screen_assistant_server.backstage_bridge.subprocess.run", run)
    bridge = BackstageBridge(config)
    bridge.cli = bridge._cli
    bridge.cli("start", task)

    args, kwargs = calls[0]
    assert "--source=ios_app" in args
    assert "--desktop-authorized" in args
    assert "--account=a;& arbitrary" in args
    assert kwargs.get("shell", False) is False
    assert "BACKSTAGE_WORKER_TOKEN" not in kwargs["env"]
    assert "AO_SESSION_ID" not in kwargs["env"]
    assert "worker-secret" not in args and "worker-secret" not in kwargs["env"].values()
